import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { once } from "node:events";
import { SessionStore, createBff, COOKIE_NAME } from "../src/bff.mjs";
import { SharedSessionRevocationClient } from "../src/shared-session-revocation-client.mjs";
import { PersonalMemoryClient } from "../src/personal-memory-client.mjs";

function fixture(t, { revoke=false, fail=false, extraResult=false, sharing=false }={}) {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),"preference-bff-")), key=crypto.randomBytes(32), calls=[];
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
  store.revalidate=async(_config,_id,_session,_fetch,_now,options)=>assert.equal(options.forcePrincipalCheck,true);
  const client=new PersonalMemoryClient({store,config,commitmentKey:key,origin:"https://core.test",credential:"a".repeat(64),
    fetchImpl:async(url,init)=>{
      calls.push({url,body:JSON.parse(init.body)});
      if(revoke) store.scheduleRevocation(sessionId,store.getForRevocation(sessionId),"logout");
      if(fail) throw new Error("uncertain dispatch");
      if(sharing) {
        const input=JSON.parse(init.body).request;
        const result=url.endsWith("sharing-propose") ? {
          version:1,operation_id:input.operation_id,source:"core.personal-preferences.v1",applies_to:"both_homes",
          effect:"read_and_manage_confirmed_preferences",grants_expire_at:new Date(Date.now()+86400000).toISOString(),
          expires_at:new Date(Date.now()+60000).toISOString(),reviewed_digest:"c".repeat(64),
        } : {version:1,operation_id:input.operation_id,status:"committed"};
        return new Response(JSON.stringify({version:1,result:{...result,...(extraResult ? {authority:{}} : {})}}),
          {headers:{"content-type":"application/json"}});
      }
      return new Response(JSON.stringify({version:1,result:{revision:0,fact_id:null,preference:null,
        confirmed_at:null,source:"core.personal-preferences.v1",source_site:null,
        ...(extraResult ? {principal_id:"private-owner"} : {})}}),{headers:{"content-type":"application/json"}});
    }});
  t.after(()=>{store.close();fs.rmSync(dir,{recursive:true,force:true});});
  return {store,config,client,sessionId,calls};
}

test("preference transport binds real session and never accepts browser owner claims",async t=>{
  const f=fixture(t);
  assert.equal((await f.client.request(f.sessionId,"read",{})).result.preference,null);
  assert.equal(f.calls[0].url,"https://core.test/internal/personal-memory/v1/read");
  assert.equal(f.calls[0].body.subject,"owner");
  assert.match(f.calls[0].body.session_commitment,/^[a-f0-9]{64}$/);
  await assert.rejects(f.client.request(f.sessionId,"read",{subject:"another-owner"}));
  assert.equal(f.calls.length,1);
});

test("logout during transport suppresses private response",async t=>{
  const f=fixture(t,{revoke:true});
  await assert.rejects(f.client.request(f.sessionId,"read",{}),/outcome_unknown/);
  assert.equal(f.calls.length,1);
});

test("unexpected private fields in Core response are not forwarded",async t=>{
  const f=fixture(t,{extraResult:true});
  await assert.rejects(f.client.request(f.sessionId,"read",{}),/outcome_unknown/);
});

test("uncertain confirmation is sent once without retry",async t=>{
  const f=fixture(t,{fail:true});
  await assert.rejects(f.client.request(f.sessionId,"confirm",{version:1,operation_id:crypto.randomUUID(),
    reviewed_digest:"c".repeat(64),gesture_id:crypto.randomUUID()}),/outcome_unknown/);
  assert.equal(f.calls.length,1);
  assert.ok(f.calls[0].body.gesture_id);
  assert.equal(f.calls[0].body.request.gesture_id,undefined);
});

test("browser preference route enforces origin and CSRF before contacting Core",async t=>{
  const f=fixture(t), server=createBff(f.config,{store:f.store,personalMemory:f.client});
  server.listen(0,"127.0.0.1"); await once(server,"listening");
  try {
    const target=`http://127.0.0.1:${server.address().port}/api/agent/personal-memory/read`;
    const headers={cookie:`${COOKIE_NAME}=${f.sessionId}`,"content-type":"application/json",origin:"https://home.test"};
    const denied=await fetch(target,{method:"POST",headers,body:"{}"});
    assert.equal(denied.status,403); assert.equal(f.calls.length,0);
    const allowed=await fetch(target,{method:"POST",headers:{...headers,"x-csrf-token":f.store.get(f.sessionId).csrf},body:"{}"});
    assert.equal(allowed.status,200); assert.equal(f.calls.length,1);
  } finally { await new Promise(resolve=>server.close(resolve)); }
});

test("sharing transport binds the session and validates the fixed review and outcomes",async t=>{
  const f=fixture(t,{sharing:true}), operation_id=crypto.randomUUID();
  const review=await f.client.request(f.sessionId,"sharing-propose",{version:1,operation_id});
  assert.equal(review.result.applies_to,"both_homes");
  assert.equal(f.calls[0].body.subject,"owner");
  assert.equal((await f.client.request(f.sessionId,"sharing-confirm",{version:1,operation_id,reviewed_digest:"c".repeat(64)})).result.status,"committed");
  await f.client.request(f.sessionId,"sharing-outcome",{version:1,operation_id});
  await assert.rejects(f.client.request(f.sessionId,"sharing-propose",{version:1,operation_id,capability:"lighting.execute"}));
  assert.equal(f.calls.length,3);
});

test("sharing never forwards private authority fields from Core",async t=>{
  const f=fixture(t,{sharing:true,extraResult:true});
  await assert.rejects(f.client.request(f.sessionId,"sharing-propose",{version:1,operation_id:crypto.randomUUID()}));
});

test("sharing browser route requires the existing CSRF boundary",async t=>{
  const f=fixture(t,{sharing:true}), server=createBff(f.config,{store:f.store,personalMemory:f.client});
  server.listen(0,"127.0.0.1"); await once(server,"listening");
  try {
    const target=`http://127.0.0.1:${server.address().port}/api/agent/personal-memory/sharing-propose`;
    const headers={cookie:`${COOKIE_NAME}=${f.sessionId}`,"content-type":"application/json",origin:"https://home.test"};
    const body=JSON.stringify({version:1,operation_id:crypto.randomUUID()});
    assert.equal((await fetch(target,{method:"POST",headers,body})).status,403);
    assert.equal(f.calls.length,0);
    assert.equal((await fetch(target,{method:"POST",headers:{...headers,"x-csrf-token":f.store.get(f.sessionId).csrf},body})).status,200);
  } finally { await new Promise(resolve=>server.close(resolve)); }
});
