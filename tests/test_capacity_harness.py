"""The load harness must distinguish privacy failures from intentional overload."""
import asyncio
import json
from pathlib import Path
import sys

import httpx
import pytest

# CLI helpers are deliberately outside the service import path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from capacity_distributed import read_manifest, target_url  # noqa: E402
from capacity_http import validate_response, workload  # noqa: E402


@pytest.fixture
def expected_owner():
    return {"owner_id": "owner-a", "workspace_id": "workspace-a", "cookie": "synthetic-session-a",
            "readable_ids": ["chat-a"], "message_ids": {"chat-a": ["message-a"]}, "contact_ids": ["contact-a"]}


def snapshot(owner):
    return {"user": {"id": owner["owner_id"]}, "workspace": {"id": owner["workspace_id"]},
            "workspaces": [{"id": owner["workspace_id"]}], "conversations": [{"id": "chat-a"}],
            "connections": [{"workspace_id": owner["workspace_id"]}], "contacts": [{"id": "contact-a"}],
            **{key: [] for key in ("memories", "styles", "drafts", "actions", "grants")}}


@pytest.mark.parametrize("private_dimension", ["owner", "workspace", "conversation", "memory", "contact"])
def test_capacity_validator_rejects_scope_leaks(expected_owner, private_dimension):
    body = snapshot(expected_owner)
    assert validate_response("bootstrap", 200, body, expected_owner)
    if private_dimension == "owner":
        body["user"]["id"] = "other-owner"
    elif private_dimension == "workspace":
        body["workspaces"].append({"id": "other-workspace"})
    elif private_dimension == "conversation":
        body["conversations"].append({"id": "revoked-chat"})
    elif private_dimension == "memory":
        body["memories"] = [{"conversation_id": "other-chat"}]
    else:
        body["contacts"].append({"id": "forgotten-or-expired-contact"})
    assert not validate_response("bootstrap", 200, body, expected_owner)


def test_workload_reports_overload_as_failure_not_privacy_success(monkeypatch, expected_owner):
    class FakeClient:
        def __init__(self, **kwargs):
            self.cookies = httpx.Cookies()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, path):
            return httpx.Response(503, json={"detail": "Request capacity is busy"})

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    result = asyncio.run(workload("https://synthetic.invalid", "/v1", [expected_owner, {**expected_owner, "owner_id": "owner-b"}],
                                 concurrency=1, requests_per_user=20))
    assert result["adaptive_aborted"]
    assert result["http_requests"] == result["failed_invariant_checks"] == 4
    assert result["successful_invariant_checks"] == result["safety_invariant_failures"] == 0
    assert result["unavailable_or_transport_failures"] == 4
    assert result["http_statuses"] == {"503": 4}


@pytest.mark.parametrize("url", ["http://load.invalid", "https://u:p@load.invalid", "https://load.invalid/api",
                                  "https://load.invalid?token=anything"])
def test_remote_load_target_rejects_insecure_or_credential_origins(url):
    with pytest.raises(ValueError):
        target_url(url)


def test_distributed_fixture_requires_private_distinct_synthetic_sessions(tmp_path, expected_owner):
    path = tmp_path / "manifest.json"
    value = {"fixture_type": "milo_capacity_disposable", "external_sends_enabled": False,
             "owners": [expected_owner, {**expected_owner, "owner_id": "owner-b", "cookie": "synthetic-session-b"}]}
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    assert len(read_manifest(path)["owners"]) == 2
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        read_manifest(path)
    path.chmod(0o600)
    value["owners"][1]["cookie"] = value["owners"][0]["cookie"]
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="independent"):
        read_manifest(path)
