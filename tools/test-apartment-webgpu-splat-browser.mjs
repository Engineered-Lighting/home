// The WebGPU splat pipeline (?renderer=webgpu) against Spark, in the browser,
// on the deterministic synthetic room only (no household data):
//
//   - the cutaway and exterior-cleanup TSL node against the CPU twins
//     dollhouseKeep() and splatExteriorKeep(), splat by splat;
//   - images against Spark 2.1 at fixed poses, with and without the cut;
//   - the GPU radix sort's order every frame (WebGPU backend only), and the
//     CPU fallback's order (WebGL2 backend);
//   - the Lit colour hook redirecting colour (WebGPU backend only).
//
// Frames are compared in memory; nothing is written unless
// HOME_SPLAT_PROOF_IMAGES names a local directory.
//
//   HOME_SPLAT_PROOF_BACKENDS  comma list; default 'webgpu-webgl' (CI: the node
//                              renderer's WebGL2 backend). Add 'webgpu' on a
//                              machine with an adapter.
//   HOME_SPLAT_PROOF_CHANNEL   Playwright browser channel ('chrome', 'msedge').
//   HOME_SPLAT_PROOF_SPLATS    synthetic splat count (default 40000).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { deflateSync } from 'node:zlib';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'), SRC = path.join(ROOT, 'app/src');
const backends = (process.env.HOME_SPLAT_PROOF_BACKENDS || 'webgpu-webgl').split(',').map(value => value.trim()).filter(Boolean);
assert.ok(backends.every(value => ['webgpu', 'webgpu-webgl'].includes(value)), 'backends are webgpu and/or webgpu-webgl');
const SPLATS = Number(process.env.HOME_SPLAT_PROOF_SPLATS) || 40000;
const imageDir = process.env.HOME_SPLAT_PROOF_IMAGES || null;
const W = 768, H = 432;

const MAPS = {
  spark: { three: '/vendor/three-0.180.0/three.module.js', 'three/addons/': '/vendor/three-0.180.0/addons/',
    '@sparkjsdev/spark': '/vendor/spark-2.1.0/spark.module.js' },
  node: { three: '/vendor/three-0.186.1/three.webgpu.js', 'three/webgpu': '/vendor/three-0.186.1/three.webgpu.js',
    'three/tsl': '/vendor/three-0.186.1/three.tsl.js', 'three/addons/': '/vendor/three-0.186.1/addons/' },
};
const page = map => `<!doctype html><html><head><meta charset="utf-8"><script type="importmap">${JSON.stringify({ imports: MAPS[map] })}</script></head>
<body style="margin:0;background:#000"><canvas id="c" width="${W}" height="${H}" style="width:${W}px;height:${H}px"></canvas>
<script type="module">window.__ready = true;</script></body></html>`;

function serve() {
  const types = { '.js': 'text/javascript', '.mjs': 'text/javascript' };
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://local');
    if (url.pathname === '/__proof/spark.html' || url.pathname === '/__proof/node.html') {
      response.writeHead(200, { 'content-type': 'text/html' });
      return response.end(page(url.pathname.includes('spark') ? 'spark' : 'node'));
    }
    if (url.pathname === '/favicon.ico') { response.writeHead(204); return response.end(); }
    const file = path.join(SRC, path.normalize(decodeURIComponent(url.pathname)).replace(/^([/\\])+/, ''));
    const allowed = file.startsWith(path.join(SRC, 'vendor')) || file.startsWith(path.join(SRC, 'home-3d'));
    if (!allowed || !/\.(?:m?js)$/.test(file) || !fs.existsSync(file)) { response.writeHead(404); return response.end(); }
    response.writeHead(200, { 'content-type': types[path.extname(file)], 'cache-control': 'no-store' });
    fs.createReadStream(file).pipe(response);
  });
  return new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(server)));
}

