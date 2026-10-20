// Gateway rules for the den lighting brain (AL-1, contract v1), exposed under
// /api/lighting-brain/v1/. The brain binds loopback on this host; the gateway
// is its only door.
//
// - Home reads state and the SSE stream with its gateway session.
// - Home's overrides (POST/DELETE) need the session plus
//   X-Home-Lighting-Brain-CSRF: 1, so a cross-site form cannot post one.
// - Edge producers (the Victoria HA bridge, the Jetson activity loop) POST
//   events with their own bearer token and no gateway session. The brain
//   checks the token; the gateway forwards only that and the body headers.

export const LIGHTING_BRAIN_PREFIX = "/api/lighting-brain";
export const LIGHTING_BRAIN_CSRF_HEADER = "x-home-lighting-brain-csrf";

const HOME = "[a-z0-9][a-z0-9-]{0,63}";
const FIXTURE = "[A-Za-z0-9][A-Za-z0-9._-]{0,127}";

const ROUTES = [
  ["GET", new RegExp(`^/v1/homes/${HOME}/state$`), "read"],
  ["GET", new RegExp(`^/v1/homes/${HOME}/stream$`), "read"],
  ["POST", new RegExp(`^/v1/homes/${HOME}/overrides$`), "override"],
  ["DELETE", new RegExp(`^/v1/homes/${HOME}/overrides/${FIXTURE}$`), "override"],
  ["POST", new RegExp(`^/v1/homes/${HOME}/events$`), "producer"],
];

// "read" | "override" | "producer" | null. No route takes a query string.
export function lightingBrainRouteKind(method, suffix) {
  const raw = String(suffix || "");
  if (raw.includes("?") || raw.includes("#") || raw.includes("%")) return null;
  for (const [allowedMethod, pattern, kind] of ROUTES) {
    if (method === allowedMethod && pattern.test(raw)) return kind;
  }
  return null;
}

export function hasBearer(headers) {
  return /^Bearer [A-Za-z0-9._~+/=-]{16,512}$/.test(String(headers.authorization || ""));
}

export function hasLightingBrainCsrf(headers) {
  return String(headers[LIGHTING_BRAIN_CSRF_HEADER] || "").trim() === "1";
}

// Producer ingress is a fixed protocol, not a header tunnel: only the bearer
// and the body headers reach the brain (no cookie, Basic, forwarding headers).
export function lightingBrainProducerHeaders(reqHeaders, target) {
  const headers = { host: new URL(target).host };
  for (const name of ["authorization", "content-type", "content-length", "user-agent", "accept"]) {
    if (reqHeaders[name] !== undefined) headers[name] = reqHeaders[name];
  }
  return headers;
}

// Session requests carry no caller credential to the brain.
export function lightingBrainSessionHeaders(headers) {
  delete headers.authorization;
  delete headers[LIGHTING_BRAIN_CSRF_HEADER];
  return headers;
}
