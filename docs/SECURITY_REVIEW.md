# Security review and live reply release boundaries

Review date: 7 October 2026. Baseline source was published commit
`4c9be7357bf2bb5df9cbcedce6dbd9ea090466a0`; the hardening reviewed below is a later
working-tree increment. Reproductions used temporary SQLite databases, synthetic input,
local ASGI requests and no provider calls. Severity describes plausible application
impact, not a claim that a remote production exploit was observed.

This is a bounded code review and regression record. A dependency audit reports known
advisories for its exact graph. Neither that audit nor passing tests proves that every
vulnerability has been found, or that a 50,000-account service is production-ready.

## Findings and remediation evidence

| ID / severity | Reproduction or affected boundary | Concrete remediation / current evidence |
| --- | --- | --- |
| SR01 High: public request resource exhaustion | Ordinary unauthenticated requests could buffer about 12 MB before endpoint authentication, without a stream deadline or process admission bound | New ASGI boundary admits before body/SQL, uses a 64 KiB ordinary cap, exact import/webhook exceptions, bounded headers and read deadline. Chunk overflow stops consumption; a stalled stream returns 408 without endpoint SQL. Local regressions passed; production edge/process sizing is separate |
| SR02 High: authentication work and nonce growth | Twenty-five `/auth/nonce` requests with cookies cleared returned 200 and created 25 durable challenge rows. Configured Google exchanges performed remote certificate work without a local attempt bound | Source/global issuance and exchange limits now precede endpoint SQL and share `/v1` aliases. Two allowed issues followed by 429 created exactly two rows in the regression. A certificate-only cache uses verified HTTPS, a five-second timeout, 64 KiB response bound, provider cache expiry and outage backoff. Thirty simultaneous verification transports share one certificate fetch; expired certificates are never reused during an outage. Six transport regressions passed. Real Google sign-in remains unrun |
| SR03 High: rate-limit identity spoofing | An initial limiter used arbitrary presented cookie/bearer hashes as ordinary API buckets. Rotating fake values bypassed a per-session limit while `/me` still queried SQL | Independent source counters now supplement claimed-session counters. Rotating invalid bearer and cookie regressions return 401, 401, then 429. Unsigned forwarded-IP rotation cannot establish source identity. These tests used memory counters; shared Redis and deployed proxy behavior need separate evidence |
| SR04 High: ordinary rate exhaustion blocked Pause | After the ordinary global API budget exhausted, a valid owner Pause returned 429 despite free reserved request slots | Control admission and rate budgets are separate. Tests commit actual owner Pause while an ordinary request is stalled, and after ordinary global rate exhaustion. This does not promise control completion during unavailable SQL/Redis or an already-started provider call |
| SR05 Medium: deeply nested JSON caused 500 | A roughly 4 KB unauthenticated JSON body nested 2,000 levels caused an unhandled server error | JSON depth is bounded before decoding; malformed/deep JSON receives generic 400. Tests at 65 and 2,000 levels confirm no endpoint SQL. Error bytes do not echo private input |
| SR06 Medium: validation echoed private input | An oversized credential and extra private fields were reproduced in a 422 response's `input` fields | Validation output keeps only location/type/message, at most 20 errors; strips input/context. A marker-bearing credential/private-field test confirms the marker is absent from the response |
| SR07 Medium: unsafe production configuration | `Settings.prepare()` accepted a negative browser TTL, negative import limits and a public HTTP origin | Bounds now validate session/import/admission/rate/pool settings. Production requires exact HTTPS origins, shared Redis limits, secure sessions, managed encryption and PostgreSQL; rejects local limiter modes. Configuration regressions passed without creating a production database connection |
| SR08 High: unbounded tenant scans and cleanup | Legacy lists scanned all workspace resources; export iterated every conversation and up to 20,000 messages each; deletion collected all source IDs and private rows in one transaction | Owner lists now default to 100 rows with a 200-row maximum. Synchronous export/erasure checks 200 conversations, 20,000 relevant rows and 32 MiB encrypted payload before decryption/mutation. Oversized work returns stable 413 without partial erasure. See [storage admission](storage-admission.md); a durable workflow for larger operations remains unimplemented |
| SR09 Medium: expired authentication metadata custody | Expired native/browser sessions and encrypted device names had no periodic cleanup; challenge issuance performed broad expiry deletion | The retention worker now deletes bounded expired metadata batches. Issuance purges at most 100 expired challenges. Refreshable native credentials remain until their refresh deadline. The eighth migration adds measured expiry indexes; see [query profile](auth-expiry-profile.md). Production must run the retention service |
| SR10 Release gate: dependency advisories | Historical audits covered different native/workspace and production graphs, with different counts | Cryptography is now locked to 50.0.2; the current Python production/all-dependency audits report zero known advisories. Current Node workspace retains 29 affected dependency nodes (21 high, eight moderate); selected web production and gateway graphs report zero. Fresh API retains 264 OS advisory rows, including two critical, plus one medium vendored Rust advisory. Native/build and container findings remain open; see [exact audit scope](dependency-audit.md) |
| SR11 High: concurrent submission and schedule state | Overlapping workers could finalize recurring jobs twice; a second live submission could incorrectly mark an active attempt uncertain; overlapping event flushes could escape duplicate reconciliation | Short PostgreSQL workspace advisory transactions serialize SQL authority/admission/claims/finalization across processes. Provider/model network work stays outside those locks. Active submission is preserved, timed-out submission becomes uncertain, and uncertainty does not permit blind retries. Separate-process regressions and restore drills complement unit tests; they do not certify the mock gateway as a distributed live session actor |
| SR12 Medium: stale private Tools details | Same-owner snapshots could remove a memory or source while an open modal retained its old row/detail, including a delayed GET response | Modal rows, private results and outstanding GETs are fenced to the authorized snapshot version and re-resolved after a change. Seven focused regressions passed. A real SQL/browser regression verifies that unchanged polling preserves an edit, Forget closes the memory, and a late source response cannot reopen it while raw permitted history remains |
| SR13 Medium: oversized ordinary browser-proxy bodies | The browser proxy used its import-sized cap for ordinary JSON before backend authentication | The proxy now limits ordinary JSON to 64 KiB; only exact import and import-preview routes admit up to 12 MiB. The final 25-case proxy bundle includes rejection versus the explicit import exception. Provider ingress still needs deployed evidence |
| SR14 High: web buffering before API admission | Concurrent slow uploads could allocate web-process buffers before reaching API request limits | The proxy admits before body reading/upstream work: 32 process slots, 28 ordinary/four controls, two imports inside the ordinary budget. A lease lasts through response consumption, cancellation or deadline. Held-request, import, control-reserve, error/abort/deadline cleanup and recovery regressions passed in the 25-case bundle. Limits are per web process; the final guard was not capacity-rebenchmarked |
| SR15 High: shared Business credential claim | An authenticated first owner could attempt to claim a deployment-wide configured Business number; old unauthorized connector rows could also reach outbound or signed-ingress paths | Exact verified Google subject binding now precedes Cloud create/verify and protects outbound final authority plus both signed webhook resolution phases. Blank binding denies Cloud access; mock/export paths remain separate. Twenty-two new cases and the 121-case focused core/messaging/webhook/automation suite passed with simulated provider responses. See [operator binding](business-owner-binding.md) |

