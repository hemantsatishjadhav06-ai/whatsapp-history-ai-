# Railway deployment

The repository includes deployable web, API, Jobs and Retention containers, plus an optional Actions worker. A configured manifest and a successful local build do not mean a Railway deployment exists. Deployment is complete only after Railway reports healthy services and the public URL passes acceptance checks.

Use one Railway project with PostgreSQL and Redis services plus these repository services. PostgreSQL stores durable owner data; Redis is required for shared production request limits and is checked by API readiness. Keep each service's source root at the repository root and deploy the tested source from `hemantsatishjadhav06-ai/whatsapp-history-ai-`.

On 7 October 2026, Railway's [official Config as Code guide](https://docs.railway.com/guides/config-as-code) states that new services cannot opt into legacy Config as Code, and existing JSON/TOML configurations continue only until 1 December 2026. The files below retain the tested startup specifications for existing services. For new services, apply the matching current service settings through the dashboard/API or Railway Infrastructure as Code; uploading these files alone does not apply them. Do not rely on the deprecated `railwayConfigFile` setting for new services.

| Service | Config file | Runtime |
| --- | --- | --- |
| Web | `/railway.json` | Non-root Node 24, Next standalone app; dependency health `/readyz`, liveness `/healthz` |
| API | `/infra/railway-api.json` | Non-root Python API; serialized Alembic pre-deploy migration; health `/health/ready` |
| Jobs | `/infra/railway-jobs.json` | Waits for the exact migration head, then runs the required private SQL worker for authorized jobs and scheduled intents |
| Retention | `/infra/railway-retention.json` | Waits for the exact migration head, then runs the required private expiry and authentication/session cleanup worker |
| Actions | `/infra/railway-actions.json` | Optional private action-admission worker; leave undeployed until the live adapter and planner gates are verified |

For new services, [railway-service-settings.json](../infra/railway-service-settings.json)
contains one non-secret `ServiceInstanceUpdateInput` per enabled role. Field names
and types were checked against the live official GraphQL schema on 7 October;
authenticated application remains unrun. Apply a selected role with
[railway-service-update.graphql](../infra/railway-service-update.graphql), supplying
the actual `serviceId`, `environmentId` and role object as `input` through the CLI's
`--variables @PATH` option. Keep any temporary request under ignored `.local`.
These files are explicit API inputs; they are not automatically applied by upload.

Set the documented `RAILWAY_DOCKERFILE_PATH` service variable to `Dockerfile.web`
for Web and `services/api/Dockerfile` for API, Jobs and Retention, matching each
payload's `dockerfilePath`. Do not map legacy `builder: DOCKERFILE` to the GraphQL
builder enum, which does not accept that value. Configure protected variables with
`railway variable set KEY --stdin --skip-deploys` and explicit service/environment
selectors before creating a deployment. The Web start command is
`node apps/web/server.js`; all four services start at one replica.

The official CLI's database templates create persistent volumes. Verify the
resulting Postgres/Redis volume and private networking before adding any extra
volume; never create a duplicate blindly. Current public templates use PostgreSQL
18 and Redis 8.2, while recorded local acceptance used PostgreSQL 16 and Redis 7.4.
Those new provider versions need their own runtime acceptance. PostgreSQL mounts
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
| `GOOGLE_ANDROID_CLIENT_ID`, `GOOGLE_IOS_CLIENT_ID` | Installed-app client IDs for each native platform that will be enabled |
| `MODEL_PROVIDER` | `disabled` until a verified model configuration is supplied |
| `ENABLE_EXTERNAL_SENDS` | `false` until the provider account and send capability are verified |

Keep secrets outside Git, image layers, build arguments and command output. API and all database workers must use the same encryption key; changing it without a migration makes existing encrypted data unreadable. Add the web HTTPS origin to Google OAuth's authorized origins. Publishing the frontend does not verify Google authentication, WhatsApp pairing, history sync or provider delivery.

Native clients use a separately configured HTTPS API origin (`EXPO_PUBLIC_API_URL`) and the installed-app Google client IDs above. Keep their Expo redirect and Google application configuration aligned with the released app. These public client settings do not contain session or service credentials. Leave native sign-in unavailable until its provider configuration and a real device login have been verified.

For separate browser source buckets, configure the same dedicated secret as Web `BACKEND_PROXY_KEY` and API `TRUSTED_PROXY_KEY`. Keep Web `TRUST_PROXY_HOPS=0` until Railway ingress behavior has been verified; this ignores requester forwarding headers. An enabled hop count must select a provider-verified forwarding suffix, never a client-supplied prefix. Until trusted source forwarding is configured, browser requests share the private Web peer's source bucket, which is conservative but restricts large cohorts. Use private database/Redis routing and remove unneeded public TCP proxies from newly provisioned data services.

