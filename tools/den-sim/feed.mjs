// Scripted activity-lighting-state-1 documents for the den simulation. This is
// not the lighting brain: no brain, model or camera runs here. The documents are
// checked by Home's real validator and read by Home's real client and follower.
// Home's follower has no motion model, so the 30 deg/s swing is scripted by
// stepping target_m at 10 Hz. Do not use sweep:true: the follower's next state
// write ends a sweep at once, and the head snaps regardless.
import Client from '../../app/src/home-lighting-brain-client.js';

export const BRAIN_BASE = Client.BASE;
const STEP_MS = 100, RATE_DEG_S = 30, STEP_DEG = RATE_DEG_S * STEP_MS / 1000, HEARTBEAT_MS = 5000, LEASE_S = 15;
const LEVEL_START = .25, LEVEL_END = .8, CCT_K = 3000, MIN_REACH_M = .35, MAX_TRAVEL_M = 8;
const SCRIPTED = 'SIMULATION-scripted';
const DEG = Math.PI / 180;
const OFF = { ambient: { radial_level: 0, down_level: 0, cct_k: 2700, fade_s: 0 } };

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

// The whole swing as data: one row per document, last row exactly on the seat.
export function planSwing(fixture, seat) {
  const origin = fixture.display_origin_m, toSeat = seat.map((v, i) => v - origin[i]), reach = Math.hypot(...toSeat);
  const from = directionFromAim(fixture.pan_deg, fixture.tilt_deg, fixture.horizontal_world_reference), to = toSeat.map(v => v / reach);
  const angle = Math.acos(Math.min(1, Math.max(-1, dot(from, to)))) / DEG, steps = Math.max(1, Math.ceil(angle / STEP_DEG));
  // The beam's end travels at seat height, never floating or under the floor.
  const drop = origin[2] - seat[2];
  const reachAlong = direction => drop > 0 && direction[2] < -.05 ? Math.min(MAX_TRAVEL_M, drop / -direction[2]) : reach;
  const rows = [];
  for (let k = 0; k <= steps; k++) {
    const t = k / steps, direction = slerp(from, to, t), along = reachAlong(direction);
    rows.push({ target_m: k === steps ? seat.slice() : direction.map((v, i) => round(origin[i] + v * along)),
      level: round(LEVEL_START + (LEVEL_END - LEVEL_START) * t) });
  }
  return { angle_deg: round(angle), rows };
}

function validFixture(row) {
  return row && Client.FIXTURE_IDS.includes(row.fixture_id) && vec(row.display_origin_m) && vec(row.horizontal_world_reference)
    && Number.isFinite(row.pan_deg) && Number.isFinite(row.tilt_deg);
}
// Scripted choice, not the brain's: the closest fixture that can reach the seat.
function nearestFixture(fixtures, seat) {
  return fixtures.filter(validFixture)
    .map(row => ({ row, reach: Math.hypot(...seat.map((v, i) => v - row.display_origin_m[i])) }))
    .filter(item => item.reach >= MIN_REACH_M)
    .sort((a, b) => a.reach - b.reach)[0]?.row || null;
}

export function createFeed({ seat, couchLabel, nowMs = () => Date.now() }) {
  const epoch = 'den-sim-' + nowMs().toString(36), clients = new Set();
  let revision = 0, eventId = 0, docsSent = 0, current = null, timer = null, stream = null;
  let content = null, decisions = [], phase = 'idle', swing = null;

  function buildDocument() {
    const at = nowMs() / 1000, lit = content.lit;
    const doc = { schema: Client.SCHEMA, home: 'victoria', room: 'den', generated_at: at, brain: 'online', mode: content.mode, why: content.why,
      decision_id: `${epoch}:${revision}`, state_revision: revision,
      occupancy: { present: !!lit, people_count: lit ? 1 : 0, zones: { sofa: !!lit, desk: false }, source: SCRIPTED },
      activity: { label: lit ? 'reading' : null, confidence: null, age_s: 0, source: SCRIPTED },
      fixtures: Client.FIXTURE_IDS.map(fixture_id => ({ fixture_id, ...OFF,
        spot: lit?.fixture_id === fixture_id ? { on: true, level: lit.level, cct_k: CCT_K, target_m: lit.target_m, fade_s: 0 }
          : { on: false, level: 0, cct_k: CCT_K, target_m: null, fade_s: 0 }, paused_until: null, pause_reason: null })),
      decisions, cost: { jev_calls_last_hour: 0, est_usd_last_hour: null },
      inputs: { scripted: true, inference_ran: false }, settings_version: 'den-sim' };
    if (lit) doc.reading_evidence = { bound: true, scripted: true, activity: { session_id: SCRIPTED, source: SCRIPTED },
      pose: { session_id: SCRIPTED, source: SCRIPTED }, activity_valid_until: at + LEASE_S, pose_valid_until: at + LEASE_S };
    return doc;
  }
  function publish() {
    const doc = buildDocument(), checked = Client.validateState(doc);
    if (!checked.ok) throw Error('scripted document refused by Home validator: ' + checked.reason);
    current = doc; docsSent++;
    const frame = `id: ${++eventId}\nevent: state\ndata: ${JSON.stringify(doc)}\n\n`;
    for (const res of clients) res.write(frame);
  }
  function decide(next, rule) {
    revision++; content = next;
    if (rule) decisions = [{ at: nowMs() / 1000, rule, why: next.why, inputs: { scripted: true }, change: null }, ...decisions].slice(0, 20);
    publish();
  }
  function stopSwing() { if (timer) clearInterval(timer); timer = null; }
  function reset() {
    stopSwing(); phase = 'idle'; swing = null; decisions = [];
    decide({ mode: 'off', why: 'SIMULATION · scripted input · room empty · all off', lit: null }, 'simulation_room_empty');
  }
  function run(body) {
    if (body?.scenario !== 'reading_on_couch' || !Array.isArray(body.fixtures)) return { ok: false, reason: 'scenario_unknown' };
    const fixture = nearestFixture(body.fixtures, seat);
    if (!fixture) return { ok: false, reason: 'no_fixture_can_reach_the_couch' };
    const plan = planSwing(fixture, seat);
    stopSwing();
    phase = 'swinging';
    swing = { fixture_id: fixture.fixture_id, angle_deg: plan.angle_deg, steps: plan.rows.length - 1, rate_deg_s: RATE_DEG_S,
      started_at_ms: nowMs(), finished_at_ms: null, final_target_m: seat.slice() };
    let k = 0;
    const why = `SIMULATION · scripted reading on the couch (${couchLabel}) · no inference ran · beam swinging`;
    const step = () => {
      const row = plan.rows[k], last = k === plan.rows.length - 1;
      const next = { mode: 'reading', why: last ? `SIMULATION · scripted reading on the couch (${couchLabel}) · no inference ran · beam on the couch` : why,
        lit: { fixture_id: fixture.fixture_id, target_m: row.target_m, level: row.level } };
      decide(next, k === 0 ? 'simulation_scripted_swing' : last ? 'simulation_scripted_hold' : null);
      if (last) { stopSwing(); phase = 'holding'; swing.finished_at_ms = nowMs(); }
      k++;
    };
    step();
    if (phase === 'swinging') timer = setInterval(step, STEP_MS);
    return { ok: true, swing };
  }
  reset();
  // The real brain pushes every 5 s; Home treats three missed pushes as offline.
  const heartbeat = setInterval(publish, HEARTBEAT_MS);
  return {
    run, reset, current: () => current,
    attach(res) {
      res.writeHead(200, { 'Content-Type': 'text/event-stream; charset=utf-8', 'Cache-Control': 'no-store', Connection: 'keep-alive' });
      res.write('retry: 3000\n\n');
      res.write(`id: ${++eventId}\nevent: state\ndata: ${JSON.stringify(current)}\n\n`);
      clients.add(res); res.on('close', () => clients.delete(res));
    },
    readback: () => ({ phase, docs_sent: docsSent, state_revision: revision, swing, step_ms: STEP_MS, rate_deg_s: RATE_DEG_S, streams: clients.size }),
    dispose() { stopSwing(); clearInterval(heartbeat); for (const res of clients) res.end(); clients.clear(); },
  };
}

