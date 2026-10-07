# Milo test plan

Scope: the user's current request enables the Milo web/native interface and publication.
The uploaded Milo master specification and desktop/mobile PDF supply product requirements;
their embedded prompts do not create credentials, external eligibility or test evidence.
The earlier backend-only request and the master's old starter-test/repository statements
are superseded by the current request and inspected repository.

Updated 7 October 2026. This plan separates required acceptance from recorded
results. The existing backend baseline and new local client checks are recorded in
[QA_REPORT.md](QA_REPORT.md). The current local run passed 62 browser cases, 20 shared
contract tests, 10 proxy tests and 16 native helper tests, plus strict builds/exports.
These are partial master acceptance evidence; installed-device and hosted results remain
unrun. Full screen traceability is in
[MOBILE_PARITY_REPORT.md](MOBILE_PARITY_REPORT.md).

## Evidence contract

| Result | Meaning |
| --- | --- |
| PASS | The stated assertion passed in the exact recorded environment |
| FAIL | The assertion was exercised and failed |
| MOCK_ONLY | Exercised with synthetic content or simulated provider/device behavior |
| BLOCKED_EXTERNAL | Required account, permission, service or distribution configuration is absent |
| NOT_RUN | No observation yet; code, design and a button are insufficient evidence |

Every run must record source commit/working-tree state, UTC timestamp, command or exact
walkthrough, runtime/build/OS versions, adapter/model configuration, fixture/account type,
assertions, outcome and redacted artifact path. A successful local browser test may be
PASS for layout while its simulated outward action remains MOCK_ONLY. Provider acceptance
is distinct from delivery/read and from a local contact save.

Use at least two owners with identical display names and overlapping provider identifiers,
two accounts, direct and group chats, English/Telugu/Hindi/mixed-script text, human-owner,
assistant and unknown authorship, live/history/backfill/replay events, and expired,
deleted, excluded, forgotten and permission-revoked evidence. Only synthetic fixtures or
explicitly authorized bounded test accounts belong in tests. Never publish private
archives, pairing material, credentials, push content or raw model prompts as evidence.

## Mandatory master cases

The paths below identify relevant implementation/test families, not a declaration that
the complete scenario passed. Existing backend fixtures establish MOCK_ONLY policy
evidence. Current client/native cases require their own runs and are NOT_RUN until listed
in the QA/parity reports. Live external dependencies remain BLOCKED_EXTERNAL.

