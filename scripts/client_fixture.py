"""Disposable loopback API for real browser integration tests; never deploy it."""

import argparse
import secrets
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from assistant.config import Settings
from assistant.db import Base
from assistant.main import create_app


def require(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else None


def seed(application, settings):
    with TestClient(application) as client:
        auth = require(client, "POST", "/auth/dev", json={
            "email": "web-e2e@example.invalid", "display_name": "Browser owner",
        })
        client.headers["X-CSRF-Token"] = auth["csrf_token"]
        workspace = require(client, "POST", "/workspaces", json={"name": "Browser fixture", "timezone": "UTC"})
        connector = require(client, "POST", "/connectors", json={
            "workspace_id": workspace["id"], "provider": "mock", "account_id": "browser-fixture",
            "owner_sender_id": "Owner",
        })
        for name in ("Maya", "Neha"):
            conversation = require(client, "POST", "/conversations", json={
                "connector_id": connector["id"], "provider_chat_id": name.lower(),
                "title": name, "recipient_opted_in": True,
            })
            require(client, "PUT", f"/conversations/{conversation['id']}/permissions", json={
                "read": True, "retain": True, "learn": True, "draft": True, "send": True,
            })
            require(client, "POST", "/imports", json={
                "conversation_id": conversation["id"], "owner_sender_label": "Owner", "date_order": "DMY",
                "timezone": "UTC", "text": "06/10/2026, 09:00 - Owner: Thanks, I'll check.\n"
                f"06/10/2026, 09:01 - {name}: What time would work for you?",
            })
            require(client, "POST", f"/conversations/{conversation['id']}/style-preview")
            if name == "Neha":
                require(client, "POST", "/internal/connector-events", headers={
                    "Authorization": f"Bearer {settings.internal_service_token}",
                }, json={
                    "event_id": "browser-human-phone", "connector_id": connector["id"],
                    "conversation_id": conversation["id"], "provider_message_id": "browser-phone-1",
                    "sender_id": "Owner", "direction": "outbound", "origin": "live",
                    "author_kind": "human_owner", "provider_timestamp": datetime.now(UTC).isoformat(),
                    "content": {"type": "text", "text": "I'll handle this myself."},
                })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8001)
    parser.add_argument('--web-origin', action='append')
    arguments = parser.parse_args()
    if not 1024 <= arguments.port <= 65535:
        parser.error('Use an unprivileged loopback port')
    from urllib.parse import urlsplit
    origins = arguments.web_origin or ['http://127.0.0.1:3000', 'http://localhost:3000']
    for value in origins:
        endpoint = urlsplit(value)
        if (endpoint.scheme != 'http' or endpoint.hostname not in {'localhost', '127.0.0.1', '::1'}
                or endpoint.path not in {'', '/'} or endpoint.username or endpoint.password
                or endpoint.query or endpoint.fragment):
            parser.error('Only exact loopback test origins are accepted')
    with tempfile.TemporaryDirectory(prefix="milo-browser-", dir="/tmp") as directory:
        settings = Settings(
            _env_file=None, environment="test", database_url=f"sqlite:///{Path(directory) / 'fixture.db'}",
            encryption_key=Fernet.generate_key().decode(), allow_dev_auth=True, session_secure=False,
            model_provider="mock", internal_service_token=secrets.token_urlsafe(32),
            allowed_origins=','.join(origins),
        )
        app = create_app(settings)
        Base.metadata.create_all(app.state.engine)
        seed(app, settings)
        uvicorn.run(app, host="127.0.0.1", port=arguments.port, access_log=False)
