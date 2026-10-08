import { canonical, digest, reviewDigest, sign, verify } from "../extension/protocol.js";

export const fields = [
  { id: "email", label: "Email address", type: "email", required: true, options: [] },
  { id: "full_name", label: "Full name", type: "text", required: true, options: [] },
  { id: "privacy_consent", label: "Employer privacy consent", type: "checkbox", required: true, options: [] },
  { id: "work_authorization", label: "Work authorization", type: "select-one", required: true, options: [{ value: "", label: "Choose" }, { value: "yes", label: "Yes" }, { value: "no", label: "No" }] },
];
export const artifact = new TextEncoder().encode("%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n% Synthetic exact fixture bytes, not a real candidate resume.\n%%EOF");

// This in-memory synthetic authority is a test double, never production durable
// permission, enrollment, recovery or application state. No employer is contacted.
export async function fixtureAuthority(portalOrigin = "http://127.0.0.1:4407") {
  const pair = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
  const authorityKey = await crypto.subtle.exportKey("jwk", pair.publicKey);
  const configuration = { mode: "local_fixture", authorityOrigin: portalOrigin, portalOrigin, authorityKey, keyId: "ephemeral-fixture-authority" };
  const state = { epoch: "fixture-epoch-1", user_id: "owner_fixture", cancelled: false, permit: true, device_active: true, approval_revision: 1, package_changed: false,
    fail_begin_response: false, fail_complete_response: false, offline: false, actions: [], requests: [], mutateCommand: null, bad_artifact: false,
    beforeAuthorize: null, beforeBegin: null, beforeComplete: null, begin_delay_ms: 0 };
  const commands = new Map(); const approvals = new Map(); const permits = new Map(); const begun = new Set(); const completed = new Set(); const devices = new Map();
  const envelope = (payload, type, life = 10_000) => sign({ ...payload, version: 1, type, jti: crypto.randomUUID(), issued_at: Date.now(), expires_at: Date.now() + life }, pair.privateKey, configuration.keyId);
  function current(command, claim) {
    if (state.offline || !state.device_active || !state.permit || state.cancelled || claim.user_id !== state.user_id || command.user_id !== state.user_id
      || command.execution_epoch !== state.epoch || command.approval.revision !== state.approval_revision || state.package_changed) throw new Error("Current device, permission, account, epoch or approval was revoked");
  }
  function bound(command, field_id, review_digest) {
    return { command_id: command.jti, application_id: command.application_id, user_id: command.user_id, device_id: command.device_id, device_key_sha256: command.device_key_sha256, employer_key: command.employer_key, canonical_opening_key: command.canonical_opening_key,
      tenant_id: command.tenant_id, origin: command.target.origin, package_digest: command.package.digest, artifact_sha256: command.package.artifact.sha256,
      execution_epoch: command.execution_epoch, approval_id: command.approval.id, approval_revision: command.approval.revision, allowed_actions: ["fill"], field_id, review_digest };
  }
  async function enroll(identity, grant) {
    if (grant !== "local-owner-grant") throw new Error("Authorized candidate device enrollment is required");
    devices.set(identity.device_id, identity);
    return envelope({ user_id: state.user_id, device_id: identity.device_id, device_key_sha256: identity.key_sha256 }, "device_claim", 300_000);
  }
  async function command(identity, application_id, claimEnvelope) {
    const claim = await verify(claimEnvelope, configuration, "device_claim");
    if (!state.device_active || claim.user_id !== state.user_id || identity.device_id !== claim.device_id || !state.permit || state.cancelled) throw new Error("Current device and tenant permission are required");
    const packet = { answers: { email: "alice@example.test", full_name: "Alice Fixture", work_authorization: "yes" }, consents: { privacy_consent: true },
      artifact: { sha256: await digest(artifact), filename: "Synthetic-fixture.pdf", size_bytes: artifact.byteLength, media_type: "application/pdf" } };
    packet.digest = await digest(canonical(packet));
    const value = { application_id, user_id: state.user_id, device_id: identity.device_id, device_key_sha256: identity.key_sha256,
      employer_key: "synthetic_employer", tenant_id: "tenant_fixture", canonical_opening_key: "synthetic_employer:opening_fixture", execution_epoch: state.epoch, allowed_actions: ["fill"],
      target: { origin: configuration.portalOrigin, url: `${configuration.portalOrigin}/fixture/apply`, adapter_id: "local-synthetic-v1", frame_id: 0 },
      form: { version: "fixture-form-v1", fields: structuredClone(fields) }, package: packet,
      permit: { origin: configuration.portalOrigin, tenant_id: "tenant_fixture", employer_key: "synthetic_employer", access_basis: "synthetic_operator_grant", allowed_actions: ["fill"], expires_at: Date.now() + 120_000 },
      approval: { id: "approval_fixture", revision: state.approval_revision, digest: packet.digest, expires_at: Date.now() + 120_000 } };
    const signed = await envelope(value, "command", 120_000);
    // Fault injection happens before signing, exercising semantic bindings as
    // well as the independent untrusted-signature cases in the tests.
    if (state.mutateCommand) { state.mutateCommand(signed.payload); signed.signature = (await sign(signed.payload, pair.privateKey, configuration.keyId)).signature; }
    commands.set(signed.payload.jti, signed.payload);
    return signed;
  }
  async function operate(operation, body, identity, claimEnvelope) {
    state.requests.push({ operation, device_id: identity.device_id });
    if (state.offline) throw new Error("Online authority unavailable");
    if (operation === "enroll") return enroll(identity, body.grant);
    if (operation === "command") return command(identity, body.application_id, claimEnvelope);
    const claim = await verify(claimEnvelope, configuration, "device_claim");
    let selected;
    if (body.command_id) selected = commands.get(body.command_id);
    else if (body.action_id) selected = commands.get(permits.get(body.action_id)?.command_id);
    if (!selected || selected.device_id !== identity.device_id || claim.device_id !== identity.device_id) throw new Error("Unknown owner/device command");
    if (operation === "cancel") { state.cancelled = true; return { cancelled: true }; }
    if (operation === "authorize" && state.beforeAuthorize) await state.beforeAuthorize();
    if (operation === "begin" && state.beforeBegin) await state.beforeBegin();
    if (operation === "complete" && state.beforeComplete) await state.beforeComplete();
    current(selected, claim);
    if (operation === "artifact") return state.bad_artifact ? new Uint8Array([0]) : artifact;
    const expectedReview = await reviewDigest(selected);
    if (operation === "approve") {
      if (body.review_digest !== expectedReview || canonical(body.allowed_actions) !== '["fill"]') throw new Error("Exact current fill-only review is required");
      approvals.set(selected.jti, expectedReview);
      return envelope(bound(selected, null, expectedReview), "approval");
    }
    if (operation === "authorize") {
      if (approvals.get(selected.jti) !== expectedReview || body.review_digest !== expectedReview || !selected.form.fields.some((field) => field.id === body.field_id)
        || [...permits.values()].some((permit) => permit.command_id === selected.jti && permit.field_id === body.field_id)) throw new Error("Field was already authorized or approval is missing");
      const signed = await envelope(bound(selected, body.field_id, expectedReview), "action_permit");
      permits.set(signed.payload.jti, signed.payload);
      return signed;
    }
    if (operation === "begin") {
      const permit = permits.get(body.action_id);
      if (begun.has(body.action_id) || permit.expires_at <= Date.now()) throw new Error("Single-use action already consumed or expired");
      begun.add(body.action_id); // The test double serializes this before any await.
      state.actions.push({ action_id: body.action_id, field_id: permit.field_id, outcome: "begun" });
      if (state.begin_delay_ms) await new Promise((resolve) => setTimeout(resolve, state.begin_delay_ms));
      if (state.fail_begin_response) throw new Error("Begin response lost; no cached may-act proof is returned");
      return envelope({ ...permit, jti: crypto.randomUUID(), action_id: body.action_id }, "action_begun", 2_000);
    }
    if (operation === "complete") {
      if (!begun.has(body.action_id) || completed.has(body.action_id)) throw new Error("Unknown or repeated completion");
      completed.add(body.action_id);
      if (state.fail_complete_response) throw new Error("Completion response lost");
      state.actions.push({ action_id: body.action_id, field_id: permits.get(body.action_id).field_id, outcome: "completed" });
      return { completed: true };
    }
    throw new Error("Unsupported synthetic authority operation");
  }
  return { configuration, state, devices, commands, approvals, permits, begun, completed, operate, envelope, pair };
}
