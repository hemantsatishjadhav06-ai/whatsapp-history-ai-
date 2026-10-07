"""Validate the built non-root API image through real local HTTP requests."""

import json
import os
import secrets
import subprocess
import time
from uuid import uuid4

import httpx

from assistant.config import Settings
from demo import run_demo


def main():
    token = secrets.token_urlsafe(32)
    environment = os.environ.copy()
    environment["INTERNAL_SERVICE_TOKEN"] = token
    name = f"assistant-container-smoke-{uuid4().hex}"
    subprocess.run([
        "docker", "run", "-d", "--name", name, "-p", "127.0.0.1::8000",
        "--env", "ENVIRONMENT=test", "--env", "ALLOW_DEV_AUTH=true", "--env", "SESSION_SECURE=false",
        "--env", "MODEL_PROVIDER=mock", "--env", "ENABLE_EXTERNAL_SENDS=false",
        "--env", "INTERNAL_SERVICE_TOKEN", "relationship-assistant-api:local", "/bin/sh", "-c",
        "/app/.venv/bin/alembic upgrade head && exec /app/.venv/bin/uvicorn "
        "assistant.main:create_app --factory --host 0.0.0.0 --port 8000",
    ], env=environment, check=True, capture_output=True, text=True)
    try:
        address = subprocess.run(["docker", "port", name, "8000"], check=True,
                                 capture_output=True, text=True).stdout.strip()
        with httpx.Client(base_url=f"http://{address}", timeout=5) as client:
            deadline = time.monotonic() + 30
            while (remaining := deadline - time.monotonic()) > 0:
                try:
                    if client.get("/health/ready", timeout=min(2, remaining)).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(min(.25, max(0, deadline - time.monotonic())))
            else:
                raise TimeoutError("Synthetic API container did not become ready within 30 seconds")
            result = run_demo(client, Settings(internal_service_token=token))
            result["mode"] = "non_root_container_real_local_http_mock_transport"
            result["migration_and_readiness"] = "passed"
            print(json.dumps(result, indent=2))
    finally:
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)


if __name__ == "__main__":
    main()
