#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const repo = path.resolve(__dirname, "..");
const read = (name) => fs.readFileSync(path.join(repo, name), "utf8");
const main = read("app/src-tauri/src/main.rs");
const nativeAuth = read("app/src-tauri/src/native_auth.rs");
const nativeAttestation = read("app/src-tauri/src/native_attestation.rs");
const credentials = read("app/src-tauri/src/windows_credentials.rs");
const cargo = read("app/src-tauri/Cargo.toml");
const tauri = JSON.parse(read("app/src-tauri/tauri.conf.json"));
const mainCapability = read("app/src-tauri/capabilities/default.json");
const agentCapability = JSON.parse(read("app/src-tauri/capabilities/agent.json"));
const agentHtml = read("app/src/home-agent/index.html");
const agentPanel = read("app/src/home-agent/panel.jsx");
const agentCompiledPanel = read("app/src/home-agent/panel.js");
const app = read("app/src/home-app.jsx");
let passes = 0;
let fails = 0;

function assert(name, condition, detail) {
  if (condition) {
    passes += 1;
    process.stdout.write(`  PASS  ${name}\n`);
    return;
  }
  fails += 1;
  process.stdout.write(`  FAIL  ${name}${detail === undefined ? "" : `\n        ${JSON.stringify(detail)}`}\n`);
}

async function behaviorTest() {
  const calls = [];
  const window = {
    __TAURI__: { core: { invoke: async (command, args) => {
      calls.push({ command, args });
      if (command.startsWith("native_agent_")) return { status: 200, payload: { ok: true } };
      return { configured: true, authenticated: false };
    } } },
    location: { assign: () => { throw new Error("native page navigated instead of invoking Rust"); } },
    crypto: { randomUUID: () => "00000000-0000-4000-8000-000000000000" },
  };
  const context = vm.createContext({
    window,
    globalThis: window,
    fetch: () => { throw new Error("native API attempted webview fetch"); },
    URL,
    Error,
    String,
    Number,
    Boolean,
    Promise,
  });
  vm.runInContext(read("app/src/home-agent/api.js"), context, { filename: "api.js" });
  const api = new window.HomeAgentApi();
  await api.session();
  await api.login();
  await api.snapshot();
  await api.explainDescriptor("01900000-0000-7000-8000-000000000002");
  await api.queryParentPresence("01900000-0000-7000-8000-000000000002");
  await api.setPreference("location_memory", true);
  for (const operation of [
    () => api.principalBindingProposal(),
    () => api.requestPrincipalBinding(),
    () => api.cancelPrincipalBindingRequest(),
    () => api.confirmPrincipalBinding("a".repeat(64), "00000000-0000-4000-8000-000000000000"),
  ]) {
    let rejected = false;
    try { await operation(); }
    catch (error) { rejected = error.message === "native_principal_binding_unavailable"; }
    assert("native client rejects browser-only principal binding authority", rejected);
  }
  await api.returnHome();
  assert("native webview uses typed Rust invokes and never fetch", calls.map((item) => item.command).join(",") === [
    "native_auth_status",
    "native_auth_login",
    "native_agent_snapshot",
    "native_agent_explain_descriptor",
    "native_agent_query_parent_presence",
    "native_agent_set_preference",
    "close_agent_window",
  ].join(","), calls);
  const methods = Object.getOwnPropertyNames(window.HomeAgentApi.prototype);
  assert("native client exposes typed place queries but no initiative or generic mutation",
    ["explainDescriptor", "queryParentPresence"].every((name) => methods.includes(name)) &&
    !["initiatives", "claimInitiative"].some((name) => methods.includes(name)) &&
    !methods.some((name) => /(createPlace|confirmParent|generic)/i.test(name)), methods);
}

