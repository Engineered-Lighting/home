import crypto from "node:crypto";
import { performance } from "node:perf_hooks";
import { COOKIE_NAME, SessionStore } from "./bff.mjs";

export const VICTORIA_OAUTH_COOKIE_NAME = "__Host-home_agent_victoria_oauth";
const START = "/api/agent/auth/start", SESSION = "/api/agent/auth/session", LOGOUT = "/api/agent/auth/logout";
const TTL = 300_000, DEADLINE = 10_000, TOKEN = /^[A-Za-z0-9_-]{43}$/;
const sensitiveHeaders = ["host", "origin", "cookie", "x-csrf-token", "authorization", "proxy-authorization",
  "sec-fetch-site", "content-length", "content-type", "content-encoding", "transfer-encoding"];
const equal = (left, right) => typeof left === "string" && typeof right === "string" &&
  Buffer.byteLength(left) === Buffer.byteLength(right) && crypto.timingSafeEqual(Buffer.from(left), Buffer.from(right));
const cookie = (name, value, maxAge, sameSite) => `${name}=${value}; Path=/; HttpOnly; Secure; SameSite=${sameSite}; Max-Age=${maxAge}`;
const clearLogin = () => cookie(VICTORIA_OAUTH_COOKIE_NAME, "", 0, "Lax");
const clearSession = () => cookie(COOKIE_NAME, "", 0, "Strict");

function cookies(header) {
  const result = new Map();
  if (header === undefined) return result;
  if (typeof header !== "string" || header.length > 4096) throw new Error();
  for (const entry of header.split(";")) {
    const separator = entry.indexOf("="), name = entry.slice(0, separator).trim();
    if (separator < 1 || !name || result.has(name)) throw new Error();
    result.set(name, entry.slice(separator + 1).trim());
  }
  for (const name of [COOKIE_NAME, VICTORIA_OAUTH_COOKIE_NAME]) {
    if (result.has(name) && !TOKEN.test(result.get(name))) throw new Error();
  }
  return result;
}

