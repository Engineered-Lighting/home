/* home-3d/gaussian-splat-parse.mjs — Gaussian splat files to GPU-ready arrays.
 *
 * Pure module: no three.js, no DOM. gaussian-splat-worker.mjs runs it off the
 * main thread for the WebGPU splat pipeline (?renderer=webgpu), and the Node
 * tests run it directly. It reads the two formats Home packs ship:
 *
 *   PLY   binary little-endian 3DGS (x y z, f_dc_*, f_rest_* (0/9/24/45),
 *         opacity, scale_*, rot_* with rot_0 = w), as Scaniverse exports it;
 *   SPZ   Niantic's gzip format, versions 1-3 (v4 is zstd and refused).
 *
 * Output (all transferable, one entry per splat):
 *
 *   centers      Float32Array  3   splat-local position
 *   covariances  Float32Array  6   upper triangle of R S S^T: xx xy xz yy yz zz
 *   colors       Uint8Array    4   rgb = clamp(0.5 + SH_C0 * f_dc), a = sigmoid(opacity)
 *   sh1/sh2/sh3  Uint32Array   3/4/6 words, four bytes per word, byte = v*128+128
 *
 * The encodings follow three r186's GaussianSplatPLYLoader / SPZLoader /
 * GaussianSplatUtils (MIT, see vendor/three-0.186.1/LICENSE), so the forked
 * renderer in gaussian-splat-webgpu.mjs reads them unchanged. The SH bytes
 * are at least as fine as Spark's packing on every band (Spark: SH1 7 bit,
 * SH2 8 bit, SH3 6 bit; see shQuantizationReport below).
 */

export const SH_C0 = 0.28209479177387814;
export const SH_BAND_COMPONENTS = Object.freeze([0, 9, 15, 21]);
export const SH_BAND_WORDS = Object.freeze([0, 3, 4, 6]);
const REST_COMPONENTS = [0, 9, 24, 45];
const REST_BAND_OFFSET = [0, 0, 3, 8];
const SPZ_MAGIC = 0x5053474e;
const SPZ_HEADER_BYTES = 16;
const SPZ_COLOR_SCALE = SH_C0 / 0.15;
const SPZ_FLAG_LOD = 0x80;
const SPZ_SH_VECTORS = [0, 3, 8, 15, 24];
/** Hard bound: well past any Home pack (Victoria 370k, Echo 467k). */
export const MAX_SPLATS = 16_000_000;

const PLY_TYPES = {
    char: ['getInt8', 1], int8: ['getInt8', 1], uchar: ['getUint8', 1], uint8: ['getUint8', 1],
    short: ['getInt16', 2], int16: ['getInt16', 2], ushort: ['getUint16', 2], uint16: ['getUint16', 2],
    int: ['getInt32', 4], int32: ['getInt32', 4], uint: ['getUint32', 4], uint32: ['getUint32', 4],
    float: ['getFloat32', 4], float32: ['getFloat32', 4], double: ['getFloat64', 8], float64: ['getFloat64', 8],
};

const fail = (code) => { throw new Error(code); };
const clampByte = (value) => (value <= 0 ? 0 : value >= 255 ? 255 : Math.round(value));
const sigmoid = (value) => 1 / (1 + Math.exp(-value));

/** byte = round(v * 128 + 128) clamped to [0, 255]; decode is (byte - 128) / 128. */
export const encodeShByte = (value) => clampByte(value * 128 + 128);
export const decodeShByte = (byte) => (byte - 128) / 128;

/** Upper-triangle covariance of a Gaussian with physical scales and an xyzw quaternion. */
export function writeCovariance(target, offset, sx, sy, sz, qx, qy, qz, qw) {
    const norm = Math.hypot(qx, qy, qz, qw);
    if (!(norm > 1e-12) || !Number.isFinite(norm)) { qx = 0; qy = 0; qz = 0; qw = 1; }
    else { qx /= norm; qy /= norm; qz /= norm; qw /= norm; }
    const xx = qx * qx, yy = qy * qy, zz = qz * qz, xy = qx * qy, xz = qx * qz, yz = qy * qz;
    const wx = qw * qx, wy = qw * qy, wz = qw * qz;
    const m00 = (1 - 2 * (yy + zz)) * sx, m01 = 2 * (xy - wz) * sy, m02 = 2 * (xz + wy) * sz;
    const m10 = 2 * (xy + wz) * sx, m11 = (1 - 2 * (xx + zz)) * sy, m12 = 2 * (yz - wx) * sz;
    const m20 = 2 * (xz - wy) * sx, m21 = 2 * (yz + wx) * sy, m22 = (1 - 2 * (xx + yy)) * sz;
    target[offset] = m00 * m00 + m01 * m01 + m02 * m02;
    target[offset + 1] = m00 * m10 + m01 * m11 + m02 * m12;
    target[offset + 2] = m00 * m20 + m01 * m21 + m02 * m22;
    target[offset + 3] = m10 * m10 + m11 * m11 + m12 * m12;
    target[offset + 4] = m10 * m20 + m11 * m21 + m12 * m22;
    target[offset + 5] = m20 * m20 + m21 * m21 + m22 * m22;
}

