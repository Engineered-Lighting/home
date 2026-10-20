// The WebGPU splat pipeline's GPU-free parts (?renderer=webgpu): the worker's
// PLY/SPZ parser, the radix sort's CPU reference and keys, the SH packing
// against Spark's, the worker loader, the adaptive-performance knobs and the
// modes.js splat tick. Synthetic splats only; no browser, GPU or household
// data. HOME_SPLAT_SH_PACK=<apartment.ply> adds a LOCAL-ONLY SH check on a
// real pack (aggregate numbers only).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { gzipSync } from 'node:zlib';
import { register } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SRC = path.join(ROOT, 'app/src');
const fileUrl = relative => pathToFileURL(path.join(SRC, relative)).href;
const threeURL = fileUrl('vendor/three-0.180.0/three.module.js');
register('data:text/javascript,' + encodeURIComponent(`export async function resolve(specifier, context, next) {
  if (specifier === 'three') return { url: ${JSON.stringify(threeURL)}, shortCircuit: true };
  return next(specifier, context);
}`));

const Parse = await import(fileUrl('home-3d/gaussian-splat-parse.mjs'));
const Synthetic = await import(fileUrl('home-3d/gaussian-splat-synthetic.mjs'));
const { radixSortIndices, radialKey16, radialKey32 } = await import(fileUrl('home-3d/gaussian-splat-sort-keys.mjs'));
const { PERFORMANCE_BUDGETS, PERFORMANCE_LEVEL_ORDER } = await import(fileUrl('home-3d/adaptive-performance.mjs'));

const synthetic = (n, options) => Synthetic.makeSplats(n, { seed: 3, ...options });

