import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from assistant.config import Settings
from assistant.db import Base, make_database, now
from assistant.models import (
    Connector, Conversation, Draft, Outbox, ScheduledIntent, User, Workspace,
)
from assistant.relay import event_envelope, relay_pending
from assistant.workflows import register_pending


@pytest.fixture
def worker_database():
    settings = Settings(
        environment="test", database_url="sqlite:///:memory:",
        encryption_key=Fernet.generate_key().decode(),
    ).prepare()
    engine, factory = make_database(settings)
    Base.metadata.create_all(engine)
    with factory() as session:
        user = User(subject="worker-test", email="owner@example.invalid")
        session.add(user)
        session.flush()
        workspace = Workspace(owner_id=user.id, name="Worker test")
        session.add(workspace)
        session.flush()
        connector = Connector(
            workspace_id=workspace.id, provider="mock", account_id="worker-account", owner_sender_id="owner",
        )
        session.add(connector)
        session.flush()
        conversation = Conversation(
            workspace_id=workspace.id, connector_id=connector.id, provider_chat_id="chat", title="Worker chat",
        )
        session.add(conversation)
        session.flush()
        draft = Draft(
            workspace_id=workspace.id, conversation_id=conversation.id, recipient_id="recipient",
            text="Synthetic test content", model_version="test", conversation_revision=0, control_epoch=0,
            permission_version=1, pause_generation=0, connector_fence=1, content_hash="synthetic",
        )
        session.add(draft)
        session.flush()
        intent = ScheduledIntent(
            workspace_id=workspace.id, draft_id=draft.id, conversation_id=conversation.id,
            idempotency_key="synthetic-intent", due_at=now() + timedelta(minutes=5),
            expires_at=now() + timedelta(minutes=10), timezone="UTC",
        )
        session.add(intent)
        session.flush()
        registration = Outbox(
            workspace_id=workspace.id, kind="schedule.register", aggregate_id=intent.id,
            payload={"intent_id": intent.id, "due_at": intent.due_at.isoformat()},
        )
        event = Outbox(
            workspace_id=workspace.id, kind="message.accepted", aggregate_id="synthetic-message",
            payload={"message_id": "synthetic-message", "conversation_id": conversation.id,
                     "event_id": "synthetic-event", "live_eligible": True},
        )
        session.add_all([registration, event])
        session.commit()
        refs = SimpleNamespace(intent=intent.id, registration=registration.id, event=event.id)
    yield settings, factory, refs
    engine.dispose()


def test_registration_is_idempotent_and_stores_no_message(worker_database):
    settings, factory, refs = worker_database

    class Client:
        calls = []

        async def start_workflow(self, workflow_run, intent_id, **kwargs):
            self.calls.append((intent_id, kwargs))

    client = Client()
    assert asyncio.run(register_pending(client, factory, settings)) == 1
    assert asyncio.run(register_pending(client, factory, settings)) == 0
    assert client.calls == [(refs.intent, {
        "id": f"schedule-{refs.intent}", "task_queue": settings.workflow_task_queue,
        "id_reuse_policy": WorkflowIDReusePolicy.REJECT_DUPLICATE,
    })]
    with factory() as session:
        assert session.get(ScheduledIntent, refs.intent).workflow_registered
        assert session.get(Outbox, refs.registration).status == "registered"
        assert session.get(Outbox, refs.event).status == "pending"


def test_registration_recovers_after_start_before_sql_ack(worker_database):
    settings, factory, refs = worker_database

    class ExistingClient:
        async def start_workflow(self, workflow_run, intent_id, **kwargs):
            raise WorkflowAlreadyStartedError(kwargs["id"], "ScheduleWorkflow")

    assert asyncio.run(register_pending(ExistingClient(), factory, settings)) == 1
    with factory() as session:
        assert session.get(Outbox, refs.registration).status == "registered"


