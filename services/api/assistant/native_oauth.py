"""Google HTTPS authorization broker for installed iOS and Android clients.

Google receives only its registered HTTPS redirect. The app receives a short-
lived opaque handoff that is useless without its original S256 proof verifier.
Google codes, identity tokens and client secrets never enter app redirect URLs.
"""

import re
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
import requests
from sqlalchemy import delete, select, update
from starlette.responses import RedirectResponse

from .auth import LOGIN_NONCE_TTL_SECONDS, digest, equal_secret, verified_google_claims
from .db import aware, get_db, now
from .mobile_models import NativeOAuthAttempt
from typing import Literal


router = APIRouter(tags=["native Google HTTPS authentication"])
GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
HANDOFF_TTL_SECONDS = 60


class NativeGoogleStart(BaseModel):
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


class NativeGoogleExchange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    handoff: str = Field(min_length=35, max_length=256, pattern=r"^nh_[A-Za-z0-9_-]+$")
    code_verifier: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9._~-]+$")


def broker_configured(settings):
    return bool(settings.google_client_id and settings.google_client_secret and settings.google_native_redirect_uri)


def _broker_settings(request):
    settings = request.app.state.settings
    if not broker_configured(settings):
        raise HTTPException(503, "Native Google HTTPS sign-in is not configured")
    return settings


