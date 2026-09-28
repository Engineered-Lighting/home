// Shared server-side HA token transport. Configuration is trusted server input;
// callers must enforce their issuer registry before using these operations.
import { readHaJson } from "./ha-response.mjs";

const REQUEST_TIMEOUT_MS = 10_000;
const MAX_HA_TOKEN_BYTES = 64 * 1024;
const MAX_HA_AUTH_RESPONSE_BYTES = 2 * MAX_HA_TOKEN_BYTES + 4096;

function asTokenBuffer(value, label, { required = false } = {}) {
  const token = Buffer.isBuffer(value) ? Buffer.from(value) : Buffer.from(String(value || ""));
  if ((required && token.length === 0) || token.length > MAX_HA_TOKEN_BYTES) {
    token.fill(0);
    throw new Error(`invalid ${label}`);
  }
  return token;
}

async function exchangeCode(config, code, fetchImpl) {
  // HA Core's documented authorization-code endpoint does not authenticate
  // this client or enforce PKCE. The BFF therefore relies on its actual
  // controls: an exact same-origin HTTPS callback, one-time state bound to an
  // HttpOnly initiation cookie, prompt server-side exchange, and no token in
  // browser JavaScript. Do not send ignored verifier parameters and describe
  // them as protection.
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    code,
    client_id: config.clientId,
    redirect_uri: config.redirectUri,
  });
  const response = await fetchImpl(`${config.haUrl}/auth/token`, {
    method: "POST",
    redirect: "error",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  if (!response.ok) throw new Error(`HA token exchange failed (${response.status})`);
  return tokenBuffersFromResponse(await readHaJson(response, MAX_HA_AUTH_RESPONSE_BYTES), "exchange");
}

async function refreshAccessToken(config, refreshToken, fetchImpl) {
  if (!Buffer.isBuffer(refreshToken) || refreshToken.length === 0) {
    throw new Error("HA refresh token is unavailable");
  }
  const body = new URLSearchParams({
    grant_type: "refresh_token",
    refresh_token: refreshToken.toString("utf8"),
    client_id: config.clientId,
  });
  try {
    const response = await fetchImpl(`${config.haUrl}/auth/token`, {
      method: "POST",
      redirect: "error",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    if (!response.ok) throw new Error(`HA token refresh failed (${response.status})`);
    return tokenBuffersFromResponse(await readHaJson(response, MAX_HA_AUTH_RESPONSE_BYTES), "refresh");
  } finally {
    // Remove the only mutable request-side plaintext reference available to
    // us after fetch has consumed the form body.
    body.delete("refresh_token");
  }
}

async function revokeRefreshToken(config, refreshToken, fetchImpl) {
  if (!Buffer.isBuffer(refreshToken) || refreshToken.length === 0) {
    throw new Error("HA refresh token is unavailable");
  }
  const body = new URLSearchParams({
    token: refreshToken.toString("utf8"),
    action: "revoke",
  });
  try {
    const response = await fetchImpl(`${config.haUrl}/auth/token`, {
      method: "POST",
      redirect: "error",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    if (!response.ok) throw new Error(`HA token revocation failed (${response.status})`);
  } finally {
    body.delete("token");
  }
}

function tokenBuffersFromResponse(value, operation) {
  if (!value || typeof value.access_token !== "string" || !value.access_token) {
    throw new Error(`HA token ${operation} returned no access token`);
  }
  if (
    value.refresh_token !== undefined &&
    value.refresh_token !== null &&
    typeof value.refresh_token !== "string"
  ) {
    value.access_token = "";
    value.refresh_token = "";
    throw new Error(`HA token ${operation} returned an invalid refresh token`);
  }
  const expiresIn = value.expires_in;
  let accessToken;
  let refreshToken;
  try {
    accessToken = asTokenBuffer(value.access_token, "HA access token", { required: true });
    refreshToken = asTokenBuffer(value.refresh_token || "", "HA refresh token");
    return { accessToken, refreshToken, expiresIn };
  } catch (error) {
    accessToken?.fill(0);
    refreshToken?.fill(0);
    throw error;
  } finally {
    // JSON strings cannot be zeroized, but dropping them from the response
    // object immediately keeps their lifetime outside the session store short.
    value.access_token = "";
    if (Object.hasOwn(value, "refresh_token")) value.refresh_token = "";
  }
}

async function fetchHaSubject(config, accessToken, fetchImpl) {
  if (!Buffer.isBuffer(accessToken) || accessToken.length === 0) {
    throw new Error("HA access token is unavailable");
  }
  const headers = new Headers({
    Authorization: `Bearer ${accessToken.toString("utf8")}`,
    Accept: "application/json",
  });
  let response;
  try {
    response = await fetchImpl(`${config.haUrl}/api/home_agent_edge/whoami`, {
      headers,
      redirect: "error",
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } finally {
    headers.delete("Authorization");
  }
  if (!response.ok) throw new Error(`HA whoami failed (${response.status})`);
  const value = await readHaJson(response, 16 * 1024);
  if (!value || typeof value.user_id !== "string" || !value.user_id) {
    throw new Error("HA whoami returned no user_id");
  }
  if (value.is_active !== true) throw new Error("HA user is inactive");
  return { userId: value.user_id, isAdmin: value.is_admin === true, isActive: true };
}

export { asTokenBuffer, exchangeCode, refreshAccessToken, revokeRefreshToken, fetchHaSubject };
