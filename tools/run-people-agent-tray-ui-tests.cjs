"use strict";
// People tab on the Home Agent: the real HomePeopleOverlay from
// home-people.jsx, transpiled in the page with the vendored Babel exactly as
// the app does, rendered against a legacy store that reports ready:false, a
// same-origin Home Agent household read, a mocked Home Assistant behind
// /proxy/ha, and the real Agent origin serving the real People bridge.
//
//   NODE_PATH=<repo>/node_modules node tools/run-people-agent-tray-ui-tests.cjs [agent|agent-mobile|legacy]
//   PEOPLE_TRAY_SCREENSHOTS=<dir> also saves screenshots of each Agent run.
//
// Proves, at desktop and 375x812: headshots come from the profile store's
// agent_profile/{id}/avatar route (never probed, never the identity route);
// the tray shows relationships, recent sightings, and enrolled reference
// photos, every image through the typed /proxy/ha/.../frigate_proxy routes,
// and nothing in the DOM or on the network names a Frigate origin; the
// full-screen capture browser pages through the set; profile edits and the
// headshot upload POST to the profile store; adding a person and a
// relationship go through the bridge with the CSRF token. The legacy
// scenario pins that the legacy path is unchanged.
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),http=require("node:http");
const {chromium}=require("playwright");
const root=path.resolve(__dirname,"..");
const src=path.join(root,"app/src");
const HOME="https://home.test",AGENT="https://agent.test",EP="/proxy/ha";
const API=`${EP}/api/extended_openai_conversation`;
const SHOTS=process.env.PEOPLE_TRAY_SCREENSHOTS||null;
const PNG=Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==","base64");
const CSRF="tray-test-csrf-4c1d";

const id=n=>`0190a0b0-0000-7000-8000-00000000000${n}`;
const SELF=id(1),ANA=id(2),LEO=id(3),RUI=id(4),NOVA=id(5);
const HOUSEHOLD={people:[
  {person_id:SELF,display_name:"Marcelo",pronouns:"he/him",is_self:true},
  {person_id:ANA,display_name:"Ana",pronouns:"she/her",is_self:false},
  {person_id:LEO,display_name:"Leo",pronouns:null,is_self:false},
  {person_id:RUI,display_name:"Rui",pronouns:null,is_self:false},
]};
// Symmetric predicates arrive as the authority stores them: one edge each way.
const edge=(fid,predicate,from,to)=>({fact_id:fid,predicate,subject_person_id:from,object_person_id:to});
const RELATIONSHIPS={relationships:[
  edge("f-1","partner_of",SELF,ANA),edge("f-2","partner_of",ANA,SELF),
  edge("f-3","parent_of",SELF,LEO),edge("f-4","parent_of",ANA,LEO),
  edge("f-5","friend_of",ANA,RUI),edge("f-6","friend_of",RUI,ANA),
]};
const PROFILES={
  // frigate_url is what the pre-policy route returned; it must be ignored.
  frigate_url:"http://frigate.lan:5000",
  frigate_faces_available:true,
  profiles:{
    [SELF]:{person_id:SELF,display_name:"Marcelo",relationship_type:"me",avatar_present:true},
    [ANA]:{person_id:ANA,display_name:"Ana",relationship_type:"partner",relationship_subrole:"wife",notes:"Prefers warm light",avatar_present:true},
    [RUI]:{person_id:RUI,display_name:"Rui",relationship_type:"friend",avatar_present:false},
  },
};
const FACES={
  ana:["ana-1715000000.webp","ana_1720000000.webp"],
  marcelo:["marcelo-1716000000.webp"],
  train:["train-1719000000.webp"],
};
const EVENTS=Array.from({length:14},(_,i)=>({id:`${1720000000+i*60}.5-ev${i}`,camera:i%2?"door":"hall",start_time:1720000000+i*60,label:"person",sub_label:["ana",0.9]}));
const LEGACY_BOUNDARY={state:"legacy_frozen",semantic_write_fence_installed:true,semantic_writes_frozen:true};
const LEGACY_IDENTITY={uuid:"l-1",display_name:"Legacy Person",relationship_type:"friend",version:3};
// Every Home Assistant route this tab may call, exactly.
const TYPED_HA_ROUTES=[
  /^\/proxy\/ha\/api\/extended_openai_conversation\/identities$/,
  /^\/proxy\/ha\/api\/extended_openai_conversation\/agent_profiles$/,
  /^\/proxy\/ha\/api\/extended_openai_conversation\/agent_profile\/[0-9a-f-]{36}(\/avatar)?$/,
  /^\/proxy\/ha\/api\/extended_openai_conversation\/frigate_proxy\/faces$/,
  /^\/proxy\/ha\/api\/extended_openai_conversation\/frigate_proxy\/faces\/[a-z0-9%]+\/[A-Za-z0-9._%-]+\.(webp|jpg|png)$/,
  /^\/proxy\/ha\/api\/extended_openai_conversation\/frigate_proxy\/events$/,
  /^\/proxy\/ha\/api\/extended_openai_conversation\/frigate_proxy\/events\/[0-9]+\.[0-9]+-[a-z0-9]+\/thumbnail\.jpg$/,
  /^\/proxy\/ha\/api\/extended_openai_conversation\/identity\/l-1(\/avatar)?$/,
];

