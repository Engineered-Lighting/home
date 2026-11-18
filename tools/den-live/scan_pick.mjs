// Step 2's scan page: the real Home on loopback, on your scan, where you click the same features (nine) you clicked on
// the camera's picture. Home's own display-mesh picker (createRegisteredDeskSurfacePicker, the one your sofa mark was
// made with) gives each feature's point in the scan's own frame, and Save writes them into den-register's worksheet as
// measured_with scan-pick. After a fit, --mode show draws the camera where the fit put it, and your features as the
// camera sees them, carried into the scan by the fit. Step 4's walk test uses --mode spots: no camera picture, just four
// floor spots you can find both in the room and in the scan (a rug corner, a sofa foot), saved to <work>\spots.json.
//
//   node tools\den-live\scan_pick.mjs --work <folder> --take <scaled take> --mode pick|show [--room real|authored]
//   node tools\den-live\scan_pick.mjs --work <walk folder> --mode spots [--room real|authored]
//
// register_now.py and walk_test.py start it; it prints `ready <url>` once it listens. Exit 0: saved (pick, spots) or
// Done (show).
// The page is home_harness.mjs's: 127.0.0.1 only, its own Host only. Besides Home's own read-only routes it serves its
// state and the camera's picture, and takes Save and Done, which need this page's own Origin and its token. It prints
// no coordinate: the points go to the worksheet, which stays in the private folder.
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { HOST, createHarness, fail, loadHome, option, packRoot, readBody, refuse, send } from './home_harness.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url)), ROOT = path.resolve(HERE, '../..');
const SCAN_PICK = 'scan-pick', SHA = /^[0-9a-f]{64}$/, LIMIT_M = 100;
const MODE = option('mode', 'pick'), ROOM = option('room', 'real');
const WORK = path.resolve(option('work', '')), TAKE = path.resolve(option('take', ''));
const PORT = Number(option('port', '18186')), ORIGIN = `http://${HOST}:${PORT}`;
if (!['pick', 'show', 'spots'].includes(MODE)) fail('--mode is pick, show or spots');
if (!['real', 'authored'].includes(ROOM)) fail('--room is real or authored');
const SPOTS = MODE === 'spots';
const WORKSHEET = path.join(WORK, 'worksheet.json'), SPOTS_FILE = path.join(WORK, 'spots.json');
const SPOT_IDS = ['s1', 's2', 's3', 's4'], SPOT_LABEL = 'a floor spot you can also find in the room: a rug corner, a sofa foot';
if (SPOTS) {
  if (!option('work') || !fs.existsSync(WORK)) fail('--work must be the walk test\'s folder');
} else {
  if (!option('work') || !fs.existsSync(WORKSHEET)) fail('--work must be the registration folder, with its worksheet.json');
  if (!option('take') || !fs.existsSync(path.join(TAKE, 'take.json'))) fail('--take must be a take folder');
}
for (let dir = WORK; ; dir = path.dirname(dir)) {   // outside every git checkout, not just this one
  if (fs.existsSync(path.join(dir, '.git'))) fail('the registration folder must stay outside every git checkout');
  if (path.dirname(dir) === dir) break;
}
const TOKEN = crypto.randomBytes(18).toString('hex');
const home = await loadHome(ROOT);
const PACKS = packRoot(home, { room: ROOM, refsRoot: ROOT, packs: option('packs'), tmp: option('tmp'), prefix: 'scan-pick-' });

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
let binding = null;
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
// The walk test's spots: kept from an earlier save only when they were picked on this scan revision.
function keptSpots() {
  try {
    const doc = readJson(SPOTS_FILE);
    return doc.content_revision === binding.content_revision && Array.isArray(doc.spots) ? doc.spots : [];
  } catch { return []; }
}
function spotsState() {
  const kept = Object.fromEntries(keptSpots().map(spot => [spot.id, spot]));
  return { mode: MODE, room: ROOM, binding, clicks: {}, left: false, show: null,
    points: SPOT_IDS.map(id => ({ id, role: 'spot', label: SPOT_LABEL, picked: !!kept[id],
      point_m: kept[id] ? [...kept[id].xy_m, kept[id].z_m] : null })) };
}
function saveSpots(body) {
  const picks = body?.picks;
  if (!picks || typeof picks !== 'object' || Array.isArray(picks)) throw Error('picks_missing');
  for (const id of Object.keys(picks)) if (!SPOT_IDS.includes(id)) throw Error(`not_a_spot:${String(id).slice(0, 32)}`);
  const kept = Object.fromEntries(keptSpots().map(spot => [spot.id, spot]));
  const spots = SPOT_IDS.map(id => {
    const pick = picks[id];
    if (pick === undefined) {
      if (!kept[id]) throw Error(`not_picked:${id}`);
      return kept[id];
    }
    const p = pick?.point_m, ref = pick?.surface_ref;
    if (!Array.isArray(p) || p.length !== 3 || !p.every(v => typeof v === 'number' && Number.isFinite(v) && Math.abs(v) <= LIMIT_M)) throw Error(`bad_point:${id}`);
    if (!ref || typeof ref.asset_name !== 'string' || !/^[A-Za-z0-9._-]{1,64}$/.test(ref.asset_name) || !SHA.test(ref.asset_sha256 || '')) throw Error(`bad_surface:${id}`);
    return { id, xy_m: [metres(p[0]), metres(p[1])], z_m: metres(p[2]), measured_with: SCAN_PICK,
      surface_ref: { asset_name: ref.asset_name, asset_sha256: ref.asset_sha256 } };
  });
  writeJson(SPOTS_FILE, { schema: 'den-live-walk-spots-1', content_revision: binding.content_revision, saved_unix: Date.now() / 1000,
    note: 'Floor spots in the scan\'s frame for the walk test, picked on Home\'s display mesh. Private: never in git.', spots });
  return spots.length;
}

