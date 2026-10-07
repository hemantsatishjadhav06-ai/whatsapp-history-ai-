"""Request-boundary regressions use disposable synthetic owners and no providers.

These exercise the assembled application, including admission before reads and SQL.
They do not establish distributed capacity, real OAuth or provider reply quality.
"""

import asyncio
import json

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import event, func, select

from assistant.config import Settings
from assistant.db import Base
from assistant.main import create_app
from assistant.models import LoginNonce, Workspace


@pytest.fixture
def hardened_app(tmp_path):
    applications = []

    def make(**overrides):
        values = {
            "_env_file": None,
            "environment": "test",
            "database_url": f"sqlite:///{tmp_path / f'boundary-{len(applications)}.db'}",
            "encryption_key": Fernet.generate_key().decode(),
            "allow_dev_auth": True,
            "session_secure": False,
            "model_provider": "mock",
            "google_client_id": "synthetic-configured-client",
            "request_limits_mode": "memory",
        }
        values.update(overrides)
        application = create_app(Settings(**values))
        Base.metadata.create_all(application.state.engine)
        applications.append(application)
        return application

    yield make
    for application in applications:
        application.state.engine.dispose()


@pytest.mark.parametrize("levels", [65, 2000])
def test_deep_unauthenticated_json_is_rejected_without_endpoint_sql(hardened_app, levels):
    application = hardened_app()
    statements = []
    event.listen(application.state.engine, "before_cursor_execute",
                 lambda *args: statements.append(True))
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.post("/v1/auth/google", content="[" * levels + "0" + "]" * levels,
                               headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert not statements, "Rejected JSON must not enter endpoint SQL dependencies"


def test_validation_responses_do_not_echo_credentials_or_extra_private_fields(hardened_app):
    application = hardened_app()
    marker = "synthetic-secret-that-must-not-return"
    body = {"credential": marker, "nonce": marker, **{f"private_{i}": marker for i in range(35)}}
    with TestClient(application) as client:
        response = client.post("/auth/google", json=body)
    assert response.status_code == 422
    assert marker not in response.text
    details = response.json()["detail"]
    assert 1 <= len(details) <= 20
    assert all("input" not in item and "ctx" not in item for item in details)


def test_ordinary_body_cap_is_not_relaxed_by_an_import_named_suffix(hardened_app):
    application = hardened_app()
    body = {"text": "x" * 65536}
    with TestClient(application) as client:
        ordinary = client.post("/auth/google", json=body)
        alias = client.post("/v1/auth/google", json=body)
        import_request = client.post("/v1/imports/preview", json=body)
        forged = client.post("/auth/google/imports", json=body)
    assert ordinary.status_code == alias.status_code == forged.status_code == 413
    assert import_request.status_code in {401, 422}


def test_chunked_body_enforces_limit_without_trusting_content_length(hardened_app):
    application = hardened_app(request_default_body_bytes=1024)

    async def run():
        yielded = []

        async def body():
            for index in range(4):
                yielded.append(index)
                yield b"x" * 700

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),
                                     base_url="http://audit.invalid") as client:
            response = await client.post("/auth/google", content=body())
        assert response.status_code == 413
        assert yielded == [0, 1], "Overflow must stop draining untrusted request chunks"

    asyncio.run(run())


def test_stalled_request_body_times_out_before_database_or_login_work(hardened_app):
    application = hardened_app(request_body_timeout_seconds=0.05)
    statements = []
    event.listen(application.state.engine, "before_cursor_execute",
                 lambda *args: statements.append(True))

    async def run():
        async def body():
            yield b'{"credential":"'
            await asyncio.Event().wait()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),
                                     base_url="http://audit.invalid") as client:
            response = await asyncio.wait_for(client.post("/auth/google", content=body()), 1)
        assert response.status_code == 408

    asyncio.run(run())
    assert not statements


def test_pause_has_reserved_admission_when_ordinary_request_is_stalled(hardened_app):
    application = hardened_app(request_max_inflight=2, request_control_reserve=1,
                               request_body_timeout_seconds=5)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),
                                     base_url="http://audit.invalid") as client:
            login = await client.post("/auth/dev", json={"email": "capacity-owner@example.invalid"})
            assert login.status_code == 200
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            workspace = await client.post("/workspaces", json={"name": "Synthetic pause", "timezone": "UTC"})
            assert workspace.status_code == 201
            identifier = workspace.json()["id"]
            started, release = asyncio.Event(), asyncio.Event()

            async def body():
                yield b'{"credential":"held"'
                started.set()
                await release.wait()
                yield b"}"

            held = asyncio.create_task(client.post("/auth/google", content=body()))
            try:
                await asyncio.wait_for(started.wait(), 1)
                ordinary = await client.get("/me")
                assert ordinary.status_code == 503
                assert int(ordinary.headers["Retry-After"]) >= 1
                assert (await client.get("/health/live")).status_code == 200
                pause = await client.post("/pause-all", params={"workspace_id": identifier})
                assert pause.status_code == 200
                with application.state.session_factory() as db:
                    assert db.get(Workspace, identifier).paused is True
            finally:
                release.set()
                await held

    asyncio.run(run())