const PAGE=`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/home-tokens.css"><body style="margin:0;background:var(--hg-bg-0)"><div id="root"></div>
<script src="/vendor/react-18.3.1/react.production.min.js"></script>
<script src="/vendor/react-18.3.1/react-dom.production.min.js"></script>
<script src="/vendor/babel-7.29.0/babel.min.js"></script>
<script src="/home-overlay.js"></script>
<script src="/home-people-helpers.js"></script>
<script>
window.HG_AGENT_ORIGIN="${AGENT}";
window.__mode=new URLSearchParams(location.search).get("mode");
const reply=(status,body)=>({ok:status>=200&&status<300,status,statusText:String(status),
  json:async()=>body,text:async()=>JSON.stringify(body),blob:async()=>new Blob([])});
window.fetchWithRetry=async({url})=>{
  return window.__mode!=="legacy"
    ? reply(200,{ready:false,setup_error:"identity store fenced",legacy_identity_boundary:${JSON.stringify(LEGACY_BOUNDARY)}})
    : reply(200,{identities:[${JSON.stringify(LEGACY_IDENTITY)}],legacy_identity_boundary:${JSON.stringify(LEGACY_BOUNDARY)}});
};
// The app's tauriFetch is fetch in a browser; the HA token rides the header.
window.tauriFetch=(url,options={})=>fetch(url,options);
(async()=>{
  const source=await (await fetch("/home-people.jsx")).text();
  (0,eval)(Babel.transform(source,{presets:["react"],filename:"home-people.jsx"}).code);
  ReactDOM.createRoot(document.getElementById("root")).render(
    React.createElement(window.HomePeopleOverlay,{open:true,onClose(){},endpoint:"${EP}",token:"people-tray-test-token"}));
})().catch(error=>{window.__bootError=String(error&&error.stack||error);});
</script></body>`;

const tray=page=>page.locator('[role="dialog"][aria-label="Identity detail"]');
const viewer=page=>page.locator('[role="dialog"][aria-label="Capture browser"]');

function served(originServer,pathname) {
  return new Promise((resolve,reject)=>{
    http.get({hostname:"127.0.0.1",port:originServer.address().port,path:pathname,headers:{Host:"agent.test"}},res=>{
      const chunks=[];res.on("data",c=>chunks.push(c));res.on("end",()=>resolve({status:res.statusCode,headers:res.headers,body:Buffer.concat(chunks)}));res.on("error",reject);
    }).on("error",reject);
  });
}

