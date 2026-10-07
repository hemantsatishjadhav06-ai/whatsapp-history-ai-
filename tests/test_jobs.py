import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from assistant.action_models import OutboundAction, SubmissionAttempt
from assistant.db import aware, now
from assistant.jobs import forget_job_sources, next_occurrence, process_jobs, resolve_local
from assistant.jobs_models import AuthorizedJob, JobRun
from assistant.messaging import process_due
from assistant.models import Message, ScheduledIntent, SendAttempt
from conftest import create_chat, login
from test_messaging import INTERNAL, approve, create_schedule, event, make_draft, receive


def payload(chat, **changes):
    due = now() + timedelta(minutes=5)
    result = {"workspace_id": chat["workspace"]["id"], "conversation_id": chat["conversation"]["id"],
              "idempotency_key": "owner-job-test-1", "purpose": "Owner-authorized follow-up", "action_kind": "SEND_TEXT",
              "content": "Our meeting is tomorrow at 10.", "due_at": due.isoformat(),
              "expires_at": (due + timedelta(days=2)).isoformat(), "timezone": "Asia/Kolkata"}
    result.update(changes)
    return result


def create_job(client, chat, **changes):
    response = client.post("/jobs", json=payload(chat, **changes))
    assert response.status_code == 201, response.text
    return response.json()


def make_due(app, job, *, ago=1):
    with app.state.session_factory() as db:
        db.get(AuthorizedJob, job["id"]).due_at = now() - timedelta(seconds=ago)
        db.commit()


def run(app):
    return asyncio.run(process_jobs(app.state.session_factory, app.state.settings))


def test_job_is_explicit_trusted_trigger_and_one_time_exact_content(app, owner_client, chat):
    job = create_job(owner_client, chat)
    assert job["trigger_type"] == "AUTHORIZED_JOB"
    make_due(app, job)
    outcome = run(app)
    assert outcome[0]["status"] == "accepted"
    assert run(app) == []
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(Message.id))) == 0
        assert db.scalar(select(func.count(SubmissionAttempt.id))) == 1
        action = db.scalar(select(OutboundAction))
        assert action.trigger_message_id is None
        assert action.authorized_job_id == job["id"]
        assert action.payload["text"] == job["content"]
        assert db.get(AuthorizedJob, job["id"]).status == "completed"


def test_job_idempotency_rejects_changed_audience_or_payload(owner_client, chat):
    body = payload(chat)
    first = owner_client.post("/jobs", json=body)
    assert first.status_code == 201
    assert owner_client.post("/jobs", json=body).json()["id"] == first.json()["id"]
    body["content"] = "Different content"
    assert owner_client.post("/jobs", json=body).status_code == 409


@pytest.mark.parametrize("changes", [
    {"conversation_id": None}, {"due_at": "2026-01-01T09:00:00"}, {"due_at": 1791255600},
    {"timezone": "Mars/Test"}, {"recurrence": "none", "max_runs": 2}, {"quiet_start": "21:00"},
    {"quiet_start": "21:00", "quiet_end": "21:00"}, {"target_message_id": "fabricated"},
    {"action_kind": "REMINDER", "conversation_id": None, "memory_ids": ["fabricated"]},
])
def test_job_contract_rejects_ambiguous_or_unscoped_input(owner_client, chat, changes):
    assert owner_client.post("/jobs", json=payload(chat, **changes)).status_code == 422


def test_job_cross_tenant_refs_denied(owner_client, chat):
    job = create_job(owner_client, chat)
    login(owner_client, "intruder@example.test")
    assert owner_client.get(f"/jobs/{job['id']}").status_code == 404
    assert owner_client.get(f"/jobs/{job['id']}/runs").status_code == 404
    assert owner_client.patch(f"/jobs/{job['id']}", json={"expected_version": 1, "operation": "cancel"}).status_code == 404
    other = create_chat(owner_client, account="other-job-account")
    assert owner_client.post("/jobs", json=payload(other, conversation_id=chat["conversation"]["id"])).status_code == 404


def test_global_pause_holds_external_job_and_local_reminder_remains_independent(app, owner_client, chat):
    external = create_job(owner_client, chat)
    local = create_job(owner_client, chat, idempotency_key="local-reminder-1", conversation_id=None,
                       action_kind="REMINDER", content="Review my notes")
    assert owner_client.post("/pause-all", params={"workspace_id": chat["workspace"]["id"]}).status_code == 200
    make_due(app, external)
    make_due(app, local)
    outcomes = run(app)
    assert {row["status"] for row in outcomes} == {"held", "reminded"}
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(SubmissionAttempt.id))) == 0
        assert db.get(AuthorizedJob, external["id"]).hold_reason == "GLOBAL_PAUSE"
        assert db.get(AuthorizedJob, local["id"]).status == "completed"
    assert owner_client.post("/resume-all", params={"workspace_id": chat["workspace"]["id"]}).status_code == 200
    assert run(app)[0]["status"] == "accepted"


