# Server-held linked-device pilot and commercial launch boundaries

The user's requested journey is Google sign-in, connect an individual WhatsApp
account, select the conversations Milo may use, receive useful drafts and private
owner answers, then permit clearly bounded replies. A managed server should retain
the connection without requiring an open user browser. This decision adds an
isolated operational QR pilot while preserving the mock connector gateway and the
official Business adapter as distinct transports.

The new `services/whatsapp-session` package uses exact locked Baileys 7.0.0-rc14 and
PostgreSQL. Python remains the owner/account/permission/action authority; the
session service never accepts a browser-issued provider send or model instruction.
It binds the scanned provider account and observed PN/LID aliases through Python,
uses expiring owner-only QR data, and retains no ungranted message body. Initial
full history is disabled. Only selected contact text reaches the existing canonical
ingestion path, with live/history/replay origin preserved and outgoing authorship
reviewed. Groups, media, native forwards, edits and deletions remain unsupported by
this bounded adapter and must be declared unavailable in capability records.

Signal credentials need durable transactional storage. The upstream multi-file
demo is unsuitable: signal keys change on send and receive, and lost updates break
delivery. AES-256-GCM AAD binds encrypted rows to the owner, connector, key category
and key identifier. A dedicated PostgreSQL advisory-lock connection identifies one
active cell per connector, all mutations use that connection, current connector
fences are checked, and loss closes the socket. Python deletes these rows on revoke
or workspace deletion. The default 20-session admission cap is a pilot protection,
not a provider capacity certification.

The durable Draft/SendAttempt ledger claims a preallocated provider message ID
before network submission. Current authority is rechecked immediately before the
socket call. Unknown results remain uncertain; receipt/echo reconciliation resolves
them without blind replay. SDK retry defaults and recent-message rehydration are
disabled. URL previews are explicitly suppressed and the isolated runtime disables
third-party Console output before imports because libsignal logs session objects
outside Baileys' logger. Normal TLS verification stays enabled.

Owner disconnect revokes the SQL session, advances its fence and removes Signal
keys before the private service call. Current authenticated authority may close an
older local socket from the same workspace, without a stale store write or callback
to a deleted session. Provider logout is attempted before local socket closure with
a bounded deadline; without a provider unlink receipt, remote revocation remains
unverified. A synthetic loopback fixture exercises both services' actual HTTP DTOs,
SQL authority and encrypted credentials without connecting to WhatsApp.

Enabled process startup selects only bounded active personal-session rows whose
workspace and connector fence still match, then rechecks Python start authority and
acquires exclusive PostgreSQL ownership before decrypting and reconnecting. This
restores server-held sessions without a browser start request. A SQL lease gap goes
through the connected callback's reconnection review; saved send attempts are never
replayed by restoration. Revoked, failed, logged-out and moved-owner rows remain
ineligible.

## Reaching 50,000 clients

50,000 signed-in clients, 50,000 connected accounts, and 50,000 simultaneous reply
requests are different workloads. The current six-service Railway pilot and this
bounded session service establish none of those capacity targets. A commercial
launch must define peak message arrival, history volume, active-account percentage,
model token/latency budgets, outbound provider quotas, availability and recovery.

Measure the actual SDK session RSS, crypto CPU and Signal key write rate before
selecting account-cell sizes. Put account placement and lease/fence ownership in a
durable control plane; shard account sockets into independently replaceable cells,
with one owner per account and no database connection per account. Use a broker
partitioned by connector and conversation, bounded backpressure, per-tenant fair
scheduling, independent model workers and send workers, and encrypted object/key
storage. Keep control commands prioritized over history and model jobs. On cell
loss, stop the old socket and place uncertain attempts in reconciliation before
restoring ownership. Network partitions cannot make external sends atomic with SQL.

Load gates must exercise idle connections and active message/reply rates separately,
multi-owner isolation, duplicate/redelivery, history bursts, expiry and deletion,
model outages, database/broker outages, reconnect storms, key persistence, graceful
drain, and realistic restore. Synthetic user counts cannot prove 50,000 live
WhatsApp account sessions or actual phone continuity. Do not advertise that number
until an exact deployed configuration passes the declared workload and recovery
criteria with reserve capacity and cost evidence.

## Provider alternatives and evidence

The unofficial SDK supports server-held QR linking technically; it is not endorsed
by WhatsApp. The pinned package is MIT but depends on GPL-3.0 libsignal 6.0.0, and
the latest SDK is an RC with imperfect dependency declarations. Track the exact lock,
SBOM, release audit, license notices and protocol compatibility. Development tests
use synthetic injected sockets and actual offline SDK imports; a user-controlled
phone scan, subsequent receipt, reconnect and unlink remain required live evidence.

For business customers, Meta Embedded Signup v4 provides customer-scoped Business
tokens, WABA/phone assets, server code exchange, subscription and payment setup.
Eligible Business app Coexistence supports 180 days of individual history with
sharing approval and a 24-hour synchronization deadline; current documentation
sets a fixed 20 messages/sec per coexistence number and excludes group history.
Its current onboarding uses a verification code and in-app confirmation; it should
not be represented as Milo's self-hosted QR protocol. Existing companion apps are
unlinked during onboarding and supported devices may be relinked. Embedded Signup
v2 is deprecated on **15 October 2026**, so new commercial onboarding must target v4.

Sources inspected over verified HTTPS on 7 October 2026:

- [Baileys source and README](https://github.com/WhiskeySockets/Baileys)
- [Pinned npm SDK](https://www.npmjs.com/package/@whiskeysockets/baileys/v/7.0.0-rc14)
- [Signal package metadata](https://www.npmjs.com/package/libsignal/v/6.0.0)
- [Meta Embedded Signup](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/overview)
- [Business app Coexistence](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-business-app-users/)
- [WhatsApp terms](https://www.whatsapp.com/legal/terms-of-service)
