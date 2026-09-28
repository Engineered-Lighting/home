(function(root) {
  "use strict";
  const document=root.document, api=new root.HomeAgentApi();
  const elements=Object.fromEntries(["status","detail","confirm","check","cancel","sign-in","retry-session"].map(id=>[id,document.getElementById(id)]));
  const nonce=root.location.hash.slice(1);
  const parent=root.opener;
  let origins=[], bound=null, review=null, confirmation=null, pendingRead=false, busy=false, finished=false;
  let expiryTimer;
  const exact=(v,keys)=>v && typeof v==="object" && !Array.isArray(v) && Object.keys(v).sort().join() === [...keys].sort().join();
  const tone=v=>["warm","neutral","cool"].includes(v);
  const setStatus=text=>{elements.status.textContent=text;};
  const send=value=>{
    if(bound) parent.postMessage({version:1,type:"home.preference.result",nonce,...value},bound.origin);
  };
  function end(status,text) {
    finished=true;clearTimeout(expiryTimer);elements.confirm.hidden=true;elements.check.hidden=true;
    setStatus(text);send({status});
  }
  async function session() {
    const value=await api.session();
    if(!value.authenticated || value.personal_memory_enabled!==true ||
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
      setStatus("Ready for your Home request.");
    } catch {
      setStatus("Sign in and enable shared preferences in Home Agent, then check sign-in here.");
      elements["sign-in"].hidden=false;elements["retry-session"].hidden=false;
    }
  }
  async function receive(event) {
    const value=event.data;
    if(finished || bound || event.source!==parent || !origins.includes(event.origin) ||
        !exact(value,["version","type","nonce","intent"]) || value.version!==1 ||
        value.type!=="home.preference.request" || value.nonce!==nonce ||
        !exact(value.intent,["operation","tone"]) || !["read","remember","correct","forget"].includes(value.intent.operation) ||
        (["remember","correct"].includes(value.intent.operation) ? !tone(value.intent.tone) : value.intent.tone!==null)) return;
    bound={origin:event.origin,intent:{...value.intent}};
    busy=true;setStatus("Checking your shared preference…");
    try {
      await session();
      const snapshot=(await api.personalMemory("read")).result;
      if(finished) return;
      if(bound.intent.operation==="read" || bound.intent.operation==="forget" && snapshot.preference===null) {
        const current=snapshot.preference?.value ?? null;
        setStatus(current ? `You prefer ${current} lighting in the evening.` : "No evening lighting preference is saved.");
        pendingRead=true;elements.confirm.textContent="Show in Home";elements.confirm.hidden=false;
        elements.detail.textContent="Only share this preference if you trust the Home conversation that opened this window.";
        return;
      }
      if(bound.intent.operation==="correct" && snapshot.preference===null) {
        end("unavailable","There is no saved preference to correct. Ask Home to remember one first.");return;
      }
      const operation=bound.intent.operation==="forget" ? "forget" : snapshot.preference ? "correct" : "remember";
      const proposed=operation==="forget" ? null : {version:1,kind:"personal_preference",key:"lighting.evening.tone",scope:"owner",value:bound.intent.tone};
      review=(await api.personalMemory("propose",{version:1,operation_id:root.crypto.randomUUID(),operation,
        expected_revision:snapshot.revision,expected_fact_id:snapshot.fact_id,preference:proposed})).result;
      if(finished) return;
      setStatus(operation==="forget" ? "Forget your evening lighting preference in both homes?" :
        `Save ${review.proposed.value} evening lighting as your preference in both homes?`);
      elements.detail.textContent=review.current ? `Currently saved: ${review.current.value}.` : "No preference is currently saved.";
      elements.confirm.textContent="Confirm and show in Home";
      elements.confirm.hidden=false;send({status:"review_pending"});
      expiryTimer=setTimeout(()=>{
        if(!confirmation && !finished) end("expired","This review expired. Make a new request in Home.");
      },Math.max(0,Date.parse(review.expires_at)-Date.now()));
    } catch { if(!finished) end("unavailable","Shared preferences are unavailable. No confirmation was sent."); }
    finally {busy=false;}
  }
  function outcome(result) {
    if(finished) return;
    if(result?.status==="committed") {
      finished=true;elements.check.hidden=true;setStatus("Saved for both homes.");send({status:"saved",tone:review.proposed.value});
    } else if(result?.status==="forgotten") end("forgotten","Forgotten in both homes.");
    else {
      setStatus(result?.status==="ledger_pending" ? "The preference is hidden. Durable forgetting is still pending." :
        "The outcome is not confirmed. Check its status; do not submit again.");
      elements.check.hidden=false;send({status:"pending"});
    }
  }
  elements.confirm.addEventListener("click",async event=>{
    if(event.isTrusted && pendingRead && !busy && !finished) {
      busy=true;elements.confirm.hidden=true;
      try {
        await session();const snapshot=(await api.personalMemory("read")).result;
        if(!finished) {send({status:"read",tone:snapshot.preference?.value ?? null});finished=true;setStatus("Shown in Home.");}
      } catch {if(!finished) end("unavailable","The preference could not be shared. Sign in again if your session changed.");}
      finally {busy=false;}
      return;
    }
    if(!event.isTrusted || busy || finished || confirmation || !review || Date.now()>=Date.parse(review.expires_at)) return;
    busy=true;elements.confirm.hidden=true;clearTimeout(expiryTimer);
    confirmation={version:1,operation_id:review.operation_id,reviewed_digest:review.reviewed_digest};
    try {
      await session();
      outcome((await api.personalMemory("confirm",{...confirmation,gesture_id:root.crypto.randomUUID()})).result);
    } catch { outcome(null); }
    finally {busy=false;}
  });
  elements.check.addEventListener("click",async event=>{
    if(!event.isTrusted || busy || finished || !confirmation) return;
    busy=true;
    try {await session();outcome((await api.personalMemory("outcome",confirmation)).result);}
    catch {outcome(null);}
    finally {busy=false;}
  });
  elements.cancel.addEventListener("click",()=>{
    if(confirmation) {setStatus("A confirmation was sent. Use Check outcome to resolve it; closing cannot undo it.");return;}
    end("cancelled","Cancelled. No confirmation was sent.");
  });
  elements["retry-session"].addEventListener("click",authenticate);
  root.addEventListener("message",receive);
  root.addEventListener("pagehide",()=>{finished=true;clearTimeout(expiryTimer);api.invalidateAuthority?.();});
  if(root.top!==root || !parent || !/^[a-f0-9]{32}$/.test(nonce)) end("unavailable","Open this review from a preference request in Home.");
  else {root.history.replaceState(null,"",root.location.pathname);authenticate();}
})(window);
