// The live den view in Home (app/src/home-den-live-view.mjs) and look-only mode, through the REAL gateway
// (web-gateway/server.mjs) on an authored den pack, with a fake den-live behind it.
// - A relay on 127.0.0.1 writes a PROXY line naming a tailnet address first, as Tailscale Serve does, so the browser
//   is a tailnet device. Every upstream target the gateway has points at the fake on 127.0.0.1, and the browser may
//   reach only the relay: no camera, host, Home Assistant or Tailscale is contacted.
// - Look-only (no session): the den view opens, in Lighting, with markers, the look-only label, no write control, no
//   non-GET request, nothing off the gateway's look-only list and no 401; again at phone size.
// - Signed in (Basic, as the gateway test signs in): markers in full Home's den view, and the stream closes when the
//   view leaves the den.
// - The chip's banners: another scan revision draws no markers; camera offline; too dark; a stream with heartbeats
//   only reads as stopped; markers return when events do.
//   node --test tools/test-den-live-look-only-browser.mjs
// DEN_LIVE_SCREENSHOTS=<folder> also saves each run's view there.
// HOME_DEN_BODY_BACKENDS=webgl,webgpu adds the native WebGPU proof on a machine with an available adapter.
import test, { after, before } from 'node:test';
import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import Core from '../app/src/home-equipment-registration.js';
import { DEN_LIVE_PREFIX, DEN_SITE } from '../web-gateway/den-live-route.mjs';
import { lookOnlyKind } from '../web-gateway/look-only.mjs';
import { initRegistration, readActiveBinding } from '../web-gateway/equipment-registration.mjs';
import { SITES } from '../web-gateway/home-location.mjs';
import { authoredDocument, installPack } from './fixtures/apartment-home-shell-fixtures.mjs';
import { measuredBodyEvent } from './fixtures/den-measured-body.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const OTHER_SITE = SITES.find(site => site !== DEN_SITE);
const USER = 'owner', PASSWORD = 'correct-horse-battery-staple';
const TAILNET_CLIENT = '100.101.102.103';
const STREAM = `${DEN_LIVE_PREFIX}/v1/homes/den/stream`, STATE = `${DEN_LIVE_PREFIX}/v1/homes/den/state`;
const BODY = `${DEN_LIVE_PREFIX}/v1/homes/den/body`;
const LABELS = `${DEN_LIVE_PREFIX}/v1/homes/den/labels`;
const TARGETS = ['HOME_WEB_HA_TARGET', 'HOME_WEB_METRICS_TARGET', 'HOME_WEB_VLLM_TARGET', 'HOME_WEB_VISION_TARGET',
  'HOME_WEB_INTELLIGENCE_TARGET', 'HOME_WEB_SUPERVISOR_TARGET', 'HOME_WEB_S2S_TARGET', 'HOME_WEB_TRACKER_TARGET',
  'HOME_WEB_VIDEO_LABELER_TARGET', 'HOME_WEB_FRIGATE_TARGET', 'HOME_WEB_AGENT_BFF_TARGET'];
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const freePort = () => new Promise(resolve => {
  const s = net.createServer(); s.listen(0, '127.0.0.1', () => { const { port } = s.address(); s.close(() => resolve(port)); });
});

let run, den, upstream, gateway, relay, origin, browser;
// What the fake den-live sends: live | other-scan | camera-offline | too-dark | heartbeats-only.
const fake = { scenario: 'live', seq: 0, streams: new Set(), opened: 0, bodies: new Set(), bodiesOpened: 0, bodySeq: 0, bodyReplay: null, seen: [] };

function bodyData(event) { return `id: ${event.seq}\nevent: body\ndata: ${JSON.stringify(event)}\n\n`; }
function emitBody(options = {}) {
  const event = measuredBodyEvent({ revision: den.revision, captured: Date.now() / 1000 - 0.05, seq: ++fake.bodySeq, ...options });
  for (const client of fake.bodies) client.write(bodyData(event));
  return event;
}

