(function(root) {
  "use strict";
  // Inline shared-preference review. Home frames this Agent-origin page inside
  // the chat card; only the provisioned Home origins may frame it (Origin CSP
  // frame-ancestors). The Agent session cookie, CSRF token and API calls stay
  // on this origin: the Home page sees only the nonce-bound result messages.
  const document=root.document, api=new root.HomeAgentApi();
  const elements=Object.fromEntries(["status","detail","confirm","check","cancel","sign-in","retry-session"].map(id=>[id,document.getElementById(id)]));
  // Home passes "#<nonce>" or "#<nonce>.<theme>" so the card follows Home's theme.
  const [, nonce="", theme="dark"]=/^#([a-f0-9]{32})(?:\.(dark|light))?$/.exec(root.location.hash) || [];
  document.documentElement.dataset.theme=theme;
  const parent=root.parent;
  const ARM_DELAY_MS=500;
  let origins=[], bound=null, review=null, confirmation=null, busy=false, finished=false;
  let expiryTimer, armTimer, armed=false, lastHeight=0;
  const exact=(v,keys)=>v && typeof v==="object" && !Array.isArray(v) && Object.keys(v).sort().join() === [...keys].sort().join();
  const tone=v=>["warm","neutral","cool"].includes(v);
  const setStatus=text=>{elements.status.textContent=text;resize();};
  const post=value=>{if(bound) parent.postMessage({version:1,nonce,...value},bound.origin);};
  const send=value=>post({type:"home.preference.result",...value});
  function resize() {
    const height=Math.ceil(document.documentElement.getBoundingClientRect().height);
    if(!bound || height===lastHeight) return;
    lastHeight=height;post({type:"home.preference.size",height});
  }
  function end(status,text) {
    finished=true;clearTimeout(expiryTimer);disarm();
    elements.confirm.hidden=true;elements.check.hidden=true;elements.cancel.hidden=true;
    setStatus(text);send({status});
  }
  // Confirm accepts only a trusted click after the button has been fully on
  // screen for a moment. This blocks instant or off-screen click placement; it
  // cannot stop a script already running in Home from styling the frame.
  function disarm() {armed=false;clearTimeout(armTimer);elements.confirm.disabled=true;}
  const visibility=new root.IntersectionObserver(entries=>{
    const entry=entries.at(-1);
    disarm();
    if(entry.isIntersecting && entry.intersectionRatio>=0.99 && document.visibilityState==="visible" && !elements.confirm.hidden) {
      armTimer=setTimeout(()=>{if(!elements.confirm.hidden && !finished) {armed=true;elements.confirm.disabled=false;}},ARM_DELAY_MS);
    }
  },{threshold:[0,0.99,1]});
  function showConfirm(label) {
    elements.confirm.textContent=label;elements.confirm.hidden=false;elements.cancel.hidden=false;disarm();
    visibility.unobserve(elements.confirm);visibility.observe(elements.confirm);resize();
  }
  document.addEventListener("visibilitychange",()=>{
    if(document.visibilityState!=="visible") disarm();
    else if(!elements.confirm.hidden) {visibility.unobserve(elements.confirm);visibility.observe(elements.confirm);}
  });
  async function session() {
    const value=await api.session();
    if(!value.authenticated) throw Object.assign(new Error("signed out"),{signedOut:true});
    if(value.personal_memory_enabled!==true ||
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
    } catch(error) {
      setStatus(error?.signedOut ? "Your Home Agent sign-in has expired. Sign in, then check sign-in here." :
        "Shared preferences aren't available for this Home Agent sign-in.");
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
      // Reads need no review: the frame answers the Home origin that framed it.
      if(bound.intent.operation==="read" || bound.intent.operation==="forget" && snapshot.preference===null) {
        const current=snapshot.preference?.value ?? null;
        finished=true;elements.cancel.hidden=true;
        setStatus(current ? `You prefer ${current} lighting in the evening.` : "No evening lighting preference is saved.");
        send({status:"read",tone:current});
        return;
      }
      if(bound.intent.operation==="correct" && snapshot.preference===null) {
        end("absent","There is no saved preference to change.");return;
      }
      const operation=bound.intent.operation==="forget" ? "forget" : snapshot.preference ? "correct" : "remember";
      const proposed=operation==="forget" ? null : {version:1,kind:"personal_preference",key:"lighting.evening.tone",scope:"owner",value:bound.intent.tone};
      review=(await api.personalMemory("propose",{version:1,operation_id:root.crypto.randomUUID(),operation,
        expected_revision:snapshot.revision,expected_fact_id:snapshot.fact_id,preference:proposed})).result;
      if(finished) return;
      elements.detail.textContent=review.current ? `Currently saved: ${review.current.value}.` : "No preference is currently saved.";
      setStatus(operation==="forget" ? "Forget your evening lighting preference in both homes?" :
        `Save ${review.proposed.value} evening lighting as your preference in both homes?`);
      showConfirm(operation==="forget" ? "forget" : "confirm");
      send({status:"review_pending"});
      expiryTimer=setTimeout(()=>{
        if(!confirmation && !finished) end("expired","This review expired. No change was made.");
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
      // An unknown outcome offers lookup only: never a second confirm or a cancel.
      elements.cancel.hidden=true;elements.check.hidden=false;
      setStatus(result?.status==="ledger_pending" ? "The preference is hidden. Durable forgetting is still pending." :
        "The outcome is not confirmed yet. Check its status; do not submit again.");
      send({status:"pending"});
    }
  }
  elements.confirm.addEventListener("click",async event=>{
    if(!event.isTrusted || !armed || busy || finished || confirmation || !review || Date.now()>=Date.parse(review.expires_at)) return;
    busy=true;elements.confirm.hidden=true;elements.cancel.hidden=true;disarm();clearTimeout(expiryTimer);
    confirmation={version:1,operation_id:review.operation_id,reviewed_digest:review.reviewed_digest};
    setStatus("Confirming…");send({status:"confirming"});
    try {
      await session();
      outcome((await api.personalMemory("confirm",{...confirmation,gesture_id:root.crypto.randomUUID()})).result);
    } catch { outcome(null); }
    finally {busy=false;}
  });
  elements.check.addEventListener("click",async event=>{
    if(!event.isTrusted || busy || finished || !confirmation) return;
    busy=true;elements.check.disabled=true;
    try {await session();outcome((await api.personalMemory("outcome",confirmation)).result);}
    catch {outcome(null);}
    finally {busy=false;elements.check.disabled=false;}
  });
  elements.cancel.addEventListener("click",event=>{
    if(!event.isTrusted || busy || finished || confirmation || !bound) return;
    end("cancelled","Cancelled. No confirmation was sent.");
  });
  elements["retry-session"].addEventListener("click",authenticate);
  root.addEventListener("message",receive);
  root.addEventListener("resize",resize);
  root.addEventListener("pagehide",()=>{finished=true;clearTimeout(expiryTimer);disarm();api.invalidateAuthority?.();});
  if(root.top===root || root.top!==parent || !nonce) end("unavailable","Open this review from a preference request in the Home chat.");
  else {root.history.replaceState(null,"",root.location.pathname);authenticate();}
})(window);
