// Actual installed MV3, ordinary profile-only TLS trust, React resume props,
// device crypto, SDK-intercepted native pairing/submit and private real SQL.
// Every employer request terminates at an owned loopback HTTPS server.
import assert from "node:assert/strict";
import test from "node:test";
import { cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { createServer } from "node:https";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { createHash } from "node:crypto";
import { prepareOwnedNativeHttpsProfile } from "./native-https-fixture.js";
const {chromium,expect}=await import(new URL("../../frontend/node_modules/@playwright/test/index.mjs",import.meta.url));
const {default:webpack}=await import(new URL("../../frontend/node_modules/webpack/lib/index.js",import.meta.url));
const ROOT=new URL("../../",import.meta.url),WEBSITE="https://www.hirewizhq.com",EXTENSION="pnjmegppacleipajnifjmbcopjjffklb";
const STORAGE="https://owned-resume.s3.amazonaws.com",PRESIGN="/uncacheable_attributes/presigned_fields?fields%5B%5D=resume";
const message=(popup,value)=>popup.evaluate(value=>chrome.runtime.sendMessage(value),value);
const sha256=bytes=>createHash("sha256").update(bytes).digest("hex");

async function fixture({submitStatus=200,loseUploadOutcome=false,loseSubmitOutcome=false,loseUploadReportBeforeServer=false,loseSubmitReportBeforeServer=false,wrongPayload=false,redirect=false}={}){
  const directory=await mkdtemp(`${tmpdir()}/hirewiz-hosted-network-browser-`);
  const tls=await prepareOwnedNativeHttpsProfile(directory,{dnsNames:["job-boards.greenhouse.io","boards.greenhouse.io","owned-resume.s3.amazonaws.com"]});
  const pending=[],requests=[],uploads=[],submissions=[];let context,server,errors="",droppedUpload=false,droppedSubmit=false;
  const python=spawn(process.env.HIREWIZ_BROWSER_PAIRED_PYTHON||new URL("backend/.venv/bin/python",ROOT).pathname,
    ["-I",new URL("backend/tests/browser_paired_assistance_chrome_bridge.py",ROOT).pathname,"--application"],
    {cwd:new URL("backend/",ROOT).pathname,stdio:["pipe","pipe","pipe"],env:{...process.env,PYTHONDONTWRITEBYTECODE:"1",PYTHONWARNINGS:"ignore::DeprecationWarning"}});
  python.stderr.on("data",data=>{errors+=data.toString();});
  createInterface({input:python.stdout}).on("line",line=>{const waiter=pending.shift();try{waiter?.resolve(JSON.parse(line));}catch(error){waiter?.reject(error);}});
  python.on("exit",()=>{for(const waiter of pending.splice(0))waiter.reject(new Error(`Owned bridge exited: ${errors}`));});
  const bridge=value=>new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error(`Owned bridge timeout: ${errors}`)),15000);
    pending.push({resolve:value=>{clearTimeout(timer);resolve(value);},reject:error=>{clearTimeout(timer);reject(error);}});python.stdin.write(`${JSON.stringify(value)}\n`);});
  try{
    const configured=await bridge({operation:"configuration"});assert.equal(configured.status,200);const setup=configured.body;
    const target=setup.target_url,submit=target.replace("https://job-boards.greenhouse.io/","https://boards.greenhouse.io/");
    const policy=Buffer.from(JSON.stringify({expiration:new Date(Date.now()+600000).toISOString(),conditions:[{bucket:"owned-resume"},["starts-with","$key","resume/"]]})).toString("base64");
    const presign={url:STORAGE,resume:{fields:{acl:"private",policy,"x-amz-signature":"e".repeat(64)},key:"resume/{timestamp}-{unique_id}.pdf"}};
    const entry=`import React,{useState,useCallback} from 'react';import{createRoot}from'react-dom/client';
      window.events={file:0,submit:0};const target=${JSON.stringify(target)},endpoint=${JSON.stringify(submit)},snapshot=${JSON.stringify(presign)},values=${JSON.stringify(setup.answers)};
      const uploader={field:'resume',url:snapshot.url,key:snapshot.resume.key,formFields:snapshot.resume.fields,uploadFile(){window.events.file++;throw new Error('A second portal upload must never run');},fileUrl(){return this.url+'/'+this.key},createForm(){throw new Error('No portal upload is authorized')}};
      const manager={uploaders:{resume:uploader}};
      function Resume(props){return React.createElement('div',{className:'file-upload','aria-labelledby':'upload-label-resume'},props.fileName?React.createElement('div',{className:'file-upload__filename'},React.createElement('p',{className:'body__secondary'},props.fileName),React.createElement('button',{type:'button','aria-label':'Remove file'},'Remove file')):[React.createElement('label',{htmlFor:'resume',key:'label'},'Attach'),React.createElement('input',{className:'visually-hidden',id:'resume',type:'file',key:'file',onChange:()=>uploader.uploadFile()})]);}
      function App(){const[resume,setResume]=useState(null);const selected=useCallback(value=>setResume(value.file),[]);
        const send=async event=>{event.preventDefault();window.events.submit++;const application={first_name:values.first_name,last_name:values.last_name,email:values.email,answers_attributes:{},demographic_answers:[],data_compliance:{},attachments:{},from_job_board_renderer:true,employments:[],mapped_url_token:null,appcast_click_id:null,time_zone:Intl.DateTimeFormat().resolvedOptions().timeZone,resume_url:resume.url,resume_url_filename:resume.name};${wrongPayload?"application.first_name='Unapproved';":""}
          const response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_application:application,fingerprint:'owned-local-fingerprint'})});if(response.ok)location.href=target+'/confirmation';};
        return React.createElement('form',{id:'application-form',action:target,method:'get',onSubmit:send},${JSON.stringify(setup.field_mapping)}.map(field=>React.createElement(React.Fragment,{key:field.id},React.createElement('label',{htmlFor:field.id},field.label+'*'),React.createElement('input',{id:field.id,type:'text',maxLength:255,'aria-required':'true',defaultValue:values[field.id]}))),React.createElement('div',{id:'upload-label-resume'},'Resume/CV*'),React.createElement(Resume,{id:'resume',uploadManager:manager,onChange:selected,fileName:resume?.name}),React.createElement('button',{type:'submit'},'Submit application'));}
      createRoot(document.getElementById('root')).render(React.createElement(App));`;
    await writeFile(`${directory}/portal-entry.js`,entry);
    await new Promise((resolve,reject)=>webpack({mode:"production",target:"web",entry:`${directory}/portal-entry.js`,output:{path:directory,filename:"portal-bundle.js"},
      resolve:{modules:[new URL("frontend/node_modules/",ROOT).pathname]},devtool:false},(error,stats)=>error||stats.hasErrors()?reject(error||new Error(stats.toString({all:false,errors:true}))):resolve()));
    const bundle=await readFile(`${directory}/portal-bundle.js`);
    server=createServer({key:await readFile(tls.trusted.key),cert:await readFile(tls.trusted.cert)},async(request,response)=>{
      const host=request.headers.host,parts=[];for await(const part of request)parts.push(part);const body=Buffer.concat(parts);
      requests.push({host,url:request.url,method:request.method});response.setHeader("Cache-Control","no-store");
      if(host==="boards.greenhouse.io"){
        response.setHeader("Access-Control-Allow-Origin","https://job-boards.greenhouse.io");
        response.setHeader("Access-Control-Allow-Methods","POST, GET, OPTIONS");
        response.setHeader("Access-Control-Allow-Headers","Content-Type");
        if(request.method==="OPTIONS"){response.writeHead(204);response.end();return;}
      }
      if(host==="job-boards.greenhouse.io"&&request.method==="GET"&&request.url===new URL(target).pathname){response.setHeader("Content-Type","text/html");response.end('<!doctype html><html><head><style>.visually-hidden{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}</style></head><body><div id="root"></div><script src="/owned-portal-bundle.js"></script></body></html>');}
      else if(host==="job-boards.greenhouse.io"&&request.method==="GET"&&request.url==="/owned-portal-bundle.js"){response.setHeader("Content-Type","application/javascript");response.end(bundle);}
      else if(host==="job-boards.greenhouse.io"&&request.method==="GET"&&request.url===new URL(target).pathname+"/confirmation"){response.setHeader("Content-Type","text/html");response.end('<div class="confirmation"><div class="confirmation__content">Owned synthetic acknowledgement</div></div>');}
      else if(host==="boards.greenhouse.io"&&request.method==="GET"&&request.url===PRESIGN){response.setHeader("Content-Type","application/json");response.end(JSON.stringify(presign));}
      else if(host==="owned-resume.s3.amazonaws.com"&&request.method==="POST"&&request.url==="/"){uploads.push(body);response.writeHead(204);response.end();}
      else if(host==="boards.greenhouse.io"&&request.method==="POST"&&request.url===new URL(submit).pathname){submissions.push(body);if(redirect){response.writeHead(302,{Location:target+"/confirmation"});response.end();}else{response.writeHead(submitStatus,{"Content-Type":"application/json"});response.end('{}');}}
      else{response.writeHead(404);response.end();}
    });
    await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));const port=server.address().port;
    const extension=`${directory}/extension`;await cp(new URL("../extension/",import.meta.url),extension,{recursive:true});
    const manifest=JSON.parse(await readFile(`${extension}/manifest.json`,"utf8"));manifest.permissions.push("webRequest");delete manifest.optional_permissions;
    manifest.host_permissions=[`${WEBSITE}/*`,"https://job-boards.greenhouse.io/*","https://boards.greenhouse.io/*",`${STORAGE}/*`];await writeFile(`${extension}/manifest.json`,JSON.stringify(manifest));
    await writeFile(`${extension}/config.js`,`export const CONFIG=Object.freeze(${JSON.stringify({mode:"reviewed_assistance",websiteOrigin:WEBSITE,portalOrigin:"https://job-boards.greenhouse.io",extensionId:EXTENSION,nativePairing:{executorRevision:setup.executor_revision,authorityKey:setup.authority_key}})});`);
    const args=[`--disable-extensions-except=${extension}`,`--load-extension=${extension}`,`--host-resolver-rules=MAP job-boards.greenhouse.io 127.0.0.1:${port},MAP boards.greenhouse.io 127.0.0.1:${port},MAP owned-resume.s3.amazonaws.com 127.0.0.1:${port}`,"--no-proxy-server"];
    assert.ok(args.every(value=>!/ignore.*cert|allow-insecure|enable-features|disable-web-security/i.test(value)));
    context=await chromium.launchPersistentContext(tls.profile,{channel:"chromium",headless:true,serviceWorkers:"allow",args});
    const worker=context.serviceWorkers()[0]||await context.waitForEvent("serviceworker");assert.equal(new URL(worker.url()).host,EXTENSION);
    await worker.evaluate(()=>{globalThis.ownedNativeTrace=[];for(const [name,stage] of [["onBeforeRequest","before"],["onHeadersReceived","headers"],["onCompleted","complete"],["onErrorOccurred","error"]])chrome.webRequest[name].addListener(detail=>{
      ownedNativeTrace.push({stage,url:detail.url,requestId:detail.requestId,method:detail.method,initiator:detail.initiator,tabId:detail.tabId,frameId:detail.frameId,documentId:detail.documentId,statusCode:detail.statusCode,ip:detail.ip,fromCache:detail.fromCache,timeStamp:detail.timeStamp,error:detail.error,
        body:detail.requestBody?{error:detail.requestBody.error,formData:Object.keys(detail.requestBody.formData||{}),raw:detail.requestBody.raw?.map(part=>({bytes:part.bytes?.byteLength,file:!!part.file}))}:null});
    },{urls:["https://boards.greenhouse.io/*","https://owned-resume.s3.amazonaws.com/*"]},stage==="before"?["requestBody"]:stage==="headers"?["responseHeaders"]:undefined);});
    await context.route("**/*",async route=>{
      const request=route.request(),url=request.url();
      if(url.startsWith("chrome-extension:")||["job-boards.greenhouse.io","boards.greenhouse.io","owned-resume.s3.amazonaws.com"].includes(new URL(url).hostname))return route.continue();
      if(!url.startsWith(`${WEBSITE}/api/`)||request.method()!=="POST")return route.abort("blockedbyclient");
      if(url.endsWith("/outcome")){
        const body=request.postDataJSON().body;
        if(loseUploadReportBeforeServer && !droppedUpload && body.upload_evidence){droppedUpload=true;return route.abort("failed");}
        if(loseSubmitReportBeforeServer && !droppedSubmit && body.receipt){droppedSubmit=true;return route.abort("failed");}
      }
      const pairing=url.startsWith(`${WEBSITE}/api/browser-pairing/device/`),result=await bridge({operation:pairing?"pairing_device":"assistance_device",action:url.split("/").at(-1),origin:request.headers().origin||"",body:request.postDataJSON()});
      if(result.status!==200)requests.push({api:url,status:result.status,body:result.body});
      if(url.endsWith("/outcome")&&result.status===200){if(loseUploadOutcome&&!droppedUpload&&result.body.review_protocol_version===3){droppedUpload=true;return route.abort("failed");}if(loseSubmitOutcome&&!droppedSubmit&&result.body.review_protocol_version===4){droppedSubmit=true;return route.abort("failed");}}
      if(result.body.binary_base64){const bytes=Buffer.from(result.body.binary_base64,"base64"),artifact=result.body.descriptor;return route.fulfill({status:200,headers:{"Content-Type":artifact.media_type,"Content-Length":String(bytes.length),"X-Artifact-SHA256":artifact.sha256,"X-Artifact-Filename":artifact.filename},body:bytes});}
      return route.fulfill({status:result.status,contentType:"application/json",body:JSON.stringify(result.body)});
    });
    const popup=await context.newPage();await popup.goto(`chrome-extension://${EXTENSION}/popup.html`);
    const portal=await context.newPage();await portal.goto(target);await expect(portal.locator("#resume")).toBeAttached();
    const started=await message(popup,{type:"pairing_start"});assert.equal(started.ok,true,started.error);
    assert.equal((await bridge({operation:"candidate_fixture_approve",pairing_id:started.value.pairing_id})).status,200);
    const paired=await message(popup,{type:"pairing_complete"});assert.equal(paired.ok,true,paired.error);assert.equal(paired.value.connected,true);
    const candidate=async(action,body)=>{const value=await bridge({operation:"assistance_candidate",action,body});assert.equal(value.status,200,JSON.stringify(value));return value.body;};
    const review=async(type)=>{const prepared=await message(popup,{type,application_id:setup.application_id});assert.equal(prepared.ok,true,prepared.error+" "+JSON.stringify(requests.filter(value=>value.api)));return candidate("review",{identity:prepared.value.review_id});};
    const approve=async reviewed=>{await candidate("approve",{identity:reviewed.id,review_digest:reviewed.review_digest,confirmed:true});const checked=await message(popup,{type:"assistance_check"});assert.equal(checked.ok,true,checked.error);return checked.value;};
    return {popup,portal,worker,setup,bridge,candidate,review,approve,uploads,submissions,requests,context,
      async close(){await context?.close();server?.closeAllConnections();if(server?.listening)await new Promise(resolve=>server.close(resolve));
        if(python.exitCode===null){python.stdin.end();await new Promise(resolve=>python.once("exit",resolve));}await rm(directory,{recursive:true,force:true});assert.equal(errors,"");}
    };
  }catch(error){await context?.close();server?.closeAllConnections();if(server?.listening)await new Promise(resolve=>server.close(resolve));python.kill();await rm(directory,{recursive:true,force:true});throw error;}
}