function peopleEvent() {
  const now = Date.now() / 1000, base = { schema: 'den-live-people-1', home: 'den', seq: ++fake.seq, transform_id: 'authored-transform-1',
    content_revision: den.revision, capture_ts: now - .42, emitted_ts: now, mode: 'rgb', camera: { status: 'ok', why: null },
    health: { fps_in: 4, dropped_per_min: 0, lag_s_median: .42, transit_s_median: .08 },
    tracks: [{ track_id: 'e1-trk-1', state: 'confirmed', xy_m: [1.2, 1.5], sigma_m: .1, heading_deg: 90, age_s: 0 },
      { track_id: 'e1-trk-2', state: 'coasting', xy_m: [2.8, 2.6], sigma_m: .2, heading_deg: null, age_s: .4 }] };
  if (fake.scenario === 'other-scan') return { ...base, content_revision: `${den.revision}-other` };
  if (fake.scenario === 'camera-offline') return { ...base, capture_ts: null, camera: { status: 'offline', why: 'camera unreachable' }, tracks: [] };
  if (fake.scenario === 'too-dark') return { ...base, mode: 'none', camera: { status: 'ok', why: 'too dark' }, tracks: [] };
  return base;
}

function startUpstream() {
  const server = http.createServer((req, res) => {
    fake.seen.push(`${req.method} ${req.url}`);
    if (req.method === 'GET' && req.url === STREAM) {
      res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store' });
      res.write('retry: 1000\n\n');
      fake.streams.add(res); fake.opened += 1;
      let beat = 0;
      const timer = setInterval(() => {
        if (fake.scenario === 'heartbeats-only') { if (++beat % 4 === 0) res.write(': heartbeat\n\n'); return; }
        const event = peopleEvent();
        res.write(`id: ${event.seq}\nevent: people\ndata: ${JSON.stringify(event)}\n\n`);
      }, 250);
      req.on('close', () => { clearInterval(timer); fake.streams.delete(res); });
      return;
    }
    if (req.method === 'GET' && req.url === BODY) {
      res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store', 'X-Home-Den-Body-Schema': 'den-live-body-2' });
      res.write(`event: body_clock\ndata: ${JSON.stringify({ server_ts: Date.now() / 1000 })}\n\n`);
      if (fake.bodyReplay) res.write(bodyData(fake.bodyReplay));
      fake.bodies.add(res); fake.bodiesOpened += 1;
      req.on('close', () => fake.bodies.delete(res));
      return;
    }
    const body = req.url === STATE ? { people: peopleEvent() } : req.url === LABELS
      ? { schema: 'den-room-labels-1', home: 'den', room_id: 'den', content_revision: den.revision, revision: 0, saved_at: null, labels: [] }
      : { ok: false, error: 'not_served_by_fake' };
    res.writeHead(req.url === STATE || req.url === LABELS ? 200 : req.url.startsWith('/api/lighting-brain/') ? 503 : 404, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(body));
  });
  return new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(server)));
}

/* The PROXY line Tailscale Serve writes, then the browser's bytes both ways. */
function startRelay(port, gatewayPort) {
  const server = net.createServer(client => {
    const up = net.connect(gatewayPort, '127.0.0.1', () => {
      up.write(`PROXY TCP4 ${TAILNET_CLIENT} 100.64.0.2 ${client.remotePort} 443\r\n`);
      client.pipe(up); up.pipe(client);
    });
    up.on('error', () => client.destroy()); client.on('error', () => up.destroy());
  });
  return new Promise(resolve => server.listen(port, '127.0.0.1', () => resolve(server)));
}

