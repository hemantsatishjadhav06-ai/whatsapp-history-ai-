from datetime import timedelta

from sqlalchemy import select

from assistant.db import now
from assistant.models import Message, Suppression
from assistant.native_models import NativeRecord
from conftest import login
from test_native import native_event
from test_messaging import receive
from test_messaging import make_draft


def test_retention_redacts_expired_source_and_blocks_replay(app, owner_client, chat):
    body = native_event(chat)
    response = receive(owner_client, body)
    assert response.status_code == 200, response.text
    source_id = response.json()["message_id"]
    memory = owner_client.post(f"/conversations/{chat['conversation']['id']}/memories", json={
        "text": "Private retained fact", "source_message_ids": [source_id], "status": "confirmed",
    })
    assert memory.status_code == 201, memory.text
    with app.state.session_factory() as db:
        db.get(Message, source_id).received_at = now() - timedelta(days=31)
        db.commit()
    result = owner_client.post("/privacy/retention/sweep", params={"workspace_id": chat["workspace"]["id"]})
    assert result.status_code == 200, result.text
    assert result.json()["raw_records_redacted"] == 1
    with app.state.session_factory() as db:
        assert db.get(Message, source_id).deleted and db.get(Message, source_id).text == ""
        assert db.scalar(select(NativeRecord).where(NativeRecord.message_id == source_id)).payload == {}
        assert source_id in db.scalar(select(Suppression)).source_message_ids
    assert receive(owner_client, body).json()["status"] == "duplicate"
    again = owner_client.post("/privacy/retention/sweep", params={"workspace_id": chat["workspace"]["id"]})
    assert again.json()["raw_records_redacted"] == 0
    exported = owner_client.post("/data-export", params={"workspace_id": chat["workspace"]["id"]})
    assert exported.status_code == 200, exported.text
    assert "Private retained fact" not in exported.text
    assert exported.json()["conversations"][0]["messages"] == []


def test_retention_owner_isolation_and_versioned_policy(owner_client, chat):
    policy = owner_client.get("/v1/privacy/retention", params={"workspace_id": chat["workspace"]["id"]})
    assert policy.status_code == 200 and policy.json()["raw_days"] == 30
    changed = owner_client.put("/v1/privacy/retention", json={"workspace_id": chat["workspace"]["id"],
                                                           "raw_days": 7, "derived_days": 14, "audit_days": 30})
    assert changed.status_code == 200 and changed.json()["version"] == 2
    login(owner_client, "intruder@example.test")
    assert owner_client.get("/privacy/retention", params={"workspace_id": chat["workspace"]["id"]}).status_code == 404
    assert owner_client.post("/privacy/retention/sweep", params={"workspace_id": chat["workspace"]["id"]}).status_code == 404


def test_expired_native_source_cannot_enter_reads_drafts_or_memory(owner_client, chat):
    body = native_event(chat, content={"type": "text", "text": "Expired private source"},
                        expires_at=(now() - timedelta(seconds=1)).isoformat())
    response = receive(owner_client, body)
    assert response.status_code == 200, response.text
    conv = chat["conversation"]["id"]
    assert owner_client.get(f"/conversations/{conv}/messages").json() == []
    assert owner_client.get("/inbox", params={"workspace_id": chat["workspace"]["id"]}).json()["conversations"][0]["latest_message"] is None
    memory = owner_client.post(f"/conversations/{conv}/memories", json={
        "text": "Expired source fact", "source_message_ids": [response.json()["message_id"]], "status": "confirmed",
    })
    assert memory.status_code == 422
    exported = owner_client.post("/v1/data-export", params={"workspace_id": chat["workspace"]["id"]})
    assert "Expired private source" not in exported.text


def test_versioned_google_nonce_cookie_binds_login(client, monkeypatch):
    from assistant import auth
    nonce = client.get("/v1/auth/nonce")
    assert nonce.status_code == 200
    assert "Path=/v1/auth" in nonce.headers["set-cookie"]
    challenge = nonce.json()["nonce"]
    monkeypatch.setattr(auth, "verified_google_claims", lambda credential, client_id, nonce: {
        "sub": "synthetic-versioned-google", "email": "versioned@example.test", "name": "Versioned",
    })
    result = client.post("/v1/auth/google", json={"credential": "synthetic-token", "nonce": challenge},
                         headers={"X-CSRF-Token": challenge})
    assert result.status_code == 200, result.text
    assert client.get("/v1/me").json()["email"] == "versioned@example.test"
    assert client.post("/v1/workspaces", json={"name": "Owner"}).status_code == 403


def test_capability_evidence_is_account_scoped(owner_client, chat):
    response = owner_client.get(f"/v1/connectors/{chat['connector']['id']}/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["simulation"] is True and body["adapter_version"] == "mock-actions-v1"
    statuses = {item["name"]: item for item in body["capabilities"]}
    assert statuses["native_forward"]["status"] == "supported"
    assert statuses["phone_continuity"]["status"] == "unsupported"
    assert statuses["contact_write_os"]["status"] == "unsupported"
    login(owner_client, "second-owner@example.test")
    assert owner_client.get(f"/connectors/{chat['connector']['id']}/capabilities").status_code == 404


def test_retention_redacts_owner_text_without_message_sources(app, owner_client, chat):
    from assistant.models import Draft
    draft_id = make_draft(app, chat, text="Private owner text without message evidence")[0]
    with app.state.session_factory() as db:
        db.get(Draft, draft_id).created_at = now() - timedelta(days=91)
        db.commit()
    result = owner_client.post("/privacy/retention/sweep", params={"workspace_id": chat["workspace"]["id"]})
    assert result.status_code == 200, result.text
    assert result.json()["derived_records_redacted"] == 1
    with app.state.session_factory() as db:
        draft = db.get(Draft, draft_id)
        assert draft.text == "" and draft.status == "cancelled"
    repeat = owner_client.post("/privacy/retention/sweep", params={"workspace_id": chat["workspace"]["id"]})
    assert repeat.json()["derived_records_redacted"] == 0


def test_retention_audit_cleanup_is_paged_and_isolated(app, owner_client, chat):
    from assistant.lifecycle import sweep_retention
    from assistant.models import AuditEvent
    workspace_id = chat["workspace"]["id"]
    with app.state.session_factory() as db:
        for ordinal in range(3):
            db.add(AuditEvent(workspace_id=workspace_id, actor_id="synthetic", action="synthetic.old",
                              resource_id=str(ordinal), details={}, created_at=now() - timedelta(days=91)))
        db.commit()
        first = sweep_retention(db, workspace_id, batch_size=2)
        db.commit()
        second = sweep_retention(db, workspace_id, batch_size=2)
        db.commit()
    assert first["audit_records_deleted"] == 2 and first["more_possible"] is True
    assert second["audit_records_deleted"] == 1 and second["more_possible"] is False
