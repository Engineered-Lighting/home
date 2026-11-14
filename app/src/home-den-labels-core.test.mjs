// node --test app/src/home-den-labels-core.test.mjs
import assert from 'node:assert/strict';
import test from 'node:test';
import * as L from './home-den-labels-core.mjs';

const near = (a, b, tol = 1e-3) => assert.ok(Math.abs(a - b) <= tol, `${a} is not ${b}`);
const label = (over = {}) => L.makeLabel({ id: 'l-desk-abc12', name: 'desk', kind: 'table',
    box: { centre_m: [1, 2, 0.375], size_m: [1.4, 0.7, 0.75], yaw_deg: 0 }, ...over });
const doc = (labels = [label()]) => ({ ...L.emptyDoc('rev-a'), labels });

test('names: exact usual names lead, then names starting with what is typed; what you typed comes last', () => {
    assert.deepEqual(L.suggestions('desk').map(s => s.name), ['desk', 'desk lamp', 'desk chair']);
    assert.deepEqual(L.suggestions('3D pri').map(s => s.name), ['3D printer', '3D pri']);
    assert.equal(L.suggestions('3D pri').at(-1).typed, true);
    assert.equal(L.suggestions('').length, 6);
    assert.equal(L.suggestions('sofa').some(s => s.typed), false);
});

test('kinds are guessed from the name, and an unknown name is "other"', () => {
    assert.equal(L.guess('3D printer').kind, 'machine');
    assert.equal(L.guess('blue painting').kind, 'picture');
    assert.equal(L.guess('old couch').kind, 'seat');
    assert.equal(L.guess('reading corner').kind, 'area');
    assert.equal(L.guess('Big bookcase').kind, 'storage');
    assert.equal(L.guess('guitar').kind, 'other');
    assert.deepEqual(L.guess('sofa').size, [2.0, 0.9, 0.85]);
});

test('names: cleaned, checked, and made unique', () => {
    assert.equal(L.cleanName('  my   desk '), 'my desk');
    assert.equal(L.nameProblem('', []), 'Give it a name.');
    assert.match(L.nameProblem('desk!', []), /letters/);
    assert.match(L.nameProblem('DESK', [label()]), /Another label/);
    assert.equal(L.nameProblem('DESK', [label()], 'l-desk-abc12'), null);
    assert.equal(L.uniqueName('desk', [label()]), 'desk 2');
    assert.match(L.newId('3D printer!', 1700000000000, 0.5), /^l-3d-printer-[a-z0-9]{5,7}$/);
});

test('a picture clicked on a wall sits flush with it, front into the room', () => {
    // A wall facing -y (the room is on the -y side), clicked at 1.5 m.
    const { box, on_wall } = L.placeBox({ name: 'painting', hit: { point: [2, 4, 1.5], normal: [0, -1, 0] }, viewer: [2, 0] });
    assert.equal(on_wall, true);
    assert.equal(box.yaw_deg, 180);
    const [fx, fy] = L.front(box.yaw_deg); near(fx, 0); near(fy, -1);
    near(box.centre_m[1], 4 - 0.015); near(box.centre_m[2], 1.5);
    assert.deepEqual(box.size_m, [0.8, 0.03, 0.6]);
});

test('a machine clicked on its side sits behind the side, centred on the click height', () => {
    // A printer on a cabinet: its side faces +x, clicked at 0.85 m.
    const { box, on_wall } = L.placeBox({ name: '3D printer', hit: { point: [0.53, 2.65, 0.85], normal: [1, 0, 0] }, viewer: [5, 0] });
    assert.equal(on_wall, false);
    assert.equal(box.yaw_deg, 270);                       // front faces +x
    near(box.centre_m[0], 0.53 - 0.25); near(box.centre_m[2], 0.85);
    near(box.centre_m[2] - box.size_m[2] / 2, 0.55);      // bottom on the cabinet, not the floor
});

test('a side click low down stands the box on the floor', () => {
    const { box } = L.placeBox({ name: 'bookshelf', hit: { point: [0.75, 3.6, 0.4], normal: [0, -1, 0] }, viewer: [2, 0] });
    near(box.centre_m[2] - box.size_m[2] / 2, 0); near(box.size_m[2], 1.8);
});

test('a top click sets the top at the click; a seat keeps its typical height', () => {
    const printer = L.placeBox({ name: '3D printer', hit: { point: [0.3, 2.65, 1.1], normal: [0, 0, 1] }, viewer: [5, 0] }).box;
    near(printer.centre_m[2] + printer.size_m[2] / 2, 1.1); near(printer.size_m[2], 0.6);
    const sofa = L.placeBox({ name: 'sofa', hit: { point: [2.6, 3.3, 0.42], normal: [0, 0, 1] }, viewer: [7, -3] }).box;
    near(sofa.size_m[2], 0.85); near(sofa.centre_m[2], 0.425);
    assert.equal(sofa.yaw_deg, 180);                      // the front of four room-aligned turns that faces the viewer
});

