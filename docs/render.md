# Render deployment

[render.yaml](../render.yaml) prepares a new paid Render project for the tested GitHub repository. Only Web has a public HTTPS URL. API, PostgreSQL, Redis-compatible Key Value, Jobs and Retention use Render's private network in the same region. This file is infrastructure configuration; no Render deployment or live URL is claimed until the provider reports the requested commit live and the public acceptance checks pass.

The user selected a new Render project. Existing services and data should not be adopted, changed or deleted by this setup. Review the resource plan in Render before creating it: the blueprint uses paid services, rather than free services that sleep or databases that expire. Current prices, workspace availability and region eligibility must be checked in the actual account; no price estimate is treated as verified here.

| Resource | Blueprint name | Paid pilot plan | Runtime and purpose |
| --- | --- | --- | --- |
| Public Web | `milo-web` | `standard`, one instance | Non-root Node 24, Next standalone; `/readyz` checks the private API |
| Private API | `milo-api` | `standard`, one instance | Non-root Python, one Uvicorn process; runtime `PORT=10000` |
| Jobs | `milo-jobs` | `starter`, one instance | Authorized SQL jobs and scheduled intents; no public listener |
| Retention | `milo-retention` | `starter`, one instance | Bounded retention and expired authentication metadata cleanup |
| PostgreSQL | `milo-postgres` | `basic_1gb`, 10 GiB storage | PostgreSQL 16, durable encrypted owner data |
| Key Value | `milo-limits` | `starter` | Shared Redis-compatible atomic rate limits, `noeviction` |

All resources use `singapore`; this is a deployment location, not verification of model-provider processing location or any legal requirement. PostgreSQL and Key Value have empty external IP allow lists. Supported text records are stored in PostgreSQL; this release does not implement social-media attachment/object storage. The repository's Actions worker, Temporal worker and Kafka relay are not provisioned by this pilot blueprint. General production action delivery remains disabled until the actual adapter and planner are implemented and tested.

## Protected runtime settings

The blueprint creates `milo-backend-production`, an environment group linked to API, Jobs and Retention. Add the following protected values securely to that group in Render. These values are deliberately omitted from Git, rather than filled with deployable defaults. `sync: false` is used only for Web's directly entered origin, not as a promise that Render will prompt for secrets inside an environment group.

| Group setting | Required value |
| --- | --- |
| `ENCRYPTION_KEY` | A valid Fernet key retained in an external secret manager; the same stable key for every API/database worker |
| `INTERNAL_SERVICE_TOKEN` | A separate random service-authentication secret |
| `ALLOWED_ORIGINS` | The actual Web HTTPS origin, with no wildcard, credentials or URL path; use explicit comma-separated origins only when needed |
| `GOOGLE_CLIENT_ID` | The verified browser Google application client ID when sign-in is enabled |
| `GOOGLE_ANDROID_CLIENT_ID`, `GOOGLE_IOS_CLIENT_ID` | Verified installed-app client IDs only when the corresponding native apps are configured |

Set Web `PUBLIC_APP_ORIGIN` to exactly the same actual HTTPS origin. Use the assigned Render URL visible in the service's Dashboard, or an already owned and verified custom domain. Do not invent an `onrender.com` URL from the service name: Render can choose a different slug. If the assigned URL is available only after initial provisioning, configure these origin values afterward and redeploy; provisioning alone is not a healthy application release. Google OAuth authorized origins must include this exact public origin. An unset Google client ID leaves real sign-in unavailable.

The shared group enforces `ENVIRONMENT=production`, `ALLOW_DEV_AUTH=false`, `SESSION_SECURE=true`, `REQUEST_LIMITS_MODE=redis`, `MODEL_PROVIDER=disabled` and `ENABLE_EXTERNAL_SENDS=false`. Backend settings fail closed without PostgreSQL, the externally retained encryption key and an exact HTTPS allowed origin. No mock model or development login is enabled in production. Each service gets its private database connection through `fromDatabase: connectionString` and its private Redis connection through `fromService: connectionString`; neither connection string belongs in the public client bundle.

Web receives `BACKEND_HOSTPORT` using `fromService` with the private API's `hostport` property. This property contains a bare host and port. Web's explicit `/bin/sh -c` startup wrapper builds `BACKEND_URL="http://${BACKEND_HOSTPORT}"` at runtime before starting Node. A YAML `value` containing `${...}` is not relied on for interpolation, and the bare host/port is not passed to the proxy as if it were a complete URL. API origins never refer back to Web environment variables, so there is no Web-to-API-to-Web secret-reference cycle.

