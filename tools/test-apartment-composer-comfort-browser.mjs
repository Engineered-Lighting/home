// Composer comfort (APT-Z02): real Home shell, renderer and trusted native
// mouse and touch input on the authored room. No household assets, saves,
// sessions, service writes or private preview evidence.
// Deadlines are fixed before the first run and never raised to pass:
// 145 s for the whole proof, 25 s warm-up to a ready Victoria fixture
// inventory, 15 s per readiness wait, 180 s for the node:test file.
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
const PROOF_DEADLINE_MS = 145000, WARMUP_MS = 25000, WAIT_MS = 15000;
const R0 = .14, R1 = 1.3, MARGIN = .12, HUB_PX = 34, OLD_HUB_PX = 22;
// Inside every lit lobe yet clear of the 34 px hub at the home camera pose.
const LOBE_START_M = .55;
const levelAt = radius => Math.round(Math.max(0, Math.min(1, (radius - R0) / (R1 - R0))) * 100) / 100;
const radiusFor = level => R0 + level * (R1 - R0);
// Levels are whole percents; compare in steps so float rounding never decides.
const withinSteps = (level, target, steps = 1) => Math.abs(Math.round(level * 100) - Math.round(target * 100)) <= steps;
const hash = bytes => createHash('sha256').update(bytes).digest('hex');

// The same public-code fence as the directional and shell proofs.
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

// Page-side observation only: trusted pointer events, modeled-producer calls
// and the composer readout. No application callback or event is dispatched.
function installObservers() {
  localStorage.setItem('apartment3d.site', 'echo'); localStorage.setItem('home.perf.backgroundWarmup', 'off'); window.HG_DISABLE_SERVICE_WORKER = true;
  window.__comfortPointers = [];
  for (const type of ['pointerdown', 'pointermove', 'pointerup', 'pointercancel']) window.addEventListener(type, event => {
    window.__comfortPointers.push({ type, isTrusted: event.isTrusted, pointerType: event.pointerType, pointerId: event.pointerId,
      isPrimary: event.isPrimary, x: event.clientX, y: event.clientY, start: event.target?.closest?.('[data-apt-radial-start]')?.getAttribute('data-apt-radial-start') ?? null });
  }, true);
  window.__comfortControl = name => {
    const nodes = [...document.querySelectorAll('button')].filter(node => (node.getAttribute('aria-label') || node.textContent).trim().replace(/\s+/g, ' ') === name
      && node.getClientRects().length && !node.closest('[aria-hidden="true"]'));
    if (nodes.length !== 1) throw Error('expected one visible control: ' + name + ' (' + nodes.length + ')');
    return nodes[0];
  };
}

