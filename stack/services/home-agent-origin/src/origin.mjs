import fs from "node:fs";
import http from "node:http";
import path from "node:path";

const MAX_REQUEST_BYTES = 64 * 1024;
const UPSTREAM_TIMEOUT_MS = 10_000;
const COOKIE_NAMES = new Set(["__Host-home_agent", "__Host-home_agent_oauth"]);
const STATIC_ASSETS = new Map([
  ["/home-agent/", ["index.html", "text/html; charset=utf-8", "no-store"]],
  ["/home-agent/index.html", ["index.html", "text/html; charset=utf-8", "no-store"]],
  ["/home-agent/api.js", ["api.js", "text/javascript; charset=utf-8", "private, no-cache"]],
  ["/home-agent/panel.js", ["panel.js", "text/javascript; charset=utf-8", "private, no-cache"]],
  ["/home-agent/panel.css", ["panel.css", "text/css; charset=utf-8", "private, no-cache"]],
]);
const UUID_PATH = "[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}";
const BROWSER_API_ROUTES = Object.freeze([
  ["POST", /^\/api\/agent\/auth\/start$/],
  ["GET", /^\/api\/agent\/auth\/callback$/],
  ["GET", /^\/api\/agent\/auth\/session$/],
  ["POST", /^\/api\/agent\/auth\/logout$/],
  ["GET", /^\/api\/agent\/v1\/onboarding\/status$/],
  ["GET", /^\/api\/agent\/v1\/principal-binding-proposal$/],
  ["POST", /^\/api\/agent\/v1\/principal-binding-request$/],
  ["POST", /^\/api\/agent\/v1\/principal-binding-request\/cancel$/],
  ["POST", /^\/api\/agent\/v1\/principal-binding-proposal\/confirm$/],
  ["GET", /^\/api\/agent\/v1\/parent-relationship-proposal$/],
  ["POST", /^\/api\/agent\/v1\/parent-relationship-proposal$/],
  ["POST", /^\/api\/agent\/v1\/parent-relationship-proposal\/confirm$/],
  ["GET", /^\/api\/agent\/v1\/people-directory$/],
  ["GET", /^\/api\/agent\/v1\/relationships$/],
  ["GET", /^\/api\/agent\/v1\/snapshot$/],
  ["POST", /^\/api\/agent\/v1\/memory-transactions$/],
  ["GET", new RegExp(`^/api/agent/v1/memory-transactions/${UUID_PATH}$`, "i")],
  ["POST", new RegExp(`^/api/agent/v1/memory-transactions/${UUID_PATH}/confirm$`, "i")],
  ["PUT", /^\/api\/agent\/v1\/preferences\/(location_memory|travel_greetings)$/],
  ["POST", new RegExp(`^/api/agent/v1/facts/${UUID_PATH}/correction-preview$`, "i")],
  ["POST", new RegExp(`^/api/agent/v1/descriptor-corrections/${UUID_PATH}/confirm$`, "i")],
  ["POST", new RegExp(`^/api/agent/v1/facts/${UUID_PATH}/retraction-preview$`, "i")],
  ["POST", new RegExp(`^/api/agent/v1/descriptor-retractions/${UUID_PATH}/confirm$`, "i")],
  ["POST", new RegExp(`^/api/agent/v1/facts/${UUID_PATH}/forget-preview$`, "i")],
  ["POST", /^\/api\/agent\/v1\/forget-preview$/],
  ["GET", new RegExp(`^/api/agent/v1/erasure-requests/${UUID_PATH}$`, "i")],
  ["POST", new RegExp(`^/api/agent/v1/erasure-requests/${UUID_PATH}/confirm$`, "i")],
]);

const STATIC_SECURITY_HEADERS = Object.freeze({
  "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; font-src 'self'; base-uri 'none'; form-action 'none'; frame-src 'none'; frame-ancestors 'none'; object-src 'none'",
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cross-Origin-Resource-Policy": "same-origin",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
  "Referrer-Policy": "no-referrer",
  "Strict-Transport-Security": "max-age=31536000",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
});

