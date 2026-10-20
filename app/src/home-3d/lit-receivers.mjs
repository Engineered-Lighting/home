/* home-3d/lit-receivers.mjs — L4: lit splats and points, the scene glue for
 * lit-basis-blend.mjs ("Hyperreal browser room lighting" L4).
 *
 * Behind the opt-in flag (?lit-splats=1) and disclosed as simulated, Lit can
 * relight Photo (the splats) and Cloud (the point cloud) from L3's additive
 * lighting layer. Nothing here loads unless the flag is on, and every change
 * is reversed exactly on exit:
 *
 *   WebGL (r180, the default)   the blend is a GPGPU pass into an RGBA8
 *       target; Spark reads it through an object modifier (by splat index,
 *       capture alpha kept, SH off while lit), the GL points through a lit
 *       copy of their ShaderMaterial that shares every uniform.
 *   WebGPU (?renderer=webgpu)   the blend is a compute pass into the splat
 *       fork's colour hook (gaussian-splat-webgpu.mjs colorHook) and into a
 *       storage buffer the point sprites read. The WebGL2 backend of the node
 *       renderer declares lit receivers unavailable, as the hook does.
 *
 * The pass runs in modes.js's splat tick, before renderer.render() and never
 * inside it, and only when a weight, the adaptation gain or the operator has
 * changed. The four live spots' columns arrive from L5's bake worker each time
 * a spot moves.
 */
import * as THREE from 'three';

const search = new URL(import.meta.url).search;
const Blend = await import(`./lit-basis-blend.mjs${search}`);
const Loader = await import(`./lit-layer-loader.mjs${search}`);
const Exposure = await import(`./lit-exposure.mjs${search}`);

export const LIT_RECEIVERS_FLAG = 'lit-splats';
// Meter samples: a fixed spread of receivers, kept when they are in view.
const METER_SAMPLES = 4096;
const W = Blend.LIT_TEXTURE_WIDTH;

/* The flag: ?lit-splats=1 on the page, this session only (never stored). */
export function litReceiversOptedIn(location = globalThis.location) {
    try { return new URLSearchParams(location?.search || '').get(LIT_RECEIVERS_FLAG) === '1'; } catch (_) { return false; }
}

/* The layer receiver set for a loaded asset: Photo's PLY or the cloud. */
export function receiverSetFor(kind, sourceName) {
    if (kind === 'points') return 'points';
    return Loader.LIT_LAYER_SETS[sourceName] && Loader.LIT_LAYER_SETS[sourceName] !== 'points' ? Loader.LIT_LAYER_SETS[sourceName] : null;
}

/* Load and verify the layer for one receiver set (lit-layer-loader.mjs). */
export async function loadReceiverLayer({ pack, set, digest, signal = null, fetchImpl, subtle } = {}) {
    if (!pack?.manifest || !pack.baseUrl) throw Object.assign(new Error('lighting_layer_pack_unavailable'), { reason: 'lighting_layer_pack_unavailable' });
    const baseUrl = Loader.layerBaseUrl(pack.baseUrl, pack.manifest.revision);
    return Loader.loadLightingLayer({ baseUrl, pack: pack.manifest, digest, set, signal, fetchImpl, subtle });
}

const fail = reason => Object.assign(new Error(reason), { reason });

/* Receiver positions (the object's own frame) and the count it draws. */
function receiverPositions(kind, object) {
    if (kind === 'points') {
        const position = object?.geometry?.getAttribute?.('position');
        if (!position) throw fail('lit_receivers_points_unavailable');
        return { count: Math.min(position.count, object.geometry.drawRange?.count ?? Infinity), positions: position.array };
    }
    if (object?.isHomeGaussianSplat) return { count: object.numSplats, positions: object._cpuCentres };
    if (typeof object?.forEachSplat === 'function' && Number.isSafeInteger(object.numSplats)) {
        const positions = new Float32Array(object.numSplats * 3);
        object.forEachSplat((index, center) => { positions[index * 3] = center.x; positions[index * 3 + 1] = center.y; positions[index * 3 + 2] = center.z; });
        return { count: object.numSplats, positions };
    }
    throw fail('lit_receivers_splat_unavailable');
}

