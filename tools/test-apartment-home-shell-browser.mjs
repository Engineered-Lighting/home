// Authored-room browser acceptance for the actual Home Apartment shell.
// Uses the production renderer and private local test registration, never a
// household pack, live camera, model session, physical command or deployment.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createHash } from 'node:crypto';
import { deflateSync } from 'node:zlib';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import Core from '../app/src/home-equipment-registration.js';
import { createEquipmentRegistrationGateway, initRegistration, readRegistration, readActiveBinding } from '../web-gateway/equipment-registration.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SRC = path.join(ROOT, 'app/src');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');

// Reuse only the existing authored asset/registration fixture constructors.
// Importing that test module would also run its independent browser workload.
const fixtureSource = fs.readFileSync(path.join(ROOT, 'tools/test-victoria-virtual-fixtures-browser.mjs'), 'utf8');
const fixtureStart = fixtureSource.indexOf('function authoredPositions()');
const fixtureEnd = fixtureSource.indexOf('\ntest(', fixtureStart);
assert.ok(fixtureStart >= 0 && fixtureEnd > fixtureStart, 'shared authored fixture constructors must be present');
const { installPack, authoredDocument } = new Function('fs', 'path', 'hash', 'Core', 'deflateSync', 'Buffer',
  fixtureSource.slice(fixtureStart, fixtureEnd) + '\nreturn { installPack, authoredDocument };')
  (fs, path, hash, Core, deflateSync, Buffer);

function candidateSources() {
  const raw = process.env.HOME_APARTMENT_CANDIDATE_ROOT;
  const candidate = raw && path.resolve(raw);
  const overlay = candidate && (fs.existsSync(path.join(candidate, 'app/src/home-app.jsx')) ? path.join(candidate, 'app/src') : candidate);
  const sources = require('./serve-home-map-rehearsal.cjs').sourceFiles();
  return new Map([...sources].flatMap(([url, baseline]) => {
    const relative = path.relative(SRC, baseline);
    const changed = overlay && path.join(overlay, relative);
    const selected = changed && fs.existsSync(changed) ? changed : baseline;
    return fs.existsSync(selected) && fs.lstatSync(selected).isFile() && !fs.lstatSync(selected).isSymbolicLink()
      ? [[url, { selected, relative: relative.split(path.sep).join('/'), candidate: selected === changed }]] : [];
  }));
}

