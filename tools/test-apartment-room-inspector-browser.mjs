// APT-R02: real Home shell, renderer, native room controls and authored data.
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

test('real authored room inspector preserves named aims and modeled channel bindings without output', { timeout: 180000 }, async () => {
  assert.ok(!process.env.HOME_APARTMENT_CANDIDATE_ROOT, 'proof serves only this worktree');
  const run = path.join(ROOT, '.tmp/apartment-room-inspector-browser', 'run-' + Date.now());
  fs.mkdirSync(run, { recursive: true });
  const packs = path.join(run, 'packs'), privateRoot = path.join(run, 'private');
  const echo = installPack(packs, 'echo'), victoria = installPack(packs, 'victoria');
  // Customize authored Echo before sealing its final manifest and data hashes.
  // The point matches the existing editor's custom-point representation;
  // Victoria's four positioned fixtures and registration remain untouched.
  echo.seed.zones = [{ id: 'authored-study', name: 'Authored study', color: '#a8ffd8',
    floor_polygon: [[0, 0], [4, 0], [4, 4], [0, 4]], ceiling_height_m: 2.5 }];
  echo.seed.devices[0].room_id = 'authored-study';
  echo.seed.targets = [{ id: 'authored-reading-point', name: 'Authored reading point', category: 'custom', shape: 'point',
    pos: [1.4, 1.7, .7], normal: [0, 0, 1], up: [0, 1, 0], room_id: 'authored-study', source: 'manual',
    confidence: 1, updated_at: '2026-10-18T00:00:00.000Z' }];
  echo.files['seed-model.json'] = Buffer.from(JSON.stringify(echo.seed));
  echo.manifest.assets['seed-model.json'] = { path: 'seed-model.json', bytes: echo.files['seed-model.json'].length,
    sha256: hash(echo.files['seed-model.json']) };
  echo.files['content-pack.json'] = Buffer.from(JSON.stringify(echo.manifest));
  for (const name of ['seed-model.json', 'content-pack.json']) fs.writeFileSync(path.join(packs, echo.site, echo.revision, name), echo.files[name]);
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1,
    packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  const protectedFiles = [echo, victoria].flatMap(item => Object.keys(item.files).map(name => path.join(packs, item.site, item.revision, name)));
  protectedFiles.push(path.join(packs, 'echo/ACTIVE'), path.join(packs, 'victoria/ACTIVE'), path.join(packs, 'index.json'), path.join(privateRoot, 'registration.json'));
  const protectedHashes = Object.fromEntries(protectedFiles.map(file => [file, hash(fs.readFileSync(file))]));
  const sources = publicSources(SRC), receipt = { schema: 'home-apartment-room-inspector-authored-browser-1',
    authored_room: true, real_renderer: true, complete_home_shell: true, household_data: false, screenshots: false,
    source_mode: 'current-worktree-only', source_sha256: Object.fromEntries(['home-apartment.jsx', 'home-apartment-cards.jsx',
      'home-apartment-edit.jsx', 'home-3d/engine.js', 'home-3d/picking.js', 'home-3d/direct-aim-gesture.mjs']
      .map(name => [name, hash(fs.readFileSync(path.join(SRC, name)))])),
    physical_commands: 0, saves: 0, camera_sessions: 0, model_sessions: 0,
    checks: [], requests: [], forbidden: [], blocked_read_subscriptions: [], missing_code: [], errors: [], served: {}, browser: browserOptions() };
  let server, handler, origin, browser, context, page, timer;
  const save = () => fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => {
    browser?.close().catch(() => {}); server?.closeAllConnections(); reject(Error('145s room inspector browser deadline'));
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
    const descriptors = () => page.evaluate(() => window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures
      .map(fixture => window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(fixture.fixture_id)));
    const withoutPower = descriptor => {
      const copy = structuredClone(descriptor);
      for (const channel of [copy.state.spotlight, copy.state.downward, ...copy.state.radials]) delete channel.on;
      return copy;
    };
    const group = name => page.getByRole('region', { name: name + ' lights', exact: true });
    const editorToolbar = () => page.locator('[data-apt-edit-ui="toolbar"]');
    const editorInspector = () => page.locator('[data-apt-edit-ui="inspector"]');
    const expectCleanEditor = async () => {
      assert.equal(await editorToolbar().getByRole('button', { name: 'undo', exact: true }).isDisabled(), true);
      assert.equal(await editorToolbar().getByRole('button', { name: 'redo', exact: true }).isDisabled(), true);
      const saveButton = editorToolbar().locator('button[title*="Home Assistant"]');
      assert.equal(await saveButton.count(), 1, 'existing editor save control is mounted');
      assert.equal(await saveButton.isDisabled(), true, 'opening an editor must not create a dirty draft');
      assert.equal(await page.getByRole('button', { name: 'Finish placement', exact: true }).count(), 0);
      assert.equal(await page.locator('[data-apt-edit-ui="targets-and-palette"]')
        .getByRole('button', { name: 'Cancel', exact: true }).count(), 0);
      assert.equal(await page.getByRole('button', { name: /click destination/ }).count(), 0);
    };
    await waitSite('echo');
    await page.getByRole('button', { name: 'Authored Echo light Unavailable', exact: true }).waitFor();
    await page.locator('[data-apt-semantic-device-select="1"] option[value="authored-echo-light"]').waitFor({ state: 'attached' });
    await page.waitForFunction(() => window.HomeOverlay?.topKey?.() === 'apartment-embedded'
      && window.__HOME_APARTMENT_SITE_API?.readback().loadedContentRevision === 'authored-echo-r1'
      && document.querySelector('[data-apt-semantic-device-select="1"]')?.disabled === false);
    const echoScope = await page.evaluate(() => window.__APT_ENGINE.sceneIdentity);
    const originalDraft = await page.evaluate(() => localStorage.getItem('apartment3d.modelDraft'));
    await check('authoritative Echo room shows Unknown and disables offline room power while its row selects the real device', async () => {
      const room = group('Authored study'), power = room.getByRole('checkbox', { name: 'Authored study room power', exact: true });
      await room.getByRole('heading', { name: 'Authored study', exact: true }).waitFor();
      assert.equal(echo.seed.devices[0].pos, null);
      assert.equal(await power.isDisabled(), true); assert.equal(await power.isChecked(), false);
      assert.equal(await power.evaluate(node => node.indeterminate), true);
      assert.match(await room.innerText(), /Unknown/); assert.match(await room.innerText(), /Home Assistant controls unavailable/);
      await room.getByRole('button', { name: 'Authored Echo light Unavailable', exact: true }).click();
      await page.getByRole('button', { name: 'close card', exact: true }).waitFor();
      assert.match(await page.locator('.apt-home-controls').innerText(), /Authored Echo light/);
      await page.getByRole('button', { name: 'close card', exact: true }).click(); await room.waitFor();
      return { device_id: echo.seed.devices[0].id, saved_room_id: echo.seed.devices[0].room_id, unpositioned: true, room_power: 'unknown', physical_control_disabled: true };
    });
    await check('named aim Edit opens the existing target selected without placement, dirty state or a save', async () => {
      await page.getByRole('region', { name: 'Named aims', exact: true })
        .getByRole('button', { name: 'Edit Authored reading point', exact: true }).click();
      await editorInspector().getByRole('spinbutton', { name: 'target x', exact: true }).waitFor();
      assert.equal(await editorInspector().locator('input:not([type])').inputValue(), echo.seed.targets[0].name);
      for (const [index, axis] of ['x', 'y', 'z'].entries()) assert.equal(
        Number(await editorInspector().getByRole('spinbutton', { name: 'target ' + axis, exact: true }).inputValue()), echo.seed.targets[0].pos[index]);
      assert.match(await page.locator('[data-apt-edit-ui="targets-and-palette"]').innerText(), /ADD TARGET · STEP 1 OF 2/);
      assert.equal(await page.locator('[data-apt-edit-ui="targets-and-palette"]')
        .getByRole('button', { name: '· ' + echo.seed.targets[0].name, exact: true }).count(), 1);
      await expectCleanEditor();
      assert.equal(await page.evaluate(() => localStorage.getItem('apartment3d.modelDraft')), originalDraft);
      await editorToolbar().getByRole('button', { name: 'done', exact: true }).click(); await group('Authored study').waitFor();
      return { target_id: echo.seed.targets[0].id, selected_coordinates_exact: true, placing: false, save_disabled: true, empty_undo_redo: true };
    });
    await check('generic Setup after named Edit uses the normal default tool without carrying the target entry', async () => {
      await page.locator('.apt-home-setup').click(); await editorToolbar().waitFor();
      assert.equal(await editorInspector().getByRole('spinbutton', { name: /^target [xyz]$/ }).count(), 0);
      assert.match(await page.locator('[data-apt-edit-ui="targets-and-palette"]').innerText(), /Select a marker or target directly in 3D/);
      await expectCleanEditor();
      await editorToolbar().getByRole('button', { name: 'done', exact: true }).click(); await group('Authored study').waitFor();
      assert.equal(await page.evaluate(() => localStorage.getItem('apartment3d.modelDraft')), originalDraft);
      return { selected_target: null, default_tool_visible: true, save_disabled: true };
    });
    await selectHome('victoria');
    await page.waitForFunction(() => window.__APT_ENGINE?.overlay.virtualFixturesReadback().fixture_count === 4);
    const inventory = await page.evaluate(() => window.__APT_ENGINE.overlay.virtualFixturesReadback());
    assert.equal(inventory.visible, true); assert.equal(inventory.status, 'ready');
    const fixtures = inventory.fixtures;
    const originalDescriptors = await descriptors();
    await check('Victoria accurately groups the four registered authored fixtures as Unassigned with display on', async () => {
      const room = group('Unassigned'); await room.getByRole('heading', { name: 'Unassigned', exact: true }).waitFor();
      assert.equal(await room.getByRole('checkbox', { name: 'Unassigned room power', exact: true }).isChecked(), true);
      assert.equal(await room.getByRole('checkbox').isDisabled(), false);
      assert.equal(await room.getByRole('button').count(), 4);
      for (const fixture of fixtures) assert.equal(await room.getByRole('button', { name: 'Select ' + fixture.label, exact: true }).count(), 1);
      assert.deepEqual(await selected(), []);
      return { fixture_ids: fixtures.map(fixture => fixture.fixture_id), membership: 'unassigned', fixture_display_on: inventory.visible };
    });
    const verifyPower = async on => {
      await page.waitForFunction(on => window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures.every(fixture =>
        fixture.state.spotlight.on === on && fixture.state.downward.on === on && fixture.state.radials.length === 6
          && fixture.state.radials.every(channel => channel.on === on)), on);
      const actual = await descriptors(); assert.equal(actual.length, 4);
      assert.deepEqual(actual.map(withoutPower), originalDescriptors.map(withoutPower),
        'only the eight channel on values may change; brightness, temperature, aim, target and canonical bindings stay exact');
      assert.equal(await page.evaluate(() => window.__APT_ENGINE.overlay.virtualFixturesReadback().visible), true);
      return actual;
    };
    await check('native Victoria room power Off changes all eight channels per fixture without moving or dimming them', async () => {
      await group('Unassigned').getByRole('checkbox', { name: 'Unassigned room power', exact: true }).uncheck();
      await verifyPower(false); assert.match(await group('Unassigned').innerText(), /Off/);
      return { fixtures: 4, channels_each: 8, on: false, non_power_descriptor_exact: true, fixture_display_on: true };
    });
    await check('native Victoria room power On restores the exact modeled fixture descriptors', async () => {
      await group('Unassigned').getByRole('checkbox', { name: 'Unassigned room power', exact: true }).check();
      assert.deepEqual(await verifyPower(true), originalDescriptors);
      assert.deepEqual(await page.evaluate(() => window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures), inventory.fixtures);
      return { fixtures: 4, channels_each: 8, on: true, complete_descriptor_restored_exact: true, fixture_display_on: true };
    });
    await check('room list selection delegates to the same fixture selected by N and P', async () => {
      await group('Unassigned').getByRole('button', { name: 'Select ' + fixtures[1].label, exact: true }).click(); await expectSelected(fixtures[1]);
      await blur(); await page.keyboard.press('n'); await expectSelected(fixtures[2]);
      await page.keyboard.press('p'); await expectSelected(fixtures[1]);
      return { ids: [fixtures[1].fixture_id, fixtures[2].fixture_id, fixtures[1].fixture_id], renderer_selection_matches: true };
    });
    await check('a native home change retires Victoria controls and keyboard selection while preserving Echo named aims and clean state', async () => {
      await selectHome('echo'); await group('Authored study').waitFor();
      assert.equal(await fixtureInspector().count(), 0); assert.equal(await group('Unassigned').count(), 0);
      await page.locator('[data-apt-semantic-device-select="1"] option[value="authored-echo-light"]').waitFor({ state: 'attached' });
      await page.waitForFunction(() => document.querySelector('[data-apt-semantic-device-select="1"]')?.disabled === false);
      await blur(); await page.keyboard.press('n'); await page.getByRole('button', { name: 'close card', exact: true }).waitFor();
      assert.match(await page.locator('.apt-home-controls').innerText(), /Authored Echo light/);
      assert.equal(await fixtureInspector().count(), 0);
      await page.getByRole('button', { name: 'close card', exact: true }).click();
      const parked = await page.evaluate(scope => window.HomeApartmentData.restoreWorkspace(scope), echoScope);
      assert.ok(parked?.model, 'real Home transition parked the admitted Echo model');
      assert.equal(parked.dirty, false); assert.deepEqual(parked.model.targets, echo.seed.targets);
      assert.equal(parked.model.devices.find(device => device.id === echo.seed.devices[0].id).room_id, echo.seed.devices[0].room_id);
      assert.equal(await page.evaluate(() => localStorage.getItem('apartment3d.modelDraft')), originalDraft);
      // Selection is retained per home. Re-admission must synchronize the
      // retained inspector with the new renderer before Back returns to room.
      await selectHome('victoria'); await expectSelected(fixtures[1]);
      await fixtureInspector().getByRole('button', { name: 'Back to room', exact: true }).click();
      await group('Unassigned').waitFor(); assert.deepEqual(await selected(), []);
      // Generation is an engine-lifetime identity and may retire on a home
      // change. Persistent modeled state, targets and canonical bindings may not.
      const persistent = ({ generation, ...descriptor }) => descriptor;
      assert.deepEqual((await descriptors()).map(persistent), originalDescriptors.map(persistent));
      return { retired_home: 'victoria', new_keyboard_device: echo.seed.devices[0].id, echo_dirty: false,
        saved_target_exact: true, retained_selection: fixtures[1].fixture_id, back_to_room_selection: null, fixture_descriptors_exact: true };
    });
    for (const [file, expected] of Object.entries(protectedHashes)) assert.equal(hash(fs.readFileSync(file)), expected, 'authored pack and registration bytes stay exact');
    for (const [file, expected] of Object.entries(receipt.source_sha256)) assert.equal(receipt.served[file]?.sha256, expected, 'served production source matches the captured candidate');
    assert.deepEqual(receipt.forbidden, []); assert.deepEqual(receipt.missing_code, []); assert.deepEqual(receipt.errors, []);
    assert.equal(receipt.requests.some(request => !['GET', 'HEAD'].includes(request.method)), false);
    receipt.authored_data_hashes_unchanged = true; receipt.fixture_display_on = true;
  }
  try { await Promise.race([proof(), deadline]); receipt.result = 'passed'; }
  catch (error) {
    receipt.result = 'failed'; receipt.failure = error.stack || String(error); save();
    if (page && !page.isClosed()) receipt.failure_state = await page.evaluate(() => ({
      selected: (() => { const ids = []; window.__APT_ENGINE?.overlay.virtualFixturesGroup?.traverse(node => {
        if (node.name === 'virtual-fixture-aim-handle' && node.visible) ids.push(node.userData.fixture_id);
      }); return ids; })(), inspector: document.querySelector('[data-victoria-virtual-fixtures="1"]')?.getAttribute('aria-label'),
      editor: [...document.querySelectorAll('[data-apt-edit-ui]')].map(node => ({ part: node.dataset.aptEditUi, text: node.innerText.slice(0, 800) })),
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
