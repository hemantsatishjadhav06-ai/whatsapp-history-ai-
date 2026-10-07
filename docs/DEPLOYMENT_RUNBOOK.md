# Deployment and recovery runbook

Updated 7 October 2026. Local development is operable; a production deployment, provider
account and capacity stage have not been validated. See [deployment details](deployment.md)
and [existing operations runbooks](runbooks.md).

## Local reproduction

Use Python 3.12, uv 0.12.19 and Node 24. From the checkout, run `make bootstrap`,
`make test`, `make lint`, `make gateway-check` and `make demo`. Bootstrap preserves an
existing ignored `.env`, installs frozen locks and applies migrations. `make dev` starts
the API on loopback port 8000 without access logs. `/health/ready` checks SQL schema
and the required request-limit backend; it does not certify external access.

Compose profiles provision local PostgreSQL/pgvector, Redis, Kafka and Temporal via
`make infra`, `make infra-events` and `make infra-workflows`. Start independently restartable
`make worker`, `make registrar` and `make relay` against the same SQL database. Optional
real-infrastructure smoke uses `scripts/integration_smoke.py --kafka --temporal`; sending
remains simulated. Use a dedicated disposable database for PostgreSQL fixture tests.

The handoff adds an authorized-job polling worker (`python -m assistant.jobs`, or
`--once` for one bounded tick). The opt-in Node mock action bridge is started from
`services/connector-gateway` with `node src/start.ts` and binds loopback port 8090 by
default. Configure `CONNECTOR_GATEWAY_URL`, `CONNECTOR_GATEWAY_TOKEN` and the timeout
on Python; configure the matching `GATEWAY_TOKEN`, `PYTHON_AUTHORITY_URL` and separate
`PYTHON_INTERNAL_TOKEN` on Node. Supply token values privately through runtime settings,
never in committed scripts or logs. The gateway creates no WhatsApp session/socket and
rejects production mode. The actual local Python–Node–SQL smoke passed all four wire
operations with four durable attempts, duplicate suppression and forged-recipient rejection.
No WhatsApp provider calls occurred.

Configure the application's explicit retention worker with `python -m assistant.lifecycle`
(`--once` for one sweep, optional `--workspace-id` for a selected workspace). It processes
SQL application data and preserves replay tombstones; it performs no provider or backup
deletion. The selected-chat planner worker is `python -m assistant.actions` (`--once` for
one bounded run). These are independent restartable backend processes, not UI actions.

In this cloud instance, API 0.3.0 and persistent action/job/retention workers were restarted
against the current source. OpenAPI version and both health checks passed. Default action dispatch uses built-in mock transport,
so a persistent Node gateway is optional and is not running. The updated actual local
Kafka/Temporal smoke passed, including worker kill/restart and exactly one accepted mock
send attempt. These process/service results do not establish WhatsApp delivery.

Build with `make image`; in this cloud's managed TLS-proxy environment use the CA mount
documented in [deployment](deployment.md). Keep TLS/package verification enabled. Run
`scripts/container_smoke.py` against the built image for isolated non-root acceptance.
Docker/Compose files target development and do not supply a production secret/TLS service.

## Milo clients and Railway preparation

The source/API version is 0.3.0 with eight migrations through `86b7bbad6fc1`. The persistent
port-8000 API and workers were refreshed; the current synthetic demo and API-container
workflow passed with artifacts listed in [QA](QA_REPORT.md). Browser acceptance used an
independent disposable port-8001 fixture; its results are separate from the persistent runtime.

From the repository root run `npm ci --ignore-scripts`, `npm run typecheck`,
`npm run test:clients`, `npm run test:proxy`, `npm run test:privacy`, `npm test --workspace=@milo/mobile`,
`npm run build` and `npm run mobile:export`. The current local checks passed 20 shared
contract, 25 proxy, seven Tools privacy, 16 native helper and 64 browser cases, plus strict typechecks/build/export.
Expo export produces JavaScript/assets, not signed installed iOS/Android builds. Real OAuth,
native permissions and physical lifecycle need separately recorded builds/accounts.

