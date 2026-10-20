/* home-3d/lit-bvh-bake.mjs — L5: one shadowed bounce traced through a BVH
 * over the scan mesh ("Hyperreal browser room lighting" L5; WebGPU renderer
 * research 3d and 5).
 *
 * Light transport is linear in each emitter's output, so the bounce is kept
 * as one layer per emitter, baked at unit candela and white, and the lightmap
 * is their sum weighted by each light's current candela and colour. A moved
 * spot re-bakes only its own layer; a slider only re-weights.
 *
 *   - Spots (they move): virtual point lights. Rays leave the spot inside its
 *     cone, land on the scan, and each landing re-emits the reflected power;
 *     every receiver gathers them through shadow rays, so bounce never passes
 *     through a wall.
 *   - Downs and radial zones (they never move): a cosine hemisphere gather
 *     per receiver, with a shadow ray from each landing to each light.
 *
 * Receivers are the scan's welded vertices (split at creases). The lightmap
 * gives every triangle its own 2 x 2 texel cell holding the plane through its
 * three corner values, so bilinear sampling reproduces per-vertex
 * interpolation exactly and nothing bleeds between triangles.
 *
 * Two tracers share one algorithm and one random sequence: WGSL compute (a
 * stack BVH walk ported from the research spike, ordered children,
 * Moller-Trumbore) and its CPU twin for tests, the WebGL2-only fallback and
 * the lux readout. Both run in a worker (lit-bvh-worker.mjs); the BVH is a
 * binned SAH build, also in the worker.
 *
 * Everything here is modeled: assumed lumens, assumed albedos from the scan
 * colours, one bounce. Never photometry, never measured.
 *
 * No three.js, no DOM: lit-view.mjs does the scene glue, so this module runs
 * unchanged in a worker, in Node tests and on either renderer.
 */

export const LIT_BAKE_METHOD = 'compute-bvh-one-bounce';
// Per light: pos.xyz, candela, axis.xyz, cosOuter, colour.rgb, cosInner, 4 spare.
export const LIGHT_FLOATS = 16;
export const MAX_LAYER_LIGHTS = 32;
export const BVH_LEAF_SIZE = 4;
export const BVH_BINS = 16;
// Traversal stack in WGSL; the build falls back to median splits deep in the
// tree so no path is longer than this.
export const BVH_STACK = 64;
const MEDIAN_DEPTH = 40;
// Traversal steps per ray, in both tracers: a degenerate ray can never walk
// the whole tree and stall the GPU watchdog.
export const BVH_MAX_STEPS = 4096;
// Reciprocal direction components are clamped away from zero (no inf x 0).
const INV_EPS = 1e-12;
const safeInv = v => 1 / (Math.abs(v) < INV_EPS ? (v < 0 ? -INV_EPS : INV_EPS) : v);
export const LIT_BAKE_RECEIVER_OFFSET_M = 0.003;
// Virtual point lights: the geometry term is clamped to this distance, the
// usual bias that keeps a nearby landing from making a hot spot.
export const LIT_VPL_MIN_DISTANCE_M = 0.15;
export const LIT_BAKE_RANGE_M = 40;
// Welded vertices split where face normals differ by more than 60 degrees.
const CREASE_COS = 0.5;
const WELD_STEP_M = 2e-4;

/* Sample budgets. `full` is the desktop GPU; `progressive` spreads the same
 * spot re-bake over passes (iPhone-class GPUs); `cpu` is the CPU twin when no
 * WebGPU adapter exists. Lux always uses the CPU twin with `lux`. */
export const LIT_BAKE_QUALITY = Object.freeze({
    // GPU work is sliced so no single dispatch holds the GPU much past a frame.
    full: Object.freeze({ name: 'full', vpl: 512, vplPasses: 1, vplSlice: 128, sky: 48, skyChunk: 16384, chunkMs: 6 }),
    progressive: Object.freeze({ name: 'progressive', vpl: 128, vplPasses: 4, vplSlice: 32, sky: 24, skyChunk: 8192, chunkMs: 4 }),
    cpu: Object.freeze({ name: 'cpu', vpl: 128, vplPasses: 1, sky: 8, chunkMs: 12 }),
    lux: Object.freeze({ name: 'lux', vpl: 2048, sky: 512, paths: 1024, depth: 5 }),
});

/* ---- Random sequence (identical in WGSL) ----------------------------------- */
export function hashSeed(a, b, c) {
    let h = (Math.imul(a >>> 0, 9781) + Math.imul(b >>> 0, 6271) + Math.imul(c >>> 0, 26699)) >>> 0;
    h = Math.imul(h ^ (h >>> 16), 0x7feb352d) >>> 0;
    h = Math.imul(h ^ (h >>> 15), 0x846ca68b) >>> 0;
    return (h ^ (h >>> 16)) >>> 0;
}
export function createRandom(seed) {
    let state = seed >>> 0;
    return () => {
        state = (Math.imul(state, 747796405) + 2891336453) >>> 0;
        let w = Math.imul(((state >>> ((state >>> 28) + 4)) ^ state) >>> 0, 277803737) >>> 0;
        w = ((w >>> 22) ^ w) >>> 0;
        return w / 4294967296;
    };
}

/* three.js spot falloff: smoothstep(cos(angle), cos(angle x (1 - penumbra)), cos). */
export function coneFalloff(cosOuter, cosInner, cosine) {
    if (!(cosInner > cosOuter)) return cosine >= cosOuter ? 1 : 0;
    const t = Math.min(1, Math.max(0, (cosine - cosOuter) / (cosInner - cosOuter)));
    return t * t * (3 - 2 * t);
}

/* Pack lights for the tracers. Each light: { origin: [x,y,z], direction:
 * [x,y,z], cosOuter, cosInner, candela = 1, color = [1,1,1] }. */
export function packLights(lights) {
    const list = Array.from(lights || []).slice(0, MAX_LAYER_LIGHTS), data = new Float32Array(Math.max(1, list.length) * LIGHT_FLOATS);
    list.forEach((light, i) => {
        const o = i * LIGHT_FLOATS, d = light.direction, length = Math.hypot(d[0], d[1], d[2]) || 1, color = light.color || [1, 1, 1];
        data.set([light.origin[0], light.origin[1], light.origin[2], Number.isFinite(light.candela) ? light.candela : 1,
            d[0] / length, d[1] / length, d[2] / length, light.cosOuter, color[0], color[1], color[2], light.cosInner, 0, 0, 0, 0], o);
    });
    return { data, count: list.length };
}

/* ---- Half floats ---------------------------------------------------------- */
const F32 = new Float32Array(1), U32 = new Uint32Array(F32.buffer);
export function toHalf(value) {
    F32[0] = value;
    const x = U32[0], sign = (x >>> 16) & 0x8000, exponent = (x >>> 23) & 0xff;
    let mantissa = x & 0x7fffff;
    if (exponent === 0xff) return sign | 0x7c00 | (mantissa ? 0x200 : 0);
    const e = exponent - 112;
    if (e >= 31) return sign | 0x7bff;
    if (e <= 0) {
        if (e < -10) return sign;
        mantissa = (mantissa | 0x800000) >>> (1 - e);
        return sign | ((mantissa + 0x1000) >>> 13);
    }
    return (sign | (e << 10) | (mantissa >>> 13)) + ((mantissa >>> 12) & 1);
}
let halfTable = null;
export function halfTableF32() {
    if (halfTable) return halfTable;
    halfTable = new Float32Array(65536);
    for (let h = 0; h < 65536; h++) {
        const sign = h & 0x8000 ? -1 : 1, e = (h >>> 10) & 0x1f, m = h & 0x3ff;
        halfTable[h] = sign * (e === 0 ? m * 2 ** -24 : e === 31 ? (m ? NaN : Infinity) : (1 + m / 1024) * 2 ** (e - 15));
    }
    return halfTable;
}

/* ---- Scene: receivers and BVH --------------------------------------------- */

/* Welded receivers for world-space triangles (9 floats each). Returns
 * receivers (pos.xyz, 0, normal.xyz, 0 per receiver), corners (receiver per
 * triangle corner) and face normals. */
