import { expect, test, type Page } from '@playwright/test';

async function workspaceFixture(page: Page, failure: 'lost-response' | 'refresh') {
  let created = false; let failed = false;
  const requests: { key: string | undefined; body: Record<string, unknown> }[] = [];
  const owner = { id: 'synthetic-workspace-owner', display_name: 'Setup owner', email: 'setup@example.invalid' };
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/config') return route.fulfill({ json: { backend_configured: true, google_configured: false } });
    if (path === '/api/me') return route.fulfill({ json: owner });
    if (path === '/api/auth/csrf') return route.fulfill({ json: { csrf_token: 'synthetic-workspace-csrf' } });
    if (path === '/api/workspaces' && route.request().method() === 'POST') {
      requests.push({ key: route.request().headers()['idempotency-key'], body: route.request().postDataJSON() }); created = true;
      if (failure === 'lost-response' && !failed) { failed = true; return route.abort('failed'); }
      return route.fulfill({ json: { id: 'synthetic-created-workspace' } });
    }
    if (path === '/api/ui/bootstrap') {
      if (created && failure === 'refresh' && !failed) { failed = true; return route.fulfill({ status: 503, json: { detail: 'Synthetic loading interruption' } }); }
      return route.fulfill({ json: { user: owner,
        workspace: { id: created ? 'synthetic-created-workspace' : '', name: 'Synthetic setup', timezone: 'UTC', paused: false, pause_generation: 0 },
        workspaces: [], connections: [], conversations: [], messages: [], actions: [], drafts: [], tasks: [], jobs: [], memories: [], styles: [], grants: [], routes: [], activity: [], contacts: [], budget: null, retention: null, simulation: false,
        generated_at: '2026-10-07T12:00:00Z', snapshot_version: created ? 'created' : 'empty' } });
    }
    if (path.endsWith('/personal/config')) return route.fulfill({ json: { enabled: false, configured: false, status: 'disabled', simulation: false } });
    if (path.endsWith('/personal/status')) return route.fulfill({ json: { enabled: false, configured: false, connected: false, simulation: false, status: 'disabled', connector: null } });
    if (path === '/api/integrations/whatsapp/status') return route.fulfill({ json: { configured: false, owner_authorized: false, connectors: [], missing_requirements: [] } });
    return route.fulfill({ status: 404, json: { detail: 'Unavailable synthetic route' } });
  });
  return requests;
}

for (const failure of ['lost-response', 'refresh'] as const) test(`workspace creation keeps the same attempt after ${failure}`, async ({ page }) => {
  const requests = await workspaceFixture(page, failure); await page.goto('/onboarding');
  await expect(page.getByRole('heading', { name: 'Make it yours', exact: true })).toBeVisible();
  await page.getByRole('textbox', { name: 'Workspace name', exact: true }).fill('My acknowledged workspace');
  await page.getByRole('combobox', { name: 'Timezone', exact: true }).selectOption('UTC');
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: failure === 'refresh' ? 'Synthetic loading interruption' : 'Failed to fetch' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Workspace name', exact: true })).toBeDisabled();
  expect(requests).toHaveLength(1); expect(requests[0].key).toMatch(/^[0-9a-f-]{36}$/);
  if (failure === 'refresh') await expect(page.getByRole('status')).toContainText('Your workspace was created.');
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Connect carefully', exact: true })).toBeVisible();
  expect(requests).toHaveLength(failure === 'refresh' ? 1 : 2);
  if (failure === 'lost-response') expect(requests[1]).toEqual(requests[0]);
  expect(requests[0].body).toEqual({ name: 'My acknowledged workspace', timezone: 'UTC' });
});
