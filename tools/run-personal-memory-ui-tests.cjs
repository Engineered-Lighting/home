"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path");
const {chromium}=require("playwright");
const http=require("node:http");
const root=path.resolve(__dirname,".."),fact="00000000-0000-0000-0000-000000000001";
const preference=tone=>({version:1,kind:"personal_preference",key:"lighting.evening.tone",scope:"owner",value:tone});
const VIEWPORTS={desktop:{width:1280,height:800},phone:{width:375,height:812}};
const MODES=["remember","light","correct","read","forget","unknown","untrusted","expired","cancel","offscreen","wrong-origin","foreign-embedder","cancel-context","clear-review"];
const PROMPTS={read:"What lighting do I prefer in the evening?",forget:"Forget my evening lighting preference.",correct:"Actually, I prefer neutral lighting in the evening."};
// A minimal Home chat: user text, replies, and an inline slot for the review card.
const homePage=(mode,text)=>`<!doctype html><html data-theme="${mode==="light"?"light":"dark"}"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>:root{--hg-fg-4:#767676}[data-theme=light]{--hg-fg-4:#8A8270}body{margin:0;font:15px system-ui;background:${mode==="light"?"#EFE9DC;color:#1A1812":"#000;color:#EDEDED"}}main{padding:16px}#ask{min-height:44px}.spacer{height:${mode==="offscreen"?2400:0}px}</style>
<main><button id="ask">Ask Home</button><div class="spacer"></div><div id="thread"></div></main>
<script>window.HG_WEB_MODE=true;window.HG_AGENT_ORIGIN='https://agent.test';window.events=[];window.current=true;</script>
<script src="/home-personal-memory.js"></script>
<script>
const thread=document.getElementById('thread');
const addEvent=e=>{events.push(e);const row=document.createElement('div');row.className='event '+e.kind;row.textContent=e.text;thread.appendChild(row);
  if(e.mountReview){const slot=document.createElement('div');slot.className='slot';row.appendChild(slot);e.mountReview(slot);}};
document.getElementById('ask').onclick=()=>HomePersonalMemory.run(${JSON.stringify(text)},{addEvent,isCurrent:()=>current});
</script>`;

async function served(originServer,pathname) {
  return new Promise((resolve,reject)=>{
    http.get({hostname:"127.0.0.1",port:originServer.address().port,path:pathname,headers:{Host:"agent.test"}},res=>{
      const chunks=[];res.on("data",chunk=>chunks.push(chunk));
      res.on("end",()=>resolve({status:res.statusCode,headers:res.headers,body:Buffer.concat(chunks)}));
      res.on("error",reject);
    }).on("error",reject);
  });
}