export function buildReceivers(triangles) {
    const triCount = Math.floor(triangles.length / 9), corners = new Uint32Array(triCount * 3);
    const faceNormals = new Float32Array(triCount * 3);
    let sums = new Float32Array(Math.max(16, triCount * 2) * 3), positions = new Float32Array(sums.length);
    let count = 0;
    const byKey = new Map(), q = value => Math.round(value / WELD_STEP_M) + 65536;
    for (let t = 0; t < triCount; t++) {
        const o = t * 9;
        const ax = triangles[o], ay = triangles[o + 1], az = triangles[o + 2];
        const e1x = triangles[o + 3] - ax, e1y = triangles[o + 4] - ay, e1z = triangles[o + 5] - az;
        const e2x = triangles[o + 6] - ax, e2y = triangles[o + 7] - ay, e2z = triangles[o + 8] - az;
        // Area-weighted face normal (twice the area).
        const nx = e1y * e2z - e1z * e2y, ny = e1z * e2x - e1x * e2z, nz = e1x * e2y - e1y * e2x, length = Math.hypot(nx, ny, nz);
        if (length > 0) { faceNormals[t * 3] = nx / length; faceNormals[t * 3 + 1] = ny / length; faceNormals[t * 3 + 2] = nz / length; }
        for (let k = 0; k < 3; k++) {
            const x = triangles[o + k * 3], y = triangles[o + k * 3 + 1], z = triangles[o + k * 3 + 2];
            const key = (q(x) * 131072 + q(y)) * 131072 + q(z);
            let list = byKey.get(key), found = -1;
            if (list) for (const index of list) {
                const sx = sums[index * 3], sy = sums[index * 3 + 1], sz = sums[index * 3 + 2], sl = Math.hypot(sx, sy, sz);
                if (length === 0 || sl === 0 || (sx * nx + sy * ny + sz * nz) / (sl * length) > CREASE_COS) { found = index; break; }
            }
            if (found < 0) {
                if (count * 3 >= sums.length) {
                    const grownSums = new Float32Array(sums.length * 2), grownPositions = new Float32Array(sums.length * 2);
                    grownSums.set(sums); grownPositions.set(positions); sums = grownSums; positions = grownPositions;
                }
                found = count++;
                positions[found * 3] = x; positions[found * 3 + 1] = y; positions[found * 3 + 2] = z;
                if (list) list.push(found); else byKey.set(key, [found]);
            }
            sums[found * 3] += nx; sums[found * 3 + 1] += ny; sums[found * 3 + 2] += nz;
            corners[t * 3 + k] = found;
        }
    }
    const receivers = new Float32Array(count * 8);
    for (let r = 0; r < count; r++) {
        const sx = sums[r * 3], sy = sums[r * 3 + 1], sz = sums[r * 3 + 2], sl = Math.hypot(sx, sy, sz);
        receivers.set([positions[r * 3], positions[r * 3 + 1], positions[r * 3 + 2], 0,
            sl > 0 ? sx / sl : 0, sl > 0 ? sy / sl : 0, sl > 0 ? sz / sl : 1, 0], r * 8);
    }
    return { receivers, corners, faceNormals, receiverCount: count, triangleCount: triCount };
}

/* Binned SAH BVH (16 bins, leaves of 4 or fewer) over world-space triangles.
 * nodes: 32 B each (min.xyz f32, first-or-left u32, max.xyz f32, count u32);
 * a leaf has count > 0, an inner node's right child is left + 1.
 * tris: 16 floats each (v0, edge1, edge2, albedo), in BVH order. */
export function buildBvh(triangles, albedo = null) {
    const n = Math.floor(triangles.length / 9);
    const centroid = new Float32Array(3 * n), low = new Float32Array(3 * n), high = new Float32Array(3 * n);
    for (let i = 0; i < n; i++) for (let k = 0; k < 3; k++) {
        const a = triangles[i * 9 + k], b = triangles[i * 9 + 3 + k], c = triangles[i * 9 + 6 + k];
        low[3 * i + k] = Math.min(a, b, c); high[3 * i + k] = Math.max(a, b, c); centroid[3 * i + k] = (a + b + c) / 3;
    }
    const order = new Uint32Array(n);
    for (let i = 0; i < n; i++) order[i] = i;
    let capacity = Math.max(1, 2 * Math.ceil(n / 1) + 1), nodeF = new Float32Array(capacity * 8), nodeU = new Uint32Array(nodeF.buffer);
    let nodeCount = 1, depthMax = 0;
    const binCount = new Uint32Array(BVH_BINS), binLow = new Float32Array(BVH_BINS * 3), binHigh = new Float32Array(BVH_BINS * 3);
    const leftArea = new Float64Array(BVH_BINS), leftCount = new Uint32Array(BVH_BINS);
    const stack = [[0, n, 0, 0]];
    while (stack.length) {
        const [start, end, index, depth] = stack.pop();
        depthMax = Math.max(depthMax, depth);
        let mnx = Infinity, mny = Infinity, mnz = Infinity, mxx = -Infinity, mxy = -Infinity, mxz = -Infinity;
        let cnx = Infinity, cny = Infinity, cnz = Infinity, cxx = -Infinity, cxy = -Infinity, cxz = -Infinity;
        for (let i = start; i < end; i++) {
            const t = order[i] * 3;
            mnx = Math.min(mnx, low[t]); mny = Math.min(mny, low[t + 1]); mnz = Math.min(mnz, low[t + 2]);
            mxx = Math.max(mxx, high[t]); mxy = Math.max(mxy, high[t + 1]); mxz = Math.max(mxz, high[t + 2]);
            cnx = Math.min(cnx, centroid[t]); cny = Math.min(cny, centroid[t + 1]); cnz = Math.min(cnz, centroid[t + 2]);
            cxx = Math.max(cxx, centroid[t]); cxy = Math.max(cxy, centroid[t + 1]); cxz = Math.max(cxz, centroid[t + 2]);
        }
        const o = index * 8;
        if (n === 0) { mnx = mny = mnz = mxx = mxy = mxz = 0; }
        nodeF[o] = mnx; nodeF[o + 1] = mny; nodeF[o + 2] = mnz; nodeF[o + 4] = mxx; nodeF[o + 5] = mxy; nodeF[o + 6] = mxz;
        const count = end - start;
        if (count <= BVH_LEAF_SIZE) { nodeU[o + 3] = start; nodeU[o + 7] = count; continue; }
        const cmin = [cnx, cny, cnz], cmax = [cxx, cxy, cxz];
        let best = Infinity, bestAxis = -1, bestSplit = 0;
        if (depth < MEDIAN_DEPTH) for (let axis = 0; axis < 3; axis++) {
            const extent = cmax[axis] - cmin[axis];
            if (!(extent > 1e-9)) continue;
            binCount.fill(0); binLow.fill(Infinity); binHigh.fill(-Infinity);
            for (let i = start; i < end; i++) {
                const t = order[i] * 3, b = Math.min(BVH_BINS - 1, ((centroid[t + axis] - cmin[axis]) / extent * BVH_BINS) | 0);
                binCount[b]++;
                for (let k = 0; k < 3; k++) {
                    if (low[t + k] < binLow[b * 3 + k]) binLow[b * 3 + k] = low[t + k];
                    if (high[t + k] > binHigh[b * 3 + k]) binHigh[b * 3 + k] = high[t + k];
                }
            }
            let ax0 = Infinity, ay0 = Infinity, az0 = Infinity, ax1 = -Infinity, ay1 = -Infinity, az1 = -Infinity, c = 0;
            for (let b = 0; b < BVH_BINS - 1; b++) {
                c += binCount[b];
                if (binCount[b]) {
                    ax0 = Math.min(ax0, binLow[b * 3]); ay0 = Math.min(ay0, binLow[b * 3 + 1]); az0 = Math.min(az0, binLow[b * 3 + 2]);
                    ax1 = Math.max(ax1, binHigh[b * 3]); ay1 = Math.max(ay1, binHigh[b * 3 + 1]); az1 = Math.max(az1, binHigh[b * 3 + 2]);
                }
                leftCount[b] = c;
                leftArea[b] = c ? (ax1 - ax0) * (ay1 - ay0) + (ay1 - ay0) * (az1 - az0) + (az1 - az0) * (ax1 - ax0) : 0;
            }
            ax0 = ay0 = az0 = Infinity; ax1 = ay1 = az1 = -Infinity; c = 0;
            for (let b = BVH_BINS - 1; b > 0; b--) {
                c += binCount[b];
                if (binCount[b]) {
                    ax0 = Math.min(ax0, binLow[b * 3]); ay0 = Math.min(ay0, binLow[b * 3 + 1]); az0 = Math.min(az0, binLow[b * 3 + 2]);
                    ax1 = Math.max(ax1, binHigh[b * 3]); ay1 = Math.max(ay1, binHigh[b * 3 + 1]); az1 = Math.max(az1, binHigh[b * 3 + 2]);
                }
                const rightArea = c ? (ax1 - ax0) * (ay1 - ay0) + (ay1 - ay0) * (az1 - az0) + (az1 - az0) * (ax1 - ax0) : 0;
                const cost = leftCount[b - 1] * leftArea[b - 1] + c * rightArea;
                if (leftCount[b - 1] && c && cost < best) { best = cost; bestAxis = axis; bestSplit = b; }
            }
        }
        let mid;
        if (bestAxis < 0) mid = (start + end) >> 1;
        else {
            const extent = cmax[bestAxis] - cmin[bestAxis];
            let i = start, j = end - 1;
            while (i <= j) {
                const t = order[i], b = Math.min(BVH_BINS - 1, ((centroid[t * 3 + bestAxis] - cmin[bestAxis]) / extent * BVH_BINS) | 0);
                if (b < bestSplit) i++; else { order[i] = order[j]; order[j] = t; j--; }
            }
            mid = i;
            if (mid === start || mid === end) mid = (start + end) >> 1;
        }
        if (nodeCount + 2 > capacity) {
            capacity *= 2;
            const grown = new Float32Array(capacity * 8); grown.set(nodeF); nodeF = grown; nodeU = new Uint32Array(nodeF.buffer);
        }
        const left = nodeCount; nodeCount += 2;
        nodeU[o + 3] = left; nodeU[o + 7] = 0;
        stack.push([start, mid, left, depth + 1], [mid, end, left + 1, depth + 1]);
    }
    const tris = new Float32Array(16 * Math.max(1, n));
    for (let i = 0; i < n; i++) {
        const t = order[i], s = t * 9, d = i * 16;
        tris[d] = triangles[s]; tris[d + 1] = triangles[s + 1]; tris[d + 2] = triangles[s + 2];
        tris[d + 4] = triangles[s + 3] - triangles[s]; tris[d + 5] = triangles[s + 4] - triangles[s + 1]; tris[d + 6] = triangles[s + 5] - triangles[s + 2];
        tris[d + 8] = triangles[s + 6] - triangles[s]; tris[d + 9] = triangles[s + 7] - triangles[s + 1]; tris[d + 10] = triangles[s + 8] - triangles[s + 2];
        tris[d + 12] = albedo ? albedo[t * 3] : 0.5; tris[d + 13] = albedo ? albedo[t * 3 + 1] : 0.5; tris[d + 14] = albedo ? albedo[t * 3 + 2] : 0.5;
    }
    const nodes = nodeF.slice(0, nodeCount * 8);
    return { nodes: nodes.buffer, nodeF: nodes, nodeU: new Uint32Array(nodes.buffer), nodeCount, tris, order, triangleCount: n, depth: depthMax };
}

