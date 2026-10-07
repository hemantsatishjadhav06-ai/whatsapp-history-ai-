# Milo native test evidence

Updated 7 October 2026. This report distinguishes cloud validation from installed
iOS/Android testing. No APK, AAB, IPA, development-client install, EAS build ID,
TestFlight release or store approval was produced in this workspace.

## Latest cloud rerun — 7 October 2026

| Field | Observed value |
| --- | --- |
| App source version | `@milo/mobile` 0.3.0 |
| Runtime | Expo 55.0.31 / React Native 0.83.10 / React 19.2.0 |
| Execution | Cloud Linux, Node 24.19.0, npm 11.9.0 |
| Owner/provider data | Synthetic fixtures and mocked transport only |
| Unit run | 16 passed / 0 failed / 0 skipped; [latest test log](../../.local/hardening-native-tests.log) |
| Strict TS | All three workspaces passed (`@milo/mobile`, `@milo/web`, `@milo/contracts`); [latest typecheck log](../../.local/hardening-workspace-typecheck.log) |
| All-platform export | Android, iOS and web passed; [latest export log](../../.local/hardening-native-export.log) |
| OS/device model | None attached; not a physical device or installed simulator |
| Native build/signing ID | None |

The latest checks ran under Node **24.19.0** after the dependency lock changed to
`tsx` **4.23.15** and workspace override `uuid` **11.1.1**. Native app source remains
version **0.3.0**; Expo/React Native/React versions are unchanged. The checkout base
was `4c9be7357bf2bb5df9cbcedce6dbd9ea090466a0` with uncommitted hardening changes.
The inspected root `package-lock.json` SHA-256 was
`5b0c619a9ee93813779e30a4863a692dccbe295e077fad37fcbf0c2c0ade2e26`.

The logs do not embed start/finish UTC timestamps. These are their observed final
filesystem write times on **7 October 2026**, recorded explicitly as UTC:

| Evidence | Final log write, UTC | Bytes | Log SHA-256 |
| --- | --- | ---: | --- |
| [Native helper tests](../../.local/hardening-native-tests.log), 16 PASS | 09:35:06.429758614 | 2,142 | `39a60b9cbd21186bd15cd320e101304fdbc2eda1ae3eff465aa7cd187cf82c54` |
| [All-workspace strict TypeScript](../../.local/hardening-workspace-typecheck.log), PASS | 09:35:21.949663707 | 641 | `f05cb772a5e021c2a5e4b329ca59644bb61197ecbba4919842857f1f8290e731` |
| [Three-platform Expo export](../../.local/hardening-native-export.log), PASS | 09:37:58.584754687 | 2,220 | `c477b6eb5dbe63c91870f88f878a69e7f25bc6ab1b2be6a260ca795a66b1cfca` |

Latest exported files, hashed directly after the rerun:

| Target / local artifact | Bytes | SHA-256 | Comparison with prior export |
| --- | ---: | --- | --- |
| [Android Hermes bundle](dist/_expo/static/js/android/entry-52abd3c85985d0762cb079495c3d055b.hbc) | 2,991,404 | `1706fd3cf29b1a9ea532418ba532aa581fdf2ed2aa199a9110dfd71123a65a89` | Same filename/size; checksum changed |
| [iOS Hermes bundle](dist/_expo/static/js/ios/entry-a625294f1e9d2edf49dc86f5c56be944.hbc) | 2,902,314 | `14f58db787d082c628d6faa38bd707484891ee57f26b86db94e42843092a800e` | Same filename/size; checksum changed |
| [Web JavaScript bundle](dist/_expo/static/js/web/entry-b87a9fefa5c9a0522e0e4efac2c2e4af.js) | 1,279,841 | `0388860622b428dd0a30daad9eb19a447a89c54ac1a40465d5248d2194de39a6` | Identical size and checksum |

The two Hermes bytecode files were not reproduced byte-for-byte despite matching
names/sizes; the cause of the checksum differences has not been investigated.
Passing helper/type/export checks does not establish binary reproducibility.
The web artifact matches the recorded prior checksum. Filesystem writes for
Android/web were 09:37:58.576754733 UTC and iOS 09:37:58.580754710 UTC.

Expo emitted 16 static routes, including the five tabs, sign-in, assistant,
dynamic tools/detail routes and router aliases. These identify generated
JavaScript/Hermes artifacts, not signed application binaries. `dist/` and cloud
logs are ignored local outputs. Links to them refer to this workspace and do not
make those artifacts available in a clean checkout; preserve redacted logs and
artifact hashes with any future release evidence.

The executed tests verify session origin/environment/token/expiry binding,
profile-data stripping, ordered secure-storage deletion/save, stale refresh writes,
failed-storage recovery, owner/read-generation fencing, exact draft recipient
isolation, captured bearer/no cookie fallback, no uncertain mutation retry,
public-origin/internal-route restrictions, same-workspace read revocation,
memory Forget/permission edits, and old-response/new-snapshot reconciliation.
They invoke runtime helpers with deterministic fake storage/network and synthetic
data. They do not prove keychain/Keystore behavior or component interaction.

## Prior cloud evidence — superseded for latest checks

The prior run completed **7 October 2026 at 07:35:08 UTC**. Its unit and TypeScript
logs completed at 07:34:38 and 07:34:50 UTC respectively, with 16 helper cases,
strict native TypeScript and three-platform export passing. These recorded hashes
are the comparison baseline; that earlier `dist/` was overwritten by the rerun.

| Prior target | Bytes | Prior SHA-256 |
| --- | ---: | --- |
| Android | 2,991,404 | `e8b903e0657b7a992488a4a92a2753ffe657465085aa48d1ef942107a4f49726` |
| iOS | 2,902,314 | `92e667bbbaa273645231d651e27d2b8dfc2dbf119ad291b25fc5ba21c1325009` |
| Web | 1,279,841 | `0388860622b428dd0a30daad9eb19a447a89c54ac1a40465d5248d2194de39a6` |

## Dependency audit boundary

The latest native/workspace graph still reports **29 affected dependency nodes:
21 high and eight moderate**. This is residual advisory exposure, not complete
remediation and not a count of distinct exploitable bugs. The earlier count of
36 affected nodes (21 high / 15 moderate) was a **prior graph result** and is not
the current count. Selected web production graphs are separate from the native/
build graph. See the current [security review](../../docs/SECURITY_REVIEW.md).

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
