import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const routes = ['/','/inbox','/actions','/memory','/rules','/connections','/activity','/settings','/more'];

for (const route of routes) {
  test(`renders a usable, labelled ${route} workspace`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(route);
    await expect(page.getByRole('main')).toBeVisible();
    await expect(page.getByRole('main').getByRole('heading').first()).toBeVisible();
    await expect(page.getByText(/synthetic demo/i).first()).toBeVisible();
    expect(errors).toEqual([]);
  });
}

for (const route of ['/','/inbox/chat_maya','/actions','/memory','/rules','/settings']) {
  test(`has no serious accessibility violations on ${route}`, async ({ page }) => {
    await page.goto(route);
    await expect(page.getByRole('main')).toBeVisible();
    const result = await new AxeBuilder({ page }).withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze();
    expect(result.violations).toEqual([]);
  });
}

test('all primary surfaces remain inside a 320px viewport', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  for (const route of routes) {
    await page.goto(route);
    await expect(page.getByRole('main')).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    expect(overflow, `${route} overflows horizontally`).toBe(false);
    await expect(page.getByRole('banner').getByRole('button', { name: /pause replies & actions/i })).toBeVisible();
  }
});

test('recipient composer uses newline and remains separate from Milo', async ({ page }) => {
  await page.goto('/inbox/chat_maya');
  const composer = page.getByRole('textbox', { name: 'Message Maya', exact: true });
  await composer.fill('One line');
  await composer.press('Enter');
  await composer.press('End');
  await composer.press('A');
  await expect(composer).toHaveValue('One line\nA');
  await expect(page.getByRole('button', { name: /prepare message/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /ask milo/i }).first()).toBeVisible();
});

test('proxy does not expose internal dispatch or arbitrary targets', async ({ request }) => {
  for (const path of ['/api/internal/actions/run-once','/api/v1/internal/dispatch-authority','/api/https://example.com']) {
    const response = await request.post(path, { data: {} });
    expect([400,403,404,405]).toContain(response.status());
  }
});

test('web responses include privacy and browser security headers', async ({ request }) => {
  const response = await request.get('/');
  expect(response.status()).toBe(200);
  expect(response.headers()['x-content-type-options']).toBe('nosniff');
  expect(response.headers()['permissions-policy']).toContain('microphone=(self)');
  expect(response.headers()['referrer-policy']).toBe('strict-origin-when-cross-origin');
});
