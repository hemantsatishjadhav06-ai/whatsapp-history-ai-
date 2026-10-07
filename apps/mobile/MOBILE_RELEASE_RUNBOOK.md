# Milo native development and release runbook

Updated 7 October 2026. Cloud source/typecheck/export is verified. Signing,
installed builds and store publication have not been performed.

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
trusted HTTPS origin without path/query/credentials; preview/production reject
HTTP. `.invalid` in the example deliberately connects nothing. Installed-app
Google IDs are retrieved from `/v1/auth/config`; configure the corresponding
backend `GOOGLE_IOS_CLIENT_ID`/`GOOGLE_ANDROID_CLIENT_ID` and registered redirect
in secure environment settings. Client IDs are public; client/provider/model
secrets must never enter a mobile bundle. The configured scheme/path is
`milo://oauth`; verify the actual native redirect against the provider before use.

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
