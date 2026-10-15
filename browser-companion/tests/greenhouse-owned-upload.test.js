// Inert event/permission/fetch fixtures only. These tests establish parser and
// one-shot guards; hosted-network-browser.test.js owns genuine Chrome proof.
import assert from "node:assert/strict";
import test, { after, before } from "node:test";
import { GreenhouseOwnedUpload, reviewedUploadPlan, validateSubmittedApplication } from "../extension/greenhouse-owned-upload.js";
import { PRESIGN_URL } from "../extension/greenhouse-native-network.js";
import { canonical, digest } from "../extension/protocol.js";

const NOW = 1800000000000, ORIGIN = "https://owned-resume.s3.amazonaws.com";
const TARGET = "https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/4740333005";
const EXTENSION = "pnjmegppacleipajnifjmbcopjjffklb", UUID = "00000000-0000-4000-8000-00000000000a";
const encoder = new TextEncoder();
const originalNavigator = Object.getOwnPropertyDescriptor(globalThis, "navigator");
before(() => Object.defineProperty(globalThis, "navigator", { value: { userAgent: "Chrome/151.0.0.0" }, configurable: true }));
after(() => originalNavigator ? Object.defineProperty(globalThis, "navigator", originalNavigator) : delete globalThis.navigator);

function presign() {
  const policy = { expiration: new Date(NOW + 600000).toISOString(), conditions: [{ bucket: "owned-resume" }, ["starts-with", "$key", "resume/"]] };
  return { url: ORIGIN, resume: { fields: { acl: "private", policy: Buffer.from(JSON.stringify(policy)).toString("base64"), "x-amz-signature": "a".repeat(64) }, key: "resume/{timestamp}-{unique_id}.pdf" } };
}
function policyChange(snapshot, mutate) {
  const policy = JSON.parse(Buffer.from(snapshot.resume.fields.policy, "base64")); mutate(policy);
  snapshot.resume.fields.policy = Buffer.from(JSON.stringify(policy)).toString("base64");
}
const parser = snapshot => reviewedUploadPlan(snapshot, encoder.encode(JSON.stringify(snapshot)), { inert_fixture: true }, { now: NOW, uniqueId: UUID });
class Event {
  constructor() { this.listeners = new Set(); }
  addListener(listener) { this.listeners.add(listener); }
  removeListener(listener) { this.listeners.delete(listener); }
  emit(value) { for (const listener of this.listeners) listener(value); }
}
async function fixture({ formData = false, postFailure = false, permission = () => true } = {}) {
  const webRequest = Object.fromEntries(["onBeforeRequest", "onHeadersReceived", "onBeforeRedirect", "onCompleted", "onErrorOccurred"].map(name => [name, new Event()]));
  webRequest.handlerBehaviorChanged = async () => {};
  const calls = [], dispatches = [], permissions = [];
  const state = { now: NOW, cancelled: false };
  const chromeApi = { runtime: { id: EXTENSION }, webRequest, permissions: { contains: async value => { permissions.push(value); return permission(value, permissions.length); } } };
  const snapshot = presign();
  const owned = new GreenhouseOwnedUpload({ chromeApi, now: () => state.now, fetcher: async (url, options) => {
    calls.push({ url, method: options.method });
    const post = options.method === "POST", requestId = post ? "owned-upload" : "owned-presign";
    const normalized = post ? ORIGIN + "/" : PRESIGN_URL;
    assert.equal(url, post ? ORIGIN : PRESIGN_URL);
    assert.equal(options.credentials, "omit"); assert.equal(options.redirect, "error"); assert.equal(options.cache, "no-store");
    const detail = { url: normalized, method: options.method, requestId, initiator: `chrome-extension://${EXTENSION}`, tabId: -1, frameId: -1, timeStamp: state.now };
    if (post) {
      assert.ok(options.body instanceof ArrayBuffer);
      const sent = options.body.slice(0); dispatches.push(sent);
      detail.requestBody = formData ? { formData: { ...Object.fromEntries(Object.entries(owned.prepared.formFields).map(([key, value]) => [key, [value]])), file: ["exact.pdf"] } } : { raw: [{ bytes: sent }] };
    }
    webRequest.onBeforeRequest.emit(detail);
    if (postFailure && post) throw new Error("Synthetic lost original response; no retry");
    webRequest.onHeadersReceived.emit({ url: normalized, requestId, statusCode: post ? 204 : 200, responseHeaders: [] });
    webRequest.onCompleted.emit({ url: normalized, requestId, statusCode: post ? 204 : 200, fromCache: false, ip: "127.0.0.1", timeStamp: state.now + 1 });
    return post ? new Response(null, { status: 204 }) : Response.json(snapshot);
  } });
  const plan = await owned.prepare(TARGET, "c".repeat(64));
  const bytes = encoder.encode("Owned exact PDF bytes\n\0\u00ff");
  const lease = { upload_plan: structuredClone(plan), allowed_actions: ["upload_resume"], review_protocol_version: 3, target_url: TARGET,
    deadline: NOW + 1000, artifact: { filename: "exact.pdf", media_type: "application/pdf", size_bytes: bytes.length, sha256: await digest(bytes) },
    review_id: UUID, action_id: "00000000-0000-4000-8000-00000000000b", review_digest: "d".repeat(64), document_id: "owned-document", native_context: { tab_id: 44, time_zone: "UTC" } };
  let before = 0;
  const upload = (options = {}) => owned.upload({ lease, bytes, plan, beforeDispatch: async () => { before++; }, cancelled: () => state.cancelled, ...options });
  return { owned, calls, dispatches, permissions, plan, lease, bytes, state, upload, before: () => before };
}

