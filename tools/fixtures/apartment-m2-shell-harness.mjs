// Shared authored harness for the M2 shell browser suites: the actual Home
// shell on the production renderer with authored packs, a synthetic Echo
// camera and switchable asset faults. No household pack, live camera,
// session or command; every non-GET request is refused and recorded.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import Core from '../../app/src/home-equipment-registration.js';
import { createEquipmentRegistrationGateway, initRegistration, readActiveBinding } from '../../web-gateway/equipment-registration.mjs';
import { installPack, authoredDocument } from './apartment-home-shell-fixtures.mjs';
import { waitForDirectionalReadiness as waitReadiness } from './apartment-directional-readiness.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const SRC = path.join(ROOT, 'app/src');

// Names the waiting line in a timeout, which the shared poller cannot know.
export async function waitForDirectionalReadiness(...args) {
  const caller = new Error().stack.split(/\n/)[2]?.trim();
  try { return await waitReadiness(...args); } catch (error) { error.message += ' at ' + caller; throw error; }
}

/* Every screen the old Home menu reached, and where it lives in the unified
 * shell. The PR lists this table; the walk below opens each one. */
export const FORMER_SCREENS = Object.freeze([
  { id: 'perception-lab', label: 'Perception Lab', place: 'Menu › Perception Lab', exit: 'Back' },
  { id: 'metrics', label: 'Metrics', place: 'Menu › Metrics', exit: 'Back' },
  { id: 'people', label: 'People', place: 'Menu › People' },
  { id: 'intelligence', label: 'Intelligence', place: 'Menu › Intelligence' },
  { id: 'video-labeler', label: 'Video labeler', place: 'Menu › Video labeler (desktop) and /labeler' },
  { id: 'lights', label: 'Lights / Travel Mode', place: 'Top bar › Lights / Travel Mode, Menu and /lights' },
  { id: 'preferences', label: 'Preferences / connection', place: 'Menu › Preferences / connection and Chat › Connection' },
  { id: 'simulation', label: 'Simulation', place: '/simulation, ?simulation=1 and Chat setup › Try simulation; Menu › Simulation while it runs' },
  { id: 'bench', label: 'Bench', place: 'Chat header › Bench, when the bench console answers' },
  { id: 'apartment', label: 'Apartment room', place: 'The default route; /apartment' },
  { id: 'previous-home', label: 'Previous Home', place: 'Menu › Previous Home and ?apartmentRedesign=0' },
].map(Object.freeze));

function staticSources(root) {
  const sources = new Map(), excluded = new Set(['node_modules', 'data', 'artifacts', 'private', 'recordings', 'logs', 'precompiled']);
  (function walk(directory, depth = 0) {
    assert.ok(depth <= 12, 'static source depth is bounded');
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (entry.name.startsWith('.') || excluded.has(entry.name)) continue;
      const file = path.join(directory, entry.name), relative = path.relative(root, file).split(path.sep).join('/');
      if (relative === 'assets/apartment' || relative.startsWith('assets/apartment/')) continue;
      const stat = fs.lstatSync(file);
      if (stat.isSymbolicLink()) continue;
      if (stat.isDirectory()) { walk(file, depth + 1); continue; }
      const trustedAsset = /^(?:vendor|fonts|icons)\//.test(relative) && /\.(?:wasm|woff2?|ttf|otf|png|jpe?g|webp|svg|ico|ktx2|json)$/.test(entry.name);
      const rootAsset = directory === root && ['index.html', 'favicon.svg', 'favicon.ico', 'favicon-32.png', 'apple-touch-icon.png', 'site.webmanifest'].includes(entry.name);
      if (!/\.(?:js|mjs|jsx|css)$/.test(entry.name) && !trustedAsset && !rootAsset) continue;
      sources.set('/' + relative, file);
    }
  })(root);
  return sources;
}

