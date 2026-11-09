// Geometry and clicks for the den simulation's older lighting brain. The geometry comes from the
// unmodified tools/den-room/brain-targets.mjs. Your sofa mark and registration export are used only
// together, because the mark names revision 3. Without both, a zone is sealed around a DECLARED point
// and the file says so.
//
// On each click chain.py runs the pinned tools on a prepared take, and the brain decides from what
// they produced, on a labelled geometry copy whose camera_m is null: facing comes from the shoulder
// labels, and a refusal shows as no beam. Then the dry run, then the click's run-receipt.json.
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { execFileSync, spawn } from 'node:child_process';
import { registerHooks } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import Core from '../../app/src/home-equipment-registration.js';
import Sofa from '../../app/src/home-lighting-sofa-target.js';
import { sealRegisteredDeskTarget } from '../../app/src/home-3d/registered-desk-target.mjs';
import { TABLE, buildReceipt, headTools, sha256 } from './receipt.mjs';
import { topLine } from './top_line.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url)), ROOT = path.resolve(HERE, '../..');
const THREE = pathToFileURL(path.join(ROOT, 'app/src/vendor/three-0.180.0/three.module.js')).href;
// The same vendored copy Home's import map names, so no npm install is needed.
registerHooks({ resolve(specifier, context, next) {
  if (specifier === 'three') return { url: THREE, format: 'module', shortCircuit: true };
  const resolved = next(specifier, context);
  return resolved.url.includes('/vendor/three-') ? { ...resolved, format: 'module' } : resolved;
} });
const { buildBrainGeometry } = await import('../den-room/brain-targets.mjs');

const ZONE_HALF_M = .4;
const round = v => Math.round(v * 1000) / 1000;
const BRAIN_FILES = ['tools/den-sim/brain_run.py', 'tools/den-sim/brain.mjs', 'tools/den-sim/chain.py',
  ...['__init__.py', 'aim.py', 'brain.py', 'geometry.py', 'inputs.py', 'rules.py', 'settings.py'].map(name => `stack/services/lighting-brain/lighting_brain/${name}`),
  'stack/services/perception-lab/activity_loop.py', 'stack/services/perception-lab/brain_publisher.py'];
const DRY_RUN_FILES = ['tools/den-sim/dry_run.py', ...['fixture_execution.py', 'fixture_joint_execution.py', 'fixture_profile.py', 'test_fixture_profile.py']
  .map(name => `stack/services/home-context/${name}`)];

export function standInSofa(point, registration) {
  const [x, y, z] = point;
  const corners = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([dx, dy]) => ({ point_m: [x + dx * ZONE_HALF_M, y + dy * ZONE_HALF_M, z], normal_unit: [0, 0, 1] }));
  return Sofa.sealSofaTarget({ binding: registration.binding, equipment_ref: { revision: registration.revision, digest: registration.digest }, corners, confirmed: true },
    { sealDesk: sealRegisteredDeskTarget, digest: Core.digest });
}
// brain-targets stamps owner-marked truth on what it builds. A stand-in must not keep that.
function relabel(geometry, den_sim) {
  const { digest: _replaced, ...body } = geometry;
  const labelled = { ...body, synthetic: true, truth_role: 'den_sim_stand_in_not_owner_marked', den_sim };
  return { ...labelled, digest: Core.digest(labelled) };
}
function baseGeometry({ registration, couch, notes }) {
  const inputs = { couch: couch.label, sockets: registration.label };
  if (registration.source === 'export' && couch.source === 'sofa_mark') {
    try { return { geometry: buildBrainGeometry({ sofa: couch.mark, registration: registration.doc, generatedAt: Date.now() / 1000 }).geometry, inputs }; }
    catch (error) { notes.push(`sofa mark refused by brain-targets: ${error.message}. Using a stand-in zone around its seat point.`); }
  }
  const sofa = couch.standIn || standInSofa(couch.point, registration.doc);
  return { geometry: relabel(buildBrainGeometry({ sofa, registration: registration.doc, generatedAt: Date.now() / 1000 }).geometry, inputs), inputs };
}

