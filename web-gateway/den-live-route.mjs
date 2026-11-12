// Gateway rules for the live den view's people stream (den-live), under /api/den-live/v1/. den-live binds
// 127.0.0.1:8111 on this host, and the gateway is its only door. It's off unless HOME_WEB_DEN_LIVE=1.
//
// - Reads only, for one home, "den": the latest state, and the server-sent event stream.
// - A signed-in session reads them, and so does a look-only tailnet device (look-only.mjs).
// - No caller credential or cookie reaches den-live: only Accept, Last-Event-ID and User-Agent pass.
// - Home learns from the page shell which site's apartment view the markers belong to (denLiveRuntime). That's the
//   den's site, as the registration route names it, so no site is written here.
import { REGISTRATION_ROUTE } from "./equipment-registration.mjs";

export const DEN_LIVE_PREFIX = "/api/den-live";
export const DEN_LIVE_HOME = "den";
export const DEN_SITE = /^\/api\/apartment\/v1\/sites\/([a-z0-9][a-z0-9-]{0,63})\//.exec(REGISTRATION_ROUTE)[1];

const READ = /^\/v1\/homes\/den\/(?:state|stream)$/;

// "read" | null. GET only: den-live's stream answers no HEAD on the stream path, and nothing takes a query string.
export function denLiveRouteKind(method, suffix) {
  const raw = String(suffix || "");
  if (raw.includes("?") || raw.includes("#") || raw.includes("%")) return null;
  return method === "GET" && READ.test(raw) ? "read" : null;
}

export function denLiveEnabled(env = process.env) {
  return ["1", "true", "yes", "on"].includes(String(env.HOME_WEB_DEN_LIVE || "").trim().toLowerCase());
}

export function denLiveHeaders(reqHeaders, target) {
  const headers = { host: new URL(target).host };
  for (const name of ["accept", "last-event-id", "user-agent"]) {
    if (reqHeaders[name] !== undefined) headers[name] = reqHeaders[name];
  }
  return headers;
}

// The page shell's line telling Home where the den's markers come from.
export function denLiveRuntime() {
  const base = `${DEN_LIVE_PREFIX}/v1/homes/${DEN_LIVE_HOME}/`;
  return `window.HomeDenLive=Object.freeze({home:${JSON.stringify(DEN_LIVE_HOME)},site:${JSON.stringify(DEN_SITE)},`
    + `state:${JSON.stringify(base + "state")},stream:${JSON.stringify(base + "stream")}});`;
}
