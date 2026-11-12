// Gateway rules for the live den view's people stream (den-live), under /api/den-live/v1/. den-live binds
// 127.0.0.1:8111 on this host, and the gateway is its only door. It's off unless HOME_WEB_DEN_LIVE=1.
//
// - Reads, for one home, "den": the latest state, the server-sent event stream, and the den's labels document.
// - A signed-in session reads them, and so does a look-only tailnet device (look-only.mjs).
// - One write: signed-in Home saves the labels document (PUT .../labels, den-room-labels-1), with the
//   x-home-den-labels header, a JSON body of at most 64 KB, and nothing else. Look-only never writes.
// - No caller credential or cookie reaches den-live: only Accept, Last-Event-ID and User-Agent pass, plus a save's
//   Content-Type and Content-Length.
// - Home learns from the page shell which site's apartment view the markers belong to (denLiveRuntime). That's the
//   den's site, as the registration route names it, so no site is written here.
import { REGISTRATION_ROUTE } from "./equipment-registration.mjs";

export const DEN_LIVE_PREFIX = "/api/den-live";
export const DEN_LIVE_HOME = "den";
export const DEN_SITE = /^\/api\/apartment\/v1\/sites\/([a-z0-9][a-z0-9-]{0,63})\//.exec(REGISTRATION_ROUTE)[1];
export const DEN_LABELS_CSRF_HEADER = "x-home-den-labels";
export const DEN_LABELS_MAX_BYTES = 65536;

const READ = /^\/v1\/homes\/den\/(?:state|stream|labels)$/;
// The live body (den-live-body-1 frames) has a path of its own, so it can stay with signed-in Home: look-only.mjs
// passes "read" only, never "body".
const BODY = /^\/v1\/homes\/den\/body$/;
const LABELS = /^\/v1\/homes\/den\/labels$/;

// "read" | "body" | "labels" | null. GET reads (den-live's streams answer no HEAD); PUT on the labels path is the one
// write, "labels". Nothing takes a query string.
export function denLiveRouteKind(method, suffix) {
  const raw = String(suffix || "");
  if (raw.includes("?") || raw.includes("#") || raw.includes("%")) return null;
  if (method === "PUT") return LABELS.test(raw) ? "labels" : null;
  if (method !== "GET") return null;
  return READ.test(raw) ? "read" : BODY.test(raw) ? "body" : null;
}

// Why a labels save is refused before it reaches den-live, or null: [status, error].
export function denLabelsSaveProblem(headers) {
  if (String(headers[DEN_LABELS_CSRF_HEADER] || "").trim() !== "1") return [403, "den_labels_csrf_required"];
  if (!/^application\/json(?:\s*;|$)/i.test(String(headers["content-type"] || ""))) return [415, "content_type"];
  const length = String(headers["content-length"] || "");
  if (headers["transfer-encoding"] || !/^\d{1,6}$/.test(length)) return [411, "length_required"];
  if (Number(length) > DEN_LABELS_MAX_BYTES) return [413, "too_large"];
  return null;
}

export function denLiveEnabled(env = process.env) {
  return ["1", "true", "yes", "on"].includes(String(env.HOME_WEB_DEN_LIVE || "").trim().toLowerCase());
}

export function denLiveHeaders(reqHeaders, target, method = "GET") {
  const headers = { host: new URL(target).host };
  const names = method === "PUT" ? ["accept", "user-agent", "content-type", "content-length"] : ["accept", "last-event-id", "user-agent"];
  for (const name of names) {
    if (reqHeaders[name] !== undefined) headers[name] = reqHeaders[name];
  }
  return headers;
}

// The page shell's line telling Home where the den's markers, and for signed-in Home its body, come from.
export function denLiveRuntime() {
  const base = `${DEN_LIVE_PREFIX}/v1/homes/${DEN_LIVE_HOME}/`;
  return `window.HomeDenLive=Object.freeze({home:${JSON.stringify(DEN_LIVE_HOME)},site:${JSON.stringify(DEN_SITE)},`
    + `state:${JSON.stringify(base + "state")},stream:${JSON.stringify(base + "stream")},body:${JSON.stringify(base + "body")}});`;
}

// The page shell's line telling Home where the den's labels are (home-den-labels-view.mjs). Look-only gets it too:
// it reads the labels, and the gateway refuses it every write.
export function denLabelsRuntime() {
  const base = `${DEN_LIVE_PREFIX}/v1/homes/${DEN_LIVE_HOME}/`;
  return `window.HomeDenLabels=Object.freeze({site:${JSON.stringify(DEN_SITE)},labels:${JSON.stringify(base + "labels")}});`;
}