/* ---- CPU tracer (the WGSL twin) ------------------------------------------- */
const NO_HIT = 0xffffffff;
function slab(ox, oy, oz, ix, iy, iz, nodeF, o, tmax) {
    const t0x = (nodeF[o] - ox) * ix, t1x = (nodeF[o + 4] - ox) * ix;
    const t0y = (nodeF[o + 1] - oy) * iy, t1y = (nodeF[o + 5] - oy) * iy;
    const t0z = (nodeF[o + 2] - oz) * iz, t1z = (nodeF[o + 6] - oz) * iz;
    const tn = Math.max(Math.min(t0x, t1x), Math.min(t0y, t1y), Math.min(t0z, t1z));
    const tf = Math.min(Math.max(t0x, t1x), Math.max(t0y, t1y), Math.max(t0z, t1z));
    return tf >= Math.max(tn, 0) && tn < tmax ? Math.max(tn, 0) : 1e30;
}
const traceStack = new Uint32Array(BVH_STACK);
/* Closest (anyHit false) or any (true) hit along a unit ray; returns
 * { t, tri } with tri NO_HIT when nothing is hit before tmax. */
export function trace(bvh, ox, oy, oz, dx, dy, dz, tmax, anyHit = false, out = { t: 0, tri: NO_HIT }) {
    const { nodeF, nodeU, tris } = bvh;
    let bestT = tmax, bestTri = NO_HIT, sp = 0, ni = 0;
    const ix = safeInv(dx), iy = safeInv(dy), iz = safeInv(dz);
    if (!bvh.triangleCount) { out.t = tmax; out.tri = NO_HIT; return out; }
    for (let steps = 0; steps < BVH_MAX_STEPS; steps++) {
        const o = ni * 8, count = nodeU[o + 7];
        if (count > 0) {
            const first = nodeU[o + 3];
            for (let i = first; i < first + count; i++) {
                const b = i * 16;
                const e1x = tris[b + 4], e1y = tris[b + 5], e1z = tris[b + 6], e2x = tris[b + 8], e2y = tris[b + 9], e2z = tris[b + 10];
                const px = dy * e2z - dz * e2y, py = dz * e2x - dx * e2z, pz = dx * e2y - dy * e2x;
                const det = e1x * px + e1y * py + e1z * pz;
                if (Math.abs(det) <= 1e-9) continue;
                const inv = 1 / det, sx = ox - tris[b], sy = oy - tris[b + 1], sz = oz - tris[b + 2];
                const u = (sx * px + sy * py + sz * pz) * inv;
                if (u < 0 || u > 1) continue;
                const qx = sy * e1z - sz * e1y, qy = sz * e1x - sx * e1z, qz = sx * e1y - sy * e1x;
                const v = (dx * qx + dy * qy + dz * qz) * inv;
                if (v < 0 || u + v > 1) continue;
                const t = (e2x * qx + e2y * qy + e2z * qz) * inv;
                if (t > 1e-4 && t < bestT) { bestT = t; bestTri = i; if (anyHit) { out.t = t; out.tri = i; return out; } }
            }
            if (sp === 0) break;
            ni = traceStack[--sp]; continue;
        }
        const left = nodeU[o + 3], right = left + 1;
        const dl = slab(ox, oy, oz, ix, iy, iz, nodeF, left * 8, bestT), dr = slab(ox, oy, oz, ix, iy, iz, nodeF, right * 8, bestT);
        if (dl > 1e29 && dr > 1e29) { if (sp === 0) break; ni = traceStack[--sp]; continue; }
        if (dl <= dr) { ni = left; if (dr < 1e29) traceStack[sp++] = right; }
        else { ni = right; if (dl < 1e29) traceStack[sp++] = left; }
        if (sp >= BVH_STACK) break;
        if (steps === BVH_MAX_STEPS - 1 && anyHit) { out.t = 0; out.tri = 0; return out; }
    }
    out.t = bestT; out.tri = bestTri;
    return out;
}

function triNormal(tris, tri, into) {
    const b = tri * 16, e1x = tris[b + 4], e1y = tris[b + 5], e1z = tris[b + 6], e2x = tris[b + 8], e2y = tris[b + 9], e2z = tris[b + 10];
    const nx = e1y * e2z - e1z * e2y, ny = e1z * e2x - e1x * e2z, nz = e1x * e2y - e1y * e2x, l = Math.hypot(nx, ny, nz) || 1;
    into[0] = nx / l; into[1] = ny / l; into[2] = nz / l;
    return into;
}
// Tangent frame used by both tracers (as in the spike).
function frame(nx, ny, nz, into) {
    let tx = 1, ty = 0, tz = 0;
    if (Math.abs(nx) > 0.9) { tx = 0; ty = 1; }
    let bx = ny * tz - nz * ty, by = nz * tx - nx * tz, bz = nx * ty - ny * tx;
    const bl = Math.hypot(bx, by, bz) || 1; bx /= bl; by /= bl; bz /= bl;
    into[0] = by * nz - bz * ny; into[1] = bz * nx - bx * nz; into[2] = bx * ny - by * nx;
    into[3] = bx; into[4] = by; into[5] = bz;
    return into;
}
const scratchFrame = new Float64Array(6), scratchNormal = new Float64Array(3), scratchHit = { t: 0, tri: NO_HIT };

/* Unshadowed irradiance from packed light l at p with normal n (rgb into
 * `out`, returns the distance or 0). */
function lightTerm(lights, l, px, py, pz, nx, ny, nz, out) {
    const o = l * LIGHT_FLOATS, tx = lights[o] - px, ty = lights[o + 1] - py, tz = lights[o + 2] - pz;
    const d2 = tx * tx + ty * ty + tz * tz, d = Math.sqrt(d2);
    if (d < 1e-5) return 0;
    const wx = tx / d, wy = ty / d, wz = tz / d, ndl = nx * wx + ny * wy + nz * wz;
    if (ndl <= 0) return 0;
    const cone = coneFalloff(lights[o + 7], lights[o + 11], -(wx * lights[o + 4] + wy * lights[o + 5] + wz * lights[o + 6]));
    if (cone <= 0) return 0;
    const scale = lights[o + 3] * cone * ndl / Math.max(d2, 0.01);
    out[0] = lights[o + 8] * scale; out[1] = lights[o + 9] * scale; out[2] = lights[o + 10] * scale;
    return d;
}
function visibleTo(bvh, px, py, pz, nx, ny, nz, qx, qy, qz, eps) {
    const ox = px + nx * eps, oy = py + ny * eps, oz = pz + nz * eps;
    const tx = qx - ox, ty = qy - oy, tz = qz - oz, d = Math.hypot(tx, ty, tz);
    if (d <= eps * 2) return true;
    return trace(bvh, ox, oy, oz, tx / d, ty / d, tz / d, d - eps, true, scratchHit).tri === NO_HIT;
}

/* Virtual point lights for one spot (packed light 0), as the vplEmit kernel:
 * Float32Array of 12 floats each (pos.xyz, valid, normal.xyz, 0, power.rgb, 0). */
