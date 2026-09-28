import assert from "node:assert/strict";
import test from "node:test";
import { VictoriaHandoffClient } from "../src/victoria-handoff-client.mjs";

const id = "00000000-0000-0000-0000-000000000001", now = Date.now();
const input = () => ({ pairing_id: id, token: "a".repeat(64) });
const receipt = () => ({ pairing_id: id, issuer_id: "home-assistant:victoria", site_id: "victoria",
  session_commitment: "b".repeat(64), valid_until: now + 59000 });
const json = value => new Response(JSON.stringify({ version: 1, handoff: value }), { headers: { "content-type": "application/json" } });
const options = fetchImpl => ({ endpoint: "https://victoria.test/internal/shared-identity/v1/victoria-handoff",
  credential: "c".repeat(64), fetchImpl, now: () => now, monotonicNow: () => 0 });

const admission=()=>({pairing_id:id,session_commitment:"a".repeat(64),challenge_commitment:"b".repeat(64),
  proof_id:"00000000-0000-0000-0000-000000000002",registration_revision:1,valid_until:now+240000});
const proof=()=>({proof_id:admission().proof_id,subject:"victoria-owner",issuer_id:"home-assistant:victoria",
  session_commitment:admission().session_commitment,challenge_commitment:admission().challenge_commitment,registration_revision:1,
  authenticated_at:new Date(now).toISOString(),issued_at:new Date(now).toISOString(),expires_at:new Date(now+300000).toISOString()});
const envelope=value=>new Response(JSON.stringify(value),{headers:{"content-type":"application/json"}});

test("Victoria authentication admission and proof lookup use fixed private paths",async()=>{
  const calls=[];
  const client=new VictoriaHandoffClient(options(async(url,init)=>{
    calls.push({url,body:JSON.parse(init.body)});
    return envelope(url.endsWith("-admission")?{version:1,admission:{version:1,status:"authentication_required",pairing_id:id}}:{version:1,proof:proof()});
  }));
  await client.admitAuthentication(admission());
  assert.deepEqual(await client.authenticationOutcome(admission()),proof());
  assert.equal(calls[0].url,"https://victoria.test/internal/shared-identity/v1/victoria-auth-admission");
  assert.equal(calls[1].url,"https://victoria.test/internal/shared-identity/v1/victoria-auth-outcome");
  assert.deepEqual(calls[1].body,{version:1,pairing_id:id});
});

for(const change of [p=>p.issuer_id="home-assistant:echo",p=>p.session_commitment="f".repeat(64),
  p=>p.challenge_commitment="f".repeat(64),p=>p.registration_revision=2,p=>p.proof_id=id,
  p=>p.expires_at=new Date(now).toISOString(),p=>p.issued_at=new Date(now+2000).toISOString()]){
  test("Victoria proof lookup rejects substituted or stale authority",async()=>{
    const p=proof();change(p);
    const client=new VictoriaHandoffClient(options(async()=>envelope({version:1,proof:p})));
    await assert.rejects(client.authenticationOutcome(admission()),/outcome_unknown/);
  });
}

test("handoff uses fixed private TLS destination without browser credentials", async () => {
  let call;
  const client = new VictoriaHandoffClient(options(async (url, init) => { call = { url, init }; return json(receipt()); }));
  assert.deepEqual(await client.redeem(input()), receipt());
  assert.deepEqual(JSON.parse(call.init.body), { version: 1, ...input() });
  assert.equal(call.init.redirect, "error"); assert.equal(call.init.credentials, "omit");
  assert.equal(call.url, options().endpoint);
});

for (const mutate of [v => v.site_id = "echo", v => v.issuer_id = "home-assistant:echo",
  v => v.pairing_id = "00000000-0000-0000-0000-000000000002", v => v.subject = "owner",
  v => v.valid_until = now, v => v.valid_until = now + 60001, v => v.session_commitment = "bad"]) {
  test("substituted, expired or widened handoff fails closed", async () => {
    const client = new VictoriaHandoffClient(options(async () => { const v = receipt(); mutate(v); return json(v); }));
    await assert.rejects(client.redeem(input()), /outcome_unknown/);
  });
}

test("unknown exchange is not retried and private errors are hidden", async () => {
  let calls = 0;
  const client = new VictoriaHandoffClient(options(async () => { calls++; throw new Error("private token"); }));
  await assert.rejects(client.redeem(input()), /^Error: handoff_outcome_unknown$/); assert.equal(calls, 1);
});

test("cancellation retains concurrency charge until transport ends", async () => {
  let release; const gate = new Promise(r => release = r), controller = new AbortController();
  const client = new VictoriaHandoffClient(options(async () => { await gate; return json(receipt()); }));
  const one = client.redeem(input(), { signal: controller.signal }), two = client.redeem(input(), { signal: controller.signal });
  controller.abort(); await assert.rejects(one); await assert.rejects(two);
  await assert.rejects(client.redeem(input()), /unavailable/);
  release(); await new Promise(r => setImmediate(r));
  assert.deepEqual(await client.redeem(input()), receipt());
});

test("caller mutation cannot retarget an in-flight exchange", async () => {
  let release; const gate = new Promise(r => release = r), value = input();
  const client = new VictoriaHandoffClient(options(async () => { await gate; return json(receipt()); }));
  const pending = client.redeem(value); value.pairing_id = "different"; release();
  assert.equal((await pending).pairing_id, id);
});

test("clock jump rejects otherwise valid result", async () => {
  let wall = now;
  const client = new VictoriaHandoffClient({ ...options(async () => { wall += 2000; return json(receipt()); }), now: () => wall });
  await assert.rejects(client.redeem(input()), /unknown/);
});