function runPython(script, request) {
  return JSON.parse(execFileSync('python', ['-B', path.join(HERE, script)], { encoding: 'utf8', windowsHide: true, timeout: 30000, maxBuffer: 32 * 1024 * 1024,
    stdio: ['pipe', 'pipe', 'pipe'], input: JSON.stringify(request) }));
}
// The dry run is a display: if it fails, the swing still stands and the page says why.
function fixtureCommand(take, summary, geometry, refs) {
  const row = geometry.fixtures.find(item => item.fixture_id === summary.fixture_id);
  const brain = summary.lit ? { fixture_id: summary.fixture_id, aim_origin_m: row.aim_origin_m, horizontal_world_reference: row.horizontal_world_reference,
    target_m: summary.target_m, level: summary.level, cct_k: summary.cct_k, pan_deg: summary.pan_deg, tilt_deg: summary.tilt_deg } : null;
  try {
    return runPython('dry_run.py', { take, brain, codec_tools: refs.codec.tools, codec_contracts: refs.codec.contracts });
  } catch (error) {
    return { sent: false, send_path: null, take, failed: `The dry run failed: ${String(error.stderr || error.message).trim().split('\n').at(-1)}` };
  }
}
// What the panel needs from the command. Whole commands are large, and the panel polls its status.
function commandStatus(command) {
  return { pending: false, failed: command.failed || null, lit: !!command.brain, ik_ok: !!command.ik?.ok, ik_reason: command.ik?.reason || null,
    codec_found: !!command.codec?.found, packets: command.wire?.counts.total || 0, can_oracle_ok: command.can?.oracle_ok ?? null,
    allowlist_entries: command.can?.allowlist_entries ?? null };
}
// The hops chain.py runs on a click, in order. A break is filed under one of these, never a hop 0.
const CLICK_HOPS = [3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 17];
const litFixture = row => row.state.fixtures.find(item => item.spot.on) || null;
const plain = text => String(text).replace(/_/g, ' ');
function summarize(timeline, seat) {
  const last = timeline.rows.at(-1), lit = timeline.rows.map(litFixture).find(Boolean), plan = last.spot_plan;
  return { label: timeline.label, events: timeline.events, schemas: timeline.schemas,
    lit: !!lit, refusal: lit ? null : last.refusal, fixture_id: lit?.fixture_id || null, target_m: lit?.spot.target_m || null,
    level: lit?.spot.level ?? null, cct_k: lit?.spot.cct_k ?? null,
    pan_deg: plan?.ok ? plan.pan_deg : null, tilt_deg: plan?.ok ? plan.tilt_deg : null,
    hand_from_seat_m: lit ? round(Math.hypot(...lit.spot.target_m.map((v, i) => v - seat[i]))) : null,
    facing_source: last.reader?.facing_source || null,
    person_m: last.reader ? last.reader.shoulder_mid.slice(0, 2) : null,
    candidates: (plan?.candidates || []).map(row => ({ fixture_id: row.fixture_id, ok: row.ok, reason: row.reason })) };
}

