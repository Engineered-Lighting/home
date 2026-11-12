/* The live body in the apartment view, drawn in the owner-approved "Recording" look (2026-11-11): a projected memory in
 * warm light. Fine hot particles with interference bands, an analogue flicker and a rare glitch band, hotter where the
 * surface turns away and where it moves, two trailing frames, a soft glow, motes in the air and a faint floor pool.
 * - Provenance shows: back fill is a little dimmer, remembered limbs dimmer still, guessed limbs faint and flickering.
 * - The head is drawn thinner (no face: the data is depth only).
 * - What the person holds is part of the house, not of the person: steady points in Home's accent blue (#4D9CFF), no
 *   glow, flicker or trails.
 * - Zoomed out it stays crisp: points grow at most 1.25x and the glow eases off.
 * Frames are den-live-body-1 (apartment-body-frame.mjs), drawn about one frame interval behind the newest and
 * interpolated per depth-pixel key. Presentation only: no network, identity, lighting or model API. Whoever holds the
 * stream calls update(). */
import * as THREE from 'three';
import { KIND, decodeBodyFrame, drawBuffers, mergeFrames, mergeJoints, zoomRatio, MAX_POINTS } from './apartment-body-frame.mjs';

export const RECORDING = Object.freeze({ tint: [1.0, 0.5, 0.16], core: [1.0, 0.88, 0.62], object: [0x4d / 255, 0x9c / 255, 0xff / 255],
    world: 0.0105, minPx: 1.1, gain: 1.15, glowWorld: 0.05, glowGain: 0.16, motes: 22, pool: 0.14 });
const CAPACITY = MAX_POINTS * 2, GONE_S = 2.5, FADE_S = 0.6;

