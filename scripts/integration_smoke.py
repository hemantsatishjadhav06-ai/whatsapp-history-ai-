"""Exercise a real local Kafka broker and Temporal with disposable mock account data."""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from sqlalchemy import func, select
from temporalio.client import Client

from assistant.models import Outbox, ScheduledIntent, SendAttempt
from assistant.relay import relay_pending
from assistant.workflows import register_pending
from demo import checked, incoming, seed_owner, synthetic_backend


async def kafka_check(app, client, settings):
    _, connector, conversation = seed_owner(client)
    checked(client, "POST", "/internal/connector-events",
            json=incoming(connector["id"], conversation["id"], str(uuid4())),
            headers={"Authorization": f"Bearer {settings.internal_service_token}"})
    topic = f"assistant.integration.{uuid4().hex}"
    producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap_servers, enable_idempotence=True)
    await producer.start()
    try:
        published = await relay_pending(producer, app.state.session_factory, topic=topic)
    finally:
        await producer.stop()
    consumer = AIOKafkaConsumer(
        topic, bootstrap_servers=settings.kafka_bootstrap_servers,
        auto_offset_reset="earliest", enable_auto_commit=False, group_id=f"integration-{uuid4()}",
    )
    await consumer.start()
    try:
        event = await asyncio.wait_for(consumer.getone(), timeout=20)
    finally:
        await consumer.stop()
    envelope = json.loads(event.value)
    assert published == 1
    assert envelope["kind"] == "message.accepted"
    assert envelope["payload"]["live_eligible"] is True
    assert "text" not in envelope["payload"]
    with app.state.session_factory() as session:
        assert session.get(Outbox, envelope["id"]).status == "published"
    return {"published": published, "consumed": 1, "sql_acknowledged": True, "content_in_event": False}


def approved_schedule(client, conversation_id):
    draft = checked(client, "POST", f"/conversations/{conversation_id}/drafts", json={})
    draft = checked(client, "PATCH", f"/drafts/{draft['id']}", json={"text": "Synthetic scheduled acknowledgement."})
    checked(client, "POST", f"/drafts/{draft['id']}/approve", json={"content_hash": draft["content_hash"]})
    due = datetime.now(UTC) + timedelta(seconds=12)
    intent = checked(client, "POST", "/scheduled-intents", json={
        "draft_id": draft["id"], "idempotency_key": f"integration-{uuid4()}",
        "due_at": due.isoformat(), "expires_at": (due + timedelta(seconds=60)).isoformat(), "timezone": "UTC",
    })
    return intent, draft, due


async def timer_started(handle, process):
    for _ in range(30):
        if process.poll() is not None:
            raise RuntimeError("Synthetic Temporal worker exited before timer registration")
        history = await handle.fetch_history()
        if any(event.HasField("timer_started_event_attributes") for event in history.events):
            return
        await asyncio.sleep(.25)
    raise TimeoutError("Synthetic workflow did not start its timer")


async def temporal_check(app, client, settings):
    settings.workflow_task_queue = f"integration-{uuid4().hex}"
    _, connector, conversation = seed_owner(client)
    checked(client, "POST", "/internal/connector-events",
            json=incoming(connector["id"], conversation["id"], str(uuid4())),
            headers={"Authorization": f"Bearer {settings.internal_service_token}"})
    intent, draft, due = approved_schedule(client, conversation["id"])
    temporal = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    assert await register_pending(temporal, app.state.session_factory, settings) == 1
    assert await register_pending(temporal, app.state.session_factory, settings) == 0
    handle = temporal.get_workflow_handle(f"schedule-{intent['id']}")
    environment = os.environ.copy()
    environment.update({
        "ENVIRONMENT": "test", "DATABASE_URL": settings.database_url, "ENCRYPTION_KEY": settings.encryption_key,
        "ALLOW_DEV_AUTH": "true", "SESSION_SECURE": "false", "MODEL_PROVIDER": "mock",
        "ENABLE_EXTERNAL_SENDS": "false", "TEMPORAL_ADDRESS": settings.temporal_address,
        "TEMPORAL_NAMESPACE": settings.temporal_namespace, "WORKFLOW_TASK_QUEUE": settings.workflow_task_queue,
        "NO_PROXY": "localhost,127.0.0.1", "no_proxy": "localhost,127.0.0.1",
    })
    worker = None
    log_path = Path(".local") / f"temporal-worker-{uuid4().hex}.log"
    with log_path.open("w+b") as output:
        def start_worker():
            return subprocess.Popen([sys.executable, "-m", "assistant.workflows", "worker"],
                                    env=environment, stdin=subprocess.DEVNULL, stdout=output, stderr=output)

        try:
            worker = start_worker()
            await timer_started(handle, worker)
            assert datetime.now(UTC) < due, "Timer must be registered before the worker outage"
            # Terminate only the worker started here; the timer remains on the real server.
            worker.kill()
            worker.wait(timeout=10)
            remaining = (due - datetime.now(UTC)).total_seconds()
            if remaining > 0:
                await asyncio.sleep(remaining + .5)
            worker = start_worker()
            result_task = asyncio.create_task(handle.result())
            for _ in range(140):
                if worker.poll() is not None:
                    result_task.cancel()
                    raise RuntimeError(f"Synthetic worker exited; inspect {log_path}")
                if result_task.done():
                    break
                await asyncio.sleep(.25)
            status = await asyncio.wait_for(result_task, timeout=1)
            assert status == "accepted"
            with app.state.session_factory() as session:
                row = session.get(ScheduledIntent, intent["id"])
                assert row.workflow_registered and row.status == "accepted"
                attempts = session.scalar(select(func.count()).select_from(SendAttempt)
                                          .where(SendAttempt.draft_id == draft["id"]))
                assert attempts == 1
            return {"registered_idempotently": True, "worker_killed_before_due": True,
                    "worker_restarted_after_due": True, "intent_status": status, "send_attempts": attempts,
                    "transport": "mock_only"}
        except BaseException:
            await handle.terminate("Synthetic integration test did not complete")
            raise
        finally:
            if worker is not None and worker.poll() is None:
                worker.terminate()
                try:
                    worker.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kafka", action="store_true")
    parser.add_argument("--temporal", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.kafka and not args.temporal:
        parser.error("Choose --kafka, --temporal, or both")
    result = {"mode": "real_local_services_synthetic_sqlite_mock_transport"}
    with synthetic_backend() as (app, client, settings):
        if args.kafka:
            result["kafka"] = asyncio.run(kafka_check(app, client, settings))
        if args.temporal:
            result["temporal"] = asyncio.run(temporal_check(app, client, settings))
    report = json.dumps(result, indent=2) + "\n"
    print(report, end="")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report)


if __name__ == "__main__":
    main()