test("installed normal MV3 uploads exact bytes once and settles only the exact native browser-validated hosted200",async()=>{
  const f=await fixture();try{
    const upload=await f.review("assistance_prepare_native_upload");assert.deepEqual(upload.payload.allowed_actions,["upload_resume"]);assert.equal(f.uploads.length,0);
    await f.approve(upload);const attached=await message(f.popup,{type:"assistance_fill"});assert.equal(attached.ok,true,attached.error+" "+JSON.stringify(await f.worker.evaluate(()=>ownedNativeTrace)));assert.equal(attached.value.phase,"filled");
    assert.equal(f.uploads.length,1);assert.equal((await f.portal.evaluate(()=>events)).file,0,"No portal uploader callback/second storage POST");
    let audit=(await f.bridge({operation:"audit"})).body;assert.equal(audit.charged_credits,0);assert.equal(audit.receipt,null);assert.equal(audit.binary_deliveries,1);
    await message(f.popup,{type:"assistance_cancel"});const final=await f.review("assistance_prepare_submit");
    assert.equal(final.payload.receipt_contract,"greenhouse_hosted_browser_https_200_v2");assert.equal(f.submissions.length,0);
    await f.approve(final);audit=(await f.bridge({operation:"audit"})).body;assert.equal(audit.charged_credits,0);assert.equal(audit.credits,80);
    const submitted=await message(f.popup,{type:"assistance_fill"});assert.equal(submitted.ok,true,submitted.error+" "+JSON.stringify(await f.worker.evaluate(()=>ownedNativeTrace)));assert.equal(submitted.value.employer_confirmed,true);assert.equal(submitted.value.phase,"filled");
    audit=(await f.bridge({operation:"audit"})).body;assert.equal(audit.application_status,"confirmed");assert.equal(audit.charged_credits,20);assert.equal(audit.submit_attempts,1);
    assert.equal(audit.receipt.contract,"greenhouse_hosted_browser_https_200_v2");assert.equal(audit.receipt.network.ip,"127.0.0.1");assert.equal(audit.receipt.network.from_cache,false);assert.equal(audit.receipt.application_id,undefined);
    assert.equal(f.submissions.length,1);assert.equal(f.uploads.length,1);assert.equal((await message(f.popup,{type:"assistance_fill"})).ok,false);
    const body=JSON.parse(f.submissions[0]);assert.deepEqual(body.job_application,final.payload.expected_application);assert.equal(sha256(Buffer.from(JSON.stringify(body))),audit.receipt.network.request_body_sha256);
  }finally{await f.close();}
});

