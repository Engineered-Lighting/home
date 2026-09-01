"use strict";

const assert = require("node:assert/strict");
const A = require("../app/src/home-apartment-aiming.js");

let count = 0;
function test(name, fn) {
  try {
    fn();
    count += 1;
    process.stdout.write(`ok ${count} - ${name}\n`);
  } catch (error) {
    process.stderr.write(`not ok ${count + 1} - ${name}\n${error.stack}\n`);
    process.exitCode = 1;
  }
}
const near = (actual, expected, epsilon = 1e-6) => assert.ok(
  Math.abs(actual - expected) <= epsilon,
  `expected ${actual} to be within ${epsilon} of ${expected}`,
);

const calibration = {
  model: "ideal_two_axis_pan_tilt",
  horizontal_world_reference: [0, 1, 0],
  pan_sign: -1,
  tilt_sign: 1,
  raw_encoder_reference_deg: { pan: 10, tilt: -2 },
  derived_offsets_deg: { yaw: 5, tilt_zero: 2 },
};

test("compiled collision identity is a full SHA-256 for the known runtime proxy", () => {
  assert.match(A.COLLISION_GEOMETRY_SHA256, /^[0-9a-f]{64}$/);
  assert.ok(A.COLLISION_GEOMETRY_SHA256.startsWith("320cd1d6"));
});

test("cardinal pan directions use +Y as the calibrated reference", () => {
  near(A.solveAim([0, 0, 2], [0, 2, 2]).pan_deg, 0);
  near(A.solveAim([0, 0, 2], [2, 0, 2]).pan_deg, 90);
  near(A.solveAim([0, 0, 2], [-2, 0, 2]).pan_deg, -90);
  near(Math.abs(A.solveAim([0, 0, 2], [0, -2, 2]).pan_deg), 180);
});

test("positive tilt points down and negative tilt points up", () => {
  near(A.solveAim([0, 0, 2], [0, 1, 1]).tilt_deg, 45);
  near(A.solveAim([0, 0, 2], [0, 1, 3]).tilt_deg, -45);
  near(A.solveAim([0, 0, 2], [0, 1, 2]).tilt_deg, 0);
});

test("vertical destinations retain preview but are non-executable", () => {
  const down = A.solveAim([0, 0, 2], [0, 0, 0], { current_pan_deg: 37 });
  assert.equal(down.tilt_deg, 90);
  assert.equal(down.pan_deg, 37);
  assert.equal(down.pan_indeterminate, true);
  assert.equal(down.executable, false);
});

test("limits are reported without clipping or wrapping", () => {
  const result = A.solveAim([0, 0, 2], [2, 0, 2], {
    limits: { pan_min_deg: -30, pan_max_deg: 30, tilt_min_deg: -10, tilt_max_deg: 10 },
  });
  near(result.pan_deg, 90);
  assert.deepEqual(result.violations, ["pan_limit"]);
  assert.equal(result.executable, false);
});

test("raw and mechanical angles round trip with explicit signs and offsets", () => {
  const mechanical = A.rawToMechanical({ pan: 20, tilt: 3 }, calibration);
  assert.equal(mechanical.ok, true);
  near(mechanical.pan, -5);
  near(mechanical.tilt, 7);
  const raw = A.mechanicalToRaw(mechanical, calibration);
  near(raw.pan, 20);
  near(raw.tilt, 3);
});

test("missing axis signs fail closed", () => {
  assert.equal(A.rawToMechanical({ pan: 0, tilt: 0 }, {}).ok, false);
  assert.equal(A.mechanicalToRaw({ pan: 0, tilt: 0 }, {}).ok, false);
});

test("actual raw destination and physical travel are exposed", () => {
  const result = A.solveAim([0, 0, 2], [1, 1, 1], {
    calibration,
    limits: { pan_min_deg: -180, pan_max_deg: 180, tilt_min_deg: -90, tilt_max_deg: 90 },
    current_raw: { pan: 10, tilt: -2 },
  });
  assert.ok(result.raw_destination);
  assert.ok(result.physical_travel.total_abs_deg > 0);
});

