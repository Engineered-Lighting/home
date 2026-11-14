import test from 'node:test';
import assert from 'node:assert/strict';
import { createSpotPlayback } from '../app/src/home-den-spot-playback.mjs';

const binding = { content_revision: 'den-r1', registration_version: 1,
    manifest_digest: 'a'.repeat(64), frame_digest: 'b'.repeat(64), canonical_collision_digest: 'c'.repeat(64) };
const fixture = fixture_id => ({ fixture_id, pan_deg: -14, tilt_deg: 45, on: true, level: .6, cct_k: 3000, why: 'reading', trace_id: 'trace-1' });

function setup() {
    let at = 0, last = null;
    const scope = { generation: 1, binding, equipment_revision: 3, equipment_digest: 'd'.repeat(64), fixture_ids: ['a', 'b'] };
    const overlay = { coordinatorSpotScope: () => scope, applyCoordinatorSpots(rows) { last = rows; return true; } };
    const player = createSpotPlayback({ home: 'den', site: 'victoria', clock: () => at });
    const controls = (patch = {}, rtt = 0) => player.observeControl({ epoch: 'one', control_revision: 1, enabled: true,
        stopped: false, server_time_ms: 10_000 + at, ...patch }, at - rtt, at);
    const sample = (seq, patch = {}) => player.receive({ schema: 'den-virtual-spots-1', home: 'den', site: 'victoria', epoch: 'one', seq,
        control_revision: 1, enabled: true, stopped: false, binding, equipment_revision: 3, equipment_digest: scope.equipment_digest,
        fixtures: scope.fixture_ids.map(fixture), sampled_at_ms: 10_000 + at, expires_at_ms: 10_100 + at, ...patch });
    player.attach(overlay);
    return { player, overlay, scope, controls, sample, advance: ms => { at += ms; }, last: () => last };
}

test('spot starts dark and only complete, bound, current producer snapshots can light it', t => {
    const s = setup(); t.after(() => s.player.attach(null));
    assert.equal(s.last(), null);
    s.sample(1);
    assert.equal(s.last(), null, 'first snapshot waits for the producer clock');
    s.controls();
    assert.deepEqual(s.last(), [fixture('a'), fixture('b')]);
    assert.equal(s.player.current().fixtures[0].trace_id, 'trace-1');
    s.sample(2, { fixtures: [fixture('a'), fixture('a')] });
    assert.equal(s.last(), null, 'a missing row cannot leave its previously lit spot on');
    s.sample(3, { binding: { ...binding, registration_version: 2 } });
    assert.equal(s.last(), null);
    s.sample(4);
    assert.ok(s.last());
    s.scope.generation++;
    s.player.check();
    assert.equal(s.last(), null, 'an overlay rebuild retires the applied generation');
    s.sample(4);
    assert.equal(s.last(), null, 'the same sample never lights a rebuilt overlay');
});

test('expiry and reconnect never extend a delayed lease', t => {
    const s = setup(); t.after(() => s.player.attach(null));
    s.controls(); s.sample(1);
    s.advance(101); s.player.check();
    assert.equal(s.last(), null);
    s.sample(2, { sampled_at_ms: 10_000, expires_at_ms: 10_100 });
    assert.equal(s.last(), null);
    s.sample(3);
    assert.ok(s.last());
    s.advance(20); s.player.disconnect();
    s.sample(4, { sampled_at_ms: 10_101, expires_at_ms: 10_201 });
    s.controls();
    assert.equal(s.last(), null, 'a pre-disconnect sample is withheld even when its lease is still valid');
    s.sample(5);
    assert.ok(s.last());
});

test('a crossed resume acknowledgment cannot release a newer stop, and Off stays independent', t => {
    const s = setup(); t.after(() => s.player.attach(null));
    s.controls(); s.sample(1);
    const earlier = s.player.block('stop');
    const later = s.player.block('stop');
    s.player.release('stop', earlier, 2);
    s.controls({ control_revision: 2 }); s.sample(2, { control_revision: 2 });
    assert.equal(s.last(), null);
    assert.equal(s.player.holds().stop, later);
    s.player.acknowledge('stop', later, 3);
    s.sample(3, { control_revision: 3, stopped: true });
    assert.equal(s.last(), null);
    s.player.block('off');
    s.sample(4, { control_revision: 4, stopped: true, enabled: false });
    s.sample(5, { control_revision: 5, enabled: false });
    assert.equal(s.last(), null, 'resuming does not turn automation on');
    s.sample(6, { control_revision: 6 });
    assert.ok(s.last());
});

test('the authoritative dark snapshot carries the trace for the stopped fixture', t => {
    const s = setup(); t.after(() => s.player.attach(null));
    s.controls(); s.sample(1);
    const dark = s.scope.fixture_ids.map(id => ({ ...fixture(id), on: false, level: 0, why: 'Stopped', trace_id: 'stop-1' }));
    s.sample(2, { control_revision: 2, stopped: true, fixtures: dark });
    assert.deepEqual(s.last(), dark);
    assert.equal(s.player.current().fixtures[0].trace_id, 'stop-1');
});

test('an unknown Stop holds through remount and waits for authoritative Stop then Resume', t => {
    const s = setup(); t.after(() => s.player.attach(null));
    s.controls(); s.sample(1);
    s.player.block('stop');
    s.player.disconnect(); s.player.attach(null); s.player.attach(s.overlay);
    s.controls({ epoch: 'two' }); s.sample(2, { epoch: 'two' });
    assert.equal(s.last(), null);
    s.sample(3, { epoch: 'two', control_revision: 2, stopped: true });
    s.sample(4, { epoch: 'two', control_revision: 3 });
    assert.ok(s.last());
});