async function browserBindingBehaviorTest() {
  const calls = [];
  const responses = new Map([
    ["/api/agent/auth/session", { authenticated: true, user_id: "opaque-subject", csrf_token: "csrf" }],
    ["/api/agent/v1/principal-binding-proposal", { state: "not_requested" }],
    ["/api/agent/v1/principal-binding-request", { state: "awaiting_operator_review" }],
    ["/api/agent/v1/principal-binding-request/cancel", { state: "not_requested" }],
    ["/api/agent/v1/principal-binding-proposal/confirm", { state: "bound" }],
  ]);
  const window = {
    location: { assign: () => { throw new Error("binding API attempted navigation"); } },
    crypto: { randomUUID: () => "00000000-0000-4000-8000-000000000000" },
  };
  const context = vm.createContext({
    window,
    globalThis: window,
    fetch: async (url, init) => {
      calls.push({ url, init });
      return { ok: true, status: 200, json: async () => responses.get(url) || {} };
    },
    URL,
    Error,
    String,
    Number,
    Boolean,
    Promise,
  });
  vm.runInContext(read("app/src/home-agent/api.js"), context, { filename: "api-browser.js" });
  const api = new window.HomeAgentApi();
  await api.session();
  await api.principalBindingProposal();
  await api.requestPrincipalBinding();
  await api.cancelPrincipalBindingRequest();
  await api.confirmPrincipalBinding(
    "a".repeat(64),
    "018f6f42-3a8b-4c11-8123-123456789abc",
  );
  assert("browser binding API uses only the four exact semantic routes",
    calls.map(({ url }) => url).join(",") === [
      "/api/agent/auth/session",
      "/api/agent/v1/principal-binding-proposal",
      "/api/agent/v1/principal-binding-request",
      "/api/agent/v1/principal-binding-request/cancel",
      "/api/agent/v1/principal-binding-proposal/confirm",
    ].join(","), calls);
  const writes = calls.slice(2);
  assert("browser binding writes carry CSRF and no client-supplied identity IDs",
    writes.every(({ init }) => init.method === "POST" && init.headers["X-CSRF-Token"] === "csrf") &&
    writes[0].init.body === "{}" &&
    writes[1].init.body === "{}" &&
    JSON.stringify(JSON.parse(writes[2].init.body)) === JSON.stringify({
      proposal_digest: "a".repeat(64),
      confirmation_nonce: "018f6f42-3a8b-4c11-8123-123456789abc",
    }) &&
    !writes.some(({ init }) => /(person_id|ha_user_id|actor_id|principal_id)/.test(init.body)), writes);
}

function principalOperationBoundaryTest() {
  const prelude = agentPanel.slice(0, agentPanel.indexOf("function HomeAgentPanel"));
  const context = vm.createContext({ React: {} });
  vm.runInContext(prelude, context, { filename: "panel-security-prelude.js" });
  const result = vm.runInContext(`(() => {
    const ticket = capturePrincipalOperation("subject-a", 7);
    const material = publicNativeInstallationMaterial({
      installation_id: "01900000-0000-4000-8000-000000000001",
      public_jwk: { kty: "EC", crv: "P-256", x: "x", y: "y", kid: "kid", private_key: "forbidden" },
      access_token: "forbidden",
      nonce: "forbidden",
    }, true);
    return {
      current: principalOperationIsCurrent(ticket, "subject-a", 7),
      staleGeneration: principalOperationIsCurrent(ticket, "subject-a", 8),
      changedSubject: principalOperationIsCurrent(ticket, "subject-b", 7),
      signedOut: principalOperationIsCurrent(ticket, null, 7),
      material: JSON.stringify(material),
      browserMaterial: publicNativeInstallationMaterial({
        installation_id: "01900000-0000-4000-8000-000000000001",
        public_jwk: { kty: "EC", crv: "P-256", x: "x", y: "y", kid: "kid" },
      }, false),
    };
  })()`, context);
  assert("principal-operation tickets expire on reset, logout, and account switch",
    result.current === true &&
    result.staleGeneration === false &&
    result.changedSubject === false &&
    result.signedOut === false, result);
  assert("enrollment card selects only public native material and stays hidden in browsers",
    result.material === JSON.stringify({
      installation_id: "01900000-0000-4000-8000-000000000001",
      public_jwk: { kty: "EC", crv: "P-256", x: "x", y: "y", kid: "kid" },
    }) && result.browserMaterial === null &&
    agentPanel.includes("Public installation enrollment material") &&
    !agentPanel.includes("navigator.clipboard"), result);

  const handlers = [
    "requestPrincipalBinding",
    "cancelPrincipalBindingRequest",
    "confirmPrincipalBinding",
    "propose",
    "confirm",
    "setPreference",
    "previewLifecycle",
    "confirmLifecycle",
    "queryPlace",
  ];
  const guarded = handlers.every((name) => {
    const start = agentPanel.indexOf(`const ${name} = async`);
    const next = agentPanel.indexOf("\n  const ", start + 10);
    const end = next < 0 ? agentPanel.indexOf("\n  return (", start) : next;
    const body = agentPanel.slice(start, end);
    return start >= 0 &&
      body.includes("beginPrincipalOperation()") &&
      body.includes("principalOperationCurrent(ticket)");
  });
  assert("every private async panel handler discards stale principal results", guarded, handlers);

  const signOutStart = agentPanel.indexOf("const signOut = async");
  const signOutEnd = agentPanel.indexOf("\n  return (", signOutStart);
  const signOut = agentPanel.slice(signOutStart, signOutEnd);
  const logoutAwait = signOut.indexOf("await api.logout()");
  assert("sign-out invalidates private authority before network revocation",
    signOutStart >= 0 &&
    signOut.indexOf("refreshGeneration.current += 1") >= 0 &&
    signOut.indexOf("clearPrincipalState()") >= 0 &&
    signOut.indexOf("activeSubject.current = null") >= 0 &&
    signOut.indexOf('setPhase("signed_out")') >= 0 &&
    logoutAwait > signOut.indexOf("clearPrincipalState()") &&
    logoutAwait > signOut.indexOf('setPhase("signed_out")'), signOut);
}

