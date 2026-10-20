/* home-3d/modes.js — render-mode state machine: points | splat | mesh (| live, P4).
 *
 * Splat: Spark SplatMesh loading the ORIGINAL-frame Scaniverse SPZ; the
 * scan->apartment registration (frame.json T_splat) is applied as the
 * object's model matrix — Spark handles SH under rotation like any renderer,
 * so the pipeline never has to rotate spherical harmonics (and the SPZ ships
 * untouched). Mesh: the decimated collision GLB with a normals material
 * (debug view — 1 MB vs the 54 MB textured export).
 *
 * Crossfade: points uOpacity <-> SplatMesh opacity over 600 ms.
 */
import * as THREE from 'three';

const { createCutawayUniforms, updateCutawayUniforms, injectMeshCutaway } =
    await import(`./dollhouse-cutaway-material.js${new URL(import.meta.url).search}`);
const { createPhotoFloorUniforms, injectPhotoFloorSupport } =
    await import(`./photo-floor-support.js${new URL(import.meta.url).search}`);
const { fetchApartmentAssetBytes } =
    await import(`./asset-byte-cache.mjs${new URL(import.meta.url).search}`);

const easeInOutCubic = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

const DESKTOP_ASSET_PLAN = Object.freeze({
    deviceClass: 'desktop',
    splatNames: Object.freeze(['apartment.ply', 'apartment.spz']),
    meshNames: Object.freeze(['mesh.glb', 'collision.glb']),
});
const MOBILE_ASSET_PLAN = Object.freeze({
    deviceClass: 'mobile',
    splatNames: Object.freeze(['apartment.mobile.ply', 'apartment.ply', 'apartment.spz']),
    meshNames: Object.freeze(['mesh.mobile.glb', 'mesh.glb', 'collision.glb']),
});
const MOBILE_MAX_FIDELITY_ASSET_PLAN = Object.freeze({
    deviceClass: 'mobile',
    splatNames: Object.freeze(['apartment.ply', 'apartment.spz', 'apartment.mobile.ply']),
    meshNames: Object.freeze(['mesh.glb', 'mesh.mobile.glb', 'collision.glb']),
});

/** Stable asset identity derived from the coarse performance target, never the viewport. */
export function apartmentAssetPlanForPerformanceTarget(value) {
    const targetTier = typeof value === 'string' ? value : value?.targetTier;
    const mode = typeof value === 'object'
        ? (value?.performanceMode ?? value?.mode)
        : null;
    if (targetTier !== 'mobile60' && targetTier !== 'mobile30') return DESKTOP_ASSET_PLAN;
    return mode === 'maxFidelity' ? MOBILE_MAX_FIDELITY_ASSET_PLAN : MOBILE_ASSET_PLAN;
}

function abortError() {
    const error = new Error('scene_load_aborted');
    error.name = 'AbortError';
    return error;
}

function assertLiveSignal(signal) {
    if (signal?.aborted) throw abortError();
}

/** The renderer needs a canonical local frame; world bearing is a separate gate. */
export function validateSceneFrame(frame, { siteId = null } = {}) {
    const fail = () => { throw new Error('scene_registration_invalid'); };
    if (!frame || typeof frame !== 'object' || Array.isArray(frame)) {
        throw new Error('scene_frame_unavailable');
    }
    if (frame.frame !== 'z_up_metric_floor0'
        || !Array.isArray(frame.extents_m) || frame.extents_m.length !== 3
        || !frame.extents_m.every((value) => Number.isFinite(value) && value > 0)
        || typeof frame.splat_baked !== 'boolean') fail();
    const placeholder = frame.placeholder !== undefined && frame.placeholder !== null && frame.placeholder !== false;
    if (frame.site !== undefined && frame.site !== siteId && siteId !== null) fail();
    // Legacy Echo and the disclosed placeholder predate the site field.
    if (siteId && siteId !== 'echo' && !placeholder && frame.site !== siteId) fail();
    if ((frame.T_splat !== undefined && frame.T_splat !== null) || !frame.splat_baked) {
        const transform = frame.T_splat;
        if (!Array.isArray(transform) || transform.length !== 4
            || !transform.every((row) => Array.isArray(row) && row.length === 4 && row.every(Number.isFinite))
            || transform[3].some((value, index) => Math.abs(value - (index === 3 ? 1 : 0)) > 1e-8)) fail();
        const matrix = new THREE.Matrix4().set(...transform.flat());
        if (!Number.isFinite(matrix.determinant()) || matrix.determinant() <= 1e-10) fail();
        const axes = [0, 1, 2].map((column) => new THREE.Vector3(transform[0][column], transform[1][column], transform[2][column]));
        const lengths = axes.map((axis) => axis.length());
        if (lengths.some((length) => length <= 1e-10 || Math.abs(length - lengths[0]) > lengths[0] * 1e-6)
            || axes.some((axis, index) => axes.some((other, otherIndex) => index !== otherIndex
                && Math.abs(axis.dot(other)) > lengths[index] * lengths[otherIndex] * 1e-6))) fail();
    }
    return frame;
}

/** Own network cancellation; Spark's decoder itself has no AbortSignal API. */
export async function loadSplatAsset({ url, signal, siteId = null, contentRevision = null, onProgress = null }) {
    assertLiveSignal(signal);
    // The download starts with the decoder import, so its progress shows at once.
    const [{ SplatMesh }, fileBytes] = await Promise.all([
        import('@sparkjsdev/spark'),
        fetchApartmentAssetBytes({ url, siteId, contentRevision, signal, onProgress }),
    ]);
    assertLiveSignal(signal);
    const splat = new SplatMesh({ fileBytes, fileName: new URL(url, window.location.href).pathname });
    try {
        await splat.initialized;
        assertLiveSignal(signal);
        return splat;
    } catch (error) {
        splat.dispose?.();
        throw error;
    }
}

