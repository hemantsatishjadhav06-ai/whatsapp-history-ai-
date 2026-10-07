import asyncio
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select

from assistant.db import now, uid
from assistant.messaging import content_hash, dispatch_draft, process_due
from assistant.models import (
    Connector, Conversation, Draft, Memory, Message, MessageEvent, Outbox, Permission,
    ScheduledIntent, SendAttempt, Workspace,
)


INTERNAL = {"Authorization": "Bearer test-internal-service-token"}


def make_draft(app, chat, text="Thank you. I will check and reply shortly."):
    with app.state.session_factory() as db:
        conversation = db.get(Conversation, chat["conversation"]["id"])
        permission = db.scalar(select(Permission).where(Permission.conversation_id == conversation.id))
        workspace = db.get(Workspace, conversation.workspace_id)
        connector = db.get(Connector, conversation.connector_id)
        draft = Draft(id=uid(), workspace_id=workspace.id, conversation_id=conversation.id,
                      recipient_id=conversation.provider_chat_id, text=text, evidence_message_ids=[],
                      missing_facts=[], model_version="owner-test", profile_version=0,
                      conversation_revision=conversation.revision, control_epoch=conversation.control_epoch,
                      permission_version=permission.version, pause_generation=workspace.pause_generation,
                      connector_fence=connector.fence, content_hash=content_hash(text))
        db.add(draft)
        db.commit()
        return draft.id, draft.content_hash


def approve(client, draft):
    response = client.post(f"/drafts/{draft[0]}/approve", json={"content_hash": draft[1]})
    assert response.status_code == 200, response.text
    return response.json()


def event(chat, **changes):
    value = {"event_id": uid(), "workspace_id": "untrusted-body-workspace",
             "connector_id": chat["connector"]["id"], "conversation_id": chat["conversation"]["id"],
             "provider_message_id": uid(), "sender_id": "Contact", "direction": "inbound",
             "origin": "live", "event_type": "message.created", "provider_timestamp": now().isoformat(),
             "content": {"type": "text", "text": "Are you available?"}}
    value.update(changes)
    return value


def receive(client, body):
    return client.post("/internal/connector-events", json=body, headers=INTERNAL)


def test_service_auth_required(owner_client, chat):
    assert owner_client.post("/internal/connector-events", json=event(chat)).status_code == 401


def test_event_ownership_is_derived_and_timestamp_independent_dedup(app, owner_client, chat):
    body = event(chat)
    result = receive(owner_client, body)
    assert result.status_code == 200, result.text
    body["event_id"] = uid()
    body["provider_timestamp"] = (now() - timedelta(hours=3)).isoformat()
    assert receive(owner_client, body).json()["status"] == "duplicate"
    with app.state.session_factory() as db:
        message = db.scalar(select(Message))
        assert message.workspace_id == chat["workspace"]["id"]
        assert db.scalar(select(func.count(Message.id))) == 1
        assert db.scalar(select(func.count(MessageEvent.id))) == 1


def test_account_and_conversation_mismatch_rejected(owner_client, chat):
    assert receive(owner_client, event(chat, account_id="wrong-account")).status_code == 403
    assert receive(owner_client, event(chat, conversation_id="other-chat")).status_code == 404


def test_no_retention_drops_content(app, owner_client, chat):
    response = owner_client.put(f"/conversations/{chat['conversation']['id']}/permissions",
                               json={"read": True, "retain": False})
    assert response.status_code == 200
    result = receive(owner_client, event(chat))
    assert result.json()["status"] == "ignored"
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(Message.id))) == 0
        assert db.scalar(select(func.count(MessageEvent.id))) == 0
        assert db.scalar(select(func.count(Outbox.id))) == 0


@pytest.mark.parametrize("origin", ["history", "replay", "unknown"])
def test_nonlive_never_triggers_reply(app, owner_client, chat, origin):
    result = receive(owner_client, event(chat, origin=origin))
    assert result.status_code == 200
    assert result.json()["automatic_reply"] is False
    assert result.json()["live_eligible"] is False
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(SendAttempt.id))) == 0
        assert db.get(Conversation, chat["conversation"]["id"]).last_inbound_at is None


