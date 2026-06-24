/* ============================================================================
 * home-explain-helpers.js — pure helpers for the explainability drawer.
 *
 * Extracted from home-explain.jsx so they can be required directly by
 * the Node test runner (which can't transpile JSX). Both the JSX
 * drawer and the test runner load these helpers.
 *
 * No React, no DOM, no Tauri. Just JS.
 * ============================================================================ */

"use strict";

/** Format a single field for compact display. Robust to nulls. */
function fmtField(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  try { return JSON.stringify(value); } catch { return String(value); }
}

/** Age of a ts (ISO string OR unix-seconds number) relative to now.
 *  Returns "—" for invalid; "<60s ago", "<60m ago", "<24h ago" else. */
function ageOf(ts, nowMs) {
  if (!ts) return "—";
  let ms;
  if (typeof ts === "number") ms = Math.floor(ts * 1000);
  else if (typeof ts === "string") ms = Date.parse(ts);
  if (!Number.isFinite(ms)) return "—";
  const now = nowMs == null ? Date.now() : nowMs;
  const sec = Math.max(0, Math.floor((now - ms) / 1000));
  if (sec < 60) return sec + "s ago";
  if (sec < 3600) return Math.floor(sec / 60) + "m ago";
  return Math.floor(sec / 3600) + "h ago";
}

/** Detect entry "kind" with backward-compat default of routing_decision
 *  (the LOG_KIND_ROUTING_DECISION default in external_routing.py is
 *  added during write, but legacy entries on disk may be missing it). */
function entryKind(entry) {
  return (entry && entry.kind) || "routing_decision";
}

/** Group entries from /routing_log?conv_id=… into the drawer's
 *  rendered sections. Returns:
 *  {
 *    decision:      routing_decision entry (single) OR null,
 *    toolCalls:     tool_call[],
 *    perceptions:   perception_dispatch[],
 *    confirmations: confirmation[],
 *    other:         everything else, by-kind,
 *  } */
function partitionEntries(entries) {
  if (!Array.isArray(entries)) {
    return { decision: null, toolCalls: [], perceptions: [], confirmations: [], other: [] };
  }
  const decision = entries.find((e) => entryKind(e) === "routing_decision") || null;
  const toolCalls = entries.filter((e) => entryKind(e) === "tool_call");
  const perceptions = entries.filter((e) => entryKind(e) === "perception_dispatch");
  const confirmations = entries.filter((e) => entryKind(e) === "confirmation");
  const known = new Set(["routing_decision", "tool_call", "perception_dispatch", "confirmation"]);
  const other = entries.filter((e) => !known.has(entryKind(e)) && e !== decision);
  return { decision, toolCalls, perceptions, confirmations, other };
}

/** Walk an events array right-to-left for the most recent one with
 *  a non-empty convId. Used by /why with no arg. */
function findRecentConvId(events) {
  if (!Array.isArray(events)) return null;
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e && e.convId) return e.convId;
  }
  return null;
}

/** Compute the cumulative latency of a turn from its entries.
 *  routing_decision.ext_latency_ms (for external) + sum of tool
 *  latencies. */
function turnLatencyMs(entries) {
  const p = partitionEntries(entries);
  let total = 0;
  if (p.decision) {
    total += Number(p.decision.ext_latency_ms || 0);
  }
  for (const tc of p.toolCalls) {
    total += Number(tc.latency_ms || (tc.result && tc.result.latency_ms) || 0);
  }
  return total;
}

/** Determine if a bubble event is "why-eligible" — what enables the
 *  click-to-open path. Mirrors the gate in EventContent. */
function isWhyEligible(event) {
  if (!event || !event.convId) return false;
  const k = event.kind;
  if (k === "home" || k === "external") return true;
  if (k === "action") {
    // Controllable cards have their own interactive surface; skip.
    if (event.controllable && event.actionCtx) return false;
    return true;
  }
  return false;
}

/** M5+ (Addendum 27): compute proportional widths for the tool
 *  timeline bar visualization. Takes the tool_call entries and
 *  returns an array of {name, latency_ms, widthPct, offsetPct}
 *  ready for direct render as horizontal bars. Widths are
 *  proportional to each tool's share of the SUMMED latency (not
 *  total turn — that would include LLM + sample time). Offsets
 *  cumulate so bars sit side-by-side like a Gantt strip rather
 *  than overlapping.
 *
 *  Each bar gets a minimum width of `minPct` (default 4%) so very
 *  fast tools are still visible / hoverable. The sum can exceed
 *  100% in that case — caller clips overflow visually.
 *
 *  Returns [] for empty input or when no tool has measurable
 *  latency (every latency_ms missing or zero). */
function computeToolTimeline(toolCalls, opts) {
  opts = opts || {};
  const minPct = opts.minPct == null ? 4 : opts.minPct;
  if (!Array.isArray(toolCalls) || toolCalls.length === 0) return [];
  // Pull latency_ms from either top-level entry.latency_ms or nested
  // result.latency_ms (M0.3 tool result shape).
  const lat = (e) => {
    const v = e && (e.latency_ms != null ? e.latency_ms : (e.result && e.result.latency_ms));
    return (typeof v === "number" && Number.isFinite(v) && v >= 0) ? v : 0;
  };
  const total = toolCalls.reduce((acc, e) => acc + lat(e), 0);
  if (total <= 0) return [];
  let offsetPct = 0;
  const out = [];
  for (const e of toolCalls) {
    const ms = lat(e);
    let pct = (ms / total) * 100;
    if (pct < minPct && ms > 0) pct = minPct;
    out.push({
      name: (e && (e.tool || e.name)) || "(tool)",
      latency_ms: ms,
      widthPct: pct,
      offsetPct,
      ok: e && e.result && e.result.ok,
    });
    offsetPct += pct;
  }
  return out;
}

// Dual export — window for the browser, module.exports for Node tests.
if (typeof window !== "undefined") {
  window.fmtExplainField = fmtField;
  window.ageOfExplainTsHelper = ageOf;
  window.entryKindHelper = entryKind;
  window.partitionExplainEntriesHelper = partitionEntries;
  window.findRecentConvIdHelper = findRecentConvId;
  window.turnLatencyMsHelper = turnLatencyMs;
  window.isWhyEligibleHelper = isWhyEligible;
  window.computeToolTimelineHelper = computeToolTimeline;
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    fmtField, ageOf, entryKind,
    partitionEntries, findRecentConvId,
    turnLatencyMs, isWhyEligible,
    computeToolTimeline,
  };
}
