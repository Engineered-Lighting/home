/* Deterministic tests of the body continuity probe's collector, attribution and join, on synthetic streams. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import vm from 'node:vm';
import { PROBE_SCHEMA, TAP_SCHEMA, attributeWindow, causeOf, createBodyContinuityCollector, decide, installBodyContinuityProbe,
    joinReceipts, packetRows, withheld } from './body-continuity-core.mjs';
import { main as joinMain } from './body-continuity-join.mjs';
import { gateStatus, guardRequest, parseArgs, readyToOpen, receiptName, writeReceipt } from './body-continuity-probe.mjs';

const FRAME_MS = 16;
const SERVER_TS = 1000;          // body_clock server_ts, received 5 ms after the connection started at 0

/* A minimal model of home-den-live-view.mjs's body path, driving the collector the way the page wiring does. */
function denPage({ clock = true } = {}) {
    const c = createBodyContinuityCollector();
    const conn = c.connect('body', 0);
    c.sse(conn, 'open', 2);
    if (clock) c.bodyClock(conn, SERVER_TS, 5);
    const app = { frames: 0, reason: 'unavailable', ready: null, version: 0, applied: false, seq: 0 };
    const upper = t => SERVER_TS + t / 1000 + 0.005 + t / 1e7;
    const status = () => ({ frames: app.frames, reason: app.reason });
    function packet(t, event) {
        const rec = c.bodyPacket(conn, { run_id: 'run-1', seq: ++app.seq, track_id: 'run-1-e1-7', home: 'den', ...event }, t);
        const before = status();
        if (event.status === 'clear') { app.ready = null; app.reason = event.reason; }
        else if (clock && upper(t) >= event.capture_ts && upper(t) < event.expires_ts) {
            app.ready = event; app.applied = false; app.reason = null; app.frames++;
        } else if (clock) { app.ready = null; app.reason = 'stale'; }
        c.bodyHandled(rec, before, status());
        return rec;
    }
    const sim = {
        c, conn, app, upper,
        ready(t, remainsMs = 1000) { const capture = upper(t) - (2 - remainsMs / 1000); return packet(t, { status: 'ready', capture_ts: capture, expires_ts: capture + 2 }); },
        clear(t, reason) { return packet(t, { status: 'clear', reason }); },
        people(t, tracks = [{ track_id: 'run-1-e1-7', state: 'confirmed', association_status: 'confident', association_revision: 1, current_obs_ids: ['o1'] }],
            status = 'fresh') {
            c.people({ perception_status: status, presence: tracks.length ? 'present' : status === 'fresh' ? 'empty' : 'unknown', tracks }, t);
        },
        /* One rendered frame: the app's presentation step (expiry, then apply), the engine's draw, then the rAF tick. */
        frame(t, { drawWhenCleared = false } = {}) {
            if (app.ready && upper(t) >= app.ready.expires_ts) { app.ready = null; app.reason = 'stale'; }
            if (app.ready && !app.applied) { app.version++; app.applied = true; }
            if (app.ready || drawWhenCleared) c.draw(t + 1, app.version, true);
            c.frame(t + 2, true, true);
        },
        /* Frames every FRAME_MS from `from`; each event fires at its own time, before the first frame after it. */
        run(from, to, events = {}, options = {}) {
            const queue = Object.entries(events).map(([at, fn]) => [Number(at), fn]).sort((a, b) => a[0] - b[0]);
            for (let t = from; t < to; t += FRAME_MS) {
                while (queue.length && queue[0][0] <= t) { const [at, fn] = queue.shift(); fn(at); }
                sim.frame(t, options);
            }
        },
    };
    return sim;
}
const peopleEvery = (sim, from, to, step = 400, tracks) => Object.fromEntries(
    Array.from({ length: Math.ceil((to - from) / step) }, (_, i) => [from + i * step + 3, t => sim.people(t, tracks)]));

test('a same-track scope_changed clear followed by a ready is one short producer_scope_changed interval', () => {
    const sim = denPage();
    sim.people(-100); sim.ready(-90);
    sim.run(-80, 0);
    sim.c.beginWindow(0);
    sim.run(0, 3000, { ...peopleEvery(sim, 0, 3000), 600: t => sim.ready(t), 1200: t => sim.ready(t), 1500: t => sim.clear(t, 'scope_changed'),
        1520: t => sim.ready(t), 2100: t => sim.ready(t), 2700: t => sim.ready(t) });
    const a = attributeWindow(sim.c.export(3000));
    assert.equal(a.intervals.length, 1);
    assert.equal(a.intervals[0].cause, 'producer_scope_changed');
    assert.ok(a.intervals[0].duration_ms > 0 && a.intervals[0].duration_ms <= 2 * FRAME_MS + 1, `blink ${a.intervals[0].duration_ms} ms`);
    assert.deepEqual(a.intervals[0].clears.map(c => c.reason), ['scope_changed']);
    assert.equal(a.scope_changed.clears, 1);
    assert.equal(a.scope_changed.with_hidden_frame, 1);
    assert.equal(a.decision.verdict, 'dominant');
    assert.equal(a.decision.family, 'producer_scope_changed');
    assert.ok(a.drawn_fraction.while_single_confirmed > 0.98);
    assert.equal(a.false_persistence.frames_drawn_after_clear, 0);
    assert.equal(a.acceptance.accepted, 5);   // the -90 ready came before the window
    assert.equal(a.acceptance.counter_source.frames_fallback, 5);
    assert.ok(a.acceptance.accept_to_draw_ms.max <= FRAME_MS + 1);
});

