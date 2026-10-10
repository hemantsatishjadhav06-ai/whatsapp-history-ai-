"""Full personal WhatsApp sync: every one-to-one chat, bulk history, live semantics and backfill anchors."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from assistant.db import aware, now
from assistant.lifecycle_models import RetentionPolicy
from assistant.models import AuditEvent, Connector, Conversation, Message, Outbox, Permission
from assistant.whatsapp_personal_models import PersonalChatAlias, PersonalChatSync, PersonalWhatsAppSession
import test_whatsapp_personal as personal_suite
from test_messaging import make_draft
from test_whatsapp_personal import CONTACT, INTERNAL, OWNER, OWNER_LID, PREFIX, post_event

# Reuse the linked-owner session fixtures: `personal` (a connected phone) and `personal_chat`
# (plus one explicitly authorized contact).
personal = personal_suite.personal
personal_chat = personal_suite.personal_chat

FRIEND = "919800000001@s.whatsapp.net"
FRIEND_LID = "123456789012345@lid"
OTHER = "919800000002@s.whatsapp.net"


def stamp(minutes_ago=0):
    return (now() - timedelta(minutes=minutes_ago)).isoformat()


def item(identifier, jid=FRIEND, from_me=False, text="Synthetic history text", minutes_ago=10, **changes):
    return {"id": identifier, "chat_jid": jid, "from_me": from_me, "timestamp": stamp(minutes_ago), "text": text,
            **changes}


def sync(client, ident, origin="history", messages=(), chats=(), **extra):
    return client.post("/internal/whatsapp-session-sync", headers=INTERNAL,
                       json={**ident, "origin": origin, "messages": list(messages), "chats": list(chats), **extra})


def count(app, model, *where):
    with app.state.session_factory() as db:
        return db.scalar(select(func.count()).select_from(model).where(*where))


def test_history_imports_every_contact_without_approval_or_automation(app, personal):
    client, ident = personal["client"], personal["identity"]
    result = sync(client, ident, chats=[{"jid": FRIEND, "title": "Rahul", "title_source": "contact", "unread_count": 2},
                                        {"jid": OTHER, "title": "Priya push", "title_source": "push"}],
                  messages=[item("h1", minutes_ago=30), item("h2", from_me=True, text="haan bhai", minutes_ago=29),
                            item("h3", jid=OTHER, minutes_ago=5), item("self", jid=OWNER, minutes_ago=4)],
                  progress={"phase": "recent", "percent": 40})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["accepted"] == 3 and body["chats_created"] == 2 and body["ignored"] == 1
    with app.state.session_factory() as db:
        chats = {row.provider_chat_id: row for row in db.scalars(select(Conversation).where(
            Conversation.connector_id == personal["connector"]["id"]))}
        assert set(chats) == {FRIEND, OTHER} and chats[FRIEND].title == "Rahul"
        grant = db.scalar(select(Permission).where(Permission.conversation_id == chats[FRIEND].id))
        assert (grant.read, grant.retain, grant.learn, grant.draft, grant.send) == (True, True, True, True, False)
        assert chats[FRIEND].recipient_opted_in is False
        sent = db.scalar(select(Message).where(Message.provider_message_id == "h2"))
        assert sent.author_kind == "human_owner" and sent.excluded_from_learning is False and sent.origin == "history"
        assert db.scalar(select(Message.text).where(Message.provider_message_id == "h1")) == "Synthetic history text"
        assert db.scalar(select(func.count()).select_from(Outbox).where(Outbox.kind == "message.accepted")) == 0
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action == "conversation.work_invalidated")) == 0
        sync_row = db.scalar(select(PersonalChatSync).where(PersonalChatSync.conversation_id == chats[FRIEND].id))
        assert sync_row.unread_count == 2 and sync_row.oldest_provider_message_id == "h1"
        session = db.scalar(select(PersonalWhatsAppSession).where(
            PersonalWhatsAppSession.connector_id == personal["connector"]["id"]))
        assert (session.sync_phase, session.sync_progress) == ("recent", 40)
    # A repeated chunk is idempotent.
    again = sync(client, ident, messages=[item("h1", minutes_ago=30), item("h3", jid=OTHER, minutes_ago=5)])
    assert again.json()["duplicates"] == 2 and count(app, Message) == 3


def test_phone_number_and_lid_addresses_share_one_chat(app, personal):
    client, ident = personal["client"], personal["identity"]
    assert sync(client, ident, chats=[{"jid": FRIEND, "alt_jid": FRIEND_LID, "title": "Rahul"}]).status_code == 200
    assert sync(client, ident, messages=[item("lid-only", jid=FRIEND_LID)]).json()["accepted"] == 1
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Conversation)) == 1
        conv = db.scalar(select(Conversation))
        assert conv.provider_chat_id == FRIEND
        assert db.scalar(select(Message.conversation_id).where(Message.provider_message_id == "lid-only")) == conv.id
        assert {row.alias_jid for row in db.scalars(select(PersonalChatAlias))} == {FRIEND, FRIEND_LID}



def test_chat_seen_first_by_private_id_shows_the_number_once_shared(app, personal):
    client, ident = personal["client"], personal["identity"]
    sync(client, ident, messages=[item("lid-first", jid=FRIEND_LID)])
    with app.state.session_factory() as db:
        assert db.scalar(select(Conversation.title)) == "WhatsApp contact"
    sync(client, ident, messages=[item("mapped", jid=FRIEND, chat_alt_jid=FRIEND_LID)])
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Conversation)) == 1
        assert db.scalar(select(Conversation.title)) == "+919800000001"
    sync(client, ident, messages=[item("named", jid=FRIEND_LID, push_name="Rahul")])
    sync(client, ident, messages=[item("again", jid=FRIEND, chat_alt_jid=FRIEND_LID)])
    with app.state.session_factory() as db:
        assert db.scalar(select(Conversation.title)) == "Rahul"


def test_live_messages_keep_takeover_outbox_and_unread_semantics(app, personal):
    client, ident = personal["client"], personal["identity"]
    inbound = sync(client, ident, origin="live", messages=[item("live-in", text="bro dinner?", minutes_ago=0)])
    assert inbound.status_code == 200 and inbound.json()["accepted"] == 1
    with app.state.session_factory() as db:
        conv = db.scalar(select(Conversation))
        assert db.scalar(select(PersonalChatSync.unread_count).where(PersonalChatSync.conversation_id == conv.id)) == 1
        outbox = db.scalar(select(Outbox).where(Outbox.kind == "message.accepted"))
        assert outbox is not None and outbox.payload["live_eligible"] is True
    outgoing = sync(client, ident, origin="live", messages=[item("live-out", from_me=True, text="haan", minutes_ago=0)])
    assert outgoing.json()["accepted"] == 1
    with app.state.session_factory() as db:
        conv = db.scalar(select(Conversation))
        assert conv.control_state == "HUMAN_TAKEOVER"
        assert db.scalar(select(Message.author_kind).where(Message.provider_message_id == "live-out")) == "human_owner"
        assert db.scalar(select(PersonalChatSync.unread_count).where(PersonalChatSync.conversation_id == conv.id)) == 0
    edited = sync(client, ident, origin="live", messages=[item("live-in", event="edited", text="bro lunch?",
                                                               revision=int(now().timestamp()), minutes_ago=0)])
    assert edited.json()["accepted"] == 1
    with app.state.session_factory() as db:
        assert db.scalar(select(Message.text).where(Message.provider_message_id == "live-in")) == "bro lunch?"


def test_selected_mode_and_owner_opt_outs_are_respected(app, personal_chat):
    client, ident = personal_chat["client"], personal_chat["identity"]
    workspace_id = personal_chat["workspace"]["id"]
    assert client.put(PREFIX + "/preferences", json={"workspace_id": workspace_id,
                                                     "import_mode": "selected"}).status_code == 200
    result = sync(client, ident, messages=[item("unknown", jid=OTHER), item("granted", jid=CONTACT)])
    assert result.json()["accepted"] == 1 and result.json()["ignored"] == 1
    assert count(app, Conversation, Conversation.provider_chat_id == OTHER) == 0
    # The owner switched one chat off; importing all never re-grants it.
    assert client.put(PREFIX + "/preferences", json={"workspace_id": workspace_id,
                                                     "import_mode": "all"}).status_code == 200
    with app.state.session_factory() as db:
        grant = db.scalar(select(Permission).where(Permission.conversation_id == personal_chat["conversation"]["id"]))
        grant.read = False
        db.commit()
    assert sync(client, ident, messages=[item("after-opt-out", jid=CONTACT)]).json()["accepted"] == 0
    assert count(app, Message, Message.provider_message_id == "after-opt-out") == 0


def test_stale_lease_paused_workspace_and_groups_are_refused(app, personal):
    client, ident = personal["client"], personal["identity"]
    assert sync(client, {**ident, "connector_fence": ident["connector_fence"] + 1}, messages=[item("x")]).status_code == 409
    group = sync(client, ident, messages=[item("g", jid="120363000000000000@g.us")])
    assert group.status_code == 422
    with app.state.session_factory() as db:
        from assistant.models import Workspace
        db.get(Workspace, personal["workspace"]["id"]).paused = True
        db.commit()
    assert sync(client, ident, messages=[item("paused")]).status_code == 423
    assert sync(client, ident, origin="live", messages=[item("paused-live", minutes_ago=0)]).json()["status"] == "ignored"
    with app.state.session_factory() as db:
        db.get(Workspace, personal["workspace"]["id"]).paused = False
        db.get(Connector, personal["connector"]["id"]).lease_expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert sync(client, ident, messages=[item("expired")]).status_code == 409
    assert count(app, Message) == 0


def test_large_history_batches_fit_the_private_sync_limit(app, personal):
    client, ident = personal["client"], personal["identity"]
    messages = [item(f"bulk-{index}", text="x" * 1500, minutes_ago=index + 1) for index in range(400)]
    result = sync(client, ident, messages=messages)
    assert result.status_code == 200, result.text
    assert result.json()["accepted"] == 400


def test_inbox_orders_chats_by_latest_message_with_previews(app, personal):
    client, ident = personal["client"], personal["identity"]
    sync(client, ident, chats=[{"jid": FRIEND, "title": "Older chat", "unread_count": 3},
                               {"jid": OTHER, "title": "Newer chat"}],
         messages=[item("old", jid=FRIEND, text="old news", minutes_ago=60),
                   item("new", jid=OTHER, from_me=True, text="latest   reply", minutes_ago=1)])
    snapshot = client.get("/ui/bootstrap", params={"workspace_id": personal["workspace"]["id"],
                                                   "conversation_limit": 1})
    assert snapshot.status_code == 200, snapshot.text
    first = snapshot.json()["conversations"]
    assert [row["title"] for row in first] == ["Newer chat"] and first[0]["preview"] == "You: latest reply"
    cursor = snapshot.json()["pagination"]["conversation_next_cursor"]
    second = client.get("/ui/bootstrap", params={"workspace_id": personal["workspace"]["id"], "conversation_limit": 1,
                                                 "conversation_cursor": cursor}).json()["conversations"]
    assert [row["title"] for row in second] == ["Older chat"]
    assert second[0]["unread"] == 3 and second[0]["preview"] == "old news"


def test_first_link_keeps_full_history_unless_owner_chose_retention(app, personal):
    with app.state.session_factory() as db:
        policy = db.scalar(select(RetentionPolicy).where(RetentionPolicy.workspace_id == personal["workspace"]["id"]))
        assert policy.raw_days == 3650 and policy.version == 2
    client = personal["client"]
    changed = client.put("/privacy/retention", json={"workspace_id": personal["workspace"]["id"], "raw_days": 45,
                                                     "derived_days": 90, "audit_days": 90})
    assert changed.status_code == 200
    from assistant.whatsapp_sync import keep_full_history
    with app.state.session_factory() as db:
        keep_full_history(db, db.get(Connector, personal["connector"]["id"]))
        db.commit()
        assert db.scalar(select(RetentionPolicy.raw_days).where(
            RetentionPolicy.workspace_id == personal["workspace"]["id"])) == 45



def test_phone_linked_before_full_sync_keeps_full_history_on_its_next_connection(app, personal):
    client, ident = personal["client"], personal["identity"]
    with app.state.session_factory() as db:
        # A link made before full sync existed still carries the untouched 30-day default.
        policy = db.scalar(select(RetentionPolicy).where(RetentionPolicy.workspace_id == personal["workspace"]["id"]))
        policy.raw_days, policy.version = 30, 1
        db.commit()
    assert post_event(client, ident, "connection", state="connected", account_id=OWNER,
                      account_aliases=[OWNER, OWNER_LID]).status_code == 200
    with app.state.session_factory() as db:
        policy = db.scalar(select(RetentionPolicy).where(RetentionPolicy.workspace_id == personal["workspace"]["id"]))
        assert (policy.raw_days, policy.version) == (3650, 2)


def test_backfill_targets_newest_chats_first_and_stops_when_exhausted(app, personal):
    client, ident = personal["client"], personal["identity"]
    sync(client, ident, messages=[item("friend-oldest", minutes_ago=90), item("friend-newer", minutes_ago=2),
                                  item("other-only", jid=OTHER, minutes_ago=50)])
    result = client.post("/internal/whatsapp-session-backfill", headers=INTERNAL, json={**ident, "limit": 5})
    assert result.status_code == 200, result.text
    targets = result.json()["targets"]
    assert [row["jid"] for row in targets] == [FRIEND, OTHER]
    assert targets[0]["oldest_id"] == "friend-oldest" and targets[0]["oldest_from_me"] is False
    # Older pages move the anchor backwards; an empty page completes the chat.
    sync(client, ident, origin="backfill", messages=[item("friend-ancient", minutes_ago=600)])
    after = client.post("/internal/whatsapp-session-backfill", headers=INTERNAL,
                        json={**ident, "limit": 5, "reports": [{"jid": OTHER, "outcome": "exhausted"}]}).json()
    assert [row["jid"] for row in after["targets"]] == [FRIEND]
    assert after["targets"][0]["oldest_id"] == "friend-ancient"
    status = client.get(PREFIX + "/sync", params={"workspace_id": personal["workspace"]["id"]}).json()
    assert status["chats"] == 2 and status["messages"] == 4 and status["import_mode"] == "all"
    assert status["backfill_pending"] == 1 and status["backfill_complete"] == 1


def test_lease_gap_reviews_only_chats_with_pending_work(app, personal_chat):
    client, ident = personal_chat["client"], personal_chat["identity"]
    sync(client, ident, messages=[item("idle", jid=OTHER)])
    make_draft(app, personal_chat)
    with app.state.session_factory() as db:
        db.get(Connector, personal_chat["connector"]["id"]).lease_expires_at = now() - timedelta(seconds=1)
        db.commit()
    denied = client.post("/internal/whatsapp-session-authority", headers=INTERNAL,
                         json={**ident, "operation": "status"}).json()
    assert denied["allowed"] is False and denied["reason_code"] == "lease_expired"
    assert post_event(client, ident, "connection", state="connected", account_id=OWNER).status_code == 200
    with app.state.session_factory() as db:
        states = {row.provider_chat_id: row.control_state for row in db.scalars(select(Conversation))}
        assert states[CONTACT] == "RECONNECT_REVIEW" and states[OTHER] == "DRAFT_MODE"
    revoked = client.post("/internal/whatsapp-session-authority", headers=INTERNAL,
                          json={**ident, "connector_fence": ident["connector_fence"] + 1, "operation": "status"}).json()
    assert revoked["reason_code"] == "revoked"


@pytest.mark.parametrize("owner_jid", [OWNER, OWNER_LID])
def test_owner_self_chat_is_never_imported(app, personal, owner_jid):
    assert sync(personal["client"], personal["identity"], messages=[item("note", jid=owner_jid)]).json()["ignored"] == 1
    assert count(app, Conversation) == 0


def test_address_book_contacts_name_chats_but_never_create_them(app, personal):
    client, ident = personal["client"], personal["identity"]
    sync(client, ident, chats=[{"jid": OTHER, "title": "Saved Name", "title_source": "contact", "contact_only": True}])
    assert count(app, Conversation) == 0
    sync(client, ident, messages=[item("first", jid=OTHER, push_name="Push Name")])
    sync(client, ident, chats=[{"jid": OTHER, "title": "Saved Name", "title_source": "contact", "contact_only": True}])
    sync(client, ident, messages=[item("second", jid=OTHER, push_name="Another Push")])
    with app.state.session_factory() as db:
        assert db.scalar(select(Conversation.title)) == "Saved Name"


def test_chats_without_stored_messages_sort_by_whatsapp_activity(app, personal):
    client, ident = personal["client"], personal["identity"]
    quiet = (now() - timedelta(days=3)).isoformat()
    sync(client, ident, chats=[{"jid": OTHER, "title": "Cleared chat", "last_activity_at": quiet}],
         messages=[item("recent", jid=FRIEND, minutes_ago=5)])
    # A live chat event for an unknown chat creates nothing.
    sync(client, ident, origin="live", chats=[{"jid": "919800000003@s.whatsapp.net", "unread_count": 0, "contact_only": True}])
    titles = [row["title"] for row in client.get("/ui/bootstrap", params={
        "workspace_id": personal["workspace"]["id"]}).json()["conversations"]]
    assert titles == ["+919800000001", "Cleared chat"]
    sync(client, ident, messages=[item("older", jid=OTHER, minutes_ago=10 * 24 * 60)])
    with app.state.session_factory() as db:
        stored = db.scalar(select(Conversation.last_message_at).where(Conversation.provider_chat_id == OTHER))
    assert abs((aware(stored) - (now() - timedelta(days=3))).total_seconds()) < 60
