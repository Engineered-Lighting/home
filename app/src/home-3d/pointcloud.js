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

const { createCutawayUniforms, cutawayVertexDeclarations, cutawayFragmentDeclarations } =
    await import(`./dollhouse-cutaway-material.js${new URL(import.meta.url).search}`);

/*
 * The point size law, driven per zoom detent.
 *
 * Size is inverse depth, which is right for a cloud seen from outside and wrong
 * from inside it: at the old closest detent every point nearer than the orbit
 * centre hit the fixed 12px ceiling and the room turned into fat grey discs. Two
 * uniforms fix that without touching the overview look:
 *
 *   uMaxPointPx   the ceiling, 12 device px at the overview detent (exactly the
 *                 old constant) falling to 3.5 CSS px at the closest detent, and
 *                 never above 6 device px there, so the close view keeps the
 *                 fine grain the splat reference has on every display. The S2
 *                 judge measures gl_PointSize, which is device px, so the 6 px
 *                 criterion is enforced in device px, not CSS px.
 *   uDepthFloor   depth below which a point stops growing, 0.35 x the orbit
 *                 radius. At the overview no point is that near, so it changes
 *                 nothing there; inside, it stops the nearest shell of points
 *                 from being the biggest thing on screen.
 *
 * pointSizePx() and pointSizeLimits() below are this exact law in JS, so the
 * per-detent numbers are asserted without a GL context.
 */
export const POINT_SIZE_OVERVIEW_MAX_PX = 12;
export const POINT_SIZE_CLOSE_MAX_CSS_PX = 3.5;
export const POINT_SIZE_CLOSE_MAX_DEVICE_PX = 6;
export const POINT_DEPTH_FLOOR_FRACTION = 0.35;

export const vertexShader = /* glsl */ `
${cutawayVertexDeclarations}
uniform float uTime;
uniform float uPixelRatio;
uniform float uBaseSize;
uniform float uFadeNear;
uniform float uWallNear;
uniform float uFadeFar;
uniform float uSizeRef;
uniform float uJitterAmp;
uniform float uMaxPointPx;
uniform float uDepthFloor;

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
    vDollhouseLocal = (uDollhouseWorldToLocal * modelMatrix * vec4(pos, 1.0)).xyz;

    float depth = -mvPosition.z;
    vDepthFactor = 1.0 - smoothstep(uFadeNear, uFadeFar, depth);
    // wall-fade (P8): at room zoom the engine sets uWallNear > 0 and points
    // between the camera and the room (depth < uWallNear) melt away so the
    // near wall never blocks the interior
    if (uWallNear > 0.0) {
        vDepthFactor *= smoothstep(uWallNear * 0.35, uWallNear * 0.85, depth);
    }

    float sizeAtten = uBaseSize * uPixelRatio * (uSizeRef / max(depth, uDepthFloor));
    gl_PointSize = clamp(sizeAtten * (0.3 + 0.7 * vDepthFactor), 0.5, uMaxPointPx);

    gl_Position = projectionMatrix * mvPosition;
}
`;

function finiteOr(value, fallback) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : fallback;
}

function smoothstep(edge0, edge1, x) {
    const t = Math.min(1, Math.max(0, (x - edge0) / (edge1 - edge0)));
    return t * t * (3 - 2 * t);
}

/** The vertex shader's vDepthFactor, including the room-zoom wall fade. */
export function pointDepthFactor(depth, { fadeNear, fadeFar, wallNear = 0 } = {}) {
    let factor = 1 - smoothstep(fadeNear, fadeFar, depth);
    if (wallNear > 0) factor *= smoothstep(wallNear * 0.35, wallNear * 0.85, depth);
    return factor;
}

/** gl_PointSize for one point, exactly as the vertex shader computes it. */
export function pointSizePx({
    depth,
    depthFactor = 1,
    pixelRatio = 1,
    baseSize = 1.4,
    sizeRef = 55,
    maxPointPx = POINT_SIZE_OVERVIEW_MAX_PX,
    depthFloor = 0,
} = {}) {
    const sizeDepth = Math.max(finiteOr(depth, 0), finiteOr(depthFloor, 0));
    const attenuated = baseSize * pixelRatio * (sizeRef / sizeDepth);
    const size = attenuated * (0.3 + 0.7 * depthFactor);
    return Math.min(maxPointPx, Math.max(0.5, Number.isNaN(size) ? 0.5 : size));
}

/**
 * The two per-frame uniforms for the current orbit.
 *
 * Interpolated on log radius between the overview and the closest detent, so a
 * detent tween changes the ceiling as smoothly as it changes the zoom, and the
 * pre-zoom pull-back past the overview keeps the overview's ceiling.
 */
export function pointSizeLimits({ orbitRadius, overviewRadius, closestRadius, pixelRatio = 1 } = {}) {
    const ratio = finiteOr(pixelRatio, 1) > 0 ? finiteOr(pixelRatio, 1) : 1;
    const closeMax = Math.min(POINT_SIZE_CLOSE_MAX_DEVICE_PX, POINT_SIZE_CLOSE_MAX_CSS_PX * ratio);
    const radius = Math.max(1e-6, finiteOr(orbitRadius, overviewRadius));
    const outer = Math.max(1e-6, finiteOr(overviewRadius, radius));
    const inner = Math.max(1e-6, Math.min(outer, finiteOr(closestRadius, outer)));
    const span = Math.log(outer / inner);
    const t = span > 1e-9
        ? Math.min(1, Math.max(0, Math.log(radius / inner) / span))
        : 1;
    return Object.freeze({
        maxPointPx: closeMax + (POINT_SIZE_OVERVIEW_MAX_PX - closeMax) * t,
        depthFloor: POINT_DEPTH_FLOOR_FRACTION * radius,
    });
}

