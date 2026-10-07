"""Separate device sessions and single-use native identity challenges."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, EncryptedJSON, EncryptedText
from .models import Entity


class NativeLoginChallenge(Entity, Base):
    __tablename__ = "native_login_challenges"
    nonce_hash: Mapped[str] = mapped_column(String(64), unique=True)
    code_challenge: Mapped[str] = mapped_column(String(43))
    platform: Mapped[str] = mapped_column(String(10))
    device_name: Mapped[str] = mapped_column(EncryptedText)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class NativeSession(Entity, Base):
    __tablename__ = "native_sessions"
    __table_args__ = (UniqueConstraint("refresh_token_hash", name="uq_native_session_refresh"),)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    refresh_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    platform: Mapped[str] = mapped_column(String(10))
    device_name: Mapped[str] = mapped_column(EncryptedText)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class NativeOAuthAttempt(Entity, Base):
    """Short-lived server Google flow and a one-use app proof-bound handoff."""
    __tablename__ = "native_oauth_attempts"
    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    code_challenge: Mapped[str] = mapped_column(String(43))
    platform: Mapped[str] = mapped_column(String(10))
    device_name: Mapped[str] = mapped_column(EncryptedText)
    google_nonce: Mapped[str] = mapped_column(EncryptedText)
    google_verifier: Mapped[str] = mapped_column(EncryptedText)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    handoff_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    verified_identity: Mapped[dict | None] = mapped_column(EncryptedJSON, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
