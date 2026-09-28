import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import https from "node:https";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {createVictoriaSessionStore} from "../src/bff.mjs";
import {QualifiedHaAuth} from "../src/qualified-ha-auth.mjs";
import {SharedSessionRevocationClient} from "../src/shared-session-revocation-client.mjs";
import {VictoriaSessionHandoff} from "../src/victoria-session-handoff.mjs";
import {createVictoriaHandoffIngress,VICTORIA_HANDOFF_PATH} from "../src/victoria-handoff-ingress.mjs";
import {createVictoriaHandoffBrowser,VICTORIA_HANDOFF_BROWSER_PATH} from "../src/victoria-handoff-browser.mjs";
import {COOKIE_NAME} from "../src/bff.mjs";
import {HaLoginFlow} from "../src/ha-login-flow.mjs";
import {HaRevocationOutbox} from "../src/ha-revocation-outbox.mjs";
import {FreshHaCeremony} from "../src/fresh-ha-ceremony.mjs";
import {GovernedFreshHaCeremony} from "../src/governed-fresh-ha-ceremony.mjs";
import {SharedAuthProofClient} from "../src/shared-auth-proof-client.mjs";
import {SharedAuthProofJournal} from "../src/shared-auth-proof-journal.mjs";
import {VictoriaLinkCeremony} from "../src/victoria-link-ceremony.mjs";
import {VictoriaLinkAuthentication} from "../src/victoria-link-authentication.mjs";

const file=name=>fs.readFileSync(new URL(`../../../../tools/shared-home/tests/fixtures/gateway_client/${name}`,import.meta.url));
const json=v=>new Response(JSON.stringify(v),{headers:{"content-type":"application/json"}});
const pairingId="00000000-0000-0000-0000-000000000001";
async function fixture(t,browser=false,enableAuthentication=false){
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),"handoff-tls-"));
  const auth=new QualifiedHaAuth({issuerId:"home-assistant:victoria",siteId:"victoria",origin:"https://ha.test",
    clientId:"https://victoria.test/",redirectUri:"https://victoria.test/callback",fetchImpl:async url=>
      url.endsWith("/whoami")?json({user_id:"owner",is_active:true}):json({access_token:"access",refresh_token:"refresh",expires_in:600})});
  const key=crypto.randomBytes(32),client=new SharedSessionRevocationClient({endpoint:"https://private.test/internal/shared-identity/v1/session-revocations",
    issuerId:"home-assistant:victoria",credential:"d".repeat(64),fetchImpl:()=>assert.fail("unused")});
  const {store,config}=createVictoriaSessionStore({auth,browserOrigin:"https://victoria.test",echoOrigins:new Set(["https://echo.test"]),
    sessionDbPath:path.join(dir,"sessions.sqlite"),sessionEncryptionKey:crypto.randomBytes(32),idleTtlMs:600000,absoluteTtlMs:1200000,
    sharedSessionRevocation:{client,commitmentKey:key}});
  const handoff=new VictoriaSessionHandoff({store,config,commitmentKey:key});
  let authentication,proofs,outbox;
  if(enableAuthentication){
    outbox=new HaRevocationOutbox({databasePath:path.join(dir,"cleanup.sqlite"),encryptionKey:crypto.randomBytes(32),auth});
    proofs=new SharedAuthProofJournal({databasePath:path.join(dir,"proofs.sqlite"),encryptionKey:crypto.randomBytes(32),issuerId:"home-assistant:victoria"});
    const flow=new HaLoginFlow({origin:"https://ha.test",clientId:"https://victoria.test/",redirectUri:"https://victoria.test/callback",issuerId:"home-assistant:victoria",
      fetchImpl:async()=>json({type:"form",flow_id:"f".repeat(32),handler:["homeassistant",null],step_id:"init",data_schema:[{name:"username"},{name:"password"}]})});
    const proofClient=new SharedAuthProofClient({endpoint:"https://private.test/internal/shared-identity/v1/auth-proofs",credential:"f".repeat(64),issuerId:"home-assistant:victoria",fetchImpl:()=>assert.fail("unused")});
    const ceremony=new VictoriaLinkCeremony({store,config,commitmentKey:key,ceremony:new GovernedFreshHaCeremony({
      ceremony:new FreshHaCeremony({flow,auth,outbox}),client:proofClient,journal:proofs})});
    authentication=new VictoriaLinkAuthentication({handoff,ceremony,store,config});
  }
  const tls={key:file("server-test-only.key"),cert:file("server.pem")};
  const server=browser?createVictoriaHandoffBrowser({store,config,handoff,tls,authentication}):createVictoriaHandoffIngress({handoff,credential:"e".repeat(64),tls,authentication});
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  t.after(async()=>{await new Promise(resolve=>server.close(resolve));proofs?.close();outbox?.close();handoff.close();store.close();fs.rmSync(dir,{recursive:true,force:true});});
  const {id}=await store.authenticateLogin("a".repeat(32));
  const offer=browser?null:await handoff.create({sessionId:id,pairingId});
  const body=browser?{version:1,pairing_id:pairingId,consent:true}:{version:1,token:offer.token,pairing_id:pairingId};
  const defaults=browser?{host:"victoria.test",origin:"https://victoria.test",cookie:`${COOKIE_NAME}=${id}`,"x-csrf-token":store.get(id).csrf}:{authorization:`Bearer ${"e".repeat(64)}`};
  const request=({headers={},raw=JSON.stringify(body),method="POST",url=browser?VICTORIA_HANDOFF_BROWSER_PATH:VICTORIA_HANDOFF_PATH}={})=>new Promise((resolve,reject)=>{
    const req=https.request({host:"127.0.0.1",servername:"localhost",port:server.address().port,ca:file("ca.pem"),method,path:url,
      headers:{...defaults,"content-type":"application/json",...headers},agent:false},res=>{
        let text="";res.on("data",chunk=>text+=chunk);res.on("end",()=>resolve({status:res.statusCode,headers:res.headers,
          body:text?(res.headers["content-type"]?.startsWith("application/json")?JSON.parse(text):text):null}));
      });req.on("error",reject);req.end(raw);
  });
  return {handoff,offer,body,request,store,id,authentication};
}

