/* home-3d/lit-exposure.mjs — Lit's exposure and tone policy (L2, "Hyperreal
 * browser room lighting": lights off reads naturally only with a fixed night
 * exposure that adapts partly).
 *
 * Pure and renderer-independent like lit-model.mjs: no imports, no three.js,
 * no DOM. It holds
 *
 *   the policy        one fixed design-night EV100 (Tier-0's pre-exposure,
 *                     Filament's camera model) plus a bounded partial
 *                     adaptation: about 60 % of the metered EV drop, capped at
 *                     +5.5 EV, easing fast when the room brightens and slowly
 *                     when it darkens;
 *   the ambient floor a disclosed, ASSUMED night ambient (moonlight or street
 *                     light through the windows) that replaces Tier-0's
 *                     undisclosed hemisphere fill;
 *   the display chain per-pixel mesopic desaturation and blue shift below a
 *                     few cd/m², the adaptation gain, AgX with a lifted toe
 *                     (Reinhard as the fallback), and the comparison views
 *                     (linear and Khronos PBR Neutral, without presentation
 *                     effects);
 *   CPU twins         of every display step, the reference the TSL is checked
 *                     against;
 *   TSL builders      that take the 'three/tsl' namespace as an argument, so
 *                     this module stays loadable on the WebGL path and in Node.
 *
 * Night adaptation is presentation, never photometry: a dimmed render is not
 * lux, and everything here is assumed, not measured.
 */

/* ---- Design night and partial adaptation ------------------------------------
 * The fixed design-night EV100 is Tier-0's pre-exposure (LIT_EXPOSURE 0.038,
 * EV100 4.45 through Filament's exposure = 1 / (1.2 x 2^EV100)). Lights stay
 * pre-exposed at it on the CPU; adaptation is a gain on top, applied only in
 * the display chain, so photometric values never under- or overflow fp16 and
 * every light keeps intensity = candela x the design exposure.
 *
 * What the eye adapts to is metered, as spike 3's policy (d) did: the scene-
 * referred luminance of what the view shows. The drop is how far its EV100 sits
 * below the design night; the eye recovers `fraction` of it, never more than
 * `capEv` and never below 0. */
export const LIT_ADAPTATION = Object.freeze({
    fraction: 0.6,          // of the metered EV drop below the design night
    capEv: 5.5,             // at most +5.5 EV
    tauBrightenMs: 750,     // the room got brighter: the eye adapts fast
    tauDarkenMs: 3000,      // the room got darker: dark adaptation is slow
    settledEv: 0.01,
    presentation: true, measured: false,
});

/* The meter: the log-average of the 50th-95th percentile band of the lit
 * mesh's scene luminance (cd/m²), on a coarse grid of the scene-referred
 * target, read back at most every 200 ms. The band ignores the darkest half
 * (shadows, the unlit) and the brightest 5 % (fixture bezels, hot pools). */
export const LIT_METER = Object.freeze({ low: 0.5, high: 0.95, periodMs: 200, width: 48, height: 27 });
// Reflected-light meter calibration (Filament, ISO 2720): EV100 = log2(L x 100 / K).
export const LIT_METER_K = 12.5;
export const ev100FromLuminance = luminance => (luminance > 0 ? Math.log2(luminance * 100 / LIT_METER_K) : -Infinity);

/* The disclosed night ambient: moonlight or street light through the windows,
 * 0.2 lx (inside the 0.1-1 lx range), about 4100 K, as a sky/ground
 * hemisphere because Tier-0 does not model the window openings. ASSUMED,
 * never measured. */
export const LIT_AMBIENT_FLOOR = Object.freeze({
    lux: 0.2, kelvin: 4100, groundRatio: 0.3, status: 'assumed', measured: false,
    source: 'Assumed night ambient: moonlight or street light through the windows, about 0.2 lx. Not modeled through the openings; not measured.',
});

/* The metered luminance (cd/m²) of `samples` (scene luminance per sample;
 * non-positive and non-finite samples are not the scene), or null. */
export function meterLuminance(samples, meter = LIT_METER) {
    const values = Float64Array.from(Array.from(samples || []).filter(value => Number.isFinite(value) && value > 0)).sort();
    if (!values.length) return null;
    const low = Math.min(values.length - 1, Math.floor(values.length * meter.low));
    const high = Math.max(low + 1, Math.min(values.length, Math.ceil(values.length * meter.high)));
    let sum = 0;
    for (let i = low; i < high; i++) sum += Math.log2(values[i]);
    return 2 ** (sum / (high - low));
}

