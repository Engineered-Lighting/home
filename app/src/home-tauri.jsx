/* Home — Tauri runtime glue.
 *
 * Lightweight wrappers around the Tauri 2.x JavaScript API exposed on
 * window.__TAURI__. Everything degrades cleanly to plain browser when run
 * outside Tauri (e.g. opening index.html in a browser for design review).
 *
 * The shapes here purposely mirror what HomeApp uses — getWindow(), tauriFetch(),
 * loadPrefs(), savePrefs() — so the call sites in home-app.jsx don't care
 * whether they're in Tauri or a vanilla browser.
 */

const _tauri = typeof window !== "undefined" ? window.__TAURI__ : undefined;
const IS_TAURI = !!_tauri;

/* Current Tauri window (for minimize/close/drag operations). */
function getTauriWindow() {
  if (!IS_TAURI) return null;
  const win = _tauri?.window || _tauri?.webviewWindow;
  return win?.getCurrentWindow ? win.getCurrentWindow() :
         win?.getCurrent      ? win.getCurrent()       :
         win?.appWindow       || null;
}

/* Browser fetch wrapper. The former broad Tauri HTTP plugin is intentionally
 * removed: native Agent/HA authentication and semantic requests cross only
 * the typed Rust command boundary. Legacy cross-origin service calls are
 * fail-closed in the packaged desktop app.
 *
 * Simulation Mode guard: when `window.__SIM_ACTIVE === true`, return a
 * safe `{ok:false}` response without making the network call. This is
 * a defense-in-depth backstop — the primary guards are at the useEffect
 * level (real-service pollers early-return). A first warning per host
 * is logged so leaks are visible during development. */
const _simWarnedHosts = new Set();
async function tauriFetch(url, init) {
  if (typeof window !== "undefined" && window.__SIM_ACTIVE === true) {
    try {
      const u = new URL(url);
      if (!_simWarnedHosts.has(u.host)) {
        _simWarnedHosts.add(u.host);
        console.warn(`[sim] tauriFetch blocked request to ${u.host} — Simulation Mode is active.`);
      }
    } catch (e) { /* ignore url parse */ }
    return {
      ok: false, status: 0, statusText: "blocked-by-simulation-mode",
      json: async () => ({}),
      text: async () => "",
    };
  }
  if (IS_TAURI) {
    void url;
    void init;
    return {
      ok: false, status: 0, statusText: "native-typed-transport-required",
      json: async () => ({}),
      text: async () => "",
    };
  }
  return fetch(url, init);
}

/* localStorage-backed non-sensitive prefs. Credentials and private history
 * are deliberately excluded; HomeSecurity removes values from older builds. */
const PREFS_KEY = "hg-prefs";

function safePrefs(input) {
  if (window.HomeSecurity?.sanitizePrefs) {
    return window.HomeSecurity.sanitizePrefs(input);
  }
  const {
    token, accessToken, refreshToken, s2sToken,
    stackToken, externalToken, apiKey, ...rest
  } = input || {};
  return rest;
}

function loadPrefs(defaults = {}) {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    const prefs = raw ? { ...defaults, ...safePrefs(JSON.parse(raw)) } : { ...defaults };
    if (typeof window !== "undefined" && window.HomeServices && !window.HG_WEB_MODE) {
      const services = window.HomeServices;
      const endpoint = services.get("ha");
      const metricsBase = services.get("metrics");
      const s2sBase = services.get("s2s");
      if (endpoint) prefs.endpoint = endpoint;
      if (metricsBase) prefs.metricsBase = metricsBase;
      if (s2sBase) prefs.s2sBase = s2sBase;
    }
    if (typeof window !== "undefined" && window.HG_WEB_MODE) {
      const isDefaultLan = (value, port) => {
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
      };
      if (window.HG_DEFAULT_HA_BASE && isDefaultLan(prefs.endpoint, 8123)) prefs.endpoint = window.HG_DEFAULT_HA_BASE;
      if (window.HG_DEFAULT_METRICS_BASE && isDefaultLan(prefs.metricsBase, 8092)) prefs.metricsBase = window.HG_DEFAULT_METRICS_BASE;
      if (window.HG_DEFAULT_S2S_BASE && isDefaultLan(prefs.s2sBase, 8094)) prefs.s2sBase = window.HG_DEFAULT_S2S_BASE;
    }
    return prefs;
  } catch {
    return { ...defaults };
  }
}

function savePrefs(prefs) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(safePrefs(prefs)));
  } catch {}
}

function loadEvents() {
  return [];
}

function saveEvents(events) {
  // Conversation/event content is session-only until an explicit governed
  // memory transaction elects and encrypts a source turn.
  void events;
}

function loadConversationId() {
  return null;
}
function saveConversationId(id) {
  void id;
}

/* localStorage-backed onboarding flags — its own tiny blob (savePrefs writes a
 * fixed key set, so onboarding state can't live there). saveOnboarding is a
 * merge-patch: it reads the current blob, applies the patch, writes back. */
const ONBOARDING_KEY = "hg-onboarding";

function loadOnboarding(defaults = {}) {
  try {
    const raw = localStorage.getItem(ONBOARDING_KEY);
    if (!raw) return { ...defaults };
    return { ...defaults, ...JSON.parse(raw) };
  } catch {
    return { ...defaults };
  }
}

function saveOnboarding(patch) {
  try {
    const cur = loadOnboarding();
    localStorage.setItem(ONBOARDING_KEY, JSON.stringify({ ...cur, ...patch }));
  } catch {}
}

/* Window controls — no-op when not running in Tauri. */
async function winClose()    { (await getTauriWindow())?.close?.(); }
async function winMinimize() { (await getTauriWindow())?.minimize?.(); }
async function winMaximize() {
  const w = await getTauriWindow();
  if (!w) return;
  const max = await w.isMaximized?.();
  if (max) w.unmaximize?.();
  else     w.maximize?.();
}

Object.assign(window, {
  IS_TAURI,
  getTauriWindow,
  tauriFetch,
  loadPrefs, savePrefs,
  loadEvents, saveEvents,
  loadConversationId, saveConversationId,
  loadOnboarding, saveOnboarding,
  winClose, winMinimize, winMaximize,
});
