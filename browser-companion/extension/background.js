import { CONFIG } from "./config.js";
import { Companion } from "./protocol.js";
import { deviceFailurePhase, deviceIdentity } from "./device.js";
import { FixtureTransport } from "./transport.js";
import { localAdapter } from "./local-adapter.js";
import { PairingTransport } from "./pairing-transport.js";
import { PairingRuntime } from "./pairing-runtime.js";
import { AssistanceTransport } from "./assistance-transport.js";
import { AssistanceRuntime } from "./assistance-runtime.js";
import { razorpayAdapter } from "./razorpay-adapter.js";
import { GreenhouseOwnedUpload, validateSubmittedApplication } from "./greenhouse-owned-upload.js";
import { GreenhouseNativeExchange } from "./greenhouse-native-network.js";
import { greenhouseResumeComponent } from "./greenhouse-resume-component.js";
import { base64url, canonical, digest } from "./protocol.js";
import { LEGACY_KEYS, LostKeyRecovery, RECOVERY_UNAVAILABLE, RecoveryGate, recoveryKey } from "./lost-key-recovery.js";

let assistancePromise;
const nativeAssistance = () => CONFIG.mode === "reviewed_assistance" && CONFIG.nativePairing != null;
const recovery = new LostKeyRecovery({ readLegacy: () => boundedStorage(chrome.storage.local.get([...LEGACY_KEYS])) });
const recoveryGate = new RecoveryGate();
let localRecoveryContext = null, missingKeyObserved = false;
async function recoveryFence(expected = localRecoveryContext) {
  if (!nativeAssistance()) return;
  if (canonical(await recovery.context()) !== canonical(expected)) throw new Error(RECOVERY_UNAVAILABLE);
}
async function scopedRead(name, context) {
  await recoveryFence(context);
  const key = recoveryKey(context, name);
  return (await boundedStorage(chrome.storage.local.get(key)))[key] ?? null;
}
async function scopedWrite(name, value, context) {
  await recoveryFence(context);
  const key = recoveryKey(context, name);
  await boundedStorage(chrome.storage.local.set({ [key]: value }));
  await recoveryFence(context);
}
async function assistanceEngine() {
  return assistancePromise ??= (async () => {
    const context = localRecoveryContext;
    if (CONFIG.mode !== "reviewed_assistance" || CONFIG.extensionId !== chrome.runtime.id) throw new Error("Reviewed assistance configuration is unavailable");
    const permission = async (origin) => {
      await recoveryFence(context);
      if (![CONFIG.websiteOrigin, CONFIG.portalOrigin].includes(origin)
        || !await chrome.permissions.contains({ origins: [`${origin}/*`] })) throw new Error("The exact reviewed origin permission is missing or revoked");
    };
    const targetTab = async (url) => {
      if (!/^https:\/\/job-boards\.greenhouse\.io\/razorpaysoftwareprivatelimited\/jobs\/[0-9]+$/.test(url)) throw new Error("Only the exact selected Razorpay job is supported");
      await permission(CONFIG.portalOrigin);
      const tabs = await chrome.tabs.query({ url });
      if (tabs.length !== 1 || !Number.isInteger(tabs[0].id)) throw new Error("Open exactly one tab for the selected Razorpay application");
      return tabs[0];
    };
    const ownedUpload=new GreenhouseOwnedUpload();let component=null;
    const evidenceStore=async(kind,reviewId,value=undefined)=>{
      const stored=await scopedRead("native_application_evidence",context)||{};
      if(!stored || typeof stored!=="object" || Array.isArray(stored) || Object.keys(stored).length>4 || JSON.stringify(stored).length>65536)throw new Error("Retained native evidence needs inspection.");
      const id=`${kind}:${reviewId}`;
      if(value===undefined)return stored[id]||null;
      const next={...stored,[id]:value};
      if(Object.keys(next).length>4 || JSON.stringify(next).length>65536)throw new Error("Inspect retained native attempts before another application.");
      await scopedWrite("native_application_evidence",next,context);return value;
    };
    const clearEvidence=async(kind,reviewId)=>{
      const stored=await scopedRead("native_application_evidence",context)||{};
      if(!stored || typeof stored!=="object" || Array.isArray(stored) || Object.keys(stored).length>4 || JSON.stringify(stored).length>65536)throw new Error("Retained native evidence needs inspection.");
      const next={...stored};delete next[`${kind}:${reviewId}`];await scopedWrite("native_application_evidence",next,context);
    };
    const resumeComponent=async(operation,tabId,documentId,expected)=>{
      await recoveryFence(context);
      const [result]=await chrome.scripting.executeScript({target:{tabId,documentIds:[documentId]},world:"MAIN",func:greenhouseResumeComponent,args:[operation,expected]});
      if(result?.documentId!==documentId || result?.result?.outcome!==(operation==="apply"?"callback_dispatched":"inspected"))throw new Error(result?.result?.reason||"The known resume component is unavailable.");
      return result.result;
    };
    const browser = {
      async inspect(url, mapping, documentId = null, version = 1, upload = null) {
        const tab = await targetTab(url);
        if (!documentId) {
          const [identity] = await chrome.scripting.executeScript({ target: { tabId: tab.id, frameIds: [0] }, func: () => true });
          documentId = identity?.documentId;
        }
        if (!documentId) throw new Error("Chrome document identity is unavailable");
        // DOM code needs only the visible upload identity. Keep native proof in
        // the trusted worker: Chrome scripting transport can omit null-valued
        // dictionary fields, and page/DOM results must never rewrite that proof.
        const domUpload=upload?Object.fromEntries(["review_id","action_id","filename","status"].map(key=>[key,upload[key]])):null;
        const [result] = await chrome.scripting.executeScript({ target: { tabId: tab.id, documentIds: [documentId] }, func: razorpayAdapter,
          args: [null, null, { document_id: documentId, frame_id: 0, field_mapping: mapping, review_protocol_version: version, upload:domUpload }] });
        if (result?.documentId !== documentId || result?.result?.outcome !== "inspected") throw new Error(result?.result?.reason || "The exact current portal form is not supported");
        if(version===4 && upload){
          if(result.result.upload?.review_id!==upload.review_id || result.result.upload?.action_id!==upload.action_id
            || result.result.upload?.filename!==upload.filename || result.result.upload?.status!==upload.status)throw new Error("The visible upload changed.");
          result.result.upload=structuredClone(upload);
        }
        if(version>=3){
          const [zone]=await chrome.scripting.executeScript({target:{tabId:tab.id,documentIds:[documentId]},func:()=>Intl.DateTimeFormat().resolvedOptions().timeZone});
          if(zone?.documentId!==documentId || typeof zone.result!=="string" || !/^[A-Za-z0-9_+/-]{1,128}$/.test(zone.result))throw new Error("The current browser context changed.");
          return {...result.result,native_context:{tab_id:tab.id,time_zone:zone.result}};
        }
        return result.result;
      },
      async prepareUpload(url,inspection){
        if(!nativeAssistance())throw new Error("Native candidate browser enrollment is required for exact upload.");
        const tab=await targetTab(url);
        const checked=await resumeComponent("inspect",tab.id,inspection.document_id,{target_url:url});
        await recoveryFence(context);
        const plan=await ownedUpload.prepare(url,checked.resume_component_sha256);
        if(checked.storage_origin!==plan.storage_origin){ownedUpload.clear();throw new Error("The known portal uploader and approved employer storage origin differ.");}
        component={...checked,tab_id:tab.id,document_id:inspection.document_id};return plan;
      },
      async uploadAndAttach(url,mapping,expected,lease,bytes,isCancelled){
        const tab=await targetTab(url),plan=lease.upload_plan;
        if(!component || component.tab_id!==tab.id || component.document_id!==lease.document_id || lease.native_context?.tab_id!==tab.id
          || component.resume_component_sha256!==plan.resume_component_sha256)throw new Error("The exact reviewed resume component is no longer retained.");
        const beforeDispatch=async()=>{
          await recoveryFence(context);
          if(isCancelled())throw new Error("Upload cancelled before dispatch.");
          const current=await browser.inspect(url,mapping,lease.document_id,3);
          if(current.context_digest!==expected.context_digest || canonical(current.assisted_values)!==canonical(expected.current_assisted_values)
            || canonical(current.native_context)!==canonical(lease.native_context))throw new Error("The reviewed form or current device context changed before upload.");
          await resumeComponent("check",tab.id,lease.document_id,{target_url:url,token:component.token,resume_component_sha256:plan.resume_component_sha256});
        };
        const proof=await ownedUpload.upload({lease,bytes,plan,beforeDispatch,cancelled:isCancelled});
        await evidenceStore("upload",lease.review_id,{proof,selection_reported:false});
        if(isCancelled() || Date.now()>=lease.deadline)throw new Error("The resume may be uploaded; cancellation or expiry stopped the form callback. No upload was repeated.");
        const selected=await browser.fill(url,mapping,{...expected,upload_plan:plan},{...lease,artifact_base64:base64url(bytes),native_upload_completed:true});
        if(selected.outcome!=="selected_only" || selected.selection_reported!==true)throw new Error("The exact file selection is uncertain after upload.");
        if(isCancelled() || Date.now()>=lease.deadline)throw new Error("Cancellation or expiry stopped the known form callback after upload.");
        await resumeComponent("apply",tab.id,lease.document_id,{target_url:url,token:component.token,resume_component_sha256:plan.resume_component_sha256,
          storage_origin:plan.storage_origin,storage_key:plan.storage_key,uploaded_url:proof.uploaded_url,filename:lease.artifact.filename,
          size_bytes:lease.artifact.size_bytes,media_type:lease.artifact.media_type,artifact_sha256:lease.artifact.sha256,deadline:lease.deadline});
        let observed=null;
        while(!isCancelled() && Date.now()<lease.deadline){
          try{observed=await browser.inspect(url,mapping,lease.document_id,4,{review_id:lease.review_id,action_id:lease.action_id,filename:lease.artifact.filename,status:"portal_reports_uploaded"});}catch{/* A pending React commit has not established continuity. */}
          if(observed?.upload_continuity?.action_id===lease.action_id)break;
          await new Promise(resolve=>setTimeout(resolve,25));
        }
        if(!observed?.upload_continuity || observed.upload_continuity.action_id!==lease.action_id || isCancelled())throw new Error("The known upload callback outcome is uncertain. Inspect without uploading again.");
        await evidenceStore("upload",lease.review_id,{proof,selection_reported:true});
        return {...selected,upload_evidence:proof};
      },
      async nativeSubmit(url,mapping,expected,lease){
        const tab=await targetTab(url);
        await ownedUpload.permission();
        if(lease.native_context?.tab_id!==tab.id || lease.receipt_contract!=="greenhouse_hosted_browser_https_200_v2"
          || await digest(canonical(lease.expected_application))!==lease.expected_application_sha256)throw new Error("The exact native receipt contract changed.");
        const exchange=new GreenhouseNativeExchange({kind:"submit",url:url.replace("https://job-boards.greenhouse.io/","https://boards.greenhouse.io/"),
          tabId:tab.id,documentId:lease.document_id,deadline:lease.deadline,validateBody:validateSubmittedApplication(lease.expected_application,lease.expected_application_sha256)});
        const {result,evidence}=await exchange.run(()=>browser.fill(url,mapping,expected,lease));
        const receipt={version:"greenhouse_hosted_browser_https_200_v2",review_id:lease.review_id,action_id:lease.action_id,review_digest:lease.review_digest,
          application_sha256:lease.expected_application_sha256,native:evidence};
        await evidenceStore("submit",lease.review_id,{proof:receipt});return {...result,receipt};
      },
      readEvidence:(kind,reviewId)=>evidenceStore(kind,reviewId),
      clearEvidence,
      async observe(url) {
        await permission(CONFIG.portalOrigin);
        if (!/^https:\/\/job-boards\.greenhouse\.io\/razorpaysoftwareprivatelimited\/jobs\/[0-9]+$/.test(url)) throw new Error("Unknown submit destination");
        const tabs=await chrome.tabs.query({url:[url,url+"/confirmation"]});
        if (tabs.length!==1 || !Number.isInteger(tabs[0].id)) throw new Error("Open exactly one tab for this attempted application or its confirmation page.");
        const [result]=await chrome.scripting.executeScript({target:{tabId:tabs[0].id,frameIds:[0]},func:(expected)=> {
          if (![expected,expected+"/confirmation"].includes(location.href) || window!==window.top) return null;
          const confirmation=location.href===expected+"/confirmation" && Boolean(document.querySelector(".confirmation .confirmation__content"));
          const waiting=Boolean(document.querySelector('input[type="password"],iframe[title*="challenge"],dialog[open]'));
          const validation=[...document.querySelectorAll('[role="alert"],.field-error')].some((node)=>node.getClientRects().length && node.textContent.trim());
          return {status:confirmation?"confirmation_observed":waiting?"waiting_user":validation?"validation_error":"unknown",url:location.href};
        },args:[url]});
        if (!result?.result || !result.documentId) throw new Error("The current portal status could not be inspected.");
        return {...result.result,document_id:result.documentId};
      },
      async fill(url, mapping, expected, action) {
        const tab = await targetTab(url);
        await recoveryFence(context);
        const [result] = await chrome.scripting.executeScript({ target: { tabId: tab.id, documentIds: [expected.document_id] }, func: razorpayAdapter,
          args: [expected, action, { document_id: expected.document_id, frame_id: 0, field_mapping: mapping, review_protocol_version: expected.review_protocol_version ?? 1, upload: action.upload }] });
        return result?.documentId === expected.document_id && result?.result ? result.result : { outcome: "blocked", reason: "The reviewed document is no longer available" };
      },
    };
    let identity;
    if (nativeAssistance()) {
      const connection = await pairingEngine();
      if (!connection.status().connected || connection.state.phase !== "paired") throw new Error("Complete and check the reviewed native browser connection before application assistance.");
      // Native pairing assigns its durable device ID. The retained private key
      // stays in IndexedDB; the signed identity claim cannot replace that key.
      identity = { ...connection.transport.identity, device_id: connection.state.device_id };
    } else identity = await deviceIdentity();
    const transport = new AssistanceTransport({ configuration: CONFIG, identity, beforePost: () => permission(CONFIG.websiteOrigin) });
    const store = { read: () => scopedRead("assistance_checkpoint", context),
      write: (value) => scopedWrite("assistance_checkpoint", value, context) };
    const runtime = await new AssistanceRuntime({ transport, browser, store }).initialise();
    if (nativeAssistance()) await runtime.check();
    return runtime;
  })();
}

