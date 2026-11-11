// Step 2's scan page: the real Home on loopback, on your scan, where you click the same six features you clicked on
// the camera's picture. Home's own display-mesh picker (createRegisteredDeskSurfacePicker, the one your sofa mark was
// made with) gives each feature's point in the scan's own frame, and Save writes them into den-register's worksheet as
// measured_with scan-pick. After a fit, --mode show draws the camera where the fit put it, and your six features as the
// camera sees them, carried into the scan by the fit.
//
//   node tools\den-live\scan_pick.mjs --work <folder> --take <scaled take> --mode pick|show [--room real|authored]
//
// register_now.py starts it; it prints `ready <url>` once it listens. Exit 0: saved (pick) or Done (show).
// It binds 127.0.0.1 only and refuses any other Host. GET only, except Save and Done, which need this page's own
// Origin and its token. Nothing beyond this harness is reachable: anything else is a 503, and WebSocket upgrades are
// destroyed. It prints no coordinate: the points go to the worksheet, which stays in the private folder.
import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import Core from '../../app/src/home-equipment-registration.js';
import { readActiveBinding, REGISTRATION_ROUTE } from '../../web-gateway/equipment-registration.mjs';
import { createContentPackHandler } from '../../web-gateway/content-pack-route.mjs';
import { createLightingLayerHandler } from '../../web-gateway/lighting-layer.mjs';
import { authoredDocument, installPack } from '../fixtures/apartment-home-shell-fixtures.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url)), ROOT = path.resolve(HERE, '../..'), SRC = path.join(ROOT, 'app/src');
const SITE = /\/sites\/([^/]+)\//.exec(REGISTRATION_ROUTE)[1];   // the den's site, as the gateway's own route names it
const SCAN_PICK = 'scan-pick', SHA = /^[0-9a-f]{64}$/, LIMIT_M = 100;

function option(name, fallback) {
  const at = process.argv.indexOf('--' + name);
  return at > 1 ? process.argv[at + 1] : fallback;
}
function fail(text) { console.log(`refused: ${text}`); process.exit(2); }
const MODE = option('mode', 'pick'), ROOM = option('room', 'real');
const WORK = path.resolve(option('work', '')), TAKE = path.resolve(option('take', ''));
const HOST = '127.0.0.1', PORT = Number(option('port', '18186')), ORIGIN = `http://${HOST}:${PORT}`;
if (!['pick', 'show'].includes(MODE)) fail('--mode is pick or show');
if (!['real', 'authored'].includes(ROOM)) fail('--room is real or authored');
const WORKSHEET = path.join(WORK, 'worksheet.json');
if (!option('work') || !fs.existsSync(WORKSHEET)) fail('--work must be the registration folder, with its worksheet.json');
if (!option('take') || !fs.existsSync(path.join(TAKE, 'take.json'))) fail('--take must be a take folder');
for (let dir = WORK; ; dir = path.dirname(dir)) {   // outside every git checkout, not just this one
  if (fs.existsSync(path.join(dir, '.git'))) fail('the registration folder must stay outside every git checkout');
  if (path.dirname(dir) === dir) break;
}
const TOKEN = crypto.randomBytes(18).toString('hex');

/* ---------- the packs: your scan, or the authored room for tests ---------- */
function packRoot() {
  if (ROOM === 'real') {
    const refs = JSON.parse(fs.readFileSync(path.join(ROOT, 'tools', 'den-sim', 'refs.json'), 'utf8'));
    return path.resolve(option('packs', refs.content_packs));
  }
  // --tmp: where the authored room's packs go (tests give their own folder: a terminated process skips 'exit')
  const packs = fs.mkdtempSync(path.join(path.resolve(option('tmp', os.tmpdir())), 'scan-pick-'));
  process.on('exit', () => fs.rmSync(packs, { recursive: true, force: true }));
  const item = installPack(packs, SITE);
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1, packs: [{ manifest: item.manifest, active: true, baseUrl: item.base }] }));
  return packs;
}
const PACKS = packRoot();
let binding;
try { binding = readActiveBinding(PACKS); } catch (error) { fail(`no active pack for the den in ${ROOM === 'real' ? 'your content packs' : 'the authored room'} (${error.code || error.message})`); }
// The authored room's sockets, as the den simulation serves them. Your scan needs none: the picker reads the mesh.
const registration = ROOM === 'authored' ? Core.sealDocument(Core.normalizeDocument(authoredDocument(binding, Core))) : null;

