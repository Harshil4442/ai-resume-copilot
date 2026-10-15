// Genuine native network events against an owned loopback server. These tests
// establish request-body visibility only; HTTP cannot establish a TLS receipt.
import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { createServer } from "node:http";
import { createHash } from "node:crypto";
const { chromium } = await import(new URL("../../frontend/node_modules/@playwright/test/index.mjs", import.meta.url));

const sha256 = value => createHash("sha256").update(value).digest("hex");
const FILE_SIZE = 1024 * 1024;

async function fixture() {
  const directory = await mkdtemp(`${tmpdir()}/hirewiz-native-upload-exposure-`);
  const received = new Map(); let context;
  const server = createServer((request, response) => {
    if (request.method === "POST" && ["/page-upload", "/owned-upload"].includes(request.url)) {
      const parts = [];
      request.on("data", part => parts.push(part));
      request.on("end", () => { received.set(request.url, Buffer.concat(parts)); response.end("{}"); });
    } else if (request.method === "GET" && request.url === "/") {
      response.setHeader("Content-Type", "text/html");
      response.end('<input id="resume" type="file"><script>document.querySelector("input").addEventListener("change",async event=>{const form=new FormData();form.append("key","approved/exact.pdf");form.append("file",event.target.files[0]);window.uploadResult=await fetch("/page-upload",{method:"POST",body:form}).then(response=>response.status);});</script>');
    } else { response.writeHead(404); response.end(); }
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  try {
    const shipping = JSON.parse(await readFile(new URL("../extension/manifest.json", import.meta.url), "utf8"));
    await writeFile(`${directory}/manifest.json`, JSON.stringify({ manifest_version: 3, name: "Owned native upload exposure fixture", version: "1.0",
      key: shipping.key, permissions: ["webRequest", "storage"], host_permissions: [`${origin}/*`], background: { service_worker: "background.js" } }));
    await writeFile(`${directory}/background.js`, `
      const hash=async bytes=>[...new Uint8Array(await crypto.subtle.digest("SHA-256",bytes))].map(value=>value.toString(16).padStart(2,"0")).join("");
      globalThis.captures={};
      chrome.webRequest.onBeforeRequest.addListener(details=>{
        const parts=details.requestBody?.raw||[],length=parts.reduce((total,part)=>total+(part.bytes?.byteLength||0),0);
        const bytes=new Uint8Array(length);let offset=0;
        for(const part of parts){if(part.bytes){bytes.set(new Uint8Array(part.bytes),offset);offset+=part.bytes.byteLength;}}
        hash(bytes).then(sha256=>{globalThis.captures[new URL(details.url).pathname]={requestId:details.requestId,tabId:details.tabId,documentId:details.documentId||null,
          initiator:details.initiator,method:details.method,raw_bytes:length,raw_sha256:sha256,file_placeholders:parts.filter(part=>part.file).length,formData_keys:Object.keys(details.requestBody?.formData||{})};});
      },{urls:["${origin}/*"]},["requestBody"]);
      globalThis.ownedUpload=async size=>{
        const bytes=Uint8Array.from({length:size},(_,index)=>index%256),form=new FormData();
        form.append("key","approved/exact.pdf");form.append("file",new File([bytes],"approved.pdf",{type:"application/pdf",lastModified:0}));
        const serialized=new Request("${origin}/owned-upload",{method:"POST",body:form}),body=await serialized.arrayBuffer();
        const response=await fetch(serialized.url,{method:"POST",body,headers:{"Content-Type":serialized.headers.get("Content-Type")},credentials:"omit",redirect:"error",cache:"no-store"});
        return {status:response.status,body_size:body.byteLength,body_sha256:await hash(body),file_sha256:await hash(bytes)};
      };`);
    context = await chromium.launchPersistentContext(`${directory}/profile`, { channel: "chromium", headless: true, serviceWorkers: "allow",
      args: [`--disable-extensions-except=${directory}`, `--load-extension=${directory}`] });
    const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
    assert.equal(new URL(worker.url()).host, "pnjmegppacleipajnifjmbcopjjffklb");
    const page = await context.newPage(); await page.goto(origin);
    return { page, worker, received, origin, async capture(path) {
      await page.waitForFunction(async ({extension, path}) => {
        // The extension-owned worker is read directly below; this wait observes
        // only the local server's already completed request in the page case.
        return extension === "owned" || window.uploadResult === 200;
      }, {extension:path === "/owned-upload" ? "owned" : "page",path});
      for (let attempt = 0; attempt < 100; attempt++) {
        const value = await worker.evaluate(path => globalThis.captures[path] || null, path);
        if (value) return value;
        await new Promise(resolve => setTimeout(resolve, 10));
      }
      throw new Error("Owned native request capture missing");
    }, async close() { await context?.close(); await new Promise(resolve => server.close(resolve)); await rm(directory, {recursive:true, force:true}); } };
  } catch (failure) { await context?.close(); await new Promise(resolve => server.close(resolve)); await rm(directory,{recursive:true,force:true}); throw failure; }
}

test("native MV3 observes framing but omits the programmatic File bytes from ordinary page multipart", async () => {
  const f = await fixture(); try {
    await f.page.evaluate(size => {
      const transfer = new DataTransfer(); transfer.items.add(new File([Uint8Array.from({length:size},(_,index)=>index%256)],"approved.pdf",{type:"application/pdf",lastModified:0}));
      const input = document.querySelector("input"); input.files = transfer.files; input.dispatchEvent(new Event("change",{bubbles:true}));
    }, FILE_SIZE);
    const capture = await f.capture("/page-upload"), actual = f.received.get("/page-upload");
    assert.equal(capture.method,"POST"); assert.ok(capture.tabId >= 0); assert.equal(capture.initiator,f.origin);
    assert.ok(actual.length > FILE_SIZE); assert.ok(capture.raw_bytes < FILE_SIZE);
    assert.notEqual(capture.raw_sha256,sha256(actual));
  } finally { await f.close(); }
});

test("native MV3 captures every exact byte of extension-owned serialized multipart once", async () => {
  const f = await fixture(); try {
    const uploaded = await f.worker.evaluate(size => globalThis.ownedUpload(size),FILE_SIZE);
    const capture = await f.capture("/owned-upload"), actual = f.received.get("/owned-upload");
    assert.equal(uploaded.status,200); assert.equal(capture.method,"POST"); assert.equal(capture.tabId,-1);
    assert.equal(capture.initiator,"chrome-extension://pnjmegppacleipajnifjmbcopjjffklb");
    assert.equal(capture.file_placeholders,0); assert.deepEqual(capture.formData_keys,[]);
    assert.equal(capture.raw_bytes,actual.length); assert.equal(capture.raw_sha256,sha256(actual));
    assert.equal(uploaded.body_size,actual.length); assert.equal(uploaded.body_sha256,capture.raw_sha256);
    const header=actual.indexOf(Buffer.from('filename="approved.pdf"')),start=actual.indexOf(Buffer.from("\r\n\r\n"),header)+4;
    const end=actual.indexOf(Buffer.from("\r\n------"),start), file=actual.subarray(start,end);
    assert.equal(file.length,FILE_SIZE); assert.equal(sha256(file),uploaded.file_sha256);
    assert.equal(f.received.size,1,"one owned upload; no automatic replay");
  } finally { await f.close(); }
});
