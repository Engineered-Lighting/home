import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { once } from "node:events";
import { SessionStore, createBff, COOKIE_NAME } from "../src/bff.mjs";
import { SharedSessionRevocationClient } from "../src/shared-session-revocation-client.mjs";
import { LightingClient } from "../src/lighting-client.mjs";

const review = (input, extra = {}) => ({version:1,status:"review",review:{version:1,operation_id:input.operation_id,
  expires_at:new Date(Date.now()+60000).toISOString(),reviewed_digest:"d".repeat(64),
  operations:input.sites.map(site=>({site_id:site,name:"Kitchen",operation:input.operation,brightness:input.brightness})),
  ...extra}});
const summary = (input, status = "done") => ({version:1,operation_id:input.operation_id,status,
  results:[{site_id:"victoria",name:"Kitchen",operation:"off",brightness:null,status:"succeeded"}]});

function fixture(t, { reply, revoke=false, fail=false }={}) {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),"lighting-bff-")), key=crypto.randomBytes(32), calls=[];
  const config={ready:true,secureCookie:false,allowInMemorySessions:false,
    allowedOrigins:new Set(["https://home.test"]),haUrl:"https://ha.test",clientId:"https://home.test",
    redirectUri:"https://home.test/api/agent/auth/callback",postLoginRedirect:"/",
    coreUrl:"http://core.internal:8096",coreToken:"test-only",principalRevalidateMs:300000,
    sessionCleanupIntervalMs:60000,sessionCleanupBatchSize:10,idleTtlMs:600000,absoluteTtlMs:1200000,
    sessionDbPath:path.join(dir,"session.sqlite"),sessionEncryptionKey:crypto.randomBytes(32),
    sharedSessionRevocation:{commitmentKey:key,client:new SharedSessionRevocationClient({
      endpoint:"https://core.test/internal/shared-identity/v1/session-revocations",
      issuerId:"home-assistant:echo",credential:"e".repeat(64),fetchImpl:async()=>{throw new Error("offline fixture");}})}};
  const store=new SessionStore(config);
  const sessionId=store.retainLoginTokens({accessToken:Buffer.from("access"),refreshToken:Buffer.from("refresh"),expiresIn:600});
  store.completeLogin(sessionId,{userId:"owner",isActive:true,haIssuerId:"home-assistant:echo",siteId:"echo"});
  const forced=[];
  store.revalidate=async(_config,_id,_session,_fetch,_now,options)=>{forced.push(options.forcePrincipalCheck);};
  const client=new LightingClient({store,config,commitmentKey:key,origin:"https://core.test",credential:"a".repeat(64),
    fetchImpl:async(url,init)=>{
      const body=JSON.parse(init.body);
      calls.push({url,body});
      if(revoke) store.scheduleRevocation(sessionId,store.getForRevocation(sessionId),"logout");
      if(fail) throw new Error("uncertain dispatch");
      const [status,value]=reply(url.split("/").at(-1),body.request);
      return new Response(JSON.stringify(value),{status,headers:{"content-type":"application/json"}});
    }});
  t.after(()=>{store.close();fs.rmSync(dir,{recursive:true,force:true});});
  return {store,config,client,sessionId,calls,forced};
}

const proposal = () => ({version:1,operation_id:crypto.randomUUID(),sites:["victoria"],targets:["kitchen"],
  operation:"off",brightness:null});

test("lighting transport binds the session and forwards only the typed request",async t=>{
  const f=fixture(t,{reply:(op,input)=>[200,{version:1,result:review(input)}]});
  const input=proposal();
  const result=await f.client.request(f.sessionId,"propose",input);
  assert.equal(result.result.review.operations[0].name,"Kitchen");
  assert.equal(f.calls[0].url,"https://core.test/internal/lighting/v1/propose");
  assert.equal(f.calls[0].body.subject,"owner");
  assert.match(f.calls[0].body.session_commitment,/^[a-f0-9]{64}$/);
  assert.deepEqual(f.calls[0].body.request,input);
  // Every lighting request re-checks the Home Assistant principal first.
  assert.ok(f.forced.length>=2 && f.forced.every(Boolean));
});

test("browser requests cannot carry entities, scenes, owners or unbounded changes",async t=>{
  const f=fixture(t,{reply:(op,input)=>[200,{version:1,result:review(input)}]});
  for (const bad of [
    {...proposal(),entity_id:"light.kitchen"}, {...proposal(),targets:["scene.movie"]}, {...proposal(),operation:"toggle"},
    {...proposal(),operation:"brightness",brightness:0}, {...proposal(),operation:"on",brightness:40},
    {...proposal(),sites:["victoria","victoria"]}, {...proposal(),sites:["paris"]}, {...proposal(),subject:"someone"},
  ]) await assert.rejects(f.client.request(f.sessionId,"propose",bad),/invalid_lighting_request/);
  assert.equal(f.calls.length,0);
});

