/* home-3d/lit-view.mjs — Tier-0 Lit (APT-V01; SPEC Lit view; LIGHT-AND-BODY-RESEARCH Tier 0).
 *
 * Relights the existing scan mesh with simulated light from the virtual
 * fixtures, using built-in three.js parts only: SpotLights, built-in PCF
 * shadow maps and MeshStandardMaterial, with no custom shader, so the view
 * ports to WebGPURenderer unchanged. The light model (assumed lumens to
 * candela by beam angle, the assumed dimming curve, constant-luminance
 * colour, zone directions, the 160 ms ease, the fixed night exposure) lives
 * in the renderer-independent lit-model.mjs. Lights ease toward each set
 * level on the fixtures' 160 ms clock; the aim follows the exact metadata.
 *
 * Spot shadows are crisp (2048 PCF) and Down shadows soft (1024 PCF); each
 * shadow map is cached and re-renders only when its own light moves. Each
 * radial zone is its own unshadowed lobe in the live view. Light intensity
 * is candela x the fixed night exposure, and the renderer's tone mapping is
 * left untouched, so the cloud, the splat, the orbs and every overlay keep
 * their exact unlit look.
 *
 * This is a modeled view, never photometry: the lumen ratings are SPEC's
 * assumptions and are disclosed with the view, and materials are assumed
 * (the scan's own colours stand in for albedo).
 *
 * Reversible and confined to the mesh: Lit swaps the mesh materials and
 * geometry for lit copies and restores the originals on exit. Shadow maps
 * use the default depth material, so the camera's dollhouse cutaway never
 * changes world shadowing.
 *
 * One bounce (L5, lit-bvh-bake.mjs) is traced through a BVH over the scan
 * mesh in a worker (WebGPU compute, or its CPU twin), with a shadow ray on
 * every path, so it never leaks through a wall. It lands in a built-in
 * lightMap: for its lifetime Lit gives each mesh node a non-indexed copy
 * with a uv1 atlas of 2 x 2 texels per triangle. A moved spot re-bakes only
 * its own layer, once the Set sweep lands; a slider only re-weights the
 * layers. The lux readout at named aims is modeled with the same tracer.
 *
 * L2's exposure and tone pipeline. Lights stay pre-exposed at the fixed
 * design night (lit-model.mjs LIT_EXPOSURE, EV100 4.45) on both paths; the
 * policy lives in lit-exposure.mjs.
 *
 *   WebGL (r180, the default)   MeshStandardMaterial, drawn straight to the
 *       canvas with no tone pass. With no night adaptation here, the assumed
 *       ambient floor is shown night-adapted.
 *   WebGPU path (?renderer=webgpu, either backend)   MeshStandardNodeMaterial
 *       with the shared TSL dollhouse cut (no GLSL patch anywhere). The lit
 *       mesh renders scene-referred into a half-float target; one TSL pass
 *       applies the mesopic shift, the metered partial night adaptation and
 *       AgX with a lifted toe, then encodes for the canvas; everything else
 *       (orbs, beams, overlays) draws on top over the mesh's depth, never tone
 *       mapped. The frame loop is untouched: the passes run inside
 *       renderer.render for the engine's own scene while Lit is on.
 *
 * Night adaptation is presentation, not lux.
 *
 * L4 lit receivers (behind ?lit-splats=1, lit-receivers.mjs): in Photo and
 * Cloud no mesh is relit and no light or shadow map is made. The engine hands
 * Lit a receiver controller; each sync passes it the eased lights and the
 * adaptation, and it re-blends the splat or point colours from L3's light
 * basis. The scan mesh still feeds the bake (never displayed or swapped), which
 * then re-bakes only the live spots and their columns for those receivers. The
 * adaptation meters the relit receivers in view, on both renderers.
 */
import * as THREE from 'three';

const Model = await import(`./lit-model.mjs${new URL(import.meta.url).search}`);
const Bake = await import(`./lit-bvh-bake.mjs${new URL(import.meta.url).search}`);
const Exposure = await import(`./lit-exposure.mjs${new URL(import.meta.url).search}`);
/* The WebGPU path's pieces, loaded only when 'three' is the r186 WebGPU build
 * (index.html's ?renderer=webgpu importmap); the WebGL path never loads TSL. */
const NodePath = typeof THREE.WebGPURenderer === 'function' ? await loadNodePath() : null;
async function loadNodePath() {
    const search = new URL(import.meta.url).search;
    const [tsl, cutaway, fingerprints] = await Promise.all([import('three/tsl'),
        import(`./dollhouse-cutaway-tsl.mjs${search}`), import(`./webgpu-port-fingerprints.mjs${search}`)]);
    return { tsl, homeDollhouseKeepNode: cutaway.homeDollhouseKeepNode, probeOnBeforeCompile: fingerprints.probeOnBeforeCompile };
}
export const { LIT_ASSUMED_LUMENS, LIT_ASSUMPTION_SOURCE, LIT_DISCLOSURE, LIT_DISCLOSURE_DETAIL, LIT_EXPOSURE, LIT_TARGET_FPS,
    LIT_BOUNCE_LIMIT, LIT_BOUNCE_ALBEDO, candelaFromLumens, litDimmingFraction } = Model;
export const { LIT_ADAPTATION, LIT_AMBIENT_FLOOR, LIT_TONE, LIT_TONE_OPERATORS } = Exposure;
/* WebGPU path: the layer the scene-referred pass draws (the lit mesh and its
 * lights). The mesh keeps layer 0 too, so picking and readbacks are unchanged. */
export const LIT_LAYER = 29;
// Tier-0's pre-exposure is the fixed design night (EV100 4.45).
export const LIT_DESIGN_EV100 = Exposure.ev100FromExposure(Model.LIT_EXPOSURE);
// Cached shadow maps by softness: 4 spots at 2048, 4 downs at 1024 (halved when reduced).
export const LIT_SHADOW_MAP_SIZES = Object.freeze({ crisp: 2048, soft: 1024 });
export const LIT_LUX_LABEL = 'Modeled lux';
// Below LIT_TARGET_FPS for this many consecutive 500 ms stats, Lit declares
// a reduced quality instead of silently dropping frames.
const LIT_SLOW_SAMPLES = 4;
// Scan colours carry the capture's own lighting; scale them toward an albedo.
const ASSUMED_ALBEDO_SCALE = 0.8;
// A spot aimed during the Set sweep re-bakes once the sweep lands, or after this.
const SWEEP_DEFER_MAX_MS = 1500;
const MAX_LUX_AIMS = 8;