function state() {
  if (SPOTS) return spotsState();
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

/* ---------- the server ---------- */
let finished = false;
function finish(code, message) {
  console.log(message);
  setTimeout(() => { server.closeAllConnections(); server.close(() => process.exit(code)); }, 300);
}
const PAGE_INIT = `<script>window.HG_DISABLE_SERVICE_WORKER=true;window.__scanPick={token:${JSON.stringify(TOKEN)}};`
  + `try{localStorage.setItem('apartment3d.site',${JSON.stringify(home.site)});localStorage.setItem('home.perf.backgroundWarmup','off');localStorage.setItem('apartment3d.prewarm','off');}catch(_){}</script>`;
async function routes(req, res, url) {
  const { pathname } = url;
  if (req.method === 'POST' && (pathname === '/__pick/save' || pathname === '/__pick/done')) {
    if (req.headers.origin !== ORIGIN || req.headers['x-scan-pick'] !== TOKEN || finished) { refuse(res, 403); return true; }
    if (pathname === '/__pick/done') {
      if (MODE !== 'show') { refuse(res, 409); return true; }
      finished = true; send(res, 200, { ok: true });
      finish(0, 'the page says done');
      return true;
    }
    if (MODE === 'show') { refuse(res, 409); return true; }
    let count;
    try { count = (SPOTS ? saveSpots : save)(await readBody(req)); } catch (error) { send(res, 422, { ok: false, error: error.message }); return true; }
    finished = true; send(res, 200, { ok: true, saved: count });
    finish(0, SPOTS ? `saved ${count} floor spots for the walk test` : `saved ${count} scan picks to the worksheet`);
    return true;
  }
  if (req.method !== 'GET') return false;
  if (pathname === '/__pick/state') { send(res, 200, state()); return true; }
  if (pathname === '/__pick/left.png') {
    const file = path.join(WORK, 'pick', 'left.png');
    if (fs.existsSync(file)) send(res, 200, fs.readFileSync(file), { 'Content-Type': 'image/png' }); else refuse(res, 404);
    return true;
  }
  return false;
}
const made = createHarness({ home, packs: PACKS, port: PORT, pageInit: PAGE_INIT, panel: path.join(HERE, 'scan_pick_panel.js'),
  panelRoute: '/__pick/panel.js', authoredRoom: ROOM === 'authored', routes });
if (made.error) fail(`no active pack for the den in ${ROOM === 'real' ? 'your content packs' : 'the authored room'} (${made.error})`);
binding = made.binding;
const { server } = made;
server.on('error', error => fail(error.code === 'EADDRINUSE' ? `port ${PORT} is busy; is another scan page still open?` : error.message));
server.listen(PORT, HOST, () => console.log(`ready ${ORIGIN}/`));
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => { server.closeAllConnections(); server.close(() => process.exit(1)); });