(async function mainTest() {
  process.stdout.write("\nnative_agent_desktop_security_test\n");
  assert("broad Tauri HTTP and default core capabilities are removed", !/^\s*tauri-plugin-http\s*=/m.test(cargo) && !/\.plugin\(tauri_plugin_http/m.test(main) && !mainCapability.includes("http:") && !mainCapability.includes("core:default"));
  assert("refresh tokens use Windows Credential Manager", ["CredWriteW", "CredReadW", "CredDeleteW", "CRED_PERSIST_LOCAL_MACHINE"].every((value) => credentials.includes(value)));
  assert("per-install ES256 key is generated natively and persisted only through Credential Manager",
    cargo.includes('p256 = { version = "0.13", features = ["ecdsa"] }') &&
    nativeAttestation.includes("SigningKey::random(&mut OsRng)") &&
    nativeAttestation.includes("windows_credentials::write") &&
    nativeAttestation.includes("installation-attestation/v1") &&
    nativeAttestation.includes("Zeroizing") &&
    !nativeAttestation.includes("pub fn sign_request_proof"));
  assert("access tokens are kept in zeroizing Rust memory", nativeAuth.includes("Option<Zeroizing<String>>") && nativeAuth.includes("bytes.zeroize()"));
  assert("OAuth uses exact metadata and one-time loopback binding without ignored PKCE parameters",
    !nativeAuth.includes('append_pair("code_challenge"') &&
    !nativeAuth.includes('("code_verifier",') &&
    nativeAuth.includes("metadata_allows_redirect") &&
    nativeAuth.includes("MAX_METADATA_BYTES") &&
    nativeAuth.includes("callback_from_request") &&
    nativeAuth.includes("constant_time_equal"));
  assert("OAuth callback is locked to loopback port 43821", nativeAuth.includes("const LOCKED_CALLBACK_PORT: u16 = 43_821") && nativeAuth.includes('value.host_str() != Some("127.0.0.1")'));
  const logoutImplementation = nativeAuth.slice(nativeAuth.indexOf("pub fn logout"), nativeAuth.indexOf("fn revoke_pending_locked"));
  assert("logout enters durable revocation-pending state before active deletion", logoutImplementation.indexOf("write(&config.pending_credential_target") < logoutImplementation.indexOf("delete(&config.credential_target") && nativeAuth.includes("start_pending_revocation_retry"));
  assert("invalid refresh is a terminal local logout", nativeAuth.includes("StatusCode::BAD_REQUEST | StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN") && nativeAuth.includes('Err("native_authentication_required".to_string())'));
  assert("off-Windows and missing configuration fail closed", nativeAuth.includes('return Self::unconfigured("native_auth_unsupported")') && nativeAuth.includes("NativeConfig::from_env"));
  assert("main legacy window is explicitly denied native authority", main.includes('assert!(!native_authority_window("main"))') && main.includes('label == AGENT_WINDOW_LABEL'));
  const guardCount = (main.match(/require_agent_window\(&window\)\?;/g) || []).length;
  assert("every remaining auth/mutation command is guarded by caller window label", guardCount >= 17, guardCount);
  assert("Tauri creates a separate hidden local Agent window", tauri.app.windows.length === 2 && tauri.app.windows[1].label === "agent" && tauri.app.windows[1].url === "home-agent/index.html" && tauri.app.windows[1].visible === false, tauri.app.windows);
  assert("Agent capability is scoped only to local auth-event observation", agentCapability.windows?.length === 1 && agentCapability.windows[0] === "agent" && JSON.stringify(agentCapability.permissions) === JSON.stringify(["core:event:allow-listen", "core:event:allow-unlisten"]));
  assert("Agent document has a script-strict local CSP", agentHtml.includes("default-src 'none'") && agentHtml.includes("script-src 'self'") && !agentHtml.includes("unsafe-inline") && !agentHtml.includes("unsafe-eval"));
  assert("Agent document has no runtime compiler, inline script, inline style, or navigable Home link", !/(babel|text\/babel|<style|<script[^>]*>\s*[^<])/i.test(agentHtml) && !/style=|href=/i.test(agentPanel));
  assert("Agent document has no parent-origin asset dependency", !agentHtml.includes("../") && !read("app/src/home-agent/api.js").includes("../index.html"));
  const agentSources = [agentHtml, agentPanel, agentCompiledPanel, read("app/src/home-agent/api.js")].join("\n");
  assert("Agent source and generated bundle remain clean UTF-8 without mojibake canaries", agentSources.includes("parents’") && agentSources.includes("Core’s") && agentSources.includes("—") && !/[âÃÂ]/.test(agentSources));
  assert("legacy app opens Agent through Rust instead of navigating its own webview", app.includes('invoke("open_agent_window")') && app.includes("onNativeAuth={openAgentSurface}") && !app.includes('case "agent":\n        window.location.assign'));
  assert("native command surface cannot navigate or evaluate the Agent window", !/fn\s+native_.*(?:navigate|eval)/.test(main) && !/\.navigate\(|eval\(/.test(main));
  assert("Agent close hides the fixed authority window and main close destroys it", main.includes("api.prevent_close()") && main.includes("agent.destroy()"));
  assert("native Rust has no generic network command or privileged excluded mutation", !/\b(?:async\s+)?fn native_agent_request\s*\(/.test(main) && !/(CreatePlace|ConfirmParent)/.test(nativeAuth));
  assert("login refuses to overwrite an active refresh credential", nativeAuth.includes("login_credential_gate") && nativeAuth.includes("native_auth_already_authenticated"));
  const principalReset = agentPanel.slice(
    agentPanel.indexOf("const clearPrincipalState"),
    agentPanel.indexOf("const refresh = async"),
  );
  assert("logout and account changes clear every principal-private panel state",
    [
      "setOnboarding(null)",
      "setBindingProposal(null)",
      "setBindingBusy(false)",
      "setSnapshot(null)",
      "setRelationship(null)",
      "setPresence(null)",
      "setTransaction(null)",
      "setLifecycle(null)",
    ].every((value) => principalReset.includes(value)) &&
    agentPanel.includes("activeSubject.current !== subject") &&
    agentPanel.includes("authorityGeneration.current += 1") &&
    agentPanel.includes("refreshGeneration.current"), principalReset);
  assert("native panel remains contained without explicit canary memory capability",
    agentPanel.includes('nextSnapshot?.capabilities?.persistent_memory !== "enabled"') &&
    agentPanel.includes('setPhase("native_contained")'));
  assert("deployed Agent client has no initiative listing, claim, or presentation path",
    !/\b(?:initiatives|claimInitiative)\s*\(/.test(read("app/src/home-agent/api.js")) &&
    !/(native_agent_(?:list|claim)_initiative|Private travel greeting|Present once)/.test(agentSources));
  assert("browser identity preview is fixed, explicit, and keeps location choices off",
    agentPanel.includes('id="principal-binding-preview">{bindingProposal.confirmation_statement}</p>') &&
    agentPanel.includes("Review code <code>{bindingProposal.review_code}</code>") &&
    agentPanel.includes("Location memory default: off. Travel greetings default: off.") &&
    agentPanel.includes("window.crypto.randomUUID()") &&
    !read("app/src/home-agent/api.js").includes("/api/agent/v1/people") &&
    !read("app/src/home-agent/api.js").includes("/api/agent/v1/principal-bindings"));
  principalOperationBoundaryTest();
  await behaviorTest();
  await browserBindingBehaviorTest();

  process.stdout.write(`\n${passes} pass · ${fails} fail\n`);
  if (fails) process.exit(1);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
