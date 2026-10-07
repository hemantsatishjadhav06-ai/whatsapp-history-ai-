# Load and capacity evidence

Evidence date: 6 October 2026. The only recorded workload result is a small synthetic
development smoke. It does not establish production throughput or any number of connected
WhatsApp accounts. See [original measured result](load-test-results.md).

| Recorded workload | Observed result |
| --- | --- |
| ASGI canonical ingestion, temporary SQLite, synthetic mock connector | 100 requests at concurrency four |
| Accepted/persisted records | 100 successful, zero failed; 100 stored messages |
| Wall time / request rate | 3.368 seconds / 29.7 attempted requests per second |
| p50 / p95 request latency | 111.73 / 313.04 ms |
| Actual transport/model traffic | None |

This latest smoke exercised the local uncommitted handoff implementation during concurrent
database tests and a container build in the cloud workspace. The prior smoke measured
1.327 seconds / 75.37 requests/s with p50/p95 51.65/59.60 ms. Different contention and source
state prevent treating those two small samples as a controlled performance comparison.
Do not extrapolate either smoke to a 50,000-account service.

The handoff proposes separate release gates; none has been measured here:

| Target workload | Evidence required |
| --- | --- |
| 50,000 connected isolated customer accounts | Actual adapter session footprint, leases/fences, reconnect/failover and phone continuity |
| Event plane | 11,600 events/s sustained, p95 durable ingest under one second at declared provisioned load |
| Event burst | 24,000/s processing capacity; 250,000 injected within five seconds during background load; backlog clears within 30 seconds; no lost accepted inputs |
| AI peak/burst | Background 232/s and 463/s, plus 50,000 jobs; measured queue age, expiry, tokens and drain |
| Normal candidate generation | p95 under 15 seconds for declared model and normal workload; burst measured separately |
| Control responsiveness | Commit p95 under 250 ms and p99 under one second; phone observation lag recorded separately |
| 50,000 authenticated subscribers | Approximately 10,000 scoped metadata deliveries/s, 5% reconnect within 30s and bounded resources; client enabled but this capacity run NOT_RUN |
| Soak/fault recovery | At least 24 hours at target stage; bounded pools/queues/memory and no stale-authority submissions or blind resends |
| Disaster recovery | Tested same-region acknowledged-input loss zero; proposed regional RPO <=15 minutes/RTO <=2 hours |

These are proposed acceptance targets, not provider-approved quotas. At the handoff's
eight-second average model latency, 232/463 eligible jobs per second imply about 1,856/3,704
concurrent model calls before verification/embeddings. Quotas, token cost and expiry must
be measured independently of session count. A larger queue does not make late automatic
replies useful; expire stale work rather than flood recipients after an outage.

Stage permitted cohorts at 100, 1,000, 10,000 and 50,000 only after each stage proves
eligibility, resources, isolation, control correctness and rollback. Tenant cells, virtual
shard placement, PgBouncer, managed multi-zone infrastructure and fairness remain deployment
work. Local Docker/Kafka/Temporal success is integration evidence, not capacity proof.
