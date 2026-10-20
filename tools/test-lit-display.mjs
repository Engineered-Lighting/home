// Lighting's one HDR frame (lit-display.mjs): the CPU twins of the bloom,
// the fixture faces and the per-pixel display pass that the GLSL (WebGL) and
// TSL (WebGPU) passes are checked against in the browser proofs. Authored
// values only; no renderer, browser or household data.
import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const Display = await import(pathToFileURL(path.join(ROOT, 'app/src/home-3d/lit-display.mjs')).href);
const Exposure = await import(pathToFileURL(path.join(ROOT, 'app/src/home-3d/lit-exposure.mjs')).href);
const Model = await import(pathToFileURL(path.join(ROOT, 'app/src/home-3d/lit-model.mjs')).href);

const luma = c => 0.2126729 * c[0] + 0.7151522 * c[1] + 0.0721750 * c[2];
function frameOf(width, height, fill = () => [0, 0, 0, 1]) {
  const image = new Float32Array(width * height * 4);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) image.set(fill(x, y), (y * width + x) * 4);
  return image;
}
const sum = (image, k = 1) => { let s = 0; for (let i = 0; i < image.length; i += 4) s += luma([image[i], image[i + 1], image[i + 2]]) * k; return s; };
const CAMERA = { look: 'camera', operator: 'agx', gain: 1, whiteBalance: [[1, 0, 0], [0, 1, 0], [0, 0, 1]], agxPower: 1.35, preExposure: 0.038 };

test('everything in the frame is presentation, assumed and disclosed as such', () => {
  for (const item of [Display.LIT_BLOOM, Display.LIT_NOISE]) { assert.equal(item.presentation, true); assert.equal(item.measured, false); }
  assert.equal(Display.LIT_EMITTERS.status, 'assumed'); assert.equal(Display.LIT_EMITTERS.measured, false);
  assert.match(Display.LIT_FRAME_DISCLOSURE, /Camera glare \(presentation\)/);
  assert.match(Display.LIT_FRAME_DISCLOSURE, /assumed apertures/);
  assert.match(Display.LIT_FRAME_DISCLOSURE, /Simulated sensor noise \(presentation\)/);
  assert.equal(Display.LIT_BLOOM.mips, 6); assert.equal(Display.LIT_BLOOM.strength, 0.03); assert.equal(Display.LIT_BLOOM.karis, true);
  assert.ok(Display.LIT_FRAME.emitterClamp < Display.LIT_HALF_MAX, 'faces are stored below half float\'s largest value');
});

test('the bloom of a uniform frame is the frame times the strength: the chain is an average of blurs', () => {
  const [w, h] = [64, 40], value = [0.7, 0.5, 0.3, 1];
  const bloom = Display.bloomChain(frameOf(w, h, () => value), w, h);
  assert.deepEqual(bloom.sizes, [[32, 20], [16, 10], [8, 5], [4, 2], [2, 1], [1, 1]], 'six mips from half resolution');
  for (let i = 0; i < bloom.data.length; i += 4) for (let c = 0; c < 3; c++)
    assert.ok(Math.abs(bloom.data[i + c] - value[c] * Display.LIT_BLOOM.strength) < 1e-9, 'Karis on, still exact on a flat frame');
});

test('energy: the 13-tap downsample keeps a bright patch\'s energy; the chain keeps it to within the edge clamp', () => {
  const [w, h] = [128, 96];
  const image = frameOf(w, h, (x, y) => (x >= 60 && x < 68 && y >= 44 && y < 52 ? [5, 4, 3, 1] : [0, 0, 0, 1]));
  const [mw, mh] = [w >> 1, h >> 1];
  const down = Display.bloomDownsample(image, w, h, mw, mh, { first: true, karis: false, strength: 1 });
  assert.ok(Math.abs(sum(down, 4) - sum(image)) / sum(image) < 1e-9, 'every source texel is counted once: ' + sum(down, 4) + ' vs ' + sum(image));
  const chain = Display.bloomChain(image, w, h, { karis: false, strength: 1 });
  // The coarsest mips (4 x 3, 2 x 1, 1 x 1) reach the frame's edges, where reads clamp: a little is lost there.
  assert.ok(Math.abs(sum(chain.data, 4) - sum(image)) / sum(image) < 0.03, 'the chain conserves energy within the edge clamp: ' + sum(chain.data, 4) + ' vs ' + sum(image));
  // The mix is convex: every level's weight sums to one.
  const s = Display.LIT_BLOOM.spread, weights = [0, 1, 2, 3, 4].map(i => s ** i * (1 - s)).concat(s ** 5);
  assert.ok(Math.abs(weights.reduce((a, b) => a + b) - 1) < 1e-12);
  assert.ok(weights[0] > weights[5], 'a narrow core and a broad, weak tail');
});

