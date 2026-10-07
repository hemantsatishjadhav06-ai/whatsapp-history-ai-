import test from 'node:test';
import assert from 'node:assert/strict';
import { automaticDraftState, type AutomaticDraftChoice } from '../automatic-drafts';

test('automatic preparation loses ready status at its exact expiry even without a server poll', () => {
  const now = Date.parse('2026-10-07T12:00:00Z');
  const choice: AutomaticDraftChoice = { conversation_id: 'synthetic-contact', enabled: true, version: 1,
    expires_at: new Date(now + 1000).toISOString(), max_drafts_per_hour: 3, status: 'ready', reason_code: null, latest_job: null };
  assert.deepEqual(automaticDraftState(choice, now), { status: 'ready', reason: null });
  assert.deepEqual(automaticDraftState(choice, now + 1000), { status: 'blocked', reason: 'GRANT_EXPIRED' });
  for (const expires_at of [null, 'invalid']) assert.equal(automaticDraftState({ ...choice, expires_at }, now).status, 'blocked');
  assert.deepEqual(automaticDraftState({ ...choice, enabled: false }, now), { status: 'disabled', reason: null });
  assert.deepEqual(automaticDraftState({ ...choice, status: 'blocked', reason_code: 'MODEL_DISABLED' }, now), { status: 'blocked', reason: 'MODEL_DISABLED' });
});
