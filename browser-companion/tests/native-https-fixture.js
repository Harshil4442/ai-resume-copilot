// Local-only ordinary certificate verification. No OS trust, securityInfo,
// debugger/CDP, certificate-error bypass, request interception or page receipt.
// Chromium profile schema + trust protobuf primary sources:
// https://chromium.googlesource.com/chromium/src/+/main/components/server_certificate_database/server_certificate_database.cc
// https://chromium.googlesource.com/chromium/src/+/main/components/server_certificate_database/server_certificate_database.proto
// https://chromium.googlesource.com/chromium/src/+/main/chrome/browser/net/server_certificate_database_service_factory.cc
import assert from "node:assert/strict";
import { spawn, execFile } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, mkdir, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import { createServer as httpServer } from "node:http";
import { createServer as httpsServer } from "node:https";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const run = promisify(execFile);
const shippingId = "pnjmegppacleipajnifjmbcopjjffklb";
const ownedDnsNames = ["job-boards.greenhouse.io", "boards.greenhouse.io", "owned-resume.s3.amazonaws.com"];
const preparation = String.raw`
import datetime, hashlib, ipaddress, json, pathlib, sqlite3, sys
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
root, profile = map(pathlib.Path, sys.argv[1:3])
dns_names = json.loads(sys.argv[3])
assert isinstance(dns_names,list) and len(dns_names)<=3 and len(set(dns_names))==len(dns_names)
assert set(dns_names)<={"job-boards.greenhouse.io","boards.greenhouse.io","owned-resume.s3.amazonaws.com"}
now = datetime.datetime.now(datetime.timezone.utc)
def key(): return rsa.generate_private_key(public_exponent=65537, key_size=2048)
def name(value): return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, value)])
def ca(label):
    private = key(); subject = name(label)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(private.public_key())
      .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(days=1))
      .not_valid_after(now+datetime.timedelta(days=2)).add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
      .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False, data_encipherment=False,
          key_agreement=False, key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
      .sign(private, hashes.SHA256()))
    return private, cert
trusted_key, trusted = ca('Owned temporary profile fixture root')
untrusted_key, untrusted = ca('Owned untrusted negative fixture root')
for label, issuer_key, issuer in [('trusted', trusted_key, trusted), ('untrusted', untrusted_key, untrusted)]:
    private = key()
    leaf = (x509.CertificateBuilder().subject_name(name('localhost')).issuer_name(issuer.subject).public_key(private.public_key())
      .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(hours=1))
      .not_valid_after(now+datetime.timedelta(days=1)).add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
      .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]+[x509.DNSName(value) for value in dns_names]), critical=False)
      .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
      .sign(issuer_key, hashes.SHA256()))
    (root/(label+'.key')).write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (root/(label+'.key')).chmod(0o600)
    (root/(label+'.pem')).write_bytes(leaf.public_bytes(serialization.Encoding.PEM)+issuer.public_bytes(serialization.Encoding.PEM))
(profile/'Default').mkdir(parents=True, exist_ok=False)
der = trusted.public_bytes(serialization.Encoding.DER)
with sqlite3.connect(profile/'Default'/'ServerCertificate') as db:
    db.execute('CREATE TABLE meta(key LONGVARCHAR NOT NULL UNIQUE PRIMARY KEY, value LONGVARCHAR)')
    db.executemany('INSERT INTO meta VALUES(?,?)', [('version','1'),('last_compatible_version','1')])
    db.execute('CREATE TABLE certificates(sha256hash_hex TEXT PRIMARY KEY,der_cert BLOB NOT NULL,trust_settings BLOB NOT NULL)')
    # CertificateMetadata.trust.CertificateTrust.trust_type = TRUSTED (3).
    db.execute('INSERT INTO certificates VALUES(?,?,?)', (hashlib.sha256(der).hexdigest(), der, bytes.fromhex('0a020803')))
`;

async function listen(server) {
  server.listen(0, "127.0.0.1"); await once(server, "listening");
  return server.address().port;
}
async function close(server) {
  server.closeAllConnections?.(); await new Promise(resolve => server.close(resolve));
}

