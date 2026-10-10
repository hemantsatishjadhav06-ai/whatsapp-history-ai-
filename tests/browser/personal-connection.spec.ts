import { expect, test, type Page } from '@playwright/test';

// Local synthetic pairing material only. These fixtures contact no WhatsApp account.
const syntheticQR = 'synthetic-owner-only,temporary-linking-data';
function snapshot(owner = 'owner-a', workspace = 'workspace-a', version = 'v1') {
  return { user: { id: owner, display_name: 'Pairing fixture owner', email: `${owner}@example.invalid` },
    workspace: { id: workspace, name: 'Synthetic pairing fixture', timezone: 'UTC', paused: false, pause_generation: 0 },
    workspaces: [], connections: [], conversations: [], messages: [], actions: [], drafts: [], tasks: [], jobs: [], memories: [],
    styles: [], grants: [], routes: [], activity: [], contacts: [], budget: null, retention: null, simulation: false,
    generated_at: '2026-10-07T12:00:00Z', snapshot_version: version };
}
async function fixture(page: Page, options: { enabled?: boolean; connected?: boolean; expiresAfter?: number; savedChat?: boolean; automaticExpiresAfter?: number; linkCode?: boolean; importMode?: 'all' | 'selected'; syncPhase?: string | null; linkedAgoMs?: number } = {}) {
  let importMode = options.importMode ?? 'selected'; const modeWrites: Record<string, unknown>[] = [];
  let currentSnapshot: Record<string, unknown> = snapshot(); let enabled = options.enabled ?? true;
  let connected = options.connected ?? false; let started = connected; let hold: Promise<void> | null = null;
  const expiresAt = new Date(Date.now() + (options.expiresAfter ?? 45_000)).toISOString();
  const changes: { path: string; body: Record<string, unknown> }[] = []; let pairingCount = 0;
  let automatic = { conversation_id: 'saved-personal-chat', enabled: options.automaticExpiresAfter !== undefined, version: options.automaticExpiresAfter === undefined ? 0 : 1, expires_at: (options.automaticExpiresAfter === undefined ? null : new Date(Date.now() + options.automaticExpiresAfter).toISOString()) as string | null, max_drafts_per_hour: 3, status: options.automaticExpiresAfter === undefined ? 'disabled' : 'ready', reason_code: null as string | null, latest_job: null };
  const automaticWrites: Record<string, unknown>[] = [];
  let statusUnavailable = false;
  let writing: Record<string, unknown>[] = []; let heldWriting: Promise<void> | null = null; let writingCount = 0;
  const connector = () => ({ id: 'personal-fixture', workspace_id: String((currentSnapshot.workspace as { id: string }).id), provider: 'whatsapp_personal', account_id: 'synthetic-phone', status: connected ? 'connected' : 'pairing', capabilities: {}, fence: 1 });
  const status = () => ({ enabled, configured: enabled, connected, simulation: false, status: connected ? 'connected' : started ? 'pairing' : 'not_connected', connector: started ? connector() : null });
  if (connected) currentSnapshot = { ...currentSnapshot, connections: [connector()] };
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/config') return route.fulfill({ json: { backend_configured: true, google_configured: false, model: { status: 'disabled' } } });
    if (path === '/api/me') return route.fulfill({ json: currentSnapshot.user });
    if (path === '/api/auth/csrf') return route.fulfill({ json: { csrf_token: 'synthetic-pairing-csrf' } });
    if (path === '/api/ui/bootstrap') return route.fulfill({ json: currentSnapshot });
    if (path === '/api/integrations/whatsapp/status') return route.fulfill({ json: { configured: false, owner_authorized: false, webhook_configured: false, external_sends_enabled: false, missing_requirements: [], connectors: [] } });
    if (path.endsWith('/personal/config')) return route.fulfill({ json: { enabled, configured: enabled, status: enabled ? 'configured' : 'disabled', simulation: false, google_verified_required: true, capabilities: {}, live_verified: false } });
    if (path.endsWith('/personal/status')) return route.fulfill(statusUnavailable ? { status: 503, json: { detail: 'Synthetic status outage' } } : { json: status() });
    if (path.endsWith('/personal/pairing')) {
      pairingCount++; const held = hold; const body = { connector_id: 'personal-fixture', state: 'pairing', qr: { value: syntheticQR, expires_at: expiresAt }, poll_after_seconds: 3, pairing_code: options.linkCode ? { code: 'ABCD2345', expires_at: new Date(Date.now() + 120_000).toISOString() } : null };
      if (held) await held; return route.fulfill({ json: body });
    }
    if (path.endsWith('/personal/sync')) return route.fulfill({ json: { workspace_id: 'workspace-a', import_mode: importMode, connector_id: 'personal-fixture',
      chats: importMode === 'all' ? 182 : 1, messages: importMode === 'all' ? 24518 : 3, phase: options.syncPhase !== undefined ? options.syncPhase : importMode === 'all' ? 'full' : 'complete', progress: importMode === 'all' ? 64 : 100,
      last_sync_at: new Date().toISOString(), linked_at: new Date(Date.now() - (options.linkedAgoMs ?? 3_600_000)).toISOString(), oldest_message_at: '2021-03-04T10:00:00Z', backfill_pending: importMode === 'all' ? 12 : 0, backfill_complete: 0 } });
    if (path.endsWith('/personal/preferences') && route.request().method() === 'PUT') {
      const body = route.request().postDataJSON() as Record<string, unknown>; modeWrites.push(body);
      importMode = body.import_mode === 'all' ? 'all' : 'selected'; return route.fulfill({ json: body });
    }
    if (path.endsWith('/personal/chats')) return route.fulfill({ json: { connector_id: 'personal-fixture', chats: [{ provider_chat_id: 'synthetic-peer', title: 'Synthetic person', kind: 'contact', conversation_id: options.savedChat ? 'saved-personal-chat' : null, permissions: options.savedChat ? { read: true, retain: true, draft: true } : {} }, { provider_chat_id: 'unsupported-group', title: 'Unsupported group', kind: 'group', conversation_id: null }], limit: 50, has_more: false } });
    if (path === '/api/conversations/saved-personal-chat/messages') { writingCount++; const response = writing; const held = heldWriting; if (held) await held; return route.fulfill({ json: response }); }
    if (path.endsWith('/automatic-drafts')) {
      if (route.request().method() === 'PUT') {
        const body = route.request().postDataJSON() as Record<string, unknown>; automaticWrites.push(body);
        automatic = { ...automatic, enabled: body.enabled === true, version: automatic.version + 1, expires_at: body.enabled ? String(body.expires_at) : automatic.expires_at, max_drafts_per_hour: Number(body.max_drafts_per_hour ?? automatic.max_drafts_per_hour), status: body.enabled ? 'blocked' : 'disabled', reason_code: body.enabled ? 'MODEL_DISABLED' : null };
      }
      return route.fulfill({ json: automatic });
    }
    if (route.request().method() === 'POST') {
      changes.push({ path, body: route.request().postDataJSON() as Record<string, unknown> });
      if (path.endsWith('/personal/start')) { started = true; return route.fulfill({ json: status() }); }
      if (path.endsWith('/personal/disconnect')) { started = false; connected = false; return route.fulfill({ json: { status: 'disconnected', connector_id: 'personal-fixture', fence: 2 } }); }
      if (path.endsWith('/personal/authorship/confirm')) return route.fulfill({ json: { status: 'confirmed', conversation_id: 'saved-personal-chat', confirmed_count: (changes.at(-1)!.body.message_ids as string[]).length } });
      if (path.endsWith('/personal/chats/authorize')) return route.fulfill({ json: { conversation: { id: 'saved-personal-chat' }, permissions: changes.at(-1)!.body } });
    }
    return route.fulfill({ status: 404, json: { detail: 'Unavailable synthetic fixture route' } });
  });
  return { changes, automaticWrites, modeWrites, failStatus: () => { statusUnavailable = true; }, setWriting: (rows: Record<string, unknown>[]) => { writing = rows; }, holdWriting: (promise: Promise<void> | null) => { heldWriting = promise; }, writingCount: () => writingCount, pairingCount: () => pairingCount, hold: (value: Promise<void> | null) => { hold = value; },
    switchOwner: () => { enabled = false; currentSnapshot = snapshot('owner-b', 'workspace-b', 'changed-owner'); } };
}
const panel = (page: Page) => page.getByRole('region', { name: 'WhatsApp phone connection', exact: true });
async function startLinking(page: Page, number = '+1 555 000 0000') {
  await panel(page).getByRole('textbox', { name: 'Your WhatsApp number, with country code', exact: true }).fill(number);
  await panel(page).getByRole('button', { name: 'Get my link code', exact: true }).click();
}