function launchOptions(platform = process.platform) {
  const args = ['--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'];
  if (platform === 'win32') args.unshift(process.env.M2_SWIFTSHADER === '1' ? '--use-angle=swiftshader' : '--use-angle=d3d11');
  else if (platform === 'linux') args.unshift('--use-angle=swiftshader', '--enable-unsafe-swiftshader');
  const installed = fs.existsSync(chromium.executablePath());
  return { headless: true, args, ...(platform === 'linux' ? { channel: 'chromium' } : !installed && platform === 'win32' ? { channel: 'msedge' } : {}) };
}

export async function runM2Shell(name, steps) {
  const run = path.join(ROOT, '.tmp/' + name, 'run-' + Date.now());
  fs.mkdirSync(run, { recursive: true });
  const packs = path.join(run, 'packs'), privateRoot = path.join(run, 'private');
  const echo = installPack(packs, 'echo'), victoria = installPack(packs, 'victoria');
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1,
    packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  // A synthetic camera placed in the authored Echo model. Its pose is the
  // model's own estimated device pose; no calibration or household image.
  const echoModel = { ...echo.seed, devices: [...echo.seed.devices, { id: 'authored-echo-camera', type: 'camera',
    name: 'Authored corner camera', pos: [0.4, 0.4, 2.2], yaw_rad: 0.8 }] };
  const sources = staticSources(SRC), requests = [], forbidden = [], errors = [], checks = [], started = Date.now();
  checks.push = (...rows) => Array.prototype.push.apply(checks, rows.map(row => ({ ...row, at_ms: Date.now() - started })));
  const faults = new Map(), stalled = [];
  const receipt = { run, schema: 'home-' + name + '-1', authored_room: true, real_renderer: true, real_household_data: false,
    live_camera: false, model_session_started: false, physical_commands: 0, checks, forbidden, errors };
  let server, browser, origin, handler, timer;
  const deadline = new Promise((_, reject) => { timer = setTimeout(() => {
    browser?.close().catch(() => {}); server?.closeAllConnections(); reject(new Error('145s ' + name + ' deadline'));
  }, 145000); });
  const assetPath = (pack, name) => pack.base + name;

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
            : route === 'sessions' ? { site_id: 'victoria', version: 1, items: [] } : route === 'recordings' ? { items: [] } : null;
          if (value) { body = Buffer.from(JSON.stringify(value)); type = 'application/json'; }
        }
        if (url.pathname === '/content-packs/index.json') { body = fs.readFileSync(path.join(packs, 'index.json')); type = 'application/json'; }
        else {
          const pack = [echo, victoria].find(item => url.pathname.startsWith(item.base));
          if (pack) {
            const fault = faults.get(url.pathname);
            body = pack.files[url.pathname.slice(pack.base.length)];
            if (fault === 'fail') { res.writeHead(404); res.end(); return; }
            if (fault === 'stall' && body) {
              // Part of the declared bytes, then hold: the toolbar shows a real percentage.
              res.writeHead(200, { 'Content-Type': type, 'Content-Length': body.length, 'Cache-Control': 'no-store' });
              // Three quarters arrive in steps, so a reader that joins late still sees new bytes.
              const quarter = Math.floor(body.length / 4);
              res.write(body.subarray(0, quarter)); stalled.push(res);
              for (const [step, delay] of [[2, 400], [3, 1200]]) setTimeout(() => {
                if (!res.destroyed && !res.writableEnded) res.write(body.subarray((step - 1) * quarter, step * quarter));
              }, delay);
              return;
            }
            if (url.pathname.endsWith('.json')) type = 'application/json';
          }
        }
        if (!body && url.pathname.startsWith('/proxy/')) {
          const value = url.pathname.endsWith('/model') || url.pathname.endsWith('/apartment_model') ? echoModel
            : url.pathname.endsWith('/api/states') ? [] : url.pathname.endsWith('/api/config') ? { unit_system: { length: 'm' } } : {};
          body = Buffer.from(JSON.stringify(value)); type = 'application/json';
        }
        const file = sources.get(url.pathname === '/' ? '/index.html' : url.pathname);
        if (!body && file) {
          body = fs.readFileSync(file);
          type = /\.(?:js|mjs|jsx)$/.test(file) ? 'text/javascript' : file.endsWith('.html') ? 'text/html' : file.endsWith('.css') ? 'text/css'
            : /\.(?:json|webmanifest)$/.test(file) ? 'application/json' : file.endsWith('.svg') ? 'image/svg+xml' : type;
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
    browser = await chromium.launch(launchOptions());
    const context = await browser.newContext({ viewport: { width: 1366, height: 768 }, serviceWorkers: 'block' });
    await context.addCookies([{ name: 'authored_owner_session', value: '1', url: origin }]);
    await context.addInitScript(() => {
      if (!sessionStorage.getItem('authored-m2-seeded')) {
        localStorage.setItem('apartment3d.site', 'echo'); sessionStorage.setItem('authored-m2-seeded', '1');
      }
      localStorage.setItem('home.perf.backgroundWarmup', 'off');
      // No post-boot asset prewarm: a fault test must see the asset load it starts,
      // not bytes an earlier warm-up already cached in memory.
      localStorage.setItem('apartment3d.prewarm', 'off');
      window.HG_DISABLE_SERVICE_WORKER = true;
      window.__benchOpened = [];
      window.open = (url) => { window.__benchOpened.push(String(url)); return null; };
    });
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      if (['data:', 'blob:'].includes(url.protocol)) return route.continue();
      // The bench console probe is answered by an authored stand-in.
      if (url.origin === 'http://127.0.0.1:8765' && url.pathname === '/healthz' && route.request().method() === 'GET') {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ service: 'bench-console' }) });
      }
      if (url.origin !== origin) {
        const harmlessProbe = ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) && route.request().method() === 'GET' && url.pathname === '/healthz';
        if (!harmlessProbe) forbidden.push({ origin: url.origin, path: url.pathname, method: route.request().method(), external: true });
        return route.abort();
      }
      return route.continue();
    });
    await context.routeWebSocket('**/*', socket => socket.close());
    const page = await context.newPage(); page.setDefaultTimeout(15000);
    page.on('pageerror', error => errors.push(error.message));

    const waitSite = site => waitForDirectionalReadiness(page, site => window.__HOME_APARTMENT_SITE_API?.readback().phase === 'ready'
      && window.__APT_ENGINE?.siteId === site, site);
    const homes = () => page.getByRole('group', { name: 'Home', exact: true });
    const selectedHome = () => homes().locator('button[aria-pressed="true"]').innerText();
    const toolbar = () => page.getByRole('toolbar', { name: 'Room view', exact: true });
    const modeButton = label => toolbar().getByRole('button', { name: label + ' view', exact: true });
    const notice = () => page.locator('.apt-ui-toolbar-notice');
    const engineMode = () => page.evaluate(() => window.__APT_ENGINE.modes.mode);
    const openShell = async (query = '?apartmentRedesign=1&worldZoom=off') => {
      await page.goto(origin + '/' + query, { waitUntil: 'domcontentloaded' });
      await page.locator('.apt-home-shell[data-home-layout="unified"]').waitFor({ timeout: 45000 });
    };
    const tab = name => page.locator('.apt-home-tabs').getByRole('tab', { name, exact: true }).click();
    const menu = async label => {
      await page.getByRole('button', { name: 'Home menu', exact: true }).click();
      await page.getByRole('menu', { name: 'Home features', exact: true }).getByRole('menuitem', { name: label, exact: true }).click();
    };

    const shell = { page, origin, faults, stalled, requests, checks, receipt, echo, victoria, assetPath, waitSite, homes, selectedHome,
      toolbar, modeButton, notice, engineMode, openShell, tab, menu };
    await steps(shell);
    assert.deepEqual(forbidden, []); assert.deepEqual(errors, []);
    receipt.ok = true;
  }
  try { await Promise.race([exercise(), deadline]); }
  catch (error) { receipt.failure = error.stack || error.message; throw error; }
  finally {
    clearTimeout(timer); for (const res of stalled.splice(0)) res.destroy();
    await browser?.close();
    if (server) await new Promise(resolve => { server.closeAllConnections(); server.close(resolve); });
    fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    process.stdout.write(name + ' evidence: ' + path.join(run, 'receipt.json') + '\n');
  }
}
