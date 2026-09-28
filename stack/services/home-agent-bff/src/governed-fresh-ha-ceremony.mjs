import { FreshHaCeremony } from "./fresh-ha-ceremony.mjs";
import { SharedAuthProofClient, parseSharedIdentityInstant } from "./shared-auth-proof-client.mjs";
import { SharedAuthProofJournal } from "./shared-auth-proof-journal.mjs";
import { performance } from "node:perf_hooks";

// Server-owned challenge admission only. The caller must obtain the fixed
// session/challenge, revision, proof ID and deadline from its governed ceremony.
// This class neither authenticates that admission nor mounts a browser route.
export class GovernedFreshHaCeremony {
  #ceremony; #client; #journal; #now; #mono; #contexts = new Map(); #starting = new Set();
  #recovering = new Map();
  constructor({ ceremony, client, journal, now = Date.now, monotonicClock = () => performance.now() }) {
    if (!(ceremony instanceof FreshHaCeremony) || !(client instanceof SharedAuthProofClient) ||
        !(journal instanceof SharedAuthProofJournal) || typeof now !== "function" || typeof monotonicClock !== "function" ||
        JSON.parse(ceremony.binding)[0] !== client.issuerId || journal.issuerId !== client.issuerId) {
      throw new Error("governed_ceremony_binding_rejected");
    }
    this.#ceremony = ceremony; this.#client = client; this.#journal = journal; this.#now = now; this.#mono = monotonicClock;
  }
  #live(context) {
    const now = this.#now(), elapsed = this.#mono()-context.mono, wall = now-context.started;
    return Number.isSafeInteger(now) && Number.isFinite(elapsed) && wall >= 0 && elapsed >= 0 &&
      Math.abs(wall-elapsed) <= 1000 && now < context.validUntil && !context.controller.signal.aborted;
  }
  get binding() { return this.#ceremony.binding; }
  async begin({ sessionCommitment, challengeCommitment, proofId, registrationRevision, validUntil, expectedSubject }) {
    const now = this.#now();
    for (const [handle, context] of this.#contexts) {
      if (context.validUntil <= now && !context.busy) this.#contexts.delete(handle);
    }
    if (!Number.isSafeInteger(now) || !Number.isSafeInteger(validUntil) || validUntil <= now || validUntil > now+300_000 ||
        typeof proofId !== "string" || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(proofId) ||
        !Number.isSafeInteger(registrationRevision) || registrationRevision < 1 || this.#contexts.size+this.#starting.size+this.#recovering.size >= 32 ||
        [...this.#contexts.values(), ...this.#starting].some((item) => item.proofId === proofId) ||
        this.#journal.inspect(proofId).state !== "not_found") throw new Error("governed_challenge_rejected");
    const context = { sessionCommitment, challengeCommitment, proofId, registrationRevision, validUntil,
      started: now, mono: this.#mono(), busy: false, controller: new AbortController() };
    this.#starting.add(context);
    try {
      const form = await this.#ceremony.begin({ sessionCommitment, challengeCommitment, expectedSubject });
      if (!this.#live(context)) {
        await this.#ceremony.revokeSession(sessionCommitment);
        throw new Error("governed_challenge_expired");
      }
      this.#contexts.set(form.handle, context);
      return form;
    } finally { this.#starting.delete(context); }
  }
  async submit({ handle, sessionCommitment, input }) {
    const context = this.#contexts.get(handle);
    if (!context || context.busy || context.sessionCommitment !== sessionCommitment ||
        !this.#live(context)) throw new Error("governed_ceremony_rejected");
    context.busy = true;
    try {
      const result = await this.#ceremony.submit({ handle, sessionCommitment, input });
      if (this.#contexts.get(handle) !== context || !this.#live(context)) throw new Error();
      if (result.status !== "verified") return result;
      if (result.issuerId !== this.#journal.issuerId || result.sessionCommitment !== sessionCommitment ||
          result.challengeCommitment !== context.challengeCommitment) throw new Error();
      this.#journal.retain({ proof_id: context.proofId, subject: result.subject,
        session_commitment: sessionCommitment, challenge_commitment: context.challengeCommitment,
        authenticated_at: result.authenticatedAt, registration_revision: context.registrationRevision });
      const proof = await this.#journal.dispatch(context.proofId, this.#client, { signal: context.controller.signal });
      if (this.#contexts.get(handle) !== context || !this.#live(context)) throw new Error();
      this.#contexts.delete(handle);
      return Object.freeze({ status: "proved", proof }); // Private coordinator result, never browser JSON.
    } catch {
      this.#contexts.delete(handle);
      await this.#ceremony.revokeSession(sessionCommitment).catch(() => {});
      throw new Error("governed_ceremony_outcome_unavailable");
    } finally { context.busy = false; }
  }
  async revokeSession(sessionCommitment) {
    for (const context of this.#recovering.values()) if (context.sessionCommitment === sessionCommitment) context.controller.abort();
    for (const context of this.#starting) if (context.sessionCommitment === sessionCommitment) context.controller.abort();
    for (const [handle, context] of this.#contexts) if (context.sessionCommitment === sessionCommitment) {
      context.controller.abort(); this.#contexts.delete(handle);
    }
    await this.#ceremony.revokeSession(sessionCommitment);
  }

  async recover({ sessionCommitment, challengeCommitment, proofId, registrationRevision, validUntil, expectedSubject }) {
    // Admission is server-owned just as in begin(). Recovery requires a known
    // subject and original challenge; possession of a proof ID is insufficient.
    const now = this.#now(), retained = this.#journal.inspect(proofId);
    if (!Number.isSafeInteger(now) || !Number.isSafeInteger(validUntil) || validUntil <= now || validUntil > now+300_000 ||
        this.#recovering.has(proofId) || this.#contexts.size+this.#starting.size+this.#recovering.size >= 32 ||
        !["completed", "indeterminate"].includes(retained.state) ||
        typeof expectedSubject !== "string" || !expectedSubject ||
        retained.submission.subject !== expectedSubject || retained.submission.session_commitment !== sessionCommitment ||
        retained.submission.challenge_commitment !== challengeCommitment || retained.submission.registration_revision !== registrationRevision) {
      throw new Error("governed_recovery_rejected");
    }
    const context = { sessionCommitment, validUntil, started: now, mono: this.#mono(), controller: new AbortController() };
    this.#recovering.set(proofId, context);
    try {
      const options = { signal: context.controller.signal };
      // Even a locally completed receipt must pass current database authority.
      const proof = retained.state === "indeterminate"
        ? await this.#journal.reconcile(proofId, this.#client, options)
        : await this.#client.inspect(retained.submission, options);
      if (retained.state === "completed" && ["issued_at", "expires_at"].some((field) =>
        parseSharedIdentityInstant(proof[field]) !== parseSharedIdentityInstant(retained.receipt[field]))) throw new Error();
      if (!this.#live(context)) throw new Error();
      return Object.freeze({ status: "proved", proof });
    } catch {
      throw new Error("governed_ceremony_outcome_unavailable");
    } finally { this.#recovering.delete(proofId); }
  }
}
