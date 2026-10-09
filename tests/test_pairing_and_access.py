"""Number-bound personal pairing, dedicated session-service token, owner access code and config guards."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from assistant.config import Settings
from assistant.db import now
from assistant.models import Connector, User
from assistant.whatsapp_personal_models import PersonalWhatsAppSession
from test_business_owner_binding import workspace
from test_whatsapp_personal import INTERNAL, OWNER, OWNER_LID, PREFIX, envelope, post_event, settings

ACCESS_CODE = "synthetic-owner-access-code-with-32-plus-bytes"
AUTHORITY = "synthetic-session-authority-token-32-bytes"


def started(app, client, monkeypatch, state=None, **body):
    settings(app)
    wid = workspace(client)
    sent = []

    def private(_settings, operation, payload):
        sent.append((operation, payload))
        return dict(state or {"schema_version": 1, "state": "starting", "account_id": None})

    monkeypatch.setattr("assistant.whatsapp_personal.private_request", private)
    result = client.post(PREFIX + "/start", json={"workspace_id": wid, **body})
    assert result.status_code == 200, result.text
    return result.json()["connector"]["id"], sent


def test_pairing_completes_only_for_the_number_the_owner_entered(app, owner_client, monkeypatch):
    cid, sent = started(app, owner_client, monkeypatch, phone_number="+1 (555) 000-0000")
    assert sent[-1][1]["pairing_phone"] == "15550000000"
    with app.state.session_factory() as db:
        session = db.scalar(select(PersonalWhatsAppSession).where(PersonalWhatsAppSession.connector_id == cid))
        assert session.expected_account_hash and "15550000000" not in session.expected_account_hash
    paired = post_event(owner_client, envelope(app, cid), "connection", state="connected",
                        account_id=OWNER, account_aliases=[OWNER, OWNER_LID])
    assert paired.status_code == 200, paired.text


def test_relayed_pairing_by_another_account_is_refused_and_revoked(app, owner_client, monkeypatch):
    cid, _ = started(app, owner_client, monkeypatch, phone_number="+91 76978 74277")
    ident = envelope(app, cid)
    refused = post_event(owner_client, ident, "connection", state="connected",
                         account_id=OWNER, account_aliases=[OWNER, OWNER_LID])
    assert refused.status_code == 409
    with app.state.session_factory() as db:
        row = db.get(Connector, cid)
        assert row.status == "disconnected" and row.fence == ident["connector_fence"] + 1
        assert row.account_id != OWNER
        assert db.scalar(select(PersonalWhatsAppSession).where(PersonalWhatsAppSession.connector_id == cid)) is None


@pytest.mark.parametrize("number", ["7697874277", "+0 1234 5678", "+12", "+91 76978 74277 x9", "+1555000000012345"])
def test_pairing_number_must_be_international(app, owner_client, monkeypatch, number):
    settings(app)
    wid = workspace(owner_client)
    monkeypatch.setattr("assistant.whatsapp_personal.private_request", lambda *_: pytest.fail("No private start"))
    assert owner_client.post(PREFIX + "/start", json={"workspace_id": wid, "phone_number": number}).status_code == 422


def test_pairing_code_is_returned_only_when_valid_and_short_lived(app, owner_client, monkeypatch):
    state = {"schema_version": 1, "state": "qr", "account_id": None}
    cid, _ = started(app, owner_client, monkeypatch, state=state, phone_number="+1 555 000 0000")
    with app.state.session_factory() as db:
        db.get(Connector, cid).status = "pairing"
        db.commit()
    soon = (now() + timedelta(seconds=120)).isoformat()
    for code, expires, expected in [("ABCD1234", soon, "ABCD1234"), ("ABCD-1234", soon, "ABCD-1234"),
                                    ("abcd<12>", soon, None), ("ABCD1234", (now() + timedelta(hours=1)).isoformat(), None)]:
        state["pairing"] = {"code": code, "expires_at": expires}
        result = owner_client.get(PREFIX + "/pairing", params={"connector_id": cid})
        assert result.status_code == 200
        assert (result.json()["pairing_code"] or {}).get("code") == expected
        assert result.headers["cache-control"] in {"no-store", "private, no-store"}


def test_session_service_uses_its_own_token_and_it_opens_no_other_internal_route(app, owner_client, monkeypatch):
    app.state.settings.whatsapp_personal_authority_token = AUTHORITY
    cid, _ = started(app, owner_client, monkeypatch, phone_number="+1 555 000 0000")
    ident = envelope(app, cid)
    body = {**ident, "operation": "status", "send": None}
    assert owner_client.post("/internal/whatsapp-session-authority", headers=INTERNAL, json=body).status_code == 401
    session_headers = {"Authorization": f"Bearer {AUTHORITY}"}
    assert owner_client.post("/internal/whatsapp-session-authority", headers=session_headers, json=body).status_code == 200
    for path in ("/internal/connector-events", "/internal/jobs/run-due"):
        assert owner_client.post(path, headers=session_headers, json={}).status_code == 401


def test_owner_access_code_sign_in(app):
    with TestClient(app) as client:
        assert client.post("/auth/access-code", json={"code": ACCESS_CODE}).status_code == 404
        app.state.settings.owner_access_code = ACCESS_CODE
        assert client.get("/auth/config").json()["access_code_enabled"] is True
        assert client.post("/auth/access-code", json={"code": ACCESS_CODE[:-1]}).status_code == 401
        assert client.post("/auth/access-code", json={"code": ACCESS_CODE},
                           headers={"Origin": "https://attacker.invalid"}).status_code == 403
        result = client.post("/auth/access-code", json={"code": ACCESS_CODE})
        assert result.status_code == 200, result.text
        assert client.get("/me").status_code == 200
        with app.state.session_factory() as db:
            assert db.scalar(select(User.subject).where(User.id == client.get("/me").json()["id"])) == "operator:owner"


def test_access_code_owner_may_pair_personal_whatsapp_in_production(app, monkeypatch):
    app.state.settings.owner_access_code = ACCESS_CODE
    with TestClient(app) as client:
        login = client.post("/auth/access-code", json={"code": ACCESS_CODE})
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        settings(app)
        wid = workspace(client)
        monkeypatch.setattr("assistant.whatsapp_personal.private_request",
                            lambda *_: {"schema_version": 1, "state": "starting", "account_id": None})
        app.state.settings.environment = "production"
        assert client.post(PREFIX + "/start", json={"workspace_id": wid, "phone_number": "+1 555 000 0000"}).status_code == 200


def production(**changes):
    values = {"_env_file": None, "environment": "production", "database_url": "postgresql://u:p@db.invalid/app",
              "encryption_key": "b" * 43 + "=", "allowed_origins": "https://milo.example", "redis_url": "redis://r:6379/0"}
    return Settings(**{**values, **changes})


@pytest.mark.parametrize("changes, message", [
    ({"owner_access_code": "short"}, "owner_access_code"),
    ({"whatsapp_personal_authority_token": "x" * 31}, "whatsapp_personal_authority_token"),
    ({"internal_service_token": ACCESS_CODE, "owner_access_code": ACCESS_CODE}, "different secrets"),
    ({"internal_service_token": "weak"}, "internal service token"),
    ({"whatsapp_personal_enabled": True, "whatsapp_personal_session_url": "http://session.railway.internal:8091",
      "whatsapp_personal_session_token": "g" * 40, "internal_service_token": "i" * 40}, "dedicated session authority"),
])
def test_production_secret_guards(changes, message):
    with pytest.raises(ValueError, match=message):
        production(**changes).prepare()


def test_hosted_runtime_requires_explicit_environment(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    with pytest.raises(ValueError, match="ENVIRONMENT explicitly"):
        Settings(_env_file=None).prepare()
    assert Settings(_env_file=None, environment="test").prepare().environment == "test"
