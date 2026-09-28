import assert from "node:assert/strict";
import test from "node:test";
import { HaLoginFlow } from "../src/ha-login-flow.mjs";

const sessionCommitment = "a".repeat(64);
const challengeCommitment = "b".repeat(64);
const form = (overrides = {}) => ({ type: "form", flow_id: "c".repeat(32),
  handler: ["homeassistant", null], step_id: "init", errors: {},
  data_schema: [{ name: "username" }, { name: "password" }], ...overrides });
const code = () => form({ type: "create_entry", result: "d".repeat(64) });
const input = { username: "owner", password: "not-a-real-password" };
function fixture(results) {
  let wall = 100_000, mono = 1000;
  const calls = [];
  const flow = new HaLoginFlow({ origin: "https://ha.example/", clientId: "https://home.example/",
    redirectUri: "https://home.example/callback", issuerId: "home-assistant:victoria",
    wallClock: () => wall, monotonicClock: () => mono,
    fetchImpl: async (url, options) => {
      calls.push({ url: url.href, ...options, body: options.body ? JSON.parse(options.body) : undefined });
      if (options.method === "DELETE") return new Response("{}", { headers: { "content-type": "application/json" } });
      const value = results.shift();
      if (typeof value === "function") return value();
      return new Response(JSON.stringify(value), { headers: { "content-type": "application/json" } });
    } });
  return { flow, calls, begin: () => flow.begin({ sessionCommitment, challengeCommitment }),
    advance: (w, m = w) => { wall += w; mono += m; } };
}

test("real protocol fixes provider/endpoints and returns one-use server code with conservative time", async () => {
  const f = fixture([form(), code()]);
  const start = await f.begin();
  f.advance(2000);
  const result = await f.flow.submit({ handle: start.handle, sessionCommitment, input });
  assert.equal(result.status, "code");
  assert.equal(result.issuerId, "home-assistant:victoria");
  assert.equal(result.ceremonyStartedAt, 100_000);
  assert.equal(result.expiresAt, 400_000);
  assert.equal(result.challengeCommitment, challengeCommitment);
  assert.equal(Object.hasOwn(result, "authenticated_at"), false);
  assert.equal(Object.hasOwn(start, "flow_id"), false);
  assert.deepEqual(f.calls[0].body.handler, ["homeassistant", null]);
  assert.equal(f.calls[0].body.redirect_uri, "https://home.example/callback");
  assert.equal(f.calls[1].url, `https://ha.example/auth/login_flow/${"c".repeat(32)}`);
  assert.equal(f.calls[1].redirect, "error");
  assert.equal(f.calls[1].credentials, "omit");
  await assert.rejects(f.flow.submit({ handle: start.handle, sessionCommitment, input }));
});

for (const [name, wall, mono] of [["expiry", 300_000, 300_000], ["rollback", -1, 1], ["clock jump", 2000, 0]]) {
  test(`initial response rejected after ${name} still cancels the returned flow`, async () => {
    const f = fixture([() => {
      f.advance(wall, mono);
      return new Response(JSON.stringify(form()), { headers: { "content-type": "application/json" } });
    }]);
    await assert.rejects(f.begin(), /fresh_auth_flow_rejected/);
    assert.equal(f.calls.length, 2);
    assert.equal(f.calls[1].method, "DELETE");
    assert.equal(f.calls[1].url, `https://ha.example/auth/login_flow/${"c".repeat(32)}`);
  });
}

test("MFA continuation and invalid credentials do not mint a completed result", async () => {
  const f = fixture([form(), form({ errors: { base: "invalid_auth" } }),
    form({ step_id: "mfa", data_schema: [{ name: "code" }] }), code()]);
  const { handle } = await f.begin();
  assert.equal((await f.flow.submit({ handle, sessionCommitment, input })).invalid, true);
  assert.deepEqual((await f.flow.submit({ handle, sessionCommitment, input })).fields, ["code"]);
  await assert.rejects(f.flow.submit({ handle, sessionCommitment, input }));
  assert.equal((await f.flow.submit({ handle, sessionCommitment, input: { code: "123456" } })).status, "code");
});

for (const [name, result] of Object.entries({
  automatic: code(), trusted_network: form({ handler: ["trusted_networks", null] }),
  injection: form({ data_schema: [{ name: "client_id" }] }),
  path: form({ flow_id: "../auth/token" }),
})) test(`rejects initial ${name}`, async () => {
  const f = fixture([result]);
  await assert.rejects(f.begin(), /fresh_auth_flow_rejected/);
});

