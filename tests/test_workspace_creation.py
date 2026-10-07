"""Retry/owner isolation for workspace creation; no external account calls."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from assistant.core import WorkspaceInput, create_workspace
from assistant.models import AuditEvent, Workspace
from conftest import login


BODY = {"name": "My space", "timezone": "UTC"}
HEADERS = {"Idempotency-Key": "workspace-creation-retry-1"}


def test_workspace_retry_returns_committed_workspace_once(owner_client, db):
    first = owner_client.post("/workspaces", json=BODY, headers=HEADERS)
    assert first.status_code == 201
    # Simulate the user not receiving the first response and retrying its key.
    second = owner_client.post("/v1/workspaces", json=BODY, headers=HEADERS)
    assert second.status_code == 201 and second.json() == first.json()
    assert db.scalar(select(func.count()).select_from(Workspace)) == 1
    assert db.scalar(select(func.count()).select_from(AuditEvent).where(
        AuditEvent.action == "workspace.created")) == 1
    workspace = db.scalar(select(Workspace))
    assert workspace.creation_key_hash != HEADERS["Idempotency-Key"]
    assert "creation_key_hash" not in second.json()


def test_creation_key_cannot_change_request_or_cross_owner(owner_client, db):
    first = owner_client.post("/workspaces", json=BODY, headers=HEADERS).json()
    changed = owner_client.post("/workspaces", json={**BODY, "timezone": "Asia/Kolkata"}, headers=HEADERS)
    assert changed.status_code == 409
    assert db.scalar(select(func.count()).select_from(Workspace)) == 1
    login(owner_client, "another-owner@example.test")
    other = owner_client.post("/workspaces", json=BODY, headers=HEADERS)
    assert other.status_code == 201 and other.json()["id"] != first["id"]


@pytest.mark.parametrize("key", ["short", "x" * 129, "invalid key", "a/b?secret", "a\tb-secret"])
def test_invalid_creation_key_is_rejected_without_write(owner_client, db, key):
    response = owner_client.post("/workspaces", json=BODY, headers={"Idempotency-Key": key})
    assert response.status_code == 422
    assert db.scalar(select(func.count()).select_from(Workspace)) == 0


def test_requests_without_a_key_still_create_distinct_workspaces(owner_client):
    first = owner_client.post("/workspaces", json=BODY)
    second = owner_client.post("/workspaces", json=BODY)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


def test_parallel_creation_unique_constraint_arbitrates_actual_postgres(app, owner_client, db):
    if db.get_bind().dialect.name != "postgresql":
        pytest.skip("Actual PostgreSQL concurrent unique-insert arbitration")
    user_id = owner_client.get("/me").json()["id"]
    barrier = Barrier(2)

    def create():
        with app.state.session_factory() as session:
            scalar = session.scalar
            first = True

            def synchronized_scalar(*args, **kwargs):
                nonlocal first
                result = scalar(*args, **kwargs)
                if first:
                    first = False
                    assert result is None
                    barrier.wait(timeout=10)
                return result

            session.scalar = synchronized_scalar
            return create_workspace(WorkspaceInput(**BODY), user=SimpleNamespace(id=user_id),
                                    db=session, idempotency_key=HEADERS["Idempotency-Key"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: create(), range(2)))
    assert results[0] == results[1]
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Workspace)) == 1
    assert db.scalar(select(func.count()).select_from(AuditEvent).where(
        AuditEvent.action == "workspace.created")) == 1
