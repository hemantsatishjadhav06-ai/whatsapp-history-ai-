# Readiness record

The backend increment is verified; the current request enables the Milo web/native UI
and publication increment. Local client checks passed; publication checks remain open.
Runtime validation and external integration eligibility are separate from implemented code.

The CTO handoff backend increment has passed synthetic functional/regression checks.
Native outward operations remain simulated, and real account/model/production gates stay open.
Current per-feature status and exact evidence are tracked in
[IMPLEMENTATION_STATUS.md](../IMPLEMENTATION_STATUS.md) and [QA_REPORT.md](QA_REPORT.md).

| Check / capability | Current evidence |
| --- | --- |
| Frozen dependency environment | Final saved `bash scripts/dev-bootstrap.sh` repeat passed frozen Python plus root/gateway npm locks, PostgreSQL/Redis health and seven migrations |
| Temporal workflow sandbox | Workflow definition successfully prepared with installed Temporal SDK |
| SQL-outbox worker tests | 8 passed: idempotent registration, crash-ack recovery, retained failures, cancellation, Kafka acknowledgement/exclusion, content rejection |
| TypeScript connector boundary | Typecheck and 33 contract/mock-adapter/gateway tests passed; no live pairing/transport |
| Synthetic load smoke | 100/100 canonical ingestion writes passed at concurrency 4; see load results |
| Synthetic draft-to-send demo | Passed import/reimport, owner style, live event, draft/edit, exact-hash approval, mock send, repeated same attempt and pause |
| Backend suite | Full runs: 533 SQLite passed (228.91s), 533 PostgreSQL passed (589.73s). Later privacy bundle: 77 focused cases passed on SQLite (19.61s) and PostgreSQL (59.59s), each with one known compatibility warning; separate commands, not a larger full-run claim. Prior 495-case evidence remains in QA |
| Startup and Alembic migrations | Seven revisions through `05439876fc2e`; local PostgreSQL migration/alignment passed. Native rotating-refresh upgrade does not invent credentials for existing sessions |
| Running PostgreSQL API | Version 0.3.0 confirmed in OpenAPI after restart; live/readiness health and final synthetic HTTP workflow passed, including scoped import/style/draft/approval/mock dispatch/pause and content cleanup |
| Bounded automation | 20 tests passed for explicit business-hours grants, verified facts/templates, history/stale exclusion, limits, expiry, takeover/revocation, uncertainty and restart recovery; simulated sends only |
| General selected-chat actions | Mock acceptance for versioned grants, independent reaction meaning/habits, authentic native text, precise two-audience forwarding, durable logical deduplication, control/source changes and current authority |
| Local people/companion | Exact namespace identity and local-only saves, typed authenticated commands, scoped digest, suppression/expiry and independent local work tested; external contact destinations unavailable |
| Authorized jobs/privacy/budgets | Bounded proactive and recurring exact-owner jobs, DST policies, held schedules/quiet hours/expiry, reminders, model/action quota reservations and bounded retention tested |
| Authenticated action bridge | Actual local Python–Node–SQL smoke passed four wire operations/four durable attempts, duplicate suppression and forged-recipient rejection; all transport operations simulated |
| Worker entry points | Actions, jobs and retention `--once` startup passed |
| Docker images | Frozen API 0.3.0 and standalone web rebuilt; 45 API/38 web inputs unchanged, both non-root; authoritative cloud CA bundle mounted only at build time |
| Non-root image acceptance | API migrations/readiness and local HTTP import/style/approval/idempotent mock dispatch/Pause passed; authenticated web private-proxy/assets/nonce/CSRF/origin/route denial passed; zero provider calls |
| Compose services | PostgreSQL(pgvector), Redis, Kafka 3.9.0 and Temporal 1.26.2 started and healthy in this cloud instance |
| Real Kafka relay | Actual broker publish/consume passed with SQL acknowledgement and metadata-only envelope |
| Real Temporal recovery | Stable SQL-outbox registration passed; worker SIGKILL before due/restart after due led to accepted mock dispatch and exactly 1 send attempt |
| Updated Kafka/Temporal rerun | Passed: one actual publish/consume with SQL acknowledgment/no private body; SIGKILL-before-due/restart-after-due produced exactly one accepted mock send attempt |
| PostgreSQL deployment recovery | Requires separately recorded database/integration evidence; the Temporal worker recovery harness used disposable SQLite |
| Real Google/Meta/model integrations | Credentials and actual authorized operations are required; mock tests do not establish these |
| Phone coexistence/personal pairing | Not implemented or validated |
| 50,000 accounts / production launch | Unmeasured; architecture target only |
| Milo browser/native acceptance | Production web build, strict workspace typechecks, 62 browser cases, 20 shared-contract/10 proxy/16 native helper cases and all-platform Expo export passed; installed-device evidence absent |
| Cloud setup configuration | Saved installer repeat passed; instructions updated for seven migrations, web and native. Draft requires publication; protected Railway token/backboard access and new-task restoration remain unverified |
| GitHub | Source is local and reviewable in the checkout; no commit or push performed |

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
Redis has no application consumer yet. The development instance deliberately enables local
mock login/drafting and keeps external sending disabled. Those development flags are forbidden
by production settings. The API health check does not certify external account access.
