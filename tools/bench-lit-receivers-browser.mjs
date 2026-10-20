#!/usr/bin/env node
// L4 frame cost at Victoria's scale, on synthetic data only: 370,001 splats
// (v4-floor's count) and 876,773 points in the authored 4 x 4 x 2.5 m room,
// with an analytic lighting layer for both. No household pack is read.
//
// For each renderer it measures, in Photo and in Cloud:
//   unlit       rAF interval and renderer.render CPU time, static camera
//   lit steady  the same with Lit on and nothing changing (the blend is idle)
//   lit moving  the same while one light's level changes every frame (a blend
//               every frame: the worst case, e.g. dragging a slider)
//   frame       the HDR frame's bloom + display passes alone, waited on
//   blend       the blend pass alone, waited on: gl.finish (WebGL) or
//               queue.onSubmittedWorkDone (WebGPU); an upper bound on GPU time
//   spot column the worker time to re-bake one spot's receiver column
//
// Local only (it needs a GPU; WebGPU needs a channel with an adapter):
//   node tools/bench-lit-receivers-browser.mjs [--renderer webgl|webgpu] [--channel msedge]
// Comparable 4090 GPU gate, isolated to the actual blend at 370,001 splats:
//   node tools/bench-lit-receivers-browser.mjs --renderer webgpu --channel msedge --modes splat --points 1 --gpu-timestamps --timestamp-only
// Timestamp mode uses 100 GPU samples, each after a 256 MiB cache flush.
// Unsupported timestamps, another adapter or fewer samples remain unmeasured.
// Writes .tmp/bench-lit-receivers/<run>/bench.json.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import Core from '../app/src/home-equipment-registration.js';
import { createEquipmentRegistrationGateway, initRegistration, readActiveBinding } from '../web-gateway/equipment-registration.mjs';
import { installPack, authoredDocument } from './fixtures/apartment-home-shell-fixtures.mjs';
import { authoredLightingLayer } from './fixtures/apartment-lit-layer-fixtures.mjs';
import { measureLitWebGpuTimestamps, classifyLitWebGpuGate } from './lib/lit-webgpu-timestamp-benchmark.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'), SRC = path.join(ROOT, 'app/src');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const arg = (name, fallback) => { const i = process.argv.indexOf('--' + name); return i > 0 ? process.argv[i + 1] : fallback; };
const RENDERER = arg('renderer', 'webgl'), CHANNEL = arg('channel', RENDERER === 'webgpu' ? 'msedge' : null);
const SPLATS = Number(arg('splats', 370001)), POINTS = Number(arg('points', 876773)), MODES = arg('modes', 'splat,points').split(',');
const GPU_TIMESTAMPS = process.argv.includes('--gpu-timestamps'), TIMESTAMP_ONLY = process.argv.includes('--timestamp-only');
const TIMESTAMP_ITERATIONS = Number(arg('timestamp-iterations', 100));
const BENCH_STARTED = Date.now();
assert.ok(['webgl', 'webgpu'].includes(RENDERER) && MODES.length && MODES.every(mode => ['splat', 'points'].includes(mode)));
assert.ok([SPLATS, POINTS].every(count => Number.isInteger(count) && count > 0 && count <= 1000000), 'receiver counts stay bounded');
assert.ok(!GPU_TIMESTAMPS || RENDERER === 'webgpu', 'GPU timestamps require the WebGPU renderer');
assert.ok(!TIMESTAMP_ONLY || GPU_TIMESTAMPS, '--timestamp-only requires --gpu-timestamps');
assert.ok(Number.isInteger(TIMESTAMP_ITERATIONS) && TIMESTAMP_ITERATIONS > 0 && TIMESTAMP_ITERATIONS <= 200);

