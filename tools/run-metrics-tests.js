#!/usr/bin/env node
/* Pure local tests for app/src/home-metrics.jsx metrics tray helper contracts. */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "home-metrics.jsx");
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

function sliceBetween(startNeedle, endNeedle) {
  const start = source.indexOf(startNeedle);
  if (start < 0) throw new Error("missing start marker: " + startNeedle);
  const end = source.indexOf(endNeedle, start);
  if (end < 0) throw new Error("missing end marker after " + startNeedle + ": " + endNeedle);
  return source.slice(start, end);
}

function loadHelpers() {
  const script = [
    sliceBetween("function _hasVariance", "function MetricCard"),
    "Object.assign(window, { _hasVariance, _pipelineStageModel });",
  ].join("\n\n");
  const sandbox = { window: {}, Number, Math, Array };
  vm.runInNewContext(script, sandbox, { filename: "home-metrics.helpers.js" });
  return sandbox.window;
}

const H = loadHelpers();

process.stdout.write("\nmetrics_exports_and_static_contract_test\n");
assert("_hasVariance extracted", typeof H._hasVariance === "function");
assert("_pipelineStageModel extracted", typeof H._pipelineStageModel === "function");
for (const key of [
  "HmSurface",
  "HmCard",
  "HmStatRow",
  "HmMetricCard",
  "HmArc",
  "HmTimelineStrip",
  "HmPipelineBar",
  "HmDepHealthStrip",
  "HmRoomGrid",
  "HmTabs",
  "HmTrayBody",
  "HmStatusLine",
  "HmHealthDot",
  "HmEmptyState",
  "HmDiagModal",
  "HmSparkline",
]) {
  assert(`${key} exported on window`, source.includes(`${key}:`), key);
}
assert("MetricCard auto mode is variance-gated", source.includes('actualViz = _hasVariance(history, max) ? "spark" : "none";'));
assert("DepHealthStrip uses semantic HealthDot tones", source.includes('<HealthDot tone={s.tone || "idle"} size={4} />'));
assert("PipelineBar uses normalized stage model", source.includes("_pipelineStageModel(stages, totalMs)"));
assert("PipelineBar top width scales against barTotalMs", source.includes("(s.dur / barTotalMs) * 100"));
assert("PipelineBar empty state remains explicit", source.includes(">no recent turn</div>"));
assert("RoomGrid displays occupied identity", source.includes('r.identity || "occupied"'));
assert("Sparkline exposes stable SVG viewBox", source.includes("viewBox={`0 0 ${width} ${height}`}"));

process.stdout.write("\nmetrics_variance_helper_test\n");
assert("non-array history has no variance", H._hasVariance(null) === false);
assert("short history has no variance", H._hasVariance([1, 9]) === false);
assert("flat history has no variance", H._hasVariance([7, 7, 7, 7]) === false);
assert("exact threshold is still treated as flat", H._hasVariance([1, 2, 3], 100) === false);
assert("above absolute threshold has variance", H._hasVariance([1, 2, 3.2], 100) === true);
assert("large max uses scaled threshold", H._hasVariance([100, 115, 120], 1000) === false);
assert("large max accepts range above scaled threshold", H._hasVariance([100, 115, 121], 1000) === true);
assert("non-numeric samples are ignored", H._hasVariance([1, "9", 1, 4], 100) === true);
assert("too few numeric samples stay flat", H._hasVariance([1, "9", 4], 100) === false);
assert("NaN max falls back to sane scale", H._hasVariance([0, 2.5, 3], NaN) === true);
assert("negative max falls back to sane scale", H._hasVariance([0, 2.5, 3], -10) === true);

process.stdout.write("\nmetrics_pipeline_stage_model_test\n");
let m = H._pipelineStageModel([
  { label: "stt", dur: 10 },
  { label: "bad", dur: Infinity },
  { label: "neg", dur: -5 },
  null,
  { label: "nan", dur: NaN },
], 100);
assert("pipeline model filters non-finite stage durations", m.safeStages.map((s) => s.label).join(",") === "stt,neg", m.safeStages);
assert("pipeline model clamps negative durations to zero", m.safeStages[1].dur === 0, m.safeStages);
assert("pipeline model keeps provided positive display total", m.displayTotalMs === 100, m);
assert("pipeline model uses positive total as bar scale when it fits", m.barTotalMs === 100, m);
assert("pipeline model picks slowest valid stage", m.slowest && m.slowest.label === "stt", m.slowest);

m = H._pipelineStageModel([
  { label: "stt", dur: 20 },
  { label: "llm", dur: 30 },
], 0);
assert("pipeline model falls back to summed total when total is missing", m.displayTotalMs === 50, m);
assert("pipeline model scales missing-total bars to summed duration", m.barTotalMs === 50, m);

m = H._pipelineStageModel([
  { label: "stt", dur: 8 },
  { label: "llm", dur: 12 },
], 10);
assert("pipeline model preserves reported total for display", m.displayTotalMs === 10, m);
assert("pipeline model expands bar scale when stages exceed reported total", m.barTotalMs === 20, m);
assert("pipeline percentages cannot exceed 100 after normalization", m.safeStages.every((s) => (s.dur / m.barTotalMs) * 100 <= 100), m);

m = H._pipelineStageModel("not stages", NaN);
assert("pipeline model tolerates non-array stages", Array.isArray(m.safeStages) && m.safeStages.length === 0, m);
assert("pipeline model has inert totals when nothing usable exists", m.displayTotalMs === 0 && m.barTotalMs === 1 && m.slowest === null, m);

if (fails) {
  console.log("\nFailures:");
  for (const f of failures) console.log("- " + f.name + (f.detail ? ": " + JSON.stringify(f.detail) : ""));
}
console.log(`\n${passes} pass . ${fails} fail`);
process.exit(fails ? 1 : 0);
