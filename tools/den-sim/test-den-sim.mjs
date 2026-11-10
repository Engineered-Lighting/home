// The den simulation's click side, without a browser (plan, "Done means"): the data root; test 7,
// the top line, on fixtures under all six conditions and on the real chain; the hop table's count;
// all five takes with no reply on file (test 1's outcomes on a fake vLLM's replies are in
// test-den-sim-honesty.mjs); test 2's searches over click-path code, what clicks wrote and
// what the harness served; test 6 on real clicks (the argv, the models' names, an edited rows file);
// test 5's frame hash on a click; test 9's step-5 parts (bodies unchanged under re-fits, a fit 1 m off
// the mark not on the couch, nothing printed), test 12 and test 19's private half on a sealed invented
// export and mark in a scratch data root; and the site id. Each check has a control
// that must make it fail. Hop 6's own conditions, one by one, are in test_chain.py.
//
//   set DEN_SIM_SET=<a prepared set>, DEN_SIM_HOME and LOCALAPPDATA to scratch folders, then
//   node --test tools\den-sim\test-den-sim.mjs
//
// The harness tests need DEN_SIM_SET and skip without it. Each starts run.mjs with DEN_SIM_HOME and
// LOCALAPPDATA both pointed at a new scratch folder of its own, so nothing reads or writes the
// owner's private folder or runs. The real-room test reads the real scan pack's binding and floor,
// read-only, as the plan's own probe did; its export and mark are invented and sealed here.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, spawn } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import Client from '../../app/src/home-lighting-brain-client.js';
import Core from '../../app/src/home-equipment-registration.js';
import Sofa from '../../app/src/home-lighting-sofa-target.js';
import { sealRegisteredDeskTarget } from '../../app/src/home-3d/registered-desk-target.mjs';
import { readActiveBinding } from '../../web-gateway/equipment-registration.mjs';
import { authoredDocument } from '../fixtures/apartment-home-shell-fixtures.mjs';
import { chainRequest } from './brain.mjs';
import { nextVersion, writeCouch } from './couch_to_room.mjs';
import { buildReceipt, finalRow, personColumn, pictureNote, receiptView, sha256 } from './receipt.mjs';
import { topLine } from './top_line.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url)), ROOT = path.resolve(HERE, '../..');
const TABLE = JSON.parse(fs.readFileSync(path.join(HERE, 'hops.json'), 'utf8'));
const REFS = JSON.parse(fs.readFileSync(path.join(HERE, 'refs.json'), 'utf8'));
const SET = process.env.DEN_SIM_SET ? path.resolve(process.env.DEN_SIM_SET) : null;
const NEEDS_SET = SET && fs.existsSync(path.join(SET, 'set.json')) ? false : 'needs DEN_SIM_SET=<a prepared set>';
const ORIGIN = 'http://127.0.0.1:18180';
const scratch = prefix => fs.mkdtempSync(path.join(os.tmpdir(), prefix));
const sleep = ms => new Promise(done => setTimeout(done, ms));
// The site id, read from the one existing constant rather than written here.
const SITE = Client.BASE.split('/').at(-1);
// Click-path code: what a click runs or what serves it. The display file and the recorded side aside.
const CLICK_PATH = ['run.mjs', 'brain.mjs', 'feed.mjs', 'panel.js', 'receipt.mjs', 'top_line.mjs', 'data-root.mjs', 'hops.json', 'chain.py', 'brain_run.py',
  'dry_run.py', 'scene/frame_hash.py', 'couch_to_room.mjs', 'person.py'];
// The plan's step 4 words up to hop 12, with no sofa mark saved and no reply on file: a click with a
// person stops at hop 12, "Qwen has not seen these exact frames".
const STEP4_UNASKED = { 1: 'SCRIPTED', 2: 'SCRIPTED', 3: 'ADAPTER', 4: 'ADAPTER', 5: 'ADAPTER', 6: 'ADAPTER', 7: 'REAL', 8: 'REAL', 9: 'ADAPTER', 10: 'SCRIPTED',
  11: 'ADAPTER', 12: 'MISSING' };
const NOT_SEEN = /^Qwen has not seen these exact frames: 5 of 5 bodies have no reply on file$/;

test('the data root is DEN_SIM_HOME, or else the home folder, and never %LOCALAPPDATA%', t => {
  const tmp = scratch('den-sim-root-');
  t.after(() => fs.rmSync(tmp, { recursive: true, force: true }));
  const home = path.join(tmp, 'home'), lad = path.join(tmp, 'lad');
  const plant = (root, name) => {
    fs.mkdirSync(path.join(root, 'prepared', name), { recursive: true });
    fs.writeFileSync(path.join(root, 'prepared', name, 'set.json'), JSON.stringify({ schema: 'den-sim-prepared-set-1', refusal: null, ended_unix: 1 }));
  };
  plant(path.join(lad, 'den-sim'), 'aaaaaaaaaaaa');   // the control: a set where the old root was
  const env = { ...process.env, USERPROFILE: home, HOME: home, LOCALAPPDATA: lad };
  delete env.DEN_SIM_HOME;
  const ask = () => JSON.parse(execFileSync(process.execPath, ['--input-type=module', '-e',
    `const m = await import(${JSON.stringify(pathToFileURL(path.join(HERE, 'data-root.mjs')).href)}); const root = m.dataRoot();`
    + 'console.log(JSON.stringify({ root, set: m.newestPreparedSet(root) }));'], { env, encoding: 'utf8' }));
  let got = ask();
  assert.equal(got.root, path.join(home, 'den-sim'));
  assert.equal(got.set, null, 'the planted %LOCALAPPDATA% set is not read');
  plant(path.join(home, 'den-sim'), 'bbbbbbbbbbbb');
  got = ask();
  assert.equal(got.set, path.join(home, 'den-sim', 'prepared', 'bbbbbbbbbbbb'), 'a set under the home folder is found');
  const custom = JSON.parse(execFileSync(process.execPath, ['--input-type=module', '-e',
    `const m = await import(${JSON.stringify(pathToFileURL(path.join(HERE, 'data-root.mjs')).href)}); console.log(JSON.stringify(m.dataRoot()));`],
  { env: { ...env, DEN_SIM_HOME: path.join(tmp, 'elsewhere') }, encoding: 'utf8' }));
  assert.equal(custom, path.join(tmp, 'elsewhere'));
  // A newer set that prepare refused is passed over, and named, so the page can say why it shows an older one.
  fs.mkdirSync(path.join(home, 'den-sim', 'prepared', 'cccccccccccc'), { recursive: true });
  fs.writeFileSync(path.join(home, 'den-sim', 'prepared', 'cccccccccccc', 'set.json'),
    JSON.stringify({ schema: 'den-sim-prepared-set-1', set: 'cccccccccccc', refusal: { hop: 4, reason: 'check.py: FAIL' }, ended_unix: 2 }));
  const refused = JSON.parse(execFileSync(process.execPath, ['--input-type=module', '-e',
    `const m = await import(${JSON.stringify(pathToFileURL(path.join(HERE, 'data-root.mjs')).href)}); const root = m.dataRoot(); const set = m.newestPreparedSet(root);`
    + 'console.log(JSON.stringify({ set, since: m.refusedSince(root, set).map(row => [row.id, row.refusal.hop]) }));'], { env, encoding: 'utf8' }));
  assert.deepEqual(refused, { set: path.join(home, 'den-sim', 'prepared', 'bbbbbbbbbbbb'), since: [['cccccccccccc', 4]] });
});

/* ---------- test 7 on fixtures: the six conditions ---------- */

// A click where every hop reads its finished word: what the end of the plan looks like.
function finished() {
  const rows = TABLE.hops.map(hop => ({ n: hop.n, name: hop.name, word: hop.word, reached: true, ran: 'ran now', stand_ins: hop.word === 'REAL' ? [] : [hop.stand_ins[0]],
    tools: [{ path: hop.tool.path, pin: hop.tool.pin, expected: 'blob-1', actual: 'blob-1' }], read: [], wrote: [] }));
  rows[5].wrote = [{ path: 'rows.jsonl', sha256: 'r1' }];
  rows[6].read = [{ path: 'take/frames.csv', sha256: 't1', recorded: { kind: 'take', by: 'prepare-receipt.json', sha256: 't1' } }, { path: 'rows.jsonl', sha256: 'r1', from_hop: 6 }];
  rows[6].wrote = [{ path: 'observe/observations.jsonl', sha256: 'o1' }];
  rows[7].read = [{ path: 'observe/observations.jsonl', sha256: 'o1', from_hop: 7 }];
  rows[13].read = [{ path: 'your sofa mark', sha256: 'm1', recorded: { kind: 'your mark', by: 'refit-receipt.json', sha256: 'm1' } },
    { path: 'registration-revision-3.json', sha256: 'e1', recorded: { kind: 'your export', by: 'refit-receipt.json', sha256: 'e1' } }];
  return { schema: 'den-sim-run-receipt-1', click: 1, take: 'aaaaaaaaaaaa', rows, stopped: null, pressed_again: false, prepare_refusal: null, aim_check: { within_tolerance: true } };
}
const edited = change => { const receipt = finished(); change(receipt); return topLine(TABLE, receipt); };
// Stopped at hop n by the pipeline's own answer: the hops after it are not reached.
const stoppedAt = (n, why) => receipt => {
  receipt.stopped = { hop: n, why, answer: true };
  receipt.aim_check = null;
  for (const row of receipt.rows) if (row.n > n) Object.assign(row, { reached: false, ran: 'not reached on this click', read: [], wrote: [], tools: [] });
};

