// Den simulation harness: the real Home on loopback, fed scripted lighting-brain
// documents. It binds 127.0.0.1 only; the page's CSP is connect-src 'self';
// anything not served here is a logged 503; non-GET is refused except the feed;
// WebSocket upgrades are destroyed. There is no path to a bulb, the camera, the
// Jetson, LA, Home Assistant or any LAN host.
//
//   node tools\den-sim\run.mjs                   your real scan pack
//   node tools\den-sim\run.mjs --room authored   authored room and sockets, no household data
//
// Optional private inputs, read only from %LOCALAPPDATA%\den-sim\private:
//   registration-revision-3.json       saved from Home; otherwise AUTHORED SOCKETS
//   victoria-den-sofa-target-v4.json   your sofa mark; otherwise a DECLARED couch point
//   couch-declared.json                {"point_m":[x,y,z]}, the DECLARED point to use
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import Core from '../../app/src/home-equipment-registration.js';
import { readActiveBinding, REGISTRATION_ROUTE } from '../../web-gateway/equipment-registration.mjs';
import { createContentPackHandler } from '../../web-gateway/content-pack-route.mjs';
import { createLightingLayerHandler } from '../../web-gateway/lighting-layer.mjs';
import { readPackIndex } from '../content-packs.mjs';
import { authoredDocument, installPack } from '../fixtures/apartment-home-shell-fixtures.mjs';
import { BRAIN_BASE, createFeed, hopList } from './feed.mjs';

const HOST = '127.0.0.1', PORT = 18180, ORIGIN = `http://${HOST}:${PORT}`;
const HERE = path.dirname(fileURLToPath(import.meta.url)), ROOT = path.resolve(HERE, '../..'), SRC = path.join(ROOT, 'app/src');
const refs = JSON.parse(fs.readFileSync(path.join(HERE, 'refs.json'), 'utf8'));
const SEAT_HEIGHT_M = .45;

function option(name, fallback) {
  const at = process.argv.indexOf('--' + name);
  return at > 1 ? process.argv[at + 1] : fallback;
}
const room = option('room', 'real');
if (!['real', 'authored'].includes(room)) throw Error('--room must be real or authored');
if (!process.env.LOCALAPPDATA) throw Error('LOCALAPPDATA is not set');
const OUT = path.join(process.env.LOCALAPPDATA, 'den-sim'), PRIVATE = path.join(OUT, 'private');
const relative = path.relative(ROOT, OUT);
if (!relative.startsWith('..') && !path.isAbsolute(relative)) throw Error('run output must stay outside the checkout');
const run = path.join(OUT, 'runs', new Date().toISOString().replace(/[:.]/g, '-'));
fs.mkdirSync(run, { recursive: true }); fs.mkdirSync(PRIVATE, { recursive: true });

function packRoot() {
  if (room === 'real') return path.resolve(refs.content_packs);
  const packs = path.join(run, 'packs');
  const items = ['echo', 'victoria'].map(site => installPack(packs, site));
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1,
    packs: items.map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  return packs;
}
const PACKS = packRoot(), binding = readActiveBinding(PACKS);
const privateNotes = [];
const same = (a, b) => Core.canonicalJson(a) === Core.canonicalJson(b);
// PowerShell 5.1 writes UTF-16 with `>` and a UTF-8 BOM with Set-Content.
function readPrivateJson(file) {
  const bytes = fs.readFileSync(file), text = bytes[0] === 0xff && bytes[1] === 0xfe ? bytes.toString('utf16le') : bytes.toString('utf8');
  return JSON.parse(text.replace(/^﻿/, ''));
}

