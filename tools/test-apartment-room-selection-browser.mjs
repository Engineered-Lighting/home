// APT-R01: real Home shell, renderer, native input and authored room data.
// No household assets, service writes, sessions or private preview evidence.
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

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'), SRC = path.join(ROOT, 'app/src');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');

// Same public-code inventory fence as the retained full Home shell proof.
function publicSources(root) {
  const result = new Map(), excluded = new Set(['node_modules', 'data', 'artifacts', 'private', 'recordings', 'logs']);
  assert.ok(fs.lstatSync(root).isDirectory() && !fs.lstatSync(root).isSymbolicLink());
  let count = 0, bytes = 0;
  function visit(directory, depth = 0) {
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
  }
  visit(root); return result;
}

function browserOptions() {
  const installed = fs.existsSync(chromium.executablePath());
  assert.ok(installed || process.platform === 'win32', 'pinned Chromium must exist on Linux');
  const args = ['--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'];
  if (process.platform === 'win32') args.unshift('--use-angle=d3d11');
  else if (process.platform === 'linux') args.unshift('--use-gl=angle', '--use-angle=swiftshader-webgl', '--enable-unsafe-swiftshader');
  return { headless: true, args, ...(process.platform === 'linux' ? { channel: 'chromium' } : !installed ? { channel: 'msedge' } : {}) };
}

