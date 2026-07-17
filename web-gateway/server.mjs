import crypto from "node:crypto";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import https from "node:https";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const APP_DIR = path.join(ROOT, "app", "src");
const FALLBACK_APARTMENT_DIR = path.join(APP_DIR, "assets", "apartment");

const HOST = process.env.HOME_WEB_HOST || "127.0.0.1";
const PORT = Number(process.env.HOME_WEB_PORT || 5181);
const APARTMENT_ASSETS_DIR = path.resolve(process.env.HOME_WEB_APARTMENT_ASSETS_DIR || path.join(ROOT, "app", "data", "apartment"));
const LEGACY_BASIC_AUTH = (process.env.HOME_WEB_BASIC_AUTH || "").trim();
const AUTH_REQUIRED = /^(1|true|yes|on)$/i.test(String(process.env.HOME_WEB_AUTH_REQUIRED || ""));
const defaultAuthRoot =
  process.env.APPDATA ||
  process.env.LOCALAPPDATA ||
  process.env.XDG_CONFIG_HOME ||
  (process.env.HOME ? path.join(process.env.HOME, ".config") : ROOT);
const AUTH_FILE = path.resolve(
  process.env.HOME_WEB_AUTH_FILE || path.join(defaultAuthRoot, "EngineeredLightingHome", "web-auth.json"),
);
const AUTH_COOKIE = "home_web_auth";
const PASSWORD_MIN_LENGTH = 12;
const PACKAGE_JSON = (() => {
  try {
    return JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8"));
  } catch {
    return {};
  }
})();
function readGitCommit() {
  try {
    return execFileSync("git", ["rev-parse", "--short=12", "HEAD"], {
      cwd: ROOT,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  } catch {
    return "local";
  }
}

const BUILD_VERSION = process.env.HOME_BUILD_VERSION || PACKAGE_JSON.version || "0.1.0";
const BUILD_COMMIT = process.env.HOME_BUILD_COMMIT || process.env.GITHUB_SHA || readGitCommit();
const BUILD_STARTED_AT = Date.now().toString(36);
const BUILD_ASSET_VERSION = process.env.HOME_WEB_ASSET_VERSION || `${BUILD_COMMIT}-${BUILD_STARTED_AT}`;
const STATIC_VERSIONED_CACHE = "private, max-age=31536000, immutable";
const STATIC_REVALIDATE_CACHE = "private, no-cache";
const APARTMENT_HEAVY_CACHE = "private, max-age=604800, stale-while-revalidate=86400";
const APARTMENT_METADATA_CACHE = "private, no-cache";
const STACK_TOKEN_PROXY_MARKER = "__home_web_gateway_stack_token__";
const DEFAULT_STACK_TOKEN_FILE = "/opt/home-ai-voice/.env";

const MIME = new Map([
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".jsx", "text/plain; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".webmanifest", "application/manifest+json; charset=utf-8"],
  [".svg", "image/svg+xml"],
  [".png", "image/png"],
  [".jpg", "image/jpeg"],
  [".jpeg", "image/jpeg"],
  [".webp", "image/webp"],
  [".ico", "image/x-icon"],
  [".glb", "model/gltf-binary"],
  [".ply", "application/octet-stream"],
  [".spz", "application/octet-stream"],
  [".wasm", "application/wasm"],
]);

function envTarget(name, fallback) {
  return (process.env[name] || fallback).replace(/\/+$/, "");
}

function parseEnvValue(value) {
  let text = String(value || "").trim();
  if (
    (text.startsWith('"') && text.endsWith('"')) ||
    (text.startsWith("'") && text.endsWith("'"))
  ) {
    text = text.slice(1, -1);
  }
  return text.trim();
}

function readStackTokenFile(filePath) {
  if (!filePath || !fs.existsSync(filePath)) return "";
  try {
    const raw = fs.readFileSync(filePath, "utf8");
    for (const line of raw.split(/\r?\n/)) {
      const match = line.match(/^\s*STACK_TOKEN\s*=\s*(.*?)\s*$/);
      if (match) return parseEnvValue(match[1]);
    }
  } catch {
    return "";
  }
  return "";
}

function loadStackTokenProxy() {
  const direct = parseEnvValue(process.env.HOME_WEB_STACK_TOKEN || process.env.STACK_TOKEN || "");
  if (direct) {
    return {
      enabled: true,
      source: process.env.HOME_WEB_STACK_TOKEN ? "HOME_WEB_STACK_TOKEN" : "STACK_TOKEN",
      token: direct,
    };
  }
  const token = readStackTokenFile(process.env.HOME_WEB_STACK_TOKEN_FILE || DEFAULT_STACK_TOKEN_FILE);
  return token
    ? { enabled: true, source: "file", token }
    : { enabled: false, source: "none", token: "" };
}

const stackTokenProxy = loadStackTokenProxy();

function rx(pattern) {
  return (suffix) => pattern.test(suffix.split("?")[0]);
}

const GROUNDED_VISION_CAMERA_PATH = "[a-z0-9_-]{1,64}";
const GROUNDED_VISION_IMAGE_PATHS = [
  new RegExp(`^/reason/${GROUNDED_VISION_CAMERA_PATH}/annotated\\.jpg$`, "i"),
  new RegExp(`^/reason_zoom/${GROUNDED_VISION_CAMERA_PATH}/(?:overview|detail)\\.jpg$`, "i"),
  new RegExp(`^/snapshot/${GROUNDED_VISION_CAMERA_PATH}/latest\\.jpg$`, "i"),
];

const GROUNDED_VISION_METADATA_PATHS = new Set([
  "/reason/latest",
  "/describe/latest",
]);

function hasOnlyCacheBuster(parsed) {
  if (!parsed.search) return true;
  const entries = [...parsed.searchParams.entries()];
  return entries.length === 1 && entries[0][0] === "cb" && /^\d{1,20}$/.test(entries[0][1]);
}

function isGroundedVisionResultRoute(suffix, method) {
  if (method !== "GET" && method !== "HEAD") return false;
  const parsed = new URL(suffix, "http://home.local");
  if (GROUNDED_VISION_METADATA_PATHS.has(parsed.pathname)) return !parsed.search;
  return GROUNDED_VISION_IMAGE_PATHS.some((pattern) => pattern.test(parsed.pathname)) &&
    hasOnlyCacheBuster(parsed);
}

function stripVisionAdapterResponseHeaders(headers) {
  delete headers["access-control-allow-credentials"];
  delete headers["access-control-allow-origin"];
  delete headers["set-cookie"];
  delete headers["www-authenticate"];
  headers["cross-origin-resource-policy"] = "same-origin";
  headers["x-content-type-options"] = "nosniff";
  return headers;
}

function groundedVisionResponseHeaders(headers, suffix) {
  const parsed = new URL(suffix, "http://home.local");
  stripVisionAdapterResponseHeaders(headers);
  if (GROUNDED_VISION_METADATA_PATHS.has(parsed.pathname)) {
    headers["content-type"] = "application/json; charset=utf-8";
    headers["cache-control"] = "no-store";
  } else {
    headers["content-type"] = "image/jpeg";
    // The sidecar rewrites these camera-scoped filenames after every look.
    // The app adds a unique numeric cache-buster, making that URL safe to
    // retain briefly without allowing a stale image to cross turns.
    headers["cache-control"] = parsed.searchParams.has("cb")
      ? "private, max-age=60, immutable"
      : "private, no-cache";
  }
  return headers;
}

function isVisionCompatibilityRoute(suffix, method) {
  const parsed = new URL(suffix, "http://home.local");
  if (method === "GET" || method === "HEAD") {
    if (/^(?:\/healthz|\/cameras|\/describe\/latest|\/reason\/latest)$/i.test(parsed.pathname)) {
      return !parsed.search;
    }
    return GROUNDED_VISION_IMAGE_PATHS.some((pattern) => pattern.test(parsed.pathname)) &&
      hasOnlyCacheBuster(parsed);
  }
  if (method === "POST") {
    return !parsed.search && /^\/(?:describe|describe_clip|reason|reason_zoom)$/.test(parsed.pathname);
  }
  return false;
}

const routes = [
  {
    prefix: "/proxy/ha",
    env: "HOME_WEB_HA_TARGET",
    target: envTarget("HOME_WEB_HA_TARGET", "http://192.168.0.125:8123"),
    allow: rx(/^\/api\/(websocket$|states(?:\/|$)|services\/|conversation\/process$|camera_proxy|camera_proxy_stream|tts_proxy\/|extended_openai_conversation\/)/),
    ws: true,
  },
  {
    prefix: "/proxy/metrics",
    env: "HOME_WEB_METRICS_TARGET",
    target: envTarget("HOME_WEB_METRICS_TARGET", "http://192.168.0.100:8092"),
    allow: rx(/^\/(healthz|metrics|conversations\/(stream|recent|event)|traces?\/|traces$)/),
    ws: false,
  },
  {
    prefix: "/proxy/vllm",
    env: "HOME_WEB_VLLM_TARGET",
    target: envTarget("HOME_WEB_VLLM_TARGET", "http://192.168.0.100:8000"),
    allow: rx(/^\/(health|healthz|v1\/(models|chat\/completions|completions))/),
    ws: false,
  },
  {
    prefix: "/proxy/vision-results",
    env: "HOME_WEB_VISION_TARGET",
    target: envTarget("HOME_WEB_VISION_TARGET", "http://192.168.0.100:8091"),
    allow: isGroundedVisionResultRoute,
    transformResponseHeaders: groundedVisionResponseHeaders,
    ws: false,
  },
  {
    prefix: "/proxy/vision",
    env: "HOME_WEB_VISION_TARGET",
    target: envTarget("HOME_WEB_VISION_TARGET", "http://192.168.0.100:8091"),
    allow: isVisionCompatibilityRoute,
    transformResponseHeaders: stripVisionAdapterResponseHeaders,
    ws: false,
  },
  {
    prefix: "/proxy/intelligence",
    env: "HOME_WEB_INTELLIGENCE_TARGET",
    target: envTarget("HOME_WEB_INTELLIGENCE_TARGET", "http://192.168.0.100:8095"),
    allow: rx(/^\/(healthz|api\/|lighting|memory|episodes|decisions|experiments|proposals|readiness)/),
    ws: false,
  },
  {
    prefix: "/proxy/supervisor",
    env: "HOME_WEB_SUPERVISOR_TARGET",
    target: envTarget("HOME_WEB_SUPERVISOR_TARGET", "http://home-app.taild52a15.ts.net:8093"),
    allow: rx(/^\/(healthz|api\/(stack|services)\/)/),
    ws: false,
  },
  {
    prefix: "/proxy/bridge",
    env: "HOME_WEB_S2S_TARGET",
    target: envTarget("HOME_WEB_S2S_TARGET", "http://192.168.0.100:8094"),
    allow: rx(/^\/(healthz|rooms|s2s)/),
    ws: true,
  },
  {
    prefix: "/proxy/tracker",
    env: "HOME_WEB_TRACKER_TARGET",
    target: envTarget("HOME_WEB_TRACKER_TARGET", "http://192.168.0.100:8098"),
    allow: rx(/^\/(healthz|ws\/tracks|tracks|calib\/|apartment_model|model|seed-model|frame)/),
    ws: true,
  },
  {
    prefix: "/proxy/video-labeler",
    env: "HOME_WEB_VIDEO_LABELER_TARGET",
    target: envTarget("HOME_WEB_VIDEO_LABELER_TARGET", "http://192.168.0.100:8099"),
    allow: rx(/^\/(healthz|api\/video-labeler\/)/),
    ws: false,
  },
  {
    prefix: "/proxy/frigate",
    env: "HOME_WEB_FRIGATE_TARGET",
    target: envTarget("HOME_WEB_FRIGATE_TARGET", "http://192.168.0.125:5000"),
    allow: rx(/^\/(api\/|clips\/faces\/)/),
    ws: false,
  },
];

function routeSummary() {
  return routes.map(({ prefix, env, target, ws }) => {
    let host = target;
    try {
      const parsed = new URL(target);
      host = `${parsed.protocol}//${parsed.host}`;
    } catch {
      host = target;
    }
    return { prefix, env, target: host, ws: !!ws };
  });
}

function gatewayHealth() {
  return {
    ok: true,
    service: "home-web-gateway",
    version: BUILD_VERSION,
    commit: BUILD_COMMIT,
    assetVersion: BUILD_ASSET_VERSION,
    host: HOST,
    port: PORT,
    auth: {
      enabled: authState.enabled,
      source: authState.source,
      required: AUTH_REQUIRED,
    },
    stackTokenProxy: {
      enabled: stackTokenProxy.enabled,
      source: stackTokenProxy.source,
    },
    apartmentAssetsDir: APARTMENT_ASSETS_DIR,
    routes: routeSummary(),
    ts: new Date().toISOString(),
  };
}

function parseCookies(header) {
  const out = {};
  for (const part of String(header || "").split(";")) {
    const idx = part.indexOf("=");
    if (idx < 0) continue;
    out[part.slice(0, idx).trim()] = decodeURIComponent(part.slice(idx + 1).trim());
  }
  return out;
}

function timingSafeStringEqual(a, b) {
  const got = Buffer.from(String(a || ""));
  const expected = Buffer.from(String(b || ""));
  return got.length === expected.length && crypto.timingSafeEqual(got, expected);
}

function parseBasicPair(value) {
  const raw = String(value || "");
  const idx = raw.indexOf(":");
  if (idx <= 0) return null;
  const username = raw.slice(0, idx).trim();
  const password = raw.slice(idx + 1);
  if (!username || !password) return null;
  return { username, password };
}

function hashPassword(password, salt = crypto.randomBytes(18).toString("base64url"), iterations = 210000) {
  const hash = crypto.pbkdf2Sync(String(password), salt, iterations, 32, "sha256").toString("base64url");
  return { kdf: "pbkdf2-sha256", iterations, salt, hash };
}

function loadAuthState() {
  if (!AUTH_REQUIRED) {
    return { enabled: false, source: "tailscale", username: "" };
  }
  try {
    if (fs.existsSync(AUTH_FILE)) {
      const data = JSON.parse(fs.readFileSync(AUTH_FILE, "utf8"));
      if (data?.username && data?.password?.salt && data?.password?.hash) {
        return {
          enabled: true,
          source: "file",
          username: String(data.username),
          password: data.password,
        };
      }
    }
  } catch (err) {
    console.warn(`[auth] ignoring unreadable auth file ${AUTH_FILE}: ${err.message}`);
  }
  const legacy = parseBasicPair(LEGACY_BASIC_AUTH);
  if (legacy) {
    return { enabled: true, source: "env", ...legacy };
  }
  console.warn("[auth] HOME_WEB_AUTH_REQUIRED=1 but no valid auth file or HOME_WEB_BASIC_AUTH value was found; gateway auth is disabled");
  return { enabled: false, source: "disabled", username: "" };
}

let authState = loadAuthState();

function verifyCredentials(username, password) {
  if (!authState.enabled) return true;
  if (!timingSafeStringEqual(username, authState.username)) return false;
  if (authState.source === "env") return timingSafeStringEqual(password, authState.password);
  const p = authState.password;
  const candidate = hashPassword(password, p.salt, p.iterations || 210000).hash;
  return timingSafeStringEqual(candidate, p.hash);
}

function authCookieValue() {
  if (!authState.enabled) return "";
  const fingerprint = authState.source === "env"
    ? `${authState.username}:${authState.password}`
    : `${authState.username}:${authState.password.salt}:${authState.password.hash}`;
  return crypto.createHash("sha256").update(`home-web-session:v2:${fingerprint}`).digest("base64url");
}

function setAuthCookie(res) {
  res.setHeader("Set-Cookie", `${AUTH_COOKIE}=${encodeURIComponent(authCookieValue())}; HttpOnly; SameSite=Lax; Path=/`);
}

function clearAuthCookie(res) {
  res.setHeader("Set-Cookie", `${AUTH_COOKIE}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0`);
}

function writePassword(username, password) {
  const record = {
    version: 1,
    username,
    password: hashPassword(password),
    updatedAt: new Date().toISOString(),
  };
  fs.mkdirSync(path.dirname(AUTH_FILE), { recursive: true });
  fs.writeFileSync(AUTH_FILE, JSON.stringify(record, null, 2), { encoding: "utf8", mode: 0o600 });
  authState = loadAuthState();
}

function hasValidAuth(req) {
  if (!authState.enabled) return { ok: true, setCookie: false };
  const cookies = parseCookies(req.headers.cookie);
  if (cookies[AUTH_COOKIE] === authCookieValue()) return { ok: true, setCookie: false };

  const auth = String(req.headers.authorization || "");
  if (!auth.startsWith("Basic ")) return { ok: false, setCookie: false };
  let decoded = "";
  try {
    decoded = Buffer.from(auth.slice("Basic ".length), "base64").toString("utf8");
  } catch {
    return { ok: false, setCookie: false };
  }
  const pair = parseBasicPair(decoded);
  const ok = !!pair && verifyCredentials(pair.username, pair.password);
  return { ok, setCookie: ok };
}

function requireAuth(req, res) {
  const auth = hasValidAuth(req);
  if (auth.ok) {
    if (auth.setCookie && res) setAuthCookie(res);
    return true;
  }
  if (res) {
    sendUnauthorized(req, res);
  }
  return false;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function sendJson(res, status, body, headers = {}) {
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    ...headers,
  });
  res.end(JSON.stringify(body));
}

function wantsHtml(req) {
  return String(req.headers.accept || "").includes("text/html");
}

function sendUnauthorized(req, res) {
  const parsed = new URL(req.url, "http://home.local");
  const pageRequest = req.method === "GET" && !parsed.pathname.startsWith("/proxy/") && parsed.pathname !== "/healthz";
  if (pageRequest || (req.method === "GET" && wantsHtml(req))) {
    sendAuthPage(req, res, { mode: "login", status: 401 });
    return;
  }
  sendJson(res, 401, { ok: false, error: "authentication_required" });
}

function readRequestJson(req, maxBytes = 64 * 1024) {
  return new Promise((resolve, reject) => {
    let raw = "";
    req.setEncoding("utf8");
    req.on("data", (chunk) => {
      raw += chunk;
      if (raw.length > maxBytes) {
        reject(new Error("request body too large"));
        req.destroy();
      }
    });
    req.on("end", () => {
      if (!raw.trim()) {
        resolve({});
        return;
      }
      try {
        if (String(req.headers["content-type"] || "").includes("application/x-www-form-urlencoded")) {
          resolve(Object.fromEntries(new URLSearchParams(raw)));
        } else {
          resolve(JSON.parse(raw));
        }
      } catch {
        reject(new Error("invalid request body"));
      }
    });
    req.on("error", reject);
  });
}

function sendAuthPage(req, res, { mode = "login", status = 200 } = {}) {
  const authed = hasValidAuth(req).ok;
  const activeMode = authed && mode !== "login" ? "account" : "login";
  const username = authState.username || "marcelo";
  const html = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Home Access</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #050607;
      --panel: #101215;
      --panel-2: #15191e;
      --line: #2a3037;
      --text: #f4f7fb;
      --muted: #89919b;
      --accent: #d59622;
      --danger: #f06a61;
    }
    * { box-sizing: border-box; }
    html, body { min-height: 100%; margin: 0; }
    body {
      display: grid;
      place-items: center;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif;
    }
    main {
      width: min(430px, calc(100vw - 36px));
      border: 1px solid var(--line);
      background: var(--panel);
      padding: 28px;
    }
    .brand { margin-bottom: 28px; }
    .brand h1 { margin: 0; font-size: 24px; font-weight: 650; letter-spacing: 0; }
    .brand p {
      margin: 3px 0 0;
      color: var(--muted);
      font: 10px/1.4 "Geist Mono", "SFMono-Regular", Consolas, monospace;
      letter-spacing: .22em;
      text-transform: lowercase;
    }
    .tabs { display: flex; gap: 8px; margin-bottom: 20px; }
    .tab {
      border: 1px solid var(--line);
      background: var(--panel-2);
      color: var(--muted);
      padding: 8px 11px;
      font: 11px/1 "Geist Mono", "SFMono-Regular", Consolas, monospace;
      letter-spacing: .12em;
      text-transform: lowercase;
    }
    .tab.active { color: #0d0f12; border-color: var(--accent); background: var(--accent); }
    label {
      display: block;
      margin: 15px 0 6px;
      color: var(--muted);
      font: 10px/1 "Geist Mono", "SFMono-Regular", Consolas, monospace;
      letter-spacing: .18em;
      text-transform: lowercase;
    }
    input {
      width: 100%;
      border: 0;
      border-bottom: 1px solid var(--line);
      border-radius: 0;
      background: transparent;
      color: var(--text);
      padding: 10px 0;
      outline: none;
      font: inherit;
    }
    input:focus { border-color: var(--accent); }
    button {
      width: 100%;
      margin-top: 22px;
      border: 1px solid var(--accent);
      background: var(--accent);
      color: #0d0f12;
      padding: 11px 14px;
      cursor: pointer;
      font: 11px/1 "Geist Mono", "SFMono-Regular", Consolas, monospace;
      letter-spacing: .14em;
      text-transform: lowercase;
    }
    button.secondary {
      border-color: var(--line);
      background: transparent;
      color: var(--muted);
    }
    .msg { min-height: 20px; margin-top: 14px; color: var(--muted); font-size: 12px; }
    .msg.error { color: var(--danger); }
    .row { display: flex; gap: 10px; }
    .row button { flex: 1; }
    .hidden { display: none; }
    a { color: var(--accent); text-decoration: none; }
  </style>
</head>
<body>
  <main>
    <div class="brand">
      <h1>home</h1>
      <p>engineered lighting</p>
    </div>

    <section id="loginPanel" class="${activeMode === "login" ? "" : "hidden"}">
      <div class="tabs"><div class="tab active">sign in</div></div>
      <form id="loginForm">
        <label for="username">username</label>
        <input id="username" name="username" autocomplete="username" value="${escapeHtml(username)}" required />
        <label for="password">password</label>
        <input id="password" name="password" type="password" autocomplete="current-password" required autofocus />
        <button type="submit">enter home</button>
      </form>
      <div id="loginMsg" class="msg"></div>
    </section>

    <section id="accountPanel" class="${activeMode === "account" ? "" : "hidden"}">
      <div class="tabs"><div class="tab active">account</div></div>
      <form id="changeForm">
        <label>username</label>
        <input value="${escapeHtml(username)}" disabled />
        <label for="currentPassword">current password</label>
        <input id="currentPassword" name="currentPassword" type="password" autocomplete="current-password" required />
        <label for="newPassword">new password</label>
        <input id="newPassword" name="newPassword" type="password" autocomplete="new-password" minlength="${PASSWORD_MIN_LENGTH}" required />
        <label for="confirmPassword">confirm password</label>
        <input id="confirmPassword" name="confirmPassword" type="password" autocomplete="new-password" minlength="${PASSWORD_MIN_LENGTH}" required />
        <button type="submit">change password</button>
      </form>
      <div class="row">
        <button id="openHome" class="secondary" type="button">open home</button>
        <button id="logout" class="secondary" type="button">sign out</button>
      </div>
      <div id="changeMsg" class="msg"></div>
    </section>
  </main>
  <script>
    const loginForm = document.getElementById("loginForm");
    const changeForm = document.getElementById("changeForm");
    const loginMsg = document.getElementById("loginMsg");
    const changeMsg = document.getElementById("changeMsg");

    function setMsg(el, text, error) {
      el.textContent = text || "";
      el.className = "msg" + (error ? " error" : "");
    }

    async function postJson(url, body) {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      let data = {};
      try { data = await res.json(); } catch {}
      if (!res.ok || data.ok === false) throw new Error(data.error || "request failed");
      return data;
    }

    loginForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      setMsg(loginMsg, "checking...", false);
      try {
        await postJson("/auth/login", {
          username: loginForm.username.value,
          password: loginForm.password.value,
        });
        location.href = "/";
      } catch (err) {
        setMsg(loginMsg, "that username or password did not work", true);
      }
    });

    changeForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const next = changeForm.newPassword.value;
      if (next !== changeForm.confirmPassword.value) {
        setMsg(changeMsg, "new passwords do not match", true);
        return;
      }
      setMsg(changeMsg, "saving...", false);
      try {
        await postJson("/auth/change-password", {
          currentPassword: changeForm.currentPassword.value,
          newPassword: next,
        });
        changeForm.reset();
        setMsg(changeMsg, "password updated", false);
      } catch (err) {
        setMsg(changeMsg, err.message === "password_too_short" ? "use at least ${PASSWORD_MIN_LENGTH} characters" : "could not change password", true);
      }
    });

    document.getElementById("openHome")?.addEventListener("click", () => { location.href = "/"; });
    document.getElementById("logout")?.addEventListener("click", async () => {
      await postJson("/auth/logout", {});
      location.href = "/auth";
    });
  </script>
