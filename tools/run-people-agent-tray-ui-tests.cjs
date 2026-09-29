"use strict";
// People tray for Agent-sourced people: the real HomePeopleOverlay from
// home-people.jsx, transpiled in the page with the vendored Babel exactly as
// the app does, rendered against a legacy store that reports ready:false and
// a same-origin Home Agent household read.
//
//   NODE_PATH=<repo>/node_modules node tools/run-people-agent-tray-ui-tests.cjs
//
// Proves: the tray renders the person and their relationships from the
// overlay's own data, never calls the HA identity detail or avatar routes with
// an agent person id, hides every edit control, and the banner describes the
// Agent source. The legacy scenario pins that the legacy path is unchanged.
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path");
const {chromium}=require("playwright");
const root=path.resolve(__dirname,"..");
const src=path.join(root,"app/src");
const HOME="https://home.test",HA="https://ha.test";

const HOUSEHOLD={people:[
  {person_id:"p-self",display_name:"Marcelo",pronouns:"he/him",is_self:true},
  {person_id:"p-ana",display_name:"Ana",pronouns:"she/her",is_self:false},
  {person_id:"p-leo",display_name:"Leo",pronouns:null,is_self:false},
  {person_id:"p-rui",display_name:"Rui",pronouns:null,is_self:false},
]};
// Symmetric predicates arrive as the authority stores them: one edge each way.
const edge=(id,predicate,from,to)=>({fact_id:id,predicate,subject_person_id:from,object_person_id:to});
const RELATIONSHIPS={relationships:[
  edge("f-1","partner_of","p-self","p-ana"),edge("f-2","partner_of","p-ana","p-self"),
  edge("f-3","parent_of","p-self","p-leo"),edge("f-4","parent_of","p-ana","p-leo"),
  edge("f-5","friend_of","p-ana","p-rui"),edge("f-6","friend_of","p-rui","p-ana"),
]};
const LEGACY_BOUNDARY={state:"legacy_frozen",semantic_write_fence_installed:true,semantic_writes_frozen:true};
const LEGACY_IDENTITY={uuid:"l-1",display_name:"Legacy Person",relationship_type:"friend",version:3};

const PAGE=`<!doctype html><meta charset="utf-8"><body><div id="root"></div>
<script src="/vendor/react-18.3.1/react.production.min.js"></script>
<script src="/vendor/react-18.3.1/react-dom.production.min.js"></script>
<script src="/vendor/babel-7.29.0/babel.min.js"></script>
<script src="/home-overlay.js"></script>
<script src="/home-people-helpers.js"></script>
<script>
window.__calls=[];
window.__mode=new URLSearchParams(location.search).get("mode");
const reply=(status,body)=>({ok:status>=200&&status<300,status,statusText:String(status),
  json:async()=>body,text:async()=>JSON.stringify(body),blob:async()=>new Blob([])});
window.fetchWithRetry=async({url})=>{
  window.__calls.push({kind:"retry",method:"GET",url});
  return window.__mode==="agent"
    ? reply(200,{ready:false,setup_error:"identity store fenced",legacy_identity_boundary:${JSON.stringify(LEGACY_BOUNDARY)}})
    : reply(200,{identities:[${JSON.stringify(LEGACY_IDENTITY)}],legacy_identity_boundary:${JSON.stringify(LEGACY_BOUNDARY)}});
};
window.tauriFetch=async(url,options={})=>{
  window.__calls.push({kind:"tauri",method:options.method||"GET",url});
  if(url==="${HA}/api/extended_openai_conversation/identity/l-1") return reply(200,{identity:${JSON.stringify(LEGACY_IDENTITY)},relationships:[],preferences:[]});
  return reply(404,{error:"not_found"});
};
(async()=>{
  const source=await (await fetch("/home-people.jsx")).text();
  (0,eval)(Babel.transform(source,{presets:["react"],filename:"home-people.jsx"}).code);
  ReactDOM.createRoot(document.getElementById("root")).render(
    React.createElement(window.HomePeopleOverlay,{open:true,onClose(){},endpoint:"${HA}",token:"people-tray-test-token"}));
})().catch(error=>{window.__bootError=String(error&&error.stack||error);});
</script></body>`;

const text=(page,selector)=>page.locator(selector).first().innerText();
const tray=page=>page.locator('[role="dialog"][aria-label="Identity detail"]');

async function openRow(page,name) {
  await page.locator('tr[role="button"]',{hasText:name}).first().click();
  await tray(page).waitFor();
}

