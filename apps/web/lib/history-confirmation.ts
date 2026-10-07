import type { MiloState } from './types';

type OwnerScope = Pick<MiloState, 'mode' | 'user' | 'workspaceId'>;
const message = 'Selected history imported. No outgoing action was created.';
// Client navigation remounts the page. Keep only this generic, bounded confirmation.
let confirmation: { scope: string; expiresAt: number } | null = null;
const scope = (state: OwnerScope) => JSON.stringify([state.user.id, state.workspaceId]);

export function rememberHistoryImport(state: OwnerScope): void {
  if (state.mode === 'live' && state.workspaceId) confirmation = { scope: scope(state), expiresAt: Date.now() + 20_000 };
}

export function currentHistoryImportNotice(state: OwnerScope): string | null {
  if (confirmation && confirmation.expiresAt <= Date.now()) confirmation = null;
  return state.mode === 'live' && confirmation?.scope === scope(state) ? message : null;
}

export function dismissHistoryImportNotice(state: OwnerScope): void {
  if (confirmation?.scope === scope(state)) confirmation = null;
}
