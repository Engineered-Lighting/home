(function(root) {
  "use strict";
  // Explicit cross-home lighting in the Home chat. HomeLightingIntent turns the
  // owner's words into a typed request; the review is an Agent-origin page
  // framed inline in the chat card (like the shared-preference review). Its
  // session, CSRF token and API calls never reach this page: Home sends only the
  // typed request and receives only results bound to this card's nonce, from
  // that exact frame and origin. At the owner's choice, a clear request is
  // carried out directly (after the one-time consent); unclear ones are asked.
  let active=null;
  const HOME={echo:"Los Angeles",victoria:"Victoria"};
  const SITES=["echo","victoria"], OPERATIONS=["on","off","brightness"];
  const RESULTS=["succeeded","failed","not_sent","unknown","pending"];
  const exact=(v,keys)=>v && typeof v==="object" && !Array.isArray(v) && Object.keys(v).sort().join()===[...keys].sort().join();
  const clean=v=>String(v).replace(/[\u0000-\u001f\u007f*_`[\]<>#]/g,"").slice(0,120);
  const quote=v=>`"${clean(v)}"`;
  function reset() {active?.cancel();active=null;}

  function clarification(c) {
    const home=HOME[c.site_id], names=c.candidates.map(clean).join(", ");
    return {
      unknown_light:`I couldn't find ${quote(c.target)} in ${home}.${names ? ` Lights there: ${names}.` : ""} Nothing was changed.`,
      ambiguous_light:`${quote(c.target)} matches more than one light in ${home}: ${names}. Which one? Nothing was changed.`,
      no_eligible_lights:`There are no lights I can switch in ${home} right now. Nothing was changed.`,
      not_dimmable:`${quote(c.target)} in ${home} can only be turned on or off. Nothing was changed.`,
      light_unavailable:`${quote(c.target)} in ${home} is unavailable right now. Nothing was changed.`,
      too_many_lights:"That is more than 16 lights at once. Name fewer lights. Nothing was changed.",
    }[c.reason];
  }
  function summary(status,results) {
    const verb=r=>r.operation==="brightness" ? `set to ${r.brightness}%` : `turned ${r.operation}`;
    const line=r=>`${clean(r.name)} (${HOME[r.site_id]}): ${r.status==="succeeded" ? verb(r) :
      r.status==="unknown" || r.status==="pending" ? "result not confirmed" : "not changed"}`;
    const head=status==="done" ? "Done." : status==="failed" ? "No lights were changed." : "Some lights were not changed.";
    return `${head} ${results.map(line).join("; ")}.`;
  }

  function run(text,options) {
    const parsed=root.HomeLightingIntent?.parse(text,options.viewedHome);
    reset();
    if(!parsed) return false;
    const emit=event=>options.addEvent({...event,privateMemoryContext:true,privateCameraContext:true});
    emit({kind:"user",text});
    if(parsed.clarify) {emit({kind:"home",text:parsed.message});return true;}
    if(!root.HG_WEB_MODE) {
      emit({kind:"home",text:"Lighting in both homes currently requires authenticated Home web."});return true;
    }
    let origin;
    try {
      const url=new URL(root.HG_AGENT_ORIGIN);
      if(url.protocol!=="https:" || url.origin!==root.HG_AGENT_ORIGIN || url.origin===root.location.origin) throw new Error();
      origin=url.origin;
    } catch {emit({kind:"home",text:"The trusted lighting review is not configured yet."});return true;}
    const request=parsed.request;
    const nonce=root.crypto.randomUUID().replace(/-/g,"");
    const frame=root.document.createElement("iframe");
    frame.src=origin+"/home-agent/lighting-review.html#"+nonce;
    frame.title="Lighting review";
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
    const reply=message=>emit({kind:"home",text:message});
    const receive=event=>{
      const value=event.data;
      if(!valid() || event.origin!==origin || event.source!==frame.contentWindow || !value || value.version!==1 || value.nonce!==nonce) return;
      if(value.type==="home.lighting.size") {
        if(exact(value,["version","type","nonce","height"]) && Number.isInteger(value.height) && value.height>=48 && value.height<=640) frame.style.height=value.height+"px";
        return;
      }
      if(value.type!=="home.lighting.result" || typeof value.status!=="string") return;
      const status=value.status;
      if(["review_pending","confirming"].includes(status)) {
        if(exact(value,["version","type","nonce","status"]) && status==="confirming") confirming=true;
        return;
      }
      if(status==="clarify") {
        const c=value.clarification;
        if(!exact(value,["version","type","nonce","status","clarification"]) ||
            !exact(c,["reason","site_id","target","candidates"]) || !SITES.includes(c.site_id) ||
            !Array.isArray(c.candidates) || c.candidates.length>8 || !c.candidates.every(n=>typeof n==="string")) return;
        const message=clarification(c);
        if(!message) return;
        reply(message);dispose();return;
      }
      if(["done","partial","failed"].includes(status)) {
        const results=value.results;
        if(!exact(value,["version","type","nonce","status","results"]) || !Array.isArray(results) || !results.length ||
            results.length>16 || !results.every(r=>exact(r,["site_id","name","operation","brightness","status"]) &&
              SITES.includes(r.site_id) && typeof r.name==="string" && OPERATIONS.includes(r.operation) && RESULTS.includes(r.status))) return;
        reply(summary(status,results));dispose();return;
      }
      if(!exact(value,["version","type","nonce","status"])) return;
      const messages={
        pending:"The lighting result is not confirmed yet. Use Check outcome in the card; do not ask again.",
        not_permitted:"Lighting control between homes is not allowed yet. In the Home Agent panel, choose Review lighting control. Nothing was changed.",
        expired:"That request expired before it was sent. Nothing was changed; ask again to continue.",
        unavailable:"Lighting is unavailable right now. Nothing was changed.",
      };
      if(!messages[status]) return;
      reply(messages[status]);
      // A pending outcome keeps the card so its lookup stays reachable.
      if(status!=="pending") dispose();
    };
    frame.addEventListener("load",()=>{
      // A reloaded or re-attached frame lost its nonce; never re-bind it.
      if(++loads===1 || done) return;
      if(valid()) reply(confirming ? "The lighting card closed after the request was sent. Lights may have changed; check them before asking again." :
        "The lighting card closed. Nothing was changed.");
      dispose();
    });
    const handle={cancel(){dispose();}};
    active=handle;root.addEventListener("message",receive);
    timer=setInterval(()=>{
      if(!valid()) {handle.cancel();return;}
      frame.contentWindow?.postMessage({version:1,type:"home.lighting.request",nonce,request},origin);
    },500);
    deadline=setTimeout(()=>{
      if(valid()) reply(confirming ? "The lighting card timed out after the request was sent. Lights may have changed; check them before asking again." :
        "The lighting card timed out. Nothing was changed.");
      dispose();
    },300000);
    // The generic inline-review card (shared with preference reviews) mounts the frame.
    emit({kind:"personal-memory-review",text:"Switching lights…",mountReview(container) {
      if(done || !container) return ()=>{};
      if(frame.parentNode!==container) container.appendChild(frame);
      return ()=>{if(frame.parentNode===container && !done) frame.remove();};
    }});
    return true;
  }
  root.addEventListener("pagehide",reset);
  root.HomeLightingControl=Object.freeze({run,reset});
})(window);
