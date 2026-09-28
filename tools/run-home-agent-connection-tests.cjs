"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const root = path.resolve(__dirname, "..");
const read = (name) => fs.readFileSync(path.join(root, name), "utf8");
const registrySource = read("app/src/home-connection-registry.js");
const apiSource = read("app/src/home-agent/api.js");
const authority = { version: 1, site_id: "echo", ha_issuer_id: "home-assistant:echo" };
const session = (overrides = {}) => ({ authenticated: true, user_id: "same-subject", csrf_token: "csrf-a", authority, ...overrides });
const response = (value, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => value });
function fixture(fetchImpl = async () => response(session())) {
  const calls = [];
  const window = { location: { origin: "https://agent.test", assign() {} }, crypto: require("node:crypto").webcrypto };
  const context = vm.createContext({ window, URL, AbortController, performance, structuredClone,
    fetch: (url, options) => { calls.push({ url, options }); return fetchImpl(url, options); } });
  vm.runInContext(registrySource, context);
  vm.runInContext(apiSource, context);
  return { api: new window.HomeAgentApi(), window, calls };
}
function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

test("real session contract provisions Echo and semantic traffic stays same-origin", async () => {
  const f = fixture(async (url) => response(url.endsWith("session") ? session() : { enabled: false }));
  assert.equal((await f.api.session()).authority.ha_issuer_id, "home-assistant:echo");
  await f.api.disablePreference("location_memory");
  assert.equal(f.api.registry.snapshot().siteId, "echo");
  assert.equal(f.api.registry.getProvisioned("echo").issuer, "home-assistant:echo");
  assert.equal(f.calls[1].url, "/api/agent/v1/preferences/location_memory");
  assert.equal(f.calls[1].options.headers["X-CSRF-Token"], "csrf-a");
  assert.equal(f.calls[1].options.credentials, "include");
  assert.equal(f.calls[1].options.redirect, "error");
  assert.ok(f.calls.every((call) => call.options.signal instanceof AbortSignal));
  assert.equal(f.calls[1].options.headers["X-Authenticated-Home-Site"], undefined);
});

test("personal memory stays on authenticated web routes and refuses caller authority", async () => {
  const f=fixture(async url=>response(url.endsWith("session") ? session() : {version:1,result:null}));
  await f.api.session();
  await f.api.personalMemory("read");
  assert.equal(f.calls[1].url,"/api/agent/personal-memory/read");
  assert.equal(f.calls[1].options.headers["X-CSRF-Token"],"csrf-a");
  assert.equal(f.calls[1].options.method,"POST");
  await assert.rejects(f.api.personalMemory("read",{principal_id:"caller-owner"}),/invalid_personal_memory/);
  await assert.rejects(f.api.personalMemory("arbitrary-operation",{}),/invalid_personal_memory/);
  assert.equal(f.calls.length,2);
  f.api.invoke=()=>{throw new Error("must not invoke native");};
  await assert.rejects(f.api.personalMemory("read"),/native_personal_memory_unavailable/);
});

test("shared-link methods use exact same-origin browser requests and session CSRF", async () => {
  const f = fixture(async (url) => response(url.endsWith("session") ? session() : { version: 1 }));
  const id = "00000000-0000-0000-0000-000000000001";
  const gesture = "00000000-0000-0000-0000-000000000002";
  await f.api.session();
  await f.api.sharedLinkReview(id);
  await f.api.confirmSharedLink(id, gesture, "a".repeat(64));
  await f.api.sharedLinkOutcome(id);
  assert.deepEqual(f.calls.slice(1).map((c) => c.url), ["review", "confirm", "outcome"].map((v) => `/api/agent/shared-identity/${v}`));
  for (const call of f.calls.slice(1)) {
    assert.equal(call.options.method, "POST");
    assert.equal(call.options.headers["X-CSRF-Token"], "csrf-a");
    assert.equal(call.options.credentials, "include");
    const body = JSON.parse(call.options.body);
    assert.equal(body.ceremony_id, id);
    assert.equal(body.subject, undefined);
    assert.equal(body.session_commitment, undefined);
  }
  assert.deepEqual(JSON.parse(f.calls[2].options.body), { ceremony_id: id, gesture_id: gesture, reviewed_digest: "a".repeat(64) });
});

