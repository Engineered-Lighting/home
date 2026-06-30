/* home-apartment-prewarm.js - idle cache warmer for the /apartment view.
 *
 * The Apartment engine is intentionally created only when the user opens the
 * view, but the browser can still fetch modules and immutable-ish scan assets
 * after the main app is usable. This keeps first paint light while making the
 * first /apartment open feel much closer to a warm reopen.
 */
(function () {
  const DATA_DIR = "C:/Claude/home/app/data/apartment";
  const MODE_KEY = "apartment3d.prewarm";
  const VALID_MODES = new Set(["off", "metadata", "cloud", "full"]);
  const SMALL_ASSETS = [
    { label: "manifest", names: ["manifest.json"] },
    { label: "frame", names: ["frame.json"] },
    { label: "floor", names: ["floor.json"] },
    { label: "seed model", names: ["seed-model.json"] },
  ];
  const CLOUD_ASSETS = [{ label: "point cloud", names: ["points.ply"] }];
  const FULL_ASSETS = [
    { label: "photo scan", names: ["apartment.spz", "apartment.ply"] },
    { label: "mesh", names: ["mesh.glb", "collision.glb"] },
  ];

  let controller = null;
  let run = null;
  const state = {
    state: "idle",
    mode: null,
    reason: null,
    startedAt: null,
    finishedAt: null,
    current: null,
    warmed: [],
    failed: [],
    skipped: [],
    modules: null,
  };

  function clone(value) {
    try { return JSON.parse(JSON.stringify(value)); }
    catch (e) { return value; }
  }

  function normalizeBase(base) {
    return String(base || "").replace(/\/+$/, "");
  }

  function connection() {
    return navigator.connection || navigator.mozConnection || navigator.webkitConnection || null;
  }

  function requestedMode(mode) {
    if (mode && VALID_MODES.has(mode)) return mode;
    try {
      const saved = localStorage.getItem(MODE_KEY);
      if (VALID_MODES.has(saved)) return saved;
    } catch (e) { /* */ }
    return window.HG_WEB_MODE ? "full" : "metadata";
  }

  function effectiveMode(mode, force) {
    const next = requestedMode(mode);
    if (next === "off" || force) return next;
    const net = connection();
    if (net?.saveData) return next === "full" ? "cloud" : next;
    if (/^slow-2g|2g$/i.test(net?.effectiveType || "")) return next === "full" ? "metadata" : next;
    return next;
  }

  function baseCandidates() {
    const bases = [];
    try {
      const resolved = window.HomeServices?.get?.("apartmentAssets");
      if (resolved) bases.push(normalizeBase(resolved));
    } catch (e) { /* */ }
    try {
      if (window.HG_WEB_MODE && window.HG_DEFAULT_APARTMENT_ASSET_BASE) {
        bases.push(normalizeBase(window.HG_DEFAULT_APARTMENT_ASSET_BASE));
      }
    } catch (e) { /* */ }
    try {
      const saved = !window.HomeServices && localStorage.getItem("apartment3d.assetsBase");
      if (saved) bases.push(normalizeBase(saved));
    } catch (e) { /* */ }
    if (!window.HG_WEB_MODE && !(window.IS_TAURI || window.__TAURI__)) bases.push("assets/apartment");
    return [...new Set(bases.filter(Boolean))];
  }

  function assetCandidates(name) {
    const urls = baseCandidates().map((base) => `${base}/${name}`);
    if (window.IS_TAURI || window.__TAURI__) {
      urls.push(`http://asset.localhost/${encodeURIComponent(`${DATA_DIR}/${name}`)}`);
    }
    urls.push(`assets/apartment/${name}`);
    return [...new Set(urls)];
  }

  function fetchOptions(url, signal) {
    const absolute = /^[a-z][a-z0-9+.-]*:\/\//i.test(url);
    const sameOrigin = url.startsWith("/") || !absolute || url.startsWith(window.location.origin);
    return {
      cache: "force-cache",
      credentials: sameOrigin ? "same-origin" : "omit",
      signal,
    };
  }

  async function drain(response, signal) {
    const headerBytes = Number(response.headers.get("Content-Length") || 0);
    let bytes = 0;
    if (response.body?.getReader) {
      const reader = response.body.getReader();
      for (;;) {
        if (signal?.aborted) throw signal.reason || new DOMException("aborted", "AbortError");
        const { done, value } = await reader.read();
        if (done) break;
        bytes += value?.byteLength || 0;
      }
      return bytes || headerBytes;
    }
    const buf = await response.arrayBuffer();
    return buf.byteLength || headerBytes;
  }

  async function warmUrl(label, url, signal) {
    const started = performance.now();
    const response = await fetch(url, fetchOptions(url, signal));
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const bytes = await drain(response, signal);
    return {
      label,
      url,
      bytes,
      ms: Math.round(performance.now() - started),
    };
  }

  async function warmOneOf(group, signal) {
    const errors = [];
    for (const name of group.names) {
      for (const url of assetCandidates(name)) {
        state.current = `${group.label}: ${name}`;
        try {
          const result = await warmUrl(group.label, url, signal);
          state.warmed.push(result);
          return result;
        } catch (err) {
          errors.push(`${name} ${url}: ${err?.message || err}`);
        }
      }
    }
    const failed = { label: group.label, error: errors[errors.length - 1] || "no candidate warmed" };
    state.failed.push(failed);
    return failed;
  }

  function groupsForMode(mode) {
    const groups = [...SMALL_ASSETS];
    if (mode === "cloud" || mode === "full") groups.push(...CLOUD_ASSETS);
    if (mode === "full") groups.push(...FULL_ASSETS);
    return groups;
  }

  async function warmModules() {
    try {
      const mod = await window.Home3D?.ready;
      if (mod?.preloadModules) {
        state.modules = await mod.preloadModules({ includeRenderers: true });
      } else {
        state.modules = { ok: false, loaded: 0, failed: ["Home3D preload hook unavailable"] };
      }
    } catch (err) {
      state.modules = { ok: false, loaded: 0, failed: [String(err?.message || err)] };
    }
  }

  async function start(options = {}) {
    if (run && state.state === "running") return status();
    const mode = effectiveMode(options.mode, !!options.force);
    if (mode === "off") {
      state.skipped.push({ at: new Date().toISOString(), reason: "prewarm disabled" });
      return status();
    }
    controller = new AbortController();
    state.state = "running";
    state.mode = mode;
    state.reason = options.reason || "manual";
    state.startedAt = new Date().toISOString();
    state.finishedAt = null;
    state.current = "modules";
    state.warmed = [];
    state.failed = [];
    state.modules = null;
    run = (async () => {
      try {
        await warmModules();
        for (const group of groupsForMode(mode)) {
          await warmOneOf(group, controller.signal);
        }
        state.state = state.failed.length ? "degraded" : "ready";
      } catch (err) {
        if (controller.signal.aborted) state.state = "aborted";
        else {
          state.state = "degraded";
          state.failed.push({ label: state.current || "prewarm", error: String(err?.message || err) });
        }
      } finally {
        state.current = null;
        state.finishedAt = new Date().toISOString();
        run = null;
        window.dispatchEvent(new CustomEvent("home-apartment-prewarm", { detail: status() }));
      }
    })();
    return status();
  }

  function abort() {
    if (controller && state.state === "running") controller.abort("apartment prewarm aborted");
    return status();
  }

  function status() {
    return clone({
      ...state,
      saveData: !!connection()?.saveData,
      effectiveType: connection()?.effectiveType || null,
    });
  }

  function scheduleAutoStart() {
    const waitForBoot = () => {
      if (window.__bootState?.failed) return;
      if (!window.__bootState?.done) {
        setTimeout(waitForBoot, 1000);
        return;
      }
      const idle = window.requestIdleCallback || ((cb) => setTimeout(() => cb({ timeRemaining: () => 0 }), 2500));
      idle(() => start({ reason: "idle" }), { timeout: 9000 });
    };
    setTimeout(waitForBoot, window.HG_WEB_MODE ? 2200 : 6000);
  }

  window.HomeApartmentPrewarm = {
    start,
    abort,
    status,
    setMode(mode) {
      if (!VALID_MODES.has(mode)) throw new Error(`invalid apartment prewarm mode: ${mode}`);
      localStorage.setItem(MODE_KEY, mode);
      return status();
    },
  };

  scheduleAutoStart();
})();
