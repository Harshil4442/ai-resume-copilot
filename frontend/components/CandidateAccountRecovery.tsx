"use client";

import { getCsrfToken, useSession } from "next-auth/react";
import Link from "next/link";
import Script from "next/script";
import { useCallback, useEffect, useRef, useState } from "react";

import type { components } from "../lib/generated/api";
import { Button } from "./ui/Button";

type Challenge = { challenge_id: string; nonce: string; client_id: string; expires_at_ms: number };
type Eligibility = "ELIGIBLE" | "PASSWORD_SIGN_IN_REQUIRED" | "GOOGLE_ENROLLMENT_AVAILABLE" | "LEGACY_ENROLLMENT_UNAVAILABLE" | "UNAVAILABLE";
type GoogleId = {
  initialize: (configuration: { client_id: string; nonce: string; callback: (reply: { credential?: string }) => void;
    auto_select: false; ux_mode: "popup" }) => void;
  renderButton: (element: HTMLElement, options: { type: "standard"; theme: "outline"; size: "large"; text: "continue_with" }) => void;
  cancel?: () => void;
};
const ELIGIBILITY = new Set<Eligibility>(["ELIGIBLE", "PASSWORD_SIGN_IN_REQUIRED", "GOOGLE_ENROLLMENT_AVAILABLE", "LEGACY_ENROLLMENT_UNAVAILABLE", "UNAVAILABLE"]);
const REFUSALS: Record<string, number> = { RECOVERY_AUTHENTICATION_FAILED: 401, RECOVERY_EMAIL_PROOF_REQUIRED: 403,
  RECOVERY_UNAVAILABLE: 409, RECOVERY_REQUEST_REJECTED: 422, RATE_LIMITED: 429, REJECTED: 403 };
const GOOGLE_SCRIPT = "https://accounts.google.com/gsi/client";
const LOGIN = "/login?returnTo=%2Faccount-recovery";

class RecoveryRefusal extends Error {
  constructor(readonly code: string) { super("Account recovery was refused"); }
}

class RecoveryProofExpired extends Error {}

function googleId(): GoogleId | undefined {
  return (window as Window & { google?: { accounts?: { id?: GoogleId } } }).google?.accounts?.id;
}

async function post(operation: string, body: object, controller: AbortController, beforeSend?: () => void) {
  const csrf = await getCsrfToken();
  if (!csrf || !/^[a-f0-9]{64}$/.test(csrf) || controller.signal.aborted) throw new Error();
  beforeSend?.();
  const response = await fetch(`/api/candidate-account/${operation}`, { method: "POST", cache: "no-store",
    credentials: "same-origin", redirect: "error", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]),
    headers: { "Content-Type": "application/json", "X-Hirewiz-Account-CSRF": csrf }, body: JSON.stringify(body) });
  const data: unknown = await response.json();
  if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error();
  const result = data as Record<string, unknown>;
  if (!response.ok) {
    if (Object.keys(result).join(",") === "status" && typeof result.status === "string" && REFUSALS[result.status] === response.status) {
      throw new RecoveryRefusal(result.status);
    }
    throw new Error();
  }
  return result;
}

function refusalMessage(code: string): string {
  switch (code) {
    case "RECOVERY_EMAIL_PROOF_REQUIRED": return "This Google account cannot verify current ownership of your HireWiz email. Use your existing HireWiz password or contact support. This recovery option supports Gmail and verified Google Workspace email only.";
    case "RECOVERY_UNAVAILABLE": return "Google recovery is unavailable for this account. It only supports older accounts that have not enrolled yet. Use your existing sign-in method or contact support.";
    case "RECOVERY_AUTHENTICATION_FAILED": return "Google recovery could not verify the same account or the proof expired. Sign in with the same Google account again, then start a fresh recovery review.";
    case "RATE_LIMITED": return "Too many account requests. Wait before starting a fresh recovery review.";
    default: return "The recovery request was rejected before enrollment. Reload and review your sign-in, password and consent before starting again.";
  }
}

