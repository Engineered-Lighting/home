/* Narrow browser client for the fail-closed Home Agent BFF. */
(function initHomeAgentApi(global) {
  "use strict";

  function browserRouteAllowed(method, path) {
    const id = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
    const routes = {
      GET: ["auth/session", "v1/(?:snapshot|onboarding/status|principal-binding-proposal|parent-relationship-proposal|household|relationships)", `v1/memory-transactions/${id}`],
      POST: ["auth/(?:start|logout)", "v1/(?:principal-binding-request(?:/cancel)?|principal-binding-proposal/confirm|parent-relationship-proposal(?:/confirm)?|partner-attestation|household-person|memory-transactions)",
        "shared-identity/(?:review|confirm|outcome|start|handoff|issuance-outcome|auth-begin|auth-submit|auth-outcome|victoria-auth-admit|victoria-auth-outcome|prepare-review)",
        "personal-memory/(?:read|propose|confirm|outcome|sharing-propose|sharing-confirm|sharing-outcome)",
        "lighting/(?:status|propose|confirm|outcome|consent-propose|consent-confirm|consent-outcome)",
        `v1/memory-transactions/${id}/confirm`, `v1/facts/${id}/(?:correction|retraction|forget)-preview`,
        `v1/(?:descriptor-corrections|descriptor-retractions|erasure-requests)/${id}/confirm`],
      PUT: ["v1/preferences/(?:location_memory|travel_greetings)"],
    };
    return typeof path === "string" && !/[\r\n]/.test(path) && Object.hasOwn(routes, method) &&
      routes[method].some((route) => new RegExp(`^/api/agent/${route}$`).test(path));
  }

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
      if (base !== "") throw new Error("unprovisioned_agent_endpoint");
      this.base = "";
      this.csrf = "";
      this.invoke = global.__TAURI__?.core?.invoke || null;
      this.authority = null;
      this.authorityGeneration = 0;
      this.sessionSequence = 0;
      this.pending = new Set();
      this.authorityListeners = new Set();
      if (!this.invoke) {
        const origin = new URL(global.location?.origin || "");
        if (origin.protocol !== "https:" || origin.origin !== global.location.origin) {
          throw new Error("unprovisioned_agent_origin");
        }
        this.registry = global.HomeConnectionRegistry.create([{
          siteId: "echo", issuer: "home-assistant:echo", revision: "echo-browser-session-v1",
          trustRevision: "same-origin-https-v1", transports: { private: origin.href }, capabilities: ["session"],
        }], { call: async (request) => {
          if (request.siteId !== "echo" || request.issuer !== "home-assistant:echo" ||
              request.endpoint !== origin.href || request.operation !== "session") {
            throw new Error("unprovisioned_agent_operation");
          }
          const value = await this.request("/api/agent/auth/session", { signal: request.signal, sessionSequence: request.payload.sequence });
          const authority = value.authority;
          if (value.authenticated !== true || typeof value.user_id !== "string" || !value.user_id ||
              typeof value.csrf_token !== "string" || !value.csrf_token ||
              !authority || authority.version !== 1 || authority.site_id !== "echo" ||
              authority.ha_issuer_id !== "home-assistant:echo") {
            throw new Error("session_authority_mismatch");
          }
          return { siteId: authority.site_id, value };
        } });
        this.registry.select("echo", "private");
      }
    }

    subscribeAuthority(listener) {
      this.authorityListeners.add(listener);
      return () => this.authorityListeners.delete(listener);
    }

    invalidateAuthority() {
      this.authorityGeneration += 1;
      this.sessionSequence += 1;
      this.pending.forEach((controller) => controller.abort());
      this.csrf = "";
      this.authority = null;
      this.registry?.revoke("echo");
      this.authorityListeners.forEach((listener) => { try { listener(); } catch (_) {} });
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

    async request(path, { method = "GET", body, signal, csrfToken = this.csrf, sessionSequence } = {}) {
      if (this.invoke) throw new Error("native_transport_required");
      // Paths originate in the typed methods below, never connection metadata.
      if (!browserRouteAllowed(method, path)) {
        throw new Error("unprovisioned_agent_route");
      }
      if (!path.startsWith("/api/agent/auth/") && !this.authority) throw new Error("authenticated_session_required");
      const generation = this.authorityGeneration;
      const controller = new AbortController();
      controller.sessionSequence = sessionSequence;
      const abort = () => controller.abort();
      if (signal?.aborted) abort();
      signal?.addEventListener("abort", abort, { once: true });
      this.pending.add(controller);
      const current = () => !controller.signal.aborted && generation === this.authorityGeneration &&
        (sessionSequence === undefined || sessionSequence === this.sessionSequence);
      try {
        if (!current()) throw new Error("stale_agent_response");
        const headers = { Accept: "application/json" };
        if (body !== undefined) headers["Content-Type"] = "application/json";
        if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
        const response = await fetch(path, {
          method,
          credentials: "include",
          headers,
          body: body === undefined ? undefined : JSON.stringify(body),
          signal: controller.signal,
          redirect: "error",
        });
        const payload = await response.json().catch(() => ({}));
        if (!current()) throw new Error("stale_agent_response");
        if (!response.ok) {
          if (response.status === 401) this.invalidateAuthority();
          const structured = payload.error && typeof payload.error === "object" ? payload.error : null;
          const code = structured?.code || (typeof payload.error === "string" ? payload.error : `request_failed_${response.status}`);
          const error = new Error(structured?.message || code);
          error.status = response.status;
          error.code = code;
          error.payload = payload;
          throw error;
        }
        return payload;
      } finally {
        this.pending.delete(controller);
        signal?.removeEventListener("abort", abort);
      }
    }

    async session() {
      if (this.invoke) return this.native("native_auth_status");
      if (this.logoutPending) throw new Error("logout_in_progress");
      const sequence = ++this.sessionSequence;
      this.pending.forEach((controller) => { if (controller.sessionSequence !== undefined) controller.abort(); });
      try {
        const result = await this.registry.request("session", { sequence });
        if (sequence !== this.sessionSequence) throw new Error("stale_agent_session");
        const value = result.value;
        const identity = JSON.stringify([value.authority.ha_issuer_id, value.authority.site_id, value.user_id]);
        if (this.authority && (this.authority !== identity || this.csrf !== value.csrf_token)) this.invalidateAuthority();
        this.authority = identity;
        this.csrf = value.csrf_token;
        return value;
      } catch (error) {
        if (sequence === this.sessionSequence) this.invalidateAuthority();
        throw error;
      }
    }

    async login() {
      if (this.invoke) return this.native("native_auth_login");
      if (this.logoutTask) throw new Error("logout_in_progress");
      const value = await this.request("/api/agent/auth/start", { method: "POST", body: {} });
      if (!value.authorize_url) throw new Error("oauth_start_failed");
      this.logoutPending = false;
      this.logoutCsrf = "";
      global.location.assign(value.authorize_url);
    }

    async logout() {
      if (this.invoke) return this.native("native_auth_logout");
      if (this.logoutTask) return this.logoutTask;
      if (!this.logoutPending) this.logoutCsrf = this.csrf;
      this.logoutPending = true;
      this.invalidateAuthority();
      this.logoutTask = (async () => {
        const value = await this.request("/api/agent/auth/logout", { method: "POST", body: {}, csrfToken: this.logoutCsrf });
        if (value?.ok !== true) throw new Error("logout_confirmation_unavailable");
        this.logoutPending = false;
        this.logoutCsrf = "";
        return value;
      })().finally(() => { this.logoutTask = null; });
      // An uncertain delivery leaves the local fence and original logout-only
      // CSRF intact. Refresh cannot silently undo an explicit sign-out request.
      return this.logoutTask;
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
    sharedLinkReview(ceremonyId, { signal } = {}) {
      return this.sharedLinkRequest("review", { ceremony_id: ceremonyId }, signal);
    }
    personalMemory(operation, body = {}, { signal } = {}) {
      if (this.invoke) return Promise.reject(new Error("native_personal_memory_unavailable"));
      const shapes = { read: [], propose: ["version","operation_id","operation","expected_revision","expected_fact_id","preference"],
        confirm: ["version","operation_id","reviewed_digest","gesture_id"], outcome: ["version","operation_id","reviewed_digest"],
        "sharing-propose": ["version","operation_id"], "sharing-confirm": ["version","operation_id","reviewed_digest"],
        "sharing-outcome": ["version","operation_id"] };
      const fields = shapes[operation];
      if (!fields || !body || typeof body !== "object" || Array.isArray(body) ||
          Object.keys(body).length !== fields.length || !fields.every(field => Object.hasOwn(body,field))) {
        return Promise.reject(new Error("invalid_personal_memory_request"));
      }
      return this.request(`/api/agent/personal-memory/${operation}`, {method:"POST",body:structuredClone(body),signal});
    }
    lighting(operation, body = {}, { signal } = {}) {
      if (this.invoke) return Promise.reject(new Error("native_lighting_unavailable"));
      const shapes = { status: [], propose: ["version","operation_id","sites","targets","operation","brightness"],
        confirm: ["version","operation_id","reviewed_digest"], outcome: ["version","operation_id"],
        "consent-propose": ["version","operation_id"], "consent-confirm": ["version","operation_id","reviewed_digest"],
        "consent-outcome": ["version","operation_id"] };
      const fields = shapes[operation];
      if (!fields || !body || typeof body !== "object" || Array.isArray(body) ||
          Object.keys(body).length !== fields.length || !fields.every(field => Object.hasOwn(body,field))) {
        return Promise.reject(new Error("invalid_lighting_request"));
      }
      return this.request(`/api/agent/lighting/${operation}`, {method:"POST",body:structuredClone(body),signal});
    }
    sharedLinkSetup(operation, body, { signal } = {}) {
      if (this.invoke) return Promise.reject(new Error("native_shared_link_unavailable"));
      const shapes = { start: [], handoff: ["pairing_id", "token"], "issuance-outcome": ["pairing_id"],
        "auth-begin": ["pairing_id"], "auth-submit": ["pairing_id", "handle", "input"], "auth-outcome": ["pairing_id"],
        "victoria-auth-admit": ["pairing_id"], "victoria-auth-outcome": ["pairing_id"], "prepare-review": ["pairing_id"] };
      const fields = shapes[operation];
      if (!fields || !body || typeof body !== "object" || Array.isArray(body) || Object.keys(body).length !== fields.length ||
          !fields.every(field => Object.hasOwn(body, field)) || fields.includes("pairing_id") &&
          (typeof body.pairing_id !== "string" || !/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(body.pairing_id)) ||
          operation === "handoff" && (typeof body.token !== "string" || !/^[a-f0-9]{64}$/.test(body.token))) {
        return Promise.reject(new Error("invalid_shared_link_request"));
      }
      return this.request(`/api/agent/shared-identity/${operation}`, { method: "POST", body: structuredClone(body), signal });
    }
    confirmSharedLink(ceremonyId, gestureId, reviewedDigest, { signal } = {}) {
      return this.sharedLinkRequest("confirm", {
        ceremony_id: ceremonyId, gesture_id: gestureId, reviewed_digest: reviewedDigest,
      }, signal);
    }
    sharedLinkOutcome(ceremonyId, { signal } = {}) {
      return this.sharedLinkRequest("outcome", { ceremony_id: ceremonyId }, signal);
    }
    sharedLinkRequest(operation, body, signal) {
      if (this.invoke) return Promise.reject(new Error("native_shared_link_unavailable"));
      const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
      const fields = operation === "confirm" ? ["ceremony_id", "gesture_id", "reviewed_digest"] : ["ceremony_id"];
      if (!["review", "confirm", "outcome"].includes(operation) || !body || typeof body !== "object" ||
          Array.isArray(body) || Object.keys(body).length !== fields.length ||
          !fields.every((field) => Object.hasOwn(body, field)) ||
          typeof body.ceremony_id !== "string" || !uuid.test(body.ceremony_id) ||
          (operation === "confirm" && (typeof body.gesture_id !== "string" || !uuid.test(body.gesture_id) ||
            typeof body.reviewed_digest !== "string" || !/^[a-f0-9]{64}$/.test(body.reviewed_digest)))) {
        return Promise.reject(new Error("invalid_shared_link_request"));
      }
      return this.request(`/api/agent/shared-identity/${operation}`, { method: "POST", body: { ...body }, signal });
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
      attestPartner(body) {
        if (this.invoke) return Promise.reject(new Error("native_partner_attestation_unavailable"));
        return this.request("/api/agent/v1/partner-attestation", { method: "POST", body });
      }
      createHouseholdPerson(body) {
        if (this.invoke) return Promise.reject(new Error("native_household_person_unavailable"));
        return this.request("/api/agent/v1/household-person", { method: "POST", body });
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
  // panel.js loads as a separate <script> and calls randomUuid7 when recording
  // a partner or adding a person. Without this it resolves to nothing in a
  // browser and both handlers die on their first statement -- no request, no
  // error banner, a button that silently does nothing. The module.exports
  // below only runs under Node, so the tests could never catch it.
  global.randomUuid7 = randomUuid7;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { HomeAgentApi, randomUuid7 };
  }
})(typeof window !== "undefined" ? window : globalThis);
