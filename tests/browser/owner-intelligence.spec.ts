import { expect, test, type BrowserContext } from '@playwright/test';

const api = process.env.MILO_API_TEST_URL;
test.skip(!api, 'Requires the disposable API and its explicitly synthetic model.');

async function seed(context: BrowserContext, precedingChats = 0) {
  const marker = crypto.randomUUID();
  const login = await context.request.post(`${api}/auth/dev`, { data: { email: `intelligence-${marker}@example.invalid`, display_name: 'Intelligence fixture owner' } });
  expect(login.ok()).toBe(true);
  const headers = { 'X-CSRF-Token': (await login.json()).csrf_token };
  const workspace = await (await context.request.post(`${api}/workspaces`, { headers, data: { name: 'Exact-chat intelligence fixture', timezone: 'UTC' } })).json();
  const connector = await (await context.request.post(`${api}/connectors`, { headers, data: { workspace_id: workspace.id, provider: 'mock', account_id: marker, owner_sender_id: 'Owner' } })).json();
  for (let index = 0; index < precedingChats; index++) {
    const earlier = await context.request.post(`${api}/conversations`, { headers, data: { connector_id: connector.id, provider_chat_id: `${marker}-earlier-${index}`, title: `Earlier contact ${index}` } });
    expect(earlier.ok()).toBe(true);
    const earlierId = (await earlier.json()).id;
    const permission = await context.request.put(`${api}/conversations/${earlierId}/permissions`, { headers, data: { read: true, retain: false, learn: false, draft: false, send: false, share: false } });
    expect(permission.ok()).toBe(true);
  }
  const chat = await (await context.request.post(`${api}/conversations`, { headers, data: { connector_id: connector.id, provider_chat_id: marker, title: 'Intelligence contact' } })).json();
  const grant = await context.request.put(`${api}/conversations/${chat.id}/permissions`, { headers, data: { read: true, retain: true, learn: true, draft: false, send: false, share: false } });
  expect(grant.ok()).toBe(true);
  const imported = await context.request.post(`${api}/imports`, { headers, data: { conversation_id: chat.id, owner_sender_label: 'Owner', date_order: 'DMY', timezone: 'UTC',
    text: '07/10/2026, 10:00 - Owner: Thank you, I will check the plan.\n07/10/2026, 10:01 - Intelligence contact: Can we review the meeting plan?' } });
  expect(imported.ok()).toBe(true);
  return { chat, workspace };
}

test('owner can ask a readable chat without draft/send grants and retains the answer after unrelated budget changes', async ({ page, context }) => {
  const { chat, workspace } = await seed(context);
  const csrf = page.waitForResponse(response => response.url().includes('/api/auth/csrf') && response.ok());
  await page.goto(`/inbox/${chat.id}`);
  const headers = { 'X-CSRF-Token': (await (await csrf).json()).csrf_token };
  await expect(page.getByRole('textbox', { name: 'Message Intelligence contact', exact: true })).toBeVisible();
  await page.getByRole('main').getByRole('button', { name: 'Ask Milo', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'A moment with Milo' });
  await dialog.getByRole('button', { name: 'Ask about this chat', exact: true }).click();
  await expect(dialog.getByLabel('Exact conversation')).toHaveValue(chat.id);
  const question = 'What do we know about the meeting plan?';
  await dialog.getByRole('textbox', { name: 'Your question about this chat' }).fill(question);
  const response = page.waitForResponse(value => value.url().includes('/api/assistant/commands') && value.ok());
  await dialog.getByRole('button', { name: 'Ask Milo', exact: true }).click();
  const answer = (await (await response).json()).result;
  expect(answer.audience).toBe('owner_only'); expect(answer.external_actions).toBe(false); expect(answer.development_mock).toBe(true);
  await expect(dialog.getByText(answer.text, { exact: true })).toBeVisible();
  const budget = await (await context.request.get(`${api}/workspaces/${workspace.id}/budget`)).json();
  expect(budget.usage.token_units).toBe(0); // Synthetic model calls do not invent real provider usage.
  const changedBudget = await context.request.put(`${api}/workspaces/${workspace.id}/budget`, { headers,
    data: { expected_version: budget.version, max_tokens_per_day: 100000 } });
  expect(changedBudget.ok()).toBe(true);
  const refresh = page.waitForResponse(value => value.url().includes('/api/ui/bootstrap') && value.ok());
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange'))); await refresh;
  await expect(dialog.getByText(answer.text, { exact: true })).toBeVisible();
  const revoked = await context.request.put(`${api}/conversations/${chat.id}/permissions`, { headers, data: { read: false, retain: true, learn: false, draft: false, send: false, share: false } });
  expect(revoked.ok()).toBe(true);
  const update = page.waitForResponse(value => value.url().includes('/api/ui/bootstrap') && value.ok());
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange'))); await update;
  await expect(dialog.getByText(answer.text, { exact: true })).toHaveCount(0);
  await expect(dialog.getByRole('textbox', { name: 'Your question about this chat' })).toHaveValue(question);
});

