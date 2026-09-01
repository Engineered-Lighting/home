import { CAMERA_JOURNEYS, DEFAULT_ENVIRONMENT, SCALE_STOPS } from "./fixtures.js";

const SCALE_IDS = new Set(SCALE_STOPS.map((stop) => stop.id));
const cloneEnvironment = (environment) => ({
  network: environment.network,
  provider: environment.provider,
  core: environment.core,
  ai: environment.ai,
  siteHealth: { ...environment.siteHealth },
});

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const lerp = (from, to, progress) => from + ((to - from) * progress);

function selectedSite(state, siteId = state.selectedSiteId) {
  const site = state.sites.find((candidate) => candidate.id === siteId);
  if (!site) throw new RangeError("navigation target is not present in the fixture registry");
  return site;
}

export function sampleJourney(site, destination, elapsedMs) {
  const frames = CAMERA_JOURNEYS[destination];
  if (!frames) throw new RangeError("unknown journey destination");
  const durationMs = frames.at(-1).atMs;
  const bounded = clamp(elapsedMs, 0, durationMs);
  const nextIndex = frames.findIndex((frame) => frame.atMs >= bounded);
  const upperIndex = nextIndex < 0 ? frames.length - 1 : nextIndex;
  const lowerIndex = Math.max(0, upperIndex - 1);
  const lower = frames[lowerIndex];
  const upper = frames[upperIndex];
  const span = Math.max(1, upper.atMs - lower.atMs);
  const progress = lowerIndex === upperIndex ? 0 : (bounded - lower.atMs) / span;
  const anchorBlend = lerp(lower.anchorBlend, upper.anchorBlend, progress);
  return {
    elapsedMs: bounded,
    durationMs,
    keyframeIndex: upperIndex,
    scale: lowerIndex === upperIndex || progress < 0.5 ? lower.scale : upper.scale,
    owner: lowerIndex === upperIndex || progress < 0.5 ? lower.owner : upper.owner,
    progress: durationMs === 0 ? 1 : bounded / durationMs,
    camera: {
      latitude: site.anchor.latitude * anchorBlend,
      longitude: site.anchor.longitude * anchorBlend,
      heightKm: lerp(lower.heightKm, upper.heightKm, progress),
      pitchDeg: lerp(lower.pitchDeg, upper.pitchDeg, progress),
      bearingDeg: lerp(lower.bearingDeg, upper.bearingDeg, progress),
    },
  };
}

export function createSpatialState({
  sites,
  environment = DEFAULT_ENVIRONMENT,
  reducedMotion = false,
} = {}) {
  if (!Array.isArray(sites) || sites.length === 0) throw new TypeError("at least one synthetic site is required");
  const firstSite = sites[0];
  const initialSample = sampleJourney(firstSite, "interior", 0);
  return {
    revision: 0,
    sites: sites.map((site) => ({ ...site, anchor: { ...site.anchor }, capabilities: [...site.capabilities] })),
    selectedSiteId: firstSite.id,
    focusedSiteId: firstSite.id,
    scale: initialSample.scale,
    owner: initialSample.owner,
    camera: initialSample.camera,
    environment: cloneEnvironment(environment),
    reducedMotion: Boolean(reducedMotion),
    journey: {
      status: "idle",
      intentId: null,
      destination: "planet",
      playback: "manual",
      elapsedMs: 0,
      durationMs: 0,
      keyframeIndex: 0,
      progress: 0,
    },
    announcement: "Planet view ready with synthetic fixtures.",
  };
}

export function replaceSites(state, sites) {
  if (!Array.isArray(sites) || sites.length === 0) throw new TypeError("at least one synthetic site is required");
  const nextSelected = sites.some((site) => site.id === state.selectedSiteId) ? state.selectedSiteId : sites[0].id;
  return {
    ...state,
    revision: state.revision + 1,
    sites: sites.map((site) => ({ ...site, anchor: { ...site.anchor }, capabilities: [...site.capabilities] })),
    selectedSiteId: nextSelected,
    focusedSiteId: nextSelected,
    announcement: `${sites.length} synthetic sites loaded.`,
  };
}

export function selectSite(state, siteId, { moveFocus = true } = {}) {
  const site = selectedSite(state, siteId);
  return {
    ...state,
    revision: state.revision + 1,
    selectedSiteId: site.id,
    focusedSiteId: moveFocus ? site.id : state.focusedSiteId,
    announcement: `${site.label} selected.`,
  };
}

export function moveSiteFocus(state, direction) {
  const currentIndex = Math.max(0, state.sites.findIndex((site) => site.id === state.focusedSiteId));
  let nextIndex = currentIndex;
  if (direction === "next") nextIndex = (currentIndex + 1) % state.sites.length;
  else if (direction === "previous") nextIndex = (currentIndex - 1 + state.sites.length) % state.sites.length;
  else if (direction === "first") nextIndex = 0;
  else if (direction === "last") nextIndex = state.sites.length - 1;
  else throw new RangeError("unknown focus direction");
  return {
    ...state,
    focusedSiteId: state.sites[nextIndex].id,
  };
}

