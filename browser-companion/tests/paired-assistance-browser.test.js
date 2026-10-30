// Installed shipping-identity MV3 → original Chrome P256 proofs → real native
// SDK-intercepted production composition → protected + SQL activation. All HTTP
// is locally fulfilled. Test-only fixed clocks match the synthetic native clock.
import assert from "node:assert/strict";
import { readinessFixture } from "./pairing-readiness-fixture.js";
import test from "node:test";
import { cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
const { chromium, expect } = await import(new URL("../../frontend/node_modules/@playwright/test/index.mjs", import.meta.url));

const ROOT = new URL("../../", import.meta.url);
const WEBSITE = "https://www.hirewizhq.com";
const EXTENSION = "pnjmegppacleipajnifjmbcopjjffklb";
const message = (popup, value) => popup.evaluate((input) => chrome.runtime.sendMessage(input), value);

async function fixture({ expectedNativeFailures = [] } = {}) {
  const directory = await mkdtemp(`${tmpdir()}/hirewiz-paired-assistance-browser-`);
  const pending = [], requests = []; let errors = "", context;
  const python = spawn(process.env.HIREWIZ_BROWSER_PAIRED_PYTHON || new URL("backend/.venv/bin/python", ROOT).pathname,
    ["-I",new URL("backend/tests/browser_paired_assistance_chrome_bridge.py", ROOT).pathname],
    {cwd:new URL("backend/", ROOT).pathname,stdio:["pipe","pipe","pipe"],env:{...process.env,PYTHONDONTWRITEBYTECODE:"1",PYTHONWARNINGS:"ignore::DeprecationWarning"}});
  python.stderr.on("data", chunk => {errors += chunk.toString();});
  createInterface({input:python.stdout}).on("line",line => {
    const waiter = pending.shift();
    try { waiter?.resolve(JSON.parse(line)); } catch (failure) { waiter?.reject(failure); }
  });
  python.on("exit",() => {for(const waiter of pending.splice(0)) waiter.reject(new Error(`Owned native bridge exited: ${errors}`));});
  const bridge = value => new Promise((resolve,reject) => {
    const timeout = setTimeout(() => reject(new Error(`Owned native bridge timed out: ${errors}`)),15000);
    pending.push({resolve:reply => {clearTimeout(timeout);resolve(reply);},reject:error => {clearTimeout(timeout);reject(error);}});
    python.stdin.write(`${JSON.stringify(value)}\n`);
  });
  try {
    const reply = await bridge({operation:"configuration"}); assert.equal(reply.status,200);
    const setup = reply.body, extension = `${directory}/extension`;
    await cp(new URL("../extension/",import.meta.url),extension,{recursive:true});
    const manifest = JSON.parse(await readFile(`${extension}/manifest.json`,"utf8"));
    // Exact synthetic host grant avoids Chrome's human permission dialog. It
    // does not claim evidence for human approval of production host grants.
    manifest.host_permissions = manifest.optional_host_permissions;
    await writeFile(`${extension}/manifest.json`,JSON.stringify(manifest));
    await writeFile(`${extension}/config.js`,`Date.now=()=>${setup.now_ms};\nexport const CONFIG=Object.freeze(${JSON.stringify({mode:"reviewed_assistance",websiteOrigin:WEBSITE,portalOrigin:"https://job-boards.greenhouse.io",extensionId:EXTENSION,
      nativePairing:{executorRevision:setup.executor_revision,authorityKey:setup.authority_key}})});\n`);
    async function launch() {
      context = await chromium.launchPersistentContext(`${directory}/profile`,{channel:"chromium",headless:true,serviceWorkers:"allow",
        args:[`--disable-extensions-except=${extension}`,`--load-extension=${extension}`]});
      const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
      assert.equal(new URL(worker.url()).host,EXTENSION);
      await context.route("**/*",async route => {
        const request = route.request(), url = request.url();
        if(url.startsWith("chrome-extension:")) return route.continue();
        const origin = request.headers().origin || "";
        requests.push({url,origin,method:request.method()});
        const ready = readinessFixture({url, method:request.method(), headers:request.headers(), body:request.postData()}, WEBSITE, EXTENSION);
        if (ready) return route.fulfill(ready);
        let result;
        if(url.startsWith(`${WEBSITE}/api/browser-pairing/device/`) && request.method()==="POST") {
          result = await bridge({operation:"pairing_device",action:url.split("/").at(-1),origin,body:request.postDataJSON()});
        } else if(url.startsWith(`${WEBSITE}/api/browser-assistance/device/`) && request.method()==="POST") {
          result = await bridge({operation:"assistance_device",origin,body:request.postDataJSON()});
        } else return route.abort("blockedbyclient");
        return route.fulfill({status:result.status,contentType:"application/json",body:JSON.stringify(result.body)});
      });
      const popup = await context.newPage(); await popup.goto(`chrome-extension://${EXTENSION}/popup.html`);
      await expect(popup.locator("#native-connection")).toBeVisible();
      await expect(popup.locator("#assistance")).toBeHidden();
      return {popup,worker};
    }
    const current = await launch();
    return {...current,bridge,requests,setup,get context(){return context;},
      async restart(){await context.close();return launch();},
      async close(){await context?.close();if(python.exitCode===null){python.stdin.end();await new Promise(resolve=>python.once("exit",resolve));}
        await rm(directory,{recursive:true,force:true});
        assert.deepEqual(errors.split("\n").filter(Boolean).map(line=>JSON.parse(line)),expectedNativeFailures);
        assert.ok(requests.every(({url,origin,method})=>method==="GET" ? readinessFixture({url,method,headers:origin?{origin}:{},body:null},WEBSITE,EXTENSION)?.status===204
          : method==="POST" && origin===`chrome-extension://${EXTENSION}`
          && (url.startsWith(`${WEBSITE}/api/browser-pairing/device/`) || url.startsWith(`${WEBSITE}/api/browser-assistance/device/`))),"Only finite public readiness or owned signed fulfillments with actual extension Origin");}
    };
  } catch(error){await context?.close();python.kill();await rm(directory,{recursive:true,force:true});throw error;}
}

async function start(f) {
  const result = await message(f.popup,{type:"pairing_start"}); assert.equal(result.ok,true,result.error);
  assert.equal(result.value.phase,"awaiting_candidate"); assert.equal(result.value.connected,false);
  assert.deepEqual(result.value.allowed_actions,[]);
  return result.value;
}
async function complete(f) {
  const result = await message(f.popup,{type:"pairing_complete"}); assert.equal(result.ok,true,result.error);
  return result.value;
}

test("installed MV3 genuine native pairing activates same Chrome key for assistance and restart requires fresh claim",async()=>{
  const f = await fixture();try {
    assert.equal((await message(f.popup,{type:"assistance_status"})).ok,false);
    const selected = await start(f);
    assert.equal((await f.bridge({operation:"candidate_fixture_approve",pairing_id:selected.pairing_id})).status,200);
    assert.equal((await complete(f)).connected,true);
    const assistance = await message(f.popup,{type:"assistance_status"});assert.equal(assistance.ok,true,assistance.error);
    assert.equal(assistance.value.phase,"active");assert.equal(assistance.value.device_id,selected.device_id);
    const refreshed = await f.popup.evaluate(async()=>{const {deviceIdentity}=await import(chrome.runtime.getURL("device.js"));const key=await deviceIdentity();return {device_id:key.device_id,key_sha256:key.key_sha256,extractable:key.private_key.extractable};});
    assert.equal(refreshed.extractable,false);assert.notEqual(refreshed.device_id,selected.device_id);
    assert.equal(assistance.value.key_sha256,refreshed.key_sha256);
    const audit=(await f.bridge({operation:"audit"})).body;
    assert.deepEqual(audit.devices,[{id:selected.device_id,user_id:f.setup.candidate_id,state:"active",key_sha256:refreshed.key_sha256}]);
    assert.equal(audit.protected_devices,1);assert.equal(audit.application_actions,0);assert.equal(audit.signatures,2);
    const next=await f.restart();
    assert.equal((await message(next.popup,{type:"assistance_status"})).ok,false);
    assert.equal((await message(next.popup,{type:"pairing_status"})).value.connected,false);
    const checked=await message(next.popup,{type:"pairing_refresh"});assert.equal(checked.ok,true,checked.error);assert.equal(checked.value.connected,true);
    assert.equal((await message(next.popup,{type:"assistance_status"})).value.device_id,selected.device_id);
    assert.equal(f.requests.filter(({url})=>url.endsWith("/complete")).length,1);
  }finally{await f.close();}
});

test("installed MV3 wrong candidate password cannot enable application UI or native completion",async()=>{
  const f=await fixture({expectedNativeFailures:[{event:"native_boundary_failure_v1",component:"browser_pairing",stage:"native_operation",exception_type:"GuardDenied"}]});try {
    const selected=await start(f);
    assert.equal((await f.bridge({operation:"candidate_fixture_approve",pairing_id:selected.pairing_id,wrong_password:true})).status,403);
    const checked=await complete(f);assert.equal(checked.connected,false);assert.equal(checked.phase,"awaiting_candidate");
    assert.equal((await message(f.popup,{type:"assistance_status"})).ok,false);
    await expect(f.popup.locator("#assistance")).toBeHidden();
    assert.equal(f.requests.filter(({url})=>url.endsWith("/complete")).length,0);
    const audit=(await f.bridge({operation:"audit"})).body;
    assert.deepEqual(audit.devices,[]);assert.equal(audit.protected_devices,0);assert.equal(audit.signatures,0);assert.equal(audit.application_actions,0);
  }finally{await f.close();}
});

test("installed MV3 unknown original completion never enables assistance or replays after restart",async()=>{
  const f=await fixture();try {
    const selected=await start(f);
    assert.equal((await f.bridge({operation:"candidate_fixture_approve",pairing_id:selected.pairing_id})).status,200);
    await f.bridge({operation:"fault",fault:"native_completion_commit_loss"});
    const failed=await message(f.popup,{type:"pairing_complete"});assert.equal(failed.ok,false);
    assert.equal((await message(f.popup,{type:"pairing_status"})).value.phase,"unknown");
    assert.equal((await message(f.popup,{type:"assistance_status"})).ok,false);
    assert.equal((await message(f.popup,{type:"pairing_complete"})).ok,false);
    await f.bridge({operation:"fault",fault:"clear"});
    const next=await f.restart();
    assert.equal((await message(next.popup,{type:"pairing_status"})).value.phase,"unknown");
    assert.equal((await message(next.popup,{type:"pairing_refresh"})).ok,false);
    const inspected=await message(next.popup,{type:"pairing_inspect"});assert.equal(inspected.ok,true,inspected.error);assert.equal(inspected.value.observed_status,"COMPLETED");assert.equal(inspected.value.connected,false);
    assert.equal((await message(next.popup,{type:"assistance_status"})).ok,false);
    assert.equal(f.requests.filter(({url})=>url.endsWith("/complete")).length,1);
    const audit=(await f.bridge({operation:"audit"})).body;
    assert.deepEqual(audit.devices,[]);assert.equal(audit.protected_devices,0);assert.equal(audit.signatures,1);
  }finally{await f.close();}
});