/* The adaptation a metered luminance asks for, in EV above the design night. */
export function adaptationFromLuminance(meteredCdM2, designEv100, policy = LIT_ADAPTATION) {
    if (!(meteredCdM2 > 0) || !Number.isFinite(designEv100)) return { dropEv: 0, adaptEv: 0 };
    const dropEv = Math.max(0, designEv100 - ev100FromLuminance(meteredCdM2));
    return { dropEv, adaptEv: Math.min(policy.capEv, policy.fraction * dropEv) };
}

/* One step of the adaptation ease, in EV. Rising adaptation means the room
 * darkened (slow); falling means it brightened (fast). Snaps once settled. */
export function easeAdaptation(currentEv, targetEv, elapsedMs, { snap = false, policy = LIT_ADAPTATION } = {}) {
    if (snap || !Number.isFinite(currentEv) || Number.isNaN(elapsedMs)) return { ev: targetEv, adapting: false };
    const tau = targetEv > currentEv ? policy.tauDarkenMs : policy.tauBrightenMs;
    const next = currentEv + (targetEv - currentEv) * (elapsedMs > 0 ? -Math.expm1(-elapsedMs / tau) : 0);
    return Math.abs(targetEv - next) <= policy.settledEv ? { ev: targetEv, adapting: false } : { ev: next, adapting: true };
}

export const adaptationGain = adaptEv => 2 ** (Number.isFinite(adaptEv) ? adaptEv : 0);
// Filament's camera model, as lit-model.mjs: exposure = 1 / (1.2 x 2^EV100).
export const ev100FromExposure = exposure => (exposure > 0 ? Math.log2(1 / (1.2 * exposure)) : NaN);

/* ---- Display chain ------------------------------------------------------------
 * Operators: the view is AgX with a lifted toe, Reinhard the proven fallback;
 * 'neutral' (Khronos PBR Neutral) and 'linear' are comparison views. The
 * comparison views show the fixed design exposure with no presentation
 * effects (no adaptation, no mesopic shift, no toe), for calibration. */
export const LIT_TONE_OPERATORS = Object.freeze(['agx', 'reinhard', 'neutral', 'linear']);
export const LIT_TONE = Object.freeze({
    view: 'agx', fallback: 'reinhard', compare: Object.freeze(['linear', 'neutral']),
    /* The lifted toe, on luminance before AgX: Y' = Y + lift x Y / (1 + Y / knee).
     * About +1.6 EV for the deepest shadows, under +0.1 EV at mid grey; AgX's
     * own toe crushes everything below about 1 % to black (spike 3). */
    toe: Object.freeze({ lift: 2, knee: 0.004 }),
});
export const litTonePresentation = operator => operator === 'agx' || operator === 'reinhard';

/* Per-pixel mesopic shift (after Jensen et al., "A Night Rendering", UUCS-00-016):
 * from photopic at 10^0.6 (about 4) cd/m² to fully shifted at 0.01 cd/m², by
 * the pixel's own scene luminance, so a dim warm pool stays warm while the
 * moonlit room turns blue-grey. Luminance is kept; only chroma moves toward
 * a blue-grey of the same luminance. */
const LUMA = [0.2126729, 0.7151522, 0.0721750];
export const lumaOf = ([r, g, b]) => LUMA[0] * r + LUMA[1] * g + LUMA[2] * b;
// Spike 3's blue-grey, scaled to luminance 1.
const MESOPIC_TINT = [0.8, 0.92, 1.25];
export const LIT_MESOPIC = Object.freeze({
    photopicLog10: 0.6, spanLog10: 2.6, strength: 0.85,
    tint: Object.freeze(MESOPIC_TINT.map(value => value / lumaOf(MESOPIC_TINT))),
});

export function mesopicWeight(luminanceCdM2, mesopic = LIT_MESOPIC) {
    const L = Math.max(Number.isFinite(luminanceCdM2) ? luminanceCdM2 : 0, 1e-6);
    const x = Math.min(1, Math.max(0, (mesopic.photopicLog10 - Math.log10(L)) / mesopic.spanLog10));
    return x * x * (3 - 2 * x) * mesopic.strength;
}
/* `rgb` scene-referred and pre-exposed; `preExposure` turns it back into cd/m². */
export function mesopicShift(rgb, preExposure, mesopic = LIT_MESOPIC) {
    const Y = lumaOf(rgb), s = mesopicWeight(Y / preExposure, mesopic);
    return rgb.map((value, i) => value + (mesopic.tint[i] * Y - value) * s);
}
export function liftToe(rgb, toe = LIT_TONE.toe) {
    const Y = lumaOf(rgb);
    if (!(Y > 1e-10)) return rgb.slice();
    const lifted = Y + toe.lift * Y / (1 + Y / toe.knee);
    return rgb.map(value => value * lifted / Y);
}

