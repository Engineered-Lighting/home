/* home-3d/lit-model.mjs — the Tier-0 Lit light model, renderer-independent.
 *
 * Pure data and arithmetic with no three.js and no renderer: channel lumens to
 * candela, beam angles, the six radial zone directions, Kelvin display
 * colours and the per-fixture light list. lit-view.mjs turns it into built-in
 * three lights today; a WebGPU renderer can consume the same model unchanged.
 *
 * Every lumen rating here is SPEC's ASSUMPTION (SPEC Lit view), never a
 * measured or profile value, and is disclosed with the view.
 *
 * L1 ("Hyperreal browser room lighting") adds the photometric conventions
 * every Lit renderer shares: the lumen set's source, Filament-style flux
 * integration and a synthetic IES-style LUT from the datasheet FWHM, an
 * assumed dimming curve, constant-luminance CCT, the 160 ms ease and
 * pre-exposure. All ASSUMED: Victoria's Den fixtures are not commissioned.
 */

/* SPEC.md > Lit view: assumed lumen ratings (SPEC also assumes ceiling 800
 * and plug 400, which no Den fixture has). Not measured; never presented as
 * such. Owner decision Q7 (2026-10-19) keeps SPEC's set for Lit until
 * measurement. It supersedes, for Lit only, the fixture profile's max_flux_lm
 * (spot 250, down 400, radial 60) in home-den-fixtures.js, which stays as it
 * is: the fixture model is protected by Den. */
export const LIT_LUMEN_SOURCE = Object.freeze({
    summary: 'SPEC Lit view assumed lumen ratings',
    reference: 'docs/design/apartment-unified/SPEC.md › Lit view',
    status: 'assumed',
    measured: false,
    decision: 'Owner decision Q7, 2026-10-19: SPEC lumen set for Lit until measured fixtures exist',
    supersedes: 'home-den-fixtures.js max_flux_lm (spot 250, down 400, radial 60 lm), for Lit only; that field is unchanged and owned by Den',
});
// The value map compares and serialises as the plain SPEC set; its source
// rides along as a non-enumerable `source`, so litState().assumedLumens
// carries where the numbers come from without changing their shape.
export const LIT_ASSUMED_LUMENS = Object.freeze(Object.defineProperty({ spot: 450, down: 350, zone: 120 },
    'source', { value: LIT_LUMEN_SOURCE, enumerable: false }));
export const LIT_ASSUMPTION_SOURCE = LIT_LUMEN_SOURCE.summary;
export const LIT_DISCLOSURE = 'Simulated · not photometric · assumed output';
export const LIT_DISCLOSURE_DETAIL = 'Assumed output, not measured: spot 450 lm, down 350 lm, each radial zone 120 lm. '
    + 'Materials are assumed from the scan colours. One bounce, ray traced against the scan mesh '
    + 'with assumed albedos; lux at named aims is modeled.';
// The exposure sentence follows from lit-exposure.mjs, per renderer path.
/* L5's bounce (lit-bvh-bake.mjs) traces and shadows every path, so it no
 * longer leaks through walls; what it still does not do is stated with the
 * view. Albedos are assumed: the scan colours x 0.8 (sRGB to linear,
 * clamped), the untinted material's grey where a mesh has no colour. */
export const LIT_BOUNCE_LIMIT = 'One bounce, shadowed · per-vertex detail · assumed albedos · radial zones cast no direct shadows';
export const LIT_BOUNCE_ALBEDO = Object.freeze({ source: 'scan colours x 0.8', clamp: Object.freeze([0.02, 0.9]), untinted: '#9ea4aa', measured: false });
/* The fixed night exposure (prototype scene.js _litApply). It scales every
 * simulated light's candela into display intensity, so a narrow spot reads
 * brighter than a wide wash of the same output, as it does in the room. */
export const LIT_EXPOSURE = 0.038;
export const LIT_TARGET_FPS = 30;
// Prototype beam shapes: the spot cone half-angle is 0.8 x its FWHM in
// degrees with a 0.55 penumbra; the Down ring and each radial zone are
// soft-edged lobes of 1.0 and 0.95 rad.
export const SPOT_HALF_ANGLE_PER_FWHM_DEG = 0.8;
export const SPOT_PENUMBRA = 0.55;
export const DOWN_HALF_ANGLE = 1.0;
export const ZONE_HALF_ANGLE = 0.95;
// Shadow softness: Spot crisp (small aperture), Down soft (diffuser).
export const SHADOW_RADIUS = Object.freeze({ crisp: 2.5, soft: 6 });
// A radial lobe aims outward and slightly down (prototype: d x 2 - 0.35 z).
const ZONE_DROP = -0.35;

