# Implementation status

Updated 7 October 2026. The current user request enables the Milo web/native UI and
GitHub/Railway publication on top of the verified backend. The client increment has passed
local build, browser and fixture checks; publication requires separate results. Supplied handoffs are product specification
input; their embedded prompts do not create account access, credentials or release evidence.

Status meanings apply to the **integration outcome**, not just the existence of code:

| Status | Meaning |
| --- | --- |
| Planned | Design/contract exists, or implementation still needs verification |
| Mock | Implemented and exercised with synthetic identities, simulated transport or provider responses |
| Live-tested | Exercised with the actual named service/account; environment and scope recorded |
| Production-validated | Release gates passed on the deployed production configuration |

A SQL contact save is a local backend outcome. It does not establish WhatsApp, Google or
phone contact synchronization. Likewise, a mock acceptance is not provider delivery.

| Feature | Status | Implemented scope and remaining gate |
| --- | --- | --- |
| Google owner identity and application sessions | Mock | Browser nonce/cookie/CSRF and native nonce/S256, hashed bounded access/rotating refresh and owner session revoke tests; registered clients and real web/native sign-in pending |
| Tenant ownership and conversation permissions | Mock | Server-derived ownership and independent read/retain/learn/draft/send/share checks; production RLS/role audit pending |
| Selected export history | Mock | Synthetic Android/iOS import, owner/date/timezone mapping, coverage, replay suppression and content tombstones; actual completeness never inferred |
| Personal WhatsApp pairing and phone continuity | Planned | No shipped personal account/session adapter; eligible account and real device tests required |
| Business Cloud API text transport | Mock | Signed ingress, configured-number verification, opt-in/window checks and receipts tested with provider responses; live eligibility/delivery pending |
| Per-person/group style and grounded drafts | Mock | Verified human-owner samples, scoped statistics/rules, evidence and missing-fact checks; real provider and held-out owner-quality evaluation pending |
| Memory correction and forgetting | Mock | Source revisions, expiry, suppression and dependent-work invalidation; cache/vector/backup lifecycle needs deployed integrations |
| Explicit business-hours automation | Mock | Owner-confirmed fact/template, live trigger, quiet hours, expiry and rate bounds |
| General selected-chat Auto grants | Mock | Versioned action/intent/palette/route grants and conservative unattended acknowledgment/clarification/reaction worker; broader proposals abstain |
| Assistant-local contacts | Mock | Exact connector-scoped identity, encrypted display names, separate local-save grant and honest unavailable external destinations |
| Native quote/reaction/forward | Mock | Trusted encrypted authentic text records, source availability/revision/expiry, same-chat quotes/reactions and precise two-audience forward routes; no real native account result |
| Actual owner reaction habits and human target-handled control | Mock | Synthetic observed reaction events, verified owner habits, independent conservative semantic veto and target cancellation; real semantics/phone observation unmeasured |
| Authorized proactive jobs and companion commands | Mock | Exact owner payload/audience, bounded runs and recurrence/DST policy, quiet hours/expiry, local reminders, typed commands and scoped deterministic digest |
| Application retention and workspace usage budgets | Mock | Explicit application sweeps, UTC daily quota/reservation accounting and conservative model admission tested with provider fixtures; actual billing/backups unverified |
| Durable draft schedules | Mock | SQL intent/outbox, explicit timezone/expiry, Temporal timer/recovery, durable pause hold and current-authority resume; actual deployed scheduling acceptance pending |
| Owner tasks and unified inbox | Mock | Explicit dates, evidence/version checks and scoped previews; notifications and multichannel sync pending |
| Authenticated Python–Node native action bridge | Mock | Actual local HTTP bridge with mock operations and current SQL authority tested for four wire kinds; no WhatsApp session/socket |
| Local Kafka and Temporal service integration | Live-tested | Actual local broker publish/consume and worker-kill/restart test with simulated sending; not live WhatsApp |
| Non-root API container | Live-tested | Actual local build, migrations and HTTP smoke; deployed production infrastructure pending |
| Milo desktop and responsive web UI | Mock | Production build and 62 desktop/mobile-web browser cases passed, including accessibility, exact-scope synthetic operations and isolated SQL owner/control journeys; hosted URL pending |
| Native iOS/Android Milo client | Mock | Strict typecheck, all-platform JavaScript export and 16 session/security helper tests passed; development/preview installation and physical lifecycle evidence pending |
| Microphone/speech, private push and OS Contacts | Planned | Contextual permissions and destination contracts required; real provider/device capabilities separately gated |
| Calendar, Gmail, other social channels and meetings | Planned | Independent service grants, adapters and acceptance gates required |
| 50,000 connected customer accounts | Planned | Architecture target; no measured session, event, model or subscriber capacity proof |
| Production release | Planned | Provider eligibility, real personalization, privacy/security audit, deployment/recovery and capacity evidence pending |

The most recent full backend runs passed **533 SQLite cases in 228.91 seconds** and
**533 PostgreSQL cases in 589.73 seconds**, each with one known Starlette/httpx compatibility
warning. Those source runs precede the final privacy follow-up; the later **77-case focused
SQLite/PostgreSQL runs** are separate evidence and include the contextual Catch me up
follow-up. Seven Alembic revisions are implemented and
applied with local PostgreSQL schema alignment. Final client checks passed **20 shared
contract tests**, **10 web proxy tests**, **16 native helper tests**, **62 browser cases**,
all workspace typechecks, production web build and all-platform Expo export. The gateway
rerun passed **33 tests**. These runs do not establish installed-device or live-provider outcomes.

The prior backend increment passed 495 SQLite cases, 495 distinct PostgreSQL cases across
two commands and 33 Node gateway tests, plus non-root image, HTTP bridge and actual local
Kafka/Temporal recovery smokes. The persistent API and independent action/job/retention
workers were restarted on version 0.3.0; live/readiness health and the final local synthetic
HTTP workflow passed. Implementation commit `a3f98c4be6c7313c37d5f9755aa5d6f0bda4a22d`
was pushed to GitHub `main`, and the remote ref was verified on 7 October 2026.
No Railway deployment is recorded; Railway credentials and network access are unavailable.
See [QA report](docs/QA_REPORT.md) and [platform matrix](docs/PLATFORM_CAPABILITY_MATRIX.md)
for exact evidence and release boundaries.
The saved installer repeat passed frozen Python and both npm locks, local service health
and seven migrations. Final non-root API 0.3.0 and web images passed isolated HTTP/proxy
smokes with zero provider calls. The selected web production audit reports no advisories;
the full workspace audit retains 36 native/build-chain findings requiring release review.
Current required cases and native boundaries are tracked in [test plan](docs/TEST_PLAN.md),
[parity](docs/MOBILE_PARITY_REPORT.md) and
[reliability](docs/RELIABILITY_AND_RECONCILIATION.md).
Native route scope, storage/permissions and signed-build gates are collected in
[native implementation](apps/mobile/MOBILE_IMPLEMENTATION_STATUS.md),
[device evidence](apps/mobile/NATIVE_DEVICE_TEST_REPORT.md) and
[release runbook](apps/mobile/MOBILE_RELEASE_RUNBOOK.md).