test('all-chats mode reads every chat without per-chat approval and shows import progress', async ({ page }) => {
  const data = await fixture(page, { connected: true, importMode: 'all' }); await page.goto('/connections');
  const sync = panel(page).getByRole('region', { name: 'WhatsApp sync', exact: true });
  await expect(panel(page)).toContainText('Your phone is linked. Milo is reading all your chats.');
  await expect(sync).toContainText('24,518'); await expect(sync).toContainText('182');
  await expect(sync).toContainText('Importing your WhatsApp history · 64%');
  await expect(sync).toContainText('12 chats to go');
  await expect(sync.getByRole('radio', { name: /All my chats/ })).toBeChecked();
  // Per-chat controls stay available but collapsed; nothing asks for approval to read.
  await expect(panel(page).getByRole('form', { name: 'Choose one linked WhatsApp conversation', exact: true })).toBeHidden();
  await sync.getByRole('radio', { name: /Only chats I choose/ }).check();
  await expect.poll(() => data.modeWrites).toEqual([{ workspace_id: 'workspace-a', import_mode: 'selected' }]);
  await expect(panel(page)).toContainText('Your phone is linked. Choose the chats Milo may read.');
  expect(data.changes).toEqual([]);
});

test('a link whose history never came gets clear steps, and backfill is not shown as an import', async ({ page }) => {
  await fixture(page, { connected: true, importMode: 'all', syncPhase: 'on_demand' }); await page.goto('/connections');
  const sync = panel(page).getByRole('region', { name: 'WhatsApp sync', exact: true });
  await expect(sync).toContainText('WhatsApp has not sent your past chats for this link.');
  await expect(sync).toContainText('remove “Milo” under WhatsApp → Linked devices, then link again here. Chats already in Milo stay.');
  await expect(sync).toContainText('Your chats are in Milo. New messages arrive live.');
  await expect(sync).toContainText('12 chats to go');
  await expect(sync).not.toContainText('Importing your WhatsApp history');
});

