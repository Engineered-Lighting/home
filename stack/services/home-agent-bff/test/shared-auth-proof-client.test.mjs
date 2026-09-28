import assert from "node:assert/strict";
import test from "node:test";
import { SharedAuthProofClient } from "../src/shared-auth-proof-client.mjs";

const endpoint = "https://proof.internal/internal/shared-identity/v1/auth-proofs";
const credential = "a".repeat(64);
const submission = () => ({ proof_id: "12345678-1234-1234-1234-123456789abc", subject: "same-id",
  session_commitment: "b".repeat(64), challenge_commitment: "c".repeat(64),
  authenticated_at: "2026-09-26T12:00:00.000Z", registration_revision: 1 });
const receipt = (value, changes = {}) => ({ version: 1, proof: { ...value,
  issuer_id: "home-assistant:echo", issued_at: "2026-09-26T12:00:01Z",
  expires_at: "2026-09-26T12:05:00Z", ...changes } });
const response = (value) => new Response(JSON.stringify(value), { headers: { "content-type": "application/json" } });
const client = (fetchImpl, overrides = {}) => new SharedAuthProofClient({
  endpoint, credential, issuerId: "home-assistant:echo", fetchImpl,
  now: () => Date.parse("2026-09-26T12:00:02Z"), ...overrides });

test("fixed service endpoint and exact receipt, with no browser credentials or redirects", async () => {
  const input = submission();
  const transport = client(async (url, options) => {
    assert.equal(url, endpoint);
    assert.equal(options.redirect, "error");
    assert.equal(options.credentials, "omit");
    assert.equal(options.headers.authorization, `Bearer ${credential}`);
    assert.deepEqual(JSON.parse(options.body), { version: 1, proof: input });
    return response(receipt(input));
  });
  assert.deepEqual(await transport.issue(input), receipt(input).proof);
});

test("configuration cannot include caller URLs, insecure transport or unknown issuer", () => {
  for (const changes of [{ endpoint: endpoint.replace("https:", "http:") },
    { endpoint: endpoint + "?target=echo" }, { endpoint: "https://u:p@proof.internal" },
    { issuerId: "echo" }, { credential: "short" }]) {
    assert.throws(() => client(() => assert.fail(), changes));
  }
});

test("invalid submissions fail before dispatch", async () => {
  const transport = client(() => assert.fail());
  for (const changes of [{ issuer_id: "home-assistant:victoria" }, { subject: " padded " },
    { proof_id: "bad" }, { registration_revision: true }, { authenticated_at: "yesterday" }]) {
    await assert.rejects(transport.issue({ ...submission(), ...changes }), /invalid_proof_submission/);
  }
});

for (const [label, changes] of Object.entries({ issuer: { issuer_id: "home-assistant:victoria" },
  subject: { subject: "other" }, revision: { registration_revision: 2 },
  time: { authenticated_at: "2026-09-26T12:00:01Z" },
  window: { expires_at: "2026-09-26T12:06:00Z" }, extra: { unexpected: true } })) {
  test(`reject mismatched ${label} without retry`, async () => {
    let calls = 0;
    const transport = client(async () => { calls++; return response(receipt(submission(), changes)); });
    await assert.rejects(transport.issue(submission()), /^Error: proof_outcome_unknown$/);
    assert.equal(calls, 1);
  });
}

test("oversized and failed responses do not reflect upstream content", async () => {
  for (const make of [() => new Response("private", { status: 500 }),
    () => response({ private: "x".repeat(5000) })]) {
    await assert.rejects(client(async () => make()).issue(submission()), /^Error: proof_outcome_unknown$/);
  }
});

test("cancelled physical requests stay charged until transport settles", async () => {
  const release = [];
  const transport = client(() => new Promise((resolve) => release.push(resolve)));
  const controllers = [new AbortController(), new AbortController()];
  const pending = controllers.map((controller) => transport.issue(submission(), { signal: controller.signal }));
  controllers.forEach((controller) => controller.abort());
  await Promise.all(pending.map((p) => assert.rejects(p, /proof_outcome_unknown/)));
  await assert.rejects(transport.issue(submission()), /proof_transport_busy/);
  release.forEach((resolve) => resolve(response(receipt(submission()))));
  await new Promise((resolve) => setImmediate(resolve));
  const next = transport.issue(submission());
  release[2](response(receipt(submission())));
  assert.equal((await next).subject, "same-id");
});

test("submission is frozen before asynchronous receipt validation", async () => {
  const input = submission();
  let release;
  const transport = client(() => new Promise((resolve) => { release = resolve; }));
  const pending = transport.issue(input);
  input.subject = "mutated";
  release(response(receipt(submission())));
  assert.equal((await pending).subject, "same-id");
});

test("microsecond precision and timezone equivalence are preserved", async () => {
  const input = { ...submission(), authenticated_at: "2026-09-26T05:00:00.000123-07:00" };
  const transport = client(async () => response(receipt(input, {
    authenticated_at: "2026-09-26T12:00:00.000123Z",
    expires_at: "2026-09-26T12:05:00.000123Z" })));
  assert.equal((await transport.issue(input)).authenticated_at, "2026-09-26T12:00:00.000123Z");
  for (const changes of [{ authenticated_at: "2026-09-26T12:00:00.000124Z" },
    { expires_at: "2026-09-26T12:05:00.000124Z" }]) {
    await assert.rejects(client(async () => response(receipt(input, changes))).issue(input), /proof_outcome_unknown/);
  }
});

test("impossible dates and excess precision fail before dispatch", async () => {
  const transport = client(() => assert.fail());
  for (const authenticated_at of ["2026-02-30T12:00:00Z", "2026-09-26T24:00:00Z",
    "0000-09-26T12:00:00Z", "2026-09-26T12:00:00.1234567Z", "2026-09-26T12:00:00+24:00"]) {
    await assert.rejects(transport.issue({ ...submission(), authenticated_at }), /invalid_proof_submission/);
  }
});

test("receipt expiring during transport is not delivered as successful", async () => {
  let now = Date.parse("2026-09-26T12:04:59Z");
  const transport = client(async () => {
    now = Date.parse("2026-09-26T12:05:00Z");
    return response(receipt(submission()));
  }, { now: () => now });
  await assert.rejects(transport.issue(submission()), /proof_outcome_unknown/);
});
