/* home-3d/gaussian-splat-device-check.mjs — the on-device splat gate (webgpu-splat-check.html).
 *
 * Renders a Victoria-sized synthetic room (no household data) through the
 * WebGPU splat pipeline on a scripted orbit and reports what the gate needs:
 * frame time (median and p95 from requestAnimationFrame), GPU time where the
 * device exposes timestamps, sorts per frame, a sort-order check, and the
 * backend and adapter. Nothing is uploaded or saved; results stay on screen
 * and in window.__splatCheck.
 *
 * Query: n (default 370001), faint (fraction, default 0.17 at 370k),
 * sh (0-3), bits (16|32), backend (webgpu|webgl), seconds (default 20),
 * dpr (default min(devicePixelRatio, 1.5)), path (overview|inside),
 * ply (a same-origin URL of a real pack's apartment.ply, local use only).
 */
import * as THREE from 'three/webgpu';

const cb = new URL(import.meta.url).search;
const { HomeGaussianSplat } = await import(`./gaussian-splat-webgpu.mjs${cb}`);
const { parseSplatInWorker } = await import(`./gaussian-splat-loader.mjs${cb}`);
const { makeSplats, toPly, cameraAt } = await import(`./gaussian-splat-synthetic.mjs${cb}`);

const percentile = (values, p) => {
    if (!values.length) return null;
    const sorted = [...values].sort((a, b) => a - b);
    return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * p))];
};
const toBase64 = (bytes, length) => {
    const view = new Uint8Array(bytes.buffer, bytes.byteOffset, length);
    let text = '';
    for (let i = 0; i < view.length; i += 0x8000) text += String.fromCharCode(...view.subarray(i, i + 0x8000));
    return btoa(text);
};
const round = (value, digits = 2) => (value == null || !Number.isFinite(value) ? null : Number(value.toFixed(digits)));

