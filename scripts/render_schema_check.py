"""Check a Render Blueprint against the official schema without logging values.

Run with isolated, pinned jsonschema/PyYAML tooling; no application dependencies
are added. A local trusted schema can be supplied for offline confirmation.
"""

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import signal
from urllib.request import Request, urlopen

import jsonschema
import yaml

SCHEMA_URL = "https://render.com/schema/render.yaml.json"
MAX_SCHEMA_BYTES = 256 * 1024
MAX_BLUEPRINT_BYTES = 1024 * 1024
FETCH_TIMEOUT_SECONDS = 10


@contextmanager
def fetch_deadline():
    # GitHub's Linux runners and the cloud workspace support a hard deadline,
    # including DNS and a response that drips bytes within a socket timeout.
    if not hasattr(signal, "setitimer"):
        raise RuntimeError("A hard fetch deadline is unavailable on this platform")

    def timed_out(_signal, _frame):
        raise TimeoutError("Official schema fetch exceeded its deadline")

    previous_handler = signal.signal(signal.SIGALRM, timed_out)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, FETCH_TIMEOUT_SECONDS)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)


def limited_file(path, limit):
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Input exceeds its size limit")
    return data


def official_schema():
    request = Request(SCHEMA_URL, headers={"User-Agent": "milo-render-schema-check/1"})
    with fetch_deadline(), urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
        if response.geturl() != SCHEMA_URL:
            raise ValueError("Official schema redirects are not accepted")
        data = response.read(MAX_SCHEMA_BYTES + 1)
    if len(data) > MAX_SCHEMA_BYTES:
        raise ValueError("Official schema exceeds its size limit")
    return data


def inspect_schema(node, known_fields):
    if isinstance(node, dict):
        reference = node.get("$ref")
        if reference is not None and (not isinstance(reference, str) or not reference.startswith("#")):
            raise ValueError("Only internal schema references are accepted")
        properties = node.get("properties")
        if isinstance(properties, dict):
            known_fields.update(properties)
        for value in node.values():
            inspect_schema(value, known_fields)
    elif isinstance(node, list):
        for value in node:
            inspect_schema(value, known_fields)


def error_path(error, known_fields):
    parts = ["$"]
    for segment in error.absolute_path:
        if isinstance(segment, int):
            parts.append(f"[{segment}]")
        else:
            parts.append(f".{segment}" if segment in known_fields else ".[field]")
    return "".join(parts)


def run(args):
    schema_data = limited_file(args.schema, MAX_SCHEMA_BYTES) if args.schema else official_schema()
    blueprint_data = limited_file(args.blueprint, MAX_BLUEPRINT_BYTES)
    schema = json.loads(schema_data)
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise ValueError("Expected a Draft 2020-12 schema")
    if schema.get("$id") != SCHEMA_URL:
        raise ValueError("Expected the official Render schema identifier")
    known_fields = set()
    inspect_schema(schema, known_fields)
    jsonschema.Draft202012Validator.check_schema(schema)
    blueprint = yaml.safe_load(blueprint_data)
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    count = 0
    errors = []
    for error in validator.iter_errors(blueprint):
        count += 1
        if len(errors) < 10:
            # ValidationError.message can contain the rejected secret value.
            # Log only public schema locations and the failed constraint name.
            errors.append({"path": error_path(error, known_fields), "constraint": error.validator})
    counts = {}
    if isinstance(blueprint, dict):
        services = blueprint.get("services", [])
        if isinstance(services, list):
            counts["services"] = len(services)
            allowed_types = {"web", "pserv", "worker", "redis", "keyvalue", "cron", "static"}
            counts["service_types"] = dict(Counter(
                item.get("type") if isinstance(item.get("type"), str) and item.get("type") in allowed_types else "other"
                for item in services if isinstance(item, dict)))
        for key in ("databases", "envVarGroups"):
            if isinstance(blueprint.get(key), list):
                counts[key] = len(blueprint[key])
    return {
        "status": "PASS" if count == 0 else "FAIL",
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "schema_source": SCHEMA_URL,
        "schema_read_mode": "local_trusted_file" if args.schema else "official_https",
        "schema_draft": schema["$schema"],
        "checker_versions": {"jsonschema": version("jsonschema"), "PyYAML": version("PyYAML")},
        "schema_bytes": len(schema_data),
        "schema_sha256": hashlib.sha256(schema_data).hexdigest(),
        "blueprint_bytes": len(blueprint_data),
        "blueprint_sha256": hashlib.sha256(blueprint_data).hexdigest(),
        "schema_size_limit_bytes": MAX_SCHEMA_BYTES,
        "fetch_deadline_seconds": FETCH_TIMEOUT_SECONDS,
        "resource_counts": counts,
        "error_count": count,
        "errors": errors,
        "provider_workspace_semantic_validation": "NOT_RUN",
        "provider_resource_creation": "NOT_RUN",
        "public_live_acceptance": "NOT_RUN",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", type=Path, help="Local trusted official schema JSON for offline confirmation")
    parser.add_argument("--blueprint", type=Path, default=Path("render.yaml"))
    parser.add_argument("--output", type=Path, help="Optional metadata-only JSON evidence file")
    args = parser.parse_args()
    try:
        result = run(args)
    except Exception:
        # URLs, connection information, YAML values and exception input are not
        # diagnostic fields. A failed fetch/parse/check always exits nonzero.
        result = {"status": "ERROR", "reason": "Schema fetch, input parsing or schema validation could not complete"}
    rendered = json.dumps(result, sort_keys=True, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n")
    print(rendered)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