for(const browser of [true,false]){
  test(`optional Victoria authentication uses ${browser?"browser session/CSRF":"private service credential"} boundary`,async t=>{
    const f=await fixture(t,browser,true);
    const offer=f.offer || await f.handoff.create({sessionId:f.id,pairingId});
    const receipt=await f.handoff.redeem({token:offer.token,pairingId});
    const admission={pairing_id:pairingId,session_commitment:receipt.session_commitment,challenge_commitment:"a".repeat(64),
      proof_id:crypto.randomUUID(),registration_revision:1,valid_until:Date.now()+240000};
    const url=browser?"/api/agent/shared-identity/auth-begin":"/internal/shared-identity/v1/victoria-auth-admission";
    const raw=JSON.stringify(browser?{pairing_id:pairingId}:{version:1,admission});
    if(browser)await f.authentication.admit(admission);
    for(const headers of browser?[{cookie:""},{origin:"https://evil.test"},{"x-csrf-token":"wrong"}]:[{authorization:"Bearer bad"},{cookie:"browser"},{origin:"https://evil.test"}]){
      assert.ok([401,403].includes((await f.request({url,raw,headers})).status));
    }
    const result=await f.request({url,raw});
    assert.equal(result.status,200);
    assert.equal(browser?result.body.status:result.body.admission.status,browser?"form":"authentication_required");
    assert.equal(JSON.stringify(result.body).includes(receipt.session_commitment),false);
  });
}

test("Victoria setup assets require explicit authentication composition and the provisioned host",async t=>{
  const enabled=await fixture(t,true,true),disabled=await fixture(t,true,false);
  const page=await enabled.request({method:"GET",url:"/",raw:""});
  assert.equal(page.status,200);assert.match(page.body,/<title>Connect Victoria/);
  assert.match(page.headers["content-security-policy"],/frame-ancestors 'none'/);
  assert.equal(page.headers["cache-control"],"no-store");
  assert.equal((await disabled.request({method:"GET",url:"/",raw:""})).status,404);
  assert.equal((await enabled.request({method:"GET",url:"/",raw:"",headers:{host:"wrong.test"}})).status,403);
  assert.equal((await enabled.request({method:"GET",url:"/link-setup-config",raw:""})).body.ha_origin,"https://ha.test");
});

test("private TLS ingress redeems the actual Victoria handoff once",async t=>{
  const f=await fixture(t),response=await f.request();
  assert.equal(response.status,200);assert.equal(response.headers["cache-control"],"no-store");
  assert.equal(response.body.handoff.issuer_id,"home-assistant:victoria");
  assert.equal(response.body.handoff.pairing_id,pairingId);
  assert.equal(JSON.stringify(response.body).includes("owner"),false);
  assert.equal((await f.request()).status,503);
});

