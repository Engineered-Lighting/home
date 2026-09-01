import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { CANDIDATE_RUNTIME_MANIFEST, assertCandidateAdapter } from "../candidate-adapter.js";
import { installRuntimeInstrumentation } from "../benchmark.js";
import { ENVIRONMENT_PRESETS, SCALE_STOPS, SYNTHETIC_SITES } from "../fixtures.js";
import {
  FRAME_TO_HOST,
  HOST_TO_FRAME,
  RENDERER_ADAPTER_IDS,
  createConnectionEnvelope,
  createEnvelope,
  isConnectionEnvelope,
  parseEnvelope,
  validateSyntheticSite,
} from "../protocol.js";
import {
  advanceJourney,
  createSpatialState,
  publicSnapshot,
  setEnvironment,
  setReducedMotion,
  startJourney,
} from "../state.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const spikeRoot = path.resolve(here, "..");
let passed = 0;
let failed = 0;

function test(name, body) {
  try {
    body();
    passed += 1;
    console.log(`  PASS  ${name}`);
  } catch (error) {
    failed += 1;
    console.error(`  FAIL  ${name}`);
    console.error(`        ${error.message}`);
  }
}

const validInit = () => createEnvelope(HOST_TO_FRAME.INIT, "test-init", {
  sites: SYNTHETIC_SITES,
  environment: ENVIRONMENT_PRESETS.nominal,
  reducedMotion: false,
  adapterId: "deterministic-dom",
});

test("fixtures are synthetic US/Canada records only", () => {
  assert.deepEqual(SYNTHETIC_SITES.map((site) => site.countryCode), ["US", "CA"]);
  assert.ok(SYNTHETIC_SITES.every(validateSyntheticSite));
  assert.ok(SYNTHETIC_SITES.every((site) => site.privacyClass === "synthetic"));
  assert.ok(SYNTHETIC_SITES.every((site) => site.anchor.accuracyMeters >= 1000));
});

test("camera fixture covers planet through interior boundary", () => {
  assert.equal(SCALE_STOPS[0].id, "planet");
  assert.equal(SCALE_STOPS.at(-1).id, "interior");
  assert.equal(SCALE_STOPS.find((stop) => stop.id === "handoff").owner, "coordinator");
});

test("valid synthetic INIT passes the host-to-frame schema", () => {
  assert.equal(parseEnvelope(validInit(), "host-to-frame").ok, true);
});

test("parser rejects unknown top-level and payload fields", () => {
  const topLevel = { ...validInit(), extra: true };
  assert.equal(parseEnvelope(topLevel, "host-to-frame").code, "invalid-envelope");
  const payload = { ...validInit(), payload: { ...validInit().payload, extra: true } };
  assert.equal(parseEnvelope(payload, "host-to-frame").code, "invalid-payload");
});

test("frame errors are finite codes without free-form text", () => {
  const valid = createEnvelope(FRAME_TO_HOST.ERROR, "error-0001", { code: "command-failed" });
  assert.equal(parseEnvelope(valid, "frame-to-host").ok, true);
  const privateText = createEnvelope(FRAME_TO_HOST.ERROR, "error-0002", {
    code: "command-failed",
    message: "Internal path C:\\private\\home",
  });
  assert.equal(parseEnvelope(privateText, "frame-to-host").ok, false);
  const inventedCode = createEnvelope(FRAME_TO_HOST.ERROR, "error-0003", { code: "private-ip-192.168.1.2" });
  assert.equal(parseEnvelope(inventedCode, "frame-to-host").ok, false);
});

test("parser rejects invalid direction instead of falling through", () => {
  assert.equal(parseEnvelope(validInit(), "sideways").code, "invalid-direction");
});

test("connection envelope is exact and rejects extra fields", () => {
  const connection = createConnectionEnvelope("connect-test");
  assert.equal(isConnectionEnvelope(connection), true);
  assert.equal(isConnectionEnvelope({ ...connection, extra: true }), false);
  assert.equal(isConnectionEnvelope({ ...connection, payload: { transport: "message-port", extra: true } }), false);
});

