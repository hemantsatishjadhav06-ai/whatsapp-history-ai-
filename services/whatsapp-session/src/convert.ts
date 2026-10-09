/** Content-only conversion of SDK messages into the bounded private sync shape. No downloads or link fetches. */
import { normalizeMessageContent, proto } from "@whiskeysockets/baileys";
import type { WAMessage } from "@whiskeysockets/baileys";
import { canonicalJid, individualJid } from "./protocol.ts";
import type { SyncMessage } from "./protocol.ts";

const MAX_TEXT = 20_000;
type Content = proto.IMessage;
type Converted = Pick<SyncMessage, "kind" | "text">;

function seconds(value: unknown): number {
  if (typeof value === "number") return value;
  if (typeof value === "bigint") return Number(value);
  if (value && typeof value === "object" && "toNumber" in value && typeof value.toNumber === "function") return Number(value.toNumber());
  if (typeof value === "string" && /^\d+$/.test(value)) return Number(value);
  return Number.NaN;
}
function clip(text: string): string {
  const value = text.replace(/\u0000/g, "").trim();
  return value.length > MAX_TEXT ? `${value.slice(0, MAX_TEXT - 1)}…` : value;
}
function withCaption(label: string, caption: string | null | undefined): string {
  return caption?.trim() ? `${label} ${caption.trim()}` : label;
}
function duration(value: number | null | undefined): string {
  if (!value || value < 0) return "";
  return ` (${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, "0")})`;
}
/** The visible text of one normalized message body, or null when it carries nothing to show. */
export function describe(content: Content | null | undefined, viewOnce = false): Converted | null {
  if (!content) return null;
  const once = viewOnce ? "View once " : "";
  if (content.conversation) return { kind: "text", text: content.conversation };
  if (content.extendedTextMessage?.text) return { kind: "text", text: content.extendedTextMessage.text };
  if (content.imageMessage) return { kind: "media", text: withCaption(`📷 ${once}Photo`, viewOnce ? null : content.imageMessage.caption) };
  if (content.videoMessage) {
    const label = content.videoMessage.gifPlayback ? "GIF" : `🎥 ${once}Video${duration(content.videoMessage.seconds)}`;
    return { kind: "media", text: withCaption(label, viewOnce ? null : content.videoMessage.caption) };
  }
  if (content.ptvMessage) return { kind: "media", text: `🎥 Video message${duration(content.ptvMessage.seconds)}` };
  if (content.audioMessage) {
    return { kind: "media", text: content.audioMessage.ptt ? `🎤 Voice message${duration(content.audioMessage.seconds)}`
      : `🎵 Audio${duration(content.audioMessage.seconds)}` };
  }
  if (content.documentMessage) {
    return { kind: "media", text: withCaption(`📄 ${content.documentMessage.fileName || content.documentMessage.title || "Document"}`,
      content.documentMessage.caption) };
  }
  if (content.stickerMessage) return { kind: "media", text: "Sticker" };
  if (content.locationMessage) {
    const place = [content.locationMessage.name, content.locationMessage.address].filter(Boolean).join(", ");
    return { kind: "location", text: `📍 ${place || "Location"}` };
  }
  if (content.liveLocationMessage) return { kind: "location", text: withCaption("📍 Live location", content.liveLocationMessage.caption) };
  if (content.contactMessage) return { kind: "contact", text: `👤 ${content.contactMessage.displayName || "Contact card"}` };
  if (content.contactsArrayMessage) {
    const count = content.contactsArrayMessage.contacts?.length ?? 0;
    return { kind: "contact", text: `👤 ${content.contactsArrayMessage.displayName || `${count} contacts`}` };
  }
  const poll = content.pollCreationMessage ?? content.pollCreationMessageV2 ?? content.pollCreationMessageV3;
  if (poll) {
    const options = (poll.options ?? []).map(option => option.optionName).filter(Boolean).join(" / ");
    return { kind: "poll", text: `📊 ${poll.name || "Poll"}${options ? ` — ${options}` : ""}` };
  }
  if (content.eventMessage) return { kind: "event", text: `📅 ${content.eventMessage.name || "Event"}` };
  if (content.buttonsResponseMessage?.selectedDisplayText) return { kind: "text", text: content.buttonsResponseMessage.selectedDisplayText };
  if (content.listResponseMessage?.title) return { kind: "text", text: content.listResponseMessage.title };
  if (content.templateButtonReplyMessage?.selectedDisplayText) return { kind: "text", text: content.templateButtonReplyMessage.selectedDisplayText };
  if (content.interactiveResponseMessage?.body?.text) return { kind: "text", text: content.interactiveResponseMessage.body.text };
  const template = content.templateMessage?.hydratedTemplate ?? content.templateMessage?.hydratedFourRowTemplate;
  if (template?.hydratedContentText) return { kind: "other", text: template.hydratedContentText };
  if (content.buttonsMessage?.contentText) return { kind: "other", text: content.buttonsMessage.contentText };
  if (content.listMessage?.description || content.listMessage?.title) {
    return { kind: "other", text: content.listMessage.description || content.listMessage.title || "" };
  }
  if (content.interactiveMessage?.body?.text) return { kind: "other", text: content.interactiveMessage.body.text };
  if (content.groupInviteMessage) return { kind: "other", text: `Group invite: ${content.groupInviteMessage.groupName || "WhatsApp group"}` };
  if (content.productMessage) return { kind: "other", text: "🛍️ Product" };
  if (content.orderMessage) return { kind: "other", text: "🛒 Order" };
  if (content.requestPaymentMessage || content.sendPaymentMessage) return { kind: "other", text: "💳 Payment" };
  return null;
}
const CALL_STUBS = new Map<number, string>([[40, "📞 Missed voice call"], [41, "📹 Missed video call"],
  [45, "📞 Missed group voice call"], [46, "📹 Missed group video call"]]);