function browserOptions(webgpu) {
  const args = ['--disable-background-networking'];
  if (webgpu) args.push('--enable-unsafe-webgpu', '--ignore-gpu-blocklist');
  if (process.platform === 'win32') args.unshift('--use-angle=d3d11');
  else if (process.platform === 'linux') args.unshift('--use-gl=angle', '--use-angle=swiftshader-webgl', '--enable-unsafe-swiftshader');
  const channel = process.env.HOME_SPLAT_PROOF_CHANNEL || null;
  const installed = fs.existsSync(chromium.executablePath());
  return { headless: !webgpu, args, ...(channel ? { channel } : process.platform === 'linux' ? { channel: 'chromium' } : !installed ? { channel: 'msedge' } : {}) };
}

/* ---- in the page ---------------------------------------------------------- */

// Shared scene setup: the same splats, transform, cut and poses on both renderers.
const SCENE = String.raw`
const { makeSplats, toPly, cameraAt } = await import('/home-3d/gaussian-splat-synthetic.mjs');
const synthetic = makeSplats(options.splats, { seed: 5, faintFraction: 0.17 });
const ply = toPly(synthetic);
// world -> apartment: the room is Y-up, the apartment frame Z-up (as apartmentRoot).
const worldToApartment = [1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1];
const cut = { enabled: true, center: [4, -3, 1.35], halfExtent: [4, 3, 1.35], direction: [0, -1],
  threshold: 0.78, feather: 0.03, floorZ: 0.25 };
const metricCut = { ...cut, direction: [1, 0], metricShell: { axis: [1, 0], insetM: 0.4, featherM: 0.1 } };
const off = { ...cut, enabled: false };
const exterior = { minXY: [0.6, -5.4], maxXY: [7.4, -0.6] };
const poses = [
  { name: 'overview', ...cameraAt('overview', 0.123), policy: off, exterior: null },
  { name: 'inside', ...cameraAt('inside', 0.3), policy: off, exterior: null },
  { name: 'cutaway', ...cameraAt('overview', 0.3), policy: cut, exterior: null },
  { name: 'metric-cutaway+exterior', ...cameraAt('overview', 0.62), policy: metricCut, exterior },
];
const placeSplat = (object) => { object.position.set(0.3, 0, -0.2); object.rotation.y = 0.12; object.scale.setScalar(1.03); object.updateMatrixWorld(true); };
const placeCamera = (camera, pose) => { camera.position.fromArray(pose.eye); camera.lookAt(...pose.target); camera.aspect = ${W} / ${H}; camera.updateProjectionMatrix(); camera.updateMatrixWorld(); };
const encode = (rows) => { let t = ''; for (let i = 0; i < rows.length; i += 0x8000) t += String.fromCharCode(...rows.subarray(i, i + 0x8000)); return btoa(t); };
`;