test("cyclic payloads are rejected without throwing", () => {
  const cyclicEnvironment = {
    network: "online",
    provider: "live",
    core: "online",
    ai: "online",
    siteHealth: {},
  };
  cyclicEnvironment.siteHealth.self = cyclicEnvironment;
  const envelope = {
    ...validInit(),
    payload: { ...validInit().payload, environment: cyclicEnvironment },
  };
  assert.doesNotThrow(() => parseEnvelope(envelope, "host-to-frame"));
  assert.equal(parseEnvelope(envelope, "host-to-frame").ok, false);
});

test("only explicit synthetic fixture records may carry coordinates", () => {
  const state = publicSnapshot(createSpatialState({ sites: SYNTHETIC_SITES }));
  const preciseState = structuredClone(state);
  preciseState.camera.latitude = 10;
  const envelope = createEnvelope(FRAME_TO_HOST.STATE, "state-precise", preciseState);
  assert.equal(parseEnvelope(envelope, "frame-to-host").ok, false);

  const event = createEnvelope(FRAME_TO_HOST.EVENT, "event-precise", {
    kind: "site-selected",
    siteId: SYNTHETIC_SITES[0].id,
    longitude: 10,
  });
  assert.equal(parseEnvelope(event, "frame-to-host").ok, false);
});

test("public snapshots strip anchors and exact coordinates", () => {
  const snapshot = publicSnapshot(createSpatialState({ sites: SYNTHETIC_SITES }));
  const serialized = JSON.stringify(snapshot);
  assert.equal(serialized.includes("anchor"), false);
  assert.equal(serialized.includes("latitude"), false);
  assert.equal(serialized.includes("longitude"), false);
  assert.equal(parseEnvelope(createEnvelope(FRAME_TO_HOST.STATE, "state-valid", snapshot), "frame-to-host").ok, true);
});

test("latest opposite intent reverses from the current deterministic position", () => {
  let state = createSpatialState({ sites: SYNTHETIC_SITES });
  state = startJourney(state, {
    intentId: "intent-in",
    siteId: SYNTHETIC_SITES[0].id,
    destination: "interior",
    playback: "manual",
  });
  state = advanceJourney(state, { deltaMs: 1600 });
  const before = state.journey.elapsedMs;
  state = startJourney(state, {
    intentId: "intent-out",
    siteId: SYNTHETIC_SITES[0].id,
    destination: "planet",
    playback: "manual",
  });
  assert.equal(state.journey.intentId, "intent-out");
  assert.equal(state.journey.elapsedMs, state.journey.durationMs - before);
});

test("a newer same-direction intent preserves progress", () => {
  let state = createSpatialState({ sites: SYNTHETIC_SITES });
  state = startJourney(state, {
    intentId: "intent-one",
    siteId: SYNTHETIC_SITES[0].id,
    destination: "interior",
    playback: "manual",
  });
  state = advanceJourney(state, { deltaMs: 2500 });
  state = startJourney(state, {
    intentId: "intent-two",
    siteId: SYNTHETIC_SITES[1].id,
    destination: "interior",
    playback: "manual",
  });
  assert.equal(state.journey.intentId, "intent-two");
  assert.equal(state.journey.elapsedMs, 2500);
  assert.equal(state.selectedSiteId, SYNTHETIC_SITES[1].id);
});

test("reduced motion resolves a journey immediately", () => {
  let state = createSpatialState({ sites: SYNTHETIC_SITES });
  state = setReducedMotion(state, true);
  state = startJourney(state, {
    intentId: "intent-reduced",
    siteId: SYNTHETIC_SITES[0].id,
    destination: "interior",
    playback: "auto",
  });
  assert.equal(state.journey.status, "completed");
  assert.equal(state.journey.progress, 1);
  assert.equal(state.scale, "interior");
});

test("offline and AI-offline environments do not block spatial state", () => {
  let state = createSpatialState({ sites: SYNTHETIC_SITES });
  state = setEnvironment(state, ENVIRONMENT_PRESETS.offline);
  assert.equal(state.environment.network, "offline");
  state = startJourney(state, {
    intentId: "intent-offline",
    siteId: SYNTHETIC_SITES[0].id,
    destination: "interior",
    playback: "manual",
  });
  assert.equal(state.journey.status, "running");
  state = setEnvironment(state, ENVIRONMENT_PRESETS.aiOffline);
  assert.equal(state.environment.ai, "offline");
  assert.equal(state.journey.status, "running");
});

