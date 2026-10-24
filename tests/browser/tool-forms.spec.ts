import { expect, test, type Page } from '@playwright/test';

const reminder = 'What should we remind you about?';

function snapshot(version: string, owner = 'owner-a', workspace = 'workspace-a', jobs: Record<string, unknown>[] = []) {
  return { user: { id: owner, display_name: 'Forms fixture owner', email: `${owner}@example.invalid` },
    workspace: { id: workspace, name: 'Synthetic forms fixture', timezone: 'UTC', paused: false, pause_generation: 0 },
    workspaces: [], connections: [], conversations: [], messages: [], actions: [], drafts: [], tasks: [], jobs, memories: [],
    styles: [], grants: [], routes: [], activity: [], contacts: [], budget: null, retention: null, simulation: false,
    generated_at: '2026-10-24T09:00:00Z', snapshot_version: version };
}
const agendaItem = { id: 'job-arrived', workspace_id: 'workspace-a', conversation_id: null, action_kind: 'REMINDER',
  status: 'scheduled', content: 'Synthetic agenda item from a background refresh', purpose: 'Owner reminder',
  due_at: '2026-10-26T09:00:00Z', expires_at: '2026-10-27T09:00:00Z', timezone: 'UTC', recurrence: 'none', max_runs: 1, version: 1 };

async function revalidate(page: Page) {
  const response = page.waitForResponse(result => result.url().includes('/api/ui/bootstrap') && result.ok());
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await response;
}

test('a new reminder keeps what the owner types while its address is still loading', async ({ page }) => {
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  // Hold any server render of the dialog's address, as on a slow phone network. The open dialog must
  // not depend on it, and nothing that arrives later may replace what the owner already typed.
  await page.route(url => url.pathname === '/actions/new', async route => { await held; await route.continue(); });
  try {
    await page.goto('/actions');
    await page.getByRole('button', { name: 'Schedule something' }).click();
    const content = page.getByRole('dialog').getByRole('textbox', { name: reminder });
    await content.fill('Typed before the address loaded');
    release();
    await expect(page).toHaveURL(/\/actions\/new$/);
    await expect(content).toHaveValue('Typed before the address loaded');
  } finally { release(); }
});

test('a dialog opened while the previous one is still closing stays open', async ({ page }) => {
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  // Hold any server render of the list's address, which a deep link has not loaded yet: a close still
  // in flight must not shut the next dialog.
  await page.route(url => url.pathname === '/actions', async route => { await held; await route.continue(); });
  try {
    await page.goto('/actions/new');
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('button', { name: 'Close details', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await page.getByRole('button', { name: 'Schedule something' }).click();
    await dialog.getByRole('textbox', { name: reminder }).fill('Opened while the last one was closing');
    release();
    await expect(page).toHaveURL(/\/actions\/new$/);
    await expect(dialog.getByRole('textbox', { name: reminder })).toHaveValue('Opened while the last one was closing');
  } finally { release(); }
});

test('a background refresh with new data keeps a new reminder, an owner switch clears it', async ({ page }) => {
  let current = snapshot('v1');
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/config') return route.fulfill({ json: { backend_configured: true, google_configured: false, model: { status: 'disabled' } } });
    if (path === '/api/me') return route.fulfill({ json: current.user });
    if (path === '/api/auth/csrf') return route.fulfill({ json: { csrf_token: 'synthetic-forms-csrf' } });
    if (path === '/api/ui/bootstrap') return route.fulfill({ json: current });
    return route.fulfill({ status: 404, json: { detail: 'Unavailable synthetic fixture route' } });
  });
  await page.goto('/actions');
  await expect(page.getByRole('button', { name: 'Your workspace', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Schedule something' }).click();
  await expect(page).toHaveURL(/\/actions\/new$/);
  const content = page.getByRole('dialog').getByRole('textbox', { name: reminder });
  await content.fill('Call the auditor about the third-quarter numbers');
  // New content (here a new agenda item, in practice a new WhatsApp message) changes the snapshot.
  current = snapshot('v2', 'owner-a', 'workspace-a', [agendaItem]);
  await revalidate(page);
  await expect(page.getByRole('article').filter({ hasText: agendaItem.content })).toBeVisible();
  await expect(content).toHaveValue('Call the auditor about the third-quarter numbers');
  // A different owner and workspace never inherit the unfinished text.
  current = snapshot('v3', 'owner-b', 'workspace-b');
  await revalidate(page);
  await expect(page.getByRole('article').filter({ hasText: agendaItem.content })).toHaveCount(0);
  await expect(content).toHaveValue('');
});
