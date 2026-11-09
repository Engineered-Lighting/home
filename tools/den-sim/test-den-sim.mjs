// The den simulation's click side, without a browser (plan, "Done means"): the data root; test 7,
// the top line, on fixtures under all six conditions and on the real chain; the hop table's count;
// test 2's searches over click-path code, what clicks wrote and what the harness served; test 5's
// frame hash on a click; test 9's stdout half, test 12 and test 19's private half on a sealed
// invented export and mark in a scratch data root; and the site id. Each check has a control
// that must make it fail.
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
import { buildReceipt, finalRow } from './receipt.mjs';
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
  'dry_run.py', 'scene/frame_hash.py'];
// The plan's step 2 words, hop by hop, with no sofa mark saved.
const STEP2 = { 1: 'SCRIPTED', 2: 'SCRIPTED', 3: 'ADAPTER', 4: 'ADAPTER', 5: 'ADAPTER', 6: 'SCRIPTED', 7: 'REAL', 8: 'REAL', 9: 'ADAPTER', 10: 'SCRIPTED',
  11: 'MISSING', 12: 'SCRIPTED', 13: 'ADAPTER', 14: 'SCRIPTED', 15: 'MISSING', 16: 'MISSING', 17: 'ADAPTER', 18: 'ADAPTER', 19: 'SCRIPTED', 20: 'REAL',
  21: 'ADAPTER', 22: 'ADAPTER', 23: 'SCRIPTED', 24: 'MISSING', 25: 'MISSING', 26: 'MISSING' };

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
  assert.equal(edited(r => { r.pressed_again = true; }).line, 'Pressed again: press Reset for a new verdict');
  assert.equal(edited(r => { r.pressed_again = true; r.aim_check = null; }).verdict, 'pressed again', 'a second press needs no fresh aim check');
  assert.equal(edited(r => { r.pressed_again = true; stoppedAt(15, 'not on the couch')(r); }).verdict, 'stopped', 'a second press the pipeline answered still says so');
  assert.equal(edited(r => { r.prepare_refusal = { hop: 4, reason: 'check.py: FAIL' }; }).line, 'Prepare stopped at hop 4: check.py: FAIL');
  assert.equal(edited(r => { r.stopped = { hop: 7, why: 'replay.py exit 2', answer: false }; }).verdict, 'not yet', 'a break is never an answer');
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
    const guarded = file => execFileSync('python', ['-B', file, '--guarded', probe], { encoding: 'utf8', windowsHide: true }).trim().split(/\r?\n/);
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

