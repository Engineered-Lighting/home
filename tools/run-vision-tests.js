#!/usr/bin/env node
/* Pure local tests for app/src/home-vision.jsx helper/source contracts. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "home-vision.jsx");
const VISION_SERVICE_SRC = path.join(REPO, "stack", "services", "vision", "app.py");
const source = fs.readFileSync(SRC, "utf8");
const visionServiceSource = fs.readFileSync(VISION_SERVICE_SRC, "utf8");

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

function loadHelpers() {
  const start = source.indexOf("const HG_CAMERAS");
  const end = source.indexOf("/* Live frame:", start);
  if (start < 0 || end < 0) throw new Error("home-vision helper block not found");
  const script = source.slice(start, end)
    + "\nObject.assign(window, { HG_CAMERAS, VISION_REFRESH_MS, _haBaseFromWs });";
  const sandbox = { window: {}, URL };
  vm.runInNewContext(script, sandbox, { filename: "home-vision.helpers.js" });
  return sandbox.window;
}

(function main() {
  process.stdout.write("\nvision_helper_contract_test\n");
  const H = loadHelpers();
  assert("HG_CAMERAS exports five cameras", Array.isArray(H.HG_CAMERAS) && H.HG_CAMERAS.length === 5, H.HG_CAMERAS);
  assert("camera ids remain stable", H.HG_CAMERAS.map((c) => c.id).join(",") === "living_room,kitchen,dining_room,workshop,driveway", H.HG_CAMERAS);
  assert("camera entities remain Home Assistant camera entity ids", H.HG_CAMERAS.every((c) => /^camera\./.test(c.entity)), H.HG_CAMERAS);
  assert("vision stream refresh is two minutes", H.VISION_REFRESH_MS === 120000, H.VISION_REFRESH_MS);
  assert("ws HA URL maps to http base", H._haBaseFromWs("ws://192.168.0.125:8123/api/websocket") === "http://192.168.0.125:8123", H._haBaseFromWs("ws://192.168.0.125:8123/api/websocket"));
  assert("wss HA URL maps to https base", H._haBaseFromWs("wss://home.example/api/websocket") === "https://home.example", H._haBaseFromWs("wss://home.example/api/websocket"));
  assert("http HA URL remains http base", H._haBaseFromWs("http://ha.local:8123/api") === "http://ha.local:8123", H._haBaseFromWs("http://ha.local:8123/api"));
  assert("missing HA URL returns blank base", H._haBaseFromWs("") === "");
  assert("invalid HA URL returns blank base", H._haBaseFromWs("not a url") === "");

  process.stdout.write("\nvision_stream_source_contract_test\n");
  assert("vision requests signed camera_proxy_stream paths", source.includes('const path = `/api/camera_proxy_stream/${entity}`;') && source.includes('type: "auth/sign_path"'));
  assert("signed paths expire after one hour", source.includes("expires: 3600"));
  assert("signed stream is re-signed before expiry", source.includes("50 * 60 * 1000"));
  assert("stream remount cadence uses VISION_REFRESH_MS", source.includes("setInterval(() =>") && source.includes("VISION_REFRESH_MS"));
  assert("focus/visibility recovery remounts streams", source.includes('document.addEventListener("visibilitychange"') && source.includes('window.addEventListener("focus"'));
  assert("no-first-frame watchdog schedules reload", source.includes('scheduleReload("no first frame")') && source.includes("10000 + Math.min(failRef.current, 5) * 6000"));

  process.stdout.write("\nvision_sim_and_occupancy_source_contract_test\n");
  assert("Simulation Mode bypasses live signed stream by pausing hook", source.includes("paused: paused || !!simulationSrc"));
  assert("Simulation Mode suppresses live errors", source.includes("const err = simulationSrc ? null : live.err"));
  assert("Simulation Mode uses deterministic stream key", source.includes("const streamKey = simulationSrc ? `sim-${camera.id}` : live.streamKey"));
  assert("Simulation Mode changes remount live/sim frames", source.includes("prevSimRef.current !== simActive") && source.includes("setOpenCount((c) => c + 1)"));
  assert("occupied count derives from labels prop", source.includes("const occupied = cameras.filter((c) => (labels[c.id] || []).length > 0).length;"), source);
  assert("active camera labels derive from labels prop", source.includes("const objLabels = (labels[active.id] || []);"), source);
  assert("collapsed summary surfaces occupied count", source.includes('`${cameras.length} cameras') && source.includes('`${occupied} occupied`'));
  assert("vision exports card and camera roster", source.includes("Object.assign(window, { HomeVisionCard, HG_CAMERAS });"));

  process.stdout.write("\nvision_card_carousel_source_contract_test\n");
  assert("portrait camera tabs update selected camera", /onClick=\{\(\) => setIdx\(i\)\}/.test(source));
  assert("wide carousel click records suppression, selects camera, and centers tile",
    source.includes("lastClickAt.current = Date.now();") &&
    source.includes("setIdx(i);") &&
    source.includes('behavior: "smooth"') &&
    source.includes('inline: "center"') &&
    source.includes('block: "nearest"'));
  assert("carousel dwell selection is debounced through IntersectionObserver",
    source.includes("new IntersectionObserver") &&
    source.includes("timer = setTimeout(() => setIdx(i), 120)") &&
    source.includes('rootMargin: "0px -20% 0px -20%"') &&
    source.includes("threshold: [0.5, 0.75, 0.95]"));
  assert("hovered camera id is tracked for refresh pause",
    source.includes("const [hoverPausedCameraId, setHoverPausedCameraId] = React.useState(null);") &&
    source.includes("onMouseEnter={() => setHoverPausedCameraId(c.id)}") &&
    source.includes("onMouseLeave={() => setHoverPausedCameraId((cameraId) => (cameraId === c.id ? null : cameraId))}"));
  assert("hover pause is passed into HomeVisionFrame instead of hard-coded false",
    source.includes("paused={hoverPausedCameraId === c.id}") &&
    !source.includes("paused={false}"));

  process.stdout.write("\nvision_describe_clip_service_contract_test\n");
  assert("vision sidecar exposes POST /describe_clip", visionServiceSource.includes('@app.post("/describe_clip", response_model=DescribeClipOut)'));
  assert("describe_clip request model includes frames", visionServiceSource.includes("class DescribeClipIn") && visionServiceSource.includes("frames: int ="));
  assert("describe_clip response includes frames_captured", visionServiceSource.includes("class DescribeClipOut") && visionServiceSource.includes("frames_captured: int"));
  assert("describe_clip clamps requested frame count", visionServiceSource.includes("frames = max(DESCRIBE_CLIP_MIN_FRAMES, min(DESCRIBE_CLIP_MAX_FRAMES, body.frames))"));
  assert("describe_clip samples multiple frames", visionServiceSource.includes("for i in range(frames):") && visionServiceSource.includes("/api/camera_proxy/{entity_id}"));
  assert("describe_clip records sampling and inference latency", visionServiceSource.includes("sampling_ms") && visionServiceSource.includes("inference_ms"));
  assert("describe_clip returns captured frame count", visionServiceSource.includes("frames_captured=len(jpegs)"));

  if (fails) {
    console.log("\nFailures:");
    for (const f of failures) console.log("- " + f.name + (f.detail ? ": " + JSON.stringify(f.detail) : ""));
  }
  console.log(`\n${passes} pass . ${fails} fail`);
  process.exit(fails ? 1 : 0);
})();
