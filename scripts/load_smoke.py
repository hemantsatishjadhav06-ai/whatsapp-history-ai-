"""Small synthetic ingestion benchmark; it does not establish production capacity."""

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

import httpx

from demo import incoming, seed_owner, synthetic_backend


async def exercise(app, settings, connector_id, conversation_id, *, requests, concurrency):
    semaphore = asyncio.Semaphore(concurrency)
    durations = []
    failures = []
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://synthetic.invalid") as client:
        async def ingest(index):
            async with semaphore:
                started = time.perf_counter()
                response = await client.post(
                    "/internal/connector-events", json=incoming(connector_id, conversation_id, f"load-{index}"),
                    headers={"Authorization": f"Bearer {settings.internal_service_token}"},
                )
                durations.append((time.perf_counter() - started) * 1000)
                if not response.is_success:
                    failures.append(response.status_code)

        started = time.perf_counter()
        await asyncio.gather(*(ingest(index) for index in range(requests)))
        elapsed = time.perf_counter() - started
    ordered = sorted(durations)
    return {
        "mode": "synthetic_asgi_sqlite", "requests": requests, "concurrency": concurrency,
        "successful_requests": requests - len(failures), "failed_requests": len(failures),
        "error_statuses": sorted(set(failures)), "elapsed_seconds": round(elapsed, 3),
        "requests_per_second": round(requests / elapsed, 2),
        "p50_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(ordered[max(0, int(len(ordered) * .95) - 1)], 2),
        "scope": "Single process, temporary SQLite database, mock connector, no model or provider traffic",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.requests <= 10000 or not 1 <= args.concurrency <= 100:
        parser.error("requests must be 1..10000; concurrency must be 1..100")
    with synthetic_backend() as (app, client, settings):
        _, connector, conversation = seed_owner(client)
        result = asyncio.run(exercise(app, settings, connector["id"], conversation["id"],
                                      requests=args.requests, concurrency=args.concurrency))
        stored = client.get(f"/conversations/{conversation['id']}/messages", params={"limit": 200})
        if args.requests <= 200:
            assert len(stored.json()) == result["successful_requests"]
    report = json.dumps(result, indent=2) + "\n"
    print(report, end="")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report)
    if result["failed_requests"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
