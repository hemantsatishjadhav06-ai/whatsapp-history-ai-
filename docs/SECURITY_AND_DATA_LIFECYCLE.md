# Security and data lifecycle evidence

Updated 7 October 2026. This records implemented application controls and remaining
deployment gates; it is not a production security certification. See also
[privacy and retention](privacy-retention.md) and [QA report](QA_REPORT.md).

## Identity, scope and authority

Google identity establishes application identity, not WhatsApp/Gmail/Calendar/Contacts
access. Owner APIs derive workspace scope through stored ownership. Read, retain, learn,
draft, send and share permissions are independent and expire. Internal transport ingress
requires service authentication; Meta webhooks require raw-body HMAC verification.
Public/model inputs cannot assign trusted account ownership or alter forwarding grants.

Application session values are hashed at rest, cookies are HttpOnly and production requires
Secure cookies. Login binds a single-use nonce; unsafe authenticated requests require CSRF
and configured origin checks. Production settings reject development login, SQLite,
missing encryption configuration and mock model providers.

Dispatch is SQL-authoritative. Conversation revisions/control epochs, workspace pause
generation, permission versions and connector fences invalidate prior authority. Exact
recipient and payload hashes are checked again before submission. Uncertain attempts
remain durable and block blind retries. A signature or queue reference cannot override
revoked current authority. Short PostgreSQL workspace advisory transactions now serialize
SQL controls, claims, quota reservations and finalization across processes; provider/model
network work is outside those transactions. Process-local submission guards remain in
the mock transport, and these SQL controls do not certify a distributed live-session gateway.

The handoff extends these obligations to native source records, route versions, reaction
targets, general grants and authorized jobs. Synthetic security/lifecycle regressions cover
these boundaries; production security validation remains open. Authentic provider envelopes
must enter through a trusted connector,
stay encrypted and remain unavailable to public clients and models as raw session objects.

## Data custody

Private message, draft, memory, task-title, style-rule, missing-fact and automation-template
fields use application encryption before SQL storage. IDs, timestamps, hashes, local style
statistics and audit metadata are not all encrypted by that field cipher. Development
key material is in ignored `.local/`; production requires managed key custody. Existing
ciphertext requires its original key; replacing the key does not rotate stored data.

Production storage-volume/backup encryption, least-privilege SQL roles, per-tenant KMS,
audited row-level security, private object storage and secret references are not completed
deployment integrations. PostgreSQL pgvector is provisioned locally, but no sensitive vector
index is deployed. Redis is disposable infrastructure and is not an authority store.
Production now requires Redis for shared, hashed, expiring request-rate counters; readiness
checks it and unavailable limit storage fails closed. Separate per-process request admission
and SQL pool/time limits bound work. See [request-limit evidence](request-limits-smoke.md)
and the [security review](SECURITY_REVIEW.md) for the tested scope.

Models receive bounded permitted conversation context. Readable content processed by AI
is not end-to-end encrypted throughout that pipeline. Before configuring a real model,
record the destination, region, data-use/retention terms, pinned model and fallback policy.
AI workers must not receive session secrets or arbitrary network/file tools.

Model requests use durable conservative token/cost reservations before network calls,
with trusted usage reconciliation and exact-model operator-attested prices. Unknown usage
retains an uncertain reservation; a configured cost ceiling with unverified pricing blocks
admission. These controls are tested with provider fixtures, not real billing. The model
processing endpoint exposes declared configuration and explicitly reports provider
configuration unverified; no automatic provider fallback is configured.

## Forget, delete, export and restore

Forgetting a derived memory records suppression and invalidates dependent work; deleting
the original message is a separate action. Source edits/deletions invalidate dependent
memory/style/tasks. Conversation/account content deletion clears private content, revokes
grants and preserves content-free suppression/tombstone/attempt metadata. Exports require
current owner/read scope. They are application exports, not complete provider archives.
Synchronous export and erasure preflight conversation count, relevant row count and encrypted
payload bytes before decryption or mutation. Oversized operations return 413 without partial
erasure; larger background exports/erasures remain unimplemented. The exact limits and their
memory-size distinction are in [storage admission](storage-admission.md).

Authentic records, contacts, reaction examples, action payloads and job content are included
in scoped cleanup/export tests. Exports omit private raw provider envelopes. Deletion cannot
recall already submitted provider operations. External provider
logs, model services, queues and backups have their own deletion/retention requirements.

