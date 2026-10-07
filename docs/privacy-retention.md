# Privacy, retention, and deletion

The owner grants read, retain, learn, draft, send, and share separately for each conversation.
All grants initially deny access; a grant can expire. Ownership is checked through the
workspace owner on authenticated API calls. Conversation revisions, permission versions,
pause generations, and connector fences invalidate pending work after relevant changes.
The handoff adds mock native forwarding through exact owner-granted routes. Source sharing
and destination sending are independent authority checks; identity links alone do not
authorize sharing. The Business transport does not permit customer cross-sharing.

Message text, draft text, memory text, owner-task titles, owner style rules, draft missing-fact
arrays and automation templates are encrypted before SQL storage with Fernet. The handoff
also encrypts provider originals, identity-context objects, reaction context, action payloads,
local contact display names and authorized-job purpose/content. Structural
metadata, IDs, timestamps, style statistics, hashes, and audit
metadata are not all encrypted by that application field cipher. Transport security,
storage-volume encryption, access controls, backup encryption, and key custody remain
deployment responsibilities. Development stores its generated key in ignored
`.local/encryption.key`; production requires an externally managed key. Key loss makes
encrypted fields unreadable. Rotating existing data is a separate migration procedure.

Style learning selects owner-authored human examples within the authorized conversation.
Assistant output, unclear outgoing authorship, media/system messages, deleted content, and
excluded samples do not become writing-style evidence. Memories retain message IDs and
source versions; candidates require owner review before confirmed-memory retrieval.
Model input is scoped and bounded; configured external providers may receive authorized
context needed for a proposal. A mock draft provides no evidence of a real provider's data
handling or output quality.

`DELETE /memories/{id}` records suppression and removes the memory. Evidence edits/deletions
invalidate dependent memories and style information. Conversation-data deletion removes
derived records, empties encrypted message/draft content, revokes grants, marks messages
deleted, cancels pending work, and retains content-free tombstones/ledger metadata to stop
old work from reactivating. Account-data deletion applies this behavior across the owner's
selected workspace. Export returns currently authorized conversation data; it is not a provider
archive export or backup management tool.

Deletion affects the application's live SQL records. It does not recall an already accepted
provider message or guarantee deletion from provider logs, external model services, or
backups. There is no backup integration or automated backup purge in this build.
On restore, reapply deletion/suppression tombstones before permitting traffic or dispatch.
Disconnected connectors increment their fence and invalidate work; revocation of external
platform tokens must also be performed through the provider.

The handoff adds owner-configured application retention policy and an explicit bounded
sweep for old raw message content, derived memories, private draft/job/task/style/action
content and audit metadata. Source-dependent contact/native context is removed with expired
raw records. Default application
windows are 30 raw days and 90 derived/audit days. A stored policy needs a running sweep to
perform cleanup; it is not a claim of autonomous provider or backup deletion. Tombstones
remain to prevent replay resurrection. Synthetic retention evidence is recorded in
[QA](QA_REPORT.md); provider and backup lifecycle are independent unfinished gates.

No universal per-table sweep, Kafka compaction, legal-hold system, per-tenant key rotation,
or audited PostgreSQL RLS deployment is completed. Retention for media, usage, completed jobs,
action metadata, private provider material and backups must be reviewed before production.
Verify forgetting after restore and establish provider-specific privacy notices and
export/deletion behavior. See [security/lifecycle evidence](SECURITY_AND_DATA_LIFECYCLE.md).

Logs and Kafka events should contain IDs and statuses rather than chat bodies, session
cookies, access tokens, or complete environment dumps. The relay rejects unexpected payload
keys and nested metadata. Session tokens/CSRF values are stored as hashes; logging and
reverse-proxy configuration must preserve those boundaries.
