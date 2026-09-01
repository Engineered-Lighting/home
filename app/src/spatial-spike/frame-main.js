import { SCALE_STOPS, SYNTHETIC_SITES, DEFAULT_ENVIRONMENT } from "./fixtures.js";
import { createCandidateAdapter } from "./adapter-loader.js";
import {
  afterVisualSettlement,
  installRuntimeInstrumentation,
  memorySample,
  resourceTotals,
  startFrameSampler,
  summarizeDurations,
} from "./benchmark.js";
import {
  FRAME_ERROR_CODES,
  FRAME_TO_HOST,
  HOST_TO_FRAME,
  createEnvelope,
  isConnectionEnvelope,
  parseEnvelope,
} from "./protocol.js";
import {
  advanceJourney,
  cancelJourney,
  createSpatialState,
  moveSiteFocus,
  publicSnapshot,
  replaceSites,
  selectSite,
  setEnvironment,
  setReducedMotion,
  startJourney,
} from "./state.js";

const elements = {
  surface: document.getElementById("renderer-surface"),
  rendererStatus: document.getElementById("renderer-status"),
  rendererReadout: document.getElementById("renderer-readout"),
  startupReadout: document.getElementById("startup-readout"),
  runLifecycle: document.getElementById("run-lifecycle"),
  benchmarkResult: document.getElementById("benchmark-result"),
  siteList: document.getElementById("site-list"),
  scaleRail: document.getElementById("scale-rail"),
  scaleReadout: document.getElementById("scale-readout"),
  networkStatus: document.getElementById("network-status"),
  providerStatus: document.getElementById("provider-status"),
  degradedBanner: document.getElementById("degraded-banner"),
  selectedReadout: document.getElementById("selected-readout"),
  ownerReadout: document.getElementById("owner-readout"),
  aiReadout: document.getElementById("ai-readout"),
  motionReadout: document.getElementById("motion-readout"),
  journeyProgress: document.getElementById("journey-progress"),
  liveRegion: document.getElementById("live-region"),
};

let state = createSpatialState({ sites: SYNTHETIC_SITES, environment: DEFAULT_ENVIRONMENT });
let port = null;
let frameSequence = 0;
let animationFrame = null;
let previousAnimationTime = null;
let lastAnnouncedRevision = -1;
let activeAdapterId = "deterministic-dom";
let coldUsefulMs = null;
let benchmarkJourneyResolve = null;
let benchmarkRunning = false;
const siteButtonById = new Map();
const scaleStopById = new Map();
const runtimeInstrumentation = installRuntimeInstrumentation();

const nextId = (prefix) => `${prefix}-${String(++frameSequence).padStart(4, "0")}`;

const ADAPTER_LABELS = Object.freeze({
  "deterministic-dom": "Deterministic fallback",
  "cesium-separate": "CesiumJS 1.144.0",
  "maplibre-sandbox": "MapLibre GL JS 6.6.0",
});

function nativeAuthorityVisible() {
  const authorityNames = [
    ["__", "TAURI", "__"],
    ["__", "TAURI", "_INTERNALS__"],
  ].map((parts) => parts.join(""));
  return authorityNames.some((name) => Object.hasOwn(globalThis, name));
}

function adapterOptions() {
  return {
    onActivateSite: (siteId) => activateSite(siteId, { focus: false, emit: true }),
  };
}

let adapter = createCandidateAdapter("deterministic-dom", adapterOptions());
adapter.mount(elements.surface);
adapter.setSites(state.sites);

async function mountAdapter(adapterId, { disposeCurrent = true } = {}) {
  if (nativeAuthorityVisible()) throw new Error(FRAME_ERROR_CODES.RUNTIME_AUTHORITY_EXPOSED);
  if (disposeCurrent) {
    adapter.dispose();
    await afterVisualSettlement(1);
  }
  const nextAdapter = createCandidateAdapter(adapterId, adapterOptions());
  const started = performance.now();
  try {
    await nextAdapter.mount(elements.surface);
    nextAdapter.setSites(state.sites);
    nextAdapter.render(publicSnapshot(state));
    await afterVisualSettlement(2);
  } catch (error) {
    nextAdapter.dispose();
    throw error;
  }
  adapter = nextAdapter;
  activeAdapterId = adapterId;
  coldUsefulMs = performance.now() - started;
  elements.surface.dataset.adapter = adapterId;
  return adapter;
}