def test_stale_live_event_never_triggers_reply(owner_client, chat):
    result = receive(owner_client, event(chat, provider_timestamp=(now() - timedelta(minutes=3)).isoformat()))
    assert result.json()["live_eligible"] is False
    assert result.json()["automatic_reply"] is False


def test_approval_exact_hash_and_edit_reset(app, owner_client, chat):
    draft = make_draft(app, chat)
    assert owner_client.post(f"/drafts/{draft[0]}/approve", json={"content_hash": "0" * 64}).status_code == 409
    approve(owner_client, draft)
    result = owner_client.patch(f"/drafts/{draft[0]}", json={"text": "Owner-reviewed new text"})
    assert result.status_code == 200
    assert result.json()["approved_hash"] is None
    assert result.json()["status"] == "needs_approval"
    assert owner_client.post(f"/drafts/{draft[0]}/dispatch").status_code == 409


def test_new_message_invalidates_approved_draft(app, owner_client, chat):
    draft = make_draft(app, chat)
    approve(owner_client, draft)
    assert receive(owner_client, event(chat)).status_code == 200
    assert owner_client.post(f"/drafts/{draft[0]}/dispatch").status_code == 409
    with app.state.session_factory() as db:
        assert db.get(Draft, draft[0]).status == "cancelled"
        assert db.scalar(select(func.count(SendAttempt.id))) == 0


def test_pause_cancels_and_resume_does_not_restore(app, owner_client, chat):
    draft = make_draft(app, chat)
    approve(owner_client, draft)
    assert owner_client.post("/pause-all", params={"workspace_id": chat["workspace"]["id"]}).status_code == 200
    assert owner_client.post("/resume-all", params={"workspace_id": chat["workspace"]["id"]}).status_code == 200
    assert owner_client.post(f"/drafts/{draft[0]}/dispatch").status_code == 409


def test_unknown_owner_send_takes_over_and_incoming_does_not_resume(app, owner_client, chat):
    draft = make_draft(app, chat)
    approve(owner_client, draft)
    observed = receive(owner_client, event(chat, sender_id="Owner", direction="outbound"))
    assert observed.json()["author_kind"] == "unknown_owner_outgoing"
    assert receive(owner_client, event(chat)).status_code == 200
    with app.state.session_factory() as db:
        conversation = db.get(Conversation, chat["conversation"]["id"])
        assert conversation.control_state == "HUMAN_TAKEOVER"
        assert conversation.control_epoch >= 1
        assert db.get(Draft, draft[0]).status == "cancelled"
        outgoing = db.scalar(select(Message).where(Message.direction == "outbound"))
        assert outgoing.excluded_from_learning


def test_mock_send_idempotent_and_assistant_echo_not_human(app, owner_client, chat):
    draft = make_draft(app, chat)
    approve(owner_client, draft)
    first = owner_client.post(f"/drafts/{draft[0]}/dispatch")
    assert first.status_code == 200, first.text
    again = owner_client.post(f"/drafts/{draft[0]}/dispatch")
    assert first.json() == again.json()
    provider_id = first.json()["provider_message_id"]
    assert provider_id.startswith("mock:")
    echo = receive(owner_client, event(chat, sender_id="Owner", direction="outbound",
                                       author_kind="human_owner", provider_message_id=provider_id))
    assert echo.json()["author_kind"] == "assistant"
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(SendAttempt.id))) == 1
        assert db.get(Conversation, chat["conversation"]["id"]).control_state == "DRAFT_MODE"
        assert db.scalar(select(Message)).excluded_from_learning


def test_uncertain_send_never_retries(app, owner_client, chat, monkeypatch):
    calls = []

    async def ambiguous(*args):
        calls.append(args)
        return "uncertain", None, "transport_outcome_unknown"

    monkeypatch.setattr("assistant.messaging._transport_send", ambiguous)
    draft = make_draft(app, chat)
    approve(owner_client, draft)
    assert owner_client.post(f"/drafts/{draft[0]}/dispatch").json()["status"] == "uncertain"
    assert owner_client.post(f"/drafts/{draft[0]}/dispatch").json()["status"] == "uncertain"
    assert len(calls) == 1
    # Uncorrelated provider receipts cannot turn uncertainty into success.
    result = owner_client.post("/internal/send-receipts", headers=INTERNAL,
                               json={"connector_id": chat["connector"]["id"],
                                     "provider_message_id": "unknown", "status": "accepted"})
    assert result.status_code == 404


