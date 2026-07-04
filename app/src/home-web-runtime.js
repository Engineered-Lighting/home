/* Home web runtime defaults.
 *
 * Loaded by both the Tauri shell and the browser-served app. It only enables
 * web mode for ordinary http(s) origins, leaving Tauri/custom-protocol builds
 * on their existing LAN/Tauri defaults.
 */
(function () {
  const loc = window.location || {};
  const host = String(loc.hostname || "");
  const isHttp = loc.protocol === "http:" || loc.protocol === "https:";
  const isTauriHost = host === "tauri.localhost" || host === "asset.localhost";
  const webMode = isHttp && !isTauriHost;
  if (!webMode) return;

  const PREFS_KEY = "hg-prefs";
  const EVENTS_KEY = "hg-events";
  const CONV_ID_KEY = "hg-conv-id";
  const ONBOARDING_KEY = "hg-onboarding";
  const MAX_EVENTS = 200;

  function isDefaultLan(value, port) {
    if (!value) return true;
    try {
      const u = new URL(String(value).replace(/^ws/, "http"));
      return (
        (u.hostname === "192.168.0.100" || u.hostname === "192.168.0.125" || u.hostname === "localhost" || u.hostname === "127.0.0.1") &&
        (!port || u.port === String(port))
      );
    } catch {
      return false;
    }
  }

  async function browserTauriFetch(url, init) {
    if (window.__SIM_ACTIVE === true) {
      return {
        ok: false,
        status: 0,
        statusText: "blocked-by-simulation-mode",
        json: async () => ({}),
        text: async () => "",
      };
    }
    return fetch(url, init);
  }

  function browserLoadPrefs(defaults = {}) {
    try {
      const raw = localStorage.getItem(PREFS_KEY);
      const prefs = raw ? { ...defaults, ...JSON.parse(raw) } : { ...defaults };
      if (window.HG_DEFAULT_HA_BASE && isDefaultLan(prefs.endpoint, 8123)) prefs.endpoint = window.HG_DEFAULT_HA_BASE;
      if (window.HG_DEFAULT_METRICS_BASE && isDefaultLan(prefs.metricsBase, 8092)) prefs.metricsBase = window.HG_DEFAULT_METRICS_BASE;
      if (window.HG_DEFAULT_S2S_BASE && isDefaultLan(prefs.s2sBase, 8094)) prefs.s2sBase = window.HG_DEFAULT_S2S_BASE;
      return prefs;
    } catch {
      return { ...defaults };
    }
  }

  function browserSavePrefs(prefs) {
    try {
      localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
    } catch {}
  }

  function browserLoadEvents() {
    try {
      const raw = localStorage.getItem(EVENTS_KEY);
      if (!raw) return [];
      const arr = JSON.parse(raw);
      return Array.isArray(arr) ? arr.slice(-MAX_EVENTS) : [];
    } catch {
      return [];
    }
  }

  function browserSaveEvents(events) {
    try {
      const trimmed = events.slice(-MAX_EVENTS).map((event) => {
        const { streaming, ...rest } = event;
        return rest;
      });
      localStorage.setItem(EVENTS_KEY, JSON.stringify(trimmed));
    } catch {}
  }

  function browserLoadConversationId() {
    try { return localStorage.getItem(CONV_ID_KEY) || null; } catch { return null; }
  }

  function browserSaveConversationId(id) {
    try {
      if (id) localStorage.setItem(CONV_ID_KEY, id);
      else localStorage.removeItem(CONV_ID_KEY);
    } catch {}
  }

  function browserLoadOnboarding(defaults = {}) {
    try {
      const raw = localStorage.getItem(ONBOARDING_KEY);
      if (!raw) return { ...defaults };
      return { ...defaults, ...JSON.parse(raw) };
    } catch {
      return { ...defaults };
    }
  }

  function browserSaveOnboarding(patch) {
    try {
      const cur = browserLoadOnboarding();
      localStorage.setItem(ONBOARDING_KEY, JSON.stringify({ ...cur, ...patch }));
    } catch {}
  }

  Object.assign(window, {
    HG_WEB_MODE: true,
    HG_DEFAULT_HA_BASE: "/proxy/ha",
    HG_DEFAULT_METRICS_BASE: "/proxy/metrics",
    HG_DEFAULT_VLLM_BASE: "/proxy/vllm",
    HG_DEFAULT_VISION_BASE: "/proxy/vision",
    HG_DEFAULT_INTELLIGENCE_BASE: "/proxy/intelligence",
    HG_DEFAULT_SUPERVISOR_BASE: "/proxy/supervisor",
    HG_DEFAULT_S2S_BASE: "/proxy/bridge",
    HG_DEFAULT_TRACKER_BASE: "/proxy/tracker",
    HG_DEFAULT_VIDEO_LABELER_BASE: "/proxy/video-labeler",
    HG_DEFAULT_FRIGATE_BASE: "/proxy/frigate",
    HG_DEFAULT_APARTMENT_ASSET_BASE: "/assets/apartment",
  });

  if (typeof window.tauriFetch !== "function") {
    Object.assign(window, {
      IS_TAURI: false,
      getTauriWindow: () => null,
      tauriFetch: browserTauriFetch,
      loadPrefs: browserLoadPrefs,
      savePrefs: browserSavePrefs,
      loadEvents: browserLoadEvents,
      saveEvents: browserSaveEvents,
      loadConversationId: browserLoadConversationId,
      saveConversationId: browserSaveConversationId,
      loadOnboarding: browserLoadOnboarding,
      saveOnboarding: browserSaveOnboarding,
      winClose: async () => {},
      winMinimize: async () => {},
      winMaximize: async () => {},
    });
  }

  if ("serviceWorker" in navigator && !window.HG_DISABLE_SERVICE_WORKER) {
    window.addEventListener("load", () => {
      const build = window.__HOME_ASSET_VERSION || window.__HOME_BUILD_COMMIT || "dev";
      const swUrl = `./home-service-worker.js?v=${encodeURIComponent(build)}`;
      let refreshing = false;
      navigator.serviceWorker.addEventListener("controllerchange", () => {
        if (refreshing) return;
        refreshing = true;
        try {
          const key = `home-sw-refresh:${build}`;
          if (sessionStorage.getItem(key)) return;
          sessionStorage.setItem(key, "1");
        } catch (e) { /* sessionStorage can fail in private mode */ }
        window.location.reload();
      });
      navigator.serviceWorker.register(swUrl, { scope: "./" })
        .then((registration) => registration.update().catch(() => {}))
        .catch((err) => console.warn("[home-web] service worker registration failed", err));
    }, { once: true });
  }
})();
