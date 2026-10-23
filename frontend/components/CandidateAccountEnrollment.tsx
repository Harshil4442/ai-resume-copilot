"use client";

import { getCsrfToken, useSession } from "next-auth/react";
import Link from "next/link";
import Script from "next/script";
import { useEffect, useRef, useState } from "react";

import { Button } from "./ui/Button";

type Challenge = { challenge_id: string; nonce: string; client_id: string; expires_at_ms: number };
type GoogleId = {
  initialize: (configuration: { client_id: string; nonce: string; callback: (reply: { credential?: string }) => void;
    auto_select: false; ux_mode: "popup" }) => void;
  renderButton: (element: HTMLElement, options: { type: "standard"; theme: "outline"; size: "large"; text: "continue_with" }) => void;
  cancel?: () => void;
};
type Eligibility = "ELIGIBLE" | "PASSWORD_SIGN_IN_REQUIRED" | "GOOGLE_ENROLLMENT_AVAILABLE" | "LEGACY_ENROLLMENT_UNAVAILABLE" | "UNAVAILABLE";
const ELIGIBILITY = new Set<Eligibility>(["ELIGIBLE", "PASSWORD_SIGN_IN_REQUIRED", "GOOGLE_ENROLLMENT_AVAILABLE", "LEGACY_ENROLLMENT_UNAVAILABLE", "UNAVAILABLE"]);
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const GOOGLE_SCRIPT = "https://accounts.google.com/gsi/client";

function googleId(): GoogleId | undefined {
  return (window as Window & { google?: { accounts?: { id?: GoogleId } } }).google?.accounts?.id;
}