function loadRegistration() {
  const file = path.join(PRIVATE, 'registration-revision-3.json');
  if (room === 'real' && fs.existsSync(file)) {
    try {
      const raw = readPrivateJson(file), doc = raw.registration || raw;
      const result = Core.validateDocument(doc, { binding });
      if (doc.revision === 3 && result.ok) return { doc, source: 'export', label: 'REAL SOCKETS · registration revision 3' };
      privateNotes.push(`registration export refused: revision ${doc.revision}, valid ${result.ok}`);
    } catch (error) { privateNotes.push('registration export unreadable: ' + error.message); }
  }
  const doc = Core.sealDocument(Core.normalizeDocument(authoredDocument(binding, Core)));
  if (!Core.validateDocument(doc, { binding }).ok) throw Error('authored registration is invalid for the active pack');
  return { doc, source: 'authored', label: 'AUTHORED SOCKETS' };
}
function loadCouch() {
  const file = path.join(PRIVATE, 'victoria-den-sofa-target-v4.json');
  if (room === 'real' && fs.existsSync(file)) {
    try {
      const mark = readPrivateJson(file), point = mark.seat?.point_m;
      const valid = mark.schema === 'home-registered-sofa-target-1' && same(mark.binding, binding)
        && Array.isArray(point) && point.length === 3 && point.every(Number.isFinite);
      if (valid) return { point, source: 'sofa_mark', label: 'YOUR SOFA MARK' };
      privateNotes.push('sofa mark refused: wrong schema, pack binding or seat');
    } catch (error) { privateNotes.push('sofa mark unreadable: ' + error.message); }
  }
  return { point: declaredPoint(), source: 'declared', label: 'DECLARED' };
}
// Where the script says the couch is, never read from the sofa mark. A private
// point if one was declared, or else the middle of this pack's floor, so the
// beam at least lands inside the room. The page labels it DECLARED either way.
function declaredPoint() {
  const file = path.join(PRIVATE, 'couch-declared.json');
  if (room === 'real' && fs.existsSync(file)) {
    try {
      const point = readPrivateJson(file).point_m;
      if (Array.isArray(point) && point.length === 3 && point.every(Number.isFinite)) return point;
      privateNotes.push('couch-declared.json refused: point_m must be [x, y, z]');
    } catch (error) { privateNotes.push('couch-declared.json unreadable: ' + error.message); }
  }
  const directory = path.join(PACKS, 'victoria', binding.content_revision);
  const polygon = JSON.parse(fs.readFileSync(path.join(directory, 'floor.json'), 'utf8')).walkable_polygon;
  const centre = [0, 1].map(axis => polygon.reduce((sum, corner) => sum + corner[axis], 0) / polygon.length);
  return [...centre.map(v => Math.round(v * 100) / 100), SEAT_HEIGHT_M];
}
const registration = loadRegistration(), couch = loadCouch();

function sourceIsPinned() {
  try { execFileSync('git', ['-C', ROOT, 'diff', '--quiet', refs.base.sha, '--', 'app/src'], { stdio: 'ignore', windowsHide: true }); return true; }
  catch { return false; }
}
const pinned = sourceIsPinned();
const context = { room, registration: { source: registration.source, label: registration.label, revision: registration.doc.revision },
  couch: { source: couch.source, label: couch.label, point: couch.point }, base: refs.base.sha.slice(0, 8), source_is_pinned: pinned,
  pack_revision: binding.content_revision, private_notes: privateNotes };
const feed = createFeed({ seat: couch.point, couchLabel: couch.label });

/* ---------- static source, the same inventory as the Home browser suites ---------- */
function staticSources(root) {
  const sources = new Map(), excluded = new Set(['node_modules', 'data', 'artifacts', 'private', 'recordings', 'logs', 'precompiled']);
  (function walk(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (entry.name.startsWith('.') || excluded.has(entry.name)) continue;
      const file = path.join(directory, entry.name), name = path.relative(root, file).split(path.sep).join('/');
      if (name === 'assets/apartment' || name === 'img/raw') continue;
      const stat = fs.lstatSync(file);
      if (stat.isSymbolicLink()) continue;
      if (stat.isDirectory()) { walk(file); continue; }
      const trusted = /^(?:vendor|fonts|icons)\//.test(name) && /\.(?:wasm|woff2?|ttf|otf|png|jpe?g|webp|svg|ico|ktx2|json)$/.test(entry.name);
      const rootAsset = directory === root && ['index.html', 'favicon.svg', 'favicon.ico', 'favicon-32.png', 'apple-touch-icon.png', 'site.webmanifest'].includes(entry.name);
      if (/\.(?:js|mjs|jsx|css)$/.test(entry.name) || trusted || rootAsset) sources.set('/' + name, file);
    }
  })(root);
  return sources;
}
const sources = staticSources(SRC);
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
  '.jsx': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json', '.webmanifest': 'application/manifest+json',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp',
  '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf', '.otf': 'font/otf', '.wasm': 'application/wasm' };
