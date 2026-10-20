// Synthetic scenes for the L5 bounce bake (no household data): a scripted two-
// room test (two closed rooms behind a 0.2 m wall), and a 174k-triangle room
// the size of Victoria's scan for timing. Z up, metres, world space.
// Pure JS so the same scenes run in Node and in the browser proof.

/* A rectangle split into nu x nv quads; normal = u x v. */
function quad(out, albedo, origin, u, v, nu, nv, rho, jitter = null) {
  const at = (i, j) => [0, 1, 2].map(k => origin[k] + u[k] * i / nu + v[k] * j / nv);
  for (let j = 0; j < nv; j++) for (let i = 0; i < nu; i++) {
    let a = at(i, j), b = at(i + 1, j), c = at(i + 1, j + 1), d = at(i, j + 1);
    if (jitter) [a, b, c, d] = [a, b, c, d].map(p => jitter(p));
    out.push(...a, ...b, ...c, ...a, ...c, ...d);
    albedo.push(...rho, ...rho);
  }
}

/* A closed box room seen from inside: floor up, ceiling down, walls inward. */
export function roomBox(out, albedo, [x0, y0, z0], [x1, y1, z1], { step = 0.25, floor = [0.35, 0.33, 0.3], wall = [0.6, 0.6, 0.58], ceiling = [0.8, 0.8, 0.8], jitter = null } = {}) {
  const sx = x1 - x0, sy = y1 - y0, sz = z1 - z0, n = length => Math.max(1, Math.round(length / step));
  quad(out, albedo, [x0, y0, z0], [sx, 0, 0], [0, sy, 0], n(sx), n(sy), floor, jitter);          // floor, normal +z
  quad(out, albedo, [x0, y0, z1], [0, sy, 0], [sx, 0, 0], n(sy), n(sx), ceiling, jitter);        // ceiling, -z
  quad(out, albedo, [x0, y0, z0], [0, 0, sz], [sx, 0, 0], n(sz), n(sx), wall, jitter);           // y0 wall, +y
  quad(out, albedo, [x0, y1, z0], [sx, 0, 0], [0, 0, sz], n(sx), n(sz), wall, jitter);           // y1 wall, -y
  quad(out, albedo, [x0, y0, z0], [0, sy, 0], [0, 0, sz], n(sy), n(sz), wall, jitter);           // x0 wall, +x
  quad(out, albedo, [x1, y0, z0], [0, 0, sz], [0, sy, 0], n(sz), n(sy), wall, jitter);           // x1 wall, -x
}

export const ROOM_A = Object.freeze({ min: [0, 0, 0], max: [4, 4, 2.6] });
export const ROOM_B = Object.freeze({ min: [4.2, 0, 0], max: [8.2, 4, 2.6] });

/* Two rooms that share a 0.2 m wall and have no opening between them. */
export function twoRoomScene(options = {}) {
  const triangles = [], albedo = [];
  roomBox(triangles, albedo, ROOM_A.min, ROOM_A.max, options);
  roomBox(triangles, albedo, ROOM_B.min, ROOM_B.max, options);
  return { triangles: new Float32Array(triangles), albedo: new Float32Array(albedo) };
}

const cos = Math.cos;
/* Lights in room A only, in the shape Lit uses: a 20 deg FWHM spot (half
 * angle 16 deg, penumbra 0.55), a down light (1 rad, penumbra 1) and six
 * radial zones (0.95 rad), at a ceiling fixture. */
export function roomALights({ aim = [1.2, 1.4, 0] } = {}) {
  const origin = [2.2, 2.4, 2.45], d = aim.map((v, i) => v - origin[i]), l = Math.hypot(...d);
  const spotHalf = 16 * Math.PI / 180;
  const lights = [{ key: 'A:spot', mode: 'vpl', origin, direction: d.map(v => v / l), cosOuter: cos(spotHalf), cosInner: cos(spotHalf * 0.45), candela: 1880, color: [1, 0.92, 0.82] },
    { key: 'A:down', mode: 'sky', origin: [2.2, 2.4, 2.4], direction: [0, 0, -1], cosOuter: cos(1), cosInner: 1, candela: 120, color: [1, 0.95, 0.9] }];
  for (let k = 0; k < 6; k++) {
    const a = k * Math.PI / 3, v = [cos(a) * 2, Math.sin(a) * 2, -0.35], n = Math.hypot(...v);
    lights.push({ key: 'A:zone' + (k + 1), mode: 'sky', origin: [2.2 + v[0] / n * 0.05, 2.4 + v[1] / n * 0.05, 2.45 + v[2] / n * 0.05],
      direction: v.map(x => x / n), cosOuter: cos(0.95), cosInner: 1, candela: 30, color: [1, 0.97, 0.94] });
  }
  return lights;
}

