import fs from "node:fs";
import { VIA_SERVE } from "./home-location.mjs";
import { lookOnlyClient } from "./look-only.mjs";
import { hasBearer, lightingBrainRouteKind, LIGHTING_BRAIN_PREFIX } from "./lighting-brain-route.mjs";
import { DEN_OPS_PREFIX } from "./den-ops-status.mjs";

function bearerProducer(req) {
  if (req.method !== "POST" || !hasBearer(req.headers)) return false;
  if (req.url.startsWith(LIGHTING_BRAIN_PREFIX + "/")) {
    return lightingBrainRouteKind(req.method, req.url.slice(LIGHTING_BRAIN_PREFIX.length)) === "producer";
  }
  return req.url.startsWith(DEN_OPS_PREFIX + "/") &&
    /^\/hosts\/[a-z0-9][a-z0-9-]{0,63}\/status$/.test(req.url.slice(DEN_OPS_PREFIX.length));
}

function urlFrom(value) {
  try { return new URL(value); } catch { return null; }
}

export function serveHomeOrigin(value) {
  const origin = urlFrom(value);
  return origin?.protocol === "https:" && origin.origin === value ? origin : null;
}

// A full-Home Serve connection is admitted before route or Agent dispatch.
export function serveHomeAdmission(req, origin, { upgrade = false } = {}) {
  if (!req.socket[VIA_SERVE]) return null;
  if (!origin || !lookOnlyClient(req) || req.headers.host?.toLowerCase() !== origin.host) return false;
  const pathname = req.url?.split("?", 1)[0];
  if (!pathname?.startsWith("/") || pathname.includes("//") || pathname.includes("\\")) return false;
  const safe = req.method === "GET" || req.method === "HEAD";
  const supplied = req.headers.origin;
  if (supplied !== undefined && supplied !== origin.origin) return false;
  if (supplied === undefined && (upgrade || (!safe && !bearerProducer(req)))) return false;
  if (["same-site", "cross-site"].includes(req.headers["sec-fetch-site"])) {
    return safe && !upgrade && req.headers["sec-fetch-mode"] === "navigate" && req.headers["sec-fetch-dest"] === "document";
  }
  return true;
}

export function readServeCredential(file, pattern) {
  if (!file) return "";
  let fd;
  try {
    if (!fs.lstatSync(file).isFile()) return "";
    fd = fs.openSync(file, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.size > 4096 || (process.platform !== "win32" && (stat.mode & 0o077) !== 0)) return "";
    const value = fs.readFileSync(fd, "utf8").replace(/\r?\n$/, "");
    return pattern.test(value) ? value : "";
  } catch {
    return "";
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}

export const SERVE_LIGHTING_PREFIX = "/api/home/lighting";
const MAX_REQUEST_BYTES = 2048;
const MAX_RESPONSE_BYTES = 32 * 1024;
const RELAY_TIMEOUT_MS = 105_000;
const RELAY_ERRORS = new Map([
  [404, "not_found"], [409, "conflict"], [413, "invalid_request"],
  [422, "invalid_request"], [429, "lighting_busy"],
]);

export function createServeLightingRelay({ bffTarget, credentialFile, sendJson }) {
  const credential = readServeCredential(credentialFile, /^[a-f0-9]{64}$/);
  const bff = urlFrom(bffTarget);
  const configured = credential && bff?.protocol === "http:" && bff.hostname === "127.0.0.1"
    && !bff.username && !bff.password;

  return async function relay(req, res, admitted) {
    const operation = req.url === SERVE_LIGHTING_PREFIX + "/action" ? "action"
      : req.url === SERVE_LIGHTING_PREFIX + "/outcome" ? "outcome" : null;
    const error = (status, code) => sendJson(res, status, { ok: false, error: code }, { "Cache-Control": "no-store" });
    const reject = (status, code) => { req.resume(); error(status, code); };
    if (!admitted || !operation) return reject(404, "not_found");
    if (req.method !== "POST") return reject(405, "method_not_allowed");
    if (!configured) return reject(503, "home_lighting_unavailable");
    if (req.headers["x-home-lighting-csrf"] !== "1") return reject(403, "lighting_csrf_required");
    if (req.headers["content-type"] !== "application/json" || req.headers["content-encoding"] !== undefined) {
      return reject(415, "json_required");
    }
    const length = String(req.headers["content-length"] ?? "");
    if (req.headers["transfer-encoding"] !== undefined || !/^\d{1,6}$/.test(length)) {
      return reject(411, "length_required");
    }
    if (Number(length) > MAX_REQUEST_BYTES) return reject(413, "body_too_large");

    try {
      const chunks = [];
      let requestSize = 0;
      for await (const chunk of req) {
        requestSize += chunk.byteLength;
        if (requestSize > MAX_REQUEST_BYTES) return error(413, "body_too_large");
        chunks.push(chunk);
      }
      const response = await fetch(new URL("/internal/home/serve-lighting/" + operation, bff), {
        method: "POST",
        headers: { authorization: "Bearer " + credential, "content-type": "application/json" },
        body: Buffer.concat(chunks),
        redirect: "error",
        signal: AbortSignal.timeout(RELAY_TIMEOUT_MS),
      });
      if (!response.headers.get("content-type")?.startsWith("application/json") || !response.body) {
        await response.body?.cancel();
        return error(502, "lighting_outcome_unknown");
      }
      const reply = [];
      let size = 0;
      for await (const chunk of response.body) {
        size += chunk.byteLength;
        if (size > MAX_RESPONSE_BYTES) break;
        reply.push(chunk);
      }
      if (size > MAX_RESPONSE_BYTES) return error(502, "lighting_outcome_unknown");
      const answer = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(reply)));
      if (response.ok && answer?.version === 1 && answer.result && typeof answer.result === "object") {
        return sendJson(res, 200, { version: 1, result: answer.result }, { "Cache-Control": "no-store" });
      }
      if (RELAY_ERRORS.get(response.status) === answer?.error ||
          response.status === 503 && ["home_lighting_unavailable", "lighting_outcome_unknown"].includes(answer?.error)) {
        return error(response.status, answer.error);
      }
      return error(502, "lighting_outcome_unknown");
    } catch {
      if (!res.destroyed) return error(502, "lighting_outcome_unknown");
    }
  };
}