test("shared-link client rejects caller authority, malformed IDs and native fallback", async () => {
  const f = fixture();
  const id = "00000000-0000-0000-0000-000000000001";
  await assert.rejects(f.api.sharedLinkReview("https://other.test"), /invalid_shared/);
  await assert.rejects(f.api.confirmSharedLink(id, id, "bad"), /invalid_shared/);
  await assert.rejects(f.api.sharedLinkRequest("review", { ceremony_id: id, subject: "guest" }), /invalid_shared/);
  await assert.rejects(f.api.sharedLinkRequest("grants", { ceremony_id: id }), /invalid_shared/);
  let nativeCalls = 0;
  f.api.invoke = () => { nativeCalls++; };
  for (const operation of ["sharedLinkReview", "sharedLinkOutcome"]) {
    await assert.rejects(f.api[operation](id), /native_shared_link_unavailable/);
  }
  await assert.rejects(f.api.confirmSharedLink(id, id, "a".repeat(64)), /native_shared_link_unavailable/);
  assert.equal(nativeCalls, 0);
  assert.equal(f.calls.length, 0);
});

test("shared-link response cannot survive authority invalidation", async () => {
  const pending = deferred();
  const f = fixture((url) => url.endsWith("session") ? response(session()) : pending.promise);
  await f.api.session();
  const request = f.api.sharedLinkReview("00000000-0000-0000-0000-000000000001");
  f.api.invalidateAuthority();
  pending.resolve(response({ accounts: [{ subject: "private" }] }));
  await assert.rejects(request, /stale_agent_response/);
});

test("link setup uses exact browser routes without caller authority or native fallback", async () => {
  const f=fixture(),id="00000000-0000-0000-0000-000000000001";
  await f.api.session();
  await f.api.sharedLinkSetup("start",{});
  await f.api.sharedLinkSetup("handoff",{pairing_id:id,token:"a".repeat(64)});
  await f.api.sharedLinkSetup("auth-submit",{pairing_id:id,handle:"b".repeat(64),input:{username:"owner",password:"fixture"}});
  for(const call of f.calls.slice(1))assert.equal(call.options.headers["X-CSRF-Token"],"csrf-a");
  const count=f.calls.length;
  await assert.rejects(f.api.sharedLinkSetup("prepare-review",{pairing_id:id,subject:"guest"}),/invalid_shared/);
  await assert.rejects(f.api.sharedLinkSetup("https://other.test",{}),/invalid_shared/);
  await assert.rejects(f.api.sharedLinkSetup("handoff",{pairing_id:id,token:"bad"}),/invalid_shared/);
  f.api.invoke=()=>assert.fail("native call forbidden");
  await assert.rejects(f.api.sharedLinkSetup("start",{}),/native_shared/);
  assert.equal(f.calls.length,count);
});

test("unsupported authority cannot install CSRF or authorize semantic requests", async () => {
  for (const invalid of [undefined, {}, { ...authority, version: 2 },
    { ...authority, site_id: "victoria" }, { ...authority, ha_issuer_id: "home-assistant:victoria" }]) {
    const f = fixture(async () => response(session({ authority: invalid })));
    await assert.rejects(f.api.session(), /authority_mismatch/);
    assert.equal(f.api.csrf, "");
    assert.equal(f.api.authority, null);
    await assert.rejects(f.api.snapshot(), /authenticated_session_required/);
    assert.equal(f.calls.length, 1);
  }
});

test("unprovisioned base, site, route or operation cannot cause a fetch or Echo fallback", async () => {
  const f = fixture();
  assert.throws(() => new f.window.HomeAgentApi("https://other.test"), /unprovisioned/);
  assert.throws(() => f.api.registry.select("victoria", "private"), /not provisioned/);
  await assert.rejects(f.api.registry.request("memory"), /not provisioned/);
  for (const target of ["https://other.test/api/agent/auth/session", "//other.test/", "/api/agent/v1/unknown", "/api/agent/auth/session?site=victoria"]) {
    await assert.rejects(f.api.request(target), /unprovisioned/);
  }
  assert.equal(f.calls.length, 0);
});