test('a fresh link waits for WhatsApp before suggesting a new link', async ({ page }) => {
  await fixture(page, { connected: true, importMode: 'all', syncPhase: null, linkedAgoMs: 30_000 }); await page.goto('/connections');
  const sync = panel(page).getByRole('region', { name: 'WhatsApp sync', exact: true });
  await expect(sync).toContainText('24,518');
  await expect(sync).not.toContainText('WhatsApp has not sent your past chats');
});

test('an unavailable pilot cannot show a pairing code or start a connection', async ({ page }) => {
  const data = await fixture(page, { enabled: false }); await page.goto('/connections');
  await expect(panel(page)).toContainText('Phone linking is not available in this deployment yet.');
  await expect(panel(page).getByRole('button', { name: 'Get my link code', exact: true })).toHaveCount(0);
  await expect(panel(page).getByRole('img')).toHaveCount(0); expect(data.changes).toEqual([]);
});

test('a failed status refresh cannot keep claiming a currently linked phone or showing old private chats', async ({ page }) => {
  const data = await fixture(page, { connected: true, savedChat: true }); await page.goto('/connections');
  await expect(panel(page)).toContainText('Your phone is linked. Choose the chats Milo may read.');
  await panel(page).getByRole('combobox', { name: 'Individual WhatsApp conversation', exact: true }).selectOption('synthetic-peer');
  data.failStatus(); await panel(page).getByRole('button', { name: 'Refresh phone linking', exact: true }).click();
  await expect(panel(page)).toContainText('Current phone linking is unconfirmed. Refresh linking to review its current state.');
  await expect(panel(page)).not.toContainText('Your phone is linked. Choose the chats Milo may read.');
  await expect(panel(page).getByRole('form', { name: 'Choose one linked WhatsApp conversation', exact: true })).toHaveCount(0);
  await expect(panel(page).getByRole('button', { name: 'Disconnect this linked device', exact: true })).toBeEnabled();
  expect(data.changes).toEqual([]);
});

