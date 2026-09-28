"use strict";
(() => {
  const el=id=>document.getElementById(id);
  let session=null,pairingId=null,busy=false,blocked=false,epoch=0,expiry=0,haOrigin=null;
  const status=text=>{el("status").textContent=text;};
  const reset=()=>{
    epoch++; session=null;blocked=true;expiry=0;
    el("pairing").hidden=true;el("auth-form").replaceChildren();el("auth-form").hidden=true;
    el("code").value="";el("code-label").hidden=true;el("logout").hidden=true;
  };
  async function request(path,body,method="POST") {
    const generation=epoch,headers={Accept:"application/json"};
    if(body!==undefined)headers["Content-Type"]="application/json";
    if(session && method!=="GET")headers["X-CSRF-Token"]=session.csrf_token;
    const response=await fetch(path,{method,credentials:"same-origin",redirect:"error",cache:"no-store",headers,
      ...(body===undefined?{}:{body:JSON.stringify(body)})});
    const result=await response.json();
    if(generation!==epoch)throw new Error("session_changed");
    if(response.status===401){reset();el("login").hidden=false;}
    if(!response.ok)throw new Error("unavailable");
    return result;
  }
  async function run(work) {
    if(busy)return;
    busy=true;document.querySelectorAll("button").forEach(button=>button.disabled=true);
    try {await work();} catch (_) {status("No verified result was returned. Check the original authentication status or sign in again; nothing was retried.");}
    finally {busy=false;document.querySelectorAll("button").forEach(button=>button.disabled=false);}
  }
  function authenticationResult(value) {
    if(blocked)return;
    if(value?.status==="authenticated" && value.site_id==="victoria" && value.ceremony_id===pairingId) {
      el("auth-form").replaceChildren();el("auth-form").hidden=true;el("recover").hidden=true;
      status("Victoria verified. Return to Home, check Victoria authentication, then review both accounts.");return;
    }
    if(value?.status!=="form" || !/^[a-f0-9]{64}$/.test(value.handle || "") || !Array.isArray(value.fields) ||
      !value.fields.length || value.fields.length>8 || new Set(value.fields).size!==value.fields.length ||
      value.fields.some(field=>!["username","password","code","multi_factor_auth_module"].includes(field)))throw new Error();
    const form=el("auth-form");form.replaceChildren();form.hidden=false;
    for(const field of value.fields){
      const label=document.createElement("label");label.textContent=({username:"Username",password:"Password",code:"Authentication code",multi_factor_auth_module:"Verification method"})[field];
      const input=document.createElement(field==="multi_factor_auth_module"?"select":"input");input.name=field;input.required=true;
      if(field==="multi_factor_auth_module"){
        if(!Array.isArray(value.choices) || value.choices.length>16)throw new Error();
        for(const [key,text] of value.choices){const option=document.createElement("option");option.value=key;option.textContent=text;input.append(option);}
      }else{input.type=field==="password"?"password":"text";input.maxLength=256;
        input.autocomplete=field==="password"?"current-password":field==="username"?"username":"one-time-code";}
      label.append(input);form.append(label);
    }
    const submit=document.createElement("button");submit.textContent="Authenticate Victoria account";form.append(submit);
    form.onsubmit=event=>{
      event.preventDefault();if(busy)return;
      const data=new FormData(form),input={};for(const field of value.fields)input[field]=String(data.get(field)||"");
      form.reset();form.hidden=true;el("recover").hidden=false;
      run(async()=>authenticationResult(await request("/api/agent/shared-identity/auth-submit",{pairing_id:pairingId,handle:value.handle,input})));
    };
    status(value.invalid?"Authentication was not accepted. Check your details.":"Verify the same Victoria account used for this connection.");
  }
  el("login").onclick=()=>run(async()=>{
    const config=await request("/link-setup-config",undefined,"GET");haOrigin=config.ha_origin;
    const result=await request("/api/agent/auth/start");const url=new URL(result.authorize_url);
    if(url.protocol!=="https:" || url.origin!==haOrigin || url.pathname!=="/auth/authorize" || url.username || url.password)throw new Error();
    window.location.assign(url.href);
  });
  el("offer").onclick=()=>run(async()=>{
    if(blocked || !pairingId)return;
    el("offer").hidden=true;
    const result=await request("/api/agent/shared-identity/handoff",{version:1,pairing_id:pairingId,consent:true});
    if(result?.version!==1 || result.offer?.pairing_id!==pairingId || !/^[a-f0-9]{64}$/.test(result.offer.token) ||
      !Number.isSafeInteger(result.offer.expires_at) || result.offer.expires_at<=Date.now() || result.offer.expires_at>Date.now()+60000)throw new Error();
    expiry=result.offer.expires_at;el("code").value=result.offer.token;el("code-label").hidden=false;
    status("Copy this code into the Los Angeles Home tab.");
  });
  el("begin").onclick=()=>run(async()=>{
    if(blocked || !pairingId)return;el("begin").hidden=true;el("recover").hidden=false;
    expiry=0;el("code").value="";el("code-label").hidden=true;el("expiry").textContent="";
    authenticationResult(await request("/api/agent/shared-identity/auth-begin",{pairing_id:pairingId}));
  });
  el("recover").onclick=()=>run(async()=>{
    if(blocked || !pairingId)return;
    authenticationResult(await request("/api/agent/shared-identity/auth-outcome",{pairing_id:pairingId}));
  });
  el("logout").onclick=()=>run(async()=>{
    const operation=request("/api/agent/auth/logout");
    el("pairing").hidden=true;el("auth-form").replaceChildren();el("code").value="";
    try{await operation;}finally{reset();el("login").hidden=false;status("Signed out locally. Sign in again before linking.");}
  });
  window.addEventListener("hashchange",()=>{reset();status("The pairing changed. Reload this page before continuing.");});
  window.addEventListener("pagehide",reset);
  window.setInterval(()=>{
    if(!expiry)return;
    const seconds=Math.max(0,Math.ceil((expiry-Date.now())/1000));el("expiry").textContent=seconds?`Code expires in ${seconds} seconds.`:"Connection code expired.";
    if(!seconds){el("code").value="";el("code-label").hidden=true;expiry=0;}
  },1000);
  run(async()=>{
    pairingId=/^#shared-link\/([a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})$/.exec(window.location.hash)?.[1] || null;
    session=await request("/api/agent/auth/session",undefined,"GET");
    if(session?.authenticated!==true || session.authority?.site_id!=="victoria" || session.authority?.ha_issuer_id!=="home-assistant:victoria" ||
      typeof session.csrf_token!=="string" || !session.csrf_token){reset();throw new Error();}
    blocked=false;el("logout").hidden=false;el("pairing").hidden=!pairingId;
    status(pairingId?"Victoria is signed in. Connect this session using the code below.":"Victoria is signed in. Return to Home and start account linking, then open its Victoria connection link.");
  });
})();
