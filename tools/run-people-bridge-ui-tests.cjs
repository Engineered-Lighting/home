"use strict";
// People bridge: the real Home Agent household client from home-people.jsx
// (fetchAgentHousehold, writeAgentViaBridge, createAgentPerson,
// recordAgentRelationship), run in a Home page against the real Agent origin
// server serving the real people-bridge.{html,js}, with a mocked BFF.
//
//   NODE_PATH=<repo>/node_modules node tools/run-people-bridge-ui-tests.cjs [mode]
//
// Reads: household + relationships only, only for a provisioned Home origin.
// Writes (owner decision, 2026-09-29): only POST household-person and POST
// partner-attestation, with the session's CSRF token in X-CSRF-Token, a body
// rebuilt from Core's request models, one POST per request, and a reply that
// never carries the CSRF token. Everything else is refused before any POST.
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),http=require("node:http");
const {chromium}=require("playwright");
const root=path.resolve(__dirname,"..");
const people=fs.readFileSync(path.join(root,"app/src/home-people.jsx"),"utf8");
const START_MARKER="// The agent authority speaks predicates";
const END_MARKER="// ── end of the Home Agent household client ──";
const start=people.indexOf(START_MARKER);
const end=people.indexOf(END_MARKER,start);
assert.ok(start>0 && end>start,"household client block not found in home-people.jsx");
const client=people.slice(start,end).replace("const PEOPLE_BRIDGE_TIMEOUT_MS = 15000;","const PEOPLE_BRIDGE_TIMEOUT_MS = 2500;");
assert.ok(!/<[A-Za-z]/.test(client.replace(/[<>]=?|=>/g,"")),"extracted block must be plain JavaScript");

const CSRF="private-csrf-value-7f3a";
const SELF="0190a0b0-0000-7000-8000-000000000001",ANA="0190a0b0-0000-7000-8000-000000000002";
const HOUSEHOLD={people:[{person_id:"p-self",display_name:"Marcelo",pronouns:"he/him",is_self:true},{person_id:"p-2",display_name:"Partner",pronouns:null,is_self:false},{person_id:"p-3",display_name:"Friend",pronouns:null,is_self:false}]};
// p-2 and p-3 are friends with each other; only p-2 has an edge to the
// account holder, so only p-2 may be classed from edges.
const RELATIONSHIPS={relationships:[
  {fact_id:"f-1",subject_person_id:"p-self",object_person_id:"p-2",predicate:"partner_of"},
  {fact_id:"f-2",subject_person_id:"p-2",object_person_id:"p-3",predicate:"friend_of"},
]};
const READ_MODES=["bridge-ok","relationships-refused","signed-out","household-error","wrong-origin","foreign-embedder","same-origin-works"];
const WRITE_MODES=["write-person","write-relationship","write-refused","write-core-error","write-signed-out","write-wrong-origin"];
const MODES=[...READ_MODES,...WRITE_MODES];

const V7=/^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const V4=/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const CEREMONY="0190a0b0-1111-7222-8333-444455556666",NONCE="11111111-2222-4333-8444-555566667777";
// Each is sent straight to the bridge, bypassing the Home helpers, and must
// be refused with no POST.
const REFUSED=[
  ["unknown operation",{operation:"delete_person",body:{ceremony_id:CEREMONY,display_name:"X"}}],
  ["operation named like a prototype key",{operation:"constructor",body:{}}],
  ["extra body key",{operation:"add_person",body:{ceremony_id:CEREMONY,display_name:"X",status:"erased"}}],
  ["person_id supplied",{operation:"add_person",body:{ceremony_id:CEREMONY,display_name:"X",person_id:ANA}}],
  ["empty display name",{operation:"add_person",body:{ceremony_id:CEREMONY,display_name:"   "}}],
  ["long display name",{operation:"add_person",body:{ceremony_id:CEREMONY,display_name:"x".repeat(256)}}],
  ["ceremony not v7",{operation:"add_person",body:{ceremony_id:NONCE,display_name:"X"}}],
  ["bad privacy scope",{operation:"add_person",body:{ceremony_id:CEREMONY,display_name:"X",privacy_scope:"public"}}],
  ["auto_expire without expiry",{operation:"add_person",body:{ceremony_id:CEREMONY,display_name:"X",directive:"auto_expire"}}],
  ["expiry without auto_expire",{operation:"add_person",body:{ceremony_id:CEREMONY,display_name:"X",directive_expires_at:"2030-01-01T00:00:00Z"}}],
  ["naive expiry",{operation:"add_person",body:{ceremony_id:CEREMONY,display_name:"X",directive:"auto_expire",directive_expires_at:"2030-01-01T00:00:00"}}],
  ["unknown predicate",{operation:"add_relationship",body:{ceremony_id:CEREMONY,partner_person_id:ANA,attestation_nonce:NONCE,predicate:"enemy_of"}}],
  ["nonce not v4",{operation:"add_relationship",body:{ceremony_id:CEREMONY,partner_person_id:ANA,attestation_nonce:CEREMONY}}],
  ["self edge",{operation:"add_relationship",body:{ceremony_id:CEREMONY,partner_person_id:ANA,attestation_nonce:NONCE,subject_person_id:ANA}}],
  ["partner not a uuid",{operation:"add_relationship",body:{ceremony_id:CEREMONY,partner_person_id:"../people",attestation_nonce:NONCE}}],
  ["authority claimed",{operation:"add_relationship",body:{ceremony_id:CEREMONY,partner_person_id:ANA,attestation_nonce:NONCE,authority:"explicit_subject"}}],
  ["body is an array",{operation:"add_person",body:[CEREMONY,"X"]}],
];

