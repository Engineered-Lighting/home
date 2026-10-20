// L1 light model (lit-model.mjs): the renderer-independent photometric
// conventions Lit uses. Candela, Filament-style flux integration, the
// synthetic FWHM LUT, the assumed dimming curve, constant-luminance CCT,
// radial zone directions, the 160 ms ease and pre-exposure, with no WebGL.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const M = await import(pathToFileURL(path.join(ROOT, 'app/src/home-3d/lit-model.mjs')).href);
const near = (actual, expected, tolerance, message) => assert.ok(Math.abs(actual - expected) <= tolerance, `${message}: ${actual} vs ${expected} (±${tolerance})`);

test('the lumen set is SPEC\'s, assumed, with its source recorded and the fixture profile superseded for Lit only', () => {
  assert.deepEqual({ ...M.LIT_ASSUMED_LUMENS }, { spot: 450, down: 350, zone: 120 });
  assert.deepEqual(Object.keys(M.LIT_ASSUMED_LUMENS), ['spot', 'down', 'zone'], 'the value map stays the plain SPEC set');
  assert.equal(M.LIT_ASSUMED_LUMENS.source, M.LIT_LUMEN_SOURCE);
  assert.equal(M.LIT_LUMEN_SOURCE.status, 'assumed'); assert.equal(M.LIT_LUMEN_SOURCE.measured, false);
  assert.match(M.LIT_LUMEN_SOURCE.reference, /SPEC\.md › Lit view/);
  assert.equal(M.LIT_ASSUMPTION_SOURCE, M.LIT_LUMEN_SOURCE.summary);
  assert.match(M.LIT_LUMEN_SOURCE.decision, /Q7, 2026-10-19/);
  assert.match(M.LIT_LUMEN_SOURCE.supersedes, /home-den-fixtures\.js max_flux_lm \(spot 250, down 400, radial 60 lm\), for Lit only/);
  assert.ok(Object.isFrozen(M.LIT_ASSUMED_LUMENS) && Object.isFrozen(M.LIT_LUMEN_SOURCE));
  const fixtures = fs.readFileSync(path.join(ROOT, 'app/src/home-den-fixtures.js'), 'utf8');
  assert.match(fixtures, /max_flux_lm: 60,/); assert.match(fixtures, /downlight_ring: \{ beam_deg: 60, max_flux_lm: 400,/);
  assert.match(fixtures, /spot: \{ fwhm_deg: 20, profile: "gaussian", max_flux_lm: 250,/, 'the protected fixture profile is left as it is');
});

test('candela: lumens spread evenly over a cone', () => {
  for (const theta of [.05, .2, .5, 1, Math.PI / 2])
    near(M.candelaFromLumens(450, theta), 450 / (2 * Math.PI * (1 - Math.cos(theta))), 1e-9, 'cone at ' + theta);
  near(M.candelaFromLumens(4 * Math.PI, Math.PI), 1, 1e-12, 'an isotropic 4 pi lm source is 1 cd');
  assert.ok(M.candelaFromLumens(450, .2) > M.candelaFromLumens(450, .6), 'narrower is brighter');
  assert.equal(M.candelaFromLumens(450, 0), 0); assert.equal(M.candelaFromLumens(NaN, .5), 0);
});

test('flux integration (Filament): integrating a profile recovers its lumens', () => {
  near(M.integrateFlux(() => 1, 1), 4 * Math.PI, 1e-9, 'a 1 cd isotropic source emits 4 pi lm');
  near(M.integrateFlux(theta => Math.cos(theta) > 0 ? Math.cos(theta) : 0, 1), Math.PI, 1e-6, 'a Lambertian hemisphere emits pi I0');
  const theta = .5, cone = t => (t <= theta ? 1 : 0);
  near(M.integrateFlux(cone, M.candelaFromLumens(450, theta), { samples: 20000 }), 450, .5, 'a uniform cone at candelaFromLumens gives back its lumens');
  // A Gaussian lobe: 2 pi I0 integral exp(-t^2 / 2 s^2) sin t dt = 2 pi I0 s^2 (1 - s^2 / 3 + s^4 / 15 - ...),
  // with s from the 20 deg FWHM (the series of sin t against the Gaussian moments).
  const sigma = 20 * Math.PI / 180 / (2 * Math.sqrt(2 * Math.LN2));
  const reference = 2 * Math.PI * sigma * sigma * (1 - sigma * sigma / 3 + sigma ** 4 / 15);
  near(M.integrateFlux(M.gaussianBeamProfile(20), 1), reference, reference * 1e-5, 'Gaussian lobe flux');
  const peak = M.peakCandelaFromLumens(450, M.gaussianBeamProfile(20));
  near(M.integrateFlux(M.gaussianBeamProfile(20), peak), 450, 1e-6, 'peakCandelaFromLumens inverts integrateFlux');
  assert.equal(M.integrateFlux(null), 0); assert.equal(M.peakCandelaFromLumens(450, () => 0), 0);
});

test('the synthetic IES-style LUT matches the 20 deg datasheet FWHM, peak 1, and integrates to its lumens', () => {
  const lut = M.litBeamLut();
  assert.equal(lut.width, 1); assert.equal(lut.height, 180); assert.equal(lut.values.length, 180); assert.equal(lut.stepDeg, 1);
  assert.equal(lut.fwhmDeg, 20); assert.equal(lut.status, 'assumed'); assert.equal(lut.measured, false); assert.equal(lut.lumens, 450);
  assert.equal(lut.values[0], 1, 'normalised to peak 1 on the axis');
  near(lut.values[10], .5, 1e-6, 'half maximum at half the FWHM (10 deg off axis)');
  for (let i = 1; i < 180; i++) assert.ok(lut.values[i] <= lut.values[i - 1], 'falls off monotonically');
  near(M.integrateFlux(lut, lut.peakCandela), 450, .5, 'the LUT carries the peak candela that integrates to the lumen rating');
  const sigma = 20 * Math.PI / 180 / (2 * Math.sqrt(2 * Math.LN2));
  near(lut.peakCandela, 450 / (2 * Math.PI * sigma * sigma), 450 / (2 * Math.PI * sigma * sigma) * .01, 'a narrow Gaussian peaks near flux / (2 pi sigma^2)');
  near(M.litBeamLut({ fwhmDeg: 40 }).values[20], .5, 1e-6, 'any FWHM');
});

test('dimming: the assumed curve is 0 at 0, 1 at 1, continuous and strictly increasing', () => {
  assert.equal(M.litDimmingFraction(0), 0); near(M.litDimmingFraction(1), 1, 1e-12, 'full');
  assert.equal(M.LIT_DIMMING_CURVE.status, 'assumed'); assert.equal(M.LIT_DIMMING_CURVE.measured, false);
  let previous = -1;
  for (let i = 0; i <= 10000; i++) {
    const value = M.litDimmingFraction(i / 10000);
    assert.ok(value > previous, 'strictly increasing at ' + i / 10000); previous = value;
  }
  const toe = 0.08 - 1e-12; near(M.litDimmingFraction(toe), M.litDimmingFraction(0.08), 1e-9, 'continuous at the L* toe');
  near(M.litDimmingFraction(.5), (66 / 116) ** 3, 1e-12, 'half the slider is L* 50: about 18 % of the flux');
  assert.ok(M.litDimmingFraction(.5) < .5, 'not linear brightness');
  assert.equal(M.litDimmingFraction(-1), 0); near(M.litDimmingFraction(2), 1, 1e-12, 'clamped');
  assert.equal(M.litDimmingFraction(NaN), 0);
});

test('CCT: constant luminance, Y = 1 within 1e-3 from 1667 K to 25000 K, no negative channel', () => {
  for (let kelvin = 1000; kelvin <= 26000; kelvin += 50) {
    const rgb = M.cctToLinearRgb(kelvin);
    near(M.linearSrgbLuminance(rgb), 1, 1e-3, 'luminance at ' + kelvin + ' K');
    assert.ok(rgb.every(value => value >= 0 && Number.isFinite(value)), 'in gamut at ' + kelvin + ' K');
  }
  const warm = M.cctToLinearRgb(2700), cool = M.cctToLinearRgb(5000), d65 = M.cctToLinearRgb(6504);
  assert.ok(warm[0] > warm[1] && warm[1] > warm[2], '2700 K is warm');
  assert.ok(cool[2] > warm[2] && cool[0] < warm[0], '5000 K is cooler');
  near(d65[0], d65[2], .06, '6504 K sits near the D65 white of linear sRGB');
  // Kim et al. against the published locus: 2700 K x 0.4599 y 0.4106 (Planckian), 5000 K x 0.3451 y 0.3516.
  const at2700 = M.kelvinToXy(2700), at5000 = M.kelvinToXy(5000);
  near(at2700.x, .4599, 1e-3, 'x at 2700 K'); near(at2700.y, .4106, 1e-3, 'y at 2700 K');
  near(at5000.x, .3451, 1e-3, 'x at 5000 K'); near(at5000.y, .3516, 1e-3, 'y at 5000 K');
});

test('CCT below about 2000 K: the raw locus has negative blue, and the clamp keeps luminance', () => {
  const { x, y } = M.kelvinToXy(1700);
  const raw = M.xyzToLinearSrgb(M.xyToXYZ(x, y, 1));
  assert.ok(raw[2] < 0, 'Planckian 1700 K is outside sRGB: ' + raw);
  near(M.linearSrgbLuminance(raw), 1, 1e-6, 'XYZ at Y = 1 maps to luminance 1');
  const clamped = M.cctToLinearRgb(1700);
  assert.equal(Math.min(...clamped), 0, 'chroma pulled in just far enough');
  near(M.linearSrgbLuminance(clamped), 1, 1e-9, 'luminance preserved');
  assert.ok(clamped[0] / clamped[1] > 1 && clamped[0] / clamped[1] < raw[0] / raw[1], 'still warm, slightly desaturated');
});

test('the six radial zone directions: unit, outward at each azimuth and slightly down', () => {
  const azimuths = [0, 60, 120, 180, 240, 300], directions = M.radialZoneDirections(azimuths);
  assert.equal(directions.length, 6);
  directions.forEach((direction, index) => {
    assert.deepEqual(direction, M.zoneDirection(azimuths[index]), 'the same direction Lit lights use');
    near(Math.hypot(...direction), 1, 1e-12, 'unit');
    near(Math.atan2(direction[1], direction[0]), Math.atan2(Math.sin(azimuths[index] * Math.PI / 180), Math.cos(azimuths[index] * Math.PI / 180)), 1e-12, 'azimuth');
    near(direction[2], -.35 / Math.hypot(2, .35), 1e-12, 'the prototype tilt down');
  });
  assert.deepEqual(M.radialZoneDirections([]), []);
});

test('easing: the 160 ms time constant SPEC promises, settled in about half a second', () => {
  assert.equal(M.LIT_EASE_TAU_MS, 160);
  const fixtures = fs.readFileSync(path.join(ROOT, 'app/src/home-3d/equipment-virtual-fixtures.mjs'), 'utf8');
  assert.match(fixtures, /RADIAL_FADE_MS = 160, RADIAL_SETTLED_LEVEL = \.0005, RADIAL_SETTLED_KELVIN = 2/, 'the same clock and thresholds as the radial drawing');
  assert.equal(M.LIT_EASE_SETTLED_LEVEL, .0005); assert.equal(M.LIT_EASE_SETTLED_KELVIN, 2);
  near(M.litEaseAlpha(160), 1 - Math.exp(-1), 1e-12, 'one time constant');
  assert.ok(M.litEaseAlpha(480) > .95, 'about half a second to settle');
  assert.equal(M.litEaseAlpha(0), 0); assert.equal(M.litEaseAlpha(-5), 0);
  // Frame-rate independent: two 8 ms steps equal one 16 ms step.
  near(M.easeToward(M.easeToward(0, 1, 8), 1, 8), M.easeToward(0, 1, 16), 1e-12, 'frame-rate independent');
  let value = 1, previous = 1;
  for (let i = 0; i < 200 && value !== 0; i++) { value = M.easeToward(value, 0, 16); assert.ok(value < previous); previous = value; }
  assert.equal(value, 0, 'snaps to the target once within the settle threshold');
  assert.equal(M.easeToward(NaN, .4, 16), .4);
  assert.equal(M.easeToward(3000, 2700, 1e6, { settled: 2 }), 2700);
});

test('pre-exposure: Filament exposure = 1 / (1.2 x 2^EV100), applied to lights on the CPU', () => {
  near(M.exposureFromEv100(0), 1 / 1.2, 1e-12, 'EV100 0');
  near(M.exposureFromEv100(9), 1 / (1.2 * 512), 1e-15, 'a 300 lx interior sits near EV100 9');
  near(M.ev100FromExposure(M.exposureFromEv100(6.5)), 6.5, 1e-12, 'round trip');
  near(M.ev100FromExposure(.038), Math.log2(1 / (1.2 * .038)), 1e-12, 'Tier-0 Lit\'s fixed 0.038 is EV100 ≈ 4.45');
  assert.ok(Number.isNaN(M.ev100FromExposure(0)));
  near(M.preExpose(5000, M.exposureFromEv100(9)), 5000 / 614.4, 1e-9, 'pre-exposed intensity');
  assert.equal(M.preExpose(NaN, 1), 0);
});

test('fixture lights use the dimmed flux, never the raw slider level', () => {
  const lights = M.fixtureLights({ spot: { origin: [0, 0, 2], direction: [0, 0, -1], channel: { on: true, brightness: .5, color_temp_kelvin: 3000 } },
    down: { origin: [0, 0, 1.9], channel: { on: true, brightness: .5 } }, center: [0, 0, 2],
    zones: [{ number: 1, azimuth_deg: 0, channel: { on: false, brightness: 1 } }] }, { spotFwhmDeg: 20 });
  for (const light of lights) {
    assert.equal(light.fluxFraction, M.litDimmingFraction(light.level));
    near(light.candela, light.peakCandela * light.fluxFraction, 1e-12, light.kind + ' candela is peak x dimmed output');
    near(light.intensity, M.preExpose(light.candela, M.LIT_EXPOSURE), 1e-12, light.kind + ' pre-exposed');
  }
  assert.equal(lights[0].level, .5, 'level stays the set brightness'); near(lights[0].peakCandela, M.candelaFromLumens(450, M.spotHalfAngle(20)), 1e-12, 'spot peak');
  assert.equal(lights[2].candela, 0, 'an off zone adds no light');
  // L5 retired #319's instant radiosity: the bounce is traced in lit-bvh-bake.mjs,
  // weighted by each light's dimmed candela.
  assert.equal(M.bouncePatches, undefined); assert.equal(M.LIT_BOUNCE_LIMIT, undefined);
});

test('easeLights steps fixture lights toward their set level and Kelvin, snapping when asked', () => {
  const set = level => M.fixtureLights({ down: { origin: [0, 0, 2], channel: { on: true, brightness: level, color_temp_kelvin: 2700 } } });
  const first = M.easeLights(set(1), [], Infinity, { snap: true });
  assert.equal(first.easing, false); assert.deepEqual(first.shown, [{ level: 1, kelvin: 2700 }]);
  const step = M.easeLights(set(.2), first.shown, 160);
  const level = 1 + (.2 - 1) * (1 - Math.exp(-1));
  assert.equal(step.easing, true); near(step.lights[0].level, level, 1e-12, 'one time constant'); assert.equal(step.lights[0].setLevel, .2);
  near(step.lights[0].candela, step.lights[0].peakCandela * M.litDimmingFraction(level), 1e-12, 'output follows the shown level through the curve');
  near(step.lights[0].intensity, step.lights[0].candela * M.LIT_EXPOSURE, 1e-12, 'pre-exposed');
  let state = step;
  for (let i = 0; i < 200 && state.easing; i++) state = M.easeLights(set(.2), state.shown, 16);
  assert.equal(state.easing, false); assert.equal(state.lights[0].level, .2, 'settles exactly on the set level');
  const snapped = M.easeLights(set(.6), state.shown, 16, { snap: true });
  assert.equal(snapped.lights[0].level, .6); assert.equal(snapped.easing, false, 'reduced motion snaps');
  const fresh = M.easeLights(set(.6), [], 16);
  assert.equal(fresh.lights[0].level, .6, 'a light with no previous step shows its set level');
});