</body>
</html>`;
  res.writeHead(status, {
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "no-store",
  });
  res.end(html);
}

async function handleAuthRoute(req, res, parsed) {
  if (parsed.pathname === "/auth" || parsed.pathname === "/auth/") {
    sendAuthPage(req, res, { mode: hasValidAuth(req).ok ? "account" : "login" });
    return;
  }

  if (parsed.pathname === "/auth/status" && req.method === "GET") {
    sendJson(res, 200, {
      ok: true,
      authEnabled: authState.enabled,
      authenticated: hasValidAuth(req).ok,
      username: authState.username || null,
    });
    return;
  }

  if (parsed.pathname === "/auth/login" && req.method === "POST") {
    if (!authState.enabled) {
      setAuthCookie(res);
      sendJson(res, 200, { ok: true });
      return;
    }
    let body;
    try {
      body = await readRequestJson(req);
    } catch {
      sendJson(res, 400, { ok: false, error: "invalid_request" });
      return;
    }
    if (!verifyCredentials(body.username, body.password)) {
      sendJson(res, 401, { ok: false, error: "invalid_credentials" });
      return;
    }
    setAuthCookie(res);
    sendJson(res, 200, { ok: true, username: authState.username });
    return;
  }

  if (parsed.pathname === "/auth/logout" && req.method === "POST") {
    clearAuthCookie(res);
    sendJson(res, 200, { ok: true });
    return;
  }

  if (parsed.pathname === "/auth/change-password" && req.method === "POST") {
    if (!hasValidAuth(req).ok) {
      sendJson(res, 401, { ok: false, error: "authentication_required" });
      return;
    }
    let body;
    try {
      body = await readRequestJson(req);
    } catch {
      sendJson(res, 400, { ok: false, error: "invalid_request" });
      return;
    }
    const next = String(body.newPassword || "");
    if (next.length < PASSWORD_MIN_LENGTH) {
      sendJson(res, 400, { ok: false, error: "password_too_short" });
      return;
    }
    if (!verifyCredentials(authState.username, body.currentPassword)) {
      sendJson(res, 401, { ok: false, error: "invalid_current_password" });
      return;
    }
    try {
      writePassword(authState.username, next);
    } catch (err) {
      sendJson(res, 500, { ok: false, error: "password_write_failed", detail: err.message });
      return;
    }
    setAuthCookie(res);
    sendJson(res, 200, { ok: true, username: authState.username });
    return;
  }

  sendJson(res, 404, { ok: false, error: "not_found" });
}

function cleanHeaders(reqHeaders, target) {
  const headers = { ...reqHeaders };
  headers.host = new URL(target).host;
  if (String(headers.authorization || "").startsWith("Basic ")) delete headers.authorization;
  delete headers.cookie;
  delete headers["proxy-authorization"];
  return headers;
}

function isSupervisorStackApi(route, suffix) {
  if (route.prefix !== "/proxy/supervisor") return false;
  const parsed = new URL(suffix, "http://home.local");
  return /^\/api\/(stack|services)\//.test(parsed.pathname);
}

function proxyHeaders(reqHeaders, route, suffix) {
  const headers = cleanHeaders(reqHeaders, route.target);
  if (route.prefix === "/proxy/vision-results" || route.prefix === "/proxy/vision") {
    // Vision routes authenticate at the gateway boundary. They never forward
    // a caller credential (including an unrelated browser Bearer token) to
    // the untrusted vision adapter.
    delete headers.authorization;
  }
  if (route.prefix === "/proxy/supervisor") {
    delete headers.authorization;
    if (isSupervisorStackApi(route, suffix) && stackTokenProxy.enabled) {
      headers.authorization = `Bearer ${stackTokenProxy.token}`;
    }
  }
  return headers;
}

function findRoute(urlPath) {
  return routes.find((route) => urlPath === route.prefix || urlPath.startsWith(route.prefix + "/"));
}

function routeSuffix(route, reqUrl) {
  const parsed = new URL(reqUrl, "http://home.local");
  const suffixPath = parsed.pathname.slice(route.prefix.length) || "/";
  return `${suffixPath}${parsed.search}`;
}

function buildTargetUrl(route, suffix) {
  const base = new URL(route.target);
  const parsed = new URL(suffix, base);
  const basePath = base.pathname.replace(/\/+$/, "");
  const suffixPath = parsed.pathname.startsWith("/") ? parsed.pathname : `/${parsed.pathname}`;
  parsed.pathname = `${basePath}${suffixPath}`;
  return parsed;
}

function safeFile(root, urlPath) {
  let decoded;
  try {
    decoded = decodeURIComponent(urlPath.split("?")[0]);
  } catch {
    return null;
  }
  const normalized = path.normalize(decoded).replace(/^(\.\.[/\\])+/, "");
  const full = path.resolve(root, normalized.replace(/^[/\\]+/, ""));
  return full.startsWith(path.resolve(root)) ? full : null;
}

function fileEtag(stat) {
  return `"${stat.size.toString(16)}-${Math.floor(stat.mtimeMs).toString(16)}"`;
}

function normalizeEtag(value) {
  return String(value || "").trim().replace(/^W\//, "").replace(/^"|"$/g, "");
}

function clientHasFreshCopy(req, stat, etag) {
  const inm = String(req.headers["if-none-match"] || "");
  if (inm && inm.split(",").some((v) => normalizeEtag(v) === normalizeEtag(etag))) return true;
  const ims = req.headers["if-modified-since"];
  if (!ims) return false;
  const since = Date.parse(String(ims));
  return Number.isFinite(since) && Math.floor(stat.mtimeMs / 1000) <= Math.floor(since / 1000);
}

function serveFile(req, res, filePath, { cache = "no-store" } = {}) {
  fs.stat(filePath, (err, stat) => {
    if (err || !stat.isFile()) {
      res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("not found");
      return;
    }
    const type = MIME.get(path.extname(filePath).toLowerCase()) || "application/octet-stream";
    const etag = fileEtag(stat);
    const headers = {
      "Content-Type": type,
      "Content-Length": stat.size,
      "Cache-Control": cache,
      "ETag": etag,
      "Last-Modified": stat.mtime.toUTCString(),
    };
    if (clientHasFreshCopy(req, stat, etag)) {
      res.writeHead(304, {
        "Cache-Control": cache,
        "ETag": etag,
        "Last-Modified": stat.mtime.toUTCString(),
      });
      res.end();
      return;
    }
    res.writeHead(200, {
      ...headers,
    });
    if (req.method === "HEAD") {
      res.end();
      return;
    }
    fs.createReadStream(filePath).pipe(res);
  });
}

function jsString(value) {
  return JSON.stringify(String(value || ""));
}

function sendIndex(req, res, filePath) {
  fs.readFile(filePath, "utf8", (err, html) => {
    if (err) {
      res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("not found");
      return;
    }
    const stackProxyRuntime = stackTokenProxy.enabled
      ? `window.__HOME_WEB_STACK_TOKEN_PROXY=true;window.__STACK_TOKEN=${jsString(STACK_TOKEN_PROXY_MARKER)};`
      : "window.__HOME_WEB_STACK_TOKEN_PROXY=false;";
    const runtime = `<script>window.__HOME_BUILD_VERSION=${jsString(BUILD_VERSION)};window.__HOME_BUILD_COMMIT=${jsString(BUILD_COMMIT)};window.__HOME_ASSET_VERSION=${jsString(BUILD_ASSET_VERSION)};${stackProxyRuntime}</script>`;
    const body = html.includes("</head>") ? html.replace("</head>", `${runtime}\n</head>`) : `${runtime}\n${html}`;
    const etag = `"index-${Buffer.byteLength(body).toString(16)}-${BUILD_ASSET_VERSION}"`;
    res.writeHead(200, {
      "Content-Type": "text/html; charset=utf-8",
      "Content-Length": Buffer.byteLength(body),
      "Cache-Control": "no-store",
      "ETag": etag,
    });
    if (req.method === "HEAD") res.end();
    else res.end(body);
  });
}

function isApartmentMetadata(pathname) {
  return /\.(?:json|webmanifest)$/i.test(pathname);
}

function isApartmentHeavy(pathname) {
  return /\.(?:ply|spz|glb|wasm|jpg|jpeg|png|webp)$/i.test(pathname);
}

function appStaticCache(pathname, parsed) {
  if (pathname === "/index.html") return "no-store";
  if (pathname === "/home-service-worker.js") return STATIC_REVALIDATE_CACHE;
  if (pathname === "/site.webmanifest" || pathname.endsWith(".webmanifest")) return STATIC_REVALIDATE_CACHE;
  if (pathname.startsWith("/vendor/")) return STATIC_VERSIONED_CACHE;
  if (parsed.searchParams.has("v")) return STATIC_VERSIONED_CACHE;
  return STATIC_REVALIDATE_CACHE;
}

// Watchdog/liveness endpoint. Answered before auth so an external monitor can
// poll it on a locked-down (auth-required) gateway, and handled for both GET
// and HEAD. Logs at debug because the web-app watchdog hits it every ~5 min.
function handleHealthz(req, res) {
  console.debug(`[healthz] ${req.method} ${req.url}`);
  if (req.method === "HEAD") {
    res.writeHead(200, { "Cache-Control": "no-store" });
    res.end();
    return;
  }
  if (req.method !== "GET") {
    res.writeHead(405, { "Cache-Control": "no-store", Allow: "GET, HEAD" });
    res.end();
    return;
  }
  // Superset payload: the spec-required { status, commit, uptime } plus the
  // existing gatewayHealth() fields (routes, auth, stackTokenProxy) that the
  // readiness probe in run-web-gateway-stack-token-tests.js relies on. `commit`
  // is pinned to the asset version per spec, overriding gatewayHealth's
  // git-only commit.
  const body = {
    ...gatewayHealth(),
    status: "ok",
    commit: BUILD_ASSET_VERSION,
    uptime: process.uptime(),
  };
  sendJson(res, 200, body, { "Cache-Control": "no-store" });
}

function handleResetCache(req, res) {
  if (req.method === "HEAD") {
    res.writeHead(200, {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
    });
    res.end();
    return;
  }
  if (req.method !== "GET") {
    res.writeHead(405, { "Cache-Control": "no-store", Allow: "GET, HEAD" });
    res.end();
    return;
  }

  const freshTarget = `/?fresh=${encodeURIComponent(BUILD_ASSET_VERSION)}`;
  const body = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Resetting Home Cache</title>
  <style>
    :root { color-scheme: dark; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #050606;
      color: #f4f7f8;
      font: 15px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    main { width: min(30rem, calc(100vw - 3rem)); }
    p { color: #aeb8bd; }
    a { color: #9dccff; }
  </style>
</head>
<body>
  <main>
    <h1>resetting home cache</h1>
    <p id="status">clearing service worker and cached app assets...</p>
    <p><a href="${freshTarget}">open home</a></p>
  </main>
  <script>
    (async function () {
      const status = document.getElementById("status");
      try {
        if ("serviceWorker" in navigator && navigator.serviceWorker.getRegistrations) {
          const regs = await navigator.serviceWorker.getRegistrations();
          await Promise.all(regs.map((reg) => reg.unregister()));
        }
        if ("caches" in window && caches.keys) {
          const names = await caches.keys();
          await Promise.all(names
            .filter((name) => /^home-web-static-/.test(name))
            .map((name) => caches.delete(name)));
        }
        status.textContent = "cache cleared. loading the current build...";
      } catch (err) {
        status.textContent = "cache reset had a problem, trying a fresh load anyway...";
      }
      window.location.replace("${freshTarget}");
    })();
  </script>
</body>
</html>`;
  res.writeHead(200, {
    "Content-Type": "text/html; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
    "Cache-Control": "no-store",
  });
  res.end(body);
}

