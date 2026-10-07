.PHONY: bootstrap dev test lint gateway-check qr-check qr-session image migrate infra infra-events infra-workflows worker registrar relay demo load-smoke actions jobs retention bridge-smoke web web-build web-test mobile mobile-export
export UV_CACHE_DIR ?= $(CURDIR)/.local/uv-cache
export UV_PYTHON_INSTALL_DIR ?= $(CURDIR)/.local/python
export BUILDX_CONFIG ?= $(CURDIR)/.local/buildx

bootstrap:
	bash scripts/dev-bootstrap.sh

dev:
	uv run --frozen uvicorn assistant.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log

test:
	uv run --frozen pytest -q

lint:
	uv run --frozen ruff check services tests scripts

gateway-check:
	npm --prefix services/connector-gateway run check

qr-check:
	npm --prefix services/whatsapp-session run check

qr-session:
	npm --prefix services/whatsapp-session start

image:
	docker build -f services/api/Dockerfile -t relationship-assistant-api:local .

migrate:
	uv run --frozen alembic upgrade head

infra:
	docker compose --env-file .env -f infra/compose.yaml up -d --wait

infra-events:
	docker compose --env-file .env -f infra/compose.yaml --profile events up -d --wait

infra-workflows:
	docker compose --env-file .env -f infra/compose.yaml --profile workflows up -d --wait

worker:
	uv run --frozen python -m assistant.workflows worker

registrar:
	uv run --frozen python -m assistant.workflows registrar

relay:
	uv run --frozen python -m assistant.relay

demo:
	uv run --frozen python scripts/demo.py

load-smoke:
	uv run --frozen python scripts/load_smoke.py

actions:
	uv run --frozen python -m assistant.actions

jobs:
	uv run --frozen python -m assistant.jobs

retention:
	uv run --frozen python -m assistant.lifecycle

bridge-smoke:
	uv run --frozen python scripts/bridge_smoke.py

web:
	npm run dev --workspace=@milo/web

web-build:
	npm run build --workspace=@milo/web

web-test:
	npm run test:web

mobile:
	npm run dev --workspace=@milo/mobile

mobile-export:
	npm run mobile:export
