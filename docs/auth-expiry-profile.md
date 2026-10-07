Authentication expiry queries were profiled on 7 October 2026 using an isolated
local PostgreSQL database. Each of three metadata tables contained 50,000
synthetic records, with 100 eligible for deletion. The temporary database was
removed after profiling. This measures database queries, without provider calls
or a concurrent user workload.

The measured query selected at most 100 IDs, ordered by `(expires_at, id)`, with
`FOR UPDATE SKIP LOCKED`. `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` produced these
single-run results:

| Table | Before | After | Resulting plan |
| --- | ---: | ---: | --- |
| `login_nonces` | 4.028 ms | 0.121 ms | Expiry and ID index scan |
| `sessions` | 5.869 ms | 0.116 ms | Expiry and ID index scan |
| `native_sessions` | 8.452 ms | 0.282 ms | Bitmap scans using refresh expiry and existing refresh hash indexes |

Before indexing, each query scanned all 50,000 records and rejected 49,900.
For native sessions, all access tokens had expired, while 49,900 sessions still
had a valid refresh credential. Adding the refresh expiry index allowed the
query to find the 100 unrefreshable sessions without scanning those valid
device sessions.

Migration `86b7bbad6fc1` adds these three indexes. Existing native access expiry
and challenge expiry indexes remain available. Measurements depend on cache,
record distribution, and host load; they establish the selected query plans,
without establishing production capacity.

The global worker deletes at most 100 authentication records per tick across
all four categories. Browser and native challenge request paths each delete at
most 100 expired records from their own challenge table. Owner content-retention
requests do not perform global authentication cleanup.
