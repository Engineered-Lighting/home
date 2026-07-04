#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const REPO = path.resolve(__dirname, "..");
const APT = fs.readFileSync(path.join(REPO, "app", "src", "home-apartment.jsx"), "utf8");
const CARDS = fs.readFileSync(path.join(REPO, "app", "src", "home-apartment-cards.jsx"), "utf8");
const DATA = fs.readFileSync(path.join(REPO, "app", "src", "home-apartment-data.js"), "utf8");
const APP = fs.readFileSync(path.join(REPO, "app", "src", "home-app.jsx"), "utf8");
const MODES = fs.readFileSync(path.join(REPO, "app", "src", "home-3d", "modes.js"), "utf8");
const ENGINE = fs.readFileSync(path.join(REPO, "app", "src", "home-3d", "engine.js"), "utf8");
const RIG = fs.readFileSync(path.join(REPO, "app", "src", "home-3d", "rig.js"), "utf8");
const PREWARM = fs.readFileSync(path.join(REPO, "app", "src", "home-apartment-prewarm.js"), "utf8");

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
assert("mesh fallback does not use neon normal material", !MODES.includes("MeshNormalMaterial") && MODES.includes("meshDisplayMaterial"));
assert("mobile mesh can prefer optional phone asset", MODES.includes("'mesh.mobile.glb'") && MODES.indexOf("'mesh.mobile.glb'") < MODES.indexOf("'mesh.glb'"));
assert("mesh debug reports fallback source", MODES.includes("meshSource: state.meshSource") && MODES.includes("meshFallback: state.meshFallback"));
assert("mesh disables child frustum culling for inside-camera snaps", MODES.includes("o.frustumCulled = false;"));
assert("prewarm can fetch optional mobile mesh first", PREWARM.includes('"mesh.mobile.glb"') && PREWARM.indexOf('"mesh.mobile.glb"') < PREWARM.indexOf('"mesh.glb"'));
assert("prewarm fetches full-quality photo scan before mobile fallback", PREWARM.includes('"apartment.ply"') && PREWARM.includes('"apartment.mobile.ply"') && PREWARM.indexOf('"apartment.ply"') < PREWARM.indexOf('"apartment.mobile.ply"'));
assert("mode loader can parse photo and mesh in the background", MODES.includes("async preload(targets = ['splat', 'mesh'])") && MODES.includes("loadSplat()") && MODES.includes("loadMesh()"));
assert("preloaded photo and mesh remain hidden until selected", MODES.includes("state.splat = splat;") && MODES.includes("state.mesh = grp;") && MODES.includes("visibleFor(state.mode);"));
assert("mode debug reports splat source and count", MODES.includes("splatSource: state.splatSource") && MODES.includes("numSplats: state.splat"));

process.stdout.write("\napartment_zoom_control_contract_test\n");
assert("AptZoomButton component exists", APT.includes("function AptZoomButton"));
assert("zoom controls have an accessible group label", APT.includes('aria-label="apartment zoom controls"'));
assert("zoom in button calls rig zoom in", APT.includes('title="zoom in"') && APT.includes("zoomApartment(1)"));
assert("zoom out button calls rig zoom out", APT.includes('title="zoom out"') && APT.includes("zoomApartment(-1)"));
assert("zoom controls hide during camera pose", APT.includes("const showZoomHud = !editing && !cameraTop"));
assert("debug API exposes zoomIn", APT.includes("zoomIn: () => zoomApartment(1)"));
assert("debug API exposes zoomOut", APT.includes("zoomOut: () => zoomApartment(-1)"));
assert("apartment view quietly preloads photo and mesh after cloud is ready", APT.includes("function aptShouldModePrewarm") && APT.includes('engine.modes.preload(["splat"])') && APT.includes('engine.modes.preload(["mesh"])'));

