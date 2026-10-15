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
  if (![1, 2, 3, 4].includes(version) || !Number.isSafeInteger(version)) return fail("Unsupported reviewed form protocol");
  const adapterVersion = version === 4 ? "razorpay-greenhouse-submit-v4" : version === 3 ? "razorpay-greenhouse-attachment-v3" : version === 2 ? "razorpay-greenhouse-controls-v2" : "razorpay-greenhouse-text-v1";
  const recipient = "https://email-address-validator.us.greenhouse.io/address/validate";
  const baseLabels = { first_name: "First Name", last_name: "Last Name", email: "Email" };
  const customLabels = new Set(["LinkedIn Profile", "Website", "Total Years of Experience", "Current Location"]);
  const choiceLabels = new Set(["Current Career Stage", "Gender", "Current Industry", "Current Designation"]);
  const hostedText = (field) => /^(company-name|title|start-date-year|end-date-year)-[0-4]$/.test(field.id)
    && field.label === (field.id.startsWith("company-name-") ? "Company name" : field.id.startsWith("title-") ? "Title" : field.id.startsWith("start-") ? "Start date year" : "End date year");
  const hostedChoice = (field) => field.id === "country" && field.label === "Country"
    || /^(start|end)-date-month-[0-4]$/.test(field.id) && field.label === (field.id.startsWith("start-") ? "Start date month" : "End date month");
  const validChoice = (field) => version >= 2 && field.type === "combobox" && (choiceLabels.has(field.label)
    && /^question_\d{1,20}$/.test(field.id) || hostedChoice(field)) && Array.isArray(field.options) && field.options.length >= 1 && field.options.length <= (field.id === "country" ? 244 : 64)
    && field.options.every((option) => option && Object.keys(option).sort().join(",") === "label,value"
      && [option.value, option.label].every((value) => typeof value === "string" && value.length > 0 && value.length <= 255 && value.isWellFormed()))
    && new Set(field.options.map(({ value }) => value)).size === field.options.length
    && new Set(field.options.map(({ label }) => label)).size === field.options.length;
  const supplied = identity?.field_mapping;
  if (!Array.isArray(supplied) || supplied.length < 3 || supplied.length > (version >= 2 ? 64 : 7)
    || supplied.some((field) => !field || typeof field !== "object" || Array.isArray(field)
      || Object.keys(field).sort().join(",") !== (field.type === "combobox" ? "id,label,options,required,type" : "id,label,required,type")
      || typeof field.id !== "string" || field.id.length > 128 || typeof field.label !== "string" || typeof field.required !== "boolean"
      || !(field.type === "text" && (Object.hasOwn(baseLabels, field.id) ? field.label === baseLabels[field.id] : /^question_\d+$/.test(field.id) && customLabels.has(field.label) || [2, 4].includes(version) && hostedText(field))
        || version >= 2 && field.type === "tel" && field.id === "phone" && field.label === "Phone" || validChoice(field)
        || [2, 4].includes(version) && field.type === "checkbox" && /^current-role-[0-4]_1$/.test(field.id) && field.label === "Current role" && field.required === false
        || [2, 4].includes(version) && field.type === "button" && /^employment-add-[1-4]$/.test(field.id) && field.label === "Add another" && field.required === false))
    || new Set(supplied.map(({ id }) => id)).size !== supplied.length
    || new Set(supplied.filter((field) => !hostedText(field) && !hostedChoice(field) && !["checkbox", "button"].includes(field.type)).map(({ label }) => label)).size !== supplied.filter((field) => !hostedText(field) && !hostedChoice(field) && !["checkbox", "button"].includes(field.type)).length
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
  const addDescriptor = fieldMapping.find(({ type }) => type === "button");
  const addButtons = () => [...document.querySelectorAll(".employment--container > button.add-another-button")].filter((node) => node.type === "button" && normalise(node.textContent || "") === "Add another");
  const getControl = (id) => id === addDescriptor?.id ? (addButtons().length === 1 ? addButtons()[0] : null) : document.getElementById(id);
  const controlId = (node) => addDescriptor && node === getControl(addDescriptor.id) ? addDescriptor.id : node.id;
  const discoverRows = () => {
    const companies = [...document.querySelectorAll('input[id^="company-name-"]')];
    if (!companies.length) return null;
    if (companies.length > 5 || companies.some((node, index) => node.id !== `company-name-${index}`)) throw new Error("Unsupported employment row identities require manual review");
    const rows = companies.map((node, index) => {
      const role = document.getElementById(`current-role-${index}_1`);
      const ids = [`title-${index}`, `start-date-month-${index}`, `start-date-year-${index}`, ...(!role?.checked ? [`end-date-month-${index}`, `end-date-year-${index}`] : [])];
      if (!(role instanceof HTMLInputElement) || role.type !== "checkbox" || ids.some((id) => !document.getElementById(id))) return null;
      return { index, current_role: role.checked };
    });
    return rows.some((row) => row === null) ? null : rows;
  };
  const resumeState = () => {
    const groups = [...document.querySelectorAll('.file-upload[aria-labelledby="upload-label-resume"]')];
    if (groups.length !== 1) return null;
    const group = groups[0], label = document.getElementById("upload-label-resume");
    if (!visible(group) || !label || normalise(label.textContent || "").replace(/\s*\*\s*$/, "") !== "Resume/CV") throw new Error("The known Resume/CV upload group changed");
    const names = [...group.querySelectorAll('.file-upload__filename p.body__secondary')];
    const removals = [...group.querySelectorAll('button[aria-label="Remove file"]')];
    const progress = group.querySelector('[role="progressbar"],progress');
    const error = group.querySelector('[role="alert"],.file-upload__error,.error');
    const input = group.querySelector('input#resume[type="file"]');
    if (error && normalise(error.textContent || "")) throw new Error("The portal reports a resume upload error");
    if (names.length === 1 && removals.length === 1 && !input && !progress) return { group, input:null, status:"portal_reports_uploaded", filename:normalise(names[0].textContent || "") };
    if (progress && !input && !names.length && !removals.length) return { group, input:null, status:"uploading", filename:"" };
    if (input && !names.length && !removals.length && !progress) {
      if (group.querySelectorAll('input[type="file"]').length !== 1 || !input.classList.contains("visually-hidden")
        || ![...(input.labels || [])].some((node) => visible(node) && normalise(node.textContent || "") === "Attach")) throw new Error("The known empty resume input changed");
      return { group, input, status:"empty", filename:"" };
    }
    throw new Error("Unsupported or ambiguous Resume/CV upload state requires candidate inspection");
  };
  const continuityKey = "__hirewiz_resume_continuity_v1";
  const continuity = () => {
    const record = globalThis[continuityKey], state = resumeState();
    if (!record || record.invalid || record.phase !== "complete" || !state || state.status !== "portal_reports_uploaded"
      || state.group !== record.group || state.filename !== record.filename || state.group.querySelector('.file-upload__filename p.body__secondary') !== record.filenameNode) return null;
    return {review_id:record.review_id,action_id:record.action_id,artifact_sha256:record.artifact_sha256,document_id:record.document_id,target_url:record.target_url};
  };
  const monitorSelection = (group, approved) => {
    globalThis[continuityKey]?.dispose();
    const record={...approved,group,phase:"uploading",invalid:false,filenameNode:null};
    let dispatching=true;
    const invalidate=()=>{record.invalid=true;};
    const input=(event)=>{if(!dispatching && event.target instanceof HTMLInputElement && event.target.type==="file")invalidate();};
    const click=(event)=>{if(group.contains(event.target) && event.target.closest('button[aria-label="Remove file"],label[for="resume"]'))invalidate();};
    const observer=new MutationObserver(()=>{
      if(record.invalid)return;
      try {
        const state=resumeState();
        if(!state || state.group!==group) return invalidate();
        if(record.phase==="complete") return invalidate();
        if(state.status==="portal_reports_uploaded" && state.filename===record.filename){record.phase="complete";record.filenameNode=group.querySelector('.file-upload__filename p.body__secondary');}
        else if(state.status!=="uploading")invalidate();
      }catch{invalidate();}
    });
    observer.observe(group,{subtree:true,childList:true,attributes:true,characterData:true});
    group.closest('form').addEventListener('input',input,true); group.closest('form').addEventListener('change',input,true); group.addEventListener('click',click,true);
    record.dispose=()=>{observer.disconnect();group.closest('form')?.removeEventListener('input',input,true);group.closest('form')?.removeEventListener('change',input,true);group.removeEventListener('click',click,true);};
    record.dispatched=()=>{dispatching=false;};
    Object.defineProperty(globalThis,continuityKey,{value:record,configurable:true});
    return record;
  };
  const read = (activeChoice = null, selectedAttachment = null) => {
    const upload = [3, 4].includes(version) ? resumeState() : null;
    if (version === 4 && (!upload || upload.status !== "portal_reports_uploaded")) throw new Error("Wait for the portal to report a completed resume upload before final review");
    if (version === 3 && upload && !selectedAttachment && upload.status !== "empty") throw new Error("Resume input must be empty before attachment approval");
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
      !(upload && upload.group.contains(node)) && ![...widgets.values()].some(({ widget }) => widget.contains(node) && !choices.has(node.id)));
    if (!all.length || all.length > bounds.controls) throw new Error("Form context exceeds the reviewed control bound");
    const duplicates = new Set();
    const identified = document.querySelectorAll("[id]");
    if (identified.length > bounds.ids || document.forms.length > bounds.forms) throw new Error("Form context exceeds the reviewed identity/form bound");
    for (const node of identified) {
      if (node.id && duplicates.has(node.id)) throw new Error("Ambiguous DOM identity");
      if (node.id) duplicates.add(node.id);
    }
    const assisted = fieldMapping.map(({ id, label: mappedLabel, type, required }) => {
      const node = getControl(id);
      if (!(node instanceof HTMLInputElement || type === "button" && node instanceof HTMLButtonElement) || !node.form) throw new Error("The mapped application form is incomplete");
      const label = type === "button" ? normalise(node.textContent || "") : normalise([...(node.labels || [])].map((entry) => entry.textContent).join(" ")).replace(/\s*\*\s*$/, "");
      const choice = type === "combobox";
      if (label !== mappedLabel || node.type !== (choice ? "text" : type)
        || (choice ? node.getAttribute("role") !== "combobox" : node.getAttribute("role"))
        || (choice ? ![-1, 255].includes(node.maxLength) : type === "checkbox" || type === "button" ? false : node.maxLength !== (/^(start|end)-date-year-[0-4]$/.test(id) ? 4 : 255)) || Boolean(node.required || node.getAttribute("aria-required") === "true") !== required
        || node.disabled || node.readOnly || !visible(node)) throw new Error("Reviewed text field metadata changed or requires manual input");
      if (type === "checkbox" && (node.name !== id.slice(0, -2) || node.value !== "1")) throw new Error("The reviewed current-role checkbox changed");
      if (type === "button" && discoverRows()?.length !== Number(id.split("-").at(-1))) throw new Error("Employment row count changed before Add another");
      return node;
    });
    const form = assisted[0].form;
    if (assisted.some((node) => node.form !== form)) throw new Error("Reviewed fields must share one application form");
    const fileInputs = [...document.querySelectorAll("input[type=file]")];
    if (version === 3) {
      const resume = document.getElementById("resume");
      if (!(selectedAttachment && upload && !resume) && (fileInputs.length !== 1 || fileInputs[0] !== resume || !(resume instanceof HTMLInputElement)
        || resume.form !== form || resume.multiple || resume.disabled || !(upload?.input === resume || visible(resume))
        || !upload && normalise([...(resume.labels || [])].map((node) => node.textContent).join(" ")).replace(/\s*\*\s*$/, "") !== "Resume/CV")) throw new Error("One known visible Resume/CV file input in the reviewed form is required");
      const retainedUpload = [...form.querySelectorAll('input[type="hidden"]')].some((node) => /resume|attachment|upload/i.test(`${node.id} ${node.name}`) && Boolean(node.value))
        || [form, ...form.querySelectorAll('[data-attachment-id],[data-upload-id],[data-file-id]')].some((node) => ["data-attachment-id", "data-upload-id", "data-file-id"].some((name) => Boolean(node.getAttribute(name))))
        || [...form.querySelectorAll('button,[role="button"]')].some((node) => {
          const description = `${normalise(node.textContent || "")} ${node.getAttribute("aria-label") || ""}`;
          return /\b(remove|replace|delete)\b/i.test(description) && /\b(resume|attachment|file)\b/i.test(description);
        }) || [...form.querySelectorAll('a[href]')].some((node) => node.hasAttribute("download") || /\.(pdf|docx)(?:[?#]|$)/i.test(node.href));
      if (retainedUpload && !(selectedAttachment && upload)) throw new Error("An existing or ambiguous uploaded attachment must be handled manually");
    }
    if (fileInputs.some((node) => node.files.length && !(version === 3 && selectedAttachment === node && node.files.length === 1))) throw new Error("Selected manual attachment is unverified; finish assistance before attachment");
    const forms = [...document.forms].map((node) => {
      const entry = { path: path(node), attributes: attributes(node), action: node.action, method: node.method };
      const clone = node.cloneNode(true);
      if (upload) clone.querySelector('.file-upload[aria-labelledby="upload-label-resume"]')?.remove();
      // Only reviewed choice subtrees contain mutable selection labels/popups.
      // Their complete supported semantics are captured as field descriptors.
      for (const id of widgets.keys()) clone.querySelector(`[id="${id}"]`)?.closest("div.select")?.remove();
      bindPrivate(entry, "text_sha256", bounded(normalise(clone.textContent), bounds.text)); return entry;
    });
    const fields = all.map((node) => {
      const id = controlId(node);
      const labelled = (node.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean).map((id) => document.getElementById(id)?.textContent || "");
      const descriptionIds = choices.has(node.id) ? stableChoiceDescription(node) : (node.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
      const described = descriptionIds.map((id) => document.getElementById(id)?.textContent || "");
      const control = {
        id, name: node.name || "", label: bounded(normalise([...(node.labels || [])].map((entry) => entry.textContent).join(" "))),
        aria_labelled: labelled.map((value) => bounded(normalise(value))), description: described.map((value) => bounded(normalise(value))),
        type: node.tagName === "TEXTAREA" ? "textarea" : node.type, tag: node.tagName,
        // Chrome scripting dictionary transport drops null-valued properties.
        // Explicit absence sentinels keep the captured context round-trip exact.
        role: node.getAttribute("role") || "", required: Boolean(node.required || node.getAttribute("aria-required") === "true"),
        max: node.maxLength ?? -1, disabled: Boolean(node.disabled), readonly: Boolean(node.readOnly), hidden: !visible(node),
        // A controlled React input can update its value attribute after a fill.
        // Assisted values are bound by approved-prefix/expected-before checks.
        attributes: allowed.has(controlId(node)) ? attributes(node).filter(([name]) => !(choices.has(node.id)
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
      if (!allowed.has(controlId(node))) bindPrivate(control, "manual_value_sha256", {
        value: node.type === "file" ? "" : bounded(node.value || ""), selected_count: node.type === "file" && selectedAttachment !== node ? node.files.length : 0,
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
    const scripts = [...document.scripts]; const links = [...document.querySelectorAll("a[href],link[href]")].filter((node) => !upload?.group.contains(node)); const frames = [...document.querySelectorAll("iframe")];
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
    const attachmentSchema = upload && version === 3 ? selectedAttachment ? current.attachmentSchema : bounded(upload.group.outerHTML,bounds.text) : null;
    if (attachmentSchema !== null) bindPrivate(context,"resume_upload_sha256",attachmentSchema);
    if (upload && version === 4) context.resume_upload = { attributes:attributes(upload.group), html:bounded(upload.group.outerHTML,bounds.text) };
    const serialized = canonical({ context, private_values: privateHashes.map(({ key, value }) => [key, value]) });
    if (new TextEncoder().encode(serialized).length > bounds.total) throw new Error("Form context exceeds the reviewed total byte bound");
    const assistedValues = Object.fromEntries(assisted.map((node) => {
      const id = controlId(node);
      return [id, bounded(widgets.get(id)?.value ?? (node.type === "checkbox" ? String(node.checked) : node.type === "button" ? String(discoverRows()?.length) : node.value), 255)];
    }));
    return { context, serialized, fields, form, assisted, assistedValues, privateHashes, upload, attachmentSchema };
  };
  let current;
  try { current = read(); } catch (error) { return fail(error.message); }
  await Promise.all(current.privateHashes.map(async ({ target, key, value }) => { target[key] = await sha256(value); }));
  const contextDigest = await sha256(canonical(current.context));
  // Web Crypto yields. Recheck all state after it returns, before any events.
  try { const latest = read(); if (latest.serialized !== current.serialized) return fail("Form context changed during inspection"); current.assisted = latest.assisted; current.assistedValues = latest.assistedValues; } catch (error) { return fail(error.message); }
  const inspection = {
    outcome: "inspected", ...(version > 1 ? { review_protocol_version: version } : {}), ...(version === 3 ? { attachment: { input_id: "resume", expected_empty: true } } : {}), adapter_id: adapterVersion, origin, url: location.href, frame_id: 0, document_id: identity.document_id,
    tenant_id: tenant, employer_key: "razorpay", canonical_opening_key: current.context.opening,
    form_version: `${adapterVersion}:${contextDigest}`, context_digest: contextDigest, context: current.context, fields: current.fields,
    assisted_fields: current.fields.filter((field) => allowed.has(field.id)), manual_fields: current.fields.filter((field) => !allowed.has(field.id)),
    assisted_values: current.assistedValues,
    field_mapping: fieldMapping,
    ...([2, 4].includes(version) && discoverRows() ? { hosted_rows: discoverRows() } : {}),
    required_controls: current.fields.filter((field) => field.required).length, checkpoint: null, status: "manual_handoff",
    message: version === 2 ? "Only explicitly reviewed text, phone and supported choices can be filled. Only listed values and actions are authorized. Changed or unknown controls, attachment and final submission require separate action. No application receipt or charge is established."
      : "Up to seven reviewed plain text fields only. Phone, Country, employment, choices, attachment and final submission remain manual. No application receipt or charge is established.",
  };
  if (version === 4) {
    const buttons = [...current.form.querySelectorAll('button[type="submit"]')].filter(visible);
    const submit = buttons.length === 1 ? buttons[0] : null;
    const nonAnswers = new Set(["submit", "button", "file", "hidden"]);
    const unknown = current.fields.filter((field) => !allowed.has(field.id) && !nonAnswers.has(field.type));
    // Unknown answers/consents are not silently omitted from the final review.
    // The candidate must handle them, then a supported contract is required.
    const requiredMissing = fieldMapping.some((field) => field.required && field.type !== "button" && !current.assistedValues[field.id]);
    const submitReady = submit && !submit.disabled && normalise(submit.textContent || "") === "Submit application"
      && current.form.id === "application-form" && current.form.method === "get" && current.form.action === location.href
      && !unknown.length && !requiredMissing && current.form.checkValidity();
    inspection.manual_fields = unknown;
    inspection.ready = Boolean(submitReady);
    inspection.upload = { ...identity.upload, filename: current.upload.filename, status: current.upload.status };
    inspection.upload_continuity=continuity();
    if (!inspection.upload_continuity || inspection.upload_continuity.review_id!==identity.upload?.review_id
      || inspection.upload_continuity.action_id!==identity.upload?.action_id || inspection.upload_continuity.document_id!==identity.document_id
      || inspection.upload_continuity.target_url!==location.href) inspection.ready=false;
    inspection.submit_control = { form_id:"application-form", type:"submit", label:"Submit application" };
    if (action && !inspection.ready) return fail("Complete required values and candidate checkpoints before a supported final review");
  }
  if (!action) return inspection;
  if (!expected || expected.outcome !== "inspected" || !digestPattern.test(expected.context_digest || "")
    || action.context_digest !== expected.context_digest) return fail("Exact reviewed context is missing or action digest changed");
  if (contextDigest !== expected.context_digest) return fail("Reviewed form context digest changed");
  if (!same(current.context, expected.context)) return fail("Reviewed form context values changed");
  if (inspection.form_version !== expected.form_version) return fail("Reviewed form version changed");
  if (expected.document_id !== identity.document_id || action.document_id !== identity.document_id || action.frame_id !== 0) return fail("Chrome document or frame changed");
  if (["origin", "url", "tenant_id", "employer_key", "canonical_opening_key"].some((key) => inspection[key] !== expected[key])) return fail("Reviewed employer destination changed");
  if (version === 4) {
    if (action.review_protocol_version !== 4 || action.control_type !== "submit" || action.field_id !== "final-submit"
      || !same(action.allowed_actions,["submit"]) || action.fee_credits !== 20 || !digestPattern.test(action.native_proof_sha256 || "")
      || !digestPattern.test(expected.review_digest || "") || action.review_digest !== expected.review_digest
      || !same(current.assistedValues,expected.current_assisted_values) || !same(inspection.upload,action.upload)
      || !same(inspection.upload_continuity,action.upload_continuity) || action.artifact_sha256!==inspection.upload_continuity?.artifact_sha256
      || !same(inspection.submit_control,action.submit_control) || !same(inspection.submit_control,expected.submit_control)
      || !Number.isSafeInteger(action.deadline) || action.deadline <= Date.now() || action.deadline-Date.now()>10000 || action.cancelled === true
      || !digestPattern.test(expected.reviewed_answers_sha256 || "") || action.reviewed_answers_sha256 !== expected.reviewed_answers_sha256
      || !same(expected.reviewed_answers,Object.fromEntries(fieldMapping.filter((field) => field.type !== "button").map((field) => [field.id,current.assistedValues[field.id]])))) return fail("A distinct exact current final-submit lease and complete approved answers are required");
    const fingerprint = await sha256(canonical({context_digest:contextDigest,reviewed_answers:expected.reviewed_answers}));
    if (fingerprint !== expected.reviewed_answers_sha256) return fail("The final reviewed answers changed");
    let latest; try { latest=read(); } catch (error) { return fail(error.message); }
    if (latest.serialized !== current.serialized || !same(latest.assistedValues,expected.current_assisted_values)
      || !same(latest.upload && {filename:latest.upload.filename,status:latest.upload.status},{filename:inspection.upload.filename,status:inspection.upload.status})
      || !same(continuity(),inspection.upload_continuity) || Date.now() >= action.deadline || action.cancelled === true) return fail("The portal or final-submit lease changed before the click");
    const button=[...latest.form.querySelectorAll('button[type="submit"]')].filter(visible);
    if (button.length !== 1 || button[0].disabled || normalise(button[0].textContent || "") !== "Submit application" || !latest.form.checkValidity()) return fail("The reviewed submit button or required form state changed");
    // Exact native button click only. Greenhouse owns its fetch, CAPTCHA and
    // validation callbacks. No raw form.submit(), synthesized POST or retry.
    button[0].click();
    return {outcome:"click_dispatched",field_id:"final-submit",employer_confirmed:false,message:"One approved submit click dispatched. Employer receipt remains unverified; never resend automatically."};
  }
  if (version === 3) {
    const artifact = expected.artifact;
    const ownedUpload = same(action.allowed_actions,["upload_resume"]) && action.native_upload_completed === true
      && action.upload_plan?.transport === "greenhouse-owned-upload-browser-https-v2" && same(action.upload_plan,expected.upload_plan);
    if (action.review_protocol_version !== 3 || action.control_type !== "file" || action.field_id !== "resume"
      || !(same(action.allowed_actions,["attach"]) && !action.upload_plan || ownedUpload) || !same(action.attachment, { input_id: "resume", expected_empty: true })
      || !same(inspection.attachment, action.attachment) || !same(artifact, action.artifact)
      || !digestPattern.test(expected.review_digest || "") || action.review_digest !== expected.review_digest
      || !artifact || !digestPattern.test(artifact.sha256 || "") || action.artifact_sha256 !== artifact.sha256
      || !Number.isSafeInteger(artifact.size_bytes) || artifact.size_bytes <= 0 || artifact.size_bytes > 10 * 1024 * 1024
      || !["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"].includes(artifact.media_type)
      || typeof artifact.filename !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$/.test(artifact.filename)
      || !Number.isSafeInteger(action.deadline) || action.deadline <= Date.now() || action.deadline - Date.now() > 10000
      || action.cancelled === true || typeof action.artifact_base64 !== "string" || !/^[A-Za-z0-9_-]+$/.test(action.artifact_base64)
      || action.artifact_base64.length > Math.ceil(10 * 1024 * 1024 * 4 / 3)) return fail("The exact approved sealed file and current attachment lease are required");
    let bytes;
    try { bytes = Uint8Array.from(atob(action.artifact_base64.replaceAll("-", "+").replaceAll("_", "/")), (character) => character.charCodeAt(0)); }
    catch { return fail("The approved file bytes are malformed"); }
    if (bytes.length !== artifact.size_bytes) return fail("The approved file size changed");
    const hash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))].map((byte) => byte.toString(16).padStart(2, "0")).join("");
    if (hash !== artifact.sha256) return fail("The approved file hash changed");
    const file = new File([bytes], artifact.filename, { type: artifact.media_type, lastModified: 0 });
    const transfer = new DataTransfer(); transfer.items.add(file);
    bytes.fill(0); action.artifact_base64="";
    let latest;
    try { latest = read(); } catch (error) { return fail(error.message); }
    if (latest.serialized !== current.serialized || !same(latest.assistedValues, expected.current_assisted_values)
      || Date.now() >= action.deadline || action.cancelled === true) return fail("The reviewed form, answers or attachment lease changed before selection");
    const control = document.getElementById("resume");
    if (Date.now() >= action.deadline || action.cancelled === true) return fail("The attachment lease expired before file selection");
    // No await from the final context/deadline checks through the only mutation.
    const monitor=current.upload ? monitorSelection(current.upload.group,{review_id:action.review_id,action_id:action.action_id,artifact_sha256:artifact.sha256,
      filename:artifact.filename,document_id:identity.document_id,target_url:location.href}) : null;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "files").set.call(control, transfer.files);
    const selectedBefore = control.files[0];
    if (control.files.length !== 1 || selectedBefore !== file || selectedBefore.name !== artifact.filename || selectedBefore.size !== artifact.size_bytes || selectedBefore.type !== artifact.media_type) return {outcome:"unknown",field_id:"resume",reason:"The exact File selection could not be observed"};
    if (ownedUpload) {
      monitor?.dispatched();
      return {outcome:"selected_only",field_id:"resume",selection_reported:true,message:"Exact File selected without dispatching the portal uploader; the known component callback requires the original native upload proof."};
    }
    control.dispatchEvent(new Event("input", { bubbles: true }));
    control.dispatchEvent(new Event("change", { bubbles: true }));
    monitor?.dispatched();
    try {
      if (!control.isConnected && current.upload) {
        const after=read(null,control);
        if (!after.upload || !["uploading","portal_reports_uploaded"].includes(after.upload.status)
          || after.upload.status === "portal_reports_uploaded" && after.upload.filename !== artifact.filename
          || after.serialized !== current.serialized || !same(after.assistedValues,expected.current_assisted_values)) throw new Error("The portal changed outside its known upload transition");
        return {outcome:"selected_only",field_id:"resume",selection_reported:true,message:"Exact resume selected; portal upload state is browser-observed only."};
      }
      const selectedList = control.files, selected = selectedList[0];
      if (document.getElementById("resume") !== control || control.files.length !== 1 || selected.name !== artifact.filename
        || selected.size !== artifact.size_bytes || selected.type !== artifact.media_type) throw new Error("The selected resume changed during the page callback");
      const selectedHash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", await selected.arrayBuffer()))].map((byte) => byte.toString(16).padStart(2, "0")).join("");
      const after = read(null, control);
      if (document.getElementById("resume") !== control || control.files !== selectedList || control.files[0] !== selected
        || selectedHash !== artifact.sha256 || after.serialized !== current.serialized
        || !same(after.assistedValues, expected.current_assisted_values)) throw new Error("The file or form changed after selection; employer upload outcome is unknown");
    } catch (error) { return { outcome: "unknown", field_id: "resume", reason: error.message }; }
    return { outcome: "selected_only", field_id: "resume", selection_reported:true, message: "Exact resume selected locally. Employer upload acceptance is unconfirmed. No application was submitted or charged." };
  }
  const reviewed = expected.reviewed_answers;
  if (!reviewed || typeof reviewed !== "object" || Array.isArray(reviewed) || !Object.keys(reviewed).length || Object.keys(reviewed).length > (version === 2 ? 64 : 7)
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
  const control = getControl(action.field_id);
  if (!current.assisted.includes(control) || current.assistedValues[action.field_id] !== action.expected_before) return fail("Reviewed control expected-before value changed");
  if (control.id === "email" && (!action.email_blur_disclosure || action.email_blur_disclosure.acknowledged !== true
    || action.email_blur_disclosure.event !== "blur" || action.email_blur_disclosure.recipient !== recipient
    || action.email_blur_disclosure.context_digest !== contextDigest)) return fail("Review and acknowledge the bound email validation disclosure before filling");
  if (version === 2 && (action.review_protocol_version !== 2 || action.control_type !== fieldMapping.find(({ id }) => id === action.field_id).type)) return fail("The typed control action changed");
  if (["checkbox", "button"].includes(control.type)) {
    const id = action.field_id;
    if (control.type === "checkbox" && !["true", "false"].includes(action.value)
      || control.type === "button" && (action.value !== String(Number(action.expected_before) + 1) || id !== `employment-add-${action.expected_before}`)) return fail("The exact reviewed employment action changed");
    if (Date.now() >= action.deadline || action.cancelled === true) return fail("The employment action expired before disclosure");
    control.click();
    // Structural transitions deliberately end the approved sequence. A fresh
    // inspection/review binds any newly added row or conditional end controls.
    return { outcome: "unknown", field_id: id, reason: "The single reviewed employment click requires inspection. Review the current form before continuing; no automatic replay." };
  }
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
        if (control.id === "country" && options.some((node) => {
          const flag = [...(node.querySelector(".iti__flag")?.classList || [])].find((name) => /^iti__[a-z]{2}$/.test(name));
          return !descriptor.options.some(({ value, label }) => flag === `iti__${value}` && label === normalise(node.textContent || ""));
        })) return { outcome: "unknown", field_id: control.id, reason: "The exact country flag/value changed" };
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
  try {
    const after = read();
    if (after.serialized !== current.serialized || !same(after.assistedValues, { ...expectedValues, [control.id]: action.value })) return { outcome: "unknown", field_id: control.id, reason: "The filled value or conditional form changed after disclosure. Inspect and review again." };
  } catch (error) { return { outcome: "unknown", field_id: control.id, reason: error.message }; }
  const manualHandoff = current.fields.filter((field) => field.required && (!allowed.has(field.id)
    || !Object.hasOwn(reviewed, field.id) || document.getElementById(field.id)?.value !== reviewed[field.id])).map(({ id, label, hidden }) => ({ id, label, hidden }));
  return { outcome: control.value === action.value ? "filled_only" : "unknown", field_id: control.id, manual_handoff: manualHandoff,
    message: "One approved text answer filled only. Complete remaining manual controls, attachment and final submission yourself. No employer receipt or application charge is established." };
}
