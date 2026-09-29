// Provisioned server-side issuer transport; does not link accounts or grant access.
import { HaUnavailableError, exchangeCode, fetchHaSubject, refreshAccessToken, revokeRefreshToken } from "./ha-token-transport.mjs";

function https(value, root = false) {
  const url = new URL(value);
  if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash ||
      (root && url.pathname !== "/")) throw new Error("invalid_ha_issuer_configuration");
  return url;
}

export class QualifiedHaAuth {
  #config; #issuer; #site; #fetch;
  constructor({ issuerId, siteId, origin, clientId, redirectUri, fetchImpl = fetch }) {
    if (!["echo", "victoria"].includes(siteId) || issuerId !== `home-assistant:${siteId}`) {
      throw new Error("invalid_ha_issuer_configuration");
    }
    const ha = https(origin, true), client = https(clientId), redirect = https(redirectUri);
    if (client.origin !== redirect.origin) throw new Error("invalid_ha_issuer_configuration");
    this.#config = Object.freeze({ haUrl: ha.origin, clientId: client.href, redirectUri: redirect.href });
    this.#issuer = issuerId;
    this.#site = siteId;
    this.#fetch = fetchImpl;
  }

  get binding() {
    return JSON.stringify([this.#issuer, this.#site, this.#config.haUrl,
      this.#config.clientId, this.#config.redirectUri]);
  }

  async exchange(code) {
    if (typeof code !== "string" || !/^[a-f0-9]{32,128}$/.test(code)) throw new Error("invalid_ha_login_code");
    try { return await exchangeCode(this.#config, code, this.#fetch); }
    catch { throw new Error("ha_code_exchange_failed"); }
  }

  async verify(accessToken) {
    try {
      const subject = await fetchHaSubject(this.#config, accessToken, this.#fetch);
      if (subject.userId.trim() !== subject.userId || [...subject.userId].length > 64 ||
          /[\x00-\x1f\x7f]/.test(subject.userId)) throw new Error();
      return Object.freeze({ ...subject, haIssuerId: this.#issuer, siteId: this.#site });
    } catch (error) {
      if (error instanceof HaUnavailableError) throw new HaUnavailableError();
      throw new Error("ha_subject_verification_failed");
    }
  }

  async refresh(refreshToken) {
    try { return await refreshAccessToken(this.#config, refreshToken, this.#fetch); }
    catch (error) {
      if (error instanceof HaUnavailableError) throw new HaUnavailableError();
      throw new Error("ha_token_refresh_failed");
    }
  }

  async revoke(refreshToken) {
    try { await revokeRefreshToken(this.#config, refreshToken, this.#fetch); }
    catch { throw new Error("ha_token_revocation_failed"); }
  }
}