async function approvedFinal(f){
  const upload=await f.review("assistance_prepare_native_upload");await f.approve(upload);
  const result=await message(f.popup,{type:"assistance_fill"});assert.equal(result.ok,true,result.error);
  await message(f.popup,{type:"assistance_cancel"});const final=await f.review("assistance_prepare_submit");await f.approve(final);return final;
}

for(const [name,options] of [["202",{submitStatus:202}],["redirect",{redirect:true}],["changed serialized answer",{wrongPayload:true}]]){
  test(`installed native ${name} keeps a single unknown attempt and never settles or resends`,async()=>{
    const f=await fixture(options);try{
      await approvedFinal(f);const result=await message(f.popup,{type:"assistance_fill"});assert.equal(result.ok,false);
      let audit=(await f.bridge({operation:"audit"})).body;assert.equal(audit.charged_credits,0);assert.equal(audit.receipt,null);assert.equal(audit.submit_attempts,1);assert.equal(audit.credits,80);
      assert.equal(f.submissions.length,1);assert.equal((await message(f.popup,{type:"assistance_fill"})).ok,false);
      const reconciled=await message(f.popup,{type:"assistance_reconcile"});assert.equal(reconciled.ok,true,reconciled.error);assert.equal(reconciled.value.employer_confirmed,false);
      audit=(await f.bridge({operation:"audit"})).body;assert.equal(audit.application_status,"unknown");assert.equal(audit.charged_credits,0);assert.equal(f.submissions.length,1);
    }finally{await f.close();}
  });
}

