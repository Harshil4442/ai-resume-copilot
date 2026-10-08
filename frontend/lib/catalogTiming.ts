const BACKEND_PHASES = new Set([
  "backend_headers", "middleware_jwt", "dependency_jwt", "db_acquire", "auth_lookup",
  "catalog_owner_lookup", "catalog_sources", "catalog_render",
]);
const MAX_DURATION_MS = 3_600_000;

function duration(value: number) {
  return Number.isFinite(value) && value >= 0 && value <= MAX_DURATION_MS ? value.toFixed(1) : null;
}

export function catalogTimingHeaders(upstream: Headers | null, sessionMs: number, backendHeadersMs: number | null) {
  const result = new Headers();
  const entries = new Map<string, string>();
  const duplicates = new Set<string>();
  const raw = upstream?.get("server-timing");
  if (raw && raw.length <= 2048 && raw.split(",").length <= 16) {
    for (const entry of raw.split(",")) {
      // Entire-entry match: descriptions, extensions, unknown names and injected text are discarded.
      const match = /^\s*([a-z_]+);dur=(\d{1,7}(?:\.\d{1,3})?)\s*$/.exec(entry);
      if (!match || !BACKEND_PHASES.has(match[1])) continue;
      const value = duration(Number(match[2]));
      if (value === null) continue;
      if (entries.has(match[1])) duplicates.add(match[1]);
      entries.set(match[1], `${match[1]};dur=${value}`);
    }
  }
  for (const name of duplicates) entries.delete(name);
  const session = duration(sessionMs);
  const backend = backendHeadersMs === null ? null : duration(backendHeadersMs);
  if (session !== null) entries.set("bff_session", `bff_session;dur=${session}`);
  if (backend !== null) entries.set("bff_backend_headers", `bff_backend_headers;dur=${backend}`);
  if (entries.size) result.set("Server-Timing", Array.from(entries.values()).join(", "));
  const id = upstream?.get("x-correlation-id");
  if (id && /^[0-9a-f]{32}$/.test(id)) result.set("X-Correlation-ID", id);
  return result;
}
