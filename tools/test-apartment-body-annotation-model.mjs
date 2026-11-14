// The live body's label rules (app/src/home-3d/apartment-body-annotation-model.mjs): what the caption says, when it
// changes, and how the head direction is steadied.
//   node --test tools/test-apartment-body-annotation-model.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const M = await import(pathToFileURL(path.join(ROOT, 'app/src/home-3d/apartment-body-annotation-model.mjs')).href);
const { createBodyAnnotationState, createFacingFilter, captionLines, cardRows, detailFor, formatDuration } = M;

const DT = 0.1, DEG = Math.PI / 180;
// A seated person facing +x (pelvis at 0.5 m) unless told otherwise.
function joints({ az = 0, pelvis = 0.5, at = [0, 0] } = {}) {
    const head = [at[0], at[1], pelvis + 0.65], ear = 0.07, lx = -Math.sin(az), ly = Math.cos(az);   // the person's left
    return { left_hip: [at[0] + lx * 0.1, at[1] + ly * 0.1, pelvis], right_hip: [at[0] - lx * 0.1, at[1] - ly * 0.1, pelvis],
        left_shoulder: [at[0] + lx * 0.18, at[1] + ly * 0.18, pelvis + 0.45], right_shoulder: [at[0] - lx * 0.18, at[1] - ly * 0.18, pelvis + 0.45],
        left_ear: [head[0] - Math.sin(az) * ear, head[1] + Math.cos(az) * ear, head[2]], right_ear: [head[0] + Math.sin(az) * ear, head[1] - Math.cos(az) * ear, head[2]],
        nose: [head[0] + Math.cos(az) * 0.1, head[1] + Math.sin(az) * 0.1, head[2] - 0.02] };
}
function run(state, seconds, input, t0) {
    let out, t = t0;
    for (let i = 0; i < Math.round(seconds / DT); i++) { t += DT; out = state.step({ now: t, dt: DT, centroid: [0, 0, 0.8], joints: joints(), zoomT: 0.5, ...input(t) }); }
    return { out, t };
}

test('detail by zoom: everything up close, name and activity in the room, name at the overview', () => {
    assert.deepEqual([0, 0.34, 0.35, 0.79, 0.8, 1].map(detailFor), [2, 2, 1, 1, 0, 0]);
});

test('an activity shows at once, but changing or clearing it takes 2 s (it stays through detection gaps)', () => {
    const s = createBodyAnnotationState();
    let r = run(s, 0.2, () => ({ activity: 'Reading' }), 0);
    assert.equal(r.out.activity, 'Reading', 'shown at once when nothing is shown');
    r = run(s, 1.5, () => ({ activity: null }), r.t);
    assert.equal(r.out.activity, 'Reading', 'a 1.5 s gap does not clear it');
    r = run(s, 0.3, () => ({ activity: 'Reading' }), r.t);
    r = run(s, 1.9, () => ({ activity: 'Phone' }), r.t);
    assert.equal(r.out.activity, 'Reading', 'a change under 2 s is not shown');
    r = run(s, 0.3, () => ({ activity: 'Phone' }), r.t);
    assert.equal(r.out.activity, 'Phone');
    assert.ok(r.out.since < 0.5, 'its duration starts when it is shown');
    r = run(s, 2.2, () => ({ activity: null }), r.t);
    assert.equal(r.out.activity, '');
});

test('the held-object tag stays 1.5 s past its last sighting', () => {
    const s = createBodyAnnotationState();
    let r = run(s, 0.3, () => ({ object: { label: 'Book', confidence: 0.9 } }), 0);
    assert.deepEqual(r.out.object, { label: 'Book', confidence: 0.9 });
    r = run(s, 1.3, () => ({ object: null }), r.t);
    assert.equal(r.out.object?.label, 'Book');
    r = run(s, 0.4, () => ({ object: null }), r.t);
    assert.equal(r.out.object, null);
});

test('the caption changes side only past 42% / 66% of the width', () => {
    const s = createBodyAnnotationState(), side = x => s.step({ now: 1, dt: DT, joints: joints(), centroid: [0, 0, 0.8], headScreenX: x, width: 1000 }).right;
    assert.equal(side(500), true, 'starts on the right');
    assert.equal(side(650), true);
    assert.equal(side(670), false);
    assert.equal(side(450), false, 'inside the band: no flip back');
    assert.equal(side(410), true);
});

test('posture from the pose: seated below a 0.72 m pelvis, walking above 0.25 m/s', () => {
    const s = createBodyAnnotationState();
    assert.equal(run(s, 0.5, () => ({}), 0).out.posture, 'Seated');
    const w = createBodyAnnotationState();
    const r = run(w, 2, t => ({ joints: joints({ pelvis: 0.95, at: [t * 0.8, 0] }), centroid: [t * 0.8, 0, 1] }), 0);
    assert.equal(r.out.posture, 'Walking');
    const still = run(createBodyAnnotationState(), 2, () => ({ joints: joints({ pelvis: 0.95 }), centroid: [0, 0, 1] }), 0);
    assert.equal(still.out.posture, 'Standing');
    assert.ok(Math.abs(still.out.ringZ - 0.01) < 1e-9, 'standing: the ring is on the floor');
    assert.ok(Math.abs(run(createBodyAnnotationState(), 3, () => ({}), 0).out.ringZ - 0.4) < 0.01, 'seated: on the seat');
});