test("beam radius is distance times tangent of half FWHM", () => {
  near(A.beamRadius(2, 20), 2 * Math.tan(10 * Math.PI / 180));
});

test("normal and oblique planes produce bounded footprints", () => {
  const normal = A.projectBeamToPlane({
    origin: [0, 0, 2], direction: [0, 0, -1], plane_point: [0, 0, 0],
    plane_normal: [0, 0, 1], full_fwhm_deg: 20,
  });
  assert.equal(normal.kind, "ellipse");
  near(normal.major_radius_m, normal.minor_radius_m);
  const oblique = A.projectBeamToPlane({
    origin: [0, 0, 2], direction: [0.5, 0, -1], plane_point: [0, 0, 0],
    plane_normal: [0, 0, 1], full_fwhm_deg: 20,
  });
  assert.equal(oblique.kind, "ellipse");
  assert.ok(oblique.major_radius_m > oblique.minor_radius_m);
});

test("surface footprint boundary lies in the selected destination plane", () => {
  const planePoint = [0.25, -0.5, 0.8];
  const planeNormal = [0.2, 0.4, 1];
  const footprint = A.projectBeamToPlane({
    origin: [0, 0, 2.4], direction: [0.25, -0.5, -1.6],
    plane_point: planePoint, plane_normal: planeNormal, full_fwhm_deg: 20,
  });
  assert.equal(footprint.kind, "ellipse");
  assert.equal(footprint.points.length, 64);
  const normalLength = Math.hypot(...planeNormal);
  for (const point of footprint.points) {
    const offPlane = Math.abs(point.reduce((sum, value, index) =>
      sum + (value - planePoint[index]) * planeNormal[index] / normalLength, 0));
    near(offPlane, 0, 1e-8);
  }
});

test("grazing projection is partial instead of a false ellipse", () => {
  const result = A.projectBeamToPlane({
    origin: [0, 0, 1], direction: [1, 0, -0.02], plane_point: [0, 0, 0],
    plane_normal: [0, 0, 1], full_fwhm_deg: 20,
  });
  assert.equal(result.kind, "partial");
});

test("first-hit obstruction requires a hit before the destination", () => {
  assert.equal(A.firstHitObstruction(4, 2).blocked, true);
  assert.equal(A.firstHitObstruction(4, 4).blocked, false);
});

test("six radial zones honor floor-view order and fine adjustment", () => {
  const c = { anchor_zone: 2, anchor_world_angle_deg: 15, fine_adjust_deg: 5, order: "clockwise" };
  near(A.radialZoneAngle(2, c), 20);
  near(A.radialZoneAngle(3, c), 80);
  near(A.radialZoneAngle(1, { ...c, order: "counterclockwise" }), 80);
});

function freshSnapshot(overrides = {}) {
  return {
    connected: true,
    device: { stable_id: "gimbal-a" },
    product_profile_sha256: "profile-a",
    session_epoch: "boot-a",
    state_seq: 42,
    angle: {
      pan: { deg: 1, source: "motor", age_ms: 20 },
      tilt: { deg: 2, source: "motor", age_ms: 25 },
    },
    ...overrides,
  };
}

test("fresh matching motor telemetry is accepted", () => {
  const result = A.evaluateTelemetry(freshSnapshot(), {
    now_ms: 1100, received_at_ms: 1000,
    expected_device: "gimbal-a", expected_profile: "profile-a",
  });
  assert.equal(result.valid, true);
  assert.equal(result.ages_ms.pan, 120);
});

test("canonical bench USB and Product Aim fields form the stable identity", () => {
  const snapshot = freshSnapshot({
    device: undefined,
    product_profile_sha256: undefined,
    usb: { board_vid_pid: "303A:1001", board_serial: "GIMBAL-001" },
    product_aim: { profile_sha256: "profile-bench", ready: true },
  });
  const identity = A.telemetryIdentity(snapshot);
  assert.equal(identity.device, "303A:1001/GIMBAL-001");
  assert.equal(identity.profile, "profile-bench");
});