function allocate(count, degree) {
    if (!Number.isSafeInteger(count) || count <= 0 || count > MAX_SPLATS) fail('splat_count_invalid');
    const data = { count, shDegree: degree, centers: new Float32Array(count * 3),
        covariances: new Float32Array(count * 6), colors: new Uint8Array(count * 4), sh1: null, sh2: null, sh3: null };
    for (let band = 1; band <= degree; band++) {
        data[`sh${band}`] = new Uint32Array(count * SH_BAND_WORDS[band]).fill(0x80808080);
    }
    return data;
}

/** Bounds of the drawn extent (2 sigma), for the sort's depth range. */
function finishBounds(data) {
    const { count, centers, covariances } = data;
    const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
    let finite = 0;
    for (let i = 0; i < count; i++) {
        const x = centers[i * 3], y = centers[i * 3 + 1], z = centers[i * 3 + 2];
        if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) continue;
        const c = i * 6;
        const r = 2 * Math.sqrt(Math.max(covariances[c], covariances[c + 3], covariances[c + 5], 0));
        if (!Number.isFinite(r)) continue;
        finite++;
        if (x - r < min[0]) min[0] = x - r; if (x + r > max[0]) max[0] = x + r;
        if (y - r < min[1]) min[1] = y - r; if (y + r > max[1]) max[1] = y + r;
        if (z - r < min[2]) min[2] = z - r; if (z + r > max[2]) max[2] = z + r;
    }
    if (!finite) fail('splat_bounds_empty');
    const center = [0, 1, 2].map((axis) => (min[axis] + max[axis]) / 2);
    data.bounds = { min, max, center, radius: Math.hypot(max[0] - center[0], max[1] - center[1], max[2] - center[2]) };
    return data;
}

// ---- PLY -------------------------------------------------------------------

export function parsePlyHeader(bytes) {
    const u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    const limit = Math.min(u8.length, 64 * 1024);
    let end = -1;
    for (let i = 0; i + 10 < limit; i++) {
        // "end_header\n"
        if (u8[i] === 0x65 && u8[i + 1] === 0x6e && u8[i + 2] === 0x64 && u8[i + 3] === 0x5f
            && u8[i + 4] === 0x68 && u8[i + 9] === 0x72 && (u8[i + 10] === 0x0a || u8[i + 10] === 0x0d)) {
            end = i + 10 + (u8[i + 10] === 0x0d && u8[i + 11] === 0x0a ? 2 : 1);
            break;
        }
    }
    if (end < 0) fail('splat_ply_header_invalid');
    const lines = new TextDecoder().decode(u8.subarray(0, end)).split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    if (lines[0] !== 'ply') fail('splat_ply_header_invalid');
    let format = null;
    const elements = [];
    for (const line of lines.slice(1)) {
        const parts = line.split(/\s+/);
        if (parts[0] === 'format') format = parts[1];
        else if (parts[0] === 'element') elements.push({ name: parts[1], count: Number(parts[2]), properties: [] });
        else if (parts[0] === 'property') {
            const element = elements[elements.length - 1];
            if (!element) fail('splat_ply_header_invalid');
            if (parts[1] === 'list') { element.list = true; continue; }
            const type = PLY_TYPES[parts[1]];
            if (!type) fail('splat_ply_property_unsupported');
            element.properties.push({ name: parts[2], reader: type[0], size: type[1] });
        }
    }
    if (format !== 'binary_little_endian') fail('splat_ply_format_unsupported');
    for (const element of elements) {
        if (!Number.isSafeInteger(element.count) || element.count < 0) fail('splat_ply_header_invalid');
        let offset = 0;
        for (const property of element.properties) { property.offset = offset; offset += property.size; }
        element.stride = offset;
    }
    return { headerBytes: end, elements };
}

