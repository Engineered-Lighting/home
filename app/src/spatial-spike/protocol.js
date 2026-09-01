export const PROTOCOL_VERSION = "home.spatial-spike.v1";
export const CONNECTION_TYPE = "home.spatial-spike/connect";

export const HOST_TO_FRAME = Object.freeze({
  INIT: "home.spatial-spike/init",
  SET_SITES: "home.spatial-spike/set-sites",
  SET_ENVIRONMENT: "home.spatial-spike/set-environment",
  SET_REDUCED_MOTION: "home.spatial-spike/set-reduced-motion",
  NAVIGATE: "home.spatial-spike/navigate",
  ADVANCE_JOURNEY: "home.spatial-spike/advance-journey",
  CANCEL_JOURNEY: "home.spatial-spike/cancel-journey",
  REQUEST_SNAPSHOT: "home.spatial-spike/request-snapshot",
});

export const FRAME_TO_HOST = Object.freeze({
  READY: "home.spatial-spike/ready",
  STATE: "home.spatial-spike/state",
  EVENT: "home.spatial-spike/event",
  SNAPSHOT: "home.spatial-spike/snapshot",
  ERROR: "home.spatial-spike/error",
});

export const FRAME_ERROR_CODES = Object.freeze({
  INVALID_ENVELOPE: "invalid-envelope",
  INVALID_TYPE: "invalid-type",
  INVALID_PAYLOAD: "invalid-payload",
  UNSUPPORTED_COMMAND: "unsupported-command",
  COMMAND_FAILED: "command-failed",
  MESSAGE_UNREADABLE: "message-unreadable",
});

const HOST_TYPES = new Set(Object.values(HOST_TO_FRAME));
const FRAME_TYPES = new Set(Object.values(FRAME_TO_HOST));
const FRAME_ERROR_CODE_VALUES = new Set(Object.values(FRAME_ERROR_CODES));
const ID_PATTERN = /^[a-z0-9][a-z0-9._:-]{0,95}$/i;
const FORBIDDEN_KEYS = new Set([
  "address",
  "street",
  "postal",
  "postalcode",
  "token",
  "accesstoken",
  "refreshtoken",
  "credential",
  "credentials",
  "secret",
  "url",
  "uri",
  "cameraurl",
  "cameraframe",
]);
const PRECISE_GEO_KEYS = new Set([
  "lat",
  "latitude",
  "lon",
  "lng",
  "longitude",
  "coordinates",
  "bbox",
  "bounds",
  "geofence",
]);
const ENV_VALUES = Object.freeze({
  network: new Set(["online", "offline"]),
  provider: new Set(["live", "degraded", "unavailable"]),
  core: new Set(["online", "offline"]),
  ai: new Set(["online", "offline"]),
});

const isPlainObject = (value) => Boolean(
  value
  && typeof value === "object"
  && !Array.isArray(value)
  && (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null),
);

const normalizeKey = (key) => String(key).replace(/[^a-z0-9]/gi, "").toLowerCase();

const hasForbiddenKey = (value, { preciseGeo = false, seen = new WeakSet(), depth = 0 } = {}) => {
  if (!value || typeof value !== "object") return false;
  if (depth > 12 || seen.has(value)) return true;
  seen.add(value);
  if (Array.isArray(value)) {
    return value.some((child) => hasForbiddenKey(child, { preciseGeo, seen, depth: depth + 1 }));
  }
  if (!isPlainObject(value)) return true;
  return Object.entries(value).some(([key, child]) => {
    const normalized = normalizeKey(key);
    return FORBIDDEN_KEYS.has(normalized)
      || (preciseGeo && PRECISE_GEO_KEYS.has(normalized))
      || hasForbiddenKey(child, { preciseGeo, seen, depth: depth + 1 });
  });
};

const hasExactKeys = (value, required, optional = []) => {
  if (!isPlainObject(value)) return false;
  const keys = Object.keys(value);
  const allowed = new Set([...required, ...optional]);
  return required.every((key) => Object.hasOwn(value, key))
    && keys.every((key) => allowed.has(key));
};

const validId = (value) => typeof value === "string" && ID_PATTERN.test(value);
const finiteBetween = (value, min, max) => Number.isFinite(value) && value >= min && value <= max;

