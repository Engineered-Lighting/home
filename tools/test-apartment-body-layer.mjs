// The live body's Recording layer (app/src/home-3d/apartment-body-layer.mjs): den-live-body-1 frames drawn in the
// apartment view.
//   node --test tools/test-apartment-body-layer.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { register } from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { measuredBodyEvent } from './fixtures/den-measured-body.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fileUrl = relative => pathToFileURL(path.join(ROOT, 'app/src', relative)).href;
const threeURL = fileUrl('vendor/three-0.180.0/three.module.js');
// The browser's importmap, for Node: bare 'three' is the vendored r180 build.
register('data:text/javascript,' + encodeURIComponent(`export async function resolve(specifier, context, next) {
  if (specifier === 'three') return { url: ${JSON.stringify(threeURL)}, shortCircuit: true };
  return next(specifier, context);
}`));
const THREE = await import(threeURL);
const { attachApartmentBodyLayer } = await import(fileUrl('home-3d/apartment-body-layer.mjs'));
const { decodeLiveBodyEvent, encodeBodyFrame, KIND } = await import(fileUrl('home-3d/apartment-body-frame.mjs'));

function fixture(options = {}) {
    const apartmentRoot = new THREE.Group(), camera = new THREE.PerspectiveCamera(35, 1.5, 0.1, 100), clock = { t: 100 };
    camera.position.set(0, -6, 3); camera.lookAt(0, 0, 1); camera.updateMatrixWorld();
    const layer = attachApartmentBodyLayer({ apartmentRoot, camera }, { now: () => clock.t, animate: false, ...options });
    const advance = (seconds, steps = Math.max(1, Math.round(seconds / 0.02))) => { for (let i = 0; i < steps; i++) { clock.t += seconds / steps; layer.tick(seconds / steps); } };
    let ts = 10;
    const frame = (points, extra = {}) => encodeBodyFrame({ trackId: 'trk-1', captureTs: (ts += 0.1), points, ...extra });
    const body = (dx = 0, extra = []) => [...Array.from({ length: 30 }, (_, i) => ({ key: i + 1, xyz_m: [dx + 0.01 * (i % 5), 0, 0.2 + i * 0.05], kind: KIND.measured })), ...extra];
    const group = () => apartmentRoot.getObjectByName('home-apartment-body-owned');
    const drawn = name => group().getObjectByName(name).geometry.drawRange.count;
    return { apartmentRoot, layer, clock, advance, frame, body, group, drawn };
}

test('the layer is its own group under the apartment root, with no lighting authority, and dispose removes it', () => {
    const f = fixture();
    assert.ok(f.group());
    assert.equal(f.group().userData.noLightingAuthority, true);
    f.layer.dispose();
    assert.equal(f.group(), undefined);
    assert.equal(f.layer.update(f.frame(f.body())), false, 'a disposed layer draws nothing');
});

test('frames draw, fade in, and an off-contract or stale frame is ignored', () => {
    const f = fixture();
    assert.equal(f.layer.update({ schema: 'den-live-body-1' }), false);
    assert.equal(f.layer.update(f.frame(f.body())), true);
    f.advance(0.05);
    assert.equal(f.drawn('body-recording'), 30);
    const presence = f.layer.status().presence;
    f.advance(1);
    assert.ok(f.layer.status().presence > presence && f.layer.status().presence > 0.9, 'fades in over about a second');
    const stale = encodeBodyFrame({ trackId: 'trk-1', captureTs: 1, points: f.body() });
    assert.equal(f.layer.update(stale), false, 'older than the newest frame');
});

test('two frames interpolate by key, and the previous frames become the trails', () => {
    const f = fixture();
    f.layer.update(f.frame(f.body(0))); f.advance(0.15);
    f.layer.update(f.frame(f.body(1))); f.advance(0.15 * 0.5);
    const x = f.group().getObjectByName('body-recording').geometry.attributes.position.array[0];
    assert.ok(x > 0.05 && x < 0.95, `halfway-ish between frames: ${x}`);
    f.advance(0.3);
    assert.ok(Math.abs(f.group().getObjectByName('body-recording').geometry.attributes.position.array[0] - 1) < 1e-6, 'arrives at the newest');
    assert.equal(f.drawn('body-trail-1'), 30);
    assert.equal(f.drawn('body-trail-2'), 0, 'only two frames so far');
});