/* Candela from a lumen rating spread evenly over a cone of half-angle theta:
 * I = flux / (2 pi (1 - cos theta)). */
export function candelaFromLumens(lumens, halfAngleRad) {
    const solid = 2 * Math.PI * (1 - Math.cos(halfAngleRad));
    return solid > 0 && Number.isFinite(lumens) ? lumens / solid : 0;
}

export function spotHalfAngle(fwhmDeg) {
    return (Math.max(1, Number(fwhmDeg) || 20) * SPOT_HALF_ANGLE_PER_FWHM_DEG) * Math.PI / 180;
}

/* A channel's set level: its brightness when on, else 0. Output follows the
 * level through the assumed dimming curve (litDimmingFraction). */
export function channelLevel(channel) {
    return channel && channel.on === true && Number.isFinite(channel.brightness) && channel.brightness > 0
        ? Math.min(1, channel.brightness) : 0;
}

/* The unit direction of a radial zone in the fixture's Z-up frame. */
export function zoneDirection(azimuthDeg) {
    const a = (Number(azimuthDeg) || 0) * Math.PI / 180;
    const v = [Math.cos(a) * 2, Math.sin(a) * 2, ZONE_DROP];
    const n = Math.hypot(...v);
    return v.map(x => x / n);
}
/* A fixture's six radial zone directions, from each zone's azimuth. */
export const radialZoneDirections = azimuthsDeg => Array.from(azimuthsDeg || [], zoneDirection);

/* Display colour only (Tanner Helland's blackbody fit, as the fixtures use),
 * as sRGB components 0..1. Not a measured spectrum, and not for lighting:
 * Lit lights use cctToLinearRgb (constant luminance). */
export function kelvinRgb(kelvin) {
    const t = (Number.isFinite(kelvin) ? kelvin : 3000) / 100;
    const red = t <= 66 ? 255 : 329.698727446 * Math.pow(t - 60, -0.1332047592);
    const green = t <= 66 ? 99.4708025861 * Math.log(t) - 161.1195681661 : 288.1221695283 * Math.pow(t - 60, -0.0755148492);
    const blue = t >= 66 ? 255 : t <= 19 ? 0 : 138.5177312231 * Math.log(t - 10) - 305.0447927307;
    const c = v => Math.min(255, Math.max(0, v)) / 255;
    return [c(red), c(green), c(blue)];
}

/* The lights one fixture contributes, in its own Z-up frame.
 *   fixture = { spot: { origin, direction, channel } | null,
 *               down: { origin, channel } | null,
 *               center: [x, y, z] | null,
 *               zones: [{ number, azimuth_deg, channel }] }
 * Each light: { kind, number, origin, direction, halfAngle, penumbra, shadow,
 *   level, kelvin, peakCandela, fluxFraction, candela, intensity }: level is
 * the set brightness, fluxFraction = litDimmingFraction(level), candela =
 * peakCandela x fluxFraction and intensity = candela x exposure. */
