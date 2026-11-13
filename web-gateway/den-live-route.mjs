// The live Den service binds loopback. Serve and ordinary authenticated Home
// share these exact routes. Body v2 and virtual controls use upstream paths
// that old producers cannot serve after a rollback.
import { REGISTRATION_ROUTE } from "./equipment-registration.mjs";

export const DEN_LIVE_PREFIX = "/api/den-live";
export const DEN_LIVE_HOME = "den";
export const DEN_SITE = /^\/api\/apartment\/v1\/sites\/([a-z0-9][a-z0-9-]{0,63})\//.exec(REGISTRATION_ROUTE)[1];
export const DEN_LABELS_CSRF_HEADER = "x-home-den-labels";
export const DEN_LABELS_MAX_BYTES = 65536;
export const DEN_LIVE_CSRF_HEADER = "x-home-den-live-csrf";
export const DEN_LIVE_WRITE_MAX_BYTES = 65536;

const HOME = "/v1/homes/den";
const COORDINATOR = HOME + "/coordinator";
const READ = new Set([HOME + "/state", HOME + "/stream", HOME + "/labels"]);
const COORDINATOR_WRITES = new Set(["switch", "settings", "learned/clear", "desk", "stop", "resume"]
  .map(operation => COORDINATOR + "/" + operation));

export function denLiveRouteKind(method, suffix) {
  if (method === "PUT") return suffix === HOME + "/labels" ? "labels" : null;
  if (method === "POST") return COORDINATOR_WRITES.has(suffix) ? "coordinator-write" : null;
  if (method !== "GET") return null;
  if (READ.has(suffix)) return "read";
  if (suffix === HOME + "/body") return "body";
  if (suffix === COORDINATOR) return "coordinator";
  return null;
}

function jsonFrameProblem(headers, maxBytes, typeError) {
  if (!/^application\/json(?:\s*;|$)/i.test(String(headers["content-type"] || "").trim())) return [415, typeError];
  const length = String(headers["content-length"] ?? "");
  if (headers["transfer-encoding"] !== undefined || !/^\d{1,6}$/.test(length)) return [411, "length_required"];
  if (Number(length) > maxBytes) return [413, "too_large"];
  return null;
}

export function denLabelsSaveProblem(headers) {
  if (String(headers[DEN_LABELS_CSRF_HEADER] || "").trim() !== "1") return [403, "den_labels_csrf_required"];
  return jsonFrameProblem(headers, DEN_LABELS_MAX_BYTES, "content_type");
}

export function hasDenLiveCsrf(headers) {
  return String(headers?.[DEN_LIVE_CSRF_HEADER] || "").trim() === "1";
}

export function denLiveWriteRefusal(method, suffix, headers = {}) {
  if (denLiveRouteKind(method, suffix) !== "coordinator-write") return { status: 403, error: "den_live_write_not_allowed" };
  const problem = hasDenLiveCsrf(headers)
    ? jsonFrameProblem(headers, DEN_LIVE_WRITE_MAX_BYTES, "json_required")
    : [403, "den_live_csrf_required"];
  return problem ? { status: problem[0], error: problem[1] } : null;
}

export function denLiveEnabled(env = process.env) {
  return ["1", "true", "yes", "on"].includes(String(env.HOME_WEB_DEN_LIVE || "").trim().toLowerCase());
}

export function denLiveUpstreamSuffix(suffix, kind, { bodyV2, virtual, serve }) {
  if (kind === "body") return bodyV2 ? suffix + "-v2" : null;
  if (kind !== "coordinator" && kind !== "coordinator-write") return suffix;
  if (virtual) {
    if (suffix === COORDINATOR + "/desk") return null;
    return COORDINATOR + "/virtual" + suffix.slice(COORDINATOR.length);
  }
  if (serve || suffix === COORDINATOR + "/stop" || suffix === COORDINATOR + "/resume") return null;
  return suffix;
}

export function denLiveHeaders(reqHeaders, target, method = "GET") {
  const headers = { host: new URL(target).host };
  const names = method === "PUT" || method === "POST"
    ? ["accept", "user-agent", "content-type", "content-length"]
    : ["accept", "last-event-id", "user-agent"];
  for (const name of names) {
    if (reqHeaders[name] !== undefined) headers[name] = reqHeaders[name];
  }
  return headers;
}

const base = `${DEN_LIVE_PREFIX}/v1/homes/${DEN_LIVE_HOME}/`;
export function denLiveRuntime({ bodyV2 = false, virtual = false, coordinator = true } = {}) {
  return "window.HomeDenLive=Object.freeze(" + JSON.stringify({
    home: DEN_LIVE_HOME, site: DEN_SITE, state: base + "state", stream: base + "stream",
    ...(bodyV2 ? { body: base + "body", bodySchema: "den-live-body-2" } : {}),
    ...(coordinator ? { coordinator: base + "coordinator" } : {}),
    ...(virtual ? { virtual: true } : {}),
  }) + ");";
}

export function denLabelsRuntime() {
  return "window.HomeDenLabels=Object.freeze(" + JSON.stringify({ site: DEN_SITE, labels: base + "labels" }) + ");";
}
