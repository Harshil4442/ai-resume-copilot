"use client";

import { Mail } from "lucide-react";
import { useSession } from "next-auth/react";
import { useEffect, useRef, useState } from "react";
import { changeNotificationPreference, readNotificationPreference, type NotificationPreference } from "../lib/notificationPreferencesClient";
import { Button } from "./ui/Button";

function OwnedDigestPreference() {
  const [current, setCurrent] = useState<NotificationPreference | null>(null);
  const [draft, setDraft] = useState(false);
  const [stage, setStage] = useState<"loading" | "ready" | "saving" | "unavailable" | "uncertain">("loading");
  const [saved, setSaved] = useState(false);
  const active = useRef<AbortController | null>(null);

  async function refresh(signal: AbortSignal) {
    try {
      const result = await readNotificationPreference(signal);
      if (signal.aborted) return;
      setCurrent(result); setDraft(result.weekly_digest); setStage("ready");
    } catch { if (!signal.aborted) { setCurrent(null); setStage("unavailable"); } }
  }

  useEffect(() => {
    const controller = new AbortController(); active.current = controller;
    void readNotificationPreference(controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      setCurrent(result); setDraft(result.weekly_digest); setStage("ready");
    }).catch(() => {
      if (!controller.signal.aborted) { setCurrent(null); setStage("unavailable"); }
    }).finally(() => { if (active.current === controller) active.current = null; });
    return () => { controller.abort(); active.current?.abort(); active.current = null; };
  }, []);

  function manualRefresh() {
    if (active.current) return;
    const controller = new AbortController(); active.current = controller;
    setStage("loading"); setSaved(false); setCurrent(null);
    void refresh(controller.signal).finally(() => { if (active.current === controller) active.current = null; });
  }

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (active.current || !current || stage !== "ready" || draft === current.weekly_digest) return;
    const controller = new AbortController(); active.current = controller;
    setStage("saving"); setSaved(false);
    try {
      const result = await changeNotificationPreference(current, draft, controller.signal);
      if (controller.signal.aborted) return;
      setCurrent(result); setDraft(result.weekly_digest); setStage("ready"); setSaved(true);
    } catch {
      if (!controller.signal.aborted) { setCurrent(null); setStage("uncertain"); }
    } finally { if (active.current === controller) active.current = null; }
  }

  return <div aria-busy={stage === "loading" || stage === "saving"}>
    {stage === "loading" ? <p className="mt-5 text-sm text-muted-foreground" role="status">Checking your email preference…</p> : null}
    {current ? <>
      <p className="mt-5 text-sm font-semibold">Weekly emails: {current.weekly_digest ? "On" : "Off"}</p>
      {!current.projection_current ? <p className="mt-2 text-sm leading-6 text-muted-foreground">Your delivery schedule is not current. Review your preference below or refresh its current state.</p> : null}
      <form onSubmit={save} className="mt-4 grid gap-5">
        <label className="flex items-start gap-3 text-sm leading-6">
          <input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-primary" checked={draft}
            disabled={stage !== "ready"} onChange={(event) => { setDraft(event.target.checked); setSaved(false); }} />
          <span>Send me weekly email updates about roles I have already saved or discovered.</span>
        </label>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" className="min-h-11 w-full sm:w-auto" disabled={stage !== "ready" || draft === current.weekly_digest}>
            {stage === "saving" ? "Saving preference…" : draft ? "Enable weekly emails" : "Turn off weekly emails"}
          </Button>
          {saved ? <p role="status" className="text-sm font-semibold text-primary">Email preference saved.</p> : null}
        </div>
      </form>
    </> : null}
    {stage === "unavailable" || stage === "uncertain" ? <div className="mt-5 space-y-4">
      <p role="alert" className="text-sm leading-6 text-muted-foreground">{stage === "uncertain"
        ? "We could not confirm whether your change finished. We will not repeat it automatically. Refresh the current preference before making another change."
        : "Email settings are temporarily unavailable. Your current preference could not be verified."}</p>
      <Button variant="secondary" type="button" className="min-h-11" onClick={manualRefresh}>Refresh current preference</Button>
    </div> : null}
  </div>;
}

export default function WeeklyDigestPreference() {
  const { data: session, status } = useSession();
  const owner = session?.user?.id || session?.user?.email;
  return <section className="surface p-5 sm:p-7" aria-labelledby="weekly-digest-heading">
    <div className="flex items-center gap-3"><span className="icon-tile"><Mail size={18} aria-hidden="true" /></span><h2 id="weekly-digest-heading" className="font-display text-lg font-normal">Weekly role updates</h2></div>
    <p className="mt-4 max-w-2xl text-sm leading-6 text-muted-foreground">An optional email with up to five updates to your saved or previously discovered roles. At most one digest each week; weeks without eligible updates are skipped. Account and security emails are separate. Digest delivery depends on available email capacity. You can review role details in your dashboard.</p>
    {status === "authenticated" && owner ? <OwnedDigestPreference key={owner} />
      : <p className="mt-5 text-sm text-muted-foreground" role="status">{status === "loading" ? "Checking your account…" : "Sign in to review your email preference."}</p>}
  </section>;
}
