# Backend operations runbooks

## Stop assistant actions

Authenticate as the workspace owner and call `POST /pause-all?workspace_id=...`. This increments
the pause generation and blocks new external work. Durable schedules are held, while
ordinary pending reply actions are invalidated. Local reminders/contact saves remain
separate from external sends. Use conversation takeover for a single
chat. Inspect `/activity` and `/scheduled-intents` under the same workspace. A provider request
already accepted cannot be recalled by a pause; the send ledger records its outcome. Resume
only after reviewing current state. Held schedules recheck exact approval, source,
authority and expiry; canceled actions stay canceled. Human takeover requires explicit
per-chat resume. General Auto grants are separate from owner-invoked exact draft approval.

## Outbox or scheduler delay

Inspect SQL pending outbox counts/oldest age and scheduled-intent statuses without printing
message content. Ensure migrations and database connectivity work first. For schedule
registrations, verify the Temporal namespace/task queue and both registrar and worker are
running. Pending rows survive a failed registration. Stable workflow IDs reject duplicate
reuse, including recovery after the workflow started but SQL acknowledgement did not commit.
Restart the registrar; do not create a second intent to compensate for uncertain registration.

If the worker was down at the due time, start it again and inspect the intent/attempt ledger.
Temporal activities re-read SQL and can cancel or expire the work instead of sending. For an
explicit local fallback, the authenticated internal due-runner can process due SQL intents;
it uses the same sender and ledger. Do not bypass policy by sending directly through a provider.

Kafka publication acknowledges SQL after the broker acknowledges. A relay crash can redeliver
an event; deduplicate by outbox event ID on consumption. Investigate broker connectivity and
unexpected metadata keys before retrying a failing batch. Never change a row to published
merely to make a backlog disappear. Schedule-registration rows belong to the Temporal registrar.

## Uncertain send outcome

Keep `dispatching` or `uncertain` attempts blocked from new provider submissions. Inspect the
provider's receipt or reconciliation data using the authorized integration. Submit a verified
receipt through the authenticated internal receipt endpoint. Distinguish provider acceptance
from final delivery/read status. A generic HTTP timeout does not establish that the provider
did not send. Do not delete the ledger or repeatedly create replacement drafts to retry.

## Disconnect, revoked access, or stale connector

Disconnect through the owner endpoint to advance the connector fence and invalidate work.
Revoke tokens at the provider when required; local disconnect does not automatically revoke
external credentials. After restoring connectivity, verify the same eligible account and
review capability/lease state before resuming. A disconnected or expired lease cannot become
current simply by replaying old events or retaining an old approval.

## SQL restore and deleted data

Pause admission and dispatch. Restore encrypted SQL data together with the correct managed
key version. Reapply deletion and memory-suppression tombstones before starting relays,
registrars, or workers. Verify tenant isolation, schema version, and pending intent/attempt
state. Test one synthetic permitted action and one revoked action before enabling external
transport. Establish and measure backup retention and recovery objectives separately; this
repository does not implement backup orchestration.

## Encryption-key loss or provider credential failure

Do not replace an existing encryption key with a newly generated key against old records.
Recover the correct key from managed custody or acknowledge unrecoverable encrypted data.
For provider credentials, inspect binding names/status and the specific failed operation,
never values. Replace/revoke secrets through secure settings, then verify a read-only account
operation before enabling external sends. Keep request bodies and bearer tokens out of logs.
