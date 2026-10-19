/* home-3d/engine.js — ESM entry for the 3D apartment engine.
 *
 * Bridge contract (see index.html): this module is dynamically imported by a
 * <script type="module"> bootstrap which resolves window.Home3D.ready with the
 * module namespace. Babel-side UI code awaits Home3D.ready and touches nothing
 * else on Home3D — that discipline is what makes the no-bundler ESM/Babel split
 * race-free.
 *
 * Sibling modules are imported DYNAMICALLY with the same ?cb= query this module
 * was loaded with — static imports would dodge the app's cache-busting policy
 * (WebView2 disk-caches asset:// aggressively; see index.html loader comment).
 * Vendor libs (bare 'three', '@sparkjsdev/spark') are version-dirred instead.
 *
 * The engine instance stays resident after the view closes (rAF stopped,
 * GPU buffers kept) so reopening is instant.
 */
import * as THREE from 'three';

const cb = new URL(import.meta.url).search || '';
const sib = (name) => import(`./${name}${cb}`);

/* The classic-script prewarmer calls this same classifier before the engine
 * exists, so it warms the asset the later loader will actually request. */
export async function selectApartmentPerformanceTarget({
    navigatorRef = globalThis.navigator,
    windowRef = globalThis.window,
} = {}) {
    const adaptiveM = await sib('adaptive-performance.mjs');
    return adaptiveM.selectClientPerformanceTarget({ navigatorRef, windowRef });
}

/* Keep the classic prewarmer on the exact same immutable asset plan consumed
 * by modes.js without making the classic-script layer duplicate ESM policy. */
export async function apartmentAssetPlanForPerformanceTarget(target) {
    const modesM = await sib('modes.js');
    return modesM.apartmentAssetPlanForPerformanceTarget(target);
}

/* Classic prewarming uses the same site/revision fences as every loader. */
export async function apartmentAssetCandidates(name, options = {}) {
    const assetsM = await sib('assets.js');
    return assetsM.candidates(name, null, options);
}

/* The classic warmer and the mode loaders share this versioned ESM singleton. */
export async function warmApartmentAssetBytes(options) {
    const cacheM = await sib('asset-byte-cache.mjs');
    return cacheM.warmApartmentAssetBytes(options);
}

/* Optional display evidence beside the unchanged marker API. All binding
 * checks are synchronous; no pending digest or private document survives reset. */
export function bindEquipmentCutawayRegistration({ overlay, sceneIdentity, manifest,
    getManifest = () => manifest, isCurrent = () => true, enabled = true,
    core = globalThis.HomeEquipmentRegistration, deriveShell } = {}) {
    const original = overlay?.setEquipmentRegistration;
    const hashes = new WeakMap();
    const sha = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
    const deepFrozen = value => !value || typeof value !== 'object'
        || Object.isFrozen(value) && Object.values(value).every(deepFrozen);
    const freeze = value => {
        if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); }
        return value;
    };
    const digest = value => {
        if (hashes.has(value)) return hashes.get(value);
        const result = core.digest(value);
        if (deepFrozen(value)) hashes.set(value, result);
        return result;
    };
    let disposed = false, roomShell = null, expected = null, adopted = null, reason = 'registration_unavailable';
    try {
        if (enabled && sceneIdentity?.siteId === 'victoria' && typeof original === 'function'
            && typeof deriveShell === 'function' && typeof core?.digest === 'function'
            && typeof core?.validateDocument === 'function' && typeof core?.canonicalJson === 'function'
            && manifest?.schemaVersion === 1 && manifest.siteId === sceneIdentity.siteId && manifest.sceneAvailable === true
            && manifest.revision === sceneIdentity.contentRevision && manifest.registrationVersion === sceneIdentity.registrationVersion
            && manifest.assets?.['frame.json']?.sha256 === sceneIdentity.frameDigest
            && sha(sceneIdentity.frameDigest) && sha(manifest.assets?.['collision.glb']?.sha256)) {
            expected = Object.freeze({ content_revision: manifest.revision, registration_version: manifest.registrationVersion,
                manifest_digest: digest(manifest), frame_digest: sceneIdentity.frameDigest,
                canonical_collision_digest: manifest.assets['collision.glb'].sha256 });
            if (!sha(expected.manifest_digest)) expected = null;
        }
    } catch (_) { expected = null; }
    function clear(nextReason) { roomShell = null; adopted = null; reason = nextReason; }
    function current() {
        if (disposed || !expected || isCurrent() !== true) return false;
        const value = getManifest();
        return value?.siteId === sceneIdentity.siteId && value.revision === expected.content_revision
            && value.registrationVersion === expected.registration_version && value.sceneAvailable === true
            && value.assets?.['frame.json']?.sha256 === expected.frame_digest
            && value.assets?.['collision.glb']?.sha256 === expected.canonical_collision_digest
            && digest(value) === expected.manifest_digest;
    }
    function setRegistration(...args) {
        clear('registration_unavailable');
        const result = original.apply(this, args); // Preserve receiver, options and return identity.
        try {
            const document = args[0];
            if (!current()) { clear('scene_identity_changed'); return result; }
            if (!document || document.references?.confirmed !== true || !sha(document.digest)
                || !core.validateDocument(document, { binding: expected, verifyPositions: false }).ok) return result;
            const copy = JSON.parse(core.canonicalJson(document));
            const shell = deriveShell(copy, expected);
            if (shell && current()) {
                roomShell = freeze(JSON.parse(core.canonicalJson(shell)));
                adopted = { revision: copy.revision, digest: copy.digest }; reason = null;
            }
        } catch (_) { clear('registration_invalid'); }
        return result;
    }
    if (typeof original === 'function') overlay.setEquipmentRegistration = setRegistration;
    function getShell() {
        if (disposed) return null;
        try { if (!current()) clear('scene_identity_changed'); }
        catch (_) { clear('scene_identity_changed'); }
        return roomShell;
    }
    return Object.freeze({
        getShell,
        status() {
            getShell();
            return { admitted: !!roomShell, disposed, reason,
                documentRevision: adopted?.revision ?? null, documentDigest: adopted?.digest ?? null };
        },
        dispose() {
            if (disposed) return;
            disposed = true; clear('disposed');
            if (overlay?.setEquipmentRegistration === setRegistration) overlay.setEquipmentRegistration = original;
        },
    });
}