def test_paused_job_expires_after_outage_without_late_send(app, owner_client, chat):
    job = create_job(owner_client, chat, max_lateness_seconds=30)
    owner_client.post("/pause-all", params={"workspace_id": chat["workspace"]["id"]})
    make_due(app, job, ago=31)
    assert run(app)[0]["status"] == "expired"
    owner_client.post("/resume-all", params={"workspace_id": chat["workspace"]["id"]})
    assert run(app) == []


def test_owner_hold_resume_and_version_cas(app, owner_client, chat):
    job = create_job(owner_client, chat)
    controlled = owner_client.patch(f"/jobs/{job['id']}", json={"expected_version": 1, "operation": "hold"})
    assert controlled.status_code == 200
    assert controlled.json()["hold_reason"] == "OWNER_HOLD"
    assert owner_client.patch(f"/jobs/{job['id']}", json={"expected_version": 1, "operation": "resume"}).status_code == 409
    make_due(app, job)
    assert run(app)[0]["status"] == "held"
    resumed = owner_client.patch(f"/jobs/{job['id']}", json={"expected_version": 2, "operation": "resume"})
    assert resumed.status_code == 200
    assert run(app)[0]["status"] == "accepted"


@pytest.mark.parametrize("control", ["permissions", "takeover", "disconnect"])
def test_revoked_authority_blocks_trusted_job_at_due(app, owner_client, chat, control):
    job = create_job(owner_client, chat)
    conversation = chat["conversation"]["id"]
    if control == "permissions":
        owner_client.put(f"/conversations/{conversation}/permissions", json={"read": True, "retain": True, "draft": True})
    elif control == "takeover":
        owner_client.post(f"/conversations/{conversation}/takeover")
    else:
        response = owner_client.delete(f"/connectors/{chat['connector']['id']}")
        assert response.status_code == 200
    make_due(app, job)
    assert run(app)[0]["status"] == "needs_review"
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(SubmissionAttempt.id))) == 0


def test_job_sources_edit_and_forget_immediately_block(app, owner_client, chat):
    body = event(chat, origin="history", content={"type": "text", "text": "Saved factual source"})
    assert receive(owner_client, body).status_code == 200
    with app.state.session_factory() as db:
        source = db.scalar(select(Message))
        source_id = source.id
    job = create_job(owner_client, chat, evidence_message_ids=[source_id])
    body.update(event_id="source-edit-event", event_type="message.edited", source_revision=2,
                content={"type": "text", "text": "Corrected factual source"})
    assert receive(owner_client, body).status_code == 200
    make_due(app, job)
    assert run(app)[0]["status"] == "needs_review"
    with app.state.session_factory() as db:
        forget_job_sources(db, chat["conversation"]["id"], [source_id])
        db.commit()
        current = db.get(AuthorizedJob, job["id"])
        assert current.status == "cancelled" and current.content == ""


def test_daily_recurrence_preserves_wall_clock_across_dst():
    job = SimpleNamespace(due_at=datetime(2026, 3, 7, 14, tzinfo=UTC), recurrence="daily",
                          timezone="America/New_York", local_time="09:00:00",
                          ambiguity_policy="reject", gap_policy="reject")
    assert next_occurrence(job) == datetime(2026, 3, 8, 13, tzinfo=UTC)


def test_weekly_recurrence_preserves_wall_clock_across_dst():
    job = SimpleNamespace(due_at=datetime(2026, 10, 25, 13, tzinfo=UTC), recurrence="weekly",
                          timezone="America/New_York", local_time="09:00:00",
                          ambiguity_policy="reject", gap_policy="reject")
    assert next_occurrence(job) == datetime(2026, 11, 1, 14, tzinfo=UTC)


def test_dst_ambiguity_and_gap_require_explicit_resolution():
    ambiguous = datetime(2026, 11, 1, 1, 30)
    with pytest.raises(ValueError, match="DST_AMBIGUOUS"):
        resolve_local(ambiguous, "America/New_York")
    assert resolve_local(ambiguous, "America/New_York", "earlier") == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert resolve_local(ambiguous, "America/New_York", "later") == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)
    gap = datetime(2026, 3, 8, 2, 30)
    with pytest.raises(ValueError, match="DST_GAP"):
        resolve_local(gap, "America/New_York")
    assert resolve_local(gap, "America/New_York", gap_policy="skip") is None


def test_expired_recurrence_skips_instead_of_flooding(app, owner_client, chat):
    job = create_job(owner_client, chat, recurrence="daily", max_runs=3, max_lateness_seconds=30)
    make_due(app, job, ago=31)
    assert run(app)[0]["reason_code"] == "EXPIRED"
    assert run(app) == []
    with app.state.session_factory() as db:
        current = db.get(AuthorizedJob, job["id"])
        assert current.runs_done == 1 and aware(current.due_at) > now()
        assert db.scalar(select(func.count(SubmissionAttempt.id))) == 0
        assert db.scalar(select(JobRun)).status == "expired"