test("adapter contract fails closed on incomplete candidates", () => {
  assert.throws(() => assertCandidateAdapter({ apiVersion: "home.spatial-renderer-adapter.v1", id: "broken" }));
});

test("lifecycle instrumentation reports whether worker and WebGL hooks are installed", () => {
  const context = { isContextLost: () => false };
  class FakeCanvas {
    constructor() {
      this.isConnected = true;
    }

    getContext() {
      return context;
    }
  }
  class FakeWorker {
    terminate() {}
  }
  const fakeGlobal = { HTMLCanvasElement: FakeCanvas, Worker: FakeWorker };
  const instrumentation = installRuntimeInstrumentation(fakeGlobal);
  const canvas = new fakeGlobal.HTMLCanvasElement();
  canvas.getContext("webgl2");
  const worker = new fakeGlobal.Worker("fixture-worker.js");
  let snapshot = instrumentation.snapshot();
  assert.deepEqual(snapshot.tracking, { workers: true, webgl: true });
  assert.equal(snapshot.webgl.live, 1);
  assert.equal(snapshot.workers.active, 1);
  worker.terminate();
  snapshot = instrumentation.snapshot();
  assert.equal(snapshot.workers.active, 0);
});

test("candidate set is deterministic fallback plus Cesium and sandboxed MapLibre", () => {
  assert.deepEqual(
    CANDIDATE_RUNTIME_MANIFEST.candidates.map((candidate) => candidate.id),
    ["deterministic-dom", "cesium-separate", "maplibre-sandbox"],
  );
  assert.deepEqual(RENDERER_ADAPTER_IDS, ["deterministic-dom", "cesium-separate", "maplibre-sandbox"]);
  assert.ok(CANDIDATE_RUNTIME_MANIFEST.candidates.every((candidate) => candidate.status === "available"));
  assert.ok(fs.existsSync(path.join(spikeRoot, "cesium-adapter.js")));
  assert.ok(fs.existsSync(path.join(spikeRoot, "maplibre-adapter.js")));
  assert.ok(fs.existsSync(path.join(spikeRoot, "benchmark.js")));
});

test("host iframe uses the narrow sandbox and no referrer", () => {
  const html = fs.readFileSync(path.join(spikeRoot, "index.html"), "utf8");
  assert.match(html, /sandbox="allow-scripts"/);
  assert.doesNotMatch(html, /allow-same-origin/);
  assert.match(html, /referrerpolicy="no-referrer"/);
  assert.match(html, /connect-src 'none'/);
});

test("frame exposes keyboard/list, live-region, and reduced-motion hooks", () => {
  const html = fs.readFileSync(path.join(spikeRoot, "frame.html"), "utf8");
  const script = fs.readFileSync(path.join(spikeRoot, "frame-main.js"), "utf8");
  const css = fs.readFileSync(path.join(spikeRoot, "frame.css"), "utf8");
  assert.match(html, /role="listbox"/);
  assert.match(html, /aria-live="polite"/);
  assert.match(script, /ArrowDown/);
  assert.match(script, /ArrowUp/);
  assert.match(script, /event\.key === "Enter"/);
  assert.match(css, /prefers-reduced-motion: reduce/);
  assert.match(css, /forced-colors: active/);
  assert.match(html, /connect-src 'self'/);
  assert.match(html, /worker-src 'self'/);
  assert.match(html, /Run 20-cycle check/);
});

test("aria-hidden decorative globe contains no focusable marker controls", () => {
  const adapter = fs.readFileSync(path.join(spikeRoot, "deterministic-adapter.js"), "utf8");
  assert.match(adapter, /world\.setAttribute\("aria-hidden", "true"\)/);
  assert.match(adapter, /createElement\(documentRef, "div", "fixture-marker"\)/);
  assert.doesNotMatch(adapter, /createElement\(documentRef, "button", "fixture-marker"\)/);
});

test("owned runtime source contains no remote URL literals", () => {
  const files = fs.readdirSync(spikeRoot, { withFileTypes: true })
    .filter((entry) => entry.isFile() && /\.(?:html|js|css|json|md)$/.test(entry.name))
    .map((entry) => path.join(spikeRoot, entry.name));
  for (const file of files) {
    assert.doesNotMatch(fs.readFileSync(file, "utf8"), /https?:\/\//i, path.basename(file));
  }
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exitCode = failed === 0 ? 0 : 1;
