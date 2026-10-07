"""At-least-once publication of transactional SQL outbox metadata to Kafka.

Consumers must deduplicate by event.id. Schedule registrations have a separate
Temporal registrar and are deliberately excluded. Message bodies are never
published by this relay.
"""

import argparse
import asyncio
import json
import logging

from aiokafka import AIOKafkaProducer

logger = logging.getLogger(__name__)
METADATA_KEYS = frozenset({
    "message_id", "conversation_id", "event_id", "live_eligible", "intent_id", "due_at",
    "draft_id", "import_id", "revision", "source_revision", "provider", "connector_id", "version",
    "resource_id", "status", "permission_version", "control_epoch", "pause_generation",
    "action_id", "job_id", "run_id", "kind", "reason_code",
})


def event_envelope(row) -> bytes:
    payload = row.payload or {}
    if set(payload) - METADATA_KEYS:
        raise ValueError("Outbox payload contains unsupported metadata")
    # Reject containers: an allowed key cannot become a route for plaintext content.
    if any(not isinstance(value, (str, bool, int, float, type(None))) for value in payload.values()):
        raise ValueError("Outbox metadata values must be scalar")
    return json.dumps({
        "schema_version": 1,
        "id": row.id,
        "workspace_id": row.workspace_id,
        "kind": row.kind,
        "aggregate_id": row.aggregate_id,
        "payload": payload,
    }, separators=(",", ":"), sort_keys=True).encode()


async def relay_pending(producer, session_factory, *, topic: str = "assistant.events.v1",
                        batch_size: int = 100) -> int:
    from sqlalchemy import select

    from .models import Outbox

    published = 0
    with session_factory() as session, session.begin():
        rows = session.scalars(
            select(Outbox)
            .where(Outbox.status == "pending", Outbox.kind != "schedule.register")
            .order_by(Outbox.created_at, Outbox.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).all()
        for row in rows:
            await producer.send_and_wait(topic, event_envelope(row), key=row.id.encode())
            row.status = "published"
            published += 1
    return published


async def run(*, once: bool = False, topic: str = "assistant.events.v1"):
    from .config import Settings
    from .db import make_database

    settings = Settings().prepare()
    _, session_factory = make_database(settings)
    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        enable_idempotence=True,
        acks="all",
    )
    await producer.start()
    try:
        while True:
            try:
                count = await relay_pending(producer, session_factory, topic=topic)
                logger.info("Event publication batch completed: %d", count)
            except Exception:
                logger.error("Event publication failed; pending rows retained")
                if once:
                    raise
            if once:
                return
            await asyncio.sleep(1)
    finally:
        await producer.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--topic", default="assistant.events.v1")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(once=args.once, topic=args.topic))


if __name__ == "__main__":
    main()
