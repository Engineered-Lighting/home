/* home-3d/lit-display.mjs — Lighting's one HDR frame: the per-pixel display
 * pass, energy-conserving bloom and the visible fixture faces ("Night
 * relighting realism" research, shortlist #1 and #3).
 *
 * Pure and renderer-independent like lit-exposure.mjs: no three.js, no DOM.
 * lit-frame.mjs draws with it. Splats, points and the mesh are rendered
 * scene-referred (pre-exposed linear, Filament's design-night exposure) into
 * one half-float frame; this module holds what runs over that frame:
 *
 *   bloom       Jimenez's COD: Advanced Warfare chain (SIGGRAPH 2014): six
 *               mips from half resolution, a 13-tap downsample with a Karis
 *               average on the first mip, a 3 x 3 tent upsample, each level
 *               a convex mix with the coarser one (in the shader, into its
 *               own target), so the chain is an average of blurs and keeps
 *               the frame's mean. It is mixed in
 *               before the tone curve, out = mix(scene, bloom, 0.03).
 *   emitters    each lit fixture's face (the spot's lens, the down's
 *               aperture, a radial zone's diffuser) at the luminance its
 *               assumed lumens imply over an assumed aperture. A spot's lens
 *               is about 10^7 cd/m², beyond half float once pre-exposed, so
 *               the frame stores it clamped and tags the pixel (alpha >= 2
 *               holds log2 of the clamp); the first bloom mip reads the tag and
 *               adds the true energy outside the Karis average, which would
 *               otherwise treat a lamp as a firefly.
 *   display     once per pixel: the bloom mix, the exposure gain, the camera
 *               white balance (CAT16), AgX with the Punchy power, sRGB, then
 *               simulated sensor noise (signal-dependent, stable while the
 *               camera rests) and a triangular dither against banding. L2's
 *               eye chain and the comparison views (linear, PBR Neutral) are
 *               kept; the comparison views get no presentation effect at all.
 *
 * Every step has a CPU twin here (tests check the GPU passes against them),
 * GLSL for the default WebGL renderer (r180) and TSL for the WebGPU path
 * (r186; `T` is the 'three/tsl' namespace, passed in so this module stays
 * loadable in Node and on the WebGL path).
 *
 * All of it is presentation: none of it enters lux, the linear readback or
 * the meter. The emitter apertures, the bloom weights and the noise model are
 * assumed, never measured.
 */
const search = new URL(import.meta.url).search;
const Exposure = await import(`./lit-exposure.mjs${search}`);

/* ---- Constants -------------------------------------------------------------- */
// Half float's largest finite value, and the clamp the frame stores emitters at.
export const LIT_HALF_MAX = 65504;
export const LIT_FRAME = Object.freeze({ format: 'RGBA16F', preExposed: true, emitterClamp: 6e4, emitterTag: 2 });

/* The bloom (COD: AW, LearnOpenGL "Phys. Based Bloom"). `spread` is how much
 * of each level comes from the coarser one: the levels' weights are
 * (1 - s), s (1 - s), s^2 (1 - s), ..., s^5, a narrow core and a broad weak
 * tail. ASSUMED: no glare spread function is published for a phone lens; the
 * owner's photo of one LED in a dark room would replace these. */
export const LIT_BLOOM = Object.freeze({ mips: 6, strength: 0.03, spread: 0.6, radius: 1, karis: true,
    presentation: true, measured: false,
    source: 'Camera glare (presentation): an energy-conserving bloom (COD: Advanced Warfare), mixed at 3 % before the tone curve; assumed, not measured from this phone.' });

/* Simulated sensor noise in display-linear luminance: variance a·Y + b
 * (Poisson-Gaussian, after Wei et al. 2020), luminance only, re-drawn only
 * when the camera moves. Then a triangular dither of one 8-bit step. ASSUMED. */
export const LIT_NOISE = Object.freeze({ shot: 1e-4, read: 1e-6, dither: 1 / 255, presentation: true, measured: false,
    source: 'Simulated sensor noise (presentation), still while the camera rests; assumed.' });

/* The fixtures' emitting faces. ASSUMED apertures (no drawing of the Den
 * fixtures' optics is on file): the spot is a Carclo 10511 frosted triple
 * optic, three cells of about 10 mm; the down a 60 mm aperture; a radial zone
 * a 50 x 40 mm diffuser segment. Luminance = the assumed output over that
 * area: a spot's peak candela / area (falling off with its beam outside the
 * cone, with a little frosted spill), a Lambertian face's flux / (pi x area). */
export const LIT_EMITTERS = Object.freeze({
    spot: Object.freeze({ areaM2: 2.4e-4, lambertian: false, spill: 0.01 }),
    down: Object.freeze({ areaM2: Math.PI * 0.03 ** 2, lambertian: true }),
    zone: Object.freeze({ areaM2: 2e-3, lambertian: true }),
    offsetM: 0.004, status: 'assumed', measured: false,
    source: 'Fixture faces drawn at the luminance their assumed lumens imply over assumed apertures (spot lens about 2.4 cm², down 60 mm, zone diffuser 20 cm²); presentation, not measured.',
});

export const LIT_FRAME_DISCLOSURE = 'One HDR frame: ' + LIT_EMITTERS.source + ' ' + LIT_BLOOM.source + ' ' + LIT_NOISE.source;

