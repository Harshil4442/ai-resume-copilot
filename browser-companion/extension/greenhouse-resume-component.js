// Executed in MAIN only for the known Greenhouse Resume/CV component. It never
// sends a network request or fabricates an upload acknowledgement. The privileged
// extension supplies a URL only after its genuine native exact-byte upload ACK.
export async function greenhouseResumeComponent(operation, expected) {
  const fail=()=>({outcome:"blocked",reason:"The known reviewed resume component changed; no callback was repeated."});
  const canonical=value=>value===null||typeof value!=="object"?JSON.stringify(value):Array.isArray(value)?`[${value.map(canonical).join(",")}]`:`{${Object.keys(value).sort().map(key=>`${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  const hash=async value=>[...new Uint8Array(await crypto.subtle.digest("SHA-256",new TextEncoder().encode(canonical(value))))].map(value=>value.toString(16).padStart(2,"0")).join("");
  const target=/^https:\/\/job-boards\.greenhouse\.io\/razorpaysoftwareprivatelimited\/jobs\/[0-9]+$/;
  if(window!==window.top || !expected || !target.test(location.href) || location.href!==expected.target_url || !["inspect","check","apply"].includes(operation))return fail();
  const input=document.querySelector('form#application-form .file-upload[aria-labelledby="upload-label-resume"] input#resume[type="file"]');
  const group=input?.closest('.file-upload[aria-labelledby="upload-label-resume"]');
  if(!(input instanceof HTMLInputElement) || !input.isConnected || input.disabled || input.multiple || !group
    || document.querySelectorAll('input[type="file"]').length!==1 || group.querySelector('.file-upload__filename,[role="progressbar"],progress,[role="alert"]')
    || !input.classList.contains("visually-hidden") || ![...(input.labels||[])].some(label=>label.textContent.trim()==="Attach"))return fail();
  const fibers=Object.keys(input).filter(key=>key.startsWith("__reactFiber$"));if(fibers.length!==1)return fail();
  let fiber=input[fibers[0]],props=null;const candidates=[];
  for(let depth=0;fiber&&depth<16;depth++,fiber=fiber.return){
    const value=fiber.memoizedProps;
    if(value?.id==="resume" && typeof value.onChange==="function" && value.uploadManager?.uploaders?.resume)candidates.push(value);
  }
  if(candidates.length!==1)return fail();props=candidates[0];
  const uploader=props.uploadManager.uploaders.resume;
  if(uploader.field!=="resume" || typeof uploader.url!=="string" || typeof uploader.key!=="string" || !uploader.key || uploader.key.length>1024
    || !uploader.formFields || typeof uploader.formFields!=="object" || Array.isArray(uploader.formFields)
    || typeof uploader.uploadFile!=="function" || typeof uploader.fileUrl!=="function" || typeof uploader.createForm!=="function"
    || Object.keys(uploader.formFields).length>24 || Object.entries(uploader.formFields).some(([key,value])=>typeof value!=="string" || value.length>16384 || key.length>80))return fail();
  let origin;try{const url=new URL(uploader.url);if(url.protocol!=="https:" || url.pathname!=="/" || url.search || url.hash || url.username || url.password || url.port)return fail();origin=url.origin;}catch{return fail();}
  const snapshot=()=>({contract:"greenhouse-resume-component-v1",field:"resume",url:uploader.url,key:uploader.key,fields:uploader.formFields});
  const serialized=canonical(snapshot()),digest=await hash(snapshot()),recordKey="__hirewiz_known_resume_component_v1";
  if(!input.isConnected || props.uploadManager.uploaders.resume!==uploader)return fail();
  if(operation==="inspect"){
    if(input.files.length!==0)return fail();
    const token=crypto.randomUUID();
    Object.defineProperty(globalThis,recordKey,{value:{input,group,props,uploader,callback:props.onChange,digest,token,used:false},configurable:true});
    return {outcome:"inspected",resume_component_sha256:digest,token,storage_origin:origin};
  }
  const record=globalThis[recordKey];
  if(!record || record.used || record.token!==expected.token || record.digest!==digest || expected.resume_component_sha256!==digest
    || record.input!==input || record.group!==group || record.props!==props || record.uploader!==uploader || record.callback!==props.onChange)return fail();
  if(operation==="check")return input.files.length===0 && canonical(snapshot())===serialized
    ?{outcome:"inspected",resume_component_sha256:digest,token:record.token,storage_origin:origin}:fail();
  if(!Number.isSafeInteger(expected.deadline) || Date.now()>=expected.deadline || expected.deadline-Date.now()>10000
    || origin!==expected.storage_origin || expected.uploaded_url!==`${expected.storage_origin}/${expected.storage_key}`
    || input.files.length!==1 || input.files[0].name!==expected.filename || input.files[0].size!==expected.size_bytes || input.files[0].type!==expected.media_type)return fail();
  const selected=input.files[0],fileHash=[...new Uint8Array(await crypto.subtle.digest("SHA-256",await selected.arrayBuffer()))].map(value=>value.toString(16).padStart(2,"0")).join("");
  if(fileHash!==expected.artifact_sha256 || !input.isConnected || input.files.length!==1 || input.files[0]!==selected
    || props.onChange!==record.callback || props.uploadManager.uploaders.resume!==uploader || canonical(snapshot())!==serialized || Date.now()>=expected.deadline)return fail();
  // Burn before the exact existing callback. No input/change event can trigger
  // the portal uploader and no second storage POST is constructed here.
  record.used=true;
  record.callback({file:{name:expected.filename,url:expected.uploaded_url}});
  return {outcome:"callback_dispatched",filename:expected.filename,uploaded_url:expected.uploaded_url};
}
