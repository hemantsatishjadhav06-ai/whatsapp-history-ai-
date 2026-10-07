# Capability and integration status

Capabilities are explicit `supported`, `unsupported`, `unknown`, or `unavailable` observations, not a
promise that every WhatsApp account behaves alike. An unverified production account does
not become send-capable merely because a user creates a connector record.

| Connector / integration | Available in this repository | Remaining external validation |
| --- | --- | --- |
| Owner-provided WhatsApp export | Parse/import authorized text; mapping, coverage, replay suppression | Completeness depends on the actual export; media and unavailable messages are not reconstructed |
| Mock connector | Synthetic receive/edit/delete, authentic-record fixtures, text/quote/reaction/forward policy and local contacts in development/tests | Provides no account access, phone continuity, pairing, external contact synchronization, or production capacity evidence |
| WhatsApp Business Cloud API | Signed webhook handler, configured number verification, contact text send adapter and receipts | Meta credentials, eligible account, webhook routing, recipient opt-in, provider quotas, delivery and coexistence acceptance |
| Personal linked-device WhatsApp | Provider-neutral contract only | No unofficial pairing/session adapter shipped; policy, reliability, owner echo, and phone coexistence must be evaluated before implementation |
| Native third-party WhatsApp agents | No transport integration | Do not infer access to a user's other chats from an assistant command channel |
| Google sign-in | Server token/claim verification, one-use nonce, secure revocable sessions; new client flow in progress | Registered Google client and real web/native identity exchange |
| Model provider | Structured proposal boundary and bounded scoped context; disabled/mock/configured modes | API credential, provider data terms, live output quality and task-specific evaluation |
| Temporal | Durable ID-only timer worker plus idempotent SQL-outbox registrar | Running service/namespace, worker recovery and restart verification for the deployed stack |
| Kafka | Metadata-only transactional outbox relay | Running broker, consumers, acknowledgement/redelivery and outage/recovery verification |
| Calendar/Gmail/other social channels | Common ownership/permissions boundary is reusable | OAuth scopes, provider adapters, normalized inbox and action-specific grants are not implemented |

Groups default to read-only, and all conversation grants start disabled. Business Cloud
sending remains restricted to supported contact text messages, with opt-in and the customer
service window checked. Template delivery and universal group sending are not included.

Owner-authored follow-up tasks and an authorized inbox summary endpoint are implemented.
Tasks accept explicit times and evidence references, require versioned edits, and mark
edited/deleted source evidence for review. They do not extract commitments or notify the owner.

The existing business-hours worker answers exact English questions using an owner-confirmed
fact, a static template, explicit expiry/quiet hours and an hourly budget. The CTO handoff
adds general selected-chat grants and a bounded fresh-event planner for safe acknowledgments,
clarifications and suitable reactions with verified habits. Native quote/forward proposals
use authentic originals and exact routes; they do not reconstruct native sources from text
imports. This general ledger is mock-only, with final acceptance results tracked in
[QA](QA_REPORT.md). It does not execute unconstrained generated factual claims.

Owner-invoked drafting retains edit/approval controls. Typed companion commands, scoped
digests, exact local contacts, trusted proactive/recurring jobs and application retention
controls are backend features; they do not establish frontend usability or real provider
operations. Real personal automation/personalization, natural-language date resolution,
calendar availability, meeting booking, delivered reminder notifications, voice/media
transcription and attachments require further implementation/validation. Supported local
reminders produce a private record/receipt, not an email/phone notification.

The dated account/adapter evidence matrix is maintained in
[PLATFORM_CAPABILITY_MATRIX.md](PLATFORM_CAPABILITY_MATRIX.md). Current interface and status
details are in [IMPLEMENTATION_STATUS.md](../IMPLEMENTATION_STATUS.md).
