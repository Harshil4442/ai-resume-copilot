"use client";

import Link from "next/link";
import { signIn } from "next-auth/react";
import { useEffect, useRef, useState } from "react";
import { accountMutationHeaders } from "../lib/accountTransportClient";
import { apiDownload } from "../lib/api";
import { Button } from "./ui/Button";

type Device = { id: string; state: string; key_sha256: string; extension_id: string; expires_at_ms: number };
type Payload = {
  application_id: string; target_url: string; package_digest: string;
  artifact: { filename: string; media_type: string; size_bytes: number; sha256: string;
    artifact_id?: string; resume_id?: number; resume_version_id?: string | null; resume_choice?: "original" | "tailored" | "custom" };
  context_digest: string; reviewed_answers_sha256: string;
  review_protocol_version?: 2 | 3;
  reviewed_answers?: Record<string, string>;
  attachment?: { input_id: "resume"; expected_empty: true };
  fields: { id: string; label: string; type: "text" | "tel" | "combobox"; value: string; expected_before: string;
    options?: { value: string; label: string }[]; value_label?: string; expected_before_label?: string }[];
  manual_fields: { id: string; label: string; required: boolean }[];
  disclosures: string[]; allowed_actions: ["fill"] | ["attach"]; fee_credits: 0;
};
type Review = { id: string; state: string; expires_at_ms: number; payload?: Payload; review_digest: string; filled_count: number;
  review_protocol_version?: 2 | 3; attachment_status?: "selected_upload_unconfirmed" | "unknown" };
