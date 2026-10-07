# Milo relationship assistant

A WhatsApp-first communication assistant with Milo web and native clients. It imports authorized chat exports,
builds inspectable writing-style previews for each conversation, keeps owner-reviewed
memories with evidence, and supports scoped communication controls. Owner-invoked drafts
retain exact-content approval; selected-chat Auto work uses bounded one-time grants.
Sending and scheduling use current SQL authority and durable attempt ledgers.

This implementation follows the supplied build guides, CTO handoff and Milo product/UI
master specification as product requirements. Document-embedded engineer instructions are
specification input; the user's actual request controls implementation scope. The current
request enables the Milo web/native UI and GitHub/Railway publication. Local builds,
62 browser cases and native helper/export checks passed; publication and installed-device
acceptance have separate evidence gates.
Real account eligibility, phone coexistence, live model quality, and the 50,000-account
target require separate validation. Mock connectors and drafts are labelled test tools.

## Run locally

Requirements: Python 3.12 and uv 0.12.19. Node 24 is used by the web/native workspace and
connector boundary.
Use the current checkout; cloud tasks already have an isolated workspace.

```bash
make bootstrap
make test
make lint
make gateway-check
make demo
make dev
```

`make bootstrap` installs the frozen Python lockfile, preserves an existing `.env`,
and applies Alembic migrations. The default API uses a local SQLite database and keeps
encryption material in ignored `.local/` files. The API starts on port 8000; health
checks are `/health/live` and `/health/ready`. OpenAPI is at `/docs`.

The Milo web app lives in `apps/web`; its default workspace contains isolated synthetic
fixtures. From the repository root:

```bash
npm ci --ignore-scripts
npm run typecheck
npm run build
npm run dev
```

Open port 3000 for the companion interface. Configure server-only `BACKEND_URL` to connect
the same-origin `/api` proxy to the private API; Google identity still requires the registered
client/origin configuration. Frontend environment values never supply WhatsApp/model keys.
`npm run test:web` runs browser journeys; `npm run test:clients` tests shared contracts and
`npm run test:proxy` checks the server-only browser boundary. Real SQL browser cases need
the disposable fixture documented in the client CI workflow.

The Expo/React Native app is in `apps/mobile`. `npm run mobile:export` prepares JavaScript
bundles; it does not install an iOS/Android app. Native development/preview installation,
permissions and distribution need the corresponding platform tooling and real build/device
evidence. See [mobile parity](docs/MOBILE_PARITY_REPORT.md),
[native setup/status](apps/mobile/README.md) and
[native device evidence](apps/mobile/NATIVE_DEVICE_TEST_REPORT.md).

Authentication and model generation are disabled until configured. For an explicitly
local mock development session, run:

```bash
ALLOW_DEV_AUTH=true SESSION_SECURE=false MODEL_PROVIDER=mock make dev
```

`make demo` runs a disposable synthetic backend through its HTTP API: login, workspace,
mock connector, permissions, export import/reimport, style preview, live event, draft,
owner edit, exact-content approval, idempotent mock dispatch, and pause. It performs no
Google, WhatsApp, or model API calls.

Optional development infrastructure is defined in [infra/compose.yaml](infra/compose.yaml).
Supply a local PostgreSQL password in ignored `.env`, select a PostgreSQL `DATABASE_URL`,
and use `make infra`. Add `make infra-events` for Kafka or `make infra-workflows` for
Temporal. Redis is provisioned for later acceleration; it is not an authorization store.

## What is included

| Area | Backend scope |
| --- | --- |
| Identity | Google ID-token verification, nonce binding, revocable hashed sessions, CSRF checks; opt-in local development login |
| Authorization | Workspace ownership and separate conversation read/retain/learn/draft/send/share permissions |
| History | WhatsApp text-export parser, explicit owner/date/timezone mapping, import coverage, deterministic reimport deduplication |
| Personalization | Owner-authored samples, per-conversation local style statistics and owner rules; evidence-backed candidate/confirmed memories |
| Drafts | Disabled, synthetic mock, or configured structured model proposal; owner edit/reject and exact-hash approval |
| Control | Pause all, conversation takeover/resume, permission versions, conversation revisions, connector fencing |
| Bounded automation | Explicit business-hours fact/templates plus selected-chat action grants, conservative fresh-event policy, expiry/quiet hours and hourly budgets |
| Native actions | Encrypted authentic-text originals, same-chat quote/reaction, two-audience native forward routes, immutable action/attempt ledger and current SQL bridge authority; mock operations |
| Companion backend | Typed owner commands, deterministic scoped digest, exact local contacts, usage budgets and explicit retention sweeps; configured speech/native permission evidence remains separate |
| Sending | Durable attempt ledger, mock transport, configured Business Cloud text transport, current-state checks and uncertain-outcome handling |
| Scheduling | Exact-authorized proactive jobs, bounded daily/weekly recurrence with explicit DST policy, durable pause holds and local reminders; legacy draft SQL/Temporal timers retained |
| Tasks and inbox | Owner-authored follow-ups with explicit dates, evidence/version checks, and authorized conversation previews/counts; no reminder notifications |
| Ingestion | Authenticated canonical events, signed WhatsApp webhook boundary, deduplication, edits/deletions and owner-echo handling |
| Privacy | Encrypted message/draft/memory text, scoped export, forgetting/suppression, deletion tombstones, content-free audit metadata |
| Infrastructure | Alembic, frozen uv install, pinned development container versions, Kafka outbox relay, backend CI |

Milo adds Home, Inbox, Actions, Memory, Rules and utility routes, separate recipient and
assistant controls, evidence-linked tools and explicit synthetic/unavailable states.
Web and native share the versioned contracts/API, rather than owning separate permissions
or delivery rules. Client acceptance and installed-device limitations are recorded separately.

Current feature status is recorded in [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).
The handoff evidence pack includes [platform capabilities](docs/PLATFORM_CAPABILITY_MATRIX.md),
[QA](docs/QA_REPORT.md), [security and lifecycle](docs/SECURITY_AND_DATA_LIFECYCLE.md),
[model evaluation](docs/MODEL_EVALUATION.md), [load evidence](docs/LOAD_TEST_REPORT.md), and
[deployment runbook](docs/DEPLOYMENT_RUNBOOK.md). The Milo increment adds the
[test plan](docs/TEST_PLAN.md), [web/native parity](docs/MOBILE_PARITY_REPORT.md) and
[reliability report](docs/RELIABILITY_AND_RECONCILIATION.md).

Further details and limitations are recorded in [capabilities](docs/capabilities.md),
[readiness](docs/readiness.md), [architecture](docs/architecture.md),
[local development](docs/local-development.md), [product scope](docs/product-spec.md),
[privacy](docs/privacy-retention.md), [deployment](docs/deployment.md),
[runbooks](docs/runbooks.md), and [load test results](docs/load-test-results.md).

Source is published on [GitHub main](https://github.com/hemantsatishjadhav06-ai/whatsapp-history-ai-/tree/main).
The tested implementation commit is `a3f98c4be6c7313c37d5f9755aa5d6f0bda4a22d`; its push and remote ref were verified on 7 October 2026.
Railway authentication/network access remains blocked, so no hosted application URL is available.
Live personal WhatsApp pairing/sync, real model quality, Calendar/Gmail connections,
installed-device integration and production-scale gateway orchestration require separate
evidence. Browser fixture journeys do not establish those outcomes.