/* Light colour for a Kelvin setting at constant luminance (Kim et al. ->
 * XYZ at Y = 1 -> linear sRGB), so brightness alone sets luminance. The orbs
 * and beams keep their own display colour. */
export function kelvinColor(kelvin, target = new THREE.Color()) {
    const [r, g, b] = Model.cctToLinearRgb(kelvin);
    return target.setRGB(r, g, b, THREE.LinearSRGBColorSpace);
}

/* The model's input for one virtual fixture, read from the fixture's own
 * exact metadata (never the eased drawing), in its parent's frame. */
export function fixtureModelInput(fixtureRoot) {
    if (!fixtureRoot?.isObject3D || !fixtureRoot.parent) return null;
    fixtureRoot.updateWorldMatrix(true, true);
    const local = node => fixtureRoot.parent.worldToLocal(node.getWorldPosition(new THREE.Vector3())).toArray();
    const robotic = fixtureRoot.getObjectByName('robotic-spotlight')?.userData;
    const down = fixtureRoot.getObjectByName('downward-zone'), emitter = fixtureRoot.getObjectByName('downward-emitter');
    const orb = fixtureRoot.getObjectByName('fixture-orb');
    const zones = [];
    for (let number = 1; number <= 6; number++) {
        const channel = fixtureRoot.getObjectByName('radial-zone-' + number)?.userData?.channel;
        if (channel) zones.push({ number, azimuth_deg: channel.azimuth_deg, channel });
    }
    return {
        fixtureId: fixtureRoot.userData?.fixture_id ?? null,
        spot: robotic && Array.isArray(robotic.beam_origin_m) && Array.isArray(robotic.direction_unit)
            ? { origin: robotic.beam_origin_m, direction: robotic.direction_unit, channel: robotic.channel } : null,
        down: down && emitter ? { origin: local(emitter), channel: down.userData.channel } : null,
        center: orb ? local(orb) : fixtureRoot.position.toArray(),
        zones,
    };
}

/* The world-space lights of one fixture: the model's lights, moved from the
 * fixture's parent frame into the world. */
export function fixtureWorldLights(fixtureRoot, { spotFwhmDeg = 20 } = {}) {
    const input = fixtureModelInput(fixtureRoot);
    if (!input) return [];
    const parent = fixtureRoot.parent;
    return Model.fixtureLights(input, { spotFwhmDeg }).map(light => ({
        ...light, fixtureId: input.fixtureId,
        origin: parent.localToWorld(new THREE.Vector3(...light.origin)),
        direction: new THREE.Vector3(...light.direction).transformDirection(parent.matrixWorld),
    }));
}

const srgbToLinear = c => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const mobileGpu = () => !!(globalThis.navigator?.userAgentData?.mobile || /iPhone|iPad|Android/.test(globalThis.navigator?.userAgent || '')
    || (globalThis.navigator?.maxTouchPoints > 1 && /Macintosh/.test(globalThis.navigator?.userAgent || '')));
function defaultBakeEndpoint() {
    if (typeof Worker === 'function') {
        try { return new Worker(new URL(`./lit-bvh-worker.mjs${new URL(import.meta.url).search}`, import.meta.url), { type: 'module' }); }
        catch (_) { /* in-thread below */ }
    }
    return Bake.createLocalBakeEndpoint({ gpu: globalThis.navigator?.gpu || null });
}

/* Assumed albedo per triangle for the bounce, the one the lit material
 * shows: the scan colour (sRGB to linear) x the assumed scale, sampled at the
 * triangle's corners and centre from a 64 x 64 copy of the map. A map whose
 * pixels cannot be read counts as mid grey. */
function albedoSampler(material, geometry) {
    const source = Array.isArray(material) ? material.find(Boolean) : material;
    const map = source?.map, scale = ASSUMED_ALBEDO_SCALE, untinted = new THREE.Color(0x9ea4aa);
    const colors = source?.vertexColors ? geometry.attributes.color : null, size = 64;
    let pixels = null;
    if (map?.image) {
        try {
            const canvas = typeof OffscreenCanvas === 'function' ? new OffscreenCanvas(size, size) : globalThis.document?.createElement('canvas');
            if (canvas) {
                canvas.width = size; canvas.height = size;
                const context = canvas.getContext('2d', { willReadFrequently: true });
                context.drawImage(map.image, 0, 0, size, size);
                pixels = context.getImageData(0, 0, size, size).data;
            }
        } catch (_) { pixels = null; }
        map.updateMatrix?.();
    }
    const uv = new THREE.Vector2(), wrap = value => value - Math.floor(value);
    return (t, uvs) => {
        if (colors) {
            let r = 0, g = 0, b = 0;
            for (let k = 0; k < 3; k++) { r += colors.getX(t * 3 + k); g += colors.getY(t * 3 + k); b += colors.getZ(t * 3 + k); }
            return [r / 3 * scale, g / 3 * scale, b / 3 * scale];
        }
        if (!map) return [untinted.r, untinted.g, untinted.b];
        if (!pixels || !uvs) return [0.5 * scale, 0.5 * scale, 0.5 * scale];
        const sum = [0, 0, 0];
        const at = (u, v) => {
            uv.set(u, v);
            if (map.matrix) uv.applyMatrix3(map.matrix);
            const x = Math.min(size - 1, Math.floor(wrap(uv.x) * size)), y = Math.min(size - 1, Math.floor((map.flipY ? 1 - wrap(uv.y) : wrap(uv.y)) * size));
            const o = (y * size + x) * 4;
            for (let c = 0; c < 3; c++) sum[c] += srgbToLinear(pixels[o + c] / 255);
        };
        const u0 = uvs.getX(t * 3), v0 = uvs.getY(t * 3), u1 = uvs.getX(t * 3 + 1), v1 = uvs.getY(t * 3 + 1), u2 = uvs.getX(t * 3 + 2), v2 = uvs.getY(t * 3 + 2);
        at(u0, v0); at(u1, v1); at(u2, v2); at((u0 + u1 + u2) / 3, (v0 + v1 + v2) / 3);
        return sum.map(value => Math.min(0.9, Math.max(0.02, value / 4 * scale)));
    };
}

const UNIFORM_DEFAULTS = { float: () => 0, vec2: () => new THREE.Vector2(), vec3: () => new THREE.Vector3(),
    vec4: () => new THREE.Vector4(), mat4: () => new THREE.Matrix4() };

