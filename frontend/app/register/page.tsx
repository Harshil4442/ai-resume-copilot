"use client";

import { ArrowRight, FileCheck2, ShieldCheck, Target } from "lucide-react";
import { getProviders, signIn } from "next-auth/react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "../../components/ui/Button";
import { trackEvent } from "../../lib/analytics";
import { prepareGoogleRegistrationConsent, register } from "../../lib/auth";

export default function RegisterPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [googleAvailable, setGoogleAvailable] = useState(false);

  useEffect(() => {
    getProviders()
      .then((providers) => setGoogleAvailable(Boolean(providers?.google)))
      .catch(() => setGoogleAvailable(false));
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
      await register(email, password);
      trackEvent("account_created", { method: "credentials" });
      const result = await signIn("credentials", { email, password, redirect: false });
      if (result?.error) {
        setCreated(true);
        return;
      }
      trackEvent("registration_completed", { method: "credentials" });
      router.push("/resume");
    } catch (registerError) {
      setError(registerError instanceof Error ? registerError.message : "Registration failed");
      trackEvent("registration_failed", { method: "credentials" });
    } finally {
      setLoading(false);
    }
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
        <section className="max-w-xl">
          <p className="eyebrow">Start with your evidence</p>
          <h1 className="mt-3 text-4xl font-black leading-tight text-neutral-100 sm:text-5xl">Build a role-specific application without inventing your story.</h1>
          <div className="mt-8 divide-y divide-white/10 border-y border-white/10">
            <div className="flex gap-4 py-4"><FileCheck2 size={20} className="shrink-0 text-primary" /><div><p className="font-bold text-neutral-200">Approve facts once</p><p className="mt-1 text-sm text-neutral-500">Keep reusable evidence under your control.</p></div></div>
            <div className="flex gap-4 py-4"><Target size={20} className="shrink-0 text-accent" /><div><p className="font-bold text-neutral-200">Connect every target role</p><p className="mt-1 text-sm text-neutral-500">Preserve the job, resume version, next action, and outcome.</p></div></div>
          </div>
        </section>

        <section className="surface p-6 sm:p-8" aria-labelledby="register-heading">
          <p className="data-label">HireWiz account</p>
          <h2 id="register-heading" className="mt-1 text-2xl font-black text-neutral-100">Create your account</h2>
          <form onSubmit={onSubmit} className="mt-7 grid gap-5">
            <label className="grid gap-2 text-sm font-semibold text-neutral-300">
              Email address
              <input className="field-control" type="email" value={email} onChange={(event) => setEmail(event.target.value)} required autoComplete="email" />
            </label>
            <label className="grid gap-2 text-sm font-semibold text-neutral-300">
              Password
              <input className="field-control" type="password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={10} maxLength={128} required autoComplete="new-password" placeholder="At least 10 characters" />
            </label>
            <label className="flex cursor-pointer items-start gap-3 text-xs leading-5 text-neutral-500">
              <input type="checkbox" checked={agreed} onChange={(event) => setAgreed(event.target.checked)} className="mt-0.5 h-4 w-4 accent-primary" />
              <span>I am at least 18 and agree to the <Link href="/terms" className="text-neutral-300 underline">Terms</Link> and acknowledge the <Link href="/privacy" className="text-neutral-300 underline">Privacy Policy</Link>.</span>
            </label>
            <Button type="submit" className="w-full" disabled={loading || !agreed}>{loading ? "Creating account..." : "Create account"} <ArrowRight size={16} /></Button>
          </form>

          {googleAvailable ? (
            <div className="mt-6 border-t border-white/10 pt-6">
              <Button type="button" variant="secondary" className="w-full" onClick={startGoogleRegistration} disabled={!agreed || loading}>
                <span className="font-black" aria-hidden="true">G</span> Continue with Google
              </Button>
            </div>
          ) : null}
          {created ? <p className="mt-5 border border-primary/25 bg-primary/5 p-3 text-sm text-[#69debd]">Account created. <Link href="/login" className="font-bold underline">Sign in to continue</Link>.</p> : null}
          {error ? <div className="mt-5 flex gap-2 border border-coral/30 bg-coral/5 p-3 text-sm text-[#ffab9e]" role="alert"><ShieldCheck size={17} className="shrink-0" /> {error}</div> : null}
          <p className="mt-7 text-center text-sm text-neutral-500">Already registered? <Link href="/login" className="font-bold text-primary hover:underline">Sign in</Link></p>
        </section>
      </div>
    </main>
  );
}
