"""Owner questions read both human sides without widening draft or chat scope."""

from datetime import timedelta

import pytest

from assistant import intelligence
from assistant.db import now
from assistant.models import Suppression
from assistant.native_models import MessageContext
from conftest import create_chat
from test_connected_intelligence import ask, model_capture
from test_intelligence import seed_message


@pytest.mark.parametrize("kind", ["contact", "group"])
def test_owner_answer_reads_attributed_human_history_and_excludes_ambiguous_or_generated_text(
        owner_client, db, monkeypatch, kind):
    chat = create_chat(owner_client, kind=kind, learn=False, draft=False, send=False)
    contact = seed_message(db, chat, "Can we meet Tuesday?")
    owner = seed_message(db, chat, "I agreed to Tuesday", direction="outbound", author_kind="human_owner")
    operator = seed_message(db, chat, "I suggested Wednesday", direction="outbound",
                            author_kind="other_authorized_operator")
    ambiguous = seed_message(db, chat, "Unknown operator private suggestion", direction="outbound",
                             author_kind="unknown_owner_outgoing", excluded=True)
    assistant = seed_message(db, chat, "Generated unverified promise", direction="outbound",
                             author_kind="assistant", excluded=True)
    replay = seed_message(db, chat, "Unverified replay owner promise", direction="outbound", origin="replay",
                          author_kind="human_owner")
    other = create_chat(owner_client, account="unrelated-owner-answer-account")
    foreign = seed_message(db, other, "Other chat private agreement", direction="outbound", author_kind="human_owner")
    captured = model_capture(monkeypatch, [contact.id, owner.id])
    result = ask(owner_client, chat, question="What did we agree and what did the other operator suggest?")
    assert result.status_code == 200, result.text
    entries = {row["id"]: row for row in captured["messages"]}
    assert set(entries) == {contact.id, owner.id, operator.id}
    assert entries[owner.id]["direction"] == "outbound"
    assert entries[owner.id]["author_kind"] == "human_owner"
    assert entries[contact.id]["direction"] == "inbound"
    assert entries[operator.id]["author_kind"] == "other_authorized_operator"
    assert not {ambiguous.id, assistant.id, replay.id, foreign.id} & set(entries)
    assert result.json()["result"]["audience"] == "owner_only"
    assert captured["style"]["features"] == {}


def test_owner_answer_excludes_forgotten_and_expired_outbound_evidence(owner_client, db, monkeypatch):
    chat = create_chat(owner_client)
    visible = seed_message(db, chat, "We agreed to Tuesday", direction="outbound", author_kind="human_owner")
    forgotten = seed_message(db, chat, "Private forgotten owner message", direction="outbound", author_kind="human_owner")
    expired = seed_message(db, chat, "Private expired owner message", direction="outbound", author_kind="human_owner")
    db.add(Suppression(workspace_id=chat["workspace"]["id"], conversation_id=chat["conversation"]["id"],
                       content_hash="outbound-forgotten", source_message_ids=[forgotten.id]))
    db.add(MessageContext(workspace_id=expired.workspace_id, connector_id=expired.connector_id,
                          conversation_id=expired.conversation_id, message_id=expired.id,
                          expires_at=now() - timedelta(seconds=1)))
    db.commit()
    captured = model_capture(monkeypatch, [visible.id])
    result = ask(owner_client, chat, question="What did I agree to?")
    assert result.status_code == 200, result.text
    assert [row["id"] for row in captured["messages"]] == [visible.id]


def test_draft_reply_policy_keeps_owner_raw_history_out_of_contact_reply_context(owner_client, db, monkeypatch):
    chat = create_chat(owner_client)
    incoming = seed_message(db, chat, "When can we meet?")
    owner = seed_message(db, chat, "Owner-only private wording 91827", direction="outbound", author_kind="human_owner")
    captured = model_capture(monkeypatch, [incoming.id])
    result = owner_client.post(f"/conversations/{chat['conversation']['id']}/drafts", json={})
    assert result.status_code == 201, result.text
    assert [row["id"] for row in captured["messages"]] == [incoming.id]
    assert owner.id not in str(captured) and "91827" not in str(captured)
    assert "direction" not in captured["messages"][0]


def test_owner_answer_prompt_does_not_treat_other_operator_statement_as_owner_agreement(app):
    payload = intelligence.model_request_payload(app.state.settings, {"purpose": "owner_answer"})
    prompt = payload["messages"][0]["content"]
    assert "other_authorized_operator" in prompt
    assert "Do not attribute another operator's wording to the owner" in prompt
    assert "not proof that promised work or an action occurred" in prompt