test('a fixture face brings its true energy into the bloom; the Karis average would treat it as a firefly', () => {
  const [w, h] = [64, 64], at = [31, 31];
  const trueRgb = [4e5, 3.6e5, 2.8e5];
  const tagged = Display.emitterPixel(trueRgb);
  assert.ok(tagged[3] > Display.LIT_FRAME.emitterTag, 'stored clamped, tagged with log2 of the clamp');
  assert.ok(Math.max(...tagged.slice(0, 3)) <= Display.LIT_FRAME.emitterClamp + 1e-9);
  assert.ok(Math.abs(tagged[0] * 2 ** (tagged[3] - Display.LIT_FRAME.emitterTag) - trueRgb[0]) < 1e-6, 'the tag recovers the true value');
  const withTag = frameOf(w, h, (x, y) => (x === at[0] && y === at[1] ? tagged : [0.01, 0.01, 0.01, 1]));
  const untagged = frameOf(w, h, (x, y) => (x === at[0] && y === at[1] ? [...tagged.slice(0, 3), 1] : [0.01, 0.01, 0.01, 1]));
  const k = Display.LIT_BLOOM.strength;
  const glow = image => sum(Display.bloomChain(image, w, h).data, 4) / k - 0.01 * (w * h - 1);
  const face = luma(trueRgb);
  assert.ok(Math.abs(glow(withTag) - face) / face < 0.03, 'the tagged face blooms with its true energy (within the edge clamp): ' + glow(withTag) + ' vs ' + face);
  assert.ok(glow(untagged) < face * 0.1, 'an untagged hot pixel loses most of it to the Karis average: ' + glow(untagged));
});

test('the display pass: the camera look is monotone, and dimming every light darkens every pixel', () => {
  let last = -1;
  for (let ev = -14; ev <= 8; ev += 0.25) {
    const v = 2 ** ev, out = Display.litFramePixel([v, v, v, 1], null, CAMERA);
    assert.ok(out[1] >= last - 1e-12, 'monotone in exposure at ' + ev + ' EV'); last = out[1];
  }
  const scenes = [[0.002, 0.003, 0.001, 1], [0.08, 0.05, 0.02, 1], [1.5, 1.2, 0.7, 1], [40, 30, 20, 1]];
  for (const look of [CAMERA, { ...CAMERA, look: 'eye', gain: 8 }]) for (const scene of scenes) {
    let previous = Infinity;
    for (const k of [1, 0.6, 0.3, 0.1, 0.03, 0]) {
      const out = Display.litFramePixel(scene.map((v, i) => (i < 3 ? v * k : v)), [0.01 * k, 0.008 * k, 0.005 * k], look);
      const y = luma(out);
      assert.ok(y <= previous + 1e-12, `dimming never brightens (${look.look}, ${scene}, ${k})`); previous = y;
    }
  }
});

test('the comparison views get no bloom, gain, white balance or noise; the look is a per-pixel twin of L2', () => {
  const scene = [0.3, 0.2, 0.1, 1], bloom = [5, 5, 5], noise = { ...Display.LIT_NOISE, seed: 7 };
  const linear = Display.litFramePixel(scene, bloom, { ...CAMERA, operator: 'linear', gain: 16, noise });
  assert.deepEqual(linear.slice(0, 3).map(v => +v.toFixed(12)), scene.slice(0, 3).map(Exposure.srgbEncode).map(v => +v.toFixed(12)));
  const eye = Display.litFramePixel(scene, null, { look: 'eye', operator: 'agx', gain: 4, preExposure: 0.038 });
  const l2 = Exposure.litDisplayPixel(scene.slice(0, 3), { preExposure: 0.038, gain: 4, operator: 'agx' }).map(Exposure.srgbEncode);
  eye.slice(0, 3).forEach((v, c) => assert.ok(Math.abs(v - l2[c]) < 1e-12, 'eye = L2\'s chain'));
  const camera = Display.litFramePixel(scene, null, { ...CAMERA, gain: 2 });
  const twin = Exposure.litCameraPixel(scene.slice(0, 3), { gain: 2, whiteBalance: CAMERA.whiteBalance }).map(Exposure.srgbEncode);
  camera.slice(0, 3).forEach((v, c) => assert.ok(Math.abs(v - twin[c]) < 1e-12, 'camera = lit-exposure\'s camera look'));
});

test('premultiplied coverage: half-covered pixels are toned unpremultiplied and keep their coverage', () => {
  const full = Display.litFramePixel([0.4, 0.3, 0.2, 1], null, CAMERA), half = Display.litFramePixel([0.2, 0.15, 0.1, 0.5], null, CAMERA);
  for (let c = 0; c < 3; c++) assert.ok(Math.abs(half[c] - full[c] * 0.5) < 1e-12);
  assert.equal(half[3], 0.5);
  assert.deepEqual(Display.litFramePixel([0, 0, 0, 0], [1, 1, 1], CAMERA), [0, 0, 0, 0], 'uncovered stays transparent');
  assert.equal(Display.litFramePixel(Display.emitterPixel([1e6, 1e6, 1e6]), null, CAMERA)[3], 1, 'a face is opaque');
});

