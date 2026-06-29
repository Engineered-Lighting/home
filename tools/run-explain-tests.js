#!/usr/bin/env node
/* ============================================================================
 * tools/run-explain-tests.js
 *
 * Pure-function tests for home-explain-helpers.js (Addendum 27 M5).
 *
 *   node tools/run-explain-tests.js
 *
 * Exit 0 if all pass, 1 if any fail.
 *
 * Why: the explainability drawer's correctness depends on partitioning
 * routing-log entries into the right sections + showing the right
 * "most recent convId" for /why. A bug here = empty drawer or wrong
 * data assembly.
 * ============================================================================ */

"use strict";

const path = require("path");
const H = require(path.join(__dirname, "..", "app", "src", "home-explain-helpers.js"));

let passes = 0;
let fails = 0;
const failures = [];

function assert(name, cond, detail) {
  if (cond) {
    passes++;
    process.stdout.write("  \x1b[32mPASS\x1b[0m  " + name + "\n");
  } else {
    fails++;
    failures.push({ name, detail });
    process.stdout.write("  \x1b[31mFAIL\x1b[0m  " + name);
    if (detail !== undefined) {
      const dumped = typeof detail === "string"
        ? detail
        : JSON.stringify(detail, null, 2).replace(/\n/g, "\n        ");
      process.stdout.write("\n        " + dumped);
    }
    process.stdout.write("\n");
  }
}

/* ────────────────────────────────────────────────────────────────────────
 * Suite 1 — fmtField + ageOf
 * ──────────────────────────────────────────────────────────────────────── */
process.stdout.write("\nfmt_and_age_test\n");

assert("fmt: null returns dash", H.fmtField(null) === "—");
assert("fmt: undefined returns dash", H.fmtField(undefined) === "—");
assert("fmt: string passes through", H.fmtField("kitchen") === "kitchen");
assert("fmt: number stringifies", H.fmtField(42) === "42");
assert("fmt: boolean stringifies", H.fmtField(true) === "true" && H.fmtField(false) === "false");
assert("fmt: object JSON-encodes", H.fmtField({ a: 1 }) === '{"a":1}');

// ageOf with explicit now (deterministic)
const now = 1779000000000; // arbitrary fixed unix ms
assert("ageOf: null returns dash", H.ageOf(null, now) === "—");
assert("ageOf: ISO 30s ago",
  H.ageOf(new Date(now - 30_000).toISOString(), now) === "30s ago",
  H.ageOf(new Date(now - 30_000).toISOString(), now));
assert("ageOf: ISO 120s ago → 2m",
  H.ageOf(new Date(now - 120_000).toISOString(), now) === "2m ago",
  H.ageOf(new Date(now - 120_000).toISOString(), now));
assert("ageOf: ISO 7200s ago → 2h",
  H.ageOf(new Date(now - 7_200_000).toISOString(), now) === "2h ago",
  H.ageOf(new Date(now - 7_200_000).toISOString(), now));
assert("ageOf: unix-seconds number 45s ago",
  H.ageOf((now / 1000) - 45, now) === "45s ago",
  H.ageOf((now / 1000) - 45, now));
assert("ageOf: future ts clamps to 0s", H.ageOf(now / 1000 + 60, now) === "0s ago");
assert("ageOf: invalid string returns dash", H.ageOf("not a date", now) === "—");

/* ────────────────────────────────────────────────────────────────────────
 * Suite 2 — entryKind
 * ──────────────────────────────────────────────────────────────────────── */
process.stdout.write("\nentry_kind_test\n");

assert("entryKind: defaults to routing_decision when missing",
  H.entryKind({ ts: 0 }) === "routing_decision");
assert("entryKind: respects explicit",
  H.entryKind({ kind: "tool_call" }) === "tool_call");
assert("entryKind: null entry → routing_decision",
  H.entryKind(null) === "routing_decision");

/* ────────────────────────────────────────────────────────────────────────
 * Suite 3 — partitionEntries
 * ──────────────────────────────────────────────────────────────────────── */
process.stdout.write("\npartition_entries_test\n");

const sampleEntries = [
  { ts: 1, kind: "routing_decision", conv_id: "c1", intent: "local", matched: "HOME_VERBS",
    local_reply_snippet: "OK." },
  { ts: 2, kind: "tool_call", conv_id: "c1", tool: "execute_services", latency_ms: 12,
    result: { ok: true } },
  { ts: 3, kind: "tool_call", conv_id: "c1", tool: "find_person", latency_ms: 8,
    result: { ok: true } },
  { ts: 4, kind: "perception_dispatch", conv_id: "c1", room: "kitchen", verdict: "ok",
    latency_ms: 220 },
];
const p = H.partitionEntries(sampleEntries);
assert("partition: decision is the routing_decision row", p.decision && p.decision.tool === undefined && p.decision.matched === "HOME_VERBS");
assert("partition: toolCalls length = 2", p.toolCalls.length === 2);
assert("partition: perceptions length = 1", p.perceptions.length === 1);
assert("partition: confirmations empty", p.confirmations.length === 0);
assert("partition: other empty when all kinds known", p.other.length === 0);

