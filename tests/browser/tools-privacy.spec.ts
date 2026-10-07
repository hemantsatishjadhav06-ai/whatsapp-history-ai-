import { expect, test, type BrowserContext, type Page } from '@playwright/test';

const apiUrl = process.env.MILO_API_TEST_URL;
test.skip(!apiUrl, 'Requires disposable synthetic SQL data; no provider account is used.');

async function seed(context: BrowserContext) {
  const marker = crypto.randomUUID();
  const auth = await context.request.post(`${apiUrl}/auth/dev`, { data: {
    email: `tools-privacy-${marker}@example.invalid`, display_name: 'Tools privacy owner',
  } });
  expect(auth.ok()).toBe(true);
  const headers = { 'X-CSRF-Token': (await auth.json()).csrf_token };
  const workspace = await context.request.post(`${apiUrl}/workspaces`, { headers,
    data: { name: 'Tools privacy fixture', timezone: 'UTC' } });
  expect(workspace.ok()).toBe(true);
  const connector = await context.request.post(`${apiUrl}/connectors`, { headers, data: {
    workspace_id: (await workspace.json()).id, provider: 'mock', account_id: marker, owner_sender_id: 'Owner',
  } });
  expect(connector.ok()).toBe(true);
  const conversationResponse = await context.request.post(`${apiUrl}/conversations`, { headers, data: {
    connector_id: (await connector.json()).id, provider_chat_id: marker, title: 'Tools private chat',
  } });
  expect(conversationResponse.ok()).toBe(true);
  const conversation = await conversationResponse.json();
  expect((await context.request.put(`${apiUrl}/conversations/${conversation.id}/permissions`, { headers,
    data: { read: true, retain: true, learn: true, draft: true, send: false, share: false },
  })).ok()).toBe(true);
  const secret = `Synthetic Tools source ${marker}`;
  expect((await context.request.post(`${apiUrl}/imports`, { headers, data: {
    conversation_id: conversation.id, owner_sender_label: 'Owner', date_order: 'DMY', timezone: 'UTC',
    text: `06/10/2026, 09:00 - Owner: ${secret}`,
  } })).ok()).toBe(true);
  const messages = await (await context.request.get(`${apiUrl}/conversations/${conversation.id}/messages`)).json();
  const memoryText = `Synthetic Tools memory ${marker}`;
  const memory = await context.request.post(`${apiUrl}/conversations/${conversation.id}/memories`, { headers,
    data: { text: memoryText, source_message_ids: [messages[0].id], status: 'candidate' },
  });
  expect(memory.ok()).toBe(true);
  return { conversation, memory: await memory.json(), secret, memoryText };
}

async function revalidate(page: Page) {
  const response = page.waitForResponse(result => result.url().includes('/api/ui/bootstrap') && result.ok());
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await response;
}

test('same-owner forgetting removes an open Tools memory and discards its late private detail', async ({ page, context }) => {
  const fixture = await seed(context);
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let captured = false;
  await page.route(`**/api/conversations/${fixture.conversation.id}/messages`, async route => {
    if (captured) { await route.continue(); return; }
    const response = await route.fetch();
    captured = true;
    await held;
    await route.fulfill({ response });
  });
  try {
    const csrf = page.waitForResponse(response => response.url().includes('/api/auth/csrf') && response.ok());
    await page.goto(`/memory/${fixture.memory.id}`);
    const headers = { 'X-CSRF-Token': (await (await csrf).json()).csrf_token };
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByRole('textbox', { name: 'Remembered context' })).toHaveValue(fixture.memoryText);
    await expect.poll(() => captured).toBe(true);
    // Polling the same authorized snapshot must preserve an unfinished edit.
    await dialog.getByRole('textbox', { name: 'Remembered context' }).fill('Unfinished owner correction');
    await revalidate(page);
    await expect(dialog.getByRole('textbox', { name: 'Remembered context' })).toHaveValue('Unfinished owner correction');
    const forgotten = await context.request.delete(`${apiUrl}/memories/${fixture.memory.id}`, { headers });
    expect(forgotten.ok()).toBe(true);
    await revalidate(page);
    await expect(dialog).toHaveCount(0);
    const completed = page.waitForResponse(response => response.url().includes(`/conversations/${fixture.conversation.id}/messages`));
    release();
    await completed;
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(dialog).toHaveCount(0);
    await expect(page.getByText(fixture.secret, { exact: true })).toHaveCount(0);
    await expect(page.getByText(fixture.memoryText, { exact: true })).toHaveCount(0);
    const originals = await (await context.request.get(`${apiUrl}/conversations/${fixture.conversation.id}/messages`)).json();
    expect(originals.some((row: { text: string }) => row.text === fixture.secret)).toBe(true);
  } finally { release(); }
});
