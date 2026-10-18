import * as THREE from 'three';

const { DOLLHOUSE_CUTAWAY_GLSL, dollhouseMetricShellVector } = await import(`./dollhouse-cutaway.mjs${new URL(import.meta.url).search}`);

export function createCutawayUniforms() {
    return {
        uDollhouseWorldToLocal: { value: new THREE.Matrix4() },
        uDollhouseCenter: { value: new THREE.Vector3() },
        uDollhouseHalfExtent: { value: new THREE.Vector3(1, 1, 1) },
        uDollhouseDirection: { value: new THREE.Vector2() },
        uDollhouseMetricShell: { value: new THREE.Vector4(1, 0, 0, 0) },
        // enabled, normalized shell threshold, feather width, protected floor
        uDollhouseConfig: { value: new THREE.Vector4(0, 0.8, 0.03, 0.25) },
    };
}

export function updateCutawayUniforms(uniforms, policy, worldToLocal) {
    if (!uniforms?.uDollhouseConfig) return;
    const metric = dollhouseMetricShellVector(policy);
    const active = policy?.enabled === true && (policy.metricShell == null || metric[2] > 0);
    uniforms.uDollhouseMetricShell?.value.fromArray(metric);
    uniforms.uDollhouseConfig.value.set(active ? 1 : 0,
        policy?.threshold ?? 0.8, policy?.feather ?? 0.03, policy?.floorZ ?? 0.25);
    if (worldToLocal) uniforms.uDollhouseWorldToLocal.value.copy(worldToLocal);
    if (!active) return;
    uniforms.uDollhouseCenter.value.fromArray(policy.center);
    uniforms.uDollhouseHalfExtent.value.fromArray(policy.halfExtent);
    uniforms.uDollhouseDirection.value.fromArray(policy.direction);
}

export const cutawayVertexDeclarations = `
uniform mat4 uDollhouseWorldToLocal;
varying vec3 vDollhouseLocal;
`;

export const cutawayFragmentDeclarations = `
uniform vec3 uDollhouseCenter;
uniform vec3 uDollhouseHalfExtent;
uniform vec2 uDollhouseDirection;
uniform vec4 uDollhouseMetricShell;
uniform vec4 uDollhouseConfig;
varying vec3 vDollhouseLocal;
${DOLLHOUSE_CUTAWAY_GLSL}
float homeDollhouseKeep() {
    if (uDollhouseConfig.x < 0.5) return 1.0;
    return dollhouseMetricKeep(vDollhouseLocal, uDollhouseCenter,
        uDollhouseHalfExtent, uDollhouseDirection,
        uDollhouseConfig.y, uDollhouseConfig.z, uDollhouseConfig.w, uDollhouseMetricShell);
}
`;

/** Display materials only. Collision proxies deliberately never call this. */
export function injectMeshCutaway(shader, uniforms) {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
        .replace('#include <common>', `#include <common>\n${cutawayVertexDeclarations}`)
        .replace('#include <project_vertex>', `#include <project_vertex>
vDollhouseLocal = (uDollhouseWorldToLocal * modelMatrix * vec4(transformed, 1.0)).xyz;`);
    shader.fragmentShader = shader.fragmentShader
        .replace('#include <common>', `#include <common>\n${cutawayFragmentDeclarations}`)
        .replace('#include <dithering_fragment>',
            '#include <dithering_fragment>\nif (homeDollhouseKeep() < 0.5) discard;');
}
