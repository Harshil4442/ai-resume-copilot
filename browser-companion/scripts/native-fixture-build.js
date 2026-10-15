// Test-only builder. Shipping source remains disabled with zero host grants.
import { cp, mkdir, readFile, writeFile } from "node:fs/promises";
import { createHash, generateKeyPairSync, createPublicKey } from "node:crypto";

export function nativeFixtureIdentity() {
  const { publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const manifestKey = publicKey.export({ type: "spki", format: "der" });
  return identityFromPublicKey(manifestKey.toString("base64"));
}

function identityFromPublicKey(encoded) {
  if (typeof encoded !== "string" || encoded.length > 1024 || !/^[A-Za-z0-9+/]+={0,2}$/.test(encoded)) throw new Error("Invalid local fixture manifest public key");
  const bytes = Buffer.from(encoded, "base64");
  const key = createPublicKey({ key: bytes, type: "spki", format: "der" });
  if (key.asymmetricKeyType !== "rsa" || key.asymmetricKeyDetails.modulusLength !== 2048
      || key.export({ type: "spki", format: "der" }).toString("base64") !== encoded) throw new Error("Invalid local fixture manifest public key");
  const extensionId = [...createHash("sha256").update(bytes).digest().subarray(0, 16)]
    .map((value) => String.fromCharCode(97 + (value >> 4), 97 + (value & 15))).join("");
  return { manifestKey: encoded, extensionId };
}

export async function nativeFixtureBuild(destination, { websiteOrigin, authorityKey, executorRevision, manifestKey, grantTestOrigin = false }) {
  const origin = new URL(websiteOrigin);
  if (origin.protocol !== "https:" || origin.origin !== websiteOrigin || origin.username || origin.password
      || !["127.0.0.1", "localhost", "[::1]"].includes(origin.hostname)
      || typeof executorRevision !== "string" || !/^[A-Za-z0-9_:/.-]{1,160}$/.test(executorRevision)
      || !authorityKey || authorityKey.kty !== "EC" || authorityKey.crv !== "P-256"
      || !/^[A-Za-z0-9_-]{43}$/.test(authorityKey.x) || !/^[A-Za-z0-9_-]{43}$/.test(authorityKey.y)
      || "d" in authorityKey || typeof grantTestOrigin !== "boolean") throw new Error("Only an explicitly owned HTTPS loopback native fixture is permitted");
  const identity = manifestKey === undefined ? nativeFixtureIdentity() : identityFromPublicKey(manifestKey);
  const extensionId = identity.extensionId;
  const configuration = { mode: "local_native_fixture", testScope: "owned_local_native_connection", websiteOrigin,
    authorityKey: { kty: authorityKey.kty, crv: authorityKey.crv, x: authorityKey.x, y: authorityKey.y }, extensionId, executorRevision };
  await mkdir(destination, { recursive: true });
  await cp(new URL("../extension/", import.meta.url), destination, { recursive: true });
  const manifest = JSON.parse(await readFile(`${destination}/manifest.json`, "utf8"));
  Object.assign(manifest, { name: "HireWiz — LOCAL NATIVE CONNECTION TEST ONLY", key: identity.manifestKey,
    permissions: ["storage"], host_permissions: grantTestOrigin ? [`${websiteOrigin}/*`] : [], optional_host_permissions: [`${websiteOrigin}/*`] });
  manifest.content_security_policy.extension_pages += ` ${websiteOrigin}`;
  await writeFile(`${destination}/manifest.json`, JSON.stringify(manifest, null, 2));
  await writeFile(`${destination}/config.js`, `export const CONFIG = Object.freeze(${JSON.stringify(configuration)});\n`);
  return { extensionId, configuration };
}