/* ---- Emitter luminance (cd/m², modeled) -------------------------------------
 * `light` is a lit-model fixtureLights() entry (eased): kind, candela,
 * fluxFraction, kelvin, halfAngle, penumbra. `lumens` the assumed set. */
export function emitterLuminance(light, lumens) {
    const face = LIT_EMITTERS[light?.kind];
    if (!face) return 0;
    if (!face.lambertian) return Math.max(0, Number(light.candela) || 0) / face.areaM2;
    const flux = (lumens?.[light.kind] || 0) * (Number.isFinite(light.fluxFraction) ? light.fluxFraction : 0);
    return Math.max(0, flux) / (Math.PI * face.areaM2);
}
/* How bright the face looks from `cosView` off its axis (1 on axis): a
 * Lambertian face is constant over its front hemisphere; a spot lens follows
 * its cone (three's smoothstep between the outer and inner cosines) down to
 * its frosted spill. */
export function emitterViewFactor(kind, cosView, { cosOuter = 0, cosInner = 1 } = {}) {
    const face = LIT_EMITTERS[kind];
    if (!face || !(cosView > 0)) return 0;
    if (face.lambertian) return 1;
    const t = Math.min(1, Math.max(0, (cosView - cosOuter) / Math.max(1e-6, cosInner - cosOuter)));
    return face.spill + (1 - face.spill) * t * t * (3 - 2 * t);
}
/* One emitter pixel as the frame stores it: the pre-exposed radiance (rgb)
 * scaled down to the clamp, and the tag alpha = 2 + log2(true / stored). */
export function emitterPixel(rgb, clamp = LIT_FRAME.emitterClamp) {
    const peak = Math.max(rgb[0], rgb[1], rgb[2], 0);
    const s = peak > clamp ? clamp / peak : 1;
    return [rgb[0] * s, rgb[1] * s, rgb[2] * s, LIT_FRAME.emitterTag + Math.log2(1 / s)];
}
// What a tagged pixel really holds (rgb), and its stand-in for the Karis groups (0).
const tagged = a => a >= LIT_FRAME.emitterTag - 0.5;
const untag = (rgb, a) => { const k = 2 ** Math.max(0, a - LIT_FRAME.emitterTag); return [rgb[0] * k, rgb[1] * k, rgb[2] * k]; };

/* ---- Mip sizes ---------------------------------------------------------------- */
export function bloomSizes(width, height, mips = LIT_BLOOM.mips) {
    const sizes = [];
    let w = width, h = height;
    for (let i = 0; i < mips; i++) { w = Math.max(1, w >> 1); h = Math.max(1, h >> 1); sizes.push([w, h]); }
    return sizes;
}

/* ---- CPU twins of the bloom passes -----------------------------------------
 * Images are Float32Array RGBA, row-major, `width` x `height`; reads clamp to
 * the edge. The GPU passes read with texelFetch and filter by hand, so the
 * twins are exact up to half-float rounding. */
const LUMA = [0.2126729, 0.7151522, 0.0721750];
const luma = c => LUMA[0] * c[0] + LUMA[1] * c[1] + LUMA[2] * c[2];
function fetch(image, width, height, x, y) {
    const cx = Math.min(width - 1, Math.max(0, x)), cy = Math.min(height - 1, Math.max(0, y)), o = (cy * width + cx) * 4;
    return [image[o], image[o + 1], image[o + 2], image[o + 3]];
}
// The 13 taps (source texel offsets from the destination centre) and their plain weights.
export const BLOOM_TAPS = Object.freeze([[-2, 2], [0, 2], [2, 2], [-2, 0], [0, 0], [2, 0], [-2, -2], [0, -2], [2, -2], [-1, 1], [1, 1], [-1, -1], [1, -1]]);
const TAP_WEIGHTS = [0.03125, 0.0625, 0.03125, 0.0625, 0.125, 0.0625, 0.03125, 0.0625, 0.03125, 0.125, 0.125, 0.125, 0.125];
// Karis groups: the inner box (j k l m) and four corner boxes, by tap index.
const KARIS_GROUPS = [[[9, 10, 11, 12], 0.5], [[0, 1, 3, 4], 0.125], [[1, 2, 4, 5], 0.125], [[3, 4, 6, 7], 0.125], [[4, 5, 7, 8], 0.125]];
/* One bilinear tap at a source texel corner: the mean of the 2 x 2 texels
 * around it; `first` splits off tagged emitter texels (their true value). */
function cornerTap(image, width, height, cx, cy, first) {
    const plain = [0, 0, 0], emitter = [0, 0, 0];
    for (const [dx, dy] of [[-1, -1], [0, -1], [-1, 0], [0, 0]]) {
        const t = fetch(image, width, height, cx + dx, cy + dy);
        if (first && tagged(t[3])) { const u = untag(t, t[3]); for (let c = 0; c < 3; c++) emitter[c] += u[c] * 0.25; }
        else for (let c = 0; c < 3; c++) plain[c] += t[c] * 0.25;
    }
    return { plain, emitter };
}
/* The 13-tap downsample of `source` (sw x sh) into (dw x dh). The first mip
 * reads the frame: a Karis average (weights 1 / (1 + luma)) over the five
 * boxes, plus the emitters' true energy with the plain weights, all scaled by
 * the bloom strength; later mips are the plain 13-tap filter. */
