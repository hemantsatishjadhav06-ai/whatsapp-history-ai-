"""Native Google exchange using a single-use challenge and revocable bearer.

Native tokens are separate from ambient browser cookies. Identity credentials,
bearer secrets and proof verifiers are never stored or included in activity logs.
"""

import base64
import hashlib
import secrets
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, or_, select, update

from .auth import (LOGIN_NONCE_TTL_SECONDS, digest, equal_secret, get_current_user,
                   identity_user, require_origin, user_payload, verified_google_claims)
from .db import aware, get_db, now
from .mobile_models import NativeLoginChallenge, NativeSession
from .models import SessionRecord, User

router = APIRouter(tags=["device authentication", "sessions"])


class NativeChallengeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Literal["ios", "android"]
    device_name: str = Field(default="My device", min_length=1, max_length=80)
    code_challenge: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]{43}$")

    @field_validator("device_name")
    @classmethod
    def nonblank_name(cls, value):
        if not value.strip():
            raise ValueError("Device name must not be blank")
        return value.strip()


class NativeLoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credential: str = Field(min_length=1, max_length=16384)
    nonce: str = Field(min_length=32, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")
    code_verifier: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9._~-]+$")


class NativeRefreshInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str = Field(min_length=35, max_length=256, pattern=r"^nr_[A-Za-z0-9_-]+$")


def proof_challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def _native_request(request):
    # Browser identity uses its cookie-bound, CSRF-protected exchange. Native
    # clients use a system-browser OAuth flow and possess their PKCE verifier.
    if request.headers.get("origin") is not None:
        raise HTTPException(403, "Use the browser sign-in flow for browser requests")


def native_current_user(request, db):
    authorization = request.headers.get("authorization", "")
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(401, "A native bearer session is required")
    token = authorization[len(prefix):]
    if not token.startswith("na_") or len(token) > 256 or any(char.isspace() for char in token):
        raise HTTPException(401, "Session is invalid or expired")
    session = db.scalar(select(NativeSession).where(NativeSession.token_hash == digest(token)))
    if session is None or aware(session.expires_at) <= now():
        raise HTTPException(401, "Session is invalid or expired")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        require_origin(request)
    user = db.get(User, session.user_id)
    if user is None:
        raise HTTPException(401, "Session is invalid")
    request.state.session_record, request.state.session_kind = session, "native"
    return user


@router.post("/auth/native/nonce")
def native_nonce(body: NativeChallengeInput, request: Request, response: Response, db=Depends(get_db)):
    _native_request(request)
    if not getattr(request.app.state.settings, f"google_{body.platform}_client_id"):
        raise HTTPException(503, "Google sign-in for this device platform is not configured")
    nonce = secrets.token_urlsafe(32)
    expires = now() + timedelta(seconds=LOGIN_NONCE_TTL_SECONDS)
    from .auth_lifecycle import sweep_native_challenges
    sweep_native_challenges(db)
    db.add(NativeLoginChallenge(nonce_hash=digest(nonce), code_challenge=body.code_challenge,
                               platform=body.platform, device_name=body.device_name, expires_at=expires))
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"nonce": nonce, "expires_at": expires.isoformat(), "proof_method": "S256"}


@router.post("/auth/native/login")
def native_login(body: NativeLoginInput, request: Request, response: Response, db=Depends(get_db)):
    _native_request(request)
    challenge = db.scalar(select(NativeLoginChallenge).where(NativeLoginChallenge.nonce_hash == digest(body.nonce)))
    if challenge is None or aware(challenge.expires_at) <= now():
        raise HTTPException(401, "Login nonce is invalid or expired")
    if not equal_secret(challenge.code_challenge, proof_challenge(body.code_verifier)):
        raise HTTPException(401, "Native login proof is invalid")
    client_id = getattr(request.app.state.settings, f"google_{challenge.platform}_client_id")
    # Provider verification must not occupy a SQL connection while fetching keys.
    # Final challenge consumption remains an expiry-checked atomic operation.
    db.commit()
    claims = verified_google_claims(body.credential, client_id, body.nonce)
    consumed = db.execute(delete(NativeLoginChallenge).where(NativeLoginChallenge.nonce_hash == digest(body.nonce),
                          NativeLoginChallenge.expires_at > now()).execution_options(synchronize_session=False))
    if consumed.rowcount != 1:
        raise HTTPException(401, "Login nonce was already consumed or expired")
    name = claims.get("name")
    name = (name.strip()[:120] if isinstance(name, str) else "Owner") or "Owner"
    user = identity_user(db, "google:" + claims["sub"], claims["email"].lower(), name)
    token = "na_" + secrets.token_urlsafe(32)
    refresh = "nr_" + secrets.token_urlsafe(32)
    expires = now() + timedelta(seconds=request.app.state.settings.native_session_ttl_seconds)
    refresh_expires = now() + timedelta(seconds=request.app.state.settings.native_refresh_ttl_seconds)
    session = NativeSession(user_id=user.id, token_hash=digest(token), platform=challenge.platform,
                            device_name=challenge.device_name, expires_at=expires,
                            refresh_token_hash=digest(refresh), refresh_expires_at=refresh_expires)
    db.add(session)
    db.flush()
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"access_token": token, "token_type": "Bearer", "expires_at": expires.isoformat(),
            "session_id": session.id, "user": user_payload(user), "refresh_supported": True,
            "refresh_token": refresh, "refresh_expires_at": refresh_expires.isoformat()}


