"""Short PostgreSQL transaction authority for one workspace's durable decisions.

The lock orders owner controls, source changes, quotas and submission claims
across API/worker processes. It ends at commit/rollback, before any transport or
model call. It cannot recall a submission that already passed its final check.
"""

from hashlib import sha256

from sqlalchemy import text


SUBMISSION_RECOVERY_SECONDS = 120


def lock_workspace(db, workspace_id):
    if db.get_bind().dialect.name == "postgresql":
        key = int.from_bytes(sha256(f"milo-authority:{workspace_id}".encode()).digest()[:8],
                             byteorder="big", signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
