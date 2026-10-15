import { canonical, digest } from "./protocol.js";
import { GreenhouseNativeExchange, nativeStorageOrigin, PRESIGN_URL } from "./greenhouse-native-network.js";

const HEX=/^[a-f0-9]{64}$/;
const TARGET=/^https:\/\/job-boards\.greenhouse\.io\/razorpaysoftwareprivatelimited\/jobs\/[0-9]+$/;
const unavailable=()=>new Error("The exact reviewed native resume upload is unavailable. No upload was automatically repeated.");
const cancel=stream=>{try{void stream?.cancel().catch(()=>undefined);}catch{/* Preserve original failure. */}};
async function boundedResponse(response,max=65536,allowEmpty=false) {
  const reader=response.body?.getReader();if(!reader){if(allowEmpty)return new Uint8Array();throw unavailable();}
  const chunks=[];let size=0;
  try{for(;;){const next=await reader.read();if(next.done)break;size+=next.value.byteLength;if(size>max)throw unavailable();chunks.push(next.value);}}
  finally{cancel(reader);try{reader.releaseLock();}catch{/* Preserve original failure. */}}
  const bytes=new Uint8Array(size);let offset=0;for(const part of chunks){bytes.set(part,offset);offset+=part.length;}return bytes;
}
export async function reviewedUploadPlan(snapshot,responseBytes,presignEvidence,{now=Date.now(),uniqueId=crypto.randomUUID()}={}) {
  if(!snapshot || Object.keys(snapshot).sort().join(",")!=="resume,url" || !snapshot.resume || Object.keys(snapshot.resume).sort().join(",")!=="fields,key")throw unavailable();
  const origin=nativeStorageOrigin(snapshot.url),fields=snapshot.resume.fields,template=snapshot.resume.key;
  if(!origin || !fields || typeof fields!=="object" || Array.isArray(fields) || Object.keys(fields).length<3 || Object.keys(fields).length>24
    || typeof template!=="string" || template.length<1 || template.length>1024 || !/^[A-Za-z0-9/_.{}-]+$/.test(template)
    || template.startsWith("/") || template.split("/").includes("..") || !/^[a-f0-9-]{36}$/.test(uniqueId)
    || Object.entries(fields).some(([key,value])=>!/^[A-Za-z0-9_-]{1,80}$/.test(key) || ["key","file","utf8","authenticity_token","Content-Type"].includes(key)
      || typeof value!=="string" || !value || value.length>16384) || new TextEncoder().encode(canonical(fields)).length>32768)throw unavailable();
  const key=template.replace("{timestamp}",String(now)).replace("{unique_id}",uniqueId.replaceAll("-",""));
  if(/[{}]/.test(key))throw unavailable();
  const policyText=fields.policy||fields.Policy;
  if(typeof policyText!=="string" || !/^[A-Za-z0-9+/]+={0,2}$/.test(policyText))throw unavailable();
  let policyBytes,policy;try{policyBytes=Uint8Array.from(atob(policyText),character=>character.charCodeAt(0));policy=JSON.parse(new TextDecoder("utf8",{fatal:true}).decode(policyBytes));}catch{throw unavailable();}
  const expiry=Date.parse(policy?.expiration),bucket=new URL(origin).hostname.split(".s3")[0];
  if(!policy || Object.keys(policy).sort().join(",")!=="conditions,expiration" || !Array.isArray(policy.conditions) || policy.conditions.length<2 || policy.conditions.length>32
    || !Number.isSafeInteger(expiry) || expiry<=now+15000 || expiry>now+7200000
    || !policy.conditions.some(condition=>condition && !Array.isArray(condition) && condition.bucket===bucket)
    || !policy.conditions.some(condition=>Array.isArray(condition) && condition.length===3 && condition[0]==="starts-with" && condition[1]==="$key"
      && typeof condition[2]==="string" && condition[2].length>0 && key.startsWith(condition[2])
      || condition && !Array.isArray(condition) && condition.key===key))throw unavailable();
  const formFields={utf8:"✓",...fields,key,authenticity_token:"1234","Content-Type":"application/octet-stream"};
  const plan={transport:"greenhouse-owned-upload-browser-https-v2",body_provenance:"extension_owned_arraybuffer",presign_url:PRESIGN_URL,storage_origin:origin,storage_key:key,
    policy_sha256:await digest(policyBytes),fields_sha256:await digest(canonical(formFields)),presign_response_sha256:await digest(responseBytes),
    expires_at_ms:Math.min(expiry,now+300000),presign_evidence:presignEvidence};
  return {plan,formFields};
}

export function validateSubmittedApplication(expected,expectedSha256) {
  return async bytes=>{
    let envelope;try{const text=new TextDecoder("utf8",{fatal:true}).decode(bytes);envelope=JSON.parse(text);
      if(JSON.stringify(envelope)!==text)throw unavailable();}catch{throw unavailable();}
    const permitted=new Set(["job_application","fingerprint","authenticity_token","request_token","g-recaptcha-enterprise-token","security_code","captcha_retried"]);
    if(!envelope || typeof envelope!=="object" || Array.isArray(envelope) || !Object.hasOwn(envelope,"job_application")
      || Object.keys(envelope).some(key=>!permitted.has(key)) || canonical(envelope.job_application)!==canonical(expected)
      || !HEX.test(expectedSha256) || await digest(canonical(envelope.job_application))!==expectedSha256
      || Object.entries(envelope).some(([key,value])=>key!=="job_application" && (key==="captcha_retried" ? value!==true
        :key==="fingerprint" ? typeof value!=="string" || value.length>4096
        :typeof value!=="string" || !value || value.length>8192))
      || envelope.security_code && envelope["g-recaptcha-enterprise-token"])throw unavailable();
    return {application_sha256:expectedSha256};
  };
}

