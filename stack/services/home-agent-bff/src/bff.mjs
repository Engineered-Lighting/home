import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { HaUnavailableError, asTokenBuffer, exchangeCode, refreshAccessToken, revokeRefreshToken, fetchHaSubject } from "./ha-token-transport.mjs";
import { QualifiedHaAuth } from "./qualified-ha-auth.mjs";
import { EchoLinkReview } from "./echo-link-review.mjs";
import { EchoLinkStart } from "./echo-link-start.mjs";
import { PersonalMemoryClient } from "./personal-memory-client.mjs";
import { LightingClient, LIGHTING_OPERATIONS } from "./lighting-client.mjs";
import { SharedSessionRevocationOutbox } from "./shared-session-revocation-outbox.mjs";

const COOKIE_NAME = "__Host-home_agent";
const LEGACY_HA_ISSUER_ID = "home-assistant:echo";
const LEGACY_SITE_ID = "echo";

function issuerBinding(config) {
  return {
    haIssuerId: config.haIssuerId === undefined ? LEGACY_HA_ISSUER_ID : config.haIssuerId,
    siteId: config.siteId === undefined ? LEGACY_SITE_ID : config.siteId,
  };
}

function legacyIssuerConfigurationValid(config) {
  const { haIssuerId, siteId } = issuerBinding(config);
  return haIssuerId === LEGACY_HA_ISSUER_ID && siteId === LEGACY_SITE_ID;
}

function sessionOriginsJson(origins) {
  if (!(origins instanceof Set) || [...origins].some((origin) => typeof origin !== "string")) {
    throw new Error("session origins require a provisioned set");
  }
  return JSON.stringify([...origins].sort());
}
const OAUTH_COOKIE_NAME = "__Host-home_agent_oauth";
const MAX_BODY_BYTES = 64 * 1024;
const MAX_CORE_RESPONSE_BYTES = 1024 * 1024;
const REQUEST_TIMEOUT_MS = 10_000;
const SESSION_KEY_BYTES = 32;
const SESSION_IV_BYTES = 12;
const SESSION_TAG_BYTES = 16;
const MAX_HA_TOKEN_BYTES = 64 * 1024;
const SESSION_ENVELOPE_VERSION = 1;
const DEFAULT_CLEANUP_INTERVAL_MS = 60_000;
const DEFAULT_CLEANUP_BATCH_SIZE = 10;
const REVOCATION_RETRY_BASE_MS = 30_000;
const REVOCATION_RETRY_MAX_MS = 60 * 60_000;
const NATIVE_CHALLENGE_PATH = "/api/agent/native/v1/attestation/challenge";
const NATIVE_PROOF_TYPE = "home-agent-native+jwt";
const NATIVE_ATTESTED_CHANNEL = "private_tauri_attested_v1";
const NATIVE_CHALLENGE_TTL_SECONDS = 60;
const MAX_NATIVE_PROOF_BYTES = 8 * 1024;
const MAX_NATIVE_REGISTRY_BYTES = 256 * 1024;
const MAX_NATIVE_CHALLENGES = 1_024;
const MAX_NATIVE_CHALLENGES_PER_INSTALLATION = 16;
const MAX_NATIVE_USED_JTIS = 4_096;
const EMPTY_BODY_SHA256 = crypto.createHash("sha256").update(Buffer.alloc(0)).digest("base64url");
const PRINCIPAL_BINDING_CONFIRM_PATH = "/api/agent/v1/principal-binding-proposal/confirm";
const PRINCIPAL_BINDING_WRITE_PATHS = new Set([
  "/api/agent/v1/principal-binding-request",
  "/api/agent/v1/principal-binding-request/cancel",
  PRINCIPAL_BINDING_CONFIRM_PATH,
]);
const PRINCIPAL_BINDING_FRESH_AUTH_ROUTES = new Set([
  "GET /api/agent/v1/principal-binding-proposal",
  "POST /api/agent/v1/principal-binding-request",
  "POST /api/agent/v1/principal-binding-request/cancel",
  `POST ${PRINCIPAL_BINDING_CONFIRM_PATH}`,
]);
const PARENT_RELATIONSHIP_STAGE_PATH =
  "/api/agent/v1/parent-relationship-proposal";
const PARENT_RELATIONSHIP_CONFIRM_PATH =
  "/api/agent/v1/parent-relationship-proposal/confirm";
const PARENT_RELATIONSHIP_WRITE_PATHS = new Set([
  PARENT_RELATIONSHIP_STAGE_PATH,
  PARENT_RELATIONSHIP_CONFIRM_PATH,
]);
const PARENT_RELATIONSHIP_FRESH_AUTH_ROUTES = new Set([
  `GET ${PARENT_RELATIONSHIP_STAGE_PATH}`,
  `POST ${PARENT_RELATIONSHIP_STAGE_PATH}`,
  `POST ${PARENT_RELATIONSHIP_CONFIRM_PATH}`,
]);

const UUID_PATH = "[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}";
const ROUTES = Object.freeze([
  ["GET", /^\/api\/agent\/v1\/onboarding\/status$/],
  ["GET", /^\/api\/agent\/v1\/principal-binding-proposal$/],
  ["POST", /^\/api\/agent\/v1\/principal-binding-request$/],
  ["POST", /^\/api\/agent\/v1\/principal-binding-request\/cancel$/],
  ["POST", /^\/api\/agent\/v1\/principal-binding-proposal\/confirm$/],
  ["GET", /^\/api\/agent\/v1\/parent-relationship-proposal$/],
  ["POST", /^\/api\/agent\/v1\/parent-relationship-proposal$/],
  ["POST", /^\/api\/agent\/v1\/parent-relationship-proposal\/confirm$/],
  ["GET", /^\/api\/agent\/v1\/household$/],
  ["GET", /^\/api\/agent\/v1\/relationships$/],
  ["POST", /^\/api\/agent\/v1\/partner-attestation$/],
  ["POST", /^\/api\/agent\/v1\/household-person$/],
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

// Native routes are deliberately duplicated instead of derived from ROUTES:
// adding a browser capability must never silently grant it to a desktop HA
// bearer. Keep this list in lock-step with Rust AgentOperation and gateway
// NATIVE_AGENT_ROUTES through contract tests.
const NATIVE_ROUTES = Object.freeze([
  ["GET", /^\/api\/agent\/v1\/snapshot$/],
  ["GET", /^\/api\/agent\/v1\/initiatives$/],
  ["POST", new RegExp(`^/api/agent/v1/initiatives/${UUID_PATH}/claim$`, "i")],
  ["GET", /^\/api\/agent\/v1\/private-localities$/],
  ["POST", /^\/api\/agent\/v1\/private-localities\/(preview|confirm)$/],
  ["GET", new RegExp(`^/api/agent/v1/places/${UUID_PATH}/descriptor-relationship$`, "i")],
  ["GET", new RegExp(`^/api/agent/v1/places/${UUID_PATH}/parents/current-presence$`, "i")],
  ["POST", /^\/api\/agent\/v1\/memory-transactions$/],
  ["GET", new RegExp(`^/api/agent/v1/memory-transactions/${UUID_PATH}$`, "i")],
  ["POST", new RegExp(`^/api/agent/v1/memory-transactions/${UUID_PATH}/confirm$`, "i")],
  ["PUT", /^\/api\/agent\/v1\/preferences\/(location_memory|travel_greetings)$/],
  ["POST", new RegExp(`^/api/agent/v1/facts/${UUID_PATH}/(correction-preview|retraction-preview|forget-preview)$`, "i")],
  ["POST", new RegExp(`^/api/agent/v1/descriptor-(corrections|retractions)/${UUID_PATH}/confirm$`, "i")],
  ["POST", new RegExp(`^/api/agent/v1/erasure-requests/${UUID_PATH}/confirm$`, "i")],
]);

function randomToken(bytes = 32) {
  return crypto.randomBytes(bytes).toString("base64url");
}

function timingSafeEqual(a, b) {
  const left = Buffer.from(String(a || ""));
  const right = Buffer.from(String(b || ""));
  return left.length === right.length && crypto.timingSafeEqual(left, right);
}

function parseCookies(header) {
  const out = {};
  for (const pair of String(header || "").split(";")) {
    const index = pair.indexOf("=");
    if (index <= 0) continue;
    try {
      out[pair.slice(0, index).trim()] = decodeURIComponent(pair.slice(index + 1).trim());
    } catch {
      // A malformed attacker-controlled cookie is ignored, never allowed to
      // tear down the request handler process.
    }
  }
  return out;
}

function json(res, status, payload, headers = {}) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    ...headers,
  });
  res.end(body);
}

function redirect(res, location, headers = {}) {
  res.writeHead(302, {
    Location: location,
    "Cache-Control": "no-store",
    // The HA authorization code arrives in the callback query. Never allow a
    // subsequent same-origin navigation to inherit that URL as its Referer.
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    ...headers,
  });
  res.end();
}

function isAllowedOrigin(origin, allowedOrigins) {
  if (!origin) return false;
  try {
    const normalized = new URL(origin).origin;
    return allowedOrigins.has(normalized) && normalized === origin;
  } catch {
    return false;
  }
}

function safePostLoginRedirect(value) {
  const candidate = String(value || "");
  if (
    !candidate.startsWith("/") || candidate.startsWith("//") ||
    candidate.length > 2_048 || candidate.includes("\\") ||
    /[\u0000-\u001f\u007f]/.test(candidate)
  ) return null;
  try {
    const parsed = new URL(candidate, "https://home-agent.invalid");
    if (
      parsed.origin !== "https://home-agent.invalid" ||
      parsed.search || parsed.hash || parsed.pathname !== candidate
    ) return null;
    return candidate;
  } catch {
    return null;
  }
}

function routeAllowed(method, pathname) {
  return ROUTES.some(([allowedMethod, pattern]) =>
    method === allowedMethod && pattern.test(pathname));
}

function nativeRouteAllowed(method, pathname) {
  const browserPath = pathname.replace(/^\/api\/agent\/native/, "/api/agent");
  return browserPath !== pathname && NATIVE_ROUTES.some(([allowedMethod, pattern]) =>
    method === allowedMethod && pattern.test(browserPath));
}

function normalizePrincipalBindingBody(pathname, body) {
  if (!PRINCIPAL_BINDING_WRITE_PATHS.has(pathname)) return body;
  let value;
  try {
    value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body));
  } catch {
    const error = new Error("invalid principal binding request body");
    error.status = 400;
    error.code = "invalid_request_body";
    throw error;
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    const error = new Error("invalid principal binding request body");
    error.status = 400;
    error.code = "invalid_request_body";
    throw error;
  }
  const keys = Object.keys(value).sort();
  if (pathname !== PRINCIPAL_BINDING_CONFIRM_PATH) {
    if (keys.length !== 0) {
      const error = new Error("principal binding request accepts no identity fields");
      error.status = 400;
      error.code = "invalid_request_body";
      throw error;
    }
    return Buffer.from("{}");
  }
  if (
    keys.length !== 2 || keys[0] !== "confirmation_nonce" || keys[1] !== "proposal_digest" ||
    typeof value.proposal_digest !== "string" ||
    typeof value.confirmation_nonce !== "string" ||
    !/^[0-9a-f]{64}$/.test(value.proposal_digest) ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(
      value.confirmation_nonce,
    )
  ) {
    const error = new Error("invalid principal binding confirmation body");
    error.status = 400;
    error.code = "invalid_request_body";
    throw error;
  }
  return Buffer.from(JSON.stringify({
    proposal_digest: value.proposal_digest,
    confirmation_nonce: value.confirmation_nonce,
  }));
}

function normalizeParentRelationshipBody(pathname, body) {
  if (!PARENT_RELATIONSHIP_WRITE_PATHS.has(pathname)) return body;
  let value;
  try {
    value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body));
  } catch {
    const error = new Error("invalid parent relationship request body");
    error.status = 400;
    error.code = "invalid_request_body";
    throw error;
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    const error = new Error("invalid parent relationship request body");
    error.status = 400;
    error.code = "invalid_request_body";
    throw error;
  }
  const keys = Object.keys(value).sort();
  const uuid7 = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  if (pathname === PARENT_RELATIONSHIP_STAGE_PATH) {
    if (
      keys.length !== 1 || keys[0] !== "ceremony_id" ||
      typeof value.ceremony_id !== "string" ||
      !uuid7.test(value.ceremony_id)
    ) {
      const error = new Error("invalid parent relationship preview body");
      error.status = 400;
      error.code = "invalid_request_body";
      throw error;
    }
    return Buffer.from(JSON.stringify({ ceremony_id: value.ceremony_id }));
  }
  const uuid4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  if (
    keys.length !== 3 ||
    keys[0] !== "confirmation_nonce" ||
    keys[1] !== "proposal_digest" ||
    keys[2] !== "proposal_id" ||
    typeof value.proposal_id !== "string" ||
    typeof value.proposal_digest !== "string" ||
    typeof value.confirmation_nonce !== "string" ||
    !uuid7.test(value.proposal_id) ||
    !/^[0-9a-f]{64}$/.test(value.proposal_digest) ||
    !uuid4.test(value.confirmation_nonce)
  ) {
    const error = new Error("invalid parent relationship confirmation body");
    error.status = 400;
    error.code = "invalid_request_body";
    throw error;
  }
  return Buffer.from(JSON.stringify({
    proposal_id: value.proposal_id,
    proposal_digest: value.proposal_digest,
    confirmation_nonce: value.confirmation_nonce,
  }));
}

