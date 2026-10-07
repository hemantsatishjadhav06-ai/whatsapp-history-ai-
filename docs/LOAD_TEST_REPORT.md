# HTTP capacity and correctness evidence

Evidence date: 7 October 2026. The tested single-process local configuration handled the
measured 8- and 24-user read workloads without errors. Larger workloads reached
its configured limits: the paced 128-user stage and 64-request burst returned
explicit 503 responses. **50,000 concurrent users, accounts, requests, or provider
connections have not been tested.**

These measurements used real TCP HTTP against Uvicorn and the production-built
Next browser proxy, a disposable PostgreSQL database, and the actual Redis request
limiter. No WhatsApp account, Google login exchange, external send, or model call
was involved. The older [100-request SQLite/ASGI smoke](load-test-results.md)
remains ingestion integration evidence; it is superseded for HTTP read capacity
by the measurements below.

## What changed and what was checked

The bounded UI contact preview queried source context and Forget tombstones for
each candidate. A fixture with 27 valid contacts required **108 SQL queries per
bootstrap**. Request-local batches now fetch source contexts, connectors, and
suppression metadata once; the same bootstrap requires **23 queries**, a 78.7%
reduction. The canonical validator still checks exact workspace/chat/connector,
sender identity, author/origin, deleted/expired sources, revision and Forget.
No client-supplied authority or cross-request permission cache was added.

The final contact projection reads the metadata needed to validate live/backfill
identity without decrypting message bodies or unrelated participant attributes.
History originals retain canonical native validation and can require additional
queries. The 23-query measurement covers the seeded live-record fixture.

The 61 focused tests passed, including 21 new capacity/privacy helper cases:
query count stays constant from one to 50 contacts; seven prefetched provenance
mismatches fail; an older Forget is not skipped behind 130 newer tombstones;
contact identity can be previewed without decrypting an unavailable message body;
HTTP overload is reported as failure; cross-owner/private-resource injections
fail the verifier; remote runners reject insecure origins, public session
manifests and duplicate identities. This is separate from the full backend suite.

## Measured 8/24-user workloads

Each virtual user had its own owner, workspace and hashed browser session. Its
sequential request mix was 60% bootstrap, 20% messages, 10% exact server-object
resolution and 10% another owner's workspace, which must return 404. Each user
attempted 20 requests, with no think time. This is a bounded closed-loop workload,
not a fixed-rate arrival test or a long soak.

| Path / stage | Attempts | Valid owner reads | Expected foreign 404s | Unexpected failures | p50 / p95 / p99, ms | Elapsed, s | Attempts/s |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| API, 8 users | 160 | 144 | 16 | 0 | 427.547 / 1276.951 / 1498.110 | 9.482 | 16.875 |
| API, 24 users | 480 | 432 | 48 | 0 | 851.272 / 1800.833 / 2231.555 | 17.821 | 26.934 |
| Browser proxy, 8 users | 160 | 144 | 16 | 0 | 318.945 / 509.391 / 554.915 | 5.585 | 28.646 |
| Browser proxy, 24 users | 480 | 432 | 48 | 0 | 825.727 / 1664.256 / 2135.035 | 17.363 | 27.645 |

All **1,280 invariant checks passed**: 1,152 valid owner reads and 128 expected
cross-owner denials. The HTTP peak in flight was exactly 8 or 24 for its stage.
The 32-owner dataset occupied 21,586,447 bytes; normal owners had 32 conversations,
the large-inbox owner had 2,500, and populated chats had three encrypted messages.
Denied/expired permissions, expired context, sender mismatch and Forget cases
were seeded and checked against the expected contact/message IDs.

This run followed the prefetch change, final bounded admission/thread settings,
and Redis activation. It preceded the final metadata-only contact projection.
The later 128/64 stages exercise that final projection. JSON source hashes
identify both implementations.

