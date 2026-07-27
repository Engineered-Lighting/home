/* home-people-prewarm.js - lightweight warm cache for the People view.
 *
 * The People graph UI stays lazy, but the identity payload is small enough to
 * fetch shortly after boot. This lets the People overlay render from a recent
 * in-memory result even when mobile networking blips during the foreground
 * refresh.
 */
(function () {
  const CACHE_TTL_MS = 5 * 60 * 1000;
  let run = null;

  function normalizeEndpoint(endpoint) {
    return String(endpoint || "").replace(/\/+$/, "");
  }

  function writeCache(endpoint, payload, facesByPerson) {
    if (!payload || !Array.isArray(payload.identities)) return null;
    const cache = {
      endpoint: normalizeEndpoint(endpoint),
      cachedAt: Date.now(),
      identities: payload.identities || [],
      relationships: payload.relationships || [],
      frigateUrl: payload.frigate_url || null,
      facesByPerson: facesByPerson || null,
      frigateDiagnostics: {
        seedReport: payload.frigate_seed_report || null,
        capabilities: payload.frigate_capabilities || null,
      },
    };
    window.__HOME_PEOPLE_DATA_CACHE = cache;
    window.dispatchEvent(new CustomEvent("home-people-prewarm", { detail: status() }));
    return cache;
  }

  function readCache(endpoint, maxAgeMs = CACHE_TTL_MS) {
    const cache = window.__HOME_PEOPLE_DATA_CACHE;
    if (!cache) return null;
    if (cache.endpoint !== normalizeEndpoint(endpoint)) return null;
    if (!cache.cachedAt || Date.now() - cache.cachedAt > maxAgeMs) return null;
    return cache;
  }

  function loadPrefs() {
    try {
      if (typeof window.loadPrefs === "function") return window.loadPrefs({});
    } catch (_) {}
    try {
      const raw = localStorage.getItem("hg-prefs");
      return raw ? JSON.parse(raw) : {};
    } catch (_) {
      return {};
    }
  }

  function fetchJson(url, token, signal) {
    return fetch(url, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
      signal,
    }).then((resp) => {
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return resp.json();
    });
  }

  async function start(options = {}) {
    if (run) return run;
    const prefs = options.endpoint && options.token ? options : loadPrefs();
    const endpoint = normalizeEndpoint(options.endpoint || prefs.endpoint || window.HG_DEFAULT_HA_BASE);
    const token = options.token || prefs.token || "";
    const signal = options.signal || null;
    if (!endpoint || !token) return { ok: false, skipped: true, reason: "missing credentials" };
    const existing = readCache(endpoint);
    if (existing && !options.force) {
      return { ok: true, cached: true, identities: existing.identities.length };
    }
    run = (async () => {
      const startedAt = Date.now();
      const result = { ok: true, identities: 0, faces: 0, durationMs: 0 };
      try {
        const payload = await fetchJson(`${endpoint}/api/extended_openai_conversation/identities`, token, signal);
        result.identities = Array.isArray(payload?.identities) ? payload.identities.length : 0;
        let facesByPerson = null;
        if (payload?.frigate_url) {
          try {
            facesByPerson = await fetchJson(`${endpoint}/api/extended_openai_conversation/frigate_proxy?path=api/faces`, token, signal);
            result.faces = facesByPerson && typeof facesByPerson === "object" ? Object.keys(facesByPerson).length : 0;
          } catch (err) {
            result.facesError = err?.message || String(err);
          }
        }
        writeCache(endpoint, payload, facesByPerson);
        return result;
      } catch (err) {
        result.ok = false;
        result.error = err?.message || String(err);
        return result;
      } finally {
        result.durationMs = Date.now() - startedAt;
        window.__HOME_PEOPLE_PREWARM = { ...result, updatedAt: Date.now() };
        run = null;
      }
    })();
    return run;
  }

  function status() {
    const cache = window.__HOME_PEOPLE_DATA_CACHE || null;
    return {
      state: run ? "running" : cache ? "ready" : "idle",
      cache: cache ? {
        endpoint: cache.endpoint,
        ageMs: Date.now() - cache.cachedAt,
        identities: cache.identities?.length || 0,
        faces: cache.facesByPerson ? Object.keys(cache.facesByPerson).length : 0,
      } : null,
      last: window.__HOME_PEOPLE_PREWARM || null,
    };
  }

  function scheduleAutoStart() {
    const waitForBoot = () => {
      if (window.__bootState?.failed) return;
      if (!window.__bootState?.done) {
        setTimeout(waitForBoot, 500);
        return;
      }
      const cb = () => start({ reason: "post-boot" });
      if (window.requestIdleCallback) window.requestIdleCallback(cb, { timeout: 2500 });
      else setTimeout(cb, 1200);
    };
    setTimeout(waitForBoot, 500);
  }

  window.HomePeoplePrewarm = {
    start,
    status,
    readCache,
    writeCache,
  };
  scheduleAutoStart();
})();
