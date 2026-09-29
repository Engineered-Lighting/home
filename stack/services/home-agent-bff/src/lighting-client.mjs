import { SessionStore } from "./bff.mjs";
import { readHaJson } from "./ha-response.mjs";

// Private client for Core's explicit cross-home lighting service. Browser input
// carries only typed lighting requests; the authenticated Echo session supplies
// the owner subject and session commitment. Any doubt is "outcome unknown".
export const LIGHTING_OPERATIONS = ["status","propose","confirm","outcome","consent-propose","consent-confirm","consent-outcome"];
const SITES = ["echo","victoria"];
const OPERATIONS = ["on","off","brightness"];
const RESULTS = ["succeeded","failed","not_sent","unknown","pending"];
const CLARIFY = ["unknown_light","ambiguous_light","no_eligible_lights","not_dimmable","light_unavailable","too_many_lights"];
const uuid = value => typeof value === "string" && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value);
const digest = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const exact = (value, keys) => value && typeof value === "object" && !Array.isArray(value) &&
  Object.keys(value).sort().join() === [...keys].sort().join();
const instant = value => typeof value==="string" && value.length<=64 && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const name = value => typeof value==="string" && value.length>=1 && value.length<=120 && !/[\u0000-\u001f\u007f]/.test(value);
const target = value => typeof value==="string" && /^[a-z0-9][a-z0-9 '\-]{0,39}$/.test(value);
const brightness = (operation, value) => operation==="brightness" ? Number.isInteger(value) && value>=1 && value<=100 : value===null;
const change = value => exact(value,["site_id","name","operation","brightness"]) && SITES.includes(value.site_id) &&
  name(value.name) && OPERATIONS.includes(value.operation) && brightness(value.operation,value.brightness);

function validRequest(operation, value) {
  if (operation==="status") return exact(value,[]);
  if (operation==="propose") return exact(value,["version","operation_id","sites","targets","operation","brightness"]) &&
    value.version===1 && uuid(value.operation_id) && Array.isArray(value.sites) && value.sites.length>=1 &&
    value.sites.length<=2 && new Set(value.sites).size===value.sites.length && value.sites.every(site=>SITES.includes(site)) &&
    (value.targets==="all" || Array.isArray(value.targets) && value.targets.length>=1 && value.targets.length<=8 &&
      new Set(value.targets).size===value.targets.length && value.targets.every(target)) &&
    OPERATIONS.includes(value.operation) && brightness(value.operation,value.brightness);
  if (operation==="confirm" || operation==="consent-confirm") return exact(value,["version","operation_id","reviewed_digest"]) &&
    value.version===1 && uuid(value.operation_id) && digest(value.reviewed_digest);
  return exact(value,["version","operation_id"]) && value.version===1 && uuid(value.operation_id);
}

function summary(result, input) {
  return exact(result,["version","operation_id","status","results"]) && result.version===1 &&
    result.operation_id===input.operation_id && ["review","done","partial","unknown"].includes(result.status) &&
    Array.isArray(result.results) && result.results.length>=1 && result.results.length<=16 &&
    result.results.every(item=>exact(item,["site_id","name","operation","brightness","status"]) &&
      change({site_id:item.site_id,name:item.name,operation:item.operation,brightness:item.brightness}) &&
      RESULTS.includes(item.status));
}

function validResult(operation, result, input, now) {
  if (operation==="status") return exact(result,["version","permitted","homes"]) && result.version===1 &&
    exact(result.permitted,SITES) && SITES.every(site=>typeof result.permitted[site]==="boolean") &&
    Array.isArray(result.homes) && result.homes.every(site=>SITES.includes(site));
  if (operation==="consent-propose") return exact(result,["version","operation_id","source","applies_to","effect",
    "grants_expire_at","expires_at","reviewed_digest"]) && result.version===1 && result.operation_id===input.operation_id &&
    result.source==="core.lighting.v1" && result.applies_to==="both_homes" &&
    result.effect==="switch_allowlisted_lights_after_each_confirmation" &&
    instant(result.expires_at) && Date.parse(result.expires_at)>now && Date.parse(result.expires_at)<=now+61000 &&
    instant(result.grants_expire_at) && Date.parse(result.grants_expire_at)>=Date.parse(result.expires_at) &&
    Date.parse(result.grants_expire_at)<=now+366*86400000 && digest(result.reviewed_digest);
  if (operation==="consent-confirm" || operation==="consent-outcome") return exact(result,["version","operation_id","status"]) &&
    result.version===1 && result.operation_id===input.operation_id && ["committed","unknown"].includes(result.status);
  if (operation==="propose") {
    if (exact(result,["version","status","review"])) {
      const review=result.review;
      return result.version===1 && result.status==="review" &&
        exact(review,["version","operation_id","expires_at","reviewed_digest","operations"]) && review.version===1 &&
        review.operation_id===input.operation_id && instant(review.expires_at) && Date.parse(review.expires_at)>now &&
        Date.parse(review.expires_at)<=now+61000 && digest(review.reviewed_digest) && Array.isArray(review.operations) &&
        review.operations.length>=1 && review.operations.length<=16 && review.operations.every(change) &&
        review.operations.every(op=>input.sites.includes(op.site_id) && op.operation===input.operation &&
          op.brightness===input.brightness);
    }
    if (exact(result,["version","status","clarification"])) {
      const value=result.clarification;
      return result.version===1 && result.status==="clarify" &&
        exact(value,["version","reason","site_id","target","candidates"]) && value.version===1 &&
        CLARIFY.includes(value.reason) && input.sites.includes(value.site_id) &&
        (value.target===null || target(value.target)) && Array.isArray(value.candidates) &&
        value.candidates.length<=8 && value.candidates.every(name);
    }
    return summary(result,input);
  }
  return summary(result,input);
}

export class LightingClient {
  #store; #config; #key; #origin; #credential; #fetch; #active = 0;
  constructor({ store, config, commitmentKey, origin, credential, fetchImpl = fetch }) {
    const url = new URL(origin);
    if (!(store instanceof SessionStore) || !store.hasSharedSessionRevocation ||
        store.siteId !== "echo" || store.haIssuerId !== "home-assistant:echo" || !config ||
        url.protocol !== "https:" || url.pathname !== "/" || url.username || url.password || url.search || url.hash ||
        !digest(credential) || typeof fetchImpl !== "function") throw new Error("lighting_configuration_rejected");
    store.linkingContext("", commitmentKey);
    this.#store=store; this.#config=config; this.#key=Buffer.from(commitmentKey);
    this.#origin=url.origin; this.#credential=credential; this.#fetch=fetchImpl;
  }
  usesStore(store) { return store === this.#store; }
  async #context(sessionId) {
    const session=this.#store.get(sessionId);
    if (!session) throw new Error("lighting_session_unavailable");
    await this.#store.revalidate(this.#config,sessionId,session,this.#fetch,this.#store.now(),{forcePrincipalCheck:true});
    const context=this.#store.linkingContext(sessionId,this.#key);
    if (!context) throw new Error("lighting_session_unavailable");
    return context;
  }
  async request(sessionId, operation, input, {signal}={}) {
    if (!LIGHTING_OPERATIONS.includes(operation)) throw new Error("invalid_lighting_operation");
    const value=structuredClone(input);
    if (!validRequest(operation,value)) throw new Error("invalid_lighting_request");
    if (signal?.aborted || this.#active>=2) throw new Error("lighting_unavailable");
    this.#active++;
    const controller=new AbortController();
    const abort=()=>controller.abort();
    signal?.addEventListener("abort",abort,{once:true});
    // Core's lighting ingress allows 75 s: a dispatch ends within ~66 s of confirmation.
    const timer=setTimeout(abort,80000);
    let onAbort;
    const cancelled=new Promise((_,reject)=>{
      onAbort=()=>reject(new Error("lighting_outcome_unknown"));
      controller.signal.addEventListener("abort",onAbort,{once:true});
    });
    let refused=null;
    const execution=(async()=>{
      const original=await this.#context(sessionId);
      if (controller.signal.aborted) throw new Error();
      const response=await this.#fetch(this.#origin+"/internal/lighting/v1/"+operation,{
        method:"POST",redirect:"error",credentials:"omit",cache:"no-store",signal:controller.signal,
        headers:{authorization:"Bearer "+this.#credential,"content-type":"application/json"},
        body:JSON.stringify({version:1,subject:original.subject,session_commitment:original.sessionCommitment,request:value})});
      if (response.status===403) {
        // A typed refusal is definite: nothing was proposed or sent.
        const body=await readHaJson(response,1024).catch(()=>null);
        if (exact(body,["error"]) && body.error==="lighting_not_permitted") { refused="lighting_not_permitted"; throw new Error(); }
        throw new Error();
      }
      if (response.status!==200) { await response.body?.cancel().catch(()=>{}); throw new Error(); }
      const result=await readHaJson(response,16384);
      if (!exact(result,["version","result"]) || result.version!==1 ||
          !validResult(operation,result.result,value,this.#store.now())) throw new Error();
      const current=await this.#context(sessionId);
      if (current.subject!==original.subject || current.sessionCommitment!==original.sessionCommitment ||
          controller.signal.aborted) throw new Error();
      return result;
    })().finally(()=>{
      this.#active--; clearTimeout(timer); signal?.removeEventListener("abort",abort);
    });
    try { return await Promise.race([execution,cancelled]); }
    catch { throw new Error(refused ?? "lighting_outcome_unknown"); }
    finally { controller.signal.removeEventListener("abort",onAbort); }
  }
}