test('a lease that runs out before the next ready is lease_elapsed, and the share rule picks the dominant cause', () => {
    const sim = denPage();
    sim.people(-100); sim.ready(-90);
    sim.run(-80, 0);
    sim.c.beginWindow(0);
    // The 300 ready lapses near 1300 and the next arrives at 1800; the ambiguous clear at 2400 holds until 3300.
    sim.run(0, 3800, { ...peopleEvery(sim, 0, 3800), 300: t => sim.ready(t), 1800: t => sim.ready(t), 2400: t => sim.clear(t, 'ambiguous'),
        3300: t => sim.ready(t) });
    const a = attributeWindow(sim.c.export(3800));
    assert.deepEqual(a.intervals.map(iv => iv.cause), ['lease_elapsed', 'producer_ambiguous']);
    const [lease, ambiguous] = a.intervals;
    assert.ok(Math.abs(lease.start_ms - 1300) <= FRAME_MS * 2 && Math.abs(lease.end_ms - 1800) <= FRAME_MS * 2, JSON.stringify(lease));
    assert.ok(Math.abs(ambiguous.duration_ms - 900) <= FRAME_MS * 2);
    assert.equal(a.decision.verdict, 'dominant');
    assert.equal(a.decision.family, 'producer_ambiguous');
    assert.ok(a.decision.share >= 0.6 && a.decision.share < 0.7);
    assert.ok(a.exit.hide_after_local_deadline_ms.max <= FRAME_MS * 2);
    assert.equal(a.exit.hide_after_clear_ms.ambiguous.n, 1);
    assert.equal(a.causes.lease_elapsed.count, 1);
});

test('a ready past its lease on arrival is arrived_past_lease, and a ready with no clock is never counted as one', () => {
    const sim = denPage();
    sim.people(-100); sim.ready(-90);
    sim.run(-80, 0);
    sim.c.beginWindow(0);
    sim.run(0, 2000, { ...peopleEvery(sim, 0, 2000), 600: t => sim.ready(t), 900: t => sim.ready(t, -50), 1300: t => sim.ready(t) });
    const a = attributeWindow(sim.c.export(2000));
    assert.deepEqual(a.intervals.map(iv => iv.cause), ['arrived_past_lease']);
    assert.equal(a.acceptance.arrived_past_lease, 1);
    assert.deepEqual(a.acceptance.rejected_by_reason, { stale_on_arrival: 1 });

    // The old probe read `remains_ms <= 0` as true for null. Without a clock the lease is unknown, not past.
    const blind = denPage({ clock: false });
    const rec = blind.ready(100);
    assert.equal(rec.remains_ms, null);
    assert.equal(rec.reject, 'no_clock');
    const timeline = { packets: [{ t: 0, status: 'ready', accepted: true, deadline_upper: 500 }, { ...rec, t: 600 }], connections: [], prior_effective: null };
    assert.equal(causeOf({ s: 520, e: 900, prevLast: 500 }, timeline), 'lease_elapsed');
});

test('per-packet acceptance uses the newer status counters when present and falls back to frames', () => {
    const c = createBodyContinuityCollector();
    const conn = c.connect('body', 0);
    c.bodyClock(conn, SERVER_TS, 5);
    c.beginWindow(10);
    const ready = c.bodyPacket(conn, { status: 'ready', run_id: 'r', seq: 4, capture_ts: SERVER_TS, expires_ts: SERVER_TS + 2 }, 20);
    c.bodyHandled(ready, { frames: 3, accepted: 3, rejected: { stale: 1 }, reason: null },
        { frames: 3, accepted: 3, rejected: { stale: 1, watermark: 1 }, reopens: 2, reason: null });
    assert.equal(ready.source, 'status_counters');
    assert.equal(ready.accepted, false);
    assert.equal(ready.reject, 'watermark');
    const fallback = c.bodyPacket(conn, { status: 'ready', run_id: 'r', seq: 5, capture_ts: SERVER_TS, expires_ts: SERVER_TS + 2 }, 30);
    c.bodyHandled(fallback, { frames: 3, reason: null }, { frames: 4, reason: null });
    assert.equal(fallback.source, 'frames_fallback');
    assert.equal(fallback.accepted, true);
    const out = c.export(40);
    assert.deepEqual(out.counters_detected, { accepted: true, rejected_by_reason: true, reopens: true, accepted_not_drawn: false });
    assert.equal(out.counters_last.reopens, null);   // the fallback packet's status had no counters
    assert.deepEqual(out.counts.counter_source, { status_counters: 1, frames_fallback: 1 });
    assert.deepEqual(out.counts.rejected_ready, { watermark: 1 });
});

