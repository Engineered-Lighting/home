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

export async function createEngine({ canvas, hostEl, sim = false }) {
    const [pointcloudM, rigM, modesM, markersM, pickingM, assetsM] = await Promise.all([
        sib('pointcloud.js'), sib('rig.js'), sib('modes.js'),
        sib('markers.js'), sib('picking.js'), sib('assets.js'),
    ]);

    const renderer = new THREE.WebGLRenderer({
        canvas, antialias: true, alpha: false, powerPreference: 'high-performance',
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0x000000, 1);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x000000);

    const camera = new THREE.PerspectiveCamera(35, 1, 0.05, 200);

    // THE coordinate conversion: apartment frame is Z-up, three is Y-up.
    const apartmentRoot = new THREE.Group();
    apartmentRoot.rotation.x = -Math.PI / 2;
    scene.add(apartmentRoot);
    apartmentRoot.updateMatrixWorld(true);

    const pointsMaterial = pointcloudM.createPointsMaterial();
    const rig = rigM.createRig(camera);
    const modes = modesM.createModes({
        apartmentRoot, pointsMaterial, sim, scene, renderer,
        assetCandidates: assetsM.candidates,
        fetchFrame: () => assetsM.fetchFrame({ sim }),
        getPoints: () => points,
    });
    const overlay = markersM.createOverlay(apartmentRoot);
    const picking = pickingM.createPicking(camera, hostEl);

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

    // ---- point cloud (progressive) ----
    let points = null;
    let pointTotal = 0;
    let fitted = false;

    function fitNow() {
        if (!points) return;
        points.geometry.computeBoundingBox();
        const box = points.geometry.boundingBox.clone();
        box.applyMatrix4(apartmentRoot.matrixWorld);
        rig.fitBounds(box);
    }

    async function loadPoints() {
        let lastErr = null;
        for (const url of assetsM.pointsCandidates({ sim })) {
            try {
                const result = await pointcloudM.loadPointCloud(url, pointsMaterial, {
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
            } catch (e) { lastErr = e; console.warn(`[home-3d] points failed: ${url}`, e); }
        }
        throw lastErr || new Error('no point asset reachable');
    }

    const pointsPromise = (async () => {
        const result = await loadPoints();
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
    let statsAt = 0;
    let badStreak = 0;

    function frame(now) {
        if (!running) return;
        rafId = requestAnimationFrame(frame);
        const dt = Math.min(0.1, (now - last) / 1000);
        last = now;

        pointsMaterial.uniforms.uTime.value = now / 1000;
        rig.update(dt);
        // the EL depth-brightness window tracks the orbit radius so the
        // falloff aesthetic holds at every zoom detent (static 6/30 left the
        // overview pose entirely past the far fade — verified visually)
        const orbitR = rig.currentRadius();
        pointsMaterial.uniforms.uFadeNear.value = orbitR * 0.30;
        pointsMaterial.uniforms.uFadeFar.value = orbitR * 1.55;
        modes.update(dt);
        overlay.update(dt);
        renderer.render(scene, camera);

        const ft = performance.now() - now;
        frameTimes.push(ft);
        if (frameTimes.length > 120) frameTimes.shift();

        if (now - statsAt > 500) {
            statsAt = now;
            const sorted = [...frameTimes].sort((a, b) => a - b);
            const p95 = sorted[Math.floor(sorted.length * 0.95)] || 0;
            const fps = 1000 / Math.max(0.5, sorted[Math.floor(sorted.length * 0.5)] || 16.7);
            emit('stats', { fps, p95, gpu: gpuName, points: pointTotal, pixelRatio: renderer.getPixelRatio() });
            // degradation ladder: pixelRatio 2 -> 1.5 -> 1.25 -> 1
            if (p95 > 18 && frameTimes.length >= 60) {
                badStreak++;
                if (badStreak >= 4) {
                    badStreak = 0;
                    const ladder = [2, 1.5, 1.25, 1];
                    const cur = renderer.getPixelRatio();
                    const next = ladder.find((v) => v < cur - 0.01);
                    if (next) {
                        renderer.setPixelRatio(next);
                        pointsMaterial.uniforms.uPixelRatio.value = next;
                        console.log(`[home-3d] degrading pixelRatio -> ${next} (p95 ${p95.toFixed(1)}ms)`);
                    }
                }
            } else badStreak = 0;
        }
    }

    const engine = {
        scene, camera, renderer, rig, modes, overlay, picking, apartmentRoot,
        pointsMaterial, pointsPromise,
        gpu: gpuName,
        on(type, cb_) { emitter.addEventListener(type, (e) => cb_(e.detail)); },
        setRunning(v) {
            if (v === running) return;
            running = v;
            if (v) { last = performance.now(); rafId = requestAnimationFrame(frame); }
            else cancelAnimationFrame(rafId);
        },
        setSize(w, h) {
            renderer.setSize(w, h, false);
            camera.aspect = w / Math.max(1, h);
            camera.updateProjectionMatrix();
            pointsMaterial.uniforms.uPixelRatio.value = renderer.getPixelRatio();
        },
        attachInput(el) { return rigM.attachInput(el, rig); },
        refit() { fitNow(); },
        dispose() {
            engine.setRunning(false);
            renderer.dispose();
        },
    };
    return engine;
}
