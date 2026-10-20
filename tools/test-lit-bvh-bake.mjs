// L5 bounce bake (lit-bvh-bake.mjs) on its CPU twin, with no GPU and no
// household data: the BVH against brute force, welded receivers, the exact
// per-triangle lightmap, the scripted two-room zero-leak test, linear layers,
// stale-job handling in the worker protocol, and lux at named aims against an
// offline path-traced reference (modeled vs modeled).
import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { twoRoomScene, roomALights, ROOM_A, ROOM_B } from './fixtures/lit-bake-scenes.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const Bake = await import(pathToFileURL(path.join(ROOT, 'app/src/home-3d/lit-bvh-bake.mjs')).href);

function bruteForce(triangles, o, d, tmax) {
  let best = tmax, tri = -1;
  for (let i = 0; i < triangles.length / 9; i++) {
    const a = triangles.subarray(i * 9, i * 9 + 3), e1 = [0, 1, 2].map(k => triangles[i * 9 + 3 + k] - a[k]), e2 = [0, 1, 2].map(k => triangles[i * 9 + 6 + k] - a[k]);
    const p = [d[1] * e2[2] - d[2] * e2[1], d[2] * e2[0] - d[0] * e2[2], d[0] * e2[1] - d[1] * e2[0]], det = e1[0] * p[0] + e1[1] * p[1] + e1[2] * p[2];
    if (Math.abs(det) <= 1e-9) continue;
    const s = [o[0] - a[0], o[1] - a[1], o[2] - a[2]], u = (s[0] * p[0] + s[1] * p[1] + s[2] * p[2]) / det;
    if (u < 0 || u > 1) continue;
    const q = [s[1] * e1[2] - s[2] * e1[1], s[2] * e1[0] - s[0] * e1[2], s[0] * e1[1] - s[1] * e1[0]], v = (d[0] * q[0] + d[1] * q[1] + d[2] * q[2]) / det;
    if (v < 0 || u + v > 1) continue;
    const t = (e2[0] * q[0] + e2[1] * q[1] + e2[2] * q[2]) / det;
    if (t > 1e-4 && t < best) { best = t; tri = i; }
  }
  return { t: best, tri };
}
const inRoom = (room, x, y, z, pad = 0) => x >= room.min[0] - pad && x <= room.max[0] + pad && y >= room.min[1] - pad && y <= room.max[1] + pad && z >= room.min[2] - pad && z <= room.max[2] + pad;

// Drive the worker protocol in-thread and collect its replies.
function host() {
  const endpoint = Bake.createLocalBakeEndpoint({ gpu: null, yieldTask: () => new Promise(resolve => setImmediate(resolve)) });
  const replies = [];
  const waiters = [];
  endpoint.addEventListener('message', event => {
    const waiter = waiters.find(w => w.test(event.data));
    if (waiter) { waiters.splice(waiters.indexOf(waiter), 1); waiter.resolve(event.data); } else replies.push(event.data);
  });
  const next = test => new Promise(resolve => { const found = replies.find(test); if (found) { replies.splice(replies.indexOf(found), 1); resolve(found); } else waiters.push({ test, resolve }); });
  const client = Bake.createBakeClient({ endpoint });
  return { endpoint, client, replies, next };
}
const lightsPayload = (lights, { exposure = 1, quality = 'cpu', aims = [], weightsOf = light => light.color.map(c => c * light.candela) } = {}) => ({
  lights: lights.map(({ key, mode, origin, direction, cosOuter, cosInner }) => ({ key, mode, origin, direction, cosOuter, cosInner })),
  weights: Object.fromEntries(lights.map(light => [light.key, weightsOf(light)])), exposure, quality, aims,
  luxLights: lights.filter(light => light.candela > 0) });

