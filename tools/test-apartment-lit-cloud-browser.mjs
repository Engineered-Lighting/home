// Cloud view with Lighting on keeps the point cloud (2026-11-14, the owner: "When doing artificial lighting in my point
// cloud view, my entire point cloud disappears"). On a desktop-class page Cloud wears the x-ray look, which hides the
// cloud itself and draws its own copies. With Lighting on, the look stands down and the relit cloud draws in the Lit
// frame's scene-referred pass, on LIT_FRAME_LAYER (lit-frame.mjs). The look used to reset the cloud's whole layer mask
// every frame, dropping that layer, so the relit cloud drew in no pass. Real Home shell, renderer, authored packs and
// an authored lighting layer, served from this worktree.
//   node --test tools/test-apartment-lit-cloud-browser.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import Core from '../app/src/home-equipment-registration.js';
import { createEquipmentRegistrationGateway, initRegistration, readActiveBinding } from '../web-gateway/equipment-registration.mjs';
import { installPack, authoredDocument } from './fixtures/apartment-home-shell-fixtures.mjs';
import { authoredLightingLayer } from './fixtures/apartment-lit-layer-fixtures.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'), SRC = path.join(ROOT, 'app/src');
// The Lit frame's receiver layer, as lit-frame.mjs defines it (checked below against the source).
const LIT_FRAME_LAYER = 29;

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
      if (stat.isFile() && (/\.(?:js|mjs|jsx|css)$/.test(entry.name) || vendor || rootAsset)) result.set('/' + relative, file);
    }
  }
  visit(root); return result;
}

function browserOptions() {
  const installed = fs.existsSync(chromium.executablePath());
  const args = ['--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'];
  if (process.platform === 'win32') args.unshift('--use-angle=d3d11');
  else if (process.platform === 'linux') args.unshift('--use-gl=angle', '--use-angle=swiftshader-webgl', '--enable-unsafe-swiftshader');
  return { headless: true, args, ...(process.platform === 'linux' ? { channel: 'chromium' } : !installed ? { channel: 'msedge' } : {}) };
}

