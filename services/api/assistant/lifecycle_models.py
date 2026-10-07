from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import Entity, Tenant


class RetentionPolicy(Entity, Tenant, Base):
    __tablename__ = "retention_policies"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), unique=True)
    raw_days: Mapped[int] = mapped_column(Integer, default=30)
    derived_days: Mapped[int] = mapped_column(Integer, default=90)
    audit_days: Mapped[int] = mapped_column(Integer, default=90)
    version: Mapped[int] = mapped_column(Integer, default=1)
    backup_status: Mapped[str] = mapped_column(String(40), default="not_configured")
