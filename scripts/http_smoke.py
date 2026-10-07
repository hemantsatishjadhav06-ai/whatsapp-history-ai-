"""Exercise the running local API with synthetic data, then erase its content."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4
import json

import httpx


def request(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else None


if __name__ == "__main__":
    unique = str(uuid4())
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30) as client:
        assert request(client, "GET", "/health/ready")["status"] == "ok"
        auth = request(client, "POST", "/auth/dev", json={"email": f"http-smoke-{unique}@example.invalid"})
        client.headers["X-CSRF-Token"] = auth["csrf_token"]
        workspace = request(client, "POST", "/workspaces", json={"name": "Synthetic HTTP smoke"})
        connector = request(client, "POST", "/connectors", json={"workspace_id": workspace["id"],
                       "provider": "mock", "account_id": f"http-smoke-{unique}", "owner_sender_id": "Owner"})
        conversation = request(client, "POST", "/conversations", json={"connector_id": connector["id"],
                          "provider_chat_id": "synthetic-client", "title": "Synthetic client"})
        cid, wid = conversation["id"], workspace["id"]
        request(client, "PUT", f"/conversations/{cid}/permissions", json={
            "read": True, "retain": True, "learn": True, "draft": True, "send": True})
        body = {"conversation_id": cid, "text": "06/10/2026, 09:00 - Owner: Hello, thanks for your message.\n"
                  "06/10/2026, 09:01 - Client: Can you share details?", "owner_sender_label": "Owner",
                "date_order": "DMY", "timezone": "Asia/Kolkata"}
        assert request(client, "POST", "/imports/preview", json=body)["record_count"] == 2
        assert request(client, "POST", "/imports", json=body)["message_count"] == 2
        assert request(client, "POST", "/imports", json=body)["replayed"] is True
        profile = request(client, "POST", f"/conversations/{cid}/style-preview")
        assert profile["sample_count"] == 1
        draft = request(client, "POST", f"/conversations/{cid}/drafts", json={})
        draft = request(client, "PATCH", f"/drafts/{draft['id']}", json={"text": "Thanks, I'll review the details."})
        assert request(client, "POST", f"/drafts/{draft['id']}/approve",
                       json={"content_hash": draft["content_hash"]})["status"] == "approved"
        sent = request(client, "POST", f"/drafts/{draft['id']}/dispatch")
        assert sent["status"] == "accepted" and sent["provider_message_id"].startswith("mock:")
        assert request(client, "POST", f"/drafts/{draft['id']}/dispatch")["attempt_id"] == sent["attempt_id"]
        task = request(client, "POST", "/tasks", json={"workspace_id": wid, "title": "Review synthetic brief",
                   "due_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()})
        assert task["status"] == "pending"
        inbox = request(client, "GET", "/inbox", params={"workspace_id": wid})
        assert len(inbox["conversations"]) == 1 and inbox["personal_owner_task_count"] == 1
        assert request(client, "POST", "/pause-all", params={"workspace_id": wid})["paused"] is True
        assert request(client, "DELETE", "/account-data", params={"workspace_id": wid})["status"] == "completed"
        assert request(client, "GET", "/tasks", params={"workspace_id": wid}) == []
        request(client, "POST", "/auth/logout")
    print(json.dumps({"status": "passed", "mode": "running_local_api_synthetic_data",
                      "transport": "mock", "model": "mock", "content_cleanup": "completed"}, indent=2))
