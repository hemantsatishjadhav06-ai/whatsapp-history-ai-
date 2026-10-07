FROM ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 AS uv
FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_SYSTEM_CERTS=true PYTHONUNBUFFERED=1
WORKDIR /app
# One committed installation layer avoids duplicating .venv across COPY steps.
RUN --mount=type=bind,source=.,target=/src,ro \
    --mount=type=bind,from=uv,source=/uv,target=/usr/local/bin/uv,ro \
    --mount=type=secret,id=trusted_ca,target=/run/secrets/trusted_ca,mode=0444 \
    set -eu; \
    cp /src/pyproject.toml /src/uv.lock /src/alembic.ini ./; \
    mkdir -p services; \
    cp -a /src/services/api services/api; \
    cp -a /src/db db; \
    if [ -s /run/secrets/trusted_ca ]; then export SSL_CERT_FILE=/run/secrets/trusted_ca; fi; \
    uv sync --frozen --no-dev --no-editable; \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g; s|http://security.debian.org|https://security.debian.org|g' /etc/apt/sources.list.d/debian.sources; \
    if [ -s /run/secrets/trusted_ca ]; then \
      apt-get --error-on=any -o Acquire::https::CaInfo=/run/secrets/trusted_ca update; \
      apt-get -o Acquire::https::CaInfo=/run/secrets/trusted_ca upgrade -y --no-install-recommends; \
    else \
      apt-get --error-on=any update; apt-get upgrade -y --no-install-recommends; \
    fi; \
    rm -rf /var/lib/apt/lists/* /root/.cache/uv /usr/local/lib/python3.12/site-packages/pip /usr/local/lib/python3.12/site-packages/pip-*.dist-info /usr/local/lib/python3.12/ensurepip; \
    rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12; \
    groupadd --gid 10001 assistant; useradd --uid 10001 --gid 10001 --no-create-home assistant; \
    chmod -R a+rX /app/.venv /app/services /app/db; \
    chmod a+r /app/pyproject.toml /app/uv.lock /app/alembic.ini; \
    mkdir -m 700 .local; chown -R 10001:10001 .local
USER 10001:10001
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "assistant.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
