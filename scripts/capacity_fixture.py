"""Disposable PostgreSQL fixture/server for real HTTP capacity measurements.

No identity provider, model or messaging provider is contacted. Generated session
credentials are synthetic, file permissions are private and output never contains them.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import json
import os
from pathlib import Path
import secrets
import threading
import time

from sqlalchemy import event, insert, text

from assistant.auth import digest
from assistant.config import Settings
from assistant.db import now, uid
from assistant.main import create_app as production_app
from assistant.models import (AuditEvent, Connector, Conversation, Draft, Memory, Message,
                              Permission, SessionRecord, StyleProfile, Suppression, Task, User, Workspace)
from assistant.native_models import MessageContext
from assistant.people_models import ContactSource, LocalContact


def fixture_settings(path):
    config = json.loads(Path(path).read_text())
    return Settings(_env_file=None, environment="test", database_url=config["database_url"],
                    encryption_key=config["encryption_key"], model_provider="mock",
                    allow_dev_auth=False, session_secure=False, enable_external_sends=False,
                    allowed_origins=f"http://127.0.0.1:{config['web_port']}",
                    request_limits_mode="redis", redis_url=config["redis_url"],
                    request_rate_namespace=config["rate_namespace"])


def seed(application, *, owners=64, chats=32, messages=3, large_chats=2500):
    """Bulk fixture setup; SQL bypasses HTTP only for setup, never measured work."""
    stamp = now()
    users = []
    for owner_number in range(owners):
        user_id, workspace_id, connector_id = uid(), uid(), uid()
        token = secrets.token_urlsafe(32)
        user = {"owner_id": user_id, "workspace_id": workspace_id, "connector_id": connector_id,
                "cookie": token, "chat_ids": [], "readable_ids": [], "contact_ids": [],
                "message_ids": {}, "memory_ids": []}
        rows = {model: [] for model in (User, SessionRecord, Workspace, Connector, Conversation, Permission,
                                       Message, MessageContext, Memory, StyleProfile, Draft, LocalContact,
                                       ContactSource, Suppression, Task, AuditEvent)}
        rows[User].append(dict(id=user_id, subject=f"capacity:{user_id}", email=f"owner-{owner_number}@synthetic.invalid",
                               display_name=f"Synthetic owner {owner_number}"))
        rows[SessionRecord].append(dict(user_id=user_id, token_hash=digest(token), csrf_hash=digest("unused-read-only"),
                                        expires_at=stamp + timedelta(hours=12)))
        rows[Workspace].append(dict(id=workspace_id, owner_id=user_id, name=f"Synthetic workspace {owner_number}",
                                    timezone="UTC", paused=True, pause_generation=0))
        rows[Connector].append(dict(id=connector_id, workspace_id=workspace_id, provider="mock",
                                    account_id=f"capacity-{connector_id}", owner_sender_id="15559999999",
                                    status="connected", capabilities={"send_text": "supported"},
                                    lease_expires_at=stamp + timedelta(hours=12)))
        count = large_chats if owner_number == owners - 1 else chats
        for chat_number in range(count):
            chat_id = uid()
            created = stamp - timedelta(days=30) + timedelta(milliseconds=chat_number)
            denied = chat_number == 10
            expired = chat_number == 11
            rows[Conversation].append(dict(id=chat_id, workspace_id=workspace_id, connector_id=connector_id,
                                          provider_chat_id=f"peer-{owner_number}-{chat_number}",
                                          title=f"Synthetic {owner_number}/{chat_number}", kind="contact",
                                          created_at=created, revision=1, control_epoch=0, control_state="DRAFT_MODE"))
            rows[Permission].append(dict(workspace_id=workspace_id, conversation_id=chat_id, read=not denied,
                                         retain=True, learn=True, draft=True, send=False, share=False,
                                         expires_at=stamp - timedelta(days=1) if expired else None))
            user["chat_ids"].append(chat_id)
            if not denied and not expired:
                user["readable_ids"].append(chat_id)
            # A large inbox is metadata-heavy; populated first-page chats retain
            # realistic source/profile/contact data without an unbounded seed.
            populated = chat_number < chats
            ids = []
            for message_number in range(messages if populated else 0):
                message_id = uid()
                peer = str(15550000000 + owner_number * 100000 + chat_number)
                rows[Message].append(dict(id=message_id, workspace_id=workspace_id, connector_id=connector_id,
                                         conversation_id=chat_id, provider_message_id=f"load-{message_id}",
                                         sender_id=peer, direction="inbound", origin="live", author_kind="contact_human",
                                         text=f"Synthetic scoped message {owner_number}/{chat_number}/{message_number}",
                                         provider_timestamp=stamp - timedelta(minutes=message_number), revision=1))
                rows[MessageContext].append(dict(workspace_id=workspace_id, connector_id=connector_id,
                                                conversation_id=chat_id, message_id=message_id,
                                                sender_identity={"id": "wrong-peer" if chat_number == 8 else peer},
                                                expires_at=stamp - timedelta(minutes=1) if chat_number == 9 else None))
                ids.append(message_id)
            user["message_ids"][chat_id] = ids if chat_number != 9 else []
            if not ids:
                continue
            contact_id, memory_id = uid(), uid()
            rows[LocalContact].append(dict(id=contact_id, workspace_id=workspace_id, connector_id=connector_id,
                                          provider_identity=peer, display_name=f"Synthetic peer {owner_number}/{chat_number}"))
            rows[ContactSource].append(dict(workspace_id=workspace_id, contact_id=contact_id, conversation_id=chat_id,
                                           message_id=ids[0], source_revision=1))
            if chat_number == 7:
                rows[Suppression].append(dict(workspace_id=workspace_id, conversation_id=chat_id,
                                              content_hash="synthetic-forget", source_message_ids=[ids[0]]))
            if chat_number not in {7, 8, 9, 10, 11}:
                user["contact_ids"].append(contact_id)
            rows[Memory].append(dict(id=memory_id, workspace_id=workspace_id, conversation_id=chat_id,
                                    text=f"Synthetic owner {owner_number} preference {chat_number}", status="confirmed",
                                    source_message_ids=[ids[0]], source_revision={ids[0]: 1}))
            user["memory_ids"].append(memory_id)
            rows[StyleProfile].append(dict(workspace_id=workspace_id, conversation_id=chat_id, sample_count=messages,
                                          owner_rules=["Synthetic scoped rule"], evidence_message_ids=[ids[0]]))
            rows[Draft].append(dict(workspace_id=workspace_id, conversation_id=chat_id, recipient_id=f"peer-{owner_number}-{chat_number}",
                                   text="Synthetic unsent owner draft", model_version="mock-v1", content_hash="0" * 64,
                                   conversation_revision=1, control_epoch=0, permission_version=1, pause_generation=0,
                                   connector_fence=1))
        rows[Task].append(dict(workspace_id=workspace_id, conversation_id=None, title="Synthetic owner-only reminder"))
        rows[AuditEvent].append(dict(workspace_id=workspace_id, actor_id=user_id, action="capacity.fixture",
                                     resource_id=workspace_id, details={"synthetic": True}))
        with application.state.engine.begin() as connection:
            for model, values in rows.items():
                if values:
                    connection.execute(insert(model), values)
        users.append(user)
    with application.state.engine.begin() as connection:
        connection.execute(text("ANALYZE"))
    return users


def create_app():
    application = production_app(fixture_settings(os.environ["MILO_CAPACITY_CONFIG"]))
    metrics = {"query_count": 0, "query_seconds": 0, "pool_wait_seconds": 0,
               "pool_wait_max_seconds": 0, "pool_checkouts": 0, "checked_out": 0, "checked_out_peak": 0}
    lock = threading.Lock()
    pool = application.state.engine.pool
    original_get = pool._do_get

    def timed_get():
        started = time.perf_counter()
        try:
            return original_get()
        finally:
            waited = time.perf_counter() - started
            with lock:
                metrics["pool_wait_seconds"] += waited
                metrics["pool_wait_max_seconds"] = max(metrics["pool_wait_max_seconds"], waited)

    pool._do_get = timed_get

    @event.listens_for(application.state.engine, "before_cursor_execute")
    def before_query(connection, cursor, statement, parameters, context, executemany):
        context.capacity_started = time.perf_counter()

    @event.listens_for(application.state.engine, "after_cursor_execute")
    def after_query(connection, cursor, statement, parameters, context, executemany):
        with lock:
            metrics["query_count"] += 1
            metrics["query_seconds"] += time.perf_counter() - context.capacity_started

    @event.listens_for(pool, "checkout")
    def checkout(*args):
        with lock:
            metrics["pool_checkouts"] += 1
            metrics["checked_out"] += 1
            metrics["checked_out_peak"] = max(metrics["checked_out_peak"], metrics["checked_out"])

    @event.listens_for(pool, "checkin")
    def checkin(*args):
        with lock:
            metrics["checked_out"] = max(0, metrics["checked_out"] - 1)

    prior_lifespan = application.router.lifespan_context
    target = Path(os.environ["MILO_CAPACITY_METRICS"])
    metrics["configured_pool_size"] = pool.size()
    metrics["configured_pool_max_overflow"] = pool._max_overflow

    def write_metrics():
        with lock:
            snapshot = dict(metrics)
        snapshot["pool_status"] = pool.status()
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(snapshot, indent=2) + "\n")
        temporary.replace(target)

    async def publish_metrics():
        while True:
            write_metrics()
            await asyncio.sleep(0.25)

    @asynccontextmanager
    async def measured_lifespan(app):
        async with prior_lifespan(app):
            task = asyncio.create_task(publish_metrics())
            try:
                yield
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                write_metrics()
        write_metrics()

    application.router.lifespan_context = measured_lifespan
    return application
