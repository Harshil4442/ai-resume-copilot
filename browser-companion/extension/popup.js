import { CONFIG } from "./config.js";
import { decode64 } from "./protocol.js";
const element = (id) => document.getElementById(id);
let review = null; let artifactUrl = null;
let connection = null;
let assistance = null;
async function request(message) {
  const result = await chrome.runtime.sendMessage(message);
  if (!result?.ok) throw new Error(result?.error || "Companion worker unavailable. Inspect the portal before retrying.");
  return result.value;
}
function clearReview() {
  review = null; element("review").hidden = true; element("approve").checked = false; element("fill").disabled = true;
  if (artifactUrl) URL.revokeObjectURL(artifactUrl); artifactUrl = null;
}
function error(message) { clearReview(); element("result").textContent = ""; element("error").textContent = message; element("error").hidden = false; }
function pair(list, label, value) { const term = document.createElement("dt"); term.textContent = label; const description = document.createElement("dd"); description.textContent = String(value); list.append(term, description); }
function showReview(value) {
  clearReview(); review = value;
  const command = value.command; const target = element("destination"); target.replaceChildren();
  for (const [label, data] of [["Employer / tenant", `${command.employer_key} / ${command.tenant_id}`], ["Job", command.canonical_opening_key], ["Destination", command.target.url], ["Owner / device", `${command.user_id} / ${command.device_id}`], ["Package SHA-256", command.package.digest]]) pair(target, label, data);
  const artifact = command.package.artifact;
  element("file").textContent = `${artifact.filename} · ${artifact.media_type} · ${artifact.size_bytes} bytes\nSHA-256: ${artifact.sha256}\nThe hash was checked against the exact downloaded bytes. File upload remains manual.`;
  artifactUrl = URL.createObjectURL(new Blob([decode64(value.artifact_base64)], { type: artifact.media_type }));
  element("download").href = artifactUrl; element("download").download = artifact.filename;
  const answers = element("answers"); answers.replaceChildren();
  const values = { ...command.package.answers, ...command.package.consents };
  command.form.fields.forEach((field) => pair(answers, field.label, typeof values[field.id] === "boolean" ? values[field.id] ? "Yes — explicitly approved" : "No" : values[field.id]));
  element("review").hidden = false; element("cancel").disabled = false;
}
function action(id, operation) { element(id).addEventListener("click", async () => { element("error").hidden = true; try { await operation(); } catch (failure) { error(failure.message); } }); }
action("permission", async () => {
  const granted = await chrome.permissions.request({ origins: [`${CONFIG.authorityOrigin}/*`, `${CONFIG.portalOrigin}/*`] });
  if (!granted) throw new Error("Exact local fixture permission was not granted.");
  element("connect").disabled = false; element("mode").textContent = "Local synthetic fixture access only. Production remains disabled.";
});
action("connect", async () => { clearReview(); const claim = await request({ type: "enroll", grant: element("grant").value }); element("grant").value = ""; element("prepare").disabled = false; element("mode").textContent = `Connected fixture owner ${claim.user_id} on this device.`; });
action("prepare", async () => { clearReview(); showReview(await request({ type: "prepare", application_id: element("application").value })); });
element("approve").addEventListener("change", () => { element("fill").disabled = !element("approve").checked || !review; });
action("fill", async () => {
  element("fill").disabled = true; element("prepare").disabled = true;
  element("result").textContent = "Filling only. You can cancel further filling.";
  try {
    const result = await request({ type: "fill", command_id: review.command.jti, review_digest: review.review_digest, acknowledged: element("approve").checked });
    clearReview(); element("result").textContent = result.message;
  } finally { element("prepare").disabled = false; }
});
action("cancel", async () => { const result = await request({ type: "cancel" }); clearReview(); element("result").textContent = result.message; });
window.addEventListener("unload", clearReview);

