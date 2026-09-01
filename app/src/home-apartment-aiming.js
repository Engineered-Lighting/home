/* Framework-independent Apartment engineered-light aiming primitives.
 *
 * This module intentionally has no DOM, React, Three, HA, or Tauri dependency.
 * It is shared by the browser UI and executable Node tests. Nothing here sends
 * a command or writes persistent state.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.HomeApartmentAiming = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const DEG = Math.PI / 180;
  const RAD = 180 / Math.PI;
  const ENGINEERED_KIND = "engineered_gimbal_v1";
  const MAX_TELEMETRY_AGE_MS = 300;
  const PRODUCT_AIM_PATH = "/api/group/aim";
  const OPTIC_SOURCE = "https://www.carclo-optics.com/products/optic-10511";
  const COLLISION_GEOMETRY_SHA256 = "320cd1d6c843625cf802b5a8f0ed2caf6e3e8f9638377cb70d35749a48506736";

  const finite = (v) => typeof v === "number" && Number.isFinite(v);
  const clampDot = (v) => Math.max(-1, Math.min(1, v));
  const vec3 = (v, fallback = [0, 0, 0]) => Array.isArray(v) && v.length >= 3
    && v.slice(0, 3).every((x) => Number.isFinite(+x))
    ? v.slice(0, 3).map(Number) : [...fallback];
  const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const scale = (v, k) => [v[0] * k, v[1] * k, v[2] * k];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const cross = (a, b) => [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
  ];
  const length = (v) => Math.hypot(v[0], v[1], v[2]);
  const unit = (v, fallback = [0, 1, 0]) => {
    const n = length(v);
    return n > 1e-9 ? scale(v, 1 / n) : [...fallback];
  };

  function defaultOpticProfile() {
    return {
      manufacturer: "Carclo",
      part: "10511",
      beam_type: "frosted narrow spot",
      nominal_fwhm_deg: 20,
      configured_fwhm_deg: 20,
      confidence: "nominal",
      led_identity: null,
      source: { kind: "manufacturer", url: OPTIC_SOURCE },
    };
  }

  function defaultEngineeredFixtureFields() {
    return {
      fixture_kind: ENGINEERED_KIND,
      spotlight: { entity_id: null, optic_profile: defaultOpticProfile() },
      gimbal: {
        limits: {
          pan_min_deg: -180, pan_max_deg: 180,
          tilt_min_deg: -90, tilt_max_deg: 90,
          tilt_positive: "down",
        },
        device_binding: null,
        product_profile_sha256: null,
        visualization_calibration: null,
        default_target_id: null,
      },
      radial_zones: {
        zones: Array.from({ length: 6 }, (_, i) => ({ number: i + 1, entity_id: null })),
        orientation_calibration: null,
      },
    };
  }

  function normalizeEngineeredFixture(device) {
    if (!device || device.fixture_kind !== ENGINEERED_KIND) return device;
    const defaults = defaultEngineeredFixtureFields();
    const optic = { ...defaults.spotlight.optic_profile, ...(device.spotlight?.optic_profile || {}) };
    const byNumber = new Map((device.radial_zones?.zones || []).map((z) => [+z.number, z]));
    return {
      ...device,
      fixture_kind: ENGINEERED_KIND,
      spotlight: { ...defaults.spotlight, ...(device.spotlight || {}), optic_profile: optic },
      gimbal: {
        ...defaults.gimbal,
        ...(device.gimbal || {}),
        limits: { ...defaults.gimbal.limits, ...(device.gimbal?.limits || {}) },
      },
      radial_zones: {
        ...defaults.radial_zones,
        ...(device.radial_zones || {}),
        zones: Array.from({ length: 6 }, (_, i) => ({
          number: i + 1,
          entity_id: null,
          ...(byNumber.get(i + 1) || {}),
        })),
      },
    };
  }

  function calibratedBasis(calibration) {
    const raw = vec3(calibration?.horizontal_world_reference, [0, 1, 0]);
    const forward = unit([raw[0], raw[1], 0]);
    const right = unit([forward[1], -forward[0], 0], [1, 0, 0]);
    return { forward, right, down: [0, 0, -1] };
  }

  function conversionConfig(calibration) {
    const panSign = calibration?.pan_sign;
    const tiltSign = calibration?.tilt_sign;
    return {
      ok: (panSign === 1 || panSign === -1) && (tiltSign === 1 || tiltSign === -1),
      panSign,
      tiltSign,
      rawPanRef: +calibration?.raw_encoder_reference_deg?.pan || 0,
      rawTiltRef: +calibration?.raw_encoder_reference_deg?.tilt || 0,
      yawOffset: +calibration?.derived_offsets_deg?.yaw || 0,
      tiltZero: +calibration?.derived_offsets_deg?.tilt_zero || 0,
    };
  }

  function rawToMechanical(raw, calibration) {
    const cfg = conversionConfig(calibration);
    if (!cfg.ok || !finite(+raw?.pan) || !finite(+raw?.tilt)) {
      return { ok: false, reason: "explicit pan and tilt signs plus finite raw angles are required" };
    }
    return {
      ok: true,
      pan: cfg.panSign * (+raw.pan - cfg.rawPanRef) + cfg.yawOffset,
      tilt: cfg.tiltSign * (+raw.tilt - cfg.rawTiltRef) + cfg.tiltZero,
      convention: "mechanical_positive_tilt_down",
    };
  }

  function mechanicalToRaw(mechanical, calibration) {
    const cfg = conversionConfig(calibration);
    if (!cfg.ok || !finite(+mechanical?.pan) || !finite(+mechanical?.tilt)) {
      return { ok: false, reason: "explicit pan and tilt signs plus finite mechanical angles are required" };
    }
    return {
      ok: true,
      pan: cfg.rawPanRef + (+mechanical.pan - cfg.yawOffset) / cfg.panSign,
      tilt: cfg.rawTiltRef + (+mechanical.tilt - cfg.tiltZero) / cfg.tiltSign,
      convention: "raw_encoder",
    };
  }

  function solveAim(originValue, targetValue, options = {}) {
    const origin = vec3(originValue);
    const target = vec3(targetValue);
    const delta = sub(target, origin);
    const horizontalDistance = Math.hypot(delta[0], delta[1]);
    const distance = length(delta);
    if (distance < 1e-9) return { ok: false, executable: false, reason: "target equals aiming origin" };
    const basis = calibratedBasis(options.calibration || options);
    const vertical = horizontalDistance < 1e-7;
    const pan = vertical
      ? (finite(options.current_pan_deg) ? options.current_pan_deg : 0)
      : Math.atan2(dot(delta, basis.right), dot(delta, basis.forward)) * RAD;
    const tilt = Math.atan2(-delta[2], horizontalDistance) * RAD;
    const limits = {
      pan_min_deg: -180, pan_max_deg: 180,
      tilt_min_deg: -90, tilt_max_deg: 90,
      ...(options.limits || {}),
    };
    const violations = [];
    if (pan < limits.pan_min_deg || pan > limits.pan_max_deg) violations.push("pan_limit");
    if (tilt < limits.tilt_min_deg || tilt > limits.tilt_max_deg) violations.push("tilt_limit");
    const raw = options.calibration ? mechanicalToRaw({ pan, tilt }, options.calibration) : null;
    const currentRaw = options.current_raw;
    const travel = raw?.ok && finite(+currentRaw?.pan) && finite(+currentRaw?.tilt) ? {
      pan_deg: raw.pan - +currentRaw.pan,
      tilt_deg: raw.tilt - +currentRaw.tilt,
      total_abs_deg: Math.abs(raw.pan - +currentRaw.pan) + Math.abs(raw.tilt - +currentRaw.tilt),
    } : null;
    return {
      ok: true,
      executable: !vertical && violations.length === 0 && (!raw || raw.ok),
      origin, target, delta, distance_m: distance, horizontal_distance_m: horizontalDistance,
      pan_deg: pan, tilt_deg: tilt,
      pan_indeterminate: vertical,
      ambiguity: vertical ? "vertical_target_pan_indeterminate" : null,
      violations,
      raw_destination: raw?.ok ? { pan: raw.pan, tilt: raw.tilt } : null,
      raw_conversion_error: raw && !raw.ok ? raw.reason : null,
      physical_travel: travel,
    };
  }

  function directionFromAim(panDeg, tiltDeg, calibration = {}) {
    const basis = calibratedBasis(calibration);
    const p = panDeg * DEG;
    const t = tiltDeg * DEG;
    const horizontal = add(scale(basis.forward, Math.cos(p)), scale(basis.right, Math.sin(p)));
    return unit(add(scale(horizontal, Math.cos(t)), scale(basis.down, Math.sin(t))));
  }

  function beamRadius(distanceM, fullFwhmDeg) {
    if (!finite(+distanceM) || distanceM < 0 || !finite(+fullFwhmDeg)
        || fullFwhmDeg <= 0 || fullFwhmDeg >= 180) return NaN;
    return distanceM * Math.tan((fullFwhmDeg * DEG) / 2);
  }

  function rayPlaneIntersection(originValue, directionValue, planePointValue, planeNormalValue) {
    const origin = vec3(originValue);
    const direction = unit(vec3(directionValue));
    const planePoint = vec3(planePointValue);
    const normal = unit(vec3(planeNormalValue, [0, 0, 1]));
    const denom = dot(direction, normal);
    if (Math.abs(denom) < 1e-8) return { ok: false, reason: "parallel" };
    const distance = dot(sub(planePoint, origin), normal) / denom;
    if (distance <= 0) return { ok: false, reason: "behind" };
    return { ok: true, point: add(origin, scale(direction, distance)), distance_m: distance, incidence: Math.abs(denom) };
  }

  function projectBeamToPlane({ origin, direction, plane_point, plane_normal, full_fwhm_deg }) {
    const hit = rayPlaneIntersection(origin, direction, plane_point, plane_normal);
    if (!hit.ok) return { ...hit, kind: "none" };
    const radius = beamRadius(hit.distance_m, full_fwhm_deg);
    const axis = unit(vec3(direction));
    const planePoint = vec3(plane_point);
    const planeNormal = unit(vec3(plane_normal, [0, 0, 1]));
    const helper = Math.abs(axis[2]) < 0.9 ? [0, 0, 1] : [0, 1, 0];
    const u = unit(cross(axis, helper), [1, 0, 0]);
    const w = unit(cross(u, axis), [0, 1, 0]);
    const halfAngle = Math.max(0.1, Math.min(179, +full_fwhm_deg || 20)) * DEG / 2;
    const sampleCount = 64;
    const points = [];
    for (let index = 0; index < sampleCount; index += 1) {
      const phi = (index / sampleCount) * Math.PI * 2;
      const rim = add(scale(u, Math.cos(phi)), scale(w, Math.sin(phi)));
      const ray = unit(add(scale(axis, Math.cos(halfAngle)), scale(rim, Math.sin(halfAngle))));
      const boundary = rayPlaneIntersection(origin, ray, planePoint, planeNormal);
      if (boundary.ok) points.push(boundary.point);
    }
    const hitFraction = points.length / sampleCount;
    if (hit.incidence < 0.08 || hitFraction < 0.75) return { ...hit, kind: "partial", radius_m: radius,
      reason: "grazing", points, hit_fraction: hitFraction, plane_normal: planeNormal };
    const major = radius / hit.incidence;
    if (!finite(major) || major > hit.distance_m * 4) {
      return { ...hit, kind: "partial", radius_m: radius, reason: "unbounded",
        points, hit_fraction: hitFraction, plane_normal: planeNormal };
    }
    return { ...hit, kind: "ellipse", minor_radius_m: radius, major_radius_m: major,
      points, hit_fraction: hitFraction, plane_normal: planeNormal };
  }

  function firstHitObstruction(destinationDistanceM, hitDistanceM, toleranceM = 0.03) {
    const blocked = finite(hitDistanceM) && finite(destinationDistanceM)
      && hitDistanceM + toleranceM < destinationDistanceM;
    return { blocked, marker_distance_m: blocked ? hitDistanceM : null };
  }

  function radialZoneAngle(zoneNumber, calibration) {
    const n = +zoneNumber;
    if (!Number.isInteger(n) || n < 1 || n > 6) return NaN;
    const anchorZone = +calibration?.anchor_zone || 1;
    const anchorAngle = +calibration?.anchor_world_angle_deg || 0;
    const fine = +calibration?.fine_adjust_deg || 0;
    const order = calibration?.order === "counterclockwise" ? -1 : 1;
    return anchorAngle + fine + order * (n - anchorZone) * 60;
  }

  function sampleAgeMs(sample, snapshotReceivedAtMs, nowMs) {
    const received = finite(+snapshotReceivedAtMs) ? +snapshotReceivedAtMs : +nowMs;
    const elapsed = Math.max(0, +nowMs - received);
    if (finite(+sample?.age_ms)) return Math.max(0, +sample.age_ms) + elapsed;
    if (finite(+sample?.sample_ts)) {
      const sampleMs = +sample.sample_ts < 1e12 ? +sample.sample_ts * 1000 : +sample.sample_ts;
      return Math.max(0, +nowMs - sampleMs);
    }
    return Infinity;
  }

  function telemetryIdentity(snapshot) {
    const usbDevice = snapshot?.usb?.board_vid_pid && snapshot?.usb?.board_serial
      ? `${snapshot.usb.board_vid_pid}/${snapshot.usb.board_serial}` : null;
    return {
      device: snapshot?.device?.stable_id || snapshot?.device?.serial || snapshot?.device_id
        || usbDevice || null,
      profile: snapshot?.product_profile_sha256 || snapshot?.product_profile?.sha256
        || snapshot?.product_aim?.profile_sha256 || null,
      session: snapshot?.session_epoch || snapshot?.session?.epoch || snapshot?.boot_id || null,
      sequence: snapshot?.state_seq ?? snapshot?.sequence ?? null,
    };
  }

  function evaluateTelemetry(snapshot, options = {}) {
    const now = finite(+options.now_ms) ? +options.now_ms : Date.now();
    const receivedAt = finite(+options.received_at_ms) ? +options.received_at_ms : now;
    const pan = snapshot?.angle?.pan || snapshot?.pan || null;
    const tilt = snapshot?.angle?.tilt || snapshot?.tilt || null;
    const identity = telemetryIdentity(snapshot);
    const panAge = sampleAgeMs(pan, receivedAt, now);
    const tiltAge = sampleAgeMs(tilt, receivedAt, now);
    const blockers = [];
    if (!snapshot || snapshot.connected !== true) blockers.push("disconnected");
    if (pan?.source !== "motor" || tilt?.source !== "motor") blockers.push("motor_source_required");
    if (!identity.device || !identity.profile || !identity.session || identity.sequence == null) blockers.push("incomplete_identity");
    if (options.require_binding !== false && !options.expected_device) blockers.push("fixture_binding_required");
    if (options.require_binding !== false && !options.expected_profile) blockers.push("expected_profile_required");
    if (options.expected_device && identity.device !== options.expected_device) blockers.push("fixture_identity_mismatch");
    if (options.expected_profile && identity.profile !== options.expected_profile) blockers.push("profile_digest_mismatch");
    const threshold = finite(+options.max_age_ms) ? +options.max_age_ms : MAX_TELEMETRY_AGE_MS;
    if (panAge >= threshold || tiltAge >= threshold) blockers.push("stale");
    if (!finite(+pan?.deg) || !finite(+tilt?.deg)) blockers.push("angles_unavailable");
    return {
      valid: blockers.length === 0,
      blockers,
      raw: finite(+pan?.deg) && finite(+tilt?.deg) ? { pan: +pan.deg, tilt: +tilt.deg } : null,
      ages_ms: { pan: panAge, tilt: tiltAge },
      identity,
      source: pan?.source === tilt?.source ? pan?.source || null : "mixed",
      freshness_threshold_ms: threshold,
      readiness: snapshot?.ready ?? snapshot?.readiness ?? snapshot?.product_aim?.ready ?? null,
      activity: snapshot?.move?.activity || snapshot?.activity || null,
    };
  }

  function telemetryContinuity(previousIdentity, nextIdentity) {
    if (!previousIdentity || !nextIdentity) return { continuous: false, boundary: "identity_unavailable" };
    if (previousIdentity.device !== nextIdentity.device) return { continuous: false, boundary: "device_changed" };
    if (previousIdentity.profile !== nextIdentity.profile) return { continuous: false, boundary: "profile_changed" };
    if (previousIdentity.session !== nextIdentity.session) return { continuous: false, boundary: "session_changed" };
    if (!finite(+previousIdentity.sequence) || !finite(+nextIdentity.sequence)
        || +nextIdentity.sequence <= +previousIdentity.sequence) {
      return { continuous: false, boundary: "non_monotonic_sequence" };
    }
    return { continuous: true, boundary: null };
  }

  function lightCapabilities(state) {
    const a = state?.attributes || {};
    const modes = Array.isArray(a.supported_color_modes) ? a.supported_color_modes : [];
    const brightness = modes.some((m) => !["onoff", "unknown"].includes(m)) || finite(+a.brightness);
    const colorTemp = modes.includes("color_temp");
    let minKelvin = finite(+a.min_color_temp_kelvin) ? +a.min_color_temp_kelvin : null;
    let maxKelvin = finite(+a.max_color_temp_kelvin) ? +a.max_color_temp_kelvin : null;
    if (minKelvin == null && finite(+a.max_mireds) && +a.max_mireds > 0) minKelvin = 1000000 / +a.max_mireds;
    if (maxKelvin == null && finite(+a.min_mireds) && +a.min_mireds > 0) maxKelvin = 1000000 / +a.min_mireds;
    return {
      available: !!state && state.state !== "unavailable" && state.state !== "unknown",
      brightness,
      color_temp: colorTemp,
      min_kelvin: minKelvin,
      max_kelvin: maxKelvin,
      supported_color_modes: modes,
    };
  }

  function resolveFixtureProfileView(requested, availability = {}) {
    const mode = ["auto", "current", "engineered", "both"].includes(requested)
      ? requested : "auto";
    const currentAvailable = availability.current_available === true;
    const engineeredAvailable = availability.engineered_available === true;
    const engineeredConfigured = availability.engineered_configured === true;

    if (mode === "current") {
      return { requested: mode, resolved: "current", show_current: true,
        show_engineered: false, engineered_preview: false, reason: currentAvailable ? "current_online" : "current_offline" };
    }
    if (mode === "engineered") {
      return { requested: mode, resolved: "engineered", show_current: false,
        show_engineered: engineeredConfigured, engineered_preview: engineeredConfigured && !engineeredAvailable,
        reason: engineeredConfigured ? (engineeredAvailable ? "engineered_online" : "engineered_preview") : "engineered_not_configured" };
    }
    if (mode === "both") {
      return { requested: mode, resolved: "both", show_current: true,
        show_engineered: engineeredConfigured, engineered_preview: engineeredConfigured && !engineeredAvailable,
        reason: "explicit_comparison" };
    }
    if (engineeredConfigured && engineeredAvailable) {
      return { requested: mode, resolved: "engineered", show_current: false,
        show_engineered: true, engineered_preview: false, reason: "engineered_online" };
    }
    if (currentAvailable) {
      return { requested: mode, resolved: "current", show_current: true,
        show_engineered: false, engineered_preview: false, reason: "current_online" };
    }
    return { requested: mode, resolved: engineeredConfigured ? "engineered" : "current",
      show_current: !engineeredConfigured, show_engineered: engineeredConfigured,
      engineered_preview: engineeredConfigured, reason: engineeredConfigured ? "all_offline_preview" : "current_offline" };
  }

  function explicitFixtureMappings(device) {
    if (device?.fixture_kind !== ENGINEERED_KIND) return [];
    const out = [];
    if (device.spotlight?.entity_id) out.push({ role: "spotlight", entity_id: device.spotlight.entity_id });
    for (const zone of device.radial_zones?.zones || []) {
      if (zone?.entity_id) out.push({ role: `radial_${zone.number}`, entity_id: zone.entity_id });
    }
    return out;
  }

  function validateEntityMappings(devices) {
    const uses = new Map();
    const duplicates = [];
    for (const device of devices || []) {
      for (const mapping of explicitFixtureMappings(device)) {
        const prior = uses.get(mapping.entity_id);
        const use = { fixture_id: device.id, ...mapping };
        if (prior) duplicates.push({ entity_id: mapping.entity_id, uses: [prior, use] });
        else uses.set(mapping.entity_id, use);
      }
    }
    return { ok: duplicates.length === 0, duplicates, uses };
  }

  function productAimRequest(destinationValue, descriptor, postDwellMs = 0, expectedProfile = null) {
    if (!descriptor || descriptor.kind !== "qualified_target_plane") {
      return { qualified: false, reason: "no_authoritative_target_plane_binding" };
    }
    const point = vec3(destinationValue);
    const origin = vec3(descriptor.origin);
    const u = unit(vec3(descriptor.x_axis, [1, 0, 0]));
    const v = unit(vec3(descriptor.y_axis, [0, 1, 0]));
    const normal = unit(cross(u, v), [0, 0, 1]);
    const delta = sub(point, origin);
    const offPlane = Math.abs(dot(delta, normal));
    const tolerance = finite(+descriptor.tolerance_m) ? +descriptor.tolerance_m : 0.01;
    if (offPlane > tolerance) return { qualified: false, reason: "destination_off_qualified_plane", off_plane_m: offPlane };
    if (!descriptor.apartment_frame_digest || !descriptor.product_profile_sha256) {
      return { qualified: false, reason: "incomplete_target_plane_binding" };
    }
    if (expectedProfile && descriptor.product_profile_sha256 !== expectedProfile) {
      return { qualified: false, reason: "target_plane_profile_mismatch" };
    }
    return {
      qualified: true,
      endpoint: PRODUCT_AIM_PATH,
      body: {
        target_x_m: dot(delta, u),
        target_y_m: dot(delta, v),
        post_dwell_ms: Math.max(0, Math.round(+postDwellMs || 0)),
      },
      descriptor: {
        apartment_frame_digest: descriptor.apartment_frame_digest,
        product_profile_sha256: descriptor.product_profile_sha256,
      },
    };
  }

  return Object.freeze({
    ENGINEERED_KIND, MAX_TELEMETRY_AGE_MS, PRODUCT_AIM_PATH, OPTIC_SOURCE, COLLISION_GEOMETRY_SHA256,
    defaultOpticProfile, defaultEngineeredFixtureFields, normalizeEngineeredFixture,
    calibratedBasis, rawToMechanical, mechanicalToRaw, solveAim, directionFromAim,
    beamRadius, rayPlaneIntersection, projectBeamToPlane, firstHitObstruction,
    radialZoneAngle, sampleAgeMs, telemetryIdentity, evaluateTelemetry, telemetryContinuity,
    lightCapabilities, resolveFixtureProfileView, explicitFixtureMappings, validateEntityMappings, productAimRequest,
  });
});
