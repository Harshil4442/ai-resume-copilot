// Event fixtures test the verifier. Only native-upload-exposure-browser.test.js
// establishes real installed Chrome byte visibility; no fake TLS receipt claim.
import assert from "node:assert/strict";
import test from "node:test";
import { GreenhouseNativeExchange, nativeStorageOrigin } from "../extension/greenhouse-native-network.js";
import { digest } from "../extension/protocol.js";

const ORIGIN="https://approved-fixture.s3.amazonaws.com";
const EXTENSION="pnjmegppacleipajnifjmbcopjjffklb";
class Event {
  constructor(){this.listeners=new Set();}
  addListener(listener){this.listeners.add(listener);}
  removeListener(listener){this.listeners.delete(listener);}
  emit(value){for(const listener of this.listeners) listener(value);}
}
async function fixture(changes={}) {
  const webRequest=Object.fromEntries(["onBeforeRequest","onHeadersReceived","onBeforeRedirect","onCompleted","onErrorOccurred"].map(name=>[name,new Event()]));
  webRequest.handlerBehaviorChanged=async()=>{};
  const chromeApi={runtime:{id:EXTENSION},webRequest},bytes=new TextEncoder().encode("exact multipart including approved binary\u0000\u00ff");
  const now=Date.now(),options={kind:"upload",url:ORIGIN,deadline:now+1000,chromeApi,browserVersion:"Chrome/151.0.0.0",bodySha256:await digest(bytes),bodySize:bytes.length,...changes};
  const exchange=new GreenhouseNativeExchange(options);
  const before={url:ORIGIN+"/",method:"POST",requestId:"native-owned-1",initiator:`chrome-extension://${EXTENSION}`,tabId:-1,frameId:-1,timeStamp:now,
    requestBody:{raw:[{bytes:bytes.buffer}]}};
  const headers={url:ORIGIN+"/",requestId:before.requestId,statusCode:204,responseHeaders:[]};
  const complete={url:ORIGIN+"/",requestId:before.requestId,statusCode:204,fromCache:false,ip:"127.0.0.1",timeStamp:now+1};
  return {exchange,webRequest,before,headers,complete,options,async run(mutate=()=>{}){
    return exchange.run(async()=>{mutate(this);webRequest.onBeforeRequest.emit(before);webRequest.onHeadersReceived.emit(headers);webRequest.onCompleted.emit(complete);return "fetch-response-is-not-authority";});
  }};
}

test("strict native fixture joins exact outgoing bytes and browser-validated direct HTTPS upload acknowledgement",async()=>{
  const f=await fixture(),result=await f.run();assert.equal(result.evidence.request_body_sha256,f.options.bodySha256);
  assert.equal(result.evidence.status_code,204);assert.equal(result.evidence.from_cache,false);assert.equal(result.evidence.redirected,false);
  assert.equal(result.evidence.request_body_source,"extension_owned_arraybuffer");assert.equal(result.evidence.native_body_crosscheck,"raw");
  assert.equal(result.evidence.https_validation,"browser_default");assert.equal(result.evidence.certificate_sha256,undefined);assert.equal(result.evidence.tls_state,undefined);assert.equal(result.evidence.request_id,"native-owned-1");
  await assert.rejects(()=>f.exchange.run(async()=>{}));
  assert.equal(f.exchange.hub.clients.size,0);
});

test("parsed multipart crosschecks exact nonfile fields while truthfully retaining owned-buffer provenance",async()=>{
  const formFields={key:"resume/approved.pdf",policy:"approved-policy","Content-Type":"application/octet-stream"};
  const f=await fixture({formFields});f.before.requestBody={formData:{...Object.fromEntries(Object.entries(formFields).map(([key,value])=>[key,[value]])),file:["approved.pdf"]}};
  const result=await f.run();assert.equal(result.evidence.request_body_sha256,f.options.bodySha256);
  assert.equal(result.evidence.request_body_source,"extension_owned_arraybuffer");assert.equal(result.evidence.native_body_crosscheck,"form_fields");
  for(const changed of ["key","policy","extra","file"]){
    const invalid=await fixture({formFields});invalid.before.requestBody=structuredClone(f.before.requestBody);
    invalid.before.requestBody.formData[changed]=changed==="file"?[]:["changed"];
    await assert.rejects(()=>invalid.run());
  }
});

