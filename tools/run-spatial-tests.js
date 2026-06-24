#!/usr/bin/env node
/* ============================================================================
 * run-spatial-tests.js — unit tests for app/src/home-spatial-helpers.js
 * (Addendum 38 Phase 1).
 *
 * Pure-function tests for the /spatial drawer's load-bearing maths:
 * footprint-cell parsing + SVG projection, the weight colour ramp, the
 * 4×4 region map, per-light calibration status, model roll-up.
 *
 * No browser, no React. Exit 0 = all pass.
 * ============================================================================ */
"use strict";

const path = require("path");
const H = require(path.join(__dirname, "..", "app", "src",
                            "home-spatial-helpers.js"));

let passed = 0;
let failed = 0;

function check(name, ok, detail) {
  if (ok) {
    passed += 1;
    console.log("  PASS  " + name);
  } else {
    failed += 1;
    console.log("  FAIL  " + name + (detail ? "  " + detail : ""));
  }
}

// ───────────────────────── parseCell ──────────────────────────

let v = H.parseCell("3,11");
check("parseCell: '3,11' → {c:3,r:11}",
      v && v.c === 3 && v.r === 11, JSON.stringify(v));
check("parseCell: malformed → null", H.parseCell("bad") === null);
check("parseCell: three parts → null", H.parseCell("1,2,3") === null);
check("parseCell: non-string → null", H.parseCell(null) === null);

// ───────────────────────── cellRect ───────────────────────────

v = H.cellRect(0, 0, 32, 18, 640, 360);
check("cellRect: top-left cell at origin, 20×20",
      v.x === 0 && v.y === 0 && v.w === 20 && v.h === 20, JSON.stringify(v));
v = H.cellRect(31, 17, 32, 18, 640, 360);
check("cellRect: bottom-right cell positioned correctly",
      v.x === 620 && v.y === 340, JSON.stringify(v));

// ─────────────────────── footprintColor ───────────────────────

check("footprintColor: uses the ice RGB",
      H.footprintColor(0.5).indexOf("184,216,255") >= 0, H.footprintColor(0.5));
check("footprintColor: weight 0 → faint alpha 0.120",
      H.footprintColor(0).indexOf("0.120") >= 0, H.footprintColor(0));
check("footprintColor: weight 1 → bright alpha 0.780",
      H.footprintColor(1).indexOf("0.780") >= 0, H.footprintColor(1));
check("footprintColor: clamps weight > 1",
      H.footprintColor(5) === H.footprintColor(1));

// ─────────────────────── footprintSummary ─────────────────────

v = H.footprintSummary({ "0,0": 1.0, "1,0": 0.5 });
check("footprintSummary: cellCount + peak + total",
      v.cellCount === 2 && v.peak === 1.0 && v.total === 1.5, JSON.stringify(v));
v = H.footprintSummary({});
check("footprintSummary: empty dict → all zero",
      v.cellCount === 0 && v.peak === 0 && v.total === 0, JSON.stringify(v));
v = H.footprintSummary(null);
check("footprintSummary: null → all zero",
      v.cellCount === 0 && v.peak === 0, JSON.stringify(v));

// ─────────────────────── regionForCell ────────────────────────

check("regionForCell: top-left → A1",
      H.regionForCell(0, 0, 32, 18) === "A1");
check("regionForCell: bottom-right → D4",
      H.regionForCell(31, 17, 32, 18) === "D4");
check("regionForCell: mid → C3",
      H.regionForCell(16, 9, 32, 18) === "C3", H.regionForCell(16, 9, 32, 18));

// ──────────────────────── lightStatus ─────────────────────────

check("lightStatus: human confirmed → confirmed",
      H.lightStatus({ human_verdict: "confirmed" }).key === "confirmed");
check("lightStatus: human marked_empty → marked_empty",
      H.lightStatus({ human_verdict: "marked_empty" }).key === "marked_empty");
check("lightStatus: vlm disagreement → disagree (needs review)",
      H.lightStatus({ vlm_disagreement: true }).key === "disagree");
check("lightStatus: vlm validated → validated",
      H.lightStatus({ vlm_validated: true }).key === "validated");
check("lightStatus: footprint_empty → empty",
      H.lightStatus({ footprint_empty: true }).key === "empty");
check("lightStatus: bare record → unreviewed",
      H.lightStatus({}).key === "unreviewed");
check("lightStatus: null → unknown",
      H.lightStatus(null).key === "unknown");
check("lightStatus: human verdict beats vlm verdict",
      H.lightStatus({ human_verdict: "confirmed",
                      vlm_disagreement: true }).key === "confirmed");

// ─────────────────────── strongestCamera ──────────────────────

v = H.strongestCamera({ footprint: {
  living_room: { "0,0": 0.2 },
  kitchen: { "0,0": 0.9, "1,0": 0.9 },
} });
check("strongestCamera: picks the higher-total camera",
      v === "kitchen", String(v));
check("strongestCamera: no footprint → null",
      H.strongestCamera({ footprint: {} }) === null);
check("strongestCamera: no record → null",
      H.strongestCamera({}) === null);

// ──────────────────────── modelSummary ────────────────────────

v = H.modelSummary({ lights: {
  a: { vlm_validated: true },
  b: { vlm_disagreement: true },
  c: { footprint_empty: true },
  d: {},
} });
check("modelSummary: rolls up confirmed/needsReview/empty/uncalibrated",
      v.total === 4 && v.confirmed === 1 && v.needsReview === 1 &&
      v.empty === 1 && v.uncalibrated === 1, JSON.stringify(v));
