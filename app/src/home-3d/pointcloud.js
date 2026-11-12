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
    // A look material that names its classic base relights from that base.
    base = base?.userData?.classicPoints || base;
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

/*
 * The x-ray look (WebGL only), after laser-scan section renders: faint points
 * add up into light, so surfaces read as translucent layers and the far rooms
 * show through the near ones. Brightness comes from density, not from
 * per-point sparkle.
 *
 *   - point-features.mjs gives each point a normal and its sample spacing, plus
 *     the floor height and which side of each wall is inside (a worker, once
 *     per load);
 *   - a scan has about a millionth of the points a survey scan has, so each
 *     point is drawn XRAY_COPIES times, spread across the patch of surface it
 *     stands for; up close a point grows to cover its patch and shares the
 *     same light over the larger area, so tone stays continuous at every zoom;
 *   - the copies add into a half-float target before the main render (no depth
 *     test), and one full-screen pass maps that light to the screen with a
 *     soft shoulder, 1 - exp(-light * exposure), drawn first so markers and
 *     labels stay on top;
 *   - dollhouse: a wall seen from outside its room (the side without floor)
 *     drops to a 25 cm stub, as do partitions between the camera and the orbit
 *     centre, from any angle, on any scan. Home's own cutaway still applies.
 *
 * The classic material stays the cloud's own material: the engine writes its
 * uniforms every frame and the x-ray materials share those uniform objects;
 * the lit layer and the WebGPU port still start from it, and whenever another
 * material is on the cloud (the Lighting view's), x-ray steps aside and the
 * cloud draws as before. Opt out per browser with localStorage
 * 'apartment3d.pointLook' = 'classic'.
 */
export const XRAY_COPIES = 6;
export const XRAY_POINT_PX = 1.3;
export const XRAY_GAIN = 0.09;
export const XRAY_EXPOSURE = 2.6;
export const XRAY_GAMMA = 1.25;
export const XRAY_FOG = 0.45;
export const XRAY_CUT_STUB_M = 0.25;
/* The CSS viewport the gain was tuned at: the same points over fewer pixels pile up brighter. */
export const XRAY_REFERENCE_CSS_AREA = 1148 * 754;
export const POINT_LOOK_KEY = 'apartment3d.pointLook';
/* Points on this layer draw only into the x-ray target; the cloud itself moves to an unrendered layer. */
export const XRAY_LAYER = 30;
const HIDDEN_LAYER = 31;

const xrayCutGlsl = /* glsl */ `
    // dollhouse: walls seen from outside their room, and partitions on the camera's side, drop to a stub
    vec3 nO = normalize(normal);
    if (uPlanCut > 0.5 && position.z > uFloorZ + ${XRAY_CUT_STUB_M.toFixed(2)} && abs(nO.z) < 0.4) {
        vec3 nV = normalize(normalMatrix * normal), vV = normalize(-(modelViewMatrix * vec4(position, 1.0)).xyz);
        vec2 toCam = normalize(uCamLocal.xy - uTargetLocal.xy);
        bool cut = abs(aInside) > 0.5
            ? dot(nV * sign(aInside), vV) < -0.02
            : dot(position.xy - uTargetLocal.xy, toCam) > 0.4 && abs(dot(normalize(nO.xy), toCam)) > 0.45;
        if (cut) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); gl_PointSize = 0.0; return; }
    }
`;

