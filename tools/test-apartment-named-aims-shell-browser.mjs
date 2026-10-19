#!/usr/bin/env node
// Echo named aims in the mounted Home shell: the real renderer, the real
// HomeApartmentLiveView mount points and Simulation Mode over an authored Echo
// pack and an authored saved layout (one ceiling light, a painting on the
// back wall). The HANDOFF "Drag aim" example runs end to end: the reticle
// snaps to the painting, release proposes, Set moves only the simulated aim.
// Zero non-GET requests, forbidden routes or WebSocket sends. No household
// data, screenshots, service writes or hardware.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { installPack } from './fixtures/apartment-home-shell-fixtures.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'), SRC = path.join(ROOT, 'app/src');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
// Fixed before the first run (2026-10-19); never raised to make a run pass.
const BROWSER_DEADLINE_MS = 145000;

// The authored layout Simulation Mode turns into an engineered light: the
// spotlight sits so the painting is 1.85 m away at 59° from straight down.
const SPOT = [2, 2.404, 2.453], PAINTING = [2, 3.99, 1.5];
const LAYOUT = { schema_version: 1, revision: 7, exists: true, site_id: 'echo', zones: [],
  devices: [{ id: 'authored-echo-spot', type: 'light', name: 'Authored spot', ha_entity_id: 'light.authored_spot', pos: SPOT,
    height_preset: 'ceiling', room_id: null, controllable: true, source: 'manual' }],
  targets: [
    { id: 'authored-painting', name: 'Painting', category: 'art', shape: 'surface', pos: PAINTING, normal: [0, -1, 0], up: [0, 0, 1], size_m: [0.9, 0.65], source: 'authored' },
    { id: 'authored-floor-spot', name: 'Floor spot', category: 'custom', shape: 'point', pos: [2, 2, 0], normal: [0, 0, 1], up: [0, 1, 0], size_m: [0, 0], source: 'authored' }],
  meta: { frame: 'z_up_metric_floor0', site_id: 'echo', authored_test: true } };

function sources(root) {
  const result = new Map(), excluded = new Set(['node_modules', 'data', 'artifacts', 'private', 'recordings', 'logs']);
  (function visit(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (entry.name.startsWith('.') || excluded.has(entry.name)) continue;
      const file = path.join(directory, entry.name), relative = path.relative(root, file).split(path.sep).join('/');
      if (relative === 'assets/apartment' || relative.startsWith('assets/apartment/')) continue;
      if (entry.isDirectory()) { visit(file); continue; }
      if (/\.(?:js|mjs|jsx|css|html|woff2?|svg|png|ico|webmanifest|wasm|json)$/.test(entry.name)) result.set('/' + relative, file);
    }
  })(root);
  return result;
}

