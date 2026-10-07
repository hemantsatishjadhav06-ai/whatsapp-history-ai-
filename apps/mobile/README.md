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

Use [.env.example](.env.example) for public API/build configuration. Native Google
client IDs come from the API's public config; there is no bundled client secret.

- [Implementation and remaining parity gaps](MOBILE_IMPLEMENTATION_STATUS.md)
- [Actual cloud checks and unrun device matrix](NATIVE_DEVICE_TEST_REPORT.md)
- [Session/private storage](MOBILE_SECURITY_STORAGE.md)
- [OS permission availability](MOBILE_PERMISSIONS_MATRIX.md)
- [Build, signing and release runbook](MOBILE_RELEASE_RUNBOOK.md)

Cloud TypeScript/tests/Hermes export passed. Installed devices, signing, live
OAuth/provider operations and store distribution still require separate evidence.
