/* eslint-disable */
/**
 * home-apartment.jsx — the /apartment full-screen 3D spatial command center.
 *
 * P0: white cloud + constrained isometric nav. P2: apartment_model devices/
 * zones, live HA state on markers, hover labels + control cards, edit mode,
 * and the person dot (precise) / room-pulse (room-level) honesty ladder fed
 * by the spatial-tracker WS. P3: photo (splat) + mesh modes.
 *
 * Bridge contract: touches ONLY window.Home3D.ready/.error; live engine is
 * cached on window.__APT_ENGINE for instant warm reopen.
 */

const { useState, useEffect, useRef, useCallback, useMemo } = React;

const APT_FONT_MONO = '"Geist Mono", "JetBrains Mono", monospace';
const APT_FONT_SANS = '"Geist", "Inter", sans-serif';
const APT_WORLD_ENTRY_READINESS_BUDGET_MS = 500;
const APT_HANDOFF_ENDPOINT_BUDGET_MS = 4000;
const APT_WORLD_ENTRY_PROVIDER_ACTIVATION_BUDGET_MS = 120;
const APT_WORLD_ENTRY_COMPOSITOR_RESERVE_MS = 180;
/* The 500ms provider deadline reserves time for the separate endpoint gate, not the provider's cold root
 * load. Once the world has committed, a cancellable background adoption may
 * use the same 12s ceiling as the renderer's hidden tileset warmer. */
const APT_WORLD_COMMITTED_PROVIDER_READINESS_BUDGET_MS = 12_000;
const APT_WORLD_TRAVEL_READINESS_BUDGET_MS = 500;
const APT_WORLD_INTERACTION_HEIGHT_BUDGET_MS = 1600;
const APT_HANDOFF_CAPTURE_CLEANUP_RESERVE_MS = 24;
const APT_WORLD_PIN_IDLE_CADENCE_MS = 250;
const APT_WORLD_PIN_INTERACTION_TAIL_MS = 180;
const EMPTY_APARTMENT_SEMANTIC_NAVIGATION = Object.freeze({
  rooms: Object.freeze([]),
  devices: Object.freeze([]),
  statusText: "No apartment devices available.",
});

const APT_SERVICE_BY_RUNTIME_KEY = {
  HG_DEFAULT_HA_BASE: "ha",
  HG_DEFAULT_FRIGATE_BASE: "frigate",
  HG_DEFAULT_TRACKER_BASE: "tracker",
  HG_DEFAULT_APARTMENT_ASSET_BASE: "apartmentAssets",
};
const APT_CAMERA_STREAM_REFRESH_MS = 2 * 60 * 1000;

function readAptViewport() {
  if (typeof window === "undefined") return { width: 1024, height: 768, mobile: false, phone: false };
  const vv = window.visualViewport;
  const width = Math.round(vv?.width || window.innerWidth || 1024);
  const height = Math.round(vv?.height || window.innerHeight || 768);
  return { width, height, mobile: width < 700, phone: width < 480 };
}

function useAptViewport() {
  const [viewport, setViewport] = useState(readAptViewport);
  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    let raf = 0;
    const update = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => setViewport(readAptViewport()));
    };
    window.addEventListener("resize", update);
    window.visualViewport?.addEventListener("resize", update);
    window.visualViewport?.addEventListener("scroll", update);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", update);
      window.visualViewport?.removeEventListener("resize", update);
      window.visualViewport?.removeEventListener("scroll", update);
    };
  }, []);
  return viewport;
}

function aptServiceBase(storageKey, runtimeKey, fallback) {
  try {
    const service = APT_SERVICE_BY_RUNTIME_KEY[runtimeKey];
    const resolved = service && window.HomeServices?.get?.(service);
    if (resolved) return String(resolved).replace(/\/+$/, "");
  } catch (e) { /* */ }
  try {
    if (window.HG_WEB_MODE && window[runtimeKey]) {
      return String(window[runtimeKey]).replace(/\/+$/, "");
    }
  } catch (e) { /* */ }
  try {
    if (!window.HomeServices) {
      const saved = localStorage.getItem(storageKey);
      if (saved) return saved.replace(/\/+$/, "");
    }
  } catch (e) { /* */ }
  try {
    if (window[runtimeKey]) return String(window[runtimeKey]).replace(/\/+$/, "");
  } catch (e) { /* */ }
  return fallback;
}

/* Outward world zoom is opt-in and default off, so the Apartment tab behaves
 * exactly as it does today until someone asks for it. Precedence follows the
 * repo's existing flags: URL parameter, then localStorage, then off. The registry
 * owns the read; this wrapper only tolerates the registry not being loaded yet. */
function aptWorldZoomEnabled() {
  if (window.HomeMapRehearsalOfflineState?.active) return false;
  try {
    return window.HomeSpatialViewport?.isWorldZoomEnabled?.() === true;
  } catch (e) {
    return false;
  }
}

/* Device/HA simulation and spatial geography are independent. Product preview
 * commonly uses simulated entities while still navigating the registered Echo
 * Park/Victoria sites. Kansas is retained only for explicit synthetic-spatial
 * QA, never inferred from a simulated Apartment model. */
function aptSyntheticSpatialQaEnabled() {
  try {
    if (window.__APT_SYNTHETIC_SPATIAL_QA === true) return true;
    return new URLSearchParams(window.location?.search || "")
      .get("apartmentSpatialQa") === "synthetic";
  } catch (_) {
    return false;
  }
}

function aptUseSyntheticSpatialQa(simActive, qaEnabled) {
  return simActive === true && qaEnabled === true;
}

/* SPEC Layout > Fitting: the room toolbar floats over the bottom of the
 * canvas, so the renderer fits the room above it. Bottom inset in CSS pixels
 * from the host's bottom edge to just above the toolbar, or 0 without one. */
function aptRoomToolbarInsets(host) {
  const toolbar = host?.parentElement?.querySelector?.(".apt-ui-room-toolbar");
  if (!host || !toolbar) return { top: 0, right: 0, bottom: 0, left: 0 };
  const box = host.getBoundingClientRect(), bar = toolbar.getBoundingClientRect();
  const visible = bar.width > 0 && bar.height > 0 && getComputedStyle(toolbar).visibility !== "hidden"
    && bar.right > box.left && bar.left < box.right && bar.bottom > box.top && bar.top < box.bottom;
  return { top: 0, right: 0, bottom: visible ? Math.max(0, Math.round(box.bottom - bar.top + 8)) : 0, left: 0 };
}

function aptZoomTransitionDisabled(phase, targetDirection, requestedDirection) {
  return phase !== "idle" && targetDirection === requestedDirection;
}

function aptTravelBlocksZoomInput(travelling, source) {
  const ownsJourney = typeof source === "string"
    && (source.startsWith("travel-out-") || source.startsWith("travel-in-"));
  return travelling === true && !ownsJourney;
}

function aptWorldPinCadence({ visible, documentVisible, motionActive } = {}) {
  if (visible !== true || documentVisible === false) return "stopped";
  return motionActive === true ? "frame" : "idle";
}

/*
 * Which projected home is the one the camera is framed on.
 *
 * Outside a flight the world is always centred on the selected home, so its
 * label sits exactly under a cursor resting on the map. As a 44 px button it
 * took the wheel notch meant for the map (the 2026-09-18 smoke: a notch over
 * the centred Echo Park pin at 951 km "did NOT zoom"), and clicking it did
 * nothing: travelToSite refuses the selected site unless a flight is under
 * way. So it is drawn as a marker, not a control, and hit-testing passes
 * through it to the world. During a flight the origin keeps its button,
 * because selecting it then is the one way to turn back.
 *
 * Keyed to the selection, not to whichever label happens to sit mid-screen.
 * The one state where the two differ is a flight held on arrival ("the
 * destination view is not ready"): the camera is on the destination and the
 * registry, and "Return to interior", are still on the origin. Marking the
 * centred destination current there would leave "Travel to <origin>" as a
 * button travelToSite refuses (it is the selected site and nothing is
 * flying), and take away the one label that works: the destination's, which
 * runs the journey again like the card's Retry.
 */
function aptWorldPinIsCurrent(siteId, selectedSiteId, travelling) {
  return travelling !== true && siteId != null && siteId === selectedSiteId;
}

/* Whether the world owns pointer input: the same test its host's
 * pointer-events uses. Overlays forward the wheel to it only then, so a wheel
 * over a recovery button can never zoom a world that is not on screen. */
function aptWorldHostTakesInput(spatialScale, spatialTransition) {
  return !(spatialScale === "apartment" && spatialTransition === "idle");
}

/*
 * Whether an AI still is actually on screen in this frame.
 *
 * Read from cameraState(), which reports what the hero pass paints and how
 * thick the cloud over it is -- the same numbers the shader draws from, so the
 * disclosure cannot describe a frame nobody saw. hero is null beyond the swap
 * (region and planet ranges); 0.995 is the coverage the surface itself calls
 * opaque, and under it nothing of the still shows.
 */
function aptHeroStillOnScreen(camera) {
  const opacity = Number(camera?.hero?.opacity);
  if (!(opacity > 0.01)) return false;
  return !(Number(camera?.cloud?.coverage) >= 0.995);
}

/*
 * "Research imagery" is shown whenever an AI still is on screen, the handoff
 * included, and never inside the interior.
 *
 * Showing is immediate: an honest disclosure may not trail the picture it
 * discloses. Hiding waits 600 ms, because the still band has sub-second opaque
 * moments (a gap cover measured 0.07-0.2 s, a descent pass) and a label that
 * blinks with each of them reads as a fault, not as information.
 */
function aptResearchImageryShown({ interior, stillOnScreen, lastOnScreenAtMs, nowMs } = {}) {
  if (interior === true) return false;
  if (stillOnScreen === true) return true;
  return Number.isFinite(lastOnScreenAtMs) && Number.isFinite(nowMs)
    && nowMs - lastOnScreenAtMs < 600;
}

/*
 * Whether the world host is painted at all this frame.
 *
 * At the Apartment scale React holds the host at opacity 0, and the only
 * writers of a visible opacity are the handoff compositor and the
 * reduced-motion black cut, frame by frame, once the handoff's clock runs.
 * Preparation comes first -- about 250 ms warm (the S3 freeze), up to the 4 s
 * endpoint budget cold -- and throughout it the surface already reports its
 * parked still at full opacity while the screen shows the interior. So the
 * inline opacity, which is what the compositor wrote, decides; a host without
 * one paints at the default 1.
 */
function aptWorldHostPainted(opacityStyle) {
  if (opacityStyle == null || opacityStyle === "") return true;
  const opacity = Number.parseFloat(opacityStyle);
  return !Number.isFinite(opacity) || opacity > 0.01;
}

/*
 * How often the disclosure reads the frame.
 *
 * Per frame while the view moves, and also while a still is in the stack but
 * not visible -- under opaque cloud, a gap cover, or a host the handoff has
 * not painted yet -- because then it can surface on any frame with no input
 * at all (a held cloud clearing, a gap cover closing, the compositor's clock
 * starting), and the label may not trail the picture. Otherwise the pins'
 * idle cadence: with no still in the stack only camera motion can bring one,
 * and every input that moves the camera wakes the poll first; with the still
 * disclosed, a hide one idle poll late is inside the 600 ms grace anyway.
 */
function aptResearchImageryCadence({ interior, documentVisible, moving, stillInStack, stillOnScreen } = {}) {
  if (interior === true || documentVisible === false) return "stopped";
  if (moving === true) return "frame";
  return stillInStack === true && stillOnScreen !== true ? "frame" : "idle";
}

/* A renderer that will not paint again: the recovery card blacks out the
 * whole view behind it, and the credits it lives in are lifted over every
 * other layer, the overview banner included, for as long as that lasts. */
function aptRecoveryBlackout(recovery) {
  return recovery?.kind === "render-error" || recovery?.kind === "context-lost";
}

function aptTravelArrivalScale(flight) {
  if (flight?.status !== "complete") return null;
  if (flight.arrivalBand === "parcel" && flight.heightResolved === true) return "parcel";
  if (flight.arrivalBand === "unresolved" && flight.heightResolved === false) return "city";
  return null;
}

function aptSelectedApartmentSite(sites, selectedSiteId, worldZoomEnabled) {
  const entries = Array.isArray(sites) ? sites : [];
  return entries.find((entry) => entry?.id === selectedSiteId) || null;
}

function aptInteriorWorkspaceScope(siteId, authorityKey = "") {
  const site = window.HomeSpatialViewport?.getSite?.(siteId);
  const revision = site?.contentRevision || null;
  const pack = window.HomeContentPacks?.get?.(siteId, revision || undefined);
  return { siteId, contentRevision: revision,
    registrationVersion: site?.sceneRegistration?.version ?? 0,
    frameDigest: pack?.manifest?.assets?.["frame.json"]?.sha256 || null, authorityKey };
}

function aptConfirmedWorldAnchor(syntheticSpatialQa = false) {
  if (syntheticSpatialQa) {
    return Object.freeze({
      latitude: 39,
      longitude: -98,
      altitudeMeters: 0,
      siteId: "synthetic",
      heightProvenance: "unresolved",
      source: "synthetic-simulation",
      confirmed: true,
      synthetic: true,
    });
  }
  try {
    const candidate = window.HomeSpatialViewport?.getSelectedAnchor?.();
    const latitude = Number(candidate?.latitude);
    const longitude = Number(candidate?.longitude);
    const altitudeMeters = Number(candidate?.altitudeMeters || 0);
    if (
      candidate?.confirmed !== true
      || !Number.isFinite(latitude)
      || !Number.isFinite(longitude)
      || latitude < -90 || latitude > 90
      || longitude < -180 || longitude > 180
      || !Number.isFinite(altitudeMeters)
    ) return null;
    const azimuth = candidate?.localXAxisAzimuthDeg;
    const viewHeading = candidate?.parcelViewHeadingDeg;
    const parcelRangeM = Number(candidate?.parcelRangeM);
    return Object.freeze({
      latitude,
      longitude,
      altitudeMeters,
      siteId: typeof candidate?.siteId === "string" ? candidate.siteId : undefined,
      heightProvenance: candidate?.heightProvenance === "public-elevation"
        ? "public-elevation"
        : (candidate?.heightProvenance === "fixed" ? "fixed" : "unresolved"),
      /*
       * The registration travels with the anchor.
       *
       * This function narrows the registry record to what the surface needs,
       * and for a while that narrowing silently dropped the one yaw that maps
       * the scan's frame to true north. The pose derivation then had nothing to
       * work from and fell back to the band preset, so the world arrived
       * near top-down instead of facing the way the Apartment was facing --
       * with no error anywhere, because a missing optional field is not a
       * failure, it is just a different branch.
       */
      localXAxisAzimuthDeg: Number.isFinite(azimuth) ? azimuth : null,
      /*
       * And the chosen sight line for a home that has no scan to register.
       * Narrowed here for the same reason as the yaw above: a field the
       * registry sets and this function forgets is a silent different branch,
       * not an error.
       */
      parcelViewHeadingDeg: Number.isFinite(viewHeading) ? viewHeading : null,
      heroStills: Array.isArray(candidate?.heroStills) ? candidate.heroStills : [],
      contentAttributions: Array.isArray(candidate?.contentAttributions) ? candidate.contentAttributions : [],
      /* Whether there is an interior to arrive in. Without this the traversal
       * cannot tell a home you can walk into from one you can only look at,
       * and would play a collapse into an interior that does not exist. */
      sceneAvailable: candidate?.sceneAvailable === true,
      contentRevision: candidate?.contentRevision ?? null,
      parcelRangeM: Number.isFinite(parcelRangeM) && parcelRangeM > 0 ? parcelRangeM : 180,
      registrationVersion: Number(candidate?.registrationVersion) || 0,
      sampleWidthM: Number(candidate?.sampleWidthM) || 2,
      source: String(candidate.source || "confirmed-home-site"),
      confirmed: true,
      synthetic: false,
    });
  } catch (e) {
    return null;
  }
}

function aptSyncDollhouseContext(engine, { liveOn = false, liveCam = null, calibCam = null } = {}) {
  const context = { live: !!(liveOn || liveCam), calibrating: !!calibCam };
  engine?.setDollhouseContext?.(context);
  return context;
}

function aptBeginDollhouseCalibration(engine) {
  // Calibration unlocks orbit: fence display cuts before the held-pose gate clears.
  aptSyncDollhouseContext(engine, { liveCam: true, calibCam: true });
  engine?.rig?.returnToOverview?.();
}

/* The surface cache key is shared by background preparation and committed
 * entry. If both paths spell this independently, normal prepare() can erase a
 * warm target simply because its string differs. */
function aptWorldAnchorKey(anchor, dollhouseRegistration = null) {
  if (!anchor) return "";
  const dollhouseRevision = Number(dollhouseRegistration?.registrationRevision) || 0;
  return `${anchor.latitude}:${anchor.longitude}:${anchor.altitudeMeters || 0}:${anchor.registrationVersion || 0}:${dollhouseRevision}:${anchor.contentRevision || "legacy"}`;
}

/* A non-identifying revision for sharing/superseding prewarm work. Coordinates
 * stay in the private target object; the controller retains only this stable
 * site/registration identity in its diagnostics-safe bookkeeping. */
function aptWorldEntryRevision(anchor, dollhouseRegistration = null) {
  if (!anchor || anchor.confirmed !== true) return null;
  const siteId = String(anchor.siteId || dollhouseRegistration?.siteId || "").trim();
  if (!siteId) return null;
  const anchorRevision = Number(anchor.registrationVersion) || 0;
  const dollhouseRevision = Number(dollhouseRegistration?.registrationRevision) || 0;
  return `${siteId}:anchor-r${anchorRevision}:dollhouse-r${dollhouseRevision}`
    + (anchor.contentRevision ? `:content-${anchor.contentRevision}` : "");
}

/* Apartment-owned speculative readiness may briefly run the bundled local
 * surface so a cold Cesium canvas/model can finish mounting. It must never wake a
 * retained paid tileset: a staged candidate preloads when hidden, and an active
 * candidate would resume its complete render/streaming loop behind Apartment.
 * Fail closed for an unknown snapshot so future providers cannot accidentally
 * inherit this local-only exception. */
function aptStartLocalWorldPrewarmFrames(surface) {
  let snapshot = null;
  try { snapshot = surface?.snapshot?.() ?? null; } catch (_) { return false; }
  if (!snapshot || snapshot.running === true
      || snapshot.provider !== "bundled-natural-earth"
      || snapshot.providerCandidate !== null
      || typeof surface?.setRunning !== "function") return false;
  surface.setRunning(true);
  return true;
}

function aptApartmentOwnsSpatialSurface(coordinator) {
  let state = null;
  try { state = coordinator?.getState?.() ?? null; } catch (_) { return false; }
  return !state || (state.committedScale === "apartment" && state.phase === "idle");
}

/* Local prewarm cannot activate the metered provider. Natural Earth's honest
 * ellipsoid responses therefore normalize to "skipped" for both height stages,
 * while an optional registered GLB can independently fail or time out. Neither
 * outcome makes an already-mounted local surface/target cold. The committed
 * intent owns provider refinement and explicitly retries the dollhouse reveal
 * gate. Keep this decision pure so the controller's real partial result can be
 * exercised by the deterministic seam harness. */
function aptWorldPrewarmCanUseRichEntry(prewarmJoin) {
  const stages = prewarmJoin?.stages || {};
  return ["surface", "target"].every(
    (name) => stages[name] === "ready",
  );
}

function aptWorldPrewarmNeedsDollhouseRetry(prewarmJoin, hasDollhouseRegistration = false) {
  if (hasDollhouseRegistration !== true) return false;
  const status = prewarmJoin?.stages?.dollhouseAssets;
  return status !== "ready";
}

/* Paid imagery is an optional refinement, never a prerequisite for the full
 * reversible handoff. Only local destination and compositor/capture failures
 * select committed endpoint preparation. This deliberately ignores provider state. */
function aptWorldEntryRequiresColdCut({
  prewarmReady = true,
  navigationReady = true,
  compositorReady = true,
} = {}) {
  return prewarmReady !== true || navigationReady !== true || compositorReady !== true;
}

/*
 * What the viewport is actually showing.
 *
 * This used to be a fixed string per scale band, which meant it asserted
 * "bundled Natural Earth" at country range while live photorealistic tiles were
 * on screen. A disclosure line that names the wrong provider is worse than no
 * line at all: its whole job is to say what you are looking at, and it was
 * confidently wrong at exactly the ranges where a user is most likely to wonder.
 *
 * It now reads the surface's own snapshot. `provider` names the selected
 * renderer source, while `naturalEarthGlobeVisible` distinguishes an overview
 * that is actually painted from the intentional black local field.
 */
function aptSpatialAvailability(scale, snapshot) {
  if (scale === "apartment") return "Apartment mesh or splat";
  const provider = snapshot?.provider ?? null;
  const terrain = snapshot?.terrain === "ellipsoid" ? "ellipsoid terrain" : "terrain";
  if (provider === "google-photorealistic-3d") {
    const state = snapshot?.availability === "DEGRADED" ? "degraded; " : "";
    return scale === "parcel"
      ? `${state}confirmed home marker; photorealistic 3D mesh`
      : `${state}confirmed home marker; photorealistic 3D mesh; ${terrain}`;
  }
  if (provider === "bundled-natural-earth") {
    /* Natural Earth level 2 is an honest country/planet overview, but it has
     * no parcel, building, street, or city-scale detail. The semantic camera
     * may still be in those bands so the disclosure must state the missing
     * fidelity instead of letting the scale label imply that it is present. */
    if ((scale === "parcel" || scale === "city")
        && snapshot?.localDetailAvailability !== "LIVE") {
      if (snapshot?.naturalEarthGlobeVisible === false) {
        return `local detail unavailable; bundled Natural Earth overview begins at country scale; ${terrain}`;
      }
      return `local detail unavailable; fallback: bundled Natural Earth overview only; ${terrain}`;
    }
    return `fallback: bundled Natural Earth overview; ${terrain}`;
  }
  /* No surface yet, or a provider this build does not know how to name. Saying
   * so is honest; guessing a provider is the defect being fixed. */
  return "imagery unavailable";
}

function aptCameraAspect(dev) {
  const size = dev?.camera?.intrinsics?.image_size;
  if (Array.isArray(size) && +size[0] > 0 && +size[1] > 0) return +size[0] / +size[1];
  const aspect = +dev?.camera?.aspect;
  if (aspect > 0.2 && aspect < 5) return aspect;
  return 16 / 9;
}

function aptMobileCameraFrame(viewport, dev) {
  const width = Math.max(1, viewport?.width || window.innerWidth || 390);
  const height = Math.max(1, viewport?.height || window.innerHeight || 844);
  const aspect = aptCameraAspect(dev);
  const topReserve = Math.min(128, Math.max(96, Math.round(height * 0.14)));
  const bottomReserve = Math.min(112, Math.max(78, Math.round(height * 0.12)));
  const maxW = Math.max(1, width);
  const maxH = Math.max(1, height - topReserve - bottomReserve);
  let frameW = maxW;
  let frameH = frameW / aspect;
  if (frameH > maxH) {
    frameH = maxH;
    frameW = frameH * aspect;
  }
  const left = Math.max(0, Math.round((width - frameW) / 2));
  const top = Math.round(topReserve + Math.max(0, (maxH - frameH) / 2));
  return {
    position: "absolute",
    left,
    top,
    width: Math.round(frameW),
    height: Math.round(frameH),
  };
}

function aptProjectionFrame(viewport, dev) {
  const frame = aptMobileCameraFrame(viewport, dev);
  const viewportHeight = Math.max(1, viewport?.height || window.innerHeight || 1);
  return {
    viewportWidth: Math.max(1, viewport?.width || window.innerWidth || 1),
    viewportHeight,
    left: frame.left,
    top: frame.top,
    width: frame.width,
    height: frame.height,
  };
}

function aptCameraAlignment(dev) {
  const intr = dev?.camera?.intrinsics;
  const extr = dev?.camera?.extrinsics;
  const intrOk = !!(intr?.K && Array.isArray(intr.image_size));
  const extrOk = !!(extr?.q_wxyz && Array.isArray(extr.C));
  if (intrOk && extrOk) {
    const rms = Number.isFinite(+extr.rms_px) ? ` · ${(+extr.rms_px).toFixed(1)}px rms` : "";
    return { exact: true, short: "camera - calibrated view", detail: `calibrated view${rms}` };
  }
  const missing = [intrOk ? "" : "lens", extrOk ? "" : "pose"].filter(Boolean).join(" + ");
  return {
    exact: false,
    short: "camera - estimated pose",
    detail: `estimated alignment - missing ${missing || "calibration"}`,
  };
}

/* A camera view is a room pose. Victoria offers none until Den supplies a
 * calibrated camera pose; room or socket positions never stand in for one. */
const APT_DEN_CAMERA_POSE_REASON = "Needs a Den-calibrated camera pose. Room and socket positions are never used as a camera pose.";
function aptCameraViewAvailability(siteId, devices) {
  if (siteId === "victoria") return { available: false, reason: APT_DEN_CAMERA_POSE_REASON };
  return (devices || []).some((d) => d?.type === "camera" || !!d?.camera)
    ? { available: true, reason: null } : { available: false, reason: "No camera is placed in this home's model." };
}

function aptSnapshotSrc(src) {
  const clean = String(src || "").replace(/\/+$/, "");
  if (!clean || /\/latest\.jpg(?:[?#]|$)/.test(clean)) return clean;
  return `${clean}/latest.jpg`;
}

function aptHaBaseFromWs(haUrl) {
  if (!haUrl) return "";
  if (String(haUrl).startsWith("/")) return String(haUrl).replace(/\/+$/, "");
  try {
    const u = new URL(String(haUrl).replace(/^ws/, "http"));
    return `${u.protocol}//${u.host}`;
  } catch {
    return "";
  }
}

function aptCameraEntity(dev) {
  return dev?.ha_entity_id || dev?.camera?.ha_entity_id || "";
}

function aptCameraCanFeed(dev) {
  return !!(aptCameraEntity(dev) || dev?.camera?.frigate_name);
}

function useAptSignedCameraStream({ entity, haUrl, enabled }) {
  const [signed, setSigned] = useState("");
  const [err, setErr] = useState("");
  const [streamKey, setStreamKey] = useState(0);
  const reload = useCallback(() => setStreamKey((n) => n + 1), []);

  useEffect(() => {
    if (!enabled || !entity || !haUrl) {
      setSigned("");
      setErr("");
      return undefined;
    }
    let cancelled = false;
    let resignTimer = 0;
    let refreshTimer = 0;
    setSigned("");
    setErr("");

    const sign = async () => {
      try {
        const client = window.__hav_haClient || null;
        if (!client || typeof client.call !== "function") {
          setErr("no ha client");
          return;
        }
        const res = await client.call({
          type: "auth/sign_path",
          path: `/api/camera_proxy_stream/${entity}`,
          expires: 3600,
        });
        if (cancelled) return;
        const base = aptHaBaseFromWs(haUrl);
        if (!base || !res?.path) throw new Error("missing signed stream path");
        setSigned(`${base}${res.path}`);
        setErr("");
        resignTimer = setTimeout(sign, 50 * 60 * 1000);
      } catch (e) {
        if (cancelled) return;
        console.warn("[apartment] HA camera sign_path failed:", e?.message || e);
        setErr(String(e?.message || e));
      }
    };

    sign();
    refreshTimer = setInterval(reload, APT_CAMERA_STREAM_REFRESH_MS);
    return () => {
      cancelled = true;
      if (resignTimer) clearTimeout(resignTimer);
      if (refreshTimer) clearInterval(refreshTimer);
    };
  }, [entity, haUrl, enabled, reload]);

  useEffect(() => {
    if (!enabled) return undefined;
    let lastReloadAt = 0;
    const forceReload = () => {
      const now = Date.now();
      if (now - lastReloadAt < 10000) return;
      lastReloadAt = now;
      reload();
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") forceReload();
    };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", forceReload);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", forceReload);
    };
  }, [enabled, reload]);

  return { src: signed, err, streamKey, reload };
}

function aptCacheBust(url, value) {
  if (!url) return url;
  return `${url}${url.includes("?") ? "&" : "?"}_=${encodeURIComponent(value)}`;
}

function aptInitialFrameSrc(src, snapshotSrc, snapshotIntervalMs) {
  return snapshotSrc && snapshotIntervalMs > 0
    ? ""
    : src;
}

function aptLiveFeedSettled(status) {
  return ["idle", "raw", "frame", "warped"].includes(status);
}

function aptOptimisticServiceState(prev, domain, service) {
  if (!prev || !["light", "switch", "media_player"].includes(domain)) return null;
  let nextState = prev.state;
  if (service === "turn_on") nextState = domain === "media_player" ? "playing" : "on";
  else if (service === "turn_off") nextState = "off";
  else if (service === "toggle") nextState = prev.state === "on" ? "off" : "on";
  else if (service === "media_play_pause") nextState = prev.state === "playing" ? "paused" : "playing";
  else return null;
  return { ...prev, state: nextState };
}

function aptExpectedServiceState(domain, service, optimistic) {
  if (optimistic?.state) return optimistic.state;
  if (service === "turn_on") return domain === "media_player" ? "playing" : "on";
  if (service === "turn_off") return "off";
  return null;
}

function aptStateMatchesExpected(state, expected) {
  if (!expected) return true;
  return state?.state === expected;
}

function aptDelay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/*
 * Sampled fresh, never cached.
 *
 * This preference used to be read exactly once, when the spatial coordinator
 * was constructed, and the coordinator's own setReducedMotion was never called
 * afterwards — so a user who turned Reduce Motion on mid-session kept getting
 * the full 650ms handoff until the Apartment view was torn down. rig.js already
 * re-queries on every use; this follows rig.js.
 */
/*
 * The world-view selection survives a reload.
 *
 * Discovery means comparing options over a session, and losing the choice on
 * every refresh makes that impossible. Reads defensively: this value outlives
 * the build that wrote it, and an unknown id is normalised by the renderer's own
 * module rather than trusted here.
 */
/*
 * Where the home markers start being useful.
 *
 * Below this the selected home fills the frame and a dot on top of it is
 * noise, while the other home is thousands of kilometres off screen. From
 * here out both are in shot and the pair is the point.
 */
const WORLD_PIN_MIN_RANGE_M = 900_000;

const APT_WORLD_VIEW_KEY = "apartment3d.worldView";

function aptReadWorldView() {
  try {
    const raw = window.localStorage.getItem(APT_WORLD_VIEW_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    if (!Object.hasOwn(parsed, "dollhouse")) return parsed;

    /* The parcel cutaway experiment was removed. Delete its persisted choice
     * as well as ignoring it so a later option cannot accidentally resurrect
     * the retired behavior under the same key. */
    const cleaned = { ...parsed };
    delete cleaned.dollhouse;
    aptWriteWorldView(cleaned);
    return cleaned;
  } catch (error) {
    return {};
  }
}

function aptWriteWorldView(selection) {
  try {
    window.localStorage.setItem(APT_WORLD_VIEW_KEY, JSON.stringify(selection || {}));
    return true;
  } catch (error) {
    return false;
  }
}

function aptPrefersReducedMotion() {
  try {
    return window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true;
  } catch (_) {
    return false;
  }
}

function aptAbortableDelay(ms, signal) {
  if (!ms) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(done, ms);
    function done() {
      signal?.removeEventListener("abort", aborted);
      resolve();
    }
    function aborted() {
      clearTimeout(timer);
      const error = new Error("spatial viewport transition cancelled");
      error.name = "AbortError";
      reject(error);
    }
    if (signal?.aborted) aborted();
    else signal?.addEventListener("abort", aborted, { once: true });
  });
}

/* Bound a readiness operation without pretending that a Promise timeout can
 * cancel work already handed to a renderer/provider. The operation remains
 * observed after timeout; its own generation/abort ownership decides whether
 * a late result may still attach. */
function aptSettleWithin(task, timeoutMs, signal) {
  const budgetMs = Math.max(0, Number(timeoutMs) || 0);
  return new Promise((resolve, reject) => {
    let settled = false;
    let timer = null;
    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      if (timer !== null) clearTimeout(timer);
      signal?.removeEventListener?.("abort", onAbort);
      callback(value);
    };
    const onAbort = () => {
      const error = new Error("spatial viewport transition cancelled");
      error.name = "AbortError";
      finish(reject, error);
    };
    if (signal?.aborted) { onAbort(); return; }
    signal?.addEventListener?.("abort", onAbort, { once: true });
    timer = setTimeout(() => finish(resolve, { status: "timed-out", value: null }), budgetMs);
    Promise.resolve(task).then(
      (value) => finish(resolve, { status: "settled", value }),
      (error) => finish(reject, error),
    );
  });
}

/* A staged provider may become visible only while the coordinator intent that
 * asked for it still owns preparation for the same renderer/site revision.
 * Keep the predicate pure: the seam harness exercises every invalidation axis
 * without needing a live Cesium context. */
function aptWorldPromotionAuthority({
  context,
  coordinatorState,
  expectedLifecycleRevision,
  currentLifecycleRevision,
  targetRevision,
  currentTargetRevision,
  expectedSurface,
  currentSurface,
} = {}) {
  return Boolean(
    context
    && !context.signal?.aborted
    && coordinatorState?.phase === "preparing"
    && coordinatorState.latestIntentId === context.intentId
    && coordinatorState.latestIntentRevision === context.intentRevision
    && coordinatorState.transition?.intentId === context.intentId
    && coordinatorState.transition?.intentRevision === context.intentRevision
    && expectedLifecycleRevision === currentLifecycleRevision
    && targetRevision
    && targetRevision === currentTargetRevision
    && expectedSurface
    && expectedSurface === currentSurface
  );
}

/* Turn an already-created hidden provider into the visible surface only inside
 * a bounded, host-owned lifecycle transaction. Provider/tile callbacks never
 * call this function: a late root request may stage and prewarm, but a current
 * coordinator intent must explicitly prove quiet imagery and then revalidate
 * authority immediately before the synchronous promotion. */
function aptTryPromoteStagedWorldImagery({
  activation,
  signal,
  timeoutMs,
  settleWithin,
  waitForImagery,
  mayPromote,
  promote,
} = {}) {
  if (activation?.status === "ready") return Promise.resolve(activation);
  if (activation?.status !== "staged") {
    return Promise.resolve({ status: "skipped" });
  }
  if (typeof mayPromote !== "function" || !mayPromote()) {
    return Promise.resolve({ status: "superseded" });
  }
  const budgetMs = Math.max(0, Number(timeoutMs) || 0);
  if (!(budgetMs > 0) || typeof settleWithin !== "function"
      || typeof waitForImagery !== "function" || typeof promote !== "function") {
    return Promise.resolve({ status: "not-ready" });
  }
  let readinessTask;
  try {
    readinessTask = waitForImagery({ timeoutMs: budgetMs, signal });
  } catch (_) {
    return Promise.resolve({ status: "failed" });
  }
  return settleWithin(readinessTask, budgetMs, signal).then((readiness) => {
    if (readiness?.status !== "settled" || readiness.value !== true) {
      return { status: "not-ready" };
    }
    /* This is deliberately adjacent to promote(). Do not move the authority
     * check above the await: cancellation, lifecycle teardown, or a site
     * revision can occur while Cesium is proving the hidden frame is quiet. */
    if (!mayPromote()) return { status: "superseded" };
    try {
      return promote() ?? { status: "not-ready" };
    } catch (_) {
      return { status: "failed" };
    }
  }, (error) => (
    signal?.aborted || error?.name === "AbortError"
      ? { status: "superseded" }
      : { status: "failed" }
  ));
}

/* Continuous wheel/pinch motion intentionally bypasses coordinator prepare(),
 * so it needs a separate visibility authority from a semantic +/- intent. The
 * operation may reveal a staged provider only while the same viewport
 * lifecycle, site/scene revision, surface, and world ownership are still
 * current. Keeping this predicate separate avoids weakening
 * aptWorldPromotionAuthority for late Apartment-entry continuations. */
function aptWorldInteractionPromotionAuthority({
  signal,
  expectedOperationGeneration,
  currentOperationGeneration,
  coordinatorState,
  expectedLifecycleRevision,
  currentLifecycleRevision,
  targetRevision,
  currentTargetRevision,
  expectedSurface,
  currentSurface,
} = {}) {
  return Boolean(
    signal
    && !signal.aborted
    && expectedOperationGeneration === currentOperationGeneration
    && coordinatorState?.phase === "idle"
    && coordinatorState.committedScale !== "apartment"
    && expectedLifecycleRevision === currentLifecycleRevision
    && targetRevision
    && targetRevision === currentTargetRevision
    && expectedSurface
    && expectedSurface === currentSurface
  );
}

/* Detect a successful semantic COMMIT whose destination is the world. This
 * includes Apartment entry, +/- scale intents, and inter-home travel: each can
 * supersede an older provider adopter while reusing the same hidden tileset.
 * Continuous wheel synchronization publishes idle -> idle instead, so the
 * activating edge prevents every reported band change from starting a tail. */
function aptCommittedWorldIntent(previous, next) {
  return Boolean(
    previous?.phase === "activating"
    && previous.transition?.toSurface === "world"
    && next?.phase === "idle"
    && next.committedScale !== "apartment"
  );
}

/* One non-blocking, provider-revalidated adoption transaction for explicit
 * world interaction.
 *
 * Rapid wheel and pinch events join the same Promise instead of starting a
 * readiness listener per event. A timed-out activation is deliberately not
 * aborted: Cesium's paid root factory cannot be cancelled and may still become
 * a hidden staged candidate for the next explicit gesture. A completed request
 * is not memoized by site revision alone: the provider may have been deferred,
 * expired, or faulted since the previous gesture. Cancellation only revokes
 * host visibility authority. */
function aptCreateWorldImageryAdopter({
  timeoutMs = 500,
  refinementTimeoutMs = 1600,
  clock = () => performance.now(),
  settleWithin,
  tryPromote,
  activate,
  waitForImagery,
  promote,
  refine,
  hasAuthority,
} = {}) {
  if ([clock, settleWithin, tryPromote, activate, waitForImagery, promote, refine, hasAuthority]
    .some((callback) => typeof callback !== "function")) {
    throw new TypeError("world imagery adopter callbacks must be functions");
  }
  const budgetMs = Math.max(1, Number(timeoutMs) || 500);
  const refineBudgetMs = Math.max(1, Number(refinementTimeoutMs) || 1600);
  let generation = 0;
  let active = null;

  const owns = (record) => Boolean(
    active === record
    && record.generation === generation
    && !record.controller.signal.aborted
    && hasAuthority(record, generation)
  );

  const cancel = () => {
    generation += 1;
    if (active) active.controller.abort();
    active = null;
  };

  const request = ({
    source = "world-interaction",
    targetRevision = null,
    timeoutMs: requestTimeoutMs = null,
    restart = false,
  } = {}) => {
    if (!targetRevision) return Promise.resolve({ status: "skipped" });
    if (active && active.targetRevision === targetRevision
      && !active.controller.signal.aborted && restart !== true) {
      return active.promise;
    }
    if (active) cancel();

    const record = {
      generation: ++generation,
      source,
      targetRevision,
      budgetMs: Number.isFinite(requestTimeoutMs)
        ? Math.max(1, Number(requestTimeoutMs))
        : budgetMs,
      controller: new AbortController(),
      startedAt: clock(),
      promise: null,
    };
    active = record;
    const remainingMs = () => Math.max(
      0,
      record.budgetMs - (clock() - record.startedAt),
    );

    record.promise = (async () => {
      if (!owns(record)) return { status: "superseded" };
      let activationWindow;
      try {
        activationWindow = await settleWithin(
          activate(record),
          remainingMs(),
          record.controller.signal,
        );
      } catch (error) {
        return record.controller.signal.aborted || error?.name === "AbortError"
          ? { status: "superseded" }
          : { status: "failed" };
      }
      if (!owns(record)) return { status: "superseded" };
      if (activationWindow?.status !== "settled") return { status: "not-ready" };

      const result = await tryPromote({
        activation: activationWindow.value,
        signal: record.controller.signal,
        timeoutMs: remainingMs(),
        settleWithin,
        waitForImagery,
        mayPromote: () => owns(record),
        promote,
      });
      if (result?.status !== "ready" || !owns(record)) return result;

      /* Promotion deliberately invalidates the previous provider's height.
       * Keep this request coalescible while the newly-visible provider refines
       * roof then dollhouse ground height. Wheel/pinch callers never await the
       * Promise, so camera input remains immediate; a second gesture joins the
       * same bounded refinement instead of starting a competing sample. The
       * provider itself remains visible if refinement fails or times out. */
      try {
        await refine(record, {
          timeoutMs: refineBudgetMs,
          mayCommit: () => owns(record),
        });
      } catch (error) {
        if (record.controller.signal.aborted || error?.name === "AbortError") {
          return { status: "superseded" };
        }
      }
      if (!owns(record)) return { status: "superseded" };
      return result;
    })().finally(() => {
      if (active === record) active = null;
    });
    return record.promise;
  };

  return Object.freeze({
    request,
    cancel,
    snapshot() {
      return Object.freeze({
        generation,
        active: Boolean(active),
        activeTargetRevision: active?.targetRevision ?? null,
      });
    },
  });
}

/* Keep capture cancellation ahead of the host's preparation timeout so an
 * immediate retry cannot overlap the previous frame's PBO/fence. Returning
 * zero prevents a late readback; the host retains the committed source view. */
function aptHandoffCaptureBudget(deadlineAt, now, cleanupReserveMs = 24) {
  const deadline = Number(deadlineAt);
  const current = Number(now);
  const reserve = Math.max(0, Number(cleanupReserveMs) || 0);
  if (!Number.isFinite(deadline) || !Number.isFinite(current)) return 0;
  return Math.max(0, deadline - current - reserve);
}

function aptDisposeHandoffCompositorSlots(
  readyRef,
  pendingRef,
  promiseRef,
  generationRef,
) {
  generationRef.current += 1;
  const ready = readyRef.current;
  const pending = pendingRef.current;
  readyRef.current = null;
  pendingRef.current = null;
  promiseRef.current = null;
  ready?.dispose?.();
  if (pending && pending !== ready) pending.dispose?.();
  return generationRef.current;
}

function aptHandoffCompositorMayInstall(
  generation,
  currentGeneration,
  { aborted = false, hasHost = true, pendingMatches = true } = {},
) {
  return generation === currentGeneration
    && aborted !== true
    && hasHost === true
    && pendingMatches === true;
}

/* Wait for the Apartment rig to finish a deliberate pre-travel zoom without
 * tying the journey to a guessed timeout. The hard deadline is only a
 * fail-safe for a throttled/paused renderer; it lands on the same overview
 * detent and preserves the selected azimuth/elevation. */
function aptWaitForRigSettle(rig, { isCurrent, timeoutMs = 700 } = {}) {
  return new Promise((resolve, reject) => {
    const startedAt = performance.now();
    let timer = 0;
    const finish = (forced = false) => {
      if (timer) clearTimeout(timer);
      resolve({ forced });
    };
    const cancel = () => {
      if (timer) clearTimeout(timer);
      const error = new Error("Apartment travel superseded");
      error.name = "AbortError";
      reject(error);
    };
    const check = () => {
      if (isCurrent && !isCurrent()) { cancel(); return; }
      if (!rig?.isTweening?.()) { finish(false); return; }
      if (performance.now() - startedAt >= timeoutMs) {
        rig.goTo?.({ zoom: 0, dur: 0 });
        finish(true);
        return;
      }
      timer = setTimeout(check, 16);
    };
    check();
  });
}

function aptShouldModePrewarm() {
  try {
    if (localStorage.getItem("apartment3d.modePrewarm") === "off") return false;
  } catch (e) { /* storage can fail in private mode */ }
  try {
    const net = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    if (net?.saveData) return false;
    if (/^slow-2g|2g$/i.test(net?.effectiveType || "")) return false;
  } catch (e) { /* */ }
  return true;
}

function AptHudButton({ label, ariaLabel, onClick, active, disabled, title, mobile = readAptViewport().mobile }) {
  return (
    <button
      type="button"
      data-apt-hud-button="1"
      aria-label={ariaLabel || label}
      aria-pressed={typeof active === "boolean" ? active : undefined}
      onClick={onClick} disabled={disabled} title={title} className="hg-focusable"
      style={{
        background: active ? "var(--hg-ice)" : "rgba(10,12,16,0.55)",
        border: "1px solid " + (active ? "var(--hg-ice)" : "var(--hg-border-soft)"),
        color: active ? "#0b0d11" : (disabled ? "var(--hg-fg-5)" : "var(--hg-fg-1)"),
        minHeight: mobile ? 38 : "auto",
        minWidth: mobile ? 44 : "auto",
        padding: mobile ? "7px 10px" : "5px 12px", fontFamily: APT_FONT_MONO, fontSize: mobile ? 9.5 : 10.5,
        letterSpacing: mobile ? "0.08em" : "0.1em", textTransform: "lowercase",
        cursor: disabled ? "default" : "pointer", backdropFilter: "blur(6px)",
      }}
    >{label}</button>
  );
}

function AptZoomButton({ label, title, onClick, disabled, buttonRef, mobile = readAptViewport().mobile }) {
  const size = mobile ? 44 : 38;
  return (
    <button
      ref={buttonRef}
      type="button"
      className="hg-focusable"
      aria-label={title}
      title={title}
      onClick={onClick}
      disabled={disabled}
      style={{
        width: size,
        height: size,
        display: "grid",
        placeItems: "center",
        border: "1px solid var(--hg-border-soft)",
        background: disabled ? "rgba(10,12,16,0.30)" : "rgba(10,12,16,0.62)",
        color: disabled ? "var(--hg-fg-5)" : "var(--hg-fg-0)",
        fontFamily: APT_FONT_MONO,
        fontSize: mobile ? 18 : 16,
        lineHeight: 1,
        cursor: disabled ? "default" : "pointer",
        backdropFilter: "blur(10px)",
        boxShadow: "0 10px 28px rgba(0,0,0,0.28)",
      }}
    >
      {label}
    </button>
  );
}

/*
 * A native, non-canvas route through every Apartment room and device.
 *
 * It occupies one clipped pixel until keyboard focus enters it, then
 * :focus-within reveals the existing-token panel below. Screen readers see a
 * normal labelled navigation landmark at all times; sighted pointer users do
 * not get another permanent layer of Apartment chrome. Device selection and
 * opening controls are separate so arrowing through a native select never
 * yanks focus into a card before the user has finished browsing.
 */
function AptSemanticNavigation({
  navigation,
  devices: roomDevices,
  roomId,
  deviceId,
  onRoomId,
  onDeviceId,
  onPreviousDevice,
  onNextDevice,
  onOpenDevice,
  deviceSelectRef,
  disabled = false,
}) {
  const rooms = navigation?.rooms || [];
  const devices = roomDevices || navigation?.devices || [];
  const selectedRoomId = rooms.some((room) => room.id === roomId) ? roomId : "";
  const selectedDeviceId = devices.some((device) => device.id === deviceId) ? deviceId : "";
  const canBrowseDevices = !disabled && devices.length > 0;

  return (
    <nav
      data-apt-semantic-navigation="1"
      aria-label="Apartment rooms and devices"
    >
      <div style={{
        fontFamily: APT_FONT_SANS, fontSize: 13, color: "var(--hg-fg-0)",
        marginBottom: 4,
      }}>
        apartment navigation
      </div>
      <div id="apt-semantic-navigation-help" style={{
        fontFamily: APT_FONT_MONO, fontSize: 9, lineHeight: 1.45,
        color: "var(--hg-fg-3)", marginBottom: 9,
      }}>
        Browse the model without the 3D canvas. Choosing a room filters the
        device list; devices not positioned in the scan remain available.
      </div>
      <label style={{ display: "grid", gap: 4, marginBottom: 8 }}>
        <span style={{
          fontFamily: APT_FONT_MONO, fontSize: 9, letterSpacing: "0.12em",
          color: "var(--hg-fg-4)", textTransform: "uppercase",
        }}>room</span>
        <select
          data-apt-semantic-room-select="1"
          aria-label="Apartment room"
          aria-describedby="apt-semantic-navigation-help"
          value={selectedRoomId}
          disabled={disabled || rooms.length === 0}
          onChange={(event) => onRoomId?.(event.target.value)}
          className="hg-focusable"
          style={{
            minHeight: 36, width: "100%", padding: "5px 7px",
            fontFamily: APT_FONT_MONO, fontSize: 10,
            color: "var(--hg-fg-1)", background: "var(--hg-bg-1, #0b1218)",
            border: "1px solid var(--hg-border-soft)",
          }}
        >
          <option value="">{rooms.length ? `All rooms · ${rooms.length}` : "No rooms available"}</option>
          {rooms.map((room) => (
            <option key={room.id} value={room.id}>{room.name}</option>
          ))}
        </select>
      </label>
      <label style={{ display: "grid", gap: 4 }}>
        <span style={{
          fontFamily: APT_FONT_MONO, fontSize: 9, letterSpacing: "0.12em",
          color: "var(--hg-fg-4)", textTransform: "uppercase",
        }}>device</span>
        <select
          ref={deviceSelectRef}
          data-apt-semantic-device-select="1"
          aria-label="Apartment device"
          aria-describedby="apt-semantic-navigation-help"
          value={selectedDeviceId}
          disabled={!canBrowseDevices}
          onChange={(event) => onDeviceId?.(event.target.value)}
          className="hg-focusable"
          style={{
            minHeight: 36, width: "100%", padding: "5px 7px",
            fontFamily: APT_FONT_MONO, fontSize: 10,
            color: "var(--hg-fg-1)", background: "var(--hg-bg-1, #0b1218)",
            border: "1px solid var(--hg-border-soft)",
          }}
        >
          <option value="">{devices.length
            ? `${devices.length} devices`
            : selectedRoomId ? "No devices in this room" : "No devices available"}</option>
          {devices.map((device) => (
            <option key={device.id} value={device.id}>{device.ariaLabel}</option>
          ))}
        </select>
      </label>
      <div role="group" aria-label="Device navigation" style={{
        display: "grid", gridTemplateColumns: "1fr 1.35fr 1fr", gap: 6, marginTop: 9,
      }}>
        <button
          type="button"
          data-apt-semantic-previous="1"
          aria-label="Previous apartment device"
          aria-keyshortcuts="P"
          disabled={!canBrowseDevices}
          onClick={onPreviousDevice}
          className="hg-focusable"
        >previous</button>
        <button
          type="button"
          data-apt-semantic-open="1"
          aria-haspopup="dialog"
          disabled={disabled || !selectedDeviceId}
          onClick={onOpenDevice}
          className="hg-focusable"
        >open controls</button>
        <button
          type="button"
          data-apt-semantic-next="1"
          aria-label="Next apartment device"
          aria-keyshortcuts="N"
          disabled={!canBrowseDevices}
          onClick={onNextDevice}
          className="hg-focusable"
        >next</button>
      </div>
    </nav>
  );
}

/*
 * The "Research imagery" disclosure, shown only while an AI still is on screen.
 *
 * It used to render at every world range, planet included, where the only
 * imagery on screen is the live map or the bundled globe: a statement about a
 * picture nobody could see, placed over the one they were looking at. And it
 * was hidden during the outward handoff, whose particles collapse into exactly
 * that still, because the scale is still "apartment" until the handoff
 * commits. Visibility now follows the frame (aptResearchImageryShown), so its
 * state lives below a memo boundary like the projected pins: polling
 * cameraState() per frame while the world moves never rerenders the Apartment
 * view. At rest it polls at the pins' idle cadence unless a still is in the
 * stack but hidden (aptResearchImageryCadence), so it owns no rAF over a
 * settled view.
 *
 * "On screen" is the surface's own hero/cloud numbers AND a painted world
 * host (aptWorldHostPainted): at the Apartment scale the host is transparent
 * until the handoff compositor's clock starts, and the interior is what shows
 * during preparation, however opaque the parked still is.
 */
const AptResearchImageryDisclosure = React.memo(function AptResearchImageryDisclosure({
  surfaceRef,
  worldHostRef,
  interior,
  motionActive,
  holdLayout,
  note,
}) {
  const [shown, setShown] = useState(false);
  /* Outlives effect restarts, so the end of a handoff cannot cut the grace. */
  const lastOnScreenAtRef = useRef(null);
  useEffect(() => {
    let frame = 0;
    let idleTimer = 0;
    let cancelled = false;
    let interactionUntil = 0;
    const documentVisible = () => typeof document === "undefined"
      || document.visibilityState !== "hidden";
    const clearScheduled = () => {
      if (frame) cancelAnimationFrame(frame);
      if (idleTimer) clearTimeout(idleTimer);
      frame = 0;
      idleTimer = 0;
    };
    const surfaceIsMoving = () => {
      if (motionActive || performance.now() < interactionUntil) return true;
      try {
        const snapshot = surfaceRef.current?.snapshot?.();
        return snapshot?.navigating === true || snapshot?.zooming === true;
      } catch (_) {
        return false;
      }
    };
    const tick = () => {
      frame = 0;
      idleTimer = 0;
      if (cancelled) return;
      let camera = null;
      if (!interior) {
        try { camera = surfaceRef.current?.cameraState?.() ?? null; } catch (_) { camera = null; }
      }
      const nowMs = performance.now();
      const stillInStack = camera?.hero != null;
      const stillOnScreen = !interior && aptHeroStillOnScreen(camera)
        && aptWorldHostPainted(worldHostRef?.current?.style?.opacity);
      if (stillOnScreen) lastOnScreenAtRef.current = nowMs;
      const next = aptResearchImageryShown({
        interior, stillOnScreen, lastOnScreenAtMs: lastOnScreenAtRef.current, nowMs,
      });
      setShown((previous) => (previous === next ? previous : next));
      /* Inside the interior there is no still to watch until a handoff starts,
       * and that change of props restarts this effect. */
      const cadence = aptResearchImageryCadence({
        interior,
        documentVisible: documentVisible(),
        moving: !interior && surfaceIsMoving(),
        stillInStack,
        stillOnScreen,
      });
      if (cadence === "frame") frame = requestAnimationFrame(tick);
      else if (cadence === "idle") idleTimer = setTimeout(tick, APT_WORLD_PIN_IDLE_CADENCE_MS);
    };
    /*
     * Any input may be the start of a move, so it wakes the per-frame poll
     * before the first frame it could change. Heard on the window in the
     * capture phase, ahead of everything that consumes it: the world host's
     * zoom listener stops propagation, and a wheel over a projected label or a
     * recovery button, the zoom buttons and the keyboard never reach the host
     * at all. Passive, so it can never delay the input it hears.
     */
    const wakeForInteraction = () => {
      if (interior || cancelled) return;
      interactionUntil = performance.now() + APT_WORLD_PIN_INTERACTION_TAIL_MS;
      if (frame) return;
      clearScheduled();
      frame = requestAnimationFrame(tick);
    };
    const onVisibilityChange = () => {
      clearScheduled();
      if (documentVisible()) tick();
    };
    const wakeEvents = ["wheel", "pointerdown", "keydown", "touchmove"];
    const listening = !interior && typeof window !== "undefined";
    if (listening) {
      for (const type of wakeEvents) {
        window.addEventListener(type, wakeForInteraction, { passive: true, capture: true });
      }
    }
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibilityChange);
    }
    tick();
    return () => {
      cancelled = true;
      clearScheduled();
      if (listening) {
        for (const type of wakeEvents) {
          window.removeEventListener(type, wakeForInteraction, { capture: true });
        }
      }
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisibilityChange);
      }
    };
  }, [surfaceRef, worldHostRef, interior, motionActive]);

  /*
   * While a recovery card is up it sits on the credits' top edge, and the
   * credits grow with this block: collapsing it would slide the card's buttons
   * under the pointer by its height (about 20 px, or 70-75 px with Echo
   * Park's note) each time the still came or went. So it keeps its box,
   * invisible and aria-hidden, until the card goes. The hidden box carries no
   * data-apt-content-review, so the audits count a disclosure only when one is
   * on screen.
   */
  if (!shown && !holdLayout) return null;
  const reserved = !shown;
  return (
    <div aria-hidden={reserved ? "true" : undefined}
      data-apt-content-review-reserved={reserved ? "1" : undefined}
      style={{ justifySelf: "end", display: "grid", gap: 3, justifyItems: "end",
        maxWidth: "min(100%, 460px)", visibility: reserved ? "hidden" : undefined }}>
      <span data-apt-content-review={reserved ? undefined : "research-only"}
        title={reserved ? undefined : "This imported home pack has not been approved for production use."}
        style={{ fontSize: 10, color: "#fff", padding: "4px 7px", background: "rgba(5,12,18,.8)" }}>
        Research imagery
      </span>
      {note ? (
        <span data-apt-content-review-note={reserved ? undefined : "1"}
          style={{ fontSize: 10, lineHeight: 1.35, color: "#d7dde3", textAlign: "right",
            padding: "4px 7px", background: "rgba(5,12,18,.8)" }}>
          {note}
        </span>
      ) : null}
    </div>
  );
});

/*
 * The journey recovery message: a card above the credits, not a dialog in the
 * middle of the view.
 *
 * Centred, it sat exactly where the cursor rests while zooming and swallowed
 * the wheel (the 2026-09-17 audit, stage 5), so the one gesture that could
 * clear a held cloud was the one it blocked. The card passes the wheel, drags
 * and clicks through; only its buttons are hit-tested, and a wheel over them
 * bubbles to the card and is handed to the world as the same event
 * (onWheelForward). The host renders it inside the credits and it sits on
 * their top edge, so it can never cover the imagery attribution, whose height
 * changes with the provider. The disclosure above the attribution keeps its
 * box while the card is up (holdLayout), so the still coming and going cannot
 * move these buttons under the pointer. Visibility is restated
 * because the credits are hidden inside the interior, where a failed journey
 * also reports here. A broken renderer still blacks out the view behind it.
 */
function AptJourneyRecoveryCard({
  message,
  blackout,
  showOverview,
  onRetry,
  onReturn,
  onShowOverview,
  onWheelForward,
}) {
  const cardRef = useRef(null);
  useEffect(() => {
    const card = cardRef.current;
    if (!card || !onWheelForward) return undefined;
    card.addEventListener("wheel", onWheelForward, { passive: false });
    return () => card.removeEventListener("wheel", onWheelForward);
  }, [onWheelForward]);
  return (
    <div ref={cardRef} data-apt-journey-recovery="1" role="status" aria-live="polite"
      style={{ position: "absolute", left: "50%", bottom: "calc(100% + 8px)", transform: "translateX(-50%)",
        width: "min(420px, 100%)", boxSizing: "border-box", padding: "12px 14px", borderRadius: 8,
        color: "#fff", background: "rgba(5,12,18,.94)", fontSize: 12, lineHeight: 1.45,
        visibility: "visible",
        boxShadow: blackout ? "0 0 0 200vmax #050c12" : "0 8px 32px rgba(0,0,0,.5)",
        pointerEvents: "none" }}>
      <p style={{ margin: "0 0 10px" }}>{message}</p>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <button type="button" className="hg-focusable" style={{ minHeight: 44, minWidth: 64, pointerEvents: "auto" }}
          onClick={onRetry}>Retry</button>
        <button type="button" className="hg-focusable" style={{ minHeight: 44, minWidth: 64, pointerEvents: "auto" }}
          onClick={onReturn}>Return to interior</button>
        {showOverview && (
          <button type="button" className="hg-focusable" style={{ minHeight: 44, pointerEvents: "auto" }}
            onClick={onShowOverview}>Show world overview</button>
        )}
      </div>
    </div>
  );
}

/* Projected home labels update on nearly every frame of a planet flight. Keep
 * that high-frequency state below a memo boundary so moving two 44px buttons
 * never rerenders the full Apartment view and its accessibility/card tree. */
/*
 * THE HERO STILL AND THE CLOUD PASS-THROUGH USED TO LIVE HERE.
 *
 * WHAT THEY ARE. The still is a sharp image of the home that the world opens
 * on and pulls out of; the cloud is what hides the moment that still is
 * exchanged for the live map. Both exist because Google's photorealistic mesh
 * is aerial photogrammetry and is softest exactly where this product asks the
 * most of it -- one roofline, filling the frame, at the moment the scan hands
 * over. A cross-fade cannot make the exchange, because it requires the two
 * images to agree pixel for pixel and they do not; an opaque occluder removes
 * the requirement, because nothing has to match while nothing is visible.
 *
 * WHY THEY ARE NOT HERE ANY MORE. They were an <img> and seven blurred
 * gradients composited over the canvas. DOM was chosen to avoid a third WebGL
 * context, since this view already runs two and WebView2 kills the OLDEST when
 * it hits its cap -- which is the Apartment's. That reasoning was sound and is
 * still recorded at the live-feed canvas below; it simply does not apply to
 * where they went, which is a post-process stage INSIDE the widget that was
 * already here. No new context exists.
 *
 * What DOM could not do is the reason to move. Compositing over the canvas
 * makes the browser blend a full-viewport image and seven large blurred
 * gradients on top of every frame the scene has already paid for, and a
 * gradient has no depth to march, so the cloud could only ever be a flat
 * sweep -- which is what "the clouds are just a grey overlay" meant. In the
 * shader the still is one texture fetch and the cloud is a real volumetric
 * slab, and the two cost ONE pass together rather than two.
 *
 * See hero-cloud-pass.mjs for the shader and world-surface.js for the wiring.
 * The arithmetic still lives in hero-still.mjs and cloud-handoff.mjs, still
 * arrives through cameraState(), and this host still decides nothing -- the
 * only change is which surface draws it.
 */
function aptWorldPinEmphasis(distancePx, focused = false) {
  const strength = focused ? 1 : Math.max(0, 1 - Math.max(0, Number(distancePx)) / 120);
  return { strength, scale: 1 + 0.25 * (Number.isFinite(strength) ? strength : 0) };
}

const AptWorldSitePins = React.memo(function AptWorldSitePins({
  visible,
  surfaceRef,
  interactionTargetRef,
  sites,
  selectedSiteId,
  travelling,
  motionActive,
  onSelect,
  onWheelForward,
  frameStyle,
}) {
  const [pins, setPins] = useState([]);
  const [pointer, setPointer] = useState(null);
  const [focusedSiteId, setFocusedSiteId] = useState(null);
  const pinsHostRef = useRef(null);
  /* The projection loop's wake, for wheel that reaches a pin instead of the
   * world host and so never passes the host's own listener. */
  const wakeRef = useRef(null);
  useEffect(() => {
    if (!visible) return undefined;
    const target = interactionTargetRef?.current?.parentElement;
    let frame = 0;
    let latest = null;
    const move = (event) => {
      const box = pinsHostRef.current?.getBoundingClientRect();
      if (!box) return;
      latest = { x: event.clientX - box.left, y: event.clientY - box.top,
        width: box.width, height: box.height };
      if (!frame) frame = requestAnimationFrame(() => { frame = 0; setPointer(latest); });
    };
    const leave = () => { latest = null; setPointer(null); };
    target?.addEventListener('pointermove', move, { passive: true });
    target?.addEventListener('pointerleave', leave);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      target?.removeEventListener('pointermove', move);
      target?.removeEventListener('pointerleave', leave);
    };
  }, [visible, interactionTargetRef]);
  const projectionTargets = useMemo(() => sites.map((site) => ({
    id: site.id,
    longitude: site.visualAnchor?.longitude ?? site.site?.longitude,
    latitude: site.visualAnchor?.latitude ?? site.site?.latitude,
    altitudeMeters: 0,
  })), [sites]);
  const sitesById = useMemo(() => new Map(sites.map((site) => [site.id, site])), [sites]);

  useEffect(() => {
    if (!visible || !projectionTargets.length) {
      setPins((previous) => (previous.length ? [] : previous));
      return undefined;
    }
    let frame = 0;
    let idleTimer = 0;
    let cancelled = false;
    let pointerActive = false;
    let interactionUntil = 0;
    const documentVisible = () => typeof document === "undefined"
      || document.visibilityState !== "hidden";
    const clearScheduled = () => {
      if (frame) cancelAnimationFrame(frame);
      if (idleTimer) clearTimeout(idleTimer);
      frame = 0;
      idleTimer = 0;
    };
    const surfaceIsMoving = () => {
      if (motionActive || travelling || pointerActive || Date.now() < interactionUntil) return true;
      try {
        const snapshot = surfaceRef.current?.snapshot?.();
        return snapshot?.navigating === true || snapshot?.zooming === true;
      } catch (_) {
        return false;
      }
    };
    const scheduleFrame = () => {
      if (cancelled || !documentVisible() || frame) return;
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = 0;
      frame = requestAnimationFrame(tick);
    };
    const scheduleIdle = () => {
      if (cancelled || !documentVisible() || idleTimer) return;
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
      idleTimer = setTimeout(tick, APT_WORLD_PIN_IDLE_CADENCE_MS);
    };
    const scheduleNext = () => {
      const cadence = aptWorldPinCadence({
        visible,
        documentVisible: documentVisible(),
        motionActive: surfaceIsMoving(),
      });
      if (cadence === "frame") scheduleFrame();
      else if (cadence === "idle") scheduleIdle();
      else clearScheduled();
    };
    const tick = () => {
      frame = 0;
      idleTimer = 0;
      if (cancelled) return;
      const surface = surfaceRef.current;
      const camera = surface?.cameraState?.();
      if (surface && camera && camera.rangeM >= WORLD_PIN_MIN_RANGE_M) {
        try {
          const next = surface.projectSites?.(projectionTargets) ?? [];
          setPins((previous) => {
            if (previous.length === next.length && previous.every((pin, index) => {
              const now = next[index];
              return now && pin.id === now.id
                && Math.abs(pin.xFraction - now.xFraction) < 0.0015
                && Math.abs(pin.yFraction - now.yFraction) < 0.0015;
            })) return previous;
            return next;
          });
        } catch (_) {
          setPins((previous) => (previous.length ? [] : previous));
        }
      } else {
        setPins((previous) => (previous.length ? [] : previous));
      }
      scheduleNext();
    };
    const wakeForInteraction = () => {
      interactionUntil = Date.now() + APT_WORLD_PIN_INTERACTION_TAIL_MS;
      scheduleFrame();
    };
    const onPointerDown = () => {
      pointerActive = true;
      scheduleFrame();
    };
    const onPointerDone = () => {
      pointerActive = false;
      wakeForInteraction();
    };
    const onVisibilityChange = () => {
      clearScheduled();
      if (documentVisible()) scheduleFrame();
    };
    wakeRef.current = wakeForInteraction;
    const interactionTarget = interactionTargetRef?.current;
    interactionTarget?.addEventListener?.("pointerdown", onPointerDown, { passive: true });
    /* Capture, like the host's own zoom listener: that one stops propagation
     * in the capture phase, so a bubbling listener here never heard a notch
     * over the map and the labels waited out an idle poll to follow the zoom. */
    interactionTarget?.addEventListener?.("wheel", wakeForInteraction, { passive: true, capture: true });
    interactionTarget?.addEventListener?.("touchmove", wakeForInteraction, { passive: true });
    if (typeof window !== "undefined") {
      window.addEventListener("pointerup", onPointerDone, { passive: true });
      window.addEventListener("pointercancel", onPointerDone, { passive: true });
      window.addEventListener("blur", onPointerDone);
    }
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibilityChange);
    }
    scheduleFrame();
    return () => {
      cancelled = true;
      clearScheduled();
      if (wakeRef.current === wakeForInteraction) wakeRef.current = null;
      interactionTarget?.removeEventListener?.("pointerdown", onPointerDown);
      interactionTarget?.removeEventListener?.("wheel", wakeForInteraction, { capture: true });
      interactionTarget?.removeEventListener?.("touchmove", wakeForInteraction);
      if (typeof window !== "undefined") {
        window.removeEventListener("pointerup", onPointerDone);
        window.removeEventListener("pointercancel", onPointerDone);
        window.removeEventListener("blur", onPointerDone);
      }
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisibilityChange);
      }
    };
  }, [visible, surfaceRef, interactionTargetRef, projectionTargets, motionActive, travelling]);

  /*
   * A wheel over a label continues the zoom.
   *
   * This layer is a sibling of the world host, not its child, so a notch over
   * a button never reached the host's listener and was simply lost -- over the
   * labels, which is where a person looks while zooming out to them. The layer
   * and the current home's marker are pointer-events:none, so only a travel
   * button can receive a wheel; it bubbles here and the SAME event is handed
   * to the handler the host uses. Nothing is synthesised, so the zoom sees one
   * notch, not two.
   */
  const layerMounted = visible && pins.length > 0;
  useEffect(() => {
    const layer = pinsHostRef.current;
    if (!layerMounted || !layer) return undefined;
    const onWheel = (event) => {
      wakeRef.current?.();
      onWheelForward?.(event);
    };
    layer.addEventListener("wheel", onWheel, { passive: false });
    return () => layer.removeEventListener("wheel", onWheel);
  }, [layerMounted, onWheelForward]);

  if (!visible || !pins.length) return null;
  return (
    <div
      ref={pinsHostRef}
      data-apt-world-pins="1"
      style={{
        ...frameStyle,
        left: 0,
        /* Above the chrome scrim (z4, later in the DOM), which painted over
         * any label in the top ~170 px or bottom ~190 px of the view -- where
         * the other home sits at planet range. Still below the top bar and
         * bottom HUD (z5, later in the DOM), the zoom controls (z6), credits
         * (z7) and the overview banner (z8), so no label covers a control. */
        zIndex: 5,
        pointerEvents: "none",
        background: "transparent",
      }}
    >
      {pins.map((pin) => {
        const site = sitesById.get(pin.id);
        if (!site) return null;
        const isSelected = site.id === selectedSiteId;
        const isCurrent = aptWorldPinIsCurrent(site.id, selectedSiteId, travelling);
        /* A marker is not a control: neither the cursor nor focus may lift it
         * the way they lift a travel button. */
        const distance = !isCurrent && pointer ? Math.hypot(pointer.x - pin.xFraction * pointer.width,
          pointer.y - pin.yFraction * pointer.height) : Infinity;
        const emphasis = aptWorldPinEmphasis(distance, !isCurrent && focusedSiteId === pin.id);
        const style = {
          position: "absolute",
          left: `${pin.xFraction * 100}%`,
          top: `${pin.yFraction * 100}%`,
          transform: `translate(-50%, -50%) scale(${emphasis.scale})`,
          transition: aptPrefersReducedMotion() ? "none" : "transform 150ms ease, box-shadow 150ms ease",
          minWidth: 44,
          height: 44,
          padding: "0 10px",
          border: `1px solid ${isSelected ? "rgba(127,231,255,0.72)" : "rgba(255,255,255,0.38)"}`,
          background: isSelected ? "rgba(5,18,25,0.88)" : "rgba(5,10,15,0.82)",
          color: isSelected ? "#bdf5ff" : "rgba(255,255,255,0.94)",
          boxShadow: emphasis.strength > 0
            ? `0 0 ${12 + 12 * emphasis.strength}px rgba(127,231,255,${0.45 * emphasis.strength}), 0 0 0 3px rgba(127,231,255,${0.2 * emphasis.strength})`
            : isSelected
            ? "0 0 0 3px rgba(127,231,255,0.16), 0 4px 18px rgba(0,0,0,0.42)"
            : "0 4px 16px rgba(0,0,0,0.42)",
          cursor: isCurrent ? "default" : travelling ? "progress" : "pointer",
          /* The current home's marker passes the wheel, drags and clicks
           * straight through to the world under it. */
          pointerEvents: isCurrent ? "none" : "auto",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontFamily: APT_FONT_MONO,
          fontSize: 10,
          fontWeight: 700,
          letterSpacing: "0.08em",
          whiteSpace: "nowrap",
        };
        if (isCurrent) {
          /* A map marker, named as one. The visible text stays the bare home
           * name (the zoom audit and the journey harness read it); "current
           * home" is in the accessible name, and the dot is the visible cue.
           * It keeps data-apt-world-pin because the zoom audit waits for the
           * current home's label to be visible. It is deliberately not
           * hit-testable, so elementFromPoint at its centre finds the world;
           * data-apt-world-pin-current tells an audit that is by design, not
           * a layer painted over it. */
          return (
            <span
              key={pin.id}
              data-apt-world-pin={pin.id}
              data-apt-world-pin-current="1"
              role="img"
              aria-label={`${site.shortName}, current home`}
              aria-current="location"
              style={style}
            >
              <span aria-hidden="true" style={{ width: 6, height: 6, borderRadius: "50%",
                background: "#7fe7ff", marginRight: 7, flex: "none" }} />
              {site.shortName}
            </span>
          );
        }
        return (
          <button
            key={pin.id}
            type="button"
            data-apt-world-pin={pin.id}
            title={`Travel to ${site.shortName}`}
            aria-label={`Travel to ${site.shortName}`}
            onFocus={() => setFocusedSiteId(pin.id)}
            onBlur={() => setFocusedSiteId(null)}
            onClick={() => { void onSelect(pin.id); }}
            style={style}
          >
            {site.shortName}
          </button>
        );
      })}
    </div>
  );
});

/* Live MJPEG feed, undistorted on the fly. When the snapped camera carries
   solved intrinsics with real distortion (dist[0] != 0) the raw <img> stays in
   the DOM hidden (opacity 0, NOT display:none — Chrome stops decoding
   multipart frames for undisplayed images) as the stream source, and a rAF
   loop warps its current frame through a WebGL fragment shader: each output
   (undistorted) pixel is normalized via K, pushed through the Brown forward
   model — radial k1..k3, rational k4..k6 when the solver picked that model,
   tangential p1,p2 — and samples the distorted source there. A plain 2D
   canvas can't do this per-pixel warp at 720p/30; raw WebGL is fine here
   (no THREE — that stays inside home-3d). Falls back to the plain <img>
   when there are no intrinsics, no WebGL, shader trouble, or the stream is
   CORS-blocked (crossOrigin load error). */
function AptUndistortedFeed({ src, snapshotSrc, snapshotIntervalMs = 0, alt, intrinsics, style, onStatus, onReload }) {
  const canvasRef = useRef(null);
  const imgRef = useRef(null);
  const [fallback, setFallback] = useState(false);
  const [warpedReady, setWarpedReady] = useState(false);
  const [frameSrc, setFrameSrc] = useState(() => aptInitialFrameSrc(src, snapshotSrc, snapshotIntervalMs));
  const warpedReadyRef = useRef(false);
  const rawSeenRef = useRef(false);
  const publishFrameRef = useRef(null);
  const refreshTimerRef = useRef(0);
  const loadWatchdogRef = useRef(0);
  const frameRequestRef = useRef(0);
  const preloadRef = useRef(null);
  const useSnapshots = !!(snapshotSrc && snapshotIntervalMs > 0);

  const K = intrinsics && intrinsics.K;
  const dist = (intrinsics && intrinsics.dist) || [];
  const wantWarp = !fallback && !!(K && dist.length >= 1 && dist[0] !== 0);

  useEffect(() => {
    setFallback(false);
    setWarpedReady(false);
    warpedReadyRef.current = false;
    rawSeenRef.current = false;
    onStatus?.("connecting");
    const base = useSnapshots ? snapshotSrc : src;
    const clearTimers = () => {
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
      if (loadWatchdogRef.current) clearTimeout(loadWatchdogRef.current);
      if (preloadRef.current) {
        preloadRef.current.onload = null;
        preloadRef.current.onerror = null;
        preloadRef.current = null;
      }
      refreshTimerRef.current = 0;
      loadWatchdogRef.current = 0;
    };
    const publish = (delay = 0) => {
      clearTimers();
      const run = () => {
        const frameId = ++frameRequestRef.current;
        const next = useSnapshots ? aptCacheBust(base, Date.now()) : base;
        if (useSnapshots) {
          const img = new Image();
          preloadRef.current = img;
          img.onload = async () => {
            if (frameRequestRef.current !== frameId) return;
            try { if (img.decode) await img.decode(); } catch (e) { /* decoded enough for display */ }
            if (frameRequestRef.current !== frameId) return;
            if (loadWatchdogRef.current) clearTimeout(loadWatchdogRef.current);
            loadWatchdogRef.current = 0;
            rawSeenRef.current = true;
            setFrameSrc(next);
            onStatus?.("frame");
            publishFrameRef.current?.(Math.max(900, snapshotIntervalMs));
          };
          img.onerror = () => {
            if (frameRequestRef.current !== frameId) return;
            if (loadWatchdogRef.current) clearTimeout(loadWatchdogRef.current);
            loadWatchdogRef.current = 0;
            onStatus?.("retrying");
            publishFrameRef.current?.(Math.max(1600, snapshotIntervalMs * 2));
          };
          const timeout = Math.max(12000, snapshotIntervalMs * 8);
          loadWatchdogRef.current = setTimeout(() => {
            if (frameRequestRef.current !== frameId) return;
            onStatus?.("retrying");
            publish(0);
          }, timeout);
          img.src = next;
        } else {
          setFrameSrc(next);
          loadWatchdogRef.current = setTimeout(() => {
            if (frameRequestRef.current !== frameId) return;
            onStatus?.("retrying");
            onReload?.("no first frame");
          }, 12000);
        }
      };
      if (delay > 0) refreshTimerRef.current = setTimeout(run, delay);
      else run();
    };
    publishFrameRef.current = publish;
    publish(0);
    return () => {
      publishFrameRef.current = null;
      clearTimers();
    };
  }, [src, snapshotSrc, snapshotIntervalMs, useSnapshots, onStatus, onReload]);

  const handleImageLoad = useCallback(() => {
    if (useSnapshots) return;
    if (loadWatchdogRef.current) clearTimeout(loadWatchdogRef.current);
    loadWatchdogRef.current = 0;
    frameRequestRef.current += 1;
    onStatus?.(useSnapshots ? "frame" : "raw");
    if (useSnapshots) {
      publishFrameRef.current?.(Math.max(900, snapshotIntervalMs));
    }
  }, [useSnapshots, snapshotIntervalMs, onStatus]);

  const handleImageError = useCallback(() => {
    if (useSnapshots) return;
    if (loadWatchdogRef.current) clearTimeout(loadWatchdogRef.current);
    loadWatchdogRef.current = 0;
    frameRequestRef.current += 1;
    onStatus?.(useSnapshots ? "retrying" : "error");
    if (useSnapshots) {
      publishFrameRef.current?.(Math.max(1600, snapshotIntervalMs * 2));
    } else {
      setFallback(true);
    }
  }, [useSnapshots, snapshotIntervalMs, onStatus]);

  useEffect(() => {
    if (!wantWarp) return undefined;
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img) return undefined;

    let gl = null;
    try { gl = canvas.getContext("webgl", { antialias: false, depth: false, stencil: false }); }
    catch (e) { /* no webgl */ }
    if (!gl) { setFallback(true); return undefined; }

    const VS = `
      attribute vec2 aPos;
      varying vec2 vUv;
      void main() {
        // clip y=+1 (canvas top) -> vUv.y=0: DOM <img> uploads rows top-first
        // (UNPACK_FLIP_Y left false), so image pixel convention (y down)
        // matches texture v directly — no flips anywhere else
        vUv = vec2(aPos.x * 0.5 + 0.5, 0.5 - aPos.y * 0.5);
        gl_Position = vec4(aPos, 0.0, 1.0);
      }`;
    const FS = `
      precision highp float;
      varying vec2 vUv;
      uniform sampler2D uTex;
      uniform vec2 uSize;   // texture size, px
      uniform vec4 uK;      // fx, fy, cx, cy scaled to texture px
      uniform vec3 uKr;     // k1, k2, k3
      uniform vec3 uKd;     // k4, k5, k6 (rational; zeros for 5-param)
      uniform vec2 uP;      // p1, p2
      void main() {
        vec2 pix = vUv * uSize;
        float xu = (pix.x - uK.z) / uK.x;
        float yu = (pix.y - uK.w) / uK.y;
        float r2 = xu * xu + yu * yu;
        float r4 = r2 * r2;
        float r6 = r4 * r2;
        float rad = (1.0 + uKr.x * r2 + uKr.y * r4 + uKr.z * r6)
                  / (1.0 + uKd.x * r2 + uKd.y * r4 + uKd.z * r6);
        float xd = xu * rad + 2.0 * uP.x * xu * yu + uP.y * (r2 + 2.0 * xu * xu);
        float yd = yu * rad + uP.x * (r2 + 2.0 * yu * yu) + 2.0 * uP.y * xu * yu;
        vec2 suv = vec2(uK.x * xd + uK.z, uK.y * yd + uK.w) / uSize;
        if (suv.x < 0.0 || suv.x > 1.0 || suv.y < 0.0 || suv.y > 1.0) {
          gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0);
        } else {
          gl_FragColor = texture2D(uTex, suv);
        }
      }`;

    const mkShader = (type, text) => {
      const s = gl.createShader(type);
      if (!s) return null;   // lost context: createShader returns null and
                             // shaderSource(null) would THROW (no boundary
                             // above us — it would blank the whole app)
      gl.shaderSource(s, text);
      gl.compileShader(s);
      return gl.getShaderParameter(s, gl.COMPILE_STATUS) ? s : null;
    };
    const vs = mkShader(gl.VERTEX_SHADER, VS);
    const fs = vs && mkShader(gl.FRAGMENT_SHADER, FS);
    const prog = fs ? gl.createProgram() : null;
    if (prog) {
      gl.attachShader(prog, vs);
      gl.attachShader(prog, fs);
      gl.linkProgram(prog);
    }
    if (!prog || !gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      setFallback(true);
      // this effect returns no cleanup on this path — release the context
      // here or the failed attempt still burns a context slot
      try { gl.getExtension("WEBGL_lose_context")?.loseContext(); } catch (e) { /* */ }
      return undefined;
    }
    gl.useProgram(prog);

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]),
                  gl.STATIC_DRAW);
    const aPos = gl.getAttribLocation(prog, "aPos");
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

    const tex = gl.createTexture();
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);

    // K arrives as nested 3x3 rows from the solver (calib.py K.tolist());
    // tolerate a flat-9 copy too. dist is [k1,k2,p1,p2,k3,(k4,k5,k6)].
    const kf = Array.isArray(K[0])
      ? [K[0][0], K[1][1], K[0][2], K[1][2]]
      : [K[0], K[4], K[2], K[5]];
    const d = (i) => +dist[i] || 0;
    gl.uniform1i(gl.getUniformLocation(prog, "uTex"), 0);
    gl.uniform3f(gl.getUniformLocation(prog, "uKr"), d(0), d(1), d(4));
    gl.uniform3f(gl.getUniformLocation(prog, "uKd"), d(5), d(6), d(7));
    gl.uniform2f(gl.getUniformLocation(prog, "uP"), d(2), d(3));
    const uKLoc = gl.getUniformLocation(prog, "uK");
    const uSizeLoc = gl.getUniformLocation(prog, "uSize");
    const calW = (intrinsics.image_size && +intrinsics.image_size[0]) || 0;
    const calH = (intrinsics.image_size && +intrinsics.image_size[1]) || 0;

    let raf = 0;
    let lastW = 0, lastH = 0;
    const draw = () => {
      raf = requestAnimationFrame(draw);
      const w = img.naturalWidth, h = img.naturalHeight;
      if (!(w > 0) || !(h > 0)) return;
      if (!rawSeenRef.current) {
        rawSeenRef.current = true;
        onStatus?.(useSnapshots ? "frame" : "raw");
      }
      if (w !== lastW || h !== lastH) {
        lastW = w; lastH = h;
        canvas.width = w; canvas.height = h;
        gl.viewport(0, 0, w, h);
        // K was solved at image_size; the MJPEG can stream at another res
        const sx = calW ? w / calW : 1, sy = calH ? h / calH : 1;
        gl.uniform4f(uKLoc, kf[0] * sx, kf[1] * sy, kf[2] * sx, kf[3] * sy);
        gl.uniform2f(uSizeLoc, w, h);
      }
      try {
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
      } catch (e) {
        // CORS-tainted stream — give the user the raw feed instead
        cancelAnimationFrame(raf);
        onStatus?.("raw");
        setFallback(true);
        return;
      }
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      if (!warpedReadyRef.current) {
        warpedReadyRef.current = true;
        setWarpedReady(true);
        onStatus?.("warped");
      }
    };
    raf = requestAnimationFrame(draw);

    return () => {
      cancelAnimationFrame(raf);
      try {
        gl.deleteTexture(tex); gl.deleteBuffer(buf); gl.deleteProgram(prog);
        gl.deleteShader(vs); gl.deleteShader(fs);
        // WebView2/Chromium caps live WebGL contexts (~16) and silently kills
        // the OLDEST on overflow — which is the 3D engine's. Without this
        // explicit release every feed open/close leaks one context slot until
        // the engine canvas goes permanently black mid-session.
        const lose = gl.getExtension("WEBGL_lose_context");
        if (lose) lose.loseContext();
      } catch (e) { /* context already gone */ }
      if (img) img.src = "";   // close the hidden MJPEG connection immediately
    };
  }, [wantWarp, src, snapshotSrc, intrinsics, useSnapshots, onStatus]);

  if (!wantWarp) {
    if (useSnapshots && !frameSrc) {
      return (
        <div
          data-apt-live-feed="loading"
          data-apt-live-visible="0"
          data-apt-frame-src=""
          style={{ ...style, background: "#000" }}
        />
      );
    }
    return (
      <img
        src={frameSrc}
        alt={alt}
        data-apt-live-feed="img"
        data-apt-live-visible="1"
        data-apt-frame-src={frameSrc || ""}
        onLoad={handleImageLoad}
        onError={handleImageError}
        style={style}
      />
    );
  }
  const fit = style?.objectFit || "fill";
  const frameStyle = { ...style, position: "relative", overflow: "hidden" };
  const fillStyle = {
    position: "absolute",
    inset: 0,
    width: "100%",
    height: "100%",
    objectFit: fit,
  };
  return (
    <div style={frameStyle}>
      <img
        ref={imgRef} src={frameSrc} alt="" aria-hidden="true" crossOrigin="anonymous"
        data-apt-live-feed="img"
        data-apt-live-visible={warpedReady ? "0" : "1"}
        data-apt-frame-src={frameSrc || ""}
        onLoad={handleImageLoad}
        onError={handleImageError}
        style={{ ...fillStyle, opacity: warpedReady ? 0 : 1, pointerEvents: "none" }}
      />
      <canvas
        ref={canvasRef}
        data-apt-live-feed="canvas"
        data-apt-live-visible={warpedReady ? "1" : "0"}
        style={{ ...fillStyle, opacity: warpedReady ? 1 : 0, pointerEvents: "none" }}
      />
    </div>
  );
}

function AptHomePanelSlot({ enabled, target, children, className }) {
  if (!enabled) return children;
  return target ? ReactDOM.createPortal(className ? <div className={className}>{children}</div> : children, target) : null;
}

function AptUnifiedRange({ label, ariaLabel, value, min = 0, max = 100, step = 1, suffix = "%", onChange }) {
  return <label className="apt-ui-slider">
    <span className="apt-ui-row"><span className="grow">{label}</span><span className="apt-ui-value">{Math.round(value || 0)}{suffix}</span></span>
    <input className={`apt-ui-range${String(suffix).trim() === "K" ? " is-temperature" : ""}`} type="range" aria-label={ariaLabel || label} min={min} max={max} step={step}
      value={value ?? min} onChange={event => onChange(Number(event.target.value))} />
  </label>;
}

/* The handoff's existing 16px icon paths match Home's restrained stroke treatment. */
function AptUnifiedIcon({ name }) {
  const paths = {
    back: "M10 3L5 8l5 5", close: "M4 4l8 8M12 4l-8 8", chevL: "M10 3L5 8l5 5", chevR: "M6 3l5 5-5 5",
    chevD: "M3 6l5 5 5-5", chevU: "M3 10l5-5 5 5",
    layers: "M8 2l6 3-6 3-6-3 6-3zM2 8l6 3 6-3M2 11l6 3 6-3", help: "M6 6a2 2 0 1 1 2.8 1.8c-.5.3-.8.7-.8 1.2V10M8 12.5v.5",
    fit: "M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4", info: "M8 7v5M8 4.5V5M8 15A7 7 0 1 0 8 1a7 7 0 0 0 0 14z",
    expand: "M9 3h4v4M7 13H3V9M13 3L9 7M3 13l4-4", collapse: "M13 7H9V3M3 9h4v4M9 7l4-4M7 9l-4 4",
  };
  return <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name] || ""} /></svg>;
}

function AptUnifiedRoomToolbar({ mode, modeLoading, modeError, sourceLabel, sourceDetail, sourceStatus, stats, disabled, pointsOnlyReason, modeLockedReason, view,
  onMode, onOrbit, onFit, onRehearse, nativePacks, packVersion, packBusy, onPackChange, hasVirtualFixtures, virtualVisible, onVirtualVisible,
  homeId, homeName, hasPerception, fixtureCount, fixtureSelected, about, modeProgress = null, viewLockedReason = null, onDismissNotice,
  lit = null, onLit = null }) {
  const Guide = window.HomeApartmentRoomGuide;
  const [popover, setPopover] = useState(null);
  const [guideDialog, setGuideDialog] = useState(null);
  const popoverRef = useRef(null);
  window.HomeOverlay.useOverlayLayer({ key: "apartment-room-toolbar", active: !!popover, rootRef: popoverRef,
    trap: false, initialFocus: "first", onEscape: () => setPopover(null) });
  const openGuide = (dialog) => { setPopover(null); setGuideDialog(dialog); };
  Guide.useHelpShortcut(!guideDialog, () => openGuide("help"));
  Guide.useFitPopover(popoverRef, popover);
  /* Per-mode loading and recovery words. A saved-view failure keeps the room on
   * the mode it already shows; nothing here switches homes. */
  const APT_MODE_WORDS = { points: "Cloud", splat: "Photo", mesh: "Mesh" };
  const recoveryFor = (error, home = homeName || "this home") => {
    if (!error || typeof error !== "object" || !APT_MODE_WORDS[error.mode] || error.mode === "points") return null;
    const word = APT_MODE_WORDS[error.mode];
    return error.source === "saved-view"
      ? { word, title: "Your saved view didn't load", body: `${word} isn't available here, so the room stays on Cloud. You're still looking at ${home}.` }
      : { word, title: `${word} isn't available here`, body: `You're still looking at ${home}.` };
  };
  const recovery = !modeLoading ? recoveryFor(modeError) : null;
  const photoError = !!recovery && modeError.mode === "splat" && modeError.source !== "saved-view";
  const retryPhotoDisabled = !!(disabled || pointsOnlyReason || modeLockedReason);
  const backToCloudDisabled = !!(disabled || modeLockedReason);
  const loadingPercent = modeLoading && modeProgress?.mode === modeLoading && Number.isFinite(modeProgress.percent) ? modeProgress.percent : null;
  return <div className="apt-ui-room-toolbar" role="toolbar" aria-label="Room view" data-apt-label-avoid="">
    <button type="button" className="apt-ui-toolbar-source hide-small" title={sourceLabel} onClick={() => setPopover(popover === "source" ? null : "source")}><AptUnifiedIcon name="info" /><span>{sourceLabel}</span></button>
    <span className="apt-ui-toolbar-separator hide-small" />
    <div className="apt-ui-seg" role="group" aria-label="Scene mode">{[["points", "Cloud"], ["splat", "Photo"], ["mesh", "Mesh"]].map(([key, label]) =>
      <button type="button" key={key} aria-label={`${label} view`} aria-pressed={mode === key && !lit}
        title={key !== "points" && pointsOnlyReason ? pointsOnlyReason : undefined}
        disabled={disabled || modeLoading === key || (key !== "points" && !!pointsOnlyReason)}
        onClick={() => (key === "mesh" && lit ? onLit?.(false) : onMode(key))}>{label}</button>)}
      <button type="button" aria-label="Lit view" aria-pressed={!!lit}
        title={!onLit ? "Lit needs modeled fixtures in this home" : modeLockedReason || pointsOnlyReason || "Simulated light on the mesh · not photometric"}
        disabled={disabled || !onLit || !!modeLockedReason || !!pointsOnlyReason || modeLoading === "mesh"}
        onClick={() => onLit?.(!lit)}>Lit</button>
    </div><span className="apt-ui-toolbar-separator apt-ui-orbit" />
    <button type="button" className="apt-ui-button is-icon is-small apt-ui-orbit" aria-label="Orbit left" disabled={disabled || !!viewLockedReason}
      title={viewLockedReason || undefined} onClick={() => onOrbit(-1)}><AptUnifiedIcon name="chevL" /></button>
    <span className="apt-home-toolbar-detent apt-ui-value apt-ui-orbit" role="status" aria-live="polite" aria-atomic="true"
      aria-label={`View angle ${view + 1} of 8`}>{view + 1}/8</span>
    <button type="button" className="apt-ui-button is-icon is-small apt-ui-orbit" aria-label="Orbit right" disabled={disabled || !!viewLockedReason}
      title={viewLockedReason || undefined} onClick={() => onOrbit(1)}><AptUnifiedIcon name="chevR" /></button>
    {/* Icon-only, as in the prototype toolbar, so the row fits a 768 px stage. */}
    <button type="button" className="apt-ui-button is-icon is-small hide-small" aria-label="Fit room" disabled={disabled || !!viewLockedReason}
      title={viewLockedReason || "Fit room"} onClick={onFit}><AptUnifiedIcon name="fit" /></button>
    <span className="apt-ui-toolbar-separator hide-small" />
    <div className="apt-ui-help"><button type="button" className="apt-ui-button is-small" aria-label="Layers"
      aria-expanded={popover === "layers"} onClick={() => setPopover(popover === "layers" ? null : "layers")}><AptUnifiedIcon name="layers" /><span className="apt-ui-toolbar-word">Layers</span></button>
      {popover === "layers" && <div ref={popoverRef} role="dialog" aria-label="Room layers">
        <h3 className="apt-ui-heading">Room layers</h3>
        <Guide.LayersPanel homeId={homeId} hasPerception={hasPerception} fixtureCount={fixtureCount}
          seams={hasVirtualFixtures ? { fixtures: { value: virtualVisible, set: onVirtualVisible } } : {}} />
        <button type="button" className="apt-ui-button is-small" onClick={() => setPopover(null)}>Close layers</button>
      </div>}
    </div>
    <div className="apt-ui-help"><button type="button" className="apt-ui-button is-icon is-small" aria-label="Gestures and keys"
      title="Gestures and keys (?)" aria-haspopup="dialog" aria-expanded={guideDialog === "help"}
      onClick={() => openGuide("help")}><AptUnifiedIcon name="help" /></button>
    </div>
    {guideDialog === "help" && <Guide.HelpDialog homeId={homeId} hasPerception={hasPerception}
      onClose={() => setGuideDialog(null)} onAbout={about ? () => setGuideDialog("about") : null}>
      {onRehearse && <button type="button" className="apt-ui-button is-small" onClick={() => { setGuideDialog(null); onRehearse(); }}>Rehearse moving book</button>}
      {nativePacks && <section className="apt-ui-channel"><h3 className="apt-ui-heading">Home pack</h3>
        <button type="button" className="apt-ui-button" disabled={packBusy} onClick={() => { setGuideDialog(null); onPackChange(); }}>Import home pack</button>
        {nativePacks.length > 1 && <select className="apt-ui-input" aria-label="Home pack version" value={packVersion} disabled={packBusy}
          onChange={event => { setGuideDialog(null); onPackChange(event.target.value); }}>{nativePacks.map(pack =>
            <option key={pack.manifest.revision} value={pack.manifest.revision}>{pack.manifest.revision}</option>)}</select>}
      </section>}
    </Guide.HelpDialog>}
    {guideDialog === "about" && about && <Guide.AboutDialog homeName={homeName} facts={Guide.aboutFacts(about)}
      onClose={() => setGuideDialog(null)} />}
    <Guide.Legend homeId={homeId} selected={!!fixtureSelected} hasPerception={hasPerception}
      suppressed={!!(popover || guideDialog || modeLoading || modeError || viewLockedReason)} />
    {popover === "source" && <div ref={popoverRef} className="apt-ui-toolbar-source-detail" role="dialog" aria-label="Scene source">
      <h3 className="apt-ui-heading">Scene source</h3><p className="apt-ui-copy">{sourceDetail || sourceLabel}</p>{sourceStatus}
      <p className="apt-ui-meta">Cloud, Photo and Mesh use this home's existing scene assets.</p>
      {about && <button type="button" className="apt-ui-button is-small" onClick={() => openGuide("about")}>What's real here</button>}
      {stats && <details><summary className="apt-ui-meta">Renderer details</summary><p className="apt-ui-meta">
        {Math.round(stats.fps)} fps · {(stats.points / 1000).toFixed(0)}k points · DPR {stats.pixelRatio}
        {stats.calls != null ? ` · ${stats.calls} draw calls` : ""}{stats.tris ? ` · ${(stats.tris / 1e6).toFixed(1)}m triangles` : ""}
        <br />{String(stats.gpu).slice(0, 48)}
        {mode === "splat" && stats.modeDebug && <><br />Photo {stats.modeDebug.numSplats ? (stats.modeDebug.numSplats / 1000).toFixed(0) + "k" : "—"}
          {` · visible ${String(stats.modeDebug.splatVisible)} · parent ${stats.modeDebug.srParent}`}</>}
      </p></details>}
      <button type="button" className="apt-ui-button is-small" onClick={() => setPopover(null)}>Close source</button></div>}
    {lit && !modeLoading && !modeError && <p className="apt-ui-toolbar-notice" role="note" data-apt-lit-disclosure={lit.quality}
      title={lit.disclosureDetail}>{lit.disclosure}{lit.quality === "reduced" ? " · reduced quality for speed" : ""}
      {lit.bounceLimit && <><br /><span data-apt-lit-bounce-limit="">{lit.bounceLimit}</span></>}
      {lit.lux?.length > 0 && <><br /><span data-apt-lit-lux="">{lit.luxLabel}{lit.lux.slice(0, 3).map(row =>
        ` · ${row.label} ${row.lux >= 100 ? Math.round(row.lux / 10) * 10 : Math.round(row.lux)} lx`).join("")}</span></>}</p>}
    {(modeLoading || modeError) && <div className="apt-ui-toolbar-notice" data-apt-mode-notice={modeLoading ? "loading" : recovery ? modeError.source || "mode" : "message"}>
      {modeLoading ? <><div role="status"><h3>Loading {APT_MODE_WORDS[modeLoading] || modeLoading}{loadingPercent != null ? ` · ${Math.round(loadingPercent)}%` : ""}</h3>
        <p>The current view stays as it is.</p></div>
        {loadingPercent != null && <div className="apt-ui-progress" role="progressbar" aria-label={`Loading ${APT_MODE_WORDS[modeLoading] || modeLoading}`}
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(loadingPercent)}><i style={{ width: `${Math.max(0, Math.min(100, loadingPercent))}%` }} /></div>}</>
        : recovery ? <div role="status"><h3>{recovery.title}</h3><p>{recovery.body}</p></div>
          : <span role="status">{typeof modeError === "string" ? modeError : modeError?.message || "This view didn't load."}</span>}
      {recovery && !photoError && <div className="apt-ui-toolbar-recovery" role="group" aria-label={`${recovery.word} recovery`}>
        <button type="button" className="apt-ui-button is-small" aria-label={`Retry ${recovery.word}`} disabled={retryPhotoDisabled}
          title={modeLockedReason || pointsOnlyReason || undefined}
          onClick={() => { if (!retryPhotoDisabled) onMode(modeError.mode); }}>Retry</button>
        {modeError.source === "saved-view"
          ? <button type="button" className="apt-ui-button is-small" disabled={!onDismissNotice} onClick={() => onDismissNotice?.()}>Stay on Cloud</button>
          : <button type="button" className="apt-ui-button is-small" disabled={backToCloudDisabled} title={modeLockedReason || undefined}
            onClick={() => { if (!backToCloudDisabled) onMode("points"); }}>Back to Cloud</button>}
      </div>}
      {photoError && <div className="apt-ui-toolbar-recovery" role="group" aria-label="Photo recovery">
        <button type="button" className="apt-ui-button is-small" aria-label="Retry Photo" disabled={retryPhotoDisabled}
          title={modeLockedReason || pointsOnlyReason || undefined}
          onClick={() => { if (!retryPhotoDisabled) onMode("splat"); }}>Retry</button>
        <button type="button" className="apt-ui-button is-small" disabled={backToCloudDisabled} title={modeLockedReason || undefined}
          onClick={() => { if (!backToCloudDisabled) onMode("points"); }}>Back to Cloud</button>
      </div>}
    </div>}
  </div>;
}

const APT_AIM_HISTORY_STEPS = 30;
const APT_NO_ECHO_NAMED_AIMS = Object.freeze({ overlay: null, aimTab: null, roomList: null, inspector: null, escape: () => false,
  openNamedAim: () => false, recall: () => null, host: null });
const aptNoEchoNamedAims = () => APT_NO_ECHO_NAMED_AIMS;
const aptUndoShortcut = () => /Mac|iPhone|iPad/.test(navigator.platform || "") ? "⌘Z" : "Ctrl+Z";
const aptVirtualFixtureLit = item => !!(item.state?.spotlight?.on || item.state?.downward?.on || item.state?.radials?.some(radial => radial.on));

// The part of the room view the Home column or phone sheet covers, so the
// aim bar stays reachable beside it.
function aptShellInsets(host) {
  const panel = host?.ownerDocument?.querySelector(".apt-home-shell .apt-home-panel");
  if (!panel) return null;
  const view = host.getBoundingClientRect(), cover = panel.getBoundingClientRect();
  if (!(cover.width > 0 && cover.height > 0) || cover.right <= view.left || cover.left >= view.right
      || cover.bottom <= view.top || cover.top >= view.bottom) return null;
  if (cover.left <= view.left + 1 && cover.right >= view.right - 1) return { bottom: view.bottom - cover.top };
  return cover.left > view.left + view.width / 2 ? { right: view.right - cover.left } : { left: cover.right - view.left };
}
// The presentation asks every frame; measure the shell at most every 250 ms.
function aptShellInsetsReader(host) {
  let measuredAt = -Infinity, value = null;
  return () => {
    const now = performance.now();
    if (now - measuredAt > 250) { measuredAt = now; value = aptShellInsets(host); }
    return value;
  };
}

// Distance and tilt from the registered optical origin, or from the socket
// display point when none is registered, as the room chip measures them.
function aptAimMeasure(descriptor, point) {
  const origin = Array.isArray(descriptor?.optical_origin_m) ? descriptor.optical_origin_m : descriptor?.display_origin_m;
  if (!Array.isArray(origin) || !Array.isArray(point)) return null;
  const ray = point.map((value, axis) => value - origin[axis]), length = Math.hypot(...ray);
  if (!(length > 0)) return null;
  return `${length.toFixed(2)} m · ${Math.round(Math.acos(Math.max(-1, Math.min(1, -ray[2] / length))) * 180 / Math.PI)}° tilt`;
}
const APT_AIM_REASON = { blocked: "Blocked", out_of_reach: "Out of reach", no_surface: "No surface here", not_aimable: "Can’t aim here" };

/* The Aim tab's rows (APT-A05): where the light points now (warm), what is
 * proposed (cyan) or why that spot can't be used, Set aim and Discard, and
 * Tap to aim (APT-A08). */
function AptAimProposalRows({ descriptor, limits, proposal, tapAim, onSet, onDiscard, onTapAim }) {
  const now = descriptor.target?.point, target = proposal?.target;
  const disclosures = [!Array.isArray(descriptor.optical_origin_m) && "Modeled from the socket display point · optical pivot not registered",
    limits?.physical_travel !== "commissioned" && "travel limits not commissioned"].filter(Boolean);
  const swatch = color => <span aria-hidden="true" style={{ flex: "none", width: 9, height: 9, marginTop: 4, borderRadius: "50%",
    border: `2px solid ${color}` }} />;
  return <div data-apt-aim-rows="">
    <div className="apt-ui-row" data-apt-aim-row="current" style={{ alignItems: "flex-start", gap: 8 }}>{swatch("#ffddb0")}
      <span className="apt-ui-grow"><span className="apt-ui-channel-name">Now</span>
        <span className="apt-ui-meta" style={{ display: "block" }}>{now
          ? `${descriptor.target.surface_id || "Room surface"} · ${aptAimMeasure(descriptor, now) || ""}` : "Not aimed at a room surface yet"}</span></span></div>
    {target ? <div className="apt-ui-row" data-apt-aim-row="proposed" data-state={target.valid ? "valid" : "invalid"}
      style={{ alignItems: "flex-start", gap: 8 }}>{swatch(target.valid ? "#45dfff" : "#ff625f")}
      <span className="apt-ui-grow"><span className="apt-ui-channel-name">Proposed</span>
        <span className="apt-ui-meta" style={{ display: "block" }}>{target.valid
          ? `${target.surface || "This spot"} · ${aptAimMeasure(descriptor, target.point_m) || ""}`
          : `${APT_AIM_REASON[target.reason] || APT_AIM_REASON.not_aimable} · only Discard is available`}</span></span></div>
      : <p className="apt-ui-copy" data-apt-aim-empty="">{tapAim ? "Tap a surface in the room." : "Drag the reticle onto a surface."}</p>}
    <div className="apt-ui-actions" role="group" aria-label="Aim proposal">
      {target?.valid && <button type="button" className="apt-ui-button" data-apt-aim-tab-action="set" onClick={onSet}
        style={{ background: "#45dfff", borderColor: "#45dfff", color: "#04161b" }}>Set aim</button>}
      {target && <button type="button" className="apt-ui-button" data-apt-aim-tab-action="discard" onClick={onDiscard}>Discard</button>}
      <button type="button" className="apt-ui-button" aria-pressed={!!tapAim} onClick={onTapAim}>{tapAim ? "Tap a surface…" : "Tap to aim"}</button>
    </div>
    {disclosures.length > 0 && <p className="apt-ui-meta" data-apt-aim-tab-disclosure="">{disclosures.join(" · ")}</p>}
  </div>;
}

// Bulk radial power preserves saved levels unless every zone is fully dark.
function aptUnifiedRadialPowerPatch(radials, on) {
  const restoreLevel = on && radials.length > 0 && radials.every(zone => zone.brightness === 0);
  return radials.map(zone => ({ number: zone.number, on, ...(restoreLevel ? { brightness: .5 } : {}) }));
}

const aptEmptySubscribe = () => () => {};
const aptEmptySnapshot = () => null;
/* Presentation only. The host supplies registered descriptors and its existing guarded callbacks. */
function AptUnifiedVirtualInspector({ homeName, fixtures, fixture, selected, visible, onVisible, onSelect, onBack,
  onPatch, onPatchAll, onUndo, onRedo, canUndo, canRedo, notice, sourceLabel, onSetup, canSetup, deskControls,
  tab: controlledTab, onTabChange, roomGroups, onRoomPower, roomPowerDisabledReason, namedAims, onShowComposer, directionalView, commandBinding,
  aimControls, aimNamedAims }) {
  const radialSnapshot = React.useSyncExternalStore(directionalView?.subscribe || aptEmptySubscribe,
    directionalView?.getSnapshot || aptEmptySnapshot, aptEmptySnapshot);
  const [localTab, setLocalTab] = useState("light");
  const tab = controlledTab ?? localTab, setTab = onTabChange || setLocalTab;
  const [zone, setZone] = useState(null);
  const previousNoticeRef = useRef(notice);
  useEffect(() => { setZone(null); }, [fixture?.fixture_id]);
  useEffect(() => {
    const previous = previousNoticeRef.current;
    previousNoticeRef.current = notice;
    // Returning from Setup remounts this inspector. A retained old aim notice
    // must not override the user's Light/Zones choice on that remount.
    if (notice === previous) return;
    if (/^(Aiming at|Aim updated|No visible room surface|That aim is unavailable|Aim canceled|Aim undone|Aim restored|This aim belongs)/.test(notice || "")) setTab("aim");
  }, [notice]);
  const isLit = item => !!(item.state?.spotlight?.on || item.state?.downward?.on || item.state?.radials?.some(radial => radial.on));
  const visibility = <label className="apt-ui-row apt-ui-meta"><input type="checkbox" checked={visible}
    aria-label="Virtual spotlights" onChange={event => onVisible(event.target.checked)} />Virtual spotlights</label>;
  const list = <ul className="apt-ui-list">{fixtures.map(item => <li key={item.fixture_id}>
    <button type="button" className="apt-ui-list-button" aria-label={`Select ${item.label || item.fixture_id}`}
      aria-current={selected && fixture?.fixture_id === item.fixture_id ? "true" : undefined} onClick={() => onSelect(item.fixture_id)}>
      <span className={`apt-ui-lamp${isLit(item) ? " is-on" : ""}`} aria-hidden="true" />
      <span className="nm">{item.label || item.fixture_id}</span><span className="vl">{isLit(item) ? "On" : "Off"}</span>
    </button></li>)}</ul>;
  if (!selected || !fixture) return <aside className="apt-ui-inspector" data-victoria-virtual-fixtures="1" aria-label="Room lighting inspector">
    <header className="apt-ui-header"><div><h2 className="apt-ui-title">{homeName}</h2>
      <div className="apt-ui-sub">{fixtures.length} fixtures at mapped sockets · {fixtures.filter(isLit).length} lit</div></div>
      <div className="apt-ui-actions"><span className="apt-ui-badge" data-state="modeled">Modeled</span><span className="apt-ui-badge">Not commissioned</span></div>
    </header>
    <section className="apt-ui-section"><div className="apt-ui-row"><h3 className="apt-ui-heading grow">Quick</h3>
      {[true, false].map(on => <button type="button" key={String(on)} className="apt-ui-button is-small" disabled={!fixtures.length}
        onClick={() => onPatchAll(on)}>All {on ? "on" : "off"}</button>)}</div><p className="apt-ui-meta">Modeled channels · nothing is sent to hardware.</p></section>
    {roomGroups && window.AptRoomLightGroups
      ? <window.AptRoomLightGroups groups={roomGroups} onSelect={onSelect} onPower={onRoomPower} disabledReason={roomPowerDisabledReason} />
      : <section className="apt-ui-section"><h3 className="apt-ui-heading">Lights</h3>{list}</section>}
    {namedAims}
    <section className="apt-ui-section">{visibility}<p className="apt-ui-meta">Select a light in the room or list to adjust its channels and aim.</p>
      <p className="apt-ui-meta">{sourceLabel}</p></section>
  </aside>;
  const state = fixture.state || {}, radials = radialSnapshot?.descriptor.fixture_id === fixture.fixture_id
    ? radialSnapshot.descriptor.state.radials : state.radials || [];
  const selectedRadials = zone == null ? radials : radials.filter(item => item.number === zone);
  const radialLevel = selectedRadials.length ? selectedRadials.reduce((sum, item) => sum + (item.brightness ?? 1), 0) / selectedRadials.length : 0;
  const patchRadials = patch => onPatch({ radials: selectedRadials.map(item => ({ number: item.number, ...patch })) });
  // Profile ranges are assumed until the Den confirms them; a moved slider powers its channel on.
  const range = (key, fallback) => Array.isArray(fixture.color_temp_ranges?.[key]) ? fixture.color_temp_ranges[key] : fallback;
  const channel = (key, title, desc, brightnessLabel, temperatureLabel, minimum) => {
    const current = state[key] || {}, [low, high] = range(key, [minimum, 5000]);
    const powered = value => !current.on && value > 0 ? { on: true } : {};
    return <section className="apt-ui-channel" aria-label={`${title} channel`}>
      <div className="apt-ui-channel-head"><div className="apt-ui-grow"><div className="apt-ui-channel-name">{title}</div>
        <div className="apt-ui-channel-desc">{desc}</div></div><label className="apt-ui-switch">
        <input type="checkbox" aria-label={`${title} power`} checked={!!current.on}
          onChange={event => onPatch({ [key]: { on: event.target.checked } })} /><span>{current.on ? "On" : "Off"}</span></label></div>
      <AptUnifiedRange label="Brightness" ariaLabel={brightnessLabel} value={(current.brightness ?? 1) * 100}
        onChange={value => onPatch({ [key]: { brightness: value / 100, ...powered(value) } })} />
      <AptUnifiedRange label="Color temp · assumed range" ariaLabel={temperatureLabel} value={current.color_temp_kelvin ?? 3000}
        min={low} max={high} step={100} suffix=" K" onChange={value => onPatch({ [key]: { color_temp_kelvin: value, ...powered(1) } })} />
      {key === "spotlight" && <div className="apt-ui-row"><span className="apt-ui-meta grow">Where it points is set in Aim.</span>
        <button type="button" className="apt-ui-button is-small" onClick={() => setTab("aim")}>Aim</button></div>}
    </section>;
  };
  return <aside className="apt-ui-inspector" data-victoria-virtual-fixtures="1" aria-label="Fixture lighting inspector">
    <header className="apt-ui-header"><div className="apt-ui-header-row">
      <button type="button" className="apt-ui-button is-icon" aria-label="All lights" onClick={onBack}><AptUnifiedIcon name="back" /></button>
      <div className="apt-ui-grow"><h2 className="apt-ui-title">{fixture.label || fixture.fixture_id}</h2>
        <div className="apt-ui-sub">{homeName} · {isLit(fixture) ? "Light on" : "Light off"}</div></div>
      <button type="button" className="apt-ui-button is-icon" aria-label="Close light" onClick={onBack}><AptUnifiedIcon name="close" /></button></div>
      <div className="apt-ui-actions"><span className="apt-ui-badge" data-state="modeled">Modeled</span><span className="apt-ui-badge">Virtual channels</span></div>
      <div className="apt-ui-seg is-full" role="tablist" aria-label="Light sections">{[["light", "Light"], ["aim", "Aim"], ["setup", "Setup"]].map(([key, label]) =>
        <button key={key} type="button" role="tab" aria-selected={tab === key} aria-controls={`apt-ui-virtual-${key}`} onClick={() => setTab(key)}>{label}</button>)}
      </div>
    </header>
    {tab === "light" && <section className="apt-ui-section" id="apt-ui-virtual-light" role="tabpanel" aria-label="Light channels">
      <div className="apt-ui-row"><span className="apt-ui-meta grow">All channels</span>{[true, false].map(on =>
        <button type="button" key={String(on)} className="apt-ui-button is-small" onClick={() => onPatch({
          spotlight: { on }, downward: { on }, radials: aptUnifiedRadialPowerPatch(radials, on) })}>All {on ? "on" : "off"}</button>)}</div>
      {channel("spotlight", "Spot", "Hidden robotic spotlight", "Brightness", "Color temperature", 2700)}
      {channel("downward", "Down", "Soft pool below the socket", "Down brightness", "Down color temperature", 2200)}
      <section className="apt-ui-channel" aria-label="Radial channel"><div className="apt-ui-channel-head">
        <div className="apt-ui-grow"><div className="apt-ui-channel-name">Radial</div><div className="apt-ui-channel-desc">Six outward zones behind the diffuser film</div></div>
        <label className="apt-ui-switch"><input type="checkbox" aria-label="Radial power" checked={radials.some(item => item.on)}
          onChange={event => onPatch({ radials: aptUnifiedRadialPowerPatch(radials, event.target.checked) })} />
          <span>{radials.some(item => item.on) ? "On" : "Off"}</span></label></div>
        {window.AptDirectionalRing && <window.AptDirectionalRing radials={radials} zone={zone} onZone={setZone} onShow={onShowComposer} view={directionalView}
          power={zone != null && <label className="apt-ui-switch"><input type="checkbox" aria-label={`Radial zone ${zone} power`}
            checked={!!selectedRadials[0]?.on} onChange={event => patchRadials({ on: event.target.checked })} /><span>{selectedRadials[0]?.on ? "On" : "Off"}</span></label>} />}
        <div className="apt-ui-actions"><span className="apt-ui-meta grow">All six zones</span>{[0, 25, 50, 100].map(value => <button type="button" className="apt-ui-button is-small" key={value}
          aria-pressed={value === 0 ? radials.every(item => !item.on || item.brightness === 0)
            : radials.every(item => item.on && item.brightness === value / 100)}
          onClick={() => { setZone(null); onPatch({ radials: radials.map(item => ({ number: item.number, on: value > 0, brightness: value / 100 })) }); }}>{value ? `${value}%` : "Off"}</button>)}</div>
        <div className="apt-ui-actions"><span className="apt-ui-meta grow">Light only</span>{["Toward walls", "Into the room"].map(label =>
          <button key={label} type="button" className="apt-ui-button is-small" disabled title="Available after radial heading commissioning">{label}</button>)}</div>
        <AptUnifiedRange label="Brightness" ariaLabel="Radial brightness" value={radialLevel * 100} onChange={value => patchRadials({ on: value > 0, brightness: value / 100 })} />
        <AptUnifiedRange label="Color temp · assumed range" ariaLabel="Radial color temperature" min={range("radials", [2200, 5000])[0]}
          max={range("radials", [2200, 5000])[1]} step={100} suffix=" K"
          value={selectedRadials[0]?.color_temp_kelvin ?? 3000} onChange={value => patchRadials({ color_temp_kelvin: value })} />
      </section>
      <p className="apt-ui-meta">Virtual preview · no hardware command sent.</p>
    </section>}
    {tab === "aim" && <section className="apt-ui-section" id="apt-ui-virtual-aim" role="tabpanel" aria-label="Fixture aim">
      <h3 className="apt-ui-heading">Spotlight aim</h3>
      {aimControls || <p className="apt-ui-copy">Drag the aim handle onto a visible room surface. Its socket stays fixed.</p>}
      {aimNamedAims}
      {['pan', 'tilt'].map(axis => <AptUnifiedRange key={axis} label={axis === 'pan' ? 'Pan' : 'Tilt'} ariaLabel={axis === 'pan' ? 'Pan' : 'Tilt'} suffix="°"
        min={fixture.limits?.[`${axis}_min_deg`] ?? (axis === 'pan' ? -175 : -5)} max={fixture.limits?.[`${axis}_max_deg`] ?? (axis === 'pan' ? 175 : 88)}
        value={state[`${axis}_deg`] || 0} onChange={value => onPatch({ [`${axis}_deg`]: value })} />)}
      <div className="apt-ui-actions"><button type="button" className="apt-ui-button" disabled={!canUndo} onClick={onUndo}>Undo aim</button>
        <button type="button" className="apt-ui-button" disabled={!canRedo} onClick={onRedo}>Redo aim</button></div>
      {notice && <p role="status" className="apt-ui-copy">{notice}</p>}<p className="apt-ui-meta">Modeled aim · virtual preview only.</p>
      {window.AptCommandLadder && <window.AptCommandLadder site="victoria" fixture={fixture} binding={commandBinding} />}
    </section>}
    {tab === "setup" && <section className="apt-ui-section" id="apt-ui-virtual-setup" role="tabpanel" aria-label="Fixture setup">
      <h3 className="apt-ui-heading">Fixture</h3><select className="apt-ui-input" aria-label="Virtual fixture" value={fixture.fixture_id} onChange={event => onSelect(event.target.value)}>
        {fixtures.map(item => <option key={item.fixture_id} value={item.fixture_id}>{item.label || item.fixture_id}</option>)}
      </select><div className="apt-ui-row"><span className="apt-ui-meta grow">Identity</span><span className="apt-ui-value">{fixture.fixture_id}</span></div>
      <p className="apt-ui-copy">Six radial LED-strip zones, one downward zone and a hidden robotic spotlight inside the white diffuser cylinder.</p>
      {visibility}<button type="button" className="apt-ui-button" disabled={!canSetup} onClick={onSetup}>Edit fixture placement</button>
      <h3 className="apt-ui-heading">Registered desk target</h3>{deskControls}
      {notice && !/^(Drag a fixture|Aiming at|Aim updated|No visible room surface|That aim is unavailable|Aim canceled|Aim undone|Aim restored|This aim belongs)/.test(notice)
        && <p role="status" className="apt-ui-copy">{notice}</p>}
    </section>}
  </aside>;
}

function HomeApartmentLiveView({ open, onClose, endpoint, token, sim, connection = "disconnected", embedded = false, onOpenDynamicReplay, mapRehearsalOpen = false, onCloseMapRehearsal, mapRehearsalError = "", unifiedShell = false, shellControlsHost = null, shellCameraHost = null, onShellState, onShowShellControls, onShowShellAim, onOpenPerceptionLab = null }) {
  const controlConnectionRef = useRef({ connection, endpoint, token, generation: 0 });
  if (controlConnectionRef.current.connection !== connection || controlConnectionRef.current.endpoint !== endpoint
      || controlConnectionRef.current.token !== token) {
    controlConnectionRef.current = { connection, endpoint, token, generation: controlConnectionRef.current.generation + 1 };
  }
  const liveShadow = window.HomeLiveSceneShadow?.active === true;
  const viewport = useAptViewport();
  const mobile = viewport.mobile;
  const hostRef = useRef(null);
  const worldHostRef = useRef(null);
  const worldCreditsRef = useRef(null);
  const emptyInteriorRef = useRef(null);
  const apartmentSceneControlsEnabledRef = useRef(false);
  const viewRootRef = useRef(null);
  const escapeTriggerRef = useRef(null);
  const escapeOpenersRef = useRef({});
  const escapeOperationsRef = useRef({});
  const escapeFocusFrameRef = useRef(null);
  const viewOpenRef = useRef(open);
  viewOpenRef.current = open;
  const engineRef = useRef(null);
  const worldSurfaceRef = useRef(null);
  const worldScaleObserverCleanupRef = useRef(null);
  const worldSurfacePendingRef = useRef(null);
  const worldSurfacePromiseRef = useRef(null);
  const worldEntryPrewarmRef = useRef(null);
  const worldEntryPrewarmPromiseRef = useRef(null);
  const confirmedWorldAnchorRef = useRef(null);
  const worldAnchorKeyRef = useRef("");
  const spatialCoordinatorRef = useRef(null);
  const restoredWorldScaleRef = useRef(null);
  const spatialCoordinatorPromiseRef = useRef(null);
  const spatialLifecycleRevisionRef = useRef(0);
  const spatialIntentRef = useRef(0);
  const spatialTravelIntentRef = useRef(0);
  const worldOverviewAbortRef = useRef(null);
  const zoomRequestRef = useRef(null);
  const boundaryMomentumRef = useRef(null);
  const boundaryFrameRef = useRef(0);
  const boundaryLastFrameRef = useRef(0);
  const boundaryWheelRef = useRef(null);
  /* The world host's own wheel handler while the world takes input, else
   * null. Overlays above the world (projected home buttons, the recovery
   * card's buttons) hand their wheel events to it; see forwardWheelToWorld. */
  const worldWheelHandlerRef = useRef(null);
  const zoomInButtonRef = useRef(null);
  const zoomOutButtonRef = useRef(null);
  const emptyInteriorExitPendingRef = useRef(false);
  const semanticDeviceSelectRef = useRef(null);
  const pendingSpatialFocusRef = useRef(null);
  const pendingSpatialFocusElementRef = useRef(null);
  const detachRef = useRef(null);
  const flyTimerRef = useRef(null);     // pending feed-reveal timeout
  const flySeqRef = useRef(0);
  const preSnapModeRef = useRef(null);  // render mode to restore after a camera snap
  const [phase, setPhase] = useState("boot");
  const [error, setError] = useState(null);
  const [progress, setProgress] = useState({ loaded: 0, total: 0 });
  const [stats, setStats] = useState(null);
  const [mode, setMode] = useState("points");
  const [modeLoading, setModeLoading] = useState(null);
  const [litState, setLitState] = useState(null);
  const [modeError, setModeError] = useState(null);
  const [modeProgress, setModeProgress] = useState(null);   // { mode, percent } for the loading notice
  const modeRequestRef = useRef(null);
  /* The loaded interior's disclosed placeholder (engine.scenePlaceholder), or
   * null for a real scan. Drives the "scan pending" badge and points-only. */
  const [interiorPlaceholder, setInteriorPlaceholder] = useState(null);
  const [toast, setToast] = useState(null);
  const [spatialScale, setSpatialScale] = useState("apartment");
  const [spatialTransition, setSpatialTransition] = useState("idle");
  const [spatialTransitionDirection, setSpatialTransitionDirection] = useState(null);
  /*
   * The single answer to "does the Apartment own the viewport right now".
   *
   * `spatialScale` alone is the wrong signal, and it is the signal every guard
   * used to key on. The coordinator does not flip it until activate(), so for
   * the entire outward handoff it still reads "apartment": the host keeps
   * pointerEvents auto, the orbit input stays attached, and a user can drag the
   * scene around while it is collapsing to a 4px point behind a black firewall.
   * The transition phase is what closes that window.
   */
  const apartmentOwnsInput = spatialScale === "apartment" && spatialTransition === "idle";
  /* The registry is the authority; this mirrors it for rendering only. */
  const [homeSites, setHomeSites] = useState(() => {
    try { return window.HomeSpatialViewport?.getSites?.() || []; } catch (_) { return []; }
  });
  const [selectedSiteId, setSelectedSiteId] = useState(() => {
    try { return window.HomeSpatialViewport?.getSelectedSiteId?.() || null; } catch (_) { return null; }
  });
  const [travelling, setTravelling] = useState(false);
  const [journeyRecovery, setJourneyRecovery] = useState(null);
  const [surfaceRecovery, setSurfaceRecovery] = useState(null);
  const [worldOverviewActive, setWorldOverviewActive] = useState(false);
  const journeyRetryRef = useRef(null);
  const loadedInteriorSiteRef = useRef(null);
  const pendingInteriorRef = useRef(null);
  const interiorSessionRef = useRef(null);
  const interiorSwitchRequestRef = useRef(null);
  const modelSiteRef = useRef(null);
  const modelScopeRef = useRef(null);
  const workspaceStateRef = useRef(null);
  const workspaceAuthorityRef = useRef({ key: null });
  const [interiorSwitchState, setInteriorSwitchState] = useState({ phase: "idle" });
  const [contentPackBusy, setContentPackBusy] = useState(false);
  const [contentPacks, setContentPacks] = useState([]);
  /* The hero still currently armed, or null. Held here so the layer and the
   * debug entry points share one source of truth. */
  const [heroStill, setHeroStill] = useState(null);
  /* Read by the download entry point, which must see the current still without
   * being a dependency of anything that re-renders. */
  const heroStillRef = useRef(null);
  useEffect(() => { heroStillRef.current = heroStill; }, [heroStill]);
  const travellingRef = useRef(false);
  /*
   * Where each home currently sits on screen, as viewport fractions.
   *
   * Fractions rather than coordinates on purpose: the registry owns the
   * latitudes, the surface does the projection, and what reaches the DOM is a
   * position in the frame. A marker never carries a location.
   */
  /*
   * Whether the selected home has an interior to arrive in, and what to call
   * it. Null while the registry has not resolved, so the empty state is shown
   * only on a definite absence rather than on a not-yet-known.
   */
  const worldZoomEnabled = aptWorldZoomEnabled();
  const selectedSiteEntry = aptSelectedApartmentSite(
    homeSites,
    selectedSiteId,
    worldZoomEnabled,
  );
  const selectedSiteInterior = selectedSiteEntry ? selectedSiteEntry.sceneAvailable === true
    && loadedInteriorSiteRef.current === selectedSiteEntry.id
    && interiorSwitchState.phase !== "loading" && interiorSwitchState.phase !== "error" : null;
  const selectedSiteName = selectedSiteEntry?.shortName || "this home";
  // APT-P01: the camera dock follows the home's perception capability, not
  // its site id. A site record's home-den-capabilities-1 block wins; without
  // one the dock model's interim default for that home applies.
  const siteCapabilities = selectedSiteEntry?.capabilities || window.HomeSpatialViewport?.getSite?.(selectedSiteId)?.capabilities || null;
  const perceptionCapability = window.HomeApartmentPerceptionDockModel?.perceptionCapability({ siteId: selectedSiteId, capabilities: siteCapabilities })
    || { variant: "none", source: "interim_default" };
  const perceptionDockAvailable = perceptionCapability.variant === "exact-frame" && !!window.HomeApartmentDenPerception;
  const selectedContentRevision = selectedSiteEntry?.contentRevision || null;
  const apartmentSceneControlsEnabled = !liveShadow && apartmentOwnsInput && phase === "ready" && selectedSiteInterior === true;
  // The current HA model belongs to Echo. Importing a scan grants no control
  // binding for another home; that requires its own reviewed integration.
  apartmentSceneControlsEnabledRef.current = apartmentSceneControlsEnabled;
  const apartmentSceneUnavailableReason = selectedSiteInterior === false
    ? `${selectedSiteName} interior is not uploaded`
    : null;
  /* A DISCLOSED placeholder interior (a generic massing model standing in for
   * a scan that does not exist yet) is a real, enterable scene -- unlike the
   * missing-interior panel above -- but it must never pass for the home: it is
   * named on screen and offers the point cloud only (scene-placeholder.mjs). */
  const interiorPlaceholderShown = selectedSiteInterior === true && !!interiorPlaceholder;
  const interiorPointsOnlyReason = interiorPlaceholderShown
    ? `${selectedSiteName} · scan pending: point cloud only`
    : null;
  /*
   * Which alternatives the world view is built from.
   *
   * Held as ids rather than resolved presets: the resolved objects come from an
   * ES module the renderer owns, and duplicating their numbers here is exactly
   * how an option ends up meaning one thing in the panel and another on screen.
   */
  const [worldViewSelection, setWorldViewSelection] = useState(() => aptReadWorldView());
  const [worldViewGroups, setWorldViewGroups] = useState(null);
  const [worldViewPanelOpen, setWorldViewPanelOpen] = useState(false);
  const worldViewKeyRef = useRef("");
  /* The resolved presets, for consumers outside React's render tree -- the
   * coordinator adapters run in a closure created once and cannot read state. */
  const worldViewBundleRef = useRef(null);
  /* Installed only when a measurement asks for it; it wraps the page's network
   * entry points, which is not something a viewport should do by default. */
  const providerTrafficRef = useRef(null);
  const providerTrafficPromiseRef = useRef(null);
  const handoffCompositorRef = useRef(null);
  const handoffCompositorPendingRef = useRef(null);
  const handoffCompositorPromiseRef = useRef(null);
  const handoffCompositorGenerationRef = useRef(0);
  const handoffFallbackRef = useRef(null);
  /* A cold prewarm miss opts one intent into the bounded host-owned black cut.
   * Identity, rather than a boolean, prevents a cancelled intent from changing
   * the visual path of the newer request that superseded it. */
  const coldWorldEntryIntentRef = useRef(null);
  /* Returning from the world has the same finite capture contract. A separate
   * token keeps an inward capture miss from changing any later outward intent. */
  const coldApartmentEntryIntentRef = useRef(null);
  /* Provider creation can outlive the 500ms readiness budget. This token keeps
   * a late factory result tied to one viewport lifecycle, site revision, and
   * transition signal; the surface supplies the final mutation generation. */
  const worldImageryActivationRef = useRef(null);
  const worldImageryActivationGenerationRef = useRef(0);
  const worldImageryPromotedIntentRef = useRef(null);
  /* Explicit wheel/pinch/travel may adopt a provider that finished staging
   * after the Apartment firewall. The adopter owns coalescing and cancellation;
   * this ref only exposes it to input handlers outside the coordinator closure. */
  const worldImageryAdopterRef = useRef(null);
  /* A timed-out/failed optional GLB must not block the local handoff. The next
   * successfully activated world intent gets one fresh, bounded reveal gate;
   * the surface owns registration/load generations underneath it. */
  const worldDollhouseRetryIntentRef = useRef(null);
  /* The one live canonical handoff clock. Gesture handlers retarget this
   * object; they never cancel/restart the renderer animation mid-frame. */
  const handoffMotionRef = useRef(null);
  /* Lets the outer-detent controller distinguish a visual inverse that already
   * reached progress 0 from a failed preparation that still needs spring-back. */
  const handoffSourceReversalRef = useRef(null);
  const [azIdx, setAzIdx] = useState(1);
  const [editing, setEditing] = useState(false);
  const [editorTargetEntry, setEditorTargetEntry] = useState(null);
  useEffect(() => { if (!editing) setEditorTargetEntry(null); }, [editing]);
  const [aimMode, setAimMode] = useState(false);
  const [aimFixtureId, setAimFixtureId] = useState(null);
  const [aimDestination, setAimDestination] = useState(null);
  const [model, setModel] = useState(window.HomeApartmentData.EMPTY_MODEL);
  const [seedModel, setSeedModel] = useState(null);
  const [registry, setRegistry] = useState({ entities: [], areas: [], devices: [], states: {} });
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState({ state: "idle", detail: "not edited" });
  const [liveReview, setLiveReview] = useState({ state: "idle", live: null, detail: "" });
  const [track, setTrack] = useState(null);          // primary person track
  const [trackerStatus, setTrackerStatus] = useState("connecting");
  const [servicePulse, setServicePulse] = useState(0);
  const [apartmentSemanticNavigation, setApartmentSemanticNavigation] = useState(
    EMPTY_APARTMENT_SEMANTIC_NAVIGATION,
  );
  const [semanticRoomId, setSemanticRoomId] = useState("");
  const [semanticDeviceId, setSemanticDeviceId] = useState(null);
  const [semanticRoomStatus, setSemanticRoomStatus] = useState("");
  const [semanticStatusDeviceId, setSemanticStatusDeviceId] = useState(null);
  const [hoverId, setHoverId] = useState(null);
  const [quickBusy, setQuickBusy] = useState(false);
  const quickBusyRef = useRef(false);
  const [cardId, setCardId] = useState(null);
  const [inCamPose, setInCamPose] = useState(false);
  const [liveCam, setLiveCam] = useState(null);   // camera device while snapped
  const [liveOn, setLiveOn] = useState(false);    // live feed vs 3D from the pose
  const [liveFeedStatus, setLiveFeedStatus] = useState("idle");
  const [cameraPoseReady, setCameraPoseReady] = useState(false);
  const [calibCam, setCalibCam] = useState(null); // correspondence-capture overlay
  const dollhouseContextRef = useRef(null);
  dollhouseContextRef.current = { liveOn, liveCam, calibCam };
  useEffect(() => {
    aptSyncDollhouseContext(engineRef.current, dollhouseContextRef.current);
  }, [phase, selectedSiteId, selectedContentRevision, liveOn, liveCam, calibCam]);
  const calibPickRef = useRef(false);
  const calibApiRef = useRef(null);
  const calibPointsRef = useRef(null);
  const calibPairCountRef = useRef(0);  // banked correspondence pairs (Esc discard guard)
  const calibEscArmRef = useRef(0);     // timestamp of the arming Esc; 0 = disarmed
  const [anchors, setAnchors] = useState({});        // id -> {x, y, visible}
  const [hostSize, setHostSize] = useState({ width: 0, height: 0 });
  const statesRef = useRef({});                      // entity_id -> ha state
  const simActive = !!(sim && sim.active);
  workspaceAuthorityRef.current.key = liveShadow
    ? 'live-resident:' + window.HomeLiveSceneShadow.resident_binding?.digest
    : window.HomeApartmentData.ensureWorkspaceAuthority?.({ endpoint, token, simulation: simActive })?.key;
  const apartmentAuthorityKey = workspaceAuthorityRef.current.key;
  const apartmentDeviceControlsEnabled = apartmentSceneControlsEnabled && selectedSiteEntry?.id === "echo"
    && modelScopeRef.current?.authorityKey === workspaceAuthorityRef.current.key;
  workspaceStateRef.current = { model, dirty: ["unsaved", "saving", "validation", "offline", "conflict", "simulation"].includes(saveStatus.state) };
  const rehearsalIsolated = liveShadow || mapRehearsalOpen || window.HomeMapRehearsalOfflineState?.active === true;
  const apartmentPlacementDisplayEnabled = apartmentSceneControlsEnabled && !rehearsalIsolated
    && modelScopeRef.current?.authorityKey === workspaceAuthorityRef.current.key
    && modelSiteRef.current === selectedSiteId
    && (selectedSiteId === "echo" || selectedSiteId === "victoria" && !!model._equipment_registration);
  const apartmentPlacementControlsEnabled = apartmentPlacementDisplayEnabled
    && (selectedSiteId === "echo" || model.source_detail?.read_only === false);
  const syntheticSpatialQa = aptUseSyntheticSpatialQa(
    simActive,
    aptSyntheticSpatialQaEnabled(),
  );
  const [serviceEpoch, setServiceEpoch] = useState(0);
  const [equipmentReloadEpoch, setEquipmentReloadEpoch] = useState(0);
  const [virtualSpotlightsVisible, setVirtualSpotlightsVisible] = useState(true);
  const [virtualFixturePreview, setVirtualFixturePreview] = useState(null);
  const [virtualFixtureId, setVirtualFixtureId] = useState("");
  const [virtualFixtureChannel, setVirtualFixtureChannel] = useState("spotlight");
  const [virtualFixtureControlsOpen, setVirtualFixtureControlsOpen] = useState(false);
  const [virtualInspectorTab, setVirtualInspectorTab] = useState("aim");
  const [fixtureInteractionMode, setFixtureInteractionMode] = useState("spot");
  const fixtureInteractionModeRef = useRef("spot");
  fixtureInteractionModeRef.current = fixtureInteractionMode;
  const radialGestureOwnerRef = useRef(null);
  const virtualFixturePreviewDirtyRef = useRef(false);
  const publishVirtualFixturePreviewRef = useRef(null);
  const directionalProjectionRef = useRef(null);
  const directionalViewRef = useRef(null);
  const [directionalView, setDirectionalView] = useState(null);
  // APT-P01: one dock presentation state (collapsed, default, expanded or
  // hidden) and one detail level, kept across homes for this view.
  const [unifiedDock, setUnifiedDock] = useState(() => {
    try { return window.HomeApartmentPerceptionDockModel?.normalizeDockState(localStorage.getItem("apt.dock.state")) || "default"; } catch (_) { return "default"; }
  });
  const [unifiedDockDetail, setUnifiedDockDetail] = useState(() => {
    try {
      const fromUrl = new URLSearchParams(window.location.search || "").get("detail");
      return window.HomeApartmentPerceptionDockModel?.normalizeDetail(fromUrl || localStorage.getItem("apt.dock.detail")) || "simple";
    } catch (_) { return "simple"; }
  });
  const [virtualAimNotice, setVirtualAimNotice] = useState("");
  const [virtualAimHistoryVersion, setVirtualAimHistoryVersion] = useState(0);
  const virtualAimHistoryRef = useRef({ undo: [], redo: [] });
  const virtualAimGestureRef = useRef(null);
  const virtualFixtureControlsOpenRef = useRef(false);
  virtualFixtureControlsOpenRef.current = virtualFixtureControlsOpen;
  // In-memory aim proposals (home-apartment-aim-proposals.js) and the room
  // overlay that shows and edits them (home-apartment-room-overlay.jsx).
  const aimProposalStoreRef = useRef(null);
  if (!aimProposalStoreRef.current && window.HomeApartmentAimProposals?.createAimProposalStore)
    aimProposalStoreRef.current = window.HomeApartmentAimProposals.createAimProposalStore();
  const aimProposals = aimProposalStoreRef.current;
  const echoNamedAimsRef = useRef(null);
  React.useSyncExternalStore(aimProposals?.subscribe || aptEmptySubscribe,
    aimProposals?.getSnapshot || aptEmptySnapshot, aimProposals?.getSnapshot || aptEmptySnapshot);
  const [aimPresentation, setAimPresentation] = useState(null);
  const aimPresentationRef = useRef(null);
  const aimActionsRef = useRef(null);
  const [hoverFixtureId, setHoverFixtureId] = useState(null);
  const [tapAim, setTapAim] = useState(null);
  const [deskTargetSelection, setDeskTargetSelection] = useState(null);
  const deskTargetCaptureRef = useRef(null);
  const virtualFixtureStatesRef = useRef({ key: null, states: new Map(), targets: new Map() });
  const denDeskAimPreviewRef = useRef(null);
  const registerDenDeskAimPreview = useCallback((controller, mounted) => {
    if (mounted) denDeskAimPreviewRef.current = controller;
    else if (denDeskAimPreviewRef.current === controller) denDeskAimPreviewRef.current = null;
  }, []);
  const refreshDenDeskAimPreview = useCallback((engine) => {
    if (engineRef.current === engine) setVirtualFixturePreview(engine?.overlay.virtualFixturesReadback?.() || null);
  }, []);
  // AL-4: the den's virtual fixtures follow the central lighting brain.
  const lightingBrainRef = useRef(null);
  const [lightingBrain, setLightingBrain] = useState(null);
  const [denOpsBanner, setDenOpsBanner] = useState(null);
  const [targetMarkKind, setTargetMarkKind] = useState("desk");
  const virtualFixtureDocument = selectedSiteId === "victoria" && apartmentPlacementDisplayEnabled
    && !rehearsalIsolated && model._equipment_registration?.scope?.site_id === "victoria"
    && model._equipment_registration?.binding?.content_revision === selectedContentRevision
    && model._equipment_registration?.binding?.registration_version === (selectedSiteEntry?.sceneRegistration?.version ?? 0)
    && model._equipment_registration?.binding?.frame_digest === modelScopeRef.current?.frameDigest
    ? model._equipment_registration : null;
  const virtualFixtureItems = virtualFixturePreview?.fixtures || [];
  const selectedVirtualFixture = virtualFixtureItems.find((fixture) => fixture.fixture_id === virtualFixtureId)
    || virtualFixtureItems[0] || null;
  const selectedVirtualChannel = virtualFixtureChannel.startsWith("radial-")
    ? selectedVirtualFixture?.state?.radials.find((zone) => zone.number === Number(virtualFixtureChannel.slice(7)))
    : selectedVirtualFixture?.state?.[virtualFixtureChannel];
  const virtualFixtureBindingKey = virtualFixtureDocument
    ? JSON.stringify([apartmentAuthorityKey, virtualFixtureDocument.binding]) : null;
  const modePrewarmRef = useRef(null);
  const sourceMeta = liveShadow ? { kind: 'live_shadow', label: 'registered shadow model' } : window.HomeApartmentData.modelSourceMeta?.(model)
    || { kind: simActive ? "simulation" : "empty", label: simActive ? "Simulation" : "No model" };
  const liveComparison = useMemo(() => (
    liveReview.state === "ready" && liveReview.live
      ? window.HomeApartmentData.compareApartmentModels?.(model, liveReview.live) || null
      : null
  ), [model, liveReview]);
  const semanticDeviceRoomById = useMemo(() => {
    const roomByDevice = new Map();
    for (const device of model.devices || []) {
      const id = typeof device?.id === "string" ? device.id.trim() : "";
      const roomId = typeof device?.room_id === "string" ? device.room_id.trim() : "";
      if (id && !roomByDevice.has(id)) roomByDevice.set(id, roomId);
    }
    return roomByDevice;
  }, [model.devices]);
  const semanticDevicesForRoom = useMemo(() => {
    const devices = apartmentSemanticNavigation.devices || [];
    if (!semanticRoomId) return devices;
    return devices.filter((device) => semanticDeviceRoomById.get(device.id) === semanticRoomId);
  }, [apartmentSemanticNavigation.devices, semanticDeviceRoomById, semanticRoomId]);

  useEffect(() => {
    const bump = () => setServiceEpoch((n) => n + 1);
    window.addEventListener("home-services-change", bump);
    return () => window.removeEventListener("home-services-change", bump);
  }, []);

  const showToast = useCallback((text, opts) => {
    const sticky = !!(opts && (opts.sticky || opts.tone === "error"));
    // Actions delegate to an existing caller-owned operation and its currentness
    // check. A function without that guard is never exposed as an action.
    const action = typeof opts?.action === "function" && typeof opts?.isCurrent === "function"
      ? window.HomeApartmentUI.createToastAction(opts.action, { isCurrent: opts.isCurrent }) : null;
    setToast({ text, sticky, timeoutMs: opts?.timeoutMs, actionLabel: opts?.actionLabel, action });
  }, []);
  const dismissToast = useCallback((dismissed) => setToast(current => current === dismissed ? null : current), []);

  const cancelSpatialTravel = useCallback(async () => {
    worldOverviewAbortRef.current?.abort();
    worldOverviewAbortRef.current = null;
    const active = travellingRef.current
      || (spatialCoordinatorRef.current?.getState?.()?.phase ?? "idle") !== "idle";
    const ownedModeRequest = modeRequestRef.current;
    ++spatialTravelIntentRef.current;
    if (ownedModeRequest && modeRequestRef.current === ownedModeRequest) {
      modeRequestRef.current = null;
      setModeLoading(null);
    }
    worldSurfaceRef.current?.cancelNavigation?.("user-cancelled");
    travellingRef.current = false;
    setTravelling(false);
    await spatialCoordinatorRef.current?.cancel?.();
    return active;
  }, []);
  useEffect(() => {
    if (!mapRehearsalOpen) return;
    setEditing(false); setAimMode(false); setAimDestination(null); setCalibCam(null); setLiveCam(null); setLiveOn(false); setCardId(null);
    ++spatialIntentRef.current;
    void cancelSpatialTravel();
  }, [mapRehearsalOpen, cancelSpatialTravel]);

  /* Mounting is shared by speculative preparation and committed entry. The
   * promise is deliberately retained after success: neither path may allocate
   * a second Cesium renderer while the first one is still becoming ready. */
  const ensureWorldSurface = useCallback(async () => {
    const engine = engineRef.current;
    const worldHost = worldHostRef.current;
    if (!engine || !worldHost) throw new Error("Apartment viewport is not ready");
    const lifecycleRevision = spatialLifecycleRevisionRef.current;
    const requireCurrentLifecycle = () => {
      if (spatialLifecycleRevisionRef.current !== lifecycleRevision) {
        const error = new Error("Apartment spatial viewport lifecycle changed");
        error.name = "AbortError";
        throw error;
      }
    };

    if (worldSurfaceRef.current) return worldSurfaceRef.current;
    let surfacePromise = worldSurfacePromiseRef.current;
    if (!surfacePromise) {
      surfacePromise = (async () => {
        const next = await engine.createWorldSurface();
        worldSurfacePendingRef.current = next;
        try {
          requireCurrentLifecycle();
          await next.mount(worldHost, { creditContainer: worldCreditsRef.current });
          requireCurrentLifecycle();
          if (worldViewBundleRef.current?.resolved) {
            next.setViewOptions?.(worldViewBundleRef.current.resolved);
          }
          worldSurfaceRef.current = next;
          return next;
        } catch (error) {
          next.dispose?.();
          throw error;
        } finally {
          if (worldSurfacePendingRef.current === next) worldSurfacePendingRef.current = null;
        }
      })();
      worldSurfacePromiseRef.current = surfacePromise;
    }
    try {
      const surface = await surfacePromise;
      requireCurrentLifecycle();
      return surface;
    } catch (error) {
      if (worldSurfacePromiseRef.current === surfacePromise) {
        worldSurfacePromiseRef.current = null;
      }
      throw error;
    }
  }, []);

  const ensureSpatialCoordinator = useCallback(async (confirmedAnchor) => {
    // Keep the lifecycle adapter stable while allowing a newly confirmed site
    // revision to replace the anchor used on the next Apartment -> world entry.
    // Calls which only need the already-created coordinator must not erase the
    // last confirmed anchor while an inter-home journey is still in flight.
    if (confirmedAnchor !== undefined) confirmedWorldAnchorRef.current = confirmedAnchor;
    if (spatialCoordinatorRef.current) return spatialCoordinatorRef.current;
    if (spatialCoordinatorPromiseRef.current) return spatialCoordinatorPromiseRef.current;
    const engine = engineRef.current;
    if (!engine) throw new Error("Apartment viewport is not ready");
    const lifecycleRevision = spatialLifecycleRevisionRef.current;
    const requireCurrentLifecycle = () => {
      if (spatialLifecycleRevisionRef.current !== lifecycleRevision) {
        const error = new Error("Apartment spatial viewport lifecycle changed");
        error.name = "AbortError";
        throw error;
      }
    };

    const promise = (async () => {
      const surface = await ensureWorldSurface();
      requireCurrentLifecycle();

      const applyAnchor = () => {
        const activeAnchor = confirmedWorldAnchorRef.current;
        if (!activeAnchor) throw new Error("Apartment world anchor is unavailable");
        const site = syntheticSpatialQa
          ? null
          : window.HomeSpatialViewport?.getSelectedSite?.();
        const registration = site?.dollhouseRegistration ?? null;
        const key = aptWorldAnchorKey(activeAnchor, registration);
        if (worldAnchorKeyRef.current === key) return;
        surface.setAnchor(activeAnchor);
        surface.setDollhouseRegistration?.(registration);
        worldAnchorKeyRef.current = key;
      };

      /*
       * The reversible handoff, driven from ONE loop.
       *
       * The compositor deliberately owns no rAF: it does not own the progress
       * clock, and a second loop would sample it in a different task from the
       * Apartment's collapse transform and the world's fade. All three come out
       * of the same handoffFrame() here, so what noCoexistenceViolations()
       * proves on the curves is what actually paints.
       *
       * Nothing in here is allowed to fail the handoff. A missing compositor, a
       * dead GPU and a slow capture all still play the full timeline; only the
       * user's own reduced-motion setting takes the short cut.
       */
      const runHandoff = async (context, direction) => {
        /* Product-safe diagnostics: phase and timing only. No coordinates, no
         * address, no tile URL, no key. */
        const handoffDebug = { direction, frames: 0, durationMs: null, compositor: null };
        window.__aptHandoffDebug = handoffDebug;
        const apartmentHost = hostRef.current;
        const worldHost = worldHostRef.current;
        const compositor = handoffCompositorRef.current;
        /*
         * The handoff preset overrides the coordinator's duration.
         *
         * The coordinator carries one number for every surface transition; the
         * panel is choosing the TEMPO of this particular one. Reading the preset
         * here is what makes 300 / 420 / 650 real rather than three labels.
         */
        const presetMs = worldViewBundleRef.current?.resolved?.handoff?.durationMs;
        const coordinatorDurationMs = Math.max(0, Number(context.durationMs) || 0);
        /* Reduced motion is a 120ms semantic cut owned by the coordinator. A
         * visual tempo preset must never turn that accessibility path back into
         * a 300/420/650ms particle animation. */
        const reducedMotion = aptPrefersReducedMotion() || coordinatorDurationMs === 120;
        const blackCutOnly = reducedMotion;
        const durationMs = reducedMotion
          ? coordinatorDurationMs
          : Number.isFinite(presetMs)
            ? presetMs
            : coordinatorDurationMs;
        /* A wheel can already have pulled the Apartment through the reversible
         * preview band. Continue from that exact canonical progress instead of
         * jumping back to a full-size first frame when the snap commits. */
        const boundaryState = direction === "outward"
          ? boundaryMomentumRef.current?.snapshot?.()
          : null;
        const startProgress = boundaryState?.phase === "triggered"
          ? Math.max(0, Math.min(0.99, Number(boundaryState.progress) || 0))
          : 0;
        const startVelocity = boundaryState?.phase === "triggered"
          ? Number(boundaryState.progressVelocity) || 0
          : 0;
        const startAcceleration = boundaryState?.phase === "triggered"
          ? Number(boundaryState.progressAcceleration) || 0
          : 0;
        const view = typeof window !== "undefined" ? window : null;
        handoffDebug.durationMs = durationMs;
        handoffDebug.startProgress = direction === "inward" ? 1 : startProgress;
        handoffDebug.startVelocity = direction === "outward" ? startVelocity : 0;
        handoffDebug.startAcceleration = direction === "outward" ? startAcceleration : 0;
        handoffDebug.reversals = [];
        handoffDebug.compositor = compositor ? compositor.snapshot() : null;

        /* The compositor's DOM survives a WebGL failure and already paints a
         * black firewall. This host-owned layer covers the still rarer case
         * where the compositor module itself cannot be created, and it is also
         * the intentional 120ms reduced-motion path. Chrome remains above it;
         * only the spatial viewport changes owner. */
        const runBlackCut = async () => {
          const overlay = handoffFallbackRef.current;
          if (!overlay || !view?.requestAnimationFrame || durationMs <= 0) {
            if (overlay) {
              overlay.style.visibility = "visible";
              overlay.style.opacity = "1";
            }
            try { await aptAbortableDelay(durationMs, context.signal); }
            finally {
              if (overlay) {
                overlay.style.opacity = "0";
                overlay.style.visibility = "hidden";
              }
            }
            return context.signal.aborted ? "aborted" : "complete";
          }
          overlay.style.visibility = "visible";
          overlay.style.opacity = "0";
          const ease = (value) => {
            const t = Math.max(0, Math.min(1, value));
            return t * t * (3 - 2 * t);
          };
          return new Promise((resolve) => {
            let frameId = 0;
            let startedAt = null;
            let settled = false;
            const finish = (status) => {
              if (settled) return;
              settled = true;
              if (frameId) view.cancelAnimationFrame(frameId);
              context.signal.removeEventListener("abort", onAbort);
              const apartmentWasCommitted = context.fromSurface === "apartment";
              if (status === "aborted") {
                apartmentHost.style.opacity = apartmentWasCommitted ? "1" : "0";
                worldHost.style.opacity = apartmentWasCommitted ? "0" : "1";
              }
              apartmentHost.style.removeProperty("transform");
              overlay.style.opacity = "0";
              overlay.style.visibility = "hidden";
              resolve(status);
            };
            const onAbort = () => finish("aborted");
            const step = (at) => {
              frameId = 0;
              if (context.signal.aborted) { finish("aborted"); return; }
              if (startedAt === null) startedAt = at;
              const phase = Math.max(0, Math.min(1, (at - startedAt) / durationMs));
              const fadingOut = ease(phase * 2);
              const fadingIn = ease((phase - 0.5) * 2);
              const sourceOpacity = phase < 0.5 ? 1 - fadingOut : 0;
              const destinationOpacity = phase < 0.5 ? 0 : fadingIn;
              const blackOpacity = phase < 0.5 ? fadingOut : 1 - fadingIn;
              if (direction === "outward") {
                apartmentHost.style.opacity = String(sourceOpacity);
                worldHost.style.opacity = String(destinationOpacity);
                if (!blackCutOnly) {
                  apartmentHost.style.transform = `scale(${Math.max(0.0019, 1 - 0.9981 * fadingOut)})`;
                }
              } else {
                worldHost.style.opacity = String(sourceOpacity);
                apartmentHost.style.opacity = String(destinationOpacity);
                if (!blackCutOnly && phase >= 0.5) {
                  apartmentHost.style.transform = `scale(${0.0019 + 0.9981 * fadingIn})`;
                }
              }
              overlay.style.opacity = String(blackOpacity);
              handoffDebug.frames += 1;
              handoffDebug.lastProgress = direction === "outward" ? phase : 1 - phase;
              if (phase >= 1) { finish("complete"); return; }
              frameId = view.requestAnimationFrame(step);
            };
            context.signal.addEventListener("abort", onAbort, { once: true });
            frameId = view.requestAnimationFrame(step);
          });
        };

        if (!apartmentHost || !worldHost || !view?.requestAnimationFrame || durationMs === 0) {
          handoffDebug.skipped = !apartmentHost ? "no-host"
            : !worldHost ? "no-world-host"
            : !view?.requestAnimationFrame ? "no-raf"
            : "zero-duration";
          await aptAbortableDelay(durationMs, context.signal);
          return;
        }

        if (blackCutOnly) {
          handoffDebug.skipped = "reduced-motion-semantic-cut";
          handoffDebug.fallback = await runBlackCut();
          handoffDebug.finishedAt = Math.round(view.performance?.now?.() ?? 0);
          return;
        }

        const initialProgress = direction === "inward" ? 1 : startProgress;
        const motion = compositor?.createMotion?.({
          direction,
          startProgress: initialProgress,
          startVelocity: direction === "outward" ? startVelocity : 0,
          startAcceleration: direction === "outward" ? startAcceleration : 0,
          durationMs,
        }) ?? null;
        if (!motion) {
          /* A failed compositor has no canonical frame writer. Preserve the
           * existing bounded black-cut fallback rather than inventing a second,
          * untested progress clock in this classic-script host. */
          handoffDebug.skipped = "no-compositor-clock";
          handoffDebug.fallback = await runBlackCut();
          handoffDebug.finishedAt = Math.round(view.performance?.now?.() ?? 0);
          return;
        }
        const motionRecord = {
          context,
          direction,
          motion,
          active: true,
          retarget(nextDirection, source) {
            if (!this.active || context.signal.aborted) return this.motion.snapshot();
            const before = this.motion.snapshot();
            const next = this.motion.retarget(nextDirection);
            if (next.reversals !== before.reversals) {
              handoffDebug.reversals.push(Object.freeze({
                from: before.targetDirection,
                to: next.targetDirection,
                progress: Number(next.progress.toFixed(6)),
                source: String(source || "gesture"),
              }));
              handoffDebug.targetDirection = next.targetDirection;
            }
            return next;
          },
          snapshot() { return this.motion.snapshot(); },
        };
        handoffMotionRef.current = motionRecord;

        /* Perspective size during preview is exactly the timeline scale. Clear
         * the rig pull and replace it with the equivalent host transform in the
         * same task, before the browser can paint, so the seam has no size pop. */
        if (!reducedMotion) {
          const initial = compositor?.setProgress(initialProgress) ?? null;
          engineRef.current.rig.setOvershoot?.(0);
          if (initial) {
            apartmentHost.style.opacity = String(initial.apartmentOpacity);
            apartmentHost.style.transform = "scale(" + initial.apartmentScale + ")";
            worldHost.style.opacity = String(initial.worldOpacity);
          }
        }
        const outcome = await new Promise((resolve) => {
          let frameId = 0;
          let lastAt = null;
          let settled = false;
          const finish = (value = "aborted") => {
            if (settled) return;
            settled = true;
            if (frameId) view.cancelAnimationFrame(frameId);
            context.signal.removeEventListener("abort", onAbort);
            resolve(value);
          };
          const onAbort = () => finish("aborted");
          const step = (at) => {
            frameId = 0;
            if (context.signal.aborted) { finish(); return; }
            const now = Number.isFinite(at) ? at : (view.performance?.now?.() ?? 0);
            const deltaMs = lastAt === null ? 0 : Math.max(0, Math.min(50, now - lastAt));
            lastAt = now;
            /* There is one progress value for both directions. Retargeting the
             * pure clock changes its endpoint without re-basing progress, so
             * this frame is always the exact inverse of a frame already shown. */
            const motionState = motion.advance(deltaMs);
            const progress = motionState.progress;
            const frame = compositor?.setProgress(progress) ?? null;
            if (frame) {
              apartmentHost.style.opacity = String(frame.apartmentOpacity);
              apartmentHost.style.transform = "scale(" + frame.apartmentScale + ")";
              worldHost.style.opacity = String(frame.worldOpacity);
            }
            handoffDebug.frames += 1;
            handoffDebug.lastProgress = progress;
            handoffDebug.targetDirection = motionState.targetDirection ?? direction;
            if (motionState.settled) {
              finish(motionState.endpoint ?? "destination");
              return;
            }
            frameId = view.requestAnimationFrame(step);
          };
          context.signal.addEventListener("abort", onAbort, { once: true });
          frameId = view.requestAnimationFrame(step);
        });
        /*
         * Drop the transform, KEEP the opacities.
         *
         * React never writes transform, so removing it is safe and necessary --
         * the endpoint scale is 0.0019 and leaving it would pin the Apartment
         * to an invisible speck. The opacities are the opposite case: React
         * DOES own them, and it diffs against its own previous props rather
         * than against the DOM. It had already written the settled value before
         * this loop began, so removing that value here leaves React believing
         * the DOM is correct while it shows the wrong thing, and nothing rewrites
         * it until an unrelated re-render happens to change the prop. The last
         * frame already wrote exactly the settled endpoint, so the fix is simply
         * to leave it there.
         */
        if (motionRecord) motionRecord.active = false;
        if (handoffMotionRef.current === motionRecord) handoffMotionRef.current = null;
        if (context.signal.aborted) {
          /* Cancellation restores the last committed owner explicitly. React
           * may believe it already wrote these values and otherwise leave the
           * imperative mid-handoff opacity stranded in the DOM. */
          const apartmentWasCommitted = context.fromSurface === "apartment";
          apartmentHost.style.opacity = apartmentWasCommitted ? "1" : "0";
          worldHost.style.opacity = apartmentWasCommitted ? "0" : "1";
        }
        apartmentHost.style.removeProperty("transform");
        handoffDebug.settledEndpoint = outcome;
        handoffDebug.finishedAt = Math.round(view.performance?.now?.() ?? 0);
        /* Reaching the source through the inverse curve is a visual cancellation,
         * not a successful activation of the original destination. Throw only
         * after the exact source frame has been applied; the coordinator then
         * keeps its original committed owner and runs normal target cleanup. */
        if (outcome === "source" && !context.signal.aborted) {
          if (boundaryMomentumRef.current?.snapshot?.()?.activeIntentId === context.intentId) {
            handoffSourceReversalRef.current = context.intentId;
          }
          const error = new Error("spatial viewport handoff reversed to source");
          error.name = "AbortError";
          throw error;
        }
      };

      const disposeCompositor = () => {
        aptDisposeHandoffCompositorSlots(
          handoffCompositorRef,
          handoffCompositorPendingRef,
          handoffCompositorPromiseRef,
          handoffCompositorGenerationRef,
        );
      };

      /*
       * Built at commit, never at mount. This is the app's THIRD live WebGL
       * context, and WebView2 caps them at 16 and evicts the OLDEST -- which in
       * this app is the Apartment's own. Holding one warm across a session
       * turns a bounded risk into a standing one.
       *
       * Reduced motion never builds it at all: the cut is a semantic jump, so
       * a particle field would be the very thing the preference asks us not to
       * draw, and the context is not worth taking for it.
       */
      const armCompositor = async (context, {
        captureDeadlineAt = performance.now() + APT_HANDOFF_ENDPOINT_BUDGET_MS,
      } = {}) => {
        if (handoffCompositorRef.current || context.signal.aborted) return;
        if (aptPrefersReducedMotion()) return;
        /* Preparation can be requested again while the capture/GL allocation
         * from the first request is still awaiting a frame. Share that work:
         * a second compositor would consume a fourth WebGL context and the
         * loser could land after cancellation with stale pixels. */
        if (handoffCompositorPromiseRef.current) {
          const inFlight = handoffCompositorPromiseRef.current;
          try { await inFlight; } catch (_) { /* firewall still plays */ }
          /* The shared allocation may have belonged to the intent this one
           * superseded. That owner correctly disposes its result when aborted;
           * retry once under the latest context instead of silently degrading
           * the winning handoff to a black cut. */
          if (!handoffCompositorRef.current && !context.signal.aborted
              && handoffCompositorPromiseRef.current === inFlight) {
            handoffCompositorPromiseRef.current = null;
            return armCompositor(context, { captureDeadlineAt });
          }
          return;
        }
        const generation = ++handoffCompositorGenerationRef.current;
        const promise = (async () => {
          /* The handoff preset decides how dense the field is and how long the
           * streaks are. Passed at construction because the geometry is built
           * once; changing the preset builds the next handoff differently. */
          const handoffPreset = worldViewBundleRef.current?.resolved?.handoff ?? null;
          const captureSurface = Object.freeze({
            performanceBudget: () => surface.performanceBudget?.() ?? null,
            /* Lets the compositor wait for the AI still to be fully up before it
             * takes the particles' colours (WORLD_CAPTURE_GATE). */
            captureReadiness: () => surface.captureReadiness?.() ?? null,
            captureFrame: async ({ scale } = {}) => {
              const presentationReady = await surface.waitForPresentation?.({
                timeoutMs: Math.max(0, captureDeadlineAt - performance.now()),
                signal: context.signal,
              });
              if (presentationReady !== true || context.signal.aborted) return null;
              const timeoutMs = aptHandoffCaptureBudget(
                captureDeadlineAt,
                performance.now(),
                APT_HANDOFF_CAPTURE_CLEANUP_RESERVE_MS,
              );
              /* A missed deadline retains the source without allocating a late PBO. */
              if (!(timeoutMs > 0)) return Promise.resolve(null);
              return surface.captureFrame?.({ scale, timeoutMs }) ?? Promise.resolve(null);
            },
          });
          let compositor = null;
          try {
            compositor = await engineRef.current.createHandoffCompositor({
              host: viewRootRef.current,
              worldSurface: captureSurface,
              apartmentSurface: {
                captureFrame: (options) => engineRef.current.captureFrame?.({ ...options, signal: context.signal }),
              },
              particleCount: handoffPreset?.particleCount,
              streakFraction: handoffPreset?.streakFraction,
            });
            if (generation !== handoffCompositorGenerationRef.current
                || context.signal.aborted || !viewRootRef.current) {
              compositor.dispose();
              return null;
            }
            /* Retain the not-yet-primed allocation so a host timeout can tear
             * down its WebGL context immediately. Its capture proxy expires
             * before that same deadline, preventing an orphaned PBO/fence from
             * overlapping an immediate retry. */
            handoffCompositorPendingRef.current = compositor;
            const primed = await compositor.prime();
            if (!primed.endpointsReady) {
              if (handoffCompositorPendingRef.current === compositor) handoffCompositorPendingRef.current = null;
              compositor.dispose();
              return null;
            }
            if (!aptHandoffCompositorMayInstall(
              generation,
              handoffCompositorGenerationRef.current,
              {
                aborted: context.signal.aborted,
                hasHost: Boolean(viewRootRef.current),
                pendingMatches: handoffCompositorPendingRef.current === compositor,
              },
            )) {
              compositor.dispose();
              return null;
            }
            handoffCompositorPendingRef.current = null;
            handoffCompositorRef.current = compositor;
            return compositor;
          } catch (error) {
            if (handoffCompositorPendingRef.current === compositor) {
              handoffCompositorPendingRef.current = null;
            }
            if (handoffCompositorRef.current === compositor) {
              handoffCompositorRef.current = null;
            }
            compositor?.dispose?.();
            throw error;
          }
        })();
        handoffCompositorPromiseRef.current = promise;
        try {
          await promise;
        } catch (_) {
          /* the firewall still plays */
        } finally {
          if (handoffCompositorPromiseRef.current === promise
              && !handoffCompositorRef.current) {
            handoffCompositorPromiseRef.current = null;
          }
        }
      };

      const apartmentSurface = {
        async prepare(context) {
          if (context.signal.aborted) return;
          if (coldApartmentEntryIntentRef.current === context.intentId) {
            coldApartmentEntryIntentRef.current = null;
          }
          engineRef.current.rig.setOvershoot?.(0);
          engineRef.current.rig.goTo({ zoom: 0, dur: 0 });
          engineRef.current.setRunning(true);
          /*
           * The return needs its own compositor, and its own capture.
           *
           * Without this the inward trip has no firewall and no field: the
           * timeline still runs for 650ms but writes nothing, so React's
           * declarative opacities flip on the first frame and the return reads
           * as an instant cut. Arming here also re-primes at the RETURN range,
           * which is a different camera distance than the outward prime -- the
           * old texture would reassemble the world from the wrong altitude.
          */
          if (context.fromSurface !== context.toSurface && !aptPrefersReducedMotion()) {
            const captureDeadlineAt = performance.now()
              + APT_HANDOFF_ENDPOINT_BUDGET_MS;
            const compositorWindow = await aptSettleWithin(
              armCompositor(context, { captureDeadlineAt }),
              APT_HANDOFF_ENDPOINT_BUDGET_MS,
              context.signal,
            );
            const compositorReady = compositorWindow.status === "settled"
              && Boolean(handoffCompositorRef.current);
            if (!compositorReady) {
              /* Release a late allocation and retain the complete world until retry. */
              disposeCompositor();
              setJourneyRecovery({ kind: "content-unavailable", message: "The interior view is still preparing." });
              journeyRetryRef.current = () => zoomRequestRef.current?.(1, { source: "retry-interior" });
              throw new Error("interior endpoint not ready");
            }
          }
          await aptAbortableDelay(0, context.signal);
        },
        async activate(context) {
          try {
            if (context.signal.aborted) return;
            if (context.fromSurface !== context.toSurface) {
              await runHandoff(context, "inward");
              disposeCompositor();
            }
            setSpatialScale("apartment");
            /* A completed return owns the detent again. Clear the boundary latch
             * only after the inward compositor has reached its exact endpoint;
             * clearing it in prepare() would re-arm wheel input underneath the
             * still-visible world and allow a second transition to race the first. */
            boundaryMomentumRef.current?.dispatch?.({
              type: "reset",
              reason: "apartment-reactivated",
            });
            engineRef.current.rig.setOvershoot?.(0);
          } finally {
            if (coldApartmentEntryIntentRef.current === context.intentId) {
              coldApartmentEntryIntentRef.current = null;
            }
          }
        },
        suspend() {
          engineRef.current.setRunning(false);
        },
        cancel(context) {
          if (coldApartmentEntryIntentRef.current === context.intentId) {
            coldApartmentEntryIntentRef.current = null;
          }
          if (context.fromSurface === "world") {
            engineRef.current.setRunning(false);
            setSpatialScale(context.fromScale);
            disposeCompositor();
          }
        },
      };

      /*
       * Give the world its imagery.
       *
       * Without this the surface only ever renders the bundled Natural Earth
       * tier, which at parcel range is a single stretched level-2 texel -- a
       * flat olive field that reads as a broken map rather than as an offline
       * fallback. The capability and the key delivery both existed; nothing
       * connected them.
       *
       * Called only after the outer-detent threshold commits a coordinator
       * intent. Creating the tileset spends one of the account's 1,000 monthly
       * root-tile requests: sub-threshold fidgeting cannot activate it, while a
       * later visual handoff reversal cannot recall a request already handed to
       * the provider. The surface reuses a live session until it ages out.
       *
       * Only explicit synthetic-spatial QA suppresses this. Simulated HA/device
       * state still navigates registered geography and may use configured live
       * imagery exactly like the ordinary Apartment model.
       */
      const currentWorldEntryRevision = () => {
        const anchor = confirmedWorldAnchorRef.current;
        const site = syntheticSpatialQa
          ? null
          : window.HomeSpatialViewport?.getSelectedSite?.();
        return aptWorldEntryRevision(anchor, site?.dollhouseRegistration ?? null);
      };

      const invalidateWorldImageryActivation = (context) => {
        const record = worldImageryActivationRef.current;
        if (!record || (context && record.intentId !== context.intentId)) return false;
        record.cancelled = true;
        worldImageryActivationGenerationRef.current += 1;
        worldImageryActivationRef.current = null;
        /* Do not manufacture a setAnchor() mutation just to cancel host
         * ownership. Cesium's factory cannot be cancelled once requested, and
         * a simple handoff reversal should preserve the resulting hidden,
         * reusable candidate. A real site change already calls setAnchor(),
         * while lifecycle teardown disposes the surface; both provide the
         * renderer-side generation invalidation a stale factory needs. */
        return true;
      };

      const activateWorldImagery = (context, targetRevision = currentWorldEntryRevision()) => {
        if (syntheticSpatialQa || context.signal.aborted || !targetRevision) return Promise.resolve(null);
        const providers = window.HomeWorldProviders;
        if (!providers) return Promise.resolve(null);

        const record = {
          generation: ++worldImageryActivationGenerationRef.current,
          lifecycleRevision,
          targetRevision,
          intentId: context.intentId,
          surface,
          surfaceActivationStarted: false,
          cancelled: false,
          promise: null,
        };
        const ownsActivation = () => (
          worldImageryActivationRef.current === record
          && worldImageryActivationGenerationRef.current === record.generation
          && spatialLifecycleRevisionRef.current === record.lifecycleRevision
          && worldSurfaceRef.current === record.surface
          && currentWorldEntryRevision() === record.targetRevision
          && !record.cancelled
          && !context.signal.aborted
        );
        const onAbort = () => invalidateWorldImageryActivation(context);
        const releaseOwnership = () => {
          context.signal.removeEventListener("abort", onAbort);
          if (worldImageryActivationRef.current === record) {
            worldImageryActivationRef.current = null;
          }
        };
        worldImageryActivationRef.current = record;
        context.signal.addEventListener("abort", onAbort, { once: true });

        record.promise = (async () => {
          try {
            const config = await providers.load();
            if (!ownsActivation()) return config;
            if (!config.configured) {
              if (new URLSearchParams(window.location.search).get("worldOverview") !== "off") {
                surface.setOverviewNavigation?.(true);
                setWorldOverviewActive(true);
              }
              return config;
            }
            surface.setOverviewNavigation?.(false);
            setWorldOverviewActive(false);
            record.surfaceActivationStarted = true;
            const result = await surface.activatePhotorealistic({
              tilesKey: providers.getTilesKey(),
            });
            if (!ownsActivation()) return { status: "superseded" };
            if (["fallback", "unavailable", "failed"].includes(result?.status)
                && new URLSearchParams(window.location.search).get("worldOverview") !== "off") {
              surface.setOverviewNavigation?.(true);
              setWorldOverviewActive(true);
            }
            return result;
          } catch (_) {
            // Natural Earth remains the contractual overview fallback and the
            // local black field remains usable; a provider failure cannot fail zoom.
            return ownsActivation() ? { status: "unavailable" } : { status: "superseded" };
          } finally {
            releaseOwnership();
          }
        })();
        return record.promise;
      };

      /*
       * Derive the world pose from the live Apartment camera and apply it.
       *
       * Silent on failure by design: an unregistered site, a rig that cannot
       * report a basis, or a surface that has already moved on all leave the
       * band pose in place, which is a worse view but still a view.
       */
      /*
       * Arm the home's declared stills.
       *
       * Nothing did this before, which is why the hero layer only ever appeared
       * when a debug caller armed one by hand: the feature was complete and
       * unreachable. The stills travel with the anchor, so this runs wherever
       * the anchor is applied rather than on a lifecycle event that might not
       * fire on every route into the world.
       */
      const armDeclaredStills = () => {
        const anchor = confirmedWorldAnchorRef.current;
        const surface = worldSurfaceRef.current;
        if (!surface?.setHeroStills) return null;
        try {
          return surface.setHeroStills(anchor?.heroStills || [], anchor);
        } catch (_) {
          /* A still is an enhancement; never let it stop world entry. */
          return null;
        }
      };

      const applyRegisteredWorldPose = async (context) => {
        const anchor = confirmedWorldAnchorRef.current;
        const entryRangeM = surface.heroEntryRange?.(anchor) ?? anchor?.parcelRangeM;
        const capturedPose = anchor?.heroStills?.[0];
        if (capturedPose && Number.isFinite(capturedPose.headingDeg) && Number.isFinite(capturedPose.pitchDeg)) {
          armDeclaredStills();
          return surface.setRegisteredPose({
            headingDeg: capturedPose.headingDeg, pitchDeg: capturedPose.pitchDeg, rollDeg: 0,
            ...(capturedPose.fovAxis === "vertical" && Number.isFinite(capturedPose.fovDeg)
              ? { verticalFovDeg: capturedPose.fovDeg } : {}),
            rangeM: entryRangeM,
          });
        }
        const azimuth = anchor?.localXAxisAzimuthDeg;
        if (!Number.isFinite(azimuth)) {
          /*
           * No scan to register, so there is no Apartment basis to re-apply --
           * but there may still be a chosen sight line, and if there is it must
           * be applied HERE too. Returning null left the band's generic camera
           * in place, and the band carries no heading at all, so a home without
           * a scan arrived pointing wherever navigate() happened to leave it.
           * travelToSite already honoured the chosen bearing; cold entry did
           * not, so the same home faced two different ways depending on how you
           * got there -- and the hero still, which is captured at one bearing,
           * can only be right for one of them.
           */
          const chosen = anchor?.parcelViewHeadingDeg;
          if (!Number.isFinite(chosen)) return null;
          armDeclaredStills();
          return surface.setRegisteredPose({
            headingDeg: chosen,
            /* PARCEL_DESCENT_PITCH_DEG in world-camera.mjs; see the note at the
             * traversal's copy of this literal. */
            pitchDeg: -35,
            rollDeg: 0,
            rangeM: entryRangeM,
          });
        }
        try {
          const pose = await engineRef.current.apartmentWorldPose({ localXAxisAzimuthDeg: azimuth });
          if (!pose || context.signal.aborted) return null;
          armDeclaredStills();
          return surface.setRegisteredPose({
            headingDeg: pose.headingDeg,
            pitchDeg: pose.pitchDeg,
            rollDeg: pose.rollDeg,
            verticalFovDeg: pose.verticalFovDeg,
            rangeM: entryRangeM,
          });
        } catch (_) {
          return null;
        }
      };

      const mayPromoteWorldImagery = (context, targetRevision) => (
        aptWorldPromotionAuthority({
          context,
          coordinatorState: spatialCoordinatorRef.current?.getState?.(),
          expectedLifecycleRevision: lifecycleRevision,
          currentLifecycleRevision: spatialLifecycleRevisionRef.current,
          targetRevision,
          currentTargetRevision: currentWorldEntryRevision(),
          expectedSurface: surface,
          currentSurface: worldSurfaceRef.current,
        })
      );

      const promoteStagedWorldImagery = (
        context,
        activation,
        targetRevision,
        timeoutMs,
      ) => aptTryPromoteStagedWorldImagery({
        activation,
        signal: context.signal,
        timeoutMs,
        settleWithin: aptSettleWithin,
        waitForImagery: (options) => (
          surface.waitForImagery?.(options) ?? Promise.resolve(false)
        ),
        mayPromote: () => mayPromoteWorldImagery(context, targetRevision),
        promote: () => surface.promotePhotorealistic?.() ?? { status: "not-ready" },
      });

      const interactionImageryAdopter = aptCreateWorldImageryAdopter({
        timeoutMs: APT_WORLD_ENTRY_READINESS_BUDGET_MS,
        refinementTimeoutMs: APT_WORLD_INTERACTION_HEIGHT_BUDGET_MS,
        settleWithin: aptSettleWithin,
        tryPromote: aptTryPromoteStagedWorldImagery,
        activate: (record) => activateWorldImagery({
          intentId: `world-interaction-${record.generation}`,
          signal: record.controller.signal,
        }, record.targetRevision),
        waitForImagery: (options) => (
          surface.waitForImagery?.(options) ?? Promise.resolve(false)
        ),
        promote: () => surface.promotePhotorealistic?.() ?? { status: "not-ready" },
        refine: async (record, { timeoutMs, mayCommit }) => {
          const startedAt = performance.now();
          const remainingMs = () => Math.max(
            0,
            timeoutMs - (performance.now() - startedAt),
          );
          const assertAuthority = () => {
            if (record.controller.signal.aborted || !mayCommit()) {
              const error = new Error("world imagery refinement superseded");
              error.name = "AbortError";
              throw error;
            }
          };
          assertAuthority();
          const surfaceHeight = await surface.resolveSurfaceHeight({
            timeoutMs: remainingMs(),
          });
          assertAuthority();
          const dollhouseGround = await (
            surface.resolveDollhouseGroundHeight?.({
              timeoutMs: remainingMs(),
            }) ?? Promise.resolve({ heightM: null, source: "unregistered" })
          );
          assertAuthority();
          return { surfaceHeight, dollhouseGround };
        },
        hasAuthority: (record, currentOperationGeneration) => (
          aptWorldInteractionPromotionAuthority({
            signal: record.controller.signal,
            expectedOperationGeneration: record.generation,
            currentOperationGeneration,
            coordinatorState: spatialCoordinatorRef.current?.getState?.(),
            expectedLifecycleRevision: lifecycleRevision,
            currentLifecycleRevision: spatialLifecycleRevisionRef.current,
            targetRevision: record.targetRevision,
            currentTargetRevision: currentWorldEntryRevision(),
            expectedSurface: surface,
            currentSurface: worldSurfaceRef.current,
          })
        ),
      });
      worldImageryAdopterRef.current?.cancel?.();
      worldImageryAdopterRef.current = interactionImageryAdopter;

      const completeColdWorldEntry = async (context, reason = "entry-readiness-deadline") => {
        /* Staging and visibility are separate renderer operations. Only an
         * intent that already promoted Google needs an explicit rollback here.
         * Calling defer while its uncancellable factory is still in flight
         * would advance the renderer generation and waste that paid request;
         * the factory already guarantees its late result attaches hidden. */
        if (worldImageryPromotedIntentRef.current === context.intentId) {
          try { surface.deferPhotorealistic?.(); } catch (_) { /* disposed */ }
          worldImageryPromotedIntentRef.current = null;
        }
        if (context.signal.aborted) return;
        const navigation = await surface.navigate({
          intentId: context.intentId,
          band: context.toScale,
          motion: "cut",
          durationMs: 0,
        });
        if (context.signal.aborted || navigation.status !== "settled") {
          const error = new Error("cold world entry superseded");
          error.name = "AbortError";
          throw error;
        }
        /* Apply the captured pose after the generic band pose, before the
         * endpoint readiness gate can authorize visual ownership. */
        if (context.fromSurface !== context.toSurface) {
          await applyRegisteredWorldPose(context);
        }
        if (context.signal.aborted) {
          const error = new Error("cold world entry superseded");
          error.name = "AbortError";
          throw error;
        }
        if (!aptPrefersReducedMotion()) {
          const preparation = await aptSettleWithin(
            armCompositor(context), APT_HANDOFF_ENDPOINT_BUDGET_MS, context.signal,
          );
          if (preparation.status !== "settled" || !handoffCompositorRef.current) {
            disposeCompositor();
            setJourneyRecovery({ kind: "content-unavailable", message: "The outside view is not ready for this display. Import a complete home pack or retry." });
            journeyRetryRef.current = () => zoomRequestRef.current?.(-1, { source: "retry-outside" });
            throw new Error("outside endpoint not ready");
          }
        }
      };

      const worldLifecycle = {
        async prepare(context) {
          if (context.signal.aborted) return;
          if (coldWorldEntryIntentRef.current === context.intentId) {
            coldWorldEntryIntentRef.current = null;
          }
          applyAnchor();
          surface.setRunning(true);

          const enteringWorld = context.fromSurface === "apartment"
            && context.toSurface === "world";
          if (enteringWorld) {
            if (worldDollhouseRetryIntentRef.current?.intentId === context.intentId) {
              worldDollhouseRetryIntentRef.current = null;
            }
            /* One deadline begins at the outer-detent threshold and covers
             * every awaited destination-readiness step: matching local prewarm,
             * hidden provider activation, destination navigation, imagery
             * quiet, promotion, provider-specific heights, and compositor
             * prime. Registered-pose math remains outside because it is local
             * and is required on both the warm and cold paths. */
            const readinessStartedAt = performance.now();
            const remainingReadinessMs = () => Math.max(
              0,
              APT_WORLD_ENTRY_READINESS_BUDGET_MS - (performance.now() - readinessStartedAt),
            );
            /* Always leave enough of the same deadline for a local-surface
             * capture. Paid imagery may use only the surplus; otherwise a slow
             * optional root request could consume the entire budget and make
             * the rich handoff unreachable on the default/offline path. */
            const remainingProviderReadinessMs = () => Math.max(
              0,
              remainingReadinessMs() - APT_WORLD_ENTRY_COMPOSITOR_RESERVE_MS,
            );
            const deferProviderCandidate = () => {
              try { surface.deferPhotorealistic?.(); } catch (_) { /* disposed */ }
              if (worldImageryPromotedIntentRef.current === context.intentId) {
                worldImageryPromotedIntentRef.current = null;
              }
            };
            const anchor = confirmedWorldAnchorRef.current;
            const site = syntheticSpatialQa
              ? null
              : window.HomeSpatialViewport?.getSelectedSite?.();
            const revision = aptWorldEntryRevision(
              anchor,
              site?.dollhouseRegistration ?? null,
            );
            let prewarmJoin = null;
            if (revision && worldEntryPrewarmRef.current) {
              try {
                prewarmJoin = await worldEntryPrewarmRef.current.joinForHandoff({
                  revision,
                  maxWaitMs: remainingReadinessMs(),
                  signal: context.signal,
                });
              } catch (_) {
                prewarmJoin = null;
              }
            }
            if (context.signal.aborted) return;

            /* Missing revision/controller/join data is cold by default. Only
             * affirmative local surface + target stages for this exact joined
             * revision may enter the richer path; optional provider/heights/
             * GLB status cannot veto a capturable local frame. There is
             * no null-result fall-through into the old serial 6s + 6s waits. */
            const prewarmReady = aptWorldPrewarmCanUseRichEntry(prewarmJoin);
            if (aptWorldPrewarmNeedsDollhouseRetry(
              prewarmJoin,
              Boolean(site?.dollhouseRegistration),
            )) {
              /* Speculation never owns visibility, and a settled controller is
               * deliberately reused. Mark this intent to open the renderer's
               * reveal gate only after it wins either handoff path; the asset
               * never consumes compositor eligibility or the 500ms firewall. */
              worldDollhouseRetryIntentRef.current = Object.freeze({
                intentId: context.intentId,
                revision,
              });
            }
            if (aptWorldEntryRequiresColdCut({ prewarmReady })) {
              if (revision) void activateWorldImagery(context, revision).catch(() => {});
              await completeColdWorldEntry(context, "prewarm-not-ready");
              return;
            }

            const activationWindow = await aptSettleWithin(
              activateWorldImagery(context, revision),
              Math.min(
                APT_WORLD_ENTRY_PROVIDER_ACTIVATION_BUDGET_MS,
                remainingProviderReadinessMs(),
              ),
              context.signal,
            );
            const imageryActivation = activationWindow.value;
            let providerCandidateReady = activationWindow.status === "settled" && (
              imageryActivation?.status === "staged"
              || imageryActivation?.status === "ready"
            );

            const navigationWindow = await aptSettleWithin(
              surface.navigate({
                intentId: context.intentId,
                band: context.toScale,
                /* The destination is hidden; the compositor is the visible
                 * motion. A camera tween here would spend the whole readiness
                 * budget before the default local field can be captured. */
                motion: "cut",
                durationMs: 0,
              }),
              remainingReadinessMs(),
              context.signal,
            );
            const navigationReady = navigationWindow.status === "settled"
              && navigationWindow.value?.status === "settled";
            if (aptWorldEntryRequiresColdCut({ navigationReady })) {
              await completeColdWorldEntry(context, "navigation-not-ready");
              return;
            }

            await applyRegisteredWorldPose(context);
            if (context.signal.aborted) return;

            if (providerCandidateReady) {
              const imageryWindow = await aptSettleWithin(
                surface.waitForImagery?.({
                  timeoutMs: remainingProviderReadinessMs(),
                  signal: context.signal,
                })
                  ?? Promise.resolve(false),
                remainingProviderReadinessMs(),
                context.signal,
              );
              if (imageryWindow.status !== "settled" || imageryWindow.value !== true) {
                deferProviderCandidate();
                providerCandidateReady = false;
              }
            }

            if (providerCandidateReady) {
              /* Provider construction is still hidden at this point. Promotion
               * is a synchronous, host-owned commit made only after the quiet
               * gate succeeds for this intent and revision. No tile callback
               * or late activation continuation can make Google visible. */
              if (context.signal.aborted || currentWorldEntryRevision() !== revision) {
                deferProviderCandidate();
                await completeColdWorldEntry(context, "entry-superseded-before-promotion");
                return;
              }
              let promotion = null;
              try { promotion = surface.promotePhotorealistic?.() ?? null; }
              catch (_) { promotion = null; }
              if (promotion?.status !== "ready") {
                deferProviderCandidate();
                providerCandidateReady = false;
              } else {
                worldImageryPromotedIntentRef.current = context.intentId;
              }
            }

            if (providerCandidateReady) {
              /* Promotion changes the provider generation and intentionally
               * invalidates Natural-Earth height caches. Resolve the visible
               * provider's roof first, then its registered dollhouse ground:
               * the ground resolver captures the surface generation, so the
               * two cannot run in parallel. Provider refinement may use only
               * the budget surplus reserved above the compositor floor. */
              const surfaceHeightWindow = await aptSettleWithin(
                surface.resolveSurfaceHeight({ timeoutMs: remainingProviderReadinessMs() }),
                remainingProviderReadinessMs(),
                context.signal,
              );
              const surfaceHeightSource = surfaceHeightWindow.value?.source;
              const providerSurfaceHeightReady = surfaceHeightWindow.status === "settled"
                && Number.isFinite(surfaceHeightWindow.value?.heightM)
                && (surfaceHeightSource === "cached" || surfaceHeightSource === "sampled-surface");
              if (!providerSurfaceHeightReady) {
                deferProviderCandidate();
                providerCandidateReady = false;
              }
            }

            if (providerCandidateReady) {
              const groundHeightWindow = await aptSettleWithin(
                surface.resolveDollhouseGroundHeight?.({
                  timeoutMs: remainingProviderReadinessMs(),
                }) ?? Promise.resolve({ heightM: null, source: "unregistered" }),
                remainingProviderReadinessMs(),
                context.signal,
              );
              const groundHeight = groundHeightWindow.value;
              const providerGroundHeightReady = groundHeightWindow.status === "settled"
                && groundHeight?.source !== "superseded"
                && groundHeight?.source !== "site-mismatch"
                && (groundHeight?.source === "unregistered" || Number.isFinite(groundHeight?.heightM));
              if (!providerGroundHeightReady) {
                deferProviderCandidate();
                providerCandidateReady = false;
              }
            }
            if (context.signal.aborted) {
              deferProviderCandidate();
              return;
            }

            if (!aptPrefersReducedMotion()) {
              const compositorWindow = await aptSettleWithin(
                armCompositor(context, {
                  captureDeadlineAt: performance.now() + APT_HANDOFF_ENDPOINT_BUDGET_MS,
                }),
                APT_HANDOFF_ENDPOINT_BUDGET_MS,
                context.signal,
              );
              const compositorReady = compositorWindow.status === "settled"
                && Boolean(handoffCompositorRef.current);
              if (aptWorldEntryRequiresColdCut({ compositorReady })) {
                /* Generation invalidation makes a late create/prime dispose its
                 * own WebGL context instead of installing after the black cut. */
                disposeCompositor();
                await completeColdWorldEntry(context, "compositor-not-ready");
                return;
              }
            }
            return;
          }

          /* World-owned scale changes can adopt a cold root request that
           * finished only after Apartment entry's 500ms firewall. It is
           * intentionally left hidden, but it must not remain hidden forever:
           * each later intent gets one finite chance to adopt that staged
           * session before moving its camera. Nothing promotes from a provider
           * callback or late continuation. */
          const targetRevision = currentWorldEntryRevision();
          const promotionStartedAt = performance.now();
          const remainingWorldPromotionMs = () => Math.max(
            0,
            APT_WORLD_ENTRY_READINESS_BUDGET_MS
              - (performance.now() - promotionStartedAt),
          );
          const activationWindow = await aptSettleWithin(
            activateWorldImagery(context, targetRevision),
            remainingWorldPromotionMs(),
            context.signal,
          );
          if (context.signal.aborted) return;
          if (activationWindow.status === "settled") {
            await promoteStagedWorldImagery(
              context,
              activationWindow.value,
              targetRevision,
              remainingWorldPromotionMs(),
            );
          }
          if (context.signal.aborted) return;
          const result = await surface.navigate({
            intentId: context.intentId,
            band: context.toScale,
            motion: context.durationMs === 0 ? "cut" : "animate",
            durationMs: context.durationMs,
          });
          if (context.signal.aborted || result.status !== "settled") {
            const error = new Error("spatial viewport transition superseded");
            error.name = "AbortError";
            throw error;
          }
        },
        async activate(context) {
          try {
            if (context.signal.aborted) return;
            setSpatialScale(context.toScale);
            if (context.fromSurface !== context.toSurface) {
              await runHandoff(context, "outward");
              disposeCompositor();
            }
            const dollhouseRetry = worldDollhouseRetryIntentRef.current;
            if (dollhouseRetry?.intentId === context.intentId) {
              worldDollhouseRetryIntentRef.current = null;
              if (!context.signal.aborted
                  && currentWorldEntryRevision() === dollhouseRetry.revision) {
                /* A retry begins only after this destination has genuinely won
                 * the reversible handoff. waitForDollhouse is itself bounded
                 * and registration-generation guarded, so a later site change
                 * cannot reveal the old home's model. A timeout leaves it
                 * hidden and the next committed entry retries again. */
                void (surface.waitForDollhouse?.({
                  timeoutMs: APT_WORLD_ENTRY_READINESS_BUDGET_MS,
                }) ?? Promise.resolve({ status: "not-required" })).catch(() => {});
              }
            }
          } finally {
            if (coldWorldEntryIntentRef.current === context.intentId) {
              coldWorldEntryIntentRef.current = null;
            }
            if (worldImageryPromotedIntentRef.current === context.intentId) {
              worldImageryPromotedIntentRef.current = null;
            }
            if (worldDollhouseRetryIntentRef.current?.intentId === context.intentId) {
              worldDollhouseRetryIntentRef.current = null;
            }
          }
        },
        suspend() {
          interactionImageryAdopter.cancel();
          surface.setRunning(false);
        },
        cancel(context) {
          /* A direct-manipulation reversal revokes host authority without
           * destroying an in-flight paid root request. The renderer keeps any
           * late result staged and hidden; a site change/setAnchor or lifecycle
           * disposal remains the authority that invalidates the factory. */
          invalidateWorldImageryActivation(context);
          if (context.fromSurface === "apartment" && context.toSurface === "world"
              && worldImageryPromotedIntentRef.current === context.intentId) {
            try { surface.deferPhotorealistic?.(); }
            catch (_) { /* disposed */ }
            worldImageryPromotedIntentRef.current = null;
          }
          if (coldWorldEntryIntentRef.current === context.intentId) {
            coldWorldEntryIntentRef.current = null;
          }
          if (worldDollhouseRetryIntentRef.current?.intentId === context.intentId) {
            worldDollhouseRetryIntentRef.current = null;
          }
          setSpatialScale(context.fromScale);
          disposeCompositor();
          if (context.fromSurface === "apartment") {
            surface.setRunning(false);
            return undefined;
          }
          return surface.navigate({
            intentId: `${context.intentId}-rollback`,
            band: context.fromScale,
            motion: "cut",
            durationMs: 0,
          });
        },
      };

      let previousCoordinatorState = null;
      const coordinator = await engineRef.current.createSpatialViewportCoordinator({
        apartmentSurface,
        worldSurface: worldLifecycle,
        initialScale: restoredWorldScaleRef.current || "apartment",
        reducedMotion: aptPrefersReducedMotion(),
        onStateChange(next) {
          const previous = previousCoordinatorState;
          previousCoordinatorState = next;
          setSpatialTransition(next.phase);
          setSpatialTransitionDirection(
            next.phase === "idle" ? null : next.transition?.direction ?? null,
          );
          if (next.phase === "idle") setSpatialScale(next.committedScale);
          /* A cold Google root commonly outlives the 500ms visual firewall.
           * The committed world intent is itself explicit visibility authority,
           * so give that one entry a cancellable adoption tail instead of
           * stranding the now-warm tileset behind the local field until the
           * user happens to scroll again. This is a host lifecycle event, never a
           * provider/tile callback, and the adopter revalidates idle world
           * ownership, lifecycle, site revision, surface, and generation both
           * before and after every await. */
          const committedWorldIntent = aptCommittedWorldIntent(previous, next);
          if (committedWorldIntent) {
            const targetRevision = currentWorldEntryRevision();
            void interactionImageryAdopter.request({
              source: "world-intent-commit",
              targetRevision,
              timeoutMs: APT_WORLD_COMMITTED_PROVIDER_READINESS_BUDGET_MS,
              /* A semantic world transition may have superseded the activation
               * wrapper owned by the previous tail while the uncancellable
               * Cesium factory kept running. Replacing even the same revision
               * gives the winning COMMIT fresh visibility authority. */
              restart: true,
            }).catch(() => {});
          }
        },
      });
      try {
        requireCurrentLifecycle();
      } catch (error) {
        coordinator.dispose?.();
        throw error;
      }
      spatialCoordinatorRef.current = coordinator;
      restoredWorldScaleRef.current = null;
      worldScaleObserverCleanupRef.current?.();
      worldScaleObserverCleanupRef.current = surface.observeScaleBand?.(({ band }) => {
        const activeCoordinator = spatialCoordinatorRef.current;
        const state = activeCoordinator?.getState?.();
        if (!state || state.phase !== "idle" || state.committedScale === "apartment") return;
        activeCoordinator.syncWorldScale?.(band);
      }) ?? null;
      return coordinator;
    })();
    spatialCoordinatorPromiseRef.current = promise;
    try {
      return await promise;
    } catch (error) {
      spatialCoordinatorPromiseRef.current = null;
      throw error;
    }
  }, [ensureWorldSurface, syntheticSpatialQa]);

  /*
   * Keep the coordinator's reduced-motion answer current.
   *
   * It was read once at coordinator construction and the coordinator's own
   * setReducedMotion dispatcher was never called, so turning Reduce Motion on
   * mid-session did nothing until the Apartment view was torn down. The
   * compositor re-queries per handoff on its own; this is what makes the
   * coordinator's duration agree with it.
   */
  useEffect(() => {
    const query = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!query?.addEventListener) return undefined;
    const onChange = (event) => {
      spatialCoordinatorRef.current?.setReducedMotion?.(event.matches === true);
      boundaryMomentumRef.current?.dispatch?.({
        type: "reduced-motion",
        value: event.matches === true,
      });
    };
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  /* Selection can change from the registry as well as from this control, so the
   * dropdown follows the registry rather than owning the value. */
  useEffect(() => {
    const registry = window.HomeSpatialViewport;
    if (!registry?.subscribe) return undefined;
    const sync = () => {
      setHomeSites(registry.getSites());
      setSelectedSiteId(registry.getSelectedSiteId());
    };
    sync();
    return registry.subscribe(sync);
  }, []);

  /*
   * Load the option groups once, so the panel can render real labels and notes
   * rather than a list of ids.
   */
  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const engine = engineRef.current;
        /*
         * `phase` is in the dependency list for a reason that is not obvious:
         * this effect runs the moment the view opens, which is BEFORE the engine
         * exists. Without a dependency that changes when the engine arrives, the
         * early return here is permanent and the panel renders its toggle and
         * nothing else -- a control that opens onto an empty box.
         */
        if (!engine?.worldViewOptions) return;
        const bundle = await engine.worldViewOptions(worldViewSelection);
        if (cancelled) return;
        worldViewBundleRef.current = bundle;
        setWorldViewGroups(bundle.groups);
      } catch (_) { /* the panel simply does not open */ }
    })();
    return () => { cancelled = true; };
  }, [open, phase, worldViewSelection]);

  /*
   * Apply the selection to the live surface.
   *
   * Keyed on the resolved selection key rather than on object identity: the
   * selection round-trips through localStorage, so identity is meaningless and
   * comparing it would either re-apply every render or never re-apply at all.
   *
   * This runs whenever the selection changes AND whenever a surface appears, so
   * choosing an option before the world exists is not silently dropped -- which
   * is the failure mode that has bitten this branch four times.
   */
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const engine = engineRef.current;
        const surface = worldSurfaceRef.current;
        if (!engine?.worldViewOptions) return;
        const bundle = await engine.worldViewOptions(worldViewSelection);
        if (cancelled) return;
        worldViewKeyRef.current = bundle.key;
        worldViewBundleRef.current = bundle;
        /* Quality is a viewport-wide choice. Apply it to the resident
         * Apartment renderer even before Cesium exists, then let the world
         * surface consume the same shared governor when it is mounted. */
        engine.setPerformanceMode?.(
          bundle.resolved?.quality?.performanceMode ?? "adaptive",
        );
        surface?.setViewOptions?.(bundle.resolved);
        /*
         * The glide itself asks to come home, not just a wheel notch.
         *
         * A momentum zoom keeps travelling after the last notch, so waiting for
         * the next wheel event left the camera parked against the ground until
         * the user scrolled again.
         */
        surface?.setReturnBoundaryHandler?.(() => {
          void zoomRequestRef.current?.(1, { source: "world-return-coast" });
        });
      } catch (_) { /* the world keeps whatever it had */ }
    })();
    return () => { cancelled = true; };
  }, [worldViewSelection, spatialScale, spatialTransition]);

  const setWorldViewOption = useCallback((groupKey, presetId) => {
    setWorldViewSelection((previous) => {
      const next = { ...previous, [groupKey]: presetId };
      aptWriteWorldView(next);
      return next;
    });
  }, []);

  const markModelDirty = useCallback(() => {
    if (rehearsalIsolated) return;
    setSaveStatus(simActive
      ? { state: "simulation", detail: "unsaved simulation change · live model untouched" }
      : { state: "unsaved", detail: "changes not yet saved" });
  }, [simActive, rehearsalIsolated]);

  useEffect(() => {
    if (!embedded) return undefined;
    const api = {
      exitEdit: () => setEditing(false),
      closeCards: () => {
        setCardId(null);
        setHoverId(null);
      },
    };
    window.__HOME_APARTMENT_EMBEDDED_API = api;
    return () => {
      if (window.__HOME_APARTMENT_EMBEDDED_API === api) {
        delete window.__HOME_APARTMENT_EMBEDDED_API;
      }
    };
  }, [embedded]);

  const captureCurrentWorkspace = useCallback(() => {
    if (liveShadow) return;
    const engine = engineRef.current || window.__APT_ENGINE;
    if (!engine?.siteId) return;
    const scope = { ...(engine.sceneIdentity || engine.workspaceScope || { siteId: engine.siteId, contentRevision: engine.contentRevision || null }),
      authorityKey: workspaceAuthorityRef.current.key };
    const view = { mode: engine.modes?.mode || "points", az: engine.rig?.azimuthIndex?.() ?? 1,
      el: engine.rig?.elevationIndex?.() ?? 0, zoom: engine.rig?.zoomIndex?.() ?? 0 };
    window.HomeApartmentData.saveViewPreference?.(scope, view);
    if (modelSiteRef.current === engine.siteId && modelScopeRef.current?.authorityKey === scope.authorityKey
        && (modelScopeRef.current?.frameDigest || null) === (scope.frameDigest || null)
        && (modelScopeRef.current?.registrationVersion ?? 0) === (scope.registrationVersion ?? 0)) {
      window.HomeApartmentData.captureWorkspace?.(scope, { ...workspaceStateRef.current, view });
    }
  }, []);

  const clearInteriorState = useCallback(() => {
    captureCurrentWorkspace();
    detachRef.current?.(); detachRef.current = null;
    if (pendingInteriorRef.current) {
      pendingInteriorRef.current.cancelled = true;
      pendingInteriorRef.current.controller?.abort();
      pendingInteriorRef.current.engine?.dispose?.();
      pendingInteriorRef.current = null;
    }
    engineRef.current?.setRunning(false);
    flySeqRef.current += 1;
    if (flyTimerRef.current) { clearTimeout(flyTimerRef.current); flyTimerRef.current = null; }
    modelSiteRef.current = null;
    modelScopeRef.current = null;
    setModel(window.HomeApartmentData.EMPTY_MODEL); setSeedModel(null);
    setRegistry({ entities: [], areas: [], devices: [], states: {} }); statesRef.current = {};
    setLiveCam(null); setLiveOn(false); setLiveFeedStatus("idle"); setCameraPoseReady(false);
    setCalibCam(null); setCardId(null); setHoverId(null); setTrack(null); setEditing(false);
    setAimMode(false); setAimFixtureId(null); setAimDestination(null);
    setInteriorPlaceholder(null); setModeError(null); setModeLoading(null); setError(null);
    setSaveStatus({ state: "idle", detail: "viewing selected home" });
    setSaving(false);
    setLiveReview({ state: "idle", live: null, detail: "" });
    setPhase("loading");
  }, [captureCurrentWorkspace]);

  const bindInteriorEngine = useCallback(async (engine, isCurrent = () => true) => {
        const current = () => isCurrent() && engineRef.current === engine
          && window.HomeSpatialViewport?.getSelectedSiteId?.() === engine.siteId;
        if (!current()) return;
        aptSyncDollhouseContext(engine, dollhouseContextRef.current);
        loadedInteriorSiteRef.current = engine.siteId;
        /* Never carry one interior's disclosure onto the next engine; the new
         * one reports its own once frame.json has been read. */
        setInteriorPlaceholder(engine.scenePlaceholder ?? null);
        Promise.resolve(engine.scenePlaceholderPromise).then((placeholder) => {
          if (isCurrent() && engineRef.current === engine) setInteriorPlaceholder(placeholder ?? null);
        }).catch(() => {});
        const momentum = await engine.createApartmentBoundaryMomentum({
          reducedMotion: aptPrefersReducedMotion(),
        });
        if (!current()) { momentum?.dispose?.(); momentum?.dispatch?.({ type: "reset", reason: "superseded" }); return; }
        boundaryMomentumRef.current = momentum;
        setPhase("loading");
        engine.pointsPromise.then(
          () => current() && setPhase("ready"),
          (e) => { if (current()) { setError(String(e?.message || e)); setPhase("error"); } },
        );
        engine.pointsPromise.then((p) => { if (current()) calibPointsRef.current = p; }).catch(() => {});
        const viewScope = { ...(engine.sceneIdentity || engine.workspaceScope),
          authorityKey: workspaceAuthorityRef.current.key };
        let viewRestored = false, lastViewSignature = "";
        const persistView = () => {
          if (liveShadow || !current() || !viewRestored) return;
          const view = { mode: engine.modes.mode, az: engine.rig.azimuthIndex(),
            el: engine.rig.elevationIndex(), zoom: engine.rig.zoomIndex() };
          const signature = JSON.stringify(view);
          if (signature !== lastViewSignature) {
            lastViewSignature = signature;
            window.HomeApartmentData.saveViewPreference?.(viewScope, view);
          }
        };
        // keep the bottom toggle honest: calibrate/snap switch modes through
        // the engine directly, so subscribe rather than trusting local state —
        // and re-sync NOW: the close path restores the pre-snap mode AFTER
        // these listeners were unsubscribed, so local `mode` can be stale
        const modePresentationScope = engine.sceneIdentity ? { ...engine.sceneIdentity } : null;
        const modePresentationLifecycle = spatialLifecycleRevisionRef.current;
        const modePresentationIsCurrent = () => {
          try {
            if (!modePresentationScope?.siteId || !viewOpenRef.current || engineRef.current !== engine
              || spatialLifecycleRevisionRef.current !== modePresentationLifecycle) return false;
            const site = window.HomeSpatialViewport?.getSite?.(modePresentationScope.siteId);
            if (!site || window.HomeSpatialViewport?.getSelectedSiteId?.() !== modePresentationScope.siteId
              || typeof window.HomeContentPacks?.get !== "function") return false;
            const revision = site.contentRevision || null;
            const manifest = window.HomeContentPacks.get(modePresentationScope.siteId, revision || undefined)?.manifest;
            const identity = engine.sceneIdentity;
            return engine.siteId === modePresentationScope.siteId && identity?.siteId === modePresentationScope.siteId
              && (engine.contentRevision || "legacy") === (modePresentationScope.contentRevision || "legacy")
              && (identity.contentRevision || "legacy") === (modePresentationScope.contentRevision || "legacy")
              && (revision || "legacy") === (modePresentationScope.contentRevision || "legacy")
              && (!manifest || (manifest.revision || "legacy") === (revision || "legacy"))
              && (identity.registrationVersion ?? null) === (modePresentationScope.registrationVersion ?? null)
              && (identity.frameDigest ?? null) === (modePresentationScope.frameDigest ?? null)
              && (manifest?.registrationVersion ?? site.sceneRegistration?.version ?? null) === (modePresentationScope.registrationVersion ?? null)
              && (manifest?.assets?.["frame.json"]?.sha256 ?? null) === (modePresentationScope.frameDigest ?? null);
          } catch (_) { return false; }
        };
        const unsubs = [
          engine.on("progress", (p) => { if (current()) setProgress({ ...p }); }),
          engine.on("stats", (s) => { if (current()) setStats(s); }),
          engine.modes.onLoadProgress?.(({ kind, loaded, total }) => {
            const mode = kind === "splat" || kind === "sparkRenderer" ? "splat" : kind === "mesh" || kind === "collision" ? "mesh" : null;
            if (mode && total && current()) setModeProgress({ mode, percent: Math.min(100, (loaded / total) * 100) });
          }),
          engine.on("contextlost", () => { if (current()) showToast("graphics context lost — recovering"); }),
          engine.modes.onChange((next) => {
            if (modePresentationIsCurrent()) setMode(next);
            if (current()) persistView();
          }),
        ];
        setMode(engine.modes.mode);
        engine.picking.setHost?.(hostRef.current);

        // everything below is synchronous — detachRef is assigned before any
        // call that could throw, so a failure can't strand live listeners
        const host = hostRef.current;
        const size = () => {
          const width = host.clientWidth || window.innerWidth || 1;
          const height = host.clientHeight || window.innerHeight || 1;
          engine.setSize(width, height);
          worldSurfaceRef.current?.resize?.();
          setHostSize((prev) => (
            prev.width === width && prev.height === height ? prev : { width, height }
          ));
        };
        const ro = new ResizeObserver(size);
        const detachInput = engine.attachInput(host, {
          onZoomStep: (direction, meta) => {
            if (!apartmentSceneControlsEnabledRef.current) return;
            const boundaryPhase = boundaryMomentumRef.current?.snapshot?.()?.phase;
            /* Once preview starts, BOTH signs stay with the continuous boundary
             * controller. Sending wheel-up back to the detented rig would zoom
             * the Apartment in while its preview overshoot was still applied,
             * and a trailing event after snap could supersede the handoff with a
             * world-scale intent before the world even owned input. */
            if (meta?.continuousBoundary || (boundaryPhase && boundaryPhase !== "idle")) {
              boundaryWheelRef.current?.(meta);
            }
            else zoomRequestRef.current?.(direction, meta);
          },
        });
        const azTimer = setInterval(() => {
          if (!current()) return;
          setAzIdx(engine.rig.azimuthIndex());
          setInCamPose(!!engine.rig.inCameraPose?.());
          persistView();
        }, 200);
        detachRef.current = () => {
          ro.disconnect(); detachInput(); clearInterval(azTimer);
          unsubs.forEach((u) => { try { u?.(); } catch (e) { /* */ } });
        };
        size();
        ro.observe(host);
        engine.setRunning(aptApartmentOwnsSpatialSurface(spatialCoordinatorRef.current));
        await engine.pointsPromise;
        if (!current()) return;
        const scope = { ...(engine.sceneIdentity || engine.workspaceScope),
          authorityKey: workspaceAuthorityRef.current.key };
        const preference = liveShadow ? null : window.HomeApartmentData.readViewPreference?.(scope);
        if (preference) {
          engine.rig.goTo?.({ az: preference.az, el: preference.el, zoom: preference.zoom, dur: 0 });
          if (!engine.scenePlaceholder && preference.mode !== engine.modes.mode) {
            try { await engine.modes.setMode(preference.mode, { duration: 0 }); }
            catch (error) {
              // Same shape as a picked mode's failure, so the toolbar can offer recovery.
              if (current()) setModeError({ mode: preference.mode, source: "saved-view",
                message: `${preference.mode === "splat" ? "photo" : preference.mode} unavailable`,
                detail: String(error?.message || error || "asset load failed") });
            }
          }
        }
        viewRestored = true;
        persistView();
  }, [showToast]);

  const prepareInteriorForSite = useCallback(async (siteId, isCurrent = () => true) => {
    await window.HomeContentPacks?.ready;
    const site = window.HomeSpatialViewport?.getSite?.(siteId);
    if (!site?.sceneAvailable || !isCurrent()) return false;
    const revision = site.contentRevision || "legacy";
    const preparedScope = aptInteriorWorkspaceScope(siteId);
    const contentIsCurrent = () => isCurrent()
      && (window.HomeSpatialViewport?.getSite?.(siteId)?.contentRevision || "legacy") === revision;
    const oldEngine = engineRef.current || window.__APT_ENGINE;
    if (oldEngine?.siteId === siteId && (oldEngine.contentRevision || "legacy") === revision) {
      await oldEngine.pointsPromise;
      if (!contentIsCurrent()) return false;
      engineRef.current = oldEngine;
      if (hostRef.current && window.__APT_CANVAS?.parentElement !== hostRef.current) hostRef.current.appendChild(window.__APT_CANVAS);
      detachRef.current?.(); detachRef.current = null;
      await bindInteriorEngine(oldEngine, contentIsCurrent);
      return contentIsCurrent();
    }
    const canvas = document.createElement("canvas");
    canvas.style.cssText = "display:block;width:100%;height:100%;";
    const module = await window.Home3D.ready;
    if (!module || !contentIsCurrent()) return false;
    let next = null;
    let adopted = false;
    const pending = { engine: null, cancelled: false, controller: new AbortController() };
    pendingInteriorRef.current = pending;
    try {
      next = await module.createEngine({ canvas, hostEl: hostRef.current, sim: simActive, siteId,
        contentRevision: revision === "legacy" ? null : revision,
        signal: pending.controller.signal,
        performanceController: oldEngine?.performanceController });
      next.contentRevision = revision;
      next.workspaceScope = preparedScope;
      pending.engine = next;
      if (pending.cancelled || !contentIsCurrent()) return false;
      next.setRunning(false);
      next.setSize(hostRef.current?.clientWidth || 1, hostRef.current?.clientHeight || 1);
      const loaded = await aptSettleWithin(next.pointsPromise, APT_HANDOFF_ENDPOINT_BUDGET_MS);
      if (loaded.status !== "settled" || pending.cancelled || !contentIsCurrent()) return false;
      // The complete world retains ownership while the new scan is installed.
      // No model or live-control state from the previous home is presented.
      detachRef.current?.();
      detachRef.current = null;
      oldEngine?.setRunning(false);
      oldEngine?.dispose?.();
      window.__APT_CANVAS?.remove?.();
      hostRef.current?.appendChild(canvas);
      engineRef.current = next;
      window.__APT_ENGINE = next;
      window.__APT_CANVAS = canvas;
      window.__APT_ENGINE_PROMISE = Promise.resolve(next);
      adopted = true;
      if (pendingInteriorRef.current === pending) pendingInteriorRef.current = null;
      setLiveCam(null); setLiveOn(false); setCalibCam(null); setCardId(null);
      await bindInteriorEngine(next, contentIsCurrent);
      return contentIsCurrent();
    } catch (_) { return false; }
    finally {
      if (pendingInteriorRef.current === pending) pendingInteriorRef.current = null;
      if (!adopted) { next?.dispose?.(); canvas.remove(); }
    }
  }, [bindInteriorEngine, simActive]);

  /* ---------------- engine boot ---------------- */
  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;

    (async () => {
      try {
        await window.HomeContentPacks?.ready;
        if (cancelled) return;
        const registry = window.HomeSpatialViewport;
        const siteId = registry?.getSelectedSiteId?.() || "echo";
        await interiorSwitchRequestRef.current?.(siteId, { force: true });
      } catch (e) {
        if (!cancelled) { setError(String(e?.message || e)); setPhase("error"); }
      }
    })();

    return () => {
      cancelled = true;
      if (flyTimerRef.current) { clearTimeout(flyTimerRef.current); flyTimerRef.current = null; }
      captureCurrentWorkspace();
      if (pendingInteriorRef.current) {
        pendingInteriorRef.current.cancelled = true;
        pendingInteriorRef.current.controller?.abort();
        pendingInteriorRef.current.engine?.dispose?.(); pendingInteriorRef.current = null;
      }
      interiorSessionRef.current = null;
      flySeqRef.current += 1;
      detachRef.current?.();
      detachRef.current = null;
      if (boundaryFrameRef.current) cancelAnimationFrame(boundaryFrameRef.current);
      boundaryFrameRef.current = 0;
      boundaryLastFrameRef.current = 0;
      pendingSpatialFocusRef.current = null;
      pendingSpatialFocusElementRef.current = null;
      try { boundaryMomentumRef.current?.dispatch?.({ type: "reset", reason: "view-closed" }); } catch (_) { /* */ }
      boundaryMomentumRef.current = null;
      engineRef.current?.rig?.setOvershoot?.(0);
      // this component instance PERSISTS across open/close (home-app renders
      // it permanently; open=false just renders null) — clear per-session UI
      // state here or a live feed / calibration overlay / control card
      // resurrects the next time the view opens
      setLiveCam(null); setLiveOn(false); setLiveFeedStatus("idle"); setCameraPoseReady(false); setCalibCam(null); setCardId(null);
      setAimMode(false); setAimDestination(null);
      engineRef.current?.overlay?.setAimBeam?.(null);
      setEditing(false);
      spatialLifecycleRevisionRef.current += 1;
      spatialTravelIntentRef.current += 1;
      worldOverviewAbortRef.current?.abort();
      worldOverviewAbortRef.current = null;
      setWorldOverviewActive(false);
      travellingRef.current = false;
      setTravelling(false);
      worldImageryAdopterRef.current?.cancel?.();
      worldImageryAdopterRef.current = null;
      spatialCoordinatorRef.current?.dispose?.();
      spatialCoordinatorRef.current = null;
      spatialCoordinatorPromiseRef.current = null;
      /*
       * Closing the view mid-handoff must give the GPU back explicitly. The
       * canvas element going out of scope would release the context only on the
       * next GC, and WebView2 evicts the OLDEST of its 16 contexts when the cap
       * is hit — which in this app is the Apartment's own. This is the same leak
       * the live-feed warp path already had to fix.
       */
      aptDisposeHandoffCompositorSlots(
        handoffCompositorRef,
        handoffCompositorPendingRef,
        handoffCompositorPromiseRef,
        handoffCompositorGenerationRef,
      );
      coldWorldEntryIntentRef.current = null;
      coldApartmentEntryIntentRef.current = null;
      worldImageryPromotedIntentRef.current = null;
      worldDollhouseRetryIntentRef.current = null;
      if (worldImageryActivationRef.current) {
        worldImageryActivationRef.current.cancelled = true;
      }
      worldImageryActivationGenerationRef.current += 1;
      worldImageryActivationRef.current = null;
      if (handoffMotionRef.current) handoffMotionRef.current.active = false;
      handoffMotionRef.current = null;
      handoffSourceReversalRef.current = null;
      /* The handoff writes these inline every frame; a close at 400ms would
       * otherwise leave the Apartment pinned at 0.0019 scale and invisible when
       * the view reopens. */
      hostRef.current?.style.removeProperty("opacity");
      hostRef.current?.style.removeProperty("transform");
      worldHostRef.current?.style.removeProperty("opacity");
      if (handoffFallbackRef.current) {
        handoffFallbackRef.current.style.opacity = "0";
        handoffFallbackRef.current.style.visibility = "hidden";
      }
      worldEntryPrewarmRef.current?.dispose?.();
      worldEntryPrewarmRef.current = null;
      /* A dynamic import may resolve after close. Clearing the ownership slot
       * makes its installation handler dispose that late controller instead of
       * reviving background work in a closed Apartment view. */
      worldEntryPrewarmPromiseRef.current = null;
      worldSurfacePendingRef.current?.dispose?.();
      worldSurfacePendingRef.current = null;
      worldScaleObserverCleanupRef.current?.();
      worldScaleObserverCleanupRef.current = null;
      worldSurfaceRef.current?.dispose?.();
      worldSurfaceRef.current = null;
      worldSurfacePromiseRef.current = null;
      providerTrafficRef.current?.dispose?.();
      providerTrafficRef.current = null;
      /* Dynamic import can still be resolving. Its lifecycle token disposes a
       * late probe; clearing this slot lets the next open start cleanly. */
      providerTrafficPromiseRef.current = null;
      window.HomeWorldProviders?.forget?.();
      confirmedWorldAnchorRef.current = null;
      worldAnchorKeyRef.current = "";
      setSpatialScale("apartment");
      setSpatialTransition("idle");
      setSpatialTransitionDirection(null);
      const eng = engineRef.current;
      if (eng) {
        // never leave the resident engine in a held/locked pose or with the
        // cloud hidden — a reopen would look dead with no recovery affordance
        if (!liveShadow) eng.rig.resetPose?.();
        if (preSnapModeRef.current != null) {
          const back = preSnapModeRef.current;
          preSnapModeRef.current = null;
          if (eng.modes.mode !== back) eng.modes.setMode(back, { duration: 0 }).catch(() => {});
        }
        eng.setRunning(false);
      }
    };
  }, [open, simActive, showToast, embedded, bindInteriorEngine, captureCurrentWorkspace]);

  /* Once the Apartment is genuinely usable, prepare the hidden local world
   * surface for the selected site's exact anchor/registration revision. This
   * path intentionally has no provider callback and every start repeats the
   * literal false permission: paid/network provider activation remains owned
   * by committed worldLifecycle.prepare(). */
  useEffect(() => {
    const engine = engineRef.current;
    if (!open || phase !== "ready" || !worldZoomEnabled
        || !engine?.createWorldEntryPrewarm) {
      worldEntryPrewarmRef.current?.cancel?.();
      return undefined;
    }

    const anchor = aptConfirmedWorldAnchor(syntheticSpatialQa);
    const site = syntheticSpatialQa
      ? null
      : window.HomeSpatialViewport?.getSelectedSite?.();
    const dollhouseRegistration = site?.dollhouseRegistration ?? null;
    const revision = aptWorldEntryRevision(anchor, dollhouseRegistration);
    if (!anchor || !revision) {
      worldEntryPrewarmRef.current?.cancel?.();
      return undefined;
    }

    let cancelled = false;
    const lifecycleRevision = spatialLifecycleRevisionRef.current;
    (async () => {
      let controllerPromise = worldEntryPrewarmPromiseRef.current;
      if (!controllerPromise) {
        /* CSS visibility:hidden keeps a cold local prewarm invisible, but Cesium
         * Model readiness still needs scene updates. Share one bounded frame
         * lease across the parallel readiness branches. A retained active or
         * staged Google surface is deliberately ineligible: cached queries may
         * still settle while it is parked, and committed worldLifecycle.prepare
         * remains the only authority that may restart its renderer. */
        let hiddenReadinessUsers = 0;
        let suspendAfterHiddenReadiness = false;
        const withHiddenWorldFrames = async (context, operation) => {
          if (context.signal?.aborted) return { status: "cancelled" };
          if (hiddenReadinessUsers === 0) {
            suspendAfterHiddenReadiness = aptStartLocalWorldPrewarmFrames(context.surface);
          }
          hiddenReadinessUsers += 1;
          try {
            return await operation();
          } finally {
            hiddenReadinessUsers = Math.max(0, hiddenReadinessUsers - 1);
            if (hiddenReadinessUsers === 0) {
              const shouldSuspend = suspendAfterHiddenReadiness;
              suspendAfterHiddenReadiness = false;
              const apartmentStillOwnsSurface = aptApartmentOwnsSpatialSurface(
                spatialCoordinatorRef.current,
              );
              if (shouldSuspend && apartmentStillOwnsSurface) {
                try { context.surface.setRunning(false); } catch (_) { /* disposed */ }
              }
            }
          }
        };
        controllerPromise = engine.createWorldEntryPrewarm({
          ensureSurface: async ({ signal }) => {
            const surface = await ensureWorldSurface();
            return signal.aborted ? { status: "cancelled" } : surface;
          },
          applyTarget: ({ target, surface, signal }) => {
            if (signal.aborted) return "cancelled";
            if (target.viewOptions) surface.setViewOptions?.(target.viewOptions);
            surface.setAnchor(target.anchor);
            surface.setDollhouseRegistration?.(target.dollhouseRegistration);
            confirmedWorldAnchorRef.current = target.anchor;
            worldAnchorKeyRef.current = aptWorldAnchorKey(
              target.anchor,
              target.dollhouseRegistration,
            );
            return "ready";
          },
          resolveSurfaceHeight: (context) => withHiddenWorldFrames(context, () => (
            context.surface.resolveSurfaceHeight({ timeoutMs: context.timeoutMs })
          )),
          resolveDollhouseGroundHeight: (context) => withHiddenWorldFrames(context, () => (
            context.surface.resolveDollhouseGroundHeight?.({ timeoutMs: context.timeoutMs })
              ?? { status: "not-required" }
          )),
          /* setDollhouseRegistration above starts cache loading with show:false.
           * Speculation must never call waitForDollhouse: that method opens the
           * reveal gate, and a join timeout does not cancel background work.
           * Only a successfully activated world intent may reveal the cache. */
        });
        worldEntryPrewarmPromiseRef.current = controllerPromise;
        controllerPromise.then(
          (controller) => {
            if (worldEntryPrewarmPromiseRef.current !== controllerPromise) {
              controller.dispose?.();
              return;
            }
            worldEntryPrewarmRef.current = controller;
          },
          () => {
            if (worldEntryPrewarmPromiseRef.current === controllerPromise) {
              worldEntryPrewarmPromiseRef.current = null;
            }
          },
        );
      }

      let controller;
      try {
        controller = await controllerPromise;
      } catch (_) {
        return;
      }
      if (cancelled || spatialLifecycleRevisionRef.current !== lifecycleRevision
          || worldEntryPrewarmPromiseRef.current !== controllerPromise) return;

      let bundle;
      try {
        bundle = await engine.worldViewOptions(worldViewSelection);
      } catch (_) {
        return;
      }
      if (cancelled || spatialLifecycleRevisionRef.current !== lifecycleRevision
          || !aptApartmentOwnsSpatialSurface(spatialCoordinatorRef.current)) return;

      const connectionInfo = window.navigator?.connection ?? null;
      void controller.start({
        apartmentOpen: true,
        apartmentReady: true,
        worldZoomEnabled: true,
        network: connectionInfo,
        streamingPreset: bundle?.resolved?.streaming ?? null,
        revision,
        target: Object.freeze({
          anchor,
          dollhouseRegistration,
          viewOptions: bundle?.resolved ?? null,
        }),
        allowProviderActivation: false,
      }).catch(() => {
        /* Preparation is opportunistic. Committed entry owns the fail-soft
         * fallback and must remain reachable after every background failure. */
      });
    })();

    return () => { cancelled = true; };
  }, [open, phase, selectedSiteId, worldViewSelection, syntheticSpatialQa,
    worldZoomEnabled, ensureWorldSurface]);

  /* Once the cloud is usable, quietly load photo + mesh into the resident
     engine. The post-boot prewarmer handles network bytes; this handles parser
     and GPU readiness without switching the visible mode. */
  useEffect(() => {
    if (!open || phase !== "ready") return undefined;
    if (liveShadow) return undefined;
    const engine = engineRef.current;
    if (!engine?.modes?.preload || !aptShouldModePrewarm()) return undefined;
    if (modePrewarmRef.current?.engine === engine && modePrewarmRef.current.done) return undefined;
    const token = { engine, done: false };
    modePrewarmRef.current = token;

    let cancelled = false;
    let completed = 0;
    const timers = [];
    const idleIds = [];
    const requestIdle = (cb, timeout) => {
      if (window.requestIdleCallback) {
        const id = window.requestIdleCallback(cb, { timeout });
        idleIds.push(["idle", id]);
        return;
      }
      const id = setTimeout(() => cb({ didTimeout: true, timeRemaining: () => 0 }), Math.min(timeout, 900));
      idleIds.push(["timeout", id]);
    };
    const schedule = (delay, timeout, cb) => {
      timers.push(setTimeout(() => requestIdle(async () => {
        if (cancelled) return;
        const result = await cb();
        if (!cancelled) {
          completed += 1;
          token.done = completed >= 3;
          window.dispatchEvent(new CustomEvent("home-apartment-mode-prewarm", { detail: result }));
        }
      }, timeout), delay));
    };

    schedule(40, 1200, () => engine.modes.preload(["collision"]));
    schedule(mobile ? 450 : 700, 2500, () => engine.modes.preload(["splat"]));
    schedule(mobile ? 2600 : 1600, 4500, () => engine.modes.preload(["mesh"]));

    return () => {
      cancelled = true;
      timers.forEach((id) => clearTimeout(id));
      idleIds.forEach(([kind, id]) => {
        if (kind === "idle") window.cancelIdleCallback?.(id);
        else clearTimeout(id);
      });
      if (modePrewarmRef.current === token && !token.done) modePrewarmRef.current = null;
    };
  }, [open, phase, mobile]);

  /* ---------------- model + registry load ---------------- */
  useEffect(() => {
    if (!open) return undefined;
    if (liveShadow) {
      setModel(window.HomeLiveResidentModel);
      setSeedModel(window.HomeLiveResidentModel);
      setRegistry({ entities: [], areas: [], devices: [], states: {} });
      setSaveStatus({ state: 'idle', detail: 'registered shadow model · read only' });
      return undefined;
    }
    let dead = false;
    setLiveReview({ state: "idle", live: null, detail: "" });
    if (mapRehearsalOpen || window.HomeMapRehearsalOfflineState?.readback?.().preview_released) return undefined;
    (async () => {
      /* Per-site: the seed model is a content-pack asset, and without the id
       * this resolved to Echo's for whichever home was open. */
      const scope = aptInteriorWorkspaceScope(selectedSiteId, workspaceAuthorityRef.current.key);
      const current = () => !dead && window.HomeSpatialViewport?.getSelectedSiteId?.() === scope.siteId
        && workspaceAuthorityRef.current.key === scope.authorityKey
        && (window.HomeSpatialViewport?.getSite?.(scope.siteId)?.contentRevision || null) === scope.contentRevision
        && (window.HomeSpatialViewport?.getSite?.(scope.siteId)?.sceneRegistration?.version ?? 0) === scope.registrationVersion
        && (window.HomeContentPacks?.get?.(scope.siteId, scope.contentRevision)?.manifest?.assets?.["frame.json"]?.sha256 || null) === scope.frameDigest;
      const workspace = window.HomeApartmentData.restoreWorkspace?.(scope);
      if (workspace?.model && current()) {
        modelSiteRef.current = scope.siteId;
        modelScopeRef.current = scope;
        setModel(workspace.model);
        if (workspace.dirty) setSaveStatus({ state: "unsaved", detail: "session changes preserved" });
      }
      const seed = await window.HomeApartmentData.getSeedModel?.(scope.siteId, { contentRevision: scope.contentRevision });
      if (!current()) return;
      setSeedModel(seed || null);
      if (workspace?.dirty) return;
      if (simActive && !dead) setRegistry({ entities: [], areas: [], devices: [], states: {} });
      // retry until a true remote load lands — the first attempt can race
      // tauriFetch readiness and fall back to a cached/seed copy, which the
      // user reads as "my saves were overwritten"
      for (let attempt = 0; attempt < 10 && !dead; attempt++) {
        const m = await window.HomeApartmentData.getModel({ endpoint, token, sim: simActive,
          ...scope, equipmentRegistration: window.HG_WEB_MODE === true });
        if (!current() || (modelSiteRef.current === scope.siteId && workspaceStateRef.current?.dirty
            && modelScopeRef.current?.authorityKey === scope.authorityKey
            && modelScopeRef.current?.frameDigest === scope.frameDigest
            && modelScopeRef.current?.registrationVersion === scope.registrationVersion)) return;
        modelSiteRef.current = scope.siteId;
        modelScopeRef.current = scope;
        setModel(m);
        const meta = window.HomeApartmentData.modelSourceMeta?.(m) || {};
        setSaveStatus(meta.kind === "simulation"
          ? { state: "simulation", detail: "isolated · cannot save live" }
          : meta.kind === "live_ha_model" || meta.kind === "equipment_registration"
            ? { state: "saved", detail: `revision ${m.revision}` }
            : meta.kind === "local_draft"
              ? { state: "offline", detail: "local draft · not saved to Home Assistant" }
              : { state: "idle", detail: meta.label || "loaded" });
        const fromRemote = meta.kind === "live_ha_model"
          || (meta.kind === "seed_model" && Number.isInteger(m.source_detail?.authoritative_revision));
        if (fromRemote || simActive || !token || scope.siteId !== "echo") break;
        await new Promise((r) => setTimeout(r, 2500));
      }
    })();
    return () => { dead = true; };
  /* selectedSiteId included: the seed model is per-site, so switching homes
   * must reload it rather than leaving the previous home's in place. */
  }, [open, endpoint, token, simActive, selectedSiteId, selectedContentRevision, mapRehearsalOpen, equipmentReloadEpoch]);

  /* Registry inventory follows the authenticated HA connection separately
     from the Apartment document. Reconnecting must refresh all real lights,
     but must never reload the model over unsaved editor changes. */
  useEffect(() => {
    if (!open) return undefined;
    if (simActive || rehearsalIsolated || selectedSiteId !== "echo") {
      setRegistry({ entities: [], areas: [], devices: [], states: {} });
      return undefined;
    }
    if (connection !== "online") return undefined;
    const client = window.__hav_haClient;
    if (!client) return undefined;
    let dead = false;
    window.HomeApartmentData.getRegistry(client, { siteId: selectedSiteId }).then((nextRegistry) => {
      if (!dead && window.HomeSpatialViewport?.getSelectedSiteId?.() === "echo") setRegistry(nextRegistry);
    }).catch(() => {
      if (!dead) setRegistry({ entities: [], areas: [], devices: [], states: {} });
    });
    return () => { dead = true; };
  }, [open, simActive, connection, endpoint, token, serviceEpoch, rehearsalIsolated, selectedSiteId]);

  /* correspondence pick: NATIVE capture-phase listener — the rig's input
     handlers stop propagation, so React synthetic events never fire on the
     host (this is why Step 2 clicks "did nothing") */
  useEffect(() => {
    const host = hostRef.current;
    if (!host || !apartmentSceneControlsEnabled) return undefined;
    const onPick = (ev) => {
      if (!calibPickRef.current || !calibApiRef.current) return;
      const e = engineRef.current;
      if (!e) return;
      // MESH-ONLY raycast: true surface intersection. Point-cloud picking
      // accepted any stray point within 4cm of the ray anywhere along it —
      // corners "snapped" to wall-top fragments behind them (user-diagnosed:
      // pairs floated once the camera rotated).
      const targets = [e.modes.getMesh && e.modes.getMesh()].filter(Boolean);
      const hits = (e.picking.pick(targets, ev.clientX, ev.clientY) || [])
        .filter((h) => h.point);
      let local = null;
      if (hits.length) {
        local = e.apartmentRoot.worldToLocal(hits[0].point.clone());
      } else {
        const f = e.picking.floorPoint(e.apartmentRoot, ev.clientX, ev.clientY);
        if (f) local = { x: f[0], y: f[1], z: 0 };
      }
      if (local) {
        calibApiRef.current.acceptScenePoint([
          +local.x.toFixed(3), +local.y.toFixed(3), +local.z.toFixed(3)]);
        ev.stopPropagation();
      }
    };
    // drag-to-adjust: grab a green pair marker, slide it along the mesh
    let dragIdx = -1;
    const meshTargets = () => {
      const e = engineRef.current;
      return [e && e.modes.getMesh && e.modes.getMesh()].filter(Boolean);
    };
    const onDown = (ev) => {
      const e = engineRef.current;
      if (!e || !calibApiRef.current || !e.overlay.calibGroup) return;
      const hits = e.picking.pick([e.overlay.calibGroup], ev.clientX, ev.clientY) || [];
      const hit = hits.find((h) => h.object && h.object.geometry &&
                                   h.object.geometry.type === "SphereGeometry");
      if (!hit) return;
      dragIdx = Math.floor(e.overlay.calibGroup.children.indexOf(hit.object) / 2);
      ev.stopPropagation(); ev.preventDefault();
    };
    const onMove = (ev) => {
      if (dragIdx < 0) return;
      const e = engineRef.current;
      const hits = (e.picking.pick(meshTargets(), ev.clientX, ev.clientY) || [])
        .filter((h) => h.point);
      if (hits.length) {
        const l = e.apartmentRoot.worldToLocal(hits[0].point.clone());
        e.overlay.moveCalibMarker(dragIdx, [l.x, l.y, l.z]);
      }
      ev.stopPropagation();
    };
    const onUp = (ev) => {
      if (dragIdx < 0) return;
      const e = engineRef.current;
      const s = e.overlay.calibGroup.children[dragIdx * 2];
      if (s && calibApiRef.current && calibApiRef.current.updatePair) {
        calibApiRef.current.updatePair(dragIdx,
          [+s.position.x.toFixed(3), +s.position.y.toFixed(3), +s.position.z.toFixed(3)]);
      }
      dragIdx = -1;
      ev.stopPropagation();
    };
    host.addEventListener("click", onPick, true);
    host.addEventListener("pointerdown", onDown, true);
    host.addEventListener("pointermove", onMove, true);
    host.addEventListener("pointerup", onUp, true);
    return () => {
      host.removeEventListener("click", onPick, true);
      host.removeEventListener("pointerdown", onDown, true);
      host.removeEventListener("pointermove", onMove, true);
      host.removeEventListener("pointerup", onUp, true);
    };
  }, [open, phase, apartmentSceneControlsEnabled]);

  /* 3D confirmation markers for banked pairs — rendered by the engine's
     overlay (the Babel layer has no working dynamic import of three) */
  const syncCalibMarkers = (pairs) => {
    const e = engineRef.current;
    if (e && e.overlay.setCalibMarkers) {
      e.overlay.setCalibMarkers((pairs || []).map((p) => p.xyz));
    }
  };

  /* model -> overlay sync */
  useEffect(() => {
    if (liveShadow || mapRehearsalOpen) return;
    const engine = engineRef.current;
    if (!engine || phase !== "ready") return;
    if (engine.siteId !== selectedSiteId || !apartmentPlacementDisplayEnabled) {
      engine.overlay.setDevices([]); engine.overlay.setZones([]); engine.overlay.setTargets?.([]);
      engine.overlay.setEquipmentRegistration?.(null);
      return;
    }
    // The editor owns selected-wall/measurement highlights during editing;
    // parent passive effects must not clear the child's highlighted reference.
    if (!editing || !model._equipment_registration) engine.overlay.setEquipmentRegistration?.(model._equipment_registration || null);
    engine.overlay.setDevices(model.devices || []);
    engine.overlay.setZones(model.zones || []);
    engine.overlay.setTargets?.(model.targets || []);
    for (const [eid, st] of Object.entries(statesRef.current)) {
      const dev = (model.devices || []).find((d) => d.ha_entity_id === eid);
      if (dev) engine.overlay.setDeviceState(dev.id, st);
    }
    if (editing) engine.overlay.setZonesVisible(0.7);
  }, [model, phase, editing, selectedSiteId, mapRehearsalOpen, apartmentPlacementDisplayEnabled]);

  // The leased desk preview owns its fixture's aim; remembered manual state
  // never overwrites that aim while the preview is active.
  const reapplyVirtualFixtureMemory = (overlay, ownedId) => {
    for (const [id, state] of virtualFixtureStatesRef.current.states) if (id !== ownedId) overlay.setVirtualFixtureState?.(id, state);
    for (const [id, target] of virtualFixtureStatesRef.current.targets) if (id !== ownedId) overlay.aimVirtualFixtureAtTarget?.(id, target);
  };
  // This ordinary-view preview reads the admitted survey without supplying
  // missing optical origins or entering the committed-execution workflow.
  useEffect(() => {
    const engine = engineRef.current;
    const overlay = engine?.overlay;
    if (!overlay?.setVirtualFixtures) return;
    const identity = engine.sceneIdentity, binding = virtualFixtureDocument?.binding;
    const matchesScene = binding && identity?.siteId === selectedSiteId
      && identity.contentRevision === binding.content_revision
      && identity.registrationVersion === binding.registration_version
      && identity.frameDigest === binding.frame_digest;
    const document = open && phase === "ready" && matchesScene ? virtualFixtureDocument : null;
    overlay.setVirtualFixtures(document);
    if (document) {
      if (virtualFixtureStatesRef.current.key !== virtualFixtureBindingKey) {
        virtualFixtureStatesRef.current = { key: virtualFixtureBindingKey, states: new Map(), targets: new Map() };
        virtualAimHistoryRef.current = { undo: [], redo: [] };
        setVirtualAimHistoryVersion(value => value + 1);
        setVirtualAimNotice("");
        setVirtualInspectorTab("aim");
        setFixtureInteractionMode("spot");
      }
      const preview = denDeskAimPreviewRef.current?.readback?.();
      reapplyVirtualFixtureMemory(overlay, preview?.active ? preview.fixtureId : null);
    }
    overlay.setVirtualFixturesVisible(virtualSpotlightsVisible && !editing && !calibCam
      && !liveOn && spatialScale === "apartment");
    if (document) lightingBrainRef.current?.follower.refresh();
    setVirtualFixturePreview(overlay.virtualFixturesReadback?.() || null);
  }, [open, phase, selectedSiteId, selectedContentRevision, virtualFixtureDocument,
    virtualFixtureBindingKey, virtualSpotlightsVisible, editing, calibCam, liveOn, spatialScale]);

  // Always on while the Victoria view shows the registered fixtures. Brain
  // offline holds the last applied state; physical output is never involved.
  useEffect(() => {
    const engine = engineRef.current, Follower = window.HomeLightingBrainFollower, Client = window.HomeLightingBrainClient;
    if (!open || phase !== "ready" || !virtualFixtureBindingKey || rehearsalIsolated || !Follower || !Client
        || !engine?.overlay?.setVirtualFixtureState || typeof window.fetch !== "function") {
      setLightingBrain(null); return undefined;
    }
    const following = Follower.createBrainFollowing({ Client, fetch: window.fetch.bind(window), EventSource: window.EventSource,
      setTimeout: (fn, ms) => window.setTimeout(fn, ms), clearTimeout: (id) => window.clearTimeout(id),
      getOverlay: () => engineRef.current === engine ? engine.overlay : null,
      onChange: setLightingBrain,
      onApplied: () => { if (engineRef.current === engine) syncVirtualFixturePreview(engine); } });
    lightingBrainRef.current = following;
    // AL-5 monitor banner: polled while visible; a failed read shows nothing.
    const ops = Client.createOpsStatusPoller({ fetch: window.fetch.bind(window),
      setTimeout: (fn, ms) => window.setTimeout(fn, ms), clearTimeout: (id) => window.clearTimeout(id),
      isHidden: () => document.hidden === true, onChange: setDenOpsBanner });
    const visible = () => ops.wake();
    document.addEventListener("visibilitychange", visible);
    ops.start();
    return () => {
      document.removeEventListener("visibilitychange", visible);
      ops.stop(); setDenOpsBanner(null);
      following.dispose();
      if (lightingBrainRef.current === following) lightingBrainRef.current = null;
    };
  }, [open, phase, virtualFixtureBindingKey, rehearsalIsolated]);

  const syncVirtualFixturePreview = (engine, suppliedResult, publish = true) => {
    const result = suppliedResult || engine?.overlay.virtualFixturesReadback?.();
    if (!result) return;
    // The active desk preview's aim is temporary and never becomes remembered state.
    const preview = denDeskAimPreviewRef.current?.readback?.(), ownedId = preview?.active ? preview.fixtureId : null;
    for (const fixture of result.fixtures || []) {
      if (fixture.fixture_id === ownedId) continue;
      const state = fixture.state;
      virtualFixtureStatesRef.current.states.set(fixture.fixture_id, {
        radials: state.radials.map(({ number, on, brightness, color_temp_kelvin }) => ({ number, on, brightness, color_temp_kelvin })),
        downward: { ...state.downward }, spotlight: { ...state.spotlight }, pan_deg: state.pan_deg, tilt_deg: state.tilt_deg,
      });
      const target = engine.overlay.virtualFixtureInteractionDescriptor?.(fixture.fixture_id)?.target;
      if (target) virtualFixtureStatesRef.current.targets.set(fixture.fixture_id, target);
      else virtualFixtureStatesRef.current.targets.delete(fixture.fixture_id);
    }
    if (publish) { virtualFixturePreviewDirtyRef.current = false; setVirtualFixturePreview(result); }
    else virtualFixturePreviewDirtyRef.current = true;
  };
  const changeVirtualFixture = (patch, fixtureId = selectedVirtualFixture?.fixture_id, { deferPreview = false } = {}) => {
    const engine = engineRef.current, binding = virtualFixtureDocument?.binding, identity = engine?.sceneIdentity;
    if (!virtualFixtureItems.some(fixture => fixture.fixture_id === fixtureId) || !binding || phase !== "ready" || workspaceAuthorityRef.current.key !== apartmentAuthorityKey
      || identity?.siteId !== "victoria"
      || identity.contentRevision !== binding.content_revision || identity.registrationVersion !== binding.registration_version
      || identity.frameDigest !== binding.frame_digest) return;
    const result = engine.overlay.setVirtualFixtureState?.(fixtureId, patch);
    if (!result?.ok) return;
    // Targets, remembered state and the selected readout publish synchronously.
    // Only the unrelated full-room React summary may wait for the HUD turn.
    syncVirtualFixturePreview(engine, result, !deferPreview);
    directionalViewRef.current?.sync(engine.overlay.virtualFixtureInteractionDescriptor?.(fixtureId));
    return true;
  };
  /* The one hand-change switch point (Den coordination agreement, 2026-10-19).
   * Every fixture change a person makes in the UI calls it synchronously
   * first: channel edits and sliders, aim Set, undo and redo. On the UI line it
   * does nothing. Den binds this one function to manualTakeover(), and later
   * to aimPreview.releaseForManual(fixtureId, { reason }), when it composes the
   * desk preview. It never throws and never starts aiming. Proposals change no
   * fixture, so proposing, discarding and restoring a proposal do not call it. */
  const releaseFixtureForHandChange = (fixtureId, { reason } = {}) => {
    let released = false;
    try {
      const preview = denDeskAimPreviewRef.current, owned = preview?.readback?.();
      released = !!(owned?.active && owned.fixtureId === fixtureId);
      preview?.manualTakeover?.(reason === "aim" ? "manual_aim_gesture"
        : reason === "undo" || reason === "redo" ? "manual_aim_history" : "manual_fixture_control");
    } catch (_) { /* The seam never blocks a person's change. */ }
    // AL-4: a person's change pauses only that fixture and posts the brain
    // override before the change is applied.
    try { lightingBrainRef.current?.follower.handChange(fixtureId, { reason: reason || "change" }); } catch (_) { /* as above */ }
    return { released, restored: false, excluded: false };
  };
  const changeVirtualFixtureByHand = (patch, fixtureId = selectedVirtualFixture?.fixture_id, options) => {
    releaseFixtureForHandChange(fixtureId, { reason: "change" });
    return changeVirtualFixture(patch, fixtureId, options);
  };
  const changeVirtualChannel = (patch) => changeVirtualFixtureByHand(virtualFixtureChannel.startsWith("radial-")
    ? { radials: [{ number: Number(virtualFixtureChannel.slice(7)), ...patch }] }
    : { [virtualFixtureChannel]: { ...patch, ...(patch.brightness > 0 && !selectedVirtualChannel?.on ? { on: true } : {}) } });

  // Selection is presentation only. Capture the installed viewport lifetime,
  // rather than a travel intent that may be canceled without replacing it.
  const selectionEngine = engineRef.current;
  const selectionLifecycle = spatialLifecycleRevisionRef.current;
  const getVirtualFixtureSelectionContext = useCallback(() => {
    try {
      const engine = selectionEngine, document = virtualFixtureDocument, binding = document?.binding;
      if (!open || phase !== "ready" || !viewOpenRef.current || !apartmentSceneControlsEnabledRef.current
          || !engine || engineRef.current !== engine || spatialLifecycleRevisionRef.current !== selectionLifecycle
          || workspaceAuthorityRef.current.key !== apartmentAuthorityKey || modelSiteRef.current !== "victoria"
          || modelScopeRef.current?.authorityKey !== apartmentAuthorityKey || !binding
          || modelScopeRef.current.frameDigest !== binding.frame_digest
          || modelScopeRef.current.registrationVersion !== binding.registration_version
          || modelScopeRef.current.contentRevision !== binding.content_revision
          || selectedSiteId !== "victoria" || window.HomeSpatialViewport?.getSelectedSiteId?.() !== "victoria"
          || engine.siteId !== "victoria" || engine.sceneIdentity?.siteId !== "victoria"
          || engine.contentRevision !== binding.content_revision
          || engine.sceneIdentity.contentRevision !== binding.content_revision
          || engine.sceneIdentity.registrationVersion !== binding.registration_version
          || engine.sceneIdentity.frameDigest !== binding.frame_digest
          || editing || calibCam || liveOn || rehearsalIsolated
          || spatialScale !== "apartment" || travellingRef.current || deskTargetCaptureRef.current?.active) return null;
      const preview = engine.overlay.virtualFixturesReadback?.();
      if (preview?.status !== "ready"
          || preview.revision !== document.revision || preview.digest !== document.digest) return null;
      return { engine, document, preview };
    } catch (_) { return null; }
  }, [selectionEngine, selectionLifecycle, open, phase, apartmentAuthorityKey, selectedSiteId,
    virtualFixtureDocument, editing, calibCam, liveOn, rehearsalIsolated, spatialScale]);

  publishVirtualFixturePreviewRef.current = () => {
    const context = getVirtualFixtureSelectionContext();
    if (context) syncVirtualFixturePreview(context.engine, context.preview);
    else virtualFixturePreviewDirtyRef.current = false;
  };

  const selectVirtualFixture = useCallback((id, { fromGesture = false, reveal = true } = {}) => {
    try {
      const context = getVirtualFixtureSelectionContext();
      if (!context || typeof id !== "string") return false;
      const { engine, document, preview } = context;
      if (fromGesture && preview.visible !== true) return false;
      const gesture = virtualAimGestureRef.current?.readback();
      if (gesture?.active && !(fromGesture && gesture.fixture_id === id)) return false;
      if (!preview.fixtures?.some(fixture => fixture.fixture_id === id)) return false;
      const descriptor = engine.overlay.virtualFixtureInteractionDescriptor?.(id), binding = document.binding;
      if (!descriptor || descriptor.fixture_id !== id || descriptor.equipment_revision !== document.revision
          || descriptor.equipment_digest !== document.digest || !descriptor.binding
          || Object.keys(descriptor.binding).length !== Object.keys(binding).length
          || Object.keys(binding).some(key => descriptor.binding[key] !== binding[key])) return false;
      const selected = engine.overlay.setVirtualFixtureSelected?.(id);
      if (selected?.fixture_id !== id || getVirtualFixtureSelectionContext()?.engine !== engine) return false;
      if (reveal) {
        // Selecting from nothing opens Light (SPEC › Selection precedence 2);
        // a later selection keeps the tab it finds.
        if (!virtualFixtureControlsOpenRef.current) {
          fixtureInteractionModeRef.current = "zones"; setFixtureInteractionMode("zones"); setVirtualInspectorTab("light");
        }
        setVirtualFixtureId(id); setVirtualFixtureControlsOpen(true);
        if (unifiedShell) onShowShellControls?.();
      }
      return true;
    } catch (_) { return false; }
  }, [getVirtualFixtureSelectionContext, unifiedShell, onShowShellControls]);

  // A returned home can have a fresh overlay but retained inspector state.
  // Restore only its admitted highlight; don't reopen or switch shell panels.
  useEffect(() => {
    if (virtualFixtureControlsOpen && virtualFixtureId) selectVirtualFixture(virtualFixtureId, { reveal: false });
  }, [selectVirtualFixture, virtualFixtureControlsOpen, virtualFixtureId,
    virtualFixturePreview?.revision, virtualFixturePreview?.digest]);

  const changeFixtureInteractionMode = useCallback(mode => {
    const context = getVirtualFixtureSelectionContext();
    if (!["zones", "spot"].includes(mode) || !context
        || radialGestureOwnerRef.current || virtualAimGestureRef.current?.readback().active) return false;
    fixtureInteractionModeRef.current = mode;
    setFixtureInteractionMode(mode);
    setVirtualInspectorTab(mode === "zones" ? "light" : "aim");
    const view = directionalViewRef.current;
    if (view) view.sync(context.engine.overlay.virtualFixtureInteractionDescriptor?.(view.getSnapshot().descriptor.fixture_id));
    return true;
  }, [getVirtualFixtureSelectionContext]);
  const changeVirtualInspectorTab = useCallback(tab => {
    if (tab === "light" || tab === "aim") return changeFixtureInteractionMode(tab === "light" ? "zones" : "spot");
    if (tab !== "setup" || radialGestureOwnerRef.current || virtualAimGestureRef.current?.readback().active) return false;
    setVirtualInspectorTab(tab);
    return true;
  }, [changeFixtureInteractionMode]);
  const directionalCameraKey = engine => {
    // Hover parallax continues settling every RAF. It must not retire painting.
    // Public rig navigation, projection, root and viewport changes still do.
    const pose = engine.rig.snapshotPose?.();
    return JSON.stringify([hostRef.current?.clientWidth, hostRef.current?.clientHeight,
      engine.camera.projectionMatrix.elements, engine.apartmentRoot.matrixWorld.elements,
      engine.rig.state?.locked, engine.rig.inCameraPose?.(),
      pose && [pose.az, pose.el, pose.zoom, pose.curAz, pose.curEl, pose.curRadius,
        pose.previewAz, pose.previewEl, pose.overshoot, pose.fitDistance, pose.target]]);
  };
  const getDirectionalSnapshot = useCallback((id, { drawing = true } = {}) => {
    const context = getVirtualFixtureSelectionContext();
    if (!context || !virtualFixtureControlsOpen || !virtualSpotlightsVisible || id !== virtualFixtureId || context.preview.visible !== true
        || drawing && (context.engine.rig.state?.locked || context.engine.rig.inCameraPose?.() || context.engine.rig.isTweening?.()
          || virtualAimGestureRef.current?.readback().active)) return null;
    const descriptor = context.engine.overlay.virtualFixtureInteractionDescriptor?.(id), binding = context.document.binding;
    if (!descriptor || descriptor.truth_role !== "virtual_preview" || descriptor.physical_commands !== false
        || descriptor.fixture_id !== id || descriptor.equipment_revision !== context.document.revision
        || descriptor.equipment_digest !== context.document.digest || !descriptor.binding
        || Object.keys(descriptor.binding).length !== Object.keys(binding).length
        || Object.keys(binding).some(key => descriptor.binding[key] !== binding[key])) return null;
    return { ...context, descriptor };
  }, [getVirtualFixtureSelectionContext, virtualFixtureControlsOpen, virtualSpotlightsVisible, virtualFixtureId]);
  const isDirectionalProjectionCurrent = useCallback(projection => {
    const context = getDirectionalSnapshot(projection?.descriptor.fixture_id), api = window.HomeApartmentDirectional;
    return !!(context && api && projection.geometry && api.identityKey(projection.descriptor) === api.identityKey(context.descriptor)
      && projection.key === directionalCameraKey(context.engine));
  }, [getDirectionalSnapshot]);
  const projectDirectionalFixture = useCallback(() => {
    try {
      const context = getDirectionalSnapshot(virtualFixtureId), api = window.HomeApartmentDirectional, host = hostRef.current;
      if (!context || !api || !host?.isConnected || host.hidden || host.inert || host.closest?.('[hidden], [inert]')) return null;
      const { engine, descriptor } = context;
      const matrix = engine.camera.projectionMatrix.clone().multiply(engine.camera.matrixWorldInverse).multiply(engine.apartmentRoot.matrixWorld);
      const frame = api.createPlaneFrame({ anchor: descriptor.display_anchor_m, clipFromLocal: matrix.elements,
        width: host.clientWidth, height: host.clientHeight });
      if (!frame?.project([0, 0])?.visible) return null;
      return { frame, descriptor: { ...descriptor, label: context.preview.fixtures.find(item => item.fixture_id === virtualFixtureId)?.label },
        key: directionalCameraKey(engine) };
    } catch (_) { return null; }
  }, [getDirectionalSnapshot, virtualFixtureId]);
  const directionalProjectRef = useRef(projectDirectionalFixture);
  directionalProjectRef.current = projectDirectionalFixture;

  // Drawing borrows the resident engine's admitted frame. Logical targets and
  // readouts remain event-driven; no second render loop or delayed input queue.
  useEffect(() => {
    const engine = selectionEngine, host = hostRef.current, api = window.HomeApartmentDirectional;
    const initial = getDirectionalSnapshot(virtualFixtureId, { drawing: false });
    if (!engine || !host || !api || !initial || !apartmentSceneControlsEnabled) return undefined;
    const identity = api.identityKey(initial.descriptor);
    let closed = false, lease = null;
    const admitted = () => {
      const context = getDirectionalSnapshot(virtualFixtureId, { drawing: false });
      return !closed && document.visibilityState !== "hidden" && context?.engine === engine
        && api.identityKey(context.descriptor) === identity && host.isConnected;
    };
    const retire = owned => {
      if (!owned || owned.retired) return;
      owned.retired = true;
      const view = owned.view;
      if (directionalViewRef.current === view && view) {
        directionalProjectionRef.current = null;
        radialGestureOwnerRef.current?.cancel();
        directionalViewRef.current = null;
      }
      try { view?.dispose(); } finally {
        owned.controller.abort(); owned.layer?.dispose();
        setDirectionalView(previous => previous === view ? null : previous);
      }
      if (lease === owned) lease = null;
    };
    const start = async () => {
      if (lease || !admitted()) return;
      const owned = { controller: new AbortController(), layer: null, view: null, retired: false };
      lease = owned;
      const current = () => lease === owned && !owned.retired && !owned.controller.signal.aborted && admitted();
      try {
        const module = await window.Home3D.ready;
        if (!current()) { retire(owned); return; }
        const presentation = await module.loadDirectionalPresentation();
        if (!current()) { retire(owned); return; }
        const layer = await engine.createUiOverlayLayer({ host, signal: owned.controller.signal, isCurrent: current,
          onPresentationFrame: info => owned.view?.frame(info), onPresentationDispose: () => retire(owned) });
        if (!layer || !current()) { layer?.dispose(); retire(owned); return; }
        owned.layer = layer;
        const context = getDirectionalSnapshot(virtualFixtureId, { drawing: false });
        owned.view = presentation.createDirectionalPresentation({ group: owned.layer.group, host, api,
          initial: { descriptor: { ...context.descriptor, label: context.preview.fixtures.find(item => item.fixture_id === virtualFixtureId)?.label } },
          getProjection: () => directionalProjectRef.current(), getMode: () => fixtureInteractionModeRef.current,
          publishPlane: value => { if (directionalViewRef.current === owned.view) directionalProjectionRef.current = value; },
          isCurrent: current, onRetire: () => retire(owned),
          reducedMotion: () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true });
        if (!current()) { retire(owned); return; }
        directionalViewRef.current = owned.view;
        setDirectionalView(owned.view);
      } catch (_) { retire(owned); }
    };
    const onRunning = ({ running }) => { if (running) void start(); else retire(lease); };
    const unsubscribe = engine.on("running", onRunning);
    const onVisibility = () => { retire(lease); if (document.visibilityState !== "hidden") void start(); };
    document.addEventListener("visibilitychange", onVisibility);
    void start();
    return () => { closed = true; unsubscribe(); document.removeEventListener("visibilitychange", onVisibility); retire(lease); };
  }, [selectionEngine, getDirectionalSnapshot, virtualFixtureId, apartmentSceneControlsEnabled]);

  // APT-L02: restrained beams and the Down pool, drawn on their own UI overlay
  // layer from published descriptors while a Den light is selected, like the
  // directional composer. Equipment state and receivers are unchanged.
  const lightPoolsInputRef = useRef(null), lightPoolsDrawRef = useRef(null);
  lightPoolsInputRef.current = { selectedId: virtualFixtureControlsOpen ? virtualFixtureId : null,
    floor: virtualFixtureDocument?.references?.floor || null, beams: "selected", pools: true };
  useEffect(() => {
    const engine = selectionEngine, host = hostRef.current;
    if (!engine || !host || !virtualFixtureDocument || !apartmentSceneControlsEnabled || !virtualFixtureControlsOpen) return undefined;
    let closed = false, poolLease = null;
    const admitted = () => !closed && document.visibilityState !== "hidden" && engineRef.current === engine
      && engine.siteId === "victoria" && host.isConnected;
    const retire = owned => {
      if (!owned || owned.retired) return;
      owned.retired = true;
      try { owned.view?.dispose(); } finally { owned.controller.abort(); owned.layer?.dispose(); }
      if (poolLease === owned) poolLease = null;
      lightPoolsDrawRef.current = null;
    };
    // Frames redraw at most 10 Hz: after a React-side change (marked dirty),
    // or when the selected beam's published metadata (new objects on every
    // renderer update) changed. Radial painting alone pays no readback.
    const draw = (owned, nowMs = null) => {
      if (!owned.view || owned.retired) return;
      const overlay = engine.overlay;
      if (nowMs != null) {
        if (nowMs - (owned.drawnAt || 0) < 100) return;
        owned.drawnAt = nowMs;
        const id = lightPoolsInputRef.current?.selectedId;
        if (owned.beamId !== id || !owned.beamNode?.parent) {
          owned.beamId = id;
          owned.beamNode = id ? overlay.virtualFixturesGroup?.getObjectByName("equipment-virtual-fixture:" + id)?.getObjectByName("robotic-spotlight") : null;
        }
        const beam = owned.beamNode?.userData;
        const moved = !!beam && (beam.endpoint_m !== owned.lastEndpoint || beam.channel !== owned.lastChannel);
        if (!moved && !owned.dirty) return;
        if (beam) { owned.lastEndpoint = beam.endpoint_m; owned.lastChannel = beam.channel; }
      }
      owned.dirty = false;
      const preview = overlay.virtualFixturesReadback?.();
      const fixtures = preview?.status === "ready" && preview.visible ? preview.fixtures.map(item => {
        const spot = overlay.virtualFixturesGroup?.getObjectByName("equipment-virtual-fixture:" + item.fixture_id)
          ?.getObjectByName("robotic-spotlight")?.userData;
        return { fixture_id: item.fixture_id, label: item.label, state: item.state,
          anchor_m: overlay.virtualFixtureInteractionDescriptor?.(item.fixture_id)?.display_anchor_m,
          beam: spot ? { origin_m: spot.beam_origin_m, endpoint_m: spot.endpoint_m, reach_m: spot.reach_m } : null };
      }) : [];
      owned.view.update(owned.module.lightPoolsPlan({ fixtures, ...lightPoolsInputRef.current }));
    };
    const start = async () => {
      if (poolLease || !admitted()) return;
      const owned = { controller: new AbortController(), layer: null, view: null, module: null, retired: false };
      poolLease = owned;
      const current = () => poolLease === owned && !owned.retired && !owned.controller.signal.aborted && admitted();
      try {
        const home3d = await window.Home3D.ready;
        if (!current() || typeof home3d.loadLightPools !== "function") { retire(owned); return; }
        owned.module = await home3d.loadLightPools();
        if (!current()) { retire(owned); return; }
        const layer = await engine.createUiOverlayLayer({ host, signal: owned.controller.signal, isCurrent: current,
          onPresentationFrame: info => draw(owned, info.nowMs), onPresentationDispose: () => retire(owned) });
        if (!layer || !current()) { layer?.dispose(); retire(owned); return; }
        owned.layer = layer; owned.view = owned.module.createLightPools({ layer });
        lightPoolsDrawRef.current = () => { owned.dirty = true; }; draw(owned);
      } catch (_) { retire(owned); }
    };
    const unsubscribe = engine.on("running", ({ running }) => { if (running) void start(); else retire(poolLease); });
    const onVisibility = () => { retire(poolLease); if (document.visibilityState !== "hidden") void start(); };
    document.addEventListener("visibilitychange", onVisibility);
    void start();
    return () => { closed = true; unsubscribe(); document.removeEventListener("visibilitychange", onVisibility); retire(poolLease); };
  }, [selectionEngine, virtualFixtureBindingKey, apartmentSceneControlsEnabled, virtualFixtureControlsOpen]);
  useEffect(() => { lightPoolsDrawRef.current?.(); },
    [virtualFixturePreview, virtualFixtureId, virtualFixtureControlsOpen, virtualSpotlightsVisible]);

  const directionalDisplay = directionalView && !directionalView.readback().disposed && hostRef.current && window.AptDirectionalComposer
    && window.HomeApartmentDirectional?.identityKey(getDirectionalSnapshot(virtualFixtureId, { drawing: false })?.descriptor)
      === directionalView.readback().identity ? directionalView : null;

  // Component input owns radial painting; the existing renderer and direct-aim
  // algorithms are unchanged. Numeric readouts and modeled edits are immediate.
  useEffect(() => {
    const host = hostRef.current, api = window.HomeApartmentDirectional;
    if (!host || !api) return undefined;
    const consume = event => { event.preventDefault(); event.stopPropagation(); };
    const cancel = reason => {
      const owner = radialGestureOwnerRef.current; radialGestureOwnerRef.current = null;
      owner?.gesture.cancel(reason); directionalViewRef.current?.pointer(null, false);
      try { if (owner && host.hasPointerCapture?.(owner.pointerId)) host.releasePointerCapture(owner.pointerId); } catch (_) { /* detached viewport */ }
    };
    const current = owner => {
      const context = getDirectionalSnapshot(owner.fixtureId);
      const projection = directionalProjectionRef.current;
      if (!context || fixtureInteractionModeRef.current !== "zones" || api.identityKey(context.descriptor) !== owner.identity
          || !isDirectionalProjectionCurrent(projection) || api.identityKey(projection.descriptor) !== owner.identity) return null;
      owner.projection = projection;
      return context;
    };
    const point = (event, projection) => {
      const rect = host.getBoundingClientRect();
      if (!(rect.width > 0 && rect.height > 0)) return null;
      return projection.frame.unproject((event.clientX - rect.left) * host.clientWidth / rect.width,
        (event.clientY - rect.top) * host.clientHeight / rect.height);
    };
    const down = event => {
      if (radialGestureOwnerRef.current) {
        if (event.pointerId !== radialGestureOwnerRef.current.pointerId) { consume(event); cancel("multiple-pointers"); }
        return;
      }
      if (fixtureInteractionModeRef.current !== "zones" || event.button !== 0 || event.isPrimary === false || event.defaultPrevented
          || event.shiftKey || event.ctrlKey || event.metaKey || event.altKey
          || event.target?.closest?.('button, input, select, textarea, a, [data-aim-ignore]')) return;
      const context = getVirtualFixtureSelectionContext();
      if (!context || context.preview.visible !== true) return;
      const canvas = context.engine.renderer.domElement;
      const handle = event.target?.closest?.('[data-apt-radial-start]');
      if (event.target !== canvas && !handle) return;
      let picked;
      try { picked = event.target === canvas
        ? context.engine.picking.pick(context.engine.overlay.virtualFixturePickObjects(), event.clientX, event.clientY)[0]?.object.userData.fixture_id : virtualFixtureId; }
      catch (_) { return; }
      if (!picked && !handle) return; // Empty room space retains ordinary orbit.
      if (picked && (picked !== virtualFixtureId || !virtualFixtureControlsOpen)) {
        if (selectVirtualFixture(picked, { fromGesture: true })) consume(event);
        return; // A new selection needs its own admitted, drawn plane.
      }
      if (picked || handle) consume(event);
      // A retained selection can be behind Chat. Reuse the same guarded
      // selection/reveal path even when its ID and open state are unchanged.
      if (!selectVirtualFixture(virtualFixtureId, { fromGesture: true })) return;
      const snapshot = getDirectionalSnapshot(virtualFixtureId), projection = directionalProjectionRef.current;
      if (!snapshot || !isDirectionalProjectionCurrent(projection)) return;
      const local = point(event, projection);
      if (!local) return;
      const gesture = api.createRadialGesture({ snapshot: snapshot.descriptor, pointerId: event.pointerId, point: local,
        startable: !!handle || picked === virtualFixtureId });
      if (!gesture?.readback().active) return;
      consume(event);
      radialGestureOwnerRef.current = { gesture, projection, pointerId: event.pointerId, fixtureId: virtualFixtureId,
        identity: api.identityKey(snapshot.descriptor), lastClientX: event.clientX, lastClientY: event.clientY,
        lastPatch: null, cancel: () => cancel("escape") };
      const initial = gesture.readback(), rect = host.getBoundingClientRect();
      directionalViewRef.current?.pointer({ zone: initial.zone, level: initial.level,
        x: event.clientX - rect.left, y: event.clientY - rect.top }, true);
      try { host.setPointerCapture(event.pointerId); } catch (_) { cancel("capture-unavailable"); }
    };
    const move = event => {
      const owner = radialGestureOwnerRef.current;
      if (!owner || owner.pointerId !== event.pointerId) return;
      consume(event);
      const context = current(owner), local = point(event, owner.projection);
      if (!context || !local) { cancel("retired-or-unprojectable"); return; }
      // Camera settling can move the plane beneath a stationary pointer.
      // Only a new native client point supplies another paint sample.
      if (event.clientX === owner.lastClientX && event.clientY === owner.lastClientY) return;
      owner.lastClientX = event.clientX; owner.lastClientY = event.clientY;
      const result = owner.gesture.move({ snapshot: context.descriptor, pointerId: event.pointerId, point: local });
      if (!owner.gesture.readback().active) { cancel("retired"); return; }
      if (result?.patch?.radials?.length) {
        const key = JSON.stringify(result.patch);
        if (key !== owner.lastPatch) {
          if (!changeVirtualFixtureByHand(result.patch, owner.fixtureId, { deferPreview: true })) { cancel("write-refused"); return; }
          owner.lastPatch = key;
        }
      }
      if (result) directionalViewRef.current?.pointer({ zone: result.zone, level: result.level,
        x: event.clientX - host.getBoundingClientRect().left, y: event.clientY - host.getBoundingClientRect().top }, true);
    };
    const up = event => {
      const owner = radialGestureOwnerRef.current;
      if (!owner || owner.pointerId !== event.pointerId) return;
      move(event);
      if (radialGestureOwnerRef.current === owner) {
        owner.gesture.end({ snapshot: current(owner)?.descriptor, pointerId: event.pointerId }); cancel("ended");
      }
    };
    const canceled = event => { if (radialGestureOwnerRef.current?.pointerId === event.pointerId) { consume(event); cancel(event.type); } };
    const key = event => {
      const top = window.HomeOverlay?.topKey?.();
      if (!event.defaultPrevented && (!top || top === "apartment" || top === "apartment-embedded")
          && event.key === "Escape" && radialGestureOwnerRef.current) {
        consume(event); event.stopImmediatePropagation(); cancel("escape");
      }
    };
    const blur = () => cancel("blur");
    const wheel = () => cancel("wheel");
    const listeners = [["pointerdown", down], ["pointermove", move], ["pointerup", up], ["pointercancel", canceled], ["lostpointercapture", canceled], ["wheel", wheel]];
    for (const [name, listener] of listeners) host.addEventListener(name, listener, true);
    window.addEventListener("keydown", key, true); window.addEventListener("blur", blur);
    return () => {
      cancel("owner-disposed");
      for (const [name, listener] of listeners) host.removeEventListener(name, listener, true);
      window.removeEventListener("keydown", key, true); window.removeEventListener("blur", blur);
    };
  }, [getDirectionalSnapshot, isDirectionalProjectionCurrent, getVirtualFixtureSelectionContext, selectVirtualFixture,
    virtualFixtureId, virtualFixtureControlsOpen, fixtureInteractionMode]);

  const deskTargetContextCurrent = (capture) => {
    const engine = engineRef.current, document = virtualFixtureDocument;
    return !!capture && open && phase === "ready" && !editing && !calibCam && !liveOn
      && virtualSpotlightsVisible && spatialScale === "apartment" && !rehearsalIsolated
      && engine === capture.engine && workspaceAuthorityRef.current.key === capture.authorityKey
      && engine?.sceneIdentity?.siteId === "victoria"
      && engine.sceneIdentity.contentRevision === capture.binding.content_revision
      && engine.sceneIdentity.registrationVersion === capture.binding.registration_version
      && engine.sceneIdentity.frameDigest === capture.binding.frame_digest
      && document?.revision === capture.equipment_ref.revision && document?.digest === capture.equipment_ref.digest
      && JSON.stringify(document.binding) === JSON.stringify(capture.binding);
  };
  const deskTargetSourceCurrent = (capture) => {
    if (!capture?.surface_ref) return true;
    try {
      return JSON.stringify(capture.engine.deskTargetSurfaceReference(capture.binding))
        === JSON.stringify(capture.surface_ref);
    } catch (_) { return false; }
  };
  const showDeskTargetCorners = (capture, corners) => {
    if (!deskTargetContextCurrent(capture)) return;
    capture.engine.overlay.setTargets?.(corners.map((corner, index) => ({
      id: `desk-selection-${index + 1}`, name: `Desk corner ${index + 1}`, category: "desk",
      shape: "point", pos: corner.point_m, normal: corner.normal_unit,
    })));
  };
  const cancelDeskTargetSelection = () => {
    const capture = deskTargetCaptureRef.current;
    if (!capture) return;
    capture.controller?.dispose(); capture.active = false;
    if (deskTargetContextCurrent(capture)) capture.engine.overlay.setTargets?.(model.targets || []);
    deskTargetCaptureRef.current = null; setDeskTargetSelection(null);
  };
  const startDeskTargetSelection = async (kind = "desk") => {
    cancelDeskTargetSelection();
    const engine = engineRef.current, document = virtualFixtureDocument;
    if (!document || !engine?.overlay.createDeskTargetCapture) return;
    const capture = { engine, authorityKey: apartmentAuthorityKey,
      binding: JSON.parse(JSON.stringify(document.binding)),
      equipment_ref: { revision: document.revision, digest: document.digest },
      corners: [], active: true, kind: kind === "sofa" ? "sofa" : "desk" };
    if (!deskTargetContextCurrent(capture)) return;
    denDeskAimPreviewRef.current?.manualTakeover('desk_selection_started');
    virtualAimGestureRef.current?.cancel();
    deskTargetCaptureRef.current = capture;
    if (engine.prepareDeskTargetSurface) {
      setDeskTargetSelection({ status: "preparing", corners: [], confirmed: false });
      try {
        const reference = await engine.prepareDeskTargetSurface(capture.binding);
        if (deskTargetCaptureRef.current !== capture || !deskTargetContextCurrent(capture)) return;
        capture.surface_ref = JSON.parse(JSON.stringify(reference));
        if (!deskTargetSourceCurrent(capture)) throw Error("registered_desk_surface_source_changed");
      } catch (error) {
        if (deskTargetCaptureRef.current !== capture || !deskTargetContextCurrent(capture)) return;
        capture.active = false;
        setDeskTargetSelection({ status: "unavailable", reason: error.message,
          corners: [], confirmed: false });
        return;
      }
    }
    setDeskTargetSelection({ status: "selecting", corners: [], confirmed: false,
      surface_ref: capture.surface_ref || null });
    capture.controller = engine.overlay.createDeskTargetCapture({
      element: engine.renderer.domElement, windowTarget: window,
      getIdentity: () => [workspaceAuthorityRef.current.key, engine.sceneIdentity,
        virtualFixtureDocument.revision, virtualFixtureDocument.digest,
        capture.surface_ref ? engine.deskTargetSurfaceReference(capture.binding) : null],
      isEnabled: () => deskTargetCaptureRef.current === capture && deskTargetContextCurrent(capture),
      surfaceHit: (x, y) => {
        if (capture.surface_ref) {
          const hit = engine.deskTargetSurfaceHit(x, y, capture.binding);
          if (!hit) throw Error("registered_surface_no_hit");
          if (JSON.stringify(hit.surface_ref) !== JSON.stringify(capture.surface_ref))
            throw Error("registered_desk_surface_source_changed");
          return hit;
        }
        const hit = engine.displaySurfaceHit?.(x, y);
        if (hit) return hit;
        // Explain a refused pick without replacing it with unfiltered geometry.
        if (engine.modes?.getCollision && engine.picking?.pick) {
          const collision = engine.modes.getCollision();
          if (!collision) throw Error("registered_collision_unavailable");
          const raw = engine.picking.pick([collision], x, y);
          throw Error(raw.length ? "registered_surface_raw_hit_unselected" : "registered_surface_no_hit");
        }
        return null;
      },
      onChange: value => {
        if (deskTargetCaptureRef.current !== capture) return;
        capture.corners = value.corners;
        if (value.status === "retired") capture.active = false;
        setDeskTargetSelection({ ...value, confirmed: false, surface_ref: capture.surface_ref || null });
        showDeskTargetCorners(capture, value.corners);
      },
      onComplete: corners => {
        if (deskTargetCaptureRef.current !== capture || !deskTargetContextCurrent(capture)) return;
        capture.corners = corners; capture.active = false;
        setDeskTargetSelection({ status: "complete", corners, confirmed: false,
          surface_ref: capture.surface_ref || null });
        showDeskTargetCorners(capture, corners);
      },
    });
  };
  const saveDeskTargetSelection = () => {
    const capture = deskTargetCaptureRef.current;
    if (deskTargetSelection?.status !== "complete" || !deskTargetSelection.confirmed
        || !deskTargetContextCurrent(capture)) return;
    if (!deskTargetSourceCurrent(capture)) {
      cancelDeskTargetSelection();
      setVirtualAimNotice("The scan source changed. Select the desktop again.");
      return;
    }
    try {
      const input = {
        binding: capture.binding, equipment_ref: capture.equipment_ref,
        corners: capture.corners, confirmed: true,
        ...(capture.surface_ref ? { surface_ref: capture.surface_ref } : {}),
      };
      // The sofa mark reuses the desk sealing and is kept private beside it.
      const record = capture.kind === "sofa"
        ? window.HomeLightingSofaTarget.sealSofaTarget(input, { sealDesk: capture.engine.overlay.sealRegisteredDeskTarget,
          digest: window.HomeEquipmentRegistration.digest })
        : capture.engine.overlay.sealRegisteredDeskTarget(input, window.HomeEquipmentRegistration.digest);
      const blob = new Blob([JSON.stringify(record, null, 2) + "\n"], { type: "application/json" });
      const url = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = url; link.download = capture.kind === "sofa" ? "victoria-den-sofa-target.json" : "victoria-den-desk-target.json"; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setDeskTargetSelection(value => ({ ...value, status: "saved" }));
    } catch (error) {
      setVirtualAimNotice(capture.kind === "sofa" ? "Sofa mark unavailable. Select its four seat corners again."
        : "Desk target unavailable. Select its four actual scan corners again.");
    }
  };
  useEffect(() => () => {
    const capture = deskTargetCaptureRef.current;
    if (capture) {
      capture.controller?.dispose(); capture.active = false;
      // A retired selection must never restore old markers into a new scope.
      if (engineRef.current === capture.engine && workspaceAuthorityRef.current.key === capture.authorityKey
          && capture.engine.sceneIdentity?.frameDigest === capture.binding.frame_digest
          && capture.engine.sceneIdentity?.contentRevision === capture.binding.content_revision) {
        capture.engine.overlay.setTargets?.([]);
      }
      deskTargetCaptureRef.current = null;
    }
    setDeskTargetSelection(null);
  }, [open, phase, apartmentAuthorityKey, virtualFixtureBindingKey,
    virtualFixtureDocument?.revision, virtualFixtureDocument?.digest,
    editing, calibCam, liveOn, virtualSpotlightsVisible, spatialScale]);

  // A light's pending proposal, read through the store's own fence. The
  // store only remembers; every fixture change below stays in this host.
  const aimScopeFor = id => {
    const context = getVirtualFixtureSelectionContext();
    const descriptor = context && typeof id === "string" ? context.engine.overlay.virtualFixtureInteractionDescriptor?.(id) : null;
    const scope = descriptor && aimProposals ? aimProposals.scopeFromDescriptor("victoria", descriptor) : null;
    return scope ? { context, descriptor, scope, proposal: aimProposals.current(scope) } : null;
  };
  // Undo and redo are per light: each entry keeps one light's {current aim,
  // proposal} before and after one change, at most 30 steps per light
  // (SPEC › Aim lifecycle). The binding reset above still clears every light.
  const recordAimHistory = (id, before, after, coalesce = null) => {
    const history = virtualAimHistoryRef.current, now = Date.now();
    const last = history.undo.findLast(entry => entry.fixture_id === id);
    // One slider sweep is one step.
    if (coalesce && last?.coalesce === coalesce && last.binding === virtualFixtureBindingKey && now - last.at < 1000) {
      last.after = after; last.at = now;
    } else {
      history.undo.push({ fixture_id: id, binding: virtualFixtureBindingKey, before, after, coalesce, at: now });
      const mine = history.undo.filter(entry => entry.fixture_id === id);
      if (mine.length > APT_AIM_HISTORY_STEPS) history.undo.splice(history.undo.indexOf(mine[0]), 1);
    }
    history.redo = history.redo.filter(entry => entry.fixture_id !== id);
    setVirtualAimHistoryVersion(value => value + 1);
  };
  // History restores only where the light points. Its channels keep their
  // current values, and a snapshot from another preview generation refuses.
  const applyAimHistoryState = (id, state, reason) => {
    // Release first, so a desk preview that owns this light withdraws before
    // its aim is read or compared (Den gate, 2026-10-19).
    releaseFixtureForHandChange(id, { reason });
    const held = aimScopeFor(id), aim = state?.aim;
    if (!held || !aim || aim.fixture_id !== id || aim.generation !== held.descriptor.generation) return false;
    if (aim.state.pan_deg !== held.descriptor.state.pan_deg || aim.state.tilt_deg !== held.descriptor.state.tilt_deg
        || JSON.stringify(aim.target) !== JSON.stringify(held.descriptor.target)) {
      const result = held.context.engine.overlay.restoreVirtualFixtureAim?.(id, { ...aim,
        state: { ...held.descriptor.state, pan_deg: aim.state.pan_deg, tilt_deg: aim.state.tilt_deg } });
      if (!result?.ok) return false;
    }
    const live = aimScopeFor(id);
    if (!live) return false;
    if (live.proposal && live.proposal.id !== state.proposal?.id) aimProposals.take(live.scope, live.proposal.id);
    if (state.proposal && live.proposal?.id !== state.proposal.id && !aimProposals.restore(live.scope, state.proposal).ok)
      aimProposals.propose(live.scope, { origin: state.proposal.origin, target: state.proposal.target });
    return true;
  };
  const restoreVirtualAimHistory = (direction, fixtureId = selectedVirtualFixture?.fixture_id) => {
    const history = virtualAimHistoryRef.current, source = direction === "undo" ? history.undo : history.redo;
    const index = source.findLastIndex(entry => entry.fixture_id === fixtureId), entry = source[index], engine = engineRef.current;
    if (!entry || entry.binding !== virtualFixtureBindingKey || workspaceAuthorityRef.current.key !== apartmentAuthorityKey) return false;
    if (!applyAimHistoryState(entry.fixture_id, direction === "undo" ? entry.before : entry.after, direction)) {
      setVirtualAimNotice("This aim belongs to an earlier scene. Select a new room surface."); return false;
    }
    source.splice(index, 1); (direction === "undo" ? history.redo : history.undo).push(entry);
    engine.overlay.setVirtualFixtureSelected?.(entry.fixture_id); setVirtualFixtureId(entry.fixture_id);
    syncVirtualFixturePreview(engine); setVirtualAimHistoryVersion(value => value + 1);
    setVirtualAimNotice(direction === "undo" ? "Aim undone." : "Aim restored.");
    return true;
  };
  const aimHistoryLength = (direction, fixtureId = selectedVirtualFixture?.fixture_id) =>
    virtualAimHistoryRef.current[direction].filter(entry => entry.fixture_id === fixtureId).length;
  // Pan and Tilt stay as the accessible fine-tune (APT-A05, A08); their edits
  // enter the same per-light history.
  const patchVirtualFixtureFromInspector = (patch, fixtureId = selectedVirtualFixture?.fixture_id) => {
    const axes = ["pan_deg", "tilt_deg"].filter(key => Object.hasOwn(patch || {}, key));
    const held = axes.length ? aimScopeFor(fixtureId) : null;
    const changed = changeVirtualFixtureByHand(patch, fixtureId);
    const after = changed && held ? aimScopeFor(fixtureId) : null;
    if (after) recordAimHistory(fixtureId, { aim: held.descriptor, proposal: held.proposal },
      { aim: after.descriptor, proposal: after.proposal }, "slider:" + axes.join());
    return changed;
  };

  // A drop, tap or arrow key only proposes; the current aim does not move.
  const proposeAim = ({ fixture_id: id, origin = "manual", target } = {}) => {
    const held = aimScopeFor(id);
    const result = held ? aimProposals.propose(held.scope, { origin, target }) : null;
    if (!result?.ok) return null;
    recordAimHistory(id, { aim: held.descriptor, proposal: held.proposal }, { aim: held.descriptor, proposal: result.proposal });
    setVirtualAimNotice(result.proposal.target.valid ? "Proposed aim. Set aim moves the light; Discard keeps the current aim."
      : "This spot can’t be aimed at. Discard it or drag again.");
    return result.proposal;
  };
  // Set goes through the existing fenced write, at the preview generation
  // the proposal was made in. A moved scope refuses and nothing changes.
  // `sweep` asks for Repair's 650 ms Set sweep where the producer has it;
  // undo and redo restore without one.
  const setAimProposal = ({ fixture_id: id, proposal } = {}) => {
    // Release first: the undo entry's "before" must be the person's aim, not
    // a desk preview's temporary one (Den gate, 2026-10-19).
    releaseFixtureForHandChange(id, { reason: "aim" });
    const held = aimScopeFor(id), target = proposal?.target;
    if (!held?.proposal || held.proposal.id !== proposal?.id || target?.valid !== true || !Array.isArray(target.normal_m)) {
      setVirtualAimNotice("That proposal belongs to an earlier scene. Drag the aim handle again."); return false;
    }
    const before = { aim: held.descriptor, proposal: held.proposal };
    if (!aimProposals.take(held.scope, proposal.id).ok) return false;
    const result = held.context.engine.overlay.aimVirtualFixtureAtTarget(id, { point: target.point_m.slice(),
      normal: target.normal_m.slice(), kind: "registered_mesh" }, { expectedGeneration: held.descriptor.generation, sweep: true });
    if (!result?.ok) {
      aimProposals.propose(held.scope, { origin: proposal.origin, target });
      setVirtualAimNotice("That aim is unavailable. Choose another room surface."); return false;
    }
    recordAimHistory(id, before, { aim: result.descriptor, proposal: null });
    syncVirtualFixturePreview(held.context.engine);
    setVirtualAimNotice("Aim set in the modeled preview. No hardware command sent.");
    showToast("Aim set · modeled preview, no hardware command sent");
    return true;
  };
  const discardAimProposal = ({ fixture_id: id, proposal } = {}) => {
    const held = aimScopeFor(id);
    if (!held?.proposal || held.proposal.id !== proposal?.id || !aimProposals.discard(held.scope, proposal.id).ok) return false;
    recordAimHistory(id, { aim: held.descriptor, proposal: held.proposal }, { aim: held.descriptor, proposal: null });
    setVirtualAimNotice(`Proposal discarded. ${aptUndoShortcut()} brings it back.`);
    return true;
  };
  // Aiming from the room brings Aim forward without moving the gesture,
  // keeps Chat open and raises a phone sheet to half (APT-A02).
  const startAimFromRoom = ({ fixture_id: id } = {}) => {
    if (id !== virtualFixtureId || !virtualFixtureControlsOpen) return;
    setVirtualInspectorTab("aim");
    if (unifiedShell) (onShowShellAim || onShowShellControls)?.();
  };

  // Victoria presentation gestures are separate from Echo's physical-device
  // capability. Selection never grants device, camera or command authority.
  useEffect(() => {
    const engine = engineRef.current, overlay = engine?.overlay;
    if (!open || phase !== "ready" || !virtualFixtureDocument || editing || calibCam || liveOn
        || !virtualSpotlightsVisible || spatialScale !== "apartment" || rehearsalIsolated
        || !overlay?.createVirtualFixtureAimGesture) return undefined;
    const binding = virtualFixtureDocument.binding, capturedAuthority = apartmentAuthorityKey;
    const current = () => engineRef.current === engine && engine.siteId === "victoria"
      && workspaceAuthorityRef.current.key === capturedAuthority
      && engine.sceneIdentity?.contentRevision === binding.content_revision
      && engine.sceneIdentity?.registrationVersion === binding.registration_version
      && engine.sceneIdentity?.frameDigest === binding.frame_digest;
    const gesture = overlay.createVirtualFixtureAimGesture({
      element: engine.renderer.domElement, windowTarget: window,
      isEnabled: () => current() && !deskTargetCaptureRef.current?.active,
      getIdentity: () => [workspaceAuthorityRef.current.key, engine.sceneIdentity, virtualFixtureDocument.revision, virtualFixtureDocument.digest],
      pickFixture: (x, y) => engine.picking.pick(overlay.virtualFixturePickObjects(), x, y)[0]?.object.userData.fixture_id || null,
      surfaceHit: (x, y) => {
        const hit = engine.displaySurfaceHit?.(x, y);
        return hit ? { point: hit.point, normal: hit.normal, kind: "registered_mesh" } : null;
      },
      captureSnapshot: id => overlay.virtualFixtureInteractionDescriptor(id),
      // Only the reticle starts an aim (APT-A01, R01). A press on a fixture
      // body still selects it and keeps the pointer from orbiting, but this
      // controller may never preview, so it has nothing to restore or commit.
      previewTarget: () => ({ ok: false, reason: "reticle_only" }),
      restoreSnapshot: () => ({ ok: true }),
      onSelect: id => {
        if (!selectVirtualFixture(id, { fromGesture: true })) return;
        setVirtualFixtureChannel("spotlight");
      },
      onPreview: info => {
        if (!current() || info.status === "canceled" || info.status === "stale_scene") return;
        setVirtualAimNotice("Drag the round aim handle to aim this light.");
      },
    });
    virtualAimGestureRef.current = gesture;
    return () => {
      gesture.dispose();
      if (virtualAimGestureRef.current === gesture) virtualAimGestureRef.current = null;
      overlay.setVirtualFixtureSelected(null);
    };
  }, [open, phase, virtualFixtureBindingKey, virtualFixtureDocument, apartmentAuthorityKey,
    editing, calibCam, liveOn, virtualSpotlightsVisible, spatialScale, rehearsalIsolated, unifiedShell,
    onShowShellControls, selectVirtualFixture]);

  // The in-room aim overlay borrows the resident engine's admitted frame, as
  // the directional composer does. It needs no selection: labels show for
  // every light.
  const aimOverlayWanted = open && phase === "ready" && !!virtualFixtureDocument && virtualSpotlightsVisible
    && apartmentSceneControlsEnabled && !editing && !calibCam && !liveOn && spatialScale === "apartment" && !rehearsalIsolated;
  useEffect(() => {
    const engine = selectionEngine, host = hostRef.current, binding = virtualFixtureDocument?.binding;
    if (!aimOverlayWanted || !engine || !host || !binding || typeof engine.createUiOverlayLayer !== "function") return undefined;
    const capturedAuthority = apartmentAuthorityKey;
    let lease = null, closed = false;
    const admitted = () => !closed && document.visibilityState !== "hidden" && host.isConnected
      && engineRef.current === engine && engine.siteId === "victoria" && viewOpenRef.current
      && apartmentSceneControlsEnabledRef.current && workspaceAuthorityRef.current.key === capturedAuthority
      && engine.sceneIdentity?.contentRevision === binding.content_revision
      && engine.sceneIdentity?.registrationVersion === binding.registration_version
      && engine.sceneIdentity?.frameDigest === binding.frame_digest;
    const retire = owned => {
      if (!owned || owned.retired) return;
      owned.retired = true;
      if (aimPresentationRef.current === owned.presentation) aimPresentationRef.current = null;
      try { owned.presentation?.dispose(); } finally {
        owned.controller.abort(); owned.layer?.dispose();
        setAimPresentation(previous => previous === owned.presentation ? null : previous);
      }
      if (lease === owned) lease = null;
    };
    const start = async () => {
      if (lease || !admitted()) return;
      const owned = { controller: new AbortController(), layer: null, presentation: null, retired: false };
      lease = owned;
      const current = () => lease === owned && !owned.retired && !owned.controller.signal.aborted && admitted();
      try {
        const module = await window.Home3D.ready;
        if (!current() || typeof module.loadAimPresentation !== "function") { retire(owned); return; }
        const api = await module.loadAimPresentation();
        if (!current()) { retire(owned); return; }
        const layer = await engine.createUiOverlayLayer({ host, signal: owned.controller.signal, isCurrent: current,
          onPresentationFrame: () => owned.presentation?.frame(), onPresentationDispose: () => retire(owned) });
        if (!layer || !current()) { layer?.dispose(); retire(owned); return; }
        owned.layer = layer;
        owned.presentation = api.createAimPresentation({ layer, host, isCurrent: current,
          getInsets: aptShellInsetsReader(host), onRetire: () => retire(owned),
          // Repair's canonical beam trace, once the engine has one; until then nothing is called blocked.
          traceBeam: typeof engine.traceBeam === "function" ? (origin, point) => engine.traceBeam(origin, point) : undefined });
        if (!current()) { retire(owned); return; }
        aimPresentationRef.current = owned.presentation;
        setAimPresentation(owned.presentation);
      } catch (_) { retire(owned); }
    };
    const onRunning = ({ running }) => { if (running) void start(); else retire(lease); };
    const unsubscribe = engine.on("running", onRunning);
    const onVisibility = () => { retire(lease); if (document.visibilityState !== "hidden") void start(); };
    document.addEventListener("visibilitychange", onVisibility);
    // A resize can briefly leave the viewport unmeasurable, which retires the
    // layer. Labels and the reticle come back once it is admitted again.
    const revive = setInterval(() => { if (!lease) void start(); }, 300);
    void start();
    return () => {
      closed = true; clearInterval(revive); unsubscribe(); document.removeEventListener("visibilitychange", onVisibility); retire(lease);
    };
  }, [selectionEngine, aimOverlayWanted, virtualFixtureDocument, apartmentAuthorityKey]);

  const aimOverlayFixtures = useMemo(() => {
    const engine = engineRef.current;
    if (!aimPresentation || !engine) return [];
    return (virtualFixturePreview?.fixtures || []).map(item => {
      const descriptor = engine.overlay.virtualFixtureInteractionDescriptor?.(item.fixture_id);
      return descriptor && { fixture_id: item.fixture_id, label: item.label || item.fixture_id,
        on: aptVirtualFixtureLit(item), display_anchor_m: descriptor.display_anchor_m };
    }).filter(Boolean);
  }, [aimPresentation, virtualFixturePreview]);
  // One label per fixture: while the HTML labels are mounted, the canvas
  // equipment tags of the mounts they name stay hidden.
  const aimOverlayMountKey = useMemo(() => aimOverlayFixtures.map(item => (virtualFixturePreview?.fixtures || [])
    .find(fixture => fixture.fixture_id === item.fixture_id)?.mount_id).filter(id => typeof id === "string").join("|"),
  [aimOverlayFixtures, virtualFixturePreview]);
  useEffect(() => {
    const overlay = engineRef.current?.overlay;
    if (typeof overlay?.setRoomLabelledEquipment !== "function") return undefined;
    overlay.setRoomLabelledEquipment(aimOverlayMountKey ? aimOverlayMountKey.split("|") : []);
    return () => { try { overlay.setRoomLabelledEquipment([]); } catch (_) { /* engine disposed */ } };
  }, [aimPresentation, aimOverlayMountKey]);
  const aimSelectedDescriptor = useMemo(() => aimPresentation && virtualFixtureControlsOpen && virtualFixtureId
    ? engineRef.current?.overlay.virtualFixtureInteractionDescriptor?.(virtualFixtureId) || null : null,
  [aimPresentation, virtualFixtureControlsOpen, virtualFixtureId, virtualFixturePreview]);
  const aimSelectedScope = aimSelectedDescriptor && aimProposals ? aimProposals.scopeFromDescriptor("victoria", aimSelectedDescriptor) : null;
  const aimSelectedProposal = aimSelectedScope ? aimProposals.current(aimSelectedScope) : null;
  // One stable overlay element: the host re-renders often, and each overlay
  // render re-syncs and re-places every label. Callbacks reach the current
  // handlers through aimActionsRef, so they never need to change.
  const aimOverlayCallbacks = useMemo(() => ({
    onSelect: id => aimActionsRef.current.select(id), onHover: id => setHoverFixtureId(id),
    onAimStart: info => aimActionsRef.current.start(info), onPropose: request => aimActionsRef.current.propose(request),
    onSet: request => aimActionsRef.current.setProposal(request), onDiscard: request => aimActionsRef.current.discard(request),
  }), []);
  const aimOverlaySelectedId = virtualFixtureControlsOpen ? virtualFixtureId : null;
  const aimOverlayPillId = directionalDisplay ? virtualFixtureId : null;
  const aimOverlayLimits = selectedVirtualFixture?.limits || null;
  const aimOverlayAim = useMemo(() => aimSelectedDescriptor && fixtureInteractionMode === "spot"
    ? { descriptor: aimSelectedDescriptor, limits: aimOverlayLimits, namedAims: [] } : null,
  [aimSelectedDescriptor, fixtureInteractionMode, aimOverlayLimits]);
  const aimOverlayElement = useMemo(() => aimPresentation && window.AptRoomOverlay
    ? <window.AptRoomOverlay presentation={aimPresentation} store={aimProposals} siteId="victoria" fixtures={aimOverlayFixtures}
      selectedId={aimOverlaySelectedId} hoverId={hoverFixtureId} pillFixtureId={aimOverlayPillId} aim={aimOverlayAim} {...aimOverlayCallbacks} />
    : null,
  [aimPresentation, aimProposals, aimOverlayFixtures, aimOverlaySelectedId, hoverFixtureId, aimOverlayPillId, aimOverlayAim, aimOverlayCallbacks]);

  // Proposals clear on an authority change and retire with the scope their
  // fence names. They are never persisted.
  useEffect(() => { aimProposals?.clearAll(); }, [aimProposals, apartmentAuthorityKey]);
  useEffect(() => {
    const engine = engineRef.current;
    if (!aimProposals || !engine || !virtualFixturePreview?.fixtures) return;
    aimProposals.retainScopes("victoria", virtualFixturePreview.fixtures.map(item =>
      aimProposals.scopeFromDescriptor("victoria", engine.overlay.virtualFixtureInteractionDescriptor?.(item.fixture_id))).filter(Boolean));
  }, [aimProposals, virtualFixturePreview]);

  // Canvas hover shows a light's name and a soft ring. It never changes the inspector.
  useEffect(() => {
    const engine = selectionEngine, host = hostRef.current, canvas = engine?.renderer?.domElement;
    if (!aimPresentation || !host || !canvas) { setHoverFixtureId(null); return undefined; }
    let frame = null, last = null;
    const pick = () => {
      frame = null;
      if (!last) return;
      let id = null;
      try { id = engine.picking.pick(engine.overlay.virtualFixturePickObjects(), last.x, last.y)[0]?.object.userData.fixture_id || null; }
      catch (_) { id = null; }
      setHoverFixtureId(previous => previous === id ? previous : id);
    };
    const move = event => {
      if (event.buttons || event.target !== canvas || event.pointerType === "touch") return;
      last = { x: event.clientX, y: event.clientY };
      if (frame == null) frame = requestAnimationFrame(pick);
    };
    const leave = () => { last = null; setHoverFixtureId(null); };
    host.addEventListener("pointermove", move, { passive: true }); canvas.addEventListener("pointerleave", leave);
    return () => {
      if (frame != null) cancelAnimationFrame(frame);
      host.removeEventListener("pointermove", move); canvas.removeEventListener("pointerleave", leave);
      setHoverFixtureId(null);
    };
  }, [aimPresentation, selectionEngine]);

  // Tap to aim (APT-A08): one tap on a surface proposes, as a drop would.
  // While it is on, the tap belongs to it, so it neither orbits nor deselects.
  useEffect(() => {
    if (tapAim && (tapAim !== virtualFixtureId || !virtualFixtureControlsOpen || fixtureInteractionMode !== "spot" || !aimPresentation))
      setTapAim(null);
  }, [tapAim, virtualFixtureId, virtualFixtureControlsOpen, fixtureInteractionMode, aimPresentation]);
  useEffect(() => {
    const presentation = aimPresentation, canvas = selectionEngine?.renderer?.domElement;
    if (!tapAim || !presentation || !canvas) return undefined;
    let press = null;
    const consume = event => { event.preventDefault(); event.stopImmediatePropagation(); };
    const down = event => {
      if (event.target !== canvas) return;
      consume(event);
      press = event.button === 0 && event.isPrimary !== false ? { id: event.pointerId, x: event.clientX, y: event.clientY } : null;
    };
    const up = event => {
      if (event.target !== canvas && press?.id !== event.pointerId) return;
      consume(event);
      const tap = press; press = null;
      if (!tap || tap.id !== event.pointerId || Math.hypot(event.clientX - tap.x, event.clientY - tap.y) >= 8) return;
      const model = presentation.describeAt(event.clientX, event.clientY), target = model && presentation.proposalTarget(model);
      if (!target) { setVirtualAimNotice("No surface here. Tap a wall, furniture or floor."); return; }
      if (aimActionsRef.current.propose({ fixture_id: tapAim, target })) {
        setTapAim(null); aimActionsRef.current.start({ fixture_id: tapAim, source: "tap" });
      }
    };
    const click = event => { if (event.target === canvas) consume(event); };
    const cancel = () => { press = null; };
    const previousCursor = canvas.style.cursor;
    canvas.style.cursor = "crosshair";
    const listeners = [["pointerdown", down], ["pointerup", up], ["click", click], ["pointercancel", cancel]];
    for (const [name, listener] of listeners) window.addEventListener(name, listener, true);
    return () => {
      canvas.style.cursor = previousCursor;
      for (const [name, listener] of listeners) window.removeEventListener(name, listener, true);
    };
  }, [tapAim, aimPresentation, selectionEngine]);

  // Aim keys beyond the overlay's own reticle and bar. Enter sets a valid
  // proposal when focus is on no control; Cmd/Ctrl+Z and Shift+Cmd/Ctrl+Z
  // step the selected light's aim history. Text fields, Setup's editor and
  // any overlay above the Apartment keep their own keys.
  aimActionsRef.current = { propose: proposeAim, start: startAimFromRoom, select: id => selectVirtualFixture(id),
    setProposal: setAimProposal, discard: discardAimProposal,
    enabled: () => !!(open && selectedSiteId === "victoria" && virtualFixtureControlsOpen && apartmentSceneControlsEnabled
      && !editing && !radialGestureOwnerRef.current && !virtualAimGestureRef.current?.readback().active
      && !aimPresentationRef.current?.readback().dragging),
    history: direction => restoreVirtualAimHistory(direction),
    set: () => {
      const held = aimScopeFor(virtualFixtureId);
      return held?.proposal?.target.valid === true && setAimProposal({ fixture_id: virtualFixtureId, proposal: held.proposal });
    } };
  useEffect(() => {
    if (!open) return undefined;
    const onAimKey = event => {
      if (event.defaultPrevented || event.altKey || event.isComposing) return;
      const top = window.HomeOverlay?.topKey?.(), target = event.target;
      if (top && top !== "apartment" && top !== "apartment-embedded") return;
      if (target?.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target?.tagName || "")) return;
      const actions = aimActionsRef.current;
      if (!actions?.enabled()) return;
      if ((event.metaKey || event.ctrlKey) && (event.key === "z" || event.key === "Z")) {
        if (actions.history(event.shiftKey ? "redo" : "undo")) event.preventDefault();
        return;
      }
      if (event.key === "Enter" && !event.metaKey && !event.ctrlKey && !event.shiftKey
          && !target?.closest?.('button, a[href], summary, [role="button"], [role="tab"], [role="switch"], [role="slider"], [role="option"]')
          && actions.set()) event.preventDefault();
    };
    window.addEventListener("keydown", onAimKey);
    return () => window.removeEventListener("keydown", onAimKey);
  }, [open]);

  /*
   * The engine owns the ESM/classic-script boundary. Rebuild the small frozen
   * semantic projection whenever its model, current selection, or HA state can
   * change; a generation guard prevents a slower dynamic import from restoring
   * stale labels after a newer selection. Sites and coordinates never cross
   * this bridge.
   */
  useEffect(() => {
    let cancelled = false;
    const engine = engineRef.current;
    if (!open || phase !== "ready" || !engine?.apartmentSemanticNavigation || engine.siteId !== "echo") {
      setApartmentSemanticNavigation(EMPTY_APARTMENT_SEMANTIC_NAVIGATION);
      setSemanticStatusDeviceId(null);
      if (!open) {
        setSemanticRoomId("");
        setSemanticDeviceId(null);
        setSemanticRoomStatus("");
      }
      return undefined;
    }

    const requestedDeviceId = cardId || semanticDeviceId || null;
    Promise.resolve(engine.apartmentSemanticNavigation(
      model,
      statesRef.current,
      requestedDeviceId,
    )).then((next) => {
      if (cancelled) return;
      const resolved = next || EMPTY_APARTMENT_SEMANTIC_NAVIGATION;
      setApartmentSemanticNavigation(resolved);
      setSemanticStatusDeviceId(requestedDeviceId);
      if (semanticRoomId && !(resolved.rooms || []).some((room) => room.id === semanticRoomId)) {
        setSemanticRoomId("");
        setSemanticRoomStatus("");
      }
      if (semanticDeviceId && (
        !(resolved.devices || []).some((device) => device.id === semanticDeviceId)
        || (semanticRoomId && semanticDeviceRoomById.get(semanticDeviceId) !== semanticRoomId)
      )) {
        setSemanticDeviceId(null);
      }
    }).catch((error) => {
      if (cancelled) return;
      console.warn("[apartment] semantic navigation unavailable:", error?.message || error);
      setApartmentSemanticNavigation(EMPTY_APARTMENT_SEMANTIC_NAVIGATION);
      setSemanticStatusDeviceId(null);
    });
    return () => { cancelled = true; };
  }, [open, phase, model, servicePulse, cardId, semanticDeviceId, semanticRoomId,
    semanticDeviceRoomById]);

  useEffect(() => {
    if (!cardId) return;
    setSemanticDeviceId(cardId);
    setSemanticRoomStatus("");
  }, [cardId]);

  const selectSemanticRoom = useCallback((roomId) => {
    const rooms = apartmentSemanticNavigation.rooms || [];
    const index = rooms.findIndex((room) => room.id === roomId);
    if (index < 0) {
      setSemanticRoomId("");
      setSemanticRoomStatus(
        `All ${rooms.length} apartment room${rooms.length === 1 ? "" : "s"}. `
        + `${(apartmentSemanticNavigation.devices || []).length} devices available.`,
      );
      return;
    }
    const room = rooms[index];
    const roomDeviceCount = (apartmentSemanticNavigation.devices || [])
      .filter((device) => semanticDeviceRoomById.get(device.id) === room.id).length;
    setSemanticRoomId(room.id);
    if (semanticDeviceId && semanticDeviceRoomById.get(semanticDeviceId) !== room.id) {
      setSemanticDeviceId(null);
    }
    setSemanticRoomStatus(
      `Room ${index + 1} of ${rooms.length}. ${room.ariaLabel}. `
      + `${roomDeviceCount} device${roomDeviceCount === 1 ? "" : "s"}.`,
    );
  }, [apartmentSemanticNavigation.rooms, apartmentSemanticNavigation.devices,
    semanticDeviceRoomById, semanticDeviceId]);

  const selectSemanticDevice = useCallback((deviceId) => {
    const device = semanticDevicesForRoom.find((candidate) => candidate.id === deviceId);
    setSemanticDeviceId(device?.id || null);
    setSemanticRoomStatus("");
  }, [semanticDevicesForRoom]);

  const stepSemanticDevice = useCallback((direction, { openCard = false } = {}) => {
    const devices = semanticDevicesForRoom;
    if (!devices.length) return null;
    const step = direction < 0 ? -1 : 1;
    const currentId = cardId || semanticDeviceId;
    const index = devices.findIndex((device) => device.id === currentId);
    const nextIndex = index < 0
      ? (step < 0 ? devices.length - 1 : 0)
      : (index + step + devices.length) % devices.length;
    const nextId = devices[nextIndex].id;
    setSemanticDeviceId(nextId);
    setSemanticRoomStatus("");
    if (openCard) setCardId(nextId);
    return nextId;
  }, [semanticDevicesForRoom, cardId, semanticDeviceId]);

  const openSemanticDevice = useCallback(() => {
    const device = semanticDevicesForRoom
      .find((candidate) => candidate.id === semanticDeviceId);
    if (!device) return false;
    setCardId(device.id);
    return true;
  }, [semanticDevicesForRoom, semanticDeviceId]);

  /* Quiet simulation beams are part of the spatial overview, not a selection
     artifact. Aim mode replaces these with the emphasized current/preview
     solve, then this layer returns when the inspector closes. */
  useEffect(() => {
    const engine = engineRef.current;
    if (!engine?.overlay?.setAimBeams || phase !== "ready" || aimMode) return;
    if (rehearsalIsolated) return;
    if (!simActive || editing || selectedSiteId !== "echo" || engine.siteId !== "echo") { engine.overlay.setAimBeams([]); return; }
    const A = window.HomeApartmentAiming;
    const runtimeByFixture = window.__SIM_APARTMENT_AIM_RUNTIME?.readAll?.() || {};
    const targetById = new Map((model.targets || []).map((target) => [target.id, target]));
    const specs = (model.devices || []).filter((device) => device.fixture_kind === A?.ENGINEERED_KIND).map((fixture) => {
      const runtime = runtimeByFixture[fixture.id];
      const runtimeDestination = runtime?.current_destination;
      const target = runtimeDestination?.pos ? runtimeDestination : targetById.get(fixture.gimbal?.default_target_id);
      let direction = [0, 0, -1], distance = Math.max(0.35, +fixture.pos?.[2] || 2.3), currentMode = null;
      if (runtimeDestination?.kind === "mirror") {
        direction = [0, 0, 1]; distance = 0.2; currentMode = "mirror_bounce";
      } else if (target?.pos) {
        const delta = target.pos.map((value, index) => value - fixture.pos[index]);
        distance = Math.hypot(...delta); direction = delta.map((value) => value / distance);
      }
      const footprintCandidate = !currentMode && target?.normal
        ? A.projectBeamToPlane({ origin: fixture.pos, direction, plane_point: target.pos,
          plane_normal: target.normal, full_fwhm_deg: fixture.spotlight?.optic_profile?.configured_fwhm_deg || 20 })
        : null;
      const footprint = footprintCandidate?.kind === "ellipse" ? footprintCandidate : null;
      const calibration = fixture.radial_zones?.orientation_calibration;
      const radial = calibration ? fixture.radial_zones.zones.map((zone) => ({
        number: zone.number, angle_deg: A.radialZoneAngle(zone.number, calibration),
        active: runtime?.zones?.[zone.number]?.state === "on",
        color_temp_kelvin: runtime?.zones?.[zone.number]?.color_temp_kelvin,
      })) : [];
      return { fixture_id: fixture.id, origin: fixture.pos,
        color_temp_kelvin: runtime?.spotlight?.color_temp_kelvin,
        current: { origin: fixture.pos, direction, distance_m: distance,
          full_fwhm_deg: fixture.spotlight?.optic_profile?.configured_fwhm_deg || 20,
          state: runtime?.spotlight?.state || "unknown",
          color_temp_kelvin: runtime?.spotlight?.color_temp_kelvin, mode: currentMode,
          footprint: footprint?.points?.length ? footprint : null,
          surface_aligned: footprint?.kind === "ellipse" }, radial };
    });
    engine.overlay.setAimBeams(specs, "__none__");
  }, [open, phase, model, simActive, editing, aimMode, servicePulse, rehearsalIsolated, selectedSiteId, selectedContentRevision]);

  /* HA entity state binding */
  useEffect(() => {
    if (!open || phase !== "ready" || simActive || rehearsalIsolated || selectedSiteId !== "echo" || connection !== "online") return undefined;
    const client = window.__hav_haClient;
    if (!client) return undefined;
    const engine = engineRef.current;
    const authority = workspaceAuthorityRef.current.key;
    const connectionGeneration = controlConnectionRef.current.generation;
    const current = () => engineRef.current === engine && engine?.siteId === "echo"
      && window.__hav_haClient === client && controlConnectionRef.current.connection === "online"
      && controlConnectionRef.current.generation === connectionGeneration
      && workspaceAuthorityRef.current.key === authority
      && window.HomeSpatialViewport?.getSelectedSiteId?.() === "echo"
      && (window.HomeSpatialViewport?.getSite?.("echo")?.contentRevision || "legacy") === (engine.contentRevision || "legacy");
    return window.HomeApartmentData.bindStates(client, model, (entityId, st) => {
      if (!current()) return;
      statesRef.current[entityId] = st;
      const dev = (model.devices || []).find((d) => d.ha_entity_id === entityId);
      if (dev) engine.overlay.setDeviceState(dev.id, st);
      setServicePulse((n) => n + 1);
    }, { siteId: selectedSiteId, isCurrent: current });
  }, [open, phase, model, simActive, rehearsalIsolated, selectedSiteId, selectedContentRevision, endpoint, token, connection]);

  /* tracker WS -> person dot / room pulse */
  useEffect(() => {
    if (!open || phase !== "ready" || rehearsalIsolated) return undefined;
    if (selectedSiteId !== "echo") { setTrack(null); return undefined; }
    const engine = engineRef.current;
    const authority = workspaceAuthorityRef.current.key;
    const current = () => engineRef.current === engine && engine?.siteId === "echo"
      && workspaceAuthorityRef.current.key === authority
      && window.HomeSpatialViewport?.getSelectedSiteId?.() === "echo"
      && (window.HomeSpatialViewport?.getSite?.("echo")?.contentRevision || "legacy") === (engine.contentRevision || "legacy");
    return window.HomeApartmentData.openTracks({
      siteId: selectedSiteId, isCurrent: current,
      sim: simActive,
      onStatus: (status) => { if (current()) setTrackerStatus(status); },
      onTracks: (frame) => {
        if (!current()) return;
        const t = (frame.tracks || [])[0] || null;
        setTrack(t);
        engine.overlay.setPerson(t);
      },
    });
  }, [open, phase, simActive, serviceEpoch, selectedSiteId, selectedContentRevision, rehearsalIsolated, endpoint, token]);

  /* hover + click picking (view and side-effect-free Aim mode) */
  useEffect(() => {
    if (!open || phase !== "ready" || editing || rehearsalIsolated || !apartmentSceneControlsEnabled) return undefined;
    if (!apartmentDeviceControlsEnabled) return undefined;
    const engine = engineRef.current;
    const host = hostRef.current;
    if (!engine || !host) return undefined;
    let raf = 0;
    let gesture = null;
    const onDown = (e) => { gesture = { x: e.clientX, y: e.clientY, moved: false }; };
    const onMove = (e) => {
      if (gesture && Math.hypot(e.clientX - gesture.x, e.clientY - gesture.y) > 8) gesture.moved = true;
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const hits = engine.picking.pick(engine.overlay.pickObjects(), e.clientX, e.clientY);
        const id = hits.length ? hits[0].object.userData.deviceId : null;
        setHoverId(id);
        engine.overlay.setHover(id);
        const targetHits = aimMode ? engine.picking.pick(engine.overlay.targetPickObjects?.() || [], e.clientX, e.clientY) : [];
        const targetId = targetHits.length ? targetHits[0].object.userData.targetId : null;
        engine.overlay.setTargetHover?.(targetId, aimDestination?.kind === "named" ? aimDestination.id : null);
        host.style.cursor = id || targetId || aimMode ? "crosshair" : "grab";
      });
    };
    const onClick = (e) => {
      if (gesture?.moved) { gesture = null; return; }
      gesture = null;
      const hits = engine.picking.pick(engine.overlay.pickObjects(), e.clientX, e.clientY);
      const deviceId = hits.length ? hits[0].object.userData.deviceId : null;
      if (aimMode) {
        const radialHits = engine.picking.pick(engine.overlay.aimRadialPickObjects?.() || [], e.clientX, e.clientY);
        if (radialHits.length) {
          const { fixtureId, zoneNumber } = radialHits[0].object.userData || {};
          const radialFixture = (model.devices || []).find((device) => device.id === fixtureId);
          const radialZone = radialFixture?.radial_zones?.zones?.find((zone) => +zone.number === +zoneNumber);
          setAimFixtureId(fixtureId);
          if (simActive) {
            const next = window.__SIM_APARTMENT_AIM_RUNTIME?.toggleZone?.(fixtureId, +zoneNumber);
            setServicePulse((value) => value + 1);
            showToast(`radial ${zoneNumber} · ${next?.zones?.[zoneNumber]?.state || "updated"} · simulation`);
          } else if (radialZone?.entity_id) {
            void callSvc("light", "toggle", { entity_id: radialZone.entity_id });
          } else {
            showToast(`radial ${zoneNumber} is not mapped to Home Assistant`);
          }
          return;
        }
        if (deviceId) {
          setAimFixtureId(deviceId);
          setCardId(null);
          return;
        }
        const targetHits = engine.picking.pick(engine.overlay.targetPickObjects?.() || [], e.clientX, e.clientY);
        const targetId = targetHits.length ? targetHits[0].object.userData.targetId : null;
        const target = (model.targets || []).find((item) => item.id === targetId);
        if (target) {
          setAimDestination({ kind: "named", id: target.id, name: target.name, pos: target.pos,
            normal: target.normal, target });
          return;
        }
        const collision = engine.modes?.getCollision?.();
        if (!collision) { showToast("collision proxy is still loading"); return; }
        const hit = engine.picking.surfaceHit(engine.apartmentRoot, [collision], e.clientX, e.clientY)
          || (engine.modes?.getMesh?.()
            ? engine.picking.surfaceHit(engine.apartmentRoot, [engine.modes.getMesh()], e.clientX, e.clientY)
            : null);
        if (hit) setAimDestination({ kind: "mesh", id: `mesh-${hit.point.map((v) => v.toFixed(3)).join("-")}`,
          name: "exact mesh point", pos: hit.point, normal: hit.normal });
        else showToast("no collision surface at that point · orbit or select a named target");
        return;
      }
      const clickedDevice = (model.devices || []).find((device) => device.id === deviceId);
      if (clickedDevice?.fixture_kind === window.HomeApartmentAiming?.ENGINEERED_KIND) {
        setCardId(null);
        setAimFixtureId(clickedDevice.id);
        setAimDestination(null);
        setAimMode(true);
        return;
      }
      setCardId(deviceId);
    };
    host.addEventListener("pointerdown", onDown);
    host.addEventListener("pointermove", onMove);
    host.addEventListener("click", onClick);
    return () => {
      host.removeEventListener("pointerdown", onDown);
      host.removeEventListener("pointermove", onMove);
      host.removeEventListener("click", onClick);
      cancelAnimationFrame(raf);
    };
  }, [open, phase, editing, model, aimMode, aimDestination?.id, showToast, simActive,
    apartmentSceneControlsEnabled, rehearsalIsolated]);

  /* label/card screen anchors — updated on a light timer (rAF-driven HUD) */
  useEffect(() => {
    if (!open || phase !== "ready") return undefined;
    const timer = setInterval(() => {
      const engine = engineRef.current;
      if (!engine) return;
      if (virtualFixturePreviewDirtyRef.current && !radialGestureOwnerRef.current) publishVirtualFixturePreviewRef.current?.();
      const out = {};
      const px = (obj) => engine.picking.projectToScreen(
        obj.getWorldPosition(new (Object.getPrototypeOf(obj.position).constructor)()));
      // person anchor
      if (engine.overlay.personGroup.visible) {
        out.__person = px(engine.overlay.personGroup);
      }
      const want = new Set([hoverId, cardId].filter(Boolean));
      for (const id of want) {
        const m = engine.overlay.markersById.get(id);
        if (m) out[id] = px(m.group);
      }
      setAnchors(previous => JSON.stringify(previous) === JSON.stringify(out) ? previous : out);
    }, 80);
    return () => clearInterval(timer);
  }, [open, phase, hoverId, cardId]);

  /* leave the camera snap: shared by esc, the '← back' button, and the
     resurrect-guard — closes the feed, cancels the pending feed timer,
     restores the pre-snap render mode (the calibrated snap forced mesh;
     without this the cloud stays hidden at uOpacity 0 — black screen),
     then flies home */
  const exitCameraPose = useCallback(() => {
    if (flyTimerRef.current) { clearTimeout(flyTimerRef.current); flyTimerRef.current = null; }
    flySeqRef.current += 1;
    setLiveCam(null); setLiveOn(false); setLiveFeedStatus("idle"); setCameraPoseReady(false); setCalibCam(null);
    calibPairCountRef.current = 0; calibEscArmRef.current = 0;
    const e = engineRef.current;
    if (!e) return;
    aptSyncDollhouseContext(e);
    const back = preSnapModeRef.current;
    preSnapModeRef.current = null;
    if (back && e.modes.mode !== back) e.modes.setMode(back, { duration: 0 }).catch(() => {});
    e.rig.returnToOverview();
    setTimeout(() => {
      const eng = engineRef.current;
      if (eng && !eng.rig.inCameraPose?.()) eng.refit?.({ dur: 0 });
    }, 820);
  }, []);

  /* keyboard — rig keys only; Escape is owned by the HomeOverlay layers below */
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.defaultPrevented) return;
      /* Native controls and editors own their arrows/typing. Without SELECT
       * and BUTTON here, moving through the home picker also rotated the 3D
       * scene behind it. N/P is the one deliberate button exception: after a
       * semantic shortcut opens a card, focus moves to its first button, and
       * the same shortcut must be able to continue through every device. */
      const cycleKey = (e.key === "n" || e.key === "p") && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey;
      const semanticCycleFromCard = cycleKey
        && e.target?.closest?.('[data-apt-control-card="1"], [data-victoria-virtual-fixtures="1"]');
      if (e.target?.isContentEditable
          || (e.target && /INPUT|TEXTAREA|SELECT/.test(e.target.tagName))
          || ((e.target?.tagName === "BUTTON" || e.target?.closest?.('[role="button"], [role="tab"], [role="switch"], [role="slider"]'))
            && !semanticCycleFromCard)) return;
      const rig = engineRef.current?.rig;
      if (!rig) return;
      const apartmentOwnsViewport = apartmentSceneControlsEnabled;
      if ((e.key === "a" || e.key === "z") && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey
          && virtualFixtureControlsOpen && selectedSiteId === "victoria" && apartmentOwnsViewport
          && !radialGestureOwnerRef.current && !deskTargetCaptureRef.current?.active) {
        if (changeFixtureInteractionMode(e.key === "z" ? "zones" : "spot")) e.preventDefault();
        return;
      }
      const zoomKey = e.key === "+" || e.key === "=" || e.key === "-";
      if (travellingRef.current && zoomKey) {
        e.preventDefault();
        return;
      }
      if (e.key === "ArrowLeft" && apartmentOwnsViewport) { e.preventDefault(); rig.stepAzimuth(-1); }
      else if (e.key === "ArrowRight" && apartmentOwnsViewport) { e.preventDefault(); rig.stepAzimuth(1); }
      else if (e.key === "ArrowUp" && apartmentOwnsViewport) { e.preventDefault(); rig.stepElevation(1); }
      else if (e.key === "ArrowDown" && apartmentOwnsViewport) { e.preventDefault(); rig.stepElevation(-1); }
      else if ((e.key === "h" || e.key === "Home") && apartmentOwnsViewport) { e.preventDefault(); rig.snapHome(); }
      else if ((e.key === "+" || e.key === "=")
          && (spatialScale !== "apartment" || apartmentOwnsViewport)) {
        e.preventDefault();
        zoomRequestRef.current?.(1, { source: "keyboard" });
      }
      else if (e.key === "-" && (spatialScale !== "apartment"
          || apartmentOwnsViewport || selectedSiteInterior === false)) {
        e.preventDefault();
        zoomRequestRef.current?.(-1, { source: "keyboard" });
      }
      else if (cycleKey && !editing && apartmentOwnsViewport) {
        if (selectedSiteId === "victoria") {
          const context = getVirtualFixtureSelectionContext();
          if (!context) return;
          const ids = context.preview.fixtures.map(fixture => fixture.fixture_id);
          if (!ids.length) return;
          const index = virtualFixtureControlsOpen ? ids.indexOf(virtualFixtureId) : -1;
          const next = index < 0 ? (e.key === "n" ? 0 : ids.length - 1)
            : (index + (e.key === "n" ? 1 : -1) + ids.length) % ids.length;
          if (selectVirtualFixture(ids[next])) e.preventDefault();
          return;
        }
        /* The semantic list is deliberately broader than overlay picks:
         * unpositioned devices have no marker but must remain keyboard
         * discoverable and open the same control card at its safe fallback
         * screen position. */
        if (!semanticDevicesForRoom.length) return;
        e.preventDefault();
        stepSemanticDevice(e.key === "n" ? 1 : -1, { openCard: true });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, editing, spatialScale, selectedSiteInterior, apartmentSceneControlsEnabled,
    semanticDevicesForRoom, stepSemanticDevice, selectedSiteId, getVirtualFixtureSelectionContext,
    virtualFixtureControlsOpen, virtualFixtureId, selectVirtualFixture, changeFixtureInteractionMode]);

  // Remember the actual local UI/canvas trigger before its control can unmount.
  // React portal events still pass through this view, including column controls.
  const rememberEscapeTrigger = event => {
    if (event.key === "Escape") return;
    escapeTriggerRef.current = event.target?.closest?.('button, a[href], input, select, textarea, [tabindex]') || viewRootRef.current;
  };
  React.useLayoutEffect(() => {
    const next = { aim: aimMode, card: cardId, setup: editing, camera: liveCam,
      selection: virtualFixtureControlsOpen && (virtualFixtureId || true), desk: !!deskTargetSelection };
    for (const key of Object.keys(next)) {
      if (next[key] && next[key] !== escapeOperationsRef.current[key]) {
        escapeOpenersRef.current[key] = escapeTriggerRef.current || document.activeElement;
      }
    }
    escapeOperationsRef.current = next;
  }, [aimMode, cardId, editing, liveCam, virtualFixtureControlsOpen, virtualFixtureId, deskTargetSelection]);
  const restoreApartmentOperationFocus = useCallback(key => {
    const opener = escapeOpenersRef.current[key] || escapeTriggerRef.current;
    delete escapeOpenersRef.current[key];
    if (escapeFocusFrameRef.current !== null) cancelAnimationFrame(escapeFocusFrameRef.current);
    escapeFocusFrameRef.current = requestAnimationFrame(() => {
      escapeFocusFrameRef.current = null;
      const top = window.HomeOverlay.topKey();
      if (!viewOpenRef.current || top && top !== "apartment" && top !== "apartment-embedded") return;
      window.HomeOverlay.restoreFocus(opener, viewRootRef);
    });
  }, []);
  useEffect(() => () => {
    if (escapeFocusFrameRef.current !== null) cancelAnimationFrame(escapeFocusFrameRef.current);
  }, []);
  const clearVirtualFixtureSelection = useCallback(() => {
    setVirtualFixtureControlsOpen(false);
    engineRef.current?.overlay.setVirtualFixtureSelected?.(null);
  }, []);

  // Record claims before the canvas aim controller consumes pointer events.
  // Its pointerup can still be followed by a native compatibility click.
  useEffect(() => {
    const context = getVirtualFixtureSelectionContext(), host = hostRef.current;
    const engine = context?.engine, canvas = engine?.renderer?.domElement;
    if (!engine || !host || !canvas) return undefined;
    let pointer = null;
    const current = () => getVirtualFixtureSelectionContext()?.engine === engine;
    const fixtureAt = event => {
      try { return engine.picking.pick(engine.overlay.virtualFixturePickObjects(), event.clientX, event.clientY).length > 0; }
      catch (_) { return true; } // An unavailable pick cannot authorize deselection.
    };
    const canvasAt = event => {
      try { return host.ownerDocument.elementFromPoint(event.clientX, event.clientY) === canvas; }
      catch (_) { return false; }
    };
    const onDown = event => {
      pointer = null;
      if (event.target !== canvas || event.button !== 0 || event.isPrimary === false || event.defaultPrevented
          || event.shiftKey || event.ctrlKey || event.metaKey || event.altKey || !current()
          || virtualAimGestureRef.current?.readback().active) return;
      pointer = { id: event.pointerId, x: event.clientX, y: event.clientY,
        claimed: fixtureAt(event), moved: false, released: false };
    };
    const onMove = event => {
      if (pointer?.id === event.pointerId && Math.hypot(event.clientX - pointer.x, event.clientY - pointer.y) >= 5) pointer.moved = true;
    };
    const onUp = event => {
      if (pointer?.id !== event.pointerId) return;
      onMove(event); pointer.released = true;
      if (event.defaultPrevented || virtualAimGestureRef.current?.readback().active || !current() || !canvasAt(event)) pointer.claimed = true;
    };
    const cancel = () => { pointer = null; };
    const lostCapture = event => { if (pointer?.id === event.pointerId && !pointer.released) cancel(); };
    const onClick = event => {
      const click = pointer; pointer = null;
      if (!click?.released || click.claimed || click.moved || event.defaultPrevented || event.button !== 0
          || Number.isFinite(event.pointerId) && event.pointerId > 0 && event.pointerId !== click.id
          // The orbit owner captures on host; native clicks can retarget there.
          // Only the recorded canvas-origin pointer and uncovered canvas qualify.
          || event.target !== canvas && event.target !== host || !canvasAt(event)
          || !current() || virtualAimGestureRef.current?.readback().active
          || fixtureAt(event)) return;
      clearVirtualFixtureSelection();
    };
    const listeners = [["pointerdown", onDown], ["pointermove", onMove], ["pointerup", onUp],
      ["pointercancel", cancel], ["lostpointercapture", lostCapture], ["click", onClick], ["wheel", cancel]];
    for (const [name, listener] of listeners) host.addEventListener(name, listener, true);
    window.addEventListener("blur", cancel);
    return () => {
      cancel();
      for (const [name, listener] of listeners) host.removeEventListener(name, listener, true);
      window.removeEventListener("blur", cancel);
    };
  }, [getVirtualFixtureSelectionContext, clearVirtualFixtureSelection]);

  // One claimed Escape calls one existing cancellation path. Child overlays
  // and their editable fields have priority through HomeOverlay. No generic
  // camera/session stop, model reset or speculative proposal discard is added.
  const handleApartmentEscape = () => {
    if (radialGestureOwnerRef.current) {
      radialGestureOwnerRef.current.cancel(); restoreApartmentOperationFocus("selection"); return true;
    }
    if (deskTargetCaptureRef.current) {
      cancelDeskTargetSelection(); restoreApartmentOperationFocus("desk"); return true;
    }
    if (virtualAimGestureRef.current?.readback().active) {
      virtualAimGestureRef.current.cancel(); restoreApartmentOperationFocus("drag"); return true;
    }
    // Step 1: a reticle drag cancels and leaves any proposal unchanged.
    if (aimPresentationRef.current?.readback().dragging) { aimPresentationRef.current.cancelDrag(); return true; }
    if (echoNamedAimsRef.current?.escape("drag")) return true;
    if (mapRehearsalOpen) { void window.HomeMapRehearsalUI?.close?.(); return true; }
    if (travellingRef.current || (spatialCoordinatorRef.current?.getState?.()?.phase ?? "idle") !== "idle") {
      void cancelSpatialTravel(); return true;
    }
    // Step 4 for Echo's engineered light: discard its proposal before leaving aim.
    if (echoNamedAimsRef.current?.escape("proposal")) return true;
    if (aimMode) {
      setAimMode(false); setAimDestination(null); restoreApartmentOperationFocus("aim"); return true;
    }
    // Step 2: leave tap to aim.
    if (tapAim) { setTapAim(null); return true; }
    // Guard banked calibration pairs before any camera/setup exit can clear
    // them. Preserve the existing five-second warning and final modal/pass
    // behavior; any new capture still disarms this guard at its source.
    if (calibCam && calibPairCountRef.current > 0) {
      const now = Date.now();
      if (!calibEscArmRef.current || now - calibEscArmRef.current > 5000) {
        calibEscArmRef.current = now;
        showToast(`calibration in progress — Esc again discards ${calibPairCountRef.current} pairs`, { tone: "error" });
        return true;
      }
      calibEscArmRef.current = 0;
      calibPairCountRef.current = 0;
      setToast(null);
      return false;
    }
    if (engineRef.current?.rig?.inCameraPose?.()) {
      exitCameraPose(); restoreApartmentOperationFocus("camera"); return true;
    }
    if (editing) { setEditing(false); restoreApartmentOperationFocus("setup"); return true; }
    if (cardId) { setCardId(null); restoreApartmentOperationFocus("card"); return true; }
    if (echoNamedAimsRef.current?.escape("selection")) return true;
    // Step 4: discard the selected light's proposal; Cmd/Ctrl+Z restores it.
    const pendingAim = virtualFixtureControlsOpen ? aimScopeFor(virtualFixtureId)?.proposal : null;
    if (pendingAim && discardAimProposal({ fixture_id: virtualFixtureId, proposal: pendingAim })) return true;
    if (virtualFixtureControlsOpen) {
      clearVirtualFixtureSelection(); restoreApartmentOperationFocus("selection"); return true;
    }
    return false;
  };
  window.HomeOverlay.useOverlayLayer({
    key: "apartment",
    active: !!(open && !embedded),
    onEscape: () => { if (handleApartmentEscape() === false) onClose?.(); },
    rootRef: viewRootRef,
    getRoots: () => [shellControlsHost, shellCameraHost],
    trap: true,
    initialFocus: "root",
  });
  window.HomeOverlay.useOverlayLayer({
    key: "apartment-embedded",
    active: !!(open && embedded),
    passive: true,
    initialFocus: "none",
    onEscape: handleApartmentEscape,
    getRoot: () => viewRootRef.current,
    getRoots: () => [shellControlsHost, shellCameraHost],
  });

  const refitMobileOverview = useCallback((dur = 520) => {
    const engine = engineRef.current;
    if (!engine || !readAptViewport().mobile) return;
    const wasCameraPose = !!engine.rig.inCameraPose?.();
    if (engine.rig.inCameraPose?.()) {
      if (flyTimerRef.current) { clearTimeout(flyTimerRef.current); flyTimerRef.current = null; }
      flySeqRef.current += 1;
      setLiveCam(null); setLiveOn(false); setLiveFeedStatus("idle"); setCameraPoseReady(false); setCalibCam(null);
      engine.rig.resetPose?.();
    }
    engine.refit?.({ dur: wasCameraPose ? 0 : dur });
  }, []);

  const pickMode = useCallback(async (m) => {
    const engine = engineRef.current;
    /* The resident engine still contains Echo Park while Victoria's opaque
     * placeholder is visible. A programmatic or raced click must not mutate
     * that hidden renderer and make the placeholder appear interactive. */
    if (!engine || selectedSiteInterior === false) return;
    /* The engine refuses too; asking first keeps the "check the asset" toast
     * below for real load failures rather than a scene that has no photo. */
    if (m !== "points" && engine.scenePlaceholder?.pointsOnly) {
      showToast(`${window.HomeSpatialViewport?.getSite?.(engine.siteId)?.shortName || "This home"} · scan pending: point cloud only`);
      return;
    }
    if ((liveCam || engine.rig.inCameraPose?.()) && !calibCam) {
      showToast("camera view is locked to mesh - Return to room to change modes");
      return;
    }
    const siteId = engine.siteId;
    const revision = engine.contentRevision || "legacy";
    const lifecycle = spatialLifecycleRevisionRef.current;
    const intent = spatialTravelIntentRef.current;
    const contextIsCurrent = () => {
      try {
        const site = window.HomeSpatialViewport?.getSite?.(siteId);
        return !!siteId && !!site && viewOpenRef.current && engineRef.current === engine
          && engine.siteId === siteId && (engine.contentRevision || "legacy") === revision
          && spatialLifecycleRevisionRef.current === lifecycle && spatialTravelIntentRef.current === intent
          && window.HomeSpatialViewport?.getSelectedSiteId?.() === siteId
          && (site.contentRevision || "legacy") === revision;
      } catch (_) { return false; }
    };
    if (!contextIsCurrent()) return;
    // explicit pick overrides any pending post-snap restore
    preSnapModeRef.current = null;
    // ALWAYS delegate to the engine — modes.setMode dedups same-mode (cheaply
    // re-asserting visibility) and reconciles raced transitions by generation.
    // The old `if (m === engine.modes.mode) return` short-circuit dropped a
    // 'cloud' click made while a slow mesh load was still in flight (the
    // engine's mode had not flipped yet), leaving the mesh on screen.
    let request;
    const isCurrent = () => modeRequestRef.current === request && contextIsCurrent();
    try {
      // Own only this host's publication. The renderer retains its existing
      // mode generation and load deduplication; no second mode call is made.
      request = engine.modes.setMode(m);
      modeRequestRef.current = request;
      if (isCurrent()) {
        setModeLoading(m);
        setModeError(null);
      }
      await request;
      if (!isCurrent()) return;
      setMode(m);
    } catch (e) {
      if (!isCurrent()) return;
      const assetHint = m === "splat" ? "check apartment.spz" : "check mesh.glb / collision.glb";
      setModeError({
        mode: m,
        message: m === "splat" ? "photo unavailable" : "mesh unavailable",
        detail: `${e?.message || String(e || "asset load failed")} - ${assetHint}`,
      });
      showToast(m === "splat" ? "photo unavailable — check apartment.spz asset"
                              : "mesh unavailable — check collision.glb asset", { tone: "error" });
    } finally {
      if (isCurrent()) {
        setModeLoading(null);
        refitMobileOverview(m === "points" ? 360 : 520);
      }
      if (modeRequestRef.current === request) modeRequestRef.current = null;
    }
  }, [showToast, refitMobileOverview, liveCam, calibCam, selectedSiteInterior]);
  // Progress belongs to the load in flight; it can arrive before the loading
  // state commits (a joined read), so it is cleared only when loading ends.
  useEffect(() => { if (!modeLoading) setModeProgress(null); }, [modeLoading]);
  /* Tier-0 Lit (engine.setLit): Mesh first, then the relit mesh. Session
   * only and never restored, so Lit is off by default on every device. */
  const toggleLit = useCallback(async (next) => {
    const engine = engineRef.current;
    if (!engine?.setLit) return;
    if (next && engine.modes.mode !== "mesh") await pickMode("mesh");
    if (engineRef.current !== engine) return;
    const state = await engine.setLit(!!next && engine.modes.mode === "mesh");
    if (engineRef.current === engine) setLitState(state);
  }, [pickMode]);
  useEffect(() => {
    const engine = engineRef.current;
    setLitState(engine?.litState?.() || null);
    return engine?.on?.("lit", (state) => { if (engineRef.current === engine) setLitState(state); });
  }, [phase, selectedSiteId]);

  /*
   * The +/- controls stay mounted through a handoff, but the already-targeted
   * direction becomes disabled; projected home markers are also temporarily
   * disabled during a cross-home flight. If one launched the journey, park
   * focus on the stable viewport root before that state change, then restore
   * the same semantic control after the coordinator is idle. Pointer-initiated
   * motion does not steal focus because no tracked control is active.
   */
  const parkSpatialControlFocus = useCallback(() => {
    const active = document.activeElement;
    let target = null;
    if (active === zoomInButtonRef.current) target = "zoom-in";
    else if (active === zoomOutButtonRef.current) target = "zoom-out";
    else if (active?.hasAttribute?.("data-apt-world-pin")) {
      target = "world-pin";
      pendingSpatialFocusElementRef.current = active;
    }
    if (!target) return;
    if (target !== "world-pin") pendingSpatialFocusElementRef.current = null;
    pendingSpatialFocusRef.current = target;
    try { viewRootRef.current?.focus?.({ preventScroll: true }); }
    catch (_) { try { viewRootRef.current?.focus?.(); } catch (_) { /* detached */ } }
  }, []);

  useEffect(() => {
    if (!open || travelling || spatialTransition !== "idle" || !pendingSpatialFocusRef.current) {
      return undefined;
    }
    const frame = requestAnimationFrame(() => {
      const key = pendingSpatialFocusRef.current;
      const target = key === "zoom-in"
        ? zoomInButtonRef.current
        : key === "zoom-out"
          ? zoomOutButtonRef.current
          : key === "world-pin"
            ? pendingSpatialFocusElementRef.current
            : null;
      pendingSpatialFocusRef.current = null;
      pendingSpatialFocusElementRef.current = null;
      const fallback = viewRootRef.current;
      const next = target?.isConnected && !target.disabled ? target : fallback;
      try { next?.focus?.({ preventScroll: true }); }
      catch (_) { try { next?.focus?.(); } catch (_) { /* detached */ } }
    });
    return () => cancelAnimationFrame(frame);
  }, [open, spatialScale, spatialTransition, travelling]);

  const zoomApartment = useCallback(async (dir, meta = null) => {
    const rig = engineRef.current?.rig;
    if (!rig) return;
    const direction = dir < 0 ? "outward" : "inward";
    const existingCoordinator = spatialCoordinatorRef.current;
    const coordinatorState = existingCoordinator?.getState?.();
    const missingInteriorExit = spatialScale === "apartment"
      && selectedSiteInterior === false;

    /* A geographic flight owns the world camera until it settles. Its pin
     * remains a one-way, non-reversible trip; user zoom input is swallowed so
     * wheelZoom cannot cancel the flight and snap back around the old anchor. */
    if (aptTravelBlocksZoomInput(travellingRef.current, meta?.source)) return false;

    /* The missing-interior panel sits above a still-resident scan for another
     * home. It may only leave through the explicit outward viewport control;
     * inward requests and any direct rig fallback would mutate that hidden
     * scan. A persisted world-only selection may reopen before a coordinator
     * exists, so outward exit is allowed to construct that world lazily. */
    if (missingInteriorExit && direction !== "outward") return false;

    /* A painted handoff owns one canonical clock. Retarget that live clock
     * instead of cancelling the coordinator and letting rollback replace a
     * partial frame with full Apartment/world opacity in one paint. The clock
     * decides the semantic outcome only when it reaches an endpoint. */
    const liveHandoff = handoffMotionRef.current;
    if (liveHandoff?.active && coordinatorState?.phase === "activating") {
      const snapshot = liveHandoff.retarget(direction, meta?.source);
      setSpatialTransitionDirection(snapshot.targetDirection ?? direction);
      return Object.freeze({
        status: "retargeted",
        intentId: liveHandoff.context.intentId,
        direction: snapshot.targetDirection,
        progress: snapshot.progress,
      });
    }

    if (missingInteriorExit) {
      const anchor = aptConfirmedWorldAnchor(syntheticSpatialQa);
      if (!anchor) {
        showToast("world view needs a confirmed home location", { tone: "error" });
        return false;
      }
      try {
        /* Do not step/reset the resident rig: it still contains Echo while the
         * opaque Victoria placeholder is selected. Only the spatial
         * coordinator may transfer ownership to the selected world anchor. */
        const coordinator = existingCoordinator || await ensureSpatialCoordinator(anchor);
        parkSpatialControlFocus();
        const result = await coordinator.requestSemanticZoom("outward", {
          atApartmentOverview: true,
          intentId: meta?.intentId || `apartment-viewport-${++spatialIntentRef.current}`,
        });
        if (result.status === "failed") {
          showToast("world view unavailable - Apartment remains active", { tone: "error" });
        }
        return result;
      } catch (error) {
        if (error?.name === "AbortError") return undefined;
        console.warn("[apartment] missing-interior world exit failed:", error?.message || error);
        showToast("world view unavailable - Apartment remains active", { tone: "error" });
        return undefined;
      }
    }

    if (existingCoordinator && (
      coordinatorState?.committedScale !== "apartment"
      || coordinatorState?.phase !== "idle"
    )) {
      parkSpatialControlFocus();
      try {
        if (direction === "inward" && coordinatorState?.committedScale === "parcel"
            && coordinatorState?.phase === "idle") {
          const siteId = window.HomeSpatialViewport?.getSelectedSiteId?.();
          const travelRevision = spatialTravelIntentRef.current;
          const ready = await prepareInteriorForSite(siteId, () => (
            spatialTravelIntentRef.current === travelRevision
            && window.HomeSpatialViewport?.getSelectedSiteId?.() === siteId
            && existingCoordinator.getState()?.committedScale === "parcel"
            && existingCoordinator.getState()?.phase === "idle"
          ));
          if (!ready) {
            if (window.HomeSpatialViewport?.getSelectedSite?.()?.sceneAvailable !== true) {
              showToast("Exterior view · interior scan not uploaded yet.");
              return { status: "held", reason: "interior-not-uploaded" };
            }
            setJourneyRecovery({ kind: "content-unavailable", message: "This home's interior is not ready. The outside view remains available." });
            journeyRetryRef.current = () => zoomRequestRef.current?.(1, { source: "retry-interior-content" });
            return { status: "held", reason: "interior-not-ready" };
          }
        }
        const result = await existingCoordinator.requestSemanticZoom(direction, {
          intentId: meta?.intentId || `apartment-viewport-${++spatialIntentRef.current}`,
        });
        if (result.status === "failed") {
          showToast("world view could not change scale", { tone: "error" });
        }
        return result;
      } catch (error) {
        console.warn("[apartment] spatial viewport zoom failed:", error?.message || error);
        showToast("world view could not change scale", { tone: "error" });
        return undefined;
      }
    }

    if (rig.inCameraPose?.()) {
      showToast("camera view is locked - Return to room before zooming");
      return;
    }
    if (direction !== "outward" || !rig.atWorldBoundary?.() || !worldZoomEnabled) {
      return rig.stepZoom(dir, 260);
    }
    if (phase !== "ready" || editing || aimMode || calibCam || liveCam || liveOn) {
      showToast("finish the current Apartment task before zooming outside");
      return;
    }

    const anchor = aptConfirmedWorldAnchor(syntheticSpatialQa);
    if (!anchor) {
      showToast("world view needs a confirmed home location", { tone: "error" });
      return;
    }

    try {
      const coordinator = await ensureSpatialCoordinator(anchor);
      /* A reversal can happen while mounting/prewarming the world. The pure
       * boundary controller owns that gesture; if its exact intent is no longer
       * active, never let this delayed preparation start a stale handoff. */
      if (
        meta?.boundaryIntent
        && boundaryMomentumRef.current?.snapshot?.()?.activeIntentId !== meta.intentId
      ) {
        return { status: "cancelled", intentId: meta.intentId };
      }
      parkSpatialControlFocus();
      const result = await coordinator.requestSemanticZoom("outward", {
        atApartmentOverview: true,
        intentId: meta?.intentId || `apartment-viewport-${++spatialIntentRef.current}`,
      });
      if (result.status === "failed") {
        showToast("world view unavailable - Apartment remains active", { tone: "error" });
      }
      return result;
    } catch (error) {
      if (error?.name === "AbortError") return undefined;
      console.warn("[apartment] world viewport handoff failed:", error?.message || error);
      engineRef.current?.setRunning(true);
      setSpatialScale("apartment");
      setSpatialTransition("idle");
      setSpatialTransitionDirection(null);
      showToast("world view unavailable - Apartment remains active", { tone: "error" });
    }
  }, [aimMode, calibCam, editing, ensureSpatialCoordinator, liveCam, liveOn,
    parkSpatialControlFocus, phase, prepareInteriorForSite, selectedSiteInterior, showToast, syntheticSpatialQa,
    spatialScale, worldZoomEnabled]);
  zoomRequestRef.current = zoomApartment;

  /* The missing-interior cover, not the resident Echo canvas, owns the one
   * continuous gesture that remains meaningful in Victoria: outward zoom back
   * to the map. Positive wheel motion and a closing two-finger pinch call the
   * coordinator directly through the normal zoom path. All inward, orbit,
   * click, and double-click input terminates on this opaque sibling. */
  useEffect(() => {
    const panel = emptyInteriorRef.current;
    if (!open || !panel || spatialScale !== "apartment"
        || spatialTransition !== "idle" || selectedSiteInterior !== false) {
      return undefined;
    }

    emptyInteriorExitPendingRef.current = false;
    const touches = new Map();
    let pinchDistance = 0;
    const span = () => {
      const points = [...touches.values()];
      if (points.length !== 2) return 0;
      return Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);
    };
    const requestOutward = (source) => {
      if (emptyInteriorExitPendingRef.current) return;
      emptyInteriorExitPendingRef.current = true;
      Promise.resolve(zoomRequestRef.current?.(-1, { source })).then(
        (result) => {
          if (!result || result.status === "failed" || result.status === "cancelled") {
            emptyInteriorExitPendingRef.current = false;
          }
        },
        () => { emptyInteriorExitPendingRef.current = false; },
      );
    };
    const onWheel = (event) => {
      event.preventDefault();
      if (Number(event.deltaY) > 0) requestOutward("missing-interior-wheel");
    };
    const onTouchStart = (event) => {
      for (const touch of event.changedTouches) {
        touches.set(touch.identifier, { x: touch.clientX, y: touch.clientY });
      }
      pinchDistance = touches.size === 2 ? span() : 0;
    };
    const onTouchMove = (event) => {
      for (const touch of event.changedTouches) {
        if (touches.has(touch.identifier)) {
          touches.set(touch.identifier, { x: touch.clientX, y: touch.clientY });
        }
      }
      event.preventDefault();
      if (touches.size !== 2) { pinchDistance = 0; return; }
      const nextDistance = span();
      if (pinchDistance > 0 && nextDistance < pinchDistance - 8) {
        requestOutward("missing-interior-pinch");
      }
      pinchDistance = nextDistance;
    };
    const onTouchEnd = (event) => {
      for (const touch of event.changedTouches) touches.delete(touch.identifier);
      pinchDistance = touches.size === 2 ? span() : 0;
    };

    panel.addEventListener("wheel", onWheel, { passive: false });
    panel.addEventListener("touchstart", onTouchStart, { passive: true });
    panel.addEventListener("touchmove", onTouchMove, { passive: false });
    panel.addEventListener("touchend", onTouchEnd, { passive: true });
    panel.addEventListener("touchcancel", onTouchEnd, { passive: true });
    return () => {
      panel.removeEventListener("wheel", onWheel);
      panel.removeEventListener("touchstart", onTouchStart);
      panel.removeEventListener("touchmove", onTouchMove);
      panel.removeEventListener("touchend", onTouchEnd);
      panel.removeEventListener("touchcancel", onTouchEnd);
      touches.clear();
      pinchDistance = 0;
    };
  }, [open, selectedSiteInterior, spatialScale, spatialTransition]);

  /*
   * Turn the pure boundary reducer into the one Apartment-side motion loop.
   *
   * The rig continues to own every interior detent. At its outermost detent,
   * native wheel magnitude feeds a short elastic preview, then momentum either
   * springs the Apartment back or emits exactly one opaque coordinator intent.
   * The same canonical progress seeds runHandoff(), so the snap is a continuation
   * of the gesture instead of a second animation starting at full size.
   */
  useEffect(() => {
    if (!open) return undefined;
    let disposed = false;

    const paintBoundary = (state) => {
      const engine = engineRef.current;
      const controller = boundaryMomentumRef.current;
      if (!engine?.rig || !controller || !state) return;
      if (state.phase === "committed") {
        engine.rig.setOvershoot?.(0);
        return;
      }
      const hostWidthPx = hostRef.current?.clientWidth || window.innerWidth || 1280;
      engine.rig.setOvershoot?.(controller.rigOvershoot(hostWidthPx));
    };

    const scheduleFrame = () => {
      if (disposed || boundaryFrameRef.current) return;
      const phaseNow = boundaryMomentumRef.current?.snapshot?.()?.phase;
      if (phaseNow !== "preview" && phaseNow !== "returning") return;
      boundaryFrameRef.current = requestAnimationFrame((at) => {
        boundaryFrameRef.current = 0;
        if (disposed) return;
        const previous = boundaryLastFrameRef.current || at;
        boundaryLastFrameRef.current = at;
        const controller = boundaryMomentumRef.current;
        if (!controller) return;
        processResult(controller.dispatch({
          type: "frame",
          deltaMs: Math.max(0, Math.min(50, at - previous)),
        }));
      });
    };

    const handleEffect = (effect) => {
      if (effect.type === "snap") {
        void (async () => {
          let result;
          try {
            result = await zoomRequestRef.current?.(-1, {
              source: "boundary-snap",
              intentId: effect.intentId,
              boundaryIntent: true,
            });
          } catch (_) {
            result = { status: "failed" };
          }
          if (disposed) return;
          const controller = boundaryMomentumRef.current;
          if (!controller) return;
          const status = result?.status === "committed"
            ? "committed"
            : result?.status === "failed"
              ? "failed"
              : result?.status === "stale"
                ? "superseded"
                : "cancelled";
          if (status === "cancelled" && handoffSourceReversalRef.current === effect.intentId) {
            handoffSourceReversalRef.current = null;
            /* The compositor already retraced all the way to canonical 0. Mark
             * the intent settled, then reset without painting the boundary's
             * separate elastic return from charge 1; doing both state updates
             * in this task avoids a second shrink after the seamless inverse. */
            controller.dispatch({
              type: "settled",
              intentId: effect.intentId,
              status,
            });
            processResult(controller.dispatch({
              type: "reset",
              reason: "handoff-visually-reversed",
            }));
            return;
          }
          processResult(controller.dispatch({
            type: "settled",
            intentId: effect.intentId,
            status,
          }));
        })();
        return;
      }
      if (effect.type === "cancel") {
        const coordinator = spatialCoordinatorRef.current;
        if (!coordinator) return;
        /* Requesting the opposite semantic direction is also the coordinator's
         * cancellation primitive: latest intent wins, its AbortController stops
         * preparation/animation, and the target adapter restores the committed
         * owner before this spring paints the Apartment back to its detent. */
        void coordinator.requestSemanticZoom("inward", {
          intentId: `${effect.intentId}:reverse`,
        }).catch(() => {});
      }
    };

    function processResult(result) {
      if (!result || disposed) return;
      paintBoundary(result.state);
      result.effects?.forEach(handleEffect);
      if (result.state.phase === "preview" || result.state.phase === "returning") {
        scheduleFrame();
      } else {
        boundaryLastFrameRef.current = 0;
      }
    }

    const dispatch = (action) => {
      const controller = boundaryMomentumRef.current;
      if (!controller) return null;
      const result = controller.dispatch(action);
      processResult(result);
      return result;
    };

    boundaryWheelRef.current = (meta) => dispatch({
      type: "wheel",
      deltaY: meta?.deltaY,
      deltaMode: meta?.deltaMode,
      viewportHeightPx: meta?.viewportHeightPx,
      timeStampMs: meta?.timeStamp,
    });

    const reset = (reason) => {
      dispatch({ type: "reset", reason });
      engineRef.current?.rig?.setOvershoot?.(0);
    };
    const onBlur = () => reset("window-blurred");
    const onVisibility = () => {
      if (document.visibilityState === "hidden") reset("document-hidden");
    };
    window.addEventListener("blur", onBlur);
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      disposed = true;
      boundaryWheelRef.current = null;
      window.removeEventListener("blur", onBlur);
      document.removeEventListener("visibilitychange", onVisibility);
      if (boundaryFrameRef.current) cancelAnimationFrame(boundaryFrameRef.current);
      boundaryFrameRef.current = 0;
      boundaryLastFrameRef.current = 0;
      engineRef.current?.rig?.setOvershoot?.(0);
    };
  }, [open]);

  /* Starting another Apartment task while the outer-detent preview is elastic
   * must restore the exact detent first. These states cannot become true during
   * a committed cross-surface handoff because Apartment input is already inert. */
  useEffect(() => {
    if (!open || (phase === "ready" && !editing && !aimMode && !calibCam && !liveCam && !liveOn)) {
      return;
    }
    boundaryMomentumRef.current?.dispatch?.({ type: "reset", reason: "apartment-task-started" });
    engineRef.current?.rig?.setOvershoot?.(0);
  }, [aimMode, calibCam, editing, liveCam, liveOn, open, phase]);

  const requestWorldImageryAdoption = useCallback((source) => {
    if (syntheticSpatialQa) return Promise.resolve({ status: "skipped" });
    if (worldOverviewActive && spatialScale !== "apartment" && source !== "recovery-retry") {
      return Promise.resolve({ status: "overview" });
    }
    const anchor = confirmedWorldAnchorRef.current;
    const site = window.HomeSpatialViewport?.getSelectedSite?.();
    const targetRevision = aptWorldEntryRevision(
      anchor,
      site?.dollhouseRegistration ?? null,
    );
    return worldImageryAdopterRef.current?.request?.({ source, targetRevision })
      ?? Promise.resolve({ status: "unavailable" });
  }, [syntheticSpatialQa, worldOverviewActive, spatialScale]);

  useEffect(() => {
    if (!open) return undefined;
    const host = worldHostRef.current;
    if (!host) return undefined;
    /*
     * Continuous, damped, anchor-locked. Not a band transition.
     *
     * Every wheel event used to be rate-limited to one per 150ms and then
     * turned into a whole discrete band jump, so a single flick crossed parcel
     * to city to country to planet in about half a second and each hop was its
     * own 300ms animation. The continuous log-range zoom this replaces it with
     * already existed and was simply never connected: events accumulate into
     * one live motion that keeps easing after the fingers stop.
     *
     * The rig path stays for the buttons and the keyboard, which are discrete
     * by nature; only the wheel is continuous.
     */
    const reverseActiveTransition = (deltaY, source) => {
      if (spatialTransition === "idle" || !deltaY) return false;
      const state = spatialCoordinatorRef.current?.getState?.();
      const activeDirection = state?.transition?.direction;
      const requestedDirection = deltaY < 0 ? "inward" : "outward";
      const liveTarget = handoffMotionRef.current?.snapshot?.()?.targetDirection;
      /* During the compositor window compare with the LIVE target, not with the
       * direction that originally launched the coordinator. That distinction
       * makes outward -> inward -> outward possible inside one 420ms clock.
       * During preparation there is no painted clock yet, so the older
       * latest-intent cancellation path remains the safe behaviour. */
      const shouldRetarget = liveTarget
        ? requestedDirection !== liveTarget
        : activeDirection && requestedDirection !== activeDirection;
      if (shouldRetarget) {
        void zoomRequestRef.current?.(requestedDirection === "inward" ? 1 : -1, {
          source,
        });
      }
      return true;
    };

    const onWorldWheel = (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (travellingRef.current) return;
      if (reverseActiveTransition(event.deltaY, "world-transition-reverse")) return;
      const surface = worldSurfaceRef.current;
      if (!surface?.wheelZoom) return;
      const result = surface.wheelZoom({ deltaY: event.deltaY, deltaMode: event.deltaMode });
      /* A late root request is still hidden at this point. This explicit
       * gesture owns one coalesced readiness attempt, but camera motion never
       * waits for it. */
      void requestWorldImageryAdoption("wheel").catch(() => {});
      /*
       * Scrolling in past the camera floor is what asks to come home.
       *
       * Making the wheel continuous removed the only path that triggered the
       * return: every inward event used to be a discrete band step, and the
       * last one crossed back into the Apartment. Continuous zoom just clamps
       * at the floor instead, so the gesture has to be re-attached to the
       * boundary the surface now reports. Guarded on the transition being idle
       * because the clamp is reported on every further notch, and without it a
       * user still scrolling would queue a second handoff into the first.
       */
      if (result?.atReturnBoundary && spatialTransition === "idle") {
        // +1 is INWARD here, not outward. The rig's convention is that "+"
        // zooms in, and passing -1 asked for the opposite: the return boundary
        // walked the camera out to planet scale instead of bringing it home.
        void zoomRequestRef.current?.(1, { source: "world-return" });
      }
    };
    /*
     * Pinch drives the SAME momentum path as the wheel.
     *
     * Not a second zoom implementation. A pinch that called its own code would
     * feel different from a scroll for no reason a user could name, and would
     * need its own return-boundary handling -- which is exactly how the return
     * gesture got lost when the wheel went continuous. The pinch is converted
     * into the wheel's own delta units and handed to the same call, so every
     * behaviour downstream (momentum, coast, the boundary that brings you home)
     * is inherited rather than reimplemented.
     */
    const activeTouches = new Map();
    let pinchDistance = 0;

    const touchSpan = () => {
      const points = [...activeTouches.values()];
      if (points.length < 2) return 0;
      return Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);
    };

    const onTouchStart = (event) => {
      for (const touch of event.changedTouches) {
        activeTouches.set(touch.identifier, { x: touch.clientX, y: touch.clientY });
      }
      if (activeTouches.size === 2) pinchDistance = touchSpan();
    };

    const onTouchMove = (event) => {
      if (activeTouches.size < 2) return;
      for (const touch of event.changedTouches) {
        if (activeTouches.has(touch.identifier)) {
          activeTouches.set(touch.identifier, { x: touch.clientX, y: touch.clientY });
        }
      }
      const span = touchSpan();
      if (!(span > 0) || !(pinchDistance > 0)) return;
      event.preventDefault();
      const delta = pinchDistance - span;
      pinchDistance = span;
      if (travellingRef.current) {
        event.stopPropagation();
        return;
      }
      /* Pixels of pinch are not pixels of wheel: a comfortable pinch travels far
       * less than a scroll gesture, so it is scaled to reach the same step. */
      if (Math.abs(delta) < 0.5) return;
      if (reverseActiveTransition(delta, "world-transition-reverse-pinch")) return;
      const surface = worldSurfaceRef.current;
      if (!surface?.wheelZoom) return;
      const result = surface.wheelZoom({ deltaY: delta * 2.6, deltaMode: 0 });
      void requestWorldImageryAdoption("pinch").catch(() => {});
      if (result?.atReturnBoundary && spatialTransition === "idle") {
        void zoomRequestRef.current?.(1, { source: "world-return-pinch" });
      }
    };

    const onTouchEnd = (event) => {
      for (const touch of event.changedTouches) activeTouches.delete(touch.identifier);
      pinchDistance = activeTouches.size === 2 ? touchSpan() : 0;
    };

    host.addEventListener("wheel", onWorldWheel, { passive: false, capture: true });
    host.addEventListener("touchstart", onTouchStart, { passive: true });
    host.addEventListener("touchmove", onTouchMove, { passive: false });
    host.addEventListener("touchend", onTouchEnd, { passive: true });
    host.addEventListener("touchcancel", onTouchEnd, { passive: true });
    /* Offered to overlays only while the host itself would be hit-tested. */
    worldWheelHandlerRef.current = aptWorldHostTakesInput(spatialScale, spatialTransition)
      ? onWorldWheel : null;
    return () => {
      if (worldWheelHandlerRef.current === onWorldWheel) worldWheelHandlerRef.current = null;
      host.removeEventListener("wheel", onWorldWheel, { capture: true });
      host.removeEventListener("touchstart", onTouchStart);
      host.removeEventListener("touchmove", onTouchMove);
      host.removeEventListener("touchend", onTouchEnd);
      host.removeEventListener("touchcancel", onTouchEnd);
    };
  }, [open, requestWorldImageryAdoption, spatialScale, spatialTransition, travelling]);

  /*
   * A wheel that landed on an overlay control, handed to the world.
   *
   * The SAME event, not a copy. Dispatching a synthetic WheelEvent at the host
   * would run every window capture listener a second time (the activity
   * monitor, the journey harness's latency probe) and the zoom would count a
   * notch nobody made. While the world is not taking input the event is left
   * alone, exactly as the host would have been skipped by hit-testing.
   */
  const forwardWheelToWorld = useCallback((event) => {
    worldWheelHandlerRef.current?.(event);
  }, []);

  /*
   * Travel to the other home.
   *
   * The gesture is a destination change, so the journey has to read as one:
   * leave the home you are in through the same collapse the wheel produces,
   * cross the globe, and select the new map destination at its safely resolved
   * arrival scale.
   * Switching the anchor underneath a live view would teleport the camera
   * across a continent between two frames, which is the one thing the whole
   * handoff exists to avoid.
   */
  const switchInteriorHome = useCallback(async (siteId, options = {}) => {
    if (!viewOpenRef.current) return false;
    if (!interiorSessionRef.current) {
      interiorSessionRef.current = window.HomeApartmentSiteSession.create({
        registry: window.HomeSpatialViewport,
        getEngine: () => engineRef.current,
        begin: () => {
          const cancelled = cancelSpatialTravel();
          const generation = ++spatialTravelIntentRef.current;
          const lifecycle = ++spatialLifecycleRevisionRef.current;
          return { cancelled, isCurrent: () => generation === spatialTravelIntentRef.current
            && lifecycle === spatialLifecycleRevisionRef.current && viewOpenRef.current };
        },
        beforeSelect: () => {
          clearInteriorState();
          worldScaleObserverCleanupRef.current?.(); worldScaleObserverCleanupRef.current = null;
          worldEntryPrewarmRef.current?.dispose?.(); worldEntryPrewarmRef.current = null;
          worldEntryPrewarmPromiseRef.current = null;
          worldSurfacePendingRef.current?.dispose?.(); worldSurfacePendingRef.current = null;
          worldSurfaceRef.current?.dispose?.(); worldSurfaceRef.current = null;
          worldSurfacePromiseRef.current = null;
          worldImageryAdopterRef.current?.cancel?.(); worldImageryAdopterRef.current = null;
          confirmedWorldAnchorRef.current = null; worldAnchorKeyRef.current = "";
          aptDisposeHandoffCompositorSlots(handoffCompositorRef, handoffCompositorPendingRef,
            handoffCompositorPromiseRef, handoffCompositorGenerationRef);
          worldHostRef.current?.style.setProperty("opacity", "0");
          hostRef.current?.style.removeProperty("transform");
          hostRef.current?.style.removeProperty("opacity");
          if (handoffFallbackRef.current) {
            handoffFallbackRef.current.style.opacity = "0";
            handoffFallbackRef.current.style.visibility = "hidden";
          }
          spatialCoordinatorRef.current?.dispose?.(); spatialCoordinatorRef.current = null;
          spatialCoordinatorPromiseRef.current = null;
          setSpatialScale("apartment"); setSpatialTransition("idle"); setSpatialTransitionDirection(null);
        },
        prepare: prepareInteriorForSite,
        onState: (state) => {
          setInteriorSwitchState(state);
          setTravelling(false); travellingRef.current = false;
          if (state.phase === "ready") engineRef.current?.setRunning(true);
          if (state.phase === "unavailable") setPhase("ready");
          if (state.phase === "error") { setPhase("error"); setError(state.error); }
        },
      });
    }
    return interiorSessionRef.current.request(siteId, options);
  }, [cancelSpatialTravel, clearInteriorState, prepareInteriorForSite]);
  interiorSwitchRequestRef.current = switchInteriorHome;

  useEffect(() => {
    if (!open || travellingRef.current) return;
    const state = interiorSessionRef.current?.readback?.();
    const desiredMatches = state?.siteId === selectedSiteId && (state?.contentRevision || null) === selectedContentRevision;
    if (desiredMatches && ["loading", "ready", "unavailable", "error"].includes(state.phase)) return;
    const engine = engineRef.current;
    if (!engine && (!state || state.phase === "idle")) return;
    if (!engine || engine.siteId !== selectedSiteId || (engine.contentRevision || null) !== selectedContentRevision || state?.phase === "invalidated") {
      if ((spatialCoordinatorRef.current?.getState?.()?.committedScale ?? spatialScale) === "apartment") {
        void switchInteriorHome(selectedSiteId, { force: true });
      }
    }
  }, [open, selectedSiteId, selectedContentRevision, switchInteriorHome, spatialScale, interiorSwitchState.phase]);

  useEffect(() => {
    const api = { selectHome: (siteId) => switchInteriorHome(siteId), readback: () => ({
      ...interiorSessionRef.current?.readback?.(),
      loadedSiteId: engineRef.current?.siteId || null,
      loadedContentRevision: engineRef.current?.contentRevision || null,
    }) };
    window.__HOME_APARTMENT_SITE_API = api;
    return () => { if (window.__HOME_APARTMENT_SITE_API === api) delete window.__HOME_APARTMENT_SITE_API; };
  }, [switchInteriorHome]);

  const travelToSite = useCallback(async (siteId, options = {}) => {
    if (options.presentation === "interior" || !aptWorldZoomEnabled()) return switchInteriorHome(siteId, options);
    const registry = window.HomeSpatialViewport;
    if (!registry) return false;
    const wasTravelling = travellingRef.current;
    const cancelPending = cancelSpatialTravel();
    const travelIntent = ++spatialTravelIntentRef.current;
    await cancelPending;
    await window.HomeContentPacks?.ready;
    if (spatialTravelIntentRef.current !== travelIntent) return false;
    const target = registry.getSite?.(siteId);
    if (!target || (!wasTravelling && siteId === registry.getSelectedSiteId?.())) return false;
    const destination = registry.getAnchorForSite?.(siteId);
    if (!destination) return false;

    const lifecycleRevision = spatialLifecycleRevisionRef.current;
    const travelIsCurrent = () => (
      spatialTravelIntentRef.current === travelIntent
      && spatialLifecycleRevisionRef.current === lifecycleRevision
    );
    const requireCurrentTravel = () => {
      if (travelIsCurrent()) return;
      const error = new Error("Apartment travel superseded");
      error.name = "AbortError";
      throw error;
    };

    parkSpatialControlFocus();
    travellingRef.current = true;
    setTravelling(true);
    setJourneyRecovery(null);
    setToast(null);
    const journeyDebug = { siteId, startedFromScale: spatialScale };
    window.__aptJourneyDebug = journeyDebug;
    try {
      await ensureSpatialCoordinator();
      requireCurrentTravel();
      journeyDebug.coordinatorPhase = spatialCoordinatorRef.current?.getState?.()?.phase ?? null;
      journeyDebug.committedScale = spatialCoordinatorRef.current?.getState?.()?.committedScale ?? null;
      const destinationHero = await worldSurfaceRef.current?.prepareHeroStills?.(
        destination.heroStills || [], destination,
      );
      requireCurrentTravel();
      if (destinationHero?.ready !== true) {
        setJourneyRecovery({ kind: "content-unavailable", message: "The destination home pack is not ready for this display." });
        journeyRetryRef.current = () => travelToSite(siteId);
        journeyDebug.destinationPresentation = "held-before-departure";
        return false;
      }

      /*
       * Leave the way you always leave.
       *
       * If we are inside, the journey starts with the ordinary collapse out of
       * THIS home -- not the destination. An earlier version re-anchored first
       * so the particles would assemble into the destination, which loaded
       * faster but threw away the premise: you never saw the home you were
       * leaving recede, so the trip had no origin.
       */
      if (spatialCoordinatorRef.current?.getState?.()?.committedScale === "apartment") {
        const rig = engineRef.current?.rig;
        if (!rig) throw new Error("Apartment camera is unavailable");
        /* A site can be selected from any interior zoom detent. Collapse must
         * still begin at the overview boundary: one zoomRequest only advances
         * one detent, then correctly refuses to create the world surface. Move
         * to zoom 0 first while preserving azimuth/elevation and await the
         * actual rig tween. rig.goTo samples Reduce Motion itself. */
        boundaryMomentumRef.current?.dispatch?.({
          type: "reset",
          reason: "site-travel-started",
        });
        rig.setOvershoot?.(0);
        if (rig.zoomIndex?.() !== 0) {
          rig.goTo({ zoom: 0, dur: 350 });
        }
        const overview = await aptWaitForRigSettle(rig, {
          isCurrent: travelIsCurrent,
          timeoutMs: aptPrefersReducedMotion() ? 80 : 700,
        });
        requireCurrentTravel();
        journeyDebug.apartmentOverview = overview;

        /*
         * Leave through zoomApartment, not through the coordinator directly.
         *
         * Calling requestSemanticZoom straight skips everything zoomApartment
         * does first -- most importantly creating the world surface. Without it
         * the departure silently does nothing, worldSurfaceRef stays null, the
         * flight below is skipped for want of a surface, and the journey
         * "completes" having never left the room. -1 is outward.
         */
        const departure = await zoomRequestRef.current?.(-1, { source: `travel-out-${siteId}` });
        requireCurrentTravel();
        /* Product-safe: a status string and a scale name, no coordinates. The
         * departure's result was discarded before, which is precisely what let
         * a silent no-op look like a completed journey. */
        journeyDebug.departure = departure ?? null;
        if (departure?.status !== "committed" || departure?.scale !== "parcel") {
          throw new Error(`home departure did not complete: ${departure?.status || "missing"}`);
        }
        journeyDebug.ownerAfterDeparture = document
          .querySelector('[data-apt-spatial-owner]')?.dataset.aptSpatialOwner ?? null;
      }

      /*
       * The journey itself: out to the planet, across, and down again.
       *
       * One continuous camera move rather than a cut hidden behind the
       * firewall. The point of having two homes is that they are on the same
       * planet, and this is the only moment that says so.
       */
      const surface = worldSurfaceRef.current;
      if (!surface?.flyToSite) throw new Error("world surface is unavailable");
      let arrivalScale = null;
      /* Set when the approach down the still stops short: see below. */
      let approachHeld = null;
      {
        const azimuth = destination.localXAxisAzimuthDeg;
        /*
         * Registered bearing first; a chosen sight line second; north last.
         *
         * The last of those used to be the only one, paired with a -45 pitch
         * that matched nothing else in the product -- the "frozen literal with
         * no stated rule" this file's own plan calls out. North is not a
         * framing decision, it is the absence of one, and at Victoria it aimed
         * the arrival straight into closed tree canopy with the house behind
         * it. PARCEL_DESCENT_PITCH_DEG is the same descent Echo Park uses, so
         * both homes now arrive at the same angle and differ only in bearing.
         */
        let headingDeg = Number.isFinite(destination.parcelViewHeadingDeg)
          ? destination.parcelViewHeadingDeg
          : 0;
        /* PARCEL_DESCENT_PITCH_DEG in world-camera.mjs. Repeated as a literal
         * because this file is a classic script and cannot import the module;
         * world-camera.test.mjs pins the two to the same number. */
        let pitchDeg = -35;
        if (Number.isFinite(azimuth)) {
          try {
            const pose = await engineRef.current?.apartmentWorldPose({ localXAxisAzimuthDeg: azimuth });
            if (pose) { headingDeg = pose.headingDeg; pitchDeg = pose.pitchDeg; }
          } catch (_) { /* the default framing stands */ }
          requireCurrentTravel();
        }
        /* The destination's stills, armed before the flight so they are decoded
         * by the time it lands. setAnchor drops the departure's levels; nothing
         * put the arrival's back, so travelling anywhere left the world showing
         * raw mesh however many stills that home had declared. */
        if (destination.heroStills?.length) {
          headingDeg = destination.heroStills[0].headingDeg;
          pitchDeg = destination.heroStills[0].pitchDeg;
        }
        /*
         * The flight stops about 1.2 km out and the approach glides the rest:
         * through the destination's cloud band and down every level of its
         * still to the handback. Both directions, so the return to Echo Park
         * shows its still too -- it used to land in the innermost level and go
         * straight into the particles. A surface without the approach keeps
         * the old landing at the handback range.
         */
        const approachRangeM = destinationHero.rangeM ?? destination.parcelRangeM;
        const throughHero = typeof surface.approachThroughHero === "function";
        const flight = await surface.flyToSite({
          // Preserve the closed calibration fields (site/revision/sample width/
          // parcel range) instead of silently collapsing this to three numbers.
          anchor: destination,
          headingDeg,
          pitchDeg,
          rangeM: approachRangeM,
          throughHero,
        });
        requireCurrentTravel();
        if (flight?.status !== "complete") {
          throw new Error(`world flight did not complete: ${flight?.status || "missing"}`);
        }
        arrivalScale = aptTravelArrivalScale(flight);
        journeyDebug.flight = {
          status: flight?.status ?? "missing",
          arrivalBand: flight?.arrivalBand ?? "missing",
          rangeM: Number.isFinite(flight?.rangeM) ? Math.round(flight.rangeM) : null,
          heightResolved: flight?.heightResolved === true,
        };
        if (!arrivalScale) {
          throw new Error(`world flight returned unsafe arrival semantics: ${flight?.arrivalBand || "missing"}`);
        }
        /* Only onto a resolved parcel arrival: an unresolved height leaves the
         * camera at the flight's safe floor and never takes it down the still. */
        if (throughHero && arrivalScale === "parcel") {
          const approach = await surface.approachThroughHero({ rangeM: approachRangeM, headingDeg, pitchDeg });
          requireCurrentTravel();
          /* Product-safe: a status, a rounded range and the band timing. */
          journeyDebug.approach = {
            status: approach?.status ?? "missing",
            rangeM: Number.isFinite(approach?.rangeM) ? Math.round(approach.rangeM) : null,
            durationS: Number.isFinite(approach?.durationS) ? Math.round(approach.durationS * 100) / 100 : null,
          };
          /*
           * The flight has already adopted the destination anchor, so an
           * approach that stops short is still an arrival THERE -- the registry
           * follows the camera, and throwing here used to leave the world at
           * the destination with the selection and registration at the origin.
           * It is an exterior arrival: the particles never run from wherever
           * the camera was left. A viewer who took the camera (the wheel
           * cancels the glide) simply has it; anything else is held with a
           * retry that runs the approach again.
           */
          if (approach?.status !== "complete") {
            approachHeld = { status: approach?.status || "missing", headingDeg, pitchDeg, rangeM: approachRangeM };
          }
        }
      }

      const destinationPresented = await surface.waitForPresentation?.({
        timeoutMs: APT_HANDOFF_ENDPOINT_BUDGET_MS,
      });
      requireCurrentTravel();
      if (destinationPresented !== true) {
        setJourneyRecovery({ kind: "content-unavailable", message: "The destination view is not ready. The previous complete view is retained." });
        journeyRetryRef.current = () => travelToSite(siteId);
        journeyDebug.destinationPresentation = "held";
        return false;
      }

      // The registry follows the camera, so a failed journey does not leave the
      // app claiming to be somewhere it never went.
    captureCurrentWorkspace();
    registry.selectSite(siteId);
      /* Keep the origin scan active for the visible departure. Registering the
       * destination before the flight hid the origin immediately and allowed a
       * cancelled journey to leave the wrong registration active. The target
      * remains hidden here until its explicit readiness gate below succeeds. */
      surface.setDollhouseRegistration?.(target.dollhouseRegistration ?? null);
      /* A provider-height miss intentionally lands at the conservative flight
       * floor. Never label that view as parcel: hold an honest broader semantic
       * band until the provider-specific height resolver rebases the camera. */
      spatialCoordinatorRef.current?.syncWorldScale?.(arrivalScale);
      confirmedWorldAnchorRef.current = aptConfirmedWorldAnchor(syntheticSpatialQa);
      worldAnchorKeyRef.current = aptWorldAnchorKey(
        destination,
        target.dollhouseRegistration ?? null,
      );

      if (surface) {
        /* Post-flight readiness is a bounded refinement, never a second
         * journey. Imagery and dollhouse loading are independent of the two
         * height commits, so start them together. Roof then ground remains
         * sequential because the roof resolver advances the provider/anchor
         * generation the ground resolver captures. Every branch receives the
         * same shrinking 500ms deadline; renderer registration generations
         * prevent a late old-site result or dollhouse reveal from committing. */
        const readinessStartedAt = performance.now();
        const remainingTravelReadinessMs = () => Math.max(
          0,
          APT_WORLD_TRAVEL_READINESS_BUDGET_MS - (performance.now() - readinessStartedAt),
        );
        /* The destination click is explicit visibility authority. If Apartment
         * entry left a paid global tileset staged, adopt it for the destination
         * before resolving provider-scoped roof height. This runs after the
         * continuous flight, so it cannot stall the GTA camera move itself. */
        const imageryReadiness = requestWorldImageryAdoption("site-travel");
        const dollhouseReadiness = surface.waitForDollhouse?.({
          timeoutMs: remainingTravelReadinessMs(),
        }) ?? Promise.resolve({ status: "not-required" });
        const providerHeightReadiness = (async () => {
          await imageryReadiness;
          requireCurrentTravel();
          const surfaceHeight = await surface.resolveSurfaceHeight({
            timeoutMs: remainingTravelReadinessMs(),
          });
          requireCurrentTravel();
          const dollhouseGround = await (
            surface.resolveDollhouseGroundHeight?.({
              timeoutMs: remainingTravelReadinessMs(),
            }) ?? Promise.resolve({ status: "not-required" })
          );
          requireCurrentTravel();
          return { surfaceHeight, dollhouseGround };
        })();
        const readinessWindow = await aptSettleWithin(
          Promise.allSettled([
            imageryReadiness,
            dollhouseReadiness,
            providerHeightReadiness,
          ]),
          remainingTravelReadinessMs(),
        );
        requireCurrentTravel();
        journeyDebug.destinationReadiness = readinessWindow.status;
      }

      if (approachHeld) {
        journeyDebug.interior = { status: "held", reason: `approach-${approachHeld.status}` };
        journeyDebug.selectedScale = arrivalScale;
        if (approachHeld.status === "cancelled") return true;
        setJourneyRecovery({ kind: "content-unavailable", message: `The approach to ${target.shortName} stopped short. The outside view is kept.` });
        journeyRetryRef.current = async () => {
          const again = await worldSurfaceRef.current?.approachThroughHero?.({
            rangeM: approachHeld.rangeM, headingDeg: approachHeld.headingDeg, pitchDeg: approachHeld.pitchDeg,
          });
          if (!travelIsCurrent() || again?.status !== "complete" || target.sceneAvailable !== true) return;
          if (await prepareInteriorForSite(siteId, travelIsCurrent) && travelIsCurrent()) {
            await zoomRequestRef.current?.(1, { source: `retry-approach-${siteId}` });
          }
        };
        return false;
      }

      const interiorReady = target.sceneAvailable === true
        && await prepareInteriorForSite(siteId, travelIsCurrent);
      requireCurrentTravel();
      if (arrivalScale === "parcel" && interiorReady) {
        await engineRef.current?.pointsPromise;
        requireCurrentTravel();
        const arrival = await zoomRequestRef.current?.(1, { source: `travel-in-${siteId}` });
        requireCurrentTravel();
        journeyDebug.interior = arrival;
        if (arrival?.status !== "committed" || arrival?.scale !== "apartment") {
          throw new Error("destination interior is not ready");
        }
      } else if (target.sceneAvailable !== true) {
        journeyDebug.interior = { status: "not-available", reason: "scan-not-uploaded" };
        journeyDebug.selectedScale = arrivalScale;
        showToast(`${target.shortName} · exterior view. Interior scan not uploaded yet.`);
        return true;
      } else {
        setJourneyRecovery({ kind: "content-unavailable", message: `${target.shortName} interior is not ready. Import its home pack to continue.` });
        journeyRetryRef.current = () => zoomRequestRef.current?.(1, { source: "retry-destination-interior" });
        journeyDebug.selectedScale = spatialCoordinatorRef.current?.getState?.()?.committedScale ?? arrivalScale;
        journeyDebug.interior = { status: "held", reason: "content-unavailable" };
        return false;
      }
      journeyDebug.selectedScale = spatialCoordinatorRef.current?.getState?.()?.committedScale
        ?? arrivalScale;
      return true;
    } catch (error) {
      journeyDebug.error = String(error?.message || error);
      if (travelIsCurrent() && error?.name !== "AbortError") {
        showToast("could not travel to that home", { tone: "error" });
      }
      return false;
    } finally {
      journeyDebug.finishedAt = Math.round(performance.now());
      if (spatialTravelIntentRef.current === travelIntent) {
        travellingRef.current = false;
        setTravelling(false);
      }
    }
  }, [cancelSpatialTravel, ensureSpatialCoordinator, parkSpatialControlFocus, prepareInteriorForSite, requestWorldImageryAdoption,
    showToast, spatialScale, syntheticSpatialQa, travelling]);

  useEffect(() => {
    if (!open) return undefined;
    const tick = () => {
      const next = worldSurfaceRef.current?.snapshot?.()?.recovery || null;
      setSurfaceRecovery((previous) => JSON.stringify(previous) === JSON.stringify(next) ? previous : next);
    };
    tick();
    const timer = setInterval(tick, 250);
    const packs = window.HomeContentPacks;
    const refresh = () => { setContentPacks(packs?.list?.() || []); setHomeSites(window.HomeSpatialViewport?.getSites?.() || []); };
    void Promise.resolve(packs?.ready).then(refresh);
    const unsubscribe = packs?.subscribe?.(refresh);
    return () => { clearInterval(timer); unsubscribe?.(); };
  }, [open]);

  const returnFromRecovery = useCallback(async () => {
    await cancelSpatialTravel();
    spatialLifecycleRevisionRef.current += 1;
    const surface = worldSurfaceRef.current;
    surface?.setRunning?.(false);
    surface?.dismissRecovery?.();
    // Recovery returns to the actually resident scan, never a label claiming
    // that another home's pixels belong to the selected missing interior.
    const residentSiteId = loadedInteriorSiteRef.current;
    if (residentSiteId) window.HomeSpatialViewport?.selectSite?.(residentSiteId);
    spatialCoordinatorRef.current?.dispose?.();
    spatialCoordinatorRef.current = null;
    spatialCoordinatorPromiseRef.current = null;
    worldScaleObserverCleanupRef.current?.();
    worldScaleObserverCleanupRef.current = null;
    worldEntryPrewarmRef.current?.dispose?.();
    worldEntryPrewarmRef.current = null;
    worldEntryPrewarmPromiseRef.current = null;
    surface?.dispose?.();
    worldSurfaceRef.current = null;
    worldSurfacePromiseRef.current = null;
    worldAnchorKeyRef.current = "";
    confirmedWorldAnchorRef.current = aptConfirmedWorldAnchor(syntheticSpatialQa);
    setSpatialScale("apartment"); setSpatialTransition("idle");
    setSpatialTransitionDirection(null);
    hostRef.current?.style.setProperty("opacity", "1");
    worldHostRef.current?.style.setProperty("opacity", "0");
    engineRef.current?.setRunning(true);
    setJourneyRecovery(null); setSurfaceRecovery(null);
  }, [cancelSpatialTravel, syntheticSpatialQa]);

  const retrySpatialRecovery = useCallback(async () => {
    const surface = worldSurfaceRef.current;
    const recovery = surface?.snapshot?.()?.recovery;
    if (!recovery) {
      setJourneyRecovery(null);
      await journeyRetryRef.current?.();
      return;
    }
    await cancelSpatialTravel();
    const recoveryLifecycle = spatialLifecycleRevisionRef.current;
    const recoveryIntent = spatialTravelIntentRef.current;
    const recoveryIsCurrent = () => recoveryLifecycle === spatialLifecycleRevisionRef.current
      && recoveryIntent === spatialTravelIntentRef.current;
    const action = surface.retryRecovery?.();
    if (action?.action === "recreate-surface") {
      const restoreScale = spatialCoordinatorRef.current?.getState?.()?.committedScale || "parcel";
      setJourneyRecovery({ kind: "render-error", message: "Restoring the outside view…" });
      spatialCoordinatorRef.current?.dispose?.();
      spatialCoordinatorRef.current = null; spatialCoordinatorPromiseRef.current = null;
      worldScaleObserverCleanupRef.current?.(); worldScaleObserverCleanupRef.current = null;
      worldEntryPrewarmRef.current?.dispose?.(); worldEntryPrewarmRef.current = null;
      worldEntryPrewarmPromiseRef.current = null;
      surface.dispose?.(); worldSurfaceRef.current = null; worldSurfacePromiseRef.current = null;
      worldAnchorKeyRef.current = "";
      if (restoreScale === "apartment") {
        setJourneyRecovery(null); setSurfaceRecovery(null);
        return;
      }
      restoredWorldScaleRef.current = restoreScale === "apartment" ? "parcel" : restoreScale;
      try {
        const anchor = aptConfirmedWorldAnchor(syntheticSpatialQa);
        await ensureSpatialCoordinator(anchor);
        if (!recoveryIsCurrent()) return;
        const next = worldSurfaceRef.current;
        next.setAnchor(anchor);
        next.setHeroStills?.(anchor?.heroStills || [], anchor);
        next.setRunning(true);
        await next.navigate({ intentId: `recovery-${++spatialIntentRef.current}`,
          band: restoredWorldScaleRef.current || restoreScale, motion: "cut", durationMs: 0 });
        if (!recoveryIsCurrent()) return;
        if (restoreScale === "parcel" && anchor?.heroStills?.length) next.setRegisteredPose?.({
          headingDeg: anchor.heroStills[0].headingDeg, pitchDeg: anchor.heroStills[0].pitchDeg,
          rangeM: next.heroEntryRange?.(anchor) ?? anchor.parcelRangeM,
        });
        const ready = await next.waitForPresentation?.({ timeoutMs: APT_HANDOFF_ENDPOINT_BUDGET_MS });
        if (!recoveryIsCurrent()) return;
        if (ready) setJourneyRecovery(null);
        else setJourneyRecovery({ kind: "content-unavailable", message: "The outside view is not ready yet." });
      } catch (_) {
        if (!recoveryIsCurrent()) return;
        setJourneyRecovery({ kind: "render-error", message: "The outside view could not be restored." });
        journeyRetryRef.current = async () => {
          await returnFromRecovery();
          await zoomRequestRef.current?.(-1, { source: "retry-restored-world" });
        };
      }
    } else if (action?.action === "retry-provider") {
      await requestWorldImageryAdoption("recovery-retry");
    }
  }, [cancelSpatialTravel, ensureSpatialCoordinator, requestWorldImageryAdoption, returnFromRecovery, syntheticSpatialQa]);

  const showWorldOverview = useCallback(async () => {
    await cancelSpatialTravel();
    const surface = worldSurfaceRef.current;
    const intent = spatialTravelIntentRef.current;
    const controller = new AbortController();
    worldOverviewAbortRef.current = controller;
    const ready = await surface?.showWorldOverview?.({ signal: controller.signal });
    if (controller.signal.aborted) return;
    if (worldOverviewAbortRef.current === controller) worldOverviewAbortRef.current = null;
    if (intent !== spatialTravelIntentRef.current || surface !== worldSurfaceRef.current) return;
    if (ready) {
      spatialCoordinatorRef.current?.syncWorldScale?.("planet");
      setWorldOverviewActive(true);
      setJourneyRecovery(null); setSurfaceRecovery(null);
      showToast("World overview · live local imagery unavailable");
    }
  }, [cancelSpatialTravel, showToast]);

  const changeContentPack = useCallback(async (revision = null) => {
    if (contentPackBusy) return;
    setContentPackBusy(true);
    const lifecycle = spatialLifecycleRevisionRef.current;
    let contentIntent = null;
    const isCurrent = () => lifecycle === spatialLifecycleRevisionRef.current
      && (contentIntent === null || contentIntent === spatialTravelIntentRef.current);
    const pendingContentRetry = journeyRecovery?.kind === "content-unavailable" ? journeyRetryRef.current : null;
    try {
      const pendingCancellation = cancelSpatialTravel();
      contentIntent = spatialTravelIntentRef.current;
      await pendingCancellation;
      if (!isCurrent()) return;
      const packs = window.HomeContentPacks;
      const previousIndex = JSON.stringify(packs.list());
      if (revision) await packs.activate(selectedSiteId, revision);
      else await packs.pickAndImport();
      if (!isCurrent()) return;
      if (!revision && JSON.stringify(packs.list()) === previousIndex) return;
      showToast("home pack verified and available");
      if (pendingContentRetry) {
        setJourneyRecovery(null);
        await pendingContentRetry();
        return;
      }
      const siteId = window.HomeSpatialViewport?.getSelectedSiteId?.();
      if (spatialScale !== "apartment") {
        if (await prepareInteriorForSite(siteId, isCurrent)) {
          setJourneyRecovery(null);
          await zoomRequestRef.current?.(1, { source: "content-pack-ready" });
        }
      } else await interiorSwitchRequestRef.current?.(siteId, { force: true });
    } catch (_) { showToast("home pack could not be imported; the previous version is still available", { tone: "error" }); }
    finally { setContentPackBusy(false); }
  }, [cancelSpatialTravel, contentPackBusy, journeyRecovery, prepareInteriorForSite, selectedSiteId, showToast, spatialScale]);

  const isControlCurrent = useMemo(() => {
    const generation = controlConnectionRef.current.generation;
    const engine = engineRef.current;
    const authority = workspaceAuthorityRef.current.key;
    const client = window.__hav_haClient;
    const intent = spatialTravelIntentRef.current;
    return () => viewOpenRef.current && !simActive && !rehearsalIsolated
      && controlConnectionRef.current.connection === "online" && controlConnectionRef.current.generation === generation
      && window.__hav_haClient === client && engineRef.current === engine && engine?.siteId === "echo"
      && spatialTravelIntentRef.current === intent && workspaceAuthorityRef.current.key === authority
      && modelScopeRef.current?.authorityKey === authority && modelSiteRef.current === "echo"
      && window.HomeApartmentSiteSession.canControlEcho({ selectedSiteId: window.HomeSpatialViewport?.getSelectedSiteId?.(),
        sceneSiteId: engine?.siteId, transitioning: (interiorSessionRef.current?.readback?.().phase ?? "ready") !== "ready" })
      && (window.HomeSpatialViewport?.getSite?.("echo")?.contentRevision || "legacy") === (engine?.contentRevision || "legacy");
  }, [connection, endpoint, token, selectedSiteId, selectedContentRevision, phase, simActive, rehearsalIsolated, serviceEpoch,
    spatialTravelIntentRef.current, engineRef.current, apartmentAuthorityKey]);

  const callSvc = useCallback(async (domain, service, data) => {
    // Resolves true only when Home Assistant accepted the call; guards are unchanged.
    if (rehearsalIsolated || window.HomeMapRehearsalOfflineState?.active) return false;
    if (!window.HomeApartmentSiteSession.canControlEcho({
        sceneSiteId: engineRef.current?.siteId,
        selectedSiteId: window.HomeSpatialViewport?.getSelectedSiteId?.(),
        transitioning: (interiorSessionRef.current?.readback?.().phase ?? "ready") !== "ready"
          || (window.HomeSpatialViewport?.getSite?.("echo")?.contentRevision || "legacy") !== (engineRef.current?.contentRevision || "legacy") })) {
      showToast("Home controls are not configured for this interior"); return false;
    }
    if (modelScopeRef.current?.authorityKey !== workspaceAuthorityRef.current.key) return false;
    const client = window.__hav_haClient;
    if (!client || simActive) { showToast("sim mode - controls disabled"); return false; }
    const engine = engineRef.current;
    const operation = spatialTravelIntentRef.current;
    const authority = workspaceAuthorityRef.current.key;
    const current = () => engineRef.current === engine && spatialTravelIntentRef.current === operation
      && workspaceAuthorityRef.current.key === authority
      && window.HomeSpatialViewport?.getSelectedSiteId?.() === "echo" && isControlCurrent();
    const entityId = data?.entity_id;
    const prev = entityId ? statesRef.current[entityId] : null;
    const optimistic = aptOptimisticServiceState(prev, domain, service);
    const expected = aptExpectedServiceState(domain, service, optimistic);
    const dev = entityId ? (model.devices || []).find((d) => d.ha_entity_id === entityId) : null;
    try {
      await window.HomeApartmentData.callService(client, domain, service, data, { siteId: "echo", isCurrent: () => current() && isControlCurrent() });
      if (!current()) return true;
    if (entityId && optimistic) {
      statesRef.current[entityId] = optimistic;
      if (dev) engineRef.current?.overlay.setDeviceState(dev.id, optimistic);
      setServicePulse((n) => n + 1);
    }

      if (entityId && typeof window.HomeApartmentData.readStates === "function") {
        try {
          let verified = null;
          for (const delayMs of [350, 900, 1600]) {
            await aptDelay(delayMs);
            if (!current()) return true;
            const states = await window.HomeApartmentData.readStates(client, [entityId], { siteId: "echo" });
            if (!current()) return true;
            verified = states[entityId] || null;
            if (verified) {
              statesRef.current[entityId] = verified;
              if (dev) engine.overlay.setDeviceState(dev.id, verified);
              setServicePulse((n) => n + 1);
            }
            if (verified && aptStateMatchesExpected(verified, expected)) break;
          }
          if (verified && expected && !aptStateMatchesExpected(verified, expected)) {
            showToast(`${dev?.name || entityId} is still ${verified.state} in Home Assistant`, { tone: "error" });
          }
        } catch (e) {
          console.warn("[apartment] service state readback failed", e);
        }
      }
      return true;
    } catch (e) {
      if (!current()) return false;
      showToast(e?.message?.includes("Travel Mode") ? e.message : `${entityId || domain} didn't respond`, { tone: "error" });
      return false;
    }
  }, [simActive, showToast, model.devices, isControlCurrent]);

  const compareLiveModel = useCallback(async () => {
    if (!apartmentDeviceControlsEnabled) return { ok: false, site_read_only: true };
    const scope = aptInteriorWorkspaceScope(selectedSiteId, workspaceAuthorityRef.current.key);
    const current = () => window.HomeSpatialViewport?.getSelectedSiteId?.() === scope.siteId
      && workspaceAuthorityRef.current.key === scope.authorityKey
      && (window.HomeSpatialViewport?.getSite?.(scope.siteId)?.contentRevision || null) === scope.contentRevision;
    if (simActive) return { ok: false, sim: true };
    if (connection !== "online" || !endpoint || !token) {
      const detail = "connect Home Assistant with a valid long-lived token first";
      setLiveReview({ state: "error", live: null, detail });
      setSaveStatus({ state: "offline", detail: `local draft preserved · ${detail}` });
      showToast("connect Home Assistant before comparing the live model");
      return { ok: false, credentials_required: true };
    }
    setLiveReview({ state: "loading", live: null, detail: "reading authoritative Apartment model" });
    const result = await window.HomeApartmentData.getAuthoritativeModel({ endpoint, token, ...scope });
    if (!current()) return { ok: false, superseded: true };
    if (!result.ok) {
      const detail = result.status === 401
        ? "Home Assistant rejected the token"
        : result.error || "live Apartment model unavailable";
      setLiveReview({ state: "error", live: null, detail });
      setSaveStatus({ state: "offline", detail: `local draft preserved · ${detail}` });
      showToast(`live comparison failed · ${detail}`, { tone: "error" });
      return result;
    }
    const comparison = window.HomeApartmentData.compareApartmentModels(model, result.model);
    setLiveReview({ state: "ready", live: result.model, detail: "read-only comparison complete" });
    if (comparison.sameRevision) {
      setSaveStatus({ state: "reviewed", detail: `live revision ${comparison.liveRevision} reviewed · ready for explicit publish` });
      showToast(`live revision ${comparison.liveRevision} reviewed · publish is now available`);
    } else {
      setSaveStatus({ state: "conflict", detail: `draft revision ${comparison.localRevision} · live revision ${comparison.liveRevision} · no write performed` });
      showToast("revision conflict · local draft preserved; nothing was written", { tone: "error" });
    }
    return { ...result, comparison };
  }, [simActive, connection, endpoint, token, model, showToast, apartmentDeviceControlsEnabled, selectedSiteId, selectedContentRevision]);

  const saveModel = useCallback(async ({ reviewedDraft = false } = {}) => {
    if (rehearsalIsolated || window.HomeMapRehearsalOfflineState?.active) return { ok: false, rehearsal: true };
    if (!apartmentPlacementControlsEnabled) return { ok: false, site_read_only: true };
    const scope = aptInteriorWorkspaceScope(selectedSiteId, workspaceAuthorityRef.current.key);
    const operation = spatialTravelIntentRef.current;
    const currentSource = window.HomeApartmentData.modelSourceMeta?.(model) || {};
    if (currentSource.kind === "local_draft" && connection === "online" && !reviewedDraft) {
      return compareLiveModel();
    }
    if (currentSource.kind === "local_draft" && reviewedDraft) {
      if (liveReview.state !== "ready" || !liveReview.live) return compareLiveModel();
      const comparison = window.HomeApartmentData.compareApartmentModels(model, liveReview.live);
      if (!comparison.sameRevision) {
        setSaveStatus({ state: "conflict", detail: `draft revision ${comparison.localRevision} · live revision ${comparison.liveRevision} · no write performed` });
        showToast("live revision changed · compare again before publishing", { tone: "error" });
        return { ok: false, conflict: true, stored: liveReview.live };
      }
    }
    setSaving(true);
    setSaveStatus({ state: "saving", detail: "writing authoritative Apartment model" });
    const res = await window.HomeApartmentData.saveModel(model, { endpoint, token, sim: simActive, ...scope,
      equipmentRegistration: scope.siteId === "victoria" && window.HG_WEB_MODE === true });
    // A save may complete after the owner switches homes. Advance only the
    // parked copy of the exact submitted draft; never erase newer edits.
    if (res.ok && res.model && scope.siteId === "victoria") {
      const parked = window.HomeApartmentData.restoreWorkspace?.(scope);
      const registration = window.HomeEquipmentRegistration;
      if (parked?.model?._equipment_registration && registration
          && registration.canonicalJson(registration.toDocument(parked.model)) === registration.canonicalJson(registration.toDocument(model))) {
        window.HomeApartmentData.captureWorkspace?.(scope, { model: res.model, dirty: false, view: parked.view });
      }
    }
    if (spatialTravelIntentRef.current !== operation || window.HomeSpatialViewport?.getSelectedSiteId?.() !== scope.siteId
        || workspaceAuthorityRef.current.key !== scope.authorityKey
        || (window.HomeSpatialViewport?.getSite?.(scope.siteId)?.contentRevision || null) !== scope.contentRevision
        || (window.HomeSpatialViewport?.getSite?.(scope.siteId)?.sceneRegistration?.version ?? 0) !== scope.registrationVersion
        || (window.HomeContentPacks?.get?.(scope.siteId, scope.contentRevision)?.manifest?.assets?.["frame.json"]?.sha256 || null) !== scope.frameDigest) return res;
    setSaving(false);
    if (res.ok) {
      setModel((m) => res.model || ({
        ...m, revision: res.revision, updated_at: res.updated_at,
        source_kind: "live_ha_model",
        source_detail: { endpoint, revision: res.revision },
      }));
      setLiveReview({ state: "idle", live: null, detail: "" });
      setSaveStatus({ state: "saved", detail: `revision ${res.revision}` });
      showToast(`saved · revision ${res.revision}`);
    } else if (res.conflict) {
      setSaveStatus({ state: "conflict", detail: `server revision ${res.stored?.revision ?? "changed"} · local draft preserved` });
      showToast("model changed elsewhere · local draft preserved; nothing was overwritten", { tone: "error" });
      if (res.stored && scope.siteId === "echo") {
        setLiveReview({
          state: "ready",
          live: { ...res.stored, source_kind: "live_ha_model",
            source_detail: { endpoint, revision: res.stored?.revision, read_only: true } },
          detail: "server conflict copy loaded for read-only comparison",
        });
      }
    } else if (res.validation) {
      setSaveStatus({ state: "validation", detail: res.error || "engineered fixture metadata is incomplete" });
      showToast(`cannot save · ${res.error}`, { tone: "error" });
    } else if (res.offline || res.sim) {
      setSaveStatus(res.sim
        ? { state: "simulation", detail: "isolated · live model untouched" }
        : { state: "offline", detail: scope.siteId === "victoria" ? "session edits preserved · private Home server unavailable" : "local draft · Home Assistant unavailable" });
      showToast("offline — kept as local draft", { tone: "error" });
    } else {
      setSaveStatus({ state: "offline", detail: res.error || "save failed · local draft preserved" });
      showToast(`save failed — ${res.error}`, { tone: "error" });
    }
    return res;
  }, [model, endpoint, token, simActive, connection, liveReview, compareLiveModel, showToast, apartmentPlacementControlsEnabled, selectedSiteId, selectedContentRevision]);

  const revealCameraFeedWhenReady = useCallback((dev, seq) => {
    const started = performance.now();
    const tick = () => {
      if (seq !== flySeqRef.current) return;
      const rig = engineRef.current?.rig;
      const settled = rig?.cameraPoseSettled?.() || (!rig?.cameraPoseSettled && rig?.inCameraPose?.());
      if (settled) {
        flyTimerRef.current = null;
        setCameraPoseReady(true);
        if (!simActive && aptCameraCanFeed(dev)) {
          setLiveFeedStatus((current) => aptLiveFeedSettled(current) ? current : "connecting");
          setLiveOn(true);
        } else {
          setLiveFeedStatus("idle");
        }
        return;
      }
      if (performance.now() - started > 5200) {
        flyTimerRef.current = null;
        if (!simActive && aptCameraCanFeed(dev)) {
          setLiveFeedStatus((current) => aptLiveFeedSettled(current) ? current : "connecting");
          setLiveOn(true);
          showToast("camera pose still loading - feed is live");
        } else {
          setLiveFeedStatus("idle");
        }
        return;
      }
      flyTimerRef.current = setTimeout(tick, 120);
    };
    flyTimerRef.current = setTimeout(tick, 120);
  }, [showToast, simActive]);

  const flyToDeviceView = useCallback((dev) => {
    if (!dev || !apartmentSceneControlsEnabled) return false;
    if (!apartmentDeviceControlsEnabled) return false;
    const eng = engineRef.current;
    if (!eng) return false;
    if (!aptCameraViewAvailability(eng.siteId, [dev]).available) return false;
    const seq = ++flySeqRef.current;
    if (flyTimerRef.current) { clearTimeout(flyTimerRef.current); flyTimerRef.current = null; }
    const alignment = aptCameraAlignment(dev);
    const calib = alignment.exact;
    const hasFeed = !!(!simActive && aptCameraCanFeed(dev));
    setLiveCam(dev);
    setLiveOn(false);
    setLiveFeedStatus("flying");
    setCameraPoseReady(false);
    setCalibCam(null);
    (async () => {
      let meshReady = false;
      try {
        // Camera snaps always use the textured mesh behind the live feed.
        // Keep the feed hidden during the camera flight so the user sees the
        // 3D swoop into the calibrated pose; stream signing still preloads.
        if (preSnapModeRef.current == null) preSnapModeRef.current = eng.modes.mode;
        await eng.modes.setMode("mesh", { duration: 0 });
        if (seq !== flySeqRef.current) return;
        meshReady = true;
        setMode("mesh");
        setModeError(null);
      } catch (e) {
        if (seq !== flySeqRef.current) return;
        setModeError({
          mode: "mesh",
          message: "mesh unavailable",
          detail: `${e?.message || String(e || "asset load failed")} - check mesh.glb / collision.glb`,
        });
        if (!hasFeed) showToast("camera snap needs mesh - check mesh.glb asset", { tone: "error" });
        else showToast("mesh unavailable - flying with current scan", { tone: "error" });
      }
      try {
        if (seq !== flySeqRef.current) return;
        eng.flyToDevice(dev, {
          dur: mobile ? 1150 : 980,
          fovScale: calib && !mobile ? 1 / 0.74 : 1,
          projectionFrame: calib && mobile ? aptProjectionFrame(viewport, dev) : null,
        });
        revealCameraFeedWhenReady(dev, seq);
      } catch (e) {
        if (seq !== flySeqRef.current) return;
        if (meshReady) setModeError({
          mode: "mesh",
          message: "mesh unavailable",
          detail: `${e?.message || String(e || "camera pose failed")} - check camera calibration`,
        });
        if (!hasFeed) showToast("camera snap needs mesh - check mesh.glb asset", { tone: "error" });
        else {
          setCameraPoseReady(true);
          setLiveFeedStatus((current) => aptLiveFeedSettled(current) ? current : "connecting");
          setLiveOn(true);
          showToast("camera pose failed - showing camera feed only", { tone: "error" });
        }
      }
    })();
    return true;
  }, [apartmentSceneControlsEnabled, mobile, viewport, simActive,
    revealCameraFeedWhenReady, showToast]);

  useEffect(() => {
    if (!open || typeof window === "undefined") return undefined;
    const api = {
      snapshot: () => ({
        phase,
        mode,
        liveOn,
        liveFeedStatus,
        liveCam: liveCam?.id || liveCam?.name || null,
        cameraAlignment: liveCam ? aptCameraAlignment(liveCam) : null,
        cameraPoseFlying: !!engineRef.current?.rig?.cameraPoseFlying?.(),
        cameraPoseReady,
        cameraFrame: mobile && liveCam ? (() => {
          const frame = aptMobileCameraFrame(viewport, liveCam);
          return { left: frame.left, top: frame.top, width: frame.width, height: frame.height };
        })() : null,
        mobile,
        viewport,
        devices: (model.devices || []).length,
        virtualFixtures: engineRef.current?.overlay?.virtualFixturesReadback?.() || null,
        fit: engineRef.current?.debugFit?.() || null,
        modes: engineRef.current?.modes?.debugInfo?.() || null,
        spatialScale,
        spatialTransition,
        world: worldSurfaceRef.current?.snapshot?.() || null,
      }),
      modelSnapshot: () => JSON.parse(JSON.stringify(model)),
      apartmentFit: () => engineRef.current?.debugFit?.() || null,
      setMode: pickMode,
      zoomIn: () => zoomApartment(1),
      zoomOut: () => zoomApartment(-1),
      /*
       * The world's product-safe state, and the one measurement registration
       * cannot be corrected without.
       *
       * Everything reachable here is either a phase, a provider id or an offset
       * in metres relative to the anchor. No latitude, longitude, address or
       * tile URL crosses this boundary, which is the same rule every other
       * diagnostic in this file follows.
       */
      world: () => {
        const surface = worldSurfaceRef.current;
        if (!surface) return null;
        return {
          snapshot: surface.snapshot(),
          camera: surface.cameraState?.() ?? null,
          handoff: window.__aptHandoffDebug ?? null,
          measureGroundOffset: (xFraction, yFraction) =>
            surface.measureGroundOffset?.({ xFraction, yFraction }) ?? null,
          /* The world-view options, so a switch can be PROVED to change what
           * renders rather than assumed to. */
          viewSelection: () => ({ ...worldViewSelection }),
          setViewOption: (groupKey, presetId) => { setWorldViewOption(groupKey, presetId); return true; },
          setCloudDebug: (mode) => surface.setCloudDebug?.(mode) ?? null,
          /*
           * Capture the current frame as a hero still and arm it. Returns
           * metadata only -- never the image itself, which is a picture of the
           * user's home and has no business in a diagnostic string.
           */
          armHeroStill: async (options) => {
            const captured = await surface.captureHeroStill?.(options);
            /* Say WHICH half failed. A bare null sent me guessing at a capture
             * bug that turned out to be a pose refusal, and the two have
             * completely different fixes. */
            if (!captured) return { ok: false, reason: 'capture-returned-null' };
            const adopted = surface.setHeroStill?.(captured);
            if (!adopted) {
              return {
                ok: false,
                reason: 'pose-refused',
                stillPose: {
                  siteId: captured.siteId,
                  registrationVersion: captured.registrationVersion,
                  headingDeg: captured.headingDeg,
                  pitchDeg: captured.pitchDeg,
                },
              };
            }
            setHeroStill(captured);
            return {
              widthPx: captured.widthPx,
              heightPx: captured.heightPx,
              rangeM: captured.rangeM,
              siteId: captured.siteId,
              registrationVersion: captured.registrationVersion,
            };
          },
          /* Arm an externally produced image at the current pose, which is how
           * an enhanced still gets in. Same pose check as a captured one. */
          useHeroStill: (url, options = {}) => {
            if (typeof url !== 'string' || !url) return null;
            const camera = surface.cameraState?.();
            const still = {
              url,
              widthPx: options.widthPx ?? null,
              heightPx: options.heightPx ?? null,
              rangeM: options.rangeM ?? camera?.rangeM ?? null,
              headingDeg: options.headingDeg ?? camera?.headingDeg ?? null,
              pitchDeg: options.pitchDeg ?? camera?.pitchDeg ?? null,
              siteId: options.siteId ?? null,
              registrationVersion: options.registrationVersion ?? 0,
              /*
               * The measured correction for THIS generated image, or null for
               * an image that needs none. A generative rebuild is not a
               * resampler: it returns a frame that is a fraction of a percent
               * off the one it was given, uniformly. Measured offline by phase
               * correlation against the source capture and recorded here, so
               * the number that gets applied is the number that was measured
               * and can be re-derived. hero-still.mjs refuses an implausible
               * one rather than moving the house.
               */
              registration: options.registration ?? null,
            };
            const adopted = surface.setHeroStill?.(still);
            if (!adopted) return null;
            setHeroStill(still);
            return { rangeM: still.rangeM };
          },
          /*
           * Does the enhanced still still depict the same house?
           *
           * The metric is structural on purpose. An enhancement is SUPPOSED to
           * change brightness, contrast and micro-detail, so a raw pixel
           * difference would flag success as failure. Normalising each image to
           * zero mean and unit variance throws away exactly the axis the model
           * is allowed to move and leaves the one it is not: where things ARE.
           *
           * Downsampled first, because at 64x64 a repainted roof tile averages
           * away while a roof that moved does not.
           *
           * It cannot see a hallucinated window that happens to land where a
           * dark patch already was. It is a screen, not a proof; the eye is
           * still the acceptance test.
           */
          compareStills: async (urlA, urlB, { size = 64, block = 8 } = {}) => {
            const load = (src) => new Promise((resolve, reject) => {
              const img = new Image();
              img.onload = () => resolve(img);
              img.onerror = () => reject(new Error('could not load image'));
              img.src = src;
            });
            const luminance = (img) => {
              const canvas = document.createElement('canvas');
              canvas.width = size; canvas.height = size;
              const ctx = canvas.getContext('2d');
              ctx.drawImage(img, 0, 0, size, size);
              const data = ctx.getImageData(0, 0, size, size).data;
              const out = new Float64Array(size * size);
              for (let i = 0; i < out.length; i += 1) {
                out[i] = (0.2126 * data[i * 4]) + (0.7152 * data[i * 4 + 1]) + (0.0722 * data[i * 4 + 2]);
              }
              return out;
            };
            const normalise = (v) => {
              let mean = 0;
              for (let i = 0; i < v.length; i += 1) mean += v[i];
              mean /= v.length;
              let variance = 0;
              for (let i = 0; i < v.length; i += 1) variance += (v[i] - mean) ** 2;
              const sd = Math.sqrt(variance / v.length) || 1;
              const out = new Float64Array(v.length);
              for (let i = 0; i < v.length; i += 1) out[i] = (v[i] - mean) / sd;
              return out;
            };
            const [imgA, imgB] = await Promise.all([load(urlA), load(urlB)]);
            const a = normalise(luminance(imgA));
            const b = normalise(luminance(imgB));
            let total = 0;
            for (let i = 0; i < a.length; i += 1) total += Math.abs(a[i] - b[i]);
            const meanAbs = total / a.length;
            let worst = { value: 0, xFraction: 0, yFraction: 0 };
            for (let by = 0; by + block <= size; by += block) {
              for (let bx = 0; bx + block <= size; bx += block) {
                let sum = 0;
                for (let y = 0; y < block; y += 1) {
                  for (let x = 0; x < block; x += 1) {
                    sum += Math.abs(a[(by + y) * size + bx + x] - b[(by + y) * size + bx + x]);
                  }
                }
                const value = sum / (block * block);
                if (value > worst.value) {
                  worst = { value, xFraction: (bx + block / 2) / size, yFraction: (by + block / 2) / size };
                }
              }
            }
            return {
              sourcePx: { width: imgA.naturalWidth, height: imgA.naturalHeight },
              enhancedPx: { width: imgB.naturalWidth, height: imgB.naturalHeight },
              aspectMatches: Math.abs((imgA.naturalWidth / imgA.naturalHeight)
                - (imgB.naturalWidth / imgB.naturalHeight)) < 0.01,
              structuralDrift: Number(meanAbs.toFixed(4)),
              worstBlock: {
                drift: Number(worst.value.toFixed(4)),
                xFraction: Number(worst.xFraction.toFixed(3)),
                yFraction: Number(worst.yFraction.toFixed(3)),
              },
            };
          },
          /*
           * Put the armed still on disk, because the enhancement step runs in a
           * terminal and a data URL living in the page is no use to it.
           *
           * An anchor click rather than anything cleverer: the image is already
           * a data URL, so this needs no network round trip and no dev-only
           * upload route, which would be a new authority for a debugging
           * convenience.
           */
          downloadHeroStill: (filename) => {
            const still = heroStillRef.current;
            if (!still?.url) return { ok: false, reason: 'nothing armed' };
            const name = typeof filename === 'string' && filename
              ? filename
              : `hero-still-${still.siteId || 'home'}-${still.widthPx}x${still.heightPx}`
                + (still.format === 'jpeg' ? '.jpg' : '.png');
            try {
              const link = document.createElement('a');
              link.href = still.url;
              link.download = name;
              document.body.appendChild(link);
              link.click();
              link.remove();
            } catch (error) {
              return { ok: false, reason: String(error?.message || error) };
            }
            return { ok: true, filename: name, widthPx: still.widthPx, heightPx: still.heightPx };
          },
          clearHeroStill: () => {
            surface.setHeroStill?.(null);
            setHeroStill(null);
            return true;
          },
          /* Per-rendered-frame brightness, for answering "is it flickering"
           * with pixels instead of a proxy. */
          /* Sweep where the descent waits for the ground. */
          setArrivalHold: (options) => surface.setArrivalHold?.(options) ?? null,
          /* Resolution and multisampling, for measuring the fill-rate ceiling. */
          setRenderTuning: (options) => surface.setRenderTuning?.(options) ?? null,
          startFrameSampler: (options) => surface.startFrameSampler?.(options) ?? false,
          stopFrameSampler: () => surface.stopFrameSampler?.() ?? null,
          /* What an option costs, and what the depth decode stands on. Frame
           * counts, milliseconds, byte totals and frustum planes only -- the
           * same redaction rule as every other diagnostic here. */
          renderDiagnostics: (options) => {
            const base = surface.renderDiagnostics?.(options) ?? null;
            if (!base) return null;
            /* Provider bytes come from a separate probe: the surface is barred
             * from touching the network, and a diagnostic does not get to be the
             * exception that weakens the rule. */
            if (options?.reset) providerTrafficRef.current?.reset?.();
            return { ...base, provider: providerTrafficRef.current?.read?.() ?? null };
          },
          installTrafficProbe: async () => {
            if (providerTrafficRef.current) return true;
            if (providerTrafficPromiseRef.current) {
              return Boolean(await providerTrafficPromiseRef.current);
            }
            const lifecycleRevision = spatialLifecycleRevisionRef.current;
            const pending = (async () => {
              const probe = await engineRef.current?.installProviderTrafficProbe?.(window);
              /* Closing the Apartment while the provider module is importing
               * must not let its late result re-wrap fetch/XHR after cleanup. */
              if (spatialLifecycleRevisionRef.current !== lifecycleRevision) {
                probe?.dispose?.();
                return null;
              }
              providerTrafficRef.current = probe ?? null;
              return providerTrafficRef.current;
            })();
            providerTrafficPromiseRef.current = pending;
            try {
              return Boolean(await pending);
            } finally {
              if (providerTrafficPromiseRef.current === pending) {
                providerTrafficPromiseRef.current = null;
              }
            }
          },
        };
      },
      flyFirstCamera: () => {
        const devices = model.devices || [];
        const dev = devices.find((d) => d.type === "camera") || devices.find((d) => aptCameraCanFeed(d));
        return dev ? flyToDeviceView(dev) : false;
      },
      flyFirstCalibratedCamera: () => {
        const dev = (model.devices || []).find((d) =>
          (d.type === "camera" || aptCameraCanFeed(d)) &&
          d.camera?.intrinsics &&
          d.camera?.extrinsics
        );
        return dev ? flyToDeviceView(dev) : false;
      },
      openFirstControllableCard: () => {
        const dev = (model.devices || []).find((d) =>
          d?.ha_entity_id && (d.type === "light" || d.ha_entity_id.startsWith("light.") || d.ha_entity_id.startsWith("switch."))
        );
        if (!dev) return null;
        if (!statesRef.current[dev.ha_entity_id]) {
          statesRef.current[dev.ha_entity_id] = { entity_id: dev.ha_entity_id, state: "off", attributes: {} };
        }
        setCardId(dev.id);
        setServicePulse((n) => n + 1);
        return { id: dev.id, entity_id: dev.ha_entity_id, name: dev.name };
      },
    };
    window.__havApartmentDebug = api;
    return () => {
      if (window.__havApartmentDebug === api) delete window.__havApartmentDebug;
    };
  }, [open, phase, mode, liveOn, liveFeedStatus, liveCam, cameraPoseReady, mobile, viewport, model, pickMode, zoomApartment, flyToDeviceView, spatialScale, spatialTransition]);

  const liveHaEntity = aptCameraEntity(liveCam);
  const liveHaBase = endpoint || aptServiceBase("homeAssistantUrl", "HG_DEFAULT_HA_BASE", "http://192.168.0.125:8123");
  const signedLiveFeed = useAptSignedCameraStream({
    entity: liveHaEntity,
    haUrl: liveHaBase,
    enabled: !!(open && liveCam && !calibCam && !simActive && liveHaEntity),
  });
  const liveCameraExact = !!(liveCam && aptCameraAlignment(liveCam).exact);
  // The unified shell shows the feed in the camera column, so the room never
  // reshapes around it.
  const cameraSurroundActive = !!(open && !unifiedShell && liveOn && liveCam && mobile && !calibCam && liveCameraExact && cameraPoseReady);

  const roomInspectorFrame = modelScopeRef.current?.frameDigest;
  const roomInspectorRegistration = modelScopeRef.current?.registrationVersion;
  const getRoomInspectorContext = useCallback(() => {
    if (selectedSiteId === "victoria") return getVirtualFixtureSelectionContext();
    const engine = selectionEngine, scope = modelScopeRef.current;
    if (selectedSiteId !== "echo" || !open || phase !== "ready" || !viewOpenRef.current
        || !apartmentSceneControlsEnabledRef.current || editing || calibCam || liveOn || rehearsalIsolated
        || spatialScale !== "apartment" || travellingRef.current || deskTargetCaptureRef.current?.active
        || !engine || engineRef.current !== engine || spatialLifecycleRevisionRef.current !== selectionLifecycle
        || workspaceStateRef.current.model !== model || modelSiteRef.current !== "echo"
        || workspaceAuthorityRef.current.key !== apartmentAuthorityKey || scope?.authorityKey !== apartmentAuthorityKey
        || scope?.frameDigest !== roomInspectorFrame || scope?.registrationVersion !== roomInspectorRegistration
        || scope?.contentRevision !== selectedContentRevision || window.HomeSpatialViewport?.getSelectedSiteId?.() !== "echo"
        || engine.siteId !== "echo" || engine.contentRevision !== selectedContentRevision
        || engine.sceneIdentity?.siteId !== "echo" || engine.sceneIdentity?.contentRevision !== selectedContentRevision
        || engine.sceneIdentity?.frameDigest !== roomInspectorFrame
        || engine.sceneIdentity?.registrationVersion !== roomInspectorRegistration) return null;
    return { engine, model };
  }, [selectedSiteId, getVirtualFixtureSelectionContext, selectionEngine, selectionLifecycle, open, phase,
    editing, calibCam, liveOn, rehearsalIsolated, spatialScale, model, apartmentAuthorityKey,
    roomInspectorFrame, roomInspectorRegistration, selectedContentRevision]);
  const selectRoomDevice = useCallback(id => {
    if (!getRoomInspectorContext() || selectedSiteId !== "echo"
        || !(model.devices || []).some(device => device.id === id && device.ha_entity_id)) return false;
    setCardId(id); onShowShellControls?.(); return true;
  }, [getRoomInspectorContext, selectedSiteId, model.devices, onShowShellControls]);
  const roomLightGroups = window.aptRoomLightGroups?.({ rooms: model.zones || [], lights: virtualFixtureDocument
    ? virtualFixtureItems.map(fixture => ({ id: fixture.fixture_id, label: fixture.label || fixture.fixture_id,
      ariaLabel: `Select ${fixture.label || fixture.fixture_id}`,
      room_id: fixture.room_id, selected: virtualFixtureControlsOpen && virtualFixtureId === fixture.fixture_id,
      summary: window.HomeApartmentQuick?.virtualSummary(fixture.state),
      power: [fixture.state?.spotlight, fixture.state?.downward, ...(fixture.state?.radials || [])].some(channel => channel?.on === true) ? "on"
        : fixture.state?.spotlight?.on === false && fixture.state?.downward?.on === false
          && fixture.state?.radials?.length === 6 && fixture.state.radials.every(channel => channel.on === false) ? "off" : "unknown" }))
    : (model.devices || []).filter(device => device.ha_entity_id?.startsWith("light.")).map(device => ({
      id: device.id, label: device.name || device.label || device.id, room_id: device.room_id, selected: cardId === device.id,
      summary: window.HomeApartmentQuick?.lightSummary(device.ha_entity_id, statesRef.current[device.ha_entity_id]),
      power: ["on", "off"].includes(statesRef.current[device.ha_entity_id]?.state) ? statesRef.current[device.ha_entity_id].state : "unknown",
    })) }) || [];
  // List hover highlights the same Echo marker as canvas hover; it never selects.
  const hoverRoomDevice = id => {
    const context = getRoomInspectorContext();
    if (!context || selectedSiteId !== "echo" || virtualFixtureDocument) return;
    const next = id && (model.devices || []).some(device => device.id === id) ? id : null;
    setHoverId(next); context.engine.overlay.setHover?.(next);
  };
  const roomPowerDisabledReason = !apartmentSceneControlsEnabled ? "Room controls unavailable"
    : virtualFixtureDocument ? "" : !apartmentDeviceControlsEnabled || connection !== "online" || simActive
      ? "Home Assistant controls unavailable" : "";
  const setRoomLightsPower = async (roomId, on) => {
    const context = getRoomInspectorContext();
    if (!context || typeof on !== "boolean" || roomPowerDisabledReason) return false;
    const group = roomLightGroups.find(group => group.id === roomId);
    if (!group || group.power === "unknown") return false;
    if (virtualFixtureDocument) {
      const before = window.HomeApartmentQuick?.virtualSnapshot(context.preview.fixtures
        .filter(fixture => group.lights.some(light => light.id === fixture.fixture_id))) || null;
      for (const light of group.lights) {
        if (getRoomInspectorContext()?.engine !== context.engine) return false;
        const fixture = context.preview.fixtures.find(fixture => fixture.fixture_id === light.id);
        if (!fixture) return false;
        changeVirtualFixtureByHand({ spotlight: { on }, downward: { on },
          radials: aptUnifiedRadialPowerPatch(fixture.state.radials, on) }, light.id);
        const actual = getRoomInspectorContext()?.preview.fixtures.find(fixture => fixture.fixture_id === light.id)?.state;
        if (!actual || actual.spotlight?.on !== on || actual.downward?.on !== on
            || actual.radials?.length !== 6 || actual.radials.some(zone => zone.on !== on)) return false;
      }
      offerVirtualUndo(`${group.label} ${on ? "on" : "off"}`, before, context);
      return true;
    }
    const entityIds = [...new Set(group.lights.map(light => (model.devices || []).find(device => device.id === light.id)?.ha_entity_id)
      .filter(entityId => typeof entityId === "string" && entityId.startsWith("light.")))];
    // The existing guarded loop is unchanged; Undo is offered only for calls Home Assistant accepted.
    const Q = window.HomeApartmentQuick, before = Q ? Q.snapshot(statesRef.current, entityIds) : null;
    let sent = 0;
    for (const entityId of entityIds) {
      if (getRoomInspectorContext()?.engine !== context.engine || !isControlCurrent()) return false;
      if (await callSvc("light", on ? "turn_on" : "turn_off", { entity_id: entityId }) === true) sent++;
    }
    const done = getRoomInspectorContext()?.engine === context.engine;
    if (done && before && sent) offerEchoUndo({ label: `${group.label} ${on ? "on" : "off"}`, note: "" }, before, context);
    return done;
  };
  /* Quick and room switches share one exact-Undo path (APT-R03). Echo calls go
   * through callSvc, so site, currentness and Travel Mode guards are unchanged. */
  const quickBlock = window.HomeApartmentQuick?.quickBlock({ connection, sim: simActive })
    || { all: "Quick controls unavailable", energizing: "Quick controls unavailable" };
  // Chips come from this home's own HA scenes and scripts (registry snapshot).
  const quickChips = selectedSiteId === "echo" && !virtualFixtureDocument && !simActive
    ? window.HomeApartmentQuick?.quickChips(registry.states || {}) || [] : [];
  const echoQuickCurrent = context => () => getRoomInspectorContext()?.engine === context.engine && isControlCurrent() === true;
  const undoEchoQuick = async (plan, before, current) => {
    const Q = window.HomeApartmentQuick;
    if (!Q || quickBusyRef.current || !current()) return false;
    quickBusyRef.current = true; setQuickBusy(true);
    try {
      const result = await Q.undoQuick(before, { dispatch: callSvc, isCurrent: current });
      if (!current()) return false;
      // "Undone" means Home Assistant's readback matches the exact snapshot.
      const confirmed = before.filter(entry => ["on", "off"].includes(entry.state))
        .every(entry => Q.matchesSnapshot(entry, statesRef.current[entry.entity_id]));
      if (result.ok) showToast(!result.exact
        ? `${plan.label} undone; ${result.skipped.length} unavailable light${result.skipped.length === 1 ? " was" : "s were"} not restored`
        : confirmed ? `${plan.label} undone` : `${plan.label} undo sent; Home Assistant hasn't confirmed every light yet`);
      else if (result.sent) showToast(`${plan.label} was only partly undone`, { tone: "error" });
      return result.ok;
    } finally { quickBusyRef.current = false; setQuickBusy(false); }
  };
  const runEchoQuick = async (action, context = getRoomInspectorContext()) => {
    const Q = window.HomeApartmentQuick;
    if (!Q || !context || selectedSiteId !== "echo" || virtualFixtureDocument || quickBusyRef.current) return false;
    const plan = Q.planQuick(action, { devices: model.devices || [], states: { ...registry.states, ...statesRef.current } });
    if (!plan) return false;
    const reason = quickBlock.all || (plan.energizing ? quickBlock.energizing : "");
    if (reason) { showToast(reason, { tone: "error" }); return false; }
    const current = echoQuickCurrent(context);
    quickBusyRef.current = true; setQuickBusy(true);
    let result;
    try {
      // Undo needs the live state of every light a scene touches, mapped or not.
      let states = statesRef.current;
      const unmapped = plan.targets.filter(id => !states[id]);
      if (unmapped.length && window.__hav_haClient && typeof window.HomeApartmentData.readStates === "function") {
        try { states = { ...states, ...await window.HomeApartmentData.readStates(window.__hav_haClient, unmapped, { siteId: "echo" }) }; }
        catch (_) { /* Unread lights are reported as not restorable. */ }
        if (!current()) return false;
      }
      result = await Q.applyQuick(plan, { states, dispatch: callSvc, isCurrent: current });
    } finally { quickBusyRef.current = false; setQuickBusy(false); }
    if (!result.sent || !current()) return false;
    offerEchoUndo({ ...plan, label: `${plan.label}${result.ok ? "" : " · partly applied"}` }, result.before, context);
    return result.ok;
  };
  const offerEchoUndo = (plan, before, context) => {
    const current = echoQuickCurrent(context);
    showToast(`${plan.label}${plan.note ? `. ${plan.note}` : ""}`, { actionLabel: "Undo", timeoutMs: 6000, isCurrent: current,
      action: () => { void undoEchoQuick(plan, before, current); } });
  };
  // The Den's modeled batches restore their exact channel snapshot; nothing is sent.
  const offerVirtualUndo = (label, before, context) => {
    const Q = window.HomeApartmentQuick;
    if (!Q || !before?.length) return;
    const current = () => getRoomInspectorContext()?.engine === context.engine;
    showToast(`${label} · modeled`, { actionLabel: "Undo", timeoutMs: 6000, isCurrent: current, action: () => {
      for (const entry of before) changeVirtualFixtureByHand(Q.virtualRestorePatch(entry), entry.fixture_id);
      const fixtures = getRoomInspectorContext()?.preview.fixtures || [];
      const exact = before.every(entry => Q.virtualMatches(entry, fixtures.find(fixture => fixture.fixture_id === entry.fixture_id)?.state));
      showToast(exact ? `${label} undone` : `${label} could not be undone exactly`, exact ? undefined : { tone: "error" });
    } });
  };
  const setVirtualQuickPower = on => {
    const context = getRoomInspectorContext();
    if (!context || !virtualFixtureDocument) return false;
    const before = window.HomeApartmentQuick?.virtualSnapshot(context.preview.fixtures) || null;
    for (const fixture of virtualFixtureItems) changeVirtualFixtureByHand({ spotlight: { on }, downward: { on },
      radials: aptUnifiedRadialPowerPatch(fixture.state?.radials || [], on) }, fixture.fixture_id);
    offerVirtualUndo(`All ${on ? "on" : "off"}`, before, context);
    return true;
  };
  const openNamedAimEditor = useCallback(id => {
    const context = getRoomInspectorContext();
    if (!context || model._equipment_registration || !apartmentPlacementControlsEnabled
        || !(model.targets || []).some(target => typeof target.id === "string" && target.id === id)) return false;
    setEditorTargetEntry({ id, engine: context.engine, siteId: selectedSiteId, authority: apartmentAuthorityKey,
      frame: roomInspectorFrame, registration: roomInspectorRegistration, revision: selectedContentRevision });
    setAimMode(false); setAimDestination(null); setCardId(null); setEditing(true); onShowShellControls?.(); return true;
  }, [getRoomInspectorContext, model, apartmentPlacementControlsEnabled, selectedSiteId, apartmentAuthorityKey,
    roomInspectorFrame, roomInspectorRegistration, selectedContentRevision, onShowShellControls]);
  // Named aims and Echo's engineered light on the shared aim path
  // (home-apartment-aim-library.jsx): this view supplies its state and guarded
  // callbacks and mounts what the hook returns.
  const echoNamedAims = (window.useAptEchoNamedAims || aptNoEchoNamedAims)({ open, siteId: selectedSiteId, engine: engineRef.current,
    host: hostRef.current, model, setModel, saveModel, saveStatus, markModelDirty, sim: simActive, store: aimProposals, aimMode, aimFixtureId,
    setAimMode, setAimFixtureId, editing, sceneEnabled: apartmentSceneControlsEnabled && spatialScale === "apartment" && !rehearsalIsolated,
    placementEnabled: apartmentPlacementControlsEnabled && !model._equipment_registration, contentRevision: selectedContentRevision,
    showToast, openNamedAimEditor, onShowControls: onShowShellControls, onShowAim: onShowShellAim || onShowShellControls,
    onAimChange: () => setServicePulse(value => value + 1) });
  echoNamedAimsRef.current = echoNamedAims;
  const namedAims = echoNamedAims.roomList || window.AptNamedAimList && <window.AptNamedAimList targets={model.targets || []} onEdit={openNamedAimEditor}
    disabledReason={model._equipment_registration ? "Named-aim editing is unavailable for this registration."
      : !apartmentPlacementControlsEnabled || !apartmentSceneControlsEnabled ? "Setup is unavailable for this room." : ""} />;
  const openUnifiedSetup = useCallback(() => {
    // APT-U01: Setup is a pressed toggle; pressing it again leaves like Done.
    if (editing) { setEditing(false); return; }
    if (!apartmentSceneControlsEnabled || !(apartmentDeviceControlsEnabled || apartmentPlacementControlsEnabled)) return;
    setEditorTargetEntry(null);
    setAimMode(false); setAimDestination(null); setCardId(null); setEditing(true);
    onShowShellControls?.();
  }, [editing, apartmentSceneControlsEnabled, apartmentDeviceControlsEnabled, apartmentPlacementControlsEnabled, onShowShellControls]);
  // APT-U01: Setup › Cameras and sensors starts the existing calibration. It
  // leaves Setup, flies to the camera view, and once that flight has ended
  // (settled or timed out) runs the same steps as the camera-view calibrate
  // button.
  // APT-U03: Setup › Home Assistant Flash. The same Echo, authority and
  // currentness gates as callSvc, then the shared guarded dispatch (site,
  // rehearsal, Travel Mode read from Home Assistant). Never queued.
  const flashFromSetup = useCallback(device => {
    const engine = engineRef.current, operation = spatialTravelIntentRef.current, authority = workspaceAuthorityRef.current.key;
    const current = () => engineRef.current === engine && spatialTravelIntentRef.current === operation
      && workspaceAuthorityRef.current.key === authority && modelScopeRef.current?.authorityKey === authority
      && window.HomeSpatialViewport?.getSelectedSiteId?.() === "echo" && isControlCurrent()
      && window.HomeApartmentSiteSession.canControlEcho({ sceneSiteId: engineRef.current?.siteId,
        selectedSiteId: window.HomeSpatialViewport?.getSelectedSiteId?.(),
        transitioning: (interiorSessionRef.current?.readback?.().phase ?? "ready") !== "ready" });
    if (!window.AptSetupLinks) return Promise.resolve({ ok: false, reason: "Flash is unavailable; nothing was sent." });
    return window.AptSetupLinks.flash({ client: rehearsalIsolated || simActive || window.HomeMapRehearsalOfflineState?.active ? null : window.__hav_haClient,
      entityId: device?.ha_entity_id, siteId: engine?.siteId, isCurrent: current });
  }, [rehearsalIsolated, simActive, isControlCurrent]);
  const setupCalibrationRef = useRef(null);
  const calibrateFromSetup = useCallback(device => {
    if (!device || simActive || mobile || !window.HomeApartmentCalibrate || !device.camera?.frigate_name) return false;
    setupCalibrationRef.current = device.id;
    setEditing(false);
    if (flyToDeviceView(device)) return true;
    setupCalibrationRef.current = null; return false;
  }, [simActive, mobile, flyToDeviceView]);
  useEffect(() => {
    const pending = setupCalibrationRef.current;
    if (!pending) return;
    if (!liveCam || liveCam.id !== pending || calibCam || !apartmentSceneControlsEnabled) { setupCalibrationRef.current = null; return; }
    if (liveFeedStatus === "flying") return;
    setupCalibrationRef.current = null;
    setLiveOn(false);
    const e = engineRef.current;
    if (e && preSnapModeRef.current == null) preSnapModeRef.current = e.modes.mode;
    e?.modes.setMode("mesh", { duration: 0 }).catch(() => {});
    aptBeginDollhouseCalibration(e);
    calibPairCountRef.current = 0; calibEscArmRef.current = 0;
    setCalibCam(liveCam);
  }, [liveCam, calibCam, liveFeedStatus, apartmentSceneControlsEnabled]);
  const unifiedSelectionLabel = virtualFixtureControlsOpen && selectedVirtualFixture
    ? selectedVirtualFixture.label || selectedVirtualFixture.fixture_id
    : (model.devices || []).find(device => device.id === cardId)?.name || null;
  // Chat bound to the room (APT-C02/C03): the current home's names for chips,
  // presentation-only hover and select, and one modeled change path. A Victoria
  // patch goes through changeVirtualFixture like any hand change; nothing here
  // calls a service. Every call re-checks the home and room key it was given.
  const chatRoomKey = selectedSiteId === "victoria"
    ? virtualFixtureDocument ? `victoria:${selectedContentRevision}:${virtualFixtureDocument.revision}:${virtualFixtureDocument.digest}` : ""
    : selectedSiteId === "echo" && selectedContentRevision ? `echo:${selectedContentRevision}` : "";
  const chatRoomLights = selectedSiteId === "victoria"
    ? virtualFixtureItems.map(fixture => ({ id: fixture.fixture_id, label: fixture.label || fixture.fixture_id }))
    : (model.devices || []).filter(device => device.ha_entity_id?.startsWith("light."))
      .map(device => ({ id: device.id, label: device.name || device.label || device.id, entityId: device.ha_entity_id }));
  const chatRoomAims = selectedSiteId === "echo"
    ? (model.targets || []).filter(target => typeof target.id === "string").map(target => ({ id: target.id, label: target.name || target.label || target.id }))
    : [];
  const chatRoomSelection = selectedSiteId === "victoria"
    ? virtualFixtureControlsOpen && selectedVirtualFixture ? { kind: "light", id: selectedVirtualFixture.fixture_id } : null
    : cardId && chatRoomLights.some(light => light.id === cardId) ? { kind: "light", id: cardId } : null;
  const chatRoomSignature = chatRoomKey && JSON.stringify([selectedSiteId, chatRoomKey, chatRoomLights, chatRoomAims, chatRoomSelection]);
  const chatRoomActionsRef = useRef(null);
  const chatRoomHoverRef = useRef(null);
  const chatRoomIsCurrent = ref => !!chatRoomKey && !!ref && ref.siteId === selectedSiteId && ref.key === chatRoomKey;
  const chatRoomHover = ref => {
    const engine = engineRef.current;
    if (!engine) return false;
    if (ref === null) {
      const previous = chatRoomHoverRef.current;
      chatRoomHoverRef.current = null;
      if (previous?.kind === "light") { engine.overlay.setHover(null); setHoverId(null); }
      if (previous?.kind === "aim") engine.overlay.setTargetHover?.(null, aimDestination?.kind === "named" ? aimDestination.id : null);
      return true;
    }
    // Victoria's virtual fixtures have no renderer hover seam yet.
    if (!chatRoomIsCurrent(ref) || selectedSiteId !== "echo" || !getRoomInspectorContext()) return false;
    if (ref.kind === "light" && chatRoomLights.some(light => light.id === ref.id)) {
      engine.overlay.setHover(ref.id); setHoverId(ref.id); chatRoomHoverRef.current = ref; return true;
    }
    if (ref.kind === "aim" && chatRoomAims.some(aim => aim.id === ref.id) && engine.overlay.setTargetHover) {
      engine.overlay.setTargetHover(ref.id, aimDestination?.kind === "named" ? aimDestination.id : null);
      chatRoomHoverRef.current = ref; return true;
    }
    return false;
  };
  const chatRoomSelect = ref => {
    if (!chatRoomIsCurrent(ref) || ref.kind !== "light") return false;
    return selectedSiteId === "victoria" ? selectVirtualFixture(ref.id) : selectRoomDevice(ref.id);
  };
  const chatRoomModeledLight = ref => {
    if (!chatRoomIsCurrent(ref) || selectedSiteId !== "victoria" || ref.kind !== "light"
        || !virtualFixtureControlsOpen || virtualFixtureId !== ref.id) return null;
    const fixture = getVirtualFixtureSelectionContext()?.preview.fixtures.find(item => item.fixture_id === ref.id);
    return fixture ? { state: fixture.state, color_temp_ranges: fixture.color_temp_ranges } : null;
  };
  chatRoomActionsRef.current = {
    hover: chatRoomHover,
    select: chatRoomSelect,
    readModeledLight: chatRoomModeledLight,
    applyModeledPatch: (ref, patch) => {
      // A Chat-originated write is a hand change too (Den sign-off, 2026-10-19).
      if (!chatRoomModeledLight(ref) || changeVirtualFixtureByHand(patch, ref.id) !== true) return null;
      const after = chatRoomModeledLight(ref);
      return after ? { ok: true, state: after.state } : null;
    },
  };
  const chatRoomSnapshot = useMemo(() => {
    if (!chatRoomSignature) return null;
    const [siteId, key, lights, namedAims, selection] = JSON.parse(chatRoomSignature);
    const call = (name, fallback) => (...args) => {
      try { return chatRoomActionsRef.current?.[name]?.(...args) ?? fallback; } catch (_) { return fallback; }
    };
    return Object.freeze({ schema: "home-apartment-chat-room-1", siteId, key, lights, namedAims, selection,
      hover: call("hover", false), select: call("select", false), adjust: call("select", false),
      readModeledLight: call("readModeledLight", null), applyModeledPatch: call("applyModeledPatch", null) });
  }, [chatRoomSignature]);
  useEffect(() => {
    if (!unifiedShell || !onShellState) return;
    onShellState({ siteId: selectedSiteId, sites: window.HomeSpatialViewport?.getSites?.() || [], phase,
      sourceLabel: sourceMeta.label, saveState: saveStatus.state, saveDetail: saveStatus.detail,
      physicalControls: apartmentDeviceControlsEnabled && connection === "online",
      ...aptShellLightFacts(model, virtualFixtureItems.length, virtualFixtureDocument?.scope?.room_id, connection),
      hasPerception: perceptionDockAvailable,
      dockState: perceptionDockAvailable ? unifiedDock : "hidden",
      selectionLabel: unifiedSelectionLabel, setupActive: editing,
      haConnection: connection, haLastReportedAt: selectedSiteId === "echo" ? window.HomeApartmentStates?.latestReport(statesRef.current, window.HomeApartmentQuick?.echoLightEntityIds(model.devices)) ?? null : null,
      canSetup: apartmentSceneControlsEnabled && (apartmentDeviceControlsEnabled || apartmentPlacementControlsEnabled),
      openSetup: openUnifiedSetup, aimBridge: echoNamedAims.chatBridge || null,
      selectHome: id => travelToSite(id, { presentation: "interior" }), chatRoom: chatRoomSnapshot });
  }, [unifiedShell, onShellState, selectedSiteId, selectedContentRevision, phase, sourceMeta.label,
    saveStatus.state, saveStatus.detail, apartmentDeviceControlsEnabled, connection, travelToSite, model.devices, model.zones, virtualFixtureItems.length, virtualFixtureDocument,
    unifiedSelectionLabel, editing, apartmentSceneControlsEnabled, apartmentPlacementControlsEnabled, openUnifiedSetup,
    unifiedDock, perceptionDockAvailable, chatRoomSnapshot, echoNamedAims.chatBridge]);
  useEffect(() => {
    if (unifiedShell && (cardId || virtualFixtureControlsOpen || aimMode || editing)) onShowShellControls?.();
  }, [unifiedShell, cardId, virtualFixtureControlsOpen, virtualFixtureId, aimMode, editing, onShowShellControls]);
  // APT-S02: leaving a home that still holds unsaved work says the work was
  // kept. The parked workspace is the only source; nothing is saved here.
  const keptWorkSiteRef = useRef(selectedSiteId);
  useEffect(() => {
    const previous = keptWorkSiteRef.current;
    keptWorkSiteRef.current = selectedSiteId;
    if (!unifiedShell || !previous || previous === selectedSiteId) return;
    if (!(window.HomeApartmentData?.dirtySites?.() || []).includes(previous)) return;
    showToast(`${window.HomeSpatialViewport?.getSite?.(previous)?.shortName || "The other home"} · your unsaved work here is kept`);
  }, [unifiedShell, selectedSiteId, showToast]);
  React.useLayoutEffect(() => {
    if (unifiedShell && shellControlsHost) shellControlsHost.scrollTop = 0;
  }, [unifiedShell, shellControlsHost, cardId, virtualFixtureId, virtualFixtureControlsOpen, aimMode, editing]);
  /* Room toolbar inset for the renderer's fit (aptRoomToolbarInsets). */
  React.useLayoutEffect(() => {
    const host = hostRef.current, engine = engineRef.current;
    if (!open || phase !== "ready" || !host?.parentElement || typeof engine?.setViewInsets !== "function") return undefined;
    let toolbar = null, raf = 0;
    const resize = new ResizeObserver(() => measure());
    function measure() {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const next = host.parentElement?.querySelector(".apt-ui-room-toolbar") || null;
        if (next !== toolbar) { if (toolbar) resize.unobserve(toolbar); toolbar = next; if (toolbar) resize.observe(toolbar); }
        if (engineRef.current === engine) engine.setViewInsets(aptRoomToolbarInsets(host));
      });
    }
    const children = new MutationObserver(measure);
    children.observe(host.parentElement, { childList: true });
    resize.observe(host);
    measure();
    return () => {
      cancelAnimationFrame(raf); children.disconnect(); resize.disconnect();
      if (engineRef.current === engine) engine.setViewInsets({ top: 0, right: 0, bottom: 0, left: 0 });
    };
  }, [open, phase, unifiedShell, selectedSiteId]);

  if (!open) return null;

  const pct = progress.total ? Math.floor((progress.loaded / progress.total) * 100) : 0;
  const cardDevice = (model.devices || []).find((d) => d.id === cardId) || null;
  const cardState = cardDevice ? statesRef.current[cardDevice.ha_entity_id] : null;
  const hoverDevice = (model.devices || []).find((d) => d.id === hoverId) || null;
  const cameraTop = inCamPose || !!liveCam;
  const cameraSnap = cameraTop && !calibCam;
  const cameraAlignment = liveCam ? aptCameraAlignment(liveCam) : null;
  const mobileCameraSnap = mobile && cameraTop;
  /* Unified Home keeps its room toolbar through a camera view and says where
   * the view is from in the shared hint bar; calibration keeps its own tools. */
  const unifiedCameraView = !!(unifiedShell && spatialScale === "apartment" && cameraSnap);
  const hideViewHud = unifiedShell && spatialScale === "apartment" && !calibCam ? false : mobile ? cameraTop : cameraSnap;
  const canCalibrateCamera = !!(cameraTop && liveCam && !mobile && !simActive && window.HomeApartmentCalibrate);
  const startCameraCalibration = () => {
    if (!apartmentSceneControlsEnabled) return;
    setLiveOn(false);
    const e = engineRef.current;
    if (e && preSnapModeRef.current == null) preSnapModeRef.current = e.modes.mode;
    e?.modes.setMode("mesh", { duration: 0 }).catch(() => {});
    // snapped pose sits point-blank in the geometry — pull back to
    // the overview so the room is visible and orbit/zoom unlock
    aptBeginDollhouseCalibration(e);
    calibPairCountRef.current = 0; calibEscArmRef.current = 0;
    setCalibCam(liveCam);
  };
  const returnToRoom = () => {
    if (!apartmentSceneControlsEnabled) return;
    exitCameraPose(); restoreApartmentOperationFocus("camera");
  };
  /* Navigation chrome stays fixed above the spatial effect. During a handoff,
   * the already-targeted direction is the only impossible action; the opposite
   * button remains enabled and retargets the one live reversible clock. */
  const showZoomHud = !editing && !cameraTop
    && phase !== "boot" && phase !== "loading" && phase !== "error";
  const zoomInTransitionDisabled = travelling || aptZoomTransitionDisabled(
    spatialTransition,
    spatialTransitionDirection,
    "inward",
  );
  const zoomOutTransitionDisabled = travelling || aptZoomTransitionDisabled(
    spatialTransition,
    spatialTransitionDirection,
    "outward",
  );
  const topPad = mobile ? "calc(8px + env(safe-area-inset-top, 0px)) 10px 8px" : "12px 18px";
  const bottomPad = mobile ? "8px 10px calc(10px + env(safe-area-inset-bottom, 0px))" : "14px 18px";
  const mobileCameraFrame = mobile && cameraSnap && liveCam ? aptMobileCameraFrame(viewport, liveCam) : null;
  const cameraFlightActive = !!(cameraSnap && liveCam && !cameraPoseReady && !calibCam);
  const hostFrameStyle = cameraSurroundActive
    || cameraFlightActive
    ? {
        position: "absolute",
        top: 0,
        bottom: 0,
        right: 0,
        left: 0,
        zIndex: 1,
        overflow: "hidden",
        pointerEvents: "none",
        touchAction: "none",
        cursor: "default",
      }
    : mobileCameraFrame
    ? {
        ...mobileCameraFrame,
        right: "auto",
        bottom: "auto",
        zIndex: 1,
        overflow: "hidden",
        pointerEvents: "auto",
        touchAction: "none",
        cursor: "grab",
      }
    : {
        position: "absolute",
        top: 0,
        bottom: 0,
        right: 0,
        left: calibCam && !mobile ? "46%" : 0,
        zIndex: 1,
        pointerEvents: "auto",
        touchAction: "none",
        cursor: "grab",
      };
  const liveFeedStyle = mobileCameraFrame
    ? {
        width: "100%",
        height: "100%",
        maxWidth: "none",
        maxHeight: "none",
        objectFit: "fill",
        background: "#000",
        boxShadow: "none",
        opacity: 1,
      }
    : mobile
    ? {
        width: "100vw",
        height: "100dvh",
        maxWidth: "none",
        maxHeight: "none",
        objectFit: "fill",
        background: "#000",
        boxShadow: "none",
        opacity: 1,
      }
    : {
        width: "min(92vw, calc(74vh * 16 / 9))",
        height: "74vh",
        aspectRatio: "16 / 9",
        objectFit: "cover",
        boxShadow: "0 0 60px 10px rgba(0,0,0,0.35)",
      };
  const liveFeedSettled = aptLiveFeedSettled(liveFeedStatus);
  const showCameraAlignmentBadge = cameraSnap && liveCam && cameraAlignment
    && (!cameraAlignment.exact || !liveFeedSettled);
  const frigateFeedBase = liveCam?.camera?.frigate_name
    ? `${aptServiceBase("apartment3d.frigateBase", "HG_DEFAULT_FRIGATE_BASE", "http://192.168.0.125:5000")}/api/${liveCam.camera.frigate_name}`
    : "";
  const mobileSnapshotFallbackSrc = mobile && frigateFeedBase && !signedLiveFeed.src
    ? aptSnapshotSrc(frigateFeedBase)
    : "";
  const liveFeedBase = signedLiveFeed.src || mobileSnapshotFallbackSrc || frigateFeedBase;
  const liveFeedSource = signedLiveFeed.src ? "ha" : mobileSnapshotFallbackSrc ? "snapshot" : "frigate";
  const liveSnapshotSrc = mobileSnapshotFallbackSrc || "";
  const liveFeedIntrinsics = !mobile
    ? liveCam?.camera?.intrinsics || null
    : null;
  const liveSnapshotIntervalMs = liveSnapshotSrc ? 1100 : 0;
  const sourceColor = sourceMeta.kind === "live_ha_model" ? "#91e6bd"
    : sourceMeta.kind === "simulation" ? "var(--hg-warn)"
      : sourceMeta.kind === "tracker_live" ? "var(--hg-ice)"
        : sourceMeta.kind === "seed_model" ? "#f2cf87" : "var(--hg-fg-4)";
  const revisionLabel = Number.isInteger(model.revision) ? ` · rev ${model.revision}` : "";

  return (
    <div
      ref={viewRootRef}
      data-home-apartment-view="1"
      data-theme={unifiedShell ? undefined : "dark"}
      data-unified-apartment={unifiedShell ? "1" : undefined}
      role={embedded ? "region" : "dialog"}
      aria-modal={embedded ? undefined : true}
      aria-label="3d apartment"
      tabIndex={-1}
      onKeyDownCapture={rememberEscapeTrigger}
      onPointerDownCapture={event => {
        rememberEscapeTrigger(event);
        if (!unifiedShell || !apartmentSceneControlsEnabled || event.button !== 0 || event.isPrimary === false
            || event.target !== engineRef.current?.renderer.domElement) return;
        // End inspector focus before the native canvas gesture starts. Opening
        // Aim can then replace that input without canceling an active drag.
        event.currentTarget.focus({ preventScroll: true });
      }}
      style={{
        position: embedded ? "absolute" : "fixed",
        inset: 0,
        zIndex: embedded ? 0 : 1000,
        background: "#000",
        display: "flex", flexDirection: "column", overflow: "hidden",
        minHeight: mobile && !unifiedShell ? "100dvh" : 0,
        animation: "apt-fade-in 260ms ease-out",
      }}
    >
      <style>{`
        @keyframes apt-fade-in { from { opacity: 0; } to { opacity: 1; } }
        @keyframes apt-toast-in { from { transform: translateY(8px); opacity: 0; }
                                  to { transform: translateY(0); opacity: 1; } }
        [data-apt-semantic-navigation="1"] {
          position: absolute;
          left: 10px;
          top: 10px;
          width: 1px;
          height: 1px;
          margin: -1px;
          padding: 0;
          overflow: hidden;
          clip: rect(0 0 0 0);
          clip-path: inset(50%);
          white-space: nowrap;
          pointer-events: none;
          z-index: 8;
        }
        [data-apt-semantic-navigation="1"]:focus-within {
          width: min(330px, calc(100vw - 20px));
          height: auto;
          margin: 0;
          padding: 12px;
          overflow: visible;
          clip: auto;
          clip-path: none;
          white-space: normal;
          pointer-events: auto;
          background: rgba(6, 12, 18, 0.96);
          border: 1px solid var(--hg-border);
          box-shadow: 0 12px 32px rgba(0,0,0,0.48);
        }
        [data-apt-semantic-navigation="1"] button {
          min-height: 36px;
          padding: 5px 7px;
          font-family: ${APT_FONT_MONO};
          font-size: 9px;
          letter-spacing: 0.06em;
          text-transform: lowercase;
          color: var(--hg-fg-1);
          background: rgba(10,12,16,0.55);
          border: 1px solid var(--hg-border-soft);
          cursor: pointer;
        }
        [data-apt-semantic-navigation="1"] button:disabled {
          color: var(--hg-fg-5);
          cursor: default;
        }
        @media (prefers-reduced-motion: reduce) {
          [data-home-apartment-view] *, [data-home-apartment-view] *::before,
          [data-home-apartment-view] *::after { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }
        }
        @media (max-width: 699px) {
          [data-apt-aim-inspector="1"] button,
          [data-apt-aim-inspector="1"] select { min-height: 44px !important; }
          [data-apt-hud-button="1"] { min-height: 44px !important; }
          [data-apt-semantic-navigation="1"] button,
          [data-apt-semantic-navigation="1"] select { min-height: 44px !important; }
          /* 24px view-angle dots keep their size; an invisible halo grows the
             touch box to 44px without disturbing the row layout */
          [data-apt-az-dot="1"] { position: relative; }
          [data-apt-az-dot="1"]::before { content: ""; position: absolute; inset: -10px; }
        }
      `}</style>

      {phase === "ready" && spatialScale === "apartment" && !editing && !aimMode
        && selectedSiteInterior !== false && apartmentDeviceControlsEnabled && (
        <>
          <AptSemanticNavigation
            navigation={apartmentSemanticNavigation}
            devices={semanticDevicesForRoom}
            roomId={semanticRoomId}
            deviceId={cardId || semanticDeviceId}
            onRoomId={selectSemanticRoom}
            onDeviceId={selectSemanticDevice}
            onPreviousDevice={() => stepSemanticDevice(-1)}
            onNextDevice={() => stepSemanticDevice(1)}
            onOpenDevice={openSemanticDevice}
            deviceSelectRef={semanticDeviceSelectRef}
            disabled={!apartmentSceneControlsEnabled}
          />
          <div
            data-apt-semantic-status="1"
            role="status"
            aria-live="polite"
            aria-atomic="true"
            aria-relevant="text"
            style={{
              position: "absolute",
              width: 1,
              height: 1,
              overflow: "hidden",
              clip: "rect(0 0 0 0)",
              clipPath: "inset(50%)",
              whiteSpace: "nowrap",
            }}
          >
            {semanticRoomStatus || (
              semanticStatusDeviceId === (cardId || semanticDeviceId || null)
                ? apartmentSemanticNavigation.statusText
                : ""
            )}
          </div>
        </>
      )}

      {/*
          The discovery panel.

          Dev-only and behind the same flag as the world view itself. It is a
          diagnostic, so it carries no coordinates, no address, no tile URL and
          no key -- only preset names and the notes that make them comparable.
          Rendered above the chrome so it is usable while the world is on
          screen, and it renders NOTHING when closed beyond its own toggle, so
          it cannot disturb the layout it exists to evaluate.
      */}
      {worldZoomEnabled && (
        <div
          data-apt-world-view-panel={worldViewPanelOpen ? "open" : "closed"}
          style={{
            position: "absolute", right: 10, top: 92, zIndex: 7,
            display: "flex", flexDirection: "column", alignItems: "flex-end",
            gap: 6, pointerEvents: "auto", maxHeight: "72%",
          }}
        >
          <button
            type="button"
            data-apt-world-view-toggle="1"
            aria-expanded={worldViewPanelOpen}
            aria-controls="apt-world-view-options"
            onClick={() => setWorldViewPanelOpen((v) => !v)}
            style={{
              fontFamily: APT_FONT_MONO, fontSize: 9, letterSpacing: "0.14em",
              color: "var(--hg-fg-2)", background: "rgba(6,12,18,0.82)",
              border: "1px solid var(--hg-border-soft)", padding: "5px 9px",
              minHeight: mobile ? 44 : 26, cursor: "pointer",
            }}
          >
            {worldViewPanelOpen ? "close options" : "options"}
          </button>
          {worldViewPanelOpen && worldViewGroups && (
            <div
              id="apt-world-view-options"
              role="region"
              aria-label="World view prototype options"
              style={{
                background: "rgba(6,12,18,0.93)", border: "1px solid var(--hg-border-soft)",
                padding: 10, width: mobile ? 230 : 262, overflowY: "auto",
                maxHeight: "100%", display: "flex", flexDirection: "column", gap: 10,
              }}
            >
              {worldViewGroups.map((group) => {
                const selectedPresetId = worldViewSelection[group.key] || group.fallback;
                const selectedPreset = group.presets[selectedPresetId] || group.presets[group.fallback];
                const noteId = `apt-world-view-note-${group.key}`;
                return (
                <div key={group.key} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  <span style={{
                    fontFamily: APT_FONT_MONO, fontSize: 8.5, letterSpacing: "0.18em",
                    color: "var(--hg-fg-4)", textTransform: "uppercase",
                  }}>{group.label}</span>
                  {Object.values(group.presets).map((preset) => {
                    const active = selectedPresetId === preset.id;
                    return (
                      <button
                        key={preset.id}
                        type="button"
                        data-apt-world-view-option={`${group.key}:${preset.id}`}
                        aria-pressed={active}
                        aria-label={`${preset.label}. ${preset.note}`}
                        aria-describedby={active ? noteId : undefined}
                        title={preset.note}
                        onClick={() => setWorldViewOption(group.key, preset.id)}
                        style={{
                          textAlign: "left", cursor: "pointer",
                          fontFamily: APT_FONT_MONO, fontSize: 9.5,
                          color: active ? "var(--hg-ice)" : "var(--hg-fg-2)",
                          background: active ? "rgba(120,216,223,0.12)" : "transparent",
                          border: `1px solid ${active ? "var(--hg-ice)" : "var(--hg-border-soft)"}`,
                          /* 44 is this view's own mobile floor; the stylesheet enforces it on
                             every other control and an options list is no exception. */
                          padding: "5px 7px", minHeight: mobile ? 44 : 24,
                        }}
                      >
                        {preset.label}
                      </button>
                    );
                  })}
                  <span
                    id={noteId}
                    data-apt-world-view-selected-note={group.key}
                    style={{
                      fontFamily: APT_FONT_MONO,
                      fontSize: 8.5,
                      lineHeight: 1.45,
                      color: "var(--hg-fg-4)",
                      padding: "2px 1px 4px",
                    }}
                  >
                    {selectedPreset?.note || "No description available."}
                  </span>
                </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* engine canvas is appended imperatively (persistent across mounts —
          see the boot effect); React must never own or recreate it */}
      <div
        ref={hostRef}
        data-apt-camera-surround={cameraSurroundActive ? "1" : "0"}
        data-apt-spatial-owner={spatialScale === "apartment" ? "apartment" : "world"}
        aria-hidden={spatialScale === "apartment" ? undefined : true}
        style={{
          ...hostFrameStyle,
          opacity: spatialScale === "apartment" ? 1 : 0,
          pointerEvents: apartmentSceneControlsEnabled ? hostFrameStyle.pointerEvents : "none",
          /* No transition. The handoff writes this opacity every frame from
             handoffFrame(), and a 420ms ease would chase the written value
             instead of equalling it: a 0 written at t=300ms still paints ~0.55
             at t=300ms and does not reach 0 until t~720ms, past the whole 650ms
             timeline. The Apartment would stay visibly lit through the black
             firewall and into the particle resolve — exactly the co-existence
             noCoexistenceViolations() exists to forbid, proved on the curves
             and violated in the DOM where no test could see it. */
          transition: "none",
        }}
      />

      {directionalDisplay && ReactDOM.createPortal(
        <window.AptDirectionalComposer view={directionalDisplay} onMode={changeFixtureInteractionMode}
          spotBadge={window.AptSpotPendingDot && <window.AptSpotPendingDot store={aimProposals} siteId="victoria"
            descriptor={aimSelectedDescriptor} tool={fixtureInteractionMode} />}
          spotName={window.AptSpotPendingDot && aimSelectedProposal && fixtureInteractionMode !== "spot" ? "Spot (aim pending)" : null} />,
        hostRef.current)}

      {aimOverlayElement && hostRef.current && ReactDOM.createPortal(aimOverlayElement, hostRef.current)}
      {echoNamedAims.overlay}
      {tapAim && aimPresentation && hostRef.current && ReactDOM.createPortal(
        <div role="status" data-apt-tap-aim-hint="" style={{ position: "absolute", top: 12, left: "50%", transform: "translateX(-50%)",
          zIndex: 7, display: "flex", alignItems: "center", gap: 8, padding: "5px 6px 5px 12px", borderRadius: 10,
          background: "rgba(8,9,11,.9)", border: "1px solid rgba(69,223,255,.55)", color: "#EDEDED", fontSize: 12.5, whiteSpace: "nowrap" }}>
          Tap a surface to aim {selectedVirtualFixture?.label || "this light"}
          <button type="button" className="hg-focusable" onClick={() => setTapAim(null)} style={{ height: 26, padding: "0 9px",
            borderRadius: 7, border: "1px solid rgba(255,255,255,.16)", background: "transparent", color: "inherit", font: "inherit" }}>
            Cancel <span className="hg-esc-hint">esc</span></button>
        </div>, hostRef.current)}

      {/* Renderer-only outer scale layer. It occupies the same viewport while
          every Apartment control and information surface remains a sibling
          above it. No product UI is rendered into this node. */}
      {/*
        * A home whose interior is a DISCLOSED placeholder, once inside.
        *
        * Unlike the missing-interior panel below, this scene is real and is
        * this home's own engine (a generic massing model from its own pack),
        * so it is shown rather than covered -- and named, because a generic
        * house that looked like a scan would be a fabricated room. The cloud
        * itself also spells SCAN PENDING in front of the street wall; this is
        * the part that survives a camera looking the other way. Non-interactive
        * chrome: the wheel and drags belong to the scene underneath.
        */}
      {spatialScale === "apartment" && spatialTransition === "idle" && interiorPlaceholderShown && (
        <div
          data-apt-interior-placeholder={interiorPlaceholder.kind}
          role="note"
          title={interiorPlaceholder.note || undefined}
          style={{
            position: "absolute",
            top: mobile ? "calc(104px + env(safe-area-inset-top, 0px))" : 64,
            left: "50%",
            transform: "translateX(-50%)",
            zIndex: 5,
            pointerEvents: "none",
            display: "grid",
            gap: 3,
            justifyItems: "center",
            maxWidth: "calc(100% - 32px)",
            padding: "6px 12px",
            background: "rgba(5,12,18,.86)",
            border: "1px solid var(--hg-warn)",
            textAlign: "center",
          }}
        >
          <span data-apt-scan-pending="1" style={{
            fontFamily: APT_FONT_MONO, fontSize: 11, letterSpacing: "0.14em",
            color: "var(--hg-warn)",
          }}>
            {`${selectedSiteName} · scan pending`}
          </span>
          <span style={{
            fontFamily: APT_FONT_MONO, fontSize: 9, letterSpacing: "0.08em",
            color: "var(--hg-fg-3, rgba(255,255,255,0.62))",
          }}>
            generic placeholder model · not the real building
          </span>
        </div>
      )}
      {/*
        * A home with no interior, once the collapse has finished.
        *
        * Opaque, because the Apartment canvas underneath is showing the OTHER
        * home's scan -- it is one persistent engine with one fixture, so
        * without this the descent into Victoria would arrive in Echo Park's
        * living room and present it as Victoria's.
        *
         * The resident canvas is deliberately non-interactive here because it
         * still contains another home's scan. The sibling zoom-out control is
         * the explicit mouse/touch/keyboard route back to the map; this opaque
         * panel must never forward a pointer or double-click to hidden Echo.
        */}
      {spatialScale === "apartment" && selectedSiteInterior === false && (
        <div
          ref={emptyInteriorRef}
          data-apt-empty-interior="1"
          style={{
            ...hostFrameStyle,
            left: 0,
            zIndex: 3,
            pointerEvents: "auto",
            touchAction: "none",
            background: "#05080b",
            display: "grid",
            placeItems: "center",
            textAlign: "center",
            padding: 24,
          }}
        >
          <div style={{ display: "grid", gap: 10, maxWidth: 320 }}>
            <span style={{
              fontFamily: APT_FONT_MONO, fontSize: 11, letterSpacing: "0.14em",
              color: "var(--hg-fg-1)", textTransform: "lowercase",
            }}>
              {`${selectedSiteName} interior`}
            </span>
            <span style={{
              fontFamily: APT_FONT_MONO, fontSize: 10, letterSpacing: "0.08em",
              color: "var(--hg-fg-3, rgba(255,255,255,0.55))", lineHeight: 1.6,
            }}>
              {interiorSwitchState.phase === "loading" ? "loading interior…"
                : interiorSwitchState.phase === "error" ? interiorSwitchState.error
                : "interior unavailable · scan import pending"}
            </span>
            <span style={{
              fontFamily: APT_FONT_MONO, fontSize: 9, letterSpacing: "0.1em",
              color: "var(--hg-fg-3, rgba(255,255,255,0.38))", marginTop: 6,
            }}>
              choose another home above{worldZoomEnabled ? " or zoom out to return to map" : ""}
            </span>
            {interiorSwitchState.phase === "error" && (
              <button type="button" className="hg-focusable" style={{ minHeight: 44 }}
                onClick={() => { void switchInteriorHome(selectedSiteId, { force: true }); }}>Retry interior</button>
            )}
          </div>
        </div>
      )}
      <AptWorldSitePins
        visible={spatialScale !== "apartment"}
        surfaceRef={worldSurfaceRef}
        interactionTargetRef={worldHostRef}
        sites={homeSites}
        selectedSiteId={selectedSiteId}
        travelling={travelling}
        motionActive={spatialTransition !== "idle"}
        onSelect={travelToSite}
        onWheelForward={forwardWheelToWorld}
        frameStyle={hostFrameStyle}
      />
      <div
        ref={worldHostRef}
        data-apt-world-surface="1"
        data-apt-world-scale={spatialScale}
        aria-hidden={spatialScale === "apartment" ? true : undefined}
        style={{
          ...hostFrameStyle,
          left: 0,
          zIndex: 2,
          opacity: spatialScale === "apartment" ? 0 : 1,
          visibility: spatialScale === "apartment" && spatialTransition === "idle" ? "hidden" : "visible",
          /* Own reversal input during both halves of a handoff. Apartment input
             is intentionally suspended then, but the user must still be able
             to reverse the full-viewport motion before it commits. */
          pointerEvents: aptWorldHostTakesInput(spatialScale, spatialTransition) ? "auto" : "none",
          transition: "none",
          background: "#000",
        }}
      />
      <div
        data-apt-world-credits="1"
        style={{ position: "absolute", bottom: mobile ? 156 : 90, right: 14, left: 14,
          /* The recovery card lives in here. When its blackout is up, the
             whole container is lifted over the overview banner (z8), which the
             old centred dialog covered too; otherwise it stays under it. */
          zIndex: aptRecoveryBlackout(journeyRecovery || surfaceRecovery) ? 9 : 7,
          minHeight: 20, pointerEvents: "auto", fontSize: 10, color: "#fff",
          display: "grid", gap: 3,
          visibility: spatialScale !== "apartment" || spatialTransition !== "idle" ? "visible" : "hidden" }}
      >
        {(() => {
          /* "Research imagery" says the pack is unreviewed. It does not say what
             the picture IS, and for Echo Park that is now the load-bearing fact:
             the closest still is a generative reconstruction of the map view, so
             its cars and canopy are drawn rather than observed. The pack already
             carries that sentence in review.note and nothing rendered it, which
             made the manifest more honest than the screen. */
          const reviewed = contentPacks.find((pack) => pack.active
            && pack.manifest.siteId === selectedSiteId && pack.manifest.review?.status === "research-only");
          if (!reviewed) return null;
          /* Local data, but still text rendered into chrome, so it gets what an
             attribution credit gets: control and bidi characters out, length
             clamped. A manifest is a file on disk and files get edited. */
          const note = String(reviewed.manifest.review.note || "")
            .replace(/[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g, " ")
            .replace(/\s+/g, " ").trim().slice(0, 240);
          /* Only while an AI still is on screen: the handoff included, the
             interior and the region/planet ranges never. It used to test the
             scale alone, which kept it up over the live map and the globe and
             dropped it for the outward handoff's particles. */
          return (
            <AptResearchImageryDisclosure
              surfaceRef={worldSurfaceRef}
              worldHostRef={worldHostRef}
              interior={spatialScale === "apartment" && spatialTransition === "idle"}
              motionActive={spatialTransition !== "idle" || travelling}
              holdLayout={Boolean(journeyRecovery || surfaceRecovery)}
              note={note}
            />
          );
        })()}
        <div ref={worldCreditsRef} style={{ position: "relative", minHeight: 22 }} />
        {(confirmedWorldAnchorRef.current?.contentAttributions || selectedSiteEntry?.contentAttributions || []).length > 0 && (
          <div data-apt-pack-attributions="1" style={{ display: "flex", flexWrap: "wrap", gap: "2px 8px",
            background: "rgba(5,12,18,.85)", padding: "3px 6px", width: "fit-content", maxWidth: "100%" }}>
            {(confirmedWorldAnchorRef.current?.contentAttributions || selectedSiteEntry?.contentAttributions || []).map((credit, index) => (
              credit.url
                ? <a key={index} href={credit.url} target="_blank" rel="noopener noreferrer" style={{ color: "inherit" }}>{credit.text}</a>
                : <span key={index}>{credit.text}</span>
            ))}
          </div>
        )}
        {(journeyRecovery || surfaceRecovery) && (
          <AptJourneyRecoveryCard
            message={journeyRecovery?.message || (
              surfaceRecovery?.kind === "content-unavailable" ? "This view needs a complete home pack."
                : surfaceRecovery?.kind === "provider-unavailable" ? "Google Maps is unavailable. Retry, return, or open the bundled world overview."
                : surfaceRecovery?.kind === "render-error" || surfaceRecovery?.kind === "context-lost"
                  ? "The outside view needs to be restored." : "The outside view is still loading."
            )}
            blackout={aptRecoveryBlackout(journeyRecovery || surfaceRecovery)}
            showOverview={spatialScale !== "apartment"
              && ["provider-unavailable", "streaming-timeout"].includes(surfaceRecovery?.kind)}
            onRetry={() => { void retrySpatialRecovery(); }}
            onReturn={() => { void returnFromRecovery(); }}
            onShowOverview={() => { void showWorldOverview(); }}
            onWheelForward={forwardWheelToWorld}
          />
        )}
      </div>
      {worldOverviewActive && spatialScale !== "apartment" && (
        <div data-apt-world-overview="1" style={{ position: "absolute", zIndex: 8, left: 16, top: 64,
          color: "#fff", background: "rgba(5,12,18,.9)", padding: "6px 12px", fontSize: 12 }}>
          World overview · live local imagery unavailable
          <button type="button" className="hg-focusable" style={{ marginLeft: 12, minHeight: 44 }}
            onClick={() => { void showWorldOverview(); }}>Show world overview</button>
          <button type="button" className="hg-focusable" style={{ marginLeft: 12, minHeight: 44 }}
            onClick={() => { setWorldOverviewActive(false); void returnFromRecovery(); }}>Return to interior</button>
        </div>
      )}
      <div
        ref={handoffFallbackRef}
        data-apt-handoff-fallback="1"
        aria-hidden="true"
        style={{
          ...hostFrameStyle,
          left: 0,
          zIndex: 3,
          pointerEvents: "none",
          background: "#000",
          opacity: 0,
          visibility: "hidden",
          transition: "none",
          contain: "strict",
        }}
      />
      <div
        data-apt-world-chrome-scrim="1"
        aria-hidden="true"
        style={{
          position: "absolute",
          inset: 0,
          /* z3 now belongs to the handoff compositor, which must sit above both
             renderer hosts and below all product chrome. The scrim IS product
             chrome, so it moves up rather than competing with the firewall —
             and it is additionally forced to 0 while the firewall is closed,
             because a lit gradient over the black gap is the same
             co-visibility failure in a different layer. */
          zIndex: 4,
          pointerEvents: "none",
          opacity: spatialScale === "apartment" ? 0 : 1,
          visibility: spatialScale === "apartment" && spatialTransition === "idle" ? "hidden" : "visible",
          transition: "none",
          background: [
            "linear-gradient(to bottom, rgba(3,9,14,0.82) 0, rgba(3,9,14,0.58) 64px, transparent 170px)",
            "linear-gradient(to top, rgba(3,9,14,0.86) 0, rgba(3,9,14,0.62) 76px, transparent 190px)",
          ].join(", "),
        }}
      />
      <div
        data-apt-spatial-status="1"
        role="status"
        aria-live="polite"
        aria-atomic="true"
        style={{
          position: "absolute",
          width: 1,
          height: 1,
          overflow: "hidden",
          clip: "rect(0 0 0 0)",
          clipPath: "inset(50%)",
          whiteSpace: "nowrap",
        }}
      >
        {spatialTransition === "idle"
          ? `Spatial viewport: ${spatialScale}. ${aptSpatialAvailability(spatialScale, worldSurfaceRef.current?.snapshot?.())}`
          : "Spatial viewport changing scale"}
      </div>

      {/* Classic tools remain available while a camera pose is active. */}
      {!editing && (!unifiedShell || (cameraTop && !!calibCam) || (cameraTop && spatialScale !== "apartment")) && (
        <div data-apt-legacy-toolbar="1" style={{
          position: "absolute", top: 0, left: 0, right: 0, display: "flex",
          flexDirection: mobileCameraSnap ? "column" : "row",
          alignItems: mobileCameraSnap ? "stretch" : mobile ? "flex-start" : "center",
          gap: mobileCameraSnap ? 8 : mobile ? 7 : 10,
          padding: topPad,
          pointerEvents: "none",
          flexWrap: mobileCameraSnap ? "nowrap" : viewport.width < 1100 ? "wrap" : "nowrap",
          zIndex: 5,
        }}>
          <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.05 }}>
            <span style={{ fontFamily: APT_FONT_SANS, fontSize: 15, fontWeight: 500,
                           color: "var(--hg-fg-0)", letterSpacing: "-0.02em" }}>apartment</span>
            <span style={{ fontFamily: APT_FONT_MONO, fontSize: 8.5, letterSpacing: "0.24em",
                           color: "var(--hg-fg-4)", marginTop: 3 }}>{liveShadow ? 'live geometry · read only' : 'spatial command center'}</span>
          </div>
          {homeSites.length > 1 && (
            <label
              data-apt-site-switcher="1"
              style={{
                display: "flex", alignItems: "center", gap: 6,
                pointerEvents: "auto",
                order: mobile ? -1 : 0,
              }}
            >
              <span style={{
                fontFamily: APT_FONT_MONO, fontSize: 9, letterSpacing: "0.12em",
                color: "var(--hg-fg-4)", textTransform: "uppercase",
              }}>home</span>
              <select
                aria-label="Home"
                className="hg-focusable"
                value={selectedSiteId || ""}
                onChange={(event) => { void travelToSite(event.target.value, { presentation: "interior" }); }}
                style={{
                  minHeight: 44, maxWidth: mobile ? 140 : 190, padding: "3px 6px",
                  fontFamily: APT_FONT_MONO, fontSize: 10,
                  color: "var(--hg-fg-1)", background: "var(--hg-bg-1, #0b1218)",
                  border: "1px solid var(--hg-border-soft)",
                }}
              >
                {homeSites.map((site) => (
                  <option key={site.id} value={site.id}>{site.shortName}{site.sceneAvailable ? "" : " · unavailable"}</option>
                ))}
              </select>
            </label>
          )}
          {mobileCameraSnap && (
            <span style={{ fontFamily: APT_FONT_MONO, fontSize: 8.5, letterSpacing: "0.1em",
                           color: "var(--hg-ice)", lineHeight: 1.35,
                           marginLeft: 0, maxWidth: "100%" }}>
              {cameraAlignment?.short || "camera - mesh locked"}
            </span>
          )}
          {simActive && !mobileCameraSnap && (
            <span style={{ fontFamily: APT_FONT_MONO, fontSize: 9, letterSpacing: "0.12em",
                           color: "var(--hg-warn)", border: "1px solid var(--hg-border-soft)",
                           padding: "3px 8px", marginLeft: 6 }}>sim</span>
          )}
          {!mobileCameraSnap && (
            <span style={{ fontFamily: APT_FONT_MONO, fontSize: 8.5, letterSpacing: "0.08em",
              color: sourceColor, border: `1px solid ${sourceColor}`,
              padding: "3px 7px", maxWidth: mobile ? "100%" : 260,
              whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}
              title={`${sourceMeta.label}${revisionLabel}`}>
              source · {sourceMeta.label}{revisionLabel}
            </span>
          )}
          {!liveShadow && !mobileCameraSnap && sourceMeta.kind !== "simulation" && (
            <span style={{ fontFamily: APT_FONT_MONO, fontSize: 8.5, letterSpacing: "0.08em",
              color: connection === "online" ? "#91e6bd" : "var(--hg-fg-5)" }}>
              ha · {connection === "online" ? "connected" : connection}
            </span>
          )}
          {!mobileCameraSnap && (
            <span style={{ fontFamily: APT_FONT_MONO, fontSize: 8.5, letterSpacing: "0.08em",
              color: saveStatus.state === "conflict" || saveStatus.state === "validation" ? "var(--hg-crit)"
                : saveStatus.state === "saved" ? "#91e6bd"
                  : saveStatus.state === "unsaved" || saveStatus.state === "offline"
                    ? "var(--hg-warn)" : "var(--hg-fg-5)" }}
              title={saveStatus.detail}>
              save · {saveStatus.state}
            </span>
          )}
          <span style={{ fontFamily: APT_FONT_MONO, fontSize: 8.5, letterSpacing: "0.1em",
                         color: trackerStatus === "live" ? "var(--hg-ice)" : "var(--hg-fg-5)",
                         marginLeft: mobileCameraSnap ? 0 : mobile ? 0 : 8,
                         display: mobileCameraSnap ? "none" : "inline" }}>
            {liveShadow ? 'observations · context journal' : 'tracker · ' + trackerStatus}
          </span>
          <span style={{
            marginLeft: mobileCameraSnap ? 0 : "auto",
            display: "flex",
            gap: 6,
            pointerEvents: "auto",
            flexWrap: mobileCameraSnap ? "nowrap" : mobile ? "wrap" : "nowrap",
            justifyContent: mobileCameraSnap ? "space-between" : "flex-end",
            maxWidth: mobile ? "100%" : "none",
            width: mobileCameraSnap ? "100%" : "auto",
          }}>
            {cameraTop && (
              <AptHudButton label="Return to room" disabled={!apartmentSceneControlsEnabled}
                onClick={() => { if (apartmentSceneControlsEnabled) exitCameraPose(); }} mobile={mobile} />
            )}
            {canCalibrateCamera && (
              <AptHudButton label="calibrate" active={!!calibCam}
                disabled={!apartmentSceneControlsEnabled} onClick={startCameraCalibration} mobile={mobile} />
            )}
            {!cameraTop && apartmentDeviceControlsEnabled && (
              <>
                <AptHudButton label="aim light" active={aimMode}
                  disabled={!apartmentSceneControlsEnabled} onClick={() => {
                  if (!apartmentSceneControlsEnabled) return;
                  setCardId(null); setAimMode((value) => !value);
                  if (aimMode) setAimDestination(null);
                }} mobile={mobile} />
                <AptHudButton label="edit" disabled={!apartmentSceneControlsEnabled} onClick={() => {
                  if (!apartmentSceneControlsEnabled) return;
                  setEditorTargetEntry(null);
                  setAimMode(false); setAimDestination(null); setCardId(null); setEditing(true);
                }} mobile={mobile} />
              </>
            )}
            {!cameraTop && selectedSiteId === "victoria" && apartmentPlacementControlsEnabled && (
              <AptHudButton label="edit" onClick={() => {
                setEditorTargetEntry(null);
                setAimMode(false); setAimDestination(null); setCardId(null); setEditing(true);
              }} mobile={mobile} />
            )}
            {!liveShadow && onOpenDynamicReplay && !calibCam && !aimMode && !travelling
              && spatialScale === "apartment" && spatialTransition === "idle" && (
              <AptHudButton label="Rehearse moving book" onClick={onOpenDynamicReplay} mobile={mobile} />
            )}
            {!embedded && <AptHudButton label={(mobile || mobileCameraSnap) ? "close" : "close · esc"} onClick={onClose} mobile={mobile} />}
          </span>
        </div>
      )}

      {/* loading / error */}
      {(phase === "boot" || phase === "loading") && (
        <div style={{
          position: "absolute", left: 0, right: 0, top: "46%", textAlign: "center",
          fontFamily: APT_FONT_MONO, color: "var(--hg-fg-3)", fontSize: 11,
          letterSpacing: "0.14em", pointerEvents: "none",
        }}>
          {phase === "boot" ? "initializing renderer" : `loading apartment · ${pct}%`}
          <div style={{ margin: "10px auto 0", width: 220, height: 1,
                        background: "var(--hg-border-soft)", position: "relative" }}>
            <div style={{ position: "absolute", left: 0, top: 0, bottom: 0,
                          width: `${pct}%`, background: "var(--hg-ice)", transition: "width 200ms" }} />
          </div>
        </div>
      )}
      {phase === "error" && unifiedShell && (() => {
        const Notice = window.HomeApartmentUI.Notice;
        const other = homeSites.find(site => site.id !== selectedSiteId && site.sceneAvailable);
        return <Notice kind="renderer" title={`${selectedSiteName}'s room didn't load`} label="Room recovery" actions={[
          { label: "Retry", ariaLabel: "Retry room", primary: true, onClick: () => { void switchInteriorHome(selectedSiteId, { force: true }); } },
          other && { label: `Open ${other.shortName}`, onClick: () => { void travelToSite(other.id, { presentation: "interior" }); } },
        ]}>
          <p>The room renderer stopped{error ? `: ${error}` : ""}.</p>
          <p>Nothing else changed, and Home won't switch homes on its own.</p>
        </Notice>;
      })()}
      {phase === "error" && !unifiedShell && (
        <div style={{ position: "absolute", left: 0, right: 0, top: "44%", textAlign: "center",
                      fontFamily: APT_FONT_MONO, fontSize: 11, color: "var(--hg-crit)" }}>
          renderer failed — {error}
        </div>
      )}

      {showZoomHud && (
        <div
          role="group"
          aria-label="apartment zoom controls"
          data-apt-label-avoid=""
          style={{
            position: "absolute",
            right: mobile ? 10 : 18,
            top: "50%",
            transform: "translateY(-50%)",
            display: "flex",
            flexDirection: "column",
            gap: 6,
            pointerEvents: "auto",
            zIndex: 6,
          }}
        >
          {(spatialTransition !== "idle"
              || spatialScale !== "apartment"
              || selectedSiteInterior !== false) && (
            <AptZoomButton buttonRef={zoomInButtonRef} label="+" title="zoom in"
              disabled={zoomInTransitionDisabled}
              onClick={() => zoomApartment(1)} mobile={mobile} />
          )}
          <AptZoomButton buttonRef={zoomOutButtonRef} label="-" title="zoom out"
            disabled={zoomOutTransitionDisabled}
            onClick={() => zoomApartment(-1)} mobile={mobile} />
        </div>
      )}

      {unifiedShell && !editing && !hideViewHud && spatialScale === "apartment" && (
        <AptUnifiedRoomToolbar mode={mode} modeLoading={modeLoading} modeError={modeError}
          sourceLabel={sourceMeta.label} disabled={!apartmentSceneControlsEnabled} pointsOnlyReason={interiorPointsOnlyReason}
          modeLockedReason={(liveCam || engineRef.current?.rig?.inCameraPose?.()) && !calibCam
            ? "Camera view is locked to mesh. Return to room to change modes." : null}
          sourceDetail={`${sourceMeta.label}${revisionLabel}`} stats={stats} sourceStatus={<>
            <p className="apt-ui-meta">Save · {saveStatus.state}{saveStatus.detail ? ` · ${saveStatus.detail}` : ""}</p>
            <p className="apt-ui-meta">{liveShadow ? "Observations · context journal" : `Tracker · ${trackerStatus}`}</p>
            {simActive && <p className="apt-ui-meta">Simulation</p>}</>}
          view={((azIdx % 8) + 8) % 8} onMode={next => { if (apartmentSceneControlsEnabled) { setLiveOn(false); pickMode(next); } }}
          onOrbit={direction => { if (apartmentSceneControlsEnabled) engineRef.current?.rig.stepAzimuth(direction); }}
          onFit={() => { if (apartmentSceneControlsEnabled) engineRef.current?.refit?.({ dur: 300 }); }}
          onRehearse={!liveShadow && onOpenDynamicReplay && !calibCam && !aimMode && !travelling
            && spatialScale === "apartment" && spatialTransition === "idle" ? onOpenDynamicReplay : null}
          nativePacks={!liveShadow && window.HomeContentPacks?.isNative ? contentPacks.filter(pack => pack.manifest.siteId === selectedSiteId) : null}
          packVersion={contentPacks.find(pack => pack.active && pack.manifest.siteId === selectedSiteId)?.manifest.revision || ""}
          packBusy={contentPackBusy} onPackChange={version => { void changeContentPack(version); }}
          hasVirtualFixtures={!!virtualFixtureDocument} virtualVisible={virtualSpotlightsVisible} onVirtualVisible={setVirtualSpotlightsVisible}
          homeId={selectedSiteId} homeName={selectedSiteEntry?.shortName} fixtureCount={virtualFixtureItems.length}
          fixtureSelected={virtualFixtureControlsOpen && virtualSpotlightsVisible}
          hasPerception={perceptionDockAvailable}
          modeProgress={modeProgress} onDismissNotice={() => setModeError(null)}
          viewLockedReason={unifiedCameraView ? "Camera view · Return to room to orbit or fit" : null}
          about={{ homeId: selectedSiteId, source: sourceMeta, revision: model.revision, liveShadow, simActive, trackerStatus,
            hasVirtualFixtures: !!virtualFixtureDocument, fixtureCount: virtualFixtureItems.length,
            hasPerception: perceptionDockAvailable,
            cameraViews: aptCameraViewAvailability(selectedSiteId, model.devices) }}
          lit={litState?.active ? litState : null} onLit={virtualFixtureDocument ? next => { if (apartmentSceneControlsEnabled) void toggleLit(next); } : null} />
      )}

      {unifiedCameraView && (() => {
        const HintBar = window.HomeApartmentUI.HintBar;
        return <HintBar kind="camera-view" detail={cameraAlignment ? cameraAlignment.short.replace(/^camera - /, "") : null} actions={[
          canCalibrateCamera && { label: "Calibrate", disabled: !apartmentSceneControlsEnabled, onClick: startCameraCalibration },
          { label: "Return to room", primary: true, escape: true, disabled: !apartmentSceneControlsEnabled, onClick: returnToRoom },
        ]}>Viewing from {liveCam?.name || liveCam?.label || liveCam?.id || "camera"}</HintBar>;
      })()}

      {/* Established HUD stays intact for Classic and the wider world view. */}
      {!editing && !hideViewHud && (!unifiedShell || spatialScale !== "apartment") && (
        <div style={{
          position: "absolute", left: 0, right: 0, bottom: 0, display: "flex",
          alignItems: mobile ? "stretch" : "flex-end",
          padding: bottomPad,
          pointerEvents: "none",
          gap: mobile ? 8 : 12,
          flexDirection: mobile ? "column" : "row",
          zIndex: 5,
        }}>
          <div style={{ fontFamily: APT_FONT_MONO, fontSize: 9,
                        color: spatialScale === "apartment" ? "var(--hg-fg-5)" : "var(--hg-fg-2)",
                        letterSpacing: "0.08em", lineHeight: 1.6, minWidth: mobile ? 0 : 170,
                        textAlign: mobile ? "center" : "left",
                        display: mobile && spatialScale === "apartment" ? "none" : "block" }}>
            {spatialScale !== "apartment" ? (<>
              world · {spatialScale}
              <br />{aptSpatialAvailability(spatialScale, worldSurfaceRef.current?.snapshot?.()).replace(/;/g, " ·")}
            </>) : stats && (<>
              {Math.round(stats.fps)} fps · {(stats.points / 1000).toFixed(0)}k pts · dpr {stats.pixelRatio}
              {stats.calls != null ? ` · ${stats.calls} dc` : ""}
              {stats.tris ? ` · ${(stats.tris / 1e6).toFixed(1)}m tri` : ""}
              <br />{String(stats.gpu).slice(0, 48).toLowerCase()}
              {mode === "splat" && stats.modeDebug && (
                <><br />splat {stats.modeDebug.numSplats ? (stats.modeDebug.numSplats / 1000).toFixed(0) + "k" : "—"}
                · vis {String(stats.modeDebug.splatVisible)} · sr {stats.modeDebug.srParent}</>
              )}
            </>)}
          </div>
          {/*
            * Switching homes without the map.
            *
            * The only way to change home was the projected pins, which are
            * positions on the globe: they exist only while both homes project
            * into the view, so they vanish exactly when you are close enough to
            * be looking at one -- and there was then no way back to the other
            * without first zooming out to the planet. They are also canvas
            * furniture, so keyboard users never had a route at all.
            *
            * A plain select, in the chrome, always reachable.
            */}
          {!liveShadow && window.HomeContentPacks?.isNative && (
            <div style={{ display: "flex", gap: 6, pointerEvents: "auto", alignItems: "center" }}>
              <button type="button" className="hg-focusable" disabled={contentPackBusy}
                style={{ minHeight: 44 }} onClick={() => { void changeContentPack(); }}>Import home pack</button>
              {contentPacks.filter((pack) => pack.manifest.siteId === selectedSiteId).length > 1 && (
                <select aria-label="Home pack version" disabled={contentPackBusy}
                  value={contentPacks.find((pack) => pack.active && pack.manifest.siteId === selectedSiteId)?.manifest.revision || ""}
                  style={{ minHeight: 44 }} onChange={(event) => { void changeContentPack(event.target.value); }}>
                  {contentPacks.filter((pack) => pack.manifest.siteId === selectedSiteId).map((pack) => (
                    <option key={pack.manifest.revision} value={pack.manifest.revision}>{pack.manifest.revision}</option>
                  ))}
                </select>
              )}
            </div>
          )}
          <div style={{
            margin: mobile ? "0" : "0 auto",
            display: "flex",
            gap: 6,
            pointerEvents: "auto",
            justifyContent: mobile ? "center" : "flex-start",
            flexWrap: "wrap",
            width: mobile ? "100%" : "auto",
          }}>
            {liveCam && (
              <AptHudButton label="live" active={selectedSiteInterior !== false && liveOn}
                ariaLabel={apartmentSceneUnavailableReason
                  ? `Live view unavailable: ${apartmentSceneUnavailableReason}`
                  : "Live view"}
                title={apartmentSceneUnavailableReason || undefined}
                disabled={!apartmentSceneControlsEnabled}
                onClick={() => { if (apartmentSceneControlsEnabled) setLiveOn(true); }} />
            )}
            <AptHudButton label="cloud" active={selectedSiteInterior !== false && !liveOn && mode === "points"}
              ariaLabel={apartmentSceneUnavailableReason
                ? `Cloud view unavailable: ${apartmentSceneUnavailableReason}`
                : "Cloud view"}
              onClick={() => {
                if (!apartmentSceneControlsEnabled) return;
                setLiveOn(false); pickMode("points");
              }}
              disabled={!apartmentSceneControlsEnabled || modeLoading === "points"}
              title={apartmentSceneUnavailableReason || undefined} />
            <AptHudButton label="photo" active={selectedSiteInterior !== false && !liveOn && mode === "splat"}
              ariaLabel={apartmentSceneUnavailableReason || interiorPointsOnlyReason
                ? `Photo view unavailable: ${apartmentSceneUnavailableReason || interiorPointsOnlyReason}`
                : "Photo view"}
              onClick={() => {
                if (!apartmentSceneControlsEnabled || interiorPointsOnlyReason) return;
                setLiveOn(false); pickMode("splat");
              }}
              disabled={!apartmentSceneControlsEnabled || modeLoading === "splat" || !!interiorPointsOnlyReason}
              title={apartmentSceneUnavailableReason || interiorPointsOnlyReason
                || (modeError?.mode === "splat" ? modeError.detail : "")} />
            <AptHudButton label="mesh" active={selectedSiteInterior !== false && !liveOn && mode === "mesh"}
              ariaLabel={apartmentSceneUnavailableReason || interiorPointsOnlyReason
                ? `Mesh view unavailable: ${apartmentSceneUnavailableReason || interiorPointsOnlyReason}`
                : "Mesh view"}
              onClick={() => {
                if (!apartmentSceneControlsEnabled || interiorPointsOnlyReason) return;
                setLiveOn(false); pickMode("mesh");
              }}
              disabled={!apartmentSceneControlsEnabled || modeLoading === "mesh" || !!interiorPointsOnlyReason}
              title={apartmentSceneUnavailableReason || interiorPointsOnlyReason
                || (modeError?.mode === "mesh" ? modeError.detail : "")} />
          </div>
          {(modeLoading || modeError) && (
            <div style={{
              pointerEvents: "none",
              alignSelf: mobile ? "center" : "flex-end",
              maxWidth: mobile ? "calc(100vw - 24px)" : 260,
              fontFamily: APT_FONT_MONO,
              fontSize: mobile ? 9 : 9.5,
              letterSpacing: "0.08em",
              color: modeError ? "var(--hg-warn)" : "var(--hg-fg-3)",
              background: "rgba(10,12,16,0.72)",
              border: "1px solid var(--hg-border-soft)",
              padding: "6px 9px",
              textAlign: mobile ? "center" : "left",
            }}>
              {modeLoading ? `loading ${modeLoading === "splat" ? "photo" : modeLoading}` : modeError.message}
            </div>
          )}
          <div style={{ textAlign: "right", pointerEvents: "auto", maxWidth: "100%" }}>
            <div style={{ display: "flex", gap: mobile ? 12 : 5, flexWrap: "wrap",
                          justifyContent: mobile ? "center" : "flex-end", marginBottom: 6 }}>
              {Array.from({ length: 8 }).map((_, i) => (
                <button key={i} className="hg-focusable" data-apt-az-dot="1"
                  aria-label={apartmentSceneUnavailableReason
                    ? `View angle ${i + 1} of 8 unavailable: ${apartmentSceneUnavailableReason}`
                    : `View angle ${i + 1} of 8`}
                  aria-pressed={selectedSiteInterior === false ? undefined : i === azIdx}
                  title={apartmentSceneUnavailableReason || undefined}
                  disabled={!apartmentSceneControlsEnabled}
                  onClick={() => {
                    if (!apartmentSceneControlsEnabled) return;
                    engineRef.current?.rig.goTo({ az: i, dur: 600 });
                  }}
                  style={{ width: 24, height: 24, borderRadius: 24, padding: 0,
                           cursor: apartmentSceneControlsEnabled ? "pointer" : "default",
                           border: "1px solid var(--hg-border)",
                           background: selectedSiteInterior !== false && i === azIdx
                             ? "var(--hg-ice)" : "rgba(10,12,16,0.28)",
                           opacity: selectedSiteInterior === false ? 0.32 : (i === azIdx ? 1 : 0.58) }} />
              ))}
            </div>
            <span style={{ fontFamily: APT_FONT_MONO, fontSize: 8.5, color: "var(--hg-fg-5)",
                           letterSpacing: "0.1em", display: mobile ? "none" : "inline" }}>
              {liveShadow ? 'Read-only room inspection · physical controls unavailable' : spatialScale === "apartment"
                ? "drag · wheel zoom · ←→↑↓ · h home · n/p select device · use inspector switches for power"
                : "wheel zoom · + / - scale · zoom in to return to apartment"}
            </span>
          </div>
        </div>
      )}

      {/* live camera feed - prefers HA's signed camera_proxy_stream (same path
          as the fast vision tray), with Frigate as a fallback. Shown over the
          3D canvas while snapped; mode switching returns after backing out. */}
      {/* APT-P01: in the unified shell the live feed belongs to the camera
          column, never over the room; the room keeps the camera pose. */}
      {unifiedShell && apartmentSceneControlsEnabled && liveCam && liveOn && !calibCam && (
        <AptHomePanelSlot enabled={true} target={shellCameraHost}>
          <section className="apt-dock apt-dock-live" aria-label="Live camera feed" data-apartment-live-feed="column">
            <header className="apt-ui-dock-header"><div className="apt-ui-grow">
              <h2 className="apt-ui-heading">{liveCam.name || liveCam.label || "Camera"}</h2>
              <p className="apt-ui-meta">Live feed · separate from any analyzed frame</p></div>
              {window.HomeApartmentUI?.Badge ? <window.HomeApartmentUI.Badge state={liveFeedSettled ? "live" : "loading"}>
                {liveFeedSettled ? "Live" : liveFeedStatus}</window.HomeApartmentUI.Badge> : null}</header>
            <div className="apt-dock-body"><div className="apt-dock-frame" style={{ position: "relative", aspectRatio: "16 / 9", background: "#05090c" }}>
              <AptUndistortedFeed
                key={`${liveCam.id}-${serviceEpoch}-${liveFeedSource}-${signedLiveFeed.streamKey}`}
                src={liveFeedBase}
                snapshotSrc={liveSnapshotSrc}
                snapshotIntervalMs={liveSnapshotIntervalMs}
                alt={liveCam.name}
                intrinsics={liveFeedIntrinsics}
                style={{ width: "100%", height: "100%", maxWidth: "100%", maxHeight: "100%", objectFit: "contain", background: "#000", boxShadow: "none", opacity: 1 }}
                onStatus={setLiveFeedStatus}
                onReload={signedLiveFeed.src ? signedLiveFeed.reload : undefined}
              /></div>
              <p className="apt-ui-meta">Return to room leaves this camera view. No detection boxes are mapped to this feed.</p></div>
          </section>
        </AptHomePanelSlot>
      )}
      {!unifiedShell && apartmentSceneControlsEnabled && liveCam && liveOn && !calibCam && (
        <div style={mobileCameraFrame
          ? {
              ...mobileCameraFrame,
              zIndex: 3,
              overflow: "hidden",
              pointerEvents: "none",
              display: "block",
            }
          : {
              position: "absolute",
              inset: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              padding: 0,
              pointerEvents: "none",
              zIndex: 3,
            }}>
          {/* full-viewport on mobile so video and WebGL share the same frame;
              holds the same pose behind it — true 1:1 alignment lands with
              per-camera calibration (fov + principal point). Calibrated
              Desktop calibrated cameras render through the WebGL undistorter.
              Mobile Safari/iOS can paint partial gray frames from that canvas
              path, so phone-sized viewports use the raw snapshot feed. */}
          <AptUndistortedFeed
            key={`${liveCam.id}-${serviceEpoch}-${liveFeedSource}-${signedLiveFeed.streamKey}`}
            // key forces a REMOUNT on camera switch: the cleanup deliberately
            // loses the WebGL context (context-budget hygiene), so a reused
            // canvas would come back with a dead context and no warp
            src={liveFeedBase}
            snapshotSrc={liveSnapshotSrc}
            snapshotIntervalMs={liveSnapshotIntervalMs}
            alt={liveCam.name}
            intrinsics={liveFeedIntrinsics}
            style={liveFeedStyle}
            onStatus={setLiveFeedStatus}
            onReload={signedLiveFeed.src ? signedLiveFeed.reload : undefined}
          />
          <div style={{ position: "absolute", top: mobile ? "calc(54px + env(safe-area-inset-top, 0px))" : 52, left: mobile ? 12 : 18, fontFamily: APT_FONT_MONO,
                        fontSize: 9.5, letterSpacing: "0.12em", color: "var(--hg-ice)",
                        background: "rgba(10,12,16,0.6)", padding: "4px 9px",
                        display: mobile ? "none" : "block" }}>
            live · {liveCam.name} · mjpeg
          </div>
        </div>
      )}
      {showCameraAlignmentBadge && (
        <div style={{
          position: "absolute",
          left: mobileCameraFrame ? mobileCameraFrame.left + 10 : mobile ? 12 : 18,
          top: mobileCameraFrame ? mobileCameraFrame.top + 10 : mobile ? "calc(54px + env(safe-area-inset-top, 0px))" : 52,
          zIndex: 4,
          fontFamily: APT_FONT_MONO,
          fontSize: mobile ? 9 : 9.5,
          letterSpacing: "0.1em",
          color: "var(--hg-ice)",
          background: "rgba(10,12,16,0.72)",
          border: "1px solid var(--hg-border-soft)",
          padding: "5px 8px",
          pointerEvents: "none",
          maxWidth: mobileCameraFrame ? Math.max(120, mobileCameraFrame.width - 20) : mobile ? "calc(100vw - 24px)" : 360,
        }}>
          {cameraAlignment?.detail || liveFeedStatus}
          {!liveFeedSettled ? ` · ${liveFeedStatus}` : ""}
        </div>
      )}

      {apartmentSceneControlsEnabled && calibCam && window.HomeApartmentCalibrate && React.createElement(
        window.HomeApartmentCalibrate.Component, {
          key: `${calibCam.id}-${serviceEpoch}`,
          cam: calibCam.camera.frigate_name,
          savedLens: calibCam.camera?.intrinsics || null,
          trackerBase: aptServiceBase("apartment3d.trackerBase", "HG_DEFAULT_TRACKER_BASE", "http://192.168.0.100:8098"),
          onPickRequest: (active) => { calibPickRef.current = active; },
          onIntrinsics: (lens) => {
            if (workspaceAuthorityRef.current.key !== apartmentAuthorityKey
                || window.HomeSpatialViewport?.getSelectedSiteId?.() !== selectedSiteId
                || (window.HomeSpatialViewport?.getSite?.(selectedSiteId)?.contentRevision || null) !== selectedContentRevision) return;
            // persist solved intrinsics into the camera device (app = single writer)
            setModel((m) => {
              const next = { ...m, devices: (m.devices || []).map((d) =>
                d.id === calibCam.id
                  ? { ...d, camera: { ...(d.camera || {}), intrinsics: {
                      K: lens.K, dist: lens.dist, image_size: lens.image_size,
                      rms_px: lens.rms_px } } }
                  : d) };
              window.HomeApartmentData.saveModel(next, { endpoint, token, sim: simActive, siteId: selectedSiteId, contentRevision: selectedContentRevision });
              return next;
            });
          },
          onPairsChanged: (pairs) => {
            calibPairCountRef.current = (pairs || []).length;
            calibEscArmRef.current = 0;   // any capture disarms the Esc-discard confirm
            syncCalibMarkers(pairs);
          },
          onDone: (result) => {
            if (workspaceAuthorityRef.current.key !== apartmentAuthorityKey
                || window.HomeSpatialViewport?.getSelectedSiteId?.() !== selectedSiteId
                || (window.HomeSpatialViewport?.getSite?.(selectedSiteId)?.contentRevision || null) !== selectedContentRevision) return;
            // persist the solved extrinsics into the camera device
            if (result && (result.q || result.R || result.C)) {
              setModel((m) => {
                const next = { ...m, devices: (m.devices || []).map((d) =>
                  d.id === calibCam.id
                    ? { ...d, source: "calibrated",
                        camera: { ...(d.camera || {}), extrinsics: result } }
                    : d) };
                window.HomeApartmentData.saveModel(next, { endpoint, token, sim: simActive, siteId: selectedSiteId, contentRevision: selectedContentRevision });
                return next;
              });
            }
            calibPickRef.current = false; setCalibCam(null);
            calibPairCountRef.current = 0; calibEscArmRef.current = 0;
            syncCalibMarkers([]);
          },
          registerApi: (api) => { calibApiRef.current = api; },
        })}

      {/* labels + cards layer */}
      {phase === "ready" && spatialScale === "apartment" && !editing && !unifiedCameraView
        && apartmentSceneControlsEnabled && apartmentDeviceControlsEnabled && (
        <>
          {hoverDevice && !cardDevice && (
            <window.AptDeviceLabel device={hoverDevice} screen={anchors[hoverId]} />
          )}
          {track && track.pos && (
            <window.AptPersonLabel track={track} screen={anchors.__person} />
          )}
          {track && !track.pos && <window.AptRoomChip track={track} />}
          {cardDevice && !aimMode && unifiedShell && window.AptEchoLightInspector && window.HomeApartmentQuick?.isEchoLightDevice(cardDevice) && (
            <AptHomePanelSlot enabled={true} target={shellControlsHost}><window.AptEchoLightInspector
              homeName={selectedSiteName} device={cardDevice} state={cardState} connection={connection} sim={simActive}
              roomName={(model.zones || []).find(zone => zone.id === cardDevice.room_id)?.name}
              engineered={cardDevice.fixture_kind === "engineered_gimbal_v1"}
              onService={callSvc} onBack={() => setCardId(null)}
              aimDisabledReason={apartmentDeviceControlsEnabled ? "" : "Aiming is unavailable for this room."}
              onAim={() => { if (!apartmentDeviceControlsEnabled) return; setAimFixtureId(cardDevice.id); setCardId(null); setAimMode(true); onShowShellControls?.(); }}
              onSetup={openUnifiedSetup} canSetup={apartmentSceneControlsEnabled && (apartmentDeviceControlsEnabled || apartmentPlacementControlsEnabled)}
            /></AptHomePanelSlot>
          )}
          {cardDevice && !aimMode && !(unifiedShell && window.AptEchoLightInspector && window.HomeApartmentQuick?.isEchoLightDevice(cardDevice)) && (
            <AptHomePanelSlot enabled={unifiedShell} target={shellControlsHost} className="apt-home-card"><window.AptControlCard
              device={cardDevice}
              state={cardState}
              screen={anchors[cardId] || { x: (hostSize.width || window.innerWidth) / 2, y: 100 }}
              bounds={hostSize}
              onClose={() => setCardId(null)}
              onService={callSvc}
              onFlyTo={flyToDeviceView}
              sim={simActive}
            /></AptHomePanelSlot>
          )}
        </>
      )}

      {aimMode && spatialScale === "apartment" && phase === "ready" && !editing
        && apartmentSceneControlsEnabled && apartmentDeviceControlsEnabled && window.HomeApartmentAim && (
        <AptHomePanelSlot enabled={unifiedShell} target={shellControlsHost} className="apt-ui-advanced-aim"><window.HomeApartmentAim
          model={model}
          registry={registry}
          states={statesRef.current}
          sim={simActive}
          connection={connection}
          isControlCurrent={isControlCurrent}
          engine={engineRef.current}
          fixtureId={aimFixtureId}
          onFixtureId={setAimFixtureId}
          destination={aimDestination}
          onDestination={setAimDestination}
          onModel={setModel}
          onDirty={markModelDirty}
          onSave={saveModel}
          saveStatus={saveStatus}
          saving={saving}
          aimRows={echoNamedAims.aimTab}
          onStateReadback={(latest) => {
            Object.assign(statesRef.current, latest || {});
            setServicePulse((n) => n + 1);
          }}
          onClose={() => {
            setAimMode(false); setAimDestination(null);
          }}
          mobile={mobile}
        /></AptHomePanelSlot>
      )}

      {perceptionDockAvailable && !rehearsalIsolated && (
        <AptHomePanelSlot enabled={unifiedShell} target={shellCameraHost}>
          <window.HomeApartmentDenPerception
          engine={engineRef.current}
          active={open && phase === "ready" && spatialScale === "apartment" && !editing && !calibCam && !liveOn}
          mobile={mobile}
          capability={perceptionCapability.source === "capability" ? siteCapabilities : null}
          dockState={unifiedDock} onDockState={setUnifiedDock}
          detail={unifiedDockDetail} onDetail={setUnifiedDockDetail}
          onOpenLab={onOpenPerceptionLab} Icon={AptUnifiedIcon}
          onAimPreviewController={registerDenDeskAimPreview}
          onAimPreviewChange={refreshDenDeskAimPreview}
        /></AptHomePanelSlot>
      )}

      {unifiedShell && !virtualFixtureDocument && !cardDevice && !aimMode && phase === "ready" && echoNamedAims.inspector && (
        <AptHomePanelSlot enabled={true} target={shellControlsHost}>{echoNamedAims.inspector}</AptHomePanelSlot>)}
      {unifiedShell && !virtualFixtureDocument && !cardDevice && !aimMode && phase === "ready" && !echoNamedAims.inspector && (
        <AptHomePanelSlot enabled={true} target={shellControlsHost}>
          <section className="apt-ui-inspector" aria-label="Room controls"><header className="apt-ui-header">
            <h2 className="apt-ui-title">{selectedSiteName}</h2><div className="apt-ui-sub">{(model.devices || []).filter(device => device.ha_entity_id).length} mapped devices</div>
            <div className="apt-ui-actions"><span className="apt-ui-badge" data-state={connection === "online" && apartmentDeviceControlsEnabled ? "current" : "unavailable"}>
              Home Assistant {connection === "online" && apartmentDeviceControlsEnabled ? "connected" : "unavailable"}</span></div>
          </header>{window.AptQuickControls ? <window.AptQuickControls chips={quickChips} block={quickBlock} busy={quickBusy}
            onAll={on => { void runEchoQuick({ kind: "all", on }); }} onChip={entityId => { void runEchoQuick({ kind: "chip", entity_id: entityId }); }}>
            <button type="button" className="apt-ui-button" disabled={!apartmentDeviceControlsEnabled}
              onClick={() => { if (!apartmentDeviceControlsEnabled) return; setCardId(null); setAimMode(true); onShowShellControls?.(); }}>Aim a light</button>
          </window.AptQuickControls> : <section className="apt-ui-section"><h3 className="apt-ui-heading">Quick</h3>
            <button type="button" className="apt-ui-button" disabled={!apartmentDeviceControlsEnabled}
              onClick={() => { if (!apartmentDeviceControlsEnabled) return; setCardId(null); setAimMode(true); onShowShellControls?.(); }}>Aim a light</button>
          </section>}
          {window.AptRoomLightGroups && <window.AptRoomLightGroups groups={roomLightGroups} onSelect={selectRoomDevice}
            onPower={setRoomLightsPower} disabledReason={roomPowerDisabledReason} onHover={hoverRoomDevice} />}
          {namedAims}
          <section className="apt-ui-section"><h3 className="apt-ui-heading">Other devices</h3>
            <ul className="apt-ui-list">{(model.devices || []).filter(device => device.ha_entity_id && !device.ha_entity_id.startsWith("light.")).map(device => {
              const state = statesRef.current[device.ha_entity_id];
              return <li key={device.id}><button type="button" className="apt-ui-list-button"
                onClick={() => selectRoomDevice(device.id)}>
                <span className={`apt-ui-lamp${["on", "playing"].includes(state?.state) ? " is-on" : ""}`} aria-hidden="true" />
                <span className="nm">{device.name || device.label || device.id}</span><span className="vl">{state?.state || "Unavailable"}</span>
              </button></li>;
            })}</ul>
            {!(model.devices || []).some(device => device.ha_entity_id) && <p className="apt-ui-copy">No connected devices are configured for this home.</p>}
          </section><section className="apt-ui-section"><p className="apt-ui-meta">Select a light or device in the room or list.</p>
            <p className="apt-ui-meta">{sourceMeta.label}</p></section></section>
        </AptHomePanelSlot>
      )}

      {unifiedShell && virtualFixtureDocument && phase === "ready" && spatialScale === "apartment" && !editing && !calibCam && !liveOn && (
        <AptHomePanelSlot enabled={true} target={shellControlsHost}>
          <AptUnifiedVirtualInspector homeName={selectedSiteName} fixtures={virtualFixtureItems} fixture={selectedVirtualFixture}
            selected={virtualFixtureControlsOpen} visible={virtualSpotlightsVisible} onVisible={setVirtualSpotlightsVisible}
            tab={virtualInspectorTab} onTabChange={changeVirtualInspectorTab} onSelect={selectVirtualFixture}
            onBack={clearVirtualFixtureSelection} commandBinding={virtualFixtureDocument.binding}
            directionalView={directionalDisplay}
            onShowComposer={() => changeFixtureInteractionMode("zones")}
            roomGroups={roomLightGroups} onRoomPower={setRoomLightsPower} roomPowerDisabledReason={roomPowerDisabledReason} namedAims={namedAims}
            onPatch={patchVirtualFixtureFromInspector} onPatchAll={setVirtualQuickPower} onUndo={() => restoreVirtualAimHistory("undo")} onRedo={() => restoreVirtualAimHistory("redo")}
            canUndo={aimHistoryLength("undo") > 0} canRedo={aimHistoryLength("redo") > 0}
            aimControls={aimPresentation && aimSelectedDescriptor ? <AptAimProposalRows descriptor={aimSelectedDescriptor}
              limits={selectedVirtualFixture?.limits} proposal={aimSelectedProposal} tapAim={!!tapAim}
              onSet={() => setAimProposal({ fixture_id: virtualFixtureId, proposal: aimSelectedProposal })}
              onDiscard={() => discardAimProposal({ fixture_id: virtualFixtureId, proposal: aimSelectedProposal })}
              onTapAim={() => setTapAim(value => value ? null : virtualFixtureId)} /> : null}
            aimNamedAims={window.AptDenNamedAims && <window.AptDenNamedAims registrationRevision={virtualFixtureDocument?.binding?.registration_version ?? null} />}
            notice={virtualAimNotice} sourceLabel={sourceMeta.label} onSetup={openUnifiedSetup}
            canSetup={apartmentPlacementControlsEnabled}
            deskControls={<section aria-label="Desk target" className="apt-ui-channel">
              {window.HomeLightingBrainPanel && <window.HomeLightingBrainPanel brain={lightingBrain} ops={denOpsBanner}
                labels={Object.fromEntries(virtualFixtureItems.map((fixture) => [fixture.fixture_id, fixture.label]))}
                onReturnToAuto={(id) => lightingBrainRef.current?.follower.returnToAuto(id)} />}
              <button type="button" className="apt-ui-button" disabled={!virtualSpotlightsVisible} onClick={() => { setTargetMarkKind("desk"); startDeskTargetSelection("desk"); }}>Select desk surface</button>
              <button type="button" className="apt-ui-button" disabled={!virtualSpotlightsVisible} onClick={() => { setTargetMarkKind("sofa"); startDeskTargetSelection("sofa"); }}>Mark sofa</button>
              {deskTargetSelection && <>
                <p role="status" className="apt-ui-copy">{deskTargetSelection.status === "selecting"
                  ? `${deskTargetSelection.surface_ref ? "Detailed registered scan mesh. " : ""}Click the four ${targetMarkKind === "sofa" ? "sofa seat" : "desktop"} corners in order around its edge (${deskTargetSelection.corners.length}/4). Escape cancels.${deskTargetSelection.reason ? " " + ({
                    registered_surface_unavailable: "No registered scan surface at that point.",
                    registered_collision_unavailable: "Registered scan geometry is not available yet.",
                    registered_surface_no_hit: "No registered scan triangle at that point.",
                    registered_surface_raw_hit_unselected: "Scan geometry is under this point, but the displayed surface could not be selected.",
                    registered_desk_surface_binding_changed: "The scan registration changed. Clear this selection and start again.",
                    registered_desk_surface_source_changed: "The scan source changed. Clear this selection and start again.",
                    drag_is_not_a_corner: "Drag ignored. Click without moving.",
                    registered_desk_surface_corner_required: "This surface cannot be used as a desktop corner.",
                    distinct_registered_desk_corners_required: "That corner is already selected. Choose a different corner.",
                    registered_desk_normals_disagree: "The corners are on differently facing surfaces. Check the selected surface.",
                    registered_desk_span_exceeded: "The selected corners are too far apart for one desktop.",
                    convex_cyclic_registered_desk_corners_required: "Choose the remaining corner in order around the desktop edge.",
                  }[deskTargetSelection.reason] || "That point was not added. Your selected corners are still kept.") : ""}`
                  : deskTargetSelection.status === "preparing" ? "Loading the detailed registered scan mesh for desk selection. Clear selection cancels."
                  : deskTargetSelection.status === "unavailable" ? "Desk selection is unavailable: the detailed mesh or its registered identity could not be verified."
                  : deskTargetSelection.status === "complete" ? `Four corners selected. Check the numbered markers cover your whole ${targetMarkKind === "sofa" ? "sofa seat" : "desktop"}.`
                  : deskTargetSelection.status === "saved" ? (targetMarkKind === "sofa"
                      ? "Sofa mark download prepared. Keep it private beside the desk target; the brain reads only its private export."
                      : "Desk target download prepared. Keep the file private; it enables no automatic light yet.")
                  : "Selection canceled. Start again to choose the desktop."}</p>
                {deskTargetSelection.status === "complete" && <>
                  <label className="apt-ui-row apt-ui-copy"><input type="checkbox" checked={deskTargetSelection.confirmed}
                    onChange={event => setDeskTargetSelection(value => ({ ...value, confirmed: event.target.checked }))} />{targetMarkKind === "sofa" ? "These corners bound the sofa seat." : "These corners bound my whole desktop."}</label>
                  <button type="button" className="apt-ui-button" disabled={!deskTargetSelection.confirmed} onClick={saveDeskTargetSelection}>{targetMarkKind === "sofa" ? "Save sofa mark" : "Save desk target"}</button>
                </>}
                <button type="button" className="apt-ui-button" onClick={cancelDeskTargetSelection}>Clear selection</button>
              </>}
            </section>} />
        </AptHomePanelSlot>
      )}

      {!unifiedShell && virtualFixtureDocument && phase === "ready" && spatialScale === "apartment"
        && !editing && !calibCam && !liveOn && (
        <AptHomePanelSlot enabled={unifiedShell} target={shellControlsHost}><aside data-victoria-virtual-fixtures="1" style={{ position: "absolute", left: 16,
          bottom: mobile ? 112 : 74, zIndex: 12, maxWidth: "calc(100% - 32px)",
          width: 292, maxHeight: "calc(100% - 210px)", overflowY: "auto",
          padding: "10px 12px", background: "rgba(10,17,23,.9)", color: "var(--hg-fg)",
          border: "1px solid rgba(184,216,255,.25)", fontSize: 12 }}>
          <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
            <input type="checkbox" checked={virtualSpotlightsVisible}
              onChange={(event) => setVirtualSpotlightsVisible(event.target.checked)} />
            Virtual spotlights
          </label>
          <div style={{ marginTop: 5, opacity: .7, fontSize: 10 }}>
            {virtualFixtureItems.length} fixtures · 6 radial + down + robotic spot each
          </div>
          {window.HomeLightingBrainPanel && <window.HomeLightingBrainPanel brain={lightingBrain} ops={denOpsBanner}
            labels={Object.fromEntries(virtualFixtureItems.map((fixture) => [fixture.fixture_id, fixture.label]))}
            onReturnToAuto={(id) => lightingBrainRef.current?.follower.returnToAuto(id)} />}
          {virtualSpotlightsVisible && (
            <section aria-label="Desk target" style={{ marginTop: 10 }}>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <button type="button" onClick={() => { setTargetMarkKind("desk"); startDeskTargetSelection("desk"); }} style={{ minHeight: 34 }}>Select desk surface</button>
                <button type="button" onClick={() => { setTargetMarkKind("sofa"); startDeskTargetSelection("sofa"); }} style={{ minHeight: 34 }}>Mark sofa</button>
              </div>
              {deskTargetSelection && <>
                <p role="status" style={{ fontSize: 11, lineHeight: 1.45 }}>
                  {deskTargetSelection.status === "selecting"
                    ? `${deskTargetSelection.surface_ref ? "Detailed registered scan mesh. " : ""}Click the four ${targetMarkKind === "sofa" ? "sofa seat" : "desktop"} corners in order around its edge (${deskTargetSelection.corners.length}/4). Escape cancels.${deskTargetSelection.reason ? " " + ({
                        registered_surface_unavailable: "No registered scan surface at that point.",
                        registered_collision_unavailable: "Registered scan geometry is not available yet.",
                        registered_surface_no_hit: "No registered scan triangle at that point.",
                        registered_surface_raw_hit_unselected: "Scan geometry is under this point, but the displayed surface could not be selected.",
                        registered_desk_surface_binding_changed: "The scan registration changed. Clear this selection and start again.",
                        registered_desk_surface_source_changed: "The scan source changed. Clear this selection and start again.",
                        drag_is_not_a_corner: "Drag ignored. Click without moving.",
                        registered_desk_surface_corner_required: "This surface cannot be used as a desktop corner.",
                        distinct_registered_desk_corners_required: "That corner is already selected. Choose a different corner.",
                        registered_desk_normals_disagree: "The corners are on differently facing surfaces. Check the selected surface.",
                        registered_desk_span_exceeded: "The selected corners are too far apart for one desktop.",
                        convex_cyclic_registered_desk_corners_required: "Choose the remaining corner in order around the desktop edge.",
                      }[deskTargetSelection.reason] || "That point was not added. Your selected corners are still kept.") : ""}`
                    : deskTargetSelection.status === "preparing" ? "Loading the detailed registered scan mesh for desk selection. Clear selection cancels."
                    : deskTargetSelection.status === "unavailable" ? "Desk selection is unavailable: the detailed mesh or its registered identity could not be verified."
                    : deskTargetSelection.status === "complete" ? `Four corners selected. Check the numbered markers cover your whole ${targetMarkKind === "sofa" ? "sofa seat" : "desktop"}.`
                    : deskTargetSelection.status === "saved" ? (targetMarkKind === "sofa"
                      ? "Sofa mark download prepared. Keep it private beside the desk target; the brain reads only its private export."
                      : "Desk target download prepared. Keep the file private; it enables no automatic light yet.")
                    : "Selection canceled. Start again to choose the desktop."}
                </p>
                {deskTargetSelection.status === "complete" && <>
                  <label style={{ display: "flex", gap: 6, fontSize: 11 }}>
                    <input type="checkbox" checked={deskTargetSelection.confirmed}
                      onChange={event => setDeskTargetSelection(value => ({ ...value, confirmed: event.target.checked }))} />
                    {targetMarkKind === "sofa" ? "These corners bound the sofa seat." : "These corners bound my whole desktop."}
                  </label>
                  <button type="button" disabled={!deskTargetSelection.confirmed} onClick={saveDeskTargetSelection}
                    style={{ minHeight: 34, marginTop: 7 }}>{targetMarkKind === "sofa" ? "Save sofa mark" : "Save desk target"}</button>
                </>}
                <button type="button" onClick={cancelDeskTargetSelection} style={{ minHeight: 34, marginTop: 7 }}>Clear selection</button>
              </>}
            </section>
          )}
          {virtualSpotlightsVisible && selectedVirtualFixture && (
            <details open={virtualFixtureControlsOpen} onToggle={event => setVirtualFixtureControlsOpen(event.currentTarget.open)} style={{ marginTop: 10 }}>
              <summary style={{ cursor: "pointer", minHeight: 25 }}>Fixture controls</summary>
              <label style={{ display: "block", marginTop: 8 }}>Fixture
                <select aria-label="Virtual fixture" value={selectedVirtualFixture.fixture_id}
                  onChange={(event) => {
                    setVirtualFixtureId(event.target.value);
                    engineRef.current?.overlay.setVirtualFixtureSelected?.(event.target.value);
                  }}
                  style={{ width: "100%", marginTop: 4, minHeight: 32, background: "#10171e", color: "inherit" }}>
                  {virtualFixtureItems.map((fixture) => <option key={fixture.fixture_id} value={fixture.fixture_id}>{fixture.label}</option>)}
                </select>
              </label>
              <div style={{ fontSize: 10, opacity: .7, marginTop: 10 }}>RADIAL ZONES</div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 4, marginTop: 5 }}>
                {(selectedVirtualFixture.state?.radials || []).map((zone) => (
                  <button key={zone.number} type="button" aria-label={`Radial zone ${zone.number}`} aria-pressed={zone.on}
                    onClick={() => changeVirtualFixtureByHand({ radials: [{ number: zone.number, on: !zone.on }] })}
                    style={{ minHeight: 32, color: zone.on ? "#ffe3ae" : "#88939d", background: zone.on ? "#382f22" : "#111820",
                      border: "1px solid #5a5141" }}>{zone.number}</button>
                ))}
              </div>
              <div style={{ display: "flex", gap: 5, marginTop: 7 }}>
                {[['downward', 'Downward zone'], ['spotlight', 'Robotic spotlight']].map(([key, label]) => (
                  <button key={key} type="button" aria-label={label} aria-pressed={selectedVirtualFixture.state?.[key]?.on}
                    onClick={() => changeVirtualFixtureByHand({ [key]: { on: !selectedVirtualFixture.state?.[key]?.on } })}
                    style={{ flex: 1, minHeight: 32, fontSize: 10, color: selectedVirtualFixture.state?.[key]?.on ? "#ffe3ae" : "#88939d",
                      background: selectedVirtualFixture.state?.[key]?.on ? "#382f22" : "#111820", border: "1px solid #5a5141" }}>{label}</button>
                ))}
              </div>
              <label style={{ display: "block", marginTop: 12 }}>Adjust
                <select aria-label="Adjust channel" value={virtualFixtureChannel} onChange={(event) => setVirtualFixtureChannel(event.target.value)}
                  style={{ width: "100%", marginTop: 4, minHeight: 30, background: "#10171e", color: "inherit" }}>
                  <option value="spotlight">Robotic spotlight</option><option value="downward">Downward zone</option>
                  {[1,2,3,4,5,6].map((number) => <option key={number} value={`radial-${number}`}>Radial zone {number}</option>)}
                </select>
              </label>
              <label style={{ display: "block", marginTop: 10 }}>Brightness · {Math.round((selectedVirtualChannel?.brightness ?? 1) * 100)}%
                <input aria-label="Brightness" type="range" min="0" max="100" step="1"
                  value={Math.round((selectedVirtualChannel?.brightness ?? 1) * 100)} style={{ width: "100%" }}
                  onChange={(event) => changeVirtualChannel({ brightness: Number(event.target.value) / 100 })} />
              </label>
              <label style={{ display: "block", marginTop: 8 }}>Color temperature · {selectedVirtualChannel?.color_temp_kelvin ?? 3000} K
                <input aria-label="Color temperature" type="range" min={virtualFixtureChannel === "spotlight" ? 2700 : 2200} max="5000" step="100"
                  value={selectedVirtualChannel?.color_temp_kelvin ?? 3000} style={{ width: "100%" }}
                  onChange={(event) => changeVirtualChannel({ color_temp_kelvin: Number(event.target.value) })} />
              </label>
              {virtualFixtureChannel === "spotlight" && ['pan', 'tilt'].map((axis) => (
                <label key={axis} style={{ display: "block", marginTop: 8, textTransform: "capitalize" }}>
                  {axis} · {Math.round(selectedVirtualFixture.state?.[`${axis}_deg`] || 0)}°
                  <input aria-label={axis === 'pan' ? 'Pan' : 'Tilt'} type="range" step="1"
                    min={selectedVirtualFixture.limits?.[`${axis}_min_deg`] ?? (axis === 'pan' ? -175 : -5)}
                    max={selectedVirtualFixture.limits?.[`${axis}_max_deg`] ?? (axis === 'pan' ? 175 : 88)}
                    value={selectedVirtualFixture.state?.[`${axis}_deg`] || 0} style={{ width: "100%" }}
                    onChange={(event) => changeVirtualFixtureByHand({ [`${axis}_deg`]: Number(event.target.value) })} />
                </label>
              ))}
              <p style={{ margin: "10px 0 6px", fontSize: 11, lineHeight: 1.45 }}>Select Spot, then drag the round aim handle onto a room surface.</p>
              <div style={{ display: "flex", gap: 6 }} data-aim-history={virtualAimHistoryVersion}>
                <button type="button" disabled={!aimHistoryLength("undo")} onClick={() => restoreVirtualAimHistory("undo")}
                  style={{ minHeight: 34, flex: 1 }}>Undo aim</button>
                <button type="button" disabled={!aimHistoryLength("redo")} onClick={() => restoreVirtualAimHistory("redo")}
                  style={{ minHeight: 34, flex: 1 }}>Redo aim</button>
              </div>
              {virtualAimNotice && <div role="status" style={{ marginTop: 7, fontSize: 11 }}>{virtualAimNotice}</div>}
              <div style={{ marginTop: 8, opacity: .65, fontSize: 10 }}>Virtual controls · modeled aim and light guides</div>
            </details>
          )}
        </aside></AptHomePanelSlot>
      )}

      {/* edit mode */}
      {selectedSiteId === "victoria" && sourceMeta.kind === "equipment_unavailable"
        && spatialScale === "apartment" && phase === "ready" && (
        <aside role="status" data-victoria-equipment-unavailable="1" style={{ position: "absolute", top: 110,
          right: 14, zIndex: 12, maxWidth: 330, padding: 12, background: "rgba(10,17,23,.95)", color: "var(--hg-fg)" }}>
          Equipment measurements are unavailable. The room is still available for viewing.
          <div style={{ fontSize: 11, marginTop: 6 }}>{model.source_detail?.error}</div>
          <button type="button" onClick={() => setEquipmentReloadEpoch((n) => n + 1)} style={{ minHeight: 40, marginTop: 8 }}>Retry equipment</button>
        </aside>
      )}
      {editing && spatialScale === "apartment" && phase === "ready"
        && apartmentSceneControlsEnabled && apartmentPlacementControlsEnabled && window.HomeApartmentEdit && (
        <window.HomeApartmentEdit
          engine={engineRef.current}
          model={model}
          initialTargetId={editorTargetEntry && editorTargetEntry.engine === engineRef.current
            && editorTargetEntry.siteId === selectedSiteId && editorTargetEntry.authority === apartmentAuthorityKey
            && editorTargetEntry.frame === modelScopeRef.current?.frameDigest
            && editorTargetEntry.registration === modelScopeRef.current?.registrationVersion
            && editorTargetEntry.revision === selectedContentRevision ? editorTargetEntry.id : null}
          onModel={setModel}
          seedModel={seedModel}
          registry={registry}
          onSave={saveModel}
          onDirtyChange={markModelDirty}
          onExit={() => setEditing(false)}
          sim={simActive}
          connection={connection}
          sourceMeta={sourceMeta}
          saveStatus={saveStatus}
          saving={saving}
          liveReview={liveReview}
          liveComparison={liveComparison}
          onCompareLive={compareLiveModel}
          mobile={mobile}
          inspectorHost={unifiedShell ? shellControlsHost : null}
          homeName={selectedSiteName}
          onCalibrateCamera={calibrateFromSetup}
          onFlashLight={flashFromSetup}
        />
      )}

      {!rehearsalIsolated && !editing && !aimMode && spatialScale === "apartment" && window.HomeLightingTargetPanel && (
        <window.HomeLightingTargetPanel engine={engineRef.current} model={model} siteId={selectedSiteId} />
      )}
      {!rehearsalIsolated && !editing && !aimMode && spatialScale === "apartment" && window.HomeLightingLivePanel && (
        <window.HomeLightingLivePanel siteId={selectedSiteId} roomId="den" />
      )}
      {mapRehearsalOpen && window.HomeMapRehearsalPanel && (
        <window.HomeMapRehearsalPanel engine={engineRef.current} model={model}
          phase={phase} error={mapRehearsalError} onClose={onCloseMapRehearsal} />
      )}
      {mapRehearsalOpen && !window.HomeMapRehearsalPanel && (
        <aside role="alert" style={{ position: 'absolute', right: 14, top: 75, zIndex: 12, padding: 16, background: '#101b24', color: '#edf3f6' }}>
          The Apartment rehearsal controls are unavailable.
          <button type="button" style={{ display: 'block', minHeight: 40 }} onClick={() => { window.HomeMapRehearsalOfflineState?.release?.(); onCloseMapRehearsal?.(); }}>Return to Apartment</button>
        </aside>
      )}
      {!mapRehearsalOpen && window.HomeMapRehearsalOfflineState?.cleanupPending?.() && (
        <div data-home-map-rehearsal-cleanup="unconfirmed" role="status" style={{ position: 'absolute', top: 75, right: 14, zIndex: 12, width: 'min(340px, calc(100% - 28px))', padding: 12, color: '#edf3f6', background: 'rgba(10,17,23,.96)', border: '1px solid #bd934e', borderRadius: 6 }}>
          Local rehearsal cleanup is unconfirmed. The Apartment preview stays read-only.
          <button type="button" onClick={onOpenDynamicReplay} style={{ display: 'block', minHeight: 40, marginTop: 8 }}>Reconcile local cleanup</button>
        </div>
      )}
      {!mapRehearsalOpen && window.HomeMapRehearsalOfflineState?.readback?.().preview_released && (
        <div data-home-map-rehearsal-cleanup="joined" role="status" style={{ position: 'absolute', top: 75, right: 14, zIndex: 12, width: 'min(340px, calc(100% - 28px))', padding: 12, color: '#edf3f6', background: 'rgba(10,17,23,.96)', border: '1px solid #657587', borderRadius: 6 }}>
          Rehearsal closed. The Apartment remains a local preview; live services stay suspended until you use the existing reconnect action or reload Home.
        </div>
      )}

      <window.HomeApartmentUI.Toast toast={toast} onDismiss={dismissToast} />
    </div>
  );
}

/* Local product navigation owns the replay without forwarding live credentials. */
function HomeApartmentProductView(props) {
  const [replayOpen, setReplayOpen] = useState(false);
  const [replayError, setReplayError] = useState("");
  const openRehearsal = () => {
    if (window.HomeLiveSceneShadow?.active) return;
    setReplayError("");
    try {
      const resources = window.__HOME_MAP_REHEARSAL_LOCAL_RESOURCES || [
        ...(window.__HOME_BOOT_FILES || []).map(name => "/" + name),
        "/home-3d/map-rehearsal-layer.mjs", "/home-3d/markers.js",
        "/vendor/three-0.180.0/three.module.js", "/vendor/three-0.180.0/three.core.js",
      ];
      window.HomeMapRehearsalOffline?.install(window, resources, {
        permanent: false,
        apiBase: window.__HOME_MAP_REHEARSAL_BASE_URL ?? "http://127.0.0.1:5193",
      });
    } catch (e) { setReplayError(e.message); }
    setReplayOpen(true);
  };
  useEffect(() => { if (!props.open) setReplayOpen(false); }, [props.open]);
  return React.createElement(HomeApartmentLiveView, {
    ...props,
    onOpenDynamicReplay: typeof window.HomeMapRehearsalOffline?.install === "function"
      && typeof window.HomeMapRehearsalUI?.close === "function" && window.HomeMapRehearsalPanel
      ? openRehearsal : undefined,
    mapRehearsalOpen: replayOpen,
    mapRehearsalError: replayError,
    onCloseMapRehearsal: () => setReplayOpen(false),
  });
}

/* APT-S02: the status popover's lights row from facts this home already has.
 * Light entities come from this home's own model only, so a home never shows
 * another home's Home Assistant state. */
function aptShellLightFacts(model, modeledFixtures, roomId, connection) {
  const lightEntities = (model?.devices || []).filter(device => typeof device?.ha_entity_id === "string"
    && device.ha_entity_id.startsWith("light.")).length;
  const zone = roomId && (model?.zones || []).find(item => item?.id === roomId);
  const lightsRoom = zone?.name || (typeof roomId === "string" && roomId ? roomId[0].toUpperCase() + roomId.slice(1) : "");
  return { lightEntities, modeledFixtures, lightsRoom, haConnection: connection };
}

/* Separate components keep the explicit test branch ahead of every live hook. */
function HomeApartmentView(props) {
  if (props.demoMode === "dynamic-synthetic") {
    if (!window.HomeLightingDynamicPanel) return React.createElement("div", { role: "alert" }, "Synthetic replay viewer modules are unavailable.");
    return React.createElement(window.HomeLightingDynamicPanel, props);
  }
  return React.createElement(HomeApartmentProductView, props);
}

window.HomeApartmentView = HomeApartmentView;
