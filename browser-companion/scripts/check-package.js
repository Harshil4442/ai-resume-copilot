import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createHash, createPublicKey } from "node:crypto";
import { CONFIG } from "../extension/config.js";
const manifest = JSON.parse(await readFile(new URL("../extension/manifest.json", import.meta.url)));
assert.equal(manifest.manifest_version, 3);
assert.equal(manifest.version, "0.8.1");
assert.deepEqual(manifest.host_permissions, []);
assert.deepEqual(manifest.optional_host_permissions, ["https://www.hirewizhq.com/*", "https://job-boards.greenhouse.io/*", "https://boards.greenhouse.io/*", "https://*.amazonaws.com/*"]);
assert.deepEqual(manifest.permissions.sort(), ["scripting", "storage"]);
assert.deepEqual(manifest.optional_permissions,["webRequest"]);
assert.equal(manifest.minimum_chrome_version,"144");
for (const key of ["externally_connectable", "web_accessible_resources", "content_scripts"]) assert.equal(manifest[key], undefined);
assert.equal(CONFIG.mode, "reviewed_assistance");
assert.equal(CONFIG.websiteOrigin, "https://www.hirewizhq.com");
assert.equal(CONFIG.portalOrigin, "https://job-boards.greenhouse.io");
assert.deepEqual(Object.keys(CONFIG.nativePairing).sort(), ["authorityKey", "executorRevision"]);
assert.equal(CONFIG.nativePairing.executorRevision, "companion_0_8_1");
assert.deepEqual(Object.keys(CONFIG.nativePairing.authorityKey).sort(), ["crv", "kty", "x", "y"]);
assert.equal(CONFIG.nativePairing.authorityKey.kty, "EC");
assert.equal(CONFIG.nativePairing.authorityKey.crv, "P-256");
for (const name of ["x", "y"]) assert.match(CONFIG.nativePairing.authorityKey[name], /^[A-Za-z0-9_-]{43}$/);
assert.equal(createPublicKey({ key: CONFIG.nativePairing.authorityKey, format: "jwk" }).asymmetricKeyType, "ec");
const identity = [...createHash("sha256").update(Buffer.from(manifest.key, "base64")).digest().subarray(0, 16)]
  .flatMap((byte) => [byte >> 4, byte & 15]).map((nibble) => String.fromCharCode(97 + nibble)).join("");
assert.equal(identity, CONFIG.extensionId);
assert.equal(manifest.content_security_policy.extension_pages,
  "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-src blob:; connect-src 'self' https://www.hirewizhq.com https://boards.greenhouse.io https://*.amazonaws.com");
console.log("Reviewed MV3: no pre-granted hosts; optional native Greenhouse network observation and AWS request envelope, with runtime grants limited to the exact reviewed S3 origin. No cookies, content scripts, debugger or external messages.");