Keep `TRUST_PROXY_HOPS=0` until Render's actual forwarding-header behavior has been verified. With this setting, client-supplied forwarding headers cannot choose a rate-limit identity. Browser traffic conservatively shares the private Web peer's source bucket. If trusted forwarding is later enabled, configure a dedicated matching Web `BACKEND_PROXY_KEY` and API `TRUSTED_PROXY_KEY`, each at least 32 random bytes, separately from the encryption and internal-service keys. Verify the selected right-hand forwarding suffix before changing the hop count. This blueprint does not claim that verification has happened.

Neither WhatsApp nor model-provider credentials are provisioned by default. A later eligible WhatsApp Business Cloud integration requires a verified provider account, credentials, a verified owner-subject binding and live-account acceptance, in addition to a separately designed public webhook route. The blueprint's private API cannot receive Meta webhooks or direct native-device connections from the Internet. Adding a public API domain is an explicit subsequent deployment mode; it must retain production authentication and request limits. Personal WhatsApp pairing, history sync and general autonomous replies remain implementation gates, not settings unlocked by a hosting token.

Keep completed environment files, connection strings, service credentials and customer content outside GitHub logs and build arguments. Protect and separately retain the Fernet key with the database backups. Replacing it without a tested encryption migration makes stored data unreadable. A successful database creation does not establish point-in-time recovery, restore correctness or an operating retention policy. Perform a provider-specific backup/restore drill and verify the current plan's retention/PITR features before accepting real owner data. Preserve protected environment-group values when reviewing any future Blueprint sync.

## GitHub release wiring

[The GitHub release workflow](../.github/workflows/render-release.yml) runs repository validation and local container acceptance for the commit being released. Its deployment script selects that exact Git SHA through Render's REST API, checks the configured services, polls each resulting deployment, and verifies public Web readiness. Independent service autodeploys are set to `off` in the blueprint. Also disable Blueprint auto-sync in the Render Dashboard: automatic Blueprint sync can otherwise initiate releases or resource changes outside the GitHub release workflow.

Use the protected GitHub `production` environment for secret `RENDER_API_KEY`. Add these GitHub repository or environment variables after the new services exist:

| Variable | Value |
| --- | --- |
| `RENDER_SERVICE_ID_API` | Actual Render service ID for `milo-api` |
| `RENDER_SERVICE_ID_WEB` | Actual Render service ID for `milo-web` |
| `RENDER_SERVICE_ID_JOBS` | Actual Render service ID for `milo-jobs` |
| `RENDER_SERVICE_ID_RETENTION` | Actual Render service ID for `milo-retention` |
| `RENDER_AUTO_DEPLOY` | `true` only when tested pushes to `main` should also deploy; otherwise use a manual workflow run |

An API key is a secret, while a service ID is a selector. Supply credentials through the provider/GitHub secure settings, never through a chat message or shell argument. The release script derives the public URL from Render's service metadata; it does not require a guessed URL variable. See [GitHub Actions setup and release controls](github-actions.md) for the workflow's exact configuration and evidence artifacts.

API's pre-deploy command runs `/app/.venv/bin/python -m assistant.render_entrypoint migrate`, which serializes Alembic migration through a PostgreSQL advisory lock. Jobs and Retention run `assistant.render_entrypoint wait-schema` before their respective worker commands. The worker gate waits for the repository's current migration head rather than treating YAML list order as a deployment dependency. Neither worker independently applies migrations. Render's Docker command override explicitly invokes the shell when runtime variable expansion or command sequencing is needed.

Release API first, then Jobs, Retention and Web after the API migration succeeds. The exact selected SHA must be reported by all four Render deployments. Web `/readyz` returns only coarse readiness and its provider-injected `RENDER_GIT_COMMIT`, without private backend addresses or exception details. It bounds its private API probe to three seconds, shares concurrent probes and caches the coarse result for at most five seconds. API `/health/ready` checks database schema and shared Redis. `/healthz` is Web liveness and should not be treated as proof that the database or backend is ready. Render private services use a port health check; the blueprint does not assign an unsupported private-service HTTP health-check path.

GitHub validates the selected source commit and records its built-image scans. Render
rebuilds that commit; this is not promotion of the identical scanned image digest.
Frozen application locks and pinned base images remain enforced, but signed OS
updates and build artifacts can change between builds. Provider image provenance
and a production-filesystem scan remain separate evidence.

