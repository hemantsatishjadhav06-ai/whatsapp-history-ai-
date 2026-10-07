/** The personal linked-device pilot is separate from the Business integration. */
export type PersonalConfig = {
  enabled: boolean; configured: boolean; status: 'disabled' | 'configured' | 'unavailable';
  simulation: boolean; google_verified_required: boolean; capabilities: Record<string, unknown>; live_verified: boolean;
};
export type PersonalStatus = {
  status: string; enabled: boolean; configured: boolean; connected: boolean; simulation: boolean;
  connector: null | { id: string; workspace_id: string; provider: string; account_id: string; status: string;
    capabilities: Record<string, unknown>; fence: number; lease_expires_at?: string | null };
};
export type PersonalPairing = {
  connector_id: string; state: string; qr: null | { value: string; expires_at: string }; poll_after_seconds: number;
};
export type PersonalConsent = { read: boolean; retain: boolean; learn: boolean; draft: boolean; send: boolean; recipient_opted_in: boolean };
export type PersonalChat = {
  provider_chat_id: string; title: string; kind: string; conversation_id: string | null;
  permissions?: Partial<PersonalConsent> & { share?: boolean; version?: number; expires_at?: string | null };
};
export type PersonalChats = { connector_id: string; chats: PersonalChat[]; has_more: boolean; limit: number; next_cursor?: string | null };

export function emptyPersonalConsent(): PersonalConsent {
  return { read: false, retain: false, learn: false, draft: false, send: false, recipient_opted_in: false };
}
export function personalConsentFor(chat: PersonalChat): PersonalConsent {
  const empty = emptyPersonalConsent();
  for (const key of Object.keys(empty) as (keyof PersonalConsent)[]) empty[key] = chat.permissions?.[key] === true;
  return empty;
}
export function validPersonalConsent(consent: PersonalConsent): boolean {
  return (!(consent.learn || consent.draft || consent.send) || (consent.read && consent.retain))
    && (!consent.send || consent.recipient_opted_in);
}

/** QR secrets are displayed only for an active pairing state and a short lease. */
export function usablePersonalQR(pairing: PersonalPairing | null, connectorId: string | undefined, now = Date.now()): { value: string; expiresAt: number } | null {
  if (!pairing || pairing.connector_id !== connectorId || pairing.state !== 'pairing' || !pairing.qr) return null;
  const expiresAt = Date.parse(pairing.qr.expires_at);
  const value = pairing.qr.value;
  if (!Number.isFinite(expiresAt) || expiresAt <= now || expiresAt > now + 60_000
    || typeof value !== 'string' || !value.length || value.length > 4096 || /[\x00-\x20\x7f]/.test(value)) return null;
  return { value, expiresAt };
}

/** Draw only a standard QR matrix, with a four-module quiet zone. */
export function personalQRDrawing(matrix: { getModuleCount(): number; isDark(row: number, column: number): boolean }): { path: string; size: number } | null {
  const size = matrix.getModuleCount();
  if (!Number.isInteger(size) || size < 21 || size > 177 || (size - 21) % 4 !== 0) return null;
  let path = '';
  for (let row = 0; row < size; row++) for (let column = 0; column < size; column++)
    if (matrix.isDark(row, column)) path += `M${column + 4} ${row + 4}h1v1h-1z`;
  return { path, size: size + 8 };
}

/** The owner reviews only uncertain phone writing from the bounded, exact-chat batch. */
export function personalWritingExamples(value: unknown, conversationId: string): { id: string; text: string }[] {
  if (!Array.isArray(value)) throw new Error('Writing examples must be a current message batch');
  const ids = new Set<string>();
  return value.slice(0, 30).flatMap(row => {
    if (!row || typeof row !== 'object' || row.conversation_id !== conversationId || row.direction !== 'outbound'
      || row.author_kind !== 'unknown_owner_outgoing' || typeof row.id !== 'string' || !row.id.length || row.id.length > 80
      || ids.has(row.id) || typeof row.text !== 'string' || !row.text.trim() || row.text.length > 20_000) return [];
    ids.add(row.id); return [{ id: row.id, text: row.text }];
  });
}
