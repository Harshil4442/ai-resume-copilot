// Actual loopback HTTPS sockets and genuine native/V3/PG, synthetic inputs only.
import fs from 'node:fs';
import path from 'node:path';
import { candidateIngressHeaders } from '../lib/candidateIngressServer.ts';
const directory=process.env.HIREWIZ_CANDIDATE_FIXTURE_DIRECTORY, backend=process.env.BACKEND_URL;
const checks=[];let checkpoint='startup';
const password='synthetic-private-ingress-socket-password',id=process.env.HIREWIZ_CANDIDATE_TEST_ID;
const email=`ingress-${id}@example.com`;
function check(name,condition){if(!condition)throw new Error(name);checks.push(name);}
function counts(){return JSON.parse(fs.readFileSync(path.join(directory,'lifecycle-counts.json'),'utf8'));}
async function send(endpoint,{method='POST',value,bearer='',signed=false,headers}={}){
 const body=value===undefined?undefined:JSON.stringify(value);
 const init={method,headers:headers??{'Content-Type':'application/json',...(bearer?{Authorization:`Bearer ${bearer}`}:{})},body};
 if(signed)init.headers=candidateIngressHeaders(endpoint,init);
 const response=await fetch(backend+endpoint,{...init,redirect:'manual'});
 return {status:response.status,text:await response.text(),headers:init.headers,init};
}
function noPrivate(reply){return !/access_token|browser_pairing_session|account_binding_id|session_id|credential_sha256/.test(reply.text);}
try{
 checkpoint='public native operation inventory';
 const operations=[['GET','availability',undefined],['GET','session',undefined],
 ['POST','register',{email,password,accepted_terms:true,confirmed_age_18:true}],['POST','login',{email,password}],
 ['POST','registration-status',{email,password}],['POST','logout',undefined],
 ['POST','web-logout',{operation_id:crypto.randomUUID()}],['POST','password',{current_password:password,new_password:password+'2'}]];
 for(const [method,suffix,value]of operations){
  const endpoint='/api/auth/candidate/v1/'+suffix,before=JSON.stringify(counts());
  for(const headers of [undefined,{'Content-Type':'application/json','x-hirewiz-candidate-ingress':'ordinary-public-header'}]){
   const r=await send(endpoint,{method,value,headers});
   check(`direct public ${method} ${suffix} refuses private transport`,[401,403].includes(r.status)&&noPrivate(r));
  }
  check(`direct public ${suffix} never enters lifecycle`,before===JSON.stringify(counts()));
 }
 for(const endpoint of ['/api/%61uth/candidate/v1/login','/api/auth/candidate/v1/%6cogin','/api/auth/candidate/v1/login/','/api/auth/candidate/v1/login?private=1']){
  const r=await send(endpoint,{value:{email,password}});
  check('noncanonical direct native entry refuses or redirects without private output',r.status>=300&&noPrivate(r));
 }
 checkpoint='genuine trusted issuance';
 const registration=await send('/api/auth/candidate/v1/register',{value:{email,password,accepted_terms:true,confirmed_age_18:true},signed:true});
 check('actual signed registration uses genuine SQL/native/protected enrollment',registration.status===200&&JSON.parse(registration.text).status==='ENROLLED');
 const login=await send('/api/auth/candidate/v1/login',{value:{email,password},signed:true});
 check('actual signed login returns context only to this private server caller',login.status===200);
 const bearer=JSON.parse(login.text).access_token;
 check('trusted login returns real retained context',Boolean(JSON.parse(login.text).browser_pairing_session?.session_id));
 for(const endpoint of ['/api/auth/login','/api/auth/candidate/v1/login']){
  const before=JSON.stringify(counts()),r=await send(endpoint,{value:{email,password}});
  check('mapped candidate cannot downgrade to ordinary public credential login',r.status===403&&noPrivate(r));
  check('public mapped login never invokes lifecycle',before===JSON.stringify(counts()));
 }
 const beforeDelete=JSON.stringify(counts()),deleted=await send('/api/auth/delete-account',{bearer});
 check('public native bearer cannot delete through legacy endpoint',deleted.status===403&&noPrivate(deleted));
 check('native deletion private guard runs before native auth dependency',beforeDelete===JSON.stringify(counts()));
 const session=await send('/api/auth/candidate/v1/session',{method:'GET',bearer,signed:true});
 check('private real session stays usable after all public bypass refusals',session.status===200);
 checkpoint='unknown replay acknowledgement';
 for(const persistence of ['unpersisted','persisted']){
  const before=JSON.stringify(counts());fs.writeFileSync(path.join(directory,'ingress-unknown'),persistence);
  const r=await send('/api/auth/candidate/v1/login',{value:{email,password},signed:true});
  check(`actual ingress ${persistence} UNKNOWN returns fixed unavailable`,r.status===503&&noPrivate(r));
  check(`actual ingress ${persistence} UNKNOWN never enters lifecycle`,before===JSON.stringify(counts()));
  if(persistence==='persisted'){
   const replay=await send('/api/auth/candidate/v1/login',{value:{email,password},headers:r.headers});
   check('persisted unknown original assertion is refused without adoption',replay.status===403&&before===JSON.stringify(counts()));
  }
 }
 const again=await send('/api/auth/candidate/v1/login',{value:{email,password},signed:true});
 check('fresh trusted operation works after uncertain admission without adopting status',again.status===200);
 checkpoint='actual sixth native enrollment rate boundary';
 for(let n=2;n<=6;n++){
  const before=counts().register??0;
  const r=await send('/api/auth/candidate/v1/register',{value:{email:`limit-${n}-${id}@example.com`,password,accepted_terms:true,confirmed_age_18:true},signed:true});
  check(`genuine enrollment rate request ${n}`,n<6?r.status===200:r.status===429);
  if(n===6)check('rate-limited request never enters credential/native enrollment',before===(counts().register??0));
  if(n===6)check('429 never reflects private input or becomes generic 500',noPrivate(r)&&!r.text.includes(password));
 }
 fs.writeFileSync(path.join(directory,'private-ingress-socket-report.json'),JSON.stringify({status:'PASS',checks,scope:'actual HTTPS sockets; actual PG/native/V3; explicit synthetic operator/key/UID/clock custody; no candidate seeds'},null,2));
}catch{
 fs.writeFileSync(path.join(directory,'private-ingress-socket-report.json'),JSON.stringify({status:'FAIL',checkpoint,checks},null,2));process.exitCode=1;
}
