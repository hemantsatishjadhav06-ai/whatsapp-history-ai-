"""Separate-process PostgreSQL authority tests; all transport remains synthetic."""

import asyncio
import multiprocessing
import os
from queue import Empty

from fastapi import HTTPException
import pytest
from sqlalchemy import func, select

from assistant import actions
from assistant.action_models import SubmissionAttempt
from assistant.config import Settings
from assistant.db import make_database, uid
from assistant.jobs_models import AuthorizedJob, JobRun
from assistant.models import Draft, Memory, Message, Outbox, SendAttempt
from assistant.people_models import UsageLedger
from conftest import TEST_KEY, create_chat
from test_actions import grant, make_action, source
from test_jobs import create_job, make_due, payload
from test_messaging import event, receive
from test_automation import automatic as automatic, question


def _worker(options, operation, body, start, output, release=None):
    engine, factory = make_database(Settings(_env_file=None, **options).prepare())
    try:
        assert start.wait(timeout=20), "Process start barrier timed out"
        with factory() as db:
            if operation == "event":
                from assistant.messaging import CanonicalEvent, connector_event
                result = [connector_event(CanonicalEvent(**body), db=db) for _ in range(10)]
            elif operation == "job":
                from assistant.jobs import JobCreate, create_job
                from assistant.models import User
                user = db.get(User, body.pop("owner_id"))
                result = create_job(JobCreate(**body), user=user, db=db)
            elif operation == "usage":
                from assistant.companion import reserve_usage
                reserve_usage(db, body["workspace_id"], body["operation_key"], "SEND_TEXT", action_units=1)
                db.commit()
                result = {"status": "reserved"}
            elif operation == "job-tick":
                from assistant.jobs import process_jobs
                result = asyncio.run(process_jobs(factory, Settings(_env_file=None, **options).prepare(), body["job_id"]))
            elif operation == "automation-tick":
                from assistant import automation
                import time
                plan = automation._plan
                def delayed_plan(*args):
                    time.sleep(0.2)  # Widen the separate-process duplicate-planning race.
                    return plan(*args)
                automation._plan = delayed_plan
                result = asyncio.run(automation.process_automation(factory, Settings(_env_file=None, **options).prepare()))
            elif operation == "job-control":
                from assistant.jobs import JobControl, control_job
                from assistant.models import User
                user = db.get(User, body.pop("owner_id"))
                result = control_job(body.pop("job_id"), JobControl(**body), user=user, db=db)
            elif operation == "dispatch":
                result = asyncio.run(actions.dispatch_action(factory, Settings(_env_file=None, **options).prepare(), body["action_id"]))
            elif operation == "blocked-dispatch":
                async def paused_transport(envelope, authority):
                    output.put({"stage": "claimed", "pid": os.getpid()})
                    assert release.wait(timeout=20), "Submission release timed out"
                    authority()
                    output.put({"stage": "transport", "pid": os.getpid()})
                    return {"status": "accepted", "provider_message_id": "mock:process-boundary"}
                actions.submit_simulated = paused_transport
                result = asyncio.run(actions.dispatch_action(factory, Settings(_env_file=None, **options).prepare(), body["action_id"]))
            else:
                raise AssertionError("Unknown test operation")
        output.put({"stage": "result", "pid": os.getpid(), "result": result})
    except HTTPException as exc:
        output.put({"stage": "result", "pid": os.getpid(), "result": {"http_status": exc.status_code}})
    except Exception as exc:
        # Never send credential-bearing engine messages to process output.
        output.put({"stage": "error", "pid": os.getpid(), "error_type": type(exc).__name__})
    finally:
        engine.dispose()


def _options(app):
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("Requires real PostgreSQL and separate spawned processes")
    return {"database_url": app.state.settings.database_url, "encryption_key": TEST_KEY,
            "environment": "test", "allow_dev_auth": False, "model_provider": "mock",
            "request_limits_mode": "off", "db_pool_size": 1, "db_max_overflow": 0}