function browserOptions() {
  const installed = fs.existsSync(chromium.executablePath());
  assert.ok(installed || process.platform === 'win32', 'pinned Chromium must exist on Linux');
  const args = ['--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'];
  const native = (process.env.HOME_DEN_BODY_BACKENDS || '').split(',').includes('webgpu');
  if (native) args.push('--enable-unsafe-webgpu', '--ignore-gpu-blocklist');
  else if (process.platform === 'win32') args.unshift('--use-angle=d3d11');
  else if (process.platform === 'linux') args.unshift('--use-angle=swiftshader', '--enable-unsafe-swiftshader');
  const channel = !installed && process.platform === 'win32' ? 'msedge' : native || process.platform === 'linux' ? 'chromium' : undefined;
  return { headless: true, args, channel };
}

before(async () => {
  run = fs.mkdtempSync(path.join(os.tmpdir(), 'home-den-live-look-only-'));
  const packs = path.join(run, 'packs'), privateRoot = path.join(run, 'registration');
  fs.mkdirSync(packs); fs.mkdirSync(privateRoot);
  den = installPack(packs, DEN_SITE);
  const other = installPack(packs, OTHER_SITE);
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1, packs: [den, other].map(item =>
    ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  upstream = await startUpstream();
  const target = `http://127.0.0.1:${upstream.address().port}`;
  const gatewayPort = await freePort(), servePort = await freePort(), relayPort = await freePort();
  origin = `http://127.0.0.1:${relayPort}`;
  const env = { ...process.env, NODE_ENV: 'test', HOME_WEB_HOST: '127.0.0.1', HOME_WEB_PORT: String(gatewayPort),
    HOME_WEB_AUTH_REQUIRED: '1', HOME_WEB_AUTH_FILE: path.join(run, 'missing-auth.json'), HOME_WEB_BASIC_AUTH: `${USER}:${PASSWORD}`,
    HOME_WEB_ENABLE_LEGACY_HA_PROXY: '1', HOME_WEB_ASSET_VERSION: '', HOME_CONTENT_PACKS_DIR: packs,
    HOME_WEB_EQUIPMENT_REGISTRATION_ENABLED: '1', HOME_WEB_EQUIPMENT_REGISTRATION_ROOT: privateRoot,
    HOME_WEB_EQUIPMENT_REGISTRATION_ORIGIN: origin, HOME_WEB_DEN_LIVE_TARGET: `${target}${DEN_LIVE_PREFIX}`,
    HOME_WEB_LIGHTING_BRAIN_TARGET: `${target}/api/lighting-brain`, HOME_WEB_DEN_LIVE: '1', HOME_WEB_LOOK_ONLY: '1',
    HOME_WEB_SERVE_PORT: String(servePort), HOME_WEB_SITE_LANS: '', HOME_WEB_LOCATION_PERSON: '', HOME_WEB_DEN_LAB_ORIGIN: '' };
  for (const name of TARGETS) env[name] = target;
  gateway = childProcess.spawn(process.execPath, [path.join(ROOT, 'web-gateway/server.mjs')], { cwd: ROOT, env, stdio: ['ignore', 'ignore', 'pipe'] });
  let stderr = '';
  gateway.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-4000); });
  relay = await startRelay(relayPort, servePort);
  let ready = false;
  for (let i = 0; i < 100 && !ready; i++) {
    try { ready = (await fetch(`http://127.0.0.1:${gatewayPort}/healthz`)).ok; } catch { /* starting */ }
    if (!ready) await sleep(100);
  }
  assert.ok(ready, 'the gateway starts with den-live and look-only on: ' + stderr);
  browser = await chromium.launch(browserOptions());
});

after(async () => {
  await browser?.close();
  gateway?.kill();
  relay?.close();
  for (const res of [...fake.streams, ...fake.bodies]) res.destroy();
  upstream?.closeAllConnections?.(); upstream?.close();
  if (run) fs.rmSync(run, { recursive: true, force: true });
});

