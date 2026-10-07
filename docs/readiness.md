# Readiness record

The current connectivity application source `3336764b7846a62da07903d6a3ab123bc8650d1c`
is published on GitHub, passed [CI run 37664307493](https://github.com/hemantsatishjadhav06-ai/whatsapp-history-ai-/actions/runs/37664307493),
and was redeployed through the official Railway CLI from a 295-file Git-verified export.
API pre-deploy migration, worker schema startup and all 117 hosted checks passed at
[Milo](https://web-production-bde60.up.railway.app). Google, Meta and model credentials
are absent; real login, provider replies, installed-device behavior, managed recovery
and target capacity remain open. The alternative Render preparation remains available;
its deployment job was skipped. Runtime validation and external account eligibility
are separate from implemented code. See the
[connectivity release record](benchmarks/connectivity-release-2026-10-07.json).

The CTO handoff backend increment has passed synthetic functional/regression checks.
Native outward operations remain simulated, and real account/model/production gates stay open.
Current per-feature status and exact evidence are tracked in
[IMPLEMENTATION_STATUS.md](../IMPLEMENTATION_STATUS.md) and [QA_REPORT.md](QA_REPORT.md).

| Check / capability | Current evidence |
| --- | --- |
| Frozen dependency environment | Installer saved; installed Node versions match the current lock, cryptography 50.0.2 is active, PostgreSQL/Redis are healthy, and nine migrations/schema alignment through `3f7829c4bd10` passed |
| Temporal workflow sandbox | Workflow definition successfully prepared with installed Temporal SDK |
| SQL-outbox worker tests | 8 passed: idempotent registration, crash-ack recovery, retained failures, cancellation, Kafka acknowledgement/exclusion, content rejection |
| TypeScript connector boundary | Typecheck and 33 contract/mock-adapter/gateway tests passed; no live pairing/transport |
| Measured HTTP load | 1,280 real API/proxy checks at 8/24 users passed. Larger paced128/burst64 stages returned explicit overload failures and recovered; see the full load report |
| Synthetic draft-to-send demo | Passed import/reimport, owner style, live event, draft/edit, exact-hash approval, mock send, repeated same attempt and pause |
| Backend suite | Exact-source `3336764b` CI passed 844 PostgreSQL and 831 SQLite cases with 13 PostgreSQL-only skips; no failures/errors. Prior 734/747 local timings and corrected failures remain historical records in QA |
| Startup and Alembic migrations | Nine revisions through `3f7829c4bd10`; local PostgreSQL alignment, CI cross-process serialized migration (nine then zero upgrades), repeat upgrade and worker waiter passed. Railway pre-deploy and worker gates completed; direct remote SQL inspection was not run |
| Running PostgreSQL API | Current local health and source startup passed; previous version 0.3.0 OpenAPI and synthetic HTTP workflow passed, including scoped import/style/draft/approval/mock dispatch/pause and content cleanup |
| Bounded automation | 20 tests passed for explicit business-hours grants, verified facts/templates, history/stale exclusion, limits, expiry, takeover/revocation, uncertainty and restart recovery; simulated sends only |
| General selected-chat actions | Mock acceptance for scoped grants, reactions, native sources, forwarding, deduplication and current authority. Production general-action dispatch remains blocked pending a real adapter and evaluated planner |
| Local people/companion | Exact namespace identity and local-only saves, typed authenticated commands, scoped digest, suppression/expiry and independent local work tested; external contact destinations unavailable |
| Authorized jobs/privacy/budgets | Bounded proactive and recurring exact-owner jobs, DST policies, held schedules/quiet hours/expiry, reminders, model/action quota reservations and bounded retention tested |
| Authenticated action bridge | Actual local Python–Node–SQL smoke passed four wire operations/four durable attempts, duplicate suppression and forged-recipient rejection; all transport operations simulated |
| Worker entry points | Local and Railway images each passed Actions, Jobs and Retention `--once` startup: six entrypoints |
| Docker images | Run 37664307493 passed four image gates, HTTP/worker checks and full scans for `3336764b`; fixable HIGH/CRITICAL gates passed but unfixed findings remain. API is non-root Python 3.12.14/cryptography 50.0.2; image evidence and scan scope are in QA |
| Non-root image acceptance | API migrations/readiness and local HTTP import/style/approval/idempotent mock dispatch/Pause passed; authenticated web private-proxy/assets/nonce/CSRF/origin/route denial passed; zero provider calls |
| Compose services | PostgreSQL/Redis health rerun passed; previous actual local Kafka 3.9.0/Temporal 1.26.2 readiness and recovery records remain scoped to those runs |
| Real Kafka relay | Actual broker publish/consume passed with SQL acknowledgement and metadata-only envelope |
| Real Temporal recovery | Stable SQL-outbox registration passed; worker SIGKILL before due/restart after due led to accepted mock dispatch and exactly 1 send attempt |
| Updated Kafka/Temporal rerun | Passed: one actual publish/consume with SQL acknowledgment/no private body; SIGKILL-before-due/restart-after-due produced exactly one accepted mock send attempt |
| Local PostgreSQL recovery | Previous-source encrypted 5,001-message/37-table backup restored with matching durable/decrypted state; not rerun for this source or managed storage; wrong key rejected, Forget/held jobs/replay preserved. Managed production PITR, role/key custody and regional recovery remain unverified |
| Real Google/Meta/model integrations | Credentials are absent from the deployed pilot. Registered Google browser/native HTTPS broker configuration, eligible Business account and model settings plus actual authorized operations are required; mock tests do not establish these |
| Business Coexistence / personal pairing | Conditional eligible up-to-180-day 1:1 Business history request/ingestion implemented with once-only claim and contact consent; live account eligibility/history unverified. Personal QR/session pairing and live groups are not implemented |
| 50,000 accounts / production launch | NOT_RUN; the larger local workloads failed availability. Architecture target and distributed runner do not certify capacity |
| Milo browser/native acceptance | Exact-source CI passed 122 browser cases without retries, 40 contracts, 47 proxy, seven privacy and 25 native helpers; strict workspace typechecks, production Web build and all-platform Expo export passed. Installed-device evidence absent |
| Cloud setup configuration | Installer rerun passed frozen locks, preserved existing environment settings, and applied nine migrations; startup instructions cover web/native, workers and hosting checks; a fresh cloud task restored source/CLI access and the securely bound Railway workspace credential was verified with a scoped query |
| Railway infrastructure | PostgreSQL 18.6/Redis 8.2.10 private services and volumes ready; API pre-deploy migrations and SQL/Redis readiness verified, Jobs/Retention schema startup passed. Direct SQL/Redis inspection, runtime AOF state and managed recovery remain unverified |
| GitHub / application hosting | [Run 37664307493](https://github.com/hemantsatishjadhav06-ai/whatsapp-history-ai-/actions/runs/37664307493) passed for `3336764b`; official CLI redeployed a 295-file Git-verified export. [Live Milo](https://web-production-bde60.up.railway.app) passed 117 checks: 40 HTTP + 11 connectivity ingress + 66 Chromium across ten desktop/Pixel 7/320px surfaces, including 20 accessibility audits with zero violations; no certification or live-provider claim |

The core workflow must be able to fail when broken: authenticate, establish conversation
permissions, import/receive content, generate a scoped proposal, owner-edit/review it,
approve the exact hash, reject stale/current-state violations, dispatch through a durable
ledger, and inspect the outcome. The synthetic demo exercises that path with mock transport.

Before claiming launch readiness, resolve the pending capability gates in
[capabilities](capabilities.md) and run the deployment/recovery scenarios in
[deployment](deployment.md) and [runbooks](runbooks.md). Published environment configuration,
a restored new cloud task, real provider delivery, and production capacity must each have
their own evidence.

The installer and current API/Web startup were rerun after the connectivity changes;
local health and the new native broker configuration route passed. These local
processes do not persist into a new cloud machine. The earlier synthetic HTTP
workflow and separate disposable Python–Node gateway bridge remain evidence for
their recorded runs; no persistent native WhatsApp session is running. Optional
Kafka/Temporal recovery tests used actual local services with simulated transport.
Production requires shared Redis request limits and forbids local development login
or mock model settings. API health alone does not establish external account access.
