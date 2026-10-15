import assert from "node:assert/strict";
import test from "node:test";
import { Companion, base64url, decode64, digest, sign, verify } from "../extension/protocol.js";
import { fixtureAuthority, fields } from "../fixtures/authority.js";

async function fixture() {
  const authority = await fixtureAuthority();
  const identity = { device_id: "device_fixture", key_sha256: "b".repeat(64) };
  let checkpoint = null;
  const page = { url: `${authority.configuration.portalOrigin}/fixture/apply`, origin: authority.configuration.portalOrigin, frame_id: 0, document_id: "document_fixture", permission: true,
    user_id: "owner_fixture", tenant_id: "tenant_fixture", employer_key: "synthetic_employer", canonical_opening_key: "synthetic_employer:opening_fixture", form_version: "fixture-form-v1", fields: structuredClone(fields), checkpoint: null };
  const writes = [];
  const browser = { inspect: async () => structuredClone(page), fill: async (_target, action) => {
    if (!page.permission || page.document_id !== action.document_id || page.checkpoint || page.tenant_id !== action.command.tenant_id || Date.now() >= action.deadline) return { outcome: "blocked", reason: "DOM identity changed" };
    writes.push({ id: action.field.id, value: action.value }); return { outcome: "filled" };
  } };
  const transport = Object.fromEntries(["enroll", "command", "artifact", "approve", "authorize", "begin", "complete", "cancel"].map((operation) => [operation, async (...args) => {
    let body; let claim;
    if (operation === "enroll") body = { grant: args[0] };
    else if (operation === "command") { body = { application_id: args[0] }; claim = args[1]; }
    else if (["artifact", "cancel"].includes(operation)) { body = { command_id: args[0].jti }; claim = args[1]; }
    else if (operation === "approve") { body = { command_id: args[0].jti, review_digest: args[1], allowed_actions: ["fill"] }; claim = args[2]; }
    else if (operation === "authorize") { body = { command_id: args[0].jti, field_id: args[1], review_digest: args[2] }; claim = args[3]; }
    else { body = { action_id: operation === "begin" ? args[0].jti : args[0].action_id }; claim = args[1]; }
    return authority.operate(operation, body, identity, claim);
  }]));
  const store = { read: async () => checkpoint, write: async (value) => { checkpoint = structuredClone(value); } };
  const engine = new Companion({ configuration: authority.configuration, identity, transport, browser, store });
  await engine.enroll("local-owner-grant");
  return { authority, engine, identity, transport, browser, store, page, writes, checkpoint: () => checkpoint };
}
async function prepare(f) { return f.engine.prepare("app_fixture"); }
async function fill(f, review) { return f.engine.approveAndFill(review.command.jti, review.review_digest, true); }

test("exact approval and online per-field begin precede every DOM mutation; no upload/submit actions exist", async () => {
  const f = await fixture(); const review = await prepare(f);
  assert.equal(f.writes.length, 0);
  const original = f.browser.fill;
  f.browser.fill = async (...args) => { assert.equal(f.checkpoint().status, "action_in_progress"); assert.equal(f.authority.state.actions.at(-1).outcome, "begun"); return original(...args); };
  const result = await fill(f, review);
  assert.equal(result.status, "filled"); assert.equal(f.writes.length, fields.length);
  assert.equal(f.authority.begun.size, fields.length);
  assert.match(result.message, /has not submitted/);
  assert.deepEqual(f.authority.state.requests.filter((entry) => /upload|submit/.test(entry.operation)), []);
});
test("no explicit approval means zero writes", async () => { const f = await fixture(); const r = await prepare(f); await assert.rejects(f.engine.approveAndFill(r.command.jti, r.review_digest, false), /Review the exact/); assert.equal(f.writes.length, 0); });
test("changed review digest means zero writes", async () => { const f = await fixture(); const r = await prepare(f); await assert.rejects(f.engine.approveAndFill(r.command.jti, "wrong", true), /Review the exact/); assert.equal(f.writes.length, 0); });

