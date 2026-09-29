"use strict";
// Inline cross-home lighting review: the real Home runner and intent parser in a
// minimal Home chat, framing the real Agent-origin page served by the real
// Origin, against a scripted Agent API. No home or Home Assistant is contacted.
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path");
const {chromium}=require("playwright");
const http=require("node:http");
const root=path.resolve(__dirname,"..");
const VIEWPORTS={desktop:{width:1280,height:800},phone:{width:375,height:812}};
const MODES=["direct","both-homes","partial","unknown","clarify","not-permitted","ask-home","la-view"];
const PROMPTS={"both-homes":"Turn off the lights in both homes","clarify":"Turn off the attic light in Victoria",
  "ask-home":"Turn off the kitchen light","la-view":"Turn off the kitchen light"};
const homePage=(mode,text)=>`<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{margin:0;font:15px system-ui;background:#0b0f0e;color:#eee}main{padding:16px}#ask{min-height:44px}</style>
<main><button id="ask">Ask Home</button><div id="thread"></div></main>
<script>window.HG_WEB_MODE=true;window.HG_AGENT_ORIGIN='https://agent.test';window.events=[];window.current=true;window.handled=null;</script>
<script src="/home-lighting-intent.js"></script><script src="/home-lighting-control.js"></script>
<script>
const thread=document.getElementById('thread');
const addEvent=e=>{events.push(e);const row=document.createElement('div');row.className='event '+e.kind;row.textContent=e.text;thread.appendChild(row);
  if(e.mountReview){const slot=document.createElement('div');row.appendChild(slot);e.mountReview(slot);}};
document.getElementById('ask').onclick=()=>{handled=HomeLightingControl.run(${JSON.stringify(text)},
  {addEvent,viewedHome:${JSON.stringify(mode==="la-view" ? "echo" : "victoria")},isCurrent:()=>current});};
</script>`;

function served(originServer,pathname) {
  return new Promise((resolve,reject)=>{
    http.get({hostname:"127.0.0.1",port:originServer.address().port,path:pathname,headers:{Host:"agent.test"}},res=>{
      const chunks=[];res.on("data",c=>chunks.push(c));
      res.on("end",()=>resolve({status:res.statusCode,headers:res.headers,body:Buffer.concat(chunks)}));
      res.on("error",reject);
    }).on("error",reject);
  });
}

