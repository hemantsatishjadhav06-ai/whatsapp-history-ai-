import { expect, test, type BrowserContext } from '@playwright/test';

const apiUrl = process.env.MILO_API_TEST_URL;
test.skip(!apiUrl, 'Requires the disposable loopback API fixture; never uses a provider account.');

async function seed(context: BrowserContext, label: string) {
  const authResponse = await context.request.post(`${apiUrl}/auth/dev`, { data: {
    email: `browser-${crypto.randomUUID()}@example.invalid`, display_name: 'Browser owner',
  } });
  expect(authResponse.ok()).toBe(true);
  const auth = await authResponse.json();
  const headers = { 'X-CSRF-Token': auth.csrf_token };
  const workspaceResponse = await context.request.post(`${apiUrl}/workspaces`, { headers, data: {
    name: label, timezone: 'UTC',
  } });
  expect(workspaceResponse.ok()).toBe(true);
  const workspace = await workspaceResponse.json();
  const connectorResponse = await context.request.post(`${apiUrl}/connectors`, { headers, data: {
    workspace_id: workspace.id, provider: 'mock', account_id: crypto.randomUUID(), owner_sender_id: 'Owner',
  } });
  expect(connectorResponse.ok()).toBe(true);
  const connector = await connectorResponse.json();
  const conversationResponse = await context.request.post(`${apiUrl}/conversations`, { headers, data: {
    connector_id: connector.id, provider_chat_id: 'synthetic-maya', title: label, recipient_opted_in: true,
  } });
  expect(conversationResponse.ok()).toBe(true);
  const conversation = await conversationResponse.json();
  const permissions = await context.request.put(`${apiUrl}/conversations/${conversation.id}/permissions`, {
    headers, data: { read: true, retain: true, learn: true, draft: true, send: true },
  });
  expect(permissions.ok()).toBe(true);
  const imported = await context.request.post(`${apiUrl}/imports`, { headers, data: {
    conversation_id: conversation.id, owner_sender_label: 'Owner', date_order: 'DMY', timezone: 'UTC',
    text: `06/10/2026, 09:00 - Owner: Thanks for the update.\n06/10/2026, 09:01 - ${label}: What time works for you?`,
  } });
  expect(imported.ok()).toBe(true);
  return { workspace, conversation };
}

test('authenticated browser reads scoped SQL data and acknowledges pause only after commit', async ({ page, context }) => {
  const { workspace, conversation } = await seed(context, 'Scoped Maya');
  await page.goto(`/inbox/${conversation.id}`);
  await expect(page.getByRole('textbox', { name: 'Message Scoped Maya', exact: true })).toBeVisible();
  await expect(page.getByText('What time works for you?', { exact: true }).last()).toBeVisible();
  await page.getByRole('button', { name: 'Pause replies & actions', exact: true }).click();
  await expect.poll(async () => {
    const response = await context.request.get(`${apiUrl}/workspaces`);
    return (await response.json()).find((row: { id: string }) => row.id === workspace.id)?.paused;
  }).toBe(true);
  await expect(page.getByRole('button', { name: /resume replies & actions/i })).toBeVisible();
});

test('foreign objects do not expose another owner workspace', async ({ page, context, browser }) => {
  const { conversation } = await seed(context, 'Private owner A');
  await page.goto(`/inbox/${conversation.id}`);
  await expect(page.getByRole('textbox', { name: 'Message Private owner A', exact: true })).toBeVisible();
  const second = await browser.newContext();
  await seed(second, 'Private owner B');
  const denied = await second.request.get(`${apiUrl}/conversations/${conversation.id}/messages`);
  expect(denied.status()).toBe(404);
  const secondPage = await second.newPage();
  await secondPage.goto(`/inbox/${conversation.id}`);
  await expect(secondPage.getByRole('button', { name: 'Your workspace', exact: true })).toBeVisible();
  await expect(secondPage.getByText('Private owner A', { exact: true })).toHaveCount(0);
  await second.close();
});

test('offline pause remains unconfirmed and never displays a false acknowledgement', async ({ page, context }) => {
  const { workspace } = await seed(context, 'Offline fixture');
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Your workspace', exact: true })).toBeVisible();
  await context.setOffline(true);
  await page.getByRole('banner').getByRole('button', { name: 'Pause replies & actions', exact: true }).click();
  await expect(page.getByText(/Pause not confirmed; automation may still be active/).first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Resume replies & actions', exact: true })).toHaveCount(0);
  await context.setOffline(false);
  const response = await context.request.get(`${apiUrl}/workspaces`);
  expect((await response.json()).find((row: {id:string}) => row.id === workspace.id).paused).toBe(false);
});

test('new authenticated owner enters explicit workspace setup', async ({ page, context }) => {
  const response = await context.request.post(`${apiUrl}/auth/dev`, {data:{email:`fresh-${crypto.randomUUID()}@example.invalid`}});
  expect(response.ok()).toBe(true);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Make it yours', exact: true })).toBeVisible();
  await expect(page.getByRole('textbox', { name: /workspace/i }).first()).toBeVisible();
});

test('late private snapshot cannot restore data after logout', async ({ page, context }) => {
  await seed(context, 'Private delayed snapshot');
  await page.goto('/settings');
  await expect(page.getByRole('button', { name: 'Your workspace', exact: true })).toBeVisible();
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let captured = false;
  await page.route('**/api/ui/bootstrap*', async route => {
    const response = await route.fetch();
    captured = true;
    await held;
    await route.fulfill({response});
  });
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await expect.poll(() => captured).toBe(true);
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  release();
  await expect(page).toHaveURL(/\/login$/);
  await page.waitForTimeout(200);
  await expect(page.getByText('Private delayed snapshot', { exact: true })).toHaveCount(0);
  const me = await context.request.get(`${apiUrl}/me`);
  expect(me.status()).toBe(401);
});