const run = path.join(ROOT, '.tmp/apartment-named-aims-shell-browser', 'run-' + Date.now());
fs.mkdirSync(run, { recursive: true });
const packs = path.join(run, 'packs'), echo = installPack(packs, 'echo'), victoria = installPack(packs, 'victoria');
const index = Buffer.from(JSON.stringify({ schemaVersion: 1, packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
const files = sources(SRC);
// Simulation Mode's bundled synthetic Echo fixtures, exactly the tracked four.
for (const name of ['frame.json', 'manifest.json', 'points.sim.ply', 'seed-model.json']) files.set('/assets/apartment/' + name, path.join(SRC, 'assets/apartment', name));
const receipt = { schema: 'home-apartment-named-aims-shell-browser-1', authored_room: true, simulation_mode: true, household_data: false,
  screenshots: false, source_sha256: Object.fromEntries(['home-apartment.jsx', 'home-apartment-named-aim.jsx', 'home-apartment-named-aims.js',
    'home-apartment-echo-aim.js', 'home-apartment-aim.jsx', 'home-apartment-chat-aim.js', 'home-app.jsx'].map(name => [name, hash(fs.readFileSync(path.join(SRC, name)))])),
  checks: [], requests: [], forbidden: [], websocket_sends: [], errors: [] };
const save = () => fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');

let server, browser, timer, page;
async function proof() {
  server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost'); receipt.requests.push({ path: url.pathname, method: req.method });
    if (!['GET', 'HEAD'].includes(req.method) || /\/api\/services\/|call_service/.test(url.pathname)) {
      receipt.forbidden.push({ path: url.pathname, method: req.method }); res.writeHead(403); res.end(); return;
    }
    let body = null, type = 'application/octet-stream';
    if (url.pathname === '/content-packs/index.json') { body = index; type = 'application/json'; }
    // The local runtime recovery export Simulation Mode builds its light layout from.
    else if (url.pathname === '/assets/apartment/backups/current-layout.json') { body = Buffer.from(JSON.stringify(LAYOUT)); type = 'application/json'; }
    else { const pack = [echo, victoria].find(item => url.pathname.startsWith(item.base)); if (pack) body = pack.files[url.pathname.slice(pack.base.length)] || null; }
    if (!body && url.pathname.startsWith('/proxy/')) { body = Buffer.from(JSON.stringify(url.pathname.endsWith('/api/states') ? [] : {})); type = 'application/json'; }
    const file = files.get(url.pathname === '/' ? '/index.html' : url.pathname);
    if (!body && file) {
      body = fs.readFileSync(file);
      type = /\.(?:js|mjs|jsx)$/.test(file) ? 'text/javascript' : file.endsWith('.html') ? 'text/html' : file.endsWith('.css') ? 'text/css'
        : /\.(?:json|webmanifest)$/.test(file) ? 'application/json' : file.endsWith('.svg') ? 'image/svg+xml' : type;
    }
    if (!body) { (receipt.missing ||= []).push(url.pathname); res.writeHead(404); res.end(); return; }
    if (url.pathname.endsWith('.json')) type = 'application/json';
    res.writeHead(200, { 'Content-Type': type, 'Content-Length': body.length, 'Cache-Control': 'no-store' }); res.end(req.method === 'HEAD' ? undefined : body);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  const installed = fs.existsSync(chromium.executablePath());
  const args = ['--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'];
  if (process.platform === 'win32') args.unshift('--use-angle=d3d11');
  else if (process.platform === 'linux') args.unshift('--use-gl=angle', '--use-angle=swiftshader-webgl', '--enable-unsafe-swiftshader');
  browser = await chromium.launch({ headless: true, args, ...(process.platform === 'linux' ? { channel: 'chromium' } : !installed ? { channel: 'msedge' } : {}) });
  const context = await browser.newContext({ viewport: { width: 1366, height: 768 }, deviceScaleFactor: 1, serviceWorkers: 'block' });
  await context.addInitScript(() => {
    localStorage.setItem('apartment3d.site', 'echo'); localStorage.setItem('home.perf.backgroundWarmup', 'off');
    window.HG_DISABLE_SERVICE_WORKER = true;
  });
  await context.route('**/*', route => {
    const request = route.request(), url = new URL(request.url());
    if (['data:', 'blob:'].includes(url.protocol)) return route.continue();
    if (url.origin !== origin) { receipt.forbidden.push({ origin: url.origin, path: url.pathname, method: request.method() }); return route.abort(); }
    return route.continue();
  });
  await context.routeWebSocket('**/*', socket => { socket.onMessage(message => receipt.websocket_sends.push({ url: socket.url(), bytes: String(message).length })); socket.close(); });
  page = await context.newPage(); page.setDefaultTimeout(20000);
  page.on('pageerror', error => receipt.errors.push(error.message));
  const check = async (name, operation) => { const started = Date.now(); const detail = await operation(); receipt.checks.push({ name, ms: Date.now() - started, ...detail }); save(); };
  await page.goto(origin + '/?apartmentRedesign=1&worldZoom=off&simulation=1', { waitUntil: 'domcontentloaded' });
  await page.locator('.apt-home-shell[data-home-layout="unified"]').waitFor({ timeout: 60000 });
  await page.waitForFunction(() => window.__HOME_APARTMENT_SITE_API?.readback().phase === 'ready' && window.__APT_ENGINE?.siteId === 'echo', null, { timeout: 60000 });
  // Frame the authored 4 × 4 m room; Simulation Mode's bundled frame is the larger synthetic apartment.
  await page.evaluate(async () => {
    const engine = window.__APT_ENGINE; await engine.modes.preload(['collision']);
    let mesh = null; engine.modes.getCollision().traverse(node => { if (!mesh && node.isMesh) mesh = node; });
    mesh.geometry.computeBoundingBox();
    const Box = mesh.geometry.boundingBox.constructor, Vector = engine.camera.position.constructor, root = engine.apartmentRoot;
    root.updateMatrixWorld(true);
    const box = new Box().setFromPoints([[0, 0, 0], [4, 4, 2.5], [0, 4, 0], [4, 0, 2.5]].map(point => new Vector(...point).applyMatrix4(root.matrixWorld)));
    engine.rig.fitBounds(box, { dur: 0 });
    await new Promise(resolve => setTimeout(resolve, 300));
    // Orbit to the open side of the room, where the painting is visible.
    const rect = engine.picking.getHost().getBoundingClientRect();
    const sees = () => { const at = engine.picking.projectToScreen(new Vector(2, 3.99, 1.5).applyMatrix4(root.matrixWorld));
      const hit = engine.displaySurfaceHit(rect.left + at.x, rect.top + at.y);
      return hit && Math.hypot(hit.point[0] - 2, hit.point[1] - 3.99, hit.point[2] - 1.5) < .1; };
    for (let stop = 0; stop < 8; stop++) {
      if (sees()) { window.__namedAimStop = stop; break; }
      engine.rig.stepAzimuth(1, 0);
      await new Promise(resolve => setTimeout(resolve, 250));
    }
  });

  await check('The room lists Echo’s named aims and opens the painting’s inspector', async () => {
    await page.locator('[data-apt-named-aim-open="authored-painting"]').click();
    const inspector = page.locator('[data-apt-named-aim-inspector="authored-painting"]');
    await inspector.waitFor();
    assert.match(await inspector.innerText(), /Can reach · simulated/);
    await inspector.locator('[data-apt-named-aim-aim-here="authored-echo-spot"]').click();
    await page.locator('[data-apt-named-aim-rows]').waitFor();
    const runtime = await page.evaluate(() => window.__SIM_APARTMENT_AIM_RUNTIME.read('authored-echo-spot').current_destination);
    assert.equal(runtime.id, 'authored-floor-spot', 'Aim a light here only proposes');
    assert.match(await page.locator('[data-apt-named-aim-rows]').innerText(), /Proposed\s*Painting · 1\.85 m · 59° tilt/);
    await page.locator('[data-apt-aim-tab-action="discard"]').click();
    return { proposal_only: true };
  });

  await check('HANDOFF Drag aim: the reticle snaps to the painting; Set moves only the simulated aim', async () => {
    await page.waitForFunction(() => { const node = document.querySelector('[data-apt-aim-reticle]'); return node && !node.hidden; });
    const from = await page.evaluate(() => { const rect = document.querySelector('[data-apt-aim-reticle]').getBoundingClientRect(); return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }; });
    const to = await page.evaluate(point => { const engine = window.__APT_ENGINE, rect = engine.picking.getHost().getBoundingClientRect();
      const Vector = engine.camera.position.constructor, at = engine.picking.projectToScreen(new Vector(...point).applyMatrix4(engine.apartmentRoot.matrixWorld));
      return { x: rect.left + at.x, y: rect.top + at.y, visible: at.visible }; }, [2.1, 3.99, 1.45]);
    assert.ok(to.visible, 'the painting is in view');
    receipt.orbit_stop = await page.evaluate(() => window.__namedAimStop ?? null);
    await page.mouse.move(from.x, from.y); await page.mouse.down(); await page.mouse.move(to.x, to.y, { steps: 10 });
    await page.waitForFunction(() => !document.querySelector('[data-apt-aim-chip]')?.hidden);
    const chip = await page.locator('[data-apt-aim-chip]').innerText();
    assert.match(chip, /Snaps to “Painting”/); assert.match(chip, /1\.85 m · 59° tilt/);
    await page.mouse.up();
    await page.waitForFunction(() => { const bar = document.querySelector('[data-apt-aim-bar]'); return bar && !bar.hidden; });
    assert.equal(await page.locator('[data-apt-aim-bar-target]').innerText(), 'Painting');
    assert.match(await page.locator('[data-apt-named-aim-rows]').innerText(), /Aim\s*Simulated/);
    assert.equal((await page.evaluate(() => window.__SIM_APARTMENT_AIM_RUNTIME.read('authored-echo-spot').current_destination)).id, 'authored-floor-spot');
    await page.locator('[data-apt-aim-action="set"]').click();
    await page.waitForFunction(() => window.__SIM_APARTMENT_AIM_RUNTIME.read('authored-echo-spot').current_destination.id === 'authored-painting');
    await page.waitForFunction(() => /no hardware command sent/.test(document.body.innerText));
    return { chip: chip.replace(/\s+/g, ' ').trim(), set: 'simulated' };
  });
  await check('C04 Chat aim: the card proposes in cyan, its Set equals the bar Set, and nothing is dispatched', async () => {
    // Count every other route a submitted message could take.
    await page.evaluate(() => {
      const spy = window.__chatAimSpy = { lighting: 0, memory: 0, sockets: 0, hooked: [] };
      for (const [name, key] of [['HomeLightingControl', 'lighting'], ['HomePersonalMemory', 'memory']]) {
        const api = window[name]; if (!api?.run) continue;
        const run = api.run.bind(api); api.run = (...args) => { spy[key]++; return run(...args); }; spy.hooked.push(name);
      }
      const Socket = window.WebSocket; window.WebSocket = function (...args) { spy.sockets++; return new Socket(...args); };
    });
    const before = { requests: receipt.requests.length, sends: receipt.websocket_sends.length };
    await page.getByRole('tab', { name: /^Chat/ }).click();
    const composer = page.getByRole('combobox', { name: 'Command input' });
    await composer.fill('aim the authored spot at the floor spot');
    await composer.press('Enter');
    const card = page.locator('[data-apt-chat-aim-card]');
    await card.waitFor();
    assert.equal(await card.getAttribute('data-origin'), 'chat');
    assert.match(await card.innerText(), /Authored spot → Floor spot[\s\S]*Proposed · simulated/);
    await page.waitForFunction(() => { const bar = document.querySelector('[data-apt-aim-bar]'); return bar && !bar.hidden; });
    assert.equal(await page.locator('[data-apt-aim-bar-target]').innerText(), 'Floor spot', 'the same proposal shows in the room');
    assert.equal((await page.evaluate(() => window.__SIM_APARTMENT_AIM_RUNTIME.read('authored-echo-spot').current_destination)).id, 'authored-painting');
    await card.locator('[data-apt-chat-aim-action="set"]').click();
    await page.waitForFunction(() => window.__SIM_APARTMENT_AIM_RUNTIME.read('authored-echo-spot').current_destination.id === 'authored-floor-spot');
    await page.waitForFunction(() => document.querySelector('[data-apt-chat-aim-card]')?.getAttribute('data-state') === 'set');
    assert.equal(await page.locator('[data-apt-aim-bar]').isHidden(), true, 'Set from the card clears the room bar too');
    const spy = await page.evaluate(() => window.__chatAimSpy);
    assert.deepEqual(spy, { lighting: 0, memory: 0, sockets: 0, hooked: ['HomeLightingControl', 'HomePersonalMemory'] }, 'no lighting-control or memory dispatch and no WebSocket');
    const later = receipt.requests.slice(before.requests);
    assert.equal(later.some(request => !['GET', 'HEAD'].includes(request.method) || /\/api\/|\/proxy\//.test(request.path)), false,
      'no agent, service or proxy request');
    assert.equal(receipt.websocket_sends.length, before.sends);
    return { card: 'set', dispatches: spy };
  });
  assert.deepEqual(receipt.forbidden, [], 'no forbidden route, other origin or non-GET request');
  assert.equal(receipt.requests.some(request => !['GET', 'HEAD'].includes(request.method)), false, 'zero non-GET requests');
  assert.deepEqual(receipt.websocket_sends, []); assert.deepEqual(receipt.errors, []);
}

try {
  await Promise.race([proof(), new Promise((_, reject) => { timer = setTimeout(() => reject(Error('145s named-aims shell deadline')), BROWSER_DEADLINE_MS); })]);
  receipt.result = 'passed';
} catch (error) {
  receipt.result = 'failed'; receipt.failure = error.stack || String(error); process.exitCode = 1;
  if (page && !page.isClosed()) receipt.failure_state = await page.evaluate(() => ({ site: window.__HOME_APARTMENT_SITE_API?.readback?.(),
    engine: window.__APT_ENGINE?.siteId || null, controls: document.querySelector('.apt-home-controls')?.innerText?.slice(0, 800),
    body: document.body.innerText.slice(0, 600) })).catch(() => null);
}
finally {
  clearTimeout(timer); save();
  await browser?.close().catch(() => {}); server?.closeAllConnections?.(); server?.close();
  console.log(JSON.stringify({ receipt: path.join(run, 'receipt.json'), result: receipt.result, checks: receipt.checks.length, failure: receipt.failure?.split('\n')[0] }));
}