/* WebGPU path: the display mesh's cut (dollhouse shell plus the near-camera
 * melt) as a TSL mask, reading the display material's own live uniforms. Its
 * GLSL patch runs once on a probe to hand them over, as the
 * foundation's material ports do; no GLSL is compiled. null when the display
 * material carries no cut, and the lit mesh is then uncut. */
function cutawayMask(display) {
    if (!NodePath || !display) return null;
    let uniforms = null;
    try { uniforms = NodePath.probeOnBeforeCompile(display).uniforms; } catch (_) { return null; }
    if (!uniforms?.uDollhouseConfig || !uniforms.uDollhouseWorldToLocal) return null;
    const T = NodePath.tsl;
    const live = (name, type) => T.uniform(UNIFORM_DEFAULTS[type]()).onRenderUpdate(() => uniforms[name]?.value);
    const local = T.varying(live('uDollhouseWorldToLocal', 'mat4').mul(T.modelWorldMatrix).mul(T.vec4(T.positionLocal, 1)).xyz,
        'vLitDollhouseLocal');
    let keep = NodePath.homeDollhouseKeepNode(live, local).greaterThanEqual(0.5);
    if (uniforms.uMeshNearCut) {
        const nearCut = live('uMeshNearCut', 'float');
        keep = keep.and(nearCut.lessThanEqual(0).or(T.positionView.z.negate().greaterThanEqual(nearCut)));
    }
    return keep;
}

