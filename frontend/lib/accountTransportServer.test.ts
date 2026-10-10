// @vitest-environment node
import { createHash } from "node:crypto";
import { encode } from "next-auth/jwt";
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { handleAccount } from "./accountTransportServer";
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
