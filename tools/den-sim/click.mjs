// One click of the den simulation. chain.py runs the pinned tools on a prepared take, hop 10's occupancy, hop 15's person
// states and hop 17's coordinator in spot mode; then each Decision goes through decision_doc.mjs into Home's document
// (hop 18), the swing's rows come from the click's fixture report (hop 19, in feed.mjs), the dry run compiles the
// Decision's target and level (hops 21 to 23), and the click writes its run-receipt.json. Hop 24's aim check lands later,
// from the page (aimCheck). Nothing here decides: the coordinator did.
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { execFileSync, spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { EVIDENCE_S, decisionDocument } from './decision_doc.mjs';
import { AIM_TOLERANCE_DEG, TABLE, aimRow, buildReceipt, headTools, sha256 } from './receipt.mjs';
import { topLine } from './top_line.mjs';

export { aimRow };
const HERE = path.dirname(fileURLToPath(import.meta.url));
const DRY_RUN_FILES = ['tools/den-sim/dry_run.py', ...['fixture_execution.py', 'fixture_joint_execution.py', 'fixture_profile.py', 'test_fixture_profile.py']
  .map(name => `stack/services/home-context/${name}`)];
const round = (v, places = 3) => Math.round(v * 10 ** places) / 10 ** places;
const readJsonl = file => fs.existsSync(file) ? fs.readFileSync(file, 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line)) : [];
const mount = id => String(id).match(/mount-\d+|fixture-\d+/)?.[0] || id;

// The hops chain.py runs on a click, in order. A break is filed under one of these, never a hop 0.
const CLICK_HOPS = [3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 15, 17];
// What chain.py is told for a click on this take: what prepare recorded for it (frames.csv as the take's digest, take.json
// and its one check), the room model hop 14 wrote the couch into and the light model hop 16 built, and where its files go.
// Never the scene.
export function chainRequest({ take, prepared, setDir, roomModel, lightModel, refs, outputRoot, work, replies, lastAsk }) {
  const wrote = n => prepared.hops[String(n)]?.wrote || [], check = wrote(4).find(file => /\/check-\d+\.json$/.test(file.path));
  return { take: path.join(setDir, take), take_digest: prepared.take_digest, take_json_sha256: wrote(3).find(file => file.path.endsWith('/take.json'))?.sha256 || null,
    check_file: check ? path.posix.basename(check.path) : null, check_sha256: check?.sha256 || null, detector: prepared.hops['6'] || null, recorded: prepared.recorded,
    set: setDir, room_model: roomModel, light_model: lightModel || null, refs: refs.tools, tools_root: path.join(outputRoot, 'tools'), work, replies, last_ask: lastAsk };
}

function runPython(script, request) {
  return JSON.parse(execFileSync('python', ['-B', path.join(HERE, script)], { encoding: 'utf8', windowsHide: true, timeout: 30000, maxBuffer: 32 * 1024 * 1024,
    stdio: ['pipe', 'pipe', 'pipe'], input: JSON.stringify(request) }));
}
// The dry run is a display: if it fails, the swing still stands and the page says why. decision: the lit row, or null.
function fixtureCommand(take, decision, refs) {
  try {
    return runPython('dry_run.py', { take, decision, codec_tools: refs.codec.tools, codec_contracts: refs.codec.contracts });
  } catch (error) {
    return { sent: false, send_path: null, take, decision, failed: `The dry run failed: ${String(error.stderr || error.message).trim().split('\n').at(-1)}` };
  }
}
// What the panel needs from the command. Whole commands are large, and the panel polls its status.
export function commandStatus(command) {
  return { pending: false, failed: command.failed || null, lit: !!command.decision, ik_ok: !!command.ik?.ok, ik_reason: command.ik?.reason || null,
    codec_found: !!command.codec?.found, packets: command.wire?.counts.total || 0, can_oracle_ok: command.can?.oracle_ok ?? null,
    allowlist_entries: command.can?.allowlist_entries ?? null };
}