/** Binary little-endian 3DGS PLY. Other elements before the vertices are skipped. */
export function parsePly(buffer) {
    const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
    const { headerBytes, elements } = parsePlyHeader(bytes);
    let offset = headerBytes;
    let vertex = null;
    for (const element of elements) {
        if (element.name === 'vertex') { vertex = element; break; }
        if (element.list) fail('splat_ply_layout_unsupported');
        offset += element.count * element.stride;
    }
    if (!vertex || vertex.list) fail('splat_ply_layout_unsupported');
    const property = Object.fromEntries(vertex.properties.map((item) => [item.name, item]));
    const required = ['x', 'y', 'z', 'f_dc_0', 'f_dc_1', 'f_dc_2', 'opacity',
        'scale_0', 'scale_1', 'scale_2', 'rot_0', 'rot_1', 'rot_2', 'rot_3'];
    if (!required.every((name) => property[name])) fail('splat_ply_not_gaussian');
    let restCount = 0;
    while (property[`f_rest_${restCount}`]) restCount++;
    const degree = REST_COMPONENTS.indexOf(restCount);
    if (degree < 0) fail('splat_ply_sh_unsupported');
    const count = vertex.count;
    if (offset + count * vertex.stride > bytes.byteLength) fail('splat_ply_truncated');
    const data = allocate(count, degree);
    const view = new DataView(bytes.buffer, bytes.byteOffset + offset, count * vertex.stride);
    const stride = vertex.stride;
    // Most packs are all float32: read those through one aligned view.
    const allFloat = vertex.properties.every((item) => item.reader === 'getFloat32')
        && (bytes.byteOffset + offset) % 4 === 0;
    const floats = allFloat ? new Float32Array(bytes.buffer, bytes.byteOffset + offset, count * stride / 4) : null;
    const reader = (name) => {
        const item = property[name];
        if (floats) { const word = item.offset / 4, row = stride / 4; return (i) => floats[i * row + word]; }
        const method = item.reader, at = item.offset;
        return (i) => view[method](i * stride + at, true);
    };
    const [x, y, z, d0, d1, d2, op, s0, s1, s2, r0, r1, r2, r3] = required.map(reader);
    const rest = Array.from({ length: restCount }, (_, k) => reader(`f_rest_${k}`));
    const restStride = restCount / 3;
    const bandBytes = [null, data.sh1, data.sh2, data.sh3].map((band) => band && new Uint8Array(band.buffer));
    const { centers, covariances, colors } = data;
    for (let i = 0; i < count; i++) {
        const i3 = i * 3, i4 = i * 4;
        centers[i3] = x(i); centers[i3 + 1] = y(i); centers[i3 + 2] = z(i);
        // GraphDECO/INRIA PLY: rot_0 = w, rot_1..3 = x y z.
        writeCovariance(covariances, i * 6, Math.exp(s0(i)), Math.exp(s1(i)), Math.exp(s2(i)), r1(i), r2(i), r3(i), r0(i));
        colors[i4] = clampByte((0.5 + SH_C0 * d0(i)) * 255);
        colors[i4 + 1] = clampByte((0.5 + SH_C0 * d1(i)) * 255);
        colors[i4 + 2] = clampByte((0.5 + SH_C0 * d2(i)) * 255);
        colors[i4 + 3] = clampByte(sigmoid(op(i)) * 255);
        for (let band = 1; band <= degree; band++) {
            const target = bandBytes[band], base = i * SH_BAND_WORDS[band] * 4;
            // f_rest is channel-major (all R, then G, then B); packed bytes are coefficient-major rgb.
            for (let j = 0; j < SH_BAND_COMPONENTS[band]; j++) {
                const coefficient = REST_BAND_OFFSET[band] + Math.floor(j / 3), channel = j % 3;
                target[base + j] = encodeShByte(rest[coefficient + channel * restStride](i));
            }
        }
    }
    data.source = 'ply';
    return finishBounds(data);
}

// ---- SPZ -------------------------------------------------------------------

export function isGzip(bytes) {
    const u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    return u8.length > 2 && u8[0] === 0x1f && u8[1] === 0x8b;
}

const SCALE_LUT = Float32Array.from({ length: 256 }, (_, i) => Math.exp(i / 16 - 10));
const COLOR_LUT = Uint8Array.from({ length: 256 }, (_, i) => clampByte(((i / 255 - 0.5) * SPZ_COLOR_SCALE + 0.5) * 255));
const QUAT_LUT = Float64Array.from({ length: 1024 }, (_, i) => {
    const value = Math.SQRT1_2 * ((i & 511) / 511);
    return (i & 512) ? -value : value;
});