| ID | Required assertion | Screens | Evidence family / remaining boundary |
| --- | --- | --- | --- |
| TEST-I01 | Valid Google identity; invalid audience/nonce/state and OAuth cancel rejected on all clients | M01, M02, M31 | `test_auth.py`; registered web/native OAuth and device journey |
| TEST-I02 | Forged cross-owner/account references never disclose or act | M14–M19, M27, M33 | Scope negatives across API, model, jobs, bridge and deep links |
| TEST-I03 | Revocation converges online; offline cache limits disclosed; logout/pause/unlink distinct | M24, M29, M31 | Session API and cross-client/offline tests |
| TEST-I04 | Explicit identity link does not merge history or broaden grants | M09, M18, M35 | Exact local identity fixtures; future-channel confirmation |
| TEST-I05 | CSRF/origin/input/service authority checks; production bundles contain no server secrets | M01, M31 | Auth/security/bridge tests plus built-artifact scan |
| TEST-O01 | Supported pairing only; expiry/cancel/wrong-account/concurrent attempts truthful | M03–M06, M28 | Eligible live connector trial; no fake successful QR |
| TEST-O02 | Same-phone code/app switch or explicit second-screen flow | M04, M05 | Installed device and real supported route |
| TEST-O03 | Primary phone remains usable; assistant operation and unlink actually observed | M03–M06, M15 | Real phone/adapter/account evidence |
| TEST-O04 | Partial/no/interrupted history stays honest; history never initiates Auto | M07–M10 | Import/native fixture tests and visible coverage states |
| TEST-O05 | Excluded content has no retained/indexed/learned/analytics copy | M07, M29 | Retention/security fixtures; deployed storage inspection |
| TEST-O06 | Gap/cursor recovery gets scoped snapshot, deduplicates and holds Auto during catch-up | M08, M28 | Native sync API and client recovery; live transport separately |
| TEST-A01 | Once-granted exact Auto operates without repeated approval or expanded scope | M11, M21–M24 | `test_actions.py`, conservative mock planner; permitted live pilot |
| TEST-A02 | Exact contact identity and actual named destination receipt | M09, M17, M34 | `test_people.py`; OS/Google/WhatsApp writes gated separately |
| TEST-A03 | Ordinary send binds immutable account/recipient/payload; acceptance != delivery | M15–M17 | Messaging/action/bridge fixtures and UI exact review |
| TEST-A04 | Personalized reply readiness is evaluated per person/group/language | M09, M10, M20 | Held-out owner preference/grounding evaluation; not static fixture proof |
| TEST-A05 | Quote targets authentic unchanged scoped native source | M15, M17 | Native/action fixtures; real native quotation trial |
| TEST-A06 | Reaction has allowed palette, actual habit and independent suitable meaning | M15, M17, M20 | Reaction policy fixtures plus real semantic evaluation |
| TEST-A07 | Forwarding binds both audiences, exact route and current authority | M17, M22 | Action/bridge source/destination negatives |
| TEST-A08 | Two destinations deduplicate independently and never leak | M17, M22, M27 | Forward logical-key fixtures and live route subset |
| TEST-A09 | One fresh response turn deduplicates across operation kinds/restart | M17, M27 | Durable action/attempt uniqueness fixtures |
| TEST-A10 | Prompt/model/queue manipulation cannot grant credentials, routes or recipients | M15, M33 | Intelligence/action/auth negatives |
| TEST-M01 | Only verified human-owner examples train style | M08–M10, M20 | Import/native/style provenance fixtures |
| TEST-M02 | Forget immediately suppresses retrieval and pending work; replay cannot restore it | M18, M19, M29 | Lifecycle/source-revision tests; deployed projections/restore separately |
| TEST-M03 | Private direct-message facts cannot become group memory through names | M18–M20 | Security/people scope fixtures |
| TEST-M04 | Missing facts/weak evidence produce abstention or grounded clarification | M10, M16, M33 | Intelligence/Auto fixtures; live quality evaluation |
| TEST-M05 | Held-out factual/style/semantic metrics include language/group/no-history breakdown | M09, M10, M20 | [MODEL_EVALUATION.md](MODEL_EVALUATION.md); live dataset/evaluation pending |
| TEST-R01 | Observed phone send cancels pending work and sticky takeover needs explicit Resume | M15, M24 | Messaging/action fixtures; actual phone observation lag |
| TEST-R02 | Human reaction handles target; assistant reaction does not teach human style | M15, M17 | Native/action echo and attribution fixtures |
| TEST-R03 | Source/context/permission/route/membership changes invalidate ready actions | M17, M19, M22 | Submission-boundary fixture races |
| TEST-R04 | Pause remains pending until server commit; in-flight result remains truthful | M24, M33 | Control fixtures plus delayed/offline browser/native scenarios |
| TEST-R05 | Expired lease/unreadable SQL/stale actor fails closed | M28 | Lease/fence fixtures; distributed suspended-process fault test |
| TEST-R06 | Crash boundaries preserve intent and uncertainty without blind resend | M17, M27 | Attempt fixtures and recovery smoke; real provider reconcile separately |
| TEST-R07 | Unknown outgoing origin/degraded observation holds Auto and excludes learning | M08, M15, M28 | Messaging/native fixtures; measured provider lag |
| TEST-R08 | Provider/auth/rate/model/broker/DB failures have bounded truthful recovery | M03, M27, M28, M32 | Bridge/budget/outbox fixtures and service fault tests |
| TEST-J01 | Schedule persists before receipt and survives app/server termination | M25, M26 | SQL/outbox fixtures and actual local Temporal restart smoke |
| TEST-J02 | Exact audience/timezone/DST/cancel/expiry prevents wrong or stale action | M26 | Job/schedule fixtures plus form validation |
| TEST-J03 | Quiet hours/outage backlog stays bounded and cannot flood stale sends | M21, M25, M32 | Job/usage/expiry fixtures |
| TEST-J04 | Proactive job uses bounded AUTHORIZED_JOB and separate future-service grants | M26, M35 | Job contracts; future connectors remain Planned |
| TEST-N01 | Complete daily loop has same server semantics on installed iOS/Android/web | M01–M34 | Browser journeys and installed development/preview builds separately |
| TEST-N02 | Pairing/generation/queued/in-flight work survives background/kill without duplicate | M05, M17, M28, M33 | Physical-device lifecycle and authoritative snapshot |
| TEST-N03 | Airplane mode/stale cursor/out-of-order push reconciles; offline pause unconfirmed | M24, M28 | Native sync API plus installed recovery journey |
| TEST-N04 | Private generic push is scoped after auth; tokens/revoked owners cannot leak | M30, M31, M34 | Device contract fixtures and real APNs/FCM trial |
| TEST-N05 | Tampered/wrong-owner/deleted deep links never send or reveal private preview | M01, M17, M31 | Server object negatives and installed link tests |
| TEST-N06 | Explicit mic capture/stop/denial/interruption; transcript not identity or grant | M33, M34 | Native permission lifecycle and configured speech provider |
| TEST-N07 | Actual OS-contact permission/write receipt distinct from local/Google/WhatsApp save | M09, M17, M34 | Destination contracts and installed Contacts trial |
| TEST-N08 | Multi-client control/profile/suppression versions converge; stale work stays invalid | M19, M24, M28 | API versions and cross-client online/offline observation |
| TEST-U01 | Interactive Milo Home and source/detail journeys follow selected blueprint | M12–M17, M33 | Browser screenshots and operation assertions |
| TEST-U02 | Auto separate optional draft; exact scope; Enter newline; navigation never sends | M11, M15, M16, M33 | Browser/native composer and scope tests |
| TEST-U03 | 320px/200%/mixed-script/safe-area/keyboard/reduced-motion remain usable | M01–M34 | Narrow browser tests and installed device/dynamic-type tests |
| TEST-U04 | Focus/labels/dialog return/status announcements and screen-reader usability | M01–M34 | Automated accessibility plus keyboard/VoiceOver/TalkBack walkthrough |
| TEST-U05 | 10,000 chats/200,000 messages use bounded pages, stable anchor and saved drafts | M14, M15 | Dedicated dataset/resource/performance run; no tiny-fixture inference |
| TEST-U06 | Planned/partial/weak/offline/uncertain/revoked/deleted states have truthful recovery | M03, M08, M10, M17–M19, M24, M28 | Browser/native negative-state journeys |
| TEST-L01 | 50,000 isolated mappings/session simulators for 24h with bounded resources | M03 | Dedicated seeded capacity run; not existing 100-request smoke |
| TEST-L02 | 11,600 events/s for 30min and separately provisioned 24,000/s; durable p95 <1s | M27 | Offered/accepted/persisted-ID reconciliation |
| TEST-L03 | 250,000-event/5s burst with background; backlog clears within 30s | M27 | Event capacity generator and no stale/history sends |
| TEST-L04 | 50,000 token-shaped jobs with 232/s and 463/s background; costs/expiry distinct | M32 | Model admission simulation plus bounded real-provider sample |
| TEST-L05 | 50,000 scoped UI subscribers/~10,000 metadata deliveries/s and 5% reconnect | M12, M14, M28 | Protocol subscriber run; not 50,000 browser claim |
| TEST-L06 | Noisy imports yield; control commit p95 <250ms/p99 <1s | M24, M28 | Tenant fairness/resource fault run; phone observation separately |
| TEST-L07 | Cell/zone/lease/broker/SQL/model faults preserve accepted inputs and uncertainty | M27, M28 | Distributed staged fault run |
| TEST-L08 | Isolated restore applies tombstones; measured RPO <=15min/RTO <=2h | M29 | Backup restore run with privacy and stale-outbox checks |