export function emitVplsCpu(bvh, lights, { count, pass = 0, seed = 1, eps = LIT_BAKE_RECEIVER_OFFSET_M, range = LIT_BAKE_RANGE_M } = {}) {
    const out = new Float32Array(count * 12), f = scratchFrame, n = scratchNormal, hit = { t: 0, tri: NO_HIT };
    const px = lights[0], py = lights[1], pz = lights[2], ax = lights[4], ay = lights[5], az = lights[6], cosO = lights[7], cosI = lights[11];
    frame(ax, ay, az, f);
    const omega = 2 * Math.PI * (1 - cosO);
    for (let i = 0; i < count; i++) {
        const rnd = createRandom(hashSeed(i, pass, seed ^ 0x9e3779b9));
        const c = 1 - rnd() * (1 - cosO), phi = 2 * Math.PI * rnd(), s = Math.sqrt(Math.max(0, 1 - c * c));
        let dx = f[0] * Math.cos(phi) * s + f[3] * Math.sin(phi) * s + ax * c;
        let dy = f[1] * Math.cos(phi) * s + f[4] * Math.sin(phi) * s + ay * c;
        let dz = f[2] * Math.cos(phi) * s + f[5] * Math.sin(phi) * s + az * c;
        const dl = Math.hypot(dx, dy, dz); dx /= dl; dy /= dl; dz /= dl;
        const cone = coneFalloff(cosO, cosI, c);
        if (cone <= 0) continue;
        trace(bvh, px, py, pz, dx, dy, dz, range, false, hit);
        if (hit.tri === NO_HIT) continue;
        triNormal(bvh.tris, hit.tri, n);
        if (n[0] * dx + n[1] * dy + n[2] * dz > 0) { n[0] = -n[0]; n[1] = -n[1]; n[2] = -n[2]; }
        const b = hit.tri * 16, power = lights[3] * cone * omega / count, o = i * 12;
        out[o] = px + dx * hit.t + n[0] * eps; out[o + 1] = py + dy * hit.t + n[1] * eps; out[o + 2] = pz + dz * hit.t + n[2] * eps; out[o + 3] = 1;
        out[o + 4] = n[0]; out[o + 5] = n[1]; out[o + 6] = n[2];
        out[o + 8] = bvh.tris[b + 12] * lights[8] * power; out[o + 9] = bvh.tris[b + 13] * lights[9] * power; out[o + 10] = bvh.tris[b + 14] * lights[10] * power;
    }
    return out;
}
/* Irradiance at one point from VPLs, through shadow rays (vplGather). */
export function gatherVplsCpu(bvh, vpls, px, py, pz, nx, ny, nz, { eps = LIT_BAKE_RECEIVER_OFFSET_M, minDistance = LIT_VPL_MIN_DISTANCE_M } = {}, out = [0, 0, 0]) {
    const x = px + nx * eps, y = py + ny * eps, z = pz + nz * eps, min2 = minDistance * minDistance;
    let er = 0, eg = 0, eb = 0;
    for (let o = 0; o < vpls.length; o += 12) {
        if (vpls[o + 3] < 0.5) continue;
        const vx = vpls[o] - x, vy = vpls[o + 1] - y, vz = vpls[o + 2] - z, r2 = vx * vx + vy * vy + vz * vz, d = Math.sqrt(r2);
        if (d < 1e-4) continue;
        const wx = vx / d, wy = vy / d, wz = vz / d, cx = nx * wx + ny * wy + nz * wz, cy = -(vpls[o + 4] * wx + vpls[o + 5] * wy + vpls[o + 6] * wz);
        if (cx <= 0 || cy <= 0) continue;
        if (trace(bvh, x, y, z, wx, wy, wz, d - eps, true, scratchHit).tri !== NO_HIT) continue;
        const g = cx * cy / (Math.PI * Math.max(r2, min2));
        er += vpls[o + 8] * g; eg += vpls[o + 9] * g; eb += vpls[o + 10] * g;
    }
    out[0] = er; out[1] = eg; out[2] = eb;
    return out;
}
/* Per-light bounce irradiance at one point by cosine hemisphere gather
 * (skyGather): out[l * 3 + c] for each packed light. */
export function gatherSkyCpu(bvh, lights, lightCount, px, py, pz, nx, ny, nz, { samples, seed = 1, pass = 0, receiver = 0,
    eps = LIT_BAKE_RECEIVER_OFFSET_M, range = LIT_BAKE_RANGE_M } = {}, out = new Float64Array(lightCount * 3)) {
    out.fill(0);
    const f = frame(nx, ny, nz, new Float64Array(6)), n = new Float64Array(3), hit = { t: 0, tri: NO_HIT }, term = [0, 0, 0];
    const x = px + nx * eps, y = py + ny * eps, z = pz + nz * eps;
    const rnd = createRandom(hashSeed(receiver, pass, seed));
    for (let s = 0; s < samples; s++) {
        const r1 = rnd(), r2 = rnd(), phi = 2 * Math.PI * r1, sr = Math.sqrt(r2), cz = Math.sqrt(1 - r2);
        let dx = f[0] * Math.cos(phi) * sr + f[3] * Math.sin(phi) * sr + nx * cz;
        let dy = f[1] * Math.cos(phi) * sr + f[4] * Math.sin(phi) * sr + ny * cz;
        let dz = f[2] * Math.cos(phi) * sr + f[5] * Math.sin(phi) * sr + nz * cz;
        const dl = Math.hypot(dx, dy, dz); dx /= dl; dy /= dl; dz /= dl;
        trace(bvh, x, y, z, dx, dy, dz, range, false, hit);
        if (hit.tri === NO_HIT) continue;
        triNormal(bvh.tris, hit.tri, n);
        if (n[0] * dx + n[1] * dy + n[2] * dz > 0) { n[0] = -n[0]; n[1] = -n[1]; n[2] = -n[2]; }
        const hx = x + dx * hit.t, hy = y + dy * hit.t, hz = z + dz * hit.t, b = hit.tri * 16;
        for (let l = 0; l < lightCount; l++) {
            if (!lightTerm(lights, l, hx, hy, hz, n[0], n[1], n[2], term)) continue;
            const o = l * LIGHT_FLOATS;
            if (!visibleTo(bvh, hx, hy, hz, n[0], n[1], n[2], lights[o], lights[o + 1], lights[o + 2], eps)) continue;
            out[l * 3] += bvh.tris[b + 12] * term[0]; out[l * 3 + 1] += bvh.tris[b + 13] * term[1]; out[l * 3 + 2] += bvh.tris[b + 14] * term[2];
        }
    }
    for (let i = 0; i < out.length; i++) out[i] /= Math.max(1, samples);
    return out;
}
/* Direct irradiance from every packed light, shadowed through the BVH. */
export function directCpu(bvh, lights, lightCount, px, py, pz, nx, ny, nz, { eps = LIT_BAKE_RECEIVER_OFFSET_M } = {}, out = [0, 0, 0]) {
    const term = [0, 0, 0];
    out[0] = out[1] = out[2] = 0;
    for (let l = 0; l < lightCount; l++) {
        if (!lightTerm(lights, l, px, py, pz, nx, ny, nz, term)) continue;
        const o = l * LIGHT_FLOATS;
        if (!visibleTo(bvh, px, py, pz, nx, ny, nz, lights[o], lights[o + 1], lights[o + 2], eps)) continue;
        out[0] += term[0]; out[1] += term[1]; out[2] += term[2];
    }
    return out;
}
/* Irradiance from the second bounce on (paths from x, next-event estimation
 * to every light at each landing after the first). The lightmap carries one
 * bounce; the lux readout adds this. */
export function higherBouncesCpu(bvh, lights, lightCount, px, py, pz, nx, ny, nz, { paths = 1024, depth = 5, seed = 13,
    eps = LIT_BAKE_RECEIVER_OFFSET_M, range = LIT_BAKE_RANGE_M } = {}) {
    const rnd = createRandom(hashSeed(seed, depth, paths)), f = new Float64Array(6), n = new Float64Array(3), hit = { t: 0, tri: NO_HIT }, e = [0, 0, 0];
    const sum = [0, 0, 0];
    for (let p = 0; p < paths; p++) {
        let x = px, y = py, z = pz, cx = nx, cy = ny, cz = nz, tr = 1, tg = 1, tb = 1;
        for (let k = 0; k < depth; k++) {
            frame(cx, cy, cz, f);
            const r1 = rnd(), r2 = rnd(), phi = 2 * Math.PI * r1, sr = Math.sqrt(r2), c = Math.sqrt(1 - r2);
            let dx = f[0] * Math.cos(phi) * sr + f[3] * Math.sin(phi) * sr + cx * c;
            let dy = f[1] * Math.cos(phi) * sr + f[4] * Math.sin(phi) * sr + cy * c;
            let dz = f[2] * Math.cos(phi) * sr + f[5] * Math.sin(phi) * sr + cz * c;
            const dl = Math.hypot(dx, dy, dz); dx /= dl; dy /= dl; dz /= dl;
            const ox = x + cx * eps, oy = y + cy * eps, oz = z + cz * eps;
            trace(bvh, ox, oy, oz, dx, dy, dz, range, false, hit);
            if (hit.tri === NO_HIT) break;
            triNormal(bvh.tris, hit.tri, n);
            if (n[0] * dx + n[1] * dy + n[2] * dz > 0) { n[0] = -n[0]; n[1] = -n[1]; n[2] = -n[2]; }
            const b = hit.tri * 16;
            x = ox + dx * hit.t; y = oy + dy * hit.t; z = oz + dz * hit.t; cx = n[0]; cy = n[1]; cz = n[2];
            tr *= bvh.tris[b + 12]; tg *= bvh.tris[b + 13]; tb *= bvh.tris[b + 14];
            if (k === 0) continue;   // the first bounce is the lightmap's estimator
            directCpu(bvh, lights, lightCount, x, y, z, cx, cy, cz, { eps }, e);
            sum[0] += tr * e[0]; sum[1] += tg * e[1]; sum[2] += tb * e[2];
        }
    }
    return sum.map(v => v / Math.max(1, paths));
}
export const luminance = rgb => 0.2126729 * rgb[0] + 0.7151522 * rgb[1] + 0.0721750 * rgb[2];

/* ---- Lightmap: 2 x 2 texels per triangle ----------------------------------- */
export function lightmapLayout(triangleCount) {
    const cellsPerRow = Math.max(1, Math.ceil(Math.sqrt(Math.max(1, triangleCount))));
    const rows = Math.max(1, Math.ceil(Math.max(1, triangleCount) / cellsPerRow));
    return { width: cellsPerRow * 2, height: rows * 2, cellsPerRow, triangleCount };
}
/* uv for each non-indexed corner (A, B, C of triangle t): texel centres of its cell. */
export function lightmapUvs(layout, firstTriangle = 0, triangleCount = layout.triangleCount) {
    const uvs = new Float32Array(triangleCount * 6), { width, height, cellsPerRow } = layout;
    for (let i = 0; i < triangleCount; i++) {
        const t = firstTriangle + i, cx = (t % cellsPerRow) * 2, cy = Math.floor(t / cellsPerRow) * 2;
        uvs.set([(cx + 0.5) / width, (cy + 0.5) / height, (cx + 1.5) / width, (cy + 0.5) / height, (cx + 0.5) / width, (cy + 1.5) / height], i * 6);
    }
    return uvs;
}
/* Receiver irradiance (rgb) to half-float RGBA texels, times exposure. The
 * fourth texel of each cell extends the triangle's plane, so bilinear
 * sampling inside the triangle is exactly the corner interpolation. */
