# Render container validation — 7 October 2026

The current API and Web sources were rebuilt and accepted locally after the Render startup, provider-owner binding, history onboarding and Web readiness changes. Web was rebuilt again after the GitHub cold builder exposed a missing system-CA bootstrap. The primary Web artifact below includes that correction. Scans of the earlier `b9f6c1a…` API, `bab173cf…` Web or pre-correction `01974256…` Web images were not substituted for its scan.

This is local container validation. It does not establish that Render created services, built these artifacts, connected production credentials or passed public live acceptance. No external provider calls or billable Render operations were performed. It does not certify 50,000-user capacity.

## Immutable artifacts

| Artifact | Immutable Docker image ID | Bytes | Runtime user |
| --- | --- | ---: | --- |
| `relationship-assistant-api:render-20261007` | `sha256:bec67b6124de221ae3b978e37fe934f0412248e1503af500257e06142d3958b2` | 334,468,280 | `10001:10001` |
| `milo-web:render-ca-20261007` | `sha256:2edb6757ce7f269710c5de359c647be402cb80285118ca5d476724ca71844e3e` | 305,823,506 | `10001:10001` |

The API artifact is unchanged. The prior Web artifact `milo-web:render-20261007`, immutable ID `sha256:01974256d6f58e5ddbba67030433186faeaff0b54a73affd9fb5773930c68a5c`, 298,000,256 bytes, remains preserved as the artifact before the CA correction.

Builds used the frozen Python/npm locks, official digest-pinned Python 3.12.14 and Node 24.19.0 bases, verified HTTPS and signed APT metadata. The managed proxy and trusted public CA bundle were supplied through the previously verified local build configuration. TLS or package-signature checks were not disabled. Builds ran sequentially after the full database suites and frontend freeze. Only Web was rebuilt for the CA correction; application sources and the API Dockerfile remained unchanged.

The API runtime probe confirmed Python **3.12.14**, cryptography **50.0.2**, UID **10001**, and absence of unused global uv/pip executables. The corrected Web probe confirmed Node **v24.19.0**, UID **10001**, managed `ca-certificates 20250419~deb12u1`, and absence of global npm/npx/yarn/corepack executables. Its BuildKit CA secret is absent at runtime, and no local extra CA anchors were shipped.

## Cold-builder CA correction

The downloaded GitHub container-job log showed API build success and Web APT failure: certificate issuer unknown, with **"No system certificates available"**. The Node slim runtime did not contain the system trust required by HTTPS APT. The earlier local `trusted_ca` mount supplied trust explicitly, masking this missing default bootstrap.

Web now uses a trust stage from the same pinned official `python:3.12.14-slim-bookworm` digest as API. It copies **only** `/etc/ssl/certs/ca-certificates.crt` into Node before APT. Both the optional custom-CA branch and the default branch run fail-closed signed HTTPS index updates, install the managed Debian `ca-certificates` package, then upgrade. No Python executable, Python dependency or private CA is copied from that stage. No HTTP bootstrap or TLS/signature bypass was introduced.

The public-CA-only, no-secret APT probe through this environment's managed proxy correctly rejected that proxy's private issuer; the missing-system-certificates error disappeared. A separate test-only fixture added the already trusted managed proxy roots to its system store, preserving the pinned public bootstrap, and exercised the **default branch without any `trusted_ca` BuildKit secret**. Signed HTTPS update, certificate-package installation and upgrade passed; the APT build step took **15.9 seconds**. Those additional roots existed only in the test fixture, not the production artifact. This verifies the default APT branch with configured system trust. The next actual GitHub cold build remains the check of the public-CA path without this managed proxy.

## Source evidence

Build-input manifests were captured before each build and checked again afterward. They were identical. API was built from a working tree based on `8afbdd601fc4c6b0adb63e56e5a134bdb9461eff`; corrected Web was built from a working tree based on `fbd00f2716ea00cc43035aa3a229f20d8fed905a` plus the Dockerfile correction. The manifests describe those exact build inputs rather than claiming that the base commits already contain subsequent changes.

| Scope | Build-input files | SHA-256 of sorted path-to-content-hash manifest |
| --- | ---: | --- |
| API | 51 | `be96e4320abae2e32451f900917eb21e0d8f5fc20a6c9524f8e39c89b636674c` |
| Corrected Web | 42 | `721c17183e4baab8819fb807445b3f2673dd277443910be8cc127eecb58a7cef` |

API inputs comprise `pyproject.toml`, `uv.lock`, `alembic.ini`, the API Dockerfile, `services/api/` and `db/`. Web inputs comprise the root npm manifests, Web Dockerfile, mobile workspace manifest, `apps/web/` and `packages/contracts/`. Only source files were included; ignored caches, compiled bytecode, dependency directories and environment files were excluded. Each file was hashed with SHA-256, then the sorted compact JSON mapping was hashed.

All **37 installed API Python modules** inside the frozen container wheel matched their corresponding source-file hashes. This includes `provider_authority`, `config`, `core`, `messaging`, `webhooks`, `render_entrypoint`, `assistantui`, `intelligence`, `jobs` and `lifecycle`. The Web production build completed inside its Docker build and its input manifest remained unchanged.

The synthetic `RENDER_GIT_COMMIT` check below tests runtime reporting. A runtime environment variable alone is not an independent attestation of image contents. The actual release must still select and verify the intended Git commit through Render and the release workflow.

## Container acceptance

The API completed real local HTTP import, repeated-import deduplication, owner-style learning, draft approval, mock dispatch, repeated-dispatch deduplication and Pause controls. Dispatch was simulated; no WhatsApp or model-provider request was sent.