async function restoreDeterministicAdapter() {
  adapter = createCandidateAdapter("deterministic-dom", adapterOptions());
  await adapter.mount(elements.surface);
  adapter.setSites(state.sites);
  adapter.render(publicSnapshot(state));
  activeAdapterId = "deterministic-dom";
  coldUsefulMs = null;
  elements.surface.dataset.adapter = activeAdapterId;
}

function emit(type, payload, requestId = nextId("frame")) {
  if (!port) return;
  port.postMessage(createEnvelope(type, requestId, payload));
}

function emitState(requestId = nextId("state"), type = FRAME_TO_HOST.STATE) {
  emit(type, publicSnapshot(state), requestId);
}

function emitError(code, requestId = nextId("error")) {
  emit(FRAME_TO_HOST.ERROR, { code }, requestId);
}

function renderScaleRail() {
  if (scaleStopById.size === 0) {
    for (const stop of SCALE_STOPS) {
      const item = document.createElement("li");
      item.className = "range-stop";
      item.textContent = stop.label;
      item.dataset.scale = stop.id;
      elements.scaleRail.append(item);
      scaleStopById.set(stop.id, item);
    }
  }
  scaleStopById.forEach((item, scaleId) => item.classList.toggle("is-current", scaleId === state.scale));
}

function makeSiteButton(site) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "site-option";
  button.dataset.siteId = site.id;
  button.setAttribute("role", "option");
  const label = document.createElement("span");
  label.className = "site-option-label";
  const name = document.createElement("strong");
  name.textContent = site.label;
  const meta = document.createElement("small");
  meta.textContent = `${site.countryCode} · synthetic`;
  label.append(name, meta);
  button.append(label);
  button.addEventListener("click", () => activateSite(site.id, { focus: true, emit: true }));
  button.addEventListener("keydown", handleSiteKeydown);
  return button;
}

function renderSites() {
  const expectedIds = new Set(state.sites.map((site) => site.id));
  for (const [siteId, button] of siteButtonById) {
    if (!expectedIds.has(siteId)) {
      button.remove();
      siteButtonById.delete(siteId);
    }
  }
  for (const site of state.sites) {
    let button = siteButtonById.get(site.id);
    if (!button) {
      button = makeSiteButton(site);
      siteButtonById.set(site.id, button);
      elements.siteList.append(button);
    }
    const selected = site.id === state.selectedSiteId;
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = site.id === state.focusedSiteId ? 0 : -1;
    button.dataset.health = state.environment.siteHealth[site.id] || "unknown";
  }
}

function renderEnvironment() {
  const { environment } = state;
  elements.networkStatus.textContent = `Network ${environment.network}`;
  elements.networkStatus.className = `status-chip ${environment.network === "offline" ? "is-offline" : ""}`;
  elements.providerStatus.textContent = `Provider ${environment.provider}`;
  elements.providerStatus.className = `status-chip ${environment.provider === "live" ? "" : "is-degraded"}`;

  let degradedMessage = "";
  if (environment.network === "offline") {
    degradedMessage = "Offline: this fixture keeps cached navigation and disables all external data paths.";
  } else if (environment.provider === "degraded") {
    degradedMessage = "Provider degraded: last-good world context remains visible and is not presented as live.";
  } else if (environment.provider === "unavailable") {
    degradedMessage = "Provider unavailable: synthetic fallback geometry remains navigable.";
  } else if (environment.ai === "offline") {
    degradedMessage = "AI unavailable: navigation and site selection remain independent.";
  } else if (Object.values(environment.siteHealth).some((health) => health !== "online")) {
    degradedMessage = "One home is offline. Other homes and world navigation remain available.";
  }
  elements.degradedBanner.hidden = !degradedMessage;
  elements.degradedBanner.textContent = degradedMessage;
}

