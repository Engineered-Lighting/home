import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { SessionStore, createBff, createVictoriaSessionStore } from "../src/bff.mjs";
import { QualifiedHaAuth } from "../src/qualified-ha-auth.mjs";
import { SharedSessionRevocationClient } from "../src/shared-session-revocation-client.mjs";

const json = (value) => new Response(JSON.stringify(value), {headers:{"content-type":"application/json"}});

test("Victoria logout delivers only its issuer-bound tracked session tombstone",async t=>{
  const f=fixture(t),requests=[],key=Buffer.alloc(32,27);
  const client=new SharedSessionRevocationClient({endpoint:"https://victoria-core.test/internal/shared-identity/v1/session-revocations",
    issuerId:"home-assistant:victoria",credential:"b".repeat(64),now:f.now,fetchImpl:async(_url,init)=>{
      const value=JSON.parse(init.body).revocation;requests.push(value);
      return json({version:1,revocation:{...value,issuer_id:"home-assistant:victoria",revoked_at:new Date(f.now()).toISOString()}});
    }});
  const {store,config}=createVictoriaSessionStore({...f.options,sharedSessionRevocation:{client,commitmentKey:key}});f.track(store);
  const {id}=await store.authenticateLogin("a".repeat(32));const context=store.linkingContext(id,key);
  assert.equal(await store.revoke(config,id,store.getForRevocation(id),undefined),true);
  assert.equal(requests.length,1);assert.equal(requests[0].session_commitment,context.sessionCommitment);
  await store.cleanupExpired(config,undefined);assert.equal(requests.length,1);
});
function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(),"victoria-sessions-"));
  const tracked=[];
  t.after(()=>{for(const store of tracked) store.close();fs.rmSync(dir,{recursive:true,force:true});});
  const calls=[]; let active=true; let subject="same-owner"; let clock=Date.now();
  const fetchImpl=async(url,options)=>{
    calls.push({url,options});
    assert.equal(options.redirect,"error");
    if(url.endsWith("/whoami")) return json({user_id:subject,is_active:active,is_admin:false});
    assert.equal(url,"https://victoria-ha.test/auth/token");
    if(options.body.get("action")==="revoke") return new Response(null,{status:200});
    return json({access_token:"private-access",refresh_token:"private-refresh",expires_in:90});
  };
  const authConfig={issuerId:"home-assistant:victoria",siteId:"victoria",origin:"https://victoria-ha.test",
    clientId:"https://victoria-home.test/",redirectUri:"https://victoria-home.test/auth/callback",fetchImpl};
  const options={auth:new QualifiedHaAuth(authConfig),browserOrigin:"https://victoria-home.test",
    echoOrigins:new Set(["https://echo-home.test"]),sessionDbPath:path.join(dir,"sessions.sqlite"),
    sessionEncryptionKey:Buffer.alloc(32,43),idleTtlMs:600000,absoluteTtlMs:1200000,now:()=>clock};
  return {options,calls,authConfig,track:(store)=>tracked.push(store),advance:(n)=>clock+=n,now:()=>clock,inactive:()=>active=false,changeSubject:()=>subject="other-owner"};
}

test("Victoria session login is verified, persistent and isolated from legacy authority",async(t)=>{
  const f=fixture(t);let {store,config}=createVictoriaSessionStore(f.options);
  assert.throws(()=>store.createSession({}),/verified login/);
  assert.throws(()=>store.completeLogin("fake",{userId:"owner"}),/not been verified/);
  assert.throws(()=>createBff({ready:false},{store}),/Echo sessions/);
  const login=await store.authenticateLogin("a".repeat(32));
  let session=store.get(login.id);
  assert.equal(session.principal.haIssuerId,"home-assistant:victoria");
  assert.equal(session.principal.userId,"same-owner");
  const commitment=store.linkingContext(login.id,Buffer.alloc(32,8));
  assert.equal(commitment.issuerId,"home-assistant:victoria");
  assert.throws(()=>store.linkingContext(login.id,f.options.sessionEncryptionKey),/dedicated/);
  store.close();
  ({store,config}=createVictoriaSessionStore(f.options));f.track(store);
  session=store.get(login.id);assert.equal(session.principal.userId,"same-owner");
  const before=f.calls.length;
  await assert.rejects(store.revalidate({...config},login.id,session,undefined,f.now(),{forcePrincipalCheck:true}),/issuer mismatch/);
  assert.equal(f.calls.length,before);
  f.advance(40000);
  await store.revalidate(config,login.id,session,()=>assert.fail("caller transport forbidden"),0,{forcePrincipalCheck:true});
  assert(f.calls.some(({options})=>options.body?.get("grant_type")==="refresh_token"));
  assert.equal(await store.revoke(config,login.id,session,()=>assert.fail("caller transport forbidden")),true);
  assert.equal(store.get(login.id),null);
  assert(f.calls.some(({options})=>options.body?.get("action")==="revoke"));
});

