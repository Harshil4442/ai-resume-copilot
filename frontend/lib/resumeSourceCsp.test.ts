import { afterEach, expect, it, vi } from "vitest";

async function configuration(flag?: string) {
  vi.resetModules();
  if (flag === undefined) delete process.env.NEXT_PUBLIC_RESUME_DIRECT_UPLOAD_ENABLED;
  else vi.stubEnv("NEXT_PUBLIC_RESUME_DIRECT_UPLOAD_ENABLED", flag);
  const modulePath = "../next.config.mjs";
  const { default: config } = await import(modulePath);
  return { headers: await config.headers(), redirects: await config.redirects() };
}

afterEach(() => { vi.unstubAllEnvs(); vi.resetModules(); });

it("only literal true permits the exact storage host in connect-src", async () => {
  const disabled = await configuration("false");
  const enabled = await configuration("true");
  const disabledCsp = disabled.headers[0].headers.find((header: { key: string; value: string }) => header.key === "Content-Security-Policy")!.value;
  const enabledCsp = enabled.headers[0].headers.find((header: { key: string; value: string }) => header.key === "Content-Security-Policy")!.value;
  expect(disabledCsp).not.toContain("storage.googleapis.com");
  expect(enabledCsp.replace(" https://storage.googleapis.com", "")).toBe(disabledCsp);
  expect(enabledCsp.split("; ").find((directive: string) => directive.startsWith("connect-src "))).toContain(" https://storage.googleapis.com");
  expect(enabledCsp).not.toContain("*.googleapis.com");
  expect(enabled.headers.slice(1)).toEqual(disabled.headers.slice(1));
  expect(enabled.headers[0].headers.filter((header: { key: string }) => header.key !== "Content-Security-Policy")).toEqual(disabled.headers[0].headers.filter((header: { key: string }) => header.key !== "Content-Security-Policy"));
  expect(enabled.redirects).toEqual(disabled.redirects);
});

it.each([undefined, "FALSE", "TRUE", "1", " true", "true "])("keeps default/invalid flag %s policy byte-exact", async (flag) => {
  const disabled = await configuration("false");
  expect(await configuration(flag)).toEqual(disabled);
});