async function scenario(browser,mode) {
  const context=await browser.newContext(),errors=[],agentReads=[];
  await context.route("**/*",async route=>{
    const url=new URL(route.request().url());
    assert.equal(url.origin,HOME,"only the Home origin is ever requested: "+url.href);
    const json=value=>route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(value)});
    if(url.pathname==="/") return route.fulfill({contentType:"text/html",body:PAGE});
    if(url.pathname==="/api/agent/v1/household") {agentReads.push(url.pathname);return json(HOUSEHOLD);}
    if(url.pathname==="/api/agent/v1/relationships") {agentReads.push(url.pathname);return json(RELATIONSHIPS);}
    const file=path.join(src,decodeURIComponent(url.pathname));
    assert.ok(file.startsWith(src+path.sep)&&fs.existsSync(file),"unexpected asset "+url.pathname);
    const type=file.endsWith(".js")||file.endsWith(".jsx") ? "text/javascript" : "application/octet-stream";
    return route.fulfill({contentType:type,body:fs.readFileSync(file)});
  });
  const page=await context.newPage();
  page.on("pageerror",error=>errors.push(error.message));
  try {
    await page.goto(`${HOME}/?mode=${mode}`);
    await page.waitForFunction(()=>window.__bootError||document.querySelector('tr[role="button"],svg circle'),null,{timeout:20000}).catch(()=>{});
    const bootError=await page.evaluate(()=>window.__bootError||null);
    assert.equal(bootError,null,bootError||"");
    await page.getByRole("button",{name:"list",exact:true}).click();
    await page.locator('tr[role="button"]').first().waitFor();
    const body=await text(page,"body");
    if(mode==="agent") {
      assert.match(body,/home agent · read-only/);
      assert.match(body,/read\s+from the Home Agent/);
      assert.doesNotMatch(body,/verified legacy People view/,"the legacy banner must not describe Agent data");
      assert.equal(await page.getByRole("button",{name:"queue",exact:true}).count(),0,"no queue view for Agent data");

      await openRow(page,"Ana");
      let detail=await tray(page).innerText();
      assert.match(detail,/home agent · read-only/i);
      assert.match(detail,/Ana/);
      assert.match(detail,/she\/her/);
      assert.match(detail,/relationships \(3\)/i,"symmetric edges are listed once each");
      for(const line of [/friend of\s+Rui/,/parent of\s+Leo/,/partner of\s+Marcelo/]) assert.match(detail,line);
      assert.match(detail,/Photos, preferences, and Frigate faces are not\s+available from the Home Agent/);
      assert.doesNotMatch(detail,/HTTP 404|identity not found|loading/i);
      assert.equal(await tray(page).locator("input,textarea,select").count(),0,"no edit fields");
      assert.deepEqual(await tray(page).locator("button").evaluateAll(b=>b.map(x=>x.getAttribute("aria-label"))),["Close panel"],"only the close control");

      await openRow(page,"Leo");
      detail=await tray(page).innerText();
      assert.match(detail,/relationships \(2\)/i);
      assert.match(detail,/child of\s+Ana/);
      assert.match(detail,/child of\s+Marcelo/);

      await openRow(page,"Marcelo");
      detail=await tray(page).innerText();
      assert.match(detail,/you · he\/him/);

      const calls=await page.evaluate(()=>window.__calls);
      assert.deepEqual(calls.filter(c=>/\/identity\//.test(c.url)),[],"no HA identity detail or avatar request for an Agent person");
      assert.deepEqual(calls.map(c=>c.url),[`${HA}/api/extended_openai_conversation/identities`]);
      assert.deepEqual(agentReads.sort(),["/api/agent/v1/household","/api/agent/v1/relationships"]);
    } else {
      assert.match(body,/legacy read-only · cutover pending/);
      assert.match(body,/verified legacy People view is read-only/);
      assert.doesNotMatch(body,/home agent · read-only/);
      await openRow(page,"Legacy Person");
      await page.waitForFunction(()=>document.querySelector('[aria-label="Identity detail"] input[type="text"]'));
      const detail=await tray(page).innerText();
      assert.match(detail,/legacy read-only · cutover pending/i);
      assert.doesNotMatch(detail,/Home Agent/);
      assert.equal(await tray(page).locator('input[type="text"]').first().inputValue(),"Legacy Person");
      const calls=await page.evaluate(()=>window.__calls);
      assert.ok(calls.some(c=>c.method==="HEAD"&&c.url===`${HA}/api/extended_openai_conversation/identity/l-1/avatar`),"legacy avatar probe still runs");
      assert.ok(calls.some(c=>c.method==="GET"&&c.url===`${HA}/api/extended_openai_conversation/identity/l-1`),"legacy detail is still fetched");
      assert.deepEqual(agentReads,[],"no Agent read while the legacy store answers");
    }
    assert.deepEqual(errors,[]);
    console.log("PASS",mode);
  } catch(error) {
    console.error("FAIL",mode,{errors,calls:await page.evaluate(()=>window.__calls).catch(()=>null)});
    throw error;
  } finally {await context.close();}
}

(async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    for(const mode of ["agent","legacy"]) if(!process.argv[2]||process.argv[2]===mode) await scenario(browser,mode);
    console.log("People tray scenarios passed");
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
