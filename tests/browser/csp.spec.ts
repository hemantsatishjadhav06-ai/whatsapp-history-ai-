import { expect, test, type Page } from '@playwright/test';

// The nonce policy is enforced by the real browser: any blocked script, style,
// font, frame or connection appears as a console error or violation event.
async function watchPolicy(page: Page) {
  const violations: string[] = [];
  page.on('console', message => { if (message.type() === 'error' && /Content Security Policy|Refused to/i.test(message.text())) violations.push(message.text()); });
  page.on('pageerror', error => violations.push(error.message));
  await page.addInitScript(() => {
    const seen: string[] = (window as unknown as { __cspViolations: string[] }).__cspViolations = [];
    document.addEventListener('securitypolicyviolation', event => seen.push(`${event.effectiveDirective} ${event.blockedURI}`));
  });
  return async () => [...violations, ...await page.evaluate(() => (window as unknown as { __cspViolations: string[] }).__cspViolations)];
}

async function nonceFor(page: Page, route: string) {
  const response = await page.goto(route);
  const policy = response?.headers()['content-security-policy'] ?? '';
  expect(policy).toContain("'strict-dynamic'");
  expect(policy).toContain("object-src 'none'");
  expect(policy).toContain("frame-ancestors 'none'");
  expect(policy).not.toContain("'unsafe-eval'");
  return /'nonce-([A-Za-z0-9+/=]{16,})'/.exec(policy)?.[1];
}

for (const route of ['/', '/login', '/product', '/product/trust']) {
  test(`${route} hydrates under an enforced per-request nonce policy`, async ({ page }) => {
    const violations = await watchPolicy(page);
    const first = await nonceFor(page, route);
    expect(first).toBeTruthy();
    await page.waitForFunction(() => Boolean((window as unknown as { next?: { version?: string } }).next?.version));
    await page.waitForLoadState('networkidle');
    expect(await violations()).toEqual([]);
    expect(await nonceFor(page, route)).not.toBe(first);
  });
}

test('client-side navigation loads new route chunks without a policy violation', async ({ page }) => {
  const violations = await watchPolicy(page);
  await nonceFor(page, '/product');
  await page.waitForFunction(() => Boolean((window as unknown as { next?: { version?: string } }).next?.version));
  await page.evaluate(() => { (window as unknown as { __sameDocument: boolean }).__sameDocument = true; });
  const footer = page.getByRole('navigation', { name: 'Footer' });
  await footer.getByRole('link', { name: 'Trust & safety', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'A replica you can switch off in one tap.' })).toBeVisible();
  await footer.getByRole('link', { name: 'Live demo', exact: true }).click();
  await expect(page.locator('#main-content')).toBeVisible();
  await expect(page).toHaveURL(url => url.pathname === '/');
  await page.waitForLoadState('networkidle');
  // Still the first document: the app bundle arrived through the trusted client router.
  expect(await page.evaluate(() => (window as unknown as { __sameDocument?: boolean }).__sameDocument)).toBe(true);
  expect(await violations()).toEqual([]);
});

test('fall-through documents get the nonce policy and route handlers stay locked', async ({ request }) => {
  for (const path of ['/favicon.ico', '/api', '/healthzx', '/native-api']) {
    expect((await request.get(path)).headers()['content-security-policy']).toMatch(/'nonce-[A-Za-z0-9+/=]{16,}' 'strict-dynamic'/);
  }
  for (const path of ['/healthz', '/readyz', '/api/me', '/api/not-a-route', '/native-api/me']) {
    expect((await request.get(path)).headers()['content-security-policy']).toBe("default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'");
  }
});

test('the dynamically loaded Google Identity script runs under the nonce policy', async ({ page }) => {
  const violations = await watchPolicy(page);
  await page.route('https://accounts.google.com/gsi/client', route => route.fulfill({ contentType: 'application/javascript', body: `
    window.google = { accounts: { id: { initialize(options) { window.__gisOptions = options; },
      renderButton(element) { const button = document.createElement('button'); button.textContent = 'Continue with Google fixture'; element.replaceChildren(button); } } } };` }));
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/auth/config') return json({ backend_configured: true, google_configured: true, client_id: 'synthetic-web-client.apps.googleusercontent.com' });
    if (path === '/api/auth/nonce') return json({ nonce: 'n'.repeat(43) });
    return json({ detail: 'Authentication required' }, 401);
  });
  await page.goto('/login');
  await expect(page.getByRole('button', { name: 'Continue with Google fixture' })).toBeVisible();
  expect(await violations()).toEqual([]);
});

test('markup injected into a served page cannot run inline script or handlers', async ({ page }) => {
  const violations = await watchPolicy(page);
  // Models an HTML injection: the server's own response and CSP header, plus attacker markup.
  await page.route('**/product/trust', async route => {
    const response = await route.fetch();
    const body = (await response.text()).replace('</body>', '<script>window.__cspProbe="inline"</script><img src="data:," onerror="window.__cspProbe=\'handler\'"></body>');
    await route.fulfill({ response, body });
  });
  await page.goto('/product/trust');
  await page.waitForFunction(() => Boolean((window as unknown as { next?: { version?: string } }).next?.version));
  expect(await page.evaluate(() => (window as unknown as { __cspProbe?: string }).__cspProbe)).toBeUndefined();
  const blocked = await violations();
  expect(blocked.filter(entry => entry.startsWith('script-src-elem inline')).length).toBe(1);
  expect(blocked.filter(entry => entry.startsWith('script-src-attr inline')).length).toBe(1);
});
