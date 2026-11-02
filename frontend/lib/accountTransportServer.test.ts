// @vitest-environment node
import { createHash } from "node:crypto";
import { encode } from "next-auth/jwt";
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { handleAccount } from "./accountTransportServer";
import { ownedBootstrapFixture } from "./fixtures/ownedBootstrap";
import { ownedDashboardFixture } from "./fixtures/ownedDashboard";
const origin="https://account.example.com", secret="synthetic-dedicated-account-secret";
const csrf="a".repeat(64);
const profile={email:"synthetic@example.com",full_name:"Synthetic Candidate",profile_completeness:20,missing_fields:[],tier:"free",ai_credits:2,premium_until:null};
beforeEach(()=>{vi.restoreAllMocks();vi.stubEnv("NEXTAUTH_URL",origin);vi.stubEnv("NEXTAUTH_SECRET",secret);vi.stubEnv("BACKEND_URL","https://backend.example.com");});
async function req(operation:string,method="GET",options:{anonymous?:boolean;csrf?:boolean;foreign?:boolean;body?:string;extraHeaders?:Record<string,string>}={}){
 const token=await encode({secret,token:{accessToken:"synthetic-private-bearer",hirewizUserId:12}});
 const cookie=(options.anonymous?"":`__Secure-next-auth.session-token=${token}; `)+`__Host-next-auth.csrf-token=${encodeURIComponent(csrf+"|"+createHash("sha256").update(csrf+secret).digest("hex"))}`;
 return new NextRequest(`${origin}/api/account/${operation}`,{method,headers:{cookie,origin:options.foreign?"https://foreign.example.com":origin,"Content-Type":"application/json",...(options.csrf===false?{}:{"x-hirewiz-account-csrf":csrf}),...options.extraHeaders},body:method==="GET"?undefined:options.body||"{}"});
}
describe("dedicated safe account operations",()=>{
 it.each(["login","google-login","candidate","password","web-logout","constructor"])("cannot select issuer/native operation %s",async(operation)=>{
  const fetch=vi.fn();vi.stubGlobal("fetch",fetch);const res=await handleAccount(await req(operation,"POST"),operation);
  expect(res.status).toBe(404);expect(fetch).not.toHaveBeenCalled();
 });
 it.each([{csrf:false},{foreign:true}])("requires actual CSRF and exact origin before account mutation",async(options)=>{
  const fetch=vi.fn();vi.stubGlobal("fetch",fetch);const res=await handleAccount(await req("profile","PUT",options),"profile");
  expect(res.status).toBe(403);expect(fetch).not.toHaveBeenCalled();
 });
 it("preserves genuine user profile reads with server bearer and no private credential response",async()=>{
  const fetch=vi.fn(async()=>Response.json(profile));vi.stubGlobal("fetch",fetch);
  const res=await handleAccount(await req("profile"),"profile");expect(await res.json()).toEqual(profile);
  const [url,init]=fetch.mock.calls[0] as unknown as [string,RequestInit];expect(url).toBe("https://backend.example.com/api/auth/profile");
  expect(new Headers(init.headers).get("Authorization")).toBe("Bearer synthetic-private-bearer");expect(init.redirect).toBe("error");
 });
 it("preserves ordinary anonymous registration and projects status only",async()=>{
  vi.stubGlobal("fetch",vi.fn(async()=>Response.json({id:12,email:"synthetic@example.com",tier:"free",ai_credits:2,job_service_credits:0})));
  const res=await handleAccount(await req("register","POST",{anonymous:true,body:JSON.stringify({email:"synthetic@example.com",password:"synthetic-password",accepted_terms:true,confirmed_age_18:true})}),"register");
  expect(await res.json()).toEqual({status:"REGISTERED"});
 });
 it("blocks signup while any account cookie is retained",async()=>{
  const fetch=vi.fn();vi.stubGlobal("fetch",fetch);expect((await handleAccount(await req("register","POST"),"register")).status).toBe(409);expect(fetch).not.toHaveBeenCalled();
 });
 it.each(["register","profile"])("preserves rate refusal on %s with a fixed public message",async(operation)=>{
  const fetch=vi.fn(async()=>Response.json({detail:"synthetic-private-refusal",access_token:"synthetic-private-token"},{status:429}));vi.stubGlobal("fetch",fetch);
  const res=await handleAccount(await req(operation,operation==="register"?"POST":"PUT",{anonymous:operation==="register"}),operation);
  expect(res.status).toBe(429);expect(await res.json()).toEqual({detail:"Too many account requests. Please wait before trying again."});
  expect(res.headers.get("cache-control")).toBe("private, no-store");expect(fetch).toHaveBeenCalledTimes(1);
 });
 it.each([{...profile,access_token:"synthetic-private-token"},{...profile,browser_pairing_session:{}},{...profile,bio:{nested:{refresh_token:"synthetic-private-token"}}}])("fails closed on credential/capability reply instead of stripping it",async(value)=>{
  vi.stubGlobal("fetch",vi.fn(async()=>Response.json(value)));const res=await handleAccount(await req("profile"),"profile");
  expect(res.status).toBe(503);expect(await res.text()).not.toContain("synthetic-private-token");
 });
 it("rejects profile credential fields before forwarding and bounds register body",async()=>{
  const fetch=vi.fn();vi.stubGlobal("fetch",fetch);
  expect((await handleAccount(await req("profile","PUT",{body:'{"password":"synthetic-private"}'}),"profile")).status).toBe(422);
  expect((await handleAccount(await req("register","POST",{anonymous:true,body:"x".repeat(8193)}),"register")).status).toBe(503);expect(fetch).not.toHaveBeenCalled();
 });
 it("forwards only empty delete intent and returns exact retained deletion status",async()=>{
  vi.stubGlobal("fetch",vi.fn(async()=>Response.json({status:"deleted"})));const res=await handleAccount(await req("delete","POST"),"delete");expect(await res.json()).toEqual({status:"deleted"});
 });
});