The later Render increment also adds coarse Web readiness with a three-second
private API probe, shared concurrent requests and a maximum five-second metadata
cache. Nine focused regressions check errors, redirects, origin validation,
credential privacy, cache sharing and release SHA formatting. This is health
metadata, never an owner permission or provider-delivery result.

## Verified request-boundary run

Command: `UV_CACHE_DIR=/workspace/.cache/uv uv run pytest -q tests/test_request_hardening.py`.
Result: **22 passed in 1.95 seconds**, one existing Starlette/httpx compatibility warning.
Artifact: `.local/request-hardening-tests.log`; Ruff and file whitespace checks passed.

The tests exercise the assembled application, actual synthetic owner/SQL Pause, streaming
requests and production configuration guards. Their temporary fixture databases are SQLite
even when a broader invocation sets `TEST_DATABASE_URL`. They test memory limiter behavior;
they do not run Redis Lua, real OAuth, a physical device or a deployed HTTP edge.

The earlier assembled request/certificate/integration bundle passed **43 cases in
6.73 seconds**, including malformed Content-Length on liveness. Liveness avoids
SQL/Redis admission but still rejects malformed framing. That full SQLite
run passed **636 cases with 13 PostgreSQL-only skips in 280.69 seconds**. See QA
for its **649-case PostgreSQL pass in 494.95 seconds**. The subsequent Render/operator-binding
increment passed **734 SQLite cases with 13 skips** and **747 PostgreSQL cases**,
plus 70 desktop/mobile-web browser cases without retries. Current exact-image
results and all remaining findings are in [Render container evidence](render-container-validation.md).

The [real Redis smoke](request-limits-smoke.md) separately passed with two
independent limiter clients. Concurrent shared source, actor, nonce and global
budgets admitted exactly their configured counts; ordinary budget exhaustion
preserved independent control capacity. Fifty-two counters were hashed and
expiring. A Redis outage returned 503 before application dispatch. This proves
local shared counters, not Railway ingress trust or target throughput.

