/* home-3d/point-features.mjs — per-point values for the x-ray Cloud look.
 *
 * A homeapt-points v1 cloud holds only position and intensity. This derives,
 * once per load and off the main thread (point-features-worker.mjs):
 *
 *   normal   the surface direction (PCA of the neighbourhood): x-ray spreads
 *            each point's copies along its surface, and the dollhouse cut
 *            needs to know which way a wall faces
 *   spacing  the local sample spacing, so a point covers about the patch of
 *            surface it stands for when the camera is close
 *   inside   for wall points, which side of the wall the room is on (+1 along
 *            the normal, -1 against it, 0 unknown): the side with floor
 *            beside it. The dollhouse cut drops a wall seen from outside.
 *   floorZ   the floor height: the commonest height of up-facing points
 *
 * Neighbourhoods are 3x3x3 blocks of cubic cells. Moments are summed per cell
 * once, then per block, so the cost is linear in points plus occupied cells
 * rather than a radius search per point. A scan's surface noise is about as
 * large as its corners, so a block (about 10 cm across) is wide enough to
 * average it away. Pure: no DOM, no three.js, so Node tests run it directly.
 */

export const POINT_FEATURE_CELL_M = 0.035;
/* spacing is stored as millimetres in a byte */
export const POINT_FEATURE_SPACING_MAX_MM = 255;
/* Floor plan: a 10 cm grid of where the floor is; a wall point probes 18-50 cm to each side. */
export const FLOOR_PLAN_CELL_M = 0.1;
const FLOOR_PROBES_M = [0.18, 0.32, 0.5];

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
 * positions: Float32Array (3 per point, apartment frame, Z up), intensity: Uint8Array, count: points to use.
 * Returns { normal: Int8Array(3n), spacing: Uint8Array(n) (mm), inside: Int8Array(n), floorZ, cells }.
 */