function nativeBearer(header) {
  const match = /^Bearer ([A-Za-z0-9._~+\/-]{16,8192})$/.exec(String(header || ""));
  return match?.[1] || "";
}

function exactObjectKeys(value, expected) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  return actual.length === wanted.length && actual.every((key, index) => key === wanted[index]);
}

function canonicalBase64url(value, bytes) {
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]+$/.test(value)) return false;
  try {
    const decoded = Buffer.from(value, "base64url");
    return decoded.length === bytes && decoded.toString("base64url") === value;
  } catch {
    return false;
  }
}

function installationIdValid(value) {
  return typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value);
}

function haUserIdValid(value) {
  return typeof value === "string" && value.length >= 1 && value.length <= 64 &&
    !/[\u0000-\u001f\u007f]/.test(value);
}

function jsonHasDuplicateObjectKeys(raw) {
  let index = 0;
  let duplicate = false;
  const whitespace = () => {
    while (/[\t\n\r ]/.test(raw[index] || "")) index += 1;
  };
  const stringToken = () => {
    const start = index;
    index += 1;
    while (index < raw.length) {
      if (raw[index] === "\\") {
        index += 2;
      } else if (raw[index] === '"') {
        index += 1;
        return raw.slice(start, index);
      } else {
        index += 1;
      }
    }
    throw new Error("unterminated JSON string");
  };
  const value = () => {
    whitespace();
    if (raw[index] === "{") {
      index += 1;
      whitespace();
      const keys = new Set();
      if (raw[index] === "}") { index += 1; return; }
      while (index < raw.length) {
        whitespace();
        if (raw[index] !== '"') throw new Error("invalid JSON object key");
        const key = JSON.parse(stringToken());
        if (keys.has(key)) duplicate = true;
        keys.add(key);
        whitespace();
        if (raw[index] !== ":") throw new Error("invalid JSON object separator");
        index += 1;
        value();
        whitespace();
        if (raw[index] === "}") { index += 1; return; }
        if (raw[index] !== ",") throw new Error("invalid JSON object delimiter");
        index += 1;
      }
      throw new Error("unterminated JSON object");
    }
    if (raw[index] === "[") {
      index += 1;
      whitespace();
      if (raw[index] === "]") { index += 1; return; }
      while (index < raw.length) {
        value();
        whitespace();
        if (raw[index] === "]") { index += 1; return; }
        if (raw[index] !== ",") throw new Error("invalid JSON array delimiter");
        index += 1;
      }
      throw new Error("unterminated JSON array");
    }
    if (raw[index] === '"') {
      stringToken();
      return;
    }
    while (index < raw.length && !/[\t\n\r ,\]}]/.test(raw[index])) index += 1;
  };
  try {
    value();
    whitespace();
    return index !== raw.length || duplicate;
  } catch {
    return true;
  }
}

function loadNativeInstallationRegistry(filePath) {
  const candidate = String(filePath || "").trim();
  if (!candidate || !path.isAbsolute(candidate)) return null;
  let raw;
  let encoded;
  try {
    const stat = fs.lstatSync(candidate);
    if (
      stat.isSymbolicLink() || !stat.isFile() || stat.size <= 0 ||
      stat.size > MAX_NATIVE_REGISTRY_BYTES
    ) return null;
    encoded = fs.readFileSync(candidate);
    raw = new TextDecoder("utf-8", { fatal: true }).decode(encoded);
  } catch {
    return null;
  } finally {
    encoded?.fill(0);
  }
  let value;
  try { value = JSON.parse(raw); }
  catch { return null; }
  if (jsonHasDuplicateObjectKeys(raw)) return null;
  if (
    !exactObjectKeys(value, ["schema_version", "installations"]) ||
    value.schema_version !== 1 || !Array.isArray(value.installations) ||
    value.installations.length > 1_024
  ) return null;

  const installations = new Map();
  const publicPoints = new Set();
  for (const item of value.installations) {
    if (!exactObjectKeys(item, [
      "installation_id", "ha_user_id", "status", "public_key_jwk",
    ])) return null;
    if (
      !installationIdValid(item.installation_id) || !haUserIdValid(item.ha_user_id) ||
      !["active", "revoked"].includes(item.status) ||
      installations.has(item.installation_id)
    ) return null;
    const jwk = item.public_key_jwk;
    if (
      !exactObjectKeys(jwk, ["kty", "crv", "x", "y", "kid"]) ||
      jwk.kty !== "EC" || jwk.crv !== "P-256" || jwk.kid !== item.installation_id ||
      !canonicalBase64url(jwk.x, 32) || !canonicalBase64url(jwk.y, 32)
    ) return null;
    const publicPoint = `${jwk.x}.${jwk.y}`;
    if (publicPoints.has(publicPoint)) return null;
    let publicKey;
    try {
      publicKey = crypto.createPublicKey({ key: jwk, format: "jwk" });
      if (
        publicKey.asymmetricKeyType !== "ec" ||
        publicKey.asymmetricKeyDetails?.namedCurve !== "prime256v1"
      ) return null;
    } catch {
      return null;
    }
    installations.set(item.installation_id, Object.freeze({
      installationId: item.installation_id,
      haUserId: item.ha_user_id,
      status: item.status,
      publicKey,
    }));
    publicPoints.add(publicPoint);
  }
  return installations;
}

function nativeAttestationError(code, status = 401) {
  return Object.assign(new Error(code), { code, status });
}

function requestBodySha256(body) {
  return crypto.createHash("sha256").update(body).digest("base64url");
}

function parseNativeChallengeBody(body, clientId) {
  let value;
  let text;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(body);
    value = JSON.parse(text);
    if (jsonHasDuplicateObjectKeys(text)) throw new Error("duplicate challenge field");
  } catch {
    throw nativeAttestationError("native_challenge_invalid", 400);
  }
  if (!exactObjectKeys(value, [
    "installation_id", "method", "path", "htu", "body_sha256",
  ])) {
    throw nativeAttestationError("native_challenge_invalid", 400);
  }
  if (
    !installationIdValid(value.installation_id) ||
    !["GET", "POST", "PUT"].includes(value.method) ||
    typeof value.path !== "string" || value.path.includes("?") || value.path.includes("#") ||
    !nativeRouteAllowed(value.method, value.path) ||
    typeof value.htu !== "string" || value.htu !== `${clientId}${value.path}` ||
    !canonicalBase64url(value.body_sha256, 32) ||
    (value.method === "GET" && value.body_sha256 !== EMPTY_BODY_SHA256)
  ) throw nativeAttestationError("native_challenge_invalid", 400);
  return value;
}

function decodeCanonicalJwsJson(segment, maximumBytes) {
  if (typeof segment !== "string" || !/^[A-Za-z0-9_-]+$/.test(segment)) {
    throw nativeAttestationError("native_attestation_invalid");
  }
  let bytes;
  let text;
  let value;
  try {
    bytes = Buffer.from(segment, "base64url");
    if (
      bytes.length === 0 || bytes.length > maximumBytes ||
      bytes.toString("base64url") !== segment
    ) throw new Error("non-canonical segment");
    text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    value = JSON.parse(text);
    // Compact canonical JSON makes duplicate properties and alternate parser
    // representations fail closed before any attacker-controlled key lookup.
    if (JSON.stringify(value) !== text) throw new Error("non-canonical JSON");
    return value;
  } catch {
    throw nativeAttestationError("native_attestation_invalid");
  } finally {
    bytes?.fill(0);
  }
}

function parseNativeProof(proof) {
  if (typeof proof !== "string" || proof.length === 0) {
    throw nativeAttestationError("native_attestation_required");
  }
  if (Buffer.byteLength(proof) > MAX_NATIVE_PROOF_BYTES) {
    throw nativeAttestationError("native_attestation_invalid");
  }
  const segments = proof.split(".");
  if (segments.length !== 3 || segments.some((segment) => !segment)) {
    throw nativeAttestationError("native_attestation_invalid");
  }
  const header = decodeCanonicalJwsJson(segments[0], 1_024);
  const claims = decodeCanonicalJwsJson(segments[1], 4_096);
  if (
    !exactObjectKeys(header, ["alg", "typ", "kid"]) ||
    header.alg !== "ES256" || header.typ !== NATIVE_PROOF_TYPE ||
    !installationIdValid(header.kid)
  ) throw nativeAttestationError("native_attestation_invalid");
  if (!exactObjectKeys(claims, [
    "v", "jti", "nonce", "iat", "exp", "htm", "htu", "path", "body_sha256", "ath",
  ])) throw nativeAttestationError("native_attestation_invalid");
  if (
    claims.v !== 1 || !installationIdValid(claims.jti) ||
    !canonicalBase64url(claims.nonce, 32) ||
    !Number.isSafeInteger(claims.iat) || !Number.isSafeInteger(claims.exp) ||
    !["GET", "POST", "PUT"].includes(claims.htm) ||
    typeof claims.htu !== "string" || typeof claims.path !== "string" ||
    !canonicalBase64url(claims.body_sha256, 32) ||
    !canonicalBase64url(claims.ath, 32)
  ) throw nativeAttestationError("native_attestation_invalid");
  let signature;
  try {
    signature = Buffer.from(segments[2], "base64url");
    if (signature.length !== 64 || signature.toString("base64url") !== segments[2]) {
      throw new Error("invalid ES256 signature encoding");
    }
  } catch {
    signature?.fill(0);
    throw nativeAttestationError("native_attestation_invalid");
  }
  return {
    header,
    claims,
    signingInput: Buffer.from(`${segments[0]}.${segments[1]}`),
    signature,
  };
}

class NativeAttestationStore {
  constructor(installations, {
    now = () => Date.now(),
    maxChallenges = MAX_NATIVE_CHALLENGES,
    maxUsedJtis = MAX_NATIVE_USED_JTIS,
  } = {}) {
    this.installations = installations instanceof Map ? installations : null;
    this.now = now;
    this.maxChallenges = maxChallenges;
    this.maxUsedJtis = maxUsedJtis;
    this.challenges = new Map();
    this.usedJtis = new Map();
  }

  get configured() { return this.installations !== null; }

  #purge(nowSeconds) {
    for (const [nonce, challenge] of this.challenges) {
      if (challenge.expiresAt <= nowSeconds) this.challenges.delete(nonce);
    }
    for (const [jti, expiresAt] of this.usedJtis) {
      if (expiresAt <= nowSeconds) this.usedJtis.delete(jti);
    }
  }

  #installation(installationId, principal) {
    if (!this.configured) throw nativeAttestationError("native_attestation_unavailable", 503);
    const installation = this.installations.get(installationId);
    if (
      !installation || installation.status !== "active" ||
      installation.haUserId !== principal.userId
    ) throw nativeAttestationError("native_attestation_invalid");
    return installation;
  }

  issue(value, principal, accessToken) {
    this.#installation(value.installation_id, principal);
    const nowSeconds = Math.floor(this.now() / 1_000);
    this.#purge(nowSeconds);
    if (this.challenges.size >= this.maxChallenges) {
      throw nativeAttestationError("native_attestation_capacity", 503);
    }
    let installationChallengeCount = 0;
    for (const challenge of this.challenges.values()) {
      if (challenge.installationId === value.installation_id) installationChallengeCount += 1;
    }
    if (installationChallengeCount >= MAX_NATIVE_CHALLENGES_PER_INSTALLATION) {
      throw nativeAttestationError("native_attestation_capacity", 429);
    }
    let nonce = randomToken(32);
    while (this.challenges.has(nonce)) nonce = randomToken(32);
    const expiresAt = nowSeconds + NATIVE_CHALLENGE_TTL_SECONDS;
    this.challenges.set(nonce, Object.freeze({
      installationId: value.installation_id,
      haUserId: principal.userId,
      method: value.method,
      path: value.path,
      htu: value.htu,
      bodySha256: value.body_sha256,
      accessTokenSha256: requestBodySha256(accessToken),
      issuedAt: nowSeconds,
      expiresAt,
    }));
    return { nonce, issued_at: nowSeconds, expires_at: expiresAt };
  }

  verify({ proof, principal, accessToken, method, pathname, htu, body }) {
    const parsed = parseNativeProof(proof);
    try {
      const installation = this.#installation(parsed.header.kid, principal);
      const nowSeconds = Math.floor(this.now() / 1_000);
      this.#purge(nowSeconds);
      const challenge = this.challenges.get(parsed.claims.nonce);
      const bodySha256 = requestBodySha256(body);
      const accessTokenSha256 = requestBodySha256(accessToken);
      if (
        !challenge || challenge.expiresAt <= nowSeconds || challenge.issuedAt > nowSeconds + 1 ||
        challenge.installationId !== installation.installationId ||
        challenge.haUserId !== principal.userId || challenge.method !== method ||
        challenge.path !== pathname || challenge.htu !== htu ||
        !timingSafeEqual(challenge.bodySha256, bodySha256) ||
        !timingSafeEqual(challenge.accessTokenSha256, accessTokenSha256) ||
        parsed.claims.iat !== challenge.issuedAt || parsed.claims.exp !== challenge.expiresAt ||
        parsed.claims.exp <= parsed.claims.iat || parsed.claims.htm !== method ||
        parsed.claims.htu !== htu || parsed.claims.path !== pathname ||
        !timingSafeEqual(parsed.claims.body_sha256, bodySha256) ||
        !timingSafeEqual(parsed.claims.ath, accessTokenSha256) ||
        this.usedJtis.has(parsed.claims.jti)
      ) throw nativeAttestationError("native_attestation_invalid");
      const verified = crypto.verify(
        "sha256",
        parsed.signingInput,
        { key: installation.publicKey, dsaEncoding: "ieee-p1363" },
        parsed.signature,
      );
      if (!verified) throw nativeAttestationError("native_attestation_invalid");
      if (this.usedJtis.size >= this.maxUsedJtis) {
        throw nativeAttestationError("native_attestation_capacity", 503);
      }
      // No await occurs between the replay check and these writes. A nonce
      // and jti therefore have exactly one successful consumer per process.
      this.challenges.delete(parsed.claims.nonce);
      this.usedJtis.set(parsed.claims.jti, challenge.expiresAt);
      return installation.installationId;
    } finally {
      parsed.signingInput.fill(0);
      parsed.signature.fill(0);
    }
  }
}

