import "server-only";

import { parseUnifiedCreditCatalog, type UnifiedCreditCatalog } from "./unifiedCredits";

/** Public pricing must not wait indefinitely or invent an available offer. */
export async function getPublicUnifiedCreditCatalog(): Promise<UnifiedCreditCatalog | null> {
  const configured = process.env.BACKEND_URL?.trim().replace(/\/+$/, "");
  if (!configured) return null;
  const base = configured.endsWith("/api") ? configured : `${configured}/api`;
  const controller = new AbortController();
  let timeout: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<null>((resolve) => {
    timeout = setTimeout(() => { controller.abort(); resolve(null); }, 2_500);
  });
  try {
    return await Promise.race([
      (async () => {
        const response = await fetch(`${base}/public/credits/catalog`, {
          cache: "no-store", redirect: "error", headers: { Accept: "application/json" }, signal: controller.signal,
        });
        if (!response.ok) return null;
        return parseUnifiedCreditCatalog(await response.json());
      })(), deadline,
    ]);
  } catch { return null; }
  finally { if (timeout !== undefined) clearTimeout(timeout); }
}
