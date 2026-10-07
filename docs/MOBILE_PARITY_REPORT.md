# Milo mobile and web parity report

Updated 7 October 2026. The current user request enables frontend work. The
Milo master specification is canonical product input; its representative PDF frames are
visual references, not installed-app or backend evidence. Desktop/web and installed
native iOS/Android share server authority but use platform-appropriate layouts.

This report records implemented client surfaces and local verification. **No installed
iOS/Android binary, physical-device journey, store release,
APNs/FCM receipt or OS-contact write is claimed.** A responsive mobile browser or Expo
export cannot be counted as an installed native pass.

Native details are recorded in [implementation status](../apps/mobile/MOBILE_IMPLEMENTATION_STATUS.md),
[device evidence](../apps/mobile/NATIVE_DEVICE_TEST_REPORT.md),
[storage](../apps/mobile/MOBILE_SECURITY_STORAGE.md),
[permissions](../apps/mobile/MOBILE_PERMISSIONS_MATRIX.md) and
[release runbook](../apps/mobile/MOBILE_RELEASE_RUNBOOK.md).

## Screen inventory

Every M01–M34 route is required; M35–M36 are visibly Planned. An unavailable external
integration still needs a truthful screen with explanation/recovery, not a fabricated
Connected/Delivered state. The browser run covers usable base routes and selected
operations; unexercised details and live outcomes remain explicit below.

Screen cells below refer to complete screen requirements, including detail/recovery and
external outcomes. Passing a base-route rendering assertion alone does not pass the whole
screen contract; those partial browser results are recorded in the evidence ledger.