test('the binned SAH BVH finds the same closest hit as brute force', () => {
  const { triangles } = twoRoomScene({ step: 0.5 });
  const bvh = Bake.buildBvh(triangles);
  assert.ok(bvh.nodeCount > 1 && bvh.depth < Bake.BVH_STACK, 'the tree fits the WGSL traversal stack');
  const rnd = Bake.createRandom(5);
  for (let i = 0; i < 400; i++) {
    const o = [0.2 + rnd() * 7.8, 0.2 + rnd() * 3.6, 0.2 + rnd() * 2.2], z = rnd() * 2 - 1, a = rnd() * 2 * Math.PI, s = Math.sqrt(1 - z * z);
    const d = [Math.cos(a) * s, Math.sin(a) * s, z], hit = Bake.trace(bvh, ...o, ...d, 50), ref = bruteForce(triangles, o, d, 50);
    assert.equal(hit.tri === 0xffffffff, ref.tri < 0);
    if (ref.tri >= 0) { assert.ok(Math.abs(hit.t - ref.t) < 1e-5); assert.equal(bvh.order[hit.tri], ref.tri); }
    const any = Bake.trace(bvh, ...o, ...d, 50, true);
    assert.equal(any.tri === 0xffffffff, ref.tri < 0, 'any-hit agrees on occlusion');
  }
});

test('receivers weld shared corners and split at creases', () => {
  const { triangles } = twoRoomScene({ step: 1 });
  const r = Bake.buildReceivers(triangles);
  assert.equal(r.triangleCount, triangles.length / 9);
  assert.ok(r.receiverCount < r.triangleCount * 3 / 2, 'shared corners are one receiver');
  // A floor-wall corner is two receivers (90 degree crease), each with its face's normal.
  for (let i = 0; i < r.receiverCount; i++) {
    const n = r.receivers.subarray(i * 8 + 4, i * 8 + 7);
    assert.ok(Math.abs(Math.hypot(...n) - 1) < 1e-5);
    assert.ok(Math.max(...n.map(Math.abs)) > 0.999, 'axis-aligned faces keep axis-aligned normals: no averaging across a crease');
  }
});

test('half floats round-trip and the lightmap reproduces corner interpolation exactly', () => {
  const half = Bake.halfTableF32();
  for (const value of [0, 1, 0.5, 0.038, 123.456, 6e-5, 65504]) assert.ok(Math.abs(half[Bake.toHalf(value)] - value) <= Math.max(1e-7, Math.abs(value) * 1e-3), String(value));
  const layout = Bake.lightmapLayout(5);
  assert.equal(layout.width % 2, 0); assert.ok(layout.width * layout.height >= 5 * 4);
  const corners = new Uint32Array([0, 1, 2, 2, 1, 3, 0, 0, 0, 1, 1, 1, 3, 2, 1]), E = new Float32Array([1, 2, 3, 4, 5, 6, 0.5, 0.25, 0, 8, 8, 8]);
  const texels = Bake.fillLightmap(layout, corners, E, 0.5), uvs = Bake.lightmapUvs(layout);
  // Bilinear sample at any point of triangle 1 equals the barycentric mix of its corners.
  const sample = (u, v, c) => {
    const x = u * layout.width - 0.5, y = v * layout.height - 0.5, x0 = Math.floor(x), y0 = Math.floor(y), fx = x - x0, fy = y - y0;
    const at = (i, j) => half[texels[((y0 + j) * layout.width + x0 + i) * 4 + c]];
    return at(0, 0) * (1 - fx) * (1 - fy) + at(1, 0) * fx * (1 - fy) + at(0, 1) * (1 - fx) * fy + at(1, 1) * fx * fy;
  };
  for (const [wb, wc] of [[0, 0], [1, 0], [0, 1], [0.3, 0.3], [0.1, 0.8], [0.5, 0.5]]) {
    const wa = 1 - wb - wc, t = 1, uv = k => uvs.subarray(t * 6 + k * 2, t * 6 + k * 2 + 2);
    const u = wa * uv(0)[0] + wb * uv(1)[0] + wc * uv(2)[0], v = wa * uv(0)[1] + wb * uv(1)[1] + wc * uv(2)[1];
    for (let c = 0; c < 3; c++) {
      const expected = 0.5 * (wa * E[corners[3] * 3 + c] + wb * E[corners[4] * 3 + c] + wc * E[corners[5] * 3 + c]);
      assert.ok(Math.abs(sample(u, v, c) - expected) < 4e-3 * Math.max(1, expected), 'bilinear = barycentric');
    }
  }
});