function secretFromEnv(env, name) {
  const direct = String(env[name] || "").trim();
  if (direct) return direct;
  const file = String(env[`${name}_FILE`] || "").trim();
  if (!file) return "";
  try { return fs.readFileSync(file, "utf8").trim(); }
  catch { return ""; }
}

function decodeSessionEncryptionKey(encoded) {
  const value = String(encoded || "").trim();
  // bootstrap-secrets.sh emits an unpadded base64url key. Keeping one
  // canonical representation prevents permissive base64 decoders from
  // accepting malformed or truncated production keys.
  if (!/^[A-Za-z0-9_-]{43}$/.test(value)) return null;
  const key = Buffer.from(value, "base64url");
  return key.length === SESSION_KEY_BYTES ? key : null;
}

function sessionEncryptionKeyFromEnv(env) {
  const file = String(env.HOME_AGENT_SESSION_ENCRYPTION_KEY_FILE || "").trim();
  let encoded = "";
  if (file) {
    try { encoded = fs.readFileSync(file, "utf8").trim(); }
    catch { return null; }
  } else if (
    env.NODE_ENV === "test" &&
    env.HOME_AGENT_ALLOW_TEST_SESSION_KEY_ENV === "1"
  ) {
    // Direct secret environment variables are deliberately unavailable in
    // production. This exception exists only for hermetic unit tests.
    encoded = String(env.HOME_AGENT_SESSION_ENCRYPTION_KEY || "").trim();
  }
  return decodeSessionEncryptionKey(encoded);
}

function boundedIntegerFromEnv(env, name, fallback, minimum, maximum) {
  const raw = env[name] === undefined || env[name] === "" ? fallback : env[name];
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) return null;
  return value;
}

function insecureTestUrlsAllowed(env) {
  return env.NODE_ENV === "test" && env.HOME_AGENT_ALLOW_INSECURE_TEST_URLS === "1";
}

function exactAllowedOrigins(raw, { allowInsecure = false } = {}) {
  const values = String(raw || "").split(",").map((value) => value.trim()).filter(Boolean);
  if (!values.length) return null;
  const origins = new Set();
  for (const value of values) {
    try {
      const parsed = new URL(value);
      const protocolAllowed = parsed.protocol === "https:" ||
        (allowInsecure && parsed.protocol === "http:");
      if (
        !protocolAllowed || value !== parsed.origin || parsed.username || parsed.password ||
        parsed.pathname !== "/" || parsed.search || parsed.hash || origins.has(parsed.origin)
      ) return null;
      origins.add(parsed.origin);
    } catch {
      return null;
    }
  }
  return origins;
}

