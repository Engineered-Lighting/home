// L2 browser proof, shared by three browser files: part 1 checks Tier-0 and the
// bright half of the lights-off ladder; part 3 checks its overlapping dim half;
// part 2 checks moonlit tone views, adaptation, timing and exact restoration.
// The overlapping one-spot rung retains every adjacent ladder comparison while
// retaining each proof's original 145 s browser deadline on software GL.
// Tier-0 Lit on the WebGPU path (?renderer=webgpu-webgl in CI,
// the node renderer on its WebGL2 backend) with the scene-referred exposure and
// tone pipeline. Tier-0's behaviour holds on node materials; a scripted
// lights-off ladder (all on -> 30 % output -> one spot -> that spot at 5 % ->
// off with the assumed moonlight) darkens strictly on the display; moonlight
// is at most 60 % near-black; the 5 % spot pool stays warm; the orbs are never
// tone mapped; the disclosure and measured:false are unchanged.
// Authored fixture packs only; no household pack, camera, model session,
// physical command, screenshot or deployment.
//   HOME_LIT_WEBGPU_BACKENDS  comma list of 'webgpu-webgl' (default) and 'webgpu'
//                             (needs a real adapter: local only).
//   HOME_LIT_WEBGPU_CHANNEL     a Playwright browser channel ('msedge', 'chrome') with a
//                             WebGPU adapter; the bundled headless Chromium has none.
//   HOME_LIT_WEBGPU_FPS_GATE=1  also require >= 60 fps in Lit (owner's RTX 4090).
//   HOME_LIT_WEBGPU_ALL_TONES=1 also render the Neutral and Reinhard views at moonlight.
//   HOME_LIT_WEBGPU_STILLS=1    write the ladder rungs as PNGs beside the receipt (local review).
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import Core from '../../app/src/home-equipment-registration.js';
import { createEquipmentRegistrationGateway, initRegistration, readActiveBinding } from '../../web-gateway/equipment-registration.mjs';
import { installPack, authoredDocument } from './apartment-home-shell-fixtures.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..'), SRC = path.join(ROOT, 'app/src');
const Model = await import(pathToFileURL(path.join(SRC, 'home-3d/lit-model.mjs')).href);
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const backends = (process.env.HOME_LIT_WEBGPU_BACKENDS || 'webgpu-webgl').split(',').map(value => value.trim()).filter(Boolean);
assert.ok(backends.length && backends.every(value => ['webgpu', 'webgpu-webgl'].includes(value)), 'backends are webgpu and/or webgpu-webgl');
// Software GL compiles the 32-light node shader slowly; these waits cover that.
const LIT_WAIT_MS = 60000;
let failureSummary = null;
process.on('exit', () => { if (failureSummary) console.log('Lit WebGPU browser failure: ' + failureSummary); });

/* The spike-3 ladder, as output fractions: the slider level that gives each
 * through the assumed dimming curve (lit-model.mjs, CIE L* inverse). */
function levelForOutput(fraction) {
  let lo = 0, hi = 1;
  for (let i = 0; i < 60; i++) { const mid = (lo + hi) / 2; if (Model.litDimmingFraction(mid) < fraction) lo = mid; else hi = mid; }
  return (lo + hi) / 2;
}
const LEVEL_30 = levelForOutput(0.3), LEVEL_5 = levelForOutput(0.05);
const LADDER = ['all_on', 'output_30', 'one_spot', 'spot_5', 'off_moon'];

function publicSources(root) {
  const result = new Map(), excluded = new Set(['node_modules', 'data', 'artifacts', 'private', 'recordings', 'logs']);
  assert.ok(fs.lstatSync(root).isDirectory() && !fs.lstatSync(root).isSymbolicLink());
  let count = 0, bytes = 0;
  (function visit(directory, depth = 0) {
    assert.ok(depth <= 12);
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      assert.ok(++count <= 10000);
      if (entry.name.startsWith('.') || excluded.has(entry.name)) continue;
      const file = path.join(directory, entry.name), relative = path.relative(root, file).split(path.sep).join('/'), stat = fs.lstatSync(file);
      if (relative === 'assets/apartment' || relative.startsWith('assets/apartment/') || stat.isSymbolicLink()) continue;
      if (stat.isDirectory()) { visit(file, depth + 1); continue; }
      if (!stat.isFile()) continue;
      const vendor = /^(?:vendor|fonts|icons)\//.test(relative) && /\.(?:wasm|woff2?|ttf|otf|png|jpe?g|webp|svg|ico|ktx2|json)$/.test(entry.name);
      const rootAsset = directory === root && ['index.html', 'favicon.svg', 'favicon.ico', 'favicon-32.png', 'apple-touch-icon.png', 'site.webmanifest'].includes(entry.name);
      if (!/\.(?:js|mjs|jsx|css)$/.test(entry.name) && !vendor && !rootAsset) continue;
      bytes += stat.size; assert.ok(stat.size <= 32 * 1024 * 1024 && bytes <= 256 * 1024 * 1024);
      result.set('/' + relative, { file, relative });
    }
  })(root);
  return result;
}