def _parallel(app, operation, bodies):
    options = _options(app)
    context = multiprocessing.get_context("spawn")
    start, output = context.Event(), context.Queue()
    children = [context.Process(target=_worker, args=(options, operation, body, start, output)) for body in bodies]
    for child in children:
        child.start()
    start.set()
    try:
        messages = [output.get(timeout=30) for _ in children]
        assert all(message["stage"] == "result" for message in messages), messages
        assert len({message["pid"] for message in messages}) == len(children)
        assert os.getpid() not in {message["pid"] for message in messages}
        return [message["result"] for message in messages]
    finally:
        for child in children:
            child.join(timeout=10)
            if child.is_alive():
                child.terminate()
                child.join()
        output.close()


def test_pg_process_duplicate_events_and_revision_order(app, owner_client, chat):
    _options(app)
    body = event(chat)
    outcomes = _parallel(app, "event", [body.copy() for _ in range(4)])
    flat = [item for batch in outcomes for item in batch]
    assert sum(item["status"] == "accepted" for item in flat) == 1
    assert sum(item["status"] == "duplicate" for item in flat) == 39
    updates = [{**body, "event_id": uid(), "event_type": "message.edited", "source_revision": revision,
                "content": {"type": "text", "text": f"Synthetic revision {revision}"}} for revision in range(2, 6)]
    _parallel(app, "event", updates)
    with app.state.session_factory() as db:
        rows = list(db.scalars(select(Message)))
        assert len(rows) == 1
        assert rows[0].revision == 5 and rows[0].text == "Synthetic revision 5"


def test_pg_process_job_idempotency_is_one_row(app, owner_client, chat):
    _options(app)
    owner = owner_client.get("/me").json()["id"]
    body = {**payload(chat), "owner_id": owner}
    outcomes = _parallel(app, "job", [body.copy() for _ in range(4)])
    assert len({row["id"] for row in outcomes}) == 1
    mismatch = _parallel(app, "job", [{**body, "content": "Different exact content."}])
    assert mismatch == [{"http_status": 409}]
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AuthorizedJob)) == 1


def test_pg_process_action_claim_submits_once_and_tenant_isolated(app, owner_client, chat):
    _options(app)
    grant(owner_client, chat)
    action = make_action(owner_client, chat, source(app, chat))
    outcomes = _parallel(app, "dispatch", [{"action_id": action["id"]} for _ in range(4)])
    assert all(row["status"] in {"accepted", "submitting"} for row in outcomes)
    other = create_chat(owner_client, account="other-synthetic-account", recipient="15550009999")
    grant(owner_client, other)
    other_action = make_action(owner_client, other, source(app, other))
    _parallel(app, "dispatch", [{"action_id": other_action["id"]}])
    with app.state.session_factory() as db:
        attempts = list(db.scalars(select(SubmissionAttempt)))
        assert len(attempts) == 2 and all(row.status == "accepted" for row in attempts)
        assert len({row.connector_id for row in attempts}) == 2
        assert {row.workspace_id for row in attempts} == {chat["workspace"]["id"], other["workspace"]["id"]}


def test_pg_process_shared_budget_cannot_overreserve(app, owner_client, chat):
    _options(app)
    response = owner_client.put(f"/workspaces/{chat['workspace']['id']}/budget", json={"expected_version": 0, "max_actions_per_day": 2})
    assert response.status_code == 200, response.text
    bodies = [{"workspace_id": chat["workspace"]["id"], "operation_key": f"process:{index}"} for index in range(4)]
    results = _parallel(app, "usage", bodies)
    assert sum(result.get("status") == "reserved" for result in results) == 2
    assert sum(result.get("http_status") == 429 for result in results) == 2
    with app.state.session_factory() as db:
        assert db.scalar(select(func.sum(UsageLedger.action_units))) == 2


