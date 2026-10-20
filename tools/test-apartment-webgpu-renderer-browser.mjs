// The WebGPU renderer foundation (?renderer=webgpu) against today's WebGL
// engine, in the real app shell with the generated fixture packs only.
//
// Each backend boots in its own page: Victoria, the authored fixtures, Cloud
// and Mesh at fixed poses with the cloud's shimmer clock pinned. Frames come
// back through engine.captureFrame and are compared in memory; no image is
// written unless HOME_WEBGPU_PROOF_IMAGES names a local directory.
//
//   HOME_WEBGPU_PROOF_BACKENDS  comma list; default 'webgpu-webgl' (CI and
//                               Playwright: the node renderer's WebGL2
//                               backend). Add 'webgpu' on a machine with an
//                               adapter. The WebGL engine always runs first
//                               as the reference.
//   HOME_WEBGPU_PROOF_CHANNEL   a Playwright browser channel ('chrome',
//                               'msedge') for every run, the WebGL reference
//                               included, so frame times share a browser.
//   HOME_WEBGPU_PROOF_FRAME_GATE=1  also gate frame time against WebGL.
//   HOME_WEBGPU_PROOF_HOLD=1    only serve the fixture shell, for debugging
//                               by hand; nothing is asserted.
//   HOME_WEBGPU_PROOF_PACKS     LOCAL ONLY: a real content-pack directory to
//                               serve read-only instead of the generated
//                               packs (the authored registration still goes
//                               to a temporary folder). Never set in CI.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { deflateSync } from 'node:zlib';
import Core from '../app/src/home-equipment-registration.js';
import { createEquipmentRegistrationGateway, initRegistration, readActiveBinding } from '../web-gateway/equipment-registration.mjs';
import { installPack, authoredDocument } from './fixtures/apartment-home-shell-fixtures.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'), SRC = path.join(ROOT, 'app/src');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const candidates = (process.env.HOME_WEBGPU_PROOF_BACKENDS || 'webgpu-webgl').split(',').map(value => value.trim()).filter(Boolean);
assert.ok(candidates.every(value => ['webgpu', 'webgpu-webgl'].includes(value)), 'backends are webgpu and/or webgpu-webgl');
const imageDir = process.env.HOME_WEBGPU_PROOF_IMAGES || null;
/* Scan modes compared at the fixed poses. Cloud and Mesh by default; Photo
 * has its own harness (test-apartment-webgpu-photo-browser.mjs), so each run
 * fits the touched-harness deadline on SwiftShader. */
const proofModes = globalThis.__HOME_WEBGPU_PROOF_MODES || ['points', 'mesh'];
const modeNames = { points: 'Cloud', mesh: 'Mesh', splat: 'Photo' };
const viewport = { width: 960, height: 600 };

// Same fenced inventory policy as the overlay bridge proof.
function publicSources(root) {
  const result = new Map(), excluded = new Set(['node_modules', 'data', 'artifacts', 'private', 'recordings', 'logs']);
  let count = 0, bytes = 0;
  function visit(directory, depth = 0) {
    assert.ok(depth <= 12);
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      assert.ok(++count <= 10000);
      if (entry.name.startsWith('.') || excluded.has(entry.name)) continue;
      const file = path.join(directory, entry.name), relative = path.relative(root, file).split(path.sep).join('/'), stat = fs.lstatSync(file);
      if (relative === 'assets/apartment' || relative.startsWith('assets/apartment/') || stat.isSymbolicLink()) continue;
      if (stat.isDirectory()) { visit(file, depth + 1); continue; } if (!stat.isFile()) continue;
      const vendor = /^(?:vendor|fonts|icons)\//.test(relative) && /\.(?:wasm|woff2?|ttf|otf|png|jpe?g|webp|svg|ico|ktx2|json)$/.test(entry.name);
      const rootAsset = directory === root && ['index.html', 'favicon.svg', 'favicon.ico', 'favicon-32.png', 'apple-touch-icon.png', 'site.webmanifest'].includes(entry.name);
      if (!/\.(?:js|mjs|jsx|css)$/.test(entry.name) && !vendor && !rootAsset) continue;
      bytes += stat.size; assert.ok(stat.size <= 32 * 1024 * 1024 && bytes <= 256 * 1024 * 1024);
      result.set('/' + relative, { file, relative });
    }
  }
  visit(root); return result;
}

