// Server-side HA 2026.9.3 login-flow transport. This is NOT an identity proof
// issuer: the returned code still needs exchange, subject verification, durable
// challenge consumption, and governed owner confirmation before linking.
import { randomBytes } from "node:crypto";
import { performance } from "node:perf_hooks";
import { readHaJson } from "./ha-response.mjs";

const DIGEST = /^[a-f0-9]{64}$/;
const FLOW = /^[a-f0-9]{32}$/;
const LIFETIME = 300_000;
const MAX_RESPONSE = 32_768;
const fail = () => new Error("fresh_auth_flow_rejected");

function httpsUrl(value, root = false) {
  const url = new URL(value);
  if (url.protocol !== "https:" || url.username || url.password || url.hash ||
      url.search || (root && url.pathname !== "/")) throw fail();
  return url.href;
}

export class HaLoginFlow {
  #flows = new Map();
  #cleaning = 0;
  #requests = 0;
  #origin; #client; #redirect; #issuer; #fetch; #wall; #mono;

  constructor({ origin, clientId, redirectUri, issuerId,
    fetchImpl = globalThis.fetch, wallClock = Date.now,
    monotonicClock = () => performance.now() }) {
    this.#origin = httpsUrl(origin, true);
    this.#client = httpsUrl(clientId);
    this.#redirect = httpsUrl(redirectUri);
    if (!["home-assistant:echo", "home-assistant:victoria"].includes(issuerId) ||
        new URL(this.#client).origin !== new URL(this.#redirect).origin) throw fail();
    this.#issuer = issuerId;
    this.#fetch = fetchImpl;
    this.#wall = wallClock;
    this.#mono = monotonicClock;
  }

  #live(entry) {
    const wallAge = this.#wall() - entry.wall;
    const monoAge = this.#mono() - entry.mono;
    // Clock discontinuity invalidates rather than extending the ceremony.
    return Number.isFinite(wallAge) && Number.isFinite(monoAge) &&
      wallAge >= 0 && monoAge >= 0 && wallAge < LIFETIME &&
      monoAge < LIFETIME && Math.abs(wallAge - monoAge) <= 1000;
  }

  get binding() {
    const site = this.#issuer === "home-assistant:echo" ? "echo" : "victoria";
    return JSON.stringify([this.#issuer, site, new URL(this.#origin).origin, this.#client, this.#redirect]);
  }

  async #post(path, body) {
    if (this.#requests >= 64) throw fail();
    this.#requests++;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10_000);
    try {
      const response = await this.#fetch(new URL(path, this.#origin), {
        method: body === undefined ? "DELETE" : "POST", redirect: "error", credentials: "omit",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify(body), signal: controller.signal,
      });
      if (response.status !== 200 || response.redirected ||
          !/^application\/json(?:;|$)/i.test(response.headers.get("content-type") || "")) throw fail();
      return await readHaJson(response, MAX_RESPONSE);
    } catch {
      // Neither upstream response bodies nor credential-bearing exceptions leak.
      throw fail();
    } finally {
      controller.abort();
      clearTimeout(timer);
      this.#requests--;
    }
  }

  async #discard(id) {
    const entry = this.#flows.get(id);
    this.#flows.delete(id);
    if (entry?.flow) {
      // Best effort only. Losing the upstream response never restores a handle.
      await this.#cancelFlow(entry.flow);
    }
  }

