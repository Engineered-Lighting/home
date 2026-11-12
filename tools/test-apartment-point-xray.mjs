// The x-ray Cloud look: per-point features (point-features.mjs) on synthetic geometry, and the
// materials' contracts with the classic cloud, the lit layer and modes.js. No GPU, browser or
// household data: the geometry is a generated room.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { register } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SRC = path.join(ROOT, 'app/src');
const fileUrl = relative => pathToFileURL(path.join(SRC, relative)).href;
const threeURL = fileUrl('vendor/three-0.180.0/three.module.js');
register('data:text/javascript,' + encodeURIComponent(`export async function resolve(specifier, context, next) {
  if (specifier === 'three') return { url: ${JSON.stringify(threeURL)}, shortCircuit: true };
  return next(specifier, context);
}`));
const THREE = await import(threeURL);
globalThis.window ??= { devicePixelRatio: 1 };
const Features = await import(fileUrl('home-3d/point-features.mjs'));
const Pointcloud = await import(fileUrl('home-3d/pointcloud.js'));

// A floor (z = 0.3) from x = -0.6 to 1.0, sampled every 1 cm with 1 mm noise. An outer wall at x = 1.0
// (the room is on its -x side) and a partition at x = 0.0 (floor on both sides), each 1 m tall.
function room() {
  let seed = 7;
  const rand = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648 - 0.5) * 0.002;
  const pos = [], inten = [], kind = [], Z0 = 0.3;
  for (let i = -60; i < 100; i++) for (let j = 0; j <= 100; j++) {
    // floor within a 10 cm block of a wall blends the two normals by design: those are 'corner'
    pos.push(i / 100 + rand(), j / 100 + rand(), Z0 + rand()); inten.push(150); kind.push(Math.abs(i) < 12 || i > 87 ? 'corner' : 'floor');
  }
  for (const [x, name] of [[1.0, 'outer'], [0.0, 'partition']]) for (let k = 1; k <= 100; k++) for (let j = 0; j <= 100; j++) {
    pos.push(x + rand(), j / 100 + rand(), Z0 + k / 100 + rand()); inten.push(150);
    // probes reach 50 cm; keep the wall body clear of the room's open ends
    kind.push(k > 25 && j > 15 && j < 85 ? name : 'edge');
  }
  return { positions: Float32Array.from(pos), intensity: Uint8Array.from(inten), kind, Z0 };
}

const share = (kind, want, test) => {
  let n = 0, hit = 0;
  for (let i = 0; i < kind.length; i++) if (kind[i] === want) { n++; if (test(i)) hit++; }
  return hit / n;
};

test('features: normals, spacing, the floor height, and which side of each wall the room is on', () => {
  const { positions, intensity, kind, Z0 } = room();
  const n = intensity.length;
  const f = Features.computePointFeatures(positions, intensity, n);
  assert.equal(f.normal.length, n * 3);
  assert.equal(f.spacing.length, n);
  assert.equal(f.inside.length, n);
  assert.ok(Math.abs(f.floorZ - Z0) < 0.02, `floor found near ${Z0} (${f.floorZ.toFixed(3)})`);
  assert.ok(share(kind, 'floor', i => Math.abs(f.normal[i * 3 + 2]) > 120) > 0.95, 'floor normals point up');
  assert.ok(share(kind, 'outer', i => Math.abs(f.normal[i * 3]) > 120) > 0.95, 'wall normals point out of the wall');
  // the outer wall's room is on its -x side: sign(inside) along the normal points toward -x
  assert.ok(share(kind, 'outer', i => f.inside[i] !== 0 && Math.sign(f.inside[i]) * f.normal[i * 3] < 0) > 0.9,
    'the outer wall knows its room is on the -x side');
  assert.ok(share(kind, 'partition', i => f.inside[i] === 0) > 0.9, 'a partition (floor on both sides) has no inside');
  assert.ok(share(kind, 'floor', i => f.inside[i] === 0) === 1, 'floors are never walls');
  assert.ok(share(kind, 'corner', i => f.inside[i] === 0 || Math.abs(f.normal[i * 3 + 2]) < 52) === 1, 'only wall-facing points get an inside');
  let s = 0, c = 0; for (let i = 0; i < n; i++) if (kind[i] === 'floor') { s += f.spacing[i]; c++; }
  assert.ok(s / c > 6 && s / c < 16, `spacing near the 10 mm sampling (${(s / c).toFixed(1)} mm)`);
  const again = Features.computePointFeatures(positions, intensity, n);
  assert.deepEqual(again.inside, f.inside, 'deterministic');
});

test('features: empty and short inputs', () => {
  const empty = Features.computePointFeatures(new Float32Array(0), new Uint8Array(0), 0);
  assert.equal(empty.spacing.length, 0);
  assert.equal(empty.inside.length, 0);
  const one = Features.computePointFeatures(new Float32Array([1, 2, 3]), new Uint8Array([9]), 5);
  assert.equal(one.spacing.length, 1);
  assert.ok(one.spacing[0] >= 1);
  assert.equal(one.inside[0], 0);
});

test('smallestEigen finds the normal of a flat covariance', () => {
  const [l, x, y, z] = Features.smallestEigen(1, 1, 0.0001, 0, 0, 0);
  assert.ok(Math.abs(l - 0.0001) < 1e-9);
  assert.ok(Math.abs(Math.abs(z) - 1) < 1e-6 && Math.abs(x) < 1e-6 && Math.abs(y) < 1e-6);
});

