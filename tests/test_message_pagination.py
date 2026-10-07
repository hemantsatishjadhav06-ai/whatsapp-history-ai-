"""Timestamp ties must not silently discard authorized history between pages."""

from datetime import timedelta

from fastapi.testclient import TestClient

from assistant.db import now, uid
from assistant.models import Conversation, Message, Permission
from conftest import create_chat, login


def test_message_cursor_covers_timestamp_ties_and_rejects_foreign_ids(app, owner_client, chat):
    stamp = now()
    own_id = chat["conversation"]["id"]
    with TestClient(app) as other_client:
        login(other_client, "other-owner@example.test")
        foreign = create_chat(other_client, account="synthetic-other-owner")
        other_chat_id = uid()
        with app.state.session_factory() as db:
            db.add(
                Conversation(
                    id=other_chat_id,
                    workspace_id=chat["workspace"]["id"],
                    connector_id=chat["connector"]["id"],
                    provider_chat_id="synthetic-other-chat",
                    title="Another authorized conversation",
                    kind="contact",
                )
            )
            db.flush()
            db.add(
                Permission(
                    workspace_id=chat["workspace"]["id"],
                    conversation_id=other_chat_id,
                    read=True,
                    retain=True,
                )
            )

            def message(key, scope=chat, conversation_id=own_id, *, deleted=False, older=False):
                return Message(
                    id=key,
                    workspace_id=scope["workspace"]["id"],
                    connector_id=scope["connector"]["id"],
                    conversation_id=conversation_id,
                    provider_message_id=key,
                    sender_id="synthetic-peer",
                    direction="inbound",
                    origin="history",
                    author_kind="contact_human",
                    text=f"Synthetic {key}",
                    provider_timestamp=stamp - timedelta(seconds=1) if older else stamp,
                    deleted=deleted,
                )

            db.add_all(message(f"own-message-{index:04d}") for index in range(120))
            db.add_all(message(f"old-message-{index:04d}", older=True) for index in range(5))
            db.add_all(message(f"deleted-message-{index:04d}", deleted=True) for index in range(15))
            db.add(message("other-chat-message", conversation_id=other_chat_id))
            db.add(message("foreign-owner-message", foreign, foreign["conversation"]["id"]))
            db.commit()

        params = {"limit": 50}
        seen = []
        page_sizes = []
        for _ in range(5):
            response = owner_client.get(f"/conversations/{own_id}/messages", params=params)
            assert response.status_code == 200, response.text
            page = response.json()
            page_sizes.append(len(page))
            if not page:
                break
            seen.extend(message["id"] for message in page)
            assert all(message["text"] == f"Synthetic {message['id']}" for message in page)
            params = {"limit": 50, "before": page[0]["provider_timestamp"], "before_id": page[0]["id"]}
        expected = {f"own-message-{index:04d}" for index in range(120)}
        expected.update(f"old-message-{index:04d}" for index in range(5))
        assert page_sizes == [50, 50, 25, 0]
        assert len(seen) == len(set(seen)) == 125
        assert set(seen) == expected

        # Timestamp-only cursors retain the documented strict older-time behavior.
        legacy = owner_client.get(f"/conversations/{own_id}/messages", params={"before": stamp.isoformat()})
        assert {message["id"] for message in legacy.json()} == {
            f"old-message-{index:04d}" for index in range(5)
        }
        failures = []
        for cursor in (
            "foreign-owner-message",
            "other-chat-message",
            "deleted-message-0000",
            "missing-message",
        ):
            response = owner_client.get(
                f"/conversations/{own_id}/messages", params={"before": stamp.isoformat(), "before_id": cursor}
            )
            assert response.status_code == 404
            failures.append(response.json())
        assert all(body == {"detail": "Message cursor is unavailable"} for body in failures)
        assert (
            owner_client.get(
                f"/conversations/{own_id}/messages", params={"before_id": "own-message-0000"}
            ).status_code
            == 422
        )
        assert (
            owner_client.get(
                f"/conversations/{own_id}/messages",
                params={
                    "before": (stamp - timedelta(seconds=1)).isoformat(),
                    "before_id": "own-message-0000",
                },
            ).status_code
            == 422
        )
        assert (
            other_client.get(
                f"/conversations/{own_id}/messages",
                params={"before": stamp.isoformat(), "before_id": "own-message-0000"},
            ).status_code
            == 404
        )
        foreign_page = other_client.get(f"/conversations/{foreign['conversation']['id']}/messages").json()
        assert [message["id"] for message in foreign_page] == ["foreign-owner-message"]

        owner_client.put(
            f"/conversations/{own_id}/permissions",
            json={"read": False, "retain": False, "learn": False, "draft": False, "send": False},
        )
        assert owner_client.get(f"/conversations/{own_id}/messages", params=params).status_code == 403