test('composer comfort: 34 px hub, 0.12 m lit-lobe margin, Off readout, native sweep and emulated touch', { timeout: 145000 }, async () => {
  assert.ok(!process.env.HOME_APARTMENT_CANDIDATE_ROOT, 'proof serves only this worktree');
  const run = path.join(ROOT, '.tmp/apartment-composer-comfort-browser', 'run-' + Date.now());
  fs.mkdirSync(run, { recursive: true });
  const packs = path.join(run, 'packs'), privateRoot = path.join(run, 'private');
  const echo = installPack(packs, 'echo'), victoria = installPack(packs, 'victoria');
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1,
    packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  const protectedFiles = [echo, victoria].flatMap(item => Object.keys(item.files).map(name => path.join(packs, item.site, item.revision, name)));
  protectedFiles.push(path.join(packs, 'index.json'), path.join(privateRoot, 'registration.json'));
  const protectedHashes = Object.fromEntries(protectedFiles.map(file => [file, hash(fs.readFileSync(file))]));
  const sources = publicSources(SRC), receipt = { schema: 'home-apartment-composer-comfort-browser-1',
    authored_room: true, real_renderer: true, complete_home_shell: true, household_data: false, screenshots: false,
    deadlines_ms: { proof: PROOF_DEADLINE_MS, warmup: WARMUP_MS, wait: WAIT_MS, file: 180000 },
    source_sha256: Object.fromEntries(['home-apartment.jsx', 'home-apartment-cards.jsx', 'home-apartment-directional.js',
      'home-3d/apartment-directional-presentation.mjs'].map(name => [name, hash(fs.readFileSync(path.join(SRC, name)))])),
    checks: [], observations: [], requests: [], forbidden: [], aborted_health_probes: [], blocked_read_subscriptions: [], missing_code: [], errors: [], console_errors: [],
    dispatched: [], browser: browserOptions() };
  const started = performance.now(), elapsed = () => Math.round(performance.now() - started);
  const save = () => { receipt.elapsed_ms = elapsed(); fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n'); };
  let server, handler, origin, browser, timer;
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => {
    receipt.deadline_expired = true; save(); browser?.close().catch(() => {}); server?.closeAllConnections();
    reject(Error('145s composer comfort browser deadline'));
  }, PROOF_DEADLINE_MS); });
  // Values a stage is about to assert, kept so a hosted failure shows them.
  const observe = (stage, value) => { receipt.observations.push({ stage, at_ms: elapsed(), ...value }); save(); };
  const check = async (name, operation) => {
    const stage = { name, started_ms: elapsed() }; receipt.current_stage = name; save();
    try { const detail = await operation(); receipt.checks.push({ name, ...detail, elapsed_ms: elapsed() - stage.started_ms }); }
    finally { receipt.current_stage = null; save(); }
  };

  async function serve() {
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
          body = fs.readFileSync(entry.file);
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
  }

  // One booted home shell; mouse stages first, then touch emulation.
  async function openShell(label) {
    const context = await browser.newContext({ viewport: { width: 1366, height: 768 }, deviceScaleFactor: 1,
      reducedMotion: 'no-preference', serviceWorkers: 'block' });
    await context.addCookies([{ name: 'authored_owner_session', value: '1', url: origin }]);
    await context.addInitScript(installObservers);
    await context.route('**/*', route => {
      const request = route.request(), url = new URL(request.url());
      if (['data:', 'blob:'].includes(url.protocol)) return route.continue();
      if (url.origin !== origin) {
        // The same harmless local health probe the directional proof admits; it is still aborted.
        const harmless = ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) && request.method() === 'GET' && url.pathname === '/healthz';
        (harmless ? receipt.aborted_health_probes : receipt.forbidden).push({ origin: url.origin, path: url.pathname, method: request.method() });
        return route.abort();
      }
      return route.continue();
    });
    await context.routeWebSocket('**/*', socket => {
      const url = new URL(socket.url());
      if (url.host === new URL(origin).host && url.pathname === '/proxy/tracker/ws/tracks') receipt.blocked_read_subscriptions.push(url.pathname);
      else receipt.forbidden.push({ websocket: socket.url() });
      socket.close();
    });
    const page = await context.newPage(), cdp = await context.newCDPSession(page);
    page.setDefaultTimeout(WAIT_MS);
    page.on('pageerror', error => receipt.errors.push(label + ': ' + error.message));
    page.on('console', message => { if (message.type() === 'error') receipt.console_errors.push(label + ': ' + message.text().slice(0, 300)); });
    await page.goto(origin + '/?apartmentRedesign=1&worldZoom=off', { waitUntil: 'domcontentloaded' });
    await page.locator('.apt-home-shell[data-home-layout="unified"]').waitFor({ timeout: 45000 });
    const press = async name => { await page.evaluate(name => { const node = window.__comfortControl(name); node.focus(); }, name); await page.keyboard.press('Enter'); };
    await page.waitForFunction(() => window.__HOME_APARTMENT_SITE_API?.readback().phase === 'ready' && window.__APT_ENGINE?.siteId === 'echo', null, { timeout: WARMUP_MS });
    await press('Victoria');
    await page.waitForFunction(() => window.__HOME_APARTMENT_SITE_API?.readback().phase === 'ready' && window.__APT_ENGINE?.siteId === 'victoria'
      && window.HomeApartmentDirectional && window.__APT_ENGINE.overlay.virtualFixturesReadback().fixture_count === 4, null, { timeout: WARMUP_MS });
    const fixture = await page.evaluate(async () => {
      const engine = window.__APT_ENGINE; engine.rig.snapHome(0); await engine.modes.preload(['collision']);
      const overlay = engine.overlay; window.__comfortCalls = [];
      for (const method of ['setVirtualFixtureState', 'aimVirtualFixtureAtTarget', 'restoreVirtualFixtureAim']) {
        const original = overlay[method]; overlay[method] = (...args) => {
          const call = { method, fixture_id: args[0], patch: structuredClone(args[1]) }; window.__comfortCalls.push(call);
          const result = original(...args); call.ok = result?.ok === true; return result;
        };
      }
      return overlay.virtualFixturesReadback().fixtures[0];
    });
    await press('Select ' + fixture.label);
    await page.waitForFunction(() => document.querySelector('[data-apt-directional-composer="1"]'));
    await press('Light');
    await page.waitForFunction(() => document.querySelector('[data-apt-directional-composer="1"] [data-apt-radial-hub="hit"]')?.getAttribute('data-apt-radial-start') === 'hub'
      && !document.querySelector('[data-apt-directional-composer="1"]').hidden);
    const descriptor = () => page.evaluate(id => window.__APT_ENGINE.overlay.virtualFixtureInteractionDescriptor(id), fixture.fixture_id);
    // Client point of a fixture-height plane offset, through the real renderer.
    const planePoint = (number, radius) => page.evaluate(({ id, number, radius }) => {
      const engine = window.__APT_ENGINE, host = engine.picking.getHost(), rect = host.getBoundingClientRect();
      const snapshot = engine.overlay.virtualFixtureInteractionDescriptor(id), zone = snapshot.state.radials.find(item => item.number === number);
      const matrix = engine.camera.projectionMatrix.clone().multiply(engine.camera.matrixWorldInverse).multiply(engine.apartmentRoot.matrixWorld);
      const frame = window.HomeApartmentDirectional.createPlaneFrame({ anchor: snapshot.display_anchor_m, clipFromLocal: matrix.elements, width: host.clientWidth, height: host.clientHeight });
      const angle = zone.azimuth_deg * Math.PI / 180, screen = frame.project([radius * Math.cos(angle), radius * Math.sin(angle)]);
      if (!screen?.visible) return null;
      const x = rect.left + screen.x * rect.width / host.clientWidth, y = rect.top + screen.y * rect.height / host.clientHeight;
      const target = document.elementFromPoint(x, y);
      return { x, y, start: target?.closest?.('[data-apt-radial-start]')?.getAttribute('data-apt-radial-start') ?? null, canvas: target === engine.renderer.domElement,
        element: target ? target.tagName + '.' + String(target.className?.baseVal ?? target.className).slice(0, 60) + ' ' + (target.getAttribute('aria-label') || '') : null };
    }, { id: fixture.fixture_id, number, radius });
    const hub = () => page.evaluate(() => {
      const engine = window.__APT_ENGINE, host = engine.picking.getHost(), rect = host.getBoundingClientRect();
      const circle = document.querySelector('[data-apt-directional-composer="1"] [data-apt-radial-hub="hit"]');
      return { x: rect.left + +circle.getAttribute('cx') * rect.width / host.clientWidth, y: rect.top + +circle.getAttribute('cy') * rect.height / host.clientHeight,
        r: +circle.getAttribute('r'), start: circle.getAttribute('data-apt-radial-start') };
    });
    const startAt = (x, y) => page.evaluate(({ x, y }) => document.elementFromPoint(x, y)?.closest?.('[data-apt-radial-start]')?.getAttribute('data-apt-radial-start') ?? null, { x, y });
    const readout = () => page.evaluate(() => {
      const node = document.querySelector('[data-apt-directional-composer="1"] [role="status"]');
      return { text: node?.textContent ?? null, hidden: !node || node.hidden };
    });
    const calls = () => page.evaluate(() => window.__comfortCalls.slice());
    const captured = () => page.evaluate(() => {
      const down = [...window.__comfortPointers].reverse().find(row => row.type === 'pointerdown');
      return !!down && window.__APT_ENGINE.picking.getHost().hasPointerCapture(down.pointerId);
    });
    const pose = () => page.evaluate(() => window.__APT_ENGINE.camera.matrixWorld.toArray());
    const send = async (method, packet) => { receipt.dispatched.push({ shell: label, method, type: packet.type }); await cdp.send(method, packet); };
    // Chromium aligns moves to animation frames and may coalesce two quick
    // ones. Each sample is sent only after the page saw the previous one as
    // its own trusted pointermove, so every dispatched sample is delivered.
    const delivered = async (pointerType, x, y, dispatch) => {
      const seen = await page.evaluate(() => window.__comfortPointers.length); await dispatch();
      await page.waitForFunction(({ seen, pointerType, x, y }) => window.__comfortPointers.slice(seen).some(row => row.type === 'pointermove'
        && row.isTrusted && row.pointerType === pointerType && Math.abs(row.x - x) <= 1 && Math.abs(row.y - y) <= 1), { seen, pointerType, x, y });
    };
    const mouse = {
      move: (x, y, buttons = 0) => delivered('mouse', x, y, () => send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y, button: buttons ? 'left' : 'none', buttons, pointerType: 'mouse' })),
      drag: (x, y) => send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y, button: 'left', buttons: 1, pointerType: 'mouse' }),
      down: (x, y) => send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', buttons: 1, clickCount: 1, pointerType: 'mouse' }),
      up: (x, y) => send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', buttons: 0, clickCount: 1, pointerType: 'mouse' }),
    };
    const touch = (type, points) => {
      const dispatch = () => send('Input.dispatchTouchEvent', { type, touchPoints: points.map(([x, y, id]) => ({ x, y, id, radiusX: 4, radiusY: 4, force: 1 })) });
      return type === 'touchMove' ? delivered('touch', points[0][0], points[0][1], dispatch) : dispatch();
    };
    // The touch stages reuse this booted shell: Chromium's own touch emulation
    // (the protocol call Playwright's hasTouch makes) turns the pointer coarse.
    const enableTouch = () => cdp.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 });
    return { context, page, fixture, descriptor, planePoint, hub, startAt, readout, calls, captured, pose, mouse, touch, enableTouch };
  }
  const settle = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const onlyRadialWrites = rows => rows.every(row => row.method === 'setVirtualFixtureState' && Object.keys(row.patch).join() === 'radials'
    && row.patch.radials.every(zone => Object.keys(zone).sort().join() === 'brightness,number,on'));
  const withoutRadials = value => { const copy = structuredClone(value); delete copy.state.radials; return copy; };
  // A lit lobe whose start point is open at this pose (fixture labels and the
  // mode pill may cover a zone); the first candidate that is open wins.
  const openLobe = async (shell, candidates) => {
    const state = await shell.descriptor();
    for (const number of candidates) {
      const zone = state.state.radials[number - 1]; if (!(zone.on && zone.brightness > .5)) continue;
      const point = await shell.planePoint(number, LOBE_START_M); if (point?.start === 'zone:' + number) return { number, zone, point };
    }
    assert.fail('no open lit lobe start among ' + candidates.join(', '));
  };
  const trustedPointers = async (page, pointerType) => {
    const rows = (await page.evaluate(() => window.__comfortPointers)).filter(row => row.pointerType === pointerType);
    assert.ok(rows.length > 0 && rows.every(row => row.isTrusted), 'every observed ' + pointerType + ' pointer event is a trusted browser event'); return rows.length;
  };

  async function proof() {
    await serve();
    browser = await chromium.launch(receipt.browser);
    receipt.runtime = { node: process.version, playwright: require('playwright/package.json').version, browser: browser.version() };
    const desk = await openShell('shell'), original = await desk.descriptor();

    await check('the hub hit circle is 34 px and takes a press 22 to 34 px from the light point', async () => {
      const centre = await desk.hub(); assert.equal(centre.r, HUB_PX); assert.equal(centre.start, 'hub');
      const offset = (OLD_HUB_PX + HUB_PX) / 2, x = centre.x + offset * Math.SQRT1_2, y = centre.y + offset * Math.SQRT1_2;
      assert.equal(await desk.startAt(x, y), 'hub', 'a point outside the old 22 px hub but inside 34 px reaches the hub start');
      assert.equal(await desk.startAt(centre.x + HUB_PX + 1, centre.y + 40) === 'hub', false);
      const before = (await desk.calls()).length;
      await desk.mouse.move(x, y); await desk.mouse.down(x, y);
      assert.equal(await desk.captured(), true, 'the hub press starts an owned radial gesture');
      const out = await desk.planePoint(2, radiusFor(.95)); assert.ok(out);
      await desk.mouse.move(out.x, out.y, 1); await desk.mouse.up(out.x, out.y); await settle(desk.page);
      const after = await desk.descriptor(), rows = (await desk.calls()).slice(before);
      assert.ok(rows.length > 0 && onlyRadialWrites(rows), 'the hub gesture writes radials only');
      assert.ok(withinSteps(after.state.radials[1].brightness, .95), 'dragging out from the wide hub paints the zone under the cursor');
      assert.deepEqual(withoutRadials(after), withoutRadials(original));
      return { hub_px: centre.r, pressed_px_from_centre: offset, painted_zone: 2, level: after.state.radials[1].brightness };
    });

    await check('a lit lobe takes a press 0.06 m past its drawn edge, picks up without a jump and reads Off at zero', async () => {
      const before = await desk.descriptor(), zone = before.state.radials[0]; assert.ok(zone.on && zone.brightness > 0);
      const edge = radiusFor(zone.brightness), inside = await desk.planePoint(1, edge + MARGIN / 2); assert.ok(inside);
      assert.equal(inside.start, 'zone:1', 'the hit path reaches past the drawn edge into the margin');
      const callsBefore = (await desk.calls()).length;
      await desk.mouse.move(inside.x, inside.y); await desk.mouse.down(inside.x, inside.y);
      assert.equal(await desk.captured(), true);
      const outward = await desk.planePoint(1, edge + MARGIN * .9);
      await desk.mouse.move(outward.x, outward.y, 1); await settle(desk.page);
      assert.deepEqual((await desk.descriptor()).state.radials[0], zone, 'pickup outside the edge changes nothing until the edge is crossed');
      const inward = await desk.planePoint(1, radiusFor(.35)); await desk.mouse.move(inward.x, inward.y, 1); await settle(desk.page);
      assert.ok(withinSteps((await desk.descriptor()).state.radials[0].brightness, .35));
      assert.match((await desk.readout()).text, /^Zone 1 · 3[456]%$/);
      const zero = await desk.planePoint(1, R0 * .6); await desk.mouse.move(zero.x, zero.y, 1); await settle(desk.page);
      const off = await desk.readout(); assert.equal(off.hidden, false); assert.equal(off.text, 'Zone 1 · Off');
      await desk.mouse.up(zero.x, zero.y); await settle(desk.page);
      const after = await desk.descriptor();
      assert.deepEqual(after.state.radials[0], { ...zone, on: false, brightness: 0 });
      assert.ok(onlyRadialWrites((await desk.calls()).slice(callsBefore)));
      return { margin_m: MARGIN, pressed_past_edge_m: MARGIN / 2, pickup_exact: true, readout_at_zero: off.text };
    });

    await check('an unlit area past the margin but inside the outer ring orbits instead of painting', async () => {
      // A lit zone whose just-past-margin point is open canvas (labels and the
      // mode pill may cover others at this pose).
      let outside = null, probed = [];
      for (const number of [5, 6, 4, 3]) {
        const zone = (await desk.descriptor()).state.radials[number - 1]; assert.ok(zone.on && radiusFor(zone.brightness) + MARGIN + .05 < R1);
        const point = await desk.planePoint(number, radiusFor(zone.brightness) + MARGIN + .05); probed.push({ number, element: point?.element, start: point?.start });
        if (point?.canvas) { outside = { ...point, number }; break; }
      }
      assert.ok(outside, 'one past-margin point reaches the canvas: ' + JSON.stringify(probed)); assert.equal(outside.start, null);
      const before = await desk.descriptor(), poseBefore = await desk.pose(), callsBefore = (await desk.calls()).length;
      await desk.mouse.move(outside.x, outside.y); await desk.mouse.down(outside.x, outside.y);
      // An orbit does not depend on each sample, so these are sent without delivery waits.
      for (let step = 1; step <= 8; step++) await desk.mouse.drag(outside.x + step * 5, outside.y + step);
      await desk.mouse.up(outside.x + 40, outside.y + 8);
      await desk.page.waitForFunction(before => window.__APT_ENGINE.camera.matrixWorld.toArray().some((value, index) => value !== before[index]), poseBefore);
      assert.deepEqual((await desk.calls()).slice(callsBefore), [], 'no modeled write from an orbit');
      assert.deepEqual((await desk.descriptor()).state.radials, before.state.radials);
      await desk.page.evaluate(() => window.__APT_ENGINE.rig.snapHome(0)); await settle(desk.page);
      await desk.page.waitForFunction(() => document.querySelector('[data-apt-directional-composer="1"] [data-apt-radial-hub="hit"]')?.getAttribute('data-apt-radial-start') === 'hub');
      return { orbit: true, modeled_calls: 0, probe_zone: outside.number, probed };
    });

    await check('a fast native sweep fills the skipped zone between two painted zones', async () => {
      const before = await desk.descriptor(), next = (number, step) => (number - 1 + step) % 6 + 1;
      let n = null, start = null;
      for (const number of [4, 5, 6, 3, 2]) {
        if (!(before.state.radials[number - 1].on && before.state.radials[number - 1].brightness > .5)) continue;
        const point = await desk.planePoint(number, LOBE_START_M); if (point?.start === 'zone:' + number) { n = number; start = point; break; }
      }
      assert.ok(start, 'a lit lobe start is open at this pose');
      const [a, skipped, b] = [next(n, 1), next(n, 2), next(n, 3)];
      const callsBefore = (await desk.calls()).length;
      await desk.mouse.move(start.x, start.y); await desk.mouse.down(start.x, start.y);
      // Each target is projected after the previous sample is delivered: the
      // rig's ambient hover drift moves the camera while input is in flight.
      const first = await desk.planePoint(a, radiusFor(.4)); assert.ok(first); await desk.mouse.move(first.x, first.y, 1);
      const last = await desk.planePoint(b, radiusFor(.8)); assert.ok(last); await desk.mouse.move(last.x, last.y, 1);
      await desk.mouse.up(last.x, last.y); await settle(desk.page);
      const after = await desk.descriptor(), zone = number => after.state.radials[number - 1];
      observe('sweep', { pickup: n, zones: [a, skipped, b], levels: [zone(a).brightness, zone(skipped).brightness, zone(b).brightness] });
      assert.deepEqual(zone(n), before.state.radials[n - 1], 'the pickup zone is never written');
      assert.ok(withinSteps(zone(a).brightness, .4) && withinSteps(zone(b).brightness, .8));
      const expected = Math.round((zone(a).brightness + zone(b).brightness) / 2 * 100) / 100;
      assert.ok(withinSteps(zone(skipped).brightness, expected), 'the skipped zone takes the swept level between its neighbours');
      assert.ok(zone(skipped).brightness > zone(a).brightness && zone(skipped).brightness < zone(b).brightness && zone(skipped).on);
      assert.ok(onlyRadialWrites((await desk.calls()).slice(callsBefore)));
      const mouseEvents = await trustedPointers(desk.page, 'mouse');
      return { pickup_zone: n, interpolated_zone: skipped, levels: [zone(a).brightness, zone(skipped).brightness, zone(b).brightness], trusted_mouse_events: mouseEvents };
    });
    const deskFinal = await desk.descriptor(); assert.deepEqual(withoutRadials(deskFinal), withoutRadials(original));

    await desk.enableTouch(); const tablet = desk;
    await check('touch emulation is a coarse pointer and the composer keeps its forgiving hit areas', async () => {
      const media = await tablet.page.evaluate(() => ({ coarse: matchMedia('(pointer: coarse)').matches, anyCoarse: matchMedia('(any-pointer: coarse)').matches,
        hoverNone: matchMedia('(hover: none)').matches,
        pill: [...document.querySelectorAll('[data-apt-directional-composer="1"] .apt-directional-toolpill button')].map(node => getComputedStyle(node).height) }));
      assert.equal(media.coarse, true); assert.equal(media.anyCoarse, true);
      assert.deepEqual(media.pill, ['36px', '36px'], 'the coarse-pointer pill buttons are 36 px tall');
      assert.equal((await tablet.hub()).r, HUB_PX);
      return media;
    });
    await check('one finger paints a lit lobe from inside its edge', async () => {
      const before = await tablet.descriptor(), { number, zone, point: start } = await openLobe(tablet, [5, 6, 4, 2, 1, 3]);
      const callsBefore = (await tablet.calls()).length;
      await tablet.touch('touchStart', [[start.x, start.y, 1]]);
      assert.equal(await tablet.captured(), true, 'a single finger starts the owned gesture');
      const beyond = await tablet.planePoint(number, Math.min(R1, radiusFor(zone.brightness) + .1)); await tablet.touch('touchMove', [[beyond.x, beyond.y, 1]]);
      const target = await tablet.planePoint(number, radiusFor(.7)); await tablet.touch('touchMove', [[target.x, target.y, 1]]);
      await tablet.touch('touchEnd', []); await settle(tablet.page);
      const after = await tablet.descriptor(); observe('one-finger', { zone: number, level: after.state.radials[number - 1].brightness });
      assert.ok(withinSteps(after.state.radials[number - 1].brightness, .7), 'one finger paints like the mouse');
      assert.ok(onlyRadialWrites((await tablet.calls()).slice(callsBefore))); assert.deepEqual(withoutRadials(after), withoutRadials(before));
      return { zone: number, level: after.state.radials[number - 1].brightness };
    });
    await check('a second finger cancels the gesture and keeps what was already set', async () => {
      const { number, zone, point: start } = await openLobe(tablet, [6, 4, 5, 2, 1, 3]);
      await tablet.touch('touchStart', [[start.x, start.y, 1]]);
      const beyond = await tablet.planePoint(number, Math.min(R1, radiusFor(zone.brightness) + .1)); await tablet.touch('touchMove', [[beyond.x, beyond.y, 1]]);
      const set = await tablet.planePoint(number, radiusFor(.5)); await tablet.touch('touchMove', [[set.x, set.y, 1]]); await settle(tablet.page);
      const painted = await tablet.descriptor(); observe('two-finger-set', { zone: number, level: painted.state.radials[number - 1].brightness });
      assert.ok(withinSteps(painted.state.radials[number - 1].brightness, .5));
      const later = await tablet.planePoint(number, radiusFor(.9)), other = await tablet.planePoint(number % 6 + 1, radiusFor(1)); assert.ok(later && other);
      const callsAtCancel = (await tablet.calls()).length;
      await tablet.touch('touchStart', [[set.x, set.y, 1], [other.x, other.y, 2]]);
      await tablet.touch('touchMove', [[later.x, later.y, 1], [other.x + 20, other.y, 2]]);
      await tablet.touch('touchEnd', []); await settle(tablet.page);
      const after = await tablet.descriptor();
      assert.deepEqual(after.state.radials, painted.state.radials, 'the second finger changes nothing that was already set');
      assert.deepEqual((await tablet.calls()).slice(callsAtCancel), [], 'no modeled write after the cancel');
      const touchEvents = await trustedPointers(tablet.page, 'touch');
      const secondary = (await tablet.page.evaluate(() => window.__comfortPointers)).filter(row => row.pointerType === 'touch' && row.type === 'pointerdown' && row.isPrimary === false);
      assert.ok(secondary.length >= 1, 'the browser delivered a real second, non-primary touch pointer');
      return { zone: number, kept_level: after.state.radials[number - 1].brightness, trusted_touch_events: touchEvents, second_pointer_downs: secondary.length };
    });
    await desk.context.close();

    for (const [file, digest] of Object.entries(protectedHashes)) assert.equal(hash(fs.readFileSync(file)), digest, 'authored inputs unchanged: ' + path.basename(file));
    assert.deepEqual(receipt.forbidden, [], 'no non-GET request, foreign origin or WebSocket'); assert.deepEqual(receipt.missing_code, []);
    assert.deepEqual(receipt.errors, []);
    assert.deepEqual(receipt.console_errors.filter(text => /WebGLProgram|Shader Error|THREE\./.test(text)), [], 'no renderer shader error');
    assert.equal(receipt.checks.length, 7); receipt.passed = true;
  }
  try { await Promise.race([proof(), deadline]); }
  catch (error) { receipt.passed = false; receipt.failure = error.stack || String(error); throw error; }
  finally { clearTimeout(timer); await browser?.close().catch(() => {}); server?.closeAllConnections(); server?.close(); save();
    console.log(JSON.stringify({ receipt: path.join(run, 'receipt.json'), passed: receipt.passed === true, checks: receipt.checks.length, elapsed_ms: receipt.elapsed_ms })); }
});
