// DOM-only Chrome proof. Every page is locally fulfilled, no application is sent
// and synthetic contract hashes cannot grant server/native submission authority.
import assert from 'node:assert/strict';
import test from 'node:test';
import { cp, mkdir, mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { createHash } from 'node:crypto';
import { chromium } from '@playwright/test';
import { canonical } from '../extension/protocol.js';

const vector = JSON.parse(await readFile(new URL('../../backend/tests/fixtures/browser_submission_v5.json', import.meta.url)));
const target = vector.unified5.review_response.payload.target_url;
const mapping = vector.unified5.review_response.payload.field_mapping;
const answers = vector.unified5.review_response.payload.reviewed_answers;
const hash = value => createHash('sha256').update(value).digest('hex');
const bytes = Buffer.from('%PDF-1.4\nSynthetic DOM-only bytes\n%%EOF');
const artifact = { filename: 'Synthetic.pdf', media_type: 'application/pdf', size_bytes: bytes.length, sha256: hash(bytes) };
const reviewId = '00000000-0000-4000-8000-000000000003';
const actionId = '00000000-0000-4000-8000-000000000004';
const html = `<!doctype html><html><head><title>Owned quote DOM control</title><style>.visually-hidden{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}</style></head><body>
<form id="application-form" method="get" action="${target}">${mapping.map(({ id, label }) => `<label for="${id}">${label}*</label><input id="${id}" type="text" maxlength="255" aria-required="true" value="${answers[id]}">`).join('')}
<div id="upload-label-resume">Resume/CV*</div><div class="file-upload" aria-labelledby="upload-label-resume"><label for="resume">Attach</label><input class="visually-hidden" id="resume" type="file"></div><button type="submit">Submit application</button></form>
<script>window.events={file:0,submit:0};const form=document.querySelector('form');form.addEventListener('change',event=>{if(event.target.id!=='resume')return;events.file++;event.target.closest('.file-upload').innerHTML='<div class="file-upload__filename"><p class="body__secondary">Synthetic.pdf</p></div><button type="button" aria-label="Remove file">Remove</button>';});form.addEventListener('submit',event=>{event.preventDefault();events.submit++;});</script></body></html>`;

async function fixture(version) {
  const directory = await mkdtemp(`${tmpdir()}/hirewiz-quote-dom-`); let context; const requests = [], errors = [];
  try {
    const extension = `${directory}/extension`; await mkdir(extension);
    await cp(new URL('../extension/razorpay-adapter.js', import.meta.url), `${extension}/razorpay-adapter.js`);
    await writeFile(`${extension}/manifest.json`, JSON.stringify({ manifest_version: 3, name: 'Owned quote DOM test', version: '1.0', permissions: ['scripting'], host_permissions: ['https://job-boards.greenhouse.io/*'], background: { service_worker: 'background.js', type: 'module' } }));
    await writeFile(`${extension}/background.js`, "import { razorpayAdapter } from './razorpay-adapter.js'; globalThis.adapter = razorpayAdapter;");
    context = await chromium.launchPersistentContext(`${directory}/profile`, { channel: 'chromium', headless: true, serviceWorkers: 'allow', args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`] });
    const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
    await context.route('**/*', route => {
      requests.push([route.request().method(), route.request().url()]);
      return route.request().method() === 'GET' && route.request().url() === target ? route.fulfill({ status: 200, contentType: 'text/html', body: html }) : route.abort();
    });
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message)); await page.goto(target);
    const tab = await worker.evaluate(async url => (await chrome.tabs.query({ url }))[0].id, target);
    const documentId = await worker.evaluate(async tab => (await chrome.scripting.executeScript({ target: { tabId: tab, frameIds: [0] }, func: () => null }))[0].documentId, tab);
    const invoke = (expected, action, protocol = version, upload = undefined) => worker.evaluate(async ({ tab, documentId, mapping, expected, action, protocol, upload }) => {
      const [result] = await chrome.scripting.executeScript({ target: { tabId: tab, documentIds: [documentId] }, func: globalThis.adapter,
        args: [expected, action, { document_id: documentId, frame_id: 0, field_mapping: mapping, review_protocol_version: protocol, ...(upload ? { upload } : {}) }] });
      return result.result;
    }, { tab, documentId, mapping, expected, action, protocol, upload });
    // Use the real attachment adapter to create actual isolated-world continuity.
    const before = await invoke(null, null, 3); assert.equal(before.outcome, 'inspected', before.reason);
    const expectedAttach = { ...before, artifact, review_digest: 'a'.repeat(64), reviewed_answers: {}, current_assisted_values: before.assisted_values };
    const selected = await invoke(expectedAttach, { review_protocol_version: 3, control_type: 'file', field_id: 'resume', allowed_actions: ['attach'],
      attachment: { input_id: 'resume', expected_empty: true }, artifact, artifact_sha256: artifact.sha256, artifact_base64: bytes.toString('base64url'),
      review_id: reviewId, action_id: actionId, document_id: documentId, frame_id: 0, context_digest: before.context_digest, review_digest: expectedAttach.review_digest, deadline: Date.now() + 9000 }, 3);
    assert.equal(selected.outcome, 'selected_only', selected.reason);
    const upload = { review_id: reviewId, action_id: actionId, filename: artifact.filename, status: 'portal_reports_uploaded' };
    const inspection = await invoke(null, null, version, upload); assert.equal(inspection.outcome, 'inspected', inspection.reason); assert.equal(inspection.ready, true);
    const quote = { ...vector.unified5.review_response.billing, quote_expires_at_ms: Date.now() + 60000 };
    const reviewed = { ...inspection, review_digest: 'b'.repeat(64), reviewed_answers: answers,
      reviewed_answers_sha256: hash(canonical({ context_digest: inspection.context_digest, reviewed_answers: answers })), current_assisted_values: answers,
      ...(version === 5 ? { billing: quote } : {}) };
    const action = { review_protocol_version: version, control_type: 'submit', field_id: 'final-submit', allowed_actions: ['submit'], fee_credits: version === 5 ? 3 : 20,
      document_id: documentId, frame_id: 0, context_digest: inspection.context_digest, review_digest: reviewed.review_digest,
      reviewed_answers_sha256: reviewed.reviewed_answers_sha256, artifact_sha256: artifact.sha256, upload, upload_continuity: inspection.upload_continuity,
      submit_control: inspection.submit_control, native_proof_sha256: 'c'.repeat(64), deadline: Date.now() + 9000, ...(version === 5 ? { billing: quote } : {}) };
    return { page, inspection, reviewed, action, click: (value = action, expected = reviewed) => invoke(expected, value, version, upload),
      async close() { await context.close(); await rm(directory, { recursive: true }); await assert.rejects(stat(directory), { code: 'ENOENT' }); assert.deepEqual(errors, []); assert.deepEqual(requests, [['GET', target]], 'No employer or processor requests'); } };
  } catch (failure) { await context?.close(); await rm(directory, { recursive: true, force: true }); throw failure; }
}
for (const version of [4, 5]) test(`actual Chrome DOM protocol ${version} retains exact units and dispatches only the reviewed button`, async () => {
  const f = await fixture(version); try {
    assert.equal(f.inspection.adapter_id, `razorpay-greenhouse-submit-v${version}`);
    assert.equal((await f.click()).outcome, 'click_dispatched');
    assert.deepEqual(await f.page.evaluate(() => window.events), { file: 1, submit: 1 });
  } finally { await f.close(); }
});
test('actual Chrome DOM rejects changed/expired/missing unified billing, legacy price, and a changed current form before any click', async () => {
  const f = await fixture(5); try {
    const changes = [action => { delete action.billing; }, action => { delete action.billing.billing_currency; },
      action => { action.billing.quote_id = `csq_${'6'.repeat(32)}`; }, action => { action.billing.quote_expires_at_ms = Date.now() - 1; },
      action => { action.fee_credits = 20; }, action => { action.review_protocol_version = 4; }];
    for (const change of changes) { const action = structuredClone(f.action); change(action); assert.equal((await f.click(action)).outcome, 'blocked'); }
    await f.page.locator('#first_name').fill('Changed after consent'); assert.equal((await f.click()).outcome, 'blocked');
    assert.deepEqual(await f.page.evaluate(() => window.events), { file: 1, submit: 0 });
  } finally { await f.close(); }
});
