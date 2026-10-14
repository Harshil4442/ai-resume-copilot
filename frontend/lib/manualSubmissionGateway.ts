import { getToken } from "next-auth/jwt";
import { NextRequest, NextResponse } from "next/server";
import { candidateAuthSecret, csrfMatches, websiteOrigin } from "./candidateAuthServer";
import { boundedPairingBytes } from "./browserPairingGateway";

const HEADERS = { "Cache-Control": "private, no-store", Pragma: "no-cache", "X-Content-Type-Options": "nosniff" };
const HEX = /^[a-f0-9]{64}$/;
const failure = (status = 503) => NextResponse.json({ detail: "Your report could not be verified. Refresh this application's status before another attempt; no automatic retry occurred." }, { status, headers: HEADERS });

export async function manualSubmissionGateway(request: NextRequest, identity: string) {
  try {
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(identity) || request.nextUrl.search || request.headers.get("content-type") !== "application/json"
      || request.headers.has("content-encoding") || request.headers.has("authorization")
      || request.headers.get("origin") !== websiteOrigin(request) || request.headers.get("sec-fetch-site") === "cross-site"
      || !csrfMatches(request, request.headers.get("x-hirewiz-account-csrf") || "")) return failure(403);
    const token = await getToken({ req: request, secret: candidateAuthSecret() });
    if (typeof token?.accessToken !== "string") return failure(401);
    const bytes = await boundedPairingBytes(request.body, 4096);
    const payload = JSON.parse(new TextDecoder("utf8", { fatal: true }).decode(bytes));
    if (!payload || Object.keys(payload).sort().join(",") !== "confirmed,package_digest" || payload.confirmed !== true
      || typeof payload.package_digest !== "string" || !HEX.test(payload.package_digest)) return failure(422);
    const base = process.env.BACKEND_URL?.trim().replace(/\/+$/, "").replace(/\/api$/, "") || (process.env.NODE_ENV !== "production" ? "http://127.0.0.1:8000" : "");
    if (!base) return failure();
    const response = await fetch(`${base}/api/v1/employer-jobs/applications/${encodeURIComponent(identity)}/report-submitted`, {
      method: "POST", cache: "no-store", redirect: "error", credentials: "omit", signal: AbortSignal.timeout(12000),
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token.accessToken}` }, body: new Uint8Array(bytes),
    });
    if (!response.ok) { try { void response.body?.cancel().catch(() => undefined); } catch { /* preserve response */ }
      return failure([401, 403, 404, 409, 422, 429].includes(response.status) ? response.status : 503); }
    const raw = await boundedPairingBytes(response.body, 4096);
    const result = JSON.parse(new TextDecoder("utf8", { fatal: true }).decode(raw));
    const report = result?.candidate_report;
    if (result?.id !== identity || result.status !== "user_reported" || result.application_mode !== "manual"
      || result.charged_credits !== 0 || result.reserved_credits !== 0 || result.receipt !== null
      || !report || report.source !== "candidate_report" || report.saved_package_digest !== payload.package_digest
      || typeof report.reported_at !== "string" || !Number.isFinite(Date.parse(report.reported_at))
      || report.employer_confirmed !== false || report.used_file_verified !== false) return failure();
    // Projection avoids leaking arbitrary upstream fields or mislabelled receipts.
    return NextResponse.json({ id: identity, status: "user_reported", application_mode: "manual", charged_credits: 0,
      reserved_credits: 0, receipt: null, candidate_report: { source: "candidate_report", reported_at: report.reported_at,
        saved_package_digest: report.saved_package_digest, employer_confirmed: false, used_file_verified: false } }, { headers: HEADERS });
  } catch { return failure(); }
}
