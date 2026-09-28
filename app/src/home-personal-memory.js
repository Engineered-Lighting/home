(function(root) {
  "use strict";
  let active=null;
  const exact=(v,keys)=>v && typeof v==="object" && !Array.isArray(v) && Object.keys(v).sort().join()===[...keys].sort().join();
  const STATUSES=new Set(["review_pending","confirming","pending","forgotten","cancelled","expired","unavailable","absent","read","saved"]);
  function parse(text) {
    if(typeof text!=="string" || text.length>180) return null;
    const value=text.trim().replace(/[.!?]$/,"").toLowerCase();
    if(/^(?:what lighting do i prefer in the evening|what is my evening lighting preference)$/.test(value)) return {operation:"read",tone:null};
    if(/^forget my evening lighting preference$/.test(value)) return {operation:"forget",tone:null};
    const match=/^(remember that |actually,? )?i prefer (warm|neutral|cool) lighting (?:in the evenings?|at night)$/.exec(value);
    return match ? {operation:match[1]?.startsWith("actually") ? "correct" : "remember",tone:match[2]} : null;
  }
  function reset() {active?.cancel();active=null;}
  // The review is an Agent-origin page framed inline in the chat card. Its
  // session, CSRF token and API calls never reach this page; Home receives only
  // results bound to this card's nonce, from that exact frame and origin.
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
    const frame=root.document.createElement("iframe");
    frame.src=origin+"/home-agent/preference-review.html#"+nonce;
    frame.title="Shared preference review";
    frame.referrerPolicy="no-referrer";
    frame.setAttribute("allow","");
    frame.style.cssText="display:block;width:100%;max-width:520px;height:176px;border:0;border-radius:10px;background:#141a18;color-scheme:dark";
    let timer,deadline,done=false,loads=0,confirming=false;
    const valid=()=>!done && options.isCurrent();
    const dispose=()=>{
      done=true;clearInterval(timer);clearTimeout(deadline);root.removeEventListener("message",receive);
      frame.remove();
      if(active===handle) active=null;
    };
    const reply=text=>emit({kind:"home",text});
    const receive=event=>{
      const value=event.data;
      if(!valid() || event.origin!==origin || event.source!==frame.contentWindow || !value || value.version!==1 || value.nonce!==nonce) return;
      if(value.type==="home.preference.size") {
        if(exact(value,["version","type","nonce","height"]) && Number.isInteger(value.height) && value.height>=48 && value.height<=640) frame.style.height=value.height+"px";
        return;
      }
      const withTone=["read","saved"].includes(value.status);
      if(value.type!=="home.preference.result" || !STATUSES.has(value.status) ||
          !exact(value,["version","type","nonce","status",...(withTone ? ["tone"] : [])])) return;
      if(withTone && !(value.status==="read" && value.tone===null || ["warm","neutral","cool"].includes(value.tone))) return;
      if(value.status==="review_pending") return;
      if(value.status==="confirming") {confirming=true;return;}
      const messages={
        pending:"The change is not confirmed yet. Use Check outcome in the card; do not ask again.",
        forgotten:"Forgotten: your evening lighting preference is gone from both homes.",
        cancelled:"Cancelled. Nothing was changed.",
        expired:"That review expired before it was confirmed. Nothing was changed; ask again to continue.",
        absent:"There is no saved evening lighting preference to change. Ask me to remember one first.",
        unavailable:"Shared preferences are unavailable right now. Nothing was confirmed.",
      };
      const message=value.status==="read" ? (value.tone ? `You prefer ${value.tone} lighting in the evening. This preference is shared across both homes.` : "No evening lighting preference is saved.") :
        value.status==="saved" ? `Saved: ${value.tone} lighting in the evening, for both homes. Your lights have not been changed.` : messages[value.status];
      reply(message);
      // A pending outcome keeps the card so its lookup stays reachable.
      if(value.status!=="pending") dispose();
    };
    frame.addEventListener("load",()=>{
      // A reloaded or re-attached frame lost its nonce; never re-bind it.
      if(++loads===1 || done) return;
      if(valid()) reply(confirming ? "The preference review closed after a confirmation was sent. It may have completed; ask what I prefer to check before trying again." :
        "The preference review closed. Nothing was confirmed.");
      dispose();
    });
    const handle={cancel(){dispose();}};
    active=handle;root.addEventListener("message",receive);
    timer=setInterval(()=>{
      if(!valid()) {handle.cancel();return;}
      frame.contentWindow?.postMessage({version:1,type:"home.preference.request",nonce,intent},origin);
    },500);
    deadline=setTimeout(()=>{
      if(valid()) reply(confirming ? "The preference review timed out after a confirmation was sent. It may have completed; ask what I prefer to check." :
        "The preference review timed out. Nothing was confirmed.");
      dispose();
    },300000);
    const intro={read:"Checking your shared preference…",forget:"Review below to forget this preference in both homes.",
      remember:"Review below to save this preference for both homes.",correct:"Review below to update this preference for both homes."}[intent.operation];
    emit({kind:"personal-memory-review",text:intro,mountReview(container) {
      if(done || !container) return ()=>{};
      if(frame.parentNode!==container) container.appendChild(frame);
      return ()=>{if(frame.parentNode===container && !done) frame.remove();};
    }});
    return true;
  }
  root.addEventListener("pagehide",reset);
  root.HomePersonalMemory=Object.freeze({parse,run,reset});
})(window);
