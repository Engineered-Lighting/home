// Authored rooms and a loopback server for the Apartment Setup rail suites.
// Synthetic data only: no household assets, no service writes beyond the
// throwaway equipment-registration test gateway, no camera or model session.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import Core from '../../app/src/home-equipment-registration.js';
import { createEquipmentRegistrationGateway, initRegistration, readActiveBinding } from '../../web-gateway/equipment-registration.mjs';
import { installPack, authoredDocument } from './apartment-home-shell-fixtures.mjs';

const require = createRequire(import.meta.url);
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const SRC = path.join(ROOT, 'app/src');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const STAMP = '2026-10-18T00:00:00.000Z';

const STUDY = { id: 'authored-study', name: 'Authored study', color: '#a8ffd8',
  floor_polygon: [[0, 0], [4, 0], [4, 4], [0, 4]], ceiling_height_m: 2.5 };
const light = (id, name, pos, extra = {}) => ({ id, type: 'light', name, ha_entity_id: 'light.' + id.replace(/-/g, '_'), pos,
  yaw_rad: 0, height_preset: pos[2] >= 2 ? 'ceiling' : 'custom', room_id: STUDY.id, controllable: true,
  confidence: .5, source: 'proposed', updated_at: STAMP, ...extra });

/** The Echo model every Setup suite starts from: one room, two ceiling
 * lights, a lamp, a camera with a stream name, a point aim and wall art. */
function authoredEchoModel(seed) {
  return { ...seed, zones: [{ ...STUDY }],
    devices: [
      light('authored-ceiling-a', 'Authored ceiling A', [1.2, 1, 2.35]),
      light('authored-ceiling-b', 'Authored ceiling B', [2.8, 2.6, 2.35]),
      light('authored-lamp', 'Authored lamp', [3.4, .6, .8]),
      { id: 'authored-camera', type: 'camera', name: 'Authored camera', ha_entity_id: 'camera.authored_study',
        pos: [.2, .2, 2.2], yaw_rad: .78, height_preset: 'custom', room_id: STUDY.id, controllable: true,
        confidence: 1, source: 'manual', updated_at: STAMP, camera: { frigate_name: 'authored_study' } },
    ],
    targets: [
      { id: 'authored-reading-point', name: 'Authored reading point', category: 'custom', shape: 'point',
        pos: [1.4, 1.7, .7], normal: [0, 0, 1], up: [0, 1, 0], room_id: STUDY.id, source: 'manual', confidence: 1, updated_at: STAMP },
      { id: 'authored-wall-art', name: 'Authored wall art', category: 'art', shape: 'surface',
        pos: [2, 3.99, 1.4], normal: [0, -1, 0], up: [0, 0, 1], size_m: [.9, .65], room_id: STUDY.id,
        source: 'manual', confidence: 1, updated_at: STAMP },
    ] };
}

function writePack(packs, pack, overrides = {}) {
  for (const [name, value] of Object.entries(overrides)) {
    pack.files[name] = Buffer.isBuffer(value) ? value : Buffer.from(JSON.stringify(value));
    pack.manifest.assets[name] = { path: name, bytes: pack.files[name].length, sha256: hash(pack.files[name]) };
  }
  pack.files['content-pack.json'] = Buffer.from(JSON.stringify(pack.manifest));
  for (const name of [...Object.keys(overrides), 'content-pack.json']) {
    fs.writeFileSync(path.join(packs, pack.site, pack.revision, name), pack.files[name]);
  }
}

/** Installs both authored homes under run/. `echoOverrides` may replace
 * pack files (for example a mesh with furniture) before sealing. */
function installSetupRooms(run, { echoOverrides = {}, echoModel = authoredEchoModel } = {}) {
  const packs = path.join(run, 'packs'), privateRoot = path.join(run, 'private');
  const echo = installPack(packs, 'echo'), victoria = installPack(packs, 'victoria');
  echo.seed = echoModel(echo.seed);
  writePack(packs, echo, { 'seed-model.json': echo.seed, ...echoOverrides });
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1,
    packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  const packFiles = [echo, victoria].flatMap(item => Object.keys(item.files).map(name => path.join(packs, item.site, item.revision, name)))
    .concat([path.join(packs, 'echo/ACTIVE'), path.join(packs, 'victoria/ACTIVE'), path.join(packs, 'index.json')]);
  return { run, packs, privateRoot, echo, victoria, packFiles,
    registrationFile: path.join(privateRoot, 'registration.json'),
    packHashes: () => Object.fromEntries(packFiles.map(file => [file, hash(fs.readFileSync(file))])) };
}