export function bloomDownsample(source, sw, sh, dw, dh, { first = false, strength = LIT_BLOOM.strength, karis = LIT_BLOOM.karis } = {}) {
    const out = new Float32Array(dw * dh * 4);
    for (let y = 0; y < dh; y++) for (let x = 0; x < dw; x++) {
        const taps = BLOOM_TAPS.map(([ox, oy]) => cornerTap(source, sw, sh, 2 * x + 1 + ox, 2 * y + 1 + oy, first));
        const rgb = [0, 0, 0];
        if (first && karis) {
            let total = 0;
            for (const [members, weight] of KARIS_GROUPS) {
                const g = [0, 1, 2].map(c => members.reduce((sum, m) => sum + taps[m].plain[c], 0) / 4);
                const w = weight / (1 + luma(g));
                total += w;
                for (let c = 0; c < 3; c++) rgb[c] += w * g[c];
            }
            for (let c = 0; c < 3; c++) rgb[c] /= total;
        } else taps.forEach((tap, m) => { for (let c = 0; c < 3; c++) rgb[c] += tap.plain[c] * TAP_WEIGHTS[m]; });
        if (first) taps.forEach((tap, m) => { for (let c = 0; c < 3; c++) rgb[c] += tap.emitter[c] * TAP_WEIGHTS[m]; });
        const o = (y * dw + x) * 4, k = first ? strength : 1;
        out[o] = rgb[0] * k; out[o + 1] = rgb[1] * k; out[o + 2] = rgb[2] * k; out[o + 3] = 1;
    }
    return out;
}
// Bilinear at a texel-centre coordinate (x, y), clamped to the edge.
function bilinear(image, width, height, x, y) {
    const x0 = Math.floor(x), y0 = Math.floor(y), fx = x - x0, fy = y - y0;
    const a = fetch(image, width, height, x0, y0), b = fetch(image, width, height, x0 + 1, y0);
    const c = fetch(image, width, height, x0, y0 + 1), d = fetch(image, width, height, x0 + 1, y0 + 1);
    return [0, 1, 2].map(i => (a[i] * (1 - fx) + b[i] * fx) * (1 - fy) + (c[i] * (1 - fx) + d[i] * fx) * fy);
}
const TENT = [[-1, -1, 1], [0, -1, 2], [1, -1, 1], [-1, 0, 2], [0, 0, 4], [1, 0, 2], [-1, 1, 1], [0, 1, 2], [1, 1, 1]];
/* The tent upsample of the coarser level (sw x sh) at each pixel of `dest`
 * (dw x dh), mixed in place: dest = (1 - spread) dest + spread tent. */
export function bloomUpsample(source, sw, sh, dest, dw, dh, { spread = LIT_BLOOM.spread, radius = LIT_BLOOM.radius } = {}) {
    for (let y = 0; y < dh; y++) for (let x = 0; x < dw; x++) {
        const px = (x + 0.5) * sw / dw - 0.5, py = (y + 0.5) * sh / dh - 0.5, rgb = [0, 0, 0];
        for (const [dx, dy, w] of TENT) {
            const s = bilinear(source, sw, sh, px + dx * radius, py + dy * radius);
            for (let c = 0; c < 3; c++) rgb[c] += s[c] * w / 16;
        }
        const o = (y * dw + x) * 4;
        for (let c = 0; c < 3; c++) dest[o + c] = dest[o + c] * (1 - spread) + rgb[c] * spread;
    }
    return dest;
}
/* The whole chain over a frame (width x height): the strength-scaled bloom at
 * half resolution, mip 0 after every upsample. */
export function bloomChain(frame, width, height, options = {}) {
    const sizes = bloomSizes(width, height, options.mips ?? LIT_BLOOM.mips);
    const mips = [];
    sizes.forEach(([w, h], i) => {
        const [sw, sh] = i === 0 ? [width, height] : sizes[i - 1];
        mips.push(bloomDownsample(i === 0 ? frame : mips[i - 1], sw, sh, w, h, { ...options, first: i === 0 }));
    });
    for (let i = sizes.length - 2; i >= 0; i--) bloomUpsample(mips[i + 1], ...sizes[i + 1], mips[i], ...sizes[i], options);
    return { width: sizes[0][0], height: sizes[0][1], data: mips[0], sizes };
}
/* The bloom at a full-resolution pixel: bilinear from mip 0. */
export function bloomAt(bloom, width, height, x, y) {
    return bilinear(bloom.data, bloom.width, bloom.height, (x + 0.5) * bloom.width / width - 0.5, (y + 0.5) * bloom.height / height - 0.5);
}

/* ---- Noise and dither ----------------------------------------------------------
 * A PCG hash of (x, y, seed), identical in JS, GLSL and TSL. */