test('two-room scripted test: bounce from room A never reaches room B (zero leak)', async () => {
  const scene = twoRoomScene();
  const receivers = Bake.buildReceivers(scene.triangles);
  const { client, next } = host();
  client.setScene(scene.triangles.slice(), scene.albedo.slice());
  const info = await next(m => m.type === 'scene');
  assert.equal(info.backend, 'cpu'); assert.equal(info.receivers, receivers.receiverCount);
  const lights = roomALights();
  client.update(lightsPayload(lights));
  const map = await next(m => m.type === 'lightmap' && m.final);
  const half = Bake.halfTableF32(), layout = info.layout;
  let litA = 0, maxB = 0, countB = 0;
  for (let t = 0; t < receivers.triangleCount; t++) {
    const tri = scene.triangles.subarray(t * 9, t * 9 + 9), cx = (tri[0] + tri[3] + tri[6]) / 3, cy = (tri[1] + tri[4] + tri[7]) / 3, cz = (tri[2] + tri[5] + tri[8]) / 3;
    const x = (t % layout.cellsPerRow) * 2, y = Math.floor(t / layout.cellsPerRow) * 2;
    let value = 0;
    for (const [i, j] of [[0, 0], [1, 0], [0, 1], [1, 1]]) for (let c = 0; c < 3; c++) value = Math.max(value, half[map.data[((y + j) * layout.width + x + i) * 4 + c]]);
    if (inRoom(ROOM_B, cx, cy, cz)) { countB++; maxB = Math.max(maxB, value); } else if (value > 0.5) litA++;
  }
  assert.ok(countB > 500, 'room B is fully tessellated'); assert.ok(litA > 500, 'room A receives bounce');
  assert.equal(maxB, 0, 'every texel of every room-B triangle is exactly zero');
  // The test is not vacuous: an unshadowed gather (instant radiosity's flaw)
  // would light room B's side of the wall.
  const bvh = Bake.buildBvh(scene.triangles, scene.albedo), spot = Bake.packLights([lights[0]]).data;
  const vpls = Bake.emitVplsCpu(bvh, spot, { count: 256 });
  let leaked = 0;
  for (let o = 0; o < vpls.length; o += 12) {
    if (!vpls[o + 3]) continue;
    const x = [4.2, 2, 1.2], n = [1, 0, 0], w = [vpls[o] - x[0], vpls[o + 1] - x[1], vpls[o + 2] - x[2]], r = Math.hypot(...w);
    const cx = (n[0] * w[0]) / r, cy = -(vpls[o + 4] * w[0] + vpls[o + 5] * w[1] + vpls[o + 6] * w[2]) / r;
    if (cx < 0 && cy > 0) leaked += Bake.luminance([vpls[o + 8], vpls[o + 9], vpls[o + 10]]) * -cx * cy / (Math.PI * r * r);
  }
  assert.ok(leaked > 0.5, 'without visibility the far side of the wall would get ' + leaked.toFixed(2) + ' lx');
});

test('layers are linear: re-weighting a slider equals re-baking at the new level, with no trace', async () => {
  const scene = twoRoomScene({ step: 0.5 });
  const { client, next } = host();
  client.setScene(scene.triangles.slice(), scene.albedo.slice()); await next(m => m.type === 'scene');
  const lights = roomALights();
  client.update(lightsPayload(lights));
  const first = await next(m => m.type === 'lightmap' && m.final);
  assert.ok(first.stats.jobs.some(job => job.kind === 'static' && job.lights === 7));
  assert.ok(first.stats.jobs.some(job => job.kind === 'spot'));
  client.update(lightsPayload(lights, { weightsOf: light => light.color.map(c => c * light.candela * (light.mode === 'vpl' ? 0.5 : 1)) }));
  const half = await next(m => m.type === 'lightmap' && m.final);
  assert.deepEqual(half.stats.jobs, [], 'a level change only re-weights the layers');
  const table = Bake.halfTableF32();
  // Spot-only difference: the second map's spot share is half the first's.
  client.update(lightsPayload(lights, { weightsOf: light => (light.mode === 'vpl' ? light.color.map(c => c * light.candela) : [0, 0, 0]) }));
  const spotOnly = await next(m => m.type === 'lightmap' && m.final);
  // Corner texels only: the fourth texel of a cell is clamped at zero, so it is linear only where it is positive.
  for (let i = 0; i < first.data.length; i += 4) {
    if (Math.floor(i / 4 / first.width) % 2 === 1 && (i / 4) % 2 === 1) continue;
    const full = table[first.data[i + 1]], halved = table[half.data[i + 1]], spot = table[spotOnly.data[i + 1]];
    assert.ok(Math.abs(halved - (full - spot / 2)) <= 2e-3 * Math.max(1, full), 'linear in the spot level');
  }
});

