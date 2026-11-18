import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createSpotPlayback, scurve, localGlide, ambientOf } from '../app/src/home-den-spot-playback.mjs';

const { snapshot: lit, stopped_snapshot: stopped } = JSON.parse(readFileSync(
    new URL('./den-live/fixtures/preview-f3.json', import.meta.url), 'utf8'));
const fixture = 'F3', { binding } = lit;

// room: the registered geometry Home holds (its tracking binding and the overlay's scope), the golden fixture's by default.
function setup(room = binding) {
    let at = 1000, alarm = null, drawn = null;
    const scope = { generation: 1, binding: { content_revision: room.scan_revision },
        equipment_revision: room.equipment_revision, equipment_digest: room.equipment_digest, fixture_ids: [fixture] };
    const overlay = { coordinatorSpotScope: () => scope, applyCoordinatorSpots(rows) { drawn = rows; return true; } };
    const player = createSpotPlayback({ home: 'den', site: 'victoria', clock: () => at,
        later(callback) { alarm = callback; return 1; }, cancel() { alarm = null; } });
    player.attach(overlay);
    const connect = (epoch = lit.epoch) => {
        player.connect(at);
        player.anchor({ epoch, server_mono: 10 });
        player.tracking(room.transform_id, room.scan_revision);
    };
    connect();
    return { player, overlay, scope, connect, drawn: () => drawn, advance(ms) { at += ms; alarm?.(); } };
}

test('uses golden MOVE aim and trace, and starts dark until the registered geometry and clock match', () => {
    const { player, drawn, scope } = setup();
    player.tracking(null, null);
    player.receive(lit);
    assert.equal(drawn(), null);
    player.tracking(binding.transform_id, binding.scan_revision);
    player.receive({ ...lit, seq: 2 });
    assert.deepEqual(drawn(), [{ fixture_id: fixture, ...lit.fixtures.F3 }]);
    assert.equal(player.current().fixtures[0].keep_lit_token, 7);
    scope.generation += 1;
    player.check();
    assert.equal(drawn(), null);
    player.receive({ ...lit, seq: 2 });
    assert.equal(drawn(), null, 'a replay does not relight a rebuilt overlay');
});

test('connect time bounds a delayed snapshot and replay cannot extend the lease', () => {
    const { player, connect, drawn, advance } = setup();
    advance(1499);
    player.receive(lit);
    assert.equal(drawn()[0].on, true);
    player.receive(lit);
    advance(1);
    assert.equal(drawn(), null);
    connect();
    advance(1500);
    player.receive(lit);
    assert.equal(drawn(), null, 'the first event can already be expired at receipt');
});

test('optional timing observes validated scheduling, successful apply and clear without changing playback', () => {
    const phases = [], scope = { generation: 1, binding: { content_revision: binding.scan_revision },
        equipment_revision: binding.equipment_revision, equipment_digest: binding.equipment_digest, fixture_ids: [fixture] };
    let drawn = null;
    const player = createSpotPlayback({ home: 'den', site: 'victoria', clock: () => 1000,
        later: () => 1, cancel() {}, onPlayback(phase, value) { phases.push([phase, value?.epoch, value?.seq]); },
    });
    player.attach({ coordinatorSpotScope: () => scope, applyCoordinatorSpots: rows => { drawn = rows; return true; } });
    player.connect(1000); player.anchor({ epoch: lit.epoch, server_mono: 10 });
    player.tracking(binding.transform_id, binding.scan_revision); phases.length = 0;
    player.receive(lit);
    assert.deepEqual(phases.map(row => row[0]), ['decoded', 'scheduled', 'applied']);
    assert.ok(phases.every(row => row[1] === lit.epoch && row[2] === lit.seq));
    assert.ok(drawn[0].on); player.disconnect(); assert.equal(phases.at(-1)[0], 'clear');
    const throwing = createSpotPlayback({ home: 'den', site: 'victoria', onPlayback() { throw Error('observer'); } });
    assert.doesNotThrow(() => throwing.disconnect());
});

