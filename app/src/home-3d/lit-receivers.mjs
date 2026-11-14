/* home-3d/lit-receivers.mjs — L4: lit splats and points, the scene glue for
 * lit-basis-blend.mjs ("Hyperreal browser room lighting" L4).
 *
 * Disclosed as simulated, Lighting relights Photo (the splats) and Cloud
 * (the point cloud) from L3's additive lighting layer. Nothing here loads
 * until Lighting is turned on, and every change is reversed exactly when it
 * is turned off or the view changes:
 *
 *   WebGL (r180, the default)   the blend is a GPGPU pass into an RGBA16F
 *       target of pre-exposed linear radiance; Spark reads it through an
 *       object modifier (by splat index, capture alpha kept, SH off while
 *       lit) into an fp16 accumulator (a SparkRenderer built with
 *       accumExtSplats, swapped in while lit), the GL points through a lit
 *       copy of their ShaderMaterial that shares every uniform.
 *   WebGPU (?renderer=webgpu)   the blend is a compute pass into the splat
 *       fork's colour hook (gaussian-splat-webgpu.mjs colorHook) and into a
 *       storage buffer the point sprites read. The WebGL2 backend of the node
 *       renderer declares lit receivers unavailable, as the hook does.
 *
 * Splat and point colours stay scene-referred HDR; lit-frame.mjs composites
 * them into one half-float frame and runs the camera look once per pixel.
 * The pass runs in modes.js's splat tick, before renderer.render() and never
 * inside it, and only when a weight has changed. The live spot columns arrive
 * from L5's bake worker each time a spot moves.
 */
import * as THREE from 'three';

const search = new URL(import.meta.url).search;
const Blend = await import(`./lit-basis-blend.mjs${search}`);
const Loader = await import(`./lit-layer-loader.mjs${search}`);
const Exposure = await import(`./lit-exposure.mjs${search}`);

// Meter samples: a fixed spread of receivers, kept when they are in view.
const METER_SAMPLES = 4096;
const W = Blend.LIT_TEXTURE_WIDTH;
const MAX_RECEIVER_GPU_BYTES = 160 * 1024 * 1024;

export function receiverGpuLayout(count, { webgpu, useBasis, spotCapacity, limits } = {}) {
    if (!Number.isSafeInteger(count) || count <= 0 || ![Blend.LIT_SPOT_SLOTS, Blend.LIT_ECHO_SPOT_SLOTS].includes(spotCapacity)) return null;
    const height = Math.ceil(count / W), stride = webgpu ? count : W * height;
    const spotBytes = stride * spotCapacity * Blend.LIT_SPOT_WORDS * Uint32Array.BYTES_PER_ELEMENT;
    const basisBytes = useBasis ? stride * Blend.LIT_BASIS_WORDS * Uint32Array.BYTES_PER_ELEMENT : 0;
    const metaBytes = stride * Blend.LIT_META_WORDS * Uint32Array.BYTES_PER_ELEMENT;
    const outputBytes = stride * (webgpu ? 16 : 8);
    const bytes = spotBytes + basisBytes + metaBytes + outputBytes;
    if (bytes > MAX_RECEIVER_GPU_BYTES) return null;
    if (webgpu) {
        const bindingLimit = Math.min(limits?.maxStorageBufferBindingSize ?? 0, limits?.maxBufferSize ?? 0);
        if (Math.max(spotBytes, basisBytes, metaBytes, outputBytes) > bindingLimit
            || Math.ceil(count / 64) > (limits?.maxComputeWorkgroupsPerDimension ?? 0)) return null;
    } else if (Math.max(W, height) > (limits?.maxTextureSize ?? 0) || spotCapacity > (limits?.maxArrayTextureLayers ?? 0)) return null;
    return { height, stride, bytes };
}

/* Why this renderer cannot light receivers, or null: the node renderer's
 * WebGL2 backend has no compute (the splat colour hook says the same). */