// Hops 18 and 19: every Decision through decision_doc.mjs, its documents written for the receipt, and the rows the feed plays.
function mapDecisions({ work, folder, channels, geometry, chainRows }) {
  const decisions = readJsonl(path.join(work, folder, 'decisions.jsonl')), asks = readJsonl(path.join(work, 'asks.jsonl'));
  const personStates = readJsonl(path.join(work, 'person_states.jsonl'));
  const mapped = decisions.map(decision => ({ decision, ...decisionDocument({ decision, channels, asks, personStates }) }));
  const documents = path.join(work, 'documents.jsonl');
  fs.writeFileSync(documents, mapped.map(item => JSON.stringify(item.state ?? { refused: item.reason }) + '\n').join(''));
  const file = name => ({ path: name, sha256: sha256(path.join(work, name)) }), from = n => name => ({ ...file(name), from_hop: n });
  const decided = from(17)(`${folder}/decisions.jsonl`), table = { path: 'brain-geometry.json', sha256: sha256(geometry.path),
    recorded: { kind: 'fixture table', by: 'brain.mjs at page start, as it wrote the geometry', sha256: geometry.sha256 } };
  const refused = mapped.find(item => !item.ok), last = mapped.filter(item => item.lit.length).at(-1);
  const fixtureOf = channel => channels.find(row => row.channel === channel)?.fixture_id;
  const bound = last && !last.unbound ? `reading evidence bound to ${last.state.reading_evidence.activity.request_id}, ${last.state.reading_evidence.activity.bundle_id}, `
    + `${last.state.reading_evidence.activity.track_id} and its reply` : last ? `reading evidence unbound: ${last.unbound}` : 'no lit Decision';
  const row = { n: 18, name: TABLE.hops[17].name, word: refused ? 'MISSING' : 'ADAPTER', reached: true, ran: 'ran now', tool: { path: 'tools/den-sim/feed.mjs', pin: 'HEAD' },
    tools: headTools(['tools/den-sim/decision_doc.mjs', 'tools/den-sim/feed.mjs', 'tools/den-sim/click.mjs']),
    read: [decided, from(12)('asks.jsonl'), from(15)('person_states.jsonl'), table], wrote: [file('documents.jsonl')],
    stand_ins: refused ? [] : TABLE.hops[17].stand_ins.slice(),
    summary: refused ? `decision_doc.mjs refused a Decision: ${refused.reason}.`
      : `decision_doc.mjs mapped ${mapped.length} Decision${mapped.length === 1 ? '' : 's'} to Home's activity-lighting-state-1, one mapping: ${last ? `${last.lit.map(c => `${c} (${mount(fixtureOf(c))})`).join(', ')} lit at its target_m, ${bound}` : 'every spot off'}. `
        + 'The feed stamps today\'s clock on it, re-times the last Decision\'s document once a second, and keeps it lit, each document held to Home\'s validator.',
    note: refused ? refused.reason : null };
  const motion = { n: 19, name: TABLE.hops[18].name, word: 'SCRIPTED', reached: true, ran: 'ran now', tool: { path: 'tools/den-sim/feed.mjs', pin: 'HEAD' },
    summary: 'planSwing steps the beam from Home\'s read-back of the lit fixture to the Decision\'s target_m at 10 Hz, about 30 deg/s, fade 0 on the way: Home\'s follower has no motion model.',
    tools: headTools(['tools/den-sim/feed.mjs']), read: [decided], wrote: [], stand_ins: [TABLE.hops[18].stand_ins[0]] };
  const t0 = mapped[0]?.decision.ts;
  const rows = refused ? [] : mapped.map(item => ({ state: item.state, refusal: null, offset_s: item.decision.ts - t0, lit: item.lit.length ? fixtureOf(item.lit[0]) : null }));
  return { mapped, refused, last, rows, row, motion, decided, table, asks, personStates, chainRows };
}

