"""Encrypted PostgreSQL backup/restore and migration repeatability, synthetic only.

Uses two disposable local databases and the local PostgreSQL Docker container.
No configured development/production row, provider or model is contacted.
Credentials and the temporary encryption key are never printed or persisted.
"""

import argparse
from datetime import timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from assistant.config import Settings
from assistant.db import Base, now
from assistant.jobs_models import AuthorizedJob, JobRun
from assistant.main import create_app
from assistant.models import Draft, Message, SendAttempt, Suppression
from demo import checked, incoming, seed_owner


def _signature(app):
    with app.state.session_factory() as db:
        counts = {table.name: db.scalar(select(func.count()).select_from(table)) for table in Base.metadata.sorted_tables}
        # Includes decrypted private content and immutable approval/delivery state.
        private = {"messages": [{"id": row.id, "text": row.text, "deleted": row.deleted, "revision": row.revision}
                                 for row in db.scalars(select(Message).order_by(Message.id))],
                   "drafts": [{"id": row.id, "text": row.text, "hash": row.content_hash, "approved": row.approved_hash,
                               "status": row.status} for row in db.scalars(select(Draft).order_by(Draft.id))],
                   "jobs": [{"id": row.id, "content": row.content, "hash": row.content_hash, "status": row.status,
                              "runs": row.runs_done} for row in db.scalars(select(AuthorizedJob).order_by(AuthorizedJob.id))],
                   "attempts": [{"id": row.id, "status": row.status, "provider_id": row.provider_message_id}
                                for row in db.scalars(select(SendAttempt).order_by(SendAttempt.id))],
                   "suppression": [{"id": row.id, "source_ids": row.source_message_ids}
                                   for row in db.scalars(select(Suppression).order_by(Suppression.id))]}
        digest = sha256(json.dumps(private, sort_keys=True).encode()).hexdigest()
        return counts, digest


