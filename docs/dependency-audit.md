# Dependency and runtime audit — 7 October 2026

This review covers the locked Python environment, npm workspaces, the independent connector gateway, and the actual API/web container filesystems. Package-manager audits and container scans have different scopes. A zero package-manager result does not mean the container or application has no vulnerabilities.

The following tables retain the earlier hardening artifacts. The later Render
increment has new exact-image evidence in [Render container validation](render-container-validation.md):
API `bec67b6124de221ae3b978e37fe934f0412248e1503af500257e06142d3958b2`
retains 264 OS rows and one medium vendored Rust row; the CA-fixed Web
`2edb6757ce7f269710c5de359c647be402cb80285118ca5d476724ca71844e3e`
has 236 OS rows (one critical, 50 high, 103 medium, 81 low, one unknown),
with zero Node package rows. Installing managed certificate/OpenSSL packages
increased Web's inventory and scanner rows; it did not remove the open findings.
The locked Python/npm graphs were unchanged.

## Verified updates

- `cryptography` is locked to **50.0.2** (`>=50,<51`). This addresses four unique advisories affecting the former 47.0.0 lock, including certificate-name handling, PKCS7 RSA decryption, certificate-chain handling and bundled OpenSSL. The isolated frozen environment passed the existing functional suite; final application validation is recorded in the security review.
- `tsx` is **4.23.15**, bringing the patched `esbuild` **0.28.2**. Native SDK, React and React Native major versions were preserved.
- The root override selects **uuid 11.1.1**. A clean frozen `npm ci` reproduced the lock, and the actual xcode CommonJS `v4` caller generated its expected 24-character uppercase identifier. Native checks and all-platform exports passed. The original xcode dependency range still differs from this deliberate, verified override.
- The API image uses official, digest-pinned **Python 3.12.14**; the web image retains official, digest-pinned **Node 24.19.0**. Both apply signed Debian updates over verified HTTPS. Index-download failure stops the build; the trusted public CA bundle is readable by APT's download user. Temporary dependencies are installed and cleaned in one build step to avoid repeated VFS copies.
- API runtime global pip/ensurepip, installation caches and the uv installation binary are absent. Its frozen `.venv`, Python, Alembic and actual worker commands remain functional. Web runtime global npm/npx/yarn/corepack are removed; Node and the standalone application remain functional.

## Package-manager results

| Scope | Result |
| --- | --- |
| Web production npm dependencies | **0** advisories |
| Connector gateway npm dependencies, including development | **0** advisories |
| Locked Python production dependencies | **0** advisories across 48 packages |
| Locked Python dependencies including development | **0** advisories across 55 packages |
| npm workspace / native production dependency graph | **29 affected package nodes: 21 high, 8 moderate** |

The 29 npm graph nodes represent four underlying advisories propagated through Expo dependencies. The initial workspace audit reported 70 affected nodes. Forced Expo/React Native major changes were not used to silence the audit.

