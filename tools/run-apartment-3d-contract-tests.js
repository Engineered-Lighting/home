#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const REPO = path.resolve(__dirname, "..");
const APT = fs.readFileSync(path.join(REPO, "app", "src", "home-apartment.jsx"), "utf8");
const CARDS = fs.readFileSync(path.join(REPO, "app", "src", "home-apartment-cards.jsx"), "utf8");
const DATA = fs.readFileSync(path.join(REPO, "app", "src", "home-apartment-data.js"), "utf8");
const AIM = fs.readFileSync(path.join(REPO, "app", "src", "home-apartment-aim.jsx"), "utf8");
const AIM_CORE = fs.readFileSync(path.join(REPO, "app", "src", "home-apartment-aiming.js"), "utf8");
const GIMBAL_TELEMETRY = fs.readFileSync(path.join(REPO, "app", "src", "home-apartment-gimbal-telemetry.js"), "utf8");
const SIM = fs.readFileSync(path.join(REPO, "app", "src", "home-apartment-sim.js"), "utf8");
const INDEX = fs.readFileSync(path.join(REPO, "app", "src", "index.html"), "utf8");
const SW = fs.readFileSync(path.join(REPO, "app", "src", "home-service-worker.js"), "utf8");
const FEATURE_LOADER = fs.readFileSync(path.join(REPO, "app", "src", "home-feature-loader.js"), "utf8");
const BACKEND = fs.readFileSync(path.join(REPO, "ha-config", "extended_openai_conversation", "__init__.py"), "utf8");
const APP = fs.readFileSync(path.join(REPO, "app", "src", "home-app.jsx"), "utf8");
const MODES = fs.readFileSync(path.join(REPO, "app", "src", "home-3d", "modes.js"), "utf8");
const ENGINE = fs.readFileSync(path.join(REPO, "app", "src", "home-3d", "engine.js"), "utf8");
const RIG = fs.readFileSync(path.join(REPO, "app", "src", "home-3d", "rig.js"), "utf8");
const PREWARM = fs.readFileSync(path.join(REPO, "app", "src", "home-apartment-prewarm.js"), "utf8");
const EDIT = fs.readFileSync(path.join(REPO, "app", "src", "home-apartment-edit.jsx"), "utf8");
const MARKERS = fs.readFileSync(path.join(REPO, "app", "src", "home-3d", "markers.js"), "utf8");
const PICKING = fs.readFileSync(path.join(REPO, "app", "src", "home-3d", "picking.js"), "utf8");
const ASSETS = fs.readFileSync(path.join(REPO, "app", "src", "home-3d", "assets.js"), "utf8");
const SEED = JSON.parse(fs.readFileSync(path.join(REPO, "app", "src", "assets", "apartment", "seed-model.json"), "utf8"));

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
assert("mode loader can parse photo, mesh, and collision proxy in the background", MODES.includes("async preload(targets = ['splat', 'mesh', 'collision'])") && MODES.includes("loadSplat()") && MODES.includes("loadMesh()") && MODES.includes("loadCollision()"));
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
assert("camera snap stores the target camera immediately", APT.includes("setLiveCam(dev);") && APT.includes("const hasFeed") && APT.includes("setLiveOn(false);"));
assert("camera snap keeps feed hidden through mesh load and camera flight", APT.includes("setLiveOn(false);") && APT.indexOf("revealCameraFeedWhenReady(dev, seq)") > APT.indexOf('await eng.modes.setMode("mesh", { duration: 0 });'));
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
assert("tracker model is tried before the non-simulation remote-cache fallback", DATA.indexOf("const trackerModel = await fetchTrackerModel();") > 0
  && DATA.indexOf("const trackerModel = await fetchTrackerModel();")
    < DATA.indexOf("localStorage.getItem(REMOTE_CACHE_KEY)", DATA.indexOf("const trackerModel = await fetchTrackerModel();")));
assert("HA model can be enriched from tracker calibration", DATA.includes("function mergeTrackerCameraCalibration")
  && DATA.includes("calibration_enriched: true"));
assert("tracker enrichment is limited to cameras missing calibration", DATA.includes("const camerasNeedCalibration = (model.devices || []).some")
  && DATA.includes("!cameraCalibrationComplete(d)"));

