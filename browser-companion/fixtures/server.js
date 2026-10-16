import { createServer } from "node:http";
import { canonical, decode64, digest, base64url } from "../extension/protocol.js";
import { fixtureAuthority } from "./authority.js";

const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Local synthetic employer form</title></head><body>
<main data-hirewiz-fixture="v1" data-user="owner_fixture" data-tenant="tenant_fixture" data-employer="synthetic_employer" data-opening="synthetic_employer:opening_fixture" data-form-version="fixture-form-v1"><h1>Local synthetic employer application</h1><p>Fixture only. No real employer receives these answers.</p>
<form><label for="full_name">Full name</label><input id="full_name" required><label for="email">Email address</label><input id="email" type="email" required><label for="work_authorization">Work authorization</label><select id="work_authorization" required><option value="">Choose</option><option value="yes">Yes</option><option value="no">No</option></select><label for="privacy_consent">Employer privacy consent</label><input id="privacy_consent" type="checkbox" required><button id="submit" type="submit">Submit application manually</button></form></main>
<script>window.fixtureEvents={input:0,change:0,submit:0};document.querySelector('form').addEventListener('submit',event=>{event.preventDefault();window.fixtureEvents.submit++});for(const type of ['input','change'])document.querySelector('form').addEventListener(type,event=>{window.fixtureEvents[type]++;fetch('/fixture/autosave',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({field:event.target.id})})});</script></body></html>`;

export async function startFixtureServer() {
  const authority = await fixtureAuthority();
  const nonces = new Map(); const credentials = [];
  const server = createServer(async (request, response) => {
    response.setHeader("Access-Control-Allow-Origin", "*");
    response.setHeader("Access-Control-Allow-Headers", "content-type");
    response.setHeader("Cache-Control", "no-store");
    if (request.method === "OPTIONS") { response.writeHead(204); response.end(); return; }
    if (request.url === "/fixture/apply" && request.method === "GET") { response.setHeader("Content-Type", "text/html"); response.end(html); return; }
    try {
      const chunks = []; let size = 0;
      for await (const chunk of request) { size += chunk.length; if (size > 1_000_000) throw new Error("Fixture request too large"); chunks.push(chunk); }
      const body = JSON.parse(Buffer.concat(chunks).toString() || "{}");
      let value;
      if (request.url === "/fixture/autosave") value = { autosave_observed: true };
      else if (request.url === "/fixture/challenge") {
        credentials.push(Boolean(request.headers.cookie));
        const nonce = crypto.randomUUID(); nonces.set(nonce, { device_id: body.device_id, expires_at: Date.now() + 10_000 }); value = { nonce };
      } else if (request.url === "/fixture/device-action") {
        credentials.push(Boolean(request.headers.cookie));
        const proof = body.request; const challenge = nonces.get(proof?.nonce);
        nonces.delete(proof?.nonce); // single use before asynchronous verification
        if (!challenge || challenge.device_id !== proof.device_id || challenge.expires_at <= Date.now() || Math.abs(Date.now() - proof.issued_at) > 10_000) throw new Error("Expired or replayed device challenge");
        let identity = authority.devices.get(proof.device_id);
        if (proof.operation === "enroll") {
          const publicKey = proof.body.public_key;
          if (!publicKey || publicKey.d) throw new Error("Device enrollment requires a public key only");
          const fingerprint = await digest(canonical({ kty: publicKey.kty, crv: publicKey.crv, x: publicKey.x, y: publicKey.y }));
          if (fingerprint !== proof.body.key_sha256) throw new Error("Device public key fingerprint mismatch");
          identity = { device_id: proof.device_id, key_sha256: fingerprint, public_key: publicKey };
        }
        if (!identity) throw new Error("Device claim is missing");
        const publicKey = await crypto.subtle.importKey("jwk", identity.public_key, { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
        if (!await crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, publicKey, decode64(body.signature), new TextEncoder().encode(canonical(proof)))) throw new Error("Device possession signature is invalid");
        if (proof.operation === "command" && proof.body.application_id !== "app_fixture") throw new Error("Application does not belong to this synthetic owner");
        value = await authority.operate(proof.operation, proof.body, identity, body.claim);
        if (proof.operation === "artifact") value = { bytes: base64url(value) };
      } else throw new Error("Unsupported local fixture endpoint");
      response.setHeader("Content-Type", "application/json"); response.end(JSON.stringify(value));
    } catch (error) { response.writeHead(403, { "Content-Type": "application/json" }); response.end(JSON.stringify({ error: error.message })); }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  authority.configuration.portalOrigin = origin; authority.configuration.authorityOrigin = origin;
  return { authority, origin, credentials, close: () => new Promise((resolve) => server.close(resolve)) };
}