for(const [mode,status] of [["credential",401],["cookie",403],["origin",403],["identity",400],["duplicate_auth",401],
  ["duplicate_json",422],["oversized",413],["extra",422],["query",404],["encoding",400]]){
  test(`private TLS ingress rejects ${mode} without consuming offer`,async t=>{
    const f=await fixture(t),options={headers:{}};
    if(mode==="credential")options.headers.authorization="Bearer bad";
    if(mode==="cookie" || mode==="origin")options.headers[mode]="browser";
    if(mode==="identity")options.headers["x-authenticated-user"]="owner";
    if(mode==="duplicate_auth")options.headers.authorization=[`Bearer ${"e".repeat(64)}`,`Bearer ${"e".repeat(64)}`];
    if(mode==="duplicate_json")options.raw=JSON.stringify(f.body).replace('"version":1','"version":1,"version":1');
    if(mode==="oversized")options.raw="x".repeat(1025);
    if(mode==="extra")options.raw=JSON.stringify({...f.body,issuer_id:"home-assistant:echo"});
    if(mode==="query")options.url=VICTORIA_HANDOFF_PATH+"?url=https://other.test";
    if(mode==="encoding")options.headers["content-encoding"]="gzip";
    assert.equal((await f.request(options)).status,status);
    assert.equal((await f.handoff.redeem({token:f.offer.token,pairingId})).site_id,"victoria");
  });
}

test("private ingress has no default provisioning",()=>{
  assert.throws(()=>createVictoriaHandoffIngress(),/configuration/);
});

test("Victoria browser creation authenticates cookie and consent before private redemption",async t=>{
  const f=await fixture(t,true),response=await f.request();
  assert.equal(response.status,200);assert.equal(response.headers["referrer-policy"],"no-referrer");
  assert.deepEqual(Object.keys(response.body.offer).sort(),["expires_at","pairing_id","token"]);
  assert.equal((await f.handoff.redeem({token:response.body.offer.token,pairingId})).site_id,"victoria");
});

test("Victoria TLS listener connects browser OAuth to session bootstrap and handoff",async t=>{
  const f=await fixture(t,true);
  const start=await f.request({url:"/api/agent/auth/start",raw:""});
  assert.equal(start.status,200);
  const authorization=new URL(start.body.authorize_url);
  assert.equal(authorization.origin,"https://ha.test");
  assert.equal(authorization.searchParams.get("redirect_uri"),"https://victoria.test/callback");
  const nonce=start.headers["set-cookie"][0].split(";")[0];
  const callback=await f.request({url:`/callback?state=${authorization.searchParams.get("state")}&code=${"b".repeat(32)}`,
    method:"GET",raw:"",headers:{cookie:nonce}});
  assert.equal(callback.status,303);assert.equal(callback.headers.location,"/");
  const cookie=callback.headers["set-cookie"].find(v=>v.startsWith(`${COOKIE_NAME}=`)).split(";")[0];
  const session=await f.request({url:"/api/agent/auth/session",method:"GET",raw:"",headers:{cookie}});
  assert.equal(session.status,200);assert.equal(session.body.authenticated,true);
  const offer=await f.request({headers:{cookie,"x-csrf-token":session.body.csrf_token}});
  assert.equal(offer.status,200);
  assert.equal((await f.handoff.redeem({token:offer.body.offer.token,pairingId})).site_id,"victoria");
});

for(const [mode,status] of [["origin",403],["host",403],["cookie",401],["duplicate_cookie",401],["csrf",403],
  ["identity",400],["bearer",400],["consent",422],["extra",422],["duplicate_json",422],["private_route",404]]){
  test(`Victoria browser rejects ${mode} without minting an offer`,async t=>{
    const f=await fixture(t,true),options={headers:{}};
    if(mode==="origin")options.headers.origin="https://echo.test";
    if(mode==="host")options.headers.host="echo.test";
    if(mode==="cookie")options.headers.cookie=`${COOKIE_NAME}=missing`;
    if(mode==="duplicate_cookie")options.headers.cookie=`${COOKIE_NAME}=${f.id}; ${COOKIE_NAME}=${f.id}`;
    if(mode==="csrf")options.headers["x-csrf-token"]="wrong";
    if(mode==="identity")options.headers["x-authenticated-user"]="owner";
    if(mode==="bearer")options.headers.authorization=`Bearer ${"e".repeat(64)}`;
    if(mode==="consent")options.raw=JSON.stringify({...f.body,consent:false});
    if(mode==="extra")options.raw=JSON.stringify({...f.body,session_id:f.id});
    if(mode==="duplicate_json")options.raw=JSON.stringify(f.body).replace('"version":1','"version":1,"version":1');
    if(mode==="private_route")options.url=VICTORIA_HANDOFF_PATH;
    assert.equal((await f.request(options)).status,status);
    assert.equal((await f.request()).status,200);
  });
}