test('Stop survives reconnect, rejects an older counter and retains the last MOVE link', () => {
    const { player, connect, drawn } = setup();
    player.receive(lit);
    player.block('stop');
    assert.equal(drawn(), null);
    connect('preview-b');
    player.receive({ ...lit, epoch: 'preview-b' });
    assert.equal(drawn(), null);
    player.receive({ ...stopped, epoch: 'preview-b' });
    assert.equal(drawn()[0].on, false);
    assert.equal(drawn()[0].last_move_trace_id, lit.fixtures.F3.last_move_trace_id);
    player.receive({ ...lit, epoch: 'preview-b', seq: 3, stop_counter: 0 });
    assert.equal(drawn()[0].on, false);
    player.receive({ ...lit, epoch: 'preview-b', seq: 4, stop_counter: 1 });
    assert.equal(drawn()[0].on, true);
});

test('a stale Resume cannot release a newer local Stop and Off stays independent', () => {
    const { player, drawn } = setup();
    player.receive(lit);
    const earlier = player.block('stop');
    const later = player.block('stop');
    player.release('stop', earlier);
    player.receive({ ...lit, seq: 2 });
    assert.equal(player.holds().stop, later);
    assert.equal(drawn(), null);
    player.acknowledge('stop', later, { stopped: true, stop_counter: 1 });
    player.receive({ ...stopped, seq: 3 });
    player.block('off');
    player.receive({ ...stopped, seq: 4, on: false });
    player.receive({ ...stopped, seq: 5, on: false, stopped: false });
    assert.equal(drawn()[0].on, false);
    player.receive({ ...lit, seq: 6, stop_counter: 1 });
    assert.equal(drawn()[0].on, true);
});

test('wrong geometry and an invalid or incomplete snapshot clear the whole preview', () => {
    const { player, drawn } = setup();
    player.receive(lit);
    player.receive({ ...lit, seq: 2, binding: { ...binding, transform_id: 'other-camera' } });
    assert.equal(drawn(), null);
    player.receive({ ...lit, seq: 3 });
    assert.equal(drawn()[0].on, true);
    player.receive({ ...lit, seq: 4, fixtures: {} });
    assert.equal(drawn(), null);
    player.receive({ ...lit, seq: 5, fixtures: { F3: { ...lit.fixtures.F3, keep_lit_token: 9 } } });
    player.receive({ ...lit, seq: 4 });
    assert.equal(drawn(), null, 'a malformed later revision does not let an older event relight');
});

test('each applied snapshot leaves one paint stamp on the next frame: epoch, seq, lit and the paint time, at most 600', () => {
    const frames = [];
    globalThis.window = {};
    globalThis.requestAnimationFrame = callback => frames.push(callback);
    const paint = () => { while (frames.length) frames.shift()(); };
    try {
        const { player, overlay } = setup();
        player.receive(lit);
        assert.equal(window.HomeDenPaintStamps, undefined, 'the stamp waits for the frame that paints it');
        paint();
        const [first] = window.HomeDenPaintStamps;
        assert.deepEqual(Object.keys(first).sort(), ['epoch', 'lit', 'paint_unix_ms', 'seq']);
        assert.deepEqual({ ...first, paint_unix_ms: 0 }, { epoch: lit.epoch, seq: 1, lit: true, paint_unix_ms: 0 });
        assert.ok(Number.isSafeInteger(first.paint_unix_ms) && Math.abs(first.paint_unix_ms - Date.now()) < 60000);

        player.receive({ ...stopped, seq: 2 });
        paint();
        assert.deepEqual(window.HomeDenPaintStamps.map(({ seq, lit: on }) => [seq, on]), [[1, true], [2, false]],
            'a dark snapshot is stamped too, so the darken can be timed');

        player.receive({ ...lit, seq: 3, stop_counter: 1, binding: { ...binding, transform_id: 'other-camera' } });
        overlay.applyCoordinatorSpots = rows => rows === null;
        player.receive({ ...lit, seq: 4, stop_counter: 1 });
        paint();
        assert.equal(window.HomeDenPaintStamps.length, 2, 'a refused snapshot or a failed apply paints nothing');

        overlay.applyCoordinatorSpots = () => true;
        window.HomeDenPaintStamps.splice(0, Infinity, ...Array.from({ length: 600 }, (_, seq) =>
            ({ epoch: 'old', seq, lit: false, paint_unix_ms: 0 })));
        player.receive({ ...lit, seq: 5, stop_counter: 1 });
        paint();
        assert.equal(window.HomeDenPaintStamps.length, 600);
        assert.equal(window.HomeDenPaintStamps[0].seq, 1, 'the oldest stamp goes first');
        assert.equal(window.HomeDenPaintStamps.at(-1).seq, 5);
    } finally {
        delete globalThis.window;
        delete globalThis.requestAnimationFrame;
    }
});

