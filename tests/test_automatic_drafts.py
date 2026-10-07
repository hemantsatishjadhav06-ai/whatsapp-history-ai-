import asyncio
from datetime import datetime, timedelta
import multiprocessing
import os
import subprocess
import sys
import threading

import pytest
from sqlalchemy import func, select

from assistant.automatic_drafts import _claim, process_automatic_drafts, run_one_generation
from assistant.automatic_drafts_models import AutoDraftGrant, AutomaticDraftJob, AutomaticDraftSlot
from assistant.db import aware, now, uid
from assistant.models import Connector, Draft, Memory, Message, Outbox, SendAttempt
from assistant.people_models import UsageLedger
from conftest import create_chat, login
from test_messaging import event, receive


def grant(client, chat, **changes):
    body = {"enabled": True, "expected_version": 0, **changes}
    result = client.put(f"/conversations/{chat['conversation']['id']}/automatic-drafts", json=body)
    assert result.status_code == 200, result.text
    return result.json()


def admit(app):
    return asyncio.run(process_automatic_drafts(app.state.session_factory, app.state.settings))


def generate(app):
    return asyncio.run(run_one_generation(app.state.session_factory, app.state.settings))


def incoming(client, chat, **changes):
    result = receive(client, event(chat, **changes))
    assert result.status_code == 200, result.text
    return result.json()


def test_existing_draft_permission_never_opts_in(app, owner_client, chat):
    assert owner_client.get(f"/conversations/{chat['conversation']['id']}/automatic-drafts").json()["version"] == 0
    incoming(owner_client, chat)
    assert admit(app) == [] and generate(app) is None


def test_explicit_grant_admits_once_and_prepares_unapproved_draft(app, owner_client, chat, monkeypatch):
    granted = grant(owner_client, chat)
    assert granted["status"] == "ready"
    incoming(owner_client, chat)
    def forbidden(*args):
        raise AssertionError("Admission must never call a model")
    with monkeypatch.context() as patch:
        patch.setattr("assistant.intelligence.call_model", forbidden)
        assert admit(app)[0]["status"] == "queued"
    assert admit(app) == []
    outcome = generate(app)
    assert outcome["status"] == "drafted" and generate(app) is None
    with app.state.session_factory() as db:
        draft = db.get(Draft, outcome["draft_id"])
        assert draft.status == "needs_approval" and draft.approved_hash is None
        assert draft.model_version == "mock-v1" and aware(draft.context_expires_at) <= now() + timedelta(minutes=10)
        assert db.scalar(select(func.count(SendAttempt.id))) == 0
        assert db.scalar(select(UsageLedger)).status == "consumed"
        assert db.scalar(select(Outbox).where(Outbox.kind == "message.accepted")).status == "pending"


def test_one_second_model_timeout_still_admits_automatic_generation(app, owner_client, chat):
    app.state.settings.model_timeout_seconds = 1
    grant(owner_client, chat)
    incoming(owner_client, chat)
    admit(app)
    assert generate(app)["status"] == "drafted"


def test_jobs_once_entrypoint_admits_and_drains_at_most_one_mock_generation(app, owner_client, chat):
    grant(owner_client, chat)
    incoming(owner_client, chat)
    login(owner_client, "once-second-owner@example.test")
    second = create_chat(owner_client, account="once-second-owner-account")
    grant(owner_client, second)
    incoming(owner_client, second)
    variables = os.environ.copy()
    variables.update(ENVIRONMENT="test", MODEL_PROVIDER="mock",
                     DATABASE_URL=app.state.settings.database_url,
                     ENCRYPTION_KEY=app.state.settings.encryption_key)
    result = subprocess.run([sys.executable, "-m", "assistant.jobs", "--once"], env=variables,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(Draft.id))) == 1
        assert db.scalar(select(func.count(AutomaticDraftJob.id)).where(AutomaticDraftJob.status == "drafted")) == 1
        assert db.scalar(select(func.count(AutomaticDraftJob.id)).where(AutomaticDraftJob.status == "queued")) == 1
        assert db.scalar(select(func.count(SendAttempt.id))) == 0


@pytest.mark.parametrize("changes", [
    {"origin": "history"}, {"origin": "replay"}, {"origin": "unknown"},
    {"direction": "outbound", "sender_id": "Owner", "author_kind": "human_owner"},
    {"direction": "outbound", "sender_id": "Owner", "author_kind": "assistant"},
    {"provider_timestamp": (now() - timedelta(minutes=3)).isoformat()},
])
def test_nonlive_and_owner_events_never_enqueue(app, owner_client, chat, changes):
    grant(owner_client, chat)
    incoming(owner_client, chat, **changes)
    assert admit(app) == []


