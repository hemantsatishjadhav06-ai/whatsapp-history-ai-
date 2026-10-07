"""Durable outbound invariants under overlapping workers and stale SQL reads.

Every provider boundary is simulated. PostgreSQL receipt cases deliberately
overlap separate transactions; they do not depend on one worker's local guard.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import threading

import pytest
from sqlalchemy import func, select, text

from assistant import actions, messaging
from assistant.action_models import OutboundAction, SubmissionAttempt
from assistant.db import aware, now, uid
from assistant.jobs import process_jobs
from assistant.jobs_models import AuthorizedJob, JobRun
from assistant.models import Draft, Memory, SendAttempt
from assistant.native_models import MessageContext
from test_actions import grant, make_action, source
from test_jobs import create_job, make_due
from test_messaging import approve, make_draft


def test_overlapping_job_workers_finalize_one_recurrence_once(app, owner_client, chat, monkeypatch):
    job = create_job(owner_client, chat, recurrence="daily", max_runs=3)
    make_due(app, job)
    original_dispatch = actions.dispatch_action

    async def overlap():
        accepted = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def delayed_first_dispatch(*args, **kwargs):
            nonlocal calls
            calls += 1
            first = calls == 1
            result = await original_dispatch(*args, **kwargs)
            if first:
                assert result["status"] == "accepted"
                accepted.set()
                await asyncio.wait_for(release.wait(), timeout=5)
            return result

        monkeypatch.setattr(actions, "dispatch_action", delayed_first_dispatch)
        first = asyncio.create_task(process_jobs(app.state.session_factory, app.state.settings, job["id"]))
        try:
            await asyncio.wait_for(accepted.wait(), timeout=5)
            second = await process_jobs(app.state.session_factory, app.state.settings, job["id"])
        finally:
            release.set()
            first_result = await first
        assert first_result[0]["status"] == "accepted"
        assert second[0]["status"] == "accepted"

    asyncio.run(overlap())
    with app.state.session_factory() as db:
        current = db.get(AuthorizedJob, job["id"])
        run = db.scalar(select(JobRun).where(JobRun.job_id == current.id))
        assert run.occurrence == 0 and run.status == "accepted"
        assert current.runs_done == 1
        assert current.status == "scheduled"
        # make_due moves only the test occurrence; the owner's saved wall clock
        # remains the requested time for subsequent recurrences.
        assert aware(current.due_at) == datetime.fromisoformat(job["due_at"]) + timedelta(days=1)
        assert db.scalar(select(func.count()).select_from(JobRun)) == 1
        assert db.scalar(select(func.count()).select_from(SubmissionAttempt)) == 1


def test_overlapping_action_dispatch_preserves_fresh_inflight_claim(app, owner_client, chat, monkeypatch):
    grant(owner_client, chat)
    action = make_action(owner_client, chat, source(app, chat))

    async def overlap():
        claimed = asyncio.Event()
        release = asyncio.Event()
        transport_calls = 0

        async def delayed_transport(envelope, authority):
            nonlocal transport_calls
            transport_calls += 1
            claimed.set()
            await asyncio.wait_for(release.wait(), timeout=5)
            authority()
            return {"status": "accepted", "provider_message_id": f"mock:race:{envelope['action_id']}"}

        monkeypatch.setattr(actions, "submit_simulated", delayed_transport)
        first = asyncio.create_task(actions.dispatch_action(app.state.session_factory, app.state.settings, action["id"]))
        try:
            await asyncio.wait_for(claimed.wait(), timeout=5)
            duplicate = await actions.dispatch_action(app.state.session_factory, app.state.settings, action["id"])
        finally:
            release.set()
            result = await first
        assert duplicate["status"] == "submitting"
        assert result["status"] == "accepted"
        assert transport_calls == 1

    asyncio.run(overlap())
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(SubmissionAttempt)) == 1


def test_aged_action_claim_recovers_as_uncertain_without_resubmitting(app, owner_client, chat, monkeypatch):
    grant(owner_client, chat)
    action = make_action(owner_client, chat, source(app, chat))
    with app.state.session_factory() as db:
        row = db.get(OutboundAction, action["id"])
        row.status = "submitting"
        db.add(SubmissionAttempt(workspace_id=row.workspace_id, action_id=row.id, connector_id=row.connector_id,
               destination_conversation_id=row.destination_conversation_id, payload_hash=row.payload_hash,
               connector_fence=row.connector_fence, status="submitting", created_at=now() - timedelta(seconds=121)))
        db.commit()

    async def forbidden_transport(*args, **kwargs):
        pytest.fail("Recovery must not issue a second provider submission")

    monkeypatch.setattr(actions, "submit_simulated", forbidden_transport)
    for _ in range(2):
        result = asyncio.run(actions.dispatch_action(app.state.session_factory, app.state.settings, action["id"]))
        assert result["status"] == "uncertain"
        assert result["reason_code"] == "DELIVERY_UNCERTAIN"
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(SubmissionAttempt)) == 1


def test_aged_legacy_claim_recovers_as_uncertain_without_resubmitting(app, owner_client, chat, monkeypatch):
    draft = make_draft(app, chat)
    approve(owner_client, draft)
    with app.state.session_factory() as db:
        row = db.get(Draft, draft[0])
        row.status = "dispatching"
        db.add(SendAttempt(workspace_id=row.workspace_id, draft_id=row.id, conversation_id=row.conversation_id,
               content_hash=row.content_hash, connector_fence=row.connector_fence, status="dispatching",
               created_at=now() - timedelta(seconds=121)))
        db.commit()

    async def forbidden_transport(*args, **kwargs):
        pytest.fail("Restart recovery must preserve the original submission ledger")

    monkeypatch.setattr(messaging, "_transport_send", forbidden_transport)
    for _ in range(2):
        result = asyncio.run(messaging.dispatch_draft(app.state.session_factory, app.state.settings, draft[0]))
        assert result["status"] == "uncertain"
        assert result["error_code"]
    with app.state.session_factory() as db:
        assert db.get(Draft, draft[0]).status == "uncertain"
        assert db.scalar(select(func.count()).select_from(SendAttempt)) == 1


def test_memory_only_job_cannot_use_expired_message_context(app, owner_client, chat):
    evidence = source(app, chat, origin="history", owner=True, text="The office opens at nine.")
    memory_id = uid()
    with app.state.session_factory() as db:
        context = db.scalar(select(MessageContext).where(MessageContext.message_id == evidence))
        context.expires_at = now() + timedelta(minutes=10)
        db.add(Memory(id=memory_id, workspace_id=chat["workspace"]["id"],
               conversation_id=chat["conversation"]["id"], text="The office opens at nine.", status="confirmed",
               source_message_ids=[evidence], source_revision={evidence: 1}, expires_at=now() + timedelta(days=1)))
        db.commit()
    job = create_job(owner_client, chat, content="The office opens at nine.", memory_ids=[memory_id])
    assert job["evidence_message_ids"] == []
    make_due(app, job)
    with app.state.session_factory() as db:
        db.scalar(select(MessageContext).where(MessageContext.message_id == evidence)).expires_at = now() - timedelta(seconds=1)
        db.commit()
    result = asyncio.run(process_jobs(app.state.session_factory, app.state.settings, job["id"]))
    assert result[0]["status"] == "needs_review"
    assert result[0]["reason_code"] in {"CONTEXT_STALE", "SOURCE_MISSING"}
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(SubmissionAttempt)) == 0


def test_global_pause_updates_metadata_without_decrypting_pending_payloads(app, owner_client, chat):
    draft = make_draft(app, chat)
    approve(owner_client, draft)
    grant(owner_client, chat)
    action = make_action(owner_client, chat, source(app, chat))
    job = create_job(owner_client, chat)
    with app.state.session_factory() as db:
        # Owner controls require only authoritative metadata. Corrupt test-only
        # private ciphertext makes an accidental payload read fail this check.
        for table, column, row_id in (("drafts", "text", draft[0]), ("outbound_actions", "payload", action["id"]),
                                      ("authorized_jobs", "content", job["id"])):
            db.execute(text(f"UPDATE {table} SET {column}=:value WHERE id=:id"),
                       {"value": "invalid-synthetic-ciphertext", "id": row_id})
        db.commit()
    response = owner_client.post("/pause-all", params={"workspace_id": chat["workspace"]["id"]})
    assert response.status_code == 200, response.text
    with app.state.session_factory() as db:
        assert db.scalar(select(Draft.status).where(Draft.id == draft[0])) == "cancelled"
        assert db.scalar(select(OutboundAction.status).where(OutboundAction.id == action["id"])) == "canceled"
        assert db.scalar(select(AuthorizedJob.status).where(AuthorizedJob.id == job["id"])) == "held"


def _overlapping_receipts(app, attempt_type, lower, higher):
    """Hold an old lower-priority read while stronger evidence can arrive.

    A PostgreSQL row lock legitimately serializes the second request until the
    bounded wait ends. Without locking, stronger evidence commits first and the
    stale lower-priority request must still be prevented from overwriting it.
    """
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("Requires independent PostgreSQL transactions and row locks")
    loaded = threading.Event()
    stronger_finished = threading.Event()

    def lower_transaction():
        with app.state.session_factory() as db:
            original_scalar = db.scalar
            delayed = False

            def delay_old_read(statement, *args, **kwargs):
                nonlocal delayed
                value = original_scalar(statement, *args, **kwargs)
                if isinstance(value, attempt_type) and not delayed:
                    delayed = True
                    loaded.set()
                    stronger_finished.wait(timeout=1)
                return value

            db.scalar = delay_old_read
            return lower(db)

    def higher_transaction():
        assert loaded.wait(timeout=5), "Lower-priority receipt never loaded its attempt"
        try:
            with app.state.session_factory() as db:
                return higher(db)
        finally:
            stronger_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        old = pool.submit(lower_transaction)
        new = pool.submit(higher_transaction)
        old.result(timeout=10)
        new.result(timeout=10)


def test_native_receipt_stale_delivered_cannot_overwrite_read(app, owner_client, chat):
    grant(owner_client, chat)
    action = make_action(owner_client, chat, source(app, chat))
    asyncio.run(actions.dispatch_action(app.state.session_factory, app.state.settings, action["id"]))
    with app.state.session_factory() as db:
        provider_id = db.scalar(select(SubmissionAttempt.provider_message_id))
    values = {"connector_id": chat["connector"]["id"], "action_id": action["id"], "provider_message_id": provider_id}
    delivered = actions.ActionReceipt(**values, status="delivered")
    read = actions.ActionReceipt(**values, status="read")
    _overlapping_receipts(app, SubmissionAttempt,
                         lambda db: actions.action_receipt(delivered, db=db),
                         lambda db: actions.action_receipt(read, db=db))
    with app.state.session_factory() as db:
        actions.action_receipt(actions.ActionReceipt(**values, status="accepted"), db=db)
    with app.state.session_factory() as db:
        assert db.scalar(select(SubmissionAttempt.status)) == "read"
        assert db.get(OutboundAction, action["id"]).status == "read"


def test_legacy_receipt_stale_failure_cannot_overwrite_delivered(app, owner_client, chat):
    draft = make_draft(app, chat)
    approve(owner_client, draft)
    result = asyncio.run(messaging.dispatch_draft(app.state.session_factory, app.state.settings, draft[0]))
    values = {"connector_id": chat["connector"]["id"], "provider_message_id": result["provider_message_id"]}
    failed = messaging.Receipt(**values, status="failed")
    delivered = messaging.Receipt(**values, status="delivered")
    _overlapping_receipts(app, SendAttempt,
                         lambda db: messaging.reconcile_receipt(failed, db=db),
                         lambda db: messaging.reconcile_receipt(delivered, db=db))
    with app.state.session_factory() as db:
        messaging.reconcile_receipt(messaging.Receipt(**values, status="accepted"), db=db)
    with app.state.session_factory() as db:
        assert db.scalar(select(SendAttempt.status)) == "delivered"
        assert db.get(Draft, draft[0]).status == "delivered"
