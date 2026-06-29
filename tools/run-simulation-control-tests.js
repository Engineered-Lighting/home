#!/usr/bin/env node
/* Pure local tests for app/src/simulation-controls.jsx. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "simulation-controls.jsx");
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

function loadModule() {
  const sandbox = { window: {}, Object, Set, Array, Math, Promise };
  vm.runInNewContext(source, sandbox, { filename: SRC });
  return sandbox.window.SimControlStore;
}

async function main() {
  process.stdout.write("\nsim_control_exports_test\n");
  const store = loadModule();
  assert("SimControlStore exported", store && typeof store === "object");
  assert("store exposes reset/get/subscribe/callService", typeof store.reset === "function" && typeof store.getState === "function" && typeof store.subscribe === "function" && typeof store.callService === "function");
  assert("default light state mirrors HA shape", store.getState("light.kitchen").state === "on" && store.getState("light.kitchen").attributes.brightness === 200, store.getState("light.kitchen"));
  assert("default media state mirrors HA shape", store.getState("media_player.kitchen_sonos").state === "playing" && Array.isArray(store.getState("media_player.kitchen_sonos").attributes.group_members), store.getState("media_player.kitchen_sonos"));
  assert("unknown entity returns null", store.getState("light.nope") === null);
  assert("getAll exposes all default entities", Object.keys(store.getAll()).length >= 9 && store.getAll()["media_player.living_room_onkyo"], Object.keys(store.getAll()));

  process.stdout.write("\nsim_control_subscription_test\n");
  let emits = 0;
  const unsub = store.subscribe(() => { emits += 1; });
  await store.callService({ domain: "light", service: "turn_off", targets: ["light.kitchen"] });
  assert("callService emits subscribers", emits === 1, emits);
  unsub();
  await store.callService({ domain: "light", service: "turn_on", targets: ["light.kitchen"] });
  assert("unsubscribe stops subscriber notifications", emits === 1, emits);
  store.subscribe(() => { emits += 1; });
  store.reset();
  assert("reset emits subscribers", emits === 2, emits);

  process.stdout.write("\nsim_control_light_service_test\n");
  await store.callService({
    domain: "light",
    service: "turn_on",
    targets: ["light.office"],
    service_data: { brightness_pct: 50, color_temp_kelvin: 4200 },
  });
  const officeLight = store.getState("light.office");
  assert("light.turn_on powers on and maps brightness_pct", officeLight.state === "on" && officeLight.attributes.brightness === 128, officeLight);
  assert("light.turn_on applies color temperature", officeLight.attributes.color_temp_kelvin === 4200, officeLight);
  await store.callService({
    domain: "light",
    service: "turn_on",
    targets: ["light.office"],
    service_data: { brightness_pct: 10, brightness: 33 },
  });
  assert("raw brightness overrides brightness_pct when both supplied", store.getState("light.office").attributes.brightness === 33, store.getState("light.office"));
  await store.callService({ domain: "light", service: "toggle", targets: ["light.office"] });
  assert("light.toggle turns on light off", store.getState("light.office").state === "off", store.getState("light.office"));
  await store.callService({ domain: "light", service: "toggle", targets: ["light.office"], service_data: { brightness_pct: 100 } });
  assert("light.toggle turns off light on and applies new brightness", store.getState("light.office").state === "on" && store.getState("light.office").attributes.brightness === 255, store.getState("light.office"));
  await store.callService({ domain: "light", service: "turn_off", targets: ["light.office"] });
  assert("light.turn_off powers off", store.getState("light.office").state === "off", store.getState("light.office"));
  await store.callService({ domain: "light", service: "turn_on", targets: ["light.unknown"], service_data: { brightness_pct: 1 } });
  assert("unknown light target is ignored", store.getState("light.unknown") === null);

  process.stdout.write("\nsim_control_media_service_test\n");
  await store.callService({ domain: "media_player", service: "media_pause", targets: ["media_player.kitchen_sonos"] });
  assert("media_pause sets paused", store.getState("media_player.kitchen_sonos").state === "paused", store.getState("media_player.kitchen_sonos"));
  await store.callService({ domain: "media_player", service: "media_play", targets: ["media_player.kitchen_sonos"] });
  assert("media_play sets playing", store.getState("media_player.kitchen_sonos").state === "playing", store.getState("media_player.kitchen_sonos"));
  await store.callService({ domain: "media_player", service: "media_play_pause", targets: ["media_player.kitchen_sonos"] });
  assert("media_play_pause toggles playing to paused", store.getState("media_player.kitchen_sonos").state === "paused", store.getState("media_player.kitchen_sonos"));
  await store.callService({ domain: "media_player", service: "media_play_pause", targets: ["media_player.kitchen_sonos"] });
  assert("media_play_pause toggles paused to playing", store.getState("media_player.kitchen_sonos").state === "playing", store.getState("media_player.kitchen_sonos"));

  const beforeTitle = store.getState("media_player.kitchen_sonos").attributes.media_title;
  await store.callService({ domain: "media_player", service: "media_next_track", targets: ["media_player.kitchen_sonos"] });
  const nextTitle = store.getState("media_player.kitchen_sonos").attributes.media_title;
  await store.callService({ domain: "media_player", service: "media_previous_track", targets: ["media_player.kitchen_sonos"] });
  const prevTitle = store.getState("media_player.kitchen_sonos").attributes.media_title;
  assert("next/previous track cycles playlist", beforeTitle === "Topographic Oceans" && nextTitle === "Field of Reeds" && prevTitle === "Topographic Oceans", { beforeTitle, nextTitle, prevTitle });

  await store.callService({ domain: "media_player", service: "volume_set", targets: ["media_player.kitchen_sonos"], service_data: { volume_level: 2 } });
  assert("volume_set clamps high values", store.getState("media_player.kitchen_sonos").attributes.volume_level === 1, store.getState("media_player.kitchen_sonos"));
  await store.callService({ domain: "media_player", service: "volume_set", targets: ["media_player.kitchen_sonos"], service_data: { volume_level: -1 } });
  assert("volume_set clamps low values", store.getState("media_player.kitchen_sonos").attributes.volume_level === 0, store.getState("media_player.kitchen_sonos"));
  await store.callService({ domain: "media_player", service: "volume_mute", targets: ["media_player.kitchen_sonos"], service_data: { is_volume_muted: true } });
  assert("volume_mute sets mute flag", store.getState("media_player.kitchen_sonos").attributes.is_volume_muted === true, store.getState("media_player.kitchen_sonos"));

  process.stdout.write("\nsim_control_media_group_test\n");
  await store.callService({
    domain: "media_player",
    service: "join",
    targets: ["media_player.kitchen_sonos"],
    service_data: { group_members: ["media_player.office_sonos", "media_player.kitchen_sonos"] },
  });
  const kitchenGroup = store.getState("media_player.kitchen_sonos").attributes.group_members;
  const officePlayer = store.getState("media_player.office_sonos");
  assert("join merges group members uniquely", JSON.stringify(kitchenGroup) === JSON.stringify(["media_player.kitchen_sonos", "media_player.office_sonos"]), kitchenGroup);
  assert("join propagates group and playing state to members", JSON.stringify(officePlayer.attributes.group_members) === JSON.stringify(kitchenGroup) && officePlayer.state === "playing", officePlayer);
  await store.callService({ domain: "media_player", service: "unjoin", targets: ["media_player.kitchen_sonos"] });
  assert("unjoin resets target group to itself", JSON.stringify(store.getState("media_player.kitchen_sonos").attributes.group_members) === JSON.stringify(["media_player.kitchen_sonos"]), store.getState("media_player.kitchen_sonos"));
  assert("unjoin removes target from other groups", !store.getState("media_player.office_sonos").attributes.group_members.includes("media_player.kitchen_sonos"), store.getState("media_player.office_sonos"));

  process.stdout.write("\nsim_control_reset_test\n");
  store.reset();
  assert("reset restores default light values", store.getState("light.kitchen").state === "on" && store.getState("light.office").state === "off", { kitchen: store.getState("light.kitchen"), office: store.getState("light.office") });
  assert("reset restores default media values", store.getState("media_player.kitchen_sonos").attributes.volume_level === 0.34 && store.getState("media_player.kitchen_sonos").state === "playing", store.getState("media_player.kitchen_sonos"));

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
