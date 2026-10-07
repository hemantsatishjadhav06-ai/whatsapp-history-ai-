"""Read-only remote capacity runner for explicitly provisioned synthetic fixtures.

This does not provision 50,000 users or certify deployment capacity. Each worker
uses separate synthetic sessions, exports no session secrets, keeps TLS verified,
and is bounded to 1,000 owners. Infrastructure and private manifests must exist
before the planned distributed scenario can be executed.
"""
import argparse
import asyncio
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import stat
import time
from urllib.parse import urlsplit

try:
    from .capacity_http import workload
except ImportError:
    from capacity_http import workload


def read_manifest(path):
    path = Path(path)
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("Synthetic session manifest must have private 0600 permissions")
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Manifest exceeds the bounded 64MiB runner input")
    value = json.loads(path.read_text())
    if value.get("fixture_type") != "milo_capacity_disposable" or value.get("external_sends_enabled") is not False:
        raise ValueError("Only a disposable synthetic fixture with external sends disabled is supported")
    owners = value.get("owners", [])
    required = {"owner_id", "workspace_id", "cookie", "readable_ids", "contact_ids", "message_ids"}
    if len(owners) < 2 or any(not required <= owner.keys() for owner in owners):
        raise ValueError("Manifest lacks the seeded owner/source expectations")
    if len({owner["owner_id"] for owner in owners}) != len(owners) or len({owner["cookie"] for owner in owners}) != len(owners):
        raise ValueError("Fixture owners and sessions must be independent")
    return value


def target_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Use a dedicated HTTPS fixture origin without embedded credentials")
    if parsed.path not in {"", "/"}:
        raise ValueError("Configure the origin and API prefix separately")
    return value.rstrip("/")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--prefix", choices=["/v1", "/api"], default="/v1")
    parser.add_argument("--owner-offset", type=int, required=True)
    parser.add_argument("--virtual-users", type=int, default=1000)
    parser.add_argument("--requests-per-user", type=int, default=20)
    parser.add_argument("--think-seconds", type=float, default=30)
    parser.add_argument("--stagger-seconds", type=float, default=60)
    parser.add_argument("--start-at", help="Optional synchronized UTC ISO timestamp; requires externally verified clock synchronization")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.virtual_users <= 1000 or not 10 <= args.requests_per_user <= 200 or args.owner_offset < 0:
        parser.error("Bound each runner to 1..1000 distinct owners and 10..200 requests each")
    if not 0 <= args.think_seconds <= 30 or not 0 <= args.stagger_seconds <= 120:
        parser.error("Bound think/stagger to 0..30 and 0..120 seconds")
    fixture = read_manifest(args.manifest)
    origin = target_url(args.target)
    if fixture.get("target_origin") != origin:
        parser.error("Target must exactly match the dedicated fixture manifest origin")
    all_owners = fixture["owners"]
    end = args.owner_offset + args.virtual_users
    if end > len(all_owners):
        parser.error("Requested owner range exceeds the provisioned fixture")
    # Include a distinct owner for the negative authorization probe, even when
    # a final worker contains only one virtual user. Never share its session.
    owners = all_owners[args.owner_offset:end] + [all_owners[end % len(all_owners)]]
    start_epoch = None
    if args.start_at:
        stamp = datetime.fromisoformat(args.start_at)
        if stamp.tzinfo is None or not 0 <= stamp.timestamp() - time.time() <= 300:
            parser.error("Start timestamp requires an offset and must be within the next five minutes")
        start_epoch = stamp.timestamp()
    started = datetime.now(UTC).isoformat()
    result = asyncio.run(workload(origin, args.prefix, owners, concurrency=args.virtual_users,
                                 requests_per_user=args.requests_per_user, think_seconds=args.think_seconds,
                                 stagger_seconds=args.stagger_seconds, start_epoch=start_epoch))
    report = {"fixture_id": fixture["fixture_id"], "runner_pid": os.getpid(), "started_at": started,
              "finished_at": datetime.now(UTC).isoformat(), "owner_range": [args.owner_offset, end],
              "target": origin, "prefix": args.prefix, "limits": fixture.get("service_configuration"),
              "mode": "synthetic_fixture_remote_https_read_only", "tls_verification": True,
              "clock_synchronization_verified_by_runner": False, "run": result,
              "capacity_claim": "single runner result; summing per-runner peaks does not prove simultaneous 50k concurrency"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Aggregate report written: {args.output}")
    if result["failed_invariant_checks"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
