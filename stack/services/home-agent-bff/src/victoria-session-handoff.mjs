import crypto from "node:crypto";
import { performance } from "node:perf_hooks";
import { SessionStore } from "./bff.mjs";

const uuid=value=>typeof value==="string" && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value);
const tokenDigest=value=>crypto.createHash("sha256").update(value).digest("hex");

// Private Victoria composition. create requires the authenticated local HTTP
// session, exact origin/CSRF and explicit pairing intent. redeem belongs only
// on a separately authenticated coordinator ingress. Neither method links an
// owner or replaces the subsequent fresh HA proof and reviewed confirmation.
export class VictoriaSessionHandoff {
  #store; #config; #key; #mono; #offers=new Map(); #redeemed=new Map(); #creating=new Set(); #active=0; #closed=false;
  constructor({store,config,commitmentKey,monotonicNow=()=>performance.now()}) {
    if(!(store instanceof SessionStore) || !store.hasSharedSessionRevocation || store.haIssuerId!=="home-assistant:victoria" ||
      store.siteId!=="victoria" || config?.haIssuerId!==store.haIssuerId || config.siteId!==store.siteId ||
      typeof config.haAuthBinding!=="string" || typeof monotonicNow!=="function") throw new Error("invalid_handoff_configuration");
    store.linkingContext("",commitmentKey);
    this.#store=store;this.#config=config;this.#key=Buffer.from(commitmentKey);this.#mono=monotonicNow;
  }
  #clock() {
    const wall=this.#store.now(),mono=this.#mono();
    if(!Number.isSafeInteger(wall) || !Number.isFinite(mono)) throw new Error("handoff_clock_unavailable");
    return {wall,mono};
  }
  usesBoundary(store,config) {return store===this.#store && config===this.#config;}
  #live(start,deadline) {
    const now=this.#clock();
    return !this.#closed && now.wall>=start.wall && now.mono>=start.mono && now.wall<deadline &&
      Math.abs((now.wall-start.wall)-(now.mono-start.mono))<=1000;
  }
  async #context(sessionId) {
    const session=this.#store.get(sessionId);
    if(!session) throw new Error("handoff_session_unavailable");
    await this.#store.revalidate(this.#config,sessionId,session,undefined,this.#store.now(),{forcePrincipalCheck:true});
    const context=this.#store.linkingContext(sessionId,this.#key);
    if(!context || context.issuerId!=="home-assistant:victoria" || context.siteId!=="victoria") throw new Error("handoff_session_unavailable");
    return context;
  }
  #prune() {
    for(const [key,offer] of this.#offers) if(!this.#live(offer.start,offer.deadline)) this.#offers.delete(key);
    for(const [key,offer] of this.#redeemed) if(!this.#live(offer.start,offer.deadline)) this.#redeemed.delete(key);
  }
  async create({sessionId,pairingId}) {
    if(!uuid(pairingId) || typeof sessionId!=="string" || !sessionId || this.#closed) throw new Error("handoff_unavailable");
    this.#prune();
    if(this.#active>=2 || this.#offers.size+this.#redeemed.size+this.#creating.size>=32 || this.#creating.has(sessionId) ||
      this.#redeemed.has(pairingId) || [...this.#offers.values()].some(v=>v.pairingId===pairingId) ||
      [...this.#offers.values()].some(v=>v.sessionId===sessionId)) throw new Error("handoff_busy");
    const start=this.#clock();this.#active++;this.#creating.add(sessionId);
    try {
      const context=await this.#context(sessionId),deadline=Math.min(start.wall+60_000,context.validUntil);
      if(!this.#live(start,deadline)) throw new Error();
      const token=crypto.randomBytes(32).toString("hex");
      this.#offers.set(tokenDigest(token),{sessionId,pairingId,context,start,deadline});
      // No subject, commitment, cookie or HA token is exposed to the browser.
      return Object.freeze({token,pairing_id:pairingId,expires_at:deadline});
    } catch {throw new Error("handoff_unavailable");}
    finally {this.#active--;this.#creating.delete(sessionId);}
  }
  async redeem({token,pairingId}) {
    if(typeof token!=="string" || !/^[a-f0-9]{64}$/.test(token) || !uuid(pairingId) || this.#closed) throw new Error("handoff_unavailable");
    this.#prune();
    if(this.#active>=2) throw new Error("handoff_busy");
    const key=tokenDigest(token),offer=this.#offers.get(key);
    if(!offer || offer.pairingId!==pairingId) throw new Error("handoff_unavailable");
    // Claim before awaiting HA. Failure/unknown delivery never restores a token.
    this.#offers.delete(key);this.#active++;
    try {
      const current=await this.#context(offer.sessionId);
      if(!this.#live(offer.start,offer.deadline) || current.subject!==offer.context.subject ||
        current.sessionCommitment!==offer.context.sessionCommitment) throw new Error();
      this.#redeemed.set(pairingId,{...offer,deadline:Math.min(offer.start.wall+300_000,current.validUntil)});
      return Object.freeze({pairing_id:pairingId,issuer_id:"home-assistant:victoria",site_id:"victoria",
        session_commitment:current.sessionCommitment,valid_until:Math.min(offer.deadline,current.validUntil)});
    } catch {throw new Error("handoff_unavailable");}
    finally {this.#active--;}
  }
  async resolvePairing({pairingId,sessionCommitment,sessionId}) {
    this.#prune();
    const offer=this.#redeemed.get(pairingId);
    if(!offer || this.#active>=2 || offer.context.sessionCommitment!==sessionCommitment ||
      sessionId!==undefined && sessionId!==offer.sessionId) throw new Error("handoff_pairing_unavailable");
    this.#active++;
    try {
      const current=await this.#context(offer.sessionId);
      if(!this.#live(offer.start,offer.deadline) || current.subject!==offer.context.subject ||
        current.sessionCommitment!==offer.context.sessionCommitment) throw new Error("handoff_pairing_unavailable");
      return Object.freeze({sessionId:offer.sessionId,validUntil:Math.min(offer.deadline,current.validUntil)});
    } finally {this.#active--;}
  }
  close() {this.#closed=true;this.#offers.clear();this.#redeemed.clear();this.#key.fill(0);}
}