export const xrayVertexShader = /* glsl */ `
${cutawayVertexDeclarations}
uniform float uPixelRatio;
uniform float uFadeNear;
uniform float uWallNear;
uniform float uFadeFar;
uniform float uViewportPx;
uniform float uCopy;
uniform float uXrayGain;
uniform float uPlanCut;
uniform float uFloorZ;
uniform vec3 uCamLocal;
uniform vec3 uTargetLocal;

attribute float intensity;
attribute float aSpacing;
attribute float aInside;

varying float vLight;

float hash(float x) { return fract(sin(x) * 43758.5453); }

void main() {
    ${xrayCutGlsl}
    float r = fract(sin(dot(position.xy, vec2(12.9898, 78.233)) + position.z * 3.7) * 43758.5453);
    // each copy sits a little way across the surface; copy 0 is the measured point
    vec3 t1 = normalize(cross(nO, abs(nO.z) < 0.9 ? vec3(0.0, 0.0, 1.0) : vec3(1.0, 0.0, 0.0))), t2 = cross(nO, t1);
    float spacing = aSpacing * 0.255;                                   // aSpacing: mm / 255
    vec3 p = position + (t1 * (hash(r * 91.7 + uCopy * 13.1) - 0.5) + t2 * (hash(r * 37.3 + uCopy * 7.7) - 0.5))
        * spacing * 1.2 * step(0.5, uCopy);
    vec4 mvPosition = modelViewMatrix * vec4(p, 1.0);
    vDollhouseLocal = (uDollhouseWorldToLocal * modelMatrix * vec4(position, 1.0)).xyz;
    float depth = -mvPosition.z;
    float fade = 1.0 - ${XRAY_FOG.toFixed(2)} * smoothstep(uFadeNear * 0.5, uFadeFar, depth);
    if (uWallNear > 0.0) fade *= smoothstep(uWallNear * 0.35, uWallNear * 0.85, depth);
    // up close a point grows to cover its patch and shares the same light over the larger area
    float base = ${XRAY_POINT_PX.toFixed(2)} * uPixelRatio;
    float patchPx = spacing * projectionMatrix[1][1] * 0.5 * uViewportPx / max(depth, 0.2) * 0.75;
    float size = max(base, patchPx);
    vLight = (0.12 + intensity) * fade * uXrayGain * (base * base) / (size * size);
    gl_PointSize = size;
    gl_Position = projectionMatrix * mvPosition;
}
`;

export const xrayFragmentShader = /* glsl */ `
${cutawayFragmentDeclarations}
varying float vLight;

void main() {
    float d = length(gl_PointCoord - vec2(0.5));
    if (d > 0.5) discard;
    float keep = homeDollhouseKeep();
    if (keep <= 0.001) discard;
    float k = exp(-d * d * 10.0) * keep;
    gl_FragColor = vec4(vec3(vLight * k), vLight * k);
}
`;

export const xrayCompositeFragmentShader = /* glsl */ `
uniform sampler2D tLight;
uniform float uExposure;
uniform float uGamma;
uniform float uOpacity;
varying vec2 vUv;

void main() {
    float light = texture2D(tLight, vUv).r;
    float t = pow(1.0 - exp(-light * uExposure), uGamma) * uOpacity;
    gl_FragColor = vec4(vec3(t), t);                                   // premultiplied over the page
}
`;

/** The x-ray gain for a CSS viewport: the same points over fewer pixels pile up brighter. */
export function xrayGainForViewport(cssWidth, cssHeight) {
    const area = finiteOr(cssWidth, 0) * finiteOr(cssHeight, 0);
    const ratio = area > 0 ? area / XRAY_REFERENCE_CSS_AREA : 1;
    return XRAY_GAIN * Math.min(4, Math.max(0.15, ratio));
}

/** Whether this browser keeps the classic cloud (localStorage opt-out). */
export function xrayLookOptedOut(storage = globalThis.localStorage) {
    try { return storage?.getItem?.(POINT_LOOK_KEY) === 'classic'; } catch (e) { return false; }
}

