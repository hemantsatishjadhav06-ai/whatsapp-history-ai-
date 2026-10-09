export type RecordEntity = {id: string; [key: string]: unknown};
export type ApplicationActionKind = 'add_contact' | 'send' | 'reply' | 'quote' | 'reaction' | 'forward';
export type ConversationMode = 'disabled' | 'read-only' | 'draft' | 'Auto';
export type AssistantState = 'Idle' | 'Listening' | 'Processing' | 'Needs information' | 'Draft/proposal' | 'Done/receipt' | 'Paused' | 'Unavailable';
export type Conversation = RecordEntity & {conversation_id?: string; title: string; kind: string; connector_id: string;
  provider_chat_id: string; control_state: string; revision: number; mode?: ConversationMode; preview?: string;
  account_label?: string; timestamp?: string; unread?: number; pending_drafts?: number; pending_tasks?: number; avatar_color?: string};
export type Connection = RecordEntity & {provider: string; account_id: string; status: string; label?: string;
  capabilities: Record<string, unknown>};
export type Message = RecordEntity & {conversation_id: string; text: string; direction: string; author_kind: string;
  provider_timestamp: string; revision: number; origin?: string; reply_to?: string | null};
export type Workspace = RecordEntity & {name: string; timezone: string; paused: boolean; pause_generation: number};
export type MiloSnapshot = {user: {id: string; display_name: string; email: string}; workspaces: Workspace[];
  workspace: Workspace; connections: Connection[]; conversations: Conversation[]; messages: Message[];
  actions: RecordEntity[]; drafts: RecordEntity[]; tasks: RecordEntity[]; jobs: RecordEntity[]; memories: RecordEntity[];
  styles: RecordEntity[]; grants: RecordEntity[]; routes: RecordEntity[]; activity: RecordEntity[]; contacts: RecordEntity[];
  budget: RecordEntity | null; retention: RecordEntity | null; generated_at: string; simulation: boolean;
  pagination?: {conversation_next_cursor: string|null;has_more_conversations:boolean;resource_limit:number;scope:string}};
export function actionLabel(kind: string): string {
  return ({add_contact: 'Save contact', send: 'Send message', reply: 'Personalized reply', quote: 'Quoted reply',
    reaction: 'Emoji reaction', forward: 'Native forward', send_text: 'Send message', quoted_reply: 'Quoted reply',
    emoji_reaction: 'Emoji reaction', native_forward: 'Native forward',SEND_TEXT:'Text message · legacy protocol',
    QUOTE:'Quoted reply',REACTION:'Emoji reaction',FORWARD:'Native forward',REMINDER:'Owner reminder'} as Record<string, string>)[kind] ?? kind.replaceAll('_', ' ');
}
export function stateLabel(state: string): string {
  return ({AUTO_ENABLED: 'Auto', DRAFT_MODE: 'Draft', READ_ONLY: 'Read only', AI_OFF: 'Disabled',
    HUMAN_TAKEOVER: 'Phone takeover', RECONNECT_REVIEW: 'Reconnect review', ready: 'Ready', prepared: 'Prepared',
    submitting: 'Submitting', accepted: 'Provider accepted', delivered: 'Delivered', read: 'Read', uncertain: 'Submission uncertain',
    needs_approval: 'Owner review', blocked: 'Held', held: 'Held', cancelled: 'Canceled', canceled: 'Canceled', expired: 'Expired'} as Record<string, string>)[state] ?? state.replaceAll('_', ' ');
}
export function modeFor(conversation: Conversation, grants: RecordEntity[] = []): ConversationMode {
  if(grants.some(grant=>grant.conversation_id===conversation.id&&grant.enabled&&grant.mode==='AUTO'))return 'Auto';
  return conversation.mode ?? ({AUTO_ENABLED: 'Auto', READ_ONLY: 'read-only', AI_OFF: 'disabled'} as Record<string, ConversationMode>)[conversation.control_state] ?? 'draft';
}
// Server-computed, deterministic review warnings on outward draft text. They never
// change approval semantics: the owner still approves the exact displayed text.
export type DraftRiskFlag = 'link' | 'payment' | 'phone_number' | 'commitment';
export const draftRiskLabels: Record<DraftRiskFlag, string> = {link: 'contains a link', payment: 'mentions a payment or bank details',
  phone_number: 'contains a phone number', commitment: 'makes a promise or confirmation'};
export function draftRiskFlags(draft: RecordEntity | null | undefined): string[] {
  const flags: unknown = draft?.risk_flags;
  return Array.isArray(flags) ? [...new Set(flags.filter((flag): flag is string => typeof flag === 'string' && flag.length > 0))] : [];
}
export function draftRiskWarning(draft: RecordEntity | null | undefined): string | null {
  const flags = draftRiskFlags(draft);
  return flags.length ? `Check before sending: ${flags.map(flag => draftRiskLabels[flag as DraftRiskFlag] ?? `mentions ${flag.replaceAll('_', ' ')}`).join(' · ')}` : null;
}
export function canCancel(status: string): boolean { return ['ready', 'prepared', 'scheduled', 'held', 'needs_approval', 'approved'].includes(status); }
export function resolveObject(snapshot: MiloSnapshot, kind: string, id: string): RecordEntity | undefined {
  const groups: Record<string, RecordEntity[]> = {conversation: snapshot.conversations, action: snapshot.actions,
    memory: snapshot.memories, connection: snapshot.connections, job: snapshot.jobs, task: snapshot.tasks};
  return groups[kind]?.find((row) => row.id === id);
}
export function reconcileSnapshot(previous: MiloSnapshot | null, next: MiloSnapshot): MiloSnapshot {
  // A newly authorized snapshot replaces private cache in full. Removed/revoked
  // objects cannot survive a convenient client-side merge.
  if (!previous || previous.workspace.id !== next.workspace.id) return next;
  if (next.workspace.pause_generation < previous.workspace.pause_generation) {
    return {...next, workspace: {...next.workspace, paused: previous.workspace.paused,
      pause_generation: previous.workspace.pause_generation}};
  }
  return next;
}
