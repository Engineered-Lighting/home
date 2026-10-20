// Den activity-lighting ops status (AL-5): the always-on monitors on the LA
// host and the Victoria Jetson report their back-off level here, and Home
// shows one banner from GET /api/den-ops/v1/status.
//
// - POST /api/den-ops/v1/hosts/{host}/status: a monitor's own bearer token
//   (only its SHA-256 is configured), schema den-ops-status-1.
// - GET /api/den-ops/v1/status: the gateway session, or any monitor token
//   (the acting monitor reads the other hosts' levels), schema den-ops-banner-1.
//
// Statuses live in memory. Monitors re-post every check (about a minute), so
// a gateway restart heals itself; a host that stops posting turns stale.
import crypto from "node:crypto";
import fs from "node:fs";

export const DEN_OPS_PREFIX = "/api/den-ops/v1";
export const DEN_OPS_STEPS = ["normal", "cadence_reduced", "deep_stack_paused", "loop_stopped"];
const HOST_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;
const SIGNAL_RE = /^[a-z0-9][a-z0-9_.:-]{0,63}$/;
const MAX_BODY_BYTES = 16 * 1024;
const TOKENS_MAX_BYTES = 64 * 1024;

function sha256Hex(value) {
  return crypto.createHash("sha256").update(String(value)).digest("hex");
}

export function loadDenOpsTokens(file, { platform = process.platform } = {}) {
  const disabled = { enabled: false, monitors: [] };
  if (!file) return disabled;
  try {
    const stat = fs.statSync(file);
    if (!stat.isFile() || stat.size > TOKENS_MAX_BYTES) throw new Error("invalid");
    if (platform !== "win32" && (stat.mode & 0o077) !== 0) {
      console.warn("[den-ops] HOME_WEB_DEN_OPS_TOKENS_FILE must not be readable by group or others; den-ops status disabled");
      return disabled;
    }
    const doc = JSON.parse(fs.readFileSync(file, "utf8"));
    if (doc?.schema !== "den-ops-monitor-tokens-1" || !Array.isArray(doc.monitors)) throw new Error("invalid");
    const monitors = doc.monitors.map((entry) => {
      if (!HOST_RE.test(String(entry?.host || "")) || !/^[0-9a-f]{64}$/.test(String(entry?.token_sha256 || ""))) {
        throw new Error("invalid");
      }
      return { host: entry.host, digest: Buffer.from(entry.token_sha256, "hex") };
    });
    return monitors.length ? { enabled: true, monitors } : disabled;
  } catch {
    console.warn("[den-ops] HOME_WEB_DEN_OPS_TOKENS_FILE is missing or invalid; den-ops status disabled");
    return disabled;
  }
}

// The monitor host a bearer token belongs to, or null.
export function denOpsMonitorFor(tokens, authorization) {
  const match = /^Bearer ([A-Za-z0-9._~+/=-]{32,512})$/.exec(String(authorization || ""));
  if (!match || !tokens.enabled) return null;
  const digest = Buffer.from(sha256Hex(match[1]), "hex");
  let host = null;
  for (const monitor of tokens.monitors) {
    if (crypto.timingSafeEqual(digest, monitor.digest)) host = monitor.host;
  }
  return host;
}

function boundedString(value, max) {
  return typeof value === "string" && value.length <= max;
}

function finiteOrNull(value) {
  return value === null || (typeof value === "number" && Number.isFinite(value));
}

// Returns the normalized status or throws Error(code).
export function parseDenOpsStatus(doc, host) {
  const fail = (code) => { throw new Error(code); };
  if (!doc || typeof doc !== "object" || Array.isArray(doc)) fail("status_invalid");
  if (doc.schema !== "den-ops-status-1") fail("status_schema");
  if (doc.host !== host) fail("status_host_mismatch");
  if (!Number.isInteger(doc.level) || doc.level < 0 || doc.level > 3) fail("status_level");
  if (doc.step !== DEN_OPS_STEPS[doc.level]) fail("status_step");
  if (typeof doc.latched !== "boolean") fail("status_latched");
  if (!boundedString(doc.role, 32)) fail("status_role");
  if (!finiteOrNull(doc.since) || !finiteOrNull(doc.checked_at) || doc.checked_at === null) fail("status_time");
  if (!boundedString(doc.message, 240)) fail("status_message");
  if (!Array.isArray(doc.reasons) || doc.reasons.length > 20) fail("status_reasons");
  const reasons = doc.reasons.map((reason) => {
    if (!reason || typeof reason !== "object" || !SIGNAL_RE.test(String(reason.signal || "")) ||
        !Number.isInteger(reason.level) || reason.level < 0 || reason.level > 3 ||
        !boundedString(reason.detail, 240)) {
      fail("status_reason");
    }
    return { signal: reason.signal, level: reason.level, detail: reason.detail };
  });
  return {
    schema: doc.schema, host, role: doc.role, level: doc.level, step: doc.step, latched: doc.latched,
    since: doc.since, checked_at: doc.checked_at, message: doc.message, reasons,
  };
}