/* A page on the relay, with every request, response and socket it makes. */
async function openHome({ viewport = { width: 1366, height: 768 }, signedIn = false, mobile = false, url = '/' } = {}) {
  const context = await browser.newContext({ viewport, isMobile: mobile, hasTouch: mobile, serviceWorkers: 'block',
    ...(signedIn ? { extraHTTPHeaders: { Authorization: `Basic ${Buffer.from(`${USER}:${PASSWORD}`).toString('base64')}` } } : {}) });
  const seen = { requests: [], responses: [], sockets: [], external: [], errors: [] };
  // A look-only device starts as a fresh phone would, with nothing stored. Signed in, Home opens on the den, quietly.
  if (signedIn) await context.addInitScript(site => {
    try {
      localStorage.setItem('home.perf.backgroundWarmup', 'off'); localStorage.setItem('apartment3d.prewarm', 'off');
      localStorage.setItem('apartment3d.site', site);
    } catch (_) { /* storage off */ }
  }, DEN_SITE);
  await context.route('**/*', route => {
    const target = new URL(route.request().url());
    if (['data:', 'blob:'].includes(target.protocol) || target.origin === origin) return route.continue();
    seen.external.push(`${route.request().method()} ${target.origin}${target.pathname}`);
    return route.abort();
  });
  await context.routeWebSocket('**/*', socket => { seen.sockets.push(socket.url()); socket.close(); });
  const page = await context.newPage();
  page.setDefaultTimeout(20000);
  const local = url => url.startsWith(origin) ? url.slice(origin.length) : null;
  page.on('request', request => { const at = local(request.url()); if (at) seen.requests.push({ method: request.method(), url: at }); });
  page.on('response', response => { const at = local(response.url()); if (at) seen.responses.push({ status: response.status(), url: at }); });
  page.on('pageerror', error => seen.errors.push(error.message));
  await page.goto(origin + url, { waitUntil: 'domcontentloaded' });
  return { context, page, seen };
}
const denLive = page => page.evaluate(() => window.HomeDenLiveView?.status() || null);
async function waitForMarkers(page, timeout = 90000) {
  await page.waitForFunction(() => (window.HomeDenLiveView?.status().layer?.tracks ?? 0) > 0, null, { timeout });
  return denLive(page);
}
async function waitForBanner(page, banner) {
  await page.waitForFunction(value => window.HomeDenLiveView?.status().banner === value, banner, { timeout: 12000 });
}
const WRITE_CONTROLS = {
  'fixture inspector': '.apt-ui-inspector, [aria-label="Room controls"], [aria-label="Room lighting inspector"], [aria-label="Fixture lighting inspector"]',
  'aim panel or reticle': '.apt-ui-advanced-aim, [data-apt-aim-reticle]:not([hidden]), [data-apt-aim-bar]:not([hidden])',
  'Lab camera dock': '[data-apartment-den-perception], [aria-label="Den camera dock"], .pl-inline-recording',
  'site switcher and menu': '.apt-home-switcher, .apt-home-menu, [aria-label="Home menu"]',
  'Home column, chat and camera panels': '.apt-home-panel, .apt-home-chat, [data-home-chat-surface], .apt-home-camera',
  'Setup and marks': '[data-apt-edit-ui], .apt-home-setup',
  'live camera feed': '[data-apartment-live-feed], [aria-label="Live camera feed"]',
};
const WRITE_WORDS = /Return to Auto|Select desk surface|Mark sofa|Save desk target|Perception Lab|Home Assistant|Start session|Setup/;
async function writeControls(page) {
  return page.evaluate(([selectors, words]) => {
    const found = Object.entries(selectors).filter(([, selector]) => document.querySelector(selector)).map(([name]) => name);
    const text = [...document.querySelectorAll('button, [role="menuitem"], [role="tab"], a')].map(node => node.textContent.trim())
      .filter(value => new RegExp(words).test(value));
    return [...found, ...text.map(value => `control "${value}"`)];
  }, [WRITE_CONTROLS, WRITE_WORDS.source]);
}
async function screenshot(page, name) {
  const folder = process.env.DEN_LIVE_SCREENSHOTS;
  if (folder) { fs.mkdirSync(folder, { recursive: true }); await page.screenshot({ path: path.join(folder, `${name}.png`) }); }
}
/* Nothing but GETs, all on the gateway's look-only list, none refused. */
function assertLookOnlyTraffic(seen, name) {
  const kindOf = request => request.method === 'GET' && request.url === BODY ? 'den-live-body' : lookOnlyKind(request.method, request.url);
  const kinds = {};
  for (const request of seen.requests) {
    const kind = kindOf(request) || 'OFF THE LIST', at = request.url.replace(/\?.*$/, '');
    (kinds[kind] ||= new Set()).add(kind === 'static' ? at.replace(/[^/]+$/, '*') : at);
  }
  process.stdout.write(`# ${name}: ${seen.requests.length} requests, by look-only kind\n` + Object.entries(kinds)
    .map(([kind, urls]) => `#   ${kind}: ${[...urls].sort().join(' ')}\n`).join(''));
  const writes = seen.requests.filter(request => request.method !== 'GET');
  assert.deepEqual(writes, [], 'a look-only page sends no non-GET request');
  const off = [...new Set(seen.requests.filter(request => kindOf(request) === null).map(request => request.url))];
  assert.deepEqual(off, [], 'every request is on the gateway look-only list');
  assert.deepEqual(seen.responses.filter(response => response.status === 401), [], 'no 401');
  assert.deepEqual(seen.sockets, [], 'no WebSocket');
  assert.deepEqual(seen.external, [], 'nothing beyond this origin');
  assert.deepEqual(seen.errors, [], 'no page error');
}

