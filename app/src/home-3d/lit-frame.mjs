/* home-3d/lit-frame.mjs — Lighting's one HDR frame on both renderers ("Night
 * relighting realism" research, shortlist #1 and #3).
 *
 * While Lighting is on, the engine's own renderer.render(scene, camera) is
 * wrapped (engine.js frame() is untouched) and draws one Lit frame:
 *
 *   1 scene-referred   whatever is lit (the relit splats or points, or the
 *                      relit mesh) on LIT_FRAME_LAYER into a half-float target
 *                      cleared to transparent black: pre-exposed linear HDR,
 *                      premultiplied. In Photo and Cloud the scan mesh then
 *                      writes depth only (the dollhouse cut kept), so the
 *                      fixture faces are hidden behind walls.
 *   2 meter            (Mesh) a coarse copy, read back asynchronously.
 *   3 emitters         each lit fixture's face at its assumed luminance,
 *                      clamped for half float and tagged (lit-display.mjs).
 *   4 bloom            the COD: AW chain: six mips from half resolution.
 *   5 output           into the target the engine asked for (the canvas or
 *                      the capture target): the display pass as the first,
 *                      full-screen draw, then everything that is not lit
 *                      (orbs, beams, markers) over it, never tone mapped. The
 *                      lit receivers are hidden for it; the lit mesh draws
 *                      depth only, so overlays stay occluded by the room.
 *
 * WebGL (r180) uses ShaderMaterials from lit-display.mjs's GLSL; the WebGPU
 * path (r186) node materials from its TSL. Presentation only: bloom, the
 * camera look, noise and the fixture faces never enter lux, the meter or the
 * linear readback, which read the scene-referred pass before any of them.
 */
import * as THREE from 'three';

const search = new URL(import.meta.url).search;
const Display = await import(`./lit-display.mjs${search}`);
const Model = await import(`./lit-model.mjs${search}`);
const Exposure = await import(`./lit-exposure.mjs${search}`);

// Lit content (shared with lit-view.mjs), the fixture faces, the depth-only scan.
export const LIT_FRAME_LAYER = 29, LIT_EMITTER_LAYER = 28, LIT_OCCLUDER_LAYER = 27;
const IDENTITY = Object.freeze([[1, 0, 0], [0, 1, 0], [0, 0, 1]]);

/* `T` is the 'three/tsl' namespace on the WebGPU path (lit-view.mjs loads it
 * with the r186 build), null on WebGL. */
