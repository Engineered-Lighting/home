// A cold interior load over a slow or failing link (2026-11-14: a fresh device sat on "the interior could not be
// loaded" because a new engine had 4 s to load its scan). Real Home shell, renderer and authored packs, served from
// this worktree.
// - A scan whose bytes arrive over more than 4 s still mounts, with its progress on screen meanwhile.
// - A scan whose first load fails mounts on one automatic retry, before the room offers Retry.
//   node --test tools/test-apartment-slow-scan-browser.mjs
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

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'), SRC = path.join(ROOT, 'app/src');
const SLOW_MS = 6000, CHUNKS = 12;

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

test('a cold interior load waits for a slow scan, shows its progress, and retries a failed one once', { timeout: 170000 }, async () => {
  const run = path.join(ROOT, '.tmp/apartment-slow-scan-browser', 'run-' + Date.now());
  fs.mkdirSync(run, { recursive: true });
  const packs = path.join(run, 'packs'), privateRoot = path.join(run, 'private');
  const echo = installPack(packs, 'echo'), victoria = installPack(packs, 'victoria');
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1,
    packs: [echo, victoria].map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  const sources = publicSources(SRC);
  // How the scan is served: 'slow' streams every points.ply over SLOW_MS; 'fail' answers the engine's first one 503.
  // The page tags the engine's own fetch (cache 'force-cache', pointcloud.js) with x-test-scan: engine; the Lit layer
  // loader's 'no-store' copy goes untagged.
  let scan = 'slow';
  const seen = { engine: 0, lit: 0 };
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
    else {
      const pack = [echo, victoria].find(item => url.pathname.startsWith(item.base));
      if (pack) { body = pack.files[url.pathname.slice(pack.base.length)] ?? null; if (url.pathname.endsWith('.json')) type = 'application/json'; }
      if (pack === victoria && url.pathname.endsWith('/points.ply') && body) {
        const lit = req.headers['x-test-scan'] !== 'engine';
        const n = ++seen[lit ? 'lit' : 'engine'];
        if (scan === 'fail' && !lit && n === 1) { res.writeHead(503, { 'Cache-Control': 'no-store' }); res.end(); return; }
        if (scan === 'slow') {
          res.writeHead(200, { 'Content-Type': type, 'Content-Length': body.length, 'Cache-Control': 'no-store' });
          const size = Math.ceil(body.length / CHUNKS);
          let i = 0;
          const send = () => {
            if (res.destroyed) return;
            res.write(body.subarray(i * size, (i + 1) * size));
            if (++i < CHUNKS) setTimeout(send, SLOW_MS / CHUNKS); else res.end();
          };
          setTimeout(send, SLOW_MS / CHUNKS);
          return;
        }
      }
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
  const open = async () => {
    const context = await browser.newContext({ viewport: { width: 1366, height: 768 }, deviceScaleFactor: 1, serviceWorkers: 'block' });
    await context.addCookies([{ name: 'authored_owner_session', value: '1', url: origin }]);
    await context.addInitScript(() => {
      localStorage.setItem('apartment3d.site', 'victoria'); localStorage.setItem('home.perf.backgroundWarmup', 'off');
      window.HG_DISABLE_SERVICE_WORKER = true;
      const fetchFirst = window.fetch.bind(window);
      window.fetch = (input, init = {}) => {
        const target = typeof input === 'string' ? input : input?.url || String(input);
        if (/\/points\.ply(?:\?|$)/.test(target) && init?.cache === 'force-cache') {
          const headers = new Headers(init.headers || {}); headers.set('x-test-scan', 'engine');
          return fetchFirst(input, { ...init, headers });
        }
        return fetchFirst(input, init);
      };
      // The room's own loading line, sampled while the scan arrives.
      window.__scanPercents = [];
      setInterval(() => {
        const match = /loading apartment · (\d+)%/.exec(document.body?.innerText || '');
        if (match) window.__scanPercents.push(Number(match[1]));
      }, 150);
    });
    await context.route('**/*', route => new URL(route.request().url()).origin === origin || /^(data|blob):/.test(route.request().url())
      ? route.continue() : route.abort());
    await context.routeWebSocket('**/*', socket => socket.close());
    const page = await context.newPage();
    const warnings = [];
    page.on('console', message => { if (message.type() === 'warning') warnings.push(message.text()); });
    await page.goto(origin + '/?apartmentRedesign=1&worldZoom=off', { waitUntil: 'domcontentloaded' });
    return { context, page, warnings };
  };
  const mounted = page => page.waitForFunction(() => window.__HOME_APARTMENT_SITE_API?.readback().phase === 'ready'
    && window.__APT_ENGINE?.siteId === 'victoria', null, { timeout: 60000 });
  const phase = page => page.evaluate(() => window.__HOME_APARTMENT_SITE_API?.readback() ?? null);
  try {
    // A slow scan: its bytes arrive over SLOW_MS (more than the old 4 s), and the room shows how far it is.
    scan = 'slow';
    let { context, page } = await open();
    const started = Date.now();
    await mounted(page).catch(async error => { throw new Error(`the slow scan never mounted: ${JSON.stringify(await phase(page))} (${error.message.split('\n')[0]})`); });
    const took = Date.now() - started;
    assert.ok(seen.engine >= 1, 'the engine fetched the scan');
    assert.ok(took > 4000, `the scan took longer than the old 4 s budget (${took} ms)`);
    const percents = await page.evaluate(() => window.__scanPercents);
    assert.ok(percents.some(value => value > 0 && value < 100), `the room showed the scan's progress meanwhile: ${JSON.stringify([...new Set(percents)])}`);
    await context.close();

    // A failed first load: the engine's first request answers 503; one fresh engine loads it before the room shows Retry.
    scan = 'fail'; seen.engine = 0; seen.lit = 0;
    let warnings;
    ({ context, page, warnings } = await open());
    await mounted(page).catch(async error => { throw new Error(`the retried scan never mounted: ${JSON.stringify(await phase(page))} (${error.message.split('\n')[0]})`); });
    assert.equal(seen.engine, 2, 'the engine loaded the scan twice: the 503, then the retry');
    assert.ok(warnings.some(text => /the victoria scan failed; loading it once more/.test(text)), `the retry is logged: ${JSON.stringify(warnings.slice(0, 6))}`);
    await context.close();
  } finally {
    await browser.close();
    server.closeAllConnections?.(); server.close();
  }
});
