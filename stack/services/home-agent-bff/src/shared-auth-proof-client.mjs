// Internal service transport only. Callers must first authenticate HA and bind
// the submission to a governed challenge. No production route enables this yet.
import { readHaJson } from "./ha-response.mjs";

const PATH = "/internal/shared-identity/v1/auth-proofs";
const LOOKUP_PATH = "/internal/shared-identity/v1/auth-proof-outcome";
const FIELDS = ["proof_id", "subject", "session_commitment", "challenge_commitment",
  "authenticated_at", "registration_revision"];
const exactKeys = (value, fields) => value && typeof value === "object" &&
  !Array.isArray(value) && Object.keys(value).length === fields.length &&
  fields.every((key) => Object.hasOwn(value, key));
const digest = (value) => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
// PostgreSQL/Python receipts retain microseconds. Date.parse alone truncates
// them and normalizes some impossible calendar dates, hiding substitutions.
function instant(value) {
  if (typeof value !== "string") return null;
  const match = /^(\d{4}-\d\d-\d\d)T(\d\d:\d\d:\d\d)(?:\.(\d{1,6}))?(Z|([+-])(\d\d):(\d\d))$/.exec(value);
  if (!match || match[1].startsWith("0000-") || Number(match[2].slice(0, 2)) > 23) return null;
  const civil = `${match[1]}T${match[2]}`;
  const milliseconds = Date.parse(`${civil}Z`);
  if (!Number.isFinite(milliseconds) || new Date(milliseconds).toISOString() !== `${civil}.000Z`) return null;
  const hours = Number(match[6] || 0), minutes = Number(match[7] || 0);
  if (hours > 23 || minutes > 59) return null;
  const offset = (hours * 60 + minutes) * 60 * (match[5] === "-" ? -1 : 1);
  return BigInt(milliseconds) * 1000n + BigInt((match[3] || "").padEnd(6, "0")) - BigInt(offset) * 1_000_000n;
}

export { instant as parseSharedIdentityInstant };

export function validateProofSubmission(submission) {
  if (!exactKeys(submission, FIELDS) || typeof submission.proof_id !== "string" ||
      !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(submission.proof_id) ||
      typeof submission.subject !== "string" || !submission.subject.length ||
      [...submission.subject].length > 64 || submission.subject.trim() !== submission.subject ||
      /[\x00-\x1f\x7f]/.test(submission.subject) ||
      !digest(submission.session_commitment) || !digest(submission.challenge_commitment) ||
      instant(submission.authenticated_at) === null || !Number.isSafeInteger(submission.registration_revision) ||
      submission.registration_revision < 1) throw new Error("invalid_proof_submission");
  return Object.freeze(Object.fromEntries(FIELDS.map((key) => [key, submission[key]])));
}

export function validateProofReceipt(proof, expected, issuer) {
  expected = validateProofSubmission(expected);
  const authenticated = instant(proof?.authenticated_at), issued = instant(proof?.issued_at), expires = instant(proof?.expires_at);
  if (!exactKeys(proof, [...FIELDS, "issuer_id", "issued_at", "expires_at"]) || proof.issuer_id !== issuer ||
      FIELDS.filter((key) => key !== "authenticated_at").some((key) => proof[key] !== expected[key]) ||
      authenticated === null || issued === null || expires === null ||
      authenticated !== instant(expected.authenticated_at) || issued < authenticated ||
      issued >= expires || expires > authenticated+300_000_000n) throw new Error("invalid_proof_receipt");
  return Object.freeze({ ...proof });
}

export class SharedAuthProofClient {
  #endpoint; #issuer; #credential; #fetch; #now; #active = 0;
  constructor({ endpoint, issuerId, credential, fetchImpl = fetch, now = Date.now }) {
    const url = new URL(endpoint);
    if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash ||
        url.pathname !== PATH || !["home-assistant:echo", "home-assistant:victoria"].includes(issuerId) ||
        !digest(credential) || typeof now !== "function") throw new Error("invalid_proof_transport_configuration");
    this.#endpoint = url.href;
    this.#issuer = issuerId;
    this.#credential = credential;
    this.#fetch = fetchImpl;
    this.#now = now;
  }

  get issuerId() { return this.#issuer; }

  async issue(submission, { signal } = {}) {
    return this.#request(submission, this.#endpoint, signal);
  }

  async inspect(submission, { signal } = {}) {
    return this.#request(submission, new URL(LOOKUP_PATH, this.#endpoint).href, signal);
  }

  async #request(submission, endpoint, signal) {
    const expected = validateProofSubmission(submission);
    if (signal?.aborted) throw new Error("proof_request_cancelled");
    if (this.#active >= 2) throw new Error("proof_transport_busy");
    // Copy before the first await: caller mutation must not change receipt checks.
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal?.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(abort, 10_000);
    this.#active += 1;
    const operation = (async () => {
      const response = await this.#fetch(endpoint, {
        method: "POST", redirect: "error", credentials: "omit", cache: "no-store",
        headers: { "authorization": `Bearer ${this.#credential}`, "content-type": "application/json" },
        body: JSON.stringify({ version: 1, proof: expected }), signal: controller.signal,
      });
      if (response.status !== 200) {
        await response.body?.cancel().catch(() => {});
        throw new Error();
      }
      const result = await readHaJson(response, 4096);
      const proof = validateProofReceipt(result.proof, expected, this.#issuer);
      const expires = instant(proof.expires_at);
      const deliveredAt = this.#now();
      if (!exactKeys(result, ["version", "proof"]) || result.version !== 1 ||
          !Number.isSafeInteger(deliveredAt) || expires <= BigInt(deliveredAt) * 1000n ||
          instant(proof.issued_at) > (BigInt(deliveredAt)+1000n) * 1000n) throw new Error();
      return Object.freeze(proof);
    })().finally(() => {
      // Cancellation never frees a slot while the underlying transport is live.
      this.#active -= 1;
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
    });
    let onAbort;
    const cancelled = new Promise((_, reject) => {
      onAbort = () => reject(new Error("proof_outcome_unknown"));
      controller.signal.addEventListener("abort", onAbort, { once: true });
      if (controller.signal.aborted) onAbort();
    });
    try {
      return await Promise.race([operation, cancelled]);
    } catch {
      // Includes uncertain commit acknowledgement. Never retry or create an ID.
      throw new Error("proof_outcome_unknown");
    } finally {
      controller.signal.removeEventListener("abort", onAbort);
    }
  }
}