| Remaining base advisory | Severity | Compatibility constraint |
| --- | --- | --- |
| [braces GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm) | High | Latest published 3.0.3 remains affected. |
| [node-forge GHSA-86w9-cpqp-85rv](https://github.com/advisories/GHSA-86w9-cpqp-85rv) | High | Latest published 1.4.0 remains affected. |
| [sprintf-js GHSA-hp3w-g68c-fv3c](https://github.com/advisories/GHSA-hp3w-g68c-fv3c) | Moderate | Latest published 1.1.3 remains affected. |
| [decode-uri-component GHSA-vcc3-ghjq-m6fr](https://github.com/advisories/GHSA-vcc3-ghjq-m6fr) | Moderate | Patched 0.5.0 is ESM; the current query-string 7 dependency requires a CommonJS function. An unchecked override would break that caller. This dependency includes a native routing runtime path. |

Compatible upstream patches and updated native SDK dependencies are required before claiming that these findings are resolved. Successful native exports do not establish absence of these advisories.

## Container findings

The scanner is official Trivy **0.75.0**, pinned to manifest digest `sha256:9db099105405c648166e6b94155eb32f8da12673cf1f455207f7385cc9a77283`, using the vulnerability database updated **2026-10-07 07:38 UTC** and downloaded that day. It scans exported images as an unprivileged user with capabilities dropped. Both reports bind to the immutable final image IDs below. Vulnerability findings are retained, including unfixed findings. Secret scans are a separate concern.

Counts below are scanner rows, not unique CVEs. The final images have no Debian 12 findings with a currently available fixed package version. That describes patch availability; it does not erase affected or deferred findings.

| Actual image scope | Critical | High | Medium | Low | Unknown | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| API Debian packages | 2 | 53 | 107 | 101 | 1 | 264 |
| API Python packages | 0 | 0 | 0 | 0 | 0 | 0 |
| API vendored Rust dependencies | 0 | 0 | 1 | 0 | 0 | 1 |
| Web Debian packages | 1 | 48 | 95 | 77 | 1 | 222 |
| Web Node packages | 0 | 0 | 0 | 0 | 0 | 0 |

Before these changes, the API OS scan had 407 rows and its Python scan 14; the web OS scan had 245 and its Node package scan 30. Updates removed the available fixes, including critical Perl/OpenSSL findings. Removing unused runtime package managers removed their vulnerable dependencies.

Remaining material findings are explicit:

- **SQLite CVE-2025-7458:** `libsqlite3-0 3.40.1-2+deb12u2`, critical, Debian status `affected`, no available Debian 12 fixed version. Production configuration uses PostgreSQL, but SQLite remains shipped and is used by local test/development paths. This inventory finding is not claimed fixed.
- **zlib CVE-2023-45853:** critical scanner severity, Debian status `will_not_fix`, present in both images. This vendor status is retained and is not described as a remediation. See the [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2023-45853) for the package/component assessment.
- **rustls GHSA-2mjx-qc3c-rqvc:** medium, vendored version 0.23.42 in the Temporal 1.34.0 wheel; fixed upstream in 0.23.45. Temporal 1.34.0 was the current latest PyPI release checked in this audit. Altering or deleting the wheel's Cargo metadata would not patch its compiled dependency and was not used.

## Artifacts and checks

API image: `relationship-assistant-api:security-20261007`, immutable ID `sha256:b9f6c1a528f96decd6761ad74260936ab6e20450163e25c114cdb3526628a89c`, 334,437,683 bytes, user `10001:10001`, migration head `86b7bbad6fc1`.

Web image: `milo-web:security-20261007`, immutable ID `sha256:bab173cfa2eb425c2e30092bfad18cad109d5c1ab3d3ef434de08bbc1e9b6a70`, 297,957,626 bytes, user `10001:10001`, Node `v24.19.0`. This image includes the final proxy admission guard. Both accepted images are also tagged `:local` for the container smoke scripts. Prior rollback images were exported and their configuration/layer digests verified before unloading unused VFS layers; infrastructure images and volumes were preserved.

The API image passed actual HTTP import/style/approval/mock-send/Pause flows, IPv4/IPv6/runtime-PORT/admission/recovery/shutdown checks, and both Actions/Retention `--once` entrypoints in disposable synthetic databases. These checks make no real provider calls.

The final web image passed actual HTTP acceptance for standalone assets, the real private backend proxy, authentication nonce cookie scope, authenticated snapshots, CSRF-protected controls, cross-site rejection, forwarded HTTPS origin, and blocked development/internal routes. Its runtime probe confirmed UID 10001 and absent unused global package-manager executables. No external provider calls were made.

The final proxy guards have **25 passing focused tests**, including held normal/import requests, reserved control admission, rejection before buffering or forwarding, client abort, upload deadline, response consumption/cancellation/deadline and recovery. **Seven passing Tools privacy tests** cover same-owner correction/Forget/read/grant/capability changes and late private responses. Strict workspace TypeScript and the final production Next build pass. The final browser acceptance passed **64 desktop/mobile-web cases**, with no failures, skips or retries, including scoped modal revocation against the real API. Further validation evidence is recorded in the main security/readiness reports.

The load-test report explicitly distinguishes the measured capacity run from later proxy/Tools fixes. The later admission guard has focused regression coverage; its presence does not certify a new throughput number or 50,000-user deployment.

To repeat dependency checks, use the frozen locks with `npm audit --omit=dev --workspace=@milo/web --json`, `npm audit --prefix services/connector-gateway --json`, workspace/native npm audits, and hashed `uv export` requirements passed to pip-audit with `--require-hashes --no-deps --disable-pip`. Keep TLS verification enabled and inspect the returned scope/counts, including nonzero audit results.