function serviceRoot(value, { kind, allowInsecure = false } = {}) {
  try {
    const parsed = new URL(String(value || ""));
    if (
      !["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password ||
      parsed.pathname !== "/" || parsed.search || parsed.hash
    ) return null;
    if (kind === "ha" && parsed.protocol !== "https:") {
      const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(parsed.hostname);
      if (!loopback && !allowInsecure) return null;
    }
    return parsed.origin;
  } catch {
    return null;
  }
}

function oauthClientId(value, allowedOrigins, { allowInsecure = false } = {}) {
  try {
    const parsed = new URL(String(value || ""));
    const protocolAllowed = parsed.protocol === "https:" ||
      (allowInsecure && parsed.protocol === "http:");
    if (
      !protocolAllowed || parsed.username || parsed.password ||
      parsed.pathname !== "/" || parsed.search || parsed.hash ||
      String(value) !== parsed.origin || !allowedOrigins?.has(parsed.origin)
    ) return null;
    return parsed.origin;
  } catch {
    return null;
  }
}

function oauthRedirect(value, allowedOrigins, { allowInsecure = false } = {}) {
  try {
    const parsed = new URL(String(value || ""));
    const protocolAllowed = parsed.protocol === "https:" ||
      (allowInsecure && parsed.protocol === "http:");
    if (
      !protocolAllowed || parsed.username || parsed.password ||
      parsed.pathname !== "/api/agent/auth/callback" || parsed.search || parsed.hash ||
      !allowedOrigins?.has(parsed.origin)
    ) return null;
    return `${parsed.origin}/api/agent/auth/callback`;
  } catch {
    return null;
  }
}

function exactNativePublicOrigin(value) {
  try {
    const parsed = new URL(String(value || ""));
    if (
      parsed.protocol !== "https:" || parsed.username || parsed.password ||
      parsed.pathname !== "/" || parsed.search || parsed.hash ||
      String(value) !== parsed.origin
    ) return null;
    return parsed.origin;
  } catch {
    return null;
  }
}

function endpointConfigurationValid(config) {
  const allowInsecure = config.allowInsecureTestUrls === true;
  const origins = config.allowedOrigins instanceof Set
    ? exactAllowedOrigins([...config.allowedOrigins].join(","), { allowInsecure })
    : null;
  return Boolean(
    legacyIssuerConfigurationValid(config) && origins && origins.size === config.allowedOrigins.size &&
    serviceRoot(config.haUrl, { kind: "ha", allowInsecure }) === config.haUrl &&
    serviceRoot(config.coreUrl, { kind: "core", allowInsecure }) === config.coreUrl &&
    oauthClientId(config.clientId, origins, { allowInsecure }) === config.clientId &&
    oauthRedirect(config.redirectUri, origins, { allowInsecure }) === config.redirectUri
  );
}

function configFromEnv(env = process.env) {
  const allowInsecureTestUrls = insecureTestUrlsAllowed(env);
  const allowedOrigins = exactAllowedOrigins(env.HOME_AGENT_ALLOWED_ORIGINS, {
    allowInsecure: allowInsecureTestUrls,
  });
  const sessionDbPath = String(env.HOME_AGENT_SESSION_DB_PATH || "").trim();
  const allowInMemorySessions = env.NODE_ENV === "test" &&
    env.HOME_AGENT_ALLOW_IN_MEMORY_SESSIONS === "1";
  const nativeInstallationsFile = String(env.HOME_AGENT_NATIVE_INSTALLATIONS_FILE || "").trim();
  const nativeInstallations = loadNativeInstallationRegistry(nativeInstallationsFile);
  const nativePublicOrigin = exactNativePublicOrigin(env.HOME_AGENT_NATIVE_PUBLIC_ORIGIN);
  const config = {
    bindHost: env.HOME_AGENT_BFF_HOST || "127.0.0.1",
    port: Number(env.HOME_AGENT_BFF_PORT || 8097),
    // Registered authority is stable when its LAN/Tailscale transport changes.
    // Victoria needs its own reviewed authority ingress; legacy Core is Echo.
    // These labels do not attest a live HA installation. Operators must verify
    // that the provisioned HA endpoint/trust configuration belongs to Echo.
    haIssuerId: env.HOME_AGENT_HA_ISSUER_ID ?? LEGACY_HA_ISSUER_ID,
    siteId: env.HOME_AGENT_SITE_ID ?? LEGACY_SITE_ID,
    allowedOrigins: allowedOrigins || new Set(),
    haUrl: serviceRoot(env.HOME_AGENT_HA_URL, {
      kind: "ha", allowInsecure: allowInsecureTestUrls,
    }),
    clientId: oauthClientId(env.HOME_AGENT_OAUTH_CLIENT_ID, allowedOrigins, {
      allowInsecure: allowInsecureTestUrls,
    }),
    redirectUri: oauthRedirect(env.HOME_AGENT_OAUTH_REDIRECT_URI, allowedOrigins, {
      allowInsecure: allowInsecureTestUrls,
    }),
    postLoginRedirect: safePostLoginRedirect(
      env.HOME_AGENT_POST_LOGIN_REDIRECT || "/home-agent/index.html",
    ),
    coreUrl: serviceRoot(env.HOME_AGENT_CORE_URL, {
      kind: "core", allowInsecure: allowInsecureTestUrls,
    }),
    coreToken: secretFromEnv(env, "HOME_AGENT_CORE_TOKEN"),
    sessionEncryptionKey: sessionEncryptionKeyFromEnv(env),
    sessionDbPath,
    allowInMemorySessions,
    nativeInstallationsFile,
    // Native attestation is an independently fail-closed boundary. A missing
    // or malformed offline registry or dedicated public audience disables
    // native semantic routes without taking browser OAuth or record-only
    // ingest offline.
    nativeInstallations,
    nativePublicOrigin,
    nativeAttestationConfigured: nativeInstallations !== null && nativePublicOrigin !== null,
    allowInsecureTestUrls,
    secureCookie: env.HOME_AGENT_INSECURE_TEST_COOKIE !== "1",
    idleTtlMs: boundedIntegerFromEnv(
      // Defaults stay short; a single-owner hobby install may opt into
      // sessions of up to 400 days, the longest cookie browsers keep. HA
      // access tokens still refresh and the principal is still revalidated.
      env, "HOME_AGENT_SESSION_IDLE_MS", 30 * 60_000, 60_000, 400 * 24 * 60 * 60_000,
    ),
    absoluteTtlMs: boundedIntegerFromEnv(
      env, "HOME_AGENT_SESSION_ABSOLUTE_MS", 12 * 60 * 60_000, 60_000, 400 * 24 * 60 * 60_000,
    ),
    // Re-check HA on every private request by default. A bounded non-zero
    // interval is available for constrained installations, but revocation is
    // otherwise immediate at the semantic boundary.
    principalRevalidateMs: boundedIntegerFromEnv(
      env, "HOME_AGENT_PRINCIPAL_REVALIDATE_MS", 0, 0, 15 * 60_000,
    ),
    sessionCleanupIntervalMs: boundedIntegerFromEnv(
      env, "HOME_AGENT_SESSION_CLEANUP_INTERVAL_MS", DEFAULT_CLEANUP_INTERVAL_MS,
      1_000, 60 * 60_000,
    ),
    sessionCleanupBatchSize: boundedIntegerFromEnv(
      env, "HOME_AGENT_SESSION_CLEANUP_BATCH_SIZE", DEFAULT_CLEANUP_BATCH_SIZE, 1, 100,
    ),
  };
  const sessionPersistenceReady = (
    sessionDbPath && path.isAbsolute(sessionDbPath) && sessionDbPath !== ":memory:"
  ) || allowInMemorySessions;
  config.nativeAttestationConfigured = Boolean(
    config.nativeAttestationConfigured && config.clientId &&
    config.nativePublicOrigin !== config.clientId &&
    !config.allowedOrigins.has(config.nativePublicOrigin)
  );
  config.ready = Boolean(
    legacyIssuerConfigurationValid(config) && config.allowedOrigins.size && config.haUrl && config.clientId &&
    config.redirectUri && config.postLoginRedirect && config.coreUrl && config.coreToken &&
    config.sessionEncryptionKey?.length === SESSION_KEY_BYTES && sessionPersistenceReady &&
    config.idleTtlMs && config.absoluteTtlMs && config.principalRevalidateMs !== null &&
    config.sessionCleanupIntervalMs && config.sessionCleanupBatchSize
  );
  return config;
}


function packTokenBundle(accessToken, refreshToken) {
  let access;
  let refresh;
  try {
    access = asTokenBuffer(accessToken, "HA access token", { required: true });
    refresh = asTokenBuffer(refreshToken, "HA refresh token", { required: true });
    const plaintext = Buffer.allocUnsafe(8 + access.length + refresh.length);
    plaintext.writeUInt32BE(access.length, 0);
    plaintext.writeUInt32BE(refresh.length, 4);
    access.copy(plaintext, 8);
    refresh.copy(plaintext, 8 + access.length);
    return plaintext;
  } finally {
    access?.fill(0);
    refresh?.fill(0);
  }
}

function unpackTokenBundle(plaintext) {
  if (plaintext.length < 8) throw new Error("invalid sealed HA token envelope");
  const accessLength = plaintext.readUInt32BE(0);
  const refreshLength = plaintext.readUInt32BE(4);
  if (
    accessLength === 0 || accessLength > MAX_HA_TOKEN_BYTES ||
    refreshLength > MAX_HA_TOKEN_BYTES ||
    8 + accessLength + refreshLength !== plaintext.length
  ) throw new Error("invalid sealed HA token envelope");
  return {
    accessToken: Buffer.from(plaintext.subarray(8, 8 + accessLength)),
    refreshToken: Buffer.from(plaintext.subarray(8 + accessLength)),
  };
}

function sessionAad(id) {
  return Buffer.from(`home-agent-session:v${SESSION_ENVELOPE_VERSION}:${id}`);
}

function sealTokenBundle(key, id, accessToken, refreshToken) {
  const iv = crypto.randomBytes(SESSION_IV_BYTES);
  const plaintext = packTokenBundle(accessToken, refreshToken);
  try {
    const cipher = crypto.createCipheriv("aes-256-gcm", key, iv, {
      authTagLength: SESSION_TAG_BYTES,
    });
    cipher.setAAD(sessionAad(id));
    return {
      version: SESSION_ENVELOPE_VERSION,
      iv,
      ciphertext: Buffer.concat([cipher.update(plaintext), cipher.final()]),
      tag: cipher.getAuthTag(),
    };
  } finally {
    plaintext.fill(0);
  }
}

function openTokenBundle(key, id, envelope) {
  if (
    envelope?.version !== SESSION_ENVELOPE_VERSION ||
    !Buffer.isBuffer(envelope.iv) || envelope.iv.length !== SESSION_IV_BYTES ||
    !Buffer.isBuffer(envelope.tag) || envelope.tag.length !== SESSION_TAG_BYTES ||
    !Buffer.isBuffer(envelope.ciphertext)
  ) throw new Error("invalid sealed HA token envelope");
  const decipher = crypto.createDecipheriv("aes-256-gcm", key, envelope.iv, {
    authTagLength: SESSION_TAG_BYTES,
  });
  decipher.setAAD(sessionAad(id));
  decipher.setAuthTag(envelope.tag);
  let first;
  let last;
  let plaintext;
  try {
    first = decipher.update(envelope.ciphertext);
    last = decipher.final();
    plaintext = Buffer.concat([first, last]);
    return unpackTokenBundle(plaintext);
  } finally {
    first?.fill(0);
    last?.fill(0);
    plaintext?.fill(0);
  }
}

function destroyEnvelope(envelope) {
  envelope?.iv?.fill(0);
  envelope?.ciphertext?.fill(0);
  envelope?.tag?.fill(0);
}

const QUALIFIED_SESSION_PROFILES = new WeakSet();

// Separate authority composition; never enables the legacy BFF/Core routes.
function createVictoriaSessionStore({ auth, browserOrigin, echoOrigins, sessionDbPath,
  sessionEncryptionKey, idleTtlMs, absoluteTtlMs, sharedSessionRevocation, now = Date.now }) {
  if (!(auth instanceof QualifiedHaAuth)) throw new Error("qualified session auth required");
  const [issuer, site, , clientId, redirectUri] = JSON.parse(auth.binding);
  const origin = new URL(browserOrigin);
  const exactOrigin = (value) => {
    const parsed = new URL(value);
    if (parsed.protocol !== "https:" || parsed.origin !== value || parsed.username || parsed.password) {
      throw new Error("qualified session origin invalid");
    }
    return parsed;
  };
  exactOrigin(browserOrigin);
  if (issuer !== "home-assistant:victoria" || site !== "victoria" ||
      !(echoOrigins instanceof Set) || !echoOrigins.size ||
      [...echoOrigins].some((value) => exactOrigin(value).hostname === origin.hostname) ||
      new URL(clientId).origin !== browserOrigin || new URL(redirectUri).origin !== browserOrigin ||
      typeof sessionDbPath !== "string" || !path.isAbsolute(sessionDbPath) || sessionDbPath === ":memory:" ||
      (fs.existsSync(sessionDbPath) && fs.lstatSync(sessionDbPath).isSymbolicLink()) ||
      !Number.isSafeInteger(idleTtlMs) || idleTtlMs <= 0 ||
      !Number.isSafeInteger(absoluteTtlMs) || absoluteTtlMs < idleTtlMs) {
    throw new Error("qualified Victoria session configuration rejected");
  }
  const config = Object.freeze({ haIssuerId: issuer, siteId: site, haAuthBinding: auth.binding,
    allowedOrigins: new Set([browserOrigin]), principalRevalidateMs: 0 });
  const profile = Object.freeze({ auth, config,
    binding: JSON.stringify(["victoria-link-session-v1", auth.binding, browserOrigin, [...echoOrigins].sort()]) });
  QUALIFIED_SESSION_PROFILES.add(profile);
  const store = new SessionStore({ idleTtlMs, absoluteTtlMs, sessionEncryptionKey, sessionDbPath,
    haIssuerId: issuer, siteId: site, allowedOrigins: config.allowedOrigins, sharedSessionRevocation, now }, profile);
  return Object.freeze({ store, config });
}

class SessionStore {
  #sessionEncryptionKey;
  #db;
  #cleanupTimer;
  #cleanupInFlight = false;
  #closed = false;
  #haIssuerId;
  #siteId;
  #browserOriginsJson;
  #migratingLegacyEcho = false;
  #pendingLogins = new Map();
  #qualifiedProfile;
  #verifiedLogins = new Map();
  #qualifiedLoginsInFlight = 0;
  #qualifiedChecks = new Set();
  #sharedRevocation;
  #sharedSessionOwner;
  #restoredSharedSessions = new Set();

  constructor({
    idleTtlMs,
    absoluteTtlMs,
    sessionEncryptionKey,
    sessionDbPath,
    allowInMemorySessions = false,
    haIssuerId = LEGACY_HA_ISSUER_ID,
    siteId = LEGACY_SITE_ID,
    allowedOrigins = new Set(),
    sharedSessionRevocation,
    now = () => Date.now(),
  } = {}, qualifiedProfile) {
    if (qualifiedProfile !== undefined && !QUALIFIED_SESSION_PROFILES.has(qualifiedProfile)) throw new Error("invalid session profile");
    this.#qualifiedProfile = qualifiedProfile;
    if (!qualifiedProfile && !legacyIssuerConfigurationValid({ haIssuerId, siteId })) {
      throw new Error("issuer has no provisioned legacy authority boundary");
    }
    this.#haIssuerId = haIssuerId;
    this.#siteId = siteId;
    this.#browserOriginsJson = sessionOriginsJson(allowedOrigins);
    if (sessionDbPath && !allowedOrigins.size) throw new Error("persistent sessions require provisioned browser origins");
    if (!Buffer.isBuffer(sessionEncryptionKey) || sessionEncryptionKey.length !== SESSION_KEY_BYTES) {
      throw new Error("a 32-byte BFF session encryption key is required");
    }
    if (sessionDbPath && (!path.isAbsolute(sessionDbPath) || sessionDbPath === ":memory:")) {
      throw new Error("BFF session database path must be an absolute filesystem path");
    }
    if (!sessionDbPath && !allowInMemorySessions) {
      throw new Error("a persistent BFF session database path is required");
    }
    this.idleTtlMs = idleTtlMs;
    this.absoluteTtlMs = absoluteTtlMs;
    this.now = now;
    this.#sessionEncryptionKey = Buffer.from(sessionEncryptionKey);
    this.preauth = new Map();
    this.sessions = new Map();
    this.sessionDbPath = sessionDbPath || ":memory:";
    this.#db = new DatabaseSync(this.sessionDbPath);
    try {
      if (sharedSessionRevocation && !sessionDbPath) throw new Error("shared revocation requires persistent session storage");
      if (sharedSessionRevocation) {
        const ownerPath = `${fs.realpathSync(sessionDbPath)}.owner.sqlite`;
        if (fs.existsSync(ownerPath) && fs.lstatSync(ownerPath).isSymbolicLink()) throw new Error("session ownership path invalid");
        this.#sharedSessionOwner = new DatabaseSync(ownerPath);
        this.#sharedSessionOwner.exec("PRAGMA busy_timeout=0; BEGIN EXCLUSIVE");
      }
      SharedSessionRevocationOutbox.preflight(this.#db,sharedSessionRevocation,haIssuerId,this.#sessionEncryptionKey);
      this.#initializeDatabase();
      if (sharedSessionRevocation) this.#sharedRevocation = new SharedSessionRevocationOutbox(
        this.#db,sharedSessionRevocation,haIssuerId,siteId,now);
      if (sessionDbPath) fs.chmodSync(sessionDbPath, 0o600);
      this.#loadSessions();
    } catch (error) {
      this.#sharedRevocation?.close();
      this.#sharedSessionOwner?.close();
      this.#db.close();
      this.#sessionEncryptionKey.fill(0);
      throw error;
    }
  }

  get haIssuerId() { return this.#haIssuerId; }
  get siteId() { return this.#siteId; }
  get hasSharedSessionRevocation() { return !!this.#sharedRevocation; }
  usesQualifiedConfiguration(config) { return !!this.#qualifiedProfile && config === this.#qualifiedProfile.config; }

  #initializeDatabase() {
    const version = this.#db.prepare("PRAGMA user_version").get().user_version;
    const tables = new Set(this.#db.prepare("SELECT name FROM sqlite_master WHERE type='table'").all().map((row) => row.name));
    if (this.#qualifiedProfile) {
      if (version !== 0) {
        if (version !== 2 || !tables.has("bff_qualified_binding")) throw new Error("qualified sessions cannot adopt legacy storage");
        const marker = this.#db.prepare("SELECT * FROM bff_qualified_binding WHERE id=1").get();
        if (!marker || marker.binding !== this.#qualifiedProfile.binding) throw new Error("qualified session binding changed");
        const probe = openTokenBundle(this.#sessionEncryptionKey, marker.binding, {
          version: 1, iv: Buffer.from(marker.iv), ciphertext: Buffer.from(marker.ciphertext), tag: Buffer.from(marker.tag) });
        try { if (probe.accessToken.toString() !== "qualified-session-v1" || probe.refreshToken.toString() !== "qualified-session-marker") throw new Error("invalid qualified session key probe"); }
        finally { probe.accessToken.fill(0); probe.refreshToken.fill(0); }
      }
    } else if (tables.has("bff_qualified_binding")) throw new Error("qualified sessions cannot enter legacy authority");
    if (![0, 1, 2].includes(version) || (version === 0 && tables.size) ||
        (version === 1 && !tables.has("bff_session")) ||
        (version === 2 && (!tables.has("bff_session") || !tables.has("bff_issuer_binding")))) {
      throw new Error("unsupported BFF session database authority schema");
    }
    if (version !== 0) {
      const expected = ["id:TEXT", "principal_json:TEXT", "envelope_version:INTEGER", "iv:BLOB", "ciphertext:BLOB", "tag:BLOB",
        "access_expires_at:INTEGER", "csrf:TEXT", "created_at:INTEGER", "last_seen_at:INTEGER", "principal_checked_at:INTEGER",
        "state:TEXT", "revocation_reason:TEXT", "revocation_attempts:INTEGER", "next_revocation_at:INTEGER"];
      const columns = this.#db.prepare("PRAGMA table_info(bff_session)").all();
      if (JSON.stringify(columns.map((column) => `${column.name}:${column.type}`)) !== JSON.stringify(expected) ||
          columns[0].pk !== 1 || columns.slice(1).some((column) => column.pk !== 0)) {
        throw new Error("unsupported legacy BFF session table schema");
      }
    }
    this.#migratingLegacyEcho = version === 1;
    this.#db.exec(`
      PRAGMA journal_mode=WAL;
      PRAGMA synchronous=FULL;
      PRAGMA secure_delete=ON;
      PRAGMA busy_timeout=5000;
      CREATE TABLE IF NOT EXISTS bff_session (
        id TEXT PRIMARY KEY,
        principal_json TEXT NOT NULL,
        envelope_version INTEGER NOT NULL,
        iv BLOB NOT NULL,
        ciphertext BLOB NOT NULL,
        tag BLOB NOT NULL,
        access_expires_at INTEGER NOT NULL,
        csrf TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        last_seen_at INTEGER NOT NULL,
        principal_checked_at INTEGER NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('active', 'revocation_pending', 'authority_revoked')),
        revocation_reason TEXT,
        revocation_attempts INTEGER NOT NULL DEFAULT 0,
        next_revocation_at INTEGER NOT NULL DEFAULT 0
      );
      CREATE INDEX IF NOT EXISTS bff_session_revocation_due
        ON bff_session(state, next_revocation_at);
    `);
    this.#db.exec("BEGIN IMMEDIATE");
    try {
      if (this.#qualifiedProfile && version === 0) {
        this.#db.exec("CREATE TABLE bff_qualified_binding (id INTEGER PRIMARY KEY CHECK(id=1),binding TEXT NOT NULL,iv BLOB NOT NULL,ciphertext BLOB NOT NULL,tag BLOB NOT NULL)");
        const probe = sealTokenBundle(this.#sessionEncryptionKey, this.#qualifiedProfile.binding, Buffer.from("qualified-session-v1"), Buffer.from("qualified-session-marker"));
        try { this.#db.prepare("INSERT INTO bff_qualified_binding VALUES(1,?,?,?,?)").run(this.#qualifiedProfile.binding,probe.iv,probe.ciphertext,probe.tag); }
        finally { destroyEnvelope(probe); }
      }
      this.#db.exec(`CREATE TABLE IF NOT EXISTS bff_issuer_binding (
        id INTEGER PRIMARY KEY CHECK(id=1), issuer_id TEXT NOT NULL, site_id TEXT NOT NULL,
        browser_origins_json TEXT NOT NULL
      )`);
      const binding = this.#db.prepare("SELECT issuer_id,site_id,browser_origins_json FROM bff_issuer_binding WHERE id=1").get();
      // Old Echo binaries reset user_version to 1 on startup. A rollback may
      // retain this marker; require the same issuer/origins before re-upgrading.
      if ((version === 2 && !binding) || (binding && (binding.issuer_id !== this.#haIssuerId ||
          binding.site_id !== this.#siteId || binding.browser_origins_json !== this.#browserOriginsJson))) {
        throw new Error("session database issuer binding changed");
      }
      this.#db.prepare("INSERT OR IGNORE INTO bff_issuer_binding VALUES(1,?,?,?)").run(this.#haIssuerId, this.#siteId, this.#browserOriginsJson);
      if (this.#migratingLegacyEcho) {
        // Only a v1 database from the previously single-Echo BFF is eligible.
        // Keep encrypted token envelopes/revocation queues intact. Migrate the
        // authenticated subject metadata atomically with the database binding.
        for (const row of this.#db.prepare("SELECT * FROM bff_session").all()) {
          // A migrated database must carry envelopes authenticated by this
          // deployment's existing key, not merely a similarly named table.
          const tokens = openTokenBundle(this.#sessionEncryptionKey, row.id, {
            version: Number(row.envelope_version), iv: Buffer.from(row.iv),
            ciphertext: Buffer.from(row.ciphertext), tag: Buffer.from(row.tag),
          });
          tokens.accessToken.fill(0);
          tokens.refreshToken.fill(0);
          let principal;
          try { principal = JSON.parse(row.principal_json); } catch { continue; }
          if (principal && ((principal.haIssuerId !== undefined && principal.haIssuerId !== this.#haIssuerId) ||
              (principal.siteId !== undefined && principal.siteId !== this.#siteId))) {
            throw new Error("legacy session contains a contradictory issuer binding");
          }
          if (principal && typeof principal === "object" && !Array.isArray(principal) &&
              principal.haIssuerId === undefined && principal.siteId === undefined) {
            principal.haIssuerId = this.#haIssuerId;
            principal.siteId = this.#siteId;
            this.#db.prepare("UPDATE bff_session SET principal_json=? WHERE id=?").run(JSON.stringify(principal), row.id);
          }
        }
      }
      this.#db.exec("PRAGMA user_version=2; COMMIT");
    } catch (error) {
      this.#db.exec("ROLLBACK");
      throw error;
    }
  }

  #loadSessions() {
    const rows = this.#db.prepare("SELECT * FROM bff_session").all();
    const now = this.now();
    for (const row of rows) {
      let principal;
      let state = String(row.state || "revocation_pending");
      try { principal = JSON.parse(String(row.principal_json)); }
      catch { principal = null; }
      if (principal && ((principal.haIssuerId !== undefined && principal.haIssuerId !== this.#haIssuerId) ||
          (principal.siteId !== undefined && principal.siteId !== this.#siteId))) {
        throw new Error("persisted session issuer binding changed");
      }
      if (!principal || typeof principal.userId !== "string" || !principal.userId ||
          principal.haIssuerId !== this.#haIssuerId || principal.siteId !== this.#siteId) {
        principal = { userId: "", isAdmin: false, isActive: false,
          haIssuerId: this.#haIssuerId, siteId: this.#siteId };
        if (state !== "authority_revoked") state = "revocation_pending";
      }
      if (!new Set(["active", "revocation_pending", "authority_revoked"]).has(state)) {
        state = "revocation_pending";
      }
      const id = String(row.id || "");
      // A session already linked before the restart keeps linking (owner
      // decision). One restored without an armed record, such as a snapshot
      // taken before linking, still needs a new login before it may link.
      if (this.#sharedRevocation && !this.#sharedRevocation.armed(id)) this.#restoredSharedSessions.add(id);
      if (this.#sharedRevocation?.retired(id) && state === "active") state = "revocation_pending";
      const session = {
        principal,
        sealedTokens: {
          version: Number(row.envelope_version),
          iv: Buffer.from(row.iv),
          ciphertext: Buffer.from(row.ciphertext),
          tag: Buffer.from(row.tag),
        },
        accessExpiresAt: Number(row.access_expires_at),
        csrf: String(row.csrf || ""),
        createdAt: Number(row.created_at),
        lastSeenAt: Number(row.last_seen_at),
        principalCheckedAt: Number(row.principal_checked_at),
        state,
        revocationReason: row.revocation_reason === null
          ? null
          : String(row.revocation_reason),
        revocationAttempts: Number(row.revocation_attempts) || 0,
        nextRevocationAt: Number(row.next_revocation_at) || 0,
      };
      this.sessions.set(id, session);
      if (session.state === "active" && this.#isExpired(session, now)) {
        this.scheduleRevocation(id, session, "expired", now);
      } else if (state !== String(row.state)) {
        this.#persist(id, session);
      }
    }
  }

  #persist(id, session) {
    const atomicRevocation = !!this.#sharedRevocation && session.state !== "active";
    if (atomicRevocation) this.#db.exec("BEGIN IMMEDIATE");
    try {
    if (atomicRevocation) this.#sharedRevocation.schedule(id);
    this.#db.prepare(`
      INSERT INTO bff_session (
        id, principal_json, envelope_version, iv, ciphertext, tag,
        access_expires_at, csrf, created_at, last_seen_at,
        principal_checked_at, state, revocation_reason,
        revocation_attempts, next_revocation_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET
        principal_json=excluded.principal_json,
        envelope_version=excluded.envelope_version,
        iv=excluded.iv,
        ciphertext=excluded.ciphertext,
        tag=excluded.tag,
        access_expires_at=excluded.access_expires_at,
        csrf=excluded.csrf,
        created_at=excluded.created_at,
        last_seen_at=excluded.last_seen_at,
        principal_checked_at=excluded.principal_checked_at,
        state=excluded.state,
        revocation_reason=excluded.revocation_reason,
        revocation_attempts=excluded.revocation_attempts,
        next_revocation_at=excluded.next_revocation_at
    `).run(
      id,
      JSON.stringify(session.principal),
      session.sealedTokens.version,
      session.sealedTokens.iv,
      session.sealedTokens.ciphertext,
      session.sealedTokens.tag,
      session.accessExpiresAt,
      session.csrf,
      session.createdAt,
      session.lastSeenAt,
      session.principalCheckedAt,
      session.state,
      session.revocationReason,
      session.revocationAttempts,
      session.nextRevocationAt,
    );
    if (atomicRevocation) this.#db.exec("COMMIT");
    } catch (error) {
      if (atomicRevocation) this.#db.exec("ROLLBACK");
      throw error;
    }
  }

  #isExpired(session, now = this.now()) {
    return now - session.createdAt > this.absoluteTtlMs ||
      now - session.lastSeenAt > this.idleTtlMs;
  }

  createPreauth() {
    const state = randomToken();
    const browserNonce = randomToken();
    this.preauth.set(state, { browserNonce, createdAt: this.now() });
    return { state, browserNonce };
  }

  consumePreauth(state, browserNonce) {
    const item = this.preauth.get(state);
    this.preauth.delete(state);
    if (
      !item ||
      this.now() - item.createdAt > 10 * 60_000 ||
      !timingSafeEqual(item.browserNonce, browserNonce)
    ) return null;
    return item;
  }

  createSession({ principal, accessToken, refreshToken, expiresIn }) {
    if (this.#qualifiedProfile) throw new Error("qualified sessions require verified login");
    return this.#storeSession({ principal, accessToken, refreshToken, expiresIn }, false);
  }

  async authenticateLogin(code) {
    if (!this.#qualifiedProfile || this.#closed || this.#qualifiedLoginsInFlight >= 2) throw new Error("qualified login unavailable");
    this.#qualifiedLoginsInFlight++;
    let tokens, id;
    try {
      tokens = await this.#qualifiedProfile.auth.exchange(code);
      id = this.retainLoginTokens(tokens);
      const principal = await this.#qualifiedProfile.auth.verify(tokens.accessToken);
      this.#verifiedLogins.set(id, principal);
      return this.completeLogin(id, principal);
    } catch {
      if (id) {
        const pending = this.abandonLogin(id);
        await this.revoke(this.#qualifiedProfile.config,id,pending,undefined,{reason:"unverified_login"});
      } else if (tokens?.refreshToken?.length) {
        await this.#qualifiedProfile.auth.revoke(tokens.refreshToken).catch(() => {});
      }
      throw new Error("qualified session login unavailable");
    } finally {
      this.#verifiedLogins.delete(id);
      this.#qualifiedLoginsInFlight--;
      tokens?.accessToken.fill(0); tokens?.refreshToken.fill(0);
    }
  }

  #configurationMatches(config) {
    return this.#qualifiedProfile ? config === this.#qualifiedProfile.config : legacyIssuerConfigurationValid(config);
  }

  retainLoginTokens(tokens) {
    if (!Buffer.isBuffer(tokens.refreshToken) || !tokens.refreshToken.length) {
      throw new Error("login refresh token unavailable");
    }
    const stored = this.#storeSession({ ...tokens,
      principal: { userId: "", isActive: false, isAdmin: false } }, true);
    this.#pendingLogins.set(stored.id, this.now());
    return stored.id;
  }

  completeLogin(id, principal) {
    if (this.#qualifiedProfile && this.#verifiedLogins.get(id) !== principal) throw new Error("qualified login has not been verified");
    const session = this.sessions.get(id);
    const started = this.#pendingLogins.get(id);
    const now = this.now();
    if (started === undefined || now < started || now - started >= REQUEST_TIMEOUT_MS ||
        !session || session.state !== "revocation_pending" ||
        session.revocationReason !== "unverified_login" || !principal?.userId ||
        principal.isActive !== true || principal.haIssuerId !== this.#haIssuerId ||
        principal.siteId !== this.#siteId) throw new Error("login completion rejected");
    const previous = { ...session };
    Object.assign(session, { principal, state: "active", revocationReason: null,
      nextRevocationAt: 0, principalCheckedAt: this.now() });
    try { this.#persist(id, session); }
    catch (error) { Object.assign(session, previous); throw error; }
    this.#pendingLogins.delete(id);
    return { id, csrf: session.csrf };
  }

  abandonLogin(id) {
    this.#pendingLogins.delete(id);
    return this.getForRevocation(id);
  }

  #storeSession({ principal, accessToken, refreshToken, expiresIn }, pending) {
    if (!principal || (principal.haIssuerId !== undefined && principal.haIssuerId !== this.#haIssuerId) ||
        (principal.siteId !== undefined && principal.siteId !== this.#siteId)) {
      throw new Error("session principal issuer mismatch");
    }
    principal = { ...principal, haIssuerId: this.#haIssuerId, siteId: this.#siteId };
    const id = randomToken();
    const csrf = randomToken();
    const now = this.now();
    const session = {
      principal,
      sealedTokens: sealTokenBundle(
        this.#sessionEncryptionKey,
        id,
        accessToken,
        refreshToken,
      ),
      accessExpiresAt: now + Math.max(1, Number(expiresIn) || 300) * 1000,
      csrf,
      createdAt: now,
      lastSeenAt: now,
      principalCheckedAt: now,
      state: pending ? "revocation_pending" : "active",
      revocationReason: pending ? "unverified_login" : null,
      revocationAttempts: 0,
      nextRevocationAt: 0,
    };
    try {
      this.#persist(id, session);
      this.sessions.set(id, session);
    } catch (error) {
      destroyEnvelope(session.sealedTokens);
      throw error;
    }
    return { id, csrf };
  }

  get(id) {
    const key = String(id || "");
    const session = this.sessions.get(key);
    if (!session) return null;
    const now = this.now();
    if (session.state !== "active") return null;
    if (this.#isExpired(session, now)) {
      this.scheduleRevocation(key, session, "expired", now);
      return null;
    }
    const previousLastSeenAt = session.lastSeenAt;
    session.lastSeenAt = now;
    try { this.#persist(key, session); }
    catch {
      session.lastSeenAt = previousLastSeenAt;
      return null;
    }
    return session;
  }

  linkingContext(id, commitmentKey) {
    // Internal only: the caller must authenticate the request, enforce origin
    // and CSRF, and obtain explicit linking intent before asking for this value.
    // This is an existing session binding, not fresh HA authentication proof.
    if (!Buffer.isBuffer(commitmentKey) || commitmentKey.length !== 32 ||
        crypto.timingSafeEqual(commitmentKey, this.#sessionEncryptionKey)) {
      throw new Error("dedicated linking session commitment key required");
    }
    if (typeof id !== "string" || !id) return null;
    // A snapshot predating commitment arming has no tombstone to replay. Keep
    // ordinary HA session compatibility, but require a new login before linking.
    if (this.#sharedRevocation && this.#restoredSharedSessions.has(id)) return null;
    const candidate = this.sessions.get(id);
    const checkedNow = this.now();
    if (!candidate || !Number.isSafeInteger(checkedNow) ||
        ![candidate.createdAt, candidate.lastSeenAt, candidate.principalCheckedAt]
          .every((time) => Number.isSafeInteger(time) && time <= checkedNow) ||
        checkedNow >= candidate.createdAt + this.absoluteTtlMs ||
        checkedNow >= candidate.lastSeenAt + this.idleTtlMs) return null;
    const session = this.get(id);
    const now = this.now();
    if (!session || !Number.isSafeInteger(now) || now < checkedNow || !Number.isSafeInteger(session.principalCheckedAt) ||
        now < session.principalCheckedAt || now - session.principalCheckedAt >= 300_000 ||
        session.principal?.isActive !== true || session.principal.haIssuerId !== this.#haIssuerId ||
        session.principal.siteId !== this.#siteId || typeof session.principal.userId !== "string" ||
        !session.principal.userId.length || [...session.principal.userId].length > 64 ||
        session.principal.userId.trim() !== session.principal.userId ||
        /[\x00-\x1f\x7f]/.test(session.principal.userId)) return null;
    const validUntil = Math.min(session.principalCheckedAt + 300_000,
      session.createdAt + this.absoluteTtlMs, session.lastSeenAt + this.idleTtlMs);
    if (!Number.isSafeInteger(validUntil) || validUntil <= now) return null;
    const material = JSON.stringify(["home-agent:shared-link:v1:bff-session",
      this.#haIssuerId, this.#siteId, id]);
    return Object.freeze({ issuerId: this.#haIssuerId, siteId: this.#siteId,
      subject: session.principal.userId,
      sessionCommitment: this.#sharedRevocation ? this.#sharedRevocation.arm(id,commitmentKey) :
        crypto.createHmac("sha256", commitmentKey).update(material).digest("hex"),
      checkedAt: session.principalCheckedAt, validUntil });
  }

  getForRevocation(id) {
    const key = String(id || "");
    const session = this.sessions.get(key);
    return session || null;
  }

  scheduleRevocation(id, session, reason, now = this.now()) {
    const key = String(id || "");
    if (!session || this.sessions.get(key) !== session) return false;
    this.#pendingLogins.delete(key);
    if (session.state !== "authority_revoked") session.state = "revocation_pending";
    session.revocationReason = String(reason || "unspecified");
    session.nextRevocationAt = Math.min(session.nextRevocationAt || now, now);
    try {
      this.#persist(key, session);
      return true;
    } catch {
      return false;
    }
  }

  #recordRevocationFailure(id, session, now = this.now()) {
    if (session.state === "authority_revoked") return;
    session.state = "revocation_pending";
    session.revocationAttempts += 1;
    const exponent = Math.min(10, Math.max(0, session.revocationAttempts - 1));
    session.nextRevocationAt = now + Math.min(
      REVOCATION_RETRY_MAX_MS,
      REVOCATION_RETRY_BASE_MS * (2 ** exponent),
    );
    try { this.#persist(id, session); }
    catch {
      // The in-memory record remains fail-closed. The next cleanup cycle will
      // retry both persistence and authority revocation.
    }
  }

  #deleteAuthorityRevoked(id, session) {
    if (this.#sharedRevocation) this.#db.exec("BEGIN IMMEDIATE");
    try {
    this.#sharedRevocation?.schedule(id);
    this.#db.prepare("DELETE FROM bff_session WHERE id = ?").run(id);
    if (this.#sharedRevocation) this.#db.exec("COMMIT");
    } catch (error) {
      if (this.#sharedRevocation) this.#db.exec("ROLLBACK");
      throw error;
    }
    this.sessions.delete(id);
    destroyEnvelope(session.sealedTokens);
  }

  async #withTokens(id, session, callback) {
    const key = String(id || "");
    if (!session || this.sessions.get(key) !== session) {
      throw new Error("session is no longer active");
    }
    const tokens = openTokenBundle(this.#sessionEncryptionKey, key, session.sealedTokens);
    try {
      return await callback(tokens);
    } finally {
      tokens.accessToken.fill(0);
      tokens.refreshToken.fill(0);
    }
  }

  #replaceTokens(id, session, accessToken, refreshToken, expiresIn, now = this.now()) {
    const key = String(id || "");
    if (!session || this.sessions.get(key) !== session) {
      throw new Error("session is no longer active");
    }
    const replacement = sealTokenBundle(
      this.#sessionEncryptionKey,
      key,
      accessToken,
      refreshToken,
    );
    const previous = session.sealedTokens;
    const previousExpiry = session.accessExpiresAt;
    session.sealedTokens = replacement;
    session.accessExpiresAt = now + Math.max(1, Number(expiresIn) || 300) * 1000;
    try { this.#persist(key, session); }
    catch (error) {
      session.sealedTokens = previous;
      session.accessExpiresAt = previousExpiry;
      destroyEnvelope(replacement);
      throw error;
    }
    destroyEnvelope(previous);
  }

  async revalidate(
    config,
    id,
    session,
    fetchImpl,
    now = Date.now(),
    { forcePrincipalCheck = false } = {},
  ) {
    if (!this.#configurationMatches(config) || session.principal.haIssuerId !== this.#haIssuerId ||
        session.principal.siteId !== this.#siteId || (this.#browserOriginsJson !== "[]" &&
        sessionOriginsJson(config.allowedOrigins) !== this.#browserOriginsJson)) {
      throw new Error("session issuer mismatch before revalidation");
    }
    const qualifiedKey = String(id || "");
    if (this.#qualifiedProfile) {
      now = this.now();
      if (!Number.isSafeInteger(now) || this.sessions.get(qualifiedKey) !== session ||
          session.state !== "active" || ![session.createdAt,session.lastSeenAt,session.principalCheckedAt]
            .every((stamp) => Number.isSafeInteger(stamp) && stamp <= now) ||
          now >= session.createdAt + this.absoluteTtlMs || now >= session.lastSeenAt + this.idleTtlMs) {
        this.scheduleRevocation(id,session,"invalid_revalidation_context");
        throw new Error("qualified session unavailable before revalidation");
      }
      if (this.#qualifiedChecks.has(qualifiedKey) || this.#qualifiedChecks.size >= 2) {
        throw new Error("qualified session revalidation busy");
      }
      this.#qualifiedChecks.add(qualifiedKey);
    }
    const shouldRefresh = session.accessExpiresAt <= now + 60_000;
    const shouldCheckPrincipal = forcePrincipalCheck || shouldRefresh ||
      now - session.principalCheckedAt >= Math.max(0, config.principalRevalidateMs || 0);
    if (!shouldCheckPrincipal) { this.#qualifiedChecks.delete(qualifiedKey); return session; }

    try { await this.#withTokens(id, session, async (current) => {
      let refreshed;
      let activeAccessToken = current.accessToken;
      let activeRefreshToken = current.refreshToken;
      try {
        if (shouldRefresh) {
          refreshed = this.#qualifiedProfile ? await this.#qualifiedProfile.auth.refresh(current.refreshToken) :
            await refreshAccessToken(config, current.refreshToken, fetchImpl);
          activeAccessToken = refreshed.accessToken;
          if (refreshed.refreshToken.length) activeRefreshToken = refreshed.refreshToken;
        }
        const principal = this.#qualifiedProfile ? await this.#qualifiedProfile.auth.verify(activeAccessToken) :
          await fetchWhoami(config, activeAccessToken, fetchImpl);
        const checkedAt = this.#qualifiedProfile ? this.now() : now;
        if (this.#qualifiedProfile && (this.sessions.get(String(id)) !== session || session.state !== "active" ||
            !Number.isSafeInteger(checkedAt) || checkedAt < now ||
            checkedAt >= session.createdAt + this.absoluteTtlMs || checkedAt >= session.lastSeenAt + this.idleTtlMs)) {
          throw new Error("qualified session ended during revalidation");
        }
        if (!timingSafeEqual(principal.userId, session.principal.userId)) {
          throw new Error("HA principal changed during session");
        }
        session.principal = principal;
        session.principalCheckedAt = now;
        if (refreshed) {
          this.#replaceTokens(
            id,
            session,
            activeAccessToken,
            activeRefreshToken,
            refreshed.expiresIn,
            now,
          );
        } else {
          this.#persist(String(id || ""), session);
        }
      } catch (error) {
        if (error instanceof HaUnavailableError) {
          // HA is briefly unreachable: keep the session. A refresh that already
          // rotated the tokens must be kept, or the old refresh token is dead.
          if (refreshed) {
            try {
              this.#replaceTokens(id, session, activeAccessToken, activeRefreshToken, refreshed.expiresIn, now);
            } catch { /* the next successful refresh or HA denial decides */ }
          }
          throw error;
        }
        if (this.#qualifiedProfile) this.scheduleRevocation(id, session, "failed_revalidation");
        if (this.#qualifiedProfile && refreshed) {
          // A refresh may have rotated the token before verification failed or
          // logout won. Retain the replacement as revocation-only work.
          let cleanupId;
          try {
            cleanupId = this.retainLoginTokens({ ...refreshed, refreshToken: activeRefreshToken });
            const cleanup = this.abandonLogin(cleanupId);
            await this.revoke(config, cleanupId, cleanup, undefined, { reason: "failed_revalidation" });
          } catch {
            if (!cleanupId) await this.#qualifiedProfile.auth.revoke(activeRefreshToken).catch(() => {});
          }
        }
        throw error;
      } finally {
        refreshed?.accessToken.fill(0);
        refreshed?.refreshToken.fill(0);
      }
    }); } catch (error) {
      if (this.#qualifiedProfile && !(error instanceof HaUnavailableError)) {
        this.scheduleRevocation(id, session, "failed_revalidation");
      }
      throw error;
    } finally { this.#qualifiedChecks.delete(qualifiedKey); }
    return session;
  }

  async revoke(config, id, session, fetchImpl, { reason = "logout", now = this.now() } = {}) {
    const key = String(id || "");
    if (!session || this.sessions.get(key) !== session) return false;
    if (!this.#configurationMatches(config) || session.principal.haIssuerId !== this.#haIssuerId ||
        session.principal.siteId !== this.#siteId || (this.#browserOriginsJson !== "[]" &&
        sessionOriginsJson(config.allowedOrigins) !== this.#browserOriginsJson)) return false;
    if (session.state === "authority_revoked") {
      try {
        this.#deleteAuthorityRevoked(key, session);
        await this.#sharedRevocation?.drain();
        return this.#sharedRevocation ? this.#sharedRevocation.delivered(key) : true;
      } catch { return false; }
    }
    this.scheduleRevocation(key, session, reason, now);
    try {
      await this.#withTokens(key, session, async ({ refreshToken }) => {
        if (this.#qualifiedProfile) await this.#qualifiedProfile.auth.revoke(refreshToken);
        else await revokeRefreshToken(config, refreshToken, fetchImpl);
      });
    } catch {
      this.#recordRevocationFailure(key, session, now);
      return false;
    }

    // Persisting this intermediate state means a local DELETE failure never
    // causes an already-revoked authority token to be used again.
    session.state = "authority_revoked";
    session.nextRevocationAt = now;
    try { this.#persist(key, session); }
    catch {
      // HA has already confirmed revocation; attempt local deletion anyway.
    }
    try {
      this.#deleteAuthorityRevoked(key, session);
      await this.#sharedRevocation?.drain();
      return this.#sharedRevocation ? this.#sharedRevocation.delivered(key) : true;
    } catch { return false; }
  }

  async cleanupExpired(config, fetchImpl, {
    now = this.now(),
    batchSize = config.sessionCleanupBatchSize || DEFAULT_CLEANUP_BATCH_SIZE,
  } = {}) {
    const limit = Math.max(1, Math.min(100, Number(batchSize) || DEFAULT_CLEANUP_BATCH_SIZE));
    const candidates = [];
    for (const [id, session] of this.sessions) {
      if (candidates.length >= limit) break;
      const loginStarted = this.#pendingLogins.get(id);
      if (loginStarted !== undefined) {
        if (now >= loginStarted && now - loginStarted < REQUEST_TIMEOUT_MS) continue;
        this.#pendingLogins.delete(id);
      }
      if (session.state === "active" && this.#isExpired(session, now)) {
        this.scheduleRevocation(id, session, "expired", now);
        candidates.push([id, session]);
      } else if (
        session.state === "authority_revoked" ||
        (session.state === "revocation_pending" && session.nextRevocationAt <= now)
      ) {
        candidates.push([id, session]);
      }
    }
    let completed = 0;
    for (const [id, session] of candidates) {
      if (await this.revoke(config, id, session, fetchImpl, {
        reason: session.revocationReason || "expired",
        now,
      })) completed += 1;
    }
    await this.#sharedRevocation?.drain();
    return { attempted: candidates.length, completed };
  }

  startCleanup(config, fetchImpl) {
    if (this.#cleanupTimer || this.#closed) return;
    const intervalMs = Math.max(1_000, Number(config.sessionCleanupIntervalMs) ||
      DEFAULT_CLEANUP_INTERVAL_MS);
    this.#cleanupTimer = setInterval(() => {
      if (this.#cleanupInFlight || this.#closed) return;
      this.#cleanupInFlight = true;
      this.cleanupExpired(config, fetchImpl)
        .catch(() => {})
        .finally(() => { this.#cleanupInFlight = false; });
    }, intervalMs);
    this.#cleanupTimer.unref();
  }

  stopCleanup() {
    if (this.#cleanupTimer) clearInterval(this.#cleanupTimer);
    this.#cleanupTimer = null;
  }

  close() {
    if (this.#closed) return;
    this.#sharedRevocation?.close();
    this.#closed = true;
    this.stopCleanup();
    for (const session of this.sessions.values()) destroyEnvelope(session.sealedTokens);
    this.sessions.clear();
    this.#pendingLogins.clear();
    this.#sessionEncryptionKey.fill(0);
    this.#db.close();
    this.#sharedSessionOwner?.close();
  }
}

async function readBody(req) {
  const declared = Number(req.headers["content-length"] || 0);
  if (declared > MAX_BODY_BYTES) throw Object.assign(new Error("body too large"), { status: 413 });
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) throw Object.assign(new Error("body too large"), { status: 413 });
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

// The browser keeps the cookie exactly as long as the server keeps the session.
function sessionCookie(id, secure, absoluteTtlMs) {
  const maxAge = Math.floor(absoluteTtlMs / 1000);
  if (!Number.isSafeInteger(maxAge) || maxAge <= 0) throw new Error("session cookie lifetime required");
  return `${COOKIE_NAME}=${encodeURIComponent(id)}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${maxAge}${secure ? "; Secure" : ""}`;
}

function clearSessionCookie(secure) {
  return `${COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0${secure ? "; Secure" : ""}`;
}

function oauthCookie(value, secure) {
  return `${OAUTH_COOKIE_NAME}=${encodeURIComponent(value)}; Path=/; HttpOnly; SameSite=Lax; Max-Age=600${secure ? "; Secure" : ""}`;
}

function clearOauthCookie(secure) {
  return `${OAUTH_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0${secure ? "; Secure" : ""}`;
}

function requestSession(req, store) {
  return store.get(parseCookies(req.headers.cookie)[COOKIE_NAME]);
}

function requireOrigin(req, config, res) {
  if (isAllowedOrigin(req.headers.origin, config.allowedOrigins)) return true;
  json(res, 403, { error: "origin_denied" });
  return false;
}

function requireCsrf(req, session, res) {
  if (timingSafeEqual(req.headers["x-csrf-token"], session?.csrf)) return true;
  json(res, 403, { error: "csrf_denied" });
  return false;
}

function oauthCallbackParameters(url) {
  const stateValues = url.searchParams.getAll("state");
  const codeValues = url.searchParams.getAll("code");
  if (
    [...url.searchParams.keys()].length !== 2 ||
    stateValues.length !== 1 || codeValues.length !== 1
  ) return null;
  const [state] = stateValues;
  const [code] = codeValues;
  if (
    !/^[A-Za-z0-9_-]{43}$/.test(state) ||
    !code || code.length > 4_096 || /[^\x21-\x7e]/.test(code)
  ) return null;
  return { state, code };
}

async function fetchWhoami(config, accessToken, fetchImpl) {
  if (!legacyIssuerConfigurationValid(config)) throw new Error("HA issuer is not provisioned");
  return { ...await fetchHaSubject(config, accessToken, fetchImpl), ...issuerBinding(config) };
}

async function proxyCoreRequest(
  config,
  req,
  res,
  url,
  principal,
  fetchImpl,
  requestId,
  publicPrefix,
  { rawBody: providedRawBody, nativeInstallationId = null } = {},
) {
  let rawBody;
  let body;
  try {
    const binding = issuerBinding(config);
    if (!legacyIssuerConfigurationValid(config) || principal.haIssuerId !== binding.haIssuerId ||
        principal.siteId !== binding.siteId) {
      throw new Error("principal issuer cannot enter legacy Core");
    }
    rawBody = providedRawBody === undefined
      ? (req.method === "GET" ? undefined : await readBody(req))
      : providedRawBody;
    body = rawBody === undefined
      ? undefined
      : normalizeParentRelationshipBody(
        url.pathname,
        normalizePrincipalBindingBody(url.pathname, rawBody),
      );
    const corePath = url.pathname.replace(publicPrefix, "");
    const headers = {
      Authorization: `Bearer ${config.coreToken}`,
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Authenticated-HA-User": principal.userId,
      "X-Authenticated-HA-Issuer": binding.haIssuerId,
      "X-Authenticated-Home-Site": binding.siteId,
      "X-Request-Id": requestId,
    };
    if (nativeInstallationId) {
      headers["X-Home-Agent-Channel"] = NATIVE_ATTESTED_CHANNEL;
      headers["X-Home-Agent-Installation"] = nativeInstallationId;
    }
    const upstream = await fetchImpl(`${config.coreUrl}${corePath}${url.search}`, {
      method: req.method,
      redirect: "error",
      headers,
      body: body?.length ? body : undefined,
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    const responseBody = await readBoundedCoreResponse(upstream);
    res.writeHead(upstream.status, {
      "Content-Type": upstream.headers.get("content-type") || "application/json; charset=utf-8",
      "Content-Length": responseBody.length,
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    });
    res.end(responseBody);
  } catch (error) {
    json(res, error.status || 502, {
      error: error.code || (error.status === 413 ? "body_too_large" : "agent_upstream_unavailable"),
      request_id: requestId,
    });
  } finally {
    if (Buffer.isBuffer(rawBody)) rawBody.fill(0);
    if (Buffer.isBuffer(body) && body !== rawBody) body.fill(0);
  }
}

async function readBoundedCoreResponse(response) {
  const declared = response.headers.get("content-length");
  if (declared !== null) {
    const length = Number(declared);
    if (!Number.isSafeInteger(length) || length < 0 || length > MAX_CORE_RESPONSE_BYTES) {
      const error = new Error("Core response exceeds the BFF limit");
      error.status = 502;
      error.code = "agent_upstream_response_too_large";
      throw error;
    }
  }
  if (!response.body) return Buffer.alloc(0);
  const reader = response.body.getReader();
  const chunks = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const size = value?.byteLength || 0;
      if (size > MAX_CORE_RESPONSE_BYTES - total) {
        await reader.cancel("response_too_large").catch(() => {});
        const error = new Error("Core response exceeds the BFF limit");
        error.status = 502;
        error.code = "agent_upstream_response_too_large";
        throw error;
      }
      total += size;
      // The bound is checked before creating an additional Buffer copy.
      chunks.push(Buffer.from(value));
    }
    const combined = Buffer.concat(chunks, total);
    for (const chunk of chunks) chunk.fill(0);
    return combined;
  } catch (error) {
    for (const chunk of chunks) chunk.fill(0);
    throw error;
  } finally {
    reader.releaseLock();
  }
}

function createBff(config, { fetchImpl = fetch, store, attestationStore, linkReview, linkStart, personalMemory, lighting } = {}) {
  if (store && (store.haIssuerId !== LEGACY_HA_ISSUER_ID || store.siteId !== LEGACY_SITE_ID)) {
    throw new Error("legacy BFF requires Echo sessions");
  }
  const persistenceReady = (
    config.sessionDbPath && path.isAbsolute(config.sessionDbPath) &&
    config.sessionDbPath !== ":memory:"
  ) || config.allowInMemorySessions === true;
  if (config.ready && safePostLoginRedirect(config.postLoginRedirect) !== config.postLoginRedirect) {
    throw new Error("BFF cannot start ready with an unsafe post-login redirect");
  }
  if (config.ready && !endpointConfigurationValid(config)) {
    throw new Error("BFF cannot start ready with unsafe endpoint configuration");
  }
  if (
    config.ready &&
    (!Buffer.isBuffer(config.sessionEncryptionKey) ||
      config.sessionEncryptionKey.length !== SESSION_KEY_BYTES ||
      !persistenceReady)
  ) throw new Error("BFF cannot start ready without sealed persistent sessions");
  const ownsStore = !store;
  const sessions = store || (config.ready ? new SessionStore(config) : null);
  if (linkReview !== undefined && (!(linkReview instanceof EchoLinkReview) || !linkReview.usesStore(sessions))) {
    throw new Error("link review requires the same authenticated session store");
  }
  if (linkStart !== undefined && (!(linkStart instanceof EchoLinkStart) || !linkStart.usesStore(sessions))) {
    throw new Error("link start requires the same authenticated session store");
  }
  if (personalMemory !== undefined && (!(personalMemory instanceof PersonalMemoryClient) || !personalMemory.usesStore(sessions))) {
    throw new Error("personal memory requires the same authenticated session store");
  }
  if (lighting !== undefined && (!(lighting instanceof LightingClient) || !lighting.usesStore(sessions))) {
    throw new Error("lighting requires the same authenticated session store");
  }
  const nativeAttestations = attestationStore || new NativeAttestationStore(
    config.nativeAttestationConfigured === true ? config.nativeInstallations : null,
  );
  const nativeBoundaryConfigured = config.nativeAttestationConfigured === true &&
    exactNativePublicOrigin(config.nativePublicOrigin) === config.nativePublicOrigin &&
    config.nativePublicOrigin !== config.clientId &&
    config.allowedOrigins instanceof Set &&
    !config.allowedOrigins.has(config.nativePublicOrigin) &&
    nativeAttestations.configured;
  if (sessions && config.ready) sessions.startCleanup(config, fetchImpl);
  const server = http.createServer({
    // Bound the unauthenticated and bearer-authenticated ingress before a
    // route handler can wait on readBody. In particular, a slow challenge body
    // cannot hold a BFF socket indefinitely by dripping bytes.
    requestTimeout: REQUEST_TIMEOUT_MS,
    headersTimeout: REQUEST_TIMEOUT_MS,
    keepAliveTimeout: Math.floor(REQUEST_TIMEOUT_MS / 2),
    connectionsCheckingInterval: 1_000,
    maxHeaderSize: 16 * 1024,
    requireHostHeader: true,
  }, async (req, res) => {
    const requestId = randomToken(12);
    res.setHeader("X-Request-Id", requestId);
    let url;
    try { url = new URL(req.url, "http://bff.internal"); }
    catch { return json(res, 400, { error: "invalid_url", request_id: requestId }); }

    if (url.pathname === "/healthz") {
      return json(res, config.ready ? 200 : 503, {
        ok: config.ready,
        configured: config.ready,
        service: "home-agent-bff",
        native_attestation_configured: nativeBoundaryConfigured,
      });
    }
    if (!config.ready) return json(res, 503, { error: "bff_unconfigured", request_id: requestId });

    if (url.pathname === "/api/agent/auth/start" && req.method === "POST") {
      if (!requireOrigin(req, config, res)) return;
      const pending = sessions.createPreauth();
      const target = new URL(`${config.haUrl}/auth/authorize`);
      target.search = new URLSearchParams({
        client_id: config.clientId,
        redirect_uri: config.redirectUri,
        response_type: "code",
        state: pending.state,
      }).toString();
      return json(
        res,
        200,
        { authorize_url: target.toString() },
        { "Set-Cookie": oauthCookie(pending.browserNonce, config.secureCookie) },
      );
    }

    if (url.pathname === "/api/agent/auth/callback" && req.method === "GET") {
      const callback = oauthCallbackParameters(url);
      if (!callback) return json(
        res,
        400,
        { error: "oauth_callback_invalid" },
        { "Set-Cookie": clearOauthCookie(config.secureCookie) },
      );
      const browserNonce = parseCookies(req.headers.cookie)[OAUTH_COOKIE_NAME];
      const pending = sessions.consumePreauth(callback.state, browserNonce);
      if (!pending) return json(
        res,
        400,
        { error: "oauth_state_invalid" },
        { "Set-Cookie": clearOauthCookie(config.secureCookie) },
      );
      try {
        const tokens = await exchangeCode(config, callback.code, fetchImpl);
        let pendingId;
        try {
          pendingId = sessions.retainLoginTokens(tokens);
          const principal = await fetchWhoami(config, tokens.accessToken, fetchImpl);
          const session = sessions.completeLogin(pendingId, principal);
          return redirect(res, config.postLoginRedirect, {
            "Set-Cookie": [
              sessionCookie(session.id, config.secureCookie, config.absoluteTtlMs),
              clearOauthCookie(config.secureCookie),
            ],
          });
        } catch (error) {
          if (pendingId) {
            const pending = sessions.abandonLogin(pendingId);
            await sessions.revoke(config, pendingId, pending, fetchImpl, { reason: "login_failed" });
          } else {
            // Persistence failure cannot authorize a session. Revoke immediately
            // while token bytes are available; no durable recovery is claimed.
            await revokeRefreshToken(config, tokens.refreshToken, fetchImpl).catch(() => {});
          }
          throw error;
        } finally {
          tokens.accessToken.fill(0);
          tokens.refreshToken.fill(0);
        }
      } catch {
        return json(
          res,
          502,
          { error: "oauth_exchange_failed" },
          { "Set-Cookie": clearOauthCookie(config.secureCookie) },
        );
      }
    }

    if (url.pathname.startsWith("/api/agent/native/")) {
      if (req.headers.origin || req.headers.cookie) {
        return json(res, 403, { error: "native_transport_denied", request_id: requestId });
      }
      const isChallenge = url.pathname === NATIVE_CHALLENGE_PATH && req.method === "POST";
      if (url.search || (!isChallenge && !nativeRouteAllowed(req.method, url.pathname))) {
        return json(res, 404, { error: "route_not_allowed", request_id: requestId });
      }
      if (!nativeBoundaryConfigured) {
        return json(res, 503, { error: "native_attestation_unavailable", request_id: requestId });
      }
      const bearer = nativeBearer(req.headers.authorization);
      if (!bearer) return json(res, 401, { error: "native_authentication_required" });
      const accessToken = Buffer.from(bearer);
      let principal;
      let rawBody;
      try {
        try { principal = await fetchWhoami(config, accessToken, fetchImpl); }
        catch {
          return json(res, 401, { error: "native_authentication_revoked" });
        }
        rawBody = await readBody(req);
        if (isChallenge) {
          const challengeRequest = parseNativeChallengeBody(rawBody, config.nativePublicOrigin);
          const challenge = nativeAttestations.issue(challengeRequest, principal, accessToken);
          return json(res, 200, challenge);
        }
        const nativeInstallationId = nativeAttestations.verify({
          proof: req.headers.dpop,
          principal,
          accessToken,
          method: req.method,
          pathname: url.pathname,
          htu: `${config.nativePublicOrigin}${url.pathname}`,
          body: rawBody,
        });
        await proxyCoreRequest(
          config,
          req,
          res,
          url,
          principal,
          fetchImpl,
          requestId,
          /^\/api\/agent\/native/,
          { rawBody, nativeInstallationId },
        );
        return;
      } catch (error) {
        return json(res, error.status || 502, {
          error: error.code || (error.status === 413 ? "body_too_large" : "native_attestation_invalid"),
          request_id: requestId,
        });
      } finally {
        rawBody?.fill(0);
        accessToken.fill(0);
      }
    }

    const sessionId = parseCookies(req.headers.cookie)[COOKIE_NAME];
    if (url.pathname === "/api/agent/auth/logout" && req.method === "POST") {
      const logoutSession = sessions.getForRevocation(sessionId);
      if (!logoutSession) return json(
        res,
        401,
        { error: "authentication_required" },
        { "Set-Cookie": clearSessionCookie(config.secureCookie) },
      );
      if (
        !requireOrigin(req, config, res) ||
        !requireCsrf(req, logoutSession, res)
      ) return;
      const revoked = await sessions.revoke(
        config,
        sessionId,
        logoutSession,
        fetchImpl,
        { reason: "logout" },
      );
      return json(
        res,
        revoked ? 200 : 503,
        revoked ? { ok: true } : { error: "logout_revocation_pending", retryable: true },
        { "Set-Cookie": clearSessionCookie(config.secureCookie) },
      );
    }

    const session = requestSession(req, sessions);
    if (!session) return json(
      res,
      401,
      { error: "authentication_required" },
      { "Set-Cookie": clearSessionCookie(config.secureCookie) },
    );
    const linkOperation = req.method === "POST" && !url.search && linkReview ?
      ({ "/api/agent/shared-identity/review": "review", "/api/agent/shared-identity/confirm": "confirm",
        "/api/agent/shared-identity/outcome": "outcome" })[url.pathname] : null;
    const startOperation = req.method === "POST" && !url.search && linkStart ?
      ({ "/api/agent/shared-identity/start": "start", "/api/agent/shared-identity/handoff": "redeem",
        "/api/agent/shared-identity/issuance-outcome": "recover" })[url.pathname] : null;
    const authenticationOperation = req.method === "POST" && !url.search && linkStart?.authenticationEnabled ?
      ({ "/api/agent/shared-identity/auth-begin": "begin", "/api/agent/shared-identity/auth-submit": "submit",
        "/api/agent/shared-identity/auth-outcome": "recover" })[url.pathname] : null;
    const victoriaAuthenticationOperation=req.method === "POST" && !url.search && linkStart?.authenticationEnabled ?
      ({"/api/agent/shared-identity/victoria-auth-admit":"admit","/api/agent/shared-identity/victoria-auth-outcome":"outcome"})[url.pathname] : null;
    const preparingReview=req.method==="POST" && !url.search && linkStart?.reviewEnabled && url.pathname==="/api/agent/shared-identity/prepare-review";
    const memoryOperation = req.method === "POST" && !url.search && personalMemory ?
      ({"/api/agent/personal-memory/read":"read","/api/agent/personal-memory/propose":"propose",
        "/api/agent/personal-memory/confirm":"confirm","/api/agent/personal-memory/outcome":"outcome",
        "/api/agent/personal-memory/sharing-propose":"sharing-propose",
        "/api/agent/personal-memory/sharing-confirm":"sharing-confirm",
        "/api/agent/personal-memory/sharing-outcome":"sharing-outcome"})[url.pathname] : null;
    const lightingOperation = req.method === "POST" && !url.search && lighting &&
      url.pathname.startsWith("/api/agent/lighting/") &&
      LIGHTING_OPERATIONS.includes(url.pathname.slice("/api/agent/lighting/".length)) ?
      url.pathname.slice("/api/agent/lighting/".length) : null;
    const isFreshIdentityRoute = Boolean(memoryOperation || lightingOperation || linkOperation || startOperation || authenticationOperation || victoriaAuthenticationOperation || preparingReview) || (
      !url.search && (
        PRINCIPAL_BINDING_FRESH_AUTH_ROUTES.has(`${req.method} ${url.pathname}`) ||
        PARENT_RELATIONSHIP_FRESH_AUTH_ROUTES.has(`${req.method} ${url.pathname}`)
      )
    );
    if (
      isFreshIdentityRoute && req.method !== "GET" &&
      (!requireOrigin(req, config, res) || !requireCsrf(req, session, res))
    ) return;
    try {
      await sessions.revalidate(
        config,
        sessionId,
        session,
        fetchImpl,
        Date.now(),
        { forcePrincipalCheck: isFreshIdentityRoute },
      );
    } catch (error) {
      if (error instanceof HaUnavailableError) {
        return json(res, 503, { error: "home_assistant_unavailable", retryable: true });
      }
      sessions.scheduleRevocation(sessionId, session, "authentication_revoked");
      return json(
        res,
        401,
        { error: "authentication_revoked" },
        { "Set-Cookie": clearSessionCookie(config.secureCookie) },
      );
    }

    if (lightingOperation) {
      let rawBody;
      const controller=new AbortController();
      const disconnected=()=>controller.abort();
      res.once("close",disconnected);
      try {
        if (req.headers["content-type"]!=="application/json" || req.headers["content-encoding"]) return json(res,415,{error:"json_required"});
        rawBody=await readBody(req);
        if (rawBody.length>2048) return json(res,413,{error:"body_too_large"});
        const text=new TextDecoder("utf-8",{fatal:true}).decode(rawBody);
        if (jsonHasDuplicateObjectKeys(text)) return json(res,422,{error:"invalid_lighting_request"});
        const result=await lighting.request(sessionId,lightingOperation,JSON.parse(text),{signal:controller.signal});
        if (!res.destroyed) return json(res,200,result);
      } catch (error) {
        // Malformed input and typed refusals are definite; anything else must be looked up.
        if (res.destroyed) return;
        if (error instanceof SyntaxError || ["invalid_lighting_request","invalid_lighting_operation"].includes(error?.message)) {
          return json(res,422,{error:"invalid_lighting_request"});
        }
        return error?.message==="lighting_not_permitted" ?
          json(res,403,{error:"lighting_not_permitted"}) : json(res,503,{error:"lighting_outcome_unknown"});
      } finally {
        rawBody?.fill(0);
        res.removeListener("close",disconnected);
      }
      return;
    }

    if (memoryOperation) {
      let rawBody;
      const controller=new AbortController();
      const disconnected=()=>controller.abort();
      res.once("close",disconnected);
      try {
        if (req.headers["content-type"]!=="application/json" || req.headers["content-encoding"]) return json(res,415,{error:"json_required"});
        rawBody=await readBody(req);
        if (rawBody.length>2048) return json(res,413,{error:"body_too_large"});
        const text=new TextDecoder("utf-8",{fatal:true}).decode(rawBody);
        if (jsonHasDuplicateObjectKeys(text)) return json(res,422,{error:"invalid_preference_request"});
        const result=await personalMemory.request(sessionId,memoryOperation,JSON.parse(text),{signal:controller.signal});
        if (!res.destroyed) return json(res,200,result);
      } catch {
        if (!res.destroyed) return json(res,503,{error:"personal_memory_outcome_unknown"});
      } finally {
        rawBody?.fill(0);
        res.removeListener("close",disconnected);
      }
      return;
    }

    if (linkOperation || startOperation || authenticationOperation || victoriaAuthenticationOperation || preparingReview) {
      let rawBody;
      const controller = new AbortController();
      const disconnected = () => controller.abort();
      res.once("close", disconnected);
      try {
        if (req.headers["content-type"] !== "application/json" || req.headers["content-encoding"]) {
          return json(res, 415, { error: "json_required" });
        }
        rawBody = await readBody(req);
        if (rawBody.length > 1024) return json(res, 413, { error: "body_too_large" });
        const text = new TextDecoder("utf-8", { fatal: true }).decode(rawBody);
        const input = JSON.parse(text);
        if (jsonHasDuplicateObjectKeys(text)) return json(res, 422, { error: "invalid_link_request" });
        let result;
        if (preparingReview) {
          if(controller.signal.aborted) return;
          result=await linkStart.prepareReview(sessionId,input,{signal:controller.signal});
        } else if (victoriaAuthenticationOperation) {
          if(controller.signal.aborted) return;
          result=await linkStart.victoriaAuthentication(sessionId,victoriaAuthenticationOperation,input,{signal:controller.signal});
        } else if (authenticationOperation) {
          if (controller.signal.aborted) return;
          result = await linkStart.authenticate(sessionId, authenticationOperation, input);
        } else if (startOperation) {
          const keys = input && typeof input === "object" && !Array.isArray(input) ? Object.keys(input).sort().join(",") : null;
          const expected = { start: "", redeem: "pairing_id,token", recover: "pairing_id" }[startOperation];
          if (keys !== expected) return json(res, 422, { error: "invalid_link_request" });
          if (controller.signal.aborted) return;
          result = startOperation === "start" ? await linkStart.start(sessionId) :
            startOperation === "redeem" ? await linkStart.redeem(sessionId, input, { signal: controller.signal }) :
              await linkStart.recover(sessionId, input.pairing_id, { signal: controller.signal });
        } else {
          result = await linkReview.request(sessionId, linkOperation, input, { signal: controller.signal });
        }
        if (!res.destroyed) return json(res, 200, result);
      } catch {
        if (!res.destroyed) return json(res, 503, { error: "linking_unavailable" });
      } finally {
        rawBody?.fill(0);
        res.removeListener("close", disconnected);
      }
      return;
    }

    if (url.pathname === "/api/agent/auth/session" && req.method === "GET") {
      return json(res, 200, {
        authenticated: true,
        authority: {
          version: 1,
          site_id: session.principal.siteId,
          ha_issuer_id: session.principal.haIssuerId,
        },
        user_id: session.principal.userId,
        is_admin: session.principal.isAdmin,
        csrf_token: session.csrf,
        ...(linkReview ? { shared_link_review_enabled: true } : {}),
        ...(lighting ? { lighting_enabled: true } : {}),
        ...(personalMemory ? { personal_memory_enabled: true,
          personal_memory_home_origins:config.personalMemoryHomeOrigins ?? [] } : {}),
        ...(linkStart ? { shared_link_start_enabled: true } : {}),
        ...(linkStart?.authenticationEnabled ? { shared_link_auth_enabled: true } : {}),
        ...(linkStart?.browserSetup ? { shared_link_setup: linkStart.browserSetup } : {}),
      });
    }

    if (url.search || !routeAllowed(req.method, url.pathname)) {
      return json(res, 404, { error: "route_not_allowed", request_id: requestId });
    }
    if (req.method !== "GET" && !isFreshIdentityRoute) {
      if (!requireOrigin(req, config, res) || !requireCsrf(req, session, res)) return;
    }

    return proxyCoreRequest(
      config,
      req,
      res,
      url,
      session.principal,
      fetchImpl,
      requestId,
      /^\/api\/agent/,
    );
  });
  server.maxHeadersCount = 64;
  server.maxRequestsPerSocket = 100;
  server.setTimeout(REQUEST_TIMEOUT_MS);
  server.on("close", () => {
    sessions?.stopCleanup();
    if (ownsStore) sessions?.close();
  });
  return server;
}

export {
  COOKIE_NAME,
  EMPTY_BODY_SHA256,
  OAUTH_COOKIE_NAME,
  MAX_BODY_BYTES,
  MAX_CORE_RESPONSE_BYTES,
  REQUEST_TIMEOUT_MS,
  NATIVE_ATTESTED_CHANNEL,
  NATIVE_CHALLENGE_PATH,
  NATIVE_PROOF_TYPE,
  NATIVE_ROUTES,
  ROUTES,
  NativeAttestationStore,
  SessionStore,
  configFromEnv,
  createBff,
  createVictoriaSessionStore,
  isAllowedOrigin,
  jsonHasDuplicateObjectKeys,
  loadNativeInstallationRegistry,
  nativeRouteAllowed,
  normalizeParentRelationshipBody,
  normalizePrincipalBindingBody,
  parseCookies,
  randomToken,
  routeAllowed,
  safePostLoginRedirect,
  sessionCookie,
};