def test_registration_failure_retains_pending_intent(worker_database):
    settings, factory, refs = worker_database

    class FailedClient:
        async def start_workflow(self, *args, **kwargs):
            raise ConnectionError("synthetic unavailable server")

    with pytest.raises(ConnectionError):
        asyncio.run(register_pending(FailedClient(), factory, settings))
    with factory() as session:
        assert session.get(Outbox, refs.registration).status == "pending"
        assert not session.get(ScheduledIntent, refs.intent).workflow_registered


def test_cancelled_intent_does_not_register(worker_database):
    settings, factory, refs = worker_database
    with factory() as session, session.begin():
        session.get(ScheduledIntent, refs.intent).status = "cancelled"

    class UnusedClient:
        async def start_workflow(self, *args, **kwargs):
            pytest.fail("Cancelled intent must not create a timer")

    assert asyncio.run(register_pending(UnusedClient(), factory, settings)) == 0
    with factory() as session:
        assert session.get(Outbox, refs.registration).status == "obsolete"


def test_relay_acknowledges_metadata_and_excludes_schedules(worker_database):
    _, factory, refs = worker_database

    class Producer:
        calls = []

        async def send_and_wait(self, topic, value, key):
            self.calls.append((topic, json.loads(value), key))

    producer = Producer()
    assert asyncio.run(relay_pending(producer, factory)) == 1
    assert asyncio.run(relay_pending(producer, factory)) == 0
    topic, envelope, key = producer.calls[0]
    assert topic == "assistant.events.v1"
    assert envelope["id"] == refs.event
    assert envelope["payload"]["live_eligible"] is True
    assert key == refs.event.encode()
    assert "text" not in envelope["payload"]
    with factory() as session:
        assert session.get(Outbox, refs.event).status == "published"
        assert session.get(Outbox, refs.registration).status == "pending"


def test_relay_failure_keeps_event_for_retry(worker_database):
    _, factory, refs = worker_database

    class FailedProducer:
        async def send_and_wait(self, *args, **kwargs):
            raise ConnectionError("synthetic unavailable broker")

    with pytest.raises(ConnectionError):
        asyncio.run(relay_pending(FailedProducer(), factory))
    with factory() as session:
        assert session.get(Outbox, refs.event).status == "pending"


@pytest.mark.parametrize("payload", [{"text": "private"}, {"message_id": {"text": "private"}}])
def test_relay_rejects_content_payloads(payload):
    row = SimpleNamespace(id="id", workspace_id="workspace", kind="message.accepted",
                          aggregate_id="aggregate", payload=payload)
    with pytest.raises(ValueError):
        event_envelope(row)


def test_relay_publishes_prepared_action_without_payload_content(app, owner_client, chat):
    from test_actions import grant
    from test_native import native_event
    from test_messaging import receive
    grant(owner_client, chat, kinds=["SEND_TEXT"], allowed_intents=["acknowledgement"])
    received = receive(owner_client, native_event(chat, content={"type": "text", "text": "Thanks for the update."}))
    assert received.status_code == 200, received.text
    source = received.json()["message_id"]
    response = owner_client.post("/actions", json={"conversation_id": chat["conversation"]["id"],
        "kind": "SEND_TEXT", "intent": "acknowledgement", "trigger_message_id": source,
        "text": "Thanks!"})
    assert response.status_code == 201, response.text
    action_id = response.json()["id"]
    envelopes = []

    class Producer:
        async def send_and_wait(self, topic, value, key):
            envelopes.append(json.loads(value))

    assert asyncio.run(relay_pending(Producer(), app.state.session_factory)) >= 2
    event = next(row for row in envelopes if row["kind"] == "action.ready")
    assert event["payload"]["action_id"] == action_id
    assert event["payload"]["kind"] == "SEND_TEXT"
    assert "Thanks!" not in json.dumps(envelopes)
    assert asyncio.run(relay_pending(Producer(), app.state.session_factory)) == 0
