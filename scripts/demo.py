"""Run a synthetic backend walkthrough through HTTP, without external services."""

import json
import secrets
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from assistant.config import Settings
from assistant.db import Base
from assistant.main import create_app


@contextmanager
def synthetic_backend():
    Path(".local").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="synthetic-", dir=".local") as directory:
        settings = Settings(
            environment="test", database_url=f"sqlite:///{Path(directory).resolve()}/demo.db",
            encryption_key=Fernet.generate_key().decode(), allow_dev_auth=True, session_secure=False,
            model_provider="mock", internal_service_token=secrets.token_urlsafe(32),
        )
        app = create_app(settings)
        # This disposable test fixture uses the model schema. Real development uses Alembic.
        Base.metadata.create_all(app.state.engine)
        with TestClient(app) as client:
            yield app, client, settings


def checked(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    if not response.is_success:
        raise RuntimeError(f"Synthetic request failed: {method} {path}: {response.status_code}")
    return response.json() if response.content else None


def seed_owner(client):
    login = checked(client, "POST", "/auth/dev", json={
        "email": f"synthetic-{uuid4()}@example.invalid", "display_name": "Owner",
    })
    client.headers["X-CSRF-Token"] = login["csrf_token"]
    workspace = checked(client, "POST", "/workspaces", json={"name": "Synthetic walkthrough", "timezone": "UTC"})
    connector = checked(client, "POST", "/connectors", json={
        "workspace_id": workspace["id"], "provider": "mock", "account_id": f"synthetic-{uuid4()}",
        "owner_sender_id": "Owner",
    })
    conversation = checked(client, "POST", "/conversations", json={
        "connector_id": connector["id"], "provider_chat_id": "friend", "title": "Synthetic friend",
        "recipient_opted_in": True,
    })
    checked(client, "PUT", f"/conversations/{conversation['id']}/permissions", json={
        "read": True, "retain": True, "learn": True, "draft": True, "send": True, "share": False,
    })
    return workspace, connector, conversation


def incoming(connector_id, conversation_id, index):
    return {
        "event_id": f"synthetic-event-{index}", "connector_id": connector_id,
        "conversation_id": conversation_id, "provider_message_id": f"synthetic-message-{index}",
        "sender_id": "friend", "direction": "inbound", "origin": "live",
        "author_kind": "contact_human", "provider_timestamp": datetime.now(UTC).isoformat(),
        "content": {"type": "text", "text": "Thanks for the update!"},
    }


def run_demo(client, settings):
    workspace, connector, conversation = seed_owner(client)
    cid = conversation["id"]
    export = "\n".join([
        "06/10/2026, 09:00 - Owner: Hey! Thanks for the note 🙂",
        "06/10/2026, 09:01 - Friend: Thanks! Please confirm the plan.",
        "06/10/2026, 09:02 - Owner: I'll check and get back to you.",
        "06/10/2026, 09:03 - Friend: Sounds good.",
    ])
    imported = checked(client, "POST", "/imports", json={
        "conversation_id": cid, "text": export, "owner_sender_label": "Owner",
        "date_order": "DMY", "timezone": "UTC",
    })
    replayed = checked(client, "POST", "/imports", json={
        "conversation_id": cid, "text": export, "owner_sender_label": "Owner",
        "date_order": "DMY", "timezone": "UTC",
    })
    assert replayed["replayed"] is True
    style = checked(client, "POST", f"/conversations/{cid}/style-preview")
    checked(client, "POST", "/internal/connector-events", json=incoming(connector["id"], cid, "demo"),
            headers={"Authorization": f"Bearer {settings.internal_service_token}"})
    draft = checked(client, "POST", f"/conversations/{cid}/drafts", json={"instruction": "Acknowledge the message."})
    draft = checked(client, "PATCH", f"/drafts/{draft['id']}", json={"text": "Thanks! I'll check and get back to you."})
    approved = checked(client, "POST", f"/drafts/{draft['id']}/approve", json={"content_hash": draft["content_hash"]})
    sent = checked(client, "POST", f"/drafts/{draft['id']}/dispatch")
    again = checked(client, "POST", f"/drafts/{draft['id']}/dispatch")
    assert sent["attempt_id"] == again["attempt_id"]
    assert sent["status"] == "accepted"
    checked(client, "POST", "/pause-all", params={"workspace_id": workspace["id"]})
    return {
        "mode": "synthetic_only", "imported_records": imported["message_count"],
        "reimport_deduplicated": True, "owner_style_sample_count": style["sample_count"],
        "approved_status": approved["status"], "dispatch_status": sent["status"],
        "repeat_dispatch_same_attempt": True, "pause_all": "accepted",
        "external_network_calls": 0,
    }


if __name__ == "__main__":
    with synthetic_backend() as (_, demo_client, demo_settings):
        print(json.dumps(run_demo(demo_client, demo_settings), indent=2))
