// Explicit local recovery only. No server, page, permission or employer operations.
import { canonical, digest } from "./protocol.js";
import { createLocalIdentity } from "./device.js";

export const RECOVERY_UNAVAILABLE = "Local lost-key recovery needs inspection. No application action is enabled.";
export const LEGACY_KEYS = Object.freeze(["pairing_checkpoint", "assistance_checkpoint", "native_application_evidence"]);
const JOURNAL = "lost-key-recovery-v1", MAX_BYTES = 384 * 1024, REVIEW_MS = 5 * 60 * 1000;
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const HEX = /^[a-f0-9]{64}$/;
const fail = () => new Error(RECOVERY_UNAVAILABLE);
const same = (left, right) => canonical(left) === canonical(right);
const recordKeys = "created_at_ms,id,phase,snapshot,snapshot_digest,version";

function snapshot(value) {
  const result = Object.fromEntries(LEGACY_KEYS.map((name) => [name, value?.[name] ?? null]));
  if (new TextEncoder().encode(JSON.stringify(result)).byteLength > MAX_BYTES) throw fail();
  canonical(result); // Refuse non-JSON or unsafe keys instead of discarding data.
  return structuredClone(result);
}

function validRecord(value) {
  try {
    return value && Object.keys(value).sort().join(",") === recordKeys && value.version === 1 && UUID.test(value.id)
      && ["pending", "active", "blocked"].includes(value.phase) && HEX.test(value.snapshot_digest)
      && Number.isSafeInteger(value.created_at_ms) && value.created_at_ms > 0
      && Object.keys(value.snapshot).sort().join(",") === [...LEGACY_KEYS].sort().join(",")
      && value.snapshot.pairing_checkpoint !== null
      && same(snapshot(value.snapshot), value.snapshot);
  } catch { return false; }
}

function summary(record, changed = false) {
  const ids = [record.snapshot.pairing_checkpoint?.device_id, record.snapshot.assistance_checkpoint?.device_id]
    .filter((id) => typeof id === "string" && UUID.test(id));
  return { recovery_id: record.id, phase: changed ? "blocked" : record.phase,
    archive_bytes: new TextEncoder().encode(JSON.stringify(record.snapshot)).byteLength,
    old_device_ids: [...new Set(ids)], no_old_device_revocation_proven: true, application_actions: [] };
}

export function recoveryKey(context, name) {
  if (!LEGACY_KEYS.includes(name)) throw fail();
  if (context === null) return name;
  if (!context || !UUID.test(context.id) || context.phase !== "active") throw fail();
  return `recovery:${context.id}:${name}`;
}

// Atomic operations share the existing IDB store; no key is exported, replaced or deleted.
export class RecoveryJournal {
  async transaction(mode, operation) {
    let database;
    try {
      database = await new Promise((resolve, reject) => {
        let settled = false;
        const request = indexedDB.open("hirewiz-companion-device", 1);
        const stop = () => { if (!settled) { settled = true; clearTimeout(timer); reject(fail()); } };
        const timer = setTimeout(stop, 3000);
        request.onerror = stop; request.onblocked = stop;
        // Match ordinary first-use initialization; never replace an existing store.
        request.onupgradeneeded = () => request.result.createObjectStore("keys");
        request.onsuccess = () => {
          if (settled) { request.result.close(); return; }
          settled = true; clearTimeout(timer); resolve(request.result);
        };
      });
      return await new Promise((resolve, reject) => {
        let result, settled = false;
        const tx = database.transaction("keys", mode), store = tx.objectStore("keys");
        const stop = () => { if (!settled) { settled = true; clearTimeout(timer); reject(fail()); } };
        const timer = setTimeout(() => { try { tx.abort(); } catch { /* terminal */ } stop(); }, 3000);
        tx.onabort = stop; tx.onerror = stop;
        tx.oncomplete = () => { if (!settled) { settled = true; clearTimeout(timer); resolve(result); } };
        const refuse = () => { try { tx.abort(); } catch { /* already terminal */ } stop(); };
        const read = (key, next) => {
          const request = store.get(key);
          request.onsuccess = () => { try { next(request.result ?? null); } catch { refuse(); } };
        };
        operation({ store, read, refuse, result: (value) => { result = value; } });
      });
    } catch { throw fail(); }
    finally { database?.close(); }
  }
  read() {
    return this.transaction("readonly", ({ read, result }) => read(JOURNAL, (record) =>
      read("device", (legacy) => result({ record, legacyMissing: legacy === null }))));
  }
  reserve(record) {
    return this.transaction("readwrite", ({ store, read, result, refuse }) => read(JOURNAL, (existing) => {
      if (existing !== null) { refuse(); return; }
      read("device", (legacy) => {
        if (legacy !== null) { refuse(); return; }
        store.add(record, JOURNAL); result(record);
      });
    }));
  }
  activate(expected, identity) {
    return this.transaction("readwrite", ({ store, read, result, refuse }) => read(JOURNAL, (record) => {
      if (!same(record, expected) || record.phase !== "pending") { refuse(); return; }
      read("device", (legacy) => {
        if (legacy !== null) { refuse(); return; }
        store.add(identity, `device-recovery:${record.id}`);
        const active = { ...record, phase: "active" };
        store.put(active, JOURNAL); result(active);
      });
    }));
  }
  block(expected) {
    return this.transaction("readwrite", ({ store, read, result, refuse }) => read(JOURNAL, (record) => {
      if (!record || record.id !== expected.id || record.snapshot_digest !== expected.snapshot_digest) { refuse(); return; }
      const blocked = { ...record, phase: "blocked" }; store.put(blocked, JOURNAL); result(blocked);
    }));
  }
}