test("logout aborts a session refresh and late success cannot reinstall credentials", async () => {
  const delayed = deferred();
  const f = fixture(async (url) => url.endsWith("logout") ? response({ ok: true }) : delayed.promise);
  const refreshing = f.api.session();
  const rejected = assert.rejects(refreshing, /stale|aborted/i);
  const signal = f.calls[0].options.signal;
  await f.api.logout();
  assert.equal(signal.aborted, true);
  delayed.resolve(response(session()));
  await rejected;
  assert.equal(f.api.csrf, "");
  assert.equal(f.api.authority, null);
});

test("session lookup is closed while logout delivery is pending", async () => {
  const delayed = deferred();
  const f = fixture(async (url) => url.endsWith("logout") ? delayed.promise : response(session()));
  await f.api.session();
  const loggingOut = f.api.logout();
  await assert.rejects(f.api.session(), /logout_in_progress/);
  assert.equal(f.calls.length, 2);
  assert.equal(f.calls[1].options.headers["X-CSRF-Token"], "csrf-a");
  delayed.resolve(response({ ok: true }));
  await loggingOut;
});

test("uncertain logout stays fenced and explicit retry uses only the original CSRF", async () => {
  let attempts = 0;
  const f = fixture(async (url) => {
    if (url.endsWith("logout") && ++attempts === 1) throw new Error("delivery_unknown");
    return response(url.endsWith("session") ? session() : { ok: true });
  });
  await f.api.session();
  await assert.rejects(f.api.logout(), /delivery_unknown/);
  await assert.rejects(f.api.session(), /logout_in_progress/);
  await assert.rejects(f.api.snapshot(), /authenticated_session_required/);
  assert.equal(f.calls.length, 2);
  assert.equal(f.api.csrf, "");
  await f.api.logout();
  assert.equal(f.calls.length, 3);
  assert.equal(f.calls[2].url, "/api/agent/auth/logout");
  assert.equal(f.calls[2].options.headers["X-CSRF-Token"], "csrf-a");
  assert.equal(f.api.authority, null);
  assert.equal(f.api.logoutCsrf, "");
});

test("concurrent logout calls share one delivery and cannot clear each other's fence", async () => {
  const delayed = deferred();
  const f = fixture(async (url) => url.endsWith("logout") ? delayed.promise : response(session()));
  await f.api.session();
  const first = f.api.logout();
  const second = f.api.logout();
  assert.equal(f.calls.length, 2);
  assert.equal(f.calls[1].options.signal.aborted, false);
  await assert.rejects(f.api.session(), /logout_in_progress/);
  delayed.resolve(response({ ok: true }));
  await Promise.all([first, second]);
  assert.equal(f.api.csrf, "");
  assert.equal(f.api.authority, null);
});

test("revocation cancels private reads and delayed decoding cannot deliver private data", async () => {
  const decoded = deferred();
  const f = fixture(async (url) => {
    if (url.endsWith("snapshot")) return { ok: true, status: 200, json: () => decoded.promise };
    if (url.endsWith("household")) return response({ error: "authentication_revoked" }, 401);
    return response(session());
  });
  await f.api.session();
  const pending = f.api.snapshot();
  const rejected = assert.rejects(pending, /stale|aborted/i);
  await assert.rejects(f.api.household(), /authentication_revoked/);
  assert.equal(f.calls[1].options.signal.aborted, true);
  decoded.resolve({ secret: "must not be delivered" });
  await rejected;
  assert.equal(f.api.csrf, "");
});

test("newer session wins and an older rejected response cannot revoke it", async () => {
  const older = deferred();
  let index = 0;
  const f = fixture(async () => ++index === 1 ? older.promise : response(session({ csrf_token: "new-session" })));
  const previous = f.api.session();
  const rejected = assert.rejects(previous, /stale|aborted/i);
  await f.api.session();
  assert.equal(f.calls[0].options.signal.aborted, true);
  older.resolve(response({ error: "authentication_revoked" }, 401));
  await rejected;
  assert.equal(f.api.csrf, "new-session");
  assert.ok(f.api.authority);
});