export function createLitView({ scene, renderer, getMesh, getFixtureRoots, spotFwhmDeg = 20,
    getNamedAims = () => [], isPresentationActive = () => false, onStateChange = () => {}, createBakeEndpoint = defaultBakeEndpoint,
    getBakeMesh = null, getReceivers = null,
    bakeQuality = mobileGpu() ? 'progressive' : null, signal = null,
    now = () => performance.now(), reducedMotion = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)') } = {}) {
    if (!scene?.isScene || !renderer?.shadowMap || typeof getMesh !== 'function' || typeof getFixtureRoots !== 'function')
        throw new TypeError('lit_view_dependencies_required');
    // The scene-referred pipeline needs the node renderer and the r186 build.
    const nodePath = !!(NodePath && renderer.isWebGPURenderer);
    const group = new THREE.Group();
    group.name = 'lit-view-lights';
    group.userData = { presentation: 'modeled_lit_view', photometry: 'assumed_not_measured', physical_commands: false };
    /* The disclosed, assumed night ambient (lit-exposure.mjs) in place of
     * Tier-0's undisclosed fill: lux x the design pre-exposure. The WebGL path
     * has no adaptation stage, so it shows the floor already night-adapted
     * (x 2^capEv), which keeps Tier-0's navigable night room. */
    const floor = new THREE.HemisphereLight(0xffffff, 0xffffff,
        LIT_AMBIENT_FLOOR.lux * Model.LIT_EXPOSURE * (nodePath ? 1 : Exposure.adaptationGain(LIT_ADAPTATION.capEv)));
    floor.name = 'lit-ambient-floor';
    floor.userData = { lit_source: 'ambient_floor', lux: LIT_AMBIENT_FLOOR.lux, kelvin: LIT_AMBIENT_FLOOR.kelvin,
        status: LIT_AMBIENT_FLOOR.status, measured: false, night_adapted: !nodePath };
    kelvinColor(LIT_AMBIENT_FLOOR.kelvin, floor.color);
    floor.groundColor.copy(floor.color).multiplyScalar(LIT_AMBIENT_FLOOR.groundRatio);
    group.add(floor);
    let active = false, quality = 'full', slow = 0, swapped = [], lights = [], signature = '', saved = null, mesh = null;
    // L4: the receiver controller while Lit relights Photo or Cloud, and the
    // mesh the bake traces (the displayed mesh, or in Photo/Cloud the scan).
    let receivers = null, bakeSource = null, receiverMeterAt = -Infinity;
    // The 160 ms ease: what each light shows, and the clock of the last step.
    let placementSignature = '', shown = [], easedAt = null, easing = false;
    // Partial night adaptation (WebGPU path): EV above the design night, toward
    // the latest meter of the scene-referred target (null until the first).
    let adaptEv = null, adaptTarget = { dropEv: 0, adaptEv: 0 }, adaptedAt = null, adapting = false, metered = null, meterSerial = 0;
    let operator = nodePath ? LIT_TONE.view : 'linear';
    if (nodePath) floor.layers.set(LIT_LAYER);
    // The bounce bake: one worker for Lit's lifetime, one scene per mesh.
    let baker = null, bakeScene = 0, bakeSignature = '', bakePlacement = '', deferredSince = null, lightmap = null, lux = [];
    const idleBounce = () => ({ method: Bake.LIT_BAKE_METHOD, status: 'idle', backend: null, receivers: 0, triangles: 0, lastBake: null, progress: null, error: null });
    let bounce = idleBounce();
    const changed = () => { try { onStateChange(state()); } catch (_) { /* the view keeps going */ } };
    signal?.addEventListener?.('abort', () => { baker?.dispose(); baker = null; }, { once: true });

    // ---- WebGPU path: the scene-referred pipeline ------------------------------
    const pipeline = nodePath ? createScenePipeline() : null;
    function createScenePipeline() {
        const T = NodePath.tsl;
        // Lit materials write scene-referred linear values: no display encode.
        const sceneReferred = T.context({ getOutput: output => output });
        const noShadowMask = T.bool(true);
        const preExposure = T.uniform(Model.LIT_EXPOSURE), gain = T.uniform(1);
        const size = new THREE.Vector2();
        let target = null, display = null, readTarget = null, inner = null, wrapper = null, lastCamera = null;
        let meterTarget = null, meterQuad = null, meterPending = false, meterRequestedAt = -Infinity, meterToken = 0;
        function ensure(width, height) {
            if (!target) {
                target = new THREE.RenderTarget(width, height, { type: THREE.HalfFloatType, samples: renderer.samples,
                    depthBuffer: true, generateMipmaps: false, colorSpace: THREE.LinearSRGBColorSpace });
                target.texture.name = 'lit-scene-referred';
                /* The display transform, drawn as a clip-space quad first in
                 * the output pass (not a pass of its own: on the canvas the
                 * multisampled colour is not kept between passes). */
                const material = new THREE.NodeMaterial();
                material.name = 'lit-display-transform';
                material.depthTest = false; material.depthWrite = false; material.blending = THREE.NoBlending;
                material.vertexNode = T.vec4(T.positionGeometry.xy, 0, 1);
                display = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), material);
                display.name = 'lit-display-transform';
                display.frustumCulled = false; display.renderOrder = Number.MIN_SAFE_INTEGER; display.visible = false;
                display.raycast = () => {};
                display.userData = { presentation_only: true };
                group.add(display);
                toneNode();
            } else if (target.width !== width || target.height !== height) target.setSize(width, height);
        }
        function toneNode() {
            if (!display) return;
            display.material.fragmentNode = Exposure.litDisplayNode(T, { color: T.texture(target.texture, T.screenUV), preExposure, gain, operator });
            display.material.needsUpdate = true;
        }
        /* The meter (lit-exposure.mjs LIT_METER): a coarse copy of the
         * scene-referred target, read back asynchronously; never more than one
         * in flight and never more often than the period. */
        function requestMeter() {
            const at = now();
            if (meterPending || at - meterRequestedAt < Exposure.LIT_METER.periodMs) return;
            const { width, height } = Exposure.LIT_METER;
            if (!meterTarget) {
                meterTarget = new THREE.RenderTarget(width, height, { type: THREE.HalfFloatType, depthBuffer: false,
                    generateMipmaps: false, colorSpace: THREE.LinearSRGBColorSpace });
                const material = new THREE.NodeMaterial();
                material.name = 'lit-meter';
                material.fragmentNode = T.texture(target.texture, T.uv());
                meterQuad = new THREE.QuadMesh(material);
            }
            meterPending = true; meterRequestedAt = at;
            renderer.setRenderTarget(meterTarget);
            meterQuad.render(renderer);
            const token = meterToken;
            Promise.resolve(renderer.readRenderTargetPixelsAsync(meterTarget, 0, 0, width, height)).then(data => {
                if (token !== meterToken || !active || !data) return;
                const values = unpadRows(data, width, height, 4), half = values instanceof Uint16Array;
                const read = half ? index => THREE.DataUtils.fromHalfFloat(values[index]) : index => values[index];
                const samples = new Float64Array(width * height);
                for (let i = 0; i < width * height; i++) samples[i] = read(i * 4 + 3) > 0.5
                    ? Exposure.lumaOf([read(i * 4), read(i * 4 + 1), read(i * 4 + 2)]) / Model.LIT_EXPOSURE : 0;
                const luminance = Exposure.meterLuminance(samples);
                if (luminance !== null) { metered = luminance; meterSerial++; }
            }).catch(() => { /* the next period meters again */ }).finally(() => { if (token === meterToken) meterPending = false; });
        }
        /* One Lit frame into the current render target (the canvas, or the
         * capture target): 1 the lit mesh scene-referred into the half-float
         * target; 2 the output pass: the display transform as its background,
         * the lit mesh's depth without colour, then everything else over it. */
        function render(camera) {
            const output = renderer.getRenderTarget(), face = renderer.getActiveCubeFace?.() ?? 0, mip = renderer.getActiveMipmapLevel?.() ?? 0;
            if (output) size.set(output.width, output.height); else renderer.getDrawingBufferSize(size);
            ensure(Math.max(1, Math.round(size.x)), Math.max(1, Math.round(size.y)));
            lastCamera = camera;
            const autoClear = renderer.autoClear, layers = camera.layers.mask;
            try {
                sync();
                renderer.setRenderTarget(target); renderer.autoClear = true; camera.layers.set(LIT_LAYER);
                inner.call(renderer, scene, camera);
                camera.layers.mask = layers;
                requestMeter();
                renderer.setRenderTarget(output, face, mip);
                display.visible = true;
                for (const entry of swapped) entry.node.material = entry.depth;
                inner.call(renderer, scene, camera);
            } finally {
                display.visible = false;
                for (const entry of swapped) entry.node.material = entry.lit;
                camera.layers.mask = layers; renderer.autoClear = autoClear;
                if (renderer.getRenderTarget() !== output) renderer.setRenderTarget(output, face, mip);
            }
        }
        return {
            preExposure, gain,
            materialFor(display, options) {
                const material = new THREE.MeshStandardNodeMaterial(options);
                material.contextNode = sceneReferred;
                const depth = new THREE.MeshBasicNodeMaterial({ side: options.side, colorWrite: false });
                depth.name = 'lit-view-depth';
                const mask = cutawayMask(display);
                if (mask) { material.maskNode = mask; material.maskShadowNode = noShadowMask; depth.maskNode = mask; }
                return { material, depth, cut: !!mask };
            },
            install() {
                if (wrapper) return;
                inner = renderer.render;
                wrapper = function litRender(target3d, camera) {
                    if (!active || target3d !== scene || !camera?.isCamera || scene.overrideMaterial) return inner.apply(this, arguments);
                    return render(camera);
                };
                renderer.render = wrapper;
            },
            uninstall() {
                // A later wrapper (a test's timing probe) keeps ours as its inner; ours passes through once inactive.
                if (wrapper && renderer.render === wrapper) renderer.render = inner;
                wrapper = null;
                if (display) { group.remove(display); display.geometry.dispose(); display.material.dispose(); }
                target?.dispose(); readTarget?.dispose(); meterTarget?.dispose(); meterQuad?.material.dispose();
                target = null; display = null; readTarget = null; lastCamera = null; meterTarget = null; meterQuad = null;
                meterToken++; meterPending = false; meterRequestedAt = -Infinity;
            },
            retone: toneNode,
            /* Linear readback for calibration: the lit mesh's scene-referred
             * luminance (cd/m², modeled from assumed output), never tone mapped. */
            async readLinear({ scale = 0.25 } = {}) {
                if (!active || !lastCamera || !inner) return null;
                renderer.getDrawingBufferSize(size);
                const factor = Math.min(1, Math.max(0.01, Number(scale) || 0.25));
                const width = Math.max(1, Math.round(size.x * factor)), height = Math.max(1, Math.round(size.y * factor));
                if (!readTarget) readTarget = new THREE.RenderTarget(width, height, { type: THREE.HalfFloatType, depthBuffer: true,
                    generateMipmaps: false, colorSpace: THREE.LinearSRGBColorSpace });
                else if (readTarget.width !== width || readTarget.height !== height) readTarget.setSize(width, height);
                const camera = lastCamera, previous = renderer.getRenderTarget(), layers = camera.layers.mask, autoClear = renderer.autoClear;
                try {
                    renderer.setRenderTarget(readTarget); renderer.autoClear = true; camera.layers.set(LIT_LAYER);
                    inner.call(renderer, scene, camera);
                } finally { camera.layers.mask = layers; renderer.autoClear = autoClear; renderer.setRenderTarget(previous); }
                const data = await renderer.readRenderTargetPixelsAsync(readTarget, 0, 0, width, height);
                if (!active || !data) return null;
                const values = unpadRows(data, width, height, 4);
                const half = values instanceof Uint16Array, luminance = new Float32Array(width * height), coverage = new Uint8Array(width * height);
                const read = half ? index => THREE.DataUtils.fromHalfFloat(values[index]) : index => values[index];
                for (let i = 0; i < width * height; i++) {
                    const y = Exposure.lumaOf([read(i * 4), read(i * 4 + 1), read(i * 4 + 2)]);
                    luminance[i] = y / Model.LIT_EXPOSURE; coverage[i] = read(i * 4 + 3) > 0.5 ? 1 : 0;
                }
                return { width, height, bottomUp: !renderer.backend?.isWebGPUBackend, unit: 'cd/m2', modeled: true, measured: false,
                    preExposure: Model.LIT_EXPOSURE, luminance, coverage };
            },
        };
    }

    function litMaterialFor(original) {
        const source = Array.isArray(original) ? original.find(Boolean) : original;
        const tinted = !!(source?.map || source?.vertexColors);
        const options = { map: source?.map || null, vertexColors: !!source?.vertexColors,
            color: tinted ? new THREE.Color(ASSUMED_ALBEDO_SCALE, ASSUMED_ALBEDO_SCALE, ASSUMED_ALBEDO_SCALE) : 0x9ea4aa,
            roughness: 0.88, metalness: 0, side: source?.side ?? THREE.FrontSide,
            lightMap: lightmap, lightMapIntensity: 1 };
        const made = pipeline ? pipeline.materialFor(source, options) : { material: new THREE.MeshStandardMaterial(options), depth: null, cut: false };
        made.material.name = 'lit-view-assumed-material';
        return made;
    }
    /* Lit's mesh: a non-indexed copy of each scan geometry with derived
     * smooth normals (the unlit display never needed normals, and scan
     * exports may not carry them) and the lightmap's uv1 cells. The display
     * geometry is left exactly as it was. */
    function swapMaterials(next, source = next) {
        restoreMaterials();
        mesh = next; bakeSource = source;
        lightmap?.dispose(); lightmap = null;
        bakeSignature = ''; bakePlacement = ''; deferredSince = null; lux = [];
        if (!source) { bounce = { ...idleBounce(), backend: bounce.backend }; return; }
        const nodes = [];
        source.updateWorldMatrix(true, true);
        source.traverse(node => { if (node.isMesh && node.geometry?.attributes?.position) nodes.push(node); });
        const lit = nodes.map(node => {
            const original = node.geometry, addedNormals = !original.attributes.normal;
            if (addedNormals) original.computeVertexNormals();
            const geometry = original.index ? original.toNonIndexed() : original.clone();
            if (addedNormals) original.deleteAttribute('normal');
            return { node, geometry, triangles: Math.floor(geometry.attributes.position.count / 3) };
        });
        const total = lit.reduce((sum, item) => sum + item.triangles, 0), layout = Bake.lightmapLayout(total);
        lightmap = new THREE.DataTexture(new Uint16Array(layout.width * layout.height * 4), layout.width, layout.height, THREE.RGBAFormat, THREE.HalfFloatType);
        lightmap.name = 'lit-bounce-lightmap'; lightmap.channel = 1;
        lightmap.magFilter = THREE.LinearFilter; lightmap.minFilter = THREE.LinearFilter; lightmap.generateMipmaps = false;
        lightmap.needsUpdate = true;
        const triangles = new Float32Array(total * 9), albedo = new Float32Array(total * 3), p = new THREE.Vector3();
        let first = 0;
        for (const { node, geometry, triangles: count } of lit) {
            geometry.setAttribute('uv1', new THREE.BufferAttribute(Bake.lightmapUvs(layout, first, count), 2));
            const position = geometry.attributes.position, sample = albedoSampler(node.material, geometry);
            for (let t = 0; t < count; t++) {
                for (let k = 0; k < 3; k++) {
                    p.fromBufferAttribute(position, t * 3 + k).applyMatrix4(node.matrixWorld);
                    triangles[(first + t) * 9 + k * 3] = p.x; triangles[(first + t) * 9 + k * 3 + 1] = p.y; triangles[(first + t) * 9 + k * 3 + 2] = p.z;
                }
                albedo.set(sample(t, geometry.attributes.uv), (first + t) * 3);
            }
            first += count;
            // Photo and Cloud: the scan only feeds the bake; nothing is swapped.
            if (!mesh) { geometry.dispose(); continue; }
            const made = litMaterialFor(node.material);
            swapped.push({ node, material: node.material, geometry: node.geometry, castShadow: node.castShadow, receiveShadow: node.receiveShadow,
                lit: made.material, depth: made.depth, cut: made.cut, layerAdded: !!pipeline && !node.layers.isEnabled(LIT_LAYER) });
            node.geometry = geometry;
            node.material = made.material;
            node.castShadow = true; node.receiveShadow = true;
            if (pipeline) node.layers.enable(LIT_LAYER);
        }
        bounce = { ...idleBounce(), status: total ? 'building' : 'idle', triangles: total };
        if (!total) return;
        try {
            baker ||= Bake.createBakeClient({ endpoint: createBakeEndpoint(), onScene, onLightmap, onLux, onError, onReceiverLayer });
            bakeScene = baker.setScene(triangles, albedo);
            if (receivers) {
                const { set, positions, normals } = receivers.bakeReceivers();
                baker.setReceivers(set, positions, normals);
            }
        } catch (error) { bounce = { ...bounce, status: 'error', error: String(error?.message || error) }; }
    }
    function restoreMaterials() {
        for (const entry of swapped) {
            entry.lit.dispose?.(); entry.depth?.dispose?.();
            entry.node.geometry.dispose?.();
            if (entry.layerAdded) entry.node.layers.disable(LIT_LAYER);
            entry.node.material = entry.material;
            entry.node.geometry = entry.geometry;
            entry.node.castShadow = entry.castShadow; entry.node.receiveShadow = entry.receiveShadow;
        }
        swapped = []; mesh = null; bakeSource = null;
    }

    function onScene(message) {
        if (!active || message.seq !== bakeScene) return;
        bounce = { ...bounce, status: 'baking', backend: message.backend, receivers: message.receivers, bvhNodes: message.nodes, buildMs: message.buildMs };
        bakeSignature = '';
        changed();
    }
    function onLightmap(message) {
        if (!active || message.scene !== bakeScene || !lightmap || message.width !== lightmap.image.width || message.height !== lightmap.image.height) return;
        lightmap.image.data = message.data;
        lightmap.needsUpdate = true;
        const jobs = message.stats?.jobs || [], spot = jobs.filter(job => job.kind === 'spot'), fixed = jobs.find(job => job.kind === 'static');
        bounce = { ...bounce, status: message.final ? 'ready' : 'baking', backend: message.stats?.backend ?? bounce.backend, quality: message.stats?.quality ?? null,
            progress: message.final ? null : message.stats?.progress ?? null,
            lastBake: message.final && jobs.length ? { spots: spot.length, spotMs: spot.length ? Math.max(...spot.map(job => job.ms)) : null,
                firstPassMs: spot.length ? Math.max(...spot.map(job => job.firstPassMs)) : null, staticMs: fixed?.ms ?? null } : bounce.lastBake };
        changed();
    }
    function onLux(message) {
        if (!active || message.scene !== bakeScene) return;
        lux = message.aims.slice(0, MAX_LUX_AIMS).map(row => Object.freeze({ ...row, modeled: true, measured: false }));
        changed();
    }
    function onReceiverLayer(message) {
        if (!active || !receivers || message.scene !== bakeScene) return;
        if (receivers.setSpotColumn(message.key, message.version, message.data)) {
            bounce = { ...bounce, receiverSpotMs: message.ms ?? null };
            changed();
        }
    }
    function onError(message) {
        if (message.type === 'warning') { bounce = { ...bounce, warning: message.message }; return; }
        bounce = { ...bounce, status: 'error', error: message.message };
        changed();
    }
    const bakeKey = source => `${source.fixtureId ?? ''}:${source.kind}:${source.number ?? 0}`;
    const round = values => values.map(value => (typeof value === 'number' ? +value.toFixed(5) : value));
    function namedAims() {
        return (getNamedAims() || []).slice(0, MAX_LUX_AIMS).map(aim => {
            const point = new THREE.Vector3(...(aim.point || [0, 0, 0])), normal = new THREE.Vector3(...(aim.normal || [0, 0, 1]));
            if (aim.frame?.isObject3D) { aim.frame.updateWorldMatrix(true, false); point.applyMatrix4(aim.frame.matrixWorld); normal.transformDirection(aim.frame.matrixWorld); }
            return { id: String(aim.id ?? ''), label: String(aim.label ?? aim.id ?? ''), point: round(point.toArray()), normal: round(normal.normalize().toArray()) };
        });
    }
    /* Post the exact light set to the bake: placements decide what re-bakes,
     * levels and Kelvin only re-weight. A spot moved while the fixture is
     * still drawing (the 650 ms Set sweep) waits for it to land. */
    function syncBake(exact) {
        if (!baker || !bakeScene || bounce.status === 'building' || bounce.status === 'error') return;
        const allLights = exact.map(source => ({
            key: bakeKey(source), mode: source.kind === 'spot' ? 'vpl' : 'sky',
            origin: round(source.origin.toArray()), direction: round(source.direction.toArray()),
            cosOuter: Math.cos(source.halfAngle), cosInner: Math.cos(source.halfAngle * (1 - source.penumbra)),
            candela: quality === 'reduced' && source.kind === 'zone' ? 0 : source.candela, color: round(Model.cctToLinearRgb(source.kelvin)) }));
        // L4: the basis already carries the downs' and zones' bounce for the
        // receivers; only the live spots are baked (the lux readout keeps all).
        const bakeLights = receivers ? allLights.filter(light => light.mode === 'vpl') : allLights;
        const aims = namedAims();
        const placement = bakeLights.filter(light => light.mode === 'vpl').map(light => light.key + light.origin.join(',') + light.direction.join(',')).join('|');
        const weights = Object.fromEntries(bakeLights.map(light => [light.key, light.color.map(c => c * light.candela)]));
        const next = JSON.stringify([bakeLights.map(light => [light.key, light.origin, light.direction]), weights, aims]);
        if (next === bakeSignature) return;
        if (bakePlacement && placement !== bakePlacement && isPresentationActive()) {
            deferredSince ??= now();
            if (now() - deferredSince < SWEEP_DEFER_MAX_MS) return;
        }
        deferredSince = null; bakeSignature = next; bakePlacement = placement;
        baker.update({ lights: bakeLights.map(({ candela, color, ...light }) => light), weights, exposure: LIT_EXPOSURE, quality: bakeQuality, aims,
            luxLights: allLights.filter(light => light.candela > 0) });
        if (bounce.status === 'ready') bounce = { ...bounce, status: 'baking' };
    }

    function makeLight(source) {
        const light = new THREE.SpotLight(0xffffff, 0, 0, source.halfAngle, source.penumbra, 2);
        light.name = 'lit-' + source.kind + (source.number ? '-' + source.number : '');
        light.userData = { lit_source: source.kind, assumed_lumens: LIT_ASSUMED_LUMENS[source.kind], measured: false,
            shape: source.kind + (source.number || ''), shadow: source.shadow };
        if (source.shadow) {
            const size = LIT_SHADOW_MAP_SIZES[source.shadow];
            light.castShadow = true;
            light.shadow.autoUpdate = false; light.shadow.needsUpdate = true;
            light.shadow.mapSize.set(size, size);
            light.shadow.camera.near = 0.05; light.shadow.camera.far = 14;
            light.shadow.bias = -0.0004; light.shadow.normalBias = 0.02;
            light.shadow.radius = Model.SHADOW_RADIUS[source.shadow];
        }
        if (pipeline) light.layers.set(LIT_LAYER);
        group.add(light, light.target);
        return light;
    }
    function rebuild(sources) {
        for (const light of lights) { light.shadow?.map?.dispose?.(); group.remove(light, light.target); light.dispose?.(); }
        lights = sources.map(makeLight);
        shown = [];
    }
    function applyQuality() {
        for (const light of lights) {
            if (!light.castShadow) continue;
            const full = LIT_SHADOW_MAP_SIZES[light.userData.shadow], size = quality === 'reduced' ? full / 2 : full;
            if (light.shadow.mapSize.x === size) continue;
            light.shadow.mapSize.set(size, size); light.shadow.map?.dispose?.(); light.shadow.map = null; light.shadow.needsUpdate = true;
        }
        renderer.shadowMap.needsUpdate = true;
    }

    /* Partial night adaptation toward the latest meter (WebGPU path), on its
     * own clock: it keeps easing after the lights settle. Until the first
     * meter the view shows the design night and reports itself adapting. */
    function adapt(at) {
        if (!pipeline && !receivers) return;
        if (metered === null) { adapting = true; return; }
        adaptTarget = Exposure.adaptationFromLuminance(metered, LIT_DESIGN_EV100);
        const elapsed = adaptedAt === null ? Infinity : at - adaptedAt;
        const step = Exposure.easeAdaptation(adaptEv, adaptTarget.adaptEv, elapsed, { snap: adaptEv === null || !!reducedMotion?.matches });
        adaptEv = step.ev; adapting = step.adapting; adaptedAt = at;
        if (pipeline && !receivers) pipeline.gain.value = Exposure.adaptationGain(adaptEv);
    }

    /* Follow the fixtures' current exact state. Cheap when nothing changed. */
    function sync(camera = null) {
        if (!active) return false;
        if (receivers) return syncReceivers(camera);
        const currentMesh = getMesh();
        if (currentMesh !== mesh) {
            swapMaterials(currentMesh); renderer.shadowMap.needsUpdate = true;
            for (const light of lights) if (light.castShadow) light.shadow.needsUpdate = true;
        }
        const exact = (getFixtureRoots() || []).flatMap(root => fixtureWorldLights(root, { spotFwhmDeg }));
        const shape = exact.map(source => source.kind + (source.number || '')).join(',');
        if (shape !== lights.map(light => light.userData.shape).join(',')) { rebuild(exact); applyQuality(); }
        syncBake(exact);
        // SPEC: ease each set level and Kelvin on the 160 ms clock. The first
        // sync after enable, a rebuilt light set and reduced motion snap.
        const at = now(), elapsed = easedAt === null ? Infinity : at - easedAt;
        const step = Model.easeLights(exact, shown, elapsed, { snap: elapsed === Infinity || !!reducedMotion?.matches });
        const sources = step.lights;
        easedAt = at; shown = step.shown; easing = step.easing;
        adapt(at);
        const format = values => values.map(value => (typeof value === 'number' ? value.toFixed(5) : String(value))).join(':');
        // Shadow maps depend on where lights are, never on how bright: an
        // eased level re-renders the lights only.
        const placement = sources.map(source => format([...source.origin.toArray(), ...source.direction.toArray()])).join('|') + '|' + quality;
        const key = placement + '#' + sources.map(source => format([source.candela, source.kelvin])).join('|');
        if (key === signature) return false;
        const moved = placement !== placementSignature;
        signature = key; placementSignature = placement;
        sources.forEach((source, index) => {
            const light = lights[index], pose = format([...source.origin.toArray(), ...source.direction.toArray()]);
            // Each shadow map is cached: only a light that moved re-renders its own.
            if (light.castShadow && light.userData.pose !== pose) light.shadow.needsUpdate = true;
            light.userData.pose = pose;
            light.position.copy(source.origin);
            light.target.position.copy(source.origin).addScaledVector(source.direction, 2);
            light.target.updateMatrixWorld(true);
            light.intensity = quality === 'reduced' && source.kind === 'zone' ? 0 : source.intensity;
            kelvinColor(source.kelvin, light.color);
            light.userData.candela = source.candela;
            light.userData.fixture_id = source.fixtureId;
        });
        if (moved) renderer.shadowMap.needsUpdate = true;
        return true;
    }

    /* L4: Photo or Cloud. The same exact lights, bake and ease; the eased
     * lights and the adaptation go to the receiver controller instead of
     * three lights. The meter reads the relit receivers in view. */
    function syncReceivers(camera) {
        const source = getBakeMesh?.() || null;
        if (source !== bakeSource) swapMaterials(null, source);
        const exact = (getFixtureRoots() || []).flatMap(root => fixtureWorldLights(root, { spotFwhmDeg }));
        syncBake(exact);
        const at = now(), elapsed = easedAt === null ? Infinity : at - easedAt;
        const step = Model.easeLights(exact, shown, elapsed, { snap: elapsed === Infinity || !!reducedMotion?.matches });
        easedAt = at; shown = step.shown; easing = step.easing;
        if (camera && at - receiverMeterAt >= Exposure.LIT_METER.periodMs) {
            receiverMeterAt = at;
            const luminance = receivers.meter(camera);
            if (luminance !== null) { metered = luminance; meterSerial++; }
        }
        adapt(at);
        receivers.sync({ lights: step.lights, gain: Exposure.adaptationGain(adaptEv ?? 0), operator });
        return true;
    }
    /* L4: one Lit step for the receivers, from modes.js's splat tick (before
     * renderer.render): follow the lights, then blend if anything changed. */
    function tickReceivers(camera) {
        if (!active || !receivers) return false;
        try { sync(camera); } catch (_) { /* the frame still renders */ }
        return receivers.flush();
    }

    function enable() {
        if (active) return state();
        const target = getMesh() ? null : getReceivers?.() || null;
        if (target) {
            // Photo or Cloud: no lights, shadow maps, render pass or scene hook.
            receivers = target;
            saved = { receiversOnly: true };
            active = true; quality = 'full'; slow = 0; signature = ''; placementSignature = ''; shown = []; easedAt = null;
            adaptEv = null; adaptedAt = null; adapting = false; metered = null; receiverMeterAt = -Infinity; operator = LIT_TONE.view;
            sync();
            return state();
        }
        saved = { enabled: renderer.shadowMap.enabled, type: renderer.shadowMap.type, autoUpdate: renderer.shadowMap.autoUpdate,
            ownBeforeRender: Object.hasOwn(scene, 'onBeforeRender'), onBeforeRender: scene.onBeforeRender };
        renderer.shadowMap.enabled = true;
        renderer.shadowMap.type = THREE.PCFShadowMap;
        renderer.shadowMap.autoUpdate = false;
        scene.add(group);
        active = true; quality = 'full'; slow = 0; signature = ''; placementSignature = ''; shown = []; easedAt = null;
        adaptEv = null; adaptedAt = null; adapting = false; metered = null; operator = pipeline ? LIT_TONE.view : 'linear';
        if (pipeline) {
            // The WebGPU path syncs inside its own render passes.
            pipeline.install();
        } else {
            const previous = saved.onBeforeRender;
            // Sync on the render that uses it, without touching the engine loop.
            scene.onBeforeRender = function litBeforeRender(...args) {
                try { sync(); } catch (_) { /* the frame still renders */ }
                return previous.apply(this, args);
            };
        }
        sync();
        return state();
    }
    function disable() {
        if (!active) return state();
        active = false;
        if (saved?.receiversOnly) {
            receivers = null; restoreMaterials();
            baker?.dispose(); baker = null; bakeScene = 0; lightmap?.dispose(); lightmap = null; lux = [];
            bakeSignature = ''; bakePlacement = ''; deferredSince = null; bounce = idleBounce();
            shown = []; easedAt = null; easing = false;
            adaptEv = null; adaptedAt = null; adapting = false; adaptTarget = { dropEv: 0, adaptEv: 0 }; metered = null;
            saved = null;
            return state();
        }
        pipeline?.uninstall();
        restoreMaterials();
        for (const light of lights) { light.shadow?.map?.dispose?.(); light.dispose?.(); }
        group.remove(...lights.flatMap(light => [light, light.target]));
        lights = []; signature = ''; placementSignature = ''; shown = []; easedAt = null; easing = false;
        baker?.dispose(); baker = null; bakeScene = 0; lightmap?.dispose(); lightmap = null; lux = [];
        bakeSignature = ''; bakePlacement = ''; deferredSince = null; bounce = idleBounce();
        adaptEv = null; adaptedAt = null; adapting = false; adaptTarget = { dropEv: 0, adaptEv: 0 }; metered = null;
        if (pipeline) pipeline.gain.value = 1;
        scene.remove(group);
        renderer.shadowMap.enabled = saved.enabled;
        renderer.shadowMap.type = saved.type;
        renderer.shadowMap.autoUpdate = saved.autoUpdate;
        if (saved.ownBeforeRender) scene.onBeforeRender = saved.onBeforeRender; else delete scene.onBeforeRender;
        saved = null;
        return state();
    }
    /* Declared degradation: a sustained frame rate under the target halves
     * the shadow maps and drops the radial lobes (and their bounce), and
     * says so. */
    function observeFrameRate(fps) {
        // Lit receivers have no per-frame cost to shed (the blend runs on change).
        if (!active || receivers || quality === 'reduced' || !Number.isFinite(fps)) return state();
        slow = fps < LIT_TARGET_FPS ? slow + 1 : 0;
        if (slow >= LIT_SLOW_SAMPLES) { quality = 'reduced'; applyQuality(); signature = ''; sync(); }
        return state();
    }
    /* The view operator, or a comparison view (lit-exposure.mjs). The WebGL
     * path has no tone pass: it is always 'linear'. */
    function setTone(next) {
        if (!LIT_TONE_OPERATORS.includes(next)) throw new RangeError('lit_tone_operator_unknown');
        if (receivers && next !== operator) { operator = next; sync(); }
        else if (pipeline && next !== operator) { operator = next; pipeline.retone(); }
        return state();
    }
    const readLinear = options => (pipeline ? pipeline.readLinear(options) : Promise.resolve(null));
    function state() {
        const adaptive = !!pipeline || !!receivers, ev = adaptive && adaptEv !== null ? adaptEv : 0;
        return Object.freeze({ active, quality, lights: lights.length,
            shadowLights: lights.filter(light => light.castShadow).length, shadowMapSizes: LIT_SHADOW_MAP_SIZES,
            meshSwapped: swapped.length, exposure: LIT_EXPOSURE,
            easing, easeTauMs: Model.LIT_EASE_TAU_MS, dimmingCurve: Model.LIT_DIMMING_CURVE.name,
            pipeline: receivers ? 'per-receiver-display' : pipeline ? 'scene-referred-half-float' : 'display-direct',
            designEv100: LIT_DESIGN_EV100, nightAdaptation: adaptive, adaptationEv: ev, adaptationTargetEv: adaptive ? adaptTarget.adaptEv : 0,
            adaptationDropEv: adaptive ? adaptTarget.dropEv : 0, adapting, ev100: LIT_DESIGN_EV100 - ev,
            meteredCdM2: adaptive ? metered : null, meterSerial,
            tone: Object.freeze({ operator, available: adaptive ? LIT_TONE_OPERATORS : Object.freeze(['linear']),
                presentation: adaptive && Exposure.litTonePresentation(operator) }),
            receivers: receivers ? receivers.state() : null,
            cutaway: swapped.length > 0 && swapped.every(entry => entry.cut),
            ambientFloor: LIT_AMBIENT_FLOOR,
            bounce: Object.freeze({ ...bounce, lastBake: bounce.lastBake ? Object.freeze({ ...bounce.lastBake }) : null }),
            // Photo and Cloud state their own limits (the relit receivers'), Mesh L5's.
            bounceLimit: (receivers && receivers.state().limit) || LIT_BOUNCE_LIMIT, assumedAlbedos: LIT_BOUNCE_ALBEDO,
            lux: Object.freeze(lux.slice()), luxLabel: LIT_LUX_LABEL,
            assumedLumens: LIT_ASSUMED_LUMENS, assumptionSource: LIT_ASSUMPTION_SOURCE, measured: false,
            disclosure: LIT_DISCLOSURE,
            disclosureDetail: LIT_DISCLOSURE_DETAIL + ' ' + Exposure.litExposureDisclosure({ adaptive, designEv100: LIT_DESIGN_EV100 })
                + (receivers ? ' ' + receivers.state().disclosure : '') });
    }
    return { enable, disable, sync, tickReceivers, observeFrameRate, setTone, readLinear, state, group };
}

/* WebGPU buffer rows are padded to 256 bytes; `channels` values per pixel. */
function unpadRows(data, width, height, channels) {
    const row = width * channels;
    if (data.length === row * height) return data;
    const bytesPerValue = data.BYTES_PER_ELEMENT, padded = Math.ceil(row * bytesPerValue / 256) * 256 / bytesPerValue;
    const out = new data.constructor(row * height);
    for (let y = 0; y < height; y++) out.set(data.subarray(y * padded, y * padded + row), y * row);
    return out;
}
