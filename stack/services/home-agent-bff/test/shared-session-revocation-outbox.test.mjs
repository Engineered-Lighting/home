import assert from "node:assert/strict";
import { test } from "node:test";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { SessionStore } from "../src/bff.mjs";
import { SharedSessionRevocationClient } from "../src/shared-session-revocation-client.mjs";

function fixture(t) {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),"shared-session-outbox-"));
  const dbPath=path.join(dir,"sessions.sqlite"), key=crypto.randomBytes(32);
  let clock=Date.now(), fail=false, store, onDelivery;
  const requests=[];
  const client=new SharedSessionRevocationClient({endpoint:"https://core.test/internal/shared-identity/v1/session-revocations",
    issuerId:"home-assistant:echo",credential:"a".repeat(64),now:()=>clock,fetchImpl:async (_url,init)=>{
      const value=JSON.parse(init.body).revocation; requests.push(value);
      await onDelivery?.();
      if(fail) throw new Error("transport lost");
      return new Response(JSON.stringify({version:1,revocation:{...value,issuer_id:"home-assistant:echo",revoked_at:new Date(clock).toISOString()}}),{headers:{"content-type":"application/json"}});
    }});
  const config={sessionDbPath:dbPath,sessionEncryptionKey:crypto.randomBytes(32),allowedOrigins:new Set(["https://echo.test"]),
    idleTtlMs:60_000,absoluteTtlMs:300_000,now:()=>clock,
    sharedSessionRevocation:{client,commitmentKey:key},haUrl:"https://ha.test"};
  const open=()=>store=new SessionStore(config);
  open();
  const db=new DatabaseSync(dbPath);
  t.after(()=>{store.close();db.close();fs.rmSync(dir,{recursive:true,force:true});});
  const login=()=>{const id=store.retainLoginTokens({accessToken:Buffer.from("access"),refreshToken:Buffer.from("refresh"),expiresIn:300});
    store.completeLogin(id,{userId:"owner",isActive:true,haIssuerId:"home-assistant:echo",siteId:"echo"});return id;};
  return {config,key,db,requests,open,login,get store(){return store;},tick:(n=2000)=>clock+=n,setFail:v=>fail=v,
    pauseDelivery:callback=>onDelivery=callback,
    cleanup:()=>store.cleanupExpired(config,async()=>new Response("",{status:200}))};
}

test("logout retains a Core tombstone after HA cleanup and retries exact ID across restart",async t=>{
  const f=fixture(t),id=f.login();
  const context=f.store.linkingContext(id,f.key);
  const original=f.db.prepare("SELECT * FROM bff_shared_revocation").get();
  assert.equal(original.state,"armed");assert.equal(original.session_commitment,context.sessionCommitment);
  f.setFail(true);
  assert.equal(await f.store.revoke(f.config,id,f.store.getForRevocation(id),async()=>new Response("",{status:200})),false);
  assert.equal(f.db.prepare("SELECT count(*) n FROM bff_session").get().n,0);
  assert.equal(f.db.prepare("SELECT state FROM bff_shared_revocation").get().state,"pending");
  await f.cleanup();assert.equal(f.requests.length,1);
  f.store.close();f.open();f.tick();f.setFail(false);await f.cleanup();
  assert.deepEqual(f.requests[1],f.requests[0]);
  assert.equal(f.requests[1].revocation_id,original.revocation_id);
  assert.equal(f.db.prepare("SELECT state FROM bff_shared_revocation").get().state,"delivered");
  await f.cleanup();assert.equal(f.requests.length,2);
});

test("restart keeps linked sessions linked; restored unlinked sessions must sign in again to link",async t=>{
  // Owner decision: a BFF restart no longer ends linked sessions.
  const f=fixture(t),linked=f.login(),ordinary=f.login();
  const before=f.store.linkingContext(linked,f.key);
  f.store.close();f.open();
  assert.ok(f.store.get(linked));assert.ok(f.store.get(ordinary));
  assert.equal(f.store.linkingContext(linked,f.key).sessionCommitment,before.sessionCommitment);
  assert.equal(f.store.linkingContext(ordinary,f.key),null);
  assert.equal(f.db.prepare("SELECT state FROM bff_shared_revocation").get().state,"armed");
  await f.cleanup();assert.equal(f.requests.length,0);
});