The API pre-deploy step applies the repository's Alembic migrations before traffic. Its startup override binds both IPv4 and IPv6, which supports Railway ingress and private service DNS; the installed Uvicorn version was verified with HTTP on both local address families. Deploy Jobs and Retention after API migrations succeed, using the same database and production settings. Neither worker needs a public domain or an HTTP health route. Retention visits workspaces in pages of 100 and sleeps 30 seconds between completed cycles; query batches are bounded, while full-cycle duration depends on the tenant count and retention backlog. A stopped Retention service allows expired authentication metadata to accumulate, so monitor its process, restart history, cycle age and cleanup output. Jobs' SQL timers work without Temporal. The Actions manifest is preparation for a later verified adapter/planner release and is not part of the enabled pilot topology. The existing Temporal worker, registrar and Kafka relay are optional independent processes; their development plaintext clients require supported production TLS/authentication configuration before connecting to external infrastructure.

For authenticated CLI deployment, the official Railway CLI supports `RAILWAY_TOKEN` for a project/environment token and `RAILWAY_API_TOKEN` for an account/workspace token. An OAuth session from `railway login` is also supported. A project token can select its own project/environment; explicit service selectors are still required for a monorepo. Use a secure environment secret for noninteractive cloud tasks instead of placing tokens in chat or shell arguments. CLI variable list commands print raw values and should not be used in shared logs.

After access is available, link or select the exact project/environment/service, upload the repository with `railway up --service <service> --environment <environment> --ci`, then inspect deployment status and generate the Web domain. `railway up --detach` only starts deployment; it does not establish that the site is healthy. For GitHub autodeploys, use `railway service source connect --repo hemantsatishjadhav06-ai/whatsapp-history-ai- --branch main --service <service>` after the repository has been published.

Acceptance checks cover Web `/healthz` and `/readyz`, API `/health/ready`, the public companion page, Google login configuration, server-side proxy/cookie behavior and read-only capability status. `/readyz` exposes only coarse private API readiness and validated `RAILWAY_GIT_COMMIT_SHA` metadata (or Render metadata on Render); verify the actual provider deployment's source identity separately. Test live provider operations only after their real credentials and account evidence are available. Keep development login and mock connectors unavailable on the public production API.

For local container acceptance, build from the repository root with `docker build -f Dockerfile.web -t milo-web:local .`, then run `uv run --frozen python scripts/web_container_smoke.py`. In an environment with a managed certificate authority, supply its existing trusted bundle with `--secret id=trusted_ca,src=/etc/ssl/certs/ca-certificates.crt`; certificate verification remains enabled. The smoke test starts and removes its own disposable web container and synthetic API, checks standalone assets, private backend requests, authenticated snapshots, nonce-cookie paths, CSRF controls and cross-site rejection, and makes no provider calls. Its temporary backend uses development authentication solely to seed the test owner; the production Railway API forbids that mode.

The latest access check used Railway CLI 5.63.3 and both official GraphQL API hosts. The ordinary sandbox route returned HTTP 403; the supported escalated network route succeeded with HTTP 200. With the supplied credential, project-token authentication returned `Project Token not found`; account-token authentication returned `Not Authorized`. CLI project status, account identity and project-list checks also rejected it. This establishes an authentication blocker after network access was resolved; it is not merely a CLI login prompt.

The saved environment draft already requires `RAILWAY_TOKEN` for `backboard.railway.app` and `backboard.railway.com`, and permits those API destinations. Create a valid project token for the intended deployment environment and save it securely under that existing requirement; apply it to the runtime and recheck project status. A declared requirement or saved draft is not authenticated access. No callable Railway connector is available here; the official CLI/API are the fallback. No resources were created or deployment/public URL claimed. Rotate the credential posted in chat and keep replacements out of source and shared logs.

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

On 2026-10-07, this runtime smoke passed against the rebuilt API image `sha256:b9f6c1a528f96decd6761ad74260936ab6e20450163e25c114cdb3526628a89c`: 10 of 72 probes received the configured overload response, readiness recovered to 200, and shutdown exited 0 in 0.655 seconds. Separate disposable containers also ran `/app/.venv/bin/python -m assistant.lifecycle --once` and `/app/.venv/bin/python -m assistant.actions --once`; each exited 0 as UID 10001 after applying all eight migrations to `86b7bbad6fc1`. Each had its own empty SQLite database, no network, disabled providers, and verified matches for seven frozen API source modules. These worker checks verify entrypoints and schema compatibility, rather than cleanup throughput, populated-database behavior or live delivery. The official Railway CLI 5.63.3 separately parsed both worker manifests in an offline configuration-migration dry run, with one replica each; neither check created a Railway service.