v = H.modelSummary({});
check("modelSummary: empty model → all zero", v.total === 0);

// ─────────────────────── zonePolygonPoints ────────────────────
// Frigate zone geometry shares our normalised [0,1] camera frame.

check("zonePolygonPoints: norm verts → SVG pixel points",
      H.zonePolygonPoints([[0, 0], [0.5, 0.5], [1, 1]], 1280, 720)
        === "0,0 640,360 1280,720",
      H.zonePolygonPoints([[0, 0], [0.5, 0.5], [1, 1]], 1280, 720));
check("zonePolygonPoints: empty / non-array → ''",
      H.zonePolygonPoints([], 1280, 720) === "" &&
      H.zonePolygonPoints(null, 1280, 720) === "");
check("zonePolygonPoints: skips malformed vertices",
      H.zonePolygonPoints([[0.1, 0.1], "bad", [0.2, 0.2]], 1000, 1000)
        === "100,100 200,200",
      H.zonePolygonPoints([[0.1, 0.1], "bad", [0.2, 0.2]], 1000, 1000));

// ────────────────────────── zoneColor ─────────────────────────

check("zoneColor: [r,g,b] → rgb() string",
      H.zoneColor([31, 119, 180]) === "rgb(31,119,180)",
      H.zoneColor([31, 119, 180]));
check("zoneColor: missing colour → fallback",
      H.zoneColor(null, "rgb(1,2,3)") === "rgb(1,2,3)");
check("zoneColor: clamps out-of-range channels",
      H.zoneColor([300, -5, 128]) === "rgb(255,0,128)",
      H.zoneColor([300, -5, 128]));

// ─────────────────────── reviewBuckets ────────────────────────
// The guided review queue: lights needing a human look vs settled.

v = H.reviewBuckets({
  a: { vlm_validated: true },                       // validated → reviewed
  b: { vlm_disagreement: true },                    // disagree  → needs review
  c: { footprint_empty: true },                     // empty     → reviewed
  d: {},                                            // unreviewed→ needs review
  e: { human_verdict: "confirmed" },                // confirmed → reviewed
  f: { state_mismatch: true, vlm_validated: true }, // mismatch  → needs review
});
check("reviewBuckets: splits needs-review vs reviewed",
      v.needsReview.join() === "b,d,f" && v.reviewed.join() === "a,c,e",
      JSON.stringify(v));
check("reviewBuckets: state_mismatch forces review even if vlm agreed",
      v.needsReview.indexOf("f") >= 0);
check("reviewBuckets: human verdict settles a vlm-disagreement light",
      H.reviewBuckets({ x: { vlm_disagreement: true,
                             human_verdict: "confirmed" } })
        .reviewed.join() === "x");
v = H.reviewBuckets({});
check("reviewBuckets: empty model → empty buckets",
      v.needsReview.length === 0 && v.reviewed.length === 0);
v = H.reviewBuckets(null);
check("reviewBuckets: null → empty buckets",
      v.needsReview.length === 0 && v.reviewed.length === 0,
      JSON.stringify(v));
v = H.reviewBuckets({ z: {}, a: { vlm_validated: true } });
check("reviewBuckets: ids sorted within each bucket",
      v.needsReview.join() === "z" && v.reviewed.join() === "a");

// ─────────────────────── pointInPolygon ───────────────────────

const _sq = [[0, 0], [1, 0], [1, 1], [0, 1]];
check("pointInPolygon: centre of the unit square is inside",
      H.pointInPolygon(0.5, 0.5, _sq) === true);
check("pointInPolygon: a point outside the square",
      H.pointInPolygon(1.5, 0.5, _sq) === false);
check("pointInPolygon: degenerate (<3 verts) contains nothing",
      H.pointInPolygon(0.5, 0.5, [[0, 0], [1, 1]]) === false);

// ────────────────────── rasterizePolygon ──────────────────────
// A polygon covering the top-left quarter of a 4×4 grid → the 4 cells
// whose centres ((c+0.5)/4, (r+0.5)/4) fall inside [0,0.5]×[0,0.5].

v = H.rasterizePolygon([[0, 0], [0.5, 0], [0.5, 0.5], [0, 0.5]], 4, 4);
check("rasterizePolygon: top-left quarter → 4 cells",
      Object.keys(v).sort().join("|") === "0,0|0,1|1,0|1,1",
      Object.keys(v).sort().join("|"));
check("rasterizePolygon: rasterised cells are weight 1.0", v["0,0"] === 1.0);
check("rasterizePolygon: degenerate polygon → {}",
      Object.keys(H.rasterizePolygon([[0, 0], [1, 1]], 4, 4)).length === 0);

// ────────────────────── polygonCentroid ───────────────────────

v = H.polygonCentroid([[0, 0], [1, 0], [1, 1], [0, 1]]);
check("polygonCentroid: unit square → (0.5, 0.5)",
      v && Math.abs(v[0] - 0.5) < 1e-6 && Math.abs(v[1] - 0.5) < 1e-6,
      JSON.stringify(v));
check("polygonCentroid: empty polygon → null",
      H.polygonCentroid([]) === null);

console.log("\n" + passed + " passed, " + failed + " failed");
process.exit(failed === 0 ? 0 : 1);