Quiesce the existing Jobs and Retention consumers before a later schema-changing release and use a tested compatible migration strategy. Worker startup's schema gate does not stop an old worker that is already running, and successful per-service deployment cannot make a multi-service release atomic. Verify platform shutdown/drain behavior and durable job recovery before relying on it during live provider submissions. The documented rollout does not promise zero downtime or automated data rollback.

After live deployment, verify the companion page, `/healthz`, `/readyz`, Google sign-in, cookie paths, same-origin/CSRF behavior, data import and permission isolation with an authorized test owner. Verify that external sends remain disabled. Do not turn synthetic demo acceptance or mocked reply delivery into a claim that a real WhatsApp message was delivered. Real provider tests require the separate authorized provider setup and account evidence.

## Pilot capacity and recovery limits

This is a modest paid pilot, not a 50,000-user configuration. Each API/database worker is one process/instance. Defaults allow 32 active API requests, including four control slots, with 48 thread tokens and a separate 64-connection Uvicorn limit. Each Web process also bounds proxy requests and large imports. These guards prevent unbounded buffering; they do not prove throughput or control latency at saturation.

The configured database connection ceiling is up to 30 across API, Jobs and Retention: `(5 pool + 5 overflow) × 3 processes`, plus migration/operations reserve. Check the actual managed PostgreSQL `max_connections`, reserved slots, memory and pool wait before using this topology. Extra API instances or workers each add another pool. No PgBouncer or verified multi-cell tenant scaling is installed. Keep API and workers at one instance until their distributed control, job claiming and capacity gates have been verified with the provisioned services.

The local [load report](LOAD_TEST_REPORT.md) passed the smaller 8/24 concurrency cases and recorded overload failures at larger paced/burst stages. The 50,000-user target was not run or met. Hosting plan names and a green deployment do not change that evidence. Stage representative polling, ingestion, reply proposal, control/revocation, worker-loss and storage/restore workloads on the actual paid infrastructure before expanding access. Monitor p95/p99 latency, unexpected failures, explicit admission/rate rejections, pool wait, Redis memory, due-job age, retention cycle age, stored-row growth and per-process memory.

## Verification and access status

On 2026-10-07, Render documentation/schema and API requests from this task received HTTP 403. No callable Render connector or authenticated Render token was available. Therefore Render-side Blueprint schema/semantic validation, resource creation, deployment polling and public live acceptance could not be completed in this runtime. Local YAML/runtime-contract checks are reported separately from provider validation. No billable Render resources were created by these checks.

Authoritative source checks used Render's public [CLI repository](https://github.com/render-oss/cli/tree/5772e84f9b8ba99ba1b3890d73423a70ca68daf8). Its generated REST types confirm exact `commitId` deployment selection, service metadata and deployment status values. Its [Blueprint validation implementation](https://github.com/render-oss/cli/blob/5772e84f9b8ba99ba1b3890d73423a70ca68daf8/cmd/blueprintvalidate.go) sends an authenticated multipart `POST /v1/blueprints/validate` with a file and workspace ID; it is not offline schema validation. Render's official [Celery blueprint](https://github.com/render-examples/celery/blob/main/render.yaml) and [Sidekiq blueprint](https://github.com/render-examples/sidekiq/blob/main/render.yaml) demonstrate private Redis-compatible service references, `connectionString`, `noeviction`, workers and empty external IP allow lists.

When secure Render access and workspace selection are available, run the official `render blueprints validate render.yaml --workspace <workspace-id> --output json` before approving the first resource plan. Provider schema and semantic validation are required, including current plan/region compatibility and existing-resource conflict review. Validation is a read-only preview and does not create the services. Then create the new Blueprint/project, configure protected values and exact origins, configure GitHub service IDs, run the tested release workflow, and return only the URL obtained from successful provider/public acceptance.

Provider references: [Blueprint specification](https://render.com/docs/blueprint-spec), [private networking](https://render.com/docs/private-network), [Docker deployments](https://render.com/docs/docker), [pre-deploy commands](https://render.com/docs/pre-deploy-command), [service deployments API](https://api-docs.render.com/reference/create-deploy), [environment groups](https://render.com/docs/configure-environment-variables#environment-groups), and [instance plans](https://render.com/pricing). These links identify the required authoritative provider checks; blocked retrieval is not reported as current-document verification.