export function hash32(x, y, seed) {
    let state = (Math.imul(x >>> 0, 1973) + Math.imul(y >>> 0, 9277) + Math.imul(seed >>> 0, 26699)) >>> 0;
    state = (Math.imul(state, 747796405) + 2891336453) >>> 0;
    const word = Math.imul(((state >>> ((state >>> 28) + 4)) ^ state) >>> 0, 277803737) >>> 0;
    return ((word >>> 22) ^ word) >>> 0;
}
const unit = h => (h >>> 8) / 16777216;
// The sum of two uniforms minus one has variance 1/6; this makes it unit.
const NOISE_NORM = Math.sqrt(6);

/* ---- The display pass, CPU twin --------------------------------------------------
 * `scene` is one frame pixel, pre-exposed linear rgba (premultiplied: the
 * splats and points blend that way over a cleared frame; alpha is coverage,
 * or an emitter's tag); `bloom` the strength-scaled bloom there. Returns
 * display-encoded rgba, premultiplied, before 8-bit quantisation. `look`
 * 'camera' (the default for realism) or 'eye' (L2); the comparison operators
 * get none of bloom, gain, balance, noise or dither. */
export function litFramePixel(scene, bloom = null, { look = 'camera', operator = 'agx', gain = 1, whiteBalance = null,
    agxPower = Exposure.LIT_CAMERA_LOOK.agxPower, preExposure, strength = LIT_BLOOM.strength, noise = null, x = 0, y = 0 } = {}) {
    if (!Exposure.LIT_TONE_OPERATORS.includes(operator)) throw new RangeError('lit_tone_operator_unknown');
    const presentation = Exposure.litTonePresentation(operator);
    const alpha = scene.length > 3 ? (tagged(scene[3]) ? 1 : Math.min(1, Math.max(0, scene[3]))) : 1;
    let c = [0, 1, 2].map(i => Math.max(0, scene[i]) / Math.max(alpha, 1e-4));
    if (presentation && bloom) c = c.map((v, i) => v * (1 - strength) + Math.max(0, bloom[i]));
    let mapped;
    if (presentation && look === 'camera' && operator === 'agx') mapped = Exposure.litCameraPixel(c, { gain, whiteBalance, look: { ...Exposure.LIT_CAMERA_LOOK, agxPower } });
    else mapped = Exposure.litDisplayPixel(c, { preExposure, gain, operator });
    mapped = mapped.map(v => Math.min(1, Math.max(0, v)));
    if (presentation && noise) {
        const Y = luma(mapped), sigma = Math.sqrt(noise.shot * Y + noise.read);
        const n = (unit(hash32(x, y, noise.seed)) + unit(hash32(x, y, noise.seed + 1)) - 1) * sigma * NOISE_NORM;
        mapped = mapped.map(v => Math.min(1, Math.max(0, v + n)));
    }
    let out = mapped.map(Exposure.srgbEncode);
    if (presentation && noise) {
        const d = (unit(hash32(x, y, noise.seed + 2)) + unit(hash32(x, y, noise.seed + 3)) - 1) * noise.dither;
        out = out.map(v => v + d);
    }
    return [out[0] * alpha, out[1] * alpha, out[2] * alpha, alpha];
}

/* ---- GLSL (WebGL2, r180) ------------------------------------------------------
 * Full-screen passes drawn with a 2 x 2 plane; texelFetch only, so the CPU
 * twins above are the reference. */