test('look-only: the den view opens in Lighting with live markers, no write control, and only listed GETs', { timeout: 170000 }, async () => {
  fake.scenario = 'live';
  const { context, page, seen } = await openHome();
  try {
    const status = await waitForMarkers(page);
    const view = await page.evaluate(() => ({ title: document.title, label: document.querySelector('[data-den-live-label]')?.textContent,
      site: window.__APT_ENGINE?.siteId, revision: window.__APT_ENGINE?.contentRevision, lookOnly: window.HomeLookOnly?.active,
      chip: document.querySelector('[data-den-live-chip]')?.innerText, markers: window.HomeDenLiveView.markers() }));
    assert.equal(view.lookOnly, true);
    assert.equal(view.site, DEN_SITE, 'the den view opens');
    assert.equal(view.revision, den.revision);
    assert.equal(view.title, 'Den · live · look-only');
    assert.equal(view.label, 'Den · live · look-only', 'the look-only label shows');
    assert.ok(await page.locator('[data-den-live-label]').isVisible());
    assert.equal(await page.locator('[data-den-live-signin]').getAttribute('href'), '/auth', 'sign-in is one tap away');
    assert.ok(await page.locator('[data-den-live-signin]').isVisible(), 'the sign-in link shows');
    assert.equal(status.stream, 'open'); assert.equal(status.mounted, true); assert.equal(status.banner, '');
    assert.ok(view.markers.length === 2 && view.markers.every(marker => marker.xy_m.every(v => v > 0 && v < 4)), JSON.stringify(view.markers));
    assert.match(view.chip, /Den · live · look-only/);
    assert.match(view.chip, /2 people · 0\.50 s behind/);
    await page.locator('[aria-label="Lighting"][aria-pressed="true"]').waitFor({ timeout: 20000 });
    assert.equal(fake.streams.size, 1, 'one stream');
    await page.waitForFunction(() => window.HomeDenLiveView.status().body?.stream === 'open');
    assert.equal(fake.bodies.size, 1, 'a no-session tailnet client opens the separate body stream');
    await screenshot(page, 'look-only');

    // A press on a fixture's label or body selects nothing and opens nothing.
    const labels = page.locator('[data-apt-fixture-label]:not([hidden])'), label = labels.first();
    assert.ok(await labels.count() > 0, 'the den\'s fixtures are labelled, so a press can be tried');
    {
      await label.click({ force: true });
      const box = await label.boundingBox();
      if (box) { await page.mouse.move(box.x + box.width / 2, box.y + box.height + 30); await page.mouse.down(); await page.mouse.up(); }
      await sleep(600);
    }
    assert.deepEqual(await writeControls(page), [], 'no write control is mounted');
    assert.equal(await page.locator('[data-apt-fixture-label][data-state="selected"]').count(), 0, 'nothing is selected');

    // The chip's banners, on the same page.
    fake.scenario = 'other-scan';
    await waitForBanner(page, 'other-scan');
    await page.waitForFunction(() => window.HomeDenLiveView.status().layer?.tracks === 0, null, { timeout: 8000 });
    assert.match(await page.locator('[data-den-live-chip]').innerText(), /Another scan revision than the camera is registered to: no markers/);
    await sleep(1000);
    assert.equal((await denLive(page)).layer.tracks, 0, 'an event in another scan revision draws no markers');
    fake.scenario = 'camera-offline';
    await waitForBanner(page, 'camera-offline');
    assert.match(await page.locator('[data-den-live-chip]').innerText(), /Camera offline: camera unreachable/);
    fake.scenario = 'too-dark';
    await waitForBanner(page, 'too-dark');
    assert.match(await page.locator('[data-den-live-chip]').innerText(), /Too dark/);
    fake.scenario = 'heartbeats-only';
    await waitForBanner(page, 'stream-stopped');
    assert.match(await page.locator('[data-den-live-chip]').innerText(), /Stream stopped/);
    await screenshot(page, 'look-only-stopped');
    fake.scenario = 'live';
    await waitForMarkers(page, 10000);
    await waitForBanner(page, '');
    assert.equal(fake.streams.size, 1, 'still one stream');

    assertLookOnlyTraffic(seen, 'look-only');
    assert.ok(seen.requests.some(request => request.url === STREAM), 'the page read den-live through the gateway');
  } finally {
    await context.close();
  }
  await sleep(500);
  assert.equal(fake.streams.size, 0, 'closing the page closes its stream');
});

