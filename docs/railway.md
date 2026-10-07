# Railway deployment

The repository includes deployable web, API, Jobs and Retention containers, plus an optional Actions worker. A configured manifest and a successful local build do not mean a Railway deployment exists. Deployment is complete only after Railway reports healthy services and the public URL passes acceptance checks.

## Current provider result

Application source `3336764b7846a62da07903d6a3ab123bc8650d1c` passed
[CI run 37664307493](https://github.com/hemantsatishjadhav06-ai/whatsapp-history-ai-/actions/runs/37664307493)
and was redeployed on 7 October 2026 through official Railway CLI 5.63.3 from a
clean **295-file export**, with every file hash verified against Git. The live pilot
is [Milo](https://web-production-bde60.up.railway.app). At 18:22 UTC all six services
reported successful deployments and running instances, and both persistent volumes
were ready. API pre-deploy migration and Jobs/Retention exact-schema startup gates
completed for the nine-revision source head `3f7829c4bd10`. Public readiness verifies
private SQL and shared Redis availability; Retention reported bounded sweeps.

| Role | Accepted replacement deployment |
| --- | --- |
| API | `7a55e9e7-d406-4957-934c-df0daab7ad46` |
| Jobs | `279f39b0-096c-4b17-a8fb-df4159d51443` |
| Retention | `a0bde525-5399-4092-b2a4-abf669da3a47` |
| Web | `e513951f-17c6-496e-8d2d-22b659c03813` |

All **117 hosted checks passed: 40 HTTP, 11 connectivity ingress and 66 Chromium**
across ten desktop/Pixel 7/320px surfaces. Twenty accessibility audits found zero
violations in those surfaces; this is not WCAG certification. Checks used anonymous
or synthetic content, made no provider calls or persistent browser writes, and
retained TLS verification. Current CI passed 844 PostgreSQL, 831 SQLite with 13
PostgreSQL-only skips, 122 browser cases without retries, 40 contracts, 47 proxy,
seven privacy and 25 native helpers, plus four image gates and full scans. Fixable
HIGH/CRITICAL gates passed while unfixed image/native advisories remain; see [QA](QA_REPORT.md).

Native provider Git metadata is absent, so public `release_commit: null` is expected.
The CLI's accepted upload-byte checksum was not measured; the local Git archive hash
must not be substituted for it. Railway rebuild bytes are not proven identical to
CI image bytes. [Connectivity release evidence](benchmarks/connectivity-release-2026-10-07.json)
records current source, accepted deployments and acceptance boundaries. Keep source
autodeploy off; a documentation-only commit does not change deployed application bytes.

The existing PostgreSQL 18.6 and Redis 8.2.10 services retain private routing and
ready volumes at `/var/lib/postgresql/data` and `/data`. Volume readiness and schema
startup gates do not establish direct remote SQL/Redis inspection, runtime AOF state
or managed recovery; those checks remain unrun. All pilot roles remain at one replica.

Google, Meta and model credentials are absent. Browser/native Google sign-in and
eligible Business connectivity need the protected configuration and actual account
trials in [connectivity setup](connectivity-setup.md). The Business pilot supports
one configured number bound to one verified Google owner, with per-contact consent;
conditional up-to-180-day 1:1 Coexistence history is implemented but live-unverified.
General multi-customer Embedded Signup, personal QR pairing and live groups are not
implemented. Model generation and external sends remain disabled. Real replies,
receipts, installed-device integration and 50,000-user capacity remain unverified.

### Previous deployment evidence

The earlier source `8b1ee2da798aab53464e45842fbb455ffedaa98b` passed
[run 37638332729](https://github.com/hemantsatishjadhav06-ai/whatsapp-history-ai-/actions/runs/37638332729)
and a 266-file verified export was deployed, with 106 hosted checks. Its
[previous hosted record](benchmarks/railway-hosted-release-2026-10-07.json) remains
historical evidence. The initial `72df83c` API build failed on unsupported bind/secret
Docker mounts, and a later attempt selected Web settings for API. Mount-free
Dockerfiles and moving the legacy root Web manifest to `infra/railway-web.json`
resolved those failures. New services use explicit current service settings and
Dockerfile variables; legacy role configuration was rejected as deprecated.

## Service configuration

Use one Railway project with PostgreSQL and Redis services plus these repository services. PostgreSQL stores durable owner data; Redis is required for shared production request limits and is checked by API readiness. Keep each service's source root at the repository root and deploy the tested source from `hemantsatishjadhav06-ai/whatsapp-history-ai-`.

On 7 October 2026, Railway's [official Config as Code guide](https://docs.railway.com/guides/config-as-code) states that new services cannot opt into legacy Config as Code, and existing JSON/TOML configurations continue only until 1 December 2026. The files below retain the tested startup specifications for existing services. For new services, apply the matching current service settings through the dashboard/API or Railway Infrastructure as Code; uploading these files alone does not apply them. The provider rejected explicit `railwayConfigFile` assignment as deprecated and referred to `.railway/railway.ts`. Keep legacy role manifests under `infra/`; a root `railway.json` selected Web settings during the API build despite the explicit API Dockerfile setting. Do not add it back to configure a new role.

| Service | Railway Dockerfile | Legacy config file | Runtime |
| --- | --- | --- | --- |
| Web | `Dockerfile.web.railway` | `/infra/railway-web.json` | Non-root Node 24, Next standalone app; dependency health `/readyz`, liveness `/healthz` |
| API | `services/api/Dockerfile.railway` | `/infra/railway-api.json` | Non-root Python API; serialized Alembic pre-deploy migration; health `/health/ready` |
| Jobs | `services/api/Dockerfile.railway` | `/infra/railway-jobs.json` | Waits for the exact migration head, then runs the required private SQL worker for authorized jobs and scheduled intents |
| Retention | `services/api/Dockerfile.railway` | `/infra/railway-retention.json` | Waits for the exact migration head, then runs the required private expiry and authentication/session cleanup worker |
| Actions | `services/api/Dockerfile.railway` | `/infra/railway-actions.json` | Optional private action-admission worker; leave undeployed until the live adapter and planner gates are verified |

For new services, [railway-service-settings.json](../infra/railway-service-settings.json)
contains one non-secret `ServiceInstanceUpdateInput` per enabled role. Field names
and types were checked against the official GraphQL schema on 7 October.
Apply a selected role with
[railway-service-update.graphql](../infra/railway-service-update.graphql), supplying
the actual `serviceId`, `environmentId` and role object as `input` through the CLI's
`--variables @PATH` option. Keep any temporary request under ignored `.local`.
These files are explicit API inputs; they are not automatically applied by upload.

Set the documented `RAILWAY_DOCKERFILE_PATH` service variable to `Dockerfile.web.railway`
for Web and `services/api/Dockerfile.railway` for API, Jobs and Retention, matching each
payload's `dockerfilePath`. Do not map legacy `builder: DOCKERFILE` to the GraphQL
builder enum, which does not accept that value. Configure protected variables with
`railway variable set KEY --stdin --skip-deploys` and explicit service/environment
selectors before creating a deployment. The Web start command is
`node apps/web/server.js`; all four services start at one replica.

The official CLI's database templates create persistent volumes. Verify the
resulting Postgres/Redis volume and private networking before adding any extra
volume; never create a duplicate blindly. Current public templates use PostgreSQL
18 and Redis 8.2, while recorded local acceptance used PostgreSQL 16 and Redis 7.4.
The actual provisioned PostgreSQL 18.6 and Redis 8.2.10 services reported successful
deployments, startup readiness and ready volumes;
application-level use and recovery on these provider versions still need acceptance.
PostgreSQL mounts
`/var/lib/postgresql/data` with its `PGDATA` subdirectory; Redis mounts `/data`.
Keep database routing private and publish only the Web domain.

The web service receives `BACKEND_URL`, the private API origin including its port, and `PUBLIC_APP_ORIGIN`, its exact public HTTPS origin. The proxy uses that explicit origin for browser writes instead of trusting forwarded headers to choose an origin. Browser requests use the web service's same-origin `/api` proxy, so private backend routing and session cookies stay server-side. Next.js reads Railway's runtime `PORT`; the API manifest also expands `PORT`. Generate a Railway domain for Web. Give API a public domain only if a verified webhook or direct mobile client needs it, with HTTPS and the same production authentication requirements.

Configure API, Jobs and Retention using Railway's protected runtime variables. Any enabled Actions worker must use the same production settings:

| Variable | Production value |
| --- | --- |
| `ENVIRONMENT` | `production` |
| `DATABASE_URL` | Reference the project's PostgreSQL connection variable |
| `REDIS_URL` | Reference the project's private Redis connection variable |
| `REQUEST_LIMITS_MODE` | `redis` |
| `REQUEST_RATE_NAMESPACE` | A distinct namespace for the selected environment, such as `milo-production-v1` |
| `ENCRYPTION_KEY` | A stable externally stored Fernet key, shared by API and all database workers |
| `INTERNAL_SERVICE_TOKEN` | A separate service-authentication secret |
| `SESSION_SECURE` | `true` |
| `ALLOW_DEV_AUTH` | `false` |
| `ALLOWED_ORIGINS` | The exact HTTPS web origin and other explicitly configured client origins |
| `GOOGLE_CLIENT_ID` | The owner's configured Google OAuth application client ID |
| `GOOGLE_CLIENT_SECRET` | Server-held secret for the registered Web OAuth client; required by the native HTTPS broker |
| `GOOGLE_NATIVE_REDIRECT_URI` | `https://web-production-bde60.up.railway.app/api/auth/native/google/callback`, registered exactly with Google |
| `GOOGLE_NATIVE_APP_REDIRECT_URI` | `milo://oauth`; only an opaque single-use handoff is returned here |
| `MODEL_PROVIDER` | `disabled` until a verified model configuration is supplied |
| `ENABLE_EXTERNAL_SENDS` | `false` until the provider account and send capability are verified |

Keep secrets outside Git, image layers, build arguments and command output. API and all database workers must use the same encryption key; changing it without a migration makes existing encrypted data unreadable. Add the web HTTPS origin to Google OAuth's authorized origins. Publishing the frontend does not verify Google authentication, WhatsApp pairing, history sync or provider delivery.

Native clients use `EXPO_PUBLIC_API_URL=https://web-production-bde60.up.railway.app/native-api`.
The dedicated public proxy accepts native bearer sessions and rejects ambient browser
cookies/origins; the API remains private. Google uses the registered HTTPS server
broker callback and the app's original S256 proof. Do not ship the Google client
secret or use an Android custom-scheme Google callback. Legacy native client IDs
do not configure the broker. The signed Meta callback is also on public Web at
`/api/webhooks/whatsapp`, with raw-byte HMAC verification. See
[connectivity setup](connectivity-setup.md). Real Google/Meta and installed-device
acceptance must pass before enabling their public operations.

For separate browser source buckets, configure the same dedicated secret as Web `BACKEND_PROXY_KEY` and API `TRUSTED_PROXY_KEY`. Keep Web `TRUST_PROXY_HOPS=0` until Railway ingress behavior has been verified; this ignores requester forwarding headers. An enabled hop count must select a provider-verified forwarding suffix, never a client-supplied prefix. Until trusted source forwarding is configured, browser requests share the private Web peer's source bucket, which is conservative but restricts large cohorts. Use private database/Redis routing and remove unneeded public TCP proxies from newly provisioned data services.

The API pre-deploy step applies the repository's Alembic migrations before traffic. Its startup override binds both IPv4 and IPv6, which supports Railway ingress and private service DNS; the installed Uvicorn version was verified with HTTP on both local address families. Deploy Jobs and Retention after API migrations succeed, using the same database and production settings. Neither worker needs a public domain or an HTTP health route. Retention visits workspaces in pages of 100 and sleeps 30 seconds between completed cycles; query batches are bounded, while full-cycle duration depends on the tenant count and retention backlog. A stopped Retention service allows expired authentication metadata to accumulate, so monitor its process, restart history, cycle age and cleanup output. Jobs' SQL timers work without Temporal. The Actions manifest is preparation for a later verified adapter/planner release and is not part of the enabled pilot topology. The existing Temporal worker, registrar and Kafka relay are optional independent processes; their development plaintext clients require supported production TLS/authentication configuration before connecting to external infrastructure.

## Authentication and release

The official Railway CLI supports `RAILWAY_TOKEN` for a project/environment token
and `RAILWAY_API_TOKEN` for an account/workspace token. `RAILWAY_TOKEN` takes
precedence and sends the project-token header. For the verified workspace key,
bind it securely as `RAILWAY_API_TOKEN` and unset `RAILWAY_TOKEN` in the CLI process.
If the cloud environment stores that same key under `RAILWAY_TOKEN`, remap the
existing value in memory; a new credential is unnecessary. Supply explicit
project, environment and service IDs to avoid personal-account discovery or
local linking requirements. An OAuth session from `railway login` is also supported.

Direct API calls use `Authorization: Bearer` for account/workspace tokens, and
`Project-Access-Token` for project tokens. A workspace token cannot query personal
`me` data; test it with `workspace(workspaceId: ...)` or its accessible project
list instead. Earlier `me` and project-header errors were the wrong scope and did
not establish invalid credentials. See Railway's [official authentication examples](https://docs.railway.com/guides/public-api).
Use secure environment bindings; keep token values out of source, shell arguments
and shared output. CLI variable-list commands print raw values and must not be
used in shared logs.

After exact-source CI passes, select the already created project/environment/service
and upload a clean staging checkout with
`railway up --project <project-id> --service <service-id> --environment <environment-id> --ci`.
Record the returned deployment identity and inspect actual status/readiness.
`railway up --detach` only starts deployment. CLI upload archives the working tree;
a SHA in `--message` is a label and does not create verified Git commit metadata.
Preserve the clean source identity and verify the actual deployed build separately;
do not fabricate `RAILWAY_GIT_COMMIT_SHA` to fill an absent provider field.
Keep GitHub source autodeploy disabled until an exact-source test-gated release
path is configured. Source connection can trigger deployment; repository
publication alone does not verify a healthy release.

Acceptance checks cover Web `/healthz` and `/readyz`, API `/health/ready`, the public companion page, Google login configuration, server-side proxy/cookie behavior and read-only capability status. `/readyz` exposes only coarse private API readiness and validated `RAILWAY_GIT_COMMIT_SHA` metadata when present (or Render metadata on Render); CLI uploads have no native Git metadata, so this pilot returns `release_commit: null`. Verify source identity through the recorded Git-verified export and actual deployment evidence. Test live provider operations only after their real credentials and account evidence are available. Keep development login and mock connectors unavailable on the public production API.

For local container acceptance, build from the repository root with `docker build -f Dockerfile.web -t milo-web:local .`, then run `uv run --frozen python scripts/web_container_smoke.py`. In an environment with a managed certificate authority, supply its existing trusted bundle with `--secret id=trusted_ca,src=/etc/ssl/certs/ca-certificates.crt`; certificate verification remains enabled. The smoke test starts and removes its own disposable web container and synthetic API, checks standalone assets, private backend requests, authenticated snapshots, nonce-cookie paths, CSRF controls and cross-site rejection, and makes no provider calls. Its temporary backend uses development authentication solely to seed the test owner; the production Railway API forbids that mode.

For the Railway release, cold-build both mount-free variants separately:
`docker build -f Dockerfile.web.railway -t milo-web:railway .` and
`docker build -f services/api/Dockerfile.railway -t relationship-assistant-api:railway .`.
Run the Web and Railway runtime smokes against those image tags with their
`--image` options, and retain their own security/SBOM results. Successful checks
for the original Dockerfiles do not validate these replacement images.
The cloud's managed certificate setup may differ from Railway's public trust
store; certificate verification must remain enabled in both environments.

The successful access/provisioning used the official API and CLI 5.63.3; no
callable Railway connector was available. The ordinary sandbox network route
returned HTTP 403, while the supported route reached the API with HTTP 200 and
verified workspace-scoped access. Build-role selection and hosted startup were
resolved by removing the root legacy Web override and using the official CLI
with explicit selectors. The live pilot is verified; external integration,
managed recovery and target-capacity gates remain open.

## Pilot replicas and admission limits

The prepared pilot topology is one stateless Web replica, one API replica with one Uvicorn process, one Jobs consumer and one Retention worker. The optional Actions manifest also specifies one replica, but it stays undeployed. Browser sessions, nonces, owner data and durable operation ledgers are in shared PostgreSQL; API and all database workers require the same externally managed encryption key. The local `.local` directory is not a production data volume. This release stores supported text/native text records in PostgreSQL and does not implement media storage.

Keep API, Jobs, Retention and any enabled Actions worker at one replica while distributed control and job-claim races are being hardened and tested. Keep any future live gateway at one replica until its distributed lease and submission boundaries are verified. `submit_guard` is a process-local lock; adding replicas does not make that lock distributed. Separate database uniqueness and account fencing protect specific records, but are not evidence that every concurrent read-modify-write path is safe. Keep model calls and external social sends disabled during the pilot. Pause workers before a release, apply migrations once, verify the replacement API, then restart Jobs and Retention. Configure the platform's termination grace to exceed the API's 30-second graceful-shutdown timeout and verify that behavior before relying on drain during live submissions.

The API startup bounds active connections/tasks using `API_CONCURRENCY_LIMIT` (default 64), a 256-connection socket backlog and five-second idle keepalive. Admission overflow returns HTTP 503. These are bounded pilot settings, not a claim that 64 active users or 50,000 active users have been validated. Tune the limit only with the staged test results and allocated CPU, memory and database capacity. A client that loses confirmation of a write must reconcile the durable action/job before retrying it.

The SQLAlchemy PostgreSQL defaults are `DB_POOL_SIZE=5`, `DB_MAX_OVERFLOW=5`, `DB_POOL_TIMEOUT_SECONDS=3`, `DB_POOL_RECYCLE_SECONDS=1800` and `DB_STATEMENT_TIMEOUT_MS=15000`. Budget peak connections as `(DB_POOL_SIZE + DB_MAX_OVERFLOW) × (API replicas × Uvicorn processes + Jobs processes + Retention processes + enabled Actions processes + other database workers) + migration/operations reserve`. The required API, Jobs and Retention processes can consume up to 30 connections with the default pools; enabling Actions raises that ceiling to 40, before the migration/operations reserve. These ceilings assume each process uses its configured maximum pool and are not measured steady-state usage. Check the provisioned database's `max_connections`, reserved administration slots, memory and measured pool wait before scaling; HTTP users do not each receive a database connection. Do not add workers or replicas until that connection budget still fits. An external PgBouncer pool needs a tested mode and prepared-statement/transaction compatibility; it is not configured or verified here.

The application also bounds in-flight requests separately (`REQUEST_MAX_INFLIGHT=32` with `REQUEST_CONTROL_RESERVE=4` and `REQUEST_THREAD_TOKENS=48` by default), limits request bodies and wait time, and applies shared rate buckets through Redis. The Uvicorn connection cap precedes application admission and can still reject a control request during socket saturation; the reserve is not a measured control-latency guarantee. The default global API bucket of 60,000 requests per minute is approximately 1,000 requests/s. Fifty thousand clients polling every five seconds would request approximately 10,000/s, so that workload requires measured architecture and limit changes instead of a replica-count edit.

The [local request-limit smoke evidence](request-limits-smoke.md) verifies shared atomic budgets with two independent limiter instances against real Redis, hashed expiring counters, signed source handling and fail-closed outage behavior before application dispatch. It does not measure production throughput or verify Railway ingress headers.

Jobs scans at most 100 due records per tick, processes them serially and sleeps five seconds between ticks. This bounds a pilot batch but does not establish a throughput or recovery target. Measure due-job age, expiry, duplicate prevention and uncertain outcomes before adding consumers. PostgreSQL point-in-time recovery, a separately retained encryption key and an actual restore drill are required before claiming storage recovery; merely creating a database service does not verify recovery.

The 50,000-user target remains an unmeasured release gate. Distinguish idle browser connections, authenticated polling, event ingestion, reply generation and actual provider delivery. Stage representative load through 100, 1,000, 10,000 and 50,000 users; record successful/failed requests, p95/p99 latency, queue age, database pool wait, row/storage growth and per-replica memory. Exercise revocation, global pause, worker loss, reconnect and database restore during the declared workload. See [load-test evidence and acceptance targets](LOAD_TEST_REPORT.md) for the measured local scope and the separate capacity gates.

`uv run --frozen python scripts/railway_runtime_smoke.py` checks the manifest startup in its own empty test container: non-root execution, runtime `PORT`, both IP families, configured admission, real HTTP overload rejection/recovery and shutdown. The configuration smoke used 72 HTTP probes for a 64-connection limit, observed HTTP 503 under overload, recovered after release and shut down cleanly. It is not a 50,000-user test or an authenticated Railway deployment.

In the previous eight-migration 2026-10-07 hardening record, this runtime smoke passed against the rebuilt API image `sha256:b9f6c1a528f96decd6761ad74260936ab6e20450163e25c114cdb3526628a89c`: 10 of 72 probes received the configured overload response, readiness recovered to 200, and shutdown exited 0 in 0.655 seconds. Separate disposable containers also ran `/app/.venv/bin/python -m assistant.lifecycle --once` and `/app/.venv/bin/python -m assistant.actions --once`; each exited 0 as UID 10001 after applying all eight migrations to `86b7bbad6fc1`. Each had its own empty SQLite database, no network, disabled providers, and verified matches for seven frozen API source modules. These worker checks verify entrypoints and schema compatibility, rather than cleanup throughput, populated-database behavior or live delivery. The official Railway CLI 5.63.3 separately parsed both worker manifests in an offline configuration-migration dry run, with one replica each; neither check created a Railway service.