test("subject or CSRF replacement invalidates outstanding semantic responses", async () => {
  for (const replacement of [{ user_id: "different-subject" }, { csrf_token: "replacement-csrf" }]) {
    const delayed = deferred();
    let sessions = 0;
    const f = fixture(async (url) => url.endsWith("session")
      ? response(session(++sessions === 1 ? {} : replacement)) : delayed.promise);
    await f.api.session();
    const privateRequest = f.api.snapshot();
    const rejected = assert.rejects(privateRequest, /stale|aborted/i);
    let changed = 0;
    f.api.subscribeAuthority(() => { changed += 1; });
    await f.api.session();
    assert.equal(changed, 1);
    assert.equal(f.calls[1].options.signal.aborted, true);
    delayed.resolve(response({ secret: "old subject" }));
    await rejected;
    assert.equal(f.api.csrf, replacement.csrf_token || "csrf-a");
  }
});

test("same subject from another issuer invalidates prior authority and pending work", async () => {
  const delayed = deferred();
  let sessions = 0;
  const f = fixture(async (url) => url.endsWith("session") ? response(session(++sessions === 1 ? {} : {
    authority: { ...authority, ha_issuer_id: "home-assistant:victoria" },
  })) : delayed.promise);
  await f.api.session();
  const privateRequest = f.api.snapshot();
  const rejected = assert.rejects(privateRequest, /stale|aborted/i);
  await assert.rejects(f.api.session(), /authority_mismatch/);
  delayed.resolve(response({ secret: "echo" }));
  await rejected;
  assert.equal(f.api.authority, null);
});

test("native session and snapshot need no browser registry or webview fetch", async () => {
  const calls = [];
  const window = { __TAURI__: { core: { invoke: async (command) => {
    calls.push(command);
    return command === "native_auth_status" ? { authenticated: true } : { status: 200, payload: {} };
  } } } };
  vm.runInNewContext(apiSource, { window, fetch() { throw new Error("native fetch prohibited"); } });
  const api = new window.HomeAgentApi();
  await api.session();
  await api.snapshot();
  assert.deepEqual(calls, ["native_auth_status", "native_agent_snapshot"]);
});

test("shipped existing panel bundle includes canonical registry before React and panel use", () => {
  const bundle = read("app/src/home-agent/panel.js");
  assert.ok(bundle.includes(registrySource));
  assert.ok(bundle.indexOf(registrySource) < bundle.indexOf("function HomeAgentPanel"));
  assert.ok(read("app/src/home-agent/panel.jsx").includes("currentSession?.authority?.ha_issuer_id"));
});

