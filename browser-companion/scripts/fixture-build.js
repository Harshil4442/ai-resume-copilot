import { cp, readFile, writeFile, mkdir } from "node:fs/promises";
import { assertFixtureConfiguration } from "../extension/protocol.js";

export async function fixtureBuild(destination, configuration) {
  assertFixtureConfiguration(configuration);
  await mkdir(destination, { recursive: true });
  await cp(new URL("../extension/", import.meta.url), destination, { recursive: true });
  const manifest = JSON.parse(await readFile(`${destination}/manifest.json`, "utf8"));
  manifest.name = "HireWiz Companion — LOCAL SYNTHETIC FIXTURES ONLY";
  // Controlled test profile grants exactly this ephemeral localhost origin.
  // This is not the disabled shipping manifest or a real employer enrollment.
  manifest.host_permissions = [...new Set([`${configuration.authorityOrigin}/*`, `${configuration.portalOrigin}/*`])];
  manifest.optional_host_permissions = manifest.host_permissions;
  manifest.content_security_policy.extension_pages += ` ${configuration.authorityOrigin}`;
  await writeFile(`${destination}/manifest.json`, JSON.stringify(manifest, null, 2));
  await writeFile(`${destination}/config.js`, `export const CONFIG = Object.freeze(${JSON.stringify(configuration)});\n`);
}