// Deterministic receivers on the authored room's floor and two walls.
function surfaceRows(count, seed) {
  let state = seed >>> 0;
  const random = () => ((state = (Math.imul(state, 1664525) + 1013904223) >>> 0) / 4294967296);
  const rows = [];
  for (let i = 0; i < count; i++) {
    const pick = random();
    if (pick < 0.5) rows.push({ p: [random() * 4, random() * 4, 0.001], n: [0, 0, 1] });
    else if (pick < 0.75) rows.push({ p: [random() * 4, 3.999, random() * 2.5], n: [0, -1, 0] });
    else rows.push({ p: [0.001, random() * 4, random() * 2.5], n: [1, 0, 0] });
  }
  return rows;
}
function splatPly(rows) {
  const names = ['x', 'y', 'z', 'nx', 'ny', 'nz', 'f_dc_0', 'f_dc_1', 'f_dc_2', 'opacity', 'scale_0', 'scale_1', 'scale_2', 'rot_0', 'rot_1', 'rot_2', 'rot_3'];
  const header = Buffer.from('ply\nformat binary_little_endian 1.0\nelement vertex ' + rows.length + '\n' + names.map(n => 'property float ' + n + '\n').join('') + 'end_header\n');
  const body = Buffer.alloc(rows.length * names.length * 4);
  rows.forEach(({ p }, i) => [...p, 0, 0, 0, .8, .4, .1, 3, Math.log(.02), Math.log(.02), Math.log(.005), 1, 0, 0, 0]
    .forEach((value, j) => body.writeFloatLE(value, (i * names.length + j) * 4)));
  return Buffer.concat([header, body]);
}
function pointsPly(rows) {
  const header = Buffer.from('ply\nformat binary_little_endian 1.0\ncomment homeapt-points v1\nelement vertex ' + rows.length
    + '\nproperty float x\nproperty float y\nproperty float z\nproperty uchar intensity\nend_header\n');
  const body = Buffer.alloc(rows.length * 13);
  rows.forEach(({ p }, i) => { p.forEach((value, j) => body.writeFloatLE(value, i * 13 + j * 4)); body[i * 13 + 12] = 200; });
  return Buffer.concat([header, body]);
}
function publicSources(root) {
  const result = new Map(), excluded = new Set(['node_modules', 'data', 'artifacts', 'private', 'recordings', 'logs']);
  (function visit(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (entry.name.startsWith('.') || excluded.has(entry.name)) continue;
      const file = path.join(directory, entry.name), relative = path.relative(root, file).split(path.sep).join('/');
      if (relative.startsWith('assets/apartment')) continue;
      if (entry.isDirectory()) { visit(file); continue; }
      if (/\.(?:js|mjs|jsx|css|html|json|wasm|woff2?)$/.test(entry.name)) result.set('/' + relative, file);
    }
  })(root);
  return result;
}

