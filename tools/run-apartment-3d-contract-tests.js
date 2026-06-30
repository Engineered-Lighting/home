#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const REPO = path.resolve(__dirname, "..");
const APT = fs.readFileSync(path.join(REPO, "app", "src", "home-apartment.jsx"), "utf8");
const CARDS = fs.readFileSync(path.join(REPO, "app", "src", "home-apartment-cards.jsx"), "utf8");
const DATA = fs.readFileSync(path.join(REPO, "app", "src", "home-apartment-data.js"), "utf8");
const MODES = fs.readFileSync(path.join(REPO, "app", "src", "home-3d", "modes.js"), "utf8");
const ENGINE = fs.readFileSync(path.join(REPO, "app", "src", "home-3d", "engine.js"), "utf8");
const RIG = fs.readFileSync(path.join(REPO, "app", "src", "home-3d", "rig.js"), "utf8");

let pass = 0;
let fail = 0;

function assert(name, cond, detail) {
  if (cond) {
    pass += 1;
    process.stdout.write(`  PASS  ${name}\n`);
  } else {
    fail += 1;
    process.stdout.write(`  FAIL  ${name}`);
    if (detail) process.stdout.write(`\n        ${detail}`);
    process.stdout.write("\n");
  }
}

process.stdout.write("\napartment_mesh_mode_contract_test\n");
assert("Spark renderer starts hidden", MODES.includes("sr.visible = false;"));
assert("visibleFor computes splatActive", MODES.includes("const splatActive = mode === 'splat' || !!state.fading;"));
assert("mesh/non-photo mode hides SplatMesh", MODES.includes("state.splat.visible = splatActive;"));
assert("mesh/non-photo mode hides SparkRenderer", MODES.includes("state.sparkRenderer.visible = splatActive;"));
assert("hidden splat opacity is forced to zero", MODES.includes("if (!splatActive && 'opacity' in state.splat) state.splat.opacity = 0;"));
assert("tickSpark is gated to active photo/fade modes", MODES.includes("state.mode === 'splat' || state.targetMode === 'splat'"));
assert("mode debug reports Spark visibility", MODES.includes("sparkVisible: state.sparkRenderer ? state.sparkRenderer.visible : null"));

process.stdout.write("\napartment_zoom_control_contract_test\n");
assert("AptZoomButton component exists", APT.includes("function AptZoomButton"));
assert("zoom controls have an accessible group label", APT.includes('aria-label="apartment zoom controls"'));
assert("zoom in button calls rig zoom in", APT.includes('title="zoom in"') && APT.includes("zoomApartment(1)"));
assert("zoom out button calls rig zoom out", APT.includes('title="zoom out"') && APT.includes("zoomApartment(-1)"));
assert("zoom controls hide during camera pose", APT.includes("const showZoomHud = !editing && !cameraTop"));
assert("debug API exposes zoomIn", APT.includes("zoomIn: () => zoomApartment(1)"));
assert("debug API exposes zoomOut", APT.includes("zoomOut: () => zoomApartment(-1)"));

process.stdout.write("\napartment_camera_snap_contract_test\n");
assert("mobile camera frame helper exists", APT.includes("function aptMobileCameraFrame"));
assert("camera snap stores the target camera immediately", APT.includes("setLiveCam(dev);") && APT.includes('"waiting for calibrated pose"') && APT.includes('"waiting for estimated pose"'));
assert("camera snap awaits mesh before fly-to-camera", APT.includes('await eng.modes.setMode("mesh", { duration: 0 });') && APT.includes("eng.flyToDevice(dev"));
assert("camera feed reveal polls until pose is held", APT.includes("const revealCameraFeedWhenReady"));
assert("feed reveal checks rig.inCameraPose", APT.includes("rig?.inCameraPose?.()"));
assert("debug snapshot exposes live feed status", APT.includes("liveFeedStatus,"));
assert("debug snapshot exposes camera frame", APT.includes("cameraFrame: mobile && liveCam"));
assert("raw camera frame remains visible before warp", APT.includes("opacity: warpedReady ? 0 : 1"));
assert("warped camera frame appears only after first draw", APT.includes("setWarpedReady(true)") && APT.includes('onStatus?.("warped")'));
assert("camera alignment helper distinguishes estimated vs calibrated", APT.includes("function aptCameraAlignment") && APT.includes("camera - estimated pose"));
assert("camera snap requires exact alignment metadata", APT.includes("const calib = alignment.exact"));
assert("camera debug snapshot exposes alignment status", APT.includes("cameraAlignment: liveCam ? aptCameraAlignment(liveCam) : null"));
assert("mobile live feed uses snapshot refresh fallback", APT.includes("function aptSnapshotSrc") && APT.includes("snapshotIntervalMs={mobile ? 1400 : 0}"));
assert("snapshot refresh keeps the calibrated frame cache-busted", APT.includes("function aptCacheBust") && APT.includes('onStatus?.(useSnapshots ? "frame" : "raw")'));
assert("snapshot refresh waits for image load before the next frame", APT.includes("publishFrameRef.current?.(Math.max(900, snapshotIntervalMs))") && !APT.includes("setInterval(publish"));
assert("snapshot refresh has a slow-network retry watchdog", APT.includes("loadWatchdogRef") && APT.includes('onStatus?.("retrying")'));
assert("camera feed maps pixels into the shared projection frame", APT.includes('objectFit: "fill"') && APT.includes('const liveFeedSettled = ["idle", "raw", "frame", "warped"].includes(liveFeedStatus);'));

