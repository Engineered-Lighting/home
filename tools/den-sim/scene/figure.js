// The procedural figure, procedural-1, holding what it holds up at chest height: the figure and the
// way of holding the model test chose on 2026-11-09. An artificial person, never a scan, so its face
// is not blurred. Joints are COCO-17 names in the picture's own frame (z up, metres), facing -y.
// THREE is passed in, so this file imports nothing and node can test the joints without a browser.
export const FIGURE_ID = 'procedural-1';
export const WAY = 'up_at_chest';
export const COCO17 = ['nose', 'left_eye', 'right_eye', 'left_ear', 'right_ear', 'left_shoulder', 'right_shoulder', 'left_elbow',
  'right_elbow', 'left_wrist', 'right_wrist', 'left_hip', 'right_hip', 'left_knee', 'right_knee', 'left_ankle', 'right_ankle'];
const PITCH = 0.42;                 // head bowed toward what is held, radians
// Small movement between the eight poses, so two looks are two pictures. Pose 0 is the model test's.
const JITTER = { head_pitch: 0.05, head_yaw: 0.08, hand_m: 0.012, spread_m: 0.008, tilt_deg: 4, yaw_deg: 3 };
// Offsets from where the person is (x, y) with absolute heights, for a seat at 0.44 m.
const SEATED = { shoulder: [0.20, 0.17, 0.98], hip: [0.12, 0.10, 0.52], knee: [0.14, -0.33, 0.56], ankle: [0.14, -0.40, 0.10],
  head: [0, 0.10, 1.20], elbow: [0.30, 0.0, 0.76], wrist: [0.15, -0.26, 0.90] };
const STANDING = { shoulder: [0.20, 0.03, 1.42], hip: [0.12, 0.02, 0.94], knee: [0.12, -0.01, 0.52], ankle: [0.13, 0.02, 0.10],
  head: [0, 0.04, 1.64], elbow: [0.30, -0.10, 1.20], wrist: [0.15, -0.39, 1.34] };
const PHONE_HAND_X = 0.065;         // a phone is narrow, so the hands come together
const BOOK = { dx: 0.075, dy: -0.027, dz: 0.05, tilt: -72, yaw: 14 };   // two covers toward the camera, a shallow V

