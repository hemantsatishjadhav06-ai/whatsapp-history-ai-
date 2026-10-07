import { expect, test, type BrowserContext, type Page } from '@playwright/test';

const apiUrl = process.env.MILO_API_TEST_URL;
test.skip(!apiUrl, 'Requires a disposable loopback SQL API; never uses a provider account.');

async function freshOwner(context: BrowserContext, page: Page) {
  const signedIn = await context.request.post(`${apiUrl}/auth/dev`, { data: {
    email: `history-owner-${crypto.randomUUID()}@example.invalid`, display_name: 'History owner',
  } });
  expect(signedIn.ok()).toBe(true);
  expect(await (await context.request.get(`${apiUrl}/workspaces`)).json()).toEqual([]);
  const csrfResponse = page.waitForResponse(response => response.url().includes('/api/auth/csrf') && response.ok());
  await page.goto('/');
  const csrf = await (await csrfResponse).json();
  const headers = { 'X-CSRF-Token': csrf.csrf_token };
  await expect(page.getByRole('heading', { name: 'Make it yours', exact: true })).toBeVisible();
  await page.getByRole('textbox', { name: 'Workspace name', exact: true }).fill('My private imported history');
  await page.getByRole('combobox', { name: 'Timezone', exact: true }).selectOption('UTC');
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Connect carefully', exact: true })).toBeVisible();
  const workspaces = await (await context.request.get(`${apiUrl}/workspaces`)).json();
  expect(workspaces).toHaveLength(1);
  return { headers, workspace: workspaces[0] };
}

async function enterChat(page: Page, options: { title: string; group?: boolean; learn?: boolean; draft?: boolean }) {
  await page.getByRole('button', { name: 'Set up an export-only chat', exact: true }).click();
  const setup = page.getByRole('form', { name: 'Set up an export-only chat', exact: true });
  await setup.getByRole('textbox', { name: 'Exact owner sender label in the export', exact: true }).fill('Owner');
  await setup.getByRole('textbox', { name: 'Chat title', exact: true }).fill(options.title);
  await setup.getByRole('textbox', { name: 'Chat identifier for this export', exact: true }).fill(`local-${crypto.randomUUID()}`);
  await setup.getByLabel('Chat audience').selectOption(options.group ? 'group' : 'contact');
  await expect(setup.getByRole('button', { name: 'Create export-only chat', exact: true })).toBeDisabled();
  await setup.getByRole('checkbox', { name: 'Read this selected chat history', exact: true }).check();
  await setup.getByRole('checkbox', { name: 'Retain this history under my workspace retention policy', exact: true }).check();
  if (options.draft) await setup.getByRole('checkbox', { name: 'Prepare unsent drafts for my review', exact: true }).check();
  if (options.learn) await setup.getByRole('checkbox', { name: 'Learn voice and memory from permitted examples in this chat', exact: true }).check();
  return setup;
}