// Private factory only. The caller supplies the exact qualified store/config
// pair and owns the HTTPS listener, SessionStore cleanup timer and store lifetime.
export function createVictoriaBrowserLogin({ store, config } = {}) {
  if (!(store instanceof SessionStore) || !store.usesQualifiedConfiguration(config) ||
      !store.hasSharedSessionRevocation || store.haIssuerId !== "home-assistant:victoria" || store.siteId !== "victoria" ||
      !(config.allowedOrigins instanceof Set) || config.allowedOrigins.size !== 1) throw new Error("invalid_victoria_login_configuration");
  const [issuer, site, haOrigin, clientId, redirectUri] = JSON.parse(config.haAuthBinding);
  const origin = [...config.allowedOrigins][0], browser = new URL(origin), ha = new URL(haOrigin), callback = new URL(redirectUri);
  if (issuer !== store.haIssuerId || site !== store.siteId || browser.protocol !== "https:" || browser.origin !== origin ||
      ha.protocol !== "https:" || ha.origin !== haOrigin || new URL(clientId).origin !== origin || callback.origin !== origin ||
      callback.search || callback.hash || [START, SESSION, LOGOUT].includes(callback.pathname)) throw new Error("invalid_victoria_login_configuration");
  const pending = new Map(), operations = new Set();
  let closed = false;
  const stamp = () => ({ wall: store.now(), mono: performance.now() });
  const fresh = (started, ttl) => {
    const wall = store.now(), mono = performance.now(), age = wall - started.wall, elapsed = mono - started.mono;
    return Number.isSafeInteger(wall) && Number.isSafeInteger(started.wall) && Number.isFinite(elapsed) &&
      age >= 0 && elapsed >= 0 && age < ttl && elapsed < ttl && Math.abs(age - elapsed) <= 1000;
  };

  async function handle(req, res) {
    const rawPath = typeof req.url === "string" ? req.url.split("?")[0] : "";
    const method = ({ [START]: "POST", [callback.pathname]: "GET", [SESSION]: "GET", [LOGOUT]: "POST" })[rawPath];
    if (!method) return false;
    const reply = (status, value, extra = {}) => {
      if (res.destroyed || res.writableEnded) return;
      res.writeHead(status, { "content-type": "application/json", "cache-control": "no-store",
        "referrer-policy": "no-referrer", "x-content-type-options": "nosniff", ...extra });
      res.end(status === 303 ? "" : JSON.stringify(value));
    };
    if (closed) { reply(503, { error: "login_unavailable" }); return true; }
    if (req.method !== method) { reply(405, { error: "method_not_allowed" }); return true; }
    const headers = req.headersDistinct;
    if (!headers || sensitiveHeaders.some((name) => headers[name]?.length > 1)) {
      reply(400, { error: "invalid_request" }); return true;
    }
    if (req.socket.encrypted !== true || headers.host?.[0] !== browser.host) {
      reply(403, { error: "origin_denied" }); return true;
    }
    if (Object.keys(headers).some((name) => ["authorization", "proxy-authorization", "forwarded", "x-real-ip", "x-user-id", "x-ha-user-id", "x-site-id"].includes(name) ||
        name.startsWith("x-authenticated-") || name.startsWith("x-home-agent-") || name.startsWith("x-forwarded-"))) {
      reply(400, { error: "claimed_authority_forbidden" }); return true;
    }
    if (rawPath !== callback.pathname) {
      const suppliedOrigin = headers.origin?.[0], fetchSite = headers["sec-fetch-site"]?.[0];
      if (suppliedOrigin !== undefined ? suppliedOrigin !== origin || fetchSite !== undefined && fetchSite !== "same-origin" :
          rawPath !== SESSION || fetchSite !== "same-origin") {
        reply(403, { error: "origin_denied" }); return true;
      }
    }
    let url, suppliedCookies;
    try {
      url = new URL(req.url, origin);
      if (url.hash || url.origin !== origin || rawPath !== callback.pathname && url.search) throw new Error();
      suppliedCookies = cookies(headers.cookie?.[0]);
      if (headers["content-encoding"] || headers["transfer-encoding"] ||
          headers["content-length"] && headers["content-length"][0] !== "0") throw new Error();
    } catch { reply(400, { error: "invalid_request" }); return true; }
    if (operations.size >= 2) { reply(429, { error: "login_busy" }); return true; }
    const context = { cancelled: false, started: stamp() };
    const disconnected = () => { context.cancelled = true; };
    const unavailable = () => closed || context.cancelled || res.destroyed || res.writableEnded || !fresh(context.started, DEADLINE);
    res.once("close", disconnected);
    operations.add(context);
    let timer;
    const execution = (async () => {
      for await (const chunk of req) if (chunk.length) throw new Error();
      if (unavailable()) throw new Error();
      if (rawPath === START) {
        for (const [key, value] of pending) if (!fresh(value, TTL)) pending.delete(key);
        if (pending.size >= 32) return reply(429, { error: "login_start_busy" });
        const state = crypto.randomBytes(32).toString("base64url"), nonce = crypto.randomBytes(32).toString("base64url");
        pending.set(state, { nonce, ...stamp() });
        const authorize = new URL("/auth/authorize", haOrigin);
        authorize.search = new URLSearchParams({ response_type: "code", client_id: clientId, redirect_uri: redirectUri, state }).toString();
        return reply(200, { authorize_url: authorize.href }, { "set-cookie": cookie(VICTORIA_OAUTH_COOKIE_NAME, nonce, TTL / 1000, "Lax") });
      }
      if (rawPath === callback.pathname) {
        const keys = [...url.searchParams.keys()].sort();
        if (keys.length !== 2 || keys[0] !== "code" || keys[1] !== "state") return reply(400, { error: "invalid_callback" }, { "set-cookie": clearLogin() });
        const state = url.searchParams.get("state"), code = url.searchParams.get("code");
        if (!TOKEN.test(state) || !/^[a-f0-9]{32,128}$/.test(code)) return reply(400, { error: "invalid_callback" }, { "set-cookie": clearLogin() });
        const started = pending.get(state);
        pending.delete(state);
        if (!started || !equal(started.nonce, suppliedCookies.get(VICTORIA_OAUTH_COOKIE_NAME)) || !fresh(started, TTL)) {
          return reply(400, { error: "invalid_callback" }, { "set-cookie": clearLogin() });
        }
        let login, delivered = false;
        try {
          login = await store.authenticateLogin(code);
          if (unavailable() || !fresh(started, TTL)) throw new Error();
          const oldId = suppliedCookies.get(COOKIE_NAME), previous = oldId && store.getForRevocation(oldId);
          if (previous && !await store.revoke(config, oldId, previous, undefined, { reason: "login_replaced" })) throw new Error();
          if (unavailable()) throw new Error();
          reply(303, null, { location: "/", "set-cookie": [cookie(COOKIE_NAME, login.id,
            Math.floor(store.absoluteTtlMs / 1000), "Strict"), clearLogin()] });
          delivered = true;
        } finally {
          if (login && !delivered) await store.revoke(config, login.id, store.getForRevocation(login.id), undefined, { reason: "login_not_delivered" });
        }
        return;
      }
      const sessionId = suppliedCookies.get(COOKIE_NAME);
      const session = rawPath === LOGOUT ? store.getForRevocation(sessionId) : store.get(sessionId);
      if (!session) return reply(401, { error: "authentication_required" }, { "set-cookie": clearSession() });
      if (rawPath === LOGOUT) {
        if (!equal(headers["x-csrf-token"]?.[0], session.csrf)) return reply(403, { error: "csrf_denied" });
        const revoked = await store.revoke(config, sessionId, session, undefined, { reason: "logout" });
        if (unavailable()) throw new Error();
        return reply(revoked ? 200 : 503, revoked ? { ok: true } : { error: "logout_revocation_pending", retryable: true }, { "set-cookie": clearSession() });
      }
      try {
        await store.revalidate(config, sessionId, session, undefined, store.now(), { forcePrincipalCheck: true });
      } catch {
        if (store.getForRevocation(sessionId)?.state === "active") return reply(503, { error: "session_unavailable" });
        return reply(401, { error: "authentication_required" }, { "set-cookie": clearSession() });
      }
      if (unavailable() || store.get(sessionId) !== session) {
        store.scheduleRevocation(sessionId, session, "authentication_revoked");
        return reply(401, { error: "authentication_required" }, { "set-cookie": clearSession() });
      }
      reply(200, { authenticated: true, authority: { version: 1, site_id: "victoria", ha_issuer_id: "home-assistant:victoria" },
        user_id: session.principal.userId, is_admin: session.principal.isAdmin === true, csrf_token: session.csrf });
    })().finally(() => { operations.delete(context); clearTimeout(timer); res.removeListener("close", disconnected); });
    const deadline = new Promise((_, reject) => { timer = setTimeout(() => { context.cancelled = true; reject(new Error()); }, DEADLINE); timer.unref(); });
    try { await Promise.race([execution, deadline]); }
    catch { reply(503, { error: "login_unavailable" }, rawPath === callback.pathname ? { "set-cookie": clearLogin() } : {}); }
    finally { if (!req.complete) req.destroy(); }
    return true;
  }

  return Object.freeze({ handle, close() { closed = true; pending.clear(); for (const context of operations) context.cancelled = true; } });
}