def test_nonce_rate_bucket_covers_aliases_and_ignores_unsigned_forwarded_identity(hardened_app):
    application = hardened_app(request_rate_auth_issues=2)
    with TestClient(application) as client:
        for path, forwarded in [("/auth/nonce", "192.0.2.1"), ("/v1/auth/nonce", "192.0.2.2")]:
            client.cookies.clear()
            response = client.get(path, headers={"X-Forwarded-For": forwarded})
            assert response.status_code == 200
        client.cookies.clear()
        denied = client.get("/auth/nonce", headers={"X-Forwarded-For": "192.0.2.3"})
        assert denied.status_code == 429
        assert int(denied.headers["Retry-After"]) >= 1
    with application.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(LoginNonce)) == 2


def test_native_broker_start_shares_source_bound_auth_issue_limit(hardened_app):
    from assistant.mobile_auth import proof_challenge
    application = hardened_app(request_rate_auth_issues=2, google_client_secret="synthetic-secret",
                               google_native_redirect_uri="https://milo.example/api/auth/native/google/callback")
    with TestClient(application) as client:
        assert client.get("/v1/auth/nonce").status_code == 200
        body = {"platform": "android", "code_challenge": proof_challenge("a" * 43)}
        assert client.post("/auth/native/google/start", json=body).status_code == 200
        assert client.post("/v1/auth/native/google/start", json=body,
                           headers={"Authorization": "Bearer rotating-forged-session"}).status_code == 429


def test_native_callback_exchange_and_refresh_share_source_bound_auth_exchange_limit(hardened_app):
    application = hardened_app(request_rate_auth_exchanges=2, google_client_secret="synthetic-secret",
                               google_native_redirect_uri="https://milo.example/api/auth/native/google/callback")
    with TestClient(application) as client:
        assert client.get("/v1/auth/native/google/callback", params={"state": "a" * 43}).status_code == 401
        assert client.post("/auth/native/google/exchange", json={"handoff": "nh_" + "a" * 43,
                           "code_verifier": "a" * 43}).status_code == 401
        assert client.post("/v1/auth/native/refresh", json={"refresh_token": "nr_" + "a" * 43}).status_code == 429


def test_device_refresh_proof_revoke_retains_control_reserve_after_auth_and_api_exhaustion(hardened_app, monkeypatch):
    from assistant.mobile_models import NativeSession
    from test_mobile_auth import native_login
    application = hardened_app(request_rate_auth_exchanges=1, request_rate_global_api=2)
    with TestClient(application) as client:
        native = native_login(client, application, monkeypatch)
        for _ in range(2):
            assert client.get("/v1/me").status_code == 401
        assert client.get("/v1/me").status_code == 429
        assert client.post("/v1/auth/native/refresh", json={"refresh_token": native["refresh_token"]}).status_code == 429
        assert client.post("/v1/auth/native/revoke", json={"refresh_token": native["refresh_token"]}).status_code == 204
    with application.state.session_factory() as db:
        assert db.get(NativeSession, native["session_id"]) is None


@pytest.mark.parametrize("credential_header", ["Authorization", "Cookie"])
def test_rotating_unverified_session_values_cannot_bypass_source_admission(hardened_app, credential_header):
    application = hardened_app(request_rate_api=2, request_rate_source_api=2)
    with TestClient(application) as client:
        outcomes = []
        for index in range(3):
            value = f"Bearer na_synthetic_invalid_{index}" if credential_header == "Authorization" else (
                f"session_token=synthetic_invalid_{index}")
            outcomes.append(client.get("/me", headers={credential_header: value}).status_code)
    assert outcomes == [401, 401, 429]


def test_ordinary_global_rate_exhaustion_does_not_prevent_authorized_pause(hardened_app):
    application = hardened_app()
    with TestClient(application) as client:
        login = client.post("/auth/dev", json={"email": "rate-owner@example.invalid"})
        assert login.status_code == 200
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        workspace = client.post("/workspaces", json={"name": "Rate-limited fixture", "timezone": "UTC"})
        assert workspace.status_code == 201
        application.state.settings.request_rate_global_api = 2
        for _ in range(5):
            if client.get("/me").status_code == 429:
                break
        else:
            pytest.fail("The ordinary global rate budget did not exhaust")
        response = client.post("/pause-all", params={"workspace_id": workspace.json()["id"]})
        assert response.status_code == 200
        with application.state.session_factory() as db:
            assert db.get(Workspace, workspace.json()["id"]).paused is True


@pytest.mark.parametrize("invalid", [
    {"session_ttl_seconds": 0},
    {"session_ttl_seconds": 2592001},
    {"max_import_bytes": 0},
    {"max_import_records": 0},
    {"request_limits_mode": "off"},
    {"request_limits_mode": "memory"},
    {"allowed_origins": "http://public.invalid"},
    {"allowed_origins": "https://public.invalid/path"},
    {"allowed_origins": "https://user:pass@public.invalid"},
    {"allowed_origins": "*"},
])
def test_production_rejects_insecure_or_unbounded_request_settings(invalid):
    values = {"_env_file": None, "environment": "production",
              "database_url": "postgresql+psycopg://configuration-only",
              "encryption_key": Fernet.generate_key().decode(),
              "allowed_origins": "https://public.invalid", "request_limits_mode": "redis"}
    values.update(invalid)
    with pytest.raises(ValueError):
        Settings(**values).prepare()


def test_invalid_json_uses_generic_message_without_returning_private_bytes(hardened_app):
    application = hardened_app()
    marker = "synthetic-private-json-marker"
    with TestClient(application) as client:
        response = client.post("/auth/google", content=json.dumps({"private": marker})[:-1],
                               headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert marker not in response.text