test("qualified database rejects key, HA transport, callback and origin binding drift before reuse",async(t)=>{
  const f=fixture(t);let {store}=createVictoriaSessionStore(f.options);
  await store.authenticateLogin("a".repeat(32));store.close();
  for(const change of [
    {sessionEncryptionKey:Buffer.alloc(32,9)},
    {auth:new QualifiedHaAuth({...f.authConfig,origin:"https://other-ha.test"})},
    {auth:new QualifiedHaAuth({...f.authConfig,redirectUri:"https://victoria-home.test/other"})},
    {echoOrigins:new Set(["https://other-echo.test"])}
  ]) assert.throws(()=>createVictoriaSessionStore({...f.options,...change}));
  ({store}=createVictoriaSessionStore(f.options));store.close();
  assert.throws(()=>new SessionStore({...f.options,haIssuerId:"home-assistant:echo",siteId:"echo",allowedOrigins:new Set(["https://echo-home.test"])}),/qualified sessions cannot enter legacy/);
});

test("Victoria cannot adopt legacy sessions or bypass explicit qualified factory",(t)=>{
  const f=fixture(t);
  assert.throws(()=>new SessionStore({...f.options,haIssuerId:"home-assistant:victoria",siteId:"victoria"}),/legacy authority/);
  assert.throws(()=>new SessionStore({...f.options,haIssuerId:"home-assistant:victoria",siteId:"victoria"},{}),/invalid session profile/);
  const legacy=new SessionStore({...f.options,allowedOrigins:new Set(["https://echo-home.test"])});legacy.close();
  assert.throws(()=>createVictoriaSessionStore(f.options),/cannot adopt legacy/);
  const db=new DatabaseSync(f.options.sessionDbPath);
  assert.equal(db.prepare("SELECT issuer_id FROM bff_issuer_binding").get().issuer_id,"home-assistant:echo");db.close();
});

test("Victoria requires distinct cookie hostname and exact browser/client origins",(t)=>{
  const f=fixture(t);
  for(const change of [{echoOrigins:new Set()}, {echoOrigins:new Set(["https://victoria-home.test:8443"])},
    {browserOrigin:"https://victoria-home.test/"},{browserOrigin:"https://other-home.test"},
    {echoOrigins:new Set(["http://echo-home.test"])}, {sessionDbPath:":memory:"}]) {
    assert.throws(()=>createVictoriaSessionStore({...f.options,...change}));
  }
});

test("unverified login is revoked and cannot create a usable Victoria session",async(t)=>{
  const f=fixture(t);f.inactive();const {store}=createVictoriaSessionStore(f.options);f.track(store);
  await assert.rejects(store.authenticateLogin("a".repeat(32)),/login unavailable/);
  assert.equal(store.sessions.size,0);
  assert(f.calls.some(({options})=>options.body?.get("action")==="revoke"));
});

test("changed HA subject is rejected during forced revalidation",async(t)=>{
  const f=fixture(t);const {store,config}=createVictoriaSessionStore(f.options);f.track(store);
  const login=await store.authenticateLogin("a".repeat(32));const session=store.get(login.id);f.changeSubject();
  await assert.rejects(store.revalidate(config,login.id,session,undefined,f.now(),{forcePrincipalCheck:true}),/principal changed/);
  assert.equal(store.get(login.id),null);
  assert.equal(store.linkingContext(login.id,Buffer.alloc(32,8)),null);
});