process.stdout.write("\napartment_named_targets_contract_test\n");
assert("apartment model normalizes a targets collection", DATA.includes("function ensureModelShape")
  && DATA.includes("targets: Array.isArray(src.targets) ? src.targets : []"));
assert("edit mode offers table, island, art, custom point, and custom surface targets",
  EDIT.includes('{ category: "table"') && EDIT.includes('{ category: "island"')
    && EDIT.includes('{ category: "art"') && EDIT.includes('label: "custom point"')
    && EDIT.includes('label: "custom surface"'));
assert("target placement uses an exact mesh point and face normal", EDIT.includes("engine.picking.surfaceHit")
  && PICKING.includes("surfaceHit(apartmentRoot, objects, clientX, clientY)"));
assert("surface targets fail closed when the collision mesh is unavailable",
  EDIT.includes('if (!hit && placingTarget.shape !== "point")'));
assert("target overlay renders named point/surface geometry", MARKERS.includes("function buildTarget")
  && MARKERS.includes("makeTagSprite(target.name") && MARKERS.includes("targetPickObjects()"));
assert("target editor exposes direct move, rotate, resize, finish, and cancel controls",
  EDIT.includes('"move on surface"') && EDIT.includes('aria-label="target rotation"')
    && EDIT.includes('label="Finish placement"') && EDIT.includes('label="Cancel"')
    && EDIT.includes('aria-label={`target ${axis}`}'));
assert("target drag preserves surface-plane rotation", EDIT.includes("targetRotationDegrees(target)")
  && EDIT.includes("targetUpForRotation(target.normal || d.last.normal, previousRotation)"));
assert("direct target drag preserves horizontal-table and vertical-art constraints",
  EDIT.includes('target.category === "art" && (!hit || Math.abs(hit.normal[2]) > 0.65)')
    && EDIT.includes('["table", "island"].includes(target.category) && hit && Math.abs(hit.normal[2]) < 0.65'));
assert("edit survey pose remains orbitable and restores the previous view",
  RIG.includes("let editReturn = null") && RIG.includes("clearAbsolutePose();")
    && RIG.includes("goTo({ az: state.az, el: 0, zoom: 0, dur })")
    && RIG.includes("goTo(previous ? { ...previous, dur } : { dur })")
    && RIG.includes("const COMMIT_PX = 24") && EDIT.includes("syncSurveyView"));
assert("target placement distinguishes a click from an orbit drag",
  EDIT.includes("placementGestureRef") && EDIT.includes("Math.hypot(")
    && EDIT.includes("placeTargetAt(e.clientX, e.clientY)")
    && EDIT.includes("engine.rig.dragPreview(dx, dy") && EDIT.includes("engine.rig.dragRelease(dx, dy"));
assert("edit UI provides bottom left and right orbit controls",
  EDIT.includes('aria-label="Apartment edit orbit controls"')
    && EDIT.includes('ariaLabel="Orbit room left"') && EDIT.includes('ariaLabel="Orbit room right"')
    && EDIT.includes("engine?.rig.stepAzimuth(-1)") && EDIT.includes("engine?.rig.stepAzimuth(1)"));
assert("local static preview resolves the real runtime mesh service before web fallback",
  ASSETS.indexOf("if (loopback") > 0
    && ASSETS.indexOf("if (loopback") < ASSETS.indexOf("if (window.HG_WEB_MODE"));
assert("view syncs first-class targets into the 3D overlay", APT.includes("engine.overlay.setTargets?.(model.targets || [])"));
assert("seed contains table, island, and art target categories", ["table", "island", "art"].every((category) =>
  SEED.targets.some((target) => target.category === category)));

process.stdout.write("\napartment_zone_editing_contract_test\n");
assert("zone overlay exposes pickable shaded bodies and draggable vertex handles",
  MARKERS.includes("zonePickObjects()") && MARKERS.includes("zonePart = 'body'")
    && MARKERS.includes("zonePart = 'vertex'") && MARKERS.includes("zoneVertexIndex"));
assert("zone editor distinguishes whole-boundary and individual-corner drags",
  EDIT.includes('kind: vertexIndex == null ? "zone-body" : "zone-vertex"')
    && EDIT.includes('d.kind === "zone-vertex" || d.kind === "zone-body"')
    && EDIT.includes("engine.overlay.previewZone?.(d.id, poly)"));