def test_second_authoritative_check_blocks_new_control_state(app, owner_client, chat, monkeypatch):
    from assistant import messaging
    original = messaging._validate_current
    checks = []

    def interleaved(db, draft, settings, approved=True):
        result = original(db, draft, settings, approved)
        checks.append(1)
        if len(checks) == 1:
            with app.state.session_factory() as control:
                workspace = control.get(Workspace, draft.workspace_id)
                workspace.paused = True
                workspace.pause_generation += 1
                control.commit()
        return result

    draft = make_draft(app, chat)
    approve(owner_client, draft)
    monkeypatch.setattr(messaging, "_validate_current", interleaved)
    outcome = asyncio.run(dispatch_draft(app.state.session_factory, app.state.settings, draft[0]))
    assert outcome["status"] == "blocked"
    assert outcome["provider_message_id"] is None


def test_edit_delete_invalidates_memory_evidence(app, owner_client, chat):
    body = event(chat)
    assert receive(owner_client, body).status_code == 200
    with app.state.session_factory() as db:
        message = db.scalar(select(Message))
        memory = Memory(id=uid(), workspace_id=message.workspace_id, conversation_id=message.conversation_id,
                        text="Previously remembered fact", status="approved", source_message_ids=[message.id],
                        source_revision={message.id: 1})
        memory_id = memory.id
        db.add(memory)
        db.commit()
    body.update(event_id=uid(), event_type="message.deleted", source_revision=2)
    assert receive(owner_client, body).status_code == 200
    with app.state.session_factory() as db:
        assert db.get(Memory, memory_id).status == "invalidated"
        message = db.scalar(select(Message))
        assert message.deleted and message.text == ""


def test_deleted_message_cannot_be_restored_by_later_edit_or_replay(app, owner_client, chat):
    body = event(chat)
    assert receive(owner_client, body).status_code == 200
    body.update(event_id=uid(), event_type="message.deleted", source_revision=2)
    assert receive(owner_client, body).status_code == 200
    body.update(event_id=uid(), event_type="message.edited", source_revision=3,
                content={"type": "text", "text": "Content must remain erased"})
    assert receive(owner_client, body).json()["status"] == "ignored"
    body.update(event_id=uid(), event_type="message.created", origin="replay", source_revision=4)
    assert receive(owner_client, body).json()["status"] == "duplicate"
    with app.state.session_factory() as db:
        message = db.scalar(select(Message))
        assert message.deleted and message.text == ""
        assert message.revision == 2


def test_message_edit_cannot_change_identity(owner_client, chat):
    body = event(chat)
    assert receive(owner_client, body).status_code == 200
    body.update(event_id=uid(), event_type="message.edited", source_revision=2,
                sender_id="Impersonated participant")
    assert receive(owner_client, body).status_code == 409


def create_schedule(client, draft, key="schedule-test-1"):
    due = now() + timedelta(minutes=5)
    response = client.post("/scheduled-intents", json={"draft_id": draft[0], "idempotency_key": key,
                                                       "due_at": due.isoformat(),
                                                       "expires_at": (due + timedelta(minutes=10)).isoformat(),
                                                       "timezone": "Asia/Kolkata",
                                                       "original_expression": "in five minutes"})
    assert response.status_code == 201, response.text
    return response.json()


def test_schedule_durable_outbox_and_due_uses_single_dispatcher(app, owner_client, chat):
    draft = make_draft(app, chat)
    approve(owner_client, draft)
    scheduled = create_schedule(owner_client, draft)
    with app.state.session_factory() as db:
        intent = db.get(ScheduledIntent, scheduled["id"])
        assert intent.timezone == "Asia/Kolkata"
        outbox = db.scalar(select(Outbox).where(Outbox.kind == "schedule.register"))
        assert outbox.aggregate_id == intent.id
        intent.due_at = now() - timedelta(seconds=1)
        db.commit()
    results = asyncio.run(process_due(app.state.session_factory, app.state.settings))
    assert results[0]["status"] == "accepted"
    assert asyncio.run(process_due(app.state.session_factory, app.state.settings)) == []
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(SendAttempt.id))) == 1