const bodyBackends = (process.env.HOME_DEN_BODY_BACKENDS || 'webgl').split(',');
for (const backend of bodyBackends) {
  test(`${backend}: measured body draws, clears, expires and stays out of transitions`, { timeout: 170000 }, async () => {
    fake.scenario = 'live';
    const { context, page } = await openHome({ url: `/?renderer=${backend}` });
    const waitForBody = async (reason = null) => page.waitForFunction(expected => {
      const state = window.HomeDenLiveView?.status().body;
      const root = window.__APT_ENGINE?.apartmentRoot.getObjectByName('home-apartment-body-owned');
      if (expected !== null) return state?.reason === expected && state.layer?.points === 0;
      const recording = root?.getObjectByName(window.__APT_ENGINE.rendererBackend === 'webgl' ? 'body-recording' : 'body-recording-webgpu');
      return state?.last_paint_age_s != null && (recording?.isSprite ? recording.count : recording?.geometry?.drawRange.count) > 0;
    }, reason);
    try {
      await waitForMarkers(page);
      await page.waitForFunction(() => window.HomeDenLiveView.status().body?.stream === 'open');
      assert.equal(await page.evaluate(() => window.__APT_ENGINE.rendererBackend), backend === 'webgpu-webgl' ? 'webgpu-webgl2' : backend);
      const canvas = page.locator('[data-home-apartment-view] canvas').first();
      await canvas.screenshot({ animations: 'disabled' });
      const frame = emitBody();
      await waitForBody();
      assert.equal((await denLive(page)).body.layer.points, frame.points.count);
      const pixelProof = await page.evaluate(async () => {
        const engine = window.__APT_ENGINE;
        const node = engine.apartmentRoot.getObjectByName('home-apartment-body-owned');
        const camera = engine.camera, renderer = engine.renderer, pose = new camera.constructor().copy(camera, false), render = renderer.render;
        // Pin both readbacks to the same camera; only this test's first capture opts the body in.
        renderer.render = function (scene, activeCamera) { return render.call(this, scene, activeCamera === camera ? pose : activeCamera); };
        try {
          node.userData.excludeFromApartmentCapture = false;
          const drawn = await engine.captureFrame({ scale: 1 });
          node.userData.excludeFromApartmentCapture = true;
          const empty = await engine.captureFrame({ scale: 1 });
          if (!drawn?.pixels || !empty?.pixels || drawn.pixels.length !== empty.pixels.length) return -1;
          let changed = 0;
          for (let i = 0; i < drawn.pixels.length; i += 4) {
            if (Math.abs(drawn.pixels[i] - empty.pixels[i]) + Math.abs(drawn.pixels[i + 1] - empty.pixels[i + 1]) + Math.abs(drawn.pixels[i + 2] - empty.pixels[i + 2]) > 5) changed += 1;
          }
          return changed;
        } finally { node.userData.excludeFromApartmentCapture = true; renderer.render = render; }
      });
      assert.ok(pixelProof > 12, `the renderer draws the measured figure into the canvas (${pixelProof} changed pixels)`);
      await screenshot(page, `measured-body-${backend}`);
      // Keep the camera fixed. Mark the apartment capture's draw so the next live render cannot skew the assertion.
      const captureWhileVisible = await page.evaluate(async () => {
        const engine = window.__APT_ENGINE;
        const node = engine.apartmentRoot.getObjectByName('home-apartment-body-owned');
        let drawn = 0, captureDraw = false;
        const recording = node.getObjectByName(engine.rendererBackend === 'webgl' ? 'body-recording' : 'body-recording-webgpu');
        const before = recording.onBeforeRender, render = engine.renderer.render;
        const evidence = [];
        engine.renderer.render = function (scene, camera) {
          if (scene === engine.scene && camera === engine.camera) evidence.push(node.visible);
          captureDraw = scene === engine.scene && camera === engine.camera && node.visible === false;
          try { return render.apply(this, arguments); } finally { captureDraw = false; }
        };
        recording.onBeforeRender = function (...args) {
          if (captureDraw) drawn += 1;
          before.apply(this, args);
        };
        try { return { ok: !!(await engine.captureFrame({ scale: 0.25 })), drawn, hiddenForCapture: evidence.includes(false), restored: node.visible }; }
        finally { recording.onBeforeRender = before; engine.renderer.render = render; }
      });
      assert.deepEqual(captureWhileVisible, { ok: true, drawn: 0, hiddenForCapture: true, restored: true });
      const zoomed = await page.evaluate(() => {
        const { rig } = window.__APT_ENGINE;
        return rig.stepZoom(1, 0).accepted;
      });
      assert.equal(zoomed, true, 'the close view is reachable from room zoom');
      await waitForBody('stale');
      emitBody();
      await waitForBody();
      await screenshot(page, `measured-body-${backend}-close`);
      emitBody({ reason: 'ambiguous' });
      await waitForBody('ambiguous');
      assert.equal((await denLive(page)).layer.tracks, 2, 'a body clear leaves anonymous markers');

      const earlier = emitBody({ epoch: 2 });
      await waitForBody();
      await waitForBody('stale');
      fake.bodyReplay = earlier;
      const connections = fake.bodiesOpened;
      for (const res of fake.bodies) res.destroy();
      await page.waitForFunction(() => window.HomeDenLiveView.status().body?.stream === 'none');
      await page.waitForFunction(() => window.HomeDenLiveView.status().body?.stream === 'open');
      assert.ok(fake.bodiesOpened > connections);
      assert.equal((await denLive(page)).body.layer.points, 0, 'replaying an expired frame cannot restore it');
      emitBody({ epoch: 2 });
      await waitForBody();
      emitBody({ epoch: 2, captured: Date.now() / 1000 + 2 });
      await waitForBody('stale');
      emitBody({ epoch: 2, revision: `${den.revision}-old` });
      await waitForBody('scope_changed');
      assert.equal((await denLive(page)).layer.tracks, 2);
    } finally {
      fake.bodyReplay = null;
      await context.close();
    }
  });
}