async function sparkFrames(options) {
  const THREE = await import('three');
  const { SplatMesh, SparkRenderer } = await import('@sparkjsdev/spark');
  const { createSplatCutawayModifier } = await import('/home-3d/splat-cutaway.js');
  // eslint-disable-next-line no-eval
  /* SCENE */
  const renderer = new THREE.WebGLRenderer({ canvas: document.getElementById('c'), antialias: false, alpha: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(1); renderer.setSize(768, 432, false); renderer.setClearColor(0x000000, 1);
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(50, 768 / 432, 0.05, 200); scene.add(camera);
  const splat = new SplatMesh({ fileBytes: ply, fileName: 'synthetic.ply' }); await splat.initialized;
  placeSplat(splat); splat.frustumCulled = false; scene.add(splat);
  const modifier = await createSplatCutawayModifier(splat);
  const spark = new SparkRenderer({ renderer, autoUpdate: false }); spark.frustumCulled = false; camera.add(spark);
  const gl = renderer.getContext(), frames = [];
  for (const pose of poses) {
    modifier.update(pose.policy, worldToApartment, pose.exterior);
    placeCamera(camera, pose);
    for (let i = 0; i < 4; i++) { await spark.update({ scene, camera, time: 0 }); renderer.render(scene, camera); }
    const pixels = new Uint8Array(768 * 432 * 4); gl.readPixels(0, 0, 768, 432, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    const rows = new Uint8Array(pixels.length);
    for (let y = 0; y < 432; y++) rows.set(pixels.subarray((431 - y) * 768 * 4, (432 - y) * 768 * 4), y * 768 * 4);
    frames.push({ pose: pose.name, pixels: encode(rows) });
  }
  return { frames };
}

async function nodeProof(options) {
  const THREE = await import('three/webgpu');
  const TSL = await import('three/tsl');
  const { HomeGaussianSplat } = await import('/home-3d/gaussian-splat-webgpu.mjs');
  const { parseSplatInWorker } = await import('/home-3d/gaussian-splat-loader.mjs');
  const { createSplatCutawayNode } = await import('/home-3d/splat-cutaway-tsl.mjs');
  const { dollhouseKeep } = await import('/home-3d/dollhouse-cutaway.mjs');
  const { splatExteriorKeep } = await import('/home-3d/splat-exterior-cleanup.mjs');
  const { writeCovariance } = await import('/home-3d/gaussian-splat-parse.mjs');
  /* SCENE */
  const result = {};
  const renderer = new THREE.WebGPURenderer({ canvas: document.getElementById('c'), antialias: false, alpha: true, forceWebGL: options.forceWebGL });
  renderer.outputColorSpace = THREE.LinearSRGBColorSpace; renderer.toneMapping = THREE.NoToneMapping;
  await renderer.init(); renderer.setPixelRatio(1); renderer.setSize(768, 432, false); renderer.setClearColor(0x000000, 1);
  result.backend = renderer.backend.isWebGPUBackend ? 'webgpu' : 'webgl2';

  // 1. The keep node against the CPU twins, one random Gaussian per row.
  {
    const n = 8192;
    let seed = 99; const random = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296);
    const centre = new Float32Array(n * 4), covA = new Float32Array(n * 4), covB = new Float32Array(n * 4), cov = new Float32Array(6);
    const world = new THREE.Object3D(); placeSplat(world);
    const rows = [];
    for (let i = 0; i < n; i++) {
      const c = [random() * 9 - 0.5, random() * 3, random() * 7 - 0.5];
      const s = [0.002 + random() * 0.4, 0.002 + random() * 0.4, 0.001 + random() * 0.1];
      const q = new THREE.Quaternion(random() - 0.5, random() - 0.5, random() - 0.5, random() - 0.5).normalize();
      centre.set(c, i * 4);
      writeCovariance(cov, 0, s[0], s[1], s[2], q.x, q.y, q.z, q.w);
      covA.set(cov.subarray(0, 4), i * 4); covB[i * 4] = cov[4]; covB[i * 4 + 1] = cov[5];
      const centreWorld = new THREE.Vector3(...c).applyMatrix4(world.matrixWorld);
      const quatWorld = world.quaternion.clone().multiply(q);
      rows.push({ center: centreWorld.toArray(), scales: s.map(v => v * 1.03), quaternion: [quatWorld.x, quatWorld.y, quatWorld.z, quatWorld.w] });
    }
    const attribute = (array, size) => new THREE.StorageInstancedBufferAttribute(array, size);
    const centreAttribute = attribute(centre, 4), covAAttribute = attribute(covA, 4), covBAttribute = attribute(covB, 4);
    const out = attribute(new Float32Array(n), 1);
    const node = createSplatCutawayNode();
    const kernel = TSL.Fn(() => {
      const i = TSL.instanceIndex;
      TSL.storage(out, 'float', n).element(i).assign(node.keep(TSL.storage(centreAttribute, 'vec4', n).element(i).xyz,
        TSL.storage(covAAttribute, 'vec4', n).element(i), TSL.storage(covBAttribute, 'vec4', n).element(i)));
    })().compute(n);
    const checks = [];
    for (const pose of poses) {
      node.update(pose.policy, worldToApartment, pose.exterior, world.matrixWorld);
      renderer.compute(kernel);
      const gpu = new Float32Array(await renderer.getArrayBufferAsync(out));
      let worst = 0, mismatches = 0, kept = 0, cutCount = 0;
      const toApartment = new THREE.Matrix4().fromArray(worldToApartment);
      for (let i = 0; i < n; i++) {
        const local = new THREE.Vector3(...rows[i].center).applyMatrix4(toApartment).toArray();
        const cpu = dollhouseKeep(local, pose.policy) * (pose.exterior ? splatExteriorKeep(rows[i], pose.exterior, worldToApartment) : 1);
        const diff = Math.abs(gpu[i] - cpu);
        if (diff > 2e-3) mismatches++; else worst = Math.max(worst, diff);
        if (cpu > 0.999) kept++; if (cpu < 0.001) cutCount++;
      }
      checks.push({ pose: pose.name, n, mismatches, worstMatched: worst, kept, cut: cutCount });
    }
    result.keep = checks;
  }

  // 2. Frames at the shared poses, through the worker parser like the app.
  const data = await parseSplatInWorker(ply.buffer.slice(0));
  const cutaway = createSplatCutawayNode();
  const splat = new HomeGaussianSplat(data, { renderer, keyBits: 16, cutaway });
  placeSplat(splat);
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(50, 768 / 432, 0.05, 200);
  camera.coordinateSystem = renderer.coordinateSystem; camera.updateProjectionMatrix();
  scene.add(splat);
  const target = new THREE.RenderTarget(768, 432);
  const capture = async () => {
    renderer.setRenderTarget(target); renderer.render(scene, camera); renderer.setRenderTarget(null);
    const pixels = await renderer.readRenderTargetPixelsAsync(target, 0, 0, 768, 432);
    const rows = new Uint8Array(768 * 432 * 4);
    if (result.backend === 'webgpu') rows.set(pixels.subarray(0, rows.length));
    else for (let y = 0; y < 432; y++) rows.set(pixels.subarray((431 - y) * 768 * 4, (432 - y) * 768 * 4), y * 768 * 4);
    return rows;
  };
  result.frames = [];
  for (const pose of poses) {
    cutaway.update(pose.policy, worldToApartment, pose.exterior, splat.matrixWorld);
    placeCamera(camera, pose);
    splat.update(renderer, camera);
    result.frames.push({ pose: pose.name, pixels: encode(await capture()) });
  }

  // 3. Sort order: every frame of a scripted orbit.
  cutaway.update(off, worldToApartment, null, splat.matrixWorld);
  if (result.backend === 'webgpu') {
    result.sort = [];
    for (const keyBits of [16, 32]) {
      const sorted = new HomeGaussianSplat(data, { renderer, keyBits });
      placeSplat(sorted);
      const sortScene = new THREE.Scene(); sortScene.add(sorted);
      const before = sorted.pipelineReadback().counts.sorts;
      const checks = [];
      for (let frame = 0; frame < 24; frame++) {
        placeCamera(camera, { ...cameraAt(frame % 2 ? 'inside' : 'overview', frame / 24) });
        sorted.update(renderer, camera);
        renderer.render(sortScene, camera);
        if (frame % 4 === 3) checks.push(await sorted.verifySortAsync(renderer));
      }
      result.sort.push({ keyBits, sortsPerFrame: (sorted.pipelineReadback().counts.sorts - before) / 24, checks });
      sorted.dispose();
    }
  } else {
    // The fallback's CPU order is non-increasing in distance (16-bit radial key).
    placeCamera(camera, { ...cameraAt('inside', 0.4) });
    splat.update(renderer, camera);
    // three pads the PBO-backed array; the draw reads the first `count` entries.
    const order = splat._cpuOrder.array.subarray(0, data.count), eye = new THREE.Vector3().setFromMatrixPosition(camera.matrixWorld).applyMatrix4(splat.matrixWorld.clone().invert());
    const keys = Array.from(order, i => Math.hypot(data.centers[i * 3] - eye.x, data.centers[i * 3 + 1] - eye.y, data.centers[i * 3 + 2] - eye.z));
    // Back to front within one 16-bit bucket of the sort's own depth range.
    const bucket = 2 * data.bounds.radius / 65535;
    let violations = 0;
    for (let i = 1; i < keys.length; i++) if (keys[i] > keys[i - 1] + bucket * 1.01) violations++;
    result.cpuSort = { count: keys.length, permutation: new Set(order).size === data.count, violations, sorts: splat.pipelineReadback().counts.cpuSorts };
  }

  // 4. The Lit colour hook: a relit buffer replaces colour, and switches back.
  const hook = splat.colorHook();
  result.colorHook = { available: hook.available };
  if (hook.available) {
    placeCamera(camera, poses[0]); splat.update(renderer, camera);
    const mean = rows => { const sum = [0, 0, 0]; let count = 0; for (let i = 0; i < rows.length; i += 4) { if (rows[i + 3] === 0 && !rows[i] && !rows[i + 1]) continue; for (let c = 0; c < 3; c++) sum[c] += rows[i + c]; count++; } return sum.map(v => v / Math.max(1, count)); };
    const capture0 = mean(await capture());
    renderer.compute(TSL.Fn(() => { hook.write.element(TSL.instanceIndex).assign(TSL.vec4(1, 0, 0, 0)); })().compute(hook.count));
    hook.setActive(true);
    const relit = mean(await capture());
    hook.setActive(false);
    const restored = mean(await capture());
    result.colorHook = { available: true, capture: capture0, relit, restored };
  }
  splat.dispose();
  return result;
}

function compare(a, b) {
  let covered = 0, sum = 0, over16 = 0, over48 = 0;
  for (let i = 0; i < a.length; i += 4) {
    const ma = Math.max(a[i], a[i + 1], a[i + 2]), mb = Math.max(b[i], b[i + 1], b[i + 2]);
    if (ma < 2 && mb < 2) continue;
    covered++;
    let d = 0; for (let c = 0; c < 3; c++) d = Math.max(d, Math.abs(a[i + c] - b[i + c]));
    sum += d; if (d > 16) over16++; if (d > 48) over48++;
  }
  return { covered, mean: covered ? sum / covered : 0, over16: covered ? over16 / covered : 0, over48: covered ? over48 / covered : 0 };
}

function png(pixels) {
  const raw = Buffer.alloc((W * 4 + 1) * H);
  for (let y = 0; y < H; y++) { raw[y * (W * 4 + 1)] = 0; pixels.copy(raw, y * (W * 4 + 1) + 1, y * W * 4, (y + 1) * W * 4); }
  const chunk = (type, data) => {
    const length = Buffer.alloc(4); length.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type), data]);
    let crc = ~0; for (const byte of body) { crc ^= byte; for (let k = 0; k < 8; k++) crc = (crc >>> 1) ^ (0xEDB88320 & -(crc & 1)); }
    const tail = Buffer.alloc(4); tail.writeUInt32BE(~crc >>> 0); return Buffer.concat([length, body, tail]);
  };
  const header = Buffer.alloc(13); header.writeUInt32BE(W, 0); header.writeUInt32BE(H, 4); header[8] = 8; header[9] = 6;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}

