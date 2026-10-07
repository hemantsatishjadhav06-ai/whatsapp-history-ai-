from datetime import timedelta

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from assistant.config import Settings
from assistant.db import now
from assistant.models import Connector


def google_login(client, app, monkeypatch, subject, email="owner@example.test"):
    app.state.settings.google_client_id = "configured-pilot-client"
    nonce = client.get("/auth/nonce").json()["nonce"]
    claims = {"sub": subject, "email": email, "email_verified": True,
              "iss": "https://accounts.google.com", "aud": "configured-pilot-client",
              "nonce": nonce, "exp": (now() + timedelta(hours=1)).timestamp()}
    monkeypatch.setattr("assistant.auth.google_id_token.verify_oauth2_token", lambda *_: claims)
    response = client.post("/auth/google", json={"credential": "synthetic-signed-credential", "nonce": nonce},
                           headers={"X-CSRF-Token": nonce})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]


def cloud_settings(app, subject="authorized-google-sub"):
    app.state.settings.whatsapp_authorized_owner_subject = subject
    app.state.settings.whatsapp_phone_number_id = "operator-phone-id"
    app.state.settings.whatsapp_access_token = "synthetic-server-provider-secret"


def workspace(client):
    response = client.post("/workspaces", json={"name": "Pilot"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def connector_body(workspace_id, **extra):
    return {"workspace_id": workspace_id, "provider": "whatsapp_cloud",
            "account_id": "operator-phone-id", "owner_sender_id": "Owner", **extra}


def reject_provider_network(monkeypatch):
    def forbidden(*_, **__):
        raise AssertionError("Unauthorized owner must never use server-held Meta credentials")
    monkeypatch.setattr(httpx, "get", forbidden)


@pytest.mark.parametrize("binding", ["", "authorized-google-sub"])
def test_matching_email_development_owner_cannot_claim_business_number(app, owner_client, monkeypatch, binding):
    cloud_settings(app, binding)
    reject_provider_network(monkeypatch)
    response = owner_client.post("/connectors", json=connector_body(workspace(owner_client)))
    assert response.status_code == 403
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Connector)) == 0


@pytest.mark.parametrize("binding", ["", "authorized-google-sub"])
def test_other_verified_google_owner_cannot_be_first_to_claim_known_number(app, client, monkeypatch, binding):
    cloud_settings(app, binding)
    google_login(client, app, monkeypatch, "other-google-sub")
    reject_provider_network(monkeypatch)
    response = client.post("/connectors", json=connector_body(workspace(client)))
    assert response.status_code == 403
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Connector)) == 0


def test_exact_google_owner_can_claim_and_verify_configured_number(app, client, monkeypatch):
    cloud_settings(app)
    google_login(client, app, monkeypatch, "authorized-google-sub")
    body = connector_body(workspace(client))
    response = client.post("/connectors", json=body)
    assert response.status_code == 201, response.text
    connector = response.json()
    assert connector["status"] == "needs_verification"
    requests = []
    def meta_get(url, **kwargs):
        requests.append((url, kwargs))
        return httpx.Response(200, request=httpx.Request("GET", url), json={"id": "operator-phone-id"})
    monkeypatch.setattr(httpx, "get", meta_get)
    verified = client.post(f"/connectors/{connector['id']}/verify")
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "connected"
    assert len(requests) == 1
    assert requests[0][0].endswith("/operator-phone-id")
    assert requests[0][1]["headers"]["Authorization"] == "Bearer synthetic-server-provider-secret"
    assert "synthetic-server-provider-secret" not in verified.text
    assert client.post("/connectors", json=body).json()["id"] == connector["id"]


@pytest.mark.parametrize("binding", ["", "authorized-google-sub"])
def test_existing_unauthorized_business_row_cannot_verify_with_global_credentials(app, client, monkeypatch, binding):
    cloud_settings(app, binding)
    google_login(client, app, monkeypatch, "other-google-sub")
    workspace_id = workspace(client)
    with app.state.session_factory() as db:
        row = Connector(**connector_body(workspace_id), status="needs_verification")
        db.add(row)
        db.commit()
        connector_id = row.id
    reject_provider_network(monkeypatch)
    response = client.post(f"/connectors/{connector_id}/verify")
    assert response.status_code == 403
    with app.state.session_factory() as db:
        assert db.get(Connector, connector_id).status == "needs_verification"


def test_authorized_owner_cannot_verify_a_legacy_different_number(app, client, monkeypatch):
    cloud_settings(app)
    google_login(client, app, monkeypatch, "authorized-google-sub")
    with app.state.session_factory() as db:
        row = Connector(**connector_body(workspace(client), account_id="legacy-different-phone"),
                        status="needs_verification")
        db.add(row)
        db.commit()
        connector_id = row.id
    reject_provider_network(monkeypatch)
    assert client.post(f"/connectors/{connector_id}/verify").status_code == 409


@pytest.mark.parametrize("authority", [{"subject": "authorized-google-sub"}, {"role": "operator"},
                                      {"whatsapp_authorized_owner_subject": "authorized-google-sub"}])
def test_request_supplied_business_authority_cannot_override_operator_binding(app, owner_client, monkeypatch, authority):
    cloud_settings(app)
    reject_provider_network(monkeypatch)
    assert owner_client.post("/connectors", json=connector_body(workspace(owner_client), **authority)).status_code == 422
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Connector)) == 0


@pytest.mark.parametrize("subject", ["google:authorized-google-sub", " owner", "owner ", "owner\nother", "a" * 249])
def test_binding_rejects_ambiguous_or_namespaced_subject_config(subject):
    with pytest.raises(ValueError, match="raw verified Google subject"):
        Settings(_env_file=None, environment="test", encryption_key=Fernet.generate_key().decode(),
                 whatsapp_authorized_owner_subject=subject).prepare()