function browserOptions(backend) {
  const installed = fs.existsSync(chromium.executablePath());
  const args = ['--disable-background-networking', '--renderer-process-limit=2'];
  const channel = process.env.HOME_WEBGPU_PROOF_CHANNEL || null;
  if (backend === 'webgpu') args.push('--enable-unsafe-webgpu', '--ignore-gpu-blocklist');
  if (process.platform === 'win32') args.unshift('--use-angle=d3d11');
  else if (process.platform === 'linux') args.unshift('--use-gl=angle', '--use-angle=swiftshader-webgl', '--enable-unsafe-swiftshader');
  return { headless: true, args, ...(channel ? { channel } : process.platform === 'linux' ? { channel: 'chromium' } : !installed ? { channel: 'msedge' } : {}) };
}

/* Deep equality where numbers may differ by float noise: r186's math is not
 * bit-identical to r180's (picks differ by ~1e-15 m). Returns the largest
 * numeric difference; anything else must match exactly. */
function assertNumericallyEqual(actual, expected, label, epsilon = 1e-9) {
  let largest = 0;
  (function walk(a, b, at) {
    if (typeof a === 'number' && typeof b === 'number') {
      const diff = Math.abs(a - b); largest = Math.max(largest, diff);
      assert.ok(diff <= epsilon || Object.is(a, b), `${label}${at}: ${a} vs ${b}`); return;
    }
    if (a && b && typeof a === 'object' && typeof b === 'object') {
      assert.equal(Array.isArray(a), Array.isArray(b), label + at);
      assert.deepEqual(Object.keys(a).sort(), Object.keys(b).sort(), label + at);
      for (const key of Object.keys(a)) walk(a[key], b[key], at + '.' + key); return;
    }
    assert.equal(a, b, label + at);
  })(actual, expected, '');
  return largest;
}

/*
 * Per-pixel largest colour difference, over pixels either frame covers.
 * The canvas is premultiplied and the stage behind it is dark, so what is
 * seen is the RGB channels. Alpha is reported apart: at silhouettes it is
 * MSAA coverage, which WebGPU and ANGLE's D3D11 resolve differently.
 */
function compareFrames(a, b) {
  assert.equal(a.width, b.width); assert.equal(a.height, b.height);
  const histogram = new Uint32Array(256);
  let covered = 0, sum = 0, alphaOver48 = 0;
  for (let i = 0; i < a.pixels.length; i += 4) {
    if (a.pixels[i + 3] === 0 && b.pixels[i + 3] === 0) continue;
    let diff = 0;
    for (let c = 0; c < 3; c++) diff = Math.max(diff, Math.abs(a.pixels[i + c] - b.pixels[i + c]));
    if (Math.abs(a.pixels[i + 3] - b.pixels[i + 3]) > 48) alphaOver48++;
    histogram[diff]++; covered++; sum += diff;
  }
  const over = threshold => { let n = 0; for (let d = threshold + 1; d < 256; d++) n += histogram[d]; return covered ? n / covered : 0; };
  let p99 = 0;
  for (let d = 0, seen = 0; d < 256; d++) { seen += histogram[d]; if (seen >= covered * 0.99) { p99 = d; break; } }
  return { covered, mean: covered ? sum / covered : 0, p99, over16: over(16), over48: over(48),
    alphaOver48: covered ? alphaOver48 / covered : 0 };
}