export function fillLightmap(layout, corners, irradiance, exposure = 1, out = new Uint16Array(layout.width * layout.height * 4)) {
    const { width, cellsPerRow, triangleCount } = layout, one = 0x3c00;
    const put = (x, y, r, g, b) => {
        const o = (y * width + x) * 4;
        out[o] = toHalf(Math.max(0, r) * exposure); out[o + 1] = toHalf(Math.max(0, g) * exposure); out[o + 2] = toHalf(Math.max(0, b) * exposure); out[o + 3] = one;
    };
    for (let t = 0; t < triangleCount; t++) {
        const cx = (t % cellsPerRow) * 2, cy = Math.floor(t / cellsPerRow) * 2;
        const a = corners[t * 3] * 3, b = corners[t * 3 + 1] * 3, c = corners[t * 3 + 2] * 3;
        put(cx, cy, irradiance[a], irradiance[a + 1], irradiance[a + 2]);
        put(cx + 1, cy, irradiance[b], irradiance[b + 1], irradiance[b + 2]);
        put(cx, cy + 1, irradiance[c], irradiance[c + 1], irradiance[c + 2]);
        put(cx + 1, cy + 1, irradiance[b] + irradiance[c] - irradiance[a], irradiance[b + 1] + irradiance[c + 1] - irradiance[a + 1],
            irradiance[b + 2] + irradiance[c + 2] - irradiance[a + 2]);
    }
    return out;
}

/* ---- WGSL: the same three kernels ---------------------------------------- */
const INV_EPS_WGSL = '1e-12';
export const LIT_BAKE_WGSL = /* wgsl */`
struct Node { mn: vec3f, a: u32, mx: vec3f, count: u32 };
struct Tri { v0: vec4f, e1: vec4f, e2: vec4f, col: vec4f };
struct Light { pos: vec3f, cd: f32, axis: vec3f, cosO: f32, col: vec3f, cosI: f32, spare: vec4f };
struct Recv { p: vec4f, n: vec4f };
struct Vpl { p: vec4f, n: vec4f, pw: vec4f };
struct U { cfg: vec4u, cfg2: vec4u, f: vec4f };
@group(0) @binding(0) var<storage, read> nodes: array<Node>;
@group(0) @binding(1) var<storage, read> tris: array<Tri>;
@group(0) @binding(2) var<storage, read> recv: array<Recv>;
@group(0) @binding(3) var<storage, read> lights: array<Light>;
@group(0) @binding(4) var<storage, read_write> vpls: array<Vpl>;
@group(0) @binding(5) var<storage, read_write> outv: array<f32>;
@group(0) @binding(6) var<uniform> u: U;
const NONE = 0xffffffffu;
const PI = 3.14159265;
var<private> state: u32;
fn hashSeed(a: u32, b: u32, c: u32) -> u32 {
  var h = a * 9781u + b * 6271u + c * 26699u;
  h = (h ^ (h >> 16u)) * 0x7feb352du; h = (h ^ (h >> 15u)) * 0x846ca68bu; return h ^ (h >> 16u);
}
fn rnd() -> f32 {
  state = state * 747796405u + 2891336453u;
  var w = ((state >> ((state >> 28u) + 4u)) ^ state) * 277803737u; w = (w >> 22u) ^ w;
  return f32(w) / 4294967296.0;
}
fn cone(cosO: f32, cosI: f32, c: f32) -> f32 {
  if (cosI <= cosO) { return select(0.0, 1.0, c >= cosO); }
  let t = clamp((c - cosO) / (cosI - cosO), 0.0, 1.0); return t * t * (3.0 - 2.0 * t);
}
fn slab(o: vec3f, inv: vec3f, mn: vec3f, mx: vec3f, tmax: f32) -> f32 {
  let t0 = (mn - o) * inv; let t1 = (mx - o) * inv;
  let tn = max(max(min(t0.x, t1.x), min(t0.y, t1.y)), min(t0.z, t1.z));
  let tf = min(min(max(t0.x, t1.x), max(t0.y, t1.y)), max(t0.z, t1.z));
  if (tf >= max(tn, 0.0) && tn < tmax) { return max(tn, 0.0); } return 1e30;
}
struct Hit { t: f32, tri: u32 };
fn safeInv(v: f32) -> f32 { return 1.0 / select(v, select(${INV_EPS_WGSL}, -${INV_EPS_WGSL}, v < 0.0), abs(v) < ${INV_EPS_WGSL}); }
// No continue, no early return inside loops: D3D12 shader compilers have
// turned that shape into a GPU hang here. A walk that runs out of steps
// counts as occluded for shadow rays, so a bad ray can never leak light.
fn trace(o: vec3f, d: vec3f, tmax: f32, anyHit: bool) -> Hit {
  var h = Hit(tmax, NONE); let inv = vec3f(safeInv(d.x), safeInv(d.y), safeInv(d.z));
  var stack: array<u32, ${BVH_STACK}>; var sp = 0u; var ni = 0u;
  var done = arrayLength(&tris) == 0u; var found = false; var steps = 0u;
  while (!done && steps < ${BVH_MAX_STEPS}u) {
    steps++;
    let n = nodes[ni];
    var pop = false;
    if (n.count > 0u) {
      for (var i = n.a; i < n.a + n.count; i++) {
        let tr = tris[i]; let p = cross(d, tr.e2.xyz); let det = dot(tr.e1.xyz, p);
        if (abs(det) > 1e-9 && !found) {
          let id = 1.0 / det; let s = o - tr.v0.xyz; let uu = dot(s, p) * id;
          let qv = cross(s, tr.e1.xyz); let vv = dot(d, qv) * id; let t = dot(tr.e2.xyz, qv) * id;
          if (uu >= 0.0 && uu <= 1.0 && vv >= 0.0 && uu + vv <= 1.0 && t > 1e-4 && t < h.t) { h = Hit(t, i); found = anyHit; }
        }
      }
      pop = true;
    } else {
      let l = n.a; let r = n.a + 1u; let nl = nodes[l]; let nr = nodes[r];
      let dl = slab(o, inv, nl.mn, nl.mx, h.t); let dr = slab(o, inv, nr.mn, nr.mx, h.t);
      if (dl > 1e29 && dr > 1e29) { pop = true; }
      else if (dl <= dr) { ni = l; if (dr < 1e29 && sp < ${BVH_STACK}u) { stack[sp] = r; sp++; } }
      else { ni = r; if (dl < 1e29 && sp < ${BVH_STACK}u) { stack[sp] = l; sp++; } }
    }
    if (found) { done = true; }
    else if (pop) { if (sp == 0u) { done = true; } else { sp--; ni = stack[sp]; } }
  }
  if (!done && anyHit) { h = Hit(0.0, 0u); }
  return h;
}
fn triNormal(i: u32) -> vec3f { let tr = tris[i]; let c = cross(tr.e1.xyz, tr.e2.xyz); let l = length(c); return select(vec3f(0.0, 0.0, 1.0), c / l, l > 0.0); }
struct Frame { t: vec3f, b: vec3f };
fn frameOf(n: vec3f) -> Frame {
  let t = select(vec3f(1.0, 0.0, 0.0), vec3f(0.0, 1.0, 0.0), abs(n.x) > 0.9);
  let b = normalize(cross(n, t)); return Frame(cross(b, n), b);
}
fn lightTerm(li: u32, p: vec3f, n: vec3f) -> vec4f {
  let L = lights[li]; let tl = L.pos - p; let d2 = dot(tl, tl); let d = sqrt(d2);
  if (d < 1e-5) { return vec4f(0.0); }
  let wi = tl / d; let ndl = dot(n, wi); if (ndl <= 0.0) { return vec4f(0.0); }
  let c = cone(L.cosO, L.cosI, -dot(wi, L.axis)); if (c <= 0.0) { return vec4f(0.0); }
  return vec4f(L.col * (L.cd * c * ndl / max(d2, 0.01)), d);
}
fn visibleTo(p: vec3f, n: vec3f, q: vec3f) -> bool {
  let o = p + n * u.f.x; let tl = q - o; let d = length(tl);
  if (d <= u.f.x * 2.0) { return true; }
  return trace(o, tl / d, d - u.f.x, true).tri == NONE;
}
// One thread per virtual point light of the spot in lights[0].
@compute @workgroup_size(64) fn vplEmit(@builtin(global_invocation_id) g: vec3u) {
  let i = g.x; let count = u.cfg2.y; if (i >= count) { return; }
  state = hashSeed(i, u.cfg2.z, u.cfg2.x ^ 0x9e3779b9u);
  let L = lights[0]; let fr = frameOf(L.axis);
  let c = 1.0 - rnd() * (1.0 - L.cosO); let ph = 2.0 * PI * rnd(); let s = sqrt(max(0.0, 1.0 - c * c));
  let d = normalize(fr.t * cos(ph) * s + fr.b * sin(ph) * s + L.axis * c);
  var v = Vpl(vec4f(0.0), vec4f(0.0), vec4f(0.0));
  let w = cone(L.cosO, L.cosI, c);
  if (w > 0.0) {
    let h = trace(L.pos, d, u.f.z, false);
    if (h.tri != NONE) {
      var ny = triNormal(h.tri); if (dot(ny, d) > 0.0) { ny = -ny; }
      let power = L.cd * w * 2.0 * PI * (1.0 - L.cosO) / f32(count);
      v = Vpl(vec4f(L.pos + d * h.t + ny * u.f.x, 1.0), vec4f(ny, 0.0), vec4f(tris[h.tri].col.xyz * L.col * power, 0.0));
    }
  }
  vpls[i] = v;
}
// One thread per receiver: gather VPLs cfg.z..cfg.w through shadow rays and
// add them to outv[receiver * 3] (cfg2.w = 1 starts the sum). Slicing the
// VPLs, not the receivers, keeps every dispatch wide enough to fill the GPU.
@compute @workgroup_size(64) fn vplGather(@builtin(global_invocation_id) g: vec3u) {
  if (g.x >= u.cfg.y) { return; }
  let r = u.cfg.x + g.x; let R = recv[r]; let n = R.n.xyz; let x = R.p.xyz + n * u.f.x;
  var e = vec3f(0.0);
  for (var i = u.cfg.z; i < min(u.cfg.w, u.cfg2.y); i++) {
    let v = vpls[i]; let dv = v.p.xyz - x; let r2 = dot(dv, dv); let dist = sqrt(r2);
    let w = dv / max(dist, 1e-6); let cx = dot(n, w); let cy = -dot(v.n.xyz, w);
    if (v.p.w > 0.5 && dist >= 1e-4 && cx > 0.0 && cy > 0.0) {
      if (trace(x, w, dist - u.f.x, true).tri == NONE) { e += v.pw.xyz * (cx * cy / (PI * max(r2, u.f.y))); }
    }
  }
  let o = r * 3u; let keep = select(1.0, 0.0, u.cfg2.w == 1u);
  outv[o] = outv[o] * keep + e.x; outv[o + 1u] = outv[o + 1u] * keep + e.y; outv[o + 2u] = outv[o + 2u] * keep + e.z;
}
// One thread per receiver of a chunk: cosine hemisphere gather, per light,
// into outv[(local receiver * lights + light) * 3].
@compute @workgroup_size(64) fn skyGather(@builtin(global_invocation_id) g: vec3u) {
  if (g.x >= u.cfg.y) { return; }
  let r = u.cfg.x + g.x; let nl = min(u.cfg.z, ${MAX_LAYER_LIGHTS}u); let R = recv[r]; let n = R.n.xyz; let x = R.p.xyz + n * u.f.x;
  state = hashSeed(r, u.cfg2.z, u.cfg2.x); let fr = frameOf(n);
  // Accumulate in this thread's own slice of outv: a large indexed private
  // array beside the traversal stack hangs some D3D12 drivers.
  let base = g.x * nl * 3u;
  for (var l = 0u; l < nl * 3u; l++) { outv[base + l] = 0.0; }
  for (var s = 0u; s < u.cfg.w; s++) {
    let r1 = rnd(); let r2 = rnd(); let ph = 2.0 * PI * r1; let sr = sqrt(r2);
    let d = normalize(fr.t * cos(ph) * sr + fr.b * sin(ph) * sr + n * sqrt(1.0 - r2));
    let h = trace(x, d, u.f.z, false);
    if (h.tri != NONE) {
      var ny = triNormal(h.tri); if (dot(ny, d) > 0.0) { ny = -ny; }
      let y = x + d * h.t; let rho = tris[h.tri].col.xyz;
      for (var l = 0u; l < nl; l++) {
        let lt = lightTerm(l, y, ny);
        if (lt.w > 0.0) {
          if (visibleTo(y, ny, lights[l].pos)) { let add = rho * lt.xyz; let o = base + l * 3u; outv[o] += add.x; outv[o + 1u] += add.y; outv[o + 2u] += add.z; }
        }
      }
    }
  }
  let inv = 1.0 / f32(max(u.cfg.w, 1u));
  for (var l = 0u; l < nl * 3u; l++) { outv[base + l] *= inv; }
}
`;