export function startJourney(state, intent) {
  const site = selectedSite(state, intent.siteId);
  const frames = CAMERA_JOURNEYS[intent.destination];
  if (!frames) throw new RangeError("unknown journey destination");
  const durationMs = frames.at(-1).atMs;
  const matchingFrame = frames.find((frame) => frame.scale === state.scale);
  const reversedInFlight = state.journey.status === "running"
    && state.journey.destination !== intent.destination;
  const resumedElapsedMs = state.journey.status === "running" && state.journey.destination === intent.destination
    ? state.journey.elapsedMs
    : reversedInFlight
      ? durationMs - state.journey.elapsedMs
      : matchingFrame?.atMs ?? 0;
  const elapsedMs = state.reducedMotion ? durationMs : resumedElapsedMs;
  const sample = sampleJourney(site, intent.destination, elapsedMs);
  const completed = state.reducedMotion || sample.elapsedMs >= durationMs;
  return {
    ...state,
    revision: state.revision + 1,
    selectedSiteId: site.id,
    focusedSiteId: site.id,
    scale: sample.scale,
    owner: sample.owner,
    camera: sample.camera,
    journey: {
      status: completed ? "completed" : "running",
      intentId: intent.intentId,
      destination: intent.destination,
      playback: intent.playback,
      elapsedMs: sample.elapsedMs,
      durationMs,
      keyframeIndex: sample.keyframeIndex,
      progress: sample.progress,
    },
    announcement: completed
      ? `${site.label}: ${sample.scale} view selected without animation.`
      : `${site.label}: journey to ${intent.destination} started.`,
  };
}

export function advanceJourney(state, { deltaMs = 0, nextKeyframe = false } = {}) {
  if (state.journey.status !== "running") return state;
  const frames = CAMERA_JOURNEYS[state.journey.destination];
  let elapsedMs = state.journey.elapsedMs + Math.max(0, deltaMs);
  if (nextKeyframe) {
    const next = frames.find((frame) => frame.atMs > state.journey.elapsedMs);
    elapsedMs = next ? next.atMs : state.journey.durationMs;
  }
  const site = selectedSite(state);
  const sample = sampleJourney(site, state.journey.destination, elapsedMs);
  const completed = sample.elapsedMs >= sample.durationMs;
  return {
    ...state,
    revision: state.revision + 1,
    scale: sample.scale,
    owner: sample.owner,
    camera: sample.camera,
    journey: {
      ...state.journey,
      status: completed ? "completed" : "running",
      elapsedMs: sample.elapsedMs,
      keyframeIndex: sample.keyframeIndex,
      progress: sample.progress,
    },
    announcement: completed
      ? `${site.label}: ${sample.scale} view ready.`
      : `${site.label}: ${sample.scale} scale.`,
  };
}

export function cancelJourney(state, intentId) {
  if (state.journey.intentId !== intentId || state.journey.status !== "running") return state;
  return {
    ...state,
    revision: state.revision + 1,
    journey: { ...state.journey, status: "cancelled" },
    announcement: "Camera journey cancelled at the current scale.",
  };
}

export function setEnvironment(state, environment) {
  return {
    ...state,
    revision: state.revision + 1,
    environment: cloneEnvironment(environment),
    announcement: environment.network === "offline"
      ? "Offline fixture active. Cached spatial navigation remains available."
      : environment.provider === "degraded"
        ? "Provider-degraded fixture active. Last-good world context is shown."
        : environment.ai === "offline"
          ? "AI unavailable. Spatial navigation remains available."
          : "Nominal fixture restored.",
  };
}

export function setReducedMotion(state, reducedMotion) {
  const enabled = Boolean(reducedMotion);
  if (state.reducedMotion === enabled) return state;
  const next = { ...state, reducedMotion: enabled, revision: state.revision + 1 };
  if (enabled && state.journey.status === "running") {
    const site = selectedSite(state);
    const sample = sampleJourney(site, state.journey.destination, state.journey.durationMs);
    return {
      ...next,
      scale: sample.scale,
      owner: sample.owner,
      camera: sample.camera,
      journey: {
        ...state.journey,
        status: "completed",
        elapsedMs: sample.durationMs,
        keyframeIndex: sample.keyframeIndex,
        progress: 1,
      },
      announcement: `${site.label}: destination selected without animation.`,
    };
  }
  return {
    ...next,
    announcement: enabled ? "Reduced motion enabled." : "Reduced motion disabled.",
  };
}

export function publicSnapshot(state) {
  if (!SCALE_IDS.has(state.scale)) throw new RangeError("state contains an unknown scale");
  return {
    revision: state.revision,
    selectedSiteId: state.selectedSiteId,
    focusedSiteId: state.focusedSiteId,
    scale: state.scale,
    owner: state.owner,
    reducedMotion: state.reducedMotion,
    environment: cloneEnvironment(state.environment),
    sites: state.sites.map((site) => ({
      id: site.id,
      label: site.label,
      countryCode: site.countryCode,
      availability: state.environment.siteHealth[site.id] || site.availability,
    })),
    camera: {
      heightKm: Number(state.camera.heightKm.toFixed(3)),
      pitchDeg: Number(state.camera.pitchDeg.toFixed(2)),
      bearingDeg: Number(state.camera.bearingDeg.toFixed(2)),
    },
    journey: { ...state.journey },
    announcement: state.announcement,
  };
}