| ID | Required screen/detail | Desktop/web | Installed iOS | Installed Android |
| --- | --- | --- | --- | --- |
| M01 | Sign-in, cancel/errors and sign-out | Logout/fresh-owner MOCK_ONLY; real OAuth BLOCKED_EXTERNAL | NOT_RUN | NOT_RUN |
| M02 | Owner timezone/language/preferences | Setup form MOCK_ONLY; complete preferences NOT_RUN | NOT_RUN | NOT_RUN |
| M03 | Connections, identity/health/capabilities/coverage/revoke | Base route MOCK_ONLY; live outcomes BLOCKED_EXTERNAL | NOT_RUN | NOT_RUN |
| M04 | Connection route, eligibility and phone continuity | NOT_RUN | NOT_RUN | NOT_RUN |
| M05 | QR/code expiry and supported same-phone/second-screen recovery | BLOCKED_EXTERNAL | BLOCKED_EXTERNAL | BLOCKED_EXTERNAL |
| M06 | Verified connected account identity | BLOCKED_EXTERNAL | BLOCKED_EXTERNAL | BLOCKED_EXTERNAL |
| M07 | Exact conversation read/retain/learn scope | NOT_RUN | NOT_RUN | NOT_RUN |
| M08 | Initial learning, observed history coverage/no-history | NOT_RUN | NOT_RUN | NOT_RUN |
| M09 | People & voice detail, provisional evidence | NOT_RUN | NOT_RUN | NOT_RUN |
| M10 | Unsent per-person/group style preview | NOT_RUN | NOT_RUN | NOT_RUN |
| M11 | Exact selected-chat Auto setup, actions/limits/routes/expiry | NOT_RUN | NOT_RUN | NOT_RUN |
| M12 | Milo Home, <=5 Needs you, Upcoming/Handled/freshness | Route/layout/axe MOCK_ONLY; full state matrix NOT_RUN | NOT_RUN | NOT_RUN |
| M13 | Source-linked Catch me up with incomplete/stale receipts | Scoped API MOCK_ONLY; complete UI journey NOT_RUN | NOT_RUN | NOT_RUN |
| M14 | Scoped inbox filters/pagination/reading position | Route/SQL scope MOCK_ONLY; large-inbox anchor NOT_RUN | NOT_RUN | NOT_RUN |
| M15 | Exact conversation/authorship/mode/takeover and recipient composer | Scope/newline/draft persistence MOCK_ONLY; live takeover BLOCKED_EXTERNAL | NOT_RUN | NOT_RUN |
| M16 | Optional draft review, exact edit/send/discard/schedule/stale state | Exact-review fixture MOCK_ONLY; complete delivery journey NOT_RUN | NOT_RUN | NOT_RUN |
| M17 | Six operation-specific action receipts/cancel/uncertainty | Uncertain/no-resend fixture MOCK_ONLY; provider outcomes BLOCKED_EXTERNAL | NOT_RUN | NOT_RUN |
| M18 | Facts, People & voice, Commitments and Preferences memory | Base route/axe MOCK_ONLY; complete detail matrix NOT_RUN | NOT_RUN | NOT_RUN |
| M19 | Memory source/version/allowed-use detail, Correct/Forget | Correct/Forget fixture MOCK_ONLY; complete revoked-state journey NOT_RUN | NOT_RUN | NOT_RUN |
| M20 | Group voice, participants/membership/mentions/topics | NOT_RUN | NOT_RUN | NOT_RUN |
| M21 | Rules list, modes/situations/enabled/expiry/budget | Base route/axe MOCK_ONLY; full rule journeys NOT_RUN | NOT_RUN | NOT_RUN |
| M22 | Rule editor with exact scopes/actions/quiet/expiry | NOT_RUN | NOT_RUN | NOT_RUN |
| M23 | Explicit no-send rule simulation and hold reasons | NOT_RUN | NOT_RUN | NOT_RUN |
| M24 | Chat takeover/global Pause, pending/acknowledged and fresh Resume | SQL acknowledgment/offline Pause MOCK_ONLY; live in-flight BLOCKED_EXTERNAL | NOT_RUN | NOT_RUN |
| M25 | Actions agenda, local reminder versus external schedule | Base route/reminder fixture MOCK_ONLY; full agenda NOT_RUN | NOT_RUN | NOT_RUN |
| M26 | Schedule exact account/recipient/text/timezone/expiry/details | Reminder create/cancel MOCK_ONLY; external schedule UI NOT_RUN | NOT_RUN | NOT_RUN |
| M27 | Activity, evidence and accepted/delivered/uncertain/held/canceled | Base route MOCK_ONLY; full/provider receipt matrix NOT_RUN | NOT_RUN | NOT_RUN |
| M28 | Reconnect/unavailable, stale cache and held sends/reconciliation | NOT_RUN | NOT_RUN | NOT_RUN |
| M29 | Privacy retention/export/delete/forget-source distinction | Settings route MOCK_ONLY; complete export/delete UI NOT_RUN | NOT_RUN | NOT_RUN |
| M30 | Notification/focus preferences and lockscreen privacy | NOT_RUN | BLOCKED_EXTERNAL for push | BLOCKED_EXTERNAL for push |
| M31 | Security/device sessions/revoke/logout/account removal | Logout/race fixture MOCK_ONLY; complete session UI NOT_RUN | NOT_RUN | NOT_RUN |
| M32 | Actual usage/quota/budget/hold reasons | NOT_RUN | NOT_RUN | NOT_RUN |
| M33 | Contextual Milo type/voice/transcript/clarify/result | Voice-unavailable/dialog axe MOCK_ONLY; real speech BLOCKED_EXTERNAL | NOT_RUN; speech BLOCKED_EXTERNAL | NOT_RUN; speech BLOCKED_EXTERNAL |
| M34 | Native More and microphone/push/optional Contacts permission recovery | Web utility navigation MOCK_ONLY | NOT_RUN | NOT_RUN |
| M35 | Gmail/Calendar independent-scoped roadmap | Planned | Planned | Planned |
| M36 | Social/meetings/team roles and permissions roadmap | Planned | Planned | Planned |

M05/M06 can have a working unavailable/recovery screen while the actual pairing outcome
remains BLOCKED_EXTERNAL. Fixture rendering is MOCK_ONLY, not live connected identity.

## Visual and navigation acceptance

Selected Milo design: warm canvas `#FAF7F2`, white surfaces, ink `#2C2538`, secondary
`#6E6578`, primary `#6740C8`, lilac `#EAE0FD`, peach `#F6C7AA`, border `#E3DCEB`, caution
`#FEEDD1`/`#754915`; Space Grotesk headings and DM Sans body. Messages use a readable
16px base, cards 24px radius and primary controls at least 44px. Actual contrast/focus
must be measured; tokens alone are not WCAG evidence.