export function fixtureLights(fixture, { spotFwhmDeg = 20, exposure = LIT_EXPOSURE } = {}) {
    const lights = [];
    // Output: peak candela at full, times the set level through the assumed dimming curve.
    const push = ({ peakCandela, ...light }) => {
        const fluxFraction = litDimmingFraction(light.level), candela = peakCandela * fluxFraction;
        lights.push({ ...light, peakCandela, fluxFraction, candela, intensity: preExpose(candela, exposure) });
    };
    if (fixture?.spot && fixture.spot.origin && fixture.spot.direction) {
        const half = spotHalfAngle(spotFwhmDeg), level = channelLevel(fixture.spot.channel);
        push({ kind: 'spot', number: null, origin: fixture.spot.origin.slice(), direction: fixture.spot.direction.slice(),
            halfAngle: half, penumbra: SPOT_PENUMBRA, shadow: 'crisp', level, kelvin: fixture.spot.channel?.color_temp_kelvin,
            peakCandela: candelaFromLumens(LIT_ASSUMED_LUMENS.spot, half) });
    }
    if (fixture?.down && fixture.down.origin) {
        const level = channelLevel(fixture.down.channel);
        push({ kind: 'down', number: null, origin: fixture.down.origin.slice(), direction: [0, 0, -1],
            halfAngle: DOWN_HALF_ANGLE, penumbra: 1, shadow: 'soft', level, kelvin: fixture.down.channel?.color_temp_kelvin,
            peakCandela: candelaFromLumens(LIT_ASSUMED_LUMENS.down, DOWN_HALF_ANGLE) });
    }
    for (const zone of fixture?.zones || []) {
        if (!fixture.center) break;
        const direction = zoneDirection(zone.azimuth_deg), level = channelLevel(zone.channel);
        push({ kind: 'zone', number: zone.number, origin: fixture.center.map((v, i) => v + direction[i] * 0.05), direction,
            halfAngle: ZONE_HALF_ANGLE, penumbra: 1, shadow: null, level, kelvin: zone.channel?.color_temp_kelvin,
            peakCandela: candelaFromLumens(LIT_ASSUMED_LUMENS.zone, ZONE_HALF_ANGLE) });
    }
    return lights;
}

/* ---- Flux and beam profiles (L1) -------------------------------------------
 * Filament's rule: flux comes from integrating the profile, never from a
 * header. For a rotationally symmetric profile p(theta) (theta from the beam
 * axis, 0..pi, any peak) and a peak intensity in candela,
 * flux = peak x 2 pi x integral p(theta) sin(theta) dtheta, by composite
 * Simpson. `profile` is a function of theta or a LUT from litBeamLut. */
export function integrateFlux(profile, peakCandela = 1, { samples = 2048 } = {}) {
    const p = typeof profile === 'function' ? profile : lutSampler(profile);
    if (typeof p !== 'function' || !Number.isFinite(peakCandela)) return 0;
    const n = Math.max(2, Math.ceil(samples / 2) * 2), h = Math.PI / n;
    let sum = 0;
    for (let i = 0; i <= n; i++) {
        const theta = i * h, weight = i === 0 || i === n ? 1 : i % 2 ? 4 : 2;
        const value = Number(p(theta));
        sum += weight * (Number.isFinite(value) && value > 0 ? value : 0) * Math.sin(theta);
    }
    return peakCandela * 2 * Math.PI * sum * h / 3;
}

/* The peak candela that makes a profile emit `lumens` (the inverse of integrateFlux). */
export function peakCandelaFromLumens(lumens, profile, options) {
    const unit = integrateFlux(profile, 1, options);
    return unit > 0 && Number.isFinite(lumens) ? lumens / unit : 0;
}

/* A Gaussian lobe whose full width at half maximum is fwhmDeg: the spot's
 * datasheet beam angle (Carclo 10511, 20 deg) is the only datasheet-grade
 * optical number in the fixture model. Peak 1 on the axis, 0.5 at fwhm / 2. */
export function gaussianBeamProfile(fwhmDeg) {
    const sigma = (Math.max(1e-3, Number(fwhmDeg) || 0) * Math.PI / 180) / (2 * Math.sqrt(2 * Math.LN2));
    return theta => Math.exp(-(theta * theta) / (2 * sigma * sigma));
}

/* A synthetic IES-style LUT: the rotationally symmetric 180 x 1 red-channel
 * layout IESSpotLight reads (vertical angles 0..179 deg in 1 deg steps from
 * the beam axis), normalised to peak 1. The peak candela travels beside it,
 * as IESLoader drops it. Status is always 'assumed': no goniophotometry. */