export class LostKeyRecovery {
  constructor({ readLegacy, journal = new RecoveryJournal(), createIdentity = createLocalIdentity, now = () => Date.now() }) {
    Object.assign(this, { readLegacy, journal, createIdentity, now }); this.reviewed = null;
  }
  async inspect() {
    const { record, legacyMissing } = await this.journal.read();
    if (record === null) return { phase: "not_started", application_actions: [] };
    if (!validRecord(record) || await digest(canonical(record.snapshot)) !== record.snapshot_digest) throw fail();
    const changed = !legacyMissing || await digest(canonical(snapshot(await this.readLegacy()))) !== record.snapshot_digest;
    // Inspection never resumes or creates an identity. Observed drift is latched
    // in journal metadata so restoring old bytes cannot silently unlock it.
    if (changed && record.phase !== "blocked") return summary(await this.journal.block(record));
    return summary(record);
  }
  async context() {
    const { record, legacyMissing } = await this.journal.read();
    if (record === null) return null;
    if (!validRecord(record) || record.phase !== "active" || await digest(canonical(record.snapshot)) !== record.snapshot_digest) throw fail();
    if (!legacyMissing || await digest(canonical(snapshot(await this.readLegacy()))) !== record.snapshot_digest) {
      await this.journal.block(record); throw fail();
    }
    return { id: record.id, phase: "active", slot: `device-recovery:${record.id}` };
  }
  async review() {
    this.reviewed = null;
    const { record, legacyMissing } = await this.journal.read();
    if (record !== null || !legacyMissing) throw fail();
    const saved = snapshot(await this.readLegacy());
    if (saved.pairing_checkpoint === null) throw fail();
    const value = { version: 1, id: crypto.randomUUID(), phase: "pending", created_at_ms: this.now(),
      snapshot_digest: await digest(canonical(saved)), snapshot: saved };
    this.reviewed = value;
    return { ...summary(value), snapshot_digest: value.snapshot_digest, expires_at_ms: value.created_at_ms + REVIEW_MS,
      warning: "Old connection and application outcomes remain unresolved. This one-time local recovery cannot revoke an old server device. A crash or changed state stops recovery for inspection; automatic retry and another identity are unsupported." };
  }
  async commit({ recovery_id, acknowledged }) {
    const reviewed = this.reviewed; this.reviewed = null;
    if (acknowledged !== true || !reviewed || recovery_id !== reviewed.id || this.now() > reviewed.created_at_ms + REVIEW_MS
        || this.now() < reviewed.created_at_ms) throw fail();
    const unchanged = async () => await digest(canonical(snapshot(await this.readLegacy()))) === reviewed.snapshot_digest;
    if (!await unchanged()) throw fail();
    // Atomic reservation checks key absence and makes competing/stale commits lose
    // before generation. A crash retains pending quarantine and never retries it.
    await this.journal.reserve(reviewed);
    if (!await unchanged()) { await this.journal.block(reviewed); throw fail(); }
    const identity = await this.createIdentity();
    if (!await unchanged()) { await this.journal.block(reviewed); throw fail(); }
    const active = await this.journal.activate(reviewed, identity);
    const final = await this.journal.read();
    if (!final.legacyMissing || !same(final.record, active) || !await unchanged()) { await this.journal.block(active); throw fail(); }
    return summary(active);
  }
}

// In one service worker, recovery never races any existing operation. Ordinary
// operations stay concurrent, preserving Pause/cancellation of pending requests.
export class RecoveryGate {
  constructor() { this.running = 0; this.recovering = false; }
  async run(exclusive, operation) {
    if (this.recovering || exclusive && this.running !== 0) throw fail();
    if (exclusive) this.recovering = true; else this.running++;
    try { return await operation(); }
    finally { if (exclusive) this.recovering = false; else this.running--; }
  }
}