process.stdout.write("\napartment_photo_mobile_asset_contract_test\n");
assert("mobile photo mode prefers generated mobile splat", MODES.includes("'apartment.mobile.ply'") && MODES.indexOf("'apartment.mobile.ply'") < MODES.indexOf("'apartment.ply'"));
assert("splat load has a timeout instead of hanging forever", MODES.includes("splat load timeout") && MODES.includes("mobile ? 45000 : 90000"));

process.stdout.write("\napartment_light_control_contract_test\n");
assert("card buttons are real touch targets", CARDS.includes('type="button"') && CARDS.includes("minHeight: mobile ? 38"));
assert("card controls stop canvas gesture propagation", CARDS.includes("const stopCardEvent") && CARDS.includes("onPointerDown={stopCardEvent}") && CARDS.includes("onClick={(e) => { stopCardEvent(e); onClick?.(e); }}"));
assert("apartment service calls support HA callService and raw call", DATA.includes('typeof client.callService === "function"') && DATA.includes('typeof client.call !== "function"'));
assert("apartment service payload uses HA target fields", DATA.includes('const targetKeys = new Set(["entity_id", "area_id", "device_id"])') && DATA.includes("payload.target = target"));
assert("apartment controls optimistically update selected state", APT.includes("function aptOptimisticServiceState") && APT.includes("setServicePulse((n) => n + 1)") && APT.includes("statesRef.current[entityId] = optimistic"));
assert("apartment controls rollback optimistic state on failure", APT.includes("statesRef.current[entityId] = prev") && APT.includes("didn't respond"));
assert("apartment debug API can open a controllable card for button audits", APT.includes("openFirstControllableCard") && APT.includes("setCardId(dev.id)"));

process.stdout.write("\napartment_calibrated_projection_contract_test\n");
assert("engine reads solved camera center C", ENGINE.includes("const calibratedCenter = Array.isArray(ex?.C)") && ENGINE.includes("new THREE.Vector3(+ex.C[0], +ex.C[1], +ex.C[2])"));
assert("engine builds projection from intrinsics K", ENGINE.includes("function projectionFromIntrinsics") && ENGINE.includes("2 * fx / p.w"));
assert("engine passes calibrated projection into rig", ENGINE.includes("projection = projectionFromIntrinsics(intr, fovScale)") && ENGINE.includes("rig.flyToPose({ position: worldPos, quaternion: worldQuat, fov, dur, projection })"));
assert("rig preserves custom projection while held", RIG.includes("projection: p.projection || null") && RIG.includes("applyCustomProjection(heldPose.projection)"));

process.stdout.write("\napartment_tracker_model_fallback_contract_test\n");
assert("data layer has a tracker model fallback", DATA.includes("async function fetchTrackerModel"));
assert("tracker fallback converts websocket bases to http", DATA.includes("function toHttpBase") && DATA.includes('clean.startsWith("ws://")'));
assert("tracker fallback fetches live /model", DATA.includes('fetcher(`${base}/model`, { cache: "no-store" })'));
assert("tracker model is tried before local remote cache", DATA.indexOf("const trackerModel = await fetchTrackerModel();") > 0
  && DATA.indexOf("const trackerModel = await fetchTrackerModel();") < DATA.indexOf('localStorage.getItem("apartment3d.remoteCache")'));
assert("HA model can be enriched from tracker calibration", DATA.includes("function mergeTrackerCameraCalibration")
  && DATA.includes("calibration_enriched: true"));
assert("tracker enrichment is limited to cameras missing calibration", DATA.includes("const camerasNeedCalibration = (model.devices || []).some")
  && DATA.includes("!cameraCalibrationComplete(d)"));

if (fail) {
  process.stdout.write(`\n${pass} pass . ${fail} fail\n`);
  process.exit(1);
}
process.stdout.write(`\n${pass} pass . 0 fail\n`);
