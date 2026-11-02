import { getCsrfToken } from "next-auth/react";

export type ErasureResult = "ERASURE_LINK_REQUESTED" | "ACCOUNT_ERASED" | "ERASURE_LINK_INVALID"
  | "RATE_LIMITED" | "ERASURE_REQUEST_REJECTED" | "ERASURE_UNAVAILABLE";

export async function privacyErasure(operation: "request" | "confirm", body: object,
  signal: AbortSignal, beforeSend: () => void): Promise<ErasureResult> {
  const csrf = await getCsrfToken();
  if (!csrf || !/^[a-f0-9]{64}$/.test(csrf) || signal.aborted) throw new Error("ERASURE_NOT_SENT");
  beforeSend();
  const response = await fetch(`/api/privacy-erasure/${operation}`, { method: "POST", cache: "no-store", redirect: "error",
    credentials: "same-origin", signal: AbortSignal.any([signal, AbortSignal.timeout(20000)]),
    headers: { "Content-Type": "application/json", "X-Hirewiz-Account-CSRF": csrf }, body: JSON.stringify(body) });
  const data: unknown = await response.json();
  if (!data || typeof data !== "object" || Array.isArray(data) || Object.keys(data).join(",") !== "status") return "ERASURE_UNAVAILABLE";
  const status = (data as { status: unknown }).status;
  const expected: Record<string, number> = { ERASURE_LINK_REQUESTED: 202, ACCOUNT_ERASED: 200,
    ERASURE_LINK_INVALID: 400, RATE_LIMITED: 429, ERASURE_REQUEST_REJECTED: 422, ERASURE_UNAVAILABLE: 503 };
  if (status === "ERASURE_REQUEST_REJECTED" && [403, 422].includes(response.status)) return status;
  if (typeof status !== "string" || expected[status] !== response.status
      || operation === "request" && ["ACCOUNT_ERASED", "ERASURE_LINK_INVALID"].includes(status)
      || operation === "confirm" && status === "ERASURE_LINK_REQUESTED") return "ERASURE_UNAVAILABLE";
  return status as ErasureResult;
}