test("den-live's current snapshot plays: a top-level subject and the v4 scan at equipment revision 3", () => {
    const v4 = { transform_id: 'synthetic-transform-v4', scan_revision: 'local-2026-10-15-den-view-r1-v4',
        equipment_revision: 3, equipment_digest: 'synthetic-equipment-r3' };
    const subject = { track_id: 'e1-trk-1', camera_epoch: 1, association_revision: 4, request_id: 'ask-1' };
    const current = { ...lit, site: 'victoria', binding: v4, subject };
    const { player, drawn } = setup(v4);
    player.receive(current);
    assert.deepEqual(drawn(), [{ fixture_id: fixture, ...lit.fixtures.F3 }]);
    assert.deepEqual(player.current().subject, subject);
    player.receive({ ...stopped, site: 'victoria', binding: v4, subject: null, seq: 2 });
    assert.equal(drawn()[0].on, false, 'a dark snapshot with no subject still plays');
    player.receive({ ...current, seq: 3, stop_counter: 1 });
    assert.equal(drawn()[0].on, true);

    for (const [seq, wrong] of [[4, { equipment_revision: 2 }], [6, { scan_revision: 'local-2026-10-15-den-view-r1-v3' }],
        [8, { equipment_digest: 'synthetic-equipment-r2' }], [10, { transform_id: 'synthetic-transform-v3' }]]) {
        player.receive({ ...current, seq, stop_counter: 1, binding: { ...v4, ...wrong } });
        assert.equal(drawn(), null, `a snapshot bound to ${JSON.stringify(wrong)} blanks the preview`);
        player.receive({ ...current, seq: seq + 1, stop_counter: 1 });
        assert.equal(drawn()[0].on, true);
    }

    const behind = setup({ ...v4, scan_revision: 'local-2026-10-15-den-view-r1-v3', equipment_revision: 2 });
    behind.player.receive(current);
    assert.equal(behind.drawn(), null, 'a Home still on the v3 room never plays a v4 snapshot');
});

/* ---- the light direction (den-live's direction.py): the glide drawn on the board's S-curve, and the room's glow ---- */

const vectors = JSON.parse(readFileSync(new URL('./den-live/fixtures/direction-scurve-vectors.json', import.meta.url), 'utf8'));
const near = (actual, expected, tolerance, message) => assert.ok(Math.abs(actual - expected) <= tolerance,
    `${message ?? ''} ${actual} vs ${expected}`);

test('Home draws the S-curve den-live plans with: the shared vectors', () => {
    assert.equal(vectors.schema, 'den-direction-scurve-vectors-2');
    assert.ok(vectors.cases.some(c => c.min_duration_s > 0), 'a glide stretched by a timed MOVE floor is among them');
    assert.ok(vectors.cases.some(c => c.limits[3] !== c.limits[1]), 'and a harder brake than start (d_max)');
    for (const { limits, duration_s: duration, samples, min_duration_s: floor } of vectors.cases) {
        if (floor > 0) near(scurve(...limits).duration, Math.max(floor, duration), 1e-8, 'the stretched predicted duration');
        const curve = scurve(...limits);
        near(curve.duration, duration, 1e-8, 'duration');
        for (const [t, s, speed] of samples) {
            near(curve.s(t), s, 1e-8, `s(${t})`);
            near(curve.speed(t), speed, 1e-7, `speed(${t})`);
        }
    }
    assert.throws(() => scurve(0, 1, 1), /scurve_limits/);
});

