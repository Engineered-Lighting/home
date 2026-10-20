// L5 bounce bake on real WebGPU, in a module worker (lit-bvh-worker.mjs), on
// synthetic scenes only (no household data, no screenshots):
//   1. the scripted two-room test on the GPU: zero leak, and the WGSL kernels
//      agree with the CPU twin;
//   2. timing on a 174k-triangle room (Victoria's scan size) with 32 lights:
//      BVH build, the static bake and a spot re-bake, full and progressive.
// Without a WebGPU adapter (CI's headless Linux) the proof says so and checks
// the worker's CPU fallback instead. HOME_LIT_BAKE_GATE=1 also asserts the
// desktop budget (spot re-bake < 100 ms), for a run on the RTX 4090, e.g.
//   HOME_LIT_BAKE_CHANNEL=msedge HOME_LIT_BAKE_GATE=1 node --test tools/test-lit-bvh-bake-browser.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SERVED = { '/lit-bvh-bake.mjs': 'app/src/home-3d/lit-bvh-bake.mjs', '/lit-bvh-worker.mjs': 'app/src/home-3d/lit-bvh-worker.mjs',
  '/lit-bake-scenes.mjs': 'tools/fixtures/lit-bake-scenes.mjs' };
const PAGE = `<!doctype html><meta charset="utf-8"><title>lit bake proof</title><script type="module">
import * as Bake from './lit-bvh-bake.mjs';
import { twoRoomScene, roomALights, largeRoomScene, largeRoomLights, ROOM_B } from './lit-bake-scenes.mjs';
const payload = (lights, quality) => ({ quality, exposure: 1, aims: [],
  lights: lights.map(({ key, mode, origin, direction, cosOuter, cosInner }) => ({ key, mode, origin, direction, cosOuter, cosInner })),
  weights: Object.fromEntries(lights.map(light => [light.key, light.color.map(c => c * light.candela)])) });
function session(endpoint) {
  const waiters = [], seen = [];
  endpoint.addEventListener('message', event => { seen.push(event.data); const w = waiters.find(w => w.test(event.data)); if (w) { waiters.splice(waiters.indexOf(w), 1); w.resolve(event.data); } });
  const next = test => new Promise((resolve, reject) => { waiters.push({ test, resolve }); setTimeout(() => reject(new Error('bake timeout')), 120000); });
  return { client: Bake.createBakeClient({ endpoint }), next, seen };
}
async function bakeTwoRoom(endpoint) {
  const s = session(endpoint), scene = twoRoomScene();
  s.client.setScene(scene.triangles.slice(), scene.albedo.slice());
  const info = await s.next(m => m.type === 'scene');
  s.client.update(payload(roomALights(), 'full'));
  const map = await s.next(m => m.type === 'lightmap' && m.final);
  // Room B triangles: every texel of their cells.
  const half = Bake.halfTableF32(), layout = info.layout, tris = scene.triangles;
  let maxB = 0, sumA = 0;
  for (let t = 0; t < tris.length / 9; t++) {
    const cx = (tris[t * 9] + tris[t * 9 + 3] + tris[t * 9 + 6]) / 3, x = (t % layout.cellsPerRow) * 2, y = Math.floor(t / layout.cellsPerRow) * 2;
    for (const [i, j] of [[0, 0], [1, 0], [0, 1], [1, 1]]) for (let c = 0; c < 3; c++) {
      const v = half[map.data[((y + j) * layout.width + x + i) * 4 + c]];
      if (cx > ROOM_B.min[0]) maxB = Math.max(maxB, v); else sumA += v;
    }
  }
  return { info, map, maxB, sumA, warnings: s.seen.filter(m => m.type === 'warning' || m.type === 'error').map(m => m.message) };
}
window.proof = async () => {
  const adapter = navigator.gpu ? await navigator.gpu.requestAdapter({ powerPreference: 'high-performance' }) : null;
  const software = Bake.isSoftwareAdapter(adapter);
  const result = { webgpu: !!adapter, software, adapter: adapter?.info ? { vendor: adapter.info.vendor, architecture: adapter.info.architecture, description: adapter.info.description } : null };
  // A hardware GPU runs in the worker as in the app; a software adapter
  // (CI's SwiftShader) is refused there, so the WGSL runs in-page instead.
  const worker = new Worker('./lit-bvh-worker.mjs', { type: 'module' });
  const gpuRoom = await bakeTwoRoom(software ? Bake.createLocalBakeEndpoint({ gpu: navigator.gpu, allowSoftware: true }) : worker);
  const cpuRoom = await bakeTwoRoom(Bake.createLocalBakeEndpoint({ gpu: null }));
  result.twoRoom = { backend: gpuRoom.info.backend, maxB: gpuRoom.maxB, sumA: gpuRoom.sumA, cpuBackend: cpuRoom.info.backend, cpuMaxB: cpuRoom.maxB, cpuSumA: cpuRoom.sumA, warnings: gpuRoom.warnings };
  if (gpuRoom.info.backend === 'webgpu') {
    // The same kernels at the same budget on both tracers: compare receiver for receiver.
    const both = [gpuRoom, cpuRoom].map(r => { const half = Bake.halfTableF32(); return Array.from(r.map.data, v => half[v]); });
    let diff = 0, total = 0;
    for (let i = 0; i < both[0].length; i += 4) for (let c = 0; c < 3; c++) { diff += Math.abs(both[0][i + c] - both[1][i + c]); total += both[1][i + c]; }
    result.twoRoom.relativeL1 = diff / Math.max(1e-9, total);
  }
  if (software) { const workerRoom = await bakeTwoRoom(worker); result.twoRoom.workerBackend = workerRoom.info.backend; result.twoRoom.workerMaxB = workerRoom.maxB; }
  worker.terminate();
  // Scan-size timing needs a hardware GPU: a software one would take minutes.
  if (gpuRoom.info.backend !== 'webgpu' || software) return result;
  // Timing on the scan-sized room (WebGPU only: the CPU twin would take minutes).
  const big = session(new Worker('./lit-bvh-worker.mjs', { type: 'module' })), scene = largeRoomScene(), lights = largeRoomLights();
  big.client.setScene(scene.triangles, scene.albedo);
  const info = await big.next(m => m.type === 'scene');
  result.large = { triangles: info.triangles, receivers: info.receivers, nodes: info.nodes, depth: info.depth, buildMs: info.buildMs, backend: info.backend };
  const quality = 'full';
  let t0 = performance.now();
  big.client.update(payload(lights, quality));
  const first = await big.next(m => m.type === 'lightmap' && m.final);
  result.large.firstBakeWallMs = performance.now() - t0; result.large.firstJobs = first.stats.jobs;
  const reaims = [];
  for (let k = 0; k < 5; k++) {
    lights[0].direction = [Math.cos(k), Math.sin(k), -2].map((v, i, a) => v / Math.hypot(...a));
    t0 = performance.now();
    big.client.update(payload(lights, quality));
    const done = await big.next(m => m.type === 'lightmap' && m.final);
    reaims.push({ wallMs: performance.now() - t0, job: done.stats.jobs[0] });
  }
  result.large.spotRebake = reaims;
  if (info.backend === 'webgpu') {
    lights[0].direction = [0.3, 0.2, -1];
    t0 = performance.now();
    big.client.update(payload(lights, 'progressive'));
    const progressive = await big.next(m => m.type === 'lightmap' && m.final);
    result.large.progressive = { wallMs: performance.now() - t0, job: progressive.stats.jobs[0] };
  }
  return result;
};
window.ready = true;
</script>`;

