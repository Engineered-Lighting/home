/* The live body's twin on the WebGPU path (?renderer=webgpu). three's node renderer cannot run the body layer's GLSL,
 * and a Points object there draws only 1 px dots, so on that path apartment-body-layer.mjs hides its GLSL objects and
 * draws through these instead: instanced sprites (as the scan's own cloud does there), built from TSL nodes and reading
 * the same per-point buffers and the same uniform objects the WebGL path uses. They draw the Recording particles (the
 * warm cores with their bands, rim, motion and provenance, and the thinner head) and the held object in the accent
 * blue. The glow, the trails, the motes and the floor pool are the WebGL path's only.
 * Loaded only on that path, where 'three' is the r186 WebGPU build and 'three/tsl' its node library. */
import * as THREE from 'three';
import {
    Fn, If, Discard, float, vec2, vec3, vec4, uv, reference, instancedBufferAttribute, varying, modelViewMatrix,
    modelWorldMatrix, cameraProjectionMatrix, cameraPosition, positionGeometry, screenSize, length, exp, pow, sin, mix,
    clamp, max, min, smoothstep, step, abs, dot, normalize, select,
} from 'three/tsl';

/* B: the layer's draw buffers (apartment-body-frame.mjs drawBuffers). shared: its shared uniforms; body and held: the
 * Recording and held-object materials' own uniforms (opacity, size, gain, tint). */
export function createBodySprites({ B, shared, body, held }) {
    const instanced = (array, size) => new THREE.InstancedBufferAttribute(array, size).setUsage(THREE.DynamicDrawUsage);
    const attrs = { pos: instanced(B.pos, 3), nrm: instanced(B.nrm, 3), kind: instanced(B.kind, 1), alpha: instanced(B.alpha, 1),
        motion: instanced(B.motion, 1), head: instanced(B.head, 1), rand: instanced(B.rand, 1) };
    const ref = (u, type) => reference('value', type, u);

    function material(uniforms, heldObject) {
        const u = name => uniforms[name] || shared[name];
        const point = instancedBufferAttribute(attrs.pos, 'vec3');
        const normal = instancedBufferAttribute(attrs.nrm, 'vec3');
        const kind = varying(instancedBufferAttribute(attrs.kind, 'float'), 'vKind');
        const alpha = varying(instancedBufferAttribute(attrs.alpha, 'float'), 'vAlpha');
        const motion = varying(instancedBufferAttribute(attrs.motion, 'float'), 'vMotion');
        const head = varying(instancedBufferAttribute(attrs.head, 'float'), 'vHead');
        const rand = varying(instancedBufferAttribute(attrs.rand, 'float'), 'vRand');
        const height = varying(point.z, 'vHeight');
        const mv = modelViewMatrix.mul(vec4(point, 1));
        const depth = mv.z.negate();
        const world = modelWorldMatrix.mul(vec4(point, 1)).xyz;
        const facingNormal = select(length(normal).greaterThan(0.1), normalize(normal), vec3(0, 0, 1));
        const worldNormal = normalize(modelWorldMatrix.mul(vec4(facingNormal, 0)).xyz);
        const rim = varying(float(1).sub(abs(dot(worldNormal, normalize(cameraPosition.sub(world))))), 'vRim');
        const px = ref(u('uWorld'), 'float').mul(ref(shared.uSizeMul, 'float')).mul(float(1).add(head.mul(0.6)))
            .mul(ref(shared.uScalePx, 'float')).div(max(depth, 0.2));
        const size = select(alpha.lessThan(0.01), float(0), clamp(px, ref(u('uMinPx'), 'float'), ref(u('uMaxPx'), 'float')));
        const clip = cameraProjectionMatrix.mul(mv);
        const sprite = new THREE.NodeMaterial();
        sprite.name = heldObject ? 'apartment-body-held-object-webgpu' : 'apartment-body-recording-webgpu';
        sprite.transparent = true;
        sprite.depthTest = true;
        sprite.depthWrite = heldObject;
        sprite.blending = heldObject ? THREE.NormalBlending : THREE.AdditiveBlending;
        sprite.vertexNode = clip.add(vec4(positionGeometry.xy.mul(size).mul(2).div(screenSize).mul(clip.w), 0, 0));
        const opacity = ref(u('uOpacity'), 'float'), gain = ref(u('uGain'), 'float'), tint = ref(u('uTint'), 'vec3');
        sprite.fragmentNode = Fn(() => {
            const d = length(uv().sub(0.5));
            If(d.greaterThan(0.5), () => { Discard(); });
            if (heldObject) {
                If(kind.lessThan(4.5), () => { Discard(); });
                const g = float(1).sub(smoothstep(0.28, 0.5, d));
                return vec4(min(tint.mul(rim.mul(0.45).add(0.75)).mul(gain), vec3(0.95)), g.mul(alpha).mul(opacity));
            }
            If(kind.greaterThan(4.5).or(head.greaterThan(0.35).and(rand.greaterThan(0.45))), () => { Discard(); });
            const back = step(0.5, kind).mul(float(1).sub(step(2.5, kind)));
            const guess = step(2.5, kind).mul(float(1).sub(step(3.5, kind)));
            const remembered = step(3.5, kind).mul(float(1).sub(step(4.5, kind)));
            const core = exp(d.mul(d).mul(-18));
            const band = pow(sin(height.mul(150).add(ref(shared.uTime, 'float').mul(2.2))).mul(0.5).add(0.5), 3).mul(0.38).add(0.62);
            const energy = rim.mul(rim).mul(1.3).mul(float(1).sub(head)).add(motion.mul(1.1)).add(0.75)
                .mul(band).mul(ref(shared.uFlicker, 'float')).mul(gain).mul(float(1).sub(head.mul(0.3)))
                .mul(float(1).sub(guess.mul(0.3)).sub(remembered.mul(0.2)).sub(back.mul(0.35)));
            const colour = mix(tint, ref(shared.uCore, 'vec3'), clamp(rim.mul(rim).add(motion.mul(0.8)), 0, 1));
            return vec4(colour.mul(energy).mul(core).mul(alpha).mul(opacity).mul(float(1).sub(head.mul(0.35))), 1);
        })();
        return sprite;
    }

    const passes = [[body, false, 'body-recording-webgpu', 20]];
    if (held) passes.push([held, true, 'body-held-object-webgpu', 21]);
    const sprites = passes.map(([uniforms, heldObject, name, order]) => {
        const s = new THREE.Sprite(material(uniforms, heldObject));
        s.name = name; s.count = 0; s.frustumCulled = false; s.renderOrder = order;
        s.raycast = () => {};
        s.userData.presentation_only = true;
        return s;
    });
    return {
        objects: sprites,
        /* The first n points of B are this frame's. */
        update(n) {
            if (n > 0) for (const a of Object.values(attrs)) { a.needsUpdate = true; a.addUpdateRange?.(0, n * a.itemSize); }
            for (const s of sprites) s.count = n;
        },
        dispose() { for (const s of sprites) s.material.dispose(); },   // a Sprite's quad is three's own, shared
    };
}