test('test 7: the top line reads yes only under the six conditions, and its count is the table\'s', () => {
  const yes = topLine(TABLE, finished());
  assert.equal(yes.verdict, 'yes', JSON.stringify(yes.reasons));
  assert.equal(yes.line, 'Connected start to finish: yes');
  const { not_reached: _, ...count } = yes.count;
  assert.deepEqual(count, TABLE.count);
  assert.deepEqual(TABLE.count, { real: 3, adapter: 19, adapter_stand_in: 10, adapter_written: 9, scripted: 4, missing: 0 }, 'the plan\'s count under the hop table');
  assert.equal(yes.count_line, '3 real, 19 adapter (10 real tools with a named stand-in, 9 written for this simulation), 4 scripted, 0 missing');
  const controls = {
    'a removed row': [r => r.rows.splice(10, 1), 1],
    'one recorded digest broken': [r => { r.rows[6].read[0].sha256 = 't2'; }, 2],
    'a recorded input the table does not list': [r => { r.rows[6].read[0].recorded.kind = 'truth'; }, 2],
    'a stand-in labelled REAL': [r => { r.rows[6].stand_ins = ['a typed joint']; }, 3],
    'a new unnamed adapter': [r => { r.rows[8].stand_ins = ['a join nobody named']; }, 3],
    'an adapter that names nothing': [r => { r.rows[8].stand_ins = []; }, 3],
    'an edited archived tool': [r => { r.rows[6].tools[0].actual = 'blob-2'; }, 4],
    'an uncommitted edit to a tool on this branch': [r => { r.rows[17].tools[0].actual = 'blob-2'; }, 4],
    'an output file left from an earlier click': [r => { r.rows[7].read[0].sha256 = 'o0'; }, 2],
    'a mark with no export': [r => Object.assign(r.rows[13], { word: 'SCRIPTED', read: [], stand_ins: ['a DECLARED point with a stand-in zone, not your sofa mark'] }), 3],
    'an aim check out of tolerance': [r => { r.aim_check = { within_tolerance: false, why: 'Home did not follow the Decision' }; }, 5],
    'a row reading MISSING': [r => { r.rows[25].word = 'MISSING'; }, 6],
    'a phone click with one recorded digest broken': [r => { stoppedAt(15, 'not reading: Qwen said phone use')(r); r.rows[6].read[0].sha256 = 't2'; }, 2],
    'two rows for one hop': [r => r.rows.push({ ...r.rows[3] }), 1],
    'a hop not reached, with nothing stopped': [r => { Object.assign(r.rows[9], { reached: false, ran: 'not reached on this click' }); }, 1],
    'every hop past 6 not reached, with nothing stopped': [r => r.rows.slice(6).forEach(row => Object.assign(row, { reached: false })), 1],
    'a hop before the stop not reached': [r => { stoppedAt(15, 'not reading: Qwen said phone use')(r); r.rows[8].reached = false; }, 1],
    'a stop at a hop the table does not have': [r => stoppedAt(99, 'a hop nobody numbered')(r), 1],
    'a read of a file a later hop wrote': [r => { r.rows[8].wrote = [{ path: 'later.jsonl', sha256: 'l1' }]; r.rows[6].read.push({ path: 'later.jsonl', sha256: 'l1', from_hop: 9 }); }, 2],
    'no aim check ran, and nothing else wrong': [r => { r.aim_check = null; }, 5],
    'pressed again, with a break': [r => { r.pressed_again = true; r.stopped = { hop: 3, why: 'frame 7 changed', answer: false }; r.rows[2].word = 'MISSING'; }, 6],
    'pressed again, with a MISSING row': [r => { r.pressed_again = true; r.rows[10].word = 'MISSING'; }, 6],
    'pressed again, with a recorded digest broken': [r => { r.pressed_again = true; r.rows[6].read[0].sha256 = 't2'; }, 2],
    'a detector tool held to another pin than the table names': [r => { r.rows[5].tools.push({ path: 'stack/services/perception-lab/rtmpose_worker.py', pin: 'HEAD', expected: 'b1', actual: 'b1' }); }, 4],
    'a detector tool the table pins to nothing': [r => { r.rows[5].tools.push({ path: 'tools/elsewhere/x.py', pin: 'HEAD', expected: 'b1', actual: 'b1' }); }, 4],
  };
  for (const [name, [change, condition]] of Object.entries(controls)) {
    const got = edited(change);
    assert.equal(got.verdict, 'not yet', name);
    assert.equal(got.line, 'Connected start to finish: not yet', name);
    assert.ok(got.reasons.some(reason => reason.condition === condition), `${name} is condition ${condition}: ${JSON.stringify(got.reasons)}`);
  }
  for (const [name, n, why] of [['a phone click', 15, 'not reading: Qwen said phone use'], ['a standing click', 15, 'not on the couch'], ['every fixture refuses', 17, 'no fixture can light the spot']]) {
    const got = edited(stoppedAt(n, why));
    assert.equal(got.verdict, 'stopped', name);
    assert.match(got.line, new RegExp(`^This click stopped at ${TABLE.hops[n - 1].name}: ${why}\\. That's the pipeline answering, not a break\\.$`));
    assert.notEqual(got.verdict, 'yes');
  }
  assert.match(edited(stoppedAt(15, 'x')).count_line, /11 not reached$/);
  // Step 5 as built: hop 15 reads ADAPTER and stops nothing, and the older brain still decides. A click whose person is
  // not on the couch, the brain lighting anyway under its 1.5 m rule, reads "not yet": hops 16 and 24 to 26 aren't built,
  // and hop 10 is still typed. Never "stopped at Person state": hop 15 feeds nothing until step 6.
  const stepFive = r => {
    Object.assign(r.rows[14], { person: [{ track_id: 'trk-1', on_couch: false, failed: ['position'] }] });
    for (const n of [16, 24, 25, 26]) Object.assign(r.rows[n - 1], { word: 'MISSING', stand_ins: [], tools: [] });
    Object.assign(r.rows[9], { word: 'SCRIPTED', stand_ins: ["the older brain's occupancy typed in brain_run.py; the track list only decides that it runs"] });
    r.aim_check = null;
  };
  const off = edited(stepFive);
  assert.equal(off.line, 'Connected start to finish: not yet');
  assert.deepEqual(off.reasons.filter(reason => reason.hop === 15), [], 'hop 15 itself holds');
  assert.deepEqual([...new Set(off.reasons.filter(reason => reason.condition === 6).map(reason => reason.hop))], [16, 24, 25, 26]);
  assert.equal(off.count_line, '3 real, 14 adapter (9 real tools with a named stand-in, 5 written for this simulation), 5 scripted, 4 missing');
  // The count follows hop 15's word: the same click with hop 15 MISSING counts one written adapter fewer (control).
  assert.equal(edited(r => { stepFive(r); Object.assign(r.rows[14], { word: 'MISSING', stand_ins: [] }); }).count_line,
    '3 real, 13 adapter (9 real tools with a named stand-in, 4 written for this simulation), 5 scripted, 5 missing');
  // If the older brain refuses on that click, the rows before its stop don't hold (16 isn't built), so still "not yet".
  assert.equal(edited(r => { stepFive(r); stoppedAt(17, 'Not reading · no spotlight')(r); }).verdict, 'not yet');
  assert.equal(edited(r => { r.pressed_again = true; }).line, 'Pressed again: press Reset for a new verdict');
  assert.equal(edited(r => { r.pressed_again = true; r.aim_check = null; }).verdict, 'pressed again', 'a second press needs no fresh aim check');
  assert.equal(edited(r => { r.pressed_again = true; stoppedAt(15, 'not on the couch')(r); }).verdict, 'stopped', 'a second press the pipeline answered still says so');
  assert.equal(edited(r => { r.prepare_refusal = { hop: 4, reason: 'check.py: FAIL' }; }).line, 'Prepare stopped at hop 4: check.py: FAIL');
  assert.equal(edited(r => { r.stopped = { hop: 7, why: 'replay.py exit 2', answer: false }; }).verdict, 'not yet', 'a break is never an answer');
  // Hop 6's pins, by path prefix: each of its real tool kinds held to its own pin reads yes (control for the two above).
  const pinned = [['tools/den-sim/chain.py', 'HEAD'], ['tools/den-detect/requirements.lock', 'HEAD'], ['tools/den-detect/detect.py, as it ran', 'HEAD'],
    ['weights yolo26s.pt', 'tools/den-detect/requirements.lock'], ['refs.json tools.rung-2-observe commit', 'refs.json tools.rung-2-observe'],
    ['stack/services/perception-lab/rtmpose_worker.py', 'refs.json tools.rung-2-observe'], ['tools/den-observe/detections.py', 'refs.json tools.rung-2-observe'],
    ['tools/den-take/check.py', 'refs.json tools.rung-2-observe']];
  assert.equal(edited(r => r.rows[5].tools.push(...pinned.map(([file, pin]) => ({ path: file, pin, expected: 'b1', actual: 'b1' })))).verdict, 'yes');
});

test('a receipt files a break under the hop that broke, and never leaves a hop before a stop unreached', () => {
  const row = (n, word = TABLE.hops[n - 1].word) => ({ n, name: TABLE.hops[n - 1].name, word, reached: true, ran: 'ran now', summary: 'ran', tools: [], read: [], wrote: [], stand_ins: [] });
  const broke = { hop: 7, why: 'replay.py exit 2: refused', answer: false };
  // The hop that broke, with no row of its own: MISSING with why, not "not reached".
  assert.deepEqual([finalRow(null, 7, broke).word, finalRow(null, 7, broke).reached, finalRow(null, 7, broke).note], ['MISSING', true, broke.why]);
  // A recorded hop whose re-check refused keeps its row, now MISSING.
  const four = finalRow(row(4), 4, { hop: 4, why: 'check-2.json sits beside it', answer: false });
  assert.deepEqual([four.word, four.reached, four.note, four.ran], ['MISSING', true, 'check-2.json sits beside it', 'ran now']);
  // Rows before the break stay as they ran; rows after it are not reached.
  assert.deepEqual(finalRow(row(6), 6, broke), row(6));
  assert.equal(finalRow(row(8), 8, broke).reached, false);
  // An answer keeps its row's own word.
  assert.equal(finalRow(row(6, 'SCRIPTED'), 6, { hop: 6, why: 'the detector saw nobody', answer: true }).word, 'SCRIPTED');
  // No row, though nothing stopped, or before the stop: MISSING, reached.
  for (const stop of [null, { hop: 9, why: 'too briefly to ask', answer: true }]) {
    const got = finalRow(null, 8, stop);
    assert.deepEqual([got.word, got.reached, got.ran], ['MISSING', true, 'no row on this click'], JSON.stringify(stop));
  }
  // Through the top line: the count names the MISSING row, and reaches no "26 not reached".
  const receipt = { rows: TABLE.hops.map(hop => finalRow(hop.n <= 6 ? row(hop.n) : null, hop.n, broke)), stopped: broke, aim_check: null };
  const top = topLine(TABLE, receipt);
  assert.equal(top.verdict, 'not yet');
  assert.equal(top.count.missing, 1); assert.equal(top.count.not_reached, 19);
  assert.ok(top.reasons.some(reason => reason.condition === 6 && reason.hop === 7), JSON.stringify(top.reasons));
});

test('a hop built at page start keeps the word it was built with, greyed, on a click that stopped before it', () => {
  const stop = { hop: 6, why: 'the detector saw nobody, nothing was asked', answer: true };
  const couch = { n: 14, name: TABLE.hops[13].name, word: 'SCRIPTED', reached: true, ran: 'built at page start', summary: 'A DECLARED point', tools: [], read: [], wrote: [], stand_ins: ['a DECLARED point with a stand-in zone, not your sofa mark'] };
  const kept = finalRow(couch, 14, stop);
  assert.deepEqual([kept.word, kept.reached, kept.ran, kept.summary], ['SCRIPTED', false, 'built at page start; not reached on this click', 'A DECLARED point']);
  const fifteen = finalRow({ ...couch, n: 15, word: 'ADAPTER', ran: 'ran now' }, 15, stop);   // control: a click hop past the stop takes the table's word
  assert.deepEqual([fifteen.word, fifteen.reached, fifteen.summary], [TABLE.hops[14].word, false, undefined]);
  // A page hop that wasn't built (hop 16) takes the table's word, as 24 to 26 do, not a greyed MISSING.
  const sixteen = finalRow({ n: 16, name: TABLE.hops[15].name, word: 'MISSING', reached: true, ran: 'not built at page start', note: 'Not built', tools: [], read: [], wrote: [], stand_ins: [] }, 16, stop);
  assert.deepEqual([sixteen.word, sixteen.reached, sixteen.ran], [TABLE.hops[15].word, false, 'not reached on this click']);
  // Through the top line, the kept row counts as not reached and the click still reads "stopped at".
  const row = n => ({ n, name: TABLE.hops[n - 1].name, word: TABLE.hops[n - 1].word, reached: true, ran: 'ran now', summary: 'ran', tools: [], read: [], wrote: [],
    stand_ins: TABLE.hops[n - 1].word === 'ADAPTER' ? [TABLE.hops[n - 1].stand_ins[0]] : [] });
  const top = topLine(TABLE, { rows: TABLE.hops.map(hop => finalRow(hop.n <= 6 ? row(hop.n) : hop.n === 14 ? couch : null, hop.n, stop)), stopped: stop, aim_check: null });
  assert.deepEqual([top.verdict, top.count.not_reached], ['stopped', 20], JSON.stringify(top.reasons));
});

// person.py's own cards (test_person.py's typed scenes, invented numbers) for hop 15's failure outcomes.
function personCards() {
  const script = ['import json, sys', 'sys.path.insert(0, sys.argv[1])', 'import test_person as tp', 'scenes = dict(held=tp.scene(safe=(False,) * 5), track=tp.scene(confident=False, safe=(True,) * 5), torso=tp.scene(), seated=tp.scene())',
    'for entry in [e for b in scenes["torso"]["bundles"] for e in b["keypoint_series"]]:', '    for joint in entry["joints_room"]:',
    '        joint["status"] = "uncertain" if joint["j"] in tp.person.SHOULDERS + tp.person.HIPS else joint["status"]',
    'print(json.dumps({name: tp.run(scene)[1] for name, scene in scenes.items()}))'].join('\n');
  return JSON.parse(execFileSync('python', ['-B', '-X', 'utf8', '-c', script, HERE], { encoding: 'utf8', windowsHide: true, timeout: 120000 }));
}
const cardReceipt = (cards, couch = {}) => ({ click: 1, take: 'aaaaaaaaaaaa', stopped: null, picture: null,
  rows: TABLE.hops.map(hop => ({ n: hop.n, name: hop.name, word: hop.word, reached: true, ran: 'ran now', summary: 'typed', stand_ins: [],
    ...(hop.n === 14 ? { couch: 'declared', word: 'SCRIPTED', ...couch } : {}), ...(hop.n === 15 ? { person: cards } : {}) })) });