/* ---------- the worksheet, the clicks and the fit, all in the registration folder ---------- */
const readJson = file => JSON.parse(fs.readFileSync(file, 'utf8').replace(/^﻿/, ''));
function writeJson(file, value) {
  fs.writeFileSync(`${file}.tmp`, JSON.stringify(value, null, 1) + '\n', 'utf8');
  fs.renameSync(`${file}.tmp`, file);
}
// den-register's clicks_path: clicks-<take folder name, other characters as _>.json
const clicksFile = () => path.join(WORK, `clicks-${path.basename(TAKE).replace(/[^A-Za-z0-9._-]/g, '_')}.json`);
// A pick counts only on the scan revision it was made on: a pick from an older pack is no pick on this one.
const pickedOn = revision => `content revision ${revision}`;
const picked = point => point.measured_with === SCAN_PICK && Array.isArray(point.room_xyz_m)
  && String(point.note || '').endsWith(pickedOn(binding.content_revision));
const metres = value => Math.round(value * 1e6) / 1e6;   // den-register's records.rounded: micrometres

function showData(sheet) {
  // The newest room-model revision's camera, and the newest registered fit that wrote it.
  const lines = fs.readFileSync(path.join(WORK, 'room-model.jsonl'), 'utf8').split('\n').filter(line => line.trim());
  const entry = (JSON.parse(lines.at(-1)).cameras || []).find(item => item.camera_id === sheet.camera_id);
  if (!entry) throw Error('no camera in the room model');
  const id = `${sheet.camera_id}@${entry.version}`, M = entry.pose.room_from_camera;
  const fits = fs.readdirSync(WORK).map(name => /^fit-([0-9]+)\.json$/.exec(name)).filter(Boolean)
    .sort((a, b) => Number(b[1]) - Number(a[1])).map(match => readJson(path.join(WORK, match[0])));
  const fit = fits.find(item => item.result === 'registered' && item.transform_id === id);
  if (!fit) throw Error(`no registered fit for ${id}`);
  const carry = p => [0, 1, 2].map(r => M[r][0] * p[0] + M[r][1] * p[1] + M[r][2] * p[2] + M[r][3]);
  return { transform_id: id, camera_m: [M[0][3], M[1][3], M[2][3]],
    features: fit.pairs.filter(pair => pair.camera_xyz_m).map(pair => ({ id: pair.id, role: pair.role, point_m: carry(pair.camera_xyz_m),
      scan_m: pair.room_xyz_m, apart_m: Math.hypot(...carry(pair.camera_xyz_m).map((v, i) => v - pair.room_xyz_m[i])) })) };
}
function state() {
  const sheet = readJson(WORKSHEET);
  let clicks = {};
  try { clicks = readJson(clicksFile()).clicks || {}; } catch { /* nothing clicked yet */ }
  return { mode: MODE, room: ROOM, binding,
    points: sheet.points.map(point => ({ id: point.id, role: point.role, label: point.label || '', picked: picked(point),
      point_m: picked(point) ? point.room_xyz_m : null })),
    clicks: Object.fromEntries(Object.entries(clicks).map(([id, c]) => [id, { u: c.u, v: c.v }])),
    left: fs.existsSync(path.join(WORK, 'pick', 'left.png')),
    show: MODE === 'show' ? showData(sheet) : null };
}

