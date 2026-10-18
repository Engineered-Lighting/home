/* Virtual den model: engineered_den_fixture_v1 defaults and the provisional den-model-1.
 *
 * Pure UMD module (see docs/VIRTUAL-DEN-CONTRACTS.md). No DOM, no network, no
 * wall clock. Positions use the Apartment frame z_up_metric_floor0 (metres,
 * Z up, floor at z = 0). Radial azimuths are world angles atan2(y, x) in degrees.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.HomeDenFixtures = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const VERSION = "home-den-fixtures-1";
  const FIXTURE_KIND = "engineered_den_fixture_v1";
  const MODEL_SCHEMA = "den-model-1";
  const FRAME = "z_up_metric_floor0";
  const FIELD_STATUSES = ["measured", "datasheet", "assumed"];
  const ELEMENT_STATUSES = ["estimated", "measured", "planned", "assumed", "datasheet", "scan"];
  const TARGET_STATUSES = ["estimated", "measured", "planned"];
  const CATEGORIES = ["desk", "reading", "sofa", "table", "printer", "bar", "art", "floor", "custom"];

  /** Every optical or mechanical field whose provenance must be stated in fixture.status. */
  const STATUS_FIELDS = [
    "mount.pos",
    "radials.zones", "radials.beam_half_angle_deg", "radials.elevation_tilt_deg",
    "radials.max_flux_lm", "radials.cct_range_k",
    "downlight_ring.beam_deg", "downlight_ring.max_flux_lm", "downlight_ring.cct_range_k",
    "spot.fwhm_deg", "spot.profile", "spot.max_flux_lm", "spot.cct_range_k",
    "gimbal.pan_min_deg", "gimbal.pan_max_deg", "gimbal.tilt_min_deg", "gimbal.tilt_max_deg",
    "gimbal.max_vel_deg_s", "gimbal.max_acc_deg_s2", "gimbal.quiet_vel_deg_s",
    "gimbal.settle_s", "gimbal.backlash_deg", "gimbal.horizontal_world_reference",
  ];

  const clone = (v) => JSON.parse(JSON.stringify(v));
  const isObj = (v) => v && typeof v === "object" && !Array.isArray(v);
  const finite = (v) => typeof v === "number" && Number.isFinite(v);
  const isVec = (v, n) => Array.isArray(v) && v.length === n && v.every(finite);

  /** Deep merge: plain objects merge, arrays and scalars from `over` replace. */
  function merge(base, over) {
    if (!isObj(base) || !isObj(over)) return over === undefined ? clone(base) : clone(over);
    const out = clone(base);
    for (const [k, v] of Object.entries(over)) out[k] = isObj(v) && isObj(out[k]) ? merge(out[k], v) : clone(v);
    return out;
  }

  function getPath(obj, dotted) {
    return dotted.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
  }

  /** Radial zone world azimuth, matching HomeApartmentAiming.radialZoneAngle, normalised to [0, 360). */
  function zoneAzimuth(number, cal) {
    const anchorZone = +cal?.anchor_zone || 1;
    const anchorAngle = +cal?.anchor_world_angle_deg || 0;
    const fine = +cal?.fine_adjust_deg || 0;
    const order = cal?.order === "counterclockwise" ? -1 : 1;
    const a = anchorAngle + fine + order * (number - anchorZone) * 60;
    return ((a % 360) + 360) % 360;
  }

  /** Default engineered_den_fixture_v1; every field status is "assumed" except the Carclo 10511 FWHM. */
  function defaultFixture(id = "den_fixture", pos = [0, 0, 2.55]) {
    const cal = { anchor_zone: 1, anchor_world_angle_deg: 0, order: "clockwise", fine_adjust_deg: 0 };
    const status = {};
    for (const f of STATUS_FIELDS) status[f] = "assumed";
    status["spot.fwhm_deg"] = "datasheet";
    return {
      id, kind: FIXTURE_KIND, name: id,
      mount: { pos: [...pos], status: "assumed" },
      radials: {
        count: 6,
        orientation_calibration: cal,
        zones: Array.from({ length: 6 }, (_, i) => ({ number: i + 1, azimuth_deg: zoneAzimuth(i + 1, cal) })),
        beam_half_angle_deg: 30, elevation_tilt_deg: 0, max_flux_lm: 60, cct_range_k: [2200, 5000],
      },
      downlight_ring: { beam_deg: 60, max_flux_lm: 400, cct_range_k: [2200, 5000] },
      spot: { fwhm_deg: 20, profile: "gaussian", max_flux_lm: 250, cct_range_k: [2700, 5000],
        optic: "Carclo 10511" },
      gimbal: {
        pan_min_deg: -175, pan_max_deg: 175, tilt_min_deg: -5, tilt_max_deg: 88,
        max_vel_deg_s: 90, max_acc_deg_s2: 180, quiet_vel_deg_s: 15,
        settle_s: 0.3, backlash_deg: 0.5, horizontal_world_reference: [1, 0, 0],
      },
      status,
    };
  }

  /** Fill defaults and force exactly 6 radial zones (azimuths from the orientation calibration). */
  function normalizeFixture(input) {
    const base = defaultFixture(input?.id, input?.mount?.pos);
    const out = merge(base, input || {});
    out.kind = FIXTURE_KIND;
    out.radials.count = 6;
    const given = new Map((input?.radials?.zones || []).map((z) => [+z.number, z]));
    out.radials.zones = Array.from({ length: 6 }, (_, i) => ({
      number: i + 1,
      azimuth_deg: zoneAzimuth(i + 1, out.radials.orientation_calibration),
      ...(given.get(i + 1) || {}),
    }));
    out.status = { ...base.status, ...(input?.status || {}) };
    return out;
  }

  /** Validate one fixture as given (no defaults filled). Returns a list of error strings. */
  function validateFixture(fx) {
    const errors = [];
    const where = `fixture ${fx?.id ?? "?"}`;
    if (!fx || typeof fx.id !== "string") errors.push(`${where}: id required`);
    if (fx?.kind !== FIXTURE_KIND) errors.push(`${where}: kind must be ${FIXTURE_KIND}`);
    if (!isVec(fx?.mount?.pos, 3)) errors.push(`${where}: mount.pos must be [x,y,z]`);
    if (!ELEMENT_STATUSES.includes(fx?.mount?.status)) errors.push(`${where}: mount.status required`);
    const zones = fx?.radials?.zones;
    if (fx?.radials?.count !== 6 || !Array.isArray(zones) || zones.length !== 6) {
      errors.push(`${where}: exactly 6 radials required`);
    } else {
      const nums = zones.map((z) => +z.number).sort((a, b) => a - b).join(",");
      if (nums !== "1,2,3,4,5,6") errors.push(`${where}: radial zone numbers must be 1..6`);
      if (!zones.every((z) => finite(z.azimuth_deg))) errors.push(`${where}: radial azimuth_deg required`);
    }
    for (const part of ["downlight_ring", "spot", "gimbal"]) {
      if (!isObj(fx?.[part])) errors.push(`${where}: ${part} required`);
    }
    const g = fx?.gimbal || {};
    if (!(g.pan_min_deg < g.pan_max_deg) || !(g.tilt_min_deg < g.tilt_max_deg)) errors.push(`${where}: gimbal limits invalid`);
    if (!(g.max_vel_deg_s > 0) || !(g.max_acc_deg_s2 > 0) || !(g.quiet_vel_deg_s > 0)) errors.push(`${where}: gimbal speeds must be > 0`);
    if (!(fx?.spot?.fwhm_deg > 0 && fx.spot.fwhm_deg < 180)) errors.push(`${where}: spot.fwhm_deg invalid`);
    for (const f of STATUS_FIELDS) {
      if (getPath(fx, f) === undefined) errors.push(`${where}: ${f} missing`);
      if (!FIELD_STATUSES.includes(fx?.status?.[f])) errors.push(`${where}: status["${f}"] must be measured|datasheet|assumed`);
    }
    return errors;
  }

  /** Yaw-only quaternion [w,x,y,z] about +Z (camera optical axis = body +X after rotation). */
  function yawQuat(fromXY, toXY) {
    const yaw = Math.atan2(toXY[1] - fromXY[1], toXY[0] - fromXY[0]);
    return [Math.cos(yaw / 2), 0, 0, Math.sin(yaw / 2)];
  }

  function rect(x0, y0, x1, y1) { return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]; }

  /** Provisional den-model-1: estimated 4.2 x 3.6 x 2.6 m room until the owner's tape session. */
  function provisionalDenModel() {
    const X = 4.2, Y = 3.6, Z = 2.6, IN = 0.35, MZ = 2.55;
    const corners = [
      ["den_fx_sw", "SW corner", [IN, IN, MZ]],
      ["den_fx_se", "SE corner", [X - IN, IN, MZ]],
      ["den_fx_ne", "NE corner", [X - IN, Y - IN, MZ]],
      ["den_fx_nw", "NW corner", [IN, Y - IN, MZ]],
    ];
    const fixtures = corners.map(([id, name, pos]) => {
      const fx = defaultFixture(id, pos);
      fx.name = name;
      // Pan 0 faces the room centre so every den target sits well inside the pan envelope.
      fx.gimbal.horizontal_world_reference = [X / 2 - pos[0], Y / 2 - pos[1], 0];
      return fx;
    });
    const flat = (id, name, category, pos, size, up, extra = {}) => ({
      id, name, category, shape: "surface", pos, normal: [0, 0, 1], up, size_m: size,
      status: "estimated", task: true, ...extra,
    });
    const art = (id, name, pos, normal) => ({
      id, name, category: "art", shape: "surface", pos, normal, up: [0, 0, 1], size_m: [0.6, 0.45],
      status: "estimated", task: false,
    });
    const targets = [
      flat("desk_work_area", "Desk work area", "desk", [1.3, 3.25, 0.74], [1.2, 0.6], [0, 1, 0]),
      flat("reading_chair_book", "Reading chair book region", "reading", [0.85, 1.1, 0.65], [0.35, 0.25], [0, 1, 0]),
      flat("sofa_seat", "Sofa seat", "sofa", [3.7, 1.8, 0.45], [2.0, 0.6], [1, 0, 0]),
      flat("coffee_table_top", "Coffee table (planned)", "table", [2.6, 1.8, 0.42], [1.0, 0.55], [1, 0, 0], { status: "planned" }),
      flat("printer_bed", "3D printer bed", "printer", [3.8, 0.45, 0.9], [0.3, 0.3], [0, 1, 0]),
      flat("bar_top", "Bar top", "bar", [3.8, 3.2, 1.05], [0.5, 0.4], [0, 1, 0]),
      art("art_1", "Artwork 1 (west wall)", [0.01, 2.2, 1.5], [1, 0, 0]),
      art("art_2", "Artwork 2 (south wall)", [1.0, 0.01, 1.5], [0, 1, 0]),
      art("art_3", "Artwork 3 (east wall)", [X - 0.01, 1.8, 1.55], [-1, 0, 0]),
      flat("floor_center", "Floor centre", "floor", [2.1, 1.8, 0.0], [1.0, 1.0], [0, 1, 0]),
    ];
    const zone = (id, name, poly, anchors) => ({ id, name, floor_polygon: poly, anchor_target_ids: anchors, status: "estimated" });
    const zones = [
      zone("desk", "Desk", rect(0.5, 2.4, 2.1, Y), ["desk_work_area"]),
      zone("reading_chair", "Reading chair", rect(0.3, 0.4, 1.4, 1.5), ["reading_chair_book"]),
      zone("sofa", "Sofa", rect(3.2, 1.1, X, 2.5), ["sofa_seat"]),
      zone("coffee_table", "Coffee table", rect(2.0, 1.4, 3.2, 2.2), ["coffee_table_top"]),
      zone("printer_corner", "Printer corner", rect(3.3, 0, X, 0.9), ["printer_bed"]),
      zone("bar_corner", "Bar corner", rect(3.3, 2.8, X, Y), ["bar_top"]),
      zone("doorway", "Doorway", rect(1.8, 0, 2.7, 0.8), []),
      zone("floor", "Den floor", rect(0, 0, X, Y), ["floor_center"]),
    ];
    const eye = (id, kind, min, max) => ({ id, kind, min, max, status: "estimated" });
    const eye_boxes = [
      eye("eyes_desk_chair", "seat", [1.05, 2.45, 0.95], [1.55, 2.95, 1.35]),
      eye("eyes_reading_chair", "seat", [0.6, 0.55, 0.95], [1.0, 0.95, 1.35]),
      eye("eyes_sofa", "seat", [3.45, 1.2, 0.95], [4.05, 2.4, 1.35]),
      eye("eyes_walkway", "walkway", [1.9, 0.0, 1.4], [2.6, 1.2, 1.9]),
    ];
    const deskChair = [1.3, 2.7];
    const camPos = [2.1, 3.5, 1.2];
    return {
      schema: MODEL_SCHEMA, revision: "provisional-r1", site_id: "victoria", frame: FRAME,
      geometry_source: "estimated", built_with: VERSION,
      room: {
        size_m: [X, Y, Z], status: "estimated",
        walls: [
          { id: "south", p0: [0, 0], p1: [X, 0], status: "estimated" },
          { id: "east", p0: [X, 0], p1: [X, Y], status: "estimated" },
          { id: "north", p0: [X, Y], p1: [0, Y], status: "estimated" },
          { id: "west", p0: [0, Y], p1: [0, 0], status: "estimated" },
        ],
        openings: [
          { id: "door", wall: "south", kind: "door", x0: 1.8, x1: 2.7, z0: 0, z1: 2.05, status: "estimated" },
          { id: "window", wall: "north", kind: "window", x0: 2.4, x1: 3.4, z0: 0.9, z1: 2.1, status: "estimated" },
        ],
      },
      camera: { id: "den-oak", pos: camPos, q_wxyz: yawQuat(camPos, deskChair), hfov_deg: 80, vfov_deg: 55,
        max_range_m: 6, status: "assumed" },
      fixtures, targets, zones, eye_boxes,
    };
  }

  /** Validate a den-model-1. Returns { ok, errors }. */
  function validateModel(model) {
    const errors = [];
    if (model?.schema !== MODEL_SCHEMA) errors.push(`schema must be ${MODEL_SCHEMA}`);
    if (model?.frame !== FRAME) errors.push(`frame must be ${FRAME}`);
    if (!["estimated", "measured", "scan"].includes(model?.geometry_source)) errors.push("geometry_source invalid");
    if (!isVec(model?.room?.size_m, 3) || !model.room.size_m.every((v) => v > 0)) errors.push("room.size_m invalid");
    if (!Array.isArray(model?.room?.walls) || model.room.walls.length < 3) errors.push("room.walls required");
    const cam = model?.camera;
    if (!cam || !isVec(cam.pos, 3) || !isVec(cam.q_wxyz, 4)) errors.push("camera pos/q_wxyz required");
    if (!ELEMENT_STATUSES.includes(cam?.status)) errors.push("camera.status required");
    const fixtures = model?.fixtures || [];
    if (fixtures.length !== 4) errors.push("exactly 4 fixtures required");
    for (const fx of fixtures) errors.push(...validateFixture(fx));
    const ids = new Set();
    const dup = (id, what) => { if (ids.has(id)) errors.push(`duplicate id ${id} (${what})`); ids.add(id); };
    for (const fx of fixtures) dup(fx.id, "fixture");
    for (const t of model?.targets || []) {
      dup(t.id, "target");
      if (!CATEGORIES.includes(t.category)) errors.push(`target ${t.id}: category invalid`);
      if (!isVec(t.pos, 3) || !isVec(t.normal, 3) || !isVec(t.up, 3) || !isVec(t.size_m, 2)) errors.push(`target ${t.id}: geometry invalid`);
      if (!TARGET_STATUSES.includes(t.status)) errors.push(`target ${t.id}: status required`);
      if (typeof t.task !== "boolean") errors.push(`target ${t.id}: task must be boolean`);
    }
    const targetIds = new Set((model?.targets || []).map((t) => t.id));
    for (const z of model?.zones || []) {
      dup(z.id, "zone");
      if (!Array.isArray(z.floor_polygon) || z.floor_polygon.length < 3) errors.push(`zone ${z.id}: polygon needs >= 3 points`);
      for (const a of z.anchor_target_ids || []) if (!targetIds.has(a)) errors.push(`zone ${z.id}: unknown anchor ${a}`);
      if (!ELEMENT_STATUSES.includes(z.status)) errors.push(`zone ${z.id}: status required`);
    }
    for (const e of model?.eye_boxes || []) {
      dup(e.id, "eye_box");
      if (!["seat", "walkway"].includes(e.kind)) errors.push(`eye_box ${e.id}: kind invalid`);
      if (!isVec(e.min, 3) || !isVec(e.max, 3) || e.min.some((v, i) => v >= e.max[i])) errors.push(`eye_box ${e.id}: min/max invalid`);
      if (!ELEMENT_STATUSES.includes(e.status)) errors.push(`eye_box ${e.id}: status required`);
    }
    return { ok: errors.length === 0, errors };
  }

  function pointInPolygon(p, poly) {
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const [xi, yi] = poly[i], [xj, yj] = poly[j];
      if ((yi > p[1]) !== (yj > p[1]) && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }

  /**
   * Resolve a target or zone id. Returns { kind: "target"|"zone", target?, zone?, pos } or null.
   * A zone resolves to its first anchor target's position, else its polygon centroid at floor height.
   */
  function resolveId(model, id) {
    const target = (model?.targets || []).find((t) => t.id === id);
    if (target) return { kind: "target", target, zone: zoneOfPoint(model, target.pos), pos: [...target.pos] };
    const zone = (model?.zones || []).find((z) => z.id === id);
    if (!zone) return null;
    const anchor = (model.targets || []).find((t) => t.id === (zone.anchor_target_ids || [])[0]);
    const poly = zone.floor_polygon;
    const c = poly.reduce((a, p) => [a[0] + p[0] / poly.length, a[1] + p[1] / poly.length], [0, 0]);
    return { kind: "zone", zone, target: anchor || null, pos: anchor ? [...anchor.pos] : [c[0], c[1], 0] };
  }

  /** Smallest-area zone whose floor polygon contains the point (plan view). */
  function zoneOfPoint(model, p) {
    let best = null, bestArea = Infinity;
    for (const z of model?.zones || []) {
      if (!pointInPolygon(p, z.floor_polygon)) continue;
      const poly = z.floor_polygon;
      let area = 0;
      for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) area += (poly[j][0] + poly[i][0]) * (poly[j][1] - poly[i][1]);
      area = Math.abs(area) / 2;
      if (area < bestArea) { best = z; bestArea = area; }
    }
    return best;
  }

  /** World azimuths of the 6 radial zones of a fixture: [{ number, azimuth_deg }]. */
  function radialAzimuths(fixture) {
    const cal = fixture?.radials?.orientation_calibration;
    const byNumber = new Map((fixture?.radials?.zones || []).map((z) => [+z.number, z]));
    return Array.from({ length: 6 }, (_, i) => {
      const given = byNumber.get(i + 1);
      const az = cal ? zoneAzimuth(i + 1, cal) : (finite(given?.azimuth_deg) ? given.azimuth_deg : zoneAzimuth(i + 1, null));
      return { number: i + 1, azimuth_deg: az };
    });
  }

  return Object.freeze({
    VERSION, FIXTURE_KIND, MODEL_SCHEMA, FRAME, STATUS_FIELDS, FIELD_STATUSES,
    defaultFixture, normalizeFixture, validateFixture, validateModel, provisionalDenModel,
    resolveId, zoneOfPoint, pointInPolygon, radialAzimuths,
  });
});
