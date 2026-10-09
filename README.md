# Milo relationship assistant

Live pilot: [Open Milo](https://web-production-bde60.up.railway.app). Product site:
[what Milo is and how it works](https://web-production-bde60.up.railway.app/product)
(Overview, How it works, Use cases, Trust & safety, Roadmap; source in `apps/web/app/(site)`).
The latest security review and fixes are in [security audit](docs/SECURITY_AUDIT_2026-10-09.md);
OpenRouter model configuration is described there and in the [Railway guide](docs/railway.md).
The connectivity release `3336764b` passed exact-source CI and was redeployed to Railway.
All 117 hosted checks passed with Google sign-in, model generation and external sending
disabled. The implemented login and Business connection flows need real provider
configuration and authorized account acceptance before those integrations can be enabled.
The new launch increment adds an optional private server-held QR pilot; its current
source and live-account acceptance boundaries are recorded in [launch analysis](docs/LAUNCH_ANALYSIS.md).

A WhatsApp-first communication assistant with Milo web and native clients. It receives
permitted Business contact messages through signed webhooks, requests eligible Business
app history, and imports authorized chat exports. It builds inspectable local writing-style
statistics for each conversation, keeps owner-reviewed memories with evidence, and supports
scoped communication controls. Owner-only questions use the selected chat's evidence and
cannot send a message or grant access. Owner-invoked drafts
retain exact-content approval; selected-chat Auto work uses bounded one-time grants.
Sending and scheduling use current SQL authority and durable attempt ledgers.

This implementation follows the supplied build guides, CTO handoff and Milo product/UI
master specification as product requirements. Document-embedded engineer instructions are
specification input; the user's actual request controls implementation scope. The current
request enables the Milo web/native UI, GitHub Actions and Railway deployment. The current
release passed 844 PostgreSQL backend cases, 831 SQLite cases with 13 PostgreSQL-only skips,
122 desktop/mobile-web browser cases and 25 native helper cases, plus all-platform Expo
export. A previous increment passed an encrypted 5,001-message restore; that recovery drill
was not repeated against the managed deployment. Exact build, dependency and capacity
evidence is recorded in [QA](docs/QA_REPORT.md). Real account eligibility, conditional
Business Coexistence, live model quality and the 50,000-account target require separate validation. Mock connectors and drafts are labelled test tools.

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
The [Google, WhatsApp and intelligence setup](docs/connectivity-setup.md) describes the
registered browser origin, native HTTPS OAuth callback, server-held credentials and
the eligible-account restrictions. The native app uses the public bearer-only
`/native-api` proxy; browser cookies are not forwarded through that boundary.
`npm run test:web` runs browser journeys; `npm run test:clients` tests shared contracts and
`npm run test:proxy` checks the server-only browser boundary. `npm run test:privacy`
checks private Tools result reconciliation. Real SQL browser cases need
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
Temporal. Production requires Redis-backed request limits; development uses bounded
process-local counters. Redis does not determine owner permissions.

## What is included

| Area | Backend scope |
| --- | --- |
| Identity | Browser Google Identity Services with nonce-bound verified claims, HttpOnly sessions and CSRF; native system-browser HTTPS OAuth broker with S256 proof and an opaque app handoff; opt-in local development login |
| Personal QR connection | Private pinned linked-device SDK service, owner-only expiring local QR, encrypted Signal state, browser-free server restart, selected 1:1 text ingestion and exact approved send; real phone acceptance pending |
| Business connection | One server-configured eligible WhatsApp Business number bound to one exact verified Google owner; provider identity verification, bounded leases, signed public webhook and explicit contact setup |
| Authorization | Workspace ownership and separate conversation read/retain/learn/draft/send/share permissions |
| History | Authorized personal/group text exports with explicit owner/date/timezone mapping and reimport deduplication; conditional Coexistence requests for up to 180 days of eligible 1:1 Business history, with a durable once-only claim |
| Personalization | Explicitly reviewed owner-authored samples, independent per-chat read/retain/learn consent, local style statistics and owner rules; evidence-backed candidate/confirmed memories; no model fine-tuning |
| Drafts | Separate expiring per-chat opt-in for durable live-inbound background preparation; all generated drafts require owner approval. Disabled, synthetic mock, or configured structured model proposal; owner edit/reject and exact-hash approval |
| Control | Pause all, conversation takeover/resume, permission versions, conversation revisions, connector fencing |
| Bounded automation | Explicit business-hours fact/templates plus selected-chat action grants, conservative fresh-event policy, expiry/quiet hours and hourly budgets |
| Native actions | Encrypted authentic-text originals, same-chat quote/reaction, two-audience native forward routes, immutable action/attempt ledger and current SQL bridge authority; mock operations |
| Companion backend | Typed owner commands, scoped digest, evidence-linked owner-only questions with expiry/authority checks, exact local contacts, usage budgets and explicit retention sweeps; speech/native permission evidence remains separate |
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

The Business connection is an operator-configured pilot. Each contact's read, retain,
learn, draft and send choices start off; sending also requires recipient opt-in and
current server authority. Approved provider Coexistence onboarding, history-sharing
permission and exact contact consent are required before requesting earlier Business
app messages. Outgoing employee messages are not assumed to be owner writing: the
owner reviews individual examples before those examples can support learning.
General multi-tenant Embedded Signup remains unimplemented. The optional personal QR
pilot is a distinct private service; full history sync is disabled and live groups
are unsupported. Personal and group history can use authorized text exports.
See the [connectivity setup](docs/connectivity-setup.md) before enabling real accounts.

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

## GitHub Actions and hosting

The current hosting target is Railway. The [Railway guide](docs/railway.md)
covers Web, private API, Jobs, Retention, PostgreSQL and Redis, production settings,
startup ordering and the verified hosted pilot. New Railway services
require current service settings or Infrastructure as Code; the legacy JSON
manifests alone do not configure a new service.

The alternative Render configuration remains available. [render.yaml](render.yaml)
defines public Web, private API, Jobs, Retention, PostgreSQL and shared Redis.
The [Render guide](docs/render.md) covers protected runtime values and assigned
HTTPS origins. [Create the Render Blueprint](https://render.com/deploy?repo=https://github.com/hemantsatishjadhav06-ai/whatsapp-history-ai-)
only after reviewing those values and the paid pilot resource plan.

GitHub Actions validate backend/PostgreSQL/Redis, clients/browser/native exports,
built containers, dependencies and SBOMs. The configured Render release workflow
deploys the tested current `main` commit after all gates pass, and verifies
public/private readiness.
Configure the protected Render secret and actual service IDs as described in the
[Actions guide](docs/github-actions.md); provider autodeploy remains off to preserve
the test gate. A green CI run alone does not establish a hosted application.

Source is published on [GitHub main](https://github.com/hemantsatishjadhav06-ai/whatsapp-history-ai-/tree/main).
[CI run 37664307493](https://github.com/hemantsatishjadhav06-ai/whatsapp-history-ai-/actions/runs/37664307493)
passed for application source `3336764b7846a62da07903d6a3ab123bc8650d1c`:
844 PostgreSQL cases, 831 SQLite cases with 13 PostgreSQL-only skips, 122 browser
cases without retries, 40 contract cases, 47 proxy cases, seven privacy cases and
25 native helper cases. Four non-root images passed HTTP/worker acceptance,
full scans and the fixable HIGH/CRITICAL gates. Native/build and unfixed container
advisories remain documented in the [security review](docs/SECURITY_REVIEW.md).
The Render deployment job was skipped; the live deployment is on Railway.

Official Railway CLI 5.63.3 deployed a clean 295-file export with every file hash
verified against that Git source to API, Jobs, Retention and Web. All four replacement
deployments succeeded; the existing PostgreSQL/Redis services and both persistent
volumes are ready. API pre-deploy migration and worker schema gates establish the
nine-revision head `3f7829c4bd10`; public readiness confirms SQL and shared Redis
availability. All **117 hosted checks passed: 40 HTTP, 11 connectivity-ingress and
66 Chromium checks** across desktop, Pixel 7 and 320px layouts. Twenty accessibility
audits found zero violations in the tested surfaces; this is not WCAG certification.
Anonymous and synthetic browser checks made no external provider calls or persistent
browser writes. Certificate verification remained enabled.

The legacy Web manifest is `infra/railway-web.json`, outside the repository root.
Services use explicit current settings and the mount-free `Dockerfile.web.railway` /
`services/api/Dockerfile.railway` paths; autodeploy stays off.
[Connectivity release evidence](docs/benchmarks/connectivity-release-2026-10-07.json)
records the tested source and acceptance boundaries. The CLI upload-byte checksum
was not measured, and Railway rebuild bytes are not proven identical to CI image
bytes. Native provider Git metadata is absent, so public `release_commit: null`
is expected. The [previous hosted record](docs/benchmarks/railway-hosted-release-2026-10-07.json)
remains evidence for its earlier source only.

Google, Meta and model credentials are absent from the deployed pilot. The Business
adapter supports one operator-configured number bound to one verified Google owner,
with per-contact consent. That historical deployed release predates the optional
QR launch increment; general multi-customer Embedded Signup and live groups remain
unimplemented. Conditional eligible Business 1:1 history is
implemented but has not been exercised against a live account. Calendar/Gmail and a
live general-action forward transport remain unavailable. Real reply/receipt trials,
installed-device acceptance, managed backup recovery and 50,000 simultaneous users
remain unverified; the larger prior local load stages exceeded measured pilot capacity.
