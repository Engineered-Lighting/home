// Feeds Home the coordinator's Decisions for the den simulation (hops 18 and 19). Each Decision
// came through decision_doc.mjs, one mapping, into Home's own document; this feed plays them to
// Home's real client, follower and overlay, re-timed to now, and keeps the last one lit. Home's
// follower has no motion model, so the 30 deg/s swing is scripted: documents stepping target_m at
// 10 Hz are inserted before the lit Decision's own document. Do not use sweep:true: the
// follower's next state write ends a sweep at once, and the head snaps regardless.
import Client from '../../app/src/home-lighting-brain-client.js';

export const BRAIN_BASE = Client.BASE;
const STEP_MS = 100, RATE_DEG_S = 30, STEP_DEG = RATE_DEG_S * STEP_MS / 1000;
// A Decision's reading evidence lasts 2.8 s and Home stops a spot when it expires, so the
// last Decision's document is re-timed faster than that, as if the reader keeps reading.
const HEARTBEAT_MS = 1000;
const LEVEL_START = .25, MAX_TRAVEL_M = 8;
const SCRIPTED = 'SIMULATION-scripted', LA_DIRECT = 'den_sim_la_direct', DEN_TRACK = 'den_sim_den_track';
const SOURCE_CLOCKS = ['interval_start', 'observed_at', 'available_at', 'valid_until'];
const DEG = Math.PI / 180;

const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const round = v => Math.round(v * 10000) / 10000;
const vec = v => Array.isArray(v) && v.length === 3 && v.every(Number.isFinite);
function unit(v) { const n = Math.hypot(...v); return v.map(x => x / n); }
// The inverse of aimAtTarget in equipment-virtual-fixtures.mjs.
function directionFromAim(panDeg, tiltDeg, reference) {
  const r = unit([reference[0], reference[1], 0]), right = [r[1], -r[0], 0];
  const pan = panDeg * DEG, tilt = tiltDeg * DEG, flat = Math.cos(tilt);
  return [0, 1, 2].map(i => flat * (Math.cos(pan) * r[i] + Math.sin(pan) * right[i]) - (i === 2 ? Math.sin(tilt) : 0));
}
function slerp(a, b, t) {
  const angle = Math.acos(Math.min(1, Math.max(-1, dot(a, b))));
  if (angle < 1e-6) return b.slice();
  const k = Math.sin(angle);
  return unit(a.map((v, i) => (Math.sin((1 - t) * angle) * v + Math.sin(t * angle) * b[i]) / k));
}

const validFixture = row => row && Client.FIXTURE_IDS.includes(row.fixture_id) && vec(row.display_origin_m)
  && vec(row.horizontal_world_reference) && Number.isFinite(row.pan_deg) && Number.isFinite(row.tilt_deg);

// The whole swing as data: one row per document, last row exactly on the target. fixture: Home's read-back of where the
// fixture points, which the click brought; target: the point to land on, a Decision's target_m (plan, hop 19). A swing
// with no read-back or no point is refused, never guessed.
export function planSwing(fixture, target, level) {
  if (!validFixture(fixture)) throw Error('swing_readback_invalid');
  if (!vec(target) || !Number.isFinite(level)) throw Error('swing_target_invalid');
  const origin = fixture.display_origin_m, toTarget = target.map((v, i) => v - origin[i]), reach = Math.hypot(...toTarget);
  const from = directionFromAim(fixture.pan_deg, fixture.tilt_deg, fixture.horizontal_world_reference), to = toTarget.map(v => v / reach);
  const angle = Math.acos(Math.min(1, Math.max(-1, dot(from, to)))) / DEG, steps = Math.max(1, Math.ceil(angle / STEP_DEG));
  // The beam's end travels at the target's height, never floating or under the floor.
  const drop = origin[2] - target[2];
  const reachAlong = direction => drop > 0 && direction[2] < -.05 ? Math.min(MAX_TRAVEL_M, drop / -direction[2]) : reach;
  const rows = [];
  for (let k = 0; k <= steps; k++) {
    const t = k / steps, direction = slerp(from, to, t), along = reachAlong(direction);
    rows.push({ target_m: k === steps ? target.slice() : direction.map((v, i) => round(origin[i] + v * along)),
      level: round(LEVEL_START + (level - LEVEL_START) * t) });
  }
  return { angle_deg: round(angle), rows };
}

const litFixture = row => row.lit ? row.state.fixtures.find(item => item.fixture_id === row.lit && item.spot.on) || null : null;