function halfToFloat(bits) {
    const sign = bits & 0x8000 ? -1 : 1, exponent = (bits >> 10) & 0x1f, mantissa = bits & 0x3ff;
    if (exponent === 0) return sign * 2 ** -14 * (mantissa / 1024);
    if (exponent === 31) return mantissa ? NaN : sign * Infinity;
    return sign * 2 ** (exponent - 15) * (1 + mantissa / 1024);
}

/** Decompressed SPZ (v1-3) bytes. */
export function parseSpzRaw(buffer) {
    const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
    if (bytes.byteLength < SPZ_HEADER_BYTES) fail('splat_spz_invalid');
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    if (view.getUint32(0, true) !== SPZ_MAGIC) fail('splat_spz_invalid');
    const version = view.getUint32(4, true);
    if (version < 1 || version > 3) fail('splat_spz_version_unsupported');
    const count = view.getUint32(8, true);
    const storedDegree = view.getUint8(12), fractionalBits = view.getUint8(13), flags = view.getUint8(14);
    if (storedDegree >= SPZ_SH_VECTORS.length) fail('splat_spz_sh_unsupported');
    const degree = Math.min(storedDegree, 3);
    const positionsSize = count * 3 * (version === 1 ? 2 : 3);
    const rotationsSize = count * (version === 3 ? 4 : 3);
    const shSize = count * SPZ_SH_VECTORS[storedDegree] * 3;
    const lodSize = flags & SPZ_FLAG_LOD ? count * 6 : 0;
    if (bytes.byteLength !== SPZ_HEADER_BYTES + positionsSize + count * 7 + rotationsSize + shSize + lodSize) {
        fail('splat_spz_invalid');
    }
    const data = allocate(count, degree);
    let at = SPZ_HEADER_BYTES;
    const positions = bytes.subarray(at, at += positionsSize);
    const alphas = bytes.subarray(at, at += count);
    const colorBytes = bytes.subarray(at, at += count * 3);
    const scales = bytes.subarray(at, at += count * 3);
    const rotations = bytes.subarray(at, at += rotationsSize);
    const sh = bytes.subarray(at, at += shSize);
    const { centers, covariances, colors } = data;
    const fixed = 1 / (1 << fractionalBits);
    const q = [0, 0, 0, 0];
    for (let i = 0; i < count; i++) {
        const i3 = i * 3, i4 = i * 4;
        if (version === 1) {
            for (let axis = 0; axis < 3; axis++) {
                const o = (i3 + axis) * 2;
                centers[i3 + axis] = halfToFloat(positions[o] | (positions[o + 1] << 8));
            }
        } else {
            for (let axis = 0; axis < 3; axis++) {
                const o = i * 9 + axis * 3;
                centers[i3 + axis] = (((positions[o] << 8) | (positions[o + 1] << 16) | (positions[o + 2] << 24)) >> 8) * fixed;
            }
        }
        if (version === 3) {
            const o = i * 4;
            const packed = (rotations[o] | (rotations[o + 1] << 8) | (rotations[o + 2] << 16) | (rotations[o + 3] << 24)) >>> 0;
            const largest = packed >>> 30;
            const a = QUAT_LUT[packed & 1023], b = QUAT_LUT[(packed >>> 10) & 1023], c = QUAT_LUT[(packed >>> 20) & 1023];
            // The three small components fill the other indices in descending order.
            const others = [0, 1, 2, 3].filter((index) => index !== largest);
            q[others[0]] = c; q[others[1]] = b; q[others[2]] = a;
            q[largest] = Math.sqrt(Math.max(0, 1 - (a * a + b * b + c * c)));
        } else {
            q[0] = rotations[i3] / 127.5 - 1; q[1] = rotations[i3 + 1] / 127.5 - 1; q[2] = rotations[i3 + 2] / 127.5 - 1;
            q[3] = Math.sqrt(Math.max(0, 1 - q[0] * q[0] - q[1] * q[1] - q[2] * q[2]));
        }
        writeCovariance(covariances, i * 6, SCALE_LUT[scales[i3]], SCALE_LUT[scales[i3 + 1]], SCALE_LUT[scales[i3 + 2]],
            q[0], q[1], q[2], q[3]);
        colors[i4] = COLOR_LUT[colorBytes[i3]];
        colors[i4 + 1] = COLOR_LUT[colorBytes[i3 + 1]];
        colors[i4 + 2] = COLOR_LUT[colorBytes[i3 + 2]];
        colors[i4 + 3] = alphas[i];
    }
    // SPZ SH bytes already use (byte - 128) / 128, coefficient-major rgb.
    const sourceStride = SPZ_SH_VECTORS[storedDegree] * 3;
    for (let band = 1, read = 0; band <= degree; band++) {
        const target = new Uint8Array(data[`sh${band}`].buffer), stride = SH_BAND_WORDS[band] * 4;
        for (let i = 0; i < count; i++) {
            target.set(sh.subarray(i * sourceStride + read, i * sourceStride + read + SH_BAND_COMPONENTS[band]), i * stride);
        }
        read += SH_BAND_COMPONENTS[band];
    }
    data.source = 'spz';
    return finishBounds(data);
}

