"""Authenticated self identity lookup never exposes another owner or a token."""

from datetime import timedelta

from sqlalchemy import func, select

from assistant.db import now
from assistant.models import AuditEvent, Connector, User
from test_business_owner_binding import cloud_settings, google_login, workspace
from test_mobile_auth import native_login


def test_operator_identity_requires_authenticated_verified_google_owner(client, app, monkeypatch):
    path = "/v1/integrations/whatsapp/operator-identity"
    assert client.get(path, params={"workspace_id": "unknown"}).status_code == 401
    google_login(client, app, monkeypatch, "synthetic-google-sub")
    workspace_id = workspace(client)
    body = client.get(path, params={"workspace_id": workspace_id})
    assert body.status_code == 200
    assert body.json() == {"workspace_id": workspace_id, "provider": "google",
                           "google_subject": "synthetic-google-sub", "identity_verified": True}
    assert body.headers["cache-control"] == "no-store"
    assert client.get("/integrations/whatsapp/operator-identity", params={"workspace_id": workspace_id}).json() == body.json()


def test_development_identity_is_rejected_even_when_it_owns_workspace(owner_client):
    workspace_id = workspace(owner_client)
    result = owner_client.get("/v1/integrations/whatsapp/operator-identity", params={"workspace_id": workspace_id})
    assert result.status_code == 403
    assert "google_subject" not in result.text


def test_second_verified_owner_cannot_read_first_workspace_operator_identity(client, app, monkeypatch):
    google_login(client, app, monkeypatch, "first-google-sub")
    first_workspace = workspace(client)
    google_login(client, app, monkeypatch, "second-google-sub", email="other@example.test")
    second_workspace = workspace(client)
    other = client.get("/v1/integrations/whatsapp/operator-identity", params={"workspace_id": first_workspace})
    assert other.status_code == 404 and "first-google-sub" not in other.text
    own = client.get("/v1/integrations/whatsapp/operator-identity", params={"workspace_id": second_workspace})
    assert own.status_code == 200 and own.json()["google_subject"] == "second-google-sub"


def test_native_and_browser_verified_same_owner_receive_same_identity_without_tokens(client, app, monkeypatch):
    subject = "shared-google-sub"
    google_login(client, app, monkeypatch, subject)
    workspace_id = workspace(client)
    browser = client.get("/v1/integrations/whatsapp/operator-identity", params={"workspace_id": workspace_id})
    native = native_login(client, app, monkeypatch, subject=subject)
    mobile = client.get("/v1/integrations/whatsapp/operator-identity", params={"workspace_id": workspace_id},
                        headers={"Authorization": "Bearer " + native["access_token"]})
    assert browser.json() == mobile.json()
    assert native["access_token"] not in mobile.text and native["refresh_token"] not in mobile.text
    assert client.cookies.get("session_token") not in browser.text
    assert "credential" not in mobile.text and "token" not in mobile.text
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1


def test_lookup_is_readonly_and_does_not_claim_provider_access_or_disclose_configured_binding(client, app, monkeypatch):
    cloud_settings(app, subject="configured-other-owner")
    app.state.settings.whatsapp_app_secret = "synthetic-private-meta-secret"
    google_login(client, app, monkeypatch, "current-google-sub")
    workspace_id = workspace(client)
    with app.state.session_factory() as db:
        before_audits = db.scalar(select(func.count()).select_from(AuditEvent))
    response = client.get("/v1/integrations/whatsapp/operator-identity", params={"workspace_id": workspace_id})
    assert response.status_code == 200 and response.json()["google_subject"] == "current-google-sub"
    for private in ("configured-other-owner", "synthetic-private-meta-secret", "synthetic-server-provider-secret"):
        assert private not in response.text
    assert client.get("/v1/integrations/whatsapp/status", params={"workspace_id": workspace_id}).json()["owner_authorized"] is False
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == before_audits
        assert db.scalar(select(func.count()).select_from(Connector)) == 0


def test_business_status_exposes_exact_lease_expiry_and_null_when_unset(client, app, monkeypatch):
    cloud_settings(app, subject="current-google-sub")
    google_login(client, app, monkeypatch, "current-google-sub")
    workspace_id = workspace(client)
    expiry = now() + timedelta(minutes=5)
    with app.state.session_factory() as db:
        connector = Connector(workspace_id=workspace_id, provider="whatsapp_cloud", account_id="operator-phone-id",
                              owner_sender_id="operator-phone-id", status="connected", lease_expires_at=expiry)
        db.add(connector)
        db.commit()
        connector_id = connector.id
    params = {"workspace_id": workspace_id}
    first = client.get("/v1/integrations/whatsapp/status", params=params).json()["connectors"][0]
    assert first["lease_expires_at"] == expiry.isoformat() and first["lease_valid"] is True
    with app.state.session_factory() as db:
        db.get(Connector, connector_id).lease_expires_at = None
        db.commit()
    second = client.get("/v1/integrations/whatsapp/status", params=params).json()["connectors"][0]
    assert second["lease_expires_at"] is None and second["lease_valid"] is False