export type ChatAddress = { jid: string; alt?: string };
/**
 * One sync record for a one-to-one chat message, edit or deletion. `address` maps the SDK chat address
 * (phone number or LID) to the canonical chat so both addresses land in the same conversation.
 */
export function convertMessage(message: WAMessage, address: (jid: string, alt?: string) => ChatAddress,
                               now = Date.now()): SyncMessage | null {
  const key = message.key;
  if (!key?.id || !key.remoteJid) return null;
  const raw = canonicalJid(key.remoteJid);
  if (!individualJid(raw)) return null;
  const altRaw = key.remoteJidAlt ? canonicalJid(key.remoteJidAlt) : undefined;
  const chat = address(raw, altRaw && individualJid(altRaw) ? altRaw : undefined);
  const stamp = seconds(message.messageTimestamp);
  if (!Number.isFinite(stamp) || stamp <= 0 || stamp > now / 1000 + 60) return null;
  const base = { chat_jid: chat.jid, ...(chat.alt ? { chat_alt_jid: chat.alt } : {}), from_me: Boolean(key.fromMe),
    timestamp: new Date(stamp * 1000).toISOString(),
    ...(!key.fromMe && message.pushName ? { push_name: message.pushName.slice(0, 160) } : {}) };
  const wrapper = message.message;
  const viewOnce = Boolean(wrapper?.viewOnceMessage || wrapper?.viewOnceMessageV2 || wrapper?.viewOnceMessageV2Extension);
  const content = normalizeMessageContent(wrapper);
  const protocol = content?.protocolMessage;
  if (protocol) {
    const target = protocol.key?.id;
    if (!target || target.length > 180) return null;
    if (protocol.type === proto.Message.ProtocolMessage.Type.REVOKE) {
      return { ...base, id: target, event: "deleted", kind: "other", text: "", revision: Math.min(Math.floor(stamp), 2_147_483_647) };
    }
    if (protocol.type === proto.Message.ProtocolMessage.Type.MESSAGE_EDIT) {
      const edited = describe(normalizeMessageContent(protocol.editedMessage));
      if (!edited?.text.trim()) return null;
      return { ...base, id: target, event: "edited", kind: edited.kind, text: clip(edited.text),
        revision: Math.min(Math.floor(stamp), 2_147_483_647) };
    }
    return null;
  }
  if (key.id.length > 180) return null;
  const stub = message.messageStubType ? CALL_STUBS.get(message.messageStubType) : undefined;
  const described = stub ? { kind: "call" as const, text: stub } : describe(content, viewOnce);
  if (!described?.text.trim()) return null;
  const context = content ? Object.values(content).find(value =>
    value && typeof value === "object" && "contextInfo" in value) as { contextInfo?: proto.IContextInfo | null } | undefined : undefined;
  const replyTo = context?.contextInfo?.stanzaId;
  return { ...base, id: key.id, event: "created", kind: described.kind, text: clip(described.text),
    ...(replyTo && replyTo.length <= 180 ? { reply_to: replyTo } : {}) };
}
