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
    const state = { mode: 'points', fading: null, splat: null, mesh: null };
    const listeners = [];

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

    async function loadSplat() {
        if (state.splat) return state.splat;
        const { SplatMesh, SparkRenderer } = await import('@sparkjsdev/spark');
        if (scene && renderer && !state.sparkRenderer) {
            const sr = new SparkRenderer({ renderer });
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
        const names = ['apartment.spz', 'apartment.ply']; // SplatTransform's spz flavor defeats Spark's decoder; ply always works
        const urls = names.flatMap((n) => candidates(n, null, { sim }));
        for (const url of urls) {
            try {
                const splat = new SplatMesh({ url });
                await splat.initialized;
                await applyRegistration(splat);
                splat.renderOrder = 0;
                // Spark's objects have no geometry bounds — three.js frustum-
                // culls them, which silently skips onBeforeRender (Spark's
                // entire update/sort/draw path). Verified the hard way.
                splat.frustumCulled = false;
                apartmentRoot.add(splat);
                state.splat = splat;
                return splat;
            } catch (e) { lastErr = e; }
        }
        throw lastErr || new Error('no splat asset reachable');
    }

    async function loadMesh() {
        if (state.mesh) return state.mesh;
        const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js');
        let lastErr = null;
        for (const url of candidates('collision.glb', null, { sim })) {
            try {
                const gltf = await new GLTFLoader().loadAsync(url);
                const grp = gltf.scene;
                grp.traverse((o) => {
                    if (o.isMesh) o.material = new THREE.MeshNormalMaterial({ flatShading: true });
                });
                apartmentRoot.add(grp);
                state.mesh = grp;
                return grp;
            } catch (e) { lastErr = e; }
        }
        throw lastErr || new Error('no mesh asset reachable');
    }

    function visibleFor(mode) {
        if (state.splat) state.splat.visible = mode === 'splat' || !!state.fading;
        if (state.mesh) state.mesh.visible = mode === 'mesh';
        setPointsVisible(mode === 'points' || !!state.fading);
    }

    const modes = {
        get mode() { return state.mode; },
        onChange(cb) { listeners.push(cb); },

        async setMode(next, { duration = 600 } = {}) {
            if (next === state.mode) return;
            const prev = state.mode;
            if (next === 'splat') await loadSplat();   // throws -> UI toast, stays put
            if (next === 'mesh') await loadMesh();

            if (duration > 0 &&
                ((prev === 'points' && next === 'splat') || (prev === 'splat' && next === 'points'))) {
                state.fading = { t: 0, duration, dir: next === 'splat' ? 1 : -1 };
                visibleFor(next);
            } else {
                if (state.splat && 'opacity' in state.splat) state.splat.opacity = next === 'splat' ? 1 : 0;
                pointsMaterial.uniforms.uOpacity.value = next === 'points' ? 1 : 0;
                visibleFor(next);
            }
            state.mode = next;
            listeners.forEach((cb) => cb(next, prev));
        },

        /* explicit Spark tick — belt and suspenders over its autoUpdate */
        tickSpark(cam, time) {
            if (!state.sparkRenderer || (!state.splat && !state.fading)) return;
            try {
                state.sparkRenderer.update({ scene, camera: cam, time });
            } catch (e) { /* */ }
        },

        debugInfo() {
            return {
                splat: !!state.splat,
                splatVisible: state.splat ? state.splat.visible : null,
                numSplats: state.splat ? (state.splat.numSplats ?? null) : null,
                srParent: state.sparkRenderer ? (state.sparkRenderer.parent?.type || 'none') : 'none',
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