export default function CandidateAccountEnrollment() {
  const { data: session, status: sessionStatus } = useSession();
  const [eligibility, setEligibility] = useState<Eligibility | null>(null);
  const [mode, setMode] = useState<"google" | "password">("google");
  const [password, setPassword] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [hasPassword, setHasPassword] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [googleVerified, setGoogleVerified] = useState(false);
  const [googleStarted, setGoogleStarted] = useState(false);
  const [enrolled, setEnrolled] = useState(false);
  const [pending, setPending] = useState(false);
  const [statusEmail, setStatusEmail] = useState("");
  const [statusPassword, setStatusPassword] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);
  const active = useRef<AbortController | null>(null);
  const proof = useRef<{ challenge: Challenge; idToken: string } | null>(null);
  const activeChallenge = useRef<Challenge | null>(null);
  const googleButton = useRef<HTMLDivElement>(null);
  const initialized = useRef(false);
  const scriptTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false; active.current?.abort(); proof.current = null; activeChallenge.current = null;
      if (scriptTimer.current) clearTimeout(scriptTimer.current);
      if (initialized.current) googleId()?.cancel?.();
    };
  }, []);

  useEffect(() => {
    if (sessionStatus !== "authenticated") return;
    const controller = new AbortController();
    Promise.all(["eligibility", "availability"].map(async (operation) => {
      const response = await fetch(`/api/candidate-account/${operation}`, { cache: "no-store", redirect: "error",
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]) });
      if (!response.ok) throw new Error();
      return response.json();
    }))
      .then(([data, availability]) => {
        if (!data || Object.keys(data).join(",") !== "status" || !ELIGIBILITY.has(data.status)
            || !availability || Object.keys(availability).sort().join(",") !== "fresh_registration,legacy_enrollment"
            || typeof availability.fresh_registration !== "boolean" || typeof availability.legacy_enrollment !== "boolean") throw new Error();
        if (!controller.signal.aborted) setEligibility(availability.legacy_enrollment || data.status === "ELIGIBLE"
          ? data.status as Eligibility : "UNAVAILABLE");
      })
      .catch(() => { if (!controller.signal.aborted) setEligibility("UNAVAILABLE"); });
    return () => controller.abort();
  }, [sessionStatus]);

  async function post(operation: string, body: object, controller: AbortController, beforeSend?: () => void) {
    const csrf = await getCsrfToken();
    if (!csrf || !/^[a-f0-9]{64}$/.test(csrf) || controller.signal.aborted) throw new Error();
    beforeSend?.();
    const response = await fetch(`/api/candidate-account/${operation}`, { method: "POST", cache: "no-store",
      credentials: "same-origin", redirect: "error", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]),
      headers: { "Content-Type": "application/json", "X-Hirewiz-Account-CSRF": csrf }, body: JSON.stringify(body) });
    if (!response.ok) throw new Error();
    return response.json() as Promise<Record<string, unknown>>;
  }

  function chooseMode(value: "google" | "password") {
    if (active.current || pending || enrolled) return;
    proof.current = null; activeChallenge.current = null; setChallenge(null); setGoogleVerified(false);
    setPassword(""); setCurrentPassword(""); setAccepted(false); setMode(value);
    setError(null); setMessage(null);
    if (initialized.current) googleId()?.cancel?.();
  }

  async function startGoogle() {
    if (active.current || pending || enrolled || googleStarted || !accepted) return;
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setError(null); setMessage(null);
    try {
      const data = await post("google-enrollment-challenge", {}, controller);
      if (Object.keys(data).sort().join(",") !== "challenge_id,client_id,expires_at_ms,nonce"
          || typeof data.challenge_id !== "string" || !UUID.test(data.challenge_id)
          || typeof data.nonce !== "string" || !/^[A-Za-z0-9_-]{32,128}$/.test(data.nonce)
          || typeof data.client_id !== "string" || !/^[0-9]+-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com$/.test(data.client_id)
          || !Number.isSafeInteger(data.expires_at_ms) || (data.expires_at_ms as number) <= Date.now()
          || (data.expires_at_ms as number) > Date.now() + 300000) throw new Error();
      if (controller.signal.aborted) return;
      activeChallenge.current = data as Challenge;
      setChallenge(data as Challenge); setGoogleStarted(true);
      scriptTimer.current = setTimeout(() => {
        if (!mounted.current || initialized.current) return;
        activeChallenge.current = null; setChallenge(null);
        setError("Google verification could not load. Reload this page before a fresh verification.");
      }, 15000);
    } catch {
      if (!controller.signal.aborted) setError("Google verification is unavailable. Sign in with Google again, then return to start a fresh enrollment review.");
    } finally {
      active.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  function initializeGoogle() {
    if (!challenge || initialized.current || !mounted.current) return;
    const id = googleId();
    if (!id || !googleButton.current || challenge.expires_at_ms <= Date.now()) {
      setError("Google verification is unavailable or expired. Reload before a fresh verification.");
      return;
    }
    if (scriptTimer.current) clearTimeout(scriptTimer.current);
    initialized.current = true;
    try {
      id.initialize({ client_id: challenge.client_id, nonce: challenge.nonce, auto_select: false, ux_mode: "popup",
      callback: (reply) => {
        if (!mounted.current || activeChallenge.current?.challenge_id !== challenge.challenge_id) return;
        if (proof.current || challenge.expires_at_ms <= Date.now()
            || typeof reply.credential !== "string" || reply.credential.length < 100 || reply.credential.length > 4096) {
          if (mounted.current) setError("Google verification expired or could not be verified. Reload for a fresh review.");
          return;
        }
        proof.current = { challenge, idToken: reply.credential };
        setGoogleVerified(true); setError(null); setMessage("Google verification received. Review your password and enrollment choice, then enroll this account.");
      } });
      id.renderButton(googleButton.current, { type: "standard", theme: "outline", size: "large", text: "continue_with" });
    } catch {
      activeChallenge.current = null;
      setError("Google verification is unavailable. Reload before a fresh verification.");
    }
  }

  async function enroll(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (active.current || pending || enrolled || !accepted || !password || password.length > 128) return;
    const selectedProof = proof.current;
    if (mode === "google" && (password.length < 10 || !selectedProof || selectedProof.challenge.expires_at_ms <= Date.now()
        || hasPassword && !currentPassword)) {
      proof.current = null; activeChallenge.current = null; setGoogleVerified(false);
      setError("Complete fresh Google verification and your password choice before enrolling."); return;
    }
    const controller = new AbortController(); active.current = controller;
    let sent = false;
    setBusy(true); setError(null); setMessage(null);
    try {
      const body = mode === "google" && selectedProof ? { challenge_id: selectedProof.challenge.challenge_id,
        nonce: selectedProof.challenge.nonce, id_token: selectedProof.idToken, password,
        ...(hasPassword ? { current_password: currentPassword } : {}), accepted_enrollment: true }
        : { password, accepted_enrollment: true };
      const result = await post(mode === "google" ? "enroll-google" : "enroll-password", body, controller, () => { sent = true; });
      if (Object.keys(result).join(",") !== "status" || result.status !== "ENROLLED") throw new Error();
      if (!controller.signal.aborted) {
        setEnrolled(true); setMessage(mode === "google"
          ? "Enrollment is complete for your existing account. Sign out, then sign in with Google or your HireWiz password to use the browser companion."
          : "Enrollment is complete for your existing account. Sign out, then sign in with your HireWiz password to use the browser companion.");
      }
    } catch {
      if (!controller.signal.aborted) {
        if (sent) {
          setPending(true); setStatusEmail(session?.user?.email || "");
          setError("The enrollment outcome is uncertain. Check enrollment status with your email and HireWiz password. Do not send enrollment again.");
        } else setError("Enrollment was not started. Check your sign-in and try again.");
      }
    } finally {
      proof.current = null; activeChallenge.current = null; setPassword(""); setCurrentPassword(""); setAccepted(false); setGoogleVerified(false);
      active.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  async function checkStatus(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (active.current || !statusEmail || !statusPassword || !pending) return;
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setError(null); setMessage(null);
    try {
      const result = await post("registration-status", { email: statusEmail, password: statusPassword }, controller);
      if (Object.keys(result).join(",") !== "status" || !["ENROLLED", "PENDING_REVIEW_REQUIRED", "LEGACY_ENROLLMENT_UNAVAILABLE"].includes(result.status as string)) throw new Error();
      if (!controller.signal.aborted) {
        if (result.status === "ENROLLED") {
          setEnrolled(true); setPending(false);
          setMessage("Enrollment is complete. Sign out, then sign in again using your enrolled sign-in method.");
        } else setMessage(result.status === "PENDING_REVIEW_REQUIRED"
          ? "Enrollment needs review. Checking status cannot activate it. Contact support and do not submit enrollment again."
          : "Enrollment could not be confirmed for this account. Your existing workspace remains available. Contact support before another enrollment request.");
      }
    } catch {
      if (!controller.signal.aborted) setError("Enrollment status could not be verified. Check your email and password or try this status check later.");
    } finally {
      setStatusPassword(""); active.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  const available = sessionStatus === "authenticated" && eligibility !== null && !["ELIGIBLE", "UNAVAILABLE", "PASSWORD_SIGN_IN_REQUIRED"].includes(eligibility);
  return <section className="surface space-y-6 p-5 sm:p-8" aria-labelledby="enrollment-title" aria-busy={busy}>
    <div><p className="eyebrow">Your existing HireWiz account</p><h1 id="enrollment-title" className="mt-3 font-display text-3xl sm:text-4xl">Enable browser account approval</h1>
      <p className="mt-4 text-sm leading-7 text-muted-foreground">Keep your account, resumes and workspace. Enroll it for browser companion approval and use a HireWiz password when approving a final application submission. Each device and application still needs your separate review.</p></div>
    {sessionStatus === "loading" || sessionStatus === "authenticated" && eligibility === null ? <p role="status">Checking your account…</p>
      : sessionStatus !== "authenticated" || eligibility === "PASSWORD_SIGN_IN_REQUIRED" ? <p><Link className="font-semibold text-primary underline" href="/login">Sign in to continue</Link>, then return to this enrollment page.</p>
      : enrolled || eligibility === "ELIGIBLE" ? <div role="status" className="space-y-3"><p>{message || "Your account is already enrolled. Continue to your browser companion."}</p>
        {enrolled ? <Link className="inline-block font-semibold text-primary underline" href="/logout">Sign out before signing in again</Link> : <Link className="font-semibold text-primary underline" href="/browser-companion">Open browser companion</Link>}</div>
      : eligibility === "UNAVAILABLE" ? <p role="status">Account enrollment is currently unavailable. Your existing workspace remains available.</p>
      : pending ? <form className="space-y-4" onSubmit={checkStatus}>
        <h2 className="text-lg font-semibold">Check enrollment status</h2>
        <p className="text-sm leading-7 text-muted-foreground">This checks the earlier request. It does not create an account or retry enrollment.</p>
        <label className="grid gap-2 text-sm font-semibold">Account email<input className="field-control" type="email" autoComplete="email" required maxLength={320} value={statusEmail} onChange={(event) => setStatusEmail(event.target.value)} disabled={busy} /></label>
        <label className="grid gap-2 text-sm font-semibold">HireWiz password for status<input className="field-control" type="password" autoComplete="current-password" required maxLength={128} value={statusPassword} onChange={(event) => setStatusPassword(event.target.value)} disabled={busy} /></label>
        <Button type="submit" disabled={busy || !statusEmail || !statusPassword}>Check enrollment status</Button>
      </form>
      : available ? <form className="space-y-5" onSubmit={enroll}>
        <fieldset className="space-y-3" disabled={busy}><legend className="text-sm font-semibold">How do you sign in to this account?</legend>
          <label className="mr-6 inline-flex items-center gap-2 text-sm"><input type="radio" name="enrollment-method" checked={mode === "google"} onChange={() => chooseMode("google")} />Google</label>
          <label className="inline-flex items-center gap-2 text-sm"><input type="radio" name="enrollment-method" checked={mode === "password"} onChange={() => chooseMode("password")} />HireWiz password</label>
        </fieldset>
        <label className="grid gap-2 text-sm font-semibold">{mode === "google" ? "Choose a HireWiz password" : "Current HireWiz password"}<input className="field-control" type="password" autoComplete={mode === "google" ? "new-password" : "current-password"} minLength={mode === "google" ? 10 : 1} maxLength={128} required value={password} onChange={(event) => setPassword(event.target.value)} disabled={busy} /></label>
        {mode === "google" ? <><p className="text-xs leading-6 text-muted-foreground">Choose 10–128 characters. Your Google password is never requested here.</p>
          <label className="flex items-start gap-3 text-sm"><input type="checkbox" checked={hasPassword} onChange={(event) => { setHasPassword(event.target.checked); setCurrentPassword(""); }} disabled={busy} />I already have a HireWiz password</label>
          {hasPassword ? <label className="grid gap-2 text-sm font-semibold">Existing HireWiz password<input className="field-control" type="password" autoComplete="current-password" required maxLength={128} value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} disabled={busy} /></label> : null}</> : null}
        <label className="flex items-start gap-3 text-sm leading-7"><input className="mt-1.5" type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} disabled={busy} />I want to enroll my existing account for browser approval and use my HireWiz password to approve final submission. Device connections and application actions need separate approval.</label>
        {mode === "google" ? <div className="space-y-3"><p className="text-xs leading-6 text-muted-foreground">Starting verification loads Google Identity Services. Choose the same Google account you use for HireWiz.</p>
          {!googleStarted ? <Button type="button" variant="secondary" disabled={busy || !accepted} onClick={() => void startGoogle()}>Verify my Google account</Button>
            : <><div ref={googleButton} aria-label="Google account verification" />{!googleVerified ? <p className="text-sm text-muted-foreground">Use the Google button to verify your account. If verification expires, reload for a fresh review.</p> : null}
              <Button type="button" variant="ghost" disabled={busy} onClick={() => window.location.reload()}>Reload for fresh verification</Button></>}
          {challenge ? <Script src={GOOGLE_SCRIPT} strategy="afterInteractive" onReady={initializeGoogle} onError={() => { activeChallenge.current = null; setChallenge(null); setError("Google verification could not load. Reload before a fresh verification."); }} /> : null}
        </div> : null}
        <Button type="submit" disabled={busy || !accepted || !password || mode === "google" && (!googleVerified || password.length < 10 || hasPassword && !currentPassword)}>{busy ? "Working…" : "Enroll this account"}</Button>
      </form> : null}
    {message && !enrolled ? <p role="status" className="text-sm leading-7">{message}</p> : null}
    {error ? <p role="alert" className="text-sm leading-7 text-coral">{error}</p> : null}
    {!enrolled && !pending ? <Link href="/account-recovery" className="block text-sm font-semibold text-primary underline">Forgot your HireWiz password?</Link> : null}
    <Link href="/dashboard" className="inline-block text-sm font-semibold text-primary underline underline-offset-4">Return to your workspace</Link>
  </section>;
}
