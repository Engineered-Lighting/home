// Den simulation harness: the real Home on loopback, following the older lighting brain on
// what the pinned pipeline tools made of a prepared, rendered take. It binds 127.0.0.1 only;
// the page's CSP is connect-src 'self'; anything not served here is a logged 503; non-GET is
// refused except the feed; WebSocket upgrades are destroyed. There is no path to a bulb, the
// camera, the Jetson, LA, Home Assistant or any LAN host.
//
//   node tools\den-sim\run.mjs                         your real scan pack, the newest prepared set
//   node tools\den-sim\run.mjs --room authored         authored room and sockets, no household data
//   node tools\den-sim\run.mjs --prepared <set folder> a prepared set somewhere else
//
// Needs python on the PATH, with numpy and Pillow for the picture.
//
// Data root: DEN_SIM_HOME, or else %USERPROFILE%\den-sim. Optional private inputs, read only
// from its private folder and only for your real scan:
//   registration-revision-3.json   saved from Home; otherwise AUTHORED SOCKETS
//   your sofa mark                 any file there whose schema is home-registered-sofa-target-1;
//                                  used only with the export it names, otherwise a DECLARED point
//   facing.json                    degrees to turn the reader from the stated facing (left positive)
//   couch-declared.json            {"point_m":[x,y,z]}, the DECLARED point to use without a mark
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import Core from '../../app/src/home-equipment-registration.js';
import Sofa from '../../app/src/home-lighting-sofa-target.js';
import { sealRegisteredDeskTarget } from '../../app/src/home-3d/registered-desk-target.mjs';
import { readActiveBinding, REGISTRATION_ROUTE } from '../../web-gateway/equipment-registration.mjs';
import { createContentPackHandler } from '../../web-gateway/content-pack-route.mjs';
import { createLightingLayerHandler } from '../../web-gateway/lighting-layer.mjs';
import { readPackIndex } from '../content-packs.mjs';
import { authoredDocument, installPack } from '../fixtures/apartment-home-shell-fixtures.mjs';
import { prepareBrain, privateReason, standInSofa } from './brain.mjs';
import { dataRoot, newestPreparedSet, refusedSince } from './data-root.mjs';
import { BRAIN_BASE, createFeed } from './feed.mjs';
import { TABLE, headTools, receiptView, sha256 } from './receipt.mjs';

const HOST = '127.0.0.1', PORT = 18180, ORIGIN = `http://${HOST}:${PORT}`;
const HERE = path.dirname(fileURLToPath(import.meta.url)), ROOT = path.resolve(HERE, '../..'), SRC = path.join(ROOT, 'app/src');
const refs = JSON.parse(fs.readFileSync(path.join(HERE, 'refs.json'), 'utf8'));
const SEAT_HEIGHT_M = .45, SPOT_ABOVE_SEAT_M = .30;   // the spot: 0.30 m above your mark's seat point, the rule hop 14 states
const MARK_SCHEMA = 'home-registered-sofa-target-1', CAMERA_ID = 'den-virtual';

function option(name, fallback) {
  const at = process.argv.indexOf('--' + name);
  return at > 1 ? process.argv[at + 1] : fallback;
}
const room = option('room', 'real');
if (!['real', 'authored'].includes(room)) throw Error('--room must be real or authored');
const OUT = dataRoot(), PRIVATE = path.join(OUT, 'private');
const relative = path.relative(ROOT, OUT);
if (!relative.startsWith('..') && !path.isAbsolute(relative)) throw Error('run output must stay outside the checkout');
const run = path.join(OUT, 'runs', new Date().toISOString().replace(/[:.]/g, '-'));
fs.mkdirSync(run, { recursive: true }); fs.mkdirSync(PRIVATE, { recursive: true });

