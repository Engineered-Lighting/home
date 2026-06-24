#!/usr/bin/env node
/* Pure local tests for app/src/home-video-labeler-data.js. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "home-video-labeler-data.js");
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
      const dumped = typeof detail === "string"
        ? detail
        : JSON.stringify(detail, null, 2);
      process.stdout.write("\n        " + dumped.replace(/\n/g, "\n        "));
    }
    process.stdout.write("\n");
  }
}

function makeLocalStorage() {
  const store = new Map();
  return {
    get length() { return store.size; },
    key(i) { return Array.from(store.keys())[i] || null; },
    getItem(k) { return store.has(k) ? store.get(k) : null; },
    setItem(k, v) { store.set(String(k), String(v)); },
    removeItem(k) { store.delete(String(k)); },
    clear() { store.clear(); },
    _store: store,
  };
}

function loadModule(opts) {
  opts = opts || {};
  const localStorage = opts.localStorage || makeLocalStorage();
  const calls = [];
  const fetcher = opts.fetch || (async (url, init) => {
    calls.push({ url, init });
    return {
      ok: true,
      status: 200,
      async json() { return { ok: true }; },
    };
  });
  const sandbox = {
    window: {
      __SIM_ACTIVE: !!opts.sim,
      tauriFetch: opts.tauriFetch,
    },
    localStorage,
    fetch: fetcher,
    Date,
    Math,
    JSON,
    encodeURIComponent,
  };
  vm.runInNewContext(source, sandbox, { filename: SRC });
  return { D: sandbox.window.HomeVideoLabelerData, calls, localStorage, window: sandbox.window };
}

async function main() {
  process.stdout.write("\nvideo_labeler_exports_test\n");
  const baseLoad = loadModule();
  const D = baseLoad.D;
  assert("HomeVideoLabelerData exported", D && typeof D === "object");
  assert("vlBase exported", typeof D.vlBase === "function");
  assert("segment math helpers exported", typeof D.normalizeSegments === "function" && typeof D.createSegmentAt === "function");
  assert("media URL helpers exported", typeof D.streamUrl === "function" && typeof D.keyframeUrl === "function");
  assert("data export is frozen", Object.isFrozen(D));

  process.stdout.write("\nvideo_labeler_base_and_url_test\n");
  assert("default base is used", D.vlBase() === "http://192.168.0.100:8099", D.vlBase());
  baseLoad.localStorage.setItem(D.BASE_KEY, "http://labeler.local:8099///");
  assert("stored base is trimmed", D.vlBase() === "http://labeler.local:8099", D.vlBase());
  assert("streamUrl URL-encodes id and includes original flag", D.streamUrl("cam 1/clip.mp4", { original: true }) === "http://labeler.local:8099/api/video-labeler/videos/cam%201%2Fclip.mp4/stream?original=1", D.streamUrl("cam 1/clip.mp4", { original: true }));
  assert("sprite manifest URL is encoded", D.spriteManifestUrl("cam 1") === "http://labeler.local:8099/api/video-labeler/videos/cam%201/sprite", D.spriteManifestUrl("cam 1"));
  assert("keyframe URL prepends base for relative path", D.keyframeUrl("/api/video-labeler/kf.jpg") === "http://labeler.local:8099/api/video-labeler/kf.jpg", D.keyframeUrl("/api/video-labeler/kf.jpg"));
  assert("keyframe URL preserves absolute URL", D.keyframeUrl("https://cdn.example/kf.jpg") === "https://cdn.example/kf.jpg", D.keyframeUrl("https://cdn.example/kf.jpg"));
  assert("spriteSheetUrl supports manifest ref", D.spriteSheetUrl("vid", "sprites/s0.jpg") === "http://labeler.local:8099/sprites/s0.jpg", D.spriteSheetUrl("vid", "sprites/s0.jpg"));
  assert("spriteSheetUrl supports numeric sheet index", D.spriteSheetUrl("vid", 3) === "http://labeler.local:8099/api/video-labeler/videos/vid/sprite/3", D.spriteSheetUrl("vid", 3));
  const simLoad = loadModule({ sim: true });
  assert("sim mode nulls service base", simLoad.D.vlBase() === null, simLoad.D.vlBase());
  assert("sim mode nulls media URLs", simLoad.D.streamUrl("vid") === null && simLoad.D.keyframeUrl("x.jpg") === null);

  process.stdout.write("\nvideo_labeler_api_contract_test\n");
  const apiCalls = [];
  const apiLoad = loadModule({
    fetch: async (url, init) => {
      apiCalls.push({ url, init });
      return {
        ok: true,
        status: 201,
        async json() { return { url, method: init && init.method || "GET" }; },
      };
    },
  });
  apiLoad.localStorage.setItem(apiLoad.D.BASE_KEY, "http://vl.test/");
  let r = await apiLoad.D.getVideo("video/a b");
  assert("getVideo resolves ok wrapper", r.ok === true && r.status === 201, r);
  assert("getVideo encodes path segment", apiCalls[0].url === "http://vl.test/api/video-labeler/videos/video%2Fa%20b", apiCalls[0]);
  assert("vlApi injects no-store cache", apiCalls[0].init.cache === "no-store", apiCalls[0]);
  await apiLoad.D.importManual("kitchen batch");
  assert("importManual posts JSON payload", apiCalls[1].init.method === "POST" && JSON.parse(apiCalls[1].init.body).batch_name === "kitchen batch", apiCalls[1]);
  await apiLoad.D.listJobs({ state: "running", type: "import/manual" });
  assert("listJobs encodes query params", apiCalls[2].url.endsWith("/api/video-labeler/jobs?state=running&type=import%2Fmanual"), apiCalls[2].url);
  await apiLoad.D.reviewSegment("vid 1", "seg/a", "accept");
  assert("reviewSegment posts action", JSON.parse(apiCalls[3].init.body).action === "accept" && apiCalls[3].url.includes("seg%2Fa"), apiCalls[3]);
  await apiLoad.D.createCustomLabel({ axis: "activity_primary", name: "making tea" });
  assert("createCustomLabel maps activity_primary to server activity", JSON.parse(apiCalls[4].init.body).axis === "activity", apiCalls[4]);
  await apiLoad.D.promoteCustomLabel("custom/tea", "cooking");
  assert("promoteCustomLabel encodes slug and body", apiCalls[5].url.includes("custom%2Ftea") && JSON.parse(apiCalls[5].init.body).canonical_value === "cooking", apiCalls[5]);

  const errLoad = loadModule({
    fetch: async () => ({
      ok: false,
      status: 409,
      async json() { return { detail: "revision conflict", revision: 7 }; },
    }),
  });
  const err = await errLoad.D.putLabels("vid", { revision: 3, axes: {} });
  assert("HTTP errors resolve no-throw wrapper", err.ok === false && err.status === 409 && err.error === "revision conflict", err);
  const throwLoad = loadModule({ fetch: async () => { throw new Error("offline"); } });
  const thrown = await throwLoad.D.health();
  assert("network errors resolve no-throw wrapper", thrown.ok === false && thrown.status === 0 && /offline/.test(thrown.error), thrown);
  const simApi = await simLoad.D.listVideos();
  assert("sim API returns simulation-mode wrapper without fetch", simApi.ok === false && simApi.error === "simulation mode", simApi);

  process.stdout.write("\nvideo_labeler_ontology_and_draft_test\n");
  const ontologyLoad = loadModule({
    fetch: async () => ({
      ok: true,
      status: 200,
      async json() {
        return { custom: [{ slug: "tea", axis: "activity" }, { slug: "flag", axis: "quality" }] };
      },
    }),
  });
  const ontology = await ontologyLoad.D.getOntology();
  assert("getOntology maps server activity axis to client lane", ontology.data.custom[0].axis === "activity_primary", ontology.data);
  const draftLoad = loadModule();
  draftLoad.D.saveDraft("video1", { doc: { axes: { activity_primary: [{ id: "tmp_1" }] } }, baseRevision: 2, rev: 5 });
  const draft = draftLoad.D.loadDraft("video1");
  assert("saveDraft/loadDraft round-trip", draft && draft.rev === 5 && draft.ts > 0, draft);
  draftLoad.D.clearDraft("video1");
  assert("clearDraft removes stored draft", draftLoad.D.loadDraft("video1") === null);
  const validated = draftLoad.D.validateDraftDoc({
    activity_primary: [{ id: "srv1" }, { id: "tmp_local" }, { id: "stale" }],
    quality: [{ id: "seg_local_abc" }, { id: null }],
  }, {
    activity_primary: [{ id: "srv1" }],
  });
  assert("validateDraftDoc preserves server and temp ids", validated.axes.activity_primary.map((s) => s.id).join(",") === "srv1,tmp_local", validated);
  assert("validateDraftDoc drops stale/invalid ids", validated.dropped === 2, validated);

  process.stdout.write("\nvideo_labeler_format_and_metadata_test\n");
  assert("windowIdFor uses milliseconds", D.windowIdFor("vid", 1.2344) === "aw_vid_1234", D.windowIdFor("vid", 1.2344));
  assert("suggestionDisagreement reads evidence", D.suggestionDisagreement({ evidence: { disagreement: 0.42 } }) === 0.42);
  assert("suggestionDisagreement reads aux fallback", D.suggestionDisagreement({ aux: { disagreement: 0.2 } }) === 0.2);
  assert("suggestionEvidenceText reads notes", D.suggestionEvidenceText({ aux: { notes: "hands near sink" } }) === "hands near sink");
  assert("suggestionEvidenceText prefixes other_hint", D.suggestionEvidenceText({ evidence: { other_hint: "low light" } }) === "hint: low light");
  assert("colorFor returns canonical known color", /^hsl\(/.test(D.colorFor("cooking")), D.colorFor("cooking"));
  assert("colorFor hashes custom labels", /^hsl\(\d+ 48% 58%\)$/.test(D.colorFor("custom:tea")), D.colorFor("custom:tea"));
  assert("fmtTimecode formats frames", D.fmtTimecode(61.5, 30) === "01:01.15", D.fmtTimecode(61.5, 30));
  assert("fmtDuration formats under hour", D.fmtDuration(125) === "2:05", D.fmtDuration(125));
  assert("fmtDuration formats over hour", D.fmtDuration(3661) === "1:01:01", D.fmtDuration(3661));
  assert("slot labels are user-facing", D.slotLabel(null) === "you" && D.slotLabel("p3") === "person 3", [D.slotLabel(null), D.slotLabel("p3")]);

  process.stdout.write("\nvideo_labeler_segment_math_test\n");
  const lane = [
    { id: "a", start_s: 0, end_s: 4, value: "cooking" },
    { id: "b", start_s: 3, end_s: 8, value: "cleaning" },
    { id: "p2a", start_s: 2, end_s: 6, value: "standing", person_slot: "p2" },
  ];
  const normalized = D.normalizeSegments(lane, 10, 0.2);
  assert("normalizeSegments clips same-slot overlap", normalized.find((s) => s.id === "b").start_s === 4, normalized);
  assert("normalizeSegments allows cross-slot overlap", normalized.find((s) => s.id === "p2a").start_s === 2, normalized);
  assert("laneSlots primary first then sorted extras", JSON.stringify(D.laneSlots(lane)) === JSON.stringify([null, "p2"]), D.laneSlots(lane));
  const split = D.splitAt([{ id: "a", start_s: 0, end_s: 10, value: "x" }], 4);
  assert("splitAt produces two segments", split.length === 2 && split[0].end_s === 4 && split[1].start_s === 4 && /^tmp_/.test(split[1].id), split);
  const moved = D.moveSegment([
    { id: "a", start_s: 0, end_s: 2 },
    { id: "b", start_s: 3, end_s: 5 },
  ], "b", 1, { duration: 10 });
  assert("moveSegment clamps against same-slot left neighbor", moved.find((s) => s.id === "b").start_s === 2, moved);
  const resized = D.resizeSegment([
    { id: "a", start_s: 0, end_s: 4 },
    { id: "b", start_s: 4, end_s: 8 },
  ], "a", "end", 7, { duration: 10, minDur: 1 });
  assert("resizeSegment clips same-slot right neighbor", resized.find((s) => s.id === "a").end_s === 7 && resized.find((s) => s.id === "b").start_s === 7, resized);
  const carved = D.carveRange([{ id: "a", start_s: 0, end_s: 10, value: "x" }], 3, 7, 0.5);
  assert("carveRange splits around removed span", carved.length === 2 && carved[0].end_s === 3 && carved[1].start_s === 7 && /^tmp_/.test(carved[1].id), carved);
  const created = D.createSegmentAt([{ id: "a", start_s: 0, end_s: 2 }], 5, "reading", { duration: 8, prefDur: 3, minDur: 1, slot: "p2" });
  assert("createSegmentAt inserts slot segment", created && created.segments.some((s) => s.id === created.id && s.person_slot === "p2" && s.value === "reading"), created);
  const merged = D.mergeAdjacent([
    { id: "a", start_s: 0, end_s: 2, value: "x" },
    { id: "p2", start_s: 2, end_s: 4, value: "z", person_slot: "p2" },
    { id: "b", start_s: 4, end_s: 6, value: "y" },
  ], "a");
  assert("mergeAdjacent skips other slots and merges same-slot neighbor", merged.length === 2 && merged.find((s) => s.id === "a").end_s === 6, merged);
  assert("findSnap picks nearest within tolerance", D.findSnap([1, 2.1, 3], 2, 0.2) === 2.1);
  assert("findSnap returns null outside tolerance", D.findSnap([1, 3], 2, 0.2) === null);

  if (fails) {
    console.log("\nFailures:");
    for (const f of failures) console.log("- " + f.name + (f.detail ? ": " + JSON.stringify(f.detail) : ""));
  }
  console.log(`\n${passes} pass . ${fails} fail`);
  process.exit(fails ? 1 : 0);
}

main().catch((err) => {
  console.error(err && err.stack || err);
  process.exit(1);
});