assert("zone rail explains direct manipulation and offers an explicit new-zone workflow",
  EDIT.includes("Drag a round corner to reshape it") && EDIT.includes("Drag inside the shade to move the whole room")
    && EDIT.includes('label="+ new zone"') && EDIT.includes('label="Finish zone"')
    && EDIT.includes('label="Cancel"'));
assert("zone inspector supports exact corner coordinates and topology changes",
  EDIT.includes("updateZoneVertex") && EDIT.includes('label="add corner"')
    && EDIT.includes('label="remove selected"') && EDIT.includes("selectedZoneVertex"));
assert("new-zone clicks have a visible 3D draft and cannot accidentally drag an existing room",
  MARKERS.includes("function setZoneDraft(poly)") && EDIT.includes("engine.overlay.setZoneDraft")
    && EDIT.indexOf("if (zoneDrawing && fp)") < EDIT.indexOf("const zoneHit = engine.picking.pick"));
assert("zone boundary changes refresh target and device room assignments",
  EDIT.includes("function refreshRoomAssignments(model)")
    && EDIT.includes("for (const item of [...(model.devices || []), ...(model.targets || [])])"));

process.stdout.write("\napartment_live_mapping_contract_test\n");
assert("editor identifies model source, revision, HA connection, and save state",
  EDIT.includes("data-apartment-source") && EDIT.includes("sourceMeta?.label")
    && EDIT.includes("HA {connection") && EDIT.includes('save · {sim ? "simulation cannot save"'));
assert("fixture mapping distinguishes mapped, unplaced, and non-fixture lights",
  EDIT.includes("Mapped fixtures") && EDIT.includes("Unplaced Home Assistant lights")
    && EDIT.includes("Other mapped lights"));
assert("fixture links are deliberate and duplicate placement is blocked",
  EDIT.includes("Home Assistant link") && EDIT.includes("confirm remove link")
    && EDIT.includes("duplicate blocked") && EDIT.includes("position and calibration were preserved"));
assert("fixture identity reconciliation has an explicit geometry-locked workflow",
  EDIT.includes('label="fixture links"') && EDIT.includes('data-apartment-geometry-lock="active"')
    && EDIT.includes('tool === "select" ? { id, kind: "device", moved: false } : null')
    && DATA.includes("function reconcileFixtureEntityLink")
    && DATA.includes("compareSpatialGeometry(model, source)"));
assert("placing a light requires an explicit ceiling-fixture or other-light choice",
  EDIT.includes('label="ceiling fixture"') && EDIT.includes('"other light"')
    && EDIT.includes('placement_kind: "ceiling_fixture"'));
assert("simulation saves are disabled before local draft persistence",
  DATA.indexOf("if (sim)") > 0 && DATA.indexOf("if (sim)") < DATA.indexOf("localStorage.setItem(DRAFT_KEY"));
assert("local drafts require a read-only live comparison before explicit publish",
  DATA.includes("async function getAuthoritativeModel")
    && DATA.includes("function compareApartmentModels")
    && EDIT.includes("data-apartment-live-reconciliation")
    && EDIT.includes("Publish reviewed draft")
    && APT.includes("currentSource.kind === \"local_draft\" && connection === \"online\" && !reviewedDraft"));
assert("save conflicts preserve the displayed local draft and load only a comparison copy",
  APT.includes("local draft preserved; nothing was overwritten")
    && APT.includes("server conflict copy loaded for read-only comparison")
    && !APT.includes("reloaded server copy (your draft is kept locally)"));
assert("Apartment diagnostics expose a read-only model snapshot for verified backups",
  APT.includes("modelSnapshot: () => JSON.parse(JSON.stringify(model))"));

process.stdout.write("\napartment_fixture_tape_contract_test\n");
assert("ceiling lights normalize a fixture-bottom calibration worksheet", DATA.includes("function ensureFixtureCalibration")
  && DATA.includes('aiming_origin: "fixture_bottom"') && DATA.includes("floor_to_bottom_verification_m"));
assert("two perpendicular wall measurements solve fixture x/y", DATA.includes("function reconcileFixturePosition")
  && DATA.includes("wallCoordinate + sign * measurement.distance_m"));
assert("floor/ceiling and fixture drop solve practical aim height", DATA.includes("derived_floor_to_bottom_m")
  && DATA.includes("pos[2] = calibration.derived_floor_to_bottom_m"));