function render() {
  const snapshot = publicSnapshot(state);
  adapter.render(snapshot);
  renderScaleRail();
  renderSites();
  renderEnvironment();
  const selected = state.sites.find((site) => site.id === state.selectedSiteId);
  const adapterLabel = ADAPTER_LABELS[activeAdapterId] || activeAdapterId;
  elements.rendererStatus.textContent = adapterLabel;
  elements.rendererReadout.textContent = adapterLabel;
  elements.startupReadout.textContent = coldUsefulMs === null ? "Fixture" : `${Math.round(coldUsefulMs)} ms`;
  elements.scaleReadout.textContent = SCALE_STOPS.find((stop) => stop.id === state.scale)?.label || state.scale;
  elements.selectedReadout.textContent = selected?.label || "Unknown fixture";
  elements.ownerReadout.textContent = state.owner === "world"
    ? "World adapter"
    : state.owner === "coordinator"
      ? "Scene coordinator"
      : "Interior boundary";
  elements.aiReadout.textContent = state.environment.ai === "online" ? "Available" : "Unavailable";
  elements.motionReadout.textContent = state.reducedMotion ? "Reduced" : "Standard";
  elements.journeyProgress.textContent = state.journey.status === "running"
    ? `${Math.round(state.journey.progress * 100)}% · ${state.journey.playback}`
    : state.journey.status;
  if (lastAnnouncedRevision !== state.revision) {
    elements.liveRegion.textContent = state.announcement;
    lastAnnouncedRevision = state.revision;
  }
}

function focusCurrentSite() {
  siteButtonById.get(state.focusedSiteId)?.focus();
}

function handleSiteKeydown(event) {
  const direction = {
    ArrowDown: "next",
    ArrowRight: "next",
    ArrowUp: "previous",
    ArrowLeft: "previous",
    Home: "first",
    End: "last",
  }[event.key];
  if (direction) {
    event.preventDefault();
    state = moveSiteFocus(state, direction);
    render();
    focusCurrentSite();
    return;
  }
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    activateSite(event.currentTarget.dataset.siteId, { focus: true, emit: true });
  }
}

function activateSite(siteId, { focus, emit: shouldEmit }) {
  try {
    state = selectSite(state, siteId, { moveFocus: focus });
    render();
    if (focus) focusCurrentSite();
    if (shouldEmit) {
      emit(FRAME_TO_HOST.EVENT, { kind: "site-selected", siteId });
      emitState();
    }
  } catch {
    emitError(FRAME_ERROR_CODES.UNKNOWN_SITE);
  }
}

function stopAnimation() {
  if (animationFrame !== null) cancelAnimationFrame(animationFrame);
  animationFrame = null;
  previousAnimationTime = null;
}

function animationTick(now) {
  if (state.journey.status !== "running" || state.journey.playback !== "auto") {
    stopAnimation();
    return;
  }
  const deltaMs = previousAnimationTime === null ? 0 : Math.min(80, now - previousAnimationTime);
  previousAnimationTime = now;
  const previousKeyframe = state.journey.keyframeIndex;
  state = advanceJourney(state, { deltaMs });
  render();
  if (state.journey.keyframeIndex !== previousKeyframe || state.journey.status !== "running") {
    emit(FRAME_TO_HOST.EVENT, {
      kind: state.journey.status === "completed" ? "journey-completed" : "journey-keyframe",
      siteId: state.selectedSiteId,
      intentId: state.journey.intentId,
      scale: state.scale,
    });
    emitState();
  }
  if (state.journey.status === "running") animationFrame = requestAnimationFrame(animationTick);
  else {
    stopAnimation();
    const resolveJourney = benchmarkJourneyResolve;
    benchmarkJourneyResolve = null;
    resolveJourney?.();
  }
}

function beginJourney({ intentId, siteId, destination, playback }) {
  stopAnimation();
  try {
    state = startJourney(state, { intentId, siteId, destination, playback });
    render();
    emit(FRAME_TO_HOST.EVENT, { kind: "journey-started", siteId, intentId, scale: state.scale });
    emitState();
    if (state.journey.status === "running" && playback === "auto") {
      animationFrame = requestAnimationFrame(animationTick);
    }
  } catch {
    emitError(FRAME_ERROR_CODES.INVALID_NAVIGATION);
  }
}