function png(frame) {
  const raw = Buffer.alloc((frame.width * 4 + 1) * frame.height);
  for (let y = 0; y < frame.height; y++) {
    raw[y * (frame.width * 4 + 1)] = 0;
    frame.pixels.copy(raw, y * (frame.width * 4 + 1) + 1, y * frame.width * 4, (y + 1) * frame.width * 4);
  }
  const chunk = (type, data) => {
    const length = Buffer.alloc(4); length.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type), data]);
    let crc = ~0; for (const byte of body) { crc ^= byte; for (let k = 0; k < 8; k++) crc = (crc >>> 1) ^ (0xEDB88320 & -(crc & 1)); }
    const tail = Buffer.alloc(4); tail.writeUInt32BE(~crc >>> 0);
    return Buffer.concat([length, body, tail]);
  };
  const header = Buffer.alloc(13); header.writeUInt32BE(frame.width, 0); header.writeUInt32BE(frame.height, 4); header[8] = 8; header[9] = 6;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}

// In the page: fixed poses, both scan modes, and every renderer-independent
// contract the WebGPU seam must leave untouched.
async function proofInPage(modes) {
  const e = window.__APT_ENGINE, overlay = e.overlay;
  const frames = n => new Promise(resolve => { let left = n; const tick = () => (--left <= 0 ? resolve() : requestAnimationFrame(tick)); requestAnimationFrame(tick); });
  // Steady state: the rig's tween, then eased presentation (fixture visuals,
  // cutaway) that runs on frame time and so differs mid-way between backends.
  const settle = async () => {
    for (let i = 0; i < 400 && e.rig.isTweening(); i++) await frames(1);
    const until = performance.now() + 900;
    while (performance.now() < until) await frames(1);
  };
  const encode = frame => {
    // Top-down RGBA, base64.
    const rows = frame.height, stride = frame.width * 4, out = new Uint8Array(frame.pixels.length);
    for (let y = 0; y < rows; y++) out.set(frame.pixels.subarray((frame.bottomUp ? rows - 1 - y : y) * stride, ((frame.bottomUp ? rows - 1 - y : y) + 1) * stride), y * stride);
    let text = ''; for (let i = 0; i < out.length; i += 0x8000) text += String.fromCharCode(...out.subarray(i, i + 0x8000));
    return { width: frame.width, height: frame.height, base64: btoa(text) };
  };
  const capture = async () => {
    e.setRunning(false);
    e.pointsMaterial.uniforms.uTime.value = 0;
    // Photo: Spark sorts and commits the cut asynchronously, so on a slow
    // GPU (SwiftShader in CI) the last update can still belong to an earlier
    // pose. Finish any update in flight, then one started at this pose. The
    // node renderer's tick is synchronous; the same calls are no-ops there.
    if (e.modes.mode === 'splat') {
      for (let i = 0; i < 2; i++) { e.modes.tickSplat(e.camera, performance.now() / 1000); await e.modes.whenSparkIdle(); }
    }
    const frame = await e.captureFrame({ scale: 1 });
    e.setRunning(true);
    return frame ? encode(frame) : null;
  };
  const errors = [];
  await e.pointsPromise;
  e.setPerformanceMode('maxFidelity');
  const result = { backend: e.rendererBackend, gpu: e.gpu, poses: [], picks: [], projections: [], errors };
  const poses = [['home', () => {}], ['orbit+2', () => { e.rig.stepAzimuth(1, 0); e.rig.stepAzimuth(1, 0); }],
    ['zoom+2', () => { e.rig.stepZoom(1, 0); e.rig.stepZoom(1, 0); }]];
  // Photo is Spark on WebGL and Home's splat renderer on the node renderer;
  // on Victoria it carries the dollhouse cut, exterior cleanup and photo floor.
  for (const mode of modes) {
    await e.modes.setMode(mode, { duration: 0 });
    for (const [name, apply] of poses) {
      e.rig.snapHome(0); await settle(); apply(); await settle();
      result.poses.push({ mode, pose: name, frame: await capture() });
    }
  }
  // Picking and projection: CPU paths, identical on both backends.
  await e.modes.setMode('mesh', { duration: 0 });
  e.rig.snapHome(0); await settle();
  const rect = e.renderer.domElement.getBoundingClientRect();
  // Off-centre grid: the home pose is symmetric, so a centred column runs
  // exactly along the room's corner seam, where the face a ray meets is a
  // rounding tie (the WebGPU projection rounds differently at ~1e-13).
  for (let gy = 1; gy <= 5; gy++) for (let gx = 1; gx <= 7; gx++) {
    const x = rect.left + rect.width * (gx + 0.37) / 8.5, y = rect.top + rect.height * (gy + 0.29) / 6.5;
    const hit = e.displaySurfaceHit(x, y);
    result.picks.push(hit ? { point: hit.point, normal: hit.normal } : null);
  }
  for (const point of [[0.5, 0.5, 0.2], [2, 2, 1], [3.5, 3.5, 2.4], [0, 0, 0]]) {
    const world = e.apartmentRoot.localToWorld(new e.camera.position.constructor(...point));
    const screen = e.picking.projectToScreen(world);
    result.projections.push({ x: Math.round(screen.x * 1e6) / 1e6, y: Math.round(screen.y * 1e6) / 1e6, visible: screen.visible });
  }
  result.fit = e.debugFit();
  // Fixtures: readback shape, descriptors, an aim preview and its restore.
  const readback = overlay.virtualFixturesReadback();
  const ids = readback.fixtures.map(item => item.fixture_id);
  // depth.passes counts captures since boot, so it follows timing, not the backend.
  result.fixtures = { readback: { ...readback, depth: { ...readback.depth, passes: readback.depth.passes > 0 } },
    descriptors: ids.map(id => overlay.virtualFixtureInteractionDescriptor(id)) };
  const aim = overlay.aimVirtualFixtureAtTarget(ids[0], { point: [2, 2, 0.5], normal: [0, 0, 1], kind: 'authored_floor' });
  result.fixtures.aim = { ok: aim?.ok ?? null, descriptor: overlay.virtualFixtureInteractionDescriptor(ids[0]) };
  // Photo loads on every backend (Spark on WebGL, the splat fork on the node renderer).
  try { await e.modes.setMode('splat', { duration: 0 }); result.photo = { ok: true, mode: e.modes.mode }; }
  catch (error) { result.photo = { ok: false, error: String(error?.message || error) }; }
  await e.modes.setMode('points', { duration: 0 });
  // Frame time over 120 frames of the live loop, mesh mode, home pose.
  await e.modes.setMode('mesh', { duration: 0 });
  const stats = [];
  const off = e.on('stats', value => stats.push(value));
  const intervals = []; let last = performance.now();
  await new Promise(resolve => { let n = 0; const tick = now => { intervals.push(now - last); last = now; if (++n >= 120) resolve(); else requestAnimationFrame(tick); }; requestAnimationFrame(tick); });
  off();
  intervals.sort((a, b) => a - b);
  result.frameTime = { medianMs: intervals[60], p95Ms: intervals[114], renderP95Ms: stats.at(-1)?.renderP95 ?? null, calls: stats.at(-1)?.calls ?? null };
  result.capture = await (async () => { const frame = await e.captureFrame(); return frame ? { width: frame.width, height: frame.height, bottomUp: frame.bottomUp, length: frame.pixels.length } : null; })();
  result.webgpu = e.webgpuReadback();
  return result;
}