const API_SECURITY_HEADERS = Object.freeze({
  "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
  "Cache-Control": "no-store",
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cross-Origin-Resource-Policy": "same-origin",
  "Referrer-Policy": "no-referrer",
  "Strict-Transport-Security": "max-age=31536000",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
});

function exactHttpsOrigin(value) {
  try {
    const parsed = new URL(String(value || ""));
    if (
      parsed.protocol !== "https:" || parsed.username || parsed.password ||
      parsed.pathname !== "/" || parsed.search || parsed.hash ||
      String(value) !== parsed.origin
    ) return null;
    return parsed;
  } catch {
    return null;
  }
}

function exactBffRoot(value) {
  try {
    const parsed = new URL(String(value || ""));
    if (
      parsed.protocol !== "http:" || parsed.username || parsed.password ||
      parsed.pathname !== "/" || parsed.search || parsed.hash ||
      String(value) !== parsed.origin
    ) return null;
    return parsed;
  } catch {
    return null;
  }
}

function boundedPort(value, fallback) {
  const port = Number(value === undefined || value === "" ? fallback : value);
  return Number.isSafeInteger(port) && port >= 1 && port <= 65_535 ? port : null;
}

function configFromEnv(env = process.env) {
  const publicOrigin = exactHttpsOrigin(env.HOME_AGENT_WEB_PUBLIC_ORIGIN);
  const bff = exactBffRoot(env.HOME_AGENT_WEB_BFF_URL);
  const assetRoot = String(env.HOME_AGENT_WEB_ASSET_ROOT || "").trim();
  const bindHost = String(env.HOME_AGENT_WEB_HOST || "0.0.0.0").trim();
  const port = boundedPort(env.HOME_AGENT_WEB_PORT, 8096);
  const assetRootReady = Boolean(
    assetRoot && path.isAbsolute(assetRoot) &&
    [...STATIC_ASSETS.values()].every(([filename]) => {
      try {
        return fs.statSync(path.join(assetRoot, filename)).isFile();
      } catch {
        return false;
      }
    }),
  );
  return Object.freeze({
    ready: Boolean(
      publicOrigin && bff && assetRootReady && bindHost && port &&
      !/[\u0000-\u001f\u007f]/.test(bindHost)
    ),
    bindHost,
    port,
    publicOrigin: publicOrigin?.origin || null,
    expectedHost: publicOrigin?.host.toLowerCase() || null,
    bffOrigin: bff?.origin || null,
    assetRoot,
  });
}

function singleRawHeader(req, name) {
  const values = [];
  const target = name.toLowerCase();
  for (let index = 0; index < req.rawHeaders.length; index += 2) {
    if (String(req.rawHeaders[index]).toLowerCase() === target) {
      values.push(String(req.rawHeaders[index + 1] || ""));
    }
  }
  return values.length === 1 ? values[0] : values.length === 0 ? "" : null;
}

function safeRequestTarget(raw) {
  const value = String(raw || "");
  if (
    !value.startsWith("/") || value.startsWith("//") || value.includes("\\") ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) return null;
  try {
    const parsed = new URL(value, "http://agent-origin.internal");
    return parsed.origin === "http://agent-origin.internal" && !parsed.hash ? parsed : null;
  } catch {
    return null;
  }
}

function browserApiRouteAllowed(method, url) {
  if (!(url instanceof URL)) return false;
  const allowed = BROWSER_API_ROUTES.some(([candidateMethod, pattern]) =>
    method === candidateMethod && pattern.test(url.pathname));
  if (!allowed) return false;
  // Only HA's callback carries a query, and the BFF applies the exact
  // one-code/one-state shape. All other query-bearing API requests fail here.
  return !url.search || (
    method === "GET" && url.pathname === "/api/agent/auth/callback"
  );
}

