// This module consumes only Chrome's native webRequest events. Page messages,
// DOM success and fetch.ok never establish an employer acknowledgement.
import { digest } from "./protocol.js";

const HEX = /^[a-f0-9]{64}$/;
const PORTAL = "https://job-boards.greenhouse.io";
const SUBMIT = /^https:\/\/boards\.greenhouse\.io\/razorpaysoftwareprivatelimited\/jobs\/[0-9]+$/;
const PRESIGN = "https://boards.greenhouse.io/uncacheable_attributes/presigned_fields?fields%5B%5D=resume";
export const PRESIGN_URL = PRESIGN;
export const nativeStorageOrigin = value => {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password && !url.port && !url.search && !url.hash
      && url.pathname === "/" && /^[a-z0-9][a-z0-9.-]{0,120}\.s3(?:\.[a-z0-9-]+)?\.amazonaws\.com$/.test(url.hostname)
      ? url.origin : null;
  } catch { return null; }
};
const fail = () => new Error("Native employer acknowledgement is unavailable. The action was not automatically repeated.");
const boundedId = value => typeof value === "string" && value.length > 0 && value.length <= 160;
const hubs=new WeakMap();
function nativeHub(chromeApi){
  if(hubs.has(chromeApi))return hubs.get(chromeApi);
  const hub={clients:new Set(),ready:null};
  const filter={urls:["https://boards.greenhouse.io/*","https://*.amazonaws.com/*"]};
  const route=(method,detail)=>{for(const client of [...hub.clients])if(detail.url===client.url)client[method](detail);};
  chromeApi.webRequest.onBeforeRequest.addListener(detail=>route("before",detail),filter,["requestBody"]);
  chromeApi.webRequest.onHeadersReceived.addListener(detail=>route("headers",detail),filter,["responseHeaders"]);
  chromeApi.webRequest.onBeforeRedirect.addListener(detail=>{for(const client of [...hub.clients])if(client.matches(detail))client.reject();},filter);
  chromeApi.webRequest.onCompleted.addListener(detail=>route("complete",detail),filter);
  chromeApi.webRequest.onErrorOccurred.addListener(detail=>{for(const client of [...hub.clients])if(client.matches(detail))client.reject();},filter);
  // One registration barrier per worker, rather than consuming Chrome's bounded
  // handlerBehaviorChanged quota for each upload and submit. Unscoped traffic is
  // discarded synchronously; only one explicitly armed exchange retains bytes.
  hub.ready=chromeApi.webRequest.handlerBehaviorChanged();hubs.set(chromeApi,hub);return hub;
}