export function litReceiversUnsupported(renderer) {
    return renderer?.isWebGPURenderer && !renderer.backend?.isWebGPUBackend ? 'lit_receivers_unavailable_on_webgl2_backend' : null;
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
/* `drawables()` lists what the frame draws for this object (Spark's renderer
 * for the WebGL splat); `setSplatHdr(on)` swaps Spark's fp16 renderer in and
 * out (modes.js), awaited inside the idle window. */
export async function createLitReceivers({ renderer, kind, object, layer, look = 'camera', whenIdle = () => Promise.resolve(),
    drawables = () => [object], setSplatHdr = null } = {}) {
    if (kind !== 'splat' && kind !== 'points') throw fail('lit_receivers_kind_unknown');
    const node = !!renderer?.isWebGPURenderer, unsupported = litReceiversUnsupported(renderer);
    if (unsupported) return { available: false, reason: unsupported };
    const { count, positions } = receiverPositions(kind, object);
    if (!layer || layer.count !== count) return { available: false, reason: 'lighting_layer_count_mismatch' };
    const echo = layer.siteId === 'echo', useBasis = !echo;
    const spotCapacity = echo ? Blend.LIT_ECHO_SPOT_SLOTS : Blend.LIT_SPOT_SLOTS;
    const gl = node ? null : renderer.getContext();
    const layout = receiverGpuLayout(count, { webgpu: node, useBasis, spotCapacity,
        limits: node ? renderer.backend.device.limits : { maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE),
            maxArrayTextureLayers: gl.getParameter(gl.MAX_ARRAY_TEXTURE_LAYERS) } });
    if (!layout) return { available: false, reason: 'lit_receivers_gpu_capacity' };
    // The point cloud stays monochrome (owner, 2026-10-20): grey surfaces, coloured light.
    const monochrome = kind === 'points';
    const packed = Blend.packReceiverSet(monochrome ? { ...layer, albedo: Blend.greyAlbedo(layer.albedo) } : layer, { useBasis });
    // The window column is blended on its own (capped); the camera look uses it.
    packed.windowSlot = useBasis ? layer.columnsMeta.findIndex(column => column.kind === 'window') : -1;
    const captureDiffuse = layer.columnsMeta.some(column => column.kind === 'capture_diffuse');
    const lightingDisclosure = captureDiffuse
        ? 'Photo and Cloud are relit by simulation. The bake estimates and removes diffuse capture lighting and drops the capture’s sheen.'
            + (monochrome ? '' : ' Photo uses a uniform low ambient light floor at night.')
        : Blend.LIT_RECEIVERS_DISCLOSURE;
    const lightingLimit = captureDiffuse
        ? `Precomputed surface colors · live spots re-baked with shadows · ${monochrome ? 'fixtures only' : 'uniform night ambient'} · capture sheen dropped`
        : Blend.LIT_RECEIVERS_LIMIT;
    const columnWords = layout.stride * Blend.LIT_SPOT_WORDS;
    const spotData = new Uint32Array(columnWords * spotCapacity);
    const spots = Array.from({ length: spotCapacity }, (_, slot) => spotData.subarray(slot * columnWords, (slot + 1) * columnWords));
    const weights = new Float32Array(Blend.LIT_WEIGHT_VEC4S * 4);
    const admitted = new Map(), byBakeKey = new Map(), readySlots = new Map(), dirtySlots = new Set();
    let nextAdmission = 0, liveSpots = 0, bouncedSpots = 0, expectedSpots = 0;
    let operator = Exposure.LIT_TONE.view, signature = '', dirty = true, attached = false, disposed = false, hdrSwapped = false;
    let blends = 0, lastBlendMs = null, matched = [], unbaked = [], whiteKelvin = null;
    if (!Blend.LIT_LOOKS.includes(look)) throw fail('lit_look_unknown');
    /* The window column's median over the room's interior receivers
     * (capture-relative): the night ambient through the windows is scaled
     * to it (lit-basis-blend.mjs LIT_WINDOW_AMBIENT). */
    const windowMedian = (() => {
        const k = packed.windowSlot;
        if (k < 0) return null;
        const half = h => { const e = (h >>> 10) & 31, m = h & 1023; return e === 0 ? m * 2 ** -24 : e === 31 ? Infinity : (1 + m / 1024) * 2 ** (e - 15); };
        const values = [], step = Math.max(1, Math.floor(count / 20000));
        for (let i = 0; i < count; i += step) {
            if (layer.flags[i] & Blend.LIT_RECEIVER_FLAGS.emitter) continue;
            const u = layer.basis[i * layer.columns + k] / 255;
            values.push(u * u * half(layer.scale[i]) * layer.columnScale[k]);
        }
        values.sort((a, b) => a - b);
        const median = values[values.length >> 1];
        return median > 0 ? median : null;
    })();

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

    // ---- WebGL: GPGPU into an RGBA16F target ---------------------------------
    async function createGlBlend() {
        const { height } = layout;
        const texture = (words, offset, stride) => {
            const data = new Uint32Array(W * height * 4);
            for (let i = 0; i < count; i++) data.set(words.subarray(i * stride + offset, i * stride + offset + 4), i * 4);
            const map = new THREE.DataTexture(data, W, height, THREE.RGBAIntegerFormat, THREE.UnsignedIntType);
            map.internalFormat = 'RGBA32UI';
            map.minFilter = THREE.NearestFilter; map.magFilter = THREE.NearestFilter; map.generateMipmaps = false;
            map.needsUpdate = true;
            return map;
        };
        const spotTexture = new THREE.DataArrayTexture(spotData, W, height, spotCapacity);
        spotTexture.format = THREE.RGIntegerFormat;
        spotTexture.type = THREE.UnsignedIntType;
        spotTexture.internalFormat = 'RG32UI';
        // Allocate the array up front. Zero-weight slots are never sampled.
        spotTexture.addLayerUpdate(0);
        spotTexture.needsUpdate = true;
        const maps = { uMeta: texture(packed.meta, 0, 4), uSpots: spotTexture };
        if (useBasis) {
            maps.uBasisA = texture(packed.basis, 0, 8);
            maps.uBasisB = texture(packed.basis, 4, 8);
        }
        const target = new THREE.WebGLRenderTarget(W, height, { format: THREE.RGBAFormat, type: THREE.HalfFloatType, depthBuffer: false,
            stencilBuffer: false, generateMipmaps: false, minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter });
        target.texture.name = 'lit-receivers-relit';
        const weightValues = Array.from({ length: Blend.LIT_WEIGHT_VEC4S }, () => new THREE.Vector4());
        const uniforms = { uWeights: { value: weightValues }, uCount: { value: count },
            ...Object.fromEntries(Object.entries(maps).map(([name, map]) => [name, { value: map }])) };
        const quadScene = new THREE.Scene(), camera = new THREE.Camera();
        const material = new THREE.ShaderMaterial({ name: 'lit-receivers-blend', uniforms,
            vertexShader: Blend.LIT_BLEND_VERTEX_GLSL, fragmentShader: Blend.litBlendFragmentGlsl({ useBasis, spotCapacity, windowSlot: packed.windowSlot }),
            depthTest: false, depthWrite: false, blending: THREE.NoBlending, toneMapped: false });
        const quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), material);
        quad.frustumCulled = false;
        quadScene.add(quad);
        let restore = null;
        return {
            backend: 'webgl-gpgpu',
            // The blend is scene-referred: the look and operator live in the frame.
            retone() {},
            uploadSpots() {
                for (const slot of dirtySlots) spotTexture.addLayerUpdate(slot);
                spotTexture.needsUpdate = true;
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
                // Splat colour stays fp16 through Spark (accumExtSplats).
                if (setSplatHdr) { await setSplatHdr(true); hdrSwapped = true; }
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
                    if (hdrSwapped) { hdrSwapped = false; await setSplatHdr(false); }
                };
            },
            async detach() { const undo = restore; restore = null; await undo?.(); },
            /* Proofs: the blend pass alone, in ms per pass: 20 back to back,
             * waited on once (gl.finish), as the WebGPU path measures it. */
            async time() {
                const gl = renderer.getContext(), batch = 20;
                gl.finish();
                const t0 = performance.now();
                for (let i = 0; i < batch; i++) this.run();
                gl.finish();
                return (performance.now() - t0) / batch;
            },
            async readback(n) {
                const rows = Math.ceil(n / W), out = new Uint16Array(W * rows * 4), gl = renderer.getContext();
                // The async frame readback may leave a pixel-pack buffer bound;
                // a synchronous read needs none (else INVALID_OPERATION).
                const pack = gl.getParameter(gl.PIXEL_PACK_BUFFER_BINDING);
                gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null);
                try { renderer.readRenderTargetPixels(target, 0, 0, W, rows, out); } finally { gl.bindBuffer(gl.PIXEL_PACK_BUFFER, pack); }
                return Array.from({ length: n }, (_, i) => [out[i * 4], out[i * 4 + 1], out[i * 4 + 2]].map(v => THREE.DataUtils.fromHalfFloat(v)));
            },
            dispose() {
                target.dispose(); material.dispose(); quad.geometry.dispose();
                for (const map of Object.values(maps)) map.dispose();
            },
            memoryBytes: layout.bytes,
        };
    }

    // ---- WebGPU: compute into storage ---------------------------------------
    async function createNodeBlend() {
        const T = await import('three/tsl');
        const hook = kind === 'splat' ? object.colorHook?.() : null;
        if (kind === 'splat' && !hook?.available) return { available: false, reason: hook?.reason || 'splat_relit_unavailable' };
        const attribute = (array, size) => new THREE.StorageBufferAttribute(array, size);
        const metaAttr = attribute(packed.meta, 4), basisAttr = useBasis ? attribute(packed.basis, 4) : null, spotAttr = attribute(spotData, 2);
        const weightAttr = attribute(weights, 4);
        const read = (attr, type, n) => T.storage(attr, type, n).toReadOnly();
        const buffers = { meta: read(metaAttr, 'uvec4', count), basis: basisAttr && read(basisAttr, 'uvec4', count * 2),
            spots: read(spotAttr, 'uvec2', count * spotCapacity), weights: read(weightAttr, 'vec4', Blend.LIT_WEIGHT_VEC4S) };
        let output = null, write;
        if (hook) write = (i, rgb) => hook.write.element(i).assign(T.vec4(rgb, 0));
        else {
            output = attribute(new Float32Array(count * 4), 4);
            const store = T.storage(output, 'vec4', count);
            write = (i, rgb) => store.element(i).assign(T.vec4(rgb, 1));
        }
        let compute = null;
        const build = () => { compute = Blend.litBlendComputeNode(T, { count, ...buffers, spotCapacity, windowSlot: packed.windowSlot, write }); };
        build();
        let restore = null;
        return {
            backend: 'webgpu-compute',
            retone() {},
            uploadSpots() {
                for (const slot of dirtySlots) spotAttr.addUpdateRange(slot * columnWords, columnWords);
                spotAttr.needsUpdate = true;
            },
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
            /* Proofs: the compute pass alone, in ms per dispatch: 20 back to
             * back, waited on once, so the queue's completion latency (several
             * ms on its own) is amortized instead of counted per pass. */
            async time() {
                const queue = renderer.backend.device.queue, batch = 20;
                await queue.onSubmittedWorkDone();
                const t0 = performance.now();
                for (let i = 0; i < batch; i++) this.run();
                await queue.onSubmittedWorkDone();
                return (performance.now() - t0) / batch;
            },
            async readback(n) {
                const values = new Float32Array(await renderer.getArrayBufferAsync(hook ? hook.attribute : output));
                return Array.from({ length: n }, (_, i) => [values[i * 4], values[i * 4 + 1], values[i * 4 + 2]]);
            },
            dispose() { compute?.dispose?.(); },
            memoryBytes: layout.bytes,
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
        bakeReceivers() { return { set: layer.set, directFirst: echo, ...worldReceivers() }; },
        /* A removed fixture's slot can be reused without displaying its old
         * column. A unique worker key also discards replies for that lifetime. */
        trackSpots(placements) {
            const wanted = new Map(placements);
            if (wanted.size === admitted.size && expectedSpots === wanted.size
                && placements.every(([key, origin]) => admitted.get(key)?.origin === origin)) return false;
            expectedSpots = wanted.size;
            for (const [key, entry] of admitted) {
                if (wanted.has(key) && wanted.get(key) === entry.origin) continue;
                admitted.delete(key); byBakeKey.delete(entry.bakeKey); readySlots.delete(key);
                if (entry.uploaded) liveSpots--;
                if (entry.uploadedBounce) bouncedSpots--;
                dirtySlots.delete(entry.slot);
            }
            const occupied = new Set([...admitted.values()].map(entry => entry.slot));
            let slot = 0;
            for (const [key, origin] of wanted) {
                if (admitted.has(key)) continue;
                while (occupied.has(slot)) slot++;
                if (slot >= spotCapacity) break;
                const bakeKey = `${++nextAdmission}:${key}`;
                const entry = { key, origin, bakeKey, slot, version: -1, bounced: false, uploaded: false, uploadedBounce: false };
                admitted.set(key, entry); byBakeKey.set(bakeKey, entry); occupied.add(slot);
            }
            dirty = true;
            return true;
        },
        spotBakeKey(key) { return admitted.get(key)?.bakeKey ?? null; },
        clearSpotColumns() {
            for (const entry of admitted.values()) {
                entry.version = -1; entry.bounced = false; entry.uploaded = false; entry.uploadedBounce = false;
            }
            readySlots.clear(); dirtySlots.clear();
            liveSpots = bouncedSpots = 0; dirty = true;
        },
        /* A live spot's column from the bake (half rgb per receiver). */
        setSpotColumn(bakeKey, version, halves, bounced = true) {
            const entry = byBakeKey.get(bakeKey);
            if (disposed || !entry || !Number.isSafeInteger(version) || entry.version >= version
                || !(halves instanceof Uint16Array) || halves.length !== count * 3) return false;
            entry.version = version; entry.bounced = bounced;
            Blend.writeSpotColumn(spots[entry.slot], count, halves);
            readySlots.set(entry.key, entry.slot); dirtySlots.add(entry.slot);
            dirty = true;
            return true;
        },
        /* Follow the eased lights, the adaptation gain, the operator and the
         * look (the meter and the white balance follow them; the blend itself
         * is scene-referred). Cheap when nothing changed; returns whether a
         * blend is due. */
        sync({ lights = [], gain = 1, operator: next = operator, look: nextLook = look } = {}) {
            if (disposed) return false;
            if (next !== operator) { operator = next; gpu.retone(); }
            if (Blend.LIT_LOOKS.includes(nextLook)) look = nextLook;
            const result = Blend.blendWeights({ lights, columns: useBasis ? layer.columnsMeta : [], columnScale: layer.columnScale,
                spotSlots: readySlots, gain, look, windowMedian, monochrome }, weights);
            matched = result.matched; unbaked = result.unbaked; whiteKelvin = result.whiteKelvin;
            const next2 = Array.from(weights, value => value.toPrecision(7)).join(',');
            if (next2 !== signature) { signature = next2; dirty = true; }
            return dirty;
        },
        /* Run the blend if anything changed: from modes.js's splat tick. */
        flush() {
            if (!attached || disposed || !dirty) return false;
            const t0 = performance.now();
            if (dirtySlots.size) gpu.uploadSpots();
            gpu.run();
            if (dirtySlots.size) {
                for (const entry of admitted.values()) {
                    if (!dirtySlots.has(entry.slot)) continue;
                    if (!entry.uploaded) { entry.uploaded = true; liveSpots++; }
                    if (entry.uploadedBounce !== entry.bounced) {
                        entry.uploadedBounce = entry.bounced;
                        bouncedSpots += entry.bounced ? 1 : -1;
                    }
                }
                dirtySlots.clear();
            }
            dirty = false; blends++; lastBlendMs = performance.now() - t0;
            return true;
        },
        /* Proofs only: the first n receivers' pre-exposed linear radiance as
         * the GPU wrote it (half float), and the CPU twin's for the same weights. */
        async readback(n = Math.min(count, 64)) {
            const gpuRows = await gpu.readback(Math.min(n, count));
            const cpu = gpuRows.map((_, i) => Blend.blendReceiver(packed, i, weights, spots).map(value => Math.max(0, value)));
            return { gpu: gpuRows, cpu, unit: 'pre-exposed linear radiance', preExposure: weights[Blend.PARAMS_SLOT * 4] };
        },
        /* What lit-frame.mjs draws in its scene-referred pass for this object. */
        drawables() { return (drawables() || []).filter(Boolean); },
        /* The display pass's white balance and AgX power for this look. */
        displayParams() { return Blend.displayParams(weights); },
        /* Proofs: the blend pass timed `iterations` times (ms, sorted). */
        async benchmark(iterations = 20) {
            if (!attached || disposed) return null;
            const times = [];
            for (let i = 0; i < iterations; i++) times.push(await gpu.time());
            return times.sort((a, b) => a - b);
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
                // The night exterior beyond the glass never sets the exposure.
                if (layer.flags[sampleIndex[s]] & Blend.LIT_RECEIVER_FLAGS.emitter) continue;
                samples.push(Blend.receiverLuminance(packed, sampleIndex[s], weights, spots));
            }
            // The camera meters toward the highlights; the eye, the mid-tones.
            return Exposure.meterLuminance(samples, look === 'camera' && operator === 'agx' ? Exposure.LIT_CAMERA_LOOK.meter : Exposure.LIT_METER);
        },
        /* The exposure the look asks for (EV above the design night) from the
         * metered luminance: the camera places the highlights at display 0.8;
         * the eye recovers 60 % of the drop (L2). */
        adaptationFrom(metered, designEv100) {
            return look === 'camera' && operator === 'agx'
                ? Exposure.cameraAdaptationFromLuminance(metered, 1 / (1.2 * 2 ** designEv100))
                : Exposure.adaptationFromLuminance(metered, designEv100);
        },
        state() {
            return Object.freeze({ available: true, kind, set: layer.set, source: layer.source, count, backend: gpu.backend, attached,
                operator, blends, lastBlendMs, columns: layer.columns, matched: matched.length, unbaked: Object.freeze(unbaked.slice()),
                liveSpots, bouncedSpots, expectedSpots, spotCapacity, gpuBytes: gpu.memoryBytes, layerBytes: layer.bytes, binding: 'sha256-verified',
                provenance: layer.provenance, simulated: true, measured: false, look: look === 'camera' && operator === 'agx' ? 'camera' : 'eye',
                whiteKelvin: whiteKelvin && Math.round(whiteKelvin), windowAmbient: windowMedian !== null && !monochrome, monochrome,
                disclosure: lightingDisclosure + (monochrome ? ' Cloud stays monochrome: grey surfaces lit only by the fixtures, in their own colour.'
                    : look === 'camera' && operator === 'agx' && whiteKelvin ? ' ' + Exposure.litCameraDisclosure({ kelvin: whiteKelvin }) : ''),
                limit: lightingLimit });
        },
    };
}