test("presign parser emits distinct v2 owned-buffer provenance and exact reviewed hashes", async () => {
  const snapshot = presign(), bytes = encoder.encode(JSON.stringify(snapshot));
  const { plan, formFields } = await parser(snapshot);
  assert.equal(plan.transport, "greenhouse-owned-upload-browser-https-v2");
  assert.equal(plan.body_provenance, "extension_owned_arraybuffer"); assert.equal(plan.presign_url, PRESIGN_URL);
  assert.equal(plan.storage_origin, ORIGIN); assert.equal(plan.storage_key, `resume/${NOW}-${UUID.replaceAll("-", "")}.pdf`);
  assert.equal(plan.expires_at_ms, NOW + 300000);
  assert.equal(plan.policy_sha256, await digest(Buffer.from(snapshot.resume.fields.policy, "base64")));
  assert.equal(plan.fields_sha256, await digest(canonical(formFields))); assert.equal(plan.presign_response_sha256, await digest(bytes));
  assert.deepEqual(formFields, { utf8: "✓", ...snapshot.resume.fields, key: plan.storage_key, authenticity_token: "1234", "Content-Type": "application/octet-stream" });
  assert.equal(plan.policy, undefined); assert.equal(plan["x-amz-signature"], undefined);
});

for (const [name, mutate] of [
  ["unknown snapshot key", value => { value.extra = true; }],
  ["unknown resume key", value => { value.resume.extra = true; }],
  ["non-storage origin", value => { value.url = "https://unapproved.example"; }],
  ["origin suffix attack", value => { value.url = ORIGIN + ".evil.example"; }],
  ["origin path", value => { value.url += "/different"; }],
  ["origin credentials", value => { value.url = "https://user:password@owned-resume.s3.amazonaws.com"; }],
  ["parent key", value => { value.resume.key = "resume/../unapproved.pdf"; }],
  ["unsupported interpolation", value => { value.resume.key = "resume/{filename}.pdf"; }],
  ["duplicate interpolation", value => { value.resume.key = "resume/{timestamp}-{timestamp}.pdf"; }],
  ["unapproved prefix", value => { value.resume.key = "different/exact.pdf"; }],
  ["reserved file field", value => { value.resume.fields.file = "unreviewed"; }],
  ["reserved key field", value => { value.resume.fields.key = "unreviewed"; }],
  ["reserved content type field", value => { value.resume.fields["Content-Type"] = "unreviewed"; }],
  ["non-string field", value => { value.resume.fields.acl = true; }],
  ["too few fields", value => { delete value.resume.fields.acl; }],
  ["too many fields", value => { Object.assign(value.resume.fields, Object.fromEntries(Array.from({ length: 22 }, (_, index) => [`extra_${index}`, "value"]))); }],
  ["oversized field", value => { value.resume.fields.acl = "a".repeat(16385); }],
  ["oversized aggregate fields", value => { Object.assign(value.resume.fields, { acl: "a".repeat(16000), "x-amz-signature": "b".repeat(16000), extra: "c".repeat(1000) }); }],
  ["empty field", value => { value.resume.fields.acl = ""; }],
  ["invalid base64 policy", value => { value.resume.fields.policy = "not-base64?"; }],
  ["invalid UTF8 policy", value => { value.resume.fields.policy = Buffer.from([255]).toString("base64"); }],
  ["invalid JSON policy", value => { value.resume.fields.policy = Buffer.from("not json").toString("base64"); }],
  ["unapproved policy field", value => policyChange(value, policy => { policy.extra = true; })],
  ["expired policy", value => policyChange(value, policy => { policy.expiration = new Date(NOW).toISOString(); })],
  ["insufficient policy life", value => policyChange(value, policy => { policy.expiration = new Date(NOW + 15000).toISOString(); })],
  ["unbounded policy life", value => policyChange(value, policy => { policy.expiration = new Date(NOW + 7200001).toISOString(); })],
  ["wrong bucket", value => policyChange(value, policy => { policy.conditions[0].bucket = "other-bucket"; })],
  ["missing key condition", value => policyChange(value, policy => { policy.conditions[1] = { acl: "private" }; })],
]) test(`presign parser rejects ${name}`, async () => { const value = presign(); mutate(value); await assert.rejects(() => parser(value)); });

