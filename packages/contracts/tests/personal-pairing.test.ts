import test from 'node:test';
import assert from 'node:assert/strict';
import { emptyPersonalConsent, internationalPhoneNumber, personalConsentFor, personalQRDrawing, personalWritingExamples, usablePersonalPairingCode, usablePersonalQR, validPersonalConsent, type PersonalPairing } from '../personal-pairing';

const now = Date.parse('2026-10-07T12:00:00Z');
const pairing: PersonalPairing = { connector_id: 'owner-connector', state: 'pairing', poll_after_seconds: 3,
  qr: { value: 'synthetic,temporary,phone-pairing', expires_at: new Date(now + 45_000).toISOString() } };

test('only a current, bounded code for the exact connector may be displayed', () => {
  assert.equal(usablePersonalQR(pairing, 'owner-connector', now)?.value, pairing.qr!.value);
  assert.equal(usablePersonalQR(pairing, 'other-connector', now), null);
  assert.equal(usablePersonalQR({ ...pairing, state: 'connected' }, 'owner-connector', now), null);
  assert.equal(usablePersonalQR(pairing, 'owner-connector', now + 45_000), null);
  for (const expires_at of ['invalid', new Date(now - 1).toISOString(), new Date(now + 60_001).toISOString()])
    assert.equal(usablePersonalQR({ ...pairing, qr: { ...pairing.qr!, expires_at } }, 'owner-connector', now), null);
  for (const value of ['', 'x'.repeat(4097), 'secret\nmaterial', 'secret\0material'])
    assert.equal(usablePersonalQR({ ...pairing, qr: { ...pairing.qr!, value } }, 'owner-connector', now), null);
});

test('new chat choices grant nothing and existing choices require explicit true values', () => {
  const chat = { provider_chat_id: 'synthetic-peer', title: 'A person', kind: 'contact', conversation_id: null };
  assert.deepEqual(personalConsentFor(chat), emptyPersonalConsent());
  assert.deepEqual(personalConsentFor({ ...chat, permissions: { read: true, retain: true, draft: true, share: true } }),
    { ...emptyPersonalConsent(), read: true, retain: true, draft: true });
});

test('learning and draft permissions need read and retain, sending additionally needs recipient agreement', () => {
  for (const kind of ['learn', 'draft', 'send'] as const) {
    assert.equal(validPersonalConsent({ ...emptyPersonalConsent(), [kind]: true }), false);
    assert.equal(validPersonalConsent({ ...emptyPersonalConsent(), read: true, retain: true, [kind]: true }), kind !== 'send');
  }
  assert.equal(validPersonalConsent({ ...emptyPersonalConsent(), read: true, retain: true, send: true, recipient_opted_in: true }), true);
  assert.equal(validPersonalConsent(emptyPersonalConsent()), true);
});

test('local QR drawing preserves dark modules and bounds matrix access to a standard QR version', () => {
  const visits: [number, number][] = [];
  assert.deepEqual(personalQRDrawing({ getModuleCount: () => 21, isDark: (row, column) => { visits.push([row, column]); return row === 0 && column === 0 || row === 20 && column === 20; } }),
    { path: 'M4 4h1v1h-1zM24 24h1v1h-1z', size: 29 });
  assert.equal(visits.length, 441);
  assert.deepEqual(visits.at(-1), [20, 20]);
  for (const size of [0, 20, 22, 178, 1000000, NaN, 21.5]) assert.equal(personalQRDrawing({ getModuleCount: () => size, isDark: () => { throw new Error('Unbounded matrix read'); } }), null);
  assert.equal(personalQRDrawing({ getModuleCount: () => 177, isDark: () => false })?.size, 185);
});

test('phone writing review excludes assistant, peer and other-chat text and never extends beyond the current 30-message batch', () => {
  const row = { id: 'owner-one', text: 'Synthetic phone writing', conversation_id: 'selected-chat', direction: 'outbound', author_kind: 'unknown_owner_outgoing' };
  assert.deepEqual(personalWritingExamples([row, row, { ...row, id: 'assistant', author_kind: 'assistant' },
    { ...row, id: 'incoming', direction: 'inbound' }, { ...row, id: 'other-chat', conversation_id: 'elsewhere' }, { ...row, id: 'already-confirmed', author_kind: 'human_owner' }], 'selected-chat'), [{ id: row.id, text: row.text }]);
  assert.equal(personalWritingExamples(Array.from({ length: 31 }, (_, index) => ({ ...row, id: String(index) })), 'selected-chat').length, 30);
  assert.deepEqual(personalWritingExamples([{ ...row, text: 'x'.repeat(20001) }, { ...row, text: '' }], 'selected-chat'), []);
  assert.throws(() => personalWritingExamples({ messages: [row] }, 'selected-chat'));
});

test('a link code is shown only for the exact connector while pairing and briefly valid', () => {
  const coded: PersonalPairing = { ...pairing, pairing_code: { code: 'ABCD2345', expires_at: new Date(now + 120_000).toISOString() } };
  assert.deepEqual(usablePersonalPairingCode(coded, 'owner-connector', now), { code: 'ABCD-2345', expiresAt: now + 120_000 });
  assert.equal(usablePersonalPairingCode(coded, 'other-connector', now), null);
  assert.equal(usablePersonalPairingCode({ ...coded, state: 'connected' }, 'owner-connector', now), null);
  assert.equal(usablePersonalPairingCode(coded, 'owner-connector', now + 120_000), null);
  assert.equal(usablePersonalPairingCode({ ...coded, pairing_code: { code: 'abcd<23>', expires_at: coded.pairing_code!.expires_at } }, 'owner-connector', now), null);
  assert.equal(usablePersonalPairingCode({ ...coded, pairing_code: { code: 'ABCD2345', expires_at: new Date(now + 600_000).toISOString() } }, 'owner-connector', now), null);
  assert.equal(usablePersonalPairingCode(pairing, 'owner-connector', now), null);
});

test('pairing numbers must be international with a country code', () => {
  assert.equal(internationalPhoneNumber(' +91 76978 74277 '), '+917697874277');
  assert.equal(internationalPhoneNumber('+1 (555) 000-0000'), '+15550000000');
  for (const value of ['7697874277', '+0 1234 5678', '+12', '+91 7697 x', '+1555000000012345']) assert.equal(internationalPhoneNumber(value), null);
});