export function litBeamLut({ fwhmDeg = 20, lumens = LIT_ASSUMED_LUMENS.spot, size = 180 } = {}) {
    const profile = gaussianBeamProfile(fwhmDeg), values = new Float32Array(size);
    for (let i = 0; i < size; i++) values[i] = profile(i * Math.PI / 180);
    const peak = values.reduce((max, value) => Math.max(max, value), 0) || 1;
    for (let i = 0; i < size; i++) values[i] /= peak;
    const lut = { width: 1, height: size, stepDeg: 1, values, fwhmDeg, profile: 'gaussian', status: 'assumed', measured: false, lumens };
    lut.peakCandela = peakCandelaFromLumens(lumens, lut);
    return Object.freeze(lut);
}
function lutSampler(lut) {
    const values = lut?.values, step = (Number(lut?.stepDeg) || 1) * Math.PI / 180;
    if (!values?.length) return null;
    return theta => {
        const at = theta / step, i = Math.floor(at);
        if (i >= values.length - 1) return i === values.length - 1 && at === i ? values[i] : 0;
        return values[i] + (values[i + 1] - values[i]) * (at - i);
    };
}

/* ---- Dimming (L1) ----------------------------------------------------------
 * ASSUMED: a channel's 0..1 brightness is a perceptual level, and relative
 * flux follows the inverse of CIE 1976 lightness, Y = ((100 s + 16) / 116)^3
 * (linear below the L* toe). It replaces linear brightness x lumens.
 * Continuous, strictly increasing, 0 at 0 and 1 at 1. The real driver curve is
 * owner question Q10; measured fixtures replace this. */
export const LIT_DIMMING_CURVE = Object.freeze({ name: 'cie-lightness-inverse', status: 'assumed', measured: false,
    note: 'Slider level read as CIE L*/100; relative flux = L*^-1. Driver curve unknown until commissioning (Q10).' });
const L_STAR_KAPPA = 24389 / 27, L_STAR_EPSILON = 216 / 24389;
export function litDimmingFraction(level) {
    const s = Number.isFinite(level) ? Math.min(1, Math.max(0, level)) : 0;
    const lightness = 100 * s;
    return lightness > L_STAR_KAPPA * L_STAR_EPSILON ? ((lightness + 16) / 116) ** 3 : lightness / L_STAR_KAPPA;
}

/* ---- Constant-luminance CCT (L1) -------------------------------------------
 * Kim et al.'s cubic spline of the Planckian locus (1667 to 25000 K) -> CIE xy
 * -> XYZ at Y = 1 -> linear sRGB (IEC 61966-2-1, D65), so brightness sets
 * luminance alone and colour carries none. Below about 2000 K the locus is
 * outside sRGB (negative blue); the chroma is pulled toward the equal-
 * luminance grey just far enough to clear it. For lighting only: the orbs and
 * beams keep their own display colour (kelvinRgb). */
export const LIT_CCT_RANGE_K = Object.freeze([1667, 25000]);
export function kelvinToXy(kelvin) {
    const T = Math.min(LIT_CCT_RANGE_K[1], Math.max(LIT_CCT_RANGE_K[0], Number.isFinite(kelvin) ? kelvin : 3000));
    const T2 = T * T, T3 = T2 * T;
    const x = T <= 4000
        ? -0.2661239e9 / T3 - 0.2343589e6 / T2 + 0.8776956e3 / T + 0.179910
        : -3.0258469e9 / T3 + 2.1070379e6 / T2 + 0.2226347e3 / T + 0.240390;
    const x2 = x * x, x3 = x2 * x;
    const y = T <= 2222 ? -1.1063814 * x3 - 1.34811020 * x2 + 2.18555832 * x - 0.20219683
        : T <= 4000 ? -0.9549476 * x3 - 1.37418593 * x2 + 2.09137015 * x - 0.16748867
            : 3.0817580 * x3 - 5.87338670 * x2 + 3.75112997 * x - 0.37001483;
    return { x, y };
}
export function xyToXYZ(x, y, Y = 1) {
    return y > 0 ? [x * Y / y, Y, (1 - x - y) * Y / y] : [0, 0, 0];
}
// IEC 61966-2-1 XYZ -> linear sRGB, and its luminance row (the Y row of the inverse).
const XYZ_TO_SRGB = [[3.2404542, -1.5371385, -0.4985314], [-0.9692660, 1.8760108, 0.0415560], [0.0556434, -0.2040259, 1.0572252]];
export const LINEAR_SRGB_LUMINANCE = Object.freeze([0.2126729, 0.7151522, 0.0721750]);
export const linearSrgbLuminance = ([r, g, b]) => LINEAR_SRGB_LUMINANCE[0] * r + LINEAR_SRGB_LUMINANCE[1] * g + LINEAR_SRGB_LUMINANCE[2] * b;
export function xyzToLinearSrgb([X, Y, Z]) {
    return XYZ_TO_SRGB.map(([a, b, c]) => a * X + b * Y + c * Z);
}
/* Linear sRGB [r, g, b] with luminance exactly 1 and no negative channel. */
export function cctToLinearRgb(kelvin) {
    const { x, y } = kelvinToXy(kelvin);
    let rgb = xyzToLinearSrgb(xyToXYZ(x, y, 1));
    const Y = linearSrgbLuminance(rgb), low = Math.min(...rgb);
    if (low < 0 && Y > 0) {
        // Equal-luminance grey has every channel = Y, so mixing toward it keeps Y.
        const t = Y / (Y - low);
        rgb = rgb.map(value => Y + t * (value - Y));
    }
    const luminance = linearSrgbLuminance(rgb);
    return rgb.map(value => Math.max(0, value / luminance));
}

