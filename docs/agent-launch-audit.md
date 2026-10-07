# Agent construction and launch audit

The backend implements bounded workflows as typed modules and SQL ledgers. An
agent name alone does not establish provider access, message delivery, model
quality, or concurrent-client capacity. This audit separates implemented behavior
from the service that executes it and from the missing live integration gates.

## Construction and execution

| Workflow | Implementation | Execution | Live capability and remaining gate |
| --- | --- | --- | --- |
| Authorized message ingestion | `messaging.py`, `webhooks.py`, provider adapters | API accepts canonical events with exact account/conversation scope | Meta requires verified Business credentials, webhook signing, ownership, and per-conversation consent; personal QR adapter has separate gates |
| Owner writing style | `intelligence.refresh_style_from_messages` | In the locked import/ingestion transaction | Local statistics from verified human-owner messages; excludes generated, forwarded, ambiguous, suppressed, and expired sources; no model training claim |
| Conversation retrieval | `intelligence.context_messages`, `valid_memories` | On draft/owner-answer requests | Exact-chat scope, bounded history and owner-confirmed memories; cannot read an entire account without authorized provider sync |
| Draft reply | `intelligence.generate_scoped_result`, `create_draft` | Authenticated API request | Configured model produces a structured draft, never sends; credentials, pricing, quality evaluation, and owner approval are required |
| Automatic draft preparation | `automatic_drafts.py`, explicit `AutoDraftGrant`, durable `AutomaticDraftJob` | Required Jobs worker: SQL-only `automatic_draft_admission` lane and independent generation loop | New authorized live incoming messages produce `needs_approval` drafts; explicit opt-in, current scoped permissions, configured model and budget required; no automatic approval or send |
| Private owner assistant | `companion.run_command` (`ask_me`, `catch_me_up`, `teach_me`, `write_with_me`, `pause`, `resume`) | Authenticated API request | Owner-only answers cite supplied evidence and recheck authority; not a general tool-using autonomous agent |
| Verified business-hours reply | `automation.process_automation` | Required Jobs worker, `verified_business_hours` lane | Exact owner-granted template and confirmed fact; Meta delivery uses the ordinary gated draft dispatcher when configured |
| Selected reactive action | `actions.process_auto_actions` | Required Jobs worker, `selected_actions` lane; optional dedicated Actions worker | Small deterministic planner with explicit grants; generic send/quote/reaction/forward transport remains simulation-only in production |
| Owner-authorized proactive job | `jobs.process_jobs` | Required Jobs worker, `authorized_jobs` lane | Exact owner-authored payload, bounded expiry/recurrence, quiet hours, current permissions; generic external transport remains simulation-only; private reminders are SQL records |
| Approved timed delivery | `messaging.process_due` | Required Jobs worker, `scheduled_intents` lane; optional Temporal activities | Exact approved draft and current authority; configured Meta text delivery supported; no automatic promise/date inference |
| Forward/reaction authority | `actions.py`, `native.py` | Generic action admission and final dispatcher | Native source provenance, exact destination grant, deduplication and takeover checks exist; real generic provider adapter and device evidence are pending |
| Follow-up organizer | `tasks.py` | Owner API requests and inbox | Owner-authored task records; automatic promise extraction and push notifications are not implemented |
| Retention and Forget | `lifecycle.py`, per-module purge/invalidation helpers | Required Retention worker and owner controls | Removes/invalidates scoped data; managed-provider backup recovery still needs separate testing |
| Durable event relay/timers | `relay.py`, `workflows.py` | Optional Kafka/Temporal services | Metadata references and stable identities; not required for the SQL pilot and not evidence of 50,000 concurrent users |

The required Jobs entrypoint now invokes all five bounded reactive/proactive
lanes every second after the previous tick completes. A lane failure is recorded
using only its name and exception class. Remaining lanes are attempted before
the process exits for supervised restart. Every lane retains its existing SQL
claim, authorization, provider, and uncertainty gates. A separate optional
Actions worker can coexist through the same idempotent SQL ledgers; it is not a
second permission to send.