const mutations = {
  "other owner": (c) => { c.user_id = "other_owner"; }, "other device": (c) => { c.device_id = "other_device"; },
  "other device key": (c) => { c.device_key_sha256 = "c".repeat(64); }, "foreign origin": (c) => { c.target.origin = "https://employer.example"; },
  "foreign tenant permit": (c) => { c.permit.tenant_id = "other_tenant"; }, "absent tenant permit": (c) => { c.permit = null; },
  "expired tenant permit": (c) => { c.permit.expires_at = Date.now() - 1; }, "missing approval": (c) => { c.approval.id = null; },
  "stale approval": (c) => { c.approval.expires_at = Date.now() - 1; }, "submit action": (c) => { c.allowed_actions.push("submit"); },
  "missing answer": (c) => { delete c.package.answers.email; }, "inferred consent": (c) => { delete c.package.consents.privacy_consent; },
  "unsupported upload": (c) => { c.form.fields[0].type = "file"; }, "other frame": (c) => { c.target.frame_id = 1; },
};
for (const [name, mutate] of Object.entries(mutations)) test(`fail closed ${name}`, async () => {
  const f = await fixture(); f.authority.state.mutateCommand = mutate;
  await assert.rejects(prepare(f)); assert.equal(f.writes.length, 0);
});
test("forged signature is rejected before page inspection", async () => {
  const f = await fixture(); const original = f.transport.command; f.transport.command = async (...args) => { const e = await original(...args); e.payload.tenant_id = "forged"; return e; };
  await assert.rejects(prepare(f), /signature/); assert.equal(f.writes.length, 0);
});
test("wrong exact file hash fails before disclosure", async () => { const f = await fixture(); f.authority.state.bad_artifact = true; await assert.rejects(prepare(f), /Exact file/); assert.equal(f.writes.length, 0); });
test("signed but internally inconsistent package digest fails before disclosure", async () => {
  const f = await fixture(); f.authority.state.mutateCommand = (command) => { command.package.answers.full_name = "Another approved-looking name"; };
  await assert.rejects(prepare(f), /Sealed package digest/); assert.equal(f.writes.length, 0);
});
for (const [name, mutation] of Object.entries({
  owner: (p) => { p.user_id = "another_owner"; }, device: (p) => { p.device_id = "another_device"; },
  tenant: (p) => { p.tenant_id = "another_tenant"; }, artifact: (p) => { p.artifact_sha256 = "a".repeat(64); },
  approval: (p) => { p.approval_revision += 1; }, actions: (p) => { p.allowed_actions = ["submit"]; },
  field: (p) => { p.field_id = "another_field"; }, epoch: (p) => { p.execution_epoch = "another_epoch"; },
})) test(`fresh signed action permit ${name} mismatch cannot authorize disclosure`, async () => {
  const f = await fixture(); const r = await prepare(f); const original = f.transport.authorize;
  f.transport.authorize = async (...args) => {
    const envelope = await original(...args); mutation(envelope.payload);
    return sign(envelope.payload, f.authority.pair.privateKey, f.authority.configuration.keyId);
  };
  await assert.rejects(fill(f, r)); assert.equal(f.authority.begun.size, 0); assert.equal(f.writes.length, 0);
});
test("expired single-use begin proof remains unknown without DOM mutation", async () => {
  const f = await fixture(); const r = await prepare(f); const original = f.transport.begin;
  f.transport.begin = async (...args) => {
    const envelope = await original(...args); envelope.payload.expires_at = Date.now() - 1;
    return sign(envelope.payload, f.authority.pair.privateKey, f.authority.configuration.keyId);
  };
  await assert.rejects(fill(f, r), /Expired/); assert.equal(f.checkpoint().status, "unknown"); assert.equal(f.writes.length, 0);
});
test("signed begin proof exceeding the two-second action window cannot authorize a write", async () => {
  const f = await fixture(); const r = await prepare(f); const original = f.transport.begin;
  f.transport.begin = async (...args) => {
    const envelope = await original(...args); envelope.payload.expires_at = envelope.payload.issued_at + 10_000;
    return sign(envelope.payload, f.authority.pair.privateKey, f.authority.configuration.keyId);
  };
  await assert.rejects(fill(f, r), /Expired or invalid/); assert.equal(f.checkpoint().status, "unknown"); assert.equal(f.writes.length, 0);
});
for (const checkpoint of ["login", "captcha", "mfa", "assessment"]) test(`human checkpoint ${checkpoint} pauses without mutation`, async () => { const f = await fixture(); f.page.checkpoint = checkpoint; await assert.rejects(prepare(f), /Human step/); assert.equal(f.writes.length, 0); });
for (const change of ["tenant", "document", "form", "owner", "permission"]) test(`page ${change} race pauses before first write`, async () => {
  const f = await fixture(); const r = await prepare(f);
  if (change === "tenant") f.page.tenant_id = "other";
  if (change === "document") f.page.document_id = "new";
  if (change === "form") f.page.fields.push({ id: "new", label: "New required answer", type: "text", required: true, options: [] });
  if (change === "owner") f.page.user_id = "other";
  if (change === "permission") f.page.permission = false;
  await assert.rejects(fill(f, r)); assert.equal(f.writes.length, 0);
});
for (const change of ["epoch", "approval", "permit", "owner", "device", "package"]) test(`fresh online ${change} revocation overrides a valid cached command`, async () => {
  const f = await fixture(); const r = await prepare(f);
  if (change === "epoch") f.authority.state.epoch = "new_epoch";
  if (change === "approval") f.authority.state.approval_revision += 1;
  if (change === "permit") f.authority.state.permit = false;
  if (change === "owner") f.authority.state.user_id = "other_owner";
  if (change === "device") f.authority.state.device_active = false;
  if (change === "package") f.authority.state.package_changed = true;
  await assert.rejects(fill(f, r)); assert.equal(f.writes.length, 0);
});
test("offline cached command cannot authorize filling", async () => { const f = await fixture(); const r = await prepare(f); f.authority.state.offline = true; await assert.rejects(fill(f, r), /unavailable/); assert.equal(f.writes.length, 0); });
test("single-use begin cannot be replayed", async () => {
  const f = await fixture(); const r = await prepare(f); await fill(f, r);
  const permit = [...f.authority.permits.values()][0];
  await assert.rejects(f.transport.begin(permit, f.engine.claim), /already consumed/);
  await assert.rejects(fill(f, r), /Review the exact/); assert.equal(f.writes.length, fields.length);
});
test("cancel wins while authorize is pending and prevents first disclosure", async () => {
  const f = await fixture(); const r = await prepare(f); f.authority.state.beforeAuthorize = async () => { await f.engine.cancel(); };
  await assert.rejects(fill(f, r)); assert.equal(f.writes.length, 0);
});
test("conditional questions after one autosave pause subsequent writes", async () => {
  const f = await fixture(); const r = await prepare(f); const original = f.browser.fill;
  f.browser.fill = async (...args) => { const result = await original(...args); f.page.fields.push({ id: "conditional", label: "New question", type: "text", required: true, options: [] }); return result; };
  await assert.rejects(fill(f, r), /form changed/); assert.equal(f.writes.length, 1);
});
test("lost begin response persists unknown and restart cannot replay", async () => {
  const f = await fixture(); const r = await prepare(f); f.authority.state.fail_begin_response = true;
  await assert.rejects(fill(f, r), /response lost/); assert.equal(f.checkpoint().status, "unknown"); assert.equal(f.writes.length, 0);
  const restarted = new Companion({ configuration: f.authority.configuration, identity: f.identity, transport: f.transport, browser: f.browser, store: f.store });
  await restarted.enroll("local-owner-grant"); await assert.rejects(restarted.prepare("app_fixture"), /Previous fill/);
  assert.equal((await restarted.cancel()).status, "unknown");
  assert.equal(f.checkpoint().status, "unknown");
  await assert.rejects(restarted.prepare("app_fixture"), /Previous fill/);
});
test("lost completion after autosave remains unknown with no repeated write", async () => {
  const f = await fixture(); const r = await prepare(f); f.authority.state.fail_complete_response = true;
  await assert.rejects(fill(f, r), /response lost/); assert.equal(f.checkpoint().status, "unknown"); assert.equal(f.writes.length, 1);
  await assert.rejects(prepare(f), /Previous fill/); assert.equal(f.writes.length, 1);
});
test("duplicate approval while filling does not run a second loop", async () => {
  const f = await fixture(); const r = await prepare(f); let release;
  f.authority.state.beforeAuthorize = () => new Promise((resolve) => { release = resolve; });
  const pending = fill(f, r); while (!release) await new Promise((resolve) => setTimeout(resolve, 0));
  await assert.rejects(fill(f, r), /already running/); f.authority.state.beforeAuthorize = null; release(); await pending;
  assert.equal(f.writes.length, fields.length);
});
for (const size of [250_000, 5_000_000]) test(`exact artifact base64 round-trip at ${size} bytes`, async () => {
  const original = crypto.getRandomValues(new Uint8Array(60_000)); const bytes = new Uint8Array(size); for (let i = 0; i < size; i += original.length) bytes.set(original.subarray(0, Math.min(original.length, size - i)), i);
  const decoded = decode64(base64url(bytes)); assert.equal(await digest(decoded), await digest(bytes)); assert.equal(decoded.length, size);
});
test("shipping disabled configuration refuses enrollment", async () => { const f = await fixture(); f.engine.configuration = { mode: "disabled" }; await assert.rejects(f.engine.enroll("local-owner-grant"), /disabled/); assert.equal(f.writes.length, 0); });


test("fixture envelopes capture one clock read and preserve strict verifier lifetimes", async () => {
  let clock = 1_700_000_000_000; let calls = 0;
  const authority = await fixtureAuthority(undefined, { now: () => { calls += 1; return clock++; } });
  for (const [type, life] of [["action_begun", 2_000], ["approval", 10_000], ["action_permit", 10_000], ["command", 120_000], ["device_claim", 300_000]]) {
    const issuedAt = clock; const before = calls;
    const signed = type === "approval" ? await authority.envelope({}, type) : await authority.envelope({}, type, life);
    assert.equal(calls, before + 1);
    assert.equal(signed.payload.issued_at, issuedAt);
    assert.equal(signed.payload.expires_at, issuedAt + life);
    await verify(signed, authority.configuration, type, issuedAt);
    const tooLong = await sign({ ...signed.payload, expires_at: issuedAt + life + 1 }, authority.pair.privateKey, authority.configuration.keyId);
    await assert.rejects(verify(tooLong, authority.configuration, type, issuedAt), /Expired or invalid authority command/);
  }
});