function RecoveryForm({ email }: { email: string }) {
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [verified, setVerified] = useState(false);
  const [expired, setExpired] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [pending, setPending] = useState(false);
  const [enrolled, setEnrolled] = useState(false);
  const [statusEmail, setStatusEmail] = useState(email);
  const [statusPassword, setStatusPassword] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);
  const active = useRef<AbortController | null>(null);
  const proof = useRef<{ challenge: Challenge; idToken: string } | null>(null);
  const activeChallenge = useRef<Challenge | null>(null);
  const initialized = useRef(false);
  const googleButton = useRef<HTMLDivElement>(null);
  const scriptTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const expireProof = useCallback((selected: Challenge) => {
    if (!mounted.current || activeChallenge.current !== selected) return;
    proof.current = null; activeChallenge.current = null; initialized.current = false;
    if (scriptTimer.current) clearTimeout(scriptTimer.current);
    googleId()?.cancel?.();
    setChallenge(null); setVerified(false); setExpired(true); setMessage(null);
    setError("Google verification expired. Your new password and consent are still here. Verify Google again before submitting recovery.");
  }, []);

  useEffect(() => {
    if (!challenge) return;
    const timer = setTimeout(() => expireProof(challenge), Math.max(0, challenge.expires_at_ms - Date.now()));
    return () => clearTimeout(timer);
  }, [challenge, expireProof]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false; active.current?.abort(); proof.current = null; activeChallenge.current = null;
      if (scriptTimer.current) clearTimeout(scriptTimer.current);
      if (initialized.current) googleId()?.cancel?.();
    };
  }, []);

  async function startGoogle() {
    if (active.current || blocked || pending || enrolled || challenge || !accepted) return;
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setError(null); setMessage(null); setExpired(false);
    try {
      const data = await post("recovery-google-challenge", {}, controller);
      if (Object.keys(data).sort().join(",") !== "challenge_id,client_id,expires_at_ms,nonce"
          || typeof data.challenge_id !== "string" || !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(data.challenge_id)
          || typeof data.nonce !== "string" || !/^[A-Za-z0-9_-]{32,128}$/.test(data.nonce)
          || typeof data.client_id !== "string" || !/^[0-9]+-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com$/.test(data.client_id)
          || !Number.isSafeInteger(data.expires_at_ms) || (data.expires_at_ms as number) <= Date.now()
          || (data.expires_at_ms as number) > Date.now() + 300000) throw new Error();
      if (controller.signal.aborted) return;
      const selected = data as Challenge;
      activeChallenge.current = selected; setChallenge(selected);
      scriptTimer.current = setTimeout(() => {
        if (!mounted.current || activeChallenge.current !== selected || initialized.current) return;
        activeChallenge.current = null; setBlocked(true);
        setError("Google verification could not load. Reload before a fresh recovery review.");
      }, 15000);
    } catch (failure) {
      if (!controller.signal.aborted) {
        setBlocked(true);
        setError(failure instanceof RecoveryRefusal ? refusalMessage(failure.code)
          : "Recovery verification is unavailable. No password change was started. Sign in with Google again or contact support.");
      }
    } finally {
      active.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  function initializeGoogle() {
    if (!challenge || activeChallenge.current !== challenge || initialized.current || !mounted.current) return;
    if (challenge.expires_at_ms <= Date.now()) { expireProof(challenge); return; }
    const id = googleId();
    if (!id || !googleButton.current) {
      setBlocked(true); setError("Google verification is unavailable or expired. Reload before a fresh recovery review."); return;
    }
    if (scriptTimer.current) clearTimeout(scriptTimer.current);
    initialized.current = true;
    try {
      id.initialize({ client_id: challenge.client_id, nonce: challenge.nonce, auto_select: false, ux_mode: "popup",
        callback: (reply) => {
          if (!mounted.current || activeChallenge.current !== challenge || proof.current) return;
          if (challenge.expires_at_ms <= Date.now()) { expireProof(challenge); return; }
          if (typeof reply.credential !== "string" || reply.credential.length < 100 || reply.credential.length > 4096) {
            activeChallenge.current = null; setBlocked(true); setError("Google verification expired or could not be verified. Reload for a fresh recovery review."); return;
          }
          proof.current = { challenge, idToken: reply.credential }; setVerified(true); setError(null);
          setMessage("Google verification received. Review your new password and recovery consent, then submit recovery yourself.");
        } });
      id.renderButton(googleButton.current, { type: "standard", theme: "outline", size: "large", text: "continue_with" });
    } catch {
      activeChallenge.current = null; setBlocked(true); setError("Google verification is unavailable. Reload before a fresh recovery review.");
    }
  }

  async function recover(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (active.current || blocked || pending || enrolled || !accepted || password.length < 10 || password.length > 128 || password !== confirmation) return;
    const selected = proof.current;
    if (!selected || selected.challenge.expires_at_ms <= Date.now()) {
      if (selected) expireProof(selected.challenge);
      return;
    }
    const controller = new AbortController(); active.current = controller;
    let sent = false, keepForm = false;
    setBusy(true); setError(null); setMessage(null);
    try {
      const body: components["schemas"]["GoogleRecovery"] = { challenge_id: selected.challenge.challenge_id, nonce: selected.challenge.nonce,
        id_token: selected.idToken, password, accepted_enrollment: true, accepted_recovery: true };
      const result = await post("recover-google", body, controller, () => {
        if (proof.current !== selected || selected.challenge.expires_at_ms <= Date.now()) throw new RecoveryProofExpired();
        sent = true;
      });
      if (Object.keys(result).join(",") !== "status" || result.status !== "ENROLLED") throw new Error();
      if (!controller.signal.aborted) { setEnrolled(true); setMessage("Your password was replaced and this existing account is enrolled. Sign out, then sign in again with Google or your new HireWiz password."); }
    } catch (failure) {
      if (!controller.signal.aborted) {
        if (failure instanceof RecoveryProofExpired) { keepForm = true; expireProof(selected.challenge); }
        else if (failure instanceof RecoveryRefusal) { setBlocked(true); setError(refusalMessage(failure.code)); }
        else if (sent) { setPending(true); setError("The recovery outcome is uncertain. Check enrollment status using your account email and the new password you chose. Do not submit recovery again."); }
        else { setBlocked(true); setError("Recovery was not sent. Reload and review your sign-in before starting again."); }
      }
    } finally {
      proof.current = null; activeChallenge.current = null; setVerified(false);
      if (!keepForm) { setPassword(""); setConfirmation(""); setAccepted(false); }
      active.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  async function checkStatus(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (active.current || !pending || !statusEmail || !statusPassword) return;
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setError(null); setMessage(null);
    try {
      const result = await post("registration-status", { email: statusEmail, password: statusPassword }, controller);
      if (Object.keys(result).join(",") !== "status" || !["ENROLLED", "PENDING_REVIEW_REQUIRED", "LEGACY_ENROLLMENT_UNAVAILABLE"].includes(result.status as string)) throw new Error();
      if (!controller.signal.aborted) {
        if (result.status === "ENROLLED") { setEnrolled(true); setPending(false); setMessage("Recovery enrollment is complete. Sign out, then sign in again with Google or your new HireWiz password."); }
        else setMessage(result.status === "PENDING_REVIEW_REQUIRED"
          ? "Recovery enrollment needs review. Checking status cannot activate it. Contact support and do not submit recovery again."
          : "Recovery enrollment could not be confirmed. Contact support before another recovery request.");
      }
    } catch { if (!controller.signal.aborted) setError("Enrollment status could not be verified. Check the email and new password you entered, or contact support. Do not submit recovery again."); }
    finally { setStatusPassword(""); active.current = null; if (!controller.signal.aborted) setBusy(false); }
  }

  return <div className="space-y-5" aria-busy={busy}>
    {enrolled ? <div role="status" className="space-y-3"><p>{message}</p><Link href="/logout" className="font-semibold text-primary underline">Sign out before signing in again</Link></div>
      : pending ? <form onSubmit={checkStatus} className="space-y-4"><h2 className="text-lg font-semibold">Check enrollment status</h2>
        <p className="text-sm leading-7">This only checks the earlier recovery request. It cannot activate enrollment or repeat recovery.</p>
        <label className="grid gap-2 text-sm font-semibold">Account email<input className="field-control" type="email" autoComplete="email" required maxLength={320} value={statusEmail} onChange={(event) => setStatusEmail(event.target.value)} disabled={busy} /></label>
        <label className="grid gap-2 text-sm font-semibold">New HireWiz password for status<input className="field-control" type="password" autoComplete="current-password" required maxLength={128} value={statusPassword} onChange={(event) => setStatusPassword(event.target.value)} disabled={busy} /></label>
        <Button type="submit" disabled={busy || !statusEmail || !statusPassword}>Check enrollment status</Button></form>
      : blocked ? <div className="space-y-3"><Link href={LOGIN} className="block font-semibold text-primary underline">Sign in with Google again</Link><Button type="button" variant="secondary" onClick={() => window.location.reload()}>Reload for a fresh recovery review</Button></div>
      : <form onSubmit={recover} className="space-y-5">
        <label className="grid gap-2 text-sm font-semibold">New HireWiz password<input className="field-control" type="password" autoComplete="new-password" required minLength={10} maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} disabled={busy} /></label>
        <label className="grid gap-2 text-sm font-semibold">Confirm new HireWiz password<input className="field-control" type="password" autoComplete="new-password" required minLength={10} maxLength={128} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} disabled={busy} /></label>
        <p className="text-xs leading-6 text-muted-foreground">Use 10–128 characters. Your Google password is never requested here.{confirmation && confirmation !== password ? " The new passwords must match." : ""}</p>
        <label className="flex items-start gap-3 text-sm leading-7"><input className="mt-1.5" type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} disabled={busy} />I want to replace my forgotten HireWiz password and enroll this existing account for browser approval. Old HireWiz sessions will end. Each device and application still needs separate approval.</label>
        <div className="space-y-3"><p className="text-xs leading-6 text-muted-foreground">Starting verification loads Google Identity Services. Choose the same Google account whose email is already on your HireWiz account.</p>
          {!challenge ? <Button type="button" variant="secondary" disabled={busy || !accepted} onClick={() => void startGoogle()}>{expired ? "Verify Google again" : "Verify Google for recovery"}</Button>
            : <><div ref={googleButton} aria-label="Google recovery verification" /><p className="text-sm">Google verification expires at {new Date(challenge.expires_at_ms).toLocaleTimeString()}. If it expires, verify again here; your password and consent stay in this form.</p>{!verified ? <p className="text-sm">Use the Google button to verify this recovery.</p> : null}</>}
          {challenge ? <Script key={challenge.challenge_id} src={GOOGLE_SCRIPT} strategy="afterInteractive" onReady={initializeGoogle} onError={() => {
            if (!mounted.current || activeChallenge.current !== challenge) return;
            activeChallenge.current = null; setBlocked(true); setError("Google verification could not load. Reload before a fresh recovery review.");
          }} /> : null}
        </div>
        <Button type="submit" disabled={busy || !accepted || !verified || password.length < 10 || password.length > 128 || password !== confirmation}>{busy ? "Working…" : "Replace password and enroll this account"}</Button>
      </form>}
    {message && !enrolled ? <p role="status" className="text-sm leading-7">{message}</p> : null}
    {error ? <p role="alert" className="text-sm leading-7 text-coral">{error}</p> : null}
  </div>;
}

