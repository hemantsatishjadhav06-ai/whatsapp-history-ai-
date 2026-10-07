# Milo native implementation status

Updated 7 October 2026. App source version **0.3.0**. This is a React Native/Expo
client with native primitives and navigation, sharing the backend and contracts
with the Next.js web client. It contains no WebView and no independent WhatsApp
sender. The supplied Milo master is product input; mockups are not test evidence.

## Implemented source

| Inventory | Native route | Behavior and current boundary |
| --- | --- | --- |
| M01 | `/sign-in` | System-browser Google login through the server's registered HTTPS OAuth callback, single-use state/nonce, proof-bound opaque app handoff and native session exchange; cancel/error states and explicit synthetic demo. Real Google-account/installed-device identity remains unverified. |
| M02 | `/tools/onboarding`, `/tools/preferences` | Create workspace with IANA timezone; language is English pilot. Editing existing workspace preferences is unavailable in the API. |
| M03–M06 | `/tools/connections`, `connection-route`, `pairing`, `identity`; `/detail/connection/:id` | Server-held Business setup/status, provider verification and bounded lease, exact owner/contact consent and optional eligible Coexistence history request; export-only setup and capability/revoke tools. Personal QR linking, live groups, phone continuity and real delivery remain unavailable or unverified. |
| M07–M08 | `/tools/scope`, `/tools/learning` | Exact direct/group scope with independent permissions; pasted DMY export preview/import without sends. File picker and lifetime history certification are unavailable. |
| M09–M10 | `/tools/people`, `voice`, `preview` and Business Connections review | Scoped local style statistics from eligible reviewed owner examples, unsent preview, explicit writing rules, assistant-local contacts and separate local-save grant. Business employee/assistant text is not assumed to be owner writing; learning requires exact chat consent. |
| M11, M21–M23 | `/tools/auto`, `rules`, `rule-editor`, `simulation` | Bounded Auto actions/intents, quiet hours/rate/expiry, precise forwarding routes; deterministic synthetic no-send check is labelled as such. |
| M12–M14 | `/`, `/tools/catch-up`, `/inbox` | Brief, <=5 Needs you, Upcoming/Handled receipts, scoped catch-up, search/direct/group/mode/account filters, bounded cursor loading. |
| M15–M17 | `/detail/conversation/:id`, `/detail/draft/:id`, `/detail/action/:id` | Full thread, owner/assistant/unknown authorship, exact owner composer, separate contextual Milo, native source review for quote/reaction/forward, exact draft hash approval, receipts/evidence/cancel. |
| M18–M20 | `/memory`, `/detail/memory/:id`, `/tools/group` | Scope/status/search, sources/versions, CAS correction and Forget; separate group profile. Participant/membership freshness and mentions are unavailable. |
| M24 | `/tools/pause` | Global control is pending until server acknowledgement; per-chat sticky takeover/Resume is distinct. Same pending control key can be explicitly retried. |
| M25–M26 | `/actions`, `/tools/reminder`, `/tools/schedule`, `/detail/task/:id`, `/detail/job/:id` | Private owner reminders versus exact external schedules, timezone/DST resolver, expiry, bounded recurrence and CAS hold/resume/cancel. |
| M27–M29 | `/tools/activity`, `reconnect`, `privacy` | Evidence links, stale/unavailable state, retention CAS, owner-triggered authorized export, separate source-chat deletion and account-data deletion. |
| M30–M32 | `/tools/notifications`, `devices`, `usage` | Session list/revoke/logout and hard budgets; push and device routing are unavailable. Quiet hours remain rule settings. |
| M33–M34 | `/assistant`, `/more`, `/tools/system-access` | Home versus exact-chat typed Catch me up/Ask/Write/Teach; owner-only source-linked questions cannot send or grant access; separate draft flow and structured results. Microphone/push/phone Contacts truthfully disabled. |
| M35–M36 | `/tools/future` | Gmail, Calendar, social, meetings and teams are Planned; no invented account access. |