test("telemetry ages monotonically and cannot become fresh by repetition", () => {
  const snapshot = freshSnapshot();
  const earlier = A.evaluateTelemetry(snapshot, { now_ms: 1100, received_at_ms: 1000 });
  const later = A.evaluateTelemetry(snapshot, { now_ms: 1400, received_at_ms: 1000 });
  assert.ok(later.ages_ms.pan > earlier.ages_ms.pan);
  assert.ok(later.blockers.includes("stale"));
});

test("non-motor, missing session, identity mismatch, and profile mismatch fail", () => {
  const result = A.evaluateTelemetry(freshSnapshot({
    session_epoch: null,
    angle: { pan: { deg: 1, source: "sim", age_ms: 0 }, tilt: { deg: 2, source: "motor", age_ms: 0 } },
  }), { expected_device: "other", expected_profile: "other" });
  for (const blocker of ["motor_source_required", "incomplete_identity", "fixture_identity_mismatch", "profile_digest_mismatch"]) {
    assert.ok(result.blockers.includes(blocker));
  }
});

test("telemetry requires an explicit fixture and profile binding", () => {
  const result = A.evaluateTelemetry(freshSnapshot(), { now_ms: 1000, received_at_ms: 1000 });
  assert.ok(result.blockers.includes("fixture_binding_required"));
  assert.ok(result.blockers.includes("expected_profile_required"));
});

test("disconnect, reboot, and non-monotonic sequence form continuity boundaries", () => {
  const base = { device: "gimbal-a", profile: "profile-a", session: "boot-a", sequence: 10 };
  assert.deepEqual(A.telemetryContinuity(base, { ...base, sequence: 11 }), { continuous: true, boundary: null });
  assert.equal(A.telemetryContinuity(base, { ...base, session: "boot-b", sequence: 1 }).boundary, "session_changed");
  assert.equal(A.telemetryContinuity(base, { ...base, sequence: 9 }).boundary, "non_monotonic_sequence");
  assert.equal(A.evaluateTelemetry({ ...freshSnapshot(), connected: false }, {
    expected_device: "gimbal-a", expected_profile: "profile-a",
  }).blockers.includes("disconnected"), true);
});

test("light capabilities use modern Kelvin bounds", () => {
  const result = A.lightCapabilities({ state: "on", attributes: {
    supported_color_modes: ["color_temp"], min_color_temp_kelvin: 1800, max_color_temp_kelvin: 6500,
  } });
  assert.equal(result.brightness, true);
  assert.equal(result.color_temp, true);
  assert.equal(result.min_kelvin, 1800);
  assert.equal(result.max_kelvin, 6500);
});

test("light capabilities fall back to legacy mired bounds", () => {
  const result = A.lightCapabilities({ state: "on", attributes: {
    supported_color_modes: ["color_temp"], min_mireds: 154, max_mireds: 500,
  } });
  near(result.min_kelvin, 2000);
  near(result.max_kelvin, 1000000 / 154);
});

test("automatic shared-mount view prefers an available Engineered profile", () => {
  const result = A.resolveFixtureProfileView("auto", {
    current_available: true, engineered_available: true, engineered_configured: true,
  });
  assert.equal(result.resolved, "engineered");
  assert.equal(result.show_engineered, true);
  assert.equal(result.show_current, false);
  assert.equal(result.engineered_preview, false);
});

test("automatic shared-mount view falls back to the current light", () => {
  const result = A.resolveFixtureProfileView("auto", {
    current_available: true, engineered_available: false, engineered_configured: true,
  });
  assert.equal(result.resolved, "current");
  assert.equal(result.show_current, true);
  assert.equal(result.show_engineered, false);
});

test("explicit Engineered view remains a clearly simulated preview while offline", () => {
  const result = A.resolveFixtureProfileView("engineered", {
    current_available: true, engineered_available: false, engineered_configured: true,
  });
  assert.equal(result.show_current, false);
  assert.equal(result.show_engineered, true);
  assert.equal(result.engineered_preview, true);
  assert.equal(result.reason, "engineered_preview");
});

test("both view keeps co-located identities visible without changing availability", () => {
  const result = A.resolveFixtureProfileView("both", {
    current_available: false, engineered_available: false, engineered_configured: true,
  });
  assert.equal(result.show_current, true);
  assert.equal(result.show_engineered, true);
  assert.equal(result.engineered_preview, true);
});

