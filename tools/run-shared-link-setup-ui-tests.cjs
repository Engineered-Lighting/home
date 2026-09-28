"use strict";
// Actual shipped assets, isolated browser fixtures only. No home endpoint traffic.
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path");
const {chromium}=require("playwright");
const root=path.resolve(__dirname,".."),id="00000000-0000-0000-0000-000000000001",gesture="00000000-0000-0000-0000-000000000002";
const review=()=>({version:1,ceremony_id:id,gesture_id:gesture,reviewed_digest:"a".repeat(64),expires_at:new Date(Date.now()+240000).toISOString(),
  accounts:[{site_id:"echo",issuer_id:"home-assistant:echo",subject:"owner"},{site_id:"victoria",issuer_id:"home-assistant:victoria",subject:"victoria-owner"}]});
// Model a server clock 2 s ahead of the browser: a full 60 s window must still be accepted.
const serverExpiry=()=>Date.now()+62000;
const form=()=>({status:"form",handle:"f".repeat(64),step:"init",fields:["username","password"],invalid:false});
(async()=>{
  const browser=await chromium.launch({headless:true});
  try{
    for(const mode of ["success","unknown-handoff"]){
      const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage(),calls=[],errors=[];
      page.on("pageerror",e=>errors.push(e.message));
      await page.route("**/*",async route=>{
        const url=new URL(route.request().url());assert.equal(url.origin,"https://agent.test");
        const reply=(body,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(body)});
        if(url.pathname.startsWith("/home-agent/")){
          const name=url.pathname==="/home-agent/"?"index.html":url.pathname.split("/").at(-1);
          assert.ok(["index.html","api.js","panel.js","panel.css"].includes(name));
          return route.fulfill({body:fs.readFileSync(path.join(root,"app/src/home-agent",name)),contentType:name.endsWith("html")?"text/html":name.endsWith("css")?"text/css":"text/javascript"});
        }
        const operation=url.pathname.split("/").at(-1);calls.push({operation,body:route.request().postDataJSON()});
        if(operation==="session")return reply({authenticated:true,user_id:"owner",csrf_token:"csrf",authority:{version:1,site_id:"echo",ha_issuer_id:"home-assistant:echo"},shared_link_review_enabled:true,shared_link_setup:{victoria_origin:"https://victoria.test"}});
        if(operation==="snapshot")return reply({rollout_mode:"shadow",capabilities:{},preferences:{}});
        if(operation==="status")return reply({state:"bound"});
        if(operation==="start")return reply({pairing_id:id,expires_at:serverExpiry()});
        if(operation==="handoff" && mode==="unknown-handoff")return reply({error:"unknown"},503);
        if(["handoff","issuance-outcome","victoria-auth-admit"].includes(operation))return reply({version:1,status:"authentication_required",ceremony_id:id});
        if(operation==="auth-begin")return reply(form());
        if(["auth-submit","victoria-auth-outcome"].includes(operation))return reply({version:1,status:"authenticated",site_id:operation==="auth-submit"?"echo":"victoria",ceremony_id:id});
        if(["prepare-review","review"].includes(operation))return reply(review());
        if(operation==="confirm")return reply({version:1,status:"confirmed",ceremony_id:id});
        return reply({});
      });
      try{
        await page.goto("https://agent.test/home-agent/");
        await page.getByRole("button",{name:"Start account linking",exact:true}).click();
        assert.equal(await page.getByRole("link",{name:"Create a Victoria connection code"}).getAttribute("href"),`https://victoria.test/#shared-link/${id}`);
        await page.getByLabel("Victoria connection code").fill("a".repeat(64));
        await page.getByRole("button",{name:"Connect this Victoria session",exact:true}).click();
        if(mode==="unknown-handoff"){
          await page.getByRole("button",{name:"Check pairing status",exact:true}).click();
          assert.equal(calls.filter(c=>c.operation==="handoff").length,1);
        }
        await page.getByRole("button",{name:"Verify Los Angeles account",exact:true}).click();
        await page.getByLabel("Username",{exact:true}).fill("owner");await page.getByLabel("Password",{exact:true}).fill("fixture-only");
        if(mode==="success")await page.screenshot({path:path.join(root,".tmp/shared-link-setup-mobile.png"),fullPage:true});
        await page.getByRole("button",{name:"Authenticate Los Angeles account",exact:true}).click();
        await page.getByRole("button",{name:"Prepare Victoria verification",exact:true}).click();
        await page.getByRole("button",{name:"Check Victoria authentication",exact:true}).click();
        await page.getByRole("button",{name:"Prepare account review",exact:true}).click();
        await page.getByRole("button",{name:"Review both accounts",exact:true}).click();
        assert.equal(calls.filter(c=>c.operation==="confirm").length,0);
        await page.getByRole("checkbox",{name:"These are both my accounts."}).check();
        await page.getByRole("button",{name:"Link these two accounts",exact:true}).click();
        await page.getByText("Your two accounts are linked.",{exact:true}).waitFor();
        assert.equal(calls.filter(c=>c.operation==="confirm").length,1);
        assert.deepEqual(await page.evaluate(()=>Object.keys(localStorage)),[]);
        assert.deepEqual(errors,[]);
        console.log(`PASS Home setup ${mode}`);
      }finally{await context.close();}
    }
    const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage(),calls=[],errors=[];
    page.on("pageerror",e=>errors.push(e.message));
    await page.route("**/*",async route=>{
      const url=new URL(route.request().url());assert.equal(url.origin,"https://victoria.test");
      const assets={"/":"victoria-link-page.html","/link-setup.js":"victoria-link-page.js","/link-setup.css":"victoria-link-page.css"};
      if(assets[url.pathname])return route.fulfill({body:fs.readFileSync(path.join(root,"stack/services/home-agent-bff/src",assets[url.pathname])),
        contentType:url.pathname==="/"?"text/html":url.pathname.endsWith("css")?"text/css":"text/javascript"});
      const operation=url.pathname.split("/").at(-1);calls.push(operation);
      const body=operation==="session"?{authenticated:true,csrf_token:"csrf",authority:{site_id:"victoria",ha_issuer_id:"home-assistant:victoria"}}:
        operation==="handoff"?{version:1,offer:{pairing_id:id,token:"b".repeat(64),expires_at:serverExpiry()}}:
        operation==="auth-begin"?form():{version:1,status:"authenticated",site_id:"victoria",ceremony_id:id};
      return route.fulfill({contentType:"application/json",body:JSON.stringify(body)});
    });
    try{
      await page.goto(`https://victoria.test/#shared-link/${id}`);
      await page.getByRole("button",{name:"Create connection code",exact:true}).click();
      await page.waitForFunction(()=>document.getElementById("code").value.length===64);
      assert.equal(await page.getByLabel("Connection code",{exact:true}).inputValue(),"b".repeat(64));
      await page.getByRole("button",{name:"Verify Victoria account",exact:true}).click();
      await page.getByLabel("Username",{exact:true}).fill("victoria-owner");await page.getByLabel("Password",{exact:true}).fill("fixture-only");
      await page.screenshot({path:path.join(root,".tmp/victoria-link-setup-mobile.png"),fullPage:true});
      await page.getByRole("button",{name:"Authenticate Victoria account",exact:true}).click();
      await page.getByText(/Victoria verified\. Return to Home/).waitFor();
      assert.equal(calls.filter(c=>c==="auth-submit").length,1);
      assert.equal(await page.getByLabel("Password",{exact:true}).count(),0);
      assert.deepEqual(errors,[]);console.log("PASS Victoria setup");
    }finally{await context.close();}
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