test('facing: per-frame noise of about 12 degrees is steadied to well under a degree a frame', () => {
    const f = createFacingFilter();
    let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    let prev = null, worst = 0;
    for (let i = 0; i < 300; i++) {
        const a = (rnd() - 0.5) * 2 * 12 * DEG, out = f.step([Math.cos(a), Math.sin(a), 0], 1 / 30), az = Math.atan2(out[1], out[0]);
        if (i > 60 && prev != null) worst = Math.max(worst, Math.abs(az - prev));
        prev = az;
    }
    assert.ok(worst < 1 * DEG, `steadied: ${(worst / DEG).toFixed(2)} deg per frame`);
});

test('facing: a real 150 degree turn lands within a second', () => {
    const f = createFacingFilter();
    for (let i = 0; i < 30; i++) f.step([1, 0, 0], 1 / 30);
    const target = 150 * DEG; let landed = null;
    for (let i = 1; i <= 60; i++) {
        const out = f.step([Math.cos(target), Math.sin(target), 0], 1 / 30), az = Math.atan2(out[1], out[0]);
        if (landed == null && Math.abs(az - target) < 10 * DEG) landed = i / 30;
    }
    assert.ok(landed != null && landed < 1, `landed after ${landed} s`);
});

test('facing comes from the skeleton: the shoulder and hip lines, not the noisy nose and ears', () => {
    const J = joints({ az: 30 * DEG });
    const f = M.facingOf(J);
    assert.ok(Math.abs(Math.atan2(f[1], f[0]) - 30 * DEG) < 1e-9);
    const noisyHead = { ...J, nose: [J.nose[0], J.nose[1] - 0.06, J.nose[2]] };   // a nose lift 6 cm off: about 35 degrees on nose-ears
    assert.ok(Math.abs(Math.atan2(M.facingOf(noisyHead)[1], M.facingOf(noisyHead)[0]) - 30 * DEG) < 1e-9, 'noise in the head does not move it');
    const swapped = { ...J, left_shoulder: J.right_shoulder, right_shoulder: J.left_shoulder, left_hip: J.right_hip, right_hip: J.left_hip };
    assert.ok(Math.abs(Math.atan2(M.facingOf(swapped)[1], M.facingOf(swapped)[0]) - 30 * DEG) < 1e-9, 'a left/right swap is settled by the nose');
    const headOnly = { nose: J.nose, left_ear: J.left_ear, right_ear: J.right_ear };
    assert.ok(Math.abs(Math.atan2(M.facingOf(headOnly)[1], M.facingOf(headOnly)[0]) - 30 * DEG) < 2 * DEG, 'without shoulders, the head');
    assert.equal(M.facingOf({}), null);
});

test('facing from the pose: the skeleton direction, with the sight-line origin smoothed', () => {
    const s = createBodyAnnotationState();
    const r = run(s, 3, () => ({ joints: joints({ az: 90 * DEG }) }), 0);
    assert.ok(Math.abs(r.out.facingAz - 90 * DEG) < 2 * DEG);
    assert.ok(r.out.eye && r.out.head);
});

test('the measured figure uses the current clockwise torso bearing and has no sight line', () => {
    const s = createBodyAnnotationState();
    const update = facingDeg => s.step({ now: 1, dt: 0.016, joints: joints(), torso: { facingDeg, pelvisZ: 0.5 },
        centroid: [0, 0, 1], zoomT: 0 });
    const north = update(0), east = update(90);
    assert.equal(north.facingAz, Math.PI / 2);
    assert.ok(Math.abs(east.facingAz) < 1e-10);
    assert.equal(east.eye, null);
    assert.equal(east.head, null);
    assert.equal(east.posture, 'Seated');
    assert.equal(update(null).facingAz, null);
});

test('captions say less zoomed out, and name an unknown person "Someone"', () => {
    const st = { detail: 0, posture: 'Seated', activity: 'Reading', since: 754 };
    const resident = { name: 'Resident', known: true, source: 'face', confidence: 0.94 };
    assert.deepEqual(captionLines(resident, st, 'Reading light · 70%'), { name: 'Resident', doing: '', provenance: '', house: '' });
    assert.equal(captionLines(resident, { ...st, detail: 1 }).doing, 'Reading');
    assert.equal(captionLines(resident, { ...st, detail: 1, activity: '' }).doing, 'Seated');
    assert.deepEqual(captionLines(resident, { ...st, detail: 2 }, 'Reading light · 70%'),
        { name: 'Resident', doing: 'Seated · Reading · 13 min', provenance: 'face · 94%', house: 'Reading light · 70%' });
    assert.equal(captionLines(null, st).name, 'Someone');
    assert.equal(captionLines({ name: 'Resident', known: false }, { ...st, detail: 2 }).provenance, 'not recognised');
    assert.equal(captionLines({ name: '   ' }, st).name, 'Someone');
    const card = cardRows(resident, st, 'Reading light · 70%');
    assert.deepEqual(card, { name: 'Resident', confidence: '94%', posture: 'Seated', activity: 'Reading · 13 min', house: 'Reading light · 70%', known: true });
});

test('durations read the way people say them', () => {
    assert.deepEqual([0, 0.4, 42, 61, 3599, 3600, 5000].map(formatDuration), ['', '1 s', '42 s', '1 min', '60 min', '1 h 0 min', '1 h 23 min']);
});
