"""Durable schedule timers with SQL as the dispatch authority.

Workflows contain only an intent ID. Their activities re-read mutable permissions,
approval, connector fencing, and expiry through the common messaging dispatcher.
Registration uses a transactional SQL outbox and stable Temporal workflow IDs.
"""

import argparse
import asyncio
import logging
from datetime import datetime, timedelta
from functools import lru_cache

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _runtime():
    from .config import Settings
    from .db import make_database

    settings = Settings().prepare()
    _, session_factory = make_database(settings)
    return settings, session_factory


@activity.defn
async def schedule_snapshot(intent_id: str) -> dict[str, str]:
    """Return timing metadata only, never a recipient or message body."""
    def load():
        from .db import aware
        from .models import ScheduledIntent

        _, session_factory = _runtime()
        with session_factory() as session:
            intent = session.get(ScheduledIntent, intent_id)
            if intent is None:
                return {"status": "missing"}
            return {"status": intent.status, "due_at": aware(intent.due_at).isoformat(),
                    "expires_at": aware(intent.expires_at).isoformat()}

    return await asyncio.to_thread(load)


@activity.defn
async def dispatch_scheduled_intent(intent_id: str) -> str:
    from .messaging import process_due

    settings, session_factory = _runtime()
    await process_due(session_factory, settings, intent_id=intent_id)
    snapshot = await schedule_snapshot(intent_id)
    return snapshot["status"]


@workflow.defn
class ScheduleWorkflow:
    @workflow.run
    async def run(self, intent_id: str) -> str:
        while True:
            snapshot = await workflow.execute_activity(
                schedule_snapshot, intent_id, start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
            if snapshot["status"] not in {"scheduled", "held"}:
                return snapshot["status"]
            if snapshot["status"] == "held":
                # A durable paused schedule retains the same workflow identity.
                # SQL expiry is checked by the activity even while held.
                await workflow.execute_activity(
                    dispatch_scheduled_intent, intent_id, start_to_close_timeout=timedelta(seconds=90),
                    retry_policy=RetryPolicy(maximum_attempts=5),
                )
                await workflow.sleep(timedelta(seconds=30))
                continue
            delay = datetime.fromisoformat(snapshot["due_at"]) - workflow.now()
            if delay.total_seconds() > 0:
                await workflow.sleep(delay)
            status = await workflow.execute_activity(
                dispatch_scheduled_intent, intent_id, start_to_close_timeout=timedelta(seconds=90),
                retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1),
                                        maximum_interval=timedelta(seconds=10), maximum_attempts=5),
            )
            if status != "held":
                return status


async def register_pending(client, session_factory, settings, *, batch_size: int = 100) -> int:
    """Commit a registration acknowledgement only after Temporal accepted it.

    A crash after starting a workflow leaves the row pending. The next registrar
    observes its stable workflow ID and acknowledges without a second timer.
    PostgreSQL row locks allow multiple registrars; SQLite is single-process dev.
    """
    from sqlalchemy import select

    from .models import Outbox, ScheduledIntent

    registered = 0
    with session_factory() as session, session.begin():
        rows = session.scalars(
            select(Outbox)
            .where(Outbox.status == "pending", Outbox.kind == "schedule.register")
            .order_by(Outbox.created_at, Outbox.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).all()
        for row in rows:
            intent = session.get(ScheduledIntent, row.aggregate_id)
            if intent is None or intent.status not in {"scheduled", "held"}:
                row.status = "obsolete"
                continue
            if row.workspace_id != intent.workspace_id:
                raise ValueError("Schedule registration crosses workspace boundary")
            try:
                await client.start_workflow(
                    ScheduleWorkflow.run,
                    intent.id,
                    id=f"schedule-{intent.id}",
                    task_queue=settings.workflow_task_queue,
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                )
            except WorkflowAlreadyStartedError:
                pass
            intent.workflow_registered = True
            row.status = "registered"
            registered += 1
    return registered


async def run_worker():
    settings, _ = _runtime()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    worker = Worker(
        client,
        task_queue=settings.workflow_task_queue,
        workflows=[ScheduleWorkflow],
        activities=[schedule_snapshot, dispatch_scheduled_intent],
        max_concurrent_activities=20,
        max_concurrent_workflow_tasks=20,
    )
    await worker.run()


async def run_registrar(*, once: bool = False):
    settings, session_factory = _runtime()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    while True:
        try:
            count = await register_pending(client, session_factory, settings)
            logger.info("Schedule registration batch completed: %d", count)
        except Exception:
            # Do not log exception bodies: provider errors may contain credentials/content.
            logger.error("Schedule registration failed; pending rows retained")
            if once:
                raise
        if once:
            return
        await asyncio.sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["worker", "registrar"])
    parser.add_argument("--once", action="store_true", help="Run one registrar batch")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker() if args.command == "worker" else run_registrar(once=args.once))


if __name__ == "__main__":
    main()
