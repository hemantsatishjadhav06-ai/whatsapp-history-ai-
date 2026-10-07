import type { MiloState } from './types';

export type PrivateToolResult<T> = { value: T; authorizationVersion: string };

/** Poll timestamps do not grant authority. Content versions belong to one owner and workspace. */
export function toolAuthorizationVersion(state: Pick<MiloState, 'mode' | 'user' | 'workspaceId' | 'data' | 'authorizationVersion'>): string {
  return JSON.stringify([state.mode, state.user.id, state.workspaceId,
    state.authorizationVersion || state.data]);
}

/** Used during render as well as request completion, before a stale body can be displayed. */
export function currentToolResult<T>(entry: PrivateToolResult<T> | null, authorizationVersion: string): T | null {
  return entry?.authorizationVersion === authorizationVersion ? entry.value : null;
}

/** A response cannot acquire the authority of a newer snapshot while it was in flight. */
export async function authorizedToolRequest<T>(request: Promise<T>, before: string, current: () => string): Promise<PrivateToolResult<T> | null> {
  try {
    const value = await request;
    return before === current() ? { value, authorizationVersion: before } : null;
  } catch (failure) {
    if (before !== current()) return null;
    throw failure;
  }
}
