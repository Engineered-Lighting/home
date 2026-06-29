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

/* Wrapper around fetch that uses Tauri's HTTP plugin in Tauri (bypassing
 * webview CORS), falling back to the browser fetch. The plugin lives at
 * window.__TAURI__.http.fetch in Tauri 2.x.
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
  if (IS_TAURI && _tauri.http?.fetch) {
    return _tauri.http.fetch(url, init);
  }
  return fetch(url, init);
}

/* localStorage-backed prefs. Two-arg writer accepts either a key/value or an
 * object of edits (so callers don't have to think). */
const PREFS_KEY = "hg-prefs";

function loadPrefs(defaults = {}) {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    const prefs = raw ? { ...defaults, ...JSON.parse(raw) } : { ...defaults };
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
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch {}
}

const EVENTS_KEY = "hg-events";
const CONV_ID_KEY = "hg-conv-id";
const MAX_EVENTS = 200;

function loadEvents() {
  try {
    const raw = localStorage.getItem(EVENTS_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.slice(-MAX_EVENTS) : [];
  } catch {
    return [];
  }
}

function saveEvents(events) {
  try {
    const trimmed = events.slice(-MAX_EVENTS).map((e) => {
      // Persisted events are always at rest — strip the streaming flag so
      // restored turns don't show a blinking caret.
      const { streaming, ...rest } = e;
      return rest;
    });
    localStorage.setItem(EVENTS_KEY, JSON.stringify(trimmed));
  } catch {}
}

function loadConversationId() {
  try { return localStorage.getItem(CONV_ID_KEY) || null; } catch { return null; }
}
function saveConversationId(id) {
  try {
    if (id) localStorage.setItem(CONV_ID_KEY, id);
    else    localStorage.removeItem(CONV_ID_KEY);
  } catch {}
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
