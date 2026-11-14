/* den-client.js: what Home knows about the den, built only from the events it receives (never from the sim's truth).
 *
 * It follows Home's own rules (app/src/home-den-live-view.mjs, home-den-live-qwen.mjs, home-den-spot-playback.mjs):
 * - 3 s without a people event reads as "stream stopped";
 * - an answer's age is the event's emitted_ts less the answer's at, plus how long ago this page got the event;
 * - an answer shows for at most 10 s after it was given, and no longer than its own valid_for_s when sent;
 * - a spot snapshot is good until its valid_until_mono; the body frame until its expires_ts.
 * With proposed fields off, the view sees exactly what den-live sends today.
 */
(function (root) {
'use strict';

const STOPPED_S = 3, ANSWER_S = 10, HISTORY_S = 150;
const ACTIVITY = Object.freeze({ reading: 'Reading', hobby: 'Close-up hobby', phone_use: 'On the phone',
    computer_work: 'At a computer', resting: 'Resting', transition: 'Settling in', other: 'Something else', unclear: 'Unclear' });
const OBJECT = Object.freeze({ book: 'Book', phone: 'Phone', cup: 'Cup', craft: 'Craft', other: 'Something', none: null, unclear: null });
const BASIS = Object.freeze({ person_visible: 'person in view', no_person_visible: 'no one in view', multiple_people_visible: 'two people in view',
    subject_partly_out_of_frame: 'partly out of view', seated_posture: 'seated', standing_posture: 'standing', lying_or_reclined: 'reclined',
    moving_between_frames: 'moving', still_between_frames: 'still', book_in_hands: 'book in hands', book_pages_open: 'pages open',
    book_closed: 'book closed', phone_in_hands: 'phone in hands', phone_screen_toward_face: 'screen toward face', cup_in_hands: 'cup in hands',
    hands_on_keyboard: 'hands on keys', hands_on_mouse: 'hand on mouse', hands_resting: 'hands resting', head_down: 'head down',
    head_toward_monitor_side: 'head toward screen', eyes_closed: 'eyes closed', low_light_or_blur: 'low light' });
const REASON = Object.freeze({
    task: 'Lit for close-up work', pre_aim: 'Aimed at you, dark, ready', waiting_for_task: 'Waiting for close-up work',
    waiting_for_tracking: 'Nobody tracked', task_released: 'Fading out: the task ended', stopped: 'Stop is holding',
    off: 'Automation is off', expired: 'Its decision expired',
    in_front_of_reader: 'In front of you', out_of_range: "Can't turn that far", beam_through_head: 'Its beam would cross a head',
    no_facing: "Can't tell which way you face", too_far: 'Too far away', route_over_180: 'The turn is over 180°',
    ambiguous_aim: 'Two ways to aim; neither is sure' });
const OUTCOME = Object.freeze({ bound: 'Answered', unbound_two_people: "Can't tell whose answer: two people in view",
    unbound_person_changed: 'Answer dropped: the person changed', failed: "Qwen didn't answer" });
const REFUSAL = new Set(['in_front_of_reader', 'out_of_range', 'beam_through_head', 'no_facing', 'too_far', 'route_over_180', 'ambiguous_aim']);

function create(sim) {
    let s;
    function reset() {
        s = { openedAt: null, people: null, peopleAt: -Infinity, peopleSeen: 0, spot: null, spotAt: -Infinity, coordinator: null,
            body: null, bodyAt: -Infinity, history: [], lastWord: new Map(), lastReply: null, replies: [], arrivals: [] };
    }
    reset();

    function feed(events, now) {
        if (s.openedAt == null) s.openedAt = now;
        for (const e of events) {
            if (e.type === 'people') {
                s.people = e.data; s.peopleAt = e.at; s.peopleSeen += 1;
                s.arrivals.push({ kind: 'people', at: e.at });
                const q = e.data.health?.qwen;
                if (q?.last_reply_at != null && q.last_reply_at !== s.lastReply) {
                    s.lastReply = q.last_reply_at;
                    const age = e.data.emitted_ts - q.last_reply_at;
                    s.replies.push({ at: e.at - Math.max(0, age), wall: q.last_reply_at });
                    s.arrivals.push({ kind: 'answer', at: e.at });
                }
                record(e);
            } else if (e.type === 'spot') {
                s.spot = e.data; s.spotAt = e.at; s.arrivals.push({ kind: 'spot', at: e.at });
                recordSpot(e);
            } else if (e.type === 'coordinator') {
                s.coordinator = e.data;
            } else if (e.type === 'body') {
                s.body = e.data; s.bodyAt = e.at; s.arrivals.push({ kind: 'body', at: e.at });
            }
        }
        const cut = now - HISTORY_S;
        while (s.history.length && s.history[0].at < cut) s.history.shift();
        while (s.arrivals.length && s.arrivals[0].at < now - 8) s.arrivals.shift();
        while (s.replies.length && s.replies[0].at < cut) s.replies.shift();
    }

    /* A compact history for timelines: one row per people event and a sparse spot row at 2 Hz. */
    function record(e) {
        const d = e.data, tr = d.tracks.find(t => t.track_id.endsWith('trk-3')) || d.tracks[0];
        s.history.push({ at: e.at, kind: 'people', count: d.tracks.length, status: d.perception_status, mode: d.mode,
            word: tr?.activity?.word ?? null, answeredAt: tr?.activity ? e.at - (d.emitted_ts - tr.activity.at) : null,
            validFor: tr?.activity?.proposed?.valid_for_s ?? null, track: tr?.track_id ?? null,
            identity: tr?.proposed?.identity ?? null, seatedish: (tr?.sigma_m ?? 1) < 0.1, qwen: d.health?.qwen?.state });
    }
    function recordSpot(e) {
        const last = s.history.findLast ? s.history.findLast(h => h.kind === 'spot') : null;
        if (last && e.at - last.at < 0.5) return;
        const f = e.data.fixtures.F3;
        s.history.push({ at: e.at, kind: 'spot', level: f.level, reason: f.reason, phase: f.motion_phase, stopped: e.data.stopped,
            request: e.data.subject?.request_id ?? null });
    }

    /* Everything a view draws, at page time now. proposed: include the fields den-live doesn't send yet. */
    function view(now, { proposed = true } = {}) {
        const sincePeople = now - s.peopleAt;
        const live = s.people && sincePeople <= STOPPED_S;
        const connecting = !s.people && s.openedAt != null && now - s.openedAt <= STOPPED_S;
        const p = s.people;
        let banner = null;
        if (!connecting && !live) banner = { kind: 'stream-stopped', words: 'Stream stopped', detail: s.people ? `no event for ${Math.floor(sincePeople)} s` : 'no events yet' };
        else if (p?.camera?.why === 'too dark') banner = { kind: 'too-dark', words: 'Too dark to see', detail: 'infrared is not on yet' };
        else if (p?.camera?.why) banner = { kind: 'camera', words: cap(p.camera.why) };
        const health = p?.health || {};
        const tracks = live ? p.tracks.map(t => track(t, p, sincePeople, proposed)) : [];
        const q = health.qwen || null;
        const qwen = q && {
            state: live ? q.state : 'unknown', why: q.why, replyMedian: q.reply_s_median, asksPerMin: q.asks_per_min,
            lastReplyAge: q.last_reply_at != null ? p.emitted_ts - q.last_reply_at + sincePeople : null,
            inflightAge: proposed && q.proposed?.inflight_since != null ? p.emitted_ts - q.proposed.inflight_since + sincePeople : null,
            lastOutcome: proposed && q.proposed?.last_outcome ? { kind: q.proposed.last_outcome.kind, age: p.emitted_ts - q.proposed.last_outcome.at + sincePeople,
                words: OUTCOME[q.proposed.last_outcome.kind] || q.proposed.last_outcome.kind } : null,
            line: qwenLine(tracks, q, live),
        };
        const sinceSpot = now - s.spotAt;
        const spotFresh = s.spot && sinceSpot <= 1.5;
        const light = s.spot && {
            fresh: spotFresh, age: sinceSpot, stopped: s.spot.stopped, subject: s.spot.subject,
            fixtures: sim.ROOM.fixtures.map(f => {
                const row = s.spot.fixtures[f.id];
                return { id: f.id, name: f.name, at: f.at, on: spotFresh && row.on, level: spotFresh ? row.level : 0, cct: row.cct_k,
                    target: row.target_m, reason: row.reason, words: REASON[row.reason] || cap(row.reason.replace(/_/g, ' ')),
                    refused: REFUSAL.has(row.reason), phase: row.motion_phase };
            }),
        };
        if (light) {
            light.lit = light.fixtures.find(f => f.on) || null;
            light.aimed = light.fixtures.find(f => f.target) || null;
            const subjectTrack = light.subject && tracks.find(t => t.id === light.subject.track_id);
            light.subjectTrack = subjectTrack || null;
            light.subjectAnswer = proposed && light.subject?.request_id && subjectTrack?.activity?.requestId === light.subject.request_id;
        }
        const bodyLive = s.body && p && (s.body.expires_ts - (p.emitted_ts + sincePeople)) > 0 && now - s.bodyAt < 1.0;
        return {
            now, live, connecting, banner, sincePeople,
            perception: !live ? 'stale' : p.perception_status,
            behind: live ? (health.lag_s_median ?? 0) + (health.transit_s_median ?? 0) : null,
            fps: health.fps_in ?? null, dropped: health.dropped_per_min ?? null,
            timings: health.timings_ms || {}, lag: health.lag_s_median, transit: health.transit_s_median,
            count: tracks.length, tracks, qwen, light,
            coordinator: s.coordinator,
            body: bodyLive ? { pose: s.body.pose, track: s.body.track_id, age: now - s.bodyAt, capture: s.body.capture_ts } : null,
            arrivals: s.arrivals, history: s.history, replies: s.replies,
        };
    }

    function track(t, p, sincePeople, proposed) {
        const a = t.activity;
        let activity = null;
        if (a && typeof a.word === 'string') {
            const age = Math.max(0, p.emitted_ts - a.at + sincePeople);
            const own = proposed && Number.isFinite(a.proposed?.valid_for_s) ? a.proposed.valid_for_s - sincePeople : Infinity;
            const left = Math.min(ANSWER_S - age, own);
            if (left > 0) {
                const facts = proposed ? a.proposed?.facts : null;
                activity = { word: a.word, words: ACTIVITY[a.word] || 'Unclear', closeUp: a.close_up === true, age, left,
                    life: Math.max(0, Math.min(1, left / ANSWER_S)), requestId: proposed ? a.proposed?.request_id : null,
                    stillAge: proposed && a.proposed?.capture_ts != null ? p.emitted_ts - a.proposed.capture_ts + sincePeople : null,
                    object: facts ? OBJECT[facts.held_object] ?? null : null, objectKey: facts?.held_object ?? null,
                    gaze: facts?.gaze_target ?? null, book: facts?.book_state ?? null, posture: facts?.subject_posture ?? null,
                    basis: proposed ? (a.proposed?.basis || []).map(k => BASIS[k] || k) : [] };
            }
        }
        const id = proposed ? t.proposed?.identity : null;
        let identity = { name: 'Someone', kind: 'unknown', strength: 'none', detail: 'not recognised yet' };
        if (!proposed) identity = { name: 'Someone', kind: 'unknown', strength: 'none', detail: 'no identity field today' };
        else if (id?.kind === 'household' && id.label) {
            const strong = id.score >= 0.8;
            identity = { name: strong ? id.label : `${id.label}?`, kind: 'household', strength: strong ? 'strong' : 'weak', score: id.score,
                age: id.at != null ? p.emitted_ts - id.at + sincePeople : null, detail: `face match ${id.score.toFixed(2)}` };
        } else if (id?.kind === 'guest') {
            identity = { name: 'Guest', kind: 'guest', strength: 'none', score: id.score,
                age: id.at != null ? p.emitted_ts - id.at + sincePeople : null, detail: 'face seen, not in the household' };
        }
        const seated = proposed ? t.proposed?.seated ?? null : null;
        return { id: t.track_id, state: t.state, xy: t.xy_m, sigma: t.sigma_m, heading: t.heading_deg, near: t.near, seated,
            posture: proposed ? ({ sitting: 'Seated', standing: 'Standing', walking: 'Walking' }[t.proposed?.posture] ?? null) : null,
            epoch: t.camera_epoch, revision: t.association_revision, identity, activity };
    }

    function qwenLine(tracks, q, live) {
        if (!live) return 'Qwen: no stream';
        const a = tracks.map(t => t.activity).filter(Boolean).sort((x, y) => x.age - y.age)[0];
        if (a) return `${a.words}${a.closeUp ? ' · close-up work' : ''} · ${Math.round(a.age)} s ago`;
        if (q.state === 'asking') return 'Asking Qwen…';
        if (q.state === 'unavailable') return `Qwen unavailable${q.why ? ` · ${q.why}` : ''}`;
        return `Qwen off${q.why ? ` · ${q.why}` : ''}`;
    }

    return { feed, view, reset, get state() { return s; } };
}

const cap = text => (text ? text[0].toUpperCase() + text.slice(1) : '');

root.DenClient = Object.freeze({ create, ACTIVITY, OBJECT, BASIS, REASON, OUTCOME, STOPPED_S, ANSWER_S });
})(typeof window !== 'undefined' ? window : globalThis);
