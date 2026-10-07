from datetime import timedelta

import pytest
from sqlalchemy import func, select, text

from assistant.db import now, uid
from assistant.models import Conversation, Message, Permission, Suppression
from assistant.native_models import MessageContext
from assistant.people import auto_save_contact, forget_people_sources, purge_people_data
from assistant.people_models import ContactSaveGrant, ContactSource, LocalContact
from conftest import create_chat, login
from test_messaging import event, receive


def source(db, chat, *, sender="15550001234", origin="live", text_value="Hello", **changes):
    row = Message(workspace_id=chat["workspace"]["id"], connector_id=chat["connector"]["id"],
                  conversation_id=chat["conversation"]["id"], provider_message_id=uid(), sender_id=sender,
                  direction="inbound", origin=origin, author_kind="contact_human", text=text_value,
                  provider_timestamp=now(), **changes)
    db.add(row)
    db.commit()
    return row


def save(client, chat, record, **changes):
    body = {"conversation_id": chat["conversation"]["id"], "source_message_id": record.id}
    body.update(changes)
    return client.post("/contacts", json=body)


def test_exact_identity_replay_and_encrypted_name(owner_client, chat, db):
    first = source(db, chat)
    result = save(owner_client, chat, first, display_name="Private display name")
    assert result.status_code == 201, result.text
    data = result.json()
    assert data["provider_identity"] == "15550001234"
    assert data["saved_destination"] == "assistant_local" and data["external_write"] is False
    again = save(owner_client, chat, first)
    assert again.json()["id"] == data["id"]
    assert db.scalar(select(func.count(LocalContact.id))) == 1
    assert db.scalar(select(func.count(ContactSource.id))) == 1
    ciphertext = db.execute(text("SELECT display_name FROM local_contacts")).scalar_one()
    assert "Private display name" not in ciphertext


def test_same_name_never_merges_identities(owner_client, chat, db):
    a = save(owner_client, chat, source(db, chat, sender="15550000001"), display_name="Alex").json()
    b = save(owner_client, chat, source(db, chat, sender="15550000002"), display_name="Alex").json()
    assert a["id"] != b["id"]
    assert len(owner_client.get("/people", params={"workspace_id": chat["workspace"]["id"]}).json()) == 2


def test_identity_connector_namespace_not_name_or_phone_merge(owner_client, chat, db):
    a = save(owner_client, chat, source(db, chat)).json()
    other = create_chat(owner_client, account="second-account", name="Same name")
    b = save(owner_client, other, source(db, other)).json()
    assert a["id"] != b["id"] and a["connector_id"] != b["connector_id"]


@pytest.mark.parametrize("destination", ["whatsapp", "google_contacts", "phone_os"])
def test_destination_never_claims_unavailable_external_write(owner_client, chat, db, destination):
    result = save(owner_client, chat, source(db, chat), destination=destination)
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "CAPABILITY_UNAVAILABLE"
    assert db.scalar(select(func.count(LocalContact.id))) == 0


@pytest.mark.parametrize("fields", [{"sender": "Alex"}, {"origin": "history"}, {"origin": "replay"},
                                     {"deleted": True},
                                     {"sender": "Owner"}])
def test_unverified_or_unavailable_sources_cannot_save_contact(owner_client, chat, db, fields):
    result = save(owner_client, chat, source(db, chat, **fields))
    assert result.status_code == 409


def test_contact_save_does_not_require_owner_style_learning_eligibility(owner_client, chat, db):
    assert save(owner_client, chat, source(db, chat, excluded_from_learning=True)).status_code == 201


def test_expired_native_context_cannot_save_or_expose_local_contact(owner_client, chat, db):
    row = source(db, chat)
    assert save(owner_client, chat, row).status_code == 201
    db.add(MessageContext(workspace_id=row.workspace_id, connector_id=row.connector_id,
                          conversation_id=row.conversation_id, message_id=row.id,
                          expires_at=now() - timedelta(seconds=1)))
    db.commit()
    assert save(owner_client, chat, row).status_code == 409
    assert owner_client.get("/contacts", params={"workspace_id": chat["workspace"]["id"]}).json() == []


def test_verified_live_ingestion_automatically_saves_under_local_grant(owner_client, chat, db):
    assert owner_client.put(f"/conversations/{chat['conversation']['id']}/contact-save-grant", json={
        "enabled": True, "expires_at": (now() + timedelta(days=1)).isoformat()}).status_code == 200
    result = receive(owner_client, event(chat, sender_id="15550001234"))
    assert result.status_code == 200, result.text
    data = owner_client.get("/contacts", params={"workspace_id": chat["workspace"]["id"]}).json()
    assert len(data) == 1 and data[0]["display_name"] == "15550001234"


