#!/usr/bin/env node
/* Pure local tests for app/src/home-tauri.jsx. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "home-tauri.jsx");
const source = fs.readFileSync(SRC, "utf8");

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

function makeLocalStorage(initial) {
  const store = new Map(Object.entries(initial || {}).map(([k, v]) => [String(k), String(v)]));
  return {
    get length() { return store.size; },
    key(i) { return Array.from(store.keys())[i] || null; },
    getItem(k) { return store.has(String(k)) ? store.get(String(k)) : null; },
    setItem(k, v) { store.set(String(k), String(v)); },
    removeItem(k) { store.delete(String(k)); },
    clear() { store.clear(); },
    _store: store,
  };
}

function loadModule(opts) {
  opts = opts || {};
  const calls = [];
  const warnings = [];
  const localStorage = opts.localStorage || makeLocalStorage();
  const window = {
    __SIM_ACTIVE: !!opts.sim,
    __TAURI__: opts.tauri,
  };
  const sandbox = {
    window,
    localStorage,
    fetch: opts.fetch || (async (url, init) => {
      calls.push({ url, init, via: "browser" });
      return {
        ok: true,
        status: 200,
        async json() { return { ok: true, via: "browser" }; },
        async text() { return "ok"; },
      };
    }),
    console: {
      warn: (...args) => warnings.push(args.join(" ")),
      log: () => {},
      error: () => {},
    },
    URL,
    JSON,
    Set,
  };
  vm.runInNewContext(source, sandbox, { filename: SRC });
  return { window, localStorage, calls, warnings };
}

async function main() {
  process.stdout.write("\ntauri_glue_exports_test\n");
  const base = loadModule();
  const w = base.window;
  assert("IS_TAURI false outside Tauri", w.IS_TAURI === false);
  assert("helpers are assigned to window", typeof w.tauriFetch === "function" && typeof w.loadPrefs === "function" && typeof w.winClose === "function");
  assert("getTauriWindow is null outside Tauri", w.getTauriWindow() === null);

  process.stdout.write("\ntauri_glue_fetch_test\n");
  const browserRes = await w.tauriFetch("http://example.local/api", { method: "POST" });
  assert("browser fetch fallback is used outside Tauri", base.calls.length === 1 && base.calls[0].url === "http://example.local/api", base.calls);
  assert("browser fetch response is returned", browserRes.ok === true && (await browserRes.json()).via === "browser");

  const tauriCalls = [];
  const tauri = loadModule({
    tauri: {
      http: {
        fetch: async (url, init) => {
          tauriCalls.push({ url, init, via: "tauri" });
          return { ok: true, status: 202, json: async () => ({ via: "tauri" }), text: async () => "" };
        },
      },
    },
    fetch: async () => {
      throw new Error("browser fetch should not be used in Tauri");
    },
  });
  const tauriRes = await tauri.window.tauriFetch("http://ha.local/api/states", { headers: { A: "B" } });
  // Security contract: inside Tauri, raw HTTP is refused — native code must
  // use the typed transport. The HTTP plugin must NOT be called.
  assert("Tauri raw HTTP is refused (typed transport required)",
    tauriCalls.length === 0 && tauriRes.ok === false && tauriRes.status === 0 &&
    tauriRes.statusText === "native-typed-transport-required", tauriCalls);
  assert("Tauri refusal response is inert JSON",
    JSON.stringify(await tauriRes.json()) === "{}" && (await tauriRes.text()) === "");

  const sim = loadModule({
    sim: true,
    tauri: { http: { fetch: async () => { throw new Error("tauri fetch leaked"); } } },
    fetch: async () => { throw new Error("browser fetch leaked"); },
  });
  const blocked1 = await sim.window.tauriFetch("http://ha.local/api/a");
  const blocked2 = await sim.window.tauriFetch("http://ha.local/api/b");
  const blocked3 = await sim.window.tauriFetch("http://other.local/api");
  const blockedRelative = await sim.window.tauriFetch("/relative/path");
  assert("Simulation Mode returns blocked response", blocked1.ok === false && blocked1.status === 0 && blocked1.statusText === "blocked-by-simulation-mode", blocked1);
  assert("Simulation Mode never calls real fetch", sim.calls.length === 0, sim.calls);
  assert("Simulation Mode warns once per absolute host", sim.warnings.length === 2 && /ha\.local/.test(sim.warnings[0]) && /other\.local/.test(sim.warnings[1]), sim.warnings);
  assert("Simulation Mode also blocks relative URLs", blockedRelative.ok === false && blockedRelative.status === 0);

  process.stdout.write("\ntauri_glue_prefs_and_events_test\n");
  const prefs = loadModule();
  assert("loadPrefs returns defaults when empty", JSON.stringify(prefs.window.loadPrefs({ theme: "dark" })) === JSON.stringify({ theme: "dark" }));
  prefs.window.savePrefs({ theme: "light", sidecar: "http://x" });
  assert("savePrefs stores JSON", JSON.parse(prefs.localStorage.getItem("hg-prefs")).theme === "light", prefs.localStorage.getItem("hg-prefs"));
  assert("loadPrefs merges stored prefs over defaults", prefs.window.loadPrefs({ theme: "dark", model: "qwen" }).theme === "light" && prefs.window.loadPrefs({ model: "qwen" }).model === "qwen");
  prefs.localStorage.setItem("hg-prefs", "{bad json");
  assert("loadPrefs falls back on corrupt JSON", prefs.window.loadPrefs({ ok: 1 }).ok === 1);

  // Privacy contract (governed-agent work): conversation/event content in the
  // Tauri glue is session-only — saveEvents/saveConversationId are no-ops and
  // the loaders return empty defaults. (The browser web runtime keeps its own
  // localStorage persistence — see home-web-runtime.js.)
  const manyEvents = Array.from({ length: 205 }, (_, i) => ({ id: i, streaming: i % 2 === 0 }));
  prefs.window.saveEvents(manyEvents);
  assert("saveEvents is session-only (writes nothing)",
    prefs.localStorage.getItem("hg-events") === null);
  assert("loadEvents returns an empty session default",
    Array.isArray(prefs.window.loadEvents()) && prefs.window.loadEvents().length === 0);

  process.stdout.write("\ntauri_glue_conversation_and_onboarding_test\n");
  assert("loadConversationId defaults null", prefs.window.loadConversationId() === null);
  prefs.window.saveConversationId("conv-1");
  assert("saveConversationId is session-only (id not persisted)",
    prefs.window.loadConversationId() === null);
  assert("loadOnboarding returns defaults when empty", prefs.window.loadOnboarding({ seen: false }).seen === false);
  prefs.window.saveOnboarding({ seen: true, step: "voice" });
  prefs.window.saveOnboarding({ step: "lights" });
  const onboarding = prefs.window.loadOnboarding({ theme: "dark" });
  assert("saveOnboarding merge-patches existing flags", onboarding.seen === true && onboarding.step === "lights" && onboarding.theme === "dark", onboarding);
  prefs.localStorage.setItem("hg-onboarding", "{broken");
  assert("loadOnboarding falls back on corrupt JSON", prefs.window.loadOnboarding({ recovered: true }).recovered === true);

  process.stdout.write("\ntauri_glue_window_controls_test\n");
  await w.winClose();
  await w.winMinimize();
  await w.winMaximize();
  assert("window controls no-op outside Tauri", true);

  const controlCalls = [];
  const curWindow = {
    close: async () => controlCalls.push("close"),
    minimize: async () => controlCalls.push("minimize"),
    isMaximized: async () => false,
    maximize: async () => controlCalls.push("maximize"),
    unmaximize: async () => controlCalls.push("unmaximize"),
  };
  const controls = loadModule({
    tauri: {
      window: {
        getCurrentWindow: () => curWindow,
      },
    },
  });
  assert("getTauriWindow uses getCurrentWindow", controls.window.getTauriWindow() === curWindow);
  await controls.window.winClose();
  await controls.window.winMinimize();
  await controls.window.winMaximize();
  assert("window controls call Tauri methods", controlCalls.join(",") === "close,minimize,maximize", controlCalls);

  const toggleCalls = [];
  const maxedWindow = {
    isMaximized: async () => true,
    maximize: async () => toggleCalls.push("maximize"),
    unmaximize: async () => toggleCalls.push("unmaximize"),
  };
  const toggled = loadModule({ tauri: { webviewWindow: { getCurrent: () => maxedWindow } } });
  assert("getTauriWindow falls back to webviewWindow.getCurrent", toggled.window.getTauriWindow() === maxedWindow);
  await toggled.window.winMaximize();
  assert("winMaximize unmaximizes when already maximized", toggleCalls.join(",") === "unmaximize", toggleCalls);

  const appWindow = { marker: true };
  const legacy = loadModule({ tauri: { window: { appWindow } } });
  assert("getTauriWindow falls back to appWindow", legacy.window.getTauriWindow() === appWindow);

  if (fails) {
    console.log("\nFailures:");
    for (const f of failures) console.log("- " + f.name + (f.detail ? ": " + JSON.stringify(f.detail) : ""));
  }
  console.log(`\n${passes} pass . ${fails} fail`);
  process.exit(fails ? 1 : 0);
}

main().catch((err) => {
  console.error(err && err.stack || err);
  process.exit(1);
});
