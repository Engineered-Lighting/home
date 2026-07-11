#!/usr/bin/env node
/* Scenario tests for abstract visual prompts automatically invoking /look. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "home-natural-look.js");
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
      const dumped = typeof detail === "string" ? detail : JSON.stringify(detail, null, 2);
      process.stdout.write("\n        " + dumped.replace(/\n/g, "\n        "));
    }
    process.stdout.write("\n");
  }
}

function loadApi() {
  const sandbox = {
    window: {},
    module: { exports: {} },
    exports: {},
    setTimeout,
    clearTimeout,
    AbortController,
  };
  vm.runInNewContext(source, sandbox, { filename: "home-natural-look.js" });
  return sandbox.module.exports;
}

function makeHarness(overrides = {}) {
  const api = loadApi();
  const events = [];
  const calls = {
    ensureFeature: [],
    lookRunner: [],
    sendToHA: [],
  };
  const options = {
    addEvent(ev) { events.push(ev); },
    endpoint: "/proxy/ha",
    ensureFeature: async (...args) => {
      calls.ensureFeature.push(args);
      return overrides.featureLoaded !== false;
    },
    lookRunner: async (req) => {
      calls.lookRunner.push(req);
      if (overrides.runner) return overrides.runner(req, calls.lookRunner.length);
      return {
        camera: req.camera,
        answer: `${req.camera} looks normal`,
        annotatedUrl: `/annotated/${req.camera}.jpg`,
        detailUrl: `/detail/${req.camera}.jpg`,
      };
    },
    metricsBase: "/proxy/metrics",
    metricsBaseFromEndpoint: () => "/proxy/metrics",
    normalizeAnswer: (v) => String(v || "").trim(),
    roomContext: overrides.roomContext,
    sendToHA: async (...args) => { calls.sendToHA.push(args); },
    simActive: !!overrides.simActive,
    timeoutMs: overrides.timeoutMs || 500,
  };
  if (overrides.omitRunner) delete options.lookRunner;
  return { api, events, calls, options };
}

function eventTexts(events) {
  return events.map((e) => e.text || "").join("\n");
}

(async function main() {
  const api = loadApi();

  process.stdout.write("\nabstract_visual_positive_scenarios\n");
  [
    ["what do you see in my apartment", "apartment", null, "quick_scan"],
    ["what's going on inside", "apartment", null, "quick_scan"],
    ["check my apartment visually", "apartment", null, "quick_scan"],
    ["what's happening in the kitchen", "apartment", "kitchen", "targeted_look"],
    ["what do you see on my coffee table", "apartment", "living_room", "targeted_look"],
    ["look at the driveway", "home", "driveway", "targeted_look"],
    ["does anything look weird", "apartment", null, "anomaly_scan"],
    ["describe everything in the kitchen", "apartment", "kitchen", "detailed_scan"],
    ["give me a visual status", "apartment", null, "quick_scan"],
  ].forEach(([prompt, scope, camera, mode]) => {
    const intent = api.detectDeepLookIntent(prompt);
    assert(`triggers visual routing: ${prompt}`, !!intent && intent.scope === scope && intent.explicitCamera === camera && intent.mode === mode, intent);
  });

  process.stdout.write("\nabstract_visual_negative_scenarios\n");
  [
    "turn off all my lights",
    "dim the kitchen",
    "which lights are on",
    "turn travel mode on",
    "are my models online",
    "what's going on with my models",
    "deploy the app",
    "what is my stack token",
    "what's going on with the app",
    "describe the implementation plan",
    "what do you think about this release",
    "what's going on with the website",
    "what do you see in this UI bug",
    "look at the test output",
    "describe the desktop build error",
  ].forEach((prompt) => {
    assert(`does not trigger visual routing: ${prompt}`, api.detectDeepLookIntent(prompt) === null, api.detectDeepLookIntent(prompt));
  });

  process.stdout.write("\ncamera_selection_scenarios\n");
  const aptCams = api.selectDeepLookCameras(api.detectDeepLookIntent("what do you see in my apartment"), null, 3).map((c) => c.id);
  assert("apartment default excludes driveway", aptCams.length === 3 && !aptCams.includes("driveway"), aptCams);
  const homeCams = api.selectDeepLookCameras(api.detectDeepLookIntent("what do you see in my home"), { rooms: { driveway: { occupied: true } } }, 3).map((c) => c.id);
  assert("home scope can include driveway", homeCams.includes("driveway"), homeCams);
  const explicit = api.selectDeepLookCameras(api.detectDeepLookIntent("what's happening in the kitchen"), { rooms: { living_room: { occupied: true } } }, 3).map((c) => c.id);
  assert("explicit camera wins over occupied smart ranking", JSON.stringify(explicit) === JSON.stringify(["kitchen"]), explicit);
  const occupied = api.selectDeepLookCameras(api.detectDeepLookIntent("what do you see in my apartment"), { rooms: { dining_room: { occupied: true }, kitchen: { age_s: 2 } } }, 3).map((c) => c.id);
  assert("occupied rooms rank first", occupied[0] === "dining_room", occupied);
  const freshUntil = new Date(Date.now() + 60_000).toISOString();
  const hinted = api.selectDeepLookCameras(
    api.detectDeepLookIntent("what do you see in my apartment"),
    null,
    3,
    [{ camera: "kitchen", fresh_until: freshUntil, semantic_hint: "person in kitchen", summary: "kitchen: possible person" }],
  ).map((c) => c.id);
  assert("fresh Frigate perception hints rank camera first", hinted[0] === "kitchen", hinted);
  const stale = api.selectDeepLookCameras(
    api.detectDeepLookIntent("what do you see in my apartment"),
    null,
    3,
    [{ camera: "kitchen", fresh_until: "2020-01-01T00:00:00.000Z", semantic_hint: "person in kitchen", summary: "kitchen: possible person" }],
  ).map((c) => c.id);
  assert("stale Frigate perception hints do not rank camera", stale[0] !== "kitchen", stale);
  const malformed = api.selectDeepLookCameras(api.detectDeepLookIntent("what do you see in my apartment"), { rooms: "broken" }, 99).map((c) => c.id);
  assert("malformed roomContext still returns safe indoor defaults", malformed.length === 4 && !malformed.includes("driveway"), malformed);
  assert("camera selection caps at requested limit", api.selectDeepLookCameras(api.detectDeepLookIntent("what do you see in my apartment"), null, 2).length === 2);

  process.stdout.write("\nmocked_submit_success_scenario\n");
  {
    const h = makeHarness({ roomContext: { rooms: { kitchen: { occupied: true } } } });
    const result = await h.api.runNaturalDeepLook("what do you see in my apartment", h.options);
    const texts = eventTexts(h.events);
    assert("successful abstract prompt is handled", result.handled === true && result.fallback === false, result);
    assert("feature loader is called for look natural-language", JSON.stringify(h.calls.ensureFeature[0]) === JSON.stringify(["look", "look", "natural-language"]), h.calls.ensureFeature);
    assert("look runner called once per selected camera", h.calls.lookRunner.length === 3, h.calls.lookRunner.map((c) => c.camera));
    assert("broad abstract prompt sends focused monitoring instruction", h.calls.lookRunner.every((c) => c.question.includes("home-monitoring dashboard") && c.question.includes("Do not list ordinary furniture")), h.calls.lookRunner.map((c) => c.question));
    assert("broad abstract prompt asks for zoom or segmentation", h.calls.lookRunner.every((c) => c.question.includes("zoom/crop plus segmentation")), h.calls.lookRunner.map((c) => c.question));
    assert("HA fallback is not called on look success", h.calls.sendToHA.length === 0, h.calls.sendToHA);
    assert("one user event is emitted", h.events.filter((e) => e.kind === "user").length === 1, h.events);
    assert("transcript shows looking deeply", texts.includes("looking deeply · kitchen"), texts);
    assert("transcript includes one final grounded answer", h.events.filter((e) => e.kind === "home").length === 1 && texts.includes("Nothing important stands out."), texts);
    assert("perception cards carry annotated segmentation image mode", h.events.filter((e) => e.kind === "perception").every((e) => e.imageMode === "annotated" && e.snapshotUrl), h.events.filter((e) => e.kind === "perception"));
    assert("perception cards prefer full-frame annotated images over zoom crops", h.events.filter((e) => e.kind === "perception").every((e) => String(e.snapshotUrl || "").startsWith("/annotated/")), h.events.filter((e) => e.kind === "perception"));
    const finalAnswer = h.events.find((e) => e.kind === "home")?.text || "";
    assert("final broad answer is focused, not room-label inventory", finalAnswer.includes("I checked ") && finalAnswer.length < 120 && !/living room:|kitchen:|dining room:|coffee table|wooden island/i.test(finalAnswer), finalAnswer);
    assert("abstract prompt is not echoed as slash look", !texts.includes("/look what do you see in my apartment"), texts);
  }

  process.stdout.write("\ncoffee_table_targeted_scenario\n");
  {
    const h = makeHarness();
    const result = await h.api.runNaturalDeepLook("what do you see on my coffee table", h.options);
    assert("coffee table question is handled by natural look", result.handled === true && result.fallback === false, result);
    assert("coffee table routes to living room camera", h.calls.lookRunner.length === 1 && h.calls.lookRunner[0].camera === "living_room", h.calls.lookRunner);
    assert("targeted coffee table look asks for zoom/segmentation", h.calls.lookRunner[0].question.includes("zoom/crop plus segmentation"), h.calls.lookRunner[0].question);
  }

  process.stdout.write("\nfocused_summary_scenarios\n");
  {
    const summary = api.summarizeDeepLookResults("what do you see in my apartment", [
      { ok: true, name: "living room", answer: "A living room with a couch, coffee table, plant, bicycle, door and picture." },
      { ok: true, name: "kitchen", answer: "kitchen with white cabinets, stove, sink, wooden island, and doorway to bedroom." },
      { ok: true, name: "dining room", answer: "A dining area with a white oval table and three chairs on a wooden floor." },
    ], []);
    assert("broad low-signal inventory becomes quiet status", summary === "I checked living room, kitchen, and dining room. Nothing important stands out.", summary);
  }
  {
    const summary = api.summarizeDeepLookResults("what do you see in my home", [
      { ok: true, name: "living room", answer: "No obvious activity." },
      { ok: true, name: "driveway", answer: "A person is walking near a covered car." },
      { ok: true, name: "kitchen", answer: "kitchen with white cabinets, sink, stove, and island." },
    ], []);
    assert("broad notable activity stays focused", summary.includes("Quick scan: In the driveway") && summary.includes("person is walking") && summary.includes("living room and kitchen") && !summary.includes("white cabinets"), summary);
  }
  {
    const summary = api.summarizeDeepLookResults("what is happening outside", [
      { ok: true, name: "driveway", answer: "A person walks on the sidewalk near a covered car." },
      { ok: true, name: "living room", answer: "No obvious activity." },
    ], []);
    assert("notable action grammar stays natural", summary.includes("In the driveway, a person walks") && !summary.includes("I see a person walks"), summary);
  }
  {
    const summary = api.summarizeDeepLookResults("does anything look weird", [
      { ok: true, name: "kitchen", answer: "There is water on the floor near the sink." },
      { ok: true, name: "living room", answer: "No obvious activity." },
    ], []);
    assert("there-is grammar lowercases after room clause", summary.includes("In the kitchen, there is water") && !summary.includes(", There is"), summary);
  }
  {
    const summary = api.summarizeDeepLookResults("what is happening outside", [
      { ok: true, name: "driveway", answer: "A package is sitting near the front door." },
      { ok: true, name: "kitchen", answer: "No obvious activity." },
    ], []);
    assert("package grammar stays natural", summary.includes("In the driveway, a package is sitting") && !summary.includes("I see a package is"), summary);
  }
  {
    const summary = api.summarizeDeepLookResults("what do you see in my home", [
      { ok: true, name: "driveway", answer: "I don't have a full picture right now. I don't have a full picture right now. I can check specific areas if you'd like. I can check specific areas if you'd like." },
    ], []);
    assert("duplicated model text collapses before final answer", (summary.match(/full picture/g) || []).length === 1 && (summary.match(/specific areas/g) || []).length === 1, summary);
  }
  {
    const summary = api.summarizeDeepLookResults("describe everything in the kitchen", [
      { ok: true, name: "kitchen", answer: "A kitchen with white cabinets, stove, sink, wooden island, and doorway to bedroom." },
    ], []);
    assert("detailed mode preserves useful inventory", /white cabinets|wooden island/i.test(summary), summary);
  }
  {
    const summary = api.summarizeDeepLookResults("does anything look weird", [
      { ok: true, name: "living room", answer: "The frame is blurry and hard to tell." },
      { ok: true, name: "kitchen", answer: "No obvious activity." },
    ], []);
    assert("uncertain visual results are not overstated", summary.includes("I'm not fully sure") && summary.includes("frame is blurry"), summary);
  }

  process.stdout.write("\nmocked_submit_partial_failure_scenario\n");
  {
    const h = makeHarness({
      runner(req, index) {
        if (index === 2) throw new Error("camera offline");
        return { camera: req.camera, answer: `${req.camera} ok`, overviewUrl: `/overview/${req.camera}.jpg` };
      },
    });
    const result = await h.api.runNaturalDeepLook("what do you see in my apartment", h.options);
    const texts = eventTexts(h.events);
    assert("partial failure is still handled without HA fallback", result.handled === true && result.fallback === false && h.calls.sendToHA.length === 0, result);
    assert("partial failure emits visible diagnostic", texts.includes("grounded look failed") && texts.includes("camera offline"), texts);
    assert("partial failure final answer names failed camera", texts.includes("I could not inspect"), texts);
  }

  process.stdout.write("\nmocked_submit_all_failure_scenario\n");
  {
    const h = makeHarness({ runner() { throw new Error("sidecar down"); } });
    const result = await h.api.runNaturalDeepLook("what do you see in my apartment", h.options);
    const texts = eventTexts(h.events);
    assert("all failures fall back to HA", result.handled === true && result.fallback === true && h.calls.sendToHA.length === 1, { result, calls: h.calls.sendToHA });
    assert("fallback uses echoUser false", h.calls.sendToHA[0][1]?.echoUser === false, h.calls.sendToHA);
    assert("all failures emit visible reason", texts.includes("grounded look failed for every selected camera"), texts);
    assert("user text is still emitted only once", h.events.filter((e) => e.kind === "user").length === 1, h.events);
  }

  process.stdout.write("\nmocked_submit_feature_failure_scenarios\n");
  {
    const h = makeHarness({ featureLoaded: false });
    const result = await h.api.runNaturalDeepLook("what do you see in my apartment", h.options);
    const texts = eventTexts(h.events);
    assert("feature-load failure falls back", result.reason === "feature-unavailable" && h.calls.sendToHA.length === 1, result);
    assert("feature-load failure is visible", texts.includes("look feature did not load"), texts);
  }
  {
    const h = makeHarness({ omitRunner: true });
    const result = await h.api.runNaturalDeepLook("what do you see in my apartment", h.options);
    const texts = eventTexts(h.events);
    assert("missing runner falls back", result.reason === "runner-missing" && h.calls.sendToHA.length === 1, result);
    assert("missing runner is visible", texts.includes("look runner missing"), texts);
  }

  process.stdout.write("\nmocked_submit_malformed_response_scenario\n");
  {
    const h = makeHarness({ runner() { return null; } });
    const result = await h.api.runNaturalDeepLook("what do you see in my apartment", h.options);
    const texts = eventTexts(h.events);
    assert("malformed response becomes failure and fallback", result.reason === "all-look-failed" && h.calls.sendToHA.length === 1, result);
    assert("malformed response is visible", texts.includes("malformed look response"), texts);
  }

  process.stdout.write("\nsimulation_and_non_visual_scenarios\n");
  {
    const h = makeHarness({ simActive: true });
    const result = await h.api.runNaturalDeepLook("what do you see in my apartment", h.options);
    assert("simulation mode does not invoke look", result.handled === false && h.calls.lookRunner.length === 0 && h.calls.sendToHA.length === 0, result);
  }
  {
    const h = makeHarness();
    const result = await h.api.runNaturalDeepLook("turn off all my lights", h.options);
    assert("non-visual prompt is not handled by look", result.handled === false && h.calls.lookRunner.length === 0 && h.calls.sendToHA.length === 0, result);
  }

  if (fails) {
    console.log("\nFailures:");
    for (const f of failures) console.log("- " + f.name + (f.detail ? ": " + JSON.stringify(f.detail) : ""));
  }
  console.log(`\n${passes} pass . ${fails} fail`);
  process.exit(fails ? 1 : 0);
})().catch((e) => {
  console.error(e && e.stack ? e.stack : e);
  process.exit(1);
});
