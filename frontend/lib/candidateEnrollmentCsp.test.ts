import { expect, it } from "vitest";

it("permits the exact official Google Identity Services resources while keeping other Google script origins closed", async () => {
  const modulePath = "../next.config.mjs";
  const { default: config } = await import(modulePath);
  const headers = await config.headers();
  const csp = headers[0].headers.find((header: {key:string;value:string}) => header.key === "Content-Security-Policy").value as string;
  const directive = (name: string) => csp.split("; ").find(value => value.startsWith(name + " "))!;
  expect(directive("script-src")).toContain(" https://accounts.google.com/gsi/client");
  expect(directive("style-src")).toContain(" https://accounts.google.com/gsi/style");
  expect(directive("connect-src")).toContain(" https://accounts.google.com/gsi/");
  expect(directive("frame-src")).toContain(" https://accounts.google.com/gsi/");
  expect(csp).not.toContain("*.google.com");
  expect(directive("script-src").replace("https://accounts.google.com/gsi/client", "")).not.toContain("accounts.google.com");
  expect(directive("frame-ancestors")).toBe("frame-ancestors 'none'");
});
