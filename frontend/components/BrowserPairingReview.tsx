"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Button } from "./ui/Button";

type Challenge = { challenge_id: string; nonce: string; operation: "confirm_pairing";
  pairing_id: string; device_id: string; key_sha256: string; request_sha256: string; expires_at_ms: number };
type Review = { csrf: string; challenge: Challenge };
type Eligibility = "ELIGIBLE" | "PASSWORD_SIGN_IN_REQUIRED" | "LEGACY_ENROLLMENT_UNAVAILABLE" | "UNAVAILABLE";
const ELIGIBILITY = new Set<Eligibility>(["ELIGIBLE", "PASSWORD_SIGN_IN_REQUIRED", "LEGACY_ENROLLMENT_UNAVAILABLE", "UNAVAILABLE"]);
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;

export default function BrowserPairingReview({ pairingId }: { pairingId: string | null }) {
  const [review, setReview] = useState<Review | null>(null);
  const [password, setPassword] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [eligibility, setEligibility] = useState<Eligibility | null>(null);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  const valid = Boolean(pairingId && UUID.test(pairingId));

  async function post(operation: string, body: object, controller: AbortController, csrf?: string) {
    const response = await fetch(`/api/browser-pairing/candidate/${operation}`, { method: "POST", redirect: "error",
      cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
      headers: { "Content-Type": "application/json", ...(csrf ? { "X-Hirewiz-CSRF": csrf } : {}) }, body: JSON.stringify(body) });
    if (!response.ok) throw new Error(response.status === 401 ? "Sign in to HireWiz before reviewing this connection."
      : response.status === 403 ? "This connection could not be confirmed. Start a fresh review from your companion."
      : "Secure browser pairing is currently unavailable. Your application was not approved by this request.");
    return response.json();
  }

  async function loadReview() {
    if (!valid || active.current) return;
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setError(null); setReview(null); setAccepted(false); setPassword("");
    try {
      // This status comes from the retained server session. It conveys no
      // identity or authority and never substitutes for password approval.
      const response = await fetch("/api/candidate-account/eligibility", { redirect: "error", cache: "no-store",
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]) });
      const status: unknown = response.ok ? await response.json() : null;
      if (!status || typeof status !== "object" || Array.isArray(status) || Object.keys(status).join(",") !== "status"
          || !("status" in status) || !ELIGIBILITY.has(status.status as Eligibility)) throw new Error("Secure browser pairing is currently unavailable.");
      if (controller.signal.aborted) return;
      setEligibility(status.status as Eligibility);
      if (status.status !== "ELIGIBLE") return;
      const csrf = await post("csrf", {}, controller);
      const challenge = await post("challenge", { pairing_id: pairingId }, controller, csrf.csrf_token);
      if (challenge.pairing_id !== pairingId || challenge.operation !== "confirm_pairing") throw new Error("The reviewed connection changed. Start a fresh request from your companion.");
      if (!controller.signal.aborted) setReview({ csrf: csrf.csrf_token, challenge });
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Secure browser pairing is currently unavailable.");
    } finally {
      active.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  async function confirm(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!review || !accepted || !password || active.current) return;
    if (review.challenge.expires_at_ms <= Date.now()) { setReview(null); setPassword(""); setAccepted(false); setError("This review expired. Start a fresh request from your companion."); return; }
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setError(null);
    try {
      const response = await post("confirm", { protocol_version: 2, operation: "confirm_pairing", method: "password_reauth",
        challenge_id: review.challenge.challenge_id, nonce: review.challenge.nonce, password, confirmed: true }, controller, review.csrf);
      if (response.status !== "CANDIDATE_CONFIRMED" || response.pairing_id !== review.challenge.pairing_id || response.device_id !== review.challenge.device_id) throw new Error("The pairing response could not be verified. No automatic retry will occur.");
      if (!controller.signal.aborted) setConfirmed(true);
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "The connection outcome is unavailable. Return to your companion without retrying automatically.");
    } finally {
      setPassword(""); setAccepted(false); setReview(null); active.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return <section className="surface space-y-6 p-5 sm:p-8" aria-busy={busy}>
    <div><p className="eyebrow">Browser connection</p><h1 className="mt-3 font-display text-3xl sm:text-4xl">Connect your companion</h1>
      <p className="mt-4 max-w-2xl text-sm leading-7 text-muted-foreground">Review the connection opened by your HireWiz companion. Connecting identifies this device. You will review each job, resume and answer separately before any employer action.</p></div>
    {!valid ? <p role="alert" className="text-sm text-coral">Open this page from a fresh connection request in your browser companion.</p>
      : confirmed ? <div role="status" className="rounded-xl border border-border bg-surface p-5"><h2 className="text-lg font-semibold">Candidate confirmation saved</h2><p className="mt-2 text-sm leading-7 text-muted-foreground">Return to your companion to finish proving its device key. This connection has not approved filling, uploading or submitting a job application.</p></div>
      : eligibility === "PASSWORD_SIGN_IN_REQUIRED" ? <div className="space-y-3"><p className="text-sm leading-7 text-muted-foreground">Sign in with your HireWiz password to review this browser connection.</p><Link className="inline-block text-sm font-semibold text-primary underline underline-offset-4" href={`/login?returnTo=${encodeURIComponent(`/browser-companion?pairing=${pairingId}`)}`}>Sign in and return to this connection</Link></div>
      : eligibility === "LEGACY_ENROLLMENT_UNAVAILABLE" ? <p role="status" className="text-sm leading-7 text-muted-foreground">Secure browser connection is not available for this account yet. Your existing HireWiz account and job search remain available.</p>
      : eligibility === "UNAVAILABLE" ? <p role="status" className="text-sm leading-7 text-muted-foreground">Secure browser connection is currently unavailable. No application action has been authorized.</p>
      : review ? <form onSubmit={confirm} className="space-y-5">
        <div className="rounded-xl border border-border p-4"><h2 className="font-semibold">Verify the same request</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">Compare these fingerprints with the connection displayed in your companion.</p><dl className="mt-4 space-y-3 text-xs"><div><dt className="font-semibold">Device key</dt><dd className="mt-1 break-all font-mono">{review.challenge.key_sha256}</dd></div><div><dt className="font-semibold">Connection request</dt><dd className="mt-1 break-all font-mono">{review.challenge.request_sha256}</dd></div></dl></div>
        <label className="flex items-start gap-3 text-sm leading-6"><input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} disabled={busy} className="mt-1" />I checked that these fingerprints match my companion and want to connect this device.</label>
        <label className="grid gap-2 text-sm font-semibold">HireWiz password<input type="password" autoComplete="current-password" maxLength={1024} value={password} onChange={(event) => setPassword(event.target.value)} disabled={busy} required className="field-control" /></label>
        <p className="text-xs leading-6 text-muted-foreground">Your password is sent only to HireWiz for this confirmation. The companion does not receive it.</p>
        <Button type="submit" disabled={busy || !accepted || !password}>{busy ? "Confirming…" : "Confirm this device"}</Button>
      </form> : <Button onClick={() => void loadReview()} disabled={busy}>{busy ? "Loading review…" : "Review connection"}</Button>}
    {error ? <p role="alert" className="text-sm leading-6 text-coral">{error}</p> : null}
    <Link href="/employer-jobs" className="inline-block text-sm font-semibold text-primary underline underline-offset-4">Return to job search</Link>
  </section>;
}
