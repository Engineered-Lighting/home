(function(root) {
  "use strict";
  // Inline cross-home lighting. Home frames this Agent-origin page inside the
  // chat card; only the provisioned Home origins may frame it (Origin CSP
  // frame-ancestors). The Agent session cookie, CSRF token and API calls stay on
  // this origin: Home sends only a typed request and receives only nonce-bound
  // result messages.
  //
  // The owner chose (2026-09-29) direct execution, like Los Angeles's own chat:
  // once lighting control is allowed in the Agent panel, a request that resolves
  // to exact allowlisted lights is carried out immediately, without a second
  // click. Unclear requests still come back as questions, and an uncertain
  // result offers a status check only; nothing is ever sent twice.
  const document=root.document, api=new root.HomeAgentApi();
  const elements=Object.fromEntries(["status","changes","detail","check","sign-in","retry-session"]
    .map(id=>[id,document.getElementById(id)]));
  const nonce=root.location.hash.slice(1);
  const parent=root.parent;
  const SITES=["echo","victoria"], OPERATIONS=["on","off","brightness"];
  const HOME={echo:"Los Angeles",victoria:"Victoria"};
  const RESULTS=["succeeded","failed","not_sent","unknown","pending"];
  const CLARIFY=["unknown_light","ambiguous_light","no_eligible_lights","not_dimmable","light_unavailable","too_many_lights"];
  let origins=[], bound=null, operationId=null, busy=false, finished=false, lastHeight=0;
  const exact=(v,keys)=>v && typeof v==="object" && !Array.isArray(v) && Object.keys(v).sort().join()===[...keys].sort().join();
  const name=v=>typeof v==="string" && v.length>=1 && v.length<=120 && !/[\u0000-\u001f\u007f]/.test(v);
  const change=op=>op.operation==="brightness" ? `set to ${op.brightness}%` : `turn ${op.operation}`;
  const setStatus=text=>{elements.status.textContent=text;resize();};
  const post=value=>{if(bound) parent.postMessage({version:1,nonce,...value},bound.origin);};
  const send=value=>post({type:"home.lighting.result",...value});
  function resize() {
    const height=Math.ceil(document.documentElement.getBoundingClientRect().height);
    if(!bound || height===lastHeight) return;
    lastHeight=height;post({type:"home.lighting.size",height});
  }
  function end(status,text,extra={}) {
    finished=true;elements.check.hidden=true;setStatus(text);send({status,...extra});
  }
  function validRequest(v) {
    return exact(v,["sites","targets","operation","brightness"]) && Array.isArray(v.sites) && v.sites.length>=1 &&
      v.sites.length<=2 && new Set(v.sites).size===v.sites.length && v.sites.every(s=>SITES.includes(s)) &&
      (v.targets==="all" || Array.isArray(v.targets) && v.targets.length>=1 && v.targets.length<=8 &&
        new Set(v.targets).size===v.targets.length && v.targets.every(t=>typeof t==="string" && /^[a-z0-9][a-z0-9 '\-]{0,39}$/.test(t))) &&
      OPERATIONS.includes(v.operation) &&
      (v.operation==="brightness" ? Number.isInteger(v.brightness) && v.brightness>=1 && v.brightness<=100 : v.brightness===null);
  }
  function validSummary(value) {
    return exact(value,["version","operation_id","status","results"]) && value.version===1 &&
      ["done","partial","failed","unknown"].includes(value.status) && Array.isArray(value.results) &&
      value.results.length>=1 && value.results.length<=16 && value.results.every(r=>
        exact(r,["site_id","name","operation","brightness","status"]) && SITES.includes(r.site_id) && name(r.name) &&
        OPERATIONS.includes(r.operation) && RESULTS.includes(r.status));
  }
  async function session() {
    const value=await api.session();
    if(!value.authenticated || value.lighting_enabled!==true ||
        !Array.isArray(value.personal_memory_home_origins) || !value.personal_memory_home_origins.length) throw new Error("unavailable");
    origins=value.personal_memory_home_origins.filter(origin=>{
      try {const url=new URL(origin);return url.protocol==="https:" && url.origin===origin && url.origin!==root.location.origin;}
      catch {return false;}
    });
    if(bound && !origins.includes(bound.origin)) throw new Error("authority changed");
    return value;
  }
  async function authenticate() {
    try {
      await session();
      elements["sign-in"].hidden=true;elements["retry-session"].hidden=true;
      setStatus("Connecting to your Home conversation…");
    } catch {
      setStatus("Sign in to Home Agent and allow lighting control, then check sign-in.");
      elements["sign-in"].hidden=false;elements["retry-session"].hidden=false;
    }
  }
  function showChanges(operations) {
    elements.changes.replaceChildren(...operations.map(op=>{
      const item=document.createElement("li");
      item.textContent=`${HOME[op.site_id]} · ${op.name}: ${change(op)}`;
      return item;
    }));
    elements.changes.hidden=false;
  }
  function outcome(summary) {
    if(finished) return;
    if(summary && validSummary(summary) && summary.status!=="unknown") {
      showChanges(summary.results);
      end(summary.status,summary.status==="done" ? "Done." : summary.status==="failed" ? "No light was changed." :
        "Some lights were not changed.",{results:summary.results.map(r=>({site_id:r.site_id,name:r.name,
          operation:r.operation,brightness:r.brightness,status:r.status}))});
      return;
    }
    // An uncertain result offers lookup only; the request is never sent again.
    elements.check.hidden=false;
    setStatus("The result is not confirmed yet. Check its status; do not ask again.");
    send({status:"pending"});
  }
  async function receive(event) {
    const value=event.data;
    if(finished || bound || event.source!==parent || !origins.includes(event.origin) ||
        !exact(value,["version","type","nonce","request"]) || value.version!==1 ||
        value.type!=="home.lighting.request" || value.nonce!==nonce || !validRequest(value.request)) return;
    bound={origin:event.origin,request:structuredClone(value.request)};
    busy=true;setStatus("Checking the lights…");
    let review=null;
    try {
      await session();
      const result=(await api.lighting("propose",{version:1,operation_id:root.crypto.randomUUID(),...bound.request})).result;
      if(finished) return;
      if(result?.status==="clarify") {
        const c=result.clarification;
        if(!exact(c,["version","reason","site_id","target","candidates"]) || !CLARIFY.includes(c.reason) ||
            !SITES.includes(c.site_id) || !Array.isArray(c.candidates) || !c.candidates.every(name)) throw new Error("invalid");
        end("clarify","Nothing was changed.",{clarification:{reason:c.reason,site_id:c.site_id,target:c.target,
          candidates:c.candidates.slice(0,8)}});
        return;
      }
      if(result?.status!=="review" || !exact(result.review,["version","operation_id","expires_at","reviewed_digest","operations"]) ||
          !Array.isArray(result.review.operations) || !result.review.operations.length) throw new Error("invalid");
      review=result.review;
    } catch (error) {
      busy=false;
      if(finished) return;
      if(error?.status===403 && error.message==="lighting_not_permitted") {
        end("not_permitted","Lighting control is not allowed yet. Nothing was changed.");
      } else end("unavailable","Lighting is unavailable. Nothing was changed.");
      return;
    }
    showChanges(review.operations);
    setStatus("Switching…");
    operationId=review.operation_id;
    send({status:"confirming"});
    try {
      outcome((await api.lighting("confirm",{version:1,operation_id:review.operation_id,
        reviewed_digest:review.reviewed_digest})).result);
    } catch { outcome(null); }
    finally {busy=false;}
  }
  elements.check.addEventListener("click",async event=>{
    if(!event.isTrusted || busy || finished || !operationId) return;
    busy=true;elements.check.disabled=true;
    try {await session();outcome((await api.lighting("outcome",{version:1,operation_id:operationId})).result);}
    catch {outcome(null);}
    finally {busy=false;elements.check.disabled=false;}
  });
  elements["retry-session"].addEventListener("click",authenticate);
  root.addEventListener("message",receive);
  root.addEventListener("resize",resize);
  root.addEventListener("pagehide",()=>{finished=true;api.invalidateAuthority?.();});
  if(root.top===root || root.top!==parent || !/^[a-f0-9]{32}$/.test(nonce)) end("unavailable","Open this from a lighting request in the Home chat.");
  else {root.history.replaceState(null,"",root.location.pathname);authenticate();}
})(window);