export function createDenOpsStore({ staleS = 300, now = () => Date.now() / 1000 } = {}) {
  const hosts = new Map();
  return {
    put(status) {
      hosts.set(status.host, { ...status, received_at: now() });
    },
    banner() {
      const at = now();
      const list = [...hosts.values()].sort((a, b) => a.host.localeCompare(b.host)).map((status) => {
        const age = Math.max(0, at - status.received_at);
        return { ...status, age_s: Math.round(age * 10) / 10, stale: age > staleS };
      });
      const live = list.filter((status) => !status.stale);
      const level = live.reduce((max, status) => Math.max(max, status.level), 0);
      let banner = null;
      if (level > 0) {
        const worst = live.filter((status) => status.level === level)[0];
        banner = { severity: level >= 2 ? "alert" : "warn", host: worst.host, text: worst.message };
      } else if (list.some((status) => status.stale)) {
        const silent = list.filter((status) => status.stale).map((status) => status.host);
        banner = { severity: "warn", host: silent[0], text: `Ops monitor silent on ${silent.join(", ")}` };
      } else {
        const notice = live.find((status) => status.reasons.length);
        if (notice) banner = { severity: "notice", host: notice.host, text: notice.message };
      }
      return {
        schema: "den-ops-banner-1", generated_at: at, level, step: DEN_OPS_STEPS[level],
        banner, hosts: list,
      };
    },
  };
}

export function readDenOpsBody(req) {
  return new Promise((resolve, reject) => {
    const declared = Number(req.headers["content-length"]);
    if (!Number.isInteger(declared) || declared < 0) {
      reject(Object.assign(new Error("length_required"), { status: 411 }));
      return;
    }
    if (declared > MAX_BODY_BYTES) {
      reject(Object.assign(new Error("body_too_large"), { status: 413 }));
      req.resume();
      return;
    }
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(Object.assign(new Error("body_too_large"), { status: 413 }));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
      } catch {
        reject(Object.assign(new Error("invalid_json"), { status: 400 }));
      }
    });
    req.on("error", () => reject(Object.assign(new Error("request_invalid"), { status: 400 })));
  });
}

// Handles every /api/den-ops/v1 request. `sessionOk(req, res)` is the
// gateway's own session check (it answers 401 itself when it returns false).
export async function handleDenOps(req, res, { pathname, tokens, store, sessionOk, sendJson }) {
  if (!tokens.enabled) {
    sendJson(res, 404, { ok: false, error: "capability_disabled", capability: "den_ops_status" });
    return;
  }
  const suffix = pathname.slice(DEN_OPS_PREFIX.length);
  const monitor = denOpsMonitorFor(tokens, req.headers.authorization);
  if (suffix === "/status") {
    if (req.method !== "GET") {
      sendJson(res, 405, { ok: false, error: "method_not_allowed" }, { Allow: "GET" });
      return;
    }
    if (!monitor && !sessionOk(req, res)) return;
    sendJson(res, 200, store.banner());
    return;
  }
  const match = /^\/hosts\/([^/]+)\/status$/.exec(suffix);
  if (!match || !HOST_RE.test(match[1])) {
    sendJson(res, 404, { ok: false, error: "not_found" });
    return;
  }
  if (req.method !== "POST") {
    sendJson(res, 405, { ok: false, error: "method_not_allowed" }, { Allow: "POST" });
    return;
  }
  if (!monitor) {
    sendJson(res, 401, { ok: false, error: "monitor_token_required" });
    return;
  }
  if (monitor !== match[1]) {
    sendJson(res, 403, { ok: false, error: "monitor_host_mismatch" });
    return;
  }
  let doc;
  try {
    doc = await readDenOpsBody(req);
  } catch (error) {
    sendJson(res, error.status || 400, { ok: false, error: error.message });
    return;
  }
  let status;
  try {
    status = parseDenOpsStatus(doc, monitor);
  } catch (error) {
    sendJson(res, 422, { ok: false, error: error.message });
    return;
  }
  store.put(status);
  sendJson(res, 200, { ok: true, host: monitor, level: status.level });
}
