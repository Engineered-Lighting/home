/**
 * home-apartment-named-aims.js — APT-U04/U05 named-aim geometry, pure.
 *
 * A named aim is geometry only: it never sends a light command. Every
 * function here is a plain calculation over the Apartment frame (Z up,
 * metres) so the mapper UI and its tests share one definition of:
 *   - the planar region under a mesh pick (auto-fit to a furniture top),
 *   - the suggested name and type, and the save checks,
 *   - which aimable lights can reach a draft (the existing aim solver),
 *   - corner, centre and height handles that stay on the aim's own plane.
 * Saved targets keep the existing apartment_model target shape.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.AptNamedAims = api;
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  const CATEGORIES = Object.freeze({
    table: Object.freeze({ label: "Table", flat: true, preset: [1.2, 0.75] }),
    island: Object.freeze({ label: "Island", flat: true, preset: [1.2, 0.65] }),
    art: Object.freeze({ label: "Art", vertical: true, preset: [0.9, 0.65] }),
    custom: Object.freeze({ label: "Custom", preset: [0.6, 0.6] }),
  });
  const FLAT = 0.65;              // |normal.z| at or above this is a flat face (the editor's rule)
  const MIN_SIZE = 0.05;          // 5 cm
  const MAX_SIZE = 8;
  const LIFT_HANDLE_M = 0.32;     // the height arrow sits 0.32 m above a flat aim
  const GENERIC_NAME = /^(?:mesh|collision|room|scene|node|object|geometry|primitive|group|root|scan|untitled)[\s_\-.0-9]*$/i;

  const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const scale = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const length = (a) => Math.hypot(a[0], a[1], a[2]);
  const unit = (a, fallback = [0, 0, 1]) => { const l = length(a); return l > 1e-9 ? scale(a, 1 / l) : fallback.slice(); };
  const r3 = (v) => Math.round(v * 1000) / 1000;
  const r4 = (v) => Math.round(v * 10000) / 10000;
  const cm = (v) => Math.round(v * 100) / 100;
  const isFlat = (normal) => Math.abs(unit(normal)[2]) >= FLAT;

  /* The editor's in-plane basis: `right` and `up` span the aim's plane. A
     target's stored `up` sets its rotation; without one the editor default
     applies (world up on walls, +y on flat faces). */
  function basis(normalValue, upValue) {
    const normal = unit(normalValue);
    let up = Array.isArray(upValue) ? unit(upValue) : (Math.abs(normal[2]) < 0.75 ? [0, 0, 1] : [0, 1, 0]);
    up = unit(sub(up, scale(normal, dot(up, normal))), Math.abs(normal[2]) < 0.75 ? [0, 0, 1] : [0, 1, 0]);
    let right = cross(up, normal);
    if (length(right) < 1e-6) { up = Math.abs(normal[2]) < 0.75 ? [0, 0, 1] : [0, 1, 0]; right = cross(up, normal); }
    right = unit(right, [1, 0, 0]);
    up = unit(cross(normal, right), [0, 1, 0]);
    return { normal, right, up, flat: isFlat(normal) };
  }

  /* ---------------- planar region under a pick ---------------- */

  const regionCache = new WeakMap();
  function transformPoint(m, x, y, z) {
    if (!m) return [x, y, z];
    return [m[0] * x + m[4] * y + m[8] * z + m[12], m[1] * x + m[5] * y + m[9] * z + m[13], m[2] * x + m[6] * y + m[10] * z + m[14]];
  }
  function triangles(positions, index, matrix) {
    const count = index ? Math.floor(index.length / 3) : Math.floor(positions.length / 9);
    const vertex = (i) => transformPoint(matrix, positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]);
    const tris = new Array(count), keys = new Map();
    const key = (p) => Math.round(p[0] * 1000) + "," + Math.round(p[1] * 1000) + "," + Math.round(p[2] * 1000);
    for (let t = 0; t < count; t++) {
      const ids = index ? [index[t * 3], index[t * 3 + 1], index[t * 3 + 2]] : [t * 3, t * 3 + 1, t * 3 + 2];
      const p = ids.map(vertex), n = cross(sub(p[1], p[0]), sub(p[2], p[0])), area = length(n) / 2;
      const vertexKeys = p.map(key);
      tris[t] = { p, n: unit(n), area, keys: vertexKeys };
      for (const k of vertexKeys) { const list = keys.get(k); if (list) list.push(t); else keys.set(k, [t]); }
    }
    return { tris, keys };
  }

  /** The connected, coplanar set of triangles around `faceIndex`, in the
   * Apartment frame. `matrix` maps geometry coordinates into that frame
   * (column-major 4x4). A large mesh is indexed once per geometry. */
  function planarRegion({ positions, index = null, faceIndex, matrix = null, cacheKey = null, angleToleranceDeg = 4,
    distanceTolerance = 0.01, maxTriangles = 20000 } = {}) {
    if (!positions || !Number.isInteger(faceIndex) || faceIndex < 0) return null;
    let mesh = cacheKey && regionCache.get(cacheKey);
    if (!mesh || mesh.matrixKey !== String(matrix)) {
      mesh = { ...triangles(positions, index, matrix), matrixKey: String(matrix) };
      if (cacheKey) regionCache.set(cacheKey, mesh);
    }
    const seed = mesh.tris[faceIndex];
    if (!seed || seed.area < 1e-9) return null;
    const cos = Math.cos(angleToleranceDeg * Math.PI / 180), offset = dot(seed.n, seed.p[0]);
    const seen = new Set([faceIndex]), queue = [faceIndex], members = [];
    while (queue.length && members.length < maxTriangles) {
      const t = queue.shift(), tri = mesh.tris[t];
      members.push(tri);
      for (const k of tri.keys) for (const other of mesh.keys.get(k) || []) {
        if (seen.has(other)) continue;
        seen.add(other);
        const candidate = mesh.tris[other];
        if (candidate.area < 1e-9 || Math.abs(dot(candidate.n, seed.n)) < cos) continue;
        if (candidate.p.some((point) => Math.abs(dot(seed.n, point) - offset) > distanceTolerance)) continue;
        queue.push(other);
      }
    }
    const unique = new Map();
    for (const tri of members) tri.p.forEach((point, i) => unique.set(tri.keys[i], point));
    return { normal: seed.n, points: [...unique.values()], area: members.reduce((sum, tri) => sum + tri.area, 0),
      triangles: members.length, truncated: members.length >= maxTriangles };
  }

  /* Minimum-area rectangle around the region's points in its plane
     (convex hull + rotating calipers). Returns centre, size and the in-plane
     `up` that orients the rectangle. */
  function fitRectangle(points, normalValue) {
    const b = basis(normalValue);
    const origin = points[0];
    const flat2 = points.map((point) => { const d = sub(point, origin); return [dot(d, b.right), dot(d, b.up)]; });
    const hull = convexHull(flat2);
    if (hull.length < 3) return null;
    let best = null;
    for (let i = 0; i < hull.length; i++) {
      const a = hull[i], c = hull[(i + 1) % hull.length];
      const ex = unit([c[0] - a[0], c[1] - a[1], 0], [1, 0, 0]), ey = [-ex[1], ex[0]];
      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      for (const p of hull) {
        const x = p[0] * ex[0] + p[1] * ex[1], y = p[0] * ey[0] + p[1] * ey[1];
        minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y);
      }
      const area = (maxX - minX) * (maxY - minY);
      if (!best || area < best.area - 1e-9) best = { area, ex, ey, minX, maxX, minY, maxY };
    }
    // Prefer the axis nearest the editor's default orientation so a desk
    // aligned with the room reads width × depth without a rotation.
    let ex = best.ex, ey = best.ey, w = best.maxX - best.minX, h = best.maxY - best.minY;
    const cx = (best.minX + best.maxX) / 2, cy = (best.minY + best.maxY) / 2;
    if (Math.abs(ex[1]) > Math.abs(ex[0])) { const t = ex; ex = [ey[0], ey[1], 0]; ey = [-t[0], -t[1]]; [w, h] = [h, w]; }
    if (ex[0] < 0) { ex = [-ex[0], -ex[1], 0]; ey = [-ey[0], -ey[1]]; }
    const centre2 = [cx * best.ex[0] + cy * best.ey[0], cx * best.ex[1] + cy * best.ey[1]];
    const centre = add(origin, add(scale(b.right, centre2[0]), scale(b.up, centre2[1])));
    const upInPlane = unit(add(scale(b.right, ey[0]), scale(b.up, ey[1])), b.up);
    return { center: centre.map(r3), size: [cm(w), cm(h)], up: upInPlane.map(r4), normal: b.normal.map(r4) };
  }
  function convexHull(points) {
    const sorted = points.map((p) => [p[0], p[1]]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    if (sorted.length < 3) return sorted;
    const turn = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    const lower = [], upper = [];
    for (const p of sorted) { while (lower.length >= 2 && turn(lower[lower.length - 2], lower[lower.length - 1], p) <= 1e-12) lower.pop(); lower.push(p); }
    for (let i = sorted.length - 1; i >= 0; i--) { const p = sorted[i]; while (upper.length >= 2 && turn(upper[upper.length - 2], upper[upper.length - 1], p) <= 1e-12) upper.pop(); upper.push(p); }
    return lower.slice(0, -1).concat(upper.slice(0, -1));
  }

  /* ---------------- suggestion, checks, saved shape ---------------- */

  function titleCase(value) {
    return String(value || "").replace(/[_\-.]+/g, " ").replace(/\s+/g, " ").trim()
      .replace(/(^|\s)\S/g, (letter) => letter.toUpperCase());
  }
  function uniqueName(base, targets = [], exceptId = null) {
    const used = new Set(targets.filter((target) => target && target.id !== exceptId)
      .map((target) => String(target.name || "").trim().toLowerCase()));
    if (!used.has(base.toLowerCase())) return base;
    let i = 2;
    while (used.has(`${base} ${i}`.toLowerCase())) i++;
    return `${base} ${i}`;
  }

  /** A new draft from one mesh pick. Furniture tops and small vertical
   * planes (art) fit their own size; walls and the floor start from the
   * type's preset at the clicked point. The name comes from the surface's
   * own name when it has one, otherwise from the type, unique per home. */
  function draftFromPick({ point, normal, region = null, surfaceName = "", targets = [] } = {}) {
    if (!Array.isArray(point) || point.length !== 3) return null;
    const b = basis(normal);
    const regionArea = region ? region.area : 0;
    const floor = b.flat && point[2] < 0.25;
    let category;
    if (b.flat) category = floor ? "custom" : "table";
    else category = region && regionArea > 0.02 && regionArea <= 2.5 ? "art" : "custom";
    const fit = region && !floor && (b.flat ? regionArea > 0.02 && regionArea <= 8 : category === "art")
      ? fitRectangle(region.points, dot(region.normal, b.normal) < 0 ? scale(region.normal, -1) : region.normal) : null;
    const preset = CATEGORIES[category].preset;
    const named = surfaceName && !GENERIC_NAME.test(String(surfaceName).trim()) ? titleCase(surfaceName) : "";
    const base = named || (floor ? "Floor spot" : b.flat ? CATEGORIES[category].label : category === "art" ? "Art" : "Wall spot");
    const draft = {
      id: null, name: uniqueName(base, targets), category, shape: "surface",
      pos: (fit ? fit.center : point).map(r3),
      normal: (b.flat ? [0, 0, 1] : (fit ? fit.normal : b.normal)).map(r4),
      up: fit ? fit.up : b.up.map(r4),
      size_m: fit ? fit.size.map((v) => Math.min(MAX_SIZE, Math.max(MIN_SIZE, v))) : preset.slice(),
      surface: named || (floor ? "Floor" : b.flat ? "Flat surface" : "Wall"),
      horizontal: b.flat,
      fit: fit ? { center: fit.center, size: fit.size, up: fit.up } : null,
    };
    return draft;
  }

  function draftFromTarget(target) {
    const normal = Array.isArray(target.normal) ? target.normal : [0, 0, 1];
    return { id: target.id, name: target.name || "", category: CATEGORIES[target.category] ? target.category : "custom",
      shape: target.shape === "point" ? "point" : "surface", pos: target.pos.slice(), normal: normal.slice(),
      up: Array.isArray(target.up) ? target.up.slice() : basis(normal).up.map(r4),
      size_m: Array.isArray(target.size_m) ? target.size_m.slice() : CATEGORIES[target.category]?.preset?.slice() || [0.6, 0.6],
      surface: target.surface_name || null, horizontal: isFlat(normal), fit: null };
  }

  /** Save checks: a non-empty name unique in this home, and a type that
   * agrees with the surface (tables and islands flat, art vertical). */
  function validate(draft, targets = []) {
    const name = String(draft?.name || "").trim();
    const nameError = !name ? "Name this aim."
      : targets.some((target) => target && target.id !== draft.id && String(target.name || "").trim().toLowerCase() === name.toLowerCase())
        ? `Another named aim is already called “${name}”.` : "";
    const category = CATEGORIES[draft?.category];
    const typeError = !category ? "Choose a type."
      : category.vertical && draft.horizontal ? "Art goes on a wall. Pick a vertical surface or change the type."
        : category.flat && !draft.horizontal ? "Tables and islands are flat. Pick a top surface or change the type." : "";
    return { ok: !nameError && !typeError, nameError, typeError };
  }

  function toTarget(draft, { id, roomId = null, now }) {
    const target = {
      id, name: String(draft.name).trim(), category: draft.category, shape: draft.shape,
      pos: draft.pos.map(r3), normal: draft.normal.map(r4), up: draft.up.map(r4),
      room_id: roomId, source: "manual", confidence: 1, updated_at: now,
    };
    if (draft.shape === "surface") target.size_m = draft.size_m.map(cm);
    return target;
  }

  /* ---------------- reach ---------------- */

  /** Which aimable lights can reach the draft, and why the others can't.
   * Uses the existing aim solver (pan/tilt limits) and, when supplied, a
   * beam trace for line of sight. Nothing is sent to any light. */
  function reach({ draft, devices = [], aiming, trace = null } = {}) {
    if (!draft || !Array.isArray(draft.pos) || !aiming || typeof aiming.solveAim !== "function") return [];
    return devices.filter((device) => device && device.fixture_kind === aiming.ENGINEERED_KIND).map((device) => {
      const name = device.name || device.id;
      if (!Array.isArray(device.pos) || device.pos.length !== 3) return { id: device.id, name, ok: false, reason: "no_position" };
      const solved = aiming.solveAim(device.pos, draft.pos, { limits: device.gimbal?.limits,
        horizontal_world_reference: device.gimbal?.visualization_calibration?.horizontal_world_reference });
      if (!solved.ok) return { id: device.id, name, ok: false, reason: "no_position" };
      if (solved.violations.length) return { id: device.id, name, ok: false, reason: "out_of_reach", violations: solved.violations };
      const blocked = typeof trace === "function" ? trace(device, solved) : false;
      if (blocked) return { id: device.id, name, ok: false, reason: "blocked" };
      return { id: device.id, name, ok: true, reason: null, pan_deg: solved.pan_deg, tilt_deg: solved.tilt_deg,
        direction: unit(solved.delta), distance_m: solved.distance_m };
    });
  }
  const REACH_REASON = Object.freeze({ out_of_reach: "is out of reach", blocked: "is blocked", no_position: "has no position" });
  function reachSentence(rows) {
    const ok = rows.filter((row) => row.ok), no = rows.filter((row) => !row.ok);
    if (!rows.length) return "No light in this home can be aimed.";
    return (ok.length ? `${ok.map((row) => row.name).join(", ")} can reach it.` : "No light can reach it.")
      + (no.length ? " " + no.map((row) => `${row.name} ${REACH_REASON[row.reason] || "can't reach it"}`).join(", ") + "." : "");
  }

  /* ---------------- handles on the aim's own plane ---------------- */

  const CORNERS = Object.freeze({ c0: [1, 1], c1: [-1, 1], c2: [-1, -1], c3: [1, -1] });

  function corners(draft) {
    const b = basis(draft.normal, draft.up), size = draft.size_m || [0.5, 0.5];
    const lift = scale(b.normal, 0.012);
    return Object.fromEntries(Object.entries(CORNERS).map(([key, [su, sw]]) =>
      [key, add(add(add(draft.pos, lift), scale(b.right, su * size[0] / 2)), scale(b.up, sw * size[1] / 2))]));
  }
  function handlePoints(draft) {
    const b = basis(draft.normal, draft.up);
    return { ...(draft.shape === "surface" ? corners(draft) : {}), mid: add(draft.pos, scale(b.normal, 0.012)),
      ...(b.flat ? { lift: add(draft.pos, [0, 0, LIFT_HANDLE_M]), floor: [draft.pos[0], draft.pos[1], 0] } : {}) };
  }

  function rayPlane(origin, direction, planePoint, planeNormal) {
    const denom = dot(direction, planeNormal);
    if (Math.abs(denom) < 1e-9) return null;
    const t = dot(sub(planePoint, origin), planeNormal) / denom;
    return t > 0 ? add(origin, scale(direction, t)) : null;
  }

  /** The plane a handle drag moves on. The lift arrow moves on the vertical
   * plane facing the camera through the aim; everything else on the aim's
   * own plane. */
  function dragPlane(draft, key, viewDirection) {
    const b = basis(draft.normal, draft.up);
    if (key !== "lift") return { point: draft.pos, normal: b.normal };
    const facing = [viewDirection[0], viewDirection[1], 0];
    return { point: draft.pos, normal: length(facing) < 1e-6 ? [1, 0, 0] : unit(facing) };
  }

  /** One handle drag step, from the drag's start state `d0`, its first plane
   * hit `start` and the current plane hit `point`. Corners resize with the
   * opposite corner fixed (minimum 5 cm, 1 cm rounding); the centre moves
   * within the plane; the lift arrow changes height only, within the room. */
  function dragHandle(d0, key, start, point, { roomTop = 2.4 } = {}) {
    if (!start || !point) return null;
    const b = basis(d0.normal, d0.up);
    if (key === "lift") {
      if (!b.flat) return null;
      return { pos: [d0.pos[0], d0.pos[1], r3(Math.min(roomTop, Math.max(0, d0.pos[2] + (point[2] - start[2]))))] };
    }
    const delta = sub(point, start), inPlane = sub(delta, scale(b.normal, dot(delta, b.normal)));
    if (key === "mid") return { pos: add(d0.pos, inPlane).map(r3) };
    const signs = CORNERS[key];
    if (!signs) return null;
    const [su, sw] = signs, size = d0.size_m || [0.5, 0.5];
    const opposite = add(add(d0.pos, scale(b.right, -su * size[0] / 2)), scale(b.up, -sw * size[1] / 2));
    const startCorner = add(add(d0.pos, scale(b.right, su * size[0] / 2)), scale(b.up, sw * size[1] / 2));
    const corner = add(startCorner, inPlane), v = sub(corner, opposite);
    const sign = (x, fallback) => (Math.abs(x) < 1e-6 ? fallback : Math.sign(x));
    let du = dot(v, b.right), dw = dot(v, b.up);
    du = sign(du, su) * Math.min(MAX_SIZE, Math.max(MIN_SIZE, cm(Math.abs(du))));
    dw = sign(dw, sw) * Math.min(MAX_SIZE, Math.max(MIN_SIZE, cm(Math.abs(dw))));
    const center = add(add(opposite, scale(b.right, du / 2)), scale(b.up, dw / 2));
    return { pos: center.map(r3), size_m: [Math.abs(du), Math.abs(dw)] };
  }

  /** 5 cm nudges along the aim's own plane: left/right along its width,
   * and on walls up/down, on flat surfaces away/toward along its depth. */
  function nudge(draft, dx, dy, step = 0.05) {
    const b = basis(draft.normal, draft.up);
    return { pos: add(add(draft.pos, scale(b.right, dx * step)), scale(b.up, dy * step)).map(r3) };
  }

  /** Exact numeric entry for one axis, kept on the aim's own plane: the
   * component along the surface normal is projected back, so a wall aim can't
   * leave its wall and a table aim's x/y never change its height. */
  function exactPosition(draft, axis, value) {
    const v = Number(value);
    if (!Number.isFinite(v) || ![0, 1, 2].includes(axis)) return null;
    const b = basis(draft.normal, draft.up), next = draft.pos.slice();
    next[axis] = v;
    if (b.flat) return { pos: next.map(r3) };
    const offset = dot(sub(next, draft.pos), b.normal);
    return { pos: sub(next, scale(b.normal, offset)).map(r3) };
  }

  /** In-plane rotation of a flat aim in 15° steps (the stored `up`). */
  function rotateFlat(draft, degrees) {
    const b = basis(draft.normal, draft.up), a = degrees * Math.PI / 180;
    if (!b.flat) return null;
    const up = unit(add(scale(b.up, Math.cos(a)), scale(b.right, -Math.sin(a))));
    return { up: up.map(r4) };
  }
  function flatRotation(draft) {
    const b = basis(draft.normal, draft.up);
    const reference = basis(draft.normal);
    const angle = Math.atan2(dot(cross(reference.up, b.up), b.normal), dot(reference.up, b.up)) * 180 / Math.PI;
    return ((Math.round(angle) % 360) + 360) % 360;
  }

  return Object.freeze({
    CATEGORIES, FLAT, MIN_SIZE, LIFT_HANDLE_M, isFlat, basis, planarRegion, fitRectangle, draftFromPick, draftFromTarget,
    validate, toTarget, uniqueName, reach, reachSentence, corners, handlePoints, rayPlane, dragPlane, dragHandle, nudge,
    exactPosition, rotateFlat, flatRotation,
  });
});
