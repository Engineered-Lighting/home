(function(root) {
  "use strict";
  let active=null;
  const exact=(v,keys)=>v && typeof v==="object" && !Array.isArray(v) && Object.keys(v).sort().join()===[...keys].sort().join();
  function parse(text) {
    if(typeof text!=="string" || text.length>180) return null;
    const value=text.trim().replace(/[.!?]$/,"").toLowerCase();
    if(/^(?:what lighting do i prefer in the evening|what is my evening lighting preference)$/.test(value)) return {operation:"read",tone:null};
    if(/^forget my evening lighting preference$/.test(value)) return {operation:"forget",tone:null};
    const match=/^(remember that |actually,? )?i prefer (warm|neutral|cool) lighting (?:in the evenings?|at night)$/.exec(value);
    return match ? {operation:match[1]?.startsWith("actually") ? "correct" : "remember",tone:match[2]} : null;
  }
  function reset() {active?.cancel();active=null;}
  function run(text,options) {
    const intent=parse(text);
    reset();
    if(!intent) return false;
    const emit=event=>options.addEvent({...event,privateMemoryContext:true,privateCameraContext:true});
    emit({kind:"user",text});
    if(!root.HG_WEB_MODE) {
      emit({kind:"home",text:"Shared preferences currently require authenticated Home web."});return true;
    }
    let origin;
    try {
      const url=new URL(root.HG_AGENT_ORIGIN);
      if(url.protocol!=="https:" || url.origin!==root.HG_AGENT_ORIGIN || url.origin===root.location.origin) throw new Error();
      origin=url.origin;
    } catch {emit({kind:"home",text:"The trusted preference review is not configured yet."});return true;}
    const nonce=root.crypto.randomUUID().replace(/-/g,"");
    let popup=null,timer,deadline,done=false;
    const valid=()=>!done && options.isCurrent();
    const dispose=()=>{
      done=true;clearInterval(timer);clearTimeout(deadline);root.removeEventListener("message",receive);
      if(active===handle) active=null;
    };
    const receive=event=>{
      const value=event.data;
      if(!valid() || event.origin!==origin || event.source!==popup || !value || value.version!==1 ||
          value.type!=="home.preference.result" || value.nonce!==nonce) return;
      const withTone=["read","saved"].includes(value.status);
      if(!exact(value,withTone ? ["version","type","nonce","status","tone"] : ["version","type","nonce","status"]) ||
          withTone && !(value.status==="read" && value.tone===null || ["warm","neutral","cool"].includes(value.tone))) return;
      const messages={review_pending:"Review this change in the trusted Home preference window. It applies to both homes.",
        pending:"The preference operation is not yet confirmed. Check its outcome in the review window; do not submit it again.",
        forgotten:"Your evening lighting preference has been forgotten in both homes.",
        cancelled:"Preference review cancelled. No confirmation was sent.",
        expired:"The preference review expired. Make a new request to continue.",
        unavailable:"Shared preferences are unavailable. Check the trusted review window."};
      const message=value.status==="read" ? (value.tone ? `You prefer ${value.tone} lighting in the evening. This preference is shared across both homes.` : "No evening lighting preference is saved.") :
        value.status==="saved" ? `Saved: ${value.tone} lighting in the evening, shared across both homes. Your lights have not been changed.` : messages[value.status];
      if(!message) return;
      emit({kind:"home",text:message});
      if(!["review_pending","pending"].includes(value.status)) dispose();
    };
    const open=()=>{
      if(!valid() || popup && !popup.closed) return;
      popup=root.open(origin+"/home-agent/preference-review.html#"+nonce,"home_preference_"+nonce,"popup,width=520,height=640");
      if(!popup) return false;
      return true;
    };
    const handle={cancel(){dispose();try {popup?.close();} catch {}}};
    active=handle;root.addEventListener("message",receive);
    timer=setInterval(()=>{
      if(!valid()) {handle.cancel();return;}
      if(popup?.closed) {dispose();return;}
      popup?.postMessage({version:1,type:"home.preference.request",nonce,intent},origin);
    },500);
    deadline=setTimeout(()=>{
      if(valid()) emit({kind:"home",text:"The preference review connection expired. An already-sent confirmation may still have completed; do not assume it was cancelled."});
      dispose();
    },300000);
    const opened=open();
    emit({kind:"personal-memory-launch",text:opened ? "Opening your trusted Home preference review…" : "Open the trusted Home preference review to continue.",openReview:open});
    return true;
  }
  root.addEventListener("pagehide",reset);
  root.HomePersonalMemory=Object.freeze({parse,run,reset});
})(window);
