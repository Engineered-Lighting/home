import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { projectC12Preview } from '../app/src/home-3d/c12-preview.mjs';

const AS_OF = 1_800_000_000;
const golden = JSON.parse(readFileSync(new URL('./den-live/fixtures/c12-preview-golden.json', import.meta.url), 'utf8'));
const goldenMove = golden.steps[0];
const GOLDEN_MOVE = Buffer.from(goldenMove.frame.payload_hex, 'hex');

function snapshot(fields = {}) {
    // Decode the documented offsets of the cf1c325 golden MOVE independently of the sender.
    return { run_id: 'authored-run', event_index: 12, as_of: AS_OF, fixtures: {
        F3: { aim_origin_m: [0, 0, 2], horizontal_world_reference: [1, 0, 0],
            joints_cdeg: [GOLDEN_MOVE.readInt16LE(3), GOLDEN_MOVE.readInt16LE(5)],
            spot_level: GOLDEN_MOVE[7], spot_mired: GOLDEN_MOVE.readUInt16LE(8),
            expires_at: AS_OF + 3, move_seq: GOLDEN_MOVE.readUInt16LE(0),
            move_decision_id: goldenMove.intent.decision_id, ...fields },
    } };
}

function planeHit(projection, z) {
    const { origin, direction } = projection;
    const distance = (z - origin[2]) / direction[2];
    assert.ok(distance >= 0, 'the ray must point toward the page');
    return origin.map((coordinate, axis) => coordinate + distance * direction[axis]);
}

function assertNear(actual, expected) {
    assert.equal(actual.length, expected.length);
    for (const [index, value] of actual.entries()) {
        assert.ok(Math.abs(value - expected[index]) < 1e-12, `${value} != ${expected[index]}`);
    }
}

test('the decoded firmware golden vector reaches the page in the registered fixture frame', () => {
    const projection = projectC12Preview(snapshot(), 'F3');
    assert.equal(projection.on, true);
    assert.equal(projection.level, 200 / 254);
    assert.equal(projection.cctKelvin, 2703);
    assert.deepEqual([projection.runId, projection.eventIndex, projection.moveSeq, projection.moveDecisionId],
        ['authored-run', 12, 7, goldenMove.intent.decision_id]);
    assertNear([Math.hypot(...projection.direction)], [1]);
    const diagonal = Math.sqrt(1.5);
    assertNear(planeHit(projection, 1), [diagonal, diagonal, 1]);
});

test('rotating the horizontal basis and moving the optical pivot rotates and translates the hit', () => {
    const projection = projectC12Preview(snapshot({
        aim_origin_m: [2, 3, 2], horizontal_world_reference: [0, 4, 1],
    }), 'F3');
    const diagonal = Math.sqrt(1.5);
    assertNear(planeHit(projection, 1), [2 - diagonal, 3 + diagonal, 1]);
});

test('the beam starts dark and expires without another snapshot', () => {
    assert.equal(projectC12Preview(null, 'F3').on, false);
    assert.equal(projectC12Preview(snapshot(), 'F1').on, false);
    assert.equal(projectC12Preview(snapshot({ move_seq: null, move_decision_id: null }), 'F3').on, false);

    const current = snapshot();
    const receivedAtMs = 350;
    const render = monotonicMs => projectC12Preview(current, 'F3', current.as_of + (monotonicMs - receivedAtMs) / 1000);
    assert.equal(render(3349).on, true);
    assert.equal(render(3350).on, false);
    assert.equal(render(3350).level, 0);
    assert.equal(render(3350).direction, null);
});

test('wire level zero can aim the head while keeping the beam dark', () => {
    const projection = projectC12Preview(snapshot({ spot_level: 0, expires_at: null, move_seq: 0 }), 'F3');
    assert.equal(projection.on, false);
    assert.equal(projection.level, 0);
    assert.equal(projection.moveSeq, 0);
    assertNear(planeHit(projection, 1), [Math.sqrt(1.5), Math.sqrt(1.5), 1]);
});

test('malformed or incomplete lit receipts never display a beam', () => {
    const invalid = [
        { spot_level: 255 }, { spot_mired: 0 }, { expires_at: null }, { move_seq: 65536 },
        { aim_origin_m: [0, 0, NaN] }, { horizontal_world_reference: [0, 0, 1] }, { joints_cdeg: [45.5, -3000] },
    ];
    for (const fields of invalid) assert.equal(projectC12Preview(snapshot(fields), 'F3').on, false);
    assert.equal(projectC12Preview(snapshot(), 'F3', AS_OF - 1).on, false);
});
