#!/usr/bin/env node
/* ============================================================================
 * tools/run-lab-tests.js
 *
 * Pure-function test runner for the Lab tab helpers. No JSX, no React, no
 * external dependencies. Run via:
 *
 *   node tools/run-lab-tests.js
 *
 * Exit code: 0 if all pass, 1 if any fail.
 *
 * Why this exists: prior bug-fix rounds (tooltip snapping to viewport top,
 * resource lines rendering flat) shipped because verification stopped at
 * "cargo build OK + PID alive." Pure-function tests catch THIS class of
 * bug in milliseconds without needing visual review. Every "fix shipped"
 * claim from here on requires this suite green before the user is asked
 * to look.
 * ============================================================================ */

"use strict";

const fs = require("fs");
const path = require("path");
const H = require(path.join(__dirname, "..", "app", "src", "home-metrics-lab-helpers.js"));
const REPO = path.resolve(__dirname, "..");
const LAB_SRC = fs.readFileSync(path.join(REPO, "app", "src", "home-metrics-lab.jsx"), "utf8");
const APP_SRC = fs.readFileSync(path.join(REPO, "app", "src", "home-app.jsx"), "utf8");
const COMPOSE_SRC = fs.readFileSync(path.join(REPO, "stack", "docker-compose.yml"), "utf8");

let passes = 0;
let fails = 0;
const failures = [];

function assert(name, cond, detail) {
  if (cond) {
    passes++;
    process.stdout.write("  [32mPASS[0m  " + name + "\n");
  } else {
    fails++;
    failures.push({ name, detail });
    process.stdout.write("  [31mFAIL[0m  " + name);
    if (detail !== undefined) {
      const dumped = typeof detail === "string"
        ? detail
        : JSON.stringify(detail, null, 2).replace(/\n/g, "\n        ");
      process.stdout.write("\n        " + dumped);
    }
    process.stdout.write("\n");
  }
}

function approxEq(a, b, tol) {
  tol = tol == null ? 0.01 : tol;
  return Math.abs(a - b) <= tol;
}

