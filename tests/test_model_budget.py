import json

import httpx
import pytest
from sqlalchemy import func, select

from assistant import intelligence
from assistant.models import Draft
from assistant.people_models import UsageLedger
from conftest import create_chat, login
from test_people import source


def configure(app, *, priced=True):
    settings = app.state.settings
    settings.model_provider = "openai_compatible"
    settings.model_name = "synthetic-approved-model"
    settings.model_api_key = "synthetic-test-token"
    settings.model_api_url = "https://model.example.test/v1"
    settings.model_pricing_verified = priced
    settings.model_pricing_model_name = settings.model_name
    settings.model_input_cost_microusd_per_million = 2_000_000
    settings.model_output_cost_microusd_per_million = 3_000_000
    return settings


def set_budget(client, chat, **limits):
    response = client.put(f"/workspaces/{chat['workspace']['id']}/budget", json={"expected_version": 0, **limits})
    assert response.status_code == 200, response.text


def invoke(client, chat):
    return client.post(f"/conversations/{chat['conversation']['id']}/drafts", json={"instruction": "Reply politely"})


def provider(monkeypatch, respond):
    original = httpx.Client

    def mocked_client(**kwargs):
        return original(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(intelligence.httpx, "Client", mocked_client)


def valid_response(*, usage=None):
    result = {"choices": [{"message": {"content": json.dumps({"text": "Could you clarify?",
               "evidence_message_ids": [], "missing_facts": ["Owner intent"]})}}]}
    if usage is not None:
        result["usage"] = usage
    return httpx.Response(200, json=result)


def test_zero_token_ceiling_blocks_real_model_before_any_network(app, owner_client, chat, monkeypatch, db):
    configure(app)
    set_budget(owner_client, chat, max_tokens_per_day=0)
    called = []
    monkeypatch.setattr(intelligence, "call_model", lambda *_: called.append(True))
    response = invoke(owner_client, chat)
    assert response.status_code == 429, response.text
    assert response.json()["detail"]["code"] == "QUOTA_HELD"
    assert called == []
    assert db.scalar(select(func.count(UsageLedger.id))) == 0


@pytest.mark.parametrize("pricing", ["unverified", "wrong_model", "missing_rate"])
def test_cost_ceiling_fails_closed_when_exact_model_pricing_is_unverified(app, owner_client, chat, monkeypatch, pricing):
    settings = configure(app)
    if pricing == "unverified":
        settings.model_pricing_verified = False
    elif pricing == "wrong_model":
        settings.model_pricing_model_name = "different-model"
    else:
        settings.model_output_cost_microusd_per_million = None
    set_budget(owner_client, chat, max_cost_microusd_per_day=0)
    called = []
    monkeypatch.setattr(intelligence, "call_model", lambda *_: called.append(True))
    response = invoke(owner_client, chat)
    assert response.status_code == 429, response.text
    assert response.json()["detail"]["budget"] == "max_cost_microusd_per_day"
    assert called == []
    assert settings.model_api_key not in response.text


def test_mock_model_is_explicitly_zero_external_tokens_and_cost(app, owner_client, chat, db):
    set_budget(owner_client, chat, max_tokens_per_day=0, max_cost_microusd_per_day=0)
    result = invoke(owner_client, chat)
    assert result.status_code == 201, result.text
    row = db.scalar(select(UsageLedger))
    assert row.kind == "model_mock" and row.status == "consumed"
    assert row.token_units == row.cost_microusd == row.action_units == 0


def test_real_provider_admission_is_durable_and_no_transaction_spans_network(app, owner_client, chat, monkeypatch, db):
    configure(app)
    source(db, chat, text_value="Private हिन्दी source")
    set_budget(owner_client, chat, max_tokens_per_day=100_000, max_cost_microusd_per_day=1_000_000)
    seen = {}

    def respond(request):
        with app.state.session_factory() as separate:
            reservation = separate.scalar(select(UsageLedger))
            assert reservation is not None and reservation.status == "reserved"
            seen["token_reservation"] = reservation.token_units
            seen["cost_reservation"] = reservation.cost_microusd
            assert reservation.token_units > len(request.content) + 1000
            # A separate write succeeds during the mocked external call: SQL
            # locks have been committed before the provider boundary.
            reservation.status = "reserved"
            separate.commit()
        return valid_response(usage={"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25})

    provider(monkeypatch, respond)
    response = invoke(owner_client, chat)
    assert response.status_code == 201, response.text
    db.expire_all()
    row = db.scalar(select(UsageLedger))
    assert row.status == "consumed" and row.token_units == 25 and row.cost_microusd == 55
    assert seen["token_reservation"] > row.token_units
    assert seen["cost_reservation"] > row.cost_microusd
    assert "Private" not in row.operation_key and "हिन्दी" not in row.operation_key


def test_unknown_timeout_preserves_reserved_units_and_redacts_errors(app, owner_client, chat, monkeypatch, db):
    settings = configure(app)
    set_budget(owner_client, chat, max_tokens_per_day=100_000, max_cost_microusd_per_day=1_000_000)

    def respond(request):
        raise httpx.ReadTimeout("Private provider internals", request=request)

    provider(monkeypatch, respond)
    response = invoke(owner_client, chat)
    assert response.status_code == 502
    assert "Private" not in response.text and settings.model_api_key not in response.text
    row = db.scalar(select(UsageLedger))
    assert row.status == "uncertain" and row.token_units > 1000 and row.cost_microusd > 0
    assert db.scalar(select(func.count(Draft.id))) == 0


@pytest.mark.parametrize("usage", [None, {"prompt_tokens": -1, "completion_tokens": 5},
                                  {"prompt_tokens": True, "completion_tokens": 5},
                                  {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 1},
                                  {"prompt_tokens": 10, "completion_tokens": 5.0}])
def test_missing_or_invalid_accounting_keeps_upper_reservation_uncertain(app, owner_client, chat, monkeypatch, db, usage):
    configure(app)
    provider(monkeypatch, lambda _: valid_response(usage=usage))
    response = invoke(owner_client, chat)
    assert response.status_code == 201, response.text
    row = db.scalar(select(UsageLedger))
    assert row.status == "uncertain" and row.token_units > 1000 and row.cost_microusd > 0


def test_usage_exceeding_model_output_limit_is_recorded_and_held(app, owner_client, chat, monkeypatch, db):
    configure(app)
    provider(monkeypatch, lambda _: valid_response(usage={"prompt_tokens": 10, "completion_tokens": 1001,
                                                         "total_tokens": 1011}))
    response = invoke(owner_client, chat)
    assert response.status_code == 429, response.text
    assert response.json()["detail"]["code"] == "QUOTA_HELD"
    row = db.scalar(select(UsageLedger))
    assert row.status == "uncertain" and row.token_units == 1011
    assert db.scalar(select(func.count(Draft.id))) == 0


def test_unpriced_success_keeps_unknown_cost_explicit(app, owner_client, chat, monkeypatch, db):
    configure(app, priced=False)
    provider(monkeypatch, lambda _: valid_response(usage={"prompt_tokens": 10, "completion_tokens": 5}))
    response = invoke(owner_client, chat)
    assert response.status_code == 201, response.text
    row = db.scalar(select(UsageLedger))
    assert row.kind == "model_unpriced" and row.status == "uncertain"
    assert row.token_units == 15 and row.cost_microusd == 0


def test_model_budget_uses_server_workspace_and_cannot_cross_owner(app, owner_client, chat, monkeypatch, db):
    configure(app)
    set_budget(owner_client, chat, max_tokens_per_day=0)
    login(owner_client, "different-owner@example.test")
    other = create_chat(owner_client, account="different-owner-account")
    provider(monkeypatch, lambda _: valid_response(usage={"prompt_tokens": 10, "completion_tokens": 5}))
    assert invoke(owner_client, chat).status_code == 404
    assert invoke(owner_client, other).status_code == 201
    rows = db.scalars(select(UsageLedger)).all()
    assert len(rows) == 1 and rows[0].workspace_id == other["workspace"]["id"]


def test_provider_metadata_cannot_be_injected_in_model_authored_proposal():
    with pytest.raises(ValueError):
        intelligence.ModelResult.model_validate({"text": "Hello", "missing_facts": ["Intent"],
                                                "_provider_usage": [0, 0]})


def test_trusted_receipt_settles_uncertainty_without_refunding_budget(owner_client, chat, db):
    from assistant.companion import reserve_action_budget, settle_action_budget
    reserve_action_budget(db, chat["workspace"]["id"], "action-1", "send_text")
    settle_action_budget(db, chat["workspace"]["id"], "action-1", "uncertain")
    settle_action_budget(db, chat["workspace"]["id"], "action-1", "consumed")
    db.commit()
    row = db.scalar(select(UsageLedger))
    assert row.status == "consumed" and row.action_units == 1
    settle_action_budget(db, chat["workspace"]["id"], "action-1", "released")
    assert row.status == "consumed"