process.stdout.write("\napartment_camera_snap_contract_test\n");
assert("mobile camera frame helper exists", APT.includes("function aptMobileCameraFrame"));
assert("camera snap stores the target camera immediately", APT.includes("setLiveCam(dev);") && APT.includes("const hasFeed") && APT.includes('setLiveFeedStatus(hasFeed ? "connecting" : "idle")'));
assert("camera snap starts reachable feed before mesh load", APT.includes("setLiveOn(hasFeed);") && APT.indexOf("revealCameraFeedWhenReady(dev, seq)") < APT.indexOf('await eng.modes.setMode("mesh", { duration: 0 });'));
assert("camera snap still loads mesh before fly-to-camera", APT.includes('await eng.modes.setMode("mesh", { duration: 0 });') && APT.includes("eng.flyToDevice(dev"));
assert("camera feed reveal polls until pose is held", APT.includes("const revealCameraFeedWhenReady"));
assert("feed reveal waits for settled camera pose", APT.includes("rig?.cameraPoseSettled?.()") && APT.includes("setCameraPoseReady(true)"));
assert("debug snapshot exposes live feed status", APT.includes("liveFeedStatus,"));
assert("debug snapshot exposes camera frame", APT.includes("cameraFrame: mobile && liveCam"));
assert("debug snapshot exposes render mode diagnostics", APT.includes("modes: engineRef.current?.modes?.debugInfo?.() || null"));
assert("raw camera frame remains visible before warp", APT.includes("opacity: warpedReady ? 0 : 1"));
assert("warped camera frame appears only after first draw", APT.includes("setWarpedReady(true)") && APT.includes('onStatus?.("warped")'));
assert("camera alignment helper distinguishes estimated vs calibrated", APT.includes("function aptCameraAlignment") && APT.includes("camera - estimated pose"));
assert("camera snap requires exact alignment metadata", APT.includes("const calib = alignment.exact"));
assert("camera debug snapshot exposes alignment status", APT.includes("cameraAlignment: liveCam ? aptCameraAlignment(liveCam) : null"));
assert("mobile live feed uses snapshot refresh fallback", APT.includes("function aptSnapshotSrc") && APT.includes("const liveSnapshotIntervalMs = liveSnapshotSrc ? 1100 : 0"));
assert("mobile calibrated feed avoids iOS gray WebGL canvas path", APT.includes("const liveFeedIntrinsics = !mobile") && APT.includes("intrinsics={liveFeedIntrinsics}"));
assert("mobile camera feed prefers HA stream before snapshot fallback", APT.includes("const mobileSnapshotFallbackSrc") && APT.includes("const liveFeedBase = signedLiveFeed.src || mobileSnapshotFallbackSrc || frigateFeedBase") && APT.includes('signedLiveFeed.src ? "ha" : mobileSnapshotFallbackSrc ? "snapshot" : "frigate"'));
assert("snapshot refresh keeps the calibrated frame cache-busted", APT.includes("function aptCacheBust") && APT.includes("aptCacheBust(base, Date.now())"));
assert("snapshot refresh preloads before swapping visible frames", APT.includes("preloadRef") && APT.includes("new Image()") && APT.includes("setFrameSrc(next);"));
assert("snapshot refresh waits for image load before the next frame", APT.includes("publishFrameRef.current?.(Math.max(900, snapshotIntervalMs))") && !APT.includes("setInterval(publish"));
assert("snapshot refresh has a slow-network retry watchdog", APT.includes("loadWatchdogRef") && APT.includes('onStatus?.("retrying")'));
assert("camera feed maps pixels into the shared projection frame", APT.includes('objectFit: "fill"') && APT.includes("const liveFeedSettled = aptLiveFeedSettled(liveFeedStatus);"));
assert("calibrated mobile camera snaps render mesh around uninterrupted video", APT.includes("const cameraSurroundActive") && APT.includes('data-apt-camera-surround={cameraSurroundActive ? "1" : "0"}') && APT.includes("zIndex: 3"));
assert("mobile camera snap passes video sub-rect into calibrated projection", APT.includes("function aptProjectionFrame") && APT.includes("projectionFrame: calib && mobile ? aptProjectionFrame(viewport, dev) : null"));
assert("camera surround waits for held pose before expanding canvas", APT.includes("cameraPoseReady") && APT.includes("liveCameraExact && cameraPoseReady"));
assert("mesh is not drawn as a material overlay on top of video", !APT.includes("setCameraOverlay") && !MODES.includes("cameraOverlayOriginals") && !MODES.includes("wireframe: true"));
assert("3d renderer supports transparent camera compositing", ENGINE.includes("alpha: true") && ENGINE.includes("renderer.setClearColor(0x000000, 0)") && ENGINE.includes("scene.background = null"));

