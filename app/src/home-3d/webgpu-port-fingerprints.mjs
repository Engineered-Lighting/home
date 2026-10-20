/* home-3d/webgpu-port-fingerprints.mjs — which GLSL each WebGPU port was written from.
 *
 * The WebGPU renderer (?renderer=webgpu, webgpu-material-ports.mjs) cannot run
 * ShaderMaterial or onBeforeCompile GLSL, so every such site has a hand-written
 * TSL twin. The twin is only correct for the exact GLSL it was ported from, so
 * each port records a fingerprint of that source:
 *
 *   ShaderMaterial   the vertex and fragment source as the site builds them;
 *   onBeforeCompile  the text the site's patch produces from PROBE_SHADER
 *                    below, which also hands back the uniforms it binds.
 *
 * A GLSL edit therefore changes a fingerprint. tools/test-apartment-webgpu-ports.mjs
 * fails on that in CI, and at runtime the WebGPU path reports the material as
 * drifted rather than silently drawing the old effect. Pure JS, no three.js,
 * so Node tests and the WebGL path can load it without the r186 build.
 */

/* The includes every current onBeforeCompile site anchors on. A site that
 * starts anchoring on another include changes its fingerprint, as it should. */
export const PROBE_SHADER = Object.freeze({
    vertexShader: '#include <common>\n#include <project_vertex>\n#include <fog_vertex>\n',
    fragmentShader: '#include <common>\n#include <opaque_fragment>\n#include <dithering_fragment>\n',
});

/** FNV-1a, 32 bit, as 8 hex digits. Identity only; not a security digest. */
export function fingerprintText(text) {
    let hash = 0x811c9dc5;
    const value = String(text);
    for (let i = 0; i < value.length; i++) {
        hash ^= value.charCodeAt(i);
        hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    return hash.toString(16).padStart(8, '0');
}

export function shaderMaterialFingerprint(material) {
    return fingerprintText(`${material?.vertexShader ?? ''}\n//--fragment--\n${material?.fragmentShader ?? ''}`);
}

/** Runs the site's own patch on a probe; never touches a real program. */
export function probeOnBeforeCompile(material) {
    const shader = { uniforms: {}, defines: {}, vertexShader: PROBE_SHADER.vertexShader,
        fragmentShader: PROBE_SHADER.fragmentShader };
    material.onBeforeCompile(shader, null);
    return {
        uniforms: shader.uniforms,
        fingerprint: fingerprintText(`${shader.vertexShader}\n//--fragment--\n${shader.fragmentShader}`),
    };
}

/* Port id -> the fingerprint it was written from. onBeforeCompile ports are
 * found by the site's customProgramCacheKey(); ShaderMaterial ports by their
 * fingerprint alone. */
export const PORT_FINGERPRINTS = Object.freeze({
    'pointcloud': '7ef3a4af',
    'scan-display-floor-0': 'be8a8d1f',
    'scan-display-floor-1': '2cbf54a2',
    'equipment-receiver': '00703afb',
    'equipment-orb-halo': '4d7af393',
    'equipment-beam': 'a35832a8',
    'equipment-glow': '7f6790f3',
    'directional-fill': '85fab0ed',
    'splat-cutaway': 'f1801faf',
});

/**
 * Spark's Photo cutaway (splat-cutaway.js) is a dyno graph, not a material:
 * its GLSL is the two shared function sources plus the graph's statements.
 * splat-cutaway-tsl.mjs is its TSL twin on the WebGPU splat pipeline.
 */
export function splatCutawayFingerprint({ dollhouseGlsl, exteriorGlsl, statementsSource }) {
    return fingerprintText(`${dollhouseGlsl}\n//--exterior--\n${exteriorGlsl}\n//--statements--\n${statementsSource}`);
}

/** The dyno statements block, as written in splat-cutaway.js's source. */
export function splatCutawayStatements(source) {
    const match = /statements: \(\{ inputs: i, outputs: o \}\) => \[([\s\S]*?)\n\s*\],/.exec(String(source));
    return match ? match[1].trim() : null;
}

export const ON_BEFORE_COMPILE_PORTS = Object.freeze({
    'home-scan-display-floor-0-1': 'scan-display-floor-0',
    'home-scan-display-floor-1-1': 'scan-display-floor-1',
    'equipment-visible-receiver-1': 'equipment-receiver',
    'equipment-orb-halo-1': 'equipment-orb-halo',
});