test('frames drawn after a processed clear or past the producer expiry are false persistence', () => {
    const sim = denPage();
    sim.people(-100); sim.ready(-90);
    sim.run(-80, 0);
    sim.c.beginWindow(0);
    sim.run(0, 500, peopleEvery(sim, 0, 500));
    sim.clear(500, 'empty');
    sim.run(500, 560, {}, { drawWhenCleared: true });
    const a = attributeWindow(sim.c.export(560));
    assert.ok(a.false_persistence.frames_drawn_after_clear >= 3);
    assert.equal(a.false_persistence.first[0].kind, 'after_clear');

    const late = denPage();
    late.people(-100); late.ready(-90, 300);
    late.c.beginWindow(0);
    // The app hides at its upper bound; a renderer that keeps drawing well past the lower bound is caught.
    for (let t = 0; t < 600; t += FRAME_MS) { late.c.draw(t + 1, 1, true); late.c.frame(t + 2, true, true); }
    const b = attributeWindow(late.c.export(600));
    assert.ok(b.false_persistence.frames_drawn_after_producer_expiry > 0);
    assert.ok(b.false_persistence.frames_drawn_after_local_deadline >= b.false_persistence.frames_drawn_after_producer_expiry);
});

test('the occupancy gate counts only fresh single-confirmed time, and same-track revision changes are counted', () => {
    const c = createBodyContinuityCollector();
    c.beginWindow(0);
    const one = rev => [{ track_id: 'a', state: 'confirmed', association_status: 'confident', association_revision: rev, current_obs_ids: ['x'] }];
    const two = [...one(2), { track_id: 'b', state: 'coasting', association_status: 'ambiguous', association_revision: 0, current_obs_ids: [] }];
    c.people({ perception_status: 'fresh', tracks: one(1) }, 0);
    c.people({ perception_status: 'fresh', tracks: one(1) }, 500);
    c.people({ perception_status: 'fresh', tracks: two }, 1000);       // a revision change on track a
    c.people({ perception_status: 'fresh', tracks: one(2) }, 1500);
    c.people({ perception_status: 'stale', tracks: [] }, 2000);
    const out = c.export(4000);
    assert.equal(out.occupancy.single_confirmed_ms, 1500);
    assert.equal(out.occupancy.tracked_ms, 2000);
    assert.equal(out.revisions.length, 1);
    assert.deepEqual(out.people[2].tracks.length, 2);
    assert.equal(out.people[2].coasting, 1);
    assert.equal(out.people[2].no_obs, 1);
    assert.equal(out.people[2].ambiguous, 1);
});

test('an SSE error is a timestamped sse_reconnect interval, and a plain client close is client_reopen', () => {
    const sim = denPage();
    sim.people(-100); sim.ready(-90);
    sim.run(-80, 0);
    sim.c.beginWindow(0);
    sim.run(0, 300, peopleEvery(sim, 0, 300));
    sim.c.sse(sim.conn, 'error', 300, 0);
    sim.c.sse(sim.conn, 'close', 300);
    sim.app.ready = null; sim.app.reason = 'unavailable';
    sim.run(300, 1000, peopleEvery(sim, 300, 1000));
    const out = sim.c.export(1000);
    assert.deepEqual(out.connections[0].errors, [300]);
    const a = attributeWindow(out);
    assert.deepEqual(a.intervals.map(iv => iv.cause), ['sse_reconnect']);
    assert.equal(causeOf({ s: 320, e: 900, prevLast: 290, body_stream: 'open' },
        { connections: [{ route: 'body', errors: [], closes: [310] }], packets: [] }), 'client_reopen');
});

test('someone appearing while nothing is drawn is an entry wait, excluded from the decision', () => {
    const sim = denPage();
    sim.clear(-200, 'empty');
    sim.c.beginWindow(0);
    sim.run(0, 1200, { ...peopleEvery(sim, 300, 1200), 800: t => sim.ready(t) });
    const a = attributeWindow(sim.c.export(1200));
    assert.deepEqual(a.intervals.map(iv => iv.cause), ['entry_wait']);
    assert.ok(Math.abs(a.intervals[0].duration_ms - 500) <= FRAME_MS * 2);
    assert.equal(a.decision.verdict, 'no_attributable_blank');
});

