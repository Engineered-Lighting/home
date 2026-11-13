// The live body on the WebGPU path (?renderer=webgpu): the layer hides its GLSL and draws the TSL twin
// (app/src/home-3d/apartment-body-webgpu.mjs) from the same buffers and uniforms. Here 'three' is the r186 WebGPU build
// and 'three/tsl' its node library, as the page's WebGPU importmap makes them. No GPU: the node graphs are built, not run.
//   node --test tools/test-apartment-body-webgpu.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { register } from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { measuredBodyEvent } from './fixtures/den-measured-body.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fileUrl = relative => pathToFileURL(path.join(ROOT, 'app/src', relative)).href;
const r186 = 'vendor/three-0.186.1/';
const map = { three: fileUrl(r186 + 'three.webgpu.js'), 'three/webgpu': fileUrl(r186 + 'three.webgpu.js'), 'three/tsl': fileUrl(r186 + 'three.tsl.js') };
register('data:text/javascript,' + encodeURIComponent(`const map = ${JSON.stringify(map)};
export async function resolve(specifier, context, next) {
  if (map[specifier]) return { url: map[specifier], shortCircuit: true };
  return next(specifier, context);
}`));
const THREE = await import(map.three);
const { attachApartmentBodyLayer } = await import(fileUrl('home-3d/apartment-body-layer.mjs'));
const { decodeLiveBodyEvent, encodeBodyFrame, KIND } = await import(fileUrl('home-3d/apartment-body-frame.mjs'));

async function webgpuLayer() {
    const apartmentRoot = new THREE.Group(), camera = new THREE.PerspectiveCamera(35, 1.5, 0.1, 100), clock = { t: 100 };
    camera.position.set(0, -6, 3); camera.lookAt(0, 0, 1); camera.updateMatrixWorld();
    const renderer = { isWebGPURenderer: true, getDrawingBufferSize: v => v.set(1200, 800) };
    const layer = attachApartmentBodyLayer({ apartmentRoot, camera, renderer }, { now: () => clock.t, animate: false });
    const group = apartmentRoot.getObjectByName('home-apartment-body-owned');
    for (let i = 0; i < 50 && !layer.status().webgpu; i++) await new Promise(r => setTimeout(r, 20));   // the twin loads on its own
    const advance = seconds => { for (let i = 0; i < Math.round(seconds / 0.02); i++) { clock.t += 0.02; layer.tick(0.02); } };
    return { layer, group, advance };
}
const points = (n, kind = KIND.measured, key0 = 1) => Array.from({ length: n }, (_, i) => ({ key: key0 + i, xyz_m: [0.01 * (i % 5), 0, 0.2 + i * 0.03], kind, normal: [0, -1, 0] }));

test('on the node renderer the GLSL objects are hidden and the twin draws instead', async () => {
    const { layer, group } = await webgpuLayer();
    assert.equal(layer.status().webgpu, true, 'the twin loaded');
    const visible = group.children.filter(o => o.visible);
    assert.deepEqual(visible.map(o => o.name).sort(), ['body-held-object-webgpu', 'body-recording-webgpu']);
    for (const o of visible) {
        assert.ok(o.isSprite, `${o.name} is an instanced sprite`);
        assert.ok(o.material.isNodeMaterial && o.material.vertexNode && o.material.fragmentNode, `${o.name} is built from nodes`);
        assert.equal(o.frustumCulled, false);
    }
    assert.ok(!group.children.some(o => o.visible && o.material?.isShaderMaterial), 'no GLSL is drawn');
    layer.dispose();
});

test('the twin draws this frame\'s points, with the Recording look additive and the held object solid', async () => {
    const { layer, group, advance } = await webgpuLayer();
    layer.update(encodeBodyFrame({ trackId: 'trk-1', captureTs: 1, points: [...points(30), ...points(12, KIND.heldObject, 1000)] }));
    advance(0.5);
    const recording = group.getObjectByName('body-recording-webgpu'), held = group.getObjectByName('body-held-object-webgpu');
    assert.equal(recording.count, 42);
    assert.equal(held.count, 42, 'the held-object sprite reads the same points and keeps only kind 5');
    assert.equal(recording.material.blending, THREE.AdditiveBlending);
    assert.equal(held.material.blending, THREE.NormalBlending);
    assert.equal(held.material.depthWrite, true);
    assert.ok(layer.status().presence > 0.5, 'it fades in as on WebGL');
    layer.dispose();
    assert.equal(group.parent, null, 'dispose removes the layer');
});

test('a renderer that isn\'t the node renderer keeps the WebGL objects and loads no twin', async () => {
    const apartmentRoot = new THREE.Group(), camera = new THREE.PerspectiveCamera(35, 1.5, 0.1, 100);
    const layer = attachApartmentBodyLayer({ apartmentRoot, camera, renderer: {} }, { animate: false });
    await new Promise(r => setTimeout(r, 100));
    const group = apartmentRoot.getObjectByName('home-apartment-body-owned');
    assert.equal(layer.status().webgpu, false);
    assert.ok(group.getObjectByName('body-recording').visible);
    assert.equal(group.getObjectByName('body-recording-webgpu'), undefined);
    layer.dispose();
});

test('measured WebGPU instance count clears immediately even if the twin loads late', async () => {
    const apartmentRoot = new THREE.Group(), camera = new THREE.PerspectiveCamera();
    const layer = attachApartmentBodyLayer({ apartmentRoot, camera, renderer: { isWebGPURenderer: true } }, { animate: false, measuredOnly: true });
    const { decoded } = decodeLiveBodyEvent(measuredBodyEvent());
    const group = apartmentRoot.getObjectByName('home-apartment-body-owned');
    assert.equal(layer.status().webgpu, false);
    layer.updateMeasured(decoded);
    layer.clear();
    for (let i = 0; i < 50 && !layer.status().webgpu; i++) await new Promise(resolve => setTimeout(resolve, 20));
    const recording = group.getObjectByName('body-recording-webgpu');
    assert.equal(recording.count, 0);
    assert.deepEqual(group.children.filter(child => child.visible), [recording]);
    layer.updateMeasured(decoded);
    layer.tick(0.016);
    assert.equal(recording.count, decoded.n);
    layer.clear();
    assert.equal(recording.count, 0);
    assert.equal(layer.status().points, 0);
    layer.dispose();
});
