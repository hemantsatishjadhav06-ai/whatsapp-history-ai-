# Milo native permissions matrix

Updated 7 October 2026. Optional OS integrations are unavailable in this build;
their controls explain the boundary and trigger no permission prompt. Typed
Milo, scoped history, Rules, reminders and privacy remain usable without them.

| Feature | OS access / current status | App behavior | Required before enabling |
| --- | --- | --- | --- |
| API connectivity | Standard network access; HTTPS origin | Foreground scoped fetch; no background sender | Real network/foreground tests |
| App session secrets | SecureStore/keychain/Keystore | Small token record; no biometric prompt configured | Installed storage/lock/backup checks |
| Google identity | System browser; no Gmail/Calendar consent | Registered HTTPS server broker, single-use state/nonce and proof-bound opaque app handoff; no embedded login WebView or bundled secret | Registered Web OAuth client and server-held secret; installed iOS/Android callback, cancellation, same-owner and revocation tests |
| Microphone/transcription | Not requested; adapter unavailable | Talk disabled, no Listening indicator, typing available | Provider/region/retention ADR and native capture/Stop/Cancel/interruption tests |
| Read-aloud/audio playback | Not implemented | No private automatic playback or call recording | Owner-triggered playback/Stop and private audio tests |
| Notifications | Not requested; APNs/FCM unavailable | No pretend registration or private lock-screen payload | Contextual consent, managed credentials, scoped generic push/device routing |
| Phone Contacts | Not requested; executor unavailable | Assistant-local SQL save is labelled; phone/Google/WhatsApp writes unavailable | Device-targeted grant/expiry, verified identity, current OS access, dedup and actual native receipt |
| File/media/import picker | No picker permissions requested | Explicit pasted text export with date order; no silent filesystem scan | Compatible picker and selected-file/permission/cancel tests |
| Camera/QR | Not requested | No fabricated personal linking QR | Tested eligible same-phone code or explained second-screen transport |
| Continuous background socket/timer | Not registered | Server owns Auto and schedules while the app is closed | No mobile continuous-sender design; optional bounded refresh only |

No sensitive capture/Contacts/push native adapter is bundled merely to show a
permission dialog. Future adapters must explain purpose before requesting access,
support denied/restricted/limited/revoked states, and preserve text-first use.
Backgrounding, lock or an audio interruption must stop/suspend capture under a
tested rule; a partial transcript must never initiate an effect.

OS permission is separate from server authority. Google login is not Google data
consent, microphone access is not send approval, and Contacts access is not a
cross-device contact-write receipt. `/tools/system-access` and notifications
screens describe actual current availability. Every physical permission journey
is **NOT_RUN/BLOCKED_EXTERNAL**, as recorded in the device report.
