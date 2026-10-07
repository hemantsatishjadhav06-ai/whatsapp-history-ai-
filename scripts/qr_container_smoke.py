"""Exercise the real private QR image and PostgreSQL without contacting WhatsApp."""

import argparse
import base64
import json
import os
import secrets
import shlex
import subprocess
import time
from pathlib import Path
from uuid import uuid4

POSTGRES_IMAGE = "postgres:16-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea"


def docker_result(*args, env=None, timeout=60, check=True):
    try:
        return subprocess.run(["docker", *args], env=env, check=check, capture_output=True,
                              text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
        # Captured Docker/migration output may contain database or auth details.
        raise RuntimeError(f"QR container Docker {args[0]} operation failed") from None


def docker(*args, env=None, timeout=60):
    return docker_result(*args, env=env, timeout=timeout).stdout.strip()


def private_http(service, path, *, method="GET", body=None, authenticated=False, origin=None, ipv6=False):
    # Internal-only Docker bridges intentionally do not publish host ports.
    # Execute actual loopback HTTP inside the service network namespace instead.
    # The bearer comes from the existing container environment, never command args.
    config = json.dumps({"path": path, "method": method, "body": body, "authenticated": authenticated,
                         "origin": origin, "host": "[::1]" if ipv6 else "127.0.0.1"})
    script = f"const c={config};" + """
      const headers = c.body === null ? {} : {'Content-Type':'application/json'};
      if(c.authenticated) headers.Authorization='Bearer '+process.env.SESSION_GATEWAY_TOKEN;
      if(c.origin) headers.Origin=c.origin;
      const r=await fetch('http://'+c.host+':18091'+c.path,{method:c.method,headers,
        ...(c.body === null ? {} : {body:JSON.stringify(c.body)}),signal:AbortSignal.timeout(3000)});
      process.stdout.write(JSON.stringify({status:r.status,cache_control:r.headers.get('cache-control'),data:await r.json()}));
    """
    return json.loads(docker("exec", service, "node", "--input-type=module", "-e", script, timeout=5))


def main(image, api_image):
    identity = uuid4().hex
    network, database, service = [f"milo-qr-smoke-{identity}-{kind}" for kind in ("net", "pg", "session")]
    environment = os.environ.copy()
    environment.update(POSTGRES_PASSWORD=secrets.token_urlsafe(32),
                       SESSION_GATEWAY_TOKEN=secrets.token_urlsafe(32),
                       PYTHON_INTERNAL_TOKEN=secrets.token_urlsafe(32),
                       SESSION_ENCRYPTION_KEY=base64.b64encode(secrets.token_bytes(32)).decode())
    environment["DATABASE_URL"] = f"postgresql://postgres:{environment['POSTGRES_PASSWORD']}@{database}:5432/postgres"
    environment["PORT"] = "18091"
    manifest = json.loads(Path("infra/railway-whatsapp-session.json").read_text())
    command = shlex.split(manifest["deploy"]["startCommand"])
    assert command == ["node", "src/runtime.ts"], "QR provider must use the isolated runtime entrypoint"
    checks = []
    try:
        docker("network", "create", "--internal", network)
        # Isolated, disposable storage. No development or hosted database is touched.
        docker("run", "-d", "--name", database, "--network", network, "--env", "POSTGRES_PASSWORD",
               POSTGRES_IMAGE, env=environment)
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            result = docker_result("exec", database, "pg_isready", "-h", "127.0.0.1", "-U", "postgres", timeout=5, check=False)
            if result.returncode == 0:
                break
            time.sleep(.2)
        else:
            raise TimeoutError("Disposable QR PostgreSQL did not become ready")
        migration_environment = {**environment,
                                 "DATABASE_URL": environment["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://")}
        docker("run", "--rm", "--network", network, "--env", "DATABASE_URL", "--env", "ENVIRONMENT=test",
               "--env", "ALLOW_DEV_AUTH=false", "--env", "MODEL_PROVIDER=disabled", "--env", "ENABLE_EXTERNAL_SENDS=false",
               api_image, "/app/.venv/bin/python", "-m", "assistant.render_entrypoint", "migrate",
               env=migration_environment, timeout=120)
        checks.append("actual_api_migration_compatible_session_storage")
        docker("run", "-d", "--name", service, "--network", network, "--env", "PORT",
               "--env", "DATABASE_URL", "--env", "SESSION_GATEWAY_TOKEN", "--env", "SESSION_ENCRYPTION_KEY",
               "--env", "PYTHON_INTERNAL_TOKEN", "--env", "ENABLE_PERSONAL_WHATSAPP=false",
               "--env", "PYTHON_AUTHORITY_URL=http://127.0.0.1:1", image, *command, env=environment)
        user = docker("inspect", "--format", "{{.Config.User}}", service)
        assert user == "node", "QR acceptance requires its declared non-root user"
        actual_user = json.loads(docker("exec", service, "node", "-e",
                                      "process.stdout.write(JSON.stringify({uid:process.getuid(),gid:process.getgid()}))"))
        assert actual_user == {"uid": 1000, "gid": 1000}, "Pinned Node runtime must execute as its actual unprivileged UID"
        checks.append("actual_non_root_node_uid")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                result = private_http(service, "/readyz")
                if result["status"] == 200:
                    break
            except RuntimeError:
                pass
            time.sleep(.2)
        else:
            raise TimeoutError("Real QR image did not become ready")
        assert result["data"]["capacity_verified"] is False
        checks.append("actual_postgresql_readiness")
        assert private_http(service, "/healthz")["data"]["simulation"] is False
        checks.append("real_sdk_runtime_import")
        ipv6 = private_http(service, "/healthz", ipv6=True)
        assert ipv6["status"] == 200 and ipv6["data"]["simulation"] is False
        checks.append("railway_start_command_port_and_dual_stack")
        payload = {"schema_version": 1, "workspace_id": "owned-smoke", "connector_id": "owned-smoke",
                   "connector_fence": 1, "account_id": None}
        path = "/v1/sessions/start"
        assert private_http(service, path, method="POST", body=payload)["status"] == 401
        checks.append("unauthenticated_private_rpc_denied")
        response = private_http(service, path, method="POST", body=payload, authenticated=True, origin="https://example.test")
        assert response["status"] == 403 and response["data"]["reason_code"] == "BROWSER_INGRESS_FORBIDDEN"
        checks.append("browser_private_rpc_denied")
        response = private_http(service, path, method="POST", body=payload, authenticated=True)
        assert response["status"] == 503 and response["data"]["reason_code"] == "PERSONAL_QR_DISABLED"
        assert response["cache_control"] == "no-store"
        checks.append("disabled_linking_fails_closed_without_provider_socket")
        response = private_http(service, path, method="POST", body={**payload, "unexpected": True}, authenticated=True)
        assert response["status"] == 400
        checks.append("strict_private_protocol_validation")
        # Normal SIGTERM must drain the real service without an SDK socket.
        docker("stop", "--time", "35", service)
        assert docker("inspect", "--format", "{{.State.ExitCode}}", service) == "0"
        checks.append("normal_sigterm_drain")
        print(json.dumps({"mode": "real_non_root_qr_image_private_http_disposable_postgresql",
                          "image": image, "image_id": docker("inspect", "--format", "{{.Image}}", service),
                          "checks": checks, "count": len(checks), "provider_socket_opened": False,
                          "capacity_verified": False}, indent=2))
    finally:
        for name in (service, database):
            try:
                docker_result("rm", "--force", "--volumes", name, timeout=20, check=False)
            except RuntimeError:
                pass
        try:
            docker_result("network", "rm", network, timeout=20, check=False)
        except RuntimeError:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="milo-whatsapp-session:local")
    parser.add_argument("--api-image", default="relationship-assistant-api:railway")
    args = parser.parse_args()
    main(args.image, args.api_image)