// Legacy entry (no kind field) should be treated as routing_decision
const legacy = [{ ts: 1, conv_id: "c2", text: "old style" }];
const pLegacy = H.partitionEntries(legacy);
assert("partition: legacy no-kind entry classified as routing_decision",
  pLegacy.decision && pLegacy.decision.text === "old style");

// Unknown kind goes to "other"
const withCustom = [{ ts: 1, kind: "custom_thing", conv_id: "c3", foo: "bar" }];
const pCustom = H.partitionEntries(withCustom);
assert("partition: unknown kind → other bucket",
  pCustom.other.length === 1 && pCustom.other[0].foo === "bar");

// Non-array input
assert("partition: null entries returns empty",
  H.partitionEntries(null).toolCalls.length === 0 && H.partitionEntries(null).decision === null);
assert("partition: undefined entries returns empty",
  H.partitionEntries(undefined).toolCalls.length === 0);

/* ────────────────────────────────────────────────────────────────────────
 * Suite 4 — findRecentConvId
 * ──────────────────────────────────────────────────────────────────────── */
process.stdout.write("\nfind_recent_convid_test\n");

const events = [
  { id: 1, kind: "user", text: "hi" },
  { id: 2, kind: "home", text: "hello", convId: "abc" },
  { id: 3, kind: "user", text: "what time" },
  { id: 4, kind: "home", text: "9pm", convId: "def" },
  { id: 5, kind: "system", text: "diag" },                  // no convId
];
assert("findRecent: picks last convId 'def'",
  H.findRecentConvId(events) === "def");
assert("findRecent: with no convIds returns null",
  H.findRecentConvId([{ id: 1, kind: "user", text: "x" }]) === null);
assert("findRecent: empty list returns null", H.findRecentConvId([]) === null);
assert("findRecent: non-array returns null", H.findRecentConvId(null) === null);
assert("findRecent: skips events with empty convId string",
  H.findRecentConvId([
    { kind: "home", convId: "real" },
    { kind: "home", convId: "" },
  ]) === "real");

/* ────────────────────────────────────────────────────────────────────────
 * Suite 5 — turnLatencyMs
 * ──────────────────────────────────────────────────────────────────────── */
process.stdout.write("\nturn_latency_test\n");

assert("turnLatency: sample sums decision ext_latency + 2 tool latencies",
  H.turnLatencyMs(sampleEntries) === 0 + 12 + 8 + 0,
  { actual: H.turnLatencyMs(sampleEntries) });

const extOnly = [
  { kind: "routing_decision", conv_id: "x", dispatched: "external", ext_latency_ms: 1850 },
];
assert("turnLatency: external-only turn uses ext_latency_ms",
  H.turnLatencyMs(extOnly) === 1850);

const mixed = [
  { kind: "routing_decision", ext_latency_ms: 1000 },
  { kind: "tool_call", tool: "x", result: { ok: true, latency_ms: 50 } },
  { kind: "tool_call", tool: "y", latency_ms: 25 },
];
assert("turnLatency: counts latency_ms from result.latency_ms OR top-level",
  H.turnLatencyMs(mixed) === 1075);

assert("turnLatency: empty entries returns 0",
  H.turnLatencyMs([]) === 0);

/* ────────────────────────────────────────────────────────────────────────
 * Suite 6 — isWhyEligible
 * ──────────────────────────────────────────────────────────────────────── */
process.stdout.write("\nis_why_eligible_test\n");

assert("eligible: home with convId",
  H.isWhyEligible({ kind: "home", convId: "x" }) === true);
assert("eligible: external with convId",
  H.isWhyEligible({ kind: "external", convId: "x" }) === true);
assert("eligible: action with convId",
  H.isWhyEligible({ kind: "action", convId: "x" }) === true);
assert("not eligible: action with controllable + actionCtx (slider owns clicks)",
  H.isWhyEligible({ kind: "action", convId: "x", controllable: true, actionCtx: {} }) === false);
assert("not eligible: home without convId",
  H.isWhyEligible({ kind: "home" }) === false);
assert("not eligible: user bubble",
  H.isWhyEligible({ kind: "user", convId: "x" }) === false);
assert("not eligible: voice bubble (input is upstream of routing decision)",
  H.isWhyEligible({ kind: "voice", convId: "x" }) === false);
assert("not eligible: system bubble",
  H.isWhyEligible({ kind: "system", convId: "x" }) === false);
assert("not eligible: null event",
  H.isWhyEligible(null) === false);

/* ────────────────────────────────────────────────────────────────────────
 * Suite 7 — computeToolTimeline (M5+ tool timeline bar)
 * ──────────────────────────────────────────────────────────────────────── */
process.stdout.write("\ncomputeToolTimeline_test\n");

// Empty + null
assert("timeline: empty array → []",
  Array.isArray(H.computeToolTimeline([])) && H.computeToolTimeline([]).length === 0);
assert("timeline: null → []",
  H.computeToolTimeline(null).length === 0);
assert("timeline: non-array → []",
  H.computeToolTimeline({foo:1}).length === 0);