// Rows 17 to 19 and 21 to 23, from what the brain and the dry run did on this click.
function brainRows({ work, summary, files, chainDone }) {
  const said = summary.lit ? `The older brain chose ${summary.fixture_id}, ${summary.hand_from_seat_m} m from the seat, with facing from the ${plain(summary.facing_source)}.`
    : `The older brain lit nothing: ${summary.refusal || 'no reading'}.`;
  const row = { n: 17, name: TABLE.hops[16].name, word: 'ADAPTER', reached: true, ran: 'ran now', summary: said,
    tool: { path: 'stack/services/lighting-brain/lighting_brain/brain.py', pin: 'HEAD' }, tools: [...headTools(BRAIN_FILES)],
    read: [...chainDone.adapter_reads, { ...files.geometry, from_hop: 17 }], wrote: [files.geometry, files.timeline],
    stand_ins: ["the older lighting brain decides, not rung 9's coordinator", "person_to_aim joins room-level reading to the only track and takes its newest joints",
      'jev and deep_stack health asserted, not measured', 'head pitch unknown, so the brain assumes level'] };
  const feedRow = { n: 18, name: TABLE.hops[17].name, word: 'ADAPTER', reached: true, ran: 'ran now', tool: { path: 'tools/den-sim/feed.mjs', pin: 'HEAD' },
    summary: 'This harness replays the brain\'s timeline into Home, re-timed to now, and keeps re-timing the hold. Home\'s "Brain online" means this harness.',
    tools: headTools(['tools/den-sim/feed.mjs', 'tools/den-sim/run.mjs']), read: [{ ...files.timeline, from_hop: 17 }], wrote: [],
    stand_ins: ["replays the older brain's timeline, not a Decision", 'keeps re-timing the hold, as if the reader keeps reading'] };
  const motionRow = { n: 19, name: TABLE.hops[18].name, word: 'SCRIPTED', reached: true, ran: 'ran now', tool: { path: 'tools/den-sim/feed.mjs', pin: 'HEAD' },
    summary: 'Documents stepping target_m at 10 Hz, about 30 deg/s, before the brain\'s lit state. Home\'s follower has no motion model.',
    tools: headTools(['tools/den-sim/feed.mjs']), read: [], wrote: [], stand_ins: [TABLE.hops[18].stand_ins[0]] };
  const stop = summary.lit ? null : { hop: 17, why: plain(summary.refusal || 'the brain lit nothing'), answer: true };
  return { row, feedRow, motionRow, stop, said };
}
function commandRows({ command, files, refs }) {
  const ik = command.ik, codec = command.codec || {}, contracts = refs.codec.contracts;
  const codecTools = Object.entries(refs.codec.sha256 || {}).map(([name, expected]) => {
    const actual = name === 'tools/c12_runtime_plan.py' ? codec.c12_runtime_plan_sha256 : name === 'tools/c12_runtime_wire.py' ? codec.c12_runtime_wire_sha256
      : (() => { try { return sha256(path.join(contracts, path.basename(name))); } catch { return null; } })();
    return { path: `codec ${name}`, pin: 'refs.json codec.sha256', expected, actual: actual || null };
  });
  const fromDry = [{ ...files.dryRun, from_hop: 21 }];
  return [
    { n: 21, name: TABLE.hops[20].name, word: ik?.ok ? 'ADAPTER' : 'MISSING', reached: true, ran: 'ran now', tool: { path: 'tools/den-sim/dry_run.py', pin: 'HEAD' },
      summary: ik?.ok ? 'Real compile_target on a FICTIONAL profile, its pivot and basis the virtual fixture\'s.' : `compile_target refused: ${ik?.reason || command.failed}`,
      note: ik?.ok ? null : ik?.reason || command.failed, tools: headTools(DRY_RUN_FILES), read: [{ ...files.timeline, from_hop: 17 }], wrote: [files.dryRun],
      stand_ins: [TABLE.hops[20].stand_ins[0], "aims at the older brain's hand point, not a Decision's target"] },
    { n: 22, name: TABLE.hops[21].name, word: command.wire ? 'ADAPTER' : 'MISSING', reached: true, ran: 'ran now', tool: { path: refs.codec.tools, pin: 'refs.json codec.sha256' },
      summary: command.wire ? `${command.wire.counts.total} packets from the real codec, read by path, sealed with its public test key for a fictional device. Shown, never sent.`
        : 'The codec was not found at its path, so nothing is encoded.',
      note: command.wire ? null : 'the codec was not found', tools: codecTools, read: fromDry, wrote: [], stand_ins: TABLE.hops[21].stand_ins.slice() },
    { n: 23, name: TABLE.hops[22].name, word: 'SCRIPTED', reached: true, ran: 'ran now', tool: { path: 'tools/den-sim/dry_run.py', pin: 'HEAD' },
      summary: `The Home Assistant envelope with a placeholder ieee, and two CAN A4 frames encoded here${command.can?.oracle_ok ? ', matching the contract\'s oracle' : ''}. Display only.`,
      tools: headTools(['tools/den-sim/dry_run.py']), read: fromDry, wrote: [], stand_ins: [TABLE.hops[22].stand_ins[0]] },
  ];
}

