"""Exercise a real populated upgrade, including private-context encryption."""

import json
import os
from pathlib import Path
import subprocess

from cryptography.fernet import Fernet
from sqlalchemy import MetaData, create_engine, text

from assistant.db import now


ROOT = Path(__file__).resolve().parents[1]


def test_populated_migration_encrypts_context_and_repeats(tmp_path):
    key = Fernet.generate_key()
    url = f"sqlite:///{tmp_path / 'upgrade.db'}"
    env = dict(os.environ, DATABASE_URL=url, ENCRYPTION_KEY=key.decode(), ENVIRONMENT="test")
    command = [str(ROOT / ".venv/bin/alembic")]

    def migrate(*args):
        run = subprocess.run([*command, *args], cwd=ROOT, env=env, capture_output=True, text=True)
        assert run.returncode == 0, run.stderr

    migrate("upgrade", "62a08d908c78")
    engine = create_engine(url)
    metadata = MetaData()
    metadata.reflect(engine)
    stamp = now()
    with engine.begin() as conn:
        conn.execute(metadata.tables["users"].insert().values(id="u", created_at=stamp,
                     subject="synthetic:upgrade", email="upgrade@example.test", display_name="Owner"))
        conn.execute(metadata.tables["workspaces"].insert().values(id="w", created_at=stamp,
                     owner_id="u", name="Upgrade", timezone="UTC", paused=False, pause_generation=0))
        conn.execute(metadata.tables["connectors"].insert().values(id="c", created_at=stamp,
                     workspace_id="w", provider="mock", account_id="synthetic-upgrade", owner_sender_id="Owner",
                     status="connected", capabilities={}, fence=1))
        conn.execute(metadata.tables["conversations"].insert().values(id="chat", created_at=stamp,
                     workspace_id="w", connector_id="c", provider_chat_id="recipient", title="Synthetic",
                     kind="contact", revision=1, control_epoch=0, control_state="DRAFT_MODE",
                     recipient_opted_in=False, recipient_opted_out=False, group_send_allowed=False))
        conn.execute(metadata.tables["style_profiles"].insert().values(id="style", created_at=stamp,
                     workspace_id="w", conversation_id="chat", version=1, sample_count=1,
                     sufficiency="provisional", features={}, evidence_message_ids=[],
                     owner_rules=["Private synthetic preference 7281"]))
        conn.execute(metadata.tables["drafts"].insert().values(id="draft", created_at=stamp,
                     workspace_id="w", conversation_id="chat", recipient_id="recipient",
                     text=Fernet(key).encrypt(b"Synthetic text").decode(), evidence_message_ids=[],
                     missing_facts=["Private synthetic missing fact 9281"], model_version="mock-v1",
                     profile_version=1, conversation_revision=1, control_epoch=0, permission_version=1,
                     pause_generation=0, connector_fence=1, content_hash="0" * 64, status="needs_approval"))
    migrate("upgrade", "head")
    migrate("upgrade", "head")
    migrate("check")
    with engine.connect() as conn:
        rules = conn.execute(text("SELECT owner_rules FROM style_profiles")).scalar()
        facts = conn.execute(text("SELECT missing_facts FROM drafts")).scalar()
        assert "Private" not in rules and "Private" not in facts
        assert json.loads(Fernet(key).decrypt(rules.encode())) == ["Private synthetic preference 7281"]
        assert json.loads(Fernet(key).decrypt(facts.encode())) == ["Private synthetic missing fact 9281"]
        assert conn.execute(text("SELECT COUNT(*) FROM tasks")).scalar() == 0
    engine.dispose()
