"""Disposable local diagnostics, no providers or deployment mutations."""

import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import statistics
import subprocess
import time
from uuid import uuid4

from cryptography.fernet import Fernet
from redis.asyncio import Redis
from sqlalchemy import insert, text
from assistant.config import Settings
from assistant.db import Base, make_database, now, uid
from assistant.models import User, Workspace, Connector, Conversation, Message
from assistant.people_models import UsageLedger
from assistant.request_security import RequestRateLimiter

try:
    from .capacity_http import disposable_database
except ImportError:
    from capacity_http import disposable_database

ROOT = Path(__file__).resolve().parents[1]


def plan(connection, query, parameters):
    runs = []
    for _ in range(5):
        data = connection.execute(
            text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query), parameters
        ).scalar_one()[0]
        nodes = []

        def walk(node):
            nodes.append(
                {
                    key: node[key]
                    for key in (
                        "Node Type",
                        "Index Name",
                        "Actual Rows",
                        "Actual Loops",
                        "Rows Removed by Filter",
                        "Shared Hit Blocks",
                        "Shared Read Blocks",
                    )
                    if key in node
                }
            )
            for child in node.get("Plans", []):
                walk(child)

        walk(data["Plan"])
        runs.append({"execution_ms": data["Execution Time"], "nodes": nodes})
    return {
        "repeats": 5,
        "median_execution_ms": statistics.median(row["execution_ms"] for row in runs),
        "runs": runs,
    }