Home, Inbox, Actions, Memory and More are actual Expo Router native tabs. Routes
use safe areas, scrollable layouts, platform keyboard avoidance, labelled native
controls with 48px minimum targets, scalable text and platform Back navigation.
Their real-device accessibility and keyboard behavior has not been measured.

## Shared authority and privacy

`@milo/contracts` supplies generated OpenAPI types, DTOs, API transport, formatting,
tokens and explicit synthetic fixtures. `/v1/ui/bootstrap` and owner/read-scoped
`/v1/ui/resolve` supply current objects. Deep-link navigation never authorizes an
effect. Native requests bind a captured bearer and omit browser cookies.
The production API base can use the exact `/native-api` prefix on Milo's HTTPS
origin; that proxy does not forward browser cookies or internal service credentials.
Website and app resolve the same verified Google subject to the same SQL owner.
The broker uses a server-held Web OAuth client secret; direct Google custom-scheme
callbacks and installed-client IDs are not prerequisites for this flow. See
[connectivity setup](../../docs/connectivity-setup.md).

SecureStore contains only bounded application access/rotating refresh material.
Private snapshots, message pages and unsent owner drafts are memory-only. Draft
keys include owner, workspace, exact conversation and provider recipient. Session
epochs and read generations discard late responses/pages. A changed authorized
snapshot hides ordinary stored private Assistant/Tools/detail results; an old body
is never retagged as fresh. Owner-only answers use their server-issued exact owner,
chat/source, permission, connection, memory and expiry checks; unrelated snapshot
changes cannot grant them new authority. After mutation, only an object from the
new authorized collection may replace an ordinary old response. Form text remains local.

## Previously recorded cloud evidence

- `npm run test --workspace=@milo/mobile`: **16 passed, zero failed/skipped**.
- `npm run typecheck --workspace=@milo/mobile`: strict TypeScript passed.
- Expo `export --platform all --max-workers 2`: iOS/Android Hermes and web export
  passed; final artifact identities are in `NATIVE_DEVICE_TEST_REPORT.md`.
- Native component interaction/E2E, installed binaries, physical-device journeys,
  real Google OAuth, APNs/FCM, Contacts, EAS signing and store distribution: **NOT_RUN**.

The helper tests exercise production session, API and private-result functions.
They are not React Native component tests or an installed-device substitute.

## Remaining release gates

The native development/preview/production configuration is present, but N0–N5
installed-device evidence is incomplete. Personal WhatsApp pairing, real primary-
phone continuity, push, speech, playback, file picker and device Contacts have no
working adapter. Group membership freshness is not available. Broad personalized
Auto has no quality certification.

The Business pilot serves one server-configured eligible number for one exact
verified Google owner; it does not implement general multi-tenant Embedded Signup.
Approved provider Coexistence onboarding can make up to 180 days of individual
Business app history eligible, with prior provider history sharing and each
contact's read/retain consent. Recorded accepted, failed or uncertain requests are
not automatically resubmitted. A request receipt proves neither recovered history
nor lifetime completeness. Live Google login, signed provider events, actual history
arrival and opted-in recipient delivery still need real-account/device evidence.

The backend deliberately retains `SEND_TEXT|QUOTE|REACTION|FORWARD`; canonical
`send` and `reply` requests/grants return validation errors rather than gaining
authority. The UI exposes limited-intent text Auto and exact owner-approved
drafts. Independent canonical send/reply grants and evaluated personalized Auto
remain a production parity gap; displaying an alias cannot close it.

Foreground/periodic refresh replaces the authorized bounded first page. Load more
is operable and rejects stale page responses, but loaded-page/reading-position
restoration and very large inbox performance are not complete. Native permission,
process-kill, backup, 320px/200% font scale, mixed-script, VoiceOver/TalkBack and
cross-device conflict tests must be run on signed installed builds. Unresolved
Expo/RN build-tool dependency advisories require release review; no forced SDK
downgrade or unchecked override was used.

See [device evidence](NATIVE_DEVICE_TEST_REPORT.md), [storage](MOBILE_SECURITY_STORAGE.md),
[permissions](MOBILE_PERMISSIONS_MATRIX.md) and [release steps](MOBILE_RELEASE_RUNBOOK.md).
