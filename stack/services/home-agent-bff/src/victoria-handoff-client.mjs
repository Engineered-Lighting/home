import { readHaJson } from "./ha-response.mjs";
import { validateProofReceipt } from "./shared-auth-proof-client.mjs";

const PATH = "/internal/shared-identity/v1/victoria-handoff";
const exact = (v, keys) => v && typeof v === "object" && !Array.isArray(v) &&
  Object.keys(v).length === keys.length && keys.every(k => Object.hasOwn(v, k));
const uuid = v => typeof v === "string" && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(v);
const digest = v => typeof v === "string" && /^[a-f0-9]{64}$/.test(v);

// The coordinator retains its intent before this one-use exchange. There is no
// retry: an unknown response may mean Victoria already consumed the offer.
export class VictoriaHandoffClient {
  #endpoint; #credential; #fetch; #now; #mono; #active = 0;
  constructor({ endpoint, credential, fetchImpl = fetch, now = Date.now, monotonicNow = () => performance.now() }) {
    const url = new URL(endpoint);
    if (url.protocol !== "https:" || url.pathname !== PATH || url.username || url.password || url.search || url.hash ||
        !digest(credential) || typeof fetchImpl !== "function" || typeof now !== "function" || typeof monotonicNow !== "function") {
      throw new Error("invalid_handoff_transport");
    }
    this.#endpoint = url.href; this.#credential = credential; this.#fetch = fetchImpl;
    this.#now = now; this.#mono = monotonicNow;
  }

  async redeem(value, { signal } = {}) {
    if (!exact(value, ["pairing_id", "token"]) || !uuid(value.pairing_id) || !digest(value.token)) throw new Error("invalid_handoff_request");
    const expected = Object.freeze({ ...value });
    return this.#request(PATH, expected, signal, (result, start, now) => {
      const v=result.handoff;
      if (!exact(result,["version","handoff"]) || result.version!==1 ||
          !exact(v,["pairing_id","issuer_id","site_id","session_commitment","valid_until"]) ||
          v.pairing_id!==expected.pairing_id || v.issuer_id!=="home-assistant:victoria" || v.site_id!=="victoria" ||
          !digest(v.session_commitment) || !Number.isSafeInteger(v.valid_until) || v.valid_until<=now || v.valid_until>start+60000) throw new Error();
      return Object.freeze({...v});
    });
  }
  #admission(value) {
    if (!exact(value,["pairing_id","session_commitment","challenge_commitment","proof_id","registration_revision","valid_until"]) ||
        !uuid(value.pairing_id) || !uuid(value.proof_id) || !digest(value.session_commitment) || !digest(value.challenge_commitment) ||
        !Number.isSafeInteger(value.registration_revision) || value.registration_revision<1 || !Number.isSafeInteger(value.valid_until) ||
        value.valid_until<=this.#now() || value.valid_until>this.#now()+300000) throw new Error("invalid_authentication_admission");
    return Object.freeze({...value});
  }
  admitAuthentication(value,{signal}={}) {
    const expected=this.#admission(value);
    return this.#request("/internal/shared-identity/v1/victoria-auth-admission",{admission:expected},signal,(result,_start,now)=>{
      if (!exact(result,["version","admission"]) || result.version!==1 ||
          !exact(result.admission,["version","status","pairing_id"]) || result.admission.version!==1 ||
          result.admission.status!=="authentication_required" || result.admission.pairing_id!==expected.pairing_id || now>=expected.valid_until) throw new Error();
      return Object.freeze({...result.admission});
    });
  }
  authenticationOutcome(value,{signal}={}) {
    const expected=this.#admission(value);
    return this.#request("/internal/shared-identity/v1/victoria-auth-outcome",{pairing_id:expected.pairing_id},signal,(result,_start,now)=>{
      if (!exact(result,["version","proof"]) || result.version!==1) throw new Error();
      const proof=validateProofReceipt(result.proof,{proof_id:expected.proof_id,subject:result.proof?.subject,
        session_commitment:expected.session_commitment,challenge_commitment:expected.challenge_commitment,
        registration_revision:expected.registration_revision,authenticated_at:result.proof?.authenticated_at},"home-assistant:victoria");
      if (Date.parse(proof.expires_at)<=now || Date.parse(proof.issued_at)>now+1000 || now>=expected.valid_until) throw new Error();
      return proof;
    });
  }
  async #request(path,expected,signal,validate) {
    if (signal?.aborted || this.#active >= 2) throw new Error("handoff_transport_unavailable");
    const start = this.#now(), mono = this.#mono();
    if (!Number.isSafeInteger(start) || !Number.isFinite(mono)) throw new Error("handoff_clock_unavailable");
    const controller = new AbortController(), abort = () => controller.abort();
    signal?.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(abort, 10_000); this.#active++;
    const operation = (async () => {
      const response = await this.#fetch(new URL(path,this.#endpoint).href, {
        method: "POST", redirect: "error", credentials: "omit", cache: "no-store",
        headers: { authorization: `Bearer ${this.#credential}`, "content-type": "application/json" },
        body: JSON.stringify({ version: 1, ...expected }), signal: controller.signal,
      });
      if (response.status !== 200) { await response.body?.cancel().catch(() => {}); throw new Error(); }
      const result = await readHaJson(response, 2048), now = this.#now(), endMono = this.#mono();
      if (!Number.isSafeInteger(now) ||
          !Number.isFinite(endMono) || now < start || endMono < mono || Math.abs(now - start - (endMono - mono)) > 1000 ||
          controller.signal.aborted) throw new Error();
      return validate(result,start,now);
    })().finally(() => { this.#active--; clearTimeout(timer); signal?.removeEventListener("abort", abort); });
    let onAbort;
    const cancelled = new Promise((_, reject) => {
      onAbort = () => reject(new Error("handoff_outcome_unknown"));
      controller.signal.addEventListener("abort", onAbort, { once: true });
      if (controller.signal.aborted) onAbort();
    });
    try { return await Promise.race([operation, cancelled]); }
    catch { throw new Error("handoff_outcome_unknown"); }
    finally { controller.signal.removeEventListener("abort", onAbort); }
  }
}
