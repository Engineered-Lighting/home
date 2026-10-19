// Authored-room browser proof for the renderer's fit and camera gestures
// (APT-S04 done[0], APT-R01 pan, SPEC Layout > Fitting and Gestures).
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
// SPEC Layout and responsive rules: base column width by container width.
const DESKTOP = [{ width: 1920, height: 1080, column: 360 }, { width: 1366, height: 768, column: 340 }, { width: 1024, height: 768, column: 320 }];

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

test('renderer fits the visible canvas at 1920, 1366 and 1024, pans, and keeps the owner pose through layout changes', { timeout: 180000 }, async () => {
  assert.ok(!process.env.HOME_APARTMENT_CANDIDATE_ROOT, 'this proof uses only current worktree source');
  const run = path.join(ROOT, '.tmp/apartment-rig-fit-browser', 'run-' + Date.now()); fs.mkdirSync(run, { recursive: true });
  const packs = path.join(run, 'packs'), privateRoot = path.join(run, 'private');
  const echo = installPack(packs, 'echo'), victoria = installPack(packs, 'victoria');
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1,
    packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  const protectedFiles = [echo, victoria].flatMap(item => Object.keys(item.files).map(name => path.join(packs, item.site, item.revision, name)));
  const protectedHashes = Object.fromEntries(protectedFiles.map(file => [file, hash(fs.readFileSync(file))]));
  const sources = publicSources(SRC), receipt = { schema: 'home-apartment-rig-fit-authored-browser-1', authored_room: true, real_renderer: true,
    household_data: false, physical_commands: 0, screenshots: false, checks: [], forbidden: [], missing_code: [], errors: [], served: {} };
  const check = (stage, detail = {}) => receipt.checks.push({ stage, ...detail });
  let server, browser, page, origin, handler, timer;
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => {
    browser?.close().catch(() => {}); server?.closeAllConnections(); reject(Error('120s rig fit browser deadline'));
  }, 120000); });

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
    const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, serviceWorkers: 'block' });
    await context.addCookies([{ name: 'authored_owner_session', value: '1', url: origin }]);
    await context.addInitScript(() => {
      localStorage.setItem('apartment3d.site', 'echo'); localStorage.setItem('home.perf.backgroundWarmup', 'off'); window.HG_DISABLE_SERVICE_WORKER = true;
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

    const frames = (n = 3) => page.evaluate(n => new Promise(resolve => { let k = 0; const step = () => (++k >= n ? resolve() : requestAnimationFrame(step)); requestAnimationFrame(step); }), n);
    const settle = async () => { await frames(3); await page.waitForFunction(() => !window.__APT_ENGINE.rig.isTweening(), null, { timeout: 5000 }); await frames(4); };
    const layout = () => page.evaluate(() => {
      const engine = window.__APT_ENGINE, fit = engine.debugFit(), rect = selector => document.querySelector(selector)?.getBoundingClientRect().toJSON() || null;
      const t = engine.rig.state.target;
      return { fit, stage: rect('.apt-home-stage'), panel: rect('.apt-home-panel'), toolbar: rect('.apt-ui-room-toolbar'),
        canvas: engine.renderer.domElement.getBoundingClientRect().toJSON(), zoom: engine.rig.zoomIndex(), az: engine.rig.azimuthIndex(),
        radius: engine.rig.currentRadius(), fitDistance: engine.rig.state.fitDistance, target: [t.x, t.y, t.z], ownerPose: engine.rig.ownerPose() };
    });
    const sameVec = (a, b, eps = 1e-6) => a.every((value, index) => Math.abs(value - b[index]) <= eps);

    await page.goto(origin + '/?apartmentRedesign=1&worldZoom=off', { waitUntil: 'domcontentloaded' });
    await page.locator('.apt-home-shell[data-home-layout="unified"]').waitFor({ timeout: 45000 });
    await page.waitForFunction(() => window.__APT_ENGINE?.siteId === 'echo' && window.__APT_ENGINE.debugFit?.()
      && document.querySelector('.apt-ui-room-toolbar') && window.__APT_ENGINE.viewInsets?.().bottom > 0, null, { timeout: 45000 });

    // S04 done[0]: column width and room fit at the three desktop widths.
    for (const viewport of DESKTOP) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.evaluate(() => window.__APT_ENGINE.refit({ dur: 0 }));
      await page.waitForFunction(width => Math.abs(window.__APT_ENGINE.renderer.domElement.getBoundingClientRect().width
        - (document.querySelector('.apt-home-stage')?.getBoundingClientRect().width || -1)) < 1
        && document.querySelector('.apt-home-stage').getBoundingClientRect().right <= width, viewport.width);
      await settle();
      const state = await layout();
      assert.equal(Math.round(state.panel.width), viewport.column, viewport.width + ': column has the designed width');
      assert.ok(Math.abs(state.stage.right - state.panel.left) <= 1, viewport.width + ': the room canvas ends where the column starts');
      const expectedBottom = Math.round(state.stage.bottom - state.toolbar.top + 8);
      assert.ok(Math.abs(state.fit.insets.bottom - expectedBottom) <= 1, viewport.width + ': fit subtracts the toolbar inset '
        + JSON.stringify({ insets: state.fit.insets, expectedBottom }));
      assert.equal(state.fit.visibleOk, true, viewport.width + ': the fitted room lies inside the visible canvas ' + JSON.stringify(state.fit));
      assert.ok(state.fit.bounds.maxY <= state.toolbar.top - state.stage.top + 1, viewport.width + ': the fitted room clears the toolbar');
      assert.equal(state.zoom, 0); assert.equal(state.ownerPose, false);
      check('fit_' + viewport.width + 'x' + viewport.height, { column: state.panel.width, insets: state.fit.insets, bounds: state.fit.bounds, visible: state.fit.visible });
    }

    // Before the owner navigates, a layout change refits.
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.evaluate(() => window.__APT_ENGINE.rig.snapHome(0)); await settle();
    const fresh = await layout();
    await page.setViewportSize({ width: 1300, height: 700 }); await settle();
    const refitted = await layout();
    assert.equal(refitted.ownerPose, false);
    assert.notEqual(refitted.fitDistance, fresh.fitDistance, 'a resize before any owner navigation refits');
    assert.ok(Math.abs(refitted.radius - refitted.fitDistance) < 1e-6, 'the refit lands on the new overview radius');
    check('resize_refits_before_owner_navigation', { before: fresh.fitDistance, after: refitted.fitDistance });

    // The owner zooms and pans; layout changes then keep the pose.
    const box = await page.locator('canvas').first().boundingBox();
    const cx = box.x + box.width * .5, cy = box.y + box.height * .45;
    await page.mouse.move(cx, cy); await page.mouse.wheel(0, -120); await settle();
    const zoomed = await layout();
    assert.equal(zoomed.zoom, 1, 'a wheel notch zooms one detent'); assert.equal(zoomed.ownerPose, true);
    await page.keyboard.down('Shift');
    await page.mouse.move(cx, cy); await page.mouse.down(); await page.mouse.move(cx - 90, cy - 30, { steps: 6 }); await page.mouse.up();
    await page.keyboard.up('Shift'); await settle();
    const shiftPanned = await layout();
    assert.equal(shiftPanned.az, zoomed.az, 'Shift-drag does not orbit'); assert.equal(shiftPanned.zoom, 1);
    assert.ok(!sameVec(shiftPanned.target, zoomed.target, 1e-3), 'Shift-drag pans the orbit centre');
    assert.ok(Math.abs(shiftPanned.target[1] - zoomed.target[1]) < 1e-6, 'pan stays on the floor plane');
    await page.mouse.move(cx, cy); await page.mouse.down({ button: 'right' }); await page.mouse.move(cx + 70, cy, { steps: 6 }); await page.mouse.up({ button: 'right' });
    await settle();
    const rightPanned = await layout();
    assert.equal(rightPanned.az, zoomed.az, 'right-drag does not orbit');
    assert.ok(!sameVec(rightPanned.target, shiftPanned.target, 1e-3), 'right-drag pans the orbit centre');
    assert.equal(await page.locator('[aria-label="Fixture lighting inspector"]').count(), 0, 'pan gestures never select');
    check('shift_and_right_drag_pan', { zoomed: zoomed.target, shift: shiftPanned.target, right: rightPanned.target });

    for (const [width, height] of [[1366, 768], [1024, 768], [1920, 1080]]) {
      await page.setViewportSize({ width, height }); await settle();
      const kept = await layout();
      assert.equal(kept.zoom, rightPanned.zoom, width + ': resize after owner zoom keeps the detent');
      assert.ok(Math.abs(kept.radius - rightPanned.radius) < 1e-6, width + ': resize after owner zoom keeps the radius');
      assert.ok(sameVec(kept.target, rightPanned.target), width + ': resize after owner pan keeps the centre');
      assert.equal(kept.az, rightPanned.az);
      check('no_refit_after_owner_pose_' + width + 'x' + height, { radius: kept.radius, fitDistance: kept.fitDistance });
    }

    // The view offset moves the projection centre without rescaling the room.
    const offset = await page.evaluate(async () => {
      const engine = window.__APT_ENGINE, THREE = engine.camera.position.constructor, previous = engine.viewInsets();
      const sample = () => {
        engine.camera.updateMatrixWorld(true);
        const t = engine.rig.state.target, a = engine.picking.projectToScreen(t.clone()),
          b = engine.picking.projectToScreen(t.clone().add(new THREE(1, 0, 0)));
        return { a, span: Math.hypot(b.x - a.x, b.y - a.y) };
      };
      const before = sample();
      engine.setViewInsets({ ...previous, bottom: previous.bottom + 200 });
      const after = sample(), target = engine.rig.state.target.toArray(), zoom = engine.rig.zoomIndex();
      engine.setViewInsets(previous);
      return { before, after, target, zoom, restored: engine.viewInsets() };
    });
    assert.ok(Math.abs(offset.after.span - offset.before.span) < .5, 'an inset change keeps one metre the same size on screen ' + JSON.stringify(offset));
    assert.ok(Math.abs((offset.before.a.y - offset.after.a.y) - 100) < 1, 'a 200 px bottom inset lifts the centre by 100 px ' + JSON.stringify(offset));
    assert.ok(sameVec(offset.target, rightPanned.target) && offset.zoom === rightPanned.zoom, 'an inset change after owner navigation keeps the pose');
    check('view_offset_shifts_without_rescaling', offset);

    // An explicit fit returns to the overview and re-arms layout refits.
    await page.evaluate(() => window.__APT_ENGINE.refit({ dur: 0 })); await settle();
    const refit = await layout();
    assert.equal(refit.zoom, 0); assert.equal(refit.ownerPose, false); assert.equal(refit.fit.visibleOk, true);
    check('explicit_fit_clears_owner_pose', { visible: refit.fit.visible, bounds: refit.fit.bounds });

    assert.deepEqual(Object.fromEntries(protectedFiles.map(file => [file, hash(fs.readFileSync(file))])), protectedHashes, 'authored pack bytes unchanged');
    assert.deepEqual(receipt.forbidden, []); assert.deepEqual(receipt.errors, []); assert.deepEqual(receipt.missing_code, []);
    for (const [relative, digest] of Object.entries(receipt.served)) assert.equal(digest, hash(fs.readFileSync(path.join(SRC, relative))), 'served code stayed stable: ' + relative);
    receipt.ok = true;
    fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    process.stdout.write('Authored rig fit browser evidence: ' + path.join(run, 'receipt.json') + '\n');
  }
  try { await Promise.race([proof(), deadline]); }
  catch (error) {
    receipt.failure = error.message;
    fs.writeFileSync(path.join(run, 'failure-receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    throw new Error(error.message + '\nRig fit failure evidence: ' + path.join(run, 'failure-receipt.json'), { cause: error });
  } finally {
    clearTimeout(timer); await browser?.close();
    if (server) await new Promise(resolve => { server.closeAllConnections(); server.close(resolve); });
  }
});