test('linking draws the real supplied code locally and never exposes its text or image URL', async ({ page }) => {
  const data = await fixture(page); const external: string[] = [];
  page.on('request', request => { if (!request.url().startsWith('http://127.0.0.1') && !request.url().startsWith('http://localhost')) external.push(request.url()); });
  await page.goto('/connections'); await startLinking(page);
  await expect(panel(page).getByRole('img', { name: 'Temporary WhatsApp linking QR code', exact: true })).toBeVisible();
  expect(await panel(page).innerHTML()).not.toContain(syntheticQR);
  expect(await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }))).not.toContain(syntheticQR);
  expect(external).toEqual([]); expect(data.changes).toEqual([{ path: '/api/integrations/whatsapp/personal/start', body: { workspace_id: 'workspace-a', phone_number: '+15550000000' } }]);
  await page.goto('/'); await expect(page.getByRole('img', { name: 'Temporary WhatsApp linking QR code', exact: true })).toHaveCount(0);
});

test('a number-bound link code is shown with phone steps and the QR stays a secondary option', async ({ page }) => {
  const data = await fixture(page, { linkCode: true }); await page.goto('/connections');
  await expect(panel(page).getByRole('button', { name: 'Get my link code', exact: true })).toBeDisabled();
  await panel(page).getByRole('textbox', { name: 'Your WhatsApp number, with country code', exact: true }).fill('7697874277');
  await expect(panel(page).getByRole('button', { name: 'Get my link code', exact: true })).toBeDisabled();
  await startLinking(page, '+91 76978 74277');
  await expect(panel(page).getByText('ABCD-2345', { exact: true })).toBeVisible();
  await expect(panel(page)).toContainText('Link with phone number instead');
  await expect(panel(page).getByRole('img')).toBeHidden();
  expect(data.changes).toEqual([{ path: '/api/integrations/whatsapp/personal/start', body: { workspace_id: 'workspace-a', phone_number: '+917697874277' } }]);
});

test('an expired pairing code is removed without being kept ready to scan', async ({ page }) => {
  await page.clock.install(); await fixture(page, { expiresAfter: 2_500 }); await page.goto('/connections');
  await startLinking(page);
  await expect(panel(page).getByRole('img')).toBeVisible(); await page.clock.fastForward(2_600);
  await expect(panel(page).getByRole('img')).toHaveCount(0); await expect(panel(page)).toContainText('Expired codes are removed automatically.');
});