type Props = { deviceId: string | null; reviewId: string | null; applicationId: string | null };
type Operation = "device" | "confirm" | "revoke" | "review" | "approve" | "cancel";
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const SHA256 = /^[a-f0-9]{64}$/;
const IDENTIFIER = /^[a-zA-Z0-9_-]{1,128}$/;
const BASE_FIELD_LABELS: Record<string, string> = { "First Name": "first_name", "Last Name": "last_name", Email: "email" };
const CUSTOM_FIELD_LABELS = new Set(["LinkedIn Profile", "Website", "Total Years of Experience", "Current Location"]);
const CHOICE_FIELD_LABELS = new Set(["Current Career Stage", "Gender", "Current Industry", "Current Designation"]);
const DEVICE_STATES = new Set(["pending", "active", "revoked"]);
const REVIEW_STATES = new Set(["draft", "approved", "running", "filled", "unknown", "cancelled", "expired"]);
const APPROVABLE_STATES = new Set(["draft"]);
const fileTypes = new Set(["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"]);
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown, max = 255): value is string => typeof value === "string" && value.length <= max;
const integer = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
function destination(value: unknown): value is string {
  if (!text(value, 2048)) return false;
  try { const url = new URL(value); return url.origin === "https://job-boards.greenhouse.io" && !url.search && !url.hash && /^\/razorpaysoftwareprivatelimited\/jobs\/\d+$/.test(url.pathname) && value === url.href; } catch { return false; }
}
function isDevice(value: unknown, id: string): value is Device {
  return object(value) && value.id === id && text(value.state) && DEVICE_STATES.has(value.state)
    && text(value.key_sha256) && SHA256.test(value.key_sha256) && text(value.extension_id, 128) && value.extension_id.length > 0 && integer(value.expires_at_ms);
}
function isReview(value: unknown, id: string): value is Review {
  if (!object(value) || value.id !== id || !text(value.state) || !REVIEW_STATES.has(value.state) || !integer(value.expires_at_ms)
    || !integer(value.filled_count) || value.filled_count > 12 || !text(value.review_digest) || !SHA256.test(value.review_digest)) return false;
  if (value.review_protocol_version !== undefined && ![2, 3].includes(value.review_protocol_version as number)) return false;
  if (value.review_protocol_version === 3 && (value.filled_count > 1 || (value.state === "filled" && value.filled_count !== 1)
    || value.attachment_status !== (value.state === "filled" ? "selected_upload_unconfirmed" : "unknown"))) return false;
  if (["filled", "unknown", "cancelled", "expired"].includes(value.state) && (value.payload == null || (object(value.payload) && !Object.keys(value.payload).length))) return true;
  if (!object(value.payload)) return false;
  const payload = value.payload;
  if (!text(payload.application_id, 128) || !IDENTIFIER.test(payload.application_id) || !destination(payload.target_url)
    || !text(payload.package_digest) || !SHA256.test(payload.package_digest) || !text(payload.context_digest) || !SHA256.test(payload.context_digest)
    || !text(payload.reviewed_answers_sha256) || !SHA256.test(payload.reviewed_answers_sha256) || payload.fee_credits !== 0
    || !Array.isArray(payload.allowed_actions) || payload.allowed_actions.length !== 1 || !object(payload.artifact)) return false;
  const version = payload.review_protocol_version ?? 1;
  if (![1, 2, 3].includes(version as number) || payload.allowed_actions[0] !== (version === 3 ? "attach" : "fill")) return false;
  if ((value.review_protocol_version !== undefined && value.review_protocol_version !== version) || (version === 3 && value.review_protocol_version !== 3)) return false;
  const artifact = payload.artifact;
  if (!text(artifact.filename, 512) || !artifact.filename.trim() || !text(artifact.media_type) || !fileTypes.has(artifact.media_type)
    || !integer(artifact.size_bytes) || !artifact.size_bytes || artifact.size_bytes > 10 * 1024 * 1024 || !text(artifact.sha256) || !SHA256.test(artifact.sha256)) return false;
  if (version === 3 && (!/^[\x20-\x7e]{1,240}$/.test(artifact.filename) || !text(artifact.artifact_id, 128) || !IDENTIFIER.test(artifact.artifact_id)
    || !integer(artifact.resume_id) || artifact.resume_id < 1 || !["original", "tailored", "custom"].includes(artifact.resume_choice as string)
    || (artifact.resume_choice === "tailored" ? !text(artifact.resume_version_id, 128) || !IDENTIFIER.test(artifact.resume_version_id) : artifact.resume_version_id !== null)
    || !object(payload.attachment) || Object.keys(payload.attachment).sort().join(",") !== "expected_empty,input_id"
    || payload.attachment.input_id !== "resume" || payload.attachment.expected_empty !== true
    || !object(payload.reviewed_answers) || Object.keys(payload.reviewed_answers).length !== 0)) return false;
  if (!Array.isArray(payload.fields) || (version === 3 ? payload.fields.length !== 0 : !payload.fields.length || payload.fields.length > (version === 2 ? 12 : 7))
    || payload.fields.some((field) => {
      if (!object(field) || !text(field.id, 128) || !text(field.label, 256) || !text(field.value) || !field.value.trim() || !text(field.expected_before)) return true;
      if (field.type === "text") return Object.hasOwn(BASE_FIELD_LABELS, field.label) ? field.id !== BASE_FIELD_LABELS[field.label] : !CUSTOM_FIELD_LABELS.has(field.label) || !/^question_\d+$/.test(field.id);
      if (version !== 2) return true;
      if (field.type === "tel") return field.id !== "phone" || field.label !== "Phone";
      if (field.type !== "combobox" || !CHOICE_FIELD_LABELS.has(field.label) || !/^question_\d{1,20}$/.test(field.id)
        || !Array.isArray(field.options) || !field.options.length || field.options.length > 64
        || field.options.some((option) => !object(option) || !text(option.value) || !option.value || !text(option.label) || !option.label)
        || new Set(field.options.map((option) => option.value)).size !== field.options.length
        || new Set(field.options.map((option) => option.label)).size !== field.options.length) return true;
      return !field.options.some((option) => option.value === field.value && option.label === field.value_label)
        || (field.expected_before === "" ? field.expected_before_label !== "" : !field.options.some((option) => option.value === field.expected_before && option.label === field.expected_before_label));
    }) || new Set(payload.fields.map((field) => field.id)).size !== payload.fields.length
    || new Set(payload.fields.map((field) => field.label)).size !== payload.fields.length) return false;
  return Array.isArray(payload.manual_fields) && payload.manual_fields.length <= 256 && payload.manual_fields.every((field) => object(field)
    && text(field.id, 128) && text(field.label, 256) && typeof field.required === "boolean")
    && Array.isArray(payload.disclosures) && payload.disclosures.length <= 32 && payload.disclosures.every((disclosure) => text(disclosure, 2048));
}
function payloadIdentity(payload: Payload): string {
  return JSON.stringify([payload.application_id, payload.target_url, payload.package_digest, payload.artifact.filename,
    payload.artifact.media_type, payload.artifact.size_bytes, payload.artifact.sha256, payload.context_digest, payload.reviewed_answers_sha256,
    payload.review_protocol_version ?? 1, payload.attachment, payload.reviewed_answers,
    payload.artifact.artifact_id, payload.artifact.resume_id, payload.artifact.resume_version_id, payload.artifact.resume_choice,
    payload.fields.map(({ id, label, type, value, expected_before, options, value_label, expected_before_label }) => [id, label, type, value, expected_before, options, value_label, expected_before_label]),
    payload.manual_fields.map(({ id, label, required }) => [id, label, required]), payload.disclosures, payload.allowed_actions, payload.fee_credits]);
}

