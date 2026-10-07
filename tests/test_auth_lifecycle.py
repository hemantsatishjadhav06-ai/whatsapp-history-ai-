"""Bounded auth cleanup preserves live sessions and refreshable device sessions."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, insert, select

from assistant.auth_lifecycle import sweep_auth_metadata, sweep_browser_nonces
from assistant.db import now, uid
from assistant.mobile_auth import proof_challenge
from assistant.mobile_models import NativeLoginChallenge, NativeSession
from assistant.models import LoginNonce, SessionRecord, Workspace


def _owner_id(db, chat):
    return db.get(Workspace, chat["workspace"]["id"]).owner_id


def _expired_row(model, owner_id):
    values = {"expires_at": now() - timedelta(minutes=1)}
    if model is SessionRecord:
        values.update(user_id=owner_id, token_hash=uid(), csrf_hash=uid())
    elif model is LoginNonce:
        values.update(nonce_hash=uid())
    elif model is NativeLoginChallenge:
        values.update(nonce_hash=uid(), code_challenge="a" * 43, platform="ios", device_name="Synthetic device")
    else:
        values.update(user_id=owner_id, token_hash=uid(), platform="ios", device_name="Synthetic device",
                      refresh_token_hash=uid(), refresh_expires_at=now() - timedelta(minutes=1))
    return model(**values)


def test_global_auth_sweep_is_one_bounded_fair_page_and_repeatable(app, owner_client, chat):
    models = (SessionRecord, LoginNonce, NativeLoginChallenge, NativeSession)
    with app.state.session_factory() as db:
        owner_id = _owner_id(db, chat)
        for model in models:
            db.add_all([_expired_row(model, owner_id) for _ in range(7)])
        db.commit()
        first = sweep_auth_metadata(db, batch_size=8)
        db.commit()
        assert first == {"browser_sessions_deleted": 2, "browser_nonces_deleted": 2,
                         "native_challenges_deleted": 2, "native_sessions_deleted": 2,
                         "more_possible": True, "total_deleted": 8}
        removed = first["total_deleted"]
        for _ in range(4):
            page = sweep_auth_metadata(db, batch_size=8)
            db.commit()
            assert page["total_deleted"] <= 8
            removed += page["total_deleted"]
        assert removed == 28
        assert sweep_auth_metadata(db, batch_size=8)["total_deleted"] == 0
        assert db.scalar(select(func.count()).select_from(SessionRecord)) == 1  # active owner browser session
        for model in models[1:]:
            assert db.scalar(select(func.count()).select_from(model)) == 0
    assert owner_client.get("/me").status_code == 200


def test_auth_sweep_preserves_refreshable_native_and_unexpired_access(app, owner_client, chat):
    instant = now()
    cases = {
        "refreshable": (instant - timedelta(seconds=1), uid(), instant + timedelta(days=1)),
        "access_valid_refresh_expired": (instant + timedelta(minutes=5), uid(), instant - timedelta(seconds=1)),
        "access_valid_no_refresh": (instant + timedelta(minutes=5), None, None),
        "missing_refresh_hash": (instant - timedelta(seconds=1), None, instant + timedelta(days=1)),
        "missing_refresh_expiry": (instant - timedelta(seconds=1), uid(), None),
        "fully_expired": (instant - timedelta(seconds=1), uid(), instant - timedelta(seconds=1)),
        "legacy_expired": (instant - timedelta(seconds=1), None, None),
    }
    with app.state.session_factory() as db:
        owner_id = _owner_id(db, chat)
        for label, (access, refresh_hash, refresh_expiry) in cases.items():
            db.add(NativeSession(user_id=owner_id, token_hash=uid(), device_name=label, platform="ios",
                                 expires_at=access, refresh_token_hash=refresh_hash, refresh_expires_at=refresh_expiry))
        db.commit()
        result = sweep_auth_metadata(db)
        db.commit()
        assert result["native_sessions_deleted"] == 4
        assert {row.device_name for row in db.scalars(select(NativeSession))} == {
            "refreshable", "access_valid_refresh_expired", "access_valid_no_refresh"}
        assert sweep_auth_metadata(db)["total_deleted"] == 0


@pytest.mark.parametrize("model", [LoginNonce, NativeLoginChallenge])
def test_nonce_requests_delete_only_one_expired_page(app, owner_client, chat, model):
    with app.state.session_factory() as db:
        owner_id = _owner_id(db, chat)
        db.add_all([_expired_row(model, owner_id) for _ in range(201)])
        valid = _expired_row(model, owner_id)
        valid.expires_at = now() + timedelta(minutes=10)
        db.add(valid)
        db.commit()
        valid_id = valid.id
    app.state.settings.google_ios_client_id = "synthetic-configured-ios-client"
    for expected in (101, 1):
        if model is LoginNonce:
            response = owner_client.get("/auth/nonce")
        else:
            response = owner_client.post("/auth/native/nonce", json={"platform": "ios",
                                         "code_challenge": proof_challenge("a" * 43)})
        assert response.status_code == 200, response.text
        with app.state.session_factory() as db:
            assert db.scalar(select(func.count()).select_from(model).where(model.expires_at <= now())) == expected
            assert db.get(model, valid_id) is not None
            other = NativeLoginChallenge if model is LoginNonce else LoginNonce
            assert db.scalar(select(func.count()).select_from(other)) == 0


def test_nonce_cleanup_remains_bounded_with_twenty_thousand_expired_records(app):
    instant = now()
    with app.state.session_factory() as db:
        db.execute(insert(LoginNonce), [{"nonce_hash": uid(), "expires_at": instant - timedelta(minutes=1)}
                                       for _ in range(20000)])
        db.commit()
        assert sweep_browser_nonces(db) == 100
        db.commit()
        assert db.scalar(select(func.count()).select_from(LoginNonce)) == 19900
        assert sweep_browser_nonces(db) == 100
        db.commit()
        assert db.scalar(select(func.count()).select_from(LoginNonce)) == 19800


def test_owner_content_retention_does_not_sweep_global_auth_metadata(app, owner_client, chat):
    with app.state.session_factory() as db:
        owner_id = _owner_id(db, chat)
        browser = _expired_row(SessionRecord, owner_id)
        challenge = _expired_row(NativeLoginChallenge, owner_id)
        db.add_all([browser, challenge])
        db.commit()
        browser_id, challenge_id = browser.id, challenge.id
    response = owner_client.post("/privacy/retention/sweep", params={"workspace_id": chat["workspace"]["id"]})
    assert response.status_code == 200, response.text
    with app.state.session_factory() as db:
        assert db.get(SessionRecord, browser_id) is not None
        assert db.get(NativeLoginChallenge, challenge_id) is not None


def test_internal_lifecycle_tick_sweeps_auth_once_and_commits(app, owner_client, chat, monkeypatch):
    from assistant import auth_lifecycle, config, db as database, lifecycle
    with app.state.session_factory() as db:
        owner_id = _owner_id(db, chat)
        expired = _expired_row(SessionRecord, owner_id)
        db.add(expired)
        db.commit()
        expired_id = expired.id
    calls = []
    original = auth_lifecycle.sweep_auth_metadata

    def cleanup(db, batch_size=100):
        calls.append(batch_size)
        return original(db, batch_size)

    engine = SimpleNamespace(dispose=lambda: None)
    monkeypatch.setattr(auth_lifecycle, "sweep_auth_metadata", cleanup)
    monkeypatch.setattr(config, "Settings", lambda: app.state.settings)
    monkeypatch.setattr(database, "make_database", lambda settings: (engine, app.state.session_factory))
    monkeypatch.setattr("sys.argv", ["lifecycle", "--once", "--workspace-id", chat["workspace"]["id"]])
    lifecycle.main()
    assert calls == [100]
    with app.state.session_factory() as db:
        assert db.get(SessionRecord, expired_id) is None


@pytest.mark.parametrize("batch_size", [0, -1, 1001, True, 1.5])
def test_auth_cleanup_rejects_unbounded_or_invalid_batches(app, batch_size):
    with app.state.session_factory() as db:
        with pytest.raises(ValueError, match="between 1 and 1000"):
            sweep_auth_metadata(db, batch_size=batch_size)
