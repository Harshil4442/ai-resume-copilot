import { base64url, canonical, digest } from "./protocol.js";
import { isSubmissionBilling, isSubmissionVersion, submissionUnits } from "./submission-billing.js";

const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const HEX = /^[a-f0-9]{64}$/;
const unifiedSubmissionAvailable = device => device.unified_submit_available === true && canonical(device.submit_protocol_versions) === "[4,5]";
const PHASES = new Set(["idle", "pending", "active", "draft", "approved", "running", "filled", "unknown", "cancelled", "expired", "revoked"]);
const KEYS = ["version", "device_id", "phase", "review_id", "application_id", "filled_count"].sort().join(",");

export class AssistanceRuntime {
  constructor({ transport, browser, store, now = () => Date.now() }) {
    Object.assign(this, { transport, browser, store, now });
    this.state = null; this.inspection = null; this.review = null; this.busy = false; this.cancelled = false; this.attachmentAvailable = false; this.submitAvailable = false; this.nativeUploadAvailable=false; this.capturedUpload=null;
  }
  async initialise() {
    const saved = await this.store.read();
    this.state = saved ?? { version: 1, device_id: this.transport.identity.device_id, phase: "idle", review_id: null, application_id: null, filled_count: 0 };
    if (!this.valid(this.state)) throw new Error("Stored assistance state needs inspection. No field action is enabled.");
    if (this.state.phase === "running") await this.save({ phase: "unknown" });
    if(this.state.review_id && this.browser.readEvidence)this.capturedUpload=await this.browser.readEvidence("upload",this.state.review_id);
    return this;
  }
  valid(value) {
    return value && Object.keys(value).sort().join(",") === KEYS && value.version === 1
      && value.device_id === this.transport.identity.device_id && PHASES.has(value.phase)
      && (value.review_id === null || UUID.test(value.review_id))
      && (value.application_id === null || typeof value.application_id === "string" && /^[A-Za-z0-9_-]{1,80}$/.test(value.application_id))
      && Number.isSafeInteger(value.filled_count) && value.filled_count >= 0 && value.filled_count <= 64;
  }
  async save(changes) {
    const next = { ...this.state, ...changes };
    if (!this.valid(next)) throw new Error("Assistance checkpoint is invalid.");
    await this.store.write(next); this.state = next;
  }
  status(message = "") {
    return { ...this.state, busy: this.busy, key_sha256: this.transport.identity.key_sha256,
      attachment_available: this.attachmentAvailable, submit_available: this.submitAvailable,native_upload_available:this.nativeUploadAvailable,
      native_upload:Boolean(this.review?.payload?.upload_plan),storage_origin:this.review?.payload?.upload_plan?.storage_origin||null,
      can_reconcile: !this.busy && (isSubmissionVersion(this.review?.review_protocol_version) && Boolean(this.review?.submission?.lease_exported) && this.review?.submission_status === "unknown"
        || this.review?.review_protocol_version===3 && this.capturedUpload?.selection_reported===true && this.state.phase!=="filled"), submission_status:this.review?.submission_status ?? null, employer_confirmed:this.review?.employer_confirmed === true, review_protocol_version: this.review?.review_protocol_version ?? this.review?.payload?.review_protocol_version ?? 1,
      ...(this.review?.review_protocol_version===5?{billing:{...this.review.billing}}:{}),
      review_url: this.state.review_id ? `${this.transport.configuration.websiteOrigin}/browser-companion?review=${this.state.review_id}`
        : `${this.transport.configuration.websiteOrigin}/browser-companion?device=${this.state.device_id}`,
      can_fill: Boolean(this.review?.payload?.fields?.length || [3, 4, 5].includes(this.review?.payload?.review_protocol_version)) && !this.busy && !this.cancelled && this.state.phase === "approved" && this.now() < this.review?.expires_at_ms && Boolean(this.inspection && this.review), message };
  }
  async start() {
    if (this.busy || this.state.review_id) throw new Error("Cancel or inspect the current review before connecting again.");
    const device = await this.transport.device("prepare");
    this.attachmentAvailable = device.attachment_available === true; this.submitAvailable = unifiedSubmissionAvailable(device);this.nativeUploadAvailable=device.native_upload_available===true;
    await this.save({ phase: device.state });
    return this.status("Review this browser fingerprint on HireWiz, then check the connection.");
  }
  async check() {
    if (this.busy) return this.status("A field action is running. Cancel stops future fields.");
    const device = await this.transport.device("status", this.state.review_id);
    this.attachmentAvailable = device.attachment_available === true; this.submitAvailable = unifiedSubmissionAvailable(device);this.nativeUploadAvailable=device.native_upload_available===true;
    if (device.state !== "active") {
      this.inspection = this.review = null;
      await this.save({ phase: device.state }); return this.status();
    }
    if (device.review) {
      await this.acceptReview(device.review);
      const phase = this.state.phase === "unknown" && ["running", "approved"].includes(device.review.state) ? "unknown" : device.review.state;
      await this.save({ phase, filled_count: device.review.filled_count });
    } else await this.save({ phase: "active" });
    if (isSubmissionVersion(this.review?.review_protocol_version)) return this.status(this.submissionMessage());
    return this.status(this.inspection ? "Current review checked. Filling requires approval on HireWiz." : "Current state checked. After a browser restart, cancel this review and inspect again before filling.");
  }
  submissionMessage() {
    const units = submissionUnits(this.review?.review_protocol_version);
    if (this.review?.submission_status === "confirmed") return `Employer receipt independently verified. ${units} charged. No further submit action is authorized.`;
    if (this.review?.submission_status === "verified_not_submitted") return `Independent reconciliation verified that the application was not submitted. The ${units} hold was returned.`;
    return this.review?.submission_status === "unknown" ? `Employer receipt remains unverified; ${units} stay held for reconciliation. Inspect status without resending.`
      : `Review the exact final submission and reservation of ${units} on HireWiz before one submit action.`;
  }
  async acceptReview(review) {
    if (!review || !UUID.test(review.id) || !PHASES.has(review.state) || !HEX.test(review.review_digest)
      || !Number.isSafeInteger(review.filled_count) || review.filled_count < 0 || review.filled_count > 64
      || !Number.isSafeInteger(review.expires_at_ms)) throw new Error("The current review could not be verified.");
    if (this.state.review_id && review.id !== this.state.review_id) throw new Error("A different review was returned.");
    const payload = review.payload;
    const durableVersion = review.review_protocol_version ?? payload?.review_protocol_version ?? 1;
    if (durableVersion === 5) {
      if (review.review_protocol_version !== 5 || !isSubmissionBilling(review.billing) || review.expires_at_ms > review.billing.quote_expires_at_ms
        || payload && Object.keys(payload).length && (canonical(payload.billing) !== canonical(review.billing) || canonical(payload.pricing) !== canonical(review.billing))) throw new Error("The exact unified submission quote changed.");
    } else if (review.billing !== undefined || payload?.billing !== undefined) throw new Error("A legacy review cannot infer unified billing.");
    if (this.review?.id === review.id && ((this.review.review_protocol_version ?? this.review.payload?.review_protocol_version ?? 1) !== durableVersion
      || canonical(this.review.billing ?? null) !== canonical(review.billing ?? null)
      || durableVersion === 5 && this.review.review_digest !== review.review_digest)) throw new Error("The retained submission quote changed. Inspect a new review explicitly.");
    if (![1, 2, 3, 4, 5].includes(durableVersion) || !Number.isSafeInteger(durableVersion)
      || review.filled_count > (durableVersion === 1 ? 7 : [3, 4, 5].includes(durableVersion) ? 1 : 64)
      || durableVersion === 3 && review.state === "filled" && review.filled_count !== 1
      || payload && Object.keys(payload).length && (payload.review_protocol_version ?? 1) !== durableVersion) throw new Error("The durable reviewed control version or outcome count changed.");
    if (isSubmissionVersion(durableVersion) && (review.fee_credits!==(durableVersion===5?3:20) || !["unknown","not_launched","confirmed","verified_not_submitted"].includes(review.submission_status)
      || review.employer_confirmed!==(review.submission_status==="confirmed") || review.filled_count!==(review.submission_status==="confirmed"?1:0)
      || review.submission_status==="confirmed" && review.state!=="filled")) throw new Error("The durable submission result or credit policy changed.");
    if (payload && Object.keys(payload).length) {
      const version = payload.review_protocol_version ?? 1;
      if (![1, 2, 3, 4, 5].includes(version) || !Number.isSafeInteger(version) || payload.fields?.length > (version === 1 ? 7 : 64)) throw new Error("The reviewed control version changed.");
      if (await digest(canonical(payload)) !== review.review_digest || payload.application_id !== this.state.application_id
        || canonical(payload.allowed_actions) !== (isSubmissionVersion(version) ? '["submit"]' : version === 3 ? payload.upload_plan ? '["upload_resume"]' : '["attach"]' : '["fill"]') || payload.fee_credits !== (version === 5 ? 3 : version === 4 ? 20 : 0)
        || !HEX.test(payload.package_digest) || !HEX.test(payload.artifact?.sha256) || !HEX.test(payload.context_digest)
        || !Array.isArray(payload.fields) || payload.fields.length < ([3, 4, 5].includes(version) || payload.hosted ? 0 : 1) || payload.fields.length > ([3, 4, 5].includes(version) ? 0 : 64)
        || review.filled_count > ([3, 4, 5].includes(version) ? 1 : payload.fields.length)) throw new Error("The exact reviewed package changed.");
      if (this.inspection && (payload.context_digest !== this.inspection.context_digest
        || payload.document_id !== this.inspection.document_id || payload.frame_id !== 0 || payload.target_url !== this.inspection.url
        || canonical(payload.field_mapping) !== canonical(this.inspection.field_mapping))) throw new Error("The inspected browser document changed.");
    } else if (!["filled", "unknown", "cancelled", "expired"].includes(review.state)) throw new Error("The review payload is unavailable.");
    this.review = review;
  }
  async prepare(applicationId, requestedVersion = 2, nativeUpload = false) {
    if (this.busy || this.state.review_id || !["active", "filled", "cancelled", "expired"].includes(this.state.phase)
      || typeof applicationId !== "string" || !/^[A-Za-z0-9_-]{1,80}$/.test(applicationId)) throw new Error("Check the connected browser and clear the earlier review first.");
    this.busy = true; this.cancelled = false;
    try {
      const device = await this.transport.device();
      if (device.state !== "active") throw new Error("Approve this browser on HireWiz before inspecting an application.");
      if (requestedVersion === 5 && !unifiedSubmissionAvailable(device)) throw new Error("Unified submission is unavailable. No priced review or submit action was requested.");
      let app = await this.transport.request("application", { application_id: applicationId, review_protocol_version: requestedVersion });
      if (app.application_id !== applicationId || !Array.isArray(app.field_mapping)
        || !/^https:\/\/job-boards\.greenhouse\.io\/razorpaysoftwareprivatelimited\/jobs\/[0-9]+$/.test(app.target_url)) throw new Error("Only the selected current Razorpay portal is supported.");
      const version = app.review_protocol_version ?? 1;
      if(nativeUpload && (version!==3 || app.native_upload_available!==true || typeof this.browser.prepareUpload!=="function"))throw new Error("Separately reviewed native upload is unavailable.");
      if (![1, 2, 3, 4, 5].includes(version) || !Number.isSafeInteger(version) || [3, 4, 5].includes(requestedVersion) && version !== requestedVersion) throw new Error("The application control version is unavailable.");
      let inspection = await this.browser.inspect(app.target_url, app.field_mapping, null, version, app.upload);
      if ([2, 4, 5].includes(version) && app.hosted_controls_available && inspection.hosted_rows) {
        const target = app.target_url;
        app = await this.transport.request("application", { application_id: applicationId, review_protocol_version: version, hosted_rows: inspection.hosted_rows });
        if (app.application_id !== applicationId || app.target_url !== target || app.review_protocol_version !== version
          || app.hosted_controls_available !== true || !Array.isArray(app.field_mapping)) throw new Error("The selected application's hosted controls changed during inspection.");
        inspection = await this.browser.inspect(app.target_url, app.field_mapping, inspection.document_id, version, app.upload);
      }
      if (inspection.outcome !== "inspected" || inspection.url !== app.target_url || inspection.frame_id !== 0
        || !HEX.test(inspection.context_digest) || await digest(canonical(inspection.context)) !== inspection.context_digest) throw new Error("The exact current form could not be inspected.");
      let uploadPlan=null;
      if(nativeUpload){
        uploadPlan=await this.browser.prepareUpload(app.target_url,inspection);
        const checked=await this.browser.inspect(app.target_url,app.field_mapping,inspection.document_id,3);
        if(checked.context_digest!==inspection.context_digest || canonical(checked.assisted_values)!==canonical(inspection.assisted_values))throw new Error("The form changed during upload destination review.");
        inspection=checked;
      }
      if (this.cancelled) throw new Error("Inspection cancelled before review.");
      this.inspection = inspection;
      await this.save({ phase: "active", application_id: applicationId, filled_count: 0 });
      const minimal = { outcome: "inspected", origin: inspection.origin, url: inspection.url, frame_id: 0,
        document_id: inspection.document_id, context_digest: inspection.context_digest, assisted_values: inspection.assisted_values,
        ...([2, 4, 5].includes(version) && inspection.hosted_rows && app.hosted_controls_available ? { hosted_rows: inspection.hosted_rows } : {}),
        manual_fields: inspection.manual_fields.map(({ id, label, required }) => ({ id, label, required })),
        ...(version > 1 ? { review_protocol_version: version } : {}), ...(version === 3 ? { attachment: inspection.attachment } : {}),
        ...(uploadPlan?{upload_plan:uploadPlan,native_context:inspection.native_context}:{}),
        ...(isSubmissionVersion(version) ? {upload:inspection.upload,upload_continuity:inspection.upload_continuity,ready:inspection.ready,
          ...(app.upload?.native_upload?{native_context:inspection.native_context}:{})} : {}) };
      const review = await this.transport.request("inspect", { application_id: applicationId, inspection: minimal });
      await this.acceptReview(review);
      await this.save({ review_id: review.id, phase: review.state, filled_count: review.filled_count });
      return { ...this.status("Review the exact resume, answers, destination and disclosures on HireWiz before filling."), busy: false };
    } finally { this.busy = false; }
  }
  expectedValues(index) {
    const values = { ...this.inspection.assisted_values };
    this.review.payload.fields.forEach((field, position) => { values[field.id] = position < index ? field.value : field.expected_before; });
    return values;
  }
  async fill() {
    if (this.busy || this.cancelled || this.state.phase === "unknown" || !this.inspection || !this.review || !this.state.review_id) throw new Error("A freshly inspected, approved review is required. Uncertain or cancelled actions cannot be resumed.");
    await this.check();
    if (!this.status().can_fill || this.review.expires_at_ms <= this.now()) throw new Error("Approve this exact review on HireWiz before filling.");
    if (this.review.payload.review_protocol_version === 3) return this.attach();
    if (isSubmissionVersion(this.review.payload.review_protocol_version)) return this.submit();
    this.busy = true; this.cancelled = false;
    try {
      while (this.state.filled_count < this.review.payload.fields.length) {
        if (this.cancelled) break;
        const index = this.state.filled_count, payload = this.review.payload, field = payload.fields[index];
        const expected = { ...this.inspection, review_digest: this.review.review_digest, reviewed_answers: payload.reviewed_answers,
          reviewed_answers_sha256: payload.reviewed_answers_sha256, current_assisted_values: this.expectedValues(index) };
        const current = await this.browser.inspect(payload.target_url, payload.field_mapping, this.inspection.document_id, payload.review_protocol_version ?? 1);
        if (current.context_digest !== payload.context_digest || current.document_id !== payload.document_id
          || canonical(current.assisted_values) !== canonical(expected.current_assisted_values)) throw new Error("The reviewed form or an answer changed. Cancel and inspect it again.");
        if (this.cancelled) break;
        // Persist intent before the one-shot lease request. A lost acknowledgement
        // or service-worker restart must never rerun a field event.
        await this.save({ phase: "running" });
        const begun = await this.transport.request("begin", { review_id: this.review.id, review_digest: this.review.review_digest,
          index, context_digest: payload.context_digest, document_id: payload.document_id });
        if (begun.state !== "begun") throw new Error("The earlier field outcome needs manual inspection.");
        const lease = await this.transport.verifyLease(begun.lease);
        const bindings = { review_id: this.review.id, application_id: payload.application_id, package_digest: payload.package_digest,
          artifact_sha256: payload.artifact.sha256, target_url: payload.target_url, context_digest: payload.context_digest,
          document_id: payload.document_id, frame_id: 0, review_digest: this.review.review_digest,
          reviewed_answers_sha256: payload.reviewed_answers_sha256, field_id: field.id, value: field.value, expected_before: field.expected_before };
        if (Object.entries(bindings).some(([name, value]) => lease[name] !== value)) throw new Error("The exact field authorization changed.");
        if (payload.review_protocol_version === 2) {
          bindings.review_protocol_version = 2;
          bindings.control_type = field.type;
          if (field.type === "combobox") bindings.option_label = field.value_label;
          if (Object.entries(bindings).some(([name, value]) => lease[name] !== value)) throw new Error("The exact typed control authorization changed.");
        }
        const result = this.cancelled ? { outcome: "blocked" } : await this.browser.fill(payload.target_url, payload.field_mapping, expected, lease);
        const outcome = result.outcome === "filled_only" && result.field_id === field.id ? "filled" : result.outcome === "blocked" ? "blocked" : "unknown";
        const receipt = await this.transport.request("outcome", { review_id: this.review.id, action_id: lease.action_id, outcome });
        await this.acceptReview(receipt);
        await this.save({ phase: receipt.state, filled_count: receipt.filled_count });
        if (outcome !== "filled") break;
      }
      return { ...this.status(this.cancelled ? "Further text filling stopped. An already authorized field may have finished. Check the portal and clear this review before another inspection."
        : this.state.phase === "unknown" ? "Filling stopped because a field outcome is uncertain. Check the company portal and clear this review before another inspection. No automatic retry occurred and no application fee was charged."
        : this.state.phase !== "filled" ? "Filling stopped before all approved answers were filled. Check the company portal and clear this review before another inspection. No application fee was charged."
        : this.review.payload.review_protocol_version === 2 ? "Approved answers filled. Check any remaining required or changed controls, attach your reviewed file and submit on the company portal yourself. No application fee was charged."
        : "Approved text filling finished. Complete the remaining fields, attach your reviewed file and submit on the company portal yourself. No application fee was charged."), busy: false };
    } catch (failure) {
      if (this.state.phase === "running") await this.save({ phase: "unknown" });
      throw failure;
    } finally { this.busy = false; }
  }
  async attach() {
    this.busy = true; this.cancelled = false;
    let bytes;
    try {
      const payload = this.review.payload;
      const current = await this.browser.inspect(payload.target_url, payload.field_mapping, this.inspection.document_id, 3);
      if (current.context_digest !== payload.context_digest || current.document_id !== payload.document_id
        || canonical(current.assisted_values) !== canonical(payload.assisted_values)
        || canonical(current.attachment) !== canonical(payload.attachment)) throw new Error("The reviewed form, answers or empty resume input changed. Clear and inspect again.");
      if (this.cancelled) throw new Error("Attachment cancelled before a new action.");
      await this.save({ phase: "running" });
      const begun = await this.transport.request("begin", { review_id: this.review.id, review_digest: this.review.review_digest,
        index: 0, context_digest: payload.context_digest, document_id: payload.document_id });
      if (begun.state !== "begun") throw new Error("The earlier attachment outcome needs inspection.");
      const lease = await this.transport.verifyLease(begun.lease);
      const bindings = { review_protocol_version: 3, review_id: this.review.id, application_id: payload.application_id,
        package_digest: payload.package_digest, artifact_sha256: payload.artifact.sha256, target_url: payload.target_url,
        context_digest: payload.context_digest, document_id: payload.document_id, frame_id: 0,
        review_digest: this.review.review_digest, field_id: "resume", control_type: "file" };
      if (Object.entries(bindings).some(([key, value]) => lease[key] !== value)
        || canonical(lease.artifact) !== canonical(payload.artifact) || canonical(lease.attachment) !== canonical(payload.attachment)
        || canonical(lease.upload_plan??null)!==canonical(payload.upload_plan??null) || canonical(lease.native_context??null)!==canonical(payload.native_context??null)) throw new Error("The exact attachment authorization changed.");
      let result = { outcome: "blocked" };
      if (!this.cancelled && this.now() < lease.deadline) {
        bytes = await this.transport.artifact(lease);
        if (!this.cancelled && this.now() < lease.deadline) {
          const expected={...this.inspection,artifact:payload.artifact,review_digest:this.review.review_digest,current_assisted_values:payload.assisted_values};
          result=payload.upload_plan ? await this.browser.uploadAndAttach(payload.target_url,payload.field_mapping,expected,lease,bytes,()=>this.cancelled)
            : await this.browser.fill(payload.target_url,payload.field_mapping,expected,{...lease,artifact_base64:base64url(bytes)});
        }
      }
      const outcome = result.outcome === "selected_only" && result.field_id === "resume" ? "selected" : result.outcome === "blocked" ? "blocked" : "unknown";
      if(payload.upload_plan)this.capturedUpload=await this.browser.readEvidence("upload",this.review.id);
      const receipt = await this.transport.request("outcome", { review_id: this.review.id, action_id: lease.action_id, outcome, ...(result.selection_reported === true ? {selection_reported:true} : {}),...(result.upload_evidence?{upload_evidence:result.upload_evidence}:{}) });
      await this.acceptReview(receipt);
      await this.save({ phase: receipt.state, filled_count: receipt.filled_count });
      if(payload.upload_plan && receipt.attachment_status==="uploaded_native_verified"){
        await this.browser.clearEvidence("upload",this.review.id);this.capturedUpload=null;
      }
      return { ...this.status(outcome === "selected" ? payload.upload_plan ? "Exact reviewed resume uploaded once with a native secure storage acknowledgement and connected to this form. No final application submission or credit charge occurred. Review final submission separately."
        : "Exact resume selected locally. Employer upload acceptance is unconfirmed. Finish the remaining required controls and submit yourself. No application was submitted or charged."
        : "Attachment stopped. Inspect the employer portal before clearing this review. No automatic resend, submission receipt or application fee was created."), busy: false };
    } catch (failure) {
      if(this.review?.payload?.upload_plan && this.browser.readEvidence)this.capturedUpload=await this.browser.readEvidence("upload",this.review.id);
      if (this.state.phase === "running") await this.save({ phase: "unknown" });
      throw failure;
    } finally { bytes?.fill(0); this.busy = false; }
  }
  async submit() {
    this.busy=true; this.cancelled=false;
    try {
      const payload=this.review.payload;
      const version=payload.review_protocol_version;
      const units=submissionUnits(version);
      const current=await this.browser.inspect(payload.target_url,payload.field_mapping,payload.document_id,version,payload.upload);
      if (!current.ready || current.context_digest!==payload.context_digest || canonical(current.assisted_values)!==canonical(payload.assisted_values)
        || canonical(current.upload)!==canonical(payload.upload) || payload.native_context && canonical(current.native_context)!==canonical(payload.native_context)) throw new Error("The final reviewed portal, answers or completed upload changed. No submit action was requested.");
      if (this.cancelled || this.now() >= this.review.expires_at_ms || version === 5 && (!isSubmissionBilling(payload.billing) || this.now() >= payload.billing.quote_expires_at_ms)) throw new Error("Final submission cancelled or its exact quote expired before requesting an action.");
      await this.save({phase:"running"});
      const begun=await this.transport.request("begin",{review_id:this.review.id,review_digest:this.review.review_digest,index:0,context_digest:payload.context_digest,document_id:payload.document_id});
      if (begun.state!=="begun") throw new Error("The submit attempt is uncertain. Never resend; inspect its current status.");
      const lease=await this.transport.verifyLease(begun.lease);
      const bindings={review_protocol_version:version,review_id:this.review.id,application_id:payload.application_id,package_digest:payload.package_digest,
        artifact_sha256:payload.artifact.sha256,target_url:payload.target_url,context_digest:payload.context_digest,document_id:payload.document_id,
        frame_id:0,review_digest:this.review.review_digest,reviewed_answers_sha256:payload.reviewed_answers_sha256,field_id:"final-submit",control_type:"submit",fee_credits:version===5?3:20};
      if (Object.entries(bindings).some(([key,value])=>lease[key]!==value) || canonical(lease.billing??null)!==canonical(payload.billing??null) || canonical(lease.upload)!==canonical(payload.upload)
        || canonical(lease.upload_continuity)!==canonical(payload.upload_continuity) || canonical(lease.submit_control)!==canonical(payload.submit_control)
        || ["receipt_contract","expected_application","expected_application_sha256","native_context"].some(key=>canonical(lease[key]??null)!==canonical(payload[key]??null)) || !HEX.test(lease.native_proof_sha256 || "")) throw new Error("The distinct exact final-submit authorization changed.");
      this.review.submission={action_id:lease.action_id,target_url:payload.target_url,document_id:payload.document_id,lease_exported:true};
      let result={outcome:"blocked"};
      if (!this.cancelled && this.now()<lease.deadline){
        const expected={...this.inspection,review_digest:this.review.review_digest,reviewed_answers:payload.reviewed_answers,reviewed_answers_sha256:payload.reviewed_answers_sha256,
          current_assisted_values:payload.assisted_values,submit_control:payload.submit_control,...(version===5?{billing:payload.billing}:{})};
        result=payload.receipt_contract ? await this.browser.nativeSubmit(payload.target_url,payload.field_mapping,expected,lease)
          : await this.browser.fill(payload.target_url,payload.field_mapping,expected,lease);
      }
      // Once an action is claimed, even a lost result or validation callback is
      // an uncertain possible send. No loop, native re-export or click replay.
      const observation={status:result.outcome==="click_dispatched"?"click_dispatched":"unknown",url:payload.target_url,document_id:payload.document_id};
      const receipt=await this.transport.request("outcome",{review_id:this.review.id,action_id:lease.action_id,observation,...(result.receipt?{receipt:result.receipt}:{})});
      await this.acceptReview({...receipt,submission:this.review.submission});
      await this.save({phase:receipt.state,filled_count:receipt.filled_count});
      if(receipt.employer_confirmed===true && this.browser.clearEvidence)await this.browser.clearEvidence("submit",this.review.id);
      return {...this.status(receipt.employer_confirmed ? this.submissionMessage() : `One submit attempt was authorized. Employer acceptance is unverified and ${units} remain held. Inspect or reconcile status; never automatically resend.`),busy:false};
    } catch (failure) {
      if (this.state.phase==="running") await this.save({phase:"unknown"});
      throw failure;
    } finally { this.busy=false; }
  }
  async reconcile() {
    if (this.busy) throw new Error("Wait for the current action before inspecting its status.");
    await this.check();
    if(this.review?.review_protocol_version===3){
      const captured=await this.browser.readEvidence("upload",this.review.id);
      if(!captured?.selection_reported || !UUID.test(captured.proof?.action_id))throw new Error("No original captured upload proof is available. Do not upload again.");
      this.busy=true;
      try{
        const response=await this.transport.request("reconcile",{review_id:this.review.id,action_id:captured.proof.action_id,upload_evidence:captured.proof,selection_reported:true});
        await this.acceptReview(response);await this.save({phase:response.state,filled_count:response.filled_count});
        if(response.attachment_status==="uploaded_native_verified"){await this.browser.clearEvidence("upload",this.review.id);this.capturedUpload=null;}
        return {...this.status("Original native upload proof reconciled without another upload. No final submission or credit charge occurred."),busy:false};
      }finally{this.busy=false;}
    }
    const submission=this.review?.submission;
    if (!isSubmissionVersion(this.review?.review_protocol_version) || !submission?.lease_exported || !UUID.test(submission.action_id)) throw new Error("No exported submit attempt is available for read-only inspection.");
    this.busy=true;
    try {
      const captured=this.browser.readEvidence?await this.browser.readEvidence("submit",this.review.id):null;
      let observation;try{observation=await this.browser.observe(submission.target_url,submission.document_id);}catch(failure){
        if(!captured?.proof)throw failure;
        observation={status:"unknown",url:submission.target_url,document_id:submission.document_id};
      }
      const response=await this.transport.request("reconcile",{review_id:this.review.id,action_id:submission.action_id,observation,...(captured?.proof?{receipt:captured.proof}:{})});
      await this.acceptReview({...response,submission}); await this.save({phase:response.state,filled_count:response.filled_count});
      if(response.employer_confirmed===true && this.browser.clearEvidence)await this.browser.clearEvidence("submit",this.review.id);
      return {...this.status(response.submission_status==="unknown" ? `Portal status inspected without another submit click. A public thank-you page does not verify receipt; ${submissionUnits(this.review.review_protocol_version)} remain held pending authenticated reconciliation.` : this.submissionMessage()),busy:false};
    } finally { this.busy=false; }
  }
  async cancel() {
    this.cancelled = true;
    // Do not race a cancel POST against an in-flight outcome. The executing loop
    // observes this flag before the next lease; an issued short lease can finish.
    if (this.busy) return this.status("Stopping future fields. An already authorized field may finish within its short lease; check the portal.");
    const requiresNewApplication=this.review?.review_protocol_version===5 && ["approved","running","unknown"].includes(this.review.state);
    const retainUnknown=this.state.phase==="unknown" && (this.review?.review_protocol_version===5 || this.review?.review_protocol_version===4 && this.review?.submission?.lease_exported
      || this.review?.review_protocol_version===3 && this.capturedUpload?.selection_reported===true);
    if (this.state.review_id) await this.transport.request("cancel", { review_id: this.state.review_id });
    if(retainUnknown){
      await this.check();
      return this.status("Future actions cancelled. The original uncertain attempt and captured evidence remain available for reconciliation; nothing will be resent.");
    }
    this.inspection = this.review = null;
    await this.save({ phase: "active", review_id: null, application_id: null, filled_count: 0 });
    return this.status(requiresNewApplication ? "This priced review was cleared. Use a new application ID for another final submission review. Inspect application status for the reservation outcome."
      : "Review cleared. Inspect the company portal before creating another review.");
  }
}