for(const [name,mutate] of [
  ["wrong exact body",f=>{f.before.requestBody.raw[0].bytes=new Uint8Array([1,2]).buffer;}],
  ["omitted File bytes",f=>{f.before.requestBody.raw.push({file:"untrusted-browser-file"});}],
  ["parsed form only",f=>{f.before.requestBody={formData:{file:["approved.pdf"]}};}],
  ["wrong initiator",f=>{f.before.initiator="https://job-boards.greenhouse.io";}],
  ["wrong tab",f=>{f.before.tabId=12;}],
  ["wrong method",f=>{f.before.method="GET";}],
  ["expired dispatch",f=>{f.before.timeStamp=f.options.deadline+1;}],
  ["cache",f=>{f.complete.fromCache=true;}],
  ["synthetic response without IP",f=>{delete f.complete.ip;}],
  ["pending 202",f=>{f.headers.statusCode=f.complete.statusCode=202;}],
  ["response outside lease",f=>{f.complete.timeStamp=f.options.deadline+1;}],
  ["redirect header",f=>{f.headers.responseHeaders=[{name:"Location",value:ORIGIN+"/other"}];}],
]) test(`native fixture rejects ${name}`,async()=>{const f=await fixture();await assert.rejects(()=>f.run(mutate));});

test("native redirect, duplicate request and lost completion never become replay authority",async()=>{
  for(const mode of ["redirect","duplicate","lost"]){
    const f=await fixture({deadline:Date.now()+35});
    await assert.rejects(()=>f.exchange.run(async()=>{
      f.webRequest.onBeforeRequest.emit(f.before);f.webRequest.onHeadersReceived.emit(f.headers);
      if(mode==="redirect")f.webRequest.onBeforeRedirect.emit({url:ORIGIN+"/",requestId:f.before.requestId});
      if(mode==="duplicate")f.webRequest.onBeforeRequest.emit({...f.before,requestId:"second-native-request"});
      return "fetch-ok-is-insufficient";
    }));
    await assert.rejects(()=>f.exchange.run(async()=>{throw new Error("must not retry");}));
  }
});

test("cancellation aborts waiting without a second send or reconstructed acknowledgement",async()=>{
  const f=await fixture();let sends=0;
  await assert.rejects(()=>f.exchange.run(async()=>{sends++;f.webRequest.onBeforeRequest.emit(f.before);f.exchange.cancel();return new Promise(()=>{});}));
  assert.equal(sends,1);await assert.rejects(()=>f.exchange.run(async()=>{sends++;}));assert.equal(sends,1);
});

test("final hosted submit requires exact top-document, validated JSON and direct 200",async()=>{
  const endpoint="https://boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/4740333005";
  const f=await fixture({kind:"submit",url:endpoint,tabId:42,documentId:"exact-document",bodySha256:null,bodySize:0,
    validateBody:bytes=>{assert.ok(bytes.length);return {application_sha256:"cd".repeat(32)};}});
  Object.assign(f.before,{url:endpoint,tabId:42,frameId:0,documentId:"exact-document",initiator:"https://job-boards.greenhouse.io"});
  Object.assign(f.headers,{url:endpoint,statusCode:200});Object.assign(f.complete,{url:endpoint,statusCode:200});
  const result=await f.run();assert.equal(result.evidence.application_sha256,"cd".repeat(32));assert.equal(result.evidence.document_id,"exact-document");
});

test("unsupported Chrome/native API and unreviewed storage origins stay closed",async()=>{
  const f=await fixture();assert.throws(()=>new GreenhouseNativeExchange({...f.options,browserVersion:"Chrome/143.0.0.0"}));
  for(const origin of ["https://s3.amazonaws.com","https://evil.example","https://a.s3.amazonaws.com.evil.test","http://approved-fixture.s3.amazonaws.com","https://a.s3.amazonaws.com/path"])
    assert.equal(nativeStorageOrigin(origin),null);
  assert.equal(nativeStorageOrigin(ORIGIN),ORIGIN);
  f.webRequest.onHeadersReceived.addListener=()=>{throw new Error("native observation unavailable");};
  await assert.rejects(()=>f.exchange.run(async()=>{throw new Error("must not dispatch");}));
});
