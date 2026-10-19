/* The owner's one-time sofa mark for the lighting brain. It reuses the saved
 * desk target's selector and sealing (four owner-picked scan corners on the
 * registered surface), then records the sofa zone polygon and its seat point.
 * The record stays private beside the desk target; the brain reads only the
 * private lighting-brain-geometry-1 export, never this browser. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.HomeLightingSofaTarget = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const SCHEMA = 'home-registered-sofa-target-1', GEOMETRY_SCHEMA = 'lighting-brain-geometry-1';
  const DESK_SCHEMAS = ['home-registered-desk-target-1', 'home-registered-desk-target-2'];
  const KEYS = ['schema', 'version', 'scope', 'frame', 'unit', 'binding', 'equipment_ref', 'zone', 'seat', 'selection',
    'confirmed', 'truth_role', 'qualification', 'physical_commands', 'digest'];
  const object = v => v !== null && typeof v === 'object' && !Array.isArray(v);
  const copy = v => JSON.parse(JSON.stringify(v));
  const need = (condition, reason) => { if (!condition) throw TypeError(reason); };
  const sha = v => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
  function equal(a, b) {
    if (a === b) return true;
    if (!object(a) && !Array.isArray(a) || !object(b) && !Array.isArray(b) || Array.isArray(a) !== Array.isArray(b)) return false;
    const keys = Object.keys(a);
    return keys.length === Object.keys(b).length && keys.every(key => Object.hasOwn(b, key) && equal(a[key], b[key]));
  }

  // A sealed desk-shaped record carries the checked polygon; the sofa keeps it
  // as its zone and uses the polygon centre as the seat point (approximate).
  function fromDeskSeal(desk, digestFn) {
    need(object(desk) && DESK_SCHEMAS.includes(desk.schema) && desk.confirmed === true && sha(desk.digest), 'sofa_selection_seal_required');
    need(typeof digestFn === 'function', 'sofa_digest_required');
    const t = desk.target;
    const body = { schema: SCHEMA, version: 1, scope: copy(desk.scope), frame: desk.frame, unit: desk.unit,
      binding: copy(desk.binding), equipment_ref: copy(desk.equipment_ref),
      zone: { kind: 'polygon', vertices_m: copy(t.vertices_m), centre_m: copy(t.centre_m), normal_unit: copy(t.normal_unit),
        planarity: copy(t.planarity) },
      seat: { point_m: copy(t.centre_m), method: 'zone_centre' },
      selection: copy(desk.selection), confirmed: true, truth_role: 'owner_marked_sofa_zone', qualification: null,
      physical_commands: false };
    const digest = digestFn(copy(body));
    need(sha(digest), 'sofa_digest_required');
    return { ...body, digest };
  }

  // sealDesk is the overlay's sealRegisteredDeskTarget (or the same ESM export in tools).
  function sealSofaTarget(input, { sealDesk, digest } = {}) {
    need(typeof sealDesk === 'function', 'sofa_desk_sealer_required');
    return fromDeskSeal(sealDesk(input, digest), digest);
  }

  function validateSofaTarget(record, { binding, equipment_ref } = {}, { sealDesk, digest } = {}) {
    need(object(record) && Object.keys(record).length === KEYS.length && KEYS.every(key => Object.hasOwn(record, key))
      && record.schema === SCHEMA && record.version === 1, 'closed_sofa_target_required');
    need(equal(record.binding, binding) && equal(record.equipment_ref, equipment_ref), 'sofa_target_binding_changed');
    const selection = record.selection;
    need(object(selection) && Array.isArray(selection.raw_corners), 'closed_sofa_selection_required');
    const expected = sealSofaTarget({ binding, equipment_ref, corners: selection.raw_corners, confirmed: record.confirmed,
      ...(selection.surface_ref ? { surface_ref: selection.surface_ref } : {}) }, { sealDesk, digest });
    need(equal(record, expected), 'sofa_target_changed');
    return copy(record);
  }

  // Near-fixture defaults by wall side (contract: 02 and 03 share wall B,
  // 01 and 04 share wall A). The owner can replace them at export time.
  const DEFAULT_NEAR = Object.freeze({
    sofa: Object.freeze(['victoria-den-proposed-fixture-02', 'victoria-den-mount-03-virtual-fixture']),
    desk: Object.freeze(['victoria-den-proposed-fixture-01', 'victoria-den-mount-04-virtual-fixture']),
  });
  const FIXTURE_IDS = Object.freeze(['victoria-den-proposed-fixture-01', 'victoria-den-proposed-fixture-02',
    'victoria-den-mount-03-virtual-fixture', 'victoria-den-mount-04-virtual-fixture']);
  const LIMIT_KEYS = ['pan_min_deg', 'pan_max_deg', 'tilt_min_deg', 'tilt_max_deg'];
  const vector = v => Array.isArray(v) && v.length === 3 && v.every(Number.isFinite);

  // The one private file the brain reads (AL-1 lighting-brain-geometry-1).
  // `fixtures` are Home's virtual fixture descriptors for this registration:
  // aim_origin_m is display_origin_m, the origin aimAtTarget solves from.
  // Identity, sources and zone polygons ride along as extra keys.
  function brainGeometryExport({ sofa, desk = null, fixtures, cameraM = null, nearFixtures = {}, binding, equipment_ref, generatedAt }, digestFn) {
    need(object(sofa) && sofa.schema === SCHEMA, 'sofa_target_required');
    need(equal(sofa.binding, binding) && equal(sofa.equipment_ref, equipment_ref), 'sofa_target_binding_changed');
    need(desk === null || DESK_SCHEMAS.includes(desk?.schema) && equal(desk.binding, binding)
      && equal(desk.equipment_ref, equipment_ref), 'desk_target_scope_mismatch');
    need(typeof digestFn === 'function' && Number.isFinite(generatedAt), 'brain_geometry_export_inputs_required');
    need(cameraM === null || vector(cameraM), 'camera_position_invalid');
    need(Array.isArray(fixtures) && fixtures.length === FIXTURE_IDS.length
      && FIXTURE_IDS.every(id => fixtures.filter(row => row?.fixture_id === id).length === 1), 'registered_virtual_fixtures_incomplete');
    const rows = FIXTURE_IDS.map(id => {
      const row = fixtures.find(item => item.fixture_id === id), ref = row.horizontal_world_reference;
      need(vector(row.aim_origin_m) && vector(ref) && Math.hypot(ref[0], ref[1]) > 1e-6, 'registered_virtual_fixture_geometry_invalid');
      const limits = row.limits ? Object.fromEntries(LIMIT_KEYS.map(key => [key, row.limits[key]])) : null;
      need(!limits || LIMIT_KEYS.every(key => Number.isFinite(limits[key])), 'registered_virtual_fixture_limits_invalid');
      return { fixture_id: id, aim_origin_m: copy(row.aim_origin_m), horizontal_world_reference: [ref[0], ref[1], 0],
        ...(limits ? { limits } : {}) };
    });
    const near = zone => {
      const list = nearFixtures[zone] ?? DEFAULT_NEAR[zone];
      need(Array.isArray(list) && list.length > 0 && new Set(list).size === list.length
        && list.every(id => FIXTURE_IDS.includes(id)), `${zone}_near_fixtures_invalid`);
      return [...list];
    };
    const body = { schema: GEOMETRY_SCHEMA, home: 'victoria', room: 'den', frame: sofa.scope.map_id,
      camera_m: cameraM ? copy(cameraM) : null, fixtures: rows,
      zones: {
        sofa: { seat_m: copy(sofa.seat.point_m), near_fixtures: near('sofa'), seat_method: sofa.seat.method,
          zone_vertices_m: copy(sofa.zone.vertices_m), zone_centre_m: copy(sofa.zone.centre_m), zone_normal_unit: copy(sofa.zone.normal_unit) },
        desk: { seat_m: desk ? copy(desk.target.centre_m) : null, near_fixtures: near('desk'),
          ...(desk ? { seat_method: 'desk_centre', zone_vertices_m: copy(desk.target.vertices_m), zone_centre_m: copy(desk.target.centre_m),
            zone_normal_unit: copy(desk.target.normal_unit) } : {}) },
      },
      frame_convention: sofa.frame, unit: sofa.unit, binding: copy(binding), equipment_ref: copy(equipment_ref),
      generated_at: generatedAt,
      sources: { sofa: { schema: sofa.schema, digest: sofa.digest }, ...(desk ? { desk: { schema: desk.schema, digest: desk.digest } } : {}) },
      truth_role: 'owner_marked_targets_and_virtual_fixture_model', physical_commands: false };
    return { ...body, digest: digestFn(copy(body)) };
  }

  return Object.freeze({ SCHEMA, GEOMETRY_SCHEMA, FIXTURE_IDS, DEFAULT_NEAR, fromDeskSeal, sealSofaTarget, validateSofaTarget, brainGeometryExport });
});
