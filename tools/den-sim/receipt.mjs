// One click's run-receipt.json: per hop its word, whether it ran now, was built at page start or was
// recorded on a date, its tool files against their pins, the files it read and wrote with digests,
// and its stand-ins. Recorded hops take theirs from prepare-receipt.json, and hop 5's re-fit from
// refit-receipt.json. The panel and top_line.mjs read only this.
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { installPack } from '../fixtures/apartment-home-shell-fixtures.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url)), ROOT = path.resolve(HERE, '../..');
export const TABLE = JSON.parse(fs.readFileSync(path.join(HERE, 'hops.json'), 'utf8'));
export const SCHEMA = 'den-sim-run-receipt-1';
const HARNESS = { 1: ['render.mjs', 'page.html', 'figure.js', 'shots.json'], 2: ['vcam.py'], 3: [], 4: [], 5: [] };

export const sha256 = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const git = (args, input) => execFileSync('git', ['-C', ROOT, ...args], { encoding: 'utf8', windowsHide: true, input, stdio: ['pipe', 'pipe', 'ignore'] });
const lsTree = new Map();
// {path: blob} at a commit. Commits never change, so each is read once.
export function blobsAt(commit, paths) {
  const key = `${commit} ${paths.join(' ')}`;
  if (!lsTree.has(key)) {
    const out = {};
    for (const line of git(['ls-tree', '-r', commit, '--', ...paths]).split('\n').filter(Boolean)) out[line.split('\t')[1]] = line.split('\t')[0].split(' ')[2];
    lsTree.set(key, out);
  }
  return lsTree.get(key);
}
export const resolve = ref => git(['rev-parse', `${ref}^{commit}`]).trim();
const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const atCommit = new Map();
// The sha256 of a file's bytes at a commit, or null when the commit has no such file.
function sha256At(commit, file) {
  const key = `${commit}:${file}`;
  if (!atCommit.has(key)) {
    try { atCommit.set(key, digest(execFileSync('git', ['-C', ROOT, 'cat-file', 'blob', key], { windowsHide: true, maxBuffer: 256 * 1024 * 1024, stdio: ['ignore', 'pipe', 'ignore'] }))); } catch { atCommit.set(key, null); }
  }
  return atCommit.get(key);
}
// The authored room render.mjs drew, rebuilt the way it built it: installPack's collision.glb.
let roomGlb;
function authoredRoomDigest() {
  if (roomGlb === undefined) {
    const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'den-sim-room-'));
    try { roomGlb = digest(installPack(scratch, 'den-sim-scene').files['collision.glb']); } catch { roomGlb = null; } finally { fs.rmSync(scratch, { recursive: true, force: true }); }
  }
  return roomGlb;
}
// This branch's files against their blobs at HEAD, read on each click: an uncommitted edit shows.
export function headTools(paths) {
  const expected = blobsAt(resolve('HEAD'), paths), actual = git(['hash-object', '--stdin-paths'], paths.join('\n') + '\n').split('\n');
  return paths.map((file, i) => ({ path: file, pin: 'HEAD', expected: expected[file] || null, actual: actual[i] || null }));
}
// A recorded file's blob, held to the bytes that ran as well: the sha256 prepare took of the file it
// ran must be the sha256 of the pinned blob's bytes, so a blob name copied from ls-tree vouches for nothing.
function ranAs(commit, file, row) {
  if (!row?.blob) return null;
  if (row.sha256 && row.sha256 !== sha256At(commit, file)) return `${row.blob}, though the bytes that ran (sha256 ${row.sha256.slice(0, 12)}) are not its bytes`;
  return row.blob;
}
// A tool recorded by prepare, from its receipt: the blobs it ran against the blobs at the pin refs.json names now.
function recordedTools(record, pinRef, pinLabel) {
  const commit = resolve(pinRef), expected = blobsAt(commit, Object.keys(record.files));
  return [{ path: `${pinLabel} commit`, pin: pinLabel, expected: commit, actual: record.commit },
    ...Object.entries(record.files).map(([file, row]) => ({ path: file, pin: pinLabel, expected: expected[file] || null, actual: ranAs(commit, file, row) }))];
}
const onBranch = new Map();
// Whether a commit is this branch's: HEAD or one of its ancestors.
function isAncestor(commit) {
  if (!onBranch.has(commit)) {
    try { execFileSync('git', ['-C', ROOT, 'merge-base', '--is-ancestor', commit, 'HEAD'], { windowsHide: true, stdio: 'ignore' }); onBranch.set(commit, true); } catch { onBranch.set(commit, false); }
  }
  return onBranch.get(commit);
}
// The harness files prepare ran, against their blobs at the commit that prepared the set, which must
// be on this branch: a set prepared from an unrelated commit with other scene code doesn't read clean.
function harnessTools(harness, names) {
  const files = ['prepare.py', ...names].map(name => `tools/den-sim/scene/${name}`), expected = blobsAt(harness.commit, files), pin = `the commit that prepared it (${harness.commit.slice(0, 8)})`;
  return [{ path: 'the commit that prepared it', pin: 'an ancestor of HEAD', expected: harness.commit, actual: isAncestor(harness.commit) ? harness.commit : `${harness.commit.slice(0, 8)}, not on this branch`,
      why: `(${harness.commit.slice(0, 8)}) is not on this branch: HEAD does not descend from it` },
    ...files.map(file => ({ path: file, pin, expected: expected[file] || null, actual: ranAs(harness.commit, file, harness.files[file]) }))];
}
// What kind of recorded input a prepare-time read was, by its name.
function kindOf(n, file) {
  if (n === 1) return file.endsWith('shots.json') ? 'shots.json' : 'render';
  if (file.endsWith('.npz')) return 'marks take';
  if (/clicks-[0-9a-f]+\.json$/.test(file)) return 'clicks';
  if (file.endsWith('camera-calibration.json')) return 'calibration';
  if (file.endsWith('worksheet.json')) return 'worksheet';
  return 'unknown';
}

