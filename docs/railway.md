# Railway deployment

The repository includes deployable web, API and optional schedule-worker containers. A configured manifest and a successful local build do not mean a Railway deployment exists. Deployment is complete only after Railway reports healthy services and the public URL passes acceptance checks.

Use one Railway project with a PostgreSQL service and these repository services. Keep each service's source root at the repository root, select its configuration file in Railway service settings, and connect `hemantsatishjadhav06-ai/whatsapp-history-ai-` on the published branch.

| Service | Config file | Runtime |
| --- | --- | --- |
| Web | `/railway.json` | Non-root Node 24, Next standalone app; health `/healthz` |
| API | `/infra/railway-api.json` | Non-root Python API; Alembic pre-deploy migration; health `/health/ready` |
| Jobs | `/infra/railway-jobs.json` | Optional private SQL polling worker for authorized jobs and scheduled intents |

The web service receives `BACKEND_URL`, the private API origin including its port, and `PUBLIC_APP_ORIGIN`, its exact public HTTPS origin. The proxy uses that explicit origin for browser writes instead of trusting forwarded headers to choose an origin. Browser requests use the web service's same-origin `/api` proxy, so private backend routing and session cookies stay server-side. Next.js reads Railway's runtime `PORT`; the API manifest also expands `PORT`. Generate a Railway domain for Web. Give API a public domain only if a verified webhook or direct mobile client needs it, with HTTPS and the same production authentication requirements.

Configure API and Jobs using Railway's protected runtime variables:

| Variable | Production value |
| --- | --- |
| `ENVIRONMENT` | `production` |
| `DATABASE_URL` | Reference the project's PostgreSQL connection variable |
| `ENCRYPTION_KEY` | A stable externally stored Fernet key, shared by API and Jobs |
| `INTERNAL_SERVICE_TOKEN` | A separate service-authentication secret |
| `SESSION_SECURE` | `true` |
| `ALLOW_DEV_AUTH` | `false` |
| `ALLOWED_ORIGINS` | The exact HTTPS web origin and other explicitly configured client origins |
| `GOOGLE_CLIENT_ID` | The owner's configured Google OAuth application client ID |
| `GOOGLE_ANDROID_CLIENT_ID`, `GOOGLE_IOS_CLIENT_ID` | Installed-app client IDs for each native platform that will be enabled |
| `MODEL_PROVIDER` | `disabled` until a verified model configuration is supplied |
| `ENABLE_EXTERNAL_SENDS` | `false` until the provider account and send capability are verified |

Keep secrets outside Git, image layers, build arguments and command output. API and Jobs must use the same encryption key; changing it without a migration makes existing encrypted data unreadable. Add the web HTTPS origin to Google OAuth's authorized origins. Publishing the frontend does not verify Google authentication, WhatsApp pairing, history sync or provider delivery.

Native clients use a separately configured HTTPS API origin (`EXPO_PUBLIC_API_URL`) and the installed-app Google client IDs above. Keep their Expo redirect and Google application configuration aligned with the released app. These public client settings do not contain session or service credentials. Leave native sign-in unavailable until its provider configuration and a real device login have been verified.

The API pre-deploy step applies the repository's Alembic migrations before traffic. Its startup override binds both IPv4 and IPv6, which supports Railway ingress and private service DNS; the installed Uvicorn version was verified with HTTP on both local address families. Deploy Jobs after API migrations succeed, using the same database and production settings. Jobs does not need a public domain. Its SQL timers work without Temporal. The existing Temporal worker, registrar and Kafka relay are optional independent processes; their development plaintext clients require supported production TLS/authentication configuration before connecting to external infrastructure.

For authenticated CLI deployment, the official Railway CLI supports `RAILWAY_TOKEN` for a project/environment token and `RAILWAY_API_TOKEN` for an account/workspace token. An OAuth session from `railway login` is also supported. A project token can select its own project/environment; explicit service selectors are still required for a monorepo. Use a secure environment secret for noninteractive cloud tasks instead of placing tokens in chat or shell arguments. CLI variable list commands print raw values and should not be used in shared logs.

After access is available, link or select the exact project/environment/service, upload the repository with `railway up --service <service> --environment <environment> --ci`, then inspect deployment status and generate the Web domain. `railway up --detach` only starts deployment; it does not establish that the site is healthy. For GitHub autodeploys, use `railway service source connect --repo hemantsatishjadhav06-ai/whatsapp-history-ai- --branch main --service <service>` after the repository has been published.

Acceptance checks cover Web `/healthz`, API `/health/ready`, the public companion page, Google login configuration, server-side proxy/cookie behavior and read-only capability status. Test live provider operations only after their real credentials and account evidence are available. Keep development login and mock connectors unavailable on the public production API.

For local container acceptance, build from the repository root with `docker build -f Dockerfile.web -t milo-web:local .`, then run `uv run --frozen python scripts/web_container_smoke.py`. In an environment with a managed certificate authority, supply its existing trusted bundle with `--secret id=trusted_ca,src=/etc/ssl/certs/ca-certificates.crt`; certificate verification remains enabled. The smoke test starts and removes its own disposable web container and synthetic API, checks standalone assets, private backend requests, authenticated snapshots, nonce-cookie paths, CSRF controls and cross-site rejection, and makes no provider calls. Its temporary backend uses development authentication solely to seed the test owner; the production Railway API forbids that mode.

The current cloud investigation found no Railway token or stored session. Railway CLI 5.63.3 was installed in the ignored `.local/railway-cli` directory and reports unauthorized. Requests to `railway.app`, `railway.com`, `backboard.railway.app` and `backboard.railway.com` receive a network-proxy CONNECT 403. Railway authentication and network access must be supplied before this environment can create or deploy a project. No Railway deployment or public URL is claimed by this configuration.