const VERT = /* glsl */`
attribute float aKind, aAlpha, aMotion, aHead, aRand;
uniform float uScalePx, uWorld, uSizeMul, uMinPx, uMaxPx;
uniform vec3 uGlitch;
varying float vAlpha, vRim, vKind, vMotion, vRand, vHead, vWz;
void main() {
  vec3 p = position, n = length(normal) > 0.1 ? normalize(normal) : vec3(0.0, 0.0, 1.0);
  if (aKind < 4.5) {
    if (p.z > uGlitch.x && p.z < uGlitch.y) p.x += uGlitch.z;
    p += (vec3(fract(aRand * 13.7), fract(aRand * 71.3), fract(aRand * 37.1)) - 0.5) * 0.006;
  }
  vec4 world = modelMatrix * vec4(p, 1.0);
  vec4 mv = viewMatrix * world; float depth = -mv.z;
  float px = uWorld * uSizeMul * (1.0 + aHead * 0.6) * uScalePx / max(depth, 0.2);
  gl_PointSize = aAlpha < 0.01 ? 0.0 : clamp(px, uMinPx, uMaxPx);
  vec3 nw = normalize(mat3(modelMatrix) * n);
  vRim = 1.0 - abs(dot(nw, normalize(cameraPosition - world.xyz)));
  vAlpha = aAlpha; vKind = aKind; vMotion = aMotion; vRand = aRand; vHead = aHead; vWz = p.z;
  gl_Position = projectionMatrix * mv;
}`;
const COMMON = /* glsl */`
uniform float uTime, uGain, uOpacity, uFlicker;
uniform vec3 uTint, uCore;
varying float vAlpha, vRim, vKind, vMotion, vRand, vHead, vWz;
float hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
float isBack() { return step(0.5, vKind) * (1.0 - step(2.5, vKind)); }
float isGuess() { return step(2.5, vKind) * (1.0 - step(3.5, vKind)); }
float isMem() { return step(3.5, vKind) * (1.0 - step(4.5, vKind)); }
float isObj() { return step(4.5, vKind); }
bool thinHead() { return vHead > 0.35 && vRand > 0.45; }
`;
// The person: hot particle cores, bands every ~4 cm of height drifting upward, brighter at the rim and where it moves.
const HOLO = COMMON + /* glsl */`
void main() {
  float d = length(gl_PointCoord - vec2(0.5)); if (d > 0.5 || thinHead() || isObj() > 0.5) discard;
  float g = isGuess(); if (g > 0.5 && hash(vec2(floor(uTime * 9.0), vRand * 91.0)) > 0.55) discard;
  float core = exp(-d * d * 18.0);
  float band = 0.62 + 0.38 * pow(0.5 + 0.5 * sin(vWz * 150.0 + uTime * 2.2), 3.0);
  float e = (0.75 + 1.3 * vRim * vRim * (1.0 - vHead) + 1.1 * vMotion) * band * uFlicker * uGain * (1.0 - 0.3 * vHead);
  e *= 1.0 - 0.55 * g - 0.2 * isMem() - 0.35 * isBack();
  vec3 col = mix(uTint, uCore, clamp(vRim * vRim + vMotion * 0.8, 0.0, 1.0));
  gl_FragColor = vec4(col * e * core * vAlpha * uOpacity * (1.0 - 0.35 * vHead), 1.0);
}`;
// A wide, faint gaussian of the same points: the glow a bloom pass would give, without one.
const GLOW = COMMON + /* glsl */`
void main() {
  float d = length(gl_PointCoord - vec2(0.5)); if (d > 0.5 || isObj() > 0.5) discard;
  gl_FragColor = vec4(uTint * exp(-d * d * 7.0) * vAlpha * uOpacity * uGain * uFlicker * (1.0 - 0.6 * isGuess()), 1.0);
}`;
// Over a lit room, additive light needs a slightly darker surround to read.
const HALO = COMMON + /* glsl */`
void main() {
  float d = length(gl_PointCoord - vec2(0.5)); if (d > 0.5 || isObj() > 0.5) discard;
  gl_FragColor = vec4(0.0, 0.0, 0.0, exp(-d * d * 6.0) * vAlpha * uOpacity);
}`;
// The held object: crisp, steady, matte-luminous, in the accent colour.
const OBJECT = COMMON + /* glsl */`
void main() {
  float d = length(gl_PointCoord - vec2(0.5)); if (d > 0.5 || isObj() < 0.5) discard;
  float g = 1.0 - smoothstep(0.28, 0.5, d);
  gl_FragColor = vec4(min(uTint * (0.75 + 0.45 * vRim) * uGain, vec3(0.95)), g * vAlpha * uOpacity);
}`;
const MOTE_VERT = /* glsl */`
attribute float aAlpha; uniform float uScalePx, uWorld, uMinPx; varying float vA;
void main() { vec4 mv = modelViewMatrix * vec4(position, 1.0); gl_PointSize = aAlpha < 0.01 ? 0.0 : clamp(uWorld * uScalePx / max(-mv.z, 0.2), uMinPx, 12.0); vA = aAlpha; gl_Position = projectionMatrix * mv; }`;
const MOTE_FRAG = /* glsl */`
uniform vec3 uTint; uniform float uGain, uOpacity; varying float vA;
void main() { float d = length(gl_PointCoord - vec2(0.5)); if (d > 0.5) discard; gl_FragColor = vec4(uTint * exp(-d * d * 12.0) * vA * uGain * uOpacity, 1.0); }`;
const POOL_FRAG = /* glsl */`
uniform vec3 uTint; uniform float uOpacity; varying vec2 vUv;
void main() { float r = length(vUv - 0.5) * 2.0; float a = exp(-r * r * 4.0) * (1.0 - smoothstep(0.85, 1.0, r)); gl_FragColor = vec4(uTint * a * uOpacity, 1.0); }`;

function release(root) {
    root.traverse(node => {
        node.geometry?.dispose?.();
        for (const material of Array.isArray(node.material) ? node.material : node.material ? [node.material] : []) material.dispose();
    });
    root.clear();
}