The earlier [baseline](benchmarks/http-baseline.json) used the same bounded contact
fixture before prefetch and final admission/thread settings, with request limits
off. Its direct 8-user p95 was 1571.586 ms and proxy 8-user p95 was 1302.075 ms.
The 24-user baseline aborted after 57 API requests (8 failures) and 98 proxy
requests (4 failures), all recorded 503s. The final 24-user stages completed
480 requests each with zero failures. Latency changes reflect the combined path;
they cannot be attributed solely to SQL batching or treated as an SLO guarantee.
The isolated query-count comparison is 108 versus 23.

The run order was API 8, API 24, proxy 8, proxy 24. Cache warm-up and scheduling
can affect comparisons between paths. In particular, the slower API 8-user p95
must remain in the result alongside the faster warmed proxy stage.

## Larger paced stage and deliberate overload

The 128-user stage used a two-second think time, an eight-second initial stagger,
and ten planned requests per user. The 64-user burst used no think/stagger time.
Stages stop after at least the configured VU sample count when failures exceed
5% with a minimum of four. Aborted stages remain failures; they are not relabeled
as successful smaller-cohort tests. There are no hidden request retries.

| Path / stage | Configured VUs | Owners presenting sessions / owners verified | Actual HTTP in-flight peak | Attempts | Valid 200s | 503s | p50 / p95 / p99 of all attempts, ms | Elapsed, s |
| --- | ---: | --- | ---: | ---: | ---: | ---: | --- | ---: |
| API, paced | 128 | 93 / 70 | 37 | 155 | 108 | 47 | 227.275 / 2071.906 / 2881.998 | 8.765 |
| Proxy, paced | 128 | 93 / 80 | 34 | 153 | 120 | 33 | 564.953 / 1643.399 / 2416.929 | 8.905 |
| API, burst | 64 | 64 / 28 | 64 | 127 | 28 | 99 | 122.300 / 1719.076 / 1826.596 | 1.981 |
| Proxy, burst | 64 | 64 / 28 | 64 | 127 | 28 | 99 | 295.496 / 1922.612 / 1976.165 | 2.007 |

There were zero unsafe successful responses, zero transport timeouts and zero
429s in these four stages. The overload availability result is a failure at those
offered workloads: paced failures were 47/155 and 33/153; burst failures were
99/127 per path. The paced run stopped before all 128 configured owners issued
requests, so it does **not** prove 128 simultaneous authenticated customers.
None of these abbreviated overloaded stages reached their planned foreign-owner
probes; that invariant was exercised by the complete 8/24 runs.

Burst latency for accepted reads was higher than latency for rejected traffic:
API 200-response p95/p99 was 1826.596/1841.589 ms, versus 503 p95 136.898 ms;
proxy 200-response p95/p99 was 1976.165/1982.276 ms, versus 503 p95 333.686 ms.
Attempted burst throughput was 64.099/63.268 requests/s, but accepted-read
throughput was approximately 14.13/13.95 reads/s. Fast rejection is not useful
application throughput.

Each overloaded stage had one explicit, separately recorded recovery probe:
authenticated bootstrap returned 200 with exact identity/scope and no retries.
Paced recovery took 62.500/78.201 ms; final burst recovery took 67.685/74.929 ms
for API/proxy respectively. Recovery probes are excluded from stage throughput.

## Resources and deployment limits

The host offered a four-CPU cgroup quota, five visible logical CPUs, 32 GiB memory
and about 3 GiB free disk. Databases were capped at 500 MiB; the largest seeded
128-owner database was 48,554,511 bytes. Each benchmark started one API process
and one production Next process. Container services remained running, while
other test/build/scan workloads were held for the clean intervals.

The service retained default budgets: maximum 32 in-flight requests per API
process, four reserved control slots (28 ordinary slots), 48 AnyIO thread tokens;
PostgreSQL pool five plus five overflow, three-second pool wait timeout and
15-second statement timeout. The limiter used a unique Redis namespace and the
normal per-minute API budgets of 600 per session, 6,000 per source and 60,000
globally. Control budgets were 120 per session/source and 6,000 globally. No rate,
pool, admission or process-count increase was used to manufacture a pass.
Source identity was the actual loopback peer; no signed synthetic source-IP
header override was used.

