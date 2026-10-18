/* Display-only room shell policy. Positions and bounds share the registered
 * Z-up apartment frame; collision and registration never consume this policy. */
const THRESHOLD = 0.78;
const FEATHER = 0.03;
const FLOOR_GUARD = 0.25;
const UPPER_RELAXATION = 0.06;
const UPPER_START_M = 1.30;
const UPPER_END_M = 1.55;
const FACING_EXPONENT = 0.25;
const finite = (value) => typeof value === 'number' && Number.isFinite(value);
const vector3 = (value) => Array.isArray(value) && value.length === 3 && value.every(finite);
const vector2 = (value) => Array.isArray(value) && value.length === 2 && value.every(finite);
const SHELL_INSET_M = 0.12;
const SHELL_FEATHER_M = 0.03;
const BINDING_KEYS = ['content_revision', 'registration_version', 'manifest_digest', 'frame_digest', 'canonical_collision_digest'];
const sha = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);

/** Presentation-only adoption of already authenticated, binding-checked survey
 * references. The caller still verifies the authoritative document digest. */
export function deriveRegisteredDollhouseShell(document, expectedBinding) {
    const refs = document?.references, binding = document?.binding, scope = document?.scope;
    if (document?.schema !== 'home-equipment-registration-1' || document.version !== 1
        || !scope || Object.keys(scope).length !== 3 || scope.site_id !== 'victoria'
        || scope.map_id !== 'victoria-den' || scope.room_id !== 'den'
        || !binding || !expectedBinding || Object.keys(binding).length !== BINDING_KEYS.length
        || Object.keys(expectedBinding).length !== BINDING_KEYS.length
        || !BINDING_KEYS.every(key => Object.hasOwn(binding, key) && Object.hasOwn(expectedBinding, key)
            && binding[key] === expectedBinding[key])
        || typeof binding.content_revision !== 'string' || !binding.content_revision
        || !Number.isSafeInteger(binding.registration_version) || binding.registration_version < 0
        || !['manifest_digest', 'frame_digest', 'canonical_collision_digest'].every(key => sha(binding[key]))
        || refs?.confirmed !== true || refs.frame !== 'z_up_metric_floor0' || refs.units !== 'm'
        || !vector3(refs.floor?.normal) || Math.abs(Math.hypot(...refs.floor.normal)-1) > 1e-8
        || refs.floor.normal[2] < .99
        || !finite(refs.floor.offset_m) || !Array.isArray(refs.walls) || refs.walls.length !== 4
        || refs.walls.some(wall => !vector2(wall.normal_xy) || Math.abs(Math.hypot(...wall.normal_xy) - 1) > 1e-8
            || !finite(wall.offset_m))) return null;
    const walls = refs.walls.map(wall => ({ n: wall.normal_xy.slice(), d: wall.offset_m }));
    const axis = walls[0].n.slice(), perpendicular = [-axis[1], axis[0]];
    const projected = p => [axis[0]*p[0] + axis[1]*p[1], perpendicular[0]*p[0] + perpendicular[1]*p[1]];
    const sides = new Set();
    for (const wall of walls) {
        const n = projected(wall.n), coordinate = Math.abs(n[0]) > Math.abs(n[1]) ? 0 : 1;
        if (Math.abs(n[coordinate]) < .999) return null;
        sides.add(coordinate + ':' + Math.sign(n[coordinate]));
    }
    if (sides.size !== 4) return null;
    const corners = [];
    for (let i = 0; i < walls.length; i++) for (let j = i + 1; j < walls.length; j++) {
        const a = walls[i], b = walls[j], determinant = a.n[0]*b.n[1] - a.n[1]*b.n[0];
        if (Math.abs(determinant) < 1e-6) continue;
        const point = [(a.d*b.n[1] - a.n[1]*b.d)/determinant, (a.n[0]*b.d - a.d*b.n[0])/determinant];
        if (!vector2(point) || walls.some(wall => wall.n[0]*point[0] + wall.n[1]*point[1] < wall.d - 1e-6)) continue;
        if (!corners.some(other => Math.hypot(other[0]-point[0], other[1]-point[1]) < 1e-6)) corners.push(point);
    }
    if (corners.length !== 4) return null;
    const values = corners.map(projected), low = [0, 1].map(i => Math.min(...values.map(p => p[i])));
    const high = [0, 1].map(i => Math.max(...values.map(p => p[i])));
    const halfExtentXY = high.map((v, i) => (v-low[i])/2), center = high.map((v, i) => (v+low[i])/2);
    if (halfExtentXY.some(value => value <= SHELL_INSET_M + SHELL_FEATHER_M || value > 50)) return null;
    // Permit the small surveyed departures from a rectangle, never a guessed
    // skewed-room fit: each corner must be within 3 cm of both box edges.
    if (values.some(p => p.some((v, i) => Math.min(Math.abs(v-low[i]), Math.abs(v-high[i])) > .03))) return null;
    const centerXY = [axis[0]*center[0] + perpendicular[0]*center[1], axis[1]*center[0] + perpendicular[1]*center[1]];
    const [fx, fy, fz] = refs.floor.normal;
    const floorHeights = corners.map(p => (refs.floor.offset_m-fx*p[0]-fy*p[1])/fz);
    if (!floorHeights.every(finite) || Math.max(...floorHeights)-Math.min(...floorHeights) > .25) return null;
    // The scalar display guard is conservative for a slightly tilted survey
    // floor: protect a quarter metre above its highest registered corner.
    return Object.freeze({ axis: Object.freeze(axis), centerXY: Object.freeze(centerXY),
        halfExtentXY: Object.freeze(halfExtentXY), floorZ: Math.max(...floorHeights),
        insetM: SHELL_INSET_M, featherM: SHELL_FEATHER_M });
}

