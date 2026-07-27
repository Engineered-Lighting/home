#!/usr/bin/env node
/* Pure local tests for app/src/home-control.jsx control-card helpers. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "home-control.jsx");
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

function sliceBetween(startNeedle, endNeedle) {
  const start = source.indexOf(startNeedle);
  if (start < 0) throw new Error("missing start marker: " + startNeedle);
  const end = source.indexOf(endNeedle, start);
  if (end < 0) throw new Error("missing end marker after " + startNeedle + ": " + endNeedle);
  return source.slice(start, end);
}

function loadHelpers() {
  const script = [
    sliceBetween("const CONTROL_EXPIRY_MS", "/* \u2500\u2500 Shared get_states cache"),
    sliceBetween("function fireServiceCall", "/* \u2500\u2500 ControlCardShell"),
    sliceBetween("function buildLightCaps", "// Synthetic optimistic caps"),
    sliceBetween("const MEDIA_SF", "// Album art URL"),
    "Object.assign(window, {",
    "CONTROL_EXPIRY_MS, LIGHT_CARD_SERVICES, MEDIA_CARD_SERVICES, VOLUME_ROUTING,",
    "humanize, distillSetValues, buildActionContext, ctxToHaTarget, deriveControlLifecycles,",
    "fireServiceCall, buildLightCaps, readMediaState, prettyRoom, mediaTitleOf,",
    "readTravelModeLocked",
    "});",
  ].join("\n\n");
  const sandbox = {
    window: {},
    console,
    localStorage: {
      _data: new Map(),
      getItem(key) { return this._data.has(key) ? this._data.get(key) : null; },
      setItem(key, value) { this._data.set(key, String(value)); },
      removeItem(key) { this._data.delete(key); },
    },
  };
  vm.runInNewContext(script, sandbox, { filename: "home-control.helpers.js" });
  sandbox.window.__testLocalStorage = sandbox.localStorage;
  return sandbox.window;
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
  process.stdout.write("\ncontrol_card_exports_test\n");
  const api = loadHelpers();
  assert("distillSetValues exported", typeof api.distillSetValues === "function");
  assert("buildActionContext exported", typeof api.buildActionContext === "function");
  assert("fireServiceCall exported", typeof api.fireServiceCall === "function");
  assert("buildLightCaps exported", typeof api.buildLightCaps === "function");
  assert("readMediaState exported", typeof api.readMediaState === "function");

  process.stdout.write("\ncontrol_card_light_surface_contract_test\n");
  const sliderBlock = sliceBetween("function ControlSlider", "function fireServiceCall");
  const lightBlock = sliceBetween("function LightControlCard", "function readMediaState");
  assert("ControlSlider commits on debounce and release events",
    /debRef\.current = setTimeout\(\(\) => \{[\s\S]*if \(onCommit\) onCommit\(v\);[\s\S]*\}, 120\);/.test(sliderBlock) &&
    /onPointerUp=\{flush\}/.test(sliderBlock) &&
    /onKeyUp=\{flush\}/.test(sliderBlock) &&
    /onBlur=\{flush\}/.test(sliderBlock),
    sliderBlock.slice(0, 1800));
  assert("LightControlCard brightness slider commits brightness_pct",
    /kind="brightness" label="brightness"[\s\S]*onDrag=\{setBri\}[\s\S]*onCommit=\{\(v\) => commit\(\{ brightness_pct: v \}/.test(lightBlock),
    lightBlock.slice(0, 5000));
  assert("LightControlCard color-temperature slider commits color_temp_kelvin",
    /kind="colorTemp" label="color temp"[\s\S]*onDrag=\{setKel\}[\s\S]*onCommit=\{\(v\) => commit\(\{ color_temp_kelvin: v \}/.test(lightBlock),
    lightBlock.slice(0, 5600));
  assert("LightControlCard renders all three color chips with expected kelvin values",
    /<QuickChip label="warm" disabled=\{colorDisabled\} onClick=\{\(\) => setKelChip\(2700\)\} \/>/.test(lightBlock) &&
    /<QuickChip label="neutral" disabled=\{colorDisabled\} onClick=\{\(\) => setKelChip\(4000\)\} \/>/.test(lightBlock) &&
    /<QuickChip label="cool" disabled=\{colorDisabled\} onClick=\{\(\) => setKelChip\(5500\)\} \/>/.test(lightBlock),
    lightBlock.slice(0, 6500));
  assert("LightControlCard renders dim and bright chips with expected brightness values",
    /<QuickChip label="dim" disabled=\{briDisabled\} onClick=\{\(\) => setBriChip\(20\)\} \/>/.test(lightBlock) &&
    /<QuickChip label="bright" disabled=\{briDisabled\} onClick=\{\(\) => setBriChip\(100\)\} \/>/.test(lightBlock),
    lightBlock.slice(0, 7000));
  assert("chip helpers commit through the same light.turn_on path",
    /const setKelChip = \(v\) => \{[\s\S]*if \(travelModeLocked\) return;[\s\S]*clampNum\(v, ctMin, ctMax\)[\s\S]*commit\(\{ color_temp_kelvin: cv \}/.test(lightBlock) &&
    /const setBriChip = \(v\) => \{[\s\S]*if \(travelModeLocked\) return;[\s\S]*setBri\(v\); commit\(\{ brightness_pct: v \}/.test(lightBlock),
    lightBlock.slice(0, 4300));
  assert("LightControlCard commit uses one light.turn_on dispatcher and reconciles afterward",
    /await fireServiceCall\(\{[\s\S]*domain: "light", service: "turn_on", service_data,[\s\S]*target: ctxToHaTarget\(ctx\), simTargets: ctx\.targetEntities, onControlAction/.test(lightBlock) &&
    /setStatus\("updated"\)[\s\S]*reconcile\(\); fadeToReady\(\);/.test(lightBlock) &&
    /setStatus\("error"\); setStatusMsg/.test(lightBlock),
    lightBlock.slice(0, 4600));
  assert("LightControlCard error dismiss resets visible status only",
    /status === "error"[\s\S]*<button[\s\S]*onClick=\{\(\) => \{ setStatus\("idle"\); setStatusMsg\(""\); \}\}[\s\S]*>dismiss<\/button>/.test(lightBlock),
    lightBlock.slice(0, 7600));
  assert("LightControlCard locks controls while travel mode is enabled",
    /const travelModeLocked = useTravelModeLock\(\);/.test(lightBlock) &&
    /if \(travelModeLocked\) \{[\s\S]*setStatus\("locked"\);[\s\S]*return;[\s\S]*\}/.test(lightBlock) &&
    /disabled=\{briDisabled\}/.test(lightBlock) &&
    /disabled=\{colorDisabled\}/.test(lightBlock) &&
    /travel mode enabled - controls locked/.test(lightBlock),
    lightBlock.slice(0, 9000));

  process.stdout.write("\ncontrol_card_value_distillation_test\n");
  const overBright = api.distillSetValues("light", { brightness_pct: 120 });
  assert("brightness_pct is clamped to 100", overBright.brightness_pct === 100, overBright);
  const rawBright = api.distillSetValues("light", { brightness: 128 });
  assert("raw brightness converts to pct", rawBright.brightness_pct === 50, rawBright);
  const mired = api.distillSetValues("light", { color_temp: 500 });
  assert("mired color_temp converts to kelvin", mired.color_temp_kelvin === 2000, mired);
  const kelvin = api.distillSetValues("light", { kelvin: 4100 });
  assert("kelvin alias is preserved", kelvin.color_temp_kelvin === 4100, kelvin);
  const mediaValues = api.distillSetValues("media_player", { brightness_pct: 50 });
  assert("non-light domains do not expose light slider values", Object.keys(mediaValues).length === 0, mediaValues);

  process.stdout.write("\ncontrol_card_action_context_test\n");
  const lightCtx = api.buildActionContext({
    id: "evt-light",
    service: "light.turn_on",
    attrs: { brightness_pct: 80, color_temp_kelvin: 3200 },
    entityTargets: ["light.kitchen_ceiling", "light.kitchen_sink"],
    areaTargets: [],
    deviceTargets: [],
    status: "success",
  });
  assert("light turn_on is controllable", lightCtx.controllable === true, lightCtx);
  assert("light context keeps domain", lightCtx.domain === "light", lightCtx);
  assert("light context keeps service", lightCtx.service === "light.turn_on", lightCtx);
  assert("entity-only targets remain entity kind", lightCtx.targetKind === "entity", lightCtx);
  assert("all entity targets are preserved", lightCtx.targetEntities.length === 2, lightCtx.targetEntities);
  assert("set values carry initial brightness", lightCtx.setValues.brightness_pct === 80, lightCtx.setValues);
  assert("summary mentions extra target count", /\+1$/.test(lightCtx.actionSummary), lightCtx.actionSummary);

  const offCtx = api.buildActionContext({
    id: "evt-off",
    service: "light.turn_off",
    entityTargets: ["light.kitchen_ceiling"],
  });
  assert("light turn_off does not create a rich control card", offCtx.controllable === false, offCtx);

  const areaMediaCtx = api.buildActionContext({
    id: "evt-media",
    service: "media_player.volume_set",
    attrs: { volume_level: 0.33 },
    areaTargets: ["living_room"],
  });
  assert("media volume_set is controllable", areaMediaCtx.controllable === true, areaMediaCtx);
  assert("area-only media target remains area kind", areaMediaCtx.targetKind === "area", areaMediaCtx);

  const mixedCtx = api.buildActionContext({
    id: "evt-mixed",
    service: "media_player.media_pause",
    entityTargets: ["media_player.living_room"],
    areaTargets: ["office"],
  });
  assert("mixed entity and area targets are marked mixed", mixedCtx.targetKind === "mixed", mixedCtx);

  process.stdout.write("\ncontrol_card_target_test\n");
  const haTarget = api.ctxToHaTarget(mixedCtx);
  assert("ctxToHaTarget keeps entity_id", haTarget.entity_id[0] === "media_player.living_room", haTarget);
  assert("ctxToHaTarget keeps area_id", haTarget.area_id[0] === "office", haTarget);
  assert("ctxToHaTarget omits empty target", api.ctxToHaTarget({}) === null);

  process.stdout.write("\ncontrol_card_lifecycle_test\n");
  const now = 1_000_000;
  const lifecycleEvents = [
    {
      id: "older-light",
      kind: "action",
      controllable: true,
      actionCtx: { domain: "light", status: "success", completedAt: now - 5_000 },
    },
    {
      id: "newer-light",
      kind: "action",
      controllable: true,
      actionCtx: { domain: "light", status: "success", completedAt: now - 1_000 },
    },
    {
      id: "failed-media",
      kind: "action",
      controllable: true,
      actionCtx: { domain: "media_player", status: "failed", completedAt: now - 1_000 },
    },
    {
      id: "stale-cover",
      kind: "action",
      controllable: true,
      actionCtx: { domain: "cover", status: "success", completedAt: now - api.CONTROL_EXPIRY_MS - 1 },
    },
    {
      id: "plain-action",
      kind: "action",
      controllable: false,
      actionCtx: { domain: "light", status: "success", completedAt: now },
    },
  ];
  const lifecycles = api.deriveControlLifecycles(lifecycleEvents, now);
  assert("newest same-domain action is active", lifecycles.get("newer-light") === "active", Array.from(lifecycles.entries()));
  assert("older same-domain action is superseded", lifecycles.get("older-light") === "superseded", Array.from(lifecycles.entries()));
  assert("failed action remains failed", lifecycles.get("failed-media") === "failed", Array.from(lifecycles.entries()));
  assert("stale newest domain action expires", lifecycles.get("stale-cover") === "expired", Array.from(lifecycles.entries()));
  assert("non-controllable action is excluded", !lifecycles.has("plain-action"), Array.from(lifecycles.entries()));

  process.stdout.write("\ncontrol_card_dispatch_test\n");
  const simCalls = [];
  api.__SIM_ACTIVE = true;
  api.SimControlStore = {
    callService(payload) {
      simCalls.push(payload);
      return Promise.resolve({ ok: true });
    },
  };
  let realCalled = false;
  await api.fireServiceCall({
    domain: "light",
    service: "turn_on",
    service_data: { brightness_pct: 75 },
    target: { area_id: ["kitchen"] },
    simTargets: ["light.kitchen_ceiling"],
    onControlAction() {
      realCalled = true;
      return Promise.resolve();
    },
  });
  assert("simulation dispatch calls SimControlStore once", simCalls.length === 1, simCalls);
  assert("simulation dispatch keeps sim targets", simCalls[0].targets[0] === "light.kitchen_ceiling", simCalls[0]);
  assert("simulation dispatch does not call real HA handler", realCalled === false);

  api.__SIM_ACTIVE = false;
  const realCalls = [];
  await api.fireServiceCall({
    domain: "media_player",
    service: "volume_set",
    service_data: { volume_level: 0.42 },
    target: { entity_id: ["media_player.tx_rz30"] },
    simTargets: ["media_player.living_room_sonos"],
    onControlAction(payload) {
      realCalls.push(payload);
      return Promise.resolve("ok");
    },
  });
  assert("real dispatch calls onControlAction once", realCalls.length === 1, realCalls);
  assert("real dispatch keeps service_data", realCalls[0].service_data.volume_level === 0.42, realCalls[0]);
  assert("real dispatch keeps HA target", realCalls[0].target.entity_id[0] === "media_player.tx_rz30", realCalls[0]);
  await expectReject(
    "real dispatch rejects without control handler",
    api.fireServiceCall({ domain: "light", service: "turn_on", target: { entity_id: ["light.a"] } }),
    /no control handler/,
  );

  process.stdout.write("\ncontrol_card_capability_test\n");
  const lightStates = new Map([
    ["light.kitchen_ceiling", {
      entity_id: "light.kitchen_ceiling",
      state: "on",
      attributes: {
        friendly_name: "Kitchen Ceiling",
        supported_color_modes: ["brightness", "color_temp"],
        brightness: 128,
        color_temp_kelvin: 3000,
        min_color_temp_kelvin: 2200,
        max_color_temp_kelvin: 5000,
      },
    }],
    ["light.kitchen_sink", {
      entity_id: "light.kitchen_sink",
      state: "on",
      attributes: {
        friendly_name: "Kitchen Sink",
        supported_color_modes: ["brightness"],
        brightness: 255,
      },
    }],
    ["light.dead_strip", {
      entity_id: "light.dead_strip",
      state: "unavailable",
      attributes: {},
    }],
  ]);
  const caps = api.buildLightCaps(lightStates, ["light.kitchen_ceiling", "light.kitchen_sink", "light.missing"]);
  assert("buildLightCaps marks available entities", caps.anyAvailable === true, caps);
  assert("buildLightCaps detects brightness support", caps.anyBrightness === true, caps);
  assert("buildLightCaps detects color-temp support", caps.anyColorTemp === true, caps);
  assert("buildLightCaps averages brightness from available entities", caps.avgBrightnessPct === 75, caps);
  assert("buildLightCaps marks missing entities", caps.entities.some((e) => e.entity_id === "light.missing" && e.missing), caps.entities);

  const mediaStates = new Map([
    ["media_player.living_room", {
      entity_id: "media_player.living_room",
      state: "playing",
      attributes: {
        friendly_name: "Living Room Sonos",
        media_title: "Everything in Its Right Place",
        media_artist: "Radiohead",
        supported_features: 4 | 8 | 16 | 32 | 524288,
        volume_level: 0.31,
        group_members: ["media_player.living_room", "media_player.office"],
      },
    }],
  ]);
  const media = api.readMediaState(mediaStates, "media_player.living_room");
  assert("readMediaState reports playing", media.playing === true, media);
  assert("readMediaState maps volume capability bit", media.caps.volume === true, media);
  assert("readMediaState maps grouping capability bit", media.caps.group === true, media);
  assert("mediaTitleOf trims common speaker suffixes in groups", api.mediaTitleOf(mediaStates, ["media_player.living_room", "media_player.office_speaker"]) === "Living Room + Office");
  assert("missing media entity is unavailable", api.readMediaState(mediaStates, "media_player.missing").missing === true);

  process.stdout.write("\ntravel_mode_control_lock_test\n");
  assert("travel mode lock is off by default", api.readTravelModeLocked() === false);
  api.__HOME_TRAVEL_MODE_STATE = "on";
  assert("travel mode lock reads same-tab window state", api.readTravelModeLocked() === true);
  api.__HOME_TRAVEL_MODE_STATE = "off";
  api.__testLocalStorage.setItem("home.lights.travelMode.state.v1", "on");
  assert("travel mode lock falls back to localStorage", api.readTravelModeLocked() === true);

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
