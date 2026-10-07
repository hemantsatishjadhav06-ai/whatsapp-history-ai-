"""Bounded authentication metadata expiry, independent of tenant content retention."""

from sqlalchemy import delete, or_, select

from .db import now
from .mobile_models import NativeLoginChallenge, NativeOAuthAttempt, NativeSession
from .models import LoginNonce, SessionRecord


def _batch_size(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1000:
        raise ValueError("Auth cleanup batch size must be between 1 and 1000")
    return value


def _delete_expired(db, model, predicate, limit):
    ids = list(db.scalars(select(model.id).where(predicate).order_by(model.expires_at, model.id)
                          .limit(limit).with_for_update(skip_locked=True)))
    if not ids:
        return 0, False
    result = db.execute(delete(model).where(model.id.in_(ids), predicate)
                        .execution_options(synchronize_session=False))
    return result.rowcount, len(ids) == limit


def sweep_browser_nonces(db, *, batch_size=100):
    """Request-path maintenance is confined to a bounded expired nonce page."""
    removed, _ = _delete_expired(db, LoginNonce, LoginNonce.expires_at <= now(), _batch_size(batch_size))
    return removed


def sweep_native_challenges(db, *, batch_size=100):
    """Creating a challenge never performs global session cleanup."""
    removed, _ = _delete_expired(db, NativeLoginChallenge, NativeLoginChallenge.expires_at <= now(),
                                _batch_size(batch_size))
    return removed


def sweep_native_oauth_attempts(db, *, batch_size=100):
    """Bound expired broker state without unbounded deletion on login requests."""
    removed, _ = _delete_expired(db, NativeOAuthAttempt, NativeOAuthAttempt.expires_at <= now(),
                                _batch_size(batch_size))
    return removed


def sweep_auth_metadata(db, batch_size=100):
    """Delete at most batch_size expired records across all auth tables.

    Each category receives part of the remaining budget so a backlog of browser
    sessions cannot starve native challenges or device session expiration.
    The service worker commits the transaction; owner retention routes do not
    invoke this global helper.
    """
    remaining = _batch_size(batch_size)
    instant = now()
    expired_native = (NativeSession.expires_at <= instant) & or_(
        NativeSession.refresh_token_hash.is_(None), NativeSession.refresh_token_hash == "",
        NativeSession.refresh_expires_at.is_(None), NativeSession.refresh_expires_at <= instant)
    categories = (
        ("browser_sessions_deleted", SessionRecord, SessionRecord.expires_at <= instant),
        ("browser_nonces_deleted", LoginNonce, LoginNonce.expires_at <= instant),
        ("native_challenges_deleted", NativeLoginChallenge, NativeLoginChallenge.expires_at <= instant),
        ("native_sessions_deleted", NativeSession, expired_native),
        ("native_oauth_attempts_deleted", NativeOAuthAttempt, NativeOAuthAttempt.expires_at <= instant),
    )
    result = {name: 0 for name, _, _ in categories}
    result["more_possible"] = False
    for ordinal, (name, model, predicate) in enumerate(categories):
        if remaining == 0:
            result["more_possible"] = True
            break
        categories_left = len(categories) - ordinal
        limit = (remaining + categories_left - 1) // categories_left
        removed, full_page = _delete_expired(db, model, predicate, limit)
        result[name] = removed
        remaining -= removed
        result["more_possible"] = result["more_possible"] or full_page
    result["total_deleted"] = sum(result[name] for name, _, _ in categories)
    return result
