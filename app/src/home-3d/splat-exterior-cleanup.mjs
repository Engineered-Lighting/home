/* Presentation only. Hide wholly exterior rotated three-sigma XY support and
 * oversized detached centers beyond a small allowance around the FULL retained
 * registered display geometry. Height is unrestricted; scan bytes stay intact. */
export const SPLAT_EXTERIOR_CENTER_PADDING_M = 0.25;
const finite = (value) => typeof value === 'number' && Number.isFinite(value);
const vector = (value, count) => (Array.isArray(value) || ArrayBuffer.isView(value))
    && value.length === count && Array.from(value).every(finite);
const IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

/** Invalid or unavailable display bounds disable this optional cleanup. */
export function normalizeExteriorBounds(bounds) {
    if (!vector(bounds?.minXY, 2) || !vector(bounds?.maxXY, 2)
        || !bounds.minXY.every((value, axis) => value < bounds.maxXY[axis])) return null;
    return { minXY: Array.from(bounds.minXY), maxXY: Array.from(bounds.maxXY) };
}

function affine(matrix) {
    if (!vector(matrix, 16) || Math.abs(matrix[3]) > 1e-8
        || Math.abs(matrix[7]) > 1e-8 || Math.abs(matrix[11]) > 1e-8
        || Math.abs(matrix[15] - 1) > 1e-8) return false;
    const determinant = matrix[0] * (matrix[5] * matrix[10] - matrix[9] * matrix[6])
        - matrix[4] * (matrix[1] * matrix[10] - matrix[9] * matrix[2])
        + matrix[8] * (matrix[1] * matrix[6] - matrix[5] * matrix[2]);
    return finite(determinant) && Math.abs(determinant) >= 1e-12;
}

/** CPU counterpart of the shader; Spark uses physical scales and xyzw rotation.
 * The input Gaussian is world-space, bounds are apartment-local, and matrix is
 * THREE's column-major world-to-apartment inverse. Invalid evidence retains. */
export function splatExteriorKeep(splat, bounds, matrix = IDENTITY) {
    const valid = normalizeExteriorBounds(bounds);
    if (!valid || !vector(splat?.center, 3) || !vector(splat?.scales, 3)
        || !splat.scales.every((value) => value >= 0)
        || !vector(splat?.quaternion, 4) || !affine(matrix)) return 1;
    const norm = Math.hypot(...splat.quaternion);
    if (!finite(norm) || norm <= 1e-12) return 1;
    const [x, y, z, w] = Array.from(splat.quaternion, (value) => value / norm);
    const [sx, sy, sz] = splat.scales;
    const basis = [
        [sx * (1 - 2 * (y * y + z * z)), sx * 2 * (x * y + w * z), sx * 2 * (x * z - w * y)],
        [sy * 2 * (x * y - w * z), sy * (1 - 2 * (x * x + z * z)), sy * 2 * (y * z + w * x)],
        [sz * 2 * (x * z + w * y), sz * 2 * (y * z - w * x), sz * (1 - 2 * (x * x + y * y))],
    ];
    const intervals = [];
    for (let axis = 0; axis < 2; axis++) {
        const dot = (column) => matrix[axis] * column[0]
            + matrix[axis + 4] * column[1] + matrix[axis + 8] * column[2];
        const center = dot(splat.center) + matrix[axis + 12];
        const radius = 3 * Math.hypot(...basis.map(dot));
        if (!finite(center) || !finite(radius)) return 1;
        intervals.push([center - radius, center + radius, center]);
    }
    return intervals.some(([low, high, center], axis) => high < valid.minXY[axis] || low > valid.maxXY[axis]
        || center < valid.minXY[axis] - SPLAT_EXTERIOR_CENTER_PADDING_M
        || center > valid.maxXY[axis] + SPLAT_EXTERIOR_CENTER_PADDING_M) ? 0 : 1;
}

export const SPLAT_EXTERIOR_CLEANUP_GLSL = /* glsl */ `
vec3 homeExteriorRotate(vec4 q, vec3 v) {
    vec3 t = 2.0 * cross(q.xyz, v);
    return v + q.w * t + cross(q.xyz, t);
}
float homeSplatExteriorKeep(vec3 position, vec3 scales, vec4 quaternion,
                            vec4 rowX, vec4 rowY, vec2 minXY, vec2 maxXY) {
    if (any(isnan(position)) || any(isinf(position))
        || any(isnan(scales)) || any(isinf(scales)) || any(lessThan(scales, vec3(0.0)))
        || any(isnan(quaternion)) || any(isinf(quaternion))) return 1.0;
    float qLengthSquared = dot(quaternion, quaternion);
    if (isnan(qLengthSquared) || isinf(qLengthSquared) || qLengthSquared <= 1e-24) return 1.0;
    vec4 q = quaternion * inversesqrt(qLengthSquared);
    vec3 bx = homeExteriorRotate(q, vec3(scales.x, 0.0, 0.0));
    vec3 by = homeExteriorRotate(q, vec3(0.0, scales.y, 0.0));
    vec3 bz = homeExteriorRotate(q, vec3(0.0, 0.0, scales.z));
    vec2 center = vec2(dot(rowX, vec4(position, 1.0)), dot(rowY, vec4(position, 1.0)));
    vec2 radius = 3.0 * vec2(
        length(vec3(dot(rowX.xyz, bx), dot(rowX.xyz, by), dot(rowX.xyz, bz))),
        length(vec3(dot(rowY.xyz, bx), dot(rowY.xyz, by), dot(rowY.xyz, bz))));
    if (any(isnan(center)) || any(isinf(center)) || any(isnan(radius)) || any(isinf(radius))) return 1.0;
    bool supportOutside = any(lessThan(center + radius, minXY)) || any(greaterThan(center - radius, maxXY));
    bool centerOutside = any(lessThan(center, minXY - vec2(${SPLAT_EXTERIOR_CENTER_PADDING_M})))
                      || any(greaterThan(center, maxXY + vec2(${SPLAT_EXTERIOR_CENTER_PADDING_M})));
    return supportOutside || centerOutside ? 0.0 : 1.0;
}
`;