Desktop has Home/Inbox/Actions/Memory/Rules and utility Connections/Activity/Settings.
Mobile has Home/Inbox/Actions/Memory/More, with utilities in More. Native conversations
are full-screen routes, context controls are sheets/details, and keyboard/safe-area/Back
behavior is platform appropriate. Home requests never inherit the last conversation.
“Message Maya” sends to the recipient; “Ask Milo” addresses the assistant in an explicit
scope. Enter inserts a newline. A microphone control cannot imply capture or transcription
when the real permission/provider is unavailable.

The PDF contains 11 desktop and 14 mobile representative frames. It does not replace
the full screen inventory or the missing-state, privacy and installed-device tests.

## Platform evidence ledger

| Capability / assertion | Web browser | iOS installed build | Android installed build |
| --- | --- | --- | --- |
| Runtime/version/build identity | Final production build passed on Node 24.19.0; source version 0.3.0 | All-platform JavaScript export passed; installation NOT_RUN | All-platform JavaScript export passed; installation NOT_RUN |
| Fixture browser journeys | 58 cases passed: 31 desktop/31 mobile, base routes, synthetic operations and disposable SQL control/onboarding/logout; complete master loop still partial | NOT_RUN | NOT_RUN |
| Shared contract/typechecking | 20 fixture contract tests and strict typecheck passed | Shared contracts, strict native typecheck and 16 helper tests passed; installed behavior NOT_RUN | Shared contracts, strict native typecheck and 16 helper tests passed; installed behavior NOT_RUN |
| Registered real Google OAuth/cancel/session refresh | BLOCKED_EXTERNAL | BLOCKED_EXTERNAL | BLOCKED_EXTERNAL |
| Secure access/refresh storage and rotation/revoke | New server claim/rotation/revoke fixtures passed in 108-case focused suite | Shared server fixture evidence; SecureStore/device inspection NOT_RUN | Shared server fixture evidence; SecureStore/device inspection NOT_RUN |
| 320px/200%/long mixed-script/reduced motion | 320px no-overflow assertions passed on nine primary routes; 200%/complete script/motion NOT_RUN | Dynamic type NOT_RUN | Font scaling NOT_RUN |
| Keyboard/focus/dialog return | Seven axe-audited surfaces per viewport passed; composer newline checked. Complete keyboard/screen-reader walkthrough NOT_RUN | Safe-area/keyboard/VoiceOver NOT_RUN | Safe-area/keyboard/Back/TalkBack NOT_RUN |
| Pause online/delayed/offline truth and server convergence | SQL commit acknowledgment and offline unconfirmed state passed; real Auto/in-flight provider trial BLOCKED_EXTERNAL | NOT_RUN | NOT_RUN |
| Authoritative snapshot/cursor/gap/revocation recovery | Scoped page/resolver fixtures plus held logout, delayed command revocation and forgotten-evidence browser regressions passed; complete stream/gap recovery remains partial | Server/helper result-reconciliation fixtures passed; installed recovery NOT_RUN | Server/helper result-reconciliation fixtures passed; installed recovery NOT_RUN |
| Permission denial/revoke/interruption with typing fallback | Speech BLOCKED_EXTERNAL | Physical-device NOT_RUN | Physical-device NOT_RUN |
| Generic private push and authorized deep link | Push BLOCKED_EXTERNAL | APNs/signing BLOCKED_EXTERNAL | FCM/build config BLOCKED_EXTERNAL |
| Actual OS Contacts write and destination receipt | Unsupported | Physical-device NOT_RUN | Physical-device NOT_RUN |
| Background/process kill/reopen, no duplicate external replay | NOT_RUN | NOT_RUN | NOT_RUN |
| Signed preview/store-ready distribution | Not applicable | BLOCKED_EXTERNAL | BLOCKED_EXTERNAL |

These results supersede the initial browser run with nine failures. Accessible names,
contrast and the duplicated Pause test locator were corrected before the 62-case pass.
Pagination is bounded but a full refresh currently resets the loaded page/reading
position. A 10,000-chat/200,000-message stable-anchor journey has not passed. Native
helper tests exercise application code without proving Keychain/Keystore or OS lifecycle.

