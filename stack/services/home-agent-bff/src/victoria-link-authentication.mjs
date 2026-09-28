import { isDeepStrictEqual } from "node:util";
import { VictoriaSessionHandoff } from "./victoria-session-handoff.mjs";
import { VictoriaLinkCeremony } from "./victoria-link-ceremony.mjs";

const uuid = v => typeof v === "string" && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(v);
const digest = v => typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
const fields = ["pairing_id", "session_commitment", "challenge_commitment", "proof_id", "registration_revision", "valid_until"];

// admit/outcome are private coordinator operations; authenticate belongs to the
// separate Victoria cookie/origin/CSRF boundary. No caller supplies an HA user.
// Volatile pairing admission is intentionally lost on restart. The underlying
// governed proof journal still accounts for any proof already dispatched.
export class VictoriaLinkAuthentication {
  #handoff; #ceremony; #store; #config; #admissions = new Map(); #active = 0;
  constructor({ handoff, ceremony, store, config }) {
    if (!(handoff instanceof VictoriaSessionHandoff) || !(ceremony instanceof VictoriaLinkCeremony) ||
        !handoff.usesBoundary(store, config) || !ceremony.usesBoundary(store, config)) throw new Error("victoria_auth_configuration_rejected");
    this.#handoff=handoff; this.#ceremony=ceremony; this.#store=store; this.#config=config;
  }
  usesBoundary(store,config) { return store===this.#store && config===this.#config; }
  usesHandoff(handoff) { return handoff===this.#handoff; }
  async #bounded(work) {
    if (this.#active>=2) throw new Error("victoria_auth_busy");
    this.#active++;
    try { return await work(); } finally { this.#active--; }
  }
  async #resolve(value,sessionId) {
    if (this.#store.now()>=value.valid_until) throw new Error("victoria_auth_expired");
    return this.#handoff.resolvePairing({ pairingId:value.pairing_id,sessionCommitment:value.session_commitment,sessionId });
  }
  admit(value) {
    return this.#bounded(async()=>{
      if (!value || Array.isArray(value) || Object.keys(value).sort().join(",")!==[...fields].sort().join(",") ||
          !uuid(value.pairing_id) || !uuid(value.proof_id) || !digest(value.session_commitment) || !digest(value.challenge_commitment) ||
          !Number.isSafeInteger(value.registration_revision) || value.registration_revision<1 ||
          !Number.isSafeInteger(value.valid_until) || value.valid_until>this.#store.now()+300_000) throw new Error("victoria_auth_request_rejected");
      const admission=Object.freeze({...value});
      await this.#resolve(admission);
      for (const [id,entry] of this.#admissions) if (entry.admission.valid_until<=this.#store.now() && !entry.busy) this.#admissions.delete(id);
      const old=this.#admissions.get(admission.pairing_id);
      if (old && !isDeepStrictEqual(old.admission,admission)) throw new Error("victoria_auth_conflict");
      if (!old) {
        if (this.#admissions.size>=32) throw new Error("victoria_auth_busy");
        this.#admissions.set(admission.pairing_id,{admission,begun:false,busy:false});
      }
      return Object.freeze({version:1,status:"authentication_required",pairing_id:admission.pairing_id});
    });
  }
  authenticate(sessionId,operation,input) {
    return this.#bounded(async()=>{
      if (!["begin","submit","recover"].includes(operation) || !input || Array.isArray(input) ||
          Object.keys(input).sort().join(",")!==(operation==="submit"?"handle,input,pairing_id":"pairing_id")) throw new Error("victoria_auth_request_rejected");
      const value=structuredClone(input),entry=this.#admissions.get(value.pairing_id);
      if (!entry || entry.busy) throw new Error("victoria_auth_unavailable");
      entry.busy=true;
      try {
        const session=await this.#resolve(entry.admission,sessionId),a=entry.admission;
        let result;
        if (operation==="submit") {
          if (!entry.handle || value.handle!==entry.handle || entry.proof) throw new Error("victoria_auth_unavailable");
          result=await this.#ceremony.submit({sessionId,handle:value.handle,input:value.input});
        } else {
          if (operation==="begin" && entry.begun || operation==="recover" && !entry.begun) throw new Error("victoria_auth_unavailable");
          if (operation==="begin") entry.begun=true;
          result=await this.#ceremony[operation==="begin"?"begin":"recover"]({sessionId,
            challengeCommitment:a.challenge_commitment,proofId:a.proof_id,registrationRevision:a.registration_revision,
            validUntil:Math.min(a.valid_until,session.validUntil)});
        }
        await this.#resolve(a,sessionId);
        if (result.status==="proved") {
          entry.proof=result.proof;
          return Object.freeze({version:1,status:"authenticated",site_id:"victoria",ceremony_id:a.pairing_id});
        }
        if (result.status!=="form") throw new Error("victoria_auth_unavailable");
        if (operation==="begin") entry.handle=result.handle;
        return result;
      } finally {entry.busy=false;}
    });
  }
  outcome(pairingId) {
    return this.#bounded(async()=>{
      const entry=this.#admissions.get(pairingId);
      if (!entry?.proof) throw new Error("victoria_auth_unavailable");
      await this.#resolve(entry.admission);
      return Object.freeze({...entry.proof}); // Private ingress only.
    });
  }
}
