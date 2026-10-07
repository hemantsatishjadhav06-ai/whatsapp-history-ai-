"""Verified identity exchange, short lived login nonces, and revocable sessions.

Google tokens are accepted only after verification by Google's published keys.
Neither Google credentials nor session secrets are stored in the database.
"""

import hashlib
import hmac
import math
import re
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from google.auth import exceptions as google_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import aware, get_db, now
from .models import LoginNonce, SessionRecord, User


router = APIRouter(tags=["authentication"])
SESSION_COOKIE = "session_token"
NONCE_COOKIE = "login_nonce"
LOGIN_NONCE_TTL_SECONDS = 300
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
EMAIL_PATTERN = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+\Z")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def equal_secret(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())


def require_origin(request: Request) -> None:
    """Block browser requests from origins outside the configured frontend set."""
    origin = request.headers.get("origin")
    if origin is None:
        return
    allowed = {item.strip().rstrip("/") for item in request.app.state.settings.allowed_origins.split(",")}
    if origin == "null" or origin.rstrip("/") not in allowed:
        raise HTTPException(403, "Origin is not allowed")


def user_payload(user: User) -> dict:
    return {"id": user.id, "email": user.email, "display_name": user.display_name}


def identity_user(db: Session, subject: str, email: str, display_name: str) -> User:
    user = db.scalar(select(User).where(User.subject == subject))
    if user is None:
        try:
            # Independent logins can race on the same subject. Preserve the
            # outer nonce transaction while the unique constraint chooses one.
            with db.begin_nested():
                user = User(subject=subject, email=email, display_name=display_name)
                db.add(user)
                db.flush()
        except IntegrityError:
            user = db.scalar(select(User).where(User.subject == subject))
            if user is None:
                raise
    user.email = email
    user.display_name = display_name
    db.flush()
    return user


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    if request.headers.get("authorization") is not None:
        # An invalid explicit bearer never falls back to an ambient browser cookie.
        from .mobile_auth import native_current_user
        return native_current_user(request, db)
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token or len(token) > 256:
        raise HTTPException(401, "Authentication required")
    session = db.scalar(select(SessionRecord).where(SessionRecord.token_hash == digest(token)))
    if session is None or aware(session.expires_at) <= now():
        raise HTTPException(401, "Session is invalid or expired")
    if request.method not in SAFE_METHODS:
        require_origin(request)
        csrf = request.headers.get("x-csrf-token", "")
        if not csrf or len(csrf) > 256 or not hmac.compare_digest(session.csrf_hash, digest(csrf)):
            raise HTTPException(403, "Valid X-CSRF-Token required")
    user = db.get(User, session.user_id)
    if user is None:
        raise HTTPException(401, "Session is invalid")
    request.state.session_record = session
    request.state.session_kind = "browser"
    return user


class GoogleLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credential: str = Field(min_length=1, max_length=16384)
    nonce: str = Field(min_length=32, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")


class DevelopmentLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=251)
    display_name: str = Field(default="Owner", min_length=1, max_length=120)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL_PATTERN.fullmatch(value):
            raise ValueError("Valid email required")
        return value

    @field_validator("display_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Display name cannot be empty")
        return value