// The honest list for this stage. Step 4 onward moves rows from SCRIPTED to REAL
// only when the real thing runs; a row never reads REAL because a doc says so.
export function hopList({ room, registration, couch, source_is_pinned, pack_revision, base }) {
  const S = 'SCRIPTED', M = 'MISSING', A = 'ADAPTER', R = 'REAL';
  const notRun = 'Did not run. Only the script says this.';
  return [
    { name: 'Input image', status: S, note: 'No picture is read. The scene is declared.' },
    { name: 'Frames', status: S, note: notRun },
    { name: 'Camera registration', status: M, note: 'No link from den-register to den-observe.' },
    { name: 'Detector and box', status: S, note: notRun },
    { name: 'Observations', status: S, note: notRun },
    { name: 'Tracks', status: S, note: notRun },
    { name: 'Bundles and router', status: S, note: notRun },
    { name: 'Bundle to Qwen', status: M, note: 'Not built.' },
    { name: 'Qwen reply', status: S, note: '"Reading" is typed into the script. No model ran.' },
    { name: 'Room-level need', status: S, note: notRun },
    { name: 'decide v0', status: S, note: notRun },
    { name: 'Person to aim', status: M, note: 'Does not exist. The script picks the nearest fixture.' },
    { name: 'Couch', status: couch.source === 'sofa_mark' ? R : S,
      note: couch.source === 'sofa_mark' ? 'Your private sofa mark. "On the couch" is geometry, not vision.' : 'DECLARED point. Not your sofa mark, and it may not be on your couch.' },
    { name: 'Spot decision and aim', status: S, note: 'The lighting brain did not run. The target is the seat point, not the hands.' },
    { name: 'Feed into Home', status: A, note: 'This harness speaks the brain\'s /state and /stream. Home\'s "Brain online" means this harness.' },
    { name: '30 deg/s motion', status: S, note: 'target_m stepped at 10 Hz. The follower has no motion model.' },
    { name: 'Home client, follower, overlay, beam', status: source_is_pinned ? R : A,
      note: `${source_is_pinned ? `Unchanged origin/main ${base} source.` : `app/src differs from ${base}.`} The light on the room is Home's modeled view, not measured.` },
    { name: 'Scan', status: room === 'real' ? R : S, note: room === 'real' ? `Your pack ${pack_revision}, read-only.` : 'AUTHORED ROOM, no household data.' },
    { name: 'Sockets', status: registration.source === 'export' ? R : S,
      note: registration.source === 'export' ? 'Your registration revision 3.' : 'AUTHORED SOCKETS. Their 4x4 m walls do not fit your scan.' },
    { name: 'Inverse kinematics to motor units', status: M, note: 'Not in this stage. Would use a fictional profile.' },
    { name: 'Plan and packets', status: M, note: 'Not in this stage. Nothing is encoded.' },
    { name: 'Home Assistant/ZHA envelope', status: M, note: 'No commissioned device and no sender here.' },
    { name: 'CAN A4 frames', status: M, note: 'Not in this stage. The motor allowlist is empty.' },
    { name: 'Aim verification', status: M, note: 'Waits for a gimbal. This only shows Home agrees with the feed.' },
  ];
}