test('look-only at phone size: the den view, markers and chip fit 390x844 beside Home\'s controls', { timeout: 170000 }, async () => {
  fake.scenario = 'live';
  const { context, page, seen } = await openHome({ viewport: { width: 390, height: 844 }, mobile: true });
  try {
    await waitForMarkers(page);
    assert.equal(await page.evaluate(() => window.__APT_ENGINE?.siteId), DEN_SITE);
    assert.ok(await page.locator('[data-den-live-label]').isVisible(), 'the look-only label shows');
    const boxes = await page.evaluate(() => {
      const box = node => { const r = node?.getBoundingClientRect(); return r && r.width > 0 ? { x: r.x, y: r.y, w: r.width, h: r.height } : null; };
      return { chip: box(document.querySelector('[data-den-live-chip]')), toolbar: box(document.querySelector('.apt-ui-room-toolbar')),
        zoom: box(document.querySelector('[aria-label="apartment zoom controls"]')), topbar: box(document.querySelector('.apt-home-topbar')),
        width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth };
    });
    const overlap = (a, b) => !!a && !!b && a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
    assert.ok(boxes.chip && boxes.chip.x >= 0 && boxes.chip.x + boxes.chip.w <= boxes.width, 'the chip is on screen: ' + JSON.stringify(boxes));
    for (const name of ['toolbar', 'zoom', 'topbar']) assert.ok(!overlap(boxes.chip, boxes[name]), `the chip doesn't cover the ${name}: ${JSON.stringify(boxes)}`);
    assert.ok(boxes.scroll <= boxes.width, 'no sideways scroll');
    assert.deepEqual(await writeControls(page), [], 'no write control is mounted');
    await screenshot(page, 'look-only-phone');
    assertLookOnlyTraffic(seen, 'look-only at phone size');
  } finally {
    await context.close();
  }
});

