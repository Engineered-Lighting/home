/* home-3d/lit-basis-blend.mjs — L4: the runtime light-basis blend for the
 * splats and the point cloud ("Hyperreal browser room lighting" L4).
 *
 * The scan's colours are one late-afternoon daylight state. L3's additive
 * lighting layer (lit-layer-loader.mjs) gives every receiver (a splat or a
 * point, by its index in the pack's PLY) a de-lit albedo, a bounce tint and a
 * 29-column light basis: 4 downlights and 24 radial zones in lx per lumen,
 * and the capture's window daylight. Light transport is linear, so the
 * receiver's simulated light is
 *
 *   E = ambient + tint x scale x sum_k (u8_k / 255)^2 x W_k + sum_s S_s x V_s
 *   L = albedo / pi x E
 *
 *   W_k = column_scale_k x lumens x dimmed flux fraction x CCT rgb x exposure
 *         (lit-model.mjs: SPEC's assumed lumens, the assumed dimming curve,
 *         constant-luminance CCT, the design-night pre-exposure);
 *   S_s   a live spot's column for this receiver, re-baked by L5's BVH when
 *         the spot moves (direct light through a shadow ray, plus the spot's
 *         one bounce at the nearest scan vertex), at unit candela;
 *   V_s = candela x CCT rgb x exposure;
 *   the window column carries most of the assumed night ambient (it was
 *   path traced through the real openings, so it has direction, occlusion
 *   and bounce); the capture's daylight itself is never shown at night;
 *   ambient is the rest of lit-exposure.mjs's disclosed, assumed night floor;
 *   splats flagged as exterior (seen through the glass) get an assumed dark,
 *   warm night exterior instead of being relit as interior surfaces.
 *
 * L is pre-exposed and scene-referred, and that is what the blend stores
 * (half float), so splat and point colours stay linear HDR through Spark's
 * fp16 accumulator or the WebGPU colour hook. The camera look, bloom, noise and
 * the sRGB encode run once per pixel over the composited frame
 * (lit-display.mjs, lit-frame.mjs), never per receiver. The blend runs only
 * when a weight changes.

 * Three implementations of one formula: the CPU twin below (tests and the
 * adaptation meter), a GLSL GPGPU pass for the default WebGL renderer
 * (Spark's splats read it through a dyno modifier, the GL points through
 * texelFetch), and a TSL compute pass for the WebGPU renderer (the splat
 * fork's colour hook and the point sprites).
 *
 * Everything here is modeled from assumed output. Never photometry, never
 * measured.
 */
const search = new URL(import.meta.url).search;
const Model = await import(`./lit-model.mjs${search}`);
const Exposure = await import(`./lit-exposure.mjs${search}`);

// Basis slots on the GPU: 29 columns padded to 8 words of 4 bytes.
export const LIT_BASIS_SLOTS = 32;
// Live spot columns per receiver (Victoria has four robotic spots).
export const LIT_SPOT_SLOTS = 4;
// Words per receiver: meta (albedo|flags, tint, scale), basis, spot halves.
export const LIT_META_WORDS = 4, LIT_BASIS_WORDS = 8, LIT_SPOT_WORDS = LIT_SPOT_SLOTS * 2;
// Texel rows on the WebGL path; Spark's splat index maps to (i % W, i / W).
export const LIT_TEXTURE_WIDTH = 2048;
// The layer's luminance weights (tools/spatial-pipeline/lightbake/encode.py).
export const LIT_LAYER_LUMA = Object.freeze([0.2126, 0.7152, 0.0722]);
// Weights: basis slots, spot slots, ambient, (preExposure, gain, AgX power),
// the camera white balance (three rows), then the night exterior.
export const LIT_WEIGHT_VEC4S = LIT_BASIS_SLOTS + LIT_SPOT_SLOTS + 7;
const AMBIENT_SLOT = LIT_BASIS_SLOTS + LIT_SPOT_SLOTS, PARAMS_SLOT = AMBIENT_SLOT + 1, WB_SLOT = PARAMS_SLOT + 1, EXTERIOR_SLOT = WB_SLOT + 3;
// The night ambient through the window column: rgb per unit of (u8/255)^2 x scale, w = its cap.
const WINDOW_SLOT = EXTERIOR_SLOT + 1;
export const LIT_LOOKS = Object.freeze(['camera', 'eye']);
/* ASSUMED, never measured (owner photos replace them: a night photo out of
 * the window, the blinds state). */