assert("edit UI captures two wall tapes and vertical verification", EDIT.includes("wall_distances[index]")
  && EDIT.includes('label="floor → ceiling"') && EDIT.includes('label="ceiling → fixture bottom"')
  && EDIT.includes('label="floor → fixture bottom" optional'));
assert("fixture measurements are presented as position rather than an unexplained tape tool",
  EDIT.includes('label="fixture position"') && EDIT.includes("These measurements set the light's exact 3D position")
  && EDIT.includes("Two walls set its floor location"));
assert("edit rails sit above the WebGL host and receive pointer input", EDIT.includes("zIndex: 4,")
  && EDIT.includes('data-apt-edit-ui="targets-and-palette"')
  && EDIT.includes('closest?.("[data-apt-edit-ui]")'));
assert("fixture overlay uses a distinct survey color and labels", MARKERS.includes("const SURVEY")
  && MARKERS.includes("function setFixtureCalibration") && MARKERS.includes("floor → ceiling"));
assert("all seeded ceiling lights carry a proposed worksheet", SEED.devices
  .filter((device) => device.type === "light" && device.ha_entity_id.startsWith("light."))
  .every((device) => device.aiming_origin === "fixture_bottom"
    && device.fixture_calibration?.status === "proposed"
    && device.fixture_calibration?.wall_distances?.length === 2));

process.stdout.write("\napartment_engineered_aiming_contract_test\n");
assert("engineered fixture adoption is explicit and lazy", DATA.includes("function adoptEngineeredFixture")
  && AIM.includes("Add Engineered profile") && AIM.includes("position and measurements unchanged")
  && AIM_CORE.includes('ENGINEERED_KIND = "engineered_gimbal_v1"'));
assert("current and Engineered identities share one mount with explicit view modes",
  AIM.includes('data-apt-shared-mount="1"') && AIM.includes('data-apt-profile-view={profileView}')
  && AIM.includes('["auto", "Auto"]') && AIM.includes('["current", "Current"]')
  && AIM.includes('["engineered", "Engineered"]') && AIM.includes('["both", "Both"]')
  && AIM.includes("no second fixture is created") && AIM_CORE.includes("resolveFixtureProfileView"));
assert("offline Engineered view is visibly simulated and renders a dashed preview",
  AIM.includes('renderedState = candidateView.engineered_preview ? "simulated"')
  && AIM.includes("Offline · simulated preview only") && MARKERS.includes("state === 'simulated'")
  && MARKERS.includes("dashedCone"));
assert("ordinary primary fixture links do not imply spotlight roles", AIM.includes("It does not imply a spotlight or radial role")
  && AIM_CORE.includes("explicitFixtureMappings"));
assert("client blocks duplicate engineered entity roles", DATA.includes("validateEngineeredMappings")
  && AIM.includes("duplicate blocked") && AIM_CORE.includes("validateEntityMappings"));
assert("backend validates optional v1 engineered fixture fields", BACKEND.includes("_validate_apartment_engineered_fixtures")
  && BACKEND.includes("exactly six zones") && BACKEND.includes("full collision SHA-256 required"));
assert("simulation has a deterministic engineered fixture and all requested target flows", SIM.includes("dev-sim-engineered-gimbal")
  && SIM.includes("target-sim-coffee-table") && SIM.includes("target-sim-custom-point")
  && SIM.includes("target-sim-dining-table") && SIM.includes("target-sim-kitchen-island")
  && SIM.includes("target-sim-art"));
assert("simulation adapter is isolated from HA", SIM.includes("__SIM_APARTMENT_AIM_RUNTIME")
  && DATA.indexOf("if (sim) return { ok: false, sim: true }") < DATA.indexOf("localStorage.setItem(DRAFT_KEY"));
assert("simulation can clone the saved Apartment layout without persisting adoption", DATA.includes("getSavedLayoutSnapshot")
  && DATA.includes("current-layout.json") && SIM.includes("buildSavedLayoutSimulation")
  && SIM.includes("simulation_overlay: true") && SIM.includes("...device"));

process.stdout.write("\napartment_beam_and_collision_contract_test\n");
assert("hidden collision proxy loads independently from display mesh", MODES.includes("function loadCollision")
  && MODES.includes("Material visibility suppresses rendering") && MODES.includes("side: THREE.DoubleSide")
  && MODES.includes("getCollision()"));
