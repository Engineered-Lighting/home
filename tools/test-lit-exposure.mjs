// L2 exposure and tone policy (lit-exposure.mjs): the fixed design night,
// metered partial adaptation, the disclosed ambient floor, the per-pixel
// mesopic shift and the display operators, as pure CPU twins with no WebGL.
// The TSL twin runs in tools/test-apartment-lit-webgpu-browser.mjs.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const FILE = path.join(ROOT, 'app/src/home-3d/lit-exposure.mjs');
const E = await import(pathToFileURL(FILE).href);
const M = await import(pathToFileURL(path.join(ROOT, 'app/src/home-3d/lit-model.mjs')).href);
const near = (actual, expected, tolerance, message) => assert.ok(Math.abs(actual - expected) <= tolerance, `${message}: ${actual} vs ${expected} (±${tolerance})`);
const DESIGN = M.ev100FromExposure(M.LIT_EXPOSURE);
const encode = rgb => rgb.map(E.srgbEncode);
const nearBlack = rgb => Math.max(...encode(rgb)) < 0.03;

test('pure: no imports, no three.js, no DOM, so the WebGL path and Node load it', () => {
  const code = fs.readFileSync(FILE, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
  for (const word of [/\bimport\b/, /\bTHREE\b/, /\bdocument\b/, /\bwindow\b/, /['"]three/]) assert.doesNotMatch(code, word);
});

test('the design night is Tier-0\'s pre-exposure, EV100 4.45 in Filament\'s camera model', () => {
  near(E.ev100FromExposure(M.LIT_EXPOSURE), DESIGN, 1e-12, 'one camera model');
  near(DESIGN, 4.4548, 1e-4, 'EV100');
  near(E.ev100FromLuminance(12.5 * 2 ** 9 / 100), 9, 1e-12, 'meter calibration K = 12.5');
  assert.equal(E.adaptationGain(0), 1); assert.equal(E.adaptationGain(2), 4);
});

test('partial adaptation: 60 % of the metered drop, never below 0, capped at +5.5 EV', () => {
  const at = ev => 12.5 * 2 ** ev / 100;
  assert.deepEqual(E.adaptationFromLuminance(at(DESIGN + 2), DESIGN), { dropEv: 0, adaptEv: 0 }, 'brighter than the design night: no adaptation');
  const two = E.adaptationFromLuminance(at(DESIGN - 2), DESIGN);
  near(two.dropEv, 2, 1e-9, 'drop'); near(two.adaptEv, 1.2, 1e-9, '60 %');
  const moon = E.adaptationFromLuminance(at(DESIGN - 12), DESIGN);
  near(moon.dropEv, 12, 1e-9, 'deep drop'); assert.equal(moon.adaptEv, 5.5, 'capped');
  assert.deepEqual(E.adaptationFromLuminance(0, DESIGN), { dropEv: 0, adaptEv: 0 }, 'no meter, no adaptation');
  assert.equal(E.LIT_ADAPTATION.presentation, true); assert.equal(E.LIT_ADAPTATION.measured, false);
});

test('the meter: log-average of the 50th-95th percentile band, ignoring the unlit', () => {
  const flat = new Float64Array(100).fill(0.5);
  near(E.meterLuminance(flat), 0.5, 1e-12, 'a flat field meters as itself');
  const withHoles = [...flat, 0, 0, NaN, -1];
  near(E.meterLuminance(withHoles), 0.5, 1e-12, 'non-positive samples are not the scene');
  // Half black, half 1 cd/m², plus a 3 % hot spot: the band is the lit half without the spot.
  const field = [...Array(50).fill(1e-4), ...Array(47).fill(1), ...Array(3).fill(1e4)];
  const metered = E.meterLuminance(field);
  assert.ok(metered > 0.9 && metered < 10, 'the darkest half and the hottest 5 % barely count: ' + metered);
  assert.equal(E.meterLuminance([]), null); assert.equal(E.meterLuminance([0, 0]), null);
});

test('adaptation eases slowly as the room darkens and fast as it brightens; reduced motion snaps', () => {
  const darker = E.easeAdaptation(0, 4, 1000), brighter = E.easeAdaptation(4, 0, 1000);
  near(darker.ev, 4 * -Math.expm1(-1000 / 3000), 1e-9, 'tau 3 s darkening'); assert.equal(darker.adapting, true);
  near(brighter.ev, 4 * Math.exp(-1000 / 750), 1e-9, 'tau 0.75 s brightening');
  assert.ok(4 - brighter.ev > darker.ev, 'brightening moves further in the same time');
  assert.ok(E.LIT_ADAPTATION.tauBrightenMs >= 500 && E.LIT_ADAPTATION.tauBrightenMs <= 1000);
  assert.ok(E.LIT_ADAPTATION.tauDarkenMs >= 2000 && E.LIT_ADAPTATION.tauDarkenMs <= 4000);
  assert.deepEqual(E.easeAdaptation(0, 4, 16, { snap: true }), { ev: 4, adapting: false });
  assert.deepEqual(E.easeAdaptation(null, 3, 16), { ev: 3, adapting: false }, 'the first meter snaps');
  assert.deepEqual(E.easeAdaptation(3.995, 4, 16), { ev: 4, adapting: false }, 'settles within 0.01 EV');
});

test('the ambient floor is disclosed, assumed and inside 0.1-1 lx', () => {
  const floor = E.LIT_AMBIENT_FLOOR;
  assert.ok(floor.lux >= 0.1 && floor.lux <= 1); assert.equal(floor.status, 'assumed'); assert.equal(floor.measured, false);
  assert.match(floor.source, /moonlight or street light through the windows/); assert.match(floor.source, /not measured/);
  assert.ok(Object.isFrozen(floor));
  const adaptive = E.litExposureDisclosure({ adaptive: true, designEv100: DESIGN });
  assert.match(adaptive, /EV100 4\.45/); assert.match(adaptive, /partial night adaptation: presentation, not lux/);
  assert.match(adaptive, /Assumed night ambient about 0\.2 lx/);
  assert.match(E.litExposureDisclosure({ adaptive: false, designEv100: DESIGN }), /no night adaptation/);
  for (const text of [adaptive, floor.source]) assert.doesNotMatch(text, /\bmeasured (?:output|flux|lumens)\b|photometric output/i);
});

test('mesopic shift: per pixel, by the pixel\'s own luminance, keeping luminance', () => {
  assert.equal(E.mesopicWeight(10), 0, 'photopic above about 4 cd/m²');
  near(E.mesopicWeight(1e-3), E.LIT_MESOPIC.strength, 1e-12, 'fully shifted below 0.01 cd/m²');
  for (let L = 1e-3; L < 10; L *= 1.5) assert.ok(E.mesopicWeight(L) >= E.mesopicWeight(L * 1.5), 'monotonic');
  near(E.lumaOf(E.LIT_MESOPIC.tint), 1, 1e-12, 'the blue-grey has luminance 1');
  const pre = M.LIT_EXPOSURE, warm = [1.4, 0.95, 0.5].map(v => v * pre * 0.004 / E.lumaOf([1.4, 0.95, 0.5]));
  const shifted = E.mesopicShift(warm, pre);
  near(E.lumaOf(shifted), E.lumaOf(warm), 1e-15, 'luminance kept');
  assert.ok(shifted[0] / shifted[2] < warm[0] / warm[2] && shifted[2] > shifted[0], 'a dim warm surface turns blue-grey');
  const pool = [1.4, 0.95, 0.5].map(v => v * pre * 2 / E.lumaOf([1.4, 0.95, 0.5]));
  const kept = E.mesopicShift(pool, pre);
  assert.ok(kept[0] / kept[2] > 2, 'a 2 cd/m² warm pool stays warm');
});

test('display chain: AgX with a lifted toe for the view, Reinhard fallback, faithful comparison views', () => {
  const pre = M.LIT_EXPOSURE, grey = L => [L * pre, L * pre, L * pre];
  // Moonlight at full adaptation: the floor's luminance on a 0.3 albedo.
  const moon = grey(0.3 * 0.2 / Math.PI), gain = E.adaptationGain(5.5);
  assert.ok(!nearBlack(E.litDisplayPixel(moon, { preExposure: pre, gain, operator: 'agx' })), 'moonlight is not near-black in the view');
  assert.ok(!nearBlack(E.litDisplayPixel(moon, { preExposure: pre, gain, operator: 'reinhard' })), 'nor with the Reinhard fallback');
  // A moonlit shadow (about 0.002 cd/m²): plain AgX crushes it (spike 3), the lifted toe keeps it.
  const shade = grey(0.0023);
  assert.ok(nearBlack(E.LIT_TONE_CPU.agx(shade.map(v => v * gain))), 'AgX without the lifted toe crushes a moonlit shadow');
  assert.ok(!nearBlack(E.litDisplayPixel(shade, { preExposure: pre, gain, operator: 'agx' })), 'the lifted toe keeps it');
  assert.ok(nearBlack(E.litDisplayPixel(moon, { preExposure: pre, gain, operator: 'linear' })), 'the linear comparison shows no adaptation');
  // The lifted toe: monotonic, about +1.6 EV at the bottom, under +0.1 EV at mid grey.
  let last = 0;
  for (let Y = 1e-6; Y < 20; Y *= 1.2) { const lifted = E.lumaOf(E.liftToe([Y, Y, Y])); assert.ok(lifted > last); last = lifted; }
  near(Math.log2(E.lumaOf(E.liftToe([1e-6, 1e-6, 1e-6])) / 1e-6), Math.log2(3), 1e-3, 'deep shadows');
  assert.ok(Math.log2(E.lumaOf(E.liftToe([0.18, 0.18, 0.18])) / 0.18) < 0.1, 'mid grey nearly untouched');
  // Every operator maps black to black, stays in 0..1 and is monotonic on grey.
  for (const operator of E.LIT_TONE_OPERATORS) {
    let previous = -1;
    for (let L = 0; L < 1e4; L = L ? L * 2 : 1e-4) {
      const out = E.litDisplayPixel(grey(L), { preExposure: pre, gain, operator });
      assert.ok(out.every(v => v >= 0 && v <= 1), operator + ' in range');
      const Y = E.lumaOf(out); assert.ok(Y >= previous - 1e-9, operator + ' monotonic at ' + L); previous = Y;
    }
  }
  // PBR Neutral reproduces base colours in its linear range (Khronos).
  const base = [0.5, 0.3, 0.2];
  const neutral = E.LIT_TONE_CPU.neutral(base);
  assert.ok(neutral.every((v, i) => Math.abs(v - (base[i] - 0.04)) < 1e-12), 'Neutral: only the 0.04 toe offset below the shoulder');
  assert.throws(() => E.litDisplayPixel(base, { preExposure: pre, operator: 'aces' }), /lit_tone_operator_unknown/);
  assert.equal(E.LIT_TONE.view, 'agx'); assert.equal(E.LIT_TONE.fallback, 'reinhard'); assert.deepEqual([...E.LIT_TONE.compare], ['linear', 'neutral']);
  assert.equal(E.litTonePresentation('agx'), true); assert.equal(E.litTonePresentation('neutral'), false);
});

test('the AgX twin matches three r186\'s curve at known points', () => {
  // three's agxToneMapping on grey is grey; mid grey lands near 0.18 display-linear.
  const mid = E.LIT_TONE_CPU.agx([0.18, 0.18, 0.18]);
  near(mid[0], mid[1], 1e-3, 'grey stays grey'); near(mid[1], mid[2], 1e-3, 'grey stays grey');
  assert.ok(mid[1] > 0.12 && mid[1] < 0.25, 'mid grey near 0.18: ' + mid[1]);
  const white = E.LIT_TONE_CPU.agx([16, 16, 16]);
  assert.ok(white.every(v => v > 0.9 && v <= 1), 'a bright highlight rolls to white');
  const deep = E.LIT_TONE_CPU.agx([0.002, 0.002, 0.002]);
  assert.ok(Math.max(...deep) < 0.0006, 'AgX crushes 0.002 about 7x, which is why the view lifts the toe');
});

test('litDisplayNode needs a known operator and the TSL namespace', () => {
  assert.throws(() => E.litDisplayNode({}, { operator: 'filmic' }), /lit_tone_operator_unknown/);
});
