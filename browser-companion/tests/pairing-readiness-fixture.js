// Owned local wire availability only. Signed POSTs still use the original
// cryptographic/native fixture; this response supplies no identity or claim.
export function readinessFixture({ url, method, headers, body }, website, extensionId) {
  if (method !== "GET") return null;
  const target = new URL(url);
  const origin = headers.origin;
  const valid = target.origin === website && !target.search && !target.hash
    && /^\/api\/browser-pairing\/device\/(prepare|inspect-key|status|challenge|refresh-challenge)$/.test(target.pathname)
    && (origin === undefined || origin === `chrome-extension://${extensionId}`)
    && body === null && ["cookie", "authorization", "content-type", "content-length", "content-encoding", "transfer-encoding"].every((name) => !(name in headers));
  return { status: valid ? 204 : 403, headers: { "Cache-Control": "private, no-store, max-age=0" }, body: "" };
}
