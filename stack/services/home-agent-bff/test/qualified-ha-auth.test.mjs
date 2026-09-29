import assert from "node:assert/strict";
import test from "node:test";
import { QualifiedHaAuth } from "../src/qualified-ha-auth.mjs";

const config = (siteId) => ({ siteId, issuerId: `home-assistant:${siteId}`,
  origin: `https://${siteId}.ha.internal`, clientId: `https://${siteId}.home.internal/`,
  redirectUri: `https://${siteId}.home.internal/auth/callback` });
const json = (value) => new Response(JSON.stringify(value), { headers: { "content-type": "application/json" } });

test("same HA subject stays qualified by independently provisioned issuer", async () => {
  for (const site of ["echo", "victoria"]) {
    const auth = new QualifiedHaAuth({ ...config(site), fetchImpl: async (url, options) => {
      assert.equal(url, `https://${site}.ha.internal/api/home_agent_edge/whoami`);
      assert.equal(options.redirect, "error");
      assert.equal(options.headers.get("authorization"), "Bearer fixture-access");
      return json({ user_id: "same-id", is_active: true, is_admin: false, site_id: "untrusted" });
    } });
    assert.deepEqual(await auth.verify(Buffer.from("fixture-access")), {
      userId: "same-id", isActive: true, isAdmin: false, haIssuerId: `home-assistant:${site}`, siteId: site });
  }
});

test("configuration rejects mixed issuer, insecure origin and foreign callback", () => {
  for (const changes of [{ issuerId: "home-assistant:victoria" }, { origin: "http://echo.internal" },
    { origin: "https://echo.internal/private" }, { redirectUri: "https://foreign.internal/auth/callback" }]) {
    assert.throws(() => new QualifiedHaAuth({ ...config("echo"), ...changes }));
  }
});

test("exchange and revocation use the same fixed HA origin", async () => {
  let calls = 0;
  const auth = new QualifiedHaAuth({ ...config("victoria"), fetchImpl: async (url, options) => {
    calls++;
    assert.equal(url, "https://victoria.ha.internal/auth/token");
    assert.equal(options.redirect, "error");
    if (calls === 1) {
      assert.equal(options.body.get("code"), "a".repeat(32));
      return json({ access_token: "access", refresh_token: "refresh", expires_in: 1800 });
    }
    assert.equal(options.body.get("action"), "revoke");
    assert.equal(options.body.get("token"), "refresh");
    return new Response(null, { status: 200 });
  } });
  const tokens = await auth.exchange("a".repeat(32));
  try { await auth.revoke(tokens.refreshToken); }
  finally { tokens.accessToken.fill(0); tokens.refreshToken.fill(0); }
  assert.equal(calls, 2);
});

test("inactive and malformed subjects cannot become proof principals", async () => {
  for (const value of [{ user_id: "owner", is_active: false },
    { user_id: " padded ", is_active: true }, { user_id: "x".repeat(65), is_active: true }]) {
    const auth = new QualifiedHaAuth({ ...config("echo"), fetchImpl: async () => json(value) });
    await assert.rejects(auth.verify(Buffer.from("fixture")), /^Error: ha_subject_verification_failed$/);
  }
});

test("qualified refresh fixes issuer endpoint and clears the submitted credential", async () => {
  let submitted;
  const auth = new QualifiedHaAuth({ ...config("victoria"), fetchImpl: async (url, options) => {
    assert.equal(url, "https://victoria.ha.internal/auth/token");
    assert.equal(options.redirect, "error");
    assert.equal(options.body.get("grant_type"), "refresh_token");
    assert.equal(options.body.get("client_id"), config("victoria").clientId);
    assert.equal(options.body.get("refresh_token"), "original-refresh");
    submitted = options.body;
    return json({ access_token: "new-access", refresh_token: "rotated-refresh", expires_in: 1800 });
  } });
  const secret = Buffer.from("original-refresh");
  const tokens = await auth.refresh(secret);
  try {
    assert.equal(tokens.accessToken.toString(), "new-access");
    assert.equal(tokens.refreshToken.toString(), "rotated-refresh");
    assert.equal(submitted.has("refresh_token"), false);
  } finally { tokens.accessToken.fill(0); tokens.refreshToken.fill(0); secret.fill(0); }
});

test("qualified refresh failure does not reflect credentials or endpoint details", async () => {
  const unreachable = new QualifiedHaAuth({ ...config("victoria"), fetchImpl: async () => { throw new Error("private credential details"); } });
  await assert.rejects(unreachable.refresh(Buffer.from("fixture")), /^HaUnavailableError: home_assistant_unavailable$/);
  await assert.rejects(unreachable.verify(Buffer.from("fixture")), /^HaUnavailableError: home_assistant_unavailable$/);
  const denied = new QualifiedHaAuth({ ...config("victoria"), fetchImpl: async () => new Response("private credential details", { status: 400 }) });
  await assert.rejects(denied.refresh(Buffer.from("fixture")), /^Error: ha_token_refresh_failed$/);
  const restarting = new QualifiedHaAuth({ ...config("victoria"), fetchImpl: async () => new Response("private", { status: 503 }) });
  await assert.rejects(restarting.refresh(Buffer.from("fixture")), /^HaUnavailableError: home_assistant_unavailable$/);
});
