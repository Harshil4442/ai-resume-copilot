"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiGet, apiPostJson } from "../../lib/api";
import { enrollmentPath, parseCanaryEnrollment, type CanaryChoice, type OwnedCanaryEnrollment } from "../../lib/canaryParticipation";
import { creditAvailabilityKey } from "../../lib/unifiedCreditClient";
import { formatCreditDate } from "../../lib/unifiedCredits";
import { Button } from "../ui/Button";

const key = ["unified-credits", "canary-enrollment"] as const;
async function read(review?: OwnedCanaryEnrollment, signal?: AbortSignal) {
  return parseCanaryEnrollment(await apiGet<unknown>(enrollmentPath(review), signal));
}

function OptIn({ review, busy, onChoose }: { review: OwnedCanaryEnrollment; busy: boolean; onChoose: (choice: "opt_in" | "revoke") => void }) {
  const [checked, setChecked] = useState(false), [confirming, setConfirming] = useState(false);
  const form = useRef<HTMLDivElement>(null), confirmation = useRef<HTMLDivElement>(null), returnFocus = useRef(false);
  useEffect(() => {
    if (confirming) confirmation.current?.focus();
    else if (returnFocus.current) { form.current?.querySelector("button")?.focus(); returnFocus.current = false; }
  }, [confirming]);
  return <div ref={form} className="mt-5 space-y-4">
    <label className="flex items-start gap-3 text-sm leading-6">
      <input type="checkbox" className="mt-1 size-4 shrink-0 accent-primary" checked={checked} disabled={busy}
        onChange={(event) => { setChecked(event.target.checked); setConfirming(false); }} />
      <span>I have read the participation notice and want to opt in.</span>
    </label>
    {confirming ? <div ref={confirmation} tabIndex={-1} role="group" aria-label="Confirm voluntary participation" className="rounded-xl border border-border bg-surface p-4 text-sm leading-6">
      <p>Confirm your choice for this notice. You can stop participating later. Each service still requires its own price review; joining does not reserve capacity or add credits.</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button disabled={busy || !checked || !review.can_opt_in} onClick={() => { setChecked(false); setConfirming(false); onChoose("opt_in"); }}>Confirm participation</Button>
        <Button variant="ghost" disabled={busy} onClick={() => { returnFocus.current = true; setConfirming(false); }}>Cancel</Button>
      </div>
    </div> : <Button variant="secondary" disabled={busy || !checked || !review.can_opt_in} onClick={() => setConfirming(true)}>Review participation</Button>}
  </div>;
}

