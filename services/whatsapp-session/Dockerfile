FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS runtime-trust
FROM node:24.19.0-bookworm-slim@sha256:a9f5f7c91a432850b2a8a7797adf5eadb6c733ceed61167806cee7ea7fbc29df
WORKDIR /app
COPY --from=runtime-trust /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY services/whatsapp-session/package.json services/whatsapp-session/package-lock.json ./
RUN set -eu; \
    export DEBIAN_FRONTEND=noninteractive; \
    npm ci --omit=dev --ignore-scripts --no-audit --no-fund; \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g; s|http://security.debian.org|https://security.debian.org|g' /etc/apt/sources.list.d/debian.sources; \
    apt-get --error-on=any -o Acquire::https::Timeout=30 -o Acquire::Retries=1 update; \
    apt-get -o Acquire::https::Timeout=30 -o Acquire::Retries=1 install -y --no-install-recommends ca-certificates; \
    apt-get -o Acquire::https::Timeout=30 -o Acquire::Retries=1 upgrade -y --no-install-recommends; \
    rm -rf /var/lib/apt/lists/* /root/.npm /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack /opt/yarn-v*; \
    rm -f /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/yarn /usr/local/bin/yarnpkg /usr/local/bin/corepack
COPY services/whatsapp-session/src ./src
COPY services/whatsapp-session/THIRD_PARTY_NOTICES.md ./THIRD_PARTY_NOTICES.md
COPY services/whatsapp-session/LICENSES ./LICENSES
RUN chmod -R a+rX /app
USER node
ENV NODE_ENV=production
EXPOSE 8091
CMD ["node", "src/runtime.ts"]
