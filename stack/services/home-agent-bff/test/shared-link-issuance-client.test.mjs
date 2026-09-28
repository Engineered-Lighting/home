import assert from "node:assert/strict";
import test from "node:test";
import { SharedLinkIssuanceClient } from "../src/shared-link-issuance-client.mjs";
const now=Date.now();
const id=n=>`00000000-0000-0000-0000-${String(n).padStart(12,"0")}`;
const input=()=>({subject:"owner",context:{ceremony_id:id(1),echo_session_commitment:"a".repeat(64),victoria_session_commitment:"b".repeat(64),
  echo_challenge_id:id(2),victoria_challenge_id:id(3),echo_challenge_commitment:"c".repeat(64),victoria_challenge_commitment:"d".repeat(64)}});
const receipt=()=>({ceremony_id:id(1),authorization_generation:1,revision:1,created_at:new Date(now).toISOString(),expires_at:new Date(now+300000).toISOString(),echo_registration_revision:1,victoria_registration_revision:2});
const json=v=>new Response(JSON.stringify({version:1,issuance:v}),{headers:{"content-type":"application/json"}});
const options=fetchImpl=>({endpoint:"https://coordinator.test/internal/shared-identity/v1/link-issuance",credential:"e".repeat(64),now:()=>now,fetchImpl});

test("issuance transport preserves the exact request and uses lookup-only recovery",async()=>{
  const calls=[],value=input(),client=new SharedLinkIssuanceClient(options(async(url,init)=>{calls.push({url,init});return json(receipt());}));
  assert.deepEqual(await client.begin(value),receipt());assert.deepEqual(await client.recover(value),receipt());
  assert(calls[1].url.endsWith("/link-issuance-outcome"));
  for(const {init} of calls){assert.deepEqual(JSON.parse(init.body),{version:1,request:value});assert.equal(init.redirect,"error");assert.equal(init.credentials,"omit");}
});

for(const mutate of [v=>v.ceremony_id=id(9),v=>v.authorization_generation=Number.MAX_SAFE_INTEGER+1,
  v=>v.revision=0,v=>v.principal_id=id(8),v=>v.expires_at=new Date(now+300001).toISOString(),
  v=>{v.created_at=new Date(now-300000).toISOString();v.expires_at=new Date(now).toISOString();}]){
  test("issuance transport rejects substituted or invalid receipt",async()=>{
    const client=new SharedLinkIssuanceClient(options(async()=>{const v=receipt();mutate(v);return json(v);}));
    await assert.rejects(client.begin(input()),/unknown/);
  });
}

test("untrusted owner anchors and colliding challenges reject before transport",async()=>{
  const client=new SharedLinkIssuanceClient(options(()=>assert.fail("must not fetch")));
  for(const mutate of [v=>v.context.principal_id=id(4),v=>v.context.victoria_challenge_id=v.context.echo_challenge_id,
    v=>v.context.victoria_challenge_commitment=v.context.echo_challenge_commitment,v=>v.issuer_id="home-assistant:victoria"]){
    const v=input();mutate(v);await assert.rejects(client.begin(v),/invalid/);
  }
});

test("caller mutation after dispatch cannot change the expected receipt",async()=>{
  let release;const wait=new Promise(resolve=>release=resolve),v=input();
  const client=new SharedLinkIssuanceClient(options(async()=>{await wait;return json(receipt());}));
  const pending=client.begin(v);v.context.ceremony_id=id(9);release();assert.equal((await pending).ceremony_id,id(1));
});

test("cancelled requests remain charged until physical transport settles",async()=>{
  let release;const wait=new Promise(resolve=>release=resolve),controller=new AbortController();let calls=0;
  const client=new SharedLinkIssuanceClient(options(async()=>{calls++;await wait;return json(receipt());}));
  const first=client.begin(input(),{signal:controller.signal}),second=client.recover(input(),{signal:controller.signal});
  controller.abort();await assert.rejects(first,/unknown/);await assert.rejects(second,/unknown/);
  await assert.rejects(client.begin(input()),/unavailable/);assert.equal(calls,2);
  release();await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(await client.recover(input()),receipt());
});

test("unknown delivery does not automatically retry or fall back",async()=>{
  let calls=0;const client=new SharedLinkIssuanceClient(options(async()=>{calls++;throw new Error("private error");}));
  await assert.rejects(client.begin(input()),/^Error: issuance_outcome_unknown$/);assert.equal(calls,1);
});