assert("Aim mode exact mesh hits use the collision proxy", APT.includes("engine.modes?.getCollision?.()")
  && APT.includes("surfaceHit(engine.apartmentRoot, [collision]"));
assert("screen picking resets beam-limited raycaster range", PICKING.includes("raycaster.near = 0")
  && PICKING.includes("raycaster.far = Infinity") && PICKING.indexOf("raycaster.far = Infinity") < PICKING.indexOf("raycaster.setFromCamera(ndc, camera)"));
assert("beam renderer distinguishes preview, current, off, stale, and obstruction", MARKERS.includes("OPTICAL_CYAN")
  && MARKERS.includes("kind === 'preview'") && MARKERS.includes("state !== 'off'")
  && MARKERS.includes("state === 'stale'") && MARKERS.includes("spec.obstruction_point"));
assert("beam trace uses first-hit boundary sampling and marks sparse contours partial", PICKING.includes("beamTrace(")
  && PICKING.includes("intersectObjects") && PICKING.includes("kind: points.length >= samples * 0.75 ? 'ellipse' : 'partial'"));
assert("FWHM contour is explicitly separated from qualitative fill", MARKERS.includes("exact half-maximum contour")
  && AIM.includes("FWHM is the half-maximum contour"));
assert("persistent beams use Kelvin-aware volume and dotted off cones", MARKERS.includes("function setAimBeams")
  && MARKERS.includes("function addVolumeCone") && MARKERS.includes("function dashedCone")
  && MARKERS.includes("Math.pow(t - 60") && AIM.includes("engine.overlay.setAimBeams(beamSpecs")
  && APT.includes("const [servicePulse, setServicePulse]") && APT.includes('setAimBeams(specs, "__none__")'));
assert("selected beam volume terminates in the destination surface plane", AIM_CORE.includes("points, hit_fraction: hitFraction")
  && MARKERS.includes("function addVolumeToFootprint") && MARKERS.includes("spec.surface_aligned")
  && AIM.includes("plane_point: destination.pos") && AIM.includes('surface_aligned: previewPlane?.kind === "ellipse"'));
assert("simulation exposes an explicit preview-to-current destination action", AIM.includes("Set simulated aim")
  && AIM.includes("Simulated aim set") && AIM.includes("setSimulatedDestination")
  && SIM.includes("setDestination(fixtureId, destination)") && SIM.includes("current_destination"));
assert("simulation keeps current aim stable until the preview is confirmed",
  SIM.includes("const defaultTarget = targetById.get(fixture.gimbal?.default_target_id)")
  && SIM.includes('source: "simulation_initial_state"')
  && SIM.includes("const initialRaw = solved?.raw_destination")
  && AIM.includes("if (!sim && !runtimeDestination")
  && AIM.includes("currentTarget = runtimeDestination?.pos ? runtimeDestination : defaultTarget"));
assert("confirmed simulation aims animate once and honor reduced motion", MARKERS.includes("function animateAimTransition")
  && MARKERS.includes("prefers-reduced-motion: reduce") && MARKERS.includes("slerpQuaternions")
  && AIM.includes("animateAimTransition") && AIM.includes("Aiming…") && AIM.includes("duration_ms: 880"));
assert("mirror wash and radial 3D segments are explicit simulation interactions", AIM.includes('kind: "mirror"')
  && MARKERS.includes("function addMirrorWash") && MARKERS.includes("aim-radial-zone")
  && APT.includes("aimRadialPickObjects") && SIM.includes("toggleZone(fixtureId, number)"));

process.stdout.write("\napartment_aim_inspector_contract_test\n");
assert("Aim mode has fixture, target, and exact mesh selection", APT.includes('label="aim light"')
  && AIM.includes("choose a named target or click the apartment mesh") && AIM.includes("model.targets"));
assert("clicking an engineered fixture opens its Lighting inspector instead of the generic simulation card",
  APT.includes("clickedDevice?.fixture_kind === window.HomeApartmentAiming?.ENGINEERED_KIND")
  && APT.includes("setAimFixtureId(clickedDevice.id)") && APT.includes("setAimMode(true)"));
assert("inspector exposes raw and calibrated pose with provenance", AIM.includes("raw encoder")
  && AIM.includes("mechanical") && AIM.includes("source ·") && AIM.includes("Product profile"));
assert("inspector exposes tape worksheet fields and practical origin", AIM.includes("floor → ceiling")
  && AIM.includes("ceiling → fixture bottom") && AIM.includes("derived fixture-bottom height")
  && AIM.includes("fixture-bottom origin"));
