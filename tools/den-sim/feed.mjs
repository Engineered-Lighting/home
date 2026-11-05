// Feeds Home the real lighting brain's decisions for the den simulation. The brain
// ran in brain_run.py on a scripted reader. This adapter replays its timeline to
// Home's real client, follower and overlay, re-timed to now. Home's follower has no
// motion model, so the 30 deg/s swing is scripted: documents stepping target_m at
// 10 Hz are inserted before the brain's own lit state. Do not use sweep:true: the
// follower's next state write ends a sweep at once, and the head snaps regardless.
import Client from '../../app/src/home-lighting-brain-client.js';

export const BRAIN_BASE = Client.BASE;
const STEP_MS = 100, RATE_DEG_S = 30, STEP_DEG = RATE_DEG_S * STEP_MS / 1000;
// The brain's reading evidence lasts 2.8 s and Home stops a spot when it expires, so
// the hold is re-timed faster than that, as if the scripted reader keeps reading.
const HEARTBEAT_MS = 1000;
const LEVEL_START = .25, MAX_TRAVEL_M = 8;
const SCRIPTED = 'SIMULATION-scripted';
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

// The whole swing as data: one row per document, last row exactly on the target.
export function planSwing(fixture, target, level) {
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

const validFixture = row => row && Client.FIXTURE_IDS.includes(row.fixture_id) && vec(row.display_origin_m)
  && vec(row.horizontal_world_reference) && Number.isFinite(row.pan_deg) && Number.isFinite(row.tilt_deg);
const litFixture = row => row.state.fixtures.find(item => item.spot.on) || null;

export function createFeed({ timelines, couchLabel, nowMs = () => Date.now() }) {
  const clients = new Set();
  let revision = 0, eventId = 0, docsSent = 0, current = null, held = null, timer = null, stepper = null;
  let phase = 'idle', scenario = null, swing = null;

  const labelled = why => `SIMULATION · scripted reader on the ${couchLabel} couch, no inference ran · brain: ${why}`.slice(0, 240);
  // The brain's state, unchanged but for its clocks and honest labels. Re-timing from
  // the row each time means repeating a row never stretches its evidence.
  function present(row, step = null) {
    const doc = structuredClone(row.state), now = nowMs() / 1000, shift = now - doc.generated_at, evidence = doc.reading_evidence;
    for (const source of [evidence.activity, evidence.pose]) if (source) for (const key of SOURCE_CLOCKS) source[key] += shift;
    for (const key of ['activity_valid_until', 'pose_valid_until']) if (evidence[key] !== null) evidence[key] += shift;
    doc.generated_at = now; doc.state_revision = ++revision;
    doc.why = labelled(step ? `${doc.why} · beam swinging, motion scripted` : doc.why);
    doc.decisions = doc.decisions.map(item => ({ ...item, at: item.at + shift, why: `SIMULATION · ${item.why}`.slice(0, 240) }));
    doc.occupancy.source = SCRIPTED; doc.activity.source = SCRIPTED;
    doc.inputs = { ...doc.inputs, scripted: true, inference_ran: false };
    if (step) {
      const lit = doc.fixtures.find(item => item.spot.on);
      lit.spot = { ...lit.spot, level: step.level, target_m: step.target_m, fade_s: 0 };
      lit.ambient = { ...lit.ambient, fade_s: 0 };
    }
    return doc;
  }
  function publish(doc) {
    const checked = Client.validateState(doc);
    if (!checked.ok) throw Error('brain document refused by Home validator: ' + checked.reason);
    current = doc; docsSent++;
    const frame = `id: ${++eventId}\nevent: state\ndata: ${JSON.stringify(doc)}\n\n`;
    for (const res of clients) res.write(frame);
  }
  function show(row) { held = row; publish(present(row)); }
  function stop() { clearTimeout(timer); clearInterval(stepper); timer = stepper = null; }
  function reset() {
    stop(); phase = 'idle'; scenario = null; swing = null;
    show(timelines.reading_on_couch.rows[0]);
  }
  function startSwing(row, fixture) {
    const lit = litFixture(row), plan = planSwing(fixture, lit.spot.target_m, lit.spot.level);
    held = row; phase = 'swinging';
    swing = { fixture_id: lit.fixture_id, angle_deg: plan.angle_deg, steps: plan.rows.length - 1, rate_deg_s: RATE_DEG_S,
      started_at_ms: nowMs(), finished_at_ms: null, final_target_m: lit.spot.target_m };
    let k = 0;
    const step = () => {
      if (k < plan.rows.length - 1) return publish(present(row, plan.rows[k++]));
      stop(); phase = 'holding'; swing.finished_at_ms = nowMs();
      return show(row); // The brain's own lit state lands the beam.
    };
    step();
    stepper = setInterval(step, STEP_MS);
  }
  function run(body) {
    const timeline = Object.hasOwn(timelines, body?.scenario) ? timelines[body.scenario] : null;
    if (!timeline || !Array.isArray(body.fixtures)) return { ok: false, reason: 'scenario_unknown' };
    const { summary, rows } = timeline;
    const fixture = summary.lit ? body.fixtures.find(row => validFixture(row) && row.fixture_id === summary.fixture_id) : null;
    if (summary.lit && !fixture) return { ok: false, reason: 'chosen_fixture_has_no_readback' };
    stop(); phase = 'qualifying'; scenario = timeline.scenario; swing = null;
    let k = 0;
    const next = () => {
      const row = rows[k++];
      if (litFixture(row)) return startSwing(row, fixture);
      show(row);
      if (k < rows.length) timer = setTimeout(next, (rows[k].offset_s - row.offset_s) * 1000);
      else phase = 'refused';
      return undefined;
    };
    next();
    return { ok: true, fixture_id: summary.fixture_id, person_m: summary.person_m };
  }
  reset();
  const heartbeat = setInterval(() => { if (phase !== 'swinging') show(held); }, HEARTBEAT_MS);
  return {
    run, reset, current: () => current,
    attach(res) {
      res.writeHead(200, { 'Content-Type': 'text/event-stream; charset=utf-8', 'Cache-Control': 'no-store', Connection: 'keep-alive' });
      res.write('retry: 3000\n\n');
      res.write(`id: ${++eventId}\nevent: state\ndata: ${JSON.stringify(current)}\n\n`);
      clients.add(res); res.on('close', () => clients.delete(res));
    },
    readback: () => ({ phase, scenario, docs_sent: docsSent, state_revision: revision, swing, step_ms: STEP_MS, rate_deg_s: RATE_DEG_S, streams: clients.size,
      live: held && { why: held.state.why, refusal: held.refusal, mode: held.state.mode }, summary: scenario ? timelines[scenario].summary : null }),
    dispose() { stop(); clearInterval(heartbeat); for (const res of clients) res.end(); clients.clear(); },
  };
}

// The honest list for this stage. Step 5 onward moves rows from MISSING or SCRIPTED
// only when the real thing runs; a row never reads REAL because a doc says so.
export function hopList({ room, registration, couch, source_is_pinned, pack_revision, base, brain }) {
  const S = 'SCRIPTED', M = 'MISSING', A = 'ADAPTER', R = 'REAL';
  const notRun = 'Did not run. Only the script says this.';
  const secondTry = brain.invented_camera ? ' This is a second try on a geometry copy with an INVENTED CAMERA, because the first try refused.' : '';
  const command = brain.command;
  const noCommand = command.failed || command.ik_reason || 'The brain lit nothing, so nothing was compiled.';
  return [
    { name: 'Input image', status: S, note: 'No picture is read. The scene is declared.' },
    { name: 'Frames', status: S, note: notRun },
    { name: 'Camera registration', status: M, note: 'No link from den-register to den-observe.' },
    { name: 'Detector and box', status: S, note: notRun },
    { name: 'Observations', status: S, note: notRun },
    { name: 'Tracks', status: S, note: notRun },
    { name: 'Bundles and router', status: S, note: notRun },
    { name: 'Bundle to Qwen', status: M, note: 'Not built.' },
    { name: 'Qwen reply', status: S, note: 'The activity label is typed into brain_run.py. No model ran.' },
    { name: 'Room-level need', status: S, note: notRun },
    { name: 'decide v0', status: command.decide_ok ? R : M,
      note: command.decide_ok ? `Real decide v0 from ${command.decide_source}, on a scripted person state. Log only: four Hue levels, no aim. Nothing reconciles it with the brain.` : command.decide_reason || noCommand },
    { name: 'Person to aim', status: S, note: 'Joints are typed into brain_run.py. No pipeline track feeds the brain.' },
    { name: 'Activity loop and publisher', status: R, note: 'Real code under a fake clock. Its deep stack is fake, and its events never leave its process.' },
    { name: 'Couch', status: couch.source === 'sofa_mark' ? R : S,
      note: couch.source === 'sofa_mark' ? 'Your private sofa mark. "On the couch" is the brain\'s 1.5 m rule, not vision.' : 'DECLARED point. Not your sofa mark, and it may not be on your couch.' },
    { name: 'Spot decision and aim', status: R, note: `Real lighting brain under a fake clock, not the deployed one. It chooses the fixture and the hand point, on scripted input.${secondTry}` },
    { name: 'Feed into Home', status: A, note: 'This harness replays the brain\'s timeline, re-timed to now, and keeps re-timing the hold. Home\'s "Brain online" means this harness.' },
    { name: '30 deg/s motion', status: S, note: 'Docs stepping target_m at 10 Hz, inserted before the brain\'s lit state. The follower has no motion model.' },
    { name: 'Home client, follower, overlay, beam', status: source_is_pinned ? R : A,
      note: `${source_is_pinned ? `Unchanged origin/main ${base} source.` : `app/src differs from ${base}.`} The light on the room is Home's modeled view, not measured.` },
    { name: 'Scan', status: room === 'real' ? R : S, note: room === 'real' ? `Your pack ${pack_revision}, read-only.` : 'AUTHORED ROOM, no household data.' },
    { name: 'Sockets', status: registration.source === 'export' ? R : S,
      note: registration.source === 'export' ? 'Your registration revision 3.' : 'AUTHORED SOCKETS. Their 4x4 m walls do not fit your scan.' },
    { name: 'Inverse kinematics to motor units', status: command.ik_ok ? R : M,
      note: command.ik_ok ? 'Real compile_target on a FICTIONAL profile: zero, direction, ratio and limits are invented, and its pivot and basis are the virtual fixture\'s.' : noCommand },
    { name: 'Plan and packets', status: command.packets ? R : M,
      note: command.packets ? 'Real codec with its public test key, a fictional context and a raw spot duty. This codec is older than the release origin/main pins. Bytes are shown, never sent.'
        : command.lit && !command.codec_found ? 'The codec was not found at its path, so nothing is encoded.' : noCommand },
    { name: 'Home Assistant/ZHA envelope', status: M, note: 'No path exists: no commissioned device and no sender here. Display only: the fields match origin/main\'s list, and the ieee is the placeholder UNCOMMISSIONED.' },
    { name: 'CAN A4 frames', status: command.ik_ok ? A : M,
      note: command.ik_ok ? `Encoded here from the contract's byte fields${command.can_oracle_ok ? ', matching its literal oracle on this run' : ', and its oracle could not be checked'}. The firmware's encoder did not run. The motor allowlist has ${command.allowlist_entries ?? 'unknown'} entries.` : noCommand },
    { name: 'Aim verification', status: M, note: 'Waits for a gimbal. This only shows Home agrees with the brain.' },
  ];
}