def _migrate(url, key, log):
    env = os.environ.copy()
    env.update(DATABASE_URL=url, ENCRYPTION_KEY=key, ENVIRONMENT="test", ALLOW_DEV_AUTH="false",
               MODEL_PROVIDER="disabled", ENABLE_EXTERNAL_SENDS="false", REQUEST_LIMITS_MODE="off")
    for _ in range(2):
        result = subprocess.run([".venv/bin/alembic", "upgrade", "head"], env=env, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError("Disposable PostgreSQL migration failed; inspect redacted migration log")


def _seed(app, settings, messages):
    with TestClient(app) as client:
        workspace, connector, conversation = seed_owner(client)
        cid = conversation["id"]
        headers = {"Authorization": f"Bearer {settings.internal_service_token}"}
        sentinel = "PRIVATE_SYNTHETIC_BACKUP_MARKER_" + uuid4().hex
        # Imports have no live authority and repeated uploads remain idempotent.
        history = "\n".join(f"07/10/2026, 09:00 - {'Owner' if index == 0 else 'Friend'}: {sentinel} {index}" for index in range(messages))
        body = {"conversation_id": cid, "text": history, "owner_sender_label": "Owner", "date_order": "DMY", "timezone": "UTC"}
        first = checked(client, "POST", "/imports", json=body)
        again = checked(client, "POST", "/imports", json=body)
        assert again["replayed"] and first["message_count"] == messages
        live = checked(client, "POST", "/internal/connector-events", json=incoming(connector["id"], cid, "backup-live"), headers=headers)
        checked(client, "POST", "/internal/connector-events", json=incoming(connector["id"], cid, "backup-live"), headers=headers)
        draft = checked(client, "POST", f"/conversations/{cid}/owner-drafts", json={"text": "Synthetic owner-authored acknowledgement."})
        checked(client, "POST", f"/drafts/{draft['id']}/approve", json={"content_hash": draft["content_hash"]})
        sent = checked(client, "POST", f"/drafts/{draft['id']}/dispatch")
        repeat = checked(client, "POST", f"/drafts/{draft['id']}/dispatch")
        assert sent["attempt_id"] == repeat["attempt_id"] and sent["status"] == "accepted"
        memory = checked(client, "POST", f"/conversations/{cid}/memories", json={"text": "Synthetic disposable fact",
                                                                 "source_message_ids": [live["message_id"]]})
        checked(client, "DELETE", f"/memories/{memory['id']}")
        raw = checked(client, "GET", f"/conversations/{cid}/messages", params={"limit": 200})
        evidence = checked(client, "GET", f"/conversations/{cid}/messages", params={"limit": 200, "derived_evidence": True})
        assert live["message_id"] in {row["id"] for row in raw}
        assert live["message_id"] not in {row["id"] for row in evidence}
        due = now() + timedelta(minutes=5)
        job = checked(client, "POST", "/jobs", json={"workspace_id": workspace["id"], "conversation_id": cid,
                      "idempotency_key": "backup-paused-exact-job", "purpose": "Synthetic restore safety",
                      "action_kind": "SEND_TEXT", "content": "Synthetic exact follow-up.", "due_at": due.isoformat(),
                      "expires_at": (due + timedelta(hours=1)).isoformat(), "timezone": "UTC"})
        checked(client, "POST", "/pause-all", params={"workspace_id": workspace["id"]})
        with app.state.session_factory() as db:
            db.get(AuthorizedJob, job["id"]).due_at = now() - timedelta(seconds=1)
            db.commit()
        held = checked(client, "POST", "/internal/jobs/run-due", headers=headers)
        assert held[0]["status"] == "held"
        with app.state.session_factory() as db:
            assert db.scalar(select(func.count()).select_from(JobRun)) == 0
            assert db.scalar(select(func.count()).select_from(SendAttempt)) == 1
        return sentinel, {"historical_messages": messages, "live_messages": 1, "send_attempts": 1,
                          "import_replay": True, "send_replay": True, "source_forget": True, "paused_job_preserved": True}


def run(args):
    configured = make_url(Settings().prepare().database_url)
    if configured.get_backend_name() != "postgresql" or configured.host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("This synthetic exercise accepts only local PostgreSQL")
    if not args.container.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Invalid local container name")
    key = Fernet.generate_key().decode()
    names = [f"milo_storage_{uuid4().hex}", f"milo_restore_{uuid4().hex}"]
    engine_apps = []
    started = time.monotonic()
    report = {"synthetic_only": True, "external_provider_calls": 0, "model_calls": 0,
              "transport": "mock", "created_at": now().isoformat()}
    admin = psycopg.connect(configured.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False), autocommit=True)
    try:
        for name in names:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        with tempfile.TemporaryDirectory(prefix="milo-encrypted-backup-", dir=".local") as temporary:
            backup = Path(temporary) / "synthetic.sql"
            migration_log = Path(temporary) / "migrations.log"
            source_url, restore_url = [configured.set(database=name).render_as_string(hide_password=False) for name in names]
            with migration_log.open("w") as log:
                _migrate(source_url, key, log)
            settings = Settings(_env_file=None, environment="test", database_url=source_url, encryption_key=key,
                                allow_dev_auth=True, session_secure=False, model_provider="mock", request_limits_mode="off",
                                internal_service_token=secrets.token_urlsafe(32), enable_external_sends=False,
                                connector_gateway_url="", whatsapp_access_token="", model_api_key="")
            source_app = create_app(settings)
            engine_apps.append(source_app)
            sentinel, seed_report = _seed(source_app, settings, args.messages)
            expected = _signature(source_app)
            with source_app.state.engine.connect() as connection:
                size = connection.scalar(text("SELECT pg_database_size(current_database())"))
                revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert size < 150_000_000, "Disposable synthetic fixture exceeds disk budget"
            with backup.open("wb") as handle:
                result = subprocess.run(["docker", "exec", args.container, "pg_dump", "-U", configured.username, "--format=plain", "--no-owner", "--no-acl", names[0]], stdout=handle, stderr=subprocess.PIPE)
            if result.returncode:
                raise RuntimeError("Synthetic pg_dump failed")
            backup_bytes = backup.read_bytes()
            assert sentinel.encode() not in backup_bytes, "Private message plaintext appeared in backup"
            assert b"gAAAA" in backup_bytes, "Expected encrypted private payloads in SQL backup"
            with backup.open("rb") as handle:
                result = subprocess.run(["docker", "exec", "-i", args.container, "psql", "-U", configured.username,
                                         "-d", names[1], "--single-transaction", "-v", "ON_ERROR_STOP=1"], stdin=handle, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if result.returncode:
                raise RuntimeError("Synthetic backup restore failed")
            restore_settings = settings.model_copy(update={"database_url": restore_url})
            restored = create_app(restore_settings)
            engine_apps.append(restored)
            assert _signature(restored) == expected, "Restored private data or durable authority differs"
            with migration_log.open("a") as log:
                _migrate(restore_url, key, log)
            # A restart tick cannot submit the paused durable job.
            with TestClient(restored) as client:
                result = checked(client, "POST", "/internal/jobs/run-due", headers={"Authorization": f"Bearer {settings.internal_service_token}"})
                assert result[0]["status"] == "held"
            assert _signature(restored) == expected
            wrong = create_app(restore_settings.model_copy(update={"encryption_key": Fernet.generate_key().decode()}))
            engine_apps.append(wrong)
            try:
                _signature(wrong)
            except InvalidToken:
                pass
            else:
                raise AssertionError("Restored encrypted content decrypted with wrong key")
            report.update(seed_report, tables=len(expected[0]), row_counts=expected[0], migration_revision=revision,
                          migration_upgrade_runs=4, encrypted_plaintext_absent=True, correct_key_restore=True,
                          wrong_key_rejected=True, restore_state_sha256=expected[1], backup_bytes=len(backup_bytes),
                          backup_sha256=sha256(backup_bytes).hexdigest(), database_bytes=size, elapsed_seconds=round(time.monotonic()-started, 3))
    finally:
        for application in engine_apps:
            application.state.engine.dispose()
        for name in names:
            admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (name,))
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))
        admin.close()
    report["disposable_databases_removed"] = True
    report["temporary_backup_removed"] = True
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "row_counts"}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="relationship-assistant-postgres-1")
    parser.add_argument("--messages", type=int, default=1000, choices=range(1, 5001))
    parser.add_argument("--output", default=".local/storage-reliability.json")
    run(parser.parse_args())
