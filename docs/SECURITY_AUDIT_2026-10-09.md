# Security audit and launch debug — 9 October 2026

Scope: the Python API (`services/api/assistant`), the AI agent pipeline, the Node
WhatsApp session service and connector gateway, the Next.js web proxy, Dockerfiles
and the Railway deployment. The review was read-only and was followed by targeted
fixes in the same change. Each fix has a regression test.

## How the server works

1. **Public entry is the web service.** Browser traffic uses `/api/*`, which only
   accepts allowlisted routes and forwards a few headers. Native clients use
   `/native-api/*`, which takes bearer tokens only. Meta webhooks and the Google
   callback have their own handlers. `/internal/*` cannot be reached from outside.
2. **The private API runs `RequestSecurityMiddleware` first.** It checks header and
   body sizes, limits in-flight requests (keeping a reserve for control routes), and
   applies Redis-backed rate limits per global, actor and source bucket before it
   reads the body.
3. **Authentication** (`auth.get_current_user`) accepts either a hashed native
   bearer token or a hashed `session_token` cookie. Requests that change state also
   need an allowed Origin and a matching CSRF token.
4. **Workspace scoping.** Each handler loads a resource and checks that the
   workspace owner is the current user. Then `permission_for` checks the per-chat
   switches: read, retain, learn, draft, send and share.
5. **Storage.** PostgreSQL is accessed through SQLAlchemy. Message, draft and memory
   text is encrypted with Fernet. Tokens and nonces are stored only as SHA-256
   hashes.

## How the agents work

- **Triggers.** A model is called for three things: an owner-requested draft
  (`POST /conversations/{id}/drafts`), an owner command (`write_with_me`/`ask_me`),
  and the Jobs worker preparing automatic drafts for new inbound messages in chats
  with an `AutoDraftGrant`.
- **Context.** The model only sees the selected chat: the latest 30 inbound
  messages, up to 10 older ones found by keyword, style statistics, owner rules,
  and confirmed memories (only when learn and retain are on). Consent and control
  state are snapshotted and compared before and after the provider call. The model
  has no tools.
- **Model client.** It sends an OpenAI-compatible
  `POST {MODEL_API_URL}/chat/completions` with a strict `json_schema` response. The
  response is capped at 64 KB and the call times out after at most 120 seconds. The
  reply must cite only evidence IDs it was given, or list the facts it is missing.
- **Sending.** Every generated draft is created as `needs_approval`. Approval binds
  the draft's exact content hash. Dispatch claims the draft atomically, writes a
  send-attempt ledger row, and re-checks authority just before sending.
- **Unattended actions.** Only verified business-hours templates send without a
  fresh approval. Auto action grants are mock-only and blocked in production.
  Booking, research and calendar agents do not exist yet; they are on the roadmap.