async function showConnection(value) {
  connection = value;
  element("native-connection").hidden = false;
  const permission = await chrome.permissions.contains({ origins: [`${CONFIG.websiteOrigin}/*`] });
  const idle = !value.busy;
  element("native-permission").disabled = permission;
  element("native-start").disabled = !permission || !idle || value.phase !== "idle";
  element("native-review").disabled = !value.review_url || !idle;
  element("native-confirmed").disabled = !permission || !idle || value.phase !== "awaiting_candidate";
  element("native-complete").disabled = element("native-confirmed").disabled || !element("native-confirmed").checked;
  element("native-refresh").disabled = !permission || !idle || value.phase !== "paired";
  element("native-pause").disabled = ["idle", "paused"].includes(value.phase);
  element("native-resume").disabled = !idle || value.phase !== "paused";
  element("native-inspect").disabled = !permission || !idle || value.phase !== "unknown" || !value.pairing_id;
  const list = element("native-fingerprints"); list.replaceChildren();
  if (value.key_sha256) pair(list, "Browser key SHA-256", value.key_sha256);
  if (value.request_sha256) pair(list, "Connection request SHA-256", value.request_sha256);
  const labels = { idle: "Ready to start. Review will happen on HireWiz.", preparing: "Preparing this browser connection…",
    awaiting_candidate: "Compare both fingerprints on HireWiz and approve the matching browser.", completing: "Proving this browser's key…",
    paired: value.connected ? "Current browser identity verified. No application action is enabled." : "Connection remembered. Check it again before relying on current identity.",
    refreshing: "Checking current browser identity…", unknown: "The previous outcome needs inspection. Automatic retry and a new connection are blocked.",
    paused: "Paused locally. The saved connection remains; this does not revoke an already paired device." };
  element("native-state").textContent = value.message || labels[value.phase] || "Connection needs inspection.";
}
function connectionAction(id, type) {
  action(id, async () => {
    element("native-confirmed").checked = false;
    const pending = { pairing_start: "preparing", pairing_complete: "completing", pairing_refresh: "refreshing" }[type];
    // This is presentation only; the worker persists intent before any request.
    // Keep Pause usable while a socket response is pending.
    if (pending && connection) await showConnection({ ...connection, phase: pending, busy: true, connected: false, message: "" });
    try { await showConnection(await request({ type })); }
    catch (failure) {
      await showConnection(await request({ type: "pairing_status" }));
      throw failure;
    }
  });
}
action("native-permission", async () => {
  const granted = await chrome.permissions.request({ origins: [`${CONFIG.websiteOrigin}/*`] });
  if (!granted) throw new Error("The exact HireWiz connection permission was not granted.");
  await showConnection(await request({ type: "pairing_status" }));
});
action("native-review", async () => {
  const url = new URL(connection.review_url);
  if (url.origin !== CONFIG.websiteOrigin) throw new Error("The saved review destination is invalid.");
  await chrome.tabs.create({ url: url.href });
});
connectionAction("native-start", "pairing_start");
connectionAction("native-complete", "pairing_complete");
connectionAction("native-refresh", "pairing_refresh");
connectionAction("native-pause", "pairing_cancel");
connectionAction("native-resume", "pairing_resume");
connectionAction("native-inspect", "pairing_inspect");
element("native-confirmed").addEventListener("change", () => {
  element("native-complete").disabled = !element("native-confirmed").checked || element("native-confirmed").disabled;
});