def test_schedule_idempotency_and_cancel(app, owner_client, chat):
    draft = make_draft(app, chat)
    approve(owner_client, draft)
    scheduled = create_schedule(owner_client, draft)
    body = {k: scheduled[k] for k in ("draft_id", "idempotency_key", "due_at", "expires_at", "timezone", "original_expression")}
    repeated = owner_client.post("/scheduled-intents", json=body)
    assert repeated.status_code == 201
    assert repeated.json()["id"] == scheduled["id"]
    body["due_at"] = (now() + timedelta(minutes=10)).isoformat()
    assert owner_client.post("/scheduled-intents", json=body).status_code == 409
    assert owner_client.delete(f"/scheduled-intents/{scheduled['id']}").json()["status"] == "cancelled"
    with app.state.session_factory() as db:
        db.get(ScheduledIntent, scheduled["id"]).due_at = now() - timedelta(seconds=1)
        db.commit()
    assert asyncio.run(process_due(app.state.session_factory, app.state.settings)) == []


def test_expired_schedule_never_replayed(app, owner_client, chat):
    draft = make_draft(app, chat)
    approve(owner_client, draft)
    scheduled = create_schedule(owner_client, draft)
    with app.state.session_factory() as db:
        intent = db.get(ScheduledIntent, scheduled["id"])
        intent.due_at = now() - timedelta(hours=2)
        intent.expires_at = now() - timedelta(hours=1)
        db.commit()
    results = asyncio.run(process_due(app.state.session_factory, app.state.settings))
    assert results[0]["status"] == "expired"
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(SendAttempt.id))) == 0


def test_schedule_timezone_and_approval_expiry_validation(app, owner_client, chat):
    draft = make_draft(app, chat)
    approve(owner_client, draft)
    body = {"draft_id": draft[0], "idempotency_key": "bad-timezone",
            "due_at": (now() + timedelta(minutes=5)).isoformat(),
            "expires_at": (now() + timedelta(minutes=10)).isoformat(), "timezone": "Invalid/Zone"}
    assert owner_client.post("/scheduled-intents", json=body).status_code == 422
    body["timezone"] = "Asia/Kolkata"
    body["due_at"] = (now() + timedelta(hours=2)).isoformat()
    body["expires_at"] = (now() + timedelta(hours=3)).isoformat()
    assert owner_client.post("/scheduled-intents", json=body).status_code == 409


@pytest.mark.parametrize("lease", [None, "expired"])
def test_missing_or_expired_gateway_lease_blocks_send(app, owner_client, chat, lease):
    draft = make_draft(app, chat)
    approve(owner_client, draft)
    with app.state.session_factory() as db:
        connector = db.get(Connector, chat["connector"]["id"])
        connector.lease_expires_at = None if lease is None else now() - timedelta(seconds=1)
        db.commit()
    assert owner_client.post(f"/drafts/{draft[0]}/dispatch").status_code == 409


def test_unknown_capability_does_not_authorize_send(app, owner_client, chat):
    draft = make_draft(app, chat)
    with app.state.session_factory() as db:
        connector = db.get(Connector, chat["connector"]["id"])
        connector.capabilities = {**connector.capabilities, "send_text": "unknown"}
        db.commit()
    assert owner_client.post(f"/drafts/{draft[0]}/approve", json={"content_hash": draft[1]}).status_code == 403


def test_context_expiry_invalidates_previous_approval(app, owner_client, chat):
    draft = make_draft(app, chat)
    with app.state.session_factory() as db:
        db.get(Draft, draft[0]).context_expires_at = now() + timedelta(minutes=5)
        db.commit()
    approved = approve(owner_client, draft)
    assert approved["approval_expires_at"] is not None
    with app.state.session_factory() as db:
        db.get(Draft, draft[0]).context_expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert owner_client.post(f"/drafts/{draft[0]}/dispatch").status_code == 409