test("omitted or changed commitment binding rejects reopen without consuming queue",t=>{
  const f=fixture(t),id=f.login();f.store.linkingContext(id,f.key);f.store.close();
  for(const sharedSessionRevocation of [undefined,{...f.config.sharedSessionRevocation,commitmentKey:crypto.randomBytes(32)}]) {
    assert.throws(()=>new SessionStore({...f.config,sharedSessionRevocation}),/revocation/);
    assert.equal(f.db.prepare("SELECT state FROM bff_shared_revocation").get().state,"armed");
  }
  f.open();
});

test("a logout whose storage failed is reported, closes authority now, and is not replayed after restart",async t=>{
  const f=fixture(t),id=f.login();f.store.linkingContext(id,f.key);
  f.db.exec("CREATE TRIGGER fail_shared_revocation BEFORE UPDATE ON bff_shared_revocation BEGIN SELECT RAISE(ABORT,'storage failure'); END");
  assert.equal(await f.store.revoke(f.config,id,f.store.getForRevocation(id),async()=>new Response("",{status:200})),false);
  assert.equal(f.store.get(id),null);
  assert.equal(f.db.prepare("SELECT count(*) n FROM bff_session").get().n,1);
  f.db.exec("DROP TRIGGER fail_shared_revocation");f.store.close();f.open();
  // Owner-accepted trade-off: nothing durable recorded the failed logout, so
  // the persisted session is restored by a restart like any other.
  assert.ok(f.store.get(id));await f.cleanup();assert.equal(f.requests.length,0);
});

test("arming failure or wrong key prevents commitment disclosure",t=>{
  const f=fixture(t),id=f.login();
  assert.throws(()=>f.store.linkingContext(id,crypto.randomBytes(32)),/key changed/);
  f.db.exec("CREATE TRIGGER fail_arm BEFORE INSERT ON bff_shared_revocation BEGIN SELECT RAISE(ABORT,'storage full'); END");
  assert.throws(()=>f.store.linkingContext(id,f.key),/storage full/);
  assert.equal(f.db.prepare("SELECT count(*) n FROM bff_shared_revocation").get().n,0);
});

test("outbox capacity rejects new linking without dropping retained revocations",t=>{
  const f=fixture(t);
  const insert=f.db.prepare("INSERT INTO bff_shared_revocation(session_id,session_commitment,revocation_id,state) VALUES(?,?,?,'pending')");
  f.db.exec("BEGIN");for(let i=0;i<1024;i++)insert.run(`old-${i}`,i.toString(16).padStart(64,"0"),crypto.randomUUID());f.db.exec("COMMIT");
  assert.throws(()=>f.store.linkingContext(f.login(),f.key),/storage full/);
  assert.equal(f.db.prepare("SELECT count(*) n FROM bff_shared_revocation").get().n,1024);
});

test("lost local acknowledgement retries the same tombstone after Core accepted it",async t=>{
  const f=fixture(t),id=f.login();f.store.linkingContext(id,f.key);
  f.db.exec("CREATE TRIGGER fail_ack BEFORE UPDATE ON bff_shared_revocation WHEN NEW.state='delivered' BEGIN SELECT RAISE(ABORT,'ack storage failed'); END");
  assert.equal(await f.store.revoke(f.config,id,f.store.getForRevocation(id),async()=>new Response("",{status:200})),false);
  assert.equal(f.requests.length,1);assert.equal(f.db.prepare("SELECT state FROM bff_shared_revocation").get().state,"pending");
  f.db.exec("DROP TRIGGER fail_ack");f.tick();await f.cleanup();
  assert.deepEqual(f.requests[1],f.requests[0]);assert.equal(f.db.prepare("SELECT state FROM bff_shared_revocation").get().state,"delivered");
});

test("idle expiry queues and delivers the tracked session without explicit logout",async t=>{
  const f=fixture(t),id=f.login();f.store.linkingContext(id,f.key);f.tick(60001);
  await f.cleanup();assert.equal(f.store.get(id),null);assert.equal(f.requests.length,1);
  assert.equal(f.db.prepare("SELECT state FROM bff_shared_revocation").get().state,"delivered");
});

