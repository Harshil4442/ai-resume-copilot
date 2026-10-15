// Serialized by chrome.scripting into an isolated, top-frame execution. This
// adapter recognizes only the controlled synthetic form, never a live ATS.
export function localAdapter(expected = null, action = null) {
  const fail = (reason) => ({ outcome: "blocked", reason });
  if (window !== window.top || location.hostname !== "127.0.0.1" || location.pathname !== "/fixture/apply") return fail("Unsupported local fixture page");
  const root = document.querySelector("[data-hirewiz-fixture='v1']");
  if (!root) return fail("Synthetic adapter marker is missing");
  const checkpoint = root.dataset.checkpoint || (document.querySelector("input[type=password]") ? "login" : null);
  const form = root.querySelector("form");
  if (!form) return fail("Fixture form is missing");
  const fields = [...form.querySelectorAll("input,select,textarea")].map((control) => ({
    id: control.id, label: [...(control.labels || [])].map((label) => label.textContent.trim()).join(" ").slice(0, 256),
    type: control.tagName === "TEXTAREA" ? "textarea" : control.type,
    required: control.required, options: control.tagName === "SELECT" ? [...control.options].map((option) => ({ value: option.value, label: option.label })) : [],
  })).sort((a, b) => a.id.localeCompare(b.id));
  const page = { origin: location.origin, url: location.href, frame_id: 0, user_id: root.dataset.user,
    tenant_id: root.dataset.tenant, employer_key: root.dataset.employer, canonical_opening_key: root.dataset.opening,
    form_version: root.dataset.formVersion, fields, checkpoint };
  if (!action) return page;
  if (!expected || Date.now() >= action.deadline || checkpoint) return fail(checkpoint ? `Human step required: ${checkpoint}` : "Field authorization expired");
  for (const key of ["origin", "url", "user_id", "tenant_id", "employer_key", "canonical_opening_key", "form_version"]) {
    if (page[key] !== expected[key]) return fail("Page/account identity changed before disclosure");
  }
  const schema = (value) => JSON.stringify(value.map((field) => [field.id, field.label, field.type, field.required, field.options.map((option) => [option.value, option.label])]));
  if (schema(fields) !== schema(expected.fields)) return fail("Questions changed before disclosure");
  const allowed = ["full_name", "email", "cover_letter", "work_authorization", "privacy_consent", "sponsorship_details"];
  const matches = [...form.querySelectorAll("input,select,textarea")].filter((control) => control.id === action.field_id);
  if (!allowed.includes(action.field_id) || matches.length !== 1) return fail("Missing or ambiguous fixture control");
  const control = matches[0];
  if (control.disabled || control.readOnly || !control.isConnected || control.type === "file" || control.type === "password" || control.type === "hidden") return fail("Control requires human input");
  const property = control.type === "checkbox" ? "checked" : "value";
  if (property === "checked" ? typeof action.value !== "boolean" : typeof action.value !== "string") return fail("Field value has wrong type");
  const prototype = control.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : control.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, property).set.call(control, action.value);
  // These events may autosave. They are emitted only after a fresh per-field
  // begin permit and the synchronous identity/schema check immediately above.
  control.dispatchEvent(new Event("input", { bubbles: true }));
  control.dispatchEvent(new Event("change", { bubbles: true }));
  return { outcome: "filled", field_id: action.field_id };
}