def _app_redirect(settings, **params):
    # Settings.prepare rejects every URI except this registered app destination.
    # Repeat at runtime so mutable settings never introduce an open redirect.
    if settings.google_native_app_redirect_uri != "milo://oauth":
        raise HTTPException(503, "Native app redirect is unavailable")
    return RedirectResponse(settings.google_native_app_redirect_uri + "?" + urlencode(params), status_code=303,
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def exchange_google_code(code, verifier, settings):
    """No SQL connection is held during this bounded, fixed-provider request."""
    try:
        with requests.Session() as transport:
            transport.trust_env = False
            with transport.post(GOOGLE_TOKEN_URL, data={"grant_type": "authorization_code", "code": code,
                                "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
                                "redirect_uri": settings.google_native_redirect_uri, "code_verifier": verifier},
                                timeout=(5, 10), allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise HTTPException(401, "Google authorization was not accepted")
                payload = bytearray()
                for chunk in response.iter_content(chunk_size=8192):
                    payload.extend(chunk)
                    if len(payload) > 65536:
                        raise HTTPException(503, "Google verification response is unavailable")
                import json
                result = json.loads(payload)
    except (requests.RequestException, ValueError):
        raise HTTPException(503, "Google verification service is temporarily unavailable") from None
    credential = result.get("id_token") if isinstance(result, dict) else None
    if not isinstance(credential, str) or not 1 <= len(credential) <= 16384:
        raise HTTPException(401, "Google did not return an identity credential")
    return credential


@router.post("/auth/native/google/start")
def start_native_google(body: NativeGoogleStart, request: Request, response: Response, db=Depends(get_db)):
    from .mobile_auth import _native_request, proof_challenge
    _native_request(request)
    settings = _broker_settings(request)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    upstream_verifier = secrets.token_urlsafe(48)
    expires = now() + timedelta(seconds=LOGIN_NONCE_TTL_SECONDS)
    from .auth_lifecycle import sweep_native_oauth_attempts
    sweep_native_oauth_attempts(db)
    db.add(NativeOAuthAttempt(state_hash=digest(state), code_challenge=body.code_challenge, platform=body.platform,
                             device_name=body.device_name, google_nonce=nonce, google_verifier=upstream_verifier,
                             expires_at=expires))
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    authorization = GOOGLE_AUTHORIZATION_URL + "?" + urlencode({
        "client_id": settings.google_client_id, "redirect_uri": settings.google_native_redirect_uri,
        "response_type": "code", "scope": "openid email profile", "state": state, "nonce": nonce,
        "code_challenge": proof_challenge(upstream_verifier), "code_challenge_method": "S256",
        "prompt": "select_account"})
    return {"authorization_url": authorization, "expires_at": expires.isoformat(), "proof_method": "S256",
            "app_redirect_uri": settings.google_native_app_redirect_uri}


@router.get("/auth/native/google/callback")
def native_google_callback(request: Request, state: str = Query(min_length=32, max_length=256),
                           code: str | None = Query(default=None, max_length=4096),
                           error: str | None = Query(default=None, max_length=100), db=Depends(get_db)):
    settings = _broker_settings(request)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", state):
        raise HTTPException(401, "Native Google sign-in state is invalid or expired")
    attempt = db.scalar(select(NativeOAuthAttempt).where(NativeOAuthAttempt.state_hash == digest(state),
                         NativeOAuthAttempt.status == "pending", NativeOAuthAttempt.expires_at > now()))
    if attempt is None:
        raise HTTPException(401, "Native Google sign-in state is invalid or expired")
    claimed = db.execute(update(NativeOAuthAttempt).where(NativeOAuthAttempt.id == attempt.id,
                         NativeOAuthAttempt.status == "pending", NativeOAuthAttempt.expires_at > now())
                         .values(status="processing").execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        db.rollback()
        raise HTTPException(401, "Native Google sign-in state was already consumed or expired")
    # Commit the one-use claim and release the SQL connection before Google I/O.
    db.commit()
    if error is not None or not code:
        db.execute(delete(NativeOAuthAttempt).where(NativeOAuthAttempt.id == attempt.id))
        db.commit()
        return _app_redirect(settings, error="access_denied" if error == "access_denied" else "sign_in_failed")
    try:
        credential = exchange_google_code(code, attempt.google_verifier, settings)
        claims = verified_google_claims(credential, settings.google_client_id, attempt.google_nonce)
    except HTTPException:
        db.execute(delete(NativeOAuthAttempt).where(NativeOAuthAttempt.id == attempt.id))
        db.commit()
        return _app_redirect(settings, error="sign_in_failed")
    handoff = "nh_" + secrets.token_urlsafe(32)
    expires = min(aware(attempt.expires_at), now() + timedelta(seconds=HANDOFF_TTL_SECONDS))
    identity = {key: claims[key] for key in ("sub", "email")}
    if isinstance(claims.get("name"), str):
        identity["name"] = claims["name"].strip()[:120]
    ready = db.execute(update(NativeOAuthAttempt).where(NativeOAuthAttempt.id == attempt.id,
                       NativeOAuthAttempt.status == "processing", NativeOAuthAttempt.expires_at > now())
                       .values(status="ready", handoff_hash=digest(handoff), verified_identity=identity,
                               google_verifier="", google_nonce="", expires_at=expires)
                       .execution_options(synchronize_session=False))
    if ready.rowcount != 1:
        db.rollback()
        return _app_redirect(settings, error="sign_in_failed")
    db.commit()
    return _app_redirect(settings, handoff=handoff)


@router.post("/auth/native/google/exchange")
def finish_native_google(body: NativeGoogleExchange, request: Request, response: Response, db=Depends(get_db)):
    from .mobile_auth import _native_request, create_native_session, proof_challenge
    _native_request(request)
    _broker_settings(request)
    attempt = db.scalar(select(NativeOAuthAttempt).where(NativeOAuthAttempt.handoff_hash == digest(body.handoff),
                         NativeOAuthAttempt.status == "ready", NativeOAuthAttempt.expires_at > now()))
    if attempt is None or not attempt.verified_identity:
        raise HTTPException(401, "Native sign-in handoff is invalid or expired")
    if not equal_secret(attempt.code_challenge, proof_challenge(body.code_verifier)):
        raise HTTPException(401, "Native login proof is invalid")
    consumed = db.execute(delete(NativeOAuthAttempt).where(NativeOAuthAttempt.id == attempt.id,
                          NativeOAuthAttempt.status == "ready", NativeOAuthAttempt.expires_at > now())
                          .execution_options(synchronize_session=False))
    if consumed.rowcount != 1:
        db.rollback()
        raise HTTPException(401, "Native sign-in handoff was already consumed or expired")
    return create_native_session(attempt.verified_identity, attempt.platform, attempt.device_name, request, response, db)
