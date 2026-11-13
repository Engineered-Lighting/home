import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createSpotPlayback } from '../app/src/home-den-spot-playback.mjs';

const { snapshot: lit, stopped_snapshot: stopped } = JSON.parse(readFileSync(
    new URL('./den-live/fixtures/preview-f3.json', import.meta.url), 'utf8'));
const fixture = 'F3', { binding } = lit;

function setup() {
    let at = 1000, alarm = null, drawn = null;
    const scope = { generation: 1, binding: { content_revision: binding.scan_revision },
        equipment_revision: binding.equipment_revision, equipment_digest: binding.equipment_digest, fixture_ids: [fixture] };
    const overlay = { coordinatorSpotScope: () => scope, applyCoordinatorSpots(rows) { drawn = rows; return true; } };
    const player = createSpotPlayback({ home: 'den', site: 'victoria', clock: () => at,
        later(callback) { alarm = callback; return 1; }, cancel() { alarm = null; } });
    player.attach(overlay);
    const connect = (epoch = lit.epoch) => {
        player.connect(at);
        player.anchor({ epoch, server_mono: 10 });
        player.tracking(binding.transform_id, binding.scan_revision);
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