See [Railway deployment](railway.md) for actual web/API/worker manifests and private-backend
configuration. The public web proxy uses a configured `PUBLIC_APP_ORIGIN`, browser route
allowlist, bounded ingress and a server-only `BACKEND_URL`; do not expose service credentials
through `NEXT_PUBLIC_*` or mobile bundles. Publish synthetic visitor state only while outward
sending remains disabled. A protected Railway access token/session and network access to
`backboard.railway.app`/`backboard.railway.com` are currently absent/blocked. No Railway build,
public URL, hosted health check or HTTPS browser smoke has been recorded.

The cloud installer/startup draft now covers web/native, hardening and eight migrations,
with publication required. Current locks, migrations, service health and non-root
image evidence are recorded in QA. Required Railway services are Web, API, Jobs and
Retention plus PostgreSQL and Redis; Actions stays undeployed pending its real
adapter/planner gates. Saving the draft does not apply runtime access, publish it
or verify new-task restoration.
The prior GitHub `main` baseline was published and verified against implementation commit
`a3f98c4be6c7313c37d5f9755aa5d6f0bda4a22d` on 7 October 2026.
This source publication is separate from Railway deployment and environment snapshot publication.
The saved draft adds a secure `RAILWAY_TOKEN` requirement and the two backboard domains;
review/save the environment settings and publish the environment before relying on these changes.

## Release preparation

Review [implementation status](../IMPLEMENTATION_STATUS.md), [QA](QA_REPORT.md),
[platform matrix](PLATFORM_CAPABILITY_MATRIX.md), [model evaluation](MODEL_EVALUATION.md),
[security/lifecycle](SECURITY_AND_DATA_LIFECYCLE.md) and [capacity](LOAD_TEST_REPORT.md).
Choose eligible account type/market and tested adapter version before enabling outward
operations. Configure real Google identity and each independent integration grant.

Production requires PostgreSQL, Redis-backed request limits, secure cookies, development login off, managed encryption
keys, scoped service identities, real/disabled models and secure origins. External sending
stays disabled until actual account/capability/grant gates pass. This rollout switch is
separate from per-message owner approval: configured eligible Auto work uses its existing
bounded authority.

Deploy schema expansion before compatible services. Apply migrations once as a release
step, verify schema alignment, run synthetic allowed/denied journeys, then canary selected
tenants. Do not run fixture teardown against an application database. Review destructive
data changes separately. Existing private-context encryption migration is forward-only;
use a reviewed forward fix instead of assuming every downgrade is safe.

## Stop and recover actions

Use the authenticated workspace pause endpoint and inspect its committed generation/state.
An already-started provider call may still be accepted; retain its attempt and reconcile.
Do not bypass SQL policy with a direct socket call, clear an uncertain ledger, or create
replacement actions to force a retry. Review current source/destination authority before
resuming; revoked/canceled/expired work must not revive.

If broker/model/worker service is down, keep SQL intents and outbox references durable.
Restart the affected process and reconcile stable IDs/registration gaps. Pause backfill
before control/live traffic degrades. Inspect scoped metadata/queue age without exposing
message content. Live distributed account ownership, service TLS/authentication and
provider reconciliation require additional production evidence.

For reconnect or account lease conflict, stop old ownership, advance its fence and verify
exact account/capabilities before granting new submissions. Old in-flight calls retain
their uncertainty. Local disconnect does not revoke a token at its provider.

For restore, stop admission/dispatch, restore encrypted SQL with the correct key version,
reapply suppression/deletion tombstones, reconcile attempts/workflows/outbox, verify
tenant negatives and only then re-enable traffic. Backup/RPO/RTO automation is pending.
For suspected unauthorized access or provider suspension, disable affected transport,
revoke relevant credentials and preserve redacted scoped incident evidence.

## Observability and unfinished gates

Monitor connection/lease freshness, phone observation lag, queue/outbox age, control commit
latency, model tokens/cost, expired jobs, blocked authority, uncertain provider outcomes and
deletion progress. A complete exporter/alert stack is not shipped. Real phone echoes,
native operations/contact destinations, privacy restore, distributed actor races,
production rollback and capacity must pass before a production-ready claim.