| Final corrected resource sample | API paced128 | Proxy paced128 | API burst64 | Proxy burst64 |
| --- | ---: | ---: | ---: | ---: |
| API+web CPU seconds | 6.36 | 7.28 | 1.76 | 2.48 |
| Load-driver CPU seconds | 2.13 | 1.87 | 0.72 | 0.72 |
| PostgreSQL CPU seconds | 2.15 | 2.05 | 0.64 | 0.58 |
| API+web peak summed RSS, MiB | 231.0 | 263.3 | 227.0 | 269.9 |
| Driver peak RSS, MiB | 134.8 | 140.0 | 119.4 | 121.1 |
| PostgreSQL pool/checkouts peak | 10 | 10 | 10 | 10 |
| Pool wait seconds, summed over concurrent checkouts | 47.847 | 50.994 | 11.448 | 15.599 |

Pool-wait maxima were 2.359 seconds in the paced run and 1.211 seconds in the
final burst. PostgreSQL sampling found up to ten connections in transaction
while requests performed Python work; these are not all blocked SQL statements.
The API did not retain more than ten checked-out connections. In the complete
8/24 run, pool waits totaled 450.198 seconds across concurrent requests and the
maximum was 2.085 seconds. Summed waits exceed wall time because users wait in
parallel; they are not a single queue's elapsed duration.

The final burst sampled **64 established API TCP sockets** and up to **72 sockets
on the Next server port**. These are server-port socket observations, including
idle keep-alive and internal Next connections. They are different from the
64 concurrent client HTTP requests, configured 28 ordinary API processing slots,
and 28 successfully authenticated owners. Socket sampling every 250 ms may miss
short-lived peaks. No 50,000-socket test took place.

Process RSS is summed, so PostgreSQL shared pages can be counted repeatedly.
CPU tick counters retain samples for exited processes, with at most the final
sample interval missed. The earlier 8/24 profiler included server descendants
in driver CPU/RSS and could lose exited PostgreSQL worker CPU; those earlier
driver/PG CPU fields are explicitly flagged in its JSON and should not be used.
Its HTTP, query-count and pool measurements are unaffected. The corrected
resource values above come from the final 128/64 runs.

## Large-inbox pagination

A 2,500-conversation owner had 2,498 readable chats. Exact expected pages at
readable positions 0, 1,000 and 2,468 were checked against the real bootstrap
route. Each returned at most 30 chats, used 23 SQL queries and used a timestamp/
ID keyset predicate with **no SQL OFFSET**. The first post-prefetch diagnostic
route times were 73.767, 110.278 and 68.171 ms; these are diagnostic TestClient
route timings, not transport latency or large-inbox SLOs.

PostgreSQL EXPLAIN ANALYZE for that bounded fixture reported 8.676, 2.890 and
1.791 ms at those positions. It chose sequential scans/hash joins and a bounded
sort, scanning the owner's readable metadata. No index change was justified by
this small profile. This proves correct bounded pages for 2,500 chats, not
constant database work for millions of chats or 50,000 owners. Workspace/time/ID
indexing, fair cells and total connection budgets need their own larger profile.

## Repeat and inspect

Published aggregate records contain no session cookies, database URLs,
encryption keys, message bodies or real customer data:

- [Baseline](benchmarks/http-baseline.json)
- [Complete 8/24 run](benchmarks/http-prefetch-8-24.json)
- [Paced128, failed capacity stage](benchmarks/http-paced-128.json)
- [Burst64, failed capacity stage and recovery](benchmarks/http-burst-64.json)
- [Distributed 50,000 scenario, NOT_RUN](benchmarks/target-50000.json)

Run from a bootstrapped checkout with local PostgreSQL/Redis and a current web
production build:

```sh
npm run web:build
.venv/bin/python scripts/capacity_http.py --owners 32 --chats 32 --large-chats 2500 --concurrency 8 24 --requests-per-user 20 --output .local/capacity-repeat.json --label repeat
.venv/bin/python scripts/capacity_http.py --owners 128 --chats 32 --large-chats 2500 --concurrency 128 --requests-per-user 10 --think-seconds 2 --stagger-seconds 8 --output .local/capacity-paced128.json --label paced
.venv/bin/python scripts/capacity_http.py --owners 64 --chats 32 --large-chats 2500 --concurrency 64 --requests-per-user 10 --output .local/capacity-burst64.json --label overload
```