/* A synthetic room of about `target` triangles with gently noisy surfaces and
 * 24 boxes of furniture, the size of Victoria's scan mesh (174,324). */
export function largeRoomScene(target = 174324) {
  const triangles = [], albedo = [];
  let seed = 3;
  const rnd = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
  const roomArea = 2 * (8 * 6) + 2 * (8 * 2.7) + 2 * (6 * 2.7), boxes = [];
  for (let i = 0; i < 24; i++) {
    const w = 0.4 + rnd() * 1.2, dpt = 0.4 + rnd() * 0.8, h = 0.3 + rnd() * 1.6, x = 0.3 + rnd() * (7.4 - w), y = 0.3 + rnd() * (5.4 - dpt);
    boxes.push([x, y, w, dpt, h]);
  }
  const boxArea = boxes.reduce((sum, [, , w, d, h]) => sum + 2 * (w * d + w * h + d * h), 0);
  const step = Math.sqrt((roomArea + boxArea) * 2 / target);
  // Noise is a function of position, so shared corners stay shared.
  const jitter = p => { const h = Math.sin(p[0] * 127.1 + p[1] * 311.7 + p[2] * 74.7) * 43758.5453; return [p[0], p[1], p[2] + (h - Math.floor(h) - 0.5) * 0.004]; };
  roomBox(triangles, albedo, [0, 0, 0], [8, 6, 2.7], { step, jitter });
  for (const [x, y, w, d, h] of boxes) {
    // Outward-facing furniture box (no bottom face).
    const t = [], a = [];
    roomBox(t, a, [x, y, 0], [x + w, y + d, h], { step, floor: [0.4, 0.3, 0.25], wall: [0.45, 0.35, 0.3], ceiling: [0.5, 0.4, 0.3] });
    // Flip winding so the box faces outward, and drop its floor.
    const floorTris = 2 * Math.max(1, Math.round(w / step)) * Math.max(1, Math.round(d / step));
    for (let i = floorTris; i < t.length / 9; i++) {
      triangles.push(...t.slice(i * 9, i * 9 + 3), ...t.slice(i * 9 + 6, i * 9 + 9), ...t.slice(i * 9 + 3, i * 9 + 6));
      albedo.push(...a.slice(i * 3, i * 3 + 3));
    }
  }
  return { triangles: new Float32Array(triangles), albedo: new Float32Array(albedo) };
}

/* Four fixtures x (spot + down + 6 zones) = 32 lights over the large room. */
export function largeRoomLights() {
  const fixtures = [[2, 1.5, 2.55], [6, 1.5, 2.55], [2, 4.5, 2.55], [6, 4.5, 2.55]], lights = [];
  const spotHalf = 16 * Math.PI / 180;
  fixtures.forEach((origin, f) => {
    const aim = [origin[0] + Math.cos(f) * 0.9, origin[1] + Math.sin(f) * 0.9, 0], d = aim.map((v, i) => v - origin[i]), l = Math.hypot(...d);
    lights.push({ key: f + ':spot', mode: 'vpl', origin, direction: d.map(v => v / l), cosOuter: cos(spotHalf), cosInner: cos(spotHalf * 0.45), candela: 1880, color: [1, 1, 1] });
    lights.push({ key: f + ':down', mode: 'sky', origin: [origin[0], origin[1], origin[2] - 0.05], direction: [0, 0, -1], cosOuter: cos(1), cosInner: 1, candela: 120, color: [1, 1, 1] });
    for (let k = 0; k < 6; k++) {
      const a = k * Math.PI / 3, v = [cos(a) * 2, Math.sin(a) * 2, -0.35], n = Math.hypot(...v);
      lights.push({ key: f + ':zone' + (k + 1), mode: 'sky', origin, direction: v.map(x => x / n), cosOuter: cos(0.95), cosInner: 1, candela: 30, color: [1, 1, 1] });
    }
  });
  return lights;
}