// What a recorded read holds now, hashed again, never copied from the receipt that recorded it: hop 1's
// repo files at the commit that prepared the set, and its room rebuilt; hop 5's files in the set itself.
function rehashed(n, file, harness, setDir) {
  if (n === 1) return file.path.startsWith('collision.glb') ? authoredRoomDigest() : sha256At(harness.commit, file.path);
  try { return setDir ? sha256(path.join(setDir, ...file.path.split('/'))) : null; } catch { return null; }
}

// Hops 1 to 5, as prepare recorded them, with hop 3's re-check by hash and hop 5's re-fit at page start.
function recordedRows({ prepared, refs, recheck, checkRecheck, takeRead, placement, setDir }) {
  const by = `prepare-receipt.json of take ${prepared.take}`, rows = [];
  for (const n of [1, 2, 3, 4, 5]) {
    const hop = prepared.hops[String(n)];
    if (!hop) { rows.push({ n, name: TABLE.hops[n - 1].name, word: 'MISSING', reached: true, ran: 'not recorded', note: 'prepare recorded no such hop', tools: [], read: [], wrote: [], stand_ins: [] }); continue; }
    const row = { n, name: hop.hop, word: hop.word, reached: true, ran: `recorded ${prepared.recorded}`, tool: { path: hop.tool, commit: hop.commit, pin: hop.pin },
      tools: harnessTools(prepared.harness, HARNESS[n]), read: [], wrote: [], stand_ins: [...hop.stand_ins] };
    const vouched = file => ({ path: file.path, sha256: rehashed(n, file, prepared.harness, setDir), recorded: { kind: kindOf(n, file.path), by, sha256: file.sha256 } });
    if (n === 1) {
      row.summary = `render.mjs drew ${new Set(hop.wrote.map(file => file.pose)).size} poses of procedural-1 on ${hop.renderer.split('(')[0].trim()} software GL, three r${hop.three}; ${hop.external_requests} requests left loopback.`;
      row.read = hop.read.map(vouched);
      row.wrote = hop.wrote.map(file => ({ path: `render (${file.kind}, pose ${file.pose})`, sha256: file.sha256 }));
    }
    if (n === 2) row.summary = `vcam.py served ${hop.frames_built} frames as ${hop.camera_id}, session ${hop.session}, at ${hop.speed}x, picture ${hop.picture}.`;
    if (n === 3 || n === 4) {
      row.tools.push(...recordedTools(hop.tool_files ? { commit: hop.commit, files: hop.tool_files } : { commit: hop.commit, files: {} }, refs.tools['den-take'], 'refs.json tools.den-take'));
      row.wrote = hop.wrote.map(file => ({ path: file.path, sha256: file.sha256 }));
      if (n === 4) row.read = takeRead ? [takeRead] : [];
      row.summary = n === 3 ? `take.py recorded ${hop.counts?.kept ?? 0} frames in ${hop.wall_s} s of wall, on a fast clock with its cues silenced.`
        : `check.py: ${hop.verdict}, retained ${hop.retained}, ${hop.usable_s} s usable; the harness answered its second-person prompt.`;
    }
    if (n === 3) {
      row.ran = `recorded ${prepared.recorded}, re-checked by hash now`;
      row.read = recheck ? recheck.read : [];
      row.tools.push(...(recheck?.tools || []));
      if (!recheck) row.ran = `recorded ${prepared.recorded}, not re-checked on this click`;
      if (recheck && !recheck.ok) { row.word = 'MISSING'; row.note = recheck.reason; row.summary = `Re-checked by hash now, and refused: ${recheck.reason}.`; }
      row.recheck = recheck ? { ok: recheck.ok, frames: recheck.frames, reason: recheck.reason } : null;
    }
    if (n === 4) {
      // The check prepare recorded, by its digest, and no other check-N.json beside it.
      row.ran = checkRecheck ? `recorded ${prepared.recorded}, re-checked by hash now` : `recorded ${prepared.recorded}, not re-checked on this click`;
      if (checkRecheck && !checkRecheck.ok) { row.word = 'MISSING'; row.note = checkRecheck.reason; row.summary = `Re-checked by hash now, and refused: ${checkRecheck.reason}.`; }
      row.recheck = checkRecheck ? { ok: checkRecheck.ok, reason: checkRecheck.reason, checked: checkRecheck.read } : null;
    }
    if (n === 5) {
      row.tools.push(...recordedTools({ commit: hop.commit, files: hop.tool_files }, refs.tools['den-register'], 'refs.json tools.den-register'));
      row.read = hop.read.map(vouched);
      row.wrote = hop.wrote.map(file => ({ path: file.path, sha256: file.sha256 }));
      row.stand_ins = hop.stand_ins.filter(text => !text.startsWith("the pose is in the picture's own frame"));
      row.ran = `recorded ${prepared.recorded}, re-fit at page start`;
      row.summary = `${hop.result} ${hop.transform_id || ''}, error bound ${hop.error_bound_mm} mm; ${placement.summary}`;
      for (const [name, record] of Object.entries(placement.tools || {})) row.tools.push(...recordedTools(record, refs.tools[name], `refs.json tools.${name}`));
      row.read.push(...placement.read);
      row.wrote.push(...placement.wrote);
      row.stand_ins.push(placement.stand_in);
      if (hop.word !== 'ADAPTER' || !placement.registered) { row.word = 'MISSING'; row.note = placement.registered ? hop.reasons.join(', ') : placement.summary; }
    }
    rows.push(row);
  }
  return rows;
}

