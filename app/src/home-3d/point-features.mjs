/* home-3d/point-features.mjs — per-point values for the etched Cloud look.
 *
 * The engineered.lighting site reads as an etching because its light comes from
 * edges (corners, seams, outlines, printed patterns) on true black. A
 * homeapt-points v1 cloud holds only position and intensity, so this derives,
 * once per load and off the main thread (point-features-worker.mjs):
 *
 *   normal   the surface direction (PCA of the neighbourhood), for edge-on dimming
 *   edge     0..1: how much the surface turns there, or how much its intensity
 *            stands out from its neighbourhood
 *   tone     the scan's intensity averaged over the point's block (about 10 cm)
 *   spacing  the local sample spacing, so a point is drawn about as large as
 *            the patch of surface it stands for
 *
 * Neighbourhoods are 3x3x3 blocks of cubic cells. Moments are summed per cell
 * once, then per block, so the cost is linear in points plus occupied cells
 * rather than a radius search per point. A scan's surface noise is about as
 * large as its corners, so a block (about 10 cm across) is wide enough to
 * average the noise away. Intensity is just as grainy point to point, so tone
 * and contrast are measured per cell (a cell against its block), never per
 * point: one noisy point never lights up on a plain wall. The edge thresholds
 * are percentiles of this scan's own values, so a noisier or cleaner scan
 * lights the same share of edges.
 * Pure: no DOM, no three.js, so Node tests run it directly.
 */

export const POINT_FEATURE_CELL_M = 0.035;
/* Percentile windows that map surface variation / intensity contrast to edge 0..1. */
export const POINT_FEATURE_EDGE_PERCENTILES = Object.freeze({ curvature: [0.7, 0.97], contrast: [0.8, 0.99] });
/* Intensity contrast lights less than shape: a photo scan's intensity carries baked light and shadow. */
export const POINT_FEATURE_CONTRAST_WEIGHT = 0.4;
/* spacing is stored as millimetres in a byte */
export const POINT_FEATURE_SPACING_MAX_MM = 255;

const smoothstep = (a, b, x) => {
    const t = b > a ? Math.min(1, Math.max(0, (x - a) / (b - a))) : (x >= b ? 1 : 0);
    return t * t * (3 - 2 * t);
};

function percentile(values, n, p) {
    // values are 0..1; a 4096-bin histogram is exact enough for thresholds
    const bins = new Uint32Array(4096);
    for (let i = 0; i < n; i++) bins[Math.min(4095, Math.max(0, Math.floor(values[i] * 4096)))]++;
    const want = p * n;
    let acc = 0;
    for (let b = 0; b < 4096; b++) { acc += bins[b]; if (acc >= want) return (b + 0.5) / 4096; }
    return 1;
}

/* Smallest eigenpair of the symmetric 3x3 [[a,d,f],[d,b,e],[f,e,c]]: returns [lambda, nx, ny, nz]. */
export function smallestEigen(a, b, c, d, e, f) {
    const p1 = d * d + e * e + f * f, q = (a + b + c) / 3;
    const p2 = (a - q) ** 2 + (b - q) ** 2 + (c - q) ** 2 + 2 * p1, p = Math.sqrt(p2 / 6);
    if (!(p > 1e-15)) return [q, 0, 0, 1];
    const B0 = (a - q) / p, B1 = d / p, B2 = f / p, B3 = (b - q) / p, B4 = e / p, B5 = (c - q) / p;
    const det = B0 * (B3 * B5 - B4 * B4) - B1 * (B1 * B5 - B4 * B2) + B2 * (B1 * B4 - B3 * B2);
    const phi = Math.acos(Math.max(-1, Math.min(1, det / 2))) / 3;
    const l = q + 2 * p * Math.cos(phi + 2 * Math.PI / 3);
    const r0x = a - l, r0y = d, r0z = f, r1x = d, r1y = b - l, r1z = e, r2x = f, r2y = e, r2z = c - l;
    const c01 = [r0y * r1z - r0z * r1y, r0z * r1x - r0x * r1z, r0x * r1y - r0y * r1x];
    const c02 = [r0y * r2z - r0z * r2y, r0z * r2x - r0x * r2z, r0x * r2y - r0y * r2x];
    const c12 = [r1y * r2z - r1z * r2y, r1z * r2x - r1x * r2z, r1x * r2y - r1y * r2x];
    let best = c01, bl = c01[0] ** 2 + c01[1] ** 2 + c01[2] ** 2;
    for (const v of [c02, c12]) { const m = v[0] ** 2 + v[1] ** 2 + v[2] ** 2; if (m > bl) { bl = m; best = v; } }
    const len = Math.sqrt(bl);
    if (!(len > 1e-30)) return [l, 0, 0, 1];
    return [l, best[0] / len, best[1] / len, best[2] / len];
}

