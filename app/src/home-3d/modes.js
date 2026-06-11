/* home-3d/modes.js — render-mode state machine: points | splat | mesh (| live, P4).
 *
 * P0 ships `points` for real; `splat` and `mesh` are wired as lazy loaders that
 * fail gracefully (toast in the UI) until P3 lands their assets. Crossfade:
 * points uOpacity <-> SplatMesh opacity over 600 ms (fallback: fade-through-black
 * handled UI-side).
 */
import * as THREE from 'three';

const easeInOutCubic = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

export function createModes({ apartmentRoot, pointsMaterial, sim, assetCandidates }) {
    const candidates = assetCandidates;
    const state = { mode: 'points', fading: null, splat: null, mesh: null };
    const listeners = [];

    async function loadSplat() {
        if (state.splat) return state.splat;
        const { SplatMesh } = await import('@sparkjsdev/spark');
        let lastErr = null;
        for (const url of candidates('apartment.spz', null, { sim })) {
            try {
                const splat = new SplatMesh({ url });
                await splat.initialized;
                splat.renderOrder = 0;
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
        for (const url of candidates('mesh.glb', null, { sim })) {
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
    }

    const modes = {
        get mode() { return state.mode; },
        onChange(cb) { listeners.push(cb); },

        async setMode(next, { duration = 600 } = {}) {
            if (next === state.mode) return;
            const prev = state.mode;
            if (next === 'splat') await loadSplat();   // throws -> UI toast, stays put
            if (next === 'mesh') await loadMesh();

            if ((prev === 'points' && next === 'splat') || (prev === 'splat' && next === 'points')) {
                state.fading = { t: 0, duration, dir: next === 'splat' ? 1 : -1 };
                visibleFor(next);
            } else {
                pointsMaterial.uniforms.uOpacity.value = next === 'points' ? 1 : (next === 'mesh' ? 0 : pointsMaterial.uniforms.uOpacity.value);
                visibleFor(next);
            }
            state.mode = next;
            listeners.forEach((cb) => cb(next, prev));
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