const type = file => TYPES[path.extname(file).toLowerCase()] || 'application/octet-stream';

// Runs before Home's own scripts. CSP blocks that happen before the panel loads still count.
const PAGE_INIT = `<script>window.HG_DISABLE_SERVICE_WORKER=true;window.__denSimCsp=[];`
  + `document.addEventListener('securitypolicyviolation',function(e){window.__denSimCsp.push({uri:e.blockedURI,directive:e.effectiveDirective});});`
  + `try{localStorage.setItem('apartment3d.site','victoria');localStorage.setItem('home.perf.backgroundWarmup','off');localStorage.setItem('apartment3d.prewarm','off');}catch(_){}</script>`;
function homePage() {
  const html = fs.readFileSync(path.join(SRC, 'index.html'), 'utf8');
  if (!html.includes('<head>') || !html.includes('</body>')) throw Error('index.html no longer has the expected shape');
  return html.replace('<head>', '<head>' + PAGE_INIT).replace('</body>', '<script src="/__sim/panel.js"></script></body>');
}

/* ---------- the log ---------- */
const refused = new Map(), counters = { served: 0, refused: 0, upgrades_destroyed: 0, feed_posts: 0, wrong_host: 0 };
const logFile = path.join(run, 'requests.jsonl');
function record(kind, method, pathname, status) {
  counters[kind === 'upgrade' ? 'upgrades_destroyed' : 'refused']++;
  const key = `${method} ${pathname} ${status}`, row = refused.get(key) || { method, path: pathname, status, count: 0 };
  row.count++; refused.set(key, row);
  fs.appendFile(logFile, JSON.stringify({ at: new Date().toISOString(), kind, method, path: pathname, status }) + '\n', () => {});
}
const SECURITY = { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
  // http(s) is same-origin only. data: and blob: carry local bytes, not network:
  // Spark fetches its WASM from data: URLs and the splat through blob:, and under
  // a bare connect-src 'self' Home reports "Photo isn't available here".
  'Content-Security-Policy': "default-src 'self' data: blob:; connect-src 'self' data: blob:; img-src 'self' data: blob:; media-src 'self' blob:; "
    + "font-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; worker-src 'self' blob:; "
    + "frame-src 'none'; object-src 'none'; form-action 'none'; base-uri 'self';" };
function send(res, status, body, headers = {}) {
  const bytes = Buffer.isBuffer(body) ? body : Buffer.from(typeof body === 'string' ? body : JSON.stringify(body));
  res.writeHead(status, { 'Content-Type': 'application/json', 'Content-Length': bytes.length, ...headers });
  res.end(bytes);
}
function refuse(res, method, pathname, status, kind = 'refused') {
  record(kind, method, pathname, status);
  send(res, status, { ok: false, error: 'den_sim_not_served', detail: 'Loopback simulation: nothing beyond this harness is reachable.' });
}
function readJson(req, limit = 16384) {
  return new Promise((resolve, reject) => {
    let size = 0; const chunks = [];
    req.on('data', chunk => { size += chunk.length; if (size > limit) { reject(Error('body_too_large')); req.destroy(); } else chunks.push(chunk); });
    req.on('end', () => { try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); } catch { reject(Error('json_invalid')); } });
    req.on('error', reject);
  });
}

