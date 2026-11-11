// The etched Cloud look: per-point features (point-features.mjs) on synthetic geometry, and the
// materials' contracts with the classic cloud, the lit layer and modes.js. No GPU, browser or
// household data: the geometry is a generated corner.
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

// A floor (z = 0) meeting a wall (x = 0) along y, sampled every 1 cm with 1 mm noise, and a dark
// stripe painted across the floor at x = 0.6.
function corner() {
  let seed = 7;
  const rand = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648 - 0.5) * 0.002;
  const pos = [], inten = [], kind = [];
  for (let i = 0; i <= 100; i++) for (let j = 0; j <= 100; j++) {
    const x = i / 100, y = j / 100;
    pos.push(x + rand(), y + rand(), rand());
    const stripe = x > 0.595 && x < 0.625;
    inten.push(stripe ? 20 : 150);
    kind.push(x < 0.04 ? 'crease' : stripe ? 'stripe' : (x > 0.25 && x < 0.5) || x > 0.75 ? 'floor' : 'other');
  }
  for (let k = 1; k <= 100; k++) for (let j = 0; j <= 100; j++) {
    const z = k / 100, y = j / 100;
    pos.push(rand(), y + rand(), z + rand());
    inten.push(150);
    kind.push(z < 0.04 ? 'crease' : z > 0.25 ? 'wall' : 'other');
  }
  return { positions: Float32Array.from(pos), intensity: Uint8Array.from(inten), kind };
}

const mean = (values, kind, want) => {
  let s = 0, n = 0;
  for (let i = 0; i < values.length; i++) if (kind[i] === want) { s += values[i]; n++; }
  return s / n;
};

test('features: normals follow each plane, edges light the crease and the stripe, spacing matches sampling', () => {
  const { positions, intensity, kind } = corner();
  const n = intensity.length;
  const f = Features.computePointFeatures(positions, intensity, n);
  assert.equal(f.normal.length, n * 3);
  assert.equal(f.edge.length, n);
  assert.equal(f.spacing.length, n);
  const nz = [], nx = [];
  for (let i = 0; i < n; i++) { nz.push(Math.abs(f.normal[i * 3 + 2]) / 127); nx.push(Math.abs(f.normal[i * 3]) / 127); }
  assert.ok(mean(nz, kind, 'floor') > 0.97, 'floor normals point up');
  assert.ok(mean(nx, kind, 'wall') > 0.97, 'wall normals point out of the wall');
  const edge = Array.from(f.edge, e => e / 255);
  const plain = Math.max(mean(edge, kind, 'floor'), mean(edge, kind, 'wall'));
  assert.ok(mean(edge, kind, 'crease') > 0.5, `the crease is lit (${mean(edge, kind, 'crease').toFixed(2)})`);
  // contrast lights less than shape (a photo scan's intensity carries baked light), but it does light
  const stripe = mean(edge, kind, 'stripe');
  assert.ok(stripe > 0.15 && stripe <= Features.POINT_FEATURE_CONTRAST_WEIGHT + 0.01, `the painted stripe is lit, softly (${stripe.toFixed(2)})`);
  assert.ok(stripe < mean(edge, kind, 'crease'), 'a printed edge lights less than a corner');
  assert.ok(plain < 0.1, `plain surfaces stay dark (${plain.toFixed(2)})`);
  // tone is the intensity averaged over ~10 cm: quiet on plain floor, darker over the stripe
  const tone = Array.from(f.tone);
  assert.equal(tone.length, n);
  assert.ok(Math.abs(mean(tone, kind, 'floor') - 150) < 2, `plain tone follows the scan (${mean(tone, kind, 'floor').toFixed(1)})`);
  assert.ok(mean(tone, kind, 'stripe') < mean(tone, kind, 'floor') - 20, `the 3 cm stripe darkens its tone, softened over ~10 cm (${mean(tone, kind, 'stripe').toFixed(1)})`);
  const spacing = mean(Array.from(f.spacing), kind, 'floor');
  assert.ok(spacing > 6 && spacing < 16, `spacing near the 10 mm sampling (${spacing.toFixed(1)} mm)`);
  // deterministic
  const again = Features.computePointFeatures(positions, intensity, n);
  assert.deepEqual(again.edge, f.edge);
});

