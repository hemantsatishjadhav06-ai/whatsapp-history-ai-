"""Personal session erasure through generic owner controls and provider logout."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from assistant import core
from assistant.db import now, uid
from assistant.messaging import content_hash
from assistant.models import Connector, Conversation, Draft, Message, Permission, Workspace
from assistant.whatsapp_personal_models import PersonalAccountAlias, PersonalAuthKey, PersonalWhatsAppSession
from conftest import login


def _seed_personal(app, workspace_id):
    account = f"{int(uid().replace('-', '')[:10], 16)}@s.whatsapp.net"
    with app.state.session_factory() as db:
        connector = Connector(workspace_id=workspace_id, provider="whatsapp_personal", account_id=account,
                              owner_sender_id=account, status="connected", fence=7,
                              lease_expires_at=now() + timedelta(minutes=1))
        db.add(connector)
        db.flush()
        conversation = Conversation(workspace_id=workspace_id, connector_id=connector.id,
                                    provider_chat_id="15550002222@s.whatsapp.net", title="Personal contact",
                                    kind="contact", revision=4, control_epoch=3, recipient_opted_in=True)
        db.add(conversation)
        db.flush()
        grant = Permission(workspace_id=workspace_id, conversation_id=conversation.id,
                           read=True, retain=True, learn=True, draft=True, send=True)
        message = Message(workspace_id=workspace_id, connector_id=connector.id,
                          conversation_id=conversation.id, provider_message_id="synthetic-personal-message",
                          sender_id=conversation.provider_chat_id, direction="inbound", origin="live",
                          author_kind="contact_human", text="Synthetic retained personal text",
                          provider_timestamp=now())
        draft = Draft(workspace_id=workspace_id, conversation_id=conversation.id,
                      recipient_id=conversation.provider_chat_id, text="Synthetic pending reply",
                      model_version="synthetic", conversation_revision=conversation.revision,
                      control_epoch=conversation.control_epoch, permission_version=1,
                      pause_generation=0, connector_fence=connector.fence,
                      content_hash=content_hash("Synthetic pending reply"), status="approved",
                      approved_hash=content_hash("Synthetic pending reply"),
                      approval_expires_at=now() + timedelta(minutes=5))
        session = PersonalWhatsAppSession(workspace_id=workspace_id, connector_id=connector.id,
                                          connector_fence=connector.fence, status="connected")
        db.add_all([grant, message, draft, session,
                    PersonalAuthKey(workspace_id=workspace_id, connector_id=connector.id,
                                    key_type="creds", key_id="creds", ciphertext="opaque-synthetic-envelope",
                                    updated_at=now()),
                    PersonalAccountAlias(alias_id=account, workspace_id=workspace_id, connector_id=connector.id)])
        db.commit()
        return {"workspace_id": workspace_id, "connector_id": connector.id, "conversation_id": conversation.id,
                "message_id": message.id, "draft_id": draft.id, "session_id": session.id, "account_id": account,
                "connector_fence": connector.fence}


def _credentials_present(db, personal):
    return (db.get(PersonalWhatsAppSession, personal["session_id"]) is not None,
            db.get(PersonalAuthKey, (personal["connector_id"], "creds", "creds")) is not None)


def _configure_internal(app):
    app.state.settings.whatsapp_personal_enabled = True
    app.state.settings.whatsapp_personal_session_url = "http://session-service.invalid"
    app.state.settings.whatsapp_personal_session_token = "synthetic-session-token-of-at-least-32-characters"
    return {"Authorization": f"Bearer {app.state.settings.internal_service_token}"}


def _authority(client, personal, headers):
    return client.post("/internal/whatsapp-session-authority", headers=headers,
                       json={key: personal[key] for key in
                             ("workspace_id", "connector_id", "connector_fence", "account_id")} |
                            {"operation": "status"})


def test_generic_disconnect_erases_keys_fences_actor_and_preserves_other_workspace(app, owner_client, chat):
    personal = _seed_personal(app, chat["workspace"]["id"])
    other_workspace = owner_client.post("/workspaces", json={"name": "Other workspace"}).json()
    other = _seed_personal(app, other_workspace["id"])
    headers = _configure_internal(app)
    assert _authority(owner_client, personal, headers).json()["allowed"] is True

    response = owner_client.delete(f"/connectors/{personal['connector_id']}")
    assert response.status_code == 200, response.text
    assert response.json()["fence"] == personal["connector_fence"] + 1
    assert _authority(owner_client, personal, headers).json()["allowed"] is False
    with app.state.session_factory() as db:
        assert _credentials_present(db, personal) == (False, False)
        assert _credentials_present(db, other) == (True, True)
        connector = db.get(Connector, personal["connector_id"])
        conversation = db.get(Conversation, personal["conversation_id"])
        assert connector.status == "disconnected"
        assert conversation.revision == 5 and conversation.control_epoch == 4
        assert conversation.control_state == "RECONNECT_REVIEW"
        assert db.get(Draft, personal["draft_id"]).status == "cancelled"
        assert db.get(Message, personal["message_id"]).text == "Synthetic retained personal text"
        assert db.get(PersonalAccountAlias, personal["account_id"]) is not None
        assert db.get(Connector, other["connector_id"]).fence == other["connector_fence"]
        assert db.get(Draft, other["draft_id"]).status == "approved"


@pytest.mark.parametrize("operation", ["connector", "account_data"])
def test_other_owner_cannot_erase_personal_session(app, owner_client, chat, operation):
    personal = _seed_personal(app, chat["workspace"]["id"])
    login(owner_client, "unrelated-personal-owner@example.test")
    if operation == "connector":
        response = owner_client.delete(f"/connectors/{personal['connector_id']}")
    else:
        response = owner_client.delete("/account-data", params={"workspace_id": personal["workspace_id"]})
    assert response.status_code == 404
    with app.state.session_factory() as db:
        assert _credentials_present(db, personal) == (True, True)
        assert db.get(Connector, personal["connector_id"]).fence == personal["connector_fence"]
        assert db.get(Draft, personal["draft_id"]).status == "approved"


def test_over_limit_account_purge_commits_personal_key_erasure_before_rejecting_content(
        app, owner_client, chat, monkeypatch):
    personal = _seed_personal(app, chat["workspace"]["id"])
    headers = _configure_internal(app)
    monkeypatch.setattr(core, "MAX_PRIVATE_OPERATION_ROWS", 0)
    response = owner_client.delete("/account-data", params={"workspace_id": personal["workspace_id"]})
    assert response.status_code == 413, response.text
    assert response.json()["detail"]["operation"] == "purge_workspace"
    assert _authority(owner_client, personal, headers).json()["allowed"] is False
    with app.state.session_factory() as db:
        assert _credentials_present(db, personal) == (False, False)
        assert db.get(Connector, personal["connector_id"]).fence == personal["connector_fence"] + 1
        assert db.get(Connector, personal["connector_id"]).status == "disconnected"
        assert db.get(Conversation, personal["conversation_id"]).revision == 4
        assert db.get(Message, personal["message_id"]).text == "Synthetic retained personal text"
        assert db.get(Message, personal["message_id"]).deleted is False
        assert db.get(Workspace, personal["workspace_id"]).paused is False
        assert db.get(PersonalAccountAlias, personal["account_id"]) is not None


def test_completed_account_purge_erases_personal_credentials_and_private_content(app, owner_client, chat):
    personal = _seed_personal(app, chat["workspace"]["id"])
    response = owner_client.delete("/account-data", params={"workspace_id": personal["workspace_id"]})
    assert response.status_code == 200, response.text
    with app.state.session_factory() as db:
        assert _credentials_present(db, personal) == (False, False)
        assert db.get(Connector, personal["connector_id"]).fence > personal["connector_fence"]
        assert db.get(Workspace, personal["workspace_id"]).paused is True
        message = db.get(Message, personal["message_id"])
        draft = db.get(Draft, personal["draft_id"])
        assert message.deleted is True and message.text == ""
        assert draft.status == "cancelled" and draft.text == ""
        grant = db.scalar(select(Permission).where(Permission.conversation_id == personal["conversation_id"]))
        assert not any((grant.read, grant.retain, grant.learn, grant.draft, grant.send))
        assert db.get(PersonalAccountAlias, personal["account_id"]) is not None


def test_provider_logout_erases_keys_increments_fence_and_rejects_stale_reconnect(app, owner_client, chat):
    personal = _seed_personal(app, chat["workspace"]["id"])
    headers = _configure_internal(app)
    event = {key: personal[key] for key in ("workspace_id", "connector_id", "connector_fence", "account_id")}
    response = owner_client.post("/internal/whatsapp-session-events", headers=headers,
                                 json={**event, "event_type": "connection", "data": {"state": "logged_out"}})
    assert response.status_code == 200, response.text
    assert response.json()["connector_fence"] == personal["connector_fence"] + 1
    stale = owner_client.post("/internal/whatsapp-session-events", headers=headers,
                              json={**event, "event_type": "connection", "data": {
                                  "state": "connected", "account_id": personal["account_id"]}})
    assert stale.status_code == 409, stale.text
    with app.state.session_factory() as db:
        assert _credentials_present(db, personal) == (False, False)
        assert db.get(Connector, personal["connector_id"]).status == "logged_out"
        assert db.get(Draft, personal["draft_id"]).status == "cancelled"
        assert db.get(Conversation, personal["conversation_id"]).control_state == "RECONNECT_REVIEW"
        assert db.get(PersonalAccountAlias, personal["account_id"]) is not None
