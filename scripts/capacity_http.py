"""Repeatable real-HTTP read capacity measurement on a disposable PostgreSQL DB.

Counts requests, authenticated owners, in-flight requests and HTTP sockets separately.
No real WhatsApp/model traffic or production-capacity certification is performed.
"""

import argparse
import asyncio
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
import json
import hashlib
from importlib.metadata import version
import math
import os
from pathlib import Path
import shutil
import signal
import ssl
import subprocess
import tempfile
import threading
import time

from cryptography.fernet import Fernet
import httpx
import psycopg
from psycopg import sql
from sqlalchemy import event, or_, select, text
from sqlalchemy.engine import make_url

from assistant.config import Settings
from assistant.db import Base, now, uid
from assistant.models import Conversation, Permission
from assistant.main import create_app
try:
    from .capacity_fixture import fixture_settings, seed
except ImportError:
    from capacity_fixture import fixture_settings, seed

ROOT = Path(__file__).resolve().parents[1]


def percentile(values, percentage):
    return round(sorted(values)[max(0, math.ceil(len(values) * percentage / 100) - 1)], 3) if values else None


def validate_response(kind, status, body, owner, *, page_limit=30):
    """Assert source scope and exact identity, including expected negative probes."""
    if kind == "foreign":
        return status == 404 and "user" not in body and "workspace" not in body
    if status != 200:
        return False
    allowed = set(owner["readable_ids"])
    if kind == "bootstrap":
        if body["user"]["id"] != owner["owner_id"] or body["workspace"]["id"] != owner["workspace_id"]:
            return False
        if any(row["id"] != owner["workspace_id"] for row in body["workspaces"]):
            return False
        selected = {row["id"] for row in body["conversations"]}
        if len(selected) > page_limit or not selected <= allowed:
            return False
        for key in ("memories", "styles", "drafts", "actions", "grants"):
            if any(row["conversation_id"] not in selected for row in body[key]):
                return False
        if any(row["id"] not in owner["contact_ids"] for row in body["contacts"]):
            return False
        return all(row["workspace_id"] == owner["workspace_id"] for row in body["connections"])
    if kind == "messages":
        expected = set(owner["message_ids"][owner["readable_ids"][0]])
        return {row["id"] for row in body} == expected
    if kind == "resolve":
        return body["object"]["id"] == owner["readable_ids"][0] and body["object"]["workspace_id"] == owner["workspace_id"]
    return False


def process_sample(pids, *, descendants=True):
    """Linux process-group RSS/CPU; excludes unrelated workspace processes."""
    selected = set(pids)
    entries = {}
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit():
            continue
        try:
            fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
            entries[int(directory.name)] = (int(fields[1]), int(fields[11]) + int(fields[12]), int(fields[21]))
        except (OSError, ValueError, IndexError):
            continue
    changed = True
    while changed and descendants:
        before = len(selected)
        selected.update(pid for pid, (parent, _, _) in entries.items() if parent in selected)
        changed = len(selected) != before
    ticks = os.sysconf("SC_CLK_TCK")
    page = os.sysconf("SC_PAGE_SIZE")
    counters = {pid: entries[pid][1] / ticks for pid in selected if pid in entries}
    return {"cpu_seconds": sum(counters.values()), "cpu_by_pid": counters,
            "rss_bytes": sum(entries[pid][2] for pid in selected if pid in entries) * page}


