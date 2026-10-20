// APT-C02/C03/C05: Chat bound to the room in the real Home shell with the real
// renderer and authored room data. Selecting a light offers the About chip; a
// chip message changes only the selected Victoria light in the room model, is
// refused for Los Angeles and is never sent anywhere. The clipboard is an
// in-page stub. No household assets, service writes, sessions or model runs.
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

// Same public-code inventory fence as the room-selection browser proof.
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

test('Chat bound to the room changes only the modeled selected light and dispatches nothing', { timeout: 180000 }, async () => {
  assert.ok(!process.env.HOME_APARTMENT_CANDIDATE_ROOT, 'proof serves only this worktree');
  const run = path.join(ROOT, '.tmp/apartment-chat-room-browser', 'run-' + Date.now());
  fs.mkdirSync(run, { recursive: true });
  const packs = path.join(run, 'packs'), privateRoot = path.join(run, 'private');
  const echo = installPack(packs, 'echo'), victoria = installPack(packs, 'victoria');
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1,
    packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  const protectedFiles = [echo, victoria].flatMap(item => Object.keys(item.files).map(name => path.join(packs, item.site, item.revision, name)));
  protectedFiles.push(path.join(packs, 'echo/ACTIVE'), path.join(packs, 'victoria/ACTIVE'), path.join(packs, 'index.json'), path.join(privateRoot, 'registration.json'));
  const protectedHashes = Object.fromEntries(protectedFiles.map(file => [file, hash(fs.readFileSync(file))]));
  const sources = publicSources(SRC), receipt = { schema: 'home-apartment-chat-room-authored-browser-1',
    authored_room: true, real_renderer: true, complete_home_shell: true, household_data: false, screenshots: false,
    source_mode: 'current-worktree-only', source_sha256: Object.fromEntries(['home-apartment.jsx', 'home-app.jsx',
      'home-apartment-chat-context.js', 'index.html'].map(name => [name, hash(fs.readFileSync(path.join(SRC, name)))])),
    physical_commands: 0, saves: 0, camera_sessions: 0, model_sessions: 0,
    checks: [], requests: [], forbidden: [], blocked_read_subscriptions: [], missing_code: [], errors: [], served: {}, browser: browserOptions() };
  let server, handler, origin, browser, context, page, timer;
  const save = () => fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => {
    receipt.deadline_at = Date.now(); save();
    browser?.close().catch(() => {}); server?.closeAllConnections(); reject(Error('145s chat room browser deadline'));
  }, 145000); });
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
    await context.addInitScript(() => { window.__copied = []; Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: text => { window.__copied.push(text); return Promise.resolve(); } } }); localStorage.setItem('apartment3d.site', 'echo'); localStorage.setItem('home.perf.backgroundWarmup', 'off'); window.HG_DISABLE_SERVICE_WORKER = true; });
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
    const chatTab = () => page.locator('#apt-home-tab-chat');
    const controlsTab = () => page.locator('#apt-home-tab-controls');
    const composer = () => page.getByPlaceholder('Ask your home, or type /');
    const aboutChip = () => page.locator('[data-chat-context="about"]');
    const send = async text => {
      await composer().fill(text); await composer().press('Enter');
      await page.waitForFunction(() => document.querySelector('[placeholder="Ask your home, or type /"]')?.value === '');
    };
    // Offline Home shows its connection form in place of the feed, so the
    // conversation is read through the header's own Copy transcript action.
    const transcript = async () => {
      const count = await page.evaluate(() => window.__copied.length);
      await page.locator('[data-chat-header-action="copy"]').click();
      await page.waitForFunction(count => window.__copied.length > count, count);
      return page.evaluate(() => window.__copied.at(-1));
    };
    const lastLines = (text, n) => text.split('\n').slice(-n);
    const spot = id => page.evaluate(id => window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures.find(item => item.fixture_id === id), id);
    const selected = id => page.waitForFunction(id => { const ids = []; window.__APT_ENGINE.overlay.virtualFixturesGroup.traverse(node => {
      if (node.name === 'virtual-fixture-aim-handle' && node.visible) ids.push(node.userData.fixture_id); }); return ids.length === 1 && ids[0] === id; }, id);
    const echoLight = () => page.getByRole('button', { name: /^Authored Echo light/ });
    await waitSite('echo');
    await echoLight().waitFor();
    await page.waitForFunction(() => window.__HOME_APARTMENT_SITE_API?.readback().loadedContentRevision === 'authored-echo-r1');
    await check('Echo: the About chip names the selected light and Chat refuses without routing', async () => {
      await echoLight().click();
      await page.getByRole('button', { name: 'Close light', exact: true }).waitFor();
      await chatTab().click();
      await aboutChip().waitFor();
      assert.equal(await aboutChip().locator('span').textContent(), 'About Authored Echo light');
      assert.equal(await page.locator('.apt-ui-chat-model').textContent(), 'Model not chosen · Los Angeles · Home Assistant');
      await send('brighter');
      const text = await transcript();
      assert.deepEqual(lastLines(text, 1), ['You: brighter'], 'the refusal is a system note; no Home reply and no route');
      assert.deepEqual(receipt.forbidden, []);
      return { refused: true };
    });
    await selectHome('victoria');
    await page.waitForFunction(() => window.__APT_ENGINE?.overlay.virtualFixturesReadback().fixture_count === 4);
    const inventory = await page.evaluate(() => window.__APT_ENGINE.overlay.virtualFixturesReadback());
    const fixture = inventory.fixtures[1], other = inventory.fixtures[2];
    await check('Victoria: 50%, warmer and brighter change only the selected modeled light', async () => {
      await controlsTab().click(); await page.getByRole('button', { name: 'Select ' + fixture.label, exact: true }).click();
      await selected(fixture.fixture_id);
      await chatTab().click();
      await aboutChip().waitFor();
      assert.equal(await aboutChip().locator('span').textContent(), 'About ' + fixture.label);
      assert.equal(await page.locator('.apt-ui-chat-model').textContent(), 'Model not chosen · Victoria · modeled room');
      const before = await spot(fixture.fixture_id), untouched = await spot(other.fixture_id);
      const range = before.color_temp_ranges.spotlight, k0 = before.state.spotlight.color_temp_kelvin, kelvin = Math.max(range[0], k0 - 400);
      const done = (pct, k) => `Home: ${fixture.label} spot is now ${pct}% · ${k} K in the room model. Nothing was sent to a light.`;
      await send('50%'); await send('warmer'); await send('make it brighter');
      await send('turn on the kitchen lights');
      const text = await transcript();
      assert.deepEqual(lastLines(text, 8), ['You: 50%', done(50, k0), 'You: warmer', done(50, kelvin), 'You: make it brighter', done(70, kelvin),
        'You: turn on the kitchen lights',
        `Home: I can make ${fixture.label} brighter, dimmer, warmer or cooler, or set a level like 50% (the spot, unless you say down). Nothing changed.`]);
      const after = await spot(fixture.fixture_id);
      assert.deepEqual(after.state.spotlight, { ...before.state.spotlight, on: true, brightness: 0.7, color_temp_kelvin: kelvin });
      assert.deepEqual(after.state.downward, before.state.downward);
      assert.deepEqual(after.state.radials, before.state.radials);
      assert.deepEqual(await spot(other.fixture_id), untouched, 'another light is never changed');
      assert.deepEqual(receipt.forbidden, []);
      return { fixture_id: fixture.fixture_id, brightness: 0.7, kelvin };
    });
    await check('the About chip follows the selection and stays removed only for that selection', async () => {
      // The inspector replaces the room list while a light is selected; N and P step it.
      await controlsTab().click(); await page.evaluate(() => document.activeElement?.blur()); await page.keyboard.press('n');
      await selected(other.fixture_id); await chatTab().click();
      assert.equal(await aboutChip().locator('span').textContent(), 'About ' + other.label);
      await page.getByRole('button', { name: "Don't include " + other.label, exact: true }).click();
      assert.equal(await aboutChip().count(), 0);
      await controlsTab().click(); await page.evaluate(() => document.activeElement?.blur()); await page.keyboard.press('p');
      await selected(fixture.fixture_id); await chatTab().click();
      assert.equal(await aboutChip().locator('span').textContent(), 'About ' + fixture.label);
      return { reset_on_selection_change: true };
    });
    for (const [file, expected] of Object.entries(protectedHashes)) assert.equal(hash(fs.readFileSync(file)), expected, 'authored pack and registration bytes stay exact');
    for (const [file, expected] of Object.entries(receipt.source_sha256)) assert.equal(receipt.served[file]?.sha256, expected, 'served production source matches the captured candidate');
    assert.deepEqual(receipt.forbidden, []); assert.deepEqual(receipt.missing_code, []); assert.deepEqual(receipt.errors, []);
    assert.equal(receipt.requests.some(request => !['GET', 'HEAD'].includes(request.method)), false);
    receipt.authored_data_hashes_unchanged = true;
  }
  try { await Promise.race([proof(), deadline]); receipt.result = 'passed'; }
  catch (error) {
    receipt.result = 'failed'; receipt.failure = error.stack || String(error); save();
    if (page && !page.isClosed()) receipt.failure_state = await page.evaluate(() => ({
      overlay: window.HomeOverlay?.topKey?.(), activeElement: document.activeElement?.outerHTML?.slice(0, 600),
      site: window.__HOME_APARTMENT_SITE_API?.readback(), chat: document.querySelector('.apt-ui-chat-feed')?.innerText?.slice(-1600),
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