def test_lease_heartbeat_rejects_stale_fence_and_expiry(app, owner_client, chat):
    body = {"connector_id": chat["connector"]["id"], "fence": chat["connector"]["fence"]}
    renewed = owner_client.post("/internal/connector-leases/renew", json=body, headers=INTERNAL)
    assert renewed.status_code == 200
    body["fence"] += 1
    assert owner_client.post("/internal/connector-leases/renew", json=body, headers=INTERNAL).status_code == 409
    body["fence"] -= 1
    with app.state.session_factory() as db:
        db.get(Connector, body["connector_id"]).lease_expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert owner_client.post("/internal/connector-leases/renew", json=body, headers=INTERNAL).status_code == 409


def configure_cloud(app, chat):
    settings = app.state.settings
    settings.enable_external_sends = True
    settings.whatsapp_phone_number_id = "business-phone-id"
    settings.whatsapp_access_token = "test-provider-placeholder"
    settings.whatsapp_authorized_owner_subject = "business-test-owner"
    with app.state.session_factory() as db:
        from assistant.models import User, Workspace
        workspace = db.get(Workspace, chat["workspace"]["id"])
        db.get(User, workspace.owner_id).subject = "google:business-test-owner"
        connector = db.get(Connector, chat["connector"]["id"])
        connector.provider = "whatsapp_cloud"
        connector.account_id = settings.whatsapp_phone_number_id
        conversation = db.get(Conversation, chat["conversation"]["id"])
        conversation.last_inbound_at = now() - timedelta(minutes=1)
        db.commit()


@pytest.mark.parametrize("binding", ["", "different-owner"])
def test_business_owner_binding_revocation_blocks_previously_approved_draft_without_network(
        app, owner_client, chat, monkeypatch, binding):
    configure_cloud(app, chat)
    draft = make_draft(app, chat)
    approve(owner_client, draft)
    app.state.settings.whatsapp_authorized_owner_subject = binding
    def forbidden(**_):
        raise AssertionError("Revoked Business owner cannot start provider networking")
    monkeypatch.setattr("assistant.messaging.httpx.AsyncClient", forbidden)
    response = owner_client.post(f"/drafts/{draft[0]}/dispatch")
    assert response.status_code == 403
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(SendAttempt)) == 0
        assert db.get(Draft, draft[0]).status == "approved"


def test_business_owner_binding_is_rechecked_after_network_client_setup(app, owner_client, chat, monkeypatch):
    configure_cloud(app, chat)
    draft = make_draft(app, chat)
    approve(owner_client, draft)
    class RevokingClient:
        async def __aenter__(self):
            app.state.settings.whatsapp_authorized_owner_subject = ""
            return self
        async def __aexit__(self, *_):
            return None
        async def post(self, *_, **__):
            raise AssertionError("Latest Business owner denial must prevent submission")
    monkeypatch.setattr("assistant.messaging.httpx.AsyncClient", lambda **_: RevokingClient())
    response = owner_client.post(f"/drafts/{draft[0]}/dispatch")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "blocked"
    with app.state.session_factory() as db:
        attempt = db.scalar(select(SendAttempt))
        assert attempt.status == "blocked"
        assert db.get(Draft, draft[0]).status == "cancelled"


@pytest.mark.parametrize("constraint", ["disabled", "optout", "no_optin", "window_expired", "group"])
def test_business_transport_checks_current_provider_constraints(app, owner_client, chat, constraint):
    configure_cloud(app, chat)
    draft = make_draft(app, chat)
    approve(owner_client, draft)
    with app.state.session_factory() as db:
        conversation = db.get(Conversation, chat["conversation"]["id"])
        if constraint == "disabled":
            app.state.settings.enable_external_sends = False
        elif constraint == "optout":
            conversation.recipient_opted_out = True
        elif constraint == "no_optin":
            conversation.recipient_opted_in = False
        elif constraint == "window_expired":
            conversation.last_inbound_at = now() - timedelta(hours=25)
        elif constraint == "group":
            conversation.kind = "group"
            conversation.group_send_allowed = True
        db.commit()
    assert owner_client.post(f"/drafts/{draft[0]}/dispatch").status_code == 403