test('a desk clicked on its top takes the flat-top fit, front towards the viewer', () => {
    const fit = { center: [0.4, 1.4, 0.75], size: [0.7, 1.4], up: [0, 1, 0] };
    const { box } = L.placeBox({ name: 'desk', hit: { point: [0.5, 1.6, 0.75], normal: [0, 0, 1] }, viewer: [7, -3], fit });
    near(box.size_m[0], 0.7); near(box.size_m[1], 1.4); near(box.size_m[2], 0.75);
    near(box.centre_m[0], 0.4); near(box.centre_m[2], 0.375);
    const [fx, fy] = L.front(box.yaw_deg); assert.ok(fx * 6.6 + fy * -4.4 > 0);
    // The top is the fitted top's height, even when the coarser collision click lands lower.
    const low = L.placeBox({ name: 'coffee table', hit: { point: [2.6, 2.3, 0.17], normal: [0, 0, 1] }, viewer: [7, -3],
        fit: { center: [2.6, 2.35, 0.42], size: [1.0, 0.55], up: [0, 1, 0] } }).box;
    near(low.size_m[2], 0.42); near(low.centre_m[2], 0.21);
    // A fit outside 20 cm to 3 m is ignored: the typical desk size stands at the click instead.
    const wide = L.placeBox({ name: 'desk', hit: { point: [0.5, 1.6, 0.75], normal: [0, 0, 1] }, viewer: [7, -3],
        fit: { center: [2, 2, 0.75], size: [4, 3.5], up: [0, 1, 0] } }).box;
    near(wide.size_m[0], 1.4); near(wide.centre_m[0], 0.5);
});

test('footprint corners and local frames agree', () => {
    const box = { centre_m: [1, 1, 0.5], size_m: [2, 1, 1], yaw_deg: 90 };
    const corners = L.footprint(box);
    near(corners[0][0], 1.5); near(corners[0][1], 0); near(corners[2][0], 0.5); near(corners[2][1], 2);
    const [lx, ly] = L.toLocal(box, ...L.toWorld(box, 0.3, -0.2)); near(lx, 0.3); near(ly, -0.2);
    assert.equal(L.normYaw(-90), 270); assert.equal(L.normYaw(720.04), 0);
});

test('the record: a saved document passes, and each broken field is refused in den-live words', () => {
    assert.equal(L.docProblem(doc()), null);
    assert.equal(L.docProblem(L.emptyDoc()), null);
    assert.equal(L.docProblem({ ...doc(), extra: 1 }), 'key_unknown');
    assert.equal(L.docProblem({ ...doc(), schema: 'x' }), 'schema_wrong');
    assert.equal(L.docProblem({ ...doc(), revision: -1 }), 'revision_invalid');
    assert.equal(L.docProblem(doc([label(), label({ name: 'other desk' })])), 'id_duplicate');
    assert.equal(L.docProblem(doc([label(), label({ id: 'l-desk-2' , name: 'DESK' })])), 'name_duplicate');
    assert.equal(L.docProblem(doc([{ ...label(), name: 'x'.repeat(41) }])), 'name_too_long');
    assert.equal(L.docProblem(doc([{ ...label(), kind: 'robot' }])), 'kind_unknown');
    assert.equal(L.docProblem(doc([{ ...label(), box: { centre_m: [0, 0, 0], size_m: [0, 1, 1], yaw_deg: 0 } }])), 'box_invalid');
    assert.equal(L.docProblem(doc(Array.from({ length: 101 }, (_, i) => label({ id: `l-x-${i}`, name: `x ${i}` })))), 'too_many_labels');
});

test('check these: labels from another scan, never for an empty or unsaved document', () => {
    assert.equal(L.sameScan(doc(), 'rev-a'), true);
    assert.equal(L.sameScan(doc(), 'rev-b'), false);
    assert.equal(L.sameScan(L.emptyDoc('rev-a'), 'rev-b'), true);
    assert.equal(L.sameScan(null, 'rev-b'), true);
});

test('the checklist counts names that end with an item', () => {
    const done = L.checklistDone([label({ name: 'blue painting' }), label({ id: 'l-s-1', name: 'sofa' }), label({ id: 'l-t-1', name: 'TV stand' })]);
    assert.deepEqual([...done].sort(), ['TV', 'painting', 'sofa']);
});