export const LIT_NIGHT_EXTERIOR = Object.freeze({ cdM2: 0.02, kelvin: 3000, status: 'assumed', measured: false,
    source: 'Assumed night exterior seen through the glass: dim street light, about 0.02 cd/m² at 3000 K.' });
// The capture's daylight falls off from the windows far more steeply than a
// night sky does, so the window column is capped at 4x its median.
export const LIT_WINDOW_AMBIENT = Object.freeze({ share: 0.85, capRatio: 4, status: 'assumed', measured: false,
    source: 'The assumed night ambient enters through the windows: 85 % follows the baked window column (its median over the room is that share of the floor, capped at 4x near the glass), 15 % stays uniform (hallway and standby light).' });
export const LIT_RECEIVER_FLAGS = Object.freeze({ unexplained_light: 1, clipped: 2, emitter: 4, off_surface: 8,
    low_irradiance: 16, open_hemisphere: 32, neighbor_fill: 64 });
// What the relit splats and points do and do not do, stated with the view.
export const LIT_RECEIVERS_LIMIT = 'Precomputed light basis · live spots re-baked with shadows · night ambient through the windows · capture sheen dropped';
export const LIT_RECEIVERS_DISCLOSURE = 'Photo and Cloud are relit by simulation: the capture\'s daylight is removed with a modeled '
    + 'de-lit layer, then the fixtures\' assumed output is added from a precomputed light basis. Modeled, not measured; '
    + 'the capture\'s view-dependent sheen is dropped while lit. ' + LIT_WINDOW_AMBIENT.source + ' ' + LIT_NIGHT_EXTERIOR.source;

/* The point cloud stays monochrome: each receiver's de-lit albedo becomes a
 * grey of the same lightness (sRGB8), so the cloud shows the fixtures' light
 * and colour on neutral surfaces, never the capture's colours. */
export function greyAlbedo(albedo) {
    const out = new Uint8Array(albedo.length), lin = v => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
    const enc = v => (v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055);
    for (let i = 0; i < albedo.length; i += 3) {
        const y = 0.2126729 * lin(albedo[i] / 255) + 0.7151522 * lin(albedo[i + 1] / 255) + 0.0721750 * lin(albedo[i + 2] / 255);
        out[i] = out[i + 1] = out[i + 2] = Math.round(Math.min(1, Math.max(0, enc(y))) * 255);
    }
    return out;
}

/* ---- Packing ---------------------------------------------------------------
 * One receiver set from the layer's per-field arrays into GPU words:
 *   meta  x albedo sRGB r | g << 8 | b << 16 | flags << 24
 *         y bounce tint r | g << 8 | b << 16
 *         z basis scale, fp16 bits
 *   basis 29 sqrt-encoded bytes, little-endian, zero-padded to 32. */
export function packReceiverSet({ count, columns, albedo, basis, scale, tint, flags }) {
    if (!Number.isSafeInteger(count) || count <= 0 || !Number.isInteger(columns) || columns < 1 || columns > LIT_BASIS_SLOTS)
        throw new RangeError('lit_receivers_invalid');
    if (albedo.length !== count * 3 || basis.length !== count * columns || scale.length !== count || tint.length !== count * 3
        || flags.length !== count) throw new RangeError('lit_receivers_size_mismatch');
    const meta = new Uint32Array(count * LIT_META_WORDS), words = new Uint32Array(count * LIT_BASIS_WORDS);
    const bytes = new Uint8Array(words.buffer);
    for (let i = 0; i < count; i++) {
        const a = i * 3, m = i * LIT_META_WORDS;
        meta[m] = (albedo[a] | (albedo[a + 1] << 8) | (albedo[a + 2] << 16) | (flags[i] << 24)) >>> 0;
        meta[m + 1] = tint[a] | (tint[a + 1] << 8) | (tint[a + 2] << 16);
        meta[m + 2] = scale[i];
        bytes.set(basis.subarray(i * columns, (i + 1) * columns), i * LIT_BASIS_WORDS * 4);
    }
    return { count, columns, meta, basis: words, windowSlot: -1 };
}

