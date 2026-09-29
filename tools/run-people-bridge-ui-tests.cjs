"use strict";
// People map read-only bridge: the real fetchAgentHousehold code from
// home-people.jsx, run in a Home page against the real Agent origin server.
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),http=require("node:http");
const {chromium}=require("playwright");
const root=path.resolve(__dirname,"..");
const people=fs.readFileSync(path.join(root,"app/src/home-people.jsx"),"utf8");
const start=people.indexOf("// The agent authority speaks predicates");
const end=people.indexOf("  return { identities, edges };\n}",start);
assert.ok(start>0 && end>start,"household client block not found in home-people.jsx");
const client=people.slice(start,end+"  return { identities, edges };\n}".length).replace("const PEOPLE_BRIDGE_TIMEOUT_MS = 15000;","const PEOPLE_BRIDGE_TIMEOUT_MS = 2500;");
assert.ok(!/<[A-Za-z]/.test(client.replace(/[<>]=?|=>/g,"")),"extracted block must be plain JavaScript");

const HOUSEHOLD={people:[{person_id:"p-self",display_name:"Marcelo",pronouns:"he/him",is_self:true},{person_id:"p-2",display_name:"Partner",pronouns:null,is_self:false}]};
const RELATIONSHIPS={relationships:[{fact_id:"f-1",subject_person_id:"p-self",object_person_id:"p-2",predicate:"partner_of"}]};
const MODES=["bridge-ok","relationships-refused","signed-out","household-error","wrong-origin","foreign-embedder","same-origin-works"];

function served(originServer,pathname) {
  return new Promise((resolve,reject)=>{
    http.get({hostname:"127.0.0.1",port:originServer.address().port,path:pathname,headers:{Host:"agent.test"}},res=>{
      const chunks=[];res.on("data",c=>chunks.push(c));res.on("end",()=>resolve({status:res.statusCode,headers:res.headers,body:Buffer.concat(chunks)}));res.on("error",reject);
    }).on("error",reject);
  });
}

async function scenario(browser,originServer,mode) {
  const home=mode==="foreign-embedder" ? "https://evil.test" : "https://home.test";
  const context=await browser.newContext(),agentCalls=[],errors=[];
  context.on("page",page=>page.on("pageerror",error=>errors.push(error.message)));
  await context.route("**/*",async route=>{
    const request=route.request(),url=new URL(request.url());
    const json=(value,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(value)});
    assert.ok([home,"https://agent.test"].includes(url.origin),url.href);
    if(url.origin===home) {
      if(url.pathname==="/api/agent/v1/household") return mode==="same-origin-works" ? json(HOUSEHOLD) : json({error:"authentication_required"},401);
      if(url.pathname==="/api/agent/v1/relationships") return mode==="same-origin-works" ? json(RELATIONSHIPS) : json({error:"authentication_required"},401);
      return route.fulfill({contentType:"text/html",body:`<!doctype html><meta charset="utf-8"><body><script>window.HG_AGENT_ORIGIN='https://agent.test';\n${client}\nwindow.run=()=>fetchAgentHousehold().then(r=>({ok:r}),e=>({error:e.message,status:e.status??null}));</script></body>`});
    }
    if(url.pathname.startsWith("/home-agent/")) {
      const name=url.pathname.split("/").at(-1);
      assert.ok(["people-bridge.html","people-bridge.js"].includes(name),name);
      const response=await served(originServer,url.pathname);assert.equal(response.status,200);
      return route.fulfill(response);
    }
    agentCalls.push({method:request.method(),path:url.pathname});
    if(url.pathname==="/api/agent/auth/session") {
      if(mode==="signed-out") return json({error:"authentication_required"},401);
      return json({authenticated:true,user_id:"owner",csrf_token:"private-csrf",personal_memory_enabled:true,
        personal_memory_home_origins:[mode==="wrong-origin" ? "https://another-home.test" : "https://home.test"]});
    }
    if(url.pathname==="/api/agent/v1/household") return mode==="household-error" ? json({error:"boom"},500) : json(HOUSEHOLD);
    if(url.pathname==="/api/agent/v1/relationships") return mode==="relationships-refused" ? json({error:"forbidden"},403) : json(RELATIONSHIPS);
    throw new Error("unexpected Agent route "+url.pathname);
  });
  try {
    const page=await context.newPage();await page.goto(home+"/");
    const result=await page.evaluate(()=>window.run());
    const frames=await page.evaluate(()=>document.querySelectorAll("iframe").length);
    assert.equal(frames,0,"the bridge frame is always removed");
    if(["bridge-ok","relationships-refused","same-origin-works"].includes(mode)) {
      assert.ok(result.ok,JSON.stringify(result));
      assert.deepEqual(result.ok.identities.map(p=>[p.uuid,p.display_name,p.source]),[["p-self","Marcelo","agent_authority"],["p-2","Partner","agent_authority"]]);
      assert.equal(result.ok.edges.length,mode==="relationships-refused" ? 0 : 1);
      if(mode!=="relationships-refused") assert.deepEqual([result.ok.edges[0].from_uuid,result.ok.edges[0].to_uuid],["p-self","p-2"]);
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
    assert.ok(agentCalls.every(c=>c.method==="GET"),"the bridge only ever reads");
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
    for(const mode of MODES) if(!process.argv[2] || process.argv[2]===mode) await scenario(browser,originServer,mode);
    console.log(`${MODES.length} People bridge scenarios passed`);
  } finally {
    await browser?.close();
    await new Promise(resolve=>originServer.close(resolve));
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
