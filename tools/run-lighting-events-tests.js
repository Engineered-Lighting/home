#!/usr/bin/env node
/* ============================================================================
 * tools/run-lighting-events-tests.js
 *
 * Pure local tests for app/src/home-lighting-events.jsx.
 *
 * These cover the UI-side Home Assistant event bridge for Living Lights manual
 * overrides and lighting articulations. The file is browser-loaded in the app,
 * so the test evaluates it in a small window-shaped VM context.
 * ============================================================================ */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "home-lighting-events.jsx");

let passes = 0;
let fails = 0;
const failures = [];

function assert(name, cond, detail) {
  if (cond) {
    passes++;
    process.stdout.write("  \x1b[32mPASS\x1b[0m  " + name + "\n");
  } else {
    fails++;
    failures.push({ name, detail });
    process.stdout.write("  \x1b[31mFAIL\x1b[0m  " + name);
    if (detail !== undefined) {
      const dumped = typeof detail === "string"
        ? detail
        : JSON.stringify(detail, null, 2).replace(/\n/g, "\n        ");
      process.stdout.write("\n        " + dumped);
    }
    process.stdout.write("\n");
  }
}

function loadModule() {
  const warnings = [];
  const sandbox = {
    window: {},
    Date,
    console: {
      warn: (...args) => warnings.push(args.map(String).join(" ")),
    },
  };
  const src = fs.readFileSync(SRC, "utf8");
  vm.runInNewContext(src, sandbox, { filename: SRC });
  return { api: sandbox.window.HomeLightingEvents, warnings };
}

function makeClient(options) {
  const handlers = {};
  const unsubCalls = [];
  const opts = options || {};
  const client = {
    subscribeEvents(type, handler) {
      if (opts.throwOn === type) {
        throw new Error("boom " + type);
      }
      handlers[type] = handler;
      return () => {
        unsubCalls.push(type);
        if (opts.throwOnUnsub === type) {
          throw new Error("unsub boom " + type);
        }
      };
    },
  };
  return { client, handlers, unsubCalls };
}

process.stdout.write("\nlighting_events_shape_test\n");
{
  const { api } = loadModule();
  assert("module attaches HomeLightingEvents to window", !!api);
  assert("subscribe exists", typeof api.subscribe === "function");
  assert("missing client returns undefined", api.subscribe(null, () => {}) === undefined);
  assert(
    "client without subscribeEvents returns undefined",
    api.subscribe({}, () => {}) === undefined,
  );
}

process.stdout.write("\nsubscription_wiring_test\n");
{
  const { api } = loadModule();
  const { client, handlers, unsubCalls } = makeClient();
  const events = [];
  const unsubscribe = api.subscribe(client, (ev) => events.push(ev));
  assert("subscribe returns composed unsubscribe", typeof unsubscribe === "function");
  assert(
    "subscribes to override and articulation event streams",
    Object.keys(handlers).sort().join(",") ===
      "living_lights_articulation,living_lights_override_detected",
    Object.keys(handlers),
  );
  unsubscribe();
  assert(
    "composed unsubscribe tears down both streams",
    unsubCalls.sort().join(",") ===
      "living_lights_articulation,living_lights_override_detected",
    unsubCalls,
  );
  assert("subscribing alone does not emit chat events", events.length === 0, events);
}

process.stdout.write("\nmanual_override_render_test\n");
{
  const { api } = loadModule();
  const { client, handlers } = makeClient();
  const events = [];
  api.subscribe(client, (ev) => events.push(ev));
  handlers.living_lights_override_detected({
    time_fired: "2026-06-23T10:20:30.000Z",
    data: {
      zone: "whole_kitchen",
      actual_pct: 100,
      predicted_pct: 0,
      state: "vacant",
      profile: "night",
      tv_playing: false,
      shadow_mode: false,
    },
  });

  assert("override emits one chat event", events.length === 1, events);
  assert("override emits as system event", events[0].kind === "system", events[0]);
  assert("override preserves HA timestamp", events[0].ts === "2026-06-23T10:20:30.000Z");
  assert(
    "non-debug override text is concise and zone-formatted",
    events[0].text === "Whole kitchen light adjusted manually",
    events[0].text,
  );
  assert(
    "override payload is retained in meta for introspection",
    events[0].meta && events[0].meta.lightingOverride.actual_pct === 100,
    events[0].meta,
  );
}

