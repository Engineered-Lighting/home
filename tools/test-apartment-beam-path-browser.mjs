// Authored-room browser proof for engine.beamPath / engine.traceBeam on the
// real collision proxy (APT-A01: "Blocked by ..." and the grazing note).
// Real renderer and shell over the authored fixture packs; no household pack,
// live camera, model session, physical command, screenshot or deployment.
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

function browserOptions() {
  const installed = fs.existsSync(chromium.executablePath());
  assert.ok(installed || process.platform === 'win32', 'pinned Chromium must exist on Linux');
  const args = ['--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'];
  if (process.platform === 'win32') args.unshift('--use-angle=d3d11');
  else if (process.platform === 'linux') args.unshift('--use-angle=swiftshader', '--enable-unsafe-swiftshader');
  return { headless: true, args, ...(process.platform === 'linux' ? { channel: 'chromium' } : !installed ? { channel: 'msedge' } : {}) };
}

test('the engine traces real reticle picks for the aim chip: clear paths end on the surface, blocked paths name a blocker', { timeout: 145000 }, async () => {
  assert.ok(!process.env.HOME_APARTMENT_CANDIDATE_ROOT, 'this proof uses only current worktree source');
  const run = path.join(ROOT, '.tmp/apartment-beam-path-browser', 'run-' + Date.now()); fs.mkdirSync(run, { recursive: true });
  const packs = path.join(run, 'packs'), privateRoot = path.join(run, 'private');
  const echo = installPack(packs, 'echo'), victoria = installPack(packs, 'victoria');
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1,
    packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  const protectedFiles = [echo, victoria].flatMap(item => Object.keys(item.files).map(name => path.join(packs, item.site, item.revision, name)));
  const protectedHashes = Object.fromEntries(protectedFiles.map(file => [file, hash(fs.readFileSync(file))]));
  const sources = publicSources(SRC), receipt = { schema: 'home-apartment-beam-path-authored-browser-1', authored_room: true, real_renderer: true,
    household_data: false, physical_commands: 0, screenshots: false, checks: [], forbidden: [], missing_code: [], errors: [], served: {} };
  const check = (stage, detail = {}) => receipt.checks.push({ stage, ...detail });
  let server, browser, page, origin, handler, timer;
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => {
    browser?.close().catch(() => {}); server?.closeAllConnections(); reject(Error('90s beam path browser deadline'));
  }, 90000); });

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
    browser = await chromium.launch(browserOptions());
    const context = await browser.newContext({ viewport: { width: 1366, height: 768 }, serviceWorkers: 'block' });
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
    page = await context.newPage(); page.setDefaultTimeout(15000);
    page.on('pageerror', error => receipt.errors.push(error.message));

    await page.goto(origin + '/?apartmentRedesign=1&worldZoom=off', { waitUntil: 'domcontentloaded' });
    await page.locator('.apt-home-shell[data-home-layout="unified"]').waitFor({ timeout: 45000 });
    await page.waitForFunction(() => window.__APT_ENGINE?.siteId === 'victoria'
      && window.__APT_ENGINE.overlay.virtualFixturesReadback().fixtures.length === 4, null, { timeout: 45000 });
    await page.evaluate(() => window.__APT_ENGINE.modes.preload(['collision']));
    await page.waitForFunction(() => !!window.__APT_ENGINE.modes.getCollision(), null, { timeout: 30000 });

    const result = await page.evaluate(() => {
      const engine = window.__APT_ENGINE, readback = engine.overlay.virtualFixturesReadback();
      const rect = engine.renderer.domElement.getBoundingClientRect(), rows = [];
      const sub = (a, b) => a.map((v, i) => v - b[i]), len = v => Math.hypot(...v), add = (a, b, k) => a.map((v, i) => v + b[i] * k);
      for (const fixture of readback.fixtures) {
        const origin = engine.overlay.virtualFixtureInteractionDescriptor(fixture.fixture_id).display_origin_m;
        // Real reticle picks across the canvas: what the aim chip will trace to.
        for (const [fx, fy] of [[.5, .55], [.3, .7], [.7, .7], [.5, .8]]) {
          const pick = engine.displaySurfaceHit(rect.left + rect.width * fx, rect.top + rect.height * fy);
          if (!pick) continue;
          const path = engine.beamPath(origin, pick.point), chip = engine.traceBeam(origin, pick.point);
          const span = sub(pick.point, origin), beyond = add(origin, span, 1.6), past = engine.beamPath(origin, beyond), pastChip = engine.traceBeam(origin, beyond);
          rows.push({ fixture: fixture.fixture_id, origin, target: pick.point, path: path && { clear: path.clear, hit: path.hit && { point_m: path.hit.point_m, normal_m: path.hit.normal_m,
            distance_m: path.hit.distance_m, surface_class: path.hit.surface_class }, blocker: path.blocker && { point_m: path.blocker.point_m, name: path.blocker.name,
            distance_m: path.blocker.distance_m }, target_distance_m: path.target_distance_m, incidence_deg: path.incidence_deg, grazing: path.grazing, frozen: Object.isFrozen(path) },
            chip, pastChip, past: past && { clear: past.clear, blocker: past.blocker && { point_m: past.blocker.point_m, distance_m: past.blocker.distance_m, name: past.blocker.name } },
            span_m: len(span), towardLight: path?.hit ? path.hit.normal_m.reduce((s, v, i) => s + v * span[i], 0) <= 1e-9 : null });
        }
      }
      return { rows, nullInput: engine.beamPath([0, 0], [1, 1, 1]), nullChip: engine.traceBeam([0, 0, 0], [0, 0, 0]) };
    });
    assert.ok(result.rows.length >= 4, 'enough real reticle picks were traced: ' + result.rows.length);
    for (const row of result.rows) {
      assert.ok(row.path && row.path.frozen, 'a traced pick returns a frozen path');
      assert.ok(Math.abs(row.path.target_distance_m - row.span_m) < 1e-9);
      assert.equal(row.towardLight, true, 'the reported normal faces the light');
      if (row.path.clear) {
        assert.equal(row.chip, null, 'a clear path gives the aim chip nothing to call blocked');
        assert.ok(row.path.hit && Math.hypot(...row.path.hit.point_m.map((v, i) => v - row.target[i])) < .05, 'a clear path ends on the picked surface');
        assert.ok(row.past && !row.past.clear && Math.abs(row.past.blocker.distance_m - row.path.hit.distance_m) < .05,
          'aiming past the picked surface is blocked by that surface');
        assert.ok(row.pastChip && typeof row.pastChip.name === 'string' && row.pastChip.name.length > 0
          && JSON.stringify(row.pastChip.point_m) === JSON.stringify(row.past.blocker.point_m), 'the aim chip gets that blocker point and name');
        assert.equal(typeof row.path.incidence_deg, 'number'); assert.equal(row.path.grazing, row.path.incidence_deg > 80);
      } else {
        assert.ok(row.chip && Array.isArray(row.chip.point_m) && typeof row.chip.name === 'string' && row.chip.name.length > 0,
          'a blocked path gives the aim chip a point and a name');
        assert.deepEqual(row.chip.point_m, row.path.blocker.point_m);
        assert.ok(row.path.blocker.distance_m < row.path.target_distance_m - .03);
        assert.equal(row.path.incidence_deg, null);
      }
    }
    assert.equal(result.nullInput, null); assert.equal(result.nullChip, null);
    check('engine_beam_path_on_real_collision', { traced: result.rows.length, clear: result.rows.filter(row => row.path.clear).length,
      blocked: result.rows.filter(row => !row.path.clear).length, sample: result.rows.slice(0, 3) });

    assert.deepEqual(Object.fromEntries(protectedFiles.map(file => [file, hash(fs.readFileSync(file))])), protectedHashes, 'authored pack bytes unchanged');
    assert.deepEqual(receipt.forbidden, []); assert.deepEqual(receipt.errors, []); assert.deepEqual(receipt.missing_code, []);
    for (const [relative, digest] of Object.entries(receipt.served)) assert.equal(digest, hash(fs.readFileSync(path.join(SRC, relative))), 'served code stayed stable: ' + relative);
    receipt.ok = true;
    fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    process.stdout.write('Authored beam path browser evidence: ' + path.join(run, 'receipt.json') + '\n');
  }
  try { await Promise.race([proof(), deadline]); }
  catch (error) {
    receipt.failure = error.message;
    fs.writeFileSync(path.join(run, 'failure-receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    throw new Error(error.message + '\nBeam path failure evidence: ' + path.join(run, 'failure-receipt.json'), { cause: error });
  } finally {
    clearTimeout(timer); await browser?.close();
    if (server) await new Promise(resolve => { server.closeAllConnections(); server.close(resolve); });
  }
});