  async #cancelFlow(flow) {
    this.#cleaning++;
    try { await this.#post(`/auth/login_flow/${flow}`, undefined).catch(() => {}); }
    finally { this.#cleaning--; }
  }

  #accept(entry, result, initial) {
    if (!this.#live(entry) || !FLOW.test(result.flow_id) ||
        (entry.flow && result.flow_id !== entry.flow) ||
        !Array.isArray(result.handler) || result.handler.length !== 2 ||
        result.handler[0] !== "homeassistant" || result.handler[1] !== null) throw fail();
    entry.flow = result.flow_id;
    if (result.type === "create_entry") {
      if (initial || typeof result.result !== "string" ||
          !/^[a-f0-9]{32,128}$/.test(result.result)) throw fail();
      return Object.freeze({ status: "code", code: result.result,
        issuerId: this.#issuer, sessionCommitment: entry.session,
        challengeCommitment: entry.challenge,
        // Conservative lower bound, NOT an invented HA auth_time claim.
        ceremonyStartedAt: entry.wall, expiresAt: entry.wall + LIFETIME });
    }
    if (result.type !== "form" || !Array.isArray(result.data_schema) ||
        result.data_schema.length < 1 || result.data_schema.length > 8 ||
        typeof result.step_id !== "string" || !/^[a-z_]{1,64}$/.test(result.step_id)) throw fail();
    const fields = result.data_schema.map(field => field.name);
    const permitted = ["username", "password", "code", "multi_factor_auth_module"];
    if (new Set(fields).size !== fields.length || fields.some(name => !permitted.includes(name)) ||
        (initial && (result.step_id !== "init" || !fields.includes("username") || !fields.includes("password")))) throw fail();
    entry.fields = fields;
    entry.choices = undefined;
    if (fields.includes("multi_factor_auth_module")) {
      const options = result.data_schema.find(field => field.name === "multi_factor_auth_module").options;
      if (!Array.isArray(options) || options.length < 1 || options.length > 16 ||
          options.some(pair => !Array.isArray(pair) || pair.length !== 2 ||
            typeof pair[0] !== "string" || !/^[a-zA-Z0-9_-]{1,64}$/.test(pair[0]) ||
            typeof pair[1] !== "string" || pair[1].length < 1 || pair[1].length > 128) ||
          new Set(options.map(pair => pair[0])).size !== options.length) throw fail();
      entry.choices = options.map(pair => Object.freeze([...pair]));
    }
    // Do not relay arbitrary provider schema, defaults, descriptions or HTML.
    return Object.freeze({ status: "form", step: result.step_id,
      fields: Object.freeze([...fields]),
      ...(entry.choices ? { choices: Object.freeze([...entry.choices]) } : {}),
      invalid: Object.keys(result.errors || {}).length > 0 });
  }

  async begin({ sessionCommitment, challengeCommitment }) {
    if (!DIGEST.test(sessionCommitment) || !DIGEST.test(challengeCommitment)) throw fail();
    const expired = [];
    for (const [id, entry] of this.#flows) if (!this.#live(entry)) expired.push(this.#discard(id));
    await Promise.all(expired);
    if (this.#flows.size + this.#cleaning >= 32 || [...this.#flows.values()].some(entry =>
      entry.session === sessionCommitment || entry.challenge === challengeCommitment)) throw fail();
    const id = randomBytes(32).toString("hex");
    const entry = { session: sessionCommitment, challenge: challengeCommitment,
      wall: this.#wall(), mono: this.#mono(), busy: true, attempts: 0 };
    this.#flows.set(id, entry);
    try {
      const result = await this.#post("/auth/login_flow", {
        client_id: this.#client, redirect_uri: this.#redirect,
        handler: ["homeassistant", null], type: "authorize",
      });
      if (this.#flows.get(id) !== entry) {
        if (FLOW.test(result.flow_id)) await this.#cancelFlow(result.flow_id);
        throw fail();
      }
      // Retain only the syntactically valid upstream ID before freshness/schema
      // validation so rejection can still cancel the newly created HA flow.
      if (typeof result.flow_id === "string" && FLOW.test(result.flow_id)) entry.flow = result.flow_id;
      return { handle: id, ...this.#accept(entry, result, true) };
    } catch { await this.#discard(id); throw fail(); }
    finally { entry.busy = false; }
  }

  async submit({ handle, sessionCommitment, input }) {
    const entry = this.#flows.get(handle);
    if (!entry || entry.session !== sessionCommitment || entry.busy) throw fail();
    if (!this.#live(entry) || ++entry.attempts > 8) {
      await this.#discard(handle); throw fail();
    }
    if (!input || Object.getPrototypeOf(input) !== Object.prototype ||
        Object.keys(input).length !== entry.fields.length ||
        entry.fields.some(name => !Object.hasOwn(input, name) || typeof input[name] !== "string" ||
          input[name].length < 1 || Buffer.byteLength(input[name]) > 1024)) throw fail();
    if (entry.choices && !entry.choices.some(pair => pair[0] === input.multi_factor_auth_module)) throw fail();
    entry.busy = true;
    try {
      const result = await this.#post(`/auth/login_flow/${entry.flow}`, {
        ...input, client_id: this.#client,
      });
      if (this.#flows.get(handle) !== entry) throw fail();
      const accepted = this.#accept(entry, result, false);
      if (accepted.status === "code") this.#flows.delete(handle);
      return accepted;
    } catch { await this.#discard(handle); throw fail(); }
    finally { entry.busy = false; }
  }

  async revokeSession(sessionCommitment) {
    const cleanups = [];
    for (const [id, entry] of this.#flows) {
      if (entry.session === sessionCommitment) cleanups.push(this.#discard(id));
    }
    await Promise.all(cleanups);
  }

  async cancel({ handle, sessionCommitment }) {
    const entry = this.#flows.get(handle);
    if (entry?.session === sessionCommitment) await this.#discard(handle);
  }
}
