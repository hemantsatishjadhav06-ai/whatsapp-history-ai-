# Capacity plan for 50,000 clients

The current deployment is a bounded pilot. Define a client precisely before sizing:
an authenticated web user, an idle linked account, a live provider socket, an active
model request and an outbound message consume different resources. The existing
HTTP, SQL and Redis measurements establish limited local behavior; none establishes
50,000 simultaneous real WhatsApp accounts.

## Measured bottlenecks and implemented improvements

The [exploratory HTTP report](benchmarks/launch-capacity-exploratory-2026-10-07.json)
records 8/24 concurrent-user stages with no unexpected errors and a 64-request burst
with 99 explicit 503s out of 127 attempts per path. Both paths recovered. It used
the evolving working-tree API and a preexisting Next build, disposable PostgreSQL
and synthetic sessions. Those overloaded stages remain failed availability results.

The [first SQL diagnostic](benchmarks/storage-scalability-exploratory-2026-10-07-run1.json)
and [second diagnostic](benchmarks/storage-scalability-exploratory-2026-10-07-run2.json)
record actual PostgreSQL EXPLAIN ANALYZE repetitions with 40,000 encrypted messages
and 50,000 ledger rows. They record query text, exact source hashes, plan nodes,
buffer blocks, elapsed times and cleanup. No customer rows or production indexes
were changed by the diagnostic.

Two partial indexes are implemented in revision `ae912f73c804`: retained message
pages on workspace/conversation/provider-time/id, and retention pages on
workspace/received-time/id. They eliminate the measured broad scan/sort for those
bounded pages. The query plan and timings vary with cache/scheduling and fixture
shape; use the recorded repetitions, not a single number as a latency guarantee.
An additional usage-ledger index showed too little absolute benefit to justify
its write/storage overhead and was deferred.

Older message pages now carry both timestamp and ID. A timestamp-only cursor could
permanently skip messages sharing a provider timestamp. The lexicographic cursor
is validated in the exact owner/conversation and used by the native client. A
foreign or deleted cursor cannot become a route to another owner's data. SQLite
partial predicates use `deleted IS 0`, matching the actual ORM query; PostgreSQL
uses `deleted IS FALSE`.

Held scheduler entries rotate using persisted last-inspected metadata instead of
repeatedly occupying the oldest 100-row batch. Source/control authority and due
times remain unchanged. Background drafting has separate expiring grants, durable
claims and provider concurrency limits; it does not consume the control loop while
a model request waits on the network.

## Shared limits and verified source identity

The current defaults include 60,000 global ordinary requests per minute, 6,000 per
socket source, 30 authentication operations per source and 120 controls per source.
API and Web each admit 32 requests with four reserved controls, leaving 28 ordinary
slots per process. API SQL pools default to five plus five overflow. Multiplying
replicas without a database connection budget can exhaust PostgreSQL first.

Actual two-instance Redis probes verified those default source boundaries:

| Presented sources | Attempted | Accepted | Rejected |
| --- | --- | --- | --- |
| Same socket source, ordinary API | 6,064 | 6,000 | 64 |
| Same socket source, authentication | 64 | 30 | 34 |
| Same socket source, controls | 192 | 120 | 72 |
| Distinct verified signed sources, authentication | 64 | 64 | 0 |
| Distinct verified signed sources, controls | 192 | 192 | 0 |

The signed-source rows prove the existing local verifier and shared counters,
not Railway's edge header behavior. All 6,519 task-owned probe counters were removed.
Until provider forwarding is independently verified, Web TRUST_PROXY_HOPS remains
zero and untrusted browser headers cannot select a rate-limit identity.

To enable it safely, establish the actual edge chain and which hop supplies the
client address; test spoofed X-Forwarded-For, IPv4/IPv6, duplicate headers and direct
origin access. Only then configure the exact hop count and server-held signing
secret. Verify that Web emits a bounded, fresh signature over the canonical source,
that API accepts only that signature, and that tampered/expired identities fail.
Keep independent actor/global budgets and the control reserve. Increasing a number
without this evidence can either collapse all customers into one proxy bucket or
trust a caller-selected address.

## Account cells and worker topology

The optional QR pilot defaults to 20 sessions, with an explicit bounded maximum.
Its per-account PostgreSQL advisory connection provides a simple single-cell owner
for the tested pilot. It is not a 50,000-account storage/lease strategy.

Target-scale infrastructure needs independently bounded account cells. Give each
cell a distributed renewable lease/fence, place its encrypted Signal records behind
a bounded pooled store, and route each account consistently to its current cell.
Do not keep one SQL connection per idle account. A lost cell lease must close every
owned socket and prevent stale key writes, clears and submissions. Migration must
fence the old cell before the new one can resume, including a crash between claim
and provider acceptance.

Use durable per-owner fair intake and model queues, with bounded payload size,
retention and backlog TTL. Batch source ingestion where authority permits it;
provider/model network work must stay outside short SQL authority transactions.
Separate socket ownership, API admission, model concurrency and outbound quota.
Scale one only after measuring its actual bottleneck. A private gateway token also
needs an intentional service/actor limit model before introducing many cells.

For 50,000 web clients, a 30-second poll creates at least 1,667 requests/second;
ten seconds creates 5,000/second before other work. Prefer authenticated change
notifications/subscriptions with a recoverable cursor and an explicit per-owner
audience. Periodic reconciliation must recover missed notifications without
republishing private content to broad channels. Long-lived connection memory,
file descriptors, backpressure and reconnect storms need separate measurements.

## Validation cohorts and release evidence

Advance through 100, 500, 2,000, 10,000 and 50,000 clients only after the preceding
cohort meets its defined workload. Record connected versus active users, requests
and messages per second, queue age, provider/model rates, memory per account,
database pools, TLS/edge identity, retry policy and the actual infrastructure size.
Measure sustained arrivals and burst/reconnect/soak conditions; a closed-loop test
with fewer actual participants cannot certify the configured virtual-user count.

Require owner isolation, no duplicate provider submissions, timely pause/revoke,
fair work admission, bounded costs, exact receipt correlation, restart recovery,
credential erasure and managed restore. Keep explicit overload failures in the
report and verify recovery. Model/provider sessions and an installed app must have
their own acceptance; a database with 50,000 synthetic rows proves neither.

Paid capacity, real account credentials and consenting test phones are still
required for those external cohorts. This plan and the pilot optimizations give a
testable path; no target-scale result is claimed before it exists.

The production migration uses plain ascending timestamp/id index columns; both
engines scan them backwards after the workspace/chat equality predicates. This
preserves descending page order and lets SQLite/Alembic verify reflected model
parity. The earlier exploratory diagnostic index used explicit DESC columns; its
measurements remain historical. Final-source planner regression verifies the
ascending index avoids a sort and returns the same page.