test('a moving row reaches the overlay with its glide on Home\'s clock, and the room glow with it', () => {
    let at = 5000, rows = null, options = null;
    const scope = { generation: 1, binding: { content_revision: binding.scan_revision },
        equipment_revision: binding.equipment_revision, equipment_digest: binding.equipment_digest, fixture_ids: [fixture] };
    const player = createSpotPlayback({ home: 'den', site: lit.site, clock: () => at, later: () => 1, cancel() {} });
    player.attach({ coordinatorSpotScope: () => scope,
        applyCoordinatorSpots(next, _generation, extra) { rows = next; options = extra; return true; } });
    player.connect(at);
    player.anchor({ epoch: lit.epoch, server_mono: lit.sampled_mono });
    player.tracking(binding.transform_id, binding.scan_revision);
    const { limits, duration_s } = vectors.cases[1];
    const glide = { from_deg: [-10, 20], to_deg: [-14.6, 21.2], start_mono: lit.sampled_mono - .2, duration_s, limits,
        shape: 'scurve-7' };
    at += 40;   // this snapshot's transit
    player.receive({ ...lit, seq: 20, ambient: { level: .3, scale: .4, cct_k: 2700 },
        fixtures: { [fixture]: { ...lit.fixtures[fixture], motion_phase: 'moving', glide } } });
    const drawn = rows[0].glide_local;
    near(drawn.startMs, at - 200, 1e-6, 'started 0.2 s before the sample, on Home\'s clock');
    assert.deepEqual(drawn.at(drawn.startMs), [-10, 20]);
    assert.deepEqual(drawn.at(drawn.endMs), [-14.6, 21.2]);
    const middle = drawn.at((drawn.startMs + drawn.endMs) / 2);
    near(middle[0], -12.3, 1e-9, 'half way along the symmetric curve');
    assert.deepEqual(options.ambient, { level: .3, cct_k: 2700 });
    at += 100;
    player.receive({ ...lit, seq: 21 });
    assert.equal(rows[0].glide_local, undefined, 'a settled row has no glide');
    assert.equal(options.ambient, undefined, 'an older producer sends no ambient: the room is left alone');
    player.receive({ ...lit, seq: 22, ambient: null });
    assert.equal(options.ambient, null, 'Off: the coordinator directs no room light');
    assert.equal(ambientOf({ level: 2, cct_k: 2700 }), null);
    assert.deepEqual(ambientOf({ level: .5, cct_k: 1000 }), { level: .5, cct_k: 2200 });
    assert.equal(localGlide({ ...glide, shape: 'linear' }, 0), null, 'only the board\'s own curve is drawn');
});

// A person's settled change, as the overlay reports it (equipment-virtual-fixtures.mjs onHandChange).
function handSetup({ answer = handAnswer, withPost = true, protocol = 2 } = {}) {
    let at = 1000, listener = null, ids = 0;
    const posts = [], released = [], queued = [], holds = new Map(), restored = [];
    const scope = { generation: 1, binding: { content_revision: binding.scan_revision },
        equipment_revision: binding.equipment_revision, equipment_digest: binding.equipment_digest, fixture_ids: [fixture] };
    const overlay = { coordinatorSpotScope: () => scope, applyCoordinatorSpots: () => true,
        onVirtualFixtureHand(fn) { listener = fn; return () => { listener = null; }; },
        virtualFixtureHandHolds: () => [...holds.values()],
        restoreVirtualFixtureHand(id, hand) { restored.push({ id, hand }); holds.set(id, { fixture_id: id, reason: 'restored' }); return true; },
        releaseVirtualFixtureHand(id) { released.push(id); holds.delete(id); return [id]; },
        releaseVirtualFixtureHands() { const all = [...holds.keys()]; holds.clear(); return all; } };
    const post = withPost ? async (path, body) => { posts.push({ path, body: structuredClone(body) }); return answer(path, body, posts.length); } : null;
    const player = createSpotPlayback({ home: 'den', site: 'victoria', clock: () => at, post, clientId: 'page-a', makeEventId: () => `home-${++ids}`,
        later(callback) { queued.push(callback); return queued.length; }, cancel() {} });
    player.attach(overlay);
    player.connect(at);
    player.anchor({ epoch: lit.epoch, server_mono: 10 });
    player.tracking(binding.transform_id, binding.scan_revision);
    const start = () => { holds.set(fixture, { fixture_id: fixture, label: 'Desk spot', reason: 'aim', since_ms: 1 }); listener?.({ phase: 'start', fixture_id: fixture }); };
    const settle = (patch = {}) => listener?.({ phase: 'settled', fixture_id: fixture, label: 'Desk spot', reason: 'aim', before: { tilt_deg: 30 },
        after: { tilt_deg: 41 }, at_ms: 1700000000000, ...patch });
    const change = patch => { start(); settle(patch); };
    const snap = (seq, hand, extra = {}) => {
        const latest = posts.filter(row => row.path === 'manual').at(-1)?.body;
        const record = hand && { hold_id: latest?.hold_id || 'saved-hold', event_id: latest?.event_id || 'saved-event',
            revision: latest?.client_seq || 1, state: { tilt_deg: 41 }, ...hand };
        player.receive({ ...lit, seq, ...(protocol === 2 ? { manual_protocol: 2 } : {}), ...extra,
            fixtures: { [fixture]: { ...lit.fixtures[fixture],
                manual_revision: record?.revision ?? (latest ? latest.client_seq + 1 : 0), hand: record,
                ...(extra.fixtures?.[fixture] || {}) } } });
    };
    const flush = async () => { for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve)); };
    return { player, overlay, posts, released, queued, holds, restored, start, settle, change, snap, flush, listener: () => listener };
}

