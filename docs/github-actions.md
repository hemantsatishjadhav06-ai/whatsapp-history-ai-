# GitHub validation and Render releases

GitHub stores the application, runs its tests, builds the runtime images, and retains release evidence. Render hosts the public web service, private API, PostgreSQL, Redis-compatible Key Value service, and background workers. A successful GitHub validation run alone does not establish a deployed application or live URL.

## Workflows

| Workflow | Trigger | Required work |
| --- | --- | --- |
| `backend.yml` | Pull requests, non-main pushes, reusable call | Ruff; complete SQLite and PostgreSQL suites; real Redis admission/recovery; separate-process migration locking and worker schema readiness; gateway checks; shared contracts; authenticated mock bridge; repeatable migrations/model alignment. |
| `clients.yml` | Pull requests, non-main pushes, reusable call | Frozen npm install; contracts; workspace TypeScript; proxy/privacy/native tests; Next production build; all-platform native exports; actual desktop/mobile-web browser acceptance against a disposable backend. |
| `containers.yml` | Pull requests, reusable call | Workflow/actionlint checks; both Docker builds; actual API/web HTTP acceptance; network-disabled empty-database worker entrypoints; release dependency audits; reviewed native audit baseline; full container vulnerability reports and CycloneDX SBOMs; available HIGH/CRITICAL fix gate. |
| `render-release.yml` | Every main push, manual dispatch | Calls all three validation workflows for the event's exact commit. A separately guarded job can deploy that commit to Render, then checks the live service. The final summary distinguishes validation from deployment. |
| `security.yml` | Monday 06:17 UTC, manual dispatch | Rebuilds and scans current default-branch images through the same container/security workflow, so updated advisory databases can expose newly published findings. It does not deploy. |

Third-party Actions are pinned to immutable commit SHAs. Python **3.12.14**, Node **24.19.0**, uv **0.12.19**, pip-audit **2.10.1**, and the official digest-pinned Trivy **0.75.0** scanner are explicit. Installations use the committed Python/npm locks. Checkout credentials are not persisted. Workflow tokens have `contents: read`; validation jobs receive no production secrets. There is no `pull_request_target` job that executes untrusted code with secrets.

The release browser suite disables retries, so a failed first execution fails validation. Separate-process PostgreSQL startup checks use an owned disposable database, observe both migrators waiting on the same advisory lock, then verify each revision applies once and workers wait for the exact schema head.

Main validation runs are not canceled midway through a release. Production deployments additionally share a non-canceling concurrency group. Immediately before provider mutation, the release job verifies that the checkout is the tested SHA and still the current main head. This prevents a delayed older run from replacing a newer main release. Selecting another branch in manual dispatch cannot deploy production.

## Configure a production release

First provision and configure the resources in [`render.yaml`](../render.yaml), following [`render.md`](render.md). Each service must belong to the expected repository/owner and use the main branch. Application service auto-deploy is **off** so a provider-triggered deployment cannot bypass GitHub validation. Disable Blueprint automatic sync as well; provisioning and worker start order require the runbook's schema/readiness gates.

Create the GitHub environment **production**. Put `RENDER_API_KEY` in its encrypted Actions secrets. Use a Render API key belonging to the resource owner; do not commit it, expose it in workflow logs, or paste it into a GitHub issue. Environment reviewer/branch policies can be configured by the repository owner without changing workflow code.

Set these **repository Actions variables** to actual IDs returned by Render:

| Variable | Resource |
| --- | --- |
| `RENDER_SERVICE_ID_API` | Private API service, `milo-api` |
| `RENDER_SERVICE_ID_WEB` | Public web service, `milo-web` |
| `RENDER_SERVICE_ID_JOBS` | Authorized jobs worker, `milo-jobs` |
| `RENDER_SERVICE_ID_RETENTION` | Retention worker, `milo-retention` |
| `RENDER_AUTO_DEPLOY` | Set to the exact text `true` to deploy after each successful main validation. Leave absent/false for validation-only pushes. |

The API's encryption/service/proxy keys, production origins, and provider credentials belong in Render's protected environment configuration. The workflow does not generate, copy, or infer those credentials. Google/WhatsApp/model configuration and implementation gates remain separate from hosting credentials.