test('Cloud with Lighting on keeps the relit cloud on the Lit frame layer, and the x-ray look returns when Lighting goes off', { timeout: 170000 }, async () => {
  assert.match(fs.readFileSync(path.join(SRC, 'home-3d/lit-frame.mjs'), 'utf8'), new RegExp(`export const LIT_FRAME_LAYER = ${LIT_FRAME_LAYER}\\b`));
  const run = path.join(ROOT, '.tmp/apartment-lit-cloud-browser', 'run-' + Date.now());
  fs.mkdirSync(run, { recursive: true });
  const packs = path.join(run, 'packs'), privateRoot = path.join(run, 'private');
  const echo = installPack(packs, 'echo'), victoria = installPack(packs, 'victoria');
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1,
    packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  const layer = authoredLightingLayer(victoria);
  const sources = publicSources(SRC);
  let handler;
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (!['GET', 'HEAD'].includes(req.method)) { res.writeHead(403); res.end(); return; }
    if (url.pathname.startsWith('/api/apartment/v1/')) return handler(req, res);
    let body = null, type = 'application/octet-stream';
    if (url.pathname.startsWith('/api/perception-lab/v1/')) {
      const route = url.pathname.slice('/api/perception-lab/v1/'.length), value = route === 'capabilities'
        ? { site_id: 'victoria', cameras: [], live_previews: {}, live_videos: {}, recording_allowed: false, experiments: [], apartment_viewing: { provisioned: false } }
        : route === 'sessions' ? { site_id: 'victoria', version: 1, items: [] } : route === 'recordings' ? { items: [] } : null;
      if (value) { body = Buffer.from(JSON.stringify(value)); type = 'application/json'; }
    }
    if (url.pathname === '/content-packs/index.json') { body = fs.readFileSync(path.join(packs, 'index.json')); type = 'application/json'; }
    else if (url.pathname.startsWith(layer.base)) {
      body = layer.files[url.pathname.slice(layer.base.length)] ?? null;
      if (url.pathname.endsWith('.json')) type = 'application/json';
    } else {
      const pack = [echo, victoria].find(item => url.pathname.startsWith(item.base));
      if (pack) { body = pack.files[url.pathname.slice(pack.base.length)] ?? null; if (url.pathname.endsWith('.json')) type = 'application/json'; }
    }
    if (!body && url.pathname.startsWith('/proxy/')) {
      const value = url.pathname.endsWith('/model') || url.pathname.endsWith('/apartment_model') ? echo.seed
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
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  handler = createEquipmentRegistrationGateway({ env: { NODE_ENV: 'test', HOME_WEB_EQUIPMENT_REGISTRATION_ENABLED: '1',
    HOME_WEB_EQUIPMENT_REGISTRATION_ROOT: privateRoot, HOME_WEB_EQUIPMENT_REGISTRATION_ORIGIN: origin, HOME_CONTENT_PACKS_DIR: packs },
    authenticate: req => req.headers.cookie?.includes('authored_owner_session=1') === true });
  const browser = await chromium.launch(browserOptions());
  try {
    const context = await browser.newContext({ viewport: { width: 1366, height: 768 }, deviceScaleFactor: 1, serviceWorkers: 'block' });
    await context.addCookies([{ name: 'authored_owner_session', value: '1', url: origin }]);
    await context.addInitScript(() => {
      localStorage.setItem('apartment3d.site', 'victoria'); localStorage.setItem('home.perf.backgroundWarmup', 'off');
      localStorage.setItem('apartment3d.prewarm', 'off');
      window.HG_DISABLE_SERVICE_WORKER = true;
    });
    await context.route('**/*', route => new URL(route.request().url()).origin === origin || /^(data|blob):/.test(route.request().url())
      ? route.continue() : route.abort());
    await context.routeWebSocket('**/*', socket => socket.close());
    const page = await context.newPage();
    await page.goto(origin + '/?apartmentRedesign=1&worldZoom=off', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => window.__HOME_APARTMENT_SITE_API?.readback().phase === 'ready' && window.__APT_ENGINE?.siteId === 'victoria',
      null, { timeout: 60000 });
    const cloud = page.getByRole('button', { name: 'Cloud view', exact: true });
    if (await cloud.getAttribute('aria-pressed') !== 'true') await cloud.click();
    // What draws the cloud: the cloud itself on the camera's layer 0, or the x-ray look's composite.
    const drawing = () => page.evaluate(async litLayer => {
      const engine = window.__APT_ENGINE, points = await engine.pointsPromise;
      let scene = engine.apartmentRoot; while (scene.parent) scene = scene.parent;
      const quad = scene.getObjectByName('apartment-points-xray-composite'), copies = points.getObjectByName('apartment-points-xray');
      const lit = engine.litState?.();
      return { mode: engine.modes.mode, cloudDrawn: points.visible && points.layers.isEnabled(0), litLayer: points.layers.isEnabled(litLayer), xray: !!quad,
        xrayDrawn: !!quad?.visible && !!copies?.visible, lit: lit && { active: lit.active, refused: lit.receiversRefused ?? null,
          receivers: lit.receivers && { kind: lit.receivers.kind, attached: lit.receivers.attached, blends: lit.receivers.blends } } };
    }, LIT_FRAME_LAYER);
    // A desktop-class Cloud wears the x-ray look: its copies draw, the cloud itself is hidden.
    await page.waitForFunction(async () => {
      const engine = window.__APT_ENGINE, points = await engine.pointsPromise;
      return engine.modes.mode === 'points' && !!points.getObjectByName('apartment-points-xray')?.visible && !points.layers.isEnabled(0);
    }, null, { timeout: 60000 });
    const unlit = await drawing();
    assert.deepEqual([unlit.xray, unlit.xrayDrawn, unlit.cloudDrawn], [true, true, false], `the x-ray look draws Cloud before Lighting: ${JSON.stringify(unlit)}`);

    // Lighting on: the cloud is relit in place, and it is the cloud that draws.
    await page.getByRole('button', { name: 'Lighting', exact: true }).click();
    await page.waitForFunction(() => { const s = window.__APT_ENGINE.litState?.();
      return s?.active === true && s.receivers?.kind === 'points' && s.receivers.attached === true && s.receivers.blends > 0; }, null, { timeout: 90000 })
      .catch(async error => { throw new Error(`Lighting never relit the cloud: ${JSON.stringify(await drawing())} (${error.message.split('\n')[0]})`); });
    // The look runs every frame: sample across many frames, as the old reset struck on each one.
    for (let i = 0; i < 6; i++) {
      await page.waitForTimeout(250);
      const relit = await drawing();
      assert.equal(relit.litLayer, true, `with Lighting on, the relit cloud stays on the Lit frame layer: ${JSON.stringify(relit)}`);
      assert.equal(relit.cloudDrawn, true, `with Lighting on, the cloud itself is shown: ${JSON.stringify(relit)}`);
      assert.equal(relit.xrayDrawn, false, `with Lighting on, the x-ray look's copies stand down: ${JSON.stringify(relit)}`);
    }

    // Lighting off: the x-ray look comes back.
    await page.getByRole('button', { name: 'Lighting', exact: true }).click();
    await page.waitForFunction(async () => { const engine = window.__APT_ENGINE, points = await engine.pointsPromise;
      return !engine.litState?.()?.active && !!points.getObjectByName('apartment-points-xray')?.visible && !points.layers.isEnabled(0); }, null, { timeout: 30000 })
      .catch(async error => { throw new Error(`the x-ray look didn't come back after Lighting: ${JSON.stringify(await drawing())} (${error.message.split('\n')[0]})`); });
    const after = await drawing();
    assert.equal(after.litLayer, false, `with Lighting off, the Lit frame layer is gone again: ${JSON.stringify(after)}`);
    await context.close();
  } finally {
    await browser.close();
    server.closeAllConnections?.(); server.close();
  }
});