process.stdout.write("\ndebug_override_render_test\n");
{
  const { api } = loadModule();
  const { client, handlers } = makeClient();
  const events = [];
  api.subscribe(client, (ev) => events.push(ev), { debugMode: () => true });
  handlers.living_lights_override_detected({
    data: {
      zone: "office",
      actual_pct: 85,
      predicted_pct: 30,
      state: "present",
      profile: "evening",
      tv_playing: true,
      shadow_mode: true,
    },
  });

  const text = events[0] && events[0].text;
  assert("debug override emits fallback timestamp", /^\d{4}-\d{2}-\d{2}T/.test(events[0].ts));
  assert("debug override names actual percent", text.includes("Office set to 85% manually"), text);
  assert("debug override names predicted percent", text.includes("system was targeting 30%"), text);
  assert("debug override includes profile", text.includes("evening"), text);
  assert("debug override includes tv context", text.includes("TV on"), text);
  assert("debug override includes classifier state", text.includes("state=present"), text);
  assert("debug override includes shadow context", text.includes("shadow=on"), text);
}

process.stdout.write("\narticulation_render_test\n");
{
  const { api } = loadModule();
  const { client, handlers } = makeClient();
  const events = [];
  api.subscribe(client, (ev) => events.push(ev));
  const longText = "x".repeat(240);
  handlers.living_lights_articulation({
    time_fired: "2026-06-23T11:00:00.000Z",
    data: {
      text: longText,
      zone: "sink",
      context_id: "ctx-123",
    },
  });
  handlers.living_lights_articulation({ data: {} });

  assert("articulation emits assistant event", events[0].kind === "assistant", events[0]);
  assert("articulation truncates long text to 200 chars", events[0].text.length === 200);
  assert(
    "articulation meta carries source, zone, and context id",
    events[0].meta.source === "lighting-articulation" &&
      events[0].meta.zone === "sink" &&
      events[0].meta.context_id === "ctx-123",
    events[0].meta,
  );
  assert("empty articulation defaults to acknowledgement", events[1].text === "Acknowledged.");
}

process.stdout.write("\nerror_isolation_test\n");
{
  const loaded = loadModule();
  const { api, warnings } = loaded;
  const { client, handlers } = makeClient({
    throwOn: "living_lights_override_detected",
    throwOnUnsub: "living_lights_articulation",
  });
  const events = [];
  const unsubscribe = api.subscribe(client, (ev) => events.push(ev));
  assert("override subscription failure is warned", warnings.some((w) => w.includes("overrides")));
  assert(
    "articulation still subscribes when override subscription fails",
    typeof handlers.living_lights_articulation === "function",
  );
  handlers.living_lights_articulation({ data: { text: "Still here." } });
  assert("remaining subscription still emits events", events[0].text === "Still here.");
  assert("unsubscribe swallows child unsubscribe failures", (() => {
    try {
      unsubscribe();
      return true;
    } catch (_) {
      return false;
    }
  })());
}

process.stdout.write("\n");
if (fails === 0) {
  process.stdout.write("\x1b[32m" + passes + " pass . 0 fail\x1b[0m\n");
  process.exit(0);
}

for (const f of failures) {
  process.stdout.write("FAIL " + f.name + "\n");
  if (f.detail !== undefined) {
    process.stdout.write(String(f.detail) + "\n");
  }
}
process.stdout.write("\x1b[31m" + passes + " pass . " + fails + " fail\x1b[0m\n");
process.exit(1);
