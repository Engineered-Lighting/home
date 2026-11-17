/* den-sim.js: a synthetic den, sending what den-live and its coordinator send Home, at their real rates.
 *
 * Nothing here is a real frame, position, person or room. The room is invented; "Sam" is a placeholder name.
 * Shapes follow the live contracts (den-live-people-1, den-virtual-spots-1, den-live-body-2, health.qwen) and
 * mark every field den-live does not send today with a `proposed` note (see PROPOSED below).
 *
 * The stream is a pure function of time: events(t0, t1) returns what Home would receive in [t0, t1), each with its
 * delivery time `at` (seconds on the sim clock). The story loops every LOOP_S seconds; faults are added as intervals
 * with inject(), so a timeline can replay any moment exactly.
 */
(function (root) {
'use strict';

const LOOP_S = 96;
const WALL0 = 1794600000;        // the synthetic stream's wall clock at t = 0 (s)
const MONO0 = 52000;             // and den-live's monotonic clock at t = 0 (s)
const PEOPLE_DT = 0.345;         // about 2.9 frames a second in
const SPOT_DT = 0.1;             // 10 Hz snapshots
const BODY_DT = 0.25;            // the body producer's frames
const POLL_DT = 2.0;             // Home's coordinator poll
const QWEN_VALID_S = 10;         // contract 3's expiry, from the newest still
const MIN_INTERVAL_S = 2.0;      // ask.py: the next ask no sooner than this after the last was sent
const PATIENCE_S = 10;           // the coordinator's phone and closed-book patience
const FADE_IN_S = 0.8, FADE_OUT_S = 1.2, LEVEL = 0.7, CCT_K = 2700;
const STREAM_STOPPED_S = 3;      // Home reads 3 s without an event as "stream stopped"

const PROPOSED = Object.freeze({
    'activity.facts': 'held object, book state, gaze target and posture from den-live-scene-2 (reply only today)',
    'activity.basis': "Qwen's visible_basis tokens (reply only today)",
    'activity.valid_for_s': 'how long the answer stays valid after this event (asked for on 2026-11-13)',
    'activity.capture_ts': "the newest still's capture time, for still-to-light timing",
    'activity.request_id': "the ask's request id, to match the light's subject",
    'identity': 'per-track name, guest or unknown, with a match score (face recognition, being chosen)',
    'track.seated': "contract 2's seated flag (planned); Qwen asks only about a seated person",
    'health.qwen.inflight_since': 'when the ask in flight was sent, if one is',
    'health.qwen.last_outcome': "how the newest reply ended: bound, unbound (two people, or the person changed) or failed",
});

function hash(n) {
    n = (n ^ 61) ^ (n >>> 16); n = n + (n << 3); n = n ^ (n >>> 4); n = Math.imul(n, 0x27d4eb2d); n = n ^ (n >>> 15);
    return (n >>> 0) / 4294967296;
}
const jit = (i, salt, a) => (hash(Math.floor(i) * 7919 + salt * 104729) - 0.5) * 2 * a;
const lerp = (a, b, u) => a + (b - a) * u;
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
const smooth = u => u * u * (3 - 2 * u);
const r3 = x => Math.round(x * 1000) / 1000;

/* ---- The story, in loop seconds ---- */
const CHAPTERS = Object.freeze([
    { t: 0, key: 'empty', label: 'Empty den' },
    { t: 4, key: 'enter', label: 'Sam walks in' },
    { t: 9.4, key: 'sit', label: 'Sits on the sofa' },
    { t: 20, key: 'book', label: 'Opens a book' },
    { t: 30.5, key: 'guest', label: 'A guest walks through' },
    { t: 37, key: 'reading', label: 'Reading' },
    { t: 48, key: 'phone', label: 'Picks up the phone' },
    { t: 64, key: 'dark', label: 'Room goes dark' },
    { t: 72, key: 'back', label: 'Lights back on' },
    { t: 82, key: 'leave', label: 'Stands up and leaves' },
    { t: 89, key: 'empty', label: 'Empty den' },
]);

/* The invented den: 4.2 m by 3.6 m, 2.5 m high. x right, y away from the camera's wall, z up. */
const ROOM = Object.freeze({
    size: [4.2, 3.6, 2.5],
    camera: [0.35, 0.18, 2.15],            // an invented corner camera, for the depth shell only
    seat: [1.95, 3.0],
    fixtures: [
        { id: 'F1', name: 'Spot 1', at: [0.9, 2.2, 2.46] },
        { id: 'F2', name: 'Spot 2', at: [3.5, 1.0, 2.46] },
        { id: 'F3', name: 'Spot 3', at: [2.7, 2.15, 2.46] },
        { id: 'F4', name: 'Spot 4', at: [1.9, 3.35, 2.46] },
    ],
    refusals: { F1: 'in_front_of_reader', F2: 'out_of_range', F4: 'beam_through_head' },
});

function path(points, t) {   // points: [[t, x, y], ...]
    if (t <= points[0][0]) return [points[0][1], points[0][2], 0, 0];
    for (let i = 1; i < points.length; i++) {
        const [t1, x1, y1] = points[i], [t0, x0, y0] = points[i - 1];
        if (t <= t1) {
            const u = (t - t0) / (t1 - t0), e = smooth(u);
            return [lerp(x0, x1, e), lerp(y0, y1, e), (x1 - x0) / (t1 - t0), (y1 - y0) / (t1 - t0)];
        }
    }
    const last = points[points.length - 1];
    return [last[1], last[2], 0, 0];
}
const bearing = (vx, vy) => (Math.atan2(vx, vy) * 180 / Math.PI + 360) % 360;   // clockwise from +y

const SAM_IN = [[4, 0.35, 0.3], [6.2, 1.15, 1.25], [8.4, 1.85, 2.45], [9.4, 1.95, 3.0]];
const SAM_OUT = [[82.8, 1.95, 3.0], [84.2, 1.7, 2.3], [86.4, 0.9, 1.0], [88.2, 0.3, 0.15]];
const GUEST = [[30.5, 0.35, 0.3], [32.6, 2.2, 0.9], [33.6, 3.55, 1.25], [34.8, 3.55, 1.25], [36.2, 1.6, 0.7], [37.4, 0.3, 0.15]];
const SEATED_HEADING = 196;

/* What is true in the den at loop time u (no faults). */
function story(u) {
    const people = [];
    if (u >= 4 && u < 88.2) {
        let x, y, vx = 0, vy = 0, posture = 'standing', hands = 'rest', heading = SEATED_HEADING, sitting = 0;
        if (u < 9.4) {
            [x, y, vx, vy] = path(SAM_IN, u); posture = 'walking'; heading = bearing(vx, vy);
            if (u > 8.6) { heading = lerp(heading, SEATED_HEADING, smooth(clamp((u - 8.6) / 0.8, 0, 1))); posture = 'standing'; }
        } else if (u < 82.8) {
            [x, y] = ROOM.seat; posture = 'sitting'; sitting = smooth(clamp((u - 9.4) / 0.9, 0, 1));
            if (u < 10.3) posture = 'sitting';
            hands = u >= 20 && u < 48 ? 'book' : u >= 48 && u < 76 ? 'phone' : 'rest';
            if (u >= 82) { sitting = 1 - smooth(clamp((u - 82) / 0.8, 0, 1)); posture = 'standing'; }
        } else {
            [x, y, vx, vy] = path(SAM_OUT, u); posture = 'walking'; heading = bearing(vx, vy);
        }
        const toward = faceTowardCamera(x, y, heading);
        people.push({ who: 'sam', x, y, vx, vy, heading, posture, sitting, hands,
            face: toward && (posture !== 'walking' || u > 8.2) });
    }
    if (u >= 30.5 && u < 37.4) {
        const [x, y, vx, vy] = path(GUEST, u);
        const moving = Math.hypot(vx, vy) > 0.05;
        const heading = moving ? bearing(vx, vy) : 250;
        people.push({ who: 'guest', x, y, vx, vy, heading, posture: moving ? 'walking' : 'standing', sitting: 0, hands: 'rest',
            face: faceTowardCamera(x, y, heading) });
    }
    return { people, dark: u >= 64 && u < 72 };
}
function faceTowardCamera(x, y, heading) {
    const b = heading * Math.PI / 180, fx = Math.sin(b), fy = Math.cos(b);
    const cx = ROOM.camera[0] - x, cy = ROOM.camera[1] - y, l = Math.hypot(cx, cy) || 1;
    return (fx * cx + fy * cy) / l > 0.45;
}

/* The pose the body producer would see, for the depth shell. */
function pose(person) {
    return { x: person.x, y: person.y, heading: person.heading, sitting: person.sitting, hands: person.hands,
        walking: person.posture === 'walking', speed: Math.hypot(person.vx, person.vy) };
}

/* ---- The stream ---- */
function create({ faults: initial = [] } = {}) {
    const faults = initial.slice();
    let cache = new Map();

    const inside = (kind, t) => faults.some(f => f.kind === kind && t >= f.t0 && t < f.t1);
    const anyFault = (kind, t0, t1) => faults.some(f => f.kind === kind && f.t0 < t1 && f.t1 > t0);
    const stopped = t => {   // the Stop control holds from a stop until a resume
        let state = false;
        for (const f of faults.filter(f => f.kind === 'stop').sort((a, b) => a.t0 - b.t0)) if (f.t0 <= t) state = t < f.t1;
        return state;
    };

    /* The world, faults included, at absolute time t. */
    function world(t) {
        const k = Math.floor(t / LOOP_S), u = t - k * LOOP_S;
        const base = story(u);
        const dark = base.dark || inside('dark', t);
        return { k, u, people: base.people, dark, stall: inside('stall', t), qwenDown: inside('qwen', t), stopped: stopped(t) };
    }

    /* When the last dark spell ended, before t (the tracker needs a moment to confirm the person again). */
    function lastDarkEnd(t) {
        const k = Math.floor(t / LOOP_S), u = t - k * LOOP_S;
        let end = u >= 72 ? k * LOOP_S + 72 : -Infinity;
        for (const f of faults) if (f.kind === 'dark' && f.t1 <= t) end = Math.max(end, f.t1);
        return end;
    }

    /* The camera epoch and track ids: a new epoch after each dark spell (the mode switch) and each loop. */
    function epochAt(t) {
        const k = Math.floor(t / LOOP_S);
        let epoch = 1 + k * 3;
        const u = t - k * LOOP_S;
        if (u >= 72) epoch += 1;
        for (const f of faults) if (f.kind === 'dark' && f.t1 <= t && f.t1 > k * LOOP_S) epoch += 1;
        return epoch;
    }
    function trackId(who, t) {
        const epoch = epochAt(t);
        return `r7-e${epoch}-trk-${who === 'sam' ? 3 : 4}`;
    }

    function build(k) {
        if (cache.has(k)) return cache.get(k);
        const t0 = k * LOOP_S, t1 = t0 + LOOP_S, out = [];

        /* Qwen asks: open while one confirmed track is seated, it isn't dark, and the stream runs. */
        const asks = [];
        let next = -Infinity, backoffUntil = -Infinity, failed = 0;
        for (let t = t0; t < t1; t += 0.05) {
            const w = world(t);
            const sam = w.people.find(p => p.who === 'sam');
            const seatedFor = sam && sam.posture === 'sitting' ? t - (t0 + 9.4) : -1;
            const open = !w.dark && sam && sam.posture === 'sitting' && seatedFor >= 1.0 && t - lastDarkEnd(t) >= 1.5;
            if (!open || t < next || t < backoffUntil) continue;
            const sent = t, still = sent - 0.14 - jit(sent * 20, 3, 0.03);
            const model = 1.62 + jit(sent * 20, 5, 0.22);
            const ask = { request_id: `ask-${k}-${asks.length + 1}`, sent, still, track: trackId('sam', still), epoch: epochAt(still) };
            if (w.qwenDown) {
                ask.failedAt = sent + 0.35; failed += 1; backoffUntil = ask.failedAt + 30; next = sent + MIN_INTERVAL_S;
            } else {
                ask.answered = sent + model; failed = 0;
                const seen = world(still);
                const visible = seen.people.filter(p => !seen.dark);
                const s = visible.find(p => p.who === 'sam');
                ask.facts = visible.length > 1 ? factsFor(null, true) : factsFor(s, false, still - (t0 + 9.4));
                ask.bound = visible.length === 1 && epochAt(ask.answered) === ask.epoch;
                ask.validUntil = still + QWEN_VALID_S;
                next = Math.max(sent + MIN_INTERVAL_S, ask.answered);
            }
            ask.failedInRow = failed;
            asks.push(ask);
        }

        /* People events at about 2.9 fps, each delivered about half a second after its capture. */
        const firstSeen = {};
        for (let i = Math.ceil(t0 / PEOPLE_DT); i * PEOPLE_DT < t1; i++) {
            const capture = i * PEOPLE_DT + jit(i, 1, 0.025);
            const lag = 0.4 + jit(i, 2, 0.06), transit = 0.15 + jit(i, 4, 0.03);
            const at = capture + lag + transit;
            const w = world(capture);
            if (world(at).stall) continue;
            const emitted = capture + lag;
            const tracks = [];
            if (!w.dark) {
                for (const p of w.people) {
                    const id = trackId(p.who, capture);
                    if (!(id in firstSeen)) firstSeen[id] = capture;
                    const age = capture - firstSeen[id];
                    if (age < 0.6) continue;   // confirmed only once a pose has supported it
                    const leaving = p.who === 'sam' && w.u > 87.4 || p.who === 'guest' && w.u > 36.9;
                    const track = { track_id: id, state: leaving ? 'coasting' : 'confirmed',
                        xy_m: [r3(p.x + jit(i, 11, 0.02)), r3(p.y + jit(i, 12, 0.02))],
                        sigma_m: leaving ? 0.24 : p.posture === 'walking' ? 0.12 : 0.07,
                        heading_deg: age < 1.2 ? null : Math.round(p.heading + jit(i, 13, 4)), age_s: leaving ? 0.4 : 0,
                        near: nearPhrase(p.x, p.y), service_run_id: 'r7', camera_epoch: epochAt(capture),
                        association_revision: p.who === 'sam' ? 2 : 1 };
                    const answer = currentAnswer(asks, id, emitted);
                    if (answer) track.activity = {
                        word: answer.facts.activity_hypothesis, close_up: closeUp(answer.facts), at: r3(WALL0 + answer.answered),
                        proposed: { valid_for_s: r3(answer.validUntil - emitted), capture_ts: r3(WALL0 + answer.still),
                            request_id: answer.request_id,
                            facts: { held_object: answer.facts.held_object, book_state: answer.facts.book_state,
                                gaze_target: answer.facts.gaze_target, subject_posture: answer.facts.subject_posture },
                            basis: answer.facts.visible_basis.slice() } };
                    track.proposed = { identity: identityFor(p, id, capture, t0), seated: p.posture === 'sitting' && p.sitting > 0.6,
                        posture: p.posture };
                    tracks.push(track);
                }
            }
            const sent = asks.filter(a => a.sent <= emitted && a.sent > emitted - 60);
            const replies = asks.filter(a => a.answered != null && a.answered <= emitted);
            const last = replies[replies.length - 1];
            const inflight = asks.find(a => a.sent <= emitted && (a.answered ?? a.failedAt) > emitted);
            const seated = tracks.some(tr => tr.track_id.endsWith('trk-3')) && w.people.some(p => p.who === 'sam' && p.posture === 'sitting');
            const open = !w.dark && seated;
            const failing = asks.filter(a => a.sent <= emitted).slice(-1)[0]?.failedInRow >= 3;
            out.push({ at, type: 'people', data: {
                schema: 'den-live-people-1', home: 'den', seq: i, transform_id: 'synthetic-transform',
                content_revision: 'synthetic-den-v4', capture_ts: r3(WALL0 + capture), emitted_ts: r3(WALL0 + emitted),
                mode: w.dark ? 'none' : 'rgb', camera: { status: 'ok', why: w.dark ? 'too dark' : null },
                perception_status: w.dark ? 'degraded' : 'fresh', presence: tracks.length ? 'present' : 'absent',
                health: {
                    timings_ms: {
                        fetch: { n: 30, p50: Math.round(182 + jit(i, 21, 14)), p95: Math.round(262 + jit(i, 22, 30)) },
                        detector: { n: 30, p50: Math.round(41 + jit(i, 23, 3)), p95: Math.round(63 + jit(i, 24, 6)) },
                        lift: { n: 30, p50: 6, p95: 9 }, track: { n: 30, p50: 2, p95: 4 }, publish: { n: 30, p50: 1, p95: 2 } },
                    fps_in: r3(2.9 + jit(i, 25, 0.08)), dropped_per_min: hash(i * 3) > 0.9 ? 2 : 1,
                    lag_s_median: r3(0.42 + jit(i, 26, 0.02)), transit_s_median: r3(0.15 + jit(i, 27, 0.01)),
                    qwen: { state: open ? failing ? 'unavailable' : 'asking' : 'off',
                        why: open ? failing ? "Qwen isn't answering" : null : w.dark ? 'too dark' : tracks.length ? 'no one seated' : 'no one here',
                        last_reply_at: last ? r3(WALL0 + last.answered) : null,
                        reply_s_median: replies.length ? r3(median(replies.slice(-20).map(a => a.answered - a.sent))) : null,
                        asks_per_min: sent.length, backoff_level: 0,
                        proposed: { inflight_since: inflight ? r3(WALL0 + inflight.sent) : null, last_outcome: outcome(asks, emitted) } } },
                tracks } });
        }

        /* The coordinator: pre-aim a dark spot at a seated person, light it while a close-up answer supports a task,
         * keep it through the patience after a phone answer, release on standing, losing the track, Off or Stop. */
        const spot = { level: 0, target: null, aimFrom: null, aimStart: 0, aimDur: 0, reason: 'waiting_for_tracking',
            phase: 'settled', subject: null, fadeFrom: 0, fadeStart: 0, fadeTo: 0, decision: 0, proposal: null };
        let support = null, revokedAt = null, lastDecision = null;
        for (let j = Math.ceil(t0 / SPOT_DT); j * SPOT_DT < t1; j++) {
            const t = j * SPOT_DT, w = world(t);
            const seen = world(t - 0.55);   // the coordinator works from the tracked person, about half a second behind
            const sam = !seen.dark ? seen.people.find(p => p.who === 'sam') : null;
            const tracked = sam && t - (t0 + 4) > 0.6;
            const seated = tracked && sam.posture === 'sitting';
            for (const a of asks) {
                if (a.answered == null || a.answered > t || a.answered <= t - SPOT_DT || !a.bound) continue;
                if (closeUp(a.facts)) { support = a; revokedAt = null; }
                else if (support && a.facts.activity_hypothesis !== 'unclear') revokedAt = revokedAt ?? a.answered;
            }
            if (support && (t >= support.validUntil && !revokedAt || revokedAt != null && t >= revokedAt + PATIENCE_S || !seated
                || epochAt(t) !== support.epoch)) { support = null; revokedAt = null; }
            const task = !!support && seated && !w.stopped;
            const target = sam ? readingPoint(sam) : null;
            const kind = task ? 'task' : seated ? 'pre_aim' : null;
            const wantLevel = task ? LEVEL : 0;
            if (kind && target) {
                if (!spot.target || Math.hypot(target[0] - spot.target[0], target[1] - spot.target[1]) > 0.25) {
                    spot.aimFrom = spot.target ? currentAim(spot, t) : [ROOM.fixtures[2].at[0], ROOM.fixtures[2].at[1], 0];
                    spot.target = target; spot.aimStart = t; spot.aimDur = spot.aimFrom ? 1.0 : 0;
                }
            } else if (!kind && spot.level === 0 && spot.fadeTo === 0) {
                spot.target = null;
            }
            if (wantLevel !== spot.fadeTo) {
                spot.fadeFrom = spot.level; spot.fadeTo = wantLevel;
                spot.fadeStart = Math.max(t, wantLevel > 0 ? spot.aimStart + spot.aimDur : t);
            }
            const fadeDur = spot.fadeTo > spot.fadeFrom ? FADE_IN_S : FADE_OUT_S;
            spot.level = t < spot.fadeStart ? spot.fadeFrom : lerp(spot.fadeFrom, spot.fadeTo, smooth(clamp((t - spot.fadeStart) / fadeDur, 0, 1)));
            if (spot.level < 0.002) spot.level = 0;
            const moving = spot.target && t < spot.aimStart + spot.aimDur;
            const reason = w.stopped ? 'stopped' : task ? 'task' : support == null && spot.level > 0 ? 'task_released'
                : kind === 'pre_aim' ? 'pre_aim' : tracked ? 'waiting_for_task' : 'waiting_for_tracking';
            const subject = tracked ? { track_id: trackId('sam', t - 0.55), camera_epoch: epochAt(t - 0.55), association_revision: 2,
                request_id: task ? support.request_id : null } : null;
            const aim = spot.target ? currentAim(spot, t) : null;
            const fixtures = {};
            for (const f of ROOM.fixtures) {
                const mine = f.id === 'F3';
                const level = mine && !w.stopped ? r3(spot.level) : 0;
                const [pan, tilt] = mine && aim ? panTilt(f.at, aim) : [0, 0];
                fixtures[f.id] = { on: level > 0, level, cct_k: CCT_K, pan_deg: r3(pan), tilt_deg: r3(tilt),
                    target_m: mine && aim ? aim.map(r3) : null, target_basis: mine && aim ? 'reading_point' : null,
                    reason: w.stopped ? 'stopped' : mine ? reason : seated ? ROOM.refusals[f.id] : tracked ? 'waiting_for_task' : 'waiting_for_tracking',
                    motion_phase: mine && moving ? 'moving' : 'settled' };
            }
            const proposal = kind ? { kind, fixture: 'F3', refused: seated ? { ...ROOM.refusals } : {}, target_basis: 'reading_point' } : null;
            const key = JSON.stringify([proposal, subject && subject.request_id, subject && subject.track_id]);
            if (key !== lastDecision) { lastDecision = key; spot.decision += 1; spot.proposal = { decision_id: `e7-${k}-${spot.decision}`, at: t, subject, proposal }; }
            if (!world(t + 0.08).stall) out.push({ at: t + 0.08, type: 'spot', data: {
                schema: 'den-virtual-spots-1', home: 'den', epoch: 'e7', seq: j, sampled_mono: r3(MONO0 + t),
                valid_until_mono: r3(MONO0 + t + 1.5), on: true, stopped: w.stopped, stop_counter: faults.filter(f => f.kind === 'stop' && f.t0 <= t).length,
                subject, fixtures } });
            if (Math.abs(t / POLL_DT - Math.round(t / POLL_DT)) < 1e-6 && !world(t + 0.15).stall) out.push({ at: t + 0.15, type: 'coordinator', data: {
                on: true, stopped: w.stopped, decision: spot.proposal ? { version: 'decision/v3', actuation: 'virtual', ...spot.proposal } : null,
                settings: { phone_patience_s: PATIENCE_S, pre_aim: true, fade_in_s: FADE_IN_S, fade_out_s: FADE_OUT_S } } });
        }

        /* The measured body: one person (the producer follows one track), 4 frames a second, a 1 s lease. */
        for (let i = Math.ceil(t0 / BODY_DT); i * BODY_DT < t1; i++) {
            const capture = i * BODY_DT, at = capture + 0.45 + jit(i, 31, 0.05), w = world(capture);
            const sam = w.people.find(p => p.who === 'sam');
            if (world(at).stall || !sam || w.dark || capture - (t0 + 4) < 0.6) continue;
            out.push({ at, type: 'body', data: { schema: 'den-live-body-2', status: 'ready', track_id: trackId('sam', capture),
                seq: i, capture_ts: r3(WALL0 + capture), expires_ts: r3(WALL0 + capture + 1), pose: pose(sam) } });
        }

        out.sort((a, b) => a.at - b.at);
        const built = { events: out, asks };
        cache.set(k, built);
        return built;
    }

    function currentAnswer(asks, id, t) {
        let best = null;
        for (const a of asks) {
            if (a.answered == null || a.answered > t) continue;
            if (!a.bound) { if (a.answered > (best?.answered ?? -Infinity)) best = null; continue; }
            if (a.track === id && t < a.validUntil) best = a;
        }
        // den-live drops the activity once an unbound (two people) answer lands after it
        const latest = asks.filter(a => a.answered != null && a.answered <= t).slice(-1)[0];
        if (latest && !latest.bound) return null;
        return best;
    }

    function identityFor(p, id, capture, t0) {
        const u = capture - t0;
        if (p.who === 'sam') {
            const first = u >= 72 ? 73.4 : 8.8;
            if (u < first) return { label: null, kind: 'unknown', score: null, at: null, source: 'face' };
            const seenAt = Math.max(first, Math.floor((u - first) / 6) * 6 + first);
            return { label: 'Sam', kind: 'household', score: r3(0.9 + jit(Math.floor(u / 6), 41, 0.04)), at: r3(WALL0 + t0 + seenAt), source: 'face' };
        }
        if (u < 35.2) return { label: null, kind: 'unknown', score: null, at: null, source: 'face' };
        return { label: null, kind: 'guest', score: 0.31, at: r3(WALL0 + t0 + 35.2), source: 'face' };
    }

    return {
        LOOP_S, WALL0, MONO0, CHAPTERS, ROOM, PROPOSED, STREAM_STOPPED_S, QWEN_VALID_S, PATIENCE_S,
        faults,
        world,
        chapterAt(t) { const u = ((t % LOOP_S) + LOOP_S) % LOOP_S; let c = CHAPTERS[0]; for (const x of CHAPTERS) if (u >= x.t) c = x; return c; },
        events(ta, tb) {
            const out = [];
            for (let k = Math.floor(ta / LOOP_S) - 1; k <= Math.floor(tb / LOOP_S); k++) {
                if (k < 0) continue;
                for (const e of build(k).events) if (e.at >= ta && e.at < tb) out.push(e);
            }
            return out;
        },
        asks(ta, tb) {
            const out = [];
            for (let k = Math.floor(ta / LOOP_S) - 1; k <= Math.floor(tb / LOOP_S); k++) if (k >= 0) for (const a of build(k).asks) if (a.sent >= ta && a.sent < tb) out.push(a);
            return out;
        },
        inject(kind, t0, t1) {
            if (kind === 'stop') {
                const open = faults.find(f => f.kind === 'stop' && f.t1 === Infinity);
                if (open) open.t1 = t0; else faults.push({ kind, t0, t1: Infinity });
            } else faults.push({ kind, t0, t1 });
            cache = new Map();
        },
        clearFaults() { faults.length = 0; cache = new Map(); },
        stopped(t) { return faults.some(f => f.kind === 'stop' && f.t0 <= t && t < f.t1); },
    };
}

function outcome(asks, t) {
    let last = null;
    for (const a of asks) { const end = a.answered ?? a.failedAt; if (end != null && end <= t && (!last || end > (last.answered ?? last.failedAt))) last = a; }
    if (!last) return null;
    const at = r3(WALL0 + (last.answered ?? last.failedAt));
    if (last.failedAt != null) return { kind: 'failed', at };
    if (last.bound) return { kind: 'bound', at };
    return { kind: last.facts.people_visible === 'multiple' ? 'unbound_two_people' : 'unbound_person_changed', at };
}
function factsFor(p, multiple, seatedFor = 99) {
    const facts = { people_visible: 'one', subject_posture: 'sitting', held_object: 'none', book_state: 'not_visible',
        gaze_target: 'elsewhere', typing_visible: 'no', screen_facing_subject: 'not_visible', activity_hypothesis: 'resting',
        visible_basis: ['person_visible', 'seated_posture'], change_vs_prior: 'no_change' };
    if (multiple || !p) {
        for (const k of Object.keys(facts)) if (k !== 'visible_basis') facts[k] = 'unclear';
        facts.people_visible = 'multiple'; facts.visible_basis = ['multiple_people_visible'];
        facts.screen_facing_subject = 'not_visible'; facts.typing_visible = 'unclear';
        return facts;
    }
    if (seatedFor < 1.6) { facts.activity_hypothesis = 'transition'; facts.visible_basis = ['person_visible', 'moving_between_frames']; return facts; }
    if (p.hands === 'book') Object.assign(facts, { held_object: 'book', book_state: 'open', gaze_target: 'book', activity_hypothesis: 'reading',
        visible_basis: ['book_in_hands', 'book_pages_open', 'head_down', 'seated_posture'] });
    else if (p.hands === 'phone') Object.assign(facts, { held_object: 'phone', gaze_target: 'phone', activity_hypothesis: 'phone_use',
        visible_basis: ['phone_in_hands', 'phone_screen_toward_face', 'head_down', 'seated_posture'] });
    else facts.visible_basis = ['person_visible', 'seated_posture', 'hands_resting', 'still_between_frames'];
    return facts;
}
const closeUp = facts => (facts.activity_hypothesis === 'reading' || facts.activity_hypothesis === 'hobby') && facts.people_visible === 'one';
function median(values) { const s = values.slice().sort((a, b) => a - b); return s.length ? s[Math.floor(s.length / 2)] : null; }
function nearPhrase(x, y) {
    if (y > 2.6 && x > 1.0 && x < 3.1) return 'on the sofa, beside the reading table';
    if (x > 3.2 && y < 1.9) return 'at the desk';
    if (x < 0.8 && y < 0.8) return 'by the door';
    return null;
}
function readingPoint(p) {
    const b = p.heading * Math.PI / 180;
    return [p.x + Math.sin(b) * 0.36, p.y + Math.cos(b) * 0.36, 0.82];
}
function currentAim(spot, t) {
    if (!spot.aimFrom || !spot.aimDur) return spot.target.slice();
    const u = smooth(clamp((t - spot.aimStart) / spot.aimDur, 0, 1));
    return spot.target.map((v, i) => lerp(spot.aimFrom[i], v, u));
}
function panTilt(from, to) {
    const dx = to[0] - from[0], dy = to[1] - from[1], dz = to[2] - from[2];
    return [Math.atan2(dx, dy) * 180 / Math.PI, Math.atan2(-dz, Math.hypot(dx, dy)) * 180 / Math.PI];
}

root.DenSim = Object.freeze({ create, LOOP_S, WALL0, MONO0, CHAPTERS, ROOM, PROPOSED, story, readingPoint });
})(typeof window !== 'undefined' ? window : globalThis);
