// The live den view's people layer (app/src/home-3d/apartment-people-layer.mjs): markers from den-live-people-1 events.
//   node --test tools/test-apartment-people-layer.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { register } from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fileUrl = relative => pathToFileURL(path.join(ROOT, 'app/src', relative)).href;
const threeURL = fileUrl('vendor/three-0.180.0/three.module.js');
// The browser's importmap, for Node: bare 'three' is the vendored r180 build.
register('data:text/javascript,' + encodeURIComponent(`export async function resolve(specifier, context, next) {
  if (specifier === 'three') return { url: ${JSON.stringify(threeURL)}, shortCircuit: true };
  return next(specifier, context);
}`));
const THREE = await import(threeURL);
const { attachApartmentPeopleLayer, validPeopleEvent, PEOPLE_SCHEMA } = await import(fileUrl('home-3d/apartment-people-layer.mjs'));

function fixture() {
    const apartmentRoot = new THREE.Group(), clock = { t: 100 };
    const layer = attachApartmentPeopleLayer({ apartmentRoot }, { now: () => clock.t, animate: false });
    const advance = (seconds, steps = Math.max(1, Math.round(seconds / .02))) => {
        for (let i = 0; i < steps; i++) { clock.t += seconds / steps; layer.tick(seconds / steps); }
    };
    const event = (tracks, extra = {}) => ({ schema: PEOPLE_SCHEMA, home: 'den', seq: 1, capture_ts: 1, emitted_ts: 1.4, mode: 'rgb',
        camera: { status: 'ok', why: null }, health: { fps_in: 4.9, dropped_per_min: 0, lag_s_median: .4 }, tracks, ...extra });
    const track = (id, xy, extra = {}) => ({ track_id: id, state: 'confirmed', xy_m: xy, sigma_m: .1, heading_deg: null, age_s: 0, ...extra });
    return { apartmentRoot, layer, clock, advance, event, track, root: () => apartmentRoot.getObjectByName('home-apartment-people-owned') };
}

test('the layer is its own group under the apartment root, and dispose removes it', () => {
    const f = fixture();
    assert.ok(f.root());
    assert.equal(f.root().userData.noLightingAuthority, true);
    f.layer.dispose();
    assert.equal(f.root(), undefined);
    assert.equal(f.layer.update(f.event([f.track('trk-1', [1, 2])])), false, 'a disposed layer draws nothing');
});

test('a marker springs to its track and stays on the floor', () => {
    const f = fixture();
    assert.equal(f.layer.update(f.event([f.track('trk-1', [1, 2])])), true);
    f.layer.update(f.event([f.track('trk-1', [2, 2])]));
    f.advance(.2);
    const early = f.layer.markers()[0].xy_m[0];
    assert.ok(early > 1 && early < 2, `the spring is under way, not a jump (${early})`);
    for (let i = 0; i < 10; i++) { f.layer.update(f.event([f.track('trk-1', [2, 2])])); f.advance(.2); }
    assert.ok(Math.abs(f.layer.markers()[0].xy_m[0] - 2) < .01);
    assert.equal(f.root().getObjectByName('person-trk-1').position.z, 0);
});

test('a second person is a second marker', () => {
    const f = fixture();
    f.layer.update(f.event([f.track('trk-1', [1, 1]), f.track('trk-2', [3, 1])]));
    assert.deepEqual(f.layer.markers().map(m => m.track_id).sort(), ['trk-1', 'trk-2']);
});

test('the heading arrow points along the bearing, clockwise from +y', () => {
    const f = fixture();
    f.layer.update(f.event([f.track('trk-1', [0, 0], { heading_deg: 90 })]));
    f.advance(.02);
    const pivot = f.root().getObjectByName('person-trk-1').children[2];
    assert.equal(pivot.visible, true);
    const tip = new THREE.Vector3(0, 1, 0).applyAxisAngle(new THREE.Vector3(0, 0, 1), pivot.rotation.z);
    assert.ok(Math.abs(tip.x - 1) < 1e-9 && Math.abs(tip.y) < 1e-9, 'a bearing of 90 points at +x');
    f.layer.update(f.event([f.track('trk-1', [0, 0], { heading_deg: null })]));
    f.advance(.02);
    assert.equal(pivot.visible, false, 'no heading, no arrow');
});

test('coasting dims, and a track gone from the stream fades and is removed', () => {
    const f = fixture();
    f.layer.update(f.event([f.track('trk-1', [1, 1], { state: 'coasting' })]));
    f.advance(.02);
    const disc = f.root().getObjectByName('person-trk-1').children[0];
    assert.ok(disc.material.opacity < .5, 'coasting is dimmed');
    f.layer.update(f.event([]));
    f.advance(1.5);
    assert.equal(f.layer.markers().length, 1, 'still fading');
    f.advance(1.2);
    assert.equal(f.layer.markers().length, 0, 'gone 2.5 s after it left');
    assert.equal(f.root().children.length, 0, 'its trail went with it');
});

test('the trail keeps 10 s at most', () => {
    const f = fixture();
    for (let i = 0; i < 80; i++) { f.layer.update(f.event([f.track('trk-1', [i * .05, 0])])); f.advance(.2, 2); }
    const trail = f.layer.markers()[0].trail;
    assert.ok(trail <= 50 && trail >= 45, `about 10 s of points (${trail})`);
});

test('an event that is not den-live-people-1 draws nothing', () => {
    const f = fixture();
    for (const bad of [null, {}, { schema: 'other', tracks: [] }, f.event([f.track('trk-1', [Number.NaN, 0])]),
        f.event([f.track('trk-1', [1, 1], { state: 'tentative' })]), f.event([f.track('', [1, 1])]), f.event([f.track('trk-1', [1e6, 0])])]) {
        assert.equal(validPeopleEvent(bad), false);
        assert.equal(f.layer.update(bad), false);
    }
    assert.equal(f.layer.markers().length, 0);
});

test('the status says what the stream said, and how long ago', () => {
    const f = fixture();
    f.layer.update(f.event([], { mode: 'none', camera: { status: 'ok', why: 'too dark' } }));
    f.advance(3);
    const status = f.layer.status();
    assert.equal(status.mode, 'none');
    assert.equal(status.camera.why, 'too dark');
    assert.ok(Math.abs(status.since_s - 3) < 1e-6);
});

test("each marker keeps what's near it from den-live's labels, words only", () => {
    const f = fixture();
    f.layer.update(f.event([f.track('t-1', [1, 2], { near: 'at the desk, beside the 3D printer' }), f.track('t-2', [3, 3]),
        f.track('t-3', [0, 0], { near: 'x'.repeat(161) })]));
    const near = Object.fromEntries(f.layer.markers().map(m => [m.track_id, m.near]));
    assert.deepEqual(near, { 't-1': 'at the desk, beside the 3D printer', 't-2': null, 't-3': null });
    f.layer.update(f.event([f.track('t-1', [1, 2], { near: null })]));
    assert.equal(f.layer.markers().find(m => m.track_id === 't-1').near, null);
});