const nativeMode = () => ["pairing_only", "local_native_fixture"].includes(CONFIG.mode) || nativeAssistance();
let pairingPromise;
function boundedStorage(operation) {
  let timer;
  return Promise.race([operation, new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error("Connection storage is unavailable")), 3000);
  })]).finally(() => clearTimeout(timer));
}
async function pairingEngine() {
  return pairingPromise ??= (async () => {
    const context = localRecoveryContext;
    if (!nativeMode() || CONFIG.extensionId !== chrome.runtime.id) throw new Error("Reviewed browser connection configuration is unavailable");
    const checkPermission = async () => {
      await recoveryFence(context);
      if (!await chrome.permissions.contains({ origins: [`${CONFIG.websiteOrigin}/*`] })) throw new Error("The exact HireWiz connection permission is missing or revoked");
    };
    const configuration = nativeAssistance() ? { ...CONFIG, ...CONFIG.nativePairing, mode: "pairing_only" } : CONFIG;
    const prior = await scopedRead("pairing_checkpoint", context);
    const transport = new PairingTransport({ configuration, identity: await deviceIdentity({ requireRetained: context !== null || prior !== null,
      slot: context?.slot ?? "device" }), beforePost: checkPermission });
    const store = {
      read: () => scopedRead("pairing_checkpoint", context),
      write: (value) => scopedWrite("pairing_checkpoint", value, context),
    };
    return new PairingRuntime({ transport, store, checkPermission }).initialise();
  })();
}