function stepJourney(intentId) {
  if (intentId && intentId !== state.journey.intentId) return;
  const previousKeyframe = state.journey.keyframeIndex;
  state = advanceJourney(state, { nextKeyframe: true });
  render();
  if (state.journey.keyframeIndex !== previousKeyframe) {
    emit(FRAME_TO_HOST.EVENT, {
      kind: state.journey.status === "completed" ? "journey-completed" : "journey-keyframe",
      siteId: state.selectedSiteId,
      intentId: state.journey.intentId,
      scale: state.scale,
    });
    emitState();
  }
}

async function initializeRenderer(payload, requestId) {
  stopAnimation();
  state = createSpatialState(payload);
  try {
    await mountAdapter(payload.adapterId);
  } catch (error) {
    await restoreDeterministicAdapter();
    render();
    const code = error?.message === FRAME_ERROR_CODES.RUNTIME_AUTHORITY_EXPOSED
      ? FRAME_ERROR_CODES.RUNTIME_AUTHORITY_EXPOSED
      : FRAME_ERROR_CODES.ADAPTER_LOAD_FAILED;
    emitError(code, requestId);
    return;
  }
  render();
  emit(FRAME_TO_HOST.READY, { adapterId: activeAdapterId, fixtureOnly: true }, requestId);
  emitState(requestId);
}

async function measureCameraJourney() {
  const restoreReducedMotion = state.reducedMotion;
  if (restoreReducedMotion) state = setReducedMotion(state, false);
  const sampler = startFrameSampler();
  const completion = new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      benchmarkJourneyResolve = null;
      sampler.stop();
      reject(new Error("camera-sample-timeout"));
    }, 5_000);
    benchmarkJourneyResolve = () => {
      clearTimeout(timeout);
      resolve(sampler.stop());
    };
  });
  beginJourney({
    intentId: nextId("benchmark-intent"),
    siteId: state.selectedSiteId,
    destination: "interior",
    playback: "auto",
  });
  try {
    return await completion;
  } finally {
    if (restoreReducedMotion) {
      state = setReducedMotion(state, true);
      render();
    }
  }
}

async function runLifecycleBenchmark() {
  if (benchmarkRunning) return;
  benchmarkRunning = true;
  const buttons = [...document.querySelectorAll("button")];
  const disabledBefore = buttons.map((button) => button.disabled);
  buttons.forEach((button) => { button.disabled = true; });
  elements.benchmarkResult.dataset.status = "running";
  elements.benchmarkResult.textContent = "Sampling one camera journey and 20 dispose/recreate cycles…";

  const candidateId = activeAdapterId;
  const initialColdUseful = coldUsefulMs;
  try {
    const frameTimes = await measureCameraJourney();
    const before = runtimeInstrumentation.snapshot();
    const memoryBefore = memorySample();
    const resourceStart = performance.now();
    const warmUseful = [];

    for (let cycle = 0; cycle < 20; cycle += 1) {
      await mountAdapter(candidateId);
      warmUseful.push(coldUsefulMs);
    }

    const resources = resourceTotals(performance, resourceStart);
    const after = runtimeInstrumentation.snapshot();
    const memoryAfter = memorySample();
    const warm = summarizeDurations(warmUseful);
    const workerDelta = after.tracking.workers
      ? after.workers.active - before.workers.active
      : null;
    const contextDelta = after.tracking.webgl
      ? after.webgl.live - before.webgl.live
      : null;
    const heapDelta = memoryBefore.supported && memoryAfter.supported
      ? memoryAfter.usedHeapBytes - memoryBefore.usedHeapBytes
      : null;
    const concerns = resources.otherHostEntries > 0
      || workerDelta === null
      || contextDelta === null
      || workerDelta !== 0
      || contextDelta !== 0;
    elements.benchmarkResult.dataset.status = concerns ? "attention" : "sampled";
    elements.benchmarkResult.textContent = [
      `${ADAPTER_LABELS[candidateId]} — 20 warm recreations`,
      `warm useful p95 ${warm.p95Ms ?? "n/a"} ms`,
      `camera p95 frame ${frameTimes.p95Ms ?? "n/a"} ms (${frameTimes.fpsAtP95 ?? "n/a"} FPS)`,
      `active workers Δ ${workerDelta ?? "unavailable"}`,
      `live WebGL contexts Δ ${contextDelta ?? "unavailable"}`,
      `other-host resources ${resources.otherHostEntries}`,
      `decoded runtime bytes ${resources.decodedBytes}`,
      `JS heap Δ ${heapDelta ?? "unsupported"}`,
      "Confirm process memory and GPU budget with WPR/ETW.",
    ].join(" · ");
    coldUsefulMs = initialColdUseful;
    render();
  } catch {
    elements.benchmarkResult.dataset.status = "attention";
    elements.benchmarkResult.textContent = "Lifecycle sample failed. The candidate remains unqualified; inspect the local native trace.";
    emitError(FRAME_ERROR_CODES.BENCHMARK_FAILED);
    if (!adapter?.getSnapshot?.()?.mounted) await restoreDeterministicAdapter();
    render();
  } finally {
    benchmarkRunning = false;
    buttons.forEach((button, index) => { button.disabled = disabledBefore[index]; });
  }
}