export function validateSyntheticSite(site) {
  if (!isPlainObject(site) || hasForbiddenKey(site)) return false;
  if (!hasExactKeys(site, ["id", "label", "countryCode", "privacyClass", "anchor", "availability", "capabilities"])) return false;
  if (!validId(site.id) || typeof site.label !== "string" || site.label.length < 1 || site.label.length > 80) return false;
  if (!/^[A-Z]{2}$/.test(site.countryCode || "") || site.privacyClass !== "synthetic") return false;
  if (!hasExactKeys(site.anchor, ["latitude", "longitude", "accuracyMeters", "source"])) return false;
  if (!finiteBetween(site.anchor.latitude, -90, 90) || !finiteBetween(site.anchor.longitude, -180, 180)) return false;
  if (!finiteBetween(site.anchor.accuracyMeters, 1000, 1000000) || site.anchor.source !== "fixture") return false;
  if (!new Set(["online", "offline", "unknown"]).has(site.availability)) return false;
  return Array.isArray(site.capabilities)
    && site.capabilities.length <= 12
    && site.capabilities.every(validId);
}

export function validateEnvironment(environment) {
  if (!hasExactKeys(environment, ["network", "provider", "core", "ai", "siteHealth"]) || hasForbiddenKey(environment, { preciseGeo: true })) return false;
  if (!["network", "provider", "core", "ai"].every((key) => ENV_VALUES[key].has(environment[key]))) return false;
  if (!isPlainObject(environment.siteHealth)) return false;
  return Object.entries(environment.siteHealth).every(([siteId, status]) => (
    validId(siteId) && new Set(["online", "offline", "unknown", "stale"]).has(status)
  ));
}

function validateHostPayload(type, payload) {
  switch (type) {
    case HOST_TO_FRAME.INIT:
      return hasExactKeys(payload, ["sites", "environment", "reducedMotion"])
        && !hasForbiddenKey(payload)
        && Array.isArray(payload.sites)
        && payload.sites.length > 0
        && payload.sites.length <= 12
        && payload.sites.every(validateSyntheticSite)
        && validateEnvironment(payload.environment)
        && typeof payload.reducedMotion === "boolean";
    case HOST_TO_FRAME.SET_SITES:
      return hasExactKeys(payload, ["sites"])
        && !hasForbiddenKey(payload)
        && Array.isArray(payload.sites)
        && payload.sites.length > 0
        && payload.sites.length <= 12
        && payload.sites.every(validateSyntheticSite);
    case HOST_TO_FRAME.SET_ENVIRONMENT:
      return hasExactKeys(payload, ["environment"])
        && !hasForbiddenKey(payload, { preciseGeo: true })
        && validateEnvironment(payload.environment);
    case HOST_TO_FRAME.SET_REDUCED_MOTION:
      return hasExactKeys(payload, ["reducedMotion"])
        && !hasForbiddenKey(payload, { preciseGeo: true })
        && typeof payload.reducedMotion === "boolean";
    case HOST_TO_FRAME.NAVIGATE:
      return hasExactKeys(payload, ["intentId", "siteId", "destination", "playback"])
        && !hasForbiddenKey(payload, { preciseGeo: true })
        && validId(payload.intentId)
        && validId(payload.siteId)
        && new Set(["planet", "interior"]).has(payload.destination)
        && new Set(["auto", "manual"]).has(payload.playback);
    case HOST_TO_FRAME.ADVANCE_JOURNEY:
      return hasExactKeys(payload, [], ["intentId"])
        && !hasForbiddenKey(payload, { preciseGeo: true })
        && (payload.intentId === undefined || validId(payload.intentId));
    case HOST_TO_FRAME.CANCEL_JOURNEY:
      return hasExactKeys(payload, ["intentId"])
        && !hasForbiddenKey(payload, { preciseGeo: true })
        && validId(payload.intentId);
    case HOST_TO_FRAME.REQUEST_SNAPSHOT:
      return hasExactKeys(payload, []);
    default:
      return false;
  }
}