def test_pg_process_recurring_job_restart_consumes_one_occurrence(app, owner_client, chat):
    _options(app)
    job = create_job(owner_client, chat, recurrence="daily", max_runs=3)
    make_due(app, job)
    results = _parallel(app, "job-tick", [{"job_id": job["id"]} for _ in range(4)])
    assert any(batch and batch[0]["status"] == "accepted" for batch in results)
    assert _parallel(app, "job-tick", [{"job_id": job["id"]}]) == [[]]
    with app.state.session_factory() as db:
        row = db.get(AuthorizedJob, job["id"])
        assert row.runs_done == 1 and row.status == "scheduled"
        assert db.scalar(select(func.count()).select_from(JobRun)) == 1
        assert db.scalar(select(func.count()).select_from(SubmissionAttempt)) == 1


def test_pg_process_job_control_version_has_one_winner(app, owner_client, chat):
    _options(app)
    owner = owner_client.get("/me").json()["id"]
    job = create_job(owner_client, chat)
    body = {"owner_id": owner, "job_id": job["id"], "operation": "hold", "expected_version": job["version"]}
    results = _parallel(app, "job-control", [body.copy() for _ in range(4)])
    assert sum(result.get("status") == "held" for result in results) == 1
    assert sum(result.get("http_status") == 409 for result in results) == 3


def test_pg_process_business_hours_planning_is_one_durable_decision(app, owner_client, automatic):
    _options(app)
    question(owner_client, automatic)
    results = _parallel(app, "automation-tick", [{} for _ in range(4)])
    assert any(any(row.get("status") == "accepted" for row in batch) for batch in results)
    assert _parallel(app, "automation-tick", [{}]) == [[]]
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Draft)) == 1
        assert db.scalar(select(func.count()).select_from(SendAttempt)) == 1
        assert db.scalar(select(func.count()).select_from(Outbox).where(Outbox.kind == "automation.handled")) == 1


@pytest.mark.parametrize("control", ["pause", "forget", "takeover", "delete", "permission"])
def test_pg_process_current_authority_blocks_changed_state(app, owner_client, chat, control):
    options = _options(app)
    grant(owner_client, chat)
    evidence = source(app, chat)
    action = make_action(owner_client, chat, evidence)
    context = multiprocessing.get_context("spawn")
    start, release, output = context.Event(), context.Event(), context.Queue()
    child = context.Process(target=_worker, args=(options, "blocked-dispatch", {"action_id": action["id"]}, start, output, release))
    child.start()
    start.set()
    try:
        claimed = output.get(timeout=30)
        assert claimed["stage"] == "claimed" and claimed["pid"] != os.getpid()
        if control == "pause":
            response = owner_client.post("/pause-all", params={"workspace_id": chat["workspace"]["id"]})
        elif control == "permission":
            response = owner_client.put(f"/conversations/{chat['conversation']['id']}/permissions", json={"read": True, "retain": True, "draft": True, "send": False})
        elif control == "forget":
            with app.state.session_factory() as db:
                memory = Memory(id=uid(), workspace_id=chat["workspace"]["id"], conversation_id=chat["conversation"]["id"],
                                text="Synthetic fact", status="confirmed", source_message_ids=[evidence], source_revision={evidence: 1})
                db.add(memory)
                db.commit()
                memory_id = memory.id
            response = owner_client.delete(f"/memories/{memory_id}")
        elif control == "takeover":
            response = receive(owner_client, event(chat, direction="outbound", sender_id="Owner", content={"type": "text", "text": "I am handling this."}))
        else:
            with app.state.session_factory() as db:
                provider_id = db.get(Message, evidence).provider_message_id
            response = receive(owner_client, event(chat, provider_message_id=provider_id, sender_id="peer", event_type="message.deleted", source_revision=2, content={"type": "text", "text": ""}))
        assert response.status_code in {200, 204}, response.text
        release.set()
        result = output.get(timeout=30)
        assert result["stage"] == "result", result
        assert result["result"]["status"] == "blocked"
        with pytest.raises(Empty):
            output.get(timeout=0.1)
        with app.state.session_factory() as db:
            assert db.scalar(select(func.count()).select_from(SubmissionAttempt)) == 1
            assert db.scalar(select(SubmissionAttempt.provider_message_id)) is None
    finally:
        release.set()
        child.join(timeout=10)
        if child.is_alive():
            child.terminate()
            child.join()
        output.close()