// Execute the shipped, transformed component with deterministic hooks. Child
// components are represented as elements; the real refresh/effects/API run.
function panelFixture(fetchImpl) {
  const f = fixture(fetchImpl);
  const names = [...read("app/src/home-agent/panel.jsx").split("function HomeAgentPanel() {")[1]
    .matchAll(/const \[(\w+),\s*\w+\] = useState\(/g)].map((match) => match[1]);
  const values = [], refs = [], memos = [], effects = [];
  let stateIndex = 0, refIndex = 0, memoIndex = 0, mounted = false;
  const state = () => Object.fromEntries(names.map((name, index) => [name, values[index]]));
  const React = {
    useState(initial) {
      const index = stateIndex++;
      if (!Object.hasOwn(values, index)) values[index] = initial;
      return [values[index], (value) => { values[index] = typeof value === "function" ? value(values[index]) : value; }];
    },
    useRef(initial) { const index = refIndex++; return refs[index] ||= { current: initial }; },
    useMemo(factory) { const index = memoIndex++; return memos[index] ||= factory(); },
    useEffect(effect) { if (!mounted) effects.push(effect); },
    createElement(type, props, ...children) { return { type, props, children }; },
  };
  const context = vm.createContext({ window: f.window, React });
  const bundle = read("app/src/home-agent/panel.js");
  const start = bundle.indexOf("\nconst {\n  useEffect,");
  assert.ok(start > 0);
  vm.runInContext(bundle.slice(start, bundle.lastIndexOf("ReactDOM.createRoot")), context);
  function render() {
    stateIndex = refIndex = memoIndex = 0;
    return vm.runInContext("HomeAgentPanel()", context);
  }
  render();
  mounted = true;
  effects.forEach((effect) => effect());
  function button(label) {
    function find(node) {
      if (!node || typeof node !== "object") return null;
      if (node.type === "button" && node.children.includes(label)) return node;
      for (const item of (node.children || []).flat(Infinity)) { const found = find(item); if (found) return found; }
      return null;
    }
    const result = find(render());
    assert.ok(result, `button ${label} is rendered`);
    return result.props.onClick;
  }
  return { ...f, state, button };
}
const readySnapshot = (subject) => ({ rollout_mode: "canary", capabilities: { persistent_memory: "enabled" }, principal_id: subject });
const settlePanel = () => new Promise((resolve) => setImmediate(resolve));

test("panel cannot reinstall its pre-revocation snapshot after a household 401", async () => {
  const f = panelFixture(async (url) => {
    if (url.endsWith("session")) return response(session());
    if (url.endsWith("onboarding/status")) return response({ state: "bound" });
    if (url.endsWith("snapshot")) return response(readySnapshot("private-old-principal"));
    if (url.endsWith("household")) return response({ error: "authentication_revoked" }, 401);
    return response({ relationships: [] });
  });
  await settlePanel();
  assert.equal(f.state().phase, "signed_out");
  assert.equal(f.state().session, null);
  assert.equal(f.state().snapshot, null);
  assert.equal(f.state().household, null);
});

test("panel refresh accepts a new verified subject without cancelling its own new state", async () => {
  let subject = "first-user";
  const f = panelFixture(async (url) => {
    if (url.endsWith("session")) return response(session({ user_id: subject, csrf_token: subject + "-csrf" }));
    if (url.endsWith("onboarding/status")) return response({ state: "bound" });
    if (url.endsWith("snapshot")) return response(readySnapshot(subject));
    return response({ people: [], relationships: [] });
  });
  await settlePanel();
  assert.equal(f.state().phase, "ready");
  assert.equal(f.state().snapshot.principal_id, "first-user");
  subject = "second-user";
  await f.button("Refresh")();
  assert.equal(f.state().phase, "ready");
  assert.equal(f.state().session.user_id, "second-user");
  assert.equal(f.state().snapshot.principal_id, "second-user");
});

test("panel offers an explicit retry after uncertain browser logout and refresh stays signed out", async () => {
  const f = panelFixture(async (url) => {
    if (url.endsWith("logout")) throw new Error("delivery_unknown");
    if (url.endsWith("session")) return response(session());
    if (url.endsWith("onboarding/status")) return response({ state: "bound" });
    if (url.endsWith("snapshot")) return response(readySnapshot("owner"));
    return response({ people: [], relationships: [] });
  });
  await settlePanel();
  await f.button("Sign out")();
  assert.equal(f.state().phase, "signed_out");
  assert.ok(f.button("Retry secure sign-out"));
  const count = f.calls.length;
  await f.button("Refresh")();
  assert.equal(f.state().phase, "signed_out");
  assert.equal(f.state().snapshot, null);
  assert.equal(f.calls.length, count);
});

test("explicit panel sign-in recovers from pending logout after the server cleared its cookie", async () => {
  let attempts = 0;
  const f = panelFixture(async (url) => {
    if (url.endsWith("logout")) return ++attempts === 1
      ? response({ error: "logout_revocation_pending", retryable: true }, 503)
      : response({ error: "authentication_required" }, 401);
    if (url.endsWith("auth/start")) return response({ authorize_url: "https://ha.test/auth/authorize?state=fresh" });
    if (url.endsWith("session")) return response(session());
    if (url.endsWith("onboarding/status")) return response({ state: "bound" });
    if (url.endsWith("snapshot")) return response(readySnapshot("owner"));
    return response({ people: [], relationships: [] });
  });
  let navigation;
  f.window.location.assign = (url) => { navigation = url; };
  await settlePanel();
  await f.button("Sign out")();
  await f.button("Retry secure sign-out")();
  assert.equal(f.state().phase, "signed_out");
  const count = f.calls.length;
  await f.button("Refresh")();
  assert.equal(f.calls.length, count);
  assert.equal(f.state().snapshot, null);
  await f.button("Start a new sign-in")();
  assert.equal(f.calls.length, count + 1);
  assert.equal(f.calls.at(-1).url, "/api/agent/auth/start");
  assert.equal(f.calls.at(-1).options.method, "POST");
  assert.equal(f.calls.at(-1).options.body, "{}");
  assert.equal(f.calls.at(-1).options.headers["X-CSRF-Token"], undefined);
  assert.equal(navigation, "https://ha.test/auth/authorize?state=fresh");
  assert.equal(f.state().phase, "authenticating");
  assert.equal(f.state().snapshot, null);
});