function validateFramePayload(type, payload) {
  if (!isPlainObject(payload) || hasForbiddenKey(payload, { preciseGeo: true })) return false;
  switch (type) {
    case FRAME_TO_HOST.READY:
      return hasExactKeys(payload, ["adapterId", "fixtureOnly"])
        && validId(payload.adapterId)
        && payload.fixtureOnly === true;
    case FRAME_TO_HOST.STATE:
    case FRAME_TO_HOST.SNAPSHOT:
      return hasExactKeys(payload, [
        "revision",
        "selectedSiteId",
        "focusedSiteId",
        "scale",
        "owner",
        "reducedMotion",
        "environment",
        "sites",
        "camera",
        "journey",
        "announcement",
      ])
        && Number.isSafeInteger(payload.revision)
        && payload.revision >= 0
        && validId(payload.selectedSiteId)
        && validId(payload.focusedSiteId)
        && validId(payload.scale)
        && new Set(["world", "coordinator", "interior-placeholder"]).has(payload.owner)
        && typeof payload.reducedMotion === "boolean"
        && validateEnvironment(payload.environment)
        && Array.isArray(payload.sites)
        && payload.sites.length > 0
        && payload.sites.length <= 12
        && payload.sites.every((site) => (
          hasExactKeys(site, ["id", "label", "countryCode", "availability"])
          && validId(site.id)
          && typeof site.label === "string"
          && site.label.length > 0
          && site.label.length <= 80
          && /^[A-Z]{2}$/.test(site.countryCode)
          && new Set(["online", "offline", "unknown", "stale"]).has(site.availability)
        ))
        && hasExactKeys(payload.camera, ["heightKm", "pitchDeg", "bearingDeg"])
        && finiteBetween(payload.camera.heightKm, 0, 25000)
        && finiteBetween(payload.camera.pitchDeg, -90, 90)
        && finiteBetween(payload.camera.bearingDeg, -360, 360)
        && hasExactKeys(payload.journey, [
          "status",
          "intentId",
          "destination",
          "playback",
          "elapsedMs",
          "durationMs",
          "keyframeIndex",
          "progress",
        ])
        && new Set(["idle", "running", "completed", "cancelled"]).has(payload.journey.status)
        && (payload.journey.intentId === null || validId(payload.journey.intentId))
        && new Set(["planet", "interior"]).has(payload.journey.destination)
        && new Set(["auto", "manual"]).has(payload.journey.playback)
        && finiteBetween(payload.journey.elapsedMs, 0, 6000)
        && finiteBetween(payload.journey.durationMs, 0, 6000)
        && Number.isSafeInteger(payload.journey.keyframeIndex)
        && finiteBetween(payload.journey.keyframeIndex, 0, 7)
        && finiteBetween(payload.journey.progress, 0, 1)
        && typeof payload.announcement === "string"
        && payload.announcement.length <= 160;
    case FRAME_TO_HOST.EVENT:
      return hasExactKeys(payload, ["kind"], ["siteId", "intentId", "scale"])
        && validId(payload.kind)
        && (payload.siteId === undefined || validId(payload.siteId))
        && (payload.intentId === undefined || validId(payload.intentId))
        && (payload.scale === undefined || validId(payload.scale));
    case FRAME_TO_HOST.ERROR:
      return hasExactKeys(payload, ["code"])
        && FRAME_ERROR_CODE_VALUES.has(payload.code);
    default:
      return false;
  }
}

export function createEnvelope(type, requestId, payload = {}) {
  if (!validId(requestId)) throw new TypeError("requestId must be an opaque protocol identifier");
  return Object.freeze({ protocol: PROTOCOL_VERSION, type, requestId, payload });
}

export function parseEnvelope(candidate, direction) {
  if (!hasExactKeys(candidate, ["protocol", "type", "requestId", "payload"]) || candidate.protocol !== PROTOCOL_VERSION || !validId(candidate.requestId)) {
    return { ok: false, code: "invalid-envelope" };
  }
  if (direction !== "host-to-frame" && direction !== "frame-to-host") {
    return { ok: false, code: "invalid-direction" };
  }
  const allowedTypes = direction === "host-to-frame" ? HOST_TYPES : FRAME_TYPES;
  if (!allowedTypes.has(candidate.type)) return { ok: false, code: "invalid-type" };
  const validPayload = direction === "host-to-frame"
    ? validateHostPayload(candidate.type, candidate.payload)
    : validateFramePayload(candidate.type, candidate.payload);
  if (!validPayload) return { ok: false, code: "invalid-payload" };
  return { ok: true, value: candidate };
}

export function createConnectionEnvelope(requestId) {
  return Object.freeze({
    protocol: PROTOCOL_VERSION,
    type: CONNECTION_TYPE,
    requestId,
    payload: Object.freeze({ transport: "message-port" }),
  });
}

export function isConnectionEnvelope(candidate) {
  return Boolean(
    hasExactKeys(candidate, ["protocol", "type", "requestId", "payload"])
    && candidate.protocol === PROTOCOL_VERSION
    && candidate.type === CONNECTION_TYPE
    && validId(candidate.requestId)
    && hasExactKeys(candidate.payload, ["transport"])
    && candidate.payload.transport === "message-port",
  );
}

export function summarizeEnvelope(envelope) {
  const summary = { type: envelope.type, requestId: envelope.requestId };
  if (validId(envelope.payload?.siteId)) summary.siteId = envelope.payload.siteId;
  if (validId(envelope.payload?.intentId)) summary.intentId = envelope.payload.intentId;
  if (validId(envelope.payload?.kind)) summary.kind = envelope.payload.kind;
  if (validId(envelope.payload?.scale)) summary.scale = envelope.payload.scale;
  return summary;
}