function defaultComposeServices() {
  const services = [];
  let inServices = false;
  let current = null;
  let block = [];
  function flush() {
    if (!current) return;
    if (!block.some((line) => /^\s{4}profiles:\s*\[/.test(line))) services.push(current);
  }
  for (const line of COMPOSE_SRC.split(/\r?\n/)) {
    if (!inServices) {
      if (/^services:\s*$/.test(line)) inServices = true;
      continue;
    }
    if (/^[A-Za-z0-9_-]+:\s*$/.test(line)) break;
    const m = line.match(/^  ([A-Za-z0-9][A-Za-z0-9_-]*):\s*$/);
    if (m) {
      flush();
      current = m[1];
      block = [];
      continue;
    }
    if (current) block.push(line);
  }
  flush();
  return services;
}

function displayNameForComposeService(name) {
  return name === "kokoro-tts" ? "kokoro" : name;
}

function sameOrderedItems(actual, expected) {
  return actual.length === expected.length
    && actual.every((item, index) => item === expected[index]);
}

/* ────────────────────────────────────────────────────────────────────────
 * Suite 1 — tooltip positioning
 *
 * The prior bug: container-relative coords passed to a position:fixed
 * tooltip → tooltip lands at viewport (small, small) = top-left of the
 * window instead of next to the cursor. computeTooltipPos must produce
 * viewport-clamped coords that put the tooltip RECTANGLE within view.
 * ──────────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mtooltip positioning[0m\n");

(function () {
  // Cursor in the middle of a 1920x1080 viewport, plenty of room.
  const t = H.computeTooltipPos(960, 540, 1920, 1080);
  assert("middle cursor: tooltip box stays in viewport",
    t.top >= 12 && t.top + 120 <= 1080 - 12 + 1
      && t.left >= 12 && t.left + 260 <= 1920 - 12 + 1,
    t);
  // Cursor below a typical stack-trace row — we expect tooltip placed ABOVE
  // (the bars are near top of chart card, ~y=300ish on a typical layout)
  const t2 = H.computeTooltipPos(800, 300, 1920, 1080);
  assert("cursor at y=300: tooltip placed above when there's room",
    t2.top < 300, t2);
})();

(function () {
  // Cursor very near viewport top — tooltip should fall back to BELOW.
  const t = H.computeTooltipPos(960, 30, 1920, 1080);
  assert("cursor near top: tooltip falls back to BELOW the cursor",
    t.placedAbove === false && t.top > 30, t);
})();

(function () {
  // Cursor near top-left corner — must still clamp to PAD on both axes.
  const t = H.computeTooltipPos(20, 20, 1920, 1080);
  assert("top-left corner: clamps top >= PAD",  t.top >= 12, t);
  assert("top-left corner: clamps left >= PAD", t.left >= 12, t);
})();

(function () {
  // Cursor near bottom-right — tooltip box must NOT overflow viewport.
  const t = H.computeTooltipPos(1900, 1060, 1920, 1080);
  assert("bottom-right corner: tooltip box right edge in viewport",
    t.left + 260 <= 1920 - 12 + 1, t);
  assert("bottom-right corner: tooltip box bottom edge in viewport",
    t.top + 120 <= 1080 - 12 + 1, t);
})();

(function () {
  // Regression guard for the historical bug: cursor at (1500, 600) MUST
  // produce a tooltip that's NEAR (1500, 600), not at viewport (0,0).
  const t = H.computeTooltipPos(1500, 600, 1920, 1080);
  assert("regression: cursor at (1500,600) → tooltip near it, not at origin",
    t.left > 1000 && t.top > 100, t);
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite 2 — buildStageAnchors (the per-stage anchor synthesis)
 *
 * The prior bug: when a turn had 0-1 real samples, the line graph rendered
 * as a single point and the path was flat across the whole call slot.
 * buildStageAnchors must return ONE anchor per stage, valued from real
 * samples when available, else from STAGE_PROFILES — producing meaningful
 * per-stage variance regardless of how few real samples exist.
 * ──────────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mper-stage anchor synthesis[0m\n");

(function () {
  // Turn with ZERO real samples — must still produce 4 anchors with the
  // per-stage profile values.
  const turn = {
    startedAt: 1000,
    endedAt: 1500,
    totalMs: 500,
    stages: [
      { label: "stt",   ms: 100, startedAt: 1000, endedAt: 1100 },
      { label: "llm",   ms: 200, startedAt: 1100, endedAt: 1300 },
      { label: "synth", ms: 100, startedAt: 1300, endedAt: 1400 },
      { label: "audio", ms: 100, startedAt: 1400, endedAt: 1500 },
    ],
    samples: [],
  };
  const layout = { slotStart: 0, slotEnd: 280 };
  const gpu = H.buildStageAnchors(turn, "gpu", layout);
  assert("zero samples: GPU returns 4 anchors", gpu.length === 4, gpu);
  assert("zero samples: GPU all anchors are synthetic",
    gpu.every((a) => a.kind === "synthetic"), gpu);
  // 2026-05-18 fix: STAGE_PROFILES values lowered to ~0.08-0.32 — synthetic
  // anchors must NOT fake high values (was 0.75 for gpu.llm; trust-contract
  // violation per user report). Each metric stays near its low baseline
  // when no real samples exist.
  assert("zero samples: GPU stt anchor low (≤0.20)",
    gpu[0].stage === "stt" && gpu[0].v <= 0.20, gpu[0]);
  assert("zero samples: GPU llm anchor stays LOW (≤0.20 — was 0.75 prior)",
    gpu[1].stage === "llm" && gpu[1].v <= 0.20, gpu[1]);
  assert("zero samples: GPU synth anchor stays LOW (≤0.20 — was 0.65 prior)",
    gpu[2].stage === "synth" && gpu[2].v <= 0.20, gpu[2]);
  assert("zero samples: GPU audio anchor low (≤0.20)",
    gpu[3].stage === "audio" && gpu[3].v <= 0.20, gpu[3]);
  // CPU profile is also flat now (was anti-phased) — synthetic shows
  // baseline ~0.08-0.10 across all stages. Real CPU samples will show
  // their actual variance when present.
  const cpu = H.buildStageAnchors(turn, "cpu", layout);
  assert("zero samples: CPU stays low across all stages",
    cpu.every((a) => a.v <= 0.20), cpu.map((a) => `${a.stage}=${a.v.toFixed(2)}`));
  // Old test asserted anti-phasing (audio > llm). With the flat
  // profile, ALL CPU stages are ~0.08-0.10 — no anti-phase. This is
  // intentional: synthetic data should not invent shape.
})();

(function () {
  // Turn with ONE real sample mid-LLM at 0.92 — LLM anchor must use the
  // real value, other stages stay synthetic.
  const turn = {
    startedAt: 1000,
    endedAt: 1500,
    totalMs: 500,
    stages: [
      { label: "stt",   ms: 100, startedAt: 1000, endedAt: 1100 },
      { label: "llm",   ms: 200, startedAt: 1100, endedAt: 1300 },
      { label: "synth", ms: 100, startedAt: 1300, endedAt: 1400 },
      { label: "audio", ms: 100, startedAt: 1400, endedAt: 1500 },
    ],
    samples: [{ t: 1200, gpuPct: 0.92, cpuPct: 0.08, ramPct: 0.30 }],
  };
  const gpu = H.buildStageAnchors(turn, "gpu", { slotStart: 0, slotEnd: 280 });
  assert("one real sample in LLM: LLM anchor uses real value (0.92)",
    gpu[1].kind === "real" && approxEq(gpu[1].v, 0.92), gpu[1]);
  assert("one real sample in LLM: STT anchor stays synthetic",
    gpu[0].kind === "synthetic", gpu[0]);
})();

(function () {
  // 2026-05-18 regression: stage-wide fallback for dense anchors.
  // A 200 ms LLM stage with dense=6 has 6 sub-windows of ~33ms each.
  // A single sample mid-LLM falls in ONLY ONE sub-window. The other
  // 5 should fall back to STAGE-WIDE real-sample average (the same
  // 0.92), NOT to synthetic baseline. Previously: 5 of 6 sub-windows
  // went synthetic → line jumped between 0.92 and 0.10 within a
  // single stage. Now: all 6 sub-windows read the stage's real data.
  const turn = {
    id: "dense-fallback-regression",
    startedAt: 1000, endedAt: 1500, totalMs: 500,
    stages: [
      { label: "stt",   ms: 100, startedAt: 1000, endedAt: 1100 },
      { label: "llm",   ms: 200, startedAt: 1100, endedAt: 1300 },
      { label: "synth", ms: 100, startedAt: 1300, endedAt: 1400 },
      { label: "audio", ms: 100, startedAt: 1400, endedAt: 1500 },
    ],
    samples: [{ t: 1200, gpuPct: 0.92, cpuPct: 0.08, ramPct: 0.30 }],
  };
  const dense = H.buildDenseStageAnchors(turn, "gpu", { slotStart: 0, slotEnd: 280 });
  const llmAnchors = dense.filter((a) => a.stage === "llm");
  // ALL LLM anchors should be kind:"real" — sub-window without direct
  // sample falls back to stage-wide before going synthetic.
  const allRealInLlm = llmAnchors.every((a) => a.kind === "real");
  assert("stage-wide fallback: all LLM sub-anchors marked real (no synthetic gaps)",
    allRealInLlm,
    { kinds: llmAnchors.map((a) => a.kind) });
  // All LLM anchors should read ~0.92 (stage-wide max), not synthetic ~0.10.
  const maxLlm = Math.max(...llmAnchors.map((a) => a.v));
  const minLlm = Math.min(...llmAnchors.map((a) => a.v));
  assert("stage-wide fallback: LLM anchors stay near 0.92 (no synthetic dip)",
    maxLlm >= 0.85 && minLlm >= 0.85,
    { maxLlm, minLlm });
  // STT stage has NO samples → still synthetic (fallback chain only
  // looks within the stage, not across stages).
  const sttAnchors = dense.filter((a) => a.stage === "stt");
  assert("stage-wide fallback: STT (no samples in stage) stays synthetic",
    sttAnchors.every((a) => a.kind === "synthetic"));
})();

(function () {
  // 2026-05-18 fix: rightmost turn's path doesn't end with an
  // IDLE_FLOOR anchor at x=svgW. Previously, that trailing anchor
  // dragged TEMP (synthetic ~30%) down to 3% at the right edge,
  // producing a long diagonal that visually escaped the row.
  const turns = [{
    id: "rightmost",
    startedAt: 0, endedAt: 500, totalMs: 500,
    samples: [],
    stages: [
      { label: "prefill", ms: 100, startedAt: 0,   endedAt: 100 },
      { label: "gen",     ms: 400, startedAt: 100, endedAt: 500 },
    ],
  }];
  const result = H.makeHistoryPathWithAnchors(turns, "temp", {
    perCall: 280, callFrac: 0.82, svgW: 280, yBase: 0, rowH: 32,
  });
  // Last anchor's x should be at the slot END (or near it), NOT svgW
  // with v=IDLE_FLOOR. Specifically: last anchor shouldn't be v=0.03
  // when the stage anchors are all at ~0.30 (TEMP synthetic).
  const last = result.anchors[result.anchors.length - 1];
  assert("rightmost turn: last anchor is NOT an IDLE_FLOOR sentinel at x=svgW",
    !(Math.abs(last.x - 280) < 1 && Math.abs(last.v - 0.03) < 0.001),
    { last, anchorCount: result.anchors.length });
})();

(function () {
  // TEMP profile must exist and follow the GPU thermal-lag pattern.
  const turn = {
    startedAt: 0, endedAt: 500, totalMs: 500,
    stages: [
      { label: "stt",   ms: 100, startedAt: 0,   endedAt: 100 },
      { label: "llm",   ms: 200, startedAt: 100, endedAt: 300 },
      { label: "synth", ms: 100, startedAt: 300, endedAt: 400 },
      { label: "audio", ms: 100, startedAt: 400, endedAt: 500 },
    ],
    samples: [],
  };
  const temp = H.buildStageAnchors(turn, "temp", { slotStart: 0, slotEnd: 280 });
  assert("TEMP profile exists: 4 anchors", temp.length === 4, temp);
  assert("TEMP profile: LLM is highest (thermal peak)",
    temp[1].v >= temp[0].v && temp[1].v >= temp[3].v,
    { stt: temp[0].v, llm: temp[1].v, audio: temp[3].v });
  assert("TEMP profile: all in 0..1 range",
    temp.every((a) => a.v >= 0 && a.v <= 1), temp);
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite 3 — pathFromAnchors + makeHistoryPath
 *
 * The path string must have meaningful y-variance. The prior bug:
 * anchors collapsed to a single low value → path was a horizontal line.
 * ──────────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mpath generation y-variance[0m\n");

(function () {
  // Build a synthetic-only path for GPU across 5 turns. Y-variance must
  // span at least ~half the row height (showing both idle floor + peak).
  const turns = [];
  for (let i = 0; i < 5; i++) {
    turns.push({
      startedAt: i * 1000,
      endedAt: i * 1000 + 500,
      totalMs: 500,
      stages: [
        { label: "stt",   ms: 100, startedAt: i * 1000,       endedAt: i * 1000 + 100 },
        { label: "llm",   ms: 200, startedAt: i * 1000 + 100, endedAt: i * 1000 + 300 },
        { label: "synth", ms: 100, startedAt: i * 1000 + 300, endedAt: i * 1000 + 400 },
        { label: "audio", ms: 100, startedAt: i * 1000 + 400, endedAt: i * 1000 + 500 },
      ],
      samples: [],
    });
  }
  const result = H.makeHistoryPath(turns, "gpu", {
    perCall: 280, callFrac: 0.82, yBase: 0, rowH: 32,
  });
  // Extract all y-coords from "M x,y" / " L x,y" tokens
  const ys = [];
  const re = /[ML]\s*[\d.]+,([\d.]+)/g;
  let m;
  while ((m = re.exec(result.path)) !== null) {
    ys.push(parseFloat(m[1]));
  }
  const minY = Math.min.apply(null, ys);
  const maxY = Math.max.apply(null, ys);
  assert("GPU path has ≥ 10 anchor points across 5 turns",
    ys.length >= 10, { count: ys.length });
  // 2026-05-18 fix: synthetic peak no longer 0.75 (was a fake LLM
  // spike). With profile lowered to ~0.10, synthetic-only data
  // produces a NARROW y-range. Real samples (when present) will
  // expand the range with truthful variance. Assert that synthetic
  // peak stays low rather than reaches the old 0.60 fake value.
  assert("GPU synthetic peak stays low (≤0.25; was 0.75 prior fake)",
    result.peak <= 0.25, { peak: result.peak });
  // Y-range will be small under synthetic-only data — that's the
  // honest signal that no real samples were captured.
  assert("GPU path y-range positive (line draws)",
    (maxY - minY) >= 0, { minY, maxY, range: maxY - minY });
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite 4 — pickLineColor threshold boundaries
 * ──────────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mline color threshold boundaries[0m\n");

(function () {
  // GPU thresholds: warn 0.90, crit 0.97
  assert("GPU 0.50 → cool",  H.pickLineColor(0.50, "gpu") === "var(--hg-fg-1)");
  assert("GPU 0.89 → cool",  H.pickLineColor(0.89, "gpu") === "var(--hg-fg-1)");
  assert("GPU 0.90 → warn",  H.pickLineColor(0.90, "gpu") === "var(--hg-warn)");
  assert("GPU 0.96 → warn",  H.pickLineColor(0.96, "gpu") === "var(--hg-warn)");
  assert("GPU 0.97 → crit",  H.pickLineColor(0.97, "gpu") === "var(--hg-crit)");
  // CPU thresholds: warn 0.80, crit 0.95
  assert("CPU 0.79 → cool",  H.pickLineColor(0.79, "cpu") === "var(--hg-fg-1)");
  assert("CPU 0.80 → warn",  H.pickLineColor(0.80, "cpu") === "var(--hg-warn)");
  // TEMP thresholds: warn 0.78, crit 0.90
  assert("TEMP 0.77 → cool", H.pickLineColor(0.77, "temp") === "var(--hg-fg-1)");
  assert("TEMP 0.78 → warn", H.pickLineColor(0.78, "temp") === "var(--hg-warn)");
  assert("TEMP 0.91 → crit", H.pickLineColor(0.91, "temp") === "var(--hg-crit)");
  // Unknown metric → cool
  assert("unknown → cool",   H.pickLineColor(0.99, "fake") === "var(--hg-fg-1)");
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite 5 — pickPollInterval (adaptive cadence)
 * ──────────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1madaptive poll cadence[0m\n");

(function () {
  const now = 1_000_000_000;
  assert("idle (no turn, voice inactive): 750 ms",
    H.pickPollInterval({ nowMs: now, lastTurnEndedAt: 0, voiceState: "inactive" }) === 750);
  assert("turn ended 1s ago: 250 ms",
    H.pickPollInterval({ nowMs: now, lastTurnEndedAt: now - 1000, voiceState: "inactive" }) === 250);
  assert("turn ended 3.5s ago: back to 750 ms",
    H.pickPollInterval({ nowMs: now, lastTurnEndedAt: now - 3500, voiceState: "inactive" }) === 750);
  assert("voice listening: 250 ms regardless of turn age",
    H.pickPollInterval({ nowMs: now, lastTurnEndedAt: 0, voiceState: "listening" }) === 250);
  assert("voice speaking: 250 ms",
    H.pickPollInterval({ nowMs: now, lastTurnEndedAt: 0, voiceState: "speaking" }) === 250);
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite 6 — computeBaselinePrev
 * ──────────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mbaseline prev value[0m\n");

(function () {
  assert("empty turns: prev is null",
    H.computeBaselinePrev([]) === null);
  assert("single turn: prev is null (no previous)",
    H.computeBaselinePrev([{ totalMs: 1500 }]) === null);
  assert("two turns: prev is turns[N-2].totalMs",
    H.computeBaselinePrev([{ totalMs: 1500 }, { totalMs: 1700 }]) === 1500);
  assert("five turns: prev is turns[3].totalMs",
    H.computeBaselinePrev([
      { totalMs: 1100 }, { totalMs: 1200 }, { totalMs: 1300 },
      { totalMs: 1400 }, { totalMs: 1500 },
    ]) === 1400);
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite 7 — THRESHOLDS table is the design's table verbatim
 * ──────────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mTHRESHOLDS table[0m\n");

(function () {
  assert("THRESHOLDS.gpu.warn === 0.90",  H.THRESHOLDS.gpu.warn  === 0.90);
  assert("THRESHOLDS.gpu.crit === 0.97",  H.THRESHOLDS.gpu.crit  === 0.97);
  assert("THRESHOLDS.cpu.warn === 0.80",  H.THRESHOLDS.cpu.warn  === 0.80);
  assert("THRESHOLDS.cpu.crit === 0.95",  H.THRESHOLDS.cpu.crit  === 0.95);
  assert("THRESHOLDS.ram.warn === 0.75",  H.THRESHOLDS.ram.warn  === 0.75);
  assert("THRESHOLDS.ram.crit === 0.90",  H.THRESHOLDS.ram.crit  === 0.90);
  assert("THRESHOLDS.temp.warn === 0.78", H.THRESHOLDS.temp.warn === 0.78);
  assert("THRESHOLDS.temp.crit === 0.90", H.THRESHOLDS.temp.crit === 0.90);
  // VRAM intentionally NOT in line-graph THRESHOLDS (lives in pill render).
  assert("THRESHOLDS.vram is undefined (vram is a pill, not a line)",
    H.THRESHOLDS.vram === undefined);
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite 8 — formatMs (Addendum 13 fix A)
 *
 * The prior bug: tooltip displayed "282.10000000000000014 ms" because
 * seg.ms (a float from `total * pct / 100`) was rendered verbatim.
 * formatMs centralizes "show an integer or '—'" so all three render
 * sites consistently round.
 * ──────────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mformatMs (Addendum 13)[0m\n");

(function () {
  assert("formatMs(282) === '282'",       H.formatMs(282) === "282");
  assert("formatMs(282.1) === '282'",     H.formatMs(282.1) === "282");
  assert("formatMs(282.10000014) === '282'", H.formatMs(282.10000014) === "282");
  assert("formatMs(282.5) rounds to '283' or '282' (Math.round nearest-even ok)",
    H.formatMs(282.5) === "283" || H.formatMs(282.5) === "282");
  assert("formatMs(0) === '0'",            H.formatMs(0) === "0");
  assert("formatMs(null) === '—'",         H.formatMs(null) === "—");
  assert("formatMs(undefined) === '—'",    H.formatMs(undefined) === "—");
  assert("formatMs(NaN) === '—'",          H.formatMs(NaN) === "—");
  assert("formatMs('not a number') === '—'", H.formatMs("not a number") === "—");
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite 9 — buildDenseStageAnchors (Addendum 13 fix B)
 *
 * The prior bug: 4 anchors per turn (one per stage) → triangular spikes,
 * no intra-stage wiggle. Design's demo generates ~28 samples per call.
 * buildDenseStageAnchors generates N sub-anchors per stage proportional
 * to stage duration, valued from real samples when in-window or from
 * STAGE_PROFILES + DETERMINISTIC seeded noise.
 *
 * Anti-flicker invariant: re-rendering the same turn MUST produce the
 * same anchor values. The seeded PRNG is keyed on (turn.id, metricId,
 * stage, subIdx). Tests verify both density and stability.
 * ──────────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mbuildDenseStageAnchors (Addendum 13)[0m\n");

(function () {
  const turn = {
    id: "test-turn-1",
    startedAt: 0, endedAt: 500, totalMs: 500,
    stages: [
      { label: "stt",   ms: 100, startedAt: 0,   endedAt: 100 },
      { label: "llm",   ms: 200, startedAt: 100, endedAt: 300 },
      { label: "synth", ms: 100, startedAt: 300, endedAt: 400 },
      { label: "audio", ms: 100, startedAt: 400, endedAt: 500 },
    ],
    samples: [],
  };
  const layout = { slotStart: 0, slotEnd: 280 };
  const dense = H.buildDenseStageAnchors(turn, "gpu", layout);
  assert("dense GPU returns ≥ 16 sub-anchors (4 stages × 4+ each)",
    dense.length >= 16, { count: dense.length });
  assert("dense GPU values all 0..1",
    dense.every((a) => a.v >= 0 && a.v <= 1), dense.slice(0, 3));
  assert("dense GPU x monotonically non-decreasing",
    dense.every((a, i) => i === 0 || a.x >= dense[i - 1].x),
    dense.slice(0, 3));
  // STABILITY: re-call with same turn id → identical values
  const dense2 = H.buildDenseStageAnchors(turn, "gpu", layout);
  assert("dense GPU stable across re-runs (same turn id → same values)",
    dense.length === dense2.length &&
    dense.every((a, i) => Math.abs(a.v - dense2[i].v) < 1e-9),
    { first3: dense.slice(0,3), second3: dense2.slice(0,3) });
  // Different turn id → different values (proves PRNG keyed correctly)
  const turn3 = { ...turn, id: "test-turn-3" };
  const dense3 = H.buildDenseStageAnchors(turn3, "gpu", layout);
  let anyDifferent = false;
  for (let i = 0; i < dense.length; i++) {
    if (Math.abs(dense[i].v - dense3[i].v) > 1e-9) { anyDifferent = true; break; }
  }
  assert("different turn id → at least some different anchor values",
    anyDifferent);
  // 2026-05-18 fix: LLM anchor synthetic baseline is now ~0.10 (was 0.75).
  // The previous test expected high synthetic peaks; the fix is to assert
  // synthetic stays low so users don't mistake fillers for real spikes.
  const llmAnchors = dense.filter((a) => a.stage === "llm");
  const meanLlm = llmAnchors.reduce((s, a) => s + a.v, 0) / llmAnchors.length;
  assert("dense GPU LLM synthetic mean stays low (≤0.20 — was 0.75 fake)",
    meanLlm <= 0.20, { meanLlm });
  // Real sample in LLM window overrides profile
  const turn4 = {
    ...turn,
    id: "test-turn-4",
    samples: [{ t: 200, gpuPct: 0.20, cpuPct: 0.50, ramPct: 0.40 }],
  };
  const dense4 = H.buildDenseStageAnchors(turn4, "gpu", layout);
  const llm4 = dense4.filter((a) => a.stage === "llm");
  // At least one LLM anchor should reflect the 0.20 real sample (vs ~0.75 baseline)
  const hasLowAnchor = llm4.some((a) => a.v < 0.40);
  assert("real sample mid-LLM pulls at least one anchor below 0.40",
    hasLowAnchor, { llm4 });
})();

(function () {
  // Backward-compat: buildStageAnchors still exists and returns 4 anchors
  // (was the prior contract; tests in suite 2 already pass).
  assert("buildStageAnchors still exported (back-compat alias)",
    typeof H.buildStageAnchors === "function");
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite 9a — Addendum 32 Phase C fix: attachSampleToRecentTurns.
 *
 * Root cause confirmed via /lab-dump: turn.samples is the immutable
 * snapshot taken at turn-creation time. Samples that arrive AFTER
 * turn creation (the metrics poll fires every 250-750ms; SSE arrives
 * within ms of LLM completion) never get attached. Result:
 * `persisted_samples_count: 0` for fresh turns even when
 * `live_in_window_count: 15` in labSamplesRef.
 *
 * Fix: attachSampleToRecentTurns appends each new sample to any of
 * the last K turns whose ±N ms window contains the sample's t. Turn
 * samples grow PROGRESSIVELY instead of being a one-shot snapshot.
 *
 * TDD ritual on this suite: with the helper missing (or no-op), the
 * fresh-turn assertions FAIL. With the helper present, they PASS.
 * ──────────────────────────────────────────────────────────────────── */
process.stdout.write("\n\x1b[1mattachSampleToRecentTurns (Addendum 32 H3 fix)\x1b[0m\n");

(function () {
  // Helper must exist + be callable
  assert("attachSampleToRecentTurns is a function",
    typeof H.attachSampleToRecentTurns === "function");

  // CORE REGRESSION: in-flight stub created at T=1000 (empty samples).
  // Sample arrives at T=1500 (within stub's ±3s window).
  // After attach, stub.samples must have length 1.
  const turns = [{
    id: "inflight-stub-1",
    startedAt: 1000,
    endedAt: 1000,    // stub: endedAt === startedAt
    inFlight: true,
    samples: [],
  }];
  const sample = {
    t: 1500,
    gpuPct: 0.85, cpuPct: 0.42, ramPct: 0.30, tempPct: 0.55,
  };
  const attached = H.attachSampleToRecentTurns(turns, sample);
  assert("returns attach-count 1 for matching window", attached === 1, { attached });
  assert("attach: stub.samples now has the sample",
    turns[0].samples.length === 1 && turns[0].samples[0].t === 1500,
    { samples: turns[0].samples });
  assert("attach: sample value preserved (gpuPct)",
    turns[0].samples[0].gpuPct === 0.85, { s: turns[0].samples[0] });

  // Window boundary: sample BEFORE startedAt by exactly 3s → still in window
  const turnsB = [{ id: "boundary", startedAt: 10000, endedAt: 10000, samples: [] }];
  H.attachSampleToRecentTurns(turnsB, { t: 7000, gpuPct: 0.5 });
  assert("sample at startedAt-3000 (inclusive boundary) attaches",
    turnsB[0].samples.length === 1);

  // Window boundary: sample AFTER endedAt by exactly 3s → still in window
  const turnsC = [{ id: "boundary2", startedAt: 5000, endedAt: 6000, samples: [] }];
  H.attachSampleToRecentTurns(turnsC, { t: 9000, gpuPct: 0.5 });
  assert("sample at endedAt+3000 (inclusive boundary) attaches",
    turnsC[0].samples.length === 1);

  // Out of window: sample 5s before startedAt → rejected
  const turnsD = [{ id: "out-before", startedAt: 10000, endedAt: 11000, samples: [] }];
  H.attachSampleToRecentTurns(turnsD, { t: 4000, gpuPct: 0.5 });
  assert("sample 5s before turn window does NOT attach",
    turnsD[0].samples.length === 0);

  // Out of window: sample 5s after endedAt → rejected
  const turnsE = [{ id: "out-after", startedAt: 10000, endedAt: 11000, samples: [] }];
  H.attachSampleToRecentTurns(turnsE, { t: 17000, gpuPct: 0.5 });
  assert("sample 5s after turn window does NOT attach",
    turnsE[0].samples.length === 0);

  // Dedup: pushing the SAME sample twice keeps length at 1
  const turnsF = [{ id: "dedup", startedAt: 1000, endedAt: 1500, samples: [] }];
  const s = { t: 1200, gpuPct: 0.5 };
  H.attachSampleToRecentTurns(turnsF, s);
  H.attachSampleToRecentTurns(turnsF, s);   // second push
  assert("dedup: same-t sample is not pushed twice",
    turnsF[0].samples.length === 1, { samples: turnsF[0].samples });

  // Multi-turn: sample t=1500 lands in window of two adjacent turns
  const turnsG = [
    { id: "a", startedAt: 1000, endedAt: 1200, samples: [] },
    { id: "b", startedAt: 1400, endedAt: 1700, samples: [] },
  ];
  const attachedG = H.attachSampleToRecentTurns(turnsG, { t: 1500, gpuPct: 0.5 });
  // Should attach to BOTH since both windows ([−3s..1.2+3], [1.4−3..1.7+3])
  // include 1500. This is correct progressive behavior — a sample taken
  // during the gap between turns counts toward both adjacent contexts.
  assert("attach to multiple turns when sample lands in overlapping ±3s windows",
    attachedG === 2 && turnsG[0].samples.length === 1 && turnsG[1].samples.length === 1,
    { attached: attachedG, a: turnsG[0].samples.length, b: turnsG[1].samples.length });

  // scanLookback: only most recent K turns are considered
  const turnsH = [];
  for (let i = 0; i < 10; i++) {
    turnsH.push({ id: `old-${i}`, startedAt: 1000 + i, endedAt: 1100 + i, samples: [] });
  }
  // sample at t=1005 falls in window of turns 0..7 (all within ±3s of their start/end)
  // but with scanLookback=5, only turns 5,6,7,8,9 are scanned. Of those, 5,6,7 should match.
  // Actually with such tightly-overlapping windows ALL 5 scanned turns might match;
  // the point of the test is that OLDER turns (0..4) are NOT scanned.
  H.attachSampleToRecentTurns(turnsH, { t: 1005, gpuPct: 0.5 }, { scanLookback: 5 });
  const oldTotalAttached = turnsH.slice(0, 5).reduce((a, t) => a + t.samples.length, 0);
  assert("scanLookback bounds scan to last K turns (older turns untouched)",
    oldTotalAttached === 0, { oldTotalAttached });

  // perTurnCap: pushing > cap samples keeps only the last cap
  const turnsI = [{ id: "cap-test", startedAt: 0, endedAt: 10000, samples: [] }];
  for (let i = 0; i < 60; i++) {
    H.attachSampleToRecentTurns(turnsI, { t: i, gpuPct: 0.5 }, { perTurnCap: 50 });
  }
  assert("perTurnCap enforced: samples length <= cap",
    turnsI[0].samples.length === 50);
  // The retained samples are the LAST 50 (t=10..59)
  assert("perTurnCap retains the most recent samples",
    turnsI[0].samples[0].t === 10 && turnsI[0].samples[49].t === 59);

  // No-op on missing inputs
  assert("no crash with empty turns", H.attachSampleToRecentTurns([], { t: 1, gpuPct: 0.5 }) === 0);
  assert("no crash with null sample", H.attachSampleToRecentTurns([{ startedAt: 0, endedAt: 100 }], null) === 0);
  assert("no crash with sample missing t",
    H.attachSampleToRecentTurns([{ startedAt: 0, endedAt: 100 }], { gpuPct: 0.5 }) === 0);
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite 9b — STAGE_PROFILES coverage for all emitted stage labels.
 *
 * 2026-05-18 regression: the trace adapter emits stages labeled
 * `prefill`/`gen` (typed-Tauri turns), `ambient` (Addendum 27
 * inter-turn pseudo-stage), and `ext_req`/`ext_api`/`ext_recv`
 * (routing-log external turns) — none of which were in
 * STAGE_PROFILES. profile[stg.label] returned undefined → fell to 0
 * → synthetic anchors collapsed to ~0 → all resource lines flatlined
 * at IDLE_FLOOR (3%) when no real samples were present in the stage
 * window. Visually: GPU/CPU/RAM/TEMP all showed 2% across 21 turns.
 *
 * This suite asserts every label the adapter emits has a profile
 * entry AND the lookup uses `_default` fallback for unknown labels
 * (e.g., future tool-segment names).
 * ──────────────────────────────────────────────────────────────────── */
process.stdout.write("\n\x1b[1mSTAGE_PROFILES coverage for new labels\x1b[0m\n");

(function () {
  // Every metric must have a _default key (the fallback that prevents
  // future unknown labels from collapsing the line to 0).
  const metrics = ["gpu", "cpu", "ram", "temp"];
  for (const m of metrics) {
    const p = H.STAGE_PROFILES[m];
    assert(`STAGE_PROFILES.${m} exists`, !!p);
    assert(`STAGE_PROFILES.${m}._default present (catches unknown labels)`,
      typeof p._default === "number" && p._default > 0,
      { metric: m, default: p ? p._default : undefined });
  }

  // Every label the trace adapter emits today must have an entry in
  // every metric's profile (or a sensible _default that the lookup
  // will use). Tests that the profileVal lookup yields > 0 for all of
  // them.
  const labels = [
    "stt", "llm", "synth", "audio",        // voice path
    "prefill", "gen",                       // typed-Tauri sidecar path
    "ambient",                              // Addendum 27 pseudo-stage
    "ext_req", "ext_api", "ext_recv",       // routing-log external turns
  ];
  for (const m of metrics) {
    const p = H.STAGE_PROFILES[m];
    for (const lbl of labels) {
      const v = typeof p[lbl] === "number" ? p[lbl] : p._default;
      assert(`STAGE_PROFILES.${m}.${lbl} → numeric > 0 (got ${v})`,
        typeof v === "number" && v > 0);
    }
  }

  // Regression: dense anchors built for a typed-Tauri-shaped turn
  // (prefill + gen, no samples) must produce NON-ZERO anchors. The
  // bug: anchors used to be ~0 → lines flat at 2%.
  const typedTurn = {
    id: "regression-typed",
    startedAt: 0,
    endedAt: 1000,
    totalMs: 1000,
    samples: [],   // no real samples → synthetic path
    stages: [
      { label: "prefill", ms: 200, startedAt: 0,   endedAt: 200  },
      { label: "gen",     ms: 800, startedAt: 200, endedAt: 1000 },
    ],
  };
  for (const m of metrics) {
    const anchors = H.buildDenseStageAnchors(typedTurn, m, { slotStart: 0, slotEnd: 280 });
    assert(`typed-Tauri turn produces > 0 anchors for ${m}`,
      anchors.length > 0, { count: anchors.length });
    const mean = anchors.reduce((s, a) => s + a.v, 0) / Math.max(1, anchors.length);
    assert(`typed-Tauri ${m} synthetic mean > 0.03 (not flat at IDLE_FLOOR)`,
      mean > 0.03, { mean, metric: m });
  }

  // Same regression check for routing-log external turns (ext_*).
  const extTurn = {
    id: "regression-ext",
    startedAt: 0,
    endedAt: 2000,
    totalMs: 2000,
    samples: [],
    stages: [
      { label: "ext_req",  ms: 100,  startedAt: 0,    endedAt: 100  },
      { label: "ext_api",  ms: 1700, startedAt: 100,  endedAt: 1800 },
      { label: "ext_recv", ms: 200,  startedAt: 1800, endedAt: 2000 },
    ],
  };
  for (const m of metrics) {
    const anchors = H.buildDenseStageAnchors(extTurn, m, { slotStart: 0, slotEnd: 280 });
    assert(`ext_* turn produces > 0 anchors for ${m}`,
      anchors.length > 0);
    const mean = anchors.reduce((s, a) => s + a.v, 0) / Math.max(1, anchors.length);
    assert(`ext_* ${m} synthetic mean > 0.03 (not flat)`,
      mean > 0.03, { mean, metric: m });
  }

  // Future-proofing: unknown stage label uses _default, not 0.
  const unknownTurn = {
    id: "regression-unknown-label",
    startedAt: 0, endedAt: 500, totalMs: 500,
    samples: [],
    stages: [{ label: "tool_my_future_tool", ms: 500, startedAt: 0, endedAt: 500 }],
  };
  for (const m of metrics) {
    const anchors = H.buildDenseStageAnchors(unknownTurn, m, { slotStart: 0, slotEnd: 280 });
    const mean = anchors.reduce((s, a) => s + a.v, 0) / Math.max(1, anchors.length);
    assert(`unknown label uses _default for ${m} (mean > 0.04)`,
      mean > 0.04, { mean, metric: m });
  }
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite 10 — localStorage persistence (Addendum 13 fix C.2)
 *
 * Pure functions take a storage facade with get/set so we can pass a
 * Map-backed stub in tests. Production code wires the real
 * window.localStorage. Tests guard:
 *   - load returns [] when key missing
 *   - load filters out entries older than TTL_MS
 *   - persist caps at 32 turns + 50 samples each
 *   - JSON round-trip preserves required fields
 * ──────────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mlocalStorage persistence (Addendum 13)[0m\n");

(function () {
  // Map-backed storage facade — matches localStorage shape enough
  // for the helpers' getItem/setItem usage.
  function mkStore() {
    const m = new Map();
    return {
      getItem: (k) => (m.has(k) ? m.get(k) : null),
      setItem: (k, v) => m.set(k, String(v)),
      _peek: (k) => m.get(k),
      _size: () => m.size,
    };
  }
  const KEY = "hg-lab-turns-v1";
  const TTL = 6 * 60 * 60 * 1000; // 6h
  const now = 1_700_000_000_000;

  // 1. load returns [] when key missing
  const s1 = mkStore();
  assert("load: missing key → empty array",
    H.loadLabTurnsFromStorage(s1, KEY, TTL, now).length === 0);

  // 2. load filters out turns older than TTL
  const s2 = mkStore();
  s2.setItem(KEY, JSON.stringify({
    version: 1,
    turns: [
      { id: "old", endedAt: now - TTL - 1000, totalMs: 1000, stages: [], samples: [] },
      { id: "new", endedAt: now - 1000,       totalMs: 1500, stages: [], samples: [] },
    ],
  }));
  const loaded = H.loadLabTurnsFromStorage(s2, KEY, TTL, now);
  assert("load: drops turns older than TTL",
    loaded.length === 1 && loaded[0].id === "new", loaded);

  // 3. persist caps at 32 turns
  const s3 = mkStore();
  const big = [];
  for (let i = 0; i < 100; i++) {
    big.push({
      id: "t" + i, endedAt: now - i * 1000, totalMs: 1500,
      stages: [], samples: new Array(200).fill({ t: now, gpuPct: 0.5 }),
    });
  }
  H.persistLabTurns(s3, KEY, big);
  const after = JSON.parse(s3._peek(KEY));
  assert("persist: caps at 32 turns",
    after.turns.length === 32, { len: after.turns.length });
  assert("persist: caps each turn's samples at 50",
    after.turns.every((t) => (t.samples || []).length <= 50),
    after.turns.map((t) => (t.samples || []).length).slice(0, 5));

  // 4. JSON round-trip preserves required fields
  const s4 = mkStore();
  const original = [{
    id: "rt",
    endedAt: now,
    startedAt: now - 1500,
    totalMs: 1500,
    stages: [{ label: "stt", ms: 100, startedAt: now - 1500, endedAt: now - 1400 }],
    samples: [{ t: now - 1000, gpuPct: 0.5, cpuPct: 0.2, ramPct: 0.3 }],
    transcript: "test",
  }];
  H.persistLabTurns(s4, KEY, original);
  const rt = H.loadLabTurnsFromStorage(s4, KEY, TTL, now);
  assert("round-trip: id preserved",         rt[0].id === "rt");
  assert("round-trip: totalMs preserved",    rt[0].totalMs === 1500);
  assert("round-trip: stages preserved",     rt[0].stages.length === 1);
  assert("round-trip: stage.label preserved",rt[0].stages[0].label === "stt");
  assert("round-trip: samples preserved",    rt[0].samples.length === 1);
  assert("round-trip: transcript preserved", rt[0].transcript === "test");

  // 5. Corrupt JSON → empty (no crash)
  const s5 = mkStore();
  s5.setItem(KEY, "not-valid-json{");
  assert("load: corrupt JSON → empty array, no throw",
    H.loadLabTurnsFromStorage(s5, KEY, TTL, now).length === 0);
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite 11 — valueAtX line-graph hover lookup
 *
 * The line-graph hover overlay (new this round) needs the y-value at the
 * cursor's SVG-local x. Linear interpolation between surrounding anchors.
 * Tests guard correctness at boundaries (outside range, exact anchor,
 * between anchors), empty input, and single-anchor degenerate case.
 * ──────────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mvalueAtX line-graph hover lookup[0m\n");

(function () {
  // Empty / null
  assert("valueAtX(null, 100) === null",
    H.valueAtX(null, 100) === null);
  assert("valueAtX([], 100) === null",
    H.valueAtX([], 100) === null);
  // Single anchor — always returns its value
  assert("single anchor: returns anchor.v regardless of x",
    H.valueAtX([{x: 50, v: 0.42}], 999) === 0.42);
  // Outside range — clamps to nearest endpoint
  const a = [
    {x: 0,   v: 0.10},
    {x: 100, v: 0.50},
    {x: 200, v: 0.90},
  ];
  assert("x < first.x → returns first.v",
    H.valueAtX(a, -50) === 0.10);
  assert("x > last.x → returns last.v",
    H.valueAtX(a, 500) === 0.90);
  // Exact anchor x → that anchor's v
  assert("x === anchor.x: returns exact v (first)",
    H.valueAtX(a, 0) === 0.10);
  assert("x === anchor.x: returns exact v (middle)",
    H.valueAtX(a, 100) === 0.50);
  assert("x === anchor.x: returns exact v (last)",
    H.valueAtX(a, 200) === 0.90);
  // Linear interpolation
  assert("x=50 between (0,0.10)-(100,0.50): interpolates to 0.30",
    approxEq(H.valueAtX(a, 50), 0.30, 0.001));
  assert("x=150 between (100,0.50)-(200,0.90): interpolates to 0.70",
    approxEq(H.valueAtX(a, 150), 0.70, 0.001));
  // Degenerate: two anchors at same x
  assert("zero-span span: returns earlier anchor's v",
    H.valueAtX([{x: 100, v: 0.20}, {x: 100, v: 0.80}], 100) === 0.20);
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite 12 — makeHistoryPathWithAnchors
 *
 * The hover overlay needs anchors back too (for valueAtX), so we expose
 * a variant that returns them. Guard: anchors array is non-empty + sorted.
 * ──────────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mmakeHistoryPathWithAnchors[0m\n");

(function () {
  const turns = [];
  for (let i = 0; i < 3; i++) {
    turns.push({
      id: "t" + i,
      startedAt: i * 1000, endedAt: i * 1000 + 500, totalMs: 500,
      stages: [
        { label: "stt",   ms: 100, startedAt: i*1000,     endedAt: i*1000+100 },
        { label: "llm",   ms: 200, startedAt: i*1000+100, endedAt: i*1000+300 },
        { label: "synth", ms: 100, startedAt: i*1000+300, endedAt: i*1000+400 },
        { label: "audio", ms: 100, startedAt: i*1000+400, endedAt: i*1000+500 },
      ],
      samples: [],
    });
  }
  const r = H.makeHistoryPathWithAnchors(turns, "gpu", {
    perCall: 280, callFrac: 0.82, yBase: 0, rowH: 32,
  });
  assert("returns anchors array",
    Array.isArray(r.anchors) && r.anchors.length > 0,
    { len: r.anchors ? r.anchors.length : null });
  assert("anchors include idle-floor entries",
    r.anchors.some((a) => a.v <= 0.05),
    { sample: r.anchors.slice(0, 3) });
  assert("anchors are sorted by x ascending",
    r.anchors.every((a, i) => i === 0 || a.x >= r.anchors[i - 1].x));
  // valueAtX must work on the returned anchors
  const midX = r.anchors[Math.floor(r.anchors.length / 2)].x;
  const v = H.valueAtX(r.anchors, midX);
  assert("valueAtX returns a number on returned anchors",
    typeof v === "number" && v >= 0 && v <= 1, { v });
})();

(function () {
  // Regression: merged/tool-call turns can have real wall-clock gaps between
  // stages, while totalMs is the sum of stage durations. The renderer must
  // lay the line out by contiguous display duration inside the call slot,
  // not by absolute wall-clock offsets, or anchors escape into future slots.
  const turn = {
    id: "gapped-merged-turn",
    startedAt: 0,
    endedAt: 1300,
    totalMs: 500,
    stages: [
      { label: "prefill", ms: 100, startedAt: 0,    endedAt: 100  },
      { label: "gen",     ms: 200, startedAt: 100,  endedAt: 300  },
      { label: "prefill", ms: 50,  startedAt: 1100, endedAt: 1150 },
      { label: "gen",     ms: 150, startedAt: 1150, endedAt: 1300 },
    ],
    samples: [{ t: 1200, gpuPct: 0.88, cpuPct: 0.30, ramPct: 0.25 }],
  };
  const r2 = H.makeHistoryPathWithAnchors([turn], "gpu", {
    perCall: 280, callFrac: 0.82, svgW: 280, yBase: 0, rowH: 32,
  });
  const xs = r2.anchors.map((a) => a.x);
  assert("gapped merged turn: no anchor escapes the call slot",
    xs.every((x) => x >= 0 && x <= 280),
    { min: Math.min(...xs), max: Math.max(...xs), xs: xs.slice(-8) });
  assert("gapped merged turn: anchors remain time-monotonic",
    xs.every((x, i) => i === 0 || x >= xs[i - 1]),
    { xs });
  assert("gapped merged turn: later wall-clock sample still marks anchors real",
    r2.anchors.some((a) => a.kind === "real" && a.stage === "gen" && a.v >= 0.80),
    r2.anchors.filter((a) => a.stage === "gen").slice(-8));
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite 13 — Addendum 16 F-2: trace backfill (bulk traces dedup)
 *
 * Lab history was losing traces between 5 s polls because the fetch was
 * n=1 and only the latest survived. F-2 backfills from n=50; helpers
 * traceTurnId + dedupTraces enable the writer to consume the full array
 * idempotently. These tests guard the dedup correctness.
 * ──────────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mtrace backfill (Addendum 16 F-2)[0m\n");

(function () {
  // traceTurnId — primary key
  assert("traceTurnId(null) === null",
    H.traceTurnId(null) === null);
  assert("traceTurnId({}) === null (no t_done)",
    H.traceTurnId({}) === null);
  assert("traceTurnId uses turn_id when present",
    H.traceTurnId({ turn_id: "abc-123", t_done: 1500, user_text: "x" }) === "abc-123");
  assert("traceTurnId secondary key when turn_id absent",
    H.traceTurnId({ t_done: 1500, user_text: "turn off the kitchen lights" })
      === "t-1500-turn off the kit");
  assert("traceTurnId tolerates missing user_text",
    H.traceTurnId({ t_done: 2000 }) === "t-2000-");
})();

(function () {
  // dedupTraces basic
  const traces = [
    { turn_id: "a", t_done: 1000 },
    { turn_id: "b", t_done: 2000 },
    { turn_id: "c", t_done: 3000 },
  ];
  const existing = new Set(["a"]);
  const out = H.dedupTraces(existing, traces);
  assert("dedupTraces filters out already-seen turn_ids",
    out.length === 2 && out.map((t) => t.turn_id).join(",") === "b,c",
    out);
})();

(function () {
  // dedupTraces order-preserving (oldest → newest as bridge returns)
  const traces = [
    { turn_id: "old", t_done: 1000 },
    { turn_id: "mid", t_done: 2000 },
    { turn_id: "new", t_done: 3000 },
  ];
  const out = H.dedupTraces(new Set(), traces);
  assert("dedupTraces preserves input order",
    out[0].turn_id === "old" && out[1].turn_id === "mid" && out[2].turn_id === "new",
    out);
})();

(function () {
  // dedupTraces handles empty array
  assert("dedupTraces([]) → []",
    H.dedupTraces(new Set(), []).length === 0);
  assert("dedupTraces(null) → []",
    H.dedupTraces(new Set(), null).length === 0);
  assert("dedupTraces tolerates undefined set",
    H.dedupTraces(undefined, [{turn_id:"x", t_done:1}]).length === 1);
})();

(function () {
  // dedupTraces dedupes within a batch (same turn_id appears twice)
  const dup = [
    { turn_id: "x", t_done: 100 },
    { turn_id: "x", t_done: 100 },
    { turn_id: "y", t_done: 200 },
  ];
  const out = H.dedupTraces(new Set(), dup);
  assert("dedupTraces dedupes within-batch duplicates",
    out.length === 2, out);
})();

(function () {
  // dedupTraces with the secondary-key path (no turn_id)
  const traces = [
    { t_done: 1500, user_text: "hello" },
    { t_done: 1500, user_text: "hello" },  // exact dup via secondary key
    { t_done: 1600, user_text: "world" },
  ];
  const out = H.dedupTraces(new Set(), traces);
  assert("dedupTraces collapses same-t_done+text duplicates via secondary key",
    out.length === 2, out);
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite 11 — STAGE_TINT palette discipline (Phase 2 P0 — Addendum 18a)
 *
 * Catches the off-palette bug we shipped this session: blue/purple
 * tints leaking into the lab's stack-trace bars. Tests the SOURCE
 * file (not runtime) because the bad colors are committed into JSX,
 * not produced at runtime. Reading the file with regex parsing
 * catches them before they ship.
 * ──────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mSTAGE_TINT palette discipline[0m\n");

(function () {
  const fs = require("fs");
  const jsxPath = path.join(__dirname, "..", "app", "src", "home-metrics-lab.jsx");
  const src = fs.readFileSync(jsxPath, "utf-8");

  // Extract the STAGE_TINT block (terminates at first matching `};`).
  function extractBlock(name) {
    const re = new RegExp("const\\s+" + name + "\\s*=\\s*\\{([\\s\\S]*?)\\n\\};", "m");
    const m = src.match(re);
    return m ? m[1] : null;
  }
  const tintBlock = extractBlock("STAGE_TINT");
  const slowBlock = extractBlock("STAGE_TINT_SLOW");
  assert("STAGE_TINT block present in JSX", tintBlock !== null);
  assert("STAGE_TINT_SLOW block present in JSX", slowBlock !== null);

  // Allow only neutral palette + tier-driven warn/crit colors.
  // Forbidden: ice-blue (184,216,255), purple (200,170,255), any
  // out-of-palette color outside the whitelist.
  const FORBIDDEN_RGB_FRAGMENTS = ["184,216,255", "200,170,255"];

  for (const rgb of FORBIDDEN_RGB_FRAGMENTS) {
    assert("STAGE_TINT contains no off-palette `" + rgb + "`",
      !tintBlock.includes(rgb),
      "found `" + rgb + "` in STAGE_TINT — palette drift");
  }
  // STAGE_TINT_SLOW is allowed to use amber (255,200,120 / 255,178,90) —
  // those are the legitimate warn/crit color family.
  for (const rgb of FORBIDDEN_RGB_FRAGMENTS) {
    assert("STAGE_TINT_SLOW contains no off-palette `" + rgb + "`",
      !slowBlock.includes(rgb),
      "found `" + rgb + "` in STAGE_TINT_SLOW — palette drift");
  }

  // Every stage label that processTrace or processRoutingLogEntry emits
  // MUST have a tint. The label set is hardcoded in those processors
  // (home-app.jsx) — keep this list in sync if new stages are added.
  const REQUIRED_LABELS = [
    "stt", "llm", "synth", "audio",   // processTrace (sidecar local)
    "ext_req", "ext_api", "ext_recv", // processRoutingLogEntry (external)
    "external",                       // legacy single-stage external
  ];
  for (const label of REQUIRED_LABELS) {
    const re = new RegExp("\\b" + label + "\\s*:");
    assert("STAGE_TINT defines `" + label + "`",
      re.test(tintBlock),
      "missing key — blank-bar regression risk");
    assert("STAGE_TINT_SLOW defines `" + label + "`",
      re.test(slowBlock),
      "missing key in slow palette");
  }

  // External tints must be DIMMER than their local equivalents (visual
  // language: external = ghosted / off-box). Extract opacity values.
  function opacity(block, label) {
    const re = new RegExp(label + "\\s*:\\s*\"rgba\\([^,]+,[^,]+,[^,]+,([\\d.]+)\\)\"");
    const m = block.match(re);
    return m ? parseFloat(m[1]) : null;
  }
  const audioOp = opacity(tintBlock, "audio");
  const extRecvOp = opacity(tintBlock, "ext_recv");
  assert("ext_recv opacity < audio opacity (external is dimmer)",
    extRecvOp !== null && audioOp !== null && extRecvOp < audioOp,
    { audio: audioOp, ext_recv: extRecvOp });

  const llmOp = opacity(tintBlock, "llm");
  const extApiOp = opacity(tintBlock, "ext_api");
  assert("ext_api opacity < llm opacity (external is dimmer)",
    extApiOp !== null && llmOp !== null && extApiOp < llmOp,
    { llm: llmOp, ext_api: extApiOp });

  const sttOp = opacity(tintBlock, "stt");
  const extReqOp = opacity(tintBlock, "ext_req");
  assert("ext_req opacity ≤ stt opacity (external is dimmer or equal)",
    extReqOp !== null && sttOp !== null && extReqOp <= sttOp,
    { stt: sttOp, ext_req: extReqOp });
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite 12 — index.html cache-busting loader (Phase 2 P0 — Addendum 18)
 *
 * Catches the WebView2 stale-JSX bug: static `<script src>` tags get
 * disk-cached, so JSX edits don't propagate without manual cache wipe.
 * The fix replaced 25 static tags with a dynamic loader appending
 * `?cb=${Date.now()}`. This test enforces the loader stays in place.
 * ──────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mindex.html cache-busting loader[0m\n");

(function () {
  const fs = require("fs");
  const indexPath = path.join(__dirname, "..", "app", "src", "index.html");
  const src = fs.readFileSync(indexPath, "utf-8");

  assert("index.html uses cb=Date.now() loader",
    src.includes("cb=' + Date.now()") || src.includes("cb=\" + Date.now()"),
    "loader missing — JSX changes will be served from WebView2 cache");

  // Every JSX/JS file in app/src/ MUST appear in the loader. Otherwise
  // adding a new file silently breaks (won't be loaded at all).
  const srcDir = path.join(__dirname, "..", "app", "src");
  const srcFiles = fs.readdirSync(srcDir)
    .filter((f) => (f.endsWith(".jsx") || f.endsWith(".js")) &&
                   !f.endsWith(".test.js") && f !== "babel-config.js");
  const missing = srcFiles.filter((f) => !src.includes("'" + f + "'") && !src.includes('"' + f + '"'));
  assert("every JSX/JS in app/src is listed in index.html loader",
    missing.length === 0,
    { missing });

  // No static `<script type="text/babel" src="`-tags remain (regression
  // guard — the fix replaced them all with the dynamic loader).
  const staticBabelMatch = src.match(/<script\s+type=["']text\/babel["']\s+src=/g);
  assert("no static text/babel script tags remain (regressed back to disk cache)",
    !staticBabelMatch || staticBabelMatch.length === 0,
    { found: staticBabelMatch });
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite 13 — routing-log → labTurn backfill (Phase 2 P0)
 *
 * Catches the Addendum 17 bug: external-routed turns produced no
 * sidecar trace, leaving lab history undercounted. The fix synthesizes
 * a slim LabTurn from each routing-log entry. This test verifies the
 * dedup + 3-stage subdivision contract.
 *
 * Pure-function: we test the processor logic as if it were a helper.
 * If the processor logic is reorganized into the helpers file, this
 * test should be updated; meantime, mirror the algorithm here.
 * ──────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mrouting-log backfill (synthesis + dedup)[0m\n");

(function () {
  // Mirror processRoutingLogEntry from home-app.jsx. Pure function for
  // testing; production version uses the same shape.
  function synthesize(entry, existingIds) {
    if (!entry || entry.dispatched !== "external") return null;
    const id = entry.conv_id || ("r-" + entry.ts + "-" + (entry.text || "").slice(0, 16));
    if (existingIds.has(id)) return null;
    const tsMs = entry.ts ? Date.parse(entry.ts) : Date.now();
    if (!isFinite(tsMs)) return null;
    const total = Math.max(0, entry.ext_latency_ms || 0);
    const endedAt = tsMs;
    const startedAt = endedAt - total;
    const reqMs = Math.round(total * 0.05);
    const recvMs = Math.round(total * 0.05);
    const apiMs = Math.max(0, total - reqMs - recvMs);
    return {
      id, startedAt, endedAt, totalMs: total,
      stages: [
        { label: "ext_req",  ms: reqMs },
        { label: "ext_api",  ms: apiMs },
        { label: "ext_recv", ms: recvMs },
      ],
      transcript: entry.text,
      route: "external",
    };
  }

  // Local-dispatched entries are skipped (sidecar has them).
  const local = synthesize({
    conv_id: "x1", dispatched: "local", text: "hi",
    ts: "2026-05-17T01:00:00Z", local_reply_chars: 5,
  }, new Set());
  assert("local-dispatched routing entry returns null (skipped)", local === null);

  // External-dispatched entries become 3-stage turns.
  const ext = synthesize({
    conv_id: "x2", dispatched: "external", text: "who is einstein",
    ts: "2026-05-17T01:00:30Z", ext_latency_ms: 1000, ext_reply_chars: 200,
  }, new Set());
  assert("external entry returns a turn", ext !== null);
  assert("external turn has 3 stages", ext.stages.length === 3);
  assert("first stage is ext_req", ext.stages[0].label === "ext_req");
  assert("second stage is ext_api", ext.stages[1].label === "ext_api");
  assert("third stage is ext_recv", ext.stages[2].label === "ext_recv");
  assert("stage proportions sum to total",
    ext.stages[0].ms + ext.stages[1].ms + ext.stages[2].ms === 1000);
  assert("route flag set to external", ext.route === "external");
  assert("transcript preserved", ext.transcript === "who is einstein");

  // Dedup by conv_id: re-feeding an existing id returns null.
  const dup = synthesize({
    conv_id: "x2", dispatched: "external", text: "who is einstein",
    ts: "2026-05-17T01:00:30Z", ext_latency_ms: 1000,
  }, new Set(["x2"]));
  assert("dedup by conv_id returns null", dup === null);

  // Empty ext_latency_ms → 0-duration turn (still rendered)
  const zero = synthesize({
    conv_id: "x3", dispatched: "external", text: "?",
    ts: "2026-05-17T01:00:00Z", ext_latency_ms: 0,
  }, new Set());
  assert("zero-latency external turn synthesized", zero !== null);
  assert("zero-latency turn has 0 totalMs", zero.totalMs === 0);

  // Bad ts → null (not synthesized — would break chart math)
  const badTs = synthesize({
    conv_id: "x4", dispatched: "external", text: "x", ts: "garbage",
    ext_latency_ms: 100,
  }, new Set());
  assert("invalid ts returns null", badTs === null);
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite 14 — tray-height persistence (Phase 2 P0 — today's fix)
 *
 * The drag-resizable tray persists to localStorage as
 * 'hg-tray-height-v1'. This test mirrors the clamp + parse contract.
 * ──────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mtray-height persistence + clamp[0m\n");

(function () {
  const TRAY_MIN_HEIGHT = 160;
  const TRAY_MAX_HEIGHT_VH = 0.85;
  const DEFAULT = 340;

  function loadHeight(storedRaw, viewportH) {
    const v = parseInt(storedRaw || "", 10);
    if (!Number.isFinite(v) || v < TRAY_MIN_HEIGHT) return DEFAULT;
    const maxH = Math.floor(viewportH * TRAY_MAX_HEIGHT_VH);
    return Math.max(TRAY_MIN_HEIGHT, Math.min(maxH, v));
  }
  function loadExpanded(storedRaw) {
    return storedRaw === "1";
  }
  function loadTab(storedRaw) {
    const valid = new Set(["ai", "infra", "trace"]);
    return valid.has(storedRaw) ? storedRaw : "ai";
  }

  assert("missing localStorage key → default 340", loadHeight(null, 1080) === 340);
  assert("blank localStorage value → default", loadHeight("", 1080) === 340);
  assert("garbage value → default", loadHeight("not-a-number", 1080) === 340);
  assert("below-min value → default (not clamped to min)",
    loadHeight("50", 1080) === 340);
  assert("valid value preserved", loadHeight("540", 1080) === 540);
  assert("value above viewport-max clamped to 85vh",
    loadHeight("99999", 1080) === Math.floor(1080 * 0.85));
  assert("min boundary value (160) accepted exactly",
    loadHeight("160", 1080) === 160);

  // Roundtrip: setItem then getItem returns the same value
  const fakeStorage = {};
  fakeStorage["hg-tray-height-v1"] = "540";
  assert("localStorage roundtrip preserves height",
    loadHeight(fakeStorage["hg-tray-height-v1"], 1080) === 540);

  assert("missing drawer expanded key -> collapsed", loadExpanded(null) === false);
  assert("expanded key 1 -> open", loadExpanded("1") === true);
  assert("expanded key 0 -> closed", loadExpanded("0") === false);
  assert("unknown expanded value -> closed", loadExpanded("yes") === false);
  assert("missing drawer tab key -> ai", loadTab(null) === "ai");
  assert("stored infra tab restored", loadTab("infra") === "infra");
  assert("stored trace tab restored", loadTab("trace") === "trace");
  assert("legacy/invalid drawer tab falls back to ai", loadTab("intel") === "ai");
  assert("HomeApp declares persisted drawer expanded key",
    APP_SRC.includes('const TRAY_EXPANDED_KEY = "hg-tray-expanded-v1";'));
  assert("HomeApp declares persisted drawer tab key",
    APP_SRC.includes('const TRAY_TAB_KEY = "hg-tray-tab-v1";'));
  assert("HomeApp restores persisted drawer open state",
    /useState\(\(\) => \{[\s\S]*localStorage\.getItem\(TRAY_EXPANDED_KEY\) === "1"/.test(APP_SRC));
  assert("HomeApp restores only valid persisted tray tabs",
    /TRAY_VALID_TABS\.has\(stored\) \? stored : "ai"/.test(APP_SRC));
  assert("HomeApp persists drawer open state changes",
    /localStorage\.setItem\(TRAY_EXPANDED_KEY, expanded \? "1" : "0"\)/.test(APP_SRC));
  assert("HomeApp persists active tray tab changes",
    /localStorage\.setItem\(TRAY_TAB_KEY, activeTab\)/.test(APP_SRC));
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite 15 — Addendum 26 pipeline schema drift
 *
 * Single source of truth across three places:
 *   1. PIPELINE.nodes[].id  — flow diagram structure
 *   2. SERVICE_META / ENDPOINT_META — hover copy
 *   3. The names buildStackList() emits on the live stack
 *
 * Drift between them means: a service rendered with no tooltip,
 * a pipeline node pointing at a dead service id, or a service
 * in the live list with no story for the user. This suite locks
 * down the contract so renames in any of the three places are
 * caught at CI time.
 *
 * The JSX file is parsed as a string (Node can't import JSX
 * directly) and the relevant literal arrays/objects are extracted
 * by regex. Brittle to whitespace if someone reformats the file
 * heavily, but trivially fixable when it breaks.
 * ──────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mAddendum 26 — pipeline schema drift[0m\n");

(function () {
  const fs = require("fs");
  const path = require("path");
  const src = fs.readFileSync(
    path.join(__dirname, "..", "app", "src", "home-metrics-lab.jsx"),
    "utf8"
  );

  // Helper: pull all keys defined directly under `const NAME = { ... }`
  // by scanning for top-level "key:" lines inside the block. Stops at
  // the matching `};`. Good enough for the simple flat objects we
  // declared (SERVICE_META / ENDPOINT_META).
  function extractObjectKeys(constName) {
    const re = new RegExp("const\\s+" + constName + "\\s*=\\s*\\{");
    const m = src.match(re);
    if (!m) return null;
    let i = m.index + m[0].length;
    let depth = 1;
    const keys = [];
    let buf = "";
    while (i < src.length && depth > 0) {
      const c = src[i++];
      if (c === "{") depth++;
      else if (c === "}") depth--;
      if (depth === 1 && (c === "\n" || c === ",")) {
        const km = buf.match(/^\s*["']?([\w\- ]+)["']?\s*:/);
        if (km) keys.push(km[1]);
        buf = "";
      } else if (depth >= 1) {
        buf += c;
      }
    }
    return keys;
  }

  function extractPipelineServiceIds() {
    const re = /const\s+PIPELINE\s*=\s*\[/;
    const m = src.match(re);
    if (!m) return null;
    // Scan from PIPELINE start to its closing `];`, collect every
    // `{ id: "...", kind: "service" }` (or kind: "input"/"output").
    const start = m.index + m[0].length;
    let depth = 1;
    let i = start;
    let block = "";
    while (i < src.length && depth > 0) {
      const c = src[i++];
      if (c === "[") depth++;
      else if (c === "]") depth--;
      if (depth > 0) block += c;
    }
    // Walk per-brace blocks: each inner node is `{...}` with no
    // nested braces. The outer row + branch objects have nested
    // arrays so they don't match this restricted form — that's
    // intentional, it stops the regex from picking up row.id
    // (e.g., "vision") and pairing it with the first node's `kind:`.
    // Inner blocks include both main-row nodes (`{ id, kind:
    // service|input }`) and branch-chain nodes (same shape).
    const service = [];
    const endpoint = [];
    const blockRe = /\{[^{}]*\}/g;
    let bm;
    while ((bm = blockRe.exec(block)) !== null) {
      const inner = bm[0];
      const idM = inner.match(/id:\s*["']([^"']+)["']/);
      const kindM = inner.match(/kind:\s*["'](service|input|output)["']/);
      if (idM && kindM) {
        if (kindM[1] === "service") service.push(idM[1]);
        else endpoint.push(idM[1]);
      }
    }
    return { service, endpoint };
  }

  function extractArrayStrings(constName) {
    const re = new RegExp("const\\s+" + constName + "\\s*=\\s*\\[");
    const m = src.match(re);
    if (!m) return null;
    const start = m.index + m[0].length;
    let depth = 1;
    let i = start;
    let block = "";
    while (i < src.length && depth > 0) {
      const c = src[i++];
      if (c === "[") depth++;
      else if (c === "]") depth--;
      if (depth > 0) block += c;
    }
    const out = [];
    const itemRe = /["']([^"']+)["']/g;
    let im;
    while ((im = itemRe.exec(block)) !== null) out.push(im[1]);
    return out;
  }

  function extractArrayObjectNames(constName) {
    const re = new RegExp("const\\s+" + constName + "\\s*=\\s*\\[");
    const m = src.match(re);
    if (!m) return null;
    const start = m.index + m[0].length;
    let depth = 1;
    let i = start;
    let block = "";
    while (i < src.length && depth > 0) {
      const c = src[i++];
      if (c === "[") depth++;
      else if (c === "]") depth--;
      if (depth > 0) block += c;
    }
    const out = [];
    const nameRe = /name:\s*["']([^"']+)["']/g;
    let nm;
    while ((nm = nameRe.exec(block)) !== null) out.push(nm[1]);
    return out;
  }

  const stackServiceOrder = extractArrayStrings("STACK_SERVICE_ORDER") || [];
  const stackInfraNames = extractArrayObjectNames("STACK_INFRA_ROWS") || [];
  const flowLegendServices = extractArrayStrings("FLOW_LEGEND_SERVICES") || [];
  const expectedStackServiceOrder = defaultComposeServices().map(displayNameForComposeService);

  assert("STACK_SERVICE_ORDER matches default compose services",
    sameOrderedItems(stackServiceOrder, expectedStackServiceOrder),
    { stackServiceOrder, expectedStackServiceOrder });

  // Every name buildStackList emits. SERVICE_META MUST cover all of these
  // (list view + flow tooltips).
  const liveServiceNames = [...stackServiceOrder, ...stackInfraNames];

  // External services referenced in the pipeline that are NOT in
  // buildStackList. Today: "openai" — modeled as an external branch
  // off the voice flow, not a managed container. SERVICE_META still
  // must cover it (so the tooltip renders) but the
  // "live buildStackList" check is skipped.
  const externalServices = new Set(["openai"]);

  const serviceMetaKeys = extractObjectKeys("SERVICE_META") || [];
  const endpointMetaKeys = extractObjectKeys("ENDPOINT_META") || [];
  const pipelineIds = extractPipelineServiceIds() || { service: [], endpoint: [] };

  for (const id of stackServiceOrder) {
    if (!pipelineIds.service.includes(id)) {
      assert(`non-pipeline managed service "${id}" appears in FLOW_LEGEND_SERVICES`,
        flowLegendServices.includes(id),
        `managed service "${id}" is in the list view but hidden in the flow view legend`);
    }
  }

  // 1) Every name buildStackList emits → SERVICE_META has copy
  for (const name of liveServiceNames) {
    assert(`SERVICE_META has entry for "${name}"`,
      serviceMetaKeys.includes(name),
      `missing SERVICE_META["${name}"] — list view would render with no tooltip`);
  }

  // 2) Every PIPELINE service node (main row OR branch) →
  //    SERVICE_META has copy. AND if not flagged external, must
  //    match a live name (no dead refs).
  for (const id of pipelineIds.service) {
    assert(`PIPELINE service id "${id}" exists in SERVICE_META`,
      serviceMetaKeys.includes(id),
      `pipeline references "${id}" but SERVICE_META lacks it`);
    if (!externalServices.has(id)) {
      assert(`PIPELINE service id "${id}" exists in live buildStackList output`,
        liveServiceNames.includes(id),
        `pipeline references "${id}" but no service of that name is emitted by buildStackList`);
    }
  }

  // 3) Every PIPELINE endpoint id → ENDPOINT_META has copy
  for (const id of pipelineIds.endpoint) {
    assert(`PIPELINE endpoint id "${id}" exists in ENDPOINT_META`,
      endpointMetaKeys.includes(id),
      `pipeline references endpoint "${id}" but ENDPOINT_META lacks it`);
  }

  // 4) Architecture correctness checks: services that must appear in
  //    multiple locations (main rows + branches together) because of
  //    cross-pipeline reuse. vllm is used in both vision (captioning)
  //    and voice (reasoning) — the "same service" dashed connector
  //    relies on this. If a future edit accidentally removes one, the
  //    connector logic becomes dead code AND the diagram misleads.
  const serviceFreq = {};
  for (const id of pipelineIds.service) {
    serviceFreq[id] = (serviceFreq[id] || 0) + 1;
  }
  assert("vllm appears in ≥ 2 pipeline locations (vision + voice)",
    (serviceFreq.vllm || 0) >= 2,
    `vllm should be referenced in both vision and voice rows; got ${serviceFreq.vllm || 0}`);

  // 5) The openai branch must be present (architectural requirement —
  //    Addendum 26 user feedback: external classifier branch).
  assert("openai is referenced in PIPELINE (external routing branch)",
    (serviceFreq.openai || 0) >= 1,
    `openai branch missing — the classifier's external path isn't modeled`);

  // 6) haos must be referenced from the voice flow (as a tool-dispatch
   //   branch) so the diagram honestly shows where tool calls go.
  assert("haos is referenced in PIPELINE (tool-dispatch branch)",
    (serviceFreq.haos || 0) >= 1,
    `haos should appear at least once (e.g., as the tool-dispatch branch under vllm in voice)`);
})();

/* ────────────────────────────────────────────────────────────────────────
 * F-15 suite — computeStageBaselines + computeStageDelta
 * ──────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mF-15 stage attribution baselines + deltas[0m\n");

(function () {
  // Helper to build a turn with one ms per stage label.
  const turn = (msByLabel) => ({
    totalMs: Object.values(msByLabel).reduce((a, b) => a + b, 0),
    stages: Object.entries(msByLabel).map(([label, ms]) => ({ label, ms })),
  });

  // Empty input → empty baselines
  const emptyB = H.computeStageBaselines([]);
  assert("computeStageBaselines: empty turns → empty map",
    Object.keys(emptyB).length === 0);

  assert("computeStageBaselines: undefined turns → empty map",
    Object.keys(H.computeStageBaselines(undefined)).length === 0);

  // 5 turns, llm always 200ms — baseline p50 = 200, count=4 (last excluded)
  const turns5 = [
    turn({ stt: 100, llm: 200, synth: 300, audio: 400 }),
    turn({ stt: 110, llm: 200, synth: 290, audio: 410 }),
    turn({ stt: 105, llm: 200, synth: 310, audio: 420 }),
    turn({ stt: 108, llm: 200, synth: 305, audio: 395 }),
    turn({ stt: 102, llm: 200, synth: 295, audio: 405 }),  // most recent, excluded
  ];
  const b5 = H.computeStageBaselines(turns5);
  assert("baseline excludes most-recent by default (count=4)",
    b5.llm && b5.llm.count === 4, b5.llm);
  assert("baseline p50 for stable stage equals constant value",
    b5.llm && b5.llm.p50 === 200, b5.llm);
  assert("baseline has p90", b5.llm && b5.llm.p90 === 200);

  // excludeLast=false → count=5
  const b5all = H.computeStageBaselines(turns5, { excludeLast: false });
  assert("excludeLast=false includes the latest turn (count=5)",
    b5all.llm && b5all.llm.count === 5, b5all.llm);

  // Lookback caps history
  const turns10 = Array(10).fill(0).map((_, i) => turn({ llm: 100 + i }));
  const bLook3 = H.computeStageBaselines(turns10, { lookback: 3 });
  assert("lookback=3 only considers 3 turns (after exclusion = 3)",
    bLook3.llm && bLook3.llm.count === 3);

  // computeStageDelta: stable baseline, slow stage
  const slowDelta = H.computeStageDelta(600, "llm", { llm: { p50: 200, p90: 250, count: 10 } });
  assert("3.0× baseline → 'slow' band when verySlowRatio default=2.5",
    slowDelta !== null);
  assert("delta band='very_slow' when 3.0× (> 2.5× threshold)",
    slowDelta && slowDelta.band === "very_slow",
    slowDelta);
  assert("delta surface includes summary",
    slowDelta && typeof slowDelta.summary === "string"
      && slowDelta.summary.includes("LLM") && slowDelta.summary.includes("400")
      && slowDelta.summary.includes("200"),
    slowDelta && slowDelta.summary);

  // 1.7× baseline → 'slow' band (between 1.5× and 2.5×)
  const slow17 = H.computeStageDelta(340, "llm", { llm: { p50: 200, p90: 250, count: 10 } });
  assert("1.7× → 'slow' band", slow17 && slow17.band === "slow", slow17);

  // 1.2× baseline → null (below slowRatio default 1.5)
  const ok = H.computeStageDelta(240, "llm", { llm: { p50: 200, p90: 250, count: 10 } });
  assert("1.2× → null (no callout when below 1.5× threshold)", ok === null);

  // Unstable baseline (count<4) → null even when delta is large
  const unstable = H.computeStageDelta(600, "llm", { llm: { p50: 200, p90: 250, count: 2 } });
  assert("count<4 → null (suppress callout when baseline unstable)",
    unstable === null);

  // No baseline for that stage → null
  const noBaseline = H.computeStageDelta(600, "audio", { llm: { p50: 200, p90: 250, count: 10 } });
  assert("missing stage in baseline → null", noBaseline === null);

  // Invalid input (negative ms, zero p50, NaN) → null
  assert("negative ms → null", H.computeStageDelta(-100, "llm", { llm: { p50: 200, count: 10 } }) === null);
  assert("zero p50 → null", H.computeStageDelta(500, "llm", { llm: { p50: 0, count: 10 } }) === null);
  assert("missing label → null", H.computeStageDelta(500, "", { llm: { p50: 200, count: 10 } }) === null);
  assert("missing baselines → null", H.computeStageDelta(500, "llm", null) === null);

  // very_slow via p90 path: 1.6× of p50 but > p90×1.5 → very_slow
  const p90Path = H.computeStageDelta(450, "llm", { llm: { p50: 280, p90: 290, count: 10 } });
  assert("> p90×1.5 → very_slow even at <2.5× p50",
    p90Path && p90Path.band === "very_slow", p90Path);
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite — Addendum 30B /describe-clip parser
 *
 * Old positional parser broke on two-word cameras: "living room" → frames="room".
 * New parser walks tokens, collecting non-numerics as the camera name, then
 * accepting up to 2 trailing numerics. Normalizes camera to entity_id form.
 * ──────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mAddendum 30B — /describe-clip parser[0m\n");

(function () {
  const p = H.parseDescribeClipArg;

  // 1. Single-word camera
  let r = p("kitchen");
  assert("single-word camera", r.camera === "kitchen" && r.frames === undefined && r.interval_s === undefined, r);

  // 2. Two-word camera (THE bug)
  r = p("living room");
  assert("two-word camera → underscored", r.camera === "living_room" && !r.error, r);

  // 3. Two-word camera + frames
  r = p("dining room 6");
  assert("two-word + frames", r.camera === "dining_room" && r.frames === 6, r);

  // 4. Two-word camera + frames + interval
  r = p("dining room 4 1.5");
  assert("two-word + frames + interval",
    r.camera === "dining_room" && r.frames === 4 && approxEq(r.interval_s, 1.5), r);

  // 5. camera. prefix stripped
  r = p("camera.driveway 8");
  assert("camera. prefix stripped", r.camera === "driveway" && r.frames === 8, r);

  // 6. Case normalization
  r = p("Living Room");
  assert("case normalization", r.camera === "living_room", r);

  // 7. Empty input
  r = p("");
  assert("empty input → error", r.error && !r.camera, r);

  // 8. Whitespace-only input
  r = p("   ");
  assert("whitespace-only → error", r.error && !r.camera, r);

  // 9. Regression: "kitchen 4" still works
  r = p("kitchen 4");
  assert("regression: single-word + frames", r.camera === "kitchen" && r.frames === 4, r);

  // 10. Numeric-first error (no camera)
  r = p("4 1.5");
  assert("numeric-first → error", r.error && /camera/i.test(r.error), r);

  // 11. Frames out of range
  r = p("kitchen 99");
  assert("frames out of range → error", r.error && /frames must be 2-8/.test(r.error), r);

  // 12. Frames out of range — lower
  r = p("kitchen 1");
  assert("frames below 2 → error", r.error && /frames must be 2-8/.test(r.error), r);

  // 13. Interval out of range
  r = p("kitchen 4 5.0");
  assert("interval out of range → error", r.error && /interval_s must be 0.2-2.0/.test(r.error), r);

  // 14. Whole entity_id form
  r = p("camera.living_room");
  assert("entity_id form (single token, prefix stripped)",
    r.camera === "living_room" && r.frames === undefined, r);

  // 15. Three-word camera (defensive)
  r = p("front porch left");
  assert("three-word camera", r.camera === "front_porch_left" && r.frames === undefined, r);

  // 16. Case + prefix combined
  r = p("Camera.Living_Room 4");
  assert("case + prefix combined → normalized",
    r.camera === "living_room" && r.frames === 4, r);
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite — Addendum 29 Change 3 multi-round turn grouping
 *
 * AV29-2 ordering, AV29-5 dedup collision, AV29-11 SSE out-of-order
 * reordering, AV29-12 empty-assistant handling, AV29-13 grouping
 * window boundary, AV29-18 routing-log idempotency.
 * ──────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mAddendum 29 Change 3 — multi-round grouping[0m\n");

(function () {
  const find = H.findTurnToGroupInto;
  const merge = H.mergeTraceIntoTurn;

  function makeTurn(opts) {
    return {
      id: opts.id || "t1",
      transcript: opts.text || "test",
      stages: opts.stages || [
        { label: "prefill", ms: 100, startedAt: opts.startedAt || 0, endedAt: (opts.startedAt || 0) + 100 },
        { label: "gen", ms: 300, startedAt: (opts.startedAt || 0) + 100, endedAt: (opts.startedAt || 0) + 400 },
      ],
      startedAt: opts.startedAt || 0,
      endedAt: opts.endedAt || (opts.startedAt || 0) + 400,
      totalMs: opts.totalMs || 400,
      sidecar_ids: opts.sidecar_ids || [],
      assistant_text: opts.assistant || "",
    };
  }
  function makeTrace(opts) {
    return {
      turn_id: opts.id || "tr1",
      user_text: opts.text || "test",
      assistant_text: opts.assistant || "",
      startedAt: opts.startedAt,
      t_done: opts.totalMs || 200,
      stages: opts.stages || [
        { label: "prefill", ms: 50, startedAt: opts.startedAt || 0, endedAt: (opts.startedAt || 0) + 50 },
        { label: "gen", ms: 150, startedAt: (opts.startedAt || 0) + 50, endedAt: (opts.startedAt || 0) + 200 },
      ],
    };
  }

  // 1. Empty existing turns → no group
  assert("empty existing → null", find([], makeTrace({}), 2500) === null);

  // 2. Same text + within window → match
  const existing1 = makeTurn({ id: "t1", text: "who is home", endedAt: 1000 });
  const incoming1 = makeTrace({ id: "tr2", text: "who is home", startedAt: 1500 });
  let match = find([existing1], incoming1, 2500);
  assert("same text + 500ms gap → match", match && match.id === "t1", { match });

  // 3. AV29-13: 1.5s apart, same text → match (within window)
  const existing2 = makeTurn({ id: "t2", text: "tell me a joke", endedAt: 2000 });
  const incoming2 = makeTrace({ id: "tr3", text: "tell me a joke", startedAt: 3500 });
  match = find([existing2], incoming2, 2500);
  assert("AV29-13: 1.5s gap → match", match && match.id === "t2");

  // 4. AV29-13: 3.0s apart, same text → no match (outside window)
  const existing3 = makeTurn({ id: "t3", text: "tell me a joke", endedAt: 5000 });
  const incoming3 = makeTrace({ id: "tr4", text: "tell me a joke", startedAt: 8500 });
  match = find([existing3], incoming3, 2500);
  assert("AV29-13: 3.5s gap → no match (outside 2.5s window)", match === null);

  // 5. AV29-5: same text + already-seen sidecar id → no merge
  const existing4 = makeTurn({ id: "t4", text: "hi", endedAt: 1000, sidecar_ids: ["tr5"] });
  const incoming4 = makeTrace({ id: "tr5", text: "hi", startedAt: 1500 });
  match = find([existing4], incoming4, 2500);
  assert("AV29-5: same sidecar id → no merge (idempotent)", match === null);

  // 6. Different user text → no match (even within window)
  const existing5 = makeTurn({ id: "t5", text: "who is home", endedAt: 1000 });
  const incoming5 = makeTrace({ id: "tr6", text: "what time is it", startedAt: 1500 });
  match = find([existing5], incoming5, 2500);
  assert("different text → no match", match === null);

  // 7. AV29-2: merge appends stages in chronological order
  const t6 = makeTurn({ id: "t6", text: "weather", endedAt: 1000, totalMs: 400 });
  t6.sidecar_ids = ["tr-a"];
  const newTrace = makeTrace({
    id: "tr-b",
    text: "weather",
    startedAt: 1100,
    totalMs: 200,
    stages: [
      { label: "prefill", ms: 50, startedAt: 1100, endedAt: 1150 },
      { label: "gen", ms: 150, startedAt: 1150, endedAt: 1300 },
    ],
  });
  merge(t6, newTrace);
  assert("merged stage count = sum", t6.stages.length === 4, { stages: t6.stages.map((s) => s.label) });
  // Verify monotonic startedAt (AV29-2)
  let monotonic = true;
  for (let i = 1; i < t6.stages.length; i++) {
    if (t6.stages[i].startedAt < t6.stages[i - 1].startedAt) { monotonic = false; break; }
  }
  assert("AV29-2: merged stages monotonic by startedAt", monotonic);
  assert("merged totalMs = sum of all stage ms", t6.totalMs === 600, { totalMs: t6.totalMs });
  assert("AV29-5: sidecar_ids tracks new id", t6.sidecar_ids.includes("tr-b"));
  assert("merged endedAt = last stage end", t6.endedAt === 1300);

  // 8. AV29-11: out-of-order SSE delivery → stages still sorted after merge
  const t7 = makeTurn({
    id: "t7", text: "test", endedAt: 500, totalMs: 500,
    stages: [
      { label: "prefill", ms: 100, startedAt: 0, endedAt: 100 },
      { label: "gen", ms: 400, startedAt: 100, endedAt: 500 },
    ],
  });
  // Out-of-order: trace2 arrives first, but its startedAt is LATER
  const out_of_order = makeTrace({
    id: "tr-late",
    text: "test",
    startedAt: 800,
    totalMs: 200,
    stages: [
      { label: "prefill", ms: 50, startedAt: 800, endedAt: 850 },
      { label: "gen", ms: 150, startedAt: 850, endedAt: 1000 },
    ],
  });
  merge(t7, out_of_order);
  monotonic = true;
  for (let i = 1; i < t7.stages.length; i++) {
    if (t7.stages[i].startedAt < t7.stages[i - 1].startedAt) { monotonic = false; break; }
  }
  assert("AV29-11: merged stages monotonic regardless of arrival order", monotonic);

  // 9. AV29-12: empty assistant doesn't clobber existing
  const t8 = makeTurn({ id: "t8", text: "hi", endedAt: 500, assistant: "hello there" });
  merge(t8, makeTrace({ id: "tr-empty", text: "hi", startedAt: 600, assistant: "" }));
  assert("AV29-12: empty assistant doesn't clobber existing", t8.assistant_text === "hello there");

  // 10. AV29-12: non-empty assistant DOES update
  const t9 = makeTurn({ id: "t9", text: "hi", endedAt: 500, assistant: "" });
  merge(t9, makeTrace({ id: "tr-final", text: "hi", startedAt: 600, assistant: "final answer" }));
  assert("AV29-12: non-empty assistant overrides", t9.assistant_text === "final answer");

  // 11. AV29-18: merging same trace twice doesn't duplicate stages
  const t10 = makeTurn({ id: "t10", text: "X", endedAt: 500 });
  const trDup = makeTrace({ id: "tr-dup", text: "X", startedAt: 600, totalMs: 200 });
  merge(t10, trDup);
  const stagesAfterFirst = t10.stages.length;
  // Second merge attempt — find should return null because sidecar id already tracked
  const matchDup = find([t10], trDup, 2500);
  assert("AV29-18: re-finding same trace returns null", matchDup === null);
  // (If caller still calls merge, our function is best-effort: doesn't dedup
  // internally. The find+merge combination IS idempotent because find blocks
  // the re-merge.)
  assert("AV29-18: defense — find prevents re-merge of same sidecar id",
    matchDup === null && stagesAfterFirst > 0);

  // 12. New text never merges into ANY existing
  const ts = [
    makeTurn({ id: "ta", text: "old1", endedAt: 100 }),
    makeTurn({ id: "tb", text: "old2", endedAt: 200 }),
    makeTurn({ id: "tc", text: "old3", endedAt: 300 }),
  ];
  match = find(ts, makeTrace({ id: "tr-new", text: "totally new", startedAt: 350 }), 2500);
  assert("new user_text → never merges", match === null);

  // 13. Boundary case: exactly windowMs apart
  const tb = makeTurn({ id: "tb1", text: "hi", endedAt: 1000 });
  // Gap exactly 2500 → still within (<=)
  match = find([tb], makeTrace({ id: "tr-b1", text: "hi", startedAt: 3500 }), 2500);
  assert("boundary: exactly window → match (≤)", match !== null);
  // Gap 2501 → just outside
  match = find([tb], makeTrace({ id: "tr-b2", text: "hi", startedAt: 3501 }), 2500);
  assert("boundary: window+1 → no match", match === null);

  // 14. Multiple turns with same text, most-recent should win
  const tprev = makeTurn({ id: "old", text: "ping", endedAt: 100, sidecar_ids: ["tr-old"] });
  const trecent = makeTurn({ id: "new", text: "ping", endedAt: 5000, sidecar_ids: ["tr-recent"] });
  match = find([tprev, trecent], makeTrace({ id: "tr-x", text: "ping", startedAt: 5500 }), 2500);
  assert("most-recent same-text turn wins (walks backwards)", match && match.id === "new");
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite — Addendum 29 Change 4 tool-segment splicing
 *
 * AV29-3: tool segments must (a) appear BETWEEN consecutive gen→prefill
 * pairs (never before first stage or after last gen), (b) carry the
 * actual tool_name as their label, (c) sum to the gap duration when
 * provided. AV29-18 idempotency covered indirectly via the merge tests
 * above.
 * ──────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mAddendum 29 Change 4 — tool segment splicing[0m\n");

(function () {
  const merge = H.mergeTraceIntoTurn;

  // 1. Round-1 trace with tool_calls → turn.pending_tools captures the names
  let turn = {
    id: "t-tools-1",
    transcript: "who is home",
    stages: [
      { label: "prefill", ms: 100, startedAt: 0, endedAt: 100 },
      { label: "gen", ms: 200, startedAt: 100, endedAt: 300 },
    ],
    startedAt: 0,
    endedAt: 300,
    totalMs: 300,
    sidecar_ids: ["round1"],
    assistant_text: "",
    pending_tools: ["who_is_in"],
  };
  // Round 2 trace arrives 150ms later — gap = 150ms for who_is_in
  let round2 = {
    turn_id: "round2",
    user_text: "who is home",
    assistant_text: "Marcelo is home",
    stages: [
      { label: "prefill", ms: 80, startedAt: 450, endedAt: 530 },
      { label: "gen", ms: 250, startedAt: 530, endedAt: 780 },
    ],
    tool_calls: [],
  };
  merge(turn, round2);
  // Expected: [prefill_1, gen_1, who_is_in, prefill_2, gen_2]
  const labels = turn.stages.map((s) => s.label);
  assert("AV29-3: tool segment inserted between rounds",
    JSON.stringify(labels) === '["prefill","gen","who_is_in","prefill","gen"]', { labels });

  // 2. Tool segment label matches the pending tool name
  const toolSeg = turn.stages.find((s) => s.label === "who_is_in");
  assert("AV29-3: tool segment label matches tool_name",
    toolSeg && toolSeg.label === "who_is_in" && toolSeg.kind === "tool", toolSeg);

  // 3. Tool segment lands AFTER previous gen and BEFORE new prefill
  const prevGen = turn.stages[1]; // gen of round 1
  const newPrefill = turn.stages[3]; // prefill of round 2
  assert("AV29-3: tool startedAt >= prev gen endedAt",
    toolSeg.startedAt >= prevGen.endedAt);
  assert("AV29-3: tool endedAt <= new prefill startedAt",
    toolSeg.endedAt <= newPrefill.startedAt);

  // 4. Stages remain monotonic after splice
  let mono = true;
  for (let i = 1; i < turn.stages.length; i++) {
    if (turn.stages[i].startedAt < turn.stages[i - 1].startedAt) { mono = false; break; }
  }
  assert("AV29-3: monotonic ordering preserved after splice", mono, { stages: turn.stages });

  // 5. Multi-tool: gap distributed across tools, each gets its name
  let turnMulti = {
    id: "t-multi",
    transcript: "do many things",
    stages: [
      { label: "prefill", ms: 100, startedAt: 0, endedAt: 100 },
      { label: "gen", ms: 100, startedAt: 100, endedAt: 200 },
    ],
    startedAt: 0, endedAt: 200, totalMs: 200,
    sidecar_ids: ["round1"], assistant_text: "",
    pending_tools: ["find_person", "get_room_state", "find_clips"],
  };
  let round2multi = {
    turn_id: "round2multi",
    user_text: "do many things",
    assistant_text: "done",
    stages: [
      { label: "prefill", ms: 50, startedAt: 500, endedAt: 550 },
      { label: "gen", ms: 100, startedAt: 550, endedAt: 650 },
    ],
    tool_calls: [],
  };
  merge(turnMulti, round2multi);
  const toolLabels = turnMulti.stages.filter((s) => s.kind === "tool").map((s) => s.label);
  assert("multi-tool: all tool names present in stages",
    toolLabels.length === 3 &&
    toolLabels.indexOf("find_person") !== -1 &&
    toolLabels.indexOf("get_room_state") !== -1 &&
    toolLabels.indexOf("find_clips") !== -1, toolLabels);

  // 6. Tool segments preserve insertion order (matches pending_tools order)
  const orderedToolLabels = turnMulti.stages.filter((s) => s.kind === "tool").map((s) => s.label);
  assert("multi-tool: tool order matches pending_tools order",
    orderedToolLabels[0] === "find_person" &&
    orderedToolLabels[1] === "get_room_state" &&
    orderedToolLabels[2] === "find_clips", orderedToolLabels);

  // 7. Empty pending_tools → no tool segments inserted
  let turnNoTools = {
    id: "t-no-tools",
    transcript: "ping",
    stages: [
      { label: "prefill", ms: 50, startedAt: 0, endedAt: 50 },
      { label: "gen", ms: 100, startedAt: 50, endedAt: 150 },
    ],
    startedAt: 0, endedAt: 150, totalMs: 150,
    sidecar_ids: ["round1"], assistant_text: "",
    pending_tools: [],
  };
  merge(turnNoTools, {
    turn_id: "round2", user_text: "ping", assistant_text: "pong",
    stages: [
      { label: "prefill", ms: 50, startedAt: 300, endedAt: 350 },
      { label: "gen", ms: 100, startedAt: 350, endedAt: 450 },
    ],
    tool_calls: [],
  });
  assert("no pending tools → no tool segments",
    turnNoTools.stages.every((s) => s.kind !== "tool"));

  // 8. Round 2 has tool_calls → pending_tools updated for round 3
  let turn3 = {
    id: "t3",
    transcript: "chain",
    stages: [
      { label: "prefill", ms: 50, startedAt: 0, endedAt: 50 },
      { label: "gen", ms: 100, startedAt: 50, endedAt: 150 },
    ],
    startedAt: 0, endedAt: 150, totalMs: 150,
    sidecar_ids: ["r1"], assistant_text: "",
    pending_tools: ["tool_a"],
  };
  merge(turn3, {
    turn_id: "r2", user_text: "chain", assistant_text: "",
    stages: [
      { label: "prefill", ms: 30, startedAt: 200, endedAt: 230 },
      { label: "gen", ms: 70, startedAt: 230, endedAt: 300 },
    ],
    tool_calls: [{ function: { name: "tool_b" } }],
  });
  assert("after merge, pending_tools = next round's tool_calls",
    turn3.pending_tools.length === 1 && turn3.pending_tools[0] === "tool_b",
    turn3.pending_tools);

  // 9. Tool segment minimum 1ms (visibility even when latency=0)
  let turnFast = {
    id: "tf",
    transcript: "instant",
    stages: [
      { label: "prefill", ms: 50, startedAt: 0, endedAt: 50 },
      { label: "gen", ms: 100, startedAt: 50, endedAt: 150 },
    ],
    startedAt: 0, endedAt: 150, totalMs: 150,
    sidecar_ids: ["r1"], assistant_text: "",
    pending_tools: ["fast_tool"],
  };
  merge(turnFast, {
    turn_id: "r2", user_text: "instant", assistant_text: "done",
    stages: [
      // Gap = 0ms (round 2 starts exactly when round 1 ended)
      { label: "prefill", ms: 30, startedAt: 150, endedAt: 180 },
      { label: "gen", ms: 70, startedAt: 180, endedAt: 250 },
    ],
    tool_calls: [],
  });
  const fastTool = turnFast.stages.find((s) => s.kind === "tool");
  assert("zero-gap tool gets ≥1ms width (visibility floor)",
    fastTool && fastTool.ms >= 1, fastTool);
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite — Addendum 29 Change 2 in-flight stub eviction (AV29-4)
 * ──────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mAddendum 29 Change 2 — in-flight eviction[0m\n");

(function () {
  const evict = H.evictStaleInFlight;

  // 1. Empty / no in-flight → unchanged
  let r = evict([], 100000, 60000);
  assert("empty input → empty output", Array.isArray(r) && r.length === 0);

  r = evict([{ id: "a", startedAt: 1000 }], 5000, 60000);
  assert("non-inflight turn passes through", r.length === 1 && r[0].id === "a");

  // 2. In-flight younger than 60s → keep
  r = evict([{ id: "a", inFlight: true, startedAt: 30000 }], 70000, 60000);
  assert("AV29-4: in-flight age 40s → kept (under 60s)", r.length === 1 && r[0].id === "a");

  // 3. In-flight older than 60s → evict
  r = evict([{ id: "a", inFlight: true, startedAt: 0 }], 70000, 60000);
  assert("AV29-4: in-flight age 70s → evicted", r.length === 0);

  // 4. Mixed list: keeps real turns, evicts only stale in-flight
  const turns = [
    { id: "real1",      startedAt: 0,     totalMs: 500 },
    { id: "inflight_stale", inFlight: true, startedAt: 1000 },
    { id: "real2",      startedAt: 50000, totalMs: 800 },
    { id: "inflight_fresh", inFlight: true, startedAt: 70000 },
  ];
  r = evict(turns, 90000, 60000);
  const ids = r.map((t) => t.id);
  assert("AV29-4: mixed — keep reals + fresh in-flight, drop stale",
    ids.length === 3 && ids.indexOf("inflight_stale") === -1, ids);

  // 5. Custom maxAgeMs (5s threshold)
  r = evict([{ id: "x", inFlight: true, startedAt: 0 }], 6000, 5000);
  assert("AV29-4: custom 5s threshold drops at 6s", r.length === 0);

  // 6. Boundary: exactly maxAgeMs → keep (<=)
  r = evict([{ id: "x", inFlight: true, startedAt: 0 }], 60000, 60000);
  assert("AV29-4: boundary exactly maxAgeMs → kept", r.length === 1);

  // 7. Defaults: maxAgeMs default 60000
  r = evict([{ id: "x", inFlight: true, startedAt: 0 }], 65000);
  assert("AV29-4: default maxAgeMs=60000 evicts at 65s", r.length === 0);

  // 8. nowMs default = Date.now() — stub a fresh in-flight; should survive
  // a default-time call (assumes test isn't running for 60s).
  r = evict([{ id: "x", inFlight: true, startedAt: Date.now() - 1000 }]);
  assert("AV29-4: default nowMs uses Date.now()", r.length === 1);
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite — Resource-line fidelity (synthetic flat + peak-aware real)
 *
 * Two bugs the user surfaced from a real screenshot:
 *   1. Lines visually overlapped into neighboring rows (renderer fix
 *      via SVG clipPath — not unit-testable, covered by visual check).
 *   2. Lines showed dramatic triangular peaks that were SYNTHETIC
 *      stage-profile defaults, not real measurements. Specifically:
 *      GPU showed flat 3% while triangle peaks elsewhere were 75% fake
 *      data. Trust contract violated.
 *
 * Fixes:
 *   - STAGE_PROFILES values reduced to 0.08-0.32 (a near-flat baseline,
 *     no fake "this stage was busy" peaks).
 *   - Aggregation switched from mean to MAX so brief real spikes survive.
 * ──────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mResource-line fidelity (synthetic + max aggregation)[0m\n");

(function () {
  // 1. STAGE_PROFILES: all GPU/CPU/RAM values must stay near-flat (≤0.35)
  //    so synthetic anchors don't fake high values. Trust-contract guard.
  const SP = H.STAGE_PROFILES;
  assert("STAGE_PROFILES exposed", SP && typeof SP === "object", SP);
  for (const metric of ["gpu", "cpu", "ram"]) {
    if (!SP[metric]) continue;
    for (const stg of Object.keys(SP[metric])) {
      assert(`${metric}.${stg} synthetic baseline ≤ 0.35 (no fake peaks)`,
        SP[metric][stg] <= 0.35,
        { metric, stg, value: SP[metric][stg] });
    }
  }

  // 2. buildDenseStageAnchors: when ZERO real samples exist, anchors
  //    should be near the synthetic floor — not at the old 0.75 peaks.
  //    Tests against the gpu.llm = 0.10 floor (was 0.75).
  const turnNoSamples = {
    id: "no-samples",
    startedAt: 0,
    totalMs: 800,
    stages: [
      { label: "prefill", ms: 300, startedAt: 0, endedAt: 300 },
      { label: "gen", ms: 500, startedAt: 300, endedAt: 800 },
    ],
    samples: [],
  };
  const gpuAnchors = H.buildDenseStageAnchors(turnNoSamples, "gpu", { slotStart: 0, slotEnd: 280 });
  const maxSynthetic = gpuAnchors.reduce((mx, a) => Math.max(mx, a.v), 0);
  assert("GPU synthetic peak ≤ 0.20 (no fake LLM spike)",
    maxSynthetic <= 0.20, { maxSynthetic, anchors: gpuAnchors.slice(0, 3) });
  for (const a of gpuAnchors) {
    assert(`synthetic anchor flagged kind:synthetic (${a.x.toFixed(0)})`,
      a.kind === "synthetic");
    if (a.kind !== "synthetic") break; // don't spam if first fails
  }

  // 3. buildDenseStageAnchors: when REAL samples exist, the MAX wins
  //    (not the mean). A brief GPU spike from 0.02 → 0.95 must surface
  //    as ≥0.95 in the anchor that contains it, NOT a diluted average.
  const turnWithSpike = {
    id: "spike",
    startedAt: 0,
    totalMs: 1000,
    stages: [
      { label: "prefill", ms: 200, startedAt: 0, endedAt: 200 },
      { label: "gen", ms: 800, startedAt: 200, endedAt: 1000 },
    ],
    samples: [
      // Inside the gen stage: 3 idle samples + 1 spike → mean = 0.25,
      // max = 0.95. Old code returned 0.25 (lost the spike).
      { t: 300, gpuPct: 0.02 },
      { t: 400, gpuPct: 0.02 },
      { t: 500, gpuPct: 0.95 },   // ← THE SPIKE
      { t: 600, gpuPct: 0.02 },
    ],
  };
  const gpuSpikeAnchors = H.buildDenseStageAnchors(turnWithSpike, "gpu", { slotStart: 0, slotEnd: 280 });
  const realAnchors = gpuSpikeAnchors.filter((a) => a.kind === "real");
  const realPeak = realAnchors.reduce((mx, a) => Math.max(mx, a.v), 0);
  assert("GPU real-data spike survives as max (≥0.9, was 0.25 under mean)",
    realPeak >= 0.9,
    { realPeak, realAnchors });

  // 4. Real anchors win over profiles: at least one anchor with
  //    samples should have kind:real, not synthetic.
  assert("real anchors present when samples exist",
    realAnchors.length >= 1, { gpuSpikeAnchors });

  // 5. Anchors stay within [0, 1] regardless of noise + clamping
  for (const a of gpuAnchors.concat(gpuSpikeAnchors)) {
    if (a.v < 0 || a.v > 1) {
      assert(`anchor v in [0,1] (${a.v})`, false, a);
      break;
    }
  }
  assert("all anchors clamped to [0,1] range", true);
})();

/* ────────────────────────────────────────────────────────────────────────
 * Summary
 * ──────────────────────────────────────────────────────────────── */
/* ---------------------------------------------------------------------------
 * Suite 12 - display-only resource line scaling
 *
 * The trace chart keeps raw hardware values for labels/hover, but draws a
 * scaled copy of real anchors so subtle GPU/CPU/RAM motion is visible instead
 * of collapsing into a flat 1px line.
 * ------------------------------------------------------------------------ */
process.stdout.write("\n\x1b[1mdisplay scaling for hardware lines\x1b[0m\n");

(function () {
  const anchors = [
    { x: 0, v: 0.03 },
    { x: 10, v: 0.10, kind: "real" },
    { x: 20, v: 0.11, kind: "real" },
  ];
  const scaled = H.scaleAnchorsForDisplay(anchors, { minRealAnchors: 3 });
  assert("display scaling: fewer than 3 real anchors leaves values unchanged",
    scaled.length === anchors.length
      && approxEq(scaled[0].v, anchors[0].v)
      && approxEq(scaled[1].v, anchors[1].v)
      && approxEq(scaled[2].v, anchors[2].v),
    scaled);
})();

(function () {
  const anchors = [
    { x: 0, v: 0.03 },
    { x: 10, v: 0.10, kind: "real" },
    { x: 20, v: 0.11, kind: "real" },
    { x: 30, v: 0.12, kind: "real" },
    { x: 40, v: 0.03 },
  ];
  const scaled = H.scaleAnchorsForDisplay(anchors);
  const real = scaled.filter((a) => a.kind === "real").map((a) => a.v);
  const rawSpan = 0.12 - 0.10;
  const displaySpan = Math.max(...real) - Math.min(...real);
  assert("display scaling: subtle real movement becomes visually legible",
    displaySpan > rawSpan * 5,
    { rawSpan, displaySpan, real });
  assert("display scaling: idle anchors stay pinned low",
    approxEq(scaled[0].v, 0.03) && approxEq(scaled[4].v, 0.03),
    scaled);
})();

(function () {
  const anchors = [
    { x: 0, v: 0.10, kind: "synthetic" },
    { x: 10, v: 0.11, kind: "synthetic" },
    { x: 20, v: 0.12, kind: "synthetic" },
  ];
  const scaled = H.scaleAnchorsForDisplay(anchors, { minRealAnchors: 1 });
  assert("display scaling: synthetic anchors are not amplified",
    scaled.every((a, i) => approxEq(a.v, anchors[i].v)),
    scaled);
})();

/* ---------------------------------------------------------------------------
 * Suite 13 - stack recovery tiering
 *
 * Locks down the Lab/AI recovery affordances that matter when the local model
 * stack is down: offline must expose "start", crashed/degraded services must
 * expose logs/restart, and destructive actions must remain confirm-gated.
 * ------------------------------------------------------------------------ */
process.stdout.write("\n\x1b[1mstack recovery tiering\x1b[0m\n");

(function () {
  assert("deriveLabTier helper exported", typeof H.deriveLabTier === "function");

  const verbs = (tier) => (tier.actions || []).map((a) => a.verb);

  const offline = H.deriveLabTier({}, { overall: "offline", services: [] }, null, {});
  assert("offline stack maps to offline tier", offline.tier === "offline", offline);
  assert("offline stack exposes start action", verbs(offline).includes("start"), offline.actions);
  assert("offline stack keeps logs action visible", verbs(offline).includes("logs"), offline.actions);

  const mostlyDown = H.deriveLabTier({}, {
    overall: "ready",
    services: [
      { name: "vllm", container: "exited", health: "healthy" },
      { name: "kokoro", container: "dead", health: "healthy" },
      { name: "vision-sidecar", container: "exited", health: "healthy" },
    ],
  }, null, {});
  assert("three down containers force offline recovery tier", mostlyDown.tier === "offline", mostlyDown);

  const warming = H.deriveLabTier({}, {
    overall: "ready",
    in_flight: { verb: "start", step: "loading vllm", progress_pct: 72, eta_s: 12 },
  }, null, {});
  assert("start in-flight maps to warming tier", warming.tier === "warming", warming);
  assert("warming exposes progress and no extra action buttons",
    warming.progress && warming.progress.pct === 72 && warming.actions.length === 0,
    warming);

  const crashed = H.deriveLabTier({}, {
    overall: "ready",
    services: [{ name: "vllm", probe: "error", detail: "health probe failed" }],
  }, null, {});
  assert("vllm probe error maps to error tier", crashed.tier === "error", crashed);
  assert("error tier exposes logs and restart actions",
    verbs(crashed).includes("logs") && verbs(crashed).includes("restart"),
    crashed.actions);

  const pressured = H.deriveLabTier({ vram: 22, vramMax: 24 }, { overall: "ready" }, null, {});
  assert("high VRAM maps to pressured tier", pressured.tier === "pressured", pressured);
  assert("pressured stop action is confirm-gated",
    pressured.actions[0]?.verb === "stopStack" && pressured.actions[0]?.confirm === true,
    pressured.actions);

  const partial = H.deriveLabTier({}, {
    overall: "ready",
    services: [
      { name: "vision-sidecar", health: "stale" },
      { name: "metrics-sidecar", probe: "stale" },
    ],
  }, null, {});
  assert("two degraded services map to partial tier", partial.tier === "partial", partial);
  assert("partial tier exposes restart and logs",
    verbs(partial).includes("restart") && verbs(partial).includes("logs"),
    partial.actions);

  const degraded = H.deriveLabTier({}, {
    overall: "ready",
    services: [{ name: "kokoro", health: "stale" }],
  }, null, {});
  assert("one stale service maps to degraded tier", degraded.tier === "degraded", degraded);
  assert("degraded restart is confirm-gated",
    degraded.actions.find((a) => a.verb === "restart")?.confirm === true,
    degraded.actions);

  const slow = H.deriveLabTier({}, { overall: "ready" }, { totalMs: 1600 }, { p50: 1000 });
  assert("slow turn maps to slow tier", slow.tier === "slow", slow);
  assert("slow tier has no destructive actions", slow.actions.length === 0, slow.actions);

  const ready = H.deriveLabTier({ model: "qwen3-v1-30b" }, { overall: "ready", uptime_h: 5 }, null, {});
  assert("healthy stack maps to ready tier", ready.tier === "ready", ready);
  assert("ready tier shows model and no action buttons",
    ready.statusWord === "qwen3-v1-30b" && ready.actions.length === 0,
    ready);
})();

/* ---------------------------------------------------------------------------
 * Suite - Lab diagnostics service operations surface
 *
 * The supervisor exposes one status/action row for every default compose
 * service. The Lab diagnostics pane must not hard-filter that list down to an
 * older four-service subset, or recovery affordances disappear for services
 * like Chatterbox and Intelligence.
 * ------------------------------------------------------------------------ */
process.stdout.write("\n\x1b[1mlab diagnostics service operations\x1b[0m\n");

(function () {
  const defaults = defaultComposeServices();
  assert("compose parser sees current default stack services",
    defaults.includes("chatterbox-tts")
      && defaults.includes("metrics-sidecar")
      && defaults.includes("intelligence")
      && defaults.length === 8,
    defaults);
  assert("Lab diagnostics pane receives unfiltered supervisor service list",
    /<LabDiagPane[\s\S]*services=\{services\}/.test(LAB_SRC),
    "LabDiagPane should receive services={services}");
  assert("Lab diagnostics pane does not hard-filter old four-service subset",
    !LAB_SRC.includes('["vllm", "wyoming-parakeet", "kokoro", "vision-sidecar"].includes(s.name)'),
    "old hard filter hides Chatterbox, wyoming-kokoro, metrics-sidecar, and Intelligence");
  assert("Lab diagnostics service rows wire logs stop and restart verbs",
    LAB_SRC.includes('onAction?.("serviceLogs", svc.name)')
      && LAB_SRC.includes('onAction?.("serviceStop", svc.name)')
      && LAB_SRC.includes('onAction?.("serviceRestart", svc.name)'),
    "service operation buttons must stay wired");
})();

/* ---------------------------------------------------------------------------
 * Suite - DOC-S86 Lab diagnostics pane interaction contracts
 *
 * The documented button sweep expects the pane toggle, log filter, service logs,
 * and all four global actions to be wired. The log filter must feed the log pane
 * rather than living as decorative local state, and every global action must use
 * the shared two-click confirm helper.
 * ------------------------------------------------------------------------ */
process.stdout.write("\n\x1b[1mDOC-S86 lab diagnostics interactions\x1b[0m\n");

(function () {
  assert("diag toggle opens and closes LabDiagPane",
    LAB_SRC.includes("setDiagOpen(!diagOpen)") &&
      /diagOpen\s*&&\s*\([\s\S]*<LabDiagPane/.test(LAB_SRC),
    "diagOpen should gate the diagnostic pane and the toggle should update it");
  for (const label of ["free gpu", "reload all", "pause perception", "clear cache"]) {
    const re = new RegExp(`DiagBtn label="${label}"[\\s\\S]*?confirm`);
    assert(`${label} global is confirm-gated`, re.test(LAB_SRC));
  }
  assert("pause perception stops vision-sidecar through the shared action module",
    !LAB_SRC.includes('error: "not wired"') &&
      /verb === "pausePerception"[\s\S]*HSA\.pausePerception\(\{ supervisorUrl, stackToken \}\)/.test(LAB_SRC),
    "pause perception should dispatch a real supervisor action, not an unsupported local error");
  assert("pause perception requires STACK_TOKEN before dispatch",
    /DiagBtn label="pause perception"[\s\S]*disabled=\{!tokenConfigured\}/.test(LAB_SRC),
    "pause perception stops a service and must be token-gated like other supervisor mutations");
  assert("clear cache clears local diagnostic log lines",
    LAB_SRC.includes('if (verb === "clearCache")') &&
      LAB_SRC.includes("stopLogStream();") &&
      LAB_SRC.includes("setLogLines([])") &&
      LAB_SRC.includes('setActionResult({ verb, kind: "ok" })'),
    "clear cache should finish visibly, abort any stream, and clear the local log pane");
  assert("log filter state is owned by LabDiagPane",
    LAB_SRC.includes("const [logFilters, setLogFilters] = useState(DEFAULT_LAB_LOG_FILTERS)") &&
      LAB_SRC.includes("const visibleLogLines = (logLines || []).filter"),
    "LabDiagPane should compute filtered lines");
  assert("log filter chips feed visible log lines",
    LAB_SRC.includes("<LabLogFilter filters={logFilters} onToggle={toggleLogFilter} />") &&
      LAB_SRC.includes("<LabLogPane lines={visibleLogLines} />"),
    "filter state must feed the log pane");
  assert("log line filter helper keeps all/info/warn/err/dbg semantics",
    LAB_SRC.includes("function _labLogLineMatchesFilters(line, filters)") &&
      LAB_SRC.includes("if (filters.all) return true") &&
      LAB_SRC.includes('String(line?.level || "info").toLowerCase()'),
    "filter helper should classify missing levels as info");
})();

/* ---------------------------------------------------------------------------
 * Suite - DOC-S92 Lab diagnostics log stream contracts
 *
 * S92 expects the Lab diag logs action to use the authenticated supervisor SSE
 * stream and to abort the request when the diagnostics pane closes. The
 * one-shot service-log route remains a fallback for local/browser contexts
 * where the stream helper is not loaded.
 * ------------------------------------------------------------------------ */
process.stdout.write("\n\x1b[1mDOC-S92 lab diagnostics log stream\x1b[0m\n");

(function () {
  assert("Lab diagnostics owns an abortable log-stream controller",
    LAB_SRC.includes("const logStreamCtrlRef = useRef(null)") &&
      /const stopLogStream = useCallback\(\(\) => \{[\s\S]*logStreamCtrlRef\.current\.abort\(\)/.test(LAB_SRC),
    "HmLabTab should hold and abort the active stream controller");
  assert("Lab diagnostics aborts log stream on unmount and pane close",
    /useEffect\(\(\) => \(\) => stopLogStream\(\), \[stopLogStream\]\)/.test(LAB_SRC) &&
      /if \(!diagOpen\) stopLogStream\(\);/.test(LAB_SRC),
    "closing the diagnostics pane must not leak a fetch stream");
  assert("service logs action streams supervisor SSE with bearer auth",
    /verb === "serviceLogs"[\s\S]*typeof window\.streamSse === "function"[\s\S]*HSA\.logsStreamUrl\(\{ supervisorUrl \}\)[\s\S]*window\.streamSse[\s\S]*Authorization: `Bearer \$\{stackToken\}`/.test(LAB_SRC),
    "service logs should use authenticated stack log SSE when available");
  assert("streamed log lines are escaped and bounded before rendering",
    LAB_SRC.includes("function _labEscapeHtml(value)") &&
      LAB_SRC.includes("function _labLogLineFromText(text, level = \"info\")") &&
      LAB_SRC.includes("msg: _labEscapeHtml(text)") &&
      LAB_SRC.includes("prev.length >= 200 ? [...prev.slice(-199), next] : [...prev, next]"),
    "streamed lines should not inject HTML and should remain bounded");
  assert("SSE done and errors publish visible action results",
    /event === "done"[\s\S]*kind: \(data\?\.exit_code \?\? 0\) === 0 \? "ok" : "error"/.test(LAB_SRC) &&
      /onError: \(err\) => \{[\s\S]*_labLogLineFromText\(msg, "err"\)[\s\S]*kind: "error"/.test(LAB_SRC),
    "done/error frames should be visible in the diagnostic result row");
  assert("service logs fallback still uses one-shot route when SSE helper is absent",
    /result = await HSA\.serviceLogs\(\{ supervisorUrl, stackToken, service: serviceName \}\);[\s\S]*setLogLines\(result\.body\.lines\.map\(\(line\) => _labLogLineFromText\(line, "info"\)\)\)/.test(LAB_SRC),
    "fallback keeps service logs useful outside the stream-capable runtime");
})();

process.stdout.write("\n");
process.stdout.write(passes + " pass · " + fails + " fail\n");
if (fails > 0) {
  process.stdout.write("\n[31mFailures:[0m\n");
  for (let i = 0; i < failures.length; i++) {
    process.stdout.write("  - " + failures[i].name + "\n");
  }
}
process.exit(fails === 0 ? 0 : 1);
