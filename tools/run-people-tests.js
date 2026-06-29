#!/usr/bin/env node
/* ============================================================================
 * tools/run-people-tests.js
 *
 * Pure-function tests for home-people-helpers.js (Addendum 14, Slice 5).
 * Same shape as run-lab-tests.js — no JSX, no React, no deps.
 *
 *   node tools/run-people-tests.js
 *
 * Exit 0 if all pass, 1 if any fail.
 *
 * Why: layout math + ring assignment determine whether the relationship
 * graph renders correctly. A bug → nodes off-screen or stacking on the
 * center. These tests catch geometry regressions in milliseconds.
 * ============================================================================ */

"use strict";

const fs = require("fs");
const path = require("path");
const H = require(path.join(__dirname, "..", "app", "src", "home-people-helpers.js"));
const peopleSource = fs.readFileSync(path.join(__dirname, "..", "app", "src", "home-people.jsx"), "utf8");
const appSource = fs.readFileSync(path.join(__dirname, "..", "app", "src", "home-app.jsx"), "utf8");

let passes = 0;
let fails = 0;
const failures = [];

function assert(name, cond, detail) {
  if (cond) {
    passes++;
    process.stdout.write("  [32mPASS[0m  " + name + "\n");
  } else {
    fails++;
    failures.push({ name, detail });
    process.stdout.write("  [31mFAIL[0m  " + name);
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

/* ────────────────────────────────────────────────────────────────────────
 * Suite 1 — ring assignment
 * ──────────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mring assignment[0m\n");

(function () {
  const me = { relationship_type: "me" };
  assert("'me' → center (ring 0)", H.ringForIdentity(me) === 0);
  assert("partner → ring 1",  H.ringForIdentity({ relationship_type: "partner" })  === 1);
  assert("immediate family → ring 1", H.ringForIdentity({ relationship_type: "family_immediate" }) === 1);
  assert("roommate → ring 1",  H.ringForIdentity({ relationship_type: "roommate" })  === 1);
  assert("friend → ring 2",    H.ringForIdentity({ relationship_type: "friend" })    === 2);
  assert("extended family → ring 2", H.ringForIdentity({ relationship_type: "family_extended" }) === 2);
  assert("guest → ring 2",     H.ringForIdentity({ relationship_type: "guest" })     === 2);
  assert("neighbor → ring 3",  H.ringForIdentity({ relationship_type: "neighbor" })  === 3);
  assert("service → ring 3",   H.ringForIdentity({ relationship_type: "service" })   === 3);
  assert("unknown → null (not in graph)",
    H.ringForIdentity({ relationship_type: "unknown" }) === null);
  assert("do_not_identify → null",
    H.ringForIdentity({ relationship_type: "do_not_identify" }) === null);
  assert("null identity → null", H.ringForIdentity(null) === null);
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite 2 — avatar size + radius
 * ──────────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mavatar size + ring radius[0m\n");

(function () {
  assert("center avatar 80px", H.avatarSizeForRing(0) === 80);
  assert("ring 1 avatar 60px", H.avatarSizeForRing(1) === 60);
  assert("ring 2 avatar 48px", H.avatarSizeForRing(2) === 48);
  assert("ring 3 avatar 36px", H.avatarSizeForRing(3) === 36);
  assert("ring 1 radius 150",  H.radiusForRing(1) === 150);
  assert("ring 2 radius 270",  H.radiusForRing(2) === 270);
  assert("ring 3 radius 380",  H.radiusForRing(3) === 380);
  assert("ring 0 radius 0",    H.radiusForRing(0) === 0);
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite 3 — buildRadialLayout
 * ──────────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mradial layout[0m\n");

(function () {
  const empty = H.buildRadialLayout([]);
  assert("empty input → empty output", empty.length === 0);
})();

(function () {
  // Just Marcelo at center
  const layout = H.buildRadialLayout([
    { uuid: "m", relationship_type: "me" },
  ]);
  assert("just 'me' → 1 node at origin",
    layout.length === 1 && layout[0].x === 0 && layout[0].y === 0,
    layout);
  assert("center node has ring 0 + size 80",
    layout[0].ring === 0 && layout[0].size === 80);
})();

(function () {
  // Marcelo + 1 partner in Ring 1
  const layout = H.buildRadialLayout([
    { uuid: "m", relationship_type: "me" },
    { uuid: "s", relationship_type: "partner" },
  ]);
  assert("center + 1 partner → 2 nodes",
    layout.length === 2);
  const partner = layout.find((n) => n.uuid === "s");
  assert("partner placed at top of ring 1",
    partner && partner.ring === 1
      && approxEq(partner.x, 0, 0.001)
      && approxEq(partner.y, -150, 0.001),
    partner);
})();

(function () {
  // Marcelo + 4 partners (well, 4 in ring 1) — should be evenly spaced
  const layout = H.buildRadialLayout([
    { uuid: "m",  relationship_type: "me" },
    { uuid: "p1", relationship_type: "partner" },
    { uuid: "p2", relationship_type: "family_immediate" },
    { uuid: "p3", relationship_type: "roommate" },
    { uuid: "p4", relationship_type: "family_immediate" },
  ]);
  const ring1 = layout.filter((n) => n.ring === 1);
  assert("4 ring-1 nodes laid out", ring1.length === 4);
  // All should be at radius 150
  for (const n of ring1) {
    const r = Math.hypot(n.x, n.y);
    assert(`ring-1 node ${n.uuid} on radius 150 (got ${r.toFixed(2)})`,
      approxEq(r, 150, 0.5));
  }
  // Angles should be evenly distributed
  const angles = ring1.map((n) => Math.atan2(n.y, n.x)).sort((a, b) => a - b);
  for (let i = 1; i < angles.length; i++) {
    const delta = angles[i] - angles[i - 1];
    assert(`ring-1 angle spacing[${i}] ≈ π/2 (got ${delta.toFixed(3)})`,
      approxEq(delta, Math.PI / 2, 0.01));
  }
})();

(function () {
  // Overflow handling — > overflowCap nodes in one ring
  const ids = [{ uuid: "m", relationship_type: "me" }];
  for (let i = 0; i < 15; i++) {
    ids.push({ uuid: `f${i}`, relationship_type: "friend" });
  }
  const layout = H.buildRadialLayout(ids, { overflowCap: 12 });
  const ring2Visible = layout.filter((n) => n.ring === 2 && !n.overflow);
  const overflow = layout.filter((n) => n.overflow);
  assert("12 visible ring-2 nodes (capped)", ring2Visible.length === 12);
  assert("1 overflow chip rendered", overflow.length === 1);
  assert("overflow count = 3", overflow[0].overflowCount === 3);
})();

(function () {
  // Manual positions override auto-layout
  const layout = H.buildRadialLayout(
    [
      { uuid: "m", relationship_type: "me" },
      { uuid: "s", relationship_type: "partner" },
    ],
    { manualPositions: { s: { x: 99, y: -33 } } },
  );
  const partner = layout.find((n) => n.uuid === "s");
  assert("manual position overrides auto-layout",
    partner.x === 99 && partner.y === -33 && partner.pinned === true);
})();

(function () {
  // Identities with relationship_type 'unknown' are EXCLUDED
  const layout = H.buildRadialLayout([
    { uuid: "m", relationship_type: "me" },
    { uuid: "x", relationship_type: "unknown" },
    { uuid: "y", relationship_type: "do_not_identify" },
    { uuid: "z", relationship_type: "friend" },
  ]);
  const uuids = layout.map((n) => n.uuid).filter((u) => !u.startsWith("__"));
  assert("unknown + do_not_identify excluded from graph",
    !uuids.includes("x") && !uuids.includes("y") && uuids.includes("z"),
    uuids);
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite 4 — buildEdgeGeometry
 * ──────────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1medge geometry[0m\n");

(function () {
  // Implicit edges: ALL non-center, non-overflow nodes get an edge to
  // center (Addendum 17 Bug 3 fix — previously only ring 1 got one,
  // which left ring-2/3 nodes visually orphaned).
  const layout = H.buildRadialLayout([
    { uuid: "m", relationship_type: "me" },
    { uuid: "s", relationship_type: "partner" },     // ring 1
    { uuid: "f", relationship_type: "friend" },       // ring 2
  ]);
  const edges = H.buildEdgeGeometry(layout, []);
  assert("every non-center node → 1 implicit edge to center",
    edges.length === 2 && edges.every((e) => e.implicit === true),
    edges);
  assert("friend in ring 2 → implicit edge present",
    edges.some((e) => e.fromUuid === "f"));
  assert("partner in ring 1 → implicit edge present",
    edges.some((e) => e.fromUuid === "s"));
})();

(function () {
  // Explicit relationship rows ADD to implicit edges
  const layout = H.buildRadialLayout([
    { uuid: "m", relationship_type: "me" },
    { uuid: "s", relationship_type: "partner" },           // ring 1
    { uuid: "mom", relationship_type: "family_extended" }, // ring 2
  ]);
  const rels = [
    { id: 1, from_uuid: "s", to_uuid: "mom", rel_type: "family_immediate", status: "active" },
  ];
  const edges = H.buildEdgeGeometry(layout, rels);
  // Implicit: s → m + mom → m (2). Explicit: s → mom (1). Total 3.
  assert("implicit + explicit edges combined",
    edges.length === 3 && edges.filter((e) => e.implicit).length === 2
      && edges.filter((e) => !e.implicit).length === 1,
    edges);
})();

(function () {
  // Skip edges where an endpoint isn't in the layout
  const layout = H.buildRadialLayout([
    { uuid: "m", relationship_type: "me" },
    { uuid: "s", relationship_type: "partner" },
  ]);
  const rels = [
    { id: 1, from_uuid: "s", to_uuid: "ghost", rel_type: "friend", status: "active" },
  ];
  const edges = H.buildEdgeGeometry(layout, rels, { includeImplicit: false });
  assert("edges with missing endpoint are skipped",
    edges.length === 0, edges);
})();

(function () {
  // Ended edges get the muted dashed style
  const style = H.edgeStyleFor("partner", "ended");
  assert("ended status → muted style", style.dash === "1,2" && style.width === 0.6);
  const active = H.edgeStyleFor("partner", "active");
  assert("active partner → thick ice", active.width === 2.5);
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite 5 — initialsFor
 * ──────────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1minitials fallback[0m\n");

(function () {
  assert("Marcelo → MA",       H.initialsFor("Marcelo")       === "MA");
  assert("Sarah → SA",         H.initialsFor("Sarah")         === "SA");
  assert("Sarah Smith → SS",   H.initialsFor("Sarah Smith")   === "SS");
  assert("Mary Jo Smith → MS", H.initialsFor("Mary Jo Smith") === "MS");
  assert("empty → ?",          H.initialsFor("")              === "?");
  assert("null → ?",           H.initialsFor(null)            === "?");
  assert("undefined → ?",      H.initialsFor(undefined)       === "?");
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite — edge geometry: trimToBoundaries (Phase 2 P0 — Addendum 18a)
 *
 * Regression instrument for today's people-graph polish fix. Edges
 * used to draw from node-center to node-center, passing through
 * the node circles. Fix: trim each endpoint by (radius + 4px gap)
 * so the line terminates at the circle boundary.
 *
 * Verified through observable behavior — we check the produced
 * edge.x1/y1/x2/y2 coords against expected trimmed positions.
 * ──────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1medge trim to circle boundary[0m\n");

(function () {
  // Trivial 2-node graph: me at center (size 80, radius 40), one ring-2
  // friend at top (auto-laid at radius 270, size 48, radius 24).
  const layout = H.buildRadialLayout([
    { uuid: "me", relationship_type: "me" },
    { uuid: "friend", relationship_type: "friend" },
  ]);
  const edges = H.buildEdgeGeometry(layout, []);
  assert("one implicit edge present", edges.length === 1);

  const e = edges[0];
  // Endpoints in absolute coords (relative to the SVG origin at 0,0).
  // Addendum 22: ring-2 single node now sits at -π/4 (top-right)
  // due to the per-ring angular offset. center is at (0,0) radius 40,
  // friend at (270*cos(-π/4), 270*sin(-π/4)) ≈ (190.9, -190.9) radius 24.
  // Trim direction is the line's unit vector (≈ 0.707, -0.707):
  //   from-end (friend): (190.9 - 28*0.707, -190.9 + 28*0.707) ≈ (171.1, -171.1)
  //   to-end   (center): (0 + 44*0.707,     0 - 44*0.707)      ≈ (31.1,  -31.1)
  const dist = (a, b) => Math.abs(a - b) < 0.5;
  assert("edge x1 ≈ 171.1 (friend end, trimmed along diagonal)",
    dist(e.x1, 171.1), e);
  assert("edge y1 ≈ -171.1 (friend end on -π/4 diagonal)",
    dist(e.y1, -171.1), e);
  assert("edge x2 ≈ 31.1 (center end, trimmed by center radius + 4 gap)",
    dist(e.x2, 31.1), e);
  assert("edge y2 ≈ -31.1 (center end on -π/4 diagonal)",
    dist(e.y2, -31.1), e);

  // Length sanity: visible line is 198 (= 270 - 24 - 40 - 8 gaps)
  const visLen = Math.hypot(e.x2 - e.x1, e.y2 - e.y1);
  assert("trimmed line length is 198±0.5", Math.abs(visLen - 198) < 0.5,
    { visLen });

  // Direction preserved: line goes from friend (top) → center
  // (downward = larger y). y1 should be more negative than y2.
  assert("direction preserved (friend → center)", e.y1 < e.y2, e);
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite — implicit edges extended to ALL rings (Phase 2 P0 — Addendum 17)
 *
 * Today's earlier fix extended implicit edges from ring-1-only to all
 * non-center rings. Regression guard: ring-2 friend AND ring-3 neighbor
 * each get an implicit edge to center.
 * ──────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mimplicit edges across all rings[0m\n");

(function () {
  const layout = H.buildRadialLayout([
    { uuid: "me", relationship_type: "me" },
    { uuid: "p", relationship_type: "partner" },     // ring 1
    { uuid: "f", relationship_type: "friend" },       // ring 2
    { uuid: "n", relationship_type: "neighbor" },     // ring 3
  ]);
  const edges = H.buildEdgeGeometry(layout, []);
  assert("3 non-center nodes → 3 implicit edges", edges.length === 3,
    { count: edges.length });
  assert("every edge connects to center 'me'",
    edges.every((e) => e.toUuid === "me"));
  assert("every edge marked implicit",
    edges.every((e) => e.implicit === true));
  assert("partner edge present",  edges.some((e) => e.fromUuid === "p"));
  assert("friend edge present",   edges.some((e) => e.fromUuid === "f"));
  assert("neighbor edge present", edges.some((e) => e.fromUuid === "n"));
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite — Addendum 22: subrole clustering + per-ring angular offset
 *
 * Catches the "parents stack vertically across center" issue: when
 * ring-1 has 2 family_immediate nodes (mother + father subroles),
 * the OLD layout placed them at -π/2 and +π/2 (top and bottom of
 * the screen, opposite sides of center). Addendum 22 clusters them
 * tightly around a single anchor angle so they read as a "parents"
 * pair, and offsets ring-2's starting angle so the lone friend
 * doesn't stack on the same x-axis.
 * ──────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1msubrole clustering + ring offset[0m\n");

(function () {
  // ── subroleCluster mapping ──
  assert("subroleCluster: mother → parents",
    H.subroleCluster({ relationship_subrole: "mother" }) === "parents");
  assert("subroleCluster: father → parents",
    H.subroleCluster({ relationship_subrole: "father" }) === "parents");
  assert("subroleCluster: sister → siblings",
    H.subroleCluster({ relationship_subrole: "sister" }) === "siblings");
  assert("subroleCluster: son → children",
    H.subroleCluster({ relationship_subrole: "son" }) === "children");
  assert("subroleCluster: mother-in-law → in-laws",
    H.subroleCluster({ relationship_subrole: "mother-in-law" }) === "in-laws");
  assert("subroleCluster: case-insensitive",
    H.subroleCluster({ relationship_subrole: "MOTHER" }) === "parents");
  assert("subroleCluster: unknown subrole → null",
    H.subroleCluster({ relationship_subrole: "boss" }) === null);
  assert("subroleCluster: empty subrole → null",
    H.subroleCluster({ relationship_subrole: "" }) === null);
  assert("subroleCluster: no identity → null",
    H.subroleCluster(null) === null);
})();

(function () {
  // ── ringAngleOffset per ring ──
  assert("ring 1 angle offset is 0 (no rotation)",
    H.ringAngleOffset(1) === 0);
  assert("ring 2 angle offset is π/4 (45°)",
    Math.abs(H.ringAngleOffset(2) - Math.PI / 4) < 0.001);
  assert("ring 3 angle offset is π/6 (30°)",
    Math.abs(H.ringAngleOffset(3) - Math.PI / 6) < 0.001);
})();

(function () {
  // ── Parents-cluster placement (user's exact scenario) ──
  const layout = H.buildRadialLayout([
    { uuid: "me", relationship_type: "me" },
    { uuid: "amelia", relationship_type: "family_immediate", relationship_subrole: "mother" },
    { uuid: "msr",    relationship_type: "family_immediate", relationship_subrole: "father" },
    { uuid: "david",  relationship_type: "friend" },
  ]);
  const amelia = layout.find((n) => n.uuid === "amelia");
  const msr = layout.find((n) => n.uuid === "msr");
  const david = layout.find((n) => n.uuid === "david");

  // Parents should be in the SAME upper region (both y near top)
  assert("amelia y near top (clustered with father)",
    amelia.y < -100, amelia);
  assert("msr y near top (clustered with mother)",
    msr.y < -100, msr);
  // They should be horizontally separated (not on top of each other)
  assert("amelia + msr separated horizontally (cluster wedge)",
    Math.abs(amelia.x - msr.x) > 30,
    { ameliaX: amelia.x, msrX: msr.x });
  // Neither parent at x=0 (old vertical-stack would have x=0)
  assert("amelia.x is NOT 0 (no vertical stack)",
    Math.abs(amelia.x) > 1, amelia);
  assert("msr.x is NOT 0 (no vertical stack)",
    Math.abs(msr.x) > 1, msr);

  // David (ring 2, single friend) should NOT sit at x=0 (vertical
  // axis), thanks to the ring-2 angle offset
  assert("david.x is NOT 0 (ring-2 angular offset)",
    Math.abs(david.x) > 1, david);
})();

(function () {
  // ── clustersForRing accessor ──
  const layout = H.buildRadialLayout([
    { uuid: "me", relationship_type: "me" },
    { uuid: "mom", relationship_type: "family_immediate", relationship_subrole: "mother" },
    { uuid: "dad", relationship_type: "family_immediate", relationship_subrole: "father" },
    { uuid: "sis", relationship_type: "family_immediate", relationship_subrole: "sister" },
    { uuid: "bff", relationship_type: "friend" },
  ]);
  const ring1Clusters = H.clustersForRing(layout, 1);
  assert("ring 1 has 'parents' cluster",
    Array.isArray(ring1Clusters.parents) && ring1Clusters.parents.length === 2,
    ring1Clusters);
  assert("ring 1 has 'siblings' cluster (single member)",
    Array.isArray(ring1Clusters.siblings) && ring1Clusters.siblings.length === 1,
    ring1Clusters);
  const ring2Clusters = H.clustersForRing(layout, 2);
  assert("ring 2 has NO clusters (friend has no subrole)",
    Object.keys(ring2Clusters).length === 0,
    ring2Clusters);
})();

(function () {
  // ── No-cluster fallback: identities without subroles ──
  const layout = H.buildRadialLayout([
    { uuid: "me", relationship_type: "me" },
    { uuid: "a", relationship_type: "partner" },     // ring 1 no subrole
    { uuid: "b", relationship_type: "roommate" },    // ring 1 no subrole
    { uuid: "c", relationship_type: "family_immediate" }, // ring 1 no subrole
  ]);
  // 3 nodes with no clusters → 3 separate runs → distributed around full circle
  const n1 = layout.find((n) => n.uuid === "a");
  const n2 = layout.find((n) => n.uuid === "b");
  const n3 = layout.find((n) => n.uuid === "c");
  // Each should be at a unique angle (no clustering forced)
  const angles = [Math.atan2(n1.y, n1.x), Math.atan2(n2.y, n2.x), Math.atan2(n3.y, n3.x)];
  const uniqueAngles = new Set(angles.map((a) => a.toFixed(2)));
  assert("3 non-clustered ring-1 nodes → 3 distinct angles",
    uniqueAngles.size === 3, { angles });
})();

(function () {
  // ── Cluster ORDER determinism ──
  // parents comes before siblings → parents placed at startAngle,
  // siblings placed at startAngle + runStep.
  assert("CLUSTER_ORDER[0] is 'parents'",
    H.CLUSTER_ORDER[0] === "parents");
  assert("'parents' is mapped from 'mother' subrole",
    H.SUBROLE_TO_CLUSTER.mother === "parents");
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite — Addendum 23: hover-state helpers
 *
 * Catches regressions in connectedUuidsForHovered + tooltipPlacement
 * pure functions used by the people graph for hover feedback.
 * ──────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mhover state helpers (Addendum 23)[0m\n");

(function () {
  const layout = H.buildRadialLayout([
    { uuid: "me", relationship_type: "me" },
    { uuid: "amelia", relationship_type: "family_immediate", relationship_subrole: "mother" },
    { uuid: "msr", relationship_type: "family_immediate", relationship_subrole: "father" },
    { uuid: "david", relationship_type: "friend" },
  ]);
  const edges = H.buildEdgeGeometry(layout, []);

  // ── connectedUuidsForHovered ──
  const noHover = H.connectedUuidsForHovered(layout, edges, null);
  assert("connectedUuidsForHovered(null) returns empty Set",
    noHover instanceof Set && noHover.size === 0);

  const unknownHover = H.connectedUuidsForHovered(layout, edges, "nonexistent");
  assert("connectedUuidsForHovered(unknown uuid) returns empty Set",
    unknownHover.size === 0);

  // Hovering David → returns {david, me} since the only edge is david→me
  const davidSet = H.connectedUuidsForHovered(layout, edges, "david");
  assert("hovering david → contains david + me",
    davidSet.has("david") && davidSet.has("me"));
  assert("hovering david → exactly 2 entries (david + me)",
    davidSet.size === 2, [...davidSet]);

  // Hovering center → contains center + all leaf nodes
  const meSet = H.connectedUuidsForHovered(layout, edges, "me");
  assert("hovering center 'me' contains all 4 nodes",
    meSet.size === 4 &&
    meSet.has("me") && meSet.has("amelia") &&
    meSet.has("msr") && meSet.has("david"));
})();

(function () {
  // ── tooltipPlacement ──
  const VIEW_HALF = 460;
  const layout = H.buildRadialLayout([
    { uuid: "me", relationship_type: "me" },
    { uuid: "amelia", relationship_type: "family_immediate", relationship_subrole: "mother" },
    { uuid: "msr", relationship_type: "family_immediate", relationship_subrole: "father" },
    { uuid: "david", relationship_type: "friend" },
  ]);

  // David at (190.9, -190.9) is in upper-right — tooltip default
  // "right" would land off-screen; placement should flip.
  const david = layout.find((n) => n.uuid === "david");
  const placement = H.tooltipPlacement(david, VIEW_HALF, layout);
  assert("david tooltip in viewport (x + width <= viewHalf - 8)",
    placement.x + 180 <= VIEW_HALF - 8 + 0.01, placement);
  assert("david tooltip in viewport (y + height <= viewHalf - 8)",
    placement.y + 70 <= VIEW_HALF - 8 + 0.01, placement);
  assert("david tooltip in viewport (x >= -viewHalf + 8)",
    placement.x >= -VIEW_HALF + 8 - 0.01, placement);

  // Center node should always have viewport-safe placement
  const center = layout.find((n) => n.uuid === "me");
  const centerPlacement = H.tooltipPlacement(center, VIEW_HALF, layout);
  assert("center tooltip in viewport",
    centerPlacement.x + 180 <= VIEW_HALF - 8 + 0.01 &&
    centerPlacement.x >= -VIEW_HALF + 8 - 0.01,
    centerPlacement);
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite — Addendum 24: Frigate face-crop URL builder
 *
 * Catches regressions in frigateFaceCropUrls. Key edge cases:
 * - Folder names with spaces ("marcelo sr") MUST be URL-encoded
 * - Frigate-alias lookup beats display_name fallback
 * - Empty facesByPerson / null identity returns {count:0,urls:[]}
 * ──────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mFrigate face-crop URL builder (Addendum 24)[0m\n");

(function () {
  const faces = {
    "marcelo": ["marcelo-1.webp", "marcelo-2.webp"],
    "marcelo sr": ["marcelo sr-a.webp"],
    "david": ["david-1.webp", "david-2.webp", "david-3.webp"],
  };
  const base = "http://192.168.0.125:5000";

  // ── alias lookup wins over display_name ──
  const idWithAlias = {
    display_name: "Marcelo",
    aliases: [{ alias: "marcelo", kind: "frigate_name" }, { alias: "Marcelo", kind: "name" }],
  };
  let r = H.frigateFaceCropUrls(faces, idWithAlias, base);
  assert("alias lookup → count matches frigate bucket", r.count === 2, r);
  assert("alias lookup → urls use clips/faces path",
    r.urls[0] === `${base}/clips/faces/marcelo/marcelo-1.webp`, r.urls);

  // ── display_name fallback ──
  const idNoAlias = { display_name: "David", aliases: [] };
  r = H.frigateFaceCropUrls(faces, idNoAlias, base);
  assert("display_name fallback → count matches", r.count === 3, r);

  // ── space in folder name URL-encoded (AR24-1) ──
  const idMsr = {
    display_name: "Marcelo sr",
    aliases: [{ alias: "marcelo sr", kind: "frigate_name" }],
  };
  r = H.frigateFaceCropUrls(faces, idMsr, base);
  assert("space in folder URL-encoded as %20",
    r.urls[0] === `${base}/clips/faces/marcelo%20sr/marcelo%20sr-a.webp`,
    r.urls);
  assert("frigateName preserves original (unencoded)",
    r.frigateName === "marcelo sr", r);

  // ── no facesByPerson → empty ──
  r = H.frigateFaceCropUrls(null, idWithAlias, base);
  assert("null facesByPerson returns empty", r.count === 0);
  r = H.frigateFaceCropUrls({}, idWithAlias, base);
  assert("empty facesByPerson returns empty", r.count === 0);

  // ── no identity → empty ──
  r = H.frigateFaceCropUrls(faces, null, base);
  assert("null identity returns empty", r.count === 0);

  // ── no frigateUrl → empty ──
  r = H.frigateFaceCropUrls(faces, idWithAlias, null);
  assert("null frigateUrl returns empty", r.count === 0);
  r = H.frigateFaceCropUrls(faces, idWithAlias, "");
  assert("empty frigateUrl returns empty", r.count === 0);

  // ── trailing slash in base URL handled ──
  r = H.frigateFaceCropUrls(faces, idWithAlias, "http://x:5000/");
  assert("trailing slash in base trimmed",
    r.urls[0] === "http://x:5000/clips/faces/marcelo/marcelo-1.webp", r.urls);

  // ── unknown identity (no match anywhere) → empty ──
  const idUnknown = { display_name: "Ghost", aliases: [] };
  r = H.frigateFaceCropUrls(faces, idUnknown, base);
  assert("identity with no matching bucket returns empty", r.count === 0);
})();

/* ────────────────────────────────────────────────────────────────────────
 * Suite — Addendum 24 Phase 3: custom avatar URL builder
 *
 * Catches regressions in avatarUrl(). Builds the canonical HA REST path
 * to AvatarView (GET/POST/DELETE). Cache-bust suffix bumps on upload
 * success (AR24-8) so WebView2 fetches the new bytes.
 * ──────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1mcustom avatar URL builder (Addendum 24 Phase 3)[0m\n");
(() => {
  const endpoint = "http://homeassistant.local:8123";
  const uuid = "abc123";

  // ── basic happy path ──
  let u = H.avatarUrl(endpoint, uuid, null);
  assert("avatarUrl: no cacheBust → no ?v=",
    u === "http://homeassistant.local:8123/api/extended_openai_conversation/identity/abc123/avatar", u);

  u = H.avatarUrl(endpoint, uuid, 1234567890);
  assert("avatarUrl: numeric cacheBust appended as ?v=",
    u === "http://homeassistant.local:8123/api/extended_openai_conversation/identity/abc123/avatar?v=1234567890", u);

  u = H.avatarUrl(endpoint, uuid, "abc");
  assert("avatarUrl: string cacheBust appended as ?v=",
    u === "http://homeassistant.local:8123/api/extended_openai_conversation/identity/abc123/avatar?v=abc", u);

  // ── trailing slash in endpoint trimmed (defensive, mirrors frigateFaceCropUrls) ──
  u = H.avatarUrl("http://homeassistant.local:8123/", uuid, null);
  assert("avatarUrl: trailing slash in endpoint trimmed",
    u === "http://homeassistant.local:8123/api/extended_openai_conversation/identity/abc123/avatar", u);

  // ── nullish inputs → null (caller falls back to initials) ──
  assert("avatarUrl: null endpoint → null", H.avatarUrl(null, uuid, 1) === null);
  assert("avatarUrl: empty endpoint → null", H.avatarUrl("", uuid, 1) === null);
  assert("avatarUrl: null uuid → null", H.avatarUrl(endpoint, null, 1) === null);
  assert("avatarUrl: empty uuid → null", H.avatarUrl(endpoint, "", 1) === null);

  // ── uuid is URL-encoded too (defensive against future non-standard uuid shapes) ──
  u = H.avatarUrl(endpoint, "abc 123", null);
  assert("avatarUrl: space in uuid URL-encoded as %20",
    u.indexOf("/identity/abc%20123/avatar") !== -1, u);
})();

/* ────────────────────────────────────────────────────────────────────────
 * Summary
 * ──────────────────────────────────────────────────────────────── */
process.stdout.write("\n[1midentity_mutation overlay refresh contract (DOC-S13)[0m\n");
(() => {
  assert("HomePeopleOverlay accepts explicit HA client + connection props",
    /function HomePeopleOverlay\(\{\s*open,\s*onClose,\s*endpoint,\s*token,\s*client\s*=\s*null,\s*connection\s*=\s*null,\s*sim,\s*spatialMode\s*=\s*false\s*\}\)/.test(peopleSource));
  assert("HomeApp passes HA client into People overlay",
    /<window\.HomePeopleOverlay[\s\S]*client=\{haClientRef\.current\}/.test(appSource));
  assert("HomeApp passes HA connection state into People overlay",
    /<window\.HomePeopleOverlay[\s\S]*connection=\{connection\}/.test(appSource));
  assert("overlay does not subscribe to identity mutations while HA is reconnecting",
    /connection\s*!=\s*null\s*&&\s*connection\s*!==\s*"online"[\s\S]*return undefined/.test(peopleSource));
  assert("overlay prefers explicit client but keeps legacy global fallback",
    /const eventClient\s*=\s*client\s*\|\|\s*\(\(typeof window !== "undefined"\)\s*&&\s*window\.__hav_haClient\)/.test(peopleSource));
  assert("overlay subscribes to identity_mutation events",
    peopleSource.includes('"extended_openai_conversation.identity_mutation"') &&
    /eventClient\.subscribeEvents\([\s\S]*scheduleRefresh/.test(peopleSource));
  assert("identity_mutation refresh is debounced at 500 ms",
    /setTimeout\(\(\)\s*=>\s*\{\s*pending\s*=\s*null;\s*refresh\(\);\s*\},\s*500\)/.test(peopleSource));
  assert("overlay cleanup clears pending refresh and unsubscribes",
    /if \(pending\) clearTimeout\(pending\);[\s\S]*try \{ unsub && unsub\(\); \} catch \{\}/.test(peopleSource));
  assert("subscription effect re-evaluates on client and connection changes",
    /\}, \[open, sim\?\.active, connection, client, refresh\]\);/.test(peopleSource));
})();

process.stdout.write("\n");
process.stdout.write("\n[1mpeople overlay interaction contract (DOC-S83)[0m\n");
(() => {
  assert("overlay refresh button is wired to refresh loader",
    /<button[\s\S]*onClick=\{refresh\}[\s\S]*>refresh<\/button>/.test(peopleSource));
  assert("overlay close button and Escape both call onClose",
    peopleSource.includes('if (e.key === "Escape")') &&
    peopleSource.includes("onClose();") &&
    /aria-label="Close"[\s\S]*onClick=\{onClose\}/.test(peopleSource));
  assert("graph/list/queue tabs switch view without clearing selected identity",
    /\{\["graph", "list", "queue"\]\.map\(\(v\) => \([\s\S]*onClick=\{\(\) => setView\(v\)\}/.test(peopleSource) &&
    !/onClick=\{\(\) => \{\s*setView\(v\);[\s\S]*setSelectedUuid/.test(peopleSource));
  assert("graph node click opens detail panel selection",
    peopleSource.includes("onNodeClick={(id) => id && setSelectedUuid(id.uuid)}") &&
    peopleSource.includes("identityUuid={selectedUuid}"));
  assert("list row click opens same detail panel and panel close clears selection",
    peopleSource.includes("onRowClick={(id) => setSelectedUuid(id.uuid)}") &&
    peopleSource.includes("onClose={() => setSelectedUuid(null)}"));
  assert("HomeHeader accepts and attaches People button focus ref",
    /function HomeHeader\(\{[\s\S]*peopleButtonRef[\s\S]*\}\)/.test(appSource) &&
    /\{onOpenPeople && \([\s\S]*<button[\s\S]*ref=\{peopleButtonRef\}[\s\S]*onClick=\{onOpenPeople\}/.test(appSource));
  assert("HomeApp restores focus to People header button on overlay close",
    appSource.includes("const peopleButtonRef = useRef(null);") &&
    appSource.includes("const closePeopleOverlay = useCallback(() => {") &&
    appSource.includes("peopleButtonRef.current?.focus?.();") &&
    appSource.includes("peopleButtonRef={peopleButtonRef}") &&
    appSource.includes("onClose={closePeopleOverlay}"));
})();

process.stdout.write(passes + " pass · " + fails + " fail\n");
if (fails > 0) {
  process.stdout.write("\n[31mFailures:[0m\n");
  for (let i = 0; i < failures.length; i++) {
    process.stdout.write("  - " + failures[i].name + "\n");
  }
}
process.exit(fails === 0 ? 0 : 1);
