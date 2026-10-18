/* Runtime-only Spark opacity modifier. The caller owns the idle update lease. */
const { DOLLHOUSE_CUTAWAY_GLSL, dollhouseMetricShellVector } = await import(`./dollhouse-cutaway.mjs${new URL(import.meta.url).search}`);
const { normalizeExteriorBounds, SPLAT_EXTERIOR_CLEANUP_GLSL } =
    await import(`./splat-exterior-cleanup.mjs${new URL(import.meta.url).search}`);

let sparkModule;
const loadSpark = () => (sparkModule ??= import('@sparkjsdev/spark'));
const finite = (value) => typeof value === 'number' && Number.isFinite(value);
const vector = (value, length) => Array.isArray(value) && value.length === length && value.every(finite);

function validatedSnapshot(policy, matrix) {
    if (!policy || typeof policy.enabled !== 'boolean'
        || !vector(policy.center, 3) || !vector(policy.halfExtent, 3)
        || !policy.halfExtent.every((value) => value > 0)
        || !vector(policy.direction, 2) || !finite(policy.threshold)
        || !finite(policy.feather) || policy.feather <= 0 || !finite(policy.floorZ)) {
        throw new Error('splat_cutaway_policy_invalid');
    }
    const directionMagnitude = Math.max(...policy.direction.map(Math.abs));
    if (policy.enabled && Math.abs(directionMagnitude - 1) > 1e-6) {
        throw new Error('splat_cutaway_direction_invalid');
    }
    const metricShell = dollhouseMetricShellVector(policy);
    if (policy.metricShell != null && metricShell[2] <= 0) throw new Error('splat_cutaway_metric_shell_invalid');
    if ((!Array.isArray(matrix) && !ArrayBuffer.isView(matrix)) || matrix.length !== 16
        || !Array.from(matrix).every(finite) || Math.abs(matrix[3]) > 1e-8
        || Math.abs(matrix[7]) > 1e-8 || Math.abs(matrix[11]) > 1e-8
        || Math.abs(matrix[15] - 1) > 1e-8) {
        throw new Error('splat_cutaway_transform_invalid');
    }
    const determinant = matrix[0] * (matrix[5] * matrix[10] - matrix[9] * matrix[6])
        - matrix[4] * (matrix[1] * matrix[10] - matrix[9] * matrix[2])
        + matrix[8] * (matrix[1] * matrix[6] - matrix[5] * matrix[2]);
    if (!finite(determinant) || Math.abs(determinant) < 1e-12) {
        throw new Error('splat_cutaway_transform_invalid');
    }
    return [policy.enabled, ...policy.center, ...policy.halfExtent, ...policy.direction,
        policy.threshold, policy.feather, policy.floorZ, ...metricShell, ...matrix];
}

/**
 * Attach one graph to a real SplatMesh. Await this during owned asset loading.
 * update() accepts THREE column-major apartmentRoot.matrixWorld inverse elements.
 * Both update() and dispose() must run outside an in-flight Spark update.
 */