function random(seed) {             // mulberry32: the same seed gives the same poses in every browser
  let a = seed >>> 0;
  return () => { a = (a + 0x6d2b79f5) >>> 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

export function poseParams(seed, count) {
  const out = [];
  for (let k = 0; k < count; k++) {
    if (k === 0) { out.push({ head_pitch: 0, head_yaw: 0, hand: [0, 0, 0], spread: 0, tilt: 0, yaw: 0 }); continue; }
    const r = random(seed * 7919 + k), u = () => 2 * r() - 1, h = JITTER.hand_m;
    out.push({ head_pitch: JITTER.head_pitch * u(), head_yaw: JITTER.head_yaw * u(), hand: [h * u(), h * u(), h * u()],
      spread: JITTER.spread_m * u(), tilt: JITTER.tilt_deg * u(), yaw: JITTER.yaw_deg * u() });
  }
  return out;
}

const add = (a, b) => a.map((v, i) => v + b[i]);
const mirror = ([x, y, z]) => [-x, y, z];
function turn([x, y, z], angle) { const c = Math.cos(angle), s = Math.sin(angle); return [c * x - s * y, s * x + c * y, z]; }

// The figure's joints and head centre for one pose. person: shots.json's {where, at_m, held}.
export function joints(person, pose) {
  const base = person.where.startsWith('standing') ? STANDING : SEATED, [ax, ay] = person.at_m;
  const at = v => [ax + v[0], ay + v[1], v[2]];
  const pitch = PITCH + pose.head_pitch;
  const head = at([base.head[0], base.head[1] - 0.12 * Math.sin(pitch), base.head[2]]);
  const face = offset => add(head, turn(offset, pose.head_yaw));
  let wrist = [...base.wrist];
  if (person.held === 'phone') wrist = [PHONE_HAND_X, ...wrist.slice(1)];
  const lw = add(add(wrist, pose.hand), [pose.spread, 0, 0]), rw = add(add(mirror(wrist), pose.hand), [-pose.spread, 0, 0]);
  const half = pose.hand.map(v => v / 2);
  const j = {
    nose: face([0, -0.105 * Math.cos(pitch), -0.105 * Math.sin(pitch) + 0.005]),
    left_eye: face([0.034, -0.09 * Math.cos(pitch), -0.09 * Math.sin(pitch) + 0.04]),
    right_eye: face([-0.034, -0.09 * Math.cos(pitch), -0.09 * Math.sin(pitch) + 0.04]),
    left_ear: face([0.092, 0.01, 0.005]), right_ear: face([-0.092, 0.01, 0.005]),
    left_shoulder: at(base.shoulder), right_shoulder: at(mirror(base.shoulder)),
    left_elbow: at(add(base.elbow, half)), right_elbow: at(add(mirror(base.elbow), half)),
    left_wrist: at(lw), right_wrist: at(rw),
    left_hip: at(base.hip), right_hip: at(mirror(base.hip)), left_knee: at(base.knee), right_knee: at(mirror(base.knee)),
    left_ankle: at(base.ankle), right_ankle: at(mirror(base.ankle)),
  };
  return { joints: j, head, pitch };
}

// The figure as three.js meshes, in the picture's frame.
export function buildPerson(THREE, person, pose) {
  const { joints: j, head } = joints(person, pose), g = new THREE.Group();
  const mat = (color, roughness = 0.85) => new THREE.MeshStandardMaterial({ color, roughness, metalness: 0 });
  const skin = mat(0xc68e6c, 0.6), shirt = mat(0x9c3b34, 0.9), trousers = mat(0x2c3a4f, 0.9), shoe = mat(0x1f1a18, 0.7), hair = mat(0x2a1d14, 0.9);
  const upY = new THREE.Vector3(0, 1, 0);
  const shadowed = m => { m.castShadow = m.receiveShadow = true; return m; };
  const box = (c, s, material) => { const m = new THREE.Mesh(new THREE.BoxGeometry(...s), material); m.position.set(...c); return shadowed(m); };
  const capsule = (a, b, radius, material) => {
    const A = new THREE.Vector3(...a), B = new THREE.Vector3(...b), axis = B.clone().sub(A), len = axis.length();
    const m = new THREE.Mesh(new THREE.CapsuleGeometry(radius, Math.max(1e-3, len), 8, 16), material);
    m.position.copy(A).addScaledVector(axis, 0.5); m.quaternion.setFromUnitVectors(upY, axis.normalize()); return shadowed(m);
  };
  const ball = (c, radius, material, scale = [1, 1, 1]) => {
    const m = new THREE.Mesh(new THREE.SphereGeometry(radius, 24, 18), material); m.position.set(...c); m.scale.set(...scale); return shadowed(m);
  };
  const mid = (a, b, t = 0.5) => a.map((v, i) => v + (b[i] - v) * t);
  const chest = mid(j.left_shoulder, j.right_shoulder), pelvis = mid(j.left_hip, j.right_hip);
  g.add(capsule(mid(chest, pelvis, 0.1), mid(chest, pelvis, 0.9), 0.145, shirt));
  g.add(capsule(j.left_shoulder, j.right_shoulder, 0.07, shirt), capsule(j.left_hip, j.right_hip, 0.1, trousers));
  g.add(capsule(mid(chest, head, 0.1), mid(chest, head, 0.6), 0.05, skin));             // neck
  g.add(ball(head, 0.1, skin, [0.9, 1.0, 1.15]));
  g.add(ball(add(head, [0, 0.022, 0.03]), 0.104, hair, [0.94, 1.0, 1.05]));
  g.add(ball(j.left_eye, 0.011, shoe), ball(j.right_eye, 0.011, shoe), ball(j.nose, 0.016, skin));
  g.add(ball(j.left_ear, 0.02, skin), ball(j.right_ear, 0.02, skin));
  for (const side of ['left', 'right']) {
    g.add(capsule(j[side + '_shoulder'], j[side + '_elbow'], 0.05, shirt), capsule(j[side + '_elbow'], j[side + '_wrist'], 0.04, skin));
    g.add(ball(mid(j[side + '_wrist'], j[side + '_elbow'], -0.18), 0.045, skin, [1.0, 1.25, 0.6]));    // hand
    g.add(capsule(j[side + '_hip'], j[side + '_knee'], 0.075, trousers), capsule(j[side + '_knee'], j[side + '_ankle'], 0.055, trousers));
    const a = j[side + '_ankle'];
    g.add(box([a[0], a[1] - 0.07, a[2] - 0.05], [0.09, 0.24, 0.08], shoe));
  }
  // What is held: a mustard cover, apart from the shirt, trousers and couch; a phone in its own dark case.
  const pageTexture = (() => {
    const c = document.createElement('canvas'); c.width = 128; c.height = 180;
    const x = c.getContext('2d'); x.fillStyle = '#f4efe2'; x.fillRect(0, 0, 128, 180); x.fillStyle = '#33302c';
    for (let y = 10; y < 172; y += 7) x.fillRect(8, y, 112 - (y % 21 === 3 ? 40 : 0), 3);
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
  })();
  const pages = new THREE.MeshStandardMaterial({ map: pageTexture, roughness: 0.8 }), cover = mat(0xd8c24a, 0.6), caseBack = mat(0x1c1d21, 0.4);
  const leaf = (centre, width, height, tiltDeg, yawDeg, face, thick = 0.012, back = cover) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(width, height, thick), [back, back, back, back, face, back]);
    m.position.set(...centre); m.rotation.order = 'ZXY';
    m.rotation.z = THREE.MathUtils.degToRad(yawDeg); m.rotation.x = THREE.MathUtils.degToRad(tiltDeg); return shadowed(m);
  };
  const c = mid(j.left_wrist, j.right_wrist); c[1] += BOOK.dy; c[2] += BOOK.dz;
  const tilt = BOOK.tilt + pose.tilt;
  if (person.held === 'book') {
    g.add(leaf([c[0] + BOOK.dx, c[1], c[2]], 0.15, 0.21, tilt, BOOK.yaw + pose.yaw, pages), leaf([c[0] - BOOK.dx, c[1], c[2]], 0.15, 0.21, tilt, -BOOK.yaw + pose.yaw, pages));
  } else if (person.held === 'phone') {
    const screen = new THREE.MeshStandardMaterial({ color: 0x101820, emissive: 0x5a7fa8, emissiveIntensity: 0.6 });
    g.add(leaf(c, 0.075, 0.155, tilt, pose.yaw, screen, 0.009, caseBack));
  }
  return g;
}
