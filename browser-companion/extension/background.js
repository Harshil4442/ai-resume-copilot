import { CONFIG } from "./config.js";
import { Companion } from "./protocol.js";
import { deviceIdentity } from "./device.js";
import { FixtureTransport } from "./transport.js";
import { localAdapter } from "./local-adapter.js";
import { PairingTransport } from "./pairing-transport.js";
import { PairingRuntime } from "./pairing-runtime.js";
import { AssistanceTransport } from "./assistance-transport.js";
import { AssistanceRuntime } from "./assistance-runtime.js";
import { razorpayAdapter } from "./razorpay-adapter.js";

let assistancePromise;
async function assistanceEngine() {
  return assistancePromise ??= (async () => {
    if (CONFIG.mode !== "reviewed_assistance" || CONFIG.extensionId !== chrome.runtime.id) throw new Error("Reviewed assistance configuration is unavailable");
    const permission = async (origin) => {
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
    const browser = {
      async inspect(url, mapping, documentId = null, version = 1) {
        const tab = await targetTab(url);
        if (!documentId) {
          const [identity] = await chrome.scripting.executeScript({ target: { tabId: tab.id, frameIds: [0] }, func: () => true });
          documentId = identity?.documentId;
        }
        if (!documentId) throw new Error("Chrome document identity is unavailable");
        const [result] = await chrome.scripting.executeScript({ target: { tabId: tab.id, documentIds: [documentId] }, func: razorpayAdapter,
          args: [null, null, { document_id: documentId, frame_id: 0, field_mapping: mapping, review_protocol_version: version }] });
        if (result?.documentId !== documentId || result?.result?.outcome !== "inspected") throw new Error(result?.result?.reason || "The exact current portal form is not supported");
        return result.result;
      },
      async fill(url, mapping, expected, action) {
        const tab = await targetTab(url);
        const [result] = await chrome.scripting.executeScript({ target: { tabId: tab.id, documentIds: [expected.document_id] }, func: razorpayAdapter,
          args: [expected, action, { document_id: expected.document_id, frame_id: 0, field_mapping: mapping, review_protocol_version: expected.review_protocol_version ?? 1 }] });
        return result?.documentId === expected.document_id && result?.result ? result.result : { outcome: "blocked", reason: "The reviewed document is no longer available" };
      },
    };
    const identity = await deviceIdentity();
    const transport = new AssistanceTransport({ configuration: CONFIG, identity, beforePost: () => permission(CONFIG.websiteOrigin) });
    const store = { read: async () => (await boundedStorage(chrome.storage.local.get("assistance_checkpoint"))).assistance_checkpoint ?? null,
      write: (value) => boundedStorage(chrome.storage.local.set({ assistance_checkpoint: value })) };
    return new AssistanceRuntime({ transport, browser, store }).initialise();
  })();
}

const nativeMode = () => ["pairing_only", "local_native_fixture"].includes(CONFIG.mode);
let pairingPromise;
function boundedStorage(operation) {
  let timer;
  return Promise.race([operation, new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error("Connection storage is unavailable")), 3000);
  })]).finally(() => clearTimeout(timer));
}
async function pairingEngine() {
  return pairingPromise ??= (async () => {
    if (!nativeMode() || CONFIG.extensionId !== chrome.runtime.id) throw new Error("Reviewed browser connection configuration is unavailable");
    const checkPermission = async () => {
      if (!await chrome.permissions.contains({ origins: [`${CONFIG.websiteOrigin}/*`] })) throw new Error("The exact HireWiz connection permission is missing or revoked");
    };
    const transport = new PairingTransport({ configuration: CONFIG, identity: await deviceIdentity(), beforePost: checkPermission });
    const store = {
      read: async () => (await boundedStorage(chrome.storage.local.get("pairing_checkpoint"))).pairing_checkpoint ?? null,
      write: (value) => boundedStorage(chrome.storage.local.set({ pairing_checkpoint: value })),
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
  (async () => {
    if (CONFIG.mode === "reviewed_assistance") {
      if (!message || typeof message !== "object" || Array.isArray(message) || typeof message.type !== "string"
        || Object.keys(message).some((key) => !["type", "application_id"].includes(key))) throw new Error("Unsupported assistance operation");
      const current = await assistanceEngine();
      if (message.type === "status") return { mode: CONFIG.mode, ...current.status() };
      if (message.type === "assistance_connect") return current.start();
      if (message.type === "assistance_check") return current.check();
      if (message.type === "assistance_prepare") return current.prepare(message.application_id);
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
  })().then((value) => respond({ ok: true, value }), (error) => respond({ ok: false, error: error.message }));
  return true;
});