class StageMonitor:
    """Bounded metadata-only process/PG sampling outside the client event loop."""

    def __init__(self, pids, database_url, database_pid=None, *, api_port=None, web_port=None):
        self.pids, self.database_url, self.database_pid = pids, database_url, database_pid
        self.api_port, self.web_port = api_port, web_port
        self.stop = threading.Event()
        self.metrics = {"service_rss_bytes_peak": 0, "driver_rss_bytes_peak": 0,
                        "postgres_rss_bytes_peak": 0, "postgres_connections_peak": 0,
                        "postgres_active_peak": 0, "postgres_idle_in_transaction_peak": 0,
                        "postgres_waiting_peak": 0, "api_established_tcp_connections_peak": 0,
                        "web_established_tcp_connections_peak": 0, "samples": 0, "sample_errors": 0}
        self.before = {"service": process_sample(pids), "driver": process_sample([os.getpid()], descendants=False)}
        if database_pid:
            self.before["postgres"] = process_sample([database_pid])
        self.observed = {kind: dict(sample["cpu_by_pid"]) for kind, sample in self.before.items()}
        self.thread = threading.Thread(target=self.sample, daemon=True)

    def retain_cpu(self, kind, sample):
        for pid, value in sample["cpu_by_pid"].items():
            self.observed[kind][pid] = max(self.observed[kind].get(pid, 0), value)

    def __enter__(self):
        self.thread.start()
        return self

    def sample(self):
        url = make_url(self.database_url).set(drivername="postgresql").render_as_string(hide_password=False)
        try:
            with psycopg.connect(url, autocommit=True, application_name="capacity_monitor", connect_timeout=2) as connection:
                while not self.stop.is_set():
                    try:
                        service, driver = process_sample(self.pids), process_sample([os.getpid()], descendants=False)
                        self.retain_cpu("service", service)
                        self.retain_cpu("driver", driver)
                        self.metrics["service_rss_bytes_peak"] = max(self.metrics["service_rss_bytes_peak"], service["rss_bytes"])
                        self.metrics["driver_rss_bytes_peak"] = max(self.metrics["driver_rss_bytes_peak"], driver["rss_bytes"])
                        if self.database_pid:
                            pg = process_sample([self.database_pid])
                            self.retain_cpu("postgres", pg)
                            self.metrics["postgres_rss_bytes_peak"] = max(self.metrics["postgres_rss_bytes_peak"], pg["rss_bytes"])
                        row = connection.execute("""SELECT count(*), count(*) FILTER (WHERE state='active'),
                            count(*) FILTER (WHERE state='idle in transaction'),
                            count(*) FILTER (WHERE wait_event_type IS NOT NULL AND state<>'idle')
                            FROM pg_stat_activity WHERE datname=current_database()
                            AND application_name<>'capacity_monitor'""").fetchone()
                        for key, value in zip(("postgres_connections_peak", "postgres_active_peak",
                                               "postgres_idle_in_transaction_peak", "postgres_waiting_peak"), row):
                            self.metrics[key] = max(self.metrics[key], value)
                        for key, port in (("api_established_tcp_connections_peak", self.api_port),
                                          ("web_established_tcp_connections_peak", self.web_port)):
                            if port is not None:
                                sockets = sum(1 for line in Path("/proc/net/tcp").read_text().splitlines()[1:]
                                    if (fields := line.split())[3] == "01" and int(fields[1].split(":")[1], 16) == port)
                                self.metrics[key] = max(self.metrics[key], sockets)
                        self.metrics["samples"] += 1
                    except (OSError, psycopg.Error):
                        self.metrics["sample_errors"] += 1
                    self.stop.wait(0.25)
        except psycopg.Error:
            self.metrics["sample_errors"] += 1

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=3)
        for kind, pids in (("service", self.pids), ("driver", [os.getpid()]),
                           ("postgres", [self.database_pid] if self.database_pid else [])):
            if kind in self.before:
                after = process_sample(pids, descendants=kind != "driver")
                self.retain_cpu(kind, after)
                self.metrics[kind + "_cpu_seconds"] = round(sum(max(0, value - self.before[kind]["cpu_by_pid"].get(pid, 0))
                    for pid, value in self.observed[kind].items()), 3)
        self.metrics["rss_measure"] = "sum of process RSS; shared PostgreSQL pages may be counted repeatedly"
        self.metrics["postgres_cpu_scope"] = "local PostgreSQL service including background processes"
        self.metrics["tcp_measure"] = "sampled IPv4 established server-side sockets by dedicated loopback port, including idle keep-alive; 250ms samples may miss shorter-lived peaks"
        self.metrics["cpu_measure"] = "per-PID tick deltas retained across samples; exited short-lived processes may lose at most the last sample interval"


