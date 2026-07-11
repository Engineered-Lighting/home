/* ============================================================================
 * home-people-helpers.js
 *
 * Pure-function helpers for the radial relationship graph (Addendum 14,
 * Slice 5). Plain JS so a Node test runner can require() this directly
 * without Babel, matching the home-metrics-lab-helpers.js pattern.
 *
 * The graph: Marcelo at center; concentric rings sized by relationship
 * tier (household-tight → friend/extended → peripheral). Auto-layout
 * places nodes evenly per ring; manual drag overrides per-node.
 *
 * These helpers are LOAD-BEARING: a bug in layout math = node overlaps
 * or off-screen primary nodes. Unit tests guard the geometry so
 * visual review doesn't have to.
 * ============================================================================ */

(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (typeof window !== "undefined") {
    window.HomePeopleHelpers = api;
  } else if (typeof global !== "undefined") {
    global.HomePeopleHelpers = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /* ── Ring assignment ──────────────────────────────────────────────
   * Per Addendum 14 (locked ring layout):
   *   Ring 1 (inner): partner / co-resident / immediate-family
   *   Ring 2 (mid):   friend / extended-family / regular guest
   *   Ring 3 (outer): neighbor / service / rare guest / peripheral
   *
   * 'me' is the center (handled separately, not a ring).
   * 'unknown' and 'do_not_identify' are NOT placed in the graph —
   * they show in the queue/list views, not the relationship web.
   * 'ignored' is filtered out at the aggregator level (US-1.33).
   *
   * Returns 0 (= center), 1, 2, 3, or null (= excluded from graph).
   * ────────────────────────────────────────────────────────────────── */
  const RING_1 = new Set(["partner", "family_immediate", "roommate"]);
  const RING_2 = new Set(["friend", "family_extended", "guest"]);
  const RING_3 = new Set(["neighbor", "service"]);

  function ringForIdentity(identity) {
    if (!identity) return null;
    const t = identity.relationship_type;
    if (t === "me") return 0;
    if (RING_1.has(t)) return 1;
    if (RING_2.has(t)) return 2;
    if (RING_3.has(t)) return 3;
    return null;  // unknown, do_not_identify → not in graph
  }

  /* ── Avatar size per ring ─────────────────────────────────────────
   * Center 80, then 60 / 48 / 36 px — visual hierarchy.
   * ────────────────────────────────────────────────────────────────── */
  const AVATAR_SIZE_BY_RING = { 0: 80, 1: 60, 2: 48, 3: 36 };

  function avatarSizeForRing(ring) {
    return AVATAR_SIZE_BY_RING[ring] || 36;
  }

  /* ── Per-ring radius (px from center) ─────────────────────────────
   * Tuned so consecutive rings clear each other at typical avatar sizes.
   * ────────────────────────────────────────────────────────────────── */
  const RING_RADIUS = { 1: 150, 2: 270, 3: 380 };

  function radiusForRing(ring) {
    return RING_RADIUS[ring] || 0;
  }

  /* ── Subrole cluster (Addendum 22) ───────────────────────────────
   * Group identities into natural clusters by relationship_subrole so
   * the radial layout can place them adjacent (e.g., parents next to
   * each other instead of at opposite ends of ring 1). Returns the
   * cluster name (string) or null for un-clustered nodes.
   * ────────────────────────────────────────────────────────────────── */
  const SUBROLE_TO_CLUSTER = {
    mother:           "parents",
    father:           "parents",
    parent:           "parents",
    mom:              "parents",
    dad:              "parents",
    "step-mother":    "parents",
    "step-father":    "parents",
    sister:           "siblings",
    brother:          "siblings",
    sibling:          "siblings",
    "sister-in-law":  "in-laws",
    "brother-in-law": "in-laws",
    "girlfriend":     "partners",
    "boyfriend":      "partners",
    "spouse":         "partners",
    "partner":        "partners",
    son:              "children",
    daughter:         "children",
    child:            "children",
    stepson:          "children",
    stepdaughter:     "children",
    stepchild:        "children",
    nephew:           "extended family",
    niece:            "extended family",
    "mother-in-law":  "in-laws",
    "father-in-law":  "in-laws",
    "son-in-law":     "in-laws",
    "daughter-in-law":"in-laws",
    friend:           "friends",
  };

  // Stable cluster ordering for sort determinism: clusters listed
  // EARLIER in this list place EARLIER in the ring (clockwise from
  // the ring's starting angle).
  const CLUSTER_ORDER = ["partners", "parents", "siblings", "children", "in-laws", "extended family", "friends"];

  function subroleCluster(identity) {
    if (!identity) return null;
    const sub = (identity.relationship_subrole || "").trim().toLowerCase();
    if (!sub) return null;
    return SUBROLE_TO_CLUSTER[sub] || null;
  }

  /** Sort-key for a node within its ring. Cluster members come first
   *  (in CLUSTER_ORDER), then non-clustered nodes (stable original
   *  order). Returns a tuple-like [primary, secondary]. */
  function _clusterSortKey(identity, originalIdx) {
    const cluster = subroleCluster(identity);
    if (cluster == null) return [9999, originalIdx];
    const i = CLUSTER_ORDER.indexOf(cluster);
    return [i >= 0 ? i : 999, originalIdx];
  }

  function identityDisplayKey(identity) {
    return String(identity?.display_name || "")
      .trim()
      .toLowerCase()
      .replace(/\s+/g, " ");
  }

  function buildKnownFamilyRelationships(identities) {
    const byName = new Map();
    for (const id of identities || []) {
      const key = identityDisplayKey(id);
      if (key) byName.set(key, id);
      for (const alias of id?.aliases || []) {
        if (!alias?.alias) continue;
        byName.set(String(alias.alias).trim().toLowerCase().replace(/\s+/g, " "), id);
      }
    }
    const edges = [];
    function add(from, to, relType) {
      const a = byName.get(from);
      const b = byName.get(to);
      if (!a || !b || a.uuid === b.uuid) return;
      edges.push({
        id: `known-${from}-${to}-${relType}`,
        from_uuid: a.uuid,
        to_uuid: b.uuid,
        rel_type: relType,
        status: "active",
        known: true,
      });
    }
    add("holly", "ben", "parent");
    add("holly", "peter", "parent");
    add("felipe", "ashley", "partner");
    add("ashley", "felipe", "partner");
    add("felipe", "aurelio", "parent");
    add("ashley", "aurelio", "parent");
    return edges;
  }

  function applyKnownFamilyPositions(layout, opts) {
    opts = opts || {};
    const radiusScale = Number.isFinite(opts.radiusScale) && opts.radiusScale > 0
      ? opts.radiusScale
      : 1;
    const avatarScale = Number.isFinite(opts.avatarScale) && opts.avatarScale > 0
      ? opts.avatarScale
      : 1;
    const byName = new Map();
    for (const node of layout || []) {
      if (!node || node.overflow || node.pinned) continue;
      const key = identityDisplayKey(node.identity);
      if (key) byName.set(key, node);
      for (const alias of node.identity?.aliases || []) {
        if (alias?.alias) {
          byName.set(String(alias.alias).trim().toLowerCase().replace(/\s+/g, " "), node);
        }
      }
    }
    function unit(angle) {
      return { x: Math.cos(angle), y: Math.sin(angle) };
    }
    function setNode(node, x, y, branch) {
      if (!node || node.pinned) return;
      node.x = x;
      node.y = y;
      node.familyBranch = branch || null;
    }
    function placePairWithChild(leftName, rightName, childName, angle, branch) {
      const left = byName.get(leftName);
      const right = byName.get(rightName);
      const child = byName.get(childName);
      if (!left || !right) return;
      const radial = unit(angle);
      const perp = { x: -radial.y, y: radial.x };
      const radius = radiusForRing(1) * radiusScale * 1.05;
      const sep = 76 * avatarScale;
      const mid = { x: radial.x * radius, y: radial.y * radius };
      setNode(left, mid.x - perp.x * sep / 2, mid.y - perp.y * sep / 2, branch);
      setNode(right, mid.x + perp.x * sep / 2, mid.y + perp.y * sep / 2, branch);
      if (child) {
        setNode(child, mid.x + radial.x * 116 * radiusScale, mid.y + radial.y * 116 * radiusScale, branch);
      }
    }
    function placeParentChildren(parentName, childNames, angle, branch) {
      const parent = byName.get(parentName);
      if (!parent) return;
      const children = childNames.map((name) => byName.get(name)).filter(Boolean);
      if (children.length === 0) return;
      const radial = unit(angle);
      const perp = { x: -radial.y, y: radial.x };
      const radius = radiusForRing(1) * radiusScale * 0.98;
      setNode(parent, radial.x * radius, radial.y * radius, branch);
      const childSpread = Math.max(54, 46 * avatarScale);
      const outward = 122 * radiusScale;
      const start = -((children.length - 1) * childSpread) / 2;
      for (let i = 0; i < children.length; i++) {
        const offset = start + i * childSpread;
        setNode(
          children[i],
          parent.x + radial.x * outward + perp.x * offset,
          parent.y + radial.y * outward + perp.y * offset,
          branch,
        );
      }
    }

    placeParentChildren("holly", ["ben", "peter"], -0.72, "holly-family");
    placePairWithChild("felipe", "ashley", "aurelio", 2.42, "felipe-ashley-family");
    return resolveLayoutCollisions(layout, opts);
  }

  function resolveLayoutCollisions(layout, opts) {
    opts = opts || {};
    if (!Array.isArray(layout) || layout.length < 2) return layout;
    const iterations = Number.isFinite(opts.collisionIterations)
      ? Math.max(0, opts.collisionIterations)
      : 80;
    const padding = Number.isFinite(opts.collisionPadding)
      ? opts.collisionPadding
      : 30;
    const centerPadding = Number.isFinite(opts.centerCollisionPadding)
      ? opts.centerCollisionPadding
      : 42;
    const nodes = layout.filter((n) => n && !n.overflow && !n.pinned);
    const center = nodes.find((n) => n.ring === 0) || null;
    function minDistance(a, b) {
      const labelPad = a.ring === 0 || b.ring === 0 ? centerPadding : padding;
      return ((a.size || 0) + (b.size || 0)) / 2 + labelPad;
    }
    for (let iter = 0; iter < iterations; iter++) {
      let moved = false;
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i];
          const b = nodes[j];
          const min = minDistance(a, b);
          let dx = b.x - a.x;
          let dy = b.y - a.y;
          let dist = Math.sqrt(dx * dx + dy * dy);
          if (dist >= min) continue;
          if (dist < 0.001) {
            const angle = ((i + 1) * 1.618 + (j + 1) * 0.733) * Math.PI;
            dx = Math.cos(angle);
            dy = Math.sin(angle);
            dist = 1;
          }
          const overlap = (min - dist) / 2;
          const ux = dx / dist;
          const uy = dy / dist;
          const aFixed = a.ring === 0;
          const bFixed = b.ring === 0;
          if (!aFixed) {
            a.x -= ux * (bFixed ? overlap * 2 : overlap);
            a.y -= uy * (bFixed ? overlap * 2 : overlap);
          }
          if (!bFixed) {
            b.x += ux * (aFixed ? overlap * 2 : overlap);
            b.y += uy * (aFixed ? overlap * 2 : overlap);
          }
          moved = true;
        }
      }
      if (center) {
        for (const n of nodes) {
          if (n.ring === 0) continue;
          const min = minDistance(center, n);
          const dist = Math.sqrt(n.x * n.x + n.y * n.y);
          if (dist > min) continue;
          const angle = dist > 0.001 ? Math.atan2(n.y, n.x) : -Math.PI / 2;
          n.x = Math.cos(angle) * min;
          n.y = Math.sin(angle) * min;
          moved = true;
        }
      }
      if (!moved) break;
    }
    return layout;
  }

  /** Per-ring starting-angle offset. Different rings rotate so their
   *  nodes don't stack at the same x-axis. Without this, a single
   *  ring-1 node and a single ring-2 node both placed at -π/2 share
   *  x=0 and edges visually overlap. */
  const RING_ANGLE_OFFSET = { 1: 0, 2: Math.PI / 4, 3: Math.PI / 6 };

  function ringAngleOffset(ring) {
    return RING_ANGLE_OFFSET[ring] || 0;
  }

  /* ── Addendum 24 helpers: Frigate face crops ────────────────── */

  /** Given the /api/faces payload (a {personName: [filename, ...]}
   *  dict) + an identity + the Frigate base URL, return the count of
   *  face crops Frigate has + the full URLs to fetch each crop.
   *
   *  Lookup order:
   *   1. identity.aliases with kind === "frigate_name" (canonical)
   *   2. display_name.toLowerCase() as fallback
   *
   *  Both the folder + filename MUST be URL-encoded — Frigate folder
   *  names like "marcelo sr" contain spaces (AR24-1). */
  function frigateFaceCropUrls(facesByPerson, identity, frigateBaseUrl) {
    if (!identity || !facesByPerson || !frigateBaseUrl) {
      return { count: 0, urls: [], frigateName: null };
    }
    const candidates = [];
    if (Array.isArray(identity.aliases)) {
      for (const a of identity.aliases) {
        if (a && a.kind === "frigate_name" && a.alias) {
          candidates.push(a.alias);
        }
      }
    }
    if (identity.display_name) {
      candidates.push(identity.display_name.toLowerCase());
    }
    const baseTrimmed = String(frigateBaseUrl).replace(/\/+$/, "");
    for (const cand of candidates) {
      const crops = facesByPerson[cand];
      if (Array.isArray(crops) && crops.length > 0) {
        const folder = encodeURIComponent(cand);
        return {
          count: crops.length,
          urls: crops.map((f) => `${baseTrimmed}/clips/faces/${folder}/${encodeURIComponent(f)}`),
          frigateName: cand,
        };
      }
    }
    return { count: 0, urls: [], frigateName: null };
  }

  /* ── Addendum 23 helpers: hover state ─────────────────────────── */

  /** Given the layout + edge list + a currently-hovered uuid, return
   *  the Set of uuids that should be visually "kept lit" — the hovered
   *  uuid itself plus every uuid connected to it via any edge. Returns
   *  an empty Set when hoveredUuid is null/unknown. Pure function. */
  function connectedUuidsForHovered(layout, edges, hoveredUuid) {
    const out = new Set();
    if (!hoveredUuid) return out;
    // Confirm the hovered uuid is actually in the layout (otherwise
    // a stale hover state shouldn't dim everything).
    const known = new Set((layout || []).map((n) => n.uuid));
    if (!known.has(hoveredUuid)) return out;
    out.add(hoveredUuid);
    for (let i = 0; i < (edges || []).length; i++) {
      const e = edges[i];
      if (e.fromUuid === hoveredUuid) out.add(e.toUuid);
      else if (e.toUuid === hoveredUuid) out.add(e.fromUuid);
    }
    return out;
  }

  /** Pick a tooltip placement for the hovered node. Returns
   *  { x, y, anchor } where anchor ∈ "left" | "right" | "above" |
   *  "below" tells the renderer how to draw the box relative to (x, y).
   *
   *  Algorithm:
   *  1. Default: right of node ("right" anchor at node.x + offset, node.y).
   *  2. If node sits in the right ~60% of the viewport, flip to "left".
   *  3. If node sits in the top ~60% of the viewport, prefer "below"
   *     (below has more headroom downward).
   *  4. neighbors (other layout nodes) are checked against the
   *     candidate placement; if the placement would land within
   *     `neighborMargin` of any neighbor, try the next quadrant.
   *  5. Final fallback: "below" the node by `node.size + 24px`.
   *
   *  viewHalf = SVG_HALF (e.g., 500). All coords are SVG-local. */
  function tooltipPlacement(node, viewHalf, neighbors, opts) {
    opts = opts || {};
    const tooltipW = opts.tooltipW || 180;
    const tooltipH = opts.tooltipH || 70;
    const neighborMargin = opts.neighborMargin || 30;
    const r = (node.size || 60) / 2;
    const gap = 12;

    const candidates = [
      { anchor: "right",  x: node.x + r + gap,           y: node.y },
      { anchor: "left",   x: node.x - r - gap - tooltipW, y: node.y },
      { anchor: "below",  x: node.x - tooltipW / 2,      y: node.y + r + gap },
      { anchor: "above",  x: node.x - tooltipW / 2,      y: node.y - r - gap - tooltipH },
    ];

    // Score each: viewport-safe + clear-of-neighbors wins.
    function inViewport(c) {
      return (
        c.x >= -viewHalf + 8 &&
        c.x + tooltipW <= viewHalf - 8 &&
        c.y >= -viewHalf + 8 &&
        c.y + tooltipH <= viewHalf - 8
      );
    }
    function clearOfNeighbors(c) {
      const cx = c.x + tooltipW / 2;
      const cy = c.y + tooltipH / 2;
      for (let i = 0; i < (neighbors || []).length; i++) {
        const n = neighbors[i];
        if (n.uuid === node.uuid) continue;
        const dx = n.x - cx;
        const dy = n.y - cy;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < (n.size / 2 + neighborMargin)) return false;
      }
      return true;
    }
    // Pass 1: viewport-safe AND clear of neighbors
    for (const c of candidates) {
      if (inViewport(c) && clearOfNeighbors(c)) return c;
    }
    // Pass 2: viewport-safe (even if it touches neighbors)
    for (const c of candidates) {
      if (inViewport(c)) return c;
    }
    // Pass 3: fall back to below + clamp to viewport
    const c = candidates[2];
    const cx = Math.max(-viewHalf + 8, Math.min(viewHalf - 8 - tooltipW, c.x));
    const cy = Math.max(-viewHalf + 8, Math.min(viewHalf - 8 - tooltipH, c.y));
    return { anchor: "below", x: cx, y: cy };
  }

  /** Group layout nodes by cluster for use by the renderer (arc
   *  labels). Returns { clusterName: [layoutNode, ...] } for each
   *  ring's nodes. Ignores nodes with no cluster + overflow chips. */
  function clustersForRing(layout, ring) {
    const out = {};
    if (!Array.isArray(layout)) return out;
    for (let i = 0; i < layout.length; i++) {
      const n = layout[i];
      if (!n || n.ring !== ring || n.overflow) continue;
      const c = subroleCluster(n.identity);
      if (!c) continue;
      if (!out[c]) out[c] = [];
      out[c].push(n);
    }
    return out;
  }

  /* ── Layout: assign x/y to each visible node ──────────────────────
   * Auto-layout places nodes evenly around their ring (starting at top,
   * going clockwise). Manual overrides (caller passes `manualPositions`
   * keyed by uuid) win over auto-layout. Returns array of:
   *   { uuid, x, y, ring, size, pinned }
   * x/y are RELATIVE to a centered origin (0,0) — caller translates.
   * ────────────────────────────────────────────────────────────────── */
  function buildRadialLayout(identities, opts) {
    opts = opts || {};
    const manualPositions = opts.manualPositions || {};
    const overflowCap = opts.overflowCap != null ? opts.overflowCap : 12;
    const radiusScale = Number.isFinite(opts.radiusScale) && opts.radiusScale > 0
      ? opts.radiusScale
      : 1;
    const avatarScale = Number.isFinite(opts.avatarScale) && opts.avatarScale > 0
      ? opts.avatarScale
      : 1;
    const clusterStepScale = Number.isFinite(opts.clusterStepScale) && opts.clusterStepScale > 0
      ? opts.clusterStepScale
      : 1;
    const mobileTextScale = Number.isFinite(opts.textScale) && opts.textScale > 0
      ? opts.textScale
      : 1;
    function scaledRadius(ring) {
      return radiusForRing(ring) * radiusScale;
    }
    function scaledAvatarSize(ring) {
      return avatarSizeForRing(ring) * avatarScale;
    }

    // Group by ring
    const byRing = { 0: [], 1: [], 2: [], 3: [] };
    for (let i = 0; i < (identities || []).length; i++) {
      const id = identities[i];
      const ring = ringForIdentity(id);
      if (ring === null) continue;
      byRing[ring].push(id);
    }
    // Center: always Marcelo if present, else first 'me' (else nothing)
    const out = [];
    if (byRing[0].length > 0) {
      const center = byRing[0][0];
      out.push({
        uuid: center.uuid,
        x: 0, y: 0,
        ring: 0,
        size: scaledAvatarSize(0),
        textScale: mobileTextScale,
        identity: center,
        pinned: false,
        overflow: false,
      });
    }
    // Rings 1, 2, 3
    for (let ring = 1; ring <= 3; ring++) {
      const nodes = byRing[ring];
      if (nodes.length === 0) continue;
      const radius = scaledRadius(ring);
      const size = scaledAvatarSize(ring);
      // Visible count: cap at overflowCap. Excess goes to "+ N more" chip.
      const visibleCount = Math.min(nodes.length, overflowCap);
      const overflowCount = nodes.length - visibleCount;
      // Addendum 22: cluster-aware sort so subrole groups (parents,
      // siblings, ...) land in adjacent angular positions.
      const indexed = nodes.map((id, originalIdx) => ({ id, originalIdx }));
      indexed.sort((a, b) => {
        const ka = _clusterSortKey(a.id, a.originalIdx);
        const kb = _clusterSortKey(b.id, b.originalIdx);
        if (ka[0] !== kb[0]) return ka[0] - kb[0];
        return ka[1] - kb[1];
      });
      const sortedNodes = indexed.map((x) => x.id);
      const visible = sortedNodes.slice(0, visibleCount);

      // Per-ring angle offset so concentric rings don't stack at the
      // same x-axis (Addendum 22). Without this, a single ring-2 node
      // sits directly above a ring-1 node and the edge from outer to
      // center passes through the inner — looks like the outer
      // "branches off" the inner.
      const startAngle = -Math.PI / 2 + ringAngleOffset(ring);

      // Addendum 22 cluster-wedge placement: group consecutive
      // members of the same cluster into a "run". Each run is placed
      // at its own anchor angle (runs distributed around the ring);
      // members WITHIN a run are spaced tightly (TIGHT_STEP) so they
      // visibly cluster together rather than landing at opposite
      // ends of the ring.
      // Step was π/8 (22.5°) — too tight: name labels overlapped for
      // long names like "Marcelo sr". π/4 (45°) → ~115 px between
      // ring-1 cluster member centers, which clears typical
      // mono-font display names.
      const TIGHT_STEP = (Math.PI / 4) * clusterStepScale;  // 45° between cluster siblings
      const runs = [];
      for (let i = 0; i < visible.length; i++) {
        const id = visible[i];
        const c = subroleCluster(id);
        const last = runs[runs.length - 1];
        if (last && last.cluster === c && c !== null) {
          last.members.push(id);
        } else {
          runs.push({ cluster: c, members: [id] });
        }
      }
      // Distribute runs around the circle. If overflow chip needed,
      // it counts as one extra "run" slot.
      const runSlots = runs.length + (overflowCount > 0 ? 1 : 0);
      const runAngleStep = (2 * Math.PI) / runSlots;

      for (let r = 0; r < runs.length; r++) {
        const run = runs[r];
        const runCenter = startAngle + r * runAngleStep;
        const memberCount = run.members.length;
        const span = (memberCount - 1) * TIGHT_STEP;
        const memberStart = runCenter - span / 2;
        for (let m = 0; m < memberCount; m++) {
          const id = run.members[m];
          let x, y, pinned = false;
          if (manualPositions[id.uuid]) {
            x = manualPositions[id.uuid].x;
            y = manualPositions[id.uuid].y;
            pinned = true;
          } else {
            const angle = memberStart + m * TIGHT_STEP;
            x = radius * Math.cos(angle);
            y = radius * Math.sin(angle);
          }
          out.push({
            uuid: id.uuid,
            x, y, ring, size,
            textScale: mobileTextScale,
            identity: id,
            pinned,
            overflow: false,
          });
        }
      }
      if (overflowCount > 0) {
        const angle = startAngle + runs.length * runAngleStep;
        out.push({
          uuid: `__overflow_ring${ring}__`,
          x: radius * Math.cos(angle),
          y: radius * Math.sin(angle),
          ring, size,
          textScale: mobileTextScale,
          identity: null,
          pinned: false,
          overflow: true,
          overflowCount,
        });
      }
    }
    return applyKnownFamilyPositions(out, { radiusScale, avatarScale });
  }

  /* ── Edge styling ─────────────────────────────────────────────────
   * Per Addendum 14 (locked relationship-tree spec): stroke width +
   * color encode relationship type at a glance.
   * ────────────────────────────────────────────────────────────────── */
  const EDGE_STYLE = {
    partner:           { stroke: "var(--hg-ice)", width: 2.5, dash: null },
    family_immediate:  { stroke: "var(--hg-fg-1)", width: 1.8, dash: null },
    family_extended:   { stroke: "var(--hg-fg-2)", width: 1.2, dash: null },
    roommate:          { stroke: "var(--hg-fg-1)", width: 1.5, dash: null },
    friend:            { stroke: "var(--hg-fg-2)", width: 1.0, dash: null },
    neighbor:          { stroke: "var(--hg-fg-3)", width: 0.8, dash: "4,2" },
    service:           { stroke: "var(--hg-fg-3)", width: 0.8, dash: "4,2" },
    guest:             { stroke: "var(--hg-fg-3)", width: 0.8, dash: "4,2" },
  };
  const EDGE_STYLE_ENDED = { stroke: "var(--hg-fg-4)", width: 0.6, dash: "1,2" };

  function edgeStyleFor(relType, status) {
    if (status === "ended") return EDGE_STYLE_ENDED;
    return EDGE_STYLE[relType] || { stroke: "var(--hg-fg-3)", width: 0.8, dash: null };
  }

  /* ── Build edge geometry ──────────────────────────────────────────
   * Given a layout (output of buildRadialLayout) + a list of
   * relationships (each {from_uuid, to_uuid, rel_type, status}),
   * compute edge segments. Returns array of:
   *   { fromUuid, toUuid, x1, y1, x2, y2, style }
   * Edges where either endpoint isn't in the layout are skipped.
   *
   * IMPLICIT EDGES: Ring 1 nodes get an implicit edge to center (the
   * radial layout's defining "you are my partner/family/roommate"
   * relationship is encoded in the ring placement; we don't need an
   * explicit relationships row for it).
   * ────────────────────────────────────────────────────────────────── */
  function buildEdgeGeometry(layout, relationships, opts) {
    opts = opts || {};
    const includeImplicit = opts.includeImplicit !== false;
    const byUuid = {};
    let centerNode = null;
    for (let i = 0; i < layout.length; i++) {
      const n = layout[i];
      if (n.overflow) continue;
      byUuid[n.uuid] = n;
      if (n.ring === 0) centerNode = n;
    }
    // Shorten an edge so its endpoints sit on the node-circle BOUNDARY
    // (plus a small visual gap) rather than at the node centers. Without
    // this, edges visually pass under/through the circles. opts.gap is
    // added to each endpoint's radius for breathing room.
    const TRIM_GAP = 4;
    function trimToBoundaries(x1, y1, x2, y2, r1, r2) {
      const dx = x2 - x1;
      const dy = y2 - y1;
      const len = Math.sqrt(dx * dx + dy * dy);
      if (len <= 0) return { x1, y1, x2, y2 };
      const ux = dx / len;
      const uy = dy / len;
      const a = (r1 || 0) + TRIM_GAP;
      const b = (r2 || 0) + TRIM_GAP;
      // Don't produce a negative-length line when endpoints overlap
      if (a + b >= len) return { x1, y1, x2, y2 };
      return {
        x1: x1 + ux * a,
        y1: y1 + uy * a,
        x2: x2 - ux * b,
        y2: y2 - uy * b,
      };
    }

    const edges = [];
    // Implicit edges: any non-center, non-overflow node → center.
    // Styling differentiates the ring (dashed for ring 3 service / neighbor),
    // so visual hierarchy is preserved. Without these lines the graph looks
    // like disconnected dots, which is what the user sees today.
    const explicitPairs = new Set();
    const explicitChildNodes = new Set();
    for (let i = 0; i < (relationships || []).length; i++) {
      const r = relationships[i];
      if (!r || !r.from_uuid || !r.to_uuid) continue;
      explicitPairs.add(`${r.from_uuid}->${r.to_uuid}`);
      explicitPairs.add(`${r.to_uuid}->${r.from_uuid}`);
      if (r.rel_type === "parent") explicitChildNodes.add(r.to_uuid);
    }

    if (includeImplicit && centerNode) {
      const cr = (centerNode.size || 0) / 2;
      for (let i = 0; i < layout.length; i++) {
        const n = layout[i];
        const hasExplicitParentAnchor = explicitChildNodes.has(n.uuid);
        if (n.ring && n.ring > 0 && !n.overflow && !hasExplicitParentAnchor) {
          const nr = (n.size || 0) / 2;
          const t = trimToBoundaries(n.x, n.y, centerNode.x, centerNode.y, nr, cr);
          edges.push({
            fromUuid: n.uuid,
            toUuid: centerNode.uuid,
            x1: t.x1, y1: t.y1,
            x2: t.x2, y2: t.y2,
            style: edgeStyleFor(n.identity?.relationship_type || "friend"),
            implicit: true,
          });
        }
      }
    }
    // Explicit edges from the relationships table
    for (let i = 0; i < (relationships || []).length; i++) {
      const r = relationships[i];
      const a = byUuid[r.from_uuid];
      const b = byUuid[r.to_uuid];
      if (!a || !b) continue;
      const ar = (a.size || 0) / 2;
      const br = (b.size || 0) / 2;
      const t = trimToBoundaries(a.x, a.y, b.x, b.y, ar, br);
      edges.push({
        fromUuid: r.from_uuid,
        toUuid: r.to_uuid,
        x1: t.x1, y1: t.y1,
        x2: t.x2, y2: t.y2,
        style: edgeStyleFor(r.rel_type, r.status),
        relType: r.rel_type,
        status: r.status,
        edgeId: r.id,
        implicit: false,
      });
    }
    return edges;
  }

  /* ── Initial-letter avatar fallback ──────────────────────────────
   * When no face crop is available, derive a short initial from the
   * display_name. Returns 1-2 uppercase characters.
   * ────────────────────────────────────────────────────────────────── */
  function initialsFor(displayName) {
    if (!displayName) return "?";
    const parts = String(displayName).trim().split(/\s+/);
    if (parts.length === 0) return "?";
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

  /* ── Addendum 24 Phase 3 — custom avatar URL builder ──────────────
   * Builds the canonical URL for an identity's custom avatar served by
   * AvatarView (HA-side REST endpoint). Returns null on bad inputs so
   * callers can fall back to initials cleanly. `cacheBust` is an opaque
   * string (typically Date.now() bumped on upload success per AR24-8)
   * appended as ?v= so WebView2 re-fetches after the user uploads.
   * ────────────────────────────────────────────────────────────────── */
  function avatarUrl(endpoint, uuid, cacheBust) {
    if (!endpoint || !uuid) return null;
    const base = String(endpoint).replace(/\/+$/, "");
    const qs = cacheBust ? `?v=${encodeURIComponent(String(cacheBust))}` : "";
    return `${base}/api/extended_openai_conversation/identity/${encodeURIComponent(uuid)}/avatar${qs}`;
  }

  return {
    RING_1, RING_2, RING_3,
    AVATAR_SIZE_BY_RING,
    RING_RADIUS,
    EDGE_STYLE,
    EDGE_STYLE_ENDED,
    ringForIdentity,
    avatarSizeForRing,
    radiusForRing,
    buildRadialLayout,
    buildEdgeGeometry,
    edgeStyleFor,
    initialsFor,
    // Addendum 22 — subrole clustering + per-ring angle offset
    subroleCluster,
    identityDisplayKey,
    buildKnownFamilyRelationships,
    applyKnownFamilyPositions,
    clustersForRing,
    ringAngleOffset,
    SUBROLE_TO_CLUSTER,
    CLUSTER_ORDER,
    // Addendum 23 — hover state helpers
    connectedUuidsForHovered,
    tooltipPlacement,
    // Addendum 24 — Frigate face-crop URL builder
    frigateFaceCropUrls,
    // Addendum 24 Phase 3 — custom avatar URL builder
    avatarUrl,
  };
});
