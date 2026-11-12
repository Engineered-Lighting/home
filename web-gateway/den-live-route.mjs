// Gateway rules for the live den view's people stream (den-live), under /api/den-live/v1/. den-live binds
// 127.0.0.1:8111 on this host, and the gateway is its only door. It's off unless HOME_WEB_DEN_LIVE=1.
//
// - For one home, "den": the latest state, and the server-sent event stream. A signed-in session reads them, and so
//   does a look-only tailnet device (look-only.mjs).
// - The lighting coordinator's state (den-live's coordinator_live.py), read the same way. Its four writes (the switch,
//   settings, clearing what it learned, the desk test) are for a signed-in session only, as JSON of at most 64 KB
//   with the CSRF header (denLiveWriteRefusal). Nothing else is ever written to den-live.
// - No caller credential or cookie reaches den-live: only Accept, Last-Event-ID and User-Agent pass, plus a write's
//   Content-Type and Content-Length.
// - Home learns from the page shell which site's apartment view the markers belong to (denLiveRuntime). That's the
//   den's site, as the registration route names it, so no site is written here.
import { REGISTRATION_ROUTE } from "./equipment-registration.mjs";

export const DEN_LIVE_PREFIX = "/api/den-live";
export const DEN_LIVE_HOME = "den";
export const DEN_SITE = /^\/api\/apartment\/v1\/sites\/([a-z0-9][a-z0-9-]{0,63})\//.exec(REGISTRATION_ROUTE)[1];

const READ = /^\/v1\/homes\/den\/(?:state|stream)$/;
// The live body (den-live-body-1 frames) has a path of its own, so it can stay with signed-in Home: look-only.mjs
// passes "read" only, never "body".
const BODY = /^\/v1\/homes\/den\/body$/;
const COORDINATOR = /^\/v1\/homes\/den\/coordinator$/;
const COORDINATOR_WRITE = /^\/v1\/homes\/den\/coordinator\/(?:switch|settings|learned\/clear|desk)$/;
export const DEN_LIVE_CSRF_HEADER = "x-home-den-live-csrf";
export const DEN_LIVE_WRITE_MAX_BYTES = 65536;

// "read" | "body" | "coordinator" (GET) | "coordinator-write" (POST) | null. den-live's streams answer no HEAD, and
// nothing takes a query string.
export function denLiveRouteKind(method, suffix) {
  const raw = String(suffix || "");
  if (raw.includes("?") || raw.includes("#") || raw.includes("%")) return null;
  if (method === "POST") return COORDINATOR_WRITE.test(raw) ? "coordinator-write" : null;
  if (method !== "GET") return null;
  return READ.test(raw) ? "read" : BODY.test(raw) ? "body" : COORDINATOR.test(raw) ? "coordinator" : null;
}

export function hasDenLiveCsrf(headers) {
  return String(headers?.[DEN_LIVE_CSRF_HEADER] || "").trim() === "1";
}

// Why a signed-in write to den-live is refused, or null when it may pass: one of the coordinator's four POSTs, with the
// CSRF header (a page elsewhere can't send it without a preflight the gateway never grants), as JSON with a
// Content-Length within den-live's own limit. den-live reads its body by that length, so a chunked one is refused.
export function denLiveWriteRefusal(method, suffix, headers = {}) {
  if (denLiveRouteKind(method, suffix) !== "coordinator-write") return { status: 403, error: "den_live_write_not_allowed" };
  if (!hasDenLiveCsrf(headers)) return { status: 403, error: "den_live_csrf_required" };
  if (!/^application\/json(?:\s*;|$)/i.test(String(headers["content-type"] || "").trim())) return { status: 415, error: "json_required" };
  const length = String(headers["content-length"] ?? "");
  if (headers["transfer-encoding"] !== undefined || !/^\d{1,6}$/.test(length)) return { status: 411, error: "length_required" };
  if (Number(length) > DEN_LIVE_WRITE_MAX_BYTES) return { status: 413, error: "too_large" };
  return null;
}

export function denLiveEnabled(env = process.env) {
  return ["1", "true", "yes", "on"].includes(String(env.HOME_WEB_DEN_LIVE || "").trim().toLowerCase());
}

export function denLiveHeaders(reqHeaders, target, write = false) {
  const headers = { host: new URL(target).host };
  for (const name of write ? ["accept", "content-type", "content-length", "user-agent"] : ["accept", "last-event-id", "user-agent"]) {
    if (reqHeaders[name] !== undefined) headers[name] = reqHeaders[name];
  }
  return headers;
}

// The page shell's line telling Home where the den's markers, the lighting coordinator, and for signed-in Home its
// body, come from.
export function denLiveRuntime() {
  const base = `${DEN_LIVE_PREFIX}/v1/homes/${DEN_LIVE_HOME}/`;
  return `window.HomeDenLive=Object.freeze({home:${JSON.stringify(DEN_LIVE_HOME)},site:${JSON.stringify(DEN_SITE)},`
    + `state:${JSON.stringify(base + "state")},stream:${JSON.stringify(base + "stream")},body:${JSON.stringify(base + "body")},`
    + `coordinator:${JSON.stringify(base + "coordinator")}});`;
}
