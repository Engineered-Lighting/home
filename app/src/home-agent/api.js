/* Narrow browser client for the fail-closed Home Agent BFF. */
(function initHomeAgentApi(global) {
  "use strict";

  function randomUuid7() {
    const bytes = new Uint8Array(16);
    global.crypto.getRandomValues(bytes);
    let timestamp = BigInt(Date.now());
    for (let index = 5; index >= 0; index -= 1) {
      bytes[index] = Number(timestamp & 0xffn);
      timestamp >>= 8n;
    }
    bytes[6] = (bytes[6] & 0x0f) | 0x70;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
    return [
      hex.slice(0, 8),
      hex.slice(8, 12),
      hex.slice(12, 16),
      hex.slice(16, 20),
      hex.slice(20),
    ].join("-");
  }

  class HomeAgentApi {
    constructor(base = "") {
      this.base = String(base || "").replace(/\/$/, "");
      this.csrf = "";
      this.invoke = global.__TAURI__?.core?.invoke || null;
    }

    async native(command, args = {}) {
      if (!this.invoke) throw new Error("native_transport_unavailable");
      return this.invoke(command, args);
    }

    nativePayload(response) {
      const status = Number(response?.status || 0);
      const payload = response?.payload || {};
      if (status < 200 || status >= 300) {
        const structured = payload.error && typeof payload.error === "object" ? payload.error : null;
        const code = structured?.code || (typeof payload.error === "string" ? payload.error : `request_failed_${status}`);
        const error = new Error(structured?.message || code);
        error.status = status;
        error.code = code;
        error.payload = payload;
        throw error;
      }
      return payload;
    }

    async request(path, { method = "GET", body } = {}) {
      const headers = { Accept: "application/json" };
      if (body !== undefined) headers["Content-Type"] = "application/json";
      if (method !== "GET" && this.csrf) headers["X-CSRF-Token"] = this.csrf;
      const response = await fetch(`${this.base}${path}`, {
        method,
        credentials: "include",
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const structured = payload.error && typeof payload.error === "object" ? payload.error : null;
        const code = structured?.code || (typeof payload.error === "string" ? payload.error : `request_failed_${response.status}`);
        const error = new Error(structured?.message || code);
        error.status = response.status;
        error.code = code;
        error.payload = payload;
        throw error;
      }
      return payload;
    }

    async session() {
      if (this.invoke) return this.native("native_auth_status");
      const value = await this.request("/api/agent/auth/session");
      this.csrf = value.csrf_token || "";
      return value;
    }

    async login() {
      if (this.invoke) return this.native("native_auth_login");
      const value = await this.request("/api/agent/auth/start", { method: "POST", body: {} });
      if (!value.authorize_url) throw new Error("oauth_start_failed");
      global.location.assign(value.authorize_url);
    }

    async logout() {
      if (this.invoke) return this.native("native_auth_logout");
      const value = await this.request("/api/agent/auth/logout", { method: "POST", body: {} });
      this.csrf = "";
      return value;
    }

    async returnHome() {
      if (this.invoke) return this.native("close_agent_window");
      global.close();
    }

    snapshot() {
      if (this.invoke) return this.native("native_agent_snapshot").then((value) => this.nativePayload(value));
      return this.request("/api/agent/v1/snapshot");
    }
    initiatives() {
      if (!this.invoke) return Promise.reject(new Error("native_transport_required"));
      return this.native("native_agent_list_initiatives")
        .then((value) => this.nativePayload(value));
    }
    claimInitiative(initiativeId) {
      if (!this.invoke) return Promise.reject(new Error("native_transport_required"));
      return this.native("native_agent_claim_initiative", { initiativeId })
        .then((value) => this.nativePayload(value));
    }
    privateLocalities() {
      if (!this.invoke) return Promise.reject(new Error("native_transport_required"));
      return this.native("native_agent_list_private_localities")
        .then((value) => this.nativePayload(value));
    }
    previewPrivateLocality(value) {
      if (!this.invoke) return Promise.reject(new Error("native_transport_required"));
      return this.native("native_agent_preview_private_locality", value)
        .then((response) => this.nativePayload(response));
    }
    confirmPrivateLocality(value) {
      if (!this.invoke) return Promise.reject(new Error("native_transport_required"));
      return this.native("native_agent_confirm_private_locality", value)
        .then((response) => this.nativePayload(response));
    }
    onboardingStatus() {
      if (this.invoke) return Promise.reject(new Error("native_onboarding_status_unavailable"));
      return this.request("/api/agent/v1/onboarding/status");
    }
    principalBindingProposal() {
      if (this.invoke) return Promise.reject(new Error("native_principal_binding_unavailable"));
      return this.request("/api/agent/v1/principal-binding-proposal");
    }
    requestPrincipalBinding() {
      if (this.invoke) return Promise.reject(new Error("native_principal_binding_unavailable"));
      return this.request("/api/agent/v1/principal-binding-request", {
        method: "POST",
        body: {},
      });
    }
    cancelPrincipalBindingRequest() {
      if (this.invoke) return Promise.reject(new Error("native_principal_binding_unavailable"));
      return this.request("/api/agent/v1/principal-binding-request/cancel", {
        method: "POST",
        body: {},
      });
    }
    confirmPrincipalBinding(proposalDigest, confirmationNonce) {
      if (this.invoke) return Promise.reject(new Error("native_principal_binding_unavailable"));
      return this.request("/api/agent/v1/principal-binding-proposal/confirm", {
        method: "POST",
        body: {
          proposal_digest: proposalDigest,
          confirmation_nonce: confirmationNonce,
        },
      });
    }
    parentRelationshipStatus() {
      if (this.invoke) return Promise.reject(new Error("native_parent_relationship_unavailable"));
      return this.request("/api/agent/v1/parent-relationship-proposal");
    }

      // The People tab. Browser-only by construction: these paths are in the
      // BFF's ROUTES and the origin's BROWSER_API_ROUTES, deliberately not in
      // NATIVE_ROUTES, so a desktop bearer must be refused here rather than
      // discovering a 404 at the origin.
      household() {
        if (this.invoke) return Promise.reject(new Error("native_household_unavailable"));
        return this.request("/api/agent/v1/household");
      }
      relationships() {
        if (this.invoke) return Promise.reject(new Error("native_relationships_unavailable"));
        return this.request("/api/agent/v1/relationships");
      }
    stageParentRelationship() {
      if (this.invoke) return Promise.reject(new Error("native_parent_relationship_unavailable"));
      return this.request("/api/agent/v1/parent-relationship-proposal", {
        method: "POST",
        body: { ceremony_id: randomUuid7() },
      });
    }
    confirmParentRelationship(proposalId, proposalDigest) {
      if (this.invoke) return Promise.reject(new Error("native_parent_relationship_unavailable"));
      return this.request("/api/agent/v1/parent-relationship-proposal/confirm", {
        method: "POST",
        body: {
          proposal_id: proposalId,
          proposal_digest: proposalDigest,
          confirmation_nonce: global.crypto.randomUUID(),
        },
      });
    }
    explainDescriptor(placeId) {
      if (!this.invoke) return Promise.reject(new Error("native_transport_required"));
      return this.native("native_agent_explain_descriptor", { placeId })
        .then((value) => this.nativePayload(value));
    }
    queryParentPresence(placeId) {
      if (!this.invoke) return Promise.reject(new Error("native_transport_required"));
      return this.native("native_agent_query_parent_presence", { placeId })
        .then((value) => this.nativePayload(value));
    }
    setPreference(key, enabled) {
      if (this.invoke) return this.native("native_agent_set_preference", { key, enabled: Boolean(enabled) })
        .then((value) => this.nativePayload(value));
      return this.request(`/api/agent/v1/preferences/${encodeURIComponent(key)}`, {
        method: "PUT",
        body: {
          key,
          enabled: Boolean(enabled),
          confirmation_artifact_id: global.crypto.randomUUID(),
        },
      });
    }
    disablePreference(key) {
      // Rollback/containment UI uses a direction-fixed method so it cannot
      // accidentally turn a stale consent back on through toggle logic.
      return this.setPreference(key, false);
    }
    proposeMemory(visitId, exactText) {
      if (this.invoke) return this.native("native_agent_propose_memory", { visitId, exactText })
        .then((value) => this.nativePayload(value));
      return this.request("/api/agent/v1/memory-transactions", {
        method: "POST",
        body: { visit_id: visitId, exact_text: exactText },
      });
    }
    memoryTransaction(id) {
      if (this.invoke) return this.native("native_agent_get_memory", { transactionId: id })
        .then((value) => this.nativePayload(value));
      return this.request(`/api/agent/v1/memory-transactions/${encodeURIComponent(id)}`);
    }
    confirmMemory(id, previewDigest) {
      if (this.invoke) return this.native("native_agent_confirm_memory", { transactionId: id, previewDigest })
        .then((value) => this.nativePayload(value));
      return this.request(`/api/agent/v1/memory-transactions/${encodeURIComponent(id)}/confirm`, {
        method: "POST",
        body: {
          preview_digest: previewDigest,
          confirmation_artifact_id: global.crypto.randomUUID(),
        },
      });
    }
    previewCorrection(factId, exactText) {
      if (this.invoke) return this.native("native_agent_preview_correction", { factId, exactText })
        .then((value) => this.nativePayload(value));
      return this.request(`/api/agent/v1/facts/${encodeURIComponent(factId)}/correction-preview`, {
        method: "POST",
        body: { exact_text: exactText },
      });
    }
    confirmCorrection(id, previewDigest) {
      if (this.invoke) return this.native("native_agent_confirm_correction", { transactionId: id, previewDigest })
        .then((value) => this.nativePayload(value));
      return this.request(`/api/agent/v1/descriptor-corrections/${encodeURIComponent(id)}/confirm`, {
        method: "POST",
        body: {
          preview_digest: previewDigest,
          confirmation_artifact_id: global.crypto.randomUUID(),
        },
      });
    }
    previewRetraction(factId) {
      if (this.invoke) return this.native("native_agent_preview_retraction", { factId })
        .then((value) => this.nativePayload(value));
      return this.request(`/api/agent/v1/facts/${encodeURIComponent(factId)}/retraction-preview`, {
        method: "POST",
        body: {},
      });
    }
    confirmRetraction(id, previewDigest) {
      if (this.invoke) return this.native("native_agent_confirm_retraction", { transactionId: id, previewDigest })
        .then((value) => this.nativePayload(value));
      return this.request(`/api/agent/v1/descriptor-retractions/${encodeURIComponent(id)}/confirm`, {
        method: "POST",
        body: {
          preview_digest: previewDigest,
          confirmation_artifact_id: global.crypto.randomUUID(),
        },
      });
    }
    previewForget(factId) {
      if (this.invoke) return this.native("native_agent_preview_forget", { factId })
        .then((value) => this.nativePayload(value));
      return this.request(`/api/agent/v1/facts/${encodeURIComponent(factId)}/forget-preview`, {
        method: "POST",
        body: {},
      });
    }
    confirmForget(id, previewDigest) {
      if (this.invoke) return this.native("native_agent_confirm_forget", { requestId: id, previewDigest })
        .then((value) => this.nativePayload(value));
      return this.request(`/api/agent/v1/erasure-requests/${encodeURIComponent(id)}/confirm`, {
        method: "POST",
        body: { preview_digest: previewDigest },
      });
    }
  }

  global.HomeAgentApi = HomeAgentApi;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { HomeAgentApi, randomUuid7 };
  }
})(typeof window !== "undefined" ? window : globalThis);