export function computePointFeatures(positions, intensity, count, { cell = POINT_FEATURE_CELL_M } = {}) {
    const n = Math.max(0, Math.min(count | 0, Math.floor(positions.length / 3), intensity.length));
    const normal = new Int8Array(n * 3), spacing = new Uint8Array(n), inside = new Int8Array(n);
    if (!n) return { normal, spacing, inside, floorZ: 0, cells: 0 };
    let mnx = Infinity, mny = Infinity, mnz = Infinity, mxx = -Infinity, mxy = -Infinity;
    for (let i = 0; i < n; i++) {
        const x = positions[i * 3], y = positions[i * 3 + 1], z = positions[i * 3 + 2];
        if (x < mnx) mnx = x; if (y < mny) mny = y; if (z < mnz) mnz = z; if (x > mxx) mxx = x; if (y > mxy) mxy = y;
    }
    const inv = 1 / cell;
    // Cell keys as exact integers in a Number; a Map keeps memory proportional to occupied cells.
    const SPAN = 1 << 20;
    const key = (ix, iy, iz) => (iz * SPAN + iy) * SPAN + ix;
    const map = new Map(), cellOf = new Int32Array(n);
    let ijk = new Int32Array(1 << 16), M = new Float64Array((1 << 16) * 10), nc = 0;
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
        const px = x - mnx, py = y - mny, pz = z - mnz, o = c * 10;
        M[o] += 1; M[o + 1] += px; M[o + 2] += py; M[o + 3] += pz;
        M[o + 4] += px * px; M[o + 5] += px * py; M[o + 6] += px * pz;
        M[o + 7] += py * py; M[o + 8] += py * pz; M[o + 9] += pz * pz;
    }
    // per cell: block moments -> normal and spacing
    const cn = new Float32Array(nc * 3), cs = new Float32Array(nc);
    const S = new Float64Array(10);
    for (let c = 0; c < nc; c++) {
        S.fill(0);
        const ix = ijk[c * 3], iy = ijk[c * 3 + 1], iz = ijk[c * 3 + 2];
        for (let dz = -1; dz <= 1; dz++) for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
            if (ix + dx < 0 || iy + dy < 0 || iz + dz < 0) continue;
            const q = map.get(key(ix + dx, iy + dy, iz + dz));
            if (q === undefined) continue;
            const o = q * 10;
            for (let k = 0; k < 10; k++) S[k] += M[o + k];
        }
        const w = S[0], mx = S[1] / w, my = S[2] / w, mz = S[3] / w;
        const a = S[4] / w - mx * mx, d = S[5] / w - mx * my, f = S[6] / w - mx * mz;
        const b = S[7] / w - my * my, e = S[8] / w - my * mz, cc = S[9] / w - mz * mz;
        if (w >= 4 && a + b + cc > 1e-12) {
            const [, nx, ny, nz] = smallestEigen(a, b, cc, d, e, f);
            cn[c * 3] = nx; cn[c * 3 + 1] = ny; cn[c * 3 + 2] = nz;
        } else cn[c * 3 + 2] = 1;
        // a surface crossing a block covers about (3 cells)^2: spacing ~ side / sqrt(count)
        cs[c] = (3 * cell) / Math.sqrt(w);
    }
    for (let i = 0; i < n; i++) {
        const c = cellOf[i];
        normal[i * 3] = Math.round(cn[c * 3] * 127);
        normal[i * 3 + 1] = Math.round(cn[c * 3 + 1] * 127);
        normal[i * 3 + 2] = Math.round(cn[c * 3 + 2] * 127);
        spacing[i] = Math.max(1, Math.min(POINT_FEATURE_SPACING_MAX_MM, Math.round(cs[c] * 1000)));
    }

    // Floor: the commonest 2 cm height band of up-facing points.
    const hist = new Uint32Array(400);
    for (let i = 0; i < n; i++) if (Math.abs(normal[i * 3 + 2]) > 117) {
        const bin = Math.floor((positions[i * 3 + 2] - mnz) / 0.02); if (bin >= 0 && bin < 400) hist[bin]++;
    }
    let fb = 0; for (let bin = 1; bin < 400; bin++) if (hist[bin] > hist[fb]) fb = bin;
    const floorZ = mnz + (fb + 0.5) * 0.02;
    // Floor plan: 10 cm cells with floor points in them, closing one-cell gaps.
    const G = FLOOR_PLAN_CELL_M, GX = Math.ceil((mxx - mnx) / G) + 2, GY = Math.ceil((mxy - mny) / G) + 2;
    const plan = new Uint8Array(GX * GY);
    for (let i = 0; i < n; i++) {
        if (Math.abs(positions[i * 3 + 2] - floorZ) < 0.06 && Math.abs(normal[i * 3 + 2]) > 110) {
            plan[Math.floor((positions[i * 3] - mnx) / G) + GX * Math.floor((positions[i * 3 + 1] - mny) / G)] = 1;
        }
    }
    const filled = plan.slice();
    for (let y = 1; y < GY - 1; y++) for (let x = 1; x < GX - 1; x++) {
        if (!plan[x + GX * y] && plan[x - 1 + GX * y] + plan[x + 1 + GX * y] + plan[x + GX * (y - 1)] + plan[x + GX * (y + 1)] >= 2) filled[x + GX * y] = 1;
    }
    const floorAt = (x, y) => {
        const gx = Math.floor((x - mnx) / G), gy = Math.floor((y - mny) / G);
        return gx >= 0 && gy >= 0 && gx < GX && gy < GY && filled[gx + GX * gy] === 1 ? 1 : 0;
    };
    // A wall's inside is the side with floor beside it; a partition has floor on both sides and stays 0.
    for (let i = 0; i < n; i++) {
        const nx = normal[i * 3] / 127, ny = normal[i * 3 + 1] / 127, nz = normal[i * 3 + 2] / 127;
        if (Math.abs(nz) > 0.4) continue;
        const hl = Math.hypot(nx, ny) || 1, hx = nx / hl, hy = ny / hl, x = positions[i * 3], y = positions[i * 3 + 1];
        let fp = 0, fm = 0;
        for (const d of FLOOR_PROBES_M) { fp += floorAt(x + hx * d, y + hy * d); fm += floorAt(x - hx * d, y - hy * d); }
        if (fp >= 2 && fm === 0) inside[i] = 127; else if (fm >= 2 && fp === 0) inside[i] = -127;
    }
    return { normal, spacing, inside, floorZ, cells: nc };
}
