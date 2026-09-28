import assert from "node:assert/strict";
import test from "node:test";
import crypto from "node:crypto";
import { SharedSessionRevocationClient } from "../src/shared-session-revocation-client.mjs";

const endpoint = "https://sessions.internal/internal/shared-identity/v1/session-revocations";
const value = () => ({ session_commitment: "a".repeat(64), revocation_id: crypto.randomUUID() });
const response = (request, changes = {}) => new Response(JSON.stringify({ version: 1, revocation: {
  ...request, issuer_id: "home-assistant:victoria", revoked_at: "2026-01-01T00:00:00Z", ...changes,
} }), { headers: { "content-type": "application/json" } });
const client = (fetchImpl) => new SharedSessionRevocationClient({ endpoint, issuerId: "home-assistant:victoria",
  credential: "b".repeat(64), fetchImpl, now: () => Date.parse("2026-09-26T00:00:00Z") });

test("session tombstone transport fixes endpoint and preserves exact retry identity", async () => {
  const request = value(); let calls = 0;
  const transport = client(async (url, init) => {
    calls++; assert.equal(url, endpoint); assert.equal(init.redirect, "error"); assert.equal(init.credentials, "omit");
    assert.deepEqual(JSON.parse(init.body), { version: 1, revocation: request });
    return response(request);
  });
  assert.deepEqual(await transport.revoke(request), await transport.revoke(request));
  assert.equal(calls, 2);
});

for (const change of [{ issuer_id: "home-assistant:echo" }, { session_commitment: "c".repeat(64) },
  { revocation_id: crypto.randomUUID() }, { revoked_at: "2027-01-01T00:00:00Z" }]) {
  test(`session tombstone rejects substituted ${Object.keys(change)[0]}`, async () => {
    const request = value(); let calls = 0;
    await assert.rejects(client(async () => { calls++; return response(request, change); }).revoke(request), /unknown/);
    assert.equal(calls, 1);
  });
}

test("cancelled session delivery remains charged until underlying transport settles", async () => {
  let release, calls = 0;
  const gate = new Promise((resolve) => { release = resolve; });
  const request = value();
  const transport = client(async () => { calls++; await gate; return response(request); });
  const controllers = [new AbortController(), new AbortController()];
  const pending = controllers.map((controller) => transport.revoke(request, { signal: controller.signal }));
  const rejected = pending.map((promise) => assert.rejects(promise, /unknown/));
  controllers.forEach((controller) => controller.abort());
  await Promise.all(rejected);
  await assert.rejects(transport.revoke(request), /unavailable/);
  assert.equal(calls, 2);
  release();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal((await transport.revoke(request)).revocation_id, request.revocation_id);
});

test("caller issuer and malformed commitments are rejected before transport", async () => {
  const transport = client(async () => assert.fail("must not send"));
  await assert.rejects(transport.revoke({ ...value(), issuer_id: "home-assistant:echo" }), /invalid/);
  await assert.rejects(transport.revoke({ ...value(), session_commitment: "invalid" }), /invalid/);
});
