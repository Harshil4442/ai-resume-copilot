import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import test from 'node:test';
import { AssistanceRuntime } from '../extension/assistance-runtime.js';
import { AssistanceTransport } from '../extension/assistance-transport.js';
import { SUBMISSION_BILLING_KEYS, isSubmissionBilling, submissionUnits } from '../extension/submission-billing.js';
import { base64url, canonical, digest } from '../extension/protocol.js';
import { legacySubmissionDispatch } from './legacy-submission-dispatch-fixture.js';

// Unsigned, synthetic contract vectors test client parsing; they are not native receipts.
const vector = JSON.parse(readFileSync(new URL('../../backend/tests/fixtures/browser_submission_v5.json', import.meta.url)));
const now = vector.unified5.lease_payload.issued_at_ms;
const configuration = { mode: 'reviewed_assistance', websiteOrigin: 'https://www.hirewizhq.com',
  portalOrigin: 'https://job-boards.greenhouse.io', extensionId: 'pnjmegppacleipajnifjmbcopjjffklb' };
const copy = value => structuredClone(value);
const billing = () => copy(vector.unified5.review_response.billing);

test('complete independent protocol four and five vectors retain different units and preimages', async () => {
  for (const name of ['legacy4', 'unified5']) {
    assert.equal(await digest(canonical(vector[name].review_response.payload)), vector[name].review_sha256);
    assert.equal(await digest(canonical(vector[name].lease_payload)), vector[name].lease_sha256);
  }
  assert.equal(isSubmissionBilling(billing()), true);
  assert.equal(submissionUnits(4), '20 legacy service credits');
  assert.equal(submissionUnits(5), '3 unified credits');
  assert.throws(() => submissionUnits(3));
  assert.notEqual(vector.legacy4.pricing_sha256, vector.unified5.pricing_sha256);
});
test('legacy fixture changes only the exact original preparation selector and refuses drift or duplicate selectors', () => {
  const source = readFileSync(new URL('../extension/background.js', import.meta.url), 'utf8');
  const changed = legacySubmissionDispatch(source);
  assert.equal(changed.replace('return current.prepare(message.application_id, 4);','return current.prepare(message.application_id, 5);'), source);
  assert.throws(() => legacySubmissionDispatch(source.replace('message.application_id, 5','message.application_id, 6')));
  assert.throws(() => legacySubmissionDispatch(source + source));
});
for (const key of SUBMISSION_BILLING_KEYS) test(`unified quote cannot default missing ${key}`, () => {
  const value = billing(); delete value[key]; assert.equal(isSubmissionBilling(value), false);
});
for (const [key, value] of [['credits', 20], ['credits', '3'], ['credits', true], ['billing_currency', 'INR'],
  ['operation', 'browser_submission'], ['quote_id', 'csq_BAD'], ['route_id', '../route'], ['route_version', ''],
  ['quote_expires_at_ms', 0], ['quote_expires_at_ms', 1.5], ['quote_expires_at_ms', Number.MAX_SAFE_INTEGER + 1],
  ['input_fingerprint', 'A'.repeat(64)], ['schema_version', 'legacy'], ['tariff_version', 'other'], ['extra', 3]]) {
  test(`unified quote refuses changed ${key}/${value}`, () => assert.equal(isSubmissionBilling({ ...billing(), [key]: value }), false));
}

async function signedTransport(version = 5) {
  const key = await webcrypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign', 'verify']);
  let clock = now;
  const transport = new AssistanceTransport({ configuration, identity: { device_id: vector.unified5.lease_payload.device_id },
    beforePost: async () => {}, now: () => clock });
  transport.authorityKey = await webcrypto.subtle.exportKey('jwk', key.publicKey);
  const sign = async payload => ({ payload, signature: base64url(await webcrypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, key.privateKey,
    new TextEncoder().encode(`hirewiz.browser-assistance.lease.v1\n${canonical(payload)}`))) });
  return { transport, sign, payload: copy(vector[version === 5 ? 'unified5' : 'legacy4'].lease_payload), advance: n => { clock += n; } };
}
test('genuine ECDSA verifies the complete quote without extending the original ten-second lease', async () => {
  const f = await signedTransport(); const envelope = await f.sign(f.payload);
  assert.deepEqual(await f.transport.verifyLease(envelope), f.payload);
  envelope.payload.billing.credits = 20;
  await assert.rejects(f.transport.verifyLease(envelope), /signature/);
  const old = await signedTransport(4); assert.deepEqual(await old.transport.verifyLease(await old.sign(old.payload)), old.payload);
  f.advance(10000); await assert.rejects(f.transport.verifyLease(await f.sign(f.payload)), /expired|changed/);
});
for (const change of [p => { delete p.billing; }, p => { p.billing.credits = 20; }, p => { p.deadline = p.billing.quote_expires_at_ms + 1; },
  p => { p.billing.quote_expires_at_ms = p.deadline - 1; }, p => { p.review_protocol_version = 4; }, p => { p.fee_credits = 20; }]) {
  test('even a signed authority envelope cannot change the unified price/version/expiry contract', async () => {
    const f = await signedTransport(); change(f.payload); await assert.rejects(f.transport.verifyLease(await f.sign(f.payload)));
  });
}