test('a backgrounded browser clears pairing material and requests a current code on return', async ({ page }) => {
  const data = await fixture(page); await page.goto('/connections');
  await startLinking(page);
  await expect(panel(page).getByRole('img')).toBeVisible(); const count = data.pairingCount();
  await page.evaluate(() => { Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' }); document.dispatchEvent(new Event('visibilitychange')); });
  await expect(panel(page).getByRole('img')).toHaveCount(0);
  await page.evaluate(() => { Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' }); document.dispatchEvent(new Event('visibilitychange')); });
  await expect.poll(() => data.pairingCount()).toBeGreaterThan(count); await expect(panel(page).getByRole('img')).toBeVisible();
});

test('an owner switch drops an older pairing response and its private material', async ({ page }) => {
  const data = await fixture(page); let release!: () => void; const held = new Promise<void>(resolve => { release = resolve; }); data.hold(held);
  await page.goto('/connections'); await startLinking(page);
  await expect.poll(() => data.pairingCount()).toBeGreaterThan(0); data.switchOwner(); data.hold(null);
  const updated = page.waitForResponse(response => response.url().includes('/api/ui/bootstrap'));
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange'))); await updated;
  await expect(panel(page)).toContainText('Phone linking is not available in this deployment yet.'); release();
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(panel(page).getByRole('img')).toHaveCount(0); expect(await panel(page).innerHTML()).not.toContain(syntheticQR);
});

test('linked direct chats require separate choices and never create a reply or Auto rule', async ({ page }) => {
  const data = await fixture(page, { connected: true }); await page.goto('/connections'); const connection = panel(page);
  await expect(page.getByText('WhatsApp phone · Selected scopes', { exact: true })).toBeVisible();
  await expect(page.getByText('WhatsApp phone · pilot', { exact: true })).toBeVisible();
  const form = connection.getByRole('form', { name: 'Choose one linked WhatsApp conversation', exact: true });
  await form.getByRole('combobox', { name: 'Individual WhatsApp conversation', exact: true }).selectOption('synthetic-peer');
  await expect(form.getByRole('option', { name: 'Unsupported group', exact: true })).toHaveCount(0);
  for (const choice of await form.getByRole('checkbox').all()) await expect(choice).not.toBeChecked();
  await form.getByRole('checkbox', { name: 'Allow sending under my separate rules', exact: true }).check();
  await expect(form.getByRole('button', { name: 'Save this conversation’s choices', exact: true })).toBeDisabled();
  await form.getByRole('checkbox', { name: 'Read messages in this conversation', exact: true }).check();
  await form.getByRole('checkbox', { name: 'Keep this conversation under my retention policy', exact: true }).check();
  await expect(form.getByRole('button', { name: 'Save this conversation’s choices', exact: true })).toBeDisabled();
  await form.getByRole('checkbox', { name: 'This person has agreed to receive my replies', exact: true }).check();
  await form.getByRole('button', { name: 'Save this conversation’s choices', exact: true }).click(); await expect.poll(() => data.changes.length).toBe(1);
  expect(data.changes[0]).toEqual({ path: '/api/integrations/whatsapp/personal/chats/authorize', body: { connector_id: 'personal-fixture', provider_chat_id: 'synthetic-peer', title: 'Synthetic person', read: true, retain: true, learn: false, draft: false, send: true, recipient_opted_in: true } });
});

test('ordinary draft permission leaves automatic preparation off until an explicit bounded choice, with honest model holds and revocation', async ({ page }) => {
  const data = await fixture(page, { connected: true, savedChat: true }); await page.goto('/connections');
  await panel(page).getByRole('combobox', { name: 'Individual WhatsApp conversation', exact: true }).selectOption('synthetic-peer');
  await expect(panel(page).getByRole('checkbox', { name: 'Prepare unsent replies for my review', exact: true })).toBeChecked();
  const choice = page.getByRole('region', { name: 'Automatic drafts for Synthetic person', exact: true });
  await expect(choice).toContainText('Off · no automatic draft preparation is authorized.');
  expect(data.automaticWrites).toEqual([]); expect(data.changes).toEqual([]);
  await choice.getByRole('spinbutton', { name: 'Authorize for days', exact: true }).fill('1');
  await choice.getByRole('spinbutton', { name: 'Maximum drafts per hour', exact: true }).fill('2');
  await choice.getByRole('button', { name: 'Enable automatic draft preparation', exact: true }).click();
  await expect(choice).toContainText('Enabled · preparation is blocked. The writing model is disabled in this deployment.');
  await expect(choice).toContainText('maximum 2 drafts per hour');
  expect(data.automaticWrites).toHaveLength(1);
  expect(data.automaticWrites[0]).toMatchObject({ enabled: true, expected_version: 0, max_drafts_per_hour: 2 });
  const remaining = Date.parse(String(data.automaticWrites[0].expires_at)) - Date.now();
  expect(remaining).toBeGreaterThan(86_390_000); expect(remaining).toBeLessThanOrEqual(86_400_000);
  await choice.getByRole('button', { name: 'Revoke automatic draft preparation', exact: true }).click();
  await expect(choice).toContainText('Off · no automatic draft preparation is authorized.');
  expect(data.automaticWrites[1]).toEqual({ enabled: false, expected_version: 1 });
  expect(data.changes).toEqual([]);
});

test('automatic preparation loses ready status at expiry without a fresh server result', async ({ page }) => {
  await page.clock.install(); const data = await fixture(page, { connected: true, savedChat: true, automaticExpiresAfter: 2_500 });
  await page.goto('/connections'); await panel(page).getByRole('combobox', { name: 'Individual WhatsApp conversation', exact: true }).selectOption('synthetic-peer');
  const choice = page.getByRole('region', { name: 'Automatic drafts for Synthetic person', exact: true });
  await expect(choice).toContainText('Enabled · eligible messages can prepare drafts.');
  await page.clock.fastForward(2_600);
  await expect(choice).toContainText('Enabled · preparation is blocked. Your automatic draft choice has expired.');
  expect(data.automaticWrites).toEqual([]);
});

test('phone writing needs exact per-message owner confirmation and excludes assistant or unrelated text', async ({ page }) => {
  const data = await fixture(page, { connected: true, savedChat: true });
  const row = { id: 'synthetic-owner-writing', conversation_id: 'saved-personal-chat', text: 'A synthetic reply I wrote on my phone.', direction: 'outbound', author_kind: 'unknown_owner_outgoing' };
  data.setWriting([row, { ...row, id: 'assistant-echo', text: 'Synthetic assistant text.', author_kind: 'assistant' }, { ...row, id: 'other-chat', conversation_id: 'other-chat', text: 'Other private scope.' }]);
  await page.goto('/connections'); await panel(page).getByRole('combobox', { name: 'Individual WhatsApp conversation', exact: true }).selectOption('synthetic-peer');
  const review = page.getByRole('region', { name: 'Your writing examples for Synthetic person', exact: true });
  await review.getByRole('button', { name: 'Read latest writing examples', exact: true }).click();
  await expect(review.getByRole('checkbox')).toHaveCount(1); await expect(review.getByRole('checkbox')).not.toBeChecked();
  await expect(review).not.toContainText('Synthetic assistant text.'); await expect(review).not.toContainText('Other private scope.');
  await expect(review.getByRole('button', { name: 'Confirm selected messages are my writing', exact: true })).toBeDisabled();
  expect(data.changes).toEqual([]);
  await review.getByRole('checkbox').check(); await review.getByRole('button', { name: 'Confirm selected messages are my writing', exact: true }).click();
  await expect.poll(() => data.changes.length).toBe(1);
  expect(data.changes[0]).toEqual({ path: '/api/integrations/whatsapp/personal/authorship/confirm', body: { conversation_id: 'saved-personal-chat', message_ids: ['synthetic-owner-writing'], confirm_authored_by_owner: true } });
  expect(data.automaticWrites).toEqual([]);
});

test('an owner switch drops a pending phone-writing review without displaying or confirming its text', async ({ page }) => {
  const data = await fixture(page, { connected: true, savedChat: true });
  data.setWriting([{ id: 'late-owner-writing', conversation_id: 'saved-personal-chat', text: 'Late synthetic private writing.', direction: 'outbound', author_kind: 'unknown_owner_outgoing' }]);
  let release!: () => void; data.holdWriting(new Promise<void>(resolve => { release = resolve; }));
  await page.goto('/connections'); await panel(page).getByRole('combobox', { name: 'Individual WhatsApp conversation', exact: true }).selectOption('synthetic-peer');
  await page.getByRole('button', { name: 'Read latest writing examples', exact: true }).click(); await expect.poll(() => data.writingCount()).toBe(1);
  data.switchOwner(); const updated = page.waitForResponse(response => response.url().includes('/api/ui/bootstrap'));
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange'))); await updated;
  await expect(panel(page)).toContainText('Phone linking is not available in this deployment yet.'); release();
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(page.getByText('Late synthetic private writing.', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Confirm selected messages are my writing', exact: true })).toHaveCount(0); expect(data.changes).toEqual([]);
});