async function showAssistance(value) {
  assistance = value; element("assistance").hidden = false;
  const origins = [`${CONFIG.websiteOrigin}/*`, `${CONFIG.portalOrigin}/*`];
  const permitted = await chrome.permissions.contains({ origins });
  element("assistance-permission").disabled = permitted;
  element("assistance-connect").disabled = !permitted || value.busy || value.review_id || !["idle", "pending", "active"].includes(value.phase);
  element("assistance-check").disabled = !permitted || value.busy || value.phase === "idle";
  element("assistance-review").disabled = !value.review_url || value.busy || value.phase === "idle";
  element("assistance-prepare").disabled = !permitted || value.busy || value.review_id || value.phase !== "active";
  element("assistance-prepare-attachment").disabled = element("assistance-prepare").disabled || !value.attachment_available;
  element("assistance-prepare-submit").disabled = element("assistance-prepare").disabled || !value.submit_available;
  element("assistance-reconcile").disabled = !permitted || !value.can_reconcile;
  element("assistance-fill").disabled = !permitted || !value.can_fill;
  element("assistance-fill").textContent = value.review_protocol_version === 4 ? "Submit the approved application once" : value.review_protocol_version === 3 ? "Select approved resume" : "Fill approved answers";
  element("assistance-cancel").disabled = !value.review_id;
  const list = element("assistance-fingerprint"); list.replaceChildren();
  pair(list, "Browser key SHA-256", value.key_sha256);
  element("assistance-state").textContent = value.message || (value.review_protocol_version === 4 ? `Submission state: ${value.phase}. A possible-send attempt cannot be replayed. Employer receipt is unverified; 20 credits stay held after an uncertain send.` : value.review_protocol_version === 3
    ? `Attachment state: ${value.phase}. Local selection does not confirm employer upload. Check the portal before final submission.`
    : `Current state: ${value.phase}. ${value.filled_count} approved text answers filled. Check the portal yourself before submission.`);
}
function assistanceAction(id, type) {
  action(id, async () => {
    const message = ["assistance_prepare", "assistance_prepare_attachment", "assistance_prepare_submit"].includes(type) ? { type, application_id: element("assistance-application").value.trim() } : { type };
    if (assistance && ["assistance_fill", "assistance_prepare", "assistance_prepare_attachment", "assistance_prepare_submit"].includes(type)) await showAssistance({ ...assistance, busy: true, can_fill: false, message: "Working on the reviewed application…" });
    try { await showAssistance(await request(message)); }
    catch (failure) { await showAssistance(await request({ type: "status" })); throw failure; }
  });
}
action("assistance-permission", async () => {
  const granted = await chrome.permissions.request({ origins: [`${CONFIG.websiteOrigin}/*`, `${CONFIG.portalOrigin}/*`] });
  if (!granted) throw new Error("The exact HireWiz and Greenhouse permissions were not granted.");
  await showAssistance(await request({ type: "status" }));
});
action("assistance-review", async () => {
  const url = new URL(assistance.review_url);
  if (url.origin !== CONFIG.websiteOrigin || url.pathname !== "/browser-companion") throw new Error("The review destination changed.");
  await chrome.tabs.create({ url: url.href });
});
assistanceAction("assistance-connect", "assistance_connect");
assistanceAction("assistance-check", "assistance_check");
assistanceAction("assistance-prepare", "assistance_prepare");
assistanceAction("assistance-prepare-attachment", "assistance_prepare_attachment");
assistanceAction("assistance-prepare-submit", "assistance_prepare_submit");
assistanceAction("assistance-reconcile", "assistance_reconcile");
assistanceAction("assistance-fill", "assistance_fill");
assistanceAction("assistance-cancel", "assistance_cancel");
try {
  const status = await request({ type: "status" });
  element("mode").textContent = status.mode === "disabled" ? "Disabled: production authorization, real tenant permits and device enrollment are not available."
    : status.mode === "local_fixture" ? "Local synthetic fixtures only. No real employer adapter is enabled."
    : status.mode === "local_native_fixture" ? "Controlled local native connection test. Production remains disabled."
    : status.mode === "reviewed_assistance" ? "Candidate-reviewed Razorpay assistance. Approve your exact application on HireWiz before filling."
    : "Browser identity connection. Every application needs separate current approval.";
  if (status.mode === "local_fixture") {
    element("fixture-connection").hidden = false; element("fixture-prepare").hidden = false;
    element("grant").value = "local-owner-grant";
    element("permission").disabled = false; if (status.connected) element("prepare").disabled = false;
  } else if (["pairing_only", "local_native_fixture"].includes(status.mode)) await showConnection(status);
  else if (status.mode === "reviewed_assistance") await showAssistance(status);
  if (status.checkpoint && ["action_in_progress", "unknown"].includes(status.checkpoint.status)) error("Previous fill may be incomplete. Inspect the portal; automatic recovery is disabled.");
} catch {
  element("mode").textContent = "Browser connection unavailable. No application action is enabled.";
}