/** The x-ray copy materials and the composite, sharing the classic material's per-frame uniform objects. */
export function createXrayMaterials(base, { copies = XRAY_COPIES } = {}) {
    const shared = {};
    for (const [name, uniform] of Object.entries(base.uniforms)) {
        if (name.startsWith('uDollhouse') || ['uPixelRatio', 'uFadeNear', 'uFadeFar', 'uWallNear', 'uOpacity'].includes(name)) shared[name] = uniform;
    }
    const own = {
        uViewportPx: { value: 1080 }, uXrayGain: { value: XRAY_GAIN }, uPlanCut: { value: 0 }, uFloorZ: { value: 0 },
        uCamLocal: { value: new THREE.Vector3() }, uTargetLocal: { value: new THREE.Vector3() },
    };
    const copiesMaterials = [];
    for (let c = 0; c < copies; c++) {
        const material = new THREE.ShaderMaterial({
            name: 'apartment-points-xray',
            uniforms: { ...shared, ...own, uCopy: { value: c } },
            vertexShader: xrayVertexShader,
            fragmentShader: xrayFragmentShader,
            transparent: true,
            blending: THREE.CustomBlending,
            blendEquation: THREE.AddEquation,
            blendSrc: THREE.OneFactor, blendDst: THREE.OneFactor,
            blendSrcAlpha: THREE.OneFactor, blendDstAlpha: THREE.OneFactor,
            depthWrite: false,
            depthTest: false,
        });
        // modes.js keeps these out of the depth buffer; x-ray has no depth by design
        material.userData.noDepthWrite = true;
        material.userData.classicPoints = base;
        copiesMaterials.push(material);
    }
    const composite = new THREE.ShaderMaterial({
        name: 'apartment-points-xray-composite',
        uniforms: { tLight: { value: null }, uExposure: { value: XRAY_EXPOSURE }, uGamma: { value: XRAY_GAMMA }, uOpacity: base.uniforms.uOpacity },
        vertexShader: 'varying vec2 vUv;\nvoid main() { vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }',
        fragmentShader: xrayCompositeFragmentShader,
        transparent: true,
        blending: THREE.CustomBlending,
        blendEquation: THREE.AddEquation,
        blendSrc: THREE.OneFactor, blendDst: THREE.OneMinusSrcAlphaFactor,
        blendSrcAlpha: THREE.OneFactor, blendDstAlpha: THREE.OneMinusSrcAlphaFactor,
        depthWrite: false,
        depthTest: false,
    });
    return { copies: copiesMaterials, composite, uniforms: own };
}

/** Half-float colour targets: WebGL2 with a float colour-buffer extension. */
export function xraySupported(renderer) {
    try {
        if (!renderer?.capabilities?.isWebGL2) return false;
        return !!(renderer.extensions?.has?.('EXT_color_buffer_float') || renderer.extensions?.has?.('EXT_color_buffer_half_float'));
    } catch (e) { return false; }
}

function computeFeaturesInWorker(geometry, count, signal) {
    return new Promise((resolve, reject) => {
        const search = new URL(import.meta.url).search;
        let worker;
        try {
            worker = new Worker(new URL(`./point-features-worker.mjs${search}`, import.meta.url), { type: 'module', name: 'home-point-features' });
        } catch (error) { reject(error); return; }
        const stop = () => { worker.terminate(); reject(new DOMException('scene_load_aborted', 'AbortError')); };
        if (signal?.aborted) { stop(); return; }
        signal?.addEventListener?.('abort', stop, { once: true });
        worker.onmessage = (event) => {
            signal?.removeEventListener?.('abort', stop);
            worker.terminate();
            if (event.data?.ok) resolve(event.data); else reject(new Error(event.data?.error || 'point features failed'));
        };
        worker.onerror = (event) => { signal?.removeEventListener?.('abort', stop); worker.terminate(); reject(new Error(event.message || 'point features worker failed')); };
        // copies: the geometry keeps its own arrays
        const positions = geometry.getAttribute('position').array.slice(0, count * 3);
        const intensity = geometry.getAttribute('intensity').array.slice(0, count);
        worker.postMessage({ positions, intensity, count }, [positions.buffer, intensity.buffer]);
    });
}

/**
 * Computes the features for a loaded cloud and puts the x-ray look on it.
 * Resolves to { prepass(), update(state), detach(), ms, cells } or rejects (the classic look stays).
 *   update({ viewportPx, cssWidth, cssHeight, cameraLocal, targetLocal, cut }) once per frame;
 *   prepass() just before the main render: accumulates the copies into the light target.
 */