test('owner answer is confirmed against the newer server permission version before display', async ({ page, context }) => {
  const { chat } = await seed(context);
  const csrf = page.waitForResponse(response => response.url().includes('/api/auth/csrf') && response.ok());
  await page.goto(`/inbox/${chat.id}`);
  const headers = { 'X-CSRF-Token': (await (await csrf).json()).csrf_token };
  await expect(page.getByRole('textbox', { name: 'Message Intelligence contact', exact: true })).toBeVisible();
  await page.getByRole('main').getByRole('button', { name: 'Ask Milo', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'A moment with Milo' });
  await dialog.getByRole('button', { name: 'Ask about this chat', exact: true }).click();
  await dialog.getByRole('textbox', { name: 'Your question about this chat' }).fill('What do we know about the meeting?');
  let changed = false;
  await page.route('**/api/assistant/commands', async route => {
    // Change real SQL authority after the UI's snapshot and before generation.
    const update = await context.request.put(`${api}/conversations/${chat.id}/permissions`, { headers, data: { read: true, retain: true, learn: false, draft: false, send: false, share: false } });
    expect(update.ok()).toBe(true); changed = true;
    await route.continue();
  });
  const response = page.waitForResponse(value => value.url().includes('/api/assistant/commands') && value.ok());
  await dialog.getByRole('button', { name: 'Ask Milo', exact: true }).click();
  const answer = (await (await response).json()).result;
  expect(changed).toBe(true); expect(answer.authorization_context.permissions.learn).toBe(false);
  await expect(dialog.getByText(answer.text, { exact: true })).toBeVisible();
});

test('owner can answer a deep-linked chat beyond the first bootstrap page and refresh its authority', async ({ page, context }) => {
  test.setTimeout(60_000);
  const { chat, workspace } = await seed(context, 30);
  const firstPage = await (await context.request.get(`${api}/ui/bootstrap?workspace_id=${workspace.id}`)).json();
  expect(firstPage.conversations).toHaveLength(30);
  expect(firstPage.conversations.some((row: { id: string }) => row.id === chat.id)).toBe(false);
  const csrf = page.waitForResponse(response => response.url().includes('/api/auth/csrf') && response.ok());
  await page.goto(`/inbox/${chat.id}`);
  const headers = { 'X-CSRF-Token': (await (await csrf).json()).csrf_token };
  await expect(page.getByRole('textbox', { name: 'Message Intelligence contact', exact: true })).toBeVisible();
  await page.getByRole('main').getByRole('button', { name: 'Ask Milo', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'A moment with Milo' });
  await dialog.getByRole('button', { name: 'Ask about this chat', exact: true }).click();
  await expect(dialog.getByLabel('Exact conversation')).toHaveValue(chat.id);
  await dialog.getByRole('textbox', { name: 'Your question about this chat' }).fill('What do we know about the meeting plan?');
  const response = page.waitForResponse(value => value.url().includes('/api/assistant/commands') && value.ok());
  await dialog.getByRole('button', { name: 'Ask Milo', exact: true }).click();
  const answer = (await (await response).json()).result;
  await expect(dialog.getByText(answer.text, { exact: true })).toBeVisible();
  const refreshed = page.waitForResponse(value => value.url().includes('/api/ui/resolve') && value.url().includes(chat.id) && value.ok());
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange'))); await refreshed;
  await expect(dialog.getByText(answer.text, { exact: true })).toBeVisible();
  const revoked = await context.request.put(`${api}/conversations/${chat.id}/permissions`, { headers, data: { read: false, retain: true, learn: false, draft: false, send: false, share: false } });
  expect(revoked.ok()).toBe(true);
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await expect(dialog.getByText(answer.text, { exact: true })).toHaveCount(0);
});

test('synthetic demo explains owner intelligence configuration without making a model request', async ({ page }) => {
  let calls = 0;
  await page.route('**/api/assistant/commands', async route => { calls++; await route.abort(); });
  await page.goto('/');
  await page.getByRole('button', { name: 'Talk to Milo', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'A moment with Milo' });
  await dialog.getByRole('button', { name: 'Ask about this chat', exact: true }).click();
  await dialog.getByLabel('Exact conversation').selectOption({ index: 1 });
  await dialog.getByRole('textbox', { name: 'Your question about this chat' }).fill('What did we decide?');
  await dialog.getByRole('button', { name: 'Ask Milo', exact: true }).click();
  await expect(dialog.getByText('Conversation intelligence needs a signed-in workspace and a configured AI provider. The synthetic demo does not call a model.', { exact: true })).toBeVisible();
  expect(calls).toBe(0);
});
