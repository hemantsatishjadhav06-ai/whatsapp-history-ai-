"""Exercise the scoped page access path and reversible migration on real rows."""

from datetime import timedelta
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text

from assistant.db import now
from assistant.models import Message


def test_scoped_message_index_migration_preserves_live_page(app, chat, owner_client):
    workspace = chat["workspace"]["id"]
    conversation = chat["conversation"]["id"]
    connector = chat["connector"]["id"]
    stamp = now()
    with app.state.session_factory() as db:
        db.add_all(
            Message(
                id=f"message-{index:04d}",
                workspace_id=workspace,
                connector_id=connector,
                conversation_id=conversation,
                provider_message_id=f"synthetic-page-{index}",
                sender_id="synthetic-peer",
                direction="inbound",
                origin="history",
                author_kind="contact_human",
                text="Synthetic retained example",
                provider_timestamp=stamp - timedelta(seconds=index // 100),
                deleted=index < 50,
            )
            for index in range(2000)
        )
        db.commit()

    path = (
        Path(__file__).resolve().parents[1]
        / "db/migrations/versions/ae912f73c804_scoped_message_page_index.py"
    )
    spec = importlib.util.spec_from_file_location("message_page_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    query = select(Message.id).where(
        Message.workspace_id == workspace, Message.conversation_id == conversation, Message.deleted.is_(False)
    )
    query = query.order_by(Message.provider_timestamp.desc(), Message.id.desc()).limit(50)
    expected = [f"message-{index:04d}" for index in range(99, 49, -1)]
    with app.state.engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            assert list(connection.scalars(query)) == expected
            migration.upgrade()
            assert list(connection.scalars(query)) == expected
            if connection.dialect.name == "sqlite":
                compiled = query.compile(dialect=connection.dialect, compile_kwargs={"literal_binds": True})
                plan = connection.execute(text("EXPLAIN QUERY PLAN " + str(compiled))).all()
                assert any("ix_messages_read_page" in str(row) for row in plan), plan
                assert not any("TEMP B-TREE" in str(row) for row in plan), plan

    response = owner_client.get(f"/conversations/{conversation}/messages?limit=50")
    assert response.status_code == 200
    assert [message["id"] for message in response.json()] == list(reversed(expected))
    assert all(message["text"] == "Synthetic retained example" for message in response.json())
    owner_client.put(
        f"/conversations/{conversation}/permissions",
        json={"read": False, "retain": False, "learn": False, "draft": False, "send": False},
    )
    assert owner_client.get(f"/conversations/{conversation}/messages?limit=50").status_code == 403