def test_grant_does_not_replay_old_events_and_duplicate_canonical_event(app, owner_client, chat):
    body = event(chat)
    assert receive(owner_client, body).status_code == 200
    grant(owner_client, chat)
    assert admit(app) == []
    body = event(chat)
    assert receive(owner_client, body).status_code == 200
    assert receive(owner_client, body).json()["status"] == "duplicate"
    assert len(admit(app)) == 1 and admit(app) == []


def test_grant_version_expiry_owner_scope_and_csrf(owner_client, chat):
    path = f"/conversations/{chat['conversation']['id']}/automatic-drafts"
    for expiry in [now() - timedelta(seconds=1), now() + timedelta(days=31)]:
        assert owner_client.put(path, json={"enabled": True, "expected_version": 0,
                                           "expires_at": expiry.isoformat()}).status_code == 422
    assert owner_client.put(path, json={"enabled": True, "expected_version": 0,
                                       "expires_at": "2026-10-15T12:00:00"}).status_code == 422
    granted = grant(owner_client, chat)
    assert owner_client.put(path, json={"enabled": False, "expected_version": 0}).status_code == 409
    csrf = owner_client.headers.pop("X-CSRF-Token")
    assert owner_client.put(path, json={"enabled": False, "expected_version": granted["version"]}).status_code == 403
    owner_client.headers["X-CSRF-Token"] = csrf
    login(owner_client, "different-auto-owner@example.test")
    assert owner_client.get(path).status_code == 404
    assert owner_client.put(path, json={"enabled": False, "expected_version": 1}).status_code == 404


@pytest.mark.parametrize("provider,expected", [("disabled", "MODEL_DISABLED"), ("openai", "MODEL_NOT_CONFIGURED")])
def test_unconfigured_model_reports_blocker_without_network(app, owner_client, chat, monkeypatch, provider, expected):
    app.state.settings.model_provider = provider
    app.state.settings.model_api_key = ""
    app.state.settings.model_name = ""
    assert grant(owner_client, chat)["reason_code"] == expected
    incoming(owner_client, chat)
    monkeypatch.setattr("assistant.intelligence.call_model", lambda *args: pytest.fail("Provider called"))
    outcome = admit(app)[0]
    assert outcome["status"] == "blocked" and outcome["reason_code"] == expected
    assert datetime.fromisoformat(outcome["expires_at"]) <= now() + timedelta(minutes=10)
    assert generate(app) is None


@pytest.mark.parametrize("control", ["grant", "permissions", "takeover", "pause", "fence", "source_edit", "memory_expiry"])
def test_controls_changed_during_provider_never_persist_result(app, owner_client, chat, monkeypatch, control):
    memory_id = None
    if control == "memory_expiry":
        source = incoming(owner_client, chat, origin="history")
        memory = owner_client.post(f"/conversations/{chat['conversation']['id']}/memories", json={
            "text": "Confirmed current context", "source_message_ids": [source["message_id"]],
            "status": "confirmed", "expires_at": (now() + timedelta(hours=1)).isoformat()})
        assert memory.status_code == 201
        memory_id = memory.json()["id"]
    grant(owner_client, chat)
    body = event(chat)
    assert receive(owner_client, body).status_code == 200
    admit(app)
    from assistant.intelligence import call_model
    def changed(settings, context):
        if control == "grant":
            grant(owner_client, chat, enabled=False, expected_version=1)
        elif control == "permissions":
            assert owner_client.put(f"/conversations/{chat['conversation']['id']}/permissions",
                                    json={"read": True, "retain": True, "draft": False}).status_code == 200
        elif control == "takeover":
            assert owner_client.post(f"/conversations/{chat['conversation']['id']}/takeover").status_code == 200
        elif control == "pause":
            assert owner_client.post("/pause-all", params={"workspace_id": chat["workspace"]["id"]}).status_code == 200
        elif control == "fence":
            with app.state.session_factory() as db:
                db.get(Connector, chat["connector"]["id"]).fence += 1
                db.commit()
        elif control == "memory_expiry":
            with app.state.session_factory() as db:
                db.get(Memory, memory_id).expires_at = now() - timedelta(seconds=1)
                db.commit()
        else:
            body.update(event_id=uid(), event_type="message.edited", source_revision=2,
                        content={"type": "text", "text": "Please wait"})
            assert receive(owner_client, body).status_code == 200
        return call_model(settings, context)
    monkeypatch.setattr("assistant.intelligence.call_model", changed)
    assert generate(app)["status"] == "cancelled"
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(Draft.id))) == 0
        assert db.scalar(select(func.count(SendAttempt.id))) == 0


