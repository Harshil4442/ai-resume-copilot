import { CONFIG } from "./config.js";
import { decode64 } from "./protocol.js";
const element = (id) => document.getElementById(id);
let review = null; let artifactUrl = null;
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
const status = await request({ type: "status" });
element("mode").textContent = status.mode === "disabled" ? "Disabled: production authorization, real tenant permits and device enrollment are not available." : "Local synthetic fixtures only. No real employer adapter is enabled.";
if (status.mode === "local_fixture") { element("permission").disabled = false; if (status.connected) element("prepare").disabled = false; }
if (status.checkpoint && ["action_in_progress", "unknown"].includes(status.checkpoint.status)) error("Previous fill may be incomplete. Inspect the portal; automatic recovery is disabled.");