/** Qualified GLBs are self-contained, so all network IO belongs to this fetch. */
export async function loadGlbAsset({ url, signal, siteId = null, contentRevision = null, onProgress = null }) {
    assertLiveSignal(signal);
    const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js');
    assertLiveSignal(signal);
    const bytes = await fetchApartmentAssetBytes({ url, siteId, contentRevision, signal, onProgress });
    assertLiveSignal(signal);
    // Do not let parseAsync start uncancellable external texture/buffer fetches.
    const view = new DataView(bytes);
    if (bytes.byteLength < 20 || view.getUint32(0, true) !== 0x46546c67
        || view.getUint32(4, true) !== 2 || view.getUint32(8, true) !== bytes.byteLength
        || view.getUint32(16, true) !== 0x4e4f534a) throw new Error('scene_glb_invalid');
    const jsonLength = view.getUint32(12, true);
    if (20 + jsonLength > bytes.byteLength) throw new Error('scene_glb_invalid');
    const json = JSON.parse(new TextDecoder().decode(new Uint8Array(bytes, 20, jsonLength)));
    if ([...(json.buffers || []), ...(json.images || [])].some((item) => item.uri && !item.uri.startsWith('data:'))) {
        throw new Error('scene_glb_external_resource_refused');
    }
    return new GLTFLoader().parseAsync(bytes, '');
}

