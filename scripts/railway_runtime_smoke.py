"""Exercise Railway API startup and overload bounds in a disposable container.

This validates configuration behavior, not a 50,000-user capacity claim. The
empty test database and disabled providers never touch customer data or sends.
"""

import argparse
import json
import socket
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx


def docker(*arguments):
    result = subprocess.run(["docker", *arguments], capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise RuntimeError(f"Disposable container operation failed: {arguments[0]}")
    return result.stdout.strip()


def run(image, manifest_path):
    manifest = json.loads(Path(manifest_path).read_text())
    deploy = manifest["deploy"]
    assert deploy["numReplicas"] == 1
    name = f"milo-railway-runtime-{uuid4().hex[:12]}"
    container_port = 8123
    admission_limit = 64
    probe_connections = admission_limit + 8
    sockets = []
    command = deploy["preDeployCommand"] + " && exec " + deploy["startCommand"]
    try:
        docker("run", "--detach", "--name", name,
               "--publish", f"127.0.0.1::{container_port}",
               "--env", "ENVIRONMENT=test", "--env", "ALLOW_DEV_AUTH=false",
               "--env", "SESSION_SECURE=false", "--env", "MODEL_PROVIDER=disabled",
               "--env", "ENABLE_EXTERNAL_SENDS=false", "--env", f"PORT={container_port}",
               "--env", "DATABASE_URL=sqlite:///.local/railway-runtime-smoke.db",
               image, "/bin/sh", "-c", command)
        image_id = docker("inspect", "--format", "{{.Image}}", name)
        port = int(docker("port", name, f"{container_port}/tcp").rsplit(":", 1)[1])
        assert docker("inspect", "--format", "{{.Config.User}}", name) == "10001:10001"
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5, trust_env=False) as client:
            deadline = time.monotonic() + 25
            while True:
                try:
                    if client.get("/health/ready").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError("Manifest startup did not become ready")
                time.sleep(0.05)
            assert client.get("/health/live").status_code == 200
            probe = json.loads(docker(
                "exec", name, "/app/.venv/bin/python", "-c",
                "import json,os,urllib.request; from pathlib import Path; "
                "arguments=Path('/proc/1/cmdline').read_bytes().split(b'\\0'); "
                "opener=urllib.request.build_opener(urllib.request.ProxyHandler({})); "
                f"response=opener.open('http://[::1]:{container_port}/health/live',timeout=5); "
                "print(json.dumps({'uid':os.getuid(),'ipv6_status':response.status,"
                "'admission_limit':int(arguments[arguments.index(b'--limit-concurrency')+1]),"
                "'workers':int(arguments[arguments.index(b'--workers')+1])}))"))
            assert probe == {"uid": 10001, "ipv6_status": 200, "admission_limit": admission_limit, "workers": 1}
            # Send actual HTTP requests: a Docker forwarding layer need not
            # expose idle, half-open host sockets to the application server.
            for _ in range(probe_connections):
                sockets.append(socket.create_connection(("127.0.0.1", port), timeout=5))
            request_bytes = b"GET /health/live HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n"
            for connection in sockets:
                connection.sendall(request_bytes)
            statuses = []
            for connection in sockets:
                status_line = b""
                while b"\r\n" not in status_line and len(status_line) < 4096:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    status_line += chunk
                statuses.append(int(status_line.split(b" ", 2)[1]))
            assert set(statuses) <= {200, 503} and 503 in statuses
            for connection in sockets:
                connection.close()
            sockets.clear()
            deadline = time.monotonic() + 5
            while client.get("/health/ready").status_code != 200:
                if time.monotonic() > deadline:
                    raise RuntimeError("Admission did not recover after releasing connections")
                time.sleep(0.05)
        started = time.monotonic()
        docker("stop", "--time", "35", name)
        shutdown_seconds = time.monotonic() - started
        state = json.loads(docker("inspect", "--format", "{{json .State}}", name))
        assert not state["Running"] and not state["OOMKilled"]
        assert state["ExitCode"] in {0, 143}
        return {"mode": "disposable_manifest_runtime_configuration",
                "built_image": image, "built_image_id": image_id,
                "non_root_uid": probe["uid"], "configured_port_honored": True,
                "ipv4_readiness": True, "ipv6_http": True,
                "configured_admission_limit": admission_limit,
                "admission_probe_connections": probe_connections,
                "overload_status": 503, "overload_rejections": statuses.count(503),
                "admission_recovered": True,
                "shutdown_exit_code": state["ExitCode"],
                "shutdown_seconds": round(shutdown_seconds, 3),
                "external_provider_calls": 0, "capacity_50000_users": "NOT_MEASURED"}
    finally:
        for connection in sockets:
            connection.close()
        subprocess.run(["docker", "rm", "--force", name], capture_output=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="relationship-assistant-api:local")
    parser.add_argument("--manifest", default="infra/railway-api.json")
    arguments = parser.parse_args()
    print(json.dumps(run(arguments.image, arguments.manifest), indent=2))