// three r186 ToneMappingFunctions, ported line for line (spike 3 checked them).
const col = (columns, [x, y, z]) => [0, 1, 2].map(i => columns[0][i] * x + columns[1][i] * y + columns[2][i] * z);
const SRGB_TO_REC2020 = [[0.6274, 0.0691, 0.0164], [0.3293, 0.9195, 0.0880], [0.0433, 0.0113, 0.8956]];
const REC2020_TO_SRGB = [[1.6605, -0.1246, -0.0182], [-0.5876, 1.1329, -0.1006], [-0.0728, -0.0083, 1.1187]];
const AGX_INSET = [[0.856627153315983, 0.137318972929847, 0.11189821299995], [0.0951212405381588, 0.761241990602591, 0.0767994186031903],
    [0.0482516061458583, 0.101439036467562, 0.811302368396859]];
const AGX_OUTSET = [[1.1271005818144368, -0.1413297634984383, -0.14132976349843826], [-0.11060664309660323, 1.157823702216272, -0.11060664309660294],
    [-0.016493938717834573, -0.016493938717834257, 1.2519364065950405]];
const clamp01 = value => Math.min(1, Math.max(0, value));
// As columns (GLSL mat3 order), shared with the camera passes in lit-basis-blend.mjs.
export const LIT_AGX_MATRICES = Object.freeze({ srgbToRec2020: SRGB_TO_REC2020, inset: AGX_INSET, outset: AGX_OUTSET, rec2020ToSrgb: REC2020_TO_SRGB });
export const LIT_TONE_CPU = Object.freeze({
    linear: rgb => rgb.map(clamp01),
    reinhard: rgb => rgb.map(value => clamp01(value / (1 + value))),
    agx(rgb) {
        let c = col(AGX_INSET, col(SRGB_TO_REC2020, rgb));
        c = c.map(value => clamp01((Math.log2(Math.max(value, 1e-10)) + 12.47393) / (4.026069 + 12.47393)));
        c = c.map(x => { const x2 = x * x, x4 = x2 * x2; return 15.5 * x4 * x2 - 40.14 * x4 * x + 31.96 * x4 - 6.868 * x2 * x + 0.4298 * x2 + 0.1191 * x - 0.00232; });
        c = col(AGX_OUTSET, c).map(value => Math.max(0, value) ** 2.2);
        return col(REC2020_TO_SRGB, c).map(clamp01);
    },
    neutral(rgb) {
        const start = 0.8 - 0.04, desaturation = 0.15, x = Math.min(...rgb);
        const offset = x < 0.08 ? x - 6.25 * x * x : 0.04;
        let c = rgb.map(value => value - offset);
        const peak = Math.max(...c);
        if (peak < start) return c;
        const d = 1 - start, newPeak = 1 - d * d / (peak + d - start);
        c = c.map(value => value * newPeak / peak);
        const g = 1 - 1 / (desaturation * (peak - newPeak) + 1);
        return c.map(value => value + (newPeak - value) * g);
    },
});
export const srgbEncode = value => (value <= 0.0031308 ? 12.92 * value : 1.055 * value ** (1 / 2.4) - 0.055);

/* The whole chain for one scene-referred, pre-exposed pixel: display-linear
 * rgb (0..1). The view operators get the mesopic shift, the adaptation gain
 * and (AgX) the lifted toe; the comparison views get none of them. */
export function litDisplayPixel(rgb, { preExposure, gain = 1, operator = LIT_TONE.view, mesopic = LIT_MESOPIC, toe = LIT_TONE.toe } = {}) {
    if (!LIT_TONE_OPERATORS.includes(operator)) throw new RangeError('lit_tone_operator_unknown');
    let c = rgb.map(value => Math.max(0, value));
    if (litTonePresentation(operator)) {
        c = mesopicShift(c, preExposure, mesopic).map(value => value * gain);
        if (operator === 'agx') c = liftToe(c, toe);
    }
    return LIT_TONE_CPU[operator](c);
}

/* ---- TSL ----------------------------------------------------------------------
 * The same chain as a node, for the WebGPU path's tone pass. `T` is the
 * 'three/tsl' namespace; `color` is the scene-referred vec4, `preExposure`
 * and `gain` are float nodes (uniforms). The operator is chosen at build
 * time. Output is display-encoded sRGB, the canvas's convention on both
 * paths (webgpu-material-ports.mjs). */