function served(originServer,pathname) {
  return new Promise((resolve,reject)=>{
    http.get({hostname:"127.0.0.1",port:originServer.address().port,path:pathname,headers:{Host:"agent.test"}},res=>{
      const chunks=[];res.on("data",c=>chunks.push(c));res.on("end",()=>resolve({status:res.statusCode,headers:res.headers,body:Buffer.concat(chunks)}));res.on("error",reject);
    }).on("error",reject);
  });
}

const PAGE_SCRIPT=`window.HG_AGENT_ORIGIN='https://agent.test';
window.__messages=[];
window.addEventListener("message",e=>{if(e.origin==="https://agent.test") window.__messages.push(JSON.stringify(e.data));});
${client}
const settle=p=>p.then(r=>({ok:r}),e=>({error:e.message,status:e.status??null}));
window.run=()=>settle(fetchAgentHousehold());
window.addPerson=()=>settle(createAgentPerson({display_name:"  Nova  ",pronouns:"she/her"}));
window.addRelationship=()=>settle(recordAgentRelationship({subjectId:"${ANA}",predicate:"parent_of",partnerId:"${SELF}"}));
window.addSelfRelationship=()=>settle(recordAgentRelationship({predicate:"friend_of",partnerId:"${ANA}"}));
window.raw=(message)=>settle(callPeopleBridge("https://agent.test",message,"home.people.write_result",{timeoutMs:2500,timeoutError:"raw_timeout"}));`;