@router.post("/auth/native/refresh")
def native_refresh(body: NativeRefreshInput, request: Request, response: Response, db=Depends(get_db)):
    _native_request(request)
    previous_hash = digest(body.refresh_token)
    session = db.scalar(select(NativeSession).where(NativeSession.refresh_token_hash == previous_hash,
                                                   NativeSession.refresh_expires_at > now()))
    if session is None:
        raise HTTPException(401, "Refresh session is invalid, revoked or expired")
    user = db.get(User, session.user_id)
    if user is None:
        raise HTTPException(401, "Refresh session is invalid")
    access = "na_" + secrets.token_urlsafe(32)
    refresh = "nr_" + secrets.token_urlsafe(32)
    # Rotations retain the original device-session deadline. A stolen refresh
    # token cannot perpetually extend a session or revive a revoked row.
    expires = min(now() + timedelta(seconds=request.app.state.settings.native_session_ttl_seconds),
                  aware(session.refresh_expires_at))
    changed = db.execute(update(NativeSession).where(NativeSession.id == session.id,
                         NativeSession.refresh_token_hash == previous_hash,
                         NativeSession.refresh_expires_at > now())
                         .values(token_hash=digest(access), refresh_token_hash=digest(refresh), expires_at=expires)
                         .execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(401, "Refresh token was already consumed or expired")
    result = {"access_token": access, "token_type": "Bearer", "expires_at": expires.isoformat(),
              "session_id": session.id, "user": user_payload(user), "refresh_supported": True,
              "refresh_token": refresh, "refresh_expires_at": aware(session.refresh_expires_at).isoformat()}
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/auth/native/logout", status_code=204)
def native_logout(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    if getattr(request.state, "session_kind", None) != "native":
        raise HTTPException(400, "A native bearer session is required")
    db.delete(request.state.session_record)
    db.commit()


@router.get("/auth/sessions")
def list_sessions(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    current = request.state.session_record
    result = []
    for model, kind in ((SessionRecord, "browser"), (NativeSession, "native")):
        available = or_(model.expires_at > now(), model.refresh_expires_at > now()) if kind == "native" else model.expires_at > now()
        for row in db.scalars(select(model).where(model.user_id == user.id, available)
                              .order_by(model.created_at.desc()).limit(100)):
            result.append({"id": row.id, "kind": kind, "platform": getattr(row, "platform", "browser"),
                           "device_name": getattr(row, "device_name", "Browser session"),
                           "created_at": aware(row.created_at).isoformat(), "expires_at": aware(row.expires_at).isoformat(),
                           "refresh_expires_at": aware(row.refresh_expires_at).isoformat()
                           if kind == "native" and row.refresh_expires_at else None,
                           "current": row.id == current.id and kind == getattr(request.state, "session_kind", "browser")})
    return {"sessions": result}


@router.delete("/auth/sessions/{session_id}", status_code=204)
def revoke_session(session_id: str, request: Request, response: Response, user=Depends(get_current_user), db=Depends(get_db)):
    row = db.scalar(select(NativeSession).where(NativeSession.id == session_id, NativeSession.user_id == user.id))
    if row is None:
        row = db.scalar(select(SessionRecord).where(SessionRecord.id == session_id, SessionRecord.user_id == user.id))
    if row is None:
        raise HTTPException(404, "Session not found")
    if isinstance(row, SessionRecord) and row.id == request.state.session_record.id:
        from .auth import SESSION_COOKIE
        response.delete_cookie(SESSION_COOKIE, path="/", secure=request.app.state.settings.session_secure,
                               httponly=True, samesite="lax")
    db.delete(row)
    db.commit()
