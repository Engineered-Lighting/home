import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createSpotPlayback } from '../app/src/home-den-spot-playback.mjs';

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
