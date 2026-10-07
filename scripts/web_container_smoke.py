"""Built Next container acceptance with a disposable authenticated Python backend.

Uses synthetic development authentication only in the temporary test backend.
The production web image and Railway API configuration never enable dev login.
"""
import argparse
import json
import re
import secrets
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

import httpx
import uvicorn
from cryptography.fernet import Fernet

from assistant.auth import NONCE_COOKIE, SESSION_COOKIE
from assistant.config import Settings
from assistant.db import Base
from assistant.main import create_app


def docker(*arguments):
    result = subprocess.run(["docker", *arguments], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"Disposable container operation failed: {arguments[0]}")
    return result.stdout.strip()


def checked(response):
    if not response.is_success:
        raise RuntimeError(f"Synthetic HTTP acceptance failed: {response.request.method} "
                           f"{response.request.url.path}: {response.status_code}")
    return response.json()


def run(image):
    name = f"milo-web-smoke-{uuid4().hex[:12]}"
    Path(".local").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="web-container-", dir=".local") as directory:
        api_socket = socket.socket()
        api_socket.bind(("0.0.0.0", 0))
        api_port = api_socket.getsockname()[1]
        settings = Settings(_env_file=None, environment="test", allow_dev_auth=True, session_secure=False,
                            database_url=f"sqlite:///{Path(directory).resolve()}/smoke.db",
                            encryption_key=Fernet.generate_key().decode(), internal_service_token=secrets.token_urlsafe(32),
                            model_provider="disabled", enable_external_sends=False,
                            google_client_id="", google_android_client_id="", google_ios_client_id="",
                            connector_gateway_url="", connector_gateway_token="")
        app = create_app(settings)
        Base.metadata.create_all(app.state.engine)
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [api_socket]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if time.monotonic() > deadline:
                    raise RuntimeError("Disposable backend did not start")
                time.sleep(0.02)
            public_origin = "https://milo-synthetic.example.test"
            docker("run", "--rm", "--detach", "--name", name, "--publish", "127.0.0.1::3000",
                   "--add-host", "host.docker.internal:host-gateway",
                   "--env", f"BACKEND_URL=http://host.docker.internal:{api_port}",
                   "--env", f"PUBLIC_APP_ORIGIN={public_origin}", image)
            port = docker("port", name, "3000/tcp").rsplit(":", 1)[1]
            origin = f"http://127.0.0.1:{port}"
            settings.allowed_origins = origin + "," + public_origin
            with httpx.Client(base_url=origin, timeout=10, trust_env=False) as web:
                deadline = time.monotonic() + 20
                while True:
                    try:
                        health = web.get("/healthz")
                        if health.is_success:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() > deadline:
                        raise RuntimeError("Built Next container did not become healthy")
                    time.sleep(0.05)
                assert docker("inspect", "--format", "{{.Config.User}}", name) == "10001:10001"
                image_id = docker("inspect", "--format", "{{.Image}}", name)
                page = web.get("/")
                assert page.status_code == 200
                assert "Milo" in page.text
                assets = re.findall(r'(?:src|href)="(/_next/static/[^"\s]+\.js)"', page.text)
                assert assets and web.get(assets[0]).status_code == 200
                assert web.get("/milo.svg").status_code == 200
                config = checked(web.get("/api/auth/config"))
                assert config["backend_configured"] is True
                assert config["google_configured"] is False
                assert web.get("/api/internal/connector-events").status_code == 404
                assert web.post("/api/auth/dev", json={"email": "browser@example.invalid"}).status_code == 404
                assert web.get("/api/me").status_code == 401
                nonce = checked(web.get("/api/auth/nonce"))
                assert nonce["nonce"]
                assert any(cookie.name == NONCE_COOKIE and cookie.path == "/api/auth" for cookie in web.cookies.jar)
                with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", trust_env=False) as api:
                    login = checked(api.post("/auth/dev", json={"email": "container-owner@example.invalid"}))
                    api.headers["X-CSRF-Token"] = login["csrf_token"]
                    workspace = checked(api.post("/workspaces", json={"name": "Synthetic container", "timezone": "UTC"}))
                    cookie = api.cookies.get(SESSION_COOKIE)
                    assert cookie
                    web.cookies.set(SESSION_COOKIE, cookie, domain="127.0.0.1", path="/")
                assert checked(web.get("/api/me"))["email"] == "container-owner@example.invalid"
                snapshot = checked(web.get("/api/ui/bootstrap"))
                assert snapshot["workspace"]["id"] == workspace["id"]
                token = checked(web.get("/api/auth/csrf", headers={"Sec-Fetch-Site": "same-origin"}))["csrf_token"]
                csrf_headers = {"X-CSRF-Token": token, "Origin": public_origin, "Sec-Fetch-Site": "same-origin"}
                pause = checked(web.post("/api/pause-all", params={"workspace_id": workspace["id"]}, headers=csrf_headers))
                assert pause["paused"] is True
                cross_site = web.post("/api/resume-all", params={"workspace_id": workspace["id"]}, headers={
                    "X-CSRF-Token": token, "Origin": "https://attacker.example.invalid", "Sec-Fetch-Site": "cross-site"})
                assert cross_site.status_code == 403
                forwarded = {"X-CSRF-Token": token, "Origin": public_origin, "Sec-Fetch-Site": "same-origin",
                             "Host": "milo-synthetic.example.test", "X-Forwarded-Host": "milo-synthetic.example.test",
                             "X-Forwarded-Proto": "https"}
                # Forwarded TLS/Host simulates Railway ingress; explicitly transfer
                # the test session because httpx's cookie domain is still loopback.
                forwarded["Cookie"] = f"{SESSION_COOKIE}={cookie}"
                resumed = checked(web.post("/api/resume-all", params={"workspace_id": workspace["id"]}, headers=forwarded))
                assert resumed["paused"] is False
                return {"built_image": image, "built_image_id": image_id,
                        "non_root": True, "standalone_assets": True,
                        "real_private_backend_proxy": True, "nonce_cookie_path": "/api/auth",
                        "authenticated_snapshot": True, "csrf_protected_controls": True,
                        "cross_site_blocked": True, "forwarded_https_origin": True,
                        "browser_dev_and_internal_routes_blocked": True, "external_provider_calls": 0}
        finally:
            subprocess.run(["docker", "rm", "--force", name], capture_output=True)
            server.should_exit = True
            thread.join(timeout=5)
            api_socket.close()
            app.state.engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="milo-web:local")
    arguments = parser.parse_args()
    print(json.dumps(run(arguments.image), indent=2))
