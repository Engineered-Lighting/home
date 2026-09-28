import { SessionStore } from "./bff.mjs";
import { VictoriaHandoffClient } from "./victoria-handoff-client.mjs";
import { SharedLinkIssuanceClient } from "./shared-link-issuance-client.mjs";
import { SharedLinkPairingJournal } from "./shared-link-pairing-journal.mjs";
import { EchoLinkCeremony } from "./echo-link-ceremony.mjs";
import { EchoLinkReview } from "./echo-link-review.mjs";

// Authenticated Echo HTTP routes supply the actual sessionId after origin/CSRF.
// Browser input supplies only a pairing handle and Victoria's one-use offer.
export class EchoLinkStart {
  #store; #config; #key; #journal; #handoff; #issuance; #ceremony; #review; #victoriaOrigin; #fetch; #active = 0;
  constructor({ store, config, commitmentKey, journal, handoff, issuance, ceremony, review, victoriaBrowserOrigin, fetchImpl = fetch }) {
    if (!(store instanceof SessionStore) || !store.hasSharedSessionRevocation ||
        store.siteId !== "echo" || store.haIssuerId !== "home-assistant:echo" ||
        !(journal instanceof SharedLinkPairingJournal) || !(handoff instanceof VictoriaHandoffClient) ||
        !(issuance instanceof SharedLinkIssuanceClient) || !config || typeof fetchImpl !== "function") throw new Error("link_start_configuration_rejected");
    store.linkingContext("", commitmentKey);
    if (ceremony !== undefined && (!(ceremony instanceof EchoLinkCeremony) || !ceremony.usesGovernedStore(store))) throw new Error("link_start_configuration_rejected");
    if(review!==undefined && (!(review instanceof EchoLinkReview) || !review.usesStore(store))) throw new Error("link_start_configuration_rejected");
    if(victoriaBrowserOrigin!==undefined) {
      const url=new URL(victoriaBrowserOrigin);
      if(url.protocol!=="https:" || url.origin!==victoriaBrowserOrigin || url.username || url.password ||
        !(config.allowedOrigins instanceof Set) || [...config.allowedOrigins].some(origin=>new URL(origin).hostname===url.hostname)) throw new Error("link_start_configuration_rejected");
      this.#victoriaOrigin=url.origin;
    }
    this.#store = store; this.#config = config; this.#key = Buffer.from(commitmentKey);
    this.#journal = journal; this.#handoff = handoff; this.#issuance = issuance;
    this.#ceremony = ceremony;
    this.#review=review;
    // The legacy Echo session path verifies the HA subject with this fetch.
    this.#fetch=fetchImpl;
  }
  get authenticationEnabled() { return Boolean(this.#ceremony); }
  get reviewEnabled() { return Boolean(this.#review && this.#ceremony); }
  get browserSetup() { return this.reviewEnabled && this.#victoriaOrigin ? Object.freeze({victoria_origin:this.#victoriaOrigin}) : null; }
  usesStore(store) { return store === this.#store; }
  async #context(sessionId) {
    const session = this.#store.get(sessionId);
    if (!session) throw new Error("link_session_unavailable");
    await this.#store.revalidate(this.#config, sessionId, session, this.#fetch, this.#store.now(), { forcePrincipalCheck: true });
    const context = this.#store.linkingContext(sessionId, this.#key);
    if (!context) throw new Error("link_session_unavailable");
    return context;
  }
  #unchanged(sessionId, original) {
    const current = this.#store.linkingContext(sessionId, this.#key);
    if (!current || current.subject !== original.subject || current.sessionCommitment !== original.sessionCommitment) throw new Error("link_session_unavailable");
    return current;
  }
  async #bounded(work) {
    if (this.#active >= 2) throw new Error("link_start_busy");
    this.#active++;
    try { return await work(); } finally { this.#active--; }
  }
  start(sessionId) {
    return this.#bounded(async () => this.#journal.create(await this.#context(sessionId)));
  }
  redeem(sessionId, input, options = {}) {
    return this.#bounded(async () => {
      if (!input || Object.keys(input).sort().join(",") !== "pairing_id,token" || options.signal?.aborted) throw new Error("link_start_request_rejected");
      const value = Object.freeze({ ...input }), context = await this.#context(sessionId);
      this.#journal.claimHandoff(value.pairing_id, context);
      const handoff = await this.#handoff.redeem(value, options);
      this.#journal.retainHandoff(value.pairing_id, this.#unchanged(sessionId, context), handoff);
      if (options.signal?.aborted) throw new Error("link_start_cancelled");
      const request = this.#journal.claimIssuance(value.pairing_id, this.#unchanged(sessionId, context));
      const receipt = await this.#issuance.begin(request, options);
      this.#journal.recordIssuance(value.pairing_id, this.#unchanged(sessionId, context), receipt);
      return Object.freeze({ version: 1, ceremony_id: value.pairing_id, status: "authentication_required", expires_at: receipt.expires_at });
    });
  }
  recover(sessionId, pairingId, options = {}) {
    return this.#bounded(async () => {
      const context = await this.#context(sessionId);
      const request = this.#journal.recoverIssuance(pairingId, context);
      const receipt = await this.#issuance.recover(request, options);
      this.#journal.recordIssuance(pairingId, this.#unchanged(sessionId, context), receipt);
      return Object.freeze({ version: 1, ceremony_id: pairingId, status: "authentication_required", expires_at: receipt.expires_at });
    });
  }
  authenticate(sessionId, operation, input) {
    return this.#bounded(async () => {
      if (!this.#ceremony || !["begin", "submit", "recover"].includes(operation) || !input ||
          Object.keys(input).sort().join(",") !== (operation === "submit" ? "handle,input,pairing_id" : "pairing_id")) throw new Error("authentication_request_rejected");
      const value = structuredClone(input), context = await this.#context(sessionId);
      let result;
      if (operation === "submit") {
        this.#journal.checkAuthenticationHandle(value.pairing_id, context, value.handle);
        result = await this.#ceremony.submit({ sessionId, handle: value.handle, input: value.input });
      } else {
        const admission = this.#journal.authentication(value.pairing_id, context, { claim: operation === "begin" });
        result = await this.#ceremony[operation === "begin" ? "begin" : "recover"]({ sessionId, ...admission });
      }
      const current = this.#unchanged(sessionId, context);
      if (result.status === "proved") {
        this.#journal.retainAuthenticationProof(value.pairing_id, current, result.proof);
        return Object.freeze({ version: 1, status: "authenticated", site_id: "echo", ceremony_id: value.pairing_id });
      }
      if (result.status !== "form") throw new Error("authentication_outcome_unavailable");
      if (operation === "begin") this.#journal.retainAuthenticationHandle(value.pairing_id, current, result.handle);
      // The HA adapter returns only its bounded, typed login form. Proof receipts,
      // session commitments and account subjects are never browser responses.
      return result;
    });
  }
  victoriaAuthentication(sessionId,operation,input,options={}) {
    return this.#bounded(async()=>{
      if(!["admit","outcome"].includes(operation) || !input || Object.keys(input).join(",")!=="pairing_id") throw new Error("authentication_request_rejected");
      const pairingId=input.pairing_id,context=await this.#context(sessionId);
      const admission=this.#journal.victoriaAuthentication(pairingId,context);
      if(operation==="admit") {
        await this.#handoff.admitAuthentication(admission,options);
        this.#unchanged(sessionId,context);
        return Object.freeze({version:1,status:"authentication_required",site_id:"victoria",ceremony_id:pairingId});
      }
      const proof=await this.#handoff.authenticationOutcome(admission,options);
      this.#journal.retainVictoriaProof(pairingId,this.#unchanged(sessionId,context),proof);
      return Object.freeze({version:1,status:"authenticated",site_id:"victoria",ceremony_id:pairingId});
    });
  }
  prepareReview(sessionId,input,options={}) {
    return this.#bounded(async()=>{
      if(!this.reviewEnabled || !input || Object.keys(input).join(",")!=="pairing_id") throw new Error("link_review_request_rejected");
      const pairingId=input.pairing_id,context=await this.#context(sessionId);
      const result=await this.#review.prepare(sessionId,pairingId,this.#journal,options);
      this.#unchanged(sessionId,context);
      return result;
    });
  }
}
