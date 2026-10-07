"""Assembled native broker tests use synthetic Google claims, not real OAuth."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import func, select, text
import pytest

from assistant import auth, native_oauth
from assistant.config import Settings
from assistant.db import now
from assistant.mobile_auth import proof_challenge
from assistant.mobile_models import NativeOAuthAttempt, NativeSession


VERIFIER = "a" * 43
CALLBACK = "https://milo.example/api/auth/native/google/callback"


def configured(app):
    app.state.settings.google_client_id = "synthetic-web-client"
    app.state.settings.google_client_secret = "synthetic-client-secret"
    app.state.settings.google_native_redirect_uri = CALLBACK


def start(client, app, platform="android"):
    configured(app)
    result = client.post("/v1/auth/native/google/start", json={"platform": platform,
                        "device_name": "Synthetic owner device", "code_challenge": proof_challenge(VERIFIER)})
    assert result.status_code == 200, result.text
    parsed = urlsplit(result.json()["authorization_url"])
    params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    assert parsed.scheme == "https" and parsed.netloc == "accounts.google.com"
    assert params["redirect_uri"] == CALLBACK and params["code_challenge_method"] == "S256"
    assert result.json()["app_redirect_uri"] == "milo://oauth"
    return params


def provider(app, monkeypatch, params, *, verify_pool_release=True, **overrides):
    def exchange(code, verifier, settings):
        assert code == "synthetic-google-code"
        assert proof_challenge(verifier) == params["code_challenge"]
        assert settings.google_native_redirect_uri == CALLBACK
        if verify_pool_release:
            assert app.state.engine.pool.checkedout() == 0, "Google networking must release SQL first"
        return "synthetic-google-id-token"

    claims = {"sub": "synthetic-native-owner", "email": "owner@example.test", "email_verified": True,
              "aud": "synthetic-web-client", "iss": "https://accounts.google.com", "nonce": params["nonce"],
              "exp": int((now() + timedelta(minutes=10)).timestamp()), "name": "Synthetic owner"}
    claims.update(overrides)
    monkeypatch.setattr(native_oauth, "exchange_google_code", exchange)
    monkeypatch.setattr(auth.google_id_token, "verify_oauth2_token", lambda *args: claims)


def callback(client, params):
    return client.get("/v1/auth/native/google/callback", params={"state": params["state"],
                     "code": "synthetic-google-code"}, follow_redirects=False)


def handoff(response):
    assert response.status_code == 303, response.text
    parts = urlsplit(response.headers["location"])
    assert parts.scheme == "milo" and parts.netloc == "oauth"
    params = parse_qs(parts.query)
    assert set(params) == {"handoff"}
    return params["handoff"][0]


@pytest.mark.parametrize("platform", ["ios", "android"])
def test_https_broker_creates_native_session_without_google_custom_scheme_redirect(client, app, monkeypatch, platform):
    params = start(client, app, platform)
    provider(app, monkeypatch, params)
    response = callback(client, params)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "synthetic-google" not in response.headers["location"] and "owner@example" not in response.headers["location"]
    exchange = client.post("/v1/auth/native/google/exchange", json={"handoff": handoff(response), "code_verifier": VERIFIER})
    assert exchange.status_code == 200, exchange.text
    session = exchange.json()
    assert not client.cookies.get("session_token")
    assert client.get("/v1/me", headers={"Authorization": "Bearer " + session["access_token"]}).json() == session["user"]
    assert client.get("/v1/ui/bootstrap", headers={"Authorization": "Bearer " + session["access_token"]}).status_code == 200
    with app.state.session_factory() as db:
        assert db.scalar(select(NativeOAuthAttempt)) is None
        assert db.scalar(select(NativeSession)).platform == platform


def test_broker_pending_metadata_and_verified_identity_are_encrypted(client, app, monkeypatch):
    params = start(client, app)
    with app.state.session_factory() as db:
        raw = db.execute(text("SELECT state_hash, google_nonce, google_verifier, device_name FROM native_oauth_attempts")).one()
        assert raw.state_hash == auth.digest(params["state"])
        assert params["state"] not in str(raw) and params["nonce"] not in str(raw)
        assert "Synthetic owner device" not in raw.device_name
        original = db.scalar(select(NativeOAuthAttempt))
        assert original.google_nonce == params["nonce"]
        assert proof_challenge(original.google_verifier) == params["code_challenge"]
    provider(app, monkeypatch, params)
    value = handoff(callback(client, params))
    with app.state.session_factory() as db:
        raw = db.execute(text("SELECT handoff_hash, verified_identity, google_nonce, google_verifier FROM native_oauth_attempts")).one()
        assert raw.handoff_hash == auth.digest(value) and value not in str(raw)
        assert "owner@example.test" not in raw.verified_identity and "synthetic-native-owner" not in raw.verified_identity
        row = db.scalar(select(NativeOAuthAttempt))
        assert row.google_nonce == row.google_verifier == ""
        assert 0 < (row.expires_at.replace(tzinfo=now().tzinfo) - now()).total_seconds() <= 60


def test_handoff_requires_original_app_proof_and_is_single_use(client, app, monkeypatch):
    params = start(client, app)
    provider(app, monkeypatch, params)
    value = handoff(callback(client, params))
    body = {"handoff": value, "code_verifier": VERIFIER}
    assert client.post("/v1/auth/native/google/exchange", json={**body, "code_verifier": "b" * 43}).status_code == 401
    assert client.post("/v1/auth/native/google/exchange", json=body).status_code == 200
    assert client.post("/v1/auth/native/google/exchange", json=body).status_code == 401
    assert callback(client, params).status_code == 401


def test_concurrent_callbacks_have_one_winner(client, app, monkeypatch):
    params = start(client, app)
    provider(app, monkeypatch, params, verify_pool_release=False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: callback(client, params), range(2)))
    assert sorted(response.status_code for response in responses) == [303, 401]


def test_concurrent_handoff_exchanges_create_one_session(client, app, monkeypatch):
    params = start(client, app)
    provider(app, monkeypatch, params)
    value = handoff(callback(client, params))
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post("/v1/auth/native/google/exchange",
                     json={"handoff": value, "code_verifier": VERIFIER}), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 401]
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(NativeSession)) == 1


@pytest.mark.parametrize("overrides", [{"nonce": "forged"}, {"aud": "native-wrong-client"},
                         {"iss": "https://attacker.test"}, {"exp": 0}, {"email_verified": False}])
def test_broker_rejects_invalid_google_identity_without_redirecting_secrets(client, app, monkeypatch, overrides):
    params = start(client, app)
    provider(app, monkeypatch, params, **overrides)
    result = callback(client, params)
    assert result.status_code == 303 and result.headers["location"] == "milo://oauth?error=sign_in_failed"
    assert "synthetic-google" not in result.text
    assert callback(client, params).status_code == 401
    with app.state.session_factory() as db:
        assert db.scalar(select(NativeOAuthAttempt)) is None and db.scalar(select(NativeSession)) is None


def test_google_cancel_consumes_attempt_without_session(client, app):
    params = start(client, app)
    result = client.get("/v1/auth/native/google/callback", params={"state": params["state"], "error": "access_denied"},
                        follow_redirects=False)
    assert result.headers["location"] == "milo://oauth?error=access_denied"
    assert callback(client, params).status_code == 401
    with app.state.session_factory() as db:
        assert db.scalar(select(NativeSession)) is None


def test_expired_pending_and_handoff_attempts_fail_closed(client, app, monkeypatch):
    params = start(client, app)
    provider(app, monkeypatch, params)
    with app.state.session_factory() as db:
        db.scalar(select(NativeOAuthAttempt)).expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert callback(client, params).status_code == 401
    params = start(client, app)
    provider(app, monkeypatch, params)
    value = handoff(callback(client, params))
    with app.state.session_factory() as db:
        db.scalar(select(NativeOAuthAttempt)).expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert client.post("/v1/auth/native/google/exchange", json={"handoff": value, "code_verifier": VERIFIER}).status_code == 401


def test_browser_origin_and_unconfigured_native_broker_fail_closed(client, app):
    body = {"platform": "android", "code_challenge": proof_challenge(VERIFIER)}
    assert client.post("/v1/auth/native/google/start", json=body).status_code == 503
    configured(app)
    assert client.post("/v1/auth/native/google/start", json=body, headers={"Origin": "https://milo.example"}).status_code == 403
    assert client.post("/v1/auth/native/google/exchange", json={"handoff": "nh_" + "a" * 43,
                       "code_verifier": VERIFIER}, headers={"Origin": "https://milo.example"}).status_code == 403


def test_public_config_reports_actual_broker_not_unrelated_platform_id_and_hides_secret(client, app):
    app.state.settings.google_ios_client_id = "synthetic-ios-client"
    config = client.get("/v1/auth/config").json()["google"]
    assert config["native_configured"] is config["native_broker_configured"] is False
    configured(app)
    response = client.get("/v1/auth/config")
    config = response.json()["google"]
    assert config["native_platforms"] == {"ios": True, "android": True}
    assert config["native_broker_live_verified"] is False
    assert "synthetic-client-secret" not in response.text


@pytest.mark.parametrize("callback_uri", ["http://milo.example/api/auth/native/google/callback",
                         "https://milo.example/elsewhere", "https://owner:secret@milo.example/api/auth/native/google/callback",
                         "https://milo.example/api/auth/native/google/callback?next=attacker"])
def test_broker_configuration_rejects_unsafe_callback_urls(callback_uri):
    with pytest.raises(ValueError, match="callback"):
        Settings(_env_file=None, google_native_redirect_uri=callback_uri).prepare()


def test_refresh_proof_revokes_only_its_device_even_after_access_expiry(client, app, monkeypatch):
    from test_mobile_auth import native_login
    first = native_login(client, app, monkeypatch, subject="owner-one")
    second = native_login(client, app, monkeypatch, subject="owner-two")
    with app.state.session_factory() as db:
        db.get(NativeSession, first["session_id"]).expires_at = now() - timedelta(seconds=1)
        db.commit()
    body = {"refresh_token": first["refresh_token"]}
    assert client.post("/v1/auth/native/revoke", json=body, headers={"Origin": "https://milo.example"}).status_code == 403
    assert client.post("/v1/auth/native/revoke", json=body).status_code == 204
    assert client.post("/v1/auth/native/revoke", json=body).status_code == 204
    assert client.post("/v1/auth/native/refresh", json=body).status_code == 401
    assert client.get("/v1/me", headers={"Authorization": "Bearer " + second["access_token"]}).status_code == 200


def test_same_verified_google_identity_maps_native_and_browser_to_one_owner(client, app, monkeypatch):
    params = start(client, app)
    provider(app, monkeypatch, params)
    session = client.post("/v1/auth/native/google/exchange", json={"handoff": handoff(callback(client, params)),
                          "code_verifier": VERIFIER}).json()
    workspace = client.post("/v1/workspaces", json={"name": "Native owner space", "timezone": "UTC"},
                            headers={"Authorization": "Bearer " + session["access_token"]}).json()
    nonce = client.get("/v1/auth/nonce").json()["nonce"]
    claims = {"sub": "synthetic-native-owner", "email": "owner@example.test", "email_verified": True,
              "aud": "synthetic-web-client", "iss": "https://accounts.google.com", "nonce": nonce,
              "exp": int((now() + timedelta(minutes=10)).timestamp())}
    monkeypatch.setattr(auth.google_id_token, "verify_oauth2_token", lambda *args: claims)
    browser = client.post("/v1/auth/google", json={"credential": "synthetic-browser-token", "nonce": nonce},
                          headers={"X-CSRF-Token": nonce})
    assert browser.status_code == 200 and browser.json()["user"]["id"] == session["user"]["id"]
    assert client.get("/v1/ui/bootstrap").json()["workspace"]["id"] == workspace["id"]


def test_google_code_transport_uses_only_fixed_origin_and_private_body_with_bounded_response(app, monkeypatch):
    import json
    from fastapi import HTTPException
    configured(app)
    calls = []
    body = {"id_token": "synthetic-identity-token", "access_token": "synthetic-upstream-access"}

    class Response:
        status_code = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def iter_content(self, chunk_size):
            yield json.dumps(body).encode()

    class Transport:
        trust_env = True
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, **kwargs):
            assert self.trust_env is False
            calls.append((url, kwargs))
            return Response()

    monkeypatch.setattr(native_oauth.requests, "Session", Transport)
    assert native_oauth.exchange_google_code("synthetic-code", VERIFIER, app.state.settings) == body["id_token"]
    url, options = calls[0]
    assert url == "https://oauth2.googleapis.com/token" and "?" not in url
    assert options["allow_redirects"] is False and options["stream"] is True and options["timeout"] == (5, 10)
    assert options["data"]["client_secret"] == "synthetic-client-secret" and options["data"]["code_verifier"] == VERIFIER
    body.clear()
    body["private"] = "synthetic-upstream-private-error"
    with pytest.raises(HTTPException) as error:
        native_oauth.exchange_google_code("synthetic-code", VERIFIER, app.state.settings)
    assert error.value.status_code == 401 and "synthetic" not in error.value.detail
