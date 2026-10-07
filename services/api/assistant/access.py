from fastapi import HTTPException
from sqlalchemy import select

from .db import aware, now
from .models import AuditEvent, Conversation, Permission, Workspace


def workspace_for(db, user, workspace_id):
    row = db.scalar(select(Workspace).where(Workspace.id == workspace_id, Workspace.owner_id == user.id))
    if row is None:
        raise HTTPException(404, "Workspace not found")
    return row


def conversation_for(db, user, conversation_id):
    row = db.get(Conversation, conversation_id)
    if row is None:
        raise HTTPException(404, "Conversation not found")
    workspace_for(db, user, row.workspace_id)
    return row


def permission_for(db, conversation, capability):
    row = db.scalar(select(Permission).where(Permission.conversation_id == conversation.id,
                                            Permission.workspace_id == conversation.workspace_id))
    if row is None or not getattr(row, capability) or (row.expires_at and aware(row.expires_at) <= now()):
        raise HTTPException(403, f"Conversation {capability} permission required")
    return row


def audit(db, workspace_id, actor_id, action, resource_id, **details):
    db.add(AuditEvent(workspace_id=workspace_id, actor_id=actor_id, action=action,
                      resource_id=resource_id, details=details))