test('real authored room selection keeps native input ownership and never dispatches output', { timeout: 180000 }, async () => {
  assert.ok(!process.env.HOME_APARTMENT_CANDIDATE_ROOT, 'proof serves only this worktree');
  const run = path.join(ROOT, '.tmp/apartment-room-selection-browser', 'run-' + Date.now());
  fs.mkdirSync(run, { recursive: true });
  const packs = path.join(run, 'packs'), privateRoot = path.join(run, 'private');
  const echo = installPack(packs, 'echo'), victoria = installPack(packs, 'victoria');
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1,
    packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  const protectedFiles = [echo, victoria].flatMap(item => Object.keys(item.files).map(name => path.join(packs, item.site, item.revision, name)));
  protectedFiles.push(path.join(packs, 'echo/ACTIVE'), path.join(packs, 'victoria/ACTIVE'), path.join(packs, 'index.json'), path.join(privateRoot, 'registration.json'));
  const protectedHashes = Object.fromEntries(protectedFiles.map(file => [file, hash(fs.readFileSync(file))]));
  const sources = publicSources(SRC), receipt = { schema: 'home-apartment-room-selection-authored-browser-1',
    authored_room: true, real_renderer: true, complete_home_shell: true, household_data: false, screenshots: false,
    source_mode: 'current-worktree-only', source_sha256: Object.fromEntries(['home-apartment.jsx', 'home-3d/engine.js',
      'home-3d/picking.js', 'home-3d/direct-aim-gesture.mjs'].map(name => [name, hash(fs.readFileSync(path.join(SRC, name)))])),
    physical_commands: 0, saves: 0, camera_sessions: 0, model_sessions: 0,
    checks: [], requests: [], forbidden: [], blocked_read_subscriptions: [], missing_code: [], errors: [], served: {}, browser: browserOptions() };
  let server, handler, origin, browser, context, page, timer;
  const save = () => fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => {
    browser?.close().catch(() => {}); server?.closeAllConnections(); reject(Error('145s room selection browser deadline'));
  }, 145000); });
  const check = async (name, operation) => { receipt.current_stage = name; save(); const detail = await operation(); receipt.checks.push({ name, ...detail }); save(); };
  async function proof() {
    server = http.createServer((req, res) => {
      try {
        const url = new URL(req.url, 'http://localhost'); receipt.requests.push({ path: url.pathname, method: req.method });
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
        else { const pack = [echo, victoria].find(item => url.pathname.startsWith(item.base));
          if (pack) { body = pack.files[url.pathname.slice(pack.base.length)]; if (url.pathname.endsWith('.json')) type = 'application/json'; } }
        if (!body && url.pathname.startsWith('/proxy/')) {
          const value = url.pathname.endsWith('/model') || url.pathname.endsWith('/apartment_model') ? echo.seed
            : url.pathname.endsWith('/api/states') ? [] : url.pathname.endsWith('/api/config') ? { unit_system: { length: 'm' } } : {};
          body = Buffer.from(JSON.stringify(value)); type = 'application/json';
        }
        const entry = sources.get(url.pathname === '/' ? '/index.html' : url.pathname);
        if (!body && entry) {
          const relative = path.relative(fs.realpathSync(SRC), fs.realpathSync(entry.file)).split(path.sep).join('/');
          assert.equal(relative, entry.relative); assert.ok(!relative.startsWith('../') && !path.isAbsolute(relative));
          body = fs.readFileSync(entry.file); receipt.served[relative] = { sha256: hash(body), source: 'worktree' };
          type = /\.(?:js|mjs|jsx)$/.test(entry.file) ? 'text/javascript' : entry.file.endsWith('.html') ? 'text/html'
            : entry.file.endsWith('.css') ? 'text/css' : /\.(?:json|webmanifest)$/.test(entry.file) ? 'application/json' : entry.file.endsWith('.svg') ? 'image/svg+xml' : type;
        }
        if (!body) { if (/\.(?:js|mjs|jsx|css|wasm)$/.test(url.pathname)) receipt.missing_code.push(url.pathname); res.writeHead(404); res.end(); return; }
        res.writeHead(200, { 'Content-Type': type, 'Content-Length': body.length, 'Cache-Control': 'no-store' }); res.end(req.method === 'HEAD' ? undefined : body);
      } catch (error) { receipt.errors.push('authored HTTP handler: ' + error.message); res.writeHead(500); res.end(); }
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); origin = 'http://127.0.0.1:' + server.address().port;
    handler = createEquipmentRegistrationGateway({ env: { NODE_ENV: 'test', HOME_WEB_EQUIPMENT_REGISTRATION_ENABLED: '1',
      HOME_WEB_EQUIPMENT_REGISTRATION_ROOT: privateRoot, HOME_WEB_EQUIPMENT_REGISTRATION_ORIGIN: origin, HOME_CONTENT_PACKS_DIR: packs },
      authenticate: req => req.headers.cookie?.includes('authored_owner_session=1') === true });
    browser = await chromium.launch(receipt.browser); context = await browser.newContext({ viewport: { width: 1366, height: 768 }, deviceScaleFactor: 1, serviceWorkers: 'block' });
    receipt.runtime = { node: process.version, playwright: require('playwright/package.json').version, module: require.resolve('playwright'), browser: browser.version() };
    await context.addCookies([{ name: 'authored_owner_session', value: '1', url: origin }]);
    await context.addInitScript(() => { localStorage.setItem('apartment3d.site', 'echo'); localStorage.setItem('home.perf.backgroundWarmup', 'off'); window.HG_DISABLE_SERVICE_WORKER = true; });
    await context.route('**/*', route => {
      const request = route.request(), url = new URL(request.url());
      if (['data:', 'blob:'].includes(url.protocol)) return route.continue();
      if (url.origin !== origin) {
        const harmless = ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) && request.method() === 'GET' && url.pathname === '/healthz';
        if (!harmless) receipt.forbidden.push({ origin: url.origin, path: url.pathname, method: request.method() });
        return route.abort();
      }
      return route.continue();
    });
    await context.routeWebSocket('**/*', socket => {
      const url = new URL(socket.url());
      // The existing tracker subscription is a read channel, not a service,
      // save or session-start attempt. It stays blocked in this authored proof.
      if (url.host === new URL(origin).host && url.pathname === '/proxy/tracker/ws/tracks') receipt.blocked_read_subscriptions.push(url.pathname);
      else receipt.forbidden.push({ websocket: socket.url() });
      socket.close();
    });
    page = await context.newPage(); page.setDefaultTimeout(15000); page.on('pageerror', error => receipt.errors.push(error.message));
    await page.goto(origin + '/?apartmentRedesign=1&worldZoom=off', { waitUntil: 'domcontentloaded' });
    await page.locator('.apt-home-shell[data-home-layout="unified"]').waitFor({ timeout: 45000 });
    const waitSite = async site => page.waitForFunction(site => window.__HOME_APARTMENT_SITE_API?.readback().phase === 'ready' && window.__APT_ENGINE?.siteId === site, site);
    const selectHome = async site => { await page.getByRole('group', { name: 'Home', exact: true })
      .getByRole('button', { name: site === 'victoria' ? 'Victoria' : 'Echo Park', exact: true }).click(); await waitSite(site); };
    const blur = () => page.evaluate(() => document.activeElement?.blur());
    const fixtureInspector = () => page.locator('[data-victoria-virtual-fixtures="1"][aria-label="Fixture lighting inspector"]');
    const selected = () => page.evaluate(() => {
      const ids = []; window.__APT_ENGINE.overlay.virtualFixturesGroup.traverse(node => {
        if (node.name === 'virtual-fixture-aim-handle' && node.visible) ids.push(node.userData.fixture_id);
      }); return ids;
    });
    const expectSelected = async fixture => {
      await fixtureInspector().getByRole('heading', { name: fixture.label, exact: true }).waitFor();
      assert.deepEqual(await selected(), [fixture.fixture_id]);
    };
    const descriptor = id => page.evaluate(id => window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id), id);
    const canvasPoint = (kind, id, avoid = null) => page.evaluate(({ kind, id, avoid }) => {
      const engine = window.__APT_ENGINE, canvas = engine.renderer.domElement, host = engine.picking.getHost(), rect = host.getBoundingClientRect();
      const targets = engine.overlay.virtualFixturePickObjects(), candidates = [];
      if (kind === 'fixture') for (const object of targets.filter(node => node.userData.fixture_id === id && node.userData.interaction_role === 'fixture')) {
        const point = engine.picking.projectToScreen(object.getWorldPosition(engine.camera.position.clone()));
        if (point.visible) candidates.push({ x: rect.left + point.x, y: rect.top + point.y });
      }
      else for (const y of [.3, .5, .7, .85]) for (const x of [.15, .35, .55, .75, .9]) candidates.push({ x: rect.left + rect.width * x, y: rect.top + rect.height * y });
      for (const point of candidates) {
        if (document.elementFromPoint(point.x, point.y) !== canvas || avoid && Math.hypot(point.x - avoid.x, point.y - avoid.y) < 30) continue;
        const hits = engine.picking.pick(targets, point.x, point.y);
        if (kind === 'fixture' && hits[0]?.object.userData.fixture_id === id) return point;
        if (kind !== 'fixture' && !hits.length && (kind !== 'surface' || engine.displaySurfaceHit(point.x, point.y))) return point;
      }
      return null;
    }, { kind, id, avoid });
    await waitSite('echo');
    await page.getByRole('button', { name: 'Authored Echo light Unavailable', exact: true }).waitFor();
    await page.locator('[data-apt-semantic-device-select="1"] option[value="authored-echo-light"]').waitFor({ state: 'attached' });
    await page.waitForFunction(() => window.HomeOverlay?.topKey?.() === 'apartment-embedded'
      && window.__HOME_APARTMENT_SITE_API?.readback().loadedContentRevision === 'authored-echo-r1'
      && document.querySelector('[data-apt-semantic-device-select="1"]')?.disabled === false);
    await check('Echo N/P reaches the actual unpositioned semantic device without output', async () => {
      assert.equal(echo.seed.devices[0].pos, null); await blur(); await page.keyboard.press('n');
      await page.getByRole('button', { name: 'close card', exact: true }).waitFor();
      assert.match(await page.locator('.apt-home-controls').innerText(), /Authored Echo light/);
      await page.keyboard.press('p'); await page.getByRole('button', { name: 'close card', exact: true }).waitFor();
      await page.getByRole('button', { name: 'close card', exact: true }).click();
      return { id: echo.seed.devices[0].id, unpositioned: true };
    });
    await selectHome('victoria');
    await page.waitForFunction(() => window.__APT_ENGINE?.overlay.virtualFixturesReadback().fixture_count === 4);
    await page.evaluate(async () => { const engine = window.__APT_ENGINE; engine.rig.snapHome(0); await engine.modes.preload(['collision']); });
    const inventory = await page.evaluate(() => window.__APT_ENGINE.overlay.virtualFixturesReadback());
    assert.equal(inventory.visible, true); assert.equal(inventory.status, 'ready');
    const fixtures = inventory.fixtures;
    await page.evaluate(() => {
      window.__roomSelectionNativeClicks = []; window.__roomSelectionNativeEvents = [];
      for (const type of ['pointerdown', 'pointerup', 'lostpointercapture', 'click']) document.addEventListener(type, event => {
        const engine = window.__APT_ENGINE, canvas = engine?.renderer.domElement, host = engine?.picking.getHost();
        if (!host?.contains(event.target)) return;
        const row = { type, target: event.target === canvas ? 'canvas' : event.target === host ? 'host' : event.target.tagName,
          isTrusted: event.isTrusted, pointerId: event.pointerId, button: event.button, x: event.clientX, y: event.clientY,
          detail: event.detail, defaultPreventedAtCapture: event.defaultPrevented };
        window.__roomSelectionNativeEvents.push(row);
        queueMicrotask(() => { row.defaultPreventedAfterDispatch = event.defaultPrevented; });
        if (type === 'click' && event.target === canvas) window.__roomSelectionNativeClicks.push(row);
      }, true);
    });
    await check('native list and keyboard select the same registered fixture and retain the inspector tab', async () => {
      await page.getByRole('button', { name: 'Select ' + fixtures[1].label, exact: true }).click(); await expectSelected(fixtures[1]);
      await fixtureInspector().getByRole('tab', { name: 'Setup', exact: true }).click(); await page.keyboard.press('n'); await expectSelected(fixtures[2]);
      assert.equal(await fixtureInspector().getByRole('tab', { name: 'Setup', exact: true }).getAttribute('aria-selected'), 'true');
      await page.keyboard.press('p'); await expectSelected(fixtures[1]);
      for (const [key, index] of [['p', 0], ['p', 3], ['n', 0], ['n', 1]]) { await page.keyboard.press(key); await expectSelected(fixtures[index]); }
      assert.equal(await fixtureInspector().getByRole('tab', { name: 'Setup', exact: true }).getAttribute('aria-selected'), 'true');
      return { ids: [1, 2, 1, 0, 3, 0, 1].map(index => fixtures[index].fixture_id), wrapped_both_directions: true, retained_tab: 'setup' };
    });
    await check('native cylinder click selects the same fixture and keeps its controlled tab', async () => {
      await page.keyboard.press('n'); await expectSelected(fixtures[2]);
      const point = await canvasPoint('fixture', fixtures[1].fixture_id); assert.ok(point, 'actual fixture pick has an unobscured canvas point');
      await page.mouse.click(point.x, point.y); await expectSelected(fixtures[1]);
      assert.equal(await fixtureInspector().getByRole('tab', { name: 'Setup', exact: true }).getAttribute('aria-selected'), 'true');
      return { fixture_id: fixtures[1].fixture_id, point };
    });
    await check('empty orbit changes the real camera and preserves selection; empty click clears it', async () => {
      const point = await canvasPoint('empty'); assert.ok(point);
      const before = await page.evaluate(() => window.__APT_ENGINE.camera.matrixWorld.toArray());
      await page.mouse.move(point.x, point.y); await page.mouse.down(); await page.mouse.move(point.x + 35, point.y + 8, { steps: 8 }); await page.mouse.up();
      await page.waitForFunction(before => window.__APT_ENGINE.camera.matrixWorld.toArray().some((value, index) => value !== before[index]), before);
      await expectSelected(fixtures[1]);
      // The orbit assertion above is complete. Restore its original camera
      // before choosing the separate empty-click target, excluding inertia.
      await page.evaluate(async () => { window.__APT_ENGINE.rig.snapHome(0); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); });
      const empty = await canvasPoint('empty'); assert.ok(empty); await page.mouse.click(empty.x, empty.y);
      receipt.empty_click_diagnostic = { point: empty, selected: await selected(),
        native_events: await page.evaluate(() => window.__roomSelectionNativeEvents.slice(-16)),
        inspector: await page.locator('[data-victoria-virtual-fixtures="1"]').getAttribute('aria-label') }; save();
      await page.getByRole('complementary', { name: 'Room lighting inspector', exact: true }).waitFor(); assert.deepEqual(await selected(), []);
      await page.evaluate(() => window.__APT_ENGINE.rig.snapHome(0));
      return { camera_changed: true, orbit_preserved: true, click_cleared: true };
    });
    await page.getByRole('button', { name: 'Select ' + fixtures[0].label, exact: true }).click(); await expectSelected(fixtures[0]);
    const beforeAim = await descriptor(fixtures[0].fixture_id);
    await check('Escape cancels an actual active aim before the next Escape clears selection', async () => {
      const start = await canvasPoint('fixture', fixtures[0].fixture_id), end = await canvasPoint('surface', null, start); assert.ok(start && end);
      await page.mouse.move(start.x, start.y); await page.mouse.down(); await page.mouse.move(end.x, end.y, { steps: 8 });
      await page.waitForFunction(id => window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).target !== null, fixtures[0].fixture_id);
      await page.keyboard.press('Escape'); assert.deepEqual(await descriptor(fixtures[0].fixture_id), beforeAim); await expectSelected(fixtures[0]);
      await page.mouse.up(); await expectSelected(fixtures[0]); await blur(); await page.keyboard.press('Escape');
      await page.getByRole('complementary', { name: 'Room lighting inspector', exact: true }).waitFor(); assert.deepEqual(await selected(), []);
      return { canceled_snapshot_exact: true, first_escape_preserved_selection: true, second_escape_cleared: true };
    });
    await check('a real browser click after aim release preserves selection and Undo restores the exact preview', async () => {
      const start = await canvasPoint('fixture', fixtures[0].fixture_id), end = await canvasPoint('surface', null, start); assert.ok(start && end);
      const clickCount = await page.evaluate(() => window.__roomSelectionNativeClicks.length);
      await page.mouse.move(start.x, start.y); await page.mouse.down(); await page.mouse.move(end.x, end.y, { steps: 8 }); await page.mouse.up();
      await expectSelected(fixtures[0]); await fixtureInspector().getByRole('button', { name: 'Undo aim', exact: true }).waitFor();
      const after = await descriptor(fixtures[0].fixture_id); assert.notDeepEqual(after.target, beforeAim.target);
      const clicks = await page.evaluate(offset => window.__roomSelectionNativeClicks.slice(offset), clickCount);
      assert.ok(clicks.some(event => event.isTrusted && Math.hypot(event.x - end.x, event.y - end.y) <= 2),
        'actual browser compatibility click at the aim release, not a synthetic dispatch, was observed');
      await fixtureInspector().getByRole('button', { name: 'Undo aim', exact: true }).click();
      assert.deepEqual(await descriptor(fixtures[0].fixture_id), beforeAim);
      return { native_clicks: clicks, selected_fixture: fixtures[0].fixture_id, exact_undo: true };
    });
    await check('double click on the real canvas dispatches no service or save', async () => {
      const point = await canvasPoint('fixture', fixtures[0].fixture_id); assert.ok(point); await page.mouse.dblclick(point.x, point.y);
      assert.deepEqual(receipt.forbidden, []); return { fixture_id: fixtures[0].fixture_id };
    });
    assert.deepEqual(await page.evaluate(() => window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures), inventory.fixtures);
    assert.equal(await page.evaluate(() => window.__APT_ENGINE.overlay.virtualFixturesReadback().visible), true);
    await selectHome('echo'); await page.getByRole('button', { name: 'Authored Echo light Unavailable', exact: true }).click();
    await page.getByRole('button', { name: 'close card', exact: true }).waitFor();
    await check('native Echo list uses the same semantic device after a home change', async () => {
      assert.match(await page.locator('.apt-home-controls').innerText(), /Authored Echo light/); return { id: echo.seed.devices[0].id };
    });
    for (const [file, expected] of Object.entries(protectedHashes)) assert.equal(hash(fs.readFileSync(file)), expected, 'authored pack and registration bytes stay exact');
    for (const [file, expected] of Object.entries(receipt.source_sha256)) assert.equal(receipt.served[file]?.sha256, expected, 'served production source matches the captured candidate');
    assert.deepEqual(receipt.forbidden, []); assert.deepEqual(receipt.missing_code, []); assert.deepEqual(receipt.errors, []);
    assert.equal(receipt.requests.some(request => !['GET', 'HEAD'].includes(request.method)), false);
    receipt.native_canvas_clicks = await page.evaluate(() => window.__roomSelectionNativeClicks);
    receipt.authored_data_hashes_unchanged = true; receipt.fixture_display_on = true;
  }
  try { await Promise.race([proof(), deadline]); receipt.result = 'passed'; }
  catch (error) {
    receipt.result = 'failed'; receipt.failure = error.stack || String(error); save();
    if (page && !page.isClosed()) receipt.failure_state = await page.evaluate(() => ({
      selected: (() => { const ids = []; window.__APT_ENGINE?.overlay.virtualFixturesGroup?.traverse(node => {
        if (node.name === 'virtual-fixture-aim-handle' && node.visible) ids.push(node.userData.fixture_id);
      }); return ids; })(), inspector: document.querySelector('[data-victoria-virtual-fixtures="1"]')?.getAttribute('aria-label'),
      events: window.__roomSelectionNativeEvents?.slice(-20), clicks: window.__roomSelectionNativeClicks?.slice(-10),
      overlay: window.HomeOverlay?.topKey?.(), activeElement: document.activeElement?.outerHTML?.slice(0, 600),
      site: window.__HOME_APARTMENT_SITE_API?.readback(), controls: document.querySelector('.apt-home-controls')?.innerText?.slice(0, 1600),
    })).catch(() => null);
    save(); throw error;
  }
  finally {
    clearTimeout(timer); save();
    for (const [name, close] of [['context', () => context?.close()], ['browser', () => browser?.close()],
      ['server', async () => { server?.closeAllConnections(); if (server) await new Promise(resolve => server.close(resolve)); }]]) {
      try { await close(); } catch (error) { (receipt.cleanup_errors ||= []).push({ name, message: error.message }); }
    }
    save(); console.log(JSON.stringify({ receipt: path.join(run, 'receipt.json'), result: receipt.result, checks: receipt.checks.length }));
  }
});
