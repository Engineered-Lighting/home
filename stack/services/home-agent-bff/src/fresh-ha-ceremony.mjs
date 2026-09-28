import { performance } from "node:perf_hooks";
import { HaLoginFlow } from "./ha-login-flow.mjs";
import { QualifiedHaAuth } from "./qualified-ha-auth.mjs";
import { HaRevocationOutbox } from "./ha-revocation-outbox.mjs";
import { authenticateFreshHaCode } from "./fresh-ha-subject.mjs";

// Server-internal ceremony coordinator. Begin requires an already authorized
// session/challenge from the future governed ingress. No browser route mounts it.
export class FreshHaCeremony {
  #flow; #auth; #outbox; #wall; #mono; #contexts = new Map(); #starting = new Set(); #completing = 0;
  constructor({ flow, auth, outbox, wallClock = Date.now, monotonicClock = () => performance.now() }) {
    if (!(flow instanceof HaLoginFlow) || !(auth instanceof QualifiedHaAuth) ||
        !(outbox instanceof HaRevocationOutbox) || flow.binding !== auth.binding ||
        outbox.binding !== auth.binding) throw new Error("fresh_ceremony_binding_mismatch");
    this.#flow = flow; this.#auth = auth; this.#outbox = outbox;
    this.#wall = wallClock; this.#mono = monotonicClock;
  }

  #live(context) {
    const wall = this.#wall() - context.wall, mono = this.#mono() - context.mono;
    return !context.revoked && Number.isFinite(wall) && Number.isFinite(mono) &&
      wall >= 0 && mono >= 0 && wall < 300_000 && mono < 300_000 && Math.abs(wall - mono) <= 1000;
  }

  get binding() { return this.#auth.binding; }

  async begin({ sessionCommitment, challengeCommitment, expectedSubject }) {
    if (expectedSubject !== undefined && (typeof expectedSubject !== "string" ||
        !expectedSubject.length || [...expectedSubject].length > 64 ||
        expectedSubject.trim() !== expectedSubject || /[\x00-\x1f\x7f]/.test(expectedSubject))) {
      throw new Error("fresh_ceremony_rejected");
    }
    for (const [handle, context] of this.#contexts) if (!this.#live(context)) this.#contexts.delete(handle);
    if (this.#contexts.size + this.#starting.size >= 32) throw new Error("fresh_ceremony_busy");
    // The lower login-flow drops its handle when HA emits a code. Keep session
    // and challenge exclusivity through exchange, verification and revocation.
    if ([...this.#contexts.values(), ...this.#starting].some((context) =>
      context.sessionCommitment === sessionCommitment || context.challengeCommitment === challengeCommitment)) {
      throw new Error("fresh_ceremony_busy");
    }
    const context = { sessionCommitment, challengeCommitment, expectedSubject,
      wall: this.#wall(), mono: this.#mono(), revoked: false };
    this.#starting.add(context);
    try {
      const form = await this.#flow.begin({ sessionCommitment, challengeCommitment });
      if (!this.#live(context)) {
        await this.#flow.cancel({ handle: form.handle, sessionCommitment });
        throw new Error("fresh_ceremony_expired");
      }
      this.#contexts.set(form.handle, context);
      return form;
    } finally { this.#starting.delete(context); }
  }

  async submit({ handle, sessionCommitment, input }) {
    const context = this.#contexts.get(handle);
    if (!context || context.sessionCommitment !== sessionCommitment || !this.#live(context)) {
      throw new Error("fresh_ceremony_rejected");
    }
    // Reserve before asking HA to produce a code: completion has no waiting queue.
    if (context.busy || this.#completing >= 2) throw new Error("fresh_ceremony_busy");
    context.busy = true; this.#completing++;
    let completed = false;
    try {
      const result = await this.#flow.submit({ handle, sessionCommitment, input });
      if (!this.#live(context)) throw new Error("fresh_ceremony_expired");
      if (result.status !== "code") return result;
      completed = true;
      const principal = await authenticateFreshHaCode({ auth: this.#auth, outbox: this.#outbox, code: result.code });
      if (!this.#live(context) || this.#contexts.get(handle) !== context ||
          result.sessionCommitment !== context.sessionCommitment ||
          result.challengeCommitment !== context.challengeCommitment ||
          result.issuerId !== principal.haIssuerId ||
          (context.expectedSubject !== undefined && principal.userId !== context.expectedSubject)) {
        throw new Error("fresh_ceremony_rejected");
      }
      // Internal result only: never send the code, HA tokens or private subject
      // to the browser. A governed proof transaction must validate its challenge.
      return Object.freeze({ status: "verified", issuerId: principal.haIssuerId, subject: principal.userId,
        sessionCommitment: result.sessionCommitment, challengeCommitment: result.challengeCommitment,
        authenticatedAt: new Date(result.ceremonyStartedAt).toISOString(), expiresAt: result.expiresAt });
    } catch {
      this.#contexts.delete(handle);
      await this.#flow.cancel({ handle, sessionCommitment }).catch(() => {});
      throw new Error("fresh_ceremony_rejected");
    } finally {
      if (completed) this.#contexts.delete(handle);
      context.busy = false; this.#completing--;
    }
  }

  async revokeSession(sessionCommitment) {
    for (const context of this.#starting) if (context.sessionCommitment === sessionCommitment) context.revoked = true;
    for (const [handle, context] of this.#contexts) if (context.sessionCommitment === sessionCommitment) {
      context.revoked = true; this.#contexts.delete(handle);
    }
    await this.#flow.revokeSession(sessionCommitment);
  }
}
