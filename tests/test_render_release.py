"""Release orchestration uses synthetic provider responses, never hosted deploys."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import httpx
import pytest


_SPEC = importlib.util.spec_from_file_location(
    "milo_render_release_tests", Path(__file__).resolve().parents[1] / "scripts/render_release.py")
release_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(release_module)
RenderRelease = release_module.RenderRelease
ReleaseFailure = release_module.ReleaseFailure
COMMIT = "a" * 40
IDS = {role: f"srv-{role.lower()}00000000" for role in release_module.ROLES}
ORIGIN = "https://milo-synthetic.onrender.com"


class Clock:
    def __init__(self):
        self.now = 0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, duration):
        assert duration > 0
        self.sleeps.append(duration)
        self.now += duration


class Provider:
    def __init__(self):
        self.calls = []
        self.public_calls = []
        self.services = {
            role: {"id": service_id, "type": release_module.ROLES[role],
                   "repo": release_module.REPO + ".git", "branch": "main", "autoDeploy": "no",
                   "suspended": "not_suspended", "ownerId": "tea-synthetic-owner",
                   "environmentId": "env-synthetic-shared",
                   "serviceDetails": {"runtime": "docker", "url": ORIGIN if role == "WEB" else None}}
            for role, service_id in IDS.items()
        }
        self.submissions = {}
        self.polls = {}
        self.public_live = {"status": "ok", "service": "milo-web"}
        self.public_ready = {"status": "ready", "service": "milo-web", "release_commit": COMMIT}

    @staticmethod
    def deployment(role, status="live", commit=COMMIT):
        return {"id": f"dep-{role.lower()}00000000", "status": status, "commit": {"id": commit}}

    def api_handler(self, request):
        self.calls.append(request)
        assert request.url.host == "api.render.com"
        assert request.headers.get("authorization") == "Bearer synthetic-provider-key"
        pieces = request.url.path.removeprefix("/v1/").split("/")
        role = next(role for role, service_id in IDS.items() if service_id == pieces[1])
        if len(pieces) == 2:
            return httpx.Response(200, json=deepcopy(self.services[role]))
        if request.method == "POST":
            value = self.submissions.get(role, self.deployment(role))
        else:
            value = self.polls.get(role, self.deployment(role))
        if isinstance(value, Exception):
            raise value
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(201 if request.method == "POST" else 200, json=value)

    def public_handler(self, request):
        self.public_calls.append(request)
        assert request.url.host == "milo-synthetic.onrender.com"
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        payload = self.public_live if request.url.path == "/healthz" else self.public_ready
        if isinstance(payload, httpx.Response):
            return payload
        return httpx.Response(200, json=payload)


@pytest.fixture
def release():
    provider = Provider()
    clock = Clock()
    with httpx.Client(base_url=release_module.API_ORIGIN,
                      headers={"Authorization": "Bearer synthetic-provider-key"},
                      transport=httpx.MockTransport(provider.api_handler), follow_redirects=False) as api:
        with httpx.Client(transport=httpx.MockTransport(provider.public_handler),
                          follow_redirects=False) as public:
            value = RenderRelease(api, public, clock=clock, sleep=clock.sleep, timeout=10, interval=4)
            yield value, provider, clock


def test_exact_commit_release_preflights_every_service_before_any_post(release):
    value, provider, _ = release
    report = value.run(IDS, COMMIT)
    assert [request.method for request in provider.calls] == ["GET"] * 4 + ["POST"] * 4
    assert [json.loads(request.content) for request in provider.calls if request.method == "POST"] == [
        {"commitId": COMMIT, "clearCache": "do_not_clear"}] * 4
    assert [row["role"] for row in report["deployments"]] == list(release_module.ROLES)
    assert all(row["verified_commit"] == COMMIT for row in report["deployments"])
    assert report["status"] == "PASS"
    assert report["public_url"] == ORIGIN
    assert report["external_provider_calls"] == 0
    assert [request.url.path for request in provider.public_calls] == ["/healthz", "/readyz"]


@pytest.mark.parametrize("field,bad", [
    ("id", "srv-other00000000"), ("type", "web_service"), ("repo", "https://github.com/other/repo"),
    ("branch", "development"), ("autoDeploy", "yes"), ("suspended", "suspended"),
    ("ownerId", "tea-another-owner"), ("environmentId", "env-another-environment"),
])
def test_configuration_mismatch_never_submits_deployment(release, field, bad):
    value, provider, _ = release
    provider.services["API"][field] = bad
    with pytest.raises(ReleaseFailure):
        value.run(IDS, COMMIT)
    assert not any(request.method == "POST" for request in provider.calls)
    assert value.report["status"] == "FAIL_OR_UNCONFIRMED"
    assert not value.report["deployments"]


def test_last_service_preflight_and_docker_runtime_are_checked_before_first_post(release):
    value, provider, _ = release
    provider.services["WEB"]["serviceDetails"]["runtime"] = "node"
    with pytest.raises(ReleaseFailure, match="web_service_configuration_mismatch"):
        value.run(IDS, COMMIT)
    assert len(provider.calls) == 4
    assert all(request.method == "GET" for request in provider.calls)


@pytest.mark.parametrize("commit", ["main", "a" * 39, "A" * 40, "a" * 41, "$(secret)"])
def test_commit_must_be_full_immutable_sha_before_network(release, commit):
    value, provider, _ = release
    with pytest.raises(ReleaseFailure, match="exact_git_commit_required"):
        value.run(IDS, commit)
    assert not provider.calls


@pytest.mark.parametrize("ids", [dict.fromkeys(IDS, IDS["API"]), {"API": IDS["API"]},
                                 {**IDS, "WEB": "https://attacker.example/services"}])
def test_ids_are_four_distinct_provider_identifiers_before_any_deployment(release, ids):
    value, provider, _ = release
    with pytest.raises(ReleaseFailure):
        value.run(ids, COMMIT)
    assert all(request.method == "GET" for request in provider.calls)


@pytest.mark.parametrize("field,bad", [("serviceDetails", None), ("serviceDetails", []),
                                      ("repo", None), ("repo", ["synthetic-secret"]),
                                      ("ownerId", []), ("ownerId", ""),
                                      ("environmentId", {}), ("rootDir", ["synthetic-secret"])])
def test_malformed_provider_metadata_fails_with_fixed_error_before_any_post(release, field, bad):
    value, provider, _ = release
    provider.services["API"][field] = bad
    with pytest.raises(ReleaseFailure, match="api_service_configuration_mismatch") as error:
        value.run(IDS, COMMIT)
    assert all(request.method == "GET" for request in provider.calls)
    assert "synthetic-secret" not in str(error.value)


@pytest.mark.parametrize("field,bad,reason", [("id", [], "invalid_deploy_identifier"),
                                            ("status", {}, "unknown_deployment_status"),
                                            ("commit", "synthetic-secret", "commit_not_verified"),
                                            ("commit", ["synthetic-secret"], "commit_not_verified")])
def test_malformed_deployment_shape_never_leaks_upstream_payload(release, field, bad, reason):
    value, provider, _ = release
    provider.submissions["API"] = {**provider.deployment("API"), field: bad}
    with pytest.raises(ReleaseFailure, match=reason) as error:
        value.run(IDS, COMMIT)
    assert sum(request.method == "POST" for request in provider.calls) == 1
    assert "synthetic-secret" not in str(error.value)
    assert "synthetic-secret" not in json.dumps(value.report)


@pytest.mark.parametrize("status", ["live", "build_in_progress"])
def test_wrong_commit_even_during_build_stops_later_roles(release, status):
    value, provider, _ = release
    provider.submissions["API"] = provider.deployment("API", status, "b" * 40)
    with pytest.raises(ReleaseFailure, match="api_commit_mismatch"):
        value.run(IDS, COMMIT)
    assert sum(request.method == "POST" for request in provider.calls) == 1
    assert "public_url" not in value.report


@pytest.mark.parametrize("status,reason", [("build_failed", "deployment_failed"),
                                          ("canceled", "deployment_failed"),
                                          ("unrecognized_future_state", "unknown_deployment_status")])
def test_failed_or_unknown_deployment_is_not_hosted_success(release, status, reason):
    value, provider, _ = release
    provider.submissions["API"] = provider.deployment("API", status)
    with pytest.raises(ReleaseFailure, match=reason):
        value.run(IDS, COMMIT)
    assert value.report["status"] == "FAIL_OR_UNCONFIRMED"
    assert not provider.public_calls


def test_live_deployment_requires_positive_commit_verification(release):
    value, provider, _ = release
    provider.submissions["API"] = {"id": "dep-api00000000", "status": "live"}
    with pytest.raises(ReleaseFailure, match="api_commit_not_verified"):
        value.run(IDS, COMMIT)


def test_pending_deployment_deadline_is_bounded_and_does_not_resubmit(release):
    value, provider, clock = release
    provider.submissions["API"] = provider.deployment("API", "queued")
    provider.polls["API"] = provider.deployment("API", "build_in_progress")
    with pytest.raises(ReleaseFailure, match="api_deployment_timeout"):
        value.run(IDS, COMMIT)
    assert clock.now == 10
    assert clock.sleeps == [4, 4, 2]
    assert sum(request.method == "POST" for request in provider.calls) == 1
    assert value.report["deployments"][0]["status"] == "build_in_progress"


def test_lost_post_acknowledgment_keeps_partial_report_without_retry(release):
    value, provider, _ = release
    provider.submissions["JOBS"] = httpx.ReadTimeout("synthetic-private-secret")
    with pytest.raises(ReleaseFailure, match="request_not_confirmed"):
        value.run(IDS, COMMIT)
    assert [row["status"] for row in value.report["deployments"]] == ["live", "SUBMISSION_NOT_CONFIRMED"]
    assert value.report["deployments"][0]["verified_commit"] == COMMIT
    assert sum(request.method == "POST" for request in provider.calls) == 2
    assert not provider.public_calls
    assert "synthetic-private-secret" not in json.dumps(value.report)


def test_partial_submission_is_checkpointed_before_post_even_if_process_is_interrupted(release):
    value, provider, _ = release
    snapshots = []
    value.record = lambda report: snapshots.append(deepcopy(report))
    original = provider.api_handler

    class Interrupted(BaseException):
        pass

    def handler(request):
        if request.method == "POST":
            snapshot = snapshots[-1]
            assert snapshot["deployments"][-1]["status"] == "SUBMITTING"
            if snapshot["deployments"][-1]["role"] == "JOBS":
                assert snapshot["deployments"][0]["verified_commit"] == COMMIT
                raise Interrupted()
        return original(request)

    value.api._transport = httpx.MockTransport(handler)
    with pytest.raises(Interrupted):
        value.run(IDS, COMMIT)
    assert snapshots[-1]["status"] == "DEPLOYING_JOBS"
    assert [row["status"] for row in snapshots[-1]["deployments"]] == ["live", "SUBMITTING"]
    assert not provider.public_calls


@pytest.mark.parametrize("origin", ["http://milo.onrender.com", "https://user:secret@milo.onrender.com",
                                    "https://milo.onrender.com.attacker.test", "https://milo.onrender.com/path",
                                    "https://milo.onrender.com?token=secret", "https://milo.onrender.com#secret",
                                    "https://milo.onrender.com:444", "https://milo.onrender.com:notaport",
                                    "https://[invalid.onrender.com"])
def test_public_origin_is_https_without_credentials_or_url_state(release, origin):
    value, provider, _ = release
    provider.services["WEB"]["serviceDetails"]["url"] = origin
    with pytest.raises(ReleaseFailure, match="untrusted_public_origin"):
        value.run(IDS, COMMIT)
    assert all(request.method == "GET" for request in provider.calls)


def test_public_readiness_wrong_release_fails_immediately_without_retry(release):
    value, provider, clock = release
    provider.public_ready["release_commit"] = "b" * 40
    with pytest.raises(ReleaseFailure, match="public_release_commit_mismatch"):
        value.run(IDS, COMMIT)
    assert clock.now == 0
    assert len(provider.public_calls) == 2
    assert "public_url" not in value.report


def test_public_liveness_must_belong_to_milo_and_is_bounded(release):
    value, provider, clock = release
    provider.public_live = {"status": "ok", "service": "unrelated-service"}
    with pytest.raises(ReleaseFailure, match="public_liveness_not_verified"):
        value.run(IDS, COMMIT)
    assert clock.now == 90
    assert "public_url" not in value.report


def test_provider_redirect_fails_before_mutation_and_does_not_follow_location(release):
    value, provider, _ = release
    original = provider.api_handler

    def handler(request):
        original(request)
        return httpx.Response(302, headers={"Location": "https://attacker.example.test/private"})

    value.api._transport = httpx.MockTransport(handler)
    with pytest.raises(ReleaseFailure, match="http_302"):
        value.run(IDS, COMMIT)
    assert len(provider.calls) == 1
    assert provider.calls[0].url.host == "api.render.com"
    assert provider.calls[0].method == "GET"


@pytest.mark.parametrize("ready", [{"status": "unavailable", "service": "milo-web", "release_commit": COMMIT},
                                   {"status": "ready", "service": "other", "release_commit": COMMIT}])
def test_public_dependency_readiness_failure_retries_only_get_with_bounded_deadline(release, ready):
    value, provider, clock = release
    provider.public_ready = ready
    with pytest.raises(ReleaseFailure, match="private_dependencies_not_ready"):
        value.run(IDS, COMMIT)
    assert clock.now == 90
    assert sum(request.method == "POST" for request in provider.calls) == 4
    assert value.report["status"] == "FAIL_OR_UNCONFIRMED"


def test_public_redirect_is_not_followed_or_used_to_expose_provider_credentials(release):
    value, provider, clock = release
    provider.public_ready = httpx.Response(302, headers={"Location": "https://attacker.example.test/private"})
    with pytest.raises(ReleaseFailure, match="http_302"):
        value.run(IDS, COMMIT)
    assert clock.now == 90
    assert all(request.url.host == "milo-synthetic.onrender.com" for request in provider.public_calls)
    assert all("authorization" not in request.headers for request in provider.public_calls)


@pytest.mark.parametrize("response,reason", [
    (httpx.Response(500, text="synthetic-credential"), "http_500"),
    (httpx.Response(200, content=b"x" * 65537), "response_too_large"),
    (httpx.Response(200, json=[]), "invalid_response_shape"),
    (httpx.Response(200, text="not-json-synthetic-secret"), "request_not_confirmed"),
])
def test_provider_responses_are_bounded_and_errors_contain_no_response_data(response, reason):
    with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as client:
        with pytest.raises(ReleaseFailure, match=reason) as error:
            release_module.bounded_json(client, "GET", "https://api.render.com/v1/services/synthetic")
    assert "synthetic-credential" not in str(error.value)
    assert "synthetic-secret" not in str(error.value)


def test_cli_keeps_provider_authorization_off_public_client_and_disables_redirects(monkeypatch, tmp_path):
    provider = Provider()
    actual_client = httpx.Client
    client_options = []

    def client(**kwargs):
        client_options.append(kwargs)
        handler = provider.api_handler if "base_url" in kwargs else provider.public_handler
        return actual_client(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(release_module.httpx, "Client", client)
    monkeypatch.setenv("RENDER_API_KEY", "synthetic-provider-key")
    for role, service_id in IDS.items():
        monkeypatch.setenv(f"RENDER_SERVICE_ID_{role}", service_id)
    output = tmp_path / "report.json"
    monkeypatch.setattr("sys.argv", ["render-release", "--commit", COMMIT, "--output", str(output)])
    release_module.main()
    assert json.loads(output.read_text())["status"] == "PASS"
    assert all(kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
               for kwargs in client_options)
    assert "headers" not in client_options[1]
    assert "synthetic-provider-key" not in output.read_text()


def test_cli_persists_uncertain_submission_and_exits_failure_without_post_retry(monkeypatch, tmp_path, capsys):
    provider = Provider()
    provider.submissions["JOBS"] = httpx.ReadTimeout("synthetic-private-secret")
    actual_client = httpx.Client

    def client(**kwargs):
        handler = provider.api_handler if "base_url" in kwargs else provider.public_handler
        return actual_client(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(release_module.httpx, "Client", client)
    monkeypatch.setenv("RENDER_API_KEY", "synthetic-provider-key")
    for role, service_id in IDS.items():
        monkeypatch.setenv(f"RENDER_SERVICE_ID_{role}", service_id)
    output = tmp_path / "nested" / "report.json"
    monkeypatch.setattr("sys.argv", ["render-release", "--commit", COMMIT, "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        release_module.main()
    assert error.value.code == 1
    report = json.loads(output.read_text())
    assert report["status"] == "FAIL_OR_UNCONFIRMED"
    assert report["reason_code"] == "request_not_confirmed"
    assert [row["status"] for row in report["deployments"]] == ["live", "SUBMISSION_NOT_CONFIRMED"]
    assert sum(request.method == "POST" for request in provider.calls) == 2
    printed = capsys.readouterr().out
    for forbidden in ["synthetic-provider-key", "synthetic-private-secret"]:
        assert forbidden not in output.read_text()
        assert forbidden not in printed