The backend currently retains `SEND_TEXT|QUOTE|REACTION|FORWARD`; independent canonical
`send`/`reply` action/grant normalization is incomplete and those unsupported requests
are rejected. Typed drafts and conservative text Auto retain their tested scope. Aliasing
labels cannot establish independently evaluated personalized Auto. Native Teach stores
only eligible current evidence; a conservative confirmation can clear after commit when
the authorized view changes, leaving the saved Memory detail available for review.

Native result records must include app version/build ID, Expo/React Native/native module
versions, OS version, device model, simulator/physical status, permissions, network state,
exact actions, observations and artifacts. Android export and iOS export do not establish
installation, native OAuth, push, secure-storage or contact-writing behavior.

## Latest native cloud evidence — 7 October 2026

The hardening rerun retained native source version **0.3.0**, Expo 55.0.31,
React Native 0.83.10 and React 19.2.0 under Node **24.19.0**. The current lock uses
`tsx` **4.23.15** and `uuid` **11.1.1**. The inspected root lock SHA-256 was
`5b0c619a9ee93813779e30a4863a692dccbe295e077fad37fcbf0c2c0ade2e26`.

| Latest local evidence | Outcome | Final log filesystem write, 7 October 2026 UTC |
| --- | --- | --- |
| [Native tests](../.local/hardening-native-tests.log) | 16 passed, zero failed/skipped | 09:35:06.429758614 |
| [Workspace TypeScript](../.local/hardening-workspace-typecheck.log) | Mobile, web and contracts all passed | 09:35:21.949663707 |
| [Expo all-platform export](../.local/hardening-native-export.log) | Android/iOS/web export and 16 static routes passed | 09:37:58.584754687 |

These log timestamps are observed filesystem metadata, not embedded start/finish
records. Their exact hashes and the latest local bundle links are in the
[native device evidence report](../apps/mobile/NATIVE_DEVICE_TEST_REPORT.md).
The base checkout was `4c9be7357bf2bb5df9cbcedce6dbd9ea090466a0` with uncommitted
hardening changes; the ignored local outputs are not a published signed build.

| Latest bundle | Bytes | SHA-256 | Prior comparison |
| --- | ---: | --- | --- |
| [Android](../apps/mobile/dist/_expo/static/js/android/entry-52abd3c85985d0762cb079495c3d055b.hbc) | 2,991,404 | `1706fd3cf29b1a9ea532418ba532aa581fdf2ed2aa199a9110dfd71123a65a89` | Same name/size, different from prior recorded checksum |
| [iOS](../apps/mobile/dist/_expo/static/js/ios/entry-a625294f1e9d2edf49dc86f5c56be944.hbc) | 2,902,314 | `14f58db787d082c628d6faa38bd707484891ee57f26b86db94e42843092a800e` | Same name/size, different from prior recorded checksum |
| [Web](../apps/mobile/dist/_expo/static/js/web/entry-b87a9fefa5c9a0522e0e4efac2c2e4af.js) | 1,279,841 | `0388860622b428dd0a30daad9eb19a447a89c54ac1a40465d5248d2194de39a6` | Same prior size/checksum |

Native Hermes byte-for-byte reproducibility is unproven; the checksum difference
was observed without attributing a cause. The native helper/export passes do not
change any installed-device column above: physical-device journeys, app signing/
store distribution, live OAuth, APNs/FCM push and OS Contacts remain untested or
blocked by their external prerequisites.

The latest native/workspace audit retains **29 affected dependency nodes (21 high,
eight moderate)**, so dependency remediation is incomplete. The earlier 36-node
count (21 high / 15 moderate) belongs to a prior graph only. Production web and
native/build graphs must be evaluated separately; current root conclusions are
recorded in [SECURITY_REVIEW.md](SECURITY_REVIEW.md).

## Release boundary

Browser release requires operable routes and truthful fixture/live distinctions. Installed
parity requires TEST-N01–N08 and native UX-U23–U31 on both platforms. Store distribution,
real phone continuity, provider model quality and 50,000-account capacity remain separate
gates. See [TEST_PLAN.md](TEST_PLAN.md), [QA_REPORT.md](QA_REPORT.md) and
[RELIABILITY_AND_RECONCILIATION.md](RELIABILITY_AND_RECONCILIATION.md).