Default admission is 32 active requests including four reserved control slots, with
48 AnyIO thread tokens to retain capacity for dependency cleanup. Default
ordinary request body size is 65,536 bytes, body read deadline ten seconds and JSON depth
64. Import/webhook routes have separately bounded payloads. These are configurable
guardrails, not measured capacity. Rate counters expire, store hashed identities and fail
closed when their configured backend is unavailable. Per-process admission must be sized
with actual replicas, SQL pools, edge limits and workload; it is not a distributed account
actor lease. Forwarded identity requires the explicitly configured signed proxy contract.

## Actual reply and integration scope

| Requested outcome | Actual implementation | Missing implementation or independent evidence |
| --- | --- | --- |
| Connect a personal WhatsApp account, synchronize history and preserve the primary phone | Export ingestion and connector contracts exist; no personal-session adapter is shipped | Pairing/session persistence, history/echo/membership/expiry observation, reconnect/fence ownership and real phone coexistence are implementation and live-account gates; credentials alone cannot enable them |
| Receive/send eligible WhatsApp Business text | Signed webhook and separately gated legacy Business Cloud text adapter exist | Configured eligible number, live verification, opt-in/service-window and actual accepted/delivered/receipt trials; supported account scope remains contact text |
| General unattended personalized replies | General action contracts and SQL ledger exist; default planner emits conservative fixed acknowledgments, suitable learned-habit reactions and one meeting clarification | The current generic action lane rejects non-mock providers and blocks production dispatch. A real adapter plus evaluated scoped planner is required; this is not a credentials-only gap |
| Learn how the owner talks to each person/group | Verified human-owner style statistics, scoped rules and evidence-backed memories are implemented | Held-out owner preference, factual grounding and language/group/no-history quality have not been measured. Static fixture success is not personalized voice quality |
| Generate owner-invoked replies | Configured structured model proposal exists; default disabled/mock modes are explicit, proposals remain unsent and require exact owner review | Actual model-provider configuration/billing and held-out evaluation. Evidence-reference validation checks scope and provenance, not semantic truth of every generated claim |
| Quote, react and forward native messages | Authentic scoped source/route checks and four simulated wire operations exist | No live general native transport is connected; actual originals and audience/membership/receipt behavior need a supported provider adapter and permitted account trials |
| Proactive jobs and scheduled replies | Durable exact-owner job/schedule intent, timezone/DST/expiry/holds and idempotent attempts exist | External jobs depend on a supported live action adapter; local reminder records do not deliver push/email/device notifications |
| Save contacts to the phone, Google or WhatsApp | Assistant-local contact records and precise local-save authority exist | Destination-specific service/OS executors, grants and actual write receipts remain absent; a SQL save is not synchronization |
| One inbox for Gmail/Calendar/other social services and meetings | Provider-neutral boundaries and visibly Planned routes exist | Independent service adapters, OAuth grants, cursor/reconciliation, identity confirmation and capability evidence remain required |
| Voice, private push and installed mobile behavior | Native source/build export and typed commands exist; unavailable capabilities are explicit | Speech/push/Contacts adapters and signed physical-device auth, permissions, lifecycle and distribution tests remain unrun |
| 50,000 simultaneous customers | Bounded request/SQL interfaces and a new capacity harness are under test | Record the actual offered workload, transport, identities, active connections, success/overload counts, percentiles, resources and duration. Synthetic queued requests do not prove 50,000 live provider sessions or full product capacity |

The current source explicitly blocks a live general-action release rather than reporting a
simulated provider acceptance as delivery. Do not remove that gate to make a test green.
Closing it requires a supported live adapter, current-authority checks, durable uncertainty,
provider reconciliation and separately evaluated planner behavior.

## Remaining deployment evidence

Deployed proxy source authentication, managed SQL roles/storage/key custody,
production backup/tombstone restore, distributed live-session lease fencing,
provider eligibility and outward phone observation need independent evidence. Managed
volume encryption and application field encryption are different controls. Metadata is
not uniformly encrypted, and there is no completed production RLS or restore certification.

The final release record must use current source/build identities and new results rather
than inherit the previous 533-case backend, 62-browser or small ingestion smoke as proof
of this increment. See [QA](QA_REPORT.md), [capacity](LOAD_TEST_REPORT.md),
[security lifecycle](SECURITY_AND_DATA_LIFECYCLE.md),
[platform evidence](PLATFORM_CAPABILITY_MATRIX.md) and
[reconciliation](RELIABILITY_AND_RECONCILIATION.md).