for (const scenario of [{ group: false, learn: true, draft: true }, { group: true, learn: false, draft: false }]) {
  test(`fresh owner imports a ${scenario.group ? 'group without learning' : 'contact with explicit learning'} through the UI`, async ({ context, page }) => {
    const { headers, workspace } = await freshOwner(context, page);
    const title = scenario.group ? 'Exported study group' : 'Exported Maya';
    const setup = await enterChat(page, { title, ...scenario });
    await setup.getByRole('button', { name: 'Create export-only chat', exact: true }).click();
    await expect(page.getByText(`${title} is ready for its selected history. Sending and sharing are disabled.`, { exact: true })).toBeVisible();
    const connectors = await (await context.request.get(`${apiUrl}/connectors?workspace_id=${workspace.id}`)).json();
    expect(connectors).toHaveLength(1);
    expect(connectors[0].provider).toBe('export_only');
    expect(connectors[0].capabilities.send_text).toBe('unsupported');
    expect(connectors[0].capabilities.history_sync).toBe('unsupported');
    const conversations = await (await context.request.get(`${apiUrl}/conversations?workspace_id=${workspace.id}`)).json();
    expect(conversations).toHaveLength(1);
    const chat = conversations[0];
    expect(chat.kind).toBe(scenario.group ? 'group' : 'contact');
    expect(chat.control_state).toBe(scenario.draft ? 'DRAFT_MODE' : 'READ_ONLY');
    const permission = await (await context.request.get(`${apiUrl}/conversations/${chat.id}/permissions`)).json();
    expect(permission).toMatchObject({ read: true, retain: true, draft: scenario.draft, learn: scenario.learn, send: false, share: false });
    await page.getByRole('button', { name: 'Import this chat’s history', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/connections/import/${chat.id}$`));
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel('Exact conversation')).toHaveValue(chat.id);
    await expect(dialog.getByRole('textbox', { name: 'Exact owner sender label in the export', exact: true })).toHaveValue('Owner');
    await expect(dialog).toContainText(scenario.learn ? 'Voice and memory learning is permitted' : 'Voice and memory learning is disabled');
    const file = {
      name: 'selected-history.txt', mimeType: 'text/plain',
      buffer: Buffer.from(`06/10/2026, 09:00 - Owner: Thanks, I’ll check the details.\n06/10/2026, 09:01 - ${title}: Saturday at 10?\n06/10/2026, 09:02 - Owner: Yes, that works for me.\n06/10/2026, 09:03 - ${title}: Great, see you then.`),
    };
    await dialog.getByLabel('WhatsApp text export · up to 2 MB').setInputFiles(file);
    await expect(dialog.getByRole('button', { name: 'Import selected history', exact: true })).toBeDisabled();
    // The owner identity is checked by the real parser, rather than accepted from a UI label.
    await dialog.getByRole('textbox', { name: 'Exact owner sender label in the export', exact: true }).fill('Not in this file');
    await dialog.getByRole('button', { name: 'Preview import', exact: true }).click();
    await expect(dialog.getByRole('alert')).toContainText('does not match an observed sender');
    await dialog.getByRole('textbox', { name: 'Exact owner sender label in the export', exact: true }).fill('Owner');
    await dialog.getByRole('button', { name: 'Preview import', exact: true }).click();
    await expect(dialog.getByRole('status').filter({ hasText: '4 records observed' })).toBeVisible();
    // A rejected replacement file must not leave the previous file ready for import.
    await dialog.getByLabel('WhatsApp text export · up to 2 MB').setInputFiles({ name: 'too-large.txt', mimeType: 'text/plain', buffer: Buffer.alloc(2_000_001, 'x') });
    await expect(dialog.getByRole('alert')).toContainText('smaller than 2 MB');
    await expect(dialog.getByRole('button', { name: 'Import selected history', exact: true })).toBeDisabled();
    await expect(dialog.getByRole('button', { name: 'Preview import', exact: true })).toBeDisabled();
    await dialog.getByLabel('WhatsApp text export · up to 2 MB').setInputFiles(file);
    await dialog.getByRole('button', { name: 'Preview import', exact: true }).click();
    await expect(dialog.getByRole('status').filter({ hasText: '4 records observed' })).toBeVisible();
    // Changing the interpretation invalidates the preview before any import is allowed.
    await dialog.getByRole('textbox', { name: 'Export timezone', exact: true }).fill('Asia/Kolkata');
    await expect(dialog.getByRole('button', { name: 'Import selected history', exact: true })).toBeDisabled();
    await dialog.getByRole('textbox', { name: 'Export timezone', exact: true }).fill('UTC');
    await dialog.getByRole('button', { name: 'Preview import', exact: true }).click();
    await expect(dialog.getByRole('status').filter({ hasText: '4 records observed' })).toBeVisible();
    await dialog.getByRole('button', { name: 'Import selected history', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole('status').filter({ hasText: 'Selected history imported. No outgoing action was created.' })).toBeVisible();
    const messages = await (await context.request.get(`${apiUrl}/conversations/${chat.id}/messages`)).json();
    expect(messages).toHaveLength(4);
    expect(messages.filter((message: { author_kind: string }) => message.author_kind === 'human_owner')).toHaveLength(2);
    expect(messages.every((message: { origin: string }) => message.origin === 'history')).toBe(true);
    const styles = await context.request.post(`${apiUrl}/conversations/${chat.id}/style-preview`, { headers });
    expect(styles.status()).toBe(scenario.learn ? 200 : 403);
    if (scenario.learn) expect(await styles.json()).toMatchObject({ sample_count: 2, sufficiency: 'provisional' });
    else expect(await styles.json()).toMatchObject({ detail: 'Conversation learn permission required' });
    const memory = await context.request.post(`${apiUrl}/conversations/${chat.id}/memories`, { headers, data: {
      text: `${title}: Saturday at 10 is an unconfirmed candidate.`, status: 'candidate', source_message_ids: [messages[1].id],
    } });
    expect(memory.status()).toBe(scenario.learn ? 201 : 403);
    if (!scenario.learn) expect(await memory.json()).toMatchObject({ detail: 'Conversation learn permission required' });
    await page.goto(`/inbox/${chat.id}`);
    await expect(page.getByRole('log')).toContainText('Saturday at 10?');
    const snapshot = await (await context.request.get(`${apiUrl}/ui/bootstrap`)).json();
    expect(snapshot.connections.map((row: { provider: string }) => row.provider)).toEqual(['export_only']);
    expect(snapshot.grants).toEqual([]);
    expect(snapshot.actions).toEqual([]);
    expect(snapshot.jobs).toEqual([]);
    expect(snapshot.memories).toHaveLength(scenario.learn ? 1 : 0);
  });
}

test('a failed permission step retries the same SQL chat without creating duplicate collections', async ({ context, page }) => {
  const { workspace } = await freshOwner(context, page);
  const setup = await enterChat(page, { title: 'Retry the same history chat', draft: true });
  let failed = false;
  await page.route('**/api/conversations/*/permissions', async route => {
    if (route.request().method() === 'PUT' && !failed) {
      failed = true;
      await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Temporary permission storage outage' }) });
    } else await route.continue();
  });
  await setup.getByRole('button', { name: 'Create export-only chat', exact: true }).click();
  await expect(setup.getByRole('alert')).toContainText('Temporary permission storage outage');
  await expect(setup.getByRole('textbox', { name: 'Chat title', exact: true })).toBeDisabled();
  await setup.getByRole('button', { name: 'Finish chat permissions', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Import this chat’s history', exact: true })).toBeVisible();
  const connectors = await (await context.request.get(`${apiUrl}/connectors?workspace_id=${workspace.id}`)).json();
  const conversations = await (await context.request.get(`${apiUrl}/conversations?workspace_id=${workspace.id}`)).json();
  expect(connectors).toHaveLength(1);
  expect(conversations).toHaveLength(1);
  expect(await (await context.request.get(`${apiUrl}/conversations/${conversations[0].id}/permissions`)).json()).toMatchObject({ read: true, retain: true, draft: true, send: false, share: false });
});