function publicSources(root) {
  const result = new Map(), excluded = new Set(['node_modules', 'data', 'artifacts', 'private', 'recordings', 'logs']);
  function visit(directory, depth = 0) {
    assert.ok(depth <= 12);
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (entry.name.startsWith('.') || excluded.has(entry.name)) continue;
      const file = path.join(directory, entry.name), relative = path.relative(root, file).split(path.sep).join('/'), stat = fs.lstatSync(file);
      if (relative === 'assets/apartment' || relative.startsWith('assets/apartment/') || stat.isSymbolicLink()) continue;
      if (stat.isDirectory()) { visit(file, depth + 1); continue; }
      const vendor = /^(?:vendor|fonts|icons)\//.test(relative) && /\.(?:wasm|woff2?|ttf|otf|png|jpe?g|webp|svg|ico|ktx2|json)$/.test(entry.name);
      const rootAsset = directory === root && ['index.html', 'favicon.svg', 'favicon.ico', 'favicon-32.png', 'apple-touch-icon.png', 'site.webmanifest'].includes(entry.name);
      if (!/\.(?:js|mjs|jsx|css)$/.test(entry.name) && !vendor && !rootAsset) continue;
      result.set('/' + relative, { file, relative });
    }
  }
  visit(root); return result;
}

function browserOptions() {
  const { chromium } = require('playwright');
  const installed = fs.existsSync(chromium.executablePath());
  assert.ok(installed || process.platform === 'win32', 'pinned Chromium must exist on Linux');
  const args = ['--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'];
  if (process.platform === 'win32') args.unshift('--use-angle=d3d11');
  else if (process.platform === 'linux') args.unshift('--use-gl=angle', '--use-angle=swiftshader-webgl', '--enable-unsafe-swiftshader');
  return { headless: true, args, ...(process.platform === 'linux' ? { channel: 'chromium' } : !installed ? { channel: 'msedge' } : {}) };
}

/** Loopback server: worktree sources, the authored packs, an offline Home
 * Assistant proxy and the real registration gateway in test mode. Only a
 * registration PUT may write, and only when `allowRegistrationWrites`. */