test('x-ray copies share the classic per-frame uniforms, add light and never touch depth', () => {
  const base = Pointcloud.createPointsMaterial();
  const { copies, composite } = Pointcloud.createXrayMaterials(base);
  assert.equal(copies.length, Pointcloud.XRAY_COPIES);
  assert.deepEqual(copies.map(m => m.uniforms.uCopy.value), [...Array(Pointcloud.XRAY_COPIES).keys()]);
  for (const material of copies) {
    for (const name of ['uPixelRatio', 'uFadeNear', 'uFadeFar', 'uWallNear', 'uOpacity', 'uDollhouseConfig', 'uDollhouseWorldToLocal']) {
      assert.equal(material.uniforms[name], base.uniforms[name], `${name} is the classic uniform object`);
    }
    assert.equal(material.uniforms.uPlanCut, copies[0].uniforms.uPlanCut, 'one cut state for all copies');
    assert.equal(material.depthWrite, false);
    assert.equal(material.depthTest, false);
    assert.equal(material.userData.noDepthWrite, true);
    assert.equal(material.blendEquation, THREE.AddEquation);
    assert.equal(material.blendDst, THREE.OneFactor, 'light adds up');
  }
  assert.ok(copies[0].vertexShader.includes('vDollhouseLocal = '), 'Home’s cutaway applies');
  assert.ok(copies[0].fragmentShader.includes('homeDollhouseKeep()'), 'Home’s cutaway applies');
  assert.ok(copies[0].vertexShader.includes('aInside') && copies[0].vertexShader.includes('uFloorZ'), 'the floor-plan cut applies');
  assert.equal(composite.uniforms.uOpacity, base.uniforms.uOpacity, 'the Cloud/Photo crossfade fades the composite');
  assert.equal(composite.blendDst, THREE.OneMinusSrcAlphaFactor, 'premultiplied over the page');
  assert.equal(composite.depthTest, false);
});

test('the classic shaders and the lit layer are unchanged by the x-ray look', () => {
  const base = Pointcloud.createPointsMaterial();
  assert.ok(!Pointcloud.vertexShader.includes('aSpacing') && !Pointcloud.vertexShader.includes('aInside'));
  assert.ok(Pointcloud.fragmentShader.includes('gl_FragColor = vec4(uGlowColor * brightness, glow * uOpacity * cutawayKeep);'));
  const texture = new THREE.DataTexture(new Uint8Array(4), 1, 1);
  const lit = Pointcloud.createLitPointsMaterial(base, { texture, width: 1 });
  assert.ok(lit.vertexShader.includes('vLitColor = texelFetch'));
  assert.equal(lit.uniforms.uTime, base.uniforms.uTime);
});

test('x-ray gain keeps exposure independent of window size, within bounds', () => {
  assert.equal(Pointcloud.xrayGainForViewport(1148, 754), Pointcloud.XRAY_GAIN);
  assert.ok(Pointcloud.xrayGainForViewport(574, 377) < Pointcloud.XRAY_GAIN);
  assert.ok(Pointcloud.xrayGainForViewport(2296, 1508) > Pointcloud.XRAY_GAIN);
  assert.equal(Pointcloud.xrayGainForViewport(1, 1), Pointcloud.XRAY_GAIN * 0.15);
  assert.equal(Pointcloud.xrayGainForViewport(100000, 100000), Pointcloud.XRAY_GAIN * 4);
  assert.equal(Pointcloud.xrayGainForViewport(NaN, 754), Pointcloud.XRAY_GAIN);
});

test('x-ray needs WebGL2 with a float colour buffer', () => {
  const ext = names => ({ has: n => names.includes(n) });
  assert.equal(Pointcloud.xraySupported({ capabilities: { isWebGL2: true }, extensions: ext(['EXT_color_buffer_float']) }), true);
  assert.equal(Pointcloud.xraySupported({ capabilities: { isWebGL2: true }, extensions: ext(['EXT_color_buffer_half_float']) }), true);
  assert.equal(Pointcloud.xraySupported({ capabilities: { isWebGL2: true }, extensions: ext([]) }), false);
  assert.equal(Pointcloud.xraySupported({ capabilities: { isWebGL2: false }, extensions: ext(['EXT_color_buffer_float']) }), false);
  assert.equal(Pointcloud.xraySupported(null), false);
});

test('opt-out is per browser and tolerant of storage failures', () => {
  assert.equal(Pointcloud.xrayLookOptedOut({ getItem: () => 'classic' }), true);
  assert.equal(Pointcloud.xrayLookOptedOut({ getItem: () => null }), false);
  assert.equal(Pointcloud.xrayLookOptedOut({ getItem: () => { throw new Error('denied'); } }), false);
  assert.equal(Pointcloud.xrayLookOptedOut(null), false);
});

test('modes.js keeps the x-ray copies out of the depth buffer; the engine renders the light first', () => {
  const modes = fs.readFileSync(path.join(SRC, 'home-3d/modes.js'), 'utf8');
  assert.ok(modes.includes('material.depthWrite = material.userData?.noDepthWrite ? false : depthWrite;'));
  const engine = fs.readFileSync(path.join(SRC, 'home-3d/engine.js'), 'utf8');
  const prepasses = engine.match(/xrayLook\?\.prepass\(\);\n\s*renderer\.render\(scene, camera\);/g) || [];
  assert.equal(prepasses.length, 2, 'the frame loop and the capture both accumulate before rendering');
});