test("session write failure rolls back the queue transition and closes authority until restart",t=>{
  const f=fixture(t),id=f.login();f.store.linkingContext(id,f.key);
  f.db.exec("CREATE TRIGGER fail_session BEFORE UPDATE ON bff_session BEGIN SELECT RAISE(ABORT,'session storage failed'); END");
  assert.equal(f.store.scheduleRevocation(id,f.store.getForRevocation(id),"logout"),false);
  assert.equal(f.db.prepare("SELECT state FROM bff_shared_revocation").get().state,"armed");
  assert.equal(f.store.get(id),null);f.db.exec("DROP TRIGGER fail_session");
  // Owner-accepted trade-off: the rolled-back transition leaves the session
  // armed, and a restart restores it rather than retiring it.
  f.store.close();f.open();assert.ok(f.store.get(id));
  assert.equal(f.db.prepare("SELECT state FROM bff_shared_revocation").get().state,"armed");
});

test("a second owner cannot retire or overwrite the active process's sessions",t=>{
  const f=fixture(t),id=f.login();f.store.linkingContext(id,f.key);
  assert.throws(()=>new SessionStore(f.config),/locked/);
  assert.ok(f.store.get(id));assert.equal(f.db.prepare("SELECT state FROM bff_shared_revocation").get().state,"armed");
  f.store.close();f.open();assert.ok(f.store.get(id));
  assert.equal(f.db.prepare("SELECT state FROM bff_shared_revocation").get().state,"armed");
});

test("shutdown during delivery leaves a retryable tombstone without touching a closed database",async t=>{
  const f=fixture(t),id=f.login();f.store.linkingContext(id,f.key);
  let release,entered;const started=new Promise(resolve=>entered=resolve);
  const held=new Promise(resolve=>release=resolve);
  f.pauseDelivery(async()=>{entered();await held;});
  f.store.scheduleRevocation(id,f.store.getForRevocation(id),"logout");
  const work=f.cleanup();await started;
  assert.doesNotThrow(()=>f.store.close());await work;
  assert.equal(f.db.prepare("SELECT state FROM bff_shared_revocation").get().state,"pending");
  release();await new Promise(resolve=>setImmediate(resolve));
  f.pauseDelivery(undefined);f.open();f.tick();await f.cleanup();
  assert.deepEqual(f.requests[1],f.requests[0]);assert.equal(f.db.prepare("SELECT state FROM bff_shared_revocation").get().state,"delivered");
});

test("acknowledged orphan bookkeeping is reclaimed without removing pending or live-session rows",t=>{
  const f=fixture(t),live=f.login();f.store.linkingContext(live,f.key);
  f.db.prepare("UPDATE bff_shared_revocation SET state='delivered' WHERE session_id=?").run(live);
  const insert=f.db.prepare("INSERT INTO bff_shared_revocation(session_id,session_commitment,revocation_id,state) VALUES(?,?,?,?)");
  insert.run("orphan","1".repeat(64),crypto.randomUUID(),"delivered");
  insert.run("uncertain","2".repeat(64),crypto.randomUUID(),"pending");
  assert.ok(f.store.linkingContext(f.login(),f.key));
  assert.equal(f.db.prepare("SELECT 1 FROM bff_shared_revocation WHERE session_id='orphan'").get(),undefined);
  assert.ok(f.db.prepare("SELECT 1 FROM bff_shared_revocation WHERE session_id='uncertain'").get());
  assert.ok(f.db.prepare("SELECT 1 FROM bff_shared_revocation WHERE session_id=?").get(live));
});

test("a restored pre-link snapshot cannot expose the old session commitment",t=>{
  const f=fixture(t),id=f.login();
  const backup=path.join(path.dirname(f.config.sessionDbPath),"pre-link.sqlite");
  f.db.prepare("VACUUM INTO ?").run(backup);
  const original=f.store.linkingContext(id,f.key);assert.ok(original);
  const restored=new SessionStore({...f.config,sessionDbPath:backup});
  try {
    assert.ok(restored.get(id)); // Existing HA session behavior stays independent.
    assert.equal(restored.linkingContext(id,f.key),null);
    const fresh=restored.retainLoginTokens({accessToken:Buffer.from("new"),refreshToken:Buffer.from("new-refresh"),expiresIn:300});
    restored.completeLogin(fresh,{userId:"owner",isActive:true,haIssuerId:"home-assistant:echo",siteId:"echo"});
    assert.notEqual(restored.linkingContext(fresh,f.key).sessionCommitment,original.sessionCommitment);
  } finally {restored.close();}
});
