# Local and cloud development

Use the existing checkout in `/workspace/whatsapp-history-ai-`. Each cloud task already has
an isolated environment; create no Git worktree unless the user explicitly requests one.
Commands below are run from the repository root.

```bash
bash scripts/dev-bootstrap.sh
make lint
make gateway-check
make test
make demo
make load-smoke
make dev
```

The bootstrap uses `uv sync --frozen --group dev`, locked Node installation and
`alembic upgrade head`. For a configured localhost PostgreSQL URL, it starts/checks local
PostgreSQL and Redis before migrations. Caches and
managed Python installations default to ignored `.local/` directories so startup does not
depend on a writable home directory. It creates `.env` from `.env.example` only if absent
and sets restrictive local file permissions. Existing configuration is preserved.

`make dev` starts the API on loopback port 8000. Readiness requires the migrated SQL schema:

```bash
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
```

For local development login over HTTP, explicitly set `ALLOW_DEV_AUTH=true` and
`SESSION_SECURE=false`. Use `MODEL_PROVIDER=mock` for synthetic proposals. Production
configuration rejects development authentication, insecure sessions, SQLite, and mock
models. Defaults have external sends disabled and real model calls disabled.

`make demo` and `make load-smoke` create a disposable synthetic application. These tools
generate ephemeral test session/authentication material in memory, use a temporary SQLite
database, and remove the database on completion. They do not modify your normal development
database or use real external accounts. The demo asserts import deduplication and send
attempt idempotency; the load smoke reports the exact environment and request count.

## Optional service integration

Docker Compose requires a local `POSTGRES_PASSWORD` in ignored `.env`. Set `DATABASE_URL`
to a URL-encoded `postgresql+psycopg` URL for the `assistant` user/database, then migrate.
No reusable file contains a password or provider token.

```bash
make infra
make migrate
make infra-events
make infra-workflows
```

All published infrastructure ports bind to loopback. These are single-node development
services with durable named volumes. They are not production deployment manifests.
Run the event and schedule components in separate terminals:

```bash
make relay
make worker
make registrar
```

Run the narrow, explicitly granted business-hours automation worker separately when testing it:

```bash
uv run --frozen python -m assistant.automation
```

The worker processes fresh live references only, rechecks each current grant/fact/budget,
and uses the common dispatcher. All other intents stay in owner-reviewed draft mode.

The optional integration checks are distinct from core API tests: start a real broker and
Temporal server, publish/consume an outbox event, register and execute an approved synthetic
schedule, stop/restart the worker across the due time, and confirm one send attempt after
recovery. Use `python -m assistant.workflows registrar --once` or
`python -m assistant.relay --once` for one explicit batch. A passing unit mock does not
establish real service connectivity or worker recovery.

Both real local integrations have a reusable synthetic recovery check:

```bash
uv run --frozen python scripts/integration_smoke.py --kafka --temporal
```

The reusable synthetic integration harness exercises real local services:

```bash
uv run --frozen python scripts/integration_smoke.py --kafka --temporal --output .local/integration.json
```

It publishes/consumes metadata with SQL acknowledgement through Kafka. For Temporal it
creates an approved synthetic schedule, registers it idempotently, starts a worker in an
isolated task queue, kills that worker before the due time, restarts it after the due time,
and requires an accepted mock dispatch with exactly one send attempt. Worker logs are kept
under ignored `.local/`; temporary test database/key material is discarded. The first
execution verified this scenario on Kafka 3.9.0 and Temporal 1.26.2. It uses no real accounts.

For the Business integration, configure only the actual eligible number and securely
supplied app secret, verification token, and access token. `POST /connectors/{id}/verify`
uses Meta to verify the configured identity. Webhooks must be routed over HTTPS to the
signed webhook handler. Check [capabilities](capabilities.md) before enabling external
send calls.