test('the person card says why on every outcome person.py writes, and the Person column says which', () => {
  const cards = personCards();
  assert.deepEqual([cards.held[0].on_couch, cards.held[0].footprint_m, cards.held[0].torso_margin_m], [null, null, null], 'a held track: nothing tested');
  assert.deepEqual([cards.torso[0].on_couch, cards.torso[0].failed, cards.torso[0].torso_margin_m], [false, ['torso'], null], 'no supported torso joint');
  const held = receiptView(cardReceipt(cards.held)).person, torso = receiptView(cardReceipt(cards.torso)).person, seated = receiptView(cardReceipt(cards.seated)).person;
  assert.equal(held.lines[1], 'On the couch: not tested yet: its bundle was not safe to tie to one person, so the reply was held.');
  assert.equal(held.lines[3], 'Engagement: none, support held_ambiguous: every reply was held, so need v0 had none of this track\'s replies.');
  assert.equal(receiptView(cardReceipt(cards.track)).person.lines[1], 'On the couch: not tested yet: its Track was not confident, so the reply was held.',
    'a safe bundle whose Track is not confident is not blamed on the bundle');
  // Failing by a millimetre reads as past the limit, never as the limit itself.
  const edge = { ...cards.seated[0], on_couch: false, failed: ['position', 'height'], footprint_m: 0.151, torso_margin_m: 0.001, torso_from: 'shoulders and hips' };
  assert.equal(receiptView(cardReceipt([edge])).person.lines[1], 'On the couch: no: the position half failed: 0.151 m outside the couch\'s footprint, past its 0.15 m reach; '
    + 'the height half failed: torso 0.001 m over the limit, from the shoulders and hips.');
  assert.equal(torso.lines[1], 'On the couch: no: the height half failed: no supported shoulder or hip in the bundle\'s last 2 s.');
  assert.equal(seated.lines[1], 'On the couch: yes.', 'control: a seated track');
  assert.ok([held, torso].every(card => card.lines.every(line => !/null|undefined|NaN/.test(line))), JSON.stringify([held, torso]));
  // Run every scene's column: a held track is not "none", nor is a MISSING hop 15.
  assert.deepEqual([held, torso, seated].map(personColumn), ['not tested (held)', 'not on the couch', 'on the couch']);
  const missing = cardReceipt(null);
  Object.assign(missing.rows[14], { word: 'MISSING', note: 'the room model\'s current revision holds no seat surface' });
  assert.equal(personColumn(receiptView(missing).person), 'MISSING: the room model\'s current revision holds no seat surface');
  assert.deepEqual([personColumn(null), personColumn({ tracks: [], lines: [] })], ['none', 'none'], 'control: no track reads none');
});

test('the person card says the facing is yours when the re-fit read facing.json', () => {
  const seated = personCards().seated;
  const turned = receiptView(cardReceipt(seated, { couch: 'sofa_mark', word: 'ADAPTER', facing_deg: 90 })).person.lines;
  assert.ok(turned.includes('The reader faces your way, from facing.json: turned 90 degrees from the stated facing, left positive.'), turned.join('\n'));
  const stated = receiptView(cardReceipt(seated, { couch: 'sofa_mark', word: 'ADAPTER', facing_deg: null })).person.lines;   // control: no facing.json
  assert.ok(!stated.some(line => line.includes('facing.json')));
  assert.equal(stated.length, turned.length - 1);
});

test('couch_to_room.mjs places the couch from the seat bytes the re-fit read, replaces an old couch, and versions by den-register\'s rule', { skip: fs.existsSync(path.resolve(REFS.content_packs)) ? false : 'needs the content packs' }, () => {
  const home = scratch('den-sim-couch-'), privateFolder = path.join(home, 'private');
  try {
    invent(privateFolder, [[1.0, 2.0, 0.45], [3.0, 2.0, 0.45], [3.0, 3.0, 0.45], [1.0, 3.0, 0.45]]);
    const seatFile = path.join(privateFolder, 'a-couch.json'), registration = JSON.parse(fs.readFileSync(path.join(privateFolder, 'registration-revision-3.json'), 'utf8')).registration;
    const old = { surface_id: 'sofa', kind: 'seat', geometry: { form: 'polygon-3d/1', vertices_m: [], seat_point_m: [0, 0, 0] }, focal: false };
    const from = path.join(home, 'fit.jsonl');
    fs.writeFileSync(from, `${JSON.stringify({ room_id: 'den', version: 'den-v9', frame: {}, cameras: [], surfaces: [old, { surface_id: 'wall', kind: 'wall' }],
      landmarks: [{ landmark_id: 'sofa-seat', kind: 'seat', surface_id: 'sofa' }] })}\n`);
    const to = path.join(home, 'couch', 'room-model.jsonl');
    assert.throws(() => writeCouch({ seatFile, seatSha256: '0'.repeat(64), registration, from, to, source: 'sofa_mark' }), /^Error: seat_changed_since_refit$/);
    assert.equal(fs.existsSync(to), false, 'nothing written from bytes the re-fit did not read');
    const wrote = writeCouch({ seatFile, seatSha256: sha256(seatFile), registration, from, to, source: 'sofa_mark' });   // control: the bytes it read
    const revision = JSON.parse(fs.readFileSync(to, 'utf8').trim().split('\n').at(-1));
    assert.deepEqual([revision.surfaces.map(s => s.surface_id), revision.landmarks.map(m => m.landmark_id)], [['wall', 'sofa'], ['sofa-seat']], 'one couch, the old one replaced');
    assert.notDeepEqual(revision.surfaces[1].geometry.seat_point_m, old.geometry.seat_point_m);
    // den-register's own records.next_room_version, at its pin, never this module's copy of it.
    const rule = ['import json, sys', 'sys.path.insert(0, sys.argv[1])', 'import prepare', 'prepare.archive(prepare.Path(sys.argv[2]), prepare.pins())', 'import records',
      'print(json.dumps([records.next_room_version(v, "den") for v in sys.argv[3:]]))'].join('\n');
    const theirs = JSON.parse(execFileSync('python', ['-B', '-X', 'utf8', '-c', rule, path.join(HERE, 'scene'), path.join(home, 'register-tree'), 'den-v9', 'den'],
      { encoding: 'utf8', windowsHide: true, timeout: 120000 }).trim().split('\n').at(-1));
    assert.deepEqual([wrote.version, revision.version], [theirs[0], theirs[0]]);
    assert.notEqual(theirs[0], 'den-v9', 'control: the rule moves the version');
    assert.equal(nextVersion('den', 'den'), theirs[1]);
  } finally { fs.rmSync(home, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); }
});

// A copy of a prepared set made of hard links: free, and safe because a changed file is replaced, never written through.
function linkedCopy(from, to) {
  fs.mkdirSync(to, { recursive: true });
  for (const entry of fs.readdirSync(from, { withFileTypes: true })) {
    if (entry.isDirectory()) linkedCopy(path.join(from, entry.name), path.join(to, entry.name)); else fs.linkSync(path.join(from, entry.name), path.join(to, entry.name));
  }
}
const replaceFile = (file, text) => { fs.unlinkSync(file); fs.writeFileSync(file, text); };
// A receipt for one prepared take, built with no click: hops 1 to 5 as recorded, and a chain stopped at 6.
function recordedReceipt(setDir, take, prepared = JSON.parse(fs.readFileSync(path.join(setDir, take, 'prepare-receipt.json'), 'utf8'))) {
  const start = { placement: { registered: true, summary: 'a fixture', read: [], wrote: [], tools: {}, stand_in: TABLE.hops[4].stand_ins[4] },
    couch: { n: 14, word: 'SCRIPTED', ran: 'built at page start', tools: [], read: [], wrote: [], stand_ins: [] }, home: null };
  return buildReceipt({ click: 1, takeId: take, setId: prepared.set, setDir, prepared, refs: REFS, start,
    chain: { rows: [], recheck: null, checkRecheck: null, stop: { hop: 6, why: 'a fixture', answer: true } }, brain: null, command: null });
}

