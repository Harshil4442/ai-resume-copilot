"use client";

import { ArrowRight, CheckCircle2, LogIn, ShieldCheck } from "lucide-react";
import { getProviders, signIn } from "next-auth/react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "../../components/ui/Button";
import { trackEvent } from "../../lib/analytics";
import { prepareGoogleRegistrationConsent } from "../../lib/auth";

export default function LoginPage() {
  const router = useRouter();
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
      if (result?.error) {
        setError("Invalid email or password");
        trackEvent("login_failed", { method: "credentials" });
        return;
      }
      trackEvent("login_succeeded", { method: "credentials" });
      router.push("/dashboard");
    } catch (loginError) {
      setError(loginError instanceof Error ? loginError.message : "Login failed");
      trackEvent("login_failed", { method: "credentials" });
    } finally {
      setLoading(false);
    }
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
        <section className="max-w-xl">
          <p className="eyebrow">HireWiz Career Workspace</p>
          <h1 className="mt-3 text-4xl font-black leading-tight text-neutral-100 sm:text-5xl">Continue your job search with the full record intact.</h1>
          <div className="mt-8 grid gap-4 border-t border-white/10 pt-6 text-sm text-neutral-400 sm:grid-cols-3">
            <span className="flex gap-2"><CheckCircle2 size={17} className="shrink-0 text-primary" /> Approved evidence</span>
            <span className="flex gap-2"><CheckCircle2 size={17} className="shrink-0 text-primary" /> Exact resume versions</span>
            <span className="flex gap-2"><CheckCircle2 size={17} className="shrink-0 text-primary" /> Outcome history</span>
          </div>
        </section>

        <section className="surface p-6 sm:p-8" aria-labelledby="login-heading">
          <div className="flex items-center gap-3">
            <span className="icon-tile"><LogIn size={19} /></span>
            <div><p className="data-label">Account access</p><h2 id="login-heading" className="mt-1 text-2xl font-black text-neutral-100">Sign in</h2></div>
          </div>

          <form onSubmit={onSubmit} className="mt-7 grid gap-5">
            <label className="grid gap-2 text-sm font-semibold text-neutral-300">
              Email address
              <input className="field-control" type="email" value={email} onChange={(event) => setEmail(event.target.value)} required autoComplete="email" />
            </label>
            <label className="grid gap-2 text-sm font-semibold text-neutral-300">
              Password
              <input className="field-control" type="password" value={password} onChange={(event) => setPassword(event.target.value)} required autoComplete="current-password" />
            </label>
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? "Signing in..." : "Sign in"} <ArrowRight size={16} />
            </Button>
          </form>

          {googleAvailable ? (
            <div className="mt-6 border-t border-white/10 pt-6">
              <label className="flex cursor-pointer items-start gap-3 text-xs leading-5 text-neutral-500">
                <input type="checkbox" checked={googleAgreed} onChange={(event) => setGoogleAgreed(event.target.checked)} className="mt-0.5 h-4 w-4 accent-primary" />
                <span>If Google creates a new account, I confirm I am at least 18 and agree to the <Link href="/terms" className="text-neutral-300 underline">Terms</Link> and <Link href="/privacy" className="text-neutral-300 underline">Privacy Policy</Link>.</span>
              </label>
              <Button type="button" variant="secondary" className="mt-4 w-full" onClick={startGoogleLogin} disabled={!googleAgreed || loading}>
                <span className="font-black" aria-hidden="true">G</span> Continue with Google
              </Button>
            </div>
          ) : null}

          {error ? <div className="mt-5 flex gap-2 border border-coral/30 bg-coral/5 p-3 text-sm text-[#ffab9e]" role="alert"><ShieldCheck size={17} className="shrink-0" /> {error}</div> : null}
          <p className="mt-7 text-center text-sm text-neutral-500">New to HireWiz? <Link href="/register" className="font-bold text-primary hover:underline">Create an account</Link></p>
        </section>
      </div>
    </main>
  );
}
