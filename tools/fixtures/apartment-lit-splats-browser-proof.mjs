// Lighting in every view (L4 lit splats and points) browser proof, shared by
// tools/test-apartment-lit-splats-browser.mjs (part 1: Lighting off and Photo)
// and its -part2 twin (part 2: Lighting following the view through Cloud and
// Mesh, and the node renderer's WebGL2 backend), so neither part nears the
// 180 s file limit. On the real renderer (WebGL, Spark): with Lighting off the
// frame is identical and nothing lit loads; with it on, Photo and Cloud are
// relit from the SHA-256 bound authored lighting layer, the GPU blend equals
// its CPU twin, dimming darkens, and turning Lighting off restores the exact
// frame. Authored packs and an authored layer only; no household pack, live
// camera, model session, physical command, screenshot or deployment.
//   HOME_LIT_SPLATS_RENDERER  'webgl' (default: Spark) or 'webgpu' (the splat
//                             fork's colour hook and compute; needs a real
//                             adapter, so local only, with
//   HOME_LIT_WEBGPU_CHANNEL   a browser channel that has one, e.g. 'msedge').
// Every run also proves that the node renderer's WebGL2 backend declares lit
// receivers unavailable and leaves the frame untouched.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import Core from '../../app/src/home-equipment-registration.js';
import { createEquipmentRegistrationGateway, initRegistration, readActiveBinding } from '../../web-gateway/equipment-registration.mjs';
import { installPack, authoredDocument } from './apartment-home-shell-fixtures.mjs';
import { authoredLightingLayer } from './apartment-lit-layer-fixtures.mjs';
import { authoredHdrFrame, compareFramePasses, PROOF_PARAMS, PROOF_SIZE } from './lit-frame-proof.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..'), SRC = path.join(ROOT, 'app/src');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const WAIT_MS = 45000;
let failureSummary = null;
process.on('exit', () => { if (failureSummary) console.log('Lit splats browser failure: ' + failureSummary); });
const RENDERER = process.env.HOME_LIT_SPLATS_RENDERER || 'webgl';
assert.ok(['webgl', 'webgpu'].includes(RENDERER), 'HOME_LIT_SPLATS_RENDERER is webgl or webgpu');
const LIT_MODULES = /\/home-3d\/lit-(?:receivers|basis-blend|layer-loader)\.mjs|\.lighting\//;

function publicSources(root) {
  const result = new Map(), excluded = new Set(['node_modules', 'data', 'artifacts', 'private', 'recordings', 'logs']);
  (function visit(directory, depth = 0) {
    assert.ok(depth <= 12);
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (entry.name.startsWith('.') || excluded.has(entry.name)) continue;
      const file = path.join(directory, entry.name), relative = path.relative(root, file).split(path.sep).join('/'), stat = fs.lstatSync(file);
      if (relative === 'assets/apartment' || relative.startsWith('assets/apartment/') || stat.isSymbolicLink()) continue;
      if (stat.isDirectory()) { visit(file, depth + 1); continue; }
      const vendor = /^(?:vendor|fonts|icons)\//.test(relative) && /\.(?:wasm|woff2?|ttf|otf|png|jpe?g|webp|svg|ico|ktx2|json)$/.test(entry.name);
      const rootAsset = directory === root && ['index.html', 'favicon.svg', 'favicon.ico', 'favicon-32.png', 'apple-touch-icon.png', 'site.webmanifest'].includes(entry.name);
      if (!/\.(?:js|mjs|jsx|css)$/.test(entry.name) && !vendor && !rootAsset) continue;
      result.set('/' + relative, { file, relative });
    }
  })(root);
  return result;
}
function browserOptions() {
  const installed = fs.existsSync(chromium.executablePath());
  assert.ok(installed || process.platform === 'win32', 'pinned Chromium must exist on Linux');
  const args = ['--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'];
  if (RENDERER === 'webgpu') args.push('--enable-unsafe-webgpu', '--ignore-gpu-blocklist');
  if (process.platform === 'win32') args.unshift('--use-angle=d3d11');
  else if (process.platform === 'linux') args.unshift('--use-angle=swiftshader', '--enable-unsafe-swiftshader');
  const channel = RENDERER === 'webgpu' ? process.env.HOME_LIT_WEBGPU_CHANNEL || null : null;
  return { headless: true, args, ...(channel ? { channel } : process.platform === 'linux' ? { channel: 'chromium' } : !installed ? { channel: 'msedge' } : {}) };
}
const RENDERER_QUERY = RENDERER === 'webgpu' ? '&renderer=webgpu' : '';