// Hop 6's recorded tools, from its part of prepare-receipt.json: the files detect.py loaded from its tools root,
// against refs.json tools.rung-2-observe, and detect.py as it ran, against HEAD.
function detectorTools(hop, refs) {
  if (!hop?.tool_files) return [{ path: 'the detector\'s tools', pin: 'prepare-receipt.json hop 6', expected: 'recorded', actual: null }];
  const head = resolve('HEAD'), file = 'tools/den-detect/detect.py';
  return [...recordedTools({ commit: hop.commit, files: hop.tool_files }, refs.tools['rung-2-observe'], 'refs.json tools.rung-2-observe'),
    { path: `${file}, as it ran`, pin: 'HEAD', expected: blobsAt(head, [file])[file] || null, actual: ranAs(head, file, hop.detect_py) }];
}

const missing = (n, note, extra = {}) => ({ n, name: TABLE.hops[n - 1].name, word: 'MISSING', reached: true, ran: 'not built', note, summary: note, tools: [], read: [], wrote: [], stand_ins: [], ...extra });

// The whole receipt. start: what run.mjs built at page start; chain: chain.py's rows and stop; brain,
// command: the older brain's timeline and the dry run; files: this click's own outputs with digests.
export function buildReceipt({ click, takeId, setId, setDir, prepared, refs, start, chain, brain, command, pressedAgain = false }) {
  const rows = recordedRows({ prepared, refs, recheck: chain.recheck, checkRecheck: chain.checkRecheck, takeRead: chain.takeRead, placement: start.placement, setDir });
  const byN = new Map(chain.rows.map(row => [row.n, row]));
  for (const n of [6, 7, 8, 9, 10, 11, 12, 13]) {
    const row = byN.get(n);
    if (row && n === 6) row.tools = [...(row.tools || []), ...detectorTools(prepared.hops['6'], refs)];
    rows.push(row ? { name: TABLE.hops[n - 1].name, reached: true, tools: [], read: [], wrote: [], stand_ins: [], ...row } : null);
  }
  rows.push({ ...start.couch, n: 14, name: TABLE.hops[13].name, reached: true });
  rows.push(byN.get(15) ? { name: TABLE.hops[14].name, reached: true, tools: [], read: [], wrote: [], stand_ins: [], ...byN.get(15) } : null);
  rows.push(missing(16, 'Not built: no light model exists before any picture yet.', { ran: 'not built at page start' }));
  rows.push(brain?.row || null);
  rows.push(brain?.feedRow || null, brain?.motionRow || null, start.home && { ...start.home, name: TABLE.hops[19].name, reached: true });
  rows.push(...(command?.rows || [null, null, null]));
  rows.push(missing(24, 'Not built: nothing compares where Home points with where the Decision said.'),
    missing(25, 'Not built: no virtual meter reads Home\'s modeled light.'), missing(26, 'Not built: den-verify does not run.'));
  const stop = chain.stop || brain?.stop || null;
  const out = rows.map((row, i) => finalRow(row, i + 1, stop));
  return { schema: SCHEMA, click, take: takeId, set: setId, recorded: prepared.recorded, written_unix: Date.now() / 1000,
    prepare_refusal: prepared.refusal, stopped: stop, pressed_again: pressedAgain, aim_check: null, picture: chain.picture || null, rows: out };
}

