/* ============================================================================
 * home-spatial-helpers.js — Addendum 38 Phase 1
 *
 * Pure-function helpers for the /spatial drawer (home-spatial.jsx). Plain JS
 * (no JSX, no React) so a Node test runner (tools/run-spatial-tests.js) can
 * require() this directly without Babel. The browser loads it as a <script>
 * BEFORE home-spatial.jsx and consumes via window.HomeSpatialHelpers.*
 *
 * These functions are the load-bearing maths of the light-footprint Map tab:
 * footprint-cell → SVG-rect projection, the weight colour ramp, per-light
 * calibration status, the 4×4 region map (mirrors the Python cell_to_region
 * in tools/calibrate-light-footprints.py). Splitting them out keeps the JSX
 * thin and lets the maths be unit-tested per the Addendum 18 discipline.
 * ============================================================================ */

(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (typeof window !== "undefined") {
    window.HomeSpatialHelpers = api;
  } else if (typeof global !== "undefined") {
    global.HomeSpatialHelpers = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function round3(x) { return Math.round((Number(x) || 0) * 1000) / 1000; }

  /* Parse a "col,row" footprint key → {c, r}. null if malformed. */
  function parseCell(key) {
    if (typeof key !== "string") return null;
    const parts = key.split(",");
    if (parts.length !== 2) return null;
    const c = parseInt(parts[0], 10);
    const r = parseInt(parts[1], 10);
    if (!Number.isFinite(c) || !Number.isFinite(r)) return null;
    return { c, r };
  }

  /* SVG rect for a grid cell given the grid dims + the SVG viewport. */
  function cellRect(c, r, gridCols, gridRows, svgW, svgH) {
    const w = svgW / Math.max(gridCols, 1);
    const h = svgH / Math.max(gridRows, 1);
    return { x: c * w, y: r * h, w: w, h: h };
  }

  /* Footprint weight (0..1) → fill colour. Monochrome ice ramp — faint at
   * low weight, bright ice at the peak. Alpha carries the weight so the
   * footprint reads as a heatmap over the camera backdrop. */
  function footprintColor(weight) {
    const w = Math.max(0, Math.min(1, Number(weight) || 0));
    const alpha = 0.12 + 0.66 * w;            // 0.12 → 0.78
    return "rgba(184,216,255," + alpha.toFixed(3) + ")";
  }

  /* Summary stats for one camera's footprint dict {cell: weight}. */
  function footprintSummary(fp) {
    const keys = fp ? Object.keys(fp) : [];
    if (keys.length === 0) return { cellCount: 0, peak: 0, total: 0 };
    let peak = 0;
    let total = 0;
    for (let i = 0; i < keys.length; i++) {
      const w = Number(fp[keys[i]]) || 0;
      if (w > peak) peak = w;
      total += w;
    }
    return { cellCount: keys.length, peak: round3(peak), total: round3(total) };
  }

  /* Coarse 4×4 region label (A1..D4) for a fine cell — mirrors the Python
   * cell_to_region() so the Map tab can show the same region the VLM
   * cross-check answered against. */
  function regionForCell(c, r, gridCols, gridRows) {
    const col = "ABCD"[Math.min(3, Math.floor(c * 4 / Math.max(gridCols, 1)))];
    const row = Math.min(3, Math.floor(r * 4 / Math.max(gridRows, 1))) + 1;
    return col + row;
  }

  /* Display status for a light's calibration record. Precedence: an
   * explicit human verdict wins; then footprint_empty; then the VLM
   * verdict; else uncalibrated. {key, label, color}. */
  function lightStatus(rec) {
    if (!rec) {
      return { key: "unknown", label: "no data", color: "var(--hg-fg-4)" };
    }
    const hv = rec.human_verdict;
    if (hv === "confirmed") {
      return { key: "confirmed", label: "confirmed", color: "var(--hg-ice)" };
    }
    if (hv === "rejected") {
      return { key: "rejected", label: "rejected", color: "var(--hg-crit)" };
    }
    if (hv === "recalibrate_requested") {
      return { key: "recalibrate", label: "recalibrate", color: "var(--hg-warn)" };
    }
    if (hv === "marked_empty") {
      return { key: "marked_empty", label: "marked empty", color: "var(--hg-fg-4)" };
    }
    if (rec.footprint_empty) {
      return { key: "empty", label: "no footprint", color: "var(--hg-fg-4)" };
    }
    if (rec.vlm_disagreement) {
      return { key: "disagree", label: "needs review", color: "var(--hg-warn)" };
    }
    if (rec.vlm_validated) {
      return { key: "validated", label: "vlm agree", color: "var(--hg-ice)" };
    }
    return { key: "unreviewed", label: "uncalibrated", color: "var(--hg-fg-3)" };
  }

  /* The camera whose footprint for this light carries the most signal,
   * or null when the light has no footprint anywhere. */
  function strongestCamera(rec) {
    const fp = rec && rec.footprint;
    if (!fp) return null;
    let best = null;
    let bestTotal = -1;
    const cams = Object.keys(fp);
    for (let i = 0; i < cams.length; i++) {
      const t = footprintSummary(fp[cams[i]]).total;
      if (t > bestTotal) { bestTotal = t; best = cams[i]; }
    }
    return best;
  }

  /* Roll-up counts for the drawer header. */
  function modelSummary(model) {
    const lights = (model && model.lights) || {};
    const out = { total: 0, confirmed: 0, needsReview: 0,
                  empty: 0, uncalibrated: 0 };
    const ids = Object.keys(lights);
    for (let i = 0; i < ids.length; i++) {
      out.total += 1;
      const key = lightStatus(lights[ids[i]]).key;
      if (key === "confirmed" || key === "validated") out.confirmed += 1;
      else if (key === "disagree" || key === "recalibrate") out.needsReview += 1;
      else if (key === "empty" || key === "marked_empty") out.empty += 1;
      else out.uncalibrated += 1;
    }
    return out;
  }

  /* Frigate zone polygon ([[x,y],...] normalised 0..1) → an SVG <polygon>
   * `points` string in the given viewBox pixel space. Frigate zone
   * geometry and our footprint grid share the same normalised camera
   * frame, so they overlay natively — no homography. */
  function zonePolygonPoints(polygon, svgW, svgH) {
    if (!Array.isArray(polygon) || polygon.length === 0) return "";
    const w = Number(svgW) || 0;
    const h = Number(svgH) || 0;
    const pts = [];
    for (let i = 0; i < polygon.length; i++) {
      const p = polygon[i];
      if (!Array.isArray(p) || p.length < 2) continue;
      pts.push(round3((Number(p[0]) || 0) * w) + "," +
               round3((Number(p[1]) || 0) * h));
    }
    return pts.join(" ");
  }

  /* Frigate per-zone [r,g,b] → a CSS rgb() string, with a fallback when
   * the colour is missing or malformed. */
  function zoneColor(rgb, fallback) {
    if (Array.isArray(rgb) && rgb.length === 3) {
      const c = rgb.map(function (v) {
        return Math.max(0, Math.min(255, Math.round(Number(v) || 0)));
      });
      return "rgb(" + c[0] + "," + c[1] + "," + c[2] + ")";
    }
    return fallback || "rgba(184,216,255,0.85)";
  }

  /* Partition a model's lights into the review queue: lights that still
   * need a human decision vs lights already settled. A light needs
   * review when the VLM cross-check disagreed, the check did not run, or
   * the light failed to toggle during calibration (state_mismatch).
   * Returns {needsReview:[ids], reviewed:[ids]}, each sorted. */
  function reviewBuckets(lights) {
    const needsReview = [];
    const reviewed = [];
    const ids = Object.keys(lights || {}).sort();
    for (let i = 0; i < ids.length; i++) {
      const rec = lights[ids[i]] || {};
      const key = lightStatus(rec).key;
      const needs = !!rec.state_mismatch ||
        key === "disagree" || key === "unreviewed" || key === "unknown";
      (needs ? needsReview : reviewed).push(ids[i]);
    }
    return { needsReview: needsReview, reviewed: reviewed };
  }

  /* Ray-casting point-in-polygon. `poly` is [[x,y],...]; (px,py) in the
   * same coordinate space. A polygon under 3 vertices contains nothing. */
  function pointInPolygon(px, py, poly) {
    if (!Array.isArray(poly) || poly.length < 3) return false;
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const xi = Number(poly[i][0]) || 0, yi = Number(poly[i][1]) || 0;
      const xj = Number(poly[j][0]) || 0, yj = Number(poly[j][1]) || 0;
      const hit = ((yi > py) !== (yj > py)) &&
        (px < (xj - xi) * (py - yi) / ((yj - yi) || 1e-12) + xi);
      if (hit) inside = !inside;
    }
    return inside;
  }

  /* Rasterise a normalised-[0,1] polygon to footprint cells: every grid
   * cell whose CENTRE falls inside the polygon → {"col,row": 1.0}. The
   * drawn polygon is the source of truth; this is the cell form the
   * spatial model + the existing renderers consume. */
  function rasterizePolygon(polygon, cols, rows) {
    const out = {};
    if (!Array.isArray(polygon) || polygon.length < 3) return out;
    const C = Math.max(1, cols | 0), R = Math.max(1, rows | 0);
    for (let r = 0; r < R; r++) {
      for (let c = 0; c < C; c++) {
        if (pointInPolygon((c + 0.5) / C, (r + 0.5) / R, polygon)) {
          out[c + "," + r] = 1.0;
        }
      }
    }
    return out;
  }

  /* Area-weighted (shoelace) centroid of a normalised polygon. Phase 3
   * gradient lighting keys off this — the person's distance to each
   * light's footprint centroid. Degenerate (zero-area) polygons fall
   * back to the vertex mean; empty input → null. */
  function polygonCentroid(polygon) {
    if (!Array.isArray(polygon) || polygon.length === 0) return null;
    const mean = function () {
      let sx = 0, sy = 0;
      for (let i = 0; i < polygon.length; i++) {
        sx += Number(polygon[i][0]) || 0;
        sy += Number(polygon[i][1]) || 0;
      }
      return [sx / polygon.length, sy / polygon.length];
    };
    if (polygon.length < 3) return mean();
    let a = 0, cx = 0, cy = 0;
    for (let i = 0, n = polygon.length; i < n; i++) {
      const x0 = Number(polygon[i][0]) || 0, y0 = Number(polygon[i][1]) || 0;
      const j = (i + 1) % n;
      const x1 = Number(polygon[j][0]) || 0, y1 = Number(polygon[j][1]) || 0;
      const cross = x0 * y1 - x1 * y0;
      a += cross;
      cx += (x0 + x1) * cross;
      cy += (y0 + y1) * cross;
    }
    if (Math.abs(a) < 1e-12) return mean();
    return [round3(cx / (3 * a)), round3(cy / (3 * a))];
  }

  return {
    parseCell: parseCell,
    cellRect: cellRect,
    footprintColor: footprintColor,
    footprintSummary: footprintSummary,
    regionForCell: regionForCell,
    lightStatus: lightStatus,
    strongestCamera: strongestCamera,
    modelSummary: modelSummary,
    zonePolygonPoints: zonePolygonPoints,
    zoneColor: zoneColor,
    reviewBuckets: reviewBuckets,
    pointInPolygon: pointInPolygon,
    rasterizePolygon: rasterizePolygon,
    polygonCentroid: polygonCentroid,
  };
});