test('a moved spot re-bakes only its own layer; a newer aim replaces a stale job', async () => {
  const scene = twoRoomScene({ step: 0.5 });
  const { client, next, replies } = host();
  client.setScene(scene.triangles.slice(), scene.albedo.slice()); await next(m => m.type === 'scene');
  const lights = roomALights();
  client.update(lightsPayload(lights)); await next(m => m.type === 'lightmap' && m.final);
  // Two aims in a row: the first is stale before it finishes.
  const aimed = roomALights({ aim: [3, 3, 0] }), aimedAgain = roomALights({ aim: [0.8, 3.2, 0] });
  client.update(lightsPayload(aimed)); client.update(lightsPayload(aimedAgain));
  const final = await next(m => m.type === 'lightmap' && m.final);
  assert.deepEqual(final.stats.jobs.map(job => job.kind), ['spot'], 'downs and zones stay baked');
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(replies.filter(m => m.type === 'error').length, 0);
  // The result is the newest aim's, bit for bit (the tracer is deterministic).
  const fresh = host();
  fresh.client.setScene(scene.triangles.slice(), scene.albedo.slice()); await fresh.next(m => m.type === 'scene');
  fresh.client.update(lightsPayload(aimedAgain));
  const reference = await fresh.next(m => m.type === 'lightmap' && m.final);
  assert.deepEqual(final.data, reference.data, 'the stale aim left nothing behind');
});

/* Offline reference: Monte Carlo path tracing on the same scene model (same
 * BVH, lights, assumed albedos), many bounces, next-event estimation to
 * every light. Modeled vs modeled. */
function pathTraceIrradiance(bvh, lights, point, normal, { paths = 6000, depth = 6, seed = 99 } = {}) {
  const packed = Bake.packLights(lights), direct = Bake.directCpu(bvh, packed.data, packed.count, ...point, ...normal);
  const rnd = Bake.createRandom(seed), eps = Bake.LIT_BAKE_RECEIVER_OFFSET_M, sum = [0, 0, 0];
  for (let p = 0; p < paths; p++) {
    let x = point, n = normal, throughput = [1, 1, 1];
    for (let k = 0; k < depth; k++) {
      const t = Math.abs(n[0]) > 0.9 ? [0, 1, 0] : [1, 0, 0];
      let b = [n[1] * t[2] - n[2] * t[1], n[2] * t[0] - n[0] * t[2], n[0] * t[1] - n[1] * t[0]]; const bl = Math.hypot(...b); b = b.map(v => v / bl);
      const tt = [b[1] * n[2] - b[2] * n[1], b[2] * n[0] - b[0] * n[2], b[0] * n[1] - b[1] * n[0]];
      const r1 = rnd(), r2 = rnd(), phi = 2 * Math.PI * r1, sr = Math.sqrt(r2), cz = Math.sqrt(1 - r2);
      const d = [0, 1, 2].map(i => tt[i] * Math.cos(phi) * sr + b[i] * Math.sin(phi) * sr + n[i] * cz), dl = Math.hypot(...d);
      const o = x.map((v, i) => v + n[i] * eps), hit = Bake.trace(bvh, ...o, ...d.map(v => v / dl), 40);
      if (hit.tri === 0xffffffff) break;
      const tri = bvh.tris.subarray(hit.tri * 16, hit.tri * 16 + 16);
      let ny = [tri[5] * tri[10] - tri[6] * tri[9], tri[6] * tri[8] - tri[4] * tri[10], tri[4] * tri[9] - tri[5] * tri[8]]; const nl = Math.hypot(...ny); ny = ny.map(v => v / nl);
      if (ny[0] * d[0] + ny[1] * d[1] + ny[2] * d[2] > 0) ny = ny.map(v => -v);
      const y = o.map((v, i) => v + d[i] / dl * hit.t), rho = [tri[12], tri[13], tri[14]];
      throughput = throughput.map((v, i) => v * rho[i]);
      const e = Bake.directCpu(bvh, packed.data, packed.count, ...y, ...ny);
      for (let c = 0; c < 3; c++) sum[c] += throughput[c] * e[c];
      x = y; n = ny;
    }
  }
  const indirect = sum.map(v => v / paths);
  return { lux: Bake.luminance(direct) + Bake.luminance(indirect), direct: Bake.luminance(direct), indirect: Bake.luminance(indirect) };
}

