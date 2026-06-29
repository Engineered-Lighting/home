/* Home service resolver.
 *
 * One source of truth for the Home app's service endpoints. Browser mode stays
 * same-origin through the web gateway. Tauri desktop mode can switch between
 * home LAN, direct Tailscale/MagicDNS, and custom service URLs.
 */
(function () {
  "use strict";

  const STORAGE_KEY = "hg-services-v1";
  const ERROR_LIMIT = 30;
  const TAILNET = "taild52a15.ts.net";

  const SERVICE_KEYS = [
    "ha",
    "frigate",
    "metrics",
    "vllm",
    "vision",
    "intelligence",
    "supervisor",
    "s2s",
    "tracker",
    "videoLabeler",
    "apartmentAssets",
  ];

  const META = {
    ha: {
      label: "Home Assistant",
      runtimeKey: "HG_DEFAULT_HA_BASE",
      probePath: "/api/",
      okStatuses: [200, 401],
    },
    frigate: {
      label: "Frigate",
      runtimeKey: "HG_DEFAULT_FRIGATE_BASE",
      probePath: "/api/stats",
      okStatuses: [200, 401],
    },
    metrics: {
      label: "Metrics",
      runtimeKey: "HG_DEFAULT_METRICS_BASE",
      probePath: "/healthz",
      okStatuses: [200],
    },
    vllm: {
      label: "vLLM",
      runtimeKey: "HG_DEFAULT_VLLM_BASE",
      probePath: "/v1/models",
      okStatuses: [200, 401],
    },
    vision: {
      label: "Vision",
      runtimeKey: "HG_DEFAULT_VISION_BASE",
      probePath: "/healthz",
      okStatuses: [200],
    },
    intelligence: {
      label: "Intelligence",
      runtimeKey: "HG_DEFAULT_INTELLIGENCE_BASE",
      probePath: "/healthz",
      okStatuses: [200],
    },
    supervisor: {
      label: "Supervisor",
      runtimeKey: "HG_DEFAULT_SUPERVISOR_BASE",
      probePath: "/healthz",
      okStatuses: [200],
    },
    s2s: {
      label: "S2S bridge",
      runtimeKey: "HG_DEFAULT_S2S_BASE",
      probePath: "/healthz",
      okStatuses: [200],
    },
    tracker: {
      label: "Tracker",
      runtimeKey: "HG_DEFAULT_TRACKER_BASE",
      probePath: "/healthz",
      okStatuses: [200],
    },
    videoLabeler: {
      label: "Video labeler",
      runtimeKey: "HG_DEFAULT_VIDEO_LABELER_BASE",
      probePath: "/healthz",
      okStatuses: [200],
    },
    apartmentAssets: {
      label: "Apartment assets",
      runtimeKey: "HG_DEFAULT_APARTMENT_ASSET_BASE",
      probePath: "/healthz",
      okStatuses: [200],
      fallbackProbePath: "/manifest.json",
    },
  };

  const WEB_DEFAULTS = {
    ha: "/proxy/ha",
    frigate: "/proxy/frigate",
    metrics: "/proxy/metrics",
    vllm: "/proxy/vllm",
    vision: "/proxy/vision",
    intelligence: "/proxy/intelligence",
    supervisor: "/proxy/supervisor",
    s2s: "/proxy/bridge",
    tracker: "/proxy/tracker",
    videoLabeler: "/proxy/video-labeler",
    apartmentAssets: "/assets/apartment",
  };

  const LAN_DEFAULTS = {
    ha: "http://192.168.0.125:8123",
    frigate: "http://192.168.0.125:5000",
    metrics: "http://192.168.0.100:8092",
    vllm: "http://192.168.0.100:8000",
    vision: "http://192.168.0.100:8091",
    intelligence: "http://192.168.0.100:8095",
    supervisor: "http://192.168.0.100:8093",
    s2s: "http://192.168.0.100:8094",
    tracker: "http://192.168.0.100:8098",
    videoLabeler: "http://192.168.0.100:8099",
    apartmentAssets: "http://127.0.0.1:5190",
  };

  const TAILSCALE_CANDIDATES = {
    ha: [
      "http://homeassistant:8123",
      `http://homeassistant.${TAILNET}:8123`,
      "http://100.116.3.41:8123",
    ],
    frigate: [
      "http://homeassistant:5000",
      `http://homeassistant.${TAILNET}:5000`,
      "http://100.116.3.41:5000",
    ],
    metrics: [
      "http://engineeredlightingserver1:8092",
      `http://engineeredlightingserver1.${TAILNET}:8092`,
      "http://100.87.94.18:8092",
    ],
    vllm: [
      "http://engineeredlightingserver1:8000",
      `http://engineeredlightingserver1.${TAILNET}:8000`,
      "http://100.87.94.18:8000",
    ],
    vision: [
      "http://engineeredlightingserver1:8091",
      `http://engineeredlightingserver1.${TAILNET}:8091`,
      "http://100.87.94.18:8091",
    ],
    intelligence: [
      "http://engineeredlightingserver1:8095",
      `http://engineeredlightingserver1.${TAILNET}:8095`,
      "http://100.87.94.18:8095",
    ],
    supervisor: [
      "http://engineeredlightingserver1:8093",
      `http://engineeredlightingserver1.${TAILNET}:8093`,
      "http://100.87.94.18:8093",
    ],
    s2s: [
      "http://engineeredlightingserver1:8094",
      `http://engineeredlightingserver1.${TAILNET}:8094`,
      "http://100.87.94.18:8094",
    ],
    tracker: [
      "http://engineeredlightingserver1:8098",
      `http://engineeredlightingserver1.${TAILNET}:8098`,
      "http://100.87.94.18:8098",
    ],
    videoLabeler: [
      "http://engineeredlightingserver1:8099",
      `http://engineeredlightingserver1.${TAILNET}:8099`,
      "http://100.87.94.18:8099",
    ],
    apartmentAssets: [
      "http://engineeredlightingserver1:5190",
      `http://engineeredlightingserver1.${TAILNET}:5190`,
      "http://100.87.94.18:5190",
    ],
  };

  const PROFILES = [
    {
      id: "home-lan",
      label: "Home LAN",
      shortLabel: "lan",
      description: "Use the local 192.168.0.x network.",
    },
    {
      id: "tailscale",
      label: "Remote via Tailscale",
      shortLabel: "tail",
      description: "Use MagicDNS and tailnet IP fallbacks.",
    },
    {
      id: "custom",
      label: "Custom",
      shortLabel: "custom",
      description: "Use manually edited service URLs.",
    },
  ];

  const listeners = new Set();

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function isWebMode() {
    return !!window.HG_WEB_MODE;
  }

  function normalizeUrl(value) {
    return String(value || "").trim().replace(/\/+$/, "");
  }

  function isService(service) {
    return SERVICE_KEYS.includes(service);
  }

  function readState() {
    try {
      const raw = window.localStorage && window.localStorage.getItem(STORAGE_KEY);
      const parsed = raw ? JSON.parse(raw) : {};
      return normalizeState(parsed);
    } catch {
      return normalizeState({});
    }
  }

  function normalizeState(raw) {
    const state = raw && typeof raw === "object" ? raw : {};
    const profile = PROFILES.some((p) => p.id === state.profile) ? state.profile : "home-lan";
    return {
      profile,
      custom: cleanServiceMap(state.custom),
      selected: state.selected && typeof state.selected === "object" ? state.selected : {},
      errors: Array.isArray(state.errors) ? state.errors.slice(-ERROR_LIMIT) : [],
    };
  }

  function cleanServiceMap(input) {
    const out = {};
    if (!input || typeof input !== "object") return out;
    for (const service of SERVICE_KEYS) {
      const value = normalizeUrl(input[service]);
      if (value) out[service] = value;
    }
    return out;
  }

  function writeState(state) {
    const next = normalizeState(state);
    try {
      window.localStorage && window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      /* storage can be unavailable in private contexts */
    }
    refreshGlobals(next);
    emit(next);
    return next;
  }

  function emit(state) {
    const detail = {
      profile: getProfile(state),
      services: resolvedServices(state),
    };
    for (const listener of listeners) {
      try { listener(detail); } catch (e) { console.error(e); }
    }
    try {
      window.dispatchEvent(new CustomEvent("home-services-change", { detail }));
    } catch {
      /* CustomEvent may be missing in a mocked test window */
    }
  }

  function profileById(id) {
    return PROFILES.find((profile) => profile.id === id) || PROFILES[0];
  }

  function candidatesForProfile(profileId, service, state) {
    if (!isService(service)) return [];
    if (isWebMode()) return [WEB_DEFAULTS[service]].filter(Boolean);
    if (profileId === "tailscale") return (TAILSCALE_CANDIDATES[service] || []).filter(Boolean);
    if (profileId === "custom") {
      const custom = state?.custom?.[service];
      return [custom || LAN_DEFAULTS[service]].filter(Boolean);
    }
    return [LAN_DEFAULTS[service]].filter(Boolean);
  }

  function resolveWithState(state, service, profileId) {
    if (!isService(service)) return "";
    if (isWebMode()) return WEB_DEFAULTS[service] || "";
    const activeProfile = profileId || state.profile || "home-lan";
    if (activeProfile === "custom") {
      return normalizeUrl(state.custom?.[service]) || LAN_DEFAULTS[service] || "";
    }
    const selected = normalizeUrl(state.selected?.[activeProfile]?.[service]);
    if (selected) return selected;
    const candidates = candidatesForProfile(activeProfile, service, state);
    return candidates[0] || LAN_DEFAULTS[service] || "";
  }

  function resolvedServices(state = readState()) {
    const out = {};
    for (const service of SERVICE_KEYS) out[service] = resolveWithState(state, service);
    return out;
  }

  function refreshGlobals(state = readState()) {
    for (const service of SERVICE_KEYS) {
      const key = META[service].runtimeKey;
      const value = resolveWithState(state, service);
      if (key && value) window[key] = value;
    }
    window.__HOME_SERVICE_PROFILE = getProfile(state);
  }

  function get(service) {
    return resolveWithState(readState(), service);
  }

  function candidates(service) {
    const state = readState();
    return candidatesForProfile(state.profile, service, state);
  }

  function seedCustomFromProfile(state, sourceProfile) {
    const custom = { ...(state.custom || {}) };
    for (const service of SERVICE_KEYS) {
      if (!custom[service]) custom[service] = resolveWithState(state, service, sourceProfile);
    }
    return custom;
  }

  function setProfile(id) {
    const profile = profileById(id);
    const state = readState();
    const previous = state.profile || "home-lan";
    if (profile.id === "custom") state.custom = seedCustomFromProfile(state, previous);
    state.profile = profile.id;
    return getProfile(writeState(state));
  }

  function setOverride(service, url) {
    if (!isService(service)) throw new Error(`unknown service: ${service}`);
    const value = normalizeUrl(url);
    if (!value) throw new Error(`missing URL for ${service}`);
    const state = readState();
    state.custom = seedCustomFromProfile(state, state.profile || "home-lan");
    state.custom[service] = value;
    state.profile = "custom";
    return getProfile(writeState(state));
  }

  function setCustomServices(services) {
    const state = readState();
    state.custom = seedCustomFromProfile(state, state.profile || "home-lan");
    for (const service of SERVICE_KEYS) {
      const value = normalizeUrl(services && services[service]);
      if (value) state.custom[service] = value;
    }
    state.profile = "custom";
    return getProfile(writeState(state));
  }

  function getProfile(state = readState()) {
    const profile = profileById(isWebMode() ? "home-lan" : state.profile);
    return {
      ...profile,
      id: isWebMode() ? "web" : profile.id,
      label: isWebMode() ? "Web gateway" : profile.label,
      shortLabel: isWebMode() ? "web" : profile.shortLabel,
      description: isWebMode() ? "Same-origin browser proxy routes." : profile.description,
      webMode: isWebMode(),
    };
  }

  function toHttpBase(url) {
    const clean = normalizeUrl(url);
    if (!clean) return "";
    if (clean.startsWith("/")) {
      const loc = window.location || {};
      const origin = loc.origin || `${loc.protocol || "http:"}//${loc.host || ""}`;
      return `${origin}${clean}`;
    }
    if (clean.startsWith("ws://")) return `http://${clean.slice("ws://".length)}`;
    if (clean.startsWith("wss://")) return `https://${clean.slice("wss://".length)}`;
    return clean;
  }

  function toWsBase(url) {
    const clean = normalizeUrl(url);
    if (!clean) return "";
    if (clean.startsWith("/")) {
      const loc = window.location || {};
      const proto = loc.protocol === "https:" ? "wss:" : "ws:";
      return `${proto}//${loc.host || ""}${clean}`;
    }
    if (clean.startsWith("http://")) return `ws://${clean.slice("http://".length)}`;
    if (clean.startsWith("https://")) return `wss://${clean.slice("https://".length)}`;
    return clean;
  }

  function probeUrl(service, url, timeoutMs = 4500) {
    const meta = META[service] || {};
    const base = toHttpBase(url);
    const probePath = meta.probePath || "/healthz";
    const attempt = (path) => {
      const started = Date.now();
      const target = `${base}${path}`;
      const fetcher = window.tauriFetch || window.fetch;
      if (!fetcher) return Promise.reject(new Error("fetch unavailable"));
      const init = { cache: "no-store" };
      const request = Promise.resolve().then(() => fetcher(target, init));
      const timeout = new Promise((_, reject) => {
        setTimeout(() => reject(new Error(`timeout after ${timeoutMs}ms`)), timeoutMs);
      });
      return Promise.race([request, timeout]).then((response) => {
        const status = response && typeof response.status === "number" ? response.status : 0;
        const ok = !!response?.ok || (meta.okStatuses || []).includes(status);
        return { ok, status, ms: Date.now() - started, url, probeUrl: target };
      });
    };
    return attempt(probePath).then((result) => {
      if (result.ok || !meta.fallbackProbePath) return result;
      return attempt(meta.fallbackProbePath);
    }).catch((error) => ({
      ok: false,
      status: 0,
      ms: timeoutMs,
      url,
      probeUrl: `${base}${probePath}`,
      error: String(error?.message || error),
    }));
  }

  async function probeAll() {
    const state = readState();
    const profile = getProfile(state);
    const results = [];
    const nextSelected = { ...(state.selected || {}) };
    if (!nextSelected[state.profile]) nextSelected[state.profile] = {};

    for (const service of SERVICE_KEYS) {
      const list = candidatesForProfile(state.profile, service, state);
      let best = null;
      const attempts = [];
      for (const url of list) {
        const result = await probeUrl(service, url);
        attempts.push(result);
        if (result.ok) {
          best = result;
          break;
        }
      }
      const resolved = best || attempts[attempts.length - 1] || {
        ok: false,
        status: 0,
        ms: 0,
        url: resolveWithState(state, service),
        error: "no candidate URL",
      };
      if (resolved.ok && !isWebMode() && state.profile !== "custom") {
        nextSelected[state.profile][service] = resolved.url;
      }
      results.push({
        service,
        label: META[service].label,
        profile: profile.id,
        url: resolved.url,
        ok: !!resolved.ok,
        status: resolved.status,
        ms: resolved.ms,
        error: resolved.error || "",
        attempts,
      });
    }

    state.selected = nextSelected;
    const failures = results.filter((r) => !r.ok);
    if (failures.length) {
      state.errors = [
        ...(state.errors || []),
        {
          ts: new Date().toISOString(),
          profile: profile.id,
          failures: failures.map((r) => ({
            service: r.service,
            url: r.url,
            error: r.error || `HTTP ${r.status}`,
          })),
        },
      ].slice(-ERROR_LIMIT);
    }
    writeState(state);
    return results;
  }

  function recentErrors() {
    return readState().errors || [];
  }

  function onChange(listener) {
    if (typeof listener !== "function") return () => {};
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  function buildInfo() {
    const tauri = window.__TAURI__;
    return {
      version: window.__HOME_APP_VERSION || "0.1.0",
      commit: window.__HOME_BUILD_COMMIT || "local",
      tauri: !!tauri,
      webMode: isWebMode(),
    };
  }

  function debugBundle(extra = {}) {
    return {
      generatedAt: new Date().toISOString(),
      build: buildInfo(),
      profile: getProfile(),
      services: resolvedServices(),
      recentErrors: recentErrors(),
      extra,
    };
  }

  const api = {
    services: SERVICE_KEYS.slice(),
    meta: clone(META),
    profiles: () => clone(PROFILES),
    get,
    candidates,
    setProfile,
    setOverride,
    setCustomServices,
    getProfile,
    getAll: () => resolvedServices(),
    probeAll,
    probeUrl,
    toWsBase,
    recentErrors,
    onChange,
    buildInfo,
    debugBundle,
  };

  window.HomeServices = api;
  window.__HomeServicesInternals = {
    STORAGE_KEY,
    WEB_DEFAULTS: clone(WEB_DEFAULTS),
    LAN_DEFAULTS: clone(LAN_DEFAULTS),
    TAILSCALE_CANDIDATES: clone(TAILSCALE_CANDIDATES),
  };
  refreshGlobals();
})();
