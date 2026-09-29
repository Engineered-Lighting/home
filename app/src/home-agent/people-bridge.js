(function(root) {
  "use strict";
  // People bridge. Home frames this invisible Agent-origin page to reach the
  // household with the owner's Agent session. Results go only to a Home origin
  // listed in the session's personal_memory_home_origins that framed it, bound
  // to the request nonce, and each frame answers exactly one request.
  //
  // Reads: the two GET routes below (household and relationships).
  // Writes (owner decision, 2026-09-29): exactly two POST routes, adding a
  // household person and recording a relationship. The body is checked here
  // against Core's request models (OwnerPersonCreate, OwnerPartnerAttestation)
  // and rebuilt from the checked fields; anything else is refused before any
  // request is made. The CSRF token is read from the session and sent only in
  // the same-origin POST, never to the parent.
  const nonce=(/^#([a-f0-9]{32})$/.exec(root.location.hash) || [])[1];
  const parent=root.parent;
  let session=null, busy=false;

  async function getJson(path) {
    const response=await root.fetch(path,{credentials:"same-origin",cache:"no-store",headers:{Accept:"application/json"},redirect:"error"});
    const body=await response.json().catch(()=>null);
    return {status:response.status,ok:response.ok,body};
  }

  async function readSession() {
    if(session) return session;
    const read=await getJson("/api/agent/auth/session");
    const listed=read.ok && Array.isArray(read.body?.personal_memory_home_origins) ? read.body.personal_memory_home_origins : [];
    const csrf=read.ok && typeof read.body?.csrf_token==="string" && read.body.csrf_token ? read.body.csrf_token : null;
    session={signedIn:read.ok && read.body?.authenticated===true,csrf,list:listed.filter(origin=>{
      try {const url=new URL(origin);return url.protocol==="https:" && url.origin===origin && url.origin!==root.location.origin;}
      catch {return false;}
    })};
    return session;
  }

  // ── write validation: Core's request models, restated exactly ─────────────
  const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-([1-8])[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  const PREDICATES=["parent_of","partner_of","friend_of","sibling_of","roommate_of","neighbor_of","colleague_of"];
  const DIRECTIVES=["do_not_track","ignored","silent","private","auto_expire"];
  const OFFSET_TIME=/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})$/;
  const has=(body,key)=>Object.prototype.hasOwnProperty.call(body,key);
  const uuid=(value,version)=>{
    if(typeof value!=="string") return false;
    const match=UUID.exec(value);
    return !!match && (version===undefined || Number(match[1])===version);
  };
  const text=(value,max,min=0)=>typeof value==="string" && value.trim().length>=min && value.trim().length<=max;
  const plain=value=>!!value && typeof value==="object" && !Array.isArray(value) &&
    Object.getPrototypeOf(value)===Object.prototype;

  function addPersonBody(body) {
    const keys=["ceremony_id","display_name","pronouns","privacy_scope","directive","directive_expires_at"];
    if(!plain(body) || Object.keys(body).some(key=>!keys.includes(key))) return null;
    if(!uuid(body.ceremony_id,7) || !text(body.display_name,255,1)) return null;
    if(has(body,"pronouns") && body.pronouns!==null && !text(body.pronouns,64)) return null;
    if(has(body,"privacy_scope") && !["private","household"].includes(body.privacy_scope)) return null;
    if(has(body,"directive") && body.directive!==null && !DIRECTIVES.includes(body.directive)) return null;
    const expires=has(body,"directive_expires_at") ? body.directive_expires_at : null;
    if(expires!==null && !(typeof expires==="string" && OFFSET_TIME.test(expires) && !Number.isNaN(Date.parse(expires)))) return null;
    if((body.directive==="auto_expire")!==(expires!==null)) return null;
    const out={ceremony_id:body.ceremony_id,display_name:body.display_name.trim()};
    for(const key of keys.slice(2)) if(has(body,key)) out[key]=typeof body[key]==="string" ? body[key].trim() : body[key];
    return out;
  }

  function addRelationshipBody(body) {
    const keys=["ceremony_id","partner_person_id","attestation_nonce","subject_person_id","predicate"];
    if(!plain(body) || Object.keys(body).some(key=>!keys.includes(key))) return null;
    if(!uuid(body.ceremony_id,7) || !uuid(body.partner_person_id) || !uuid(body.attestation_nonce,4)) return null;
    const subject=has(body,"subject_person_id") ? body.subject_person_id : null;
    if(subject!==null && (!uuid(subject) || subject===body.partner_person_id)) return null;
    if(has(body,"predicate") && !PREDICATES.includes(body.predicate)) return null;
    const out={ceremony_id:body.ceremony_id,partner_person_id:body.partner_person_id,attestation_nonce:body.attestation_nonce};
    if(has(body,"subject_person_id")) out.subject_person_id=subject;
    if(has(body,"predicate")) out.predicate=body.predicate;
    return out;
  }

  const WRITES={
    add_person:{path:"/api/agent/v1/household-person",check:addPersonBody,
      result:["person_id","display_name","privacy_scope"]},
    add_relationship:{path:"/api/agent/v1/partner-attestation",check:addRelationshipBody,
      result:["receipt_id","partner_person_id","subject_person_id","predicate","document_digest"]},
  };

  // Only named, scalar fields travel back: a result or a short error, never a
  // header or session value.
  function resultBody(write,ok,body) {
    const out={};
    if(!plain(body)) return out;
    const scalar=value=>value===null || ["string","number","boolean"].includes(typeof value);
    if(ok) {
      for(const key of write.result) if(has(body,key) && scalar(body[key])) out[key]=body[key];
      return out;
    }
    const error=body.error;
    if(typeof error==="string") out.error=error.slice(0,200);
    else if(plain(error)) {
      if(typeof error.code==="string") out.error=error.code.slice(0,200);
      if(typeof error.message==="string") out.message=error.message.slice(0,500);
    }
    if(typeof body.detail==="string") out.message=body.detail.slice(0,500);
    return out;
  }

  function reply(origin,type,value) {
    parent.postMessage({version:1,type,nonce,...value},origin);
  }

  function request(value) {
    if(!value || typeof value!=="object" || value.version!==1 || value.nonce!==nonce) return null;
    const keys=Object.keys(value).sort().join();
    if(value.type==="home.people.request" && keys==="nonce,type,version") return {kind:"read",type:"home.people.result"};
    if(value.type==="home.people.write" && keys==="body,nonce,operation,type,version") {
      const write=typeof value.operation==="string" && has(WRITES,value.operation) ? WRITES[value.operation] : null;
      return {kind:"write",type:"home.people.write_result",write,body:write ? write.check(value.body) : null};
    }
    return null;
  }

  async function read(origin) {
    const type="home.people.result";
    try {
      const [household,relationships]=await Promise.all([getJson("/api/agent/v1/household"),getJson("/api/agent/v1/relationships")]);
      if(household.status===401) {reply(origin,type,{status:"signed_out"});return;}
      if(!household.ok || !household.body || !Array.isArray(household.body.people)) {
        reply(origin,type,{status:"error",http_status:household.status});return;
      }
      reply(origin,type,{status:"ok",household:household.body,
        relationships:relationships.ok && relationships.body && Array.isArray(relationships.body.relationships) ? relationships.body : {relationships:[]}});
    } catch {
      reply(origin,type,{status:"error",http_status:0});
    }
  }

  async function write(origin,wanted,csrf) {
    const type="home.people.write_result";
    if(!wanted.write || !wanted.body) {reply(origin,type,{status:"refused",http_status:0,body:{error:"write_refused"}});return;}
    if(!csrf) {reply(origin,type,{status:"signed_out",http_status:401,body:{}});return;}
    try {
      const response=await root.fetch(wanted.write.path,{method:"POST",credentials:"same-origin",cache:"no-store",redirect:"error",
        headers:{Accept:"application/json","Content-Type":"application/json","X-CSRF-Token":csrf},
        body:JSON.stringify(wanted.body)});
      const body=await response.json().catch(()=>null);
      reply(origin,type,{status:response.ok ? "ok" : response.status===401 ? "signed_out" : "error",
        http_status:response.status,body:resultBody(wanted.write,response.ok,body)});
    } catch {
      reply(origin,type,{status:"error",http_status:0,body:{}});
    }
  }

  async function receive(event) {
    if(busy || event.source!==parent) return;
    const wanted=request(event.data);
    if(!wanted) return;
    // Taken before the first await: the parent repeats its request until it
    // hears back, and a write must never be sent twice.
    busy=true;
    let allowed;
    try {allowed=await readSession();} catch {return;}
    const signedOut=wanted.kind==="write" ? {status:"signed_out",http_status:401,body:{}} : {status:"signed_out"};
    if(!allowed.list.includes(event.origin)) {
      // A signed-out session lists no origins. The frame-ancestors CSP already
      // limits the parent to a provisioned Home origin, and "signed out"
      // carries no household data, so tell it to sign in. A signed-in session
      // that does not list this origin gets no answer at all.
      if(!allowed.signedIn) reply(event.origin,wanted.type,signedOut);
      return;
    }
    if(!allowed.signedIn) {reply(event.origin,wanted.type,signedOut);return;}
    if(wanted.kind==="read") await read(event.origin);
    else await write(event.origin,wanted,allowed.csrf);
  }

  if(root.top===root || root.top!==parent || !nonce) return;
  root.history.replaceState(null,"",root.location.pathname);
  root.addEventListener("message",receive);
})(window);
