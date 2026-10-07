# Shared request-limit smoke evidence

Run from the repository root with local Redis available at `127.0.0.1:6379`:

```sh
uv run --frozen python scripts/request_limits_smoke.py
```

The script uses a fresh random Redis namespace and deletes only its own keys after the run. It creates no SQL engine, calls no provider, sends no external HTTP request and never flushes Redis. Requests contain synthetic credentials and documentation-only IP addresses. Two separately constructed limiter instances share the Redis budgets, with concurrent requests alternating between them.

The local run on 2026-10-07 passed these exact admission checks:

| Budget | Concurrent requests | Accepted | Rejected |
| --- | ---: | ---: | ---: |
| Shared source across `/me` and `/v1/me`, rotating cookies and unsigned forwarding headers | 60 | 7 | 53 |
| Shared actor across route aliases and distinct source addresses | 60 | 6 | 54 |
| Nonce route aliases, rotating bearer credentials and unsigned source headers | 60 | 5 | 55 |
| Global ordinary-request budget with distinct actors and sources | 60 | 11 | 49 |
| Independent control budget after the ordinary budget is exhausted | 20 | 4 | 16 |

All 52 observed counters had numeric values and bounded expiry. Actor and source keys contained SHA-256 hashes rather than raw cookies or IP addresses. A freshly signed IPv6 source was canonicalized; expired, unsigned and tampered source claims fell back to the socket peer. These checks use a local signing key and do not establish trust in a deployed ingress header.

An unavailable, reserved local Redis port produced HTTP 503 for both ordinary route aliases through the ASGI security middleware. The synthetic application sink was not entered, so the requests were rejected before any endpoint or SQL work. Liveness remained available. This verifies the local outage boundary and shared limiter implementation; it is not a capacity benchmark, a Railway deployment acceptance check or proof of 50,000 concurrent users.