export class GreenhouseOwnedUpload {
  constructor({chromeApi=globalThis.chrome,fetcher=(...args)=>fetch(...args),now=()=>Date.now()}={}) {
    Object.assign(this,{chromeApi,fetcher,now});this.prepared=null;this.consumed=false;
  }
  async permission(origin=null) {
    const origins=["https://boards.greenhouse.io/*","https://job-boards.greenhouse.io/*",...(origin?[`${origin}/*`]:[])];
    if(!await this.chromeApi.permissions.contains({permissions:["webRequest"],origins}))throw unavailable();
  }
  async prepare(targetUrl,resumeComponentSha256) {
    if(!TARGET.test(targetUrl) || !HEX.test(resumeComponentSha256||""))throw unavailable();await this.permission();
    this.prepared=null;this.consumed=false;
    const started=this.now(),exchange=new GreenhouseNativeExchange({kind:"presign",url:PRESIGN_URL,deadline:started+10000,now:this.now,chromeApi:this.chromeApi});
    const {result,evidence}=await exchange.run(async()=>{
      const response=await this.fetcher(PRESIGN_URL,{method:"GET",credentials:"omit",redirect:"error",cache:"no-store",signal:AbortSignal.timeout(10000)});
      if(response.status!==200 || !(response.headers.get("Content-Type")||"").toLowerCase().startsWith("application/json"))throw unavailable();
      const bytes=await boundedResponse(response);let snapshot;try{snapshot=JSON.parse(new TextDecoder("utf8",{fatal:true}).decode(bytes));}catch{throw unavailable();}
      return {bytes,snapshot};
    });
    const prepared=await reviewedUploadPlan(result.snapshot,result.bytes,evidence,{now:this.now()});
    prepared.plan.resume_component_sha256=resumeComponentSha256;
    result.bytes.fill(0);this.prepared={...prepared,targetUrl};return structuredClone(prepared.plan);
  }
  async upload({lease,bytes,plan,beforeDispatch,cancelled=()=>false}) {
    // Own the approved inputs before the first yield. Callers retain their
    // buffers and objects; no awaited permission/callback can rewrite this send.
    bytes=bytes instanceof Uint8Array?bytes.slice():bytes;
    lease=structuredClone(lease);plan=structuredClone(plan);
    try{
      if(this.consumed || !this.prepared || canonical(plan)!==canonical(this.prepared.plan) || canonical(lease.upload_plan)!==canonical(plan)
        || lease.target_url!==this.prepared.targetUrl || canonical(lease.allowed_actions)!=='["upload_resume"]' || lease.review_protocol_version!==3
        || !(bytes instanceof Uint8Array) || bytes.length!==lease.artifact?.size_bytes || !HEX.test(lease.artifact?.sha256||"")
        || await digest(bytes)!==lease.artifact.sha256 || cancelled() || this.now()>=lease.deadline || this.now()>=plan.expires_at_ms
        || typeof beforeDispatch!=="function")throw unavailable();
      await this.permission(plan.storage_origin);
      // Never reconstruct this original private plan from a status/review response.
      this.consumed=true;
      const fields=this.prepared.formFields,form=new FormData();
      for(const [key,value] of Object.entries(fields))form.append(key,value);
      form.append("file",new File([bytes],lease.artifact.filename,{type:lease.artifact.media_type,lastModified:0}));
      const serialized=new Request(plan.storage_origin,{method:"POST",body:form}),body=await serialized.arrayBuffer();
      try {
        if(body.byteLength>lease.artifact.size_bytes+65536 || await digest(canonical(fields))!==plan.fields_sha256)throw unavailable();
        await beforeDispatch();await this.permission(plan.storage_origin);
        if(cancelled() || this.now()>=lease.deadline || this.now()>=plan.expires_at_ms)throw unavailable();
        const multipartSha256=await digest(body),exchange=new GreenhouseNativeExchange({kind:"upload",url:plan.storage_origin,deadline:lease.deadline,now:this.now,
          chromeApi:this.chromeApi,bodySha256:multipartSha256,bodySize:body.byteLength,formFields:fields});
        const {evidence}=await exchange.run(async()=>{
          if(cancelled() || this.now()>=lease.deadline)throw unavailable();
          const response=await this.fetcher(plan.storage_origin,{method:"POST",body,headers:{"Content-Type":serialized.headers.get("Content-Type")},
            credentials:"omit",redirect:"error",cache:"no-store",signal:AbortSignal.timeout(Math.max(1,lease.deadline-this.now()))});
          if(![200,201,204].includes(response.status))throw unavailable();await boundedResponse(response,65536,true);return true;
        });
        return {version:"greenhouse_owned_upload_browser_https_v2",review_id:lease.review_id,action_id:lease.action_id,review_digest:lease.review_digest,
          artifact_sha256:lease.artifact.sha256,artifact_size_bytes:lease.artifact.size_bytes,filename:lease.artifact.filename,target_url:lease.target_url,
          document_id:lease.document_id,tab_id:lease.native_context.tab_id,uploaded_url:`${plan.storage_origin}/${plan.storage_key}`,
          multipart_sha256:multipartSha256,multipart_size:body.byteLength,native:evidence};
      }finally{new Uint8Array(body).fill(0);this.prepared=null;}
    }finally{if(bytes instanceof Uint8Array)bytes.fill(0);}
  }
  clear(){this.prepared=null;this.consumed=true;}
}