/* ---------- the prepared set: public renders, takes, receipts, and a display file for the buttons ---------- */
const SET = option('prepared') ? path.resolve(option('prepared')) : newestPreparedSet(OUT);
if (!SET || !fs.existsSync(path.join(SET, 'set.json'))) {
  console.error(`No prepared set in ${path.join(OUT, 'prepared')}. An agent makes one with tools\\den-sim\\scene\\prepare.py.`);
  process.exit(1);
}
const readFile = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const setInfo = readFile(path.join(SET, 'set.json'));
// Where prepare stopped, for the page: on this set, and on any newer set the default start passed over.
const prepareNotes = [
  ...(setInfo.refusal ? [`prepare stopped at hop ${setInfo.refusal.hop} on this set: ${setInfo.refusal.reason}`] : []),
  ...(option('prepared') ? [] : refusedSince(OUT, SET).map(set => `the newest set ${set.id} was refused at hop ${set.refusal.hop} (${set.refusal.reason}), so set ${setInfo.set} is shown`)),
];
// The display file holds each take's label for its button. Only this panel reads it: a click is
// told a take's folder id, never a scene. Without it the buttons show the takes' folder ids, and
// every click runs exactly as it would with it.
const displayFile = path.join(SET, 'display.json');
const scenes = fs.existsSync(displayFile) ? readFile(displayFile).scenes.map(({ take, label }) => ({ take, label }))
  : setInfo.takes.filter(item => item.kind === 'scene').map(({ take }) => ({ take, label: `take ${take}` }));
if (!fs.existsSync(displayFile)) prepareNotes.push(`set ${setInfo.set} has no display file, so its buttons show folder ids`);
if (!scenes.length) {
  console.error(`Prepared set ${setInfo.set} holds no scene take to press${setInfo.refusal ? `: prepare stopped at hop ${setInfo.refusal.hop}: ${setInfo.refusal.reason}` : ''}. Prepare a new set.`);
  process.exit(1);
}
const prepared = Object.fromEntries(scenes.map(({ take }) => [take, readFile(path.join(SET, take, 'prepare-receipt.json'))]));
// The page's "chosen from N tries": a line is a Qwen try only when root names the served model and body_sha256 is a request's 64 hex.
const tries = (() => { try { return fs.readFileSync(path.join(path.dirname(SET), 'tries.jsonl'), 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line)).filter(row => typeof row.root === 'string' && /^[0-9a-f]{64}$/.test(row.body_sha256 || '')); } catch { return null; } })();
// Qwen's side of the data root: the replies a click looks up by body hash, the bodies prepare.py --owed filed
// as still owed, and what the owner's last paste said. Hashes only: none of these names a scene.
const PREPARED = path.join(OUT, 'prepared'), REPLIES = path.join(PREPARED, 'replies'), LAST_ASK = path.join(PREPARED, 'last-ask.json');
const PASTE = `python -B ${path.join(HERE, 'ask_la.py')}`;
function qwenStatus() {
  let owed = null, last = null;
  try { owed = readFile(path.join(PREPARED, 'owed', 'owed.json')).bodies.filter(name => !fs.existsSync(path.join(REPLIES, `${name}.json`))).length; } catch { /* nothing filed yet */ }
  try {
    const doc = readFile(LAST_ASK);
    last = { finished_unix: doc.finished_unix, stopped_at: doc.stopped_at, reason: doc.reason, step: doc.step, needed: doc.needed, answered: doc.answered, still_needed: doc.still_needed, root: doc.root };
  } catch { /* no paste yet */ }
  return { owed, last_ask: last, paste: PASTE };
}

