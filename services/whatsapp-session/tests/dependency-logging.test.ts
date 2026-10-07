import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
test("actual libsignal closed-session logging cannot emit synthetic Signal key material", () => {
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", `
    const { silenceDependencyConsole } = await import('./src/isolated-console.ts');
    silenceDependencyConsole();
    const { createRequire } = await import('node:module');
    const require = createRequire(import.meta.url);
    const SessionRecord = require('libsignal/src/session_record.js');
    const record = new SessionRecord();
    const session = { indexInfo: { closed: 42 }, privateKey: 'SYNTHETIC_SIGNAL_KEY_DO_NOT_LOG' };
    record.closeSession(session);
    console.info('SYNTHETIC_SIGNAL_KEY_DO_NOT_LOG');
  `], { cwd: new URL("..", import.meta.url), encoding: "utf8", timeout: 10_000 });
  assert.equal(result.status, 0); assert.equal(result.stdout, ""); assert.equal(result.stderr, "");
});
test("invalid session URL configuration does not expose the supplied credential string", () => {
  const result = spawnSync(process.execPath, ["src/runtime.ts"], { cwd: new URL("..", import.meta.url), encoding: "utf8", timeout: 10_000,
    env: { ...process.env, DATABASE_URL: "MALFORMED_SYNTHETIC_CREDENTIAL_DO_NOT_LOG", PYTHON_AUTHORITY_URL: "http://127.0.0.1:8000" } });
  assert.notEqual(result.status, 0); assert.equal(result.stderr.includes("MALFORMED_SYNTHETIC_CREDENTIAL_DO_NOT_LOG"), false);
  assert.equal(result.stderr.includes("Invalid session database configuration"), true);
});