/* ---------- routes ---------- */
// Both are the gateway's own read-only routes, in the gateway's own order.
const handleLighting = createLightingLayerHandler({ contentPacksDir: PACKS, authenticate: () => true });
const handlePack = createContentPackHandler({ contentPacksDir: PACKS, authenticate: () => true });
const LAB_CAPABILITIES = { site_id: 'victoria', cameras: [], live_previews: {}, live_videos: {}, recording_allowed: false,
  experiments: [], apartment_viewing: { provisioned: false } };

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, ORIGIN), { pathname } = url;
  for (const [name, value] of Object.entries(SECURITY)) res.setHeader(name, value);
  try {
    if (req.headers.host !== `${HOST}:${PORT}`) { counters.wrong_host++; return refuse(res, req.method, pathname, 403); }
    if (pathname === '/__sim/feed' && req.method === 'POST') {
      if (req.headers.origin !== ORIGIN) return refuse(res, req.method, pathname, 403);
      const body = await readJson(req); counters.feed_posts++;
      return send(res, 200, feed.run(body));
    }
    if (req.method !== 'GET') return refuse(res, req.method, pathname, 405);
    if (pathname === '/' && !url.search) { res.writeHead(302, { Location: '/?apartmentRedesign=1&worldZoom=off' }); return res.end(); }
    if (pathname === '/' || pathname === '/index.html') {
      feed.reset(); // A page load starts from an empty room, so every load can swing again.
      return send(res, 200, homePage(), { 'Content-Type': TYPES['.html'] });
    }
    if (pathname === '/__sim/panel.js') return send(res, 200, fs.readFileSync(path.join(HERE, 'panel.js')), { 'Content-Type': TYPES['.js'] });
    if (pathname === '/__sim/status') {
      return send(res, 200, { ...context, hops: hopList(context), feed: feed.readback(), counters, refused: [...refused.values()], run });
    }
    if (pathname === '/auth/status') return send(res, 200, { authenticated: true, username: 'den-sim' });
    if (pathname === '/api/perception-lab/v1/capabilities') return send(res, 200, LAB_CAPABILITIES, { 'X-Home-Lab-Access': 'read-only' });
    if (pathname === BRAIN_BASE + '/state') return send(res, 200, feed.current());
    if (pathname === BRAIN_BASE + '/stream') return feed.attach(res);
    if (pathname === REGISTRATION_ROUTE || pathname === `${REGISTRATION_ROUTE}/revisions/${registration.doc.revision}`) {
      return send(res, 200, { ok: true, registration: registration.doc, capabilities: { read: true, write: false } });
    }
    if (pathname.startsWith('/content-packs/')) {
      if (!handleLighting(req, res)) handlePack(req, res);
      counters.served++; return undefined;
    }
    const file = sources.get(pathname);
    if (file) { counters.served++; return send(res, 200, fs.readFileSync(file), { 'Content-Type': type(file) }); }
    return refuse(res, req.method, pathname, 503);
  } catch (error) {
    fs.appendFile(logFile, JSON.stringify({ at: new Date().toISOString(), kind: 'error', path: pathname, error: error.message }) + '\n', () => {});
    if (!res.headersSent) send(res, 500, { ok: false, error: error.message }); else res.destroy();
    return undefined;
  }
});
server.on('upgrade', (req, socket) => {
  socket.on('error', () => {});
  record('upgrade', 'UPGRADE', String(req.url).split('?')[0], 0);
  socket.destroy();
});
server.on('clientError', (_error, socket) => socket.destroy());
server.on('error', error => {
  console.error(error.code === 'EADDRINUSE' ? `Port ${PORT} is busy. Is another den-sim still running?` : error.message);
  process.exit(1);
});
server.listen(PORT, HOST, () => {
  const scan = room === 'real' ? `real scan pack ${binding.content_revision}` : 'AUTHORED ROOM';
  console.log([`Den simulation: ${ORIGIN}/`, `Room: ${scan} | Sockets: ${registration.label.replace(' · ', ', ')} | Couch: ${couch.label}`,
    ...privateNotes.map(note => 'Note: ' + note), `app/src ${pinned ? 'matches' : 'DIFFERS FROM'} ${context.base}`,
    `Run output: ${run}`, 'Everything is scripted and nothing is sent. Press Ctrl+C to stop.'].join('\n'));
  fs.writeFileSync(path.join(run, 'run.json'), JSON.stringify({ ...context, started_at: new Date().toISOString(), origin: ORIGIN }, null, 2) + '\n');
  // The pack catalog must resolve before the page asks for it.
  if (!readPackIndex(PACKS).packs.some(item => item.active && item.manifest.siteId === 'victoria')) console.error('No active Victoria pack in ' + PACKS);
});
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => { feed.dispose(); server.closeAllConnections(); server.close(() => process.exit()); });
