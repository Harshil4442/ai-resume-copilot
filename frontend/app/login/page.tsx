"use client";

import { ArrowRight, CheckCircle2, LogIn, ShieldCheck } from "lucide-react";
import { getProviders, signIn, useSession } from "next-auth/react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "../../components/ui/Button";
import { trackEvent } from "../../lib/analytics";
import { prepareGoogleRegistrationConsent } from "../../lib/auth";
import { candidateRegistration, postLoginPath } from "../../lib/candidateAuthClient";
import { useHydrated } from "../../lib/useHydrated";

export default function LoginPage() {
  const router = useRouter();
  const hydrated = useHydrated();
  const { status: sessionStatus } = useSession();
  const ready = hydrated && sessionStatus !== "loading";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [googleAgreed, setGoogleAgreed] = useState(false);
  const [googleAvailable, setGoogleAvailable] = useState(false);

  useEffect(() => {
    getProviders()
      .then((providers) => setGoogleAvailable(Boolean(providers?.google)))
      .catch(() => setGoogleAvailable(false));
  }, []);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const result = await signIn("credentials", { email, password, redirect: false });
      if (!result?.ok || result.error) {
        setError(result?.error === "SESSION_LOGOUT_REQUIRED" ? "Sign out of the current account before switching accounts."
          : result?.error === "AUTH_UNAVAILABLE" ? "Secure sign-in is unavailable. Retry later; your saved workspace is unchanged."
          : "Could not sign in. Check your credentials, or check a pending secure registration below.");
        trackEvent("login_failed", { method: "credentials" });
        return;
      }
      trackEvent("login_succeeded", { method: "credentials" });
      router.push(postLoginPath("/dashboard"));
    } catch {
      setError("Sign-in is unavailable. Retry later.");
      trackEvent("login_failed", { method: "credentials" });
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
        ? "Secure registration is pending review. No session has been issued. Contact support for recovery; checking status cannot activate it."
        : status === "LEGACY_ENROLLMENT_UNAVAILABLE"
        ? "Your existing account can use ordinary sign-in. Browser companion enrollment for existing accounts is not available yet."
        : "Secure registration is complete. Retry password sign-in.");
    } catch { setError("Registration status could not be verified. Check your credentials or retry later."); }
    finally { setPassword(""); setLoading(false); }
  }

  async function startGoogleLogin() {
    if (!googleAgreed) return;
    setError(null);
    setLoading(true);
    try {
      await prepareGoogleRegistrationConsent();
      trackEvent("oauth_started", { method: "google", surface: "login" });
      await signIn("google", { callbackUrl: "/dashboard" });
    } catch (googleError) {
      setError(googleError instanceof Error ? googleError.message : "Could not start Google sign-in.");
      setLoading(false);
    }
  }

  return (
    <main className="app-page flex min-h-[calc(100vh-4rem)] items-center">
      <div className="page-container grid gap-10 lg:grid-cols-[1fr_440px] lg:items-center">
        <section className="max-w-xl py-4 lg:py-12">
          <p className="eyebrow">HireWiz Career Workspace</p>
          <h1 className="font-display mt-3 text-5xl font-normal leading-[1.05] text-foreground sm:text-6xl">Continue your job search with the full record intact.</h1>
          <div className="mt-8 grid gap-4 border-t border-border pt-6 text-sm text-muted-foreground sm:grid-cols-3">
            <span className="flex gap-2"><CheckCircle2 size={17} className="shrink-0 text-primary" /> Approved evidence</span>
            <span className="flex gap-2"><CheckCircle2 size={17} className="shrink-0 text-primary" /> Exact resume versions</span>
            <span className="flex gap-2"><CheckCircle2 size={17} className="shrink-0 text-primary" /> Outcome history</span>
          </div>
        </section>

        <section className="surface p-7 sm:p-10" aria-labelledby="login-heading" data-auth-ready={ready} aria-busy={!ready}>
          <div className="flex items-center gap-3">
            <span className="icon-tile"><LogIn size={19} /></span>
            <div><p className="data-label">Account access</p><h2 id="login-heading" className="font-display mt-1 text-2xl font-normal text-foreground">Sign in</h2></div>
          </div>

          <form onSubmit={onSubmit} className="mt-7 grid gap-5">
            <label className="grid gap-2 text-sm font-semibold text-foreground">
              Email address
              <input disabled={!ready || loading} className="field-control" type="email" value={email} onChange={(event) => setEmail(event.target.value)} required autoComplete="email" />
            </label>
            <label className="grid gap-2 text-sm font-semibold text-foreground">
              Password
              <input disabled={!ready || loading} className="field-control" type="password" value={password} onChange={(event) => setPassword(event.target.value)} maxLength={128} required autoComplete="current-password" />
            </label>
            <Button type="submit" className="w-full" disabled={!ready || loading}>
              {loading ? "Signing in..." : "Sign in"} <ArrowRight size={16} />
            </Button>
            <Button type="button" variant="secondary" disabled={!ready || loading || !email || !password} onClick={() => void checkRegistration()}>Check secure registration status</Button>
          </form>

          {googleAvailable ? (
            <div className="mt-6 border-t border-border pt-6">
              <label className="flex cursor-pointer items-start gap-3 text-xs leading-5 text-muted-foreground">
                <input disabled={!ready || loading} type="checkbox" checked={googleAgreed} onChange={(event) => setGoogleAgreed(event.target.checked)} className="mt-0.5 h-4 w-4 accent-primary" />
                <span>If Google creates a new account, I confirm I am at least 18 and agree to the <Link href="/terms" className="text-foreground underline">Terms</Link> and <Link href="/privacy" className="text-foreground underline">Privacy Policy</Link>.</span>
              </label>
              <Button type="button" variant="secondary" className="mt-4 w-full" onClick={startGoogleLogin} disabled={!ready || !googleAgreed || loading}>
                <span className="font-semibold" aria-hidden="true">G</span> Continue with Google
              </Button>
            </div>
          ) : null}

          {error ? <div className="mt-5 flex gap-2 border border-coral/30 bg-coral/5 p-3 text-sm text-coral" role="alert"><ShieldCheck size={17} className="shrink-0" /> {error}</div> : null}
          <p className="mt-5 text-xs text-muted-foreground">Switching accounts? <Link href="/logout" className="text-primary underline">Sign out securely first</Link>.</p>
          <p className="mt-7 text-center text-sm text-muted-foreground">New to HireWiz? <Link href="/register" className="font-bold text-primary hover:underline">Create an account</Link></p>
        </section>
      </div>
    </main>
  );
}
