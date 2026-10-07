export type AutomaticDraftChoice = {
  conversation_id: string; enabled: boolean; version: number; expires_at: string | null;
  max_drafts_per_hour: number; status: 'disabled' | 'ready' | 'blocked'; reason_code: string | null;
  latest_job: null | { id: string; status: string; reason_code: string | null; draft_id: string | null; expires_at: string | null };
};

export function automaticDraftState(choice: AutomaticDraftChoice, now = Date.now()): { status: AutomaticDraftChoice['status']; reason: string | null } {
  if (!choice.enabled) return { status: 'disabled', reason: null };
  if (!choice.expires_at || !Number.isFinite(Date.parse(choice.expires_at)) || Date.parse(choice.expires_at) <= now) return { status: 'blocked', reason: 'GRANT_EXPIRED' };
  return { status: choice.status, reason: choice.reason_code };
}

/** State the server's hold in language that distinguishes consent from availability. */
export function automaticDraftReason(reason: string | null): string {
  const reasons: Record<string, string> = {
    MODEL_DISABLED: 'The writing model is disabled in this deployment.',
    MODEL_NOT_CONFIGURED: 'The writing model is not configured in this deployment.',
    MODEL_PRICING_UNVERIFIED: 'The model cost limit cannot be verified yet.',
    PERMISSION_REVOKED: 'Current reading, retention or draft permission is missing.',
    GLOBAL_PAUSE: 'Your workspace is paused.', CONTROL_HELD: 'This conversation is held for your control.',
    CONNECTOR_UNAVAILABLE: 'The connection is unavailable.', GRANT_EXPIRED: 'Your automatic draft choice has expired.',
    BUDGET_EXCEEDED: 'Your workspace budget has been reached.',
  };
  return reason ? reasons[reason] ?? `Preparation is held: ${reason.toLowerCase().replaceAll('_', ' ')}.` : 'Preparation is waiting for current checks.';
}