for (const [name, wall, mono] of [["expiry", 300_000, 300_000],
  ["rollback", -1, 1], ["clock jump", 2000, 0]]) {
  test(`invalidates on ${name} before forwarding credentials`, async () => {
    const f = fixture([form(), code()]);
    const { handle } = await f.begin();
    f.advance(wall, mono);
    await assert.rejects(f.flow.submit({ handle, sessionCommitment, input }));
    assert.equal(f.calls.filter(call => call.method === "POST").length, 1);
    assert.equal(f.calls.at(-1).method, "DELETE");
  });
}

test("session mismatch and caller-selected protocol fields never reach HA", async () => {
  const f = fixture([form(), code()]);
  const { handle } = await f.begin();
  await assert.rejects(f.flow.submit({ handle, sessionCommitment: "e".repeat(64), input }));
  await assert.rejects(f.flow.submit({ handle, sessionCommitment, input: { ...input, client_id: "https://evil.example" } }));
  assert.equal(f.calls.length, 1);
});

test("one in-flight request and revocation suppress a late completion", async () => {
  let resolve;
  const f = fixture([form(), () => new Promise(r => { resolve = r; })]);
  const { handle } = await f.begin();
  const pending = f.flow.submit({ handle, sessionCommitment, input });
  await assert.rejects(f.flow.submit({ handle, sessionCommitment, input }));
  await f.flow.revokeSession(sessionCommitment);
  resolve(new Response(JSON.stringify(code()), { headers: { "content-type": "application/json" } }));
  await assert.rejects(pending);
});

test("oversized upstream body rejects without reflecting sensitive content", async () => {
  const f = fixture([() => new Response(JSON.stringify({ secret: "x".repeat(40_000) }),
    { headers: { "content-type": "application/json" } })]);
  await assert.rejects(f.begin(), error => error.message === "fresh_auth_flow_rejected");
});

test("duplicate active challenge/session refused and restart cannot resume a handle", async () => {
  const f = fixture([form()]);
  const { handle } = await f.begin();
  await assert.rejects(f.begin());
  await assert.rejects(fixture([]).flow.submit({ handle, sessionCommitment, input }));
});

test("uncertain HTTP delivery consumes local flow and cannot be retried", async () => {
  const f = fixture([form(), () => { throw new Error("secret upstream details"); }]);
  const { handle } = await f.begin();
  await assert.rejects(f.flow.submit({ handle, sessionCommitment, input }), { message: "fresh_auth_flow_rejected" });
  await assert.rejects(f.flow.submit({ handle, sessionCommitment, input }));
  assert.equal(f.calls.filter(call => call.method === "POST").length, 2);
  assert.equal(f.calls.at(-1).method, "DELETE");
});

test("multiple MFA options survive normalization and constrain submitted selection", async () => {
  const f = fixture([form(), form({ step_id: "select_mfa_module", data_schema: [
    { name: "multi_factor_auth_module", type: "select", options: [["totp", "Authenticator"], ["notify", "Notification"]] },
  ] }), form({ step_id: "mfa", data_schema: [{ name: "code" }] }), code()]);
  const { handle } = await f.begin();
  const selection = await f.flow.submit({ handle, sessionCommitment, input });
  assert.deepEqual(selection.choices, [["totp", "Authenticator"], ["notify", "Notification"]]);
  await assert.rejects(f.flow.submit({ handle, sessionCommitment, input: { multi_factor_auth_module: "unregistered" } }));
  const next = await f.flow.submit({ handle, sessionCommitment, input: { multi_factor_auth_module: "totp" } });
  assert.equal(next.step, "mfa");
  assert.equal((await f.flow.submit({ handle, sessionCommitment, input: { code: "123456" } })).status, "code");
  assert.equal(f.calls.some(call => call.method === "DELETE"), false);
});

test("revocation while initial request is pending cancels the subsequently known HA flow", async () => {
  let resolve;
  const f = fixture([() => new Promise(r => { resolve = r; })]);
  const pending = f.begin();
  // begin first awaits its expired-flow cleanup batch.
  await new Promise(r => setImmediate(r));
  await f.flow.revokeSession(sessionCommitment);
  resolve(new Response(JSON.stringify(form()), { headers: { "content-type": "application/json" } }));
  await assert.rejects(pending);
  assert.equal(f.calls.at(-1).method, "DELETE");
});