export async function attachXrayLook(points, base, { renderer, scene, camera, signal } = {}) {
    if (!xraySupported(renderer)) throw new Error('x-ray needs half-float render targets');
    const geometry = points.geometry;
    const count = Math.min(geometry.getAttribute('position').count, geometry.drawRange.count);
    const features = await computeFeaturesInWorker(geometry, count, signal);
    if (signal?.aborted) throw new DOMException('scene_load_aborted', 'AbortError');
    geometry.setAttribute('normal', new THREE.BufferAttribute(features.normal, 3, true));
    geometry.setAttribute('aSpacing', new THREE.BufferAttribute(features.spacing, 1, true));
    geometry.setAttribute('aInside', new THREE.BufferAttribute(features.inside, 1, true));
    const { copies, composite, uniforms } = createXrayMaterials(base);
    uniforms.uFloorZ.value = features.floorZ;
    const group = new THREE.Group();
    group.name = 'apartment-points-xray';
    for (const material of copies) {
        const copy = new THREE.Points(geometry, material);
        copy.frustumCulled = false;
        copy.raycast = () => {};
        copy.layers.set(XRAY_LAYER);
        group.add(copy);
    }
    points.add(group);
    const size = renderer.getDrawingBufferSize(new THREE.Vector2());
    const target = new THREE.WebGLRenderTarget(Math.max(1, size.x), Math.max(1, size.y), { type: THREE.HalfFloatType, depthBuffer: false });
    composite.uniforms.tLight.value = target.texture;
    const quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), composite);
    quad.name = 'apartment-points-xray-composite';
    quad.frustumCulled = false;
    quad.renderOrder = -1000;
    quad.raycast = () => {};
    scene.add(quad);
    const ownLayers = points.layers.mask;
    let active = false;
    const setActive = (on) => {
        active = on;
        group.visible = on;
        quad.visible = on && points.visible;
        if (on) points.layers.set(HIDDEN_LAYER); else points.layers.mask = ownLayers;
    };
    const look = {
        ms: features.ms, cells: features.cells, floorZ: features.floorZ, target,
        get active() { return active; },
        update({ viewportPx, cssWidth, cssHeight, cameraLocal, targetLocal, cut = true } = {}) {
            // another material on the cloud (the Lighting view's) means the cloud draws as before
            setActive(points.material === base);
            if (!active) return;
            if (Number.isFinite(viewportPx) && viewportPx > 0) uniforms.uViewportPx.value = viewportPx;
            uniforms.uXrayGain.value = xrayGainForViewport(cssWidth, cssHeight);
            uniforms.uPlanCut.value = cut ? 1 : 0;
            if (cameraLocal) uniforms.uCamLocal.value.fromArray(cameraLocal);
            if (targetLocal) uniforms.uTargetLocal.value.fromArray(targetLocal);
        },
        prepass() {
            quad.visible = active && points.visible;
            if (!quad.visible) return;
            const buffer = renderer.getDrawingBufferSize(new THREE.Vector2());
            if (buffer.x !== target.width || buffer.y !== target.height) target.setSize(Math.max(1, buffer.x), Math.max(1, buffer.y));
            const previous = renderer.getRenderTarget(), autoClear = renderer.autoClear, mask = camera.layers.mask;
            const clear = renderer.getClearColor(new THREE.Color()), clearAlpha = renderer.getClearAlpha();
            try {
                camera.layers.set(XRAY_LAYER);
                renderer.setRenderTarget(target);
                renderer.setClearColor(0x000000, 0);
                renderer.autoClear = true;
                renderer.render(scene, camera);
            } finally {
                camera.layers.mask = mask;
                renderer.setRenderTarget(previous);
                renderer.setClearColor(clear, clearAlpha);
                renderer.autoClear = autoClear;
            }
        },
        detach() {
            setActive(false);
            points.remove(group);
            scene.remove(quad);
            quad.geometry.dispose();
            for (const material of copies) material.dispose();
            composite.dispose();
            target.dispose();
        },
    };
    setActive(points.material === base);
    return look;
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