function covarianceReference(scale, quat) {
  const [x, y, z, w] = quat.map(v => v / Math.hypot(...quat));
  const R = [[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
    [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
    [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]];
  const S = (i, j) => R[i][0] * R[j][0] * scale[0] ** 2 + R[i][1] * R[j][1] * scale[1] ** 2 + R[i][2] * R[j][2] * scale[2] ** 2;
  return [S(0, 0), S(0, 1), S(0, 2), S(1, 1), S(1, 2), S(2, 2)];
}

test('PLY: centres, covariance, colour, opacity and channel-major SH become the packed layout', () => {
  const s = synthetic(500);
  const data = Parse.parsePly(Synthetic.toPly(s));
  assert.equal(data.count, 500); assert.equal(data.shDegree, 3); assert.equal(data.source, 'ply');
  for (const i of [0, 7, 123, 499]) {
    assert.deepEqual(Array.from(data.centers.subarray(i * 3, i * 3 + 3)), Array.from(s.pos.subarray(i * 3, i * 3 + 3)));
    const expected = covarianceReference(Array.from(s.scale.subarray(i * 3, i * 3 + 3)), Array.from(s.quat.subarray(i * 4, i * 4 + 4)));
    expected.forEach((value, k) => assert.ok(Math.abs(data.covariances[i * 6 + k] - value) <= 1e-6 * Math.max(1e-4, Math.abs(value)) + 1e-9, `cov ${i}.${k}`));
    for (let c = 0; c < 3; c++) {
      assert.ok(Math.abs(data.colors[i * 4 + c] - Math.max(0, Math.min(255, (0.5 + Parse.SH_C0 * s.sh[48 * i + c]) * 255))) <= 0.5 + 1e-6);
    }
    assert.ok(Math.abs(data.colors[i * 4 + 3] - s.opacity[i] * 255) <= 0.51);
    // Band 2, coefficient 1 (rest index 4), green: packed j = 1 * 3 + 1 in band 2.
    const sh2 = new Uint8Array(data.sh2.buffer);
    assert.equal(sh2[i * 16 + 4], Parse.encodeShByte(s.sh[48 * i + 3 * 5 + 1]));
    // Band 3, coefficient 6 (rest index 14), blue: j = 6 * 3 + 2.
    const sh3 = new Uint8Array(data.sh3.buffer);
    assert.equal(sh3[i * 24 + 20], Parse.encodeShByte(s.sh[48 * i + 3 * 15 + 2]));
  }
  // Bounds include each splat's 2-sigma extent (the scene's 500 splats are scaled up to cover the room).
  assert.ok(data.bounds.radius > 5, 'bounds cover the 8 x 6 m room');
  const dense = Parse.parsePly(Synthetic.toPly(synthetic(20000)));
  assert.ok(dense.bounds.radius > 5 && dense.bounds.radius < 9, 'dense bounds stay near the room: ' + dense.bounds.radius);
});

test('PLY: SH0 files (Echo) and non-float layouts parse; malformed files are refused', () => {
  const header = 'ply\nformat binary_little_endian 1.0\nelement vertex 2\n'
    + ['x', 'y', 'z', 'f_dc_0', 'f_dc_1', 'f_dc_2', 'opacity', 'scale_0', 'scale_1', 'scale_2', 'rot_0', 'rot_1', 'rot_2', 'rot_3']
      .map(name => `property ${name === 'opacity' ? 'double' : 'float'} ${name}\n`).join('') + 'end_header\n';
  const body = Buffer.alloc(2 * (13 * 4 + 8));
  let at = 0;
  for (let i = 0; i < 2; i++) for (const name of ['x', 'y', 'z', 'f_dc_0', 'f_dc_1', 'f_dc_2', 'opacity', 'scale_0', 'scale_1', 'scale_2', 'rot_0', 'rot_1', 'rot_2', 'rot_3']) {
    const value = { x: i, y: 2, z: 3, opacity: 0, rot_0: 1 }[name] ?? (name.startsWith('scale') ? Math.log(0.1) : 0);
    if (name === 'opacity') { body.writeDoubleLE(value, at); at += 8; } else { body.writeFloatLE(value, at); at += 4; }
  }
  const data = Parse.parsePly(Buffer.concat([Buffer.from(header), body]));
  assert.equal(data.shDegree, 0); assert.equal(data.sh1, null);
  assert.equal(data.colors[3], 128); assert.equal(data.centers[3], 1);
  assert.ok(Math.abs(data.covariances[0] - 0.01) < 1e-7);
  assert.throws(() => Parse.parsePly(Buffer.from('ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\nend_header\n0\n')), /splat_ply_format_unsupported/);
  assert.throws(() => Parse.parsePly(Buffer.from('ply\nformat binary_little_endian 1.0\nelement vertex 1\nproperty float x\nend_header\n')), /splat_ply_not_gaussian/);
  assert.throws(() => Parse.parsePly((b => b.subarray(0, Parse.parsePlyHeader(b).headerBytes + 100))(Synthetic.toPly(synthetic(10)))), /splat_ply_truncated/);
});

/* SPZ v2/v3 writer for the round trip, from the format the reader documents. */
function spz(s, version) {
  const n = s.N, rotationBytes = version === 3 ? 4 : 3;
  const out = Buffer.alloc(16 + n * 9 + n + n * 3 + n * 3 + n * rotationBytes + n * 45);
  out.writeUInt32LE(0x5053474e, 0); out.writeUInt32LE(version, 4); out.writeUInt32LE(n, 8); out[12] = 3; out[13] = 12;
  let at = 16;
  for (let i = 0; i < n; i++) for (let a = 0; a < 3; a++) { const v = Math.round(s.pos[i * 3 + a] * 4096); out.writeIntLE(v, at, 3); at += 3; }
  for (let i = 0; i < n; i++) out[at++] = Math.round(s.opacity[i] * 255);
  for (let i = 0; i < n; i++) for (let c = 0; c < 3; c++) out[at++] = Math.max(0, Math.min(255, Math.round(((s.sh[48 * i + c] * 0.15) + 0.5) * 255)));
  for (let i = 0; i < n; i++) for (let a = 0; a < 3; a++) out[at++] = Math.max(0, Math.min(255, Math.round((Math.log(s.scale[i * 3 + a]) + 10) * 16)));
  for (let i = 0; i < n; i++) {
    let q = Array.from(s.quat.subarray(i * 4, i * 4 + 4));
    if (version === 3) {
      const largest = q.reduce((best, v, k) => (Math.abs(v) > Math.abs(q[best]) ? k : best), 0);
      if (q[largest] < 0) q = q.map(v => -v);
      const others = [0, 1, 2, 3].filter(k => k !== largest);
      const enc = v => (v < 0 ? 512 : 0) | Math.round(Math.abs(v) / Math.SQRT1_2 * 511);
      const packed = (largest << 30) | (enc(q[others[0]]) << 20) | (enc(q[others[1]]) << 10) | enc(q[others[2]]);
      out.writeUInt32LE(packed >>> 0, at); at += 4;
    } else {
      if (q[3] < 0) q = q.map(v => -v);
      for (let a = 0; a < 3; a++) out[at++] = Math.round((q[a] + 1) * 127.5);
    }
  }
  for (let i = 0; i < n; i++) for (let k = 1; k < 16; k++) for (let c = 0; c < 3; c++) out[at++] = Parse.encodeShByte(s.sh[48 * i + 3 * k + c]);
  return out;
}

test('SPZ v2 and v3 round-trip, gunzipped like the worker; v4 and junk are refused', async () => {
  const s = synthetic(300);
  const reference = Parse.parsePly(Synthetic.toPly(s));
  for (const version of [2, 3]) {
    const data = await Parse.parseSplatFile(gzipSync(spz(s, version)), { gunzip: async bytes => new Uint8Array((await import('node:zlib')).gunzipSync(bytes)) });
    assert.equal(data.source, 'spz'); assert.equal(data.count, 300); assert.equal(data.shDegree, 3);
    for (let i = 0; i < 300; i += 37) {
      for (let a = 0; a < 3; a++) assert.ok(Math.abs(data.centers[i * 3 + a] - reference.centers[i * 3 + a]) <= 1 / 4096);
      const trace = k => data.covariances[k * 6] + data.covariances[k * 6 + 3] + data.covariances[k * 6 + 5];
      const refTrace = reference.covariances[i * 6] + reference.covariances[i * 6 + 3] + reference.covariances[i * 6 + 5];
      assert.ok(Math.abs(trace(i) / refTrace - 1) < 0.15, `v${version} covariance scale ${i}`);
      assert.deepEqual(Array.from(new Uint8Array(data.sh1.buffer, i * 12, 9)), Array.from(new Uint8Array(reference.sh1.buffer, i * 12, 9)));
    }
  }
  const v4 = Buffer.alloc(32); v4.writeUInt32LE(0x5053474e, 0); v4.writeUInt32LE(4, 4);
  await assert.rejects(Parse.parseSplatFile(v4), /splat_spz_version_unsupported/);
  await assert.rejects(Parse.parseSplatFile(Buffer.from('not a splat')), /splat_format_unsupported/);
});

test('radix sort: a stable ascending order; radial keys put the farthest splat first', () => {
  const keys = Uint32Array.from({ length: 5000 }, (_, i) => (i * 2654435761) % 977);
  const order = radixSortIndices(keys, 16);
  const expected = Array.from(keys.keys()).sort((a, b) => keys[a] - keys[b] || a - b);
  assert.deepEqual(Array.from(order), expected);
  const big = Uint32Array.from({ length: 2000 }, (_, i) => ((i * 2246822519) >>> 0) ^ 0x5bd1e995);
  assert.deepEqual(Array.from(radixSortIndices(big, 32)), Array.from(big.keys()).sort((a, b) => big[a] - big[b] || a - b));
  const distances = [0.3, 1.2, 1.2001, 5, 9.99];
  for (const [i, d] of distances.entries()) {
    if (!i) continue;
    assert.ok(radialKey32(d) < radialKey32(distances[i - 1]), 'farther is a smaller 32-bit key');
    assert.ok(radialKey16(d, 0.2, 10) <= radialKey16(distances[i - 1], 0.2, 10), 'farther is a smaller-or-equal 16-bit key');
  }
  assert.equal(radialKey16(0, 1, 2), 65535); assert.equal(radialKey16(9, 1, 2), 0);
  // 16-bit resolution over Victoria's depth range (bounds radius about 5.6 m).
  assert.ok(11.2 / 65535 < 0.0002, 'buckets are under 0.2 mm');
});

test('SH packing is at least as fine as Spark\'s on every band', () => {
  const s = synthetic(4000);
  const report = Parse.shQuantizationReport({ count: s.N, degree: 3, rest: (i, k) => s.sh[48 * i + 3 + k] });
  assert.equal(report.length, 3);
  for (const band of report) {
    assert.ok(band.samples > 0);
    assert.ok(band.home.max <= 1 / 256 + 1e-9, `band ${band.band}: home step error ${band.home.max}`);
    assert.ok(band.home.max <= band.spark.max + 1e-9, `band ${band.band}: home ${band.home.max} vs Spark ${band.spark.max}`);
    assert.ok(band.home.rms <= band.spark.rms, `band ${band.band}: rms`);
  }
  // In-range coefficients round-trip within half a step; out of range both clamp.
  assert.equal(Parse.homeShRoundTrip(2), 127 / 128);
  assert.equal(Parse.sparkShRoundTrip(2, 3), 1);
});

test('LOCAL ONLY: SH packing on a real pack', { skip: !process.env.HOME_SPLAT_SH_PACK }, () => {
  const bytes = fs.readFileSync(process.env.HOME_SPLAT_SH_PACK);
  const { headerBytes, elements } = Parse.parsePlyHeader(bytes);
  const vertex = elements.find(element => element.name === 'vertex');
  const names = Object.fromEntries(vertex.properties.map(p => [p.name, p.offset]));
  const restCount = vertex.properties.filter(p => p.name.startsWith('f_rest_')).length;
  const degree = [0, 9, 24, 45].indexOf(restCount);
  const view = new DataView(bytes.buffer, bytes.byteOffset + headerBytes);
  const stride = vertex.stride, perChannel = restCount / 3;
  const report = Parse.shQuantizationReport({ count: vertex.count, degree,
    rest: (i, k) => view.getFloat32(i * stride + names[`f_rest_${Math.floor(k / 3) + (k % 3) * perChannel}`], true) });
  console.log(JSON.stringify({ count: vertex.count, degree, report }));
  for (const band of report) assert.ok(band.home.max <= band.spark.max + 1e-9 && band.home.rms <= band.spark.rms);
});

test('adaptive performance carries splat knobs on every rung', async () => {
  const { splatBudget } = await import(fileUrl('home-3d/gaussian-splat-modes.mjs'));
  let previous = 3;
  for (const level of PERFORMANCE_LEVEL_ORDER) {
    const budget = PERFORMANCE_BUDGETS[level];
    assert.ok([16, 32].includes(budget.splatSortKeyBits), level);
    assert.ok(Number.isInteger(budget.splatMaxShDegree) && budget.splatMaxShDegree <= previous, level + ' sheds SH monotonically');
    previous = budget.splatMaxShDegree;
    assert.deepEqual(splatBudget(budget), { keyBits: budget.splatSortKeyBits, maxShDegree: budget.splatMaxShDegree });
  }
  assert.deepEqual(splatBudget(null), { keyBits: 16, maxShDegree: 3 });
  assert.deepEqual(splatBudget({ splatSortKeyBits: 24, splatMaxShDegree: 9 }), { keyBits: 16, maxShDegree: 3 });
});

test('the worker loader transfers the bytes, aborts by terminating, and never parses inline', async () => {
  globalThis.window ??= {};
  const { parseSplatInWorker } = await import(fileUrl('home-3d/gaussian-splat-loader.mjs'));
  const ply = Synthetic.toPly(synthetic(50));
  const workers = [];
  class FakeWorker {
    constructor(url, options) { this.url = String(url); this.options = options; this.terminated = false; workers.push(this); }
    postMessage(message, transfer) {
      this.transferred = transfer;
      if (this.hang) return;
      const bytes = message.bytes.slice(0);
      queueMicrotask(async () => { const data = Parse.parsePly(bytes); this.onmessage({ data: { id: message.id, data } }); });
    }
    terminate() { this.terminated = true; }
  }
  const buffer = ply.buffer.slice(ply.byteOffset, ply.byteOffset + ply.byteLength);
  const data = await parseSplatInWorker(buffer, { WorkerClass: FakeWorker });
  assert.equal(data.count, 50);
  assert.match(workers[0].url, /gaussian-splat-worker\.mjs/);
  assert.equal(workers[0].options.type, 'module');
  assert.deepEqual(workers[0].transferred, [buffer], 'the buffer is transferred, not copied');
  assert.equal(workers[0].terminated, true);
  class HangingWorker extends FakeWorker { constructor(...args) { super(...args); this.hang = true; } }
  const controller = new AbortController();
  const pending = parseSplatInWorker(new ArrayBuffer(8), { WorkerClass: HangingWorker, signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, { name: 'AbortError' });
  assert.equal(workers.at(-1).terminated, true);
  await assert.rejects(parseSplatInWorker(new ArrayBuffer(8), { WorkerClass: undefined }), /splat_worker_unavailable/);
  const modesSource = fs.readFileSync(path.join(SRC, 'home-3d/gaussian-splat-modes.mjs'), 'utf8');
  assert.ok(modesSource.includes('parseSplatInWorker') && !/parsePly|parseSplatFile/.test(modesSource), 'Photo parses only in the worker');
});

test('modes: the splat tick drives the node renderer\'s splat; Spark stays the WebGL default', async () => {
  globalThis.window ??= {};
  const THREE = await import(threeURL);
  const { createModes } = await import(fileUrl('home-3d/modes.js'));
  const calls = [];
  const fakeSplat = Object.assign(new THREE.Object3D(), {
    isHomeGaussianSplat: true, numSplats: 3, opacity: 0, _cutaway: {},
    setMaxShDegree: degree => calls.push(['sh', degree]),
    update: (renderer, camera) => { calls.push(['update', renderer, camera]); return { status: 'sorted' }; },
    colorHook: () => ({ available: true, count: 3 }),
    pipelineReadback: () => ({ renderer: 'home-gaussian-splat' }),
    dispose() { calls.push(['dispose']); },
  });
  const frame = { frame: 'z_up_metric_floor0', extents_m: [4, 4, 2.5], splat_baked: true, site: 'echo' };
  const renderer = { id: 'renderer' }, camera = new THREE.PerspectiveCamera();
  const policy = { enabled: false, center: [0, 0, 0], halfExtent: [1, 1, 1], direction: [0, 0], threshold: 0.8, feather: 0.03, floorZ: 0.25 };
  const modes = createModes({ apartmentRoot: new THREE.Group(), pointsMaterial: { uniforms: { uOpacity: { value: 1 } } },
    sim: false, assetCandidates: name => ['memory://' + name], fetchFrame: async () => frame, getPoints: () => null,
    scene: new THREE.Scene(), renderer, camera, siteId: 'echo', contentRevision: 'r1', requireRegistration: true,
    dollhouseCutaway: true,
    performanceController: { getBudget: () => PERFORMANCE_BUDGETS.low, getDiagnostics: () => ({ targetTier: 'desktop' }) },
    loadAdapters: {
      loadSplat: async ({ budget }) => { calls.push(['load', budget.id]); return fakeSplat; },
      createSplatCutaway: async ({ splat, dollhouseCutaway }) => ({ update: (...args) => calls.push(['cut', dollhouseCutaway, args[0].enabled]) }),
      createSparkRenderer: null,
    } });
  assert.equal(modes.tickSpark, modes.tickSplat, 'tickSpark is the splat tick');
  assert.equal(modes.tickSplat(camera, 0), null, 'no tick outside Photo');
  modes.setDollhouseCutaway(policy, new THREE.Matrix4());
  await modes.setMode('splat', { duration: 0 });
  assert.equal(fakeSplat.opacity, 1);
  calls.length = 0;
  const result = await modes.tickSplat(camera, 0);
  assert.deepEqual(result, { status: 'updated', sort: 'sorted' });
  assert.deepEqual(calls.map(call => call[0]), ['cut', 'sh', 'update'], 'cutaway commits before the sort');
  assert.deepEqual(calls[0], ['cut', true, false]);
  assert.deepEqual(calls[1], ['sh', PERFORMANCE_BUDGETS.low.splatMaxShDegree]);
  assert.equal(calls[2][1], renderer); assert.equal(calls[2][2], camera);
  const debug = modes.debugInfo();
  assert.equal(debug.numSplats, 3); assert.equal(debug.sparkVisible, null, 'no Spark renderer was created');
  assert.deepEqual(debug.splatPipeline, { renderer: 'home-gaussian-splat' });
  assert.deepEqual(modes.splatColorHook(), { available: true, count: 3 });
  await modes.setMode('points', { duration: 0 });
  assert.equal(modes.tickSplat(camera, 0), null);
  modes.dispose();
  assert.ok(calls.some(call => call[0] === 'dispose'));
  // The flag-off engine still builds Photo from Spark.
  const source = fs.readFileSync(path.join(SRC, 'home-3d/modes.js'), 'utf8');
  assert.match(source, /export async function loadSplatAsset[\s\S]*?import\('@sparkjsdev\/spark'\)[\s\S]*?new SplatMesh\(/, 'the default splat adapter builds a Spark SplatMesh');
  assert.ok(source.includes('new SparkRenderer({ renderer: rendererRef, autoUpdate: false })'), 'the default adapter builds a SparkRenderer');
});
