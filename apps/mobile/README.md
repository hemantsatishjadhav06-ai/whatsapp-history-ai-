# Milo native client

Expo 55 / React Native 0.83.10 / React 19.2.0. Native tabs, conversations,
scoped Milo, evidence, Rules and privacy share `@milo/contracts` and the FastAPI
control plane. The explicit synthetic demo connects no account or provider.

From the repository root:

```sh
npm run test --workspace=@milo/mobile
npm run typecheck --workspace=@milo/mobile
npm run dev --workspace=@milo/mobile
```

Use [.env.example](.env.example) for public API/build configuration. The public API
base is `https://web-production-bde60.up.railway.app/native-api`; the dedicated
native proxy accepts app bearer sessions and excludes browser cookies. Google
login opens the system browser through Milo's server broker, using a registered
Web OAuth client and HTTPS callback. Only a short-lived proof-bound handoff returns
to `milo://oauth`; Google credentials and the client secret stay off the app bundle
and app URL. Installed iOS/Android Google client IDs are not required by this broker.

The [Google, WhatsApp and intelligence setup](../../docs/connectivity-setup.md)
documents operator configuration and real-account verification. Connections supports
one eligible server-configured Business number for its exact verified Google owner,
explicit contact permissions and reviewed owner writing. Conditional Coexistence
history needs approved provider onboarding, prior history-sharing permission and
per-contact consent. Personal linking and live groups remain unavailable; selected
personal/group text exports remain supported.

- [Implementation and remaining parity gaps](MOBILE_IMPLEMENTATION_STATUS.md)
- [Actual cloud checks and unrun device matrix](NATIVE_DEVICE_TEST_REPORT.md)
- [Session/private storage](MOBILE_SECURITY_STORAGE.md)
- [OS permission availability](MOBILE_PERMISSIONS_MATRIX.md)
- [Build, signing and release runbook](MOBILE_RELEASE_RUNBOOK.md)

Cloud TypeScript/tests/Hermes export passed. Installed devices, signing, live
OAuth/provider operations and store distribution still require separate evidence.