test('signed in: full Home\'s den view shows the markers, and leaving the den closes the stream', { timeout: 170000 }, async () => {
  fake.scenario = 'live';
  const { context, page, seen } = await openHome({ signedIn: true, url: '/?apartmentRedesign=1&worldZoom=off' });
  try {
    await waitForMarkers(page);
    const view = await page.evaluate(() => ({ lookOnly: window.HomeLookOnly, site: window.__APT_ENGINE?.siteId,
      chip: document.querySelector('[data-den-live-chip]')?.innerText, unified: !!document.querySelector('.apt-home-shell .apt-home-panel') }));
    assert.equal(view.lookOnly, undefined, 'a signed-in device is not look-only');
    assert.equal(view.site, DEN_SITE);
    assert.equal(view.unified, true, 'this is full Home');
    assert.match(view.chip, /Den · live/); assert.doesNotMatch(view.chip, /look-only/);
    assert.equal(fake.streams.size, 1);
    for (let i = 0; i < 40 && fake.bodies.size === 0; i++) await sleep(100);
    assert.equal(fake.bodies.size, 1, 'signed-in Home holds one body stream in the den view');
    await screenshot(page, 'signed-in');
    assert.deepEqual(seen.responses.filter(response => response.url.startsWith(DEN_LIVE_PREFIX) && response.status !== 200), []);

    // Another home: the den view is gone, and so are its stream and markers.
    await page.locator('.apt-home-switcher button[aria-pressed="false"]').first().click();
    await page.waitForFunction(() => window.HomeDenLiveView.status().stream === 'none', null, { timeout: 60000 });
    const left = await denLive(page);
    assert.equal(left.mounted, false); assert.equal(left.chip, false);
    await sleep(500);
    assert.equal(fake.streams.size, 0, 'den-live holds no stream for a device elsewhere in Home');
    assert.equal(fake.bodies.size, 0, 'nor a body stream');
  } finally {
    await context.close();
  }
});