test(`WebGPU renderer foundation matches the WebGL engine in ${proofModes.map(mode => modeNames[mode]).join(' and ')}`, { timeout: 600000 }, async () => {
  const run = fs.mkdtempSync(path.join(os.tmpdir(), 'home-webgpu-proof-'));
  const privateRoot = path.join(run, 'private');
  let packs, echo, victoria;
  if (process.env.HOME_WEBGPU_PROOF_PACKS) {
    // Real packs, served read-only: only files their manifests list.
    packs = path.resolve(process.env.HOME_WEBGPU_PROOF_PACKS);
    const index = JSON.parse(fs.readFileSync(path.join(packs, 'index.json'), 'utf8'));
    const real = site => {
      const entry = index.packs.find(item => item.active && item.manifest.siteId === site);
      const directory = path.join(packs, site, entry.manifest.revision);
      const files = new Proxy({}, { get: (_, name) => {
        const asset = Object.values(entry.manifest.assets).find(item => item.path === name) || (name === 'content-pack.json' ? { path: name } : null);
        return asset ? fs.readFileSync(path.join(directory, ...asset.path.split('/'))) : undefined;
      } });
      const seedFile = path.join(directory, 'seed-model.json');
      return { site, base: entry.baseUrl, files, seed: fs.existsSync(seedFile) ? JSON.parse(fs.readFileSync(seedFile, 'utf8')) : {} };
    };
    echo = real('echo'); victoria = real('victoria');
  } else {
    packs = path.join(run, 'packs'); echo = installPack(packs, 'echo'); victoria = installPack(packs, 'victoria');
    fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1, packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  }
  initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  const sources = publicSources(SRC), forbidden = [], missing = [];
  let handler;
  const server = http.createServer((req, res) => {
    try {
      const url = new URL(req.url, 'http://localhost');
      if (!['GET', 'HEAD'].includes(req.method) || /\/api\/services\/|\/capture(?:\/|$)|\/acquisition(?:\/|$)|\/control(?:\/|$)|\/sessions\/[^/]+\/(?:start|stop)(?:\/|$)/.test(url.pathname)) {
        forbidden.push({ path: url.pathname, method: req.method }); res.writeHead(403); res.end(); return;
      }
      if (url.pathname.startsWith('/api/apartment/v1/')) return handler(req, res);
      let body, type = 'application/octet-stream';
      if (url.pathname.startsWith('/api/perception-lab/v1/')) {
        const route = url.pathname.slice('/api/perception-lab/v1/'.length), value = route === 'capabilities' ? { site_id: 'victoria', cameras: [], live_previews: {}, live_videos: {}, recording_allowed: false, experiments: [], apartment_viewing: { provisioned: false } }
          : route === 'sessions' ? { site_id: 'victoria', version: 1, items: [] } : route === 'recordings' ? { items: [] } : null;
        if (value) { body = Buffer.from(JSON.stringify(value)); type = 'application/json'; }
      }
      if (url.pathname === '/content-packs/index.json') { body = fs.readFileSync(path.join(packs, 'index.json')); type = 'application/json'; }
      else { const pack = [echo, victoria].find(item => url.pathname.startsWith(item.base)); if (pack) { body = pack.files[url.pathname.slice(pack.base.length)]; if (url.pathname.endsWith('.json')) type = 'application/json'; } }
      if (!body && url.pathname.startsWith('/proxy/')) { const value = url.pathname.endsWith('/model') || url.pathname.endsWith('/apartment_model') ? echo.seed : url.pathname.endsWith('/api/states') ? [] : url.pathname.endsWith('/api/config') ? { unit_system: { length: 'm' } } : {}; body = Buffer.from(JSON.stringify(value)); type = 'application/json'; }
      const entry = sources.get(url.pathname === '/' ? '/index.html' : url.pathname);
      if (!body && entry) {
        body = fs.readFileSync(entry.file);
        type = /\.(?:js|mjs|jsx)$/.test(entry.file) ? 'text/javascript' : entry.file.endsWith('.html') ? 'text/html' : entry.file.endsWith('.css') ? 'text/css' : /\.(?:json|webmanifest)$/.test(entry.file) ? 'application/json' : entry.file.endsWith('.svg') ? 'image/svg+xml' : type;
      }
      if (!body) { if (/\.(?:js|mjs|jsx|css|wasm)$/.test(url.pathname)) missing.push(url.pathname); res.writeHead(404); res.end(); return; }
      res.writeHead(200, { 'Content-Type': type, 'Content-Length': body.length, 'Cache-Control': 'no-store' }); res.end(req.method === 'HEAD' ? undefined : body);
    } catch (error) { res.writeHead(500); res.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  handler = createEquipmentRegistrationGateway({ env: { NODE_ENV: 'test', HOME_WEB_EQUIPMENT_REGISTRATION_ENABLED: '1', HOME_WEB_EQUIPMENT_REGISTRATION_ROOT: privateRoot, HOME_WEB_EQUIPMENT_REGISTRATION_ORIGIN: origin, HOME_CONTENT_PACKS_DIR: packs }, authenticate: req => req.headers.cookie?.includes('authored_owner_session=1') === true });
  if (process.env.HOME_WEBGPU_PROOF_HOLD === '1') {
    // Local debugging: serve the fixture shell until the process is stopped.
    console.log('holding ' + origin + '/?apartmentRedesign=1&worldZoom=off&renderer=webgpu-webgl');
    await new Promise(() => {});
  }

  async function boot(backend) {
    const browser = await chromium.launch(browserOptions(backend));
    try {
      const context = await browser.newContext({ viewport, deviceScaleFactor: 1, serviceWorkers: 'block' });
      await context.addCookies([{ name: 'authored_owner_session', value: '1', url: origin }]);
      await context.addInitScript(() => { localStorage.setItem('apartment3d.site', 'echo'); localStorage.setItem('home.perf.backgroundWarmup', 'off'); window.HG_DISABLE_SERVICE_WORKER = true; });
      await context.route('**/*', route => {
        const url = new URL(route.request().url());
        if (['data:', 'blob:'].includes(url.protocol) || url.origin === origin) return route.continue();
        const harmless = ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) && route.request().method() === 'GET' && url.pathname === '/healthz';
        if (!harmless) forbidden.push({ origin: url.origin, path: url.pathname }); return route.abort();
      });
      await context.routeWebSocket('**/*', socket => socket.close());
      const page = await context.newPage(), pageErrors = [], warnings = [];
      page.setDefaultTimeout(30000);
      page.on('pageerror', error => pageErrors.push(error.message));
      page.on('console', message => { if (/WebGPU|home-3d\] renderer/.test(message.text())) warnings.push(message.text()); });
      await page.goto(origin + '/?apartmentRedesign=1&worldZoom=off' + (backend === 'webgl' ? '' : '&renderer=' + backend), { waitUntil: 'domcontentloaded' });
      await page.locator('.apt-home-shell[data-home-layout="unified"]').waitFor({ timeout: 60000 });
      await page.waitForFunction(() => window.__HOME_APARTMENT_SITE_API?.readback().phase === 'ready' && window.__APT_ENGINE?.siteId === 'echo', null, { timeout: 60000 });
      await page.getByRole('group', { name: 'Home', exact: true }).getByRole('button', { name: 'Victoria', exact: true }).click();
      await page.waitForFunction(() => window.__HOME_APARTMENT_SITE_API?.readback().phase === 'ready' && window.__APT_ENGINE?.siteId === 'victoria', null, { timeout: 60000 });
      await page.waitForFunction(() => window.__APT_ENGINE?.overlay?.virtualFixturesReadback?.().fixture_count > 0);
      const result = await page.evaluate(proofInPage, proofModes);
      return { ...result, pageErrors, warnings };
    } finally { await browser.close(); }
  }

  try {
    const reference = await boot('webgl');
    assert.equal(reference.backend, 'webgl');
    assert.equal(reference.webgpu, null, 'the flag-off engine loads nothing of the WebGPU seam');
    assert.deepEqual(reference.pageErrors, []);
    const report = { reference: { gpu: reference.gpu, frameTime: reference.frameTime }, candidates: [] };
    for (const backend of candidates) {
      const candidate = await boot(backend);
      assert.deepEqual(candidate.pageErrors, [], backend + ' page errors');
      assert.equal(candidate.backend, backend === 'webgpu' ? 'webgpu' : 'webgpu-webgl2', 'the requested backend is the one running: ' + candidate.gpu);
      assert.deepEqual(candidate.webgpu.ports.problems, [], 'every drawn GLSL site used its port');
      for (const id of ['scan-display-floor-1', 'equipment-receiver', 'equipment-orb-halo', 'equipment-beam', 'equipment-glow']) {
        assert.ok(candidate.webgpu.ports.builds[id] > 0, id + ' was built from its port');
      }
      assert.equal(candidate.webgpu.lost, null);
      // Contracts the seam must not move.
      const contracts = {
        picks: assertNumericallyEqual(candidate.picks, reference.picks, 'surface picks'),
        projections: assertNumericallyEqual(candidate.projections, reference.projections, 'overlay projection'),
        fit: assertNumericallyEqual(candidate.fit, reference.fit, 'fit bounds'),
        fixtures: assertNumericallyEqual(candidate.fixtures, reference.fixtures, 'fixture readback, descriptors and aim'),
      };
      assert.equal(reference.photo.ok, true, 'Photo loads on WebGL: ' + reference.photo.error);
      assert.equal(candidate.photo.ok, true, 'Photo loads on the node renderer: ' + candidate.photo.error);
      assert.ok(candidate.capture && candidate.capture.width > 0 && candidate.capture.length === candidate.capture.width * candidate.capture.height * 4,
        'captureFrame returns a frame');
      const comparisons = [];
      for (const [index, shot] of reference.poses.entries()) {
        const other = candidate.poses[index];
        assert.equal(other.mode + other.pose, shot.mode + shot.pose);
        assert.ok(shot.frame && other.frame, 'both backends captured ' + shot.mode + ' ' + shot.pose);
        const a = { width: shot.frame.width, height: shot.frame.height, pixels: Buffer.from(shot.frame.base64, 'base64') };
        const b = { width: other.frame.width, height: other.frame.height, pixels: Buffer.from(other.frame.base64, 'base64') };
        const diff = compareFrames(a, b);
        comparisons.push({ mode: shot.mode, pose: shot.pose, ...diff });
        if (imageDir) {
          fs.mkdirSync(imageDir, { recursive: true });
          fs.writeFileSync(path.join(imageDir, `${shot.mode}-${shot.pose}-webgl.png`), png(a));
          fs.writeFileSync(path.join(imageDir, `${shot.mode}-${shot.pose}-${backend}.png`), png(b));
        }
      }
      report.candidates.push({ backend, gpu: candidate.gpu, frameTime: candidate.frameTime, ports: candidate.webgpu.ports,
        largestContractDifference: contracts, comparisons });
      for (const row of comparisons) {
        console.log(`${backend} vs WebGL, ${row.mode} ${row.pose}: covered ${row.covered}, mean ${row.mean.toFixed(2)}, `
          + `p99 ${row.p99}, >16 ${(row.over16 * 100).toFixed(2)}%, >48 ${(row.over48 * 100).toFixed(2)}%, `
          + `alpha >48 ${(row.alphaOver48 * 100).toFixed(2)}%`);
      }
      for (const row of comparisons) {
        assert.ok(row.covered > 1000, `${row.mode} ${row.pose}: the scene covers the frame`);
        assert.ok(row.over48 <= 0.01, `${row.mode} ${row.pose}: ${(row.over48 * 100).toFixed(2)}% of covered pixels differ by more than 48/255`);
        assert.ok(row.mean <= 4, `${row.mode} ${row.pose}: mean difference ${row.mean.toFixed(2)}/255`);
      }
      if (process.env.HOME_WEBGPU_PROOF_FRAME_GATE === '1') {
        assert.ok(candidate.frameTime.medianMs <= reference.frameTime.medianMs * 1.05 + 0.5,
          `frame time ${candidate.frameTime.medianMs} ms vs WebGL ${reference.frameTime.medianMs} ms`);
      }
    }
    console.log(JSON.stringify(report, null, 2));
    assert.deepEqual(forbidden, []); assert.deepEqual(missing, []);
  } finally {
    server.closeAllConnections(); server.close();
    fs.rmSync(run, { recursive: true, force: true });
  }
});
