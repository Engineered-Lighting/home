(function () {
  const BABEL_PLUGINS = [
    "transform-class-properties",
    "transform-object-rest-spread",
    "transform-flow-strip-types",
  ];

  const FEATURES = {
    people: {
      label: "people",
      files: [["home-people-helpers.js", false], ["home-people.jsx", true]],
      ready: () => !!window.HomePeopleOverlay,
    },
    intelligence: {
      label: "intelligence",
      files: [["home-intelligence.jsx", true]],
      ready: () => !!window.HomeIntelligenceOverlay,
    },
    videoLabeler: {
      label: "video labeler",
      files: [
        ["home-video-labeler-data.js", false],
        ["home-video-labeler-timeline.jsx", true],
        ["home-video-labeler.jsx", true],
      ],
      ready: () => !!window.HomeVideoLabelerOverlay,
    },
    world: {
      label: "world state",
      files: [["home-worldstate-helpers.js", false], ["home-worldstate.jsx", true]],
      ready: () => !!window.HomeWorldStateDrawer,
    },
    spatial: {
      label: "spatial",
      files: [["home-spatial-helpers.js", false], ["home-spatial.jsx", true]],
      ready: () => !!window.HomeSpatialDrawer,
    },
    look: {
      label: "look",
      files: [["home-look.jsx", true]],
      ready: () => !!window.HomeLookDrawer,
    },
    lights: {
      label: "lights",
      files: [["home-lights.jsx", true]],
      ready: () => !!window.HomeLightsDrawer,
    },
    apartment: {
      label: "apartment",
      files: [
        ["home-apartment-aiming.js", false],
        ["home-apartment-gimbal-telemetry.js", false],
        ["home-apartment-data.js", false],
        ["home-apartment-sim.js", false],
        ["home-apartment-cards.jsx", true],
        ["home-apartment-aim.jsx", true],
        ["home-apartment-calibrate.jsx", true],
        ["home-apartment-edit.jsx", true],
        ["home-apartment.jsx", true],
      ],
      ready: () => !!window.HomeApartmentView,
    },
  };

  const states = {};
  const promises = {};
  const loadedFiles = new Set();

  function now() {
    return window.performance && performance.now ? performance.now() : Date.now();
  }

  function assetSuffix() {
    if (typeof window.__HOME_BOOT_ASSET_SUFFIX === "string") return window.__HOME_BOOT_ASSET_SUFFIX;
    if (window.HG_WEB_MODE && window.__HOME_ASSET_VERSION) {
      return "?v=" + encodeURIComponent(window.__HOME_ASSET_VERSION);
    }
    return "?cb=" + Date.now();
  }

  function emit(feature, state, patch) {
    const prev = states[feature] || { feature, state: "idle" };
    states[feature] = Object.assign({}, prev, patch || {}, {
      feature,
      state,
      label: FEATURES[feature] ? FEATURES[feature].label : feature,
      updatedAt: Date.now(),
    });
    try {
      window.dispatchEvent(new CustomEvent("home-feature-loader", { detail: status(feature) }));
    } catch (_) {}
    return states[feature];
  }

  function fetchText(name) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("GET", name + assetSuffix(), true);
      xhr.withCredentials = true;
      if ("overrideMimeType" in xhr) xhr.overrideMimeType("text/plain");
      xhr.timeout = 20000;
      xhr.onreadystatechange = () => {
        if (xhr.readyState !== 4) return;
        if (xhr.status === 200 || (xhr.status === 0 && xhr.responseText.length > 0)) {
          resolve(xhr.responseText);
        } else {
          reject(new Error("HTTP status " + xhr.status + ", " + (xhr.responseText ? xhr.responseText.length : 0) + " bytes"));
        }
      };
      xhr.ontimeout = () => reject(new Error("timeout loading " + name));
      xhr.onerror = () => reject(new Error("network error loading " + name));
      xhr.onabort = () => reject(new Error("aborted loading " + name));
      xhr.send(null);
    });
  }

  async function fetchWithRetry(name, attempts) {
    try {
      return await fetchText(name);
    } catch (err) {
      if (attempts <= 1) throw err;
      await new Promise((resolve) => setTimeout(resolve, 250));
      return fetchWithRetry(name, attempts - 1);
    }
  }

  function execute(name, code) {
    const script = document.createElement("script");
    script.text = code + "\n//# sourceURL=" + name;
    document.head.appendChild(script);
  }

  async function fetchFile(feature, name, isBabel) {
    if (loadedFiles.has(name)) return null;
    const fetchStart = now();
    const text = await fetchWithRetry(name, 3);
    window.__homePerf?.recordBootFile?.(name, "feature-fetch", now() - fetchStart, { feature });
    return { name, isBabel, text };
  }

  function executeFile(feature, file) {
    if (!file || loadedFiles.has(file.name)) return;
    let code = file.text;
    if (file.isBabel) {
      const transformStart = now();
      code = Babel.transform(file.text, {
        filename: file.name,
        presets: ["react", "env"],
        plugins: BABEL_PLUGINS,
        sourceMaps: "inline",
        sourceFileName: file.name,
      }).code;
      window.__homePerf?.recordBootFile?.(file.name, "feature-transform", now() - transformStart, { feature });
    }
    const executeStart = now();
    execute(file.name, code);
    window.__homePerf?.recordBootFile?.(file.name, "feature-execute", now() - executeStart, { feature });
    loadedFiles.add(file.name);
  }

  function setStoredState(feature, state, patch) {
    const prev = states[feature] || { feature, state: "idle" };
    states[feature] = Object.assign({}, prev, patch || {}, {
      feature,
      state,
      label: FEATURES[feature] ? FEATURES[feature].label : feature,
      updatedAt: Date.now(),
    });
    return states[feature];
  }

  function syncLoadedState(feature, notify) {
    const def = FEATURES[feature];
    if (!def) return null;
    if (def.ready && def.ready()) {
      if (notify) emit(feature, "loaded", { error: null, loadedAt: Date.now() });
      else setStoredState(feature, "loaded", { error: null, loadedAt: states[feature]?.loadedAt || Date.now() });
    } else if (!states[feature]) {
      if (notify) emit(feature, "idle", { error: null });
      else setStoredState(feature, "idle", { error: null });
    }
    return states[feature];
  }

  async function load(feature, reason) {
    const def = FEATURES[feature];
    if (!def) throw new Error("unknown feature: " + feature);
    syncLoadedState(feature, false);
    if (states[feature] && states[feature].state === "loaded") return states[feature];
    if (promises[feature]) return promises[feature];

    const start = now();
    emit(feature, "loading", { reason: reason || "load", error: null });
    window.__homePerf?.recordFeature?.(feature, "load:start", null, { reason: reason || "load" });
    promises[feature] = (async () => {
      try {
        // Fetch the feature bundle concurrently so an optional-service outage
        // cannot repeatedly edge individual module requests out of the
        // browser's limited HTTP/1.1 connection pool. Execute in declaration
        // order to preserve the globals each JSX file depends on.
        const files = await Promise.all(
          def.files.map((item) => fetchFile(feature, item[0], item[1])),
        );
        for (const file of files) executeFile(feature, file);
        if (def.ready && !def.ready()) throw new Error(def.label + " loaded but did not register its window export");
        const next = emit(feature, "loaded", {
          error: null,
          loadedAt: Date.now(),
          durationMs: Math.round((now() - start) * 10) / 10,
        });
        window.__homePerf?.recordFeature?.(feature, "load:done", now() - start, { reason: reason || "load" });
        return next;
      } catch (err) {
        const message = (err && err.message) || String(err);
        const next = emit(feature, "error", {
          error: message,
          failedAt: Date.now(),
          durationMs: Math.round((now() - start) * 10) / 10,
        });
        window.__homePerf?.recordFeature?.(feature, "load:error", now() - start, { error: message, reason: reason || "load" });
        throw Object.assign(err instanceof Error ? err : new Error(message), { feature, state: next });
      } finally {
        delete promises[feature];
      }
    })();
    return promises[feature];
  }

  function prefetch(feature, reason) {
    const def = FEATURES[feature];
    if (!def) return Promise.reject(new Error("unknown feature: " + feature));
    syncLoadedState(feature, false);
    if (states[feature] && states[feature].state === "loaded") return Promise.resolve(states[feature]);
    return load(feature, reason || "prefetch").catch((err) => {
      console.warn("[feature-loader] prefetch failed:", feature, err?.message || err);
      return states[feature];
    });
  }

  function status(feature) {
    if (!FEATURES[feature]) return null;
    return syncLoadedState(feature, false);
  }

  function statusAll() {
    const out = {};
    Object.keys(FEATURES).forEach((feature) => { out[feature] = status(feature); });
    return out;
  }

  Object.keys(FEATURES).forEach((feature) => syncLoadedState(feature, false));

  window.HomeFeatureLoader = Object.freeze({
    load,
    prefetch,
    status,
    statusAll,
    definitions: FEATURES,
  });
})();
