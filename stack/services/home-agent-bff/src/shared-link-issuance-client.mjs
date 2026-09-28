// Private coordinator transport. Inputs must come from authenticated per-home
// session handoff, never browser JSON. This class does not enable any route.
import { readHaJson } from "./ha-response.mjs";
import { parseSharedIdentityInstant as instant } from "./shared-auth-proof-client.mjs";

const PATH="/internal/shared-identity/v1/link-issuance";
const OUTCOME="/internal/shared-identity/v1/link-issuance-outcome";
const fields=["ceremony_id","echo_session_commitment","victoria_session_commitment","echo_challenge_id",
  "victoria_challenge_id","echo_challenge_commitment","victoria_challenge_commitment"];
const exact=(v,keys)=>v && typeof v==="object" && !Array.isArray(v) && Object.keys(v).length===keys.length && keys.every(k=>Object.hasOwn(v,k));
const uuid=v=>typeof v==="string" && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(v);
const digest=v=>typeof v==="string" && /^[a-f0-9]{64}$/.test(v);

export function validateLinkIssuanceRequest(value) {
  if (!exact(value,["subject","context"]) || typeof value.subject!=="string" || !value.subject.length ||
      [...value.subject].length>64 || value.subject.trim()!==value.subject || /[\x00-\x1f\x7f]/.test(value.subject) ||
      !exact(value.context,fields) || fields.some(k=>!(k.endsWith("_id")?uuid:digest)(value.context[k])) ||
      value.context.echo_challenge_id===value.context.victoria_challenge_id ||
      value.context.echo_challenge_commitment===value.context.victoria_challenge_commitment) throw new Error("invalid_issuance_request");
  return Object.freeze({subject:value.subject,context:Object.freeze(Object.fromEntries(fields.map(k=>[k,value.context[k]])))});
}

export class SharedLinkIssuanceClient {
  #endpoint; #credential; #fetch; #now; #active=0;
  constructor({endpoint,credential,fetchImpl=fetch,now=Date.now}) {
    const url=new URL(endpoint);
    if (url.protocol!=="https:" || url.pathname!==PATH || url.username || url.password || url.search || url.hash ||
        !digest(credential) || typeof fetchImpl!=="function" || typeof now!=="function") throw new Error("invalid_issuance_transport");
    this.#endpoint=url.href;this.#credential=credential;this.#fetch=fetchImpl;this.#now=now;
  }
  begin(value,options={}) {return this.#request(value,this.#endpoint,options.signal);}
  recover(value,options={}) {return this.#request(value,new URL(OUTCOME,this.#endpoint).href,options.signal);}
  async #request(value,url,signal) {
    const expected=validateLinkIssuanceRequest(value);
    if (signal?.aborted || this.#active>=2) throw new Error("issuance_transport_unavailable");
    const controller=new AbortController(),abort=()=>controller.abort();
    signal?.addEventListener("abort",abort,{once:true});
    const timer=setTimeout(abort,10_000);this.#active++;
    const operation=(async()=>{
      const response=await this.#fetch(url,{method:"POST",redirect:"error",credentials:"omit",cache:"no-store",
        headers:{authorization:`Bearer ${this.#credential}`,"content-type":"application/json"},
        body:JSON.stringify({version:1,request:expected}),signal:controller.signal});
      if(response.status!==200){await response.body?.cancel().catch(()=>{});throw new Error();}
      const result=await readHaJson(response,2048),receipt=result.issuance;
      const created=instant(receipt?.created_at),expires=instant(receipt?.expires_at),now=this.#now();
      if(!exact(result,["version","issuance"]) || result.version!==1 ||
        !exact(receipt,["ceremony_id","authorization_generation","revision","created_at","expires_at","echo_registration_revision","victoria_registration_revision"]) ||
        receipt.ceremony_id!==expected.context.ceremony_id ||
        ["authorization_generation","revision","echo_registration_revision","victoria_registration_revision"].some(k=>!Number.isSafeInteger(receipt[k]) || receipt[k]<1) ||
        created===null || expires===null || expires-created!==300_000_000n || !Number.isSafeInteger(now) ||
        expires<=BigInt(now)*1000n || created>(BigInt(now)+1000n)*1000n) throw new Error();
      return Object.freeze({...receipt});
    })().finally(()=>{this.#active--;clearTimeout(timer);signal?.removeEventListener("abort",abort);});
    let onAbort;
    const cancelled=new Promise((_,reject)=>{onAbort=()=>reject(new Error("issuance_outcome_unknown"));
      controller.signal.addEventListener("abort",onAbort,{once:true});if(controller.signal.aborted)onAbort();});
    try{return await Promise.race([operation,cancelled]);}
    catch{throw new Error("issuance_outcome_unknown");}
    finally{controller.signal.removeEventListener("abort",onAbort);}
  }
}