// Save: every point the worksheet lists ends up scan-picked, from this body or kept from an earlier save.
function save(body) {
  const sheet = readJson(WORKSHEET), picks = body?.picks;
  if (!picks || typeof picks !== 'object' || Array.isArray(picks)) throw Error('picks_missing');
  const ids = new Set(sheet.points.map(point => point.id));
  for (const id of Object.keys(picks)) if (!ids.has(id)) throw Error(`not_a_point:${String(id).slice(0, 32)}`);
  const today = new Date(), date = [today.getFullYear(), today.getMonth() + 1, today.getDate()].map(n => String(n).padStart(2, '0')).join('-');
  for (const point of sheet.points) {
    const pick = picks[point.id];
    if (pick === undefined) {
      if (!picked(point)) throw Error(`not_picked:${point.id}`);
      continue;
    }
    const p = pick?.point_m, ref = pick?.surface_ref;
    if (!Array.isArray(p) || p.length !== 3 || !p.every(v => typeof v === 'number' && Number.isFinite(v) && Math.abs(v) <= LIMIT_M)) throw Error(`bad_point:${point.id}`);
    if (!ref || typeof ref.asset_name !== 'string' || !/^[A-Za-z0-9._-]{1,64}$/.test(ref.asset_name) || !SHA.test(ref.asset_sha256 || '')) throw Error(`bad_surface:${point.id}`);
    Object.assign(point, { room_xyz_m: p.map(metres), measured_with: SCAN_PICK, measured_from: { x: 'scan', y: 'scan', z: 'scan' },
      note: `scan-pick on ${ref.asset_name} ${ref.asset_sha256.slice(0, 12)}, ${pickedOn(binding.content_revision)}` });
  }
  sheet.measured_on = date;
  writeJson(WORKSHEET, sheet);
  return sheet.points.length;
}

/* ---------- static source: app/src as the den simulation serves it ---------- */
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
const PAGE_INIT = `<script>window.HG_DISABLE_SERVICE_WORKER=true;window.__scanPick={token:${JSON.stringify(TOKEN)}};`
  + `try{localStorage.setItem('apartment3d.site',${JSON.stringify(SITE)});localStorage.setItem('home.perf.backgroundWarmup','off');localStorage.setItem('apartment3d.prewarm','off');}catch(_){}</script>`;
function homePage() {
  const html = fs.readFileSync(path.join(SRC, 'index.html'), 'utf8');
  if (!html.includes('<head>') || !html.includes('</body>')) throw Error('index.html no longer has the expected shape');
  return html.replace('<head>', '<head>' + PAGE_INIT).replace('</body>', '<script src="/__pick/panel.js"></script></body>');
}

/* ---------- the server ---------- */
const SECURITY = { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
  'Content-Security-Policy': "default-src 'self' data: blob:; connect-src 'self' data: blob:; img-src 'self' data: blob:; media-src 'self' blob:; "
    + "font-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; worker-src 'self' blob:; "
    + "frame-src 'none'; object-src 'none'; form-action 'none'; base-uri 'self';" };
function send(res, status, body, headers = {}) {
  const bytes = Buffer.isBuffer(body) ? body : Buffer.from(typeof body === 'string' ? body : JSON.stringify(body));
  res.writeHead(status, { 'Content-Type': 'application/json', 'Content-Length': bytes.length, ...headers });
  res.end(bytes);
}
const refuse = (res, status) => send(res, status, { ok: false, error: 'scan_pick_not_served', detail: 'Loopback scan page: nothing beyond this harness is reachable.' });
function readBody(req, limit = 16384) {
  return new Promise((resolve, reject) => {
    let size = 0; const chunks = [];
    req.on('data', chunk => { size += chunk.length; if (size > limit) { reject(Error('body_too_large')); req.destroy(); } else chunks.push(chunk); });
    req.on('end', () => { try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); } catch { reject(Error('json_invalid')); } });
    req.on('error', reject);
  });
}
function finish(code, message) {
  console.log(message);
  setTimeout(() => { server.closeAllConnections(); server.close(() => process.exit(code)); }, 300);
}
const handleLighting = createLightingLayerHandler({ contentPacksDir: PACKS, authenticate: () => true });
const handlePack = createContentPackHandler({ contentPacksDir: PACKS, authenticate: () => true });
const LAB_CAPABILITIES = { site_id: SITE, cameras: [], live_previews: {}, live_videos: {}, recording_allowed: false,
  experiments: [], apartment_viewing: { provisioned: false } };