const glslVec3 = v => `vec3(${v.map(x => Number(x).toPrecision(9)).join(', ')})`;
const glslMat3 = rows => `mat3(${rows.map(glslVec3).join(', ')})`;
export const LIT_PASS_VERTEX_GLSL = 'void main() { gl_Position = vec4(position.xy, 0.0, 1.0); }';
const GLSL_COMMON = `precision highp float;
precision highp int;
const vec3 LUMA = ${glslVec3(LUMA)};
vec4 fetchClamped(sampler2D t, ivec2 size, ivec2 p) { return texelFetch(t, clamp(p, ivec2(0), size - 1), 0); }
vec3 bilinear(sampler2D t, ivec2 size, vec2 p) {
  vec2 b = floor(p), f = p - b;
  ivec2 i = ivec2(b);
  vec3 a = fetchClamped(t, size, i).rgb, c = fetchClamped(t, size, i + ivec2(1, 0)).rgb;
  vec3 d = fetchClamped(t, size, i + ivec2(0, 1)).rgb, e = fetchClamped(t, size, i + ivec2(1, 1)).rgb;
  return mix(mix(a, c, f.x), mix(d, e, f.x), f.y);
}
`;
export function litBloomDownGlsl({ first = false, karis = LIT_BLOOM.karis } = {}) {
    const taps = BLOOM_TAPS.map(([ox, oy], m) => `  tap(c + ivec2(${ox}, ${oy}), p[${m}], e[${m}]);`).join('\n');
    return `${GLSL_COMMON}
uniform sampler2D uSource;
uniform float uStrength;
ivec2 uSourceSize;
void tap(ivec2 corner, out vec3 plain, out vec3 emitter) {
  plain = vec3(0.0); emitter = vec3(0.0);
  for (int k = 0; k < 4; k++) {
    vec4 t = fetchClamped(uSource, uSourceSize, corner + ivec2(k % 2 - 1, k / 2 - 1));
${first ? `    if (t.a >= ${(LIT_FRAME.emitterTag - 0.5).toFixed(1)}) emitter += t.rgb * exp2(max(0.0, t.a - ${LIT_FRAME.emitterTag.toFixed(1)})) * 0.25;
    else plain += t.rgb * 0.25;` : '    plain += t.rgb * 0.25;'}
  }
}
void main() {
  uSourceSize = textureSize(uSource, 0);
  ivec2 c = ivec2(gl_FragCoord.xy) * 2 + 1;
  vec3 p[13], e[13];
${taps}
  vec3 rgb = vec3(0.0);
${first && karis ? `  float total = 0.0;
${KARIS_GROUPS.map(([members, weight]) => `  { vec3 g = (${members.map(m => `p[${m}]`).join(' + ')}) * 0.25; float w = ${weight.toFixed(6)} / (1.0 + dot(g, LUMA)); total += w; rgb += w * g; }`).join('\n')}
  rgb /= total;` : TAP_WEIGHTS.map((w, m) => `  rgb += p[${m}] * ${w.toFixed(6)};`).join('\n')}
${first ? TAP_WEIGHTS.map((w, m) => `  rgb += e[${m}] * ${w.toFixed(6)};`).join('\n') + '\n  rgb *= uStrength;' : ''}
  gl_FragColor = vec4(rgb, 1.0);
}
`;
}
export function litBloomUpGlsl() {
    return `${GLSL_COMMON}
uniform sampler2D uSource;
uniform sampler2D uBase;
uniform vec2 uDestSize;
uniform float uSpread;
uniform float uRadius;
void main() {
  ivec2 uSourceSize = textureSize(uSource, 0);
  vec2 p = gl_FragCoord.xy * vec2(uSourceSize) / uDestSize - 0.5;
  vec3 rgb = vec3(0.0);
${TENT.map(([dx, dy, w]) => `  rgb += bilinear(uSource, uSourceSize, p + vec2(${dx}.0, ${dy}.0) * uRadius) * ${(w / 16).toFixed(6)};`).join('\n')}
  // The mix in the shader (never fixed-function blending, which rounds half
  // floats differently per GPU), into its own target.
  gl_FragColor = vec4(mix(texelFetch(uBase, ivec2(gl_FragCoord.xy), 0).rgb, rgb, uSpread), 1.0);
}
`;
}
// GLSL's PCG twin of hash32.
const GLSL_HASH = `uint hash32(uint x, uint y, uint seed) {
  uint state = x * 1973u + y * 9277u + seed * 26699u;
  state = state * 747796405u + 2891336453u;
  uint word = ((state >> ((state >> 28u) + 4u)) ^ state) * 277803737u;
  return (word >> 22u) ^ word;
}
float unitHash(uint h) { return float(h >> 8u) / 16777216.0; }`;
/* The display pass. The GLSL tone operators are the same lines as
 * lit-basis-blend.mjs's (the CPU twins in lit-exposure.mjs). */
