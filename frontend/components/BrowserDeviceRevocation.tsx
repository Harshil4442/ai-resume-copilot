"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Button } from "./ui/Button";

type Review = { csrf: string; challenge_id: string; nonce: string; device_id: string; expires_at_ms: number };
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;

export default function BrowserDeviceRevocation({ deviceId }: { deviceId: string | null }) {
  const [review, setReview] = useState<Review | null>(null);
  const [password, setPassword] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [unknown, setUnknown] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  const valid = Boolean(deviceId && UUID.test(deviceId));
  async function post(operation: string, body: object, controller: AbortController, csrf?: string) {
    const response = await fetch(`/api/browser-pairing/candidate/${operation}`, { method: "POST", redirect: "error", cache: "no-store",
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
      headers: { "Content-Type": "application/json", ...(csrf ? { "X-Hirewiz-CSRF": csrf } : {}) }, body: JSON.stringify(body) });
    if (!response.ok) throw new Error("Disconnection could not be verified. Sign in and review this exact device again.");
    return response.json();
  }
  async function load() {
    if (!valid || active.current || unknown || done) return;
    const controller = new AbortController(); active.current = controller; setBusy(true); setError(null);
    try {
      const csrf = await post("csrf", {}, controller);
      const challenge = await post("revoke-challenge", { device_id: deviceId }, controller, csrf.csrf_token);
      if (Object.keys(challenge).sort().join(",") !== "challenge_id,device_id,expires_at_ms,nonce,operation"
          || !UUID.test(challenge.challenge_id) || !UUID.test(challenge.nonce) || challenge.device_id !== deviceId
          || challenge.operation !== "revoke_device" || !Number.isSafeInteger(challenge.expires_at_ms)
          || challenge.expires_at_ms <= Date.now() || !/^[a-f0-9]{64}$/.test(csrf.csrf_token)) throw new Error();
      if (!controller.signal.aborted) setReview({ ...challenge, csrf: csrf.csrf_token });
    } catch { if (!controller.signal.aborted) setError("Disconnection review is unavailable. Sign in to this account and return to this exact device."); }
    finally { active.current = null; if (!controller.signal.aborted) setBusy(false); }
  }
  async function revoke(event: React.FormEvent) {
    event.preventDefault();
    if (!review || !accepted || !password || active.current || done || unknown) return;
    if (review.expires_at_ms <= Date.now()) { setReview(null); setPassword(""); setAccepted(false); setError("This review expired. Start a fresh disconnection review."); return; }
    const controller = new AbortController(); active.current = controller; setBusy(true); setError(null);
    try {
      const result = await post("revoke", { protocol_version: 2, operation: "revoke_device", method: "password_reauth",
        challenge_id: review.challenge_id, nonce: review.nonce, password, confirmed: true }, controller, review.csrf);
      if (Object.keys(result).sort().join(",") !== "device_id,event_sequence,status" || result.status !== "REVOKED"
          || result.device_id !== deviceId || !Number.isSafeInteger(result.event_sequence) || result.event_sequence <= 0) throw new Error();
      if (!controller.signal.aborted) setDone(true);
    } catch {
      if (!controller.signal.aborted) { setUnknown(true); setError("The disconnection outcome is uncertain. Future actions may already be denied. Do not resend this request; contact support to reconcile the device."); }
    } finally { setPassword(""); setAccepted(false); setReview(null); active.current = null; if (!controller.signal.aborted) setBusy(false); }
  }
  return <section className="surface space-y-5 p-5 sm:p-8" aria-busy={busy}>
    <h1 className="font-display text-3xl">Disconnect this browser</h1>
    <p className="text-sm leading-7 text-muted-foreground">Disconnecting stops future actions and cancels unused approvals. A previously authorized action may finish within its ten-second lease. A possible submission still needs reconciliation.</p>
    {!valid ? <p role="alert">Open this review from the connected browser companion.</p> : <p className="break-all text-xs">Device ID: {deviceId}</p>}
    {done ? <p role="status">This browser is disconnected. Future application actions are denied.</p> : review ? <form className="space-y-4" onSubmit={revoke}>
      <label className="flex items-start gap-3 text-sm"><input type="checkbox" checked={accepted} disabled={busy} onChange={event => setAccepted(event.target.checked)} />I want to disconnect this exact browser and stop its future application actions.</label>
      <label className="grid gap-2 text-sm font-semibold">HireWiz password<input className="field-control" type="password" autoComplete="current-password" maxLength={1024} value={password} disabled={busy} onChange={event => setPassword(event.target.value)} /></label>
      <Button type="submit" disabled={busy || !accepted || !password}>Disconnect this browser</Button>
    </form> : <Button disabled={!valid || busy || unknown} onClick={() => void load()}>Review disconnection</Button>}
    {error ? <p role="alert" className="text-sm leading-7 text-coral">{error}</p> : null}
    <Link className="text-sm font-semibold text-primary underline" href="/dashboard">Return to your workspace</Link>
  </section>;
}