Owner-configured retention adds explicit bounded sweeps for old raw messages, derived
memories and private draft/job/task/style/action content, plus audit metadata; default
windows are 30/90/90 days. Source-dependent contact/native context is removed with expired
raw records. Content tables and audit deletion use bounded batches. A configured policy
needs a running sweep to remove data.
The independent retention worker also clears bounded expired browser/native authentication
metadata. Native sessions with usable refresh credentials retain their original refresh
deadline. Challenge creation performs only bounded challenge cleanup.
No automated backup restore/purge, universal age-based retention sweep, legal-hold system
or regional recovery service is shipped. Define retention separately for raw records,
derived data, media, queues, audit metadata, keys and backups before pilot launch. Restore
must reapply tombstones before admitting traffic or retrieval. A fresh import or edited
provider event must not restore previously deleted content.

## Operational boundaries

Keep cookies, pairing codes, provider credentials and chat bodies out of access/error logs,
analytics and Kafka envelopes. Kafka carries scalar scoped references. Upload bounds and
content-type checks are implemented for text imports; remote-media download, malware
scanning and private object URL policy require additional implementation. Do not enable
arbitrary URL retrieval from messages or model output.

No production security review, dependency penetration audit or live provider eligibility
sign-off is recorded. Test negative tenant/ref substitution, prompt injection, lease/pause
races and restore-with-suppression before making stronger lifecycle/isolation claims.

## Milo client increment

The new web/native clients are now authorized implementation scope. Their privacy results
require separate acceptance, including built-artifact checks. Web uses the same-origin
allowlisted proxy and server cookie/CSRF boundary; internal routes and server secrets must
never be exposed. Public synthetic demo data must stay isolated per visitor/session and
external sends remain disabled.

Native sessions are distinct from connector sessions. Protected device storage may hold
small environment/API-origin-bound application secrets; it must not hold WhatsApp session
material, model keys or whole private chat archives. The new server contract provides
bounded access plus single-use rotating refresh tokens, hash-only custody and an original
device-session deadline of at most 30 days; existing migrated sessions receive no invented
refresh credential. New server/client rotation and installed-storage evidence must be
recorded separately. OS microphone, push and Contacts permission
must be requested only for the supported owner-invoked task.

Delayed requests may not restore private content after logout, revocation, demo entry or
owner/session change. Serialize secure-store removal/save and fence asynchronous responses
by session generation. Authorized snapshots replace private projections rather than
merging revoked content back in. Offline clients cannot immediately observe remote
revocation: disclose cache age/expiry, purge on local logout and recheck permissions before
restored content is displayed. Push/deep-link payloads are opaque navigation hints and
must reauthorize the target before preview; they never send/resume by opening a URL.

Installed-device, dependency/bundle, keyboard/screen-reader, native storage and public-host
evidence remain separate from local source/typechecking. See
[MOBILE_PARITY_REPORT.md](MOBILE_PARITY_REPORT.md) and [TEST_PLAN.md](TEST_PLAN.md).
Device-specific custody and permission boundaries are in
[native storage](../apps/mobile/MOBILE_SECURITY_STORAGE.md) and
[OS permission matrix](../apps/mobile/MOBILE_PERMISSIONS_MATRIX.md).

Local client evidence now includes 20 shared-contract, 25 proxy, seven Tools privacy,
16 native security/helper and 64 browser cases. The proxy rejects oversized declared bodies and cancels streaming
overflow before forwarding. Browser checks include foreign-owner denial, committed/offline
Pause, fresh-owner setup and a held private response released after logout. Native helper
checks exercise serialized secret operations, owner/read generations, origin binding and
no mutation retry; they do not inspect a real Keychain/Keystore or physical device.
Tools modals and delayed object/evidence reads also bind to the authorized content version:
unchanged polling preserves edits, while Forget or revoked scope removes stale private detail.

The earlier backend privacy bundle passed 77 cases on each SQLite and PostgreSQL after the
533-case baseline runs; the current full suites passed 636 SQLite cases with 13
PostgreSQL-only skips and 649 PostgreSQL cases without skips. Derived-evidence reads filter forgotten/suppressed examples before the
page limit while leaving raw source history distinct; protected draft resolution requires
current owner/read scope. The current 64-case browser run includes private-result response
fences and derived-evidence revocation/forgetting regressions. The final 16 native helper
cases and all-platform export include result reconciliation: earlier private text is never
retagged against a changed snapshot; a current scoped object must be re-read or the result discarded.

The current frozen workspace audit records 29 affected dependency nodes (21 high,
eight moderate) in native/build chains, reduced from the historical 36-node result.
Signed native release requires review and remediation. The separately selected
web/contracts production audit reports zero known advisories. Container OS and
vendored SDK findings remain separate; see [security review](SECURITY_REVIEW.md).
Audit metadata
totals describe the full lock graph, so they are not a count of deployed runtime packages.
Neither an export nor the web audit clears native dependency findings; no security certification is claimed.
