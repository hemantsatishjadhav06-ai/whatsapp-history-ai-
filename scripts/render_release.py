"""Deploy one tested Git commit to pre-provisioned Milo Render services.

Credentials come from protected environment variables. Reports contain only release
metadata; failed/uncertain submissions never retry a POST or claim a hosted success.
"""
import argparse
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import httpx

API_ORIGIN = 'https://api.render.com/v1/'
REPO = 'https://github.com/hemantsatishjadhav06-ai/whatsapp-history-ai-'
ROLES = {'API': 'private_service', 'JOBS': 'background_worker',
         'RETENTION': 'background_worker', 'WEB': 'web_service'}
PENDING = {'created', 'queued', 'build_in_progress', 'pre_deploy_in_progress', 'update_in_progress'}
FAILED = {'build_failed', 'pre_deploy_failed', 'update_failed', 'canceled', 'deactivated'}


class ReleaseFailure(RuntimeError):
    """Messages are fixed codes, never upstream response or credential text."""


def public_origin(value):
    if not isinstance(value, str):
        raise ReleaseFailure('untrusted_public_origin')
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ReleaseFailure('untrusted_public_origin') from None
    if (parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith('.onrender.com')
            or parsed.username or parsed.password or port not in {None, 443}
            or parsed.path not in {'', '/'} or parsed.query or parsed.fragment):
        raise ReleaseFailure('untrusted_public_origin')
    return f'https://{parsed.hostname}'


def bounded_json(client, method, url, **kwargs):
    try:
        with client.stream(method, url, **kwargs) as response:
            if response.status_code not in {200, 201, 202}:
                raise ReleaseFailure(f'http_{response.status_code}')
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > 65536:
                    raise ReleaseFailure('response_too_large')
            value = json.loads(content)
            if not isinstance(value, dict):
                raise ReleaseFailure('invalid_response_shape')
            return value
    except (httpx.HTTPError, ValueError):
        # A POST network failure can be accepted-but-unconfirmed. Do not retry it.
        raise ReleaseFailure('request_not_confirmed') from None


