from datetime import timedelta

from sqlalchemy import func, select, text

from assistant.db import now
from assistant.models import Connector, Draft, Message, Outbox, Suppression
from conftest import create_chat, login


def import_body(chat, **changes):
    return {"conversation_id": chat["conversation"]["id"], "owner_sender_label": "Owner",
            "date_order": "DMY", "timezone": "Asia/Kolkata",
            "text": "06/10/2026, 09:30 - Client: What are your hours?\n"
                    "06/10/2026, 09:31 - Owner: Hello! We open at 9am.\n"
                    "06/10/2026, 09:32 - Owner: Hello! We open at 9am.\n", **changes}


def test_initial_permissions_excluded(owner_client):
    chat = create_chat(owner_client, read=False, retain=False, learn=False, draft=False, send=False)
    c = chat["conversation"]["id"]
    assert owner_client.get(f"/conversations/{c}/messages").status_code == 403
    assert owner_client.post("/imports", json=import_body(chat)).status_code == 403


def test_import_preview_provenance_and_idempotency(owner_client, chat, db):
    body = import_body(chat)
    preview = owner_client.post("/imports/preview", json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["record_count"] == 3
    assert preview.json()["history_completeness"] == "unknown"
    assert db.scalar(select(func.count()).select_from(Message)) == 0
    first = owner_client.post("/imports", json=body)
    assert first.status_code == 201, first.text
    assert first.json()["message_count"] == 3
    again = owner_client.post("/imports", json=body)
    assert again.json()["id"] == first.json()["id"]
    assert again.json()["replayed"] is True
    rows = list(db.scalars(select(Message)))
    assert len(rows) == 3
    assert sum(m.author_kind == "human_owner" for m in rows) == 2
    assert all(m.origin == "history" for m in rows)
    assert db.scalar(select(func.count()).select_from(Draft)) == 0
    assert db.scalar(select(func.count()).select_from(Outbox)) == 1


def test_import_mapping_change_requires_deletion(owner_client, chat):
    body = import_body(chat)
    assert owner_client.post("/imports", json=body).status_code == 201
    body["owner_sender_label"] = "Client"
    assert owner_client.post("/imports", json=body).status_code == 409


def test_export_at_rest_encrypted(owner_client, chat, db):
    assert owner_client.post("/imports", json=import_body(chat)).status_code == 201
    raw = db.execute(text("SELECT text FROM messages")).scalars().all()
    assert raw and all("hours" not in body and "Hello" not in body for body in raw)
    assert all(body.startswith("gAAAA") for body in raw)


def test_owner_sender_must_be_present(owner_client, chat):
    assert owner_client.post("/imports", json=import_body(chat, owner_sender_label="Nobody")).status_code == 422


def test_import_size_limit(owner_client, chat, app):
    app.state.settings.max_import_bytes = 30
    assert owner_client.post("/imports", json=import_body(chat)).status_code == 413


def test_delete_sources_and_prevent_reimport(owner_client, chat, db):
    body = import_body(chat)
    assert owner_client.post("/imports", json=body).status_code == 201
    c = chat["conversation"]["id"]
    assert owner_client.delete(f"/conversations/{c}/data").status_code == 200
    db.expire_all()
    rows = list(db.scalars(select(Message)))
    assert all(row.deleted and row.text == "" for row in rows)
    assert db.scalar(select(func.count()).select_from(Suppression)) == 1
    assert owner_client.get(f"/conversations/{c}/messages").status_code == 403
    assert owner_client.put(f"/conversations/{c}/permissions", json={"read": True,"retain": True}).status_code == 200
    assert owner_client.post("/imports", json=body).status_code == 409


def test_import_requires_retain_independent_of_read(owner_client):
    chat = create_chat(owner_client, retain=False)
    assert owner_client.post("/imports/preview", json=import_body(chat)).status_code == 200
    assert owner_client.post("/imports", json=import_body(chat)).status_code == 403


def test_cross_tenant_history_import_export_and_activity(owner_client, chat):
    login(owner_client, "intruder@example.test")
    c, w = chat["conversation"]["id"], chat["workspace"]["id"]
    for endpoint in [f"/conversations/{c}/messages", f"/activity?workspace_id={w}", f"/conversations?workspace_id={w}"]:
        assert owner_client.get(endpoint).status_code == 404
    assert owner_client.post("/imports", json=import_body(chat)).status_code == 404
    assert owner_client.post(f"/data-export?workspace_id={w}").status_code == 404
    assert owner_client.delete(f"/account-data?workspace_id={w}").status_code == 404


def test_account_cannot_be_silently_transferred(owner_client, chat):
    login(owner_client, "other@example.test")
    w = owner_client.post("/workspaces", json={"name": "Other"}).json()
    response = owner_client.post("/connectors", json={"workspace_id": w["id"], "provider": "mock",
                                                       "account_id": "synthetic-owner", "owner_sender_id": "Owner"})
    assert response.status_code == 409


def test_pause_takeover_epochs_and_explicit_resume(owner_client, chat):
    c, w = chat["conversation"]["id"], chat["workspace"]["id"]
    takeover = owner_client.post(f"/conversations/{c}/takeover").json()
    assert takeover["control_state"] == "HUMAN_TAKEOVER"
    pause = owner_client.post(f"/pause-all?workspace_id={w}").json()
    assert pause["paused"] is True
    owner_client.post(f"/resume-all?workspace_id={w}")
    conv = owner_client.get(f"/conversations?workspace_id={w}").json()[0]
    assert conv["control_state"] == "HUMAN_TAKEOVER"
    resumed = owner_client.post(f"/conversations/{c}/resume").json()
    assert resumed["control_state"] == "DRAFT_MODE"
    assert resumed["control_epoch"] > takeover["control_epoch"]


def test_disconnect_fence_blocks_resume(owner_client, chat, db):
    conn, c = chat["connector"]["id"], chat["conversation"]["id"]
    response = owner_client.delete(f"/connectors/{conn}")
    assert response.status_code == 200
    assert response.json()["fence"] == 2
    assert owner_client.post(f"/conversations/{c}/resume").status_code == 409
    db.expire_all()
    assert db.get(Connector, conn).status == "disconnected"


def test_permissions_expiry_validation(owner_client, chat):
    c = chat["conversation"]["id"]
    response = owner_client.put(f"/conversations/{c}/permissions", json={"read": True,
                     "expires_at": (now() - timedelta(seconds=1)).isoformat()})
    assert response.status_code == 422


def test_export_never_includes_revoked_chat(owner_client, chat):
    assert owner_client.post("/imports", json=import_body(chat)).status_code == 201
    w,c = chat["workspace"]["id"],chat["conversation"]["id"]
    assert owner_client.post(f"/data-export?workspace_id={w}").json()["conversations"][0]["messages"]
    assert owner_client.put(f"/conversations/{c}/permissions", json={}).status_code == 200
    assert owner_client.post(f"/data-export?workspace_id={w}").json()["conversations"] == []


def test_health_schema_live_and_ready(client):
    assert client.get("/health/live").json()["status"] == "ok"
    assert client.get("/health/ready").json()["status"] == "ok"


def test_mixed_script_style_keeps_owner_attribution_and_encrypts_rules(owner_client, chat, db):
    body = import_body(chat, text="06/10/2026, 09:30 - Client: Private incoming text\n"
                       "06/10/2026, 09:31 - Owner: Hello नमस्ते\n"
                       "06/10/2026, 09:32 - Owner: నమస్కారం\n")
    assert owner_client.post("/imports", json=body).status_code == 201
    cid = chat["conversation"]["id"]
    profile = owner_client.post(f"/conversations/{cid}/style-preview").json()
    assert profile["sample_count"] == 2
    assert profile["features"]["script_usage"]["Devanagari"] == 1
    assert profile["features"]["script_usage"]["Telugu"] == 1
    assert profile["features"]["mixed_script_rate"] == 0.5
    assert "Private incoming" not in str(profile["features"])
    updated = owner_client.patch(f"/conversations/{cid}/style-profile",
                                 json={"owner_rules": ["Private preference 7812"], "reviewed": True})
    assert updated.status_code == 200
    assert updated.json()["owner_rules"] == ["Private preference 7812"]
    stored = db.execute(text("SELECT owner_rules FROM style_profiles WHERE conversation_id=:cid"),
                        {"cid": cid}).scalar()
    assert "Private preference" not in stored and stored.startswith("gAAAA")


def test_draft_inbox_recovery_and_revocation_filter(owner_client, chat):
    c, w = chat["conversation"]["id"], chat["workspace"]["id"]
    result = owner_client.post(f"/conversations/{c}/drafts", json={})
    assert result.status_code == 201
    draft_id = result.json()["id"]
    assert owner_client.get(f"/drafts/{draft_id}").json()["id"] == draft_id
    assert owner_client.get("/drafts", params={"workspace_id": w}).json()[0]["id"] == draft_id
    assert owner_client.get(f"/conversations/{c}/permissions").json()["read"] is True
    assert len(owner_client.get("/connectors", params={"workspace_id": w}).json()) == 1
    owner_client.put(f"/conversations/{c}/permissions", json={})
    assert owner_client.get("/drafts", params={"workspace_id": w}).json() == []
    assert owner_client.get(f"/drafts/{draft_id}").status_code == 403
    assert owner_client.get(f"/conversations/{c}/permissions").json()["read"] is False


def test_draft_and_connector_inbox_cannot_cross_tenants(owner_client, chat):
    c, w = chat["conversation"]["id"], chat["workspace"]["id"]
    draft_id = owner_client.post(f"/conversations/{c}/drafts", json={}).json()["id"]
    login(owner_client, "another-reader@example.test")
    assert owner_client.get(f"/drafts/{draft_id}").status_code == 404
    assert owner_client.get("/drafts", params={"workspace_id": w}).status_code == 404
    assert owner_client.get("/connectors", params={"workspace_id": w}).status_code == 404
    assert owner_client.get(f"/conversations/{c}/permissions").status_code == 404