export function attachApartmentBodyLayer(engine, { now = () => performance.now() / 1000, animate = true, reducedMotion = false,
    zoomT = null, look = RECORDING } = {}) {
    const parent = engine?.apartmentRoot;
    if (!parent || typeof parent.add !== 'function') throw Error('apartment_body_root_missing');
    const reduced = () => (typeof reducedMotion === 'function' ? reducedMotion() : reducedMotion) === true;
    const root = new THREE.Group();
    root.name = 'home-apartment-body-owned';
    root.userData.noLightingAuthority = true;
    parent.add(root);

    const shared = { uTime: { value: 0 }, uScalePx: { value: 600 }, uFlicker: { value: 1 }, uGlitch: { value: new THREE.Vector3(9, 9, 0) },
        uSizeMul: { value: 1 }, uTint: { value: new THREE.Vector3(...look.tint) }, uCore: { value: new THREE.Vector3(...look.core) } };
    const material = (fragmentShader, extra, options) => new THREE.ShaderMaterial({ vertexShader: VERT, fragmentShader,
        uniforms: { ...shared, uWorld: { value: look.world }, uMinPx: { value: look.minPx }, uMaxPx: { value: 24 }, uGain: { value: look.gain }, uOpacity: { value: 0 }, ...extra },
        transparent: true, depthWrite: false, depthTest: true, blending: THREE.AdditiveBlending, ...options });
    function geometry() {
        const g = new THREE.BufferGeometry(), B = drawBuffers(CAPACITY);
        g.setAttribute('position', new THREE.BufferAttribute(B.pos, 3).setUsage(THREE.DynamicDrawUsage));
        g.setAttribute('normal', new THREE.BufferAttribute(B.nrm, 3).setUsage(THREE.DynamicDrawUsage));
        for (const [name, array] of [['aKind', B.kind], ['aAlpha', B.alpha], ['aMotion', B.motion], ['aHead', B.head], ['aRand', B.rand]])
            g.setAttribute(name, new THREE.BufferAttribute(array, 1).setUsage(THREE.DynamicDrawUsage));
        g.setDrawRange(0, 0);
        g.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1e4);   // points move every frame; never cull
        return { g, B };
    }
    const points = (geo, mat, name, order) => { const o = new THREE.Points(geo.g, mat); o.name = name; o.frustumCulled = false; o.renderOrder = order; root.add(o); return o; };
    const now0 = geometry(), ghostGeo = [geometry(), geometry()];
    const holo = material(HOLO), glow = material(GLOW, { uWorld: { value: look.glowWorld }, uMinPx: { value: 2 }, uMaxPx: { value: 48 }, uGain: { value: look.glowGain } });
    const halo = material(HALO, { uWorld: { value: 0.07 }, uMinPx: { value: 2 }, uMaxPx: { value: 64 } }, { blending: THREE.NormalBlending });
    const objectMat = material(OBJECT, { uTint: { value: new THREE.Vector3(...look.object) }, uGain: { value: 1 } }, { blending: THREE.NormalBlending, depthWrite: true });
    const ghosts = [0.5, 0.25].map(() => material(HOLO));
    points(now0, halo, 'body-halo', 18); points(now0, glow, 'body-glow', 19); points(now0, holo, 'body-recording', 20); points(now0, objectMat, 'body-held-object', 21);
    ghostGeo.forEach((geo, i) => points(geo, ghosts[i], `body-trail-${i + 1}`, 17));

    // Motes: dust in the warm light around the person, drifting up.
    const MN = 600, mote = { pos: new Float32Array(MN * 3), vel: new Float32Array(MN * 3), age: new Float32Array(MN).fill(9), life: new Float32Array(MN).fill(1), a: new Float32Array(MN), next: 0, carry: 0 };
    const moteGeo = new THREE.BufferGeometry();
    moteGeo.setAttribute('position', new THREE.BufferAttribute(mote.pos, 3).setUsage(THREE.DynamicDrawUsage));
    moteGeo.setAttribute('aAlpha', new THREE.BufferAttribute(mote.a, 1).setUsage(THREE.DynamicDrawUsage));
    moteGeo.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1e4);
    const moteMat = new THREE.ShaderMaterial({ vertexShader: MOTE_VERT, fragmentShader: MOTE_FRAG, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
        uniforms: { uScalePx: shared.uScalePx, uWorld: { value: 0.006 }, uMinPx: { value: 1 }, uTint: { value: new THREE.Vector3(1, 0.62, 0.3) }, uGain: { value: 2.2 }, uOpacity: { value: 0 } } });
    const motes = new THREE.Points(moteGeo, moteMat); motes.name = 'body-motes'; motes.frustumCulled = false; motes.renderOrder = 22; root.add(motes);
    // A faint warm pool on the floor under the person (it seats the body in the room).
    const poolMat = new THREE.ShaderMaterial({ vertexShader: 'varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
        fragmentShader: POOL_FRAG, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
        uniforms: { uTint: { value: new THREE.Vector3(1, 0.55, 0.2) }, uOpacity: { value: 0 } } });
    const pool = new THREE.Mesh(new THREE.PlaneGeometry(1.4, 1.4), poolMat); pool.name = 'body-floor-pool'; pool.position.z = 0.012; pool.renderOrder = 3; root.add(pool);

    // The WebGPU path (?renderer=webgpu) can't run the GLSL above: there the layer hides it and draws through the TSL
    // twin in apartment-body-webgpu.mjs, from the same buffers and uniforms (the particles and the held object).
    let twin = null;
    if (engine.renderer?.isWebGPURenderer === true) {
        for (const o of [...root.children]) o.visible = false;
        const cb = new URL(import.meta.url).search;
        import(`./apartment-body-webgpu.mjs${cb}`).then(({ createBodySprites }) => {
            if (disposed) return;
            twin = createBodySprites({ B: now0.B, shared, body: holo.uniforms, held: objectMat.uniforms });
            for (const o of twin.objects) root.add(o);
        }).catch(error => console.warn('[home-3d] body layer: WebGPU twin unavailable', error));
    }

    let disposed = false, rafId = null, last = null, frames = [], period = 0.15, presence = 0, centroid = null, joints = {}, trackId = null, seen = null, poolOn = true;
    let speed = 0, prevCentroid = null, object = null;
    const scratch = new THREE.Vector3(), camWorld = new THREE.Vector3();

    function write(F, geo, fade = 1) {
        const B = geo.B;
        if (!F) { geo.g.setDrawRange(0, 0); return; }
        mergeFrames(null, F, 1, B);
        if (fade !== 1) for (let i = 0; i < B.n; i++) B.alpha[i] *= fade;
        for (const name of Object.keys(geo.g.attributes)) geo.g.attributes[name].needsUpdate = true;
        geo.g.setDrawRange(0, B.n);
    }

    /* One stream frame. Returns false, drawing nothing new, for one that isn't a valid den-live-body-1 frame. A new
     * track replaces the old one at once (one person per layer). */
    function update(frame) {
        if (disposed) return false;
        const F = decodeBodyFrame(frame);
        if (!F) return false;
        const at = now();
        if (trackId !== F.trackId) frames = [];
        const prev = frames[frames.length - 1];
        if (prev && F.captureTs <= prev.captureTs) return false;          // out of order or repeated
        if (prev) period += (Math.max(0.06, Math.min(0.5, at - prev.at)) - period) * 0.3;
        F.at = at; frames.push(F); if (frames.length > 4) frames.shift();
        trackId = F.trackId; seen = at; object = F.object;
        write(frames[frames.length - 2] || null, ghostGeo[0]); write(frames[frames.length - 3] || null, ghostGeo[1]);
        return true;
    }

    function spawnMotes(dt, n, B) {
        if (!n || reduced()) return;
        mote.carry += dt * look.motes;
        while (mote.carry >= 1) {
            mote.carry -= 1;
            const k = Math.floor(Math.random() * n); if (B.kind[k] > 4.5 || B.alpha[k] < 0.3) continue;
            const j = mote.next; mote.next = (mote.next + 1) % MN;
            for (let q = 0; q < 3; q++) mote.pos[j * 3 + q] = B.pos[k * 3 + q] + (Math.random() - 0.5) * 0.04;
            mote.vel[j * 3] = (Math.random() - 0.5) * 0.03; mote.vel[j * 3 + 1] = (Math.random() - 0.5) * 0.03; mote.vel[j * 3 + 2] = 0.05 + Math.random() * 0.03;
            mote.age[j] = 0; mote.life[j] = 1.5 + Math.random() * 2;
        }
    }
    function stepMotes(dt) {
        for (let j = 0; j < MN; j++) {
            if (mote.age[j] >= mote.life[j]) { mote.a[j] = 0; continue; }
            mote.age[j] += dt; for (let q = 0; q < 3; q++) mote.pos[j * 3 + q] += mote.vel[j * 3 + q] * dt;
            const t = mote.age[j] / mote.life[j]; mote.a[j] = Math.sin(Math.PI * Math.min(1, t)) * 0.6;
        }
        moteGeo.attributes.position.needsUpdate = true; moteGeo.attributes.aAlpha.needsUpdate = true;
    }

    function currentZoom() {
        if (typeof zoomT === 'function') { const z = zoomT(); if (Number.isFinite(z)) return Math.max(0, Math.min(1, z)); }
        if (!centroid || !engine.camera) return 1;
        parent.updateWorldMatrix(true, false);
        scratch.set(centroid[0], centroid[1], centroid[2]); parent.localToWorld(scratch);
        engine.camera.getWorldPosition(camWorld);
        return zoomRatio(camWorld.distanceTo(scratch));
    }

    /* Advance the interpolation, the flicker, the motes and the fades by dt seconds. */
    function tick(dt) {
        if (disposed) return;
        const at = now(), step = Math.max(0, Math.min(0.1, dt)), still = reduced();
        shared.uTime.value = still ? 0 : at;
        const camera = engine.camera, renderer = engine.renderer;
        if (camera?.fov) {
            const h = renderer?.getDrawingBufferSize ? renderer.getDrawingBufferSize(new THREE.Vector2()).y : 900;
            shared.uScalePx.value = h / (2 * Math.tan(camera.fov * Math.PI / 360));
        }
        const fresh = seen != null && at - seen < GONE_S;
        presence += ((fresh && frames.length ? 1 : 0) - presence) * (1 - Math.exp(-step / (fresh ? 0.35 : FADE_S)));
        if (!fresh && presence < 0.02 && frames.length) { frames = []; trackId = null; object = null; now0.g.setDrawRange(0, 0); ghostGeo.forEach(geo => geo.g.setDrawRange(0, 0)); }

        let n = 0;
        if (frames.length) {
            const C = frames[frames.length - 1], P = frames.length > 1 ? frames[frames.length - 2] : null;
            const s = still || !P ? 1 : Math.min(1, (at - C.at) / period);
            n = mergeFrames(P, C, s, now0.B);
            for (const name of Object.keys(now0.g.attributes)) now0.g.attributes[name].needsUpdate = true;
            now0.g.setDrawRange(0, n);
            joints = mergeJoints(P, C, s);
            const target = C.centroid && P?.centroid ? P.centroid.map((x, k) => x + (C.centroid[k] - x) * s) : C.centroid;
            if (target) centroid = centroid ? centroid.map((x, k) => x + (target[k] - x) * (1 - Math.exp(-step / 0.3))) : target.slice();
            if (centroid && prevCentroid && step > 0) speed += (Math.hypot(centroid[0] - prevCentroid[0], centroid[1] - prevCentroid[1]) / step - speed) * Math.min(1, step / 0.4);
            if (centroid) prevCentroid = centroid.slice();
        }
        const zt = currentZoom();
        shared.uSizeMul.value = 1 + 0.25 * zt;
        const flicker = still ? 1 : 0.93 + 0.05 * Math.sin(at * 31) + 0.03 * Math.sin(at * 77) + (Math.random() < 0.02 ? -0.12 : 0);
        shared.uFlicker.value = flicker;
        const g = at % 6.5, z0 = 0.4 + 0.9 * ((Math.floor(at / 6.5) * 0.37) % 1);
        shared.uGlitch.value.set(!still && g < 0.16 ? z0 : 9, !still && g < 0.16 ? z0 + 0.1 : 9, 0.02 * Math.sin(at * 90));
        holo.uniforms.uOpacity.value = presence; objectMat.uniforms.uOpacity.value = presence;
        glow.uniforms.uOpacity.value = presence * (1 - 0.6 * zt); halo.uniforms.uOpacity.value = presence * 0.035;
        ghosts[0].uniforms.uOpacity.value = still ? 0 : presence * 0.5; ghosts[1].uniforms.uOpacity.value = still ? 0 : presence * 0.25;
        moteMat.uniforms.uOpacity.value = presence;
        poolMat.uniforms.uOpacity.value = poolOn ? presence * look.pool * (1 - 0.5 * zt) : 0;
        if (centroid) pool.position.set(centroid[0], centroid[1], 0.012);
        spawnMotes(step, n, now0.B); stepMotes(step);
        twin?.update(now0.B.n);
    }

    if (animate && typeof requestAnimationFrame === 'function') {
        const frame = time => {
            if (disposed) return;
            tick(last == null ? 0 : (time - last) / 1000);
            last = time;
            rafId = requestAnimationFrame(frame);
        };
        rafId = requestAnimationFrame(frame);
    }

    return {
        update,
        tick,
        /* The floor pool off (the Lighting view: a warm glow on the floor could read as a lamp's). */
        setPool(on) { poolOn = on !== false; },
        /* What the labels need, in the apartment frame: the interpolated joints, the smoothed centroid, ground speed,
         * the held object's points (every 3rd) and this frame's object label. */
        snapshot() {
            const B = now0.B, objectPoints = [];
            for (let i = 0; i < B.n; i += 3) if (B.kind[i] === KIND.heldObject && B.alpha[i] > 0.3) objectPoints.push([B.pos[i * 3], B.pos[i * 3 + 1], B.pos[i * 3 + 2]]);
            return { trackId, presence, centroid: centroid && centroid.slice(), joints: { ...joints }, speed, objectPoints, object: objectPoints.length > 8 ? object : null };
        },
        status: () => ({ trackId, frames: frames.length, points: now0.B.n, presence, period_s: period, since_s: seen == null ? null : now() - seen,
            webgpu: !!twin }),
        dispose() {
            if (disposed) return;
            disposed = true;
            if (rafId != null && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(rafId);
            parent.remove(root);
            if (twin) { root.remove(...twin.objects); twin.dispose(); }   // their quad is three's shared Sprite geometry
            release(root);
        },
    };
}
