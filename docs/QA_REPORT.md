# QA report

Updated 7 October 2026; runs retain their recorded dates below. Tests use synthetic content
and identities unless explicitly
listed as actual local or hosted infrastructure. No real Google, model or WhatsApp account journey
has been exercised. The current request enables Milo web/native and deployment acceptance;
client results below are separate from backend and installed-device results.

## 7 October Railway deployment follow-up

The live pilot is [Milo](https://web-production-bde60.up.railway.app). All
**106 hosted checks passed: 40 HTTP and 66 Chromium checks** across ten surfaces
on desktop, Pixel 7 and 320px layouts. Twenty accessibility audits reported zero
violations on the tested surfaces; this is not WCAG certification. Browser
review/digest checks used synthetic content and made no external provider calls
or persistent browser writes.

[GitHub run 37638332729](https://github.com/hemantsatishjadhav06-ai/whatsapp-history-ai-/actions/runs/37638332729)
passed for exact application source
`8b1ee2da798aab53464e45842fbb455ffedaa98b`: 747 PostgreSQL cases, 734 SQLite
cases with 13 PostgreSQL-only skips, 70 browser cases, four image gates,
HTTP/worker smokes and full security scans.

The successful deployment used official Railway CLI 5.63.3 from the clean
`/tmp/milo-source-8b1ee2` export; all 266 exported files had SHA-256 identities
verified against Git before upload. API, Jobs, Retention and Web deployments
succeeded. The actual API Python build and pre-deploy migrations were confirmed;
both workers reached the exact schema head, and Retention reported zero-item
sweeps. Public health and dependency readiness returned HTTP 200 with private
SQL and shared Redis readiness. Empty-worker startup is not populated-workload
or live-delivery acceptance.
At 15:20 UTC, all six services reported successful deployments and running
instances, and both persistent volumes were ready.

Provider Git-source operations returned generic processing errors, and the
direct Git PAX archive upload failed snapshot creation. Those attempts were not
accepted release artifacts. The
successful official CLI generated its own upload archive. Its upload-byte
checksum was not measured and must not be equated with the failed Git archive's
hash. Native provider Git metadata is absent, so public `release_commit: null`
is expected; source provenance rests on the verified export, rather than a
fabricated provider commit field. [Hosted release evidence](benchmarks/railway-hosted-release-2026-10-07.json)
records the accepted source and check boundaries.

The latest request selects Railway. Its official API is reachable through the
supported network route, and the securely configured workspace token succeeded
with a Bearer-authenticated scoped project query. Earlier `Project Token not found`
and personal `me` authorization errors used the wrong token scope; they did not
prove the credential invalid. Workspace tokens cannot query personal `me` data,
and the CLI must use `RAILWAY_API_TOKEN` with `RAILWAY_TOKEN` unset and explicit
project/environment/service selectors. No credential value is stored in source.

Actual provider provisioning created a dedicated Milo project/environment and
Web, API, Jobs and Retention services. PostgreSQL 18.6 and Redis 8.2.10 deployments
reported success, with startup readiness verified from filtered provider logs,
private routing and ready persistent volumes mounted at
`/var/lib/postgresql/data` and `/data`. API migration and readiness evidence is
recorded above; managed backup recovery and direct SQL/Redis inspection remain
unverified. Redis startup settings specify AOF, every-second fsync, no eviction
and a 256 MB memory limit; runtime AOF state and recovery remain unverified.
A temporary SSH key was registered with
user authorization, then removed after `ssh.railway.com:22` refused the connection;
no direct database/Redis configuration result was obtained.

Hosted configuration reports Google sign-in unconfigured, model generation
disabled, external sends disabled and personal WhatsApp pairing unavailable.
Live provider replies, installed-device outcomes and 50,000-user capacity remain
unvalidated; the earlier larger local load stages failed availability.

The initial API deployment from exact source `72df83c1cc05e4b3c707c1bf7747b3f90c369af7`
failed during `BUILD_IMAGE`, before process startup or Alembic migrations.
Railway staff confirmed the original bind/secret Docker build mounts are
unsupported. The mount-free `Dockerfile.web.railway` and
`services/api/Dockerfile.railway` subsequently passed cold builds and acceptance
checks in [GitHub run 37635560403](https://github.com/hemantsatishjadhav06-ai/whatsapp-history-ai-/actions/runs/37635560403)
for earlier source `8ea6d57633ef993241fe2af0b5f036d9d3f22d33`. That run passed
747 PostgreSQL cases, 734 SQLite cases with 13 PostgreSQL-only skips, 70 browser
cases, all four cold image builds, HTTP/worker smokes and full security scans.

The latest `8b1ee2da` CI run reports these Railway image findings:

| Railway image scan | Reported findings | Critical | High | Fixable HIGH/CRITICAL gate |
| --- | --- | --- | --- | --- |
| API | 265 | 2 | 53 | PASS; zero reported fixable HIGH/CRITICAL findings |
| Web | 236 | 1 | 50 | PASS; zero reported fixable HIGH/CRITICAL findings |

Unfixed image advisories remain. A passed fixable-finding gate does not mean
the image is free of vulnerabilities; scan results apply to their recorded
artifacts and vulnerability database snapshot.

That earlier Railway API build reported success, but its image contained the
Web runtime despite the stored API Dockerfile path and service variable. Its
pre-deploy migration command failed, and no API process startup or application
schema readiness was established. Railway also rejected explicit legacy
`railwayConfigFile` role assignment as deprecated and referred to
`.railway/railway.ts`. The legacy root Web manifest was moved to
`infra/railway-web.json` to prevent default Web settings from overriding another
role. New services use explicit current service inputs and
`RAILWAY_DOCKERFILE_PATH` values. The correction passed the newer `8b1ee2da`
CI run and official CLI deployment recorded above; source autodeploy stays off.

Web readiness validates any available Railway runtime commit metadata, checks the
private API through `/readyz`, and starts with one pilot replica. API startup
uses serialized migration locking; Jobs and Retention wait for the exact schema
head. All 38 proxy/readiness tests, 76 startup/release-helper tests (including the
16 existing schema-startup cases), strict
web TypeScript, manifest JSON/shell checks and the production Web build passed.
Actual standalone Web HTTP checks also verified liveness, the Milo page,
private API readiness and synthetic Railway metadata; a separate unavailable
private origin returned 503 without exposing its URL. Both owned processes were
removed. These are local results; they do not validate a hosted release or user capacity.
Railway's current documentation also prevents new services from opting into
legacy Config as Code; deployment must apply the corresponding current service
settings or Infrastructure as Code, as described in [the guide](railway.md).
The four non-secret service input objects were checked against official API
input-field names and types. Provider creation is now recorded above;
configuration and upload remain explicit operations, rather than automatic
effects of the legacy manifests.

The previous exact-source [GitHub run 37617998643](https://github.com/hemantsatishjadhav06-ai/whatsapp-history-ai-/actions/runs/37617998643)
passed for `f852b1af64ade873439415a94ee752fc49cdca72`: 747 PostgreSQL cases,
734 SQLite cases with 13 PostgreSQL-only skips, 70 browser cases without retries,
cold image builds, HTTP/worker smoke checks and security gates. Deployment was
skipped. Source `72df83c1cc05e4b3c707c1bf7747b3f90c369af7` subsequently passed
GitHub validation, but its Railway API build failed as recorded above. The newer
`8ea6d576` run and its failed provider outcome are historical; `8b1ee2da` is the
tested application source for the successful hosted pilot recorded above.

## 7 October Render and fresh-owner increment

These are recorded local-source results for this earlier increment. The hardening results below
remain evidence for their recorded source and workload; capacity and restore
tests were not repeated for this increment. Every identity, history file, model
response and outbound transport in these checks is synthetic. Real Google,
WhatsApp and model calls were zero.

| Check | Result | Evidence and boundary |
| --- | --- | --- |
| Complete SQLite backend | 734 passed, 13 PostgreSQL-only skips; 241.05s | `.local/render-full-sqlite.log`; five deprecation warnings |
| Complete PostgreSQL backend | 747 passed, no skips/errors; 635.17s | `.local/render-full-postgres.log`; isolated database removed; five deprecation warnings |
| Business operator binding | 22 new regressions; focused 121-case suite passed | Create/verify, current outbound authority and both signed-ingress resolution phases reject an unbound owner, including old rows; overlaps full backend suite |
| Render release and startup helpers | 76 cases passed | Exact-SHA preflight, deployment ordering, lost acknowledgments, response/time bounds, checkpoint metadata, isolated SQLite migrations and schema waiting; provider responses simulated; overlaps full backend suite |
| Actual PostgreSQL startup | PASS | Two separate migrators blocked on the exact lock, eight revisions applied once, pre-schema waiter completed at exact head, repeat applied zero revisions and disposable database removed; [startup evidence](render-startup-smoke.md) |
| Browser proxy and readiness | 34 cases passed | Nine new health tests included; invalid origins, redirects, timeout/error, concurrent probe sharing, bounded health cache and content/credential privacy |
| Fresh-owner export onboarding | Six focused browser executions passed; 26.2s | Contact/group setup, read/retain and optional draft/learn choices, owner/date/timezone preview, import and partial-permission retry; send/share remain false; overlaps full browser run |
| Full desktop/mobile-web acceptance | 70 passed, no retries or skips; 2.3m | `.local/render-browser-final.log`; real SQL fixture and final production Next build, all existing privacy/owner/control cases included |
| Workspace TypeScript and web build | PASS | Final strict workspace check and production web build; native graph/export remains the prior unchanged-source result below |
| Fresh production API image | PASS, MOCK_ONLY transport | `bec67b6124de221ae3b978e37fe934f0412248e1503af500257e06142d3958b2`; UID10001, installed module/source proof, actual HTTP import/style/approval/deduplication/Pause, network-disabled startup and both Render workers |
| Fresh production web image | PASS, MOCK_ONLY identity/provider | `2edb6757ce7f269710c5de359c647be402cb80285118ca5d476724ca71844e3e`; verified CA bootstrap, actual HTTP assets/auth/proxy, `/readyz` and synthetic commit exposure; removal of only its temporary backend schema changed readiness to 503 |
| Exact-image vulnerability scans | OPEN findings retained | API 264 OS rows including two critical plus one medium vendored Rust finding; CA-fixed Web 236 OS rows including one critical. Python/Node runtime package findings zero; no available Debian12 fixed versions in these scans. [Container evidence](render-container-validation.md) |
| GitHub workflows | Local actionlint/Ruff checks PASS | Full reusable tests, image audits/SBOMs and exact-source Render release configured; remote run outcomes are recorded separately |
| Completed GitHub validation | PASS for `a703e9b8903c0110e9534b30a2070bd9cb40eb6f` | [Run 37616800533](https://github.com/hemantsatishjadhav06-ai/whatsapp-history-ai-/actions/runs/37616800533): 747 PostgreSQL, 734 SQLite/13 skips and 70 browser cases; cold builds, HTTP smokes, workers, audits, full scans/SBOMs and fixable HIGH/CRITICAL gate passed. Deployment skipped. Later Blueprint/checker changes require their own exact-SHA run |
| Official Render Blueprint schema | PASS, zero errors | Published Draft 2020-12 contract; [schema evidence](benchmarks/render-blueprint-schema-validation.json). Legacy database plan/version fields corrected and negative fixtures rejected; authenticated provider semantics and creation remain unrun |
| Render deployment and live URL | NOT_RUN | API is reachable but protected Render authentication and provisioned service IDs are absent; Blueprint provider validation and hosted readiness are unverified |
| Live replies and 50,000 simultaneous users | NOT_VALIDATED | Personal WhatsApp syncing and production general-action transport remain unimplemented; larger prior load stages failed availability |

The new UI tests exposed and fixed disappearing fresh-owner setup, stale import
previews and a completion notice lost during navigation. Test-only CSRF calls
were corrected to use the browser's current rotated token before the six-case
pass. Failed runs were retained separately. The first startup-smoke observer
missed a rapidly rolled-back query; it was replaced by a constant missing-schema
marker, then the actual separate-process check passed with full cleanup.

The first API-container check exceeded its original 7.5-second readiness window.
That harness removed the failed container before retaining its logs, so its
cause is unproven. Subsequent bounded checks completed the full HTTP journey;
an instrumented run observed readiness at 4.061s with no runtime error. The
tracked harness now uses a 30-second monotonic startup deadline and exact 200.
This result does not measure production throughput.

The first exact-SHA GitHub release run also reproduced a Web build portability
defect: Node slim lacked the system CA bundle needed for HTTPS APT. A public
trust bundle from the pinned official Python image now bootstraps verified
HTTPS before installing managed `ca-certificates`; TLS and signed indexes remain
required. The corrected Web image passed local HTTP acceptance and a fresh scan.
Its OS rows increased from 222 to 236 with the added certificate/OpenSSL packages;
that change is not described as advisory removal. That first GitHub run passed
747 PostgreSQL, 734 SQLite and 70 browser cases, but failed overall on the Web
build and skipped deployment. [Actions evidence](github-actions.md) records the
failed attempt and the required new exact-SHA verification separately.

A [redacted current regression summary](benchmarks/render-release-regressions.json)
records source hashes and local evidence. GitHub builds and Render builds are
separate artifacts; the same source SHA alone does not prove identical images.

## Prior 7 October security, storage and capacity increment

This section records the earlier published hardening. All
content, identities and transport replies were synthetic. Provider and model
calls were zero; actual PostgreSQL, Redis, HTTP and container execution are
identified separately.

A [redacted regression summary](benchmarks/hardening-regressions.json) preserves
its recorded counts and boundaries in Git. Raw local logs are ignored; separate
focused runs overlap the full suites and must not be added to their totals.

| Check | Current result | Scope and artifact |
| --- | --- | --- |
| Full SQLite backend | 636 passed, 13 PostgreSQL-only skips; 280.69s | `.local/backend-full-sqlite-final.log`; one existing Starlette/httpx deprecation warning |
| Full PostgreSQL backend | 649 passed, no skips/errors; 494.95s | `.local/backend-full-postgres.log` and JSON manifest; separate-process events, job/action claims, quota/control races and stale receipts included |
| Request/certificate/integration boundary | 43 passed; 6.73s | `.local/hardening-boundary-rerun.log`; streaming limits, depth/framing, sanitized errors, control reserve and certificate cache |
| Shared Redis limits | PASS | Two independent clients, exact atomic source/session/global/control budgets, hashed expiry, signed-source checks and outage rejection; [evidence](request-limits-smoke.md) |
| Encrypted PostgreSQL backup/restore | PASS; 5,001 messages, 37 tables, 36.690s | `.local/storage-reliability.json`; encrypted/plaintext marker check, exact restored digest, wrong-key rejection, suppression, held jobs and replay; disposable databases/backup removed |
| Schema | Eight migrations through `86b7bbad6fc1`, repeatable upgrade/alignment PASS | Local PostgreSQL and restored databases; measured authentication expiry indexes |
| Locked dependencies and client contracts | PASS | Installed Node package versions match the lock; cryptography 50.0.2; generated OpenAPI/TypeScript, strict workspace typechecks, 20 shared contracts, 25 proxy, seven Tools privacy and 16 native helper tests |
| Reply and HTTP bridge | MOCK_ONLY: PASS | Latest disposable demo and Python–Node–SQL text/quote/reaction/forward bridge; four durable attempts, duplicate preservation and forged-recipient rejection; `.local/hardening-demo.log`, `.local/hardening-bridge-smoke.log` |
| Native exports | PASS, Android/iOS/web | Current locked graph; exact artifact hashes and unrun installed-device gates in the native device report |
| Capacity | 8/24-user workloads PASS; larger stages FAIL availability | 1,280 validated requests; paced128/burst64 include explicit 503s and recovery; [complete measurements](LOAD_TEST_REPORT.md). 50,000 remains NOT_RUN |
| Fresh API runtime | PASS | Immutable image `b9f6c1a528f96decd6761ad74260936ab6e20450163e25c114cdb3526628a89c`; UID10001, runtime port, IPv4/IPv6, overload recovery, clean shutdown; both private worker entrypoints exit0 at migration eight |
| Fresh API container functional HTTP | MOCK_ONLY: PASS | Same immutable image; import/reimport, scoped owner style, exact approval, idempotent mock dispatch and Pause; `.local/hardening-api-container-smoke.json` |
| Fresh standalone web container | MOCK_ONLY: PASS | Immutable image `bab173cfa2eb425c2e30092bfad18cad109d5c1ab3d3ef434de08bbc1e9b6a70`; actual HTTP assets, private proxy, authenticated snapshot, nonce/cookie/CSRF/origin and route-denial acceptance; final source includes proxy admission and Tools privacy |
| Current-build desktop/mobile-web acceptance | PASS: 64, zero failures/skips, retries disabled | Final Next build after proxy cap and admission; `.local/hardening-browser-admission-final.log`. Includes actual SQL Tools Forget plus a delayed private GET, unchanged edit preservation and raw-history distinction |
| Known dependency/container advisories | OPEN | Current Python/web production/gateway package audits: zero known advisories. Native graph: 29 affected nodes. Exact API/web images: 264/222 OS rows, including two/one critical; API vendored Rust: one medium. No available Debian12 package fixes in the scan; unresolved findings are retained in the [audit](dependency-audit.md) |
| CI configuration | UPDATED, remote execution not claimed | Added PostgreSQL/Redis backend job and the focused Tools privacy command; YAML parsed locally. GitHub runner results require their own execution evidence |

The first full runs found two malformed liveness-header failures, fixed before the
passing reruns. A VFS Docker build then exhausted the workspace disk and interrupted
PostgreSQL/browser checks. Those failed logs were retained; they are not counted as
passes. Verified rollback image archives and removal of task-owned build caches
recovered 13 GiB. Dockerfiles now avoid repeated dependency snapshots and keep TLS,
signed indexes and frozen locks. Current image/browser results are finalized below.

Real WhatsApp pairing/history/phone coexistence and native general-action delivery
are still unavailable. General production action dispatch is explicitly blocked;
real model reply quality, Google sign-in and installed-device journeys remain unrun.
See [security and actual reply scope](SECURITY_REVIEW.md) before enabling a live account.

## Prior backend baseline

These results predate the new CTO handoff increment and do not verify its new code.

| Check | Recorded result | Scope |
| --- | --- | --- |
| Python suite, SQLite fixture mode | 278 passed | Synthetic API, policy, parser, auth, privacy, tasks, automation and workers |
| Python suite, PostgreSQL fixture mode | 278 passed | Dedicated disposable test database; parser/unit-only tests remain local |
| Node typecheck and contract tests | 18 passed | Isolated connector simulation; no provider socket |
| Alembic migrations | Four revisions applied/repeated and schema check passed | SQLite/PostgreSQL; populated private-context upgrade tested |
| Running local API HTTP smoke | Passed | PostgreSQL API; synthetic import/style/draft/approval/mock-send/tasks/pause/delete |
| Docker container smoke | Passed | Built non-root image, SQLite migrations and loopback API acceptance |
| Kafka integration smoke | Passed | Actual local broker publish/consume and SQL acknowledgment; metadata only |
| Temporal recovery smoke | Passed | Actual local worker killed before due and restarted after due; one mock send attempt using disposable SQLite |
| Load smoke | 100/100 successful | ASGI/SQLite, concurrency four; see [load report](LOAD_TEST_REPORT.md) |

## CTO handoff increment

| Final check | Recorded result | Scope |
| --- | --- | --- |
| Python suite, SQLite fixture mode | 495 passed in 167.43 seconds | Entire current backend suite; one Starlette/httpx compatibility deprecation warning |
| Python suite, PostgreSQL fixture mode | 494 passed in 411.95 seconds | Full run before the final audit-paging regression was added; one compatibility deprecation warning |
| Final PostgreSQL retention suite | Seven cases passed in 6.89 seconds | Includes the final bounded audit-deletion regression; overlaps six cases from the full run |
| Distinct PostgreSQL cases covered | 495 | Full run plus focused retention run; not one 495-test command |
| Node typecheck and tests | 33 passed | Legacy contract plus authenticated native-action mock gateway |
| Python–Node–SQL bridge smoke | Four operations, four durable attempts passed | Actual local HTTP; text, quote, reaction, forward, duplicate suppression and forged-recipient rejection; zero WhatsApp provider calls |
| Alembic/bootstrap | Five revisions, schema alignment and frozen bootstrap repeat passed | SQLite/PostgreSQL development checks; existing data/secret configuration preserved |
| Ruff and whitespace checks | Passed | Backend, tests, scripts and migrations; no source committed/pushed |
| Running local API/demo | Passed | PostgreSQL loopback HTTP and disposable synthetic walkthrough |
| Rebuilt non-root container | Passed | Latest image, migrations and container HTTP walkthrough, including final audit-paging fix |
| New worker entry points | All three `--once` runs passed | Action planner, authorized jobs and application retention process startup |
| Latest load smoke | 100/100 passed | Concurrency four during concurrent tests/build; detailed limits in [load report](LOAD_TEST_REPORT.md) |
| Updated Kafka/Temporal integration rerun | Passed | One actual broker publish/consume with SQL acknowledgment and no private body; Temporal worker killed before due/restarted after due produced exactly one accepted mock send attempt |
| Runtime at this prior increment | Running, initial worker logs clear | API version 0.2.0 plus persistent actions/jobs/retention workers; built-in mock transport, no persistent Node gateway or provider session |

Mock acceptance now covers six-operation policy, authentic records, selected-chat grants,
forwarding routes, reaction attribution/meaning, local contacts, authorized jobs, bridge
authority, scoped commands, model/action budgets and pause/expiry behavior. No provider
account or real model-quality gate is closed by those results.

Critical acceptance cases:

- Wrong owner/account/chat/recipient/source/route is blocked before retrieval and submission.
- History/backfill/replay does not initiate automatic communication.
- A logical turn produces at most one reply/quote/reaction, including replanning and restart;
  separately granted forwards deduplicate for each exact destination.
- Native targets are authentic, scoped, unchanged, undeleted, unexpired and available.
- Source/destination takeover or route revocation invalidates a pending forward.
- Reaction frequency does not override unsafe meaning; fresh human reactions handle their
  targets, while assistant echoes do not become human evidence.
- Contact destinations and exact identities remain distinct; unsupported external writes
  cannot be reported as local-save success.
- Global pause acknowledges committed state, prevents new external submission and preserves
  schedules; resume rechecks expiry/current authority and does not replay canceled actions.
- Accepted-but-unacknowledged sends remain uncertain and do not permit blind retries.
- Prompt content/model references cannot grant access or forge recipients, routes or jobs.
- Forget/delete blocks active retrieval and pending action evidence; replay cannot resurrect it.

Run the final checks from the repository root with `make test`, `make lint`,
`make gateway-check`, `make demo` and Alembic upgrade/schema checks. PostgreSQL fixture
tests must use a dedicated disposable `TEST_DATABASE_URL`; tests drop fixture tables.
Do not point them at the development or production application database. Provider
credentials, conversation content and token values must stay out of test reports.

## Handoff acceptance map

| Handoff cases | Repository evidence boundary |
| --- | --- |
| AC01, AC02, AC26, AC27 | Auth/scope/ref-substitution negatives; new command/route/job negatives in final increment |
| AC03 | Planned: actual eligible account and primary-phone journey required |
| AC04, AC05 | Import coverage, provisional style, history/replay exclusion; new native backfill cases included |
| AC06, AC07 | Scoped human-owner provenance and statistics tested; held-out voice preference remains unmeasured |
| AC08, AC32 | Suppression, deletion and replay negatives; actual backup restore remains Planned |
| AC09–AC12, AC17–AC21, AC34 | Native/action/contact synthetic suites passed; actual provider/device operations unverified |
| AC13–AC16 | Revision/epoch/pause/lease and submission-boundary race tests; real phone observation lag remains unmeasured |
| AC22, AC23 | Uncertain-result ledger and local recovery tests; real provider reconciliation/distributed actor handoff unverified |
| AC24, AC25 | Job timezone/DST, bounded recurrence, holds/expiry/quotas and model-failure synthetic suites passed |
| AC28, AC29 | Milo frontend now enabled; browser, configured speech and installed-device acceptance require separate results |
| AC30 | Deterministic Business opt-in/window and unavailable-route blocks; live policy/eligibility pending |
| AC31, AC33 | Capacity/fairness targets remain Planned; small smoke is not target-scale evidence |

New feature tests are in `tests/test_native.py`, `tests/test_actions.py`, `tests/test_jobs.py`,
`tests/test_people.py`, `tests/test_companion.py`, `tests/test_lifecycle.py` and gateway
`tests/action-bridge.test.ts`, alongside the prior regression suite. Schema/HTTP bridge
contract evidence must include the latest source, not only independently tested adapters.

## Release gates still open

Real owner sign-in, eligible account pairing/coexistence, native provider operations,
actual phone-contact destinations, held-out personalization, distributed crash/lease
reconciliation, restore/privacy lifecycle, production isolation audit and staged capacity
must have separate evidence. A passing mock suite does not close those gates.

## Prior Milo client and publication baseline

These results predate the current security/storage/capacity increment at the top
of this report. The request added interactive Next.js web and Expo/React Native clients. The
canonical M01–M36 inventory, all 60 TEST cases and 31 UX cases are mapped in
[TEST_PLAN.md](TEST_PLAN.md) and [MOBILE_PARITY_REPORT.md](MOBILE_PARITY_REPORT.md).
Required cases are not automatically passed by a successful build or a rendered button.

| New check | Current result | Evidence boundary |
| --- | --- | --- |
| Client workspace frozen install/typechecking | PASS | Locked install and strict typechecking across workspaces; Node 24.19.0/npm 11.9.0 |
| Shared contract tests | MOCK_ONLY: 20 passed | Final 7 October rerun; `.local/milo-contract-tests.log` |
| Web proxy tests | MOCK_ONLY: 10 passed | Configured origin, unsafe methods, internal-route denial, bounded streaming ingress, nonce cookie and authorized lookup; `.local/milo-proxy-tests.log` |
| Native helper tests | MOCK_ONLY: 16 passed, zero failures/skips | Runtime session/storage/client helpers, including same-workspace private-result reconciliation, stale-owner/read generations, ordered secrets, safe origins/paths and no mutation retry; `/tmp/milo-native-unit.log`; not installed OS capability |
| Next.js production build | PASS | Final production build; no live integration or hosted URL inferred |
| Desktop/mobile-web browser acceptance and accessibility | PASS: 62 passed, zero failures/skips in 1.3 minutes | 31 desktop and 31 mobile cases; seven axe-audited surfaces per project, 320px checks, synthetic operations, SQL onboarding/pause/logout, and delayed revoke/forget private-result regressions; `.local/milo-browser.log` |
| Focused native/session/UI API regression | MOCK_ONLY: 108 passed in 42.61s | SQLite fixture run on 6 October 2026 at 14:48:01 UTC; one known Starlette/httpx warning; Google verification mocked |
| Expo JavaScript exports | PASS | Final all-platform export after private-result fix; `/tmp/milo-native-export.log`, strict native check `/tmp/milo-native-typecheck.log`; distinct from signed development/preview installation |
| Non-root standalone web container | PASS | Exact image `sha256:ffd052cd0a0448bcbc653f69000217ff6663e4aef7be8741bbde98630c9dcdaa`; 38 input files matched; assets, private proxy, snapshot, nonce path, CSRF/origin and route denial passed; `.local/web-container-smoke.json`; zero provider calls |
| Selected web production dependency audit | PASS: zero vulnerabilities | Frozen-lock `npm audit --omit=dev --workspace=@milo/web --workspace=@milo/contracts --include-workspace-root --json`; `.local/web-production-dependency-audit.json`; separate from full native/workspace graph |
| Full workspace dependency audit | FAIL/open: 36 findings, 21 high and 15 moderate | `.local/milo-npm-audit.json`; native/Expo/Metro/Jest-related build chains remain under review; selected web audit does not clear this graph |
| Installed iOS/Android daily loop and lifecycle | NOT_RUN | OS/device/build and physical permission evidence required |
| Real Google OAuth/pairing/model/speech/push/contact writes | BLOCKED_EXTERNAL | Registered accounts/provider/signing configuration absent |
| GitHub publication | NOT_RUN | Commit and actual remote result required |
| Railway build/health/final HTTPS browser smoke | NOT_RUN | Actual access and final URL evidence required |

| New full backend run | Recorded result | Artifact / boundary |
| --- | --- | --- |
| SQLite | 533 passed in 228.91s; one known compatibility warning | `.local/milo-backend-sqlite.log`; disposable synthetic fixtures |
| PostgreSQL | 533 passed in 589.73s; one known compatibility warning | `.local/milo-backend-postgres.log`; dedicated disposable fixture database |
| Alembic | Seven revisions through `05439876fc2e` applied; alignment passed | New native identity and rotating refresh revisions; no live provider evidence |
| Saved installer reproduction | PASS | Final `bash scripts/dev-bootstrap.sh`: frozen Python/root npm/gateway npm locks, PostgreSQL/Redis health and seven migrations; no existing secret configuration overwritten |
| Final non-root API image | PASS | Version 0.3.0, UID/GID 10001, image `sha256:c599ad2e403d19b132aaabd0a219a12a434766965f2aa693681b9ff6095005b0`, 45 unchanged build inputs; migrations/readiness and HTTP import/dedup/style/approval/idempotent mock dispatch/Pause passed; `.local/api-v03-container-smoke.json`; zero external calls |
| Refreshed persistent local API/workers | PASS | API OpenAPI version 0.3.0, both health checks, independent action/job/retention workers restarted; synthetic HTTP workflow and cleanup passed in `.local/milo-http-smoke-final.log` |
| Contextual Catch me up follow-up | MOCK_ONLY: 20 SQLite cases in 7.49s and 20 PostgreSQL cases in 21.05s | Exact conversation filtering after the full 533-case runs; separate commands, not a new full-suite total |
| Final privacy regression bundle | MOCK_ONLY: 77 SQLite cases in 19.61s and 77 PostgreSQL cases in 59.59s | 7 October 2026; one known warning each; catchup, draft deep resolution and derived-evidence suppression; `.local/ui-privacy-focused-sqlite.log` and `.local/ui-privacy-focused-postgres.log` |

Read-only audit identified delayed private-snapshot resurrection across logout/session
switch, asynchronous native secure deletion/save ordering, fresh-owner zero-workspace
handling, discarded conversation cursors and small-text contrast. Fixes were included in
the final local checks above. The 62-case browser run supersedes the initial 42-case run
with nine failures; accessible names, lilac text contrast and a duplicated Pause test
locator were corrected. Installed-device and complete large-inbox behavior remain separate
gates. Recovery limitations are described in
[RELIABILITY_AND_RECONCILIATION.md](RELIABILITY_AND_RECONCILIATION.md).

Two synthetic Home screenshots were visually reviewed against the supplied PDF:
`.local/milo-home-desktop.png` and `.local/milo-home-mobile.png`. They demonstrate the
implemented warm/lilac hierarchy and desktop/mobile-web navigation, not native installation,
WCAG certification, provider connection or delivery. Final screenshots should follow the
latest successful browser run.

The native unit, strict TypeScript and final all-platform export completed on 7 October
2026 at 07:34:38, 07:34:50 and 07:35:08 UTC respectively. Artifact sizes/hashes, exact
runtime and the unrun installed-device matrix are in
[native device evidence](../apps/mobile/NATIVE_DEVICE_TEST_REPORT.md). Native implementation,
storage, OS permissions and release steps are linked from
[native README](../apps/mobile/README.md).

The 108-case focused command was
`UV_CACHE_DIR=/workspace/.cache/uv uv run pytest -q tests/test_mobile_auth.py tests/test_assistantui.py tests/test_auth.py tests/test_migrations.py tests/test_intelligence.py tests/test_lifecycle.py`.
Python 3.12.14 ran from the uncommitted working tree; no commit existed at the time.
The redacted result is `.local/ui-auth-focused-tests.log` (ignored local artifact).
`TEST_DATABASE_URL` was unset, so fixture databases were disposable SQLite. Seven local
PostgreSQL Alembic revisions through `05439876fc2e` applied and schema alignment passed;
both new full 533-case SQLite/PostgreSQL runs subsequently passed. The earlier
106-case run predates the final two protected-resolver cases and is superseded by 108.

The 20-case catchup command was `UV_CACHE_DIR=/workspace/.cache/uv uv run pytest -q tests/test_companion.py` for SQLite and `.venv/bin/pytest -q tests/test_companion.py`
with `TEST_DATABASE_URL` selecting the dedicated disposable `assistant_test` database for
PostgreSQL. The earlier output was recorded in tool transcripts without a persistent log.
The later bundle ran `pytest -q tests/test_assistantui.py tests/test_intelligence.py tests/test_companion.py`, with the default disposable SQLite fixture and then the dedicated
PostgreSQL fixture selected through `TEST_DATABASE_URL`. Both 77-case runs provide ignored
artifacts. The current suite collects 540 cases, but no full 540-case run was performed;
the recorded evidence remains full 533-case runs plus focused 77-case follow-ups. The
final 62-case browser run includes the four added desktop/mobile revocation and forgetting
regressions. The final 16 native helper cases include private-result reconciliation and
supersede the earlier 11-case run.
