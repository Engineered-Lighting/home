/* home-3d/webgpu-renderer.mjs — the WebGPU renderer foundation (preview).
 *
 * Loaded by engine.js only when index.html mapped 'three' to the vendored
 * r186 WebGPU build, i.e. the page was opened with ?renderer=webgpu (WebGPU,
 * falling back to WebGL2 by itself where there is no adapter) or
 * ?renderer=webgpu-webgl (the same node renderer on its WebGL2 backend, which
 * is what CI and Playwright run). Without the flag nothing here loads and the
 * engine keeps its WebGLRenderer path unchanged.
 *
 * What the engine gets back is a thin seam over three's WebGPURenderer:
 *
 *   renderer         a WebGPURenderer, initialised; render() is synchronous
 *                    after init, so the engine's rAF loop is unchanged;
 *   backend          'webgpu' or 'webgl2' (the fallback);
 *   gpu              adapter identity: GPUAdapterInfo on WebGPU, the unmasked
 *                    GL renderer on the fallback;
 *   isLost()         device.lost (or the fallback's context loss) happened;
 *   adoptPoints()    draw the point cloud as instanced quads;
 *   capture()        the captureFrame readback, by render-target copy.
 *
 * Output stays in display space like the WebGL canvas: no tone mapping, no
 * output pass, every material encodes its own sRGB output
 * (webgpu-material-ports.mjs). Rendering through three's default HalfFloat
 * output pass instead would blend the translucent cloud in linear light and
 * visibly change it.
 *
 * Photo: Spark is WebGL-only. On this path Photo is Home's own splat
 * renderer (gaussian-splat-webgpu.mjs) through gaussian-splat-modes.mjs.
 */
import * as THREE from 'three';
import { texture, uv } from 'three/tsl';

const cb = new URL(import.meta.url).search;
const { installMaterialPorts, displayOutputContext, createPointSpriteMaterial, PORT_FINGERPRINTS,
    shaderMaterialFingerprint } = await import(`./webgpu-material-ports.mjs${cb}`);
const { createSplatModeAdapters } = await import(`./gaussian-splat-modes.mjs${cb}`);

/* Kept for callers of the foundation preview; Photo no longer refuses. */
export const PHOTO_UNAVAILABLE = 'photo_unavailable_on_webgpu_preview';

/*
 * The engine and the equipment visuals read WebGLRenderer's info semantics,
 * and engine.js's frame loop stays identical on both paths, so the node
 * renderer is shaped to match rather than the callers taught a second shape:
 *
 *   info.render.frame  advances on every render() call. The equipment depth
 *                      capture keys its once-per-frame cache on it; r186 has
 *                      no such field, and info.calls counts the same thing.
 *   info.render.calls  the last render()'s draw calls, as WebGL's autoReset
 *                      reports them (r186 counts render() calls there and
 *                      draw calls in drawCalls). three's own increment of
 *                      the field is absorbed by the setter.
 *   autoReset          per render() call, as WebGLRenderer does.
 */
function matchWebGLInfo(renderer) {
    const info = renderer.info;
    Object.defineProperty(info.render, 'frame', { get: () => info.calls, configurable: true });
    Object.defineProperty(info.render, 'calls', { get: () => info.render.drawCalls, set: () => {}, configurable: true });
    const render = renderer.render;
    renderer.render = function renderWithReset(...args) {
        if (info.autoReset) info.reset();
        return render.apply(this, args);
    };
}

async function gpuIdentity(renderer, powerPreference) {
    const backend = renderer.backend;
    if (backend.isWebGPUBackend) {
        let info = backend.device?.adapterInfo || null;
        if (!info) {
            try { info = (await navigator.gpu.requestAdapter({ powerPreference }))?.info || null; }
            catch (_) { info = null; }
        }
        const description = info && (info.description
            || [info.vendor, info.architecture, info.device].filter(Boolean).join(' '));
        return { name: `WebGPU · ${description || 'unknown adapter'}`,
            adapter: info ? { vendor: info.vendor || '', architecture: info.architecture || '',
                device: info.device || '', description: info.description || '' } : null };
    }
    let name = 'unknown';
    try {
        const gl = backend.gl;
        const ext = gl.getExtension('WEBGL_debug_renderer_info');
        if (ext) name = gl.getParameter(ext.UNMASKED_RENDERER_WEBGL);
    } catch (_) { /* */ }
    return { name: `WebGL2 (node) · ${name}`, adapter: null };
}

/* WebGPU buffer rows are padded to 256 bytes. */
function unpadRows(data, width, height) {
    const row = width * 4;
    if (data.length === row * height) return new Uint8Array(data.buffer, data.byteOffset, data.length);
    const padded = Math.ceil(row / 256) * 256;
    const pixels = new Uint8Array(row * height);
    for (let y = 0; y < height; y++) pixels.set(data.subarray(y * padded, y * padded + row), y * row);
    return pixels;
}

