# Milo product and implementation scope

Source material: `WhatsApp_AI_Assistant_Build_Guide.md`, its matching Version 2 PDF, and
`AI_Assistant_CTO_Handoff.docx`/PDF, version 1.0 dated 6 October 2026, and the new Milo
master specification plus desktop/mobile UI PDF supplied by the user.
The handoff adds selected-chat Auto mode, six communication capabilities, authentic native
sources, reaction handling, sharing routes, trusted jobs and companion APIs. These documents
describe a relationship-aware assistant with WhatsApp first,
Google identity, phone coexistence, per-conversation personalization, correctable memory,
human takeover, scheduling, and a 50,000-account design target. Embedded document handoff
instructions are design input. The user's current request authorizes the full Milo UI,
backend integration/testing and GitHub/Railway publication; external credentials and
installed-device/provider validation must still come from the actual environment.

The product loop is connect supported accounts, select authorized conversations, ingest
available history, inspect learned style/context, enable selected actions/routes once,
execute eligible work and inspect receipts/digests. Owner-invoked drafting remains available
with review and exact-content approval. Reading, storing, learning, drafting, sending, and sharing
have independent grants. One owner can hold workspaces; each connector has a single recorded
workspace owner. Account transfer and shared operator inboxes are deferred.

| Guide requirement | Backend interpretation | Follow-up gate |
| --- | --- | --- |
| Phone remains usable | No silent account migration; export-first fallback and verified capability boundary | Live personal linked-device or eligible Business coexistence acceptance |
| Learn during first onboarding | Import available authorized export and produce a progressive local style preview | Live history coverage and large-history prioritization/jobs |
| Different writing per person/group | Owner-only human samples, scoped style profile/rules, scoped draft context | Representative owner-quality evaluation and feedback loop |
| Evidence-backed private memory | Conversation-scoped candidate/confirmed facts with message references, versions, forgetting and explicit application retention | Additional memory types, universal/provider/backup lifecycle, restore guarantees and embeddings if justified |
| Human takeover | Epoch/revision invalidation, pause generation, explicit takeover/resume, owner-echo handling | Real paired-device echo and concurrent phone/provider testing |
| Draft safety | Strict structured proposal, scoped evidence, missing facts, owner edit and exact-content approval | Live prompt-injection and quality evaluation with supported providers |
| Reliable ingestion | Durable deduplication, origin distinction, edits/deletions, transactional references | Provider outage/redelivery and history-sync coverage tests |
| Safe sends | Capability, opt-in/window, approval and current-state checks; unique attempt ledger and receipts | Real provider ambiguity/reconciliation, templates and production policy |
| Scheduling | Explicit offset-aware times, IANA timezone, expiry, durable SQL/Temporal intent, pause holds and bounded daily/weekly authorized jobs with explicit DST policy | Natural-language date resolution/UX and actual production scheduling/recovery |
| Follow-ups and unified inbox | Owner-authored evidence-backed tasks with version checks and permitted inbox previews/counts | Automated commitment extraction, delivered reminder notifications and actual multi-channel adapters |
| Groups | Default read-only and explicit permission boundary | Actual supported transport rules and group-specific automation acceptance |
| 50,000 customers | Modular API/adapter/storage/event/workflow boundaries and small measured synthetic workload | Measured session memory, tenant cells/sharding, fairness, provider budgets and burst recovery |
| One place for future channels | Provider-neutral connector and canonical event records with one shared client authority | Calendar, Gmail, social adapters and explicit identity linking |

The first shipped data structures are users/sessions/nonces, workspaces/connectors,
conversations/permissions, messages/events/imports, style profiles, scoped memories and
suppressions, drafts/attempts, owner-authored tasks, scheduled intents/outbox, automation policy records, and
audit metadata. The guide's complete set of proposed memory stores and operational services
is not yet implemented as separate storage layers.

The handoff's six outcomes are assistant-local contact saving, ordinary message sending,
personalized replies, native quoted replies, native emoji reactions and native forwarding.
Each outcome has its own capability and receipt; contact cards do not save contacts and
text copies do not count as native forwarding. WhatsApp/Google/OS contact writes remain
independent provider destinations.

General Auto work must be bounded by server-stored chat/action/intent grants, source facts,
reaction palette, limits, expiry, quiet hours and precise forwarding routes. A model can
propose only scoped server-issued references. Native targets require authentic trusted
provider objects, current revisions, availability and expiry checks. Text imports cannot
recreate those originals. A human reaction handles its target; assistant echoes are
attributed separately. Global pause blocks external actions and preserves durable jobs;
resume must recheck authority and freshness rather than revive canceled work.

Implemented backend scope and mock verification of the handoff additions are tracked in
[status](../IMPLEMENTATION_STATUS.md) and [QA](QA_REPORT.md). Milo companion Home, scoped
assistant controls and the web/native operational tools are now enabled implementation
requirements. Calendar/Gmail/social/meeting
connectors remain independent authorization and implementation milestones.

Acceptance for this increment requires tenant isolation, explicit mapping/import coverage,
history/replay exclusion from live actions, owner-only style samples, correctable memory,
draft review and stale-approval rejection, current-state dispatch, conservative uncertain
outcomes, durable scheduling references, deletion suppression, and executable development
tests. External identity, real accounts/models, provider policy, production scale, and
frontend acceptance are reported independently.

The selected Milo interface supersedes the earlier generic-dashboard direction. Desktop
uses Home, Inbox, Actions, Memory and Rules with Connections/Activity/Settings utilities;
native uses Home, Inbox, Actions, Memory and More. All M01–M34 routes/details and recovery
states are required; M35–M36 remain visibly Planned. The warm/lilac Milo design, source
evidence, exact recipient composer, separate contextual Ask Milo and committed-control
truth are shared across clients. Installed Expo/React Native builds are distinct from
mobile web. See [screen/parity inventory](MOBILE_PARITY_REPORT.md) and
[mandatory test plan](TEST_PLAN.md).

The API publishes `/v1` aliases alongside existing compatibility routes. New backend
families include `/v1/automation/grants`, `/v1/forward-routes`, `/v1/actions`, `/v1/jobs`,
`/v1/contacts`, `/v1/assistant/commands`, `/v1/assistant/digest`, workspace budget/usage,
and `/v1/privacy/retention`. Internal event and dispatch-authority endpoints require
service authentication and are not model tools or public socket APIs.

The unattended planner deliberately uses conservative static acknowledgments/clarifications
and suitable reactions with verified owner habits. It cannot autonomously invent dates,
prices, commitments or freely generated facts. Group automation requires explicit group
send authority and a trusted owner-addressed trigger. Real voice quality and broader
semantic/language support need [model evaluation](MODEL_EVALUATION.md) before expansion.