async def workload(base_url, prefix, owners, *, concurrency, requests_per_user, think_seconds=0, stagger_seconds=0, start_epoch=None):
    # Certificate-store/client setup is excluded from timed requests; verification
    # remains enabled, including when this helper is reused for a remote runner.
    tls = ssl.create_default_context()
    clients = [httpx.AsyncClient(base_url=base_url, trust_env=False, timeout=10, verify=tls,
                                limits=httpx.Limits(max_connections=1, max_keepalive_connections=1))
               for _ in range(concurrency)]
    if start_epoch is not None:
        delay = start_epoch - time.time()
        if delay < 0:
            for client in clients:
                await client.aclose()
            raise ValueError("Runner client preparation missed the requested synchronization barrier")
        await asyncio.sleep(delay)
    actual_started_at = datetime.now(UTC).isoformat()
    started = time.perf_counter()
    samples, failures, endpoints, statuses = [], Counter(), Counter(), Counter()
    endpoint_samples, status_samples = {}, {}
    safety_failures, unavailable, in_flight, peak = 0, 0, 0, 0
    identity_set, authenticated_set = set(), set()
    successful_reads, successful_denials = 0, 0
    stop = asyncio.Event()

    async def virtual_user(number):
        nonlocal in_flight, peak, safety_failures, unavailable, successful_reads, successful_denials
        owner = owners[number]
        foreign = owners[(number + 1) % len(owners)]
        async with clients[number] as client:
            client.cookies.set("session_token", owner["cookie"])
            if stagger_seconds:
                await asyncio.sleep(stagger_seconds * number / concurrency)
            for index in range(requests_per_user):
                if stop.is_set():
                    break
                selector = index % 10
                kind = "foreign" if selector == 9 else "messages" if selector in {6, 7} else "resolve" if selector == 8 else "bootstrap"
                path = {"bootstrap": "/ui/bootstrap", "messages": f"/conversations/{owner['readable_ids'][0]}/messages?limit=50",
                        "resolve": f"/ui/resolve?kind=conversation&id={owner['readable_ids'][0]}",
                        "foreign": f"/ui/bootstrap?workspace_id={foreign['workspace_id']}"}[kind]
                identity_set.add(owner["owner_id"])
                request_start = time.perf_counter()
                status = "transport_failure"
                in_flight += 1
                peak = max(peak, in_flight)
                try:
                    response = await client.get(prefix + path)
                    status = str(response.status_code)
                    statuses[status] += 1
                    body = response.json()
                    expected = 404 if kind == "foreign" else 200
                    if response.status_code != expected:
                        unavailable += 1
                        failures[f"{kind}:http_{response.status_code}"] += 1
                    elif not validate_response(kind, response.status_code, body, owner):
                        safety_failures += 1
                        failures[f"{kind}:scope_or_result"] += 1
                    else:
                        authenticated_set.add(owner["owner_id"])
                        successful_denials += int(kind == "foreign")
                        successful_reads += int(kind != "foreign")
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    unavailable += 1
                    failures[f"{kind}:transport_or_shape"] += 1
                finally:
                    in_flight -= 1
                    duration = (time.perf_counter() - request_start) * 1000
                    endpoints[kind] += 1
                    samples.append(duration)
                    endpoint_samples.setdefault(kind, []).append(duration)
                    status_samples.setdefault(status, []).append(duration)
                    if len(samples) >= concurrency and sum(failures.values()) > max(3, len(samples) // 20):
                        stop.set()
                if think_seconds:
                    await asyncio.sleep(think_seconds)

    await asyncio.gather(*(virtual_user(index) for index in range(concurrency)))
    elapsed = time.perf_counter() - started
    return {"actual_http_stage_started_at": actual_started_at, "http_requests": len(samples), "independent_authenticated_owners": len(authenticated_set),
            "configured_owner_identities": concurrency, "presented_session_owners": len(identity_set),
            "successful_owner_reads": successful_reads, "successful_foreign_owner_denials": successful_denials,
            "virtual_users": concurrency, "peak_http_requests_in_flight": peak,
            "max_client_http_connections": concurrency, "per_user_requests": requests_per_user,
            "closed_loop_think_seconds": think_seconds, "initial_stagger_seconds": stagger_seconds,
            "elapsed_seconds": round(elapsed, 3), "adaptive_aborted": stop.is_set(),
            "attempted_requests_per_second": round(len(samples) / elapsed, 3),
            "successful_invariant_checks": len(samples) - sum(failures.values()), "failed_invariant_checks": sum(failures.values()),
            "safety_invariant_failures": safety_failures, "unavailable_or_transport_failures": unavailable,
            "errors": dict(failures), "http_statuses": dict(statuses), "endpoint_requests": dict(endpoints),
            "endpoint_latency": {key: {"p50_ms": percentile(values, 50), "p95_ms": percentile(values, 95),
                                      "p99_ms": percentile(values, 99)} for key, values in endpoint_samples.items()},
            "latency_by_status": {key: {"p50_ms": percentile(values, 50), "p95_ms": percentile(values, 95),
                                      "p99_ms": percentile(values, 99)} for key, values in status_samples.items()},
            "expected_forbidden_probes": endpoints["foreign"],
            "p50_ms": percentile(samples, 50), "p95_ms": percentile(samples, 95), "p99_ms": percentile(samples, 99),
            "max_ms": round(max(samples), 3) if samples else None,
            "arrival_model": "closed-loop bounded per-owner sequential requests; not fixed-rate open-loop"}


def wait_ready(url, process):
    with httpx.Client(trust_env=False, timeout=2) as client:
        for _ in range(80):
            if process.poll() is not None:
                raise RuntimeError("Fixture server exited; inspect the private benchmark log")
            try:
                response = client.get(url)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
    raise RuntimeError("Fixture server did not become ready")


@contextmanager
def disposable_database():
    original = make_url(Settings().database_url)
    if original.get_backend_name() != "postgresql" or original.host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("This fixture requires the configured loopback PostgreSQL; remote databases are refused")
    name = "milo_capacity_" + uid().replace("-", "")[:12]
    admin_url = original.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(original.username)))
    try:
        yield original.set(database=name).render_as_string(hide_password=False), name
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (name,))
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def query_profile(application, owner, *, cursor=None, expected_ids=None):
    """One current-path request profile: SQL count, total cursor time and identity checks."""
    from fastapi.testclient import TestClient
    counts = {"queries": 0, "database_seconds": 0}

    def before(conn, cursor, statement, parameters, context, many):
        context.capacity_start = time.perf_counter()

    def after(conn, cursor, statement, parameters, context, many):
        counts["queries"] += 1
        counts["database_seconds"] += time.perf_counter() - context.capacity_start

    event.listen(application.state.engine, "before_cursor_execute", before)
    event.listen(application.state.engine, "after_cursor_execute", after)
    try:
        with TestClient(application) as client:
            client.cookies.set("session_token", owner["cookie"])
            started = time.perf_counter()
            response = client.get("/v1/ui/bootstrap", params={"conversation_cursor": cursor} if cursor else None)
            body = response.json()
            assert validate_response("bootstrap", response.status_code, body, owner)
            if expected_ids is not None:
                assert [row["id"] for row in body["conversations"]] == expected_ids
            counts.update(wall_ms=round((time.perf_counter() - started) * 1000, 3), contacts_returned=len(body["contacts"]))
    finally:
        event.remove(application.state.engine, "before_cursor_execute", before)
        event.remove(application.state.engine, "after_cursor_execute", after)
    return counts


