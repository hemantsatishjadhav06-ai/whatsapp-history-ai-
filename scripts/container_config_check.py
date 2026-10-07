"""Check that each provider selects its tested, pinned non-root Dockerfile.

PyYAML is supplied by the pinned isolated CI tool environment; it is not an
application dependency. Runtime behavior and vulnerabilities are checked on
the four actual built images separately.
"""

import json
import re
from pathlib import Path

import yaml


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_dockerfile(path, *, mount_free):
    text = path.read_text()
    # Join Dockerfile continuations before examining executable instructions.
    logical = re.sub(r"\\\r?\n", " ", text)
    instructions = [line.strip() for line in logical.splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
    sources = [line.split()[1] for line in instructions if line.upper().startswith("FROM ")]
    require(sources and all(re.search(r"@sha256:[0-9a-f]{64}$", source) for source in sources),
            f"{path}: all base images must have immutable SHA-256 digests")
    users = [line.split(None, 1)[1] for line in instructions if line.upper().startswith("USER ")]
    require(users and users[-1] == "10001:10001", f"{path}: runtime must use UID/GID 10001")
    commands = [line.split(None, 1)[1] for line in instructions if line.upper().startswith("CMD ")]
    require(commands and isinstance(json.loads(commands[-1]), list), f"{path}: JSON runtime CMD required")
    if mount_free:
        require(not any(re.search(r"--mount\s*=", line) for line in instructions
                        if line.upper().startswith("RUN ")),
                f"{path}: Railway builder does not support RUN mounts")


def main():
    for legacy in ("railway.json", "railway.toml"):
        require(not Path(legacy).exists(),
                f"{legacy}: root defaults can override service-specific Railway build settings")
    targets = {
        "web": (Path("infra/railway-web.json"), "Dockerfile.web.railway"),
        "api": (Path("infra/railway-api.json"), "services/api/Dockerfile.railway"),
        "jobs": (Path("infra/railway-jobs.json"), "services/api/Dockerfile.railway"),
        "retention": (Path("infra/railway-retention.json"), "services/api/Dockerfile.railway"),
        "actions": (Path("infra/railway-actions.json"), "services/api/Dockerfile.railway"),
    }
    settings = json.loads(Path("infra/railway-service-settings.json").read_text())
    for name, (manifest_path, dockerfile) in targets.items():
        manifest = json.loads(manifest_path.read_text())
        require(manifest["build"]["builder"] == "DOCKERFILE", f"{manifest_path}: Docker builder required")
        require(manifest["build"]["dockerfilePath"] == dockerfile,
                f"{manifest_path}: wrong Railway Dockerfile")
        require(Path(dockerfile).is_file(), f"{manifest_path}: missing Dockerfile")
        if name in settings:
            configured = settings[name]
            require(configured["dockerfilePath"] == dockerfile,
                    f"Railway {name}: service settings select a different Dockerfile")
            for key, value in manifest["deploy"].items():
                if key == "preDeployCommand":
                    value = [value] if isinstance(value, str) else value
                require(configured.get(key) == value,
                        f"Railway {name}: {key} differs between manifest and service settings")

    render = yaml.safe_load(Path("render.yaml").read_text())
    expected_render = {
        "milo-web": "Dockerfile.web", "milo-api": "services/api/Dockerfile",
        "milo-jobs": "services/api/Dockerfile", "milo-retention": "services/api/Dockerfile",
    }
    services = {service["name"]: service for service in render["services"]}
    for name, dockerfile in expected_render.items():
        require(name in services and services[name]["runtime"] == "docker",
                f"Render {name}: Docker service missing")
        require(str(Path(services[name]["dockerfilePath"])) == dockerfile,
                f"Render {name}: wrong Render Dockerfile")

    for dockerfile in sorted(set(expected_render.values()) | {item[1] for item in targets.values()}):
        check_dockerfile(Path(dockerfile), mount_free=dockerfile.endswith(".railway"))
    print(json.dumps({"provider_dockerfile_selection": "passed", "pinned_non_root_images": 4,
                      "railway_mount_free_images": 2, "railway_manifests": len(targets),
                      "railway_service_settings": len(settings), "render_docker_services": len(expected_render),
                      "railway_root_legacy_config_absent": True}))


if __name__ == "__main__":
    main()