/**
 * positions: Float32Array (3 per point), intensity: Uint8Array (0..255), count: points to use.
 * Returns { normal: Int8Array(3n), edge: Uint8Array(n), tone: Uint8Array(n), spacing: Uint8Array(n) (mm), cells, thresholds }.
 */
export function computePointFeatures(positions, intensity, count, { cell = POINT_FEATURE_CELL_M } = {}) {
    const n = Math.max(0, Math.min(count | 0, Math.floor(positions.length / 3), intensity.length));
    const normal = new Int8Array(n * 3), edge = new Uint8Array(n), tone = new Uint8Array(n), spacing = new Uint8Array(n);
    if (!n) return { normal, edge, tone, spacing, cells: 0, thresholds: null };
    let mnx = Infinity, mny = Infinity, mnz = Infinity;
    for (let i = 0; i < n; i++) {
        const x = positions[i * 3], y = positions[i * 3 + 1], z = positions[i * 3 + 2];
        if (x < mnx) mnx = x; if (y < mny) mny = y; if (z < mnz) mnz = z;
    }
    const inv = 1 / cell;
    // Cell keys as exact integers in a Number; a Map keeps memory proportional to occupied cells.
    const SPAN = 1 << 20;
    const key = (ix, iy, iz) => (iz * SPAN + iy) * SPAN + ix;
    const map = new Map(), cellOf = new Int32Array(n);
    let ijk = new Int32Array(1 << 16), M = new Float64Array((1 << 16) * 11), nc = 0;
    for (let i = 0; i < n; i++) {
        const x = positions[i * 3], y = positions[i * 3 + 1], z = positions[i * 3 + 2];
        const ix = Math.floor((x - mnx) * inv), iy = Math.floor((y - mny) * inv), iz = Math.floor((z - mnz) * inv);
        const k = key(ix, iy, iz);
        let c = map.get(k);
        if (c === undefined) {
            c = nc++;
            map.set(k, c);
            if (c * 3 + 3 > ijk.length) {
                const grown = new Int32Array(ijk.length * 2); grown.set(ijk); ijk = grown;
                const gm = new Float64Array(M.length * 2); gm.set(M); M = gm;
            }
            ijk[c * 3] = ix; ijk[c * 3 + 1] = iy; ijk[c * 3 + 2] = iz;
        }
        cellOf[i] = c;
        // moments relative to the cloud's corner keep the sums well conditioned
        const px = x - mnx, py = y - mny, pz = z - mnz, o = c * 11;
        M[o] += 1; M[o + 1] += px; M[o + 2] += py; M[o + 3] += pz;
        M[o + 4] += px * px; M[o + 5] += px * py; M[o + 6] += px * pz;
        M[o + 7] += py * py; M[o + 8] += py * pz; M[o + 9] += pz * pz; M[o + 10] += intensity[i];
    }
    // per cell: block moments -> normal, surface variation, cell vs block intensity, spacing
    const cn = new Float32Array(nc * 3), cv = new Float32Array(nc), ct = new Float32Array(nc), cc_ = new Float32Array(nc), cs = new Float32Array(nc);
    const S = new Float64Array(11);
    for (let c = 0; c < nc; c++) {
        S.fill(0);
        const ix = ijk[c * 3], iy = ijk[c * 3 + 1], iz = ijk[c * 3 + 2];
        for (let dz = -1; dz <= 1; dz++) for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
            if (ix + dx < 0 || iy + dy < 0 || iz + dz < 0) continue;
            const q = map.get(key(ix + dx, iy + dy, iz + dz));
            if (q === undefined) continue;
            const o = q * 11;
            for (let k = 0; k < 11; k++) S[k] += M[o + k];
        }
        const w = S[0], mx = S[1] / w, my = S[2] / w, mz = S[3] / w;
        const a = S[4] / w - mx * mx, d = S[5] / w - mx * my, f = S[6] / w - mx * mz;
        const b = S[7] / w - my * my, e = S[8] / w - my * mz, cc = S[9] / w - mz * mz;
        const tr = a + b + cc;
        if (w >= 4 && tr > 1e-12) {
            const [l, nx, ny, nz] = smallestEigen(a, b, cc, d, e, f);
            cv[c] = Math.max(0, l) / tr;
            cn[c * 3] = nx; cn[c * 3 + 1] = ny; cn[c * 3 + 2] = nz;
        } else { cv[c] = 0; cn[c * 3 + 2] = 1; }
        ct[c] = S[10] / w;                                            // the block's mean intensity
        cc_[c] = Math.abs(M[c * 11 + 10] / M[c * 11] - ct[c]) / 255 * (M[c * 11] / (M[c * 11] + 6));   // the cell against its block; sparse cells count less
        // a surface crossing a block covers about (3 cells)^2: spacing ~ side / sqrt(count)
        cs[c] = (3 * cell) / Math.sqrt(w);
    }
    // thresholds from this scan's own distribution
    const cvPoint = new Float32Array(n), icPoint = new Float32Array(n);
    for (let i = 0; i < n; i++) {
        const c = cellOf[i];
        cvPoint[i] = Math.min(1, cv[c] * 3);                       // variation is 0..1/3
        icPoint[i] = cc_[c];
    }
    const P = POINT_FEATURE_EDGE_PERCENTILES;
    const c0 = percentile(cvPoint, n, P.curvature[0]), c1 = percentile(cvPoint, n, P.curvature[1]);
    const i0 = percentile(icPoint, n, P.contrast[0]), i1 = percentile(icPoint, n, P.contrast[1]);
    const ce = new Float32Array(nc);
    for (let c = 0; c < nc; c++) ce[c] = Math.max(smoothstep(c0, c1, Math.min(1, cv[c] * 3)), POINT_FEATURE_CONTRAST_WEIGHT * smoothstep(i0, i1, cc_[c]));
    const be = new Float32Array(nc);
    for (let c = 0; c < nc; c++) {
        const ix = ijk[c * 3], iy = ijk[c * 3 + 1], iz = ijk[c * 3 + 2];
        let se = 0, sw = 0;
        for (let dz = -1; dz <= 1; dz++) for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
            if (ix + dx < 0 || iy + dy < 0 || iz + dz < 0) continue;
            const q = map.get(key(ix + dx, iy + dy, iz + dz));
            if (q === undefined) continue;
            se += ce[q] * M[q * 11]; sw += M[q * 11];
        }
        be[c] = sw > 0 ? se / sw : ce[c];
    }
    for (let i = 0; i < n; i++) {
        const c = cellOf[i];
        edge[i] = Math.round(Math.min(1, 0.75 * ce[c] + 0.25 * be[c]) * 255);
        tone[i] = Math.round(ct[c]);
        normal[i * 3] = Math.round(cn[c * 3] * 127);
        normal[i * 3 + 1] = Math.round(cn[c * 3 + 1] * 127);
        normal[i * 3 + 2] = Math.round(cn[c * 3 + 2] * 127);
        spacing[i] = Math.max(1, Math.min(POINT_FEATURE_SPACING_MAX_MM, Math.round(cs[c] * 1000)));
    }
    return { normal, edge, tone, spacing, cells: nc, thresholds: { curvature: [c0, c1], contrast: [i0, i1] } };
}
