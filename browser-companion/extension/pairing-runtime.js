// Identity connection lifecycle only. No application, file or field authority.
import { canonical, digest } from "./protocol.js";

const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const HEX = /^[a-f0-9]{64}$/;
const PHASES = new Set(["idle", "preparing", "awaiting_candidate", "completing", "paired", "refreshing", "unknown", "paused"]);
const PENDING = new Set(["preparing", "completing", "refreshing"]);
const KEYS = ["version", "phase", "binding", "pairing_id", "device_id", "key_sha256", "request_sha256", "expires_at_ms", "review_url", "local_operation_id", "native_operation_id", "updated_at_ms", "resume_phase"].sort().join(",");
const nullable = (value, pattern) => value === null || typeof value === "string" && pattern.test(value);

export class PairingRuntime {
  constructor({ transport, store, checkPermission, now = () => Date.now() }) {
    Object.assign(this, { transport, store, checkPermission, now });
    this.state = null; this.busy = false; this.abort = null; this.cancelled = false;
    this.saved = Promise.resolve(); this.generation = 0;
  }
  async initialise() {
    const config = this.transport.configuration;
    this.binding = await digest(canonical({ website_origin: config.websiteOrigin, extension_id: config.extensionId,
      executor_revision: config.executorRevision, authority_key: config.authorityKey }));
    const stored = await this.store.read();
    if (stored === null) {
      this.state = { version: 1, phase: "idle", binding: this.binding, pairing_id: null, device_id: null,
        key_sha256: null, request_sha256: null, expires_at_ms: null, review_url: null,
        local_operation_id: null, native_operation_id: null, updated_at_ms: this.now(), resume_phase: null };
    } else {
      if (!this.valid(stored)) throw new Error("Stored connection state needs inspection; starting again is blocked");
      if (stored.key_sha256 !== null && stored.key_sha256 !== await this.transport.keyDigest()) throw new Error("The saved connection belongs to a different retained device key");
      this.state = stored;
      if (PENDING.has(stored.phase)) await this.save({ phase: "unknown", resume_phase: null });
      this.transport.pairingId = stored.pairing_id; this.transport.deviceId = stored.device_id;
    }
    return this;
  }
  valid(value) {
    if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).sort().join(",") !== KEYS
        || value.version !== 1 || !PHASES.has(value.phase) || value.binding !== this.binding
        || !Number.isSafeInteger(value.updated_at_ms) || value.updated_at_ms <= 0
        || !nullable(value.pairing_id, UUID) || !nullable(value.device_id, UUID)
        || !nullable(value.local_operation_id, UUID) || !nullable(value.native_operation_id, UUID)
        || !nullable(value.key_sha256, HEX) || !nullable(value.request_sha256, HEX)
        || !(value.expires_at_ms === null || Number.isSafeInteger(value.expires_at_ms) && value.expires_at_ms > 0)
        || !(value.resume_phase === null || ["awaiting_candidate", "paired"].includes(value.resume_phase))) return false;
    if (value.review_url !== null && (value.pairing_id === null
        || value.review_url !== `${this.transport.configuration.websiteOrigin}/browser-companion?pairing=${value.pairing_id}`)) return false;
    if (["awaiting_candidate", "completing", "paired", "refreshing", "paused"].includes(value.phase)
        && [value.pairing_id, value.device_id, value.key_sha256, value.request_sha256, value.expires_at_ms, value.review_url].some((v) => v === null)) return false;
    if ((value.pairing_id === null) !== (value.device_id === null)) return false;
    if (["idle", "preparing"].includes(value.phase)
        && [value.pairing_id, value.device_id, value.key_sha256, value.request_sha256, value.expires_at_ms, value.review_url, value.native_operation_id].some((v) => v !== null)) return false;
    return (value.phase === "paused") === (value.resume_phase !== null) && JSON.stringify(value).length <= 2048;
  }
  async save(changes) {
    const generation = this.generation;
    const commit = async () => {
      const next = { ...this.state, ...changes, updated_at_ms: this.now() };
      if (!this.valid(next)) throw new Error("Connection checkpoint is invalid");
      await this.store.write(next);
      if (generation === this.generation) this.state = next;
    };
    const saved = this.saved.then(commit);
    this.saved = saved.catch(() => undefined);
    await saved;
  }
  status(message = "") {
    const state = this.state;
    const current = this.transport.claim?.payload;
    return { phase: state.phase, busy: this.busy, connected: state.phase === "paired" && Boolean(current && current.expires_at_ms > this.now()),
      pairing_id: state.pairing_id, device_id: state.device_id, key_sha256: state.key_sha256,
      request_sha256: state.request_sha256, expires_at_ms: state.expires_at_ms, review_url: state.review_url,
      resume_phase: state.resume_phase, message, allowed_actions: [] };
  }
  async mutate(phase, operation, success) {
    if (this.busy) throw new Error("A connection request is already running");
    this.busy = true; this.cancelled = false; this.abort = new AbortController();
    this.transport.signal = this.abort.signal;
    try {
      await this.checkPermission();
      if (this.cancelled) throw new Error("Connection paused before the request");
      // Persist intent before a request can reach any native mutation boundary.
      await this.save({ phase, local_operation_id: crypto.randomUUID(), native_operation_id: null, resume_phase: null });
      if (this.cancelled) throw new Error("Connection paused before the request");
      const result = await operation();
      if (this.cancelled) throw new Error("Connection paused during a request");
      await this.save(success(result));
      if (this.cancelled) throw new Error("Connection paused during checkpoint acknowledgement");
      return { ...this.status(), busy: false };
    } catch (failure) {
      this.transport.claim = null;
      // A lost response, interruption or cancel cannot prove no native write occurred.
      if (PENDING.has(this.state.phase) || this.state.phase === "unknown") {
        await this.save({ phase: "unknown", native_operation_id: nullable(failure?.operationId, UUID) ? failure.operationId ?? null : null, resume_phase: null });
      }
      throw new Error(this.state.phase === "unknown"
        ? "Connection outcome needs inspection. Retrying or starting another connection is blocked."
        : "Connection is unavailable. No application action was authorized.");
    } finally {
      this.busy = false; this.abort = null; this.transport.signal = null;
    }
  }
  async start() {
    if (this.state.phase !== "idle") throw new Error("This connection must be reviewed; starting another request is blocked");
    return this.mutate("preparing", () => this.transport.prepare(), (review) => ({ phase: "awaiting_candidate", ...review, review_url: review.review_url }));
  }
  async complete() {
    if (this.busy || this.state.phase !== "awaiting_candidate") throw new Error("A reviewed pending connection is required");
    await this.checkPermission();
    const retained = await this.transport.lookup("status", this.state.pairing_id);
    if (retained.pairing_id !== this.state.pairing_id || retained.device_id !== this.state.device_id) throw new Error("Current connection status does not match this device");
    if (retained.status === "REQUESTED") return this.status("Review this browser on HireWiz before completing the connection.");
    if (this.busy || this.state.phase !== "awaiting_candidate") throw new Error("This connection was paused or changed during inspection");
    if (retained.status !== "CANDIDATE_CONFIRMED") throw new Error("The current connection requires inspection; automatic recovery is disabled");
    return this.mutate("completing", () => this.transport.identify(), () => ({ phase: "paired" }));
  }
  async refresh() {
    if (this.state.phase !== "paired") throw new Error("A known paired connection is required before refresh");
    return this.mutate("refreshing", () => this.transport.identify(true), () => ({ phase: "paired" }));
  }
  async inspectUnknown() {
    if (this.busy || this.state.phase !== "unknown" || this.state.pairing_id === null) throw new Error("There is no known connection to inspect");
    await this.checkPermission();
    const retained = await this.transport.lookup("status", this.state.pairing_id);
    if (retained.pairing_id !== this.state.pairing_id || retained.device_id !== this.state.device_id) throw new Error("Current connection status does not match this device");
    // Status is evidence for inspection, never permission to replay an uncertain POST.
    return { ...this.status("Inspection does not enable a retry of the previous request."), observed_status: retained.status };
  }
  async cancel() {
    this.cancelled = true; this.abort?.abort(); this.transport.claim = null;
    this.generation += 1;
    if (this.busy || PENDING.has(this.state.phase) || this.state.phase === "unknown") {
      this.state = { ...this.state, phase: "unknown", resume_phase: null };
      await this.save({ phase: "unknown", resume_phase: null });
      return this.status("Further requests paused. The previous outcome still needs inspection.");
    }
    if (["awaiting_candidate", "paired"].includes(this.state.phase)) {
      this.state = { ...this.state, phase: "paused", resume_phase: this.state.phase };
      await this.save({ phase: "paused", resume_phase: this.state.resume_phase });
    }
    return this.status("Paused locally. This does not revoke a device already connected on HireWiz.");
  }
  async resume() {
    if (this.busy || this.state.phase !== "paused") throw new Error("Only a known locally paused connection can resume");
    await this.save({ phase: this.state.resume_phase, resume_phase: null });
    return this.status();
  }
}
