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

const easeInOutCubic = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

export function createModes({ apartmentRoot, pointsMaterial, sim, assetCandidates, fetchFrame, getPoints, scene, renderer, camera }) {
    const candidates = assetCandidates;
    const state = { mode: 'points', targetMode: 'points', modeSeq: 0,
                    fading: null, splat: null, mesh: null,
                    splatSource: null,
                    meshSource: null, meshFallback: false,
                    meshNearCut: { value: 0.0 } };
    const listeners = [];
    // In-flight load promises: a slow asset (mesh.glb is ~56 MB) must never be
    // fetched twice by two racing setMode calls — that would add a second,
    // unmanaged copy to the scene.
    let meshLoading = null;
    let splatLoading = null;

    // The EL cloud writes depth by design (self-occlusion). Once a fade ends
    // the points object must be HIDDEN in splat/mesh mode — uOpacity=0 alone
    // leaves an invisible depth wall that occludes the alpha-blended splat.
    function setPointsVisible(v) {
        const pts = getPoints && getPoints();
        if (pts) pts.visible = v;
    }

    async function applyRegistration(obj) {
        try {
            const frame = await fetchFrame();
            if (frame && frame.splat_baked) return;   // transform baked into the .spz
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
        } catch (e) {
            console.warn('[home-3d] frame.json missing — splat shown in scan frame', e);
        }
    }

    function loadSplat() {
        if (state.splat) return Promise.resolve(state.splat);
        if (splatLoading) return splatLoading;
        const p = _loadSplatImpl();
        splatLoading = p;
        p.finally(() => { if (splatLoading === p) splatLoading = null; });
        return p;
    }

    async function _loadSplatImpl() {
        const { SplatMesh, SparkRenderer } = await import('@sparkjsdev/spark');
        if (scene && renderer && !state.sparkRenderer) {
            const sr = new SparkRenderer({ renderer });
            sr.visible = false;
            sr.frustumCulled = false;
            sr.traverse((o) => { o.frustumCulled = false; });
            // Spark encodes splats relative to the SparkRenderer origin and its
            // docs have it FOLLOW THE CAMERA for precision — parent it there
            // (camera must be in the scene graph for its matrixWorld to flow).
            if (camera) {
                if (!camera.parent) scene.add(camera);
                camera.add(sr);
            } else {
                scene.add(sr);
            }
            state.sparkRenderer = sr;
        }
        let lastErr = null;
        const mobile = (() => {
            try { return (window.visualViewport?.width || window.innerWidth || 1024) < 700; }
            catch (e) { return false; }
        })();
        // Quality beats bandwidth here: the 120k mobile downsample looked like
        // a fuzzy blob on real phones. Prefer the full 466k splat, then fall
        // back to mobile only if the full runtime asset is absent.
        const names = mobile
            ? ['apartment.ply', 'apartment.spz', 'apartment.mobile.ply']
            : ['apartment.ply', 'apartment.spz']; // SplatTransform's spz flavor defeats Spark's decoder on some exports; ply always works
        const urls = names.flatMap((n) => candidates(n, null, { sim }));
        for (const url of urls) {
            try {
                const splat = new SplatMesh({ url });
                await Promise.race([
                    splat.initialized,
                    new Promise((_, reject) => setTimeout(
                        () => reject(new Error(`splat load timeout for ${url}`)),
                        mobile ? 45000 : 90000,
                    )),
                ]);
                await applyRegistration(splat);
                splat.renderOrder = 0;
                // Spark's objects have no geometry bounds — three.js frustum-
                // culls them, which silently skips onBeforeRender (Spark's
                // entire update/sort/draw path). Verified the hard way.
                splat.frustumCulled = false;
                apartmentRoot.add(splat);
                state.splat = splat;
                state.splatSource = url;
                visibleFor(state.mode);
                return splat;
            } catch (e) { lastErr = e; }
        }
        throw lastErr || new Error('no splat asset reachable');
    }

    function loadMesh() {
        if (state.mesh) return Promise.resolve(state.mesh);
        if (meshLoading) return meshLoading;
        const p = _loadMeshImpl();
        meshLoading = p;
        p.finally(() => { if (meshLoading === p) meshLoading = null; });
        return p;
    }

    function firstMaterial(material) {
        return Array.isArray(material) ? material.find(Boolean) : material;
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

    async function _loadMeshImpl() {
        const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js');
        let lastErr = null;
        // mesh.glb = the full textured Scaniverse export (1:1, 221k tris);
        // collision.glb is the decimated picking proxy — fallback display only.
        const mobile = (() => {
            try { return (window.visualViewport?.width || window.innerWidth || 1024) < 700; }
            catch (e) { return false; }
        })();
        const sources = mobile
            ? [
                { name: 'mesh.mobile.glb', textured: true, mobile: true },
                { name: 'mesh.glb', textured: true },
                { name: 'collision.glb', textured: false, fallback: true },
            ]
            : [
                { name: 'mesh.glb', textured: true },
                { name: 'collision.glb', textured: false, fallback: true },
            ];
        for (const src of sources) {
            for (const url of candidates(src.name, null, { sim })) {
                try {
                    const gltf = await new GLTFLoader().loadAsync(url);
                    const grp = gltf.scene;
                    let texturedMeshes = 0;
                    let fallbackMeshes = 0;
                    grp.traverse((o) => {
                        if (!o.isMesh) return;
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
                        if (mat.map || mat.vertexColors) texturedMeshes += 1;
                        else fallbackMeshes += 1;
                        mat.onBeforeCompile = (sh) => {
                            sh.uniforms.uMeshNearCut = state.meshNearCut;
                            sh.vertexShader = sh.vertexShader
                                .replace('#include <common>', '#include <common>\nvarying float vViewZ;')
                                .replace('#include <fog_vertex>', '#include <fog_vertex>\nvViewZ = -mvPosition.z;');
                            sh.fragmentShader = sh.fragmentShader
                                .replace('#include <common>', '#include <common>\nuniform float uMeshNearCut;\nvarying float vViewZ;')
                                .replace('#include <dithering_fragment>',
                                    '#include <dithering_fragment>\nif (uMeshNearCut > 0.0 && vViewZ < uMeshNearCut) discard;');
                        };
                        o.material = mat;
                    });
                    apartmentRoot.add(grp);
                    state.mesh = grp;
                    state.meshSource = src.name;
                    state.meshFallback = !!src.fallback || (src.textured && texturedMeshes === 0 && fallbackMeshes > 0);
                    visibleFor(state.mode);
                    return grp;
                } catch (e) { lastErr = e; }
            }
        }
        throw lastErr || new Error('no mesh asset reachable');
    }

    function visibleFor(mode) {
        const splatActive = mode === 'splat' || !!state.fading;
        if (state.splat) {
            state.splat.visible = splatActive;
            if (!splatActive && 'opacity' in state.splat) state.splat.opacity = 0;
        }
        if (state.sparkRenderer) state.sparkRenderer.visible = splatActive;
        if (state.mesh) state.mesh.visible = mode === 'mesh';
        setPointsVisible(mode === 'points' || !!state.fading);
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

        async setMode(next, { duration = 600 } = {}) {
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

            if (next === 'splat') await loadSplat();   // throws -> UI toast, stays put
            if (next === 'mesh') await loadMesh();

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

        /* explicit Spark tick — belt and suspenders over its autoUpdate */
        tickSpark(cam, time) {
            const splatActive = !!state.fading || state.mode === 'splat' || state.targetMode === 'splat';
            if (!state.sparkRenderer || !splatActive || (!state.splat && !state.fading)) return;
            try {
                state.sparkRenderer.update({ scene, camera: cam, time });
            } catch (e) { /* */ }
        },

        getMesh() { return state.mesh; },
        setMeshNearCut(v) { state.meshNearCut.value = v; },

        async preload(targets = ['splat', 'mesh']) {
            const list = Array.isArray(targets) ? targets : [targets];
            const jobs = [];
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
            const results = await Promise.all(jobs);
            return { ok: results.every((r) => r.ok), results };
        },

        debugInfo() {
            return {
                splat: !!state.splat,
                splatVisible: state.splat ? state.splat.visible : null,
                sparkVisible: state.sparkRenderer ? state.sparkRenderer.visible : null,
                splatSource: state.splatSource,
                numSplats: state.splat ? (state.splat.numSplats ?? null) : null,
                srParent: state.sparkRenderer ? (state.sparkRenderer.parent?.type || 'none') : 'none',
                meshSource: state.meshSource,
                meshFallback: state.meshFallback,
            };
        },

        update(dt) {
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
    return modes;
}
