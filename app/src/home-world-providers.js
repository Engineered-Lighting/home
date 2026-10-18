/* home-world-providers.js — runtime configuration for the Apartment world view.
 *
 * One job: fetch the Google Maps Platform key for Photorealistic 3D Tiles from
 * the first-party config endpoint and hand it to the renderer. Nothing else in
 * the client knows a provider URL, and no other provider credential ever comes
 * near the browser.
 *
 * A classic script, not an ES module: tools/check-jsx.js parses every top-level
 * file under app/src with new Function(src), which rejects import and export.
 *
 * Why the key reaches the browser at all, since that is the obvious objection:
 * CesiumJS streams tiles directly from Google using it, and Google's session
 * token is bound to the root tileset request, so proxying it would break the
 * tileset rather than protect anything. The mitigations live where they can
 * actually work: the key is restricted at Google by HTTP referrer and to the Map
 * Tiles API SKU alone, the endpoint that serves it is authenticated and
 * no-store, and it is never committed, compiled in, or logged.
 *
 * Rules this file keeps:
 *
 *   Never log the key, never put it in an error message, never write it to
 *   storage, and never attach it to a diagnostic. `describe()` exists so callers
 *   have something safe to report.
 *
 *   Never retry forever. A missing key is a configuration state, not a transient
 *   fault; hammering the endpoint would not fix it.
 */

(function initialiseHomeWorldProviders() {
  "use strict";

  var CONFIG_PATH = "/api/world/v1/config";
  var REQUEST_TIMEOUT_MS = 4000;

  /* Distinguishes "never asked" from "asked and there is none", which the UI
   * needs in order to say something true to the user. */
  var STATUS = {
    IDLE: "idle",
    LOADING: "loading",
    READY: "ready",
    UNCONFIGURED: "unconfigured",
    UNAVAILABLE: "unavailable",
  };

  var state = {
    status: STATUS.IDLE,
    // Held only in this closure. Nothing else stores it.
    tilesKey: "",
    // Safe to surface anywhere: a diagnostic must never need the value.
    keyLength: 0,
    reason: "",
    fetchedAt: 0,
  };

  var inFlight = null;
  var inFlightController = null;
  var requestGeneration = 0;

  function endpointUrl() {
    try {
      return new URL(CONFIG_PATH, window.location.origin).href;
    } catch (error) {
      return CONFIG_PATH;
    }
  }

  function fetchConfig(controller) {
    var timer = window.setTimeout(function abort() {
      if (controller) controller.abort();
    }, REQUEST_TIMEOUT_MS);

    return window.fetch(endpointUrl(), {
      method: "GET",
      headers: { Accept: "application/json" },
      // A credential must never be served from a cache, on either side.
      cache: "no-store",
      credentials: "same-origin",
      signal: controller ? controller.signal : undefined,
    }).then(function onResponse(response) {
      window.clearTimeout(timer);
      if (response.status === 503) {
        // The endpoint says so explicitly rather than returning an empty key,
        // so "not set up yet" stays distinguishable from "set up and broken".
        return { status: STATUS.UNCONFIGURED, reason: "tiles_key_unconfigured" };
      }
      if (response.status === 401 || response.status === 403) {
        return { status: STATUS.UNAVAILABLE, reason: "not_authenticated" };
      }
      if (!response.ok) {
        return { status: STATUS.UNAVAILABLE, reason: "http_" + response.status };
      }
      return response.json().then(function onBody(body) {
        if (!body || body.schemaVersion !== 1) {
          return { status: STATUS.UNAVAILABLE, reason: "unexpected_schema" };
        }
        var key = typeof body.tilesKey === "string" ? body.tilesKey.trim() : "";
        if (!key) return { status: STATUS.UNCONFIGURED, reason: "tiles_key_empty" };
        return { status: STATUS.READY, tilesKey: key };
      }).catch(function onParseError() {
        return { status: STATUS.UNAVAILABLE, reason: "unreadable_body" };
      });
    }).catch(function onNetworkError(error) {
      window.clearTimeout(timer);
      // Deliberately not the thrown message: an aborted or failed request can
      // carry the request URL, and this must stay safe to display.
      var aborted = error && error.name === "AbortError";
      return { status: STATUS.UNAVAILABLE, reason: aborted ? "timeout" : "unreachable" };
    });
  }

  function invalidateRequest() {
    requestGeneration += 1;
    var controller = inFlightController;
    inFlightController = null;
    inFlight = null;
    try {
      if (controller) controller.abort();
    } catch (error) {
      // Generation ownership still prevents a late response from being applied.
    }
  }

  function apply(result) {
    state.status = result.status;
    state.tilesKey = result.tilesKey || "";
    state.keyLength = state.tilesKey.length;
    state.reason = result.reason || "";
    state.fetchedAt = Date.now();
    return snapshot();
  }

  function snapshot() {
    return Object.freeze({
      status: state.status,
      configured: state.status === STATUS.READY,
      // Length only, never the value, so this is safe in any diagnostic.
      keyLength: state.keyLength,
      reason: state.reason,
      fetchedAt: state.fetchedAt,
    });
  }

  window.HomeWorldProviders = {
    STATUS: Object.freeze(STATUS),

    /* Resolve the configuration once and cache it for the session. Concurrent
     * callers share one request. Pass { force: true } to re-read after the
     * operator has configured a key without reloading the app. */
    load: function load(options) {
      var force = !!(options && options.force);
      if (!force && state.status === STATUS.READY) return Promise.resolve(snapshot());
      if (!force && inFlight) return inFlight;
      if (force && inFlight) invalidateRequest();
      if (typeof window.fetch !== "function") {
        return Promise.resolve(apply({ status: STATUS.UNAVAILABLE, reason: "fetch_unavailable" }));
      }
      var generation = ++requestGeneration;
      var controller = typeof AbortController === "function" ? new AbortController() : null;
      inFlightController = controller;
      state.status = STATUS.LOADING;
      var request = fetchConfig(controller).then(function settle(result) {
        if (generation !== requestGeneration) return snapshot();
        if (inFlight === request) inFlight = null;
        if (inFlightController === controller) inFlightController = null;
        return apply(result);
      }, function settleError() {
        if (generation !== requestGeneration) return snapshot();
        if (inFlight === request) inFlight = null;
        if (inFlightController === controller) inFlightController = null;
        return apply({ status: STATUS.UNAVAILABLE, reason: "unreachable" });
      });
      inFlight = request;
      return request;
    },

    /* The renderer is the only caller that should ever touch this, and only to
     * hand it straight to Cesium. Returns "" until load() has resolved READY. */
    getTilesKey: function getTilesKey() {
      return state.tilesKey;
    },

    isConfigured: function isConfigured() {
      return state.status === STATUS.READY && state.tilesKey.length > 0;
    },

    /* Safe for logs, toasts, and the debug snapshot. */
    describe: snapshot,

    /* Drop the key from memory. Called when the Apartment view tears down its
     * world surface, so the value does not outlive the thing that needed it. */
    forget: function forget() {
      invalidateRequest();
      state.tilesKey = "";
      state.keyLength = 0;
      state.status = STATUS.IDLE;
      state.reason = "";
      return snapshot();
    },
  };
}());