def test_source_chat_and_scope_are_exact(owner_client, chat, db):
    other = create_chat(owner_client, account="second")
    row = source(db, other)
    assert save(owner_client, chat, row).status_code == 409
    own = source(db, chat)
    permission = db.scalar(select(Permission).where(Permission.conversation_id == chat["conversation"]["id"]))
    permission.retain = False
    db.commit()
    assert save(owner_client, chat, own).status_code == 403


def test_contacts_hide_after_scope_revoke_source_edit_delete_or_forget(owner_client, chat, db):
    row = source(db, chat)
    assert save(owner_client, chat, row).status_code == 201
    endpoint = "/contacts"
    params = {"workspace_id": chat["workspace"]["id"]}
    assert len(owner_client.get(endpoint, params=params).json()) == 1
    row.revision += 1
    db.commit()
    assert owner_client.get(endpoint, params=params).json() == []
    assert save(owner_client, chat, row).status_code == 201
    db.add(Suppression(workspace_id=chat["workspace"]["id"], conversation_id=chat["conversation"]["id"],
                       content_hash="suppressed", source_message_ids=[row.id]))
    db.commit()
    assert owner_client.get(endpoint, params=params).json() == []


def test_contact_tenant_isolation(owner_client, chat, db):
    assert save(owner_client, chat, source(db, chat)).status_code == 201
    login(owner_client, "someone-else@example.test")
    assert owner_client.get("/contacts", params={"workspace_id": chat["workspace"]["id"]}).status_code == 404
    assert save(owner_client, chat, source(db, chat)).status_code == 404


def test_local_grant_only_saves_new_verified_sources_and_ignores_external_pause(owner_client, chat, db):
    old = source(db, chat)
    grant = owner_client.put(f"/conversations/{chat['conversation']['id']}/contact-save-grant",
                            json={"enabled": True, "expires_at": (now() + timedelta(days=1)).isoformat()})
    assert grant.status_code == 200, grant.text
    db.expire_all()
    conversation = db.get(Conversation, chat["conversation"]["id"])
    assert auto_save_contact(db, conversation, old) is None
    assert owner_client.post("/pause-all", params={"workspace_id": chat["workspace"]["id"]}).status_code == 200
    fresh = source(db, chat)
    db.expire_all()
    contact = auto_save_contact(db, db.get(Conversation, conversation.id), fresh)
    assert contact is not None
    db.commit()
    assert owner_client.post(f"/conversations/{conversation.id}/contacts/sync").json()["saved_count"] == 1


def test_local_auto_save_requires_current_grant_and_scope(owner_client, chat, db):
    row = source(db, chat)
    conversation = db.get(Conversation, chat["conversation"]["id"])
    assert auto_save_contact(db, conversation, row) is None
    db.add(ContactSaveGrant(workspace_id=conversation.workspace_id, conversation_id=conversation.id,
                            enabled=True, expires_at=now() - timedelta(seconds=1),
                            granted_at=now() - timedelta(days=1)))
    db.commit()
    assert auto_save_contact(db, conversation, row) is None


def test_deleting_local_contact_disables_contributing_auto_save_grants(owner_client, chat, db):
    assert owner_client.put(f"/conversations/{chat['conversation']['id']}/contact-save-grant", json={
        "enabled": True, "expires_at": (now() + timedelta(days=1)).isoformat()}).status_code == 200
    row = source(db, chat)
    data = save(owner_client, chat, row).json()
    assert owner_client.delete(f"/contacts/{data['id']}").status_code == 204
    db.expire_all()
    assert not db.scalar(select(ContactSaveGrant)).enabled
    assert owner_client.post(f"/conversations/{chat['conversation']['id']}/contacts/sync").status_code == 403


def test_source_forget_and_conversation_purge_remove_orphan_contacts(owner_client, chat, db):
    row = source(db, chat)
    assert save(owner_client, chat, row).status_code == 201
    forget_people_sources(db, chat["conversation"]["id"], [row.id])
    db.commit()
    assert db.scalar(select(func.count(LocalContact.id))) == 0
    assert save(owner_client, chat, row).status_code == 201
    purge_people_data(db, chat["conversation"]["id"])
    db.commit()
    assert db.scalar(select(func.count(LocalContact.id))) == 0
