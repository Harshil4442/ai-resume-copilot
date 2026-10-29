import { canonical, digest, sign } from "./protocol.js";

const UNAVAILABLE = "Device key storage is unavailable";
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
// Error metadata is private to this module, never read from stored key material.
const failurePhases = new WeakMap();
function unavailable(phase) {
  const failure = new Error(UNAVAILABLE);
  failurePhases.set(failure, phase);
  return failure;
}
export function deviceFailurePhase(failure) { return failurePhases.get(failure) ?? null; }

async function openDatabase() {
  return new Promise((resolve, reject) => {
    let settled = false;
    let request;
    try { request = indexedDB.open("hirewiz-companion-device", 1); }
    catch { reject(unavailable("OPEN-UNAVAILABLE")); return; }
    const fail = (phase) => { if (!settled) { settled = true; clearTimeout(timer); reject(unavailable(phase)); } };
    const timer = setTimeout(() => fail("OPEN-TIMEOUT"), 3000);
    request.onupgradeneeded = () => request.result.createObjectStore("keys");
    request.onblocked = () => fail("OPEN-BLOCKED"); request.onerror = () => fail("OPEN-ERROR");
    request.onsuccess = () => {
      if (settled) { request.result.close(); return; }
      settled = true; clearTimeout(timer); resolve(request.result);
    };
  });
}

function retainedIdentity(database, candidate = null, slot = "device") {
  return new Promise((resolve, reject) => {
    let value = null, settled = false, timedOut = false, tx;
    try { tx = database.transaction("keys", candidate ? "readwrite" : "readonly"); }
    catch { reject(unavailable("TX-UNAVAILABLE")); return; }
    const fail = (phase) => { if (!settled) { settled = true; clearTimeout(timer); reject(unavailable(phase)); } };
    const timer = setTimeout(() => { timedOut = true; try { tx.abort(); } catch { /* already terminal */ } fail("TX-TIMEOUT"); }, 3000);
    tx.onabort = () => fail(timedOut ? "TX-TIMEOUT" : "TX-ABORT"); tx.onerror = () => fail("TX-ERROR");
    const store = tx.objectStore("keys"), read = store.get(slot);
    read.onsuccess = () => {
      value = read.result ?? null;
      if (value === null && candidate !== null) { value = candidate; store.add(candidate, slot); }
    };
    tx.oncomplete = () => {
      if (!settled) { settled = true; clearTimeout(timer); resolve(value); }
    };
  });
}

async function validate(identity) {
  let phase = "IDENTITY-SHAPE";
  try {
    const key = identity?.private_key, jwk = identity?.public_key;
    if (!identity || Object.keys(identity).sort().join(",") !== "device_id,key_sha256,private_key,public_key"
        || !UUID.test(identity.device_id)) throw unavailable(phase);
    phase = "KEY-TYPE";
    if (!(key instanceof CryptoKey) || key.extractable || key.type !== "private") throw unavailable(phase);
    phase = "KEY-ALGORITHM";
    if (key.algorithm.name !== "ECDSA" || key.algorithm.namedCurve !== "P-256") throw unavailable(phase);
    phase = "KEY-USAGES";
    if (key.usages.join(",") !== "sign") throw unavailable(phase);
    phase = "PUBLIC-SHAPE";
    if (!jwk || jwk.kty !== "EC" || jwk.crv !== "P-256" || "d" in jwk
        || Object.keys(jwk).some((name) => !["kty", "crv", "x", "y", "ext", "key_ops"].includes(name))
        || "ext" in jwk && typeof jwk.ext !== "boolean"
        || "key_ops" in jwk && (!Array.isArray(jwk.key_ops) || jwk.key_ops.join(",") !== "verify")
        || !/^[A-Za-z0-9_-]{43}$/.test(jwk.x) || !/^[A-Za-z0-9_-]{43}$/.test(jwk.y)
        || JSON.stringify(jwk).length > 512) throw unavailable(phase);
    phase = "FINGERPRINT";
    if (identity.key_sha256 !== await digest(canonical({ kty: jwk.kty, crv: jwk.crv, x: jwk.x, y: jwk.y }))) throw unavailable(phase);
    phase = "PUBLIC-IMPORT";
    const publicKey = await crypto.subtle.importKey("jwk", jwk, { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
    phase = "PROOF-SIGN";
    const proof = new TextEncoder().encode(`hirewiz.device-storage.v1\n${crypto.randomUUID()}`);
    const signature = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, key, proof);
    phase = "PROOF-VERIFY";
    if (!await crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, publicKey, signature, proof)) throw unavailable(phase);
    return identity;
  } catch (failure) { throw deviceFailurePhase(failure) ? failure : unavailable(phase); }
}

export async function createLocalIdentity() {
  const pair = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, false, ["sign", "verify"]);
  const public_key = await crypto.subtle.exportKey("jwk", pair.publicKey);
  const key_sha256 = await digest(canonical({ kty: public_key.kty, crv: public_key.crv, x: public_key.x, y: public_key.y }));
  return { device_id: crypto.randomUUID(), key_sha256, public_key, private_key: pair.privateKey };
}

export async function deviceIdentity({ requireRetained = false, slot = "device" } = {}) {
  if (typeof requireRetained !== "boolean" || !(slot === "device" || typeof slot === "string" && /^device-recovery:[a-f0-9-]{36}$/.test(slot) && UUID.test(slot.slice(16)))
      || slot !== "device" && !requireRetained) throw unavailable("OPTIONS");
  const database = await openDatabase();
  let phase = "TX-UNAVAILABLE";
  try {
    let identity = await retainedIdentity(database, null, slot);
    if (identity === null) {
      if (requireRetained) throw unavailable("MISSING-RETAINED");
      // Crypto awaits occur outside the IDB transaction. The second read and add
      // share one serialized readwrite transaction, so concurrent callers keep
      // the same retained owner instead of overwriting each other's private key.
      phase = "CREATE";
      identity = await retainedIdentity(database, await createLocalIdentity());
    }
    const retained = await validate(identity);
    return { ...retained, sign: (payload) => sign(payload, retained.private_key, retained.device_id) };
  } catch (failure) {
    throw deviceFailurePhase(failure) ? failure : unavailable(phase);
  } finally { database.close(); }
}
