import { canonical, digest } from "./protocol.js";

const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const HEX = /^[a-f0-9]{64}$/;
const PHASES = new Set(["idle", "pending", "active", "draft", "approved", "running", "filled", "unknown", "cancelled", "expired", "revoked"]);
const KEYS = ["version", "device_id", "phase", "review_id", "application_id", "filled_count"].sort().join(",");

export class AssistanceRuntime {
  constructor({ transport, browser, store, now = () => Date.now() }) {
    Object.assign(this, { transport, browser, store, now });
    this.state = null; this.inspection = null; this.review = null; this.busy = false; this.cancelled = false;
  }
  async initialise() {
    const saved = await this.store.read();
    this.state = saved ?? { version: 1, device_id: this.transport.identity.device_id, phase: "idle", review_id: null, application_id: null, filled_count: 0 };
    if (!this.valid(this.state)) throw new Error("Stored assistance state needs inspection. No field action is enabled.");
    if (this.state.phase === "running") await this.save({ phase: "unknown" });
    return this;
  }
  valid(value) {
    return value && Object.keys(value).sort().join(",") === KEYS && value.version === 1
      && value.device_id === this.transport.identity.device_id && PHASES.has(value.phase)
      && (value.review_id === null || UUID.test(value.review_id))
      && (value.application_id === null || typeof value.application_id === "string" && /^[A-Za-z0-9_-]{1,80}$/.test(value.application_id))
      && Number.isSafeInteger(value.filled_count) && value.filled_count >= 0 && value.filled_count <= 12;
  }
  async save(changes) {
    const next = { ...this.state, ...changes };
    if (!this.valid(next)) throw new Error("Assistance checkpoint is invalid.");
    await this.store.write(next); this.state = next;
  }
  status(message = "") {
    return { ...this.state, busy: this.busy, key_sha256: this.transport.identity.key_sha256,
      review_url: this.state.review_id ? `${this.transport.configuration.websiteOrigin}/browser-companion?review=${this.state.review_id}`
        : `${this.transport.configuration.websiteOrigin}/browser-companion?device=${this.state.device_id}`,
      can_fill: !this.busy && !this.cancelled && this.state.phase === "approved" && Boolean(this.inspection && this.review), message };
  }
  async start() {
    if (this.busy || this.state.review_id) throw new Error("Cancel or inspect the current review before connecting again.");
    const device = await this.transport.device("prepare");
    await this.save({ phase: device.state });
    return this.status("Review this browser fingerprint on HireWiz, then check the connection.");
  }
  async check() {
    if (this.busy) return this.status("A field action is running. Cancel stops future fields.");
    const device = await this.transport.device("status", this.state.review_id);
    if (device.state !== "active") {
      this.inspection = this.review = null;
      await this.save({ phase: device.state }); return this.status();
    }
    if (device.review) {
      await this.acceptReview(device.review);
      const phase = this.state.phase === "unknown" && ["running", "approved"].includes(device.review.state) ? "unknown" : device.review.state;
      await this.save({ phase, filled_count: device.review.filled_count });
    } else await this.save({ phase: "active" });
    return this.status(this.inspection ? "Current review checked. Filling requires approval on HireWiz." : "Current state checked. After a browser restart, cancel this review and inspect again before filling.");
  }
  async acceptReview(review) {
    if (!review || !UUID.test(review.id) || !PHASES.has(review.state) || !HEX.test(review.review_digest)
      || !Number.isSafeInteger(review.filled_count) || review.filled_count < 0 || review.filled_count > 12
      || !Number.isSafeInteger(review.expires_at_ms)) throw new Error("The current review could not be verified.");
    if (this.state.review_id && review.id !== this.state.review_id) throw new Error("A different review was returned.");
    const payload = review.payload;
    if (payload && Object.keys(payload).length) {
      const version = payload.review_protocol_version ?? 1;
      if (![1, 2].includes(version) || !Number.isSafeInteger(version) || payload.fields?.length > (version === 1 ? 7 : 12)) throw new Error("The reviewed control version changed.");
      if (await digest(canonical(payload)) !== review.review_digest || payload.application_id !== this.state.application_id
        || canonical(payload.allowed_actions) !== '["fill"]' || payload.fee_credits !== 0
        || !HEX.test(payload.package_digest) || !HEX.test(payload.artifact?.sha256) || !HEX.test(payload.context_digest)
        || !Array.isArray(payload.fields) || payload.fields.length < 1 || payload.fields.length > 12
        || review.filled_count > payload.fields.length) throw new Error("The exact reviewed package changed.");
      if (this.inspection && (payload.context_digest !== this.inspection.context_digest
        || payload.document_id !== this.inspection.document_id || payload.frame_id !== 0 || payload.target_url !== this.inspection.url
        || canonical(payload.field_mapping) !== canonical(this.inspection.field_mapping))) throw new Error("The inspected browser document changed.");
    } else if (!["filled", "unknown", "cancelled", "expired"].includes(review.state)) throw new Error("The review payload is unavailable.");
    this.review = review;
  }
  async prepare(applicationId) {
    if (this.busy || this.state.review_id || !["active", "filled", "cancelled", "expired"].includes(this.state.phase)
      || typeof applicationId !== "string" || !/^[A-Za-z0-9_-]{1,80}$/.test(applicationId)) throw new Error("Check the connected browser and clear the earlier review first.");
    this.busy = true; this.cancelled = false;
    try {
      const device = await this.transport.device();
      if (device.state !== "active") throw new Error("Approve this browser on HireWiz before inspecting an application.");
      const app = await this.transport.request("application", { application_id: applicationId, review_protocol_version: 2 });
      if (app.application_id !== applicationId || !Array.isArray(app.field_mapping)
        || !/^https:\/\/job-boards\.greenhouse\.io\/razorpaysoftwareprivatelimited\/jobs\/[0-9]+$/.test(app.target_url)) throw new Error("Only the selected current Razorpay portal is supported.");
      const version = app.review_protocol_version ?? 1;
      if (![1, 2].includes(version) || !Number.isSafeInteger(version)) throw new Error("The application control version is unavailable.");
      const inspection = await this.browser.inspect(app.target_url, app.field_mapping, null, version);
      if (inspection.outcome !== "inspected" || inspection.url !== app.target_url || inspection.frame_id !== 0
        || !HEX.test(inspection.context_digest) || await digest(canonical(inspection.context)) !== inspection.context_digest) throw new Error("The exact current form could not be inspected.");
      if (this.cancelled) throw new Error("Inspection cancelled before review.");
      this.inspection = inspection;
      await this.save({ phase: "active", application_id: applicationId, filled_count: 0 });
      const minimal = { outcome: "inspected", origin: inspection.origin, url: inspection.url, frame_id: 0,
        document_id: inspection.document_id, context_digest: inspection.context_digest, assisted_values: inspection.assisted_values,
        manual_fields: inspection.manual_fields.map(({ id, label, required }) => ({ id, label, required })),
        ...(version === 2 ? { review_protocol_version: 2 } : {}) };
      const review = await this.transport.request("inspect", { application_id: applicationId, inspection: minimal });
      await this.acceptReview(review);
      await this.save({ review_id: review.id, phase: review.state, filled_count: review.filled_count });
      return { ...this.status("Review the exact resume, answers, destination and disclosures on HireWiz before filling."), busy: false };
    } finally { this.busy = false; }
  }
  expectedValues(index) {
    const values = { ...this.inspection.assisted_values };
    this.review.payload.fields.forEach((field, position) => { values[field.id] = position < index ? field.value : field.expected_before; });
    return values;
  }
  async fill() {
    if (this.busy || this.cancelled || this.state.phase === "unknown" || !this.inspection || !this.review || !this.state.review_id) throw new Error("A freshly inspected, approved review is required. Uncertain or cancelled actions cannot be resumed.");
    await this.check();
    if (!this.status().can_fill || this.review.expires_at_ms <= this.now()) throw new Error("Approve this exact review on HireWiz before filling.");
    this.busy = true; this.cancelled = false;
    try {
      while (this.state.filled_count < this.review.payload.fields.length) {
        if (this.cancelled) break;
        const index = this.state.filled_count, payload = this.review.payload, field = payload.fields[index];
        const expected = { ...this.inspection, review_digest: this.review.review_digest, reviewed_answers: payload.reviewed_answers,
          reviewed_answers_sha256: payload.reviewed_answers_sha256, current_assisted_values: this.expectedValues(index) };
        const current = await this.browser.inspect(payload.target_url, payload.field_mapping, this.inspection.document_id, payload.review_protocol_version ?? 1);
        if (current.context_digest !== payload.context_digest || current.document_id !== payload.document_id
          || canonical(current.assisted_values) !== canonical(expected.current_assisted_values)) throw new Error("The reviewed form or an answer changed. Cancel and inspect it again.");
        if (this.cancelled) break;
        // Persist intent before the one-shot lease request. A lost acknowledgement
        // or service-worker restart must never rerun a field event.
        await this.save({ phase: "running" });
        const begun = await this.transport.request("begin", { review_id: this.review.id, review_digest: this.review.review_digest,
          index, context_digest: payload.context_digest, document_id: payload.document_id });
        if (begun.state !== "begun") throw new Error("The earlier field outcome needs manual inspection.");
        const lease = await this.transport.verifyLease(begun.lease);
        const bindings = { review_id: this.review.id, application_id: payload.application_id, package_digest: payload.package_digest,
          artifact_sha256: payload.artifact.sha256, target_url: payload.target_url, context_digest: payload.context_digest,
          document_id: payload.document_id, frame_id: 0, review_digest: this.review.review_digest,
          reviewed_answers_sha256: payload.reviewed_answers_sha256, field_id: field.id, value: field.value, expected_before: field.expected_before };
        if (Object.entries(bindings).some(([name, value]) => lease[name] !== value)) throw new Error("The exact field authorization changed.");
        if (payload.review_protocol_version === 2) {
          bindings.review_protocol_version = 2;
          bindings.control_type = field.type;
          if (field.type === "combobox") bindings.option_label = field.value_label;
          if (Object.entries(bindings).some(([name, value]) => lease[name] !== value)) throw new Error("The exact typed control authorization changed.");
        }
        const result = this.cancelled ? { outcome: "blocked" } : await this.browser.fill(payload.target_url, payload.field_mapping, expected, lease);
        const outcome = result.outcome === "filled_only" && result.field_id === field.id ? "filled" : result.outcome === "blocked" ? "blocked" : "unknown";
        const receipt = await this.transport.request("outcome", { review_id: this.review.id, action_id: lease.action_id, outcome });
        await this.acceptReview(receipt);
        await this.save({ phase: receipt.state, filled_count: receipt.filled_count });
        if (outcome !== "filled") break;
      }
      return { ...this.status(this.cancelled ? "Further text filling stopped. An already authorized field may have finished. Check the portal and clear this review before another inspection."
        : this.state.phase === "unknown" ? "Filling stopped because a field outcome is uncertain. Check the company portal and clear this review before another inspection. No automatic retry occurred and no application fee was charged."
        : this.state.phase !== "filled" ? "Filling stopped before all approved answers were filled. Check the company portal and clear this review before another inspection. No application fee was charged."
        : this.review.payload.review_protocol_version === 2 ? "Approved answers filled. Complete the remaining Country/employment or unsupported controls, attach your reviewed file and submit on the company portal yourself. No application fee was charged."
        : "Approved text filling finished. Complete the remaining fields, attach your reviewed file and submit on the company portal yourself. No application fee was charged."), busy: false };
    } catch (failure) {
      if (this.state.phase === "running") await this.save({ phase: "unknown" });
      throw failure;
    } finally { this.busy = false; }
  }
  async cancel() {
    this.cancelled = true;
    // Do not race a cancel POST against an in-flight outcome. The executing loop
    // observes this flag before the next lease; an issued short lease can finish.
    if (this.busy) return this.status("Stopping future fields. An already authorized field may finish within its short lease; check the portal.");
    if (this.state.review_id) await this.transport.request("cancel", { review_id: this.state.review_id });
    this.inspection = this.review = null;
    await this.save({ phase: "active", review_id: null, application_id: null, filled_count: 0 });
    return this.status("Review cleared. Inspect the company portal before creating another review.");
  }
}
