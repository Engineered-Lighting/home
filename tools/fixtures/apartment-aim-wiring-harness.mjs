// Shared harness for the W2 aim-wiring browser suites: the mounted Home shell
// with the real renderer, native input and authored Victoria room data. Each
// suite brings its own checks and keeps its own fixed deadline. No household
// assets, service writes, WebSocket sends, sessions or private preview evidence.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import Core from '../../app/src/home-equipment-registration.js';
import { createEquipmentRegistrationGateway, initRegistration, readActiveBinding } from '../../web-gateway/equipment-registration.mjs';
import { installPack, authoredDocument } from './apartment-home-shell-fixtures.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..'), SRC = path.join(ROOT, 'app/src');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
// Fixed before each suite's first run: 145 s browser proof, 180 s for the file.
export const BROWSER_DEADLINE_MS = 145000, FILE_TIMEOUT_MS = 180000;
export const OPTICAL = 'Modeled from the socket display point · optical pivot not registered';
export const TRAVEL = 'travel limits not commissioned';

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

function browserOptions(renderer='webgl',browserChannel=null) {
  const installed = fs.existsSync(chromium.executablePath());
  assert.ok(installed || process.platform === 'win32', 'pinned Chromium must exist on Linux');
  const args = ['--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'];
  if (process.platform === 'linux'||process.env.HOME_BODY_SOFTWARE==='1') args.unshift('--use-gl=angle', '--use-angle=swiftshader-webgl', '--enable-unsafe-swiftshader');
  else if (process.platform === 'win32') args.unshift('--use-angle=d3d11');
  if(renderer!=='webgl')args.push('--enable-unsafe-webgpu','--ignore-gpu-blocklist');
  if(browserChannel){assert.ok(['msedge','chrome','chromium'].includes(browserChannel));return {headless:true,args,channel:browserChannel};}
  return { headless: true, args, ...(process.platform === 'linux' ? { channel: 'chromium' } : !installed ? { channel: 'msedge' } : {}) };
}

// Runs one suite's checks inside the shared, fenced Home shell proof.
export async function aimWiringProof(suite, checks, {trackingFixture=null,renderer='webgl',browserChannel=null}={}) {
  assert.ok(['webgl','webgpu','webgpu-webgl'].includes(renderer));
  assert.ok(!process.env.HOME_APARTMENT_CANDIDATE_ROOT, 'proof serves only this worktree');
  const run = path.join(ROOT, '.tmp/apartment-aim-wiring-browser', 'run-' + suite + '-' + Date.now());
  fs.mkdirSync(run, { recursive: true });
  const packs = path.join(run, 'packs'), privateRoot = path.join(run, 'private');
  const echo = installPack(packs, 'echo'), victoria = installPack(packs, 'victoria');
  trackingFixture?.bind(victoria.manifest);
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1,
    packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  const protectedFiles = [echo, victoria].flatMap(item => Object.keys(item.files).map(name => path.join(packs, item.site, item.revision, name)));
  protectedFiles.push(path.join(packs, 'echo/ACTIVE'), path.join(packs, 'victoria/ACTIVE'), path.join(packs, 'index.json'), path.join(privateRoot, 'registration.json'));
  const protectedHashes = Object.fromEntries(protectedFiles.map(file => [file, hash(fs.readFileSync(file))]));
  const sources = publicSources(SRC), receipt = { schema: 'home-apartment-aim-wiring-authored-browser-1', suite,
    authored_room: true, synthetic_victoria: true, real_renderer: true, complete_home_shell: true, household_data: false, screenshots: false,
    source_mode: 'current-worktree-only', source_sha256: Object.fromEntries(['home-apartment.jsx', 'home-apartment-room-overlay.jsx',
      'home-apartment-aim-proposals.js', 'home-3d/apartment-aim-presentation.mjs', 'home-3d/engine.js', 'home-app.jsx']
      .map(name => [name, hash(fs.readFileSync(path.join(SRC, name)))])),
    deadlines: { browser_ms: BROWSER_DEADLINE_MS, file_ms: FILE_TIMEOUT_MS },
    physical_commands: 0, saves: 0, camera_sessions: 0, model_sessions: 0,
    checks: [], requests: [], forbidden: [], websocket_sends: [], blocked_read_subscriptions: [], missing_code: [], errors: [], served: {}, browser: browserOptions(renderer,browserChannel) };
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
        if(trackingFixture?.handle(req,res,url.pathname))return;
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
    if(trackingFixture)await context.addInitScript(()=>{
      window.__HOME_FAST_TRACKING_ENABLED=true;
      const original=window.fetch,events=window.__bodyFixtureClock=[];
      window.fetch=async function(...args){
        const url=new URL(args[0]?.url||args[0],location.href);
        if(url.origin!==location.origin||url.pathname!=='/api/perception-tracking/v1/clock')return original.apply(this,args);
        const event={start_ms:performance.now()};events.push(event);if(events.length>64)events.shift();
        try{
          const response=await original.apply(this,args);event.headers_ms=performance.now();event.status=response.status;
          const json=response.json.bind(response);response.json=async()=>{try{
            const value=await json();event.json_ms=performance.now();event.elapsed_ms=event.json_ms-event.start_ms;
            event.attempt_valid=typeof value.attempt_id==='string'&&/^[a-f0-9]{32}$/.test(value.attempt_id);
            event.monotonic_finite=Number.isFinite(value.monotonic_s);return value;
          }catch(error){event.json_error=error.name;throw error;}};
          return response;
        }catch(error){event.failed_ms=performance.now();event.error=error.name;throw error;}
      };
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
      socket.onMessage(message => receipt.websocket_sends.push({ url: socket.url(), bytes: String(message).length }));
      if (url.host === new URL(origin).host && url.pathname === '/proxy/tracker/ws/tracks') receipt.blocked_read_subscriptions.push(url.pathname);
      else receipt.forbidden.push({ websocket: socket.url() });
      socket.close();
    });
    page = await context.newPage(); page.setDefaultTimeout(15000); page.on('pageerror', error => receipt.errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') (receipt.console_errors ||= []).push(message.text().slice(0, 400)); });
    await page.goto(origin + '/?apartmentRedesign=1&worldZoom=off'+(renderer==='webgl'?'':'&renderer='+renderer), { waitUntil: 'domcontentloaded' });
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

    await checks({ page, context, check, fixtures, ids, descriptor, pose, blur, inspector, fixtureInspector, selectedTab, title,
      overlayState, until, canvasPoint, surfaceHit, near, settle, reticleDrag, save, receipt,artifactDir:run });
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
      tracking:window.__APT_ENGINE?.fastTrackingReadback?.(),
      fixtureClock:window.__bodyFixtureClock,
    })).catch(() => null);
    save(); throw error;
  }
  finally {
    clearTimeout(timer); save();
    for (const [name, close] of [['tracking fixture',()=>trackingFixture?.close()],['context', () => context?.close()], ['browser', () => browser?.close()],
      ['server', async () => { server?.closeAllConnections(); if (server) await new Promise(resolve => server.close(resolve)); }]]) {
      try { await close(); } catch (error) { (receipt.cleanup_errors ||= []).push({ name, message: error.message }); }
    }
    save(); console.log(JSON.stringify({ receipt: path.join(run, 'receipt.json'), result: receipt.result, checks: receipt.checks.length }));
  }
}
