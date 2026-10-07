"""Actual loopback HTTP/SQL bridge smoke; injected sockets never contact WhatsApp.

Run ENVIRONMENT=test .venv/bin/python scripts/personal_qr_bridge_smoke.py --output <json>.
A dedicated database is created and dropped; existing database contents are never altered.
"""
from contextlib import contextmanager
from datetime import timedelta
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time

from cryptography.fernet import Fernet
import httpx
import psycopg
from psycopg import sql
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from assistant.config import Settings
from assistant.db import make_database, now, uid
from assistant.messaging import content_hash
from assistant.models import Connector, Conversation, Draft, Message, Permission, SendAttempt, StyleProfile, Workspace
from assistant.whatsapp_personal_models import PersonalAuthKey, PersonalWhatsAppSession

ROOT = Path(__file__).resolve().parents[1]
OWNER = "15550000000@s.whatsapp.net"
OWNER_LID = "98765432100@lid"
CONTACT = "15551111111@s.whatsapp.net"
UNSELECTED = "15552222222@s.whatsapp.net"
API_RUNNER = '''
import base64, json, os
if os.environ.get("ENVIRONMENT") != "test":
    raise RuntimeError("Synthetic API requires test mode")
from assistant import auth
from assistant.main import create_app
import uvicorn
def synthetic_google(credential, _transport, _audience):
    return json.loads(base64.urlsafe_b64decode(credential.encode()))
auth.google_id_token.verify_oauth2_token = synthetic_google
uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ["QR_SMOKE_API_PORT"]),
            access_log=False, log_level="critical", log_config=None)
'''


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@contextmanager
def disposable_database():
    if os.environ.get("ENVIRONMENT") == "production":
        raise RuntimeError("Synthetic bridge refuses production mode")
    configured = Settings(_env_file=ROOT / ".env")
    if configured.environment == "production":
        raise RuntimeError("Synthetic bridge refuses production configuration")
    original = make_url(os.environ.get("TEST_DATABASE_URL") or configured.database_url)
    if original.get_backend_name() != "postgresql" or original.host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("Synthetic bridge requires loopback PostgreSQL")
    name = "milo_qr_smoke_" + uid().replace("-", "")[:16]
    admin_url = original.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(original.username)))
    try:
        yield original.set(drivername="postgresql+psycopg", database=name).render_as_string(hide_password=False)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (name,))
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def stop(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def await_ready(process, client, url):
    until = time.monotonic() + 20
    while time.monotonic() < until:
        if process.poll() is not None:
            raise RuntimeError("Synthetic service exited before readiness")
        try:
            if client.get(url).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("Synthetic service readiness timed out")


def check(response, expected=200):
    # Never include response bodies, tokens, SQL URLs or process stderr in failures.
    if response.status_code != expected:
        raise AssertionError(f"Synthetic HTTP check expected {expected}, received {response.status_code}")
    return response.json()


def seed_owner_draft(factory, conversation_id):
    text = "Synthetic owner-reviewed reply."
    with factory() as db:
        conv = db.get(Conversation, conversation_id)
        permission = db.scalar(select(Permission).where(Permission.conversation_id == conv.id))
        workspace = db.get(Workspace, conv.workspace_id)
        connector = db.get(Connector, conv.connector_id)
        row = Draft(id=uid(), workspace_id=workspace.id, conversation_id=conv.id,
                    recipient_id=conv.provider_chat_id, text=text, evidence_message_ids=[], missing_facts=[],
                    model_version="synthetic-owner-reviewed", profile_version=0,
                    conversation_revision=conv.revision, control_epoch=conv.control_epoch,
                    permission_version=permission.version, pause_generation=workspace.pause_generation,
                    connector_fence=connector.fence, content_hash=content_hash(text))
        db.add(row)
        db.commit()
        return row.id, row.content_hash


def run():
    report = {"mode": "actual_loopback_http_postgresql_injected_sdk_socket", "external_provider_calls": 0,
              "external_model_calls": 0, "live_phone_verified": False, "disposable_database": True, "checks": []}
    api_process = node_process = None
    engine = None
    with disposable_database() as database_url, tempfile.TemporaryFile() as logs:
        key, gateway, internal, control = (Fernet.generate_key().decode(), secrets.token_urlsafe(40),
                                           secrets.token_urlsafe(40), secrets.token_urlsafe(40))
        api_port, node_port, control_port = free_port(), free_port(), free_port()
        while len({api_port, node_port, control_port}) != 3:
            node_port, control_port = free_port(), free_port()
        api_url, node_url, control_url = (f"http://127.0.0.1:{port}" for port in (api_port, node_port, control_port))
        env = dict(os.environ, ENVIRONMENT="test", DATABASE_URL=database_url, ENCRYPTION_KEY=key,
                   ALLOW_DEV_AUTH="false", SESSION_SECURE="false", MODEL_PROVIDER="disabled", REQUEST_LIMITS_MODE="off",
                   GOOGLE_CLIENT_ID="synthetic-qr-smoke-client", INTERNAL_SERVICE_TOKEN=internal,
                   WHATSAPP_PERSONAL_ENABLED="true", WHATSAPP_PERSONAL_SESSION_URL=node_url,
                   WHATSAPP_PERSONAL_SESSION_TOKEN=gateway, ENABLE_EXTERNAL_SENDS="true",
                   PYTHONPATH=str(ROOT / "services/api"), QR_SMOKE_API_PORT=str(api_port))
        node_env = dict(env, NODE_ENV="test", NODE_TEST_DATABASE_URL=make_url(database_url).set(
                        drivername="postgresql").render_as_string(hide_password=False),
                        SESSION_TEST_PORT=str(node_port), SESSION_TEST_CONTROL_PORT=str(control_port),
                        SESSION_TEST_CONTROL_TOKEN=control, SESSION_GATEWAY_TOKEN=gateway,
                        SESSION_ENCRYPTION_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
                        PYTHON_AUTHORITY_URL=api_url, PYTHON_INTERNAL_TOKEN=internal)
        for operation in (["upgrade", "head"], ["upgrade", "head"], ["check"]):
            migrated = subprocess.run([str(ROOT / ".venv/bin/alembic"), *operation], cwd=ROOT, env=env,
                                      stdout=logs, stderr=logs, timeout=60)
            if migrated.returncode != 0:
                raise RuntimeError("Synthetic database migration validation failed")
        report["checks"].extend(["alembic_head", "migration_repeat_and_schema_parity"])
        engine, factory = make_database(Settings(_env_file=None, environment="test", database_url=database_url,
                                                encryption_key=key, model_provider="disabled").prepare())
        with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as api, \
                httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as private:
            def node_start():
                process = subprocess.Popen(["node", "tests/bridge-fixture.ts"], cwd=ROOT / "services/whatsapp-session",
                                           env=node_env, stdout=logs, stderr=logs)
                await_ready(process, private, node_url + "/readyz")
                return process

            def control_post(path, body):
                return check(private.post(control_url + path, json=body,
                                           headers={"Authorization": "Bearer " + control}))

            def stats():
                return check(private.get(control_url + "/stats", headers={"Authorization": "Bearer " + control}))

            def owner(path, body=None, method="POST", **kwargs):
                return check(api.request(method, api_url + path, json=body, **kwargs))

            try:
                api_process = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-c", API_RUNNER], cwd=ROOT, env=env,
                                               stdout=logs, stderr=logs)
                await_ready(api_process, api, api_url + "/health/ready")
                node_process = node_start()
                nonce = owner("/auth/nonce", method="GET")["nonce"]
                credential = base64.urlsafe_b64encode(json.dumps({"sub": "synthetic-qr-owner", "email_verified": True,
                    "email": "qr-smoke@example.invalid", "iss": "https://accounts.google.com",
                    "aud": "synthetic-qr-smoke-client", "nonce": nonce,
                    "exp": (now() + timedelta(minutes=10)).timestamp()}).encode()).decode()
                signed = owner("/auth/google", {"credential": credential, "nonce": nonce},
                               headers={"X-CSRF-Token": nonce})
                api.headers["X-CSRF-Token"] = signed["csrf_token"]
                workspace = check(api.post(api_url + "/workspaces", json={"name": "Synthetic QR bridge"}), 201)
                wid = workspace["id"]
                started = owner("/integrations/whatsapp/personal/start", {"workspace_id": wid})
                cid = started["connector"]["id"]
                ident = {"schema_version": 1, "workspace_id": wid, "connector_id": cid,
                         "connector_fence": started["connector"]["fence"], "account_id": None}
                control_post("/qr", {"connector_id": cid, "value": "synthetic-private-qr-smoke"})
                pairing = api.get(api_url + "/integrations/whatsapp/personal/pairing", params={"connector_id": cid})
                assert check(pairing)["qr"]["value"] == "synthetic-private-qr-smoke"
                assert "no-store" in pairing.headers["cache-control"]
                control_post("/connect", {"connector_id": cid, "account_id": OWNER, "account_aliases": [OWNER, OWNER_LID]})
                ident["account_id"] = OWNER
                control_post("/contacts", {"connector_id": cid, "chats": [
                    {"provider_chat_id": CONTACT, "title": "Synthetic selected contact"},
                    {"provider_chat_id": UNSELECTED, "title": "Synthetic unselected contact"}]})
                discovered = owner("/integrations/whatsapp/personal/chats", method="GET", params={"connector_id": cid})
                assert len(discovered["chats"]) == 2
                with factory() as db:
                    assert db.scalar(select(func.count()).select_from(Message)) == 0
                control_post("/message", {"connector_id": cid, "provider_chat_id": UNSELECTED,
                    "direction": "inbound", "origin": "live", "provider_message_id": "synthetic-ungranted", "text": "Not retained"})
                with factory() as db:
                    assert db.scalar(select(func.count()).select_from(Message)) == 0
                grant = owner("/integrations/whatsapp/personal/chats/authorize", {"connector_id": cid,
                    "provider_chat_id": CONTACT, "title": "Synthetic selected contact", "read": True, "retain": True,
                    "learn": True, "draft": True, "send": True, "recipient_opted_in": True})
                conv_id = grant["conversation"]["id"]
                report["checks"].extend(["verified_google_exchange", "owner_only_no_store_qr", "provider_account_and_alias_binding",
                                          "contact_discovery", "ungranted_body_not_retained", "explicit_contact_consent"])
                control_post("/message", {"connector_id": cid, "provider_chat_id": CONTACT, "direction": "inbound",
                    "origin": "history", "provider_message_id": "synthetic-history", "text": "Synthetic historical request"})
                control_post("/message", {"connector_id": cid, "provider_chat_id": CONTACT, "direction": "outbound",
                    "origin": "history", "provider_message_id": "synthetic-owner-history", "text": "Synthetic owner style sample"})
                control_post("/message", {"connector_id": cid, "provider_chat_id": CONTACT, "direction": "inbound",
                    "origin": "live", "provider_message_id": "synthetic-live", "text": "Synthetic live request"})
                with factory() as db:
                    rows = list(db.scalars(select(Message).where(Message.conversation_id == conv_id)))
                    assert len(rows) == 3
                    outgoing = next(row for row in rows if row.direction == "outbound")
                    assert outgoing.author_kind == "unknown_owner_outgoing" and outgoing.excluded_from_learning
                    outgoing_id = outgoing.id
                    assert db.scalar(select(func.count()).select_from(SendAttempt)) == 0
                owner("/integrations/whatsapp/personal/authorship/confirm", {"conversation_id": conv_id,
                      "message_ids": [outgoing_id], "confirm_authored_by_owner": True})
                with factory() as db:
                    assert db.get(Message, outgoing_id).author_kind == "human_owner"
                    assert db.scalar(select(StyleProfile).where(StyleProfile.conversation_id == conv_id)).sample_count == 1
                visible = owner(f"/conversations/{conv_id}/messages", method="GET")
                assert len(visible) == 3
                draft_id, digest = seed_owner_draft(factory, conv_id)
                owner(f"/drafts/{draft_id}/approve", {"content_hash": digest})
                attempt = owner(f"/drafts/{draft_id}/dispatch")
                assert attempt["status"] == "accepted" and attempt["provider_message_id"]
                assert owner(f"/drafts/{draft_id}/dispatch") == attempt
                assert stats()["sends"] == 1
                control_post("/receipt", {"connector_id": cid, "provider_message_id": attempt["provider_message_id"], "status": "delivered"})
                control_post("/message", {"connector_id": cid, "provider_chat_id": CONTACT, "direction": "outbound",
                    "origin": "live", "provider_message_id": attempt["provider_message_id"], "text": "Synthetic owner-reviewed reply."})
                with factory() as db:
                    assert db.get(SendAttempt, attempt["attempt_id"]).status == "delivered"
                    echo = db.scalar(select(Message).where(Message.provider_message_id == attempt["provider_message_id"]))
                    assert echo.author_kind == "assistant" and echo.excluded_from_learning
                    assert db.get(Conversation, conv_id).control_state == "DRAFT_MODE"
                    assert db.scalar(select(func.count()).select_from(PersonalAuthKey)) > 0
                report["checks"].extend(["granted_live_and_history_ingest", "history_no_automatic_send", "reviewed_owner_style_learning",
                    "owner_visible_updates", "exact_owner_approved_sdk_send_once", "durable_delivery_receipt", "assistant_echo_excluded",
                    "encrypted_signal_state_written"])
                pending_id, pending_digest = seed_owner_draft(factory, conv_id)
                owner(f"/drafts/{pending_id}/approve", {"content_hash": pending_digest})
                owner("/pause-all", params={"workspace_id": wid})
                check(api.post(api_url + f"/drafts/{pending_id}/dispatch"), 409)
                assert stats()["sends"] == 1
                control_post("/message", {"connector_id": cid, "provider_chat_id": CONTACT, "direction": "inbound",
                    "origin": "live", "provider_message_id": "synthetic-paused-content", "text": "Paused body must not be retained"})
                assert stats()["sockets_ended"] == 0
                with factory() as db:
                    assert db.scalar(select(Message).where(Message.provider_message_id == "synthetic-paused-content")) is None
                owner("/resume-all", params={"workspace_id": wid})
                report["checks"].extend(["owner_pause_blocks_send", "paused_ingest_retains_no_body_and_keeps_session"])
                stop(node_process)
                node_process = node_start()
                control_post("/wait", {})
                current = owner("/integrations/whatsapp/personal/status", method="GET", params={"workspace_id": wid})
                assert current["connected"] is True and current["connector"]["account_id"] == OWNER
                assert stats()["sends"] == 0 and stats()["sockets_started"] == 1
                assert owner(f"/drafts/{draft_id}/dispatch")["status"] == "delivered"
                assert stats()["sends"] == 0
                report["checks"].extend(["encrypted_credentials_restore_after_service_restart", "sql_ledger_prevents_send_replay_after_restart"])
                stop(node_process)
                with factory() as db:
                    db.get(Connector, cid).lease_expires_at = now() - timedelta(seconds=1)
                    db.commit()
                node_process = node_start()
                control_post("/wait", {})
                assert owner("/integrations/whatsapp/personal/status", method="GET", params={"workspace_id": wid})["connected"] is True
                with factory() as db:
                    assert db.get(Conversation, conv_id).control_state == "RECONNECT_REVIEW"
                assert stats()["sends"] == 0
                report["checks"].append("expired_lease_autorestore_requires_contact_review")
                disconnected = owner("/integrations/whatsapp/personal/disconnect", {"connector_id": cid})
                assert disconnected["status"] == "disconnected" and disconnected["remote_session_close"] == "confirmed"
                assert disconnected["provider_revocation"] == "not_verified"
                assert stats()["sockets_ended"] == 1 and stats()["logouts"] == 1
                with factory() as db:
                    assert db.scalar(select(func.count()).select_from(PersonalAuthKey)) == 0
                    assert db.scalar(select(func.count()).select_from(PersonalWhatsAppSession)) == 0
                    assert db.get(Connector, cid).fence > ident["connector_fence"]
                denied = private.post(api_url + "/internal/whatsapp-session-authority", headers={"Authorization": "Bearer " + internal},
                                      json={**ident, "operation": "start"})
                assert check(denied)["allowed"] is False
                stop(node_process)
                node_process = node_start()
                assert stats()["sockets_started"] == 0
                fresh = owner("/integrations/whatsapp/personal/start", {"workspace_id": wid})
                control_post("/wait", {})
                assert fresh["connected"] is False and fresh["status"] == "starting"
                with factory() as db:
                    assert db.scalar(select(func.count()).select_from(PersonalAuthKey)) == 0
                report["checks"].extend(["disconnect_fences_old_actor", "private_actor_closed", "signal_credentials_erased",
                                          "erased_credentials_not_restored", "provider_unlink_claim_remains_unverified"])
                report["passed"] = True
            finally:
                stop(node_process)
                stop(api_process)
                if engine is not None:
                    engine.dispose()
    report["disposable_database_dropped"] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
