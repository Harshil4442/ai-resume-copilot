/** Browser account mutations require the real NextAuth double-submit token. */
export async function accountMutationHeaders(): Promise<Record<string, string>> {
  const response = await fetch("/api/auth/csrf", { cache: "no-store", redirect: "error", signal: AbortSignal.timeout(10000) });
  if (!response.ok) throw new Error("Account request is unavailable.");
  const result = await response.json();
  if (typeof result.csrfToken !== "string" || !/^[a-f0-9]{64}$/.test(result.csrfToken)) throw new Error("Account request is unavailable.");
  return { "Content-Type": "application/json", "X-Hirewiz-Account-CSRF": result.csrfToken };
}

export function accountApiPath(path: string): string {
  const operations: Record<string, string> = {
    "/auth/profile": "profile", "/auth/me": "me", "/auth/export-account": "export", "/auth/delete-account": "delete",
  };
  return operations[path] ? `/api/account/${operations[path]}` : `/api/backend${path}`;
}