test("installed owned upload reconciles the original lost proof report without a second upload",async()=>{
  const f=await fixture({loseUploadReportBeforeServer:true});try{
    const upload=await f.review("assistance_prepare_native_upload");await f.approve(upload);
    assert.equal((await message(f.popup,{type:"assistance_fill"})).ok,false);assert.equal(f.uploads.length,1);
    assert.equal((await message(f.popup,{type:"assistance_fill"})).ok,false);
    const stopped=await message(f.popup,{type:"assistance_cancel"});assert.equal(stopped.ok,true,stopped.error);assert.equal(stopped.value.review_id,upload.id);assert.equal(stopped.value.can_reconcile,true);
    const reconciled=await message(f.popup,{type:"assistance_reconcile"});assert.equal(reconciled.ok,true,reconciled.error);assert.equal(reconciled.value.phase,"filled");
    const audit=(await f.bridge({operation:"audit"})).body;assert.equal(audit.charged_credits,0);assert.equal(audit.binary_deliveries,1);assert.equal(audit.submit_attempts,0);assert.equal(f.uploads.length,1);
  }finally{await f.close();}
});

test("installed final submit reconciles original captured200 after a lost proof report without another POST",async()=>{
  const f=await fixture({loseSubmitReportBeforeServer:true});try{
    await approvedFinal(f);assert.equal((await message(f.popup,{type:"assistance_fill"})).ok,false);assert.equal(f.submissions.length,1);
    let audit=(await f.bridge({operation:"audit"})).body;assert.equal(audit.charged_credits,0);assert.equal(audit.credits,80);
    assert.equal((await message(f.popup,{type:"assistance_fill"})).ok,false);
    const stopped=await message(f.popup,{type:"assistance_cancel"});assert.equal(stopped.ok,true,stopped.error);assert.equal(stopped.value.can_reconcile,true);
    const reconciled=await message(f.popup,{type:"assistance_reconcile"});assert.equal(reconciled.ok,true,reconciled.error);assert.equal(reconciled.value.employer_confirmed,true);
    audit=(await f.bridge({operation:"audit"})).body;assert.equal(audit.charged_credits,20);assert.equal(audit.submit_attempts,1);assert.equal(f.submissions.length,1);assert.equal(f.uploads.length,1);
  }finally{await f.close();}
});

test("installed cancellation or changed form before disclosure sends no resume bytes",async()=>{
  for(const mode of ["cancel","changed"]){const f=await fixture();try{
    const upload=await f.review("assistance_prepare_native_upload");await f.approve(upload);
    if(mode==="cancel")await message(f.popup,{type:"assistance_cancel"});
    else await f.portal.locator("#first_name").fill("Changed after approval");
    assert.equal((await message(f.popup,{type:"assistance_fill"})).ok,false);
    const audit=(await f.bridge({operation:"audit"})).body;assert.equal(audit.binary_deliveries,0);assert.equal(audit.charged_credits,0);assert.equal(f.uploads.length,0);assert.equal(f.submissions.length,0);
  }finally{await f.close();}}
});