function validMetricShell(shell) {
    return vector2(shell?.axis) && Math.abs(Math.hypot(...shell.axis)-1) <= 1e-8
        && finite(shell.insetM) && shell.insetM > 0 && shell.insetM <= 1
        && finite(shell.featherM) && shell.featherM > 0 && shell.featherM < shell.insetM;
}

/** One shared optional uniform: a zero inset selects the existing fallback. */
export function dollhouseMetricShellVector(policy) {
    const shell = policy?.metricShell;
    return validMetricShell(shell) ? [...shell.axis, shell.insetM, shell.featherM] : [1, 0, 0, 0];
}

function disabledPolicy() {
    return { enabled: false, center: [0, 0, 0], halfExtent: [1, 1, 1],
        direction: [0, 0], threshold: THRESHOLD, feather: FEATHER, floorZ: FLOOR_GUARD };
}

export function deriveDollhouseCutaway({ bounds, cameraLocal, enabled = true,
    heldCamera = false, live = false, calibrating = false, floorZ = 0, roomShell = null } = {}) {
    if (enabled !== true || heldCamera || live || calibrating || !finite(floorZ)
        || !vector3(bounds?.min) || !vector3(bounds?.max) || !vector3(cameraLocal)) return disabledPolicy();
    const halfExtent = bounds.max.map((value, axis) => (value - bounds.min[axis]) / 2);
    if (!halfExtent.every((value) => finite(value) && value > 0)) return disabledPolicy();
    const center = bounds.min.map((value, axis) => value + halfExtent[axis]);
    if (validMetricShell(roomShell) && vector2(roomShell.centerXY) && vector2(roomShell.halfExtentXY)
        && roomShell.halfExtentXY.every(value => value > roomShell.insetM + roomShell.featherM && value <= 50)
        && finite(roomShell.floorZ)) {
        const [ax, ay] = roomShell.axis, dx = cameraLocal[0]-roomShell.centerXY[0], dy = cameraLocal[1]-roomShell.centerXY[1];
        const cameraXY = [ax*dx + ay*dy, -ay*dx + ax*dy], magnitude = Math.max(...cameraXY.map(Math.abs));
        if (!finite(magnitude) || magnitude <= 1e-8) return disabledPolicy();
        const direction = cameraXY.map(value => Math.sign(value)*smoothstep(.15, .4, Math.abs(value)/magnitude));
        return { enabled: true, center: [...roomShell.centerXY, center[2]], halfExtent: [...roomShell.halfExtentXY, halfExtent[2]],
            direction, threshold: THRESHOLD, feather: FEATHER, floorZ: roomShell.floorZ + FLOOR_GUARD,
            metricShell: { axis: roomShell.axis.slice(), insetM: roomShell.insetM, featherM: roomShell.featherM } };
    }
    const dx = cameraLocal[0] - center[0], dy = cameraLocal[1] - center[1];
    const maxAbs = Math.max(Math.abs(dx), Math.abs(dy));
    if (!finite(maxAbs) || maxAbs <= 1e-8 || !center.every(finite)) return disabledPolicy();
    // A corner view sees both front walls. Linear secondary-axis weighting
    // left the oblique wall opaque until almost exactly the diagonal. Keep
    // signs/max magnitude while broadening that response continuously.
    const direction = [dx / maxAbs, dy / maxAbs].map(value => Math.sign(value) * Math.pow(Math.abs(value), FACING_EXPONENT));
    return { enabled: true, center, halfExtent, direction,
        threshold: THRESHOLD, feather: FEATHER, floorZ: floorZ + FLOOR_GUARD };
}

