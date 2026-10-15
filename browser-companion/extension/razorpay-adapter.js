// Last-mile DOM checks only. The trusted background must first verify the exact
// candidate review, device/owner authority, single-use begin and Chrome document
// identity. This function neither grants permission nor invents an account.
// `expected.review_digest` is the native authority's exact approved review;
// action.review_digest must match it. Additionally the caller seals the subset
// as reviewed_answers_sha256 = SHA256(canonical({context_digest,
// reviewed_answers})); both expected/action carry that fingerprint. Caller must
// bind both digests, document and exact values to the native online permit.
// An acknowledged disclosure argument is necessary DOM input, never authority.
// identity.field_mapping comes from the signed selected application's backend
// form. Custom question IDs belong to that job; never derive IDs from another
// opening or infer sensitive/unknown questions from the page.
// Self-contained so chrome.scripting can serialize it into an isolated world.
export async function razorpayAdapter(expected = null, action = null, identity = null) {
  const fail = (reason) => ({ outcome: "blocked", reason });
  const origin = "https://job-boards.greenhouse.io";
  const tenant = "razorpaysoftwareprivatelimited";
  const version = identity?.review_protocol_version ?? 1;
  if (![1, 2].includes(version) || !Number.isSafeInteger(version)) return fail("Unsupported reviewed form protocol");
  const adapterVersion = version === 2 ? "razorpay-greenhouse-controls-v2" : "razorpay-greenhouse-text-v1";
  const recipient = "https://email-address-validator.us.greenhouse.io/address/validate";
  const baseLabels = { first_name: "First Name", last_name: "Last Name", email: "Email" };
  const customLabels = new Set(["LinkedIn Profile", "Website", "Total Years of Experience", "Current Location"]);
  const choiceLabels = new Set(["Current Career Stage", "Gender", "Current Industry", "Current Designation"]);
  const validChoice = (field) => version === 2 && field.type === "combobox" && choiceLabels.has(field.label)
    && /^question_\d{1,20}$/.test(field.id) && Array.isArray(field.options) && field.options.length >= 1 && field.options.length <= 64
    && field.options.every((option) => option && Object.keys(option).sort().join(",") === "label,value"
      && [option.value, option.label].every((value) => typeof value === "string" && value.length > 0 && value.length <= 255 && value.isWellFormed()))
    && new Set(field.options.map(({ value }) => value)).size === field.options.length
    && new Set(field.options.map(({ label }) => label)).size === field.options.length;
  const supplied = identity?.field_mapping;
  if (!Array.isArray(supplied) || supplied.length < 3 || supplied.length > (version === 2 ? 12 : 7)
    || supplied.some((field) => !field || typeof field !== "object" || Array.isArray(field)
      || Object.keys(field).sort().join(",") !== (field.type === "combobox" ? "id,label,options,required,type" : "id,label,required,type")
      || typeof field.id !== "string" || field.id.length > 128 || typeof field.label !== "string" || typeof field.required !== "boolean"
      || !(field.type === "text" && (Object.hasOwn(baseLabels, field.id) ? field.label === baseLabels[field.id] : /^question_\d+$/.test(field.id) && customLabels.has(field.label))
        || version === 2 && field.type === "tel" && field.id === "phone" && field.label === "Phone" || validChoice(field)))
    || new Set(supplied.map(({ id }) => id)).size !== supplied.length || new Set(supplied.map(({ label }) => label)).size !== supplied.length
    || Object.keys(baseLabels).some((id) => !supplied.some((field) => field.id === id))) return fail("Signed application mapping must contain only the reviewed text semantics and exact unique IDs");
  const fieldMapping = supplied.map(({ id, label, type, required, options }) => ({ id, label, type, required, ...(options ? { options } : {}) }));
  const labels = Object.fromEntries(fieldMapping.map(({ id, label }) => [id, label]));
  const allowed = new Set(Object.keys(labels));
  // Chrome result/argument transport may reorder object keys. Preserve exact
  // values and array order while hashing/comparing a canonical key order.
  const canonical = (value) => {
    if (value === null || typeof value !== "object") return JSON.stringify(value);
    if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  };
  const same = (a, b) => canonical(a) === canonical(b);
  const digestPattern = /^[a-f0-9]{64}$/;
  const normalise = (value) => value.replace(/\s+/g, " ").trim();
  const bounds = { controls: 256, scripts: 128, links: 256, frames: 32, forms: 16, ids: 2048, attributes: 128, text: 200_000, value: 32_768, total: 524_288 };
  const bounded = (value, maximum = bounds.value) => {
    if (typeof value !== "string" || !value.isWellFormed() || value.length > maximum) throw new Error("Form context exceeds the reviewed capture bounds or contains invalid Unicode");
    return value;
  };
  const visible = (node) => {
    if (!node.getClientRects().length || node.closest("[hidden],[aria-hidden='true'],[inert]")) return false;
    for (let current = node; current; current = current.parentElement) {
      const style = getComputedStyle(current);
      if (style.display === "none" || ["hidden", "collapse"].includes(style.visibility) || style.opacity === "0") return false;
    }
    return true;
  };
  const path = (node) => {
    const parts = [];
    for (let current = node; current; current = current.parentElement) {
      if (parts.length >= 64) throw new Error("Form context exceeds the reviewed ancestry bound");
      parts.push([current.tagName, current.id, current.getAttribute("class") || "", current.getAttribute("style"),
        current.getAttribute("role"), Boolean(current.hidden), current.getAttribute("aria-hidden"),
        [...(current.parentElement?.children || [])].indexOf(current)]);
    }
    return parts;
  };
  const attributes = (node) => {
    if (node.attributes.length > bounds.attributes) throw new Error("Form context exceeds the reviewed attribute bound");
    return [...node.attributes].map(({ name, value }) => [name, bounded(value)]).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0);
  };
  const sha256 = async (value) => [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)))].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  const choices = new Map(fieldMapping.filter(({ type }) => type === "combobox").map((field) => [field.id, field]));
  const stableChoiceDescription = (node) => (node.getAttribute("aria-describedby") || "")
    .split(/\s+/).filter((id) => id && id !== `react-select-${node.id}-placeholder`);
  const choiceSchema = (widget, input) => {
    const clone = widget.cloneNode(true);
    // Selection/search callbacks update only these known presentation nodes.
    // Keep all other attributes, labels, structure and button behavior bound.
    for (const node of clone.querySelectorAll(".select__menu,.select__single-value,.select__placeholder")) node.remove();
    for (const node of clone.querySelectorAll("[aria-live],[role=log]")) node.textContent = "";
    for (const node of clone.querySelectorAll('input[aria-hidden="true"][tabindex="-1"]')) node.remove();
    const control = clone.querySelector(`[id="${input.id}"]`);
    for (const name of ["value", "aria-expanded", "aria-controls", "aria-activedescendant"]) control.removeAttribute(name);
    const description = stableChoiceDescription(input).join(" ");
    if (description) control.setAttribute("aria-describedby", description); else control.removeAttribute("aria-describedby");
    for (const node of clone.querySelectorAll("[class]")) {
      node.classList.remove("select__control--is-focused", "select__control--menu-is-open", "select__value-container--has-value");
      if (node.classList.contains("select__control")) {
        // Exact normal unfocused/focused styles observed on the live portal.
        // Unknown classes remain in the fingerprint and cannot be ignored.
        node.classList.remove("remix-css-13cymwt-control", "remix-css-yt0d77-control");
      }
    }
    return bounded(clone.outerHTML, bounds.text);
  };
  const read = (activeChoice = null) => {
    // Raw manual state stays in this isolated execution. Local hashes bind
    // changes, not anonymity: low-entropy answers can still be guessed.
    const privateHashes = [];
    const bindPrivate = (target, key, value) => { target[key] = ""; privateHashes.push({ target, key, value: canonical(value) }); };
    if (window !== window.top || location.origin !== origin || location.search || location.hash
      || !/^\/razorpaysoftwareprivatelimited\/jobs\/\d+$/.test(location.pathname)) throw new Error("Exact Razorpay HTTPS opening and top frame required");
    if (!identity || identity.frame_id !== 0 || typeof identity.document_id !== "string"
      || !identity.document_id || identity.document_id.length > 128) throw new Error("Chrome caller must supply the current top-frame document identity");
    // Never read password or verification-code values, even into a fingerprint.
    if (document.querySelector("input[type=password]") || [...document.querySelectorAll("input[autocomplete='one-time-code'],input[id*='security-code'],input[id*='verification-code']")].some(visible)) throw new Error("Login or verification checkpoint requires the candidate");
    if ([...document.querySelectorAll("dialog[open],[role=dialog],[contenteditable]:not([contenteditable=false])")].some(visible)) throw new Error("Unsupported modal or editable context requires the candidate");
    if ([...document.querySelectorAll("iframe")].some((node) => visible(node) && /recaptcha/i.test(node.src) && /challenge/i.test(node.title))) throw new Error("CAPTCHA challenge requires the candidate");
    const widgets = new Map();
    for (const [id, descriptor] of choices) {
      const input = document.getElementById(id), widget = input?.closest("div.select");
      if (!widget || input.getAttribute("role") !== "combobox" || widget.querySelectorAll("input[role=combobox]").length !== 1
        || !widget.querySelector(".select__value-container") || input.value !== ""
        || input.getAttribute("aria-expanded") === "true" && id !== activeChoice) throw new Error("Reviewed choice widget is unsupported or has an active search");
      const auxiliary = [...widget.querySelectorAll("input,select,textarea,button")].filter((node) => node !== input && !node.closest(".select__menu"));
      if (auxiliary.some((node) => !(node instanceof HTMLInputElement && node.type === "text" && node.getAttribute("aria-hidden") === "true" && node.tabIndex === -1
        || node instanceof HTMLButtonElement && node.type === "button"))) throw new Error("An unknown control appeared inside the reviewed choice widget");
      const selected = [...widget.querySelectorAll(".select__single-value")];
      if (selected.length > 1) throw new Error("Choice selection is ambiguous");
      const label = selected.length ? normalise(selected[0].textContent || "") : "";
      const matches = descriptor.options.filter((option) => option.label === label);
      if (label && matches.length !== 1 || !label && widget.querySelectorAll(".select__placeholder").length !== 1) throw new Error("The current choice is outside the saved employer options");
      // Greenhouse's ReactSelect widget has no named hidden option-ID input.
      // The one-to-one public schema label/value mapping binds its visible choice.
      widgets.set(id, { widget, value: label ? matches[0].value : "", schema: choiceSchema(widget, input) });
    }
    const all = [...document.querySelectorAll("input,select,textarea,button")].filter((node) =>
      ![...widgets.values()].some(({ widget }) => widget.contains(node) && !choices.has(node.id)));
    if (!all.length || all.length > bounds.controls) throw new Error("Form context exceeds the reviewed control bound");
    const duplicates = new Set();
    const identified = document.querySelectorAll("[id]");
    if (identified.length > bounds.ids || document.forms.length > bounds.forms) throw new Error("Form context exceeds the reviewed identity/form bound");
    for (const node of identified) {
      if (node.id && duplicates.has(node.id)) throw new Error("Ambiguous DOM identity");
      if (node.id) duplicates.add(node.id);
    }
    const assisted = fieldMapping.map(({ id, label: mappedLabel, type, required }) => {
      const node = document.getElementById(id);
      if (!(node instanceof HTMLInputElement) || !node.form) throw new Error("The mapped application form is incomplete");
      const label = normalise([...(node.labels || [])].map((entry) => entry.textContent).join(" ")).replace(/\s*\*\s*$/, "");
      const choice = type === "combobox";
      if (label !== mappedLabel || node.type !== (choice ? "text" : type)
        || (choice ? node.getAttribute("role") !== "combobox" : node.getAttribute("role"))
        || (choice ? ![-1, 255].includes(node.maxLength) : node.maxLength !== 255) || Boolean(node.required || node.getAttribute("aria-required") === "true") !== required
        || node.disabled || node.readOnly || !visible(node)) throw new Error("Reviewed text field metadata changed or requires manual input");
      return node;
    });
    const form = assisted[0].form;
    if (assisted.some((node) => node.form !== form)) throw new Error("Reviewed fields must share one application form");
    if ([...document.querySelectorAll("input[type=file]")].some((node) => node.files.length)) throw new Error("Selected manual attachment is unverified; finish text assistance before attachment");
    const forms = [...document.forms].map((node) => {
      const entry = { path: path(node), attributes: attributes(node), action: node.action, method: node.method };
      const clone = node.cloneNode(true);
      // Only reviewed choice subtrees contain mutable selection labels/popups.
      // Their complete supported semantics are captured as field descriptors.
      for (const id of widgets.keys()) clone.querySelector(`[id="${id}"]`)?.closest("div.select")?.remove();
      bindPrivate(entry, "text_sha256", bounded(normalise(clone.textContent), bounds.text)); return entry;
    });
    const fields = all.map((node) => {
      const labelled = (node.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean).map((id) => document.getElementById(id)?.textContent || "");
      const descriptionIds = choices.has(node.id) ? stableChoiceDescription(node) : (node.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
      const described = descriptionIds.map((id) => document.getElementById(id)?.textContent || "");
      const control = {
        id: node.id, name: node.name || "", label: bounded(normalise([...(node.labels || [])].map((entry) => entry.textContent).join(" "))),
        aria_labelled: labelled.map((value) => bounded(normalise(value))), description: described.map((value) => bounded(normalise(value))),
        type: node.tagName === "TEXTAREA" ? "textarea" : node.type, tag: node.tagName,
        // Chrome scripting dictionary transport drops null-valued properties.
        // Explicit absence sentinels keep the captured context round-trip exact.
        role: node.getAttribute("role") || "", required: Boolean(node.required || node.getAttribute("aria-required") === "true"),
        max: node.maxLength ?? -1, disabled: Boolean(node.disabled), readonly: Boolean(node.readOnly), hidden: !visible(node),
        // A controlled React input can update its value attribute after a fill.
        // Assisted values are bound by approved-prefix/expected-before checks.
        attributes: allowed.has(node.id) ? attributes(node).filter(([name]) => !(choices.has(node.id)
          ? ["value", "checked", "selected", "aria-expanded", "aria-controls", "aria-activedescendant"] : ["value", "checked", "selected"]).includes(name))
          .map(([name, value]) => [name, choices.has(node.id) && name === "aria-describedby" ? stableChoiceDescription(node).join(" ") : value]) : [],
        path: path(widgets.get(node.id)?.widget || node), form: node.form ? [...document.forms].indexOf(node.form) : -1,
        options: node instanceof HTMLSelectElement ? [...node.options].map((option) => ({ value: bounded(option.value), label: bounded(option.label), disabled: option.disabled })) : [],
      };
      // Assisted values change only under separately reviewed expected-before and
      // approved-prefix checks in the caller. Every manual value/state is bound.
      const controlled = node.getAttribute("aria-controls");
      const target = controlled ? document.getElementById(controlled) : null;
      const controlledContext = target ? { path: path(target), attributes: attributes(target), text: bounded(normalise(target.textContent || "")) } : false;
      if (!allowed.has(node.id)) bindPrivate(control, "manual_value_sha256", {
        value: node.type === "file" ? "" : bounded(node.value || ""), selected_count: node.type === "file" ? node.files.length : 0,
        checked: Boolean(node.checked), selected_value: bounded(node.dataset.selectedValue || ""),
        text: bounded(normalise(node.textContent || "")), attributes: attributes(node),
        selected_options: node instanceof HTMLSelectElement ? [...node.options].map((option) => option.selected) : [], controlled_context: controlledContext,
      });
      else if (controlled && !choices.has(node.id)) control.controlled_context = controlledContext;
      if (choices.has(node.id)) {
        control.options = choices.get(node.id).options;
        bindPrivate(control, "widget_schema_sha256", widgets.get(node.id).schema);
      }
      return control;
    });
    const scripts = [...document.scripts]; const links = [...document.querySelectorAll("a[href],link[href]")]; const frames = [...document.querySelectorAll("iframe")];
    if (scripts.length > bounds.scripts || links.length > bounds.links || frames.length > bounds.frames) throw new Error("Form context exceeds the reviewed script/link/frame bound");
    const context = {
      adapter_version: adapterVersion, origin, url: location.href, tenant, opening: location.pathname.split("/").at(-1),
      document_id: identity.document_id, frame_id: identity.frame_id, field_mapping: fieldMapping, title: bounded(document.title), forms, fields,
      scripts: scripts.map((node) => { const entry = { attributes: attributes(node), src: node.src }; bindPrivate(entry, "text_sha256", bounded(node.textContent, bounds.text)); return entry; }),
      links: links.map((node) => { const entry = { path: path(node) }; bindPrivate(entry, "descriptor_sha256", { attributes: attributes(node), href: node.href, text: bounded(normalise(node.textContent || "")) }); return entry; }),
      frames: frames.map((node) => { const entry = { path: path(node), hidden: !visible(node) }; bindPrivate(entry, "descriptor_sha256", { attributes: attributes(node), src: node.src }); return entry; }),
      recipient_disclosure: { version: "razorpay-public-review-2026-10-09", email: { event: "blur", recipient, before_submit: true },
        manual_attachment: "Selecting a file may upload it before final submission; the storage recipient was not resolved.",
        limitation: "DOM script metadata and declared recipients do not verify live egress. Cancellation cannot recall earlier callbacks." },
    };
    const serialized = canonical({ context, private_values: privateHashes.map(({ key, value }) => [key, value]) });
    if (new TextEncoder().encode(serialized).length > bounds.total) throw new Error("Form context exceeds the reviewed total byte bound");
    const assistedValues = Object.fromEntries(assisted.map((node) => [node.id, bounded(widgets.get(node.id)?.value ?? node.value, 255)]));
    return { context, serialized, fields, form, assisted, assistedValues, privateHashes };
  };
  let current;
  try { current = read(); } catch (error) { return fail(error.message); }
  await Promise.all(current.privateHashes.map(async ({ target, key, value }) => { target[key] = await sha256(value); }));
  const contextDigest = await sha256(canonical(current.context));
  // Web Crypto yields. Recheck all state after it returns, before any events.
  try { const latest = read(); if (latest.serialized !== current.serialized) return fail("Form context changed during inspection"); current.assisted = latest.assisted; current.assistedValues = latest.assistedValues; } catch (error) { return fail(error.message); }
  const inspection = {
    outcome: "inspected", ...(version === 2 ? { review_protocol_version: 2 } : {}), adapter_id: adapterVersion, origin, url: location.href, frame_id: 0, document_id: identity.document_id,
    tenant_id: tenant, employer_key: "razorpay", canonical_opening_key: current.context.opening,
    form_version: `${adapterVersion}:${contextDigest}`, context_digest: contextDigest, context: current.context, fields: current.fields,
    assisted_fields: current.fields.filter((field) => allowed.has(field.id)), manual_fields: current.fields.filter((field) => !allowed.has(field.id)),
    assisted_values: current.assistedValues,
    field_mapping: fieldMapping,
    required_controls: current.fields.filter((field) => field.required).length, checkpoint: null, status: "manual_handoff",
    message: version === 2 ? "Only explicitly reviewed text, phone and supported choices can be filled. Country, employment, unknown controls, attachment and final submission remain manual. No application receipt or charge is established."
      : "Up to seven reviewed plain text fields only. Phone, Country, employment, choices, attachment and final submission remain manual. No application receipt or charge is established.",
  };
  if (!action) return inspection;
  if (!expected || expected.outcome !== "inspected" || !digestPattern.test(expected.context_digest || "")
    || action.context_digest !== expected.context_digest) return fail("Exact reviewed context is missing or action digest changed");
  if (contextDigest !== expected.context_digest) return fail("Reviewed form context digest changed");
  if (!same(current.context, expected.context)) return fail("Reviewed form context values changed");
  if (inspection.form_version !== expected.form_version) return fail("Reviewed form version changed");
  if (expected.document_id !== identity.document_id || action.document_id !== identity.document_id || action.frame_id !== 0) return fail("Chrome document or frame changed");
  if (["origin", "url", "tenant_id", "employer_key", "canonical_opening_key"].some((key) => inspection[key] !== expected[key])) return fail("Reviewed employer destination changed");
  const reviewed = expected.reviewed_answers;
  if (!reviewed || typeof reviewed !== "object" || Array.isArray(reviewed) || !Object.keys(reviewed).length || Object.keys(reviewed).length > (version === 2 ? 12 : 7)
    || Object.keys(reviewed).some((key) => !allowed.has(key) || typeof reviewed[key] !== "string" || !reviewed[key].isWellFormed() || !reviewed[key].trim() || reviewed[key].length > 255)
    || !allowed.has(action.field_id) || !Object.hasOwn(reviewed, action.field_id) || action.value !== reviewed[action.field_id]
    || typeof action.expected_before !== "string" || !Number.isSafeInteger(action.deadline) || Date.now() >= action.deadline || action.deadline - Date.now() > 10000
    || action.cancelled === true || !digestPattern.test(expected.review_digest || "") || action.review_digest !== expected.review_digest
    || !digestPattern.test(expected.reviewed_answers_sha256 || "") || action.reviewed_answers_sha256 !== expected.reviewed_answers_sha256) return fail("Exact approved review, value, expected-before state and short current action are required");
  const reviewFingerprint = await sha256(canonical({
    context_digest: contextDigest, reviewed_answers: reviewed,
  }));
  if (reviewFingerprint !== expected.reviewed_answers_sha256) return fail("Approved answer subset changed after review");
  try { const latest = read(); if (latest.serialized !== current.serialized) return fail("Form context changed while checking the approved review"); current.assisted = latest.assisted; current.assistedValues = latest.assistedValues; } catch (error) { return fail(error.message); }
  if (Date.now() >= action.deadline) return fail("The reviewed action expired before disclosure");
  const expectedValues = expected.current_assisted_values;
  if (!expectedValues || typeof expectedValues !== "object" || Array.isArray(expectedValues)
    || Object.keys(expectedValues).length !== allowed.size || Object.keys(expectedValues).some((id) => !allowed.has(id)
      || typeof expectedValues[id] !== "string" || !expectedValues[id].isWellFormed() || expectedValues[id].length > 255)
    || !same(current.assistedValues, expectedValues)) return fail("Assisted values changed from the reviewed expected-before and filled prefix");
  const control = document.getElementById(action.field_id);
  if (!current.assisted.includes(control) || current.assistedValues[action.field_id] !== action.expected_before) return fail("Reviewed control expected-before value changed");
  if (control.id === "email" && (!action.email_blur_disclosure || action.email_blur_disclosure.acknowledged !== true
    || action.email_blur_disclosure.event !== "blur" || action.email_blur_disclosure.recipient !== recipient
    || action.email_blur_disclosure.context_digest !== contextDigest)) return fail("Review and acknowledge the bound email validation disclosure before filling");
  if (version === 2 && (action.review_protocol_version !== 2 || action.control_type !== fieldMapping.find(({ id }) => id === action.field_id).type)) return fail("The typed control action changed");
  if (choices.has(control.id)) {
    const descriptor = choices.get(control.id), option = descriptor.options.find(({ value }) => value === action.value);
    if (!option || action.option_label !== option.label) return fail("The exact reviewed option changed");
    // Opening a dropdown is itself part of the explicitly reviewed compound
    // action: page callbacks can disclose/autosave before final submission.
    // No await separates the final checks above from this first event.
    control.click();
    let selected = false;
    while (Date.now() < action.deadline) {
      let latest;
      try { latest = read(control.id); } catch (error) { return { outcome: "unknown", field_id: control.id, reason: error.message }; }
      if (latest.serialized !== current.serialized || !same(latest.assistedValues, expectedValues)) return { outcome: "unknown", field_id: control.id, reason: "Form or answers changed while opening the reviewed choice" };
      const listId = control.getAttribute("aria-controls"), list = listId ? document.getElementById(listId) : null;
      if (list && list.getAttribute("role") === "listbox" && control.closest("div.select").contains(list)) {
        const options = [...list.querySelectorAll("[role=option]")].filter(visible);
        const labelSet = new Set(options.map((node) => normalise(node.textContent || "")));
        if (labelSet.size !== options.length || options.some((node) => !descriptor.options.some(({ label }) => label === normalise(node.textContent || "")))) return { outcome: "unknown", field_id: control.id, reason: "The employer option list changed" };
        const matches = options.filter((node) => normalise(node.textContent || "") === option.label && node.getAttribute("aria-disabled") !== "true" && !node.disabled);
        if (matches.length === 1) {
          if (Date.now() >= action.deadline) break;
          matches[0].click(); selected = true; break;
        }
      }
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    if (!selected) return { outcome: "unknown", field_id: control.id, reason: "The reviewed option did not become available before expiry" };
    try {
      const latest = read();
      if (latest.serialized !== current.serialized || !same(latest.assistedValues, { ...expectedValues, [control.id]: action.value })) return { outcome: "unknown", field_id: control.id, reason: "The selected choice or conditional form changed" };
    } catch (error) { return { outcome: "unknown", field_id: control.id, reason: error.message }; }
    return { outcome: "filled_only", field_id: control.id, message: "One explicitly reviewed choice selected. Attachment and final submission remain manual. No application fee was charged." };
  }
  // No await between the final synchronous context/value checks and mutation.
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(control, action.value);
  control.dispatchEvent(new Event("input", { bubbles: true }));
  control.dispatchEvent(new Event("change", { bubbles: true }));
  if (control.id === "email") control.dispatchEvent(new FocusEvent("blur"));
  const manualHandoff = current.fields.filter((field) => field.required && (!allowed.has(field.id)
    || !Object.hasOwn(reviewed, field.id) || document.getElementById(field.id)?.value !== reviewed[field.id])).map(({ id, label, hidden }) => ({ id, label, hidden }));
  return { outcome: control.value === action.value ? "filled_only" : "unknown", field_id: control.id, manual_handoff: manualHandoff,
    message: "One approved text answer filled only. Complete remaining manual controls, attachment and final submission yourself. No employer receipt or application charge is established." };
}