class RenderRelease:
    def __init__(self, api, public, *, clock=time.monotonic, sleep=time.sleep, timeout=1800, interval=10,
                 record=None):
        self.api, self.public = api, public
        self.clock, self.sleep = clock, sleep
        self.timeout, self.interval = timeout, interval
        self.record = record
        self.report = {'status': 'NOT_STARTED', 'deployments': [], 'external_provider_calls': 0}

    def checkpoint(self):
        if self.record:
            self.record(self.report)

    def preflight(self, ids):
        if (set(ids) != set(ROLES) or not all(isinstance(value, str) for value in ids.values())
                or len(set(ids.values())) != 4):
            raise ReleaseFailure('four_distinct_services_required')
        services = {}
        for role, service_id in ids.items():
            if not re.fullmatch(r'srv-[a-z0-9]{8,64}', service_id):
                raise ReleaseFailure('invalid_service_identifier')
            service = bounded_json(self.api, 'GET', f'services/{service_id}')
            details = service.get('serviceDetails', {})
            repo = service.get('repo')
            if not isinstance(details, dict) or not isinstance(repo, str):
                raise ReleaseFailure(f'{role.lower()}_service_configuration_mismatch')
            if (service.get('id') != service_id or service.get('type') != ROLES[role]
                    or repo.removesuffix('.git') != REPO
                    or service.get('branch') != 'main' or service.get('autoDeploy') != 'no'
                    or service.get('suspended') != 'not_suspended'
                    or not isinstance(service.get('ownerId'), str) or not service['ownerId']
                    or (service.get('environmentId') is not None and not isinstance(service['environmentId'], str))
                    or service.get('rootDir') not in ('', None)
                    or details.get('runtime', details.get('env')) != 'docker'):
                raise ReleaseFailure(f'{role.lower()}_service_configuration_mismatch')
            services[role] = service
        owners = {service.get('ownerId') for service in services.values()}
        environments = {service.get('environmentId') for service in services.values()}
        if len(owners) != 1 or not next(iter(owners)) or len(environments) != 1:
            raise ReleaseFailure('services_do_not_share_owner_and_environment')
        origin = public_origin(services['WEB']['serviceDetails'].get('url', ''))
        return origin

    def deploy(self, role, service_id, commit):
        row = {'role': role, 'service_id': service_id, 'status': 'SUBMITTING'}
        self.report['deployments'].append(row)
        self.checkpoint()
        # Exactly one POST. Lost acknowledgment is preserved as uncertain.
        try:
            deployment = bounded_json(self.api, 'POST', f'services/{service_id}/deploys',
                                      json={'commitId': commit, 'clearCache': 'do_not_clear'})
        except ReleaseFailure:
            row['status'] = 'SUBMISSION_NOT_CONFIRMED'
            self.checkpoint()
            raise
        deploy_id = deployment.get('id', '')
        if not isinstance(deploy_id, str) or not re.fullmatch(r'dep-[a-z0-9]{8,64}', deploy_id):
            row['status'] = 'SUBMISSION_NOT_CONFIRMED'
            raise ReleaseFailure('invalid_deploy_identifier')
        row['deploy_id'] = deploy_id
        self.checkpoint()
        deadline = self.clock() + self.timeout
        while True:
            status = deployment.get('status')
            if not isinstance(status, str):
                row['status'] = 'UNKNOWN'
                raise ReleaseFailure(f'{role.lower()}_unknown_deployment_status')
            row['status'] = status if status in PENDING | FAILED | {'live'} else 'UNKNOWN'
            self.checkpoint()
            commit_record = deployment.get('commit') or {}
            if not isinstance(commit_record, dict):
                raise ReleaseFailure(f'{role.lower()}_commit_not_verified')
            returned_commit = commit_record.get('id')
            if returned_commit and returned_commit != commit:
                raise ReleaseFailure(f'{role.lower()}_commit_mismatch')
            if status == 'live':
                if returned_commit != commit:
                    raise ReleaseFailure(f'{role.lower()}_commit_not_verified')
                row['verified_commit'] = commit
                self.checkpoint()
                return
            if status in FAILED:
                raise ReleaseFailure(f'{role.lower()}_deployment_failed')
            if status not in PENDING:
                raise ReleaseFailure(f'{role.lower()}_unknown_deployment_status')
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise ReleaseFailure(f'{role.lower()}_deployment_timeout')
            self.sleep(min(self.interval, remaining))
            deployment = bounded_json(self.api, 'GET', f'services/{service_id}/deploys/{deploy_id}')

    def verify_public(self, origin, commit):
        deadline = self.clock() + 90
        while True:
            try:
                live = bounded_json(self.public, 'GET', origin + '/healthz')
                ready = bounded_json(self.public, 'GET', origin + '/readyz')
                if live.get('service') != 'milo-web' or live.get('status') != 'ok':
                    raise ReleaseFailure('public_liveness_not_verified')
                if ready.get('service') != 'milo-web' or ready.get('status') != 'ready':
                    raise ReleaseFailure('private_dependencies_not_ready')
                if ready.get('release_commit') != commit:
                    raise ReleaseFailure('public_release_commit_mismatch')
                return
            except ReleaseFailure as error:
                if str(error) == 'public_release_commit_mismatch' or self.clock() >= deadline:
                    raise
                self.sleep(min(5, deadline - self.clock()))

    def run(self, ids, commit):
        if not re.fullmatch(r'[a-f0-9]{40}', commit):
            self.report.update(status='FAIL_OR_UNCONFIRMED', reason_code='exact_git_commit_required')
            raise ReleaseFailure('exact_git_commit_required')
        self.report.update(status='PREFLIGHT', requested_commit=commit)
        self.checkpoint()
        try:
            origin = self.preflight(ids)
            for role in ROLES:
                self.report['status'] = f'DEPLOYING_{role}'
                self.deploy(role, ids[role], commit)
            self.verify_public(origin, commit)
            self.report.update(status='PASS', public_url=origin, private_dependencies_ready=True)
            self.checkpoint()
            return self.report
        except ReleaseFailure as error:
            self.report.update(status='FAIL_OR_UNCONFIRMED', reason_code=str(error))
            self.checkpoint()
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--output', type=Path, default=Path('.local/render-release.json'))
    args = parser.parse_args()
    token = os.getenv('RENDER_API_KEY', '')
    ids = {role: os.getenv(f'RENDER_SERVICE_ID_{role}', '') for role in ROLES}
    if not token:
        raise SystemExit('RENDER_API_KEY is required in protected runtime settings')
    timeout = httpx.Timeout(20, connect=5)
    def record(report):
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + '.tmp')
        temporary.write_text(json.dumps(report, indent=2) + '\n')
        temporary.replace(args.output)

    with httpx.Client(base_url=API_ORIGIN, headers={'Authorization': f'Bearer {token}'},
                      timeout=timeout, follow_redirects=False, trust_env=False) as api:
        with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as public:
            release = RenderRelease(api, public, record=record)
            try:
                release.run(ids, args.commit)
            except ReleaseFailure:
                pass
            finally:
                record(release.report)
                print(json.dumps(release.report))
            if release.report['status'] != 'PASS':
                raise SystemExit(1)


if __name__ == '__main__':
    main()