export class GreenhouseNativeExchange {
  constructor({ kind, url, deadline, now = () => Date.now(), chromeApi = globalThis.chrome, browserVersion = globalThis.navigator?.userAgent,
    bodySha256 = null, bodySize = 0, formFields = null, tabId = -1, documentId = null, validateBody = null }) {
    const version = /(?:Chrome|Chromium)\/(\d+)\./.exec(browserVersion || "");
    if (!version || Number(version[1]) < 144 || !["presign", "upload", "submit"].includes(kind)
      || !Number.isSafeInteger(deadline) || deadline <= now() || deadline - now() > 10000
      || kind === "presign" && url !== PRESIGN
      || kind === "submit" && (!SUBMIT.test(url) || !Number.isInteger(tabId) || tabId < 0 || !boundedId(documentId) || typeof validateBody !== "function")
      || kind === "upload" && (!nativeStorageOrigin(url) || !HEX.test(bodySha256 || "") || !Number.isSafeInteger(bodySize) || bodySize < 1 || bodySize > 10 * 1024 * 1024 + 65536)
      || kind !== "submit" && (tabId !== -1 || documentId !== null)) throw fail();
    Object.assign(this, {kind,url:kind === "upload" ? new URL(url).href : url,deadline,now,chromeApi,bodySha256,bodySize,tabId,documentId,validateBody});
    this.formFields=formFields ? Object.freeze({...formFields}) : null;
    this.browserMajor = Number(version[1]); this.used = false; this.closed = false; this.native = null; this.body = null;
    this.listeners = []; this.timer = null; this.failure = null; this.completed = false;
  }
  install() {
    if (this.used || this.closed || this.now() >= this.deadline) throw fail();
    this.used = true;
    try {
      this.hub=nativeHub(this.chromeApi);this.hub.clients.add(this);
    } catch { this.reject(); throw fail(); }
  }
  matches(detail) { return detail?.url === this.url && (!this.native || detail.requestId === this.native.request_id); }
  reject() { this.failure = true; this.resolve?.(null); this.cleanup(); }
  cleanup() {
    this.closed = true; clearTimeout(this.timer);
    this.hub?.clients.delete(this);
    for (const [event, listener] of this.listeners.splice(0)) event.removeListener(listener);
  }
  before(detail) {
    if (detail.url !== this.url) return;
    const method = this.kind === "presign" ? "GET" : "POST";
    const initiator = this.kind === "submit" ? PORTAL : `chrome-extension://${this.chromeApi.runtime.id}`;
    if (this.native || detail.method !== method || detail.initiator !== initiator || detail.tabId !== this.tabId
      || this.kind === "submit" && (detail.frameId !== 0 || detail.documentId !== this.documentId)
      || !boundedId(detail.requestId) || !Number.isFinite(detail.timeStamp) || this.now() >= this.deadline
      || detail.timeStamp > this.deadline || detail.timeStamp < this.now() - 10000) return this.reject();
    this.native = {request_id:detail.requestId,method,url:this.url,tab_id:detail.tabId,frame_id:this.kind === "submit" ? 0 : -1,
      document_id:this.documentId,initiator,started_at_ms:Math.floor(detail.timeStamp),browser_major:this.browserMajor};
    if (this.kind === "presign") {
      if (detail.requestBody) return this.reject();
      this.body = Promise.resolve({request_body_sha256:null,request_body_size:0,request_body_source:"none",native_body_crosscheck:"none"}); return;
    }
    if(this.kind==="upload" && detail.requestBody?.formData && !detail.requestBody.error && !detail.requestBody.raw){
      const fields=detail.requestBody.formData,expected=this.formFields;
      if(!expected || Object.keys(fields).length!==Object.keys(expected).length+1 || !Array.isArray(fields.file) || fields.file.length!==1
        || typeof fields.file[0]!=="string" || Object.entries(expected).some(([key,value])=>!Array.isArray(fields[key]) || fields[key].length!==1 || fields[key][0]!==value))return this.reject();
      // Chrome parsed multipart and omitted raw file bytes. This hash describes
      // the original private immutable ArrayBuffer passed once to our own fetch,
      // never a page claim or a claim of native raw-file introspection.
      this.body=Promise.resolve({request_body_sha256:this.bodySha256,request_body_size:this.bodySize,
        request_body_source:"extension_owned_arraybuffer",native_body_crosscheck:"form_fields"});return;
    }
    const raw = detail.requestBody?.raw;
    if (detail.requestBody?.error || detail.requestBody?.formData || !Array.isArray(raw) || !raw.length || raw.length > 64
      || raw.some(part => !(part.bytes instanceof ArrayBuffer) || part.file != null)) return this.reject();
    const length = raw.reduce((total,part)=>total+part.bytes.byteLength,0);
    if (length < 1 || length > (this.kind === "upload" ? 10 * 1024 * 1024 + 65536 : 65536)) return this.reject();
    const bytes = new Uint8Array(length); let offset = 0;
    for (const part of raw) { bytes.set(new Uint8Array(part.bytes),offset); offset += part.bytes.byteLength; }
    this.body = (async () => {
      try {
        const sha256 = await digest(bytes);
        if (this.kind === "upload" && (length !== this.bodySize || sha256 !== this.bodySha256)) throw fail();
        const binding = this.kind === "submit" ? await this.validateBody(bytes) : {};
        if (!binding || typeof binding !== "object" || Array.isArray(binding)) throw fail();
        return {request_body_sha256:sha256,request_body_size:length,request_body_source:this.kind==="upload"?"extension_owned_arraybuffer":"native_raw",native_body_crosscheck:"raw",...binding};
      } finally { bytes.fill(0); }
    })();
    // Native completion can arrive before WebCrypto. Always consume rejection.
    this.body.catch(()=>this.reject());
  }
  headers(detail) {
    if (!this.matches(detail)) return;
    if (!this.native || this.native.status_code != null || detail.statusCode !== (this.kind === "upload" ? detail.statusCode : 200)
      || this.kind === "upload" && ![200,201,204].includes(detail.statusCode)
      || (detail.responseHeaders || []).some(header => ["location","refresh"].includes(header.name?.toLowerCase()))) return this.reject();
    // Native completion for this exact HTTPS URL relies on the browser's normal
    // TLS validation. We request no certificate/error overrides and claim no
    // certificate pinning or certificate-byte introspection.
    Object.assign(this.native,{status_code:detail.statusCode,https_validation:"browser_default"});
  }
  async complete(detail) {
    if (!this.matches(detail)) return;
    if (!this.native || this.completed || this.native.status_code == null || detail.statusCode !== this.native.status_code
      || detail.fromCache !== false || typeof detail.ip !== "string" || detail.ip.length < 3 || detail.ip.length > 64
      || !Number.isFinite(detail.timeStamp) || detail.timeStamp < this.native.started_at_ms || detail.timeStamp > this.deadline) return this.reject();
    this.completed = true;
    try {
      const body = await this.body;
      if (!body || this.failure || this.closed) throw fail();
      this.resolve?.({...this.native,...body,completed_at_ms:Math.floor(detail.timeStamp),from_cache:false,redirected:false,ip:detail.ip});
      this.cleanup();
    } catch { this.reject(); }
  }
  async run(send) {
    if (typeof send !== "function") throw fail();
    this.install();
    const observed = new Promise(resolve => {this.resolve=resolve;this.timer=setTimeout(()=>this.reject(),Math.max(1,this.deadline-this.now()));});
    try {
      await this.hub.ready;
      if(this.failure || this.closed || this.now()>=this.deadline)throw fail();
      // This callback is a fixed extension-owned fetch or one leased native click.
      // A fetch response alone is insufficient; every accepted result joins the
      // unique native event, exact bytes and direct secure acknowledgement.
      const result = await Promise.race([Promise.resolve().then(send),observed.then(evidence=>{
        if (!evidence) throw fail();
        return new Promise(()=>{});
      })]);
      const evidence = await observed;
      if (!evidence || this.failure) throw fail();
      return {result,evidence};
    } catch (failure) {this.reject();throw failure;} finally {this.cleanup();}
  }
  cancel() {this.reject();}
}