async function startSetupServer(rooms, { allowRegistrationWrites = false, trackerModel = () => rooms.echo.seed } = {}) {
  const sources = publicSources(SRC);
  const log = { requests: [], forbidden: [], missing: [], errors: [], registrationPuts: [], served: {} };
  let handler;
  const server = http.createServer((req, res) => {
    try {
      const url = new URL(req.url, 'http://localhost'); log.requests.push({ path: url.pathname, method: req.method });
      if (url.pathname.startsWith('/api/apartment/v1/')) {
        if (req.method === 'PUT') {
          if (!allowRegistrationWrites) { log.forbidden.push({ path: url.pathname, method: req.method }); res.writeHead(403); res.end(); return; }
          const chunks = [];
          req.on('data', chunk => chunks.push(chunk));
          req.on('end', () => log.registrationPuts.push(Buffer.concat(chunks).toString('utf8')));
        }
        return handler(req, res);
      }
      if (!['GET', 'HEAD'].includes(req.method) || /\/api\/services\/|\/capture(?:\/|$)|\/acquisition(?:\/|$)|\/control(?:\/|$)|\/sessions\/[^/]+\/(?:start|stop)(?:\/|$)/.test(url.pathname)) {
        log.forbidden.push({ path: url.pathname, method: req.method }); res.writeHead(403); res.end(); return;
      }
      let body, type = 'application/octet-stream';
      if (url.pathname.startsWith('/api/perception-lab/v1/')) {
        const route = url.pathname.slice('/api/perception-lab/v1/'.length), value = route === 'capabilities'
          ? { site_id: 'victoria', cameras: [], live_previews: {}, live_videos: {}, recording_allowed: false, experiments: [], apartment_viewing: { provisioned: false } }
          : route === 'sessions' ? { site_id: 'victoria', version: 1, items: [] } : route === 'recordings' ? { items: [] } : null;
        if (value) { body = Buffer.from(JSON.stringify(value)); type = 'application/json'; }
      }
      if (url.pathname === '/content-packs/index.json') { body = fs.readFileSync(path.join(rooms.packs, 'index.json')); type = 'application/json'; }
      else {
        const pack = [rooms.echo, rooms.victoria].find(item => url.pathname.startsWith(item.base));
        if (pack) { body = pack.files[url.pathname.slice(pack.base.length)]; if (url.pathname.endsWith('.json')) type = 'application/json'; }
      }
      if (!body && url.pathname.startsWith('/proxy/')) {
        const value = url.pathname.endsWith('/model') || url.pathname.endsWith('/apartment_model') ? trackerModel()
          : url.pathname.endsWith('/api/states') ? [] : url.pathname.endsWith('/api/config') ? { unit_system: { length: 'm' } } : {};
        body = Buffer.from(JSON.stringify(value)); type = 'application/json';
      }
      const entry = sources.get(url.pathname === '/' ? '/index.html' : url.pathname);
      if (!body && entry) {
        body = fs.readFileSync(entry.file); log.served[entry.relative] = hash(body);
        type = /\.(?:js|mjs|jsx)$/.test(entry.file) ? 'text/javascript' : entry.file.endsWith('.html') ? 'text/html'
          : entry.file.endsWith('.css') ? 'text/css' : /\.(?:json|webmanifest)$/.test(entry.file) ? 'application/json' : entry.file.endsWith('.svg') ? 'image/svg+xml' : type;
      }
      if (!body) { if (/\.(?:js|mjs|jsx|css|wasm)$/.test(url.pathname)) log.missing.push(url.pathname); res.writeHead(404); res.end(); return; }
      res.writeHead(200, { 'Content-Type': type, 'Content-Length': body.length, 'Cache-Control': 'no-store' }); res.end(req.method === 'HEAD' ? undefined : body);
    } catch (error) { log.errors.push('authored HTTP handler: ' + error.message); res.writeHead(500); res.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  handler = createEquipmentRegistrationGateway({ env: { NODE_ENV: 'test', HOME_WEB_EQUIPMENT_REGISTRATION_ENABLED: '1',
    HOME_WEB_EQUIPMENT_REGISTRATION_ROOT: rooms.privateRoot, HOME_WEB_EQUIPMENT_REGISTRATION_ORIGIN: origin, HOME_CONTENT_PACKS_DIR: rooms.packs },
    authenticate: req => req.headers.cookie?.includes('authored_owner_session=1') === true });
  return { origin, log, close: async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); } };
}

/** A browser context fenced to the loopback origin, with the authored owner
 * cookie and the same Home boot preferences the shell suites use. */
async function openSetupContext(browser, origin, log, { viewport = { width: 1366, height: 768 }, site = 'echo', theme = 'dark', fixedTime = null, mobile = false } = {}) {
  const context = await browser.newContext({ viewport, deviceScaleFactor: 1, serviceWorkers: 'block', isMobile: mobile, hasTouch: mobile });
  await context.addCookies([{ name: 'authored_owner_session', value: '1', url: origin }]);
  await context.addInitScript(({ site, theme }) => {
    localStorage.setItem('apartment3d.site', site); localStorage.setItem('home.perf.backgroundWarmup', 'off');
    if (theme === 'light') localStorage.setItem('hg-prefs', JSON.stringify({ theme: 'light' }));
    window.HG_DISABLE_SERVICE_WORKER = true;
  }, { site, theme });
  await context.route('**/*', route => {
    const request = route.request(), url = new URL(request.url());
    if (['data:', 'blob:'].includes(url.protocol)) return route.continue();
    if (url.origin !== origin) {
      const harmless = ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) && request.method() === 'GET' && url.pathname === '/healthz';
      if (!harmless) log.forbidden.push({ origin: url.origin, path: url.pathname, method: request.method() });
      return route.abort();
    }
    return route.continue();
  });
  await context.routeWebSocket('**/*', socket => {
    const url = new URL(socket.url());
    if (!(url.host === new URL(origin).host && url.pathname === '/proxy/tracker/ws/tracks')) log.forbidden.push({ websocket: socket.url() });
    socket.close();
  });
  const page = await context.newPage(); page.setDefaultTimeout(15000);
  page.on('pageerror', error => log.errors.push(error.message));
  if (fixedTime) await page.clock.setFixedTime(new Date(fixedTime));
  return { context, page };
}

/** An authored Home Assistant WebSocket for one context: auth, registries,
 * states with the Travel Mode helper, and a spy on every call_service. The
 * app connects through its remembered-login path to `${origin}/proxy/ha`.
 * Nothing leaves the browser; no real Home Assistant is involved. */
