"""Native auth boundaries; Google signature verification is replaced in tests."""

from datetime import timedelta

import pytest
from sqlalchemy import select, text

from assistant import auth
from assistant.db import now
from assistant.mobile_auth import proof_challenge
from assistant.mobile_models import NativeLoginChallenge, NativeSession
from conftest import login


VERIFIER = "a" * 43


def native_login(client, app, monkeypatch, *, subject="native-owner", overrides=None, exchange=True):
    app.state.settings.google_ios_client_id = "configured-native-ios-client"
    challenge = client.post("/v1/auth/native/nonce", json={"platform": "ios", "device_name": "Owner's iPhone",
                           "code_challenge": proof_challenge(VERIFIER)})
    assert challenge.status_code == 200, challenge.text
    nonce = challenge.json()["nonce"]
    claims = {"sub": subject, "email": f"{subject}@example.test", "email_verified": True,
              "iss": "https://accounts.google.com", "aud": "configured-native-ios-client",
              "exp": int((now() + timedelta(minutes=10)).timestamp()), "nonce": nonce, "name": "Native Owner"}
    claims.update(overrides or {})
    monkeypatch.setattr(auth.google_id_token, "verify_oauth2_token", lambda credential, request, audience: claims)
    body = {"credential": "synthetic-signed-token", "nonce": nonce, "code_verifier": VERIFIER}
    if not exchange:
        return body
    response = client.post("/v1/auth/native/login", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_native_nonce_requires_configured_platform_and_no_browser_origin(client, app):
    body = {"platform": "ios", "code_challenge": proof_challenge(VERIFIER)}
    assert client.post("/v1/auth/native/nonce", json=body).status_code == 503
    app.state.settings.google_ios_client_id = "configured-native-ios-client"
    assert client.post("/v1/auth/native/nonce", json=body, headers={"Origin": "http://localhost:3000"}).status_code == 403
    assert client.post("/v1/auth/native/nonce", json={**body, "code_challenge": "plaintext-not-proof"}).status_code == 422


def test_native_exchange_stores_hash_only_and_authenticates_existing_api_without_cookie(client, app, monkeypatch):
    result = native_login(client, app, monkeypatch)
    token = result["access_token"]
    assert result["token_type"] == "Bearer" and result["refresh_supported"] is True
    assert not client.cookies.get("session_token")
    client.headers["Authorization"] = f"Bearer {token}"
    assert client.get("/v1/me").json() == result["user"]
    workspace = client.post("/v1/workspaces", json={"name": "Native", "timezone": "Asia/Kolkata"})
    assert workspace.status_code == 201
    with app.state.session_factory() as db:
        session = db.scalar(select(NativeSession))
        assert session.token_hash == auth.digest(token) and session.token_hash != token
        assert session.device_name == "Owner's iPhone"
        assert db.scalar(select(NativeLoginChallenge)) is None
        ciphertext = db.scalar(text("SELECT device_name FROM native_sessions"))
        assert "iPhone" not in ciphertext


def test_single_use_native_nonce_and_proof_replay_rejected(client, app, monkeypatch):
    body = native_login(client, app, monkeypatch, exchange=False)
    assert client.post("/v1/auth/native/login", json={**body, "code_verifier": "b" * 43}).status_code == 401
    assert client.post("/v1/auth/native/login", json=body).status_code == 200
    assert client.post("/v1/auth/native/login", json=body).status_code == 401


@pytest.mark.parametrize("overrides", [
    {"nonce": "forged-nonce"}, {"aud": "configured-browser-only-client"}, {"iss": "https://attacker.test"},
    {"exp": 0}, {"email_verified": False}, {"azp": "another-app"},
])
def test_native_google_claims_are_verified(client, app, monkeypatch, overrides):
    body = native_login(client, app, monkeypatch, overrides=overrides, exchange=False)
    assert client.post("/v1/auth/native/login", json=body).status_code == 401


def test_native_expired_challenge_fails(client, app, monkeypatch):
    body = native_login(client, app, monkeypatch, exchange=False)
    with app.state.session_factory() as db:
        db.scalar(select(NativeLoginChallenge)).expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert client.post("/v1/auth/native/login", json=body).status_code == 401


def test_expired_native_session_and_web_token_bearer_fail_closed(client, app, monkeypatch):
    browser = login(client)
    token = client.cookies.get("session_token")
    assert client.get("/v1/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert client.get("/v1/me", headers={"Authorization": "Bearer na_invalid"}).status_code == 401
    assert client.get("/v1/me").json()["id"] == browser["user"]["id"]
    result = native_login(client, app, monkeypatch)
    with app.state.session_factory() as db:
        db.scalar(select(NativeSession)).expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert client.get("/v1/me", headers={"Authorization": f"Bearer {result['access_token']}"}).status_code == 401


def test_native_logout_revokes_and_has_no_browser_csrf_dependency(client, app, monkeypatch):
    result = native_login(client, app, monkeypatch)
    client.headers["Authorization"] = f"Bearer {result['access_token']}"
    assert client.post("/v1/auth/native/logout").status_code == 204
    assert client.get("/v1/me").status_code == 401


def test_session_list_and_revocation_are_owner_scoped(client, app, monkeypatch):
    result = native_login(client, app, monkeypatch)
    login(client, "other@example.test")
    sessions = client.get("/v1/auth/sessions").json()["sessions"]
    assert all(row["id"] != result["session_id"] for row in sessions)
    assert client.delete(f"/v1/auth/sessions/{result['session_id']}").status_code == 404
    assert client.get("/v1/me", headers={"Authorization": f"Bearer {result['access_token']}"}).status_code == 200
    client.headers["Authorization"] = f"Bearer {result['access_token']}"
    sessions = client.get("/v1/auth/sessions").json()["sessions"]
    assert sessions[0]["id"] == result["session_id"] and sessions[0]["current"] is True
    assert "token_hash" not in sessions[0]
    assert client.delete(f"/v1/auth/sessions/{result['session_id']}").status_code == 204
    assert client.get("/v1/me").status_code == 401


def test_browser_session_revocation_still_requires_csrf(client):
    login(client)
    row = client.get("/v1/auth/sessions").json()["sessions"][0]
    client.headers.pop("X-CSRF-Token")
    assert client.delete(f"/v1/auth/sessions/{row['id']}").status_code == 403


def test_bearer_cannot_disable_browser_origin_controls(client, app, monkeypatch):
    result = native_login(client, app, monkeypatch)
    headers = {"Authorization": f"Bearer {result['access_token']}", "Origin": "https://attacker.example"}
    assert client.post("/v1/workspaces", json={"name": "Wrong origin"}, headers=headers).status_code == 403


def test_native_refresh_rotates_access_and_refresh_without_extending_deadline(client, app, monkeypatch):
    original = native_login(client, app, monkeypatch)
    renewed = client.post("/v1/auth/native/refresh", json={"refresh_token": original["refresh_token"]})
    assert renewed.status_code == 200, renewed.text
    current = renewed.json()
    assert current["access_token"] != original["access_token"] and current["refresh_token"] != original["refresh_token"]
    assert current["session_id"] == original["session_id"]
    assert current["refresh_expires_at"] == original["refresh_expires_at"]
    assert client.get("/v1/me", headers={"Authorization": f"Bearer {original['access_token']}"}).status_code == 401
    assert client.get("/v1/me", headers={"Authorization": f"Bearer {current['access_token']}"}).status_code == 200
    assert client.post("/v1/auth/native/refresh", json={"refresh_token": original["refresh_token"]}).status_code == 401
    with app.state.session_factory() as db:
        row = db.scalar(select(NativeSession))
        assert row.refresh_token_hash == auth.digest(current["refresh_token"])
        assert current["refresh_token"] not in row.refresh_token_hash


def test_concurrent_refresh_has_one_winner_and_rejects_replay(client, app, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    original = native_login(client, app, monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.post, "/v1/auth/native/refresh",
                   json={"refresh_token": original["refresh_token"]}) for _ in range(2)]
        responses = [future.result() for future in futures]
    assert sorted(response.status_code for response in responses) == [200, 401]


def test_refresh_after_access_expiry_and_revocation(client, app, monkeypatch):
    original = native_login(client, app, monkeypatch)
    with app.state.session_factory() as db:
        db.scalar(select(NativeSession)).expires_at = now() - timedelta(seconds=1)
        db.commit()
    current = client.post("/v1/auth/native/refresh", json={"refresh_token": original["refresh_token"]}).json()
    client.headers["Authorization"] = f"Bearer {current['access_token']}"
    assert client.delete(f"/v1/auth/sessions/{current['session_id']}").status_code == 204
    assert client.post("/v1/auth/native/refresh", json={"refresh_token": current["refresh_token"]}).status_code == 401


def test_refresh_expiry_and_browser_cookie_do_not_renew_a_device(client, app, monkeypatch):
    original = native_login(client, app, monkeypatch)
    with app.state.session_factory() as db:
        db.scalar(select(NativeSession)).refresh_expires_at = now() - timedelta(seconds=1)
        db.commit()
    login(client, "browser-owner@example.test")
    assert client.post("/v1/auth/native/refresh", json={"refresh_token": original["refresh_token"]}).status_code == 401
    assert client.post("/v1/auth/native/refresh", json={"refresh_token": original["refresh_token"]},
                       headers={"Origin": "http://localhost:3000"}).status_code == 403


def test_refresh_rotates_only_the_presented_device_session(client, app, monkeypatch):
    first = native_login(client, app, monkeypatch, subject="first-native-owner")
    second = native_login(client, app, monkeypatch, subject="second-native-owner")
    current = client.post("/v1/auth/native/refresh", json={"refresh_token": first["refresh_token"]}).json()
    assert current["user"]["id"] == first["user"]["id"]
    assert client.get("/v1/me", headers={"Authorization": f"Bearer {second['access_token']}"}).status_code == 200
