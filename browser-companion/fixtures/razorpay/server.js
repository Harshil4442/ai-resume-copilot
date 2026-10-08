import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fixtureHtml } from "./form.js";
import { FIXTURE_PATH } from "./schema.js";

// Isolated ephemeral loopback recorder. No authority, credential or upstream client.
export async function startRazorpayFixture() {
  const events = []; const rejected = []; const failures = { email: false };
  const server = createServer(async (request, response) => {
    response.setHeader("Cache-Control", "no-store");
    response.setHeader("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; object-src 'none'");
    const url = new URL(request.url, "http://127.0.0.1");
    const send = (status, value) => { response.writeHead(status, { "Content-Type": "application/json" }); response.end(JSON.stringify(value)); };
    try {
      if (request.method === "GET" && url.pathname === FIXTURE_PATH) { response.setHeader("Content-Type", "text/html; charset=utf-8"); response.end(fixtureHtml()); return; }
      if (request.method === "GET" && url.pathname === "/fixture/razorpay-like/adapter.js") { response.setHeader("Content-Type", "text/javascript"); response.end(await readFile(new URL("./adapter.js", import.meta.url))); return; }
      if (request.method === "GET" && url.pathname === "/fixture/razorpay-like/schema.js") { response.setHeader("Content-Type", "text/javascript"); response.end(await readFile(new URL("./schema.js", import.meta.url))); return; }
      if (request.method === "GET" && url.pathname === "/fixture/razorpay-like/email-validator") { events.push({ kind: "email_validation", address: url.searchParams.get("address") }); send(failures.email ? 503 : 200, { synthetic_only: true }); return; }
      if (request.method === "POST" && ["/fixture/razorpay-like/manual-file", "/fixture/razorpay-like/manual-submit"].includes(url.pathname)) {
        const chunks = []; let size = 0;
        for await (const chunk of request) { size += chunk.length; if (size > 100_000) { send(413, { error: "Synthetic recorder bound exceeded" }); return; } chunks.push(chunk); }
        events.push({ kind: url.pathname.endsWith("manual-file") ? "manual_file" : "manual_submit", bytes: size });
        send(200, { synthetic_only: true, employer_receipt: null }); return;
      }
      rejected.push({ method: request.method, path: url.pathname }); send(404, { error: "Unsupported local fixture operation" });
    } catch { send(500, { error: "Synthetic fixture failed" }); }
  });
  await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
  return { origin: `http://127.0.0.1:${server.address().port}`, events, rejected, failures,
    close: () => new Promise((resolve) => { server.close(resolve); server.closeAllConnections(); }) };
}
