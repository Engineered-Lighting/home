import { SessionStore } from "./bff.mjs";
import { GovernedFreshHaCeremony } from "./governed-fresh-ha-ceremony.mjs";

// Private composition for the separately provisioned Victoria session boundary.
// HTTP callers still enforce the fixed origin, cookie, CSRF and linking intent.
// This class never routes Victoria subjects into the legacy Core namespace.
export class VictoriaLinkCeremony {
  #store; #config; #ceremony; #key;
  constructor({ store, config, ceremony, commitmentKey }) {
    if (!(store instanceof SessionStore) || !store.hasSharedSessionRevocation || store.haIssuerId !== "home-assistant:victoria" || store.siteId !== "victoria" ||
        !(ceremony instanceof GovernedFreshHaCeremony)) throw new Error("victoria_link_configuration_rejected");
    const [issuer, site, , client] = JSON.parse(ceremony.binding);
    if (issuer !== store.haIssuerId || site !== store.siteId || config?.haIssuerId !== issuer || config.siteId !== site ||
        config.haAuthBinding !== ceremony.binding ||
        !(config.allowedOrigins instanceof Set) || config.allowedOrigins.size !== 1 ||
        !config.allowedOrigins.has(new URL(client).origin)) throw new Error("victoria_link_configuration_rejected");
    store.linkingContext("", commitmentKey);
    this.#store = store;
    this.#config = config; // The qualified store binds the factory's exact config.
    this.#ceremony = ceremony; this.#key = Buffer.from(commitmentKey);
  }

  usesBoundary(store,config) { return store===this.#store && config===this.#config; }

  async #context(sessionId) {
    const session = this.#store.get(sessionId);
    if (!session) throw new Error("victoria_link_session_rejected");
    // Each linking operation verifies the actual HA principal, never a cached
    // subject supplied by a browser or a stationary command device.
    await this.#store.revalidate(this.#config, sessionId, session, undefined,
      this.#store.now(), { forcePrincipalCheck: true });
    const context = this.#store.linkingContext(sessionId, this.#key);
    if (!context || context.issuerId !== "home-assistant:victoria" || context.siteId !== "victoria") {
      throw new Error("victoria_link_session_rejected");
    }
    return context;
  }

  #unchanged(sessionId, original) {
    const current = this.#store.linkingContext(sessionId, this.#key);
    if (!current || current.subject !== original.subject || current.sessionCommitment !== original.sessionCommitment ||
        current.issuerId !== original.issuerId || this.#store.now() >= original.validUntil) {
      throw new Error("victoria_link_session_rejected");
    }
  }

  async #run(sessionId, operation) {
    const context = await this.#context(sessionId);
    try {
      const result = await operation(context);
      this.#unchanged(sessionId, context);
      if (result.status === "proved" && (result.proof.issuer_id !== context.issuerId ||
          result.proof.subject !== context.subject || result.proof.session_commitment !== context.sessionCommitment)) {
        throw new Error("victoria_link_session_rejected");
      }
      return result; // Private coordinator evidence, never browser JSON.
    } catch {
      await this.#ceremony.revokeSession(context.sessionCommitment).catch(() => {});
      throw new Error("victoria_link_outcome_unavailable");
    }
  }

  async begin({ sessionId, challengeCommitment, proofId, registrationRevision, validUntil }) {
    if (!Number.isSafeInteger(validUntil)) throw new Error("victoria_link_configuration_rejected");
    return this.#run(sessionId, (context) => this.#ceremony.begin({
      sessionCommitment: context.sessionCommitment, expectedSubject: context.subject,
      challengeCommitment, proofId, registrationRevision, validUntil: Math.min(validUntil, context.validUntil),
    }));
  }

  async submit({ sessionId, handle, input }) {
    return this.#run(sessionId, (context) => this.#ceremony.submit({ handle,
      sessionCommitment: context.sessionCommitment, input }));
  }

  async recover({ sessionId, challengeCommitment, proofId, registrationRevision, validUntil }) {
    if (!Number.isSafeInteger(validUntil)) throw new Error("victoria_link_configuration_rejected");
    return this.#run(sessionId, (context) => this.#ceremony.recover({
      sessionCommitment: context.sessionCommitment, expectedSubject: context.subject,
      challengeCommitment, proofId, registrationRevision, validUntil: Math.min(validUntil, context.validUntil),
    }));
  }
}
