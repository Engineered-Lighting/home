/* home-3d/gaussian-splat-sort-keys.mjs — the radial sort keys and a CPU radix reference.
 *
 * Pure module (no three.js). gaussian-splat-radix-sort.mjs encodes the same
 * keys on the GPU; the tests check its order against radixSortIndices().
 */

/** Stable ascending order of `keys` (Uint32Array): an LSD radix sort, 8 bits a pass. */
export function radixSortIndices(keys, keyBits = 32, out = new Uint32Array(keys.length)) {
    const n = keys.length;
    let srcKeys = Uint32Array.from(keys), srcVals = new Uint32Array(n);
    for (let i = 0; i < n; i++) srcVals[i] = i;
    let dstKeys = new Uint32Array(n), dstVals = new Uint32Array(n);
    const counts = new Uint32Array(256);
    for (let shift = 0; shift < keyBits; shift += 8) {
        counts.fill(0);
        for (let i = 0; i < n; i++) counts[(srcKeys[i] >>> shift) & 255]++;
        for (let d = 0, sum = 0; d < 256; d++) { const c = counts[d]; counts[d] = sum; sum += c; }
        for (let i = 0; i < n; i++) {
            const at = counts[(srcKeys[i] >>> shift) & 255]++;
            dstKeys[at] = srcKeys[i]; dstVals[at] = srcVals[i];
        }
        [srcKeys, dstKeys] = [dstKeys, srcKeys];
        [srcVals, dstVals] = [dstVals, srcVals];
    }
    out.set(srcVals);
    return out;
}

/** The 16-bit radial key: farthest first, `near`..`far` metres over 0..65535. */
export function radialKey16(distance, near, far) {
    const t = Math.min(1, Math.max(0, (distance - near) / Math.max(far - near, 1e-4)));
    return 65535 - Math.floor(t * 65535);
}

const _float = new Float32Array(1), _bits = new Uint32Array(_float.buffer);
/** The 32-bit radial key: the float's bits inverted (positive floats order as uints). */
export function radialKey32(distance) {
    _float[0] = Math.max(0, distance);
    return (~_bits[0]) >>> 0;
}