export const fragmentShader = /* glsl */ `
${cutawayFragmentDeclarations}
uniform vec3 uGlowColor;
uniform float uOpacity;

varying float vDepthFactor;
varying float vRandom;
varying float vIntensity;

void main() {
    vec2 center = gl_PointCoord - vec2(0.5);
    float dist = length(center);
    if (dist > 0.5) discard;
    float cutawayKeep = homeDollhouseKeep();
    if (cutawayKeep <= 0.001) discard;

    float glow = 1.0 - smoothstep(0.25, 0.5, dist);

    float brightness = 0.25 + 0.75 * vDepthFactor;
    brightness *= 0.85 + 0.15 * vRandom;
    brightness *= 0.55 + 0.45 * vIntensity;   // photographic shading (fill now samples the atlas)

    gl_FragColor = vec4(uGlowColor * brightness, glow * uOpacity * cutawayKeep);
}
`;

/*
 * L4 lit points (lit-receivers.mjs): a copy of the
 * cloud's material whose colour is the relit, display-encoded colour of each
 * point, read by vertex index from the blend's RGBA8 target. It shares every
 * uniform object with `base`, so the engine's per-frame writes (fade, size,
 * opacity, cutaway) reach it unchanged; only the colour differs.
 */
export function createLitPointsMaterial(base, { texture, width }) {
    const vertex = base.vertexShader
        .replace('varying float vIntensity;', 'varying float vIntensity;\nuniform sampler2D uLitColor;\nuniform int uLitWidth;\nvarying vec3 vLitColor;')
        .replace('vIntensity = intensity;', 'vIntensity = intensity;\n    vLitColor = texelFetch(uLitColor, ivec2(gl_VertexID % uLitWidth, gl_VertexID / uLitWidth), 0).rgb;');
    const fragment = base.fragmentShader
        .replace('varying float vIntensity;', 'varying float vIntensity;\nvarying vec3 vLitColor;')
        .replace('gl_FragColor = vec4(uGlowColor * brightness, glow * uOpacity * cutawayKeep);',
            'gl_FragColor = vec4(vLitColor, glow * uOpacity * cutawayKeep);');
    if (vertex === base.vertexShader || fragment === base.fragmentShader || !vertex.includes('vLitColor = texelFetch')
        || !fragment.includes('vec4(vLitColor')) throw new Error('lit_points_shader_changed');
    const mat = new THREE.ShaderMaterial({
        name: 'apartment-points-lit',
        uniforms: { ...base.uniforms, uLitColor: { value: texture }, uLitWidth: { value: width } },
        vertexShader: vertex,
        fragmentShader: fragment,
        transparent: base.transparent,
        blending: base.blending,
        depthWrite: base.depthWrite,
        depthTest: base.depthTest,
    });
    mat.userData.litPoints = true;
    return mat;
}

const TUNABLES_KEY = 'apartment3d.shader';

export function createPointsMaterial() {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(TUNABLES_KEY) || '{}'); } catch (e) { /* */ }
    const mat = new THREE.ShaderMaterial({
        uniforms: {
            ...createCutawayUniforms(),
            uTime:       { value: 0.0 },
            uPixelRatio: { value: Math.min(window.devicePixelRatio || 1, 2) },
            uBaseSize:   { value: saved.uBaseSize ?? 1.4 },
            uGlowColor:  { value: new THREE.Color(0.95, 0.95, 1.0) },
            uOpacity:    { value: 1.0 },
            // uFadeNear/uFadeFar are driven per-frame from the orbit radius
            // (engine.js); these are just pre-first-frame values.
            uFadeNear:   { value: saved.uFadeNear ?? 6.0 },
            uWallNear:   { value: 0.0 },
            uFadeFar:    { value: saved.uFadeFar ?? 30.0 },
            // 55 tuned visually against the real whole-home cloud (110 made
            // overview points read as blobs at ~23 m orbit radius)
            // The LIVE engineered.lighting site renders a SPLAT (js/main.js,
            // echopark2.splat) — its fine grain is mm-scale gaussians from
            // ~14 units away, i.e. 1-3 px. The fat-dot theory (170, from the
            // repo's UNUSED point-shader files) read as cauliflower — user
            // verdict. 55 = the fine-grain match; the look's richness comes
            // from per-point photographic shading, not size.
            uSizeRef:    { value: saved.uSizeRef ?? 55.0 },
            uJitterAmp:  { value: saved.uJitterAmp ?? 0.010 },
            // Driven per frame from the zoom detent (engine.js); these are the
            // old fixed 12px ceiling with no floor until the first frame.
            uMaxPointPx: { value: POINT_SIZE_OVERVIEW_MAX_PX },
            uDepthFloor: { value: 0.0 },
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
export async function loadPointCloud(url, material, { onProgress, signal } = {}) {
    const res = await fetch(url, { cache: 'force-cache', signal });
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

    try {
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
    } catch (error) {
        /* An AbortSignal can reject reader.read() after the GPU-side geometry
         * has already been allocated. The caller has no result object to
         * dispose in that path, so the streaming loader owns this rollback. */
        geometry.dispose();
        throw error;
    }
}
