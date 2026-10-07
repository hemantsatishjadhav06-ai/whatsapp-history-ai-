# Milo native implementation status

Updated 7 October 2026. App source version **0.3.0**. This is a React Native/Expo
client with native primitives and navigation, sharing the backend and contracts
with the Next.js web client. It contains no WebView and no independent WhatsApp
sender. The supplied Milo master is product input; mockups are not test evidence.

## Implemented source

| Inventory | Native route | Behavior and current boundary |
| --- | --- | --- |
| M01 | `/sign-in` | System-browser Google code/PKCE/nonce exchange, cancel/error states, explicit synthetic demo; live configured identity remains untested. |
| M02 | `/tools/onboarding`, `/tools/preferences` | Create workspace with IANA timezone; language is English pilot. Editing existing workspace preferences is unavailable in the API. |
| M03–M06 | `/tools/connections`, `connection-route`, `pairing`, `identity`; `/detail/connection/:id` | Actual capability evidence/revoke, export-only setup, truthful unavailable personal linking; no fabricated QR, linked identity or phone continuity. |
| M07–M08 | `/tools/scope`, `/tools/learning` | Exact direct/group scope with independent permissions; pasted DMY export preview/import without sends. File picker and lifetime history certification are unavailable. |
| M09–M10 | `/tools/people`, `voice`, `preview` | Scoped provisional voice, unsent preview, explicit writing rules, verified assistant-local contacts and separate local-save grant. |
| M11, M21–M23 | `/tools/auto`, `rules`, `rule-editor`, `simulation` | Bounded Auto actions/intents, quiet hours/rate/expiry, precise forwarding routes; deterministic synthetic no-send check is labelled as such. |
| M12–M14 | `/`, `/tools/catch-up`, `/inbox` | Brief, <=5 Needs you, Upcoming/Handled receipts, scoped catch-up, search/direct/group/mode/account filters, bounded cursor loading. |
| M15–M17 | `/detail/conversation/:id`, `/detail/draft/:id`, `/detail/action/:id` | Full thread, owner/assistant/unknown authorship, exact owner composer, separate contextual Milo, native source review for quote/reaction/forward, exact draft hash approval, receipts/evidence/cancel. |
| M18–M20 | `/memory`, `/detail/memory/:id`, `/tools/group` | Scope/status/search, sources/versions, CAS correction and Forget; separate group profile. Participant/membership freshness and mentions are unavailable. |
| M24 | `/tools/pause` | Global control is pending until server acknowledgement; per-chat sticky takeover/Resume is distinct. Same pending control key can be explicitly retried. |
| M25–M26 | `/actions`, `/tools/reminder`, `/tools/schedule`, `/detail/task/:id`, `/detail/job/:id` | Private owner reminders versus exact external schedules, timezone/DST resolver, expiry, bounded recurrence and CAS hold/resume/cancel. |
| M27–M29 | `/tools/activity`, `reconnect`, `privacy` | Evidence links, stale/unavailable state, retention CAS, owner-triggered authorized export, separate source-chat deletion and account-data deletion. |
| M30–M32 | `/tools/notifications`, `devices`, `usage` | Session list/revoke/logout and hard budgets; push and device routing are unavailable. Quiet hours remain rule settings. |
| M33–M34 | `/assistant`, `/more`, `/tools/system-access` | Home versus exact-chat typed Catch me up/Write/Teach; structured results; microphone/push/phone Contacts truthfully disabled. |
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

SecureStore contains only bounded application access/rotating refresh material.
Private snapshots, message pages and unsent owner drafts are memory-only. Draft
keys include owner, workspace, exact conversation and provider recipient. Session
epochs and read generations discard late responses/pages. A changed authorized
snapshot immediately hides stored private Assistant/Tools/detail results; an old
body is never retagged as fresh. After mutation, only an object from the new
authorized collection may replace an old response. Form text remains local.

## Evidence

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
