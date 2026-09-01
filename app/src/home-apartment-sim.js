/* home-apartment-sim.js — sim-mode fixtures for the /apartment view.
 * Zones/devices approximate the real scan footprint (14.2 x 8.2 m); the
 * scripted walk exercises BOTH honesty-ladder states: precise dot in the
 * living room, room-level pulse in the kitchen.
 * Only consumed when Simulation Mode is active (home-apartment-data.js).
 */
(function () {
  const MODEL = {
    schema_version: 1, revision: 7, exists: true,
    meta: { frame: "z_up_metric_floor0", name: "sim apartment" },
    zones: [
      { id: "living_room", name: "living room", color: "#b8d8ff", ceiling_height_m: 2.4,
        frigate_camera: "living_room",
        floor_polygon: [[7.6, 0.6], [13.8, 0.6], [13.8, 7.4], [7.6, 7.4]] },
      { id: "kitchen", name: "kitchen", color: "#a8ffd8", ceiling_height_m: 2.4,
        frigate_camera: "kitchen",
        floor_polygon: [[3.8, 0.6], [7.6, 0.6], [7.6, 4.2], [3.8, 4.2]] },
      { id: "dining_room", name: "dining room", color: "#ffe2a8", ceiling_height_m: 2.4,
        frigate_camera: "dining_room",
        floor_polygon: [[3.8, 4.2], [7.6, 4.2], [7.6, 7.4], [3.8, 7.4]] },
      { id: "workshop", name: "workshop", color: "#d8a8ff", ceiling_height_m: 2.3,
        frigate_camera: "workshop",
        floor_polygon: [[0.4, 0.6], [3.8, 0.6], [3.8, 3.4], [0.4, 3.4]] },
    ],
    targets: [
      { id: "target-sim-coffee-table", name: "coffee table", category: "table", shape: "surface",
        pos: [10.8, 3.4, 0.44], normal: [0, 0, 1], up: [0, 1, 0], size_m: [1.15, 0.62],
        room_id: "living_room", confidence: 1, source: "sim" },
      { id: "target-sim-dining-table", name: "dining table", category: "table", shape: "surface",
        pos: [5.6, 5.7, 0.75], normal: [0, 0, 1], up: [0, 1, 0], size_m: [1.85, 0.9],
        room_id: "dining_room", confidence: 1, source: "sim" },
      { id: "target-sim-kitchen-island", name: "kitchen island", category: "island", shape: "surface",
        pos: [5.6, 2.4, 0.9], normal: [0, 0, 1], up: [0, 1, 0], size_m: [1.2, 0.65],
        room_id: "kitchen", confidence: 1, source: "sim" },
      { id: "target-sim-art", name: "living room art", category: "art", shape: "surface",
        pos: [13.75, 5.1, 1.45], normal: [-1, 0, 0], up: [0, 0, 1], size_m: [1.2, 0.8],
        room_id: "living_room", confidence: 1, source: "sim" },
      { id: "target-sim-custom-point", name: "reading chair", category: "custom", shape: "point",
        pos: [12.1, 5.8, 0.9], normal: [0, 0, 1], up: [0, 1, 0], size_m: [0, 0],
        room_id: "living_room", confidence: 1, source: "sim" },
    ],
    devices: [
      { id: "dev-light-living", type: "light", name: "living lights",
        ha_entity_id: "light.living_room", pos: [10.5, 4.0, 2.35], yaw_rad: 0,
        height_preset: "ceiling", room_id: "living_room", controllable: true,
        confidence: 1, source: "manual" },
      { id: "dev-sim-engineered-gimbal", type: "light", name: "engineered gimbal · simulation",
        ha_entity_id: null, pos: [10.1, 4.7, 2.24], yaw_rad: 0,
        height_preset: "ceiling", room_id: "living_room", controllable: true,
        aiming_origin: "fixture_bottom", fixture_kind: "engineered_gimbal_v1",
        fixture_calibration: {
          status: "verified", aiming_origin: "fixture_bottom",
          wall_distances: [{ wall: "west", distance_m: 2.5 }, { wall: "south", distance_m: 4.1 }],
          floor_to_ceiling_m: 2.4, ceiling_to_fixture_bottom_m: 0.16,
          derived_floor_to_bottom_m: 2.24, floor_to_bottom_verification_m: 2.24,
          verification_error_m: 0, source: "simulation",
        },
        spotlight: {
          entity_id: "light.sim_engineered_spotlight",
          optic_profile: {
            manufacturer: "Carclo", part: "10511", beam_type: "frosted narrow spot",
            nominal_fwhm_deg: 20, configured_fwhm_deg: 20, confidence: "nominal",
            led_identity: "synthetic tunable-white LED", source: {
              kind: "manufacturer", url: "https://www.carclo-optics.com/products/optic-10511",
            },
          },
        },
        gimbal: {
          limits: { pan_min_deg: -180, pan_max_deg: 180, tilt_min_deg: -90, tilt_max_deg: 90, tilt_positive: "down" },
          device_binding: { stable_id: "sim-gimbal-001", usb_identity: "SIM:001" },
          product_profile_sha256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          default_target_id: "target-sim-coffee-table",
          visualization_calibration: {
            model: "ideal_two_axis_pan_tilt", status: "verified", authority: "visualization_only",
            pan_sign: 1, tilt_sign: 1, horizontal_world_reference: [0, 1, 0],
            raw_encoder_reference_deg: { pan: 0, tilt: 0 },
            derived_offsets_deg: { yaw: 0, tilt_zero: 0 },
            collision_geometry_sha256: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            product_profile_sha256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", based_on_model_revision: 7,
            source: "simulation", confidence: 1,
            assumptions: ["ideal orthogonal axes", "vertical pan axis"],
            verification_destinations: ["target-sim-coffee-table", "target-sim-art"],
          },
          product_target_plane_descriptor: {
            kind: "qualified_target_plane", origin: [0, 0, 0.44],
            x_axis: [1, 0, 0], y_axis: [0, 1, 0], tolerance_m: 0.03,
            apartment_frame_digest: "synthetic-apartment-frame-v1",
            product_profile_sha256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          },
        },
        radial_zones: {
          zones: Array.from({ length: 6 }, (_, i) => ({ number: i + 1, entity_id: `light.sim_radial_${i + 1}` })),
          orientation_calibration: {
            status: "verified", anchor_zone: 1, anchor_world_angle_deg: 0,
            order: "clockwise", fine_adjust_deg: 0, viewing_convention: "floor_looking_up",
            source: "simulation", based_on_model_revision: 7,
          },
        },
        confidence: 1, source: "simulation" },
      { id: "dev-light-kitchen", type: "light", name: "kitchen lights",
        ha_entity_id: "light.kitchen", pos: [5.6, 2.4, 2.35], yaw_rad: 0,
        height_preset: "ceiling", room_id: "kitchen", controllable: true,
        confidence: 1, source: "manual" },
      { id: "dev-sonos-living", type: "speaker", name: "sonos arc",
        ha_entity_id: "media_player.living_room", pos: [12.8, 2.2, 0.75], yaw_rad: 0,
        height_preset: "table", room_id: "living_room", controllable: true,
        confidence: 1, source: "manual" },
      { id: "dev-tv-living", type: "tv", name: "tv",
        ha_entity_id: "media_player.tv", pos: [13.4, 4.0, 1.3], yaw_rad: Math.PI,
        height_preset: "wall", room_id: "living_room", controllable: true,
        confidence: 1, source: "manual" },
      { id: "dev-cam-living", type: "camera", name: "living cam",
        ha_entity_id: "camera.living_room", pos: [8.0, 7.0, 2.2], yaw_rad: -2.2,
        height_preset: "wall", room_id: "living_room", controllable: false,
        confidence: 1, source: "manual" },
    ],
  };

  const PROFILE_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
  const COLLISION_SHA = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
  const AIM_RUNTIME = { primary_id: null, fixtures: {} };

  function simEntityId(prefix, id, suffix = "") {
    return `light.sim_${prefix}_${String(id || "fixture").replace(/[^a-z0-9_]+/gi, "_").toLowerCase()}${suffix}`;
  }

  function nearestTargetId(fixture, targets) {
    let best = null, bestDistance = Infinity;
    for (const target of targets || []) {
      if (!Array.isArray(target?.pos)) continue;
      const distance = Math.hypot(target.pos[0] - fixture.pos[0], target.pos[1] - fixture.pos[1]);
      if (distance < bestDistance) { bestDistance = distance; best = target.id; }
    }
    return best;
  }

  function simulatedEngineeredFixture(device, targets, index, revision) {
    const pos = Array.isArray(device.pos) ? device.pos : [0, 0, 2.24];
    const floorToCeiling = device.fixture_calibration?.floor_to_ceiling_m || Math.max(2.3, pos[2] + 0.16);
    const drop = device.fixture_calibration?.ceiling_to_fixture_bottom_m || Math.max(0, floorToCeiling - pos[2]);
    const spotlightEntity = simEntityId("spotlight", device.id);
    const stableId = `sim-${device.id}`;
    return {
      ...device, fixture_kind: "engineered_gimbal_v1", aiming_origin: "fixture_bottom",
      simulation_overlay: true,
      fixture_calibration: device.fixture_calibration || {
        status: "proposed", aiming_origin: "fixture_bottom", wall_distances: [],
        floor_to_ceiling_m: floorToCeiling, ceiling_to_fixture_bottom_m: drop,
        derived_floor_to_bottom_m: floorToCeiling - drop, source: "simulation_layout_preview",
      },
      spotlight: { entity_id: spotlightEntity, optic_profile: {
        manufacturer: "Carclo", part: "10511", beam_type: "frosted narrow spot",
        nominal_fwhm_deg: 20, configured_fwhm_deg: 20, confidence: "nominal",
        led_identity: "simulated tunable-white LED", source: { kind: "simulation" },
      } },
      gimbal: {
        limits: { pan_min_deg: -180, pan_max_deg: 180, tilt_min_deg: -90, tilt_max_deg: 90, tilt_positive: "down" },
        device_binding: { stable_id: stableId, usb_identity: `SIM:${index + 1}` },
        product_profile_sha256: PROFILE_SHA, default_target_id: nearestTargetId(device, targets),
        visualization_calibration: {
          model: "ideal_two_axis_pan_tilt", status: "verified", authority: "visualization_only",
          pan_sign: 1, tilt_sign: 1, horizontal_world_reference: [0, 1, 0],
          raw_encoder_reference_deg: { pan: 0, tilt: 0 }, derived_offsets_deg: { yaw: 0, tilt_zero: 0 },
          collision_geometry_sha256: COLLISION_SHA, product_profile_sha256: PROFILE_SHA,
          based_on_model_revision: revision, source: "simulation_layout_preview", confidence: 1,
          assumptions: ["ideal orthogonal axes", "vertical pan axis"], verification_destinations: [],
        },
        product_target_plane_descriptor: {
          kind: "qualified_target_plane", origin: [0, 0, 0], x_axis: [1, 0, 0], y_axis: [0, 1, 0],
          tolerance_m: 0.03, apartment_frame_digest: "synthetic-apartment-frame-v1",
          product_profile_sha256: PROFILE_SHA,
        },
      },
      radial_zones: {
        zones: Array.from({ length: 6 }, (_, zoneIndex) => ({ number: zoneIndex + 1,
          entity_id: simEntityId("radial", device.id, `_${zoneIndex + 1}`) })),
        orientation_calibration: {
          status: "verified", anchor_zone: 1, anchor_world_angle_deg: ((index * 23) % 60) - 30,
          order: "clockwise", fine_adjust_deg: 0, viewing_convention: "floor_looking_up",
          source: "simulation_layout_preview", based_on_model_revision: revision,
        },
      },
      source: "simulation_layout_preview",
    };
  }

  function makeRuntime(fixture, index, targetById) {
    const defaultTarget = targetById.get(fixture.gimbal?.default_target_id) || null;
    const solved = defaultTarget?.pos ? window.HomeApartmentAiming?.solveAim?.(fixture.pos, defaultTarget.pos, {
      calibration: fixture.gimbal?.visualization_calibration,
      limits: fixture.gimbal?.limits,
    }) : null;
    const initialRaw = solved?.raw_destination;
    return {
      received_at_ms: Date.now(), default_target_id: fixture.gimbal?.default_target_id,
      current_destination: defaultTarget ? {
        kind: "named", id: defaultTarget.id, name: defaultTarget.name, pos: defaultTarget.pos,
        normal: defaultTarget.normal || null, key: `named:${defaultTarget.id}`,
        committed_at_ms: Date.now(), source: "simulation_initial_state",
      } : null,
      telemetry: {
        connected: true, ready: true, state_seq: 1, session_epoch: "sim-boot-1",
        device: { stable_id: fixture.gimbal?.device_binding?.stable_id }, product_profile_sha256: PROFILE_SHA,
        angle: {
          pan: { deg: Number.isFinite(+initialRaw?.pan) ? +initialRaw.pan : ((index * 41 + 18) % 240) - 120,
            source: "sim", age_ms: 0 },
          tilt: { deg: Number.isFinite(+initialRaw?.tilt) ? +initialRaw.tilt : 34 + (index % 4) * 4,
            source: "sim", age_ms: 0 },
        },
        activity: "holding",
      },
      spotlight: { entity_id: fixture.spotlight?.entity_id, state: index % 4 === 3 ? "off" : "on",
        brightness: 120 + (index % 4) * 28, color_temp_kelvin: 2400 + (index % 5) * 550 },
      zones: Object.fromEntries(Array.from({ length: 6 }, (_, i) => [i + 1, {
        state: (i + index) % 3 ? "off" : "on", brightness: (i + index) % 3 ? 0 : 76,
        color_temp_kelvin: 2400 + (index % 5) * 550, status: "current",
      }])),
    };
  }

  function configureRuntime(model) {
    AIM_RUNTIME.fixtures = {};
    const targetById = new Map((model.targets || []).map((target) => [target.id, target]));
    const fixtures = (model.devices || []).filter((d) => d.fixture_kind === "engineered_gimbal_v1");
    fixtures.forEach((fixture, index) => { AIM_RUNTIME.fixtures[fixture.id] = makeRuntime(fixture, index, targetById); });
    AIM_RUNTIME.primary_id = fixtures[0]?.id || null;
  }

  function buildSavedLayoutSimulation(savedLayout) {
    const cloned = JSON.parse(JSON.stringify(savedLayout));
    const targets = Array.isArray(cloned.targets) ? cloned.targets : [];
    let index = 0;
    cloned.devices = (cloned.devices || []).map((device) => {
      const isCeiling = device?.type === "light" && (device.height_preset === "ceiling"
        || device.fixture_calibration || (+device?.pos?.[2] >= 1.8));
      if (!isCeiling) return device;
      return simulatedEngineeredFixture(device, targets, index++, cloned.revision);
    });
    cloned.meta = { ...(cloned.meta || {}), name: "simulation · saved Apartment layout" };
    cloned.exists = true;
    return cloned;
  }

  window.__SIM_APARTMENT_MODEL_FIXTURE = (savedLayout) => {
    const model = savedLayout ? buildSavedLayoutSimulation(savedLayout) : JSON.parse(JSON.stringify(MODEL));
    configureRuntime(model);
    return model;
  };

  window.__SIM_APARTMENT_AIM_RUNTIME = {
    read(fixtureId) {
      const id = fixtureId || AIM_RUNTIME.primary_id;
      return JSON.parse(JSON.stringify(AIM_RUNTIME.fixtures[id] || null));
    },
    readAll() { return JSON.parse(JSON.stringify(AIM_RUNTIME.fixtures)); },
    setSpotlight(fixtureId, patch) {
      if (typeof fixtureId === "object") { patch = fixtureId; fixtureId = AIM_RUNTIME.primary_id; }
      const runtime = AIM_RUNTIME.fixtures[fixtureId || AIM_RUNTIME.primary_id];
      if (runtime) Object.assign(runtime.spotlight, patch, { status: "accepted" });
      return this.read(fixtureId);
    },
    setZone(fixtureId, number, patch) {
      if (typeof fixtureId === "number") { patch = number; number = fixtureId; fixtureId = AIM_RUNTIME.primary_id; }
      const runtime = AIM_RUNTIME.fixtures[fixtureId || AIM_RUNTIME.primary_id];
      if (runtime?.zones?.[number]) {
        Object.assign(runtime.zones[number], patch, { status: "accepted" });
        if (runtime.zones[number].state === "on" && !(runtime.zones[number].brightness > 0)) {
          runtime.zones[number].brightness = 76;
        }
      }
      return this.read(fixtureId);
    },
    setZones(fixtureId, numbers, patch) {
      if (Array.isArray(fixtureId)) { patch = numbers; numbers = fixtureId; fixtureId = AIM_RUNTIME.primary_id; }
      for (const n of numbers || []) this.setZone(fixtureId, n, patch);
      return this.read(fixtureId);
    },
    toggleZone(fixtureId, number) {
      const runtime = AIM_RUNTIME.fixtures[fixtureId || AIM_RUNTIME.primary_id];
      const zone = runtime?.zones?.[number];
      if (zone) this.setZone(fixtureId, number, { state: zone.state === "on" ? "off" : "on",
        brightness: zone.state === "on" ? 0 : Math.max(zone.brightness || 0, 76) });
      return this.read(fixtureId);
    },
    setDestination(fixtureId, destination) {
      const runtime = AIM_RUNTIME.fixtures[fixtureId || AIM_RUNTIME.primary_id];
      if (!runtime || !destination || !Array.isArray(destination.pos)) return this.read(fixtureId);
      runtime.current_destination = JSON.parse(JSON.stringify({
        kind: destination.kind, id: destination.id || null, name: destination.name || null,
        pos: destination.pos, normal: destination.normal || null,
        key: destination.key || destination.id || destination.kind,
        committed_at_ms: Date.now(), source: "simulation",
      }));
      runtime.default_target_id = destination.kind === "named" ? destination.id : null;
      if (Number.isFinite(+destination.raw_destination?.pan)
          && Number.isFinite(+destination.raw_destination?.tilt)) {
        runtime.telemetry.angle.pan.deg = +destination.raw_destination.pan;
        runtime.telemetry.angle.tilt.deg = +destination.raw_destination.tilt;
      }
      runtime.telemetry.state_seq = (+runtime.telemetry.state_seq || 0) + 1;
      runtime.telemetry.activity = "holding";
      runtime.received_at_ms = Date.now();
      return this.read(fixtureId);
    },
    reset() { window.location.reload(); },
  };

  // scripted loop (~24 s): walk the living room precisely, then "lose feet"
  // in the kitchen (room-level), then back.
  const t0 = Date.now();
  window.__SIM_APARTMENT_TRACKS = () => {
    const t = ((Date.now() - t0) / 1000) % 24;
    let track;
    if (t < 12) {
      const k = t / 12;
      const x = 8.5 + 4.5 * (0.5 - 0.5 * Math.cos(k * Math.PI * 2));
      const y = 2.0 + 3.5 * (0.5 - 0.5 * Math.cos(k * Math.PI * 4)) / 2;
      track = {
        id: "t_sim", person: "alex", state: "active",
        pos: [Math.round(x * 100) / 100, Math.round(y * 100) / 100, 0],
        vel: null, cov: [[0.05, 0], [0, 0.06]], room: "living_room", zone: null,
        source_cams: ["living_room"], conf: 0.9, conf_reason: "good",
        activity: t > 6 ? "watching_tv" : null, activity_conf: 0.72, activity_source: "rules",
      };
    } else {
      track = {
        id: "t_sim", person: "alex", state: "room_only",
        pos: null, vel: null, cov: null, room: "kitchen", zone: null,
        source_cams: ["kitchen"], conf: 0.6, conf_reason: "room_only",
        activity: t > 18 ? "cooking" : null, activity_conf: 0.7, activity_source: "rules",
      };
    }
    return { type: "tracks", ts: Date.now() / 1000, tracks: [track] };
  };
})();
