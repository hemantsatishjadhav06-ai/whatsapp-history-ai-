import test from 'node:test';
import assert from 'node:assert/strict';
import { draftRiskFlags, draftRiskWarning } from '../domain';
import { createDemoClient, createDemoSnapshot } from '../demo';
import type { RecordEntity } from '../domain';

test('draft risk warnings list server flags in plain language and stay quiet without flags', () => {
  assert.equal(draftRiskWarning({ id: 'draft', risk_flags: ['link', 'payment'] }), 'Check before sending: contains a link · mentions a payment or bank details');
  assert.equal(draftRiskWarning({ id: 'draft', risk_flags: ['phone_number', 'commitment', 'phone_number'] }),
    'Check before sending: contains a phone number · makes a promise or confirmation');
  assert.equal(draftRiskWarning({ id: 'draft', risk_flags: ['future_code'] }), 'Check before sending: mentions future code');
  for (const risk_flags of [undefined, null, [], 'link', [1, '', null]]) assert.equal(draftRiskWarning({ id: 'draft', risk_flags }), null);
  assert.equal(draftRiskWarning(null), null);
  assert.deepEqual(draftRiskFlags({ id: 'draft', risk_flags: ['link', 7, 'link'] }), ['link']);
});

test('the synthetic demo shows one flagged draft and never keeps stale flags after an edit', async () => {
  const flagged = createDemoSnapshot().drafts.filter(draft => draftRiskFlags(draft).length);
  assert.equal(flagged.length, 1);
  assert.deepEqual(flagged[0].risk_flags, ['link', 'payment', 'commitment']);
  const client = createDemoClient();
  const edited = await client.request<RecordEntity>(`/drafts/${flagged[0].id}`, { method: 'PATCH', body: { text: 'I’ll call you this evening.' } });
  assert.equal(draftRiskWarning(edited), null);
  assert.equal(edited.status, 'needs_approval');
});