let finished = false;

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, ORIGIN), { pathname } = url;
  for (const [name, value] of Object.entries(SECURITY)) res.setHeader(name, value);
  try {
    if (req.headers.host !== `${HOST}:${PORT}`) return refuse(res, 403);
    if (req.method === 'POST' && (pathname === '/__pick/save' || pathname === '/__pick/done')) {
      if (req.headers.origin !== ORIGIN || req.headers['x-scan-pick'] !== TOKEN || finished) return refuse(res, 403);
      if (pathname === '/__pick/done') {
        if (MODE !== 'show') return refuse(res, 409);
        finished = true; send(res, 200, { ok: true });
        return finish(0, 'the page says done');
      }
      if (MODE !== 'pick') return refuse(res, 409);
      let count;
      try { count = save(await readBody(req)); } catch (error) { return send(res, 422, { ok: false, error: error.message }); }
      finished = true; send(res, 200, { ok: true, saved: count });
      return finish(0, `saved ${count} scan picks to the worksheet`);
    }
    if (req.method !== 'GET') return refuse(res, 405);
    if (pathname === '/' && !url.search) { res.writeHead(302, { Location: '/?apartmentRedesign=1&worldZoom=off' }); return res.end(); }
    if (pathname === '/' || pathname === '/index.html') return send(res, 200, homePage(), { 'Content-Type': TYPES['.html'] });
    if (pathname === '/__pick/panel.js') return send(res, 200, fs.readFileSync(path.join(HERE, 'scan_pick_panel.js')), { 'Content-Type': TYPES['.js'] });
    if (pathname === '/__pick/state') return send(res, 200, state());
    if (pathname === '/__pick/left.png') {
      const file = path.join(WORK, 'pick', 'left.png');
      return fs.existsSync(file) ? send(res, 200, fs.readFileSync(file), { 'Content-Type': 'image/png' }) : refuse(res, 404);
    }
    if (pathname === '/auth/status') return send(res, 200, { authenticated: true, username: 'scan-pick' });
    if (pathname === '/api/perception-lab/v1/capabilities') return send(res, 200, LAB_CAPABILITIES, { 'X-Home-Lab-Access': 'read-only' });
    if (registration && (pathname === REGISTRATION_ROUTE || pathname === `${REGISTRATION_ROUTE}/revisions/${registration.revision}`)) {
      return send(res, 200, { ok: true, registration, capabilities: { read: true, write: false } });
    }
    if (pathname.startsWith('/content-packs/')) {
      if (!handleLighting(req, res)) handlePack(req, res);
      return undefined;
    }
    const file = sources.get(pathname);
    if (file) return send(res, 200, fs.readFileSync(file), { 'Content-Type': type(file) });
    return refuse(res, 503);
  } catch (error) {
    if (!res.headersSent) send(res, 500, { ok: false, error: error.message }); else res.destroy();
    return undefined;
  }
});
server.on('upgrade', (_req, socket) => { socket.on('error', () => {}); socket.destroy(); });
server.on('clientError', (_error, socket) => socket.destroy());
server.on('error', error => fail(error.code === 'EADDRINUSE' ? `port ${PORT} is busy; is another scan page still open?` : error.message));
server.listen(PORT, HOST, () => console.log(`ready ${ORIGIN}/`));
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => { server.closeAllConnections(); server.close(() => process.exit(1)); });