test('the real chain on every prepared take: step 2\'s words, not yet, and nothing told the scene', { skip: NEEDS_SET, timeout: 400000 }, async t => {
  const home = scratch('den-sim-click-');
  const run = harness(['--room', 'authored', '--prepared', SET], scratchEnv(home));
  t.after(async () => { await run.stop(); fs.rmSync(home, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); });
  await run.ready;
  const display = JSON.parse(fs.readFileSync(path.join(SET, 'display.json'), 'utf8')).scenes;
  const first = await status();
  assert.deepEqual(first.scenes, display.map(({ take, label }) => ({ take, label })), 'the buttons carry labels and take ids, never the words');
  for (const key of ['seat_m', 'point']) assert.ok(!JSON.stringify({ couch: first.couch, brain: first.brain }).includes(`"${key}"`), `${key} left the startup record`);
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
  assert.deepEqual(Object.fromEntries(reading.view.rows.map(row => [row.n, row.word])), Object.fromEntries(Object.entries(STEP2).map(([n, word]) => [Number(n), word])));
  for (const { view } of Object.values(clicks)) assert.deepEqual(view.rows.map(row => row.name), TABLE.hops.map(hop => hop.name), 'every row is named as the table names it');
  // Hop 17's older brain is real code with stand-ins inside it; hop 18's replay of its timeline is written here.
  assert.equal(reading.view.top_line.count_line, '3 real, 9 adapter (8 real tools with a named stand-in, 1 written for this simulation), 8 scripted, 6 missing');
  assert.equal(reading.receipt.rows[16].adapter, 'stand-in');
  // Each row under what it did on this click; the rows that did not run sit apart, never under "Ran on this click".
  const grouped = Object.entries(Object.groupBy(reading.view.rows, row => row.group)).map(([group, rows]) => [group, rows.map(row => row.n)]);
  assert.deepEqual(Object.fromEntries(grouped), { recorded: [1, 2, 3, 4, 5], page: [14], click: [6, 7, 8, 9, 10, 12, 13, 17, 18, 19, 20, 21, 22, 23], 'not run': [11, 15, 16, 24, 25, 26] });
  assert.equal(reading.view.rows[10].ran, 'not built', 'hop 11 ran nothing');
  assert.ok(!reading.view.rows.some(row => /Person to aim|decide v0/.test(row.name)), "#424's two rows have left the list");
  assert.match(reading.view.rows[5].summary, /joints from the truth/);
  assert.match(reading.view.rows[5].stand_ins[0], /fake_detector places the figure's true joints from the truth folder/);
  assert.equal(reading.view.rows[2].ran, `recorded ${JSON.parse(fs.readFileSync(path.join(SET, 'set.json'), 'utf8')).recorded}, re-checked by hash now`);
  assert.ok(reading.view.top_line.reasons.some(reason => reason.condition === 6), 'MISSING rows are among the reasons');
  assert.ok(reading.view.top_line.reasons.some(reason => /Detector and box read truth/.test(reason.text)), 'the truth read is a reason, and stays one until step 4');
  // Every recorded file of the take that a click reads is held to the digest prepare recorded for it.
  const recordedFor = n => JSON.parse(fs.readFileSync(path.join(SET, reading.view.take, 'prepare-receipt.json'), 'utf8')).hops[String(n)].wrote;
  const wroteDigest = Object.fromEntries([...recordedFor(3), ...recordedFor(4)].map(file => [file.path, file.sha256]));
  const rowOf = n => reading.receipt.rows[n - 1], take = reading.view.take;
  assert.deepEqual(rowOf(3).read.map(file => [file.path, file.sha256, file.recorded.sha256]),
    [`${take}/frames.csv`, `${take}/take.json`].map(name => [name, wroteDigest[name], wroteDigest[name]]), 'hop 3 re-checks frames.csv and take.json');
  assert.equal(rowOf(4).ran, `${rowOf(1).ran.split(',')[0]}, re-checked by hash now`);
  assert.deepEqual(rowOf(4).recheck.checked.map(file => [file.path, file.sha256]), [[`${take}/check-1.json`, wroteDigest[`${take}/check-1.json`]]]);
  const sevenReads = rowOf(7).read.filter(file => file.recorded).map(file => [file.path, file.recorded.kind, file.sha256 === file.recorded.sha256]);
  for (const name of ['frames.csv', 'take.json']) assert.deepEqual(sevenReads.find(([file]) => file === `${take}/${name}`), [`${take}/${name}`, 'take', true], `hop 7 lists ${name}`);
  assert.deepEqual(sevenReads.find(([file]) => file === `${take}/check-1.json`), [`${take}/check-1.json`, 'check', true], 'hop 7 lists the check replay read');
  assert.ok(!reading.view.top_line.reasons.some(reason => reason.hop === 7), 'and hop 7 reads only what it may');
  // Finding the take's truth opens every scene's truth record: each one is a read of hop 6, and a reason.
  const scanned = rowOf(6).read.filter(file => /^truth\/\(another take, \d of \d\)\/take\.json$/.test(file.path));
  assert.equal(scanned.length, display.length - 1, JSON.stringify(rowOf(6).read.map(file => file.path)));
  assert.equal(reading.view.top_line.reasons.filter(reason => reason.hop === 6 && /read truth\/\(another take/.test(reason.text)).length, display.length - 1);
  const nobody = clicks['Nobody there'].view;
  assert.deepEqual(nobody.stopped, { hop: 6, why: 'the detector saw nobody, nothing was asked', answer: true });
  assert.ok(nobody.rows.filter(row => row.n > 6).every(row => !row.reached));
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
  // Control: the untouched take in the same copy passes the hash and reaches need.
  const phone = display.find(scene => scene.label === 'Holding a phone').take;
  const control = await click(phone);
  assert.equal(control.click.rows[2].word, 'ADAPTER');
  assert.equal(control.click.rows[12].reached, true);
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
  // A tool that fails on a click: replay, on a room model broken after the page placed it.
  const fits = path.join(home, 'fits'), fit = fs.readdirSync(fits).find(name => fs.existsSync(path.join(fits, name, 'room-model.jsonl')));
  fs.writeFileSync(path.join(fits, fit, 'room-model.jsonl'), '{"not": "a room model"}\n');
  const seven = await click(phone);
  assert.equal(seven.click.stopped.hop, 7, JSON.stringify(seven.click.stopped));
  assert.match(seven.click.stopped.why, /^replay\.py exit \d+: /);
  const byN = Object.fromEntries(seven.click.rows.map(row => [row.n, row]));
  assert.deepEqual([byN[7].word, byN[7].reached], ['MISSING', true]);
  assert.ok([1, 2, 3, 4, 5, 6].every(n => byN[n].reached), 'the rows up to the break ran');
  assert.equal(byN[6].word, 'SCRIPTED');
  assert.ok([8, 9, 13, 17].every(n => !byN[n].reached));
  assert.equal(seven.feed.phase, 'pipeline_failed');
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
    assert.equal(reading.click.rows[12].reached, true);
  } finally { await run.stop(); }
  // A set with no display file says so in one line, with no stack trace.
  fs.unlinkSync(path.join(copy, 'display.json'));
  const bare = harness(['--room', 'authored', '--prepared', copy], scratchEnv(home));
  await assert.rejects(bare.ready, error => /has no display\.json, so it has no buttons: prepare stopped at hop 4/.test(error.message) && !/\n\s+at /.test(error.message));
  await bare.stop();
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
  ...(now.click ? [now.click.top_line.line, now.click.top_line.count_line, ...now.click.top_line.reasons.map(reason => reason.text), now.click.said,
    ...now.click.rows.flatMap(row => [row.ran, row.summary, ...row.stand_ins])] : [])].filter(Boolean).join('\n');

test('tests 9, 12 and 19: a sealed invented mark reads YOUR SOFA MARK, and no number of it or the camera is printed', { skip: NEEDS_SET, timeout: 400000 }, async t => {
  const home = scratch('den-sim-mark-'), privateFolder = path.join(home, 'private'), fits = path.join(home, 'private', 'fits');
  t.after(() => fs.rmSync(home, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }));
  const corners = [[1.137, 2.291, 0.463], [2.937, 2.291, 0.463], [2.937, 3.183, 0.463], [1.137, 3.183, 0.463]];
  const mark = invent(privateFolder, corners);
  const markNumbers = [...corners.flat(), ...mark.seat.point_m, ...mark.zone.centre_m];
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
    assert.equal(now.click.rows[13].word, 'ADAPTER', 'the couch is your mark');
    assert.equal(now.click.top_line.line, 'Connected start to finish: not yet');
    assert.ok(!now.click.top_line.reasons.some(reason => /stand-in seat|authored sockets/.test(reason.text)), 'hop 5 read your files, not the stand-ins');
    const shown = panelText(now);
    assert.deepEqual(coordinatesIn(shown, markNumbers), [], 'no number of the mark in any panel text');
    assert.deepEqual(coordinatesIn(shown, camera), [], 'nor the camera');
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
  fs.writeFileSync(path.join(privateFolder, 'facing.json'), '{"degrees": 90}');
  ({ run, text } = await start());
  await run.stop();
  assert.match(text, /on your sofa mark, at this start/);
  assert.equal(fitFolders().length, 2);
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