export default function BrowserAssistanceReview({ deviceId, reviewId, applicationId }: Props) {
  const [device, setDevice] = useState<Device | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [downloaded, setDownloaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [expired, setExpired] = useState(false);
  const [signedOut, setSignedOut] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [requiresReload, setRequiresReload] = useState(false);
  const active = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; active.current?.abort(); }; }, []);
  const expiry = review && !["filled", "unknown", "cancelled", "expired"].includes(review.state) ? review.expires_at_ms : device?.state === "pending" ? device.expires_at_ms : null;
  useEffect(() => {
    if (expiry === null) return;
    const timer = setTimeout(() => setExpired(true), Math.min(Math.max(0, expiry - Date.now()), 2_147_483_647));
    return () => clearTimeout(timer);
  }, [expiry]);
  const validDevice = Boolean(deviceId && UUID.test(deviceId));
  const validReview = Boolean(reviewId && UUID.test(reviewId));
  const validApplication = Boolean(applicationId && IDENTIFIER.test(applicationId));
  const attachmentReview = (review?.review_protocol_version ?? review?.payload?.review_protocol_version) === 3;
  const fileReviewed = attachmentReview || downloaded;
  const invalid = Boolean((deviceId && !validDevice) || (reviewId && !validReview) || (applicationId && !validApplication) || (deviceId && reviewId));
  const returnUrl = validDevice ? `/browser-companion?device=${deviceId}` : validReview ? `/browser-companion?review=${reviewId}`
    : validApplication ? `/browser-companion?application=${encodeURIComponent(applicationId!)}` : "/browser-companion";

  async function post(operation: Operation, body: object, controller: AbortController): Promise<unknown> {
    const headers = await accountMutationHeaders();
    controller.signal.throwIfAborted();
    const response = await fetch(`/api/browser-assistance/candidate/${operation}`, { method: "POST", redirect: "error", cache: "no-store",
      credentials: "same-origin", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]), headers, body: JSON.stringify(body) });
    if (!response.ok) {
      if (response.status === 401) { setSignedOut(true); throw new Error("Sign in with your HireWiz Google account to review this request."); }
      throw new Error(response.status === 403 ? "This request is unavailable for your account. Start again from your own companion."
        : response.status === 409 || response.status === 410 ? "This request changed or expired. Load its current status before taking another action."
        : "This browser assistance request could not be verified. Load its current status; no automatic retry will occur.");
    }
    return response.json();
  }
  async function perform(operation: Operation) {
    const isDeviceOperation = ["device", "confirm", "revoke"].includes(operation);
    const identity = isDeviceOperation ? deviceId : reviewId;
    if (invalid || !identity || active.current || (isDeviceOperation ? !validDevice : !validReview)) return;
    if ((operation === "confirm" || operation === "approve") && (!accepted || expired || (expiry !== null && expiry <= Date.now()))) {
      setAccepted(false); setExpired(true); setError("This review expired. Load the current status from your companion."); return;
    }
    if (operation === "confirm" && device?.state !== "pending") return;
    if (operation === "approve" && (!review || !APPROVABLE_STATES.has(review.state) || !fileReviewed)) return;
    if ((operation === "confirm" || operation === "approve") && requiresReload) return;
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setError(null); setNotice(null); setSignedOut(false);
    if (operation === "device" || operation === "review") { setAccepted(false); setDownloaded(false); setExpired(false); setDevice(null); setReview(null); }
    try {
      const body = operation === "confirm" ? { identity, confirmed: true, key_sha256: device!.key_sha256 }
        : operation === "approve" ? { identity, confirmed: true, review_digest: review!.review_digest } : operation === "cancel" || operation === "revoke" ? { identity, confirmed: true } : { identity };
      const result = await post(operation, body, controller);
      if (!mounted.current || controller.signal.aborted) return;
      if (isDeviceOperation) {
        if (!isDevice(result, identity)) throw new Error("The device response changed. Load the current status; no automatic retry will occur.");
        if ((operation === "confirm" && result.state !== "active") || (operation === "revoke" && result.state !== "revoked")
          || ((operation === "confirm" || operation === "revoke") && result.key_sha256 !== device?.key_sha256)) throw new Error("The confirmed device key or state changed. Load the current status; no automatic retry will occur.");
        setDevice(result); setAccepted(false);
        if (operation === "confirm") setNotice("Device confirmation saved. Return to your companion. You will approve each application separately.");
        if (operation === "revoke") setNotice("Device access revoked. Already disclosed answers cannot be recalled.");
      } else {
        if (!isReview(result, identity) || (validApplication && result.payload && result.payload.application_id !== applicationId)) throw new Error("The application review response changed. Load the current status; no automatic retry will occur.");
        if (operation === "approve" && (!review || result.review_digest !== review.review_digest || !["approved", "running", "filled"].includes(result.state)
          || (result.payload && review.payload && payloadIdentity(result.payload) !== payloadIdentity(review.payload)))) throw new Error("The approved review changed. Load the current status; no automatic retry will occur.");
        setReview({ ...result, payload: object(result.payload) && Object.keys(result.payload).length ? result.payload : undefined }); setAccepted(false);
        if (operation === "approve") setNotice(attachmentReview ? "Attachment assistance approved. Return to your companion to fetch and select this exact saved file. Selection may upload it immediately. Employer upload remains unconfirmed; submit the application yourself."
          : result.payload?.review_protocol_version === 2 ? "Form assistance approved. Return to your companion to fill the exact reviewed answers and choices. Attach the file and submit yourself." : "Text assistance approved. Return to your companion to fill the reviewed answers. Attach the file and submit yourself.");
        if (operation === "cancel") setNotice(attachmentReview ? "Further attachment assistance cancelled. File bytes already disclosed may remain with the employer. An already authorized selection may finish within ten seconds."
          : "Further assistance cancelled. Answers already filled or disclosed may remain with the employer. A field already authorized may finish within ten seconds; future fields are stopped.");
      }
      setRequiresReload(false);
    } catch (failure) {
      if (mounted.current && !controller.signal.aborted) { setError(failure instanceof Error ? failure.message : "The request outcome is unavailable. Load its current status; no automatic retry will occur."); setAccepted(false); if (!["device", "review"].includes(operation)) setRequiresReload(true); }
    } finally {
      if (active.current === controller) active.current = null;
      if (mounted.current && !controller.signal.aborted) setBusy(false);
    }
  }
  async function download() {
    if (!review?.payload || review.payload.review_protocol_version === 3 || busy) return;
    setBusy(true); setError(null);
    try { await apiDownload(`/v1/employer-jobs/applications/${encodeURIComponent(review.payload.application_id)}/artifact`, review.payload.artifact.filename); if (mounted.current) setDownloaded(true); }
    catch (failure) { if (mounted.current) { setDownloaded(false); setError(failure instanceof Error ? failure.message : "The exact review file could not be downloaded."); } }
    finally { if (mounted.current) setBusy(false); }
  }
  async function copyApplication() {
    if (!validApplication) return;
    try { await navigator.clipboard.writeText(applicationId!); if (mounted.current) setCopied(true); }
    catch { if (mounted.current) setError("Copy the application ID shown below into your companion."); }
  }

  return <section className="surface space-y-6 p-5 sm:p-8" aria-busy={busy}>
    <div><p className="eyebrow">Candidate-reviewed browser assistance</p><h1 className="mt-3 font-display text-3xl sm:text-4xl">{validDevice ? "Connect this browser" : validReview ? attachmentReview ? "Review your exact attachment" : "Review your exact answers" : "Prepare your browser companion"}</h1>
      <p className="mt-4 text-sm leading-7 text-muted-foreground">Use your signed-in HireWiz account to approve a device and each application separately. Each Razorpay review authorizes only its listed action for the selected job. Country, employment, unsupported controls and final submission stay with you.</p></div>
    {invalid ? <p role="alert" className="text-sm text-coral">Open a valid device or application review from your companion.</p> : validDevice ? <div className="space-y-5">
      <Button variant="secondary" disabled={busy} onClick={() => void perform("device")}>{busy ? "Loading…" : device ? "Load current device status" : "Review device connection"}</Button>
      {device ? <div className="space-y-4"><p className="text-sm">Device status: <span className="font-semibold">{device.state}</span></p><dl className="space-y-3 rounded-xl border border-border p-4 text-xs"><div><dt className="font-semibold">Device key fingerprint</dt><dd className="mt-1 break-all font-mono">{device.key_sha256}</dd></div><div><dt className="font-semibold">Companion extension</dt><dd className="mt-1 break-all font-mono">{device.extension_id}</dd></div></dl>
        {device.state === "pending" ? <><label className="flex items-start gap-3 text-sm leading-6"><input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-primary" checked={accepted} disabled={busy || expired || requiresReload} onChange={(event) => setAccepted(event.target.checked)} />I checked this key fingerprint against my companion and want to connect this device to my HireWiz account.</label><Button disabled={busy || expired || requiresReload || !accepted} onClick={() => void perform("confirm")}>Confirm this device</Button></> : null}
        {!["revoked", "expired"].includes(device.state) ? <Button variant="danger" size="sm" disabled={busy} onClick={() => void perform("revoke")}>Revoke this device</Button> : null}</div> : null}
    </div> : validReview ? <div className="space-y-5">
      <Button variant="secondary" disabled={busy} onClick={() => void perform("review")}>{busy ? "Loading…" : review ? "Load current review status" : "Load exact application review"}</Button>
      {review && review.payload ? <div className="space-y-5"><p className="text-sm">Assistance status: <span className="font-semibold">{review.state}</span>{attachmentReview ? " · attachment only" : ` · ${review.filled_count} answers filled`}</p>
        <div className="rounded-xl border border-border p-4"><h2 className="font-semibold">Exact destination and file</h2><a href={review.payload.target_url} target="_blank" rel="noopener noreferrer" className="mt-2 block break-all text-sm text-primary underline underline-offset-4">{review.payload.target_url}</a><p className="mt-3 break-words text-sm font-semibold">{review.payload.artifact.filename}</p><p className="mt-1 text-xs text-muted-foreground">{(review.payload.artifact.size_bytes / 1024).toFixed(1)} KB · {review.payload.artifact.media_type}</p><p className="mt-3 break-all font-mono text-xs">SHA-256: {review.payload.artifact.sha256}</p>
          {attachmentReview ? <dl className="mt-3 space-y-2 break-words text-xs"><div><dt className="font-semibold">Saved resume source</dt><dd>{review.payload.artifact.resume_choice} resume #{review.payload.artifact.resume_id}{review.payload.artifact.resume_version_id ? ` · version ${review.payload.artifact.resume_version_id}` : ""}</dd></div><div><dt className="font-semibold">Sealed artifact</dt><dd>{review.payload.artifact.artifact_id}</dd></div><div><dt className="font-semibold">Portal file input</dt><dd>Resume/CV · resume · currently empty</dd></div></dl>
            : <Button variant="secondary" size="sm" className="mt-3" disabled={busy} onClick={() => void download()}>Download exact review file</Button>}</div>
        {!attachmentReview ? <div><h2 className="font-semibold">Answers to fill</h2><dl className="mt-3 divide-y divide-border rounded-xl border border-border px-4">{review.payload.fields.map((field) => <div key={field.id} className="py-3"><dt className="text-sm font-semibold">{field.label}</dt><dd className="mt-1 whitespace-pre-wrap break-words text-sm">{field.type === "combobox" ? field.value_label : field.value}</dd><dd className="mt-2 whitespace-pre-wrap break-words text-xs text-muted-foreground">Current value: {(field.type === "combobox" ? field.expected_before_label : field.expected_before) || "Empty"}</dd></div>)}</dl></div> : null}
        <div className="rounded-xl border border-border bg-surface p-4"><h2 className="font-semibold">Before you approve</h2><p className="mt-2 text-sm leading-6">{attachmentReview ? "Your companion may fetch this exact saved file only after you approve. Selecting it can immediately disclose or upload its bytes to the employer or its upload service before final submission. Cancelling cannot recall earlier disclosure; an already authorized selection may finish within ten seconds."
          : "Filling can disclose or autosave answers before submission. Cancelling stops future fields. A field already authorized may finish within ten seconds, and earlier callbacks cannot be recalled."}</p>{review.payload.disclosures.length ? <ul className="mt-3 list-disc space-y-2 pl-5 text-sm leading-6">{review.payload.disclosures.map((disclosure, index) => <li key={index}>{disclosure}</li>)}</ul> : null}<p className="mt-3 text-sm leading-6">{attachmentReview ? "This authorizes file selection only. Employer upload is unconfirmed, no application receipt is established, and you must submit the application yourself." : "Selecting a resume file manually may upload its bytes immediately. HireWiz does not attach files or submit from this flow."}</p><p className="mt-3 text-sm font-semibold">{attachmentReview ? "0 application credits charged for this attachment assistance." : "0 application credits charged for this fill-only assistance."}</p></div>
        <div><h2 className="font-semibold">Complete these on the employer portal</h2><ul className="mt-3 list-disc space-y-2 pl-5 text-sm leading-6">{review.payload.manual_fields.filter((field) => field.required).map((field, index) => <li key={`${field.id}:${index}`} className="break-words">{field.label || field.id || "Required manual control"}</li>)}<li>{attachmentReview ? "Check the selected file and any upload status, complete the remaining fields, resolve CAPTCHA or login, and submit the application yourself." : "Attach the reviewed resume, resolve any CAPTCHA or login, and submit the application yourself."}</li></ul></div>
        {APPROVABLE_STATES.has(review.state) ? <><label className="flex items-start gap-3 text-sm leading-6"><input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-primary" checked={accepted} disabled={busy || expired || !fileReviewed || requiresReload} onChange={(event) => setAccepted(event.target.checked)} />{attachmentReview ? "I reviewed the exact saved file, resume source and employer destination above. I authorize my companion to fetch and select only this file in the empty Resume/CV input, which may upload it immediately. Final submission remains my responsibility." : "I reviewed the exact file, destination and answers above, including pre-submit disclosure. I authorize filling only; attachment and final submission remain my responsibility."}</label>{!fileReviewed ? <p className="text-xs text-muted-foreground">Download and review the exact file before approving these answers.</p> : null}<Button disabled={busy || expired || !fileReviewed || requiresReload || !accepted} onClick={() => void perform("approve")}>{attachmentReview ? "Approve exact attachment assistance" : review.payload.review_protocol_version === 2 ? "Approve reviewed form assistance" : "Approve reviewed text assistance"}</Button></> : null}
        {!["cancelled", "expired", "filled"].includes(review.state) ? <Button variant="danger" disabled={busy} onClick={() => void perform("cancel")}>Cancel further assistance</Button> : null}
        {review.state === "unknown" ? <p role="status" className="text-sm leading-6 text-muted-foreground">{attachmentReview ? "The file selection or upload outcome is uncertain. Inspect the employer portal. No automatic retry will occur." : "A previous fill outcome is uncertain. Inspect the employer portal before continuing. No automatic retry will occur."}</p> : null}
        {review.state === "filled" ? <p role="status" className="text-sm leading-6 text-muted-foreground">{attachmentReview ? "Resume selected locally. Employer upload is unconfirmed. No application receipt or fee is established. Check the portal and submit the application yourself." : "Reviewed answers filled only. HireWiz has not submitted this application. Finish the remaining fields, attachment and submission yourself."}</p> : null}
      </div> : review ? <p role="status" className="text-sm leading-6 text-muted-foreground">{review.state === "filled" ? attachmentReview ? "Resume selected locally. Employer upload is unconfirmed. No application receipt or fee is established. Check the portal and submit the application yourself." : "Reviewed answers filled only. Finish the remaining fields, attachment and final submission yourself." : review.state === "unknown" ? attachmentReview ? "The file selection or upload outcome is uncertain. Inspect the employer portal; no automatic retry will occur." : "A previous fill outcome is uncertain. Inspect the employer portal before continuing; no automatic retry will occur." : review.state === "cancelled" ? attachmentReview ? "Further attachment assistance cancelled. File bytes already disclosed may remain with the employer." : "Further assistance cancelled. Already disclosed answers may remain with the employer." : "This review expired. Start a fresh request from your companion."}</p> : null}
    </div> : <div className="space-y-4"><div className="rounded-xl border border-border p-4"><h2 className="font-semibold">Install the Chrome pilot</h2><p className="mt-2 text-sm leading-7">On your laptop or desktop, download and unzip the companion. Open Chrome&apos;s Extensions page, turn on Developer mode, choose Load unpacked, and select the extracted hirewiz-browser-companion folder. Pin HireWiz to your toolbar. The pilot asks for HireWiz and Greenhouse access when you choose to connect.</p><a className="mt-3 inline-block text-sm font-semibold text-primary underline underline-offset-4" href="/downloads/hirewiz-browser-companion-0.4.0.zip" download>Download HireWiz companion 0.4.0</a><p className="mt-3 text-xs leading-6 text-muted-foreground">Chrome desktop only. On mobile, use the company portal manually. Supported answers require a fill review. Attachment assistance requires a separate exact-file review when available; final submission stays manual.</p></div><ol className="list-decimal space-y-3 pl-5 text-sm leading-7"><li>Open your saved application and the matching Razorpay employer portal.</li><li>Open the HireWiz companion in Chrome and connect this device.</li><li>Inspect the portal and return here to review the exact file and listed action.</li><li>Approve the reviewed assistance, then check the employer portal and submit the application yourself.</li></ol>{validApplication ? <div className="rounded-xl border border-border p-4"><p className="text-sm font-semibold">Application ID for your companion</p><code className="mt-2 block break-all text-xs">{applicationId}</code><Button variant="secondary" size="sm" className="mt-2" onClick={() => void copyApplication()}>Copy application ID</Button>{copied ? <p role="status" className="mt-2 text-xs text-primary">Application ID copied.</p> : null}</div> : null}<p className="text-sm leading-6 text-muted-foreground">Open a device connection or application review from the installed companion. This page does not start an employer action by itself.</p></div>}
    {expired ? <p role="status" className="text-sm leading-6 text-muted-foreground">This review expired. Load the current status or start a fresh request from your companion.</p> : null}
    {notice ? <p role="status" className="text-sm leading-6 text-primary">{notice}</p> : null}
    {error ? <p role="alert" className="text-sm leading-6 text-coral">{error}</p> : null}
    {signedOut ? <Button variant="secondary" onClick={() => void signIn("google", { callbackUrl: returnUrl })}>Sign in with Google</Button> : null}
    <Link href="/employer-jobs" className="inline-block text-sm font-semibold text-primary underline underline-offset-4">Return to job search</Link>
  </section>;
}
