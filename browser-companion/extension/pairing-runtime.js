// Identity connection lifecycle only. No application, file or field authority.
import { canonical, digest } from "./protocol.js";

const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const HEX = /^[a-f0-9]{64}$/;
const PHASES = new Set(["idle", "preparing", "awaiting_candidate", "completing", "paired", "refreshing", "unknown", "unknown_legacy", "paused"]);
const PENDING = new Set(["preparing", "completing", "refreshing"]);
const UNKNOWN = new Set(["unknown", "unknown_legacy"]);
const PUBLIC_FIELDS = ["pairing_id", "device_id", "key_sha256", "request_sha256", "expires_at_ms", "review_url"];
const LEGACY_KEYS = ["version", "phase", "binding", ...PUBLIC_FIELDS, "local_operation_id", "native_operation_id", "updated_at_ms", "resume_phase"];
const RECORD_KEYS = [...LEGACY_KEYS, "send_kind"].sort().join(",");
const RECONCILED_KEYS = [...LEGACY_KEYS, "send_kind", "reconciliation"].sort().join(",");
const KEYS = [...LEGACY_KEYS, "send_kind", "history"].sort().join(",");
const MAX_HISTORY_RECORDS = 64;
const MAX_CHECKPOINT_BYTES = 256 * 1024;
const nullable = (value, pattern) => value === null || typeof value === "string" && pattern.test(value);
const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const TIMING_KEYS = "elapsed_ms,http_status,issued_age_ms,issued_at_ms,version";
const timingNumber = (value, minimum, maximum) => value === null
  || Number.isSafeInteger(value) && value >= minimum && value <= maximum;

export function pairingReadTimingSupport(value) {
  try {
    if (!object(value) || Object.keys(value).sort().join(",") !== TIMING_KEYS || value.version !== 1
        || !timingNumber(value.issued_at_ms, 1, Number.MAX_SAFE_INTEGER)
        || !timingNumber(value.issued_age_ms, -86400000, 86400000)
        || !timingNumber(value.elapsed_ms, 0, 86400000)
        || !timingNumber(value.http_status, 100, 599)) return null;
    const milliseconds = (number) => number === null ? "unavailable" : `${number} ms`;
    return `Support code: HW-COMPANION-READ-TIMING. Signed issued_at_ms: ${value.issued_at_ms ?? "unavailable"}. `
      + `Client age at send: ${milliseconds(value.issued_age_ms)}. `
      + `Request elapsed: ${milliseconds(value.elapsed_ms)}. HTTP status: ${value.http_status ?? "unavailable"}. `
      + "Client timing does not establish server freshness. This diagnostic clears on the next read or worker restart.";
  } catch { return null; }
}

