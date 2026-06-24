#!/usr/bin/env node
/* Pure local tests for app/src/simulation.jsx command/session behavior. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "simulation.jsx");
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

function makeLocalStorage(initial) {
  const removed = [];
  const store = new Map(Object.entries(initial || {}).map(([k, v]) => [String(k), String(v)]));
  return {
    getItem(k) { return store.has(String(k)) ? store.get(String(k)) : null; },
    setItem(k, v) { store.set(String(k), String(v)); },
    removeItem(k) { removed.push(String(k)); store.delete(String(k)); },
    _removed: removed,
    _store: store,
  };
}

function makeScenarios() {
  const rows = [
    { id: "healthy", description: "everything healthy" },
    { id: "model-offline", description: "local model unavailable" },
    { id: "room-entry-kitchen", description: "kitchen room entry" },
  ];
  return {
    list: () => rows.slice(),
    get: (id) => rows.find((s) => s.id === id) || null,
    resolveSnapshot: () => ({}),
  };
}

function loadModule(opts) {
  opts = opts || {};
  const localStorage = opts.localStorage || makeLocalStorage({
    "hg-simulation": "1",
    "hg-simulation-scenario": "model-offline",
  });
  const window = {
    __TAURI__: opts.tauri ? {} : undefined,
    localStorage,
    location: {
      search: opts.search || "",
      href: `http://app.local/${opts.search || ""}`,
    },
    history: { replaceState: () => {} },
    SimScenarios: makeScenarios(),
    SimTimeline: { create: () => ({ play: () => {}, cancel: () => {} }) },
  };
  const sandbox = {
    window,
    URLSearchParams,
    URL,
    Math,
    console: { warn: () => {}, log: () => {}, error: () => {} },
  };
  vm.runInNewContext(source, sandbox, { filename: SRC });
  return { window, localStorage };
}

function makeCtx(active, scenario) {
  const calls = [];
  const events = [];
  const sim = {
    active: !!active,
    scenario: scenario || null,
    activate: (id) => calls.push(["activate", id]),
    deactivate: () => calls.push(["deactivate"]),
    reset: () => calls.push(["reset"]),
  };
  return {
    ctx: {
      sim,
      addEvent: (event) => events.push(event),
    },
    calls,
    events,
  };
}

(function main() {
  process.stdout.write("\nsimulation_module_boot_test\n");
  const browser = loadModule({ search: "?simulation=1&scenario=model-offline" });
  assert("legacy sim localStorage keys are removed on load", browser.localStorage._removed.join(",") === "hg-simulation,hg-simulation-scenario", browser.localStorage._removed);
  assert("browser URL parameter activates initial Simulation Mode flag", browser.window.__SIM_ACTIVE === true, browser.window.__SIM_ACTIVE);
  assert("SimulationCommand is exported", browser.window.SimulationCommand && typeof browser.window.SimulationCommand.handle === "function");
  assert("useSimulation hook is exported", typeof browser.window.useSimulation === "function");
  const plain = loadModule({ search: "" });
  assert("plain browser load starts live", plain.window.__SIM_ACTIVE === false, plain.window.__SIM_ACTIVE);
  const tauri = loadModule({ search: "?simulation=1&scenario=model-offline", tauri: true });
  assert("Tauri ignores URL sim activation on boot", tauri.window.__SIM_ACTIVE === false, tauri.window.__SIM_ACTIVE);

  process.stdout.write("\nsimulation_help_format_test\n");
  const Sim = browser.window.SimulationCommand;
  const list = Sim.formatScenarioList();
  assert("scenario list includes ids and descriptions", /\/sim healthy/.test(list) && /model-offline/.test(list) && /local model unavailable/.test(list), list);
  const inactiveHelp = Sim.formatSimHelp(false, null);
  assert("inactive help explains no real services", /no Home Assistant/.test(inactiveHelp) && /\/simulation off/.test(inactiveHelp), inactiveHelp);
  const activeHelp = Sim.formatSimHelp(true, "healthy");
  assert("active help shows current scenario", /\[sim\] ACTIVE/.test(activeHelp) && /healthy/.test(activeHelp), activeHelp);

  process.stdout.write("\nsimulation_command_full_name_test\n");
  let h = makeCtx(false, null);
  assert("/simulation with no arg activates healthy", Sim.handle("simulation", "", h.ctx) === true && h.calls[0][0] === "activate" && h.calls[0][1] === "healthy", { calls: h.calls, events: h.events });
  assert("/simulation activation posts status and list", h.events.length === 2 && /activated/.test(h.events[0].text) && /\/sim healthy/.test(h.events[1].text), h.events);
  h = makeCtx(true, "healthy");
  assert("/simulation with no arg while active shows help", Sim.handle("simulation", "", h.ctx) === true && h.calls.length === 0 && /\[sim\] ACTIVE/.test(h.events[0].text), { calls: h.calls, events: h.events });
  h = makeCtx(true, "healthy");
  assert("/simulation off deactivates", Sim.handle("simulation", "off", h.ctx) === true && h.calls[0][0] === "deactivate" && /off/.test(h.events[0].text), { calls: h.calls, events: h.events });
  h = makeCtx(true, "model-offline");
  assert("/simulation reset resets healthy baseline", Sim.handle("simulation", "reset", h.ctx) === true && h.calls[0][0] === "reset" && /healthy/.test(h.events[0].text), { calls: h.calls, events: h.events });
  h = makeCtx(true, "healthy");
  assert("/simulation help posts help", Sim.handle("simulation", "help", h.ctx) === true && /\/simulation off/.test(h.events[0].text), h.events);
  h = makeCtx(true, "healthy");
  assert("/simulation list posts scenarios", Sim.handle("simulation", "list", h.ctx) === true && /room-entry-kitchen/.test(h.events[0].text), h.events);
  h = makeCtx(false, null);
  assert("/simulation <scenario> activates lowercased scenario", Sim.handle("simulation", "MODEL-OFFLINE", h.ctx) === true && h.calls[0][1] === "model-offline", h.calls);

  process.stdout.write("\nsimulation_command_alias_test\n");
  h = makeCtx(false, null);
  assert("/sim with no arg posts usage", Sim.handle("sim", "", h.ctx) === true && h.calls.length === 0 && /usage: \/sim/.test(h.events[0].text), { calls: h.calls, events: h.events });
  h = makeCtx(true, "healthy");
  assert("/sim off deactivates", Sim.handle("sim", "off", h.ctx) === true && h.calls[0][0] === "deactivate" && /\[sim\] off/.test(h.events[0].text), { calls: h.calls, events: h.events });
  h = makeCtx(true, "healthy");
  assert("/sim reset resets", Sim.handle("sim", "reset", h.ctx) === true && h.calls[0][0] === "reset" && /\[sim\] reset/.test(h.events[0].text), { calls: h.calls, events: h.events });
  h = makeCtx(true, "healthy");
  assert("/sim scenarios posts list", Sim.handle("sim", "scenarios", h.ctx) === true && /model-offline/.test(h.events[0].text), h.events);
  h = makeCtx(false, null);
  assert("/sim <scenario> activates scenario", Sim.handle("sim", "room-entry-kitchen", h.ctx) === true && h.calls[0][0] === "activate" && h.calls[0][1] === "room-entry-kitchen", h.calls);
  h = makeCtx(false, null);
  assert("unrelated command is not handled", Sim.handle("demo", "healthy", h.ctx) === false && h.calls.length === 0 && h.events.length === 0, { calls: h.calls, events: h.events });

  process.stdout.write("\nsimulation_source_contract_test\n");
  assert("persist remains no-op/session-only", /function persist\(_active, _scenario\)\s*{\s*\/\/ No-op/.test(source), "persist body changed");
  assert("deactivate clears URL sim params", source.includes('url.searchParams.delete("simulation")') && source.includes('url.searchParams.delete("scenario")'));
  assert("active effect sets global Simulation Mode flag", source.includes("window.__SIM_ACTIVE = true;") && source.includes("window.__SIM_ACTIVE = !!state.active;"));
  assert("scenario change cancels prior timeline", source.includes("if (_activeTimeline) { _activeTimeline.cancel(); _activeTimeline = null; }"));
  assert("Simulation Mode applies roomContext fixture deltas",
    /case "roomContext":[\s\S]*setters\.setRoomContext/.test(source) &&
    /setRoomContext/.test(appSource));
  assert("Simulation Mode applies AI Stack fixture deltas",
    /case "aiStackOnline":[\s\S]*setters\.setAiStackOnline/.test(source) &&
    /case "aiStackState":[\s\S]*setters\.setAiStackState/.test(source) &&
    /setAiStackOnline, setAiStackState/.test(appSource));
  assert("HomeHeader accepts Simulation controls opener",
    /function HomeHeader\(\{[\s\S]*onOpenSimulationControls/.test(appSource));
  assert("active Simulation Mode header pill is clickable",
    /onClick=\{onOpenSimulationControls\}/.test(appSource) &&
    /title="Simulation Mode - open controls"/.test(appSource));
  assert("HomeApp owns and mounts Simulation controls dialog state",
    appSource.includes("const [simulationControlsOpen, setSimulationControlsOpen] = useState(false);") &&
    appSource.includes("onOpenSimulationControls={() => setSimulationControlsOpen(true)}") &&
    /<SimulationControlsDialog[\s\S]*open=\{simulationControlsOpen\}[\s\S]*sim=\{sim\}/.test(appSource));
  assert("HomeApp declares Simulation hook before sim.active effect dependencies",
    appSource.indexOf("const sim = window.useSimulation({") >= 0 &&
    appSource.indexOf("const sim = window.useSimulation({") <
      appSource.indexOf("}, [connection, sim.active]);"));
  assert("Simulation controls reuse existing scenario APIs",
    /function SimulationControlsDialog/.test(appSource) &&
    /window\.SimScenarios\?\.list\?\.\(\)/.test(appSource) &&
    /sim\.setScenario/.test(appSource) &&
    /sim\.reset\?\.\(\)/.test(appSource) &&
    /sim\.deactivate\?\.\(\)/.test(appSource));
  assert("app disconnects HA websocket when Simulation Mode activates",
    appSource.includes("haClientRef.current?.disconnect?.()") &&
    !appSource.includes("haClientRef.current?.close?.()"));
  assert("app reconnects HA websocket when Simulation Mode deactivates with credentials",
    /exiting simulation[\s\S]*reconnecting[\s\S]*connectTo\(endpoint, token\)/.test(appSource));

  if (fails) {
    console.log("\nFailures:");
    for (const f of failures) console.log("- " + f.name + (f.detail ? ": " + JSON.stringify(f.detail) : ""));
  }
  console.log(`\n${passes} pass . ${fails} fail`);
  process.exit(fails ? 1 : 0);
})();