## Findings

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | High | With `TRUST_PROXY_HOPS=0`, every visitor shares one rate-limit source bucket. Anonymous `POST /api/pause-all` at about 120/min caused 429s on every owner's pause, takeover, permission changes and logout. About 30/min of `GET /api/auth/nonce` blocked all sign-ins. | **Fixed (code):** control routes without credentials are rejected with 401 before rate accounting (`request_security.py`). **Fixed (option):** the web proxy can sign Railway's `X-Real-IP` (`TRUST_PROXY_HEADER=x-real-ip`) for per-client buckets. This needs `BACKEND_PROXY_KEY` on Web and a matching `TRUSTED_PROXY_KEY` on the API. |
| 2 | High | Any Google-verified user could create a workspace and call the model without limit at the operator's cost, because no budget row meant no ceiling. | **Fixed:** a default per-owner daily allowance (`MODEL_DEFAULT_DAILY_TOKENS`, 1M) applies across all of an owner's workspaces until an explicit token budget is set. |
| 3 | Medium | Synchronous model calls of up to 120 s each held ordinary request slots. About 28 parallel `ask_me` calls from one account returned 503 to everyone, including webhook ingestion. | **Fixed:** a separate process-local model admission (`MODEL_MAX_CONCURRENCY`=4, `MODEL_MAX_CONCURRENCY_PER_OWNER`=1) fails fast with `429 MODEL_BUSY`. |
| 4 | Medium | Owner-named `export_only` connector labels were unique across all tenants. One tenant's "Family" or the mobile default "My Milo" blocked everyone else with a 409, which also revealed that the label existed. | **Fixed:** migration `d41c7e9a2b10` makes export labels unique per workspace. Provider and simulated account identities stay globally unique through a partial unique index. |
| 5 | Medium | The web proxy takes its admission slot before reading the body. Slow uploads to unauthenticated routes can hold all 32 slots (slowloris). | Open. Recommended: a separate small pool for unauthenticated routes, a shorter upload deadline for 64 KB bodies, and requiring a credential before admitting authenticated routes. |
| 6 | Medium | The session service holds the all-purpose `INTERNAL_SERVICE_TOKEN`, which also authorizes `/internal/connector-events` for any tenant. | Open. Recommended: a separate token per service, scoped to its routes, at least 32 bytes long. |
| 7 | Low | The CSP does not restrict `script-src`. React escaping currently prevents XSS. | Open. Recommended: a nonce-based CSP that allows Google Identity Services, and `includeSubDomains` on HSTS. |
| 8 | Low | A live pairing QR can be relayed to a victim, which links their WhatsApp to the attacker's workspace. | Open. Recommended: check the expected phone number before accepting a pairing, or use pairing codes. |
| 9 | Low | The mobile OAuth handoff on the custom `milo://` scheme can be intercepted by a rogue app on a phished device. | Open. Recommended: verified App Links / Universal Links. |
| 10 | Low | When `ENVIRONMENT` is unset, it defaults to `development` and the production guards are skipped. | Open. Railway sets it explicitly. Recommended: refuse to start without an explicit value. |
| 11 | Low | Reuse of a rotated mobile refresh token is not detected. | Open. Recommended: revoke the session family on reuse. |
| 12 | Low | Model drafts are not screened for links, payments or commitments before the owner approves them. | Open. Recommended: flag these in the review UI. |
| 13 | Low | `docker build` from a dirty local tree could copy `apps/web/.env*` into the image. | **Fixed:** `.dockerignore` excludes `**/.env*`. |
| 14 | Low | Jobs and Retention exit on any lane failure and Railway restarted them only 3 times, after which scheduled sends and automatic drafts stopped for good. | **Fixed:** manifests use `restartPolicyType: ALWAYS`. |
| 15 | Low | Uncaught SQL errors could include bound parameters (identifiers and ciphertext) in logs. | **Fixed:** `hide_parameters=True`. |

Checked and found sound: cross-workspace access across about 110 routes, session
rotation, CSRF and login-nonce binding, Google token verification, PKCE, webhook HMAC
over the raw body, timing-safe token comparisons, AES-GCM session key storage, path
handling in the proxy, SSRF boundaries, approve/send races (advisory locks plus
content hashes), non-root containers and pinned images.

## Model provider (OpenRouter)

Railway's API service had no `MODEL_API_KEY`, `MODEL_NAME` or `MODEL_API_URL`, so
generation was disabled and no OpenRouter key was present in the repository or the
deployment. To enable it, set the following on **both** the API and Jobs services:

```
MODEL_PROVIDER=openrouter
MODEL_API_KEY=<your OpenRouter key>
MODEL_NAME=openai/gpt-4o-mini        # any model listing structured_outputs
# optional verified pricing for cost ceilings (gpt-4o-mini, micro-USD per 1M tokens)
MODEL_PRICING_VERIFIED=true
MODEL_PRICING_MODEL_NAME=openai/gpt-4o-mini
MODEL_INPUT_COST_MICROUSD_PER_MILLION=150000
MODEL_OUTPUT_COST_MICROUSD_PER_MILLION=600000
```

OpenRouter requests now send `provider.require_parameters=true`, so they only route
to providers that honour the strict JSON schema. They also send the `HTTP-Referer`
and `X-Title` attribution headers. A reply wrapped in a Markdown code fence is still
accepted. Avoid reasoning-only models: `max_tokens` is 1000, and that limit includes
reasoning tokens.
