#!/usr/bin/env node
/* Pure local tests for app/src/home-external.jsx external-routing privacy/runtime helpers. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "home-external.jsx");
const APP_SRC = path.join(REPO, "app", "src", "home-app.jsx");
const source = fs.readFileSync(SRC, "utf8");
const appSource = fs.readFileSync(APP_SRC, "utf8");

let passes = 0;
let fails = 0;
const failures = [];

function assert(name, cond, detail) {
  if (cond) {
    passes++;
    process.stdout.write("  PASS  " + name + "\n");
  } else {
    fails++;
    failures.push({ name, detail });
    process.stdout.write("  FAIL  " + name);
    if (detail !== undefined) {
      const dumped = typeof detail === "string"
        ? detail
        : JSON.stringify(detail, null, 2);
      process.stdout.write("\n        " + dumped.replace(/\n/g, "\n        "));
    }
    process.stdout.write("\n");
  }
}

function sliceBetween(startNeedle, endNeedle) {
  const start = source.indexOf(startNeedle);
  if (start < 0) throw new Error("missing start marker: " + startNeedle);
  const end = source.indexOf(endNeedle, start);
  if (end < 0) throw new Error("missing end marker after " + startNeedle + ": " + endNeedle);
  return source.slice(start, end);
}

function makeLocalStorage() {
  const store = new Map();
  return {
    getItem(key) { return store.has(key) ? store.get(key) : null; },
    setItem(key, value) { store.set(key, String(value)); },
    removeItem(key) { store.delete(key); },
    clear() { store.clear(); },
    _store: store,
  };
}

function loadModule(fetchImpl) {
  const localStorage = makeLocalStorage();
  const sandboxWindow = { localStorage };
  const script = [
    sliceBetween("const HG_FONT_MONO_EXT", "// ExternalKeyModal"),
    "function ExternalKeyModal() {}",
    sliceBetween("// /test classifier", "// Export everything to window"),
    "Object.assign(window, {",
    "askExternal, classifyIntent, explainIntent, openaiProvider, ExternalKeyModal,",
    "getExternalProvider, isExternalConfigured, formatExternalStatus,",
    "runExternalSuite, runClassifierTest, runPrivacyTest, recordStat",
    "});",
  ].join("\n\n");
  const sandbox = {
    window: sandboxWindow,
    localStorage,
    console,
    Date,
    Promise,
    JSON,
    TextDecoder,
    setTimeout(fn) { fn(); return 1; },
    clearTimeout() {},
    fetch: fetchImpl || (async () => { throw new Error("unexpected fetch"); }),
  };
  vm.runInNewContext(script, sandbox, { filename: "home-external.helpers.js" });
  return sandbox.window;
}

async function expectReject(name, promise, codeOrPattern) {
  try {
    await promise;
    assert(name, false, "expected rejection");
  } catch (err) {
    const ok = typeof codeOrPattern === "string"
      ? err && err.code === codeOrPattern
      : codeOrPattern.test(err && err.message || String(err));
    assert(name, ok, { message: err && err.message, code: err && err.code });
  }
}

(async function main() {
  process.stdout.write("\nexternal_exports_test\n");
  let fetchCalls = 0;
  const api = loadModule(async () => {
    fetchCalls++;
    throw new Error("network should not be used in this test");
  });
  assert("askExternal exported", typeof api.askExternal === "function");
  assert("classifyIntent exported", typeof api.classifyIntent === "function");
  assert("explainIntent exported", typeof api.explainIntent === "function");
  assert("openaiProvider exported", api.openaiProvider && api.openaiProvider.id === "openai", api.openaiProvider);
  assert("ExternalKeyModal export exists", typeof api.ExternalKeyModal === "function");
  assert("getExternalProvider exported", typeof api.getExternalProvider === "function");
  assert("isExternalConfigured exported", typeof api.isExternalConfigured === "function");
  assert("formatExternalStatus exported", typeof api.formatExternalStatus === "function");
  assert("stats buffer initialized", Array.isArray(api.__EXTERNAL_STATS), api.__EXTERNAL_STATS);

  process.stdout.write("\nexternal_classifier_test\n");
  assert("home action routes local", api.classifyIntent("turn off the kitchen lights") === "local");
  assert("home perception query routes local", api.classifyIntent("what do you see in the office?") === "local");
  assert("general knowledge routes external", api.classifyIntent("explain how transformer attention works") === "external");
  assert("unknown greeting stays ambiguous", api.classifyIntent("hello") === "ambiguous");
  assert("empty text stays ambiguous", api.classifyIntent("") === "ambiguous");
  assert("route explanation names HOME_VERBS", /HOME_VERBS/.test(api.explainIntent("dim the lights")), api.explainIntent("dim the lights"));
  assert("route explanation names GENERAL", /GENERAL/.test(api.explainIntent("compare OLED and Mini LED")), api.explainIntent("compare OLED and Mini LED"));
  assert("Lincoln birth question routes external", api.classifyIntent("when was Lincoln born?") === "external");
  assert("Lincoln birth explanation names GENERAL", /GENERAL/.test(api.explainIntent("when was Lincoln born?")), api.explainIntent("when was Lincoln born?"));
  const cls = api.runClassifierTest();
  assert("classifier fixture suite passes", cls.failed === 0 && cls.passed >= 20, cls);

  process.stdout.write("\nexternal_s96_s99_source_contract_test\n");
  assert("default prompt asks for 2-4 sentences", /2-4 sentences/.test(source), "DEFAULT_SYSTEM_PROMPT should pin S96 length");
  assert("default prompt forbids markdown", /no markdown/.test(source) && /no bullets/.test(source) && /no headings/.test(source) && /no code fences/.test(source));
  assert("HomeApp /ask checks active provider configuration", appSource.includes("window.isExternalConfigured") && appSource.includes("dispatchExternal(arg)"));
  assert("HomeApp /external status delegates to formatExternalStatus", appSource.includes("window.formatExternalStatus") && appSource.includes("formatExternalStatus({ provider, configured, enabled, stats })"));
  assert("HomeApp external chat row is tagged external", appSource.includes('kind: "external"') && appSource.includes("streaming: true"));
  assert("HomeApp GENERAL auto-route dispatches external only when enabled and configured",
    appSource.includes('intent === "external" && enabled && configured') && appSource.includes("dispatchExternal(text)"));
  assert("HomeApp external failure messages cover S99 cases",
    ["not_configured", "auth_failed", "rate_limited", "unreachable", "transient", "external request failed"].every((s) => appSource.includes(s)));

  process.stdout.write("\nexternal_sim_privacy_test\n");
  api.__SIM_ACTIVE = true;
  api.__SIM_EXTERNAL_RESPONSE = "alpha beta gamma delta epsilon zeta eta theta";
  const chunks = [];
  let donePayload = null;
  const simResult = await api.askExternal("explain quantum physics", {
    onChunk(evt) { chunks.push(evt.delta); },
    onDone(evt) { donePayload = evt; },
  });
  assert("sim askExternal returns simulated result", simResult && simResult.sim === true, simResult);
  assert("sim askExternal streams chunks", chunks.length >= 2 && chunks.join("").includes("alpha beta"), chunks);
  assert("sim askExternal calls onDone", donePayload && donePayload.sim === true, donePayload);
  assert("sim askExternal never calls fetch", fetchCalls === 0, fetchCalls);
  const body = api.__EXTERNAL_LAST_REQUEST;
  assert("external request has exactly system and user messages", body.messages.length === 2, body);
  assert("external request preserves bare user prompt", body.messages[1].role === "user" && body.messages[1].content === "explain quantum physics", body);
  assert("external request carries plain-text response contract",
    /2-4 sentences/.test(body.messages[0].content) &&
    /no markdown/.test(body.messages[0].content) &&
    /no bullets/.test(body.messages[0].content), body.messages[0].content);
  assert("external request marks sim body", body._sim === true, body);
  const serialized = JSON.stringify(body);
  assert("sim external body has no HA entity ids for general query", !/\b(light|sensor|binary_sensor|media_player)\.[a-z_]+/.test(serialized), serialized);
  assert("sim external body has no LAN IP", !/\b192\.168\./.test(serialized), serialized);
  assert("stats records sim success", api.__EXTERNAL_STATS.some((s) => s.provider === "sim" && s.ok === true), api.__EXTERNAL_STATS);

  process.stdout.write("\nexternal_privacy_runner_test\n");
  const prevStats = api.__EXTERNAL_STATS.length;
  const privacy = await api.runPrivacyTest();
  assert("privacy runner passes", privacy.failed === 0 && privacy.passed === 2, privacy);
  assert("privacy runner restores prior sim flag", api.__SIM_ACTIVE === true);
  assert("privacy runner records simulated stats", api.__EXTERNAL_STATS.length >= prevStats + 2, api.__EXTERNAL_STATS);

  process.stdout.write("\nexternal_error_and_provider_test\n");
  api.__SIM_ACTIVE = false;
  await expectReject(
    "unconfigured real-mode askExternal rejects not_configured",
    api.askExternal("explain relativity", { onError() {} }),
    "not_configured",
  );
  assert("unconfigured failure records stat", api.__EXTERNAL_STATS.some((s) => s.ok === false && s.error === "not_configured"), api.__EXTERNAL_STATS);

  const providerCalls = [];
  api.__EXTERNAL_PROVIDER = {
    id: "mock",
    name: "Mock external",
    isConfigured() { return true; },
    async stream(req, signal, callbacks) {
      providerCalls.push({ req, signal });
      if (callbacks && callbacks.onChunk) callbacks.onChunk({ delta: "pong" });
      if (callbacks && callbacks.onDone) callbacks.onDone({ text: "pong", latencyMs: 7 });
      return { text: "pong", latencyMs: 7, provider: "mock" };
    },
  };
  const providerChunks = [];
  const providerDone = [];
  const providerResult = await api.askExternal("write a tiny haiku", {
    onChunk(evt) { providerChunks.push(evt.delta); },
    onDone(evt) { providerDone.push(evt); },
  });
  assert("configured provider is called once", providerCalls.length === 1, providerCalls);
  assert("configured provider receives bare prompt only", providerCalls[0].req.prompt === "write a tiny haiku" && Object.keys(providerCalls[0].req).length === 1, providerCalls[0].req);
  assert("configured provider result passes through", providerResult.text === "pong" && providerResult.provider === "mock", providerResult);
  assert("configured provider callbacks pass through", providerChunks[0] === "pong" && providerDone[0].text === "pong", { providerChunks, providerDone });
  assert("custom provider success records stat when provider does not", api.__EXTERNAL_STATS.some((s) => s.provider === "mock" && s.ok === true && s.latencyMs === 7), api.__EXTERNAL_STATS);

  process.stdout.write("\nexternal_status_formatter_test\n");
  const statusText = api.formatExternalStatus({
    provider: { id: "mock", name: "Mock external" },
    configured: true,
    enabled: true,
    now: 31000,
    stats: [{ provider: "mock", ok: true, ts: 1000, latencyMs: 42, inputTokens: 12, outputTokens: 34 }],
  });
  assert("status includes provider and routing state", /provider: Mock external/.test(statusText) && /configured: yes/.test(statusText) && /auto-routing: on/.test(statusText), statusText);
  assert("status includes last-call result", /last call: ok/.test(statusText), statusText);
  assert("status includes last-call age", /age 30s/.test(statusText), statusText);
  assert("status includes last-call latency", /latency 42ms/.test(statusText), statusText);
  assert("status includes last-call tokens", /tokens in 12 \/ out 34/.test(statusText), statusText);

  api.__EXTERNAL_PROVIDER = {
    id: "boom",
    isConfigured() { return true; },
    async stream() {
      const err = new Error("rate limit");
      err.code = "rate_limited";
      throw err;
    },
  };
  await expectReject(
    "provider stream failures reject with original code",
    api.askExternal("compare things", {}),
    "rate_limited",
  );
  assert("provider failure records stat", api.__EXTERNAL_STATS.some((s) => s.provider === "boom" && s.error === "rate_limited"), api.__EXTERNAL_STATS);

  process.stdout.write("\nexternal_suite_runner_test\n");
  const suiteApi = loadModule();
  suiteApi.__SIM_ACTIVE = true;
  suiteApi.streamSse = function streamSse() {};
  suiteApi.askExternal = suiteApi.askExternal;
  const printed = [];
  await suiteApi.runExternalSuite((evt) => printed.push(evt));
  assert("external suite emits smoke line", printed.some((e) => /\[smoke\]/.test(e.text || "")), printed);
  assert("external suite emits classifier line", printed.some((e) => /\[classifier\]/.test(e.text || "")), printed);
  assert("external suite emits privacy line", printed.some((e) => /\[privacy\]/.test(e.text || "")), printed);
  assert("external suite skips live in sim mode", printed.some((e) => /sim mode active/.test(e.text || "")), printed);
  assert("external suite summary has zero failures", printed.some((e) => /0 failed/.test(e.text || "") && e.tone === "ok"), printed);

  if (fails) {
    console.log("\nFailures:");
    for (const f of failures) console.log("- " + f.name + (f.detail ? ": " + JSON.stringify(f.detail) : ""));
  }
  console.log(`\n${passes} pass . ${fails} fail`);
  process.exit(fails ? 1 : 0);
})().catch((err) => {
  console.error(err && err.stack || err);
  process.exit(1);
});
