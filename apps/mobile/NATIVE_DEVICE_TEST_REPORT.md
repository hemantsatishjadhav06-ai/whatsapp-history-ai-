# Milo native test evidence

Updated 7 October 2026. This report distinguishes cloud validation from installed
iOS/Android testing. No APK, AAB, IPA, development-client install, EAS build ID,
TestFlight release or store approval was produced in this workspace.

## Cloud run

| Field | Observed value |
| --- | --- |
| App source version | `@milo/mobile` 0.3.0 |
| Runtime | Expo 55.0.31 / React Native 0.83.10 / React 19.2.0 |
| Execution | Cloud Linux, Node 24.19.0, npm 11.9.0 |
| Owner/provider data | Synthetic fixtures and mocked transport only |
| Unit run | 16 passed / 0 failed / 0 skipped; `/tmp/milo-native-unit.log` |
| Strict TS | Passed; `/tmp/milo-native-typecheck.log` |
| All-platform export | Passed; `/tmp/milo-native-export.log` |
| OS/device model | None attached; not a physical device or installed simulator |
| Native build/signing ID | None |

Final run completed **7 October 2026 at 07:35:08 UTC**. Unit and TypeScript logs
completed at 07:34:38 and 07:34:50 UTC respectively. Generated artifacts:

| Target | Bundle under `dist/_expo/static/js/` | Bytes | SHA-256 |
| --- | --- | --- | --- |
| Android | `android/entry-52abd3c85985d0762cb079495c3d055b.hbc` | 2,991,404 | `e8b903e0657b7a992488a4a92a2753ffe657465085aa48d1ef942107a4f49726` |
| iOS | `ios/entry-a625294f1e9d2edf49dc86f5c56be944.hbc` | 2,902,314 | `92e667bbbaa273645231d651e27d2b8dfc2dbf119ad291b25fc5ba21c1325009` |
| Web export | `web/entry-b87a9fefa5c9a0522e0e4efac2c2e4af.js` | 1,279,841 | `0388860622b428dd0a30daad9eb19a447a89c54ac1a40465d5248d2194de39a6` |

Expo emitted 16 static routes, including the five tabs, sign-in, assistant,
dynamic tools/detail routes and router aliases. These identify generated
JavaScript/Hermes artifacts, not signed application binaries. `dist/` and cloud
logs are ignored local outputs.

The executed tests verify session origin/environment/token/expiry binding,
profile-data stripping, ordered secure-storage deletion/save, stale refresh writes,
failed-storage recovery, owner/read-generation fencing, exact draft recipient
isolation, captured bearer/no cookie fallback, no uncertain mutation retry,
public-origin/internal-route restrictions, same-workspace read revocation,
memory Forget/permission edits, and old-response/new-snapshot reconciliation.
They invoke runtime helpers with deterministic fake storage/network and synthetic
data. They do not prove keychain/Keystore behavior or component interaction.

## Required installed-device matrix

| Journey | iOS installed | Android installed | Blocking prerequisite |
| --- | --- | --- | --- |
| N0 tabs/details/Milo daily loop | NOT_RUN | NOT_RUN | Signed development build and chosen devices |
| Google system-browser login/cancel/PKCE/nonce | NOT_RUN | NOT_RUN | Registered platform client IDs/redirect and test owner |
| Access/refresh rotation, expired recovery/revoke | NOT_RUN | NOT_RUN | Live configured API and device storage inspection |
| Eligible WhatsApp pairing/phone continuity | BLOCKED_EXTERNAL | BLOCKED_EXTERNAL | Tested supported transport/real eligible account |
| No historical import sends | NOT_RUN | NOT_RUN | Installed client with selected test archive |
| Exact composer/source quote/reaction/forward | NOT_RUN | NOT_RUN | Installed build; separately authorized provider fixture |
| Takeover/global offline Pause/uncertain receipts | NOT_RUN | NOT_RUN | Device network interruption and scoped account fixture |
| Same-workspace revoke/Forget/cross-owner isolation | NOT_RUN | NOT_RUN | Installed device plus second control client |
| Process kill/relaunch/background/foreground | NOT_RUN | NOT_RUN | Device lifecycle runner |
| 320px/200%/long mixed-script/keyboard/Back | NOT_RUN | NOT_RUN | Chosen device sizes and accessibility settings |
| VoiceOver/TalkBack/focus/contrast announcements | NOT_RUN | NOT_RUN | Platform screen reader/device inspection |
| Large inbox/paging/reading position | NOT_RUN | NOT_RUN | >30 scoped chats; restoration gap remains |
| Mic denial/cancel/lock/audio interruption | BLOCKED_EXTERNAL | BLOCKED_EXTERNAL | Speech capture adapter/provider ADR |
| Generic push/tap foreign or revoked reference | BLOCKED_EXTERNAL | BLOCKED_EXTERNAL | APNs/FCM/project/device registrations |
| Phone Contacts permission/dedup/actual receipt | BLOCKED_EXTERNAL | BLOCKED_EXTERNAL | Device-targeted Contacts executor |
| Backup/restore/uninstall/session lifecycle | NOT_RUN | NOT_RUN | Signed device build and backup scenario |
| TestFlight/internal Android/store distribution | NOT_RUN | NOT_RUN | Managed signing, project and release review |

For each future run record timestamp, source commit, app/native build ID, runtime,
OS/device model, physical versus simulator, owner/connector eligibility, network,
permissions, precise operation, actual observation and redacted artifact. Never
record credentials, archive bodies, pairing secrets or push tokens in evidence.