function serveStatic(req, res) {
  const parsed = new URL(req.url, "http://home.local");
  if (parsed.pathname.startsWith("/assets/apartment/")) {
    const rel = parsed.pathname.slice("/assets/apartment/".length);
    const cache = isApartmentMetadata(parsed.pathname)
      ? APARTMENT_METADATA_CACHE
      : isApartmentHeavy(parsed.pathname)
        ? APARTMENT_HEAVY_CACHE
        : STATIC_REVALIDATE_CACHE;
    const configured = safeFile(APARTMENT_ASSETS_DIR, rel);
    if (configured && fs.existsSync(configured)) {
      serveFile(req, res, configured, { cache });
      return;
    }
    const fallback = safeFile(FALLBACK_APARTMENT_DIR, rel);
    if (fallback) {
      serveFile(req, res, fallback, { cache });
      return;
    }
  }

  const pathname = parsed.pathname === "/" ? "/index.html" : parsed.pathname;
  const filePath = safeFile(APP_DIR, pathname);
  if (!filePath) {
    res.writeHead(400, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("bad path");
    return;
  }
  if (pathname === "/index.html") {
    sendIndex(req, res, filePath);
    return;
  }
  serveFile(req, res, filePath, { cache: appStaticCache(pathname, parsed) });
}

function proxyHttp(req, res, route) {
  const suffix = routeSuffix(route, req.url);
  if (!route.allow(suffix, req.method)) {
    res.writeHead(403, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("proxy path not allowed");
    return;
  }

  const targetUrl = buildTargetUrl(route, suffix);
  const client = targetUrl.protocol === "https:" ? https : http;
  const upstream = client.request(targetUrl, {
    method: req.method,
    headers: proxyHeaders(req.headers, route, suffix),
  }, (upstreamRes) => {
    let headers = { ...upstreamRes.headers };
    delete headers["content-security-policy"];
    if (route.transformResponseHeaders) {
      headers = route.transformResponseHeaders(headers, suffix, upstreamRes) || headers;
    }
    if (route.prefix === "/proxy/frigate" && /^\/clips\/faces\//.test(suffix.split("?")[0])) {
      const ext = path.extname(targetUrl.pathname).toLowerCase();
      headers["content-type"] = MIME.get(ext) || headers["content-type"] || "application/octet-stream";
      headers["cache-control"] = headers["cache-control"] || "private, max-age=3600";
    }
    res.writeHead(upstreamRes.statusCode || 502, headers);
    upstreamRes.pipe(res);
  });

  upstream.on("error", (err) => {
    res.writeHead(502, { "Content-Type": "application/json; charset=utf-8" });
    res.end(JSON.stringify({ ok: false, error: err.message }));
  });

  req.pipe(upstream);
}

function proxyUpgrade(req, socket, head, route) {
  if (!route.ws) {
    socket.write("HTTP/1.1 403 Forbidden\r\n\r\n");
    socket.destroy();
    return;
  }
  const suffix = routeSuffix(route, req.url);
  if (!route.allow(suffix, req.method)) {
    socket.write("HTTP/1.1 403 Forbidden\r\n\r\n");
    socket.destroy();
    return;
  }
  const targetUrl = buildTargetUrl(route, suffix);
  if (targetUrl.protocol === "https:") {
    socket.write("HTTP/1.1 501 Not Implemented\r\n\r\nTLS websocket upstreams are not supported by this gateway");
    socket.destroy();
    return;
  }

  const upstream = net.connect(targetUrl.port || 80, targetUrl.hostname, () => {
    const headers = proxyHeaders(req.headers, route, suffix);
    let request = `${req.method} ${targetUrl.pathname}${targetUrl.search} HTTP/${req.httpVersion}\r\n`;
    for (const [key, value] of Object.entries(headers)) {
      if (Array.isArray(value)) {
        for (const item of value) request += `${key}: ${item}\r\n`;
      } else if (value != null) {
        request += `${key}: ${value}\r\n`;
      }
    }
    request += "\r\n";
    upstream.write(request);
    if (head?.length) upstream.write(head);
    upstream.pipe(socket);
    socket.pipe(upstream);
  });
  upstream.on("error", () => {
    socket.write("HTTP/1.1 502 Bad Gateway\r\n\r\n");
    socket.destroy();
  });
}

function checkConfig() {
  const summary = {
    service: "home-web-gateway",
    version: BUILD_VERSION,
    commit: BUILD_COMMIT,
    host: HOST,
    port: PORT,
    appDir: APP_DIR,
    apartmentAssetsDir: APARTMENT_ASSETS_DIR,
    authEnabled: authState.enabled,
    authSource: authState.source,
    authRequired: AUTH_REQUIRED,
    authFile: AUTH_FILE,
    stackTokenProxyEnabled: stackTokenProxy.enabled,
    stackTokenProxySource: stackTokenProxy.source,
    routes: routeSummary(),
  };
  console.log(JSON.stringify(summary, null, 2));
}

if (process.argv.includes("--check")) {
  checkConfig();
  process.exit(0);
}

const server = http.createServer(async (req, res) => {
  const parsed = new URL(req.url, "http://home.local");
  if (parsed.pathname === "/healthz") {
    handleHealthz(req, res);
    return;
  }
  if (parsed.pathname === "/reset-cache") {
    handleResetCache(req, res);
    return;
  }
  if (parsed.pathname === "/auth" || parsed.pathname.startsWith("/auth/")) {
    await handleAuthRoute(req, res, parsed);
    return;
  }

  if (!requireAuth(req, res)) return;
  const route = findRoute(parsed.pathname);
  if (route) proxyHttp(req, res, route);
  else serveStatic(req, res);
});

server.on("upgrade", (req, socket, head) => {
  if (!hasValidAuth(req).ok) {
    socket.write(`HTTP/1.1 401 Unauthorized\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nauthentication required`);
    socket.destroy();
    return;
  }
  const parsed = new URL(req.url, "http://home.local");
  const route = findRoute(parsed.pathname);
  if (!route) {
    socket.write("HTTP/1.1 404 Not Found\r\n\r\n");
    socket.destroy();
    return;
  }
  proxyUpgrade(req, socket, head, route);
});

server.listen(PORT, HOST, () => {
  console.log(`Home web gateway listening on http://${HOST}:${PORT}`);
  console.log("Use Tailscale Serve to expose this privately to your tailnet.");
});
