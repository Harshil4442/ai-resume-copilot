import { getCsrfToken } from "next-auth/react";

export type ResetResult = "RESET_LINK_REQUESTED" | "PASSWORD_RESET" | "RESET_LINK_INVALID" | "RATE_LIMITED" | "RESET_REQUEST_REJECTED" | "RESET_UNAVAILABLE";

export async function passwordReset(operation: "request" | "confirm", body: object,
  signal: AbortSignal, beforeSend: () => void): Promise<ResetResult> {
  const csrf = await getCsrfToken();
  if (!csrf || !/^[a-f0-9]{64}$/.test(csrf) || signal.aborted) throw new Error("RESET_NOT_SENT");
  beforeSend();
  const response = await fetch(`/api/password-reset/${operation}`, { method: "POST", cache: "no-store", redirect: "error",
    credentials: "same-origin", signal: AbortSignal.any([signal, AbortSignal.timeout(20000)]),
    headers: { "Content-Type": "application/json", "X-Hirewiz-Account-CSRF": csrf }, body: JSON.stringify(body) });
  const data: unknown = await response.json();
  if (!data || typeof data !== "object" || Array.isArray(data) || Object.keys(data).join(",") !== "status") return "RESET_UNAVAILABLE";
  const status = (data as { status: unknown }).status;
  const expected: Record<string, number> = { RESET_LINK_REQUESTED: 202, PASSWORD_RESET: 200, RESET_LINK_INVALID: 400,
    RATE_LIMITED: 429, RESET_REQUEST_REJECTED: 422, RESET_UNAVAILABLE: 503 };
  if (status === "RESET_REQUEST_REJECTED" && [403, 422].includes(response.status)) return status;
  if (typeof status !== "string" || expected[status] !== response.status
      || operation === "request" && ["PASSWORD_RESET", "RESET_LINK_INVALID"].includes(status)
      || operation === "confirm" && status === "RESET_LINK_REQUESTED") return "RESET_UNAVAILABLE";
  return status as ResetResult;
}
