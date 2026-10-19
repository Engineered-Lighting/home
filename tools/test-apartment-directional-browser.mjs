// Directional lighting: real Home shell, renderer and native authored input.
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

test('real authored directional controls preserve native input, aim and output boundaries', { timeout: 180000 }, async () => {
  assert.ok(!process.env.HOME_APARTMENT_CANDIDATE_ROOT, 'proof serves only this worktree');
  const run = path.join(ROOT, '.tmp/apartment-directional-browser', 'run-' + Date.now());
  fs.mkdirSync(run, { recursive: true });
  const packs = path.join(run, 'packs'), privateRoot = path.join(run, 'private');
  const echo = installPack(packs, 'echo'), victoria = installPack(packs, 'victoria');
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1,
    packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  const protectedFiles = [echo, victoria].flatMap(item => Object.keys(item.files).map(name => path.join(packs, item.site, item.revision, name)));
  protectedFiles.push(path.join(packs, 'echo/ACTIVE'), path.join(packs, 'victoria/ACTIVE'), path.join(packs, 'index.json'), path.join(privateRoot, 'registration.json'));
  const protectedHashes = Object.fromEntries(protectedFiles.map(file => [file, hash(fs.readFileSync(file))]));
  const sources = publicSources(SRC), receipt = { schema: 'home-apartment-directional-authored-browser-1',
    authored_room: true, real_renderer: true, complete_home_shell: true, household_data: false, screenshots: false,
    source_mode: 'current-worktree-only', source_sha256: Object.fromEntries(['home-apartment.jsx', 'home-apartment-cards.jsx', 'home-apartment-directional.js', 'home-3d/engine.js',
      'home-3d/picking.js', 'home-3d/direct-aim-gesture.mjs'].map(name => [name, hash(fs.readFileSync(path.join(SRC, name)))])),
    physical_commands: 0, saves: 0, camera_sessions: 0, model_sessions: 0,
    checks: [], requests: [], forbidden: [], blocked_read_subscriptions: [], missing_code: [], errors: [], served: {}, browser: browserOptions() };
  let server, handler, origin, browser, context, page, timer;
  const save = () => fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => {
    browser?.close().catch(() => {}); server?.closeAllConnections(); reject(Error('145s directional browser deadline'));
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
    browser = await chromium.launch(receipt.browser); context = await browser.newContext({ viewport: { width: 1366, height: 768 }, deviceScaleFactor: 1, reducedMotion: 'no-preference', serviceWorkers: 'block' });
    receipt.reduced_motion = 'no-preference'; // Exercise the real rig's ambient hover settling, rather than its reduced-motion bypass.
    receipt.runtime = { node: process.version, playwright: require('playwright/package.json').version, module: require.resolve('playwright'), browser: browser.version() };
    await context.addCookies([{ name: 'authored_owner_session', value: '1', url: origin }]);
    await context.addInitScript(() => {
      localStorage.setItem('apartment3d.site', 'echo'); localStorage.setItem('home.perf.backgroundWarmup', 'off'); window.HG_DISABLE_SERVICE_WORKER = true;
      window.__directionalNative = [];
      for (const type of ['pointerdown', 'pointerup', 'pointercancel', 'lostpointercapture', 'keydown', 'click', 'wheel']) window.addEventListener(type, event => {
        if (type === 'keydown' && event.key !== 'Escape') return;
        const engine = window.__APT_ENGINE;
        const control = event.target?.closest?.('.apt-home-setup') ? 'Setup' : event.target?.getAttribute?.('aria-label') || event.target?.closest?.('button')?.getAttribute('title');
        const lifecycle = type === 'click' && ['Setup', 'Show virtual spotlights layer', 'Echo Park'].includes(control);
        const descriptors = lifecycle && engine?.overlay.virtualFixturesReadback?.().fixtures
          .map(fixture => engine.overlay.virtualFixtureInteractionDescriptor(fixture.fixture_id));
        window.__directionalNative.push({ type, pointerId: event.pointerId, pointerType: event.pointerType, isTrusted: event.isTrusted,
          key: event.key, at: performance.now(), start: event.target?.closest?.('[data-apt-radial-start]')?.getAttribute('data-apt-radial-start'),
          control, call_count: window.__directionalCalls?.length || 0, descriptors,
          fixtures: type === 'keydown' ? engine?.overlay.virtualFixturesReadback?.().fixtures : undefined });
      }, true);
    });
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
    const inspector = () => page.locator('[data-victoria-virtual-fixtures="1"][aria-label="Fixture lighting inspector"]');
    const composer = () => page.locator('[data-apt-directional-composer="1"]');
    const descriptor = id => page.evaluate(id => window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id), id);
    const inventory = () => page.evaluate(() => window.__APT_ENGINE.overlay.virtualFixturesReadback());
    const selected = () => page.evaluate(() => {
      const ids = []; window.__APT_ENGINE.overlay.virtualFixturesGroup.traverse(node => {
        if (node.name === 'virtual-fixture-aim-handle' && node.visible) ids.push(node.userData.fixture_id);
      }); return ids;
    });
    const expectSelected = async fixture => {
      await inspector().getByRole('heading', { name: fixture.label, exact: true }).waitFor();
      await page.waitForFunction(id => {
        let found = false; window.__APT_ENGINE.overlay.virtualFixturesGroup.traverse(node => {
          if (node.name === 'virtual-fixture-aim-handle' && node.visible && node.userData.fixture_id === id) found = true;
        }); return found;
      }, fixture.fixture_id);
      assert.deepEqual(await selected(), [fixture.fixture_id]);
    };
    const expectMode = async mode => {
      await composer().getByRole('button', { name: mode === 'zones' ? 'Zones' : 'Spot', exact: true }).waitFor();
      await page.waitForFunction(mode => document.querySelector('[data-apt-directional-mode="' + mode + '"]')?.getAttribute('aria-pressed') === 'true', mode);
    };
    const navigationPose = () => page.evaluate(() => {
      const pose = window.__APT_ENGINE.rig.snapshotPose();
      return { az: pose.az, el: pose.el, zoom: pose.zoom, curAz: pose.curAz, curEl: pose.curEl, curRadius: pose.curRadius,
        previewAz: pose.previewAz, previewEl: pose.previewEl, overshoot: pose.overshoot, target: pose.target };
    });
    const setMode = async mode => {
      const button = composer().getByRole('button', { name: mode === 'zones' ? 'Zones' : 'Spot', exact: true }), box = await button.boundingBox();
      assert.ok(box); const before = await navigationPose();
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await page.mouse.down();
      assert.equal(await captureActive(), false, 'the native pill press must not leak pointer capture to the orbit rig');
      await page.mouse.move(box.x + box.width / 2 + 2, box.y + box.height / 2);
      assert.deepEqual(await navigationPose(), before, 'a pressed native pill does not start a camera drag preview');
      await page.mouse.up(); await expectMode(mode);
    };
    const waitHUD = () => page.waitForTimeout(180); // More than two existing 80ms HUD opportunities; no new clock or polling loop.
    const writes = () => page.evaluate(() => window.__directionalCalls || []);
    const paintWrites = rows => rows.filter(row => row.method === 'setVirtualFixtureState' && Object.keys(row.patch || {}).length === 1 && Array.isArray(row.patch.radials));
    const nativeMarker = async (type, control) => {
      const event = await page.evaluate(({ type, control }) => [...window.__directionalNative].reverse()
        .find(row => row.type === type && (control == null || row.control === control)), { type, control });
      assert.ok(event?.isTrusted, 'retirement begins with the actual trusted browser event'); return event;
    };
    const withoutRadials = value => { const result = structuredClone(value); delete result.state.radials; return result; };
    const canvasPoint = (kind, id, avoid = null) => page.evaluate(({ kind, id, avoid }) => {
      const engine = window.__APT_ENGINE, canvas = engine.renderer.domElement, host = engine.picking.getHost(), rect = host.getBoundingClientRect();
      const targets = engine.overlay.virtualFixturePickObjects(), candidates = [];
      if (kind === 'fixture') for (const object of targets.filter(node => node.userData.fixture_id === id && node.userData.interaction_role === 'fixture')) {
        const point = engine.picking.projectToScreen(object.getWorldPosition(engine.camera.position.clone()));
        if (point.visible) candidates.push({ x: rect.left + point.x, y: rect.top + point.y });
      }
      else for (const y of [.2, .35, .5, .65, .8, .9]) for (const x of [.1, .25, .4, .55, .7, .85, .95]) candidates.push({ x: rect.left + rect.width * x, y: rect.top + rect.height * y });
      for (const point of candidates) {
        if (document.elementFromPoint(point.x, point.y) !== canvas || avoid && Math.hypot(point.x - avoid.x, point.y - avoid.y) < 30) continue;
        const hits = engine.picking.pick(targets, point.x, point.y);
        if (kind === 'fixture' && hits[0]?.object.userData.fixture_id === id) return point;
        if (kind !== 'fixture' && !hits.length && (kind !== 'surface' || engine.displaySurfaceHit(point.x, point.y))) return point;
      }
      return null;
    }, { kind, id, avoid });
    const planePoint = async (id, number, radius) => {
      const result = await page.evaluate(({ id, number, radius }) => {
        const engine = window.__APT_ENGINE, host = engine.picking.getHost(), rect = host.getBoundingClientRect();
        const snapshot = engine.overlay.virtualFixtureInteractionDescriptor(id), radial = snapshot.state.radials.find(zone => zone.number === number);
        const matrix = engine.camera.projectionMatrix.clone().multiply(engine.camera.matrixWorldInverse).multiply(engine.apartmentRoot.matrixWorld);
        const frame = window.HomeApartmentDirectional.createPlaneFrame({ anchor: snapshot.display_anchor_m, clipFromLocal: matrix.elements,
          width: host.clientWidth, height: host.clientHeight });
        const angle = radial.azimuth_deg * Math.PI / 180, point = [radius * Math.cos(angle), radius * Math.sin(angle)];
        const screen = frame?.project(point), hub = frame?.project([0, 0]), tick = frame?.project([1.3 * Math.cos(angle), 1.3 * Math.sin(angle)]);
        const hubElement = document.querySelector('[data-apt-directional-composer="1"] [data-apt-radial-start="hub"]');
        const lobe = document.querySelector('[data-apt-directional-composer="1"] [data-apt-radial-start="zone:' + number + '"]');
        const line = lobe?.parentElement.querySelector('line');
        const actualHub = hubElement && { x: +hubElement.getAttribute('cx'), y: +hubElement.getAttribute('cy') };
        const actualTick = line && { x: (+line.getAttribute('x1') + +line.getAttribute('x2')) / 2, y: (+line.getAttribute('y1') + +line.getAttribute('y2')) / 2 };
        const world = engine.camera.position.clone().set(snapshot.display_anchor_m[0] + point[0], snapshot.display_anchor_m[1] + point[1], snapshot.display_anchor_m[2]);
        engine.apartmentRoot.localToWorld(world);
        const independent = engine.picking.projectToScreen(world);
        if (!screen?.visible || !hub?.visible || !actualHub || !actualTick) return { screen, hub, tick, actualHub, actualTick };
        const client = { x: rect.left + screen.x * rect.width / host.clientWidth, y: rect.top + screen.y * rect.height / host.clientHeight };
        const hit = document.elementFromPoint(client.x, client.y);
        return { screen, hub, tick, actualHub, actualTick, independent, point, client,
          target: hit?.closest?.('[data-apt-radial-start]')?.getAttribute('data-apt-radial-start'), azimuth_deg: radial.azimuth_deg };
      }, { id, number, radius });
      assert.ok(result.client, 'requested authored fixture-height point is visible and its SVG hub/tick are mounted');
      assert.ok(Math.hypot(result.screen.x - result.independent.x, result.screen.y - result.independent.y) <= .01,
        'plane forward projection agrees with the actual renderer root/camera projection');
      assert.ok(Math.hypot(result.hub.x - result.actualHub.x, result.hub.y - result.actualHub.y) < .5, 'actual SVG hub follows the current renderer');
      assert.ok(Math.hypot(result.tick.x - result.actualTick.x, result.tick.y - result.actualTick.y) < .5, 'actual SVG tick follows the actual azimuth plane');
      return result;
    };
    const verifyLobeFades = async () => {
      const rows = await composer().locator('[data-apt-radial-start^="zone:"]').evaluateAll(paths => paths.map(path => {
        const id = /^url\(#(.+)\)$/.exec(path.getAttribute('fill'))?.[1], gradient = id && path.ownerDocument.getElementById(id);
        const hub = path.ownerSVGElement.querySelector('[data-apt-radial-start="hub"]'), node = path.parentElement.querySelector('circle');
        return { id, units: gradient?.getAttribute('gradientUnits'),
          origin: gradient && [+gradient.getAttribute('x1'), +gradient.getAttribute('y1')],
          end: gradient && [+gradient.getAttribute('x2'), +gradient.getAttribute('y2')],
          hub: [+hub.getAttribute('cx'), +hub.getAttribute('cy')], node: node && [+node.getAttribute('cx'), +node.getAttribute('cy')],
          opacity: gradient && [...gradient.querySelectorAll('stop')].map(stop => +stop.getAttribute('stop-opacity')) };
      }));
      assert.equal(rows.length, 6); assert.equal(new Set(rows.map(row => row.id)).size, 6);
      for (const row of rows) {
        assert.equal(row.units, 'userSpaceOnUse'); assert.deepEqual(row.origin, row.hub); assert.deepEqual(row.end, row.node);
        assert.ok(row.opacity[0] > row.opacity[1] && row.opacity[1] > row.opacity[2]); assert.equal(row.opacity[2], 0);
      }
      return { admitted_lobes: rows.length, projected_hub_to_node_gradients: true, outward_opacity_falls_to_zero: true };
    };
    const hubPoint = async id => {
      const geometry = await planePoint(id, 1, 0);
      assert.equal(geometry.target, 'hub', 'the native pickup point reaches the real transparent hub hit region'); return geometry.client;
    };
    const captureActive = () => page.evaluate(() => {
      const host = window.__APT_ENGINE.picking.getHost(), down = [...window.__directionalNative].reverse().find(row => row.type === 'pointerdown' && row.pointerType === 'mouse');
      return !!down && host.hasPointerCapture(down.pointerId);
    });
    const startHub = async id => { const hub = await hubPoint(id); await page.mouse.move(hub.x, hub.y); await page.mouse.down(); assert.equal(await captureActive(), true); return hub; };
    const unchangedAfterRetirement = async (marker, allowReconciliation = false) => {
      const before = await writes(); await waitHUD(); await page.mouse.up(); await waitHUD();
      const after = await writes(); assert.equal(marker.isTrusted, true); assert.ok(Number.isInteger(marker.call_count));
      const late = after.slice(marker.call_count);
      if (!allowReconciliation) {
        assert.deepEqual(late, [], 'no modeled set, aim or restore call occurs after owner retirement');
        return { all_calls_before_release: before.length, all_calls_after_release: after.length, unexpected_late_calls: 0, exact_reconciliation_calls: 0 };
      }
      // The existing lifecycle effect calls the real setVirtualFixtures and
      // synchronously replays its saved writable channel state and targets.
      // Admit that one transaction only; inspect every call, including aim and
      // malformed/combined patches. Read-only azimuths remain in the complete
      // before/after producer descriptors, rather than its writable patch ABI.
      assert.ok(['Show virtual spotlights layer', 'Setup'].includes(marker.control));
      const original = marker.descriptors; assert.equal(original?.length, 4);
      assert.equal(new Set(original.map(item => item.fixture_id)).size, 4);
      const knownTargets = original.filter(item => item.target != null);
      assert.equal(knownTargets.length, 1, 'the one target created by the native Spot stage is the complete target replay inventory');
      assert.equal(knownTargets[0].fixture_id, fixture.fixture_id);
      const expectedOrder = original.map(item => ({ method: 'setVirtualFixtureState', fixture_id: item.fixture_id }))
        .concat(knownTargets.map(item => ({ method: 'aimVirtualFixtureAtTarget', fixture_id: item.fixture_id })));
      assert.equal(expectedOrder.length, 5);
      assert.deepEqual(late.map(({ method, fixture_id }) => ({ method, fixture_id })), expectedOrder,
        'exactly four state replays and the known non-null target replays are allowed, with no other modeled calls');
      const tokens = [...new Set(late.map(row => row.reconciliation))]; assert.equal(tokens.length, 1); assert.ok(Number.isInteger(tokens[0]));
      const transaction = await page.evaluate(id => window.__directionalReconciliations.find(item => item.id === id), tokens[0]);
      assert.equal(transaction?.completed, true); assert.ok(transaction.at >= marker.at);
      assert.deepEqual(transaction.document, { binding: original[0].binding, revision: original[0].equipment_revision, digest: original[0].equipment_digest });
      assert.deepEqual(transaction.before, original, 'the actual reconciliation starts with the exact pre-click producer descriptors');
      assert.deepEqual(transaction.after, original, 'transaction-final producer descriptors keep every state, target, azimuth and canonical identity exact');
      for (const row of late) {
        assert.equal(row.arguments_length, 2); assert.equal(row.ok, true); assert.ok(row.at >= transaction.at);
        const actual = original.find(item => item.fixture_id === row.fixture_id); assert.ok(actual);
        assert.deepEqual(actual.binding, transaction.document.binding);
        assert.equal(actual.equipment_revision, transaction.document.revision); assert.equal(actual.equipment_digest, transaction.document.digest);
        if (row.method === 'aimVirtualFixtureAtTarget') { assert.deepEqual(row.patch, actual.target); continue; }
        assert.deepEqual(Object.keys(row.patch).sort(), ['downward', 'pan_deg', 'radials', 'spotlight', 'tilt_deg']);
        for (const name of ['downward', 'spotlight']) {
          assert.deepEqual(Object.keys(row.patch[name]).sort(), ['brightness', 'color_temp_kelvin', 'on']);
          assert.deepEqual(row.patch[name], actual.state[name]);
        }
        assert.equal(row.patch.pan_deg, actual.state.pan_deg); assert.equal(row.patch.tilt_deg, actual.state.tilt_deg);
        assert.equal(row.patch.radials.length, 6);
        assert.deepEqual(row.patch.radials.map(zone => zone.number), [1, 2, 3, 4, 5, 6]);
        for (const zone of row.patch.radials) {
          assert.deepEqual(Object.keys(zone).sort(), ['brightness', 'color_temp_kelvin', 'number', 'on']);
          const saved = actual.state.radials.find(item => item.number === zone.number);
          assert.deepEqual(zone, { number: saved.number, on: saved.on, brightness: saved.brightness, color_temp_kelvin: saved.color_temp_kelvin });
        }
      }
      return { all_calls_before_release: before.length, all_calls_after_release: after.length,
        unexpected_late_calls: 0, exact_reconciliation_calls: late.length, reconciliation_token: transaction.id, producer_descriptors_unchanged: true };
    };

    await waitSite('echo'); await selectHome('victoria');
    await page.waitForFunction(() => window.HomeApartmentDirectional && window.__APT_ENGINE?.overlay.virtualFixturesReadback().fixture_count === 4);
    await page.evaluate(async () => { const engine = window.__APT_ENGINE; engine.rig.snapHome(0); await engine.modes.preload(['collision']); });
    const initialInventory = await inventory(); assert.equal(initialInventory.visible, true); assert.equal(initialInventory.status, 'ready');
    const fixtures = initialInventory.fixtures, fixture = fixtures[0];
    await page.evaluate(() => {
      const overlay = window.__APT_ENGINE.overlay; window.__directionalCalls = []; window.__directionalReconciliations = [];
      let sequence = 0, activeReconciliation = null;
      const descriptors = () => overlay.virtualFixturesReadback().fixtures.map(item => overlay.virtualFixtureInteractionDescriptor(item.fixture_id));
      const setDocument = overlay.setVirtualFixtures;
      overlay.setVirtualFixtures = (...args) => {
        const document = args[0], transaction = { id: ++sequence, at: performance.now(), before: structuredClone(descriptors()),
          document: document ? { binding: structuredClone(document.binding), revision: document.revision, digest: document.digest } : null, completed: false };
        activeReconciliation = transaction; window.__directionalReconciliations.push(transaction);
        try { return setDocument(...args); }
        finally { queueMicrotask(() => {
          transaction.after = structuredClone(descriptors()); transaction.completed = true;
          if (activeReconciliation === transaction) activeReconciliation = null;
        }); }
      };
      for (const method of ['setVirtualFixtureState', 'aimVirtualFixtureAtTarget', 'restoreVirtualFixtureAim']) {
        const original = overlay[method]; overlay[method] = (...args) => {
          const call = { method, fixture_id: args[0], patch: structuredClone(args[1]), arguments_length: args.length,
            reconciliation: activeReconciliation?.id ?? null, at: performance.now() };
          window.__directionalCalls.push(call); const result = original(...args); call.ok = result?.ok === true; return result;
        };
      }
    });
    await page.getByRole('button', { name: 'Select ' + fixture.label, exact: true }).click(); await expectSelected(fixture);
    const originalAim = await descriptor(fixture.fixture_id);
    await check('selected fixture defaults to Spot and Aim with a native pill above the reticle and no fabricated pending-aim dot', async () => {
      await expectMode('spot');
      assert.equal(await inspector().getByRole('tab', { name: 'Aim', exact: true }).getAttribute('aria-selected'), 'true');
      const circles = await composer().locator('svg circle').evaluateAll(nodes => nodes.map(node => ({ x: node.getAttribute('cx'), y: node.getAttribute('cy'), r: +node.getAttribute('r') })));
      assert.deepEqual(circles.map(circle => circle.r), [22, 7]);
      assert.equal(circles[0].x, circles[1].x); assert.equal(circles[0].y, circles[1].y);
      const pill = await composer().getByRole('group', { name: 'Room lighting tool', exact: true }).boundingBox();
      const hub = await composer().locator('svg circle').last().boundingBox();
      assert.ok(pill && hub && pill.y + pill.height < hub.y - 22, 'mode pill clears the fixture reticle above the cylinder');
      assert.match(await composer().innerText(), /Modeled orientation · not commissioned/);
      assert.equal((await inventory()).fixture_count, 4); return { default: 'spot', actual_hub_only: true, fixtures: 4 };
    });
    await check('existing native Spot drag still creates an aim and enables its existing Undo', async () => {
      const start = await canvasPoint('fixture', fixture.fixture_id), end = await canvasPoint('surface', null, start); assert.ok(start && end);
      await page.mouse.move(start.x, start.y); await page.mouse.down(); await page.mouse.move(end.x, end.y, { steps: 8 }); await page.mouse.up();
      await expectSelected(fixture); await inspector().getByRole('button', { name: 'Undo aim', exact: true }).waitFor();
      assert.equal(await inspector().getByRole('button', { name: 'Undo aim', exact: true }).isDisabled(), false);
      const actual = await descriptor(fixture.fixture_id); assert.ok(actual.target); assert.notDeepEqual(actual.target, originalAim.target);
      return { native_drag: true, target_present: true, undo_enabled: true };
    });
    await check('all six modeled ring zones support native keyboard selection and isolated brightness/temperature controls', async () => {
      await inspector().getByRole('tab', { name: 'Light', exact: true }).click(); await expectMode('zones');
      const ring = inspector().getByRole('group', { name: 'Radial zones', exact: true });
      for (let number = 1; number <= 6; number++) {
        const button = ring.getByRole('button', { name: 'Radial zone ' + number, exact: true }); await button.focus();
        await page.keyboard.press(number % 2 ? 'Enter' : 'Space'); assert.equal(await button.getAttribute('aria-pressed'), 'true');
      }
      const first = ring.getByRole('button', { name: 'Radial zone 1', exact: true }); await first.focus(); await page.keyboard.press('Enter');
      const before = await descriptor(fixture.fixture_id);
      const radialChannel = inspector().getByRole('region', { name: 'Radial channel', exact: true });
      await radialChannel.getByRole('button', { name: 'Off', exact: true }).click();
      let after = await descriptor(fixture.fixture_id); assert.equal(after.state.radials[0].on, false);
      assert.equal(after.state.radials[0].brightness, before.state.radials[0].brightness);
      const brightness = inspector().getByRole('slider', { name: 'Radial brightness', exact: true }); await brightness.focus(); await page.keyboard.press('ArrowRight');
      const temperature = inspector().getByRole('slider', { name: 'Radial color temperature', exact: true }); await temperature.focus(); await page.keyboard.press('ArrowRight');
      after = await descriptor(fixture.fixture_id);
      assert.equal(after.state.radials[0].brightness, before.state.radials[0].brightness + .01); assert.equal(after.state.radials[0].on, true);
      assert.equal(after.state.radials[0].color_temp_kelvin, before.state.radials[0].color_temp_kelvin + 100);
      assert.deepEqual(after.state.radials.slice(1), before.state.radials.slice(1)); assert.deepEqual(withoutRadials(after), withoutRadials(before));
      await page.keyboard.press('z'); await expectMode('zones'); await page.keyboard.press('a'); await expectMode('zones');
      return { selected_with_enter_and_space: [1, 2, 3, 4, 5, 6], only_zone: 1, off_kept_saved_level: true, focused_field_owns_a_z: true };
    });
    await check('Show in room and unfocused A/Z use native Zones/Spot modes on the actual projected fixture plane', async () => {
      await inspector().getByRole('tab', { name: 'Aim', exact: true }).click(); await expectMode('spot');
      await inspector().getByRole('tab', { name: 'Light', exact: true }).click(); await expectMode('zones');
      await inspector().getByRole('button', { name: 'Show in room', exact: true }).click(); await expectMode('zones');
      const geometry = await planePoint(fixture.fixture_id, 1, .7);
      const fade = await verifyLobeFades();
      await setMode('spot'); assert.equal(await inspector().getByRole('tab', { name: 'Aim', exact: true }).getAttribute('aria-selected'), 'true');
      await setMode('zones'); assert.equal(await inspector().getByRole('tab', { name: 'Light', exact: true }).getAttribute('aria-selected'), 'true');
      await blur(); await page.keyboard.press('a'); await expectMode('spot'); await page.keyboard.press('z'); await expectMode('zones');
      return { native_tab_modes: ['Aim:spot', 'Light:zones'], svg_hub: geometry.actualHub, svg_tick: geometry.actualTick, renderer_projection_agrees: true, fade, shortcuts: ['a', 'z'] };
    });
    await check('native hub pickup survives ambient rig settling without a jump; outward, inward and cross-zone motion only paints modeled radials', async () => {
      const before = await descriptor(fixture.fixture_id), callsBefore = (await writes()).length;
      await startHub(fixture.fixture_id);
      await composer().getByRole('group', { name: 'Room lighting tool', exact: true }).waitFor({ state: 'detached' });
      const settlingBefore = await page.evaluate(() => ({ matrix: window.__APT_ENGINE.camera.matrixWorld.toArray(), pivot: window.__APT_ENGINE.rig.snapshotPose().pivot }));
      const navigationBefore = await navigationPose(); await waitHUD();
      const settlingAfter = await page.evaluate(() => ({ matrix: window.__APT_ENGINE.camera.matrixWorld.toArray(), pivot: window.__APT_ENGINE.rig.snapshotPose().pivot }));
      assert.notDeepEqual(settlingAfter.pivot, settlingBefore.pivot, 'the actual rig ambient pivot is still settling during pickup');
      assert.notDeepEqual(settlingAfter.matrix, settlingBefore.matrix, 'actual rendered camera movement witnesses the ambient case');
      assert.deepEqual(await navigationPose(), navigationBefore); assert.equal(await captureActive(), true, 'ambient settling does not retire the radial owner');
      assert.deepEqual(await descriptor(fixture.fixture_id), before);
      const outward = await planePoint(fixture.fixture_id, 1, 1.184); await page.mouse.move(outward.client.x, outward.client.y, { steps: 4 }); await waitHUD();
      assert.equal((await descriptor(fixture.fixture_id)).state.radials[0].brightness, .9);
      assert.equal(await composer().locator('[data-apt-radial-start="zone:1"]').locator('..').locator('circle').getAttribute('fill'), '#fff',
        'only the active paint node becomes white while its owner is active');
      const inactiveNodeColors = await composer().locator('[data-apt-radial-start^="zone:"]').evaluateAll(paths => paths
        .filter(path => path.getAttribute('data-apt-radial-start') !== 'zone:1')
        .map(path => path.parentElement.querySelector('circle')?.getAttribute('fill')));
      assert.equal(inactiveNodeColors.length, 5); assert.ok(inactiveNodeColors.every(color => color && color !== '#fff'), 'the other five nodes retain their temperature cue');
      const inward = await planePoint(fixture.fixture_id, 1, .546); await page.mouse.move(inward.client.x, inward.client.y, { steps: 4 }); await waitHUD();
      assert.equal((await descriptor(fixture.fixture_id)).state.radials[0].brightness, .35);
      const cross = await planePoint(fixture.fixture_id, 2, 1.068); await page.mouse.move(cross.client.x, cross.client.y, { steps: 8 }); await page.mouse.up();
      const after = await descriptor(fixture.fixture_id); assert.equal(after.state.radials[1].brightness, .8);
      await composer().getByRole('group', { name: 'Room lighting tool', exact: true }).waitFor();
      assert.notEqual(await composer().locator('[data-apt-radial-start="zone:1"]').locator('..').locator('circle').getAttribute('fill'), '#fff');
      assert.deepEqual(withoutRadials(after), withoutRadials(before));
      assert.deepEqual(after.state.radials.map(zone => zone.color_temp_kelvin), before.state.radials.map(zone => zone.color_temp_kelvin));
      const calls = (await writes()).slice(callsBefore); assert.ok(paintWrites(calls).length > 0);
      for (const row of calls) { assert.equal(row.method, 'setVirtualFixtureState'); assert.deepEqual(Object.keys(row.patch), ['radials']);
        for (const zone of row.patch.radials) assert.deepEqual(Object.keys(zone).sort(), ['brightness', 'number', 'on']); }
      await inspector().getByRole('tab', { name: 'Aim', exact: true }).click();
      assert.equal(await inspector().getByRole('button', { name: 'Undo aim', exact: true }).isDisabled(), false);
      assert.equal(await inspector().getByRole('button', { name: 'Redo aim', exact: true }).isDisabled(), true);
      await inspector().getByRole('tab', { name: 'Light', exact: true }).click();
      assert.deepEqual((await inventory()).fixtures.filter(item => item.fixture_id !== fixture.fixture_id), initialInventory.fixtures.filter(item => item.fixture_id !== fixture.fixture_id));
      return { pickup_exact: true, ambient_camera_movement_observed: true, mode_pill_hidden_during_owned_drag: true, outward: .9, inward: .35, cross_zone: 2, cross_level: .8, only_radials_written: true, aim_target_and_history_controls_preserved: true };
    });
    await check('native empty-space orbit and another fixture selection retain their existing behavior in Zones', async () => {
      const empty = await canvasPoint('empty'); assert.ok(empty);
      const before = await page.evaluate(() => ({ matrix: window.__APT_ENGINE.camera.matrixWorld.toArray(), azimuth: window.__APT_ENGINE.rig.azimuthIndex() }));
      await page.mouse.move(empty.x, empty.y); await page.mouse.down(); await page.mouse.move(empty.x + 35, empty.y + 8, { steps: 8 }); await page.mouse.up();
      await page.waitForFunction(before => window.__APT_ENGINE.rig.azimuthIndex() !== before.azimuth
        && window.__APT_ENGINE.camera.matrixWorld.toArray().some((value, index) => value !== before.matrix[index]), before);
      await expectSelected(fixture);
      await page.evaluate(() => window.__APT_ENGINE.rig.snapHome(0)); await waitHUD();
      const other = await canvasPoint('fixture', fixtures[1].fixture_id); assert.ok(other);
      await page.mouse.click(other.x, other.y); await expectSelected(fixtures[1]);
      const original = await canvasPoint('fixture', fixture.fixture_id); assert.ok(original);
      await page.mouse.click(original.x, original.y); await expectSelected(fixture); await expectMode('zones');
      return { empty_space_orbits: true, native_fixture_selection: fixtures[1].fixture_id };
    });
    // Native input/IPC may span the existing 80ms flush. These cases prove
    // active-gesture retirement; deterministic pending-drop proof is in the
    // actual-source integration suite, not inferred from this event sequence.
    const activeMotion = async () => {
      await startHub(fixture.fixture_id); const point = await planePoint(fixture.fixture_id, 1, 1.5);
      await page.mouse.move(point.client.x, point.client.y); return point;
    };
    await check('Escape and a second native pointer retire active painting without later modeled calls', async () => {
      await activeMotion(); await page.keyboard.press('Escape'); assert.equal(await captureActive(), false);
      const escape = await page.evaluate(() => [...window.__directionalNative].reverse().find(event => event.type === 'keydown' && event.key === 'Escape'));
      assert.ok(escape?.isTrusted); await unchangedAfterRetirement(escape); await expectSelected(fixture);
      await activeMotion(); const cdp = await context.newCDPSession(page), second = await hubPoint(fixture.fixture_id);
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: second.x, y: second.y, id: 7 }] });
      assert.equal(await captureActive(), false);
      const touch = await page.evaluate(() => [...window.__directionalNative].reverse().find(event => event.type === 'pointerdown' && event.pointerType === 'touch'));
      assert.ok(touch?.isTrusted); await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); await cdp.detach();
      await unchangedAfterRetirement(touch); await expectSelected(fixture);
      return { trusted_escape: true, trusted_second_pointer: true, late_modeled_calls: 0, unflushed_queue_not_inferred: true };
    });
    await check('native wheel cancels painting while retaining the existing zoom action', async () => {
      const before = await page.evaluate(() => window.__APT_ENGINE.rig.zoomIndex()); await activeMotion();
      await page.mouse.wheel(0, -180); const marker = await nativeMarker('wheel');
      assert.equal(await captureActive(), false); await unchangedAfterRetirement(marker);
      await page.waitForFunction(before => window.__APT_ENGINE.rig.zoomIndex() !== before, before);
      await page.evaluate(() => window.__APT_ENGINE.rig.snapHome(0)); await waitHUD();
      return { paint_canceled: true, zoom_preserved: true };
    });
    await check('hiding the native fixture layer retires paint before a later pointer release', async () => {
      await activeMotion(); await page.getByRole('button', { name: 'Layers', exact: true }).focus(); await page.keyboard.press('Space');
      const dialog = page.getByRole('dialog', { name: 'Room layers', exact: true }), checkbox = dialog.getByRole('checkbox', { name: 'Show virtual spotlights layer', exact: true });
      await checkbox.focus(); await page.keyboard.press('Space'); await page.waitForFunction(() => window.__APT_ENGINE.overlay.virtualFixturesReadback().visible === false);
      const marker = await nativeMarker('click', 'Show virtual spotlights layer'); await composer().waitFor({ state: 'detached' }); const reconciliation = await unchangedAfterRetirement(marker, true);
      await checkbox.focus(); await page.keyboard.press('Space'); await dialog.getByRole('button', { name: 'Close layers', exact: true }).click();
      await page.waitForFunction(() => window.__APT_ENGINE.overlay.virtualFixturesReadback().visible === true); await expectMode('zones');
      return { hidden_retired: true, ...reconciliation, restored_display_on: true };
    });
    await check('Setup retires active painting with only the exact saved-state reconciliation and no save', async () => {
      await activeMotion(); await page.locator('.apt-home-setup').focus(); await page.keyboard.press('Space');
      await page.locator('[data-apt-edit-ui="toolbar"]').waitFor(); const marker = await nativeMarker('click', 'Setup');
      await composer().waitFor({ state: 'detached' }); const reconciliation = await unchangedAfterRetirement(marker, true);
      await page.locator('[data-apt-edit-ui="toolbar"]').getByRole('button', { name: 'done', exact: true }).click();
      // Returning from Setup must retain the chosen Zones tool; an old aim
      // notice replayed on inspector remount is not a new mode-selection event.
      await expectSelected(fixture); await expectMode('zones'); return { setup_retired: true, ...reconciliation, saves: 0 };
    });
    await check('the existing Spot Undo still restores its original aim after radial editing', async () => {
      await setMode('spot'); await inspector().getByRole('button', { name: 'Undo aim', exact: true }).click();
      const actual = await descriptor(fixture.fixture_id); assert.deepEqual(actual.target, originalAim.target);
      assert.equal(actual.state.pan_deg, originalAim.state.pan_deg); assert.equal(actual.state.tilt_deg, originalAim.state.tilt_deg);
      await setMode('zones'); return { original_pan_tilt_target_restored: true };
    });
    await check('a native home change retires active painting and preserves the exact authored saves', async () => {
      await activeMotion(); const echoButton = page.getByRole('group', { name: 'Home', exact: true }).getByRole('button', { name: 'Echo Park', exact: true });
      await echoButton.focus(); await page.keyboard.press('Space'); await waitSite('echo'); const marker = await nativeMarker('click', 'Echo Park');
      await composer().waitFor({ state: 'detached' }); await unchangedAfterRetirement(marker);
      assert.equal(await inspector().count(), 0); await selectHome('victoria'); await expectSelected(fixture); await expectMode('zones');
      assert.equal((await inventory()).visible, true); assert.equal((await inventory()).fixture_count, 4);
      return { home_retired: true, late_modeled_calls: 0, fixture_display_on: true, fixtures: 4 };
    });
    receipt.native_events = await page.evaluate(() => window.__directionalNative);
    receipt.modeled_calls = await writes();
    receipt.reconciliations = await page.evaluate(() => window.__directionalReconciliations);
    for (const [file, expected] of Object.entries(protectedHashes)) assert.equal(hash(fs.readFileSync(file)), expected, 'authored pack and registration bytes stay exact');
    for (const [file, expected] of Object.entries(receipt.source_sha256)) assert.equal(receipt.served[file]?.sha256, expected, 'served production source matches the captured candidate');
    assert.deepEqual(receipt.forbidden, []); assert.deepEqual(receipt.missing_code, []); assert.deepEqual(receipt.errors, []);
    assert.equal(receipt.requests.some(request => !['GET', 'HEAD'].includes(request.method)), false);
    receipt.authored_data_hashes_unchanged = true; receipt.fixture_display_on = true;
  }
  try { await Promise.race([proof(), deadline]); receipt.result = 'passed'; }
  catch (error) {
    receipt.result = 'failed'; receipt.failure = error.stack || String(error); save();
    if (page && !page.isClosed()) {
      const observed = await page.evaluate(() => ({ calls: window.__directionalCalls || [],
        reconciliations: window.__directionalReconciliations || [], native: window.__directionalNative || [] })).catch(() => null);
      if (observed) { receipt.modeled_calls = observed.calls; receipt.reconciliations = observed.reconciliations; receipt.native_events = observed.native; }
      save();
    }
    if (page && !page.isClosed()) receipt.failure_state = await page.evaluate(() => ({
      selected: (() => { const ids = []; window.__APT_ENGINE?.overlay.virtualFixturesGroup?.traverse(node => {
        if (node.name === 'virtual-fixture-aim-handle' && node.visible) ids.push(node.userData.fixture_id);
      }); return ids; })(), inspector: document.querySelector('[data-victoria-virtual-fixtures="1"]')?.getAttribute('aria-label'),
      events: window.__directionalNative?.slice(-20), calls: window.__directionalCalls?.slice(-20),
      composer: document.querySelector('[data-apt-directional-composer="1"]')?.outerHTML?.slice(0, 2000),
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
