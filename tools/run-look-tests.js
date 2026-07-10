#!/usr/bin/env node
/* Pure local tests for app/src/home-look.jsx /look helpers and command wiring. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const LOOK_SRC = path.join(REPO, "app", "src", "home-look.jsx");
const APP_SRC = path.join(REPO, "app", "src", "home-app.jsx");
const NATURAL_LOOK_SRC = path.join(REPO, "app", "src", "home-natural-look.js");
const source = fs.readFileSync(LOOK_SRC, "utf8");
const appSource = fs.readFileSync(APP_SRC, "utf8");
const naturalLookSource = fs.readFileSync(NATURAL_LOOK_SRC, "utf8");

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

function loadHelpers(cameras, extraWindow) {
  const start = source.indexOf("const { useState");
  const end = source.indexOf("/* A plain text segment", start);
  if (start < 0 || end < 0) throw new Error("home-look helper block not found");
  const script = source.slice(start, end)
    + "\nObject.assign(window, { LK_CAMERAS, lkParseArg, lkVisionUrl, lkReasonRequest, lkReasonZoomRequest });";
  const sandbox = {
    window: { ...(cameras ? { HG_CAMERAS: cameras } : {}), ...(extraWindow || {}) },
    React: {
      useState() {},
      useEffect() {},
      useRef() {},
      useCallback() {},
    },
    URL,
  };
  vm.runInNewContext(script, sandbox, { filename: "home-look.helpers.js" });
  return sandbox.window;
}

function loadAppDeepLookHelpers() {
  const sandbox = { window: {}, module: { exports: {} }, exports: {}, URL, setTimeout, clearTimeout, AbortController };
  vm.runInNewContext(naturalLookSource, sandbox, { filename: "home-natural-look.js" });
  return sandbox.module.exports;
}

(async function main() {
  process.stdout.write("\nlook_helper_exports_test\n");
  const api = loadHelpers();
  assert("lkParseArg exported in test harness", typeof api.lkParseArg === "function");
  assert("lkVisionUrl exported in test harness", typeof api.lkVisionUrl === "function");
  assert("lkReasonRequest exported in test harness", typeof api.lkReasonRequest === "function");
  assert("lkReasonZoomRequest exported in test harness", typeof api.lkReasonZoomRequest === "function");
  assert("fallback camera roster is present", Array.isArray(api.LK_CAMERAS) && api.LK_CAMERAS.length >= 5, api.LK_CAMERAS);

  process.stdout.write("\nlook_parse_default_roster_test\n");
  assert("empty arg opens drawer without camera/question", JSON.stringify(api.lkParseArg("")) === JSON.stringify({ camera: null, question: "" }), api.lkParseArg(""));
  assert("unknown prefix remains a question", JSON.stringify(api.lkParseArg("what is on the counter")) === JSON.stringify({ camera: null, question: "what is on the counter" }), api.lkParseArg("what is on the counter"));
  assert("single-word camera name is parsed", JSON.stringify(api.lkParseArg("kitchen what is on the counter")) === JSON.stringify({ camera: "kitchen", question: "what is on the counter" }), api.lkParseArg("kitchen what is on the counter"));
  assert("multi-word camera name is parsed", JSON.stringify(api.lkParseArg("living room is anyone there")) === JSON.stringify({ camera: "living_room", question: "is anyone there" }), api.lkParseArg("living room is anyone there"));
  assert("underscore camera id is parsed", JSON.stringify(api.lkParseArg("dining_room are the chairs visible")) === JSON.stringify({ camera: "dining_room", question: "are the chairs visible" }), api.lkParseArg("dining_room are the chairs visible"));
  assert("camera entity_id is parsed", JSON.stringify(api.lkParseArg("camera.kitchen what is on the island")) === JSON.stringify({ camera: "kitchen", question: "what is on the island" }), api.lkParseArg("camera.kitchen what is on the island"));
  assert("exact camera-only arg produces empty question", JSON.stringify(api.lkParseArg("camera.driveway")) === JSON.stringify({ camera: "driveway", question: "" }), api.lkParseArg("camera.driveway"));
  assert("case-insensitive camera parse preserves question text", JSON.stringify(api.lkParseArg("Kitchen What Is On The Counter")) === JSON.stringify({ camera: "kitchen", question: "What Is On The Counter" }), api.lkParseArg("Kitchen What Is On The Counter"));

  process.stdout.write("\nlook_parse_custom_roster_test\n");
  const custom = loadHelpers([
    { id: "living", entity: "camera.living", name: "living" },
    { id: "living_room", entity: "camera.living_room", name: "living room" },
    { id: "front_door", entity: "camera.front_door", name: "front door" },
  ]);
  assert("custom roster from HG_CAMERAS is used", custom.LK_CAMERAS.length === 3, custom.LK_CAMERAS);
  assert("longest camera prefix wins", JSON.stringify(custom.lkParseArg("living room what changed")) === JSON.stringify({ camera: "living_room", question: "what changed" }), custom.lkParseArg("living room what changed"));
  assert("custom entity_id prefix is parsed", JSON.stringify(custom.lkParseArg("camera.front_door is the package there")) === JSON.stringify({ camera: "front_door", question: "is the package there" }), custom.lkParseArg("camera.front_door is the package there"));
  assert("camera id space variant is parsed", JSON.stringify(custom.lkParseArg("front door is open")) === JSON.stringify({ camera: "front_door", question: "is open" }), custom.lkParseArg("front door is open"));

  process.stdout.write("\nlook_vision_url_test\n");
  assert("http metrics base maps to sidecar port", api.lkVisionUrl("http://192.168.0.100:8092") === "http://192.168.0.100:8091", api.lkVisionUrl("http://192.168.0.100:8092"));
  assert("https metrics base maps to https sidecar port", api.lkVisionUrl("https://ai-box.local:8092/path") === "https://ai-box.local:8091", api.lkVisionUrl("https://ai-box.local:8092/path"));
  const webLook = loadHelpers(null, { HG_DEFAULT_VISION_BASE: "/proxy/vision" });
  assert("web runtime vision default wins over metrics derivation", webLook.lkVisionUrl("http://192.168.0.100:8092") === "/proxy/vision", webLook.lkVisionUrl("http://192.168.0.100:8092"));
  assert("invalid metrics base returns empty string", api.lkVisionUrl("not a url") === "", api.lkVisionUrl("not a url"));
  assert("empty metrics base returns empty string", api.lkVisionUrl("") === "", api.lkVisionUrl(""));

  process.stdout.write("\nnatural_deep_look_classifier_test\n");
  const deep = loadAppDeepLookHelpers();
  assert("apartment visual question triggers deep look", deep.detectDeepLookIntent("what do you see in my apartment")?.scope === "apartment", deep.detectDeepLookIntent("what do you see in my apartment"));
  assert("home visual question uses home scope", deep.detectDeepLookIntent("what do you see in my home")?.scope === "home", deep.detectDeepLookIntent("what do you see in my home"));
  assert("specific camera visual question resolves camera", deep.detectDeepLookIntent("look at the driveway")?.explicitCamera === "driveway", deep.detectDeepLookIntent("look at the driveway"));
  assert("light state question does not trigger deep look", deep.detectDeepLookIntent("which lights are on") === null, deep.detectDeepLookIntent("which lights are on"));
  assert("light action does not trigger deep look", deep.detectDeepLookIntent("turn off all my lights") === null, deep.detectDeepLookIntent("turn off all my lights"));

  const selectedApt = deep.selectDeepLookCameras(
    deep.detectDeepLookIntent("what do you see in my apartment"),
    { rooms: { kitchen: { occupied: true, age_s: 5 }, living_room: { age_s: 30 } } },
    3
  ).map((c) => c.id);
  assert("apartment smart selection prioritizes occupied indoor cameras", selectedApt[0] === "kitchen" && !selectedApt.includes("driveway"), selectedApt);
  const selectedHome = deep.selectDeepLookCameras(
    deep.detectDeepLookIntent("what do you see in my home"),
    { rooms: { driveway: { occupied: true, age_s: 2 } } },
    3
  ).map((c) => c.id);
  assert("home smart selection can include outdoor cameras", selectedHome.includes("driveway"), selectedHome);

  process.stdout.write("\nlook_reason_zoom_request_test\n");
  let seenRequest = null;
  await api.lkReasonZoomRequest({
    metricsBase: "http://192.168.0.100:8092",
    camera: "kitchen",
    question: "what is on the counter",
    cacheBust: 123,
    fetchImpl: async (url, opts) => {
      seenRequest = { url, opts };
      return {
        ok: true,
        async json() {
          return {
            camera: "kitchen",
            answer: "a mug is on the counter",
            overview_url: "/reason_zoom/kitchen/overview.jpg",
            detail_url: "/reason_zoom/kitchen/detail.jpg",
          };
        },
      };
    },
  }).then((data) => {
    assert("reason_zoom request hits sidecar endpoint", seenRequest.url === "http://192.168.0.100:8091/reason_zoom", seenRequest);
    assert("reason_zoom request posts camera/question", seenRequest.opts.body === JSON.stringify({ camera: "kitchen", question: "what is on the counter" }), seenRequest.opts.body);
    assert("reason_zoom response creates cache-busted detail URL", data.detailUrl === "http://192.168.0.100:8091/reason_zoom/kitchen/detail.jpg?cb=123", data);
  }).catch((e) => {
    assert("reason_zoom request should not throw", false, e.message);
  });

  process.stdout.write("\nlook_reason_full_frame_request_test\n");
  seenRequest = null;
  await api.lkReasonRequest({
    metricsBase: "http://192.168.0.100:8092",
    camera: "driveway",
    question: "what is in the driveway",
    cacheBust: 456,
    fetchImpl: async (url, opts) => {
      seenRequest = { url, opts };
      return {
        ok: true,
        async json() {
          return {
            camera: "driveway",
            answer: "a covered vehicle is in the driveway",
            annotated_url: "/reason/driveway/annotated.jpg",
          };
        },
      };
    },
  }).then((data) => {
    assert("full-frame reason request hits sidecar endpoint", seenRequest.url === "http://192.168.0.100:8091/reason", seenRequest);
    assert("full-frame reason request posts camera/question", seenRequest.opts.body === JSON.stringify({ camera: "driveway", question: "what is in the driveway" }), seenRequest.opts.body);
    assert("full-frame reason response creates cache-busted annotated URL", data.annotatedUrl === "http://192.168.0.100:8091/reason/driveway/annotated.jpg?cb=456", data);
  }).catch((e) => {
    assert("full-frame reason request should not throw", false, e.message);
  });

  process.stdout.write("\nlook_command_wiring_test\n");
  assert("HomeLookParseArg is exported to window", source.includes("window.HomeLookParseArg = lkParseArg;"));
  assert("HomeLookDrawer is exported to window", source.includes("window.HomeLookDrawer = HomeLookDrawer;"));
  assert("HomeLookReasonRequest is exported to window", source.includes("window.HomeLookReasonRequest = lkReasonRequest;"));
  assert("natural look uses full-frame runner before zoom runner", appSource.includes("lookFullFrameRunner: window.HomeLookReasonRequest") && naturalLookSource.includes("opts.lookFullFrameRunner"));
  assert("natural look router is loaded before home-app", appSource.includes("window.HomeNaturalLook") && fs.readFileSync(path.join(REPO, "app", "src", "index.html"), "utf8").indexOf("home-natural-look.js") < fs.readFileSync(path.join(REPO, "app", "src", "index.html"), "utf8").indexOf("home-app.jsx"));
  const lookCaseStart = appSource.indexOf('case "look"');
  const lookCaseEnd = appSource.indexOf('case "world-state"', lookCaseStart);
  const lookCase = lookCaseStart >= 0 && lookCaseEnd >= 0 ? appSource.slice(lookCaseStart, lookCaseEnd) : "";
  assert("/look handler exists", lookCase.length > 0);
  assert("/look handler uses HomeLookParseArg", lookCase.includes("window.HomeLookParseArg"), lookCase);
  assert("/look handler sets initial camera/question", lookCase.includes("camera: parsed.camera") && lookCase.includes("question: parsed.question"), lookCase);
  assert("/look handler opens drawer", lookCase.includes("setLookDrawerOpen(true)"), lookCase);

  if (fails) {
    console.log("\nFailures:");
    for (const f of failures) console.log("- " + f.name + (f.detail ? ": " + JSON.stringify(f.detail) : ""));
  }
  console.log(`\n${passes} pass . ${fails} fail`);
  process.exit(fails ? 1 : 0);
})()