async function scenario(browser,originServer,mode) {
  const home=mode==="foreign-embedder" ? "https://evil.test" : "https://home.test";
  const context=await browser.newContext(),agentCalls=[],errors=[];
  context.on("page",page=>page.on("pageerror",error=>errors.push(error.message)));
  await context.route("**/*",async route=>{
    const request=route.request(),url=new URL(request.url());
    const json=(value,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(value)});
    assert.ok([home,"https://agent.test"].includes(url.origin),url.href);
    if(url.origin===home) {
      if(request.method()!=="GET") throw new Error("Home page must not write same-origin: "+url.pathname);
      if(url.pathname==="/api/agent/v1/household") return mode==="same-origin-works" ? json(HOUSEHOLD) : json({error:"authentication_required"},401);
      if(url.pathname==="/api/agent/v1/relationships") return mode==="same-origin-works" ? json(RELATIONSHIPS) : json({error:"authentication_required"},401);
      return route.fulfill({contentType:"text/html",body:`<!doctype html><meta charset="utf-8"><body><script>${PAGE_SCRIPT}</script></body>`});
    }
    if(url.pathname.startsWith("/home-agent/")) {
      const name=url.pathname.split("/").at(-1);
      assert.ok(["people-bridge.html","people-bridge.js"].includes(name),name);
      const response=await served(originServer,url.pathname);assert.equal(response.status,200);
      return route.fulfill(response);
    }
    const headers=request.headers();
    agentCalls.push({method:request.method(),path:url.pathname,csrf:headers["x-csrf-token"]||null,
      contentType:headers["content-type"]||null,body:request.postData()});
    if(url.pathname==="/api/agent/auth/session") {
      if(mode==="signed-out" || mode==="write-signed-out") return json({error:"authentication_required"},401);
      return json({authenticated:true,user_id:"owner",csrf_token:CSRF,personal_memory_enabled:true,
        personal_memory_home_origins:[["wrong-origin","write-wrong-origin"].includes(mode) ? "https://another-home.test" : "https://home.test"]});
    }
    if(request.method()==="GET" && url.pathname==="/api/agent/v1/household") return mode==="household-error" ? json({error:"boom"},500) : json(HOUSEHOLD);
    if(request.method()==="GET" && url.pathname==="/api/agent/v1/relationships") return mode==="relationships-refused" ? json({error:"forbidden"},403) : json(RELATIONSHIPS);
    if(request.method()==="POST" && url.pathname==="/api/agent/v1/household-person") {
      if(mode==="write-core-error") return json({error:{code:"capability_disabled",message:"adding a person is not deployed"},csrf_token:CSRF},503);
      return json({person_id:ANA,display_name:"Nova",privacy_scope:"household",csrf_token:CSRF},201);
    }
    if(request.method()==="POST" && url.pathname==="/api/agent/v1/partner-attestation") {
      return json({receipt_id:NONCE,partner_person_id:SELF,subject_person_id:ANA,predicate:"parent_of",document_digest:"a".repeat(64)},201);
    }
    return json({error:"unexpected"},599);
  });
  try {
    const page=await context.newPage();await page.goto(home+"/");
    const posts=()=>agentCalls.filter(c=>c.method==="POST");
    if(READ_MODES.includes(mode)) {
      const result=await page.evaluate(()=>window.run());
      if(["bridge-ok","relationships-refused","same-origin-works"].includes(mode)) {
        assert.ok(result.ok,JSON.stringify(result));
        assert.deepEqual(result.ok.identities.map(p=>[p.uuid,p.display_name,p.source]),[["p-self","Marcelo","agent_authority"],["p-2","Partner","agent_authority"],["p-3","Friend","agent_authority"]]);
        assert.equal(result.ok.edges.length,mode==="relationships-refused" ? 0 : 2);
        if(mode!=="relationships-refused") {
          assert.deepEqual([result.ok.edges[0].from_uuid,result.ok.edges[0].to_uuid],["p-self","p-2"]);
          assert.deepEqual(result.ok.identities.map(p=>p.relationship_type),["me","partner","unknown"],
            "only edges to the account holder classify a person");
        }
        assert.deepEqual(result.ok.identities[1].aliases,[{kind:"frigate_name",alias:"partner"}]);
      } else if(mode==="signed-out") {
        assert.deepEqual(result,{error:"agent_household_401",status:401});
        assert.ok(!agentCalls.some(c=>c.path.startsWith("/api/agent/v1/")),"no household read without a session");
      } else if(mode==="household-error") {
        assert.deepEqual(result,{error:"agent_household_500",status:500});
      } else {
        assert.deepEqual(result,{error:"agent_household_bridge_timeout",status:null});
        assert.ok(!agentCalls.some(c=>c.path.startsWith("/api/agent/v1/")),"no household read for an unprovisioned or foreign Home");
        if(mode==="foreign-embedder") assert.equal(agentCalls.length,0,"frame-ancestors refuses the embed before any Agent call");
      }
      if(mode==="same-origin-works") assert.equal(agentCalls.length,0,"no bridge when the same-origin read works");
      assert.ok(agentCalls.every(c=>c.method==="GET"),"a read never writes");
    } else if(mode==="write-person") {
      const result=await page.evaluate(()=>window.addPerson());
      assert.deepEqual(result,{ok:{person_id:ANA,display_name:"Nova",privacy_scope:"household"}},"only the named result fields come back");
      assert.equal(posts().length,1,"exactly one POST despite the repeated request");
      const [post]=posts();
      assert.equal(post.path,"/api/agent/v1/household-person");
      assert.equal(post.csrf,CSRF,"the session CSRF token is sent");
      assert.equal(post.contentType,"application/json");
      const body=JSON.parse(post.body);
      assert.deepEqual(Object.keys(body).sort(),["ceremony_id","display_name","privacy_scope","pronouns"]);
      assert.match(body.ceremony_id,V7);
      assert.ok(Math.abs(parseInt(body.ceremony_id.replace(/-/g,"").slice(0,12),16)-Date.now())<60000,"ceremony_id carries the current time");
      assert.deepEqual([body.display_name,body.pronouns,body.privacy_scope],["Nova","she/her","household"]);
      assert.ok(agentCalls.filter(c=>c.method==="GET").every(c=>c.path==="/api/agent/auth/session"),"a write reads nothing but the session");
    } else if(mode==="write-relationship") {
      let result=await page.evaluate(()=>window.addRelationship());
      assert.ok(result.ok && result.ok.predicate==="parent_of",JSON.stringify(result));
      result=await page.evaluate(()=>window.addSelfRelationship());
      assert.ok(result.ok,JSON.stringify(result));
      assert.equal(posts().length,2);
      for(const post of posts()) {
        assert.equal(post.path,"/api/agent/v1/partner-attestation");
        assert.equal(post.csrf,CSRF);
        assert.equal(post.contentType,"application/json");
      }
      const [third,own]=posts().map(p=>JSON.parse(p.body));
      assert.deepEqual(Object.keys(third).sort(),["attestation_nonce","ceremony_id","partner_person_id","predicate","subject_person_id"]);
      assert.match(third.ceremony_id,V7);assert.match(third.attestation_nonce,V4);
      assert.deepEqual([third.subject_person_id,third.predicate,third.partner_person_id],[ANA,"parent_of",SELF]);
      assert.deepEqual(Object.keys(own).sort(),["attestation_nonce","ceremony_id","partner_person_id","predicate"],"the account holder is not named");
    } else if(mode==="write-refused") {
      for(const [label,message] of REFUSED) {
        const result=await page.evaluate(m=>window.raw({type:"home.people.write",...m}),message);
        assert.equal(result.ok?.status,"refused",`${label}: ${JSON.stringify(result)}`);
        assert.equal(result.ok?.http_status,0,label);
      }
      // A message with any extra top-level key is not a request at all.
      const extra=await page.evaluate(()=>window.raw({type:"home.people.write",operation:"add_person",body:{},path:"/api/agent/v1/snapshot"}));
      assert.deepEqual(extra,{error:"raw_timeout",status:null});
      assert.equal(posts().length,0,"nothing refused ever reaches the BFF");
      assert.ok(agentCalls.every(c=>c.path==="/api/agent/auth/session"),"a refused write reads nothing but the session");
    } else if(mode==="write-core-error") {
      const result=await page.evaluate(()=>window.addPerson());
      assert.equal(result.status,503);
      assert.match(result.error,/adding a person is not deployed \(HTTP 503\)/);
      assert.equal(posts().length,1);
    } else if(mode==="write-signed-out") {
      const result=await page.evaluate(()=>window.addPerson());
      assert.deepEqual(result,{error:"sign in to the Home Agent to add people",status:401});
      assert.equal(posts().length,0,"no write without a session");
    } else if(mode==="write-wrong-origin") {
      const result=await page.evaluate(()=>window.addPerson());
      assert.deepEqual(result,{error:"agent_write_bridge_timeout",status:null});
      assert.equal(posts().length,0,"an unprovisioned Home origin cannot write");
    }
    const frames=await page.evaluate(()=>document.querySelectorAll("iframe").length);
    assert.equal(frames,0,"the bridge frame is always removed");
    const messages=await page.evaluate(()=>window.__messages);
    assert.ok(messages.every(m=>!m.includes(CSRF)),"the CSRF token never reaches the parent");
    assert.ok(agentCalls.every(c=>c.method==="GET" || ["/api/agent/v1/household-person","/api/agent/v1/partner-attestation"].includes(c.path)),
      "the only writes are the two owner-approved routes");
    assert.deepEqual(errors,[]);
    console.log("PASS",mode);
  } catch(error) {
    console.error("FAIL",mode,{agentCalls,errors});
    throw error;
  } finally {await context.close();}
}

(async()=>{
  const {configFromEnv,createAgentOrigin}=await import("../stack/services/home-agent-origin/src/origin.mjs");
  const originServer=createAgentOrigin(configFromEnv({
    HOME_AGENT_WEB_PUBLIC_ORIGIN:"https://agent.test",HOME_AGENT_WEB_BFF_URL:"http://127.0.0.1:1",
    HOME_AGENT_WEB_ASSET_ROOT:path.join(root,"app/src/home-agent"),HOME_AGENT_WEB_HOST:"127.0.0.1",HOME_AGENT_WEB_PORT:"8096",
    HOME_AGENT_WEB_PREFERENCE_HOME_ORIGINS:"https://home.test",
  }));
  await new Promise((resolve,reject)=>{originServer.once("error",reject);originServer.listen(0,"127.0.0.1",resolve);});
  let browser;
  try {
    browser=await chromium.launch({headless:true});
    const selected=MODES.filter(mode=>!process.argv[2] || process.argv[2]===mode);
    for(const mode of selected) await scenario(browser,originServer,mode);
    console.log(`${selected.length} People bridge scenarios passed`);
  } finally {
    await browser?.close();
    await new Promise(resolve=>originServer.close(resolve));
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