/* ---- GPU tracer ----------------------------------------------------------- */
const WORKGROUP = 64;
const MAX_DISPATCH_RECEIVERS = 65535 * WORKGROUP;
const SKY_CHUNK_MAX = 32768;
/* A software (fallback) adapter such as SwiftShader is no faster than the
 * CPU twin and holds the GPU process, so it is refused unless asked for
 * (the CI proof uses it to run the WGSL). */
export const isSoftwareAdapter = adapter => !!(adapter?.isFallbackAdapter || adapter?.info?.isFallbackAdapter
    || /swiftshader|llvmpipe|lavapipe|warp|software/i.test([adapter?.info?.vendor, adapter?.info?.architecture, adapter?.info?.description].join(' ')));
export async function createGpuTracer(gpu, { adapterOptions = { powerPreference: 'high-performance' }, allowSoftware = false } = {}) {
    if (!gpu?.requestAdapter) return null;
    const adapter = await gpu.requestAdapter(adapterOptions);
    if (!adapter || (!allowSoftware && isSoftwareAdapter(adapter))) return null;
    const limit = (name, want) => Math.min(adapter.limits?.[name] ?? want, want);
    const device = await adapter.requestDevice({ requiredLimits: {
        maxStorageBufferBindingSize: limit('maxStorageBufferBindingSize', 1 << 30), maxBufferSize: limit('maxBufferSize', 1 << 30) } });
    let lost = null;
    device.lost?.then(info => { lost = info?.message || 'device_lost'; });
    const module = device.createShaderModule({ code: LIT_BAKE_WGSL });
    const info = await module.getCompilationInfo?.();
    const errors = (info?.messages || []).filter(message => message.type === 'error');
    if (errors.length) { device.destroy?.(); throw new Error('lit_bake_wgsl: ' + errors.map(e => e.lineNum + ':' + e.message).join('; ')); }
    const S = GPUBufferUsage.STORAGE, D = GPUBufferUsage.COPY_DST, C = GPUBufferUsage.COPY_SRC;
    const entries = [0, 1, 2, 3].map(binding => ({ binding, visibility: GPUShaderStage.COMPUTE, buffer: { type: 'read-only-storage' } }))
        .concat([4, 5].map(binding => ({ binding, visibility: GPUShaderStage.COMPUTE, buffer: { type: 'storage' } })),
            [{ binding: 6, visibility: GPUShaderStage.COMPUTE, buffer: { type: 'uniform' } }]);
    const layout = device.createBindGroupLayout({ entries });
    const pipelineLayout = device.createPipelineLayout({ bindGroupLayouts: [layout] });
    const pipeline = entryPoint => device.createComputePipeline({ layout: pipelineLayout, compute: { module, entryPoint } });
    const pipelines = { vplEmit: pipeline('vplEmit'), vplGather: pipeline('vplGather'), skyGather: pipeline('skyGather') };
    const buffer = (size, usage) => device.createBuffer({ size: Math.max(16, Math.ceil(size / 16) * 16), usage });
    const upload = (data, usage = S) => { const b = buffer(data.byteLength, usage | D); device.queue.writeBuffer(b, 0, data); return b; };
    let scene = null;
    const uniform = buffer(48, GPUBufferUsage.UNIFORM | D), lightsBuffer = buffer(MAX_LAYER_LIGHTS * LIGHT_FLOATS * 4, S | D);
    function setScene(bvh, receivers, receiverCount) {
        disposeScene();
        const maxVpl = LIT_BAKE_QUALITY.full.vpl * 4;
        scene = { receiverCount, nodes: upload(new Uint8Array(bvh.nodes)), tris: upload(bvh.tris), recv: upload(receivers.length ? receivers : new Float32Array(8)),
            vpls: buffer(maxVpl * 48, S), out: buffer(Math.max(receiverCount * 3, SKY_CHUNK_MAX * MAX_LAYER_LIGHTS * 3) * 4, S | C | D),
            maxVpl };
        scene.staging = buffer(scene.out.size, GPUBufferUsage.MAP_READ | D);
        scene.bind = device.createBindGroup({ layout, entries: [scene.nodes, scene.tris, scene.recv, lightsBuffer, scene.vpls, scene.out, uniform]
            .map((resource, binding) => ({ binding, resource: { buffer: resource } })) });
    }
    function disposeScene() {
        if (!scene) return;
        for (const key of ['nodes', 'tris', 'recv', 'vpls', 'out', 'staging']) scene[key]?.destroy?.();
        scene = null;
    }
    function writeUniform(cfg, cfg2, f) {
        const data = new ArrayBuffer(48), u = new Uint32Array(data), fl = new Float32Array(data);
        u.set(cfg, 0); u.set(cfg2, 4); fl.set(f, 8);
        device.queue.writeBuffer(uniform, 0, data);
    }
    async function dispatch(name, groups) {
        const encoder = device.createCommandEncoder(), pass = encoder.beginComputePass();
        pass.setPipeline(pipelines[name]); pass.setBindGroup(0, scene.bind); pass.dispatchWorkgroups(Math.max(1, groups)); pass.end();
        device.queue.submit([encoder.finish()]);
        await device.queue.onSubmittedWorkDone();
        if (lost) throw new Error('lit_bake_device_lost: ' + lost);
    }
    async function read(byteLength) {
        const encoder = device.createCommandEncoder();
        encoder.copyBufferToBuffer(scene.out, 0, scene.staging, 0, Math.ceil(byteLength / 4) * 4);
        device.queue.submit([encoder.finish()]);
        await scene.staging.mapAsync(GPUMapMode.READ, 0, Math.ceil(byteLength / 4) * 4);
        const copy = new Float32Array(scene.staging.getMappedRange(0, Math.ceil(byteLength / 4) * 4).slice(0));
        scene.staging.unmap();
        return copy;
    }
    /* Spot layer: virtual point lights, `passes` passes of `vpl` each,
     * gathered in VPL slices of `vplSlice` over every receiver at once.
     * onPass(mean irradiance Float32Array(receivers * 3), pass) after each
     * pass; `check()` throws to abort between dispatches. */
    async function bakeVpl(light, { vpl, passes, vplSlice = vpl, seed, eps, minDistance, range }, { onPass, check }) {
        const count = Math.min(vpl, scene.maxVpl), R = scene.receiverCount, f = [eps, minDistance * minDistance, range, 0];
        device.queue.writeBuffer(lightsBuffer, 0, light);
        for (let pass = 0; pass < passes; pass++) {
            writeUniform([0, 0, 0, 0], [seed, count, pass, 0], f);
            await dispatch('vplEmit', Math.ceil(count / WORKGROUP));
            check();
            for (let start = 0; start < count; start += vplSlice) {
                for (let offset = 0; offset < R; offset += MAX_DISPATCH_RECEIVERS) {
                    const n = Math.min(MAX_DISPATCH_RECEIVERS, R - offset);
                    writeUniform([offset, n, start, start + vplSlice], [seed, count, pass, pass === 0 && start === 0 ? 1 : 0], f);
                    await dispatch('vplGather', Math.ceil(n / WORKGROUP));
                }
                check();
            }
            const sums = await read(R * 12);
            for (let i = 0; i < sums.length; i++) sums[i] /= pass + 1;
            await onPass(sums, pass);
        }
    }
    /* Static layers: hemisphere gather for up to 32 lights at once, in wide
     * receiver chunks; onChunk(offset, count, values Float32Array(count * lights * 3)). */
    async function bakeSky(lights, lightCount, { sky, skyChunk = SKY_CHUNK_MAX, seed, eps, range, from = 0, to = scene.receiverCount }, { onChunk, check }) {
        device.queue.writeBuffer(lightsBuffer, 0, lights);
        const chunk = Math.min(SKY_CHUNK_MAX, skyChunk), end = Math.min(to, scene.receiverCount);
        for (let offset = from; offset < end; offset += chunk) {
            const n = Math.min(chunk, end - offset);
            writeUniform([offset, n, lightCount, sky], [seed, 0, 0, 0], [eps, 0, range, 0]);
            await dispatch('skyGather', Math.ceil(n / WORKGROUP));
            check();
            onChunk(offset, n, await read(n * lightCount * 12));
        }
    }
    return { backend: 'webgpu', adapterInfo: adapter.info ? { vendor: adapter.info.vendor, architecture: adapter.info.architecture } : null,
        setScene, bakeVpl, bakeSky, lost: () => lost,
        dispose() { disposeScene(); uniform.destroy?.(); lightsBuffer.destroy?.(); device.destroy?.(); } };
}

