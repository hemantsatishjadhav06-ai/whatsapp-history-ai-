import { expect, test, type BrowserContext, type Page } from '@playwright/test';

const apiUrl = process.env.MILO_API_TEST_URL;
test.skip(!apiUrl, 'Requires the disposable API fixture; all data and connectors are synthetic.');

async function seed(context: BrowserContext) {
  const marker = crypto.randomUUID();
  const authResponse = await context.request.post(`${apiUrl}/auth/dev`, { data: {
    email: `assistant-privacy-${marker}@example.invalid`, display_name: 'Privacy fixture owner',
  } });
  expect(authResponse.ok()).toBe(true);
  const headers = { 'X-CSRF-Token': (await authResponse.json()).csrf_token };
  const workspaceResponse = await context.request.post(`${apiUrl}/workspaces`, { headers,
    data: { name: 'Assistant privacy fixture', timezone: 'UTC' } });
  expect(workspaceResponse.ok()).toBe(true);
  const workspace = await workspaceResponse.json();
  const connectorResponse = await context.request.post(`${apiUrl}/connectors`, { headers, data: {
    workspace_id: workspace.id, provider: 'mock', account_id: marker, owner_sender_id: 'Owner',
  } });
  expect(connectorResponse.ok()).toBe(true);
  const conversationResponse = await context.request.post(`${apiUrl}/conversations`, { headers, data: {
    connector_id: (await connectorResponse.json()).id, provider_chat_id: marker,
    title: 'Privacy Maya', recipient_opted_in: true,
  } });
  expect(conversationResponse.ok()).toBe(true);
  const conversation = await conversationResponse.json();
  const permissions = await context.request.put(`${apiUrl}/conversations/${conversation.id}/permissions`, {
    headers, data: { read: true, retain: true, learn: true, draft: true, send: false, share: false },
  });
  expect(permissions.ok()).toBe(true);
  const exportTime = (stamp: Date) => `${stamp.getUTCDate()}/${stamp.getUTCMonth() + 1}/${stamp.getUTCFullYear()}, ${stamp.getUTCHours()}:${String(stamp.getUTCMinutes()).padStart(2, '0')}`;
  const ownerSecret = `Synthetic owner evidence ${marker}`;
  const contactSecret = `Synthetic private update ${marker}`;
  const imported = await context.request.post(`${apiUrl}/imports`, { headers, data: {
    conversation_id: conversation.id, owner_sender_label: 'Owner', date_order: 'DMY', timezone: 'UTC',
    text: `${exportTime(new Date(Date.now() - 10 * 60_000))} - Owner: ${ownerSecret}\n${exportTime(new Date(Date.now() - 9 * 60_000))} - Privacy Maya: ${contactSecret}`,
  } });
  expect(imported.ok()).toBe(true);
  const messages = await (await context.request.get(`${apiUrl}/conversations/${conversation.id}/messages`)).json();
  const ownerSource = messages.find((row: { text: string }) => row.text === ownerSecret);
  expect(ownerSource).toBeTruthy();
  const memoryResponse = await context.request.post(`${apiUrl}/conversations/${conversation.id}/memories`, {
    headers, data: { text: 'Synthetic short-reply preference', source_message_ids: [ownerSource.id], status: 'candidate' },
  });
  expect(memoryResponse.ok()).toBe(true);
  return { conversation, memory: await memoryResponse.json(), ownerSecret, contactSecret };
}

async function openAssistant(page: Page, conversationId: string) {
  const csrfResponse = page.waitForResponse(response => response.url().includes('/api/auth/csrf') && response.ok());
  await page.goto(`/inbox/${conversationId}`);
  const headers = { 'X-CSRF-Token': (await (await csrfResponse).json()).csrf_token };
  await expect(page.getByRole('textbox', { name: 'Message Privacy Maya', exact: true })).toBeVisible();
  await page.getByRole('main').getByRole('button', { name: 'Ask Milo', exact: true }).click();
  return { dialog: page.getByRole('dialog', { name: 'A moment with Milo', exact: true }), headers };
}

