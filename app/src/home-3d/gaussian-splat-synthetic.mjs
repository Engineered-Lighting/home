/* home-3d/gaussian-splat-synthetic.mjs — a deterministic synthetic room of Gaussian splats.
 *
 * No household data: an 8 x 6 m room with 2.7 m walls, an open top and 24
 * furniture boxes, surface-aligned ~1.2 cm splats with SH3 and 8 % floaters,
 * optionally plus faint splats (opacity 0.05-0.3) like Victoria's v4 restore.
 * The WebGPU research spikes used this scene; the device check page
 * (webgpu-splat-check.html) and the splat proofs use it as a stand-in of
 * Victoria's size (307k / 370k). Y-up metres. toPly() writes the standard
 * 3DGS PLY, so it loads through the same worker parser as a real pack.
 */
export function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const ROOM = { x: 8, z: 6, h: 2.7 };
export function roomFaces(seed = 7) {
  const r = mulberry32(seed);
  const faces = []; // {o, u, v, n, color}
  const add = (o, u, v, n, color) => faces.push({ o, u, v, n, color, area: len(u) * len(v) });
  add([0, 0, 0], [ROOM.x, 0, 0], [0, 0, ROOM.z], [0, 1, 0], [0.55, 0.42, 0.30]); // floor
  add([0, 0, 0], [ROOM.x, 0, 0], [0, ROOM.h, 0], [0, 0, 1], [0.80, 0.78, 0.72]);
  add([0, 0, ROOM.z], [ROOM.x, 0, 0], [0, ROOM.h, 0], [0, 0, -1], [0.78, 0.80, 0.74]);
  add([0, 0, 0], [0, 0, ROOM.z], [0, ROOM.h, 0], [1, 0, 0], [0.74, 0.76, 0.80]);
  add([ROOM.x, 0, 0], [0, 0, ROOM.z], [0, ROOM.h, 0], [-1, 0, 0], [0.82, 0.76, 0.70]);
  for (let i = 0; i < 24; i++) {
    const sx = 0.4 + r() * 1.6, sz = 0.4 + r() * 1.2, sy = 0.4 + r() * 0.9;
    const x = 0.3 + r() * (ROOM.x - sx - 0.6), z = 0.3 + r() * (ROOM.z - sz - 0.6);
    const c = [0.2 + r() * 0.7, 0.2 + r() * 0.6, 0.2 + r() * 0.6];
    add([x, sy, z], [sx, 0, 0], [0, 0, sz], [0, 1, 0], c);
    add([x, 0, z], [sx, 0, 0], [0, sy, 0], [0, 0, -1], c);
    add([x, 0, z + sz], [sx, 0, 0], [0, sy, 0], [0, 0, 1], c);
    add([x, 0, z], [0, 0, sz], [0, sy, 0], [-1, 0, 0], c);
    add([x + sx, 0, z], [0, 0, sz], [0, sy, 0], [1, 0, 0], c);
  }
  return faces;
}
function len(a) { return Math.hypot(a[0], a[1], a[2]); }
function norm(a) { const l = len(a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; }
function cross(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }
function gauss(r) { let u = 0, v = 0; while (u === 0) u = r(); v = r(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); }
function matToQuat(m) { // m columns c0,c1,c2 -> quat [x,y,z,w]
  const [m00, m10, m20] = m[0], [m01, m11, m21] = m[1], [m02, m12, m22] = m[2];
  const tr = m00 + m11 + m22; let x, y, z, w;
  if (tr > 0) { const s = Math.sqrt(tr + 1) * 2; w = 0.25 * s; x = (m21 - m12) / s; y = (m02 - m20) / s; z = (m10 - m01) / s; }
  else if (m00 > m11 && m00 > m22) { const s = Math.sqrt(1 + m00 - m11 - m22) * 2; w = (m21 - m12) / s; x = 0.25 * s; y = (m01 + m10) / s; z = (m02 + m20) / s; }
  else if (m11 > m22) { const s = Math.sqrt(1 + m11 - m00 - m22) * 2; w = (m02 - m20) / s; x = (m01 + m10) / s; y = 0.25 * s; z = (m12 + m21) / s; }
  else { const s = Math.sqrt(1 + m22 - m00 - m11) * 2; w = (m10 - m01) / s; x = (m02 + m20) / s; y = (m12 + m21) / s; z = 0.25 * s; }
  return [x, y, z, w];
}
/**
 * N splats. faint: fraction of extra low-opacity splats (0.05-0.3), like the restored v4 set.
 * Splat scale shrinks with sqrt(307k/N) so screen coverage (overdraw) stays comparable.
 * Returns plain arrays: pos(3N), scale(3N, linear), quat(4N, xyzw), opacity(N), sh(48N: k-major rgb, k=0 is DC coeff).
 */
export function makeSplats(N, { seed = 1, faintFraction = 0 } = {}) {
  const r = mulberry32(seed);
  const faces = roomFaces();
  const total = faces.reduce((s, f) => s + f.area, 0);
  const cdf = []; let acc = 0; for (const f of faces) { acc += f.area / total; cdf.push(acc); }
  const pos = new Float32Array(3 * N), scale = new Float32Array(3 * N), quat = new Float32Array(4 * N);
  const opacity = new Float32Array(N), sh = new Float32Array(48 * N);
  const k = Math.sqrt(307382 / N);
  const C0 = 0.28209479177387814;
  const nFaint = Math.round(N * faintFraction);
  for (let i = 0; i < N; i++) {
    const volume = r() < 0.08;
    let p, n, color;
    if (volume) {
      p = [r() * ROOM.x, r() * ROOM.h, r() * ROOM.z]; n = norm([gauss(r), gauss(r), gauss(r)]); color = [0.6, 0.6, 0.6];
    } else {
      const u = r(); let fi = cdf.findIndex((c) => c >= u); if (fi < 0) fi = faces.length - 1;
      const f = faces[fi]; const a = r(), b = r(); const off = gauss(r) * 0.004;
      p = [0, 1, 2].map((j) => f.o[j] + f.u[j] * a + f.v[j] * b + f.n[j] * off); n = f.n; color = f.color;
    }
    pos.set(p, 3 * i);
    // tangent frame with random spin about normal
    const ref = Math.abs(n[1]) < 0.9 ? [0, 1, 0] : [1, 0, 0];
    let t1 = norm(cross(ref, n)); let t2 = cross(n, t1);
    const th = r() * Math.PI * 2, c = Math.cos(th), s = Math.sin(th);
    const a1 = [0, 1, 2].map((j) => t1[j] * c + t2[j] * s), a2 = [0, 1, 2].map((j) => -t1[j] * s + t2[j] * c);
    quat.set(matToQuat([a1, a2, n]), 4 * i);
    const big = r() < 0.03;
    const med = (big ? 0.06 : 0.012) * k * (volume ? 1.5 : 1);
    const s1 = med * Math.exp(gauss(r) * 0.5), s2 = med * Math.exp(gauss(r) * 0.5);
    scale[3 * i] = s1; scale[3 * i + 1] = s2; scale[3 * i + 2] = volume ? (s1 + s2) / 2 : Math.max(0.0005, 0.15 * Math.min(s1, s2));
    opacity[i] = i < nFaint ? 0.05 + 0.25 * r() : (volume ? 0.3 + 0.3 * r() : 0.3 + 0.7 * Math.sqrt(r()));
    const shade = 0.85 + 0.3 * r();
    for (let ch = 0; ch < 3; ch++) {
      const col = Math.min(1, color[ch] * shade + gauss(r) * 0.04);
      sh[48 * i + ch] = (col - 0.5) / C0;
    }
    for (let kk = 1; kk < 16; kk++) {
      const amp = kk < 4 ? 0.06 : kk < 9 ? 0.035 : 0.02;
      for (let ch = 0; ch < 3; ch++) sh[48 * i + 3 * kk + ch] = gauss(r) * amp;
    }
  }
  return { N, pos, scale, quat, opacity, sh };
}
/** Standard 3DGS binary PLY (what Spark and every viewer read). */
export function toPly(s) {
  const props = ['x', 'y', 'z', 'nx', 'ny', 'nz', 'f_dc_0', 'f_dc_1', 'f_dc_2'];
  for (let i = 0; i < 45; i++) props.push('f_rest_' + i);
  props.push('opacity', 'scale_0', 'scale_1', 'scale_2', 'rot_0', 'rot_1', 'rot_2', 'rot_3');
  const header = 'ply\nformat binary_little_endian 1.0\nelement vertex ' + s.N + '\n' +
    props.map((p) => 'property float ' + p).join('\n') + '\nend_header\n';
  const hb = new TextEncoder().encode(header);
  const stride = props.length; // 62
  const body = new Float32Array(s.N * stride);
  for (let i = 0; i < s.N; i++) {
    const o = i * stride;
    body[o] = s.pos[3 * i]; body[o + 1] = s.pos[3 * i + 1]; body[o + 2] = s.pos[3 * i + 2];
    for (let ch = 0; ch < 3; ch++) body[o + 6 + ch] = s.sh[48 * i + ch];
    for (let ch = 0; ch < 3; ch++) for (let kk = 1; kk < 16; kk++) body[o + 9 + ch * 15 + (kk - 1)] = s.sh[48 * i + 3 * kk + ch];
    const a = s.opacity[i]; body[o + 54] = Math.log(a / (1 - a));
    body[o + 55] = Math.log(s.scale[3 * i]); body[o + 56] = Math.log(s.scale[3 * i + 1]); body[o + 57] = Math.log(s.scale[3 * i + 2]);
    body[o + 58] = s.quat[4 * i + 3]; body[o + 59] = s.quat[4 * i]; body[o + 60] = s.quat[4 * i + 1]; body[o + 61] = s.quat[4 * i + 2];
  }
  const out = new Uint8Array(hb.length + body.byteLength);
  out.set(hb, 0); out.set(new Uint8Array(body.buffer), hb.length);
  return out;
}
/** Camera paths shared by both renderers. t in [0,1). Returns {eye, target}. */
export function cameraAt(path, t) {
  const cx = ROOM.x / 2, cz = ROOM.z / 2, a = t * Math.PI * 2;
  if (path === 'inside') return { eye: [cx + Math.cos(a) * 1.2, 1.5, cz + Math.sin(a) * 1.0], target: [cx + Math.cos(a + 1.6) * 3, 1.0, cz + Math.sin(a + 1.6) * 3] };
  // dollhouse overview: orbit at ~40 deg pitch, whole room on screen
  return { eye: [cx + Math.cos(a) * 8.5, 7.5, cz + Math.sin(a) * 8.5], target: [cx, 0.6, cz] };
}
export function lookAt(eye, target, up = [0, 1, 0]) { // column-major view matrix (world->view)
  const zaxis = norm([eye[0] - target[0], eye[1] - target[1], eye[2] - target[2]]);
  const xaxis = norm(cross(up, zaxis)); const yaxis = cross(zaxis, xaxis);
  const d = (a) => -(a[0] * eye[0] + a[1] * eye[1] + a[2] * eye[2]);
  return new Float32Array([xaxis[0], yaxis[0], zaxis[0], 0, xaxis[1], yaxis[1], zaxis[1], 0, xaxis[2], yaxis[2], zaxis[2], 0, d(xaxis), d(yaxis), d(zaxis), 1]);
}