def inbox_pagination_profile(application, owner):
    """Inspect keyset queries and real route results at early/deep positions."""
    from assistant.assistantui import _cursor
    result = []
    for offset in sorted({0, min(1000, len(owner["readable_ids"]) // 2), max(0, len(owner["readable_ids"]) - 30)}):
        with application.state.session_factory() as db:
            anchor = db.get(Conversation, owner["readable_ids"][offset - 1]) if offset else None
            readable = select(Permission.conversation_id).where(
                Permission.workspace_id == owner["workspace_id"], Permission.read.is_(True),
                or_(Permission.expires_at.is_(None), Permission.expires_at > now()))
            query = select(Conversation.id).where(Conversation.workspace_id == owner["workspace_id"], Conversation.id.in_(readable))
            if anchor:
                query = query.where(or_(Conversation.created_at > anchor.created_at,
                    (Conversation.created_at == anchor.created_at) & (Conversation.id > anchor.id)))
            query = query.order_by(Conversation.created_at, Conversation.id).limit(31)
            compiled = str(query.compile(application.state.engine, compile_kwargs={"literal_binds": True}))
            plan = db.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + compiled)).scalar_one()[0]
            nodes = []

            def walk(node):
                nodes.append({key: node[key] for key in ("Node Type", "Index Name", "Actual Rows", "Actual Loops",
                    "Rows Removed by Filter", "Shared Hit Blocks", "Shared Read Blocks") if key in node})
                for child in node.get("Plans", []):
                    walk(child)

            walk(plan["Plan"])
            cursor = _cursor(anchor) if anchor else None
        profile = query_profile(application, owner, cursor=cursor, expected_ids=owner["readable_ids"][offset:offset + 30])
        result.append({"readable_offset": offset, "route_profile": profile,
                       "plan_execution_ms": plan["Execution Time"], "plan_planning_ms": plan["Planning Time"],
                       "query_has_sql_offset": " OFFSET " in compiled, "plan_nodes": nodes})
    return result


def postgres_pid():
    try:
        result = subprocess.run(["docker", "inspect", "relationship-assistant-postgres-1", "--format", "{{.State.Pid}}"],
                                capture_output=True, text=True, timeout=2, check=True)
        return int(result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owners", type=int, default=64)
    parser.add_argument("--chats", type=int, default=32)
    parser.add_argument("--large-chats", type=int, default=2500)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[8, 32, 64])
    parser.add_argument("--requests-per-user", type=int, default=20)
    parser.add_argument("--think-seconds", type=float, default=0)
    parser.add_argument("--stagger-seconds", type=float, default=0)
    parser.add_argument("--api-port", type=int, default=8017)
    parser.add_argument("--web-port", type=int, default=3117)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", default="read-path")
    args = parser.parse_args()
    if not 2 <= args.owners <= 1000 or max(args.concurrency) > args.owners or min(args.concurrency) < 1:
        parser.error("Use 2..1000 distinct owners and concurrency 1..owners; distributed 50k target is not a local default")
    if not 12 <= args.chats <= 100 or not args.chats <= args.large_chats <= 10000 or not 10 <= args.requests_per_user <= 200:
        parser.error("Bound chats 12..100, large inbox <=10000, per-user samples 10..200")
    if not 0 <= args.think_seconds <= 30 or not 0 <= args.stagger_seconds <= 120 or shutil.disk_usage(ROOT).free < 1024 ** 3:
        parser.error("Require think time 0..30 and at least 1GiB disk before seeding")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {"label": args.label, "timestamp_utc": datetime.now(UTC).isoformat(),
              "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()),
              "mode": "real_http_loopback_postgresql_and_next_proxy", "runs": [],
              "client_setup_excluded": True, "adaptive_abort": "stop stage after >=VU samples and >5% failures (minimum four)",
              "limits": {"external_provider_calls": 0, "real_connected_whatsapp_accounts": 0,
                         "model_calls": 0, "target_50000_concurrent": "NOT_RUN", "dataset_cap_bytes": 500 * 1024 ** 2},
              "host": {"logical_cpus": os.cpu_count(), "cgroup_cpu_max": Path("/sys/fs/cgroup/cpu.max").read_text().strip(),
                       "cgroup_memory_max_bytes": Path("/sys/fs/cgroup/memory.max").read_text().strip(),
                       "disk_free_before_bytes": shutil.disk_usage(ROOT).free},
              "software": {name: version(name) for name in ("fastapi", "SQLAlchemy", "psycopg", "httpx", "uvicorn")},
              "web_build": {"build_id": (ROOT / "apps/web/.next/BUILD_ID").read_text().strip(),
                            "build_id_file_mtime_utc": datetime.fromtimestamp((ROOT / "apps/web/.next/BUILD_ID").stat().st_mtime, UTC).isoformat(),
                            "node_version": subprocess.check_output(["node", "--version"], text=True).strip(),
                            "next_version": json.loads((ROOT / "node_modules/next/package.json").read_text())["version"]},
              "source_files_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
                  "services/api/assistant/assistantui.py", "services/api/assistant/people.py",
                  "services/api/assistant/request_security.py", "services/api/assistant/config.py",
                  "services/api/assistant/db.py", "services/api/assistant/main.py",
                  "apps/web/app/api/[...path]/route.ts", "scripts/capacity_fixture.py", "scripts/capacity_http.py")}}
    processes = []
    with tempfile.TemporaryDirectory(prefix="milo-capacity-", dir="/tmp") as temporary, disposable_database() as (database_url, dbname):
        directory = Path(temporary)
        config = directory / "config.json"
        config.write_text(json.dumps({"database_url": database_url, "encryption_key": Fernet.generate_key().decode(), "web_port": args.web_port,
                                     "redis_url": Settings().redis_url, "rate_namespace": "capacity_" + dbname}))
        config.chmod(0o600)
        settings = fixture_settings(config)
        report["service_configuration"] = {key: getattr(settings, key) for key in (
            "request_limits_mode", "request_max_inflight", "request_control_reserve", "request_thread_tokens",
            "request_rate_api", "request_rate_source_api", "request_rate_global_api", "request_rate_window_seconds",
            "request_rate_control", "request_rate_source_control", "request_rate_global_control",
            "db_pool_size", "db_max_overflow", "db_pool_timeout_seconds", "db_statement_timeout_ms")}
        report["benchmark_overrides"] = {"environment": "test", "request_limits_mode": "redis",
            "request_rate_namespace": "unique capacity namespace, default budget values retained",
            "model_provider": "mock", "enable_external_sends": False, "session_secure": False,
            "auth": "synthetic seeded browser sessions, no OAuth exchange", "database": "disposable loopback PostgreSQL",
            "allowed_origins": "single loopback Next fixture", "source_identity": "actual socket peer 127.0.0.1; no signed synthetic proxy sources",
            "api_processes": 1, "web_processes": 1, "tls": "loopback HTTP only; production TLS is not measured"}
        application = create_app(settings)
        Base.metadata.create_all(application.state.engine)
        owners = seed(application, owners=args.owners, chats=args.chats, large_chats=args.large_chats)
        with application.state.engine.connect() as connection:
            size = connection.execute(text("SELECT pg_database_size(current_database())")).scalar_one()
        if size > 500 * 1024 ** 2:
            raise RuntimeError("Disposable dataset exceeded 500MiB cap; no load was applied")
        report["dataset"] = {"owners": args.owners, "small_owner_chats": args.chats,
                              "large_owner_chats": args.large_chats, "messages_per_populated_chat": 3,
                              "postgres_database_bytes": size, "synthetic_cookie_sessions": args.owners}
        # The diagnostic TestClient closes the engine; HTTP measurements still
        # use an independent real TCP server and newly checked-out connections.
        report["bootstrap_query_profile"] = query_profile(application, owners[0])
        report["large_inbox_query_profile"] = query_profile(application, owners[-1])
        report["large_inbox_pagination"] = inbox_pagination_profile(application, owners[-1])
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        environment = dict(os.environ, MILO_CAPACITY_CONFIG=str(config),
                           MILO_CAPACITY_METRICS=str(directory / "metrics.json"),
                           PYTHONPATH=str(ROOT / "scripts") + os.pathsep + str(ROOT / "services/api"))
        api_log = (directory / "api.log").open("w")
        web_log = (directory / "web.log").open("w")
        try:
            api = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-m", "uvicorn", "capacity_fixture:create_app", "--factory",
                                    "--host", "127.0.0.1", "--port", str(args.api_port), "--no-access-log"],
                                   cwd=ROOT, env=environment, stdout=api_log, stderr=subprocess.STDOUT, start_new_session=True)
            processes.append(api)
            wait_ready(f"http://127.0.0.1:{args.api_port}/health/ready", api)
            web_env = dict(environment, BACKEND_URL=f"http://127.0.0.1:{args.api_port}",
                           PUBLIC_APP_ORIGIN=f"http://127.0.0.1:{args.web_port}", NODE_ENV="production")
            web = subprocess.Popen([shutil.which("node"), str(ROOT / "node_modules/next/dist/bin/next"), "start",
                                    "--hostname", "127.0.0.1", "--port", str(args.web_port)],
                                   cwd=ROOT / "apps/web", env=web_env, stdout=web_log, stderr=subprocess.STDOUT, start_new_session=True)
            processes.append(web)
            wait_ready(f"http://127.0.0.1:{args.web_port}/api/auth/config", web)
            for endpoint, prefix, port in (("direct_api", "/v1", args.api_port), ("next_browser_proxy", "/api", args.web_port)):
                for concurrency in args.concurrency:
                    metric_path = directory / "metrics.json"
                    before_metrics = json.loads(metric_path.read_text()) if metric_path.exists() else {}
                    with StageMonitor([api.pid, web.pid], database_url, postgres_pid(), api_port=args.api_port, web_port=args.web_port) as monitor:
                        result = asyncio.run(workload(f"http://127.0.0.1:{port}", prefix, owners, concurrency=concurrency,
                                                       requests_per_user=args.requests_per_user, think_seconds=args.think_seconds,
                                                       stagger_seconds=args.stagger_seconds))
                    # Let the file-only profiler publish the completed stage;
                    # this delay is excluded from HTTP latency and throughput.
                    time.sleep(0.3)
                    after_metrics = json.loads(metric_path.read_text()) if metric_path.exists() else {}
                    result.update(endpoint=endpoint, resources=monitor.metrics)
                    result["api_query_pool_delta"] = {key: round(after_metrics.get(key, 0) - before_metrics.get(key, 0), 6)
                                                      for key in ("query_count", "query_seconds", "pool_wait_seconds", "pool_checkouts")}
                    result["api_pool_max_wait_observed_seconds"] = after_metrics.get("pool_wait_max_seconds")
                    result["api_pool_peak_checkouts_observed"] = after_metrics.get("checked_out_peak")
                    if result["failed_invariant_checks"]:
                        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=10) as recovery:
                            recovery.cookies.set("session_token", owners[0]["cookie"])
                            started = time.perf_counter()
                            recovered = recovery.get(prefix + "/ui/bootstrap")
                            try:
                                valid = validate_response("bootstrap", recovered.status_code, recovered.json(), owners[0])
                            except (ValueError, KeyError, TypeError):
                                valid = False
                            result["post_overload_recovery_probe"] = {"status": recovered.status_code,
                                "scope_and_identity_valid": valid, "wall_ms": round((time.perf_counter() - started) * 1000, 3),
                                "retries": 0, "excluded_from_stage_throughput": True}
                    report["runs"].append(result)
                    args.output.write_text(json.dumps(report, indent=2) + "\n")
                    print(json.dumps({key: result[key] for key in ("endpoint", "virtual_users", "http_requests", "p95_ms", "failed_invariant_checks")}), flush=True)
                    if result["failed_invariant_checks"]:
                        break
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
            api_log.close()
            web_log.close()
            if (directory / "metrics.json").exists():
                report["api_query_pool_metrics"] = json.loads((directory / "metrics.json").read_text())
            application.state.engine.dispose()
    report["disposable_database_dropped"] = True
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Report: {args.output}; disposable database removed", flush=True)
    if any(run["failed_invariant_checks"] for run in report["runs"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