def create_session(request: Request, response: Response, db: Session, user: User) -> dict:
    settings = request.app.state.settings
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    # Rotate an existing browser session on a successful identity exchange.
    previous = request.cookies.get(SESSION_COOKIE)
    if previous and len(previous) <= 256:
        db.execute(delete(SessionRecord).where(SessionRecord.token_hash == digest(previous)))
    db.add(SessionRecord(user_id=user.id, token_hash=digest(token), csrf_hash=digest(csrf),
                         expires_at=now() + timedelta(seconds=settings.session_ttl_seconds)))
    db.commit()
    response.set_cookie(SESSION_COOKIE, token, max_age=settings.session_ttl_seconds,
                        httponly=True, secure=settings.session_secure, samesite="lax", path="/")
    for nonce_path in ("/auth", "/v1/auth"):
        response.delete_cookie(NONCE_COOKIE, path=nonce_path, secure=settings.session_secure,
                               httponly=True, samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    return {"user": user_payload(user), "csrf_token": csrf}


@router.get("/auth/nonce")
def login_nonce(request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    require_origin(request)
    settings = request.app.state.settings
    nonce = secrets.token_urlsafe(32)
    previous = request.cookies.get(NONCE_COOKIE)
    db.execute(delete(LoginNonce).where(LoginNonce.expires_at <= now()).execution_options(synchronize_session=False))
    if previous and len(previous) <= 256:
        db.execute(delete(LoginNonce).where(LoginNonce.nonce_hash == digest(previous)))
    db.add(LoginNonce(nonce_hash=digest(nonce), expires_at=now() + timedelta(seconds=LOGIN_NONCE_TTL_SECONDS)))
    db.commit()
    response.set_cookie(NONCE_COOKIE, nonce, max_age=LOGIN_NONCE_TTL_SECONDS, httponly=True,
                        secure=settings.session_secure, samesite="lax",
                        path="/v1/auth" if request.url.path.startswith("/v1/") else "/auth")
    response.headers["Cache-Control"] = "no-store"
    return {"nonce": nonce}


def verified_google_claims(credential: str, client_id: str, nonce: str) -> dict:
    if not client_id:
        raise HTTPException(503, "Google sign-in is not configured")
    try:
        claims = google_id_token.verify_oauth2_token(credential, google_requests.Request(), client_id)
    except (ValueError, google_exceptions.GoogleAuthError):
        raise HTTPException(401, "Invalid Google credential") from None
    # Explicit claim checks keep the boundary fail-closed even with an injected
    # verifier in tests and require the browser's challenge to bind the ID token.
    expiry = claims.get("exp")
    audience = claims.get("aud")
    audience_valid = audience == client_id or (
        isinstance(audience, list) and client_id in audience
        and (len(audience) == 1 or claims.get("azp") == client_id)
    )
    subject = claims.get("sub")
    email = claims.get("email")
    token_nonce = claims.get("nonce")
    if (
        not isinstance(expiry, (int, float)) or isinstance(expiry, bool)
        or not math.isfinite(expiry) or expiry <= now().timestamp()
        or claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}
        or not audience_valid
        or (claims.get("azp") is not None and claims["azp"] != client_id)
        or not isinstance(subject, str) or not subject or len(subject) > 248
        or not isinstance(email, str) or len(email) > 320 or not EMAIL_PATTERN.fullmatch(email)
        or claims.get("email_verified") is not True
        or not isinstance(token_nonce, str) or not equal_secret(token_nonce, nonce)
    ):
        raise HTTPException(401, "Invalid Google credential claims")
    return claims


@router.post("/auth/google")
def google_login(body: GoogleLogin, request: Request, response: Response,
                 db: Session = Depends(get_db)) -> dict:
    require_origin(request)
    cookie_nonce = request.cookies.get(NONCE_COOKIE, "")
    header_nonce = request.headers.get("x-csrf-token", "")
    if (not cookie_nonce or len(cookie_nonce) > 256 or len(header_nonce) > 256
            or not equal_secret(cookie_nonce, body.nonce)
            or not equal_secret(header_nonce, body.nonce)):
        raise HTTPException(403, "Matching login nonce cookie, body, and X-CSRF-Token required")
    pending = db.scalar(select(LoginNonce).where(LoginNonce.nonce_hash == digest(body.nonce)))
    if pending is None or aware(pending.expires_at) <= now():
        raise HTTPException(401, "Login nonce is invalid or expired")
    claims = verified_google_claims(body.credential, request.app.state.settings.google_client_id, body.nonce)
    consumed = db.execute(delete(LoginNonce).where(LoginNonce.nonce_hash == digest(body.nonce),
                                                   LoginNonce.expires_at > now())
                          .execution_options(synchronize_session=False))
    if consumed.rowcount != 1:
        raise HTTPException(401, "Login nonce was already consumed or expired")
    subject = "google:" + claims["sub"]
    display_name = claims.get("name")
    display_name = display_name.strip()[:120] if isinstance(display_name, str) else "Owner"
    display_name = display_name or "Owner"
    user = identity_user(db, subject, claims["email"].lower(), display_name)
    return create_session(request, response, db, user)


@router.post("/auth/dev")
def development_login(body: DevelopmentLogin, request: Request, response: Response,
                      db: Session = Depends(get_db)) -> dict:
    settings = request.app.state.settings
    if not settings.allow_dev_auth or settings.environment not in {"development", "test"}:
        raise HTTPException(404, "Development authentication is unavailable")
    require_origin(request)
    subject = "dev:" + body.email
    user = identity_user(db, subject, body.email, body.display_name)
    return create_session(request, response, db, user)


@router.get("/me")
def me(response: Response, user: User = Depends(get_current_user)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return user_payload(user)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, user: User = Depends(get_current_user),
           db: Session = Depends(get_db)) -> None:
    db.delete(request.state.session_record)
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/", secure=request.app.state.settings.session_secure,
                           httponly=True, samesite="lax")
    response.headers["Cache-Control"] = "no-store"
