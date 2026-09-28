import { SessionStore } from "./bff.mjs";
import { FreshHaCeremony } from "./fresh-ha-ceremony.mjs";
import { GovernedFreshHaCeremony } from "./governed-fresh-ha-ceremony.mjs";

// Internal composition only. The HTTP caller must enforce authenticated cookie,
// origin/CSRF and explicit linking intent. Challenge admission belongs to Core.
// This adapter neither grants linking authority nor exposes verified subjects.
export class EchoLinkCeremony {
  #store; #ceremony; #key;
  constructor({ store, ceremony, commitmentKey }) {
    if (!(store instanceof SessionStore) || !store.hasSharedSessionRevocation || store.haIssuerId !== "home-assistant:echo" || store.siteId !== "echo" ||
        !(ceremony instanceof FreshHaCeremony || ceremony instanceof GovernedFreshHaCeremony)) {
      throw new Error("echo_link_configuration_rejected");
    }
    const [issuer, site] = JSON.parse(ceremony.binding);
    if (issuer !== "home-assistant:echo" || site !== "echo") throw new Error("echo_link_configuration_rejected");
    // Validate dedicated key policy without requiring an existing session.
    store.linkingContext("", commitmentKey);
    this.#store = store; this.#ceremony = ceremony; this.#key = Buffer.from(commitmentKey);
  }

  usesGovernedStore(store) { return store === this.#store && this.#ceremony instanceof GovernedFreshHaCeremony; }

  #context(sessionId) {
    const context = this.#store.linkingContext(sessionId, this.#key);
    if (!context) throw new Error("echo_link_session_rejected");
    return context;
  }

  #unchanged(sessionId, original) {
    const current = this.#context(sessionId);
    if (current.subject !== original.subject || current.issuerId !== original.issuerId ||
        current.sessionCommitment !== original.sessionCommitment) throw new Error("echo_link_session_rejected");
    return current;
  }

  async begin({ sessionId, challengeCommitment, proofId, registrationRevision, validUntil }) {
    const context = this.#context(sessionId);
    const form = await this.#ceremony.begin({ sessionCommitment: context.sessionCommitment,
      challengeCommitment, expectedSubject: context.subject, proofId, registrationRevision, validUntil });
    try {
      this.#unchanged(sessionId, context);
      return form;
    } catch {
      await this.#ceremony.revokeSession(context.sessionCommitment);
      throw new Error("echo_link_session_rejected");
    }
  }

  async submit({ sessionId, handle, input }) {
    const context = this.#context(sessionId);
    const result = await this.#ceremony.submit({ handle, sessionCommitment: context.sessionCommitment, input });
    try {
      this.#unchanged(sessionId, context);
      if (result.status === "verified" && (result.issuerId !== context.issuerId ||
          result.subject !== context.subject || result.sessionCommitment !== context.sessionCommitment)) {
        throw new Error("echo_link_session_rejected");
      }
      if (result.status === "proved" && (result.proof.issuer_id !== context.issuerId ||
          result.proof.subject !== context.subject || result.proof.session_commitment !== context.sessionCommitment)) {
        throw new Error("echo_link_session_rejected");
      }
      // Verified result is server-internal: forward only to the governed proof
      // transaction after challenge admission; never serialize it to a browser.
      return result;
    } catch {
      await this.#ceremony.revokeSession(context.sessionCommitment);
      throw new Error("echo_link_session_rejected");
    }
  }

  async recover({ sessionId, challengeCommitment, proofId, registrationRevision, validUntil }) {
    if (!(this.#ceremony instanceof GovernedFreshHaCeremony)) throw new Error("echo_link_recovery_unavailable");
    const context = this.#context(sessionId);
    const result = await this.#ceremony.recover({ sessionCommitment: context.sessionCommitment,
      expectedSubject: context.subject, challengeCommitment, proofId, registrationRevision, validUntil });
    try {
      this.#unchanged(sessionId, context);
      if (result.proof.issuer_id !== context.issuerId || result.proof.subject !== context.subject ||
          result.proof.session_commitment !== context.sessionCommitment) throw new Error();
      return result;
    } catch {
      await this.#ceremony.revokeSession(context.sessionCommitment);
      throw new Error("echo_link_session_rejected");
    }
  }
}