test("exact policy key is accepted without inventing a prefix and retains its own expiry", async () => {
  const snapshot = presign(); snapshot.resume.key = "resume/exact.pdf";
  policyChange(snapshot, policy => { policy.expiration = new Date(NOW + 20000).toISOString(); policy.conditions[1] = { key: snapshot.resume.key }; });
  const result = await parser(snapshot); assert.equal(result.plan.storage_key, "resume/exact.pdf"); assert.equal(result.plan.expires_at_ms, NOW + 20000);
});

for (const formData of [false, true]) test(`one owned immutable fetch, exact file bytes, no retry (parsed form=${formData})`, async () => {
  const f = await fixture({ formData }), before = f.bytes.slice();
  const result = await f.upload();
  assert.equal(result.version, "greenhouse_owned_upload_browser_https_v2");
  assert.equal(result.native.request_body_source, "extension_owned_arraybuffer"); assert.equal(result.native.native_body_crosscheck, formData ? "form_fields" : "raw");
  assert.equal(result.native.https_validation, "browser_default"); assert.equal(result.native.certificate_sha256, undefined);
  assert.equal(result.artifact_sha256, await digest(before)); assert.equal(result.native.request_body_sha256, result.multipart_sha256);
  assert.equal(result.multipart_sha256, await digest(f.dispatches[0])); assert.equal(result.multipart_size, f.dispatches[0].byteLength);
  const multipart = Buffer.from(f.dispatches[0]), header = multipart.indexOf(Buffer.from('filename="exact.pdf"'));
  const start = multipart.indexOf(Buffer.from("\r\n\r\n"), header) + 4, end = multipart.indexOf(Buffer.from("\r\n------"), start);
  assert.deepEqual(new Uint8Array(multipart.subarray(start, end)), before);
  assert.equal(f.before(), 1); assert.equal(f.owned.prepared, null); assert.equal(f.owned.consumed, true);
  await assert.rejects(() => f.upload()); assert.equal(f.calls.filter(call => call.method === "POST").length, 1);
  assert.deepEqual(f.bytes, before); // Sealed caller bytes are not rewritten.
});

for (const [name, mutate] of [
  ["changed file bytes", f => { f.bytes[0] ^= 1; }],
  ["changed size", f => { f.lease.artifact.size_bytes++; }],
  ["changed artifact hash", f => { f.lease.artifact.sha256 = "f".repeat(64); }],
  ["changed lease plan", f => { f.lease.upload_plan.storage_key = "resume/other.pdf"; }],
  ["changed public plan", f => { f.plan.fields_sha256 = "f".repeat(64); }],
  ["changed target", f => { f.lease.target_url = TARGET + "1"; }],
  ["changed action", f => { f.lease.allowed_actions = ["attach"]; }],
  ["changed version", f => { f.lease.review_protocol_version = 4; }],
  ["expired lease", f => { f.state.now = f.lease.deadline; }],
  ["expired plan", f => { f.plan.expires_at_ms = NOW; f.lease.upload_plan = structuredClone(f.plan); }],
  ["cancelled", f => { f.state.cancelled = true; }],
  ["lost private prepared state", f => { f.owned.clear(); }],
]) test(`pre-dispatch ${name} never sends resume bytes`, async () => {
  const f = await fixture(); mutate(f); await assert.rejects(() => f.upload());
  assert.equal(f.calls.filter(call => call.method === "POST").length, 0); assert.equal(f.before(), 0);
});

for (const mode of ["cancel", "deadline", "callback-error", "permission"]) test(`dispatch recheck ${mode} consumes authorization without POST`, async () => {
  const f = await fixture({ permission: (_value, count) => mode !== "permission" || count < 3 });
  await assert.rejects(() => f.upload({ beforeDispatch: async () => {
    if (mode === "cancel") f.state.cancelled = true;
    if (mode === "deadline") f.state.now = f.lease.deadline;
    if (mode === "callback-error") throw new Error("Changed exact portal context");
  } }));
  assert.equal(f.owned.consumed, true); assert.equal(f.owned.prepared, null);
  assert.equal(f.calls.filter(call => call.method === "POST").length, 0);
  await assert.rejects(() => f.upload()); assert.equal(f.calls.filter(call => call.method === "POST").length, 0);
});

