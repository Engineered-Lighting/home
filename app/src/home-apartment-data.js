/* home-apartment-data.js — data layer for the /apartment view.
 *
 * Three concerns, no React:
 *   1. apartment_model load/save — HA endpoint (revision optimistic
 *      concurrency, 409 → rebase callback) with localStorage draft fallback
 *      so edit mode never loses work.
 *   2. HA entity state binding — one get_states snapshot + one state_changed
 *      subscription filtered to bound entity ids.
 *   3. spatial-tracker WS client — live person tracks (room-level Stage A /
 *      precise Stage B), auto-reconnect, replay-aware.
 */
(function () {
  const TRACKER_KEY = "apartment3d.trackerBase";
  const DRAFT_KEY = "apartment3d.modelDraft";
  const DEFAULT_TRACKER = "ws://192.168.0.100:8098";

  function defaultTrackerBase() {
    try {
      const resolved = window.HomeServices?.get?.("tracker");
      if (resolved) return resolved;
    } catch (e) { /* */ }
    return (window.HG_WEB_MODE && window.HG_DEFAULT_TRACKER_BASE) || DEFAULT_TRACKER;
  }

  function toWsBase(base) {
    const clean = String(base || "").replace(/\/+$/, "");
    if (!clean) return "";
    if (clean.startsWith("/")) {
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      return `${proto}//${window.location.host}${clean}`;
    }
    if (clean.startsWith("http://")) return "ws://" + clean.slice("http://".length);
    if (clean.startsWith("https://")) return "wss://" + clean.slice("https://".length);
    return clean;
  }

  const EMPTY_MODEL = {
    schema_version: 1, revision: 0, exists: false,
    meta: { frame: "z_up_metric_floor0" },
    zones: [], devices: [],
  };

  function trackerBase() {
    try {
      const resolved = window.HomeServices?.get?.("tracker");
      if (resolved) return resolved;
    } catch (e) { /* */ }
    try { return localStorage.getItem(TRACKER_KEY) || defaultTrackerBase(); }
    catch (e) { return defaultTrackerBase(); }
  }

  /* seed-model.json — generated device/zone inventory (55_seed_model.py).
   * Tries the data dir (asset protocol manual URL) then the bundled copy. */
  async function fetchSeed() {
    const urls = [];
    try {
      const resolved = window.HomeServices?.get?.("apartmentAssets");
      if (resolved) urls.push(`${String(resolved).replace(/\/+$/, "")}/seed-model.json`);
    } catch (e) { /* */ }
    if (window.HG_WEB_MODE && window.HG_DEFAULT_APARTMENT_ASSET_BASE) {
      urls.push(`${String(window.HG_DEFAULT_APARTMENT_ASSET_BASE).replace(/\/+$/, "")}/seed-model.json`);
    } else if (window.IS_TAURI || window.__TAURI__) {
      urls.push(`http://asset.localhost/${encodeURIComponent("C:/Claude/home/app/data/apartment/seed-model.json")}`);
    } else {
      urls.push("assets/apartment/seed-model.json");
    }
    urls.push("assets/apartment/seed-model.json");
    for (const u of urls) {
      try {
        const fetcher = ((window.IS_TAURI || window.__TAURI__) && window.tauriFetch) || fetch;
        const r = await fetcher(u, { cache: "no-store" });
        if (r.ok) return await r.json();
      } catch (e) { /* next */ }
    }
    return null;
  }

  /* ---------------- model ---------------- */

  async function getModel({ endpoint, token, sim } = {}) {
    if (sim) {
      try {
        if (typeof window.__SIM_APARTMENT_MODEL_FIXTURE === "function") {
          return window.__SIM_APARTMENT_MODEL_FIXTURE() || EMPTY_MODEL;
        }
      } catch (e) { /* */ }
      return { ...EMPTY_MODEL, exists: true };
    }
    if (endpoint && token) {
      try {
        const base = endpoint.replace(/\/+$/, "");
        const fetcher = window.tauriFetch || fetch;
        const r = await fetcher(`${base}/api/extended_openai_conversation/apartment_model`, {
          headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
        });
        if (r.ok) {
          const model = await r.json();
          if (model && typeof model.revision === "number") {
            // Empty model -> merge the generated seed (real light/camera
            // inventory at proposed positions). Saving in edit mode persists
            // it for real; until then it's an in-memory overlay.
            if (!(model.zones || []).length && !(model.devices || []).length) {
              const seed = await fetchSeed();
              if (seed) return { ...model, zones: seed.zones, devices: seed.devices, seeded: true };
            }
            // stash the last-good REMOTE doc — boot races (tauriFetch not
            // ready yet) must fall back to THIS, never to the seed, or the
            // user sees their edits "overwritten" until the next remote load
            try { localStorage.setItem("apartment3d.remoteCache", JSON.stringify(model)); } catch (e) { /* */ }
            return model;
          }
        }
      } catch (e) { /* fall through */ }
    }
    // offline fallback order: last-good remote copy, then draft, then seed.
    // The remote cache outranks everything — a boot race must show the
    // user's real saved layout, not the seed.
    try {
      const cached = JSON.parse(localStorage.getItem("apartment3d.remoteCache") || "null");
      if (cached) return { ...cached, remote_cached: true };
    } catch (e) { /* */ }
    try {
      const draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || "null");
      if (draft) return { ...draft, offline_draft: true };
    } catch (e) { /* */ }
    const seed = await fetchSeed();
    if (seed) return { ...EMPTY_MODEL, zones: seed.zones, devices: seed.devices, seeded: true };
    return EMPTY_MODEL;
  }

  /* Save the full model. Returns {ok, revision} | {conflict, stored} | {offline}. */
  async function saveModel(model, { endpoint, token, sim } = {}) {
    const doc = { ...model };
    delete doc.exists; delete doc.offline_draft; delete doc.conflict;
    doc.schema_version = 1;
    try { localStorage.setItem(DRAFT_KEY, JSON.stringify(doc)); } catch (e) { /* */ }
    if (sim) return { ok: false, sim: true };
    if (!endpoint || !token) return { ok: false, offline: true };
    try {
      const base = endpoint.replace(/\/+$/, "");
      const fetcher = window.tauriFetch || fetch;
      const r = await fetcher(`${base}/api/extended_openai_conversation/apartment_model`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify(doc),
      });
      if (r.status === 409) {
        const stored = await r.json();
        return { ok: false, conflict: true, stored };
      }
      if (!r.ok) return { ok: false, error: `HTTP ${r.status}` };
      const res = await r.json();
      return { ok: true, revision: res.revision, updated_at: res.updated_at };
    } catch (e) {
      return { ok: false, error: String(e && e.message || e) };
    }
  }

  /* ---------------- HA registries (edit-mode palette) ---------------- */

  async function getRegistry(client) {
    // entity + area + device registries via the existing HAClient WS
    const out = { entities: [], areas: [], devices: [], states: {} };
    if (!client) return out;
    try {
      const [ents, areas, devs, states] = await Promise.all([
        client.call({ type: "config/entity_registry/list" }),
        client.call({ type: "config/area_registry/list" }),
        client.call({ type: "config/device_registry/list" }),
        client.call({ type: "get_states" }),
      ]);
      out.entities = ents || [];
      out.areas = areas || [];
      out.devices = devs || [];
      for (const s of states || []) out.states[s.entity_id] = s;
    } catch (e) {
      console.warn("[apartment] registry fetch failed", e);
    }
    return out;
  }

  /* Palette: placeable HA entities grouped by area, minus already-bound ids. */
  function buildPalette(registry, model) {
    const bound = new Set((model.devices || []).map((d) => d.ha_entity_id).filter(Boolean));
    const areaName = {};
    for (const a of registry.areas) areaName[a.area_id] = a.name;
    const deviceArea = {};
    for (const d of registry.devices) deviceArea[d.id] = d.area_id;
    const groups = {};
    for (const e of registry.entities) {
      const domain = e.entity_id.split(".")[0];
      if (!["light", "media_player", "camera", "switch"].includes(domain)) continue;
      if (bound.has(e.entity_id)) continue;
      if (e.disabled_by || e.hidden_by) continue;
      const area = areaName[e.area_id || deviceArea[e.device_id]] || "unassigned";
      (groups[area] = groups[area] || []).push({
        entity_id: e.entity_id,
        name: e.name || e.original_name ||
          (registry.states[e.entity_id]?.attributes?.friendly_name) || e.entity_id,
        domain,
      });
    }
    return groups;
  }

  /* ---------------- entity state binding ---------------- */

  function bindStates(client, model, onState) {
    if (!client) return () => {};
    const ids = new Set((model.devices || []).map((d) => d.ha_entity_id).filter(Boolean));
    let unsub = null;
    (async () => {
      try {
        const states = await client.call({ type: "get_states" });
        for (const s of states || []) if (ids.has(s.entity_id)) onState(s.entity_id, s);
        unsub = await client.subscribeEvents("state_changed", (ev) => {
          const d = ev?.data;
          if (d && ids.has(d.entity_id) && d.new_state) onState(d.entity_id, d.new_state);
        });
      } catch (e) {
        console.warn("[apartment] state binding failed", e);
      }
    })();
    return () => { try { unsub && unsub(); } catch (e) { /* */ } };
  }

  async function callService(client, domain, service, data) {
    return client.call({
      type: "call_service", domain, service,
      target: data.entity_id ? { entity_id: data.entity_id } : undefined,
      service_data: Object.fromEntries(Object.entries(data).filter(([k]) => k !== "entity_id")),
    });
  }

  /* ---------------- tracker WS ---------------- */

  function openTracks({ sim, replay, onTracks, onStatus } = {}) {
    if (sim && typeof window.__SIM_APARTMENT_TRACKS === "function") {
      // sim fixture: scripted walk, 5 Hz
      const timer = setInterval(() => onTracks(window.__SIM_APARTMENT_TRACKS()), 200);
      onStatus && onStatus("sim");
      return () => clearInterval(timer);
    }
    if (sim) { onStatus && onStatus("sim-empty"); return () => {}; }

    let ws = null, closed = false, backoff = 1000;
    const url = `${toWsBase(trackerBase())}/ws/tracks${replay ? `?replay=${replay}` : ""}`;
    const connect = () => {
      if (closed) return;
      try { ws = new WebSocket(url); } catch (e) { retry(); return; }
      ws.onopen = () => { backoff = 1000; onStatus && onStatus("live"); };
      ws.onmessage = (m) => {
        try {
          const frame = JSON.parse(m.data);
          if (frame.type === "tracks") onTracks(frame);
        } catch (e) { /* */ }
      };
      ws.onclose = () => { onStatus && onStatus("offline"); retry(); };
      ws.onerror = () => { try { ws.close(); } catch (e) { /* */ } };
    };
    const retry = () => {
      if (closed) return;
      setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, 15000);
    };
    connect();
    return () => { closed = true; try { ws && ws.close(); } catch (e) { /* */ } };
  }

  window.HomeApartmentData = {
    getModel, saveModel, getRegistry, buildPalette,
    bindStates, callService, openTracks, EMPTY_MODEL,
  };
})();