function sendJson(res, status, payload) {
  const body = Buffer.from(JSON.stringify(payload));
  res.writeHead(status, {
    ...API_SECURITY_HEADERS,
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": body.length,
  });
  res.end(body);
}

function serveStatic(req, res, config, url) {
  if (req.method !== "GET" && req.method !== "HEAD") {
    sendJson(res, 404, { error: "route_not_allowed" });
    return;
  }
  if (url.pathname === "/home-agent" && !url.search) {
    res.writeHead(308, {
      ...STATIC_SECURITY_HEADERS,
      "Cache-Control": "no-store",
      Location: "/home-agent/",
    });
    res.end();
    return;
  }
  if (url.search) {
    sendJson(res, 404, { error: "route_not_allowed" });
    return;
  }
  const asset = STATIC_ASSETS.get(url.pathname);
  if (!asset) {
    sendJson(res, 404, { error: "route_not_allowed" });
    return;
  }
  const [filename, contentType, cacheControl] = asset;
  let body;
  try {
    body = fs.readFileSync(path.join(config.assetRoot, filename));
  } catch {
    sendJson(res, 503, { error: "asset_unavailable" });
    return;
  }
  res.writeHead(200, {
    ...STATIC_SECURITY_HEADERS,
    "Cache-Control": cacheControl,
    "Content-Type": contentType,
    "Content-Length": body.length,
  });
  if (req.method === "HEAD") res.end();
  else res.end(body);
}

function filteredAgentCookies(header) {
  const result = [];
  const seen = new Set();
  for (const raw of String(header || "").split(";")) {
    const pair = raw.trim();
    if (!pair) continue;
    const index = pair.indexOf("=");
    if (index <= 0) continue;
    const name = pair.slice(0, index).trim();
    if (!COOKIE_NAMES.has(name)) continue;
    if (seen.has(name) || /[\u0000-\u001f\u007f]/.test(pair)) return null;
    seen.add(name);
    result.push(pair);
  }
  return result.join("; ");
}

async function readRequestBody(req) {
  const chunks = [];
  let total = 0;
  try {
    for await (const chunk of req) {
      const value = Buffer.from(chunk);
      total += value.length;
      if (total > MAX_REQUEST_BYTES) {
        value.fill(0);
        throw new Error("request_too_large");
      }
      chunks.push(value);
    }
    return Buffer.concat(chunks, total);
  } catch (error) {
    for (const chunk of chunks) chunk.fill(0);
    throw error;
  }
}

function safeRedirectLocation(value, publicOrigin) {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) return null;
  if (/[\u0000-\u001f\u007f\\]/.test(value)) return null;
  try {
    const parsed = new URL(value, publicOrigin);
    return parsed.origin === publicOrigin ? `${parsed.pathname}${parsed.search}${parsed.hash}` : null;
  } catch {
    return null;
  }
}

function safeSetCookies(value) {
  const values = Array.isArray(value) ? value : value ? [value] : [];
  return values.filter((cookie) => {
    const name = String(cookie).split("=", 1)[0];
    return COOKIE_NAMES.has(name) && !/[\r\n]/.test(String(cookie));
  });
}

function responseHeaders(upstreamHeaders, publicOrigin) {
  const headers = { ...API_SECURITY_HEADERS };
  for (const name of ["content-type", "content-length", "x-request-id"]) {
    if (upstreamHeaders[name] !== undefined) headers[name] = upstreamHeaders[name];
  }
  const cookies = safeSetCookies(upstreamHeaders["set-cookie"]);
  if (cookies.length) headers["set-cookie"] = cookies;
  if (upstreamHeaders.location !== undefined) {
    const location = safeRedirectLocation(upstreamHeaders.location, publicOrigin);
    if (location) headers.location = location;
    else return null;
  }
  return headers;
}