A separate container used **`--network none`**, dropped Linux capabilities and `no-new-privileges`, with an empty disposable SQLite database. These checks passed:

1. `render_entrypoint wait-schema --timeout 1` rejected the unmigrated database and returned sanitized startup diagnostics.
2. `render_entrypoint migrate` applied the release migrations.
3. `render_entrypoint wait-schema --timeout 1` accepted the exact expected head, **`86b7bbad6fc1`**.
4. `assistant.jobs --once` completed with exit code 0.
5. `assistant.lifecycle --once` completed with exit code 0.

These no-network startup checks verify the installed command paths and schema gate using SQLite. They do not replace the separate PostgreSQL migration-lock tests or prove production PostgreSQL/Redis availability on Render.

The first API invocation reached the original smoke script's short readiness timeout. Its original cleanup removed the container before failure diagnostics were captured, so that attempt's root cause remains unproven. The actual HTTP flow subsequently completed with a bounded 30-second readiness window. An instrumented repeat reached HTTP 200 **4.061 seconds after `docker run` returned**, and completed the full flow in **15.237 seconds including container startup**. Its retained runtime log showed application startup complete and no traceback or error. The tracked smoke script was subsequently updated to a monotonic 30-second readiness deadline with exact HTTP 200 acceptance; this script change is outside the Docker build inputs.

The final Web container passed the tracked HTTP acceptance script against a disposable private backend:

- Standalone JavaScript/public assets, non-root runtime and real private backend proxy.
- Authenticated snapshots, authentication nonce cookie scope, CSRF-protected controls, cross-site rejection and forwarded HTTPS origin.
- Rejection of browser development-auth and internal connector routes.
- `/readyz` returned HTTP 200 while the backend schema was ready, and exposed the expected synthetic 40-character release SHA without exposing the private backend address.
- After only the smoke test's own backend schema was removed, `/readyz` returned HTTP 503 with `status: unavailable` and `Cache-Control: no-store`. It respected the bounded coarse dependency cache.

## Fresh exact-image vulnerability scans

Both final images were exported and scanned separately with official Trivy **0.75.0**, manifest digest `sha256:9db099105405c648166e6b94155eb32f8da12673cf1f455207f7385cc9a77283`. The scanner ran as UID 1000 with capabilities dropped and `no-new-privileges`, using a read-only image mount. Its vulnerability database was updated **2026-10-07 07:38 UTC**, downloaded that day, with next update **2026-10-08 07:38 UTC**. Each report's `Metadata.ImageID` exactly matched the immutable artifact above.

Counts are scanner rows, not unique CVEs. Unfixed findings were retained.

| Exact image scope | Critical | High | Medium | Low | Unknown | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| New API Debian 12.15 packages | 2 | 53 | 107 | 101 | 1 | 264 |
| New API Python packages | 0 | 0 | 0 | 0 | 0 | 0 |
| New API vendored Rust dependencies | 0 | 0 | 1 | 0 | 0 | 1 |
| Corrected Web Debian 12.15 packages | 1 | 50 | 103 | 81 | 1 | 236 |
| New Web Node packages | 0 | 0 | 0 | 0 | 0 | 0 |

The prior Web image had 222 Debian rows. Installing the managed certificate package added OpenSSL runtime dependencies: the corrected image has seven additional `libssl3` rows and seven `openssl` rows. The resulting increase is recorded openly; providing system trust is not presented as removing these advisories.

Neither Debian scan reported an available Debian 12 fixed package version. This describes patch availability and does not mean the OS findings are resolved. Material remaining findings include:

- API **SQLite CVE-2025-7458**, critical: `libsqlite3-0 3.40.1-2+deb12u2`, vendor status `affected`. Production configuration requires PostgreSQL, but SQLite remains shipped for local paths.
- Both images **zlib CVE-2023-45853**, critical scanner severity: `zlib1g 1:1.2.13.dfsg-1`, vendor status `will_not_fix`.
- API **rustls GHSA-2mjx-qc3c-rqvc**, medium: vendored 0.23.42 in the Temporal wheel, fixed upstream in 0.23.45. A published upstream fix does not mean the installed wheel has that fix.

The package-manager/native graph findings and remediation constraints remain documented in [dependency-audit.md](dependency-audit.md). A zero Python or Web Node image result is not a claim of a vulnerability-free application or container.

## Local evidence and cleanup

Task-local current evidence includes `/tmp/milo-render-api-image.json`, `/tmp/milo-render-ca-web-image.json`, source manifests `/tmp/milo-render-api-source-proof.json` and `/tmp/milo-render-ca-web-source-proof.json`, `/tmp/milo-render-release-runtime.json`, API HTTP evidence `/tmp/milo-render-api-functional.json`, corrected Web HTTP evidence `/tmp/milo-render-ca-web-functional.json`, full current reports `/tmp/milo-render-scans/api.json` and `/tmp/milo-render-scans/web-ca.json`, and combined summary `/tmp/milo-render-ca-scan-summary.json`. The summary retains the prior Web metadata separately. Default-branch fixture results and timing are in `/tmp/milo-web-ca-default-system-result.json`; its build log and the failing public-CA-only proxy probe log were retained. These temporary paths are inspection artifacts in this workspace, not a promise of permanent external artifact hosting.

All disposable acceptance containers and their temporary application databases were removed. Accepted prior image tags and all infrastructure containers, images and volumes were preserved. No provider credentials were stored in the validation document or logs returned to the user. Actual Render provisioning, protected settings, Google sign-in, history upload through a public production origin and provider-side release acceptance remain separate deployment steps.
