// Authored-room browser proof for L4 lit splats and points (?lit-splats=1) on
// the real renderer (WebGL, Spark): flag off is the identical frame and loads
// nothing; flag on relights Photo and Cloud from the SHA-256 bound authored
// lighting layer, the GPU blend equals its CPU twin, dimming darkens, and
// leaving Lit restores the exact frame. Authored packs and an authored layer
// only; no household pack, live camera, model session, physical command,
// screenshot or deployment.
import test from 'node:test';
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

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'), SRC = path.join(ROOT, 'app/src');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const WAIT_MS = 45000;
let failureSummary = null;
process.on('exit', () => { if (failureSummary) console.log('Lit splats browser failure: ' + failureSummary); });
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
  if (process.platform === 'win32') args.unshift('--use-angle=d3d11');
  else if (process.platform === 'linux') args.unshift('--use-angle=swiftshader', '--enable-unsafe-swiftshader');
  return { headless: true, args, ...(process.platform === 'linux' ? { channel: 'chromium' } : !installed ? { channel: 'msedge' } : {}) };
}

test('L4: flag off is the identical frame; flag on relights Photo and Cloud from the bound layer and restores exactly', { timeout: 180000 }, async () => {
  const run = path.join(ROOT, '.tmp/apartment-lit-splats-browser', 'run-' + Date.now()); fs.mkdirSync(run, { recursive: true });
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
  const capture = page => page.evaluate(async () => {
    const frame = await window.__APT_ENGINE.captureFrame({ scale: .25 });
    let sum = 0; const n = frame.width * frame.height;
    for (let i = 0; i < n; i++) sum += .2126 * frame.pixels[i * 4] + .7152 * frame.pixels[i * 4 + 1] + .0722 * frame.pixels[i * 4 + 2];
    let h = 0; for (let i = 0; i < frame.pixels.length; i++) h = (Math.imul(h, 31) + frame.pixels[i]) | 0;
    return { width: frame.width, height: frame.height, luma: sum / n, digest: h };
  });
  // A settled frame: two captures in a row, frames apart, are identical.
  async function stable(page) {
    let previous = await capture(page);
    for (let i = 0; i < 40; i++) {
      await page.evaluate(() => window.__APT_ENGINE.modes.whenSparkIdle()); await frames(page, 4);
      const next = await capture(page);
      if (next.digest === previous.digest) return next;
      previous = next;
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

    // ---- Flag off: Lit stays Mesh only, nothing lit loads, the frame is identical.
    const off = await openPage(context, '');
    await enterMode(off.page, 'Photo view', 'splat');
    const offBefore = await stable(off.page);
    const offState = await off.page.evaluate(async () => {
      const engine = window.__APT_ENGINE;
      return { available: engine.litAvailableIn('splat'), state: await engine.setLit(true), mode: engine.modes.mode };
    });
    assert.equal(offState.available, false); assert.equal(offState.mode, 'splat');
    assert.ok(!offState.state?.active, 'Lit does not start in Photo without the flag');
    await frames(off.page, 8);
    const offAfter = await stable(off.page);
    assert.deepEqual(offAfter, offBefore, 'flag off: the identical frame');
    assert.deepEqual(off.requested.filter(pathname => LIT_MODULES.test(pathname)), [], 'flag off: no lit receiver module or layer is requested');
    check('flag_off_identical', { frame: offBefore });
    await off.page.close();

    // ---- Flag on: Photo relit from the bound authored layer.
    const on = await openPage(context, '&lit-splats=1');
    const page = on.page;
    await enterMode(page, 'Photo view', 'splat');
    const unlit = await stable(page);
    // Separate page loads are not bit-reproducible (Spark's load and sort
    // timing); the exact identities are proved within one page above and below.
    assert.ok(Math.abs(unlit.luma - offBefore.luma) < 0.01, 'the flag alone changes nothing until Lit is on');
    await page.getByRole('button', { name: 'Lit view', exact: true }).click();
    await page.waitForFunction(() => window.__APT_ENGINE.litState?.()?.active === true, null, { timeout: WAIT_MS });
    assert.equal(await page.evaluate(() => window.__APT_ENGINE.modes.mode), 'splat', 'Lit stays in Photo with the flag');
    await settled(page);
    await page.evaluate(() => window.__APT_ENGINE.modes.whenSparkIdle()); await frames(page, 6);
    const litState = await page.evaluate(() => { const s = window.__APT_ENGINE.litState(); return { ...s, receivers: { ...s.receivers } }; });
    assert.equal(litState.pipeline, 'per-receiver-display');
    assert.equal(litState.receivers.kind, 'splat'); assert.equal(litState.receivers.set, 'splat'); assert.equal(litState.receivers.backend, 'webgl-gpgpu');
    assert.equal(litState.receivers.binding, 'sha256-verified'); assert.equal(litState.receivers.measured, false); assert.equal(litState.receivers.simulated, true);
    assert.equal(litState.receivers.matched, 28, 'every down and radial zone found its basis column');
    assert.ok(litState.receivers.blends >= 1);
    assert.equal(litState.disclosure, 'Simulated · not photometric · assumed output');
    assert.match(litState.disclosureDetail, /relit by simulation/);
    await page.waitForFunction(() => document.querySelector('[data-apt-lit-disclosure]'), null, { timeout: WAIT_MS });
    assert.ok((await page.locator('[data-apt-lit-disclosure]').textContent()).trim().startsWith('Simulated · not photometric · assumed output'), 'disclosed in Photo');
    const relit = await capture(page);
    assert.notEqual(relit.digest, unlit.digest, 'Photo is relit');
    const twin = await page.evaluate(() => window.__APT_ENGINE.litReceiversReadback(256));
    assert.ok(twin, 'the receivers expose a proof readback');
    const worst = Math.max(...twin.gpu.flatMap((row, i) => row.map((value, c) => Math.abs(value - twin.cpu[i][c]))));
    assert.ok(worst <= 2.5 / 255, 'the GPU blend equals its CPU twin within 8-bit rounding: ' + worst + ' ' + JSON.stringify([0, 1, 100, 200, 255].map(i => [twin.gpu[i], twin.cpu[i]])));
    check('photo_relit', { luma: { unlit: unlit.luma, lit: relit.luma }, blends: litState.receivers.blends, twin_max_abs: worst,
      adaptation_ev: litState.adaptationEv, metered_cd_m2: litState.meteredCdM2 });

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
    for (const level of [1, 0.6, 0.3, 0.1, 0]) ladder.push(await lumaAt(level));
    for (let i = 1; i < ladder.length; i++) assert.ok(ladder[i] <= ladder[i - 1] + 0.05, 'dimming never brightens: ' + ladder.join(' > '));
    assert.ok(ladder[0] > ladder.at(-1) + 1, 'all off is darker than all on: ' + ladder.join(' > '));
    check('dimming_monotone', { linear_view_luma: ladder });
    const restoredStates = await page.evaluate(original => original.map(([id, state]) => window.__APT_ENGINE.overlay.setVirtualFixtureState(id, state).ok), original);
    assert.ok(restoredStates.every(Boolean), 'the fixtures are back at their starting state');
    await frames(page, 3); await settled(page);
    await page.evaluate(() => window.__APT_ENGINE.setLitTone('agx'));

    // Leaving Lit restores the exact Photo frame.
    await page.getByRole('button', { name: 'Lit view', exact: true }).click();
    await page.waitForFunction(() => window.__APT_ENGINE.litState().active === false, null, { timeout: WAIT_MS });
    await page.evaluate(() => window.__APT_ENGINE.modes.whenSparkIdle()); await frames(page, 8);
    await page.evaluate(() => window.__APT_ENGINE.modes.whenSparkIdle()); await frames(page, 4);
    const restored = await stable(page);
    assert.deepEqual(restored, unlit, 'leaving Lit restores the identical Photo frame');
    check('photo_restored', { frame: restored });

    // ---- Cloud: the points relit, then restored.
    await enterMode(page, 'Cloud view', 'points');
    await page.getByRole('button', { name: 'Lit view', exact: true }).click();
    await page.waitForFunction(() => window.__APT_ENGINE.litState?.()?.active === true, null, { timeout: WAIT_MS });
    await settled(page);
    const cloud = await page.evaluate(() => { const s = window.__APT_ENGINE.litState(); return { ...s.receivers, material: (() => {
      let name = null; window.__APT_ENGINE.scene.traverse(node => { if (node.isPoints) name = node.material.name; }); return name; })() }; });
    assert.equal(cloud.kind, 'points'); assert.equal(cloud.set, 'points'); assert.equal(cloud.material, 'apartment-points-lit');
    const cloudTwin = await page.evaluate(() => window.__APT_ENGINE.litReceiversReadback(256));
    const cloudWorst = Math.max(...cloudTwin.gpu.flatMap((row, i) => row.map((value, c) => Math.abs(value - cloudTwin.cpu[i][c]))));
    assert.ok(cloudWorst <= 2.5 / 255, 'points: GPU equals the CPU twin: ' + cloudWorst);
    await page.getByRole('button', { name: 'Lit view', exact: true }).click();
    await page.waitForFunction(() => window.__APT_ENGINE.litState().active === false, null, { timeout: WAIT_MS });
    const cloudAfter = await page.evaluate(() => { let name = null; window.__APT_ENGINE.scene.traverse(node => { if (node.isPoints) name = node.material.name; }); return name; });
    assert.notEqual(cloudAfter, 'apartment-points-lit', 'the cloud material is restored');
    check('cloud_relit_and_restored', { blends: cloud.blends, twin_max_abs: cloudWorst });

    assert.ok(receipt.requests.every(pathname => pathname.startsWith(layer.base)), 'only the layer beside the pack is read');
    assert.deepEqual(Object.fromEntries(protectedFiles.map(file => [file, hash(fs.readFileSync(file))])), protectedHashes, 'pack bytes unchanged');
    assert.deepEqual(receipt.forbidden, []); assert.deepEqual(receipt.errors, []); assert.deepEqual(receipt.missing_code, []);
    receipt.ok = true;
    fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    process.stdout.write('Authored lit splats browser evidence: ' + path.join(run, 'receipt.json') + '\n');
  }
  try { await Promise.race([proof(), deadline]); }
  catch (error) {
    failureSummary = (String(error.message) + ' | stack ' + String(error.stack || '').split(String.fromCharCode(10)).find(line => /lit-splats-browser\.mjs:\d+/.test(line))).replace(/\s+/g, ' ').slice(0, 1400);
    receipt.failure = error.message;
    fs.writeFileSync(path.join(run, 'failure-receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    throw new Error(error.message + '\nLit splats failure evidence: ' + path.join(run, 'failure-receipt.json'), { cause: error });
  } finally {
    clearTimeout(timer); await browser?.close();
    if (server) await new Promise(resolve => { server.closeAllConnections(); server.close(resolve); });
  }
});