test("explicit spotlight and radial mappings prevent duplicates across roles", () => {
  const a = A.normalizeEngineeredFixture({ id: "a", fixture_kind: A.ENGINEERED_KIND,
    spotlight: { entity_id: "light.shared" } });
  const b = A.normalizeEngineeredFixture({ id: "b", fixture_kind: A.ENGINEERED_KIND,
    radial_zones: { zones: [{ number: 1, entity_id: "light.shared" }] } });
  const result = A.validateEntityMappings([a, b]);
  assert.equal(result.ok, false);
  assert.equal(result.duplicates[0].entity_id, "light.shared");
});

test("normalization is lazy and preserves ordinary fixtures", () => {
  const ordinary = { id: "plain", type: "light" };
  assert.equal(A.normalizeEngineeredFixture(ordinary), ordinary);
  const engineered = A.normalizeEngineeredFixture({ id: "g", fixture_kind: A.ENGINEERED_KIND });
  assert.equal(engineered.radial_zones.zones.length, 6);
  assert.equal(engineered.spotlight.optic_profile.configured_fwhm_deg, 20);
});

test("adding an Engineered profile preserves shared-mount geometry and tape", () => {
  const ordinary = {
    id: "fixture-a", type: "light", ha_entity_id: "light.current",
    pos: [7.6, 1.8, 2.3], room_id: "dining_room", yaw_rad: 0.25,
    fixture_calibration: {
      status: "verified", wall_distances: [{ wall: "west", distance_m: 2.5 }],
      floor_to_ceiling_m: 2.4, ceiling_to_fixture_bottom_m: 0.1,
      derived_floor_to_bottom_m: 2.3, floor_to_bottom_verification_m: 2.3,
    },
  };
  const adopted = A.normalizeEngineeredFixture({ ...ordinary, ...A.defaultEngineeredFixtureFields() });
  assert.deepEqual(adopted.pos, ordinary.pos);
  assert.equal(adopted.room_id, ordinary.room_id);
  assert.equal(adopted.yaw_rad, ordinary.yaw_rad);
  assert.deepEqual(adopted.fixture_calibration, ordinary.fixture_calibration);
  assert.equal(adopted.ha_entity_id, "light.current");
});

test("Product request fails without a qualified frame binding", () => {
  assert.equal(A.productAimRequest([1, 2, 0], null).qualified, false);
  assert.equal(A.productAimRequest([1, 2, 0], { kind: "qualified_target_plane" }).qualified, false);
});

test("simulation Product plane emits only the exact accepted body", () => {
  const result = A.productAimRequest([2, 3, 0], {
    kind: "qualified_target_plane",
    origin: [1, 1, 0], x_axis: [1, 0, 0], y_axis: [0, 1, 0], tolerance_m: 0.001,
    apartment_frame_digest: "synthetic-frame", product_profile_sha256: "synthetic-profile",
  }, 125.4);
  assert.equal(result.qualified, true);
  assert.deepEqual(result.body, { target_x_m: 1, target_y_m: 2, post_dwell_ms: 125 });
  assert.deepEqual(Object.keys(result.body), ["target_x_m", "target_y_m", "post_dwell_ms"]);
});

test("off-plane Product destination is rejected", () => {
  const result = A.productAimRequest([0, 0, 0.5], {
    kind: "qualified_target_plane",
    origin: [0, 0, 0], x_axis: [1, 0, 0], y_axis: [0, 1, 0], tolerance_m: 0.01,
    apartment_frame_digest: "f", product_profile_sha256: "p",
  });
  assert.equal(result.reason, "destination_off_qualified_plane");
});

test("Product plane descriptor is invalidated by a profile digest change", () => {
  const result = A.productAimRequest([0, 0, 0], {
    kind: "qualified_target_plane", origin: [0, 0, 0], x_axis: [1, 0, 0], y_axis: [0, 1, 0],
    apartment_frame_digest: "frame", product_profile_sha256: "old-profile",
  }, 0, "new-profile");
  assert.equal(result.reason, "target_plane_profile_mismatch");
});

if (!process.exitCode) process.stdout.write(`1..${count}\n`);