export async function createNodeRenderer({ canvas, forceWebGL = false, antialias = true, alpha = true,
    powerPreference = 'high-performance', onLost = null } = {}) {
    const renderer = new THREE.WebGPURenderer({ canvas, antialias, alpha, powerPreference, forceWebGL });
    renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
    renderer.toneMapping = THREE.NoToneMapping;
    renderer.contextNode = displayOutputContext();
    let lost = null;
    renderer.onDeviceLost = (info) => {
        lost = { api: info?.api || null, reason: info?.reason || null, message: info?.message || 'device lost' };
        console.warn(`[home-3d] ${lost.api || 'GPU'} device LOST (${lost.reason || 'unknown'}): ${lost.message}`);
        try { onLost?.(lost); } catch (_) { /* the engine's emitter never throws back into three */ }
    };
    await renderer.init();
    const backend = renderer.backend.isWebGPUBackend ? 'webgpu' : 'webgl2';
    matchWebGLInfo(renderer);
    const ports = installMaterialPorts(renderer);
    const identity = await gpuIdentity(renderer, powerPreference);
    const drawingBuffer = new THREE.Vector2();
    let captureFull = null, captureSmall = null, captureQuad = null;
    const sprites = new Set();

    function captureTargets(width, height, targetWidth, targetHeight) {
        if (!captureFull) {
            captureFull = new THREE.RenderTarget(width, height, { samples: renderer.samples, depthBuffer: true,
                type: THREE.UnsignedByteType, generateMipmaps: false });
            captureSmall = new THREE.RenderTarget(targetWidth, targetHeight, { depthBuffer: false,
                type: THREE.UnsignedByteType, generateMipmaps: false });
            const material = new THREE.NodeMaterial();
            // Raw copy: the full target already holds display-encoded values.
            material.fragmentNode = texture(captureFull.texture, uv());
            captureQuad = new THREE.QuadMesh(material);
        }
        if (captureFull.width !== width || captureFull.height !== height) captureFull.setSize(width, height);
        if (captureSmall.width !== targetWidth || captureSmall.height !== targetHeight) captureSmall.setSize(targetWidth, targetHeight);
    }

    const seam = {
        renderer,
        backend,
        /* modes.js load adapters: Photo through Home's splat renderer, not Spark. */
        modeAdapters: createSplatModeAdapters({ renderer }),
        gpu: identity.name,
        adapter: identity.adapter,
        coordinateSystem: renderer.coordinateSystem,
        isLost: () => lost !== null,
        lostInfo: () => lost,
        ports,

        /**
         * The Points object stays the cloud's owner: geometry, bounds,
         * visibility and the GL ShaderMaterial (whose uniforms the engine
         * drives) are unchanged. It stops drawing itself (no layer) and a
         * camera-facing sprite child draws every loaded point instead.
         */
        adoptPoints(points) {
            const glMaterial = points.material;
            if (shaderMaterialFingerprint(glMaterial) !== PORT_FINGERPRINTS.pointcloud) {
                console.warn('[home-3d] WebGPU: the point cloud GLSL changed since its TSL port; drawn from the port anyway');
            }
            const position = points.geometry.getAttribute('position');
            const intensity = points.geometry.getAttribute('intensity');
            const count = Math.min(position.count, points.geometry.drawRange.count);
            const intensityValues = new Float32Array(count);
            for (let i = 0; i < count; i++) intensityValues[i] = intensity.getX(i);
            const material = createPointSpriteMaterial(glMaterial, {
                positions: new THREE.InstancedBufferAttribute(position.array, 3),
                intensity: new THREE.InstancedBufferAttribute(intensityValues, 1),
            });
            const sprite = new THREE.Sprite(material);
            sprite.name = 'apartment-points-webgpu';
            sprite.count = count;
            sprite.frustumCulled = false;
            sprite.raycast = () => {};
            sprite.userData.presentation_only = true;
            points.layers.disableAll();
            points.add(sprite);
            sprites.add(sprite);
            return sprite;
        },

        /**
         * captureFrame on this backend: render once into a multisampled
         * target the size of the drawing buffer, scale it on the GPU with one
         * bilinear tap (as the WebGL path's blit does), then copy only the
         * small target to a mapped buffer. The canvas is untouched.
         */
        async capture({ scene, camera, scale = 0.25, signal } = {}) {
            if (lost || signal?.aborted) return null;
            renderer.getDrawingBufferSize(drawingBuffer);
            const width = Math.max(1, Math.round(drawingBuffer.x));
            const height = Math.max(1, Math.round(drawingBuffer.y));
            const factor = Math.min(1, Math.max(0.01, scale));
            const targetWidth = Math.max(1, Math.round(width * factor));
            const targetHeight = Math.max(1, Math.round(height * factor));
            captureTargets(width, height, targetWidth, targetHeight);
            const previous = renderer.getRenderTarget();
            try {
                renderer.setRenderTarget(captureFull);
                renderer.render(scene, camera);
                renderer.setRenderTarget(captureSmall);
                captureQuad.render(renderer);
            } finally {
                renderer.setRenderTarget(previous);
            }
            const data = await renderer.readRenderTargetPixelsAsync(captureSmall, 0, 0, targetWidth, targetHeight);
            if (lost || signal?.aborted || !data) return null;
            // WebGPU textures start at the top row; the WebGL2 backend reads
            // its target bottom-up like the WebGL path.
            return { width: targetWidth, height: targetHeight, bottomUp: backend !== 'webgpu',
                pixels: unpadRows(data, targetWidth, targetHeight) };
        },

        dispose() {
            for (const sprite of sprites) { sprite.removeFromParent(); sprite.material.dispose(); }
            sprites.clear();
            captureFull?.dispose(); captureSmall?.dispose(); captureQuad?.material.dispose();
            ports.dispose();
        },
    };
    // engine.js's terminal dispose() calls renderer.dispose() on both paths.
    const disposeRenderer = renderer.dispose;
    renderer.dispose = function disposeWithSeam(...args) {
        seam.dispose();
        return disposeRenderer.apply(this, args);
    };
    return seam;
}