export async function createSplatCutawayModifier(splat) {
    if (!splat || typeof splat.updateGenerator !== 'function' || splat.covSplats) {
        throw new Error('splat_cutaway_mesh_unsupported');
    }
    const { dyno } = await loadSpark();
    const enabled = dyno.dynoBool(false, 'cutawayEnabled');
    const center = dyno.dynoVec3([0, 0, 0], 'cutawayCenter');
    const halfXY = dyno.dynoVec2([1, 1], 'cutawayHalfXY');
    const direction = dyno.dynoVec2([0, 0], 'cutawayDirection');
    const threshold = dyno.dynoFloat(.8, 'cutawayThreshold');
    const feather = dyno.dynoFloat(.03, 'cutawayFeather');
    const floorZ = dyno.dynoFloat(.25, 'cutawayFloorZ');
    const metricShell = dyno.dynoVec4([1, 0, 0, 0], 'cutawayMetricShell');
    const exteriorEnabled = dyno.dynoBool(false, 'exteriorEnabled');
    const exteriorMinXY = dyno.dynoVec2([0, 0], 'exteriorMinXY');
    const exteriorMaxXY = dyno.dynoVec2([1, 1], 'exteriorMaxXY');
    const rowX = dyno.dynoVec4([1, 0, 0, 0], 'cutawayRowX');
    const rowY = dyno.dynoVec4([0, 1, 0, 0], 'cutawayRowY');
    const rowZ = dyno.dynoVec4([0, 0, 1, 0], 'cutawayRowZ');
    const graph = dyno.dynoBlock({ gsplat: dyno.Gsplat }, { gsplat: dyno.Gsplat }, ({ gsplat }) => {
        const { center: position, scales, quaternion, opacity } = dyno.splitGsplat(gsplat).outputs;
        const keep = new dyno.Dyno({
            inTypes: { position: 'vec3', enabled: 'bool', center: 'vec3', halfXY: 'vec2',
                direction: 'vec2', threshold: 'float', feather: 'float', floorZ: 'float', metricShell: 'vec4',
                rowX: 'vec4', rowY: 'vec4', rowZ: 'vec4', scales: 'vec3', quaternion: 'vec4',
                exteriorEnabled: 'bool', exteriorMinXY: 'vec2', exteriorMaxXY: 'vec2' },
            outTypes: { keep: 'float' },
            inputs: { position, enabled, center, halfXY, direction, threshold, feather, floorZ, metricShell, rowX, rowY, rowZ,
                scales, quaternion, exteriorEnabled, exteriorMinXY, exteriorMaxXY },
            globals: () => [DOLLHOUSE_CUTAWAY_GLSL, SPLAT_EXTERIOR_CLEANUP_GLSL],
            statements: ({ inputs: i, outputs: o }) => [
                `vec4 worldPosition = vec4(${i.position}, 1.0);`,
                `vec3 localPosition = vec3(dot(${i.rowX}, worldPosition), dot(${i.rowY}, worldPosition), dot(${i.rowZ}, worldPosition));`,
                `${o.keep} = ${i.enabled} ? dollhouseMetricKeep(localPosition, ${i.center}, vec3(${i.halfXY}, 1.0), ${i.direction}, ${i.threshold}, ${i.feather}, ${i.floorZ}, ${i.metricShell}) : 1.0;`,
                `${o.keep} *= ${i.exteriorEnabled} ? homeSplatExteriorKeep(${i.position}, ${i.scales}, ${i.quaternion}, ${i.rowX}, ${i.rowY}, ${i.exteriorMinXY}, ${i.exteriorMaxXY}) : 1.0;`,
            ],
        }).outputs.keep;
        return { gsplat: dyno.combineGsplat({ gsplat, opacity: dyno.mul(opacity, keep) }) };
    });
    if (splat.worldModifiers !== undefined && !Array.isArray(splat.worldModifiers)) {
        throw new Error('splat_cutaway_modifiers_invalid');
    }
    splat.worldModifiers = [...(splat.worldModifiers || []), graph];
    splat.updateGenerator();
    let disposed = false;
    let previous = null;

    return {
        update(policy, matrix, exteriorBounds = null) {
            if (disposed) return false;
            const exterior = normalizeExteriorBounds(exteriorBounds);
            const minXY = exterior?.minXY ?? [0, 0];
            const maxXY = exterior?.maxXY ?? [1, 1];
            const next = [...validatedSnapshot(policy, matrix), !!exterior, ...minXY, ...maxXY];
            if (previous && next.every((value, index) => Object.is(value, previous[index]))) return false;
            // Copy caller arrays so later input mutation cannot alter a running job.
            enabled.value = policy.enabled;
            center.value = policy.center.slice();
            halfXY.value = policy.halfExtent.slice(0, 2);
            direction.value = policy.direction.slice();
            threshold.value = policy.threshold;
            feather.value = policy.feather;
            floorZ.value = policy.floorZ;
            metricShell.value = dollhouseMetricShellVector(policy);
            exteriorEnabled.value = !!exterior;
            exteriorMinXY.value = minXY;
            exteriorMaxXY.value = maxXY;
            rowX.value = [matrix[0], matrix[4], matrix[8], matrix[12]];
            rowY.value = [matrix[1], matrix[5], matrix[9], matrix[13]];
            rowZ.value = [matrix[2], matrix[6], matrix[10], matrix[14]];
            previous = next;
            splat.needsUpdate = true;
            return true;
        },
        dispose() {
            if (disposed) return;
            disposed = true;
            previous = null;
            if (splat.worldModifiers?.includes(graph)) {
                splat.worldModifiers = splat.worldModifiers.filter((modifier) => modifier !== graph);
                if (!splat.worldModifiers.length) splat.worldModifiers = undefined;
                splat.updateGenerator();
            }
        },
    };
}