/* ---- The bake host (runs in the worker, or in-thread) --------------------- */

/* Messages in:
 *   { type: 'scene', seq, triangles: Float32Array(9T), albedo: Float32Array(3T) }
 *   { type: 'lights', seq, scene, lights: [{ key, mode: 'vpl'|'sky', origin, direction, cosOuter, cosInner }],
 *     weights: { key: [r, g, b] }, exposure, quality?, aims: [{ id, label, point, normal }] }
 * Messages out:
 *   { type: 'scene', seq, ok, backend, receivers, triangles, nodes, depth, buildMs, layout }
 *   { type: 'lightmap', seq, scene, data: Uint16Array, width, height, final, stats }
 *   { type: 'lux', seq, scene, aims: [{ id, label, lux, direct, bounce }] }
 *   { type: 'error', seq, message } */
export function attachBakeHost(scope, { gpu = null, allowSoftware = false, now = () => performance.now(), yieldTask = () => new Promise(resolve => setTimeout(resolve, 0)) } = {}) {
    let scene = null, tracer = null, tracerTried = false, latest = null, done = null, running = false, disposed = false;
    const layers = new Map();   // key -> { signature, data: Uint16Array(R * 3) }
    const post = (message, transfer = []) => { if (!disposed) scope.postMessage(message, transfer); };
    const ABORT = Symbol('stale');

    async function useTracer() {
        if (tracerTried) return tracer;
        tracerTried = true;
        try { tracer = await createGpuTracer(gpu, { allowSoftware }); } catch (error) { tracer = null; post({ type: 'warning', message: String(error?.message || error) }); }
        return tracer;
    }
    async function setScene(message) {
        const t0 = now();
        const receivers = buildReceivers(message.triangles);
        const bvh = buildBvh(message.triangles, message.albedo);
        const layout = lightmapLayout(receivers.triangleCount);
        layers.clear();
        scene = { seq: message.seq, ...receivers, bvh, layout };
        const gpuTracer = receivers.triangleCount ? await useTracer() : null;
        if (gpuTracer) {
            try { gpuTracer.setScene(bvh, receivers.receivers, receivers.receiverCount); }
            catch (error) { tracer = null; post({ type: 'warning', message: String(error?.message || error) }); }
        }
        post({ type: 'scene', seq: message.seq, ok: true, backend: tracer ? 'webgpu' : 'cpu', receivers: receivers.receiverCount,
            triangles: receivers.triangleCount, nodes: bvh.nodeCount, depth: bvh.depth, buildMs: now() - t0, layout });
        void pump();
    }
    const signatureOf = light => [light.mode, ...light.origin, ...light.direction, light.cosOuter, light.cosInner].map(v => (typeof v === 'number' ? v.toFixed(5) : v)).join(',');
    const unitLight = light => packLights([{ ...light, candela: 1, color: [1, 1, 1] }]).data;
    const stale = (message, keys) => {
        if (disposed || !scene || scene.seq !== message.scene) return true;
        if (latest === message) return false;
        if (!latest || latest.scene !== message.scene) return true;
        const wanted = new Map(latest.lights.map(light => [light.key, signatureOf(light)]));
        return keys.some(([key, signature]) => wanted.get(key) !== signature);
    };
    const toHalfLayer = (values, stride = 3, index = 0, count = values.length / stride, into = new Uint16Array(count * 3), at = 0) => {
        for (let i = 0; i < count; i++) for (let c = 0; c < 3; c++) into[(at + i) * 3 + c] = toHalf(values[i * stride + index * 3 + c]);
        return into;
    };

    /* The lightmap irradiance is the weighted sum of the layers. It is kept
     * between messages and updated by difference, so one re-baked spot or
     * one moved slider costs one layer pass, not 32; a full sum every 64
     * updates bounds float drift. */
    let composed = null;
    function compose(message, pending = new Map()) {
        const R = scene.receiverCount, half = halfTableF32(), wanted = new Map();
        for (const [key, weight] of Object.entries(message.weights || {})) {
            const layer = pending.get(key) || layers.get(key)?.data;
            if (layer && weight && (weight[0] || weight[1] || weight[2])) wanted.set(key, { layer, weight: weight.slice(0, 3) });
        }
        const add = (layer, [wr, wg, wb], irradiance, sign) => {
            for (let j = 0; j < R * 3; j += 3) {
                irradiance[j] += sign * half[layer[j]] * wr; irradiance[j + 1] += sign * half[layer[j + 1]] * wg; irradiance[j + 2] += sign * half[layer[j + 2]] * wb;
            }
        };
        if (!composed || composed.scene !== scene || composed.updates >= 64) {
            composed = { scene, irradiance: new Float32Array(R * 3), used: wanted, updates: 0 };
            for (const { layer, weight } of wanted.values()) add(layer, weight, composed.irradiance, 1);
        } else {
            for (const key of new Set([...composed.used.keys(), ...wanted.keys()])) {
                const before = composed.used.get(key), after = wanted.get(key);
                if (before && after && before.layer === after.layer && before.weight.every((v, i) => v === after.weight[i])) continue;
                if (before) add(before.layer, before.weight, composed.irradiance, -1);
                if (after) add(after.layer, after.weight, composed.irradiance, 1);
                composed.updates++;
            }
            composed.used = wanted;
        }
        const irradiance = composed.irradiance;
        return { irradiance, texels: fillLightmap(scene.layout, scene.corners, irradiance, Number.isFinite(message.exposure) ? message.exposure : 1) };
    }
    function postLightmap(message, pending, final, stats) {
        const { texels } = compose(latest && latest.scene === message.scene ? latest : message, pending);
        post({ type: 'lightmap', seq: (latest || message).seq, scene: message.scene, data: texels, width: scene.layout.width, height: scene.layout.height, final, stats },
            [texels.buffer]);
    }

    async function bakeCpuVpl(light, quality, onPass, check) {
        const R = scene.receiverCount, out = new Float32Array(R * 3), tmp = [0, 0, 0];
        for (let pass = 0; pass < quality.vplPasses; pass++) {
            const vpls = emitVplsCpu(scene.bvh, light, { count: quality.vpl, pass, seed: quality.seed });
            let since = now();
            for (let r = 0; r < R; r++) {
                const o = r * 8, rc = scene.receivers;
                gatherVplsCpu(scene.bvh, vpls, rc[o], rc[o + 1], rc[o + 2], rc[o + 4], rc[o + 5], rc[o + 6], {}, tmp);
                for (let c = 0; c < 3; c++) out[r * 3 + c] = (out[r * 3 + c] * pass + tmp[c]) / (pass + 1);
                if (now() - since > quality.chunkMs) { await yieldTask(); check(); since = now(); }
            }
            await onPass(out, pass);
        }
    }
    async function bakeCpuSky(lights, count, quality, onChunk, check) {
        const R = scene.receiverCount, rc = scene.receivers, values = new Float64Array(count * 3);
        let since = now(), start = 0, buffer = [];
        for (let r = 0; r < R; r++) {
            const o = r * 8;
            gatherSkyCpu(scene.bvh, lights, count, rc[o], rc[o + 1], rc[o + 2], rc[o + 4], rc[o + 5], rc[o + 6],
                { samples: quality.sky, seed: quality.seed, receiver: r }, values);
            buffer.push(...values);
            if (now() - since > quality.chunkMs || r === R - 1) {
                onChunk(start, r + 1 - start, buffer); start = r + 1; buffer = [];
                await yieldTask(); check(); since = now();
            }
        }
    }

    async function process(message) {
        if (!scene || scene.seq !== message.scene) return;
        const quality = { ...(LIT_BAKE_QUALITY[message.quality] || (tracer ? LIT_BAKE_QUALITY.full : LIT_BAKE_QUALITY.cpu)), seed: 1 };
        const wantedKeys = new Set(message.lights.map(light => light.key));
        for (const key of [...layers.keys()]) if (!wantedKeys.has(key)) layers.delete(key);
        const dirty = message.lights.filter(light => layers.get(light.key)?.signature !== signatureOf(light));
        const spots = dirty.filter(light => light.mode === 'vpl'), statics = dirty.filter(light => light.mode !== 'vpl').slice(0, MAX_LAYER_LIGHTS);
        const R = scene.receiverCount, pending = new Map(), stats = { backend: tracer ? 'webgpu' : 'cpu', quality: quality.name, receivers: R, jobs: [] };
        if (!scene.triangleCount) { postLightmap(message, pending, true, stats); return; }
        if (statics.length) {
            const keys = statics.map(light => [light.key, signatureOf(light)]), check = () => { if (stale(message, keys)) throw ABORT; };
            const packed = packLights(statics.map(light => ({ ...light, candela: 1, color: [1, 1, 1] })));
            const out = statics.map(() => new Uint16Array(R * 3)), t0 = now();
            const onChunk = (offset, count, values) => {
                for (let l = 0; l < statics.length; l++) toHalfLayer(values, statics.length * 3, l, count, out[l], offset);
            };
            if (tracer) await tracer.bakeSky(packed.data, packed.count, { ...quality, eps: LIT_BAKE_RECEIVER_OFFSET_M, range: LIT_BAKE_RANGE_M },
                { onChunk, check });
            else await bakeCpuSky(packed.data, packed.count, quality, onChunk, check);
            statics.forEach((light, l) => { layers.set(light.key, { signature: keys[l][1], data: out[l] }); });
            stats.jobs.push({ kind: 'static', lights: statics.length, samples: quality.sky, ms: now() - t0 });
        }
        for (const light of spots) {
            const keys = [[light.key, signatureOf(light)]], check = () => { if (stale(message, keys)) throw ABORT; };
            const t0 = now(), packed = unitLight(light);
            let firstMs = null;
            const onPass = async (irradiance, pass) => {
                pending.set(light.key, toHalfLayer(irradiance));
                if (firstMs === null) firstMs = now() - t0;
                if (pass < quality.vplPasses - 1) { postLightmap(message, pending, false, { ...stats, progress: (pass + 1) / quality.vplPasses }); await yieldTask(); check(); }
            };
            if (tracer) await tracer.bakeVpl(packed, { ...quality, passes: quality.vplPasses, eps: LIT_BAKE_RECEIVER_OFFSET_M,
                minDistance: LIT_VPL_MIN_DISTANCE_M, range: LIT_BAKE_RANGE_M },
                { onPass, check });
            else await bakeCpuVpl(packed, quality, onPass, check);
            layers.set(light.key, { signature: keys[0][1], data: pending.get(light.key) });
            pending.delete(light.key);
            stats.jobs.push({ kind: 'spot', key: light.key, vpl: quality.vpl, passes: quality.vplPasses, firstPassMs: firstMs, ms: now() - t0 });
        }
        postLightmap(message, pending, true, stats);
        // Lux waits for a quiet moment: a newer message (a slider still
        // moving) is composed first.
        if (latest === message && message.aims?.length) postLux(message);
    }

    function postLux(message) {
        const lights = message.luxLights || [], aims = message.aims || [];
        const packed = packLights(lights), spots = lights.map((light, index) => ({ light, index })).filter(item => item.light.mode === 'vpl');
        const statics = lights.filter(light => light.mode !== 'vpl'), staticPacked = packLights(statics);
        const vplSets = spots.map(({ light }) => emitVplsCpu(scene.bvh, packLights([light]).data, { count: LIT_BAKE_QUALITY.lux.vpl, seed: 7 }));
        const results = aims.map((aim, index) => {
            const [px, py, pz] = aim.point, [nx, ny, nz] = aim.normal;
            const direct = directCpu(scene.bvh, packed.data, packed.count, px, py, pz, nx, ny, nz);
            const bounce = [0, 0, 0], tmp = [0, 0, 0];
            for (const vpls of vplSets) { gatherVplsCpu(scene.bvh, vpls, px, py, pz, nx, ny, nz, {}, tmp); bounce[0] += tmp[0]; bounce[1] += tmp[1]; bounce[2] += tmp[2]; }
            if (statics.length) {
                const values = gatherSkyCpu(scene.bvh, staticPacked.data, staticPacked.count, px, py, pz, nx, ny, nz,
                    { samples: LIT_BAKE_QUALITY.lux.sky, seed: 11, receiver: index });
                for (let l = 0; l < statics.length; l++) for (let c = 0; c < 3; c++) bounce[c] += values[l * 3 + c];
            }
            const higher = higherBouncesCpu(scene.bvh, packed.data, packed.count, px, py, pz, nx, ny, nz,
                { paths: LIT_BAKE_QUALITY.lux.paths, depth: LIT_BAKE_QUALITY.lux.depth, seed: 13 + index });
            const directLux = luminance(direct), bounceLux = luminance(bounce), higherLux = luminance(higher);
            return { id: aim.id, label: aim.label, lux: directLux + bounceLux + higherLux, direct: directLux, bounce: bounceLux, higherBounces: higherLux };
        });
        post({ type: 'lux', seq: message.seq, scene: message.scene, aims: results });
    }

    async function pump() {
        if (running) return;
        running = true;
        try {
            while (latest && latest !== done && !disposed) {
                const message = latest;
                // A message for a scene still being built waits for it.
                if (!scene || scene.seq !== message.scene) break;
                try { await process(message); done = message; }
                catch (error) {
                    if (error !== ABORT) { done = message; post({ type: 'error', seq: message.seq, message: String(error?.message || error) }); }
                    else if (latest === message) done = message;
                }
                await yieldTask();
            }
        } finally { running = false; }
    }
    async function onMessage(event) {
        const message = event?.data ?? event;
        if (!message || disposed) return;
        try {
            if (message.type === 'scene') { latest = null; done = null; await setScene(message); }
            else if (message.type === 'lights') { latest = message; void pump(); }
            else if (message.type === 'dispose') { disposed = true; tracer?.dispose?.(); scene = null; layers.clear(); }
        } catch (error) { post({ type: 'error', seq: message.seq, message: String(error?.message || error) }); }
    }
    scope.addEventListener('message', onMessage);
    return { onMessage, state: () => ({ scene: !!scene, layers: layers.size, backend: tracer ? 'webgpu' : 'cpu', running }) };
}