test('the fixture faces: luminance from the assumed lumens over assumed apertures', () => {
  const lumens = Model.LIT_ASSUMED_LUMENS, half = Model.spotHalfAngle(20);
  const spot = { kind: 'spot', candela: Model.candelaFromLumens(lumens.spot, half), fluxFraction: 1 };
  assert.ok(Math.abs(Display.emitterLuminance(spot, lumens) - spot.candela / Display.LIT_EMITTERS.spot.areaM2) < 1e-6);
  assert.ok(Display.emitterLuminance(spot, lumens) > 1e6, 'a spot lens is millions of cd/m²');
  assert.ok(Display.emitterLuminance(spot, lumens) * Model.LIT_EXPOSURE > Display.LIT_FRAME.emitterClamp, 'beyond half float once pre-exposed');
  const down = { kind: 'down', fluxFraction: 0.5 };
  assert.ok(Math.abs(Display.emitterLuminance(down, lumens) - lumens.down * 0.5 / (Math.PI * Display.LIT_EMITTERS.down.areaM2)) < 1e-9);
  assert.equal(Display.emitterLuminance({ kind: 'down', fluxFraction: 0 }, lumens), 0, 'off is dark');
  assert.equal(Display.emitterLuminance({ kind: 'ambient_floor' }, lumens), 0);
  // Dimming a face is monotone in its level.
  let last = Infinity;
  for (const level of [1, 0.6, 0.3, 0.1, 0]) {
    const L = Display.emitterLuminance({ kind: 'zone', fluxFraction: Model.litDimmingFraction(level) }, lumens);
    assert.ok(L <= last); last = L;
  }
  const cone = { cosOuter: Math.cos(half), cosInner: Math.cos(half * 0.8) };
  assert.equal(Display.emitterViewFactor('spot', 1, cone), 1, 'full on axis');
  assert.equal(Display.emitterViewFactor('spot', Math.cos(1.2), cone), Display.LIT_EMITTERS.spot.spill, 'frosted spill outside the beam');
  assert.equal(Display.emitterViewFactor('spot', -0.1, cone), 0, 'dark from behind');
  assert.equal(Display.emitterViewFactor('down', 0.2), 1, 'a Lambertian face is uniform over its front');
});

test('noise is still while the seed holds, re-drawn when the camera moves, and zero-mean', () => {
  const scene = [0.05, 0.05, 0.05, 1], noise = { ...Display.LIT_NOISE, seed: 40 };
  const a = Display.litFramePixel(scene, null, { ...CAMERA, noise, x: 10, y: 12 }), b = Display.litFramePixel(scene, null, { ...CAMERA, noise, x: 10, y: 12 });
  assert.deepEqual(a, b, 'the same seed: the same grain (the camera rests)');
  const c = Display.litFramePixel(scene, null, { ...CAMERA, noise: { ...noise, seed: 44 }, x: 10, y: 12 });
  assert.notDeepEqual(a, c, 'a new seed when the camera moves');
  const clean = Display.litFramePixel(scene, null, CAMERA)[1];
  let mean = 0;
  for (let i = 0; i < 4000; i++) mean += Display.litFramePixel(scene, null, { ...CAMERA, noise, x: i % 80, y: Math.floor(i / 80) })[1] - clean;
  assert.ok(Math.abs(mean / 4000) < 1.5 / 255, 'zero-mean within dither: ' + mean / 4000);
  for (let i = 0; i < 1000; i++) assert.ok(Display.hash32(i, 7, 3) === Display.hash32(i, 7, 3));
});

test('the GLSL passes are built from the same constants; the TSL builders refuse an unknown operator', () => {
  const down0 = Display.litBloomDownGlsl({ first: true }), down = Display.litBloomDownGlsl(), up = Display.litBloomUpGlsl();
  for (const source of [down0, down, up]) { assert.match(source, /void main\(\)/); assert.doesNotMatch(source, /texture2D|texture\(/, 'texelFetch only, so the twins are exact'); }
  assert.match(down0, /exp2\(max\(0\.0, t\.a - 2\.0\)\)/, 'the first mip reads the faces\' tag');
  assert.match(down0, /rgb \*= uStrength;/); assert.doesNotMatch(down, /rgb \*= uStrength;/, 'only the first mip scales by the strength');
  assert.match(up, /mix\(texelFetch\(uBase/, 'the upsample mixes in the shader, never by blending');
  for (const operator of Exposure.LIT_TONE_OPERATORS) assert.match(Display.litDisplayFragmentGlsl({ operator }), /gl_FragColor = vec4\(c \* alpha, alpha\);/);
  assert.doesNotMatch(Display.litDisplayFragmentGlsl({ operator: 'linear' }), /uNoise\.x \*|uBloom, uBloomSize/, 'comparison views: no noise, no bloom');
  assert.throws(() => Display.litDisplayFragmentGlsl({ operator: 'aces' }), /lit_tone_operator_unknown/);
  assert.throws(() => Display.litDisplayFrameNode({}, { operator: 'aces' }), /lit_tone_operator_unknown/);
  assert.match(Display.LIT_EMITTER_FRAGMENT_GLSL, /2\.0 \+ log2\(1\.0 \/ s\)/);
});