test('the held object rides along, is reported for its tag, and stays out of the body centroid', () => {
    const f = fixture();
    const book = Array.from({ length: 30 }, (_, i) => ({ key: 1000 + i, xyz_m: [0.4 + 0.005 * i, -0.2, 0.9], kind: KIND.heldObject }));
    f.layer.update(f.frame(f.body(0, book), { object: { label: 'Book', confidence: 0.9 } })); f.advance(0.2);
    const snap = f.layer.snapshot();
    assert.equal(snap.object.label, 'Book');
    assert.equal(snap.objectPoints.length, 10, 'every third object point');
    assert.ok(Math.abs(snap.centroid[0] - 0.02) < 1e-3, 'centroid from the body points only');
    const mat = f.group().getObjectByName('body-held-object').material;
    assert.equal(mat.blending, THREE.NormalBlending, 'the object is steady and solid, not additive light');
});

test('a person who leaves the stream fades out and is cleared; a new track replaces the old', () => {
    const f = fixture();
    f.layer.update(f.frame(f.body())); f.advance(1);
    f.advance(4);
    assert.ok(f.layer.status().presence < 0.02);
    assert.equal(f.layer.status().trackId, null);
    assert.equal(f.drawn('body-recording'), 0);
    f.layer.update(f.frame(f.body())); f.advance(0.2);
    f.layer.update(encodeBodyFrame({ trackId: 'trk-2', captureTs: 99, points: f.body(2) })); f.advance(0.2);
    assert.equal(f.layer.status().trackId, 'trk-2');
    assert.equal(f.layer.status().frames, 1);
});

test('reduced motion: no flicker, no glitch band, no trails', () => {
    const f = fixture({ reducedMotion: true });
    f.layer.update(f.frame(f.body(0))); f.advance(0.2); f.layer.update(f.frame(f.body(1))); f.advance(1);
    const holo = f.group().getObjectByName('body-recording').material.uniforms;
    assert.equal(holo.uFlicker.value, 1);
    assert.equal(holo.uGlitch.value.x, 9);
    assert.equal(f.group().getObjectByName('body-trail-1').material.uniforms.uOpacity.value, 0);
});

test('zoomed out, points grow at most 1.25x and the glow eases off', () => {
    const near = fixture({ zoomT: () => 0 }), far = fixture({ zoomT: () => 1 });
    for (const f of [near, far]) { f.layer.update(f.frame(f.body())); f.advance(1.5); }
    const u = f => f.group().getObjectByName('body-recording').material.uniforms, glow = f => f.group().getObjectByName('body-glow').material.uniforms.uOpacity.value;
    assert.equal(u(near).uSizeMul.value, 1);
    assert.equal(u(far).uSizeMul.value, 1.25);
    assert.ok(glow(far) < glow(near) * 0.45);
    near.layer.setPool(false); near.advance(0.1);
    assert.equal(near.group().getObjectByName('body-floor-pool').material.uniforms.uOpacity.value, 0, 'the floor pool can be turned off (Lighting view)');
});

test('the measured WebGL figure uses only the newest samples and clears in the same turn', () => {
    const f = fixture({ measuredOnly: true, reducedMotion: true });
    assert.equal(f.layer.update(f.frame(f.body())), false);
    const original = decodeLiveBodyEvent(measuredBodyEvent()).decoded;
    const next = decodeLiveBodyEvent(measuredBodyEvent({ seq: 2, x: 2.2 })).decoded;
    f.layer.updateMeasured(original);
    f.advance(0.01);
    f.layer.updateMeasured(next);
    f.advance(0.01);
    const recording = f.group().getObjectByName('body-recording');
    assert.deepEqual(f.group().children.filter(child => child.visible), [recording]);
    assert.equal(f.group().userData.excludeFromApartmentCapture, true);
    assert.equal(recording.material.uniforms.uMeasuredOnly.value, true);
    assert.equal(recording.material.uniforms.uTime.value, 0);
    assert.equal(recording.geometry.attributes.position.array[0], next.pos[0]);
    assert.equal(recording.geometry.drawRange.count, next.n);
    f.layer.clear();
    assert.equal(recording.geometry.drawRange.count, 0);
    assert.equal(f.layer.status().points, 0);
    assert.equal(f.layer.snapshot().centroid, null);
    assert.equal(f.layer.snapshot().torso, null);
});
