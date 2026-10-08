import { canonical, digest, sign } from "./protocol.js";

export async function deviceIdentity() {
  const database = await new Promise((resolve, reject) => {
    const request = indexedDB.open("hirewiz-companion-device", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("keys");
    request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
  });
  const stored = await new Promise((resolve, reject) => {
    const request = database.transaction("keys").objectStore("keys").get("device");
    request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
  });
  let identity = stored;
  if (!identity) {
    const pair = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, false, ["sign", "verify"]);
    const publicKey = await crypto.subtle.exportKey("jwk", pair.publicKey);
    const key_sha256 = await digest(canonical({ kty: publicKey.kty, crv: publicKey.crv, x: publicKey.x, y: publicKey.y }));
    identity = { device_id: crypto.randomUUID(), key_sha256, public_key: publicKey, private_key: pair.privateKey };
    await new Promise((resolve, reject) => {
      const transaction = database.transaction("keys", "readwrite");
      transaction.objectStore("keys").put(identity, "device");
      transaction.oncomplete = resolve; transaction.onerror = () => reject(transaction.error);
    });
  }
  database.close();
  return { ...identity, sign: (payload) => sign(payload, identity.private_key, identity.device_id) };
}