For an authorized manual release, open [Tested release in GitHub Actions](https://github.com/hemantsatishjadhav06-ai/whatsapp-history-ai-/actions/workflows/render-release.yml), select **Run workflow**, choose **main**, and set **deploy** to true. It reruns the same validation gates before invoking:

```sh
uv run --frozen python scripts/render_release.py \
  --commit "$COMMIT_SHA" --output .local/ci-evidence/render-release.json
```

The script uses the configured service IDs and API key. It validates service identity and auto-deploy settings, requests the exact commit, checks completion and reported commit, and deploys API before workers/web. It derives the public URL from Render's trusted HTTPS service metadata and verifies public liveness and backend readiness. A provider error, incorrect commit, missing setting, failed deployment, or failed live probe fails the job. Failed POST requests are not blindly retried. The metadata-only JSON artifact records the actual release outcome; credential-bearing provider responses are not printed.

## Evidence and security gate scope

Artifacts are retained for **14 days** for backend/container/release evidence and **7 days** for browser evidence. They include backend JUnit/results, actual built image IDs and source SHA, functional smoke logs, archive digests, dependency audit reports, both complete Trivy reports, both CycloneDX SBOMs, and the Render release record when deployment was attempted. Container archive files themselves are not uploaded.

The Python production, web production, and independent gateway audit commands fail on reported vulnerabilities or audit errors. Native production dependencies retain the four reviewed base advisories documented in [`dependency-audit.md`](dependency-audit.md): `GHSA-vfj7-8cjw-p6xm`, `GHSA-86w9-cpqp-85rv`, `GHSA-hp3w-g68c-fv3c`, and `GHSA-vcc3-ghjq-m6fr`. The native audit still fails on an API error, an unreviewed base advisory, a critical finding, or counts exceeding the reviewed baseline of **21 high / 8 moderate / 29 affected nodes**. Native artifacts are exported for validation; this does not publish or install a native app.

Trivy retains **all** severities, including unfixed findings, in the complete reports. The separately named release gate blocks **HIGH and CRITICAL findings with a vendor-provided fixed version**. It does not certify zero vulnerabilities. Unfixed Debian findings and the vendored Temporal Rust finding remain visible and require continued remediation/review. Scanner database failures stop validation rather than using an empty result. The summaries display the counts from the reports produced in that run.

Render builds the tested source commit independently. GitHub records the CI image digests; an exact source SHA does not establish that Render's separate build produced byte-identical images, because signed OS updates and external build inputs can change. A production image-digest promotion process would be an additional release mechanism.

## Verification boundary

The workflow files were parsed and checked locally with **actionlint 1.7.7**. Native audit baseline logic was checked against the recorded current audit, including rejection of a new advisory or critical increase. The workflow's strict, hashed production pip-audit command completed with **0 known vulnerabilities**. Local application test evidence is in [`QA_REPORT.md`](QA_REPORT.md).

GitHub Actions run metadata became readable later in this task. For prior commit `8afbdd601fc4c6b0adb63e56e5a134bdb9461eff`, [Backend run 37608263832](https://github.com/hemantsatishjadhav06-ai/whatsapp-history-ai-/actions/runs/37608263832) completed successfully, including both backend jobs. [Milo clients run 37608263585](https://github.com/hemantsatishjadhav06-ai/whatsapp-history-ai-/actions/runs/37608263585) passed every application step, including browser tests, but the overall job failed in the setup-uv cache post-hook with uv exit code 2. Redirected log downloads were blocked, so the precise post-hook cause remains unverified. The current clients workflow explicitly disables that optional cache and starts/stops the disposable Python fixture in its own verified process group, with browser retries disabled.

The current release workflows have not yet been pushed/run at this point in the report. Their remote result must be established from the [actual Actions runs](https://github.com/hemantsatishjadhav06-ai/whatsapp-history-ai-/actions) for the exact new commit, rather than inferred from the prior runs or local tests. GitHub secrets/variables APIs still return **403** for the available integration credentials, so remote protected deployment settings could not be configured. The Render API is now network-reachable but returns **401** without a local Render API key; no authenticated provider deployment or live URL is established by these checks.

This automation does not certify **50,000 simultaneous users**, physical-device/store acceptance, personal WhatsApp history synchronization, or general production auto-replies. See the actual capacity limitations and integration gates in [`LOAD_TEST_REPORT.md`](LOAD_TEST_REPORT.md) and [`readiness.md`](readiness.md).
