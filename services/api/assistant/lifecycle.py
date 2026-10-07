"""Bounded application retention sweeps; provider and backup lifecycle are separate."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select

from .access import audit, workspace_for
from .auth import get_current_user
from .core import invalidate, serialized_control
from .db import get_db, now
from .lifecycle_models import RetentionPolicy
from .models import AuditEvent, Conversation, Memory, Message, StyleProfile, Suppression

router = APIRouter(tags=["privacy"])


@router.get("/privacy/model-processing")
def model_processing(request: Request, user=Depends(get_current_user)):
    from urllib.parse import urlsplit
    from .intelligence import verified_model_pricing
    settings = request.app.state.settings
    configured = bool(settings.model_api_key and settings.model_name)
    pricing = verified_model_pricing(settings)
    return {"provider": settings.model_provider, "model_id": settings.model_name or None,
            "destination_host": urlsplit(settings.model_api_url).hostname if configured else None,
            "processing_region": settings.model_processing_region,
            "data_use_configuration": settings.model_data_use_configuration,
            "provider_configuration_verified": False,
            "model_budget_accounting": {"reservation": "Complete request UTF-8 byte count plus framing allowance; output limit 1000",
                                         "pricing_attested_for_configured_model": pricing is not None,
                                         "input_cost_microusd_per_million": pricing[0] if pricing else None,
                                         "output_cost_microusd_per_million": pricing[1] if pricing else None,
                                         "missing_usage": "Conservative reservation retained as uncertain",
                                         "unverified_cost_ceiling": "QUOTA_HELD before provider call"},
            "context_policy": "Current selected conversation and permitted evidence only",
            "plaintext_processing": settings.model_provider != "disabled",
            "automatic_provider_fallback": False}


@router.get("/integrations")
def integration_status(workspace_id: str, db=Depends(get_db), user=Depends(get_current_user)):
    workspace_for(db, user, workspace_id)
    from .models import Connector
    connectors = list(db.scalars(select(Connector).where(Connector.workspace_id == workspace_id)))
    return {"workspace_id": workspace_id, "integrations": [
        {"service": "whatsapp", "state": "configured_accounts" if connectors else "not_connected",
         "accounts": [{"connection_id": row.id, "provider": row.provider, "status": row.status,
                       "simulation": row.provider == "mock"} for row in connectors]},
        *[{"service": service, "state": "planned", "authorized": False, "service_scopes": []}
          for service in ("google_contacts", "calendar", "gmail", "social", "meetings")]],
        "identity_grants_service_access": False}


class RetentionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    expected_version: int | None = Field(default=None, ge=0)
    raw_days: int = Field(default=30, ge=1, le=3650)
    derived_days: int = Field(default=90, ge=1, le=3650)
    audit_days: int = Field(default=90, ge=1, le=3650)


def policy_for(db, workspace_id):
    row = db.scalar(select(RetentionPolicy).where(RetentionPolicy.workspace_id == workspace_id))
    if row is None:
        row = RetentionPolicy(workspace_id=workspace_id)
        db.add(row)
        db.flush()
    return row


def policy_view(row):
    return {"workspace_id": row.workspace_id, "raw_days": row.raw_days,
            "derived_days": row.derived_days, "audit_days": row.audit_days, "version": row.version,
            "backup_status": row.backup_status, "sweep_execution": "explicit_bounded_application_sweep",
            "tombstones": "retained_to_prevent_replay", "provider_data": "separate_provider_lifecycle"}


@router.get("/privacy/retention")
def get_retention(workspace_id: str, db=Depends(get_db), user=Depends(get_current_user)):
    workspace_for(db, user, workspace_id)
    row = policy_for(db, workspace_id)
    db.commit()
    return policy_view(row)


@router.put("/privacy/retention")
@serialized_control
def set_retention(body: RetentionInput, db=Depends(get_db), user=Depends(get_current_user)):
    workspace_for(db, user, body.workspace_id)
    existing = db.scalar(select(RetentionPolicy).where(RetentionPolicy.workspace_id == body.workspace_id))
    if body.expected_version is not None and body.expected_version != (existing.version if existing else 0):
        raise HTTPException(409, "Retention policy version changed")
    row = policy_for(db, body.workspace_id)
    row.raw_days, row.derived_days, row.audit_days = body.raw_days, body.derived_days, body.audit_days
    row.version += 1
    audit(db, body.workspace_id, user.id, "privacy.retention_changed", row.id, version=row.version)
    db.commit()
    return policy_view(row)


def sweep_retention(db, workspace_id, *, actor_id="retention-worker", batch_size=500):
    """Runs in a caller-owned transaction/submit guard; no external/network operations."""
    from .storage_authority import lock_workspace
    lock_workspace(db, workspace_id)
    from .actions import forget_action_sources
    from .jobs import forget_job_sources
    from .native import forget_native_sources
    from .native_models import MessageContext
    from .people import forget_people_sources
    from .tasks import invalidate_task_sources
    policy = policy_for(db, workspace_id)
    stamp = now()
    cutoff = stamp - timedelta(days=policy.raw_days)
    messages = list(db.scalars(select(Message).where(
        Message.workspace_id == workspace_id, Message.deleted.is_(False), Message.received_at < cutoff,
    ).order_by(Message.received_at, Message.id).limit(batch_size)))
    grouped = {}
    for message in messages:
        grouped.setdefault(message.conversation_id, []).append(message)
    for conversation_id, sources in grouped.items():
        conversation = db.get(Conversation, conversation_id)
        ids = [source.id for source in sources]
        db.add(Suppression(workspace_id=workspace_id, conversation_id=conversation_id,
                           content_hash="retention-expired", source_message_ids=ids))
        forget_action_sources(db, conversation_id, ids)
        forget_job_sources(db, conversation_id, ids)
        forget_native_sources(db, conversation_id, ids)
        for context in db.scalars(select(MessageContext).where(MessageContext.message_id.in_(ids))):
            context.sender_identity, context.participant_identity = {}, {}
        forget_people_sources(db, conversation_id, ids)
        for source in sources:
            source.text = ""
            source.deleted = True
            source.excluded_from_learning = True
            source.revision += 1
            invalidate_task_sources(db, conversation, source.id, forgotten=True)
        for memory in db.scalars(select(Memory).where(Memory.conversation_id == conversation_id)):
            if set(ids) & set(memory.source_message_ids):
                memory.text = ""
                memory.status = "invalidated"
                memory.version += 1
        for profile in db.scalars(select(StyleProfile).where(StyleProfile.conversation_id == conversation_id)):
            if set(ids) & set(profile.evidence_message_ids):
                profile.features, profile.evidence_message_ids, profile.owner_rules = {}, [], []
                profile.sample_count = 0
                profile.version += 1
                profile.sufficiency = "weak"
        invalidate(db, conversation, "retention_expired")
    old_memories = list(db.scalars(select(Memory).where(
        Memory.workspace_id == workspace_id, Memory.created_at < stamp - timedelta(days=policy.derived_days),
        Memory.status != "retention_expired",
    ).limit(batch_size)))
    for memory in old_memories:
        db.add(Suppression(workspace_id=workspace_id, conversation_id=memory.conversation_id,
                           content_hash="derived-retention-expired", source_message_ids=memory.source_message_ids))
        forget_action_sources(db, memory.conversation_id, memory.source_message_ids)
        forget_job_sources(db, memory.conversation_id, memory.source_message_ids)
        memory.text, memory.status = "", "retention_expired"
        memory.version += 1
        if memory.conversation_id not in grouped:
            invalidate(db, db.get(Conversation, memory.conversation_id), "derived_retention_expired")
    # Owner-authored draft/job/task text also has a published retention limit,
    # including records without message evidence. Preserve operation receipts.
    from uuid import NAMESPACE_URL, uuid5
    from .action_models import OutboundAction
    from .jobs_models import AuthorizedJob
    from .models import Draft, Outbox, ScheduledIntent, Task
    already_redacted = select(Outbox.aggregate_id).where(Outbox.kind == "privacy.retention_redacted")
    private_count = 0
    for model in (Draft, StyleProfile, Task, AuthorizedJob, OutboundAction):
        records = list(db.scalars(select(model).where(
            model.workspace_id == workspace_id,
            model.created_at < stamp - timedelta(days=policy.derived_days),
            model.id.not_in(already_redacted),
        ).order_by(model.created_at, model.id).limit(batch_size)))
        for record in records:
            if isinstance(record, Draft):
                record.text, record.missing_facts, record.evidence_message_ids = "", [], []
                record.approved_hash = record.approval_expires_at = None
                if record.status in {"needs_approval", "approved", "ready"}:
                    record.status = "cancelled"
                for intent in db.scalars(select(ScheduledIntent).where(
                    ScheduledIntent.draft_id == record.id, ScheduledIntent.status.in_({"scheduled", "held"}),
                )):
                    intent.status = "cancelled"
            elif isinstance(record, StyleProfile):
                record.features, record.owner_rules, record.evidence_message_ids = {}, [], []
                record.sample_count, record.sufficiency = 0, "weak"
                record.version += 1
                invalidate(db, db.get(Conversation, record.conversation_id), "style_retention_expired")
            elif isinstance(record, Task):
                record.title, record.source_message_ids, record.source_revision = "", [], {}
                record.status = "cancelled"
                record.version += 1
            elif isinstance(record, AuthorizedJob):
                record.purpose, record.content, record.evidence_message_ids, record.memory_ids = "", "", [], []
                record.status, record.hold_reason = "cancelled", "RETENTION_EXPIRED"
                record.version += 1
                for action in db.scalars(select(OutboundAction).where(OutboundAction.authorized_job_id == record.id)):
                    action.payload = {}
                    if action.status in {"ready", "held"}:
                        action.status, action.reason_code = "expired", "EXPIRED"
                        from .companion import settle_action_budget
                        settle_action_budget(db, workspace_id, action.id, "released")
            else:
                record.payload, record.evidence_message_ids = {}, []
                if record.status in {"ready", "held"}:
                    record.status, record.reason_code = "expired", "EXPIRED"
                    from .companion import settle_action_budget
                    settle_action_budget(db, workspace_id, record.id, "released")
            db.add(Outbox(id=str(uuid5(NAMESPACE_URL, f"retention:{model.__tablename__}:{record.id}")),
                          workspace_id=workspace_id, kind="privacy.retention_redacted", aggregate_id=record.id,
                          payload={"resource_kind": model.__tablename__}, status="recorded"))
            private_count += 1
    old_audit_ids = list(db.scalars(select(AuditEvent.id).where(
        AuditEvent.workspace_id == workspace_id, AuditEvent.created_at < stamp - timedelta(days=policy.audit_days),
    ).order_by(AuditEvent.created_at, AuditEvent.id).limit(batch_size)))
    removed_audits = db.execute(delete(AuditEvent).where(AuditEvent.id.in_(old_audit_ids))).rowcount
    audit(db, workspace_id, actor_id, "privacy.retention_swept", policy.id,
          raw_count=len(messages), derived_count=len(old_memories) + private_count, audit_count=removed_audits,
          policy_version=policy.version)
    return {"raw_records_redacted": len(messages), "derived_records_redacted": len(old_memories) + private_count,
            "audit_records_deleted": removed_audits,
            "more_possible": len(messages) == batch_size or len(old_memories) == batch_size
                             or private_count >= batch_size or len(old_audit_ids) == batch_size,
            "backup_status": policy.backup_status, "tombstones_retained": True}


@router.post("/privacy/retention/sweep")
@serialized_control
def run_retention(workspace_id: str, db=Depends(get_db), user=Depends(get_current_user)):
    workspace_for(db, user, workspace_id)
    result = sweep_retention(db, workspace_id, actor_id=user.id)
    db.commit()
    return result


def main():
    import argparse
    import time
    from .config import Settings
    from .db import make_database
    from .messaging import submit_guard
    from .models import Workspace
    from .auth_lifecycle import sweep_auth_metadata
    parser = argparse.ArgumentParser(description="Bounded retention worker, without provider or backup deletion")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--workspace-id")
    args = parser.parse_args()
    engine, factory = make_database(Settings().prepare())
    try:
        while True:
            with factory() as db:
                auth_cleanup = sweep_auth_metadata(db)
                db.commit()
            # Iterate in bounded pages rather than retaining all tenant IDs.
            last_id = ""
            count = 0
            while True:
                with factory() as db:
                    query = select(Workspace.id).where(Workspace.id > last_id).order_by(Workspace.id).limit(100)
                    if args.workspace_id:
                        query = query.where(Workspace.id == args.workspace_id)
                    ids = list(db.scalars(query))
                if not ids:
                    break
                for workspace_id in ids:
                    with submit_guard(workspace_id), factory() as db:
                        result = sweep_retention(db, workspace_id)
                        count += result["raw_records_redacted"] + result["derived_records_redacted"]
                        db.commit()
                last_id = ids[-1]
            print(f"Application retention sweep completed; redacted records: {count}", flush=True)
            print(f"Authentication metadata cleanup completed; expired records: {auth_cleanup['total_deleted']}", flush=True)
            if args.once:
                break
            time.sleep(30)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