async function proxyApi(req, res, config, url, rawTarget, originHeader) {
  if (!browserApiRouteAllowed(req.method, url)) {
    sendJson(res, 404, { error: "route_not_allowed" });
    return;
  }
  if (req.method !== "GET" && originHeader !== config.publicOrigin) {
    sendJson(res, 403, { error: "origin_required" });
    return;
  }
  const cookies = filteredAgentCookies(req.headers.cookie);
  if (cookies === null) {
    sendJson(res, 400, { error: "invalid_cookie" });
    return;
  }
  const declaredLength = req.headers["content-length"];
  if (
    declaredLength !== undefined &&
    (!/^\d+$/.test(String(declaredLength)) || Number(declaredLength) > MAX_REQUEST_BYTES)
  ) {
    sendJson(res, 413, { error: "request_too_large" });
    req.resume();
    return;
  }
  let body;
  try {
    body = await readRequestBody(req);
  } catch {
    sendJson(res, 413, { error: "request_too_large" });
    return;
  }
  const target = new URL(config.bffOrigin);
  const headers = { host: target.host, "content-length": String(body.length) };
  for (const name of ["accept", "content-type", "origin", "user-agent", "x-csrf-token"]) {
    if (req.headers[name] !== undefined) headers[name] = req.headers[name];
  }
  if (cookies) headers.cookie = cookies;

  const upstream = http.request({
    protocol: target.protocol,
    hostname: target.hostname,
    port: target.port,
    method: req.method,
    path: rawTarget,
    headers,
  }, (upstreamRes) => {
    const headersOut = responseHeaders(upstreamRes.headers, config.publicOrigin);
    if (!headersOut) {
      upstreamRes.resume();
      sendJson(res, 502, { error: "upstream_response_rejected" });
      return;
    }
    res.writeHead(upstreamRes.statusCode || 502, headersOut);
    upstreamRes.on("error", () => res.destroy());
    upstreamRes.pipe(res);
  });
  upstream.setTimeout(UPSTREAM_TIMEOUT_MS, () => upstream.destroy());
  upstream.on("error", () => {
    body.fill(0);
    if (!res.headersSent) sendJson(res, 502, { error: "bff_unavailable" });
    else res.destroy();
  });
  upstream.on("close", () => body.fill(0));
  upstream.end(body, () => body.fill(0));
}

function createAgentOrigin(config) {
  if (!config?.ready) throw new Error("valid Agent origin configuration is required");
  const handleRequest = async (req, res) => {
    const rawTarget = String(req.url || "");
    const url = safeRequestTarget(rawTarget);
    const host = singleRawHeader(req, "host");
    const origin = singleRawHeader(req, "origin");
    if (!url || host === null || host.toLowerCase() !== config.expectedHost || origin === null) {
      sendJson(res, 404, { error: "route_not_allowed" });
      return;
    }
    if (origin && origin !== config.publicOrigin) {
      sendJson(res, 403, { error: "origin_mismatch" });
      return;
    }
    if (url.pathname === "/home-agent" || url.pathname.startsWith("/home-agent/")) {
      serveStatic(req, res, config, url);
      return;
    }
    if (url.pathname === "/api/agent" || url.pathname.startsWith("/api/agent/")) {
      await proxyApi(req, res, config, url, rawTarget, origin);
      return;
    }
    sendJson(res, 404, { error: "route_not_allowed" });
  };
  const server = http.createServer((req, res) => {
    handleRequest(req, res).catch(() => {
      if (!res.headersSent) sendJson(res, 500, { error: "request_failed" });
      else res.destroy();
    });
  });
  server.headersTimeout = 10_000;
  server.requestTimeout = 15_000;
  server.keepAliveTimeout = 5_000;
  server.on("clientError", (_error, socket) => {
    if (socket.writable) socket.end("HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n");
  });
  server.on("upgrade", (_req, socket) => {
    socket.end("HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n");
  });
  return server;
}

export {
  BROWSER_API_ROUTES,
  STATIC_ASSETS,
  browserApiRouteAllowed,
  configFromEnv,
  createAgentOrigin,
  filteredAgentCookies,
  safeRequestTarget,
};