def test_cloud_transport_http_contract_and_delivered_receipt(app, owner_client, chat, monkeypatch):
    configure_cloud(app, chat)
    captured = []
    actual_client = httpx.AsyncClient

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.test-send"}]})

    monkeypatch.setattr("assistant.messaging.httpx.AsyncClient",
                        lambda **kwargs: actual_client(transport=httpx.MockTransport(handler), **kwargs))
    draft = make_draft(app, chat)
    approve(owner_client, draft)
    result = owner_client.post(f"/drafts/{draft[0]}/dispatch")
    assert result.json()["status"] == "accepted"
    assert captured[0].url.host == "graph.facebook.com"
    assert captured[0].url.scheme == "https"
    assert captured[0].url.path.endswith("/business-phone-id/messages")
    assert captured[0].headers["authorization"] == "Bearer test-provider-placeholder"
    receipt = owner_client.post("/internal/send-receipts", headers=INTERNAL,
                                json={"connector_id": chat["connector"]["id"],
                                      "provider_message_id": "wamid.test-send", "status": "delivered"})
    assert receipt.json()["status"] == "delivered"
    assert owner_client.post(f"/drafts/{draft[0]}/dispatch").json()["status"] == "delivered"
    assert len(captured) == 1


def test_confirmed_receipt_clears_uncertain_error_and_settles_usage(app, owner_client, chat):
    from assistant.people_models import UsageLedger
    draft = make_draft(app, chat)
    approve(owner_client, draft)
    response = owner_client.post(f"/drafts/{draft[0]}/dispatch")
    assert response.status_code == 200 and response.json()["status"] == "accepted"
    provider_id = response.json()["provider_message_id"]
    with app.state.session_factory() as db:
        attempt = db.scalar(select(SendAttempt).where(SendAttempt.draft_id == draft[0]))
        attempt.status, attempt.error_code = "uncertain", "transport_outcome_unknown"
        db.get(Draft, draft[0]).status = "uncertain"
        usage = db.scalar(select(UsageLedger).where(UsageLedger.operation_key == f"action:draft:{draft[0]}"))
        usage.status = "uncertain"
        db.commit()
    confirmed = owner_client.post("/internal/send-receipts", headers=INTERNAL, json={
        "connector_id": chat["connector"]["id"], "provider_message_id": provider_id, "status": "delivered"})
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "delivered" and confirmed.json()["error_code"] is None
    with app.state.session_factory() as db:
        usage = db.scalar(select(UsageLedger).where(UsageLedger.operation_key == f"action:draft:{draft[0]}"))
        assert usage.status == "consumed" and usage.action_units == 1


def test_real_timeout_is_uncertain_without_retry(app, owner_client, chat, monkeypatch):
    configure_cloud(app, chat)
    calls = []
    actual_client = httpx.AsyncClient

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("Provider outcome unknown", request=request)

    monkeypatch.setattr("assistant.messaging.httpx.AsyncClient",
                        lambda **kwargs: actual_client(transport=httpx.MockTransport(handler), **kwargs))
    draft = make_draft(app, chat)
    approve(owner_client, draft)
    assert owner_client.post(f"/drafts/{draft[0]}/dispatch").json()["status"] == "uncertain"
    assert owner_client.post(f"/drafts/{draft[0]}/dispatch").json()["status"] == "uncertain"
    assert len(calls) == 1


def test_schedule_cancelled_between_claim_and_submit_never_sends(app, owner_client, chat, monkeypatch):
    draft = make_draft(app, chat)
    approve(owner_client, draft)
    scheduled = create_schedule(owner_client, draft)
    with app.state.session_factory() as db:
        db.get(ScheduledIntent, scheduled["id"]).due_at = now() - timedelta(seconds=1)
        db.commit()
    sent = []

    async def interleaved(settings, provider, account, recipient, text, attempt, final_check):
        # Cancellation can succeed after an SQL claim but before external submission.
        assert owner_client.delete(f"/scheduled-intents/{scheduled['id']}").status_code == 200
        final_check()
        sent.append(text)
        return "accepted", "must-not-send", None

    monkeypatch.setattr("assistant.messaging._transport_send", interleaved)
    result = asyncio.run(process_due(app.state.session_factory, app.state.settings))
    assert result[0]["status"] == "blocked"
    assert sent == []


def test_schedule_cannot_be_sent_early_from_manual_dispatch(app, owner_client, chat):
    draft = make_draft(app, chat)
    approve(owner_client, draft)
    create_schedule(owner_client, draft)
    assert owner_client.post(f"/drafts/{draft[0]}/dispatch").status_code == 409