def test_quiet_hours_hold_within_lateness_budget(app, owner_client, chat):
    job = create_job(owner_client, chat, timezone="UTC", quiet_start=(now() - timedelta(hours=1)).strftime("%H:%M"),
                     quiet_end=(now() + timedelta(hours=1)).strftime("%H:%M"))
    make_due(app, job)
    assert run(app)[0]["reason_code"] == "QUIET_HOURS"
    with app.state.session_factory() as db:
        current = db.get(AuthorizedJob, job["id"])
        current.quiet_start, current.quiet_end = None, None
        db.commit()
    assert run(app)[0]["status"] == "accepted"


def test_delivery_uncertainty_is_not_retried(app, owner_client, chat, monkeypatch):
    from assistant import actions
    async def uncertain(envelope, authority):
        authority()
        return {"status": "uncertain", "error_code": "DELIVERY_UNCERTAIN"}
    monkeypatch.setattr(actions, "submit_simulated", uncertain)
    job = create_job(owner_client, chat)
    make_due(app, job)
    assert run(app)[0]["status"] == "uncertain"
    assert run(app) == []
    assert owner_client.patch(f"/jobs/{job['id']}", json={"expected_version": 1, "operation": "resume"}).status_code == 409
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(SubmissionAttempt.id))) == 1


def test_job_workspace_quota_holds_until_budget_is_changed(app, owner_client, chat):
    workspace_id = chat["workspace"]["id"]
    response = owner_client.put(f"/workspaces/{workspace_id}/budget",
                                json={"expected_version": 0, "max_actions_per_day": 0})
    assert response.status_code == 200
    job = create_job(owner_client, chat)
    make_due(app, job)
    outcome = run(app)
    assert outcome[0]["status"] == "held" and outcome[0]["reason_code"] == "QUOTA_HELD"
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(SubmissionAttempt.id))) == 0
    response = owner_client.put(f"/workspaces/{workspace_id}/budget",
                                json={"expected_version": 1, "max_actions_per_day": 1})
    assert response.status_code == 200
    assert run(app)[0]["status"] == "accepted"


def test_job_authority_rechecked_at_submission_after_owner_pause(app, owner_client, chat, monkeypatch):
    from assistant import actions
    async def pause_before_submit(envelope, authority):
        response = owner_client.post("/pause-all", params={"workspace_id": chat["workspace"]["id"]})
        assert response.status_code == 200
        authority()
        pytest.fail("Paused job reached simulated provider submission")
    monkeypatch.setattr(actions, "submit_simulated", pause_before_submit)
    job = create_job(owner_client, chat)
    make_due(app, job)
    result = run(app)[0]
    assert result["status"] == "blocked" and result["reason_code"] == "GLOBAL_PAUSE"
    with app.state.session_factory() as db:
        assert db.scalar(select(SubmissionAttempt)).status == "failed"


def test_legacy_schedule_pause_preserves_exact_approval_and_resumes(app, owner_client, chat):
    draft = make_draft(app, chat)
    approve(owner_client, draft)
    schedule = create_schedule(owner_client, draft)
    owner_client.post("/pause-all", params={"workspace_id": chat["workspace"]["id"]})
    with app.state.session_factory() as db:
        intent = db.get(ScheduledIntent, schedule["id"])
        assert intent.status == "held"
        intent.due_at = now() - timedelta(seconds=1)
        db.commit()
    assert asyncio.run(process_due(app.state.session_factory, app.state.settings))[0]["status"] == "held"
    owner_client.post("/resume-all", params={"workspace_id": chat["workspace"]["id"]})
    assert asyncio.run(process_due(app.state.session_factory, app.state.settings))[0]["status"] == "accepted"
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(SendAttempt.id))) == 1


def test_legacy_held_schedule_is_cancelled_by_later_permission_change(app, owner_client, chat):
    draft = make_draft(app, chat)
    approve(owner_client, draft)
    schedule = create_schedule(owner_client, draft)
    owner_client.post("/pause-all", params={"workspace_id": chat["workspace"]["id"]})
    owner_client.put(f"/conversations/{chat['conversation']['id']}/permissions", json={"read": True, "retain": True})
    owner_client.post("/resume-all", params={"workspace_id": chat["workspace"]["id"]})
    with app.state.session_factory() as db:
        assert db.get(ScheduledIntent, schedule["id"]).status == "cancelled"


def test_internal_job_runner_requires_service_auth(owner_client):
    assert owner_client.post("/internal/jobs/run-due").status_code == 401
    assert owner_client.post("/internal/jobs/run-due", headers=INTERNAL).status_code == 200