const handAnswer = (path, body) => path === 'manual/resume' ? { status: 200, answer: { ok: true,
    request_id: body.request_id, hold_id: body.hold_id, manual_revision: body.expected_revision + 1 } } : ({ status: 200, answer: { ok: true, event_id: body.event_id,
    recorded: true, learned: false, epoch: lit.epoch, from_seq: 5, manual_revision: body.client_seq,
    hand: { hold_id: body.hold_id, event_id: body.event_id, revision: body.client_seq,
        client_id: body.client_id, client_seq: body.client_seq, parts: ['spot'], state: { tilt_deg: 41 } } } });

test('a newer gesture fences old hand release before its settled report', async () => {
    const f = handSetup({ protocol: 2, answer: handAnswer });
    f.snap(1, null); f.change(); await f.flush();
    f.snap(5, handAnswer('manual', f.posts[0].body).answer.hand);
    f.start();
    f.snap(6, null, { fixtures: { [fixture]: { manual_revision: 2 } } });
    assert.deepEqual(f.released, [], 'the pending gesture already owns the fixture');
});

test('a superseded queued manual retry never reaches the server', async () => {
    const f = handSetup({ protocol: 2, answer: () => ({ status: 503, answer: null }) });
    f.snap(1, null); f.change(); await f.flush();
    const retry = f.queued.at(-1);
    f.change(); await f.flush();
    retry(); await f.flush();
    assert.equal(f.posts.length, 2, 'only the two distinct edits are posted');
});

test('fenced edits allocate their ordering at gesture start and keep a stable hold identity', async () => {
    const f = handSetup({ protocol: 2, answer: handAnswer });
    f.snap(1, null); f.start(); f.snap(2, null); f.settle(); await f.flush();
    f.change(); await f.flush();
    const [first, second] = f.posts.map(row => row.body);
    assert.equal(first.client_id, 'page-a');
    assert.equal(first.client_seq, 1); assert.equal(second.client_seq, 2);
    assert.ok(first.hold_id); assert.equal(second.hold_id, first.hold_id);
    assert.equal(first.expected_revision, 0);
    assert.deepEqual(first.snapshot, { epoch: lit.epoch, seq: 1 });
});

test('an unrelated hand cannot acknowledge and then release the latest edit', async () => {
    const f = handSetup({ protocol: 2, answer: handAnswer });
    f.snap(1, null); f.change(); await f.flush();
    f.snap(5, { hold_id: 'other-hold', event_id: 'other-event', revision: 1, state: { tilt_deg: 22 } });
    f.snap(6, null, { fixtures: { [fixture]: { manual_revision: 2 } } });
    assert.deepEqual(f.released, []);
});

test('a matching durable hand rekeys a local hold across producer restart and later releases it', async () => {
    const f = handSetup({ protocol: 2, answer: handAnswer });
    f.snap(1, null); f.change(); await f.flush();
    const hand = handAnswer('manual', f.posts[0].body).answer.hand;
    f.player.anchor({ epoch: 'preview-restarted', server_mono: 10 });
    f.snap(1, hand, { epoch: 'preview-restarted' });
    f.snap(2, null, { epoch: 'preview-restarted', fixtures: { [fixture]: { manual_revision: 2 } } });
    assert.deepEqual(f.released, [fixture]);
});

test('a fresh viewer restores durable held values silently before rendering coordinator rows', () => {
    const f = handSetup({ protocol: 2 });
    const hand = { hold_id: 'saved-hold', event_id: 'saved-event', revision: 7, client_id: 'prior-page', client_seq: 3,
        parts: ['spot'], recovering: true, state: { pan_deg: 8, tilt_deg: 41, spotlight: { on: true, brightness: .8, color_temp_kelvin: 2700 } } };
    f.snap(1, hand);
    assert.equal(f.restored.length, 1);
    assert.deepEqual(f.restored[0], { id: fixture, hand });
    assert.equal(f.posts.length, 0);
    f.snap(2, hand);
    assert.equal(f.restored.length, 1, 'the same revision is restored once');
});