// The person card (hop 15), in words from what this click's row recorded, never from the button: per track how far it
// is from the seat point, on the couch or not and which half failed, the spot by name and the engagement. No coordinate.
// A distance hop 15 left null (a held reply, no torso joint) reads in words: a card never throws on what person.py writes.
const metres = v => Number.isFinite(v) ? `${v.toFixed(2)} m` : 'an unmeasured distance';
// Each half that failed, in words, built only for the halves card.failed names.
const HALVES = {
  position: card => `the position half failed: ${metres(card.footprint_m)} outside the couch's footprint, past its 0.15 m reach`,
  height: card => `the height half failed: torso ${metres(card.torso_margin_m)} over the limit, from the ${card.torso_from}`,
  torso: () => 'the height half failed: no supported shoulder or hip in the bundle\'s last 2 s',
};
export function personCard(receipt) {
  const row = receipt.rows.find(item => item.n === 15), couch = receipt.rows.find(item => item.n === 14);
  if (!row || row.reached === false) return null;
  if (row.word === 'MISSING' || !Array.isArray(row.person)) {
    const why = row.note || row.summary || 'hop 15 wrote no person state';
    return { tracks: [], missing: why, lines: [`Person state: MISSING: ${why}.`] };
  }
  const placed = couch?.couch === 'sofa_mark' ? 'On the picture\'s couch, which this simulation placed on your mark.'
    : 'On the picture\'s couch, which this simulation placed on a DECLARED stand-in seat, not your sofa mark.';
  const facing = Number.isFinite(couch?.facing_deg) ? [`The reader faces your way, from facing.json: turned ${Number(couch.facing_deg.toFixed(1))} degrees from the stated facing, left positive.`] : [];
  const lines = row.person.flatMap(card => {
    const couchWords = card.on_couch === true ? 'On the couch: yes.'
      : card.on_couch === false ? `On the couch: no: ${(card.failed || []).map(name => (HALVES[name] || (() => `the ${name} half failed`))(card)).join('; ')}.`
      : `On the couch: not tested yet: ${card.held ? 'its bundle was not safe to tie to one person, so the reply was held' : 'no position'}.`;
    const spot = card.region === 'sofa-book' ? 'The spot: sofa-book, the book spot 0.30 m above the seat point. Its marker in the room comes at step 7.'
      : card.region === 'handheld' ? 'The spot: handheld, which isn\'t in the model, so no target.' : 'The spot: none yet.';
    const need = card.need_replies === 0 ? `Engagement: ${card.engagement}, support ${card.support}: every reply was held, so need v0 had none of this track's replies.`
      : `Engagement: ${card.engagement}, support ${card.support}, from need v0 on this track's replies.`;
    return [`Track ${card.track_id}: ${Number.isFinite(card.seat_distance_m) ? `${metres(card.seat_distance_m)} from the seat point, across the floor` : 'no position'}.`, couchWords, spot, need];
  });
  return { tracks: row.person.map(card => ({ track_id: card.track_id, on_couch: card.on_couch, held: !!card.held })), track_lines: lines.length, lines: [...lines, placed, ...facing,
    'The older brain still decides the beam until step 6, under its own 1.5 m rule, so a figure standing within 1.5 m of the seat would still light.'] };
}
// Run every scene's Person column, from the card: one word per track, MISSING with why, and "none" only with no track.
export function personColumn(card) {
  if (!card) return 'none';
  if (card.missing) return `MISSING: ${card.missing}`;
  if (!card.tracks.length) return 'none';
  return card.tracks.map(track => track.on_couch === true ? 'on the couch' : track.on_couch === false ? 'not on the couch'
    : `not tested (${track.held ? 'held' : 'no position'})`).join(', ');
}