test('a perception stall keeps the person tracked, and its blank with the tail after it is perception_stall', () => {
    const sim = denPage();
    sim.people(-100); sim.ready(-90);
    sim.run(-80, 0);
    sim.c.beginWindow(0);
    // perceive.py idle(): no frame within 1.5 s of the last capture, so a 'stale' body clear and a 'stale' people event
    // with no tracks. Frames come back at 2003 and the next ready at 2400.
    sim.run(0, 3600, { ...peopleEvery(sim, 0, 900), 600: t => sim.ready(t), 1000: t => sim.clear(t, 'stale'),
        1005: t => sim.people(t, [], 'stale'), 1405: t => sim.people(t, [], 'stale'), ...peopleEvery(sim, 2000, 3600),
        2400: t => sim.ready(t), 3000: t => sim.ready(t) });
    const a = attributeWindow(sim.c.export(3600));
    assert.deepEqual(a.intervals.map(iv => iv.cause), ['perception_stall']);
    const [stall] = a.intervals;
    assert.ok(Math.abs(stall.start_ms - 1000) <= FRAME_MS * 2 && Math.abs(stall.end_ms - 2400) <= FRAME_MS * 2, JSON.stringify(stall));
    assert.equal(a.decision.verdict, 'dominant');
    assert.equal(a.decision.family, 'perception_stall');
    assert.match(a.decision.fix, /G12-D/);
    assert.ok(a.drawn_fraction.while_single_confirmed_excluding_truthful_hides < 0.7, JSON.stringify(a.drawn_fraction));
    assert.ok(a.occupancy.frames_stall_ms > 900);
    assert.equal(a.attribution_coverage.attributed_fraction, 1);

    // A stall past perceive.py's 3 s epoch gap ends tracking there; coming back after it is an entry wait.
    const long = denPage();
    long.people(-100); long.ready(-90);
    long.run(-80, 0);
    long.c.beginWindow(0);
    long.run(0, 5600, { ...peopleEvery(long, 0, 900), 600: t => long.ready(t), 1000: t => long.clear(t, 'stale'),
        1005: t => long.people(t, [], 'stale'), ...peopleEvery(long, 4500, 5600), 4800: t => long.ready(t) });
    const b = attributeWindow(long.c.export(5600));
    assert.deepEqual(b.intervals.map(iv => iv.cause), ['perception_stall', 'entry_wait']);
    assert.ok(Math.abs(b.intervals[0].end_ms - 3803) <= FRAME_MS * 2, JSON.stringify(b.intervals[0]));
});

test('a cause inside the hidden interval before a stall keeps its part; silence counts as a stall only on an open stream', () => {
    const sim = denPage();
    sim.people(-100); sim.ready(-90);
    sim.run(-80, 0);
    sim.c.beginWindow(0);
    sim.run(0, 3000, { ...peopleEvery(sim, 0, 900), 400: t => sim.ready(t), 500: t => sim.clear(t, 'ambiguous'),
        1205: t => sim.people(t, [], 'stale'), ...peopleEvery(sim, 2000, 3000), 2400: t => sim.ready(t) });
    const a = attributeWindow(sim.c.export(3000));
    assert.deepEqual(a.intervals.map(iv => iv.cause), ['producer_ambiguous', 'perception_stall']);
    assert.ok(Math.abs(a.intervals[0].end_ms - a.intervals[1].start_ms) < 0.01);
    assert.ok(Math.abs(a.intervals[1].start_ms - 1205) <= FRAME_MS * 2, JSON.stringify(a.intervals[1]));

    const c = createBodyContinuityCollector();
    const people = c.connect('people', 0);
    c.sse(people, 'open', 1);
    c.people({ perception_status: 'fresh', tracks: [{ track_id: 'a', state: 'confirmed' }] }, 10);
    assert.deepEqual(c.state(1000).tracking, { tracked: true, eligible: true, stall: false });
    assert.deepEqual(c.state(2000).tracking, { tracked: true, eligible: true, stall: true });
    assert.equal(readyToOpen({ tracking: c.state(2000).tracking, body_clock: true }), false);
    c.sse(people, 'error', 2100, 0);
    assert.equal(c.state(2200).tracking.tracked, false);
    c.people({ perception_status: 'fresh', tracks: [] }, 2300);
    c.people({ perception_status: 'stale', tracks: [] }, 2400);
    assert.equal(c.state(2500).tracking.tracked, false);   // no one was there before this stall
});