export async function runSplatCheck({ canvas, params = new URLSearchParams(location.search), onProgress = () => {} } = {}) {
    const n = Math.max(1000, Math.min(4_000_000, Number(params.get('n')) || 370001));
    const faint = params.has('faint') ? Number(params.get('faint')) : (n >= 370000 ? 0.17 : 0);
    const sh = Math.max(0, Math.min(3, Number(params.get('sh') ?? 3)));
    const keyBits = params.get('bits') === '32' ? 32 : 16;
    const forceWebGL = params.get('backend') === 'webgl';
    const seconds = Math.max(3, Math.min(120, Number(params.get('seconds')) || 20));
    const dpr = Number(params.get('dpr')) || Math.min(window.devicePixelRatio || 1, 1.5);
    const path = params.get('path') === 'inside' ? 'inside' : 'overview';
    const result = { status: 'running', userAgent: navigator.userAgent, webgpuAvailable: !!navigator.gpu,
        secureContext: window.isSecureContext === true,
        splats: n, faint, sh, keyBits, path, dpr, seconds };
    window.__splatCheck = result;

    onProgress('Starting the renderer…');
    const renderer = new THREE.WebGPURenderer({ canvas, antialias: true, alpha: false, forceWebGL,
        powerPreference: 'high-performance', trackTimestamp: true });
    renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
    renderer.toneMapping = THREE.NoToneMapping;
    await renderer.init();
    result.backend = renderer.backend.isWebGPUBackend ? 'webgpu' : 'webgl2';
    result.adapter = renderer.backend.device?.adapterInfo
        ? Object.fromEntries(['vendor', 'architecture', 'description'].map((key) => [key, renderer.backend.device.adapterInfo[key] || '']))
        : null;
    renderer.setPixelRatio(dpr);
    const resize = () => renderer.setSize(canvas.clientWidth, canvas.clientHeight, false);
    resize();
    window.addEventListener('resize', resize);
    result.drawingBuffer = renderer.getDrawingBufferSize(new THREE.Vector2()).toArray();

    onProgress('Building the splats…');
    let bytes;
    const ply = params.get('ply');
    if (ply) {
        const url = new URL(ply, location.href);
        if (url.origin !== location.origin) throw new Error('ply must be same-origin');
        bytes = await (await fetch(url)).arrayBuffer();
        result.source = 'pack-ply';
    } else {
        const synthetic = makeSplats(n, { faintFraction: faint });
        bytes = toPly(synthetic).buffer;
        result.source = 'synthetic';
    }
    const started = performance.now();
    const data = await parseSplatInWorker(bytes);
    result.parseMs = round(performance.now() - started, 0);
    result.splats = data.count;
    result.fileShDegree = data.shDegree;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x111111);
    const camera = new THREE.PerspectiveCamera(50, 1, 0.05, 200);
    const splat = new HomeGaussianSplat(data, { renderer, keyBits, maxShDegree: sh });
    window.__splatDebug = { splat, renderer, scene, camera, THREE };
    if (result.source === 'pack-ply') splat.rotation.x = -Math.PI / 2;
    scene.add(splat);

    onProgress('Warming up…');
    const place = (t) => {
        let eye, target;
        if (result.source === 'pack-ply') {
            // Packs are Z-up; the splat is turned Y-up above. Orbit its bounds.
            const [x, y, z] = data.bounds.center, r = Math.min(12, data.bounds.radius), a = t * Math.PI * 2;
            target = [x, z, -y];
            eye = path === 'inside' ? [x + Math.cos(a) * 0.5, z + 0.4, -y + Math.sin(a) * 0.5]
                : [x + Math.cos(a) * r * 0.9, z + r * 0.8, -y + Math.sin(a) * r * 0.9];
            if (path === 'inside') target = [x + Math.cos(a + 1.6) * 3, z, -y + Math.sin(a + 1.6) * 3];
        } else ({ eye, target } = cameraAt(path, t));
        camera.position.fromArray(eye);
        camera.lookAt(...target);
        camera.aspect = canvas.clientWidth / Math.max(1, canvas.clientHeight);
        camera.updateProjectionMatrix();
    };
    const frame = (t) => { place(t); splat.update(renderer, camera); renderer.render(scene, camera); };
    for (let i = 0; i < 30; i++) { frame(i / 300); await new Promise(requestAnimationFrame); }

    onProgress('Orbiting…');
    const frameMs = [], gpuCompute = [], gpuRender = [], checks = [];
    const sortsBefore = splat.pipelineReadback().counts;
    const timed = result.backend === 'webgpu' && renderer.hasFeature?.('timestamp-query');
    let last = performance.now(), frames = 0;
    const startedAt = last;
    await new Promise((done) => {
        const tick = async (now) => {
            frameMs.push(now - last); last = now; frames++;
            frame(((now - startedAt) / 8000) % 1);
            if (timed && frames % 10 === 0) {
                Promise.all([renderer.resolveTimestampsAsync(THREE.TimestampQuery.COMPUTE),
                    renderer.resolveTimestampsAsync(THREE.TimestampQuery.RENDER)]).then(([c, r]) => {
                    if (Number.isFinite(c)) gpuCompute.push(c);
                    if (Number.isFinite(r)) gpuRender.push(r);
                });
            }
            if (frames % 120 === 60 && result.backend === 'webgpu') splat.verifySortAsync(renderer).then((check) => check && checks.push(check));
            if (now - startedAt < seconds * 1000) requestAnimationFrame(tick); else done();
        };
        requestAnimationFrame(tick);
    });
    const counts = splat.pipelineReadback().counts;
    const steady = frameMs.slice(5);
    const p95 = percentile(steady, 0.95);
    Object.assign(result, {
        status: 'done',
        frames,
        fps: round(steady.length / (steady.reduce((a, b) => a + b, 0) / 1000), 1),
        frameMedianMs: round(percentile(steady, 0.5)),
        frameP95Ms: round(p95),
        gpuComputeMedianMs: round(percentile(gpuCompute, 0.5), 3),
        gpuRenderMedianMs: round(percentile(gpuRender, 0.5), 3),
        gpuTotalMedianMs: gpuCompute.length && gpuRender.length
            ? round(percentile(gpuCompute, 0.5) + percentile(gpuRender, 0.5), 3) : null,
        sortsPerFrame: round((counts.sorts + counts.cpuSorts - sortsBefore.sorts - sortsBefore.cpuSorts) / frames, 3),
        sortChecks: checks,
        sortViolations: checks.reduce((sum, check) => sum + check.violations, 0),
        pipeline: splat.pipelineReadback(),
    });
    result.verdict = {
        sixtyFps: p95 != null && p95 < 1000 / 60 + 0.5,
        sortedEveryFrame: result.backend === 'webgpu' ? result.sortsPerFrame >= 0.99 : null,
        sortOrder: result.backend === 'webgpu' ? checks.length > 0 && result.sortViolations === 0 : null,
    };
    /* Coverage of one frame read back on the GPU (no image leaves the page). */
    result.snapshot = async (t = 0.123, { width = 256, height = 144, pixels: wantPixels = false } = {}) => {
        const target = new THREE.RenderTarget(width, height);
        place(t); splat.update(renderer, camera);
        const aspect = camera.aspect; camera.aspect = width / height; camera.updateProjectionMatrix();
        renderer.setRenderTarget(target); renderer.render(scene, camera); renderer.setRenderTarget(null);
        camera.aspect = aspect; camera.updateProjectionMatrix();
        const pixels = await renderer.readRenderTargetPixelsAsync(target, 0, 0, width, height);
        target.dispose();
        let lit = 0, sum = 0;
        for (let i = 0; i < width * height; i++) {
            const v = Math.max(pixels[i * 4], pixels[i * 4 + 1], pixels[i * 4 + 2]);
            if (v > 24) lit++; sum += v;
        }
        const draw = splat._sort ? Array.from(new Uint32Array(await renderer.getArrayBufferAsync(splat._sort.drawAttribute))) : null;
        return { litFraction: lit / (width * height), meanMax: sum / (width * height), draw, calls: renderer.info.render.drawCalls,
            ...(wantPixels ? { width, height, pixels: toBase64(pixels, width * height * 4) } : {}) };
    };
    // Keep the room on screen after measuring.
    const idle = (now) => { if (!result.stopped) { frame(((now - startedAt) / 8000) % 1); requestAnimationFrame(idle); } };
    requestAnimationFrame(idle);
    onProgress('Done');
    return result;
}
