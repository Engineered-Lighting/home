/* Provisioned connection contracts, independent of the visual site registry.
 * This factory grants no authority. BFF/Core validate every session and grant.
 * No default Victoria endpoint or new native IPC is introduced here.
 */
(function () {
  "use strict";
  var SITES = ["echo", "victoria"];
  var OPERATIONS = ["session", "observations", "memory", "proposeLighting", "confirmLighting"];

  function fail(message) { throw new Error(message); }
  function fixedHttps(value) {
    var url;
    try { url = new URL(value); } catch (_) { fail("Invalid provisioned endpoint"); }
    if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash) {
      fail("Provisioned endpoints require HTTPS without embedded credentials");
    }
    return url;
  }
  function freezeRecord(input) {
    if (!input || SITES.indexOf(input.siteId) < 0 || !input.revision || !input.trustRevision) {
      fail("Site and trust revisions are required");
    }
    var issuer = input.issuer;
    if (issuer !== "home-assistant:" + input.siteId) fail("Unregistered stable issuer identity");
    if (!input.transports || !Object.keys(input.transports).length) fail("No provisioned transport");
    var transports = {};
    Object.keys(input.transports).forEach(function (name) {
      if (["lan", "private"].indexOf(name) < 0) fail("Unregistered transport");
      transports[name] = fixedHttps(input.transports[name]).href;
    });
    if (!Array.isArray(input.capabilities) || input.capabilities.some(function (x) {
      return OPERATIONS.indexOf(x) < 0;
    })) fail("Unknown connection capability");
    return Object.freeze({ siteId: input.siteId, revision: input.revision,
      issuer: issuer, trustRevision: input.trustRevision,
      transports: Object.freeze(transports), capabilities: Object.freeze(input.capabilities.slice()) });
  }

  function create(provisionedRecords, typedClient, monotonicClock) {
    var mono = monotonicClock || function () { return performance.now() / 1000; };
    if (!Array.isArray(provisionedRecords) || !typedClient || typeof typedClient.call !== "function") {
      fail("Provisioned records and a typed client are required");
    }
    var records = new Map();
    var hosts = new Map();
    var issuers = new Set();
    provisionedRecords.forEach(function (input) {
      var record = freezeRecord(input);
      if (records.has(record.siteId) || issuers.has(record.issuer)) fail("Duplicate site or issuer");
      issuers.add(record.issuer);
      Object.keys(record.transports).forEach(function (name) {
        var host = new URL(record.transports[name]).hostname.toLowerCase();
        if (hosts.has(host) && hosts.get(host) !== record.siteId) fail("BFF homes require distinct hostnames");
        hosts.set(host, record.siteId);
      });
      records.set(record.siteId, record);
    });
    var current = null;
    var generation = 0;
    var controllers = new Set();
    var cleanups = new Set();
    var volatile = new Map();
    var contextHistory = new Map();
    var listeners = new Set();

    function invalidate() {
      generation += 1;
      controllers.forEach(function (controller) { controller.abort(); });
      controllers.clear();
      cleanups.forEach(function (cleanup) { try { cleanup(); } catch (_) {} });
      cleanups.clear();
      volatile.clear();
      contextHistory.clear();
      listeners.forEach(function (listener) { try { listener(snapshot()); } catch (_) {} });
    }
    function snapshot() {
      return Object.freeze({ siteId: current && current.siteId,
        transport: current && current.transport, generation: generation });
    }
    function select(siteId, transport) {
      var record = records.get(siteId);
      if (!record || !Object.prototype.hasOwnProperty.call(record.transports, transport)) {
        fail("Home connection is not provisioned");
      }
      if (current && current.siteId === siteId && current.transport === transport) return snapshot();
      current = { siteId: siteId, transport: transport };
      invalidate();
      return snapshot();
    }
    async function request(operation, payload) {
      if (!current) fail("Select a provisioned home connection");
      var record = records.get(current.siteId);
      if (OPERATIONS.indexOf(operation) < 0 || record.capabilities.indexOf(operation) < 0) {
        fail("Operation is not provisioned");
      }
      var selected = snapshot();
      var controller = new AbortController();
      controllers.add(controller);
      try {
        var result = await typedClient.call(Object.freeze({
          siteId: selected.siteId, issuer: record.issuer, endpoint: record.transports[selected.transport],
          revision: record.revision, trustRevision: record.trustRevision,
          operation: operation, payload: payload, signal: controller.signal,
        }));
        if (selected.generation !== generation || controller.signal.aborted) fail("Stale home response");
        // The transport must return a server-authenticated site envelope, never a client header echo.
        if (!result || result.siteId !== selected.siteId) fail("Response home mismatch");
        return result;
      } finally { controllers.delete(controller); }
    }
    return Object.freeze({
      select: select, snapshot: snapshot, request: request,
      clear: function () { current = null; invalidate(); },
      revoke: function (siteId) { if (current && current.siteId === siteId) invalidate(); },
      getProvisioned: function (siteId) { return records.get(siteId) || null; },
      subscribe: function (listener) { listeners.add(listener); return function () { listeners.delete(listener); }; },
      trackSubscription: function (generationAtStart, cleanup) {
        if (typeof cleanup !== "function") fail("Subscription disposer required");
        if (generationAtStart !== generation) { cleanup(); return false; }
        cleanups.add(cleanup); return true;
      },
      putContext: function (generationAtStart, key, value, nowSeconds) {
        if (generationAtStart !== generation || !current || !value || value.site_id !== current.siteId
            || typeof key !== "string" || !key || key.length > 128
            || key !== value.camera_id || !value.adapter_session || !Number.isInteger(value.sequence)
            || value.sequence < 0 || !Number.isInteger(value.occupancy_generation) || value.occupancy_generation < 0
            || !Number.isFinite(nowSeconds) || !Number.isFinite(value.observed_at)
            || value.observed_at > nowSeconds || value.valid_until !== value.observed_at + 180
            || value.valid_until <= nowSeconds || (!contextHistory.has(key) && contextHistory.size >= 64)) return false;
        var previous = contextHistory.get(key);
        if (previous && (previous.retired.has(value.adapter_session)
            || (value.adapter_session === previous.session && (
              value.occupancy_generation < previous.occupancyGeneration
              || value.sequence <= previous.sequence || value.observed_at < previous.observedAt)))) return false;
        var retired = previous ? previous.retired : new Set();
        if (previous && previous.session !== value.adapter_session) retired.add(previous.session);
        contextHistory.set(key, { retired: retired, session: value.adapter_session,
          occupancyGeneration: value.occupancy_generation, sequence: value.sequence, observedAt: value.observed_at });
        volatile.set(key, { value: Object.freeze(Object.assign({}, value)),
          expiresMono: mono() + Math.min(180, value.valid_until - nowSeconds) }); return true;
      },
      currentContext: function (key, nowSeconds, availability) {
        var entry = volatile.get(key);
        var value = entry && entry.value;
        if (value && availability && availability.site_id === value.site_id
            && availability.adapter_session === value.adapter_session && availability.online === false) {
          volatile.delete(key); return null;
        }
        if (entry && (mono() >= entry.expiresMono || nowSeconds >= value.valid_until || nowSeconds < value.observed_at)) {
          volatile.delete(key); return null;
        }
        if (!value || !current || !Number.isFinite(nowSeconds) || !availability || availability.online !== true
            || availability.site_id !== current.siteId || availability.retained === true
            || !Number.isFinite(availability.checked_at) || nowSeconds < availability.checked_at
            || nowSeconds - availability.checked_at > 10
            || availability.adapter_session !== value.adapter_session
            || availability.occupancy_generation !== value.occupancy_generation
            || !Number.isFinite(value.observed_at) || !Number.isFinite(value.valid_until)
            || value.observed_at > nowSeconds || value.valid_until !== value.observed_at + 180
            || nowSeconds >= value.valid_until) return null;
        return value;
      },
    });
  }
  window.HomeConnectionRegistry = Object.freeze({ create: create });
}());
