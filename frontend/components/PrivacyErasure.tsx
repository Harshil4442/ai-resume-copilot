"use client";

import Link from "next/link";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { privacyErasure } from "../lib/privacyErasureClient";
import { readPrivacyErasureLink, setPrivacyErasureOutcome } from "../lib/privacyErasureLink";
import { useHydrated } from "../lib/useHydrated";
import { Button } from "./ui/Button";

export function PrivacyErasureRequest() {
  const ready = useHydrated();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
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
      const result = await privacyErasure("request", { email: email.trim() }, controller.signal, () => { posted = true; });
      if (controller.signal.aborted) return;
      if (result === "ERASURE_LINK_REQUESTED") setSent(true);
      else setError(result === "RATE_LIMITED" ? "Too many requests. Please wait before requesting another link."
        : result === "ERASURE_REQUEST_REJECTED" ? "Check your email address and try again."
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

  return <section className="surface p-6 sm:p-9" aria-labelledby="erasure-request-heading" aria-busy={busy}>
    <p className="eyebrow">Account privacy</p>
    <h1 id="erasure-request-heading" className="mt-3 font-display text-3xl sm:text-4xl">Finish account deletion</h1>
    <p className="mt-4 text-sm leading-6 text-muted-foreground">If an earlier account deletion was interrupted, request an email link to confirm permanent removal of your saved account data. This does not restore account access.</p>
    {sent ? <div className="mt-6 space-y-3" role="status"><h2 className="font-semibold">Check your email</h2>
      <p className="text-sm leading-6 text-muted-foreground">If this address is eligible, a confirmation link will arrive shortly. Check your spam folder too. The link expires in 10 minutes.</p></div> : null}
    <form onSubmit={submit} className="mt-6 grid gap-5">
      <label htmlFor="erasure-email" className="grid gap-2 text-sm font-semibold">Email address
        <input id="erasure-email" className="field-control" type="email" inputMode="email" autoComplete="email" required
          maxLength={320} value={email} onChange={(event) => { setEmail(event.target.value); setSent(false); setError(null); }} disabled={!ready || busy} />
      </label>
      <Button type="submit" disabled={!ready || busy} className="min-h-11">{busy ? "Sending request…" : sent ? "Request another link" : "Send confirmation link"}</Button>
    </form>
    {error ? <p role="alert" className="mt-5 text-sm leading-6 text-coral">{error}</p> : null}
    <Link href="/contact" className="mt-6 inline-flex min-h-11 items-center text-sm font-semibold text-primary underline">Contact support</Link>
  </section>;
}

type Stage = "loading" | "ready" | "invalid" | "complete" | "uncertain";

export function PrivacyErasureConfirm() {
  const [stage, setStage] = useState<Stage>("loading");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const token = useRef<string | null>(null);
  const binding = useRef<symbol | null>(null);
  const initialized = useRef(false);
  const active = useRef<AbortController | null>(null);
  useLayoutEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    const retained = readPrivacyErasureLink();
    token.current = retained.token; binding.current = retained.binding;
    setStage(retained.outcome === "submitted" || retained.outcome === "uncertain" ? "uncertain"
      : retained.outcome ?? (retained.token ? "ready" : "invalid"));
  }, []);
  useEffect(() => () => active.current?.abort(), []);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (active.current || !confirmed || !["ready", "uncertain"].includes(stage) || !token.current || !binding.current) return;
    const proof = token.current, owner = binding.current;
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setError(null);
    let posted = false;
    try {
      const result = await privacyErasure("confirm", { token: proof, confirm_erasure: true }, controller.signal, () => {
        if (!setPrivacyErasureOutcome(owner, "submitted")) throw new Error("ERASURE_NOT_SENT");
        posted = true;
      });
      if (controller.signal.aborted) return;
      if (result === "ACCOUNT_ERASED" || result === "ERASURE_LINK_INVALID") {
        const outcome = result === "ACCOUNT_ERASED" ? "complete" : "invalid";
        if (!setPrivacyErasureOutcome(owner, outcome)) { setStage("invalid"); return; }
        token.current = null; setStage(outcome);
      } else if (result === "RATE_LIMITED" || result === "ERASURE_REQUEST_REJECTED") {
        setPrivacyErasureOutcome(owner, stage === "uncertain" ? "uncertain" : null);
        setError(result === "RATE_LIMITED" ? "Too many attempts. Please wait before trying again."
          : "The confirmation was rejected. Check this page before trying again.");
      } else { setPrivacyErasureOutcome(owner, "uncertain"); setStage("uncertain"); }
    } catch {
      if (!controller.signal.aborted) {
        if (posted) { setPrivacyErasureOutcome(owner, "uncertain"); setStage("uncertain"); }
        else setError("The confirmation was not sent. Check your connection and try again.");
      }
    } finally {
      active.current = null;
      if (!controller.signal.aborted) { setBusy(false); setConfirmed(false); }
    }
  }

  const canConfirm = stage === "ready" || stage === "uncertain";
  return <section className="surface p-6 sm:p-9" aria-labelledby="erasure-confirm-heading" aria-busy={busy || stage === "loading"}>
    <p className="eyebrow">Account privacy</p>
    <h1 id="erasure-confirm-heading" className="mt-3 font-display text-3xl sm:text-4xl">{stage === "complete" ? "Account data deleted" : "Confirm permanent deletion"}</h1>
    {stage === "loading" ? <p className="mt-5 text-sm" role="status">Checking your confirmation link…</p> : null}
    {stage === "ready" ? <p className="mt-4 text-sm leading-6 text-muted-foreground">This permanently removes your account, saved resumes, evidence and workspace data. This action cannot be undone. Required billing and security records may be retained.</p> : null}
    {stage === "uncertain" ? <p className="mt-5 text-sm leading-6 text-muted-foreground" role="alert">We could not confirm the deletion outcome. Nothing will be sent automatically. You can explicitly check this same request below; if it has not finished, that action may complete the deletion.</p> : null}
    {canConfirm ? <form onSubmit={submit} className="mt-6 grid gap-5">
      <label className="flex items-start gap-3 text-sm leading-6"><input type="checkbox" className="mt-1 h-4 w-4 shrink-0" checked={confirmed}
        onChange={(event) => setConfirmed(event.target.checked)} disabled={busy} required />
        <span>I want to permanently delete this account and its saved data.</span></label>
      <Button type="submit" variant="danger" className="min-h-11" disabled={!confirmed || busy}>{busy ? "Confirming…" : stage === "uncertain" ? "Check or finish this deletion" : "Permanently delete account"}</Button>
    </form> : null}
    {stage === "invalid" ? <div className="mt-5 space-y-4" role="alert"><p className="text-sm leading-6 text-muted-foreground">This confirmation link is invalid or has expired.</p><Button asChild className="min-h-11"><Link href="/privacy-erasure">Request a new link</Link></Button></div> : null}
    {stage === "complete" ? <p className="mt-5 text-sm leading-6 text-muted-foreground" role="status">Your account data has been deleted. Account access remains closed. Required billing and security records may be retained.</p> : null}
    {error ? <p role="alert" className="mt-5 text-sm leading-6 text-coral">{error}</p> : null}
    <Link href="/contact" className="mt-6 inline-flex min-h-11 items-center text-sm font-semibold text-primary underline">Contact support</Link>
  </section>;
}
