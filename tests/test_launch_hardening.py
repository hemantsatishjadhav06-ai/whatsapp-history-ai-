"""Launch-readiness fixes: tenant-scoped labels, model spend/concurrency bounds,
OpenRouter routing and credential-free control admission."""

import json
import threading

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from assistant import intelligence
from assistant.config import Settings
from assistant.models import Draft
from assistant.people_models import UsageLedger
from conftest import create_chat, login
from test_model_budget import configure, invoke, provider, set_budget, valid_response


def test_export_labels_are_scoped_to_each_workspace(app):
    with TestClient(app) as first, TestClient(app) as second:
        login(first, "first-owner@example.test")
        login(second, "second-owner@example.test")
        mine = create_chat(first, provider="export_only", account="Family")
        theirs = create_chat(second, provider="export_only", account="Family")
        assert mine["connector"]["id"] != theirs["connector"]["id"]
        repeat = first.post("/connectors", json={"workspace_id": mine["workspace"]["id"], "provider": "export_only",
                                                 "account_id": "Family", "owner_sender_id": "Owner"})
        assert repeat.status_code == 201
        assert repeat.json()["id"] == mine["connector"]["id"]


def test_default_daily_model_allowance_applies_without_a_workspace_budget(app, owner_client, chat, monkeypatch, db):
    settings = configure(app)
    settings.model_default_daily_tokens = 10
    called = []
    monkeypatch.setattr(intelligence, "call_model", lambda *_: called.append(True))
    response = invoke(owner_client, chat)
    assert response.status_code == 429, response.text
    assert response.json()["detail"]["budget"] == "default_model_tokens_per_day"
    assert called == []
    assert db.scalar(select(func.count(UsageLedger.id))) == 0


def test_explicit_workspace_token_budget_replaces_the_default_allowance(app, owner_client, chat, monkeypatch):
    settings = configure(app)
    settings.model_default_daily_tokens = 10
    set_budget(owner_client, chat, max_tokens_per_day=1_000_000)
    provider(monkeypatch, lambda request: valid_response(usage={"prompt_tokens": 50, "completion_tokens": 5,
                                                                "total_tokens": 55}))
    assert invoke(owner_client, chat).status_code == 201


def test_default_allowance_counts_every_workspace_of_the_same_owner(app, owner_client, chat, monkeypatch, db):
    settings = configure(app)
    provider(monkeypatch, lambda request: valid_response(usage={"prompt_tokens": 40, "completion_tokens": 10,
                                                                "total_tokens": 50}))
    assert invoke(owner_client, chat).status_code == 201
    settings.model_default_daily_tokens = 60
    second = create_chat(owner_client, account="synthetic-second-workspace")
    response = invoke(owner_client, second)
    assert response.status_code == 429, response.text
    assert response.json()["detail"]["budget"] == "default_model_tokens_per_day"


def test_model_admission_bounds_per_owner_and_total_concurrency():
    settings = Settings(_env_file=None, environment="test", model_provider="openai_compatible",
                        model_max_concurrency=2, model_max_concurrency_per_owner=1)
    admission = intelligence.ModelAdmission()
    with admission.slot(settings, "owner-a"):
        with pytest.raises(HTTPException) as busy:
            with admission.slot(settings, "owner-a"):
                pass
        assert busy.value.status_code == 429 and busy.value.detail["code"] == "MODEL_BUSY"
        with admission.slot(settings, "owner-b"):
            with pytest.raises(HTTPException):
                with admission.slot(settings, "owner-c"):
                    pass
    assert admission.active == 0 and admission.owners == {}
    with admission.slot(settings, "owner-a"):
        assert admission.owners == {"owner-a": 1}


def test_concurrent_generation_for_one_owner_fails_fast(app, owner_client, chat, monkeypatch):
    configure(app)
    entered, release = threading.Event(), threading.Event()

    def slow(request):
        entered.set()
        release.wait(10)
        return valid_response(usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    provider(monkeypatch, slow)
    results = []
    worker = threading.Thread(target=lambda: results.append(invoke(owner_client, chat).status_code))
    worker.start()
    try:
        assert entered.wait(10)
        busy = invoke(owner_client, chat)
        assert busy.status_code == 429, busy.text
        assert busy.json()["detail"]["code"] == "MODEL_BUSY"
    finally:
        release.set()
        worker.join(10)
    assert results == [201]


def test_openrouter_requests_require_schema_support_and_send_attribution(app, owner_client, chat, monkeypatch, db):
    settings = configure(app)
    settings.model_provider = "openrouter"
    settings.model_api_url = "https://openrouter.ai/api/v1"
    settings.model_name = settings.model_pricing_model_name = "openai/gpt-4o-mini"
    settings.allowed_origins = "https://milo.example.test"
    seen = []

    def respond(request):
        seen.append(request)
        content = "```json\n" + json.dumps({"text": "Sure — what time works?", "evidence_message_ids": [],
                                             "missing_facts": ["Preferred time"]}) + "\n```"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                          "usage": {"prompt_tokens": 30, "completion_tokens": 8, "total_tokens": 38}})

    provider(monkeypatch, respond)
    response = invoke(owner_client, chat)
    assert response.status_code == 201, response.text
    request = seen[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer synthetic-test-token"
    assert request.headers["http-referer"] == "https://milo.example.test"
    assert request.headers["x-title"] == "Milo"
    payload = json.loads(request.content)
    assert payload["provider"] == {"require_parameters": True}
    assert payload["response_format"]["json_schema"]["strict"] is True
    draft = db.get(Draft, response.json()["id"])
    assert draft.text == "Sure — what time works?" and draft.model_version == "openai/gpt-4o-mini"


def test_openrouter_provider_defaults_to_the_openrouter_endpoint():
    settings = Settings(_env_file=None, environment="test", model_provider="openrouter").prepare()
    assert settings.model_api_url == "https://openrouter.ai/api/v1"
    explicit = Settings(_env_file=None, environment="test", model_provider="openrouter",
                        model_api_url="https://gateway.example.test/v1").prepare()
    assert explicit.model_api_url == "https://gateway.example.test/v1"


def test_openai_requests_keep_the_plain_payload(app, owner_client, chat, monkeypatch):
    configure(app)
    seen = []
    provider(monkeypatch, lambda request: seen.append(request) or valid_response())
    assert invoke(owner_client, chat).status_code == 201
    assert "provider" not in json.loads(seen[0].content)
    assert "http-referer" not in seen[0].headers


@pytest.mark.parametrize("path", ["/pause-all", "/resume-all", "/auth/logout", "/v1/pause-all",
                                  "/conversations/synthetic/takeover"])
def test_credential_free_control_requests_cannot_spend_shared_control_capacity(app, path):
    app.state.settings.request_limits_mode = "memory"
    app.state.settings.request_rate_control = 1
    app.state.settings.request_rate_source_control = 1
    with TestClient(app) as anonymous, TestClient(app) as owner:
        for _ in range(5):
            assert anonymous.post(path).status_code == 401
        login(owner)
        workspace = owner.post("/workspaces", json={"name": "Owner", "timezone": "UTC"}).json()
        response = owner.post("/pause-all", params={"workspace_id": workspace["id"]})
        assert response.status_code == 200, response.text