// Call first in an empty mkdtemp directory. The caller owns teardown of the
// returned profile/certificate paths; none are OS-wide trust inputs.
export async function prepareOwnedNativeHttpsProfile(directory, { dnsNames = [] } = {}) {
  assert.ok(Array.isArray(dnsNames) && dnsNames.length <= 3 && new Set(dnsNames).size === dnsNames.length
    && dnsNames.every(value => ownedDnsNames.includes(value)), "Only the three exact owned fixture aliases are supported");
  const info = await stat(directory);
  assert.equal(resolve(directory), directory);
  assert.ok(directory.startsWith(`${resolve(tmpdir())}/hirewiz-`));
  assert.equal(info.uid, process.getuid()); assert.equal(info.mode & 0o777, 0o700);
  assert.deepEqual(await readdir(directory), []);
  const profile = `${directory}/profile`;
  await run(process.env.HIREWIZ_NATIVE_HTTPS_PYTHON || "python3", ["-I", "-B", "-c", preparation, directory, profile, JSON.stringify(dnsNames)], { timeout: 15000, maxBuffer: 8192 });
  return { profile, trusted: { key: `${directory}/trusted.key`, cert: `${directory}/trusted.pem` },
    untrusted: { key: `${directory}/untrusted.key`, cert: `${directory}/untrusted.pem` } };
}

