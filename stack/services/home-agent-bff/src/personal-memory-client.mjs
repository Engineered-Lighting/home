import { SessionStore } from "./bff.mjs";
import { readHaJson } from "./ha-response.mjs";

const uuid = value => typeof value === "string" && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value);
const digest = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const exact = (value, keys) => value && typeof value === "object" && !Array.isArray(value) &&
  Object.keys(value).sort().join() === [...keys].sort().join();
const preference = value => exact(value,["version","kind","key","scope","value"]) &&
  value.version===1 && value.kind==="personal_preference" && value.key==="lighting.evening.tone" &&
  value.scope==="owner" && ["warm","neutral","cool"].includes(value.value);
const revision = value => Number.isSafeInteger(value) && value>=0 && value<=2147483647;
const instant = value => typeof value==="string" && value.length<=64 && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));

function validResult(operation,result,input,now) {
  if (operation==="sharing-propose") return exact(result,["version","operation_id","source","applies_to",
    "effect","grants_expire_at","expires_at","reviewed_digest"]) && result.version===1 &&
    result.operation_id===input.operation_id && result.source==="core.personal-preferences.v1" &&
    result.applies_to==="both_homes" && result.effect==="read_and_manage_confirmed_preferences" &&
    instant(result.expires_at) && Date.parse(result.expires_at)>now && Date.parse(result.expires_at)<=now+61000 &&
    instant(result.grants_expire_at) && Date.parse(result.grants_expire_at)>=Date.parse(result.expires_at) &&
    Date.parse(result.grants_expire_at)<=now+366*86400000 && digest(result.reviewed_digest);
  if (["sharing-confirm","sharing-outcome"].includes(operation)) return exact(result,["version","operation_id","status"]) &&
    result.version===1 && result.operation_id===input.operation_id && ["committed","unknown"].includes(result.status);
  if (operation==="read") return exact(result,["revision","fact_id","preference","confirmed_at","source","source_site"]) &&
    revision(result.revision) && (result.revision===0 ? result.fact_id===null : uuid(result.fact_id)) &&
    (result.preference===null || preference(result.preference)) &&
    (result.confirmed_at===null ? result.revision===0 : instant(result.confirmed_at)) &&
    result.source==="core.personal-preferences.v1" &&
    (result.preference===null ? result.source_site===null : ["echo","victoria"].includes(result.source_site));
  if (operation==="propose") return exact(result,["version","operation_id","operation","expected_revision","expected_fact_id",
    "current","proposed","source_site","applies_to","effect","expires_at","reviewed_digest"]) &&
    result.version===1 && result.operation_id===input.operation_id && result.operation===input.operation &&
    result.expected_revision===input.expected_revision && result.expected_fact_id===input.expected_fact_id &&
    (result.current===null || preference(result.current)) &&
    (input.preference===null ? result.proposed===null : preference(result.proposed) && result.proposed.value===input.preference.value) &&
    result.source_site==="echo" && result.applies_to==="both_homes" && result.effect==="memory_only" &&
    instant(result.expires_at) && Date.parse(result.expires_at)>now && Date.parse(result.expires_at)<=now+61000 && digest(result.reviewed_digest);
  if (operation==="outcome" && result===null) return true;
  return exact(result,operation==="outcome" ? ["operation_id","revision","status","historical"] : ["operation_id","revision","status"]) &&
    result.operation_id===input.operation_id && revision(result.revision) && result.revision>0 &&
    ["committed","ledger_pending",...(operation==="outcome" ? ["forgotten"] : [])].includes(result.status) &&
    (operation!=="outcome" || result.historical===true);
}

