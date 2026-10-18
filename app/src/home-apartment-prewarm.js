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
  const WORLD_VIEW_KEY = "apartment3d.worldView";
  const VALID_MODES = new Set(["off", "metadata", "cloud", "full"]);
  const SMALL_ASSETS = [
    { label: "manifest", names: ["manifest.json"] },
    { label: "frame", names: ["frame.json"] },
    { label: "floor", names: ["floor.json"] },
    { label: "seed model", names: ["seed-model.json"] },
  ];
  const CLOUD_ASSETS = [{ label: "point cloud", names: ["points.ply"] }];

  function fallbackPerformanceMode(selection) {
    if (selection?.quality === "high") return "maxFidelity";
    if (selection?.quality === "low") return "performanceBattery";
    return "adaptive";
  }

  function fallbackAssetPlanForTarget(target, performanceMode = "adaptive") {
    const mobile = target === "mobile60" || target === "mobile30";
    const maximum = mobile && performanceMode === "maxFidelity";
    return {
      splatNames: maximum
        ? ["apartment.ply", "apartment.spz", "apartment.mobile.ply"]
        : mobile
        ? ["apartment.mobile.ply", "apartment.ply", "apartment.spz"]
        : ["apartment.ply", "apartment.spz"],
      meshNames: maximum
        ? ["mesh.glb", "mesh.mobile.glb", "collision.glb"]
        : mobile
        ? ["mesh.mobile.glb", "mesh.glb", "collision.glb"]
        : ["mesh.glb", "collision.glb"],
    };
  }

  function validAssetPlan(value) {
    return value
      && Array.isArray(value.splatNames) && value.splatNames.length > 0
      && Array.isArray(value.meshNames) && value.meshNames.length > 0;
  }

  let controller = null;
  let run = null;
  let generation = 0;
  let activeScope = null;
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
    siteId: null,
    contentRevision: null,
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

  function savedWorldViewSelection() {
    try {
      const raw = localStorage.getItem(WORLD_VIEW_KEY);
      const parsed = raw ? JSON.parse(raw) : null;
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (e) {
      return {};
    }
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

  function contentScope(options = {}) {
    const registry = window.HomeSpatialViewport;
    const siteId = options.siteId ?? registry?.getSelectedSiteId?.() ?? "echo";
    const site = registry?.getSite?.(siteId);
    const contentRevision = Object.prototype.hasOwnProperty.call(options, "contentRevision")
      ? options.contentRevision
      : site ? site.contentRevision ?? null : window.HomeContentPacks?.get?.(siteId)?.manifest?.revision ?? null;
    return { siteId, contentRevision };
  }

  function scopeIsCurrent(scope) {
    const selected = window.HomeSpatialViewport?.getSelectedSiteId?.() ?? scope.siteId;
    const current = contentScope({ siteId: scope.siteId });
    return selected === scope.siteId && current.contentRevision === scope.contentRevision;
  }

  async function assetCandidates(name, scope, engineModule) {
    if (engineModule?.apartmentAssetCandidates) {
      return await engineModule.apartmentAssetCandidates(name, scope);
    }
    // An older cached engine may lack the bridge. Keep the same complete-pack
    // fence, and allow the historical development paths only for unpinned Echo.
    const packs = window.HomeContentPacks;
    const pack = packs?.resolve?.(scope.siteId, name, scope.contentRevision);
    if (pack) return [pack];
    if (scope.siteId !== "echo" || scope.contentRevision != null || packs?.get?.(scope.siteId)
        || window.__TAURI_INTERNALS__ || window.__TAURI__) return [];
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

  async function warmUrl(label, url, signal, scope, engineModule) {
    const started = performance.now();
    // The renderer consumes these same immutable bytes. Merely draining a
    // large response into the HTTP cache can discard it at browser entry-size
    // limits, causing a simultaneous or subsequent Photo load to download it
    // again. Metadata and older engine bridges retain the existing read path.
    if (/\.(?:ply|spz|glb)(?:$|[?#])/i.test(url) && engineModule?.warmApartmentAssetBytes) {
      const result = await engineModule.warmApartmentAssetBytes({ url, ...scope, signal });
      return { label, url, bytes: result.bytes, cacheHit: result.cacheHit,
        ms: Math.round(performance.now() - started) };
    }
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

  async function warmOneOf(group, signal, scope, engineModule, isCurrent) {
    const errors = [];
    for (const name of group.names) {
      if (!isCurrent()) return null;
      const urls = await assetCandidates(name, scope, engineModule);
      if (!isCurrent()) return null;
      for (const url of urls) {
        if (!isCurrent()) return null;
        state.current = `${group.label}: ${name}`;
        try {
          const result = await warmUrl(group.label, url, signal, scope, engineModule);
          if (!isCurrent()) return null;
          state.warmed.push(result);
          return result;
        } catch (err) {
          if (!isCurrent()) return null;
          errors.push(`${name} ${url}: ${err?.message || err}`);
        }
      }
    }
    const failed = { label: group.label, error: errors[errors.length - 1] || "no candidate warmed" };
    if (isCurrent()) state.failed.push(failed);
    return failed;
  }

  function groupsForMode(mode, assetPlan) {
    const groups = [...SMALL_ASSETS];
    if (mode === "cloud" || mode === "full") groups.push(...CLOUD_ASSETS);
    if (mode === "full") {
      groups.push(
        { label: "photo scan", names: assetPlan.splatNames },
        { label: "mesh", names: assetPlan.meshNames },
      );
    }
    return groups;
  }

  async function warmModules(isCurrent) {
    try {
      const mod = await window.Home3D?.ready;
      if (!isCurrent()) return mod ?? null;
      if (mod?.preloadModules) {
        const modules = await mod.preloadModules({ includeRenderers: true });
        if (isCurrent()) state.modules = modules;
      } else {
        if (isCurrent()) state.modules = { ok: false, loaded: 0, failed: ["Home3D preload hook unavailable"] };
      }
      return mod ?? null;
    } catch (err) {
      if (isCurrent()) state.modules = { ok: false, loaded: 0, failed: [String(err?.message || err)] };
      return null;
    }
  }

  async function start(options = {}) {
    if (window.HomeLiveSceneShadow?.active) return { state: 'disabled', reason: 'live_shadow' };
    const requestedScope = contentScope(options);
    if (run && state.state === "running" && activeScope?.siteId === requestedScope.siteId
        && activeScope?.contentRevision === requestedScope.contentRevision) return status();
    const mode = effectiveMode(options.mode, !!options.force);
    if (mode === "off") {
      abort();
      state.skipped.push({ at: new Date().toISOString(), reason: "prewarm disabled" });
      return status();
    }
    controller?.abort("apartment prewarm superseded");
    const ownGeneration = ++generation;
    await window.HomeContentPacks?.ready;
    if (ownGeneration !== generation) return status();
    const scope = contentScope(options);
    if (!scopeIsCurrent(scope)) return status();
    const ownController = new AbortController();
    controller = ownController;
    activeScope = scope;
    const isCurrent = () => ownGeneration === generation && !ownController.signal.aborted && scopeIsCurrent(scope);
    state.state = "running";
    state.mode = mode;
    state.reason = options.reason || "manual";
    state.startedAt = new Date().toISOString();
    state.finishedAt = null;
    state.current = "modules";
    state.warmed = [];
    state.failed = [];
    state.modules = null;
    state.siteId = scope.siteId;
    state.contentRevision = scope.contentRevision;
    run = (async () => {
      try {
        const engineModule = await warmModules(isCurrent);
        if (!isCurrent()) return;
        const viewSelection = savedWorldViewSelection();
        let assetPlan = null;
        try {
          const selectedPlan = await engineModule?.selectApartmentAssetPlan?.({ viewSelection });
          if (!isCurrent()) return;
          if (validAssetPlan(selectedPlan)) assetPlan = selectedPlan;
        } catch (e) { /* compatibility path below */ }
        if (!assetPlan) {
          let performanceTarget = "desktop";
          try {
            performanceTarget = await engineModule?.selectApartmentPerformanceTarget?.()
              ?? "desktop";
            if (!isCurrent()) return;
          } catch (e) { /* keep the conservative full-quality fallback */ }
          const performanceMode = fallbackPerformanceMode(viewSelection);
          assetPlan = fallbackAssetPlanForTarget(performanceTarget, performanceMode);
          try {
            const sharedPlan = await engineModule?.apartmentAssetPlanForPerformanceTarget?.({
              targetTier: performanceTarget,
              mode: performanceMode,
            });
            if (!isCurrent()) return;
            if (validAssetPlan(sharedPlan)) assetPlan = sharedPlan;
          } catch (e) { /* compatibility fallback for an older cached engine module */ }
        }
        for (const group of groupsForMode(mode, assetPlan)) {
          if (!isCurrent()) return;
          await warmOneOf(group, ownController.signal, scope, engineModule, isCurrent);
        }
        if (!isCurrent()) return;
        state.state = state.failed.length ? "degraded" : "ready";
      } catch (err) {
        if (ownGeneration !== generation) return;
        if (ownController.signal.aborted || !scopeIsCurrent(scope)) state.state = "aborted";
        else {
          state.state = "degraded";
          state.failed.push({ label: state.current || "prewarm", error: String(err?.message || err) });
        }
      } finally {
        if (ownGeneration !== generation) return;
        if (!isCurrent() && state.state === "running") state.state = "aborted";
        state.current = null;
        state.finishedAt = new Date().toISOString();
        run = null;
        window.dispatchEvent(new CustomEvent("home-apartment-prewarm", { detail: status() }));
      }
    })();
    return status();
  }

  function abort() {
    generation += 1;
    controller?.abort("apartment prewarm aborted");
    if (state.state === "running") {
      state.state = "aborted";
      state.current = null;
      state.finishedAt = new Date().toISOString();
      window.dispatchEvent(new CustomEvent("home-apartment-prewarm", { detail: status() }));
    }
    run = null;
    controller = null;
    activeScope = null;
    return status();
  }

  function status() {
    return clone({
      ...state,
      saveData: !!connection()?.saveData,
      effectiveType: connection()?.effectiveType || null,
    });
  }

  function idle(cb, timeout = 2500) {
    if (window.requestIdleCallback) return window.requestIdleCallback(cb, { timeout });
    return setTimeout(() => cb({ timeRemaining: () => 0, didTimeout: true }), Math.min(timeout, 900));
  }

  function scheduleAutoStart() {
    if (window.HomeLiveSceneShadow?.active) return;
    if (window.__HOME_BACKGROUND_WARMUP_CONTROLS_APARTMENT) return;
    const waitForBoot = () => {
      if (window.__bootState?.failed) return;
      if (!window.__bootState?.done) {
        setTimeout(waitForBoot, 500);
        return;
      }
      idle(() => start({ mode: "full", reason: "post-boot" }), window.HG_WEB_MODE ? 1800 : 5000);
    };
    setTimeout(waitForBoot, window.HG_WEB_MODE ? 350 : 2500);
  }

  window.HomeApartmentPrewarm = {
    start,
    abort,
    status,
    setMode(mode) {
      if (!VALID_MODES.has(mode)) throw new Error(`invalid apartment prewarm mode: ${mode}`);
      try { localStorage.setItem(MODE_KEY, mode); } catch (_) { /* blocked preference storage is optional */ }
      if (mode === "off") abort();
      return status();
    },
  };

  const cancelObsolete = () => { if (activeScope && !scopeIsCurrent(activeScope)) abort(); };
  window.HomeSpatialViewport?.subscribe?.(cancelObsolete);
  window.HomeContentPacks?.subscribe?.(cancelObsolete);

  scheduleAutoStart();
})();
