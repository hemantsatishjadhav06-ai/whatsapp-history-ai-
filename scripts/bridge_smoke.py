"""Exercise Python HTTP -> Node HTTP -> current SQL authority -> mock socket.

Uses disposable SQLite, random local credentials, synthetic native records and
loopback servers. It neither pairs an account nor contacts WhatsApp.
"""
import json
import os
import secrets
import socket
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import uvicorn
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from assistant.action_models import OutboundAction, SubmissionAttempt
from assistant.actions import envelope_for
from assistant.config import Settings
from assistant.db import Base
from assistant.main import create_app


ROOT = Path(__file__).resolve().parents[1]


def checked(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    if not response.is_success:
        raise RuntimeError(f"Synthetic bridge request failed: {method} {path}: {response.status_code}")
    return response.json()


def port_socket():
    value = socket.socket()
    value.bind(("127.0.0.1", 0))
    return value


def run():
    Path(".local").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bridge-smoke-", dir=".local") as directory:
        api_socket, gateway_socket = port_socket(), port_socket()
        api_port, gateway_port = api_socket.getsockname()[1], gateway_socket.getsockname()[1]
        gateway_socket.close()
        token, gateway_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        api_origin, gateway_origin = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{gateway_port}"
        settings = Settings(_env_file=None, environment="test", allow_dev_auth=True, session_secure=False,
                            database_url=f"sqlite:///{Path(directory).resolve()}/bridge.db",
                            encryption_key=Fernet.generate_key().decode(), internal_service_token=token,
                            connector_gateway_url=gateway_origin, connector_gateway_token=gateway_token)
        app = create_app(settings)
        Base.metadata.create_all(app.state.engine)
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [api_socket]}, daemon=True)
        thread.start()
        process = None
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if time.monotonic() > deadline:
                    raise RuntimeError("Disposable API did not start")
                time.sleep(0.02)
            environment = os.environ.copy()
            environment.update({"ENVIRONMENT": "test", "GATEWAY_PORT": str(gateway_port),
                                "GATEWAY_TOKEN": gateway_token, "PYTHON_AUTHORITY_URL": api_origin,
                                "PYTHON_INTERNAL_TOKEN": token})
            process = subprocess.Popen(["node", "src/start.ts"], cwd=ROOT / "services/connector-gateway",
                                       env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with httpx.Client(base_url=api_origin, timeout=10, trust_env=False) as client:
                deadline = time.monotonic() + 10
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("Disposable mock gateway did not start")
                    try:
                        if client.get(gateway_origin + "/healthz").is_success:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() > deadline:
                        raise RuntimeError("Disposable mock gateway did not become healthy")
                    time.sleep(0.02)
                login = checked(client, "POST", "/auth/dev", json={"email": "bridge-smoke@example.invalid"})
                client.headers["X-CSRF-Token"] = login["csrf_token"]
                workspace = checked(client, "POST", "/workspaces", json={"name": "Synthetic bridge", "timezone": "UTC"})
                connector = checked(client, "POST", "/connectors", json={"workspace_id": workspace["id"],
                    "provider": "mock", "account_id": "synthetic-bridge-account", "owner_sender_id": "Owner"})

                def chat(jid):
                    row = checked(client, "POST", "/conversations", json={"connector_id": connector["id"],
                                  "provider_chat_id": jid, "title": "Synthetic chat", "recipient_opted_in": True})
                    checked(client, "PUT", f"/conversations/{row['id']}/permissions", json={
                        "read": True, "retain": True, "learn": True, "draft": True, "send": True, "share": True})
                    return {**row, "provider_chat_id": jid}

                source = chat("15550001234@s.whatsapp.net")
                destination = chat("15550005678@s.whatsapp.net")
                expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
                route = checked(client, "POST", "/forward-routes", json={"source_conversation_id": source["id"],
                    "destination_conversation_id": destination["id"], "audience": "contact", "expires_at": expiry})

                def incoming(label, origin="live", native=True):
                    key = {"remoteJid": source["provider_chat_id"], "id": f"provider-{label}", "fromMe": False}
                    event = {"event_id": f"event-{label}", "connector_id": connector["id"],
                             "conversation_id": source["id"], "provider_message_id": key["id"],
                             "sender_id": "Friend", "direction": "inbound", "origin": origin,
                             "author_kind": "contact_human", "provider_timestamp": datetime.now(UTC).isoformat(),
                             "content": {"text": "Thanks, great!"}}
                    if native:
                        event["native_record"] = {"provider_record_ref": f"original-{label}",
                            "account_id": connector["account_id"], "key": key,
                            "payload": {"key": key, "message": {"conversation": "Thanks, great!"}}}
                    return checked(client, "POST", "/internal/connector-events", json=event,
                                   headers={"Authorization": f"Bearer {token}"})["message_id"]

                habit = incoming("habit", origin="history", native=False)
                checked(client, "POST", "/internal/connector-events", json={
                    "event_id": "event-owner-habit", "connector_id": connector["id"], "conversation_id": source["id"],
                    "provider_message_id": "provider-owner-habit", "sender_id": "Owner", "direction": "outbound",
                    "origin": "history", "event_type": "reaction.added", "author_kind": "human_owner",
                    "provider_timestamp": datetime.now(UTC).isoformat(),
                    "reaction": {"target_provider_message_id": "provider-habit", "emoji": "🙏"}},
                    headers={"Authorization": f"Bearer {token}"})
                assert habit
                checked(client, "PUT", "/automation/grants", json={"conversation_id": source["id"],
                    "allowed_actions": ["SEND_TEXT", "QUOTE", "REACTION", "FORWARD"],
                    "allowed_intents": ["acknowledgement", "forwarding"], "reaction_palette": ["🙏"],
                    "forward_route_ids": [route["id"]], "quiet_start": "00:00", "quiet_end": "00:00",
                    "timezone": "UTC", "expires_at": expiry})
                accepted = []
                for kind in ("SEND_TEXT", "QUOTE", "REACTION", "FORWARD"):
                    target = incoming(kind)
                    proposal = {"conversation_id": source["id"], "kind": kind, "trigger_message_id": target,
                                "intent": "forwarding" if kind == "FORWARD" else "acknowledgement"}
                    if kind in {"SEND_TEXT", "QUOTE"}:
                        proposal["text"] = "Thanks!"
                    if kind != "SEND_TEXT":
                        proposal["target_message_id"] = target
                    if kind == "REACTION":
                        proposal["emoji"] = "🙏"
                    if kind == "FORWARD":
                        proposal["route_id"] = route["id"]
                    action = checked(client, "POST", "/actions", json=proposal)
                    sent = checked(client, "POST", f"/actions/{action['id']}/dispatch")
                    assert sent["status"] == "accepted", sent.get("reason_code")
                    repeated = checked(client, "POST", f"/actions/{action['id']}/dispatch")
                    assert repeated["status"] == "accepted"
                    accepted.append(action["id"])
                with app.state.session_factory() as db:
                    attempts = db.scalar(select(func.count()).select_from(SubmissionAttempt))
                    assert attempts == 4
                    forged = envelope_for(db, db.get(OutboundAction, accepted[-1]))
                    forged["recipient_id"] = "attacker@s.whatsapp.net"
                unauthenticated = client.post(gateway_origin + "/v1/actions", json=forged)
                assert unauthenticated.status_code == 401
                forbidden = client.post(gateway_origin + "/v1/actions", json=forged,
                                        headers={"Authorization": f"Bearer {gateway_token}"})
                assert forbidden.status_code == 409
                return {"simulation_only": True, "real_python_node_authority_http": True,
                        "typed_operations_accepted": ["SEND_TEXT", "QUOTE", "REACTION", "FORWARD"],
                        "durable_sql_attempts": attempts, "duplicates_preserve_one_attempt": True,
                        "forged_recipient_rejected": True, "external_provider_calls": 0}
        finally:
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            server.should_exit = True
            thread.join(timeout=5)
            api_socket.close()
            app.state.engine.dispose()


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
