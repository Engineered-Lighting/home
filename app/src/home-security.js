/* Home security containment helpers.
 *
 * This file executes before the JSX boot chain. It removes credentials and
 * private conversational artifacts written by older builds and provides the
 * single allowlist used by the remaining localStorage preference adapter.
 */
(function initHomeSecurity(global) {
  "use strict";

  const PREFS_KEY = "hg-prefs";
  const SERVICES_KEY = "hg-services-v1";
  const CACHE_MIGRATION_KEY = "hg-security-cache-purge-v1";
  const PRIVATE_STORAGE_KEYS = Object.freeze([
    "hg-stack-token-DEV",
    "hg-external-token-DEV",
    "hg-events",
    "hg-conv-id",
    "hg-lab-turns-v1",
    "hg-proactive-pending",
    // Older spatial surfaces persisted complete apartment models, including
    // device/camera placement, outside the governed Agent stores.
    "apartment3d.modelDraft",
    "apartment3d.remoteCache",
  ]);
  const PRIVATE_STORAGE_PREFIXES = Object.freeze([
    // Draft labels can contain private media names, notes, and time ranges.
    "videoLabeler.draft.",
  ]);
  const SECRET_PREF_FIELDS = Object.freeze([
    "token", "accessToken", "refreshToken", "s2sToken",
    "stackToken", "externalToken", "apiKey",
  ]);
  const SAFE_PREF_FIELDS = new Set([
    "endpoint", "model", "theme", "metricsBase", "s2sBase",
    "s2sVoice", "kokoroVoice", "debugMode",
  ]);
  const LEGACY_URL_KEYS = Object.freeze([
    "hg-intelligence-base",
    "videoLabeler.base",
    "apartment3d.trackerBase",
    "apartment3d.assetsBase",
  ]);

  function sanitizeServiceUrl(value) {
    if (typeof value !== "string" || !value.trim()) return value;
    try {
      const raw = value.trim();
      if (raw.startsWith("/") && !raw.startsWith("//")) {
        const parsedPath = new URL(raw, "https://home.invalid");
        return parsedPath.pathname.replace(/\/+$/, "") || "/";
      }
      const parsed = new URL(raw);
      if (!['http:', 'https:'].includes(parsed.protocol)) return "";
      parsed.username = "";
      parsed.password = "";
      parsed.search = "";
      parsed.hash = "";
      return parsed.toString().replace(/\/$/, "");
    } catch (_) {
      return "";
    }
  }

  function sanitizePrefs(input) {
    const source = input && typeof input === "object" ? input : {};
    const safe = {};
    for (const [key, value] of Object.entries(source)) {
      if (!SAFE_PREF_FIELDS.has(key)) continue;
      if (["endpoint", "metricsBase", "s2sBase"].includes(key)) {
        if (typeof value !== "string" || value.length > 2048) continue;
        const sanitized = sanitizeServiceUrl(value);
        if (sanitized) safe[key] = sanitized;
      } else if (key === "debugMode") {
        if (typeof value === "boolean") safe[key] = value;
      } else if (typeof value === "string" && value.length <= 256) {
        safe[key] = value;
      }
    }
    return safe;
  }

  function purgeLegacySensitiveStorage(storage) {
    if (!storage) return { removed: [], prefsSanitized: false };
    const removed = [];
    for (const key of PRIVATE_STORAGE_KEYS) {
      try {
        if (storage.getItem(key) !== null) removed.push(key);
        storage.removeItem(key);
      } catch (_) { /* callers also refuse to read legacy secrets */ }
    }
    // localStorage.key()/length are part of the Storage contract. Snapshot
    // names before deleting so index compaction cannot skip a matching key.
    try {
      const names = [];
      for (let index = 0; index < Number(storage.length || 0); index += 1) {
        const name = storage.key(index);
        if (typeof name === "string") names.push(name);
      }
      for (const name of names) {
        if (!PRIVATE_STORAGE_PREFIXES.some((prefix) => name.startsWith(prefix))) continue;
        if (storage.getItem(name) !== null) removed.push(name);
        storage.removeItem(name);
      }
    } catch (_) { /* fail-closed readers no longer consume legacy drafts */ }

    let prefsSanitized = false;
    try {
      const raw = storage.getItem(PREFS_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        const safe = sanitizePrefs(parsed);
        prefsSanitized = SECRET_PREF_FIELDS.some((key) =>
          Object.prototype.hasOwnProperty.call(parsed || {}, key));
        storage.setItem(PREFS_KEY, JSON.stringify(safe));
      }
    } catch (_) {
      // Malformed preference JSON is private legacy data too. Remove it so a
      // later build cannot reinterpret attacker-controlled or secret fields.
      try { storage.removeItem(PREFS_KEY); } catch (_) {}
    }
    for (const [key, value] of [["hg-external-enabled", "off"], ["hg-lab-persist", "off"]]) {
      try { storage.setItem(key, value); } catch (_) {}
    }
    try {
      const raw = storage.getItem(SERVICES_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        const custom = {};
        for (const [key, value] of Object.entries(parsed?.custom || {})) {
          const safe = sanitizeServiceUrl(value);
          if (safe) custom[key] = safe;
        }
        storage.setItem(SERVICES_KEY, JSON.stringify({
          profile: typeof parsed?.profile === "string" ? parsed.profile : "home-lan",
          custom,
          selected: {},
          errors: [],
          lastProbeAt: "",
          lastProbeProfile: "",
          lastProbe: [],
        }));
      }
    } catch (_) {
      try { storage.removeItem(SERVICES_KEY); } catch (_) {}
    }
    for (const key of LEGACY_URL_KEYS) {
      try {
        const raw = storage.getItem(key);
        if (raw === null) continue;
        const safe = sanitizeServiceUrl(raw);
        if (safe) storage.setItem(key, safe);
        else storage.removeItem(key);
      } catch (_) {
        try { storage.removeItem(key); } catch (_) {}
      }
    }
    return { removed, prefsSanitized };
  }

  async function purgeLegacySensitiveCaches(storage, cacheStorage) {
    if (!storage || !cacheStorage || typeof cacheStorage.keys !== "function") {
      return { deleted: [], supported: false };
    }
    try {
      if (storage.getItem(CACHE_MIGRATION_KEY) === "done") {
        return { deleted: [], supported: true };
      }
      const names = await cacheStorage.keys();
      await Promise.all(names.map((name) => cacheStorage.delete(name)));
      storage.setItem(CACHE_MIGRATION_KEY, "done");
      return { deleted: names, supported: true };
    } catch (_) {
      // Do not mark a failed pass complete. The next startup retries before
      // private Agent traffic is allowed to rely on the new cache policy.
      return { deleted: [], supported: true, failed: true };
    }
  }

  // Explicitly remembered HA connection only. General preferences and other
  // credentials still pass through the existing secret-stripping migration.
  const HA_LOGIN_KEY = "home.ha.remembered-login.v1";
  const HA_REMEMBER_KEY = "home.ha.remember-login";
  function remembersHaLogin(storage) {
    try { return storage.getItem(HA_REMEMBER_KEY) !== "off"; } catch (_) { return false; }
  }
  function readHaLogin(storage, endpoint) {
    try {
      const row = JSON.parse(storage.getItem(HA_LOGIN_KEY) || "null");
      const expected = sanitizeServiceUrl(endpoint);
      if (!remembersHaLogin(storage) || !row || row.endpoint !== expected || !expected) return null;
      if (typeof row.token !== "string" || !row.token.trim() || row.token.length > 16384) return null;
      return { endpoint: expected, token: row.token };
    } catch (_) { return null; }
  }
  function saveHaLogin(storage, endpoint, token, remember = true) {
    try {
      storage.setItem(HA_REMEMBER_KEY, remember ? "on" : "off");
      if (!remember) { storage.removeItem(HA_LOGIN_KEY); return true; }
      const safe = sanitizeServiceUrl(endpoint);
      if (!safe || typeof token !== "string" || !token.trim() || token.length > 16384) return false;
      storage.setItem(HA_LOGIN_KEY, JSON.stringify({ endpoint: safe, token: token.trim() }));
      return true;
    } catch (_) { return false; }
  }
  function forgetHaLogin(storage, endpoint, token) {
    try {
      if (endpoint) {
        const saved = readHaLogin(storage, endpoint);
        if (!saved || (token && saved.token !== token)) return;
      }
      storage.removeItem(HA_LOGIN_KEY);
    } catch (_) {}
  }

  const api = Object.freeze({
    HA_LOGIN_KEY, HA_REMEMBER_KEY, remembersHaLogin, readHaLogin, saveHaLogin, forgetHaLogin,
    PREFS_KEY,
    SERVICES_KEY,
    CACHE_MIGRATION_KEY,
    PRIVATE_STORAGE_KEYS,
    PRIVATE_STORAGE_PREFIXES,
    SECRET_PREF_FIELDS,
    SAFE_PREF_FIELDS,
    LEGACY_URL_KEYS,
    sanitizePrefs,
    sanitizeServiceUrl,
    purgeLegacySensitiveStorage,
    purgeLegacySensitiveCaches,
  });

  if (global) {
    global.HomeSecurity = api;
    try { global.__HOME_SECURITY_MIGRATION = purgeLegacySensitiveStorage(global.localStorage); }
    catch (_) { global.__HOME_SECURITY_MIGRATION = { removed: [], prefsSanitized: false }; }
    global.__HOME_SECURITY_CACHE_MIGRATION = purgeLegacySensitiveCaches(
      global.localStorage,
      global.caches,
    );
  }
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