export function litDisplayNode(T, { color, preExposure, gain, operator = LIT_TONE.view, mesopic = LIT_MESOPIC, toe = LIT_TONE.toe }) {
    if (!LIT_TONE_OPERATORS.includes(operator)) throw new RangeError('lit_tone_operator_unknown');
    const { Fn, float, vec3, vec4, max, clamp, mix, log, luminance, sRGBTransferOETF } = T;
    return Fn(() => {
        const c = max(color.rgb, vec3(0)).toVar();
        if (litTonePresentation(operator)) {
            const Y = luminance(c).toVar();
            const L = max(Y.div(preExposure), float(1e-6));
            const x = clamp(float(mesopic.photopicLog10).sub(log(L).div(Math.LN10)).div(mesopic.spanLog10), 0, 1);
            const s = x.mul(x).mul(float(3).sub(x.mul(2))).mul(mesopic.strength);
            c.assign(mix(c, vec3(...mesopic.tint).mul(Y), s).mul(gain));
            if (operator === 'agx') {
                const Ya = luminance(c).toVar();
                const lifted = Ya.add(Ya.mul(toe.lift).div(Ya.div(toe.knee).add(1)));
                c.mulAssign(lifted.div(max(Ya, float(1e-10))));
            }
        }
        const mapped = operator === 'agx' ? T.agxToneMapping(c, float(1))
            : operator === 'reinhard' ? T.reinhardToneMapping(c, float(1))
                : operator === 'neutral' ? clamp(T.neutralToneMapping(c, float(1)), 0, 1)
                    : clamp(c, 0, 1);
        return vec4(sRGBTransferOETF(mapped), color.a);
    })();
}

/* ---- Disclosure ---------------------------------------------------------------
 * What the view says about its exposure, per path. Never lux, never measured. */
export function litExposureDisclosure({ adaptive, designEv100 }) {
    const floor = `Assumed night ambient about ${LIT_AMBIENT_FLOOR.lux} lx (moonlight or street light through the windows).`;
    return adaptive
        ? `Fixed design-night exposure (EV100 ${designEv100.toFixed(2)}) with partial night adaptation: presentation, not lux. ${floor}`
        : `Fixed night exposure; this renderer has no night adaptation, so the ambient is shown night-adapted. ${floor}`;
}

/* ---- The camera look ------------------------------------------------------------
 * ("Night relighting realism" research, shortlist #2.) L2's chain above is an
 * EYE model: partial adaptation to the mid-tones, a lifted toe and the
 * Purkinje blue shift. People judge "photoreal" against photos, and a night
 * photo does the opposite (Liba et al. 2019, Night Sight: more contrast,
 * shadows crushed to black, light sources kept from blowing out). The camera
 * look is presentation too, never lux:
 *   exposure  metered toward the highlights: the log-average of the 90th-99th
 *             percentile of what is in view is moved toward display 0.8, by
 *             L2's partial adaptation (about 60 % of the way);
 *   toe       none, and no mesopic shift (a camera has no rods);
 *   tone      AgX with Blender's Punchy look (CDL power 1.35 in AgX space);
 *   colour    a camera white balance toward the lit fixtures: von Kries in
 *             CAT16 cone space at incomplete adaptation D = 0.75, so a 4100 K
 *             ambient reads cool beside 3000 K pools, as in a real photo. */
export const LIT_CAMERA_LOOK = Object.freeze({
    name: 'camera', meter: Object.freeze({ low: 0.9, high: 0.99 }), highlightDisplay: 0.8, minEv: -4, maxEv: 5.5,
    // L2's approved partial adaptation (about 60 %), so dimming still reads as dimmer.
    fraction: 0.6,
    agxPower: 1.35, whiteBalanceD: 0.75, presentation: true, measured: false,
});
// Spike-3/L2 eye look, for comparison and for the Mesh view.
export const LIT_EYE_LOOK = Object.freeze({ name: 'eye', presentation: true, measured: false });

// IEC 61966-2-1 linear sRGB <-> XYZ (D65), and CAT16 (Li et al. 2017).
const SRGB_TO_XYZ = [[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]];
const XYZ_TO_SRGB_M = [[3.2404542, -1.5371385, -0.4985314], [-0.9692660, 1.8760108, 0.0415560], [0.0556434, -0.2040259, 1.0572252]];
const CAT16 = [[0.401288, 0.650173, -0.051461], [-0.250268, 1.204414, 0.045854], [-0.002079, 0.048952, 0.953127]];
const mat = (a, b) => a.map(row => b[0].map((_, j) => row.reduce((sum, value, k) => sum + value * b[k][j], 0)));
const apply = (m, v) => m.map(row => row[0] * v[0] + row[1] * v[1] + row[2] * v[2]);
function invert3(m) {
    const [[a, b, c], [d, e, f], [g, h, i]] = m, A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g;
    const det = a * A + b * B + c * C;
    return [[A / det, -(b * i - c * h) / det, (b * f - c * e) / det], [B / det, (a * i - c * g) / det, -(a * f - c * d) / det],
        [C / det, -(a * h - b * g) / det, (a * e - b * d) / det]];
}
const CAT16_INV = invert3(CAT16);
/* The 3 x 3 white balance (linear sRGB rows) that adapts `sourceWhiteRgb`
 * (linear sRGB, any luminance) toward D65 white at degree D. Identity at D = 0. */