/* ---- Weights ---------------------------------------------------------------
 * Layer columns (lighting-manifest.json columns[]) to the lights lit-view
 * eases: a 'downward' column is that fixture's Down, a 'radial' column with
 * zone n its radial zone n, 'window' is the capture's daylight (0 at night). */
export function columnKey(column) {
    if (column?.kind === 'downward') return `${column.fixture_id}:down:0`;
    if (column?.kind === 'radial') return `${column.fixture_id}:zone:${column.zone}`;
    return null;
}
export const lightKey = light => `${light.fixtureId ?? ''}:${light.kind}:${light.number ?? 0}`;

/* The weights buffer (vec4s): basis slots, spot slots, the ambient floor and
 * (preExposure, adaptation gain). `lights` are lit-model fixtureLights()
 * entries in the world (eased); `spotSlots` maps a spot's key to its slot. */
export function blendWeights({ lights = [], columns = [], columnScale = [], spotSlots = new Map(), gain = 1,
    exposure = Model.LIT_EXPOSURE, lumens = Model.LIT_ASSUMED_LUMENS, ambient = Exposure.LIT_AMBIENT_FLOOR,
    look = 'eye', windowMedian = null, exterior = LIT_NIGHT_EXTERIOR, windowAmbient = LIT_WINDOW_AMBIENT, monochrome = false } = {},
into = new Float32Array(LIT_WEIGHT_VEC4S * 4)) {
    if (!LIT_LOOKS.includes(look)) throw new RangeError('lit_look_unknown');
    into.fill(0);
    const byKey = new Map(lights.map(light => [lightKey(light), light]));
    const matched = [], unbaked = [];
    columns.slice(0, LIT_BASIS_SLOTS).forEach((column, k) => {
        const key = columnKey(column), light = key && byKey.get(key);
        if (!light) return;
        matched.push(key);
        const flux = (lumens[light.kind] || 0) * (Number.isFinite(light.fluxFraction) ? light.fluxFraction : 0);
        const scale = Number(columnScale[k]) || 0, rgb = Model.cctToLinearRgb(light.kelvin);
        for (let c = 0; c < 3; c++) into[k * 4 + c] = scale * flux * rgb[c] * exposure;
    });
    for (const light of lights) {
        const key = lightKey(light);
        if (light.kind === 'spot') {
            const slot = spotSlots.get(key);
            if (slot === undefined || slot >= LIT_SPOT_SLOTS) { unbaked.push(key); continue; }
            const rgb = Model.cctToLinearRgb(light.kelvin), candela = Number.isFinite(light.candela) ? light.candela : 0;
            for (let c = 0; c < 3; c++) into[(LIT_BASIS_SLOTS + slot) * 4 + c] = candela * rgb[c] * exposure;
        } else if (!matched.includes(key)) unbaked.push(key);
    }
    // The night ambient: mostly through the window column, scaled so its
    // median over the room is that share of the assumed floor.
    /* Monochrome (the point cloud, owner 2026-10-20): only the fixtures light
     * it, so whatever they do not reach falls to black; no ambient, no night
     * exterior, and no white balance, so each light's colour temperature reads
     * as it is. */
    if (monochrome) { ambient = { ...ambient, lux: 0 }; exterior = { ...exterior, cdM2: 0 }; }
    const floor = Model.cctToLinearRgb(ambient.kelvin), window = columns.findIndex(column => column?.kind === 'window');
    const throughWindow = window >= 0 && window < LIT_BASIS_SLOTS && windowMedian > 0 ? windowAmbient.share : 0;
    for (let c = 0; c < 3; c++) into[AMBIENT_SLOT * 4 + c] = ambient.lux * (1 - throughWindow) * floor[c] * exposure;
    if (throughWindow) {
        // Per unit of (u8/255)^2 x scale; the cap in the same unit.
        const cs = Number(columnScale[window]) || 0, perUnit = cs * throughWindow * ambient.lux / windowMedian;
        for (let c = 0; c < 3; c++) into[WINDOW_SLOT * 4 + c] = perUnit * floor[c] * exposure;
        into[WINDOW_SLOT * 4 + 3] = cs > 0 ? windowAmbient.capRatio * windowMedian / cs : 0;
    }
    const night = Model.cctToLinearRgb(exterior.kelvin);
    for (let c = 0; c < 3; c++) into[EXTERIOR_SLOT * 4 + c] = exterior.cdM2 * night[c] * exposure;
    const { whiteKelvin, matrix: balance } = cameraWhiteBalance({ lights, lumens, ambient, look, monochrome });
    balance.forEach((row, r) => { for (let c = 0; c < 3; c++) into[(WB_SLOT + r) * 4 + c] = row[c]; });
    into[PARAMS_SLOT * 4] = exposure; into[PARAMS_SLOT * 4 + 1] = gain;
    into[PARAMS_SLOT * 4 + 2] = look === 'camera' ? Exposure.LIT_CAMERA_LOOK.agxPower : 1;
    return { weights: into, matched, unbaked, whiteKelvin };
}