describe("finite owned bootstrap read", () => {
 it("forwards one exact empty owned GET with bearer and no replay/clock fan-out", async () => {
  const value = ownedBootstrapFixture();
  const fetch = vi.fn(async () => Response.json(value)); vi.stubGlobal("fetch", fetch);
  const response = await handleAccount(await req("bootstrap"), "bootstrap");
  expect(response.status).toBe(200); expect(await response.json()).toEqual(value);
  expect(response.headers.get("cache-control")).toBe("private, no-store");
  expect(fetch).toHaveBeenCalledTimes(1);
  const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe("https://backend.example.com/api/auth/bootstrap"); expect(init.method).toBe("GET");
  expect(init.body).toBeUndefined(); expect(new Headers(init.headers).get("authorization")).toBe("Bearer synthetic-private-bearer");
  expect(new Headers(init.headers).has("x-hirewiz-candidate-ingress")).toBe(false);
 });
 it("preserves independently unavailable components without creating a balance", async () => {
  const value = ownedBootstrapFixture({ wallet: null });
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(value)));
  const response = await handleAccount(await req("bootstrap"), "bootstrap");
  expect(response.status).toBe(200); expect(await response.json()).toEqual(value);
 });
 it.each(["POST", "PUT", "DELETE", "PATCH"])("does not expand bootstrap to %s", async (method) => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  expect((await handleAccount(await req("bootstrap", method), "bootstrap")).status).toBe(405);
  expect(fetch).not.toHaveBeenCalled();
 });
 it("requires the existing signed account and rejects client-selected URL/query scope", async () => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  expect((await handleAccount(await req("bootstrap", "GET", { anonymous: true }), "bootstrap")).status).toBe(401);
  expect((await handleAccount(new NextRequest(`${origin}/api/account/bootstrap?user_id=999`), "bootstrap")).status).toBe(404);
  expect(fetch).not.toHaveBeenCalled();
 });
 it.each(["content-length", "transfer-encoding", "content-encoding"])("rejects a body or encoded transport before forwarding: %s", async (header) => {
  const extraHeaders = { [header]: header === "content-length" ? "1" : header === "transfer-encoding" ? "chunked" : "gzip" };
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  expect((await handleAccount(await req("bootstrap", "GET", { extraHeaders }), "bootstrap")).status).toBe(422);
  expect(fetch).not.toHaveBeenCalled();
 });
 it.each([
  { ...ownedBootstrapFixture(), candidate_lifetime: { private: "synthetic-secret" } },
  { ...ownedBootstrapFixture(), unavailable: ["credit_wallet"] },
  { ...ownedBootstrapFixture(), profile: { ...ownedBootstrapFixture().profile, password_hash: "synthetic-secret" } },
  { ...ownedBootstrapFixture(), features: { injected: { enabled: true, access_token: "synthetic-secret" } } },
 ])("rejects malformed/private nested data without credential export", async (value) => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(value)));
  const response = await handleAccount(await req("bootstrap"), "bootstrap");
  expect(response.status).toBe(503); expect(await response.text()).not.toContain("synthetic-secret");
 });
});

describe("fixed owned dashboard read", () => {
 it("forwards one exact signed empty GET and validates its bounded advisory response", async () => {
  const value = ownedDashboardFixture(); const fetch = vi.fn(async () => Response.json(value)); vi.stubGlobal("fetch", fetch);
  const response = await handleAccount(await req("dashboard"), "dashboard");
  expect(response.status).toBe(200); expect(await response.json()).toEqual(value); expect(fetch).toHaveBeenCalledTimes(1);
  expect((fetch.mock.calls[0] as unknown as [string, RequestInit])[0]).toBe("https://backend.example.com/api/auth/dashboard");
 });
 it("passes only a dashboard404 as old-server compatibility, without forwarding private error bytes", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "Not Found" }, { status: 404 })));
  const response = await handleAccount(await req("dashboard"), "dashboard");
  expect(response.status).toBe(404); expect(await response.text()).not.toContain("synthetic-secret");
 });
 it("does not treat an owned-data404 or malformed404 as an absent new route", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "Owned record not found", access_token: "synthetic-secret" }, { status: 404 })));
  const response = await handleAccount(await req("dashboard"), "dashboard");
  expect(response.status).toBe(503); expect(await response.text()).not.toContain("synthetic-secret");
 });
 it.each([401, 429, 503])("does not convert dashboard%s into compatibility404", async (status) => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({}, { status })));
  expect((await handleAccount(await req("dashboard"), "dashboard")).status).toBe(status);
 });
 it.each(["POST", "PUT", "DELETE", "PATCH"])("refuses dashboard method%s without forwarding", async (method) => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  expect((await handleAccount(await req("dashboard", method), "dashboard")).status).toBe(405);
  expect(fetch).not.toHaveBeenCalled();
 });
 it("refuses anonymous or client-selected owner scope", async () => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  expect((await handleAccount(await req("dashboard", "GET", { anonymous: true }), "dashboard")).status).toBe(401);
  expect((await handleAccount(new NextRequest(`${origin}/api/account/dashboard?user_id=99`), "dashboard")).status).toBe(404);
  expect(fetch).not.toHaveBeenCalled();
 });
 it("fails the whole malformed or credential-bearing reply", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ ...ownedDashboardFixture(), candidate_lifetime: { secret: "synthetic-secret" } })));
  const response = await handleAccount(await req("dashboard"), "dashboard");
  expect(response.status).toBe(503); expect(await response.text()).not.toContain("synthetic-secret");
 });
});
