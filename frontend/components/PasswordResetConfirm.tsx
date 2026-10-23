"use client";

import { ArrowRight, CheckCircle2, KeyRound } from "lucide-react";
import Link from "next/link";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { passwordReset } from "../lib/passwordResetClient";
import { readPasswordResetLink, setPasswordResetOutcome } from "../lib/passwordResetLink";
import { Button } from "./ui/Button";

type Stage = "loading" | "ready" | "invalid" | "complete" | "uncertain";

export default function PasswordResetConfirm() {
  const [stage, setStage] = useState<Stage>("loading");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const token = useRef<string | null>(null);
  const binding = useRef<symbol | null>(null);
  const initialized = useRef(false);
  const active = useRef<AbortController | null>(null);
  useLayoutEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    const retained = readPasswordResetLink();
    token.current = retained.token;
    binding.current = retained.binding;
    setStage(retained.token ? "ready" : retained.outcome === "submitted" ? "uncertain" : retained.outcome ?? "invalid");
  }, []);
  useEffect(() => () => { active.current?.abort(); }, []);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (active.current || stage !== "ready" || !token.current || !binding.current) return;
    if (Array.from(password).length < 10 || Array.from(password).length > 128) {
      setError("Choose a password between 10 and 128 characters."); return;
    }
    if (password !== confirmation) { setError("The passwords do not match. Enter the same new password in both fields."); return; }
    const proof = token.current;
    const owner = binding.current;
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setError(null);
    let posted = false;
    try {
      const result = await passwordReset("confirm", { token: proof, new_password: password },
        controller.signal, () => {
          if (!setPasswordResetOutcome(owner, "submitted")) throw new Error("RESET_NOT_SENT");
          posted = true;
        });
      if (controller.signal.aborted) return;
      if (result === "PASSWORD_RESET") { setPasswordResetOutcome(owner, "complete"); token.current = null; setStage("complete"); }
      else if (result === "RESET_LINK_INVALID") { setPasswordResetOutcome(owner, "invalid"); token.current = null; setStage("invalid"); }
      else if (result === "RATE_LIMITED") setError("Too many attempts. Please wait before trying again.");
      else if (result === "RESET_REQUEST_REJECTED") setError("The request was rejected. Check your password and try again.");
      else { setPasswordResetOutcome(owner, "uncertain"); token.current = null; setStage("uncertain"); }
    } catch {
      if (!controller.signal.aborted) {
        if (posted) { setPasswordResetOutcome(owner, "uncertain"); token.current = null; setStage("uncertain"); }
        else setError("The reset was not sent. Check your connection and try again.");
      }
    } finally {
      active.current = null;
      if (!controller.signal.aborted) { setBusy(false); setPassword(""); setConfirmation(""); }
    }
  }

  return <section className="surface p-6 sm:p-9" aria-labelledby="reset-heading" aria-busy={busy || stage === "loading"}>
    <span className="icon-tile">{stage === "complete" ? <CheckCircle2 size={20} aria-hidden="true" /> : <KeyRound size={20} aria-hidden="true" />}</span>
    <p className="eyebrow mt-6">Secure account recovery</p>
    <h1 id="reset-heading" className="mt-3 font-display text-3xl sm:text-4xl">{stage === "complete" ? "Password updated" : "Choose a new password"}</h1>
    {stage === "loading" ? <p className="mt-5 text-sm text-muted-foreground" role="status">Checking your reset link…</p> : null}
    {stage === "ready" ? <>
      <p className="mt-4 text-sm leading-6 text-muted-foreground">Use a unique password with at least 10 characters. Your saved workspace will stay intact.</p>
      <form onSubmit={submit} className="mt-6 grid gap-5">
        <label htmlFor="new-password" className="grid gap-2 text-sm font-semibold text-foreground">New password
          <input id="new-password" className="field-control" type="password" autoComplete="new-password" required
            value={password} onChange={(event) => setPassword(event.target.value)} maxLength={256} disabled={busy} aria-describedby="password-help" />
        </label>
        <p id="password-help" className="-mt-3 text-xs leading-5 text-muted-foreground">10–128 characters. Avoid a password you use elsewhere.</p>
        <label htmlFor="confirm-password" className="grid gap-2 text-sm font-semibold text-foreground">Confirm new password
          <input id="confirm-password" className="field-control" type="password" autoComplete="new-password" required
            value={confirmation} onChange={(event) => setConfirmation(event.target.value)} maxLength={256} disabled={busy} />
        </label>
        <Button type="submit" className="w-full min-h-11" disabled={busy}>{busy ? "Updating password…" : "Update password"}<ArrowRight size={16} aria-hidden="true" /></Button>
      </form>
    </> : null}
    {stage === "invalid" ? <div className="mt-5 space-y-4" role="alert"><p className="text-sm leading-6 text-muted-foreground">This reset link is invalid, has expired, or has already been used. Request a new link to continue.</p><Button asChild className="min-h-11"><Link href="/forgot-password">Request a new link</Link></Button></div> : null}
    {stage === "complete" ? <div className="mt-5 space-y-4" role="status"><p className="text-sm leading-6 text-muted-foreground">Your new password is ready. Clear your previous sign-in session, then sign in again to continue. Existing sessions can no longer access your workspace.</p><Button asChild className="min-h-11"><Link href="/logout">Continue to sign in<ArrowRight size={16} aria-hidden="true" /></Link></Button></div> : null}
    {stage === "uncertain" ? <div className="mt-5 space-y-4" role="alert"><p className="text-sm leading-6 text-muted-foreground">We could not confirm the reset outcome. Clear your previous sign-in session, then try signing in with your new password. If it does not work, contact support. This page will not send the reset again.</p><Button asChild className="min-h-11"><Link href="/logout">Go to sign in</Link></Button><p><Link href="/contact" className="inline-flex min-h-11 items-center text-sm font-semibold text-primary underline">Contact support</Link></p></div> : null}
    {error ? <p className="mt-5 rounded-xl border border-coral/30 bg-coral/5 p-3 text-sm leading-6 text-coral" role="alert">{error}</p> : null}
  </section>;
}