export class PersonalMemoryClient {
  #store; #config; #key; #origin; #credential; #fetch; #active = 0;
  constructor({ store, config, commitmentKey, origin, credential, fetchImpl = fetch }) {
    const url = new URL(origin);
    if (!(store instanceof SessionStore) || !store.hasSharedSessionRevocation ||
        store.siteId !== "echo" || store.haIssuerId !== "home-assistant:echo" || !config ||
        url.protocol !== "https:" || url.pathname !== "/" || url.username || url.password || url.search || url.hash ||
        !digest(credential) || typeof fetchImpl !== "function") throw new Error("personal_memory_configuration_rejected");
    store.linkingContext("", commitmentKey);
    this.#store=store; this.#config=config; this.#key=Buffer.from(commitmentKey);
    this.#origin=url.origin; this.#credential=credential; this.#fetch=fetchImpl;
  }
  usesStore(store) { return store === this.#store; }
  async #context(sessionId) {
    const session=this.#store.get(sessionId);
    if (!session) throw new Error("personal_memory_session_unavailable");
    await this.#store.revalidate(this.#config,sessionId,session,this.#fetch,this.#store.now(),{forcePrincipalCheck:true});
    const context=this.#store.linkingContext(sessionId,this.#key);
    if (!context) throw new Error("personal_memory_session_unavailable");
    return context;
  }
  async request(sessionId, operation, input, {signal}={}) {
    if (!["read","propose","confirm","outcome","sharing-propose","sharing-confirm","sharing-outcome"].includes(operation)) throw new Error("invalid_preference_operation");
    const value=structuredClone(input);
    if (operation.startsWith("sharing-")) {
      if (!exact(value,operation==="sharing-confirm" ? ["version","operation_id","reviewed_digest"] : ["version","operation_id"]) ||
          value.version!==1 || !uuid(value.operation_id) || operation==="sharing-confirm" && !digest(value.reviewed_digest)) {
        throw new Error("invalid_preference_request");
      }
    } else if (operation==="read") {
      if (!exact(value,[])) throw new Error("invalid_preference_request");
    } else if (operation==="propose") {
      if (!exact(value,["version","operation_id","operation","expected_revision","expected_fact_id","preference"]) ||
          value.version!==1 || !uuid(value.operation_id) || !["remember","correct","forget"].includes(value.operation) ||
          !Number.isSafeInteger(value.expected_revision) || value.expected_revision<0 || value.expected_revision>=2147483647 ||
          (value.expected_revision===0 ? value.expected_fact_id!==null : !uuid(value.expected_fact_id)) ||
          (value.operation!=="remember" && value.expected_revision===0)) throw new Error("invalid_preference_request");
      if (value.operation==="forget") {
        if (value.preference!==null) throw new Error("invalid_preference_request");
      } else if (!exact(value.preference,["version","kind","key","scope","value"]) ||
          value.preference.version!==1 || value.preference.kind!=="personal_preference" ||
          value.preference.key!=="lighting.evening.tone" || value.preference.scope!=="owner" ||
          !["warm","neutral","cool"].includes(value.preference.value)) throw new Error("invalid_preference_request");
    } else if (!exact(value,operation==="confirm" ? ["version","operation_id","reviewed_digest","gesture_id"] :
        ["version","operation_id","reviewed_digest"]) || value.version!==1 || !uuid(value.operation_id) ||
        !digest(value.reviewed_digest) || operation==="confirm" && !uuid(value.gesture_id)) throw new Error("invalid_preference_request");
    if (signal?.aborted || this.#active>=2) throw new Error("personal_memory_unavailable");
    this.#active++;
    const controller=new AbortController();
    const abort=()=>controller.abort();
    signal?.addEventListener("abort",abort,{once:true});
    const timer=setTimeout(abort,10000);
    let onAbort;
    const cancelled=new Promise((_,reject)=>{
      onAbort=()=>reject(new Error("personal_memory_outcome_unknown"));
      controller.signal.addEventListener("abort",onAbort,{once:true});
    });
    const execution=(async()=>{
      const original=await this.#context(sessionId);
      if (controller.signal.aborted) throw new Error();
      const {gesture_id,...request}=value;
      const body={version:1,subject:original.subject,session_commitment:original.sessionCommitment,
        request:operation==="confirm" ? request : value};
      if (operation==="confirm") body.gesture_id=gesture_id;
      const response=await this.#fetch(this.#origin+"/internal/personal-memory/v1/"+operation,{
        method:"POST",redirect:"error",credentials:"omit",cache:"no-store",signal:controller.signal,
        headers:{authorization:"Bearer "+this.#credential,"content-type":"application/json"},body:JSON.stringify(body)});
      if (response.status!==200) { await response.body?.cancel().catch(()=>{}); throw new Error(); }
      const result=await readHaJson(response,8192);
      if (!exact(result,["version","result"]) || result.version!==1 ||
          !validResult(operation,result.result,value,this.#store.now())) throw new Error();
      const current=await this.#context(sessionId);
      if (current.subject!==original.subject || current.sessionCommitment!==original.sessionCommitment ||
          controller.signal.aborted || this.#store.now()>=original.validUntil) throw new Error();
      return result;
    })().finally(()=>{
      this.#active--; clearTimeout(timer); signal?.removeEventListener("abort",abort);
    });
    try { return await Promise.race([execution,cancelled]); }
    catch { throw new Error("personal_memory_outcome_unknown"); }
    finally { controller.signal.removeEventListener("abort",onAbort); }
  }
}
