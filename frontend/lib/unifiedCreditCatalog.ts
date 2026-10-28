import "server-only";

import { parseUnifiedCreditCatalog, type UnifiedCreditCatalog } from "./unifiedCredits";

export type PublicCatalogResult =
  | { status: "ready"; catalog: UnifiedCreditCatalog }
  | { status: "not_open" | "unavailable"; catalog: null };

/** Public pricing must not wait indefinitely or invent an available offer. */
export async function getPublicUnifiedCreditCatalog(): Promise<PublicCatalogResult> {
  const configured = process.env.BACKEND_URL?.trim().replace(/\/+$/, "");
  if (!configured) return { status: "unavailable", catalog: null };
  const base = configured.endsWith("/api") ? configured : `${configured}/api`;
  const controller = new AbortController();
  let timeout: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<PublicCatalogResult>((resolve) => {
    timeout = setTimeout(() => { controller.abort(); resolve({ status: "unavailable", catalog: null }); }, 2_500);
  });
  try {
    return await Promise.race([
      (async () => {
        const response = await fetch(`${base}/public/credits/catalog`, {
          cache: "no-store", redirect: "error", headers: { Accept: "application/json" }, signal: controller.signal,
        });
        if (response.status === 404) return { status: "not_open", catalog: null } as const;
        if (!response.ok) return { status: "unavailable", catalog: null } as const;
        const catalog = parseUnifiedCreditCatalog(await response.json());
        return catalog ? { status: "ready", catalog } as const : { status: "unavailable", catalog: null } as const;
      })(), deadline,
    ]);
  } catch { return { status: "unavailable", catalog: null }; }
  finally { if (timeout !== undefined) clearTimeout(timeout); }
}