async function scenario(browser,originServer,mode,viewportName) {
  const context=await browser.newContext({viewport:VIEWPORTS[viewportName]}),calls=[],errors=[];
  let review;
  context.on("page",page=>page.on("pageerror",error=>errors.push(error.message)));
  await context.route("**/*",async route=>{
    const request=route.request(),url=new URL(request.url());
    const reply=(value,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(value)});
    assert.ok(["https://home.test","https://agent.test"].includes(url.origin),url.href);
    if(url.origin==="https://home.test") {
      const file={"/home-lighting-intent.js":"home-lighting-intent.js","/home-lighting-control.js":"home-lighting-control.js"}[url.pathname];
      if(file) return route.fulfill({contentType:"text/javascript",body:fs.readFileSync(path.join(root,"app/src",file),"utf8")});
      return route.fulfill({contentType:"text/html",body:homePage(mode,PROMPTS[mode] || "Turn off the kitchen light in Victoria.")});
    }
    if(url.pathname.startsWith("/home-agent/")) {
      const name=url.pathname.split("/").at(-1);
      assert.ok(["lighting-review.html","preference-review.css","lighting-review.js","api.js"].includes(name),name);
      const response=await served(originServer,url.pathname);
      assert.equal(response.status,200);
      return route.fulfill(response);
    }
    const operation=url.pathname.split("/").at(-1),body=request.postDataJSON();calls.push({operation,body});
    if(operation==="session") return reply({authenticated:true,user_id:"owner",csrf_token:"private-csrf",
      authority:{version:1,site_id:"echo",ha_issuer_id:"home-assistant:echo"},lighting_enabled:true,
      personal_memory_enabled:true,personal_memory_home_origins:["https://home.test"]});
    assert.equal(request.headers()["x-csrf-token"],"private-csrf");
    if(operation==="propose") {
      if(mode==="not-permitted") return reply({error:"lighting_not_permitted"},403);
      if(mode==="clarify") return reply({version:1,result:{version:1,status:"clarify",clarification:{version:1,
        reason:"unknown_light",site_id:"victoria",target:"attic",candidates:["Kitchen","Porch"]}}});
      const sites=body.sites;
      review={version:1,operation_id:body.operation_id,expires_at:new Date(Date.now()+60000).toISOString(),
        reviewed_digest:"d".repeat(64),operations:sites.map(site=>({site_id:site,name:"Kitchen",operation:body.operation,brightness:body.brightness}))};
      return reply({version:1,result:{version:1,status:"review",review}});
    }
    const summary=status=>({version:1,operation_id:review.operation_id,status,
      results:review.operations.map((op,i)=>({...op,status:status==="partial" && i===0 ? "not_sent" : status==="unknown" ? "unknown" : "succeeded"}))});
    if(operation==="confirm") {
      assert.deepEqual(body,{version:1,operation_id:review.operation_id,reviewed_digest:review.reviewed_digest});
      if(mode==="unknown") return reply({error:"lighting_outcome_unknown"},503);
      return reply({version:1,result:summary(mode==="partial" ? "partial" : "done")});
    }
    if(operation==="outcome") {
      assert.deepEqual(body,{version:1,operation_id:review.operation_id});
      return reply({version:1,result:summary("done")});
    }
    throw new Error("Unexpected route "+url.pathname);
  });
  const count=operation=>calls.filter(c=>c.operation===operation).length;
  try {
    const page=await context.newPage();await page.goto("https://home.test/");
    await page.getByRole("button",{name:"Ask Home"}).click();
    assert.equal(context.pages().length,1,"no popup window may open");
    const frame=page.frameLocator("iframe[title='Lighting review']");
    const check=frame.getByRole("button",{name:"Check outcome"});
    const replied=prefix=>page.waitForFunction(p=>events.some(e=>e.kind==="home" && e.text.startsWith(p)),prefix);
    const frameGone=()=>page.waitForFunction(()=>!document.querySelector("iframe"));
    if(mode==="la-view") {
      // Unnamed commands in the Los Angeles view stay on the existing path.
      assert.equal(await page.evaluate(()=>handled),false);
      assert.equal(await page.locator("iframe").count(),0);
    } else if(mode==="ask-home") {
      await replied("Which home do you mean");
      assert.equal(await page.locator("iframe").count(),0);
    } else if(mode==="clarify") {
      await replied("I couldn't find \"attic\" in Victoria. Lights there: Kitchen, Porch.");
      await frameGone();
    } else if(mode==="not-permitted") {
      await replied("Lighting control between homes is not allowed yet.");
      await frameGone();
    } else {
      // Direct execution: a clear request is carried out without a second click.
      if(mode==="unknown") {
        await check.waitFor();
        await replied("The lighting result is not confirmed yet");
        assert.equal(count("confirm"),1);
        assert.equal(await page.evaluate(()=>{try {return !!document.querySelector("iframe").contentWindow.document;} catch {return "blocked";}}),"blocked");
        await check.click();
      }
      await replied(mode==="partial" ? "Some lights were not changed. Kitchen (Victoria): not changed." :
        mode==="both-homes" ? "Done. Kitchen (Los Angeles): turned off; Kitchen (Victoria): turned off." :
        "Done. Kitchen (Victoria): turned off.");
      if(mode==="direct") await page.screenshot({path:path.join(root,`.tmp/lighting-inline-${viewportName}.png`)});
      await frameGone();
      assert.equal(count("confirm"),1);
      if(mode==="both-homes") assert.deepEqual(calls.find(c=>c.operation==="propose").body.sites,["echo","victoria"]);
    }
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,"no horizontal page scroll");
    assert.ok(count("propose")<=1 && count("confirm")<=1);
    const events=await page.evaluate(()=>events.map(({text,kind,privateMemoryContext})=>({text,kind,privateMemoryContext})));
    assert.ok(events.every(e=>e.privateMemoryContext));
    assert.ok(!JSON.stringify(events).includes("private-csrf"));
    assert.deepEqual(errors,[]);
    console.log("PASS",viewportName,mode);
  } catch(error) {
    const page=context.pages()[0];
    const frameText=await page?.frames().find(f=>f.url().startsWith("https://agent.test/"))?.evaluate(()=>document.body.innerText).catch(()=>null);
    console.error("FAIL",viewportName,mode,{calls:calls.map(c=>c.operation),events:await page?.evaluate(()=>events.map(e=>e.kind+": "+e.text)).catch(()=>null),frameText,errors});
    throw error;
  } finally {await context.close();}
}

(async()=>{
  fs.mkdirSync(path.join(root,".tmp"),{recursive:true});
  const {configFromEnv,createAgentOrigin}=await import("../stack/services/home-agent-origin/src/origin.mjs");
  const originServer=createAgentOrigin(configFromEnv({
    HOME_AGENT_WEB_PUBLIC_ORIGIN:"https://agent.test",
    HOME_AGENT_WEB_BFF_URL:"http://127.0.0.1:1",
    HOME_AGENT_WEB_ASSET_ROOT:path.join(root,"app/src/home-agent"),
    HOME_AGENT_WEB_HOST:"127.0.0.1",HOME_AGENT_WEB_PORT:"8096",
    HOME_AGENT_WEB_PREFERENCE_HOME_ORIGINS:"https://home.test",
  }));
  await new Promise((resolve,reject)=>{originServer.once("error",reject);originServer.listen(0,"127.0.0.1",resolve);});
  let browser;
  try {
    browser=await chromium.launch({headless:true});
    const only=process.argv[2];
    for(const viewportName of Object.keys(VIEWPORTS)) for(const mode of MODES) {
      if(!only || only===mode) await scenario(browser,originServer,mode,viewportName);
    }
  } finally {
    await browser?.close();
    await new Promise(resolve=>originServer.close(resolve));
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