// Each row as the click left it. A hop past where the click stopped keeps its expected word, greyed,
// "not reached on this click". The hop where it broke reads MISSING with why, and rows before it stay as
// they ran. A hop with no row, though nothing stopped before it, reads MISSING: never "not reached".
// A hop built at page start (hops.json's when "page") keeps the word it was built with, greyed: this click didn't reach it,
// but it is not the table's word to claim.
export function finalRow(row, n, stop) {
  const hop = TABLE.hops[n - 1], empty = { tools: [], read: [], wrote: [], stand_ins: [] };
  if (stop && n > stop.hop && hop.when === 'page' && row) {
    return { ...row, n, name: hop.name, reached: false, ran: `${row.ran}; not reached on this click`, note: 'not reached on this click' };
  }
  if (stop && n > stop.hop) return { n, name: hop.name, word: hop.word, reached: false, ran: 'not reached on this click', note: 'not reached on this click', ...empty };
  if (stop && n === stop.hop && !stop.answer) {
    return { ...empty, n, name: hop.name, ran: 'ran now', ...row, word: 'MISSING', reached: true, note: stop.why,
      summary: row?.word === 'MISSING' && row.summary ? row.summary : `The click broke here: ${stop.why}.` };
  }
  if (row) return row;
  const why = stop ? 'this click has no row for it, though it went on past it' : 'this click has no row for it, and nothing stopped it';
  return { ...empty, n, name: hop.name, word: 'MISSING', reached: true, ran: 'no row on this click', note: why, summary: `No row: ${why}.` };
}

// What the panel says under the picture. Boxes are the shown frame's person boxes when hop 6's rows hold,
// and null when they don't: then no row is drawn, and the note never says the frame holds no person.
const JOINT_MIN = .4; // the pose manifest's score_threshold, the panel's too: a joint at or over it is drawn

export function pictureNote(picture) {
  if (!picture || picture.error) return null;
  const frame = 'The take\'s own frame, read back out of its NV12 plane';
  if (!Array.isArray(picture.boxes)) return `${frame}. The detector's recorded rows did not pass hop 6's checks, so no box is drawn.`;
  if (!picture.boxes.length) return `${frame}. The detector's row for this frame holds no person, so no box is drawn.`;
  const scores = picture.boxes.map(box => `person ${box.score.toFixed(2)}`).join(', ');
  const joints = picture.boxes.some(box => (box.keypoints || []).some(point => point?.[2] >= JOINT_MIN));
  if (!joints) return `${frame}, with YOLO26s's person box and score (${scores}) from this frame's recorded row: box only, RTMPose-m placed no supported joint on it.`;
  return `${frame}, with YOLO26s's person box and score (${scores}) and RTMPose-m's supported joints from this frame's recorded row.`;
}

// The panel's view: the words and notes, without the digests the page has no use for.
export function receiptView(receipt) {
  // A row sits under what it did on this click. A row that didn't run (not built yet, not reached, or
  // with no row) sits apart, never under "Ran on this click".
  const group = row => row.reached === false || /^(not |no row)/.test(row.ran) ? 'not run' : row.ran.startsWith('recorded') ? 'recorded' : row.ran.includes('page start') ? 'page' : 'click';
  // Qwen on this click, from hop 12's row: its word, the model's served root, and what it said per request.
  const twelve = receipt.rows.find(row => row.n === 12);
  const qwen = twelve?.reached !== false && twelve?.asks ? { word: twelve.word, root: twelve.root || null, reply: twelve.reply || null, said: twelve.said || null, asks: twelve.asks } : null;
  return { click: receipt.click, take: receipt.take, stopped: receipt.stopped, person: personCard(receipt), qwen,
    boxes: Array.isArray(receipt.picture?.boxes) ? receipt.picture.boxes : null, picture_note: pictureNote(receipt.picture),
    rows: receipt.rows.map(row => ({ n: row.n, name: row.name, word: row.word, reached: row.reached !== false, ran: row.ran, group: group(row),
      summary: row.summary || row.note || '', stand_ins: row.stand_ins || [], tool: row.tool?.path || null })) };
}
