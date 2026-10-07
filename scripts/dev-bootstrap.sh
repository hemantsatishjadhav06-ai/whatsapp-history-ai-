#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.local/uv-cache}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$PWD/.local/python}"

# Use this task's existing checkout. Do not create a worktree for cloud startup.
mkdir -p .local
chmod 700 .local
command -v uv >/dev/null || { echo "Install uv with verified upstream instructions first." >&2; exit 1; }
uv sync --frozen --group dev
if [[ -f services/connector-gateway/package-lock.json ]]; then
  command -v node >/dev/null || { echo "Node 24 is required for the connector contract." >&2; exit 1; }
  node -e "if (Number(process.versions.node.split('.')[0]) !== 24) process.exit(1)" \
    || { echo "Select Node 24 before installing the connector contract." >&2; exit 1; }
  command -v npm >/dev/null || { echo "npm is required for the connector contract." >&2; exit 1; }
  npm ci --prefix services/connector-gateway --cache "$PWD/.local/npm-cache" --ignore-scripts
fi
if [[ -f package-lock.json ]]; then
  npm ci --cache "$PWD/.local/npm-cache" --ignore-scripts
fi
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
fi
local_database=$(uv run --frozen python -c 'from assistant.config import Settings; from sqlalchemy.engine import make_url; u=make_url(Settings().database_url); print("postgres" if u.get_backend_name()=="postgresql" and u.host in {"localhost","127.0.0.1"} else "other")')
if [[ "$local_database" == "postgres" ]]; then
  docker compose --env-file .env -f infra/compose.yaml up -d --wait postgres redis
fi
uv run --frozen alembic upgrade head
echo "Dependencies and migrations ready. Run make dev for the API and make web for Milo."
