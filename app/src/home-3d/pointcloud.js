/* home-3d/pointcloud.js — the white luminous point cloud.
 *
 * Shader is the engineered.lighting recipe (EngineeredLightingWebsite/js/shaders.js)
 * ported with the plan's changes: constants promoted to uniforms, metric re-tune
 * (uFadeNear/uFadeFar 6/30 vs the original 4/22 which assumed a scale-8 scene),
 * NEW uOpacity for mode crossfades, intensity attribute (real albedo, replaces
 * part of the random brightness), and Z-up object space (vertical shimmer acts
 * on position.z — see COORDINATE_FRAMES.md).
 *
 * Loader is a streaming parser for EXACTLY the pipeline's PLY layout
 * ("homeapt-points v1": x,y,z float32 + intensity uint8, little-endian) —
 * three's PLYLoader can't stream; this one materializes the cloud progressively,
 * which IS the loading UX.
 */
import * as THREE from 'three';

export const vertexShader = /* glsl */ `
uniform float uTime;
uniform float uPixelRatio;
uniform float uBaseSize;
uniform float uFadeNear;
uniform float uFadeFar;
uniform float uSizeRef;
uniform float uJitterAmp;

attribute float intensity;

varying float vDepthFactor;
varying float vRandom;
varying float vIntensity;

void main() {
    vRandom = fract(sin(dot(position.xy, vec2(12.9898, 78.233))) * 43758.5453);
    vIntensity = intensity;

    vec3 pos = position;
    // object space is Z-up (apartment frame): vertical shimmer on z
    pos.z += sin(uTime * 0.8 + vRandom * 6.2831) * uJitterAmp;
    pos.x += sin(uTime * 0.5 + vRandom * 3.14159) * uJitterAmp * 0.6;
    pos.y += cos(uTime * 0.6 + vRandom * 4.712) * uJitterAmp * 0.6;

    vec4 mvPosition = modelViewMatrix * vec4(pos, 1.0);

    float depth = -mvPosition.z;
    vDepthFactor = 1.0 - smoothstep(uFadeNear, uFadeFar, depth);

    float sizeAtten = uBaseSize * uPixelRatio * (uSizeRef / depth);
    gl_PointSize = clamp(sizeAtten * (0.3 + 0.7 * vDepthFactor), 0.5, 12.0);

    gl_Position = projectionMatrix * mvPosition;
}
`;

export const fragmentShader = /* glsl */ `
uniform vec3 uGlowColor;
uniform float uOpacity;

varying float vDepthFactor;
varying float vRandom;
varying float vIntensity;

void main() {
    vec2 center = gl_PointCoord - vec2(0.5);
    float dist = length(center);
    if (dist > 0.5) discard;

    float glow = 1.0 - smoothstep(0.25, 0.5, dist);

    float brightness = 0.25 + 0.75 * vDepthFactor;
    brightness *= 0.85 + 0.15 * vRandom;
    brightness *= 0.62 + 0.38 * vIntensity;   // measured albedo, subtle

    gl_FragColor = vec4(uGlowColor * brightness, glow * uOpacity);
}
`;

const TUNABLES_KEY = 'apartment3d.shader';

export function createPointsMaterial() {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(TUNABLES_KEY) || '{}'); } catch (e) { /* */ }
    const mat = new THREE.ShaderMaterial({
        uniforms: {
            uTime:       { value: 0.0 },
            uPixelRatio: { value: Math.min(window.devicePixelRatio || 1, 2) },
            uBaseSize:   { value: saved.uBaseSize ?? 1.4 },
            uGlowColor:  { value: new THREE.Color(0.95, 0.95, 1.0) },
            uOpacity:    { value: 1.0 },
            // uFadeNear/uFadeFar are driven per-frame from the orbit radius
            // (engine.js); these are just pre-first-frame values.
            uFadeNear:   { value: saved.uFadeNear ?? 6.0 },
            uFadeFar:    { value: saved.uFadeFar ?? 30.0 },
            // 55 tuned visually against the real whole-home cloud (110 made
            // overview points read as blobs at ~23 m orbit radius)
            uSizeRef:    { value: saved.uSizeRef ?? 55.0 },
            uJitterAmp:  { value: saved.uJitterAmp ?? 0.010 },
        },
        vertexShader,
        fragmentShader,
        transparent: true,
        blending: THREE.NormalBlending,
        depthWrite: true,
        depthTest: true,
    });
    mat.userData.saveTunables = () => {
        const u = mat.uniforms;
        try {
            localStorage.setItem(TUNABLES_KEY, JSON.stringify({
                uBaseSize: u.uBaseSize.value, uFadeNear: u.uFadeNear.value,
                uFadeFar: u.uFadeFar.value, uSizeRef: u.uSizeRef.value,
                uJitterAmp: u.uJitterAmp.value,
            }));
        } catch (e) { /* */ }
    };
    return mat;
}