test("qualified login admission is bounded before exchanging more codes",async(t)=>{
  const f=fixture(t);let release;const gate=new Promise(resolve=>release=resolve);let calls=0;
  const auth=new QualifiedHaAuth({...f.authConfig,fetchImpl:async(url,options)=>{
    if(options.body?.get("grant_type")==="authorization_code") {calls++;await gate;}
    return f.authConfig.fetchImpl(url,options);
  }});
  const {store}=createVictoriaSessionStore({...f.options,auth});f.track(store);
  const first=store.authenticateLogin("a".repeat(32)),second=store.authenticateLogin("b".repeat(32));
  await assert.rejects(store.authenticateLogin("c".repeat(32)),/login unavailable/);
  assert.equal(calls,2);release();await Promise.all([first,second]);
});

test("failed qualified logout stays unusable and resumes revocation after restart",async(t)=>{
  const f=fixture(t);let refuse=true;
  const auth=new QualifiedHaAuth({...f.authConfig,fetchImpl:async(url,options)=>{
    if(refuse && options.body?.get("action")==="revoke") throw new Error("offline");
    return f.authConfig.fetchImpl(url,options);
  }});
  let {store,config}=createVictoriaSessionStore({...f.options,auth});
  const login=await store.authenticateLogin("a".repeat(32));const session=store.get(login.id);
  assert.equal(await store.revoke(config,login.id,session),false);
  assert.equal(store.get(login.id),null);store.close();
  ({store,config}=createVictoriaSessionStore({...f.options,auth}));f.track(store);
  assert.equal(store.get(login.id),null);assert(store.getForRevocation(login.id));
  refuse=false;
  assert.equal(await store.revoke(config,login.id,store.getForRevocation(login.id)),true);
});

test("logout during refreshed-principal check cannot revive session and cleans rotated token",async(t)=>{
  const f=fixture(t);let hold=false,entered,release;
  const started=new Promise(resolve=>entered=resolve);const gate=new Promise(resolve=>release=resolve);
  const auth=new QualifiedHaAuth({...f.authConfig,fetchImpl:async(url,options)=>{
    if(hold && url.endsWith("/whoami")) {entered();await gate;}
    return f.authConfig.fetchImpl(url,options);
  }});
  const {store,config}=createVictoriaSessionStore({...f.options,auth});f.track(store);
  const login=await store.authenticateLogin("a".repeat(32));const session=store.get(login.id);
  hold=true;f.advance(40000);
  const checking=store.revalidate(config,login.id,session,undefined,f.now(),{forcePrincipalCheck:true});
  await started;
  const count=f.calls.length;
  await assert.rejects(store.revalidate(config,login.id,session),/revalidation busy/);
  assert.equal(f.calls.length,count);assert.equal(session.state,"active");
  assert.equal(await store.revoke(config,login.id,session),true);release();
  await assert.rejects(checking,/ended during revalidation/);
  assert.equal(store.get(login.id),null);assert.equal(store.sessions.size,0);
  assert.equal(f.calls.filter(({options})=>options.body?.get("action")==="revoke").length,2);
});


test("qualified session expiry and backwards clock reject before token disclosure",async(t)=>{
  const f=fixture(t);const {store,config}=createVictoriaSessionStore(f.options);f.track(store);
  const first=await store.authenticateLogin("a".repeat(32));const firstSession=store.get(first.id);
  f.advance(-1000);let count=f.calls.length;
  await assert.rejects(store.revalidate(config,first.id,firstSession,undefined,Date.now()+100000),/unavailable before/);
  assert.equal(f.calls.length,count);assert.equal(store.get(first.id),null);
  f.advance(1000);const second=await store.authenticateLogin("b".repeat(32));const secondSession=store.get(second.id);
  f.advance(f.options.idleTtlMs);count=f.calls.length;
  await assert.rejects(store.revalidate(config,second.id,secondSession,undefined,0),/unavailable before/);
  assert.equal(f.calls.length,count);assert.equal(store.get(second.id),null);
});