test('unified shell keeps the real room, private controls and one chat controller through responsive navigation', { timeout: 150000 }, async () => {
  const run = path.join(ROOT, '.tmp/apartment-home-shell-browser', 'run-' + Date.now());
  fs.mkdirSync(run, { recursive: true });
  const packs = path.join(run, 'packs'), privateRoot = path.join(run, 'private');
  const echo = installPack(packs, 'echo'), victoria = installPack(packs, 'victoria');
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1,
    packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  const initial = initRegistration(privateRoot, authoredDocument(readActiveBinding(packs)), { contentPacksDir: packs });
  const protectedFiles = [echo, victoria].flatMap(item => Object.keys(item.files).map(name => path.join(packs, item.site, item.revision, name)));
  protectedFiles.push(path.join(packs, 'echo/ACTIVE'), path.join(packs, 'victoria/ACTIVE'), path.join(packs, 'index.json'));
  const protectedHashes = Object.fromEntries(protectedFiles.map(file => [file, hash(fs.readFileSync(file))]));
  const sources = candidateSources(), requests = [], forbidden = [], errors = [], checks = [], screenshots = [], served = {};
  const receipt = { schema: 'home-apartment-shell-authored-browser-1', authored_room: true, real_renderer: true,
    real_household_data: false, live_camera: false, model_session_started: false, physical_commands: 0, survey_writes: 0,
    checks, screenshots, requests, forbidden, errors, served };
  let server, browser, page, origin, handler, timer;
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => {
    browser?.close().catch(() => {}); server?.closeAllConnections(); reject(new Error('145s authored shell browser deadline'));
  }, 145000); });

  async function exercise() {
    server = http.createServer(async (req, res) => {
      try {
        const url = new URL(req.url, 'http://localhost');
        requests.push({ path: url.pathname, method: req.method });
        if (!['GET', 'HEAD'].includes(req.method) || /\/api\/services\/|\/capture(?:\/|$)|\/acquisition(?:\/|$)|\/control(?:\/|$)|\/sessions\/[^/]+\/(?:start|stop)(?:\/|$)/.test(url.pathname)) {
          forbidden.push({ path: url.pathname, method: req.method }); res.writeHead(403); res.end(); return;
        }
        if (url.pathname.startsWith('/api/apartment/v1/')) return handler(req, res);
        let body, type = 'application/octet-stream';
        if (url.pathname.startsWith('/api/perception-lab/v1/')) {
          const route = url.pathname.slice('/api/perception-lab/v1/'.length);
          const value = route === 'capabilities' ? { site_id: 'victoria', cameras: [], live_previews: {}, live_videos: {},
            recording_allowed: false, experiments: [], apartment_viewing: { provisioned: false } }
            : route === 'sessions' ? { site_id: 'victoria', version: 1, items: [] }
              : route === 'recordings' ? { items: [] } : null;
          if (value) { body = Buffer.from(JSON.stringify(value)); type = 'application/json'; }
        }
        if (url.pathname === '/content-packs/index.json') { body = fs.readFileSync(path.join(packs, 'index.json')); type = 'application/json'; }
        else {
          const pack = [echo, victoria].find(item => url.pathname.startsWith(item.base));
          if (pack) { body = pack.files[url.pathname.slice(pack.base.length)]; if (url.pathname.endsWith('.json')) type = 'application/json'; }
        }
        if (!body && url.pathname.startsWith('/proxy/')) {
          const value = url.pathname.endsWith('/model') || url.pathname.endsWith('/apartment_model') ? echo.seed
            : url.pathname.endsWith('/api/states') ? [] : url.pathname.endsWith('/api/config') ? { unit_system: { length: 'm' } } : {};
          body = Buffer.from(JSON.stringify(value)); type = 'application/json';
        }
        const entry = sources.get(url.pathname === '/' ? '/index.html' : url.pathname);
        if (!body && entry && !url.pathname.startsWith('/assets/apartment/')) {
          body = fs.readFileSync(entry.selected); served[entry.relative] = { sha256: hash(body), candidate: entry.candidate };
          type = /\.(?:js|mjs|jsx)$/.test(entry.selected) ? 'text/javascript' : entry.selected.endsWith('.html') ? 'text/html'
            : entry.selected.endsWith('.css') ? 'text/css' : /\.(?:json|webmanifest)$/.test(entry.selected) ? 'application/json'
              : entry.selected.endsWith('.svg') ? 'image/svg+xml' : type;
        }
        if (!body) { res.writeHead(404); res.end(); return; }
        res.writeHead(200, { 'Content-Type': type, 'Content-Length': body.length, 'Cache-Control': 'no-store' });
        res.end(req.method === 'HEAD' ? undefined : body);
      } catch (error) { errors.push('authored HTTP handler: ' + error.message); res.writeHead(500); res.end(); }
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    origin = 'http://127.0.0.1:' + server.address().port;
    handler = createEquipmentRegistrationGateway({ env: { NODE_ENV: 'test', HOME_WEB_EQUIPMENT_REGISTRATION_ENABLED: '1',
      HOME_WEB_EQUIPMENT_REGISTRATION_ROOT: privateRoot, HOME_WEB_EQUIPMENT_REGISTRATION_ORIGIN: origin, HOME_CONTENT_PACKS_DIR: packs },
      authenticate: req => req.headers.cookie?.includes('authored_owner_session=1') === true });
    browser = await chromium.launch({ headless: true, args: ['--use-angle=d3d11', '--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'],
      ...(!fs.existsSync(chromium.executablePath()) ? { channel: 'msedge' } : {}) });
    const context = await browser.newContext({ viewport: { width: 1366, height: 768 }, serviceWorkers: 'block' });
    await context.addCookies([{ name: 'authored_owner_session', value: '1', url: origin }]);
    await context.addInitScript(() => {
      localStorage.setItem('apartment3d.site', 'echo'); localStorage.setItem('home.perf.backgroundWarmup', 'off');
      window.HG_DISABLE_SERVICE_WORKER = true;
    });
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      if (['data:', 'blob:'].includes(url.protocol)) return route.continue();
      if (url.origin !== origin) {
        const harmlessProbe = ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) && route.request().method() === 'GET' && url.pathname === '/healthz';
        if (!harmlessProbe) forbidden.push({ origin: url.origin, path: url.pathname, method: route.request().method(), external: true });
        return route.abort();
      }
      return route.continue();
    });
    await context.routeWebSocket('**/*', socket => socket.close());
    page = await context.newPage(); page.setDefaultTimeout(15000);
    page.on('pageerror', error => errors.push(error.message));

    async function capture(name) {
      await page.evaluate(() => {
        if (document.getElementById('authored-shell-proof-note')) return;
        const note = document.createElement('aside'); note.id = 'authored-shell-proof-note';
        note.textContent = 'AUTHORED ROOM · REAL RENDERER · NO LIVE CONNECTION';
        Object.assign(note.style, { position: 'fixed', left: '6px', bottom: '4px', zIndex: '9999', pointerEvents: 'none',
          color: '#fff', background: '#111c', padding: '3px 5px', font: '8px system-ui,sans-serif', letterSpacing: '.06em' });
        document.body.append(note);
      });
      const file = name + '.png'; await page.screenshot({ path: path.join(run, file) }); screenshots.push(file);
    }
    async function tab(name) {
      const tabs = page.locator('.apt-home-tabs');
      const semantic = tabs.getByRole('tab', { name, exact: true });
      await (await semantic.count() ? semantic : tabs.getByRole('button', { name, exact: true })).click();
    }
    async function selectedTab(key) {
      await page.waitForFunction(key => document.activeElement?.id === 'apt-home-tab-' + key
        && document.getElementById('apt-home-tab-' + key)?.getAttribute('aria-selected') === 'true', key);
    }
    async function keyboardTabs(narrow) {
      await page.getByRole('tab', { name: 'Controls', exact: true }).focus();
      await page.keyboard.press('Home'); await selectedTab('controls');
      await page.keyboard.press('ArrowRight'); await selectedTab(narrow ? 'camera' : 'chat');
      await page.keyboard.press('ArrowRight'); await selectedTab(narrow ? 'chat' : 'controls');
      await page.keyboard.press('End'); await selectedTab('chat');
      await page.keyboard.press('ArrowRight'); await selectedTab('controls');
      await page.keyboard.press('ArrowLeft'); await selectedTab('chat');
      assert.equal(await page.locator('#apt-home-tab-camera').isVisible(), narrow);
      checks.push({ stage: narrow ? 'phone_keyboard_visible_tabs' : 'desktop_keyboard_skips_hidden_camera_tab' });
    }
    async function phoneComposerBounds() {
      const controls = [page.locator('.apt-home-chat'), page.getByRole('combobox', { name: 'Command input', exact: true }),
        page.getByRole('button', { name: 'Send', exact: true }), page.getByRole('button', { name: 'Start voice mode', exact: true })];
      const viewport = page.viewportSize(), bounds = [];
      for (const [index, control] of controls.entries()) {
        const rect = await control.boundingBox(); assert.ok(rect, 'phone composer control ' + index + ' is visible');
        assert.ok(rect.width > 0 && rect.height > 0 && rect.x >= -.5 && rect.y >= -.5
          && rect.x + rect.width <= viewport.width + .5 && rect.y + rect.height <= viewport.height + .5,
        'phone composer control ' + index + ' stays inside viewport: ' + JSON.stringify(rect));
        bounds.push(rect);
      }
      for (const control of controls.slice(1)) {
        await control.focus(); assert.equal(await control.evaluate(element => element === document.activeElement), true);
      }
      checks.push({ stage: 'phone_chat_composer_send_and_mic_inside_viewport', viewport, bounds });
    }
    async function waitSite(site) {
      await page.waitForFunction(site => window.__HOME_APARTMENT_SITE_API?.readback().phase === 'ready'
        && window.__APT_ENGINE?.siteId === site, site);
    }
    async function identityCheck(stage) {
      const state = await page.evaluate(() => ({
        engine: window.__APT_ENGINE === window.__shellProof.engine,
        canvas: window.__APT_ENGINE?.renderer.domElement === window.__shellProof.canvas,
        controller: window.__havUiDebug === window.__shellProof.controller,
        input: document.querySelector('input[aria-label="Command input"]') === window.__shellProof.input,
        inputs: document.querySelectorAll('input[aria-label="Command input"]').length,
        draft: document.querySelector('input[aria-label="Command input"]')?.value,
        canvases: document.querySelectorAll('.apt-home-stage canvas').length,
        overflow: document.documentElement.scrollWidth > innerWidth,
        stage: (() => { const rect = document.querySelector('.apt-home-stage').getBoundingClientRect(); return { width: rect.width, height: rect.height }; })(),
      }));
      for (const field of ['engine', 'canvas', 'controller', 'input']) assert.equal(state[field], true, stage + ' retains ' + field);
      assert.equal(state.inputs, 1); assert.equal(state.canvases, 1, 'one renderer canvas throughout shell navigation');
      assert.equal(state.draft, 'Authored unsent shell draft'); assert.equal(state.overflow, false, stage + ' has no document overflow');
      assert.ok(state.stage.width > 0 && state.stage.height > 0, stage + ' retains an allocated room viewport');
      checks.push({ stage, ...state });
    }
    await page.goto(origin + '/?apartmentRedesign=1&worldZoom=off', { waitUntil: 'domcontentloaded' });
    await page.locator('.apt-home-shell[data-home-layout="unified"]').waitFor({ timeout: 45000 });
    await waitSite('echo');
    await page.getByRole('combobox', { name: 'Home', exact: true }).selectOption('victoria');
    await waitSite('victoria');
    await page.waitForFunction(() => window.__APT_ENGINE?.overlay.virtualFixturesGroup?.children.length === 4);
    await tab('Chat');
    const command = page.getByRole('combobox', { name: 'Command input', exact: true });
    await command.fill('Authored unsent shell draft');
    await page.evaluate(() => { window.__shellProof = { engine: window.__APT_ENGINE, canvas: window.__APT_ENGINE.renderer.domElement,
      controller: window.__havUiDebug, input: document.querySelector('input[aria-label="Command input"]') }; });
    await tab('Controls'); await tab('Chat'); await identityCheck('desktop_tabs_keep_unsent_chat');
    await keyboardTabs(false);
    await tab('Controls');
    for (const [width, height] of [[1366,768], [768,1024], [390,844]]) {
      await page.setViewportSize({ width, height });
      await page.waitForFunction(() => window.__APT_ENGINE?.renderer.domElement.width > 0);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await identityCheck('viewport_' + width + 'x' + height);
      await capture('unified-controls-' + width + 'x' + height);
    }
    await keyboardTabs(true);
    await tab('Chat'); await identityCheck('phone_chat_draft'); await phoneComposerBounds(); await capture('unified-chat-390x844');
    await page.setViewportSize({ width: 1366, height: 768 }); await tab('Controls');
    await page.evaluate(() => window.__APT_ENGINE.rig.snapHome(0));

    const aimDescriptor = id => page.evaluate(id => window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id), id);
    async function fixturePoint(id) {
      return page.evaluate(id => {
        const engine = window.__APT_ENGINE, descriptor = engine.overlay.virtualFixtureInteractionDescriptor(id);
        engine.apartmentRoot.updateWorldMatrix(true, true);
        const point = engine.apartmentRoot.position.clone().set(...descriptor.display_anchor_m); point.z -= .08;
        engine.apartmentRoot.localToWorld(point); point.project(engine.camera);
        const rect = engine.renderer.domElement.getBoundingClientRect();
        return { x: rect.left + (point.x + 1) * rect.width / 2, y: rect.top + (1 - point.y) * rect.height / 2 };
      }, id);
    }
    async function targetPoint(point) {
      return page.evaluate(async point => {
        const engine = window.__APT_ENGINE; await engine.modes.preload(['collision']);
        engine.apartmentRoot.updateWorldMatrix(true, true);
        const projected = engine.apartmentRoot.localToWorld(engine.apartmentRoot.position.clone().set(...point)).project(engine.camera);
        const rect = engine.renderer.domElement.getBoundingClientRect();
        const x = rect.left + (projected.x + 1) * rect.width / 2, y = rect.top + (1 - projected.y) * rect.height / 2;
        const hit = engine.picking.surfaceHit(engine.apartmentRoot, [engine.modes.getCollision()], x, y);
        return { x, y, hit: hit && { point: hit.point, normal: hit.normal } };
      }, point);
    }
    const fixtureIds = await page.evaluate(() => window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures.map(item => item.fixture_id));
    assert.equal(fixtureIds.length, 4);
    const before = Object.fromEntries(await Promise.all(fixtureIds.map(async id => [id, await aimDescriptor(id)])));
    for (const id of fixtureIds) {
      const point = await fixturePoint(id); await page.mouse.click(point.x, point.y);
      await page.waitForFunction(id => document.querySelector('select[aria-label="Virtual fixture"]')?.value === id, id);
      assert.deepEqual((await aimDescriptor(id)).display_anchor_m, before[id].display_anchor_m);
    }
    // Selecting the already-selected cylinder must reveal its existing Controls
    // panel even when no fixture ID or details-open state changes.
    await tab('Chat');
    const selectedCylinder = await fixturePoint(fixtureIds.at(-1));
    await page.mouse.click(selectedCylinder.x, selectedCylinder.y);
    await page.waitForFunction(() => document.getElementById('apt-home-tab-controls')?.getAttribute('aria-selected') === 'true'
      && document.getElementById('apt-home-panel-controls')?.hidden === false);
    const fixtureSelect = page.getByRole('combobox', { name: 'Virtual fixture', exact: true });
    await fixtureSelect.focus(); await page.keyboard.press('Home'); await page.keyboard.press('ArrowDown');
    assert.equal(await fixtureSelect.evaluate(element => element === document.activeElement), true);
    assert.equal(await page.getByRole('tab', { name: 'Controls', exact: true }).getAttribute('aria-selected'), 'true');
    assert.equal(await page.locator('#apt-home-panel-controls').isVisible(), true);
    checks.push({ stage: 'same_fixture_click_from_chat_and_native_keyboard_selection_reveal_controls',
      keyboard_selected_fixture: await fixtureSelect.inputValue() });
    const directId = fixtureIds[2], cylinder = await fixturePoint(directId), target = await targetPoint([2.4,2.1,0]);
    assert.ok(target.hit, 'direct aim target comes from the actual registered authored collision mesh');
    await page.mouse.move(cylinder.x, cylinder.y); await page.mouse.down();
    await page.mouse.move(target.x, target.y, { steps: 12 }); await page.mouse.up();
    await page.waitForFunction(id => !!window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id)?.target, directId);
    const aimed = await aimDescriptor(directId);
    assert.deepEqual(aimed.display_anchor_m, before[directId].display_anchor_m, 'aiming leaves the saved socket fixed');
    assert.ok(Math.hypot(...aimed.target.point.map((value, axis) => value - target.hit.point[axis])) < .03);
    assert.equal(aimed.physical_commands, false);
    await page.getByRole('button', { name: 'Undo aim', exact: true }).click();
    await page.waitForFunction(({ id, pan }) => window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id)?.state.pan_deg === pan,
      { id: directId, pan: before[directId].state.pan_deg });
    assert.deepEqual((await aimDescriptor(directId)).target, before[directId].target);
    checks.push({ stage: 'four_actual_fixture_clicks_surface_drag_and_undo', fixtureIds, aimed, undo: await aimDescriptor(directId) });
    await capture('unified-fixture-controls-1366x768');

    await page.setViewportSize({ width: 390, height: 844 });
    await tab('Camera · JEV');
    assert.equal(requests.some(row => !['GET', 'HEAD'].includes(row.method)), false, 'opening camera information never starts acquisition');
    await identityCheck('camera_tab_preserves_controller_and_draft');
    await capture('unified-camera-unavailable-390x844');
    await tab('Chat');
    await command.fill('/help');
    await page.getByRole('listbox', { name: 'Slash commands', exact: true }).waitFor();
    await command.fill('Authored unsent shell draft');

    await page.getByRole('button', { name: 'Home menu', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Metrics', exact: true }).click();
    const metrics = page.getByRole('dialog', { name: 'Home metrics', exact: true });
    await metrics.waitFor(); await page.keyboard.press('Escape');
    await metrics.waitFor({ state: 'hidden' });
    assert.equal(await page.evaluate(() => {
      const element = document.activeElement;
      return !!element?.isConnected && /^(BUTTON|INPUT|SELECT|TEXTAREA)$/.test(element.tagName)
        && element.getClientRects().length > 0 && !element.closest('[role="dialog"]');
    }), true, 'closing Metrics restores focus to a visible shell control');
    checks.push({ stage: 'metrics_escape_closes_dialog_and_restores_focus' });

    // The classic escape hatch is a UI action, then the public URL override can
    // re-enable the reversible shell without changing household configuration.
    const previousHome = page.getByText('Previous Home', { exact: true });
    if (!await previousHome.isVisible()) {
      await page.getByRole('button', { name: 'Home menu', exact: true }).click();
    }
    await previousHome.click();
    await page.waitForFunction(() => !document.querySelector('.apt-home-shell[data-home-layout="unified"]'));
    assert.equal(await page.getByRole('combobox', { name: 'Command input', exact: true }).inputValue(), 'Authored unsent shell draft');
    checks.push({ stage: 'classic_ui_escape_hatch_keeps_chat_draft' });
    await capture('previous-home-classic-390x844');
    await page.goto(origin + '/?apartmentRedesign=1&worldZoom=off', { waitUntil: 'domcontentloaded' });
    await page.locator('.apt-home-shell[data-home-layout="unified"]').waitFor(); await waitSite('echo');
    await page.goto(origin + '/?apartmentRedesign=0&worldZoom=off', { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: 'Open apartment view', exact: true }).waitFor();
    assert.equal(await page.locator('.apt-home-shell[data-home-layout="unified"]').count(), 0);
    checks.push({ stage: 'explicit_url_override_reenables_and_rolls_back_shell' });

    assert.deepEqual(readRegistration(privateRoot, { contentPacksDir: packs }), initial);
    assert.deepEqual(Object.fromEntries(protectedFiles.map(file => [file, hash(fs.readFileSync(file))])), protectedHashes);
    assert.deepEqual(forbidden, []); assert.deepEqual(errors, []);
    receipt.registration_digest = initial.digest; receipt.protected_asset_hashes = protectedHashes; receipt.ok = true;
    fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    process.stdout.write('Authored Apartment shell browser evidence: ' + path.join(run, 'receipt.json') + '\n');
  }
  try { await Promise.race([exercise(), deadline]); }
  catch (error) {
    receipt.failure = error.message;
    if (page && !page.isClosed()) {
      receipt.body = (await page.locator('body').innerText().catch(() => '')).slice(-6000);
      receipt.layout = await page.evaluate(() => ['html','body','#root','.apt-home-shell','.apt-home-stage'].map(selector => {
        const element = document.querySelector(selector), rect = element?.getBoundingClientRect();
        return { selector, rect: rect?.toJSON(), display: element && getComputedStyle(element).display,
          visibility: element && getComputedStyle(element).visibility };
      })).catch(() => []);
      await page.screenshot({ path: path.join(run, 'failure.png') }).catch(() => {});
    }
    fs.writeFileSync(path.join(run, 'failure-receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    throw new Error(error.message + '\nAuthored shell failure evidence: ' + path.join(run, 'failure-receipt.json'), { cause: error });
  } finally {
    clearTimeout(timer); await browser?.close();
    if (server) await new Promise(resolve => { server.closeAllConnections(); server.close(resolve); });
  }
});
