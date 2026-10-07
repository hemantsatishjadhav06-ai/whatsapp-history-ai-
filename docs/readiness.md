# Readiness record

The backend and Milo web/native pilot have passed the local checks below. Source is
published on GitHub; the latest user request selects Railway. Workspace authentication
and PostgreSQL/Redis readiness succeeded. Source `8b1ee2da` passed CI and its
Git-verified export was deployed through the official Railway CLI. API migrations,
worker schema startup and 106 hosted checks passed at the live
[Milo pilot](https://web-production-bde60.up.railway.app). Google/model/social
operations, production recovery and target capacity remain open. The alternative
Render preparation remains available.
Runtime validation and external integration eligibility are separate from implemented code.

The CTO handoff backend increment has passed synthetic functional/regression checks.
Native outward operations remain simulated, and real account/model/production gates stay open.
Current per-feature status and exact evidence are tracked in
[IMPLEMENTATION_STATUS.md](../IMPLEMENTATION_STATUS.md) and [QA_REPORT.md](QA_REPORT.md).

| Check / capability | Current evidence |
| --- | --- |
| Frozen dependency environment | Installer saved; installed Node versions match the current lock, cryptography 50.0.2 is active, PostgreSQL/Redis are healthy, and eight migrations/schema alignment passed |
| Temporal workflow sandbox | Workflow definition successfully prepared with installed Temporal SDK |
| SQL-outbox worker tests | 8 passed: idempotent registration, crash-ack recovery, retained failures, cancellation, Kafka acknowledgement/exclusion, content rejection |
| TypeScript connector boundary | Typecheck and 33 contract/mock-adapter/gateway tests passed; no live pairing/transport |
| Measured HTTP load | 1,280 real API/proxy checks at 8/24 users passed. Larger paced128/burst64 stages returned explicit overload failures and recovered; see the full load report |
| Synthetic draft-to-send demo | Passed import/reimport, owner style, live event, draft/edit, exact-hash approval, mock send, repeated same attempt and pause |
| Backend suite | Latest `8b1ee2da` CI passed 747 PostgreSQL and 734 SQLite cases with 13 PostgreSQL-only skips. Prior local timings (241.05s/635.17s), warnings and corrected failures are recorded in QA |
| Startup and Alembic migrations | Eight revisions through `86b7bbad6fc1`; local PostgreSQL migration/alignment and restore repeats passed. Native rotating-refresh upgrade does not invent credentials for existing sessions |
| Running PostgreSQL API | Version 0.3.0 confirmed in OpenAPI after restart; live/readiness health and final synthetic HTTP workflow passed, including scoped import/style/draft/approval/mock dispatch/pause and content cleanup |
| Bounded automation | 20 tests passed for explicit business-hours grants, verified facts/templates, history/stale exclusion, limits, expiry, takeover/revocation, uncertainty and restart recovery; simulated sends only |
| General selected-chat actions | Mock acceptance for scoped grants, reactions, native sources, forwarding, deduplication and current authority. Production general-action dispatch remains blocked pending a real adapter and evaluated planner |
| Local people/companion | Exact namespace identity and local-only saves, typed authenticated commands, scoped digest, suppression/expiry and independent local work tested; external contact destinations unavailable |
| Authorized jobs/privacy/budgets | Bounded proactive and recurring exact-owner jobs, DST policies, held schedules/quiet hours/expiry, reminders, model/action quota reservations and bounded retention tested |
| Authenticated action bridge | Actual local Python–Node–SQL smoke passed four wire operations/four durable attempts, duplicate suppression and forged-recipient rejection; all transport operations simulated |
| Worker entry points | Actions, jobs and retention `--once` startup passed |
| Docker images | Run 37638332729 passed four image gates, HTTP/worker checks and full scans for `8b1ee2da`; fixable HIGH/CRITICAL gates passed but unfixed findings remain. API is non-root Python 3.12.14/cryptography 50.0.2; image evidence and scan scope are in QA |
| Non-root image acceptance | API migrations/readiness and local HTTP import/style/approval/idempotent mock dispatch/Pause passed; authenticated web private-proxy/assets/nonce/CSRF/origin/route denial passed; zero provider calls |
| Compose services | PostgreSQL(pgvector), Redis, Kafka 3.9.0 and Temporal 1.26.2 started and healthy in this cloud instance |
| Real Kafka relay | Actual broker publish/consume passed with SQL acknowledgement and metadata-only envelope |
| Real Temporal recovery | Stable SQL-outbox registration passed; worker SIGKILL before due/restart after due led to accepted mock dispatch and exactly 1 send attempt |
| Updated Kafka/Temporal rerun | Passed: one actual publish/consume with SQL acknowledgment/no private body; SIGKILL-before-due/restart-after-due produced exactly one accepted mock send attempt |
| Local PostgreSQL recovery | Encrypted 5,001-message/37-table backup restored with matching durable/decrypted state; wrong key rejected, Forget/held jobs/replay preserved. Managed production PITR, role/key custody and regional recovery remain unverified |
| Real Google/Meta/model integrations | Credentials and actual authorized operations are required; mock tests do not establish these |
| Phone coexistence/personal pairing | Not implemented or validated |
| 50,000 accounts / production launch | NOT_RUN; the larger local workloads failed availability. Architecture target and distributed runner do not certify capacity |
| Milo browser/native acceptance | Current production build, strict web TypeScript and 38 proxy/readiness cases passed. Previous source passed 70 browser cases without retries; unchanged contracts/privacy/native graph: 20 shared-contract/seven Tools privacy/16 native helpers and all-platform Expo export passed; installed-device evidence absent |
| Cloud setup configuration | Installer/start instructions saved for eight migrations, web/native, workers and hosting checks; a fresh cloud task restored source/CLI access and the securely bound Railway workspace credential was verified with a scoped query |
| Railway infrastructure | PostgreSQL 18.6/Redis 8.2.10 private services and volumes ready; API pre-deploy migrations and SQL/Redis readiness verified, Jobs/Retention schema startup passed. Direct SQL/Redis inspection, runtime AOF state and managed recovery remain unverified |
| GitHub / application hosting | [Run 37638332729](https://github.com/hemantsatishjadhav06-ai/whatsapp-history-ai-/actions/runs/37638332729) passed for `8b1ee2da`; official CLI deployed a 266-file Git-verified export. [Live Milo](https://web-production-bde60.up.railway.app) passed 40 HTTP + 66 Chromium checks across ten desktop/Pixel 7/320px surfaces, including 20 accessibility audits with zero violations; no certification or live-provider claim |

The core workflow must be able to fail when broken: authenticate, establish conversation
permissions, import/receive content, generate a scoped proposal, owner-edit/review it,
approve the exact hash, reject stale/current-state violations, dispatch through a durable
ledger, and inspect the outcome. The synthetic demo exercises that path with mock transport.

Before claiming launch readiness, resolve the pending capability gates in
[capabilities](capabilities.md) and run the deployment/recovery scenarios in
[deployment](deployment.md) and [runbooks](runbooks.md). Published environment configuration,
a restored new cloud task, real provider delivery, and production capacity must each have
their own evidence.

The current-instance API (version 0.3.0) and independent selected-chat action, authorized-job
and application-retention workers were restarted against the new source. OpenAPI version,
both health checks and the final synthetic HTTP smoke passed. PostgreSQL, Redis, Kafka and
Temporal are local development services. The default transport is built-in mock simulation, so no
persistent Node gateway or WhatsApp session is running. The separate HTTP bridge smoke
tested a disposable local Node gateway.
Redis now provides shared production request limits; the local development API uses its
bounded memory limiter. The development instance deliberately enables local
mock login/drafting and keeps external sending disabled. Those development flags are forbidden
by production settings. The API health check does not certify external account access.