export function litDisplayFragmentGlsl({ look = 'camera', operator = 'agx', bloom = true, noise = true } = {}) {
    if (!Exposure.LIT_TONE_OPERATORS.includes(operator)) throw new RangeError('lit_tone_operator_unknown');
    const presentation = Exposure.litTonePresentation(operator), camera = presentation && look === 'camera' && operator === 'agx';
    const X = Exposure.LIT_AGX_MATRICES, mesopic = Exposure.LIT_MESOPIC, toe = Exposure.LIT_TONE.toe;
    const withBloom = presentation && bloom, withNoise = presentation && noise;
    return `${GLSL_COMMON}
uniform sampler2D uScene;
uniform sampler2D uBloom;
uniform float uStrength;
uniform float uGain;
uniform float uPreExposure;
uniform float uAgxPower;
uniform mat3 uWhiteBalance;
uniform vec4 uNoise;
${GLSL_HASH}
vec3 agxCurve(vec3 color) {
  color = clamp((log2(max(color, 1e-10)) + 12.47393) / (4.026069 + 12.47393), 0.0, 1.0);
  vec3 x2 = color * color, x4 = x2 * x2;
  return 15.5 * x4 * x2 - 40.14 * x4 * color + 31.96 * x4 - 6.868 * x2 * color + 0.4298 * x2 + 0.1191 * color - 0.00232;
}
vec3 agxLook(vec3 color, float power) {
  color = ${glslMat3(X.inset)} * (${glslMat3(X.srgbToRec2020)} * color);
  color = pow(max(agxCurve(color), vec3(0.0)), vec3(power));
  color = pow(max(vec3(0.0), ${glslMat3(X.outset)} * color), vec3(2.2));
  return clamp(${glslMat3(X.rec2020ToSrgb)} * color, 0.0, 1.0);
}
vec3 agxEye(vec3 color) {
  color = ${glslMat3(X.outset)} * agxCurve(${glslMat3(X.inset)} * (${glslMat3(X.srgbToRec2020)} * color));
  return clamp(${glslMat3(X.rec2020ToSrgb)} * pow(max(vec3(0.0), color), vec3(2.2)), 0.0, 1.0);
}
vec3 neutral(vec3 color) {
  float x = min(color.r, min(color.g, color.b));
  float offset = x < 0.08 ? x - 6.25 * x * x : 0.04;
  color -= offset;
  float peak = max(color.r, max(color.g, color.b));
  if (peak < 0.76) return color;
  float d = 0.24, newPeak = 1.0 - d * d / (peak + d - 0.76);
  color *= newPeak / peak;
  float g = 1.0 - 1.0 / (0.15 * (peak - newPeak) + 1.0);
  return mix(color, vec3(newPeak), g);
}
vec3 srgbEncode(vec3 v) { return mix(12.92 * v, 1.055 * pow(max(v, vec3(0.0031308)), vec3(1.0 / 2.4)) - 0.055, step(vec3(0.0031308), v)); }
void main() {
  ivec2 at = ivec2(gl_FragCoord.xy);
  vec4 frame = texelFetch(uScene, at, 0);
  float alpha = frame.a >= ${(LIT_FRAME.emitterTag - 0.5).toFixed(1)} ? 1.0 : clamp(frame.a, 0.0, 1.0);
  vec3 c = max(frame.rgb, vec3(0.0)) / max(alpha, 1e-4);
${withBloom ? `  ivec2 uBloomSize = textureSize(uBloom, 0);
  vec2 q = gl_FragCoord.xy * vec2(uBloomSize) / vec2(textureSize(uScene, 0)) - 0.5;
  c = c * (1.0 - uStrength) + max(bilinear(uBloom, uBloomSize, q), vec3(0.0));` : ''}
${camera ? `  c *= uGain;
  c = max(uWhiteBalance * c, vec3(0.0));
  c = agxLook(c, uAgxPower);` : presentation ? `  float Y = dot(c, LUMA);
  float L = max(Y / uPreExposure, 1e-6);
  float x = clamp((${mesopic.photopicLog10.toFixed(6)} - log(L) / ${Math.LN10.toPrecision(9)}) / ${mesopic.spanLog10.toFixed(6)}, 0.0, 1.0);
  float s = x * x * (3.0 - 2.0 * x) * ${mesopic.strength.toFixed(6)};
  c = mix(c, ${glslVec3(mesopic.tint)} * Y, s) * uGain;
${operator === 'agx' ? `  float Ya = dot(c, LUMA);
  c *= (Ya + Ya * ${toe.lift.toFixed(6)} / (Ya / ${toe.knee.toFixed(6)} + 1.0)) / max(Ya, 1e-10);
  c = agxEye(c);` : '  c = clamp(c / (1.0 + c), 0.0, 1.0);'}` : operator === 'neutral' ? '  c = clamp(neutral(c), 0.0, 1.0);' : '  c = clamp(c, 0.0, 1.0);'}
${withNoise ? `  uint ux = uint(at.x), uy = uint(at.y), seed = uint(uNoise.z);
  float sigma = sqrt(uNoise.x * dot(c, LUMA) + uNoise.y);
  c = clamp(c + (unitHash(hash32(ux, uy, seed)) + unitHash(hash32(ux, uy, seed + 1u)) - 1.0) * sigma * ${NOISE_NORM.toPrecision(9)}, 0.0, 1.0);
  c = srgbEncode(c) + (unitHash(hash32(ux, uy, seed + 2u)) + unitHash(hash32(ux, uy, seed + 3u)) - 1.0) * uNoise.w;` : '  c = srgbEncode(c);'}
  gl_FragColor = vec4(c * alpha, alpha);
}
`;
}

/* The emitter faces (GLSL). A disc facing the light's axis; the fragment
 * computes the face's luminance toward the camera, pre-exposed, and stores
 * it clamped with the tag. */
export const LIT_EMITTER_VERTEX_GLSL = `varying vec3 vWorld;
void main() {
  vec4 world = modelMatrix * vec4(position, 1.0);
  vWorld = world.xyz;
  gl_Position = projectionMatrix * viewMatrix * world;
}`;
export const LIT_EMITTER_FRAGMENT_GLSL = `precision highp float;
uniform vec3 uRadiance;
uniform vec3 uAxis;
uniform vec4 uCone;
uniform float uClamp;
varying vec3 vWorld;
void main() {
  float cosView = dot(normalize(cameraPosition - vWorld), uAxis);
  if (cosView <= 0.0) discard;
  float t = clamp((cosView - uCone.x) / max(1e-6, uCone.y - uCone.x), 0.0, 1.0);
  float factor = uCone.w > 0.5 ? 1.0 : uCone.z + (1.0 - uCone.z) * t * t * (3.0 - 2.0 * t);
  vec3 rgb = uRadiance * factor;
  float peak = max(rgb.r, max(rgb.g, rgb.b));
  float s = peak > uClamp ? uClamp / peak : 1.0;
  gl_FragColor = vec4(rgb * s, ${LIT_FRAME.emitterTag.toFixed(1)} + log2(1.0 / s));
}`;

/* ---- TSL (WebGPU, r186) -----------------------------------------------------------
 * The same passes as nodes. Textures are read with textureLoad at the
 * fragment's integer coordinate (screenCoordinate), so the passes are exact
 * twins of the GLSL on either backend. */