test('a newer gesture cancels Resume before its delayed send reaches the server', async () => {
    const f = handSetup({ protocol: 2, answer: handAnswer });
    f.snap(1, null); f.change(); await f.flush();
    const hand = handAnswer('manual', f.posts[0].body).answer.hand;
    f.snap(5, hand);
    f.player.handBack();
    f.change(); await f.flush();
    assert.equal(f.posts.some(row => row.path === 'manual/resume'), false);
    assert.notEqual(f.posts.at(-1).body.hold_id, hand.hold_id);
    assert.equal(f.holds.has(fixture), true, 'the new gesture remains locally held');
});

test('an older producer preserves local control without unfenced writes', async () => {
    const f = handSetup({ protocol: 1 });
    f.snap(1, null); f.change(); await f.flush();
    assert.equal(f.posts.length, 0);
    assert.equal(f.player.handHolds()[0].learning, 'unsupported');
    assert.equal(f.player.handBack(), 1);
    await f.flush();
    assert.equal(f.posts.length, 0);
});

test('Resume waits for its matching acknowledgment and a delayed acknowledgment cannot end a newer burst', async () => {
    let complete;
    const f = handSetup({ answer: (path, body) => path === 'manual/resume'
        ? new Promise(resolve => { complete = () => resolve(handAnswer(path, body)); }) : handAnswer(path, body) });
    f.snap(1, null); f.change(); await f.flush();
    f.snap(5, handAnswer('manual', f.posts[0].body).answer.hand);
    f.player.handBack(); await f.flush();
    assert.equal(f.holds.has(fixture), true, 'the server has not acknowledged Resume');
    f.start();
    complete(); await f.flush();
    assert.equal(f.holds.has(fixture), true);
    assert.deepEqual(f.released, []);
    f.settle(); await f.flush();
    assert.notEqual(f.posts.at(-1).body.hold_id, f.posts[0].body.hold_id, 'the gesture after Resume starts a new hold');
});

test('conflicting edits and Resume keep the local hand and do not retry stale authority', async () => {
    const f = handSetup({ answer: () => ({ status: 409, answer: { ok: false, error: 'manual_conflict' } }) });
    f.snap(1, null); f.change(); await f.flush();
    assert.equal(f.player.handHolds()[0].learning, 'not_recorded');
    f.snap(2, { hold_id: 'newer-remote', event_id: 'newer-event', revision: 9, state: { tilt_deg: 20 } });
    assert.equal(f.restored.length, 0, 'a conflicting local edit stays locally held');
    f.player.handBack(); await f.flush();
    assert.equal(f.holds.has(fixture), true);
    assert.deepEqual(f.released, []);
    assert.equal(f.posts.length, 2, 'a 409 edit and Resume are never retried');
});

test('Resume during a newer unsettled gesture cancels that draft and releases its prior acknowledged hand', async () => {
    const f = handSetup();
    f.snap(1, null); f.change(); await f.flush();
    const hand = handAnswer('manual', f.posts[0].body).answer.hand;
    f.snap(5, hand); f.start();
    f.player.handBack(); await f.flush();
    const resume = f.posts.find(row => row.path === 'manual/resume');
    assert.ok(resume, 'the acknowledged backend hold is also released');
    assert.equal(resume.body.hold_id, hand.hold_id);
    assert.equal(resume.body.expected_revision, hand.revision);
    assert.equal(f.holds.has(fixture), false);
    f.settle(); await f.flush();
    assert.equal(f.posts.length, 2, 'the cancelled draft never reports afterward');
});

test('Resume waits for the captured in-flight edit acknowledgment before choosing its revision', async () => {
    let finish;
    const f = handSetup({ answer: (path, body) => path === 'manual'
        ? new Promise(resolve => { finish = () => resolve(handAnswer(path, body)); }) : handAnswer(path, body) });
    f.snap(1, null); f.change();
    f.player.handBack(); await f.flush();
    assert.equal(f.posts.length, 1, 'the edit is still in flight');
    finish(); await f.flush();
    assert.equal(f.posts[1].body.expected_revision, 1);
    assert.equal(f.holds.has(fixture), false);
});