export function createModes({ apartmentRoot, pointsMaterial, sim, assetCandidates, fetchFrame, getPoints, scene, renderer, camera, performanceController,
    scenePointsOnly = null, signal = null, siteId = null, contentRevision = null,
    sceneIdentity = null, requireRegistration = false, loadAdapters = {}, loadTimeoutMs = null,
    dollhouseCutaway = false, photoFloorSupport = false }) {
    const candidates = assetCandidates;
    const usePhotoFloor = photoFloorSupport === true && requireRegistration;
    // Presentation only: hide wholly exterior 3-sigma XY support or detached
    // centers over 0.25 m beyond the FULL retained display mesh. Never crop Z.
    const useExteriorCleanup = usePhotoFloor && siteId === 'victoria' && !sim;
    let exteriorBounds = null;
    /* A disclosed placeholder interior (frame.json "placeholder", read by the
     * engine through scene-placeholder.mjs) is points only: its splat exists
     * only to satisfy the pack contract and it has no mesh. Async because
     * frame.json may still be in flight when the first mode change arrives. */
    async function pointsOnlyScene() {
        if (typeof scenePointsOnly !== 'function') return false;
        try { return (await scenePointsOnly()) === true; } catch (_) { return false; }
    }
    const state = { mode: 'points', targetMode: 'points', modeSeq: 0,
                    fading: null, splat: null, mesh: null, collision: null,
                    splatSource: null,
                    meshSource: null, meshFallback: false, collisionSource: null,
                    meshNearCut: { value: 0.0 }, splatCutaway: null };
    const cutawayUniforms = createCutawayUniforms();
    const photoFloorUniforms = createPhotoFloorUniforms();
    let desiredCutaway = { policy: { enabled: false, center: [0, 0, 0], halfExtent: [1, 1, 1],
        direction: [0, 0], threshold: 0.8, feather: 0.03, floorZ: 0.25 },
        inverse: new THREE.Matrix4().toArray() };
    const listeners = [];
    // In-flight load promises: a slow asset (mesh.glb is ~56 MB) must never be
    // fetched twice by two racing setMode calls — that would add a second,
    // unmanaged copy to the scene.
    let meshLoading = null;
    let collisionLoading = null;
    let splatLoading = null;
    let disposed = false;
    let sparkUpdateJob = null;
    let sparkIdlePromise = Promise.resolve(null);
    let lastSparkError = null;
    const sparkUpdateCounts = { started: 0, completed: 0, failed: 0, coalescedTicks: 0 };
    const lifetime = new AbortController();
    const resourcesDisposed = new WeakSet();
    const imagesClosed = new WeakSet();
    const pending = new Set();
    const activeDecodes = new Map();
    const decodedScenes = new WeakMap();
    const loadStatus = { splat: 'idle', mesh: 'idle', collision: 'idle', sparkRenderer: 'idle' };
    const loadCounts = { started: 0, completed: 0, failed: 0, aborted: 0, timedOut: 0, decoders: 0, disposedResources: 0 };
    const identity = sceneIdentity && Object.isFrozen(sceneIdentity) ? sceneIdentity
        : Object.freeze({ ...sceneIdentity, siteId, contentRevision });
    let frameStatus = requireRegistration ? 'unresolved' : 'not-required';
    let lastLoadError = null;
    const adapters = {
        loadSplat: loadSplatAsset,
        loadGlb: loadGlbAsset,
        createSplatCutaway: async ({ splat }) => {
            // Test/load adapters without a Spark generator have no GPU graph.
            if (!dollhouseCutaway || typeof splat.updateGenerator !== 'function') return null;
            const { createSplatCutawayModifier } = await import(`./splat-cutaway.js${new URL(import.meta.url).search}`);
            return createSplatCutawayModifier(splat);
        },
        createSparkRenderer: async ({ renderer: rendererRef }) => {
            const { SparkRenderer } = await import('@sparkjsdev/spark');
            // Home owns the update lifetime. Spark's onBeforeRender must not
            // start a second unowned update beside our explicit frame tick.
            return new SparkRenderer({ renderer: rendererRef, autoUpdate: false });
        },
        ...loadAdapters,
    };

    function assertLive() {
        if (disposed || lifetime.signal.aborted || signal?.aborted) throw abortError();
    }
    function release(resource, closeImage = true) {
        if (!resource || typeof resource.dispose !== 'function' || resourcesDisposed.has(resource)) return;
        resourcesDisposed.add(resource);
        try { resource.dispose(); } catch (_) { /* terminal resource cleanup */ }
        const image = resource.isTexture && resource.image;
        if (closeImage && image && typeof image.close === 'function' && !imagesClosed.has(image)) {
            imagesClosed.add(image);
            try { image.close(); } catch (_) { /* terminal decoded-image cleanup */ }
        }
        loadCounts.disposedResources += 1;
    }
    function releaseMaterials(materials, retainedTextures = new Set()) {
        const retainedImages = new Set([...retainedTextures].map((texture) => texture.image).filter(Boolean));
        for (const material of materials) {
            if (!material) continue;
            for (const value of Object.values(material)) {
                if (value?.isTexture && !retainedTextures.has(value)) release(value, !retainedImages.has(value.image));
            }
            release(material);
        }
    }
    function releaseAsset(value) {
        const object = value?.scene || value;
        if (!object) return;
        const materials = new Set();
        const roots = new Set([object, ...(value?.scenes || decodedScenes.get(object) || [])]);
        for (const root of roots) {
            root.removeFromParent?.();
            root.traverse?.((node) => {
                release(node.geometry);
                for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
                    if (material) materials.add(material);
                }
                if (node !== root) release(node);
            });
        }
        releaseMaterials(materials);
        for (const root of roots) release(root);
    }
    async function sceneFrame() {
        assertLive();
        try {
            const frame = await fetchFrame();
            assertLive();
            if (requireRegistration) validateSceneFrame(frame, { siteId });
            frameStatus = 'ready';
            return frame;
        } catch (error) {
            frameStatus = 'failed';
            lastLoadError = String(error?.message || error);
            throw error;
        }
    }
    const progressListeners = [];
    // Byte progress for the toolbar's loading notice; it never affects loading.
    function emitLoadProgress(kind, { loaded, total } = {}) {
        if (!Number.isFinite(loaded)) return;
        const event = Object.freeze({ kind, loaded, total: Number.isFinite(total) && total > 0 ? total : null });
        progressListeners.slice().forEach((cb) => { try { cb(event); } catch (_) {} });
    }
    function runAssetLoad(kind, url, adapter, timeoutMs) {
        assertLive();
        // A timed-out decoder may still own buffers. Never pile a retry or
        // alternate-format decode on top of it while it is winding down.
        if (activeDecodes.has(kind)) throw new Error('scene_loader_still_stopping');
        const controller = new AbortController();
        const record = { kind, controller };
        pending.add(record);
        activeDecodes.set(kind, record);
        loadStatus[kind] = 'loading';
        loadCounts.started += 1;
        loadCounts.decoders += 1;
        return new Promise((resolve, reject) => {
            let settled = false;
            let timer = null;
            const finish = (error, value, status) => {
                if (settled) return false;
                settled = true;
                clearTimeout(timer);
                lifetime.signal.removeEventListener('abort', onAbort);
                pending.delete(record);
                loadStatus[kind] = status;
                if (error) lastLoadError = String(error?.message || error);
                if (error) reject(error); else resolve(value);
                return true;
            };
            const onAbort = () => {
                controller.abort();
                if (finish(abortError(), null, 'aborted')) loadCounts.aborted += 1;
            };
            lifetime.signal.addEventListener('abort', onAbort, { once: true });
            timer = setTimeout(() => {
                controller.abort();
                if (finish(new Error(`${kind} load timeout for ${url}`), null, 'timed-out')) loadCounts.timedOut += 1;
            }, timeoutMs);
            if (lifetime.signal.aborted) onAbort();
            Promise.resolve().then(() => {
                assertLiveSignal(controller.signal);
                return adapter({ url, signal: controller.signal, siteId, contentRevision,
                    onProgress: (p) => { if (!settled && !controller.signal.aborted) emitLoadProgress(kind, p); } });
            }).then((value) => {
                if (activeDecodes.get(kind) === record) activeDecodes.delete(kind);
                loadCounts.decoders -= 1;
                if (settled || controller.signal.aborted || disposed) {
                    releaseAsset(value);
                    return;
                }
                loadCounts.completed += 1;
                if (value?.scene && Array.isArray(value.scenes)) decodedScenes.set(value.scene, value.scenes);
                finish(null, value, 'loaded');
            }, (error) => {
                if (activeDecodes.get(kind) === record) activeDecodes.delete(kind);
                loadCounts.decoders -= 1;
                if (finish(error, null, error?.name === 'AbortError' ? 'aborted' : 'failed')) loadCounts.failed += 1;
            });
        });
    }

    /* Asset identity follows the same stable device target as the Apartment
     * renderer, Cesium, and the background prewarmer. Viewport width is layout,
     * not hardware: an 844px landscape phone is still a phone, and a 600px
     * desktop window is still a desktop. */
    function currentAssetPlan() {
        try {
            return apartmentAssetPlanForPerformanceTarget(
                performanceController?.getDiagnostics?.(),
            );
        } catch (_) {
            return DESKTOP_ASSET_PLAN;
        }
    }

    // The EL cloud writes depth by design (self-occlusion). Once a fade ends
    // the points object must be HIDDEN in splat/mesh mode — uOpacity=0 alone
    // leaves an invisible depth wall that occludes the alpha-blended splat.
    function setPointsVisible(v) {
        const pts = getPoints && getPoints();
        if (pts) pts.visible = v;
    }

    function applyRegistration(obj, frame) {
        if (frame && frame.splat_baked === true) return;   // transform baked into the .spz
        const T = frame && frame.T_splat;
        if (T) {
            const m = new THREE.Matrix4();
            m.set(
                T[0][0], T[0][1], T[0][2], T[0][3],
                T[1][0], T[1][1], T[1][2], T[1][3],
                T[2][0], T[2][1], T[2][2], T[2][3],
                T[3][0], T[3][1], T[3][2], T[3][3],
            );
            // Decompose instead of pinning .matrix — Spark recomposes the
            // splat transform from position/quaternion/scale, so a hand-set
            // matrix with matrixAutoUpdate=false is silently ignored.
            m.decompose(obj.position, obj.quaternion, obj.scale);
            obj.updateMatrix();
            obj.matrixWorldNeedsUpdate = true;
        }
    }

    function loadSplat() {
        assertLive();
        if (state.splat) return Promise.resolve(state.splat);
        if (splatLoading) return splatLoading;
        const p = _loadSplatImpl();
        splatLoading = p;
        // The caller owns p's rejection; this bookkeeping branch (and its
        // mesh/collision twins below) must not raise a second, unhandled one:
        // that is a page error for every missing asset, e.g. the collision.glb
        // a placeholder pack does not ship, prewarmed on every arrival.
        p.finally(() => { if (splatLoading === p) splatLoading = null; }).catch(() => {});
        return p;
    }

    async function _loadSplatImpl() {
        if (activeDecodes.has('sparkRenderer')) throw new Error('scene_loader_still_stopping');
        const frame = await sceneFrame();
        // These independent, owned loads must overlap. The textured floor is
        // still required before Photo adopts, but need not wait for Spark.
        const photoFloorReady = usePhotoFloor ? loadMesh({ requireTextured: true }) : null;
        // A fast mesh failure is observed below after the splat settles; attach
        // a handler now so it cannot become an unhandled promise rejection.
        photoFloorReady?.catch(() => {});
        let lastErr = null;
        const assetPlan = currentAssetPlan();
        const mobile = assetPlan.deviceClass === 'mobile';
        // Bounded mobile modes prefer the 116k scan; explicit Max fidelity and
        // desktop plans prefer the full 466k scan. Both retain a safe fallback.
        // SplatTransform's mobile spz flavor defeats Spark's decoder on some
        // exports, so the shared plan intentionally omits it; ply always works.
        const urls = assetPlan.splatNames.flatMap((n) => candidates(n, null, { sim }));
        for (const url of urls) {
            let splat = null;
            let splatCutaway = null;
            try {
                // A splat load timeout cancels network IO; a decoder completing
                // later is released by runAssetLoad before it can join the scene.
                // The node renderer's splat sizes its sort and SH from the budget.
                const budget = performanceController?.getBudget?.() ?? null;
                splat = await runAssetLoad('splat', url, (load) => adapters.loadSplat({ ...load, budget }),
                    loadTimeoutMs ?? (mobile ? 45000 : 90000));
                assertLive();
                applyRegistration(splat, frame);
                splatCutaway = await adapters.createSplatCutaway({ splat, dollhouseCutaway });
                assertLive();
                if (scene && renderer && !state.sparkRenderer && adapters.createSparkRenderer) {
                    const sr = await runAssetLoad('sparkRenderer', 'spark-renderer',
                        ({ signal: rendererSignal }) => adapters.createSparkRenderer({ renderer, signal: rendererSignal }),
                        loadTimeoutMs ?? (mobile ? 45000 : 90000));
                    if (disposed || lifetime.signal.aborted) {
                        releaseAsset(sr);
                        throw abortError();
                    }
                    if (sr) {
                        sr.autoUpdate = false;
                        sr.visible = false;
                        sr.frustumCulled = false;
                        sr.traverse((o) => { o.frustumCulled = false; });
                        // Spark follows the camera for precision.
                        if (camera) {
                            if (!camera.parent) scene.add(camera);
                            camera.add(sr);
                        } else scene.add(sr);
                        state.sparkRenderer = sr;
                    }
                }
                assertLive();
                // The same revision's actual scan floor backs transparent Photo
                // gaps. Reuse the owned/deduplicated mesh loader and textures.
                if (usePhotoFloor) {
                    await photoFloorReady;
                    assertLive();
                    if (state.meshFallback) throw new Error('photo_floor_support_unavailable');
                }
                splat.renderOrder = 0;
                // Spark's objects have no geometry bounds — three.js frustum-
                // culls them, which silently skips onBeforeRender (Spark's
                // entire update/sort/draw path). Verified the hard way.
                splat.frustumCulled = false;
                apartmentRoot.add(splat);
                state.splat = splat;
                state.splatCutaway = splatCutaway;
                state.splatSource = url;
                visibleFor(state.mode);
                return splat;
            } catch (e) {
                splatCutaway?.dispose?.();
                if (splat) releaseAsset(splat);
                assertLive();
                if (activeDecodes.has('splat') || activeDecodes.has('sparkRenderer')) throw e;
                lastErr = e;
            }
        }
        throw lastErr || new Error('no splat asset reachable');
    }

    function loadMesh({ requireTextured = false } = {}) {
        assertLive();
        if (state.mesh && (!requireTextured || !state.meshFallback)) return Promise.resolve(state.mesh);
        if (meshLoading) return requireTextured ? meshLoading.then(() => {
            assertLive();
            return state.meshFallback ? loadMesh({ requireTextured: true }) : state.mesh;
        }) : meshLoading;
        const p = _loadMeshImpl({ requireTextured });
        meshLoading = p;
        p.finally(() => { if (meshLoading === p) meshLoading = null; }).catch(() => {});
        return p;
    }

    function loadCollision() {
        assertLive();
        if (state.collision) return Promise.resolve(state.collision);
        if (collisionLoading) return collisionLoading;
        const p = _loadCollisionImpl();
        collisionLoading = p;
        p.finally(() => { if (collisionLoading === p) collisionLoading = null; }).catch(() => {});
        return p;
    }

    async function _loadCollisionImpl() {
        if (requireRegistration) await sceneFrame();
        let lastErr = null;
        for (const url of candidates('collision.glb', null, { sim })) {
            let group = null;
            try {
                const gltf = await runAssetLoad('collision', url, adapters.loadGlb, loadTimeoutMs ?? 90000);
                group = gltf.scene;
                assertLive();
                group.name = 'apartment-collision-proxy';
                group.userData.collisionProxy = true;
                const originals = new Set();
                group.traverse((o) => {
                    if (!o.isMesh) return;
                    for (const material of Array.isArray(o.material) ? o.material : [o.material]) originals.add(material);
                    o.frustumCulled = false;
                    o.userData.collisionProxy = true;
                    // Material visibility suppresses rendering without disabling
                    // Mesh.raycast. The proxy remains available in every view mode.
                    // Scan meshes contain a mixture of winding directions (and
                    // the dollhouse view often looks at an interior back face).
                    // Picking must therefore be two-sided even though the proxy
                    // never renders. Otherwise a visibly solid wall/floor can
                    // reject the exact same screen-space click.
                    o.material = new THREE.MeshBasicMaterial({
                        visible: false,
                        side: THREE.DoubleSide,
                    });
                });
                releaseMaterials(originals);
                assertLive();
                apartmentRoot.add(group);
                group.updateMatrixWorld(true);
                state.collision = group;
                state.collisionSource = url;
                return group;
            } catch (e) {
                if (group) releaseAsset(group);
                assertLive();
                if (activeDecodes.has('collision')) throw e;
                lastErr = e;
            }
        }
        throw lastErr || new Error('no collision asset reachable');
    }

    function firstMaterial(material) {
        return Array.isArray(material) ? material.find(Boolean) : material;
    }

    function displayBounds(group) {
        // Include every retained display node, regardless of visibility. Work
        // in the registered apartment frame, never a world-axis inflated box,
        // a collision proxy, or the smaller measured wall rectangle.
        apartmentRoot.updateWorldMatrix(true, true);
        const inverse = apartmentRoot.matrixWorld.clone().invert();
        const transform = new THREE.Matrix4(), point = new THREE.Vector3();
        const box = new THREE.Box3();
        group.traverse(node => {
            const positions = node.isMesh && node.geometry?.attributes?.position;
            if (!positions) return;
            transform.multiplyMatrices(inverse, node.matrixWorld);
            for (let index = 0; index < positions.count; index++) {
                point.fromBufferAttribute(positions, index).applyMatrix4(transform);
                if (Number.isFinite(point.x) && Number.isFinite(point.y) && Number.isFinite(point.z)) box.expandByPoint(point);
            }
        });
        return !box.isEmpty() && box.max.x > box.min.x && box.max.y > box.min.y
            ? { minXY: [box.min.x, box.min.y], maxXY: [box.max.x, box.max.y] } : null;
    }

    function meshDisplayMaterial(src, original, geometry) {
        const material = firstMaterial(original);
        const map = material && material.map;
        if (map) {
            map.colorSpace = THREE.SRGBColorSpace;
            map.needsUpdate = true;
            return new THREE.MeshBasicMaterial({
                map,
                side: THREE.FrontSide,
                toneMapped: false,
            });
        }
        if (geometry?.attributes?.color) {
            return new THREE.MeshBasicMaterial({
                vertexColors: true,
                side: THREE.FrontSide,
                toneMapped: false,
            });
        }
        return new THREE.MeshBasicMaterial({
            color: src.textured ? 0xb9c0c7 : 0x8c96a3,
            side: THREE.DoubleSide,
            transparent: !src.textured,
            opacity: src.textured ? 1 : 0.72,
            toneMapped: false,
        });
    }

    async function _loadMeshImpl({ requireTextured = false } = {}) {
        if (requireRegistration) await sceneFrame();
        let lastErr = null;
        // mesh.glb = the full textured Scaniverse export (1:1, 221k tris);
        // collision.glb is the decimated picking proxy — fallback display only.
        const sources = currentAssetPlan().meshNames.map((name) => ({
            name,
            textured: name !== 'collision.glb',
            mobile: name === 'mesh.mobile.glb',
            fallback: name === 'collision.glb',
        })).filter((source) => !requireTextured || !source.fallback);
        for (const src of sources) {
            for (const url of candidates(src.name, null, { sim })) {
                let grp = null;
                try {
                    const gltf = await runAssetLoad('mesh', url, adapters.loadGlb, loadTimeoutMs ?? 90000);
                    grp = gltf.scene;
                    assertLive();
                    let texturedMeshes = 0;
                    let fallbackMeshes = 0;
                    const originals = new Set();
                    const retainedTextures = new Set();
                    grp.traverse((o) => {
                        if (!o.isMesh) return;
                        for (const material of Array.isArray(o.material) ? o.material : [o.material]) originals.add(material);
                        o.frustumCulled = false;
                        // the scene is unlit (cloud needs no lights) — swap PBR
                        // for basic textured so the photogrammetry atlas shows.
                        // FrontSide = dollhouse cutaway: interior-scanned wall
                        // faces point INTO rooms, so the wall nearest the
                        // camera backfaces away and culls itself while the far
                        // walls render. Plus a near-camera melt (uMeshNearCut,
                        // driven per-frame from orbit radius) for anything the
                        // culling can't catch.
                        const mat = meshDisplayMaterial(src, o.material, o.geometry);
                        if (mat.map) retainedTextures.add(mat.map);
                        if (mat.map || mat.vertexColors) texturedMeshes += 1;
                        else fallbackMeshes += 1;
                        mat.onBeforeCompile = (sh) => {
                            injectMeshCutaway(sh, cutawayUniforms);
                            if (usePhotoFloor) injectPhotoFloorSupport(sh, photoFloorUniforms);
                            sh.uniforms.uMeshNearCut = state.meshNearCut;
                            sh.vertexShader = sh.vertexShader
                                .replace('#include <common>', '#include <common>\nvarying float vViewZ;')
                                .replace('#include <fog_vertex>', '#include <fog_vertex>\nvViewZ = -mvPosition.z;');
                            sh.fragmentShader = sh.fragmentShader
                                .replace('#include <common>', '#include <common>\nuniform float uMeshNearCut;\nvarying float vViewZ;')
                                .replace('#include <dithering_fragment>',
                                    '#include <dithering_fragment>\nif (uMeshNearCut > 0.0 && vViewZ < uMeshNearCut) discard;');
                        };
                        mat.customProgramCacheKey = () => `home-scan-display-floor-${usePhotoFloor ? 1 : 0}-1`;
                        o.material = mat;
                    });
                    releaseMaterials(originals, retainedTextures);
                    assertLive();
                    if (requireTextured && texturedMeshes === 0) throw new Error('photo_floor_support_unavailable');
                    apartmentRoot.add(grp);
                    const previousMesh = state.mesh;
                    state.mesh = grp;
                    state.meshSource = src.name;
                    state.meshFallback = !!src.fallback || (src.textured && texturedMeshes === 0 && fallbackMeshes > 0);
                    exteriorBounds = useExteriorCleanup && !state.meshFallback ? displayBounds(grp) : null;
                    if (previousMesh && previousMesh !== grp) releaseAsset(previousMesh);
                    visibleFor(state.mode);
                    return grp;
                } catch (e) {
                    if (grp) releaseAsset(grp);
                    assertLive();
                    if (activeDecodes.has('mesh')) throw e;
                    lastErr = e;
                }
            }
        }
        throw lastErr || new Error('no mesh asset reachable');
    }

    function visibleFor(mode) {
        const splatActive = mode === 'splat' || !!state.fading;
        const floorOnly = usePhotoFloor && splatActive;
        photoFloorUniforms.uPhotoFloorOnly.value = floorOnly ? 1 : 0;
        if (state.splat) {
            state.splat.visible = splatActive;
            if (!splatActive && 'opacity' in state.splat) state.splat.opacity = 0;
        }
        if (state.sparkRenderer) state.sparkRenderer.visible = splatActive;
        if (state.mesh) {
            state.mesh.visible = mode === 'mesh' || floorOnly;
            state.mesh.traverse((object) => {
                if (!object.isMesh) return;
                for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
                    // Color backing only: preserve the original splat's depth
                    // ordering, including furniture represented by deeper bases.
                    material.depthWrite = !floorOnly;
                }
            });
        }
        setPointsVisible(mode === 'points' || !!state.fading);
    }

    function tickNodeSplat(cam) {
        const splat = state.splat;
        sparkUpdateCounts.started += 1;
        try {
            state.splatCutaway?.update(desiredCutaway.policy, desiredCutaway.inverse, exteriorBounds);
            const degree = performanceController?.getBudget?.()?.splatMaxShDegree;
            if (Number.isInteger(degree)) splat.setMaxShDegree(degree);
            const result = splat.update(renderer, cam);
            sparkUpdateCounts.completed += 1;
            lastSparkError = null;
            sparkIdlePromise = Promise.resolve({ status: 'updated', sort: result.status });
        } catch (error) {
            sparkUpdateCounts.failed += 1;
            lastSparkError = String(error?.message || error);
            sparkIdlePromise = Promise.resolve({ status: 'failed', error: lastSparkError });
        }
        return sparkIdlePromise;
    }

    const modes = {
        get mode() { return state.mode; },
        onChange(cb) {
            listeners.push(cb);
            return () => {
                const i = listeners.indexOf(cb);
                if (i >= 0) listeners.splice(i, 1);
            };
        },

        onLoadProgress(cb) {
            progressListeners.push(cb);
            return () => {
                const i = progressListeners.indexOf(cb);
                if (i >= 0) progressListeners.splice(i, 1);
            };
        },

        async setMode(next, { duration = 600 } = {}) {
            assertLive();
            // targetMode/gen are committed SYNCHRONOUSLY (before any await) so
            // racing callers reconcile against intent, not the lagging
            // state.mode. The old guards compared against state.mode, which
            // does not flip until AFTER the (slow) asset load — so a 'cloud'
            // click made while a mesh load was in flight was silently dropped
            // and the mesh stayed on screen.
            const gen = ++state.modeSeq;
            state.targetMode = next;

            // Already in (and stable at) the requested mode: re-assert the
            // layer visibility and return. Re-asserting (vs no-op) self-heals
            // a scene desynced by a remount or a prior raced transition.
            if (next === state.mode && !state.fading) {
                if (next === 'points') pointsMaterial.uniforms.uOpacity.value = 1;
                visibleFor(next);
                return;
            }

            // Refused before any load, so a placeholder never fetches its
            // stand-in splat. Intent is handed back only if nothing newer
            // was requested meanwhile (the latest request still wins).
            const pointsOnly = next !== 'points' && await pointsOnlyScene();
            assertLive();
            if (pointsOnly) {
                if (gen === state.modeSeq) state.targetMode = state.mode;
                throw new Error('scene_points_only');
            }
            try {
                if (next === 'splat') await loadSplat();   // throws -> UI toast, stays put
                if (next === 'mesh') await loadMesh();
                assertLive();
            } catch (error) {
                if (gen === state.modeSeq) state.targetMode = state.mode;
                throw error;
            }

            // A newer setMode was requested while we awaited the load — abandon
            // this stale transition so the LATEST request wins (the impatient
            // mesh->cloud that left the mesh overlapping the cloud).
            if (gen !== state.modeSeq) return;

            const prev = state.mode;
            if (duration > 0 &&
                ((prev === 'points' && next === 'splat') || (prev === 'splat' && next === 'points'))) {
                state.fading = { t: 0, duration, dir: next === 'splat' ? 1 : -1 };
                visibleFor(next);
            } else {
                // cancel any in-flight crossfade — orphaned, its per-frame
                // writes would drag uOpacity back toward 0 AFTER this instant
                // switch set it to 1 (invisible cloud in 'points' mode)
                state.fading = null;
                if (state.splat && 'opacity' in state.splat) state.splat.opacity = next === 'splat' ? 1 : 0;
                pointsMaterial.uniforms.uOpacity.value = next === 'points' ? 1 : 0;
                visibleFor(next);
            }
            state.mode = next;
            listeners.forEach((cb) => cb(next, prev));
        },

        /* The per-frame splat tick, before renderer.render(). Spark: one owned
         * async update; busy frames do not queue more work. The node
         * renderer's splat (?renderer=webgpu): a synchronous cutaway commit
         * and sort (gaussian-splat-webgpu.mjs update()). */
        tickSplat(cam, time) {
            if (disposed) return null;
            const splatActive = !!state.fading || state.mode === 'splat' || state.targetMode === 'splat';
            if (state.splat?.isHomeGaussianSplat) return splatActive ? tickNodeSplat(cam) : null;
            if (!state.sparkRenderer || !splatActive || (!state.splat && !state.fading)) return null;
            if (sparkUpdateJob) {
                sparkUpdateCounts.coalescedTicks += 1;
                return sparkIdlePromise;
            }
            // Capture resource ownership before invoking an adapter. Disposal
            // can clear state while Spark is awaiting readback or its worker.
            const job = { renderer: state.sparkRenderer, splat: state.splat, terminalLease: null };
            sparkUpdateJob = job;
            sparkUpdateCounts.started += 1;
            let updating;
            try {
                // Uniform commits belong to the same exclusive SDK lease.
                // Busy frames only replace desired state; they never mutate
                // a Dyno graph while an earlier update is awaiting readback.
                state.splatCutaway?.update(desiredCutaway.policy, desiredCutaway.inverse, exteriorBounds);
                updating = job.renderer.update({ scene, camera: cam, time });
            } catch (error) {
                updating = Promise.reject(error);
            }
            sparkIdlePromise = Promise.resolve(updating).then(() => {
                sparkUpdateCounts.completed += 1;
                if (!disposed && state.sparkRenderer === job.renderer) lastSparkError = null;
                return { status: 'updated' };
            }, (error) => {
                sparkUpdateCounts.failed += 1;
                const message = String(error?.message || error);
                if (!disposed && state.sparkRenderer === job.renderer) lastSparkError = message;
                return { status: 'failed', error: message };
            }).then((result) => {
                if (job.terminalLease) {
                    // Spark may have allocated ordering textures/workers after
                    // disposal detached it. Release them only after settlement.
                    const [spark, splat, cutaway] = job.terminalLease;
                    cutaway?.dispose?.();
                    releaseAsset(spark);
                    releaseAsset(splat);
                    job.terminalLease = null;
                }
                if (sparkUpdateJob === job) sparkUpdateJob = null;
                return result;
            });
            return sparkIdlePromise;
        },

        // Settles after the owned update and any terminal lease cleanup. A
        // hung vendor operation remains pending; no timeout reports false zero.
        whenSparkIdle() { return sparkIdlePromise; },

        /**
         * Lit's per-splat colour buffer (lighting report L4), or null without
         * a node-renderer splat. See HomeGaussianSplat.colorHook().
         */
        splatColorHook() { return state.splat?.colorHook?.() ?? null; },

        getMesh() { return state.mesh; },
        getCollision() { return state.collision; },
        setMeshNearCut(v) { state.meshNearCut.value = v; },
        setDollhouseCutaway(policy, worldToLocal) {
            if (disposed) return;
            updateCutawayUniforms(cutawayUniforms, policy, worldToLocal);
            desiredCutaway = { policy: { ...policy, center: policy.center.slice(),
                halfExtent: policy.halfExtent.slice(), direction: policy.direction.slice(),
                ...(policy.metricShell == null ? {} : { metricShell: { ...policy.metricShell,
                    axis: policy.metricShell.axis.slice() } }) },
                inverse: worldToLocal.toArray() };
        },

        async preload(targets = ['splat', 'mesh', 'collision']) {
            assertLive();
            let list = Array.isArray(targets) ? targets : [targets];
            const jobs = [];
            // Warming a mode the scene refuses would only download the stand-in.
            if (list.some((t) => t === 'splat' || t === 'mesh') && await pointsOnlyScene()) {
                for (const mode of list.filter((t) => t === 'splat' || t === 'mesh')) {
                    jobs.push(Promise.resolve({ mode, ok: true, skipped: 'scene_points_only' }));
                }
                list = list.filter((t) => t !== 'splat' && t !== 'mesh');
            }
            assertLive();
            if (list.includes('splat') && !state.splat) {
                jobs.push(loadSplat()
                    .then(() => ({ mode: 'splat', ok: true }))
                    .catch((e) => ({ mode: 'splat', ok: false, error: String(e?.message || e) })));
            }
            if (list.includes('mesh') && !state.mesh) {
                jobs.push(loadMesh()
                    .then(() => ({ mode: 'mesh', ok: true }))
                    .catch((e) => ({ mode: 'mesh', ok: false, error: String(e?.message || e) })));
            }
            if (list.includes('collision') && !state.collision) {
                jobs.push(loadCollision()
                    .then(() => ({ mode: 'collision', ok: true }))
                    .catch((e) => ({ mode: 'collision', ok: false, error: String(e?.message || e) })));
            }
            const results = await Promise.all(jobs);
            return { ok: results.every((r) => r.ok), results };
        },

        debugInfo() {
            return {
                identity,
                disposed,
                status: disposed ? 'disposed' : ((pending.size || splatLoading || meshLoading || collisionLoading) ? 'loading'
                    : (frameStatus === 'failed' ? 'failed' : (state.splat || state.mesh || state.collision ? 'ready' : 'idle'))),
                frameStatus,
                lastLoadError,
                pendingLoads: pending.size,
                activeDecoders: loadCounts.decoders,
                loadStatus: { ...loadStatus },
                loadCounts: { ...loadCounts },
                sparkUpdate: {
                    status: disposed ? (sparkUpdateJob ? 'disposed-pending' : 'disposed')
                        : sparkUpdateJob ? 'updating' : lastSparkError ? 'error' : 'idle',
                    activeUpdates: sparkUpdateJob ? 1 : 0,
                    pendingUpdates: sparkUpdateJob ? 1 : 0,
                    pendingCleanup: !!sparkUpdateJob?.terminalLease,
                    counts: { ...sparkUpdateCounts }, lastError: lastSparkError,
                },
                splat: !!state.splat,
                splatVisible: state.splat ? state.splat.visible : null,
                sparkVisible: state.sparkRenderer ? state.sparkRenderer.visible : null,
                splatSource: state.splatSource,
                numSplats: state.splat ? (state.splat.numSplats ?? null) : null,
                splatPipeline: state.splat?.pipelineReadback?.() ?? null,
                srParent: state.sparkRenderer ? (state.sparkRenderer.parent?.type || 'none') : 'none',
                meshSource: state.meshSource,
                meshFallback: state.meshFallback,
                photoFloorSupport: {
                    enabled: usePhotoFloor,
                    visible: usePhotoFloor && !!state.mesh?.visible && photoFloorUniforms.uPhotoFloorOnly.value > 0.5,
                    source: usePhotoFloor ? state.meshSource : null,
                    geometry: 'registered_display_mesh',
                    floorRangeM: [-0.25, 0.10],
                    writesDepth: false,
                },
                dollhouseCutaway: { ...desiredCutaway.policy },
                exteriorCleanup: { enabled: !!exteriorBounds, geometry: 'registered_display_mesh',
                    supportSigma: 3, centerPaddingM: 0.25 },
                collision: !!state.collision,
                collisionSource: state.collisionSource,
            };
        },

        dispose() {
            if (disposed) return false;
            disposed = true;
            state.modeSeq += 1;
            state.targetMode = 'points';
            state.fading = null;
            signal?.removeEventListener?.('abort', onLifetimeAbort);
            lifetime.abort();
            const spark = state.sparkRenderer;
            const splat = state.splat;
            const cutaway = state.splatCutaway;
            // Immediate detachment keeps the engine's synchronous scene walk
            // from disposing a resource still used by the owned async update.
            spark?.removeFromParent?.();
            splat?.removeFromParent?.();
            state.sparkRenderer = null;
            state.splat = null;
            state.splatCutaway = null;
            if (sparkUpdateJob) sparkUpdateJob.terminalLease = [spark, splat, cutaway];
            else { cutaway?.dispose?.(); releaseAsset(spark); releaseAsset(splat); }
            for (const key of ['mesh', 'collision']) {
                releaseAsset(state[key]);
                state[key] = null;
            }
            listeners.length = 0;
            return true;
        },

        update(dt) {
            if (disposed) return;
            if (!state.fading) return;
            const f = state.fading;
            f.t += dt * 1000;
            const k = easeInOutCubic(Math.min(1, f.t / f.duration));
            const pointsOpacity = f.dir > 0 ? 1 - k : k;
            pointsMaterial.uniforms.uOpacity.value = pointsOpacity;
            if (state.splat && 'opacity' in state.splat) state.splat.opacity = 1 - pointsOpacity;
            if (f.t >= f.duration) {
                state.fading = null;
                visibleFor(state.mode);
            }
        },
    };
    // The engine and older callers still say tickSpark.
    modes.tickSpark = modes.tickSplat;
    const onLifetimeAbort = () => modes.dispose();
    signal?.addEventListener?.('abort', onLifetimeAbort, { once: true });
    if (signal?.aborted) modes.dispose();
    return modes;
}