export function CanaryParticipation({ onChange }: { onChange?: () => void }) {
  const client = useQueryClient();
  const status = useQuery({ queryKey: key, queryFn: ({ signal }) => read(undefined, signal),
    retry: false, staleTime: 0, gcTime: 0, refetchOnWindowFocus: false });
  const [busy, setBusy] = useState(false), [unverified, setUnverified] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [unknownOptIn, setUnknownOptIn] = useState<{ digest: string; sequence: number } | null>(null);
  const pending = useRef(false), retained = useRef<OwnedCanaryEnrollment | undefined>(undefined);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const review = status.data && status.data.status !== "NOT_AVAILABLE" ? status.data : undefined;
  const pendingOptIn = !!review && unknownOptIn?.digest === review.review_sha256 && unknownOptIn.sequence === review.sequence;

  async function confirmStatus(previous: OwnedCanaryEnrollment) {
    const fresh = await read(previous);
    if (fresh.status === "NOT_AVAILABLE" || fresh.review_id !== previous.review_id || fresh.review_sha256 !== previous.review_sha256) {
      throw new Error("Participation status could not be verified.");
    }
    if (!mounted.current) return fresh;
    client.setQueryData(key, fresh);
    retained.current = undefined;
    setUnverified(false);
    await client.invalidateQueries({ queryKey: creditAvailabilityKey });
    if (mounted.current) onChange?.();
    return fresh;
  }
  async function choose(action: "opt_in" | "revoke") {
    if (pending.current || !review || (action === "opt_in" && (!review.can_opt_in || unverified))) return;
    pending.current = true; setBusy(true); setMessage(null);
    retained.current = review;
    let acknowledged = false, rejected = false;
    try {
      await client.cancelQueries({ queryKey: key });
      if (!mounted.current) return;
      const payload: CanaryChoice = { review_id: review.review_id, review_sha256: review.review_sha256,
        notice_sha256: review.notice_sha256, choice: action, expected_sequence: review.sequence, confirmed_notice: action === "opt_in", idempotency_key: crypto.randomUUID() };
      try { await apiPostJson<unknown>(enrollmentPath(), payload); acknowledged = true; }
      catch (error) { rejected = error instanceof ApiError && error.status === 409; }
      if (!mounted.current) return;
      if (action === "opt_in" && !acknowledged && !rejected) {
        setUnknownOptIn({ digest: review.review_sha256, sequence: review.sequence });
      }
      const fresh = await confirmStatus(review);
      if (!mounted.current) return;
      setMessage(rejected ? "This choice was not accepted. Review the refreshed status before trying again." : acknowledged ? (fresh.status === "OPTED_IN" ? "Your current choice is opted in." : "Your current choice is not opted in.")
        : "The request response was unavailable. A delayed choice may still complete; check status or stop participation.");
    } catch {
      if (!mounted.current) return;
      setUnverified(true);
      setMessage("Your current choice could not be confirmed. Check its status before sending another request.");
    } finally { pending.current = false; if (mounted.current) setBusy(false); }
  }
  async function recheck() {
    if (pending.current || !retained.current) return;
    pending.current = true; setBusy(true);
    try { await confirmStatus(retained.current); if (mounted.current) setMessage("Your current recorded choice has been refreshed."); }
    catch { if (mounted.current) setMessage("Participation status is still unavailable. No new request was sent."); }
    finally { pending.current = false; if (mounted.current) setBusy(false); }
  }

  // No invitation/retained participation means no promotion or controls. Owner
  // cache/drafts are remounted by SessionProviderWrapper on account changes.
  if (!review) return null;
  return <section aria-labelledby="canary-participation-title" className="rounded-2xl border border-border p-5 sm:p-6">
    <p className="eyebrow">Optional early access</p>
    <h2 id="canary-participation-title" className="mt-2 font-display text-2xl">Reviewed service pilot</h2>
    <p className="mt-2 text-sm leading-6 text-muted-foreground">Choose whether to participate in this limited pilot. Your decision is separate from purchases and individual service approvals.</p>
    <div className="mt-4 whitespace-pre-wrap break-words rounded-xl bg-surface p-4 text-sm leading-6" aria-label="Participation notice">{review.notice}</div>
    <p className="mt-3 text-xs text-muted-foreground">Review expires {formatCreditDate(review.expires_at)}.</p>
    <p className="mt-3 text-sm font-medium">{unverified || status.isError || pendingOptIn ? "Current participation is unconfirmed" : review.status === "OPTED_IN" ? "Current choice: opted in" : "Current choice: not opted in"}</p>
    {!review.execution_flag_enabled ? <p className="mt-2 text-sm text-muted-foreground">New pilot services are currently paused. You can still stop recorded participation.</p> : null}
    {message ? <p role="status" className="mt-3 text-sm leading-6">{message}</p> : null}
    {unverified ? <div className="mt-4 flex flex-wrap gap-2"><Button variant="secondary" disabled={busy} onClick={() => void recheck()}>Check participation status</Button>
        <Button variant="secondary" disabled={busy} onClick={() => void choose("revoke")}>Stop participating</Button></div>
      : pendingOptIn ? <div className="mt-4"><p className="mb-3 text-sm">An earlier opt-in may still be pending. Stop participation to prevent it from taking effect after your newer choice.</p><Button variant="secondary" disabled={busy} onClick={() => void choose("revoke")}>Stop participating</Button></div>
      : review.status === "OPTED_IN" ? <div className="mt-4"><Button variant="secondary" disabled={busy} onClick={() => void choose("revoke")}>Stop participating</Button><p className="mt-2 text-xs leading-5 text-muted-foreground">Stopping affects new participation for this review. Accepted work, saved results and refunds remain available.</p></div>
      : review.can_opt_in && !status.isError ? <OptIn key={review.review_sha256 + review.status + review.sequence} review={review} busy={busy} onChoose={(action) => void choose(action)} />
      : <p className="mt-3 text-sm text-muted-foreground">This review is not accepting new opt-ins.</p>}
    {!unverified ? <Button variant="ghost" className="mt-3" disabled={busy || status.isFetching} onClick={() => { setMessage(null); void status.refetch(); }}>Refresh participation status</Button> : null}
  </section>;
}