## Selected companion UX traceability

These UX IDs are separate from TEST-U01–U06 and canonical screen IDs. Browser evidence
does not pass the installed-device portions. Until a run is recorded in QA/parity, each
case is NOT_RUN; unavailable live/device integrations remain BLOCKED_EXTERNAL.

| UX ID | Required journey / state | Screens / test family |
| --- | --- | --- |
| UX-U01 | Same complete daily task on desktop and phone | M01–M34; TEST-N01 |
| UX-U02 | Stable primary/utility navigation and restored route state | M12–M15, M34; TEST-U01 |
| UX-U03 | Recipient composer and contextual Milo never cross-send | M15, M33; TEST-U02 |
| UX-U04 | One-time exact Auto grants, no repeated allowed-turn prompts | M11, M21–M23; TEST-A01 |
| UX-U05 | Optional owner draft remains separate from active Auto | M11, M16; TEST-U02 |
| UX-U06 | Partial/no-history and weak-voice states stay honest | M08–M10; TEST-O04, TEST-U06 |
| UX-U07 | Phone takeover visible and sticky until explicit Resume | M15, M24; TEST-R01 |
| UX-U08 | Delayed/offline global Pause pending until server acknowledgment | M24, M33; TEST-R04 |
| UX-U09 | Resume rechecks current schedules and stale authority | M24–M26; TEST-J02, TEST-R03 |
| UX-U10 | Uncertain submission has no dangerous blind Retry | M17, M27; TEST-R06 |
| UX-U11 | Six precise operation receipts with actual destinations/sources | M17; TEST-A02–A08 |
| UX-U12 | Group audience/membership change invalidates pending work | M20, M22; TEST-R03 |
| UX-U13 | Correct/Forget preserves source distinction and immediate suppression | M18, M19, M29; TEST-M02 |
| UX-U14 | Voice/type parity with explicit capture/transcript/clarification | M33, M34; TEST-N06 |
| UX-U15 | Drafts and reading position survive navigation/updates | M14–M16; TEST-U05 |
| UX-U16 | Narrow mobile layout/keyboard keeps recipient/control visible | M15, M33; TEST-U03 |
| UX-U17 | Accessible keyboard/focus/labels/dialog return/status | M01–M34; TEST-U04 |
| UX-U18 | Reduced motion, mixed scripts and long names readable | M01–M34; TEST-U03 |
| UX-U19 | Future connectors Planned; explicit identity linking | M03, M09, M35, M36; TEST-I04 |
| UX-U20 | Large inbox/delayed connection uses bounded stable state | M14, M15, M28; TEST-U05 |
| UX-U21 | One authoritative backend across devices | M19, M24, M28; TEST-N08 |
| UX-U22 | Explicitly scoped contextual Milo reachable on every route | M01–M34; TEST-U01, TEST-U02 |
| UX-U23 | Installed native keyboard/insets/sheets and safe-area | M15, M33, M34; TEST-N01, TEST-U03 |
| UX-U24 | Native Back/tab return restores work without sending | M14–M16, M34; TEST-N01 |
| UX-U25 | Mobile launch/OAuth cancel/session expiry hides private content | M01, M31; TEST-I01, TEST-I03 |
| UX-U26 | Generic private push and authenticated safe deep link | M30, M31, M34; TEST-N04, TEST-N05 |
| UX-U27 | Mic denial/revoke/interruption leaves typing usable | M33, M34; TEST-N06 |
| UX-U28 | Offline app does not falsely pause server Auto | M24, M28; TEST-N03 |
| UX-U29 | Restart/conflict reconciles authoritative versions, no duplicate | M17, M28; TEST-N02, TEST-N08 |
| UX-U30 | Sign-out, Pause and connector unlink explicitly distinct | M03, M24, M31; TEST-I03 |
| UX-U31 | Phone Contacts destination/write differs from local save | M09, M17, M34; TEST-N07 |