export function createFeed({ emptyRow, couchLabel, nowMs = () => Date.now() }) {
  const clients = new Set();
  // run: one number per click, so a page can tell this click's status from one fetched before it was pressed.
  let revision = 0, eventId = 0, docsSent = 0, current = null, held = null, timer = null, stepper = null, runs = 0;
  let phase = 'idle', take = null, swing = null, timeline = null, pipeline = { rows: [], failed: null }, refused = null, clock = null, landed = new Set();

  // What the words came from on this click: 'real' when the click used a real model's recorded reply,
  // 'fake' for a fake vLLM's, and null before any reply was used (the empty room).
  const reply = () => timeline?.reply || null;
  const words = () => ({ real: "Qwen's recorded words", fake: "a fake vLLM's scripted words" })[reply()] || 'no reply used';
  // A Decision's document labels itself ("SIMULATION · ..."): it keeps its own words after the click's.
  const own = /^SIMULATION · /;
  const labelled = why => `SIMULATION · a rendered take on the ${couchLabel} couch, recorded detector rows, ${words()} · ${why.replace(own, '')}`.slice(0, 240);
  // A Decision's document, unchanged but for its clocks and honest labels. Re-timing from the row each time means
  // repeating a row never stretches its evidence.
  function present(row, step = null) {
    const doc = structuredClone(row.state), now = nowMs() / 1000, shift = now - doc.generated_at, evidence = doc.reading_evidence;
    for (const source of [evidence.activity, evidence.pose]) if (source) for (const key of SOURCE_CLOCKS) source[key] += shift;
    for (const key of ['activity_valid_until', 'pose_valid_until']) if (evidence[key] !== null) evidence[key] += shift;
    doc.generated_at = now; doc.state_revision = ++revision;
    doc.why = labelled(step ? `${doc.why} · beam swinging, motion scripted` : doc.why);
    doc.decisions = doc.decisions.map(item => ({ ...item, at: item.at + shift, why: `SIMULATION · ${item.why.replace(own, '')}`.slice(0, 240) }));
    // A click's occupancy is the track list's (hop 10), and its activity a model's words only when a real reply was used.
    if (timeline) doc.occupancy.source = DEN_TRACK;
    doc.activity.source = reply() === 'real' ? LA_DIRECT : SCRIPTED;
    doc.inputs = { ...doc.inputs, scripted: reply() !== 'real', activity_scripted: reply() !== 'real', inference_ran: reply() === 'real' };
    if (step) {
      const lit = doc.fixtures.find(item => item.fixture_id === row.lit);
      lit.spot = { ...lit.spot, level: step.level, target_m: step.target_m, fade_s: 0 };
      lit.ambient = { ...lit.ambient, fade_s: 0 };
    }
    return doc;
  }
  // The page's stale-reply line: once the last reply's validity has passed on the chain's clock, as the feed keeps the
  // last Decision lit, how old that reply is (plan, hop 18 step 6).
  function stale() {
    const summary = timeline?.summary;
    if (!clock || !summary?.lit || !Number.isFinite(summary.last_reply_at) || !['holding', 'swinging'].includes(phase)) return null;
    const chainNow = clock.chain + (nowMs() / 1000 - clock.wall), age = chainNow - summary.last_reply_at;
    return age > summary.evidence_s ? { age_s: Math.round(age), line: `Last reply ${Math.round(age)} s old. The live system would drop this beam.` } : null;
  }
  function send(doc) {
    current = doc; docsSent++;
    const frame = `id: ${++eventId}\nevent: state\ndata: ${JSON.stringify(doc)}\n\n`;
    for (const res of clients) res.write(frame);
  }
  // Every document goes through Home's own validator first. One it refuses never reaches a client and never ends the
  // harness: the click stops with the validator's reason, and the off document stays (plan, hop 18 "Fails").
  function publish(doc) {
    const checked = Client.validateState(doc);
    if (checked.ok) { send(doc); return true; }
    stop(); refused = { reason: checked.reason, at_ms: nowMs() }; held = emptyRow; phase = 'pipeline_failed'; swing = null;
    pipeline.failed = `Home's validator refused the document (${checked.reason}), so the off document stays and all four spots stay off`;
    send(present(emptyRow));
    return false;
  }
  function show(row) { held = row; return publish(present(row)); }
  function stop() { clearTimeout(timer); clearInterval(stepper); timer = stepper = null; }
  function clear(nextPhase, nextTake) {
    stop(); phase = nextPhase; take = nextTake; swing = null; timeline = null; pipeline = { rows: [], failed: null }; refused = null; clock = null;
    show(emptyRow);
  }
  const reset = () => clear('idle', null);
  // One lit Decision: the swing from Home's read-back to its target_m, then its own document lands the beam. then: what
  // comes after it on this click. A fixture already landed on this click is already there, so it swings no further.
  function startSwing(row, fixture, then) {
    const lit = litFixture(row);
    let plan;
    try { plan = planSwing(fixture, lit.spot.target_m, lit.spot.level); } catch (error) { return fail(`the swing was refused: ${error.message}`); }
    if (landed.has(lit.fixture_id)) plan = { angle_deg: 0, rows: [plan.rows.at(-1)] };
    held = row; phase = 'swinging';
    swing = { fixture_id: lit.fixture_id, angle_deg: plan.angle_deg, steps: plan.rows.length - 1, rate_deg_s: RATE_DEG_S,
      started_at_ms: nowMs(), finished_at_ms: null, final_target_m: lit.spot.target_m };
    let k = 0;
    const step = () => {
      if (k < plan.rows.length - 1) return publish(present(row, plan.rows[k++]));
      stop(); phase = 'holding'; swing.finished_at_ms = nowMs(); landed.add(lit.fixture_id);
      const shown = show(row); // The Decision's own document lands the beam.
      if (shown) then();
      return shown;
    };
    if (step() && phase === 'swinging') stepper = setInterval(step, STEP_MS);
    return undefined;
  }
  // A click's Decisions, as decision_doc.mjs mapped them, in order, each after the last by its gap on the chain's clock.
  // played: { summary, rows: [{ state, refusal, offset_s, lit: Home's fixture id or null }], reply }; fixtures: the click's report.
  function play(played, fixtures) {
    const { rows } = played;
    const reports = Object.fromEntries(rows.filter(row => row.lit).map(row => [row.lit, fixtures.find(item => validFixture(item) && item.fixture_id === row.lit)]));
    if (Object.values(reports).some(report => !report)) return fail('A Decision lit a fixture this page has no read-back for.');
    stop(); phase = 'qualifying'; timeline = played; landed = new Set();
    clock = { wall: nowMs() / 1000, chain: rows[0]?.state.generated_at ?? 0 };
    let k = 0;
    const next = () => {
      const row = rows[k++];
      const after = () => { if (k < rows.length) timer = setTimeout(next, Math.max(0, rows[k].offset_s - row.offset_s) * 1000); };
      if (litFixture(row)) return startSwing(row, reports[row.lit], after);
      if (!show(row)) return undefined;
      if (k === rows.length) phase = 'refused';   // the last Decision lights nothing
      after();
      return undefined;
    };
    next();
    return undefined;
  }
  // 'stopped' when the pipeline answered (the detector saw nobody, nobody is reading); 'pipeline_failed' when it broke.
  // reply: what the click's words came from, when it used any, so the off document it leaves says so.
  function fail(reason, as = 'pipeline_failed', reply = null) {
    stop(); phase = as; pipeline.failed = reason;
    if (reply) { timeline = { summary: null, rows: [], reply }; show(held || emptyRow); }
  }
  // The off document is what a refused one falls back on, so Home must accept it: if not, the harness stops here, at start.
  const off = Client.validateState(present(emptyRow));
  if (!off.ok) throw Error(`the off document is refused by Home's validator: ${off.reason}`);
  revision = 0;
  reset();
  const heartbeat = setInterval(() => { if (phase !== 'swinging') show(held); }, HEARTBEAT_MS);
  return {
    begin: name => { clear('pipeline', name); return ++runs; }, hop: row => { pipeline.rows.push(row); }, play, fail, reset, current: () => current,
    attach(res) {
      res.writeHead(200, { 'Content-Type': 'text/event-stream; charset=utf-8', 'Cache-Control': 'no-store', Connection: 'keep-alive' });
      res.write('retry: 3000\n\n');
      res.write(`id: ${++eventId}\nevent: state\ndata: ${JSON.stringify(current)}\n\n`);
      clients.add(res); res.on('close', () => clients.delete(res));
    },
    readback: () => ({ phase, take, run: runs, docs_sent: docsSent, state_revision: revision, swing, step_ms: STEP_MS, rate_deg_s: RATE_DEG_S, streams: clients.size,
      live: held && { why: held.state.why, refusal: held.refusal, mode: held.state.mode }, summary: timeline?.summary || null, pipeline, refused, stale: stale() }),
    dispose() { stop(); clearInterval(heartbeat); for (const res of clients) res.end(); clients.clear(); },
  };
}