def sql_probe():
    report = {
        "mode": "diagnostic_local_postgresql_explain_only",
        "actual_authenticated_users": 0,
        "requests": 0,
        "external_provider_calls": 0,
        "production_indexes_modified": False,
        "dataset_disposable": True,
    }
    with disposable_database() as (database_url, _):
        settings = Settings(
            _env_file=None,
            environment="test",
            database_url=database_url,
            encryption_key=Fernet.generate_key().decode(),
            model_provider="disabled",
            request_limits_mode="off",
        )
        engine, _ = make_database(settings)
        try:
            Base.metadata.create_all(engine)
            stamp = now()
            user_id, workspace_id, connector_id, chat_id = (uid() for _ in range(4))
            workspace_ids = [workspace_id] + [uid() for _ in range(99)]
            with engine.begin() as connection:
                connection.execute(
                    insert(User),
                    [
                        {
                            "id": user_id,
                            "subject": "synthetic-scale-probe",
                            "email": "scale-probe@example.invalid",
                        }
                    ],
                )
                connection.execute(
                    insert(Workspace),
                    [
                        {"id": key, "owner_id": user_id, "name": "Synthetic scale probe", "timezone": "UTC"}
                        for key in workspace_ids
                    ],
                )
                connection.execute(
                    insert(Connector),
                    [
                        {
                            "id": connector_id,
                            "workspace_id": workspace_id,
                            "provider": "mock",
                            "account_id": "synthetic-scale-probe",
                            "owner_sender_id": "synthetic-owner",
                        }
                    ],
                )
                connection.execute(
                    insert(Conversation),
                    [
                        {
                            "id": chat_id,
                            "workspace_id": workspace_id,
                            "connector_id": connector_id,
                            "provider_chat_id": "synthetic-peer",
                            "title": "Synthetic scale probe",
                            "kind": "contact",
                        }
                    ],
                )
                for start in range(0, 50000, 2000):
                    rows = []
                    for index in range(start, min(start + 2000, 50000)):
                        day = stamp - timedelta(days=(index // 100) % 20)
                        rows.append(
                            {
                                "workspace_id": workspace_ids[index % 100],
                                "operation_key": f"synthetic-operation-{index}",
                                "kind": "model_mock",
                                "window_day": day.date().isoformat(),
                                "status": "released" if index % 7 == 0 else "consumed",
                                "action_units": 1,
                                "token_units": 100,
                                "cost_microusd": 0,
                                "created_at": day,
                            }
                        )
                    connection.execute(insert(UsageLedger), rows)
                for start in range(0, 40000, 2000):
                    rows = [
                        {
                            "workspace_id": workspace_id,
                            "connector_id": connector_id,
                            "conversation_id": chat_id,
                            "provider_message_id": f"synthetic-{index}",
                            "sender_id": "synthetic-peer",
                            "direction": "inbound",
                            "origin": "history",
                            "author_kind": "contact_human",
                            "text": "Synthetic probe text. " + "x" * 480,
                            "provider_timestamp": stamp - timedelta(seconds=index),
                            "received_at": stamp - timedelta(days=index % 90),
                            "deleted": False,
                        }
                        for index in range(start, min(start + 2000, 40000))
                    ]
                    connection.execute(insert(Message), rows)
                # Compare the old access path with the exact new production index.
                # Only this disposable database is modified.
                connection.execute(text("DROP INDEX ix_messages_read_page"))
                connection.execute(text("DROP INDEX ix_messages_retention_page"))
                connection.execute(text("ANALYZE"))
            usage = "SELECT coalesce(sum(action_units),0), coalesce(sum(token_units),0), coalesce(sum(cost_microusd),0) FROM usage_ledger WHERE workspace_id=:workspace AND window_day=:day AND status IN ('reserved','consumed','uncertain')"
            messages = "SELECT * FROM messages WHERE workspace_id=:workspace AND conversation_id=:chat AND deleted IS FALSE ORDER BY provider_timestamp DESC,id DESC LIMIT 50"
            retention = "SELECT * FROM messages WHERE workspace_id=:workspace AND deleted IS FALSE AND received_at<:cutoff ORDER BY received_at,id LIMIT 500"
            parameters = {
                "workspace": workspace_id,
                "day": stamp.date().isoformat(),
                "chat": chat_id,
                "cutoff": stamp - timedelta(days=30),
            }
            report["queries"] = {"usage": usage, "messages": messages, "retention": retention}
            report["parameters"] = {
                "workspace": "one synthetic workspace of 100",
                "chat": "one synthetic conversation",
                "day": "current UTC day",
                "cutoff": "current UTC time minus 30 days",
            }
            with engine.connect() as connection:
                report["usage_before"] = plan(connection, usage, parameters)
                report["messages_before"] = plan(connection, messages, parameters)
                report["retention_before"] = plan(connection, retention, parameters)
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "CREATE INDEX diagnostic_usage_workspace_day_status ON usage_ledger (workspace_id,window_day,status)"
                    )
                )
                connection.execute(
                    text(
                        "CREATE INDEX ix_messages_read_page ON messages (workspace_id,conversation_id,provider_timestamp,id) WHERE deleted IS FALSE"
                    )
                )
                connection.execute(
                    text(
                        "CREATE INDEX ix_messages_retention_page ON messages (workspace_id,received_at,id) WHERE deleted IS FALSE"
                    )
                )
                connection.execute(text("ANALYZE"))
            with engine.connect() as connection:
                report["usage_after"] = plan(connection, usage, parameters)
                report["messages_after"] = plan(connection, messages, parameters)
                report["retention_after"] = plan(connection, retention, parameters)
                report["database_bytes"] = connection.execute(
                    text("SELECT pg_database_size(current_database())")
                ).scalar_one()
                report["rows"] = {
                    "usage_ledger": 50000,
                    "messages": 40000,
                    "workspaces": 100,
                    "distinct_usage_days": 20,
                }
                report["candidate_indexes"] = {
                    "message_page": "implemented production migration ae912f73c804; no deployment performed",
                    "usage_workspace_day_status": "diagnostic only",
                    "message_retention": "implemented production migration ae912f73c804; no deployment performed",
                }
        finally:
            engine.dispose()
    report["disposable_database_dropped"] = True
    return report