/* A lit receiver controller for one loaded object ('splat' or 'points').
 * Resolves with { available: false, reason } when this renderer, asset or
 * layer cannot be lit. */
export async function createLitReceivers({ renderer, kind, object, layer, whenIdle = () => Promise.resolve() } = {}) {
    if (kind !== 'splat' && kind !== 'points') throw fail('lit_receivers_kind_unknown');
    const node = !!renderer?.isWebGPURenderer;
    if (node && !renderer.backend?.isWebGPUBackend) return { available: false, reason: 'lit_receivers_unavailable_on_webgl2_backend' };
    const { count, positions } = receiverPositions(kind, object);
    if (!layer || layer.count !== count) return { available: false, reason: 'lighting_layer_count_mismatch' };
    const packed = Blend.packReceiverSet(layer);
    const spots = new Uint32Array(count * Blend.LIT_SPOT_WORDS);
    const weights = new Float32Array(Blend.LIT_WEIGHT_VEC4S * 4);
    const spotSlots = new Map(), spotVersions = new Map();
    let operator = Exposure.LIT_TONE.view, signature = '', dirty = true, spotsDirty = false, attached = false, disposed = false;
    let blends = 0, lastBlendMs = null, matched = [], unbaked = [];

    // Receivers in the world for the bake and the meter (rigid registration).
    object.updateWorldMatrix?.(true, false);
    const matrix = object.matrixWorld ?? new THREE.Matrix4(), normalMatrix = new THREE.Matrix3().getNormalMatrix(matrix);
    const localNormals = Loader.decodeNormalsOct(layer.normalOct, count);
    function worldReceivers() {
        const P = new Float32Array(count * 3), N = new Float32Array(count * 3), v = new THREE.Vector3();
        for (let i = 0; i < count; i++) {
            v.fromArray(positions, i * 3).applyMatrix4(matrix).toArray(P, i * 3);
            v.fromArray(localNormals, i * 3).applyMatrix3(normalMatrix).normalize().toArray(N, i * 3);
        }
        return { positions: P, normals: N };
    }
    const sampleIndex = new Uint32Array(Math.min(METER_SAMPLES, count));
    for (let s = 0; s < sampleIndex.length; s++) sampleIndex[s] = Math.floor((s + 0.5) * count / sampleIndex.length);
    const samplePositions = new Float32Array(sampleIndex.length * 3);
    {
        const v = new THREE.Vector3();
        sampleIndex.forEach((index, s) => v.fromArray(positions, index * 3).applyMatrix4(matrix).toArray(samplePositions, s * 3));
    }

    const gpu = node ? await createNodeBlend() : await createGlBlend();
    if (gpu.available === false) return gpu;

    // ---- WebGL: GPGPU into an RGBA8 target ----------------------------------
    async function createGlBlend() {
        const height = Math.ceil(count / W);
        const texture = (words, offset, stride) => {
            const data = new Uint32Array(W * height * 4);
            for (let i = 0; i < count; i++) data.set(words.subarray(i * stride + offset, i * stride + offset + 4), i * 4);
            const map = new THREE.DataTexture(data, W, height, THREE.RGBAIntegerFormat, THREE.UnsignedIntType);
            map.internalFormat = 'RGBA32UI';
            map.minFilter = THREE.NearestFilter; map.magFilter = THREE.NearestFilter; map.generateMipmaps = false;
            map.needsUpdate = true;
            return map;
        };
        const maps = { uMeta: texture(packed.meta, 0, 4), uBasisA: texture(packed.basis, 0, 8), uBasisB: texture(packed.basis, 4, 8),
            uSpotA: texture(spots, 0, 8), uSpotB: texture(spots, 4, 8) };
        const target = new THREE.WebGLRenderTarget(W, height, { format: THREE.RGBAFormat, type: THREE.UnsignedByteType, depthBuffer: false,
            stencilBuffer: false, generateMipmaps: false, minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter });
        target.texture.name = 'lit-receivers-relit';
        const weightValues = Array.from({ length: Blend.LIT_WEIGHT_VEC4S }, () => new THREE.Vector4());
        const uniforms = { uWeights: { value: weightValues }, uCount: { value: count },
            ...Object.fromEntries(Object.entries(maps).map(([name, map]) => [name, { value: map }])) };
        const quadScene = new THREE.Scene(), camera = new THREE.Camera();
        const material = new THREE.ShaderMaterial({ name: 'lit-receivers-blend', uniforms,
            vertexShader: Blend.LIT_BLEND_VERTEX_GLSL, fragmentShader: Blend.litBlendFragmentGlsl({ operator }),
            depthTest: false, depthWrite: false, blending: THREE.NoBlending, toneMapped: false });
        const quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), material);
        quad.frustumCulled = false;
        quadScene.add(quad);
        let restore = null;
        return {
            backend: 'webgl-gpgpu',
            retone() { material.fragmentShader = Blend.litBlendFragmentGlsl({ operator }); material.needsUpdate = true; },
            uploadSpots() {
                for (const [name, offset] of [['uSpotA', 0], ['uSpotB', 4]]) {
                    const data = maps[name].image.data;
                    for (let i = 0; i < count; i++) data.set(spots.subarray(i * 8 + offset, i * 8 + offset + 4), i * 4);
                    maps[name].needsUpdate = true;
                }
            },
            run() {
                for (let k = 0; k < Blend.LIT_WEIGHT_VEC4S; k++) weightValues[k].fromArray(weights, k * 4);
                const previous = renderer.getRenderTarget(), autoClear = renderer.autoClear, autoReset = renderer.info.autoReset;
                try {
                    renderer.info.autoReset = false;
                    renderer.autoClear = true;
                    renderer.setRenderTarget(target);
                    renderer.render(quadScene, camera);
                } finally {
                    renderer.setRenderTarget(previous); renderer.autoClear = autoClear; renderer.info.autoReset = autoReset;
                }
                if (kind === 'splat') object.needsUpdate = true;
            },
            async attach() {
                if (kind === 'points') {
                    const { createLitPointsMaterial } = await import(`./pointcloud.js${search}`);
                    const lit = createLitPointsMaterial(object.material, { texture: target.texture, width: W });
                    const original = object.material;
                    object.material = lit;
                    restore = () => { object.material = original; lit.dispose(); };
                    return;
                }
                const { dyno } = await import('@sparkjsdev/spark');
                const relit = dyno.dynoSampler2D(target.texture, 'litRelit'), width = dyno.dynoInt(W, 'litRelitWidth');
                const graph = dyno.dynoBlock({ gsplat: dyno.Gsplat }, { gsplat: dyno.Gsplat }, ({ gsplat }) => {
                    const { index, rgba } = dyno.splitGsplat(gsplat).outputs;
                    const lit = new dyno.Dyno({
                        inTypes: { index: 'int', rgba: 'vec4', relit: 'sampler2D', width: 'int' }, outTypes: { rgba: 'vec4' },
                        inputs: { index, rgba, relit, width },
                        statements: ({ inputs: i, outputs: o }) => [
                            `${o.rgba} = vec4(texelFetch(${i.relit}, ivec2(${i.index} % ${i.width}, ${i.index} / ${i.width}), 0).rgb, ${i.rgba}.a);`],
                    }).outputs.rgba;
                    return { gsplat: dyno.combineGsplat({ gsplat, rgba: lit }) };
                });
                await whenIdle();
                const saved = { modifiers: object.objectModifiers, own: Object.hasOwn(object, 'objectModifiers'), maxSh: object.maxSh };
                object.objectModifiers = [...(object.objectModifiers || []), graph];
                // The capture's view-dependent residual is its daylight sheen.
                object.maxSh = 0;
                object.updateGenerator();
                object.needsUpdate = true;
                restore = async () => {
                    await whenIdle();
                    object.objectModifiers = saved.modifiers;
                    object.maxSh = saved.maxSh;
                    object.updateGenerator();
                    object.needsUpdate = true;
                };
            },
            async detach() { const undo = restore; restore = null; await undo?.(); },
            async readback(n) {
                const rows = Math.ceil(n / W), out = new Uint8Array(W * rows * 4), gl = renderer.getContext();
                // The async frame readback may leave a pixel-pack buffer bound;
                // a synchronous read needs none (else INVALID_OPERATION).
                const pack = gl.getParameter(gl.PIXEL_PACK_BUFFER_BINDING);
                gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null);
                try { renderer.readRenderTargetPixels(target, 0, 0, W, rows, out); } finally { gl.bindBuffer(gl.PIXEL_PACK_BUFFER, pack); }
                return Array.from({ length: n }, (_, i) => [out[i * 4], out[i * 4 + 1], out[i * 4 + 2]].map(v => v / 255));
            },
            dispose() {
                target.dispose(); material.dispose(); quad.geometry.dispose();
                for (const map of Object.values(maps)) map.dispose();
            },
            memoryBytes: W * height * 4 * 4 * 5 + W * height * 4,
        };
    }

    // ---- WebGPU: compute into storage ---------------------------------------
    async function createNodeBlend() {
        const T = await import('three/tsl');
        const hook = kind === 'splat' ? object.colorHook?.() : null;
        if (kind === 'splat' && !hook?.available) return { available: false, reason: hook?.reason || 'splat_relit_unavailable' };
        const attribute = (array, size) => new THREE.StorageBufferAttribute(array, size);
        const metaAttr = attribute(packed.meta, 4), basisAttr = attribute(packed.basis, 4), spotAttr = attribute(spots, 4);
        const weightAttr = attribute(weights, 4);
        const read = (attr, type, n) => T.storage(attr, type, n).toReadOnly();
        const buffers = { meta: read(metaAttr, 'uvec4', count), basis: read(basisAttr, 'uvec4', count * 2),
            spots: read(spotAttr, 'uvec4', count * 2), weights: read(weightAttr, 'vec4', Blend.LIT_WEIGHT_VEC4S) };
        let output = null, write;
        if (hook) write = (i, rgb) => hook.write.element(i).assign(T.vec4(rgb, 0));
        else {
            output = attribute(new Float32Array(count * 4), 4);
            const store = T.storage(output, 'vec4', count);
            write = (i, rgb) => store.element(i).assign(T.vec4(rgb, 1));
        }
        let compute = null;
        const build = () => { compute = Blend.litBlendComputeNode(T, { count, ...buffers, operator, write }); };
        build();
        let restore = null;
        return {
            backend: 'webgpu-compute',
            retone: build,
            uploadSpots() { spotAttr.needsUpdate = true; },
            run() { weightAttr.needsUpdate = true; renderer.compute(compute); },
            async attach() {
                if (hook) { hook.setActive(true); restore = () => hook.setActive(false); return; }
                const sprite = object.children?.find(child => child.name === 'apartment-points-webgpu');
                if (!sprite) throw fail('lit_receivers_points_sprite_unavailable');
                const ports = await import(`./webgpu-material-ports.mjs${search}`);
                const position = object.geometry.getAttribute('position'), intensity = object.geometry.getAttribute('intensity');
                const values = new Float32Array(count);
                for (let i = 0; i < count; i++) values[i] = intensity.getX(i);
                const lit = ports.createPointSpriteMaterial(object.material, {
                    positions: new THREE.InstancedBufferAttribute(position.array, 3), intensity: new THREE.InstancedBufferAttribute(values, 1),
                    litColor: T.storage(output, 'vec4', count).toReadOnly() });
                const original = sprite.material;
                sprite.material = lit;
                restore = () => { sprite.material = original; lit.dispose(); };
            },
            async detach() { const undo = restore; restore = null; await undo?.(); },
            async readback(n) {
                const values = new Float32Array(await renderer.getArrayBufferAsync(hook ? hook.attribute : output));
                return Array.from({ length: n }, (_, i) => [values[i * 4], values[i * 4 + 1], values[i * 4 + 2]]);
            },
            dispose() { compute?.dispose?.(); },
            memoryBytes: count * (16 + 32 + 32) + (output ? count * 16 : 0),
        };
    }

    return {
        available: true, kind, set: layer.set, count, backend: gpu.backend,
        async attach() {
            if (attached || disposed) return;
            await gpu.attach();
            attached = true; dirty = true;
        },
        async detach() {
            if (!attached) return;
            attached = false;
            await gpu.detach();
        },
        dispose() {
            if (disposed) return;
            disposed = true; attached = false;
            gpu.dispose();
        },
        /* World receivers for L5's bake (fresh arrays, transferable). */
        bakeReceivers() { return { set: layer.set, ...worldReceivers() }; },
        /* A live spot's column from the bake (half rgb per receiver). */
        setSpotColumn(key, version, halves) {
            if (disposed || !(halves instanceof Uint16Array) || halves.length !== count * 3) return false;
            if ((spotVersions.get(key) ?? -1) > version) return false;
            let slot = spotSlots.get(key);
            if (slot === undefined) {
                if (spotSlots.size >= Blend.LIT_SPOT_SLOTS) return false;
                slot = spotSlots.size; spotSlots.set(key, slot);
            }
            spotVersions.set(key, version);
            Blend.writeSpotColumn(spots, count, slot, halves);
            spotsDirty = true; dirty = true; signature = '';
            return true;
        },
        /* Follow the eased lights, the adaptation gain and the operator.
         * Cheap when nothing changed; returns whether a blend is due. */
        sync({ lights = [], gain = 1, operator: next = operator } = {}) {
            if (disposed) return false;
            if (next !== operator) { operator = next; gpu.retone(); dirty = true; }
            const result = Blend.blendWeights({ lights, columns: layer.columnsMeta, columnScale: layer.columnScale, spotSlots, gain }, weights);
            matched = result.matched; unbaked = result.unbaked;
            const next2 = Array.from(weights, value => value.toPrecision(7)).join(',');
            if (next2 !== signature) { signature = next2; dirty = true; }
            return dirty;
        },
        /* Run the blend if anything changed: from modes.js's splat tick. */
        flush() {
            if (!attached || disposed || !dirty) return false;
            const t0 = performance.now();
            if (spotsDirty) { gpu.uploadSpots(); spotsDirty = false; }
            gpu.run();
            dirty = false; blends++; lastBlendMs = performance.now() - t0;
            return true;
        },
        /* Proofs only: the first n receivers' blended display colours as
         * the GPU wrote them, and the CPU twin's for the same weights. */
        async readback(n = Math.min(count, 64)) {
            const gpuRows = await gpu.readback(Math.min(n, count));
            const cpu = gpuRows.map((_, i) => Blend.blendReceiverDisplay(packed, i, weights, spots, { operator }));
            return { gpu: gpuRows, cpu };
        },
        /* Modeled scene luminance (cd/m²) of the receivers in view, metered
         * like lit-exposure.mjs's LIT_METER; null with none in view. */
        meter(camera) {
            if (!camera?.isCamera || disposed) return null;
            camera.updateMatrixWorld?.();
            const v = new THREE.Vector3(), samples = [];
            for (let s = 0; s < sampleIndex.length; s++) {
                v.fromArray(samplePositions, s * 3).applyMatrix4(camera.matrixWorldInverse);
                if (v.z >= 0 || (camera.isPerspectiveCamera && -v.z < camera.near)) continue;
                v.applyMatrix4(camera.projectionMatrix);
                if (Math.abs(v.x) > 1 || Math.abs(v.y) > 1) continue;
                samples.push(Blend.receiverLuminance(packed, sampleIndex[s], weights, spots));
            }
            return Exposure.meterLuminance(samples);
        },
        state() {
            return Object.freeze({ available: true, kind, set: layer.set, source: layer.source, count, backend: gpu.backend, attached,
                operator, blends, lastBlendMs, columns: layer.columns, matched: matched.length, unbaked: Object.freeze(unbaked.slice()),
                liveSpots: spotSlots.size, gpuBytes: gpu.memoryBytes, layerBytes: layer.bytes, binding: 'sha256-verified',
                provenance: layer.provenance, simulated: true, measured: false, disclosure: Blend.LIT_RECEIVERS_DISCLOSURE });
        },
    };
}