function smoothstep(low, high, value) {
    const t = Math.min(1, Math.max(0, (value - low) / (high - low)));
    return t * t * (3 - 2 * t);
}

/** Exact CPU counterpart of the common shader function; 0 discards, 1 retains. */
export function dollhouseKeep(positionLocal, policy) {
    if (!policy?.enabled || !vector3(positionLocal) || !vector3(policy.center)
        || !vector3(policy.halfExtent) || !policy.halfExtent.every((value) => value > 0)
        || !Array.isArray(policy.direction) || policy.direction.length !== 2 || !policy.direction.every(finite)
        || !finite(policy.threshold) || !finite(policy.feather) || policy.feather <= 0 || !finite(policy.floorZ)) return 1;
    if (policy.metricShell != null) {
        if (!validMetricShell(policy.metricShell)) return 1;
        const shell = policy.metricShell, [ax, ay] = shell.axis;
        const dx = positionLocal[0]-policy.center[0], dy = positionLocal[1]-policy.center[1];
        const projected = [ax*dx + ay*dy, -ay*dx + ax*dy];
        const cut = Math.max(...projected.map((value, i) => (1-smoothstep(shell.insetM-shell.featherM, shell.insetM,
            policy.halfExtent[i]-Math.sign(policy.direction[i])*value))*Math.abs(policy.direction[i])));
        return 1-cut*smoothstep(policy.floorZ, policy.floorZ+FEATHER, positionLocal[2]);
    }
    const front = Math.max(
        policy.direction[0] * (positionLocal[0] - policy.center[0]) / policy.halfExtent[0],
        policy.direction[1] * (positionLocal[1] - policy.center[1]) / policy.halfExtent[1],
    );
    // Scan bounds can include padding beyond the actual wall. Reach farther
    // into the upper shell, without cutting the lower furniture band deeper.
    // floorZ already includes the protected quarter-metre floor guard.
    const aboveFloor = positionLocal[2] - (policy.floorZ - FLOOR_GUARD);
    const threshold = policy.threshold - UPPER_RELAXATION * smoothstep(UPPER_START_M, UPPER_END_M, aboveFloor);
    const cut = smoothstep(threshold, threshold + policy.feather, front)
        * smoothstep(policy.floorZ, policy.floorZ + FEATHER, positionLocal[2]);
    return 1 - cut;
}

/* The caller gates this function with policy.enabled. Its coordinates must be
 * apartment-local after all mesh node transforms, matching the CPU seam above. */
export const DOLLHOUSE_CUTAWAY_GLSL = /* glsl */ `
float dollhouseKeep(vec3 positionLocal, vec3 center, vec3 halfExtent,
                    vec2 direction, float threshold, float feather, float floorZ) {
    vec2 normalizedXY = (positionLocal.xy - center.xy) / halfExtent.xy;
    float front = max(direction.x * normalizedXY.x, direction.y * normalizedXY.y);
    float aboveFloor = positionLocal.z - (floorZ - ${FLOOR_GUARD.toFixed(2)});
    float shellThreshold = threshold - ${UPPER_RELAXATION.toFixed(2)}
                         * smoothstep(${UPPER_START_M.toFixed(2)}, ${UPPER_END_M.toFixed(2)}, aboveFloor);
    float cut = smoothstep(shellThreshold, shellThreshold + feather, front)
              * smoothstep(floorZ, floorZ + 0.03, positionLocal.z);
    return 1.0 - cut;
}
float dollhouseMetricKeep(vec3 positionLocal, vec3 center, vec3 halfExtent,
                         vec2 direction, float threshold, float feather, float floorZ,
                         vec4 metricShell) {
    if (metricShell.z <= 0.0) return dollhouseKeep(positionLocal, center, halfExtent,
                                                direction, threshold, feather, floorZ);
    vec2 delta = positionLocal.xy - center.xy;
    vec2 projected = vec2(dot(delta, metricShell.xy), dot(delta, vec2(-metricShell.y, metricShell.x)));
    vec2 inside = halfExtent.xy - sign(direction) * projected;
    vec2 cut = (vec2(1.0) - smoothstep(vec2(metricShell.z - metricShell.w), vec2(metricShell.z), inside)) * abs(direction);
    return 1.0 - max(cut.x, cut.y) * smoothstep(floorZ, floorZ + 0.03, positionLocal.z);
}
`;
