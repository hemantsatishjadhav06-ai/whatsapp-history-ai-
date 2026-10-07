# Deployment boundaries

The repository provides API/web container builds, Railway service manifests and a development
Compose stack. Their existence does not establish a production deployment or a
50,000-account service. The current web/private-API/worker configuration and hosted
acceptance steps are in [Railway deployment](railway.md); real deployment is blocked by
missing authentication and network access.

Build the API image from the repository root:

```bash
docker build -f services/api/Dockerfile -t relationship-assistant-api:local .
```

The image uses Python 3.12, the frozen uv lockfile, and a non-root runtime user. Apply
Alembic migrations as an explicit release step before admitting traffic. Persistent local
development files and secrets are excluded from the image build context. Pin container
digests and update reviewed patch versions in the release process; tags in the development
manifest are not a substitute for an image provenance policy.

In cloud environments where the build runs behind an organizational TLS proxy, mount the
already managed host CA bundle during installation. Certificate and package-checksum
verification remain enabled; the mount is not persisted in the final image:

```bash
BUILDX_CONFIG="$PWD/.local/buildx" docker build \
  --secret id=trusted_ca,src=/etc/ssl/certs/ca-certificates.crt \
  -f services/api/Dockerfile -t relationship-assistant-api:local .
```

This cloud instance required that trust configuration and successfully built the API image.
The first failure identified a missing builder CA, not an invalid dependency checksum. Use
the authoritative CA bundle for your own environment; never disable TLS verification.

Run the isolated built-image acceptance smoke after building:

```bash
uv run --frozen python scripts/container_smoke.py
```

This starts a disposable non-root API container, migrates its SQLite database, verifies
readiness, then exercises the synthetic import/style/draft/approval/idempotent-send/pause
flow through actual loopback HTTP. It removes its container on completion. No provider
tokens or real account data are used, and it does not establish PostgreSQL deployment readiness.

Production settings require `ENVIRONMENT=production`, PostgreSQL, Redis-backed request
limits, an externally managed
Fernet encryption key, secure session cookies, development login disabled, and a real or
disabled model provider. Configure the intended frontend origins, Google client ID, and
separate least-privilege internal service authentication. Supply provider credentials via
secure runtime configuration. Do not bake them into images, `.env.example`, scripts, or logs.

The real Business transport additionally needs an eligible verified phone-number ID,
app secret, webhook verification token, access token, TLS webhook routing, recipient opt-in,
and explicit external-send enablement. An identity verification response does not prove
phone coexistence, group sending, history availability, quota capacity, or provider delivery.

Deploy Web, API, Jobs and Retention as independently restartable services, plus
managed PostgreSQL and Redis. Jobs runs authorized SQL schedules without requiring
Temporal; Retention is required for bounded application/session cleanup. Keep
Actions undeployed until its real adapter and evaluated planner pass release gates.
The legacy Temporal worker/registrar and Kafka relay are optional processes sharing
the authoritative SQL database. Configure supported production TLS/authentication
before connecting them to external services; current clients and Compose profiles
target local plaintext development infrastructure. Budget all process pools together.

The live endpoint demonstrates process availability. The ready endpoint demonstrates the
SQL schema and required request-limit backend are available; it intentionally
reports external integrations as not validated.
Build deployment gates that check provider access, worker registration, event publication,
delivery receipts, migrations, and restore behavior separately. Collect content-free
metrics for outbox age, pending schedule lag, ambiguous send attempts, revoked-work blocks,
model budgets, and provider error rates. A monitoring/exporter stack is not included yet.

Before launch, validate multi-worker races against PostgreSQL, service restart recovery,
webhook redelivery, token revocation, cross-tenant attacks, owner phone echoes, opt-out,
backup restore with deletion tombstones, and realistic model quality. The requested
50,000-account capacity additionally requires measured gateway session footprint, quotas,
tenant placement cells, fairness, failure isolation, and simultaneous-burst testing. Those
are launch gates, not measurements from the small synthetic smoke test.