async function openRow(page,name) {
  // On a phone the tray covers the tab strip, so close any open tray first.
  if(await tray(page).count()) {await tray(page).getByRole("button",{name:"Close panel"}).click();await tray(page).waitFor({state:"detached"});}
  await page.getByRole("button",{name:"list",exact:true}).click();
  await page.locator('tr[role="button"]',{hasText:name}).first().click();
  await tray(page).waitFor();
}

async function scenario(browser,originServer,mode) {
  const mobile=mode==="agent-mobile";
  const context=await browser.newContext(mobile ? {viewport:{width:375,height:812},deviceScaleFactor:2,isMobile:true,hasTouch:true} : {viewport:{width:1280,height:860}});
  const errors=[],requests=[],posts=[];
  let household=HOUSEHOLD;
  const profiles=JSON.parse(JSON.stringify(PROFILES));
  await context.route("**/*",async route=>{
    const request=route.request(),url=new URL(request.url()),method=request.method();
    requests.push({origin:url.origin,method,path:url.pathname,search:url.search,auth:request.headers().authorization||null});
    assert.ok([HOME,AGENT].includes(url.origin),"only the Home and Agent origins are ever requested: "+url.href);
    const json=(value,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(value)});
    const image=type=>route.fulfill({status:200,contentType:type,headers:{"Cache-Control":"no-store"},body:PNG});
    if(url.origin===AGENT) {
      if(url.pathname.startsWith("/home-agent/")) return route.fulfill(await served(originServer,url.pathname));
      if(url.pathname==="/api/agent/auth/session") return json({authenticated:true,csrf_token:CSRF,personal_memory_home_origins:[HOME]});
      if(method==="POST") posts.push({origin:AGENT,path:url.pathname,csrf:request.headers()["x-csrf-token"]||null,body:JSON.parse(request.postData()||"null")});
      if(method==="POST" && url.pathname==="/api/agent/v1/household-person") {
        household={people:[...household.people,{person_id:NOVA,display_name:"Nova",pronouns:null,is_self:false}]};
        return json({person_id:NOVA,display_name:"Nova",privacy_scope:"household"},201);
      }
      if(method==="POST" && url.pathname==="/api/agent/v1/partner-attestation") return json({receipt_id:id(9),partner_person_id:RUI,predicate:"sibling_of",document_digest:"b".repeat(64)},201);
      return json({error:"unexpected"},599);
    }
    if(url.pathname==="/") return route.fulfill({contentType:"text/html",body:PAGE});
    if(url.pathname==="/api/agent/v1/household") return json(household);
    if(url.pathname==="/api/agent/v1/relationships") return json(RELATIONSHIPS);
    if(url.pathname.startsWith(EP+"/")) {
      assert.ok(TYPED_HA_ROUTES.some(rx=>rx.test(url.pathname)),"untyped HA route: "+url.pathname+url.search);
      assert.equal(request.headers().authorization,"Bearer people-tray-test-token",url.pathname);
      const p=url.pathname.slice(API.length);
      if(method==="POST") posts.push({origin:HOME,path:url.pathname,contentType:request.headers()["content-type"]||null,
        body:request.headers()["content-type"]==="application/json" ? JSON.parse(request.postData()||"null") : request.postDataBuffer()});
      if(p==="/identities") return json({});
      if(p==="/agent_profiles") return json(profiles);
      let m;
      if((m=/^\/agent_profile\/([0-9a-f-]{36})\/avatar$/.exec(p))) {
        if(method==="POST") {profiles.profiles[m[1]]={...(profiles.profiles[m[1]]||{}),avatar_present:true};return json({ok:true});}
        return profiles.profiles[m[1]]?.avatar_present ? image("image/png") : route.fulfill({status:404,body:""});
      }
      if((m=/^\/agent_profile\/([0-9a-f-]{36})$/.exec(p)) && method==="POST") {
        const body=JSON.parse(request.postData());
        profiles.profiles[m[1]]={...(profiles.profiles[m[1]]||{}),...body};
        return json({ok:true,profile:profiles.profiles[m[1]]});
      }
      if(p==="/frigate_proxy/faces") return json(FACES);
      if(p==="/frigate_proxy/events") {
        assert.equal(url.searchParams.get("limit"),"200");
        return json(url.searchParams.get("person")==="ana" ? EVENTS : []);
      }
      if(/^\/frigate_proxy\/(events\/.+\/thumbnail\.jpg|faces\/.+)$/.test(p)) return image("image/jpeg");
      if(p==="/identity/l-1") return json({identity:LEGACY_IDENTITY,relationships:[],preferences:[]});
      if(p==="/identity/l-1/avatar") return route.fulfill({status:404,body:""});
      return json({error:"not_found"},404);
    }
    const file=path.join(src,decodeURIComponent(url.pathname));
    assert.ok(file.startsWith(src+path.sep)&&fs.existsSync(file),"unexpected asset "+url.pathname);
    const type=file.endsWith(".js")||file.endsWith(".jsx") ? "text/javascript" : file.endsWith(".css") ? "text/css" : "application/octet-stream";
    return route.fulfill({contentType:type,body:fs.readFileSync(file)});
  });
  const page=await context.newPage();
  page.on("pageerror",error=>errors.push(error.message));
  const haCalls=()=>requests.filter(r=>r.path.startsWith(EP+"/"));
  const shot=async name=>{if(SHOTS) {fs.mkdirSync(SHOTS,{recursive:true});await page.screenshot({path:path.join(SHOTS,`${mode}-${name}.png`)});}};
  const noFrigateInDom=async label=>{
    const html=await page.evaluate(()=>document.documentElement.outerHTML);
    for(const bad of ["frigate.lan",":5000","/clips/","?path=","frigate_url"]) assert.ok(!html.includes(bad),`${label}: DOM mentions ${bad}`);
    const srcs=await page.evaluate(()=>[...document.querySelectorAll("img,image")].map(i=>i.getAttribute("src")||i.getAttribute("href")||""));
    assert.ok(srcs.every(s=>s.startsWith("blob:")),`${label}: every image is a revocable blob: ${JSON.stringify(srcs)}`);
  };
  try {
    await page.goto(`${HOME}/?mode=${mode==="legacy" ? "legacy" : "agent"}`);
    await page.waitForFunction(()=>window.__bootError||document.querySelector('svg circle,tr[role="button"]'),null,{timeout:20000}).catch(()=>{});
    const bootError=await page.evaluate(()=>window.__bootError||null);
    assert.equal(bootError,null,bootError||"");
    if(mode==="legacy") {
      const body=await page.locator("body").innerText();
      assert.match(body,/legacy read-only · cutover pending/);
      assert.match(body,/verified legacy People view is read-only/);
      assert.doesNotMatch(body,/home agent/i);
      await openRow(page,"Legacy Person");
      await page.waitForFunction(()=>document.querySelector('[aria-label="Identity detail"] input[type="text"]'));
      const detail=await tray(page).innerText();
      assert.match(detail,/legacy read-only · cutover pending/i);
      assert.doesNotMatch(detail,/Home Agent/);
      assert.equal(await tray(page).locator('input[type="text"]').first().inputValue(),"Legacy Person");
      assert.ok(haCalls().some(c=>c.method==="HEAD"&&c.path===`${API}/identity/l-1/avatar`),"legacy avatar probe still runs");
      assert.ok(haCalls().some(c=>c.method==="GET"&&c.path===`${API}/identity/l-1`),"legacy detail is still fetched");
      assert.ok(!requests.some(r=>r.path.startsWith("/api/agent/")),"no Agent read while the legacy store answers");
      assert.ok(!haCalls().some(c=>/agent_profile|frigate_proxy/.test(c.path)),"legacy with no faces flag reads no profiles or Frigate");
    } else {
      // ── roster, banner, headshots ─────────────────────────────────────
      await page.waitForFunction(()=>document.querySelector('svg image[href^="blob:"]'),null,{timeout:10000});
      const body=await page.locator("body").innerText();
      assert.match(body,/home agent/);
      assert.match(body,/recorded there and can be added here/);
      assert.doesNotMatch(body,/read-only/,"the Agent household is no longer read-only");
      assert.equal(await page.getByRole("button",{name:"queue",exact:true}).count(),0,"no queue view for Agent data");
      const avatarGets=haCalls().filter(c=>/\/avatar$/.test(c.path));
      assert.deepEqual(avatarGets.map(c=>[c.method,c.path]).sort(),[
        ["GET",`${API}/agent_profile/${SELF}/avatar`],["GET",`${API}/agent_profile/${ANA}/avatar`]].sort(),
        "headshots only for people whose profile has one, only from the profile store, never probed");
      await shot("graph");

      // ── Ana's tray: profile, relationships, sightings, enrolled ────────
      await openRow(page,"Ana");
      const t=tray(page);
      await t.locator('[data-people-gallery="sightings"] img[src^="blob:"]').first().waitFor({timeout:10000});
      await t.locator('[data-people-gallery="enrolled"] img[src^="blob:"]').first().waitFor({timeout:10000});
      let detail=await t.innerText();
      assert.match(detail,/HOME AGENT/i);
      assert.match(detail,/she\/her/);
      assert.match(detail,/relationships \(3\)/i,"symmetric edges are listed once each");
      for(const line of [/friend of\s+Rui/,/parent of\s+Leo/,/partner of\s+Marcelo/]) assert.match(detail,line);
      assert.match(detail,/recent sightings \(14\)/i);
      assert.match(detail,/enrolled reference photos \(2\)/i);
      assert.match(detail,/view all 14 sightings/);
      assert.doesNotMatch(detail,/HTTP 404|identity not found/i);
      assert.equal(await t.locator('[data-people-gallery="sightings"] button').count(),12);
      assert.equal(await t.locator('[data-people-gallery="enrolled"] button').count(),2);
      assert.ok(await t.locator("img[data-people-headshot]").count()===1,"the tray shows the headshot");
      assert.equal(await t.locator('select[id^="agent-profile-type"]').inputValue(),"partner");
      assert.equal(await t.locator('input[id^="agent-profile-subrole"]').inputValue(),"wife");
      assert.equal(await t.locator('textarea[id^="agent-profile-notes"]').inputValue(),"Prefers warm light");
      const sightingCalls=haCalls().filter(c=>c.path===`${API}/frigate_proxy/events`);
      assert.deepEqual(sightingCalls.map(c=>c.search),["?person=ana&limit=200"]);
      assert.ok(haCalls().some(c=>c.path===`${API}/frigate_proxy/events/1720000780.5-ev13/thumbnail.jpg`),"newest sighting thumbnail via the typed route");
      assert.ok(haCalls().some(c=>c.path===`${API}/frigate_proxy/faces/ana/ana_1720000000.webp`),"newest enrolled photo via the typed route");
      if(mobile) {
        await page.waitForTimeout(400); // let the slide-in animation settle
        const box=await t.boundingBox();
        assert.ok(box && box.x>=0 && box.x+box.width<=375.5,`tray fits the phone: ${JSON.stringify(box)}`);
      }
      await noFrigateInDom("tray");
      await shot("tray");

      // ── full-screen capture browser ───────────────────────────────────
      await t.locator('[data-people-gallery="sightings"] button').first().click();
      await viewer(page).waitFor();
      await viewer(page).locator('img[src^="blob:"]').first().waitFor();
      assert.match(await viewer(page).innerText(),/1 \/ 14 · door/);
      await page.keyboard.press("ArrowRight");
      assert.match(await viewer(page).innerText(),/2 \/ 14 · hall/);
      if(SHOTS) await page.waitForTimeout(400); // let the fade-in finish
      await shot("viewer");
      await page.keyboard.press("Escape");
      await viewer(page).waitFor({state:"detached"});
      assert.equal(await t.count(),1,"Escape closes only the viewer");
      await t.locator('[data-people-gallery="enrolled"] button').first().click();
      assert.match(await viewer(page).innerText(),/1 \/ 2 · enrolled reference/);
      await viewer(page).getByRole("button",{name:"Close capture browser"}).click();
      await viewer(page).waitFor({state:"detached"});

      // ── profile edit ──────────────────────────────────────────────────
      await t.locator('input[id^="agent-profile-subrole"]').fill("spouse");
      await t.getByRole("button",{name:"save profile"}).click();
      await t.getByText("saved",{exact:true}).waitFor();
      const profilePost=posts.find(p=>p.path===`${API}/agent_profile/${ANA}`);
      assert.deepEqual(profilePost?.body,{display_name:"Ana",relationship_subrole:"spouse",notes:"Prefers warm light",relationship_type:"partner"});
      await page.waitForFunction(()=>document.querySelector('input[id^="agent-profile-subrole"]')?.value==="spouse");
      assert.equal(await tray(page).count(),1,"the tray stays open after a save");

      // ── headshot upload ───────────────────────────────────────────────
      await t.getByRole("button",{name:"change headshot"}).click();
      const modal=page.locator('[role="dialog"][aria-label="Avatar editor for Ana"]');
      await modal.locator('input[type="file"]').setInputFiles({name:"ana.png",mimeType:"image/png",buffer:PNG});
      await modal.getByRole("button",{name:"save",exact:true}).click();
      await modal.waitFor({state:"detached"});
      const upload=posts.find(p=>p.path===`${API}/agent_profile/${ANA}/avatar`);
      assert.ok(upload,"the headshot is POSTed to the profile store");
      assert.equal(upload.contentType,"image/jpeg");
      assert.ok(Buffer.isBuffer(upload.body) && upload.body[0]===0xff && upload.body[1]===0xd8 && upload.body.length<=2*1024*1024,"a JPEG under 2 MB");
      const anaAvatar=`${API}/agent_profile/${ANA}/avatar`;
      await page.waitForFunction(()=>document.querySelector('[aria-label="Identity detail"] img[data-people-headshot][src^="blob:"]'),null,{timeout:10000});
      assert.ok(haCalls().filter(c=>c.method==="GET"&&c.path===anaAvatar).length>=2,"the new headshot is fetched again after the upload");

      // ── Leo: no face bucket; add a relationship from his tray ─────────
      await openRow(page,"Leo");
      detail=await tray(page).innerText();
      assert.match(detail,/child of\s+Ana/);
      assert.match(detail,/child of\s+Marcelo/);
      assert.match(detail,/No Frigate face-library entry matches this person/);
      await tray(page).getByRole("button",{name:"+ add relationship"}).click();
      await tray(page).waitFor({state:"detached"});
      const relDialog=page.getByRole("group",{name:"add a relationship"});
      assert.equal(await relDialog.getByLabel("first person").inputValue(),LEO);
      await relDialog.getByLabel("relationship",{exact:true}).selectOption("sibling_of");
      await relDialog.getByLabel("second person").selectOption(RUI);
      assert.match(await relDialog.innerText(),/Records: Leo is sibling of Rui\./);
      if(mobile) await shot("add-relationship");
      await relDialog.getByRole("button",{name:"add relationship"}).click();
      await relDialog.waitFor({state:"detached"});
      const attestation=posts.find(p=>p.path==="/api/agent/v1/partner-attestation");
      assert.equal(attestation?.origin,AGENT,"relationships are written by the Agent-origin bridge");
      assert.equal(attestation.csrf,CSRF);
      assert.deepEqual([attestation.body.subject_person_id,attestation.body.predicate,attestation.body.partner_person_id],[LEO,"sibling_of",RUI]);

      // ── add a person from the banner ──────────────────────────────────
      await page.getByRole("button",{name:"+ add person"}).click();
      const personDialog=page.getByRole("group",{name:"add someone to your household"});
      await personDialog.getByLabel("name").fill("Nova");
      await personDialog.getByLabel("relationship to you").selectOption("friend");
      if(mobile) {
        const overflow=await page.evaluate(()=>document.scrollingElement.scrollWidth-window.innerWidth);
        assert.ok(overflow<=0,`no horizontal overflow on the phone (${overflow}px)`);
      }
      await personDialog.getByRole("button",{name:"add person"}).click();
      await personDialog.waitFor({state:"detached"});
      await page.getByRole("button",{name:"list",exact:true}).click();
      await page.locator('tr[role="button"]',{hasText:"Nova"}).waitFor();
      const created=posts.find(p=>p.path==="/api/agent/v1/household-person");
      assert.equal(created?.origin,AGENT,"people are written by the Agent-origin bridge");
      assert.equal(created.csrf,CSRF);
      assert.deepEqual([created.body.display_name,created.body.privacy_scope],["Nova","household"]);
      const novaProfile=posts.find(p=>p.path===`${API}/agent_profile/${NOVA}`);
      assert.deepEqual(novaProfile?.body,{display_name:"Nova",relationship_type:"friend"},"the new person's ring is recorded");

      // ── containment over the whole run ────────────────────────────────
      assert.deepEqual(requests.filter(r=>/\/identity\//.test(r.path)),[],"no HA identity detail or avatar request for an Agent person");
      assert.ok(!requests.some(r=>r.method==="HEAD" && r.path.startsWith(EP+"/api/")),"no avatar probes");
      assert.ok(!requests.some(r=>r.origin===HOME && r.method==="POST" && r.path.startsWith("/api/agent/")),"Home never writes to the Agent same-origin");
      assert.ok(haCalls().every(c=>TYPED_HA_ROUTES.some(rx=>rx.test(c.path))),"every HA call is a typed route");
      assert.ok(!requests.some(r=>/frigate\.lan|:5000|\/clips\/|[?&]path=/.test(r.path+r.search)),"no Frigate origin or passthrough on the network");
      await noFrigateInDom("end");
    }
    assert.deepEqual(errors,[]);
    console.log("PASS",mode);
  } catch(error) {
    console.error("FAIL",mode,{errors,requests:requests.filter(r=>!/\.(js|jsx)$/.test(r.path)).map(r=>`${r.method} ${r.origin}${r.path}${r.search}`),posts:posts.map(p=>p.path)});
    if(SHOTS) await page.screenshot({path:path.join(SHOTS,`${mode}-failure.png`)}).catch(()=>{});
    throw error;
  } finally {await context.close();}
}

(async()=>{
  const {configFromEnv,createAgentOrigin}=await import("../stack/services/home-agent-origin/src/origin.mjs");
  const originServer=createAgentOrigin(configFromEnv({
    HOME_AGENT_WEB_PUBLIC_ORIGIN:AGENT,HOME_AGENT_WEB_BFF_URL:"http://127.0.0.1:1",
    HOME_AGENT_WEB_ASSET_ROOT:path.join(root,"app/src/home-agent"),HOME_AGENT_WEB_HOST:"127.0.0.1",HOME_AGENT_WEB_PORT:"8096",
    HOME_AGENT_WEB_PREFERENCE_HOME_ORIGINS:HOME,
  }));
  await new Promise((resolve,reject)=>{originServer.once("error",reject);originServer.listen(0,"127.0.0.1",resolve);});
  const browser=await chromium.launch({headless:true});
  try {
    for(const mode of ["agent","agent-mobile","legacy"]) if(!process.argv[2]||process.argv[2]===mode) await scenario(browser,originServer,mode);
    console.log("People tray scenarios passed");
  } finally {
    await browser.close();
    await new Promise(resolve=>originServer.close(resolve));
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
