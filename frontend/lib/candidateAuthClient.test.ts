// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import { candidateRegistration } from "./candidateAuthClient";

vi.mock("./accountTransportClient", () => ({ accountMutationHeaders: async () => ({ "Content-Type": "application/json" }) }));
afterEach(() => vi.unstubAllGlobals());

it.each(["register", "registration-status"] as const)("reports a known rate refusal for %s without automatic retries", async (operation) => {
  const fetch = vi.fn(async () => Response.json({ status: "RATE_LIMITED", detail: "synthetic-private-refusal" }, { status: 429 }));
  vi.stubGlobal("fetch", fetch);
  await expect(candidateRegistration(operation, "synthetic@example.invalid", "synthetic-password"))
    .rejects.toMatchObject({ name: "CandidateRegistrationRateLimitError", message: "Too many registration requests. Please wait before trying again." });
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("keeps an unavailable registration distinct from a definite rate refusal", async () => {
  const fetch = vi.fn(async () => Response.json({ detail: "synthetic-private-refusal" }, { status: 503 }));
  vi.stubGlobal("fetch", fetch);
  await expect(candidateRegistration("register", "synthetic@example.invalid", "synthetic-password"))
    .rejects.toThrow("Check registration status before trying again.");
  expect(fetch).toHaveBeenCalledTimes(1);
});
