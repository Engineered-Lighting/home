/* home-3d/lit-view.mjs — Tier-0 Lit (APT-V01; SPEC Lit view; LIGHT-AND-BODY-RESEARCH Tier 0),
 * with L2's scene-referred exposure and tone pipeline on the WebGPU path.
 *
 * Relights the existing scan mesh with simulated light from the virtual
 * fixtures, using built-in three.js lights only: SpotLights and built-in PCF
 * shadow maps. The light model (assumed lumens to candela by beam angle, the
 * assumed dimming curve, constant-luminance colour, zone directions, the
 * 160 ms ease, the fixed night pre-exposure) lives in the renderer-independent
 * lit-model.mjs; the exposure and tone policy in lit-exposure.mjs. Lights ease
 * toward each set level on the fixtures' 160 ms clock; the aim follows the
 * exact metadata.
 *
 * Spot shadows are crisp and Down shadows soft (1024 PCF); each radial zone
 * is its own unshadowed lobe. Light intensity is candela x the fixed design-
 * night pre-exposure on both paths, and the renderer's tone mapping is left
 * untouched, so the cloud, the splat, the orbs and every overlay keep their
 * exact unlit look.
 *
 *   WebGL (r180, the default)   MeshStandardMaterial, drawn straight to the
 *       canvas with no tone pass: Tier-0 as it was. With no night adaptation
 *       here, the assumed ambient floor is shown night-adapted.
 *   WebGPU path (?renderer=webgpu, either backend)   MeshStandardNodeMaterial
 *       with the shared TSL dollhouse cut (no GLSL patch anywhere). The
 *       lit mesh renders scene-referred into a half-float target; one TSL pass
 *       applies the mesopic shift, the partial night adaptation and AgX with
 *       a lifted toe, then encodes for the canvas; everything else (orbs,
 *       beams, overlays) draws on top over the mesh's depth, never tone
 *       mapped. The frame loop is untouched: the passes run inside
 *       renderer.render for the engine's own scene while Lit is on.
 *
 * This is a modeled view, never photometry: the lumen ratings and the night
 * ambient are assumptions disclosed with the view, materials are assumed (the
 * scan's own colours stand in for albedo), and night adaptation is
 * presentation, not lux.
 *
 * Reversible and confined to the mesh: Lit swaps the mesh materials for lit
 * copies and restores the originals on exit. Shadow maps ignore the cutaway,
 * so the camera's dollhouse cut never changes world shadowing. The scene is
 * static, so shadow maps re-render only on change.
 *
 * One bounce (APT-V02) is temporary instant radiosity (see lit-model.mjs):
 * eight built-in SpotLights placed at the strongest lit patches, found by
 * rays against the collision proxy.
 */
import * as THREE from 'three';

const Model = await import(`./lit-model.mjs${new URL(import.meta.url).search}`);
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
    LIT_BOUNCE_LIMIT, LIT_BOUNCE_COUNT, LIT_BOUNCE_RANGE_M, LIT_BOUNCE_ALBEDO, candelaFromLumens, bouncePatches, litDimmingFraction } = Model;
export const { LIT_ADAPTATION, LIT_AMBIENT_FLOOR, LIT_TONE, LIT_TONE_OPERATORS } = Exposure;
export const LIT_SHADOW_MAP_SIZE = 1024;
// Below LIT_TARGET_FPS for this many consecutive 500 ms stats, Lit declares
// a reduced quality instead of silently dropping frames.
const LIT_SLOW_SAMPLES = 4;
// Scan colours carry the capture's own lighting; scale them toward an albedo.
const ASSUMED_ALBEDO_SCALE = 0.8;
/* WebGPU path: the layer the scene-referred pass draws (the lit mesh and its
 * lights). The mesh keeps layer 0 too, so picking and readbacks are unchanged. */
export const LIT_LAYER = 29;
// Tier-0's pre-exposure is the fixed design night (EV100 4.45).
export const LIT_DESIGN_EV100 = Exposure.ev100FromExposure(Model.LIT_EXPOSURE);

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

