// @vitest-environment node
import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";
import { POST } from "./route";

const site = "https://www.hirewizhq.com";
const accepted = { accepted_terms: true, confirmed_age_18: true, policy_version: "2026-10-08" };
function request(headers: Record<string, string>, body: unknown = accepted) {
  return new NextRequest("https://localhost:3000/api/auth/google-consent", { method: "POST", headers: { host: "www.hirewizhq.com", "content-type": "application/json", ...headers }, body: JSON.stringify(body) });
}
const rejected: Record<string, string>[] = [{}, { origin: "https://attacker.example" }, { origin: "null" }, { origin: site, "sec-fetch-site": "cross-site" }, { origin: "https://localhost:3000", "x-forwarded-host": "localhost:3000" }];

describe("Google registration consent origin boundary", () => {
  it("accepts a legitimate browser origin behind Next's internal hostname and sets only confirmed consent", async () => {
    const response = await POST(request({ origin: site, "sec-fetch-site": "same-origin" }));
    expect(response.status).toBe(200);
    expect(response.cookies.get("hirewiz_google_registration_consent")?.value).toBe("2026-10-08");
    expect(response.cookies.get("hirewiz_google_registration_consent")?.httpOnly).toBe(true);
    expect(response.headers.get("cache-control")).toContain("no-store");
  });
  it.each(rejected)("rejects invalid or missing browser origin %j without recording consent", async (headers) => {
    const response = await POST(request(headers));
    expect(response.status).toBe(403);
    expect(response.headers.has("set-cookie")).toBe(false);
  });
  it.each([null, { ...accepted, accepted_terms: false }, { ...accepted, confirmed_age_18: false }, { ...accepted, policy_version: "outdated" }])("does not set consent for invalid or unaccepted terms %j", async (body) => {
    const response = await POST(request({ origin: site }, body));
    expect(response.status).toBe(400);
    expect(response.headers.has("set-cookie")).toBe(false);
  });
});
