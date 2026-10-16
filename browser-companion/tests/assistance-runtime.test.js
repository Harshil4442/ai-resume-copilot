import assert from 'node:assert/strict';
import test from 'node:test';
import { AssistanceRuntime } from '../extension/assistance-runtime.js';
const device='11111111-1111-4111-8111-111111111111',review='22222222-2222-4222-8222-222222222222';
async function runtime(){return new AssistanceRuntime({transport:{identity:{device_id:device}},browser:{},store:{read:async()=>null,write:async()=>{}}}).initialise();}
for(const [version,count,accept] of [[1,7,true],[1,8,false],[2,38,true],[2,64,true],[2,65,false],[3,1,true],[3,2,false],[3,0,false]])test(`erased durable v${version} completed count ${count} ${accept?'retains':'refuses'} exact protocol bound`,async()=>{
  const r=await runtime();const response={id:review,state:'filled',review_digest:'a'.repeat(64),filled_count:count,expires_at_ms:Date.now()-1000,payload:{},...(version>1?{review_protocol_version:version}:{})};
  if(accept)await r.acceptReview(response);else await assert.rejects(r.acceptReview(response));
});
test('durable marker cannot reinterpret a live payload version',async()=>{
  const r=await runtime();await assert.rejects(r.acceptReview({id:review,state:'draft',review_digest:'a'.repeat(64),filled_count:0,expires_at_ms:Date.now()+1000,review_protocol_version:2,payload:{review_protocol_version:3}}),/durable reviewed control version/);
});
for(const changed of ['application_id','target_url','review_protocol_version','hosted_controls_available','field_mapping'])test(`hosted discovery preserves exact application response binding: ${changed}`,async()=>{
  const r=await runtime();r.state.phase='active';let requests=0,inspections=0;
  const initial={application_id:'apply_test',target_url:'https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/4740333005',review_protocol_version:2,hosted_controls_available:true,field_mapping:[]};
  const values={application_id:'another_application',target_url:'https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/999',review_protocol_version:1,hosted_controls_available:false,field_mapping:null};
  r.transport.device=async()=>({state:'active'});r.transport.request=async()=>++requests===1?initial:{...initial,[changed]:values[changed]};
  r.browser.inspect=async()=>{inspections++;return{hosted_rows:[{index:0,current_role:false}],document_id:'owned-document'}};
  await assert.rejects(r.prepare('apply_test'),/hosted controls changed/);assert.equal(inspections,1);assert.equal(requests,2);
});
for(const [state,status,confirmed,count,accept] of [
  ['unknown','unknown',false,0,true],['filled','confirmed',true,1,true],['cancelled','verified_not_submitted',false,0,true],
  ['unknown','confirmed',true,1,false],['unknown','unknown',true,0,false],['unknown','unknown',false,1,false],
])test(`durable erased v4 ${state}/${status} keeps exact authoritative outcome and fee policy (${accept})`,async()=>{
  const r=await runtime();const result={id:review,state,review_protocol_version:4,review_digest:'a'.repeat(64),filled_count:count,expires_at_ms:Date.now()-1000,payload:{},submission_status:status,employer_confirmed:confirmed,fee_credits:20};
  if(accept)await r.acceptReview(result);else await assert.rejects(r.acceptReview(result));
  await assert.rejects(r.acceptReview({...result,fee_credits:0}));
});
