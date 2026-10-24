# OpenDots (CopilotKit's MIT-licensed agent workspace template), built from one pinned
# upstream commit so every deploy is reproducible. Only the application is built: the
# optional public-page browser and per-Dot computers are separate services not run here.
FROM node:24.19.0-bookworm-slim@sha256:a9f5f7c91a432850b2a8a7797adf5eadb6c733ceed61167806cee7ea7fbc29df AS build
ARG OPENDOTS_COMMIT=8091324942b86789ed0da7facbee855145d933b0
WORKDIR /src
RUN set -eu; \
    export DEBIAN_FRONTEND=noninteractive; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates git; \
    rm -rf /var/lib/apt/lists/*; \
    git init -q .; \
    git remote add origin https://github.com/CopilotKit/OpenDots.git; \
    git fetch -q --depth 1 origin "$OPENDOTS_COMMIT"; \
    git checkout -q FETCH_HEAD; \
    test "$(git rev-parse HEAD)" = "$OPENDOTS_COMMIT"; \
    npm ci --no-audit --no-fund; \
    npm run build; \
    npm prune --omit=dev --no-audit --no-fund; \
    rm -rf .git /root/.npm

FROM node:24.19.0-bookworm-slim@sha256:a9f5f7c91a432850b2a8a7797adf5eadb6c733ceed61167806cee7ea7fbc29df AS runtime
ENV NODE_ENV=production HOST=0.0.0.0 PORT=4310 DATABASE_PATH=/data/opendots.sqlite
WORKDIR /app
COPY --from=build /src/package.json /src/package-lock.json ./
COPY --from=build /src/node_modules ./node_modules
COPY --from=build /src/dist ./dist
RUN mkdir -p /data && chown node:node /data
USER node
EXPOSE 4310
CMD ["node", "dist/server/server/index.js"]