export function prepareBrain({ registration, couch, startup, geometryDirectory, runDirectory, outputRoot, notes, refs }) {
  const { geometry, inputs } = baseGeometry({ registration, couch, notes });
  const seat = geometry.zones.sofa.seat_m;
  const write = (directory, name, value) => { const file = path.join(directory, name); fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n'); return file; };
  const start = runPython('brain_run.py', { geometry: write(geometryDirectory, 'brain-geometry.json', geometry), startup: startup || null });
  let clicks = 0;

  // One click: the chain, then the brain on what it produced, then the dry run, then the receipt.
  function finish({ take, prepared, startRecord, chain, done, work, pressedAgain }) {
    const copy = relabel({ ...geometry, camera_m: null }, { ...inputs, camera: 'none: facing comes from the shoulder labels' });
    const geometryFile = write(work, 'brain-geometry-chain.json', copy);
    const timeline = runPython('brain_run.py', { geometry: geometryFile, person: done.adapter.person, label: done.adapter.label });
    timeline.summary = summarize(timeline, seat);
    timeline.adapter = done.adapter;
    fs.writeFileSync(path.join(work, 'brain-timeline.json'), JSON.stringify(timeline, null, 2) + '\n');
    const files = { geometry: { path: 'brain-geometry-chain.json', sha256: sha256(geometryFile) }, timeline: { path: 'brain-timeline.json', sha256: sha256(path.join(work, 'brain-timeline.json')) } };
    const brain = brainRows({ work, summary: timeline.summary, files, chainDone: done });
    const command = fixtureCommand(take, timeline.summary, geometry, refs);
    fs.writeFileSync(path.join(work, 'dry-run.json'), JSON.stringify(command, null, 2) + '\n');
    files.dryRun = { path: 'dry-run.json', sha256: sha256(path.join(work, 'dry-run.json')) };
    const commandInfo = { rows: brain.stop ? [null, null, null] : commandRows({ command, files, refs }) };
    const receipt = buildReceipt({ click: clicks, takeId: take, setId: prepared.set, prepared, refs, start: startRecord, chain, brain, command: commandInfo, pressedAgain });
    return { ok: true, timeline, command, command_status: commandStatus(command), receipt, said: brain.said };
  }
  function runClick({ take, prepared, setDir, roomModel, startRecord, pressedAgain }, onRow) {
    const work = path.join(runDirectory, `click-${++clicks}`);
    const child = spawn('python', ['-B', path.join(HERE, 'chain.py')], { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    const chain = { rows: [], recheck: null, takeRead: null, picture: null, stop: null };
    // Where a chain that died without saying so was: the click hop after the last line it printed.
    const reachedHop = () => { const last = chain.rows.at(-1)?.n ?? (chain.recheck?.ok ? 3 : 2); return CLICK_HOPS.find(n => n > last) ?? 17; };
    const promise = new Promise(resolve => {
      let done = null, stderr = '';
      child.on('error', error => { chain.stop = chain.stop || { hop: 3, why: `the pipeline could not start: ${error.message}`, answer: false }; });
      child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-800); });
      readline.createInterface({ input: child.stdout }).on('line', line => {
        const row = JSON.parse(line);
        if (row.hop === 'done') done = row;
        else if (row.hop === 'stopped') chain.stop = row.stop;
        else if (row.picture) chain.picture = row.picture;
        else if (row.recheck) { chain.recheck = { ...row.recheck, tools: row.tools, read: row.read }; chain.takeRead = row.read[0]; onRow({ n: 3, word: row.recheck.ok ? 'ADAPTER' : 'MISSING', summary: row.recheck.ok ? `re-checked ${row.recheck.frames} frames by hash` : row.recheck.reason }); }
        else { chain.rows.push(row); onRow(row); }
      });
      child.on('close', code => {
        const settle = result => {
          const receipt = result.receipt || buildReceipt({ click: clicks, takeId: take, setId: prepared.set, prepared, refs, start: startRecord, chain, brain: null, command: null, pressedAgain });
          fs.writeFileSync(path.join(work, 'run-receipt.json'), JSON.stringify(receipt, null, 1) + '\n');
          resolve({ ...result, receipt, top: topLine(TABLE, receipt), work });
        };
        try {
          if (!fs.existsSync(work)) fs.mkdirSync(work, { recursive: true });
          if (!done) {
            chain.stop = chain.stop || { hop: reachedHop(), why: `the pipeline stopped, exit ${code}: ${stderr.trim().split('\n').at(-1) || 'no message'}`, answer: false };
            return settle({ ok: false, reason: chain.stop.why });
          }
          return settle(finish({ take, prepared, startRecord, chain, done, work, pressedAgain }));
        } catch (error) {
          chain.stop = { hop: 17, why: `the brain could not run on the pipeline's output: ${String(error.stderr || error.message).trim().split('\n').at(-1)}`, answer: false };
          return settle({ ok: false, reason: chain.stop.why });
        }
      });
    });
    child.stdin.on('error', () => {}); // The child may be gone already; 'close' reports that.
    child.stdin.end(JSON.stringify({ take: path.join(setDir, take), take_digest: prepared.take_digest, check_sha256: prepared.hops['4']?.wrote?.[0]?.sha256 || null,
      set: setDir, room_model: roomModel, refs: refs.tools, tools_root: path.join(outputRoot, 'tools'), work }));
    return { promise, work, cancel: () => child.kill() };
  }
  return { empty_row: start.empty_row, startup: start.startup, runClick, seat_m: seat,
    command_status: { pending: true, codec_found: fs.existsSync(path.join(refs.codec.tools, 'c12_runtime_plan.py')) && fs.existsSync(path.join(refs.codec.tools, 'c12_runtime_wire.py')) } };
}
