"""Session and login boundary tests; Google signature verification is mocked.

These checks do not replace a live Google OAuth smoke test with a real client ID.
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from assistant import auth
from assistant.db import now
from assistant.models import LoginNonce, SessionRecord, User


def login(client, email="owner@example.test"):
    response = client.post("/auth/dev", json={"email": email, "display_name": "Owner"})
    assert response.status_code == 200, response.text
    return response.json()


def google_challenge(client, app, monkeypatch, claim_overrides=None):
    app.state.settings.google_client_id = "test-google-client"
    challenge = client.get("/auth/nonce")
    assert challenge.status_code == 200, challenge.text
    nonce = challenge.json()["nonce"]
    claims = {
        "sub": "verified-google-subject",
        "email": "google-owner@example.test",
        "email_verified": True,
        "name": "Google Owner",
        "iss": "https://accounts.google.com",
        "aud": "test-google-client",
        "exp": int((now() + timedelta(minutes=10)).timestamp()),
        "nonce": nonce,
    }
    claims.update(claim_overrides or {})
    monkeypatch.setattr(auth.google_id_token, "verify_oauth2_token", lambda credential, request, audience: claims)
    return nonce, claims


def exchange_google(client, nonce, **kwargs):
    return client.post("/auth/google", json={"credential": "mocked-signed-google-token", "nonce": nonce},
                       headers={"X-CSRF-Token": nonce, **kwargs})


def test_development_login_session_cookie_and_stable_identity(client, app):
    first = login(client, "Owner@Example.test")
    assert first["user"]["email"] == "owner@example.test"
    assert client.get("/me").json() == first["user"]
    first_token = client.cookies.get(auth.SESSION_COOKIE)
    second = login(client, "owner@example.test")
    assert first["user"]["id"] == second["user"]["id"]
    assert first["csrf_token"] != second["csrf_token"]
    assert first_token != client.cookies.get(auth.SESSION_COOKIE)
    with app.state.session_factory() as db:
        assert db.scalar(select(User).where(User.subject == "dev:owner@example.test")).id == first["user"]["id"]
        sessions = db.scalars(select(SessionRecord)).all()
        assert len(sessions) == 1
        assert sessions[0].token_hash == auth.digest(client.cookies.get(auth.SESSION_COOKIE))
        assert sessions[0].csrf_hash == auth.digest(second["csrf_token"])
        assert sessions[0].token_hash != client.cookies.get(auth.SESSION_COOKIE)


def test_session_cookie_is_http_only_and_no_store(client):
    response = client.post("/auth/dev", json={"email": "owner@example.test"})
    assert response.status_code == 200
    cookie = next(header for header in response.headers.get_list("set-cookie") if header.startswith("session_token="))
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie
    assert response.headers["cache-control"] == "no-store"


def test_logout_requires_csrf_and_revokes_session(client, app):
    identity = login(client)
    token = client.cookies.get(auth.SESSION_COOKIE)
    assert client.post("/auth/logout").status_code == 403
    assert client.post("/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.get("/me").status_code == 200
    assert client.post("/auth/logout", headers={"X-CSRF-Token": identity["csrf_token"]}).status_code == 204
    assert client.get("/me").status_code == 401
    with app.state.session_factory() as db:
        assert db.scalar(select(SessionRecord).where(SessionRecord.token_hash == auth.digest(token))) is None
    client.cookies.set(auth.SESSION_COOKIE, token)
    assert client.get("/me").status_code == 401


def test_expired_and_unknown_session_fail_closed(client, app):
    assert client.get("/me").status_code == 401
    client.cookies.set(auth.SESSION_COOKIE, "unrecognized-token")
    assert client.get("/me").status_code == 401
    client.cookies.clear()
    login(client)
    with app.state.session_factory() as db:
        session = db.scalar(select(SessionRecord))
        session.expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert client.get("/me").status_code == 401


@pytest.mark.parametrize("environment,enabled", [("development", False), ("production", True)])
def test_development_login_requires_explicit_nonproduction_setting(client, app, environment, enabled):
    app.state.settings.environment = environment
    app.state.settings.allow_dev_auth = enabled
    assert client.post("/auth/dev", json={"email": "owner@example.test"}).status_code == 404
    assert client.get("/me").status_code == 401


def test_browser_origin_is_checked_for_login_and_unsafe_requests(client):
    assert client.post("/auth/dev", json={"email": "owner@example.test"},
                       headers={"Origin": "https://attacker.example"}).status_code == 403
    identity = login(client)
    assert client.post("/auth/logout", headers={"Origin": "https://attacker.example",
                                               "X-CSRF-Token": identity["csrf_token"]}).status_code == 403
    assert client.post("/auth/logout", headers={"Origin": "null",
                                               "X-CSRF-Token": identity["csrf_token"]}).status_code == 403
    assert client.get("/me").status_code == 200


def test_google_login_verifies_identity_binds_nonce_and_consumes_once(client, app, monkeypatch):
    nonce, _ = google_challenge(client, app, monkeypatch)
    with app.state.session_factory() as db:
        pending = db.scalar(select(LoginNonce))
        assert pending.nonce_hash == auth.digest(nonce)
        assert pending.nonce_hash != nonce
    response = exchange_google(client, nonce)
    assert response.status_code == 200, response.text
    assert response.json()["user"]["email"] == "google-owner@example.test"
    assert client.get("/me").status_code == 200
    with app.state.session_factory() as db:
        assert db.scalar(select(LoginNonce)) is None
        user = db.scalar(select(User))
        assert user.subject == "google:verified-google-subject"
    client.cookies.clear()
    client.cookies.set(auth.NONCE_COOKIE, nonce, path="/auth")
    assert exchange_google(client, nonce).status_code == 401
    assert client.get("/me").status_code == 401


def test_google_identity_uses_subject_when_verified_email_changes(client, app, monkeypatch):
    nonce, _ = google_challenge(client, app, monkeypatch)
    first = exchange_google(client, nonce).json()["user"]
    nonce, _ = google_challenge(client, app, monkeypatch, {"email": "new-address@example.test"})
    second = exchange_google(client, nonce).json()["user"]
    assert second["id"] == first["id"]
    assert second["email"] == "new-address@example.test"


@pytest.mark.parametrize("overrides", [
    {"email_verified": False}, {"email_verified": "true"}, {"aud": "other-client"},
    {"iss": "https://attacker.example"}, {"exp": 0}, {"exp": "99999999999"},
    {"nonce": "wrong-nonce"}, {"nonce": "non-ascii-\u00e9"}, {"sub": ""},
    {"email": "not-an-email"}, {"azp": "other-client"},
    {"aud": ["test-google-client", "other-client"]},
])
def test_google_claim_rejections_never_create_session(client, app, monkeypatch, overrides):
    nonce, _ = google_challenge(client, app, monkeypatch, overrides)
    assert exchange_google(client, nonce).status_code == 401
    assert client.get("/me").status_code == 401
    with app.state.session_factory() as db:
        assert db.scalar(select(SessionRecord)) is None
        assert db.scalar(select(User)) is None


def test_invalid_google_signature_is_not_authenticated(client, app, monkeypatch):
    nonce, _ = google_challenge(client, app, monkeypatch)

    def reject(credential, request, audience):
        raise ValueError("Invalid token signature")

    monkeypatch.setattr(auth.google_id_token, "verify_oauth2_token", reject)
    assert exchange_google(client, nonce).status_code == 401
    assert client.get("/me").status_code == 401


def test_google_nonce_cookie_body_and_header_must_match(client, app, monkeypatch):
    nonce, _ = google_challenge(client, app, monkeypatch)
    assert client.post("/auth/google", json={"credential": "token", "nonce": nonce}).status_code == 403
    assert exchange_google(client, nonce, **{"X-CSRF-Token": "wrong"}).status_code == 403
    client.cookies.clear()
    assert exchange_google(client, nonce).status_code == 403
    assert client.get("/me").status_code == 401


def test_google_expired_nonce_is_rejected(client, app, monkeypatch):
    nonce, _ = google_challenge(client, app, monkeypatch)
    with app.state.session_factory() as db:
        pending = db.scalar(select(LoginNonce))
        pending.expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert exchange_google(client, nonce).status_code == 401


def test_new_nonce_invalidates_previous_challenge(client, app, monkeypatch):
    previous, _ = google_challenge(client, app, monkeypatch)
    current, _ = google_challenge(client, app, monkeypatch)
    assert previous != current
    with app.state.session_factory() as db:
        assert db.scalar(select(LoginNonce).where(LoginNonce.nonce_hash == auth.digest(previous))) is None
    assert exchange_google(client, previous).status_code == 403


def test_google_unconfigured_returns_service_unavailable(client, app, monkeypatch):
    nonce, _ = google_challenge(client, app, monkeypatch)
    app.state.settings.google_client_id = ""
    assert exchange_google(client, nonce).status_code == 503
