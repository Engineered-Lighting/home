/* Apartment unified prototype — site data.
 * Echo Park geometry/fixtures/targets/cameras come from the public repo's
 * app/src/assets/apartment/seed-model.json (all records "proposed"). Furniture
 * boxes, light states and perception samples are AUTHORED for the prototype.
 * Victoria's Den is AUTHORED from the design brief (four sockets, white
 * tubular-diffuser cylinders, radial/down/spot channels); its registered scan,
 * socket coordinates and perception data live in the owner's local checkout
 * and were not readable in this design session. */
(function () {
  const COCO = ["nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder",
    "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip", "left_knee", "right_knee",
    "left_ankle", "right_ankle"];
  const BONES = [["left_shoulder", "right_shoulder"], ["left_shoulder", "left_elbow"], ["left_elbow", "left_wrist"],
    ["right_shoulder", "right_elbow"], ["right_elbow", "right_wrist"], ["left_shoulder", "left_hip"],
    ["right_shoulder", "right_hip"], ["left_hip", "right_hip"], ["left_hip", "left_knee"], ["left_knee", "left_ankle"],
    ["right_hip", "right_knee"], ["right_knee", "right_ankle"], ["nose", "left_eye"], ["nose", "right_eye"],
    ["left_eye", "left_ear"], ["right_eye", "right_ear"]];

  const echoLight = (id, name, room, pos, state) => ({
    id, name, short: name, room, kind: "ceiling", entity: "light." + id, pos, mount: [pos[0], pos[1], pos[2] + 0.02],
    provenance: "seed", calibration: "proposed", state,
  });

  const ECHO = {
    id: "echo", name: "Echo Park", place: "Los Angeles",
    frame: "z_up_metric_floor0", extents: [14.228, 8.176, 2.786],
    scene: {
      label: "Seed model geometry", kind: "seed",
      note: "Stand-in drawn from seed-model.json. In your checkout the prototype loads app/data/apartment/points.sim.ply when it is reachable.",
      ply: ["../../../../app/data/apartment/points.sim.ply"],
      photo: false, mesh: true,
      density: { floor: 620, wall: 170, top: 900, side: 260 },
    },
    model: { source: "Live Home Assistant model", revision: 42, saved: "saved" },
    authority: { ha: "Echo Park Home Assistant", lights: 13 },
    rooms: [
      { id: "living_room", name: "Living room", poly: [[7.4, 0.6], [13.6, 0.6], [13.6, 6.2], [7.4, 6.2]], h: 2.5 },
      { id: "kitchen", name: "Kitchen", poly: [[3.0, 2.6], [7.4, 2.6], [7.4, 6.5], [3.0, 6.5]], h: 2.4 },
      { id: "dining_room", name: "Dining room", poly: [[3.0, 0.6], [7.4, 0.6], [7.4, 2.6], [3.0, 2.6]], h: 2.4 },
    ],
    openings: [
      { a: [7.4, 3.5], b: [7.4, 4.8], z1: 2.1 }, { a: [7.4, 1.0], b: [7.4, 2.3], z1: 2.1 },
      { a: [3.9, 2.6], b: [6.8, 2.6], z1: 2.4 }, { a: [13.6, 1.4], b: [13.6, 2.6], z0: 0.9, z1: 2.1 },
    ],
    furniture: [
      { id: "f-dining-table", name: "Dining table", room: "dining_room", min: [4.08, 1.05, 0], max: [5.92, 1.95, 0.75] },
      { id: "f-island", name: "Kitchen island", room: "kitchen", min: [4.1, 3.28, 0], max: [5.3, 3.92, 0.9] },
      { id: "f-counter", name: "Kitchen counter", room: "kitchen", min: [3.2, 5.92, 0], max: [7.2, 6.5, 0.9] },
      { id: "f-coffee", name: "Coffee table", room: "living_room", min: [10.04, 3.19, 0], max: [10.96, 3.81, 0.375] },
      { id: "f-sofa", name: "Sofa", room: "living_room", min: [9.35, 1.95, 0], max: [11.65, 2.85, 0.45] },
      { id: "f-sofa-back", name: "Sofa", room: "living_room", min: [9.35, 1.95, 0.45], max: [11.65, 2.2, 0.85] },
      { id: "f-desk", name: "Desk", room: "living_room", min: [9.9, 5.62, 0], max: [11.3, 6.18, 0.74] },
      { id: "f-art", name: "Living room art", room: "living_room", art: true, min: [13.56, 2.8, 1.05], max: [13.6, 4.0, 1.85] },
    ],
    targets: [
      { id: "target-dining-table", name: "Dining table", category: "table", pos: [5.0, 1.5, 0.75], normal: [0, 0, 1], size: [1.85, 0.9], source: "tape_and_scan" },
      { id: "target-coffee-table", name: "Coffee table", category: "table", pos: [10.5, 3.5, 0.375], normal: [0, 0, 1], size: [0.93, 0.62], source: "tape_and_scan" },
      { id: "target-kitchen-island", name: "Kitchen island", category: "island", pos: [4.7, 3.6, 0.9], normal: [0, 0, 1], size: [1.19, 0.65], source: "tape_and_scan" },
      { id: "target-living-room-art", name: "Living room art", category: "art", pos: [13.55, 3.4, 1.45], normal: [-1, 0, 0], size: [1.2, 0.8], source: "proposed" },
    ],
    fixtures: [
      echoLight("office", "Office", "living_room", [10.5, 5.6, 2.4], { on: true, bri: 70, k: 3000 }),
      Object.assign(echoLight("front_left", "Front left", "living_room", [8.6, 5.0, 2.4], { on: true, bri: 45, k: 2700 }), {
        engineered: { status: "simulated", fwhm: 20, tiltLimit: 75, optic: "Carclo 10511 · frosted narrow spot",
          note: "Engineered profile at the existing mount. Hardware is offline, so its beam is a simulated preview and sends no command." },
        channels: { spot: { on: true, bri: 60, k: 2700 }, radial: { on: false, levels: [0, 0, 0, 0, 0, 0], k: 2700 } },
        aim: { targetId: "target-coffee-table", point: [10.5, 3.5, 0.375], normal: [0, 0, 1], surface: "Coffee table" },
      }),
      echoLight("front_right", "Front right", "living_room", [8.6, 1.8, 2.4], { on: false, bri: 50, k: 2700 }),
      echoLight("rear_left", "Rear left", "living_room", [12.4, 5.0, 2.4], { on: false, bri: 50, k: 2700 }),
      echoLight("rear_right", "Rear right", "living_room", [12.4, 1.8, 2.4], { on: true, bri: 30, k: 2400 }),
      echoLight("dining_table_left", "Dining table left", "dining_room", [4.5, 1.5, 2.3], { on: true, bri: 60, k: 2700 }),
      echoLight("dining_table_right", "Dining table right", "dining_room", [5.5, 1.5, 2.3], { on: true, bri: 60, k: 2700 }),
      echoLight("island_left", "Island left", "kitchen", [4.2, 3.6, 2.3], { on: false, bri: 80, k: 3500 }),
      echoLight("island_right", "Island right", "kitchen", [5.2, 3.6, 2.3], { on: false, bri: 80, k: 3500 }),
      echoLight("sink", "Sink", "kitchen", [5.0, 6.0, 2.3], { on: true, bri: 40, k: 3000 }),
      { id: "ambient_left", name: "Ambient light left", short: "Ambient left", room: "living_room", kind: "plug", entity: "switch.ambient_light_left_mss110_main_channel", pos: [9.0, 1.4, 0.8], provenance: "seed", state: { on: true, k: 2400 } },
      { id: "ambient_right", name: "Ambient light right", short: "Ambient right", room: "living_room", kind: "plug", entity: "switch.ambient_light_right_mss110_main_channel", pos: [12.8, 1.4, 0.8], provenance: "seed", state: { on: false, k: 2400 } },
      { id: "smart_plug_mini", name: "Smart plug mini", short: "Plug mini", room: "living_room", kind: "plug", entity: "switch.smart_plug_mini_mss110_main_channel", pos: [13.1, 3.2, 0.8], provenance: "seed", state: { on: true, k: 2400 } },
    ],
    cameras: [
      { id: "living_room", name: "Living room camera", pos: [7.7, 5.4, 2.3], yaw: -0.85, pitch: -0.55, vfov: 58, media: "../../../../app/src/simulation/cameras/sim_living_room.jpg", w: 960, h: 540 },
      { id: "kitchen", name: "Kitchen camera", pos: [7.0, 5.6, 2.3], yaw: 3.11, pitch: -0.6, vfov: 58, media: "../../../../app/src/simulation/cameras/sim_kitchen.jpg", w: 960, h: 540 },
      { id: "dining_room", name: "Dining room camera", pos: [7.1, 4.4, 2.3], yaw: -2.24, pitch: -0.55, vfov: 58, media: "../../../../app/src/simulation/cameras/sim_dining_room.jpg", w: 960, h: 540 },
    ],
    perception: {
      capability: "detection",
      summary: "Snapshots and person detection. Pose, body and JEV are not available for Echo Park cameras.",
      tracker: { label: "Someone in the living room", level: "room", source: "Tracker /ws/tracks" },
      frames: {
        living_room: { frameId: "snapshot", detections: [{ label: "person", box: [0.094, 0.66, 0.178, 0.89], score: 0.88 }] },
        kitchen: { frameId: "snapshot", detections: [] },
        dining_room: { frameId: "snapshot", detections: [] },
      },
    },
    capabilities: { engineered: "simulated", perception: "detection", body: false, jev: false, photo: false, bench: "loopback" },
  };

  const vicFixture = (n, name, x, y, st) => ({
    id: "vic-s" + n, name: "Socket " + n + " · " + name, short: "S" + n, room: "den", kind: "cylinder",
    socket: n, entity: null, pos: [x, y, 2.455], mount: [x, y, 2.62], provenance: "modeled", calibration: "modeled",
    engineered: { status: "modeled", fwhm: 20, tiltLimit: 80, optic: "Hidden robotic spotlight · nominal 20° FWHM",
      note: "Virtual fixture at a mapped socket. Not commissioned: no physical command path, no reported state." },
    channels: st.channels, aim: st.aim, state: { on: true, bri: 0, k: 2700 },
  });
  const rad = (on, lvl, k) => ({ on, levels: [lvl, lvl, lvl, lvl, lvl, lvl], k });

  const VICTORIA = {
    id: "victoria", name: "Victoria", place: "Victoria",
    frame: "z_up_metric_floor0", extents: [4.8, 3.9, 2.62],
    scene: {
      label: "Den · authored stand-in", kind: "authored",
      note: "Authored from the brief. The registered Den scan, socket map and fixture placements are in your local checkout and were not readable here.",
      ply: [], photo: false, mesh: true,
      density: { floor: 1300, wall: 380, top: 1700, side: 520 },
    },
    model: { source: "Victoria Apartment model", revision: null, saved: "saved" },
    authority: { ha: "Victoria Home Assistant", lights: 0 },
    rooms: [{ id: "den", name: "Den", poly: [[0, 0], [4.8, 0], [4.8, 3.9], [0, 3.9]], h: 2.62 }],
    openings: [{ a: [0, 0.35], b: [0, 1.2], z1: 2.05 }, { a: [0.9, 3.9], b: [2.3, 3.9], z0: 0.85, z1: 2.15 }],
    furniture: [
      { id: "v-desk", name: "Desk", room: "den", min: [2.75, 3.25, 0], max: [4.45, 3.85, 0.74] },
      { id: "v-desk-chair", name: "Desk chair", room: "den", min: [3.28, 2.62, 0], max: [3.82, 3.12, 0.47] },
      { id: "v-desk-chair-back", name: "Desk chair", room: "den", min: [3.28, 2.56, 0.47], max: [3.82, 2.66, 0.95] },
      { id: "v-sofa", name: "Sofa", room: "den", min: [1.5, 0.12, 0], max: [3.7, 0.98, 0.44] },
      { id: "v-sofa-back", name: "Sofa", room: "den", min: [1.5, 0.12, 0.44], max: [3.7, 0.34, 0.86] },
      { id: "v-coffee", name: "Coffee table", room: "den", min: [2.15, 1.45, 0], max: [3.1, 2.0, 0.42] },
      { id: "v-reading-chair", name: "Reading chair", room: "den", min: [0.36, 2.4, 0], max: [1.14, 3.18, 0.45] },
      { id: "v-reading-chair-back", name: "Reading chair", room: "den", min: [0.36, 2.4, 0.45], max: [0.52, 3.18, 0.95] },
      { id: "v-shelf", name: "Bookshelf", room: "den", min: [0, 1.45, 0], max: [0.34, 2.25, 1.95] },
      { id: "v-painting", name: "Painting", room: "den", art: true, min: [4.76, 1.35, 1.15], max: [4.8, 2.35, 1.85] },
    ],
    targets: [
      { id: "aim-desk-reading", name: "Desk reading", category: "custom", pos: [3.6, 3.52, 0.74], normal: [0, 0, 1], size: [0.7, 0.45], source: "authored" },
      { id: "aim-painting", name: "Painting", category: "art", pos: [4.78, 1.85, 1.5], normal: [-1, 0, 0], size: [1.0, 0.7], source: "authored" },
      { id: "aim-reading-chair", name: "Reading chair", category: "custom", pos: [0.8, 2.78, 0.46], normal: [0, 0, 1], size: [0.6, 0.6], source: "authored" },
      { id: "aim-coffee-table", name: "Coffee table", category: "table", pos: [2.62, 1.72, 0.42], normal: [0, 0, 1], size: [0.95, 0.55], source: "authored" },
    ],
    fixtures: [
      vicFixture(1, "Sofa left", 1.25, 1.05, { channels: { spot: { on: false, bri: 50, k: 2700 }, down: { on: true, bri: 22, k: 2700 }, radial: rad(true, 18, 2700) }, aim: { targetId: "aim-coffee-table", point: [2.62, 1.72, 0.42], normal: [0, 0, 1], surface: "Coffee table" } }),
      vicFixture(2, "Sofa right", 3.55, 1.05, { channels: { spot: { on: true, bri: 48, k: 3000 }, down: { on: false, bri: 30, k: 2700 }, radial: rad(true, 14, 2700) }, aim: { targetId: "aim-painting", point: [4.78, 1.85, 1.5], normal: [-1, 0, 0], surface: "Painting" } }),
      vicFixture(3, "Reading corner", 1.25, 2.85, { channels: { spot: { on: false, bri: 40, k: 2700 }, down: { on: false, bri: 30, k: 2700 }, radial: rad(true, 26, 2400) }, aim: { targetId: "aim-reading-chair", point: [0.8, 2.78, 0.46], normal: [0, 0, 1], surface: "Reading chair" } }),
      vicFixture(4, "Desk", 3.55, 2.85, { channels: { spot: { on: true, bri: 55, k: 2700 }, down: { on: true, bri: 18, k: 2700 }, radial: rad(false, 0, 2700) }, aim: { targetId: "aim-desk-reading", point: [3.6, 3.52, 0.74], normal: [0, 0, 1], surface: "Desk" } }),
    ],
    cameras: [{ id: "den", name: "Den camera", pos: [4.62, 3.74, 2.3], look: [1.9, 1.3, 0.55], vfov: 60, media: null, w: 960, h: 540, standIn: true }],
    perception: {
      capability: "exact-frame",
      summary: "Exact-frame analysis: camera frame, 2D pose and model results stay bound to the frame that produced them.",
      sample: {
        frameId: 18234, cameraId: "den", recordedAt: "2026-10-16 21:04:12",
        detector: { label: "person", score: 0.91, box: null },
        pose: { model: "pose model · COCO-17", observed: 13, total: 17 },
        jev: {
          window_s: 8, frames: 6, usable: 6, minUsable: 4,
          dist: [["reading", 0.58], ["using a computer", 0.24], ["resting", 0.11], ["other", 0.07]],
          reasons: ["Head angled down toward the desk for most of the window", "Hands near the desk surface, little typing motion"],
        },
        proposal: { fixtureId: "vic-s4", targetId: "aim-desk-reading", k: 2700, bri: 55 },
        registration: { camera: "Den camera", status: "authored pose", rms_px: null },
      },
      body: {
        joints: {
          nose: [3.55, 3.27, 1.17], left_eye: [3.52, 3.24, 1.2], right_eye: [3.585, 3.24, 1.2],
          left_ear: [3.47, 3.17, 1.18], right_ear: [3.63, 3.17, 1.18], left_shoulder: [3.36, 3.06, 1.0],
          right_shoulder: [3.75, 3.06, 1.0], left_elbow: [3.3, 3.27, 0.8], right_elbow: [3.82, 3.27, 0.8],
          left_wrist: [3.43, 3.5, 0.78], right_wrist: [3.69, 3.5, 0.78], left_hip: [3.44, 2.97, 0.52],
          right_hip: [3.66, 2.97, 0.52], left_knee: [3.42, 3.42, 0.52], right_knee: [3.68, 3.42, 0.52],
          left_ankle: [3.41, 3.46, 0.08], right_ankle: [3.7, 3.46, 0.08],
        },
        inferred: ["left_knee", "right_knee", "left_ankle", "right_ankle"],
      },
    },
    capabilities: { engineered: "modeled", perception: "exact-frame", body: true, jev: true, photo: false, bench: "none" },
  };

  window.APT_DATA = { sites: { echo: ECHO, victoria: VICTORIA }, COCO, BONES };
})();
