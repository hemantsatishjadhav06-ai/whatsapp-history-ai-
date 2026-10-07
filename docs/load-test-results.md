# Synthetic load smoke result

Executed on 6 October 2026 with Python 3.12 in the cloud development workspace:

```bash
.venv/bin/python scripts/load_smoke.py --requests 100 --concurrency 4 --output .local/load-smoke.json
```

| Measurement | Observed |
| --- | --- |
| Attempted canonical ingestion requests | 100 |
| Concurrent requests | 4 |
| Successful / failed requests | 100 / 0 |
| Wall time for ingestion | 3.368 seconds |
| Attempted requests per second | 29.7 |
| p50 / p95 request latency | 111.73 / 313.04 ms |
| Persistence check | 100 stored messages matched successful requests |

Scope: one FastAPI application through HTTP ASGI transport, temporary SQLite database,
mock connector, fresh synthetic inbound text events, and no real network/model/provider
traffic. Setup time is excluded. This is a development functional/load smoke, not a
sustained capacity result or a 50,000-account benchmark. The machine's resource contention
and the ongoing implementation can change these numbers. This run used the local
uncommitted handoff implementation while database tests and a container build competed
for resources. The prior smoke recorded 1.327 seconds, 75.37 requests/s and p50/p95
51.65/59.60 ms; these small differently contended samples are not a controlled comparison.
Rerun against the final release
and record its commit/environment for comparisons.

Required production measurements include PostgreSQL concurrency, duplicate/redelivery and
edit/delete bursts, worst-case history imports, concurrent worker dispatch races, model
latency/budgets, broker and worker outages, verified gateway session footprint, account
placement cells, fair tenant scheduling, provider quotas, and recovery under the full
simultaneous-burst workload. No result in this repository establishes those gates.