function browserOptions(backend) {
  const installed = fs.existsSync(chromium.executablePath());
  assert.ok(installed || process.platform === 'win32', 'pinned Chromium must exist on Linux');
  const args = ['--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'];
  if (backend === 'webgpu') args.push('--enable-unsafe-webgpu', '--ignore-gpu-blocklist');
  if (process.platform === 'win32') args.unshift('--use-angle=d3d11');
  else if (process.platform === 'linux') args.unshift('--use-gl=angle', '--use-angle=swiftshader-webgl', '--enable-unsafe-swiftshader');
  const channel = process.env.HOME_LIT_WEBGPU_CHANNEL || null;
  return { headless: true, args, ...(channel ? { channel } : process.platform === 'linux' ? { channel: 'chromium' } : !installed ? { channel: 'msedge' } : {}) };
}

export async function runLitWebgpuBrowserProof({ part, signal }) {
  assert.ok(part === 1 || part === 2 || part === 3, 'part 1, 2 or 3');
  for (const backend of backends) { signal?.throwIfAborted(); await proveOn(backend, part, signal); }
}

async function proveOn(backend, part, signal) {
  assert.ok(!process.env.HOME_APARTMENT_CANDIDATE_ROOT, 'this proof uses only current worktree source');
  const run = path.join(ROOT, '.tmp/apartment-lit-webgpu-browser', backend + '-part' + part + '-' + Date.now()); fs.mkdirSync(run, { recursive: true });
  const packs = path.join(run, 'packs'), privateRoot = path.join(run, 'private');
  const echo = installPack(packs, 'echo'), victoria = installPack(packs, 'victoria');
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1,
    packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  const protectedFiles = [echo, victoria].flatMap(item => Object.keys(item.files).map(name => path.join(packs, item.site, item.revision, name)));
  const protectedHashes = Object.fromEntries(protectedFiles.map(file => [file, hash(fs.readFileSync(file))]));
  const sources = publicSources(SRC), receipt = { schema: 'home-apartment-lit-webgpu-authored-browser-1', backend, part, authored_room: true, real_renderer: true,
    household_data: false, physical_commands: 0, screenshots: false, checks: [], forbidden: [], missing_code: [], errors: [], served: {} };
  const check = (stage, detail = {}) => receipt.checks.push({ stage, ...detail });
  // The browser deadline remains 145 s, inside the runner's 180 s file bound.
  let server, browser, page, origin, handler, timer, stage = 'boot';
  const abort = () => { browser?.close().catch(() => {}); server?.closeAllConnections(); };
  signal?.addEventListener('abort', abort, { once: true });
  const started = Date.now(), at = name => { stage = name; receipt.stages = { ...receipt.stages, [name]: Date.now() - started }; };
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => {
    browser?.close().catch(() => {}); server?.closeAllConnections(); reject(Error('145s lit webgpu browser deadline at stage ' + stage + ' ' + JSON.stringify(receipt.stages || {})));
  }, 145000); });

  async function proof() {
    server = http.createServer((req, res) => {
      try {
        const url = new URL(req.url, 'http://localhost');
        if (!['GET', 'HEAD'].includes(req.method) || /\/api\/services\/|\/capture(?:\/|$)|\/acquisition(?:\/|$)|\/control(?:\/|$)|\/sessions\/[^/]+\/(?:start|stop)(?:\/|$)/.test(url.pathname)) {
          receipt.forbidden.push({ path: url.pathname, method: req.method }); res.writeHead(403); res.end(); return;
        }
        if (url.pathname.startsWith('/api/apartment/v1/')) return handler(req, res);
        let body, type = 'application/octet-stream';
        if (url.pathname.startsWith('/api/perception-lab/v1/')) {
          const route = url.pathname.slice('/api/perception-lab/v1/'.length), value = route === 'capabilities'
            ? { site_id: 'victoria', cameras: [], live_previews: {}, live_videos: {}, recording_allowed: false, experiments: [], apartment_viewing: { provisioned: false } }
            : route === 'sessions' ? { site_id: 'victoria', version: 1, items: [] } : route === 'recordings' ? { items: [] } : null;
          if (value) { body = Buffer.from(JSON.stringify(value)); type = 'application/json'; }
        }
        if (url.pathname === '/content-packs/index.json') { body = fs.readFileSync(path.join(packs, 'index.json')); type = 'application/json'; }
        else { const pack = [echo, victoria].find(item => url.pathname.startsWith(item.base)); if (pack) { body = pack.files[url.pathname.slice(pack.base.length)]; if (url.pathname.endsWith('.json')) type = 'application/json'; } }
        if (!body && url.pathname.startsWith('/proxy/')) {
          const value = url.pathname.endsWith('/model') || url.pathname.endsWith('/apartment_model') ? echo.seed
            : url.pathname.endsWith('/api/states') ? [] : url.pathname.endsWith('/api/config') ? { unit_system: { length: 'm' } } : {};
          body = Buffer.from(JSON.stringify(value)); type = 'application/json';
        }
        const entry = sources.get(url.pathname === '/' ? '/index.html' : url.pathname);
        if (!body && entry) {
          const relative = path.relative(fs.realpathSync(SRC), fs.realpathSync(entry.file)).split(path.sep).join('/');
          assert.equal(relative, entry.relative); assert.ok(!relative.startsWith('../') && !path.isAbsolute(relative));
          body = fs.readFileSync(entry.file); receipt.served[relative] = hash(body);
          type = /\.(?:js|mjs|jsx)$/.test(entry.file) ? 'text/javascript' : entry.file.endsWith('.html') ? 'text/html' : entry.file.endsWith('.css') ? 'text/css'
            : /\.(?:json|webmanifest)$/.test(entry.file) ? 'application/json' : entry.file.endsWith('.svg') ? 'image/svg+xml' : type;
        }
        if (!body) { if (/\.(?:js|mjs|jsx|css|wasm)$/.test(url.pathname)) receipt.missing_code.push(url.pathname); res.writeHead(404); res.end(); return; }
        res.writeHead(200, { 'Content-Type': type, 'Content-Length': body.length, 'Cache-Control': 'no-store' }); res.end(req.method === 'HEAD' ? undefined : body);
      } catch (error) { receipt.errors.push('authored HTTP handler: ' + error.message); res.writeHead(500); res.end(); }
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); origin = 'http://127.0.0.1:' + server.address().port;
    handler = createEquipmentRegistrationGateway({ env: { NODE_ENV: 'test', HOME_WEB_EQUIPMENT_REGISTRATION_ENABLED: '1',
      HOME_WEB_EQUIPMENT_REGISTRATION_ROOT: privateRoot, HOME_WEB_EQUIPMENT_REGISTRATION_ORIGIN: origin, HOME_CONTENT_PACKS_DIR: packs },
      authenticate: req => req.headers.cookie?.includes('authored_owner_session=1') === true });
    browser = await chromium.launch(browserOptions(backend));
    // Reduced motion snaps the 160 ms ease and the night adaptation, so each
    // rung is measured settled; the eases themselves are checked further down.
    const context = await browser.newContext({ viewport: { width: 1366, height: 768 }, deviceScaleFactor: 1, serviceWorkers: 'block', reducedMotion: 'reduce' });
    await context.addCookies([{ name: 'authored_owner_session', value: '1', url: origin }]);
    await context.addInitScript(() => {
      localStorage.setItem('apartment3d.site', 'victoria'); localStorage.setItem('home.perf.backgroundWarmup', 'off'); window.HG_DISABLE_SERVICE_WORKER = true;
    });
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      if (['data:', 'blob:'].includes(url.protocol)) return route.continue();
      if (url.origin !== origin) {
        const probe = ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) && route.request().method() === 'GET' && url.pathname === '/healthz';
        if (!probe) receipt.forbidden.push({ origin: url.origin, path: url.pathname, external: true });
        return route.abort();
      }
      return route.continue();
    });
    await context.routeWebSocket('**/*', socket => socket.close());
    page = await context.newPage(); page.setDefaultTimeout(20000);
    page.on('pageerror', error => receipt.errors.push(error.message));
    const consoleProblems = [];
    page.on('console', message => { if (/WebGPU: (?:unported|drifted)|NodeBuilder|TSL|WGSL|GLSL/.test(message.text())) consoleProblems.push(message.text().slice(0, 300)); });

    const frames = (n = 3) => page.evaluate(n => new Promise(resolve => { let k = 0; const step = () => (++k >= n ? resolve() : requestAnimationFrame(step)); requestAnimationFrame(step); }), n);
    // Display statistics over the pixels the scene covers (the canvas is transparent elsewhere).
    const capture = () => page.evaluate(async () => {
      const frame = await window.__APT_ENGINE.captureFrame({ scale: .25 });
      if (!frame) return null;
      const decode = value => { const v = value / 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
      const n = frame.width * frame.height, Y = new Float32Array(n), R = new Float32Array(n), B = new Float32Array(n), covered = new Uint8Array(n);
      let count = 0, sum = 0, nearBlack = 0;
      for (let i = 0; i < n; i++) {
        const p = frame.pixels;
        if (p[i * 4 + 3] === 0) continue;
        const r = decode(p[i * 4]), g = decode(p[i * 4 + 1]), b = decode(p[i * 4 + 2]);
        covered[i] = 1; count++; R[i] = r; B[i] = b; Y[i] = .2126 * r + .7152 * g + .0722 * b; sum += Y[i];
        if (Math.max(p[i * 4], p[i * 4 + 1], p[i * 4 + 2]) < 0.03 * 255) nearBlack++;
      }
      return { width: frame.width, height: frame.height, covered: count, meanY: sum / Math.max(1, count), nearBlackPct: 100 * nearBlack / Math.max(1, count),
        Y: Array.from(Y), R: Array.from(R), B: Array.from(B), mask: Array.from(covered) };
    });
    const sceneLuminance = () => page.evaluate(async () => {
      const read = await window.__APT_ENGINE.litLinearReadback({ scale: .25 });
      if (!read) return null;
      let count = 0, sum = 0;
      for (let i = 0; i < read.width * read.height; i++) if (read.coverage[i]) { count++; sum += read.luminance[i]; }
      return { meanCdM2: sum / Math.max(1, count), covered: count, unit: read.unit, measured: read.measured };
    });
    // Settled: the lights stopped easing, two meters landed after the change
    // (one may have been in flight before it) and the adaptation reached them.
    const settled = async () => {
      const serial = await page.evaluate(() => window.__APT_ENGINE.litState().meterSerial);
      await page.waitForFunction(serial => { const state = window.__APT_ENGINE.litState();
        return state.easing === false && state.adapting === false && ['ready', 'error'].includes(state.bounce.status)
          && state.meterSerial >= serial + 2; }, serial, { timeout: LIT_WAIT_MS });
    };
    const timing = () => page.evaluate(() => new Promise(resolve => {
      const engine = window.__APT_ENGINE, renderer = engine.renderer, original = renderer.render, renders = [], intervals = [];
      renderer.render = function (...args) { const t0 = performance.now(); try { return original.apply(this, args); } finally { if (args[0] === engine.scene) renders.push(performance.now() - t0); } };
      // 60 frames, or 5 s at most: hosted software GL can draw Lit at about 1 fps.
      let last = null, count = 0, first = null;
      const step = now => {
        if (last != null) intervals.push(now - last); last = now; first ??= now;
        if (++count < 61 && (count < 3 || now - first < 5000)) requestAnimationFrame(step);
        else { renderer.render = original; const p = (a, q) => [...a].sort((x, y) => x - y)[Math.floor(a.length * q)];
          resolve({ frames: intervals.length, fps_p50: 1000 / p(intervals, .5), frame_p50_ms: p(intervals, .5), frame_p95_ms: p(intervals, .95),
            render_p50_ms: p(renders, .5), render_p95_ms: p(renders, .95) }); }
      };
      requestAnimationFrame(step);
    }));

    await page.goto(origin + '/?apartmentRedesign=1&worldZoom=off&renderer=' + backend, { waitUntil: 'domcontentloaded' });
    await page.locator('.apt-home-shell[data-home-layout="unified"]').waitFor({ timeout: 45000 });
    await page.waitForFunction(() => window.__APT_ENGINE?.siteId === 'victoria'
      && window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures.length === 4, null, { timeout: 60000 });
    const renderer = await page.evaluate(() => ({ backend: window.__APT_ENGINE.rendererBackend, gpu: window.__APT_ENGINE.webgpuReadback()?.gpu }));
    assert.equal(renderer.backend, backend === 'webgpu' ? 'webgpu' : 'webgpu-webgl2', 'the requested node backend is the one running: ' + renderer.gpu);
    receipt.renderer = renderer;
    const litButton = page.getByRole('button', { name: 'Lighting', exact: true });
    await litButton.waitFor();
    assert.equal(await litButton.getAttribute('aria-pressed'), 'false', 'Lighting is off by default');

    // Mesh unlit: the baseline.
    await page.getByRole('button', { name: 'Mesh view', exact: true }).click();
    await page.waitForFunction(() => window.__APT_ENGINE.modes.mode === 'mesh' && !!window.__APT_ENGINE.modes.getMesh(), null, { timeout: LIT_WAIT_MS });
    await page.evaluate(() => window.__APT_ENGINE.rig.snapHome(0)); await frames(6);
    const snapshot = () => page.evaluate(() => {
      const engine = window.__APT_ENGINE, rows = [];
      engine.modes.getMesh().traverse(node => { if (node.isMesh) rows.push([node.material.uuid, Object.keys(node.geometry.attributes).sort().join(','), node.layers.mask]); });
      const orbs = []; engine.overlay.virtualFixturesGroup.traverse(node => { if (node.material) orbs.push([node.name, node.material.uuid, node.material.toneMapped, node.layers.mask]); });
      let litLights = 0; engine.scene.traverse(node => { if (node.userData?.lit_source) litLights++; });
      return { meshMaterials: rows, orbs, toneMapping: engine.renderer.toneMapping, shadows: engine.renderer.shadowMap.enabled,
        render: engine.renderer.render.name, cameraLayers: engine.camera?.layers?.mask ?? null, litLights };
    });
    const before = await snapshot();
    assert.ok(before.orbs.length > 0 && before.orbs.some(row => row[2] === false), 'the orbs draw untone-mapped');

    // Lit on: Tier-0's behaviour on node materials.
    at('lit_on');
    await litButton.click();
    await page.waitForFunction(() => window.__APT_ENGINE.litState?.()?.active === true, null, { timeout: LIT_WAIT_MS });
    await settled();
    await page.waitForFunction(() => document.querySelector('[aria-label="Lighting"]')?.getAttribute('aria-pressed') === 'true'
      && document.querySelector('[data-apt-lit-disclosure]'), null, { timeout: LIT_WAIT_MS });
    const note = page.locator('[data-apt-lit-disclosure]');
    const shownQuality = await page.evaluate(() => window.__APT_ENGINE.litState().quality);
    assert.ok((await note.textContent()).trim().startsWith('Simulated · not photometric · assumed output'
      + (shownQuality === 'reduced' ? ' · reduced quality for speed' : '')), 'the unchanged disclosure, and any declared reduced quality');
    const title = await note.getAttribute('title');
    assert.match(title, /Assumed output, not measured: spot 450 lm, down 350 lm, each radial zone 120 lm/);
    assert.match(title, /partial night adaptation: presentation, not lux/, 'night adaptation is said to be presentation');
    assert.match(title, /Assumed night ambient about 0\.2 lx \(moonlight or street light through the windows\)/, 'the ambient floor is disclosed');
    const lit = await page.evaluate(() => {
      const engine = window.__APT_ENGINE, state = engine.litState(), lights = [];
      engine.scene.traverse(node => { if (node.isLight && node.userData.lit_source) lights.push({ kind: node.userData.lit_source, type: node.type, intensity: node.intensity,
        castShadow: node.castShadow, radius: node.shadow?.radius ?? null, autoUpdate: node.shadow?.autoUpdate ?? null, measured: node.userData.measured, layers: node.layers.mask }); });
      const mesh = []; engine.modes.getMesh().traverse(node => { if (node.isMesh) mesh.push({ node: node.material.isMeshStandardNodeMaterial === true,
        onBeforeCompile: Object.hasOwn(node.material, 'onBeforeCompile'), cast: node.castShadow, receive: node.receiveShadow, layers: node.layers.mask }); });
      const orbs = []; engine.overlay.virtualFixturesGroup.traverse(node => { if (node.material) orbs.push([node.name, node.material.uuid, node.material.toneMapped, node.layers.mask]); });
      return { state: JSON.parse(JSON.stringify(state)), disclosure: state.disclosure, lights, mesh, orbs, toneMapping: engine.renderer.toneMapping,
        shadows: engine.renderer.shadowMap.enabled, ports: engine.webgpuReadback().ports };
    });
    assert.equal(lit.state.active, true); assert.equal(lit.state.measured, false); assert.equal(lit.disclosure, 'Simulated · not photometric · assumed output');
    assert.equal(lit.state.lights, 32); assert.equal(lit.state.shadowLights, 8);
    assert.deepEqual(lit.state.assumedLumens, { spot: 450, down: 350, zone: 120 });
    assert.equal(lit.state.bounceLimit, 'One bounce, shadowed · per-vertex detail · assumed albedos · radial zones cast no direct shadows');
    assert.equal(lit.state.bounce.status, 'ready', 'the L5 bounce bake landed in the lightmap on node materials: ' + JSON.stringify(lit.state.bounce));
    assert.equal(lit.state.exposure, .038, 'lights stay pre-exposed at the design night');
    assert.equal(lit.state.pipeline, 'scene-referred-half-float'); assert.equal(lit.state.nightAdaptation, true);
    assert.ok(Math.abs(lit.state.designEv100 - Math.log2(1 / (1.2 * .038))) < 1e-9, 'the design night is EV100 4.45');
    assert.equal(lit.state.tone.operator, 'agx'); assert.equal(lit.state.tone.presentation, true);
    assert.deepEqual(lit.state.ambientFloor, { lux: 0.2, kelvin: 4100, groundRatio: 0.3, status: 'assumed', measured: false,
      source: 'Assumed night ambient: moonlight or street light through the windows, about 0.2 lx. Not modeled through the openings; not measured.' });
    assert.equal(lit.state.cutaway, true, 'the lit mesh keeps the dollhouse cut, as TSL');
    assert.ok(lit.mesh.length > 0 && lit.mesh.every(row => row.node && !row.onBeforeCompile && row.cast && row.receive && (row.layers & (1 << 29))),
      'the scan mesh is relit with node materials and no onBeforeCompile');
    const spots = lit.lights.filter(light => light.kind === 'spot'), downs = lit.lights.filter(light => light.kind === 'down');
    assert.ok(spots.length === 4 && spots.every(light => light.castShadow && light.radius === 2.5 && light.intensity > 0 && light.autoUpdate === false));
    assert.ok(downs.length === 4 && downs.every(light => light.castShadow && light.radius === 6 && light.intensity > 0));
    assert.ok(lit.lights.filter(light => light.kind === 'zone').every(light => !light.castShadow));
    assert.deepEqual([...new Set(lit.lights.map(light => light.kind))].sort(), ['ambient_floor', 'down', 'spot', 'zone'], 'bounce is the lightmap, not lights');
    const floor = lit.lights.filter(light => light.kind === 'ambient_floor');
    assert.equal(floor.length, 1); assert.equal(floor[0].type, 'HemisphereLight');
    assert.ok(Math.abs(floor[0].intensity - 0.2 * .038) < 1e-12, 'the floor is its assumed lux at the design pre-exposure; adaptation is in the tone pass');
    assert.ok(lit.lights.every(light => light.layers === 1 << 29 && light.measured === false), 'Lit lights live on the scene-referred layer only');
    assert.equal(lit.toneMapping, before.toneMapping, 'renderer tone mapping untouched'); assert.equal(lit.shadows, true);
    assert.deepEqual(lit.orbs, before.orbs, 'orbs and beams keep the same untone-mapped materials and are never drawn by the tone pass');
    assert.deepEqual(lit.ports.problems, [], 'no unported or drifted material on the WebGPU path');
    check('tier0_on_node_materials', { lights: lit.state.lights, shadow_lights: lit.state.shadowLights, cutaway: lit.state.cutaway, pipeline: lit.state.pipeline });

    // The lights-off ladder.
    at('ladder');
    const ids = await page.evaluate(() => window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures.map(item => item.fixture_id));
    assert.equal(ids.length, 4);
    const every = (on, brightness) => ({ spotlight: { on, brightness }, downward: { on, brightness },
      radials: [1, 2, 3, 4, 5, 6].map(number => ({ number, on, brightness })) });
    const rung = { all_on: () => every(true, 1), output_30: () => every(true, LEVEL_30),
      one_spot: index => ({ ...every(false, 1), spotlight: { on: index === 0, brightness: 1 } }),
      spot_5: index => ({ ...every(false, 1), spotlight: { on: index === 0, brightness: LEVEL_5 } }), off_moon: () => every(false, 1) };
    const apply = async name => {
      const result = await page.evaluate(({ ids, patches }) => ids.map((id, index) => window.__APT_ENGINE.overlay.setVirtualFixtureState(id, patches[index]).ok),
        { ids, patches: ids.map((_, index) => rung[name](index)) });
      assert.ok(result.every(Boolean), 'ladder rung applied: ' + name);
      // Two fresh meter results plus completed easing/baking fence the state;
      // captureFrame renders that settled state without six extra idle frames.
      await settled();
    };
    const ladder = {}, frameOf = {};
    if (part === 2) await apply('off_moon');
    if (part === 1 || part === 3) {
    const rungs = part === 1 ? LADDER.slice(0, 3) : LADDER.slice(2);
    for (const name of rungs) {
      await apply(name);
      const frame = await capture(), scene = await sceneLuminance();
      const state = await page.evaluate(() => { const s = window.__APT_ENGINE.litState(); return { adaptationEv: s.adaptationEv, dropEv: s.adaptationDropEv, ev100: s.ev100, meteredCdM2: s.meteredCdM2 }; });
      assert.ok(frame && frame.covered > 1000, 'the lit room covers the frame: ' + name);
      frameOf[name] = frame;
      // Opt-in stills of the authored room for visual review; never part of the pass criteria.
      if (process.env.HOME_LIT_WEBGPU_STILLS === '1') await page.screenshot({ path: path.join(run, 'ladder-' + LADDER.indexOf(name) + '-' + name + '.png') });
      ladder[name] = { mean_display_luminance: frame.meanY, near_black_pct: frame.nearBlackPct, scene_mean_cd_m2: scene.meanCdM2, ...state };
    }
    for (let i = 1; i < rungs.length; i++) {
      assert.ok(ladder[rungs[i]].mean_display_luminance < ladder[rungs[i - 1]].mean_display_luminance,
        `mean display luminance strictly decreases: ${rungs[i - 1]} ${ladder[rungs[i - 1]].mean_display_luminance} -> ${rungs[i]} ${ladder[rungs[i]].mean_display_luminance}`);
      assert.ok(ladder[rungs[i]].scene_mean_cd_m2 < ladder[rungs[i - 1]].scene_mean_cd_m2, 'and so does the scene-referred luminance: ' + rungs[i]);
      assert.ok(ladder[rungs[i]].adaptationEv >= ladder[rungs[i - 1]].adaptationEv, 'adaptation only grows down the ladder');
    }
    if (part === 1) {
    assert.ok(ladder.all_on.adaptationEv < 0.5, 'the all-on room meters near the design night and barely adapts: ' + ladder.all_on.adaptationEv);
    }
    if (part === 3) {
    assert.ok(ladder.off_moon.adaptationEv <= 5.5 + 1e-9 && ladder.off_moon.adaptationEv > 4, 'moonlight adapts, bounded at +5.5 EV');
    assert.ok(ladder.off_moon.near_black_pct <= 60, 'moonlight is at most 60 % near-black: ' + ladder.off_moon.near_black_pct);
    assert.ok(ladder.off_moon.scene_mean_cd_m2 < 0.5, 'and the moonlit room is truly dim in scene terms (cd/m², modeled): ' + ladder.off_moon.scene_mean_cd_m2);
    // The 5 % spot pool: the pixels the spot lights over the moonlit room.
    const spot = frameOf.spot_5, moon = frameOf.off_moon, gains = [];
    for (let i = 0; i < spot.Y.length; i++) if (spot.mask[i] && moon.mask[i]) gains.push([spot.Y[i] - moon.Y[i], i]);
    gains.sort((a, b) => b[0] - a[0]);
    const pool = gains.slice(0, Math.max(20, Math.round(gains.length * .03))).filter(([gain]) => gain > 0).map(([, i]) => i);
    assert.ok(pool.length >= 20, 'the 5 % spot lights a pool');
    const ratio = (frame, pixels) => pixels.reduce((sum, i) => sum + frame.R[i], 0) / Math.max(1e-9, pixels.reduce((sum, i) => sum + frame.B[i], 0));
    const poolWarmth = ratio(spot, pool), moonWarmth = ratio(moon, moon.mask.flatMap((value, i) => (value ? [i] : [])));
    assert.ok(poolWarmth > 1.15, 'the 5 % spot pool keeps a warm hue (R/B ' + poolWarmth + ')');
    assert.ok(poolWarmth > moonWarmth, 'and is warmer than the mesopic moonlit room (' + poolWarmth + ' vs ' + moonWarmth + ')');
    ladder.spot_5.pool_r_over_b = poolWarmth; ladder.off_moon.room_r_over_b = moonWarmth;
    }
    check('lights_off_ladder', { ladder, levels: { output_30: LEVEL_30, spot_5: LEVEL_5 } });
    receipt.ladder = ladder;
    }

    if (part === 2) {
    at('tone_views');
    // Comparison views: no presentation effects, so moonlight is the design exposure's near-black.
    const compare = {};
    // Each switch recompiles the display pass, which is slow on software GL: CI
    // checks the linear comparison against the view; HOME_LIT_WEBGPU_ALL_TONES=1
    // adds Neutral and the Reinhard fallback (whose moonlight gate test-lit-exposure.mjs also holds).
    const operators = process.env.HOME_LIT_WEBGPU_ALL_TONES === '1' ? ['linear', 'neutral', 'reinhard', 'agx'] : ['linear', 'agx'];
    for (const operator of operators) {
      const state = await page.evaluate(operator => window.__APT_ENGINE.setLitTone(operator), operator);
      assert.equal(state.tone.operator, operator); assert.equal(state.tone.presentation, operator === 'agx' || operator === 'reinhard');
      await frames(4);
      const frame = await capture(); compare[operator] = { mean_display_luminance: frame.meanY, near_black_pct: frame.nearBlackPct };
    }
    assert.ok(compare.linear.mean_display_luminance < compare.agx.mean_display_luminance, 'the linear comparison view shows no night adaptation');
    if (compare.reinhard) assert.ok(compare.reinhard.near_black_pct <= 60, 'the Reinhard fallback also passes the moonlight gate: ' + compare.reinhard.near_black_pct);
    check('tone_views_at_moonlight', compare);

    // The adaptation eases: slowly as the room darkens, quickly as it brightens.
    at('adaptation_eases');
    // The room is settled in moonlight after the ladder: brighten first, then darken from the bright room.
    const all = async on => page.evaluate(({ ids, patch }) => ids.forEach(id => window.__APT_ENGINE.overlay.setVirtualFixtureState(id, patch)), { ids, patch: every(on, 1) });
    const after = async ms => { await page.waitForTimeout(ms); await frames(2); return page.evaluate(() => { const s = window.__APT_ENGINE.litState(); return { ev: s.adaptationEv, target: s.adaptationTargetEv, adapting: s.adapting }; }); };
    await page.evaluate(() => window.__APT_ENGINE.setLitTone('agx'));
    const fromEv = (await page.evaluate(() => window.__APT_ENGINE.litState().adaptationEv));
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await all(true); await frames(2);
    const brightening = await after(1000);
    assert.ok(brightening.ev < fromEv * 0.45, 'brightening adapts fast (tau 0.75 s): ' + JSON.stringify({ fromEv, ...brightening }));
    await page.emulateMedia({ reducedMotion: 'reduce' }); await frames(3); await settled(); await page.emulateMedia({ reducedMotion: 'no-preference' });
    await all(false); await frames(2);
    const darkening = await after(1000);
    assert.ok(darkening.adapting && darkening.ev > 0 && darkening.ev < darkening.target * 0.6, 'darkening adapts slowly (tau 3 s): ' + JSON.stringify(darkening));
    check('adaptation_eases', { darkening, brightening, from_ev: fromEv });
    await page.emulateMedia({ reducedMotion: 'reduce' }); await all(true); await frames(3); await settled();

    // Performance in Lit, all on (descriptive on CI; gated on the owner's GPU).
    at('performance');
    const litTiming = await timing();
    receipt.performance = { note: 'descriptive unless HOME_LIT_WEBGPU_FPS_GATE=1; headless software GL is not the owner GPU', lit: litTiming,
      quality: await page.evaluate(() => window.__APT_ENGINE.litState().quality) };
    if (process.env.HOME_LIT_WEBGPU_FPS_GATE === '1') assert.ok(litTiming.fps_p50 >= 60, 'Lit holds 60 fps: ' + JSON.stringify(litTiming));

    // Leaving Lit restores the mesh, the renderer and the camera exactly.
    at('restore');
    await page.getByRole('button', { name: 'Cloud view', exact: true }).click();
    await page.waitForFunction(() => window.__APT_ENGINE.modes.mode === 'points' && window.__APT_ENGINE.litState().active === false, null, { timeout: LIT_WAIT_MS });
    const restored = await snapshot();
    assert.deepEqual(restored.meshMaterials, before.meshMaterials, 'leaving Lit restores the exact mesh materials, attributes and layers');
    assert.deepEqual(restored.orbs, before.orbs); assert.equal(restored.render, before.render, 'renderer.render is the engine\'s own again');
    assert.equal(restored.shadows, before.shadows); assert.equal(restored.toneMapping, before.toneMapping); assert.equal(restored.litLights, 0);
    assert.equal(restored.cameraLayers, before.cameraLayers);
    check('leaving_lit_restores_mesh_and_renderer', restored);
    }

    assert.deepEqual(consoleProblems, [], 'no node-material build problems');
    assert.deepEqual(Object.fromEntries(protectedFiles.map(file => [file, hash(fs.readFileSync(file))])), protectedHashes, 'authored pack bytes unchanged');
    assert.deepEqual(receipt.forbidden, []); assert.deepEqual(receipt.errors, []); assert.deepEqual(receipt.missing_code, []);
    for (const [relative, digest] of Object.entries(receipt.served)) assert.equal(digest, hash(fs.readFileSync(path.join(SRC, relative))), 'served code stayed stable: ' + relative);
    receipt.ok = true;
    fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    process.stdout.write('Authored Lit WebGPU browser evidence: ' + path.join(run, 'receipt.json') + '\n'
      + (part !== 2 ? 'ladder ' + JSON.stringify(Object.fromEntries(Object.entries(ladder).map(([name, rung]) => [name, [rung.mean_display_luminance, rung.near_black_pct, rung.adaptationEv]]))) + '\n' : ''));
  }
  try { await Promise.race([proof(), deadline]); }
  catch (error) {
    failureSummary = (String(error.message) + ' | stack ' + String(error.stack || '').split(String.fromCharCode(10)).find(line => /lit-webgpu-browser(?:-proof)?\.mjs:\d+/.test(line))).replace(/\s+/g, ' ').slice(0, 1400);
    receipt.failure = error.message;
    fs.writeFileSync(path.join(run, 'failure-receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    throw new Error(error.message + '\nLit WebGPU failure evidence: ' + path.join(run, 'failure-receipt.json'), { cause: error });
  } finally {
    signal?.removeEventListener('abort', abort);
    clearTimeout(timer); await browser?.close();
    if (server) await new Promise(resolve => { server.closeAllConnections(); server.close(resolve); });
  }
}