async function scenario(browser,originServer,mode,viewportName) {
  const home=mode==="foreign-embedder" ? "https://evil.test" : "https://home.test";
  const context=await browser.newContext({viewport:VIEWPORTS[viewportName]}),calls=[],errors=[];
  let tone=["remember","light","unknown","untrusted","expired","cancel","offscreen"].includes(mode) || mode.endsWith("-context") || mode==="clear-review" || mode==="wrong-origin" || mode==="foreign-embedder" ? null : "warm";
  let revision=tone?1:0,review;
  context.on("page",page=>page.on("pageerror",error=>errors.push(error.message)));
  await context.route("**/*",async route=>{
    const request=route.request(),url=new URL(request.url());
    const reply=(value,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(value)});
    assert.ok([home,"https://agent.test"].includes(url.origin),url.href);
    if(url.origin===home) {
      if(url.pathname==="/home-personal-memory.js") return route.fulfill({contentType:"text/javascript",body:fs.readFileSync(path.join(root,"app/src/home-personal-memory.js"),"utf8")});
      return route.fulfill({contentType:"text/html",body:homePage(mode,PROMPTS[mode] || "Remember that I prefer warm lighting in the evening.")});
    }
    if(url.pathname.startsWith("/home-agent/")) {
      const name=url.pathname.split("/").at(-1);
      assert.ok(["preference-review.html","preference-review.css","preference-review.js","api.js","geist-latin.woff2","geist-mono-latin.woff2"].includes(name),name);
      const response=await served(originServer,url.pathname);
      assert.equal(response.status,200);
      return route.fulfill(response);
    }
    const operation=url.pathname.split("/").at(-1),body=request.postDataJSON();calls.push({operation,body});
    if(operation==="session") return reply({authenticated:true,user_id:"owner",csrf_token:"private-csrf",authority:{version:1,site_id:"echo",ha_issuer_id:"home-assistant:echo"},personal_memory_enabled:true,
      personal_memory_home_origins:[mode==="wrong-origin" ? "https://another-home.test" : "https://home.test"]});
    assert.equal(request.headers()["x-csrf-token"],"private-csrf");
    if(operation==="read") return reply({version:1,result:{revision,fact_id:revision?fact:null,preference:tone?preference(tone):null,confirmed_at:revision?new Date().toISOString():null,source:"core.personal-preferences.v1",source_site:tone?"echo":null}});
    if(operation==="propose") {
      review={version:1,operation_id:body.operation_id,operation:body.operation,expected_revision:body.expected_revision,expected_fact_id:body.expected_fact_id,current:tone?preference(tone):null,proposed:body.preference,source_site:"echo",applies_to:"both_homes",effect:"memory_only",
        expires_at:new Date(Date.now()+(mode==="expired"?1500:60000)).toISOString(),reviewed_digest:"a".repeat(64)};
      return reply({version:1,result:review});
    }
    if(operation==="confirm") {
      assert.deepEqual(Object.keys(body).sort(),["gesture_id","operation_id","reviewed_digest","version"]);
      assert.equal(body.operation_id,review.operation_id);assert.equal(body.reviewed_digest,review.reviewed_digest);
      tone=review.proposed?.value??null;revision++;
      if(mode==="unknown") return reply({error:"unknown"},503);
      return reply({version:1,result:{operation_id:review.operation_id,revision,status:mode==="forget"?"ledger_pending":"committed"}});
    }
    if(operation==="outcome") {
      assert.deepEqual(body,{version:1,operation_id:review.operation_id,reviewed_digest:review.reviewed_digest});
      return reply({version:1,result:{operation_id:review.operation_id,revision,historical:true,status:mode==="forget"?"forgotten":"committed"}});
    }
    throw new Error("Unexpected route "+url.pathname);
  });
  const count=operation=>calls.filter(c=>c.operation===operation).length;
  const texts=page=>page.evaluate(()=>events.filter(e=>e.kind==="home").map(e=>e.text));
  try {
    const page=await context.newPage();await page.goto(home+"/");
    await page.getByRole("button",{name:"Ask Home"}).click();
    assert.equal(context.pages().length,1,"no popup window may open");
    const frame=page.frameLocator("iframe[title='Shared preference review']");
    const confirm=frame.locator("#confirm"),check=frame.getByRole("button",{name:"Check outcome"}),cancel=frame.getByRole("button",{name:"Cancel"});
    const replied=prefix=>page.waitForFunction(p=>events.some(e=>e.kind==="home" && e.text.startsWith(p)),prefix);
    const frameGone=()=>page.waitForFunction(()=>!document.querySelector("iframe"));
    if(mode==="foreign-embedder") {
      // frame-ancestors refuses the embed: the review never runs, so it never calls the Agent API.
      await page.waitForTimeout(1500);
      assert.equal(calls.length,0);
    } else if(mode==="wrong-origin") {
      await frame.getByText("Connecting to your Home conversation…").waitFor();await page.waitForTimeout(1100);
      assert.equal(calls.filter(call=>call.operation!=="session").length,0);
    } else if(mode==="cancel-context" || mode==="clear-review") {
      await confirm.waitFor();
      await page.evaluate(mode=>{if(mode==="clear-review")window.HomePersonalMemory.reset();else window.current=false;},mode);
      await frameGone();
      assert.equal(await page.locator(".slot").textContent(),"closed · nothing changed");
      assert.equal(count("confirm"),0);
    } else if(mode==="read") {
      await replied("You prefer warm");
      await frameGone();
      assert.equal(count("propose"),0);assert.equal(count("confirm"),0);
    } else if(mode==="cancel") {
      await cancel.click();
      await replied("Cancelled.");await frameGone();
      assert.equal(count("confirm"),0);
    } else if(mode==="expired") {
      await confirm.waitFor();
      await replied("That review expired");await frameGone();
      assert.equal(count("confirm"),0);
    } else if(mode==="untrusted") {
      await confirm.waitFor();
      await frame.locator("#confirm:not([disabled])").waitFor();
      const agent=page.frames().find(f=>f.url().startsWith("https://agent.test/"));
      await agent.evaluate(()=>{
        const button=document.getElementById("confirm");
        button.click();
        button.dispatchEvent(new MouseEvent("click",{bubbles:true,cancelable:true}));
        button.dispatchEvent(new PointerEvent("pointerup",{bubbles:true}));
      });
      // The Home page cannot reach into the Agent frame at all.
      assert.equal(await page.evaluate(()=>{try {return !!document.querySelector("iframe").contentWindow.document;} catch {return "blocked";}}),"blocked");
      await page.waitForTimeout(400);
      assert.equal(count("confirm"),0);
      assert.equal(await confirm.isVisible(),true);
      await confirm.click();
      await replied("Saved: warm");
      assert.equal(count("confirm"),1);
    } else if(mode==="offscreen") {
      await confirm.waitFor({state:"attached"});
      await frame.getByText("Save warm evening lighting").waitFor({state:"attached"});
      await page.waitForTimeout(1200);
      assert.equal(await confirm.isDisabled(),true,"confirm must not arm while off screen");
      await page.locator("iframe").scrollIntoViewIfNeeded();
      await frame.locator("#confirm:not([disabled])").waitFor();
      await confirm.click();
      await replied("Saved: warm");
      assert.equal(count("confirm"),1);
    } else {
      await confirm.waitFor();
      assert.equal(count("confirm"),0);
      if(["remember","light"].includes(mode)) {
        await frame.locator("#confirm:not([disabled])").waitFor();
        const agent=page.frames().find(f=>f.url().startsWith("https://agent.test/"));
        const look=await agent.evaluate(()=>({theme:document.documentElement.dataset.theme,bg:getComputedStyle(document.documentElement).backgroundColor,
          font:getComputedStyle(document.getElementById("status")).fontFamily,fonts:[...document.fonts].filter(f=>f.status==="loaded").map(f=>f.family)}));
        assert.equal(look.theme,mode==="light"?"light":"dark");
        assert.equal(look.bg,"rgba(0, 0, 0, 0)","frame must be transparent over Home");
        assert.match(look.font,/^"?Geist"?,/);
        await page.screenshot({path:path.join(root,`.tmp/personal-preference-inline-${mode}-${viewportName}.png`)});
      }
      await confirm.click();
      if(["forget","unknown"].includes(mode)) {
        await check.waitFor();
        await replied("The change is not confirmed yet");
        // Unknown outcome: lookup only. No second confirm, no cancel.
        assert.equal(await confirm.isVisible(),false);assert.equal(await cancel.isVisible(),false);
        assert.ok(!(await texts(page)).some(t=>t.startsWith("Forgotten") || t.startsWith("Saved")));
        await check.click();
      }
      if(mode==="remember") {
        await frameGone();
        assert.equal(await page.locator(".slot").textContent(),"review closed");
      }
      await replied(mode==="forget" ? "Forgotten: your evening lighting preference is gone from both homes." : mode==="correct" ? "Saved: neutral lighting in the evening, for both homes." : "Saved: warm lighting in the evening, for both homes.");
      await frameGone();
      assert.equal(count("confirm"),1);
      if(mode==="correct") assert.equal(tone,"neutral");
      if(mode==="forget") assert.equal(tone,null);
    }
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,"no horizontal page scroll");
    const agent=page.frames().find(f=>f.url().startsWith("https://agent.test/"));
    if(agent && mode!=="foreign-embedder") assert.equal(await agent.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,"no horizontal card scroll");
    assert.ok(count("propose")<=1);
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