test('cancelling a newer draft can use the prior edit receipt that arrived while that draft was active', async () => {
    let finish;
    const f = handSetup({ answer: (path, body) => path === 'manual'
        ? new Promise(resolve => { finish = () => resolve(handAnswer(path, body)); }) : handAnswer(path, body) });
    f.snap(1, null); f.change(); f.start();
    finish(); await f.flush();
    f.player.handBack(); await f.flush();
    assert.equal(f.posts[1].body.expected_revision, 1);
    assert.equal(f.holds.has(fixture), false);
});

test('an explicit Resume after an edit conflict uses the returned winning hold fence', async () => {
    const winner = { hold_id: 'winner', event_id: 'winner-event', revision: 9, state: { tilt_deg: 20 } };
    const f = handSetup({ answer: (path, body) => path === 'manual'
        ? { status: 409, answer: { ok: false, error: 'manual_conflict', manual_revision: 9, hand: winner } }
        : handAnswer(path, body) });
    f.snap(1, null); f.change(); await f.flush();
    f.player.handBack(); await f.flush();
    assert.equal(f.posts[1].body.hold_id, 'winner');
    assert.equal(f.posts[1].body.expected_revision, 9);
    assert.equal(f.holds.has(fixture), false);
});

test('a delayed successful Resume cannot clear a newer authoritative hand already drawn', async () => {
    let complete;
    const f = handSetup({ answer: (path, body) => path === 'manual/resume'
        ? new Promise(resolve => { complete = () => resolve(handAnswer(path, body)); }) : handAnswer(path, body) });
    f.snap(1, null); f.change(); await f.flush();
    f.snap(5, handAnswer('manual', f.posts[0].body).answer.hand);
    f.player.handBack(); await f.flush();
    const newer = { hold_id: 'another-page', event_id: 'new-edit', revision: 3, state: { tilt_deg: 25 } };
    f.snap(6, newer);
    complete(); await f.flush();
    assert.deepEqual(f.released, []);
    assert.equal(f.restored.at(-1).hand.hold_id, newer.hold_id);
    assert.equal(f.holds.has(fixture), true);
});

test('a confirmed older local hand yields to a newer authoritative edit without an intervening blank', async () => {
    const f = handSetup();
    f.snap(1, null); f.change(); await f.flush();
    f.snap(5, handAnswer('manual', f.posts[0].body).answer.hand);
    f.snap(6, { hold_id: 'another-page', event_id: 'new-edit', revision: 2, state: { tilt_deg: 25 } });
    assert.equal(f.restored.length, 1);
    assert.equal(f.restored[0].hand.hold_id, 'another-page');
    assert.deepEqual(f.released, []);
});

test('a settled hand change goes to den-live once, with the snapshot Home was drawing, and its hold ends only when den-live releases it', async () => {
    const { player, posts, released, change, snap, flush, holds } = handSetup();
    change();
    assert.equal(posts.length, 0, 'no snapshot drawn yet: nothing to learn against');
    assert.equal(player.handHolds()[0].learning, 'unsent');
    snap(1, null);
    change();
    await flush();
    assert.equal(posts.length, 1);
    assert.equal(posts[0].path, 'manual');
    assert.deepEqual(posts[0].body, { schema: 'den-manual-1', event_id: 'home-1', fixture_id: fixture, before: { tilt_deg: 30 },
        after: { tilt_deg: 41 }, at_ms: 1700000000000, snapshot: { epoch: lit.epoch, seq: 1 },
        client_id: 'page-a', client_seq: 2, hold_id: posts[0].body.hold_id, expected_revision: 0 });
    assert.equal(player.handHolds()[0].learning, 'recorded');
    // Before from_seq, and before den-live has shown the hand, a snapshot without it ends nothing.
    snap(3, null); snap(4, null);
    assert.deepEqual(released, []);
    snap(5, { event_id: 'home-1', parts: ['spot'], since_mono: 1, ends: 'task_change', warning: null });
    assert.equal(player.handHolds()[0].warning, null);
    snap(6, { event_id: 'home-1', parts: ['spot'], since_mono: 1, ends: 'task_change', warning: 'beam_through_head' });
    assert.equal(player.handHolds()[0].warning, 'beam_through_head', 'the held spot is kept, and Home warns');
    assert.deepEqual(released, []);
    snap(7, null);
    assert.deepEqual(released, [fixture], 'den-live released it at its next reason to act');
    assert.equal(holds.size, 0);
    snap(8, null);
    assert.deepEqual(released, [fixture], 'once');
});

