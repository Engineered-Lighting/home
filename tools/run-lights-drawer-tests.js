#!/usr/bin/env node
/* Pure local tests for the /lights drawer service-call helpers and wiring. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "home-lights.jsx");
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

function loadHaCallService() {
  const start = source.indexOf("function haCallService");
  const endMarker = "window.haCallService = haCallService;";
  const end = source.indexOf(endMarker, start);
  if (start < 0 || end < 0) throw new Error("haCallService block not found");
  const snippet = source.slice(start, end + endMarker.length);
  const sandbox = { window: {} };
  vm.runInNewContext(snippet, sandbox, { filename: "home-lights.haCallService.js" });
  return sandbox.window.haCallService;
}

function loadComputeCTSliderEffective() {
  const start = source.indexOf("function computeCTSliderEffective");
  const endMarker = "window.computeCTSliderEffective = computeCTSliderEffective;";
  const end = source.indexOf(endMarker, start);
  if (start < 0 || end < 0) throw new Error("computeCTSliderEffective block not found");
  const snippet = source.slice(start, end + endMarker.length);
  const sandbox = { window: {}, Number };
  vm.runInNewContext(snippet, sandbox, { filename: "home-lights.computeCTSliderEffective.js" });
  return sandbox.window.computeCTSliderEffective;
}

function sliderUsages() {
  return source.match(/<Slider\b[\s\S]*?\/>/g) || [];
}

async function expectReject(name, promise, pattern) {
  try {
    await promise;
    assert(name, false, "expected rejection");
  } catch (err) {
    assert(name, pattern.test(err && err.message || String(err)), err && err.message);
  }
}

(async function main() {
  process.stdout.write("\nlights_drawer_export_test\n");
  assert("HomeLightsDrawer exported", source.includes("window.HomeLightsDrawer = HomeLightsDrawer;"));
  assert("haCallService exported", source.includes("window.haCallService = haCallService;"));
  assert("drawer accepts connection prop", /function HomeLightsDrawer\(\{[^}]*connection = null/.test(source));
  assert("app passes connection into HomeLightsDrawer", fs.readFileSync(path.join(REPO, "app", "src", "home-app.jsx"), "utf8").includes("connection={connection}"));

  process.stdout.write("\nct_slider_effective_test\n");
  const computeCTSliderEffective = loadComputeCTSliderEffective();
  assert("extracted computeCTSliderEffective is callable", typeof computeCTSliderEffective === "function");
  assert("inherit uses ToD 2700K", computeCTSliderEffective(0, 2700, 1800) === 2700);
  assert("inherit uses morning ToD 2200K", computeCTSliderEffective(0, 2200, 1800) === 2200);
  assert("inherit uses afternoon ToD 4000K", computeCTSliderEffective(0, 4000, 1800) === 4000);
  assert("explicit override wins over ToD", computeCTSliderEffective(3500, 2700, 1800) === 3500);
  assert("lower explicit override wins over ToD", computeCTSliderEffective(2000, 4000, 1800) === 2000);
  assert("undefined ToD falls back above slider minimum", computeCTSliderEffective(0, undefined, 1800) === 2700);
  assert("null ToD falls back above slider minimum", computeCTSliderEffective(0, null, 1800) === 2700);
  assert("zero ToD falls back above slider minimum", computeCTSliderEffective(0, 0, 1800) === 2700);
  assert("NaN ToD falls back above slider minimum", computeCTSliderEffective(0, NaN, 1800) === 2700);
  assert("string ToD values are accepted", computeCTSliderEffective(0, "2700", 1800) === 2700);
  assert("below-min ToD falls back to safe CT", computeCTSliderEffective(0, 1500, 1800) === 2700);
  assert("missing min still accepts valid ToD", computeCTSliderEffective(0, 2700, undefined) === 2700);
  assert("negative override is ignored", computeCTSliderEffective(-50, 2700, 1800) === 2700);
  assert("NaN override is ignored", computeCTSliderEffective(NaN, 2700, 1800) === 2700);
  assert("string override values are accepted", computeCTSliderEffective("3500", "2700", 1800) === 3500);

  process.stdout.write("\nha_call_service_helper_test\n");
  const haCallService = loadHaCallService();
  assert("extracted haCallService is callable", typeof haCallService === "function");

  const preferredCalls = [];
  await haCallService({
    callService(domain, service, data) {
      preferredCalls.push({ domain, service, data });
      return Promise.resolve("ok");
    },
  }, "input_boolean", "turn_off", { entity_id: "input_boolean.homeai_sleep" });
  assert("haCallService prefers client.callService", preferredCalls.length === 1, preferredCalls);
  assert("haCallService preserves preferred domain/service", preferredCalls[0].domain === "input_boolean" && preferredCalls[0].service === "turn_off", preferredCalls[0]);
  assert("haCallService preserves preferred data", preferredCalls[0].data.entity_id === "input_boolean.homeai_sleep", preferredCalls[0]);

  const rawCalls = [];
  await haCallService({
    call(payload) {
      rawCalls.push(payload);
      return Promise.resolve("ok");
    },
  }, "input_number", "set_value", {
    entity_id: ["input_number.a", "input_number.b"],
    value: 42,
    ignored: null,
  });
  assert("raw fallback sends one call", rawCalls.length === 1, rawCalls);
  assert("raw fallback uses call_service", rawCalls[0].type === "call_service", rawCalls[0]);
  assert("raw fallback moves entity_id to target", Array.isArray(rawCalls[0].target.entity_id), rawCalls[0]);
  assert("raw fallback keeps value in service_data", rawCalls[0].service_data.value === 42, rawCalls[0]);
  assert("raw fallback omits null service data", !("ignored" in rawCalls[0].service_data), rawCalls[0]);

  await expectReject(
    "helper rejects missing client clearly",
    haCallService(null, "input_boolean", "turn_on", {}),
    /client is not ready/,
  );
  await expectReject(
    "helper rejects clients that cannot call services clearly",
    haCallService({}, "input_boolean", "turn_on", {}),
    /cannot call services/,
  );
  await expectReject(
    "helper rejects disconnected HAClient-shaped clients clearly",
    haCallService({ ws: null, callService() { return Promise.resolve("wrong"); } }, "input_boolean", "turn_on", {}),
    /websocket is offline/,
  );

  process.stdout.write("\nlights_drawer_sleep_wiring_test\n");
  assert("homeai_sleep is subscribed directly", source.includes('"input_boolean.homeai_sleep"'));
  assert("direct entity subscription participates in owned test", source.includes("ownedEntityIds.has(eid) || ownedPrefixes.some"));
  assert("night-safe toggle still targets homeai_sleep", source.includes('onToggle={() => toggleBool("input_boolean.homeai_sleep")}'));
  assert("night-safe toggle disabled while HA offline", source.includes("disabled={!haOnline}") && source.includes("Home Assistant is reconnecting; light controls are disabled"));
  assert("write helpers guard offline state", source.includes("if (!haOnline) { setError(offlineWriteMessage); return; }"));
  const sliders = sliderUsages();
  assert("all rendered drawer sliders are found", sliders.length === 19, sliders.length);
  assert("all rendered drawer sliders disable while HA is offline",
    sliders.every((block) => block.includes("disabled={!haOnline}")),
    sliders.filter((block) => !block.includes("disabled={!haOnline}")).map((block) => block.slice(0, 120)));
  assert("scope buttons disable while HA is offline",
    source.includes('<button disabled={!haOnline} onClick={() => setText("input_text.living_lights_bias_zone_scope", "all")}') &&
    source.includes('<button disabled={!haOnline} onClick={() => setText("input_text.living_lights_bias_zone_scope", zone)}'));
  assert("footer write buttons disable while HA is offline",
    source.includes("<button disabled={!haOnline} onClick={clearAllCooldowns}") &&
    source.includes("<button disabled={!haOnline} onClick={() => {"));
  assert("cooldown sweeper does not write while HA is offline",
    source.includes("if (!open || !haOnline) return undefined;") &&
    source.includes("}, [open, client, haOnline]);"));

  process.stdout.write("\ntravel_mode_wiring_test\n");
  assert("travel mode helper constant is present", source.includes('const TRAVEL_MODE_ENTITY = "input_boolean.living_lights_travel_mode";'));
  assert("travel mode helper is covered by owned prefix subscription", source.includes('"input_boolean.living_lights_"'));
  const renderStart = source.indexOf("<div role=\"dialog\"");
  const travelRender = source.indexOf("<TravelModeCard", renderStart);
  const scrollBody = source.indexOf("className=\"hg-scroll\"", renderStart);
  assert("travel mode card is rendered before the drawer scroll body", travelRender > -1 && scrollBody > -1 && travelRender < scrollBody);
  assert("travel mode button is not disabled by stale missing helper state", !source.includes("disabled={disabled || busy || !available}"));
  assert("travel mode toggle verifies helper after service call", source.includes("confirmedStates = await readHaStates()") && source.includes("Travel Mode helper is not loaded in Home Assistant yet"));
  assert("travel mode force-off includes every Living Lights bulb",
    [
      "light.dining_table_left",
      "light.dining_table_right",
      "light.dining_light",
      "light.dining_light_2",
      "light.dining_room_floodlight_timed",
      "light.front_left",
      "light.front_right",
      "light.kitchen_floodlight_timed",
      "light.island_left",
      "light.island_right",
      "light.living_room_lights",
      "light.office",
      "light.outdoor_light",
      "light.rear_left",
      "light.rear_right",
      "light.sink",
      "light.sink_light",
      "light.sink_light_2",
      "light.ambient_light_left_mss110_main_channel",
      "light.ambient_light_right_mss110_main_channel",
    ].every((entityId) => source.includes(entityId)));
  assert("travel mode force-off includes ambient switches",
    source.includes("switch.ambient_light_left_mss110_main_channel") &&
    source.includes("switch.ambient_light_right_mss110_main_channel") &&
    source.includes("switch.workshop_light_left_mss110_main_channel") &&
    source.includes("switch.workshop_light_right_mss110_main_channel"));
  assert("direct CT push is blocked while travel mode is on",
    source.includes('setError("travel mode is on: lighting output is blocked")'));

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
