"""Deployment-owned authority for the single configured Business Cloud number.

Google's verified subject is stable account identity. A matching email, known
number ID, application session or workspace creation cannot claim provider keys.
"""

from fastapi import HTTPException

from .models import User, Workspace


def whatsapp_owner_authorized(settings, user: User | None) -> bool:
    subject = settings.whatsapp_authorized_owner_subject
    return bool(subject and user is not None and user.subject == f"google:{subject}")


def require_whatsapp_owner(settings, user: User | None) -> None:
    if not whatsapp_owner_authorized(settings, user):
        raise HTTPException(403, "WhatsApp Business access is unavailable for this owner")


def whatsapp_workspace_authorized(db, settings, workspace_id: str) -> bool:
    workspace = db.get(Workspace, workspace_id)
    owner = db.get(User, workspace.owner_id) if workspace is not None else None
    return whatsapp_owner_authorized(settings, owner)


def require_whatsapp_workspace(db, settings, workspace_id: str) -> None:
    if not whatsapp_workspace_authorized(db, settings, workspace_id):
        raise HTTPException(403, "WhatsApp Business access is unavailable for this owner")