// All-zero latency → [] (nothing to visualize)
const zeroLat = [
  { tool: "a", result: { ok: true, latency_ms: 0 } },
  { tool: "b", result: { ok: true } },
];
assert("timeline: all-zero latency → [] (no signal)",
  H.computeToolTimeline(zeroLat).length === 0);

// Single tool with non-zero latency → [] (single bar = no signal; the
// section renders just the row without a timeline)
// Wait — the helper returns it. The component decides to hide.
// Verify helper returns 1 entry for a single-tool input.
const single = [{ tool: "x", latency_ms: 500, result: { ok: true } }];
const singleOut = H.computeToolTimeline(single);
assert("timeline: single tool with latency → 1 bar at 100%",
  singleOut.length === 1 && singleOut[0].widthPct === 100);

// Two equal tools → 50/50 with offsets 0 + 50
const equal = [
  { tool: "a", latency_ms: 100, result: { ok: true } },
  { tool: "b", latency_ms: 100, result: { ok: true } },
];
const equalOut = H.computeToolTimeline(equal);
assert("timeline: 2 equal → 50/50",
  Math.abs(equalOut[0].widthPct - 50) < 0.01 && Math.abs(equalOut[1].widthPct - 50) < 0.01);
assert("timeline: 2 equal offsets are 0 + 50 (Gantt-style cumulative)",
  equalOut[0].offsetPct === 0 && Math.abs(equalOut[1].offsetPct - 50) < 0.01);

// Mixed latencies: 50ms + 4800ms + 50ms → describe_clip should
// dominate (~98%) but small bars should still be visible (minPct floor)
const mixedTools = [
  { tool: "fast1", latency_ms: 50, result: { ok: true } },
  { tool: "describe_clip", latency_ms: 4800, result: { ok: true } },
  { tool: "fast2", latency_ms: 50, result: { ok: true } },
];
const mixedOut = H.computeToolTimeline(mixedTools);
assert("timeline: 3 bars produced",
  mixedOut.length === 3);
assert("timeline: small bars floored to minPct=4 (visible)",
  mixedOut[0].widthPct >= 4 && mixedOut[2].widthPct >= 4);
assert("timeline: dominant bar gets most of the width",
  mixedOut[1].widthPct > 70,
  `expected dominant > 70%, got ${mixedOut[1].widthPct}`);

// Custom minPct override
const customMin = H.computeToolTimeline([
  { tool: "tiny", latency_ms: 1, result: { ok: true } },
  { tool: "huge", latency_ms: 1000, result: { ok: true } },
], { minPct: 10 });
assert("timeline: minPct override floors small bar at 10",
  customMin[0].widthPct === 10);

// Latency from nested result.latency_ms when top-level missing
const nestedLat = [
  { tool: "a", result: { ok: true, latency_ms: 200 } },
  { tool: "b", result: { ok: true, latency_ms: 100 } },
];
const nestedOut = H.computeToolTimeline(nestedLat);
assert("timeline: reads result.latency_ms when entry.latency_ms absent",
  nestedOut.length === 2 && Math.abs(nestedOut[0].widthPct - 66.67) < 1);

// `ok` field propagates for color decisions
const okField = [
  { tool: "good", latency_ms: 100, result: { ok: true } },
  { tool: "bad", latency_ms: 100, result: { ok: false, error: "boom" } },
  { tool: "unk", latency_ms: 100, result: {} },
];
const okOut = H.computeToolTimeline(okField);
assert("timeline: ok=true propagates",
  okOut[0].ok === true);
assert("timeline: ok=false propagates",
  okOut[1].ok === false);
assert("timeline: ok=undefined propagates",
  okOut[2].ok === undefined);

// Negative or NaN latency → treated as 0 (skipped)
const badLat = [
  { tool: "neg", latency_ms: -100, result: {} },
  { tool: "nan", latency_ms: NaN, result: {} },
  { tool: "real", latency_ms: 100, result: { ok: true } },
];
const badOut = H.computeToolTimeline(badLat);
assert("timeline: real tool gets 100% (bad latencies treated as 0)",
  Math.abs(badOut[2].widthPct - 100) < 0.01 || badOut[2].widthPct === 100);

// Entry without `tool` or `name` field → name = "(tool)"
const noName = [
  { latency_ms: 100, result: { ok: true } },
  { name: "secondary", latency_ms: 100, result: { ok: true } },
];
const noNameOut = H.computeToolTimeline(noName);
assert("timeline: missing tool/name falls back to '(tool)'",
  noNameOut[0].name === "(tool)");
assert("timeline: uses entry.name when entry.tool absent",
  noNameOut[1].name === "secondary");

/* ────────────────────────────────────────────────────────────────────────
 * Footer
 * ──────────────────────────────────────────────────────────────────────── */
process.stdout.write("\n");
if (fails === 0) {
  process.stdout.write("\x1b[32m" + passes + " pass · 0 fail\x1b[0m\n");
  process.exit(0);
} else {
  process.stdout.write("\x1b[31m" + passes + " pass · " + fails + " fail\x1b[0m\n");
  process.exit(1);
}