test("Core replies with private fields or mismatched operations are not forwarded",async t=>{
  for (const mutate of [
    input=>review(input,{entity_ids:["light.kitchen"]}),
    input=>{const value=review(input);value.review.operations[0].operation="on";return value;},
    input=>{const value=review(input);value.review.operation_id=crypto.randomUUID();return value;},
    input=>{const value=review(input);value.review.expires_at=new Date(Date.now()+3600000).toISOString();return value;},
  ]) {
    const f=fixture(t,{reply:(op,input)=>[200,{version:1,result:mutate(input)}]});
    await assert.rejects(f.client.request(f.sessionId,"propose",proposal()),/lighting_outcome_unknown/);
  }
});

test("an uncertain confirmation is sent once and reported as unknown",async t=>{
  const f=fixture(t,{fail:true,reply:()=>[500,{}]});
  await assert.rejects(f.client.request(f.sessionId,"confirm",{version:1,operation_id:crypto.randomUUID(),
    reviewed_digest:"d".repeat(64)}),/lighting_outcome_unknown/);
  assert.equal(f.calls.length,1);
});

test("logout during transport suppresses the private response",async t=>{
  const f=fixture(t,{revoke:true,reply:(op,input)=>[200,{version:1,result:summary(input)}]});
  await assert.rejects(f.client.request(f.sessionId,"outcome",{version:1,operation_id:crypto.randomUUID()}),
    /lighting_outcome_unknown/);
});

test("missing consent is a definite typed refusal",async t=>{
  const f=fixture(t,{reply:()=>[403,{error:"lighting_not_permitted"}]});
  await assert.rejects(f.client.request(f.sessionId,"propose",proposal()),/lighting_not_permitted/);
});

test("consent and status replies are validated exactly",async t=>{
  const f=fixture(t,{reply:(op,input)=>[200,{version:1,result:op==="status" ?
    {version:1,permitted:{echo:true,victoria:false},homes:["echo","victoria"]} : op==="consent-propose" ?
    {version:1,operation_id:input.operation_id,source:"core.lighting.v1",applies_to:"both_homes",
     effect:"switch_allowlisted_lights_after_each_confirmation",grants_expire_at:new Date(Date.now()+86400000).toISOString(),
     expires_at:new Date(Date.now()+60000).toISOString(),reviewed_digest:"c".repeat(64)} :
    {version:1,operation_id:input.operation_id,status:"committed"}}]});
  assert.equal((await f.client.request(f.sessionId,"status",{})).result.permitted.victoria,false);
  const operation_id=crypto.randomUUID();
  assert.equal((await f.client.request(f.sessionId,"consent-propose",{version:1,operation_id})).result.source,"core.lighting.v1");
  assert.equal((await f.client.request(f.sessionId,"consent-confirm",{version:1,operation_id,reviewed_digest:"c".repeat(64)})).result.status,"committed");
  await assert.rejects(f.client.request(f.sessionId,"consent-propose",{version:1,operation_id,capability:"memory.read"}));
});

test("browser lighting routes enforce origin and CSRF before contacting Core",async t=>{
  const f=fixture(t,{reply:(op,input)=>[op==="propose" ? 200 : 403,op==="propose" ?
    {version:1,result:review(input)} : {error:"lighting_not_permitted"}]});
  const server=createBff(f.config,{store:f.store,lighting:f.client});
  server.listen(0,"127.0.0.1"); await once(server,"listening");
  try {
    const base=`http://127.0.0.1:${server.address().port}/api/agent/lighting/`;
    const headers={cookie:`${COOKIE_NAME}=${f.sessionId}`,"content-type":"application/json",origin:"https://home.test"};
    const body=JSON.stringify(proposal());
    assert.equal((await fetch(base+"propose",{method:"POST",headers,body})).status,403);
    assert.equal(f.calls.length,0);
    const csrf={...headers,"x-csrf-token":f.store.get(f.sessionId).csrf};
    assert.equal((await fetch(base+"propose",{method:"POST",headers:csrf,body})).status,200);
    const refused=await fetch(base+"consent-outcome",{method:"POST",headers:csrf,
      body:JSON.stringify({version:1,operation_id:crypto.randomUUID()})});
    assert.equal(refused.status,403); assert.equal((await refused.json()).error,"lighting_not_permitted");
    const invalid=await fetch(base+"propose",{method:"POST",headers:csrf,body:JSON.stringify({...proposal(),operation:"toggle"})});
    assert.equal(invalid.status,422);
    const before=f.calls.length;
    assert.notEqual((await fetch(base+"execute",{method:"POST",headers:csrf,body})).status,200);
    assert.equal(f.calls.length,before); // unknown lighting paths never reach Core
    const session=await (await fetch(`http://127.0.0.1:${server.address().port}/api/agent/auth/session`,{headers})).json();
    assert.equal(session.lighting_enabled,true);
  } finally { await new Promise(resolve=>server.close(resolve)); }
});