Automatic preparation uses a separate grant from the ordinary draft permission.
`GET/PUT /conversations/{id}/automatic-drafts` exposes versioned opt-in, expiry
(at most 30 days), an hourly ceiling, current blockers, and the latest job.
History, replay, owner outgoing messages and assistant echoes never enqueue.
Each canonical live event/message revision has at most one job, with a ten-minute
execution lifetime; enabling or renewing a grant does not replay older events.
The SQL tick never invokes a model. A separate thread runs at most one automatic
generation per worker, and short PostgreSQL decisions admit at most four across
replicas and one per owner. Transient content-free slots retain capacity while
an in-flight owner's data is erased. The real provider transport has an absolute
deadline across headers and response streaming, a 64 KiB response cap, and no
redirects. Its timeout is reduced to fit the remaining durable claim lifetime.

Model budget reservation is committed before networking. Current grant, source,
connector fence/lease, permissions, controls, memories and generation context
are rechecked before persisting an encrypted, unapproved draft. Forget and
purge cancel pending work; results cannot recreate an erased workspace. Known
rejection before any provider call releases the budget reservation. Unknown
provider outcomes retain a conservative charge and become terminal `uncertain`
jobs; crashed claims are never automatically retried. `jobs --once` performs the
five SQL lanes and drains at most one generation for entrypoint verification.

## Concrete defects repaired in this change

1. **Held queues starved later clients.** Both job and approved-schedule workers
   repeatedly selected the oldest 100 due records. A backlog of 100 held records
   permanently hid later eligible work. Each table now persists
   `last_checked_at`, and bounded selection rotates unchecked/least-recently
   checked records first. Exact authorized `due_at`, owner holds, payloads,
   versions, and submission claims are preserved. Metadata advances monotonically
   through equal or regressing clocks and survives process restart. Migration
   `7e4a91c2d530` adds the columns and scheduling indexes.
2. **The verified business-hours worker was omitted from the deployed Jobs
   entrypoint.** The template lane previously required a separate unconfigured
   process. It now runs in the required Jobs service; configured and consented
   grants can react without an owner pressing an internal run button.
3. **Business-hours planning lacked cross-process transaction authority.**
   Planning and cancellation now take the same PostgreSQL workspace advisory
   lock as owner controls and send claims. Duplicate-marker integrity conflicts
   roll back the duplicate tick rather than terminate the worker or replay it.
4. **Invalid recent memories hid valid older facts.** Retrieval previously
   limited to 20 rows before checking expiry, suppression, and source revisions.
   It now excludes expired/non-conversation rows in SQL, checks at most 200
   candidates, and supplies at most 20 valid memories. Forgotten, stale, or
   private records cannot occupy the entire usable-fact window.

Regression coverage includes more than 100 held records in both queues, clock
ties/regression, worker-lane failure isolation, an integrated reactive template
tick, separate-process PostgreSQL template planning, and memory exclusion.
Actual test counts belong in the release report after running the final source.
Synthetic transport tests establish workflow correctness, not live delivery.
Automatic-draft regression coverage includes explicit opt-in and CAS/CSRF,
non-live exclusions, quota and configuration blockers, source/grant/permission/
memory invalidation during provider work, deletion and export, slow provider
control responsiveness, uncertain/crashed outcomes, more than 100 busy-owner
jobs, and separate-process PostgreSQL owner/global claims. A real local HTTP
server verifies that slowly streaming a body cannot evade the total deadline.
These checks exercise local fixtures and explicitly nonproduction mock models;
they do not establish paid-provider credentials, response quality, or WhatsApp
device delivery.

## Launch decisions still required

A seamless product needs a completed, verified connector journey, configured
Google/Meta/model credentials, explicit scope review, a clear first useful
owner answer, and visible readiness/paused/disconnected status. Connecting alone
does not authorize unrestricted learning or autonomous messages. History
ingestion must not trigger replies to old messages.

Unattended model-authored sends, live personal WhatsApp device/session acceptance,
generic live forwards/reactions, automatic task extraction, installed mobile
device acceptance, and provider delivery receipts require their own implemented
and tested gates. Existing mock operation contracts cannot be advertised as
these completed live agents.

The current bounded SQL pilot is not certified for 50,000 simultaneous clients.
Worker polling limits are admission bounds, not throughput measurements. Capacity
requires a workload definition, tenant/session partitioning, bounded provider and
model queues, replica/database/pool sizing, shared rate limits, observability,
failure recovery, and measured customer-flow tests on the selected infrastructure.