def test_hourly_quota_and_model_budget_block_without_provider_retry(app, owner_client, chat, monkeypatch):
    grant(owner_client, chat, max_drafts_per_hour=1)
    incoming(owner_client, chat)
    admit(app)
    assert generate(app)["status"] == "drafted"
    incoming(owner_client, chat)
    admit(app)
    monkeypatch.setattr("assistant.intelligence.call_model", lambda *args: pytest.fail("Hourly ceiling violated"))
    assert generate(app) is None
    with app.state.session_factory() as db:
        assert db.scalar(select(AutomaticDraftJob).where(AutomaticDraftJob.status == "blocked")).reason_code == "DRAFT_HOURLY_LIMIT"
    grant(owner_client, chat, expected_version=1)
    app.state.settings.model_provider = "openai_compatible"
    app.state.settings.model_api_key = "synthetic-test-token"
    app.state.settings.model_name = "synthetic-test-model"
    assert owner_client.put(f"/workspaces/{chat['workspace']['id']}/budget",
                            json={"expected_version": 0, "max_tokens_per_day": 0}).status_code == 200
    incoming(owner_client, chat)
    admit(app)
    assert generate(app)["status"] == "blocked" and generate(app) is None


def test_unverified_pricing_with_cost_ceiling_is_visible_before_model_call(app, owner_client, chat, monkeypatch):
    app.state.settings.model_provider = "openai_compatible"
    app.state.settings.model_api_key = "synthetic-test-token"
    app.state.settings.model_name = "synthetic-test-model"
    assert owner_client.put(f"/workspaces/{chat['workspace']['id']}/budget",
                            json={"expected_version": 0, "max_cost_microusd_per_day": 10}).status_code == 200
    assert grant(owner_client, chat)["reason_code"] == "MODEL_PRICING_UNVERIFIED"
    incoming(owner_client, chat)
    monkeypatch.setattr("assistant.intelligence.call_model", lambda *args: pytest.fail("Unpriced provider called"))
    assert admit(app)[0]["reason_code"] == "MODEL_PRICING_UNVERIFIED"
    assert generate(app) is None


def test_provider_unknown_outcome_and_crashed_claim_are_never_retried(app, owner_client, chat, monkeypatch):
    grant(owner_client, chat)
    incoming(owner_client, chat)
    admit(app)
    called = []
    def unknown(*args):
        called.append(True)
        raise TimeoutError("Private provider error")
    monkeypatch.setattr("assistant.intelligence.call_model", unknown)
    assert generate(app)["status"] == "uncertain"
    assert generate(app) is None and called == [True]
    incoming(owner_client, chat)
    admit(app)
    claimed = _claim(app.state.session_factory, app.state.settings)
    with app.state.session_factory() as db:
        db.get(AutomaticDraftJob, claimed).claim_expires_at = now() - timedelta(seconds=1)
        for slot in db.scalars(select(AutomaticDraftSlot).where(AutomaticDraftSlot.claim_id == claimed)):
            slot.expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert generate(app) is None
    with app.state.session_factory() as db:
        assert db.get(AutomaticDraftJob, claimed).status == "uncertain"


def test_export_forget_and_purge_remove_automatic_authority(app, owner_client, chat):
    grant(owner_client, chat)
    source = incoming(owner_client, chat)
    admit(app)
    exported = owner_client.post("/data-export", params={"workspace_id": chat["workspace"]["id"]}).json()
    assert exported["conversations"][0]["automatic_drafts"]["grant"]["enabled"]
    assert exported["conversations"][0]["automatic_drafts"]["jobs"][0]["message_id"] == source["message_id"]
    memory = owner_client.post(f"/conversations/{chat['conversation']['id']}/memories", json={
        "text": "Remember this source", "status": "confirmed", "source_message_ids": [source["message_id"]]})
    assert memory.status_code == 201
    assert owner_client.delete(f"/memories/{memory.json()['id']}").status_code == 204
    assert generate(app) is None
    assert owner_client.delete(f"/conversations/{chat['conversation']['id']}/data").status_code == 200
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(AutomaticDraftJob.id))) == 0
        assert db.scalar(select(func.count(AutoDraftGrant.id))) == 0


def test_account_purge_during_model_call_cannot_restore_erased_records(app, owner_client, chat, monkeypatch):
    grant(owner_client, chat)
    incoming(owner_client, chat)
    admit(app)
    from assistant.intelligence import call_model
    def purge(settings, context):
        assert owner_client.delete("/account-data", params={"workspace_id": chat["workspace"]["id"]}).status_code == 200
        return call_model(settings, context)
    monkeypatch.setattr("assistant.intelligence.call_model", purge)
    assert generate(app)["status"] == "purged"
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(AutomaticDraftJob.id))) == 0
        assert db.scalar(select(func.count(Draft.id))) == 0
        assert db.scalar(select(func.count(UsageLedger.id))) == 0