const tslTaps = (T, read) => ({ fetch: (size, p) => read(T.clamp(p, T.ivec2(0), size.sub(1))) });
function tslBilinear(T, read, size, p) {
    const { floor, ivec2, mix } = T, b = floor(p), f = p.sub(b), i = ivec2(b);
    const at = q => read(T.clamp(q, ivec2(0), size.sub(1))).rgb;
    return mix(mix(at(i), at(i.add(ivec2(1, 0))), f.x), mix(at(i.add(ivec2(0, 1))), at(i.add(ivec2(1, 1))), f.x), f.y);
}
const tslSize = (T, source) => T.ivec2(T.textureSize(T.texture(source), 0));
/* `source` a THREE.Texture, `strength` a float uniform. Returns the fragment node. */
export function litBloomDownNode(T, { source, strength, first = false, karis = LIT_BLOOM.karis }) {
    const { Fn, vec3, vec4, ivec2, float, dot, exp2, max, select, screenCoordinate } = T;
    const read = p => T.textureLoad(source, p), size = tslSize(T, source);
    const { fetch: get } = tslTaps(T, read), L = vec3(...LUMA);
    return Fn(() => {
        const c = ivec2(screenCoordinate.xy).mul(2).add(1).toVar('bloomCorner');
        const plain = [], emitter = [];
        BLOOM_TAPS.forEach(([ox, oy], m) => {
            const p = vec3(0).toVar(`bloomP${m}`), e = vec3(0).toVar(`bloomE${m}`);
            for (const [dx, dy] of [[-1, -1], [0, -1], [-1, 0], [0, 0]]) {
                const t = get(size, c.add(ivec2(ox + dx, oy + dy))).toVar();
                if (first) {
                    const isEmitter = t.a.greaterThanEqual(LIT_FRAME.emitterTag - 0.5);
                    e.addAssign(select(isEmitter, t.rgb.mul(exp2(max(t.a.sub(LIT_FRAME.emitterTag), 0))).mul(0.25), vec3(0)));
                    p.addAssign(select(isEmitter, vec3(0), t.rgb.mul(0.25)));
                } else p.addAssign(t.rgb.mul(0.25));
            }
            plain.push(p); emitter.push(e);
        });
        let rgb = vec3(0).toVar('bloomRgb');
        if (first && karis) {
            const total = float(0).toVar('bloomTotal');
            for (const [members, weight] of KARIS_GROUPS) {
                const g = members.map(m => plain[m]).reduce((a, b) => a.add(b)).mul(0.25);
                const w = float(weight).div(dot(g, L).add(1));
                total.addAssign(w); rgb.addAssign(g.mul(w));
            }
            rgb.divAssign(total);
        } else plain.forEach((p, m) => rgb.addAssign(p.mul(TAP_WEIGHTS[m])));
        if (first) { emitter.forEach((e, m) => rgb.addAssign(e.mul(TAP_WEIGHTS[m]))); rgb.mulAssign(strength); }
        return vec4(rgb, 1);
    })();
}
export function litBloomUpNode(T, { source, base, destSize, spread, radius }) {
    const { Fn, vec2, vec3, vec4, ivec2, mix, screenCoordinate } = T;
    const read = p => T.textureLoad(source, p), size = tslSize(T, source);
    return Fn(() => {
        const p = screenCoordinate.xy.mul(vec2(size)).div(destSize).sub(0.5).toVar('bloomUp');
        const rgb = vec3(0).toVar('bloomUpRgb');
        for (const [dx, dy, w] of TENT) rgb.addAssign(tslBilinear(T, read, size, p.add(vec2(dx, dy).mul(radius))).mul(w / 16));
        return vec4(mix(T.textureLoad(base, ivec2(screenCoordinate.xy)).rgb, rgb, spread), 1);
    })();
}
// TSL's PCG twin of hash32 (uint arithmetic wraps as in GLSL and WGSL).
function tslHash(T, x, y, seed) {
    const { uint } = T;
    const state = x.mul(uint(1973)).add(y.mul(uint(9277))).add(seed.mul(uint(26699))).mul(uint(747796405)).add(uint(2891336453)).toVar();
    const word = state.shiftRight(state.shiftRight(uint(28)).add(uint(4))).bitXor(state).mul(uint(277803737)).toVar();
    return word.shiftRight(uint(22)).bitXor(word);
}
const tslUnit = (T, h) => T.float(h.shiftRight(T.uint(8))).div(16777216);
/* The display pass: `scene` and `bloom` textures, uniforms { strength, gain,
 * preExposure, agxPower, whiteBalance (three vec3 rows), noise (vec4: shot,
 * read, seed, dither) }. */