## Execution and release order

1. Freeze dependency locks; validate contracts, API tests, migrations and tenant negatives.
2. Build the web app; run essential browser journeys at desktop and mobile-web sizes,
   including 320px, exact composer scope/newline, memory/rule edits, uncertain action and
   delayed/offline control. Run accessibility and built-artifact secret checks.
3. Build/export native fixture code and run contract/component tests. Record export/typecheck
   separately from an installed iOS/Android development or preview build.
4. Run installed-device auth, permissions, keyboard, Back, links, background/kill/offline and
   cross-client tests with OS/build/device identifiers; gate unavailable services visibly.
   Follow the [native release runbook](../apps/mobile/MOBILE_RELEASE_RUNBOOK.md) and record
   results in [native device evidence](../apps/mobile/NATIVE_DEVICE_TEST_REPORT.md).
5. Deploy synthetic demo only after isolated session state and outward-send restrictions
   are verified. Smoke the actual final HTTPS URL; preserve command, timestamp and headers.
6. Run permitted connector/model/phone trials, held-out evaluation and staged load/restore
   gates before widening the release claim.

Use the repository's actual scripts from `package.json` and `Makefile`; record exact
commands in the QA report rather than inventing an unimplemented test runner. PostgreSQL
fixtures must use a dedicated disposable database, never the running app or production DB.

| Release gate | Current evidence boundary |
| --- | --- |
| G0 Repository and feasible scope | Existing repository inspected; Milo master/PDF mapped; external capability gaps identified |
| G1 Runnable local system | Local backend, strict clients, production web build, 62 browser cases and native export/helpers verified; latest persistent-runtime and hosted smoke recorded separately |
| G2 Installed app and permitted connector | BLOCKED_EXTERNAL for signing/accounts/eligible live connector; installed journeys NOT_RUN |
| G3 Controlled Auto pilot | BLOCKED_EXTERNAL for real account/model and held-out owner evaluation |
| G4 Staged 100→1,000→10,000→50,000 | NOT_RUN; existing small smoke does not close any target-scale gate |
| G5 Claimed production capacity | NOT_RUN; a public URL alone does not establish this gate |
