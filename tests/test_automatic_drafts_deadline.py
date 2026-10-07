"""A real slow HTTP body must not outlive a background generation slot."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from assistant.db import now
from assistant.intelligence import generate_scoped_result
from assistant.models import Draft, User, Workspace
from assistant.people_models import UsageLedger


def configure(app, base_url):
    settings = app.state.settings
    settings.model_provider = "openai_compatible"
    settings.model_name = "synthetic-deadline-model"
    settings.model_api_key = "synthetic-deadline-token"
    settings.model_api_url = base_url
    settings.model_timeout_seconds = 1
    settings.model_pricing_verified = True
    settings.model_pricing_model_name = settings.model_name
    settings.model_input_cost_microusd_per_million = 2_000_000
    settings.model_output_cost_microusd_per_million = 3_000_000
    return settings


def test_trickling_provider_has_total_deadline_and_keeps_unknown_budget(
    app, owner_client, chat, db,
):
    # Every fragment arrives well within the one-second read timeout. The
    # complete valid response takes several seconds, so only a total deadline
    # can stop this request before a claimed generation slot becomes reusable.
    body = json.dumps({"choices": [{"message": {"content": json.dumps({
        "text": "Could you clarify?", "evidence_message_ids": [],
        "missing_facts": ["Owner intent"],
    })}}]}).encode()
    stop = threading.Event()
    fragments = []

    class Provider(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                for offset in range(0, len(body), 6):
                    self.wfile.write(body[offset:offset + 6])
                    self.wfile.flush()
                    fragments.append(offset)
                    if stop.wait(0.15):
                        return
            except (BrokenPipeError, ConnectionResetError):
                return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    serving = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    serving.start()
    settings = configure(app, f"http://127.0.0.1:{server.server_port}/v1")
    try:
        started = time.monotonic()
        result = owner_client.post(f"/conversations/{chat['conversation']['id']}/drafts", json={})
        elapsed = time.monotonic() - started
        assert result.status_code == 502, result.text
        assert elapsed < 2, f"Total provider deadline was not enforced: {elapsed:.3f}s"
        assert len(fragments) >= 3, "The provider must successfully trickle multiple body fragments"
        assert settings.model_api_key not in result.text
        assert db.scalar(select(func.count(Draft.id))) == 0
        reservation = db.scalar(select(UsageLedger))
        assert reservation is not None and reservation.status == "uncertain"
        assert reservation.token_units > 1000 and reservation.cost_microusd > 0
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)


def test_expired_admission_never_calls_provider_and_releases_reserved_budget(
    app, owner_client, chat, db, monkeypatch,
):
    settings = configure(app, "https://model.example.test/v1")
    owner = db.get(User, db.get(Workspace, chat["workspace"]["id"]).owner_id)
    called = []
    monkeypatch.setattr("assistant.intelligence.call_model", lambda *_: called.append(True))
    with pytest.raises(HTTPException) as error:
        generate_scoped_result(db, owner, chat["conversation"]["id"], settings,
                               "Prepare a draft", provider_deadline=now())
    assert error.value.status_code == 409
    assert called == []
    row = db.scalar(select(UsageLedger))
    assert row is not None and row.status == "released"
    assert db.scalar(select(func.count(Draft.id))) == 0