test('a declined change, an older den-live and another run never hand the fixture back; Resume and Off then On do, and tell den-live', async () => {
    const declined = handSetup({ answer: (path, body) => ({ status: 200, answer: { ok: true, event_id: body.event_id, recorded: false,
        learned: false, why_not: 'no_situation', hand: null, from_seq: 2 } }) });
    declined.snap(1, null); declined.change(); await declined.flush();
    declined.snap(2, null); declined.snap(3, null);
    assert.deepEqual(declined.released, [], 'den-live never showed a hand: Home keeps holding');
    assert.deepEqual(declined.player.handHolds().map(hold => [hold.learning, hold.why_not]), [['not_recorded', 'no_situation']]);
    const older = handSetup({ answer: () => ({ status: 404, answer: { ok: false, error: 'not_served' } }) });
    older.snap(1, null); older.change(); await older.flush();
    older.snap(2, null);
    assert.equal(older.posts.length, 1, 'a 4xx is never retried');
    assert.equal(older.player.handHolds()[0].learning, 'unsupported');
    assert.deepEqual(older.released, []);
    // den-live 1354fb95 answers the route it lacks with 501 (not_served): also unsupported at once, never retried, so
    // the chip never says the coordinator will learn.
    const unserved = handSetup({ answer: () => ({ status: 501, answer: { ok: false, error: 'not_served' } }) });
    unserved.snap(1, null); unserved.change(); await unserved.flush();
    assert.equal(unserved.posts.length, 1);
    assert.equal(unserved.queued.length, 1, 'only the snapshot check is queued: no retry');
    assert.equal(unserved.player.handHolds()[0].learning, 'unsupported');
    // Another run (den-live restarted) can't end a hold taken in the last one.
    const restarted = handSetup();
    restarted.snap(1, null); restarted.change(); await restarted.flush();
    restarted.snap(5, { event_id: 'home-1' });
    restarted.player.anchor({ epoch: 'preview-b', server_mono: 10 });
    restarted.player.receive({ ...lit, epoch: 'preview-b', seq: 9, fixtures: { [fixture]: { ...lit.fixtures[fixture], hand: null } } });
    assert.deepEqual(restarted.released, []);
    // Resume hands it back and posts manual/resume; so does the coordinator going from off to on.
    assert.equal(restarted.player.handBack(), 1);
    await restarted.flush();
    assert.equal(restarted.posts.at(-1).path, 'manual/resume');
    assert.equal(restarted.posts.at(-1).body.fixture_id, fixture);
    restarted.change();
    restarted.player.observeControl({ on: false, stopped: false, stop_counter: 0 });
    restarted.player.observeControl({ on: true, stopped: false, stop_counter: 0 });
    await restarted.flush();
    assert.equal(restarted.posts.at(-1).path, 'manual/resume');
    assert.equal(restarted.holds.size, 0);
});

test('a dropped report retries with the same event_id; a look-only page sends nothing', async () => {
    const flaky = handSetup({ answer: (path, body, n) => n === 1 ? { status: 503, answer: null }
        : { status: 200, answer: { ok: true, event_id: body.event_id, recorded: true, from_seq: 3 } } });
    flaky.snap(1, null); flaky.change(); await flaky.flush();
    assert.equal(flaky.posts.length, 1);
    flaky.queued.pop()(); await flaky.flush();   // the retry is the last thing queued
    assert.deepEqual(flaky.posts.map(row => row.body.event_id), ['home-1', 'home-1']);
    assert.equal(flaky.player.handHolds()[0].learning, 'recorded');
    const look = handSetup({ withPost: false });
    look.snap(1, null); look.change(); await look.flush();
    assert.equal(look.posts.length, 0);
    assert.equal(look.player.handHolds()[0].learning, 'none');
    assert.equal(look.player.handBack(), 1);
});

test('the report names the snapshot drawn before the burst started, and the hold counts in the run den-live replies with', async () => {
    const { posts, released, start, settle, snap, flush } = handSetup({ answer: (path, body) => ({ status: 200, answer: { ok: true,
        event_id: body.event_id, recorded: true, from_seq: 4, epoch: 'preview-b' } }) });
    snap(1, null);
    start();
    snap(2, null); snap(3, null);   // den-live glides on while the hand works
    settle();
    await flush();
    assert.deepEqual(posts[0].body.snapshot, { epoch: lit.epoch, seq: 1 });
    // den-live answered from another run: only that run's snapshots can end the hold.
    snap(4, { event_id: 'home-1' }); snap(5, null);
    assert.deepEqual(released, []);
});
