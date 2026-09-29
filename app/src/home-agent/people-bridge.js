(function(root) {
  "use strict";
  // Read-only People bridge. Home frames this invisible Agent-origin page to
  // read the household with the owner's Agent session: only the two GET routes
  // below, never a write, and results go only to the provisioned Home origin
  // that framed it, bound to the request nonce.
  const nonce=(/^#([a-f0-9]{32})$/.exec(root.location.hash) || [])[1];
  const parent=root.parent;
  let origins=null, answered=false;

  async function getJson(path) {
    const response=await root.fetch(path,{credentials:"same-origin",cache:"no-store",headers:{Accept:"application/json"},redirect:"error"});
    const body=await response.json().catch(()=>null);
    return {status:response.status,ok:response.ok,body};
  }

  async function homeOrigins() {
    if(origins) return origins;
    const session=await getJson("/api/agent/auth/session");
    const listed=session.ok && Array.isArray(session.body?.personal_memory_home_origins) ? session.body.personal_memory_home_origins : [];
    origins={signedIn:session.ok && session.body?.authenticated===true,status:session.status,list:listed.filter(origin=>{
      try {const url=new URL(origin);return url.protocol==="https:" && url.origin===origin && url.origin!==root.location.origin;}
      catch {return false;}
    })};
    return origins;
  }

  function reply(origin,value) {
    parent.postMessage({version:1,type:"home.people.result",nonce,...value},origin);
  }

  async function receive(event) {
    const value=event.data;
    if(answered || event.source!==parent || !value || typeof value!=="object" ||
        Object.keys(value).sort().join()!=="nonce,type,version" || value.version!==1 ||
        value.type!=="home.people.request" || value.nonce!==nonce) return;
    let allowed;
    try {allowed=await homeOrigins();} catch {return;}
    if(!allowed.list.includes(event.origin)) {
      // A signed-out session lists no origins. The frame-ancestors CSP already
      // limits the parent to a provisioned Home origin, and "signed out"
      // carries no household data, so tell it to sign in.
      if(!allowed.signedIn) {
        answered=true;reply(event.origin,{status:"signed_out"});
      }
      return;
    }
    answered=true;
    if(!allowed.signedIn) {reply(event.origin,{status:"signed_out"});return;}
    try {
      const [household,relationships]=await Promise.all([getJson("/api/agent/v1/household"),getJson("/api/agent/v1/relationships")]);
      if(household.status===401) {reply(event.origin,{status:"signed_out"});return;}
      if(!household.ok || !household.body || !Array.isArray(household.body.people)) {
        reply(event.origin,{status:"error",http_status:household.status});return;
      }
      reply(event.origin,{status:"ok",household:household.body,
        relationships:relationships.ok && relationships.body && Array.isArray(relationships.body.relationships) ? relationships.body : {relationships:[]}});
    } catch {
      reply(event.origin,{status:"error",http_status:0});
    }
  }

  if(root.top===root || root.top!==parent || !nonce) return;
  root.history.replaceState(null,"",root.location.pathname);
  root.addEventListener("message",receive);
})(window);
