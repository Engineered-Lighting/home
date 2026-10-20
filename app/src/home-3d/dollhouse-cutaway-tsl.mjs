/* home-3d/dollhouse-cutaway-tsl.mjs — the dollhouse cut as one shared TSL function.
 *
 * WebGPU path only (?renderer=webgpu). A line-for-line twin of
 * DOLLHOUSE_CUTAWAY_GLSL and homeDollhouseKeep() in dollhouse-cutaway.mjs /
 * dollhouse-cutaway-material.js, used by the point cloud and the scan mesh.
 * The CPU twin, dollhouseKeep(), stays the reference for both. The GLSL is
 * fingerprinted through the sites that embed it, so a change there fails
 * tools/test-apartment-webgpu-ports.mjs until this file follows.
 */
import { Fn, float, max, select, smoothstep, sign, abs, dot, vec2 } from 'three/tsl';

const { DOLLHOUSE_SHADER_CONSTANTS: C } =
    await import(`./dollhouse-cutaway.mjs${new URL(import.meta.url).search}`);

/* GLSL dollhouseKeep(). */
export const dollhouseKeepNode = Fn(([positionLocal, center, halfExtent, direction, threshold, feather, floorZ]) => {
    const normalizedXY = positionLocal.xy.sub(center.xy).div(halfExtent.xy);
    const front = max(direction.x.mul(normalizedXY.x), direction.y.mul(normalizedXY.y));
    const aboveFloor = positionLocal.z.sub(floorZ.sub(C.floorGuard));
    const shellThreshold = threshold.sub(float(C.upperRelaxation)
        .mul(smoothstep(C.upperStartM, C.upperEndM, aboveFloor)));
    const cut = smoothstep(shellThreshold, shellThreshold.add(feather), front)
        .mul(smoothstep(floorZ, floorZ.add(0.03), positionLocal.z));
    return float(1).sub(cut);
});

/* GLSL dollhouseMetricKeep(). Both branches are side-effect free, so the
 * GLSL early return becomes a select. */
export const dollhouseMetricKeepNode = Fn(([positionLocal, center, halfExtent, direction, threshold,
    feather, floorZ, metricShell]) => {
    const fallback = dollhouseKeepNode(positionLocal, center, halfExtent, direction, threshold, feather, floorZ);
    const delta = positionLocal.xy.sub(center.xy);
    const projected = vec2(dot(delta, metricShell.xy), dot(delta, vec2(metricShell.y.negate(), metricShell.x)));
    const inside = halfExtent.xy.sub(sign(direction).mul(projected));
    const cut = vec2(1).sub(smoothstep(vec2(metricShell.z.sub(metricShell.w)), vec2(metricShell.z), inside))
        .mul(abs(direction));
    const metric = float(1).sub(max(cut.x, cut.y).mul(smoothstep(floorZ, floorZ.add(0.03), positionLocal.z)));
    return select(metricShell.z.lessThanEqual(0), fallback, metric);
});

/**
 * GLSL homeDollhouseKeep(): 1 keeps, 0 cuts.
 *
 * uniform(name, type) returns the node for one of createCutawayUniforms()'s
 * uniforms, so callers choose how a material reaches them.
 */
export function homeDollhouseKeepNode(uniform, positionLocal) {
    const config = uniform('uDollhouseConfig', 'vec4');
    const keep = dollhouseMetricKeepNode(positionLocal, uniform('uDollhouseCenter', 'vec3'),
        uniform('uDollhouseHalfExtent', 'vec3'), uniform('uDollhouseDirection', 'vec2'),
        config.y, config.z, config.w, uniform('uDollhouseMetricShell', 'vec4'));
    return select(config.x.lessThan(0.5), float(1), keep);
}
