import { readHaJson } from "./ha-response.mjs";
import { parseSharedIdentityInstant } from "./shared-auth-proof-client.mjs";

const PATH = "/internal/shared-identity/v1/session-revocations";
const exact = (value, fields) => value && typeof value === "object" && !Array.isArray(value) &&
  Object.keys(value).length === fields.length && fields.every((field) => Object.hasOwn(value, field));

export function validateSessionRevocation(value) {
  if (!exact(value, ["session_commitment", "revocation_id"]) ||
      typeof value.session_commitment !== "string" || !/^[a-f0-9]{64}$/.test(value.session_commitment) ||
      typeof value.revocation_id !== "string" || !/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value.revocation_id)) {
    throw new Error("invalid_session_revocation");
  }
  return Object.freeze({ session_commitment: value.session_commitment, revocation_id: value.revocation_id });
}

// No automatic retry: the durable sender retains the original ID and decides
// when to repeat the monotonic tombstone after an uncertain acknowledgement.
export class SharedSessionRevocationClient {
  #url; #issuer; #credential; #fetch; #now; #active = 0;
  constructor({ endpoint, issuerId, credential, fetchImpl = fetch, now = Date.now }) {
    const url = new URL(endpoint);
    if (url.protocol !== "https:" || url.pathname !== PATH || url.username || url.password || url.search || url.hash ||
        !["home-assistant:echo", "home-assistant:victoria"].includes(issuerId) ||
        typeof credential !== "string" || !/^[a-f0-9]{64}$/.test(credential) || typeof fetchImpl !== "function" || typeof now !== "function") {
      throw new Error("invalid_session_revocation_transport");
    }
    this.#url = url.href; this.#issuer = issuerId; this.#credential = credential; this.#fetch = fetchImpl; this.#now = now;
  }
  get issuerId() { return this.#issuer; }

  async revoke(value, { signal } = {}) {
    const expected = validateSessionRevocation(value);
    if (signal?.aborted || this.#active >= 2) throw new Error("session_revocation_unavailable");
    const controller = new AbortController(), abort = () => controller.abort();
    signal?.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(abort, 10_000);
    this.#active++;
    const operation = (async () => {
      const response = await this.#fetch(this.#url, { method: "POST", redirect: "error", credentials: "omit", cache: "no-store",
        headers: { authorization: `Bearer ${this.#credential}`, "content-type": "application/json" },
        body: JSON.stringify({ version: 1, revocation: expected }), signal: controller.signal });
      if (response.status !== 200) { await response.body?.cancel().catch(() => {}); throw new Error(); }
      const result = await readHaJson(response, 1024), receipt = result.revocation;
      const stamp = parseSharedIdentityInstant(receipt?.revoked_at), now = this.#now();
      if (!exact(result, ["version", "revocation"]) || result.version !== 1 ||
          !exact(receipt, ["session_commitment", "revocation_id", "issuer_id", "revoked_at"]) ||
          receipt.issuer_id !== this.#issuer || receipt.session_commitment !== expected.session_commitment ||
          receipt.revocation_id !== expected.revocation_id || stamp === null || !Number.isSafeInteger(now) ||
          stamp > (BigInt(now)+1000n)*1000n) throw new Error();
      return Object.freeze({ ...receipt });
    })().finally(() => {
      this.#active--; clearTimeout(timer); signal?.removeEventListener("abort", abort);
    });
    let onAbort;
    const cancelled = new Promise((_, reject) => {
      onAbort = () => reject(new Error("session_revocation_outcome_unknown"));
      controller.signal.addEventListener("abort", onAbort, { once: true });
      if (controller.signal.aborted) onAbort();
    });
    try { return await Promise.race([operation, cancelled]); }
    catch { throw new Error("session_revocation_outcome_unknown"); }
    finally { controller.signal.removeEventListener("abort", onAbort); }
  }
}
