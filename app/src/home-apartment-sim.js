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
      { id: "target-sim-dining-table", name: "dining table", category: "table", shape: "surface",
        pos: [5.6, 5.7, 0.75], normal: [0, 0, 1], up: [0, 1, 0], size_m: [1.85, 0.9],
        room_id: "dining_room", confidence: 1, source: "sim" },
      { id: "target-sim-kitchen-island", name: "kitchen island", category: "island", shape: "surface",
        pos: [5.6, 2.4, 0.9], normal: [0, 0, 1], up: [0, 1, 0], size_m: [1.2, 0.65],
        room_id: "kitchen", confidence: 1, source: "sim" },
      { id: "target-sim-art", name: "living room art", category: "art", shape: "surface",
        pos: [13.75, 5.1, 1.45], normal: [-1, 0, 0], up: [0, 0, 1], size_m: [1.2, 0.8],
        room_id: "living_room", confidence: 1, source: "sim" },
    ],
    devices: [
      { id: "dev-light-living", type: "light", name: "living lights",
        ha_entity_id: "light.living_room", pos: [10.5, 4.0, 2.35], yaw_rad: 0,
        height_preset: "ceiling", room_id: "living_room", controllable: true,
        confidence: 1, source: "manual" },
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

  window.__SIM_APARTMENT_MODEL_FIXTURE = () => JSON.parse(JSON.stringify(MODEL));

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
        id: "t_sim", person: "marcelo", state: "active",
        pos: [Math.round(x * 100) / 100, Math.round(y * 100) / 100, 0],
        vel: null, cov: [[0.05, 0], [0, 0.06]], room: "living_room", zone: null,
        source_cams: ["living_room"], conf: 0.9, conf_reason: "good",
        activity: t > 6 ? "watching_tv" : null, activity_conf: 0.72, activity_source: "rules",
      };
    } else {
      track = {
        id: "t_sim", person: "marcelo", state: "room_only",
        pos: null, vel: null, cov: null, room: "kitchen", zone: null,
        source_cams: ["kitchen"], conf: 0.6, conf_reason: "room_only",
        activity: t > 18 ? "cooking" : null, activity_conf: 0.7, activity_source: "rules",
      };
    }
    return { type: "tracks", ts: Date.now() / 1000, tracks: [track] };
  };
})();