test('a drawn frame hidden for no seen reason is unattributed, and coverage reports the attributed fraction', () => {
    const drawn = { packets: [{ t: 0, status: 'ready', accepted: true, deadline_upper: 2000, drawn_at: 10 }], connections: [], prior_effective: null };
    assert.equal(causeOf({ s: 500, e: 600, prevLast: 490 }, drawn), 'unattributed');
    const undrawn = { ...drawn, packets: [{ t: 0, status: 'ready', accepted: true, deadline_upper: 2000 }] };
    assert.equal(causeOf({ s: 500, e: 600, prevLast: 490 }, undrawn), 'accepted_not_drawn');

    const sim = denPage();
    sim.people(-100); sim.ready(-90);
    sim.run(-80, 0);
    sim.c.beginWindow(0);
    // The page stops drawing a drawn, unexpired frame with no clear: nothing the probe saw explains it.
    sim.run(0, 2000, { ...peopleEvery(sim, 0, 2000), 600: t => sim.ready(t), 900: () => { sim.app.ready = null; }, 1300: t => sim.ready(t) });
    const a = attributeWindow(sim.c.export(2000));
    assert.deepEqual(a.intervals.map(iv => iv.cause), ['unattributed']);
    assert.equal(a.unexplained_intervals, 1);
    assert.equal(a.attribution_coverage.attributed_fraction, 0);
    assert.equal(a.attribution_coverage.meets_criterion, false);
});

test('the decision rule excludes truthful hides and entry waits and needs a 0.6 share', () => {
    assert.equal(decide({ truthful_hide: 9000, entry_wait: 500 }).verdict, 'no_attributable_blank');
    const split = decide({ lease_elapsed: 550, producer_ambiguous: 450, truthful_hide: 5000 });
    assert.equal(split.verdict, 'no_dominant_cause');
    assert.deepEqual(split.ranked.map(r => r.family), ['lease_elapsed', 'producer_ambiguous']);
    const top = decide({ lease_elapsed: 600, producer_ambiguous: 400 });
    assert.equal(top.verdict, 'dominant');
    assert.match(top.fix, /G12-D/);
});

const ORIGIN = 1_700_000_000_000, TRANSIT = 40;   // TRANSIT: browser receive minus host receive
/* A decisive browser receipt (lease_elapsed then producer_ambiguous) and the host tap over the same packets. */
function pairedReceipts() {
    const sim = denPage();
    sim.people(-100); sim.ready(-90);
    sim.run(-80, 0);
    sim.c.beginWindow(0);
    sim.run(0, 3800, { ...peopleEvery(sim, 0, 3800), 300: t => sim.ready(t), 1800: t => sim.ready(t), 2400: t => sim.clear(t, 'ambiguous'),
        3300: t => sim.ready(t) });
    const timeline = sim.c.export(3800), origin = ORIGIN, transit = TRANSIT;
    const browser = { schema: PROBE_SCHEMA, status: 'ok', decisive: true, window: { start_wall_ms: origin, end_wall_ms: origin + 3800 },
        attribution: attributeWindow(timeline, { timeOrigin: origin }), timeline: { packets: packetRows(timeline, origin) } };
    const hostBody = browser.timeline.packets.map(([wall, run_h, seq, status, reason]) => [wall - transit, run_h, seq, status, status === 'clear' ? reason : null, null]);
    const tap = { schema: TAP_SCHEMA, window: { start_wall_ms: origin - 500, end_wall_ms: origin + 4000 },
        timeline: { body: hostBody },
        coverage: { gaps: [{ cause: 'lease_elapsed', subcause: 'body_skip', duration_ms: 480, start_wall_ms: origin + 1305 - transit, end_wall_ms: origin + 1800 - transit }] },
        ambiguous: { episodes: [{ start_wall_ms: origin + 2400 - transit, end_wall_ms: origin + 3300 - transit, duration_ms: 900,
            extra_track: { class: 'far_coasting_no_obs' } }], rule: { verdict: 'insufficient_episodes' } },
        join: { frame_vs_ready: { verdict: 'body_skips_dominate' }, qwen: null } };
    return { browser, tap };
}

test('the join matches packets on run and seq, shifts host time, and splits lease_elapsed by the host sub-cause', () => {
    const { browser, tap } = pairedReceipts(), transit = TRANSIT;
    const joined = joinReceipts(browser, tap, { toleranceMs: 250 });
    assert.equal(joined.decisive, true);
    assert.equal(joined.host_overlap.fraction_of_browser_window, 1);
    assert.equal(joined.clock.matched_packets, browser.timeline.packets.length);
    assert.equal(joined.clock.browser_minus_host_ms, transit);
    assert.deepEqual(joined.intervals.map(iv => iv.subcause), ['lease_elapsed:body_skip', 'producer_ambiguous:far_coasting_no_obs']);
    assert.equal(joined.lease_elapsed_split.target, 'body_skip');
    assert.equal(joined.decision.family, 'producer_ambiguous');
    assert.equal(joined.tolerance_ms, 250);
    assert.equal(joined.host.frame_vs_ready.verdict, 'body_skips_dominate');

    const unmatched = joinReceipts(browser, { ...tap, timeline: { body: [] } });
    assert.match(unmatched.notes[0], /assumed synchronized/);
});