function packRoot() {
  if (room === 'real') return path.resolve(refs.content_packs);
  const packs = path.join(run, 'packs');
  const items = ['echo', 'victoria'].map(site => installPack(packs, site));
  fs.writeFileSync(path.join(packs, 'index.json'), JSON.stringify({ schemaVersion: 1,
    packs: items.map(item => ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  return packs;
}
const PACKS = packRoot(), binding = readActiveBinding(PACKS);
const FLOOR = path.join(PACKS, BRAIN_BASE.split('/').at(-1), binding.content_revision, 'floor.json');
const privateNotes = [];
const same = (a, b) => Core.canonicalJson(a) === Core.canonicalJson(b);
// PowerShell 5.1 writes UTF-16 with `>` and a UTF-8 BOM with Set-Content.
function readPrivateJson(file) {
  const bytes = fs.readFileSync(file), text = bytes[0] === 0xff && bytes[1] === 0xfe ? bytes.toString('utf16le') : bytes.toString('utf8');
  return JSON.parse(text.replace(/^﻿/, ''));
}
const privateFile = name => path.join(PRIVATE, name);

function loadRegistration() {
  const file = privateFile('registration-revision-3.json');
  if (room === 'real' && fs.existsSync(file)) {
    try {
      const raw = readPrivateJson(file), doc = raw.registration || raw;
      const result = Core.validateDocument(doc, { binding });
      if (doc.revision === 3 && result.ok) return { doc, source: 'export', label: 'REAL SOCKETS · registration revision 3', file };
      privateNotes.push(`registration export refused: revision ${doc.revision}, valid ${result.ok}`);
    } catch (error) { privateNotes.push('registration export refused: ' + privateReason(error)); }
  }
  const doc = Core.sealDocument(Core.normalizeDocument(authoredDocument(binding, Core)));
  if (!Core.validateDocument(doc, { binding }).ok) throw Error('authored registration is invalid for the active pack');
  return { doc, source: 'authored', label: 'AUTHORED SOCKETS' };
}
// Your sofa mark, found by its schema, never by its file's name, and checked by its seal against
// the export it names.
function findMark() {
  let names = [];
  try { names = fs.readdirSync(PRIVATE, { withFileTypes: true }).filter(entry => entry.isFile() && entry.name.toLowerCase().endsWith('.json')).map(entry => entry.name); } catch { return null; }
  const marks = names.flatMap(name => {
    try { const doc = readPrivateJson(privateFile(name)); return doc?.schema === MARK_SCHEMA ? [{ file: privateFile(name), doc, at: fs.statSync(privateFile(name)).mtimeMs }] : []; }
    catch { return []; }
  });
  if (marks.length > 1) privateNotes.push(`${marks.length} sofa marks in the private folder: using the newest`);
  return marks.sort((a, b) => b.at - a.at)[0] || null;
}
function loadCouch() {
  const found = room === 'real' ? findMark() : null;
  if (found && registration.source !== 'export') privateNotes.push('your sofa mark needs the registration export it names, so a DECLARED point stands in');
  else if (found) {
    try {
      Sofa.validateSofaTarget(found.doc, { binding, equipment_ref: { revision: registration.doc.revision, digest: registration.doc.digest } },
        { sealDesk: sealRegisteredDeskTarget, digest: Core.digest });
      return { point: found.doc.seat.point_m, source: 'sofa_mark', label: 'YOUR SOFA MARK', mark: found.doc, file: found.file };
    } catch (error) { privateNotes.push(`sofa mark refused by its seal: ${privateReason(error)}`); }
  }
  const declared = declaredPoint();
  return { point: declared.point, source: 'declared', label: 'DECLARED', where: declared.where, standIn: standInSofa(declared.point, registration.doc) };
}
function floorCentre() {
  const polygon = JSON.parse(fs.readFileSync(FLOOR, 'utf8')).walkable_polygon;
  return [0, 1].map(axis => Math.round(polygon.reduce((sum, corner) => sum + corner[axis], 0) / polygon.length * 100) / 100);
}
// Where the script says the couch is, never read from the sofa mark. A private point if one was
// declared, or else the middle of this pack's floor. The page labels it DECLARED either way.
function declaredPoint() {
  const file = privateFile('couch-declared.json');
  if (room === 'real' && fs.existsSync(file)) {
    try {
      const point = readPrivateJson(file).point_m;
      if (Array.isArray(point) && point.length === 3 && point.every(Number.isFinite)) return { point, where: 'couch-declared.json' };
      privateNotes.push('couch-declared.json refused: point_m must be [x, y, z]');
    } catch (error) { privateNotes.push('couch-declared.json refused: ' + privateReason(error)); }
  }
  return { point: [...floorCentre(), SEAT_HEIGHT_M], where: 'the middle of the floor' };
}
const registration = loadRegistration(), couch = loadCouch();

/* ---------- hop 5's re-fit: the set's camera placed on your mark, run only when an input's digest changed ---------- */
// Your mark's fits are private. Without your mark, a public stand-in seat at the DECLARED point
// places the picture's couch, and its fit is private too in your real scan.
const FITS = room === 'real' ? path.join(PRIVATE, 'fits') : path.join(OUT, 'fits');
function placement() {
  fs.mkdirSync(FITS, { recursive: true });
  const mine = couch.source === 'sofa_mark';
  const write = (name, value) => { const file = path.join(FITS, name); fs.writeFileSync(file, JSON.stringify(value) + '\n'); return file; };
  const files = { mark: mine ? couch.file : write('stand-in-seat.json', couch.standIn),
    export: registration.file && mine ? registration.file : write('stand-in-registration.json', { registration: registration.doc }),
    floor: FLOOR, facing: mine && fs.existsSync(privateFile('facing.json')) ? privateFile('facing.json') : null };
  const fit = setInfo.fit && path.join(SET, setInfo.fit.folder);
  // Every file the re-fit reads, from the set's fit as well: the clicks and calibration it copies and fits from.
  const clicks = fit && setInfo.marks_take ? path.join(fit, `clicks-${setInfo.marks_take.take}.json`) : null, calibration = fit ? path.join(fit, 'camera-calibration.json') : null;
  const want = { mark: sha256(files.mark), export: sha256(files.export), floor: sha256(files.floor), facing: files.facing ? sha256(files.facing) : null,
    set_worksheet: fit ? sha256(path.join(fit, 'worksheet.json')) : null, clicks: clicks && fs.existsSync(clicks) ? sha256(clicks) : null,
    calibration: calibration && fs.existsSync(calibration) ? sha256(calibration) : null, marks_take: setInfo.marks_take?.digest || null };
  const find = () => fs.readdirSync(FITS, { withFileTypes: true }).filter(entry => entry.isDirectory()).flatMap(entry => {
    try { const receipt = readFile(path.join(FITS, entry.name, 'refit-receipt.json')); return receipt.set === setInfo.set && receipt.result === 'registered' && same(receipt.inputs, want) ? [{ folder: entry.name, receipt }] : []; }
    catch { return []; }
  }).sort((a, b) => b.receipt.refit_unix - a.receipt.refit_unix)[0] || null;
  let found = find(), refused = null, reused = !!found;
  if (!found) {
    try {
      execFileSync('python', ['-B', '-X', 'utf8', path.join(HERE, 'scene', 'prepare.py'), '--refit', '--set', SET, '--mark', files.mark, '--export', files.export,
        '--floor', files.floor, ...(files.facing ? ['--facing', files.facing] : []), '--fits', FITS], { encoding: 'utf8', windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], timeout: 120000 });
    } catch (error) { refused = String(error.stderr || error.message).trim().split('\n').at(-1).replace(/\s*\(see .*\)$/, ''); }
    found = find();
  }
  const kinds = { mark: mine ? 'your mark' : 'a stand-in seat', export: registration.source === 'export' ? 'your export' : 'authored sockets', floor: 'floor', facing: 'facing.json',
    clicks: 'clicks', calibration: 'calibration' };
  const labels = { mark: mine ? 'your sofa mark' : 'stand-in-seat.json', export: registration.source === 'export' ? 'registration-revision-3.json' : 'stand-in-registration.json', floor: 'floor.json', facing: 'facing.json',
    clicks: clicks && `${setInfo.fit.folder}/${path.basename(clicks)}`, calibration: calibration && `${setInfo.fit.folder}/camera-calibration.json` };
  Object.assign(files, { clicks, calibration });
  const read = Object.keys(kinds).filter(key => files[key]).map(key => ({ path: labels[key], sha256: want[key],
    recorded: { kind: kinds[key], by: found ? `refit-receipt.json of fit ${found.folder}` : 'no re-fit', sha256: found ? found.receipt.inputs[key] : null } }));
  const where = mine ? 'your sofa mark' : `a public stand-in seat at the DECLARED point (${couch.where})`;
  if (!found) return { registered: false, summary: `the re-fit on ${where} was refused: ${refused || 'no fit registered'}`, read, wrote: [], tools: {}, stand_in: TABLE.hops[4].stand_ins[4], files };
  const roomModel = path.join(FITS, found.folder, 'room-model.jsonl');
  return { registered: true, folder: found.folder, reused, files, read, tools: found.receipt.tools,
    summary: `re-fit ${found.receipt.transform_id} on ${where}${reused ? ', unchanged since an earlier start' : ', at this start'}${found.receipt.facing_rule ? `; the reader faces ${found.receipt.facing_rule}` : ''}`,
    wrote: [{ path: `fits/${found.folder}/room-model.jsonl`, sha256: found.receipt.room_model_sha256 }],
    stand_in: mine ? TABLE.hops[4].stand_ins[4] : `re-fit at page start: the pose placed on a public stand-in seat at the DECLARED point, not your sofa mark`,
    room_model: { path: roomModel, sha256: found.receipt.room_model_sha256, by: `refit-receipt.json of fit ${found.folder}` },
    transform: mine ? readFile(path.join(FITS, found.folder, 'transform.json')) : null };
}
const placed = placement();
const roomModel = placed.room_model || { path: path.join(SET, setInfo.fit.folder, 'room-model.jsonl'), sha256: setInfo.fit.room_model_sha256, by: 'set.json' };
// The invented camera's place in the room model clicks use, for the page's marker. Never printed.
const cameraM = (() => {
  const entry = JSON.parse(fs.readFileSync(roomModel.path, 'utf8').trim().split('\n').at(-1));
  return entry.cameras.filter(item => item.camera_id === CAMERA_ID).at(-1).pose.room_from_camera.slice(0, 3).map(row => row[3]);
})();

function sourceIsPinned() {
  try { execFileSync('git', ['-C', ROOT, 'diff', '--quiet', refs.base.sha, '--', 'app/src'], { stdio: 'ignore', windowsHide: true }); return true; }
  catch { return false; }
}
const pinned = sourceIsPinned();
// The startup line: with both of your files saved, each fixture against the stated facing and the
// spot, through the brain's own aim.py, in words only.
const stated = couch.source === 'sofa_mark' && placed.transform
  ? { seat_m: couch.mark.seat.point_m, facing_unit: placed.transform.facing_unit, spot_m: couch.mark.seat.point_m.map((v, i) => v + (i === 2 ? SPOT_ABOVE_SEAT_M : 0)) } : null;
function startBrain() {
  try {
    // An authored room has no household data, so its geometry stays out of the private folder.
    return prepareBrain({ registration, couch, startup: stated, geometryDirectory: room === 'real' ? PRIVATE : run, runDirectory: run, outputRoot: OUT, notes: privateNotes, refs });
  } catch (error) {
    console.error(`The lighting brain could not run, so there is nothing to show. It needs python on the PATH.\n${error.message}`);
    return process.exit(1);
  }
}
const brain = startBrain();
const startup = brain.startup ? `${brain.startup.join(', ')}${brain.startup.some(words => words.endsWith('clear')) ? '' : '. None passes: say which way to turn the reader, and an agent writes facing.json'}` : null;

/* ---------- what this page built when it started: hops 5's re-fit, 14 and 20 ---------- */
const couchTools = ['tools/den-sim/run.mjs', 'tools/den-sim/brain.mjs', 'tools/den-room/brain-targets.mjs', 'app/src/home-lighting-sofa-target.js'];
function couchRow() {
  if (couch.source === 'sofa_mark') {
    return { n: 14, word: 'ADAPTER', ran: 'built at page start', tool: { path: 'tools/den-room/brain-targets.mjs', pin: 'HEAD' }, tools: headTools(couchTools),
      summary: 'Your sofa mark, checked by its seal against your registration export, in the older brain\'s geometry.',
      read: placed.read.filter(file => ['your mark', 'your export'].includes(file.recorded.kind)), wrote: [],
      stand_ins: ["the older brain's geometry holds the mark: its own 1.5 m rule decides on the couch, and its hand point is the spot"] };
  }
  return { n: 14, word: 'SCRIPTED', ran: 'built at page start', tool: { path: 'tools/den-sim/run.mjs', pin: 'HEAD' }, tools: headTools(couchTools),
    summary: `A DECLARED point (${couch.where}) with a stand-in zone sealed around it. Not your sofa mark.`, read: [], wrote: [],
    stand_ins: ['a DECLARED point with a stand-in zone, not your sofa mark'] };
}
function homeRow() {
  const now = sourceIsPinned();
  return { n: 20, word: 'REAL', ran: 'ran now', tool: { path: 'app/src', pin: 'refs.json base' }, read: [], wrote: [], stand_ins: [],
    tools: [{ path: 'app/src', pin: 'refs.json base', expected: refs.base.sha, actual: now ? refs.base.sha : 'app/src differs from it' }],
    summary: `${now ? 'Unchanged' : 'EDITED'} Home source at ${refs.base.sha.slice(0, 8)}, served from this PC's harness, which answers sign-in, Lab and registration. The light on the room is Home's modeled view.` };
}
const start = { placement: placed, couch: couchRow() };
const context = { room, registration: { source: registration.source, label: registration.label, revision: registration.doc.revision },
  couch: { source: couch.source, label: couch.label }, base: refs.base.sha.slice(0, 8), source_is_pinned: pinned,
  pack_revision: binding.content_revision, private_notes: privateNotes, tools: refs.tools,
  set: { id: setInfo.set, recorded: setInfo.recorded, figure: setInfo.figure, way: setInfo.way, takes: scenes.length, refusal: setInfo.refusal || null, notes: prepareNotes },
  placement: { registered: placed.registered, summary: placed.summary }, startup,
  tries: tries && { count: tries.length, reading: tries.filter(row => row.facts?.activity_hypothesis === 'reading').length },
  brain: { command: brain.command_status } };
const feed = createFeed({ emptyRow: brain.empty_row, couchLabel: couch.label });

/* ---------- one click: the pipeline on a prepared take, then the brain, the receipt and the replay into Home ---------- */
const commands = {}, pressed = new Set(), clickLines = [], startedAt = new Date().toISOString();
const writeRunJson = () => fs.writeFileSync(path.join(run, 'run.json'), JSON.stringify({ ...context, started_at: startedAt, origin: ORIGIN, clicks: clickLines }, null, 2) + '\n');
let activeRun = null, lastClick = null;
// "Run every scene": the same clicks, one table row each, leaving the top line as the last single click set it.
const every = { rows: [], roots: [], verdict: null };
function everyRow(take, result) {
  const view = receiptView(result.receipt), twelve = view.rows[11], lit = result.ok && result.timeline?.summary.lit;
  const boxes = view.boxes || [];
  // Qwen's words, counted over the requests it answered: "reading x4, unclear x1".
  const said = {};
  for (const ask of view.qwen?.asks || []) if (ask.said) said[ask.said.split(',')[0]] = (said[ask.said.split(',')[0]] || 0) + 1;
  const words = Object.entries(said).map(([word, n]) => `${word} x${n}`).join(', ');
  every.rows.push({ take, click: view.click, box: boxes.length ? boxes.map(box => `person ${box.score.toFixed(2)}`).join(', ') : view.boxes ? 'none' : 'rows did not hold',
    qwen: !twelve.reached ? 'not asked' : twelve.word === 'MISSING' ? `MISSING: ${twelve.summary.split(':')[0]}` : `${twelve.word === 'SCRIPTED' ? 'fake vLLM, scripted: ' : 'asked: '}${words}`,
    root: view.qwen?.root || null, beam: lit ? `lit, ${result.timeline.summary.fixture_id.match(/mount-\d+/)?.[0] || result.timeline.summary.fixture_id}` : 'off',
    why: result.receipt.stopped ? `hop ${result.receipt.stopped.hop}: ${result.receipt.stopped.why.replace(/^Qwen has not seen these exact frames: /, '')}` : lit ? '' : result.timeline?.summary.refusal || '' });
  every.roots = [...new Set(every.rows.map(row => row.root).filter(Boolean))];
  every.verdict = every.roots.length > 1 ? `replies from two models (${every.roots.join(', ')}): a table uses one` : null;
}
function startClick(body) {
  const take = body?.take;
  if (!scenes.some(scene => scene.take === take) || !Array.isArray(body.fixtures)) return { ok: false, reason: 'take_unknown' };
  if (activeRun) return { ok: false, reason: 'a_run_is_going' };
  const all = body.every === true;
  if (all && body.first === true) Object.assign(every, { rows: [], roots: [], verdict: null });
  const pressedAgain = !all && pressed.has(take);
  if (!all) { pressed.add(take); lastClick = null; }
  const runId = feed.begin(take);
  const current = activeRun = brain.runClick({ take, prepared: prepared[take], setDir: SET, roomModel, startRecord: { ...start, home: homeRow() }, pressedAgain,
    replies: REPLIES, lastAsk: LAST_ASK }, row => feed.hop({ n: row.n, word: row.word, summary: row.summary, ms: row.ms }));
  current.promise.then(result => {
    if (activeRun !== current) return; // Reloaded while it ran: the page has moved on.
    activeRun = null;
    if (all) everyRow(take, result);
    if (all) { if (result.ok) feed.play(result.timeline, body.fixtures); else feed.fail(result.reason, result.receipt.stopped?.answer ? 'stopped' : 'pipeline_failed'); return; }
    const picture = path.join(result.work, 'picture.png');
    lastClick = { click: result.receipt.click, run: runId, view: receiptView(result.receipt), top: result.top, receipt: result.receipt, said: result.said || null,
      picture: fs.existsSync(picture) ? picture : null, stills: path.join(result.work, 'stills') };
    // The torso line, relative to the limit only: in the console, run.json and the panel.
    if (result.receipt.torso) {
      console.log(`Click ${result.receipt.click}: ${result.receipt.torso.line}`);
      clickLines.push({ click: result.receipt.click, take, torso: result.receipt.torso.line });
      writeRunJson();
    }
    if (!result.ok) { feed.fail(result.reason, result.receipt.stopped?.answer ? 'stopped' : 'pipeline_failed'); return; }
    commands[result.receipt.click] = result.command;
    context.brain.command = result.command_status;
    feed.play(result.timeline, body.fixtures);
  });
  return { ok: true, run: runId };
}
function cancelClick() {
  activeRun?.cancel();
  activeRun = null;
}

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
      return send(res, 200, startClick(body));
    }
    if (req.method !== 'GET') return refuse(res, req.method, pathname, 405);
    if (pathname === '/' && !url.search) { res.writeHead(302, { Location: '/?apartmentRedesign=1&worldZoom=off' }); return res.end(); }
    if (pathname === '/' || pathname === '/index.html') {
      cancelClick();
      feed.reset(); pressed.clear(); lastClick = null; // A page load starts from an empty room, so every load can swing again.
      Object.assign(every, { rows: [], roots: [], verdict: null });
      return send(res, 200, homePage(), { 'Content-Type': TYPES['.html'] });
    }
    if (pathname === '/__sim/panel.js') return send(res, 200, fs.readFileSync(path.join(HERE, 'panel.js')), { 'Content-Type': TYPES['.js'] });
    if (pathname === '/__sim/status') {
      // The person's point, the target and the camera marker's point reach the page here, because Home's markers need them. The panel prints none.
      const click = lastClick && { ...lastClick.view, run: lastClick.run, top_line: lastClick.top, picture: !!lastClick.picture, said: lastClick.said };
      return send(res, 200, { ...context, scenes, camera_m: cameraM, feed: feed.readback(), click, counters, refused: [...refused.values()], run,
        qwen: qwenStatus(), every: { ...every, running: !!activeRun } });
    }
    if (pathname === '/__sim/receipt') return lastClick ? send(res, 200, lastClick.receipt) : refuse(res, req.method, pathname, 404);
    if (pathname === '/__sim/picture') {
      // The last click's frame, read back out of its take's own NV12 plane.
      return lastClick?.picture ? send(res, 200, fs.readFileSync(lastClick.picture), { 'Content-Type': 'image/png' }) : refuse(res, req.method, pathname, 404);
    }
    if (pathname === '/__sim/command') {
      // A display, fetched once per click. Nothing on this page or harness can send it.
      const click = Number(url.searchParams.get('click'));
      return Object.hasOwn(commands, click) ? send(res, 200, commands[click]) : refuse(res, req.method, pathname, 404);
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
    `Prepared set ${setInfo.set}, recorded ${setInfo.recorded}: ${scenes.length} takes. Each click re-checks its take by hash, holds the detector rows prepare recorded to their checks, and runs the pinned tools on them, with an INVENTED CAMERA. No model runs on a click: Qwen's words are recorded replies, looked up by each request's exact bytes.`,
    ...(qwenStatus().owed ? [`Qwen still owes ${qwenStatus().owed} replies. In a new PowerShell window, paste: ${PASTE}`] : []),
    `Camera registration: ${placed.summary}`,
    ...(startup ? [`Fixtures for the stated facing, at the spot: ${startup}`] : []),
    ...prepareNotes.map(note => 'Note: ' + note), ...privateNotes.map(note => 'Note: ' + note), `app/src ${pinned ? 'matches' : 'DIFFERS FROM'} ${context.base}`,
    `Run output: ${run}`, 'Nothing is sent. Press Ctrl+C to stop.'].join('\n'));
  writeRunJson();
  // The pack catalog must resolve before the page asks for it.
  if (!readPackIndex(PACKS).packs.some(item => item.active && item.manifest.siteId === 'victoria')) console.error('No active Victoria pack in ' + PACKS);
});
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => { cancelClick(); feed.dispose(); server.closeAllConnections(); server.close(() => process.exit()); });
