"use client";

import Link from "next/link";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { NotificationRequestError, unsubscribeNotifications } from "../lib/notificationPreferencesClient";
import { clearNotificationUnsubscribeLink, readNotificationUnsubscribeLink, setNotificationUnsubscribeOutcome } from "../lib/notificationUnsubscribeLink";
import { Button } from "./ui/Button";

type Stage = "loading" | "ready" | "sending" | "invalid" | "complete" | "uncertain";

export default function NotificationUnsubscribe() {
  const [stage, setStage] = useState<Stage>("loading");
  const proof = useRef<string | null>(null);
  const binding = useRef<symbol | null>(null);
  const initialized = useRef(false);
  const active = useRef<AbortController | null>(null);
  useLayoutEffect(() => {
    function bindLink() {
      const retained = readNotificationUnsubscribeLink();
      proof.current = retained.token; binding.current = retained.binding;
      setStage(retained.outcome === "submitted" ? "uncertain" : retained.outcome ?? (retained.token ? "ready" : "invalid"));
    }
    function changedLink() {
      // A same-path email link is a new confirmation, never an automatic send.
      // Invalidate old in-flight adoption even if its request already reached the server.
      active.current?.abort(); active.current = null;
      proof.current = null; binding.current = null;
      clearNotificationUnsubscribeLink();
      bindLink();
    }
    if (!initialized.current) { initialized.current = true; bindLink(); }
    window.addEventListener("hashchange", changedLink);
    return () => window.removeEventListener("hashchange", changedLink);
  }, []);
  useEffect(() => () => active.current?.abort(), []);

  async function confirm() {
    if (stage !== "ready" || active.current || !proof.current || !binding.current) return;
    const token = proof.current, owner = binding.current;
    if (!setNotificationUnsubscribeOutcome(owner, "submitted")) { setStage("invalid"); return; }
    proof.current = null;
    const controller = new AbortController(); active.current = controller;
    setStage("sending");
    try {
      await unsubscribeNotifications(token, controller.signal);
      if (controller.signal.aborted) return;
      if (setNotificationUnsubscribeOutcome(owner, "complete")) setStage("complete");
    } catch (error) {
      if (controller.signal.aborted) return;
      const next = error instanceof NotificationRequestError && [400, 403, 422].includes(error.status || 0) ? "invalid" : "uncertain";
      if (setNotificationUnsubscribeOutcome(owner, next)) setStage(next);
    } finally { if (active.current === controller) active.current = null; }
  }

  return <section className="surface p-6 sm:p-9" aria-labelledby="unsubscribe-heading" aria-busy={stage === "loading" || stage === "sending"}>
    <p className="eyebrow">Email preferences</p>
    <h1 id="unsubscribe-heading" className="mt-3 font-display text-3xl sm:text-4xl">{stage === "complete" ? "Weekly emails turned off" : "Stop weekly role updates"}</h1>
    {stage === "loading" ? <p className="mt-5 text-sm" role="status">Checking your email link…</p> : null}
    {stage === "ready" || stage === "sending" ? <>
      <p className="mt-4 text-sm leading-6 text-muted-foreground">Use this button to turn off the weekly role digest for the account associated with this email link. You do not need to sign in. Account and security emails continue separately.</p>
      <Button type="button" onClick={confirm} disabled={stage === "sending"} className="mt-6 min-h-11 w-full sm:w-auto">{stage === "sending" ? "Confirming…" : "Stop weekly emails"}</Button>
    </> : null}
    {stage === "complete" ? <p className="mt-5 text-sm leading-6 text-muted-foreground" role="status">Your weekly digest preference is off. An email already being sent may still arrive.</p> : null}
    {stage === "invalid" ? <p className="mt-5 text-sm leading-6 text-muted-foreground" role="alert">This email link is invalid or no longer current. You can manage weekly emails in your Profile, or contact support for help.</p> : null}
    {stage === "uncertain" ? <p className="mt-5 text-sm leading-6 text-muted-foreground" role="alert">We could not confirm the outcome. No second request will be sent automatically. Check the current email preference in your Profile, or contact support for help.</p> : null}
    <div className="mt-6 flex flex-wrap gap-x-6 gap-y-2">
      <Link href="/profile" className="inline-flex min-h-11 items-center text-sm font-semibold text-primary underline">Open Profile</Link>
      <Link href="/contact" className="inline-flex min-h-11 items-center text-sm font-semibold text-primary underline">Contact support</Link>
    </div>
  </section>;
}
