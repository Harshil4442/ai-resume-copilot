import { ASSISTED_FIELDS, FIXTURE_PATH, FIXTURE_MARKER, SYNTHETIC_IDENTITY, RECIPIENT_POLICY, validateAnswers } from "./schema.js";

const issued = new WeakSet(); const used = new WeakSet();
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const blocked = (reason) => ({ outcome: "blocked", reason, synthetic_only: true });
const allowed = new Set(ASSISTED_FIELDS.map(([id]) => id));
const visible = (control) => {
  if (!control.getClientRects().length || control.closest("[hidden],[aria-hidden='true']")) return false;
  for (let node = control; node; node = node.parentElement) {
    const style = getComputedStyle(node);
    if (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse" || style.opacity === "0") return false;
  }
  return true;
};

export function inspectSyntheticForm(expectedOrigin) {
  if (window !== window.top || location.hostname !== "127.0.0.1" || location.origin !== expectedOrigin || location.pathname !== FIXTURE_PATH || location.search || location.hash) return blocked("Exact loopback fixture origin/path/top frame required");
  const roots = [...document.querySelectorAll(`[data-hirewiz-scaffold='${FIXTURE_MARKER}']`)];
  if (roots.length !== 1) return blocked("Exact synthetic marker is missing or ambiguous");
  const root = roots[0];
  for (const key of ["tenant", "employer", "opening"]) if (root.dataset[key] !== SYNTHETIC_IDENTITY[key]) return blocked("Synthetic tenant/opening identity mismatch");
  const forms = [...root.querySelectorAll("form")]; if (forms.length !== 1) return blocked("Exactly one fixture form is required");
  if ([...forms[0].querySelectorAll("input[type=file]")].some((control) => control.files.length)) return blocked("Manual selected file is unverified; this scaffold cannot authorize assistance against it");
  const form = forms[0]; const fields = [...form.querySelectorAll("input,select,textarea")].map((control) => ({
    id: control.id, label: [...(control.labels || [])].map((label) => label.textContent.trim()).join(" "),
    type: control.tagName === "TEXTAREA" ? "textarea" : control.type, role: control.getAttribute("role"),
    required: control.required || control.getAttribute("aria-required") === "true", max: control.maxLength,
    disabled: control.disabled, readonly: control.readOnly, hidden: !visible(control),
    options: control.dataset.options || null,
  }));
  if (fields.some((field) => !field.id) || new Set(fields.map((field) => field.id)).size !== fields.length) return blocked("Missing or duplicate control identity");
  let policy; try { policy = JSON.parse(root.dataset.recipientPolicy); } catch { return blocked("Recipient policy is unreadable"); }
  if (!same(policy, RECIPIENT_POLICY)) return blocked("Unreviewed recipient policy");
  const manual = fields.filter((field) => !allowed.has(field.id));
  const manualValues = manual.map(({ id }) => { const control = document.getElementById(id); return [id, control.type === "checkbox" ? control.checked : control.type === "file" ? control.files.length : control.value, control.dataset.selectedValue || null]; });
  const context = { origin: location.origin, url: location.href, tenant: root.dataset.tenant, employer: root.dataset.employer, opening: root.dataset.opening, form_version: root.dataset.formVersion, fields, manual_values: manualValues, recipient_policy: policy };
  const checkpoint = root.dataset.checkpoint || (form.querySelector("input[type=password]") ? "login" : null);
  if (checkpoint) return blocked(`Human checkpoint: ${checkpoint}`);
  return { outcome: "inspected", synthetic_only: true, context, assisted_fields: fields.filter((field) => allowed.has(field.id)), manual_fields: manual, required_controls: fields.filter((field) => field.required).length,
    status: "manual_handoff", message: "Optional text assistance only. Remaining required controls, attachment and final submission are manual; no receipt or charge exists." };
}

// Intentionally unauthenticated, in-memory TEST gate. It proves adapter decisions
// against this fixture, not v2 integration, device pairing or restore-safe authority.
export function issueSyntheticGate({ inspection, answers, field_id, expected_before, deadline, acknowledged }) {
  validateAnswers(answers);
  if (inspection?.outcome !== "inspected" || acknowledged !== true || !allowed.has(field_id) || !Object.hasOwn(answers, field_id) || typeof expected_before !== "string" || !Number.isSafeInteger(deadline) || deadline <= Date.now() || deadline - Date.now() > 2000) throw new Error("Current exact synthetic review/acknowledgement and short gate required");
  const fields = inspection.assisted_fields.filter((field) => field.id === field_id);
  if (fields.length !== 1 || fields[0].type !== "text" || fields[0].role || fields[0].hidden || fields[0].disabled || fields[0].readonly) throw new Error("Reviewed field must be a visible supported text control");
  const gate = Object.freeze({ synthetic_only: true, context: JSON.stringify(inspection.context), answers: JSON.stringify(answers), field_id, value: answers[field_id], expected_before, deadline });
  issued.add(gate); return gate;
}

export function fillSyntheticField({ expectedOrigin, inspection, answers, gate, cancelled = false }) {
  if (!gate || !issued.has(gate) || used.has(gate)) return blocked("Absent, reconstructed or replayed synthetic gate");
  used.add(gate); // irreversible even if later checks reject
  if (cancelled || Date.now() >= gate.deadline) return blocked("Cancelled or expired synthetic step");
  const current = inspectSyntheticForm(expectedOrigin);
  if (current.outcome !== "inspected") return current;
  if (inspection?.outcome !== "inspected" || gate.context !== JSON.stringify(inspection.context) || !same(current.context, inspection.context) || gate.answers !== JSON.stringify(answers)) return blocked("Form, manual values, recipient policy or exact review changed");
  const matches = [...document.querySelectorAll("input,select,textarea")].filter((control) => control.id === gate.field_id);
  if (matches.length !== 1) return blocked("Ambiguous field");
  const control = matches[0];
  if (!allowed.has(control.id) || control.type !== "text" || control.getAttribute("role") || control.disabled || control.readOnly || !control.isConnected || !visible(control) || control.value !== gate.expected_before) return blocked("Unsupported, hidden or changed control");
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(control, gate.value);
  control.dispatchEvent(new Event("input", { bubbles: true }));
  control.dispatchEvent(new Event("change", { bubbles: true }));
  // Fixture models the observed email-on-blur callback; never Enter or Submit.
  if (control.id === "email") control.dispatchEvent(new FocusEvent("blur"));
  // The approved answer plan is not completion. Retain blank/mismatched fields,
  // while every required manual control remains the candidate's responsibility.
  const manualHandoff = inspection.context.fields.filter((field) => {
    if (!field.required) return false;
    if (!allowed.has(field.id) || !Object.hasOwn(answers, field.id)) return true;
    const controls = [...document.querySelectorAll("input,select,textarea")].filter((node) => node.id === field.id);
    return controls.length !== 1 || controls[0].value !== answers[field.id];
  }).map(({ id, label, hidden }) => ({ id, label, hidden }));
  return { outcome: "filled_only", field_id: gate.field_id, synthetic_only: true,
    manual_handoff: manualHandoff,
    message: "One reviewed synthetic answer filled. Remaining manual fields/file/final action remain; no application, receipt or charge." };
}