/** Sniffs the format. SPZ arrives gzip-compressed; `gunzip` returns the raw bytes. */
export async function parseSplatFile(buffer, { gunzip } = {}) {
    const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
    if (bytes.length > 3 && bytes[0] === 0x70 && bytes[1] === 0x6c && bytes[2] === 0x79) return parsePly(bytes);
    if (isGzip(bytes)) {
        if (typeof gunzip !== 'function') fail('splat_spz_gunzip_unavailable');
        return parseSpzRaw(await gunzip(bytes));
    }
    if (bytes.length >= 8 && new DataView(bytes.buffer, bytes.byteOffset, 8).getUint32(0, true) === SPZ_MAGIC) {
        // v4 is zstd-framed; v1-3 should arrive gzip-compressed.
        fail(new DataView(bytes.buffer, bytes.byteOffset, 8).getUint32(4, true) === 4
            ? 'splat_spz_version_unsupported' : 'splat_spz_invalid');
    }
    fail('splat_format_unsupported');
}

/** Transferable buffers of a parse result, for postMessage. */
export function splatTransferables(data) {
    return [data.centers, data.covariances, data.colors, data.sh1, data.sh2, data.sh3]
        .filter(Boolean).map((array) => array.buffer);
}

// ---- SH quantization against Spark -----------------------------------------

/*
 * Spark 2.1.0 packs SH per band into signed fixed point with max 1:
 * SH1 7 bit (x63), SH2 8 bit (x127), SH3 6 bit (x31), clamped to +-1
 * (encodeSh1Rgb / encodeSh2Rgb / encodeSh3Rgb in vendor/spark-2.1.0). The
 * WebGPU pipeline stores every band as one byte, (v * 128 + 128) clamped.
 */
export const SPARK_SH_STEPS = Object.freeze([0, 63, 127, 31]);
export const sparkShRoundTrip = (value, band) => {
    const steps = SPARK_SH_STEPS[band];
    return Math.round(Math.max(-steps, Math.min(steps, value * steps))) / steps;
};
export const homeShRoundTrip = (value) => decodeShByte(encodeShByte(value));

/**
 * Coefficient error of both packings over real f_rest values, per band.
 * `rest(i, k)` returns coefficient k (0..14) for splat i, channel-interleaved
 * as k * 3 + channel.
 */
export function shQuantizationReport({ count, degree, rest }) {
    const bands = [];
    for (let band = 1; band <= degree; band++) {
        const stats = { band, samples: 0, clipped: 0, home: { max: 0, sumSq: 0 }, spark: { max: 0, sumSq: 0 } };
        for (let i = 0; i < count; i++) {
            for (let j = 0; j < SH_BAND_COMPONENTS[band]; j++) {
                const value = rest(i, REST_BAND_OFFSET[band] * 3 + j);
                if (!Number.isFinite(value)) continue;
                stats.samples++;
                if (Math.abs(value) > 1) stats.clipped++;
                const home = Math.abs(homeShRoundTrip(value) - value), spark = Math.abs(sparkShRoundTrip(value, band) - value);
                stats.home.max = Math.max(stats.home.max, home); stats.home.sumSq += home * home;
                stats.spark.max = Math.max(stats.spark.max, spark); stats.spark.sumSq += spark * spark;
            }
        }
        for (const key of ['home', 'spark']) {
            stats[key].rms = stats.samples ? Math.sqrt(stats[key].sumSq / stats.samples) : 0;
            delete stats[key].sumSq;
        }
        bands.push(stats);
    }
    return bands;
}
