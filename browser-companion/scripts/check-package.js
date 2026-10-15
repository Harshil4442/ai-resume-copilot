import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { CONFIG } from "../extension/config.js";
const manifest = JSON.parse(await readFile(new URL("../extension/manifest.json", import.meta.url)));
assert.equal(manifest.manifest_version, 3);
assert.deepEqual(manifest.host_permissions, []);
assert.deepEqual(manifest.optional_host_permissions, ["https://www.hirewizhq.com/*", "https://job-boards.greenhouse.io/*"]);
assert.deepEqual(manifest.permissions.sort(), ["scripting", "storage"]);
for (const key of ["externally_connectable", "web_accessible_resources", "content_scripts"]) assert.equal(manifest[key], undefined);
assert.equal(CONFIG.mode, "reviewed_assistance");
assert.equal(CONFIG.websiteOrigin, "https://www.hirewizhq.com");
assert.equal(CONFIG.portalOrigin, "https://job-boards.greenhouse.io");
const identity = [...createHash("sha256").update(Buffer.from(manifest.key, "base64")).digest().subarray(0, 16)]
  .flatMap((byte) => [byte >> 4, byte & 15]).map((nibble) => String.fromCharCode(97 + nibble)).join("");
assert.equal(identity, CONFIG.extensionId);
assert.equal(manifest.content_security_policy.extension_pages,
  "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-src blob:; connect-src 'self' https://www.hirewizhq.com");
console.log("Reviewed MV3 pilot: stable identity, two optional exact origins; no cookies, broad host grants, content scripts, external messages, upload or submission.");