test('a receipt hashes hop 1\'s and hop 5\'s recorded reads again, never copying the recorded digest', { skip: NEEDS_SET }, t => {
  const home = scratch('den-sim-rehash-'), copy = path.join(home, path.basename(SET));
  t.after(() => fs.rmSync(home, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }));
  linkedCopy(SET, copy);
  const info = JSON.parse(fs.readFileSync(path.join(SET, 'set.json'), 'utf8')), take = JSON.parse(fs.readFileSync(path.join(SET, 'display.json'), 'utf8')).scenes[0].take;
  const reasonsOn = (receipt, n) => topLine(TABLE, receipt).reasons.filter(reason => reason.hop === n && reason.condition === 2).map(reason => reason.text);
  // As prepared: every read of hops 1 and 5 hashes now to what prepare recorded.
  const clean = recordedReceipt(copy, take);
  for (const n of [1, 5]) {
    assert.ok(clean.rows[n - 1].read.length >= 4, `hop ${n} lists its reads`);
    for (const read of clean.rows[n - 1].read) assert.equal(read.sha256, read.recorded.sha256, `${n}: ${read.path}`);
    assert.deepEqual(reasonsOn(clean, n), []);
  }
  // The set's clicks changed after prepare: hop 5 says so.
  const clicks = path.join(copy, info.fit.folder, `clicks-${info.marks_take.take}.json`);
  replaceFile(clicks, JSON.stringify(JSON.parse(fs.readFileSync(clicks, 'utf8')), null, 2));
  assert.match(reasonsOn(recordedReceipt(copy, take), 5).join('\n'), /clicks-[0-9a-f]+\.json, whose digest isn't the one prepare-receipt\.json/);
  // A receipt whose hop 1 records a shots.json other than the one at the commit that prepared it.
  const prepared = JSON.parse(fs.readFileSync(path.join(SET, take, 'prepare-receipt.json'), 'utf8'));
  prepared.hops['1'].read.find(read => read.path.endsWith('shots.json')).sha256 = '0'.repeat(64);
  assert.match(reasonsOn(recordedReceipt(copy, take, prepared), 1).join('\n'), /shots\.json, whose digest isn't the one/);
  // And the authored room it drew, rebuilt: a recorded room other than the one installPack builds is caught.
  prepared.hops['1'].read.find(read => read.path.startsWith('collision.glb')).sha256 = '1'.repeat(64);
  assert.match(reasonsOn(recordedReceipt(copy, take, prepared), 1).join('\n'), /collision\.glb/);
});

test('a refused re-fit makes hop 5 MISSING with why', { skip: NEEDS_SET }, () => {
  const take = JSON.parse(fs.readFileSync(path.join(SET, 'display.json'), 'utf8')).scenes[0].take;
  const prepared = JSON.parse(fs.readFileSync(path.join(SET, take, 'prepare-receipt.json'), 'utf8'));
  const start = { placement: { registered: false, summary: 'the re-fit on your sofa mark was refused: placement_refused', read: [], wrote: [], tools: {}, stand_in: TABLE.hops[4].stand_ins[4] },
    couch: { n: 14, word: 'SCRIPTED', ran: 'built at page start', tools: [], read: [], wrote: [], stand_ins: [] }, home: null };
  const receipt = buildReceipt({ click: 1, takeId: take, setId: prepared.set, setDir: SET, prepared, refs: REFS, start, chain: { rows: [], stop: { hop: 6, why: 'a fixture', answer: true } }, brain: null, command: null });
  assert.deepEqual([receipt.rows[4].word, receipt.rows[4].note], ['MISSING', start.placement.summary]);
  assert.equal(recordedReceipt(SET, take).rows[4].word, 'ADAPTER', 'control: a registered re-fit keeps hop 5 ADAPTER');
});

test('a receipt holds each recorded tool to its pinned bytes, and the prepare commit to this branch', { skip: NEEDS_SET }, () => {
  const take = JSON.parse(fs.readFileSync(path.join(SET, 'display.json'), 'utf8')).scenes[0].take;
  const fresh = () => JSON.parse(fs.readFileSync(path.join(SET, take, 'prepare-receipt.json'), 'utf8'));
  const toolReasons = prepared => topLine(TABLE, recordedReceipt(SET, take, prepared)).reasons.filter(reason => reason.condition === 4 && reason.hop <= 5).map(reason => reason.text);
  assert.deepEqual(toolReasons(fresh()), [], 'as prepared, every recorded tool is its pinned blob');
  // An archived tool that ran edited, its blob name still the pinned one: the bytes give it away.
  const edited = fresh();
  edited.hops['3'].tool_files['tools/den-take/take.py'].sha256 = '2'.repeat(64);
  assert.match(toolReasons(edited).join('\n'), /^3 Frames: tools\/den-take\/take\.py differs from refs\.json tools\.den-take$/m);
  // So for a harness file prepare ran.
  const harness = fresh();
  harness.harness.files['tools/den-sim/scene/vcam.py'].sha256 = '3'.repeat(64);
  assert.match(toolReasons(harness).join('\n'), /^2 Virtual camera and depth: tools\/den-sim\/scene\/vcam\.py differs from the commit that prepared it/m);
  // A set prepared from a commit that is not on this branch: its scene code was some other branch's.
  const elsewhere = fresh(), other = execFileSync('git', ['-C', ROOT, 'rev-parse', `${REFS.tools['den-core-live']}^{commit}`], { encoding: 'utf8' }).trim();
  assert.throws(() => execFileSync('git', ['-C', ROOT, 'merge-base', '--is-ancestor', other, 'HEAD'], { stdio: 'ignore' }), 'the control commit is not on this branch');
  elsewhere.harness.commit = other;
  assert.match(toolReasons(elsewhere).join('\n'), /^5 Camera registration: the commit that prepared it \([0-9a-f]{8}\) is not on this branch: HEAD does not descend from it$/m);
});

test('the picture note says what the drawn row holds: rows that failed, no person, box only, box and joints', () => {
  const frame = 'The take\'s own frame, read back out of its NV12 plane';
  const person = keypoints => ({ box_xyxy: [10, 20, 110, 300], score: .91, keypoints });
  assert.equal(pictureNote({ boxes: null }), `${frame}. The detector's recorded rows did not pass hop 6's checks, so no box is drawn.`);
  assert.equal(pictureNote({ boxes: [] }), `${frame}. The detector's row for this frame holds no person, so no box is drawn.`);
  const boxOnly = `${frame}, with YOLO26s's person box and score (person 0.91) from this frame's recorded row: box only, RTMPose-m placed no supported joint on it.`;
  assert.equal(pictureNote({ boxes: [person(Array(17).fill(null))] }), boxOnly, 'every joint null');
  assert.equal(pictureNote({ boxes: [person(undefined)] }), boxOnly, 'no keypoints at all');
  assert.equal(pictureNote({ boxes: [person(Array.from({ length: 17 }, (_, j) => [100 + j, 50 + j, .39]))] }), boxOnly, 'every joint under 0.4: none is drawn');
  const one = Array.from({ length: 17 }, (_, j) => [100 + j, 50 + j, j === 5 ? .4 : .2]);
  assert.equal(pictureNote({ boxes: [person(one)] }), `${frame}, with YOLO26s's person box and score (person 0.91) and RTMPose-m's supported joints from this frame's recorded row.`,
    'control: one joint at 0.4 is drawn, so the note names the joints');
  assert.equal(pictureNote({ error: 'no frame' }), null);
});

test('the hop table: 26 hops, each with its pin, and adapters that name their stand-ins', () => {
  assert.deepEqual(TABLE.hops.map(hop => hop.n), Array.from({ length: 26 }, (_, i) => i + 1));
  for (const hop of TABLE.hops) {
    assert.ok(['REAL', 'ADAPTER', 'SCRIPTED'].includes(hop.word), `${hop.n} finishes REAL, ADAPTER or SCRIPTED`);
    assert.equal(hop.adapter !== null, hop.word === 'ADAPTER', `${hop.n} has an adapter kind exactly when it is an adapter`);
    assert.equal(hop.stand_ins.length > 0, hop.word !== 'REAL', `${hop.n}: REAL names no stand-in, the others name theirs`);
    assert.match(hop.tool.pin, /^(HEAD|refs\.json (tools\.[\w/.-]+|base|codec\.sha256))$/, `${hop.n}'s pin says where it lives`);
    if (hop.tool.pin.startsWith('refs.json tools.')) assert.ok(REFS.tools[hop.tool.pin.slice(16)], `${hop.n}'s pin is in refs.json`);
    assert.ok(['recorded', 'page', 'click'].includes(hop.when));
  }
  assert.deepEqual(Object.keys(REFS.codec.sha256).sort(), ['contracts/motor-protocol-allowlist-v1.json', 'contracts/runtime-plan-v1.json', 'tools/c12_runtime_plan.py', 'tools/c12_runtime_wire.py']);
  // The plan's recorded inputs (condition 2), each in the table for every hop the plan names it for: the
  // table holds the finished state, so a later step meets no false condition 2 reason.
  const PLAN = [[['shots.json', 'render'], [1, 2, 3]], [['take', 'check'], [3, 4, 6]], [['marks take', 'clicks', 'calibration', 'facing.json'], [5]],
    [['room model'], [5, 7, 14, 15, 16]], [['rows'], [6]], [['method copy'], [9]], [['replies'], [12]], [['your mark', 'your export'], [14]],
    [['light model'], [16, 17, 26]], [['fixture table'], [16, 18, 21]]];
  const short = PLAN.flatMap(([kinds, hops]) => hops.flatMap(n => kinds.filter(kind => !TABLE.hops[n - 1].recorded_inputs.includes(kind)).map(kind => `${n}: ${kind}`)));
  assert.deepEqual(short, []);
});

/* ---------- test 2 and test 19: searches over code ---------- */

const read = name => fs.readFileSync(path.join(HERE, name), 'utf8');
const linesMatching = (files, pattern) => files.flatMap(([name, text]) => text.split('\n').flatMap((line, i) => pattern.test(line) ? [`${name}:${i + 1}`] : []));

test('test 2: click-path code names no scene, and one line alone opens the tries file', () => {
  const code = CLICK_PATH.map(name => [name, read(name)]);
  const named = /scenario|reading_on_couch|not_reading/;
  assert.deepEqual(linesMatching(code, named), []);
  assert.equal(linesMatching([...code, ['planted.py', "x = 1\nscenario = 'planted'\n"]], named).length, 1, 'control: a planted scenario line is found');
  const tries = /tries\.jsonl/;
  const opens = linesMatching(code, tries);
  assert.equal(opens.length, 1, `one line: ${opens}`);
  assert.match(opens[0], /^run\.mjs:/);
  assert.equal(linesMatching([...code, ['planted.mjs', "fs.readFileSync('tries.jsonl')\n"]], tries).length, 2, 'control: a second opener is found');
});

test('test 19: a file this branch adds holds the site id only through the one constant', () => {
  const added = execFileSync('git', ['-C', ROOT, 'diff', '--name-only', '--diff-filter=A', 'ad58fdb9', '--'], { encoding: 'utf8' }).split('\n').filter(Boolean)
    .concat(execFileSync('git', ['-C', ROOT, 'ls-files', '--others', '--exclude-standard', '--', 'tools/den-sim'], { encoding: 'utf8' }).split('\n').filter(Boolean));
  assert.ok(added.includes('tools/den-sim/hops.json') && added.includes('tools/den-sim/top_line.mjs'), added.join(' '));
  const holds = files => files.filter(([, text]) => text.toLowerCase().includes(SITE.toLowerCase())).map(([name]) => name);
  const texts = added.filter(name => fs.existsSync(path.join(ROOT, name))).map(name => [name, fs.readFileSync(path.join(ROOT, name), 'utf8')]);
  assert.deepEqual(holds(texts), []);
  assert.deepEqual(holds([...texts, ['tools/den-sim/planted.mjs', `const home = '${SITE[0].toUpperCase()}${SITE.slice(1)}';\n`]]), ['tools/den-sim/planted.mjs'], 'control: a planted new file is found');
});

test('test 19: every Python process a click starts refuses sockets, name lookups included', () => {
  // The events prepare.py refuses: every Python process here refuses the same list.
  const events = text => (text.match(/^NETWORK = \(([^)]*)\)/m)?.[1].match(/'[^']+'|"[^"]+"/g) || []).map(event => event.slice(1, -1)).sort();
  const want = events(read('scene/prepare.py'));
  assert.ok(want.includes('socket.gethostbyname') && want.includes('socket.getnameinfo') && want.length === 8, want.join(' '));
  for (const name of ['chain.py', 'brain_run.py', 'dry_run.py']) {
    const text = read(name), hook = text.indexOf('sys.addaudithook(refuse_network)');
    assert.ok(hook > 0 && /if event in NETWORK:/.test(text), `${name} installs the refusal`);
    assert.deepEqual(events(text), want, `${name} refuses what prepare.py refuses`);
    const firstOwnImport = text.search(/^from (activity_loop|fixture_execution)|^import frame_hash/m);
    assert.ok(firstOwnImport === -1 || hook < firstOwnImport, `${name} refuses sockets before it imports anything of Home's`);
  }
  assert.notDeepEqual(events("NETWORK = ('socket.connect', 'socket.bind', 'socket.sendto', 'socket.getaddrinfo')\n"), want, 'control: the old, shorter list is told apart');
  const tmp = scratch('den-sim-socket-');
  try {
    // Lookups of the loopback address open no socket, and can still send DNS: the refusal stops each one.
    const probe = path.join(tmp, 'tries-a-lookup.py');
    fs.writeFileSync(probe, "import socket\nfor name, call in (('getaddrinfo', lambda: socket.getaddrinfo('127.0.0.1', 9)), ('gethostbyname', lambda: socket.gethostbyname('127.0.0.1')),\n"
      + "                   ('gethostbyaddr', lambda: socket.gethostbyaddr('127.0.0.1')), ('getnameinfo', lambda: socket.getnameinfo(('127.0.0.1', 9), 0))):\n"
      + "    try:\n        call()\n        print(name, 'NOT REFUSED')\n    except RuntimeError as error:\n        print(name, 'refused')\n");
    // The copy below sits outside the checkout, so it finds tools/den-detect through PYTHONPATH.
    const guarded = file => execFileSync('python', ['-B', file, '--guarded', probe], { encoding: 'utf8', windowsHide: true,
      env: { ...process.env, PYTHONPATH: path.join(ROOT, 'tools', 'den-detect') } }).trim().split(/\r?\n/);
    assert.deepEqual(guarded(path.join(HERE, 'chain.py')), ['getaddrinfo refused', 'gethostbyname refused', 'gethostbyaddr refused', 'getnameinfo refused']);
    // Control: chain.py with its refusal taken out lets the same lookups through.
    fs.writeFileSync(path.join(tmp, 'chain.py'), read('chain.py').replace('sys.addaudithook(refuse_network)', 'pass'));
    fs.copyFileSync(path.join(HERE, 'hops.json'), path.join(tmp, 'hops.json'));
    assert.ok(guarded(path.join(tmp, 'chain.py')).every(line => line.endsWith('NOT REFUSED')));
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
});

/* ---------- the harness, through its own routes ---------- */

function harness(args, env) {
  if (!env.DEN_SIM_HOME || !env.LOCALAPPDATA) throw Error('the harness tests need DEN_SIM_HOME and LOCALAPPDATA set to scratch folders');
  const child = spawn(process.execPath, [path.join(HERE, 'run.mjs'), ...args], { env, windowsHide: true });
  let output = '', errors = '';
  child.stdout.on('data', chunk => { output += chunk; });
  child.stderr.on('data', chunk => { errors += chunk; });
  const stopped = new Promise(done => child.once('exit', done));
  const ready = (async () => {
    for (const end = Date.now() + 120000; Date.now() < end; await sleep(200)) {
      if (output.includes('Ctrl+C')) return;
      if (child.exitCode !== null) throw Error('the harness stopped: ' + output + errors);
    }
    throw Error('the harness did not start: ' + output + errors);
  })();
  return { ready, output: () => output, errors: () => errors, stop: () => { child.kill(); return stopped; } };
}
const status = async () => (await fetch(ORIGIN + '/__sim/status')).json();
async function click(take) {
  const reply = await (await fetch(ORIGIN + '/__sim/feed', { method: 'POST', headers: { 'Content-Type': 'application/json', Origin: ORIGIN }, body: JSON.stringify({ take, fixtures: [] }) })).json();
  assert.ok(reply.ok, JSON.stringify(reply));
  for (const end = Date.now() + 90000; Date.now() < end; await sleep(200)) {
    const now = await status();
    if (now.click) return now;
  }
  throw Error('the click did not finish');
}
const TEXT = /\.(json|jsonl|csv|md|log|txt)$/;
// Every place a needle or a key named scene or scenario appears, in text files under a folder.
function search(entries, needles, keys = ['scene', 'scenario']) {
  const hits = [];
  const walk = (value, label) => {
    if (Array.isArray(value)) value.forEach(item => walk(item, label));
    else if (value && typeof value === 'object') for (const [key, item] of Object.entries(value)) { if (keys.includes(key)) hits.push(`${label}: key ${key}`); walk(item, label); }
  };
  for (const [label, text] of entries) {
    for (const needle of needles) if (text.includes(needle)) hits.push(`${label}: ${needle}`);
    for (const line of label.endsWith('.jsonl') ? text.split('\n').filter(Boolean) : [text]) { try { walk(JSON.parse(line), label); } catch { /* not JSON */ } }
  }
  return hits;
}
function filesUnder(folder) {
  const out = [];
  (function walk(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const file = path.join(directory, entry.name);
      if (entry.isDirectory()) walk(file);
      else if (TEXT.test(entry.name)) out.push([path.relative(folder, file), fs.readFileSync(file, 'utf8')]);
      else out.push([path.relative(folder, file) + ' (name)', '']);
    }
  })(folder);
  return out.map(([label, text]) => [label, label.endsWith('(name)') ? label : text]);
}
const scratchEnv = home => ({ ...process.env, DEN_SIM_HOME: home, LOCALAPPDATA: home });

test('the real chain on every prepared take with no reply on file: step 4\'s words to hop 12, not yet, and nothing told the scene', { skip: NEEDS_SET, timeout: 400000 }, async t => {
  const home = scratch('den-sim-click-');
  // An owed list filed for another set: the page shown here must not count it, nor ask for a paste of its bodies.
  const owedList = path.join(home, 'prepared', 'owed', 'owed.json'), other = 'aaaaaaaaaaaa', bodies = ['1', '2', '3'].map(c => c.repeat(64));
  fs.mkdirSync(path.dirname(owedList), { recursive: true });
  fs.writeFileSync(owedList, JSON.stringify({ schema: 'den-sim-owed-1', set: other, bodies }));
  const run = harness(['--room', 'authored', '--prepared', SET], scratchEnv(home));
  t.after(async () => { await run.stop(); fs.rmSync(home, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); });
  await run.ready;
  const display = JSON.parse(fs.readFileSync(path.join(SET, 'display.json'), 'utf8')).scenes;
  const first = await status();
  assert.deepEqual([first.qwen.owed, first.qwen.owed_set, first.qwen.words], [null, other, 'no Qwen reply on file yet']);
  assert.ok(run.output().includes(`The owed list on file is for set ${other}, not this one: an agent runs prepare.py --owed for set ${first.set.id}.`), run.output());
  assert.ok(!run.output().includes('Qwen still owes'), 'no paste is asked for another set\'s bodies');
  assert.ok(run.output().includes('On file now: no Qwen reply on file yet.'), 'the startup line says no reply is on file');
  fs.writeFileSync(owedList, JSON.stringify({ schema: 'den-sim-owed-1', set: first.set.id, bodies }));
  assert.equal((await status()).qwen.owed, 3, 'control: the same list filed for this set is counted');
  fs.rmSync(owedList);
  assert.deepEqual(first.scenes, display.map(({ take, label }) => ({ take, label })), 'the buttons carry labels and take ids, never the words');
  for (const key of ['seat_m', 'point']) assert.ok(!JSON.stringify({ couch: first.couch, brain: first.brain }).includes(`"${key}"`), `${key} left the startup record`);
  // The stand-in seat is a square, which has no single longest edge, so its facing is a tie-break, and the page says so.
  assert.match(first.placement.summary, /; the reader faces a tie-break, not the stated rule: no single edge of the seat's zone is longest/);
  const clicks = {};
  for (const scene of display) {
    const done = await click(scene.take);
    clicks[scene.label] = { view: done.click, receipt: await (await fetch(ORIGIN + '/__sim/receipt')).json(), status: done };
  }
  // Test 7 on the real chain: not yet, with its reasons listed.
  for (const [label, { view }] of Object.entries(clicks)) {
    assert.equal(view.top_line.line, 'Connected start to finish: not yet', label);
    assert.ok(view.top_line.reasons.length > 0, label);
  }
  const reading = clicks['Reading on the couch'];
  assert.deepEqual(Object.fromEntries(reading.view.rows.filter(row => row.reached).map(row => [row.n, row.word])), Object.fromEntries(Object.entries(STEP4_UNASKED).map(([n, word]) => [Number(n), word])));
  for (const { view } of Object.values(clicks)) assert.deepEqual(view.rows.map(row => row.name), TABLE.hops.map(hop => hop.name), 'every row is named as the table names it');
  assert.equal(reading.view.top_line.count_line, '2 real, 6 adapter (5 real tools with a named stand-in, 1 written for this simulation), 3 scripted, 1 missing, 14 not reached');
  // Each row under what it did on this click; the rows that did not run sit apart, never under "Ran on this click".
  const grouped = Object.entries(Object.groupBy(reading.view.rows, row => row.group)).map(([group, rows]) => [group, rows.map(row => row.n)]);
  assert.deepEqual(Object.fromEntries(grouped), { recorded: [1, 2, 3, 4, 5, 6], click: [7, 8, 9, 10, 11, 12], 'not run': [13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26] });
  assert.equal(reading.view.rows[10].ran, 'bodies rebuilt now; Qwen not asked about all of them yet', 'hop 11 built the bodies on this click');
  // Reading and phone stop at hop 12: Qwen has not seen these exact frames, need gets no input, and the beam stays off.
  for (const label of ['Reading on the couch', 'Holding a phone']) {
    const { view, status: now } = clicks[label];
    assert.deepEqual([view.stopped.hop, view.stopped.answer, NOT_SEEN.test(view.stopped.why)], [12, false, true], label);
    assert.equal(view.rows[11].word, 'MISSING');
    assert.ok(view.rows.filter(row => row.n > 12).every(row => !row.reached), `${label}: no need input`);
    assert.deepEqual([now.feed.phase, now.feed.summary], ['pipeline_failed', null], `${label}: no brain, no beam`);
    assert.equal(view.qwen.asks.length, 5);
    assert.ok(view.qwen.asks.every(ask => ask.said === 'no reply on file' && /^[0-9a-f]{12}$/.test(ask.body)));
  }
  assert.ok(!reading.view.rows.some(row => /Person to aim|decide v0/.test(row.name)), "#424's two rows have left the list");
  const recorded = JSON.parse(fs.readFileSync(path.join(SET, 'set.json'), 'utf8')).recorded;
  assert.match(reading.view.rows[5].summary, /^YOLO26s found a person on \d+ of \d+ usable frames, and RTMPose-m placed joints on \d+ of them; median \d+ ms a frame on this PC's CPU; rows checked against the model on \S+, on 3 frames, within [0-9.]+ px\. Rows name rtmpose-m and yolo26s\.$/);
  assert.equal(reading.view.rows[5].ran, `recorded ${recorded}, rows checked against the model on ${recorded}`);
  assert.deepEqual(reading.view.rows[5].stand_ins, TABLE.hops[5].stand_ins, 'hop 6 names every stand-in the table names');
  assert.equal(reading.view.rows[2].ran, `recorded ${recorded}, re-checked by hash now`);
  assert.ok(reading.view.top_line.reasons.some(reason => reason.condition === 6), 'MISSING rows are among the reasons');
  assert.deepEqual(reading.view.top_line.reasons.filter(reason => reason.hop === 6), [], 'hop 6 reads ADAPTER with nothing against it: every digest and pin holds');
  assert.ok(!reading.view.top_line.reasons.some(reason => /truth/.test(reason.text)), 'nothing reads the truth folder any more');
  // Every recorded file of the take that a click reads is held to the digest prepare recorded for it.
  const recordedFor = n => JSON.parse(fs.readFileSync(path.join(SET, reading.view.take, 'prepare-receipt.json'), 'utf8')).hops[String(n)].wrote;
  const wroteDigest = Object.fromEntries([...recordedFor(3), ...recordedFor(4)].map(file => [file.path, file.sha256]));
  const rowOf = n => reading.receipt.rows[n - 1], take = reading.view.take;
  assert.deepEqual(rowOf(3).read.map(file => [file.path, file.sha256, file.recorded.sha256]),
    [`${take}/frames.csv`, `${take}/take.json`].map(name => [name, wroteDigest[name], wroteDigest[name]]), 'hop 3 re-checks frames.csv and take.json');
  assert.equal(rowOf(4).ran, `${rowOf(1).ran.split(',')[0]}, re-checked by hash now`);
  assert.deepEqual(rowOf(4).recheck.checked.map(file => [file.path, file.sha256]), [[`${take}/check-1.json`, wroteDigest[`${take}/check-1.json`]]]);
  const sixReads = rowOf(6).read.map(file => [file.path, file.recorded.kind, file.sha256 === file.recorded.sha256]);
  assert.deepEqual(sixReads, [[`${take}/frames.csv`, 'take', true], [`${take}/check-1.json`, 'check', true], [`${take}/detections.jsonl`, 'rows', true], [`${take}/detector-run.json`, 'rows', true]]);
  const sevenReads = rowOf(7).read.filter(file => file.recorded).map(file => [file.path, file.recorded.kind, file.sha256 === file.recorded.sha256]);
  assert.deepEqual(sevenReads.find(([file]) => file === `${take}/detections.jsonl`), [`${take}/detections.jsonl`, 'rows', true], 'hop 7 reads the recorded rows');
  for (const name of ['frames.csv', 'take.json']) assert.deepEqual(sevenReads.find(([file]) => file === `${take}/${name}`), [`${take}/${name}`, 'take', true], `hop 7 lists ${name}`);
  assert.deepEqual(sevenReads.find(([file]) => file === `${take}/check-1.json`), [`${take}/check-1.json`, 'check', true], 'hop 7 lists the check replay read');
  assert.ok(!reading.view.top_line.reasons.some(reason => reason.hop === 7), 'and hop 7 reads only what it may');
  // Hop 11 reads the take, replay's run.json and the router's two files; hop 12 the requests, and no reply, none being on file.
  assert.deepEqual(rowOf(11).read.map(file => file.path), [`${take}/frames.csv`, `${take}/take.json`, 'observe/run.json', 'requests.jsonl', 'bundles.jsonl']);
  assert.deepEqual(rowOf(12).read.map(file => file.path), ['requests.jsonl']);
  assert.deepEqual(rowOf(12).owed.length, 5);
  // Every reason the reading click gives at step 4 with no reply on file, as (condition, hop): each one true
  // now, and a new or a lost one shows. Hop 5: the stand-in seat and sockets; 10: occupancy still typed; 12: the miss.
  const want = ['2:5', '2:5', '3:5', '3:10', '3:12', '6:12', 'null:12'];
  assert.deepEqual(reading.view.top_line.reasons.map(reason => `${reason.condition}:${reason.hop}`).sort(), want.sort());
  assert.equal(reading.receipt.aim_check, null, 'no aim check is claimed');
  assert.ok(rowOf(1).read.every(file => ['shots.json', 'render'].includes(file.recorded.kind)), 'hop 1 reads only shots.json and the render\'s inputs');
  assert.deepEqual(rowOf(4).read.map(file => file.path), [`${take}/frames.csv`], 'hop 4 read the take');
  // Nobody and blank light nothing and stop at hop 6, where the real detector's rows hold no person, with no
  // box, no person card and no beam. Reading's frame has the model's box and joints, though no reply is on file.
  assert.ok(reading.view.boxes.length >= 1 && reading.view.boxes.every(box => box.score >= .5 && box.keypoints.length === 17), 'reading\'s frame has the model\'s box and joints');
  assert.equal(reading.view.picture_note, `The take's own frame, read back out of its NV12 plane, with YOLO26s's person box and score (${reading.view.boxes.map(box => `person ${box.score.toFixed(2)}`).join(', ')}) and RTMPose-m's supported joints from this frame's recorded row.`);
  for (const label of ['Nobody there', 'Blank frame']) {
    const { view, status: now } = clicks[label];
    assert.deepEqual(view.stopped, { hop: 6, why: 'the detector saw nobody, nothing was asked', answer: true }, label);
    assert.match(view.top_line.line, /^(This click stopped at Detector and box: the detector saw nobody, nothing was asked|Connected start to finish: not yet)/, label);
    assert.equal(view.rows[5].word, 'ADAPTER', `${label}: the detector ran and saw nobody, it didn't break`);
    assert.ok(view.rows.filter(row => row.n > 6).every(row => !row.reached), label);
    assert.deepEqual([view.boxes, view.person, now.feed.summary, now.feed.phase], [[], null, null, 'stopped'], `${label}: no box, no person card, no brain, no beam`);
    assert.match(view.picture_note, /The detector's row for this frame holds no person, so no box is drawn\.$/, label);
  }
  // Standing, reported: what the click did and why.
  const standing = clicks['Standing by the couch with a book'];
  t.diagnostic(`standing: ${standing.view.stopped ? `stopped at hop ${standing.view.stopped.hop}: ${standing.view.stopped.why}` : 'reached the brain'}; ${standing.view.rows[5].summary}`
    + `${standing.view.rows[16].reached ? `; ${standing.view.rows[16].summary}` : ''}`);
  // With no reply on file no click reaches hop 15, so no person card, in the panel, the console or run.json.
  const runJson = JSON.parse(fs.readFileSync(path.join(first.run, 'run.json'), 'utf8'));
  for (const [label, { view }] of Object.entries(clicks)) assert.deepEqual([view.person, view.rows[14].reached], [null, false], label);
  assert.deepEqual([runJson.clicks, /: person: /.test(run.output())], [[], false]);
  // The click's room model is the copy of the re-fit's that hop 14 wrote the couch into at page start, its digest held.
  assert.deepEqual(rowOf(7).read.filter(file => file.recorded?.kind === 'room model').map(file => [file.path.replace(/fits\/[0-9a-f]+\//, 'fits/<fit>/'), file.recorded.by, file.sha256 === file.recorded.sha256]),
    [['fits/<fit>/couch/room-model.jsonl', 'couch_to_room.mjs at page start', true]]);
  // Test 6 on the real clicks: replay ran on the recorded rows (--detections, never --fake), and every
  // observation names a real model. Test 1: the clicks' command lines differ only by folder ids.
  const clickDirs = fs.readdirSync(first.run).filter(name => /^click-\d+$/.test(name)).map(name => path.join(first.run, name));
  const argvs = clickDirs.filter(dir => fs.existsSync(path.join(dir, 'argv.jsonl'))).map(dir => fs.readFileSync(path.join(dir, 'argv.jsonl'), 'utf8'));
  assert.ok(argvs.length >= 2, 'reading and phone ran the tools');
  for (const text of argvs) {
    assert.match(text, /"--detections", "[^"]+\\\\detections\.jsonl"/);
    assert.doesNotMatch(text, /--fake/);
  }
  const shape = text => text.replace(/[0-9a-f]{12}/g, '<take>').replace(/click-\d+/g, 'click-N');
  assert.deepEqual(new Set(argvs.map(shape)).size, 1, 'one command line, differing only by folder id');
  assert.notEqual(shape(argvs[0]), shape(argvs[0].replace('--detections', '--fake')), 'control: another flag shows');
  const models = new Set(clickDirs.flatMap(dir => {
    const file = path.join(dir, 'observe', 'observations.jsonl');
    return fs.existsSync(file) ? fs.readFileSync(file, 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line).source.model) : [];
  }));
  assert.deepEqual([...models].sort(), ['rtmpose-m', 'yolo26s'].filter(name => models.has(name)), [...models].join(' '));
  assert.ok(models.has('rtmpose-m'));
  t.diagnostic(`observations' source.model: ${[...models].sort().join(', ')}`);
  const nobody = clicks['Nobody there'].view;
  // Test 2: the words and the exact display strings are nowhere a click wrote, nor in what the harness served.
  const needles = [...display.map(scene => scene.word), ...display.map(scene => scene.label)];
  const runFolder = first.run;
  const written = filesUnder(runFolder).filter(([label]) => !label.startsWith('packs'));
  assert.ok(written.some(([label]) => /click-\d+[\\/]argv\.jsonl$/.test(label)) && written.some(([label]) => /run-receipt\.json$/.test(label)));
  const served = [['status', JSON.stringify({ ...(await status()), scenes: '(the display file, for the buttons)' })],
    ...Object.entries(clicks).flatMap(([label, value]) => [[`receipt ${value.view.take}`, JSON.stringify(value.receipt)], [`status ${value.view.take}`, JSON.stringify({ ...value.status, scenes: null })]]),
    ['brain state', await (await fetch(ORIGIN + Client.BASE + '/state')).text()]];
  assert.deepEqual(search([...written, ...served], needles), []);
  const planted = written.find(([label]) => label.endsWith('run-receipt.json'));
  const hits = search([[planted[0], planted[1].replace('"take"', `"note": "${display[2].word}", "scenario": 1, "take"`)]], needles);
  assert.ok(hits.some(hit => hit.includes(display[2].word)) && hits.some(hit => hit.includes('key scenario')), 'control: a planted word and key are found');
});

test('test 5 on a click: a frame changed in a copy of a take reads MISSING at hop 3, with no need input and no beam', { skip: NEEDS_SET, timeout: 300000 }, async t => {
  const home = scratch('den-sim-hash-'), copy = path.join(home, 'prepared', path.basename(SET));
  t.after(async () => { fs.rmSync(home, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); });
  // A copy made of hard links: free, and safe because the one changed file is replaced, never written through.
  (function link(from, to) {
    fs.mkdirSync(to, { recursive: true });
    for (const entry of fs.readdirSync(from, { withFileTypes: true })) {
      if (entry.isDirectory()) link(path.join(from, entry.name), path.join(to, entry.name)); else fs.linkSync(path.join(from, entry.name), path.join(to, entry.name));
    }
  })(SET, copy);
  const display = JSON.parse(fs.readFileSync(path.join(SET, 'display.json'), 'utf8')).scenes;
  const take = display.find(scene => scene.label === 'Reading on the couch').take, other = display.find(scene => scene.label.startsWith('Standing')).take;
  const rows = folder => fs.readFileSync(path.join(folder, 'frames.csv'), 'utf8').trim().split('\n').slice(1).map(line => line.split(','));
  const victim = path.join(copy, take, rows(path.join(copy, take))[200][1]), source = path.join(SET, other, rows(path.join(SET, other))[200][1]);
  const original = fs.readFileSync(victim);
  fs.unlinkSync(victim);
  fs.writeFileSync(victim, fs.readFileSync(source));   // another scene's frame, depth and all, under this take's name
  assert.deepEqual(fs.readFileSync(path.join(SET, take, rows(path.join(SET, take))[200][1])), original, 'the real take is untouched');
  const run = harness(['--room', 'authored', '--prepared', copy], scratchEnv(home));
  t.after(() => run.stop());
  await run.ready;
  const done = await click(take);
  const rowsByN = Object.fromEntries(done.click.rows.map(row => [row.n, row]));
  assert.equal(rowsByN[3].word, 'MISSING');
  assert.match(rowsByN[3].summary, /changed or is gone since it was recorded/);
  assert.equal(done.click.stopped.hop, 3);
  assert.equal(rowsByN[13].reached, false, 'no need input');
  assert.equal(done.feed.phase, 'pipeline_failed', 'no beam');
  assert.equal(done.click.top_line.line, 'Connected start to finish: not yet');
  // Control: the untouched take in the same copy passes the hash and reaches the reply lookup.
  const phone = display.find(scene => scene.label === 'Holding a phone').take;
  const control = await click(phone);
  assert.equal(control.click.rows[2].word, 'ADAPTER');
  assert.equal(control.click.rows[11].reached, true);
  // A break is filed under the hop that broke, which reads MISSING; the rows before it stay as they ran.
  const blank = display.find(scene => scene.label === 'Blank frame').take, check = path.join(copy, blank, 'check-1.json');
  const checkBytes = fs.readFileSync(check, 'utf8');
  fs.unlinkSync(check); fs.writeFileSync(check, checkBytes.replace('{', '{ '));   // one space: the same report, other bytes
  const four = await click(blank);
  assert.deepEqual(four.click.stopped, { hop: 4, why: 'check-1.json is not the one prepare recorded', answer: false });
  assert.deepEqual(four.click.rows.slice(0, 4).map(row => [row.word, row.reached]), [['SCRIPTED', true], ['SCRIPTED', true], ['ADAPTER', true], ['MISSING', true]]);
  assert.match(four.click.top_line.count_line, /, 1 missing, 22 not reached$/);
  assert.ok(four.click.top_line.reasons.some(reason => reason.condition === 6 && reason.hop === 4));
  // replay reads take.json and the NEWEST check-N.json too. A second check beside the recorded one (a
  // second run of check.py writes one) would re-clock every observation: the click stops at hop 4.
  const standing = display.find(scene => scene.label.startsWith('Standing')).take, report = JSON.parse(fs.readFileSync(path.join(copy, standing, 'check-1.json'), 'utf8'));
  report.reported_only.offset_c_mod_period_s[0] += 0.5;
  fs.writeFileSync(path.join(copy, standing, 'check-2.json'), JSON.stringify(report, null, 1));
  const second = await click(standing);
  assert.deepEqual(second.click.stopped, { hop: 4, why: 'check-2.json beside the check-1.json prepare recorded, and replay reads the newest check', answer: false });
  assert.deepEqual([second.click.rows[3].word, second.click.rows[6].reached], ['MISSING', false]);
  // A take.json other than the one prepare recorded, its frames.csv digest left alone, stops at hop 3.
  const nobody = display.find(scene => scene.label === 'Nobody there').take, record = path.join(copy, nobody, 'take.json');
  const doc = JSON.parse(fs.readFileSync(record, 'utf8'));
  doc.ended_unix -= 86400;
  fs.unlinkSync(record); fs.writeFileSync(record, JSON.stringify(doc, null, 1));
  const three = await click(nobody);
  assert.deepEqual(three.click.stopped, { hop: 3, why: 'take.json is not the one prepare recorded', answer: false });
  assert.deepEqual([three.click.rows[2].word, three.click.rows[5].reached], ['MISSING', false]);
  // An archived tool edited after its archive was written, here in this test's own data root: its
  // marker file still vouches for the tree, and the click names the file all the same.
  const observeTree = fs.readdirSync(path.join(home, 'tools')).find(name => name.startsWith('observe-'));
  const replay = path.join(home, 'tools', observeTree, 'tools', 'den-observe', 'replay.py');
  fs.appendFileSync(replay, '\n# edited after the archive was written\n');
  const archived = await click(phone);
  assert.ok(archived.click.top_line.reasons.some(reason => reason.condition === 4 && reason.hop === 7 && /tools\/den-observe\/replay\.py differs from refs\.json tools\.rung-2-observe/.test(reason.text)),
    JSON.stringify(archived.click.top_line.reasons.filter(reason => reason.condition === 4)));
  // A tool that fails on a click: replay, on the click's room model (the couch's copy) broken after the page placed it.
  const fits = path.join(home, 'fits'), fit = fs.readdirSync(fits).find(name => fs.existsSync(path.join(fits, name, 'couch', 'room-model.jsonl')));
  fs.writeFileSync(path.join(fits, fit, 'couch', 'room-model.jsonl'), '{"not": "a room model"}\n');
  const seven = await click(phone);
  assert.equal(seven.click.stopped.hop, 7, JSON.stringify(seven.click.stopped));
  assert.match(seven.click.stopped.why, /^replay\.py exit \d+: /);
  const byN = Object.fromEntries(seven.click.rows.map(row => [row.n, row]));
  assert.deepEqual([byN[7].word, byN[7].reached], ['MISSING', true]);
  assert.ok([1, 2, 3, 4, 5, 6].every(n => byN[n].reached), 'the rows up to the break ran');
  assert.equal(byN[6].word, 'ADAPTER');
  assert.ok([8, 9, 13, 17].every(n => !byN[n].reached));
  assert.equal(seven.feed.phase, 'pipeline_failed');
  // Test 6 on a click: one byte added to the recorded rows. Hop 6 reads MISSING, never nobody, and the chain stops.
  const rowsFile = path.join(copy, phone, 'detections.jsonl'), rowsText = fs.readFileSync(rowsFile, 'utf8');
  replaceFile(rowsFile, rowsText.replace('\n', ' \n'));
  const edited = await click(phone);
  assert.deepEqual(edited.click.stopped, { hop: 6, why: 'detections.jsonl is not the one prepare recorded', answer: false });
  assert.deepEqual([edited.click.rows[5].word, edited.click.rows[6].reached, edited.click.boxes], ['MISSING', false, null]);
  assert.equal(edited.click.picture_note, 'The take\'s own frame, read back out of its NV12 plane. The detector\'s recorded rows did not pass hop 6\'s checks, so no box is drawn.');
  assert.doesNotMatch(edited.click.picture_note, /holds no person/, 'the phone take\'s frame holds a person: rows that fail their checks never say otherwise');
  assert.equal(edited.click.top_line.line, 'Connected start to finish: not yet');
  assert.ok(edited.click.top_line.reasons.some(reason => reason.condition === 6 && reason.hop === 6));
  // The weights are untouched, so no condition 4 reason at hop 6 says they changed: the rows file's digest (2), the
  // word (3), MISSING (6) and the break are its reasons.
  const atSix = edited.click.top_line.reasons.filter(reason => reason.hop === 6);
  assert.deepEqual(atSix.map(reason => reason.condition), [2, 3, 6, null], JSON.stringify(atSix));
  assert.ok(!edited.click.top_line.reasons.some(reason => /weights .* differs/.test(reason.text)), 'no weight reads as changed');
  const sixTools = (await (await fetch(ORIGIN + '/__sim/receipt')).json()).rows[5].tools.filter(tool => tool.path.startsWith('weights '));
  assert.deepEqual(sixTools.map(tool => [tool.path, tool.actual === tool.expected]), [['weights yolo26s.pt', true], ['weights rtmpose-m-body7.onnx', true], ['weights artifact.json', true]]);
  assert.equal(edited.feed.phase, 'pipeline_failed');
});

test('a set prepare refused still opens, and its refused take says where prepare stopped', { skip: NEEDS_SET, timeout: 300000 }, async t => {
  const home = scratch('den-sim-refused-'), copy = path.join(home, 'prepared', path.basename(SET));
  t.after(() => fs.rmSync(home, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }));
  linkedCopy(SET, copy);
  const display = JSON.parse(fs.readFileSync(path.join(SET, 'display.json'), 'utf8')).scenes;
  const phone = display.find(scene => scene.label === 'Holding a phone').take, refusal = { hop: 4, reason: 'check.py: FAIL, retained False, 0 usable segments' };
  const edit = (file, change) => { const doc = JSON.parse(fs.readFileSync(file, 'utf8')); change(doc); replaceFile(file, JSON.stringify(doc, null, 1)); };
  edit(path.join(copy, 'set.json'), doc => { doc.refusal = refusal; });
  edit(path.join(copy, phone, 'prepare-receipt.json'), doc => { doc.refusal = refusal; });
  const run = harness(['--room', 'authored', '--prepared', copy], scratchEnv(home));
  try {
    await run.ready;
    assert.match(run.output(), /^Note: prepare stopped at hop 4 on this set: check\.py: FAIL, retained False, 0 usable segments$/m);
    assert.deepEqual((await status()).set.refusal, refusal);
    const stopped = await click(phone);
    assert.equal(stopped.click.top_line.line, 'Prepare stopped at hop 4: check.py: FAIL, retained False, 0 usable segments');
    assert.equal(stopped.click.stopped.hop, 4);
    assert.deepEqual([stopped.click.rows[3].word, stopped.click.rows[6].reached], ['MISSING', false]);
    assert.ok(!fs.existsSync(path.join(stopped.run, `click-${stopped.click.click}`, 'argv.jsonl')), 'nothing ran on the refused take');
    // Control: a take prepare finished, in the same set, runs as ever.
    const reading = await click(display.find(scene => scene.label === 'Reading on the couch').take);
    assert.equal(reading.click.top_line.line, 'Connected start to finish: not yet');
    assert.equal(reading.click.rows[11].reached, true);
  } finally { await run.stop(); }
  // A set with no display file opens with its takes' folder ids, and says so: only the panel reads that file.
  fs.unlinkSync(path.join(copy, 'display.json'));
  const bare = harness(['--room', 'authored', '--prepared', copy], scratchEnv(home));
  try {
    await bare.ready;
    assert.match(bare.output(), /^Note: set \S+ has no display file, so its buttons show folder ids$/m);
    const scenes = (await status()).scenes;
    assert.deepEqual(scenes.map(scene => scene.label).sort(), display.map(scene => `take ${scene.take}`).sort());
  } finally { await bare.stop(); }
});

/* ---------- tests 9, 12 and 19 on a sealed invented export and mark, in your real scan's frame ---------- */

function invent(privateFolder, corners) {
  const binding = readActiveBinding(path.resolve(REFS.content_packs));
  const doc = Core.sealDocument({ ...Core.normalizeDocument(authoredDocument(binding, Core)), revision: 3 });
  assert.ok(Core.validateDocument(doc, { binding }).ok, 'the invented export validates on the active pack');
  const mark = Sofa.sealSofaTarget({ binding: doc.binding, equipment_ref: { revision: doc.revision, digest: doc.digest },
    corners: corners.map(point_m => ({ point_m, normal_unit: [0, 0, 1] })), confirmed: true }, { sealDesk: sealRegisteredDeskTarget, digest: Core.digest });
  fs.mkdirSync(privateFolder, { recursive: true });
  fs.writeFileSync(path.join(privateFolder, 'registration-revision-3.json'), JSON.stringify({ ok: true, registration: doc }));
  // Any name: run.mjs finds the mark by its schema.
  fs.writeFileSync(path.join(privateFolder, 'a-couch.json'), JSON.stringify(mark));
  return mark;
}
// A point written the ways a person or a fit would print it, at 3 and 2 decimals.
function coordinatesIn(text, values) {
  return values.flatMap(value => [3, 2].map(places => value.toFixed(places)))
    .filter(token => new RegExp(`(?<![0-9])${token.replace('.', '\\.')}(?![0-9])`).test(text));
}
const panelText = now => [now.startup, ...now.private_notes, now.placement.summary, now.feed.live?.why, now.feed.live?.refusal, ...(now.feed.summary?.candidates || []).map(row => row.reason),
  ...(now.click ? [now.click.top_line.line, now.click.top_line.count_line, ...now.click.top_line.reasons.map(reason => reason.text), now.click.said, ...(now.click.person?.lines || []),
    ...now.click.rows.flatMap(row => [row.ran, row.summary, ...row.stand_ins])] : [])].filter(Boolean).join('\n');

// Python run beside the chain for test 9's controls and test 12's revisions, from files in the test's own scratch folder.
const PATCHED_STILLS = `import json, sys
sys.path.insert(0, sys.argv[1])
import chain
request = json.load(sys.stdin)
pose = json.loads(open(request['room_model'], encoding='utf-8').read().strip().split('\\n')[-1])['cameras'][-1]['pose']
chain.JPEG['comment'] = json.dumps(pose)   # a picture patched to take the placement as an input
print(json.dumps([row['body_sha256'] for row in chain.step_ask(request)['asks']]))
`;
const OUT_TO_STDOUT = `import io, os, runpy, sys
class Tee(io.StringIO):   # the fit's private log, planted back onto stdout
    def write(self, text):
        sys.__stdout__.write(text)
        return super().write(text)
io.StringIO = Tee
sys.argv = sys.argv[1:]
sys.path.insert(0, os.path.dirname(sys.argv[0]))
runpy.run_path(sys.argv[0], run_name='__main__')
`;
const LATER_REVISIONS = `import json, sys
sys.path.insert(0, sys.argv[1])
import prepare
prepare.archive(prepare.Path(sys.argv[2]), prepare.pins())   # den-register and den-take at their pins, on sys.path
import records
latest = records.current(records.read_jsonl(sys.argv[3], 'room_model_unreadable'), 'den')
camera = latest['cameras'][-1]
moved = records.with_camera(latest, dict(camera, version='2099-01-01.1'), {'method': 'probe', 'ts': None, 'confidence': None})
dropped = records.invalidated_revision(moved, camera['camera_id'], {'reason': 'probe'})
print(json.dumps([[s['surface_id'] for s in r['surfaces']] + [m['landmark_id'] for m in r['landmarks']] for r in (moved, dropped, records.new_room_model('den'))]))
`;
// Test 9 on the chain itself, run as a click runs it (brain.mjs's chainRequest), on room models couch_to_room.mjs writes
// with your mark's couch: re-fits on a new facing.json and on a mark 1 m across the couch from yours leave every request
// body's hash unchanged, and the person reads "on the couch" only on the fit placed on your mark. Test 12's other half:
// den-observe loads each of them, and den-register's own rule keeps the surface through later revisions.
async function pictureIndependence(t, { home, privateFolder, fits, corners, readingTake, markNumbers }) {
  const work = path.join(home, 'test9'), exportFile = path.join(privateFolder, 'registration-revision-3.json'), markFile = path.join(privateFolder, 'a-couch.json');
  const registration = JSON.parse(fs.readFileSync(exportFile, 'utf8')).registration, binding = readActiveBinding(path.resolve(REFS.content_packs));
  const floor = path.join(path.resolve(REFS.content_packs), SITE, binding.content_revision, 'floor.json');
  fs.mkdirSync(work, { recursive: true });
  const script = (name, text) => { fs.writeFileSync(path.join(work, name), text); return path.join(work, name); };
  const python = (args, input) => execFileSync('python', ['-B', '-X', 'utf8', ...args], { input, encoding: 'utf8', windowsHide: true, timeout: 300000, maxBuffer: 64 << 20, env: scratchEnv(home) });
  // A sealed mark 1 m across the couch's short side from yours, outside the private folder, and a re-fit placed on it.
  const across = Sofa.sealSofaTarget({ binding: registration.binding, equipment_ref: { revision: registration.revision, digest: registration.digest },
    corners: corners.map(([x, y, z]) => ({ point_m: [x, y + 1, z], normal_unit: [0, 0, 1] })), confirmed: true }, { sealDesk: sealRegisteredDeskTarget, digest: Core.digest });
  fs.writeFileSync(path.join(work, 'across.json'), JSON.stringify(across));
  const refitArgs = folder => [path.join(HERE, 'scene', 'prepare.py'), '--refit', '--set', SET, '--mark', path.join(work, 'across.json'), '--export', exportFile, '--floor', floor, '--fits', folder];
  const said = python(refitArgs(path.join(work, 'fits')));
  const folderIn = folder => fs.readdirSync(folder, { withFileTypes: true }).find(entry => entry.isDirectory()).name;
  const cameraOf = file => JSON.parse(fs.readFileSync(file, 'utf8').trim().split('\n').at(-1)).cameras.at(-1).pose.room_from_camera.slice(0, 3).map(row => row[3]);
  const acrossFile = path.join(work, 'fits', folderIn(path.join(work, 'fits')), 'room-model.jsonl');
  assert.deepEqual(coordinatesIn(said, [...cameraOf(acrossFile), ...markNumbers]), [], 'the re-fit prints no camera and no number of a mark');
  const leaked = python([script('leak.py', OUT_TO_STDOUT), ...refitArgs(path.join(work, 'fits-leaked'))]);
  assert.ok(coordinatesIn(leaked, cameraOf(path.join(work, 'fits-leaked', folderIn(path.join(work, 'fits-leaked')), 'room-model.jsonl'))).length > 0, 'control: out=sys.stdout planted back is found');
  // The three fits: yours, yours turned by facing.json, and the one across. The couch is your mark's in each.
  const yours = fs.readdirSync(fits, { withFileTypes: true }).filter(entry => entry.isDirectory()).map(entry => entry.name)
    .sort((a, b) => (JSON.parse(fs.readFileSync(path.join(fits, a, 'refit-receipt.json'), 'utf8')).inputs.facing ? 1 : 0) - (JSON.parse(fs.readFileSync(path.join(fits, b, 'refit-receipt.json'), 'utf8')).inputs.facing ? 1 : 0));
  const prepared = JSON.parse(fs.readFileSync(path.join(SET, readingTake, 'prepare-receipt.json'), 'utf8'));
  const chainOn = (fitFile, name) => {
    const room = path.join(work, name, 'room-model.jsonl'), wrote = writeCouch({ seatFile: markFile, registration, from: fitFile, to: room, source: 'sofa_mark' });
    const request = chainRequest({ take: readingTake, prepared, setDir: SET, roomModel: { path: room, sha256: wrote.sha256, by: 'couch_to_room.mjs in this test', label: `${name}/room-model.jsonl` },
      refs: REFS, outputRoot: home, work: path.join(work, name, 'click'), replies: path.join(home, 'prepared', 'replies'), lastAsk: path.join(home, 'prepared', 'last-ask.json') });
    const rows = Object.fromEntries(python([path.join(HERE, 'chain.py')], JSON.stringify(request)).split('\n').filter(Boolean).map(line => JSON.parse(line)).filter(row => row.word).map(row => [row.n, row]));
    const bodies = fs.readFileSync(path.join(request.work, 'asks.jsonl'), 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line).body_sha256);
    return { rows, bodies, request, room };
  };
  const filled = JSON.parse(fs.readFileSync(path.join(home, 'test-fill.json'), 'utf8'))[readingTake];
  const runs = { on: chainOn(path.join(fits, yours[0], 'room-model.jsonl'), 'on'), turned: chainOn(path.join(fits, yours[1], 'room-model.jsonl'), 'turned'), across: chainOn(acrossFile, 'across') };
  for (const [name, done] of Object.entries(runs)) {
    assert.deepEqual(done.bodies, filled, `${name}: every body is the one --owed filed on the set's own fit`);
    assert.deepEqual([done.rows[7].word, done.rows[12].word], ['REAL', 'SCRIPTED'], `${name}: den-observe loads the room model, and every reply is found`);
  }
  assert.deepEqual(runs.on.rows[15].person.map(card => card.on_couch), [true], `the fit on your mark: ${runs.on.rows[15].summary}`);
  assert.deepEqual(runs.across.rows[15].person.map(card => [card.on_couch, card.failed.includes('position')]), [[false, true]], `the fit 1 m across: ${runs.across.rows[15].summary}`);
  t.diagnostic(`test 9, on your mark: ${runs.on.rows[15].person.map(chainWords).join('; ')}; across: ${runs.across.rows[15].person.map(chainWords).join('; ')}`);
  // Control: a picture patched to take the placement as an input changes the hashes, and the comparison above fails on it.
  const patched = name => JSON.parse(python([script('patched.py', PATCHED_STILLS), HERE], JSON.stringify({ core_tree: path.join(home, 'tools', `core-${REFS.tools['den-core-live']}`), take: runs[name].request.take,
    take_digest: runs[name].request.take_digest, run_json: path.join(runs[name].request.work, 'observe', 'run.json'), requests: path.join(runs[name].request.work, 'requests.jsonl'),
    bundles: path.join(runs[name].request.work, 'bundles.jsonl'), replies: runs[name].request.replies, work: fs.mkdtempSync(path.join(work, 'patched-')), owed: null, room_model: runs[name].room })).trim().split('\n').at(-1));
  assert.notDeepEqual(patched('on'), patched('across'), 'control: a seat-dependent picture moves the hashes');
  // Test 12: den-register's own rule, a later camera revision and an invalidation, keeps the surface and its landmark.
  const kept = JSON.parse(python([script('later.py', LATER_REVISIONS), path.join(HERE, 'scene'), path.join(work, 'register-tree'), runs.on.room]).trim().split('\n').at(-1));
  assert.deepEqual(kept, [['sofa', 'sofa-seat'], ['sofa', 'sofa-seat'], []], 'the surface survives revisions; control: a fresh room model has none');
  // Test 12's control at couch_to_room.mjs itself: one edited byte of the mark is refused by its seal, and nothing is written.
  const edited = path.join(work, 'edited.json'), digest = JSON.parse(fs.readFileSync(markFile, 'utf8')).digest;
  fs.writeFileSync(edited, fs.readFileSync(markFile, 'utf8').replace(digest, `${digest.slice(0, -1)}${digest.endsWith('0') ? '1' : '0'}`));
  assert.throws(() => writeCouch({ seatFile: edited, registration, from: acrossFile, to: path.join(work, 'edited', 'room-model.jsonl'), source: 'sofa_mark' }), /^TypeError: sofa_target_changed$/);
  assert.equal(fs.existsSync(path.join(work, 'edited')), false);
}
const chainWords = card => `${card.seat_distance_m} m from the seat point, on the couch ${card.on_couch ? 'yes' : `no (${card.failed.join(', ')})`}`;

test('tests 9, 12 and 19: a sealed invented mark reads YOUR SOFA MARK, and no number of it or the camera is printed', { skip: NEEDS_SET, timeout: 900000 }, async t => {
  const home = scratch('den-sim-mark-'), privateFolder = path.join(home, 'private'), fits = path.join(home, 'private', 'fits');
  t.after(() => fs.rmSync(home, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }));
  const corners = [[1.137, 2.291, 0.463], [2.937, 2.291, 0.463], [2.937, 3.183, 0.463], [1.137, 3.183, 0.463]];
  const mark = invent(privateFolder, corners);
  const markNumbers = [...corners.flat(), ...mark.seat.point_m, ...mark.zone.centre_m];
  // A fake vLLM's replies for the reading take in this scratch data root, so its click reaches the couch: the
  // bodies a re-fit on a mark builds are the ones --owed filed on the set's own fit.
  const readingTake = JSON.parse(fs.readFileSync(path.join(SET, 'display.json'), 'utf8')).scenes[0].take;
  execFileSync('python', ['-B', '-X', 'utf8', path.join(HERE, 'test_ask.py'), '--fill', home, '--set', SET, '--facts', `reading=${readingTake}`],
    { encoding: 'utf8', windowsHide: true, timeout: 600000, env: { ...process.env, DEN_SIM_HOME: home } });
  const start = async () => {
    const run = harness(['--room', 'real', '--prepared', SET], scratchEnv(home));
    try { await run.ready; return { run, text: run.output() + run.errors() }; } catch (error) { await run.stop(); throw error; }
  };
  const fitFolders = () => fs.readdirSync(fits, { withFileTypes: true }).filter(entry => entry.isDirectory()).map(entry => entry.name);
  // Test 12: REAL SOCKETS and YOUR SOFA MARK, and the startup line in words.
  let { run, text } = await start();
  try {
    assert.match(text, /Sockets: REAL SOCKETS, registration revision 3 \| Couch: YOUR SOFA MARK/);
    assert.match(text, /Camera registration: re-fit den-virtual@\S+ on your sofa mark, at this start/);
    assert.match(text, /^Fixtures for the stated facing, at the spot: F1 (clear|refused: [a-z ]+)(, F\d (clear|refused: [a-z ]+))*(\. None passes: .*)?$/m);
    assert.equal(fitFolders().length, 1);
    t.diagnostic(text.match(/^Fixtures for the stated facing.*$/m)[0]);
    const roomModel = fs.readFileSync(path.join(fits, fitFolders()[0], 'room-model.jsonl'), 'utf8').trim().split('\n').at(-1);
    const camera = JSON.parse(roomModel).cameras.at(-1).pose.room_from_camera.slice(0, 3).map(row => row[3]);
    // Test 9's stdout half, and 19's: no camera position and no number of the mark in stdout or stderr.
    assert.deepEqual(coordinatesIn(text, camera), []);
    assert.deepEqual(coordinatesIn(text, markNumbers), []);
    const now = await click(JSON.parse(fs.readFileSync(path.join(SET, 'display.json'), 'utf8')).scenes[0].take);
    assert.deepEqual([now.click.rows[11].word, now.click.rows[13].word, now.click.rows[13].reached], ['SCRIPTED', 'ADAPTER', true], 'the replies were found, and the couch is your mark');
    assert.equal(now.click.top_line.line, 'Connected start to finish: not yet');
    assert.ok(!now.click.top_line.reasons.some(reason => /stand-in seat|authored sockets/.test(reason.text)), 'hop 5 read your files, not the stand-ins');
    assert.deepEqual(now.click.top_line.reasons.filter(reason => [14, 15].includes(reason.hop)), [], 'hops 14 and 15 hold: ADAPTER, named stand-ins, digests vouched');
    // Test 12: the couch in the pipeline's own RoomModel, one revision appended by den-register's rule to a copy of the
    // fit's file, which still matches its receipt: the surface, its landmark, the spot 0.30 m up, named after the mark.
    const fit = fitFolders()[0], fitText = fs.readFileSync(path.join(fits, fit, 'room-model.jsonl'), 'utf8'), couchText = fs.readFileSync(path.join(fits, fit, 'couch', 'room-model.jsonl'), 'utf8');
    assert.equal(sha256(path.join(fits, fit, 'room-model.jsonl')), JSON.parse(fs.readFileSync(path.join(fits, fit, 'refit-receipt.json'), 'utf8')).room_model_sha256, 'the fit\'s own file is unchanged');
    assert.ok(couchText.startsWith(fitText) && couchText.trim().split('\n').length === fitText.trim().split('\n').length + 1, 'one revision appended, the rest byte for byte');
    const [before, after] = [fitText, couchText].map(text => JSON.parse(text.trim().split('\n').at(-1)));
    const sofa = after.surfaces.find(surface => surface.surface_id === 'sofa');
    assert.deepEqual([after.version, after.cameras, sofa.kind, sofa.geometry.form, after.landmarks, after.provenance.surfaces.mark_digest],
      [nextVersion(before.version, before.room_id), before.cameras, 'seat', 'polygon-3d/1', [{ landmark_id: 'sofa-seat', kind: 'seat', surface_id: 'sofa' }], mark.digest]);
    assert.deepEqual(sofa.geometry.book_point_m.map((v, i) => Math.round((v - sofa.geometry.seat_point_m[i]) * 1000) / 1000), [0, 0, .3], 'the spot 0.30 m above the seat point');
    assert.match(now.click.rows[13].summary, new RegExp(`as revision ${after.version}: surface sofa, landmark sofa-seat`));
    // The same fit, on the mark: the person card reads on the couch, on the picture's couch placed on your mark.
    assert.deepEqual([now.click.rows[14].word, now.click.person.tracks.map(row => row.on_couch)], ['ADAPTER', [true]], now.click.rows[14].summary);
    assert.ok(now.click.person.lines.includes('On the picture\'s couch, which this simulation placed on your mark.'));
    // Tests 19 and 9: no number of the mark or the camera in any panel text, the console or the harness's logs.
    const shown = panelText(now), logs = ['run.json', 'requests.jsonl', `click-${now.click.click}/argv.jsonl`].filter(name => fs.existsSync(path.join(now.run, name)))
      .map(name => fs.readFileSync(path.join(now.run, name), 'utf8')).join('\n') + run.output() + run.errors();
    assert.deepEqual(coordinatesIn(shown, markNumbers), [], 'no number of the mark in any panel text');
    assert.deepEqual(coordinatesIn(shown, camera), [], 'nor the camera');
    assert.deepEqual([coordinatesIn(logs, markNumbers), coordinatesIn(logs, camera)], [[], []], 'nor in a console line or a log');
    assert.ok(coordinatesIn(`${text}\ncamera at ${now.camera_m.map(v => v.toFixed(3)).join(', ')}`, camera).length > 0, 'control: the camera as a fit prints it, from what the page gets for its marker, is found');
    assert.ok(coordinatesIn(`${shown}\nseat at ${mark.seat.point_m[0].toFixed(2)}`, markNumbers).length > 0, 'control: a planted number is found');
    const runJson = JSON.parse(fs.readFileSync(path.join(now.run, 'run.json'), 'utf8'));
    assert.equal(runJson.couch.point, undefined); assert.equal(runJson.brain.seat_m, undefined);
    assert.deepEqual(coordinatesIn(JSON.stringify(runJson), markNumbers), []);
  } finally { await run.stop(); }
  // The re-fit runs only when a digest changed: the same files reuse it, a new facing.json makes another.
  ({ run, text } = await start());
  await run.stop();
  assert.match(text, /on your sofa mark, unchanged since an earlier start/);
  assert.equal(fitFolders().length, 1);
  assert.doesNotMatch(text, /turned \d+ degrees/, 'control: no facing.json, no turn named');
  fs.writeFileSync(path.join(privateFolder, 'facing.json'), '{"degrees": 90}');
  ({ run, text } = await start());
  await run.stop();
  assert.match(text, /on your sofa mark, at this start; the reader faces [^\n]*, then turned 90 degrees by facing\.json \(left positive\)/);
  assert.match(text, /^Couch in the click's room model: revision \S+, from your sofa mark; the reader turned 90 degrees from the stated facing by facing\.json$/m);
  assert.equal(fitFolders().length, 2);
  await pictureIndependence(t, { home, privateFolder, fits, corners, readingTake, markNumbers });
  // Controls for test 12: an edited byte is refused by the seal, and a mark with no export is not used.
  const file = path.join(privateFolder, 'a-couch.json');
  fs.writeFileSync(file, fs.readFileSync(file, 'utf8').replace(/"confirmed":true/, '"confirmed":false'));
  ({ run, text } = await start());
  await run.stop();
  assert.match(text, /Couch: DECLARED/);
  assert.match(text, /Note: sofa mark refused by its seal: /);
  assert.doesNotMatch(text, /Fixtures for the stated facing/);
  invent(privateFolder, corners);
  fs.rmSync(path.join(privateFolder, 'registration-revision-3.json'));
  ({ run, text } = await start());
  await run.stop();
  assert.match(text, /Sockets: AUTHORED SOCKETS \| Couch: DECLARED/);
  assert.match(text, /needs the registration export it names/);
  // A malformed private file is refused by name only: JSON.parse's own message quotes the file's text.
  const fragment = '[2.345, 6.789, O.45]';
  assert.throws(() => JSON.parse(`{"point_m": ${fragment}}`), error => coordinatesIn(error.message, [6.789]).length > 0, 'control: the parser quotes the text');
  fs.writeFileSync(path.join(privateFolder, 'registration-revision-3.json'), `{"registration": ${fragment}}`);
  fs.writeFileSync(path.join(privateFolder, 'couch-declared.json'), `{"point_m": ${fragment}}`);
  ({ run, text } = await start());
  try {
    assert.match(text, /^Note: registration export refused: unreadable \(SyntaxError\)$/m);
    assert.match(text, /^Note: couch-declared\.json refused: unreadable \(SyntaxError\)$/m);
    const notes = (await status()).private_notes.join('\n');
    assert.deepEqual(coordinatesIn(text + notes, [2.345, 6.789]), [], 'no part of the file in the console or the notes');
  } finally { await run.stop(); }
});