async function revalidate(page: Page) {
  const refreshed = page.waitForResponse(response => response.url().includes('/api/ui/bootstrap') && response.ok());
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await refreshed;
}

async function settleRender(page: Page) {
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}

test('revocation clears an open digest and rejects a late command while preserving an unsent instruction', async ({ page, context }) => {
  const fixture = await seed(context);
  const { dialog, headers } = await openAssistant(page, fixture.conversation.id);
  const instruction = 'Keep this typed instruction unsent.';
  await dialog.getByRole('textbox', { name: 'Ask Milo', exact: true }).fill(instruction);
  await dialog.getByRole('button', { name: 'Ask Milo', exact: true }).click();
  await expect(dialog.getByText(fixture.contactSecret, { exact: true })).toBeVisible();

  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let captured = false;
  await page.route('**/api/assistant/commands', async route => {
    const response = await route.fetch();
    captured = true;
    await held;
    await route.fulfill({ response });
  });
  try {
    await dialog.getByRole('button', { name: 'Ask Milo', exact: true }).click();
    await expect.poll(() => captured).toBe(true);
    const revoked = await context.request.put(`${apiUrl}/conversations/${fixture.conversation.id}/permissions`, {
      headers, data: { read: false, retain: true, learn: false, draft: false, send: false, share: false },
    });
    expect(revoked.ok()).toBe(true);
    await revalidate(page);
    await expect(dialog.getByText(fixture.contactSecret, { exact: true })).toHaveCount(0);
    await expect(dialog.getByRole('textbox', { name: 'Ask Milo', exact: true })).toHaveValue(instruction);
    const completed = page.waitForResponse(response => response.url().includes('/api/assistant/commands'));
    release();
    await completed;
    await settleRender(page);
    await expect(dialog.getByText(fixture.contactSecret, { exact: true })).toHaveCount(0);
    await expect(dialog.getByRole('textbox', { name: 'Ask Milo', exact: true })).toHaveValue(instruction);
  } finally { release(); }
});

test('forgetting clears Teach evidence and discards a late source response while retaining raw inbox history', async ({ page, context }) => {
  const fixture = await seed(context);
  const { dialog, headers } = await openAssistant(page, fixture.conversation.id);
  await dialog.getByRole('button', { name: 'Teach me', exact: true }).click();
  await expect(dialog.getByRole('checkbox', { name: fixture.ownerSecret })).toBeVisible();
  const instruction = 'Keep my unfinished preference here.';
  await dialog.getByRole('textbox', { name: 'Preference or fact to review', exact: true }).fill(instruction);

  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let captured = false;
  await page.route(`**/api/conversations/${fixture.conversation.id}/messages?derived_evidence=true`, async route => {
    if (captured) { await route.continue(); return; }
    const response = await route.fetch();
    captured = true;
    await held;
    await route.fulfill({ response });
  });
  try {
    await dialog.getByRole('button', { name: 'Catch me up', exact: true }).click();
    await dialog.getByRole('button', { name: 'Teach me', exact: true }).click();
    await expect.poll(() => captured).toBe(true);
    const forgotten = await context.request.delete(`${apiUrl}/memories/${fixture.memory.id}`, { headers });
    expect(forgotten.ok()).toBe(true);
    await revalidate(page);
    await expect(dialog.getByRole('checkbox', { name: fixture.contactSecret })).toBeVisible();
    await expect(dialog.getByRole('checkbox', { name: fixture.ownerSecret })).toHaveCount(0);
    const completed = page.waitForResponse(response => response.url().includes('/messages?derived_evidence=true'));
    release();
    await completed;
    await settleRender(page);
    await expect(dialog.getByRole('checkbox', { name: fixture.ownerSecret })).toHaveCount(0);
    await expect(dialog.getByRole('textbox', { name: 'Preference or fact to review', exact: true })).toHaveValue(instruction);
    const raw = await (await context.request.get(`${apiUrl}/conversations/${fixture.conversation.id}/messages`)).json();
    expect(raw.some((row: { text: string }) => row.text === fixture.ownerSecret)).toBe(true);
  } finally { release(); }
});