export async function runLitSplatsBrowserProof({ part }) {
  assert.ok(part === 1 || part === 2, 'part 1 or 2');
  const run = path.join(ROOT, '.tmp/apartment-lit-splats-browser', 'part' + part + '-run-' + Date.now()); fs.mkdirSync(run, { recursive: true });
  const packs = path.join(run, 'packs'), privateRoot = path.join(run, 'private');
  const echo = installPack(packs, 'echo'), victoria = installPack(packs, 'victoria');
  const layer = authoredLightingLayer(victoria);
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1,
    packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  const protectedFiles = [echo, victoria].flatMap(item => Object.keys(item.files).map(name => path.join(packs, item.site, item.revision, name)));
  const protectedHashes = Object.fromEntries(protectedFiles.map(file => [file, hash(fs.readFileSync(file))]));
  const sources = publicSources(SRC), receipt = { schema: 'home-apartment-lit-splats-authored-browser-1', authored_room: true, authored_layer: true,
    real_renderer: true, household_data: false, physical_commands: 0, screenshots: false, checks: [], forbidden: [], missing_code: [], errors: [], requests: [] };
  const check = (stage, detail = {}) => receipt.checks.push({ stage, ...detail });
  // Opt-in stills of the authored room for local review; never part of the pass criteria.
  const still = async (page, name) => { if (process.env.HOME_LIT_SPLATS_STILLS === '1') await page.screenshot({ path: path.join(run, name + '.png') }); };
  let server, browser, origin, handler, timer;
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => {
    browser?.close().catch(() => {}); server?.closeAllConnections(); reject(Error('170s lit splats browser deadline'));
  }, 170000); });

  async function openPage(context, query) {
    const page = await context.newPage(); page.setDefaultTimeout(15000);
    page.on('pageerror', error => receipt.errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error' || message.type() === 'warning') (receipt.console ||= []).push(message.text().slice(0, 600)); });
    const requested = [];
    page.on('request', request => requested.push(new URL(request.url()).pathname));
    await page.goto(origin + '/?apartmentRedesign=1&worldZoom=off' + query, { waitUntil: 'domcontentloaded' });
    await page.locator('.apt-home-shell[data-home-layout="unified"]').waitFor({ timeout: WAIT_MS });
    await page.waitForFunction(() => window.__APT_ENGINE?.siteId === 'victoria'
      && window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures.length === 4, null, { timeout: WAIT_MS });
    return { page, requested };
  }
  const frames = (page, n = 3) => page.evaluate(n => new Promise(resolve => { let k = 0; const step = () => (++k >= n ? resolve() : requestAnimationFrame(step)); requestAnimationFrame(step); }), n);
  /* A frame's size, mean luma and digest; frames are kept in the page by
   * name so two can be compared pixel by pixel (WebGPU's per-frame GPU sort
   * is not bit-reproducible, so that path compares within a noise floor). */
  const capture = (page, name = 'last') => page.evaluate(async name => {
    const frame = await window.__APT_ENGINE.captureFrame({ scale: .25 });
    let sum = 0; const n = frame.width * frame.height;
    for (let i = 0; i < n; i++) sum += .2126 * frame.pixels[i * 4] + .7152 * frame.pixels[i * 4 + 1] + .0722 * frame.pixels[i * 4 + 2];
    let h = 0; for (let i = 0; i < frame.pixels.length; i++) h = (Math.imul(h, 31) + frame.pixels[i]) | 0;
    (window.__litFrames ||= {})[name] = frame.pixels.slice();
    return { width: frame.width, height: frame.height, luma: sum / n, digest: h };
  }, name);
  const frameDiff = (page, a, b) => page.evaluate(([a, b]) => {
    const x = window.__litFrames[a], y = window.__litFrames[b];
    let total = 0, max = 0, over = 0;
    for (let i = 0; i < x.length; i++) { const d = Math.abs(x[i] - y[i]); total += d; max = Math.max(max, d); if (d > 8) over++; }
    return { mean: total / x.length, max, over8: over / x.length };
  }, [a, b]);
  // WebGL: bit-identical. WebGPU: within the unlit path's own measured
  // frame-to-frame noise (twice it, with a small floor).
  const NOISE = { mean: 0.05, over8: 0.001 };
  let measuredNoise = null;
  async function assertSameFrame(page, a, b, message) {
    const diff = await frameDiff(page, a, b);
    if (RENDERER === 'webgl') assert.equal(diff.max, 0, message + ' ' + JSON.stringify(diff));
    else {
      assert.ok(measuredNoise, 'the WebGPU noise floor is measured first');
      const mean = Math.max(NOISE.mean, 2 * measuredNoise.mean), over8 = Math.max(NOISE.over8, 2 * measuredNoise.over8);
      assert.ok(diff.mean <= mean && diff.over8 <= over8, message + ' ' + JSON.stringify({ diff, noise: measuredNoise }));
    }
    return diff;
  }
  // A settled frame: two captures in a row, frames apart, are identical.
  async function stable(page, name = 'stable') {
    let previous = await capture(page, name + ':a');
    for (let i = 0; i < 40; i++) {
      await page.evaluate(() => window.__APT_ENGINE.modes.whenSparkIdle()); await frames(page, 4);
      const next = await capture(page, name);
      const diff = await frameDiff(page, name, name + ':a');
      if (RENDERER === 'webgl' ? diff.max === 0 : diff.mean <= NOISE.mean && diff.over8 <= NOISE.over8) return next;
      previous = await capture(page, name + ':a');
    }
    throw new Error('the frame never settled: ' + JSON.stringify(previous));
  }
  async function enterMode(page, label, mode) {
    await page.getByRole('button', { name: label, exact: true }).click();
    await page.waitForFunction(m => window.__APT_ENGINE.modes.mode === m, mode, { timeout: WAIT_MS });
    await page.evaluate(() => window.__APT_ENGINE.rig.snapHome(0));
    await page.evaluate(() => window.__APT_ENGINE.modes.whenSparkIdle());
    await frames(page, 12);
    await page.evaluate(() => window.__APT_ENGINE.modes.whenSparkIdle());
    await frames(page, 4);
  }
  const settled = page => page.waitForFunction(() => {
    const state = window.__APT_ENGINE.litState();
    return state?.active && state.easing === false && state.adapting === false && state.meterSerial >= 2;
  }, null, { timeout: WAIT_MS });

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
        else if (url.pathname.startsWith(layer.base)) { body = layer.files[decodeURIComponent(url.pathname.slice(layer.base.length))]; receipt.requests.push(url.pathname); }
        else { const pack = [echo, victoria].find(item => url.pathname.startsWith(item.base)); if (pack) { body = pack.files[url.pathname.slice(pack.base.length)]; if (url.pathname.endsWith('.json')) type = 'application/json'; } }
        if (!body && url.pathname.startsWith('/proxy/')) {
          const value = url.pathname.endsWith('/model') || url.pathname.endsWith('/apartment_model') ? echo.seed
            : url.pathname.endsWith('/api/states') ? [] : url.pathname.endsWith('/api/config') ? { unit_system: { length: 'm' } } : {};
          body = Buffer.from(JSON.stringify(value)); type = 'application/json';
        }
        const entry = sources.get(url.pathname === '/' ? '/index.html' : url.pathname);
        if (!body && entry) {
          body = fs.readFileSync(entry.file);
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
    browser = await chromium.launch(browserOptions());
    const context = await browser.newContext({ viewport: { width: 1280, height: 720 }, serviceWorkers: 'block', reducedMotion: 'reduce' });
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

    if (part === 1) {
    // ---- Lighting off: nothing lit loads and the frame is the identical frame.
    const on = await openPage(context, RENDERER_QUERY);
    if (RENDERER === 'webgpu') assert.equal(await on.page.evaluate(() => window.__APT_ENGINE.renderer.backend?.isWebGPUBackend === true), true,
      'a real WebGPU adapter (set HOME_LIT_WEBGPU_CHANNEL)');
    const page = on.page;
    const lighting = page.getByRole('button', { name: 'Lighting', exact: true });
    assert.equal(await page.getByRole('button', { name: 'Lit view', exact: true }).count(), 0, 'Lighting is a toggle, not a view tab');
    assert.equal(await lighting.getAttribute('aria-pressed'), 'false', 'Lighting is off by default');
    await enterMode(page, 'Photo view', 'splat');
    const unlit = await stable(page, 'photo:unlit');
    await still(page, 'photo-unlit');
    await frames(page, 12);
    await stable(page, 'photo:unlit2');
    // Nothing lit runs while Lighting is off (no lit module or layer is even
    // requested): on WebGL that is the identical frame; on WebGPU this
    // difference is the unlit splat path's own noise, the floor used later.
    const offDiff = RENDERER === 'webgl' ? await assertSameFrame(page, 'photo:unlit2', 'photo:unlit', 'Lighting off: the identical frame')
      : (measuredNoise = await frameDiff(page, 'photo:unlit2', 'photo:unlit'));
    assert.deepEqual(on.requested.filter(pathname => LIT_MODULES.test(pathname)), [], 'Lighting off: no lit receiver module or layer is requested');
    check('lighting_off_identical', { frame: unlit, diff: offDiff });

    await lighting.click();
    await page.waitForFunction(() => window.__APT_ENGINE.litState?.()?.active === true, null, { timeout: WAIT_MS });
    assert.equal(await page.evaluate(() => window.__APT_ENGINE.modes.mode), 'splat', 'Photo stays the view, now lit');
    assert.equal(await page.getByRole('button', { name: 'Photo view', exact: true }).getAttribute('aria-pressed'), 'true');
    await settled(page);
    await page.evaluate(() => window.__APT_ENGINE.modes.whenSparkIdle()); await frames(page, 6);
    const litState = await page.evaluate(() => { const s = window.__APT_ENGINE.litState(); return { ...s, receivers: { ...s.receivers } }; });
    assert.equal(litState.pipeline, 'scene-referred-half-float', 'one HDR frame; the look runs once per pixel');
    assert.equal(litState.frame.format, 'RGBA16F'); assert.equal(litState.frame.bloom, true); assert.equal(litState.look, 'camera');
    assert.ok(litState.frame.emitters >= 4, 'the lit fixtures show their faces: ' + litState.frame.emitters);
    assert.ok(litState.frame.emittersClamped >= 1, 'a spot lens is beyond half float once pre-exposed: stored clamped and tagged');
    assert.equal(litState.receivers.kind, 'splat'); assert.equal(litState.receivers.set, 'splat');
    assert.equal(litState.receivers.backend, RENDERER === 'webgpu' ? 'webgpu-compute' : 'webgl-gpgpu');
    assert.equal(litState.receivers.binding, 'sha256-verified'); assert.equal(litState.receivers.measured, false); assert.equal(litState.receivers.simulated, true);
    assert.equal(litState.receivers.matched, 28, 'every down and radial zone found its basis column');
    assert.ok(litState.receivers.blends >= 1);
    assert.equal(litState.disclosure, 'Simulated · not photometric · assumed output');
    assert.match(litState.disclosureDetail, /relit by simulation/);
    await page.waitForFunction(() => document.querySelector('[data-apt-lit-disclosure]'), null, { timeout: WAIT_MS });
    assert.ok((await page.locator('[data-apt-lit-disclosure]').textContent()).trim().startsWith('Simulated · not photometric · assumed output'), 'disclosed in Photo');
    assert.equal((await page.locator('[data-apt-lit-bounce-limit]').textContent()).trim(),
      'Precomputed light basis · live spots re-baked with shadows · night ambient through the windows · capture sheen dropped', 'Photo states its own limits');
    const relit = await capture(page, 'photo:lit');
    await still(page, 'photo-lit');
    const relitDiff = await frameDiff(page, 'photo:lit', 'photo:unlit');
    assert.ok(relitDiff.mean > 1, 'Photo is relit: ' + JSON.stringify(relitDiff));
    const twin = await page.evaluate(() => window.__APT_ENGINE.litReceiversReadback(256));
    assert.ok(twin, 'the receivers expose a proof readback');
    // Scene-referred radiance in half float: relative error within fp16 rounding.
    const worst = Math.max(...twin.gpu.flatMap((row, i) => row.map((value, c) => Math.abs(value - twin.cpu[i][c]) / (Math.abs(twin.cpu[i][c]) + 1e-4))));
    assert.ok(worst <= 2e-3, 'the GPU blend equals its CPU twin within half-float rounding: ' + worst + ' ' + JSON.stringify([0, 1, 100, 200, 255].map(i => [twin.gpu[i], twin.cpu[i]])));
    check('photo_relit', { luma: { unlit: unlit.luma, lit: relit.luma }, blends: litState.receivers.blends, twin_max_rel: worst,
      adaptation_ev: litState.adaptationEv, metered_cd_m2: litState.meteredCdM2, frame: litState.frame });

    // The frame's own passes on this renderer: an authored HDR frame through
    // the real bloom and display passes equals their CPU twins; dimming every
    // light at once darkens the display monotonically at a fixed exposure.
    const runFrame = scale => page.evaluate(async ({ image, size, params }) => {
      const result = await window.__APT_ENGINE.litFrameProof(Float32Array.from(image), size.width, size.height, params);
      return { ...result, frame: Array.from(result.frame), mip: { ...result.mip, data: Array.from(result.mip.data) }, display: Array.from(result.display) };
    }, { image: Array.from(authoredHdrFrame({ scale })), size: PROOF_SIZE, params: PROOF_PARAMS });
    const framedRaw = await runFrame(1);
    fs.writeFileSync(path.join(run, 'frame-proof.json'), JSON.stringify(framedRaw));
    const framed = compareFramePasses(framedRaw);
    assert.ok(framed.mip_rel <= 6e-3, 'the bloom passes equal their CPU twin: ' + JSON.stringify(framed));
    assert.ok(framed.display_max <= 2, 'the display pass equals its CPU twin within 8-bit rounding: ' + JSON.stringify(framed));
    const dimmed = [framed, compareFramePasses(await runFrame(0.5)), compareFramePasses(await runFrame(0.25))].map(row => row.mean_display_luma);
    for (let i = 1; i < dimmed.length; i++) assert.ok(dimmed[i] < dimmed[i - 1], 'dimming darkens the display: ' + dimmed.join(' > '));
    check('frame_passes_equal_cpu_twin', { ...framed, dimmed_display_luma: dimmed });

    // Presentation never enters the linear readback: the look, glare and noise change, the luminance does not.
    const linearDigest = () => page.evaluate(async () => {
      const read = await window.__APT_ENGINE.litLinearReadback({ scale: 0.125 });
      let h = 0, sum = 0; for (let i = 0; i < read.luminance.length; i++) { h = (Math.imul(h, 31) + Math.round(read.luminance[i] * 1e4)) | 0; sum += read.luminance[i]; }
      return { h, mean: sum / read.luminance.length, n: read.luminance.length, measured: read.measured };
    });
    await page.evaluate(() => window.__APT_ENGINE.modes.whenSparkIdle()); await frames(page, 4);
    const linearCamera = await linearDigest();
    await page.evaluate(() => window.__APT_ENGINE.setLitLook('eye')); await frames(page, 4);
    const linearEye = await linearDigest();
    await page.evaluate(() => window.__APT_ENGINE.setLitLook('camera')); await frames(page, 4);
    // WebGL: bit-identical. WebGPU's per-frame sort is not bit-reproducible: the same mean within its own noise.
    if (RENDERER === 'webgl') assert.deepEqual(linearEye, linearCamera, 'the linear readback is free of the look, glare and noise');
    else {
      const again = await linearDigest(), noise = Math.abs(again.mean - linearCamera.mean);
      assert.ok(Math.abs(linearEye.mean - linearCamera.mean) <= Math.max(2 * noise, 2e-3 * linearCamera.mean),
        'the linear readback is free of the look, glare and noise ' + JSON.stringify({ linearCamera, linearEye, again }));
    }
    assert.equal(linearCamera.measured, false);
    check('linear_readback_presentation_free', linearCamera);

    // Live spots: L5's bake posts each spot's column for the splats; re-aiming
    // one re-bakes only that column and the blend follows.
    await page.waitForFunction(() => window.__APT_ENGINE.litState().receivers?.liveSpots === 4, null, { timeout: WAIT_MS });
    const beforeAim = await page.evaluate(() => { const s = window.__APT_ENGINE.litState(); return { blends: s.receivers.blends, spotMs: s.bounce.receiverSpotMs }; });
    const aimed = await page.evaluate(() => {
      const engine = window.__APT_ENGINE, id = engine.overlay.virtualFixturesReadback().fixtures[0].fixture_id;
      window.__litSpotSnapshot = [id, engine.overlay.virtualFixtureInteractionDescriptor(id)];
      return engine.overlay.aimVirtualFixtureAtTarget(id, { point: [3.4, 3.4, 0], normal: [0, 0, 1], kind: 'authored_floor' }).ok;
    });
    assert.equal(aimed, true);
    await page.waitForFunction(before => {
      const s = window.__APT_ENGINE.litState();
      return s.receivers.blends > before.blends && s.bounce.receiverSpotMs !== null && window.__APT_ENGINE.overlay.virtualFixturesPresentationActive() === false;
    }, beforeAim, { timeout: WAIT_MS });
    await settled(page);
    const spotState = await page.evaluate(() => { const s = window.__APT_ENGINE.litState(); return { liveSpots: s.receivers.liveSpots, spotMs: s.bounce.receiverSpotMs,
      unbaked: s.receivers.unbaked.length, backend: s.bounce.backend, status: s.bounce.status }; });
    assert.equal(spotState.unbaked, 0, 'every light has a basis column or a live spot column');
    check('live_spots', spotState);
    assert.equal(await page.evaluate(() => window.__APT_ENGINE.overlay.restoreVirtualFixtureAim(...window.__litSpotSnapshot).ok), true);
    await page.waitForFunction(() => window.__APT_ENGINE.overlay.virtualFixturesPresentationActive() === false, null, { timeout: WAIT_MS });

    // Dimming: in the linear comparison view (no adaptation), the frame darkens monotonically.
    await page.evaluate(() => window.__APT_ENGINE.setLitTone('linear'));
    const ids = await page.evaluate(() => window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures.map(item => item.fixture_id));
    const original = await page.evaluate(() => window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures
      .map(item => [item.fixture_id, { downward: item.state.downward, spotlight: item.state.spotlight,
        radials: item.state.radials.map(({ number, on, brightness, color_temp_kelvin }) => ({ number, on, brightness, color_temp_kelvin })) }]));
    const lumaAt = async brightness => {
      await page.evaluate(({ ids, brightness }) => {
        for (const id of ids) window.__APT_ENGINE.overlay.setVirtualFixtureState(id, { downward: { brightness }, spotlight: { on: false },
          radials: [1, 2, 3, 4, 5, 6].map(number => ({ number, brightness })) });
      }, { ids, brightness });
      await frames(page, 3); await settled(page);
      await page.evaluate(() => window.__APT_ENGINE.modes.whenSparkIdle()); await frames(page, 6);
      return (await capture(page)).luma;
    };
    const ladder = [];
    for (const level of [1, 0.6, 0.3, 0.1, 0]) { ladder.push(await lumaAt(level)); await still(page, 'photo-lit-linear-' + level); }
    for (let i = 1; i < ladder.length; i++) assert.ok(ladder[i] <= ladder[i - 1] + 0.05, 'dimming never brightens: ' + ladder.join(' > '));
    assert.ok(ladder[0] > ladder.at(-1) + 1, 'all off is darker than all on: ' + ladder.join(' > '));
    check('dimming_monotone', { linear_view_luma: ladder });
    const restoredStates = await page.evaluate(original => original.map(([id, state]) => window.__APT_ENGINE.overlay.setVirtualFixtureState(id, state).ok), original);
    assert.ok(restoredStates.every(Boolean), 'the fixtures are back at their starting state');
    await frames(page, 3); await settled(page);
    await page.evaluate(() => window.__APT_ENGINE.setLitTone('agx'));

    // Lighting off restores the exact Photo frame.
    await lighting.click();
    await page.waitForFunction(() => window.__APT_ENGINE.litState().active === false, null, { timeout: WAIT_MS });
    await page.evaluate(() => window.__APT_ENGINE.modes.whenSparkIdle()); await frames(page, 8);
    await page.evaluate(() => window.__APT_ENGINE.modes.whenSparkIdle()); await frames(page, 4);
    const restored = await stable(page, 'photo:restored');
    const restoreDiff = await assertSameFrame(page, 'photo:restored', 'photo:unlit', 'leaving Lit restores the identical Photo frame');
    check('photo_restored', { frame: restored, diff: restoreDiff });

    await page.close();
    } else {
    // ---- Lighting follows the view: Photo lit, then Cloud and Mesh lit in turn
    // without touching the toggle; Lighting off restores every view exactly.
    const page = (await openPage(context, RENDERER_QUERY)).page;
    const lighting = page.getByRole('button', { name: 'Lighting', exact: true });
    const pointMaterials = () => page.evaluate(() => { const rows = []; window.__APT_ENGINE.scene.traverse(node => {
      if (node.isPoints) rows.push(node.material.uuid, ...node.children.filter(child => child.isSprite).map(child => child.material.uuid)); }); return rows; });
    await enterMode(page, 'Cloud view', 'points');
    const cloudBefore = await pointMaterials();
    await still(page, 'cloud-unlit');
    await enterMode(page, 'Photo view', 'splat');
    await lighting.click();
    await page.waitForFunction(() => window.__APT_ENGINE.litState?.()?.receivers?.kind === 'splat', null, { timeout: WAIT_MS });
    await page.getByRole('button', { name: 'Cloud view', exact: true }).click();
    await page.waitForFunction(() => window.__APT_ENGINE.modes.mode === 'points' && window.__APT_ENGINE.litState?.()?.receivers?.kind === 'points',
      null, { timeout: WAIT_MS });
    assert.equal(await lighting.getAttribute('aria-pressed'), 'true', 'Lighting stays on across views');
    await settled(page);
    await still(page, 'cloud-lit');
    const cloud = await page.evaluate(() => ({ ...window.__APT_ENGINE.litState().receivers }));
    assert.equal(cloud.kind, 'points'); assert.equal(cloud.set, 'points');
    assert.notDeepEqual(await pointMaterials(), cloudBefore, 'the cloud draws with its lit material');
    const cloudTwin = await page.evaluate(() => window.__APT_ENGINE.litReceiversReadback(256));
    const cloudWorst = Math.max(...cloudTwin.gpu.flatMap((row, i) => row.map((value, c) => Math.abs(value - cloudTwin.cpu[i][c]))));
    assert.ok(cloudWorst <= 2.5 / 255, 'points: GPU equals the CPU twin: ' + cloudWorst);
    check('cloud_lit_by_following_the_view', { blends: cloud.blends, twin_max_abs: cloudWorst });
    // Mesh: the relit scan mesh (Tier-0 + L5), still without touching the toggle.
    await page.getByRole('button', { name: 'Mesh view', exact: true }).click();
    await page.waitForFunction(() => window.__APT_ENGINE.modes.mode === 'mesh' && window.__APT_ENGINE.litState?.()?.active === true
      && window.__APT_ENGINE.litState().receivers === null, null, { timeout: WAIT_MS });
    const mesh = await page.evaluate(() => { const s = window.__APT_ENGINE.litState(); return { lights: s.lights, meshSwapped: s.meshSwapped }; });
    assert.equal(mesh.lights, 32); assert.ok(mesh.meshSwapped > 0, 'the scan mesh is relit');
    assert.deepEqual(await pointMaterials(), cloudBefore, 'leaving Cloud restored its materials');
    check('mesh_lit_by_following_the_view', mesh);
    await lighting.click();
    await page.waitForFunction(() => !window.__APT_ENGINE.litState()?.active && window.__APT_ENGINE.litState()?.wanted === false, null, { timeout: WAIT_MS });
    await page.waitForFunction(() => document.querySelector('[aria-label="Lighting"]')?.getAttribute('aria-pressed') === 'false', null, { timeout: WAIT_MS });

    await page.close();

    // ---- The node renderer's WebGL2 backend: declared unavailable, frame untouched.
    const fallback = await openPage(context, '&renderer=webgpu-webgl');
    await enterMode(fallback.page, 'Photo view', 'splat');
    await stable(fallback.page, 'fallback:before');
    // This backend's CPU-sorted splats tie on the authored grid too: measure its noise first.
    await frames(fallback.page, 12); await stable(fallback.page, 'fallback:noise');
    const fallbackNoise = await frameDiff(fallback.page, 'fallback:noise', 'fallback:before');
    await fallback.page.getByRole('button', { name: 'Lighting', exact: true }).click();
    await fallback.page.waitForFunction(() => !!window.__APT_ENGINE.litState()?.receiversRefused, null, { timeout: WAIT_MS });
    const refused = await fallback.page.evaluate(() => ({ ...window.__APT_ENGINE.litState() }));
    assert.equal(refused.active, false); assert.equal(refused.wanted, true);
    await fallback.page.waitForFunction(() => document.querySelector('[data-apt-lit-refused]'), null, { timeout: WAIT_MS });
    assert.match(await fallback.page.locator('[data-apt-lit-refused]').textContent(), /this renderer can't relight Photo or Cloud\./);
    assert.equal(refused.receiversRefused, 'lit_receivers_unavailable_on_webgl2_backend');
    await stable(fallback.page, 'fallback:after');
    const fallbackDiff = await frameDiff(fallback.page, 'fallback:after', 'fallback:before');
    assert.ok(fallbackDiff.mean <= Math.max(NOISE.mean, 2 * fallbackNoise.mean) && fallbackDiff.over8 <= Math.max(NOISE.over8, 2 * fallbackNoise.over8),
      'refused Lit leaves the frame as it was ' + JSON.stringify({ fallbackDiff, fallbackNoise }));
    assert.deepEqual(fallback.requested.filter(pathname => /\.lighting\//.test(pathname)), [], 'refused before any layer is read');
    check('node_webgl2_backend_declares_unavailable', { reason: refused.receiversRefused, diff: fallbackDiff, noise: fallbackNoise });
    }

    assert.ok(receipt.requests.every(pathname => pathname.startsWith(layer.base)), 'only the layer beside the pack is read');
    assert.deepEqual(Object.fromEntries(protectedFiles.map(file => [file, hash(fs.readFileSync(file))])), protectedHashes, 'pack bytes unchanged');
    assert.deepEqual(receipt.forbidden, []); assert.deepEqual(receipt.errors, []); assert.deepEqual(receipt.missing_code, []);
    receipt.ok = true;
    fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    process.stdout.write('Authored lit splats browser evidence: ' + path.join(run, 'receipt.json') + '\n');
  }
  try { await Promise.race([proof(), deadline]); }
  catch (error) {
    failureSummary = (String(error.message) + ' | stack ' + String(error.stack || '').split(String.fromCharCode(10)).find(line => /lit-splats-browser(?:-proof)?\.mjs:\d+/.test(line))).replace(/\s+/g, ' ').slice(0, 1400);
    receipt.failure = error.message;
    fs.writeFileSync(path.join(run, 'failure-receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    throw new Error(error.message + '\nLit splats failure evidence: ' + path.join(run, 'failure-receipt.json'), { cause: error });
  } finally {
    clearTimeout(timer); await browser?.close();
    if (server) await new Promise(resolve => { server.closeAllConnections(); server.close(resolve); });
  }
}
