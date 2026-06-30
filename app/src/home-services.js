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

  const HOSTS = {
    windows: { label: "Windows dev machine", order: 10 },
    "ubuntu-ai": { label: "Ubuntu AI box", order: 20 },
    "home-assistant": { label: "Home Assistant", order: 30 },
    "browser-gateway": { label: "Browser gateway", order: 40 },
  };

  const TRAVEL_META = {
    ha: {
      host: "home-assistant",
      role: "core",
      travelSeverity: "blocker",
      recoveryHint: "Check HAOS, Tailscale on the Home Assistant machine, the HA token, and the HA API.",
      diagnosticCommand: "curl -i http://homeassistant:8123/api/",
    },
    frigate: {
      host: "home-assistant",
      role: "media",
      travelSeverity: "degraded",
      recoveryHint: "Check the Frigate add-on/container and that port 5000 is reachable over Tailscale.",
      diagnosticCommand: "curl -i http://homeassistant:5000/api/stats",
    },
    metrics: {
      host: "ubuntu-ai",
      role: "core",
      travelSeverity: "degraded",
      recoveryHint: "Check the metrics sidecar and the Ubuntu AI box Tailscale path.",
      diagnosticCommand: "curl -i http://home-app:8092/healthz",
    },
    vllm: {
      host: "ubuntu-ai",
      role: "ai",
      travelSeverity: "degraded",
      recoveryHint: "Check the vLLM container/service, GPU health, and model load state on the Ubuntu AI box.",
      diagnosticCommand: "curl -i http://home-app:8000/v1/models",
    },
    vision: {
      host: "ubuntu-ai",
      role: "ai",
      travelSeverity: "degraded",
      recoveryHint: "Check the vision sidecar on the Ubuntu AI box.",
      diagnosticCommand: "curl -i http://home-app:8091/healthz",
    },
    intelligence: {
      host: "ubuntu-ai",
      role: "ai",
      travelSeverity: "degraded",
      recoveryHint: "Check the intelligence service on the Ubuntu AI box.",
      diagnosticCommand: "curl -i http://home-app:8095/healthz",
    },
    supervisor: {
      host: "ubuntu-ai",
      role: "stack-control",
      travelSeverity: "degraded",
      recoveryHint: "Check BIND_ADDR, STACK_TOKEN, and the hav-stack-supervisor systemd unit.",
      diagnosticCommand: "sudo systemctl status hav-stack-supervisor --no-pager",
    },
    s2s: {
      host: "ubuntu-ai",
      role: "ai",
      travelSeverity: "degraded",
      recoveryHint: "Check the S2S bridge service and WebSocket reachability on the Ubuntu AI box.",
      diagnosticCommand: "curl -i http://home-app:8094/healthz",
    },
    tracker: {
      host: "ubuntu-ai",
      role: "media",
      travelSeverity: "degraded",
      recoveryHint: "Check the spatial tracker service and tracker WebSocket port on the Ubuntu AI box.",
      diagnosticCommand: "curl -i http://home-app:8098/healthz",
    },
    videoLabeler: {
      host: "ubuntu-ai",
      role: "media",
      travelSeverity: "degraded",
      recoveryHint: "Check the video labeler service and media mount on the Ubuntu AI box.",
      diagnosticCommand: "curl -i http://home-app:8099/healthz",
    },
    apartmentAssets: {
      host: "ubuntu-ai",
      role: "assets",
      travelSeverity: "degraded",
      recoveryHint: "Check home-apartment-assets and that app/data/apartment contains the scan and mesh files.",
      diagnosticCommand: "curl -i http://home-app:5190/healthz",
    },
  };

  for (const [service, travel] of Object.entries(TRAVEL_META)) {
    if (META[service]) Object.assign(META[service], travel);
  }

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
      "http://home-app:8092",
      `http://home-app.${TAILNET}:8092`,
      "http://100.87.94.18:8092",
    ],
    vllm: [
      "http://home-app:8000",
      `http://home-app.${TAILNET}:8000`,
      "http://100.87.94.18:8000",
    ],
    vision: [
      "http://home-app:8091",
      `http://home-app.${TAILNET}:8091`,
      "http://100.87.94.18:8091",
    ],
    intelligence: [
      "http://home-app:8095",
      `http://home-app.${TAILNET}:8095`,
      "http://100.87.94.18:8095",
    ],
    supervisor: [
      "http://home-app:8093",
      `http://home-app.${TAILNET}:8093`,
      "http://100.87.94.18:8093",
    ],
    s2s: [
      "http://home-app:8094",
      `http://home-app.${TAILNET}:8094`,
      "http://100.87.94.18:8094",
    ],
    tracker: [
      "http://home-app:8098",
      `http://home-app.${TAILNET}:8098`,
      "http://100.87.94.18:8098",
    ],
    videoLabeler: [
      "http://home-app:8099",
      `http://home-app.${TAILNET}:8099`,
      "http://100.87.94.18:8099",
    ],
    apartmentAssets: [
      "http://home-app:5190",
      `http://home-app.${TAILNET}:5190`,
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
      lastProbeAt: typeof state.lastProbeAt === "string" ? state.lastProbeAt : "",
      lastProbeProfile: typeof state.lastProbeProfile === "string" ? state.lastProbeProfile : "",
      lastProbe: Array.isArray(state.lastProbe) ? state.lastProbe : [],
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

  function sanitizeProbeResult(result) {
    if (!result || typeof result !== "object") return null;
    return {
      service: result.service || "",
      label: result.label || META[result.service]?.label || result.service || "",
      profile: result.profile || "",
      url: result.url || "",
      ok: !!result.ok,
      status: typeof result.status === "number" ? result.status : 0,
      ms: typeof result.ms === "number" ? result.ms : 0,
      error: result.error || "",
      checkedAt: result.checkedAt || "",
      attempts: Array.isArray(result.attempts)
        ? result.attempts.map((attempt) => ({
            url: attempt.url || "",
            ok: !!attempt.ok,
            status: typeof attempt.status === "number" ? attempt.status : 0,
            ms: typeof attempt.ms === "number" ? attempt.ms : 0,
            error: attempt.error || "",
          }))
        : [],
    };
  }

  function serviceResultMap(results) {
    const map = new Map();
    for (const result of Array.isArray(results) ? results : []) {
      if (result?.service) map.set(result.service, result);
    }
    return map;
  }

  function emptyHostRollups(includeBrowserGateway = false) {
    const keys = ["windows", "ubuntu-ai", "home-assistant"];
    if (includeBrowserGateway) keys.push("browser-gateway");
    const out = {};
    for (const key of keys) {
      out[key] = {
        id: key,
        label: HOSTS[key]?.label || key,
        status: key === "windows" ? "unknown" : "unknown",
        total: 0,
        ok: 0,
        failed: 0,
        blockers: 0,
        degraded: 0,
        optional: 0,
        unknown: 0,
        services: [],
      };
    }
    if (includeBrowserGateway) {
      out["browser-gateway"].status = "ready";
      out["browser-gateway"].total = 1;
      out["browser-gateway"].ok = 1;
      out["browser-gateway"].services.push({
        service: "webGateway",
        label: "Web gateway",
        ok: true,
        status: "ready",
        url: "/healthz",
      });
    }
    return out;
  }

  function severityCountsKey(severity) {
    if (severity === "blocker") return "blockers";
    if (severity === "optional") return "optional";
    return "degraded";
  }

  function hostStatusFromCounts(host) {
    if (!host || host.total === 0) return "unknown";
    if ((host.ok + host.failed) < host.total) return "unknown";
    if (host.blockers > 0) return "blocked";
    if (host.degraded > 0 || host.optional > 0) return "degraded";
    return "ready";
  }

  function risk(severity, service, title, detail, command) {
    return {
      severity,
      service: service || "",
      title,
      detail,
      command: command || "",
    };
  }

  function buildTravelReadiness(probeResults) {
    const state = readState();
    const profile = getProfile(state);
    const savedProbeMatchesProfile = state.lastProbeProfile === profile.id;
    const explicitProbeProfile = Array.isArray(probeResults) && probeResults.length ? probeResults[0]?.profile : "";
    const explicitProbeMatchesProfile = !explicitProbeProfile || explicitProbeProfile === profile.id;
    const source = Array.isArray(probeResults) && probeResults.length && explicitProbeMatchesProfile
      ? probeResults
      : (savedProbeMatchesProfile ? state.lastProbe : []);
    const results = (Array.isArray(source) ? source : [])
      .map(sanitizeProbeResult)
      .filter(Boolean);
    const byService = serviceResultMap(results);
    const checkedAt = results.length
      ? (results.find((r) => r.checkedAt)?.checkedAt || state.lastProbeAt || "")
      : "";
    const hosts = emptyHostRollups(isWebMode());
    const services = [];
    const failures = [];
    const risks = [];
    const counts = {
      total: SERVICE_KEYS.length,
      checked: results.length,
      reachable: 0,
      failed: 0,
      blockers: 0,
      degraded: 0,
      optional: 0,
      unknown: 0,
    };

    if (!results.length) {
      risks.push(risk(
        "unknown",
        "",
        "No recent travel check",
        "Run /travel check before relying on this app away from home.",
        "/travel check",
      ));
    }

    for (const service of SERVICE_KEYS) {
      const meta = META[service] || {};
      const result = byService.get(service);
      const hostKey = meta.host || "ubuntu-ai";
      const host = hosts[hostKey] || (hosts[hostKey] = {
        id: hostKey,
        label: HOSTS[hostKey]?.label || hostKey,
        status: "unknown",
        total: 0,
        ok: 0,
        failed: 0,
        blockers: 0,
        degraded: 0,
        optional: 0,
        unknown: 0,
        services: [],
      });
      const severity = meta.travelSeverity || "degraded";
      const item = {
        service,
        label: meta.label || service,
        host: hostKey,
        hostLabel: HOSTS[hostKey]?.label || hostKey,
        role: meta.role || "optional",
        severity,
        url: result?.url || resolveWithState(state, service),
        ok: !!result?.ok,
        status: result ? (result.ok ? "ready" : severity) : "unknown",
        httpStatus: result?.status || 0,
        ms: result?.ms || 0,
        error: result?.error || "",
        recoveryHint: meta.recoveryHint || "",
        diagnosticCommand: meta.diagnosticCommand || "",
        attempts: result?.attempts || [],
      };
      services.push(item);
      host.total += 1;
      host.services.push(item);
      if (!result) {
        counts.unknown += 1;
        host.unknown += 1;
        continue;
      }
      if (result.ok) {
        counts.reachable += 1;
        host.ok += 1;
      } else {
        counts.failed += 1;
        host.failed += 1;
        counts[severityCountsKey(severity)] += 1;
        host[severityCountsKey(severity)] += 1;
        failures.push(item);
      }
    }

    for (const host of Object.values(hosts)) {
      host.status = hostStatusFromCounts(host);
    }

    const ubuntu = hosts["ubuntu-ai"];
    const ubuntuChecked = (ubuntu?.services || []).filter((item) => item.status !== "unknown");
    const allUbuntuDown = ubuntuChecked.length > 0 && ubuntuChecked.length === ubuntu.services.length && ubuntuChecked.every((item) => !item.ok);
    if (allUbuntuDown) {
      risks.push(risk(
        "blocker",
        "",
        "Ubuntu AI box unreachable",
        "Every Ubuntu-owned service failed. Check power, Tailscale, SSH, and the machine itself before traveling.",
        "ssh hav-ubuntu 'tailscale status && systemctl --no-pager --failed'",
      ));
      if (ubuntu) {
        ubuntu.status = "blocked";
        ubuntu.blockers += 1;
      }
    }

    const supervisor = services.find((item) => item.service === "supervisor");
    if (supervisor && supervisor.status !== "unknown" && !supervisor.ok) {
      risks.push(risk(
        "degraded",
        "supervisor",
        "Remote stack control unavailable",
        "Supervisor is down or unreachable. Check BIND_ADDR, STACK_TOKEN, and hav-stack-supervisor before relying on stack controls.",
        supervisor.diagnosticCommand,
      ));
    }

    const apartmentAssets = services.find((item) => item.service === "apartmentAssets");
    if (apartmentAssets && apartmentAssets.status !== "unknown" && !apartmentAssets.ok) {
      risks.push(risk(
        "degraded",
        "apartmentAssets",
        "Apartment scan and mesh assets unavailable",
        "The 3D scan/mesh views need the Ubuntu asset service and app/data/apartment runtime files.",
        apartmentAssets.diagnosticCommand,
      ));
    }

    if (profile.id === "tailscale") {
      for (const item of services) {
        if (!item.ok) continue;
        const candidates = candidatesForProfile("tailscale", item.service, state);
        if (candidates.length > 1 && item.url && candidates[0] && item.url !== candidates[0]) {
          risks.push(risk(
            "degraded",
            item.service,
            `${item.label} is using a fallback URL`,
            `MagicDNS primary ${candidates[0]} did not win; selected ${item.url}. Keep the fallback IP handy for travel.`,
            "",
          ));
        }
      }
    }

    let status = "ready";
    if (!results.length) status = "unknown";
    else if (counts.blockers > 0 || allUbuntuDown) status = "blocked";
    else if (counts.degraded > 0 || risks.some((r) => r.severity === "degraded")) status = "degraded";

    return {
      generatedAt: new Date().toISOString(),
      checkedAt,
      status,
      profile,
      counts,
      hosts,
      services,
      failures,
      risks,
    };
  }

  function formatReadiness(readiness = buildTravelReadiness()) {
    const r = readiness || buildTravelReadiness();
    const lines = [
      `travel readiness: ${String(r.status || "unknown").toUpperCase()}`,
      `profile: ${r.profile?.label || "unknown"}`,
      `last check: ${r.checkedAt || "not checked"}`,
      `reachable: ${r.counts?.reachable || 0}/${r.counts?.total || 0}`,
      `blockers: ${r.counts?.blockers || 0}  degraded: ${r.counts?.degraded || 0}  optional: ${r.counts?.optional || 0}`,
    ];
    for (const host of Object.values(r.hosts || {}).sort((a, b) => (HOSTS[a.id]?.order || 99) - (HOSTS[b.id]?.order || 99))) {
      lines.push(`${host.label}: ${host.status} (${host.ok}/${host.total})`);
    }
    const failures = r.failures || [];
    if (failures.length) {
      lines.push("failures:");
      for (const item of failures) {
        const reason = item.error || (item.httpStatus ? `HTTP ${item.httpStatus}` : "unreachable");
        lines.push(`- ${item.label}: ${reason}`);
        if (item.recoveryHint) lines.push(`  ${item.recoveryHint}`);
        if (item.diagnosticCommand) lines.push(`  ${item.diagnosticCommand}`);
      }
    }
    const risks = r.risks || [];
    if (risks.length) {
      lines.push("risks:");
      for (const item of risks) lines.push(`- ${item.title}: ${item.detail}`);
    }
    return lines.join("\n");
  }

  function recoveryCommands(readiness = buildTravelReadiness()) {
    const commands = [
      {
        title: "Windows travel machine",
        commands: [
          "tailscale status",
          "ping home-app",
          "ping homeassistant",
          "ssh hav-ubuntu 'cd ~/code/home && tools/travel-readiness.sh'",
        ],
      },
      {
        title: "Ubuntu AI box",
        commands: [
          "tailscale status",
          "cd ~/code/home && tools/travel-readiness.sh",
          "sudo systemctl status home-web-gateway home-apartment-assets hav-stack-supervisor --no-pager",
          "journalctl -u home-web-gateway -n 80 --no-pager",
          "journalctl -u home-apartment-assets -n 80 --no-pager",
          "journalctl -u hav-stack-supervisor -n 80 --no-pager",
        ],
      },
      {
        title: "Home Assistant",
        commands: [
          "curl -i http://homeassistant:8123/api/",
          "curl -i http://homeassistant:5000/api/stats",
        ],
      },
      {
        title: "Web deploy rollback",
        commands: [
          "cd ~/code/home",
          "git log --oneline -5",
          "git reset --hard <previous_good_sha>",
          "sudo systemctl restart home-web-gateway",
        ],
      },
    ];
    const failed = new Set((readiness.failures || []).map((item) => item.service));
    if (failed.has("apartmentAssets")) {
      commands.push({
        title: "Apartment assets",
        commands: [
          "cd ~/code/home",
          "tools/check-home-web-assets.sh",
          "sudo systemctl restart home-apartment-assets",
          "curl -i http://home-app:5190/healthz",
        ],
      });
    }
    if (failed.has("supervisor")) {
      commands.push({
        title: "Stack supervisor",
        commands: [
          "grep -E '^(BIND_ADDR|STACK_TOKEN)=' /opt/home-ai-voice/.env",
          "sudo systemctl restart hav-stack-supervisor",
          "curl -i http://home-app:8093/healthz",
        ],
      });
    }
    return commands;
  }

  function formatRecoveryCommands(readiness = buildTravelReadiness()) {
    return recoveryCommands(readiness).map((group) => {
      const body = (group.commands || []).map((cmd) => `  ${cmd}`).join("\n");
      return `${group.title}\n${body}`;
    }).join("\n\n");
  }

  function formatServiceUrls() {
    const services = resolvedServices();
    return SERVICE_KEYS.map((service) => `${META[service]?.label || service}: ${services[service] || ""}`).join("\n");
  }

  async function probeAll() {
    const state = readState();
    const profile = getProfile(state);
    const results = [];
    const nextSelected = { ...(state.selected || {}) };
    if (!nextSelected[state.profile]) nextSelected[state.profile] = {};
    const checkedAt = new Date().toISOString();

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
        checkedAt,
      });
    }

    state.selected = nextSelected;
    state.lastProbeAt = checkedAt;
    state.lastProbeProfile = profile.id;
    state.lastProbe = results.map(sanitizeProbeResult).filter(Boolean);
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
    const readiness = buildTravelReadiness(extra.lastProbe);
    const state = readState();
    return {
      generatedAt: new Date().toISOString(),
      build: buildInfo(),
      profile: getProfile(),
      services: resolvedServices(),
      selectedCandidates: state.selected || {},
      lastProbeAt: state.lastProbeAt || "",
      lastProbeProfile: state.lastProbeProfile || "",
      readiness,
      recoveryCommands: recoveryCommands(readiness),
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
    buildTravelReadiness,
    formatReadiness,
    recoveryCommands,
    formatRecoveryCommands,
    formatServiceUrls,
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
    HOSTS: clone(HOSTS),
  };
  refreshGlobals();
})();