test('the join withholds the fix unless the browser window met its gate and the host tap overlaps it', () => {
    const { browser, tap } = pairedReceipts();
    const short = { ...browser, status: 'insufficient_occupancy', decisive: false };
    const joined = joinReceipts(short, tap);
    assert.equal(joined.decisive, false);
    assert.equal(joined.browser_status, 'insufficient_occupancy');
    assert.equal(joined.decision.verdict, 'not_decisive');
    assert.equal(joined.decision.fix, undefined);
    assert.equal(joined.decision.family, undefined);
    assert.match(joined.decision.note, /insufficient_occupancy/);
    assert.deepEqual(joined.decision.ranked.map(r => r.family), ['producer_ambiguous', 'lease_elapsed']);

    const apart = joinReceipts(browser, { ...tap, timeline: { body: [] }, window: { start_wall_ms: ORIGIN + 60000, end_wall_ms: ORIGIN + 90000 } });
    assert.equal(apart.decisive, false);
    assert.match(apart.decision.note, /no host tap over the browser window/);
    assert.ok(apart.notes.some(note => /does not overlap/.test(note)));

    const dir = mkdtempSync(join(tmpdir(), 'body-continuity-join-'));
    try {
        writeFileSync(join(dir, 'b.json'), JSON.stringify(short));
        writeFileSync(join(dir, 't.json'), JSON.stringify(tap));
        const out = join(dir, 'out'), args = ['--browser', join(dir, 'b.json'), '--tap', join(dir, 't.json'), '--out-dir', out];
        const { log, error } = console;
        console.log = console.error = () => {};
        let codes;
        try {
            codes = [joinMain(args)];
            writeFileSync(join(dir, 'b.json'), JSON.stringify(browser));
            codes.push(joinMain(args));
            writeFileSync(join(dir, 't.json'), JSON.stringify({ ...tap, schema: 'den-body-continuity-tap-1' }));   // an older tap
            codes.push(joinMain(args));
        } finally { Object.assign(console, { log, error }); }
        assert.deepEqual(codes, [3, 0, 2]);
        const written = readdirSync(out).map(name => JSON.parse(readFileSync(join(out, name), 'utf8')));
        assert.deepEqual(written.map(j => j.decision.verdict).sort(), ['dominant', 'not_decisive']);
    } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('the occupancy gate opens on fresh single-confirmed time with the body clock, and only a full window is decisive', () => {
    assert.equal(readyToOpen({ tracking: { tracked: true, eligible: true, stall: false }, body_clock: true }), true);
    assert.equal(readyToOpen({ tracking: { tracked: true, eligible: true, stall: true }, body_clock: true }), false);
    assert.equal(readyToOpen({ tracking: { tracked: true, eligible: true, stall: false }, body_clock: false }), false);
    assert.equal(readyToOpen({ tracking: { tracked: true, eligible: false, stall: false }, body_clock: true }), false);
    assert.equal(readyToOpen(null), false);
    assert.deepEqual(gateStatus({ opened: false, singleConfirmedMs: 0, minOccupiedMs: 60000, exported: false }),
        { status: 'unoccupied', decisive: false, exit_code: 3 });
    assert.deepEqual(gateStatus({ opened: true, singleConfirmedMs: 59999, minOccupiedMs: 60000, exported: true }),
        { status: 'insufficient_occupancy', decisive: false, exit_code: 3 });
    assert.deepEqual(gateStatus({ opened: true, singleConfirmedMs: 60000, minOccupiedMs: 60000, exported: true }),
        { status: 'ok', decisive: true, exit_code: 0 });
    assert.equal(gateStatus({ opened: true, singleConfirmedMs: 60000, minOccupiedMs: 60000, exported: false }).decisive, false);
    const kept = withheld(decide({ lease_elapsed: 900, producer_ambiguous: 100 }), 'not decisive (status insufficient_occupancy)');
    assert.equal(kept.verdict, 'not_decisive');
    assert.equal(kept.fix, undefined);
    assert.deepEqual(kept.ranked.map(r => r.family), ['lease_elapsed', 'producer_ambiguous']);
});

/* ---------- page wiring against a fake window ---------- */

class FakeEventSource extends EventTarget {
    static made = [];
    constructor(url) { super(); this.url = url; this.readyState = 0; FakeEventSource.made.push(this); }
    close() { this.readyState = 2; }
    emit(type, data) { const event = new Event(type); event.data = typeof data === 'string' ? data : JSON.stringify(data); this.dispatchEvent(event); }
}
function fakeWindow() {
    let now = 0;
    const frames = [];
    const win = new EventTarget();
    Object.assign(win, {
        performance: { now: () => now }, location: { href: 'https://home.example/' }, EventSource: FakeEventSource,
        HomeDenLive: { body: '/api/den-live/v1/homes/den/body', stream: '/api/den-live/v1/homes/den/stream' },
        localStorage: { items: {}, setItem(k, v) { this.items[k] = v; } },
        navigator: { mediaDevices: { getUserMedia: () => Promise.resolve('camera'), getDisplayMedia: () => Promise.resolve('screen') } },
        requestAnimationFrame: fn => frames.push(fn), setInterval: () => 1, clearInterval: () => {},
        advance(ms) { now += ms; }, flush() { const list = frames.splice(0); for (const fn of list) fn(now); },
    });
    return win;
}

test('the page wiring wraps the app body listener, times SSE opens and errors, and refuses camera and input', async () => {
    FakeEventSource.made.length = 0;
    const win = fakeWindow();
    const probe = installBodyContinuityProbe(win, createBodyContinuityCollector, { storage: { 'apartment3d.site': 'victoria' } });
    assert.equal(win.localStorage.items['apartment3d.site'], 'victoria');
    // The app, as home-den-live-view.mjs uses it: frames counts accepted readies.
    let frames = 0;
    win.HomeDenLiveView = { status: () => ({ body: { frames, reason: null, stream: 'open' } }) };
    win.advance(10);
    const source = new win.EventSource('/api/den-live/v1/homes/den/body');
    source.addEventListener('body', message => { if (JSON.parse(message.data).status === 'ready') frames++; });
    probe.collector.beginWindow(win.performance.now());
    win.advance(5); source.emit('open', '');
    win.advance(5); source.emit('body_clock', { server_ts: SERVER_TS });
    win.advance(5); source.emit('body', { status: 'ready', run_id: 'r', seq: 1, track_id: 't', capture_ts: SERVER_TS, expires_ts: SERVER_TS + 2,
        points: { count: 3, position_mm: 'AAAA' } });
    win.advance(5); source.emit('error', '');
    const people = new win.EventSource('/api/den-live/v1/homes/den/stream');
    people.emit('people', { perception_status: 'fresh', tracks: [{ track_id: 't', state: 'confirmed', xy_m: [1.25, 2.5], current_obs_ids: ['o'] }] });
    const out = probe.collector.export(win.performance.now());
    assert.equal(out.packets.length, 1);
    assert.equal(out.packets[0].accepted, true);
    assert.equal(out.packets[0].source, 'frames_fallback');
    assert.deepEqual(out.connections[0].opens, [15]);
    assert.deepEqual(out.connections[0].errors, [30]);
    const text = JSON.stringify(out);
    for (const forbidden of ['position_mm', 'AAAA', 'xy_m', '1.25', 'points']) assert.ok(!text.includes(forbidden), `export holds ${forbidden}`);
    await assert.rejects(win.navigator.mediaDevices.getUserMedia({ video: true }));
    const click = new Event('click', { cancelable: true });
    win.dispatchEvent(click);
    assert.equal(click.defaultPrevented, true);
    assert.deepEqual(probe.blocked, { input_events: 1, user_media: 1 });
    probe.stop();
    assert.equal(win.EventSource, FakeEventSource);
});

test('the page wiring reads the client continuity branch counters: rejected by key, reopens by reason, unapplied', () => {
    const win = fakeWindow();
    const probe = installBodyContinuityProbe(win, createBodyContinuityCollector, {});
    // home-den-live-view.mjs on claude/den-body-client-continuity: status().body spreads these counters.
    const counts = { received: 0, accepted: 0, applied: 0, unapplied: 1,
        rejected: { decode: 0, home: 0, pre_clock: 0, run_mismatch: 0, watermark: 0, scope: 0, before_capture: 0, past_lease: 0 },
        clears: {}, reopens: { error: 1, backlog: 0, reanchor: 2 } };
    let reason = 'unavailable';
    win.HomeDenLiveView = { status: () => ({ body: { stream: 'open', frames: counts.accepted, reason, ...counts,
        rejected: { ...counts.rejected }, reopens: { ...counts.reopens } } }) };
    const source = new win.EventSource('/api/den-live/v1/homes/den/body-v2');
    source.addEventListener('body', message => {
        const event = JSON.parse(message.data);
        counts.received++;
        if (event.seq === 3) return;                                   // a superseded connection: nothing counted
        if (event.expires_ts <= SERVER_TS + 0.5) { counts.rejected.past_lease++; reason = 'stale'; }
        else { counts.accepted++; reason = null; }
    });
    probe.collector.beginWindow(win.performance.now());
    source.emit('open', '');
    source.emit('body_clock', { server_ts: SERVER_TS });
    win.advance(10); source.emit('body', { status: 'ready', run_id: 'r', seq: 1, capture_ts: SERVER_TS, expires_ts: SERVER_TS + 2 });
    win.advance(10); source.emit('body', { status: 'ready', run_id: 'r', seq: 2, capture_ts: SERVER_TS - 2, expires_ts: SERVER_TS });
    win.advance(10); source.emit('body', { status: 'ready', run_id: 'r', seq: 3, capture_ts: SERVER_TS, expires_ts: SERVER_TS + 2 });
    const out = probe.collector.export(win.performance.now());
    assert.deepEqual(out.packets.map(p => [p.source, p.accepted, p.reject]),
        [['status_counters', true, null], ['status_counters', false, 'stale_on_arrival'], ['status_counters', false, 'uncounted']]);
    assert.deepEqual(out.counters_detected, { accepted: true, rejected_by_reason: true, reopens: true, accepted_not_drawn: true });
    assert.equal(out.counters_last.reopens, 3);
    assert.equal(out.counters_last.accepted_not_drawn, 1);
    assert.deepEqual(out.counts.rejected_ready, { stale_on_arrival: 1, uncounted: 1 });
    assert.equal(out.wrapper_ms.length, 3);
    const a = attributeWindow({ ...out, window: { start: 0, end: 30 } });
    assert.equal(a.probe_cost.body_wrapper_ms.n, 3);
    probe.stop();
});

test('the browser receipt and the host tap name the same tap schema', () => {
    const source = readFileSync(new URL('./body_continuity_tap.py', import.meta.url), 'utf8');
    assert.equal(source.match(/^SCHEMA = "([^"]+)"/m)?.[1], TAP_SCHEMA);
});

test('the serialized page script is self-contained', () => {
    const win = fakeWindow();
    const content = `(${installBodyContinuityProbe.toString()})(window, ${createBodyContinuityCollector.toString()}, ${JSON.stringify({ storage: {} })});`;
    vm.runInNewContext(content, { window: win, URL });
    assert.equal(typeof win.__denBodyProbe.collector.beginWindow, 'function');
    assert.notEqual(win.EventSource, FakeEventSource);
});

test('the read-only guard lets only same-origin GETs out and never fetches camera or media paths', () => {
    const origin = 'https://home.example';
    const at = (path, method = 'GET', resourceType = 'fetch') => guardRequest({ method, url: origin + path, resourceType }, origin);
    assert.equal(at('/api/den-live/v1/homes/den/body'), 'allow');
    assert.equal(at('/api/den-live/v1/homes/den/stream', 'GET', 'eventsource'), 'allow');
    assert.equal(at('/home-apartment-camera.jsx', 'GET', 'script'), 'allow');
    assert.equal(at('/api/den-live/v1/homes/den/coordinator/stop', 'POST'), 'non_get');
    assert.equal(at('/api/den-live/v1/homes/den/labels', 'PUT'), 'non_get');
    assert.equal(at('/api/den-live/v1/homes/den/state', 'HEAD'), 'non_get');
    assert.equal(at('/proxy/frigate/api/den/latest.jpg'), 'camera');
    assert.equal(at('/api/camera_proxy/camera.den'), 'camera');
    assert.equal(at('/live/stream.m4s', 'GET', 'media'), 'media');
    for (const path of ['/api/den/latest.jpg', '/live/den/index.m3u8', '/live/den/seg-1.m4s', '/hls/den/seg-1.ts', '/live/seg-2.ts',
        '/api/frame.jpeg', '/api/stream.mp4', '/api/stream.m3u8']) assert.equal(at(path), 'camera', path);
    assert.equal(at('/api/den-live/v1/homes/den/body-v2', 'GET', 'eventsource'), 'allow');
    assert.equal(at('/api/den-live/v1/homes/den/stream', 'GET', 'eventsource'), 'allow');
    assert.equal(guardRequest({ method: 'GET', url: 'https://elsewhere.example/x.js', resourceType: 'script' }, origin), 'cross_origin');
    assert.equal(guardRequest({ method: 'GET', url: 'data:text/plain,x', resourceType: 'fetch' }, origin), 'allow');
});

test('arguments default to a 120 s window and receipts never overwrite', () => {
    const opts = parseArgs(['--out-dir', 'x']);
    assert.equal(opts.seconds, 120);
    assert.equal(opts.label, 'desktop');
    assert.equal(parseArgs(['--out-dir', 'x', '--phone']).label, 'phone');
    assert.throws(() => parseArgs([]), /--out-dir/);
    assert.throws(() => parseArgs(['--out-dir', 'x', '--seconds', '2']), /--seconds/);
    assert.throws(() => parseArgs(['--out-dir', 'x', '--origin', 'http://example.com']), /https/);
    assert.match(receiptName(new Date('2026-11-18T05:00:50.397Z'), 'desktop', 'abcd1234'), /^body-continuity-20261118T050050Z-desktop-abcd1234\.json$/);
    const dir = mkdtempSync(join(tmpdir(), 'body-continuity-'));
    try {
        const date = new Date('2026-11-18T05:00:50Z');
        writeFileSync(join(dir, 'unrelated.json'), '{}');
        const first = writeReceipt(dir, 'desktop', { n: 1 }, date), second = writeReceipt(dir, 'desktop', { n: 2 }, date);
        assert.notEqual(first, second);
        assert.equal(readdirSync(dir).length, 3);
    } finally { rmSync(dir, { recursive: true, force: true }); }
});
