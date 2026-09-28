import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import https from "node:https";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { once } from "node:events";
import { createVictoriaLinkRuntime } from "../src/victoria-link-runtime.mjs";
import { loadVictoriaLinkProvision } from "../src/victoria-link-provision.mjs";
import { startVictoriaLinkServer } from "../victoria-link-server.mjs";
import net from "node:net";

function fixture(t) {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),"victoria-runtime-"));
  t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
  const file=name=>fs.readFileSync(new URL(`../../../../tools/shared-home/tests/fixtures/gateway_client/${name}`,import.meta.url));
  const tls={key:file("server-test-only.key"),cert:file("server.pem")};
  const endpoint=name=>({endpoint:`https://core.test/internal/shared-identity/v1/${name}`,credential:crypto.randomBytes(32).toString("hex")});
  return {browserOrigin:"https://victoria.test",echoOrigins:new Set(["https://echo.test"]),
    haOrigin:"https://victoria-ha.test",clientId:"https://victoria.test/",
    redirectUri:"https://victoria.test/callback",idleTtlMs:600000,absoluteTtlMs:1200000,
    sessionDbPath:path.join(dir,"sessions.sqlite"),proofDbPath:path.join(dir,"proofs.sqlite"),
    revocationDbPath:path.join(dir,"revocations.sqlite"),
    sessionEncryptionKey:crypto.randomBytes(32),journalKey:crypto.randomBytes(32),commitmentKey:crypto.randomBytes(32),
    sessionRevocation:endpoint("session-revocations"),proofIngress:endpoint("auth-proofs"),
    handoffCredential:crypto.randomBytes(32).toString("hex"),browserTls:tls,ingressTls:tls};
}

test("Victoria runtime reopens durable stores and does not start either listener",t=>{
  const provision=fixture(t);
  for(let n=0;n<2;n++) {
    const runtime=createVictoriaLinkRuntime(provision);
    assert.equal(runtime.browserServer.listening,false);
    assert.equal(runtime.ingressServer.listening,false);
    runtime.browserServer.close();runtime.ingressServer.close();runtime.close();runtime.close();
  }
});

test("Victoria runtime rejects reused keys, databases, insecure HA and shared cookie hostnames",t=>{
  const p=fixture(t);
  for(const patch of [{journalKey:p.commitmentKey},{proofDbPath:p.sessionDbPath},
    {haOrigin:"http://victoria-ha.test"},{echoOrigins:new Set(["https://victoria.test:8443"])},
    {browserTls:{key:"invalid",cert:"invalid"}}]) {
    assert.throws(()=>createVictoriaLinkRuntime({...p,...patch}));
  }
  const runtime=createVictoriaLinkRuntime(p);
  runtime.browserServer.close();runtime.ingressServer.close();runtime.close();
});

const request=(server,route)=>new Promise((resolve,reject)=>{
  const req=https.request({hostname:"127.0.0.1",port:server.address().port,path:route,
    rejectUnauthorized:false,headers:{host:"victoria.test"}},res=>{
    res.resume();res.on("end",()=>resolve(res.statusCode));
  });
  req.on("error",reject);req.end();
});

test("assembled browser and coordinator listeners expose only their own routes",async t=>{
  const runtime=createVictoriaLinkRuntime(fixture(t));
  const servers=[runtime.browserServer,runtime.ingressServer];
  try {
    for(const server of servers) {server.listen(0,"127.0.0.1");await once(server,"listening");}
    assert.throws(()=>runtime.close(),/drain_listeners_first/);
    assert.equal(await request(runtime.browserServer,"/"),200);
    assert.equal(await request(runtime.browserServer,"/internal/shared-identity/v1/victoria-handoff"),404);
    assert.equal(await request(runtime.browserServer,"/api/agent/memory"),404);
    assert.equal(await request(runtime.ingressServer,"/"),404);
    assert.equal(await request(runtime.ingressServer,"/api/agent/auth/session"),404);
  } finally {
    for(const server of servers) await new Promise(resolve=>server.close(resolve));
    runtime.close();
  }
});

function mountedProfile(p) {
  const dir=path.dirname(p.sessionDbPath);
  const write=(name,value)=>{const file=path.join(dir,name);fs.writeFileSync(file,value);return file;};
  const value={version:1};
  for(const name of ["browserOrigin","haOrigin","clientId","redirectUri","idleTtlMs","absoluteTtlMs",
    "sessionDbPath","proofDbPath","revocationDbPath"]) value[name]=p[name];
  value.echoOrigins=[...p.echoOrigins];
  for(const name of ["commitmentKey","journalKey","sessionEncryptionKey"])
    value[name+"File"]=write(name,p[name].toString("base64url"));
  for(const name of ["sessionRevocation","proofIngress"])
    value[name]={endpoint:p[name].endpoint,credentialFile:write(name,p[name].credential)};
  value.handoffCredentialFile=write("handoff",p.handoffCredential);
  for(const name of ["browserTls","ingressTls"])
    value[name]={certificateFile:write(name+".pem",p[name].cert),privateKeyFile:write(name+".key",p[name].key)};
  value.browserListener={address:"127.0.0.1",port:18443};
  value.ingressListener={address:"127.0.0.1",port:18444};
  const file=write("profile.json",JSON.stringify(value));
  return {value,file,save:()=>fs.writeFileSync(file,JSON.stringify(value))};
}

test("mounted Victoria profile rejects wildcard listeners and inline transport credentials",t=>{
  const p=fixture(t),{value,file,save}=mountedProfile(p);
  const parsed=loadVictoriaLinkProvision(file);
  const runtime=createVictoriaLinkRuntime(parsed);
  runtime.browserServer.close();runtime.ingressServer.close();runtime.close();
  value.browserListener.address="0.0.0.0";save();
  assert.throws(()=>loadVictoriaLinkProvision(file),/private_listener_required/);
  value.browserListener.address="127.0.0.1";
  value.proofIngress.credential="a".repeat(64);save();
  assert.throws(()=>loadVictoriaLinkProvision(file),/provision_shape_rejected/);
  delete value.proofIngress.credential;save();
  fs.writeFileSync(file,fs.readFileSync(file,"utf8").replace('{"version":1,','{"version":1,"version":1,'));
  assert.throws(()=>loadVictoriaLinkProvision(file),/duplicate_provision_field/);
});

test("failed second listener startup releases the first listener and durable stores",async t=>{
  const p=fixture(t),{value,file,save}=mountedProfile(p);
  const occupied=net.createServer();occupied.listen(0,"127.0.0.1");await once(occupied,"listening");
  const probe=net.createServer();probe.listen(0,"127.0.0.1");await once(probe,"listening");
  value.browserListener.port=probe.address().port;
  value.ingressListener.port=occupied.address().port;save();
  await new Promise(resolve=>probe.close(resolve));
  try {
    await assert.rejects(startVictoriaLinkServer(file),{code:"EADDRINUSE"});
    // A corrected restart must be able to reopen all original journals.
    const runtime=createVictoriaLinkRuntime(loadVictoriaLinkProvision(file));
    runtime.browserServer.close();runtime.ingressServer.close();runtime.close();
    probe.listen(value.browserListener.port,"127.0.0.1");await once(probe,"listening");
  } finally {
    await new Promise(resolve=>occupied.close(resolve));
    await new Promise(resolve=>probe.close(resolve));
  }
});
