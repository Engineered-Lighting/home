#!/usr/bin/env node
/* Pure local tests for app/src/home-proactive.jsx policy and coordinator flow. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const FakeTimers = require("./test_helpers/fake-timers.js");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "home-proactive.jsx");
const source = fs.readFileSync(SRC, "utf8");
const appSource = fs.readFileSync(path.join(REPO, "app", "src", "home-app.jsx"), "utf8");

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

function loadModule() {
  const start = source.indexOf("(function () {");
  const end = source.indexOf("  // \u2500\u2500 useProactiveCoordinator", start);
  if (start < 0 || end < 0) throw new Error("proactive pure/coordinator block not found");
  const script = source.slice(start, end)
    + "\n  Object.assign(window, { PROACTIVE_CONSTANTS, normalizeProactiveEvent, evaluateProactive, createCoordinator, loadProactivePending, saveProactivePending });"
    + "\n})();";
  const store = new Map();
  const localStorage = {
    getItem(key) { return store.has(key) ? store.get(key) : null; },
    setItem(key, value) { store.set(key, String(value)); },
    removeItem(key) { store.delete(key); },
    clear() { store.clear(); },
  };
  const sandbox = {
    window: { localStorage },
    console,
    Date,
    Promise,
    setTimeout,
    clearTimeout,
  };
  vm.runInNewContext(script, sandbox, { filename: "home-proactive.core.js" });
  return sandbox.window;
}

function midday() {
  return new Date(2026, 5, 23, 12, 0, 0).getTime();
}

function quietNight() {
  return new Date(2026, 5, 23, 23, 30, 0).getTime();
}

function baseCtx(overrides) {
  return Object.assign({
    modes: {},
    media: {},
    ledger: {
      lastAssistantSpokeTs: 0,
      lastProactiveSpeakTs: 0,
      lastWelcomeTs: 0,
      roomCooldowns: {},
    },
    greetedThisSession: false,
  }, overrides || {});
}

function makeHarness(api, opts) {
  opts = opts || {};
  const events = [];
  const states = [];
  const calls = [];
  const haClientRef = {
    current: {
      call(payload) {
        calls.push(payload);
        return Promise.resolve({ ok: true });
      },
    },
  };
  const env = {
    haClientRef,
    addEvent(evt) { events.push(evt); },
    setProactiveState(state) { states.push(state); },
    mediaRef: { current: opts.media || {} },
    debugModeRef: { current: !!opts.debugMode },
    simActiveRef: { current: !!opts.simActive },
    frigateOnlineRef: { current: opts.frigateOnline !== false },
  };
  return {
    coord: api.createCoordinator(env),
    env,
    events,
    states,
    calls,
  };
}

function last(arr) {
  return arr[arr.length - 1];
}

async function main() {
  const clock = FakeTimers.install({ now: midday() });
  try {
    process.stdout.write("\nproactive_exports_test\n");
    const api = loadModule();
    assert("normalizeProactiveEvent exported", typeof api.normalizeProactiveEvent === "function");
    assert("evaluateProactive exported", typeof api.evaluateProactive === "function");
    assert("createCoordinator exported", typeof api.createCoordinator === "function");
    assert("constants exported", api.PROACTIVE_CONSTANTS && api.PROACTIVE_CONSTANTS.confirmHighConf === 0.85, api.PROACTIVE_CONSTANTS);

    process.stdout.write("\nproactive_normalization_test\n");
    const norm = api.normalizeProactiveEvent({
      type: "identity_confirmed",
      displayName: "Marcelo",
      confidence: 0.91,
      timestamp: 1_700_000_000_000,
      test: true,
    }, "sse");
    assert("normalizer preserves type", norm.type === "identity_confirmed", norm);
    assert("normalizer maps displayName", norm.display_name === "Marcelo", norm);
    assert("normalizer converts ms timestamps to seconds", norm.timestamp === 1_700_000_000, norm);
    assert("normalizer tags sse source", norm.source === "sse", norm);
    assert("normalizer preserves test bypass flag", norm.test === true, norm);

    process.stdout.write("\nproactive_pending_persistence_test\n");
    const idleState = { phase: "idle", away: false, reason: null, room: null, person: null, sightingLevel: "none", sinceTs: 0 };
    api.saveProactivePending({
      phase: "speaking_proactive_message",
      room: "kitchen",
      person: "Marcelo",
      sightingLevel: "none",
      sinceTs: midday(),
    });
    // Privacy contract (governed-agent work): person/room/arrival state is
    // session-only — save is a no-op, load returns the default and clears
    // any stored key. This test used to assert persistence across reload.
    const restored = api.loadProactivePending(idleState);
    assert("proactive pending state is session-only (load returns default)",
      restored.phase === "idle" &&
        restored.person === null &&
        api.localStorage.getItem("hg-proactive-pending") === null,
      restored);
    api.saveProactivePending({ phase: "idle", sinceTs: midday() });
    assert("idle proactive state clears persisted pending notification",
      api.localStorage.getItem("hg-proactive-pending") === null);
    api.localStorage.setItem("hg-proactive-pending", JSON.stringify({
      phase: "speaking_proactive_message",
      room: "office",
      sinceTs: midday() - (16 * 60 * 1000),
    }));
    const stale = api.loadProactivePending(idleState);
    assert("stale proactive pending notification is discarded",
      stale.phase === "idle" && api.localStorage.getItem("hg-proactive-pending") === null,
      stale);

    process.stdout.write("\nproactive_policy_test\n");
    assert("malformed event is denied", api.evaluateProactive(null, baseCtx(), midday()).reason === "malformed");
    assert("low confidence is denied", api.evaluateProactive({ type: "room_entered", room: "kitchen", confidence: 0.2 }, baseCtx(), midday()).reason === "low-confidence");
    assert("quiet hours are denied", api.evaluateProactive({ type: "room_entered", room: "kitchen", confidence: 1 }, baseCtx(), quietNight()).reason === "quiet-hours");
    assert("sleep mode is denied", api.evaluateProactive({ type: "room_entered", room: "kitchen", confidence: 1 }, baseCtx({ modes: { sleep: true } }), midday()).reason === "sleep-mode");
    assert("dnd mode is denied", api.evaluateProactive({ type: "room_entered", room: "kitchen", confidence: 1 }, baseCtx({ modes: { dnd: true } }), midday()).reason === "dnd");
    assert("focus suppresses room prompts", api.evaluateProactive({ type: "room_entered", room: "kitchen", confidence: 1 }, baseCtx({ modes: { focus: true } }), midday()).reason === "focus-mode");
    assert("focus still allows welcome checks", api.evaluateProactive({ type: "person_arrived_home", person: "person.marcelo", confidence: 1 }, baseCtx({ modes: { focus: true } }), midday()).allow === true);
    assert("movie mode is denied", api.evaluateProactive({ type: "room_entered", room: "living_room", confidence: 1 }, baseCtx({ modes: { movie: true } }), midday()).reason === "movie-mode");
    assert("room video playback is denied", api.evaluateProactive(
      { type: "room_entered", room: "living_room", confidence: 1 },
      baseCtx({ media: { living_room: { state: "playing", category: "movie", volume_level: 0.1 } } }),
      midday(),
    ).reason === "movie-mode");
    assert("loud room media is denied", api.evaluateProactive(
      { type: "room_entered", room: "office", confidence: 1 },
      baseCtx({ media: { office: { state: "playing", category: "music", volume_level: 0.7 } } }),
      midday(),
    ).reason === "media-playing");
    assert("recent assistant speech is denied", api.evaluateProactive(
      { type: "room_entered", room: "office", confidence: 1 },
      baseCtx({ ledger: { lastAssistantSpokeTs: midday() - 1000, lastProactiveSpeakTs: 0, lastWelcomeTs: 0, roomCooldowns: {} } }),
      midday(),
    ).reason === "assistant-recently-spoke");
    assert("test events bypass time-based cooldowns", api.evaluateProactive(
      { type: "room_entered", room: "office", confidence: 1, test: true },
      baseCtx({
        ledger: {
          lastAssistantSpokeTs: midday() - 1000,
          lastProactiveSpeakTs: midday() - 1000,
          lastWelcomeTs: midday() - 1000,
          roomCooldowns: { office: midday() - 1000 },
        },
        greetedThisSession: true,
      }),
      midday(),
    ).allow === true);
    assert("test events still respect DND", api.evaluateProactive(
      { type: "room_entered", room: "office", confidence: 1, test: true },
      baseCtx({ modes: { dnd: true } }),
      midday(),
    ).reason === "dnd");
    assert("same-room cooldown reason wins over generic recent-speech cooldowns",
      api.evaluateProactive(
        { type: "room_entered", room: "office", confidence: 1 },
        baseCtx({
          ledger: {
            lastAssistantSpokeTs: midday() - 1000,
            lastProactiveSpeakTs: midday() - 1000,
            lastWelcomeTs: 0,
            roomCooldowns: { office: midday() - 1000 },
          },
        }),
        midday(),
      ).reason === "room-cooldown");

    process.stdout.write("\nproactive_arrival_flow_test\n");
    const h = makeHarness(api);
    h.coord.ingestEvent(api.normalizeProactiveEvent({
      type: "person_arrived_home",
      person: "person.marcelo",
      display_name: "Marcelo",
      confidence: 1,
      test: true,
    }, "ha_ws"));
    assert("arrival enters pending confirmation", last(h.states).phase === "arrival_pending_confirmation", h.states);
    h.coord.ingestEvent(api.normalizeProactiveEvent({
      type: "identity_confirmed",
      display_name: "Marcelo",
      confidence: 0.97,
      test: true,
    }, "ha_ws"));
    await Promise.resolve();
    const serviceNames = h.calls.map((c) => c.domain + "." + c.service);
    assert("strong identity fires return-home scene", serviceNames.includes("script.turn_on"), h.calls);
    assert("strong identity starts proactive conversation", serviceNames.includes("assist_satellite.start_conversation"), h.calls);
    assert("return-home feed event is emitted", h.events.some((e) => /Return-home scene applied/.test(e.text || "")), h.events);
    assert("spoken welcome feed event is emitted", h.events.some((e) => /Welcome home, Marcelo/.test(e.text || "")), h.events);
    assert("state transitions to speaking", last(h.states).phase === "speaking_proactive_message", h.states);

    h.coord.observeSatelliteState("responding");
    h.coord.observeSatelliteState("listening");
    assert("responding then listening opens follow-up window", last(h.states).phase === "listening_for_followup", h.states);
    h.coord.observeUserTurn("turn on the kitchen lights");
    assert("follow-up user turn is surfaced", h.events.some((e) => /Follow-up: "turn on the kitchen lights"/.test(e.text || "")), h.events);
    h.coord.observeSatelliteState("processing");
    assert("satellite processing moves coordinator to processing_followup", last(h.states).phase === "processing_followup", h.states);
    h.coord.observeSatelliteState("idle");
    assert("satellite idle returns coordinator to idle", last(h.states).phase === "idle", h.states);

    process.stdout.write("\nproactive_room_flow_test\n");
    const roomHarness = makeHarness(api);
    roomHarness.coord.ingestEvent(api.normalizeProactiveEvent({
      type: "room_entered",
      room: "kitchen",
      confidence: 0.95,
      test: true,
    }, "ha_ws"));
    clock.tick(api.PROACTIVE_CONSTANTS.multiRoomWindowMs);
    await Promise.resolve();
    assert("single room entry emits room prompt", roomHarness.events.some((e) => /Room prompt/.test(e.text || "") && /kitchen/.test(e.text || "")), roomHarness.events);
    assert("single room entry starts conversation", roomHarness.calls.some((c) => c.domain === "assist_satellite" && c.service === "start_conversation"), roomHarness.calls);
    assert("room prompt moves to speaking", last(roomHarness.states).phase === "speaking_proactive_message", roomHarness.states);

    const multiHarness = makeHarness(api);
    multiHarness.coord.ingestEvent(api.normalizeProactiveEvent({ type: "room_entered", room: "kitchen", confidence: 1, test: true }, "ha_ws"));
    multiHarness.coord.ingestEvent(api.normalizeProactiveEvent({ type: "room_entered", room: "office", confidence: 1, test: true }, "ha_ws"));
    clock.tick(api.PROACTIVE_CONSTANTS.multiRoomWindowMs);
    assert("multi-room burst is suppressed", last(multiHarness.states).phase === "suppressed" && last(multiHarness.states).reason === "multi-room", multiHarness.states);
    assert("multi-room burst does not speak", multiHarness.calls.length === 0, multiHarness.calls);

    api.localStorage.removeItem("hg-welcomedAt");
    const cooldownHarness = makeHarness(api);
    cooldownHarness.coord.ingestEvent(api.normalizeProactiveEvent({
      type: "room_entered",
      room: "kitchen",
      confidence: 0.95,
    }, "ha_ws"));
    clock.tick(api.PROACTIVE_CONSTANTS.multiRoomWindowMs);
    await Promise.resolve();
    assert("non-test room entry first prompt speaks", cooldownHarness.calls.length === 1, cooldownHarness.calls);
    cooldownHarness.coord.dismissPending();
    cooldownHarness.coord.ingestEvent(api.normalizeProactiveEvent({
      type: "room_entered",
      room: "kitchen",
      confidence: 0.95,
    }, "ha_ws"));
    clock.tick(api.PROACTIVE_CONSTANTS.multiRoomWindowMs);
    await Promise.resolve();
    assert("same-room re-fire after dismiss is suppressed by room cooldown",
      last(cooldownHarness.states).phase === "suppressed" && last(cooldownHarness.states).reason === "room-cooldown",
      cooldownHarness.states);
    assert("same-room cooldown re-fire does not speak again",
      cooldownHarness.calls.length === 1,
      cooldownHarness.calls);
    assert("same-room cooldown emits diagnostic reason",
      cooldownHarness.events.some((e) => e.kind === "diag" && /room prompt \(kitchen\) suppressed: room-cooldown/.test(e.text || "")),
      cooldownHarness.events);

    api.localStorage.removeItem("hg-welcomedAt");
    const dedupeHarness = makeHarness(api);
    dedupeHarness.coord.ingestEvent(api.normalizeProactiveEvent({ type: "room_entered", room: "office", confidence: 1 }, "ha_ws"));
    dedupeHarness.coord.ingestEvent(api.normalizeProactiveEvent({ type: "room_entered", room: "office", confidence: 1 }, "sse"));
    clock.tick(api.PROACTIVE_CONSTANTS.multiRoomWindowMs);
    await Promise.resolve();
    assert("cross-transport duplicate is still deduped",
      dedupeHarness.events.some((e) => e.kind === "diag" && /dedup: dropped room_entered \(sse\)/.test(e.text || "")),
      dedupeHarness.events);
    assert("cross-transport duplicate does not double-speak",
      dedupeHarness.calls.length === 1,
      dedupeHarness.calls);

    process.stdout.write("\nproactive_safety_flow_test\n");
    const simHarness = makeHarness(api, { simActive: true });
    simHarness.coord.ingestEvent(api.normalizeProactiveEvent({ type: "person_left_home", person: "person.marcelo" }, "ha_ws"));
    assert("simulation mode keeps coordinator inert", simHarness.events.length === 0 && simHarness.states.length === 0 && simHarness.calls.length === 0, { events: simHarness.events, states: simHarness.states, calls: simHarness.calls });

    const awayHarness = makeHarness(api);
    awayHarness.coord.ingestEvent(api.normalizeProactiveEvent({ type: "person_left_home", person: "person.marcelo" }, "ha_ws"));
    assert("left-home event marks away", last(awayHarness.states).away === true, awayHarness.states);
    assert("left-home event emits away feed", awayHarness.events.some((e) => /Away mode/.test(e.text || "")), awayHarness.events);

    const weakHarness = makeHarness(api);
    weakHarness.coord.ingestEvent(api.normalizeProactiveEvent({
      type: "person_arrived_home",
      person: "person.marcelo",
      display_name: "Marcelo",
      test: true,
    }, "ha_ws"));
    weakHarness.coord.ingestEvent(api.normalizeProactiveEvent({
      type: "identity_confirmed",
      display_name: "Marcelo",
      confidence: 0.7,
      test: true,
    }, "ha_ws"));
    assert("weak identity keeps arrival pending", last(weakHarness.states).phase === "arrival_pending_confirmation", weakHarness.states);
    assert("weak identity records weak sighting", last(weakHarness.states).sightingLevel === "weak-identity", weakHarness.states);

    process.stdout.write("\nproactive_status_action_flow_test\n");
    const ackHarness = makeHarness(api);
    ackHarness.coord.ingestEvent(api.normalizeProactiveEvent({
      type: "person_arrived_home",
      person: "person.marcelo",
      display_name: "Marcelo",
      confidence: 1,
      test: true,
    }, "ha_ws"));
    assert("ack test starts from pending arrival", last(ackHarness.states).phase === "arrival_pending_confirmation", ackHarness.states);
    ackHarness.coord.acknowledgePending();
    assert("acknowledge action clears pending proactive state", last(ackHarness.states).phase === "idle", ackHarness.states);
    assert("acknowledge action emits proactive diag line", ackHarness.events.some((e) => e.kind === "diag" && /status row acknowledged/.test(e.text || "")), ackHarness.events);

    const dismissHarness = makeHarness(api);
    dismissHarness.coord.ingestEvent(api.normalizeProactiveEvent({
      type: "person_arrived_home",
      person: "person.marcelo",
      display_name: "Marcelo",
      confidence: 1,
      test: true,
    }, "ha_ws"));
    dismissHarness.coord.dismissPending();
    assert("dismiss action clears pending proactive state", last(dismissHarness.states).phase === "idle", dismissHarness.states);
    assert("dismiss action emits proactive diag line", dismissHarness.events.some((e) => e.kind === "diag" && /status row dismissed/.test(e.text || "")), dismissHarness.events);

    process.stdout.write("\nproactive_status_line_source_contract_test\n");
    assert("ProactiveStatusLine accepts action callback and owns expansion state",
      source.includes("function ProactiveStatusLine({ proactive, onAction })") &&
      source.includes("const [expanded, setExpanded] = React.useState(false);"));
    assert("ProactiveStatusLine has one-shot action guard",
      source.includes("const actionFiredRef = React.useRef(false);") &&
      source.includes("if (!actionable || actionFiredRef.current) return;") &&
      source.includes("actionFiredRef.current = true;"));
    assert("status row click toggles inline actions only when actionable",
      source.includes('onClick={() => { if (actionable) setExpanded((x) => !x); }}') &&
      source.includes('aria-expanded={expanded}'));
    assert("inline proactive actions call acknowledge and dismiss verbs",
      source.includes('runAction("acknowledge", ev)') &&
      source.includes('runAction("dismiss", ev)') &&
      source.includes(">acknowledge</button>") &&
      source.includes(">dismiss</button>"));
    assert("HomeApp mounts ProactiveStatusLine in the AI tab",
      appSource.includes("window.ProactiveStatusLine") &&
      appSource.includes("<window.ProactiveStatusLine proactive={proactive} onAction={onProactiveAction} />"));
    assert("HomeApp routes proactive status actions into coordinator methods",
      appSource.includes("const handleProactiveStatusAction = useCallback((action) => {") &&
      appSource.includes("proactiveCoord.acknowledgePending();") &&
      appSource.includes("proactiveCoord.dismissPending();") &&
      appSource.includes("onProactiveAction={handleProactiveStatusAction}"));
    assert("S95 pending proactive state helpers are exported",
      source.includes("const PROACTIVE_PENDING_KEY = \"hg-proactive-pending\"") &&
      source.includes("function loadProactivePending(defaultState)") &&
      source.includes("function saveProactivePending(state)") &&
      source.includes("loadProactivePending,") &&
      source.includes("saveProactivePending,"));
    assert("HomeApp restores pending proactive state on boot",
      appSource.includes("typeof window.loadProactivePending === \"function\"") &&
      appSource.includes("window.loadProactivePending(proactiveIdleState)"));
    assert("HomeApp persists proactive state outside Simulation Mode only",
      appSource.includes("if (sim.active) return;") &&
      appSource.includes("typeof window.saveProactivePending === \"function\"") &&
      appSource.includes("window.saveProactivePending(proactive);") &&
      appSource.includes("[sim.active, proactive]"));

    if (fails) {
      console.log("\nFailures:");
      for (const f of failures) console.log("- " + f.name + (f.detail ? ": " + JSON.stringify(f.detail) : ""));
    }
    console.log(`\n${passes} pass . ${fails} fail`);
    process.exit(fails ? 1 : 0);
  } finally {
    FakeTimers.uninstall(clock);
  }
}

main().catch((err) => {
  console.error(err && err.stack || err);
  process.exit(1);
});