/* Resolve target and persisted user quality together so idle prewarm cannot
 * choose a different first asset from the runtime loader. */
export async function selectApartmentAssetPlan({
    navigatorRef = globalThis.navigator,
    windowRef = globalThis.window,
    viewSelection = null,
} = {}) {
    const [adaptiveM, modesM, viewOptionsM] = await Promise.all([
        sib('adaptive-performance.mjs'),
        sib('modes.js'),
        sib('world-view-options.mjs'),
    ]);
    const targetTier = adaptiveM.selectClientPerformanceTarget({ navigatorRef, windowRef });
    const performanceMode = viewOptionsM.resolveSelection(viewSelection)
        .quality.performanceMode;
    return modesM.apartmentAssetPlanForPerformanceTarget({ targetTier, mode: performanceMode });
}

export async function preloadModules({ includeRenderers = true } = {}) {
    const jobs = [
        sib('pointcloud.js'),
        sib('rig.js'),
        sib('modes.js'),
        sib('markers.js'),
        sib('picking.js'),
        sib('assets.js'),
    ];
    if (includeRenderers) {
        jobs.push(import('three/addons/loaders/GLTFLoader.js'));
        jobs.push(import('@sparkjsdev/spark'));
    }
    const results = await Promise.allSettled(jobs);
    return {
        ok: results.every((r) => r.status === 'fulfilled'),
        loaded: results.filter((r) => r.status === 'fulfilled').length,
        failed: results
            .filter((r) => r.status === 'rejected')
            .map((r) => String(r.reason?.message || r.reason || 'module preload failed')),
    };
}