assert("live Product command and movement are visibly disabled", AIM.includes("copy command")
  && AIM.includes("move light") && AIM.includes("no Home owner-authority delegation"));
assert("mobile Aim controls meet the 44px target and reduced motion is honored", APT.includes("min-height: 44px !important")
  && APT.includes("prefers-reduced-motion"));

process.stdout.write("\napartment_gimbal_telemetry_contract_test\n");
assert("browser adapter makes no loopback request", GIMBAL_TELEMETRY.indexOf('runtimeKind() !== "tauri"')
  < GIMBAL_TELEMETRY.indexOf("tauriFetch(`${BASE}/api/state`"));
assert("Tauri adapter verifies health before starting the read-only poll loop", GIMBAL_TELEMETRY.includes("`${BASE}/healthz`")
  && GIMBAL_TELEMETRY.includes("if (!response.ok) throw new Error(`health HTTP ${response.status}`)")
  && GIMBAL_TELEMETRY.includes("onStatus?.({ state: \"healthy\", read_only: true });\n          poll();")
  && GIMBAL_TELEMETRY.includes('method: "GET"'));
assert("telemetry module exposes no write method", !GIMBAL_TELEMETRY.includes("POST")
  && !GIMBAL_TELEMETRY.includes("/api/group/aim") && GIMBAL_TELEMETRY.includes("read_only: true"));
assert("polling pauses while Apartment is hidden", GIMBAL_TELEMETRY.includes("document.hidden")
  && GIMBAL_TELEMETRY.includes("visibilitychange"));

process.stdout.write("\napartment_ha_radial_control_contract_test\n");
assert("six radial mappings are deliberate and removable", AIM.includes("radial zone ${zone.number}")
  && AIM.includes("Change ${role} link") && AIM.includes("Remove ${role} link"));
assert("HA controls are capability and Kelvin-bound aware", AIM.includes("lightCapabilities")
  && AIM.includes("min_kelvin") && AIM.includes("max_kelvin") && AIM.includes("cap.brightness &&"));
assert("batch writes use one typed call followed by readback", AIM.includes("callService(window.__hav_haClient")
  && AIM.includes("readStates(client, entityIds)") && AIM.includes("entity_id: ids"));
assert("zone identification restores only after safe readback and offers Restore", AIM.includes("automatic restore cancelled")
  && AIM.includes("identify_brightness") && AIM.includes(">Restore</AimButton>"));
assert("copy-to-all reports incompatible and unmapped zones", AIM.includes("zone ${zone.number} incompatible")
  && AIM.includes("zone ${zone.number} unmapped") && AIM.includes(">Match spotlight</AimButton>"));

process.stdout.write("\napartment_aim_cache_contract_test\n");
assert("new aiming modules are ordered before the Apartment view", INDEX.indexOf("['home-apartment-aiming.js', false]")
  < INDEX.indexOf("['home-apartment-data.js', false]") && INDEX.indexOf("['home-apartment-aim.jsx', true]") < INDEX.indexOf("['home-apartment.jsx', true]"));
assert("new aiming modules are deferred feature members", INDEX.includes("'home-apartment-aiming.js': true")
  && INDEX.includes("'home-apartment-aim.jsx': true"));
assert("Apartment lazy-feature loader includes aiming dependencies in order", FEATURE_LOADER.indexOf('["home-apartment-aiming.js", false]')
  < FEATURE_LOADER.indexOf('["home-apartment-data.js", false]')
  && FEATURE_LOADER.indexOf('["home-apartment-aim.jsx", true]') < FEATURE_LOADER.indexOf('["home-apartment.jsx", true]'));
assert("service worker explicitly keeps aiming modules network fresh", SW.includes("FRESH_APARTMENT_MODULES")
  && SW.includes('"/home-apartment-aiming.js"') && SW.includes('"/home-apartment-aim.jsx"')
  && SW.includes('"/home-apartment-sim.js"') && SW.includes('"/home-3d/markers.js"')
  && SW.includes("if (isAppShell(request, url) || isAppCodeModule(url))"));

if (fail) {
  process.stdout.write(`\n${pass} pass . ${fail} fail\n`);
  process.exit(1);
}
process.stdout.write(`\n${pass} pass . 0 fail\n`);