export function createLitFrame({ renderer, scene, T = null, now = () => performance.now() } = {}) {
    if (!renderer || !scene?.isScene) throw new TypeError('lit_frame_dependencies_required');
    const node = !!renderer.isWebGPURenderer;
    if (node && !T) throw new TypeError('lit_frame_tsl_required');
    const group = new THREE.Group();
    group.name = 'lit-frame';
    group.userData = { presentation_only: true, photometry: 'assumed_not_measured' };
    const params = { look: 'camera', operator: 'agx', gain: 1, whiteBalance: IDENTITY, agxPower: Exposure.LIT_CAMERA_LOOK.agxPower,
        preExposure: Model.LIT_EXPOSURE, bloom: true, noise: true, seed: 1, msaa: false };
    let inner = null, wrapper = null, installation = null, active = false, lastCamera = null, frames = 0, lastPassMs = null;
    let receivers = [], receiverRoots = [], meshEntries = [], occluders = () => [], beforeFrame = null, lights = [], lumens = Model.LIT_ASSUMED_LUMENS;
    let meterTarget = null, meterPending = false, meterRequestedAt = -Infinity, meterToken = 0, meterWanted = false, metered = null, meterSerial = 0;
    let readTarget = null, clamped = 0;
    const size = new THREE.Vector2(), clearColor = new THREE.Color();

    // ---- Materials: GLSL (WebGL) or nodes (WebGPU) ------------------------------
    const uniforms = {
        strength: node ? T.uniform(Display.LIT_BLOOM.strength) : { value: Display.LIT_BLOOM.strength },
        gain: node ? T.uniform(1) : { value: 1 },
        preExposure: node ? T.uniform(Model.LIT_EXPOSURE) : { value: Model.LIT_EXPOSURE },
        agxPower: node ? T.uniform(params.agxPower) : { value: params.agxPower },
        whiteBalance: node ? [0, 1, 2].map(r => T.uniform(new THREE.Vector3(...IDENTITY[r]))) : { value: new THREE.Matrix3() },
        noise: node ? T.uniform(new THREE.Vector4()) : { value: new THREE.Vector4() },
    };
    function pass(name, { fragment, glslUniforms = {}, nodeFragment }) {
        let material;
        if (node) {
            material = new THREE.NodeMaterial();
            material.fragmentNode = nodeFragment();
            material.vertexNode = T.vec4(T.positionGeometry.xy, 0, 1);
        } else material = new THREE.ShaderMaterial({ uniforms: glslUniforms, vertexShader: Display.LIT_PASS_VERTEX_GLSL, fragmentShader: fragment });
        material.name = name;
        material.depthTest = false; material.depthWrite = false; material.toneMapped = false;
        material.blending = THREE.NoBlending;
        return material;
    }
    const quad = node ? new THREE.QuadMesh() : new THREE.Mesh(new THREE.PlaneGeometry(2, 2));
    quad.frustumCulled = false;
    const quadScene = node ? null : new THREE.Scene(), quadCamera = node ? null : new THREE.Camera();
    if (!node) quadScene.add(quad);
    function draw(target, material, clear) {
        const autoClear = renderer.autoClear;
        try {
            renderer.setRenderTarget(target); renderer.autoClear = clear;
            quad.material = material;
            if (node) quad.render(renderer); else renderer.render(quadScene, quadCamera);
        } finally { renderer.autoClear = autoClear; }
    }

    /* A frame chain: the half-float frame, its bloom mips and the passes that
     * read them (their textures are inputs, so the passes are built with
     * them). The live frame keeps one; the proofs build their own. */
    function targetOptions(extra = {}) {
        return { type: THREE.HalfFloatType, format: THREE.RGBAFormat, depthBuffer: false, stencilBuffer: false, generateMipmaps: false,
            minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter, colorSpace: THREE.LinearSRGBColorSpace, ...extra };
    }
    const makeTarget = (w, h, extra) => (node ? new THREE.RenderTarget(w, h, targetOptions(extra)) : new THREE.WebGLRenderTarget(w, h, targetOptions(extra)));
    function createChain(width, height, samples = 0) {
        const hdr = makeTarget(width, height, { depthBuffer: true, samples });
        hdr.texture.name = 'lit-frame-hdr';
        const mips = Display.bloomSizes(width, height).map(([w, h], i) => { const target = makeTarget(w, h); target.texture.name = 'lit-bloom-' + i; return target; });
        const downs = mips.map((target, i) => {
            const source = i === 0 ? hdr.texture : mips[i - 1].texture;
            return pass('lit-bloom-down-' + i, { fragment: Display.litBloomDownGlsl({ first: i === 0 }),
                glslUniforms: { uSource: { value: source }, uStrength: uniforms.strength },
                nodeFragment: () => Display.litBloomDownNode(T, { source, strength: uniforms.strength, first: i === 0 }) });
        });
        // Level i up = mix(level i down, tent(level i + 1 up), spread); the coarsest level is its own up.
        const upTargets = mips.slice(0, -1).map(target => { const up = makeTarget(target.width, target.height); up.texture.name = target.texture.name + '-up'; return up; });
        const upOf = i => (i < upTargets.length ? upTargets[i] : mips[i]);
        const ups = upTargets.map((target, i) => {
            const source = upOf(i + 1).texture, base = mips[i].texture;
            const destSize = node ? T.uniform(new THREE.Vector2(target.width, target.height)) : { value: new THREE.Vector2(target.width, target.height) };
            const spread = node ? T.uniform(Display.LIT_BLOOM.spread) : { value: Display.LIT_BLOOM.spread };
            const radius = node ? T.uniform(Display.LIT_BLOOM.radius) : { value: Display.LIT_BLOOM.radius };
            return pass('lit-bloom-up-' + i, { fragment: Display.litBloomUpGlsl(),
                glslUniforms: { uSource: { value: source }, uBase: { value: base }, uDestSize: destSize, uSpread: spread, uRadius: radius },
                nodeFragment: () => Display.litBloomUpNode(T, { source, base, destSize, spread, radius }) });
        });
        const bloomTexture = upOf(0).texture;
        const chain = {
            hdr, mips, width, height, samples, display: null, bloomTarget: upOf(0),
            /* The display pass for the current look and operator. */
            retone() {
                const options = { look: params.look, operator: params.operator, bloom: params.bloom, noise: params.noise };
                const material = pass('lit-display-pass', { fragment: Display.litDisplayFragmentGlsl(options),
                    glslUniforms: { uScene: { value: hdr.texture }, uBloom: { value: bloomTexture }, uStrength: uniforms.strength, uGain: uniforms.gain,
                        uPreExposure: uniforms.preExposure, uAgxPower: uniforms.agxPower, uWhiteBalance: uniforms.whiteBalance, uNoise: uniforms.noise },
                    nodeFragment: () => Display.litDisplayFrameNode(T, { scene: hdr.texture, bloom: bloomTexture, uniforms, look: params.look,
                        operator: params.operator, withBloom: params.bloom, withNoise: params.noise }) });
                chain.display?.dispose();
                chain.display = material;
                return material;
            },
            /* Down from the frame, then each level mixed with the coarser one. */
            bloom() {
                downs.forEach((material, i) => draw(mips[i], material, true));
                for (let i = ups.length - 1; i >= 0; i--) draw(upTargets[i], ups[i], true);
            },
            dispose() {
                hdr.dispose(); for (const target of [...mips, ...upTargets]) target.dispose();
                for (const material of [...downs, ...ups, chain.display].filter(Boolean)) material.dispose();
            },
        };
        chain.retone();
        return chain;
    }
    let chain = null, display = null;
    /* The display pass is a full-screen mesh drawn first in the output pass
     * (on WebGPU the canvas's multisampled colour does not survive between
     * passes, so it cannot be a pass of its own). */
    function retone() {
        if (!chain) return;
        const material = chain.retone();
        if (!display) {
            display = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), material);
            display.name = 'lit-display-pass';
            display.frustumCulled = false; display.renderOrder = Number.MIN_SAFE_INTEGER; display.visible = false;
            display.raycast = () => {};
            display.userData = { presentation_only: true };
            group.add(display);
        } else display.material = material;
    }
    function syncUniforms() {
        uniforms.gain.value = params.gain;
        uniforms.preExposure.value = params.preExposure;
        uniforms.agxPower.value = params.agxPower;
        const wb = params.whiteBalance || IDENTITY;
        if (node) wb.forEach((row, r) => uniforms.whiteBalance[r].value.set(...row));
        // GLSL mat3 is column-major: set() takes rows.
        else uniforms.whiteBalance.value.set(...wb[0], ...wb[1], ...wb[2]);
        uniforms.noise.value.set(Display.LIT_NOISE.shot, Display.LIT_NOISE.read, params.seed, Display.LIT_NOISE.dither);
    }
    function ensure(width, height) {
        const samples = params.msaa ? (node ? renderer.samples || 0 : (renderer.getContext().getContextAttributes?.().antialias ? 4 : 0)) : 0;
        if (chain && chain.width === width && chain.height === height && chain.samples === samples) return;
        chain?.dispose();
        chain = createChain(width, height, samples);
        retone();
    }

    // ---- Emitters: each lit fixture's face ------------------------------------
    const emitterGeometry = new THREE.CircleGeometry(1, 24);
    const emitters = [];
    function emitterMaterial() {
        if (node) {
            const u = { radiance: T.uniform(new THREE.Vector3()), axis: T.uniform(new THREE.Vector3(0, 0, 1)),
                cone: T.uniform(new THREE.Vector4(0, 1, 0, 1)), clamp: T.uniform(Display.LIT_FRAME.emitterClamp) };
            const material = new THREE.NodeMaterial();
            material.fragmentNode = Display.litEmitterNode(T, u);
            material.userData.u = u;
            return material;
        }
        const u = { uRadiance: { value: new THREE.Vector3() }, uAxis: { value: new THREE.Vector3(0, 0, 1) },
            uCone: { value: new THREE.Vector4(0, 1, 0, 1) }, uClamp: { value: Display.LIT_FRAME.emitterClamp } };
        const material = new THREE.ShaderMaterial({ uniforms: u, vertexShader: Display.LIT_EMITTER_VERTEX_GLSL, fragmentShader: Display.LIT_EMITTER_FRAGMENT_GLSL });
        material.userData.u = { radiance: u.uRadiance, axis: u.uAxis, cone: u.uCone, clamp: u.uClamp };
        return material;
    }
    const zAxis = new THREE.Vector3(0, 0, 1);
    /* The eased fixture lights in the world (lit-view.mjs fixtureWorldLights
     * entries through lit-model's ease): one face per light with output. */
    function updateEmitters() {
        const wanted = lights.filter(light => Display.LIT_EMITTERS[light.kind]);
        while (emitters.length < wanted.length) {
            const mesh = new THREE.Mesh(emitterGeometry, emitterMaterial());
            mesh.name = 'lit-fixture-face'; mesh.frustumCulled = false; mesh.layers.set(LIT_EMITTER_LAYER); mesh.raycast = () => {};
            mesh.userData = { presentation_only: true, assumed: true, measured: false };
            group.add(mesh); emitters.push(mesh);
        }
        clamped = 0;
        emitters.forEach((mesh, i) => {
            const light = wanted[i];
            const L = light ? Display.emitterLuminance(light, lumens) : 0;
            mesh.visible = L > 0;
            if (!mesh.visible) return;
            const face = Display.LIT_EMITTERS[light.kind], u = mesh.material.userData.u;
            const origin = light.origin?.isVector3 ? light.origin : new THREE.Vector3(...light.origin);
            const axis = (light.direction?.isVector3 ? light.direction.clone() : new THREE.Vector3(...light.direction)).normalize();
            mesh.position.copy(origin).addScaledVector(axis, Display.LIT_EMITTERS.offsetM);
            mesh.quaternion.setFromUnitVectors(zAxis, axis);
            mesh.scale.setScalar(Math.sqrt(face.areaM2 / Math.PI));
            mesh.updateMatrixWorld(true);
            const rgb = Model.cctToLinearRgb(light.kelvin);
            u.radiance.value.set(...rgb.map(c => c * L * params.preExposure));
            u.axis.value.copy(axis);
            const half = Number.isFinite(light.halfAngle) ? light.halfAngle : Math.PI / 4, penumbra = Number.isFinite(light.penumbra) ? light.penumbra : 0;
            u.cone.value.set(Math.cos(half), Math.cos(half * (1 - penumbra)), face.spill || 0, face.lambertian ? 1 : 0);
            if (L * params.preExposure * Math.max(...rgb) > Display.LIT_FRAME.emitterClamp) clamped++;
        });
    }

    // ---- Meter (Mesh): a coarse copy of the scene-referred pass ---------------------
    let meterMaterial = null;
    function requestMeter() {
        const at = now();
        if (!meterWanted || meterPending || at - meterRequestedAt < Exposure.LIT_METER.periodMs) return;
        const { width, height } = Exposure.LIT_METER;
        if (!meterTarget) {
            meterTarget = makeTarget(width, height, { minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter });
            meterMaterial = node ? (() => { const m = new THREE.NodeMaterial(); m.fragmentNode = T.texture(chain.hdr.texture, T.uv()); m.vertexNode = T.vec4(T.positionGeometry.xy, 0, 1); return m; })()
                : new THREE.ShaderMaterial({ uniforms: { uSource: { value: null } }, vertexShader: 'varying vec2 vUv;\nvoid main() { vUv = position.xy * 0.5 + 0.5; gl_Position = vec4(position.xy, 0.0, 1.0); }',
                    fragmentShader: 'precision highp float;\nuniform sampler2D uSource;\nvarying vec2 vUv;\nvoid main() { gl_FragColor = texture2D(uSource, vUv); }' });
            meterMaterial.depthTest = false; meterMaterial.depthWrite = false; meterMaterial.blending = THREE.NoBlending;
        }
        if (node) meterMaterial.fragmentNode.value = chain.hdr.texture; else meterMaterial.uniforms.uSource.value = chain.hdr.texture;
        meterPending = true; meterRequestedAt = at;
        draw(meterTarget, meterMaterial, true);
        const token = meterToken;
        readPixelsAsync(meterTarget, width, height).then(values => {
            if (token !== meterToken || !active || !values) return;
            const samples = new Float64Array(width * height);
            for (let i = 0; i < width * height; i++) samples[i] = values[i * 4 + 3] > 0.5
                ? Exposure.lumaOf([values[i * 4], values[i * 4 + 1], values[i * 4 + 2]]) / params.preExposure : 0;
            metered = samples; meterSerial++;
        }).catch(() => { /* the next period meters again */ }).finally(() => { if (token === meterToken) meterPending = false; });
    }
    /* A half-float target's pixels as floats (RGBA rows, padding removed). */
    async function readPixelsAsync(target, width, height) {
        let data;
        if (node) data = await renderer.readRenderTargetPixelsAsync(target, 0, 0, width, height);
        else {
            const gl = renderer.getContext(), pack = gl.getParameter(gl.PIXEL_PACK_BUFFER_BINDING);
            gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null);
            try { data = await renderer.readRenderTargetPixelsAsync(target, 0, 0, width, height, new Uint16Array(width * height * 4)); }
            finally { gl.bindBuffer(gl.PIXEL_PACK_BUFFER, pack); }
        }
        if (!data) return null;
        const row = width * 4, padded = data.length === row * height ? row : Math.ceil(row * data.BYTES_PER_ELEMENT / 256) * 256 / data.BYTES_PER_ELEMENT;
        const half = data instanceof Uint16Array, out = new Float32Array(row * height);
        for (let y = 0; y < height; y++) for (let x = 0; x < row; x++) {
            const v = data[y * padded + x];
            out[y * row + x] = half ? THREE.DataUtils.fromHalfFloat(v) : v;
        }
        return out;
    }

    // ---- The frame ---------------------------------------------------------------------
    function withLayers(camera, mask, fn) {
        const saved = camera.layers.mask;
        camera.layers.set(mask);
        try { fn(); } finally { camera.layers.mask = saved; }
    }
    function scenePass(camera, layer) { withLayers(camera, layer, () => inner.call(renderer, scene, camera)); }
    function occluderPass(camera) {
        const list = occluders() || [];
        if (!list.length) return;
        const saved = list.map(({ object, material }) => {
            const chain = [];
            for (let o = object.parent; o; o = o.parent) if (!o.visible) { chain.push(o); o.visible = true; }
            const entry = { object, material: object.material, visible: object.visible, layers: object.layers.mask, chain };
            object.material = material; object.visible = true; object.layers.set(LIT_OCCLUDER_LAYER);
            return entry;
        });
        try { scenePass(camera, LIT_OCCLUDER_LAYER); }
        finally {
            for (const entry of saved) {
                entry.object.material = entry.material; entry.object.visible = entry.visible; entry.object.layers.mask = entry.layers;
                for (const o of entry.chain) o.visible = false;
            }
        }
    }
    /* One Lit frame into the current render target. */
    function render(camera) {
        const output = renderer.getRenderTarget(), face = renderer.getActiveCubeFace?.() ?? 0, mip = renderer.getActiveMipmapLevel?.() ?? 0;
        if (output) size.set(output.width, output.height); else renderer.getDrawingBufferSize(size);
        const t0 = now();
        ensure(Math.max(1, Math.round(size.x)), Math.max(1, Math.round(size.y)));
        lastCamera = camera;
        try { beforeFrame?.(camera); } catch (_) { /* the frame still renders */ }
        syncUniforms();
        const autoClear = renderer.autoClear, background = scene.background, alpha = renderer.getClearAlpha();
        renderer.getClearColor(clearColor);
        const hidden = [];
        try {
            // 1 scene-referred, cleared to transparent black.
            scene.background = null; renderer.setClearColor(0x000000, 0);
            renderer.setRenderTarget(chain.hdr); renderer.autoClear = true;
            scenePass(camera, LIT_FRAME_LAYER);
            renderer.autoClear = false;
            // 2 the meter reads the lit content only; 3 the fixture faces, behind the room.
            requestMeter();
            if (emitters.some(mesh => mesh.visible)) {
                renderer.setRenderTarget(chain.hdr);
                occluderPass(camera);
                renderer.setRenderTarget(chain.hdr);
                scenePass(camera, LIT_EMITTER_LAYER);
            }
            renderer.setClearColor(clearColor, alpha); scene.background = background;
            // 4 bloom: down from the frame, then each level mixed with the coarser one.
            if (params.bloom && Exposure.litTonePresentation(params.operator)) chain.bloom();
            // 5 output: the display pass first, then everything unlit over it.
            renderer.setRenderTarget(output, face, mip); renderer.autoClear = autoClear;
            for (const entry of receiverRoots) { hidden.push([entry, entry.visible]); entry.visible = false; }
            for (const entry of meshEntries) entry.node.material = entry.depth;
            display.visible = true;
            inner.call(renderer, scene, camera);
        } finally {
            display.visible = false;
            for (const [object, visible] of hidden) object.visible = visible;
            for (const entry of meshEntries) entry.node.material = entry.lit;
            scene.background = background; renderer.setClearColor(clearColor, alpha); renderer.autoClear = autoClear;
            if (renderer.getRenderTarget() !== output) renderer.setRenderTarget(output, face, mip);
        }
        frames++; lastPassMs = now() - t0;
    }

    const frame = {
        group, layer: LIT_FRAME_LAYER,
        get active() { return active; },
        install() {
            if (wrapper) return;
            active = true;
            scene.add(group);
            const previous = renderer.render, owned = { active: true };
            inner = previous; installation = owned;
            wrapper = function litFrameRender(target3d, camera) {
                // A retired wrapper can still belong to a later render owner.
                // Its delegate and retirement belong to this installation,
                // even if this frame is installed again around that owner.
                if (!owned.active || !active || target3d !== scene || !camera?.isCamera || scene.overrideMaterial) return previous.apply(this, arguments);
                return render(camera);
            };
            renderer.render = wrapper;
        },
        uninstall() {
            active = false;
            if (installation) installation.active = false;
            // A later wrapper (a test's timing probe) keeps ours as its inner; ours passes through once inactive.
            if (wrapper && renderer.render === wrapper) renderer.render = inner;
            wrapper = null; installation = null;
            frame.setReceivers([]); meshEntries = []; occluders = () => []; beforeFrame = null; lights = [];
            scene.remove(group);
            meterToken++; meterPending = false; meterRequestedAt = -Infinity; metered = null;
        },
        dispose() {
            frame.uninstall();
            chain?.dispose(); meterTarget?.dispose(); readTarget?.dispose(); meterMaterial?.dispose();
            for (const mesh of emitters) mesh.material.dispose();
            emitterGeometry.dispose(); display?.geometry.dispose(); quad.geometry?.dispose?.();
            chain = null; display = null; emitters.length = 0;
        },
        /* What only the scene-referred pass draws (relit splats, points),
         * hidden in the output pass. Exact restore of their layers. */
        setReceivers(objects) {
            for (const entry of receivers) entry.layers.disable(LIT_FRAME_LAYER);
            receivers = []; receiverRoots = objects.filter(Boolean);
            for (const object of receiverRoots) object.traverse(child => {
                if (child.layers.isEnabled(LIT_FRAME_LAYER)) return;
                child.layers.enable(LIT_FRAME_LAYER); receivers.push(child);
            });
        },
        /* The lit mesh (lit-view swapped entries: node, lit, depth). */
        setMeshEntries(entries) { meshEntries = entries.filter(entry => entry.depth); params.msaa = entries.length > 0; },
        setOccluders(fn) { occluders = typeof fn === 'function' ? fn : () => []; },
        setBeforeFrame(fn) { beforeFrame = typeof fn === 'function' ? fn : null; },
        setLights(next, options = {}) { lights = next || []; lumens = options.lumens || lumens; updateEmitters(); },
        set(next) {
            const retoned = ['look', 'operator', 'bloom', 'noise'].some(key => key in next && next[key] !== params[key]);
            Object.assign(params, next);
            if (retoned) retone();
        },
        params() { return { ...params }; },
        meter(on) { meterWanted = !!on; if (!on) { metered = null; meterToken++; meterPending = false; } },
        meterSamples() { return metered; },
        get meterSerial() { return meterSerial; },
        /* Linear readback for calibration: the lit content's scene-referred
         * luminance (cd/m², modeled), never tone mapped, no bloom, no faces. */
        async readLinear({ scale = 0.25 } = {}) {
            if (!active || !lastCamera || !inner) return null;
            renderer.getDrawingBufferSize(size);
            const factor = Math.min(1, Math.max(0.01, Number(scale) || 0.25));
            const width = Math.max(1, Math.round(size.x * factor)), height = Math.max(1, Math.round(size.y * factor));
            if (!readTarget || readTarget.width !== width || readTarget.height !== height) { readTarget?.dispose(); readTarget = makeTarget(width, height, { depthBuffer: true }); }
            const previous = renderer.getRenderTarget(), autoClear = renderer.autoClear, background = scene.background, alpha = renderer.getClearAlpha();
            renderer.getClearColor(clearColor);
            try {
                scene.background = null; renderer.setClearColor(0x000000, 0);
                renderer.setRenderTarget(readTarget); renderer.autoClear = true;
                scenePass(lastCamera, LIT_FRAME_LAYER);
            } finally { scene.background = background; renderer.setClearColor(clearColor, alpha); renderer.autoClear = autoClear; renderer.setRenderTarget(previous); }
            const values = await readPixelsAsync(readTarget, width, height);
            if (!active || !values) return null;
            const luminance = new Float32Array(width * height), coverage = new Uint8Array(width * height);
            for (let i = 0; i < width * height; i++) {
                const a = values[i * 4 + 3];
                luminance[i] = Exposure.lumaOf([values[i * 4], values[i * 4 + 1], values[i * 4 + 2]]) / Math.max(1e-4, Math.min(1, a)) / params.preExposure;
                coverage[i] = a > 0.5 ? 1 : 0;
            }
            return { width, height, bottomUp: !renderer.backend?.isWebGPUBackend, unit: 'cd/m2', modeled: true, measured: false,
                preExposure: params.preExposure, luminance, coverage };
        },
        /* Proofs: an authored HDR frame (Float32Array RGBA, width x height,
         * premultiplied, pre-exposed) through the real bloom and display
         * passes; returns the bloom's mip 0 (floats) and the display output
         * (8-bit RGBA), for the CPU twins in lit-display.mjs. */
        async proofPasses(image, width, height, next = {}) {
            const saved = { ...params };
            const source = new THREE.DataTexture(image, width, height, THREE.RGBAFormat, THREE.FloatType);
            source.minFilter = THREE.NearestFilter; source.magFilter = THREE.NearestFilter; source.needsUpdate = true;
            const copyMaterial = pass('lit-frame-proof-copy', {
                fragment: 'precision highp float;\nuniform sampler2D uSource;\nvoid main() { gl_FragColor = texelFetch(uSource, ivec2(gl_FragCoord.xy), 0); }',
                glslUniforms: { uSource: { value: source } }, nodeFragment: () => T.textureLoad(source, T.ivec2(T.screenCoordinate.xy)) });
            const out = makeTarget(width, height, { type: THREE.UnsignedByteType });
            const previous = renderer.getRenderTarget();
            let proof = null, drawingRestored = false;
            try {
                // Every draw happens before the first await, with the proof's own
                // targets and parameters, so the live frame cannot interleave.
                Object.assign(params, next); syncUniforms();
                proof = createChain(width, height);
                draw(proof.hdr, copyMaterial, true);
                if (Exposure.litTonePresentation(params.operator) && params.bloom) proof.bloom();
                draw(out, proof.display, true);
                Object.assign(params, saved); syncUniforms();
                renderer.setRenderTarget(previous);
                drawingRestored = true;
                const bloom = await readPixelsAsync(proof.bloomTarget, proof.bloomTarget.width, proof.bloomTarget.height);
                const frameHalf = await readPixelsAsync(proof.hdr, width, height);
                let pixels;
                if (node) pixels = await renderer.readRenderTargetPixelsAsync(out, 0, 0, width, height);
                else {
                    const gl = renderer.getContext(), pack = gl.getParameter(gl.PIXEL_PACK_BUFFER_BINDING);
                    gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null);
                    pixels = new Uint8Array(width * height * 4);
                    try { renderer.readRenderTargetPixels(out, 0, 0, width, height, pixels); } finally { gl.bindBuffer(gl.PIXEL_PACK_BUFFER, pack); }
                }
                const row = width * 4, padded = pixels.length === row * height ? row : Math.ceil(row / 256) * 256;
                const display8 = new Uint8Array(row * height);
                for (let y = 0; y < height; y++) display8.set(pixels.subarray(y * padded, y * padded + row), y * row);
                return { width, height, mip: { width: proof.mips[0].width, height: proof.mips[0].height, data: bloom }, frame: frameHalf, display: display8,
                    sizes: proof.mips.map(target => [target.width, target.height]) };
            } finally {
                // Draw failures happen before the normal synchronous restore.
                // After asynchronous readback begins, leave the live frame's
                // current target alone rather than restoring a stale target.
                if (!drawingRestored) renderer.setRenderTarget(previous);
                Object.assign(params, saved); syncUniforms();
                source.dispose(); copyMaterial.dispose(); out.dispose(); proof?.dispose();
            }
        },
        /* Proofs: the frame's own passes (bloom + display), ms per frame over
         * `batch` back to back, waited on once. */
        async time(batch = 20) {
            if (!chain) return null;
            const wait = async () => { if (node) await renderer.backend.device?.queue.onSubmittedWorkDone(); else renderer.getContext().finish(); };
            const scratch = makeTarget(chain.width, chain.height, { type: THREE.UnsignedByteType });
            const previous = renderer.getRenderTarget();
            try {
                await wait();
                const t0 = performance.now();
                for (let k = 0; k < batch; k++) { chain.bloom(); draw(scratch, chain.display, true); }
                await wait();
                return (performance.now() - t0) / batch;
            } finally { scratch.dispose(); renderer.setRenderTarget(previous); }
        },
        state() {
            return Object.freeze({ active, format: Display.LIT_FRAME.format, width: chain?.width ?? 0, height: chain?.height ?? 0, samples: chain?.samples || 0,
                look: params.look, operator: params.operator, bloom: params.bloom && Exposure.litTonePresentation(params.operator),
                bloomMips: chain?.mips.length ?? Display.LIT_BLOOM.mips, bloomStrength: Display.LIT_BLOOM.strength, noise: params.noise && Exposure.litTonePresentation(params.operator),
                emitters: emitters.filter(mesh => mesh.visible).length, emittersClamped: clamped, frames, lastPassMs,
                presentation: true, measured: false });
        },
    };
    return frame;
}