// The lit row's command: hop 21 reads the Decision and the fixture table, 22 and 23 the dry run.
function commandRows({ command, decided, table, files, refs }) {
  const ik = command.ik, codec = command.codec || {}, contracts = refs.codec.contracts;
  const codecTools = Object.entries(refs.codec.sha256 || {}).map(([name, expected]) => {
    const actual = name === 'tools/c12_runtime_plan.py' ? codec.c12_runtime_plan_sha256 : name === 'tools/c12_runtime_wire.py' ? codec.c12_runtime_wire_sha256
      : (() => { try { return sha256(path.join(contracts, path.basename(name))); } catch { return null; } })();
    return { path: `codec ${name}`, pin: 'refs.json codec.sha256', expected, actual: actual || null };
  });
  const fromDry = [{ ...files.dryRun, from_hop: 21 }];
  return [
    { n: 21, name: TABLE.hops[20].name, word: ik?.ok ? 'ADAPTER' : 'MISSING', reached: true, ran: 'ran now', tool: { path: 'tools/den-sim/dry_run.py', pin: 'HEAD' },
      summary: ik?.ok ? `Real compile_target on a FICTIONAL profile, its pivot and basis the lit fixture's, on the Decision's target_m and level ${round(command.decision.level, 4)}.`
        : `compile_target refused: ${ik?.reason || command.failed}`,
      note: ik?.ok ? null : ik?.reason || command.failed, tools: headTools(DRY_RUN_FILES), read: [decided, table], wrote: [files.dryRun],
      stand_ins: [TABLE.hops[20].stand_ins[0]] },
    { n: 22, name: TABLE.hops[21].name, word: command.wire ? 'ADAPTER' : 'MISSING', reached: true, ran: 'ran now', tool: { path: refs.codec.tools, pin: 'refs.json codec.sha256' },
      summary: command.wire ? `${command.wire.counts.total} packets from the real codec, read by path, sealed with its public test key for a fictional device, from the Decision's aim and level. Shown, never sent.`
        : 'The codec was not found at its path, so nothing is encoded.',
      note: command.wire ? null : 'the codec was not found', tools: codecTools, read: fromDry, wrote: [], stand_ins: TABLE.hops[21].stand_ins.slice() },
    { n: 23, name: TABLE.hops[22].name, word: 'SCRIPTED', reached: true, ran: 'ran now', tool: { path: 'tools/den-sim/dry_run.py', pin: 'HEAD' },
      summary: `The Home Assistant envelope with a placeholder ieee, and two CAN A4 frames encoded here from the Decision${command.can?.oracle_ok ? ', matching the contract\'s oracle' : ''}. Display only: NOT SENT, sent false, send_path null.`,
      tools: headTools(['tools/den-sim/dry_run.py']), read: fromDry, wrote: [], stand_ins: [TABLE.hops[22].stand_ins[0]] },
  ];
}

const unit = v => { const n = Math.hypot(...v); return v.map(x => x / n); };
const angleDeg = (a, b) => Math.acos(Math.max(-1, Math.min(1, a.reduce((sum, v, i) => sum + v * b[i], 0)))) * 180 / Math.PI;
const wrapDeg = v => ((v + 180) % 360 + 360) % 360 - 180;
// Hop 24 (plan lines 636-640): at landing, two numbers in degrees. home: Home's read-back of the lit fixture, as the page
// sampled it from its real overlay: {fixture_id, display_origin_m, pan_deg, tilt_deg, target_point}.
export function aimNumbers({ target, command, home }) {
  const origin = home.display_origin_m, vec = v => Array.isArray(v) && v.length === 3 && v.every(Number.isFinite);
  if (!vec(origin) || !vec(home.target_point) || !vec(target) || ![home.pan_deg, home.tilt_deg].every(Number.isFinite) || !command?.ik?.ok) return null;
  const decision = angleDeg(unit(target.map((v, i) => v - origin[i])), unit(home.target_point.map((v, i) => v - origin[i])));
  const [pan, tilt] = command.ik.fixture_angles_deg;
  const ik = Math.max(Math.abs(wrapDeg(pan - -home.pan_deg)), Math.abs(tilt - (90 - home.tilt_deg)));
  return { decision_deg: round(decision, 4), ik_deg: round(ik, 4), tolerance_deg: AIM_TOLERANCE_DEG,
    within_tolerance: decision <= AIM_TOLERANCE_DEG && ik <= AIM_TOLERANCE_DEG, why: decision <= AIM_TOLERANCE_DEG && ik <= AIM_TOLERANCE_DEG ? null : 'Home did not follow the Decision' };
}