async function handleHostEnvelope(envelope) {
  const { type, payload, requestId } = envelope;
  try {
    switch (type) {
      case HOST_TO_FRAME.INIT:
        await initializeRenderer(payload, requestId);
        break;
      case HOST_TO_FRAME.SET_SITES:
        state = replaceSites(state, payload.sites);
        adapter.setSites(state.sites);
        render();
        emitState(requestId);
        break;
      case HOST_TO_FRAME.SET_ENVIRONMENT:
        state = setEnvironment(state, payload.environment);
        render();
        emitState(requestId);
        break;
      case HOST_TO_FRAME.SET_REDUCED_MOTION:
        state = setReducedMotion(state, payload.reducedMotion);
        render();
        emitState(requestId);
        break;
      case HOST_TO_FRAME.NAVIGATE:
        beginJourney(payload);
        break;
      case HOST_TO_FRAME.ADVANCE_JOURNEY:
        stepJourney(payload.intentId);
        break;
      case HOST_TO_FRAME.CANCEL_JOURNEY:
        state = cancelJourney(state, payload.intentId);
        stopAnimation();
        render();
        emitState(requestId);
        break;
      case HOST_TO_FRAME.REQUEST_SNAPSHOT:
        emitState(requestId, FRAME_TO_HOST.SNAPSHOT);
        break;
      default:
        emitError("unsupported-command", requestId);
    }
  } catch {
    emitError("command-failed", requestId);
  }
}

function handlePortMessage(event) {
  const parsed = parseEnvelope(event.data, "host-to-frame");
  if (!parsed.ok) {
    emitError(parsed.code);
    return;
  }
  void handleHostEnvelope(parsed.value);
}

window.addEventListener("message", (event) => {
  if (event.source !== parent || !isConnectionEnvelope(event.data) || event.ports.length !== 1) return;
  if (port) port.close();
  port = event.ports[0];
  port.onmessage = handlePortMessage;
  port.onmessageerror = () => emitError("message-unreadable");
  port.start();
  emit(FRAME_TO_HOST.READY, { adapterId: adapter.id, fixtureOnly: true }, event.data.requestId);
});

document.getElementById("enter-home").addEventListener("click", () => {
  beginJourney({
    intentId: nextId("local-intent"),
    siteId: state.selectedSiteId,
    destination: "interior",
    playback: "auto",
  });
});
document.getElementById("return-planet").addEventListener("click", () => {
  beginJourney({
    intentId: nextId("local-intent"),
    siteId: state.selectedSiteId,
    destination: "planet",
    playback: "auto",
  });
});
document.getElementById("step-journey").addEventListener("click", () => stepJourney(state.journey.intentId));
document.getElementById("cancel-journey").addEventListener("click", () => {
  if (!state.journey.intentId) return;
  state = cancelJourney(state, state.journey.intentId);
  stopAnimation();
  render();
  emitState();
});
elements.runLifecycle.addEventListener("click", () => {
  void runLifecycleBenchmark();
});

render();
