#!/usr/bin/env node
/* Pure local tests for app/src/home-apartment-data.js. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "home-apartment-data.js");
const source = fs.readFileSync(SRC, "utf8");

let passes = 0;
let fails = 0;
const failures = [];

function assert(name, cond, detail) {
  if (cond) {
    passes++;
    process.stdout.write("  PASS  " + name + "\n");
  } else {
    fails++;
    failures.push({ name, detail });
    process.stdout.write("  FAIL  " + name);
    if (detail !== undefined) {
      const dumped = typeof detail === "string"
        ? detail
        : JSON.stringify(detail, null, 2);
      process.stdout.write("\n        " + dumped.replace(/\n/g, "\n        "));
    }
    process.stdout.write("\n");
  }
}

function makeLocalStorage(initial) {
  const store = new Map(Object.entries(initial || {}).map(([k, v]) => [String(k), String(v)]));
  return {
    getItem(k) { return store.has(String(k)) ? store.get(String(k)) : null; },
    setItem(k, v) { store.set(String(k), String(v)); },
    removeItem(k) { store.delete(String(k)); },
    clear() { store.clear(); },
    _store: store,
  };
}

function okJson(data, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => data };
}

function loadModule(opts) {
  opts = opts || {};
  const fetchCalls = [];
  const tauriCalls = [];
  const warnings = [];
  const intervals = [];
  const clearedIntervals = [];
  const timeouts = [];
  const sockets = [];
  const localStorage = opts.localStorage || makeLocalStorage();
  const FakeWebSocket = opts.WebSocket || class {
    constructor(url) {
      this.url = url;
      this.closed = false;
      sockets.push(this);
    }
    close() {
      this.closed = true;
    }
  };
  const window = {
    __SIM_APARTMENT_MODEL_FIXTURE: opts.simModelFixture,
    __SIM_APARTMENT_TRACKS: opts.simTracks,
    HG_WEB_MODE: !!opts.webMode,
    HG_DEFAULT_TRACKER_BASE: opts.defaultTrackerBase,
    HG_DEFAULT_APARTMENT_ASSET_BASE: opts.defaultApartmentAssetBase,
    location: opts.location || { protocol: "http:", host: "localhost:5181" },
    tauriFetch: opts.tauriFetch || (opts.withTauriFetch ? async (url, init) => {
      tauriCalls.push({ url, init });
      return okJson({ schema_version: 1, revision: 9, zones: [{ id: "remote" }], devices: [] });
    } : undefined),
  };
  const sandbox = {
    window,
    localStorage,
    fetch: opts.fetch || (async (url, init) => {
      fetchCalls.push({ url, init });
      return okJson({ seed: true, zones: [{ id: "seed_zone" }], devices: [{ id: "seed_device" }] });
    }),
    WebSocket: FakeWebSocket,
    setInterval: (fn, ms) => {
      const id = { fn, ms, kind: "interval" };
      intervals.push(id);
      return id;
    },
    clearInterval: (id) => clearedIntervals.push(id),
    setTimeout: (fn, ms) => {
      const id = { fn, ms, kind: "timeout" };
      timeouts.push(id);
      return id;
    },
    clearTimeout: () => {},
    console: { warn: (...args) => warnings.push(args.join(" ")), log: () => {}, error: () => {} },
    encodeURIComponent,
    JSON,
    Math,
    Date,
    Object,
    Set,
    Promise,
  };
  vm.runInNewContext(source, sandbox, { filename: SRC });
  return {
    D: sandbox.window.HomeApartmentData,
    window,
    localStorage,
    fetchCalls,
    tauriCalls,
    warnings,
    intervals,
    clearedIntervals,
    timeouts,
    sockets,
  };
}

async function main() {
  process.stdout.write("\napartment_data_exports_test\n");
  const base = loadModule();
  const D = base.D;
  assert("HomeApartmentData exported", D && typeof D === "object");
  assert("model helpers exported", typeof D.getModel === "function" && typeof D.saveModel === "function");
  assert("HA helpers exported", typeof D.getRegistry === "function" && typeof D.bindStates === "function" && typeof D.callService === "function");
  assert("tracker helper exported", typeof D.openTracks === "function");
  assert("EMPTY_MODEL has stable shape", D.EMPTY_MODEL.schema_version === 1 && D.EMPTY_MODEL.revision === 0 && Array.isArray(D.EMPTY_MODEL.devices));

  process.stdout.write("\napartment_model_load_test\n");
  const fixture = { schema_version: 1, revision: 7, exists: true, zones: [{ id: "sim_zone" }], devices: [] };
  const simFixture = loadModule({ simModelFixture: () => fixture });
  assert("sim getModel returns fixture", (await simFixture.D.getModel({ sim: true })).zones[0].id === "sim_zone");
  const simDefault = await D.getModel({ sim: true });
  assert("sim getModel defaults to existing empty model", simDefault.exists === true && simDefault.revision === 0, simDefault);

  const remote = loadModule({ withTauriFetch: true });
  const remoteModel = await remote.D.getModel({ endpoint: "http://ha.local:8123///", token: "tok" });
  assert("remote getModel uses tauriFetch when available", remote.tauriCalls.length === 1, remote.tauriCalls);
  assert("remote getModel trims endpoint and uses auth/no-store", remote.tauriCalls[0].url === "http://ha.local:8123/api/extended_openai_conversation/apartment_model" && remote.tauriCalls[0].init.headers.Authorization === "Bearer tok" && remote.tauriCalls[0].init.cache === "no-store", remote.tauriCalls[0]);
  assert("remote getModel returns revisioned model", remoteModel.revision === 9 && remoteModel.zones[0].id === "remote", remoteModel);
  assert("remote getModel caches last good remote doc", JSON.parse(remote.localStorage.getItem("apartment3d.remoteCache")).revision === 9);

  const emptyRemote = loadModule({
    tauriFetch: async () => okJson({ schema_version: 1, revision: 3, zones: [], devices: [] }),
    fetch: async (url) => url.includes("asset.localhost")
      ? { ok: false, status: 404, json: async () => ({}) }
      : okJson({ zones: [{ id: "seed_zone" }], devices: [{ id: "seed_device" }] }),
  });
  const seededRemote = await emptyRemote.D.getModel({ endpoint: "http://ha.local", token: "tok" });
  assert("empty remote model overlays generated seed", seededRemote.seeded === true && seededRemote.zones[0].id === "seed_zone" && seededRemote.devices[0].id === "seed_device", seededRemote);

  const offline = loadModule({
    localStorage: makeLocalStorage({
      "apartment3d.remoteCache": JSON.stringify({ schema_version: 1, revision: 11, zones: [{ id: "cached" }], devices: [] }),
      "apartment3d.modelDraft": JSON.stringify({ schema_version: 1, revision: 5, zones: [{ id: "draft" }], devices: [] }),
    }),
  });
  const offlineModel = await offline.D.getModel();
  assert("offline fallback prefers last-good remote cache over draft", offlineModel.remote_cached === true && offlineModel.zones[0].id === "cached", offlineModel);

  const draftFallback = loadModule({
    localStorage: makeLocalStorage({
      "apartment3d.modelDraft": JSON.stringify({ schema_version: 1, revision: 5, zones: [{ id: "draft" }], devices: [] }),
    }),
    fetch: async () => ({ ok: false, status: 404, json: async () => ({}) }),
  });
  const draftModel = await draftFallback.D.getModel();
  assert("offline fallback uses draft before seed", draftModel.offline_draft === true && draftModel.zones[0].id === "draft", draftModel);

  const seedFallback = loadModule({
    fetch: async (url) => url.includes("asset.localhost")
      ? { ok: false, status: 404, json: async () => ({}) }
      : okJson({ zones: [{ id: "seed_only" }], devices: [] }),
  });
  const seedModel = await seedFallback.D.getModel();
  assert("offline fallback uses generated seed when no cache/draft", seedModel.seeded === true && seedModel.zones[0].id === "seed_only", seedModel);

  process.stdout.write("\napartment_model_save_test\n");
  const saveOffline = loadModule();
  const offlineSave = await saveOffline.D.saveModel({ revision: 1, exists: true, offline_draft: true, conflict: true, zones: [], devices: [] });
  const savedDraft = JSON.parse(saveOffline.localStorage.getItem("apartment3d.modelDraft"));
  assert("saveModel always stores sanitized draft", savedDraft.schema_version === 1 && !("exists" in savedDraft) && !("offline_draft" in savedDraft) && !("conflict" in savedDraft), savedDraft);
  assert("saveModel without endpoint/token reports offline", offlineSave.ok === false && offlineSave.offline === true, offlineSave);
  const simSave = await saveOffline.D.saveModel({ revision: 1, zones: [], devices: [] }, { sim: true });
  assert("saveModel sim mode does not report live success", simSave.ok === false && simSave.sim === true, simSave);

  const saveCalls = [];
  const saveRemote = loadModule({
    tauriFetch: async (url, init) => {
      saveCalls.push({ url, init });
      return okJson({ revision: 12, updated_at: "now" });
    },
  });
  const saveOk = await saveRemote.D.saveModel({ revision: 11, exists: true, zones: [{ id: "z" }], devices: [] }, { endpoint: "http://ha.local///", token: "tok" });
  assert("saveModel posts remote document", saveCalls[0].url === "http://ha.local/api/extended_openai_conversation/apartment_model" && saveCalls[0].init.method === "POST", saveCalls[0]);
  assert("saveModel remote request has auth and json body", saveCalls[0].init.headers.Authorization === "Bearer tok" && saveCalls[0].init.headers["Content-Type"] === "application/json" && JSON.parse(saveCalls[0].init.body).schema_version === 1, saveCalls[0]);
  assert("saveModel returns revision on success", saveOk.ok === true && saveOk.revision === 12 && saveOk.updated_at === "now", saveOk);

  const conflict = loadModule({ tauriFetch: async () => ({ ok: false, status: 409, json: async () => ({ revision: 99 }) }) });
  const conflictRes = await conflict.D.saveModel({ revision: 1, zones: [], devices: [] }, { endpoint: "http://ha", token: "tok" });
  assert("saveModel maps 409 to conflict", conflictRes.ok === false && conflictRes.conflict === true && conflictRes.stored.revision === 99, conflictRes);
  const httpErr = loadModule({ tauriFetch: async () => ({ ok: false, status: 500, json: async () => ({}) }) });
  const httpErrRes = await httpErr.D.saveModel({ revision: 1, zones: [], devices: [] }, { endpoint: "http://ha", token: "tok" });
  assert("saveModel maps HTTP errors", httpErrRes.ok === false && httpErrRes.error === "HTTP 500", httpErrRes);
  const thrown = loadModule({ tauriFetch: async () => { throw new Error("offline"); } });
  const thrownRes = await thrown.D.saveModel({ revision: 1, zones: [], devices: [] }, { endpoint: "http://ha", token: "tok" });
  assert("saveModel maps thrown errors", thrownRes.ok === false && /offline/.test(thrownRes.error), thrownRes);

  process.stdout.write("\napartment_registry_palette_test\n");
  const calls = [];
  const registryClient = {
    call: async (payload) => {
      calls.push(payload);
      if (payload.type === "config/entity_registry/list") return [
        { entity_id: "light.kitchen", area_id: "kitchen", name: "Kitchen" },
        { entity_id: "camera.living_room", device_id: "dev_cam" },
        { entity_id: "switch.hidden", hidden_by: "user" },
        { entity_id: "sensor.temp" },
        { entity_id: "media_player.bound", area_id: "living" },
      ];
      if (payload.type === "config/area_registry/list") return [
        { area_id: "kitchen", name: "Kitchen" },
        { area_id: "living", name: "Living Room" },
      ];
      if (payload.type === "config/device_registry/list") return [{ id: "dev_cam", area_id: "living" }];
      if (payload.type === "get_states") return [
        { entity_id: "camera.living_room", state: "idle", attributes: { friendly_name: "Living Cam" } },
        { entity_id: "media_player.bound", state: "off", attributes: { friendly_name: "Bound Player" } },
      ];
      return null;
    },
  };
  const registry = await D.getRegistry(registryClient);
  assert("getRegistry calls all HA registry/state endpoints", calls.map((c) => c.type).join(",") === "config/entity_registry/list,config/area_registry/list,config/device_registry/list,get_states", calls);
  assert("getRegistry maps states by entity id", registry.states["camera.living_room"].attributes.friendly_name === "Living Cam", registry.states);
  assert("getRegistry null client returns empty registry", (await D.getRegistry(null)).entities.length === 0);
  const palette = D.buildPalette(registry, { devices: [{ ha_entity_id: "media_player.bound" }] });
  assert("buildPalette groups by entity area", palette.Kitchen.some((e) => e.entity_id === "light.kitchen" && e.domain === "light"), palette);
  assert("buildPalette groups by device area and friendly name", palette["Living Room"].some((e) => e.entity_id === "camera.living_room" && e.name === "Living Cam"), palette);
  assert("buildPalette excludes bound, hidden, and unsupported entities", !JSON.stringify(palette).includes("media_player.bound") && !JSON.stringify(palette).includes("switch.hidden") && !JSON.stringify(palette).includes("sensor.temp"), palette);

  process.stdout.write("\napartment_state_binding_and_service_test\n");
  const stateEvents = [];
  let subCb = null;
  let unsubCalled = false;
  const stateClient = {
    call: async (payload) => {
      if (payload.type === "get_states") return [
        { entity_id: "light.bound", state: "on" },
        { entity_id: "light.unbound", state: "off" },
      ];
      return payload;
    },
    subscribeEvents: async (event, cb) => {
      subCb = cb;
      return () => { unsubCalled = true; };
    },
  };
  const cleanup = D.bindStates(stateClient, { devices: [{ ha_entity_id: "light.bound" }] }, (entity, state) => stateEvents.push({ entity, state }));
  await new Promise((resolve) => setImmediate(resolve));
  assert("bindStates emits initial bound states only", stateEvents.length === 1 && stateEvents[0].entity === "light.bound", stateEvents);
  subCb({ data: { entity_id: "light.bound", new_state: { state: "off" } } });
  subCb({ data: { entity_id: "light.unbound", new_state: { state: "on" } } });
  assert("bindStates filters state_changed events to bound ids", stateEvents.length === 2 && stateEvents[1].state.state === "off", stateEvents);
  cleanup();
  assert("bindStates cleanup calls subscription unsubscribe", unsubCalled === true);
  const svcCalls = [];
  const svcClient = { call: async (payload) => { svcCalls.push(payload); return { ok: true }; } };
  await D.callService(svcClient, "light", "turn_on", { entity_id: "light.bound", brightness_pct: 80 });
  await D.callService(svcClient, "media_player", "media_pause", { area_id: "living_room" });
  assert("callService shapes entity target and service_data", svcCalls[0].target.entity_id === "light.bound" && svcCalls[0].service_data.brightness_pct === 80 && !("entity_id" in svcCalls[0].service_data), svcCalls[0]);
  assert("callService leaves non-entity data in service_data", svcCalls[1].target === undefined && svcCalls[1].service_data.area_id === "living_room", svcCalls[1]);

  process.stdout.write("\napartment_tracks_test\n");
  const simTracks = loadModule({ simTracks: () => ({ type: "tracks", tracks: [{ id: "t1" }] }) });
  const trackFrames = [];
  const trackStatuses = [];
  const stopSim = simTracks.D.openTracks({ sim: true, onTracks: (frame) => trackFrames.push(frame), onStatus: (s) => trackStatuses.push(s) });
  assert("openTracks sim mode reports sim status", trackStatuses.join(",") === "sim", trackStatuses);
  assert("openTracks sim mode uses 5Hz interval", simTracks.intervals[0].ms === 200, simTracks.intervals);
  simTracks.intervals[0].fn();
  assert("openTracks sim interval emits fixture tracks", trackFrames[0].tracks[0].id === "t1", trackFrames);
  stopSim();
  assert("openTracks sim cleanup clears interval", simTracks.clearedIntervals[0] === simTracks.intervals[0]);
  const simEmpty = loadModule();
  const emptyStatuses = [];
  simEmpty.D.openTracks({ sim: true, onStatus: (s) => emptyStatuses.push(s) });
  assert("openTracks sim without fixture reports sim-empty", emptyStatuses.join(",") === "sim-empty", emptyStatuses);

  const wsLoad = loadModule({ localStorage: makeLocalStorage({ "apartment3d.trackerBase": "ws://tracker.local:8098" }) });
  const wsFrames = [];
  const wsStatuses = [];
  const closeWs = wsLoad.D.openTracks({ replay: "demo 1", onTracks: (frame) => wsFrames.push(frame), onStatus: (s) => wsStatuses.push(s) });
  assert("openTracks connects to stored tracker base with replay query", wsLoad.sockets[0].url === "ws://tracker.local:8098/ws/tracks?replay=demo 1", wsLoad.sockets[0].url);
  wsLoad.sockets[0].onopen();
  wsLoad.sockets[0].onmessage({ data: JSON.stringify({ type: "tracks", tracks: [{ id: "live" }] }) });
  wsLoad.sockets[0].onmessage({ data: JSON.stringify({ type: "heartbeat" }) });
  wsLoad.sockets[0].onmessage({ data: "{bad json" });
  assert("openTracks live status and tracks are emitted", wsStatuses[0] === "live" && wsFrames.length === 1 && wsFrames[0].tracks[0].id === "live", { wsStatuses, wsFrames });
  wsLoad.sockets[0].onclose();
  assert("openTracks schedules reconnect with initial backoff", wsStatuses.includes("offline") && wsLoad.timeouts[0].ms === 1000, { wsStatuses, timeouts: wsLoad.timeouts });
  closeWs();
  assert("openTracks cleanup closes websocket", wsLoad.sockets[0].closed === true);

  const wsDefault = loadModule();
  wsDefault.D.openTracks({ onTracks: () => {} });
  assert("openTracks uses default tracker base when unset", wsDefault.sockets[0].url === "ws://192.168.0.100:8098/ws/tracks", wsDefault.sockets[0].url);
  const wsWeb = loadModule({
    webMode: true,
    defaultTrackerBase: "/proxy/tracker",
    location: { protocol: "https:", host: "home.tailnet.ts.net" },
  });
  wsWeb.D.openTracks({ onTracks: () => {} });
  assert("openTracks maps web tracker default to same-origin wss", wsWeb.sockets[0].url === "wss://home.tailnet.ts.net/proxy/tracker/ws/tracks", wsWeb.sockets[0].url);

  if (fails) {
    console.log("\nFailures:");
    for (const f of failures) console.log("- " + f.name + (f.detail ? ": " + JSON.stringify(f.detail) : ""));
  }
  console.log(`\n${passes} pass . ${fails} fail`);
  process.exit(fails ? 1 : 0);
}

main().catch((err) => {
  console.error(err && err.stack || err);
  process.exit(1);
});
