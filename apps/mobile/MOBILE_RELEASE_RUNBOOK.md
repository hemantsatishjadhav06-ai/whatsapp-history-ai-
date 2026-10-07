# Milo native development and release runbook

Updated 7 October 2026. Prior cloud source/typecheck/export evidence is recorded
in the device report. Signing, installed builds and store publication have not
been performed; implementation of the OAuth broker is not a live-device result.

## Reproduce cloud validation

Use the repository root and its committed npm lockfile, Node 24.19.0/npm 11.9.0
(Node >=22 required). Keep Expo 55.0.31/RN 0.83.10/React 19.2.0 and compatible
modules together; the web workspace has its separate React resolution.

```sh
npm ci --ignore-scripts
npm run test --workspace=@milo/mobile
npm run typecheck --workspace=@milo/mobile
CI=1 EXPO_NO_TELEMETRY=1 npm run export --workspace=@milo/mobile -- --max-workers 2
```

The installed dependency tree and final three validation commands were executed
in this cloud environment. Where the machine has no writable home directory,
Expo's CLI settings directory was redirected without changing `HOME`:

```sh
mkdir -p /tmp/milo-expo-home
CI=1 EXPO_NO_TELEMETRY=1 __UNSAFE_EXPO_HOME_DIRECTORY=/tmp/milo-expo-home npm run export --workspace=@milo/mobile -- --max-workers 2
```

This override only changes CLI local state placement. TLS/checksum verification
was not disabled. Export writes ignored `apps/mobile/dist/`: Hermes bundles and
web/static routes, not APK/IPA files. No reusable EAS credential is stored there.

Copy `.env.example` to an ignored local env file or configure build environment
settings. Only `EXPO_PUBLIC_API_URL` and `EXPO_PUBLIC_MILO_ENV` are consumed. Use a
trusted HTTPS origin, optionally with the exact `/native-api` prefix, and no query,
fragment or credentials; preview/production reject HTTP. The example points to
`https://web-production-bde60.up.railway.app/native-api`, whose dedicated proxy
accepts native bearer sessions and excludes browser cookies. An API URL does not
enable Google or WhatsApp access by itself.

Configure a **Web application** OAuth client and consent screen in the owner's
Google Cloud project. Register Milo's public website JavaScript origin and the
exact HTTPS redirect `https://web-production-bde60.up.railway.app/api/auth/native/google/callback`.
Privately configure backend `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`GOOGLE_NATIVE_REDIRECT_URI` for that HTTPS callback and
`GOOGLE_NATIVE_APP_REDIRECT_URI=milo://oauth`. The native app fetches availability
from `/v1/auth/config`; the system browser sends Google only the registered HTTPS
callback. Milo then returns a short-lived opaque handoff to the installed app,
which exchanges it using its original S256 verifier. No Google code, ID/access
token or client secret belongs in the custom-scheme URL or mobile bundle.
`GOOGLE_IOS_CLIENT_ID` and `GOOGLE_ANDROID_CLIENT_ID` alone cannot enable this
broker and are not required by its Web-client flow. Full registration details are
in [connectivity setup](../../docs/connectivity-setup.md).

`npm run dev --workspace=@milo/mobile` starts Metro for development. Expo Go is
only a synthetic exploration aid and is not native integration evidence.

## Installed build and distribution — not yet executed

`app.json` defines scheme `milo`, bundle/application ID `com.milocompanion.app`,
SecureStore backup policy and native Router/font plugins. `eas.json` separates
development client, internal preview APK and production profiles. No EAS project,
Apple team, Android signing key, APNs/FCM credential or store submission account
has been assigned. Use managed signing custody; do not commit `.p12`, provisioning
profiles, keystores, tokens or credential dumps.

After project/owner/identifiers are reviewed, record a pinned reviewed EAS CLI
version and use its normal authenticated setup/build flow. Intended commands
(**NOT_RUN**) are `eas build --profile development --platform ios|android`, then
preview and production profiles. Local alternatives are the app's `ios`/`android`
scripts on hosts with Xcode/macOS or a supported Android/JDK toolchain. A cloud
JS export does not satisfy either native build path.

Before a private preview, complete N0–N4 installed-device cases in the device
report, including concurrent web/native revoke/Forget, offline Pause, expired
refresh, process kill, backup behavior, source authenticity and no duplicate
submission. Enable only eligible tested transport capabilities. Speech, push and
OS Contacts remain disabled until their own adapter/device evidence exists.
Verify the same real Google account resolves to the same owner/workspace on web
and installed iOS/Android builds. Test system-browser cancellation, the exact HTTPS
callback and `milo://oauth` app handoff, expired/replayed handoffs, proof mismatch,
session expiry and logout/revocation. For the operator-configured Business number,
test signed incoming contact events and consent boundaries; conditional Coexistence
history additionally requires approved onboarding, prior provider sharing consent
and actual permitted-history arrival. Personal QR linking and live groups remain
unsupported. Synthetic browser/API cases and Hermes export do not replace these
installed-device or live-provider checks.
Re-run the dependency audit and review unresolved Expo/RN build-tool advisories;
no forced incompatible SDK downgrade or unchecked transitive override is approved.

For distribution, record source commit, build/runtime/OS/device IDs, permission
matrix, privacy/data declarations, current identity-login/store requirements,
review access and actual declared platform scope. Current Google-only source does
not establish Apple distribution eligibility. TestFlight/internal Android and
store results remain pending managed credentials and release review.

## Rollout and recovery

Keep external sends off until real account/identity/phone-continuity, quota and
model-quality gates pass. Native is a client of one server action ledger; closing
or rolling back the app does not stop server Auto. Use acknowledged server Pause
and current policy/connector controls when holding execution. An offline pause
request is explicitly unconfirmed. Reconcile uncertain submissions; never use a
client reinstall or send retry as evidence of non-delivery.

No OTA update transport/channel is configured. Roll back through a previously
tested signed build and backend-compatible contract; preserve separate build
environments and revoke compromised application sessions server-side. Retain
redacted evidence for the exact release, never private chats or session secrets.