export async function createEngine({ canvas, hostEl, sim = false, siteId = null, contentRevision = undefined,
    performanceController: sharedPerformanceController = null, signal = null, loadAdapters = undefined }) {
    const assertCreationLive = () => {
        if (signal?.aborted) throw new DOMException('scene_load_aborted', 'AbortError');
    };
    assertCreationLive();
    await window.HomeContentPacks?.ready;
    assertCreationLive();
    siteId = siteId || window.HomeSpatialViewport?.getSelectedSiteId?.() || 'echo';
    if (contentRevision === undefined) contentRevision = window.HomeContentPacks?.get?.(siteId)?.manifest?.revision ?? null;
    const pinnedPack = window.HomeContentPacks?.get?.(siteId, contentRevision)?.manifest;
    const sceneIdentity = Object.freeze({
        siteId, contentRevision,
        registrationVersion: pinnedPack?.registrationVersion
            ?? window.HomeSpatialViewport?.getSite?.(siteId)?.sceneRegistration?.version ?? null,
        frameDigest: pinnedPack?.assets?.['frame.json']?.sha256 ?? null,
    });
    const [pointcloudM, rigM, modesM, markersM, pickingM, assetsM, adaptiveM, placeholderM,
        cutawayM, cutawayMaterialM] = await Promise.all([
        sib('pointcloud.js'), sib('rig.js'), sib('modes.js'),
        sib('markers.js'), sib('picking.js'), sib('assets.js'),
        sib('adaptive-performance.mjs'), sib('scene-placeholder.mjs'),
        sib('dollhouse-cutaway.mjs'), sib('dollhouse-cutaway-material.js'),
    ]);
    assertCreationLive();
    const lifetimeController = new AbortController();
    /*
     * The handoff capture's readback helpers, loaded with the engine rather
     * than inside captureFrame: there, the first outward handoff of every page
     * load fetched and compiled both modules inside the prime it is timed by.
     * They hold no state, so these cache-busted copies beside the world
     * surface's own are harmless. A failed load keeps the synchronous copy.
     */
    const captureReadbackModules = Promise.all([
        sib('async-frame-readback.mjs').catch(() => null),
        sib('world-surface-core.mjs').catch(() => null),
    ]);

    /*
     * frame.json, read once per engine and shared.
     *
     * It used to be read only when a splat loaded (for T_splat). It now also
     * says whether this interior is a disclosed placeholder, which the badge
     * and the points-only rule need from the first frame, so it is requested
     * at boot, beside the points. A failed read is not cached: the next
     * caller asks again rather than inheriting a transient miss.
     */
    let sceneFrame = null;
    function readSceneFrame() {
        if (!sceneFrame) {
            const pending = assetsM.fetchFrame({ sim, siteId, contentRevision, signal: lifetimeController.signal }).catch(() => null);
            sceneFrame = pending;
            pending.then((frame) => { if (!frame && sceneFrame === pending) sceneFrame = null; });
        }
        return sceneFrame;
    }
    let scenePlaceholder = null;
    const readScenePlaceholder = async () => {
        scenePlaceholder = placeholderM.scenePlaceholderFromFrame(await readSceneFrame());
        return scenePlaceholder;
    };
    const scenePlaceholderPromise = readScenePlaceholder();

    /*
     * One governor follows the viewport across the Apartment/world seam.
     * Device classification stays deliberately coarse; actual frame cadence
     * takes over immediately. Passing this same controller into Cesium prevents
     * two independent ladders from alternately raising and lowering quality.
     */
    const navigatorRef = window.navigator;
    const performanceController = sharedPerformanceController || adaptiveM.createAdaptivePerformanceController({
        target: adaptiveM.selectClientPerformanceTarget({
            navigatorRef,
            windowRef: window,
        }),
    });
    const performanceSamplingSource = Symbol('apartment-renderer');

    const renderer = new THREE.WebGLRenderer({
        canvas, antialias: true, alpha: true, powerPreference: 'high-performance',
    });
    renderer.setPixelRatio(Math.min(
        window.devicePixelRatio || 1,
        performanceController.getBudget().apartmentDprCap,
    ));
    renderer.setClearColor(0x000000, 0);

    const scene = new THREE.Scene();
    scene.background = null;

    const camera = new THREE.PerspectiveCamera(35, 1, 0.05, 200);

    // THE coordinate conversion: apartment frame is Z-up, three is Y-up.
    const apartmentRoot = new THREE.Group();
    apartmentRoot.rotation.x = -Math.PI / 2;
    scene.add(apartmentRoot);
    apartmentRoot.updateMatrixWorld(true);

    const pointsMaterial = pointcloudM.createPointsMaterial();
    const rig = rigM.createRig(camera);
    // Victoria's scan needs a two-sided shell cut. Echo keeps its established
    // presentation; this changes no registered geometry or picking proxy.
    const cutawayEligible = siteId === 'victoria' && !sim;
    let dollhouseContext = { live: false, calibrating: false };
    const cutawayInverse = new THREE.Matrix4();
    const cutawayCamera = new THREE.Vector3();
    const modes = modesM.createModes({
        apartmentRoot, pointsMaterial, sim, scene, renderer, camera,
        performanceController,
        signal: lifetimeController.signal, siteId, contentRevision,
        sceneIdentity,
        requireRegistration: true, loadAdapters,
        assetCandidates: (name, simName, options = {}) => assetsM.candidates(name, simName, { ...options, sim, siteId, contentRevision }),
        fetchFrame: readSceneFrame,
        getPoints: () => points,
        scenePointsOnly: async () => (scenePlaceholder
            ?? await readScenePlaceholder())?.pointsOnly === true,
        dollhouseCutaway: cutawayEligible,
        photoFloorSupport: cutawayEligible,
    });
    const overlay = markersM.createOverlay(apartmentRoot);
    const picking = pickingM.createPicking(camera, hostEl);
    let disposed = false;
    const uiLayers = new Set();
    const uiCollisionDigest = pinnedPack?.assets?.['collision.glb']?.sha256 ?? null;
    const retireUiLayer = layer => {
        try { layer.dispose(); } catch (_) { /* Other layers and renderer cleanup still run. */ }
        finally { uiLayers.delete(layer); }
    };
    const equipmentCutaway = bindEquipmentCutawayRegistration({
        overlay, sceneIdentity, manifest: pinnedPack,
        enabled: cutawayEligible && !window.HomeLiveSceneShadow?.active,
        deriveShell: cutawayM.deriveRegisteredDollhouseShell,
        core: window.HomeEquipmentRegistration || globalThis.HomeEquipmentRegistration,
        getManifest: () => {
            const active = window.HomeContentPacks?.get?.(siteId)?.manifest;
            return active?.revision === contentRevision ? window.HomeContentPacks?.get?.(siteId, contentRevision)?.manifest : null;
        },
        isCurrent: () => !disposed && !lifetimeController.signal.aborted && !window.HomeLiveSceneShadow?.active
            && (!window.HomeSpatialViewport?.getSelectedSiteId || window.HomeSpatialViewport.getSelectedSiteId() === siteId),
    });

    // GPU identity — the WebView2 dual-GPU gate (plan §12 / spike S1)
    let gpuName = 'unknown';
    try {
        const gl = renderer.getContext();
        const ext = gl.getExtension('WEBGL_debug_renderer_info');
        if (ext) gpuName = gl.getParameter(ext.UNMASKED_RENDERER_WEBGL);
    } catch (e) { /* */ }
    console.log(`[home-3d] renderer: ${gpuName}`);

    const emitter = new EventTarget();
    const emit = (type, detail) => emitter.dispatchEvent(new CustomEvent(type, { detail }));

    // Context-loss resilience: three r180 preventDefaults 'webglcontextlost'
    // internally and rebuilds GL state on 'webglcontextrestored', but renders
    // NOTHING in between and the loss is otherwise silent — surface both so
    // the UI can tell the user instead of showing a dead black canvas.
    const onApartmentContextLost = () => {
        console.warn('[home-3d] WebGL context LOST — engine canvas is blank until restored');
        emit('contextlost', {});
    };
    const onApartmentContextRestored = () => {
        console.log('[home-3d] WebGL context restored');
        emit('contextrestored', {});
    };
    canvas.addEventListener('webglcontextlost', onApartmentContextLost);
    canvas.addEventListener('webglcontextrestored', onApartmentContextRestored);

    // ---- point cloud (progressive) ----
    let points = null;
    let pointTotal = 0;
    let fitted = false;
    let lastFitBox = null;
    let lastFitLocalBox = null;

    function fitNow(options = {}) {
        if (!points) return;
        points.geometry.computeBoundingBox();
        const box = points.geometry.boundingBox.clone();
        lastFitLocalBox = { min: box.min.toArray(), max: box.max.toArray() };
        box.applyMatrix4(apartmentRoot.matrixWorld);
        lastFitBox = box.clone();
        rig.fitBounds(box, options);
    }

    function projectedFitBounds() {
        if (!lastFitBox) return null;
        const canvasWidth = renderer.domElement.clientWidth || renderer.domElement.width || 1;
        const canvasHeight = renderer.domElement.clientHeight || renderer.domElement.height || 1;
        const corners = [
            new THREE.Vector3(lastFitBox.min.x, lastFitBox.min.y, lastFitBox.min.z),
            new THREE.Vector3(lastFitBox.min.x, lastFitBox.min.y, lastFitBox.max.z),
            new THREE.Vector3(lastFitBox.min.x, lastFitBox.max.y, lastFitBox.min.z),
            new THREE.Vector3(lastFitBox.min.x, lastFitBox.max.y, lastFitBox.max.z),
            new THREE.Vector3(lastFitBox.max.x, lastFitBox.min.y, lastFitBox.min.z),
            new THREE.Vector3(lastFitBox.max.x, lastFitBox.min.y, lastFitBox.max.z),
            new THREE.Vector3(lastFitBox.max.x, lastFitBox.max.y, lastFitBox.min.z),
            new THREE.Vector3(lastFitBox.max.x, lastFitBox.max.y, lastFitBox.max.z),
        ];
        camera.updateMatrixWorld(true);
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        let outOfDepth = false;
        for (const corner of corners) {
            const p = corner.clone().project(camera);
            if (p.z < -1 || p.z > 1) outOfDepth = true;
            const x = (p.x * 0.5 + 0.5) * canvasWidth;
            const y = (-p.y * 0.5 + 0.5) * canvasHeight;
            minX = Math.min(minX, x);
            minY = Math.min(minY, y);
            maxX = Math.max(maxX, x);
            maxY = Math.max(maxY, y);
        }
        const portrait = camera.aspect < 0.78;
        const safe = portrait
            ? {
                left: 8,
                right: canvasWidth - 8,
                top: Math.min(176, Math.round(canvasHeight * 0.22)),
                bottom: canvasHeight - Math.min(130, Math.round(canvasHeight * 0.16)),
            }
            : { left: 4, right: canvasWidth - 4, top: 4, bottom: canvasHeight - 4 };
        return {
            viewport: { width: canvasWidth, height: canvasHeight, aspect: camera.aspect },
            bounds: {
                minX: Math.round(minX),
                minY: Math.round(minY),
                maxX: Math.round(maxX),
                maxY: Math.round(maxY),
                width: Math.round(maxX - minX),
                height: Math.round(maxY - minY),
            },
            safe,
            outOfDepth,
            ok: !outOfDepth && minX >= safe.left && maxX <= safe.right
                && minY >= safe.top && maxY <= safe.bottom,
        };
    }

    function intrinsicsParams(intr) {
        if (!intr || !intr.K || !Array.isArray(intr.image_size)) return null;
        const w = +intr.image_size[0];
        const h = +intr.image_size[1];
        if (!(w > 0) || !(h > 0)) return null;
        const K = intr.K;
        const fx = Array.isArray(K[0]) ? +K[0][0] : +K[0];
        const fy = Array.isArray(K[0]) ? +K[1][1] : +K[4];
        const cx = Array.isArray(K[0]) ? +K[0][2] : +K[2];
        const cy = Array.isArray(K[0]) ? +K[1][2] : +K[5];
        if (![fx, fy, cx, cy].every((v) => Number.isFinite(v) && v > 0)) return null;
        return { fx, fy, cx, cy, w, h };
    }

    function projectionFrameParams(frame) {
        if (!frame) return null;
        const vw = +frame.viewportWidth;
        const vh = +frame.viewportHeight;
        const left = +frame.left || 0;
        const top = +frame.top || 0;
        const width = +frame.width;
        const height = +frame.height;
        if (!(vw > 0) || !(vh > 0) || !(width > 0) || !(height > 0)) return null;
        return {
            scaleX: width / vw,
            scaleY: height / vh,
            offsetX: ((2 * left) + width) / vw - 1,
            offsetY: 1 - ((2 * top) + height) / vh,
            fovScaleY: vh / height,
        };
    }

    function fovFromIntrinsics(intr, fovScale = 1, projectionFrame = null) {
        const p = intrinsicsParams(intr);
        if (!p) return null;
        const frame = projectionFrameParams(projectionFrame);
        const scale = frame ? frame.fovScaleY : Math.max(0.001, fovScale || 1);
        const fy = p.fy / scale;
        return THREE.MathUtils.radToDeg(2 * Math.atan(p.h / (2 * fy)));
    }

    function projectionFromIntrinsics(intr, fovScale = 1, projectionFrame = null) {
        const p = intrinsicsParams(intr);
        if (!p) return null;
        const scale = Math.max(0.001, fovScale || 1);
        const fx = p.fx / scale;
        const fy = p.fy / scale;
        const cx = p.w / 2 + (p.cx - p.w / 2) / scale;
        const cy = p.h / 2 + (p.cy - p.h / 2) / scale;
        const frame = projectionFrameParams(projectionFrame);
        const sx = frame ? frame.scaleX : 1;
        const sy = frame ? frame.scaleY : 1;
        const ox = frame ? frame.offsetX : 0;
        const oy = frame ? frame.offsetY : 0;
        const n = camera.near;
        const f = camera.far;
        return new THREE.Matrix4().set(
            sx * (2 * fx / p.w), 0, sx * (1 - 2 * cx / p.w) - ox, 0,
            0, sy * (2 * fy / p.h), sy * (2 * cy / p.h - 1) - oy, 0,
            0, 0, -(f + n) / (f - n), -(2 * f * n) / (f - n),
            0, 0, -1, 0,
        );
    }

    async function loadPoints() {
        const frame = await readSceneFrame();
        if (lifetimeController.signal.aborted) throw new DOMException('scene_load_aborted', 'AbortError');
        modesM.validateSceneFrame(frame, { siteId });
        let lastErr = null;
        for (const url of assetsM.pointsCandidates({ sim, siteId, contentRevision })) {
            try {
                const result = await pointcloudM.loadPointCloud(url, pointsMaterial, {
                    signal: lifetimeController.signal,
                    onProgress: (loaded, total) => {
                        pointTotal = total;
                        emit('progress', { loaded, total });
                        if (!fitted && loaded > total * 0.2 && points) {
                            fitted = true;
                            fitNow();
                        }
                    },
                });
                return result;
            } catch (e) {
                lastErr = e;
                /* Terminal teardown aborts the whole candidate search. Trying
                 * each fallback with an already-aborted signal is noisy and
                 * can keep the disposed engine's promise chain alive longer. */
                if (lifetimeController.signal.aborted) throw e;
                console.warn(`[home-3d] points failed: ${url}`, e);
            }
        }
        throw lastErr || new Error('no point asset reachable');
    }

    const pointsPromise = (async () => {
        const result = await loadPoints();
        if (disposed) {
            result.geometry?.dispose?.();
            return result.points;
        }
        points = result.points;
        apartmentRoot.add(points);
        fitNow();
        emit('loaded', { total: result.total });
        return points;
    })();

    // expose the in-flight Points object so the fit-at-20% works
    pointsPromise.catch(() => { /* surfaced via UI await */ });

    // ---- render loop with frame governor ----
    let running = false;
    let rafId = 0;
    let last = performance.now();
    const frameTimes = [];
    const renderTimes = [];
    let statsAt = 0;
    let appliedPerformanceBudgetId = performanceController.getBudget().id;

    function applyPerformanceBudget({ force = false } = {}) {
        const budget = performanceController.getBudget();
        const nextPixelRatio = Math.min(
            window.devicePixelRatio || 1,
            budget.apartmentDprCap,
        );
        if (force || appliedPerformanceBudgetId !== budget.id
            || Math.abs(renderer.getPixelRatio() - nextPixelRatio) > 0.01) {
            renderer.setPixelRatio(nextPixelRatio);
            pointsMaterial.uniforms.uPixelRatio.value = nextPixelRatio;
            appliedPerformanceBudgetId = budget.id;
        }
        return budget;
    }

    /* Decisions and explicit mode/target commits publish synchronously. This
     * keeps the resident Apartment renderer converged even when Cesium supplied
     * the sample that moved the shared ladder. */
    const unsubscribePerformanceBudget = performanceController.subscribe(() => {
        if (!disposed) applyPerformanceBudget();
    });

    function frame(now) {
        if (!running) return;
        rafId = requestAnimationFrame(frame);
        const frameTimeMs = Math.max(0.5, now - last);
        const dt = Math.min(0.1, frameTimeMs / 1000);
        last = now;

        pointsMaterial.uniforms.uTime.value = now / 1000;
        rig.update(dt);
        const inCamPose = rig.inCameraPose && rig.inCameraPose();
        apartmentRoot.updateWorldMatrix(true, false);
        cutawayInverse.copy(apartmentRoot.matrixWorld).invert();
        camera.getWorldPosition(cutawayCamera).applyMatrix4(cutawayInverse);
        const cutaway = cutawayM.deriveDollhouseCutaway({
            bounds: lastFitLocalBox,
            roomShell: equipmentCutaway.getShell(),
            cameraLocal: cutawayCamera.toArray(),
            enabled: cutawayEligible && scenePlaceholder?.pointsOnly !== true,
            heldCamera: !!inCamPose,
            live: dollhouseContext.live,
            calibrating: dollhouseContext.calibrating,
        });
        cutawayMaterialM.updateCutawayUniforms(pointsMaterial.uniforms, cutaway, cutawayInverse);
        modes.setDollhouseCutaway(cutaway, cutawayInverse);
        modes.tickSpark && modes.tickSpark(camera, now / 1000);
        // the EL depth-brightness window tracks the orbit radius so the
        // falloff aesthetic holds at every zoom detent (static 6/30 left the
        // overview pose entirely past the far fade — verified visually)
        const orbitR = rig.currentRadius();
        pointsMaterial.uniforms.uFadeNear.value = orbitR * 0.30;
        pointsMaterial.uniforms.uFadeFar.value = orbitR * 1.55;
        // wall-fade (P8): only at room zoom — the near wall melts away so the
        // interior stays readable; off at overview (dollhouse reads whole)
        if (pointsMaterial.uniforms.uWallNear) {
            pointsMaterial.uniforms.uWallNear.value = !cutawayEligible
                && !dollhouseContext.live && !dollhouseContext.calibrating
                && orbitR < 9.0 ? orbitR * 0.75 : 0.0;
        }
        // point size law (S2): the ceiling shrinks and a depth floor appears
        // as the detents close in, so the closest view is fine grain rather
        // than fat discs. A held camera pose is not on the orbit and keeps the
        // old fixed ceiling.
        if (pointsMaterial.uniforms.uMaxPointPx) {
            const limits = inCamPose
                ? { maxPointPx: pointcloudM.POINT_SIZE_OVERVIEW_MAX_PX, depthFloor: 0 }
                : pointcloudM.pointSizeLimits({
                    orbitRadius: orbitR,
                    overviewRadius: rig.detentRadius(0),
                    closestRadius: rig.detentRadius(rig.detentCount() - 1),
                    pixelRatio: pointsMaterial.uniforms.uPixelRatio.value,
                });
            pointsMaterial.uniforms.uMaxPointPx.value = limits.maxPointPx;
            pointsMaterial.uniforms.uDepthFloor.value = limits.depthFloor;
        }
        if (modes.setMeshNearCut) {
            // melt near geometry at room zoom, but NEVER while sitting at a
            // camera's pose (the wall behind the camera must stay solid so the
            // live feed blends into continuous mesh)
            modes.setMeshNearCut(!cutawayEligible && !inCamPose
                && !dollhouseContext.live && !dollhouseContext.calibrating
                && orbitR < 9.0 ? orbitR * 0.55 : 0.0);
        }
        if (inCamPose && pointsMaterial.uniforms.uWallNear) {
            pointsMaterial.uniforms.uWallNear.value = 0.0;
        }
        modes.update(dt);
        overlay.update(dt);
        if (uiLayers.size) for (const layer of uiLayers) {
            try { layer.update(); } catch (_) { retireUiLayer(layer); }
        }
        renderer.render(scene, camera);

        const renderTimeMs = Math.max(0, performance.now() - now);
        frameTimes.push(frameTimeMs);
        if (frameTimes.length > 120) frameTimes.shift();
        renderTimes.push(renderTimeMs);
        if (renderTimes.length > 120) renderTimes.shift();

        performanceController.recordFrame(frameTimeMs, {
            now,
            active: true,
            samplingSource: performanceSamplingSource,
        });

        if (now - statsAt > 500) {
            statsAt = now;
            const sorted = [...frameTimes].sort((a, b) => a - b);
            const sortedRender = [...renderTimes].sort((a, b) => a - b);
            const p95 = sorted[Math.floor(sorted.length * 0.95)] || 0;
            const fps = 1000 / Math.max(0.5, sorted[Math.floor(sorted.length * 0.5)] || 16.7);
            emit('stats', {
                fps, p95, gpu: gpuName, points: pointTotal,
                pixelRatio: renderer.getPixelRatio(),
                renderP95: sortedRender[Math.floor(sortedRender.length * 0.95)] || 0,
                calls: renderer.info.render.calls,
                tris: renderer.info.render.triangles,
                modeDebug: modes.debugInfo ? modes.debugInfo() : null,
                performance: performanceController.getDiagnostics(),
            });
        }
    }

    const engine = {
        scene, camera, renderer, rig, modes, overlay, picking, apartmentRoot,
        pointsMaterial, pointsPromise,
        gpu: gpuName,
        siteId,
        contentRevision,
        sceneIdentity,
        getEquipmentCutawayRegistrationStatus: () => equipmentCutaway.status(),
        displaySurfaceHit(clientX, clientY) {
            const collision = modes.getCollision?.();
            if (!collision) return null;
            const policy = modes.debugInfo?.().dollhouseCutaway;
            return picking.surfaceHit(apartmentRoot, [collision], clientX, clientY, {
                acceptPoint: point => cutawayM.dollhouseKeep(point, policy) > .05,
            });
        },
        async createUiOverlayLayer(options = {}) {
            const { host, isCurrent, signal: layerSignal } = options || {};
            if (typeof isCurrent !== 'function' || typeof layerSignal?.aborted !== 'boolean'
                || typeof layerSignal.addEventListener !== 'function'
                || typeof layerSignal.removeEventListener !== 'function') return null;
            const identityKeys = Object.keys(sceneIdentity);
            const ownerCurrent = () => {
                try {
                    if (disposed || lifetimeController.signal.aborted || layerSignal.aborted
                        || window.HomeLiveSceneShadow?.active || !host?.isConnected
                        || picking.getHost() !== host || !host.contains?.(canvas)
                        || engine.siteId !== siteId || engine.contentRevision !== contentRevision
                        || identityKeys.some(key => engine.sceneIdentity?.[key] !== sceneIdentity[key])) return false;
                    const selectedSite = window.HomeSpatialViewport?.getSelectedSiteId?.();
                    if (selectedSite && selectedSite !== siteId) return false;
                    if (contentRevision != null) {
                        const manifest = window.HomeContentPacks?.get?.(siteId)?.manifest;
                        const registrationVersion = manifest?.registrationVersion
                            ?? window.HomeSpatialViewport?.getSite?.(siteId)?.sceneRegistration?.version ?? null;
                        if (manifest?.revision !== contentRevision
                            || registrationVersion !== sceneIdentity.registrationVersion
                            || (manifest.assets?.['frame.json']?.sha256 ?? null) !== sceneIdentity.frameDigest
                            || (manifest.assets?.['collision.glb']?.sha256 ?? null) !== uiCollisionDigest) return false;
                    } else {
                        const version = window.HomeSpatialViewport?.getSite?.(siteId)?.sceneRegistration?.version ?? null;
                        if (version !== sceneIdentity.registrationVersion) return false;
                    }
                    return true;
                } catch (_) { return false; }
            };
            const current = () => {
                try { return ownerCurrent() && isCurrent() === true && ownerCurrent(); }
                catch (_) { return false; }
            };
            if (!current()) return null;
            let layer = null, retired = false;
            try {
                const module = await sib('apartment-ui-overlay-bridge.mjs');
                if (!current()) return null;
                layer = module.createApartmentUiOverlayLayer({
                    THREE, camera, apartmentRoot, overlayRoot: overlay.overlayRoot, host,
                    projectWorld: point => picking.projectToScreen(point),
                    pickSurface: (x, y) => engine.displaySurfaceHit(x, y),
                    isCurrent: current, signal: layerSignal,
                    onDispose: () => { retired = true; uiLayers.delete(layer); },
                });
                if (!layer || typeof layer.update !== 'function' || typeof layer.dispose !== 'function') return null;
                if (retired || !current()) { retireUiLayer(layer); return null; }
                uiLayers.add(layer);
                return layer;
            } catch (_) {
                if (layer) retireUiLayer(layer);
                return null;
            }
        },
        performanceController,
        /* The disclosed placeholder this interior declares, or null for a
         * scan. The host shows "<home> · scan pending" and offers points only
         * while it is set; the promise settles once frame.json has been read. */
        scenePlaceholderPromise,
        get scenePlaceholder() { return scenePlaceholder; },
        async captureFrame({ scale = 0.25, signal } = {}) {
            if (disposed || signal?.aborted || renderer.getContext().isContextLost()) return null;
            await pointsPromise;
            /* Before the render: the render and the read must share a task.
             * Loaded with the engine, so this is already settled. */
            const [readbackM, coreM] = await captureReadbackModules;
            if (disposed || signal?.aborted || renderer.getContext().isContextLost()) return null;
            // Render and copy in one task: the default framebuffer need not be
            // preserved between frames. This does not advance the camera clock.
            modes.tickSpark?.(camera, performance.now() / 1000);
            renderer.render(scene, camera);
            const source = renderer.domElement;
            const width = Math.max(1, Math.round(source.width * Math.min(1, Math.max(0.01, scale))));
            const height = Math.max(1, Math.round(source.height * Math.min(1, Math.max(0.01, scale))));
            /*
             * Read it back without waiting on the GPU.
             *
             * drawImage + getImageData below is a synchronous readback: the
             * main thread stalls until the GPU has finished everything queued
             * before it. At the outward commit that queue holds the world's
             * first frame, and the wait measured 34-41 ms inside the handoff
             * prime, with the Apartment frozen. The PBO + fence path queues the
             * copy now and collects it on a later frame (the world surface
             * already captures this way). This canvas is multisampled: the
             * helper resolves it at full size and scales it on the GPU, so only
             * the compositor-sized capture crosses to the CPU. The CPU
             * reduction below runs only if the GPU refused the scale and the
             * full-size copy came back instead.
             */
            const gl = renderer.getContext();
            const readback = readbackM?.beginAsyncPixelPackReadback?.({
                gl,
                width: gl.drawingBufferWidth,
                height: gl.drawingBufferHeight,
                targetWidth: Math.min(width, gl.drawingBufferWidth),
                targetHeight: Math.min(height, gl.drawingBufferHeight),
            }) ?? null;
            if (readback && coreM?.downsampleRgbaFrame) {
                const cancel = () => readback.cancel();
                signal?.addEventListener?.('abort', cancel, { once: true });
                try {
                    const frame = await readback.promise;
                    if (!frame || disposed || signal?.aborted) return null;
                    const pixels = frame.width === width && frame.height === height
                        ? frame.pixels
                        : coreM.downsampleRgbaFrame(frame.pixels, frame.width, frame.height, width, height);
                    // GL rows run bottom-up; the compositor flips on bottomUp.
                    return { width, height, bottomUp: true, pixels };
                } catch (_) {
                    return null;
                } finally {
                    signal?.removeEventListener?.('abort', cancel);
                }
            }
            readback?.cancel?.();
            const copy = source.ownerDocument.createElement('canvas');
            copy.width = width;
            copy.height = height;
            const context = copy.getContext('2d', { willReadFrequently: true });
            if (!context) return null;
            try {
                context.fillStyle = '#000';
                context.fillRect(0, 0, width, height);
                context.drawImage(source, 0, 0, width, height);
                return { width, height, bottomUp: false, pixels: context.getImageData(0, 0, width, height).data };
            } catch (_) { return null; }
            finally { copy.width = copy.height = 1; }
        },
        on(type, cb_) {
            if (disposed) return () => {};
            // returns an unsubscribe — the view remounts against the resident
            // engine, so un-removable listeners pile up stale setState closures
            const h = (e) => cb_(e.detail);
            emitter.addEventListener(type, h);
            return () => emitter.removeEventListener(type, h);
        },
        setRunning(v) {
            if (disposed) return false;
            if (v === running) return;
            running = v;
            if (v) {
                performanceController.setSamplingSourceActive(
                    performanceSamplingSource,
                    true,
                    { priority: 20, now: performance.now() },
                );
                applyPerformanceBudget({ force: true });
                last = performance.now();
                rafId = requestAnimationFrame(frame);
            } else {
                cancelAnimationFrame(rafId);
                performanceController.setSamplingSourceActive(
                    performanceSamplingSource,
                    false,
                    { now: performance.now() },
                );
            }
        },
        setSize(w, h) {
            renderer.setSize(w, h, false);
            camera.aspect = w / Math.max(1, h);
            camera.updateProjectionMatrix();
            pointsMaterial.uniforms.uPixelRatio.value = renderer.getPixelRatio();
            if (points && !rig.inCameraPose?.()) fitNow({ dur: 0 });
        },
        attachInput(el, options = {}) { return rigM.attachInput(el, rig, options); },
        async createApartmentBoundaryMomentum(options = {}) {
            const module = await sib('apartment-boundary-momentum.mjs');
            let state = module.createBoundaryMomentumState(options);
            return Object.freeze({
                dispatch(action) {
                    const result = module.reduceBoundaryMomentum(state, action);
                    state = result.state;
                    return result;
                },
                snapshot() { return state; },
                rigOvershoot(hostWidthPx) {
                    return module.boundaryRigOvershoot(state.progress, {
                        hostWidthPx,
                        maxOvershoot: rig.maxOvershoot?.() ?? 0.25,
                    });
                },
            });
        },
        async createSpatialViewportCoordinator(options) {
            const module = await sib('spatial-viewport-coordinator.js');
            return module.createSpatialViewportCoordinator(options);
        },
        async createWorldSurface(options = {}) {
            const module = await sib('world-surface.js');
            return module.createWorldSurface({
                ...options,
                performanceController,
            });
        },
        async createWorldEntryPrewarm(options) {
            const module = await sib('world-entry-prewarm.mjs');
            return module.createWorldEntryPrewarm(options);
        },
        async apartmentSemanticNavigation(model, states, currentDeviceId = null) {
            const module = await sib('apartment-semantic-navigation.mjs');
            const navigation = module.buildApartmentSemanticNavigation(model, states);
            return Object.freeze({
                ...navigation,
                statusText: module.apartmentDeviceStatusText(
                    navigation.devices,
                    currentDeviceId,
                ),
            });
        },
        /* home-apartment.jsx is a Babel-transformed classic script with no ESM
         * imports of its own, so this factory is the only route it has across
         * the module boundary — the same one createWorldSurface provides. It
         * also means the host cannot reach handoff-timeline.mjs directly, which
         * is why the compositor owns all timeline consumption. */
        /*
         * The world pose the Apartment camera is currently looking from.
         *
         * This is the commitment that orientation is PRESERVED, not chosen. The
         * world opens facing the way the Apartment was facing, derived live from
         * the camera's own basis vectors, so a user who rotated the scene before
         * zooming out arrives looking the same way. Without it the world arrives
         * at whatever the band preset says -- a near top-down parcel view that
         * has nothing to do with where the user was standing.
         *
         * The pivot is excluded on purpose. Two orthogonal hover rotations of
         * PIVOT_MAX compose to 0.81 degrees and induce up to 0.5 degrees of roll
         * at high elevation, which sits exactly on the continuity budget; the
         * pre-pivot basis makes roll exactly zero and the round trip exact.
         */
        async apartmentWorldPose({ localXAxisAzimuthDeg = 0, verticalFovDeg = null } = {}) {
            const basis = rig.cameraBasisApartmentLocal?.(apartmentRoot, { includePivot: false });
            if (!basis) return null;
            const registration = await sib('scene-registration.mjs');
            const pose = registration.apartmentCameraBasisToWorldPose({
                localXAxisAzimuthDeg,
                forwardLocal: basis.forwardLocal,
                upLocal: basis.upLocal,
                verticalFovDeg: Number.isFinite(verticalFovDeg) ? verticalFovDeg : basis.verticalFovDeg,
            });
            // The Apartment's own orbit radius travels with the pose so a caller
            // can decide whether to preserve it or open at the parcel range.
            return pose ? { ...pose, apartmentRangeM: basis.rangeM } : null;
        },
        /* home-apartment.jsx is a classic script with no ESM imports, so the
         * option presets can only reach it through here. */
        async worldViewOptions(selection) {
            const module = await sib('world-view-options.mjs');
            return {
                groups: module.OPTION_GROUPS,
                defaults: module.DEFAULT_SELECTION,
                resolved: module.resolveSelection(selection),
                key: module.selectionKey(selection),
            };
        },
        /*
         * Byte accounting for provider traffic, installed on demand.
         *
         * Its own module because the world surface is barred from touching the
         * network at all -- see provider-traffic.mjs for why the obvious answer,
         * the Resource Timing API, cannot supply this number.
         */
        async installProviderTrafficProbe(win) {
            const module = await sib('provider-traffic.mjs');
            return module.installProviderTrafficProbe(win ?? window);
        },
        async createHandoffCompositor(options) {
            const module = await sib('handoff-compositor.js');
            return module.createHandoffCompositor(options);
        },
        async handoffCompositorModule() {
            return sib('handoff-compositor.js');
        },
        setPerformanceMode(mode) {
            performanceController.setMode(mode, { now: performance.now() });
            applyPerformanceBudget({ force: true });
            return performanceController.getDiagnostics();
        },
        setPerformanceTarget(target) {
            performanceController.setTarget(target, { now: performance.now() });
            applyPerformanceBudget({ force: true });
            return performanceController.getDiagnostics();
        },
        performanceBudget() {
            return performanceController.getBudget();
        },
        performanceDiagnostics() {
            return performanceController.getDiagnostics();
        },
        refit(options = {}) { fitNow(options); },
        debugFit() { return projectedFitBounds(); },
        setDollhouseContext({ live = false, calibrating = false } = {}) {
            dollhouseContext = { live: !!live, calibrating: !!calibrating };
        },

        /* Fly to a camera device's pose. Calibrated extrinsics (P4) when
         * present; else the manual pose (device pos + yaw, slight downward
         * pitch) — honest 'not calibrated' approximation. */
        flyToDevice(device, { dur = 900, fovScale = 1, projectionFrame = null } = {}) {
            apartmentRoot.updateMatrixWorld(true);
            const ex = device.camera && device.camera.extrinsics;
            const intr = device.camera && device.camera.intrinsics;
            const calibratedCenter = Array.isArray(ex?.C) && ex.C.length >= 3;
            const pos = calibratedCenter
                ? new THREE.Vector3(+ex.C[0], +ex.C[1], +ex.C[2])
                : new THREE.Vector3(device.pos[0], device.pos[1], device.pos[2] ?? 1.6);
            const worldPos = apartmentRoot.localToWorld(pos.clone());
            let worldQuat, fov, projection = null;
            if (ex && ex.q_wxyz && calibratedCenter) {
                // q_wxyz is WORLD->CAMERA (OpenCV); a three camera needs
                // CAMERA->WORLD = conjugate. Then OpenCV cam axes (+Z fwd,
                // +Y down) -> three (-Z fwd, +Y up) via Rx(180) = diag(1,-1,-1).
                const q = new THREE.Quaternion(
                    ex.q_wxyz[1], ex.q_wxyz[2], ex.q_wxyz[3], ex.q_wxyz[0]).conjugate();
                const flip = new THREE.Quaternion().setFromRotationMatrix(
                    new THREE.Matrix4().makeScale(1, -1, -1));
                const rootQ = new THREE.Quaternion();
                apartmentRoot.getWorldQuaternion(rootQ);
                worldQuat = rootQ.multiply(q).multiply(flip);
                fov = fovFromIntrinsics(intr, fovScale, projectionFrame) || 70;
                projection = projectionFromIntrinsics(intr, fovScale, projectionFrame);
            } else {
                // manual pose: look along yaw (apartment frame), pitched down 18°
                const yaw = device.yaw_rad || 0;
                const lookLocal = new THREE.Vector3(
                    pos.x + Math.cos(yaw) * 2, pos.y + Math.sin(yaw) * 2, (pos.z ?? 1.6) - 0.65);
                const lookWorld = apartmentRoot.localToWorld(lookLocal);
                const m = new THREE.Matrix4().lookAt(worldPos, lookWorld, camera.up);
                worldQuat = new THREE.Quaternion().setFromRotationMatrix(m);
                fov = 70;
            }
            if (fovScale !== 1 && !projection) {
                // widen so the central (1/fovScale) band of the viewport spans
                // exactly the camera's true fov -> a centered live feed at that
                // fraction continues seamlessly into the surrounding mesh
                fov = THREE.MathUtils.radToDeg(
                    2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(fov) / 2) * fovScale));
            }
            rig.flyToPose({ position: worldPos, quaternion: worldQuat, fov, dur, projection });
        },
        dispose() {
            if (disposed) return false;
            engine.setRunning(false);
            disposed = true;
            for (const layer of uiLayers) retireUiLayer(layer);
            equipmentCutaway.dispose();
            unsubscribePerformanceBudget();
            signal?.removeEventListener?.('abort', onExternalAbort);
            modes.dispose();
            lifetimeController.abort();
            overlay.dispose?.();
            canvas.removeEventListener('webglcontextlost', onApartmentContextLost);
            canvas.removeEventListener('webglcontextrestored', onApartmentContextRestored);
            /* Terminal disposal is intentionally stronger than the ordinary
             * Apartment close, which only suspends this resident renderer.
             * Explicitly release Spark workers/buffers when present, then all
             * ordinary three geometry/material/texture resources. Sets keep
             * shared marker geometry and atlases from being disposed twice. */
            const custom = new Set();
            const geometries = new Set();
            const materials = new Set();
            const textures = new Set();
            materials.add(pointsMaterial);
            scene.traverse((node) => {
                if (node !== scene && typeof node.dispose === 'function') custom.add(node);
                if (node.geometry?.dispose) geometries.add(node.geometry);
                const list = Array.isArray(node.material) ? node.material : [node.material];
                for (const material of list) {
                    if (!material) continue;
                    materials.add(material);
                    for (const value of Object.values(material)) {
                        if (value?.isTexture && value.dispose) textures.add(value);
                    }
                }
            });
            const safelyDispose = (resource) => {
                try { resource.dispose(); } catch (_) { /* terminal cleanup */ }
            };
            for (const resource of custom) safelyDispose(resource);
            for (const texture of textures) safelyDispose(texture);
            for (const material of materials) safelyDispose(material);
            for (const geometry of geometries) safelyDispose(geometry);
            renderer.dispose();
            /* renderer.dispose() clears three's caches but deliberately keeps
             * the browser context. A terminal engine teardown must return that
             * scarce WebGL slot immediately rather than waiting for GC. */
            renderer.forceContextLoss?.();
            return true;
        },
    };
    const onExternalAbort = () => engine.dispose();
    signal?.addEventListener?.('abort', onExternalAbort, { once: true });
    if (signal?.aborted) engine.dispose();
    return engine;
}