export default function CandidateAccountRecovery() {
  const { data: session, status } = useSession();
  const owner = session?.user?.id || session?.user?.email || "";
  const [resolved, setResolved] = useState<{ owner: string; eligibility: Eligibility } | null>(null);
  useEffect(() => {
    if (status !== "authenticated" || !owner) return;
    const controller = new AbortController();
    Promise.all(["eligibility", "availability"].map(async (operation) => {
      const response = await fetch(`/api/candidate-account/${operation}`, { cache: "no-store", redirect: "error",
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]) });
      if (!response.ok) throw new Error();
      return response.json();
    })).then(([data, available]) => {
      if (!data || Object.keys(data).join(",") !== "status" || !ELIGIBILITY.has(data.status)
          || !available || Object.keys(available).sort().join(",") !== "fresh_registration,legacy_enrollment"
          || typeof available.fresh_registration !== "boolean" || typeof available.legacy_enrollment !== "boolean") throw new Error();
      if (!controller.signal.aborted) setResolved({ owner, eligibility: available.legacy_enrollment || data.status === "ELIGIBLE" ? data.status as Eligibility : "UNAVAILABLE" });
    }).catch(() => { if (!controller.signal.aborted) setResolved({ owner, eligibility: "UNAVAILABLE" }); });
    return () => controller.abort();
  }, [status, owner]);
  const eligibility = resolved?.owner === owner ? resolved.eligibility : null;
  return <section className="surface space-y-6 p-5 sm:p-8" aria-labelledby="recovery-title">
    <div><p className="eyebrow">Your existing HireWiz account</p><h1 id="recovery-title" className="mt-3 font-display text-3xl sm:text-4xl">Forgot your HireWiz password?</h1>
      <p className="mt-4 text-sm leading-7 text-muted-foreground">Use Google to recover an older account that has not enrolled for browser approval. This replaces your HireWiz password and enrolls the same account, keeping its resumes and workspace. It supports Gmail and verified Google Workspace email only.</p></div>
    {status === "loading" || status === "authenticated" && owner && eligibility === null ? <p role="status">Checking your account…</p>
      : status !== "authenticated" || eligibility === "PASSWORD_SIGN_IN_REQUIRED" || eligibility === "LEGACY_ENROLLMENT_UNAVAILABLE" ? <p><Link href={LOGIN} className="font-semibold text-primary underline">Sign in with Google to continue recovery</Link>. Use the Google account with the email already on your HireWiz account.</p>
      : eligibility === "ELIGIBLE" ? <p role="status">This account is already enrolled. This recovery option only supports older accounts that have not enrolled. Use your existing sign-in method or contact support.</p>
      : eligibility !== "GOOGLE_ENROLLMENT_AVAILABLE" ? <p role="status">Account recovery is currently unavailable. Use your existing sign-in method or contact support.</p>
      : <RecoveryForm key={owner} email={session?.user?.email || ""} />}
    <div className="flex flex-wrap gap-4 text-sm font-semibold text-primary underline"><Link href="/account-enrollment">Use my existing HireWiz password</Link><Link href="/contact">Contact support</Link><Link href="/dashboard">Return to your workspace</Link></div>
  </section>;
}