def test_slow_provider_does_not_block_job_ticks_or_owner_controls(app, owner_client, chat, monkeypatch):
    grant(owner_client, chat)
    incoming(owner_client, chat)
    admit(app)
    entered, released = threading.Event(), threading.Event()
    from assistant.intelligence import call_model
    def slow(settings, context):
        entered.set()
        assert released.wait(5)
        return call_model(settings, context)
    monkeypatch.setattr("assistant.intelligence.call_model", slow)
    from assistant.jobs import run_worker_tick
    async def scenario():
        work = asyncio.create_task(run_one_generation(app.state.session_factory, app.state.settings))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            ticks = await asyncio.wait_for(run_worker_tick(app.state.session_factory, app.state.settings), 2)
            assert len(ticks) == 5 and all(item.get("status") != "failed" for item in ticks)
            assert owner_client.post(f"/conversations/{chat['conversation']['id']}/takeover").status_code == 200
        finally:
            released.set()
        assert (await work)["status"] == "cancelled"
    asyncio.run(scenario())


def _pg_claim(config, gate, output):
    from assistant.config import Settings
    from assistant.db import make_database
    engine, factory = make_database(Settings(**config))
    gate.wait(10)
    try:
        output.put(_claim(factory, Settings(**config)))
    finally:
        engine.dispose()


def test_postgresql_workers_share_one_owner_claim(app, owner_client, chat):
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("Cross-process claims require PostgreSQL")
    grant(owner_client, chat)
    incoming(owner_client, chat)
    admit(app)
    second = create_chat(owner_client, account="second-same-owner-account")
    grant(owner_client, second)
    incoming(owner_client, second)
    admit(app)
    context = multiprocessing.get_context("spawn")
    gate, output = context.Event(), context.Queue()
    workers = [context.Process(target=_pg_claim, args=(app.state.settings.model_dump(), gate, output)) for _ in range(4)]
    for worker in workers:
        worker.start()
    gate.set()
    results = [output.get(timeout=20) for _ in workers]
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0
    assert sum(value is not None for value in results) == 1


def test_postgresql_workers_share_global_four_call_bound(app, owner_client):
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("Cross-process global claims require PostgreSQL")
    for index in range(6):
        login(owner_client, f"bounded-auto-owner-{index}@example.test")
        chat = create_chat(owner_client, account=f"bounded-auto-account-{index}")
        grant(owner_client, chat)
        incoming(owner_client, chat)
    assert len(admit(app)) == 6
    context = multiprocessing.get_context("spawn")
    gate, output = context.Event(), context.Queue()
    workers = [context.Process(target=_pg_claim, args=(app.state.settings.model_dump(), gate, output)) for _ in range(6)]
    for worker in workers:
        worker.start()
    gate.set()
    results = [output.get(timeout=30) for _ in workers]
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0
    assert sum(value is not None for value in results) == 4
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(AutomaticDraftJob.id)).where(AutomaticDraftJob.status == "generating")) == 4
        assert db.scalar(select(func.count(AutomaticDraftSlot.id)).where(AutomaticDraftSlot.claim_id.is_not(None))) == 4


def test_claim_fairness_passes_one_hundred_busy_owner_jobs(app, owner_client, chat):
    grant(owner_client, chat, max_drafts_per_hour=10)
    incoming(owner_client, chat)
    admit(app)
    busy = _claim(app.state.session_factory, app.state.settings)
    with app.state.session_factory() as db:
        original = db.get(AutomaticDraftJob, busy)
        message = db.get(Message, original.message_id)
        for index in range(100):
            copied = Message(id=uid(), workspace_id=message.workspace_id, connector_id=message.connector_id,
                conversation_id=message.conversation_id, provider_message_id=uid(), sender_id="Contact",
                direction="inbound", origin="live", author_kind="contact_human", text="Fresh test input",
                provider_timestamp=now())
            db.add(copied)
            db.flush()
            db.add(AutomaticDraftJob(id=uid(), workspace_id=original.workspace_id, conversation_id=original.conversation_id,
                connector_id=original.connector_id, owner_id=original.owner_id, grant_id=original.grant_id,
                source_outbox_id=uid(), event_id=uid(), message_id=copied.id, message_revision=1,
                authority_snapshot=original.authority_snapshot, expires_at=now()+timedelta(minutes=10), status="queued"))
        db.commit()
    login(owner_client, "later-auto-owner@example.test")
    later = create_chat(owner_client, account="later-auto-account")
    grant(owner_client, later)
    incoming(owner_client, later)
    admitted = admit(app)[0]
    assert _claim(app.state.session_factory, app.state.settings) is None
    assert _claim(app.state.session_factory, app.state.settings) == admitted["id"]