Overloaded runs intentionally exit nonzero after writing all failures and cleanup.
The harness refuses remote fixture databases, creates a uniquely named local
PostgreSQL database, caps the seed, starts real API/proxy child processes,
collects bounded metadata, stops only its own processes, and drops only its own
database in `finally`. A fresh encryption key/private configuration is held in a
0600 temporary file and removed on exit. Client construction/certificate loading
is excluded from timing; TLS verification remains enabled for the remote runner.

Reports record the Git commit and dirty-state flag plus SHA256 for the measured
backend/harness/proxy files. The 128/64 reports additionally record actual Next
BUILD_ID, its timestamp, Node and Next versions. The measured proxy used the
successful 09:17:50 UTC build. Later patches added Tools privacy fencing, a
64 KiB ordinary proxy body cap and web-process admission before body buffering
(32 total slots, four reserved for controls, at most two imports). Those patches
have separate functional/regression evidence in QA and **were not rebenchmarked**.
The measurements above describe their recorded build, not the final proxy's
throughput. Loopback HTTP does not measure production TLS, edge
networking, mobile hardware, OAuth/model/provider latency, subscriptions, jobs,
write concurrency or WhatsApp reconnect behavior.

## Distributed 50,000 target

[scripts/capacity_distributed.py](../scripts/capacity_distributed.py) is a prepared
HTTPS read runner, not a recorded 50,000-user test. It accepts a private fixture
manifest bound to the exact disposable origin, uses independent sessions and
source expectations, preserves certificate verification and bounds each worker
to 1,000 users. Fifty provisioned workers with disjoint owner ranges are planned.
Remote execution, fixture provisioning and server capacity at that scale are
**NOT_RUN**. Per-runner peaks cannot be summed to claim simultaneous concurrency.

Before any distributed run, operators must provide a dedicated deployment with
external sends disabled/model mock, 50,000 seeded synthetic owners and private
0600 session manifests, independently verified clock synchronization, runner
CPU/network/socket margins, and server-side active-request/queue/connection
metrics. Record all rate/admission/pool/replica overrides and genuine or trusted
signed source identities. The manifest contains `fixture_type` equal to
`milo_capacity_disposable`, `external_sends_enabled:false`, a `fixture_id`, exact
`target_origin`, recorded `service_configuration`, and `owners` with synthetic
`owner_id`, `workspace_id`, `cookie`, `readable_ids`, `message_ids`, and `contact_ids`.
Do not commit that manifest.

A runner command after provisioning is:

```sh
.venv/bin/python scripts/capacity_distributed.py --manifest /private/synthetic-fixture.json --target https://dedicated-load-fixture.example --prefix /v1 --owner-offset 0 --virtual-users 1000 --requests-per-user 60 --think-seconds 30 --stagger-seconds 120 --start-at "$MILO_LOAD_START_UTC" --output runner-00.json
```

Set `MILO_LOAD_START_UTC` to a future ISO UTC timestamp within five minutes; all
clients prepare before that barrier. Each later worker uses a different offset
(1,000 through 49,000). Verify the
barrier and server-side concurrency before interpreting a simultaneous burst.
The script does not establish that clocks are synchronized or that every runner
stays within its own resource limits. The scenario proposes stages at 100,
1,000, 10,000 and 50,000, a paced soak and a separately labeled simultaneous
request burst. Acceptance thresholds are proposed gates, not observed outcomes.

At a 30-second poll interval, 50,000 active owners imply roughly 1,667 polls/sec
before other work. The present global default admits at most about 1,000 API
requests/sec. Horizontal processes also multiply PostgreSQL pools; multiplying
workers without budgeting total connections will recreate contention. Real
50,000 WhatsApp account sockets, durable ingest/event bursts, live model quotas,
action/job scheduling, subscription fan-out, reconnect/failover, a 24-hour soak
and regional disaster recovery remain separate unmeasured release gates.
