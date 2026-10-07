import { expect, test, type Page } from '@playwright/test';
import { createDemoSnapshot } from '../../packages/contracts/demo';

// Exercises browser SDK callbacks, nonce rotation and session UX. Google signing
// and SQL identity verification are deliberately mocked here and tested at the
// API boundary separately; this is never evidence of a live provider login.
async function googleFixture(page: Page, options: { configured?: boolean; failExchange?: boolean; failBootstrap?: boolean; failSdk?: boolean; existingSession?: boolean; failLogout?: boolean; expiredLogout?: boolean } = {}) {
  const exchanges: { nonce: string; credential: string; csrf: string | undefined }[] = [];
  const logouts: { csrf: string | undefined }[] = [];
  let nonceCount = 0;
  let sdkCount = 0;
  let bootstrapCount = 0;
  let signedIn = options.existingSession ?? false;
  const snapshot = createDemoSnapshot();
  snapshot.user = { id: 'verified-fixture-owner', email: 'fixture-owner@example.invalid', display_name: 'Verified fixture owner' };
  snapshot.workspace = null;
  snapshot.workspaces = [];
  snapshot.connections = []; snapshot.conversations = []; snapshot.messages = [];
  snapshot.actions = []; snapshot.jobs = []; snapshot.grants = []; snapshot.routes = [];
  snapshot.memories = []; snapshot.styles = []; snapshot.contacts = []; snapshot.tasks = []; snapshot.drafts = []; snapshot.activity = [];
  await page.route('https://accounts.google.com/gsi/client', async route => {
    sdkCount++;
    if (options.failSdk && sdkCount === 1) { await route.abort('failed'); return; }
    await route.fulfill({ contentType: 'application/javascript', body: `
      window.__googleFixture = { initializations: [] };
      window.google = { accounts: { id: {
        initialize(options) { window.__googleFixture.initializations.push(options); },
        renderButton(element) {
          const options = window.__googleFixture.initializations.at(-1);
          const button = document.createElement('button');
          button.textContent = 'Continue with Google fixture';
          button.onclick = () => options.callback({credential:'synthetic-google-browser-credential'});
          element.replaceChildren(button);
        }
      } } };
    ` });
  });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname.replace('/api', '');
    const json = (body: unknown, status = 200, headers: Record<string, string> = {}) => route.fulfill({ status, contentType: 'application/json', headers, body: JSON.stringify(body) });
    if (path === '/auth/config') return json({ backend_configured: true, google_configured: options.configured !== false, client_id: options.configured === false ? null : 'synthetic-web-client.apps.googleusercontent.com' });
    if (path === '/auth/nonce') { nonceCount++; return json({ nonce: `nonce_${nonceCount}_${'x'.repeat(40)}` }); }
    if (path === '/auth/google') {
      const body = route.request().postDataJSON();
      exchanges.push({ ...body, csrf: route.request().headers()['x-csrf-token'] });
      if (options.failExchange && exchanges.length === 1) return json({ detail: 'Login nonce was already consumed or expired' }, 401);
      signedIn = true;
      return json({ user: snapshot.user, csrf_token: 'synthetic-session-csrf' }, 200, { 'Set-Cookie': 'session_token=synthetic-browser-session; Path=/; HttpOnly; SameSite=Lax' });
    }
    if (path === '/me') return signedIn ? json(snapshot.user) : json({ detail: 'Authentication required' }, 401);
    if (path === '/auth/csrf') return json({ csrf_token: 'synthetic-session-csrf', rotated: true });
    if (path === '/auth/logout') {
      logouts.push({ csrf: route.request().headers()['x-csrf-token'] });
      if (options.failLogout && logouts.length === 1) return json({ detail: 'Temporary logout outage' }, 503);
      signedIn = false;
      if (options.expiredLogout) return json({ detail: 'Session is invalid or expired' }, 401);
      return route.fulfill({ status: 204, headers: { 'Set-Cookie': 'session_token=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0' }, body: '' });
    }
    if (path === '/ui/bootstrap') {
      bootstrapCount++;
      if (options.failBootstrap && bootstrapCount === 1) return json({ detail: 'Temporary private storage outage' }, 503);
      return json(snapshot);
    }
    return json({ detail: 'Unexpected fixture endpoint' }, 404);
  });
  return { exchanges, logouts, signedIn: () => signedIn, nonceCount: () => nonceCount, sdkCount: () => sdkCount, bootstrapCount: () => bootstrapCount };
}