async function setup(version = 5) {
  const entry = copy(vector[version === 5 ? 'unified5' : 'legacy4']);
  const review = { ...entry.review_response, state: 'approved' }, payload = review.payload;
  let clock = now; const calls = [], writes = []; let clicks = 0;
  const inspection = { ...payload, url: payload.target_url, ready: true, assisted_values: payload.assisted_values };
  const transport = { configuration, identity: { device_id: entry.lease_payload.device_id },
    device: async () => ({ state: 'active', ...vector.unified5.availability, review: copy(review) }),
    verifyLease: async lease => lease,
    request: async (operation, body) => {
      calls.push([operation, copy(body)]);
      if (operation === 'begin') return { state: 'begun', lease: copy(entry.lease_payload) };
      if (operation === 'outcome' || operation === 'reconcile') return { ...copy(review), state: 'unknown', submission_status: 'unknown', payload: {} };
      if (operation === 'cancel') return {};
      throw new Error(`Unexpected synthetic operation: ${operation}`);
    } };
  const browser = { inspect: async () => copy(inspection), nativeSubmit: async (_url, _mapping, expected, lease) => {
    clicks++; assert.equal(lease.review_protocol_version, version); assert.equal(lease.fee_credits, version === 5 ? 3 : 20);
    if (version === 5) assert.deepEqual(expected.billing, billing()); else assert.equal(expected.billing, undefined);
    return { outcome: 'click_dispatched' };
  }, observe: async () => ({ status: 'unknown' }) };
  const runtime = await new AssistanceRuntime({ transport, browser, now: () => clock,
    store: { read: async () => null, write: async state => writes.push(copy(state)) } }).initialise();
  runtime.inspection = copy(inspection); await runtime.save({ application_id: payload.application_id });
  await runtime.acceptReview(copy(review)); await runtime.save({ review_id: review.id, phase: 'approved' });
  transport.device = async () => ({ state: 'active', ...vector.unified5.availability, review: copy(runtime.review) });
  return { runtime, transport, browser, calls, writes, entry, review, clicks: () => clicks, advance: n => { clock += n; } };
}
for (const version of [4, 5]) test(`accepted protocol ${version} makes one possible send and retains its exact held units without replay`, async () => {
  const f = await setup(version); const result = await f.runtime.fill();
  assert.equal(f.clicks(), 1); assert.equal(result.phase, 'unknown'); assert.equal(f.runtime.status().can_reconcile, true);
  assert.match(result.message, version === 5 ? /3 unified credits/ : /20 legacy service credits/);
  await assert.rejects(f.runtime.fill()); assert.equal(f.clicks(), 1);
  const read = await f.runtime.reconcile(); assert.equal(read.phase, 'unknown'); assert.equal(f.clicks(), 1);
  assert.equal(f.calls.filter(([name]) => name === 'begin').length, 1);
  assert.equal(f.writes.every(state => !('billing' in state)), true);
});
test('a retained legacy approval remains executable while new unified preparation is unavailable', async () => {
  const f = await setup(4); f.transport.device = async () => ({ state: 'active', submit_available: false, ...{ review: copy(f.review) } });
  const result = await f.runtime.fill(); assert.equal(result.phase, 'unknown'); assert.equal(f.clicks(), 1); assert.equal(result.submit_available, false);
});
test('a changed quote under the same review never replaces its retained consent snapshot', async () => {
  const f = await setup(); const changed = copy(f.review);
  changed.billing.quote_id = 'csq_' + '6'.repeat(32); changed.payload.billing = copy(changed.billing); changed.payload.pricing = copy(changed.billing);
  changed.review_digest = await digest(canonical(changed.payload));
  await assert.rejects(f.runtime.acceptReview(changed), /retained submission quote changed/); assert.deepEqual(f.runtime.review.billing, billing());
  assert.equal(f.calls.length, 0); assert.equal(f.clicks(), 0);
});
test('even unchanged billing cannot replace the priced payload digest under a retained review', async () => {
  const f = await setup(); const changed = copy(f.review);
  changed.payload.disclosures.push('Changed after the first exact review'); changed.review_digest = await digest(canonical(changed.payload));
  await assert.rejects(f.runtime.acceptReview(changed), /retained submission quote changed/); assert.equal(f.calls.length, 0); assert.equal(f.clicks(), 0);
});
for (const expireAt of ['check', 'inspection']) test(`quote expiry at ${expireAt} prevents begin and employer action`, async () => {
  const f = await setup();
  if (expireAt === 'check') f.advance(60000);
  else { const original = f.browser.inspect; f.browser.inspect = async (...args) => { f.advance(60000); return original(...args); }; }
  await assert.rejects(f.runtime.fill()); assert.equal(f.calls.length, 0); assert.equal(f.clicks(), 0);
});
test('a lease quote mismatch after begin becomes unknown without an employer action or second begin', async () => {
  const f = await setup(); f.entry.lease_payload.billing.quote_id = 'csq_' + '6'.repeat(32);
  await assert.rejects(f.runtime.fill(), /authorization changed/); assert.equal(f.runtime.state.phase, 'unknown'); assert.equal(f.clicks(), 0);
  await assert.rejects(f.runtime.fill()); assert.equal(f.calls.filter(([name]) => name === 'begin').length, 1);
});
test('a lost begin acknowledgement retains the new quote and review identity through cancellation/status, never replaying begin', async () => {
  const f = await setup(); const request = f.transport.request;
  f.transport.request = async (operation, body) => { if (operation === 'begin') { f.calls.push([operation, body]); throw new Error('Owned acknowledgement loss'); } return request(operation, body); };
  await assert.rejects(f.runtime.fill(), /acknowledgement loss/); assert.equal(f.runtime.state.phase, 'unknown'); assert.equal(f.runtime.review.submission, undefined);
  await f.runtime.cancel(); assert.equal(f.runtime.state.review_id, f.review.id); assert.equal(f.runtime.state.phase, 'unknown');
  await assert.rejects(f.runtime.fill()); assert.equal(f.calls.filter(([name]) => name === 'begin').length, 1); assert.equal(f.clicks(), 0);
});
test('clearing an accepted preclaim quote explains the new-application requirement without promising an unobserved refund', async () => {
  const f = await setup(); const status = await f.runtime.cancel(); assert.match(status.message, /new application ID/);
  assert.equal(status.application_id, null); assert.equal(status.review_id, null); assert.equal(f.clicks(), 0); assert.deepEqual(f.calls.map(([name]) => name), ['cancel']);
});
for (const availability of [{ submit_available: true }, { unified_submit_available: true, submit_protocol_versions: [4] },
  { unified_submit_available: false, submit_protocol_versions: [4, 5] }, { unified_submit_available: true, submit_protocol_versions: [5, 4] }]) {
  test('new unified preparation never infers eligibility from a legacy fee or availability', async () => {
    const f = await setup(); await f.runtime.save({ review_id: null, phase: 'active' });
    f.transport.device = async () => ({ state: 'active', ...availability });
    await assert.rejects(f.runtime.prepare('apply_synthetic', 5), /Unified submission is unavailable/);
    assert.equal(f.calls.length, 0); assert.equal(f.clicks(), 0);
  });
}
test('new client explicitly requests protocol five on application and inspection, never negotiates from its fee', async () => {
  const f = await setup(); await f.runtime.save({ review_id: null, phase: 'active' }); f.runtime.review = null;
  const context = { adapter_version: 'razorpay-greenhouse-submit-v5', form: 'owned synthetic context' };
  const contextDigest = await digest(canonical(context)); const original = copy(f.review);
  original.state = 'draft'; original.payload.context_digest = contextDigest;
  original.review_digest = await digest(canonical(original.payload));
  f.browser.inspect = async () => ({ ...copy(original.payload), url: original.payload.target_url, origin: configuration.portalOrigin, context,
    context_digest: contextDigest, outcome: 'inspected', ready: true });
  f.transport.device = async () => ({ state: 'active', ...vector.unified5.availability });
  f.transport.request = async (operation, body) => {
    f.calls.push([operation, copy(body)]);
    if (operation === 'application') return { application_id: original.payload.application_id, target_url: original.payload.target_url,
      review_protocol_version: 5, field_mapping: original.payload.field_mapping, upload: original.payload.upload };
    if (operation === 'inspect') return original;
    assert.fail('No other operation during inspection');
  };
  const result = await f.runtime.prepare('apply_synthetic', 5); assert.equal(result.phase, 'draft'); assert.equal(f.clicks(), 0);
  assert.equal(f.calls.length, 2); assert.equal(f.calls[0][1].review_protocol_version, 5); assert.equal(f.calls[1][1].inspection.review_protocol_version, 5);
  assert.equal('billing' in f.calls[1][1], false); assert.equal('credits' in f.calls[0][1], false);
});
test('a backend downgrade response stops before browser inspection or another signed request', async () => {
  const f = await setup(); await f.runtime.save({ review_id: null, phase: 'active' }); let inspected = 0;
  f.browser.inspect = async () => { inspected++; assert.fail('Downgrade cannot inspect'); };
  f.transport.request = async (operation, body) => { f.calls.push([operation, body]); return { application_id: 'apply_synthetic', target_url: f.review.payload.target_url, field_mapping: [], review_protocol_version: 4 }; };
  await assert.rejects(f.runtime.prepare('apply_synthetic', 5), /control version is unavailable/);
  assert.equal(inspected, 0); assert.equal(f.calls.length, 1); assert.equal(f.clicks(), 0);
});
