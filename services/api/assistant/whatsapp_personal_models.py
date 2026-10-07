"""Personal WhatsApp session metadata; Signal credentials stay Node-owned."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import Entity, Tenant


class PersonalWhatsAppSession(Entity, Tenant, Base):
    __tablename__ = "whatsapp_personal_sessions"
    __table_args__ = (UniqueConstraint("workspace_id", name="uq_personal_session_workspace"),
                      UniqueConstraint("connector_id", name="uq_personal_session_connector"))
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="starting")
    connector_fence: Mapped[int] = mapped_column(Integer, default=1)
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_health_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)


class PersonalAuthKey(Base):
    """Opaque AES-GCM envelopes written and decrypted only by the session service."""
    __tablename__ = "wa_personal_auth_keys"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"), primary_key=True)
    key_type: Mapped[str] = mapped_column(String(80), primary_key=True)
    key_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PersonalAccountAlias(Base):
    """Content-free PN/LID ownership tombstones prevent alias-based account reuse."""
    __tablename__ = "wa_personal_account_aliases"
    alias_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"), index=True)
