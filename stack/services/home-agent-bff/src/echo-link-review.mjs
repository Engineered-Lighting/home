import { SessionStore } from "./bff.mjs";
import { readHaJson } from "./ha-response.mjs";
import { parseSharedIdentityInstant } from "./shared-auth-proof-client.mjs";
import { SharedLinkPairingJournal } from "./shared-link-pairing-journal.mjs";

const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const exact = (v, keys) => v && typeof v === "object" && !Array.isArray(v) &&
  Object.keys(v).length === keys.length && keys.every((key) => Object.hasOwn(v, key));
const subject = (v) => typeof v === "string" && v.length > 0 && [...v].length <= 64 &&
  v.trim() === v && !/[\x00-\x1f\x7f]/.test(v);

// Fixed private service transport. No environment/production entry point enables
// this class. The HTTP caller must enforce fresh login, origin and CSRF.
export class EchoLinkReview {
  #store; #key; #origin; #credential; #fetch; #now; #active = 0;
  constructor({ store, commitmentKey, origin, credential, fetchImpl = fetch, now = Date.now }) {
    const url = new URL(origin);
    if (!(store instanceof SessionStore) || !store.hasSharedSessionRevocation ||
        store.haIssuerId !== "home-assistant:echo" || store.siteId !== "echo" ||
        url.protocol !== "https:" || url.username || url.password ||
        url.pathname !== "/" || url.search || url.hash || typeof credential !== "string" ||
        !DIGEST.test(credential) || typeof fetchImpl !== "function" || typeof now !== "function") {
      throw new Error("link_review_configuration_rejected");
    }
    store.linkingContext("", commitmentKey);
    this.#store = store; this.#key = Buffer.from(commitmentKey);
    this.#origin = url.origin; this.#credential = credential; this.#fetch = fetchImpl; this.#now = now;
  }

  usesStore(store) { return store === this.#store; }

  #context(sessionId) {
    const value = this.#store.linkingContext(sessionId, this.#key);
    if (!value || value.issuerId !== "home-assistant:echo" || value.siteId !== "echo") {
      throw new Error("link_review_session_rejected");
    }
    return value;
  }

  async request(sessionId, operation, input, { signal } = {}) {
    const confirming = operation === "confirm";
    if (!["review", "confirm", "outcome"].includes(operation) ||
        !exact(input, confirming ? ["ceremony_id", "gesture_id", "reviewed_digest"] : ["ceremony_id"]) ||
        typeof input.ceremony_id !== "string" || !UUID.test(input.ceremony_id) ||
        confirming && (typeof input.gesture_id !== "string" || !UUID.test(input.gesture_id) ||
          typeof input.reviewed_digest !== "string" || !DIGEST.test(input.reviewed_digest))) {
      throw new Error("link_review_request_rejected");
    }
    const original = this.#context(sessionId);
    const frozen = Object.freeze({ ...input });
    const body = JSON.stringify({ version: 1, request: { ...frozen,
      subject: original.subject, session_commitment: original.sessionCommitment } });
    return this.#send(sessionId,operation,original,frozen,body,signal);
  }

  prepare(sessionId,pairingId,journal,{signal}={}) {
    if(!(journal instanceof SharedLinkPairingJournal)) throw new Error("link_review_request_rejected");
    const original=this.#context(sessionId),evidence=journal.reviewEvidence(pairingId,original);
    return this.#send(sessionId,"prepare",original,Object.freeze({ceremony_id:pairingId,
      victoriaSubject:evidence.victoria.subject,gestureId:evidence.choice.gesture_id}),
      JSON.stringify({version:1,request:evidence}),signal);
  }

  async #send(sessionId,operation,original,frozen,body,signal) {
    if (signal?.aborted) throw new Error("link_review_cancelled");
    if (this.#active >= 2) throw new Error("link_review_busy");
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal?.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(abort, 10_000);
    this.#active += 1;
    const execution = (async () => {
      const response = await this.#fetch(this.#origin + "/internal/shared-identity/v1/" +
        ({ confirm: "link-confirmation", review: "link-review", outcome: "link-outcome", prepare:"link-review-prepare" })[operation], {
        method: "POST", redirect: "error", credentials: "omit", cache: "no-store",
        headers: { authorization: "Bearer " + this.#credential, "content-type": "application/json" },
        body, signal: controller.signal,
      });
      if (response.status !== 200) {
        await response.body?.cancel().catch(() => {});
        throw new Error();
      }
      const result = await readHaJson(response, 4096);
      const now = this.#now();
      if (!Number.isSafeInteger(now) || result.version !== 1 || result.ceremony_id !== frozen.ceremony_id) throw new Error();
      if (operation !== "review" && operation !== "prepare") {
        const confirmed = parseSharedIdentityInstant(result.confirmed_at);
        if (!exact(result, ["version", "status", "ceremony_id", "link_id", "authorization_generation", "revision", "confirmed_at"]) ||
            result.status !== "confirmed" || typeof result.link_id !== "string" || !UUID.test(result.link_id) ||
            !Number.isSafeInteger(result.authorization_generation) || result.authorization_generation < 2 ||
            result.revision !== 1 || confirmed === null || confirmed > BigInt(now + 1000)*1000n) throw new Error();
      } else {
        const expiry = parseSharedIdentityInstant(result.expires_at);
        if (!exact(result, ["version", "ceremony_id", "gesture_id", "reviewed_digest", "expires_at", "accounts"]) ||
            typeof result.gesture_id !== "string" || !UUID.test(result.gesture_id) ||
            typeof result.reviewed_digest !== "string" || !DIGEST.test(result.reviewed_digest) ||
            expiry === null || expiry <= BigInt(now)*1000n || expiry > BigInt(now+300_000)*1000n ||
            !Array.isArray(result.accounts) || result.accounts.length !== 2 ||
            result.accounts.some((account, index) => {
              const site = index === 0 ? "echo" : "victoria";
              return !exact(account, ["site_id", "issuer_id", "subject"]) || account.site_id !== site ||
                account.issuer_id !== `home-assistant:${site}` || !subject(account.subject) ||
                index === 0 && account.subject !== original.subject;
            })) throw new Error();
        if(operation==="prepare" && (result.accounts[1].subject!==frozen.victoriaSubject || result.gesture_id!==frozen.gestureId)) throw new Error();
      }
      const current = this.#context(sessionId);
      if (current.subject !== original.subject || current.sessionCommitment !== original.sessionCommitment ||
          now < original.checkedAt || now >= original.validUntil || controller.signal.aborted) throw new Error();
      return result;
    })().finally(() => {
      // Keep the slot charged until transport really settles, even if its
      // caller cancels. Never retry an uncertain confirmation automatically.
      this.#active -= 1;
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
    });
    let onAbort;
    const cancelled = new Promise((_, reject) => {
      onAbort = () => reject(new Error("link_review_outcome_unknown"));
      controller.signal.addEventListener("abort", onAbort, { once: true });
      if (controller.signal.aborted) onAbort();
    });
    try { return await Promise.race([execution, cancelled]); }
    catch { throw new Error("link_review_outcome_unknown"); }
    finally { controller.signal.removeEventListener("abort", onAbort); }
  }
}