test('Google browser exchange submits the same nonce in body and CSRF header, then enters owner setup', async ({ page }) => {
  const fixture = await googleFixture(page);
  await page.goto('/login');
  await page.getByRole('button', { name: 'Continue with Google fixture', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Make it yours', exact: true })).toBeVisible();
  expect(fixture.exchanges).toHaveLength(1);
  expect(fixture.exchanges[0].csrf).toBe(fixture.exchanges[0].nonce);
  expect(fixture.exchanges[0].credential).toBe('synthetic-google-browser-credential');
  await expect(page.getByText('Maya', { exact: true })).toHaveCount(0);
});

test('a rejected exchange retries with a fresh challenge instead of reusing a consumed nonce', async ({ page }) => {
  const fixture = await googleFixture(page, { failExchange: true });
  await page.goto('/login');
  await page.getByRole('button', { name: 'Continue with Google fixture', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'nonce was already consumed or expired' })).toBeVisible();
  await page.getByRole('button', { name: 'Retry Google sign-in', exact: true }).click();
  await page.getByRole('button', { name: 'Continue with Google fixture', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Make it yours', exact: true })).toBeVisible();
  expect(fixture.exchanges).toHaveLength(2);
  expect(fixture.exchanges[0].nonce).not.toBe(fixture.exchanges[1].nonce);
  expect(fixture.sdkCount()).toBe(1);
});

test('an idle login renews its challenge and ignores callbacks from the old Google button', async ({ page }) => {
  await page.clock.install();
  const fixture = await googleFixture(page);
  await page.goto('/login');
  await expect(page.getByRole('button', { name: 'Continue with Google fixture', exact: true })).toBeVisible();
  await page.clock.fastForward(240_001);
  await expect.poll(fixture.nonceCount).toBe(2);
  await expect.poll(() => page.evaluate(() => (window as unknown as { __googleFixture: { initializations: unknown[] } }).__googleFixture.initializations.length)).toBe(2);
  await page.evaluate(() => (window as unknown as { __googleFixture: { initializations: { callback(response: { credential: string }): void }[] } }).__googleFixture.initializations[0].callback({ credential: 'stale-fixture-credential' }));
  expect(fixture.exchanges).toHaveLength(0);
  await page.getByRole('button', { name: 'Continue with Google fixture', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Make it yours', exact: true })).toBeVisible();
  expect(fixture.exchanges).toHaveLength(1);
  expect(fixture.exchanges[0].nonce).toContain('nonce_2_');
});

test('a blocked Google SDK can be loaded again without duplicate login scripts', async ({ page }) => {
  const fixture = await googleFixture(page, { failSdk: true });
  await page.goto('/login');
  await expect(page.getByRole('alert').filter({ hasText: 'could not load' })).toBeVisible();
  await page.getByRole('button', { name: 'Retry Google sign-in', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Continue with Google fixture', exact: true })).toBeVisible();
  expect(fixture.sdkCount()).toBe(2);
  await expect(page.locator('script[data-milo-google-identity]')).toHaveCount(1);
  expect(fixture.nonceCount()).toBe(1);
});

test('successful sign-in with unavailable workspace offers a loading retry without another Google exchange', async ({ page }) => {
  const fixture = await googleFixture(page, { failBootstrap: true });
  await page.goto('/login');
  await page.getByRole('button', { name: 'Continue with Google fixture', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'Google sign-in succeeded' })).toBeVisible();
  await expect(page.getByRole('status')).toContainText('You are signed in');
  await expect(page.getByRole('button', { name: 'Retry Google sign-in', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Continue to your workspace', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Make it yours', exact: true })).toBeVisible();
  expect(fixture.exchanges).toHaveLength(1);
  expect(fixture.bootstrapCount()).toBe(2);
});

test('restored browser sessions continue into setup without exchanging another Google credential', async ({ page }) => {
  const fixture = await googleFixture(page, { existingSession: true });
  await page.goto('/login');
  await expect(page.getByRole('button', { name: 'Continue to your workspace', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Continue to your workspace', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Make it yours', exact: true })).toBeVisible();
  expect(fixture.exchanges).toHaveLength(0);
});

test('missing Google configuration remains unavailable and never loads a provider SDK', async ({ page }) => {
  const fixture = await googleFixture(page, { configured: false });
  await page.goto('/login');
  await expect(page.getByRole('button', { name: 'Google sign-in not configured', exact: true })).toBeDisabled();
  expect(fixture.sdkCount()).toBe(0);
  expect(fixture.nonceCount()).toBe(0);
  expect(fixture.exchanges).toHaveLength(0);
});

test('duplicate Google callbacks exchange the credential once', async ({ page }) => {
  const fixture = await googleFixture(page);
  await page.goto('/login');
  await expect(page.getByRole('button', { name: 'Continue with Google fixture', exact: true })).toBeVisible();
  await page.evaluate(() => {
    const callback = (window as unknown as { __googleFixture: { initializations: { callback(response: { credential: string }): void }[] } }).__googleFixture.initializations.at(-1)!.callback;
    callback({ credential: 'synthetic-google-browser-credential' });
    callback({ credential: 'duplicate-fixture-credential' });
  });
  await expect(page.getByRole('heading', { name: 'Make it yours', exact: true })).toBeVisible();
  expect(fixture.exchanges).toHaveLength(1);
});

test('leaving login invalidates a pending Google callback', async ({ page }) => {
  const fixture = await googleFixture(page);
  await page.goto('/login');
  await expect(page.getByRole('button', { name: 'Continue with Google fixture', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Explore the synthetic demo', exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  await page.evaluate(() => (window as unknown as { __googleFixture: { initializations: { callback(response: { credential: string }): void }[] } }).__googleFixture.initializations.at(-1)!.callback({ credential: 'abandoned-fixture-credential' }));
  await expect(page.getByRole('button', { name: 'Synthetic demo', exact: true })).toBeVisible();
  expect(fixture.exchanges).toHaveLength(0);
});

test('a restored session with unavailable private storage reports the problem and retries loading', async ({ page }) => {
  const fixture = await googleFixture(page, { existingSession: true, failBootstrap: true });
  await page.goto('/login');
  await expect(page.getByRole('alert').filter({ hasText: 'Your sign-in was restored' })).toBeVisible();
  await page.getByRole('button', { name: 'Continue to your workspace', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Make it yours', exact: true })).toBeVisible();
  expect(fixture.exchanges).toHaveLength(0);
  expect(fixture.bootstrapCount()).toBe(2);
});

test('Google login waits for browser-session restoration to settle before creating a new challenge', async ({ page }) => {
  const fixture = await googleFixture(page);
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let entered = false;
  await page.route('**/api/me', async route => { entered = true; await held; await route.fallback(); });
  await page.goto('/login');
  await expect.poll(() => entered).toBe(true);
  await expect(page.getByRole('button', { name: 'Checking sign-in…', exact: true })).toBeDisabled();
  expect(fixture.sdkCount()).toBe(0);
  expect(fixture.nonceCount()).toBe(0);
  release();
  await page.getByRole('button', { name: 'Continue with Google fixture', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Make it yours', exact: true })).toBeVisible();
  expect(fixture.exchanges).toHaveLength(1);
});

for (const existingSession of [false, true]) {
  test(`a ${existingSession ? 'restored' : 'new Google'} session is revoked even when its private bootstrap failed`, async ({ page, context }) => {
    const fixture = await googleFixture(page, { existingSession, failBootstrap: true });
    await page.goto('/login');
    if (!existingSession) await page.getByRole('button', { name: 'Continue with Google fixture', exact: true }).click();
    await expect(page.getByRole('alert').filter({ hasText: 'private workspace could not load' })).toBeVisible();
    if (!existingSession) expect((await context.cookies()).find(cookie => cookie.name === 'session_token')?.httpOnly).toBe(true);
    await page.getByRole('button', { name: 'Sign out', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Explore the synthetic demo', exact: true })).toBeVisible();
    expect(fixture.logouts).toEqual([{ csrf: 'synthetic-session-csrf' }]);
    expect(fixture.signedIn()).toBe(false);
    expect((await context.cookies()).find(cookie => cookie.name === 'session_token')).toBeUndefined();
    await expect(page.getByRole('status').filter({ hasText: 'You are signed in' })).toHaveCount(0);
  });
}

test('an unconfirmed logout retains a revocation retry after clearing local private data', async ({ page }) => {
  const fixture = await googleFixture(page, { failBootstrap: true, failLogout: true });
  await page.goto('/login');
  await page.getByRole('button', { name: 'Continue with Google fixture', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'private workspace could not load' })).toBeVisible();
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'Sign-out was not confirmed' })).toBeVisible();
  expect(fixture.signedIn()).toBe(true);
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Explore the synthetic demo', exact: true })).toBeVisible();
  expect(fixture.logouts).toHaveLength(2);
  expect(fixture.logouts.every(request => request.csrf === 'synthetic-session-csrf')).toBe(true);
  expect(fixture.signedIn()).toBe(false);
});

test('an already expired browser session completes sign-out without offering a failed revocation retry', async ({ page }) => {
  const fixture = await googleFixture(page, { failBootstrap: true, expiredLogout: true });
  await page.goto('/login');
  await page.getByRole('button', { name: 'Continue with Google fixture', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'private workspace could not load' })).toBeVisible();
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Explore the synthetic demo', exact: true })).toBeVisible();
  await expect(page.getByRole('alert').filter({ hasText: 'Sign-out was not confirmed' })).toHaveCount(0);
  expect(fixture.logouts).toHaveLength(1);
});
