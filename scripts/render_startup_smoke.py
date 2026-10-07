"""Exercise Render SQL startup in separate processes using one disposable local DB.

No shared fixture database, provider, model, credential or child log is modified
or published. The PostgreSQL role needs CREATE DATABASE and DROP DATABASE rights.
"""

import argparse
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from cryptography.fernet import Fernet
import psycopg
from psycopg import sql
from sqlalchemy.engine import URL, make_url

from assistant.config import Settings
from assistant.render_entrypoint import MIGRATION_LOCK


ROOT = Path(__file__).resolve().parents[1]
REVISION_MARKER = "SMOKE_REVISION "
WAITER_MARKER = b"SMOKE_WAITER_ABSENT_SCHEMA_QUERY\n"
# The real entrypoint remains unchanged. Observe only revision IDs from its
# ordinary Alembic log records, because its CLI does not configure INFO logging.
MIGRATION_OBSERVER = """
import logging
import runpy
import sys

class RevisionAudit(logging.Handler):
    def emit(self, record):
        if record.msg == 'Running %s' and record.args:
            revision = getattr(getattr(record.args[0], 'revision', None), 'revision', None)
            if revision is not None:
                print('SMOKE_REVISION ' + revision, file=sys.stderr, flush=True)

logger = logging.getLogger('alembic.runtime.migration')
logger.setLevel(logging.INFO)
logger.addHandler(RevisionAudit())
logger.propagate = False
runpy.run_module('assistant.render_entrypoint', run_name='__main__')
"""
WAITER_OBSERVER = """
import runpy
import sys
from sqlalchemy import event
from sqlalchemy.engine import Engine

@event.listens_for(Engine, 'handle_error')
def observe_missing_schema(context):
    if (context.statement == 'SELECT version_num FROM alembic_version'
            and getattr(context.original_exception, 'sqlstate', None) == '42P01'):
        print('SMOKE_WAITER_ABSENT_SCHEMA_QUERY', file=sys.stderr, flush=True)

runpy.run_module('assistant.render_entrypoint', run_name='__main__')
"""


def local_database_url():
    raw = os.environ.get("TEST_DATABASE_URL") or Settings().database_url
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw.removeprefix("postgres://")
    configured = make_url(raw)
    if configured.get_backend_name() != "postgresql":
        raise ValueError("Local PostgreSQL is required")
    if configured.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Only loopback PostgreSQL is accepted")
    addresses = socket.getaddrinfo(configured.host, configured.port or 5432, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(address[4][0]).is_loopback for address in addresses):
        raise ValueError("PostgreSQL host must resolve exclusively to loopback")
    # Rebuild from authority fields: query parameters must not redirect the
    # connection through a service file, alternate host or Unix socket.
    address = min((ipaddress.ip_address(item[4][0]) for item in addresses), key=lambda item: item.version)
    return URL.create("postgresql+psycopg", username=configured.username, password=configured.password,
                      host=configured.host, port=configured.port, database=configured.database,
                      query={"hostaddr": str(address)})


def connect(url, application_name):
    return psycopg.connect(url.set(drivername="postgresql").render_as_string(hide_password=False),
                           autocommit=True, connect_timeout=5, application_name=application_name,
                           options="-c statement_timeout=5000 -c lock_timeout=5000")


def before_deadline(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError("Startup deadline exceeded")


def startup_query(connection, deadline, statement, params=()):
    before_deadline(deadline)
    return connection.execute(statement, params)


def expected_revisions():
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "db/migrations"))
    scripts = ScriptDirectory.from_config(config)
    heads = sorted(scripts.get_heads())
    revisions = sorted(revision.revision for revision in scripts.walk_revisions())
    if len(heads) != 1 or not revisions:
        raise RuntimeError("Startup smoke requires one nonempty migration chain")
    return heads, revisions