export class PairingRuntime {
  constructor({ transport, store, checkPermission, now = () => Date.now() }) {
    Object.assign(this, { transport, store, checkPermission, now });
    this.state = null; this.busy = false; this.abort = null; this.cancelled = false;
    this.saved = Promise.resolve(); this.generation = 0; this.inspection = null;
  }
  empty(history = []) {
    return { version: 2, phase: "idle", binding: this.binding, pairing_id: null, device_id: null,
      key_sha256: null, request_sha256: null, expires_at_ms: null, review_url: null,
      local_operation_id: null, native_operation_id: null, updated_at_ms: this.now(), resume_phase: null,
      send_kind: null, history };
  }
  async initialise() {
    const config = this.transport.configuration;
    const binding = (executor_revision) => digest(canonical({ website_origin: config.websiteOrigin, extension_id: config.extensionId,
      executor_revision, authority_key: config.authorityKey }));
    this.binding = await binding(config.executorRevision);
    this.legacyBinding = await binding("companion_0_8_0");
    const stored = await this.store.read();
    if (stored === null) this.state = this.empty();
    else {
      if (object(stored) && stored.version === 1 && config.executorRevision === "companion_0_8_1" && this.validLegacy(stored)) {
        // Preserve the entire old checkpoint and retained IDB key. This grants
        // no retry: only current signed key inspection can enable an explicit archive.
        this.state = this.empty([structuredClone(stored)]);
        await this.save({ phase: "unknown_legacy", send_kind: "legacy", key_sha256: await this.transport.keyDigest(),
          local_operation_id: stored.local_operation_id, native_operation_id: stored.native_operation_id });
      } else {
        if (!this.valid(stored)) throw new Error("Stored connection state needs inspection; starting again is blocked");
        this.state = stored;
      }
      if (this.state.key_sha256 !== null && this.state.key_sha256 !== await this.transport.keyDigest()) throw new Error("The saved connection belongs to a different retained device key");
      if (PENDING.has(this.state.phase)) await this.save({ phase: "unknown", resume_phase: null });
      this.transport.pairingId = this.state.pairing_id; this.transport.deviceId = this.state.device_id;
    }
    return this;
  }
  validLegacy(value) {
    return object(value) && Object.keys(value).sort().join(",") === [...LEGACY_KEYS].sort().join(",")
      && value.version === 1 && value.phase === "unknown" && value.binding === this.legacyBinding
      && Number.isSafeInteger(value.updated_at_ms) && value.updated_at_ms > 0
      && nullable(value.local_operation_id, UUID) && value.native_operation_id === null && value.resume_phase === null
      && PUBLIC_FIELDS.every((key) => value[key] === null) && JSON.stringify(value).length <= 2048;
  }
  validRecord(value) {
    if (!object(value) || Object.keys(value).sort().join(",") !== (Object.hasOwn(value, "reconciliation") ? RECONCILED_KEYS : RECORD_KEYS)
        || value.version !== 2 || !PHASES.has(value.phase) || value.binding !== this.binding
        || !Number.isSafeInteger(value.updated_at_ms) || value.updated_at_ms <= 0
        || !nullable(value.pairing_id, UUID) || !nullable(value.device_id, UUID)
        || !nullable(value.local_operation_id, UUID) || !nullable(value.native_operation_id, UUID)
        || !nullable(value.key_sha256, HEX) || !nullable(value.request_sha256, HEX)
        || !(value.expires_at_ms === null || Number.isSafeInteger(value.expires_at_ms) && value.expires_at_ms > 0)
        || !(value.resume_phase === null || ["awaiting_candidate", "paired"].includes(value.resume_phase))
        || ![null, "prepare", "create", "complete", "refresh", "legacy"].includes(value.send_kind)) return false;
    if (value.review_url !== null && (value.pairing_id === null
        || value.review_url !== `${this.transport.configuration.websiteOrigin}/browser-companion?pairing=${value.pairing_id}`)) return false;
    const prepared = PUBLIC_FIELDS.every((key) => value[key] !== null);
    if ((value.pairing_id === null) !== (value.device_id === null)) return false;
    if (["awaiting_candidate", "completing", "paired", "refreshing", "paused"].includes(value.phase) && !prepared) return false;
    if (value.send_kind === "create" && !prepared || ["complete", "refresh"].includes(value.send_kind) && !prepared) return false;
    if (value.phase === "idle" && (value.send_kind !== null || [...PUBLIC_FIELDS, "local_operation_id", "native_operation_id"].some((key) => value[key] !== null))) return false;
    if (value.phase === "preparing" && !["prepare", "create"].includes(value.send_kind)) return false;
    if (value.phase === "awaiting_candidate" && value.send_kind !== "create") return false;
    if (value.phase === "completing" && value.send_kind !== "complete" || value.phase === "refreshing" && value.send_kind !== "refresh") return false;
    if (value.phase === "paired" && !["complete", "refresh"].includes(value.send_kind)) return false;
    if (value.phase === "unknown" && !["prepare", "create", "complete", "refresh"].includes(value.send_kind)) return false;
    if (value.phase === "unknown_legacy" && (value.send_kind !== "legacy" || value.key_sha256 === null
        || PUBLIC_FIELDS.filter((key) => key !== "key_sha256").some((key) => value[key] !== null))) return false;
    if (value.send_kind === "legacy" && value.phase !== "unknown_legacy") return false;
    if (value.send_kind === "prepare" && !prepared && PUBLIC_FIELDS.some((key) => value[key] !== null)) return false;
    return (value.phase === "paused") === (value.resume_phase !== null)
      && (!Object.hasOwn(value, "reconciliation") || this.validExpiredReconciliation(value.reconciliation, value))
      && JSON.stringify(value).length <= 2048;
  }
  validExpiredReconciliation(proof, record) {
    const keys = ["kind", "inspected_at_ms", "rechecked_at_ms", "status", "key_status", "pairing_id", "device_id",
      "key_sha256", "request_sha256", "binding", "expires_at_ms", "extension_id", "executor_revision"].sort().join(",");
    return object(proof) && Object.keys(proof).sort().join(",") === keys
      && proof.kind === "expired_creation_unclaimed" && proof.status === "EXPIRED" && proof.key_status === "UNCLAIMED"
      && record.send_kind === "create" && (UNKNOWN.has(record.phase) || this.pendingCreation(record))
      && ["pairing_id", "device_id", "key_sha256", "request_sha256", "binding", "expires_at_ms"].every((key) => proof[key] === record[key])
      && proof.extension_id === this.transport.configuration.extensionId && proof.executor_revision === this.transport.configuration.executorRevision
      && Number.isSafeInteger(proof.inspected_at_ms) && proof.inspected_at_ms >= record.expires_at_ms
      && Number.isSafeInteger(proof.rechecked_at_ms) && proof.rechecked_at_ms >= proof.inspected_at_ms;
  }
  valid(value) {
    if (!object(value) || Object.keys(value).sort().join(",") !== KEYS || !Array.isArray(value.history) || value.history.length > MAX_HISTORY_RECORDS) return false;
    const { history, ...record } = value;
    return this.validRecord(record) && history.every((entry) => this.validLegacy(entry) || this.validRecord(entry))
      && new TextEncoder().encode(JSON.stringify(value)).byteLength <= MAX_CHECKPOINT_BYTES;
  }
  async save(changes) {
    const generation = this.generation;
    const commit = async () => {
      const next = { ...this.state, ...changes, updated_at_ms: this.now() };
      if (!this.valid(next)) throw new Error("Connection checkpoint is invalid");
      await this.store.write(next);
      if (generation === this.generation) { this.state = next; this.inspection = null; }
    };
    const saved = this.saved.then(commit);
    this.saved = saved.catch(() => undefined);
    await saved;
  }
  status(message = "") {
    const state = this.state;
    const current = this.transport.claim?.payload;
    const inspected = this.inspection?.state === state ? this.inspection : null;
    let timing;
    try { timing = this.transport.readTiming?.(); } catch { /* Timing cannot replace connection status. */ }
    return { phase: state.phase, busy: this.busy, connected: state.phase === "paired" && Boolean(current && current.expires_at_ms > this.now()),
      pairing_id: state.pairing_id, device_id: state.device_id, key_sha256: state.key_sha256,
      request_sha256: state.request_sha256, expires_at_ms: state.expires_at_ms, review_url: state.review_url,
      resume_phase: state.resume_phase, message, allowed_actions: [], observed_status: inspected?.status ?? null,
      can_inspect_connection: UNKNOWN.has(state.phase) || this.pendingCreation(state),
      can_resume_existing: inspected?.kind === "existing",
      can_archive_restart: ["unclaimed", "expired_unclaimed"].includes(inspected?.kind),
      ...(pairingReadTimingSupport(timing) === null ? {} : { transport_timing: timing }) };
  }
  pendingCreation(state = this.state) {
    return state.send_kind === "create" && (state.phase === "awaiting_candidate"
      || state.phase === "paused" && state.resume_phase === "awaiting_candidate");
  }
  async checkRetainedCreation(state) {
    const config = this.transport.configuration;
    const binding = await digest(canonical({ website_origin: config.websiteOrigin, extension_id: config.extensionId,
      executor_revision: config.executorRevision, authority_key: config.authorityKey }));
    const key = await this.transport.keyDigest();
    if (this.state !== state || state.binding !== binding || state.key_sha256 !== key) throw new Error("The retained connection or browser key changed during inspection");
  }
  async expiredCreationProof(state, retained = null) {
    await this.checkRetainedCreation(state);
    const status = retained ?? await this.transport.lookup("status", state.pairing_id);
    if (this.state !== state || status.pairing_id !== state.pairing_id || status.device_id !== state.device_id
        || status.status !== "EXPIRED" || this.now() < state.expires_at_ms) throw new Error("The exact expired connection could not be verified");
    const key = await this.transport.inspectKey();
    await this.checkRetainedCreation(state);
    if (key.protocol_version !== 2 || key.status !== "UNCLAIMED" || key.key_sha256 !== state.key_sha256
        || key.extension_id !== this.transport.configuration.extensionId
        || key.executor_revision !== this.transport.configuration.executorRevision) throw new Error("The retained browser key is claimed or its inspection is unavailable");
    return { kind: "expired_creation_unclaimed", observed_at_ms: this.now(), status: "EXPIRED", key_status: "UNCLAIMED",
      pairing_id: state.pairing_id, device_id: state.device_id, key_sha256: state.key_sha256,
      request_sha256: state.request_sha256, binding: state.binding, expires_at_ms: state.expires_at_ms,
      extension_id: key.extension_id, executor_revision: key.executor_revision };
  }
  async mutate(phase, operation, success) {
    if (this.busy) throw new Error("A connection request is already running");
    this.busy = true; this.cancelled = false; this.abort = new AbortController();
    this.transport.signal = this.abort.signal;
    try {
      await this.checkPermission();
      if (this.cancelled) throw new Error("Connection paused before the request");
      // Persist operation kind before any corresponding send, including a crash.
      await this.save({ phase, send_kind: { preparing: "prepare", completing: "complete", refreshing: "refresh" }[phase],
        local_operation_id: crypto.randomUUID(), native_operation_id: null, resume_phase: null });
      if (this.cancelled) throw new Error("Connection paused before the request");
      const result = await operation();
      if (this.cancelled) throw new Error("Connection paused during a request");
      await this.save(success(result));
      if (this.cancelled) throw new Error("Connection paused during checkpoint acknowledgement");
      return { ...this.status(), busy: false };
    } catch (failure) {
      this.transport.claim = null;
      if (PENDING.has(this.state.phase) || UNKNOWN.has(this.state.phase)) {
        await this.save({ phase: "unknown", native_operation_id: nullable(failure?.operationId, UUID) ? failure.operationId ?? null : null, resume_phase: null });
      }
      throw new Error(UNKNOWN.has(this.state.phase)
        ? "Connection outcome needs inspection. Retrying or starting another connection is blocked."
        : "Connection is unavailable. No application action was authorized.");
    } finally {
      this.busy = false; this.abort = null; this.transport.signal = null;
    }
  }
  async start() {
    if (this.state.phase !== "idle") throw new Error("This connection must be reviewed; starting another request is blocked");
    return this.mutate("preparing", () => this.transport.prepare({
      onPrepared: async (review) => {
        if (this.cancelled) throw new Error("Connection paused before creation");
        await this.save(review);
        if (this.cancelled) throw new Error("Connection paused before creation");
      },
      beforeCreate: async () => {
        if (this.cancelled) throw new Error("Connection paused before creation");
        await this.save({ send_kind: "create" });
        if (this.cancelled) throw new Error("Connection paused before creation");
      },
    }), (review) => ({ phase: "awaiting_candidate", ...review }));
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
    if (this.busy || !(UNKNOWN.has(this.state.phase) || this.pendingCreation())) throw new Error("There is no pending or uncertain connection to inspect");
    this.busy = true; this.inspection = null;
    const state = this.state;
    try {
      await this.checkPermission();
      if (["prepare", "legacy"].includes(state.send_kind)) {
        const result = await this.transport.inspectKey();
        if (this.state !== state) throw new Error("The connection changed during inspection");
        this.inspection = { state, status: result.status, kind: result.status === "UNCLAIMED" ? "unclaimed" : "blocked" };
      } else {
        if (state.send_kind === "create") await this.checkRetainedCreation(state);
        const retained = await this.transport.lookup("status", state.pairing_id);
        if (this.state !== state || retained.pairing_id !== state.pairing_id || retained.device_id !== state.device_id) throw new Error("Current connection status does not match this device");
        if (state.send_kind === "create" && retained.status === "EXPIRED" && this.now() >= state.expires_at_ms) {
          const proof = await this.expiredCreationProof(state, retained);
          this.inspection = { state, status: "EXPIRED", kind: "expired_unclaimed", proof };
        } else {
          const live = state.send_kind === "create" && ["REQUESTED", "CANDIDATE_CONFIRMED"].includes(retained.status)
            && this.now() < state.expires_at_ms;
          this.inspection = { state, status: retained.status,
            kind: live && UNKNOWN.has(state.phase) ? "existing" : live && this.pendingCreation(state) ? "pending" : "blocked" };
        }
      }
      return { ...this.status(this.inspection.kind === "unclaimed" ? "This retained browser key is unclaimed. You may explicitly archive this attempt before starting a new connection."
        : this.inspection.kind === "expired_unclaimed" ? "This exact connection expired and its browser key is unclaimed. You may explicitly archive it; starting a new connection remains a separate choice."
        : this.inspection.kind === "existing" ? "The original connection exists. Resume that exact connection without creating another."
        : this.inspection.kind === "pending" ? "This connection is still live. Review and complete this same connection; starting another stays blocked."
          : "The retained key or previous operation remains claimed or unresolved. Starting again and replaying it stay blocked."), busy: false };
    } finally { this.busy = false; }
  }
  async resumeExisting() {
    if (this.busy || this.inspection?.state !== this.state || this.inspection.kind !== "existing"
        || this.state.send_kind !== "create" || this.now() >= this.state.expires_at_ms) throw new Error("Inspect the exact pending connection before resuming it");
    const state = this.state, inspection = this.inspection;
    this.busy = true;
    try {
      await this.checkPermission();
      if (this.state !== state || this.inspection !== inspection) throw new Error("The connection changed during reconciliation");
      this.transport.pairingId = state.pairing_id; this.transport.deviceId = state.device_id;
      await this.save({ phase: "awaiting_candidate" });
      return { ...this.status("Original connection retained. Review its fingerprints on HireWiz before completing it."), busy: false };
    } finally { this.busy = false; }
  }
  async archiveUnknown() {
    const expired = this.inspection?.kind === "expired_unclaimed";
    const eligible = expired ? this.state.send_kind === "create" && (UNKNOWN.has(this.state.phase) || this.pendingCreation())
      : UNKNOWN.has(this.state.phase) && ["prepare", "legacy"].includes(this.state.send_kind) && this.inspection?.kind === "unclaimed";
    if (this.busy || this.inspection?.state !== this.state || !eligible) throw new Error("Inspect this exact unclaimed browser key before archiving the attempt");
    const state = this.state, inspection = this.inspection;
    this.busy = true;
    try {
      await this.checkPermission();
      if (this.state !== state || this.inspection !== inspection) throw new Error("The connection changed during reconciliation");
      if (state.history.length >= MAX_HISTORY_RECORDS) throw new Error("Retained connection history is full. Contact support without clearing browser storage.");
      const { history, ...record } = state;
      const archived = structuredClone(record);
      if (expired) {
        // Recheck both native facts at the archive click. An earlier inspection
        // is presentation only and cannot reset a newly claimed/changed key.
        const proof = await this.expiredCreationProof(state);
        if (this.state !== state || this.inspection !== inspection) throw new Error("The connection changed during reconciliation");
        const { observed_at_ms, ...facts } = proof;
        archived.reconciliation = { ...facts, inspected_at_ms: inspection.proof.observed_at_ms, rechecked_at_ms: observed_at_ms };
      }
      await this.save(this.empty([...history, archived]));
      this.transport.pairingId = null; this.transport.deviceId = null; this.transport.claim = null;
      return { ...this.status(expired ? "Expired connection archived with its reconciliation evidence. Starting a new connection requires your next explicit click."
        : "Previous uncertain attempt archived with its evidence. Starting a new connection requires your next explicit click."), busy: false };
    } catch (failure) {
      if (expired) this.inspection = null;
      throw failure;
    } finally { this.busy = false; }
  }
  async cancel() {
    this.cancelled = true; this.abort?.abort(); this.transport.claim = null;
    this.generation += 1;
    // No intent/checkpoint or send can precede the first permission await.
    // Keep that proven-unsent state instead of fabricating an unknown operation.
    if (this.state.phase === "idle") return this.status("Paused before any connection request was sent.");
    if (this.busy || PENDING.has(this.state.phase) || UNKNOWN.has(this.state.phase)) {
      const phase = this.state.phase === "unknown_legacy" ? "unknown_legacy" : "unknown";
      this.state = { ...this.state, phase, resume_phase: null };
      await this.save({ phase, resume_phase: null });
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