async function mockHomeAssistant(context, origin, { travel = 'off', lights = [], registry = lights } = {}) {
  const ha = { travel, frames: [], calls: [], sockets: [], subscriptions: [] };
  const states = () => [
    ...(ha.travel === null ? [] : [{ entity_id: 'input_boolean.living_lights_travel_mode', state: ha.travel, attributes: {} }]),
    ...lights.map(light => ({ entity_id: light.entity_id, state: 'off', attributes: { friendly_name: light.name, supported_color_modes: ['brightness'] },
      last_updated: '2026-10-19T00:00:00.000Z' })),
  ];
  await context.addInitScript(base => {
    localStorage.setItem('hg-prefs', JSON.stringify({ endpoint: base + '/proxy/ha' }));
    localStorage.setItem('home.ha.remembered-login.v1', JSON.stringify({ endpoint: base + '/proxy/ha', token: 'authored-test-token' }));
  }, origin);
  await context.routeWebSocket(new RegExp('^ws://' + new URL(origin).host.replace(/[.]/g, '\\.') + '/proxy/ha/api/websocket$'), socket => {
    ha.sockets.push(socket);
    socket.send(JSON.stringify({ type: 'auth_required', ha_version: '2026.10.0' }));
    socket.onMessage(raw => {
      const message = JSON.parse(raw); ha.frames.push(message);
      if (message.type === 'auth') { socket.send(JSON.stringify({ type: 'auth_ok', ha_version: '2026.10.0' })); return; }
      let result = null;
      if (message.type === 'get_states') result = states();
      else if (message.type === 'config/entity_registry/list') result = registry.map(light => ({ entity_id: light.entity_id, name: light.name, platform: 'authored', area_id: null, device_id: null }));
      else if (message.type === 'config/area_registry/list' || message.type === 'config/device_registry/list') result = [];
      else if (message.type === 'subscribe_events') ha.subscriptions.push({ socket, id: message.id, event_type: message.event_type });
      else if (message.type === 'call_service') ha.calls.push({ domain: message.domain, service: message.service, service_data: message.service_data, target: message.target });
      socket.send(JSON.stringify({ id: message.id, type: 'result', success: true, result }));
    });
  });
  /** Changes the helper in Home Assistant; `announce` also pushes the
   * state_changed event the app subscribes to. */
  ha.setTravel = (state, { announce = true } = {}) => {
    ha.travel = state;
    if (!announce) return;
    for (const subscription of ha.subscriptions.filter(item => !item.event_type || item.event_type === 'state_changed')) {
      subscription.socket.send(JSON.stringify({ id: subscription.id, type: 'event', event: { event_type: 'state_changed',
        data: { entity_id: 'input_boolean.living_lights_travel_mode', new_state: state === null ? null : { entity_id: 'input_boolean.living_lights_travel_mode', state, attributes: {} } } } }));
    }
  };
  return ha;
}

/** Real Home shell navigation used by every Setup suite. */
function homeShell(page, origin) {
  const waitSite = site => page.waitForFunction(site => window.__HOME_APARTMENT_SITE_API?.readback().phase === 'ready'
    && window.__APT_ENGINE?.siteId === site && !!document.querySelector('.apt-home-setup'), site, { timeout: 45000 });
  return {
    async boot(layout = '1', site = 'echo') {
      await page.goto(origin + '/?apartmentRedesign=' + layout + '&worldZoom=off', { waitUntil: 'domcontentloaded' });
      if (layout === '1') await page.locator('.apt-home-shell[data-home-layout="unified"]').waitFor({ timeout: 45000 });
      if (layout === '1') await waitSite(site);
      else await page.waitForFunction(site => window.__HOME_APARTMENT_SITE_API?.readback().phase === 'ready' && window.__APT_ENGINE?.siteId === site, site, { timeout: 45000 });
    },
    waitSite,
    async selectHome(site) {
      await page.getByRole('group', { name: 'Home', exact: true })
        .getByRole('button', { name: site === 'victoria' ? 'Victoria' : 'Echo Park', exact: true }).click();
      await waitSite(site);
    },
    setupButton: () => page.locator('.apt-home-topbar .apt-home-setup'),
    rail: () => page.getByRole('navigation', { name: 'Setup tools', exact: true }),
    inspector: () => page.locator('[data-apt-setup-inspector]'),
    header: () => page.locator('[data-apt-setup-inspector] .apt-ui-title'),
    async openSetup() { await page.locator('.apt-home-topbar .apt-home-setup').click(); await page.locator('[data-apt-setup-inspector]').waitFor(); },
    async tool(id) { await page.locator(`.apt-setup-rail [data-setup-tool="${id}"]`).click(); },
  };
}

export { authoredEchoModel, installSetupRooms, startSetupServer, openSetupContext, mockHomeAssistant, browserOptions, homeShell, STAMP, hash };