/* An in-thread endpoint with a worker's interface (Node tests, and pages
 * where module workers are unavailable). */
export function createLocalBakeEndpoint(options = {}) {
    const toHost = new Set(), toPage = new Set();
    let closed = false;
    const hostScope = {
        postMessage(data) { if (!closed) queueMicrotask(() => { for (const listener of toPage) listener({ data }); }); },
        addEventListener(type, listener) { if (type === 'message') toHost.add(listener); },
    };
    const host = attachBakeHost(hostScope, options);
    return {
        host,
        postMessage(data) { if (!closed) queueMicrotask(() => { for (const listener of toHost) listener({ data }); }); },
        addEventListener(type, listener) { if (type === 'message') toPage.add(listener); },
        removeEventListener(type, listener) { toPage.delete(listener); },
        terminate() { closed = true; host.onMessage({ data: { type: 'dispose' } }); toHost.clear(); toPage.clear(); },
    };
}

/* The page side of the protocol: keeps the scene sequence, drops replies
 * for a replaced scene, and transfers the triangle arrays. */
export function createBakeClient({ endpoint, onScene, onLightmap, onLux, onError } = {}) {
    let seq = 0, sceneSeq = 0, closed = false;
    const listener = event => {
        const message = event?.data;
        if (closed || !message) return;
        if (message.type === 'scene') { if (message.seq === sceneSeq) onScene?.(message); return; }
        if (message.scene !== undefined && message.scene !== sceneSeq) return;
        if (message.type === 'lightmap') onLightmap?.(message);
        else if (message.type === 'lux') onLux?.(message);
        else if (message.type === 'error' || message.type === 'warning') onError?.(message);
    };
    endpoint.addEventListener('message', listener);
    return {
        setScene(triangles, albedo) {
            sceneSeq = ++seq;
            endpoint.postMessage({ type: 'scene', seq: sceneSeq, triangles, albedo }, [triangles.buffer, albedo.buffer]);
            return sceneSeq;
        },
        update(payload) { if (!closed && sceneSeq) endpoint.postMessage({ ...payload, type: 'lights', seq: ++seq, scene: sceneSeq }); },
        get scene() { return sceneSeq; },
        dispose() {
            if (closed) return;
            closed = true;
            endpoint.removeEventListener?.('message', listener);
            try { endpoint.postMessage({ type: 'dispose' }); } catch (_) { /* already gone */ }
            endpoint.terminate?.();
        },
    };
}