export function litDisplayFrameNode(T, { scene, bloom, uniforms, look = 'camera', operator = 'agx', withBloom = true, withNoise = true }) {
    if (!Exposure.LIT_TONE_OPERATORS.includes(operator)) throw new RangeError('lit_tone_operator_unknown');
    const { Fn, vec2, vec3, vec4, ivec2, uint, float, max, clamp, log2, pow, dot, mix, sqrt, screenCoordinate } = T;
    const presentation = Exposure.litTonePresentation(operator), camera = presentation && look === 'camera' && operator === 'agx';
    const X = Exposure.LIT_AGX_MATRICES, L = vec3(...LUMA), u = uniforms;
    const byColumns = (cols, v) => vec3(...[0, 1, 2].map(i => v.x.mul(cols[0][i]).add(v.y.mul(cols[1][i])).add(v.z.mul(cols[2][i]))));
    // power null: L2's eye AgX (no Punchy power, no clamp before the outset).
    const agxLook = (color, power) => {
        let c = byColumns(X.inset, byColumns(X.srgbToRec2020, color));
        c = clamp(log2(max(c, vec3(1e-10))).add(12.47393).div(4.026069 + 12.47393), 0, 1).toVar();
        const x2 = c.mul(c), x4 = x2.mul(x2);
        c = x4.mul(x2).mul(15.5).sub(x4.mul(c).mul(40.14)).add(x4.mul(31.96)).sub(x2.mul(c).mul(6.868)).add(x2.mul(0.4298)).add(c.mul(0.1191)).sub(0.00232);
        if (power !== null) c = pow(max(c, vec3(0)), vec3(power));
        c = pow(max(byColumns(X.outset, c), vec3(0)), vec3(2.2));
        return clamp(byColumns(X.rec2020ToSrgb, c), 0, 1);
    };
    return Fn(() => {
        const at = ivec2(screenCoordinate.xy).toVar('litAt');
        const frame = T.textureLoad(scene, at).toVar('litFrameRaw');
        const alpha = T.select(frame.a.greaterThanEqual(LIT_FRAME.emitterTag - 0.5), float(1), clamp(frame.a, 0, 1)).toVar('litAlpha');
        const c = max(frame.rgb, vec3(0)).div(max(alpha, 1e-4)).toVar('litFrame');
        if (presentation && withBloom) {
            const bloomSize = tslSize(T, bloom), q = screenCoordinate.xy.mul(vec2(bloomSize)).div(vec2(tslSize(T, scene))).sub(0.5);
            c.assign(c.mul(float(1).sub(u.strength)).add(max(tslBilinear(T, p => T.textureLoad(bloom, p), bloomSize, q), vec3(0))));
        }
        if (camera) {
            c.mulAssign(u.gain);
            c.assign(max(vec3(dot(u.whiteBalance[0], c), dot(u.whiteBalance[1], c), dot(u.whiteBalance[2], c)), vec3(0)));
            c.assign(agxLook(c, u.agxPower));
        } else if (presentation) {
            const m = Exposure.LIT_MESOPIC, toe = Exposure.LIT_TONE.toe;
            const Y = dot(c, L).toVar();
            const Lum = max(Y.div(u.preExposure), float(1e-6));
            const x = clamp(float(m.photopicLog10).sub(T.log(Lum).div(Math.LN10)).div(m.spanLog10), 0, 1);
            const s = x.mul(x).mul(float(3).sub(x.mul(2))).mul(m.strength);
            c.assign(mix(c, vec3(...m.tint).mul(Y), s).mul(u.gain));
            if (operator === 'agx') {
                const Ya = dot(c, L).toVar();
                c.mulAssign(Ya.add(Ya.mul(toe.lift).div(Ya.div(toe.knee).add(1))).div(max(Ya, float(1e-10))));
                c.assign(agxLook(c, null));
            } else c.assign(clamp(c.div(c.add(1)), 0, 1));
        } else if (operator === 'neutral') c.assign(clamp(T.neutralToneMapping(c, float(1)), 0, 1));
        else c.assign(clamp(c, 0, 1));
        if (presentation && withNoise) {
            const ux = uint(at.x), uy = uint(at.y), seed = uint(u.noise.z);
            const sigma = sqrt(u.noise.x.mul(dot(c, L)).add(u.noise.y));
            const n = tslUnit(T, tslHash(T, ux, uy, seed)).add(tslUnit(T, tslHash(T, ux, uy, seed.add(uint(1))))).sub(1);
            c.assign(clamp(c.add(n.mul(sigma).mul(NOISE_NORM)), 0, 1));
            const d = tslUnit(T, tslHash(T, ux, uy, seed.add(uint(2)))).add(tslUnit(T, tslHash(T, ux, uy, seed.add(uint(3))))).sub(1);
            c.assign(T.sRGBTransferOETF(c).add(d.mul(u.noise.w)));
        } else c.assign(T.sRGBTransferOETF(c));
        return vec4(c.mul(alpha), alpha);
    })();
}
/* The emitter face as a node material's fragment: `u` holds radiance (vec3),
 * axis (vec3), cone (vec4: cosOuter, cosInner, spill, lambertian), clamp. */
export function litEmitterNode(T, u) {
    const { Fn, vec4, float, max, clamp, normalize, dot, log2, select, cameraPosition, positionWorld, Discard, If } = T;
    return Fn(() => {
        const cosView = dot(normalize(cameraPosition.sub(positionWorld)), u.axis).toVar();
        If(cosView.lessThanEqual(0), () => { Discard(); });
        const t = clamp(cosView.sub(u.cone.x).div(max(u.cone.y.sub(u.cone.x), 1e-6)), 0, 1);
        const factor = select(u.cone.w.greaterThan(0.5), float(1), u.cone.z.add(float(1).sub(u.cone.z).mul(t.mul(t).mul(float(3).sub(t.mul(2))))));
        const rgb = u.radiance.mul(factor).toVar();
        const peak = max(rgb.x, max(rgb.y, rgb.z));
        const s = select(peak.greaterThan(u.clamp), u.clamp.div(peak), float(1)).toVar();
        return vec4(rgb.mul(s), log2(float(1).div(s)).add(LIT_FRAME.emitterTag));
    })();
}
