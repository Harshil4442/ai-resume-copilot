import { describe, expect, it } from "vitest";
import { catalogTimingHeaders } from "./catalogTiming";

describe("catalog timing allowlist", () => {
  it("rebuilds finite known numeric durations without copying descriptions or injected data", () => {
    const headers = catalogTimingHeaders(new Headers({
      "server-timing": "auth_lookup;dur=3.5, catalog_sources;dur=4;desc=PRIVATE_SQL, PRIVATE_TOKEN;dur=1, db_connect;dur=9, backend_headers;dur=7.2",
      "x-correlation-id": "PRIVATE_USER_ID_TOKEN",
    }), 2, 12);
    expect(headers.get("server-timing")).toBe("auth_lookup;dur=3.5, backend_headers;dur=7.2, bff_session;dur=2.0, bff_backend_headers;dur=12.0");
    expect(headers.has("x-correlation-id")).toBe(false);
    expect(JSON.stringify(Array.from(headers))).not.toContain("PRIVATE");
  });

  it.each(["NaN", "Infinity", "-1", "3600001", "1e2", "1;foo=PRIVATE_URL", "1\r\nPRIVATE_COOKIE", "99999999999999999999"])("rejects invalid duration %s", (value) => {
    // Stub the getter because native Headers appropriately rejects CRLF first.
    const upstream = { get: (name: string) => name === "server-timing" ? `db_acquire;dur=${value}` : null } as Headers;
    expect(catalogTimingHeaders(upstream, Number.NaN, Number.POSITIVE_INFINITY).has("server-timing")).toBe(false);
  });

  it("drops ambiguous duplicate phases and only allows a bounded opaque backend ID", () => {
    const result = catalogTimingHeaders(new Headers({ "server-timing": "auth_lookup;dur=1,auth_lookup;dur=2", "x-correlation-id": "a".repeat(32) }), 1, null);
    expect(result.get("server-timing")).toBe("bff_session;dur=1.0");
    expect(result.get("x-correlation-id")).toBe("a".repeat(32));
    expect(catalogTimingHeaders(new Headers({ "server-timing": "a".repeat(2049) }), -1, null).has("server-timing")).toBe(false);
  });
});
