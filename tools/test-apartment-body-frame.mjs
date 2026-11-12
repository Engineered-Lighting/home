// The live body's frame contract (app/src/home-3d/apartment-body-frame.mjs): den-live-body-1 frames, interpolation.
//   node --test tools/test-apartment-body-frame.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const F = await import(pathToFileURL(path.join(ROOT, 'app/src/home-3d/apartment-body-frame.mjs')).href);
const { BODY_SCHEMA, KIND, validBodyFrame, decodeBodyFrame, encodeBodyFrame, mergeFrames, mergeJoints, drawBuffers, zoomRatio, MAX_POINTS } = F;

const pts = (list) => list.map(([key, x, y, z, kind = KIND.measured]) => ({ key, xyz_m: [x, y, z], kind, normal: [0, 0, 1] }));
const frame = (list, extra = {}) => encodeBodyFrame({ trackId: 'trk-1', captureTs: 10, seq: 1, points: pts(list), ...extra });

test('a frame round-trips: keys sorted, millimetre positions, kinds, unit normals and the body centroid', () => {
    const f = frame([[9, 1, 2, 1.5], [3, 1.002, 2, 0.5, KIND.backFill], [7, 1.3, 2.1, 1.0, KIND.heldObject]],
        { joints: { nose: [1, 2, 1.6], left_wrist: { xyz_m: [1.3, 2.1, 1.0], status: 'placed' } }, object: { label: 'Book', confidence: 0.9 } });
    assert.equal(f.schema, BODY_SCHEMA);
    assert.ok(validBodyFrame(f));
    const d = decodeBodyFrame(f);
    assert.deepEqual([...d.keys], [3, 7, 9]);
    assert.deepEqual([...d.kind], [KIND.backFill, KIND.heldObject, KIND.measured]);
    assert.ok(Math.abs(d.pos[0] - 1.002) < 1e-6 && Math.abs(d.pos[2] - 0.5) < 1e-6, 'millimetre precision');
    assert.ok(Math.abs(d.nrm[2] - 1) < 1e-6);
    assert.equal(d.objects, 1);
    assert.deepEqual(d.centroid.map(v => +v.toFixed(3)), [1.001, 2, 1], 'the held object is not part of the body centroid');
    assert.deepEqual(d.joints.left_wrist, [1.3, 2.1, 1.0]);
    assert.equal(d.jointStatus.left_wrist, 'placed');
    assert.deepEqual(d.object, { label: 'Book', confidence: 0.9 });
});

test('anything off-contract is refused whole', () => {
    const good = frame([[1, 0, 0, 1], [2, 0, 0, 1.1]]);
    assert.ok(validBodyFrame(good));
    const bad = [
        { ...good, schema: 'den-live-body-2' },
        { ...good, track_id: '' },
        { ...good, capture_ts: NaN },
        { ...good, points: { ...good.points, count: good.points.count + 1 } },
        { ...good, points: { ...good.points, count: MAX_POINTS + 1 } },
        { ...good, points: { ...good.points, key: undefined } },
        { ...good, points: { ...good.points, position_mm: good.points.position_mm.slice(0, -4) } },
        { ...good, joints: [{ name: 'tail', xyz_m: [0, 0, 0] }] },
        { ...good, joints: [{ name: 'nose', xyz_m: [0, 0, 0] }, { name: 'nose', xyz_m: [0, 0, 1] }] },
        { ...good, joints: [{ name: 'nose', xyz_m: [0, 0, 1e6] }] },
        { ...good, object: { label: 'x'.repeat(40) } },
        { ...good, object: { confidence: 2 } },
    ];
    for (const f of bad) { assert.equal(validBodyFrame(f), false, JSON.stringify(Object.keys(f))); assert.equal(decodeBodyFrame(f), null); }
});

test('bytes that disagree with the shape are refused: keys out of order, an unknown kind, bad base64', () => {
    const f = frame([[1, 0, 0, 1], [2, 0, 0, 1.1]]);
    const swapped = { ...f, points: { ...f.points, key: btoa(String.fromCharCode(2, 0, 0, 0, 1, 0, 0, 0)) } };
    assert.ok(validBodyFrame(swapped)); assert.equal(decodeBodyFrame(swapped), null, 'keys must ascend');
    const kind = { ...f, points: { ...f.points, kind: btoa(String.fromCharCode(0, 2)) } };
    assert.equal(decodeBodyFrame(kind), null, 'kind 2 is not defined');
    const junk = { ...f, points: { ...f.points, kind: '!!!!' } };
    assert.equal(decodeBodyFrame(junk), null);
});

test('interpolation: equal keys move between frames, unmatched points fade out and in', () => {
    const a = decodeBodyFrame(frame([[1, 0, 0, 1], [2, 0, 0, 2]]));
    const b = decodeBodyFrame(encodeBodyFrame({ trackId: 'trk-1', captureTs: 10.1, points: pts([[2, 1, 0, 2], [3, 5, 5, 5]]) }));
    const out = drawBuffers(16);
    assert.equal(mergeFrames(a, b, 0.5, out), 3);
    // key 1 only in a: fading (eased 0.5 -> 0.5); key 2 halfway; key 3 only in b: fading in
    assert.deepEqual([out.pos[0], out.alpha[0]], [0, 0.5]);
    assert.deepEqual([out.pos[3], out.pos[5]], [0.5, 2]);
    assert.deepEqual([out.pos[6], out.alpha[2]], [5, 0.5]);
    assert.equal(mergeFrames(a, b, 1, out), 2, 'at the end, a\'s unmatched point is gone');
    assert.deepEqual([out.pos[0], out.alpha[0]], [1, 1]);
    assert.equal(mergeFrames(null, b, 0, out), 2, 'no previous frame: just b');
    assert.ok(out.rand[0] >= 0 && out.rand[0] < 1);
    const before = out.rand[0]; mergeFrames(a, b, 1, out); assert.equal(out.rand[0], before, 'a key keeps its own random phase');
});

test('joints interpolate, and the capacity is never exceeded', () => {
    const a = { joints: { nose: [0, 0, 1] } }, b = { joints: { nose: [1, 0, 1], left_ear: [0, 1, 1] } };
    assert.deepEqual(mergeJoints(a, b, 0.25), { nose: [0.25, 0, 1], left_ear: [0, 1, 1] });
    const many = decodeBodyFrame(frame(Array.from({ length: 40 }, (_, i) => [i + 1, i * 0.01, 0, 1])));
    const out = drawBuffers(10);
    assert.equal(mergeFrames(null, many, 1, out), 10);
});

test('zoom ratio: 0 at Home\'s closest step, 1 at the overview', () => {
    assert.equal(zoomRatio(4.8), 0);
    assert.equal(zoomRatio(23), 1);
    assert.equal(zoomRatio(2), 0);
    assert.equal(zoomRatio(100), 1);
    assert.ok(Math.abs(zoomRatio(Math.sqrt(4.8 * 23)) - 0.5) < 1e-9);
    assert.equal(zoomRatio(NaN), 1);
});
