import { canonical, digest, sign } from "./protocol.js";

const UNAVAILABLE = "Device key storage is unavailable";
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;

async function openDatabase() {
  return new Promise((resolve, reject) => {
    let settled = false;
    const request = indexedDB.open("hirewiz-companion-device", 1);
    const fail = () => { if (!settled) { settled = true; clearTimeout(timer); reject(new Error(UNAVAILABLE)); } };
    const timer = setTimeout(fail, 3000);
    request.onupgradeneeded = () => request.result.createObjectStore("keys");
    request.onblocked = fail; request.onerror = fail;
    request.onsuccess = () => {
      if (settled) { request.result.close(); return; }
      settled = true; clearTimeout(timer); resolve(request.result);
    };
  });
}

function retainedIdentity(database, candidate = null) {
  return new Promise((resolve, reject) => {
    let value = null, settled = false;
    const tx = database.transaction("keys", candidate ? "readwrite" : "readonly");
    const fail = () => { if (!settled) { settled = true; clearTimeout(timer); reject(new Error(UNAVAILABLE)); } };
    const timer = setTimeout(() => { try { tx.abort(); } catch { /* already terminal */ } fail(); }, 3000);
    tx.onabort = fail; tx.onerror = fail;
    const store = tx.objectStore("keys"), read = store.get("device");
    read.onsuccess = () => {
      value = read.result ?? null;
      if (value === null && candidate !== null) { value = candidate; store.add(candidate, "device"); }
    };
    tx.oncomplete = () => {
      if (!settled) { settled = true; clearTimeout(timer); resolve(value); }
    };
  });
}

async function validate(identity) {
  const key = identity?.private_key, jwk = identity?.public_key;
  if (!identity || Object.keys(identity).sort().join(",") !== "device_id,key_sha256,private_key,public_key"
      || !UUID.test(identity.device_id) || !(key instanceof CryptoKey) || key.extractable || key.type !== "private"
      || key.algorithm.name !== "ECDSA" || key.algorithm.namedCurve !== "P-256" || key.usages.join(",") !== "sign"
      || !jwk || jwk.kty !== "EC" || jwk.crv !== "P-256" || "d" in jwk
      || Object.keys(jwk).some((name) => !["kty", "crv", "x", "y", "ext", "key_ops"].includes(name))
      || "ext" in jwk && typeof jwk.ext !== "boolean"
      || "key_ops" in jwk && (!Array.isArray(jwk.key_ops) || jwk.key_ops.join(",") !== "verify")
      || !/^[A-Za-z0-9_-]{43}$/.test(jwk.x) || !/^[A-Za-z0-9_-]{43}$/.test(jwk.y)
      || JSON.stringify(jwk).length > 512) throw new Error(UNAVAILABLE);
  if (identity.key_sha256 !== await digest(canonical({ kty: jwk.kty, crv: jwk.crv, x: jwk.x, y: jwk.y }))) throw new Error(UNAVAILABLE);
  const publicKey = await crypto.subtle.importKey("jwk", jwk, { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
  const proof = new TextEncoder().encode(`hirewiz.device-storage.v1\n${crypto.randomUUID()}`);
  const signature = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, key, proof);
  if (!await crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, publicKey, signature, proof)) throw new Error(UNAVAILABLE);
  return identity;
}

export async function deviceIdentity() {
  const database = await openDatabase();
  try {
    let identity = await retainedIdentity(database);
    if (identity === null) {
      // Crypto awaits occur outside the IDB transaction. The second read and add
      // share one serialized readwrite transaction, so concurrent callers keep
      // the same retained owner instead of overwriting each other's private key.
      const pair = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, false, ["sign", "verify"]);
      const publicKey = await crypto.subtle.exportKey("jwk", pair.publicKey);
      const key_sha256 = await digest(canonical({ kty: publicKey.kty, crv: publicKey.crv, x: publicKey.x, y: publicKey.y }));
      identity = await retainedIdentity(database, { device_id: crypto.randomUUID(), key_sha256,
        public_key: publicKey, private_key: pair.privateKey });
    }
    const retained = await validate(identity);
    return { ...retained, sign: (payload) => sign(payload, retained.private_key, retained.device_id) };
  } catch {
    throw new Error(UNAVAILABLE);
  } finally { database.close(); }
}
