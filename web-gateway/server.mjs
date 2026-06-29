import crypto from "node:crypto";
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
const BASIC_AUTH = (process.env.HOME_WEB_BASIC_AUTH || "").trim();
const AUTH_REALM = process.env.HOME_WEB_AUTH_REALM || "Home";

const MIME = new Map([
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".jsx", "text/plain; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
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

function rx(pattern) {
  return (suffix) => pattern.test(suffix.split("?")[0]);
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
    prefix: "/proxy/vision",
    env: "HOME_WEB_VISION_TARGET",
    target: envTarget("HOME_WEB_VISION_TARGET", "http://192.168.0.100:8091"),
    allow: rx(/^\/(healthz|snapshot\/|describe_clip|describe|reason|reason_zoom|locate|api\/)/),
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
    target: envTarget("HOME_WEB_SUPERVISOR_TARGET", "http://192.168.0.100:8093"),
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
    allow: rx(/^\/api\//),
    ws: false,
  },
];

function authCookieValue() {
  if (!BASIC_AUTH) return "";
  return crypto.createHash("sha256").update(`home-web:${BASIC_AUTH}`).digest("base64url");
}

const AUTH_COOKIE_VALUE = authCookieValue();

function parseCookies(header) {
  const out = {};
  for (const part of String(header || "").split(";")) {
    const idx = part.indexOf("=");
    if (idx < 0) continue;
    out[part.slice(0, idx).trim()] = decodeURIComponent(part.slice(idx + 1).trim());
  }
  return out;
}

function hasValidAuth(req) {
  if (!BASIC_AUTH) return { ok: true, setCookie: false };
  const cookies = parseCookies(req.headers.cookie);
  if (cookies.home_web_auth === AUTH_COOKIE_VALUE) return { ok: true, setCookie: false };

  const auth = String(req.headers.authorization || "");
  if (!auth.startsWith("Basic ")) return { ok: false, setCookie: false };
  let decoded = "";
  try {
    decoded = Buffer.from(auth.slice("Basic ".length), "base64").toString("utf8");
  } catch {
    return { ok: false, setCookie: false };
  }
  const got = Buffer.from(decoded);
  const expected = Buffer.from(BASIC_AUTH);
  const ok = got.length === expected.length && crypto.timingSafeEqual(got, expected);
  return { ok, setCookie: ok };
}

function requireAuth(req, res) {
  const auth = hasValidAuth(req);
  if (auth.ok) {
    if (auth.setCookie && res) {
      res.setHeader("Set-Cookie", `home_web_auth=${encodeURIComponent(AUTH_COOKIE_VALUE)}; HttpOnly; SameSite=Lax; Path=/`);
    }
    return true;
  }
  if (res) {
    res.writeHead(401, {
      "WWW-Authenticate": `Basic realm="${AUTH_REALM}", charset="UTF-8"`,
      "Content-Type": "text/plain; charset=utf-8",
    });
    res.end("authentication required");
  }
  return false;
}

function cleanHeaders(reqHeaders, target) {
  const headers = { ...reqHeaders };
  headers.host = new URL(target).host;
  if (String(headers.authorization || "").startsWith("Basic ")) delete headers.authorization;
  delete headers.cookie;
  delete headers["proxy-authorization"];
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

function serveFile(req, res, filePath, { cache = "no-store" } = {}) {
  fs.stat(filePath, (err, stat) => {
    if (err || !stat.isFile()) {
      res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("not found");
      return;
    }
    const type = MIME.get(path.extname(filePath).toLowerCase()) || "application/octet-stream";
    res.writeHead(200, {
      "Content-Type": type,
      "Content-Length": stat.size,
      "Cache-Control": cache,
    });
    fs.createReadStream(filePath).pipe(res);
  });
}

function serveStatic(req, res) {
  const parsed = new URL(req.url, "http://home.local");
  if (parsed.pathname === "/healthz") {
    res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
    res.end(JSON.stringify({ ok: true, service: "home-web-gateway" }));
    return;
  }

  if (parsed.pathname.startsWith("/assets/apartment/")) {
    const rel = parsed.pathname.slice("/assets/apartment/".length);
    const configured = safeFile(APARTMENT_ASSETS_DIR, rel);
    if (configured && fs.existsSync(configured)) {
      serveFile(req, res, configured, { cache: "no-cache" });
      return;
    }
    const fallback = safeFile(FALLBACK_APARTMENT_DIR, rel);
    if (fallback) {
      serveFile(req, res, fallback, { cache: "no-cache" });
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
  serveFile(req, res, filePath, { cache: pathname === "/index.html" ? "no-store" : "no-cache" });
}

function proxyHttp(req, res, route) {
  const suffix = routeSuffix(route, req.url);
  if (!route.allow(suffix)) {
    res.writeHead(403, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("proxy path not allowed");
    return;
  }

  const targetUrl = buildTargetUrl(route, suffix);
  const client = targetUrl.protocol === "https:" ? https : http;
  const upstream = client.request(targetUrl, {
    method: req.method,
    headers: cleanHeaders(req.headers, route.target),
  }, (upstreamRes) => {
    const headers = { ...upstreamRes.headers };
    delete headers["content-security-policy"];
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
  if (!route.allow(suffix)) {
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
    const headers = cleanHeaders(req.headers, route.target);
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
    host: HOST,
    port: PORT,
    appDir: APP_DIR,
    apartmentAssetsDir: APARTMENT_ASSETS_DIR,
    basicAuthEnabled: !!BASIC_AUTH,
    routes: routes.map(({ prefix, env, target, ws }) => ({ prefix, env, target, ws })),
  };
  console.log(JSON.stringify(summary, null, 2));
}

if (process.argv.includes("--check")) {
  checkConfig();
  process.exit(0);
}

const server = http.createServer((req, res) => {
  if (!requireAuth(req, res)) return;
  const parsed = new URL(req.url, "http://home.local");
  const route = findRoute(parsed.pathname);
  if (route) proxyHttp(req, res, route);
  else serveStatic(req, res);
});

server.on("upgrade", (req, socket, head) => {
  if (!hasValidAuth(req).ok) {
    socket.write(`HTTP/1.1 401 Unauthorized\r\nWWW-Authenticate: Basic realm="${AUTH_REALM}", charset="UTF-8"\r\n\r\n`);
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