process.stdout.write("\napartment_photo_mobile_asset_contract_test\n");
assert("mobile photo mode prefers full-quality splat before mobile fallback", MODES.includes("'apartment.ply'") && MODES.includes("'apartment.mobile.ply'") && MODES.indexOf("'apartment.ply'") < MODES.indexOf("'apartment.mobile.ply'"));
assert("splat load has a timeout instead of hanging forever", MODES.includes("splat load timeout") && MODES.includes("mobile ? 45000 : 90000"));

process.stdout.write("\napartment_light_control_contract_test\n");
assert("card buttons are real touch targets", CARDS.includes('type="button"') && CARDS.includes("minHeight: mobile ? 38"));
assert("card controls stop canvas gesture propagation", CARDS.includes("const stopCardEvent") && CARDS.includes("onPointerDown={stopCardEvent}") && CARDS.includes("onClick={(e) => { stopCardEvent(e); onClick?.(e); }}"));
assert("apartment service calls support HA callService and raw call", DATA.includes('typeof client.callService === "function"') && DATA.includes('typeof client.call !== "function"'));
assert("apartment service payload uses HA target fields", DATA.includes('const targetKeys = new Set(["entity_id", "area_id", "device_id"])') && DATA.includes("payload.target = target"));
assert("apartment data exposes HA readback helper", DATA.includes("async function readStates") && DATA.includes("readStates, bindStates"));
assert("apartment controls optimistically update selected state", APT.includes("function aptOptimisticServiceState") && APT.includes("setServicePulse((n) => n + 1)") && APT.includes("statesRef.current[entityId] = optimistic"));
assert("apartment controls rollback optimistic state on failure", APT.includes("statesRef.current[entityId] = prev") && APT.includes("didn't respond"));
assert("apartment controls verify state after accepted HA service calls", APT.includes("function aptExpectedServiceState") && APT.includes("window.HomeApartmentData.readStates(client, [entityId])") && APT.includes("is still ${verified.state} in Home Assistant"));
assert("apartment debug API can open a controllable card for button audits", APT.includes("openFirstControllableCard") && APT.includes("setCardId(dev.id)"));

process.stdout.write("\nhome_light_state_query_contract_test\n");
assert("home app detects direct light-state questions", APP.includes("function isDirectLightStateQuestion") && APP.includes("which|what|list|show|tell|are|currently"));
assert("home app answers light-state questions from fresh HA get_states", APP.includes("const answerDirectLightStateQuestion") && APP.includes('client.call({ type: "get_states" })'));
assert("direct light-state answer runs before external/local router", APP.indexOf("await answerDirectLightStateQuestion(text)") > 0
  && APP.indexOf("await answerDirectLightStateQuestion(text)") < APP.indexOf('let route = "local"'));
assert("direct light-state answer includes apartment switch-backed lamps", APP.includes("apartmentSwitchIds") && APP.includes("Lights and switch-backed lamps"));

process.stdout.write("\napartment_calibrated_projection_contract_test\n");
assert("engine reads solved camera center C", ENGINE.includes("const calibratedCenter = Array.isArray(ex?.C)") && ENGINE.includes("new THREE.Vector3(+ex.C[0], +ex.C[1], +ex.C[2])"));
assert("engine builds projection from intrinsics K", ENGINE.includes("function projectionFromIntrinsics") && ENGINE.includes("2 * fx / p.w"));
assert("engine maps calibrated projection into optional video sub-rect", ENGINE.includes("function projectionFrameParams") && ENGINE.includes("scaleX: width / vw") && ENGINE.includes("fovScaleY: vh / height"));
assert("engine passes calibrated projection into rig", ENGINE.includes("projection = projectionFromIntrinsics(intr, fovScale, projectionFrame)") && ENGINE.includes("rig.flyToPose({ position: worldPos, quaternion: worldQuat, fov, dur, projection })"));
assert("rig preserves custom projection while held", RIG.includes("projection: p.projection || null") && RIG.includes("applyCustomProjection(heldPose.projection)"));
assert("rig exposes settled camera pose separately from in-flight pose", RIG.includes("cameraPoseSettled() { return !!heldPose; }"));

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