/* The camera's white balance (lit-display.mjs applies it per pixel): toward
 * the flux-weighted (in mireds) colour of what is lit, the ambient counted as
 * a dim source. Identity for the eye look and for the monochrome cloud. */
export function cameraWhiteBalance({ lights = [], lumens = Model.LIT_ASSUMED_LUMENS, ambient = Exposure.LIT_AMBIENT_FLOOR,
    look = 'camera', monochrome = false } = {}) {
    let weight = ambient.lux * 10, mireds = ambient.lux * 10 * 1e6 / ambient.kelvin;
    for (const light of lights) {
        const flux = (lumens[light.kind] || 0) * (Number.isFinite(light.fluxFraction) ? light.fluxFraction : 0);
        if (!(flux > 0)) continue;
        weight += flux; mireds += flux * 1e6 / (Number.isFinite(light.kelvin) ? light.kelvin : 3000);
    }
    const whiteKelvin = weight > 0 && mireds > 0 ? 1e6 / (mireds / weight) : ambient.kelvin;
    const matrix = look === 'camera' && !monochrome ? Exposure.whiteBalanceMatrix(Model.cctToLinearRgb(whiteKelvin)) : [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
    return { whiteKelvin, matrix };
}
/* The display-pass parameters the weights carry: the white balance rows and
 * the AgX power, for lit-frame.mjs. */
export function displayParams(weights) {
    return { whiteBalance: [0, 1, 2].map(r => [0, 1, 2].map(c => weights[(WB_SLOT + r) * 4 + c])), agxPower: weights[PARAMS_SLOT * 4 + 2] };
}

/* ---- Spot columns ------------------------------------------------------------
 * L5 posts each live spot's column per receiver as half rgb (Uint16 x 3); the
 * GPU keeps all spot slots per receiver as half pairs: word 2s = r | g << 16,
 * word 2s + 1 = b. */
export function writeSpotColumn(spots, count, slot, halves) {
    if (!(spots instanceof Uint32Array) || spots.length !== count * LIT_SPOT_WORDS || slot < 0 || slot >= LIT_SPOT_SLOTS)
        throw new RangeError('lit_spot_slot_invalid');
    for (let i = 0; i < count; i++) {
        const o = i * LIT_SPOT_WORDS + slot * 2;
        if (!halves) { spots[o] = 0; spots[o + 1] = 0; continue; }
        spots[o] = (halves[i * 3] | (halves[i * 3 + 1] << 16)) >>> 0;
        spots[o + 1] = halves[i * 3 + 2];
    }
    return spots;
}

/* ---- CPU twin ----------------------------------------------------------------- */
let halfTable = null;
function half(h) {
    if (!halfTable) {
        halfTable = new Float32Array(65536);
        for (let v = 0; v < 65536; v++) {
            const sign = v & 0x8000 ? -1 : 1, e = (v >>> 10) & 0x1f, m = v & 0x3ff;
            halfTable[v] = sign * (e === 0 ? m * 2 ** -24 : e === 31 ? (m ? NaN : Infinity) : (1 + m / 1024) * 2 ** (e - 15));
        }
    }
    return halfTable[h & 0xffff];
}
const srgbToLinear = c => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
export function decodeTint(word) {
    const t = [word & 255, (word >>> 8) & 255, (word >>> 16) & 255].map(v => v / 255);
    const y = LIT_LAYER_LUMA[0] * t[0] + LIT_LAYER_LUMA[1] * t[1] + LIT_LAYER_LUMA[2] * t[2];
    return y > 0 ? t.map(v => v / y) : [1, 1, 1];
}

/* Receiver i's pre-exposed, scene-referred radiance (rgb), exactly as the
 * GPU passes compute it. */
export function blendReceiver(set, i, weights, spots = null, out = [0, 0, 0]) {
    const m = i * LIT_META_WORDS, meta = set.meta;
    if ((meta[m] >>> 24) & LIT_RECEIVER_FLAGS.emitter) {
        // Seen through the glass: the assumed night exterior, keeping a
        // little of the capture's structure through its albedo.
        const word = meta[m], a = Exposure.lumaOf([word & 255, (word >>> 8) & 255, (word >>> 16) & 255].map(v => srgbToLinear(v / 255)));
        for (let c = 0; c < 3; c++) out[c] = weights[EXTERIOR_SLOT * 4 + c] * (0.5 + a);
        return out;
    }
    const tint = decodeTint(meta[m + 1]), scale = half(meta[m + 2]);
    let er = 0, eg = 0, eb = 0;
    for (let w = 0; w < LIT_BASIS_WORDS; w++) {
        const word = set.basis[i * LIT_BASIS_WORDS + w];
        for (let b = 0; b < 4; b++) {
            const u = ((word >>> (8 * b)) & 255) / 255, q = u * u, k = (w * 4 + b) * 4;
            er += q * weights[k]; eg += q * weights[k + 1]; eb += q * weights[k + 2];
        }
    }
    if (set.windowSlot >= 0) {
        const u = ((set.basis[i * LIT_BASIS_WORDS + (set.windowSlot >> 2)] >>> (8 * (set.windowSlot & 3))) & 255) / 255;
        const b = Math.min(u * u * scale, weights[WINDOW_SLOT * 4 + 3]) / Math.max(scale, 1e-30);
        er += b * weights[WINDOW_SLOT * 4]; eg += b * weights[WINDOW_SLOT * 4 + 1]; eb += b * weights[WINDOW_SLOT * 4 + 2];
    }
    er *= tint[0] * scale; eg *= tint[1] * scale; eb *= tint[2] * scale;
    if (spots) for (let s = 0; s < LIT_SPOT_SLOTS; s++) {
        const o = i * LIT_SPOT_WORDS + s * 2, k = (LIT_BASIS_SLOTS + s) * 4, rg = spots[o];
        er += half(rg) * weights[k]; eg += half(rg >>> 16) * weights[k + 1]; eb += half(spots[o + 1]) * weights[k + 2];
    }
    er += weights[AMBIENT_SLOT * 4]; eg += weights[AMBIENT_SLOT * 4 + 1]; eb += weights[AMBIENT_SLOT * 4 + 2];
    const word = meta[m], rho = [word & 255, (word >>> 8) & 255, (word >>> 16) & 255].map(v => srgbToLinear(v / 255) / Math.PI);
    out[0] = rho[0] * er; out[1] = rho[1] * eg; out[2] = rho[2] * eb;
    return out;
}
/* The same receiver through the camera look (operator 'agx', look
 * 'camera') or L2's chain: display-encoded rgb, 0..1. */
export function blendReceiverDisplay(set, i, weights, spots = null, { operator = Exposure.LIT_TONE.view, look = 'eye' } = {}) {
    const linear = blendReceiver(set, i, weights, spots);
    if (look === 'camera' && operator === 'agx') {
        const whiteBalance = [0, 1, 2].map(r => [0, 1, 2].map(c => weights[(WB_SLOT + r) * 4 + c]));
        return Exposure.litCameraPixel(linear, { gain: weights[PARAMS_SLOT * 4 + 1], whiteBalance }).map(Exposure.srgbEncode);
    }
    const mapped = Exposure.litDisplayPixel(linear, { preExposure: weights[PARAMS_SLOT * 4], gain: weights[PARAMS_SLOT * 4 + 1], operator });
    return mapped.map(Exposure.srgbEncode);
}
/* Scene luminance (cd/m², modeled) of receiver i: the adaptation meter's sample. */
export function receiverLuminance(set, i, weights, spots = null) {
    return Exposure.lumaOf(blendReceiver(set, i, weights, spots)) / weights[PARAMS_SLOT * 4];
}

/* ---- GLSL (WebGL2 GPGPU, the default renderer) ---------------------------------
 * One fragment per receiver into an RGBA16F target LIT_TEXTURE_WIDTH wide:
 * pre-exposed, scene-referred radiance, blendReceiver() line for line.
 * Integer textures hold the packed words; weights are a uniform array. */
const glslVec3 = v => `vec3(${v.map(x => Number(x).toPrecision(9)).join(', ')})`;
export function litBlendFragmentGlsl({ spots = true, windowSlot = -1 } = {}) {
    return `precision highp float;
precision highp int;
precision highp usampler2D;
uniform usampler2D uMeta;
uniform usampler2D uBasisA;
uniform usampler2D uBasisB;
${spots ? 'uniform usampler2D uSpotA;\nuniform usampler2D uSpotB;' : ''}
uniform vec4 uWeights[${LIT_WEIGHT_VEC4S}];
uniform int uCount;
const vec3 LUMA = vec3(0.2126729, 0.7151522, 0.0721750);
const vec3 LAYER_LUMA = ${glslVec3(LIT_LAYER_LUMA)};
vec4 unorm4(uint w) { return vec4(uvec4(w, w >> 8u, w >> 16u, w >> 24u) & 255u) / 255.0; }
vec3 accumulate(uvec4 words, int base) {
  vec3 e = vec3(0.0);
  for (int j = 0; j < 4; j++) {
    vec4 u = unorm4(words[j]);
    vec4 q = u * u;
    int k = base + j * 4;
    e += q.x * uWeights[k].rgb + q.y * uWeights[k + 1].rgb + q.z * uWeights[k + 2].rgb + q.w * uWeights[k + 3].rgb;
  }
  return e;
}
vec3 spotPair(uvec4 words, int slot) {
  vec2 a = unpackHalf2x16(words.x), b = unpackHalf2x16(words.z);
  return vec3(a, unpackHalf2x16(words.y).x) * uWeights[${LIT_BASIS_SLOTS} + slot].rgb
       + vec3(b, unpackHalf2x16(words.w).x) * uWeights[${LIT_BASIS_SLOTS} + slot + 1].rgb;
}
void main() {
  ivec2 at = ivec2(gl_FragCoord.xy);
  int index = at.y * ${LIT_TEXTURE_WIDTH} + at.x;
  if (index >= uCount) { gl_FragColor = vec4(0.0); return; }
  uvec4 meta = texelFetch(uMeta, at, 0);
  vec4 albedo = unorm4(meta.x);
  vec3 t = unorm4(meta.y).rgb;
  float ty = dot(t, LAYER_LUMA);
  vec3 tint = ty > 0.0 ? t / ty : vec3(1.0);
  float scale = unpackHalf2x16(meta.z).x;
  uvec4 basisA = texelFetch(uBasisA, at, 0), basisB = texelFetch(uBasisB, at, 0);
  vec3 e = accumulate(basisA, 0) + accumulate(basisB, 16);
${windowSlot >= 0 ? `  float uw = float((${windowSlot < 16 ? 'basisA' : 'basisB'}[${(windowSlot >> 2) & 3}] >> ${8 * (windowSlot & 3)}u) & 255u) / 255.0;
  e += min(uw * uw * scale, uWeights[${WINDOW_SLOT}].w) / max(scale, 1e-30) * uWeights[${WINDOW_SLOT}].rgb;` : ''}
  e *= tint * scale;
  ${spots ? 'e += spotPair(texelFetch(uSpotA, at, 0), 0) + spotPair(texelFetch(uSpotB, at, 0), 2);' : ''}
  e += uWeights[${AMBIENT_SLOT}].rgb;
  vec3 a = albedo.rgb;
  vec3 rho = mix(a / 12.92, pow((a + 0.055) / 1.055, vec3(2.4)), step(vec3(0.04045), a)) / ${Math.PI.toPrecision(9)};
  vec3 c = max(rho * e, vec3(0.0));
  if (((meta.x >> 24u) & ${LIT_RECEIVER_FLAGS.emitter}u) != 0u) {
    vec3 lin = mix(a / 12.92, pow((a + 0.055) / 1.055, vec3(2.4)), step(vec3(0.04045), a));
    c = uWeights[${EXTERIOR_SLOT}].rgb * (0.5 + dot(lin, LUMA));
  }
  gl_FragColor = vec4(c, 1.0);
}
`;
}
export const LIT_BLEND_VERTEX_GLSL = 'void main() { gl_Position = vec4(position.xy, 0.0, 1.0); }';

/* ---- TSL (WebGPU compute) -------------------------------------------------------
 * `T` is the 'three/tsl' namespace (as lit-exposure.mjs's litDisplayNode).
 * meta/basis/spots are read-only uvec4 storage nodes, weights a read-only
 * vec4 storage node, `write(index, rgb)` returns the store node; rgb is the
 * receiver's pre-exposed linear radiance, as the GLSL pass writes it. */
export function litBlendComputeNode(T, { count, meta, basis, spots = null, weights, windowSlot = -1, write, workgroup = 64 }) {
    const { Fn, If, float, vec3, instanceIndex, unpackUnorm4x8, unpackHalf2x16, dot, max, select } = T;
    const LUMA = vec3(...LIT_LAYER_LUMA), LANES = ['x', 'y', 'z', 'w'];
    return Fn(() => {
        const i = instanceIndex;
        If(i.lessThan(count), () => {
            const m = meta.element(i).toVar('litMeta');
            const t = unpackUnorm4x8(m.y).rgb.toVar('litTintRaw');
            const ty = dot(t, LUMA);
            const tint = select(ty.greaterThan(0), t.div(max(ty, float(1e-12))), vec3(1)).toVar('litTint');
            const scale = unpackHalf2x16(m.z).x;
            const e = vec3(0).toVar('litE');
            for (let half = 0; half < 2; half++) {
                const words = basis.element(i.mul(2).add(half)).toVar(`litBasis${half}`);
                for (let j = 0; j < 4; j++) {
                    const u = unpackUnorm4x8(words[LANES[j]]), q = u.mul(u), k = half * 16 + j * 4;
                    e.addAssign(weights.element(k).xyz.mul(q.x).add(weights.element(k + 1).xyz.mul(q.y))
                        .add(weights.element(k + 2).xyz.mul(q.z)).add(weights.element(k + 3).xyz.mul(q.w)));
                }
            }
            if (windowSlot >= 0) {
                const word = basis.element(i.mul(2).add(windowSlot < 16 ? 0 : 1))[LANES[(windowSlot >> 2) & 3]];
                const uw = T.float(word.shiftRight(8 * (windowSlot & 3)).bitAnd(255)).div(255);
                const w = weights.element(WINDOW_SLOT);
                e.addAssign(w.xyz.mul(T.min(uw.mul(uw).mul(scale), w.w).div(max(scale, float(1e-30)))));
            }
            e.mulAssign(tint.mul(scale));
            if (spots) for (let pair = 0; pair < 2; pair++) {
                const words = spots.element(i.mul(2).add(pair)).toVar(`litSpots${pair}`);
                for (let s = 0; s < 2; s++) {
                    const rg = unpackHalf2x16(words[LANES[s * 2]]), b = unpackHalf2x16(words[LANES[s * 2 + 1]]).x;
                    e.addAssign(vec3(rg.x, rg.y, b).mul(weights.element(LIT_BASIS_SLOTS + pair * 2 + s).xyz));
                }
            }
            e.addAssign(weights.element(AMBIENT_SLOT).xyz);
            const linearAlbedo = T.sRGBTransferEOTF(unpackUnorm4x8(m.x).rgb).toVar('litAlbedo');
            const exterior = m.x.shiftRight(24).bitAnd(LIT_RECEIVER_FLAGS.emitter).greaterThan(0);
            const radiance = select(exterior, weights.element(EXTERIOR_SLOT).xyz.mul(dot(linearAlbedo, vec3(0.2126729, 0.7151522, 0.0721750)).add(0.5)),
                linearAlbedo.div(Math.PI).mul(e)).toVar('litRadiance');
            write(i, max(radiance, vec3(0)));
        });
    })().compute(count, [workgroup]);
}
