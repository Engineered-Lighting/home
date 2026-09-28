"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path");
const {chromium}=require("playwright");
const http=require("node:http");
const root=path.resolve(__dirname,".."),fact="00000000-0000-0000-0000-000000000001";
const preference=tone=>({version:1,kind:"personal_preference",key:"lighting.evening.tone",scope:"owner",value:tone});
(async()=>{
  const {configFromEnv,createAgentOrigin}=await import("../stack/services/home-agent-origin/src/origin.mjs");
  const originServer=createAgentOrigin(configFromEnv({
    HOME_AGENT_WEB_PUBLIC_ORIGIN:"https://agent.test",
    HOME_AGENT_WEB_BFF_URL:"http://127.0.0.1:1",
    HOME_AGENT_WEB_ASSET_ROOT:path.join(root,"app/src/home-agent"),
    HOME_AGENT_WEB_HOST:"127.0.0.1",HOME_AGENT_WEB_PORT:"8096",
  }));
  await new Promise((resolve,reject)=>{originServer.once("error",reject);originServer.listen(0,"127.0.0.1",resolve);});
  let browser;
  try {
    browser=await chromium.launch({headless:true});
    for(const mode of ["remember","correct","read","forget","unknown","wrong-origin","cancel-context","clear-review"]) {
      const context=await browser.newContext({viewport:{width:390,height:844}}),calls=[],errors=[];
      let tone=mode==="remember" || mode==="unknown" ? null : "warm",revision=tone?1:0,review;
      context.on("page",page=>page.on("pageerror",error=>errors.push(error.message)));
      await context.route("**/*",async route=>{
        const request=route.request(),url=new URL(request.url());
        const reply=(value,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(value)});
        assert.ok(["https://home.test","https://agent.test"].includes(url.origin));
        if(url.origin==="https://home.test") {
          if(url.pathname==="/home-personal-memory.js") return route.fulfill({contentType:"text/javascript",body:fs.readFileSync(path.join(root,"app/src/home-personal-memory.js"),"utf8")});
          return route.fulfill({contentType:"text/html",body:`<button id="ask">Ask Home</button><script>window.HG_WEB_MODE=true;window.HG_AGENT_ORIGIN='https://agent.test';window.events=[];window.current=true;</script><script src="/home-personal-memory.js"></script><script>document.getElementById('ask').onclick=()=>HomePersonalMemory.run(${JSON.stringify(mode==="read" ? "What lighting do I prefer in the evening?" : mode==="forget" ? "Forget my evening lighting preference." : mode==="correct" ? "Actually, I prefer neutral lighting in the evening." : "Remember that I prefer warm lighting in the evening.")},{addEvent:e=>events.push(e),isCurrent:()=>current});</script>`});
        }
        if(url.pathname.startsWith("/home-agent/")) {
          const name=url.pathname.split("/").at(-1);
          assert.ok(["preference-review.html","preference-review.css","preference-review.js","api.js"].includes(name));
          const served=await new Promise((resolve,reject)=>{
            http.get({hostname:"127.0.0.1",port:originServer.address().port,path:url.pathname,headers:{Host:"agent.test"}},res=>{
              const chunks=[];res.on("data",chunk=>chunks.push(chunk));
              res.on("end",()=>resolve({status:res.statusCode,headers:res.headers,body:Buffer.concat(chunks)}));
              res.on("error",reject);
            }).on("error",reject);
          });
          assert.equal(served.status,200);
          return route.fulfill(served);
        }
        const operation=url.pathname.split("/").at(-1),body=request.postDataJSON();calls.push({operation,body});
        if(operation==="session") return reply({authenticated:true,user_id:"owner",csrf_token:"private-csrf",authority:{version:1,site_id:"echo",ha_issuer_id:"home-assistant:echo"},personal_memory_enabled:true,
          personal_memory_home_origins:[mode==="wrong-origin" ? "https://another-home.test" : "https://home.test"]});
        assert.equal(request.headers()["x-csrf-token"],"private-csrf");
        if(operation==="read") return reply({version:1,result:{revision,fact_id:revision?fact:null,preference:tone?preference(tone):null,confirmed_at:revision?new Date().toISOString():null,source:"core.personal-preferences.v1",source_site:tone?"echo":null}});
        if(operation==="propose") {
          review={version:1,operation_id:body.operation_id,operation:body.operation,expected_revision:body.expected_revision,expected_fact_id:body.expected_fact_id,current:tone?preference(tone):null,proposed:body.preference,source_site:"echo",applies_to:"both_homes",effect:"memory_only",expires_at:new Date(Date.now()+60000).toISOString(),reviewed_digest:"a".repeat(64)};
          return reply({version:1,result:review});
        }
        if(operation==="confirm") {
          tone=review.proposed?.value??null;revision++;
          if(mode==="unknown") return reply({error:"unknown"},503);
          return reply({version:1,result:{operation_id:review.operation_id,revision,status:mode==="forget"?"ledger_pending":"committed"}});
        }
        if(operation==="outcome") return reply({version:1,result:{operation_id:review.operation_id,revision,historical:true,status:mode==="forget"?"forgotten":"committed"}});
        throw new Error("Unexpected route "+url.pathname);
      });
      try {
        const page=await context.newPage();await page.goto("https://home.test/");
        const opened=context.waitForEvent("page");await page.getByRole("button",{name:"Ask Home"}).click();const popup=await opened;
        await popup.setViewportSize({width:390,height:844});
        if(mode==="wrong-origin") {
          await popup.getByText("Ready for your Home request.").waitFor();await page.waitForTimeout(1100);
          assert.equal(calls.filter(call=>call.operation!=="session").length,0);
        } else if(mode==="cancel-context" || mode==="clear-review") {
          await popup.getByRole("button",{name:"Confirm and show in Home"}).waitFor();
          const closed=popup.waitForEvent("close");
          await page.evaluate(mode=>{if(mode==="clear-review")window.HomePersonalMemory.reset();else window.current=false;},mode);
          await closed;
          assert.equal(calls.filter(c=>c.operation==="confirm").length,0);
        } else if(mode==="read") {
          await popup.getByRole("button",{name:"Show in Home",exact:true}).waitFor();
          assert.equal((await page.evaluate(()=>events)).filter(e=>e.kind==="home").length,0);
          await popup.getByRole("button",{name:"Show in Home",exact:true}).click();
          await page.waitForFunction(()=>events.some(e=>e.text.includes("You prefer warm")));
          assert.equal(calls.filter(c=>c.operation==="confirm").length,0);
        } else {
          await popup.getByRole("button",{name:"Confirm and show in Home"}).waitFor();
          if(mode==="remember") await popup.screenshot({path:path.join(root,".tmp/personal-preference-review-phone.png")});
          assert.equal(calls.filter(c=>c.operation==="confirm").length,0);
          await popup.getByRole("button",{name:"Confirm and show in Home"}).click();
          if(["forget","unknown"].includes(mode)) {
            await popup.getByRole("button",{name:"Check outcome"}).waitFor();
            assert.ok(!(await page.evaluate(()=>events)).some(e=>e.text.startsWith("Your evening lighting preference has been forgotten")));
            await popup.getByRole("button",{name:"Check outcome"}).click();
          }
          await page.waitForFunction(()=>events.some(e=>e.text.startsWith("Saved:") || e.text.startsWith("Your evening lighting preference has been forgotten")));
          assert.equal(calls.filter(c=>c.operation==="confirm").length,1);
          if(mode==="correct") assert.equal(tone,"neutral");
        }
        if(!popup.isClosed()) assert.equal(await popup.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
        assert.ok(calls.filter(c=>c.operation==="propose").length<=1);
        const events=await page.evaluate(()=>events.map(({text,kind,privateMemoryContext})=>({text,kind,privateMemoryContext})));
        assert.ok(events.every(e=>e.privateMemoryContext));
        assert.ok(!JSON.stringify(events).includes("private-csrf"));
        assert.deepEqual(errors,[]);
        console.log("PASS",mode);
      } finally {await context.close();}
    }
  } finally {
    await browser?.close();
    await new Promise(resolve=>originServer.close(resolve));
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
