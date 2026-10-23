"use client";

import { ArrowLeft, ArrowRight, Mail, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { passwordReset } from "../lib/passwordResetClient";
import { useHydrated } from "../lib/useHydrated";
import { Button } from "./ui/Button";

export default function PasswordResetRequest() {
  const ready = useHydrated();
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ready || active.current || !email.trim()) return;
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setError(null);
    let posted = false;
    try {
      const result = await passwordReset("request", { email: email.trim() }, controller.signal, () => { posted = true; });
      if (controller.signal.aborted) return;
      if (result === "RESET_LINK_REQUESTED") setSent(true);
      else setError(result === "RATE_LIMITED" ? "Too many requests. Please wait before requesting another link."
        : result === "RESET_REQUEST_REJECTED" ? "Check your email address and try again."
        : "We could not confirm the email request. Check your inbox before requesting another link.");
    } catch {
      if (!controller.signal.aborted) setError(posted
        ? "We could not confirm the email request. Check your inbox before requesting another link."
        : "The request was not sent. Check your connection and try again.");
    } finally {
      active.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return <section className="surface p-6 sm:p-9" aria-labelledby="forgot-heading" aria-busy={busy}>
    <span className="icon-tile"><Mail size={20} aria-hidden="true" /></span>
    <p className="eyebrow mt-6">Account access</p>
    <h1 id="forgot-heading" className="mt-3 font-display text-3xl sm:text-4xl">Forgot your password?</h1>
    <p className="mt-4 text-sm leading-6 text-muted-foreground">Enter the email address on your HireWiz account. We’ll send a secure link to choose a new password.</p>
    {sent ? <div className="mt-6 space-y-4" role="status">
      <div className="rounded-xl border border-primary/20 bg-primary/5 p-4 text-sm leading-6">
        <h2 className="font-semibold text-foreground">Check your email</h2>
        <p className="mt-1 text-muted-foreground">If an eligible account uses this address, a reset link will arrive shortly. Check your spam folder too.</p>
      </div>
      <p className="text-sm leading-6 text-muted-foreground">The link expires in 15 minutes and can be used once. Haven’t received it? Check the address below before sending a new request.</p>
    </div> : null}
    <form onSubmit={submit} className="mt-6 grid gap-5">
      <label className="grid gap-2 text-sm font-semibold text-foreground" htmlFor="reset-email">Email address
        <input id="reset-email" className="field-control" type="email" autoComplete="email" inputMode="email"
          value={email} onChange={(event) => { setEmail(event.target.value); setSent(false); setError(null); }}
          required maxLength={320} disabled={!ready || busy} />
      </label>
      <Button type="submit" className="w-full min-h-11" disabled={!ready || busy}>{busy ? "Sending request…" : sent ? "Request another link" : "Send reset link"}<ArrowRight size={16} aria-hidden="true" /></Button>
    </form>
    {error ? <p className="mt-5 rounded-xl border border-coral/30 bg-coral/5 p-3 text-sm leading-6 text-coral" role="alert">{error}</p> : null}
    <p className="mt-6 flex gap-2 text-xs leading-5 text-muted-foreground"><ShieldCheck size={16} className="mt-0.5 shrink-0" aria-hidden="true" />Resetting your password keeps your saved resumes and workspace intact.</p>
    <Link href="/login" className="mt-7 inline-flex min-h-11 items-center gap-2 text-sm font-semibold text-primary underline"><ArrowLeft size={16} aria-hidden="true" />Back to sign in</Link>
  </section>;
}