def child_environment(url, key, name):
    environment = os.environ.copy()
    for field in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        environment.pop(field, None)
    environment.update(DATABASE_URL=url.render_as_string(hide_password=False), ENCRYPTION_KEY=key,
                       ENVIRONMENT="test", ALLOW_DEV_AUTH="false", MODEL_PROVIDER="disabled",
                       MODEL_API_KEY="", ENABLE_EXTERNAL_SENDS="false", REQUEST_LIMITS_MODE="off",
                       CONNECTOR_GATEWAY_URL="", CONNECTOR_GATEWAY_TOKEN="", WHATSAPP_ACCESS_TOKEN="",
                       WHATSAPP_APP_SECRET="", WHATSAPP_VERIFY_TOKEN="", WHATSAPP_PHONE_NUMBER_ID="",
                       WHATSAPP_AUTHORIZED_OWNER_SUBJECT="", GOOGLE_CLIENT_ID="",
                       GOOGLE_ANDROID_CLIENT_ID="", GOOGLE_IOS_CLIENT_ID="", PGAPPNAME=name,
                       DB_STATEMENT_TIMEOUT_MS="60000")
    return environment


def start_child(operation, url, key, name, timeout, directory, children):
    log = (directory / f"{name}.log").open("w+b")
    if operation == "migrate":
        command = [sys.executable, "-c", MIGRATION_OBSERVER, operation, "--timeout", str(timeout)]
    else:
        command = [sys.executable, "-c", WAITER_OBSERVER, operation, "--timeout", str(timeout)]
    try:
        process = subprocess.Popen(command, cwd=ROOT, env=child_environment(url, key, name),
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    except BaseException:
        log.close()
        raise
    child = {"process": process, "log": log, "operation": operation}
    children.append(child)
    return child


def wait_until(predicate, deadline, children):
    while time.monotonic() < deadline:
        if predicate():
            return
        if any(child["process"].poll() is not None for child in children):
            raise RuntimeError("A startup process ended before the observation barrier")
        time.sleep(0.05)
    raise TimeoutError("Startup observation deadline exceeded")


def finish_child(child, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Startup process deadline exceeded")
    code = child["process"].wait(timeout=remaining)
    if code != 0:
        raise RuntimeError("A startup process failed")
    child["log"].flush()
    if child["log"].seek(0, 2) > 131072:
        raise RuntimeError("Startup child output exceeded its bounded audit size")
    child["log"].seek(0)
    revisions = []
    for line in child["log"]:
        if line.startswith(REVISION_MARKER.encode()):
            revision = line[len(REVISION_MARKER):].decode("ascii").strip()
            if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", revision):
                raise RuntimeError("Invalid revision audit record")
            revisions.append(revision)
    return code, revisions


def schema_signature(connection, deadline):
    def checked(statement, params=()):
        return startup_query(connection, deadline, statement, params)

    tables = [row[0] for row in checked(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
    columns = checked("""
        SELECT table_name, column_name, ordinal_position, data_type, is_nullable, column_default
        FROM information_schema.columns WHERE table_schema='public'
        ORDER BY table_name, ordinal_position
    """).fetchall()
    indexes = checked("""
        SELECT tablename, indexname, indexdef FROM pg_indexes
        WHERE schemaname='public' ORDER BY tablename, indexname
    """).fetchall()
    constraints = checked("""
        SELECT relation.relname, constraint_row.conname, pg_get_constraintdef(constraint_row.oid)
        FROM pg_constraint constraint_row
        JOIN pg_class relation ON relation.oid=constraint_row.conrelid
        JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace
        WHERE namespace.nspname='public' ORDER BY relation.relname, constraint_row.conname
    """).fetchall()
    counts = {table: checked(sql.SQL("SELECT count(*) FROM {}").format(
        sql.Identifier("public", table))).fetchone()[0] for table in tables}
    signature = sha256(json.dumps([tables, columns, indexes, constraints, counts],
                                  sort_keys=True).encode()).hexdigest()
    return signature, tables, counts


def run(args, report):
    configured = local_database_url()
    heads, revisions = expected_revisions()
    name = "milo_render_startup_" + uuid4().hex
    url = configured.set(database=name)
    key = Fernet.generate_key().decode()
    children = []
    admin = observer = None
    created = False
    lock_held = False
    deadline = time.monotonic() + args.timeout
    report.update(expected_heads=heads, expected_revision_count=len(revisions),
                  disposable_database=name, disposable_database_removed=False)
    try:
        report["phase"] = "create_disposable_database"
        before_deadline(deadline)
        admin = connect(configured.set(database="postgres"), "milo_startup_admin")
        startup_query(admin, deadline, sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        created = True
        before_deadline(deadline)
        observer = connect(url, "milo_startup_observer")
        if startup_query(observer, deadline, "SELECT to_regclass('public.alembic_version')").fetchone()[0] is not None:
            raise RuntimeError("Disposable database is not empty")
        if startup_query(observer, deadline, "SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0]:
            raise RuntimeError("Disposable database contains public tables")
        startup_query(observer, deadline, "SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK,))
        lock_held = True
        with tempfile.TemporaryDirectory(prefix="milo-render-startup-", dir=ROOT / ".local") as temporary:
            directory = Path(temporary)
            report["phase"] = "observe_waiter_before_schema"
            waiter_name = "milo_waiter_" + uuid4().hex
            waiter = start_child("wait-schema", url, key, waiter_name, args.timeout, directory, children)

            def waiter_observed():
                # A failed query is followed immediately by ROLLBACK, so a
                # pg_stat_activity sample would usually miss its short duration.
                # This constant marker records the server's missing-table error
                # for the exact helper query, without emitting exception text.
                log_path = Path(waiter["log"].name)
                if log_path.stat().st_size > 131072:
                    raise RuntimeError("Waiter audit exceeded its bounded size")
                observed = WAITER_MARKER in log_path.read_bytes()
                return observed and waiter["process"].poll() is None

            wait_until(waiter_observed, deadline, [waiter])
            if startup_query(observer, deadline, "SELECT to_regclass('public.alembic_version')").fetchone()[0] is not None:
                raise RuntimeError("Schema appeared before migration processes started")
            report["waiter_executed_before_schema"] = True
            report["phase"] = "observe_two_migration_lock_waiters"
            names = ["milo_migrate_" + uuid4().hex for _ in range(2)]
            migrations = [start_child("migrate", url, key, process_name, args.timeout, directory, children)
                          for process_name in names]

            def two_lock_waiters():
                waiting = startup_query(observer, deadline, """
                    SELECT count(DISTINCT activity.pid)
                    FROM pg_locks locks JOIN pg_stat_activity activity ON activity.pid=locks.pid
                    WHERE activity.datname=%s AND activity.application_name=ANY(%s)
                      AND locks.locktype='advisory' AND NOT locks.granted
                      AND locks.classid=%s AND locks.objid=%s AND locks.objsubid=1
                """, (name, names, MIGRATION_LOCK >> 32, MIGRATION_LOCK & 0xFFFFFFFF)).fetchone()[0]
                return waiting == 2

            wait_until(two_lock_waiters, deadline, [waiter, *migrations])
            report["two_separate_migrators_observed_waiting_on_same_lock"] = True
            startup_query(observer, deadline, "SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK,))
            lock_held = False
            report["phase"] = "verify_concurrent_migrations"
            outcomes = [finish_child(child, deadline) for child in migrations]
            waiter_code, waiter_revisions = finish_child(waiter, deadline)
            if waiter_revisions or Counter(revision for _, applied in outcomes for revision in applied) != Counter(revisions):
                raise RuntimeError("Concurrent migration revision audit differs from repository chain")
            actual_heads = sorted(row[0] for row in startup_query(observer, deadline,
                                                                "SELECT version_num FROM alembic_version"))
            if actual_heads != heads:
                raise RuntimeError("Concurrent migrations did not reach exact repository head")
            signature, tables, counts = schema_signature(observer, deadline)
            if len(tables) <= 1 or counts.get("alembic_version") != 1 or any(
                    count for table, count in counts.items() if table != "alembic_version"):
                raise RuntimeError("Unexpected rows in migrated synthetic schema")
            report.update(migration_exit_codes=[code for code, _ in outcomes],
                          migration_process_ids=[child["process"].pid for child in migrations],
                          upgrades_per_process=[len(applied) for _, applied in outcomes],
                          each_revision_applied_once=True, actual_heads=actual_heads,
                          waiter_exit_code=waiter_code, waiter_process_id=waiter["process"].pid,
                          schema_tables=len(tables), business_rows=0, schema_sha256=signature)
            report["phase"] = "verify_repeated_migration"
            repeated = start_child("migrate", url, key, "milo_repeat_" + uuid4().hex,
                                   args.timeout, directory, children)
            repeated_code, repeated_revisions = finish_child(repeated, deadline)
            if repeated_revisions or schema_signature(observer, deadline)[0] != signature:
                raise RuntimeError("Repeated migration changed schema or reapplied revisions")
            if sorted(row[0] for row in startup_query(observer, deadline,
                                                     "SELECT version_num FROM alembic_version")) != heads:
                raise RuntimeError("Repeated migration changed exact repository head")
            report.update(repeated_migration_exit_code=repeated_code, repeated_upgrade_count=0,
                          repeated_migration_idempotent=True,
                          database_bytes=startup_query(observer, deadline,
                                                       "SELECT pg_database_size(current_database())").fetchone()[0])
            if report["database_bytes"] >= 100_000_000:
                raise RuntimeError("Disposable startup schema exceeded its disk budget")
    finally:
        report["phase"] = "cleanup" if report.get("repeated_migration_idempotent") else report["phase"]
        cleanup_failures = []
        for child in children:
            try:
                if child["process"].poll() is None:
                    child["process"].terminate()
                    try:
                        child["process"].wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        child["process"].kill()
                        child["process"].wait(timeout=3)
            except Exception:
                cleanup_failures.append("owned_child_stop")
            finally:
                try:
                    child["log"].close()
                except Exception:
                    cleanup_failures.append("temporary_log_close")
        if observer is not None:
            try:
                if lock_held:
                    observer.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK,))
            except Exception:
                cleanup_failures.append("supervisor_lock_release")
            finally:
                try:
                    observer.close()
                except Exception:
                    cleanup_failures.append("supervisor_connection_close")
        if admin is not None:
            try:
                if created:
                    try:
                        admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                                      "WHERE datname=%s AND pid<>pg_backend_pid()", (name,))
                    except Exception:
                        cleanup_failures.append("owned_database_connection_stop")
                    try:
                        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
                        report["disposable_database_removed"] = True
                    except Exception:
                        cleanup_failures.append("owned_database_drop")
            finally:
                try:
                    admin.close()
                except Exception:
                    cleanup_failures.append("admin_connection_close")
        report["owned_processes_stopped"] = all(child["process"].poll() is not None for child in children)
        if cleanup_failures:
            report["cleanup_failure_stages"] = sorted(set(cleanup_failures))
            raise RuntimeError("Disposable startup cleanup failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=".local/render-startup-smoke.json")
    parser.add_argument("--timeout", type=int, default=90, help="Startup deadline, excluding cleanup, 10–300 seconds")
    args = parser.parse_args()
    if not 10 <= args.timeout <= 300:
        parser.error("timeout must be between 10 and 300 seconds")
    started = time.monotonic()
    report = {"passed": False, "synthetic_only": True, "external_provider_calls": 0, "model_calls": 0,
              "started_at": datetime.now(UTC).isoformat(), "phase": "validate_local_configuration"}
    try:
        (ROOT / ".local").mkdir(mode=0o700, exist_ok=True)
        run(args, report)
        report.update(passed=True, phase="complete")
    except Exception as error:
        # Never emit SQL, child output, connection strings or exception messages.
        report["failure_type"] = type(error).__name__
    report.update(finished_at=datetime.now(UTC).isoformat(), elapsed_seconds=round(time.monotonic() - started, 3))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    output.chmod(0o600)
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
