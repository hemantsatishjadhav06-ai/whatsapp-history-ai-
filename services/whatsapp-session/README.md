# Private WhatsApp linked-device pilot

This isolated Node service implements a server-held QR session using the third-party
WhatsApp Web SDK `@whiskeysockets/baileys` **7.0.0-rc14**, with `pg` **8.23.1** and a
committed npm lock. It opens a real provider socket only when explicitly enabled and
an authenticated Python operation authorizes it. Tests use synthetic accounts and
an injected socket; no phone was paired during development.

The bounded pilot supports private expiring QR delivery, canonical account ownership,
reconnection, a maximum of 200 individual-chat metadata entries, granted live text
ingestion, and owner-reviewed text replies through the existing durable Python
Draft/SendAttempt ledger. It does not implement group access, native forwarding,
reactions, quotes, automatic full account history, message edits/deletions, read
receipts, media, or guaranteed device/phone continuity. Defensive incoming history
events retain `origin=history`; they do not trigger automatic replies.

## Configuration and deployment

Run `npm ci --ignore-scripts` and `npm run check` in this directory. The real SDK and
its Rust bridge import and initialize synthetic Signal credentials without lifecycle
scripts. `npm start` installs an isolated no-output dependency Console before SDK
imports: libsignal directly logs session objects independently of the SDK logger.
Application health messages include only the configured port and capability caveat.
Never invoke `src/start.ts` directly in production; use the runtime entrypoint.

| Variable | Meaning |
| --- | --- |
| `ENABLE_PERSONAL_WHATSAPP` | Node default disabled; exact `true` explicitly enables the pilot |
| `DATABASE_URL` | Private PostgreSQL URL; API migrations must have created `wa_personal_auth_keys`; no service boot DDL |
| `SESSION_ENCRYPTION_KEY` | Separate base64 encoding of a random 32-byte AES key; managed secret, preserve through deploys |
| `SESSION_GATEWAY_TOKEN` | Separate random service bearer, at least 32 bytes; same value as Python's configured session gateway token |
| `PYTHON_AUTHORITY_URL` | Fixed private API origin; HTTPS, loopback HTTP or exact `*.railway.internal` private HTTP |
| `PYTHON_INTERNAL_TOKEN` | Existing API internal service token; never a browser or Google token |
| `MAX_PERSONAL_SESSIONS` | Default 20, configurable 1–250; this is an admission bound, not tested provider capacity |
| `PORT` / `SESSION_PORT` | Railway runtime port or local private listener; default 8091 |

Use `services/whatsapp-session/Dockerfile` from the repository root. It pins the same
Node 24 image digest as the web image, installs frozen production dependencies with
scripts disabled, applies signed Debian updates over verified HTTPS, removes unused
global npm/corepack, and runs as the unprivileged Node user. A pinned Python image
supplies the public CA bundle before package requests. Keep this service private;
the web/native clients call the authenticated Python owner endpoints. Do not publish
a session-service domain or expose its token. Configure a private service URL and
gateway token in Python separately. Do not run the SDK's upstream TLS-disabled
example tests. TLS certificate verification remains enabled.

Python separately requires `WHATSAPP_PERSONAL_ENABLED=true`,
`WHATSAPP_PERSONAL_SESSION_URL` set to this service's fixed private origin, and
`WHATSAPP_PERSONAL_SESSION_TOKEN` matching `SESSION_GATEWAY_TOKEN`. Its existing
internal service token must match Node's `PYTHON_INTERNAL_TOKEN`.

`GET /healthz` reports process liveness; `GET /readyz` checks the migrated auth table.
All private operations require the gateway bearer and reject browser Origin, Cookie
and Fetch Metadata headers. Requests and streamed authority responses have size,
deadline and admission bounds. Database queries, locks and connection setup have
timeouts. Every operation obtains current Python authority; idle sessions also
revalidate authority every five seconds and stop after authority or ownership loss.

An enabled runtime also restores up to its configured session cap from current
active personal-session SQL rows. The session and connector must agree on workspace
and fence; revoked, logged-out, failed, moved-owner and other-provider rows are
excluded. Every restored socket still requires fresh Python start authority and
exclusive PostgreSQL lock ownership. Startup waits up to two minutes for migrations
and private authority availability. Encrypted credentials allow reconnect without
an open browser; a prior expired SQL lease requires the connected callback's
`RECONNECT_REVIEW` policy. Restoration does not submit any previous send attempt or
persist a QR, and it is distinct from verified provider continuity.

## Private protocol

`POST /v1/sessions/start`, `/status`, `/chats`, `/disconnect` accept exactly:

```json
{"schema_version":1,"workspace_id":"owner_workspace","connector_id":"connector","connector_fence":1,"account_id":null}
```

Before pairing, `account_id` is null; after pairing it is the provider-observed
canonical individual JID. Chats optionally accepts an exact `provider_chat_id`
filter. Responses contain `schema_version`, `state`, `account_id`, optionally
`qr:{value,expires_at}`, and optionally `chats:[{provider_chat_id,title,kind:"contact"}]`.
QR values live only in process memory for at most 45 seconds, are never callback
payloads or logs, and must be rendered locally in the owner's web/native UI with
`Cache-Control: no-store`. A connected callback includes normalized PN/LID aliases;
Python owns the global uniqueness checks before eligible text ingestion.

