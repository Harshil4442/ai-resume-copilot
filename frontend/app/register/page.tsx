"use client";

import { ArrowRight, FileCheck2, ShieldCheck, Target } from "lucide-react";
import { getProviders, signIn, useSession } from "next-auth/react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "../../components/ui/Button";
import { trackEvent } from "../../lib/analytics";
import { prepareGoogleRegistrationConsent, register } from "../../lib/auth";
import { candidateRegistration, postLoginPath } from "../../lib/candidateAuthClient";
import { useHydrated } from "../../lib/useHydrated";

export default function RegisterPage() {
  const router = useRouter();
  const hydrated = useHydrated();
  const { status: sessionStatus } = useSession();
  const ready = hydrated && sessionStatus !== "loading";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [googleAvailable, setGoogleAvailable] = useState(false);
  const [nativeAvailable, setNativeAvailable] = useState(false);
  const [nativeSignup, setNativeSignup] = useState(false);
  const [checkPending, setCheckPending] = useState(false);

  useEffect(() => {
    getProviders()
      .then((providers) => setGoogleAvailable(Boolean(providers?.google)))
      .catch(() => setGoogleAvailable(false));
    const controller = new AbortController();
    fetch("/api/candidate-account/availability", { cache: "no-store", signal: controller.signal })
      .then((response) => response.json())
      .then((value) => { if (!controller.signal.aborted) setNativeAvailable(value.fresh_registration === true); })
      .catch(() => { if (!controller.signal.aborted) setNativeAvailable(false); });
    return () => controller.abort();
  }, []);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!agreed) {
      setError("Agree to the Terms and Privacy Policy to continue.");
      return;
    }
    setError(null);
    setLoading(true);
    try {
      if (nativeSignup) await candidateRegistration("register", email, password);
      else await register(email, password);
      trackEvent("account_created", { method: "credentials" });
      const result = await signIn("credentials", { email, password, redirect: false });
      if (!result?.ok || result.error) {
        setCreated(true);
        return;
      }
      trackEvent("registration_completed", { method: "credentials" });
      router.push(postLoginPath("/resume"));
    } catch (registerError) {
      setError(nativeSignup ? "Secure registration could not be confirmed. Check its status before creating another account."
        : registerError instanceof Error ? registerError.message : "Registration failed");
      if (nativeSignup) setCheckPending(true);
      trackEvent("registration_failed", { method: "credentials" });
    } finally {
      setLoading(false);
    }
  }

  async function checkRegistration() {
    if (!email || !password || loading) return;
    setLoading(true); setError(null);
    try {
      const status = await candidateRegistration("registration-status", email, password);
      setError(status === "PENDING_REVIEW_REQUIRED"
        ? "Registration is pending review. Sign-in and browser connection remain blocked. Checking status will not activate it; contact support for recovery."
        : status === "LEGACY_ENROLLMENT_UNAVAILABLE"
        ? "Your existing account can sign in normally. Browser enrollment for existing accounts is not available yet."
        : "Secure registration is complete. Sign in with your password to continue.");
      if (status === "ENROLLED") setCreated(true);
    } catch { setError("Registration status could not be verified. Check your credentials or retry later."); }
    finally { setPassword(""); setLoading(false); }
  }

  async function startGoogleRegistration() {
    if (!agreed) return;
    setError(null);
    setLoading(true);
    try {
      await prepareGoogleRegistrationConsent();
      trackEvent("oauth_started", { method: "google", surface: "register" });
      await signIn("google", { callbackUrl: "/resume" });
    } catch (googleError) {
      setError(googleError instanceof Error ? googleError.message : "Could not start Google sign-in.");
      setLoading(false);
    }
  }

  return (
    <main className="app-page flex min-h-[calc(100vh-4rem)] items-center">
      <div className="page-container grid gap-10 lg:grid-cols-[1fr_440px] lg:items-center">
        <section className="max-w-xl py-4 lg:py-12">
          <p className="eyebrow">Start with your evidence</p>
          <h1 className="font-display mt-3 text-5xl font-normal leading-[1.05] text-foreground sm:text-6xl">Build a role-specific application without inventing your story.</h1>
          <div className="mt-8 divide-y divide-border border-y border-border">
            <div className="flex gap-4 py-4"><FileCheck2 size={20} className="shrink-0 text-primary" /><div><p className="font-bold text-foreground">Approve facts once</p><p className="mt-1 text-sm text-muted-foreground">Keep reusable evidence under your control.</p></div></div>
            <div className="flex gap-4 py-4"><Target size={20} className="shrink-0 text-primary" /><div><p className="font-bold text-foreground">Connect every target role</p><p className="mt-1 text-sm text-muted-foreground">Preserve the job, resume version, next action, and outcome.</p></div></div>
          </div>
        </section>

        <section className="surface p-7 sm:p-10" aria-labelledby="register-heading" data-auth-ready={ready} aria-busy={!ready}>
          <p className="data-label">HireWiz account</p>
          <h2 id="register-heading" className="font-display mt-1 text-2xl font-normal text-foreground">Create your account</h2>
          <form onSubmit={onSubmit} className="mt-7 grid gap-5">
            <label className="grid gap-2 text-sm font-semibold text-foreground">
              Email address
              <input disabled={!ready || loading} className="field-control" type="email" value={email} onChange={(event) => setEmail(event.target.value)} required autoComplete="email" />
            </label>
            <label className="grid gap-2 text-sm font-semibold text-foreground">
              Password
              <input disabled={!ready || loading} className="field-control" type="password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={10} maxLength={128} required autoComplete="new-password" placeholder="At least 10 characters" />
            </label>
            <label className="flex cursor-pointer items-start gap-3 text-xs leading-5 text-muted-foreground">
              <input disabled={!ready || loading} type="checkbox" checked={agreed} onChange={(event) => setAgreed(event.target.checked)} className="mt-0.5 h-4 w-4 accent-primary" />
              <span>I am at least 18 and agree to the <Link href="/terms" className="text-foreground underline">Terms</Link> and acknowledge the <Link href="/privacy" className="text-foreground underline">Privacy Policy</Link>.</span>
            </label>
            {nativeAvailable ? <label className="flex items-start gap-3 text-xs leading-5 text-muted-foreground">
              <input disabled={!ready || loading} type="checkbox" checked={nativeSignup} onChange={(event) => setNativeSignup(event.target.checked)} className="mt-0.5" />
              <span>Create a password account eligible for the browser companion. You will review each device connection separately.</span>
            </label> : <p className="text-xs leading-5 text-muted-foreground">Browser companion enrollment is not available right now. You can still create an account and use your workspace.</p>}
            <Button type="submit" className="w-full" disabled={!ready || loading || !agreed}>{loading ? "Creating account..." : "Create account"} <ArrowRight size={16} /></Button>
            {checkPending ? <Button type="button" variant="secondary" disabled={!ready || loading || !email || !password} onClick={() => void checkRegistration()}>Check registration status</Button> : null}
          </form>

          {googleAvailable && !nativeSignup ? (
            <div className="mt-6 border-t border-border pt-6">
              <Button type="button" variant="secondary" className="w-full" onClick={startGoogleRegistration} disabled={!ready || !agreed || loading}>
                <span className="font-semibold" aria-hidden="true">G</span> Continue with Google
              </Button>
            </div>
          ) : null}
          {created ? <p className="mt-5 border border-primary/25 bg-primary/5 p-3 text-sm text-primary">Account created. <Link href="/login" className="font-bold underline">Sign in to continue</Link>.</p> : null}
          {error ? <div className="mt-5 flex gap-2 border border-coral/30 bg-coral/5 p-3 text-sm text-coral" role="alert"><ShieldCheck size={17} className="shrink-0" /> {error}</div> : null}
          <p className="mt-7 text-center text-sm text-muted-foreground">Already registered? <Link href="/login" className="font-bold text-primary hover:underline">Sign in</Link></p>
        </section>
      </div>
    </main>
  );
}
