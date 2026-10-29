import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import test from "node:test";

const root = new URL("../../", import.meta.url);
const sha = (value) => createHash("sha256").update(value).digest("hex");
async function fixture(t) {
  const directory = await mkdtemp(`${tmpdir()}/hirewiz-companion-package-`);
  t.after(() => rm(directory, { recursive: true, force: true }));
  for (const path of ["extension", "scripts", "package.json", "package-lock.json"]) {
    await cp(new URL(`../${path}`, import.meta.url), `${directory}/browser-companion/${path}`, { recursive: true });
  }
  const pack = (...args) => spawnSync("python3", ["browser-companion/scripts/package-reviewed.py", ...args], { cwd: directory, encoding: "utf8" });
  const check = () => spawnSync(process.execPath, ["browser-companion/scripts/check-package.js"], { cwd: directory, encoding: "utf8" });
  return { directory, pack, check, archive: `${directory}/frontend/public/downloads/hirewiz-browser-companion-0.8.5.zip` };
}

test("official0.8.5 archive is source-exact and reproducible without altering historical downloads", async (t) => {
  const f = await fixture(t);
  assert.equal(f.check().status, 0);
  assert.equal(f.pack().status, 0);
  const first = await readFile(f.archive);
  assert.equal(f.pack("--check").status, 0);
  assert.equal(f.pack().status, 0);
  assert.equal(sha(await readFile(f.archive)), sha(first));
  assert.equal(sha(first), sha(await readFile(new URL("frontend/public/downloads/hirewiz-browser-companion-0.8.5.zip", root))));
});

test("source drift makes archive verification refuse without rewriting the delivered bytes", async (t) => {
  const f = await fixture(t); assert.equal(f.pack().status, 0);
  const before = sha(await readFile(f.archive));
  const popup = `${f.directory}/browser-companion/extension/popup.js`;
  await writeFile(popup, (await readFile(popup, "utf8")) + "\n// synthetic source drift\n");
  assert.notEqual(f.pack("--check").status, 0);
  assert.equal(sha(await readFile(f.archive)), before);
});

test("unknown source files and mismatched release metadata fail the exact package inventory", async (t) => {
  const f = await fixture(t), extra = `${f.directory}/browser-companion/extension/unknown-debug.js`;
  await writeFile(extra, "// synthetic non-release file\n");
  assert.notEqual(f.check().status, 0); assert.notEqual(f.pack().status, 0); await rm(extra);
  const manifest = `${f.directory}/browser-companion/extension/manifest.json`;
  const value = JSON.parse(await readFile(manifest, "utf8")); value.version = "0.8.4";
  await writeFile(manifest, JSON.stringify(value));
  assert.notEqual(f.check().status, 0); assert.notEqual(f.pack().status, 0);
});
