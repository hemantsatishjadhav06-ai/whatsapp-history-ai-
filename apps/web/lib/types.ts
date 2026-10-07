import type { MiloSnapshot } from '@milo/contracts';

export type Section = 'home' | 'inbox' | 'actions' | 'memory' | 'rules' | 'connections' | 'activity' | 'settings' | 'more' | 'assistant';
export type DataRecord = { id: string; [key: string]: unknown };
export type Conversation = DataRecord & {
  conversation_id: string; title: string; kind: string; control_state: string; connector_id: string;
  account_label: string; provider_chat_id: string; revision: number; preview: string; timestamp: string;
  unread: number; avatar_color: string; pending_drafts: number; pending_tasks: number;
};
export type Connection = DataRecord & {
  provider: string; account_id: string; status: string; capabilities: Record<string, unknown>; label: string;
};
export type Message = DataRecord & {
  text: string; direction: string; author_kind: string; provider_timestamp: string; revision: number;
  conversation_id: string; reply_to?: string | null;
};
export type MiloData = {
  conversations: Conversation[]; connections: Connection[]; actions: DataRecord[]; jobs: DataRecord[];
  memories: DataRecord[]; styles: DataRecord[]; grants: DataRecord[]; routes: DataRecord[];
  contacts: DataRecord[]; budget: DataRecord | null; activity: DataRecord[]; tasks: DataRecord[]; drafts: DataRecord[];
};
export type MiloState = {
  mode: 'demo' | 'live'; workspaceId: string; selectedConversationId: string | null;
  loading: boolean; error: string | null; paused: boolean; pausePending: boolean; online: boolean;
  user: { id: string; display_name: string; email: string }; data: MiloData; lastUpdated: string; timezone: string;
  authorizationVersion?: string;
  nextCursor: string | null; hasMore: boolean;
};
export type MiloActions = {
  request<T = unknown>(method: string, path: string, body?: unknown): Promise<T>;
  refresh(): Promise<void>; confirmOwnerAnswer(value: unknown, stillSelected: () => boolean): Promise<MiloSnapshot>;
  clearOwnerAnswerScope(): void; navigate(path: string): void; selectConversation(id: string): void;
  logout(): Promise<void>; setPaused(paused: boolean): Promise<void>;
  loadMore(): Promise<void>;
  ensureConversation(id: string): Promise<void>;
};
export type ToolsProps = { section: Exclude<Section, 'home' | 'inbox' | 'more' | 'assistant'>; state: MiloState; actions: MiloActions };