Owner disconnect first commits Python revocation, advances the connector fence and
erases private credentials. An authorized current disconnect closes an older local
socket from that workspace without a stale key deletion or a revoked-session
callback. Node attempts provider logout before ending the socket, with a 1.5-second
deadline. The response confirms local closure and reports
`provider_unlink_verified:false`; an SDK logout promise is not a provider unlink
receipt. A disconnect with no local session is idempotent.

`POST /v1/messages/send` additionally requires `conversation_id`, `recipient_id`,
`draft_id`, `attempt_id`, `payload_hash` and exact `text`. The hash is SHA-256 of
UTF-8 text. Python's `/internal/whatsapp-session-authority` resolves the current SQL
draft/attempt, exact recipient and controls; a model or public browser cannot issue
this envelope. Node preallocates an SDK message ID, commits a `submitting` receipt
through `/internal/whatsapp-session-events`, rechecks live authority and ownership,
then calls the socket once. Python must reject an attempt already bound to a
different provider ID and preserve unknown outcomes. Node's bounded five-minute
deduplication is supplementary; restart safety comes from the SQL ledger.

Baileys automatic message retries and recent-message caching are disabled, and
`getMessage` supplies no replay payload. Socket exceptions remain uncertain and
never authorize another send. Text sends explicitly suppress URL link previews
to prevent server HTTP fetches from message text. Acceptance is separate from a
delivery receipt, which Python correlates to an existing attempt in the exact
connector. Text received with `fromMe` remains unreviewed outgoing until the owner
explicitly confirms authorship. An assistant echo is identified by SQL attempt IDs.

## Session storage and capacity

Signal keys and credentials are stored in `wa_personal_auth_keys` as authenticated
AES-256-GCM envelopes. AAD binds workspace, connector, key type and key ID. Python
never decrypts this table. Key batches are atomic and current-fence checked. Every
credential mutation, including deletion, uses the same dedicated PostgreSQL
connection holding the connector advisory lock; a dead old cell cannot keep a
pooled write alive against a new owner's session. Lost connections and fence
changes stop the socket, and owner disconnect removes stored credentials. Session
revocation/deletion also requires Python to delete the connector's private key rows.

The pilot uses **one PostgreSQL lock connection per active session**, plus a bounded
read pool. This deliberately limits blast radius; it is not a 50,000-session design.
Large deployments need measured account cells, fenced durable placement, encrypted
key storage and broker scheduling that does not allocate a database connection per
account. See [the decision](../../docs/decisions/0003-personal-qr-sessions.md).

For real SQL tests, set `NODE_TEST_DATABASE_URL` to a dedicated synthetic PostgreSQL
database and run `npm run check`. Tests create and delete only a random test schema;
they verify encrypted roundtrip, buffer serialization, delete, restart, exclusive
ownership, AAD tampering, stale-fence deletion, and abrupt lock connection loss.
Without this variable, the SQL suite is explicitly skipped. Offline regressions
also invoke the installed SDK text encoder and libsignal logging path. All injected
socket tests are synthetic and establish neither pairing nor provider delivery.

The repository's `scripts/personal_qr_bridge_smoke.py` also uses the real private
HTTP protocol, Python SQL authority and encrypted PostgreSQL storage with an
injected synthetic socket. Its Node runner is `tests/bridge-fixture.ts` and is
excluded from the production image. That runner requires `NODE_ENV=test`, a
loopback-only `NODE_TEST_DATABASE_URL` and `PYTHON_AUTHORITY_URL`, separate
`SESSION_TEST_PORT` and `SESSION_TEST_CONTROL_PORT`, and a distinct
`SESSION_TEST_CONTROL_TOKEN`. The authenticated loopback control listener injects
synthetic QR, account, contact, message and receipt events; `/wait` joins the actual
event queue and `/stats` returns only socket/send counts. The production runtime
does not contain a test toggle or route to this fixture.

## SDK and provider status

The SDK is unofficial and unaffiliated with WhatsApp, and the pinned latest release
is a release candidate. Its MIT package depends on **GPL-3.0 `libsignal@6.0.0`**;
preserve license notices and include that dependency in distribution review. This
record describes license metadata rather than selecting terms on the user's behalf.
Its third-party declarations have NodeNext/Rust `EncodingNode` declaration errors,
so dependency-only `skipLibCheck` is enabled; local source remains strict.

[Third-party provenance and notices](THIRD_PARTY_NOTICES.md) records exact package
versions, lock integrity and upstream license files. These notices are copied into
the image alongside the licenses already retained by installed dependencies.

An npm production audit of this locked package should be recorded for each release;
a zero-advisory report does not establish absence of vulnerabilities or account risk.
Linked-device automation can be affected by WhatsApp protocol changes, device limits,
logout or account enforcement. A QR scan does not confer official Business API
permissions. The official commercial alternative is Meta Embedded Signup v4 with
customer-scoped asset/token grants and eligible Business app Coexistence; personal
consumer accounts and live groups do not become Cloud API features.
