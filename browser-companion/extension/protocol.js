export const VERSION = 1;
const encoder = new TextEncoder();
const unsafeKeys = new Set(["__proto__", "prototype", "constructor"]);

export function canonical(value) {
  if (value === null || typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number" && Number.isFinite(value)) return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (typeof value !== "object") throw new Error("Unsupported signed value");
  return `{${Object.keys(value).sort().map((key) => {
    if (unsafeKeys.has(key)) throw new Error("Unsafe signed field");
    return `${JSON.stringify(key)}:${canonical(value[key])}`;
  }).join(",")}}`;
}
export function base64url(bytes) {
  const array = new Uint8Array(bytes);
  const chunks = [];
  for (let offset = 0; offset < array.length; offset += 32_768) chunks.push(String.fromCharCode(...array.subarray(offset, offset + 32_768)));
  return btoa(chunks.join("")).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}
export function decode64(value) {
  if (typeof value !== "string" || !/^[\w-]+$/.test(value) || value.length > 8_000_000) throw new Error("Invalid signed bytes");
  const decoded = atob(value.replaceAll("-", "+").replaceAll("_", "/"));
  return Uint8Array.from(decoded, (character) => character.charCodeAt(0));
}
export async function digest(value) {
  const bytes = typeof value === "string" ? encoder.encode(value) : value;
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
export async function sign(payload, key, keyId) {
  const bytes = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, key, encoder.encode(canonical(payload)));
  return { alg: "ES256", key_id: keyId, payload, signature: base64url(bytes) };
}
export async function verify(envelope, configuration, type, now = Date.now()) {
  if (!envelope || envelope.alg !== "ES256" || envelope.key_id !== configuration.keyId || !configuration.authorityKey) throw new Error("Untrusted authority signature");
  const key = await crypto.subtle.importKey("jwk", configuration.authorityKey, { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
  const valid = await crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, key, decode64(envelope.signature), encoder.encode(canonical(envelope.payload)));
  if (!valid) throw new Error("Untrusted authority signature");
  const payload = envelope.payload;
  const maximumLife = type === "device_claim" ? 300_000 : type === "command" ? 120_000 : type === "action_begun" ? 2_000 : 10_000;
  if (payload.version !== VERSION || payload.type !== type || !payload.jti || !Number.isFinite(payload.issued_at) || !Number.isFinite(payload.expires_at)
    || payload.issued_at > now + 2_000 || payload.expires_at <= now || payload.expires_at <= payload.issued_at || payload.expires_at - payload.issued_at > maximumLife) throw new Error("Expired or invalid authority command");
  return payload;
}
export function assertFixtureConfiguration(configuration) {
  if (configuration.mode !== "local_fixture") throw new Error("Companion is disabled. Production device authorization is not available.");
  for (const value of [configuration.authorityOrigin, configuration.portalOrigin]) {
    const url = new URL(value);
    if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || url.origin !== value) throw new Error("Only exact local fixture origins are supported");
  }
}
export async function reviewDigest(command) {
  return digest(canonical({ version: command.version, application_id: command.application_id, user_id: command.user_id, device_id: command.device_id,
    employer_key: command.employer_key, tenant_id: command.tenant_id, canonical_opening_key: command.canonical_opening_key,
    target: command.target, form: command.form, package: command.package, approval: command.approval, allowed_actions: command.allowed_actions, execution_epoch: command.execution_epoch }));
}
export function assertBindings(payload, command, identity, claim) {
  const same = ["user_id", "device_id", "tenant_id", "employer_key", "canonical_opening_key", "application_id", "package_digest", "execution_epoch", "approval_id", "approval_revision", "artifact_sha256"];
  const expected = { ...command, package_digest: command.package.digest, approval_id: command.approval.id, approval_revision: command.approval.revision, artifact_sha256: command.package.artifact.sha256 };
  if (same.some((key) => payload[key] !== expected[key]) || payload.origin !== command.target.origin || payload.command_id !== command.jti
    || payload.user_id !== claim.user_id || payload.device_id !== identity.device_id || payload.device_key_sha256 !== identity.key_sha256
    || canonical(payload.allowed_actions) !== '["fill"]') throw new Error("Action authorization does not match the reviewed owner, device or package");
}

export class Companion {
  constructor({ configuration, identity, transport, browser, store, now = () => Date.now() }) {
    Object.assign(this, { configuration, identity, transport, browser, store, now });
    this.claim = null; this.review = null; this.busy = false; this.cancelled = false;
  }
  async enroll(grant) {
    assertFixtureConfiguration(this.configuration);
    this.review = null;
    const envelope = await this.transport.enroll(grant);
    const claim = await verify(envelope, this.configuration, "device_claim", this.now());
    if (!claim.user_id || claim.device_id !== this.identity.device_id || claim.device_key_sha256 !== this.identity.key_sha256) throw new Error("Device claim belongs to another owner or device");
    this.claim = envelope;
    return { user_id: claim.user_id, device_id: claim.device_id };
  }
  async currentClaim() {
    if (!this.claim) throw new Error("Connect an authorized device before review");
    return verify(this.claim, this.configuration, "device_claim", this.now());
  }
  async prepare(applicationId) {
    assertFixtureConfiguration(this.configuration);
    if (this.busy) throw new Error("A fill is already running");
    this.review = null; this.cancelled = false;
    const checkpoint = await this.store.read();
    if (checkpoint && ["action_in_progress", "unknown"].includes(checkpoint.status)) throw new Error("Previous fill may be incomplete. Inspect the portal; automatic recovery is disabled.");
    const claim = await this.currentClaim();
    const envelope = await this.transport.command(applicationId, this.claim);
    const command = await verify(envelope, this.configuration, "command", this.now());
    this.validateCommand(command, claim);
    const { digest: package_digest, ...packet } = command.package;
    if (await digest(canonical(packet)) !== package_digest) throw new Error("Sealed package digest does not match its exact file, answers and consents");
    const artifact = await this.transport.artifact(command, this.claim);
    if (artifact.byteLength !== command.package.artifact.size_bytes || await digest(artifact) !== command.package.artifact.sha256) throw new Error("Exact file bytes do not match this package");
    const page = await this.browser.inspect(command.target);
    this.assertPage(page, command);
    const review_digest = await reviewDigest(command);
    this.review = { envelope, command, review_digest, document_id: page.document_id, artifact };
    return { command, review_digest, artifact_base64: base64url(artifact), warning: "Fill only. Input events can autosave and disclose the approved answers. Upload and final submission remain manual." };
  }
  validateCommand(command, claim) {
    const { target, package: packet, permit, approval } = command;
    if (command.user_id !== claim.user_id || command.device_id !== this.identity.device_id || command.device_key_sha256 !== this.identity.key_sha256) throw new Error("Command belongs to another owner or device");
    if (target?.origin !== this.configuration.portalOrigin || new URL(target.url).origin !== target.origin || new URL(target.url).pathname !== "/fixture/apply"
      || target.adapter_id !== "local-synthetic-v1" || target.frame_id !== 0) throw new Error("Unsupported employer origin, tenant or adapter");
    if (!permit || permit.origin !== target.origin || permit.tenant_id !== command.tenant_id || permit.employer_key !== command.employer_key || permit.expires_at <= this.now()
      || canonical(permit.allowed_actions) !== '["fill"]' || permit.access_basis !== "synthetic_operator_grant") throw new Error("Current tenant-specific permission is required");
    if (!approval?.id || !Number.isSafeInteger(approval.revision) || approval.expires_at <= this.now() || approval.digest !== packet?.digest || !command.execution_epoch) throw new Error("Current exact-package approval is required");
    if (canonical(command.allowed_actions) !== '["fill"]' || !/^[a-f0-9]{64}$/.test(packet?.artifact?.sha256 || "")
      || !/^[a-f0-9]{64}$/.test(packet.digest || "") || !Number.isSafeInteger(packet.artifact.size_bytes) || packet.artifact.size_bytes <= 0 || packet.artifact.size_bytes > 5_000_000
      || !["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"].includes(packet.artifact.media_type)) throw new Error("Only a sealed fill-only package is supported");
    if (!Array.isArray(command.form?.fields) || !command.form.fields.length || command.form.fields.length > 8) throw new Error("Unsupported form schema");
    const allowed = new Set(["full_name", "email", "cover_letter", "work_authorization", "privacy_consent", "sponsorship_details"]);
    if (command.form.fields.some((field) => !allowed.has(field.id) || !["text", "email", "textarea", "select-one", "checkbox"].includes(field.type))) throw new Error("Unsupported or upload field needs manual input");
    const values = { ...packet.answers, ...packet.consents };
    if (Object.keys(packet.answers).some((key) => key in packet.consents) || command.form.fields.some((field) => field.type === "checkbox" ? !(field.id in packet.consents) : field.id in packet.consents)) throw new Error("Answers and explicit consents must have distinct schema fields");
    if (Object.keys(values).some((key) => !command.form.fields.some((field) => field.id === key)) || command.form.fields.some((field) => !(field.id in values)
      || (field.type === "checkbox" ? typeof values[field.id] !== "boolean" : typeof values[field.id] !== "string" || values[field.id].length > 5_000)
      || (field.required && (values[field.id] === "" || values[field.id] === false))
      || (field.type === "select-one" && !field.options.some((option) => option.value === values[field.id])))) throw new Error("Complete and review every question and explicit consent first");
  }
  assertPage(page, command) {
    if (page.checkpoint) throw new Error(`Human step required: ${page.checkpoint}`);
    if (!page.permission || page.frame_id !== 0 || page.origin !== command.target.origin || page.url !== command.target.url
      || page.user_id !== command.user_id || page.tenant_id !== command.tenant_id || page.employer_key !== command.employer_key
      || page.canonical_opening_key !== command.canonical_opening_key || page.form_version !== command.form.version
      || canonical(page.fields) !== canonical(command.form.fields)) throw new Error("The current page, account, tenant or form changed; prepare a new review");
  }
  async approveAndFill(commandId, review_digest, acknowledged) {
    if (this.busy) throw new Error("A fill is already running");
    const review = this.review;
    if (!acknowledged || !review || commandId !== review.command.jti || review_digest !== review.review_digest) throw new Error("Review the exact file, answers, destination and fill-only action before approval");
    this.busy = true;
    let disclosureStarted = false;
    try {
      const command = await verify(review.envelope, this.configuration, "command", this.now());
      const claim = await this.currentClaim();
      this.validateCommand(command, claim);
      const approval = await verify(await this.transport.approve(command, review_digest, this.claim), this.configuration, "approval", this.now());
      assertBindings(approval, command, this.identity, claim);
      let filled = 0;
      for (const field of command.form.fields) {
        if (this.cancelled) throw new Error("Cancelled. Previously filled answers may already have autosaved.");
        await verify(review.envelope, this.configuration, "command", this.now());
        const page = await this.browser.inspect(command.target);
        this.assertPage(page, command);
        if (page.document_id !== review.document_id) throw new Error("The page navigated; prepare a new review");
        const permit = await verify(await this.transport.authorize(command, field.id, review_digest, this.claim), this.configuration, "action_permit", this.now());
        assertBindings(permit, command, this.identity, claim);
        if (permit.field_id !== field.id || permit.review_digest !== review_digest) throw new Error("Field authorization mismatch");
        // A lost begin response is ambiguous. Never reuse a cached may-act token.
        await this.store.write({ status: "action_in_progress", application_id: command.application_id, command_id: command.jti, action_id: permit.jti, filled_count: filled });
        disclosureStarted = true;
        const begun = await verify(await this.transport.begin(permit, this.claim), this.configuration, "action_begun", this.now());
        assertBindings(begun, command, this.identity, claim);
        if (begun.field_id !== field.id || begun.action_id !== permit.jti || begun.review_digest !== review_digest) throw new Error("Single-use begin authorization mismatch");
        if (this.cancelled) throw new Error("Cancelled. Previously filled answers may already have autosaved.");
        const result = await this.browser.fill(command.target, { command, field, value: ({ ...command.package.answers, ...command.package.consents })[field.id], document_id: review.document_id, deadline: begun.expires_at });
        if (result.outcome !== "filled") throw new Error(result.reason || "The page changed before filling");
        filled += 1;
        await this.transport.complete(begun, this.claim);
        await this.store.write({ status: "field_filled", application_id: command.application_id, command_id: command.jti, action_id: permit.jti, filled_count: filled });
        disclosureStarted = false;
      }
      await this.store.write({ status: "filled", application_id: command.application_id, command_id: command.jti, filled_count: filled });
      this.review = null;
      return { status: "filled", filled_count: filled, message: "Answers filled only. Complete any upload and final submission yourself. HireWiz has not submitted an application." };
    } catch (error) {
      await this.store.write({ status: disclosureStarted ? "unknown" : this.cancelled ? "cancelled" : "paused", application_id: review.command.application_id, command_id: review.command.jti });
      this.review = null;
      throw error;
    } finally { this.busy = false; }
  }
  async cancel() {
    this.cancelled = true;
    const command = this.review?.command;
    this.review = null;
    const checkpoint = await this.store.read();
    if (!this.busy && checkpoint && ["action_in_progress", "unknown"].includes(checkpoint.status)) {
      return { status: "unknown", message: "Further filling paused. The previous action still needs inspection; cancellation cannot erase its uncertain outcome." };
    }
    if (command) await this.transport.cancel(command, this.claim);
    if (!this.busy) await this.store.write({ status: "cancelled", application_id: command?.application_id ?? null });
    return { status: "cancelled", message: "Further filling paused. Answers already filled may remain with the portal." };
  }
}