export async function runNativeHttpsFixtureProof() {
  const executable = process.env.HIREWIZ_NATIVE_HTTPS_CHROME;
  assert.ok(executable, "Set HIREWIZ_NATIVE_HTTPS_CHROME to the owned Chrome for Testing executable");
  const directory = await mkdtemp(`${tmpdir()}/hirewiz-native-https-profile-`);
  const profile = `${directory}/profile`, extension = `${directory}/extension`;
  let browser, trusted, untrusted, collector, timer;
  const requests = [];
  try {
    await prepareOwnedNativeHttpsProfile(directory, { dnsNames: ownedDnsNames });
    const handler = label => (request, response) => {
      requests.push({ label, method: request.method, url: request.url });
      response.setHeader("Cache-Control", "no-store");
      response.setHeader("Content-Type", "application/json");
      response.end('{"owned_local_fixture":true}');
    };
    trusted = httpsServer({ key: await readFile(`${directory}/trusted.key`), cert: await readFile(`${directory}/trusted.pem`) }, handler("trusted"));
    untrusted = httpsServer({ key: await readFile(`${directory}/untrusted.key`), cert: await readFile(`${directory}/untrusted.pem`) }, handler("untrusted"));
    const trustedUrl = `https://boards.greenhouse.io:${await listen(trusted)}/trusted`;
    const untrustedUrl = `https://job-boards.greenhouse.io:${await listen(untrusted)}/untrusted`;
    let report;
    const ready = new Promise(resolve => { report = resolve; });
    collector = httpServer((request, response) => {
      if (request.method !== "POST" || request.url !== "/report") { response.writeHead(404); response.end(); return; }
      const parts = []; let size = 0;
      request.on("data", part => { size += part.length; assert.ok(size < 65536); parts.push(part); });
      request.on("end", () => { const value = JSON.parse(Buffer.concat(parts)); response.end("{}"); report(value); });
    });
    const reportUrl = `http://127.0.0.1:${await listen(collector)}/report`;
    await mkdir(extension);
    const shipping = JSON.parse(await readFile(new URL("../extension/manifest.json", import.meta.url), "utf8"));
    await writeFile(`${extension}/manifest.json`, JSON.stringify({ manifest_version: 3, name: "Owned profile native TLS fixture", version: "1.0",
      key: shipping.key, permissions: ["webRequest"], host_permissions: [...ownedDnsNames.map(name => `https://${name}/*`), "http://127.0.0.1/*"], background: { service_worker: "background.js" } }));
    await writeFile(`${extension}/background.js`, `
const targets=${JSON.stringify([trustedUrl, untrustedUrl])}, events=[];
const record=(stage,details)=>{if(targets.includes(details.url))events.push({stage,...details});};
chrome.webRequest.onBeforeRequest.addListener(details=>record('before',details),{urls:targets});
chrome.webRequest.onHeadersReceived.addListener(details=>record('headers',details),{urls:targets},['responseHeaders']);
chrome.webRequest.onCompleted.addListener(details=>record('completed',details),{urls:targets});
chrome.webRequest.onErrorOccurred.addListener(details=>record('error',details),{urls:targets});
let started=false;
async function proof(){if(started)return;started=true;const results=[];
 for(const url of targets){try{const response=await fetch(url,{credentials:'omit',cache:'no-store',redirect:'error'});await response.arrayBuffer();results.push({url,status:response.status});}catch{results.push({url,error:true});}}
 await new Promise(resolve=>setTimeout(resolve,100));
 await fetch(${JSON.stringify(reportUrl)},{method:'POST',credentials:'omit',body:JSON.stringify({extension_id:chrome.runtime.id,user_agent:navigator.userAgent,events,results})});}
chrome.runtime.onInstalled.addListener(()=>void proof());chrome.runtime.onStartup.addListener(()=>void proof());setTimeout(()=>void proof(),100);
`);
    const args = ["--headless=new", `--user-data-dir=${profile}`, `--disable-extensions-except=${extension}`, `--load-extension=${extension}`,
      "--no-first-run", "--no-default-browser-check", "--disable-background-networking", "--disable-component-update", "--disable-sync", "--disable-default-apps", "--metrics-recording-only", "--no-proxy-server", `--host-resolver-rules=${ownedDnsNames.map(name => `MAP ${name} 127.0.0.1`).join(", ")}`, "about:blank"];
    assert.ok(args.every(value => !/ignore.*cert|allow-insecure|remote-debugging|enable-features|disable-web-security|ssl-key-log|test-type/i.test(value)));
    browser = spawn(executable, args, { stdio: "ignore" });
    const result = await Promise.race([ready, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("Owned Chrome native TLS report timeout")), 25000); })]);
    assert.equal(result.extension_id, shippingId);
    assert.equal(result.results[0].status, 200); assert.equal(result.results[1].error, true);
    const good = result.events.filter(event => event.url === trustedUrl), bad = result.events.filter(event => event.url === untrustedUrl);
    const headers = good.find(event => event.stage === "headers"), completed = good.find(event => event.stage === "completed");
    assert.ok(headers); assert.equal(headers.statusCode, 200); assert.ok(completed); assert.equal(completed.statusCode, 200);
    assert.equal(completed.fromCache, false); assert.equal(completed.ip, "127.0.0.1");
    assert.equal(completed.requestId, headers.requestId); assert.equal(good.filter(event => event.stage === "completed").length, 1);
    assert.equal(good.some(event => event.stage === "error"), false);
    assert.equal(bad.some(event => ["headers", "completed"].includes(event.stage)), false);
    assert.equal(bad.find(event => event.stage === "error")?.error, "net::ERR_CERT_AUTHORITY_INVALID");
    assert.deepEqual(requests, [{ label: "trusted", method: "GET", url: "/trusted" }]);
    return { status: "PASS", user_agent: result.user_agent, extension_id: result.extension_id,
      ordinary_profile_trust_only: true, os_trust_changed: false, certificate_error_bypass: false, debugger_or_cdp: false,
      trusted: { status: completed.statusCode, ip: completed.ip, from_cache: completed.fromCache, request_id: completed.requestId },
      untrusted: { error: bad.find(event => event.stage === "error").error, completed: false }, provider_calls: 0 };
  } finally {
    clearTimeout(timer);
    if (browser && browser.exitCode === null) { browser.kill("SIGTERM"); await Promise.race([once(browser, "exit"), new Promise(resolve => setTimeout(resolve, 2000))]); if (browser.exitCode === null) { browser.kill("SIGKILL"); await once(browser, "exit"); } }
    for (const server of [trusted, untrusted, collector]) if (server?.listening) await close(server);
    await rm(directory, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { console.log(JSON.stringify(await runNativeHttpsFixtureProof())); }
  catch (error) { console.error(error instanceof Error ? error.message : "Owned local TLS fixture failed"); process.exitCode = 1; }
}
