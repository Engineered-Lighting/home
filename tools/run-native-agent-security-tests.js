#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const repo = path.resolve(__dirname, "..");
const read = (name) => fs.readFileSync(path.join(repo, name), "utf8");
const main = read("app/src-tauri/src/main.rs");
const nativeAuth = read("app/src-tauri/src/native_auth.rs");
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
  await api.initiatives();
  await api.claimInitiative("01900000-0000-7000-8000-000000000001");
  await api.explainDescriptor("01900000-0000-7000-8000-000000000002");
  await api.queryParentPresence("01900000-0000-7000-8000-000000000002");
  await api.setPreference("location_memory", true);
  await api.returnHome();
  assert("native webview uses typed Rust invokes and never fetch", calls.map((item) => item.command).join(",") === [
    "native_auth_status",
    "native_auth_login",
    "native_agent_snapshot",
    "native_agent_list_initiatives",
    "native_agent_claim_initiative",
    "native_agent_explain_descriptor",
    "native_agent_query_parent_presence",
    "native_agent_set_preference",
    "close_agent_window",
  ].join(","), calls);
  const methods = Object.getOwnPropertyNames(window.HomeAgentApi.prototype);
  assert("native client exposes only typed initiative/place queries and no generic mutation",
    ["initiatives", "claimInitiative", "explainDescriptor", "queryParentPresence"].every((name) => methods.includes(name)) &&
    !methods.some((name) => /(createPlace|confirmParent|generic)/i.test(name)), methods);
}

(async function mainTest() {
  process.stdout.write("\nnative_agent_desktop_security_test\n");
  assert("broad Tauri HTTP and default core capabilities are removed", !/^\s*tauri-plugin-http\s*=/m.test(cargo) && !/\.plugin\(tauri_plugin_http/m.test(main) && !mainCapability.includes("http:") && !mainCapability.includes("core:default"));
  assert("refresh tokens use Windows Credential Manager", ["CredWriteW", "CredReadW", "CredDeleteW", "CRED_PERSIST_LOCAL_MACHINE"].every((value) => credentials.includes(value)));
  assert("access tokens are kept in zeroizing Rust memory", nativeAuth.includes("Option<Zeroizing<String>>") && nativeAuth.includes("bytes.zeroize()"));
  assert("OAuth is PKCE S256 with exact client metadata validation", nativeAuth.includes('append_pair("code_challenge_method", "S256")') && nativeAuth.includes("metadata_allows_redirect") && nativeAuth.includes("MAX_METADATA_BYTES"));
  assert("OAuth callback is locked to loopback port 43821", nativeAuth.includes("const LOCKED_CALLBACK_PORT: u16 = 43_821") && nativeAuth.includes('value.host_str() != Some("127.0.0.1")'));
  const logoutImplementation = nativeAuth.slice(nativeAuth.indexOf("pub fn logout"), nativeAuth.indexOf("fn revoke_pending_locked"));
  assert("logout enters durable revocation-pending state before active deletion", logoutImplementation.indexOf("write(&config.pending_credential_target") < logoutImplementation.indexOf("delete(&config.credential_target") && nativeAuth.includes("start_pending_revocation_retry"));
  assert("invalid refresh is a terminal local logout", nativeAuth.includes("StatusCode::BAD_REQUEST | StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN") && nativeAuth.includes('Err("native_authentication_required".to_string())'));
  assert("off-Windows and missing configuration fail closed", nativeAuth.includes('return Self::unconfigured("native_auth_unsupported")') && nativeAuth.includes("NativeConfig::from_env"));
  assert("main legacy window is explicitly denied native authority", main.includes('assert!(!native_authority_window("main"))') && main.includes('label == AGENT_WINDOW_LABEL'));
  const guardCount = (main.match(/require_agent_window\(&window\)\?;/g) || []).length;
  assert("every auth/mutation command is guarded by caller window label", guardCount >= 19, guardCount);
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
  await behaviorTest();

  process.stdout.write(`\n${passes} pass · ${fails} fail\n`);
  if (fails) process.exit(1);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
