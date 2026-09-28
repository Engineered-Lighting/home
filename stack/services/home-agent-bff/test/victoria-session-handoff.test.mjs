import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { createVictoriaSessionStore } from "../src/bff.mjs";
import { QualifiedHaAuth } from "../src/qualified-ha-auth.mjs";
import { SharedSessionRevocationClient } from "../src/shared-session-revocation-client.mjs";
import { VictoriaSessionHandoff } from "../src/victoria-session-handoff.mjs";
const json=value=>new Response(JSON.stringify(value),{headers:{"content-type":"application/json"}});
const pair="00000000-0000-0000-0000-000000000001";

async function fixture(t) {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),"victoria-handoff-"));
  let wall=Date.now(),mono=0,hook,subject="owner",checks=0;
  const auth=new QualifiedHaAuth({issuerId:"home-assistant:victoria",siteId:"victoria",origin:"https://ha.victoria.test",
    clientId:"https://victoria.test/",redirectUri:"https://victoria.test/callback",fetchImpl:async(url,init)=>{
      if(url.endsWith("/whoami")){checks++;await hook?.();return json({user_id:subject,is_active:true});}
      if(init.body.get("action")==="revoke")return new Response(null,{status:200});
      return json({access_token:"access",refresh_token:"refresh",expires_in:600});
    }});
  const key=crypto.randomBytes(32);
  const client=new SharedSessionRevocationClient({endpoint:"https://private.test/internal/shared-identity/v1/session-revocations",
    issuerId:"home-assistant:victoria",credential:"e".repeat(64),now:()=>wall,fetchImpl:async()=>{throw new Error("unused");}});
  const {store,config}=createVictoriaSessionStore({auth,browserOrigin:"https://victoria.test",echoOrigins:new Set(["https://echo.test"]),
    sessionDbPath:path.join(dir,"sessions.sqlite"),sessionEncryptionKey:crypto.randomBytes(32),idleTtlMs:600000,absoluteTtlMs:1200000,
    now:()=>wall,sharedSessionRevocation:{client,commitmentKey:key}});
  const options={store,config,commitmentKey:key,monotonicNow:()=>mono};
  const handoff=new VictoriaSessionHandoff(options);
  t.after(()=>{handoff.close();store.close();fs.rmSync(dir,{recursive:true,force:true});});
  const {id}=await store.authenticateLogin("a".repeat(32));
  return {handoff,store,id,key,options,get checks(){return checks;},advance:(ms)=>{wall+=ms;mono+=ms;},
    drift:ms=>wall+=ms,setHook:f=>hook=f,setSubject:s=>subject=s};
}

test("handoff validates the real Victoria session and discloses no subject or HA credentials",async t=>{
  const f=await fixture(t),offer=await f.handoff.create({sessionId:f.id,pairingId:pair});
  assert.deepEqual(Object.keys(offer).sort(),["expires_at","pairing_id","token"]);
  assert.match(offer.token,/^[a-f0-9]{64}$/);
  const admitted=await f.handoff.redeem({token:offer.token,pairingId:pair});
  assert.deepEqual(Object.keys(admitted).sort(),["issuer_id","pairing_id","session_commitment","site_id","valid_until"]);
  assert.equal(admitted.session_commitment,f.store.linkingContext(f.id,f.key).sessionCommitment);
  assert.equal(admitted.issuer_id,"home-assistant:victoria");assert.equal(f.checks,3);
  await assert.rejects(f.handoff.redeem({token:offer.token,pairingId:pair}),/unavailable/);
});

test("wrong pairing cannot consume the legitimate one-use offer",async t=>{
  const f=await fixture(t),offer=await f.handoff.create({sessionId:f.id,pairingId:pair});
  await assert.rejects(f.handoff.redeem({token:offer.token,pairingId:"00000000-0000-0000-0000-000000000002"}),/unavailable/);
  assert.equal((await f.handoff.redeem({token:offer.token,pairingId:pair})).pairing_id,pair);
});

for(const mode of ["expired","rollback","drift","logout","changed_subject"]){
  test(`handoff rejects ${mode} before returning session authority`,async t=>{
    const f=await fixture(t),offer=await f.handoff.create({sessionId:f.id,pairingId:pair});
    if(mode==="expired")f.advance(60000);
    if(mode==="rollback")f.drift(-1);
    if(mode==="drift")f.drift(1001);
    if(mode==="logout")f.store.scheduleRevocation(f.id,f.store.getForRevocation(f.id),"logout");
    if(mode==="changed_subject")f.setSubject("other");
    await assert.rejects(f.handoff.redeem({token:offer.token,pairingId:pair}),/unavailable/);
  });
}

test("logout during redemption suppresses delivery and token stays consumed",async t=>{
  const f=await fixture(t),offer=await f.handoff.create({sessionId:f.id,pairingId:pair});
  f.setHook(()=>f.store.scheduleRevocation(f.id,f.store.getForRevocation(f.id),"logout"));
  await assert.rejects(f.handoff.redeem({token:offer.token,pairingId:pair}),/unavailable/);
  f.setHook(undefined);await assert.rejects(f.handoff.redeem({token:offer.token,pairingId:pair}),/unavailable/);
});

test("concurrent creation and redemption cannot duplicate an offer",async t=>{
  const f=await fixture(t);let release;const gate=new Promise(resolve=>release=resolve);
  f.setHook(()=>gate);
  const work=f.handoff.create({sessionId:f.id,pairingId:pair});
  await assert.rejects(f.handoff.create({sessionId:f.id,pairingId:pair}),/busy/);
  release();const offer=await work;
  const first=f.handoff.redeem({token:offer.token,pairingId:pair});
  await assert.rejects(f.handoff.redeem({token:offer.token,pairingId:pair}),/unavailable/);
  assert.equal((await first).pairing_id,pair);
});

test("restart loses ephemeral offers rather than replaying bearer handoffs",async t=>{
  const f=await fixture(t),offer=await f.handoff.create({sessionId:f.id,pairingId:pair});f.handoff.close();
  const restarted=new VictoriaSessionHandoff(f.options);
  try{await assert.rejects(restarted.redeem({token:offer.token,pairingId:pair}),/unavailable/);}
  finally{restarted.close();}
});