const run = path.join(ROOT, '.tmp/bench-lit-receivers', 'run-' + Date.now()); fs.mkdirSync(run, { recursive: true });
const packs = path.join(run, 'packs'), privateRoot = path.join(run, 'private');
const echo = installPack(packs, 'echo'), victoria = installPack(packs, 'victoria');
const splatRows = surfaceRows(SPLATS, 7), pointRows = surfaceRows(POINTS, 11);
victoria.files['apartment.ply'] = splatPly(splatRows);
victoria.files['points.ply'] = pointsPly(pointRows);
for (const name of ['apartment.ply', 'points.ply']) victoria.manifest.assets[name] = { path: name, bytes: victoria.files[name].length, sha256: hash(victoria.files[name]) };
victoria.files['content-pack.json'] = Buffer.from(JSON.stringify(victoria.manifest));
for (const name of ['apartment.ply', 'points.ply', 'content-pack.json']) fs.writeFileSync(path.join(packs, 'victoria', victoria.revision, name), victoria.files[name]);
fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1, packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
const t0 = Date.now();
const layer = authoredLightingLayer(victoria, { rows: { splat: splatRows, points: pointRows }, sets: MODES });
process.stdout.write(`synthetic layer: ${(Object.values(layer.files).reduce((sum, b) => sum + b.length, 0) / 1e6).toFixed(1)} MB in ${Date.now() - t0} ms\n`);
const sources = publicSources(SRC);

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (!['GET', 'HEAD'].includes(req.method)) { req.resume(); res.writeHead(403); res.end(); return; }
  if (url.pathname.startsWith('/api/apartment/v1/')) return handler(req, res);
  let body = null, type = 'application/octet-stream';
  if (url.pathname === '/content-packs/index.json') { body = fs.readFileSync(path.join(packs, 'index.json')); type = 'application/json'; }
  else if (url.pathname.startsWith(layer.base)) body = layer.files[decodeURIComponent(url.pathname.slice(layer.base.length))];
  else { const pack = [echo, victoria].find(item => url.pathname.startsWith(item.base)); if (pack) body = pack.files[url.pathname.slice(pack.base.length)]; }
  if (!body && url.pathname.startsWith('/api/perception-lab/v1/')) { body = Buffer.from(JSON.stringify({ items: [], cameras: [], live_previews: {}, live_videos: {}, experiments: [] })); type = 'application/json'; }
  if (!body && url.pathname.startsWith('/proxy/')) { body = Buffer.from(JSON.stringify(url.pathname.endsWith('/model') ? echo.seed : url.pathname.endsWith('/api/states') ? [] : {})); type = 'application/json'; }
  const file = sources.get(url.pathname === '/' ? '/index.html' : url.pathname);
  if (!body && file) { body = fs.readFileSync(file); type = /\.(?:js|mjs|jsx)$/.test(file) ? 'text/javascript' : file.endsWith('.html') ? 'text/html' : file.endsWith('.css') ? 'text/css' : file.endsWith('.json') ? 'application/json' : type; }
  if (!body) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { 'Content-Type': type, 'Content-Length': body.length, 'Cache-Control': 'no-store' }); res.end(req.method === 'HEAD' ? undefined : body);
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = 'http://127.0.0.1:' + server.address().port;
const handler = createEquipmentRegistrationGateway({ env: { NODE_ENV: 'test', HOME_WEB_EQUIPMENT_REGISTRATION_ENABLED: '1',
  HOME_WEB_EQUIPMENT_REGISTRATION_ROOT: privateRoot, HOME_WEB_EQUIPMENT_REGISTRATION_ORIGIN: origin, HOME_CONTENT_PACKS_DIR: packs },
  authenticate: req => req.headers.cookie?.includes('authored_owner_session=1') === true });
const args = ['--disable-background-networking', '--use-angle=d3d11', '--disable-gpu-vsync', '--disable-frame-rate-limit'];
if (RENDERER === 'webgpu') args.push('--enable-unsafe-webgpu', '--ignore-gpu-blocklist');
const browser = await chromium.launch({ headless: true, args, ...(CHANNEL ? { channel: CHANNEL } : {}) });
const result = { schema: 'home-lit-receivers-bench-1', synthetic: true, household_data: false, renderer: RENDERER, channel: CHANNEL, splats: SPLATS, points: POINTS,
  note: 'One machine, headless,1280x720. Wall blend values are descriptive upper bounds; only the separate GPU timestamp measurement can qualify the 4090 gate.',
  source_sha256: Object.fromEntries(['app/src/home-3d/lit-receivers.mjs', 'app/src/home-3d/lit-basis-blend.mjs',
    'app/src/vendor/three-0.186.1/three.webgpu.js', 'tools/lib/lit-webgpu-timestamp-benchmark.mjs'].map(file => [file, hash(fs.readFileSync(path.join(ROOT, file)))])), modes: {} };