export function whiteBalanceMatrix(sourceWhiteRgb, D = LIT_CAMERA_LOOK.whiteBalanceD) {
    const source = apply(CAT16, apply(SRGB_TO_XYZ, sourceWhiteRgb.map(v => v / Math.max(1e-9, lumaOf(sourceWhiteRgb)))));
    const target = apply(CAT16, apply(SRGB_TO_XYZ, [1, 1, 1]));
    const gains = [0, 1, 2].map(i => D * target[i] / Math.max(1e-9, source[i]) + (1 - D));
    const diag = [[gains[0], 0, 0], [0, gains[1], 0], [0, 0, gains[2]]];
    const m = mat(XYZ_TO_SRGB_M, mat(CAT16_INV, mat(diag, mat(CAT16, SRGB_TO_XYZ))));
    // Keep the source white's luminance: a white balance moves colour, not exposure.
    const scale = lumaOf(sourceWhiteRgb) / Math.max(1e-9, lumaOf(apply(m, sourceWhiteRgb)));
    return m.map(row => row.map(v => v * scale));
}
/* AgX with the Punchy look's CDL power, as the GLSL and TSL camera passes do. */
export function agxPunchy(rgb, power = LIT_CAMERA_LOOK.agxPower) {
    let c = col(AGX_INSET, col(SRGB_TO_REC2020, rgb));
    c = c.map(value => clamp01((Math.log2(Math.max(value, 1e-10)) + 12.47393) / (4.026069 + 12.47393)));
    c = c.map(x => { const x2 = x * x, x4 = x2 * x2; return 15.5 * x4 * x2 - 40.14 * x4 * x + 31.96 * x4 - 6.868 * x2 * x + 0.4298 * x2 + 0.1191 * x - 0.00232; });
    c = c.map(value => Math.max(0, value) ** power);
    c = col(AGX_OUTSET, c).map(value => Math.max(0, value) ** 2.2);
    return col(REC2020_TO_SRGB, c).map(clamp01);
}
/* One pre-exposed pixel through the camera look: display-linear rgb (0..1). */
export function litCameraPixel(rgb, { gain = 1, whiteBalance = null, look = LIT_CAMERA_LOOK } = {}) {
    let c = rgb.map(value => Math.max(0, value) * gain);
    if (whiteBalance) c = apply(whiteBalance, c).map(value => Math.max(0, value));
    return agxPunchy(c, look.agxPower);
}
// The pre-exposed grey whose camera-look display value (sRGB-encoded) is 0.8.
let highlightLinear = null;
export function cameraHighlightLinear(look = LIT_CAMERA_LOOK) {
    if (highlightLinear !== null && look === LIT_CAMERA_LOOK) return highlightLinear;
    let lo = 1e-6, hi = 1e3;
    for (let i = 0; i < 80; i++) {
        const mid = Math.sqrt(lo * hi);
        if (srgbEncode(agxPunchy([mid, mid, mid], look.agxPower)[1]) < look.highlightDisplay) lo = mid; else hi = mid;
    }
    const value = Math.sqrt(lo * hi);
    if (look === LIT_CAMERA_LOOK) highlightLinear = value;
    return value;
}
/* The camera's exposure in EV above the design night: the metered highlight
 * luminance (cd/m², 90th-99th percentile band) placed at display 0.8. */
export function cameraAdaptationFromLuminance(highlightCdM2, preExposure, look = LIT_CAMERA_LOOK) {
    if (!(highlightCdM2 > 0) || !(preExposure > 0)) return { dropEv: 0, adaptEv: 0 };
    const ev = Math.log2(cameraHighlightLinear(look) / (highlightCdM2 * preExposure));
    return { dropEv: Math.max(0, ev), adaptEv: Math.min(look.maxEv, Math.max(look.minEv, look.fraction * ev)) };
}
export function litCameraDisclosure({ kelvin }) {
    return `Camera look: exposure set for the highlights, white balance toward about ${Math.round(kelvin / 100) * 100} K: presentation, not lux.`;
}