export function createClicks({ refs, outputRoot, runDirectory, geometry, channels }) {
  let clicks = 0;
  // One click: the chain, then its Decisions into Home's documents, then the dry run on the lit one, then the receipt.
  function finish({ take, prepared, setDir, startRecord, chain, done, work, pressedAgain }) {
    const maps = mapDecisions({ work, folder: done.decide, channels, geometry });
    const reply = done.reply;
    if (maps.refused) {
      const stop = { hop: 18, why: `decision_doc.mjs refused the Decision: ${maps.refused.reason}`, answer: false };
      const receipt = buildReceipt({ click: clicks, takeId: take, setId: prepared.set, setDir, prepared, refs, start: startRecord, chain: { ...chain, stop },
        decision: { feedRow: maps.row, motionRow: null }, command: null, pressedAgain });
      return { ok: false, reason: stop.why, receipt };
    }
    const last = maps.last, channel = last.lit[0];
    const lit = channels.find(row => row.channel === channel), place = geometry.fixtures.find(row => row.fixture_id === lit.fixture_id);
    const spot = last.state.fixtures.find(row => row.fixture_id === lit.fixture_id).spot;
    const decision = { channel, fixture_id: lit.fixture_id, aim_origin_m: place.aim_origin_m, horizontal_world_reference: place.horizontal_world_reference,
      target_m: spot.target_m, level: spot.level, cct_k: spot.cct_k, decision_id: last.decision.decision_id };
    const command = fixtureCommand(take, decision, refs);
    fs.writeFileSync(path.join(work, 'dry-run.json'), JSON.stringify(command, null, 2) + '\n');
    const files = { dryRun: { path: 'dry-run.json', sha256: sha256(path.join(work, 'dry-run.json')) } };
    const commandInfo = { rows: commandRows({ command, decided: maps.decided, table: maps.table, files, refs }) };
    const receipt = buildReceipt({ click: clicks, takeId: take, setId: prepared.set, setDir, prepared, refs, start: startRecord, chain,
      decision: { feedRow: maps.row, motionRow: maps.motion }, command: commandInfo, pressedAgain });
    // The last reply's arrival on the chain's clock, for the page's "Last reply N s old" once its validity has passed.
    const replies = maps.asks.filter(row => row.root && Number.isFinite(row.sent_ts) && Number.isFinite(row.elapsed_s)).map(row => row.sent_ts + row.elapsed_s);
    const seat = geometry.seat_m;
    const summary = { lit: true, channel, fixture_id: lit.fixture_id, target_m: spot.target_m, level: spot.level, decision_id: last.decision.decision_id,
      target_from_seat_m: round(Math.hypot(...spot.target_m.map((v, i) => v - seat[i]))), person_m: done.person_m, decision_ts: last.decision.ts,
      last_reply_at: replies.length ? Math.max(...replies) : null, evidence_s: EVIDENCE_S, decisions: maps.mapped.length };
    return { ok: true, timeline: { summary, rows: maps.rows, reply }, command, command_status: commandStatus(command), receipt, decision };
  }
  function runClick({ take, prepared, setDir, roomModel, lightModel, startRecord, pressedAgain, replies, lastAsk }, onRow) {
    const work = path.join(runDirectory, `click-${++clicks}`);
    const receiptOf = (chain, extra = {}) => buildReceipt({ click: clicks, takeId: take, setId: prepared.set, setDir, prepared, refs, start: startRecord, chain, decision: null, command: null, pressedAgain, ...extra });
    if (prepared.refusal) {
      // Prepare stopped on this take: the click shows where and why, and stops there. Nothing runs.
      fs.mkdirSync(work, { recursive: true });
      const chain = { rows: [], recheck: null, checkRecheck: null, takeRead: null, picture: null,
        stop: { hop: prepared.refusal.hop, why: `prepare stopped here: ${prepared.refusal.reason}`, answer: false } };
      const receipt = receiptOf(chain);
      fs.writeFileSync(path.join(work, 'run-receipt.json'), JSON.stringify(receipt, null, 1) + '\n');
      return { promise: Promise.resolve({ ok: false, reason: chain.stop.why, receipt, top: topLine(TABLE, receipt), work }), work, cancel: () => {} };
    }
    const child = spawn('python', ['-B', path.join(HERE, 'chain.py')], { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    const chain = { rows: [], recheck: null, checkRecheck: null, takeRead: null, picture: null, stop: null };
    // Where a chain that died without saying so was: the click hop after the last line it printed.
    const reachedHop = () => { const last = chain.rows.at(-1)?.n ?? (chain.checkRecheck?.ok ? 4 : chain.recheck?.ok ? 3 : 2); return CLICK_HOPS.find(n => n > last) ?? 17; };
    const promise = new Promise(resolve => {
      let done = null, stderr = '';
      child.on('error', error => { chain.stop = chain.stop || { hop: 3, why: `the pipeline could not start: ${error.message}`, answer: false }; });
      child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-800); });
      readline.createInterface({ input: child.stdout }).on('line', line => {
        const row = JSON.parse(line);
        if (row.hop === 'done') done = row;
        else if (row.hop === 'stopped') chain.stop = row.stop;
        else if (row.picture) chain.picture = row.picture;
        else if (row.recheck && row.n === 4) { chain.checkRecheck = { ...row.recheck, read: row.read }; onRow({ n: 4, word: row.recheck.ok ? 'ADAPTER' : 'MISSING', summary: row.recheck.ok ? 'the check prepare recorded, and no other' : row.recheck.reason }); }
        else if (row.recheck) { chain.recheck = { ...row.recheck, tools: row.tools, read: row.read }; chain.takeRead = row.read[0]; onRow({ n: 3, word: row.recheck.ok ? 'ADAPTER' : 'MISSING', summary: row.recheck.ok ? `re-checked ${row.recheck.frames} frames by hash` : row.recheck.reason }); }
        else { chain.rows.push(row); onRow(row); }
      });
      child.on('close', code => {
        const settle = result => {
          const receipt = result.receipt || receiptOf(chain);
          fs.writeFileSync(path.join(work, 'run-receipt.json'), JSON.stringify(receipt, null, 1) + '\n');
          resolve({ ...result, receipt, top: topLine(TABLE, receipt), work });
        };
        try {
          if (!fs.existsSync(work)) fs.mkdirSync(work, { recursive: true });
          if (!done) {
            chain.stop = chain.stop || { hop: reachedHop(), why: `the pipeline stopped, exit ${code}: ${stderr.trim().split('\n').at(-1) || 'no message'}`, answer: false };
            return settle({ ok: false, reason: chain.stop.why });
          }
          return settle(finish({ take, prepared, setDir, startRecord, chain, done, work, pressedAgain }));
        } catch (error) {
          chain.stop = { hop: 18, why: `the Decision could not be shown: ${String(error.stderr || error.message).trim().split('\n').at(-1)}`, answer: false };
          return settle({ ok: false, reason: chain.stop.why });
        }
      });
    });
    child.stdin.on('error', () => {}); // The child may be gone already; 'close' reports that.
    child.stdin.end(JSON.stringify(chainRequest({ take, prepared, setDir, roomModel, lightModel, refs, outputRoot, work, replies, lastAsk })));
    return { promise, work, cancel: () => child.kill() };
  }
  return { runClick };
}