let enginePromise;
async function engine() {
  return enginePromise ??= (async () => {
    const identity = await deviceIdentity();
    const scope = (origin) => `${origin}/*`;
    const checkPermission = async (origin) => {
      if (origin !== CONFIG.portalOrigin || !await chrome.permissions.contains({ origins: [scope(origin)] })) throw new Error("Exact fixture-origin permission is missing or revoked");
    };
    async function targetTab(target) {
      await checkPermission(target.origin);
      const tabs = await chrome.tabs.query({ url: target.url });
      if (tabs.length !== 1) throw new Error("Open exactly one matching local fixture tab");
      return tabs[0];
    }
    const browser = {
      async inspect(target) {
        const tab = await targetTab(target);
        const [result] = await chrome.scripting.executeScript({ target: { tabId: tab.id, frameIds: [0] }, func: localAdapter });
        if (!result?.documentId || !result.result || result.result.outcome === "blocked") throw new Error(result?.result?.reason || "Cannot inspect the current fixture page");
        return { ...result.result, document_id: result.documentId, permission: true };
      },
      async fill(target, action) {
        const tab = await targetTab(target);
        const c = action.command;
        const expected = { origin: target.origin, url: target.url, user_id: c.user_id, tenant_id: c.tenant_id, employer_key: c.employer_key,
          canonical_opening_key: c.canonical_opening_key, form_version: c.form.version, fields: c.form.fields };
        const [result] = await chrome.scripting.executeScript({ target: { tabId: tab.id, documentIds: [action.document_id] }, func: localAdapter, args: [expected, { field_id: action.field.id, value: action.value, deadline: action.deadline }] });
        return result?.result ?? { outcome: "blocked", reason: "The reviewed document is no longer available" };
      },
    };
    const store = { read: async () => (await chrome.storage.local.get("checkpoint")).checkpoint ?? null, write: (checkpoint) => chrome.storage.local.set({ checkpoint }) };
    return new Companion({ configuration: CONFIG, identity, transport: new FixtureTransport(CONFIG, identity), browser, store });
  })();
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  // No page/content-script/external messages may trigger a privileged operation.
  if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL("popup.html")) return false;
  const exclusive = ["lost_key_review", "lost_key_commit"].includes(message?.type);
  recoveryGate.run(exclusive, async () => {
    if (["lost_key_review", "lost_key_commit", "lost_key_inspect"].includes(message?.type)) {
      const keys = message.type === "lost_key_commit" ? "acknowledged,recovery_id,type" : "type";
      if (!nativeAssistance() || CONFIG.extensionId !== chrome.runtime.id || Object.keys(message).sort().join(",") !== keys) throw new Error(RECOVERY_UNAVAILABLE);
      if (message.type === "lost_key_inspect") return recovery.inspect();
      if (!missingKeyObserved) throw new Error(RECOVERY_UNAVAILABLE);
      if (message.type === "lost_key_review") return recovery.review();
      const result = await recovery.commit(message);
      pairingPromise = assistancePromise = null; localRecoveryContext = null; missingKeyObserved = false;
      return result;
    }
    if (CONFIG.mode === "reviewed_assistance" && (!message || typeof message !== "object" || Array.isArray(message) || typeof message.type !== "string"
        || Object.keys(message).some((key) => !["type", "application_id"].includes(key)))) throw new Error("Unsupported assistance operation");
    if (nativeAssistance()) localRecoveryContext = await recovery.context();
    if (CONFIG.mode === "reviewed_assistance") {
      if (nativeAssistance()) {
        const connection = await pairingEngine();
        if (message.type === "status") return { mode: CONFIG.mode, native_pairing: true,
          connection: connection.status(), assistance: connection.status().connected ? (await assistanceEngine()).status() : null };
        const operations = { pairing_start: "start", pairing_complete: "complete", pairing_refresh: "refresh",
          pairing_cancel: "cancel", pairing_resume: "resume", pairing_inspect: "inspectUnknown",
          pairing_resume_existing: "resumeExisting", pairing_archive: "archiveUnknown" };
        if (message.type === "pairing_status") return { mode: CONFIG.mode, ...connection.status() };
        if (Object.hasOwn(operations, message.type)) {
          if (message.type === "pairing_cancel" && assistancePromise) await (await assistancePromise).cancel();
          const result = await connection[operations[message.type]]();
          return { mode: CONFIG.mode, ...result };
        }
        if (!connection.status().connected) throw new Error("Check the current native browser connection before application assistance.");
        if (message.type === "assistance_connect") throw new Error("Use the candidate-reviewed browser connection above.");
      }
      const current = await assistanceEngine();
      if (["status", "assistance_status"].includes(message.type)) return { mode: CONFIG.mode, ...current.status() };
      if (message.type === "assistance_connect") return current.start();
      if (message.type === "assistance_check") return current.check();
      if (message.type === "assistance_prepare") return current.prepare(message.application_id);
      if (message.type === "assistance_prepare_attachment") return current.prepare(message.application_id, 3);
      if (message.type === "assistance_prepare_native_upload") return current.prepare(message.application_id,3,true);
      if (message.type === "assistance_prepare_submit") return current.prepare(message.application_id, 4);
      if (message.type === "assistance_reconcile") return current.reconcile();
      if (message.type === "assistance_fill") return current.fill();
      if (message.type === "assistance_cancel") return current.cancel();
      throw new Error("Unsupported assistance operation");
    }
    if (nativeMode()) {
      if (!message || typeof message !== "object" || Array.isArray(message)
          || Object.keys(message).join(",") !== "type" || typeof message.type !== "string") throw new Error("Unsupported connection operation");
      const current = await pairingEngine();
      if (message.type === "status" || message.type === "pairing_status") return { mode: CONFIG.mode, ...current.status() };
      if (message.type === "pairing_start") return current.start();
      if (message.type === "pairing_complete") return current.complete();
      if (message.type === "pairing_refresh") return current.refresh();
      if (message.type === "pairing_cancel") return current.cancel();
      if (message.type === "pairing_resume") return current.resume();
      if (message.type === "pairing_inspect") return current.inspectUnknown();
      if (message.type === "pairing_resume_existing") return current.resumeExisting();
      if (message.type === "pairing_archive") return current.archiveUnknown();
      // Identity connection never enables the fixture application protocol.
      throw new Error("Application actions require separately reviewed authority");
    }
    if (message.type === "status") {
      if (CONFIG.mode === "disabled") return { mode: "disabled" };
      const current = await engine();
      return { mode: CONFIG.mode, connected: Boolean(current.claim), busy: current.busy, checkpoint: await current.store.read() };
    }
    if (CONFIG.mode !== "local_fixture") throw new Error("Companion is disabled; no production authorization is available.");
    const current = await engine();
    if (message.type === "enroll") return current.enroll(message.grant);
    if (message.type === "prepare") return current.prepare(message.application_id);
    if (message.type === "fill") return current.approveAndFill(message.command_id, message.review_digest, message.acknowledged === true);
    if (message.type === "cancel") return current.cancel();
    throw new Error("Unsupported companion operation");
  }).then((value) => respond({ ok: true, value }), (error) => {
    const phase = message?.type === "status" ? deviceFailurePhase(error) : null;
    if (phase === "MISSING-RETAINED" && localRecoveryContext === null) missingKeyObserved = true;
    const recoveryOperation = ["lost_key_review", "lost_key_commit", "lost_key_inspect"].includes(message?.type);
    const missingRecoveredKey = phase === "MISSING-RETAINED" && localRecoveryContext !== null;
    respond({ ok: false, error: recoveryOperation || missingRecoveredKey ? RECOVERY_UNAVAILABLE : error.message,
      ...(phase && !missingRecoveredKey ? { device_phase: phase } : {}) });
  });
  return true;
});
