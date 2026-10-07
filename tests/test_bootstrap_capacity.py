"""Privacy and query-shape regressions for the bounded UI contact preview."""
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import event, select

from assistant.db import now
from assistant.models import Connector, Conversation, Suppression
from assistant.native_models import MessageContext
from assistant.people import PrefetchedContactScope, source_valid, upsert_local_contact
from test_people import source


def count_bootstrap_queries(app, client):
    statements = []

    def record(connection, cursor, statement, parameters, context, many):
        statements.append(statement)

    event.listen(app.state.engine, "before_cursor_execute", record)
    try:
        response = client.get("/v1/ui/bootstrap")
        assert response.status_code == 200, response.text
        return len(statements), response.json()
    finally:
        event.remove(app.state.engine, "before_cursor_execute", record)


def test_live_contact_preview_query_count_does_not_grow_per_contact(app, owner_client, chat, db):
    conversation = db.get(Conversation, chat["conversation"]["id"])
    first = source(db, chat, sender="15550000001")
    upsert_local_contact(db, conversation, first)
    db.commit()
    one_count, first_snapshot = count_bootstrap_queries(app, owner_client)
    assert len(first_snapshot["contacts"]) == 1
    for number in range(2, 51):
        row = source(db, chat, sender=str(15550000000 + number))
        db.add(MessageContext(workspace_id=row.workspace_id, connector_id=row.connector_id,
                              conversation_id=row.conversation_id, message_id=row.id,
                              sender_identity={"id": row.sender_id}))
        upsert_local_contact(db, conversation, row)
    db.commit()
    many_count, snapshot = count_bootstrap_queries(app, owner_client)
    assert len(snapshot["contacts"]) == 50
    assert many_count == one_count
    # Keep future changes from accidentally reintroducing source-by-source SQL.
    assert many_count < 35


@pytest.mark.parametrize("invalid", ["workspace", "conversation", "connector", "context", "expiry", "sender", "forget"])
def test_prefetched_source_scope_preserves_exact_identity_checks(chat, db, invalid):
    row = source(db, chat)
    conversation = db.get(Conversation, chat["conversation"]["id"])
    connector = db.get(Connector, conversation.connector_id)
    context = MessageContext(workspace_id=row.workspace_id, connector_id=row.connector_id,
                             conversation_id=row.conversation_id, message_id=row.id,
                             sender_identity={"id": row.sender_id})
    db.add(context)
    db.commit()
    scope = PrefetchedContactScope(conversation.workspace_id, conversation.id, connector,
                                   {row.id: context}, frozenset())
    assert source_valid(db, conversation, row)
    assert source_valid(db, conversation, row, prefetched=scope)
    if invalid == "workspace":
        scope = replace(scope, workspace_id="foreign-workspace")
    elif invalid == "conversation":
        scope = replace(scope, conversation_id="foreign-conversation")
    elif invalid == "connector":
        scope = replace(scope, connector=Connector(id="foreign-connector", workspace_id=conversation.workspace_id))
    elif invalid == "context":
        context.conversation_id = "foreign-conversation"
    elif invalid == "expiry":
        context.expires_at = now() - timedelta(seconds=1)
    elif invalid == "sender":
        context.sender_identity = {"id": "15550009999"}
    else:
        scope = replace(scope, suppressed_source_ids=frozenset({row.id}))
    assert not source_valid(db, conversation, row, prefetched=scope)


def test_contact_preview_does_not_skip_an_older_forget_tombstone(app, owner_client, chat, db):
    conversation = db.get(Conversation, chat["conversation"]["id"])
    row = source(db, chat)
    contact, _ = upsert_local_contact(db, conversation, row)
    contact_id = contact.id
    db.add(Suppression(workspace_id=row.workspace_id, conversation_id=row.conversation_id,
                       content_hash="old-forget", source_message_ids=[row.id],
                       created_at=now() - timedelta(days=2)))
    for number in range(130):
        db.add(Suppression(workspace_id=row.workspace_id, conversation_id=row.conversation_id,
                           content_hash=f"unrelated-{number}", source_message_ids=[f"unrelated-{number}"]))
    db.commit()
    _, snapshot = count_bootstrap_queries(app, owner_client)
    assert contact_id not in {value["id"] for value in snapshot["contacts"]}
    assert db.scalar(select(Suppression).where(Suppression.content_hash == "old-forget")) is not None


def test_contact_preview_uses_identity_metadata_without_decrypting_message_body(app, owner_client, chat, db):
    from sqlalchemy import text
    conversation = db.get(Conversation, chat["conversation"]["id"])
    row = source(db, chat)
    contact, _ = upsert_local_contact(db, conversation, row)
    contact_id = contact.id
    db.commit()
    # An unavailable body must not prevent the separate verified identity preview.
    # Raw history still validates/decrypts its body on its own read path.
    db.execute(text("UPDATE messages SET text=:body WHERE id=:id"), {"body": "unavailable-body", "id": row.id})
    db.commit()
    _, snapshot = count_bootstrap_queries(app, owner_client)
    assert contact_id in {value["id"] for value in snapshot["contacts"]}