async def redis_probe():
    prefix = "milo-launch-scale-" + uuid4().hex
    local = "redis://127.0.0.1:6379/0"
    proxy_key = secrets.token_urlsafe(32)
    config = Settings(
        _env_file=None,
        environment="test",
        model_provider="disabled",
        request_limits_mode="redis",
        redis_url=local,
        request_rate_namespace=prefix,
        trusted_proxy_key=proxy_key,
    )
    limiters = [RequestRateLimiter(config), RequestRateLimiter(config)]
    inspector = Redis.from_url(local, max_connections=4)
    started = time.perf_counter()
    window = int(time.time() // 60)
    report = {
        "mode": "two_local_redis_limiter_instances_synthetic_presented_credentials",
        "sql_engines": 0,
        "authenticated_owners": 0,
        "external_provider_calls": 0,
        "window_seconds": 60,
        "tested_api_source_limit": config.request_rate_source_api,
        "tested_auth_source_limit": config.request_rate_auth_issues,
        "tested_control_source_limit": config.request_rate_source_control,
        "capacity_50000": "NOT_MEASURED",
    }
    try:
        await asyncio.gather(*(limiter.ready() for limiter in limiters))

        async def execute(count, path, control=False, signed=False):
            accepted = 0

            def headers_for(index):
                headers = {
                    b"cookie": f"session_token=synthetic-{path}-{index}".encode(),
                    b"x-forwarded-for": f"198.51.100.{index % 250 + 1}".encode(),
                    b"x-milo-rate-source": f"198.51.100.{index % 250 + 1}".encode(),
                }
                if signed:
                    source = headers[b"x-milo-rate-source"].decode()
                    stamp = str(int(time.time()))
                    headers[b"x-milo-rate-timestamp"] = stamp.encode()
                    headers[b"x-milo-rate-signature"] = (
                        hmac.new(proxy_key.encode(), f"{stamp}.{source}".encode(), hashlib.sha256)
                        .hexdigest()
                        .encode()
                    )
                return headers

            for start in range(0, count, 64):
                outcomes = await asyncio.gather(
                    *(
                        limiters[index % 2].allow(
                            {
                                "type": "http",
                                "path": path,
                                "method": "POST" if control else "GET",
                                "client": ("192.0.2.100", 1000),
                            },
                            headers_for(index),
                            control=control,
                        )
                        for index in range(start, min(start + 64, count))
                    )
                )
                accepted += sum(outcomes)
            return {"submitted": count, "accepted": accepted, "rejected": count - accepted}

        report["shared_api_source"] = await execute(6064, "/v1/me")
        report["shared_auth_source"] = await execute(64, "/v1/auth/nonce")
        report["shared_control_source"] = await execute(192, "/v1/pause-all", True)
        report["verified_signed_auth_sources"] = await execute(64, "/v1/auth/nonce", signed=True)
        report["verified_signed_control_sources"] = await execute(192, "/v1/pause-all", True, signed=True)
        report["same_rate_window"] = window == int(time.time() // 60)
        if report["same_rate_window"]:
            assert report["shared_api_source"]["accepted"] == 6000
            assert report["shared_auth_source"]["accepted"] == 30
            assert report["shared_control_source"]["accepted"] == 120
            assert report["verified_signed_auth_sources"]["accepted"] == 64
            assert report["verified_signed_control_sources"]["accepted"] == 192
        report["elapsed_seconds"] = round(time.perf_counter() - started, 4)
        report["counter_keys"] = len(
            [key async for key in inspector.scan_iter(match=prefix + ":*", count=500)]
        )
    finally:
        batch = []
        async for key in inspector.scan_iter(match=prefix + ":*", count=500):
            batch.append(key)
            if len(batch) >= 500:
                await inspector.delete(*batch)
                batch = []
        if batch:
            await inspector.delete(*batch)
        assert not [key async for key in inspector.scan_iter(match=prefix + ":*", count=500)]
        await asyncio.gather(*(limiter.close() for limiter in limiters))
        await inspector.aclose()
    report["owned_counter_keys_removed"] = True
    return report


if __name__ == "__main__":
    target = ROOT / ".local/storage-scale-diagnostics.json"
    target.parent.mkdir(exist_ok=True)
    out = {
        "started_at": datetime.now(UTC).isoformat(),
        "source_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
        ),
        "capacity_50000": "NOT_MEASURED",
        "sql": sql_probe(),
    }
    target.write_text(json.dumps(out, indent=2) + "\n")
    out.update(
        {
            "redis": asyncio.run(redis_probe()),
            "fernet_ciphertext_bytes": {
                str(length): len(Fernet(Fernet.generate_key()).encrypt(b"x" * length))
                for length in (250, 500, 1000, 4000)
            },
            "source_hashes": {
                name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                for name in (
                    "services/api/assistant/models.py",
                    "services/api/assistant/people_models.py",
                    "services/api/assistant/request_security.py",
                    "services/api/assistant/companion.py",
                )
            },
        }
    )
    target.write_text(json.dumps(out, indent=2) + "\n")
    print(
        json.dumps(
            {
                "report": str(target),
                "sql_database_dropped": out["sql"]["disposable_database_dropped"],
                "redis_keys_removed": out["redis"]["owned_counter_keys_removed"],
            }
        )
    )
