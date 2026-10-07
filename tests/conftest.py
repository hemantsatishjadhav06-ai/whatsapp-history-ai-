import os

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from assistant.config import Settings
from assistant.db import Base


TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture
def app(tmp_path):
    from assistant.main import create_app
    test_url = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    application = create_app(Settings(_env_file=None, environment="test",
                            database_url=test_url, encryption_key=TEST_KEY,
                            allow_dev_auth=True, session_secure=False, model_provider="mock",
                            internal_service_token="test-internal-service-token"))
    Base.metadata.create_all(application.state.engine)
    yield application
    if not test_url.startswith("sqlite"):
        Base.metadata.drop_all(application.state.engine)
    application.state.engine.dispose()


@pytest.fixture
def client(app):
    with TestClient(app) as value:
        yield value


@pytest.fixture
def db(app):
    with app.state.session_factory() as value:
        yield value


def login(client, email="owner@example.test"):
    response = client.post("/auth/dev", json={"email": email})
    assert response.status_code == 200, response.text
    data = response.json()
    client.headers["X-CSRF-Token"] = data["csrf_token"]
    return data


@pytest.fixture
def owner_client(client):
    login(client)
    return client


def create_chat(client, *, kind="contact", provider="mock", name="Client", account="synthetic-owner",
                read=True, retain=True, learn=True, draft=True, send=True, recipient="15550001234"):
    work = client.post("/workspaces", json={"name": "Owner", "timezone": "Asia/Kolkata"})
    assert work.status_code == 201, work.text
    workspace = work.json()
    conn = client.post("/connectors", json={"workspace_id": workspace["id"], "provider": provider,
                                            "account_id": account, "owner_sender_id": "Owner"})
    assert conn.status_code == 201, conn.text
    connector = conn.json()
    conv = client.post("/conversations", json={"connector_id": connector["id"], "provider_chat_id": recipient,
                                              "title": name, "kind": kind, "recipient_opted_in": True,
                                              "group_send_allowed": kind == "group"})
    assert conv.status_code == 201, conv.text
    conversation = conv.json()
    grant = client.put(f"/conversations/{conversation['id']}/permissions",
                       json={"read": read, "retain": retain, "learn": learn, "draft": draft, "send": send})
    assert grant.status_code == 200, grant.text
    return {"workspace": workspace, "connector": connector, "conversation": conversation,
            "permissions": grant.json()}


@pytest.fixture
def chat(owner_client):
    return create_chat(owner_client)