function browserOptions() {
  const installed = fs.existsSync(chromium.executablePath());
  const args = ['--disable-background-networking', '--enable-unsafe-webgpu', '--ignore-gpu-blocklist'];
  if (process.platform === 'win32') args.unshift('--use-angle=d3d11');
  if (process.env.HOME_LIT_BAKE_SOFTWARE === '1') args.push('--use-webgpu-adapter=swiftshader', '--enable-unsafe-swiftshader');
  else if (process.platform === 'linux') args.unshift('--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--enable-features=Vulkan', '--use-webgpu-adapter=swiftshader');
  // HOME_LIT_BAKE_CHANNEL=msedge (or chrome) on Windows: the bundled Chromium
  // ships without dxil.dll, so its D3D12 WebGPU device cannot be created.
  const channel = process.env.HOME_LIT_BAKE_CHANNEL || (process.platform === 'linux' ? 'chromium' : !installed ? 'msedge' : null);
  return { headless: true, args, ...(channel ? { channel } : {}) };
}

test('the bounce bake runs on WebGPU in a worker: zero leak, the CPU twin agrees, and scan-size timing', { timeout: 600000 }, async () => {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname === '/') { res.writeHead(200, { 'Content-Type': 'text/html' }); res.end(PAGE); return; }
    const file = SERVED[url.pathname];
    if (!file) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { 'Content-Type': 'text/javascript', 'Cache-Control': 'no-store' }); res.end(fs.readFileSync(path.join(ROOT, file)));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch(browserOptions());
  try {
    const page = await browser.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error' && !/favicon|404/.test(message.text() + message.location()?.url)) errors.push(message.text()); });
    await page.goto('http://127.0.0.1:' + server.address().port + '/');
    await page.waitForFunction(() => window.ready === true);
    const result = await page.evaluate(() => window.proof());
    console.log('L5 bake proof: ' + JSON.stringify(result, null, 1));
    assert.deepEqual(errors, []);
    assert.equal(result.twoRoom.maxB, 0, 'zero leak on the ' + result.twoRoom.backend + ' tracer');
    assert.equal(result.twoRoom.cpuMaxB, 0, 'zero leak on the CPU twin');
    assert.ok(result.twoRoom.sumA > 0 && result.twoRoom.cpuSumA > 0);
    if (result.twoRoom.backend === 'webgpu') {
      assert.deepEqual(result.twoRoom.warnings, []);
      assert.ok(result.twoRoom.relativeL1 < 0.05, 'the WGSL kernels match the CPU twin within 5 % (L1 over the lightmap): ' + result.twoRoom.relativeL1);
      if (result.software) assert.equal(result.twoRoom.workerBackend, 'cpu', 'the app worker refuses a software adapter');
      else assert.equal(result.large.backend, 'webgpu');
      if (process.env.HOME_LIT_BAKE_GATE === '1' && !result.software) {
        const worst = Math.max(...result.large.spotRebake.map(row => row.job.ms));
        assert.ok(worst < 100, 'desktop gate: a spot re-bake under 100 ms (worst of 5: ' + worst.toFixed(1) + ' ms)');
      }
    } else {
      console.log('No WebGPU adapter in this browser: the worker fell back to the CPU twin; GPU parity and timing were not measured here.');
    }
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
});
