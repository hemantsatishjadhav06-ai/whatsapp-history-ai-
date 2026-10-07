FROM ghcr.io/astral-sh/uv:0.12.19 AS uv
FROM python:3.12.11-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_SYSTEM_CERTS=true PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY services/api services/api
RUN --mount=type=secret,id=trusted_ca,target=/run/secrets/trusted_ca \
    if [ -s /run/secrets/trusted_ca ]; then export SSL_CERT_FILE=/run/secrets/trusted_ca; fi; \
    uv sync --frozen --no-dev --no-editable
COPY alembic.ini ./
COPY db db
RUN groupadd --gid 10001 assistant && useradd --uid 10001 --gid 10001 --no-create-home assistant \
    && chmod -R a+rX /app/.venv /app/services /app/db \
    && chmod a+r /app/pyproject.toml /app/uv.lock /app/alembic.ini \
    && mkdir -m 700 .local && chown -R 10001:10001 .local
USER 10001:10001
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "assistant.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