test('lux at named aims, labelled modeled, is within 10 % of the offline path-traced reference', { timeout: 120000 }, async () => {
  const scene = twoRoomScene({ step: 0.25 });
  const lights = roomALights(), bvh = Bake.buildBvh(scene.triangles, scene.albedo);
  const aims = [{ id: 'pool', label: 'Spot pool', point: [1.2, 1.4, 0], normal: [0, 0, 1] },
    { id: 'wall', label: 'East wall', point: [3.99, 2.0, 1.2], normal: [-1, 0, 0] },
    { id: 'floor', label: 'Under the fixture', point: [2.2, 2.4, 0], normal: [0, 0, 1] },
    { id: 'other', label: 'Room B', point: [6, 2, 0], normal: [0, 0, 1] }];
  const { client, next } = host();
  client.setScene(scene.triangles.slice(), scene.albedo.slice()); await next(m => m.type === 'scene');
  client.update(lightsPayload(lights, { aims }));
  const lux = await next(m => m.type === 'lux');
  const rows = [];
  for (const aim of aims) {
    const live = lux.aims.find(row => row.id === aim.id), reference = pathTraceIrradiance(bvh, lights, aim.point, aim.normal);
    const oneBounce = pathTraceIrradiance(bvh, lights, aim.point, aim.normal, { depth: 1, seed: 5 });
    rows.push({ id: aim.id, modeled_lux: +live.lux.toFixed(2), reference_lux: +reference.lux.toFixed(2), one_bounce_reference: +oneBounce.lux.toFixed(2) });
    if (aim.id === 'other') { assert.equal(live.lux, 0); assert.equal(reference.lux, 0, 'no light reaches the closed room'); continue; }
    assert.ok(Math.abs(live.lux - reference.lux) <= 0.10 * reference.lux, `${aim.id}: ${live.lux.toFixed(1)} lx vs reference ${reference.lux.toFixed(1)} lx`);
    // The lightmap's own estimator (virtual point lights for the spot, a
    // hemisphere gather for the rest) against an independent one-bounce path trace.
    assert.ok(Math.abs(live.direct + live.bounce - oneBounce.lux) <= 0.05 * oneBounce.lux, `${aim.id}: one bounce matches a one-bounce path trace`);
    assert.ok(live.bounce > 0 && live.direct >= 0);
  }
  console.log('lux vs path-trace reference (modeled vs modeled): ' + JSON.stringify(rows));
});

test('the WGSL and the CPU twin share constants and kernels', () => {
  for (const entry of ['fn vplEmit', 'fn vplGather', 'fn skyGather', 'fn trace(']) assert.ok(Bake.LIT_BAKE_WGSL.includes(entry), entry);
  assert.match(Bake.LIT_BAKE_WGSL, new RegExp(`array<u32, ${Bake.BVH_STACK}>`));
  assert.ok((Bake.LIT_BAKE_WGSL.match(/var<storage/g) || []).length <= 8, 'at most 8 storage buffers per stage (the common iOS limit)');
  assert.equal(Bake.coneFalloff(Math.cos(0.5), Math.cos(0.2), Math.cos(0.1)), 1);
  assert.equal(Bake.coneFalloff(Math.cos(0.5), Math.cos(0.2), Math.cos(0.6)), 0);
});