for (const gate of ["permission", "callback"]) for (const kind of ["bytes", "lease", "plan"])
  test(`private snapshot prevents caller ${kind} mutation across ${gate} yield from changing the approved send`, async () => {
    let f;
    const mutate = () => {
      if (kind === "bytes") f.bytes[0] ^= 1;
      if (kind === "lease") { f.lease.artifact.filename = "unapproved.pdf"; f.lease.target_url = TARGET + "1"; f.lease.native_context.tab_id++; }
      if (kind === "plan") { f.plan.storage_origin = "https://different.s3.amazonaws.com"; f.plan.storage_key = "different/unapproved.pdf"; f.plan.fields_sha256 = "f".repeat(64); }
    };
    f = await fixture({ permission: (_value, count) => { if (gate === "permission" && count === 2) mutate(); return true; } });
    const original = f.bytes.slice(), expectedKey = f.plan.storage_key;
    const result = await f.upload({ beforeDispatch: async () => { if (gate === "callback") mutate(); } });
    const multipart = Buffer.from(f.dispatches[0]), header = multipart.indexOf(Buffer.from('filename="exact.pdf"'));
    assert.ok(header >= 0);
    const start = multipart.indexOf(Buffer.from("\r\n\r\n"), header) + 4, end = multipart.indexOf(Buffer.from("\r\n------"), start);
    assert.deepEqual(new Uint8Array(multipart.subarray(start, end)), original);
    assert.equal(await digest(multipart.subarray(start, end)), result.artifact_sha256);
    assert.equal(result.filename, "exact.pdf"); assert.equal(result.target_url, TARGET); assert.equal(result.tab_id, 44);
    assert.equal(result.uploaded_url, `${ORIGIN}/${expectedKey}`);
    assert.equal(f.calls.filter(call => call.method === "POST").length, 1); assert.equal(f.owned.prepared, null);
  });

test("lost original owned upload response is unknown and cannot issue another POST", async () => {
  const f = await fixture({ postFailure: true });
  await assert.rejects(() => f.upload()); assert.equal(f.owned.prepared, null); assert.equal(f.owned.consumed, true);
  await assert.rejects(() => f.upload()); assert.equal(f.calls.filter(call => call.method === "POST").length, 1);
});

test("returned public plan cannot modify the retained private recipient or bytes", async () => {
  const f = await fixture(); f.plan.storage_origin = "https://different.s3.amazonaws.com";
  f.lease.upload_plan = structuredClone(f.plan); await assert.rejects(() => f.upload());
  assert.equal(f.calls.filter(call => call.method === "POST").length, 0);
});

const application = { first_name: "Owned", last_name: "Candidate", email: "owned@example.test", employments: [], attachments: {}, answers_attributes: {} };
const submitted = value => encoder.encode(JSON.stringify(value));
test("final serializer accepts only the exact reviewed application with bounded known transport fields", async () => {
  const hash = await digest(canonical(application)), validate = validateSubmittedApplication(application, hash);
  assert.deepEqual(await validate(submitted({ job_application: application, fingerprint: "owned-fingerprint", authenticity_token: "owned-token", request_token: "owned-request", "g-recaptcha-enterprise-token": "owned-captcha", captcha_retried: true })), { application_sha256: hash });
});
for (const [name, value] of [
  ["unknown envelope key", { job_application: application, other: "unreviewed" }],
  ["unknown application key", { job_application: { ...application, other: "unreviewed" } }],
  ["changed reviewed answer", { job_application: { ...application, first_name: "Different" } }],
  ["missing application", { fingerprint: "owned" }],
  ["array envelope", [application]],
  ["non-string transport field", { job_application: application, request_token: false }],
  ["empty token", { job_application: application, authenticity_token: "" }],
  ["oversized fingerprint", { job_application: application, fingerprint: "a".repeat(4097) }],
  ["oversized token", { job_application: application, request_token: "a".repeat(8193) }],
  ["false captcha retry", { job_application: application, captcha_retried: false }],
  ["two conflicting captcha modes", { job_application: application, security_code: "owned", "g-recaptcha-enterprise-token": "owned" }],
]) test(`final serializer rejects ${name}`, async () => {
  const hash = await digest(canonical(application));
  await assert.rejects(() => validateSubmittedApplication(application, hash)(submitted(value)));
});

test("final serializer rejects invalid UTF8, JSON and review hashes", async () => {
  const hash = await digest(canonical(application));
  for (const bytes of [new Uint8Array([255]), encoder.encode("not json")]) await assert.rejects(() => validateSubmittedApplication(application, hash)(bytes));
  for (const invalid of ["not-a-digest", "f".repeat(64)]) await assert.rejects(() => validateSubmittedApplication(application, invalid)(submitted({ job_application: application })));
  const duplicate=encoder.encode(`{"job_application":{"first_name":"Unapproved"},"job_application":${JSON.stringify(application)}}`);
  await assert.rejects(()=>validateSubmittedApplication(application,hash)(duplicate));
});