export function createLitView({ scene, renderer, getMesh, getFixtureRoots, getCollision = () => null, spotFwhmDeg = 20,
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
    // The 160 ms ease: what each light shows, and the clock of the last step.
    let placementSignature = '', shown = [], easedAt = null, easing = false;
    // Partial night adaptation (WebGPU path): EV above the design night, toward
    // the latest meter of the scene-referred target (null until the first).
    let adaptEv = null, adaptTarget = { dropEv: 0, adaptEv: 0 }, adaptedAt = null, adapting = false, metered = null, meterSerial = 0;
    let operator = nodePath ? LIT_TONE.view : 'linear';
    // Bounce lights: a fixed set, so a change never recompiles.
    const bounce = Array.from({ length: LIT_BOUNCE_COUNT }, (_, index) => {
        const light = new THREE.SpotLight(0xffffff, 0, LIT_BOUNCE_RANGE_M, Model.LIT_BOUNCE_CONE, 1, 2);
        light.name = 'lit-bounce-' + (index + 1);
        light.userData = { lit_source: 'bounce', measured: false };
        group.add(light, light.target);
        return light;
    });
    if (nodePath) for (const light of [floor, ...bounce]) light.layers.set(LIT_LAYER);
    const bounceTint = new THREE.Color(...Model.LIT_BOUNCE_TINT);
    let bounceSources = 0;
    const raycaster = new THREE.Raycaster(), rayHits = new Map();
    // First surface along a light's axis, against the decimated collision
    // proxy (the mesh as a fallback). Cached per ray: only a moved light
    // casts again; level and colour changes reuse the hit.
    function castRay(origin, direction) {
        const target = getCollision() || mesh;
        if (!target) return null;
        const key = [...origin.toArray(), ...direction.toArray()].map(value => value.toFixed(4)).join(',') + '|' + target.uuid;
        if (rayHits.has(key)) return rayHits.get(key);
        raycaster.set(origin, direction.clone().normalize()); raycaster.near = 0.02; raycaster.far = 30;
        const first = raycaster.intersectObject(target, true).find(candidate => candidate.point && candidate.face);
        let hit = null;
        if (first) {
            const normal = first.face.normal.clone().transformDirection(first.object.matrixWorld);
            if (normal.dot(direction) > 0) normal.negate();
            hit = { point: first.point.toArray(), normal: normal.toArray() };
        }
        if (rayHits.size > 256) rayHits.clear();
        rayHits.set(key, hit);
        return hit;
    }

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
            roughness: 0.88, metalness: 0, side: source?.side ?? THREE.FrontSide };
        const made = pipeline ? pipeline.materialFor(source, options) : { material: new THREE.MeshStandardMaterial(options), depth: null, cut: false };
        made.material.name = 'lit-view-assumed-material';
        return made;
    }
    function swapMaterials(next) {
        restoreMaterials();
        mesh = next;
        if (!mesh) return;
        mesh.traverse(node => {
            if (!node.isMesh) return;
            // The unlit display never needed normals, and scan exports may not
            // carry them. Lit derives them for its lifetime only.
            const addedNormals = !node.geometry.attributes.normal;
            if (addedNormals) node.geometry.computeVertexNormals();
            const made = litMaterialFor(node.material);
            swapped.push({ node, material: node.material, castShadow: node.castShadow, receiveShadow: node.receiveShadow, addedNormals,
                lit: made.material, depth: made.depth, cut: made.cut, layerAdded: !!pipeline && !node.layers.isEnabled(LIT_LAYER) });
            node.material = made.material;
            node.castShadow = true; node.receiveShadow = true;
            if (pipeline) node.layers.enable(LIT_LAYER);
        });
    }
    function restoreMaterials() {
        for (const entry of swapped) {
            entry.lit.dispose?.(); entry.depth?.dispose?.();
            entry.node.material = entry.material;
            entry.node.castShadow = entry.castShadow; entry.node.receiveShadow = entry.receiveShadow;
            if (entry.addedNormals) entry.node.geometry.deleteAttribute('normal');
            if (entry.layerAdded) entry.node.layers.disable(LIT_LAYER);
        }
        swapped = []; mesh = null;
    }
    /* Shadow maps re-render only when asked. r180 reads the renderer's flag;
     * r186 reads each light's, so the WebGPU path sets both. */
    function markShadows() {
        renderer.shadowMap.needsUpdate = true;
        if (pipeline) for (const light of lights) if (light.castShadow) light.shadow.needsUpdate = true;
    }
    function makeLight(source) {
        const light = new THREE.SpotLight(0xffffff, 0, 0, source.halfAngle, source.penumbra, 2);
        light.name = 'lit-' + source.kind + (source.number ? '-' + source.number : '');
        light.userData = { lit_source: source.kind, assumed_lumens: LIT_ASSUMED_LUMENS[source.kind], measured: false,
            shape: source.kind + (source.number || '') };
        if (source.shadow) {
            light.castShadow = true;
            light.shadow.mapSize.set(LIT_SHADOW_MAP_SIZE, LIT_SHADOW_MAP_SIZE);
            light.shadow.camera.near = 0.05; light.shadow.camera.far = 14;
            light.shadow.bias = -0.0004; light.shadow.normalBias = 0.02;
            light.shadow.radius = Model.SHADOW_RADIUS[source.shadow];
            if (pipeline) { light.shadow.autoUpdate = false; light.shadow.needsUpdate = true; }
        }
        if (pipeline) light.layers.set(LIT_LAYER);
        group.add(light, light.target);
        return light;
    }
    function rebuild(sources) {
        rayHits.clear();
        for (const light of lights) { light.shadow?.map?.dispose?.(); group.remove(light, light.target); light.dispose?.(); }
        lights = sources.map(makeLight);
        shown = [];
    }
    function applyQuality() {
        const size = quality === 'reduced' ? LIT_SHADOW_MAP_SIZE / 2 : LIT_SHADOW_MAP_SIZE;
        for (const light of lights) {
            if (!light.castShadow || light.shadow.mapSize.x === size) continue;
            light.shadow.mapSize.set(size, size); light.shadow.map?.dispose?.(); light.shadow.map = null;
        }
        markShadows();
    }
    /* Partial night adaptation toward the latest meter (WebGPU path), on its
     * own clock: it keeps easing after the lights settle. Until the first
     * meter the view shows the design night and reports itself adapting. */
    function adapt(at) {
        if (!pipeline) return;
        if (metered === null) { adapting = true; return; }
        adaptTarget = Exposure.adaptationFromLuminance(metered, LIT_DESIGN_EV100);
        const elapsed = adaptedAt === null ? Infinity : at - adaptedAt;
        const step = Exposure.easeAdaptation(adaptEv, adaptTarget.adaptEv, elapsed, { snap: adaptEv === null || !!reducedMotion?.matches });
        adaptEv = step.ev; adapting = step.adapting; adaptedAt = at;
        pipeline.gain.value = Exposure.adaptationGain(adaptEv);
    }

    /* Follow the fixtures' current exact state. Cheap when nothing changed. */
    function sync() {
        if (!active) return false;
        const currentMesh = getMesh();
        if (currentMesh !== mesh) { swapMaterials(currentMesh); markShadows(); }
        const exact = (getFixtureRoots() || []).flatMap(root => fixtureWorldLights(root, { spotFwhmDeg }));
        const shape = exact.map(source => source.kind + (source.number || '')).join(',');
        if (shape !== lights.map(light => light.userData.shape).join(',')) { rebuild(exact); applyQuality(); }
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
            const light = lights[index];
            light.position.copy(source.origin);
            light.target.position.copy(source.origin).addScaledVector(source.direction, 2);
            light.target.updateMatrixWorld(true);
            light.intensity = quality === 'reduced' && source.kind === 'zone' ? 0 : source.intensity;
            kelvinColor(source.kelvin, light.color);
            light.userData.candela = source.candela;
            light.userData.fixture_id = source.fixtureId;
        });
        const patches = bouncePatches(sources, sources.map(source => (source.level > 0 ? castRay(source.origin, source.direction) : null)),
            { up: [0, 1, 0] });
        bounceSources = patches.length;
        bounce.forEach((light, index) => {
            const patch = patches[index];
            if (!patch) { light.intensity = 0; light.userData.patch = null; return; }
            light.position.fromArray(patch.position);
            light.target.position.fromArray(patch.position).add(new THREE.Vector3().fromArray(patch.normal));
            light.target.updateMatrixWorld(true);
            kelvinColor(patch.kelvin, light.color).multiply(bounceTint);
            light.intensity = patch.intensity;
            light.userData.patch = { kind: patch.kind, reflected_lm: patch.reflected, albedo: patch.albedo, assumed: true };
        });
        if (moved) markShadows();
        return true;
    }

    function enable() {
        if (active) return state();
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
        pipeline?.uninstall();
        restoreMaterials();
        for (const light of lights) { light.shadow?.map?.dispose?.(); light.dispose?.(); }
        group.remove(...lights.flatMap(light => [light, light.target]));
        lights = []; signature = ''; placementSignature = ''; shown = []; easedAt = null; easing = false; rayHits.clear(); bounceSources = 0;
        adaptEv = null; adaptedAt = null; adapting = false; adaptTarget = { dropEv: 0, adaptEv: 0 }; metered = null;
        if (pipeline) pipeline.gain.value = 1;
        for (const light of bounce) light.intensity = 0;
        scene.remove(group);
        renderer.shadowMap.enabled = saved.enabled;
        renderer.shadowMap.type = saved.type;
        renderer.shadowMap.autoUpdate = saved.autoUpdate;
        if (saved.ownBeforeRender) scene.onBeforeRender = saved.onBeforeRender; else delete scene.onBeforeRender;
        saved = null;
        return state();
    }
    /* Declared degradation: a sustained frame rate under the target halves
     * the shadow maps and drops the radial lobes, and says so. */
    function observeFrameRate(fps) {
        if (!active || quality === 'reduced' || !Number.isFinite(fps)) return state();
        slow = fps < LIT_TARGET_FPS ? slow + 1 : 0;
        if (slow >= LIT_SLOW_SAMPLES) { quality = 'reduced'; applyQuality(); signature = ''; sync(); }
        return state();
    }
    /* The view operator, or a comparison view (lit-exposure.mjs). The WebGL
     * path has no tone pass: it is always 'linear'. */
    function setTone(next) {
        if (!LIT_TONE_OPERATORS.includes(next)) throw new RangeError('lit_tone_operator_unknown');
        if (pipeline && next !== operator) { operator = next; pipeline.retone(); }
        return state();
    }
    const readLinear = options => (pipeline ? pipeline.readLinear(options) : Promise.resolve(null));
    function state() {
        const ev = pipeline && adaptEv !== null ? adaptEv : 0;
        return Object.freeze({ active, quality, lights: lights.length,
            shadowLights: lights.filter(light => light.castShadow).length,
            meshSwapped: swapped.length, exposure: LIT_EXPOSURE,
            easing, easeTauMs: Model.LIT_EASE_TAU_MS, dimmingCurve: Model.LIT_DIMMING_CURVE.name,
            pipeline: pipeline ? 'scene-referred-half-float' : 'display-direct',
            designEv100: LIT_DESIGN_EV100, nightAdaptation: !!pipeline, adaptationEv: ev, adaptationTargetEv: pipeline ? adaptTarget.adaptEv : 0,
            adaptationDropEv: pipeline ? adaptTarget.dropEv : 0, adapting, ev100: LIT_DESIGN_EV100 - ev,
            meteredCdM2: pipeline ? metered : null, meterSerial,
            tone: Object.freeze({ operator, available: pipeline ? LIT_TONE_OPERATORS : Object.freeze(['linear']),
                presentation: !!pipeline && Exposure.litTonePresentation(operator) }),
            cutaway: swapped.length > 0 && swapped.every(entry => entry.cut),
            ambientFloor: LIT_AMBIENT_FLOOR,
            bounceLights: bounce.length, bounceSources, bounceRangeM: LIT_BOUNCE_RANGE_M, bounceLimit: LIT_BOUNCE_LIMIT,
            assumedAlbedos: LIT_BOUNCE_ALBEDO,
            assumedLumens: LIT_ASSUMED_LUMENS, assumptionSource: LIT_ASSUMPTION_SOURCE, measured: false,
            disclosure: LIT_DISCLOSURE,
            disclosureDetail: LIT_DISCLOSURE_DETAIL + ' ' + Exposure.litExposureDisclosure({ adaptive: !!pipeline, designEv100: LIT_DESIGN_EV100 }) });
    }
    return { enable, disable, sync, observeFrameRate, setTone, readLinear, state, group };
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
