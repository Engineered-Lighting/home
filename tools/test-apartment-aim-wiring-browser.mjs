// APT-R01, A01, A02, A03, A04, A05, A08 and F04 on the mounted Home shell: the
// in-room aim overlay wired to the real renderer, native input and authored
// Victoria room data. HANDOFF "Direct selection", "Drag aim" (no snap) and
// "Escape order", plus tap and keyboard aiming, Chat open, the phone sheet,
// the Spot pending dot and slider power-on. No household assets, service
// writes, WebSocket sends, sessions or private preview evidence.
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
// Fixed before the first run: 145 s browser proof, 180 s for the file.
const BROWSER_DEADLINE_MS = 145000, FILE_TIMEOUT_MS = 180000;
const OPTICAL = 'Modeled from the socket display point · optical pivot not registered';
const TRAVEL = 'travel limits not commissioned';

// Same public-code inventory fence as the retained Home shell proofs.
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

test('the in-room aim overlay proposes, sets, undoes and escapes through the mounted Home shell without output', { timeout: FILE_TIMEOUT_MS }, async () => {
  assert.ok(!process.env.HOME_APARTMENT_CANDIDATE_ROOT, 'proof serves only this worktree');
  const run = path.join(ROOT, '.tmp/apartment-aim-wiring-browser', 'run-' + Date.now());
  fs.mkdirSync(run, { recursive: true });
  const packs = path.join(run, 'packs'), privateRoot = path.join(run, 'private');
  const echo = installPack(packs, 'echo'), victoria = installPack(packs, 'victoria');
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1,
    packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  const protectedFiles = [echo, victoria].flatMap(item => Object.keys(item.files).map(name => path.join(packs, item.site, item.revision, name)));
  protectedFiles.push(path.join(packs, 'echo/ACTIVE'), path.join(packs, 'victoria/ACTIVE'), path.join(packs, 'index.json'), path.join(privateRoot, 'registration.json'));
  const protectedHashes = Object.fromEntries(protectedFiles.map(file => [file, hash(fs.readFileSync(file))]));
  const sources = publicSources(SRC), receipt = { schema: 'home-apartment-aim-wiring-authored-browser-1',
    authored_room: true, synthetic_victoria: true, real_renderer: true, complete_home_shell: true, household_data: false, screenshots: false,
    source_mode: 'current-worktree-only', source_sha256: Object.fromEntries(['home-apartment.jsx', 'home-apartment-room-overlay.jsx',
      'home-apartment-aim-proposals.js', 'home-3d/apartment-aim-presentation.mjs', 'home-3d/engine.js', 'home-app.jsx']
      .map(name => [name, hash(fs.readFileSync(path.join(SRC, name)))])),
    deadlines: { browser_ms: BROWSER_DEADLINE_MS, file_ms: FILE_TIMEOUT_MS },
    physical_commands: 0, saves: 0, camera_sessions: 0, model_sessions: 0,
    checks: [], requests: [], forbidden: [], websocket_sends: [], blocked_read_subscriptions: [], missing_code: [], errors: [], served: {}, browser: browserOptions() };
  let server, handler, origin, browser, context, page, timer;
  const save = () => fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => {
    receipt.deadline_at = Date.now(); save();
    browser?.close().catch(() => {}); server?.closeAllConnections(); reject(Error('145s aim wiring browser deadline'));
  }, BROWSER_DEADLINE_MS); });
  const check = async (name, operation) => {
    receipt.current_stage = name;
    const phase = { name, started_at: Date.now(), completed: false };
    (receipt.phases ||= []).push(phase); save();
    try { const detail = await operation(); receipt.checks.push({ name, ...detail }); phase.completed = true; }
    finally { phase.duration_ms = Date.now() - phase.started_at; save(); }
  };
  async function proof() {
    server = http.createServer((req, res) => {
      try {
        const url = new URL(req.url, 'http://localhost'); receipt.requests.push({ path: url.pathname, method: req.method });
        if (!['GET', 'HEAD'].includes(req.method) || /\/api\/services\/|call_service|\/capture(?:\/|$)|\/acquisition(?:\/|$)|\/control(?:\/|$)|\/sessions\/[^/]+\/(?:start|stop)(?:\/|$)/.test(url.pathname)) {
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
          body = fs.readFileSync(entry.file); receipt.served[entry.relative] = { sha256: hash(body), source: 'worktree' };
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
    browser = await chromium.launch(receipt.browser);
    context = await browser.newContext({ viewport: { width: 1366, height: 768 }, deviceScaleFactor: 1, serviceWorkers: 'block' });
    receipt.runtime = { node: process.version, playwright: require('playwright/package.json').version, browser: browser.version() };
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
      socket.onMessage(message => receipt.websocket_sends.push({ url: socket.url(), bytes: String(message).length }));
      if (url.host === new URL(origin).host && url.pathname === '/proxy/tracker/ws/tracks') receipt.blocked_read_subscriptions.push(url.pathname);
      else receipt.forbidden.push({ websocket: socket.url() });
      socket.close();
    });
    page = await context.newPage(); page.setDefaultTimeout(15000); page.on('pageerror', error => receipt.errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') (receipt.console_errors ||= []).push(message.text().slice(0, 400)); });
    await page.goto(origin + '/?apartmentRedesign=1&worldZoom=off', { waitUntil: 'domcontentloaded' });
    await page.locator('.apt-home-shell[data-home-layout="unified"]').waitFor({ timeout: 45000 });
    const waitSite = async site => page.waitForFunction(site => window.__HOME_APARTMENT_SITE_API?.readback().phase === 'ready' && window.__APT_ENGINE?.siteId === site, site);
    await waitSite('echo');
    await page.getByRole('group', { name: 'Home', exact: true }).getByRole('button', { name: 'Victoria', exact: true }).click();
    await waitSite('victoria');
    await page.waitForFunction(() => window.__APT_ENGINE?.overlay.virtualFixturesReadback().fixture_count === 4
      && document.querySelectorAll('[data-apt-fixture-label]').length === 4);
    await page.evaluate(async () => { const engine = window.__APT_ENGINE; engine.rig.snapHome(0); await engine.modes.preload(['collision']); });
    const inventory = await page.evaluate(() => window.__APT_ENGINE.overlay.virtualFixturesReadback());
    const fixtures = inventory.fixtures, ids = fixtures.map(item => item.fixture_id);

    const descriptor = id => page.evaluate(id => window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id), id);
    const pose = () => page.evaluate(() => { const value = window.__APT_ENGINE.rig.snapshotPose?.();
      return value && { az: value.az, el: value.el, zoom: value.zoom, previewAz: value.previewAz, previewEl: value.previewEl }; });
    const blur = () => page.evaluate(() => document.activeElement?.blur());
    const inspector = () => page.locator('[data-victoria-virtual-fixtures="1"]');
    const fixtureInspector = () => page.locator('[data-victoria-virtual-fixtures="1"][aria-label="Fixture lighting inspector"]');
    const selectedTab = () => page.evaluate(() => [...document.querySelectorAll('[role="tablist"][aria-label="Light sections"] [role="tab"]')]
      .find(tab => tab.getAttribute('aria-selected') === 'true')?.textContent || null);
    const title = () => page.evaluate(() => document.querySelector('[aria-label="Fixture lighting inspector"] .apt-ui-title')?.textContent || null);
    const overlayState = () => page.evaluate(() => {
      const one = selector => document.querySelector(selector);
      const shown = node => !!node && !node.hidden && node.getClientRects().length > 0;
      const bar = one('[data-apt-aim-bar]'), chip = one('[data-apt-aim-chip]'), reticle = one('[data-apt-aim-reticle]');
      const rect = reticle?.getBoundingClientRect();
      return { bar: shown(bar) ? { state: bar.getAttribute('data-state'), text: bar.innerText } : null,
        chip: shown(chip) ? { state: chip.getAttribute('data-state'), text: chip.innerText,
          disclosures: [...chip.querySelectorAll('[data-apt-aim-disclosure]')].map(node => [node.getAttribute('data-apt-aim-disclosure'), node.textContent]) } : null,
        reticle: shown(reticle) ? { state: reticle.getAttribute('data-state'), x: rect.left + rect.width / 2, y: rect.top + rect.height / 2,
          width: rect.width, height: rect.height, label: reticle.getAttribute('aria-label') } : null,
        proposal_marker: shown(one('[data-apt-aim-proposal]')),
        labels: Object.fromEntries([...document.querySelectorAll('[data-apt-fixture-label]')].map(node =>
          [node.getAttribute('data-apt-fixture-label'), shown(node) ? node.getAttribute('data-state') : 'hidden'])),
        rings: Object.fromEntries([...document.querySelectorAll('[data-apt-fixture-ring]')].map(node =>
          [node.getAttribute('data-apt-fixture-ring'), shown(node) ? node.getAttribute('data-state') : 'hidden'])) };
    });
    const until = (name, predicate, argument) => page.waitForFunction(predicate, argument, { timeout: 15000 })
      .catch(error => { throw Error(name + ': ' + error.message); });
    // A canvas point whose visible pick is the fixture body, an empty room
    // point, or a registered surface point clear of every fixture.
    const canvasPoint = (kind, id, avoid = null) => page.evaluate(({ kind, id, avoid }) => {
      const engine = window.__APT_ENGINE, canvas = engine.renderer.domElement, rect = engine.picking.getHost().getBoundingClientRect();
      const targets = engine.overlay.virtualFixturePickObjects(), candidates = [];
      if (kind === 'fixture') for (const object of targets.filter(node => node.userData.fixture_id === id && node.userData.interaction_role === 'fixture')) {
        const point = engine.picking.projectToScreen(object.getWorldPosition(engine.camera.position.clone()));
        if (point.visible) candidates.push({ x: rect.left + point.x, y: rect.top + point.y });
      }
      else for (const y of [.3, .5, .7, .85]) for (const x of [.15, .35, .55, .75, .9]) candidates.push({ x: rect.left + rect.width * x, y: rect.top + rect.height * y });
      for (const point of candidates) {
        if (document.elementFromPoint(point.x, point.y) !== canvas || avoid && Math.hypot(point.x - avoid.x, point.y - avoid.y) < 60) continue;
        const hits = engine.picking.pick(targets, point.x, point.y);
        if (kind === 'fixture' && hits[0]?.object.userData.fixture_id === id) return point;
        if (kind !== 'fixture' && !hits.length && (kind !== 'surface' || engine.displaySurfaceHit(point.x, point.y))) return point;
      }
      return null;
    }, { kind, id, avoid });
    const surfaceHit = point => page.evaluate(({ x, y }) => { const hit = window.__APT_ENGINE.displaySurfaceHit(x, y);
      return hit && { point: [...hit.point], normal: [...hit.normal] }; }, point);
    const near = (a, b, metres = .03) => Math.hypot(...a.map((value, axis) => value - b[axis])) < metres;
    const settle = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    // Release over a surface point bound to the actual pointer-up frame.
    async function reticleDrag(end, { release = true } = {}) {
      const at = (await overlayState()).reticle;
      assert.ok(at, 'the reticle is visible for the selected aimable light');
      assert.equal(await page.evaluate(({ x, y }) => !!document.elementFromPoint(x, y)?.closest('[data-apt-aim-reticle]'), at), true,
        'the press lands on the reticle itself');
      await page.evaluate(() => { window.__aimRelease = null; window.addEventListener('pointerup', event => {
        const hit = window.__APT_ENGINE.displaySurfaceHit(event.clientX, event.clientY);
        window.__aimRelease = { x: event.clientX, y: event.clientY, hit: hit && { point: [...hit.point], normal: [...hit.normal] } };
      }, { capture: true, once: true }); });
      await page.mouse.move(at.x, at.y); await page.mouse.down();
      await page.mouse.move(end.x, end.y, { steps: 10 });
      await until('drag chip', () => !document.querySelector('[data-apt-aim-chip]')?.hidden);
      const during = await overlayState();
      if (!release) return { at, during };
      await page.mouse.up();
      return { at, during, released: await page.evaluate(() => window.__aimRelease) };
    }

    await check('Direct selection: label, cylinder and list row open the same Light inspector; hover changes nothing', async () => {
      const target = fixtures[1];
      const initial = await overlayState();
      assert.deepEqual(Object.values(initial.labels), ['idle', 'idle', 'idle', 'idle'], 'four or fewer lights: every label shows');
      await page.locator(`[data-apt-fixture-label="${target.fixture_id}"]`).click();
      await fixtureInspector().waitFor();
      assert.equal(await title(), target.label); assert.equal(await selectedTab(), 'Light', 'the first selection opens Light');
      await until('selected label and ring', id => document.querySelector(`[data-apt-fixture-label="${id}"]`)?.getAttribute('data-state') === 'selected'
        && document.querySelector(`[data-apt-fixture-ring="${id}"]`)?.getAttribute('data-state') === 'selected', target.fixture_id);
      const other = fixtures[2], before = { title: await title(), tab: await selectedTab() };
      await page.locator(`[data-apt-fixture-label="${other.fixture_id}"]`).hover();
      await until('hover ring', id => document.querySelector(`[data-apt-fixture-ring="${id}"]`)?.getAttribute('data-state') === 'hover', other.fixture_id);
      assert.deepEqual({ title: await title(), tab: await selectedTab() }, before, 'hover never changes the inspector');
      await blur(); await page.keyboard.press('Escape');
      await inspector().and(page.locator('[aria-label="Room lighting inspector"]')).waitFor();
      const body = await canvasPoint('fixture', target.fixture_id); assert.ok(body, 'an unobscured cylinder point');
      await page.mouse.click(body.x, body.y); await fixtureInspector().waitFor();
      assert.equal(await title(), target.label); assert.equal(await selectedTab(), 'Light');
      await blur(); await page.keyboard.press('Escape');
      await inspector().and(page.locator('[aria-label="Room lighting inspector"]')).waitFor();
      await page.getByRole('button', { name: 'Select ' + target.label, exact: true }).click(); await fixtureInspector().waitFor();
      assert.equal(await title(), target.label); assert.equal(await selectedTab(), 'Light');
      await blur(); await page.keyboard.press('n'); await until('N', label => document.querySelector('[aria-label="Fixture lighting inspector"] .apt-ui-title')?.textContent === label, fixtures[2].label);
      await page.keyboard.press('p'); await until('P', label => document.querySelector('[aria-label="Fixture lighting inspector"] .apt-ui-title')?.textContent === label, target.label);
      await blur(); await page.keyboard.press('Escape');
      await inspector().and(page.locator('[aria-label="Room lighting inspector"]')).waitFor();
      // Focus restoration runs on the next frame.
      await until('focus returns after Escape', () => !!document.activeElement && document.activeElement !== document.body);
      await page.keyboard.press('Escape'); await settle();
      assert.equal(await page.locator('.apt-home-shell[data-home-layout="unified"]').count(), 1, 'idle Escape closes nothing');
      return { fixture_id: target.fixture_id, paths: ['label', 'cylinder', 'list'], hover_ring: other.fixture_id };
    });

    const id = fixtures[0].fixture_id;
    await page.locator(`[data-apt-fixture-label="${id}"]`).click(); await fixtureInspector().waitFor();
    await blur(); await page.keyboard.press('a');
    await until('Spot tool and Aim tab', () => document.querySelector('[data-apt-directional-mode="spot"]')?.getAttribute('aria-pressed') === 'true');
    await until('reticle', () => !document.querySelector('[data-apt-aim-reticle]')?.hidden);
    const original = await descriptor(id);

    await check('a body drag selects its light but neither moves the aim nor orbits', async () => {
      const other = fixtures[3].fixture_id, otherBefore = await descriptor(other);
      const start = await canvasPoint('fixture', other), end = await canvasPoint('surface', null, start); assert.ok(start && end);
      const poseBefore = await pose();
      await page.mouse.move(start.x, start.y); await page.mouse.down(); await page.mouse.move(end.x, end.y, { steps: 10 }); await page.mouse.up();
      await until('body press selects', label => document.querySelector('[aria-label="Fixture lighting inspector"] .apt-ui-title')?.textContent === label, fixtures[3].label);
      await settle();
      assert.deepEqual(await descriptor(other), otherBefore, 'the dragged light keeps its exact aim');
      assert.deepEqual(await descriptor(id), original, 'the other light keeps its exact aim');
      assert.deepEqual(await pose(), poseBefore, 'the body drag did not orbit');
      assert.equal((await overlayState()).bar, null, 'a body drag proposes nothing');
      await page.locator(`[data-apt-fixture-label="${id}"]`).click(); await until('back', label => document.querySelector('[aria-label="Fixture lighting inspector"] .apt-ui-title')?.textContent === label, fixtures[0].label);
      return { fixture_id: other, pose: poseBefore };
    });

    await check('Drag aim (no snap): chip, cyan proposal while the current aim stays, Set aim, exact Undo and Redo', async () => {
      const reticleBefore = (await overlayState()).reticle;
      assert.equal(reticleBefore.width >= 44 && reticleBefore.height >= 44, true, 'the reticle hit area is 44 px');
      assert.match(reticleBefore.label, /arrow keys/);
      const end = await canvasPoint('surface', null, reticleBefore); assert.ok(end);
      const poseBefore = await pose();
      const drag = await reticleDrag(end);
      assert.equal(drag.during.chip.state, 'valid');
      assert.match(drag.during.chip.text, /\d+\.\d\d m · \d+° tilt/, 'the chip names distance and tilt');
      assert.deepEqual(drag.during.chip.disclosures, [['optical-origin', OPTICAL], ['travel-limits', TRAVEL]]);
      assert.equal(await selectedTab(), 'Aim', 'the Aim tab opens at drag start');
      assert.deepEqual(await pose(), poseBefore, 'a reticle drag never orbits');
      assert.ok(drag.released?.hit, 'the release has a visible canonical surface hit');
      await until('bar', () => !document.querySelector('[data-apt-aim-bar]')?.hidden);
      const state = await overlayState();
      assert.equal(state.bar.state, 'valid'); assert.match(state.bar.text, /Set aim/); assert.match(state.bar.text, /Discard/);
      assert.equal(state.reticle.state, 'proposed'); assert.equal(state.proposal_marker, true);
      assert.deepEqual(await descriptor(id), original, 'the current aim does not move before Set aim');
      assert.equal(await page.evaluate(() => document.querySelector('[data-apt-aim-row="proposed"]')?.getAttribute('data-state')), 'valid');
      assert.equal(await page.locator('[data-apt-aim-tab-disclosure]').textContent(), OPTICAL + ' · ' + TRAVEL);
      assert.equal(await page.evaluate(() => document.activeElement?.hasAttribute('data-apt-aim-reticle')), true);
      await page.keyboard.press('Enter');
      await until('set', id => !!window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).target, id);
      const aimed = await descriptor(id);
      assert.ok(near(aimed.target.point, drag.released.hit.point), 'Set aim lands within 3 cm of the release hit');
      assert.deepEqual(aimed.display_anchor_m, original.display_anchor_m, 'the socket stays fixed');
      assert.equal(aimed.physical_commands, false);
      assert.match(await page.locator('body').innerText(), /no hardware command sent/);
      assert.equal(await fixtureInspector().locator('.apt-ui-badge[data-state="modeled"]').textContent(), 'Modeled');
      assert.equal((await overlayState()).bar, null, 'Set retires the proposal');
      await blur(); await page.keyboard.press('Control+z');
      await until('undo', ({ id, pan }) => window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).state.pan_deg === pan, { id, pan: original.state.pan_deg });
      assert.deepEqual(await descriptor(id), original, 'Ctrl+Z restores the exact aim');
      await until('proposal back', () => !document.querySelector('[data-apt-aim-bar]')?.hidden);
      await page.keyboard.press('Control+Shift+z');
      await until('redo', id => !!window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).target, id);
      assert.deepEqual(await descriptor(id), aimed, 'Shift+Ctrl+Z re-applies the same aim');
      await page.keyboard.press('Control+z');
      await until('undo again', id => !window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).target, id);
      assert.deepEqual(await descriptor(id), original);
      return { fixture_id: id, release: drag.released, aimed_target: aimed.target, chip: drag.during.chip.text };
    });

    await check('Escape order: drag, then proposal (Ctrl+Z restores it), then selection, then nothing', async () => {
      // Undo left the earlier proposal pending; Escape discards it first.
      let state = await overlayState();
      assert.ok(state.bar, 'a proposal is pending');
      const pendingText = state.bar.text;
      const end = await canvasPoint('surface', null, state.reticle);
      await reticleDrag(end, { release: false });
      await page.keyboard.press('Escape');
      await until('drag canceled', () => document.querySelector('[data-apt-aim-chip]')?.hidden === true);
      await page.mouse.up(); await settle();
      state = await overlayState();
      assert.equal(state.bar?.text, pendingText, 'Escape during a drag cancels the drag only');
      assert.deepEqual(await descriptor(id), original);
      await page.keyboard.press('Escape');
      await until('discarded', () => document.querySelector('[data-apt-aim-bar]')?.hidden === true);
      assert.equal(await title(), fixtures[0].label, 'the selection survives the discard');
      await page.keyboard.press('Control+z');
      await until('restored', () => !document.querySelector('[data-apt-aim-bar]')?.hidden);
      assert.equal((await overlayState()).bar.text, pendingText, 'Ctrl+Z restores the discarded proposal');
      await page.keyboard.press('Escape'); await until('discarded again', () => document.querySelector('[data-apt-aim-bar]')?.hidden === true);
      await page.keyboard.press('Escape');
      await inspector().and(page.locator('[aria-label="Room lighting inspector"]')).waitFor();
      await page.keyboard.press('Escape'); await settle();
      assert.equal(await page.locator('.apt-home-shell[data-home-layout="unified"]').count(), 1, 'the last Escape does nothing');
      assert.deepEqual(await descriptor(id), original);
      return { fixture_id: id };
    });

    await page.locator(`[data-apt-fixture-label="${id}"]`).click(); await fixtureInspector().waitFor();
    await blur(); await page.keyboard.press('a');
    await until('reticle again', () => !document.querySelector('[data-apt-aim-reticle]')?.hidden);

    await check('A08: arrow keys nudge the focused reticle 10 px, or 40 px with Shift, as proposals', async () => {
      await page.locator('[data-apt-aim-reticle]').focus();
      await page.getByRole('tablist', { name: 'Light sections', exact: true }).getByRole('tab', { name: 'Light', exact: true }).click();
      await blur(); await page.keyboard.press('a');
      await page.locator('[data-apt-aim-reticle]').focus();
      const start = (await overlayState()).reticle;
      await page.keyboard.press('ArrowRight');
      await until('nudged', () => document.querySelector('[data-apt-aim-reticle]')?.getAttribute('data-state') !== 'current');
      await settle();
      const one = (await overlayState()).reticle;
      await page.keyboard.press('Shift+ArrowRight'); await settle(); await settle();
      const two = (await overlayState()).reticle;
      assert.ok(Math.abs(one.x - start.x - 10) <= 2.5 && Math.abs(one.y - start.y) <= 2.5, 'ArrowRight moves the proposal 10 px');
      assert.ok(Math.abs(two.x - one.x - 40) <= 4 && Math.abs(two.y - one.y) <= 4, 'Shift+ArrowRight moves it 40 px');
      assert.equal(await selectedTab(), 'Aim', 'a key nudge brings Aim forward');
      assert.deepEqual(await descriptor(id), original, 'nudges only propose');
      await page.keyboard.press('Escape'); await until('discard nudge', () => document.querySelector('[data-apt-aim-bar]')?.hidden === true);
      return { start, one, two };
    });

    await check('A08: Tap to aim proposes from one tap; Escape leaves the mode', async () => {
      const toggle = fixtureInspector().getByRole('button', { name: 'Tap to aim', exact: true });
      await toggle.click();
      await page.locator('[data-apt-tap-aim-hint]').waitFor();
      await blur(); await page.keyboard.press('Escape');
      await page.locator('[data-apt-tap-aim-hint]').waitFor({ state: 'detached' });
      assert.equal(await title(), fixtures[0].label, 'leaving tap mode keeps the selection');
      await toggle.click(); await page.locator('[data-apt-tap-aim-hint]').waitFor();
      const point = await canvasPoint('surface', null, (await overlayState()).reticle), hit = await surfaceHit(point);
      const poseBefore = await pose();
      await page.mouse.click(point.x, point.y);
      await until('tap proposal', () => !document.querySelector('[data-apt-aim-bar]')?.hidden);
      await page.locator('[data-apt-tap-aim-hint]').waitFor({ state: 'detached' });
      assert.equal(await title(), fixtures[0].label, 'the tap neither deselects nor selects');
      assert.deepEqual(await pose(), poseBefore, 'the tap does not orbit');
      assert.deepEqual(await descriptor(id), original);
      const proposed = await page.evaluate(() => document.querySelector('[data-apt-aim-row="proposed"]')?.getAttribute('data-state'));
      assert.equal(proposed, 'valid');
      await fixtureInspector().locator('[data-apt-aim-tab-action="set"]').click();
      await until('tap set', id => !!window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).target, id);
      assert.ok(near((await descriptor(id)).target.point, hit.point), 'the tapped spot is set within 3 cm');
      await fixtureInspector().getByRole('button', { name: 'Undo aim', exact: true }).click();
      await until('tap undo', id => !window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).target, id);
      assert.deepEqual(await descriptor(id), original);
      await fixtureInspector().locator('[data-apt-aim-tab-action="discard"]').click();
      await until('tap discard', () => document.querySelector('[data-apt-aim-bar]')?.hidden === true);
      return { point, hit };
    });

    await check('A03: the Spot pending dot shows only while a proposal waits and Spot is not the tool', async () => {
      const end = await canvasPoint('surface', null, (await overlayState()).reticle);
      await reticleDrag(end); await until('bar', () => !document.querySelector('[data-apt-aim-bar]')?.hidden);
      const spot = page.locator('[data-apt-directional-mode="spot"]');
      assert.equal(await spot.locator('[data-apt-aim-pending]').count(), 0, 'no dot while Spot is the tool');
      await page.locator('[data-apt-directional-mode="zones"]').click();
      await spot.locator('[data-apt-aim-pending]').waitFor();
      assert.equal(await page.getByRole('button', { name: 'Spot (aim pending)', exact: true }).count(), 1, 'the Spot name gains "(aim pending)"');
      await spot.click(); await settle();
      assert.equal(await spot.locator('[data-apt-aim-pending]').count(), 0);
      await page.locator('[data-apt-aim-bar] [data-apt-aim-action="discard"]').click();
      await until('discarded', () => document.querySelector('[data-apt-aim-bar]')?.hidden === true);
      return { fixture_id: id };
    });

    await check('A02, A04: with Chat open a drag keeps Chat, and the room bar sets the aim', async () => {
      await page.getByRole('tab', { name: 'Chat', exact: true }).click();
      const end = await canvasPoint('surface', null, (await overlayState()).reticle);
      await reticleDrag(end);
      await until('bar', () => !document.querySelector('[data-apt-aim-bar]')?.hidden);
      assert.equal(await page.getByRole('tab', { name: 'Chat', exact: true }).getAttribute('aria-selected'), 'true', 'Chat stays open');
      await page.locator('[data-apt-aim-bar] [data-apt-aim-action="set"]').click();
      await until('bar set', id => !!window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).target, id);
      assert.equal(await page.getByRole('tab', { name: 'Chat', exact: true }).getAttribute('aria-selected'), 'true');
      await blur(); await page.keyboard.press('Control+z');
      await until('chat undo', id => !window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).target, id);
      assert.deepEqual(await descriptor(id), original);
      await page.keyboard.press('Escape'); await until('discard', () => document.querySelector('[data-apt-aim-bar]')?.hidden === true);
      return { chat_kept: true };
    });

    await check('A02: on a phone, aiming raises the sheet from peek to half and keeps Chat', async () => {
      await page.setViewportSize({ width: 390, height: 844 });
      const grip = page.getByRole('button', { name: 'Resize Home sheet', exact: true });
      for (let tries = 0; tries < 3 && await page.locator('.apt-home-shell').getAttribute('data-sheet') !== 'peek'; tries++) await grip.click();
      assert.equal(await page.locator('.apt-home-shell').getAttribute('data-sheet'), 'peek');
      await until('phone reticle', () => !document.querySelector('[data-apt-aim-reticle]')?.hidden);
      await page.locator('[data-apt-aim-reticle]').focus(); await page.keyboard.press('ArrowLeft');
      await until('half', () => document.querySelector('.apt-home-shell')?.getAttribute('data-sheet') === 'half');
      assert.equal(await page.getByRole('tab', { name: 'Chat', exact: true }).getAttribute('aria-selected'), 'true');
      await until('phone bar', () => !document.querySelector('[data-apt-aim-bar]')?.hidden);
      await page.keyboard.press('Escape'); await until('phone discard', () => document.querySelector('[data-apt-aim-bar]')?.hidden === true);
      await page.setViewportSize({ width: 1366, height: 768 });
      await page.getByRole('tab', { name: 'Controls', exact: true }).click();
      return { sheet: 'half' };
    });

    await check('a Spot or Down brightness slider powers its channel on', async () => {
      await page.getByRole('tablist', { name: 'Light sections', exact: true }).getByRole('tab', { name: 'Light', exact: true }).click();
      const power = fixtureInspector().getByRole('checkbox', { name: 'Spot power', exact: true });
      if (await power.isChecked()) await power.uncheck();
      await until('spot off', id => window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).state.spotlight.on === false, id);
      const slider = fixtureInspector().getByRole('slider', { name: 'Brightness', exact: true });
      await slider.focus(); await page.keyboard.press('Home'); await page.keyboard.press('ArrowRight');
      await until('spot on', id => window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).state.spotlight.on === true, id);
      const down = fixtureInspector().getByRole('checkbox', { name: 'Down power', exact: true });
      if (await down.isChecked()) await down.uncheck();
      const downSlider = fixtureInspector().getByRole('slider', { name: 'Down brightness', exact: true });
      await downSlider.focus(); await page.keyboard.press('ArrowRight');
      await until('down on', id => window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id).state.downward.on === true, id);
      return { spot: true, down: true };
    });

    for (const [file, expected] of Object.entries(protectedHashes)) assert.equal(hash(fs.readFileSync(file)), expected, 'authored pack and registration bytes stay exact');
    assert.deepEqual(receipt.forbidden, []); assert.deepEqual(receipt.websocket_sends, []);
    assert.deepEqual(receipt.missing_code, []); assert.deepEqual(receipt.errors, []);
    assert.equal(receipt.requests.some(request => !['GET', 'HEAD'].includes(request.method)), false, 'zero non-GET requests');
    receipt.zero_transport = true;
  }
  try { await Promise.race([proof(), deadline]); receipt.result = 'passed'; }
  catch (error) {
    receipt.result = 'failed'; receipt.failure = error.stack || String(error); save();
    if (page && !page.isClosed()) receipt.failure_state = await page.evaluate(() => ({
      overlay: window.HomeOverlay?.topKey?.(), activeElement: document.activeElement?.outerHTML?.slice(0, 400),
      controls: document.querySelector('.apt-home-controls')?.innerText?.slice(0, 1200),
      bar: document.querySelector('[data-apt-aim-bar]')?.outerHTML?.slice(0, 600),
      reticle: document.querySelector('[data-apt-aim-reticle]')?.outerHTML?.slice(0, 400),
      debug: window.__aptAimDebug,
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