/* ---- Easing (L1) -----------------------------------------------------------
 * SPEC: Lit lights ease toward each zone's set level with the fixtures' 160 ms
 * time constant (settled in about half a second). The set level updates at
 * once; only the light eases. Same settle thresholds as the radial drawing.
 * Presentation only: easing never feeds back into fixture state or readback. */
export const LIT_EASE_TAU_MS = 160;
export const LIT_EASE_SETTLED_LEVEL = 0.0005, LIT_EASE_SETTLED_KELVIN = 2;
export const litEaseAlpha = (elapsedMs, tauMs = LIT_EASE_TAU_MS) => (elapsedMs > 0 ? -Math.expm1(-elapsedMs / tauMs) : 0);
/* One step of the exponential ease: the next shown value, snapped to the
 * target once within `settled`. */
export function easeToward(shown, target, elapsedMs, { tauMs = LIT_EASE_TAU_MS, settled = LIT_EASE_SETTLED_LEVEL } = {}) {
    if (!Number.isFinite(shown)) return target;
    const next = shown + (target - shown) * litEaseAlpha(elapsedMs, tauMs);
    return Math.abs(target - next) <= settled ? target : next;
}
/* Ease fixtureLights() entries toward their set level and Kelvin. `shown` is
 * the previous step's [{ level, kelvin }] by index (empty to snap). Returns
 * the eased lights (output recomputed through the dimming curve; `setLevel`
 * keeps the exact level), the next `shown` and whether anything still moves. */
export function easeLights(lights, shown, elapsedMs, { snap = false, exposure = LIT_EXPOSURE } = {}) {
    let easing = false;
    const nextShown = [];
    const eased = (lights || []).map((light, index) => {
        const kelvin = Number.isFinite(light.kelvin) ? light.kelvin : 3000, previous = shown?.[index];
        const next = snap || !previous ? { level: light.level, kelvin } : {
            level: easeToward(previous.level, light.level, elapsedMs),
            kelvin: easeToward(previous.kelvin, kelvin, elapsedMs, { settled: LIT_EASE_SETTLED_KELVIN }) };
        nextShown.push(next);
        if (next.level !== light.level || next.kelvin !== kelvin) easing = true;
        const fluxFraction = litDimmingFraction(next.level), candela = light.peakCandela * fluxFraction;
        return { ...light, setLevel: light.level, level: next.level, kelvin: next.kelvin, fluxFraction, candela,
            intensity: preExpose(candela, exposure) };
    });
    return { lights: eased, shown: nextShown, easing };
}

/* ---- Pre-exposure (L1) -----------------------------------------------------
 * Filament's camera model: exposure = 1 / (1.2 x 2^EV100) (K = 12.5). Lights
 * are pre-exposed on the CPU in double precision, so half-float targets never
 * see raw photometric values. Tier-0's fixed LIT_EXPOSURE 0.038 is EV100 4.45. */
export const exposureFromEv100 = ev100 => 1 / (1.2 * 2 ** ev100);
export const ev100FromExposure = exposure => (exposure > 0 ? Math.log2(1 / (1.2 * exposure)) : NaN);
export function preExpose(intensity, exposure) {
    return Number.isFinite(intensity) && Number.isFinite(exposure) ? intensity * exposure : 0;
}
