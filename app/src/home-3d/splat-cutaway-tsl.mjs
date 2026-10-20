/* home-3d/splat-cutaway-tsl.mjs — the Photo cutaway and exterior cleanup as one TSL opacity node.
 *
 * WebGPU path only (?renderer=webgpu). The twin of splat-cutaway.js's Spark
 * dyno graph: the same dollhouse keep (dollhouse-cutaway-tsl.mjs, itself the
 * twin of DOLLHOUSE_CUTAWAY_GLSL) times the same exterior cleanup
 * (SPLAT_EXTERIOR_CLEANUP_GLSL, ported below), multiplied into each splat's
 * opacity. Inputs are validated by the Spark path's own validatedSnapshot(),
 * so both renderers accept and refuse exactly the same policies. The CPU
 * twins dollhouseKeep() and splatExteriorKeep() stay the reference.
 *
 * Spark evaluates in world space with the splat's world scales and rotation.
 * Here a splat arrives as its local centre and covariance, so the CPU folds
 * the splat's matrixWorld into the world-to-apartment rows once per update:
 *
 *   apartment = C * centre,                 C = worldToApartment * matrixWorld
 *   3-sigma support along apartment X/Y = 3 * sqrt(c_r^T Sigma c_r),
 *
 * where c_r is row r of C's 3x3 part. That equals the GLSL's
 * length(dot(rowX.xyz, R*S column)) for the world-space Gaussian.
 */
import { Matrix4, Vector2, Vector3, Vector4 } from 'three';
import { Fn, float, uniform, vec3, vec4, dot, sqrt, max, select } from 'three/tsl';

const cb = new URL(import.meta.url).search;
const { dollhouseMetricKeepNode } = await import(`./dollhouse-cutaway-tsl.mjs${cb}`);
const { dollhouseMetricShellVector } = await import(`./dollhouse-cutaway.mjs${cb}`);
const { normalizeExteriorBounds, SPLAT_EXTERIOR_CENTER_PADDING_M } = await import(`./splat-exterior-cleanup.mjs${cb}`);
const { validatedSnapshot } = await import(`./splat-cutaway.js${cb}`);

/* GLSL homeSplatExteriorKeep(), on a local centre and covariance. NaN
 * evidence compares false everywhere, so it keeps, as the GLSL returns 1. */
export const splatExteriorKeepNode = Fn(([centre, covA, covB, rowX, rowY, minXY, maxXY]) => {
    // covA = (xx, xy, xz, yy), covB = (yz, zz, -, -)
    const sigma = (row) => {
        const v = row.xyz;
        const sx = vec3(covA.x, covA.y, covA.z), sy = vec3(covA.y, covA.w, covB.x), sz = vec3(covA.z, covB.x, covB.y);
        return dot(v, vec3(dot(sx, v), dot(sy, v), dot(sz, v)));
    };
    const position = vec4(centre, 1);
    const cx = dot(rowX, position), cy = dot(rowY, position);
    const rx = sqrt(max(sigma(rowX), 0)).mul(3), ry = sqrt(max(sigma(rowY), 0)).mul(3);
    const pad = float(SPLAT_EXTERIOR_CENTER_PADDING_M);
    const supportOutside = cx.add(rx).lessThan(minXY.x).or(cy.add(ry).lessThan(minXY.y))
        .or(cx.sub(rx).greaterThan(maxXY.x)).or(cy.sub(ry).greaterThan(maxXY.y));
    const centreOutside = cx.lessThan(minXY.x.sub(pad)).or(cy.lessThan(minXY.y.sub(pad)))
        .or(cx.greaterThan(maxXY.x.add(pad))).or(cy.greaterThan(maxXY.y.add(pad)));
    return select(supportOutside.or(centreOutside), float(0), float(1));
});

/**
 * Uniforms and the keep node for one splat object. keep(centre, covA, covB)
 * returns 0..1 for opacity. update() takes the same arguments as the Spark
 * modifier's update() plus the splat's matrixWorld, and reports whether
 * anything changed.
 */
export function createSplatCutawayNode() {
    const u = {
        enabled: uniform(0),
        center: uniform(new Vector3()),
        halfExtent: uniform(new Vector3(1, 1, 1)),
        direction: uniform(new Vector2()),
        threshold: uniform(0.8),
        feather: uniform(0.03),
        floorZ: uniform(0.25),
        metricShell: uniform(new Vector4(1, 0, 0, 0)),
        rowX: uniform(new Vector4(1, 0, 0, 0)),
        rowY: uniform(new Vector4(0, 1, 0, 0)),
        rowZ: uniform(new Vector4(0, 0, 1, 0)),
        exteriorEnabled: uniform(0),
        minXY: uniform(new Vector2()),
        maxXY: uniform(new Vector2(1, 1)),
    };
    const keep = Fn(([centre, covA, covB]) => {
        const position = vec4(centre, 1);
        const local = vec3(dot(u.rowX, position), dot(u.rowY, position), dot(u.rowZ, position));
        const dollhouse = dollhouseMetricKeepNode(local, u.center, u.halfExtent, u.direction,
            u.threshold, u.feather, u.floorZ, u.metricShell);
        const cut = select(u.enabled.greaterThan(0.5), dollhouse, float(1));
        const exterior = splatExteriorKeepNode(centre, covA, covB, u.rowX, u.rowY, u.minXY, u.maxXY);
        return cut.mul(select(u.exteriorEnabled.greaterThan(0.5), exterior, float(1)));
    });

    const worldToApartment = new Matrix4(), composed = new Matrix4();
    let previous = null;
    let disposed = false;
    return {
        uniforms: u,
        keep,
        update(policy, matrix, exteriorBounds = null, splatMatrixWorld = null) {
            if (disposed) return false;
            const exterior = normalizeExteriorBounds(exteriorBounds);
            const minXY = exterior?.minXY ?? [0, 0], maxXY = exterior?.maxXY ?? [1, 1];
            const world = splatMatrixWorld ? Array.from(splatMatrixWorld.elements ?? splatMatrixWorld) : null;
            const next = [...validatedSnapshot(policy, matrix), !!exterior, ...minXY, ...maxXY, ...(world ?? [])];
            if (previous && next.length === previous.length && next.every((value, index) => Object.is(value, previous[index]))) return false;
            u.enabled.value = policy.enabled ? 1 : 0;
            u.center.value.fromArray(policy.center);
            // As the Spark graph: half extent XY, z unused by the keep.
            u.halfExtent.value.set(policy.halfExtent[0], policy.halfExtent[1], 1);
            u.direction.value.fromArray(policy.direction);
            u.threshold.value = policy.threshold;
            u.feather.value = policy.feather;
            u.floorZ.value = policy.floorZ;
            u.metricShell.value.fromArray(dollhouseMetricShellVector(policy));
            u.exteriorEnabled.value = exterior ? 1 : 0;
            u.minXY.value.fromArray(minXY);
            u.maxXY.value.fromArray(maxXY);
            worldToApartment.fromArray(Array.from(matrix));
            composed.copy(worldToApartment);
            if (world) composed.multiply(new Matrix4().fromArray(world));
            const e = composed.elements;
            u.rowX.value.set(e[0], e[4], e[8], e[12]);
            u.rowY.value.set(e[1], e[5], e[9], e[13]);
            u.rowZ.value.set(e[2], e[6], e[10], e[14]);
            previous = next;
            return true;
        },
        dispose() { disposed = true; previous = null; },
    };
}