const budget = setTimeout(() => { result.failure = '480s benchmark budget exceeded'; void browser.close(); server.closeAllConnections(); }, Math.max(1, 480000 - (Date.now() - BENCH_STARTED)));
try {
  try {
    const cdp = await browser.newBrowserCDPSession();
    try {
      const info = await cdp.send('SystemInfo.getInfo');
      result.browser_gpu_devices = (info.gpu?.devices || []).map(({ vendorId, deviceId, vendorString, deviceString }) =>
        ({ vendorId, deviceId, vendorString, deviceString }));
    } finally { await cdp.detach(); }
  } catch { result.browser_gpu_devices = []; }
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 }, serviceWorkers: 'block', reducedMotion: 'reduce' });
  await context.addCookies([{ name: 'authored_owner_session', value: '1', url: origin }]);
  await context.addInitScript(() => { localStorage.setItem('apartment3d.site', 'victoria'); localStorage.setItem('home.perf.backgroundWarmup', 'off'); window.HG_DISABLE_SERVICE_WORKER = true; });
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    return ['data:', 'blob:'].includes(url.protocol) || url.origin === origin ? route.continue() : route.abort();
  });
  await context.routeWebSocket('**/*', socket => socket.close());
  const page = await context.newPage(); page.setDefaultTimeout(300000);
  page.on('pageerror', error => console.log('pageerror: ' + error.message.slice(0, 300)));
  if (process.env.HOME_LIT_BENCH_VERBOSE === '1') page.on('console', message => console.log('console: ' + message.text().slice(0, 300)));
  await page.goto(origin + '/?apartmentRedesign=1&worldZoom=off&lit-splats=1' + (RENDERER === 'webgpu' ? '&renderer=webgpu' : ''), { waitUntil: 'domcontentloaded' });
  await page.locator('.apt-home-shell[data-home-layout="unified"]').waitFor();
  await page.waitForFunction(() => window.__APT_ENGINE?.siteId === 'victoria' && window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures.length === 4);
  result.gpu = await page.evaluate(() => ({ backend: window.__APT_ENGINE.renderer.backend?.isWebGPUBackend ? 'webgpu' : 'webgl', name: window.__APT_ENGINE.gpu ?? null }));
  if (RENDERER === 'webgpu') assert.equal(result.gpu.backend, 'webgpu', 'a real WebGPU adapter');
  const timing = (frames = 120, perFrame = null) => page.evaluate(([frames, perFrame]) => new Promise(resolve => {
    const engine = window.__APT_ENGINE, renderer = engine.renderer, original = renderer.render, renders = [], intervals = [];
    renderer.render = function (...a) { const t = performance.now(); try { return original.apply(this, a); } finally { renders.push(performance.now() - t); } };
    const ids = engine.overlay.virtualFixturesReadback().fixtures.map(item => item.fixture_id);
    let last = null, count = 0;
    const step = now => {
      if (perFrame === 'dim') engine.overlay.setVirtualFixtureState(ids[0], { downward: { brightness: 0.25 + 0.5 * ((count % 20) / 20) } });
      if (last != null) intervals.push(now - last); last = now;
      if (++count < frames) requestAnimationFrame(step);
      else { renderer.render = original; const p = (a, q) => [...a].sort((x, y) => x - y)[Math.floor((a.length - 1) * q)];
        resolve({ frames: intervals.length, frame_p50_ms: p(intervals, .5), frame_p95_ms: p(intervals, .95), render_cpu_p50_ms: p(renders, .5), render_cpu_p95_ms: p(renders, .95) }); }
    };
    requestAnimationFrame(step);
  }), [frames, perFrame]);
  for (const [label, mode] of [['Photo view', 'splat'], ['Cloud view', 'points']].filter(([, m]) => MODES.includes(m))) {
    await page.getByRole('button', { name: label, exact: true }).click();
    await page.waitForFunction(m => window.__APT_ENGINE.modes.mode === m, mode);
    await page.evaluate(() => window.__APT_ENGINE.rig.snapHome(0));
    await page.waitForTimeout(1500);
    const entry = { ...(TIMESTAMP_ONLY ? {} : { unlit: await timing() }) };
    const t = Date.now();
    await page.evaluate(() => window.__APT_ENGINE.setLit(true));
    await page.waitForFunction(() => window.__APT_ENGINE.litState()?.active && window.__APT_ENGINE.litState().receivers?.blends >= 1);
    entry.lit_enable_ms = Date.now() - t;
    await page.waitForFunction(() => window.__APT_ENGINE.litState().receivers?.liveSpots === 4 && window.__APT_ENGINE.litState().adapting === false, null, { timeout: 120000 });
    entry.lit_ready_ms = Date.now() - t;
    await page.waitForTimeout(1000);
    if (!TIMESTAMP_ONLY) {
      entry.lit_steady = await timing();
      entry.lit_moving = await timing(120, 'dim');
      // HDR bloom + display: wall time after waiting for GPU completion,
      // separate from the comparable GPU timestamp blend budget below.
      entry.frame_passes_ms = await page.evaluate(async () => { const t = []; for (let i = 0; i < 10; i++) t.push(await window.__APT_ENGINE.litFrameTime(20)); t.sort((a, b) => a - b); return { method: 'wall-upper-bound', p50: t[5], min: t[0] }; });
      entry.frame = await page.evaluate(() => { const f = window.__APT_ENGINE.litState().frame; return { width: f.width, height: f.height, emitters: f.emitters, clamped: f.emittersClamped }; });
      entry.blend_ms = await page.evaluate(async () => { const times = await window.__APT_ENGINE.litReceiversBenchmark(30); const p = q => times[Math.floor((times.length - 1) * q)]; return { method: 'wall-upper-bound', p50: p(.5), p95: p(.95), min: times[0] }; });
    }
    if (GPU_TIMESTAMPS) {
      entry.gpu_timestamp_blend = await page.evaluate(measureLitWebGpuTimestamps, { iterations: TIMESTAMP_ITERATIONS, maxDurationMs: 60000 });
      entry.gpu_gate = classifyLitWebGpuGate(entry.gpu_timestamp_blend, { browserGpuDevices: result.browser_gpu_devices });
      if (mode === 'splat') process.exitCode = entry.gpu_gate.status === 'passed' ? 0 : entry.gpu_gate.status === 'failed' ? 1 : 2;
    }
    if (TIMESTAMP_ONLY) {
      await page.evaluate(() => window.__APT_ENGINE.setLit(false));
      result.modes[mode] = entry;
      process.stdout.write(mode + ' ' + JSON.stringify(entry) + '\n');
      continue;
    }
    // One spot re-aimed: its receiver column is re-baked in the worker.
    const before = await page.evaluate(() => window.__APT_ENGINE.litState().receivers.blends);
    await page.evaluate(point => { const e = window.__APT_ENGINE, id = e.overlay.virtualFixturesReadback().fixtures[1].fixture_id;
      e.overlay.aimVirtualFixtureAtTarget(id, { point, normal: [0, 0, 1], kind: 'authored_floor' }); }, mode === 'splat' ? [1, 1, 0] : [3, 3, 0]);
    const aimed = Date.now();
    await page.waitForFunction(b => window.__APT_ENGINE.litState().receivers.blends > b && window.__APT_ENGINE.overlay.virtualFixturesPresentationActive() === false
      && window.__APT_ENGINE.litState().bounce.status === 'ready', before);
    await page.waitForTimeout(500);
    const state = await page.evaluate(() => { const s = window.__APT_ENGINE.litState(); return { receivers: { ...s.receivers, unbaked: undefined }, bounce: { ...s.bounce } }; });
    entry.spot_reaim_to_blend_ms = Date.now() - aimed;
    entry.spot_column_worker_ms = state.bounce.receiverSpotMs;
    entry.spot_bake = state.bounce.lastBake; entry.bake_backend = state.bounce.backend;
    entry.receivers = { count: state.receivers.count, backend: state.receivers.backend, gpu_bytes: state.receivers.gpuBytes, layer_bytes: state.receivers.layerBytes };
    await page.evaluate(() => window.__APT_ENGINE.setLit(false));
    result.modes[mode] = entry;
    process.stdout.write(mode + ' ' + JSON.stringify(entry) + '\n');
  }
} catch (error) { result.failure = String(error?.stack || error); throw error; }
finally {
  clearTimeout(budget);
  await browser.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
  fs.writeFileSync(path.join(run, 'bench.json'), JSON.stringify(result, null, 2) + '\n');
  process.stdout.write('bench: ' + path.join(run, 'bench.json') + '\n');
}