async function runInPage(browser, origin, kind, fn, options) {
  const tab = await browser.newPage({ viewport: { width: W, height: H } });
  const errors = [];
  tab.on('pageerror', error => errors.push(error.message));
  tab.on('console', message => { if (message.type() === 'error') errors.push(message.text().slice(0, 400)); });
  tab.on('response', response => { if (response.status() >= 400) errors.push(`${response.status()} ${new URL(response.url()).pathname}`); });
  await tab.goto(`${origin}/__proof/${kind}.html`);
  await tab.waitForFunction(() => window.__ready === true);
  // The function bodies share SCENE; Playwright serialises them as source.
  const source = fn.toString().replace('/* SCENE */', SCENE).replace('// eslint-disable-next-line no-eval', '');
  const result = await tab.evaluate(`(${source})(${JSON.stringify(options)})`);
  await tab.close();
  return { ...result, errors };
}

test('the WebGPU splat pipeline matches Spark and the CPU twins', { timeout: 900000 }, async () => {
  const server = await serve();
  const origin = `http://127.0.0.1:${server.address().port}`;
  try {
    const reference = await (async () => {
      const browser = await chromium.launch(browserOptions(false));
      try { return await runInPage(browser, origin, 'spark', sparkFrames, { splats: SPLATS }); } finally { await browser.close(); }
    })();
    assert.deepEqual(reference.errors, [], 'Spark page errors');
    assert.equal(reference.frames.length, 4);
    for (const backend of backends) {
      const browser = await chromium.launch(browserOptions(backend === 'webgpu'));
      let result;
      try { result = await runInPage(browser, origin, 'node', nodeProof, { splats: SPLATS, forceWebGL: backend !== 'webgpu' }); }
      finally { await browser.close(); }
      assert.deepEqual(result.errors, [], backend + ' page errors');
      assert.equal(result.backend, backend === 'webgpu' ? 'webgpu' : 'webgl2', 'the requested backend ran');

      for (const row of result.keep) {
        console.log(`${backend} keep ${row.pose}: ${row.n} splats, kept ${row.kept}, cut ${row.cut}, mismatches ${row.mismatches}, worst matched ${row.worstMatched.toExponential(2)}`);
        assert.ok(row.mismatches <= row.n * 0.002, `${row.pose}: ${row.mismatches} keep values differ from the CPU twins`);
        if (row.pose !== 'overview' && row.pose !== 'inside') assert.ok(row.cut > 100 && row.kept > 100, row.pose + ' exercises both sides of the cut');
      }

      for (const [index, frame] of result.frames.entries()) {
        const spark = Buffer.from(reference.frames[index].pixels, 'base64'), node = Buffer.from(frame.pixels, 'base64');
        const diff = compare(spark, node);
        console.log(`${backend} vs Spark, ${frame.pose}: covered ${diff.covered}, mean ${diff.mean.toFixed(2)}, >16 ${(diff.over16 * 100).toFixed(2)}%, >48 ${(diff.over48 * 100).toFixed(2)}%`);
        if (imageDir) {
          fs.mkdirSync(imageDir, { recursive: true });
          fs.writeFileSync(path.join(imageDir, `${frame.pose}-spark.png`), png(spark));
          fs.writeFileSync(path.join(imageDir, `${frame.pose}-${backend}.png`), png(node));
        }
        assert.ok(diff.covered > 5000, frame.pose + ': the room covers the frame');
        assert.ok(diff.mean <= 4, `${frame.pose}: mean difference ${diff.mean.toFixed(2)}/255`);
        assert.ok(diff.over48 <= 0.01, `${frame.pose}: ${(diff.over48 * 100).toFixed(2)}% differ by more than 48/255`);
      }

      if (backend === 'webgpu') {
        for (const run of result.sort) {
          console.log(`webgpu sort ${run.keyBits}-bit: ${run.sortsPerFrame} sorts/frame, checks ${JSON.stringify(run.checks.map(c => [c.visible, c.violations]))}`);
          assert.equal(run.sortsPerFrame, 1, 'sorted every frame');
          for (const check of run.checks) { assert.equal(check.violations, 0); assert.ok(check.visible > 0); }
        }
        const { capture, relit, restored } = result.colorHook;
        console.log('webgpu colour hook', JSON.stringify(result.colorHook));
        assert.ok(relit[0] > 3 * relit[1] && relit[0] > 3 * relit[2], 'relit colour replaces the capture colour');
        assert.ok(capture.every((value, c) => Math.abs(value - restored[c]) < 1), 'switching back restores the capture colour');
      } else {
        console.log(`webgl2 CPU sort: ${JSON.stringify(result.cpuSort)}; colour hook ${JSON.stringify(result.colorHook)}`);
        assert.equal(result.cpuSort.violations, 0);
        assert.equal(result.cpuSort.permutation, true, 'the CPU order is a permutation of every splat');
        assert.ok(result.cpuSort.sorts >= 1);
        assert.equal(result.colorHook.available, false, 'the WebGL2 fallback declares relit splats unavailable');
      }
    }
  } finally {
    server.closeAllConnections(); server.close();
  }
});