test('features: empty and short inputs', () => {
  const empty = Features.computePointFeatures(new Float32Array(0), new Uint8Array(0), 0);
  assert.equal(empty.edge.length, 0);
  assert.equal(empty.tone.length, 0);
  const one = Features.computePointFeatures(new Float32Array([1, 2, 3]), new Uint8Array([9]), 5);
  assert.equal(one.edge.length, 1);
  assert.equal(one.spacing[0] >= 1, true);
});

test('smallestEigen finds the normal of a flat covariance', () => {
  const [l, x, y, z] = Features.smallestEigen(1, 1, 0.0001, 0, 0, 0);
  assert.ok(Math.abs(l - 0.0001) < 1e-9);
  assert.ok(Math.abs(Math.abs(z) - 1) < 1e-6 && Math.abs(x) < 1e-6 && Math.abs(y) < 1e-6);
});

test('etched materials share the classic per-frame uniforms and never write colour depth', () => {
  const base = Pointcloud.createPointsMaterial();
  const { color, depth } = Pointcloud.createEtchedPointsMaterials(base);
  for (const name of ['uTime', 'uPixelRatio', 'uFadeNear', 'uFadeFar', 'uWallNear', 'uOpacity', 'uDollhouseConfig', 'uDollhouseWorldToLocal']) {
    assert.equal(color.uniforms[name], base.uniforms[name], `${name} is the classic uniform object`);
  }
  assert.equal(depth.uniforms.uWallNear, base.uniforms.uWallNear);
  assert.equal(color.depthWrite, false);
  assert.equal(color.userData.additive, true);
  assert.equal(color.blending, THREE.CustomBlending);
  assert.equal(depth.colorWrite, false);
  assert.equal(depth.depthWrite, true);
  assert.ok(color.vertexShader.includes('homeDollhouse') || color.fragmentShader.includes('homeDollhouseKeep'), 'the cutaway applies');
  assert.ok(depth.fragmentShader.includes('homeDollhouseKeep'), 'the depth pass honours the cutaway');
});

test('the lit layer relights from the classic cloud when the etched look is on', () => {
  const base = Pointcloud.createPointsMaterial();
  const { color } = Pointcloud.createEtchedPointsMaterials(base);
  const texture = new THREE.DataTexture(new Uint8Array(4), 1, 1);
  const lit = Pointcloud.createLitPointsMaterial(color, { texture, width: 1 });
  assert.ok(lit.vertexShader.includes('vLitColor = texelFetch'));
  assert.equal(lit.blending, base.blending);
  assert.equal(lit.uniforms.uTime, base.uniforms.uTime);
});

test('the classic shaders are unchanged by the etched look', () => {
  assert.ok(!Pointcloud.vertexShader.includes('aEdge'));
  assert.ok(Pointcloud.fragmentShader.includes('gl_FragColor = vec4(uGlowColor * brightness, glow * uOpacity * cutawayKeep);'));
});

test('etched gain: brighter at the overview, continuous between detents', () => {
  const g = r => Pointcloud.etchedGain({ orbitRadius: r, overviewRadius: 20, closestRadius: 5.6 });
  assert.equal(g(20), Pointcloud.ETCHED_GAIN_OVERVIEW);
  assert.equal(g(5.6), Pointcloud.ETCHED_GAIN_CLOSE);
  assert.ok(g(10) > g(5.6) && g(10) < g(20));
  assert.equal(g(40), Pointcloud.ETCHED_GAIN_OVERVIEW);
});

test('opt-out is per browser and tolerant of storage failures', () => {
  assert.equal(Pointcloud.etchedLookOptedOut({ getItem: () => 'classic' }), true);
  assert.equal(Pointcloud.etchedLookOptedOut({ getItem: () => null }), false);
  assert.equal(Pointcloud.etchedLookOptedOut({ getItem: () => { throw new Error('denied'); } }), false);
  assert.equal(Pointcloud.etchedLookOptedOut(null), false);
});

test('modes.js keeps additive point materials out of the depth buffer', () => {
  const modes = fs.readFileSync(path.join(SRC, 'home-3d/modes.js'), 'utf8');
  assert.ok(modes.includes('material.depthWrite = material.userData?.additive ? false : depthWrite;'));
});