const RECORD = 13; // 3*f32 + u8

/* Streams a homeapt-points PLY into a Points object.
 * onProgress(loaded, total) fires as records land; geometry drawRange grows. */
export async function loadPointCloud(url, material, { onProgress } = {}) {
    const res = await fetch(url, { cache: 'force-cache' });
    if (!res.ok) throw new Error(`points fetch ${res.status} for ${url}`);

    const reader = res.body.getReader();
    let head = new Uint8Array(0);
    let headerEnd = -1, total = 0;

    const findHeader = (buf) => {
        const marker = new TextEncoder().encode('end_header\n');
        outer: for (let i = 0; i <= buf.length - marker.length; i++) {
            for (let j = 0; j < marker.length; j++) {
                if (buf[i + j] !== marker[j]) continue outer;
            }
            return i + marker.length;
        }
        return -1;
    };

    // accumulate until the header is complete
    let firstBody = null;
    while (headerEnd < 0) {
        const { done, value } = await reader.read();
        if (done) throw new Error('points: EOF before end_header');
        const merged = new Uint8Array(head.length + value.length);
        merged.set(head); merged.set(value, head.length);
        head = merged;
        headerEnd = findHeader(head);
    }
    const headerText = new TextDecoder().decode(head.slice(0, headerEnd));
    if (!headerText.includes('homeapt-points v1')) {
        throw new Error('points: not a homeapt-points v1 PLY');
    }
    const m = headerText.match(/element vertex (\d+)/);
    total = parseInt(m[1], 10);
    firstBody = head.slice(headerEnd);

    const positions = new Float32Array(total * 3);
    const intensity = new Uint8Array(total);
    const geometry = new THREE.BufferGeometry();
    const posAttr = new THREE.BufferAttribute(positions, 3);
    const intAttr = new THREE.BufferAttribute(intensity, 1, true); // normalized 0..1
    posAttr.setUsage(THREE.DynamicDrawUsage);
    intAttr.setUsage(THREE.DynamicDrawUsage);
    geometry.setAttribute('position', posAttr);
    geometry.setAttribute('intensity', intAttr);
    geometry.setDrawRange(0, 0);

    const points = new THREE.Points(geometry, material);
    points.frustumCulled = false; // bounds change during streaming

    let loaded = 0;
    let tail = new Uint8Array(0);

    const consume = (chunk) => {
        let buf = chunk;
        if (tail.length) {
            buf = new Uint8Array(tail.length + chunk.length);
            buf.set(tail); buf.set(chunk, tail.length);
        }
        const nRec = Math.min(Math.floor(buf.length / RECORD), total - loaded);
        if (nRec > 0) {
            const dv = new DataView(buf.buffer, buf.byteOffset, nRec * RECORD);
            let p = loaded * 3;
            for (let r = 0; r < nRec; r++) {
                const o = r * RECORD;
                positions[p++] = dv.getFloat32(o, true);
                positions[p++] = dv.getFloat32(o + 4, true);
                positions[p++] = dv.getFloat32(o + 8, true);
                intensity[loaded + r] = dv.getUint8(o + 12);
            }
            loaded += nRec;
            posAttr.needsUpdate = true;
            intAttr.needsUpdate = true;
            geometry.setDrawRange(0, loaded);
            if (onProgress) onProgress(loaded, total);
        }
        tail = buf.slice(nRec * RECORD);
    };

    consume(firstBody);
    for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        consume(value);
    }
    geometry.computeBoundingBox();
    geometry.computeBoundingSphere();
    points.frustumCulled = true;
    if (onProgress) onProgress(loaded, total);
    return { points, geometry, total: loaded };
}
