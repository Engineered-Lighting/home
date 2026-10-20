/* home-3d/webgpu-material-ports.mjs — TSL twins of Home's GLSL material sites.
 *
 * WebGPU path only (?renderer=webgpu). three's WebGPURenderer builds every
 * material from nodes: it ignores onBeforeCompile and cannot run a
 * ShaderMaterial. Rather than teach each site about two renderers, this module
 * hooks the renderer's own material library (renderer.library.fromMaterial),
 * which is asked for a node material whenever a program is built:
 *
 *   ShaderMaterial    matched by the fingerprint of its GLSL, replaced by the
 *                     port's node material for code generation only;
 *   onBeforeCompile   matched by the site's customProgramCacheKey(); the
 *                     site's own patch runs once on a probe to prove the GLSL
 *                     is the ported revision and to hand back its uniforms;
 *   anything else     three's ordinary conversion.
 *
 * The scene graph never changes: every object keeps its material object, so
 * picking, readbacks and visibility code see exactly what the WebGL path sees.
 * The render object keeps the original material too, so blending, depth,
 * side and the per-object uniform values all still come from it. Uniforms are
 * looked up from the drawn material at draw time (its uniforms, or the patch's
 * harvested ones), never captured at build, because three shares one compiled
 * program between materials with the same cache key.
 *
 * Colour: the WebGL canvas holds sRGB-encoded values and blends them as such.
 * webgpu-renderer.mjs keeps that by rendering straight to the canvas with no
 * output pass, and asking every material to encode its own output
 * (displayOutputContext below). Ports that write raw values in GLSL, like the
 * point cloud, return a fragmentNode, which three leaves unencoded.
 */
import * as THREE from 'three';
import {
    Fn, If, Loop, Break, Discard, float, vec2, vec3, vec4, attribute, varying, texture, uv, select,
    reference, materialReference, uniform, materialColor, materialOpacity, positionLocal, positionGeometry,
    positionView, normalViewGeometry, modelViewMatrix, modelWorldMatrix, cameraProjectionMatrix,
    screenUV, screenSize, getViewPosition, instancedBufferAttribute, context, sRGBTransferOETF,
    smoothstep, mix, step, sin, cos, fract, dot, length, max, min, clamp, abs, exp, pow, normalize,
    cross, dFdx, dFdy,
} from 'three/tsl';

const cb = new URL(import.meta.url).search;
const { homeDollhouseKeepNode } = await import(`./dollhouse-cutaway-tsl.mjs${cb}`);
const { PORT_FINGERPRINTS, ON_BEFORE_COMPILE_PORTS, shaderMaterialFingerprint, probeOnBeforeCompile } =
    await import(`./webgpu-port-fingerprints.mjs${cb}`);

/*
 * An onBeforeCompile site's uniforms, from its own patch run on a probe.
 * Looked up per material at draw time: materials with one program cache key
 * share one compiled program, so most of them never reach the build hook.
 */
const harvests = new WeakMap();
const drifted = new Map();
function harvestOf(material) {
    let harvest = harvests.get(material);
    if (!harvest) {
        const probe = probeOnBeforeCompile(material);
        const id = ON_BEFORE_COMPILE_PORTS[String(material.customProgramCacheKey())];
        harvest = { uniforms: probe.uniforms, fingerprint: probe.fingerprint, id };
        if (!id || probe.fingerprint !== PORT_FINGERPRINTS[id]) {
            drifted.set(`onBeforeCompile:${id}:${probe.fingerprint}`, { status: 'drifted', kind: 'onBeforeCompile',
                port: id || null, expected: id ? PORT_FINGERPRINTS[id] : null, actual: probe.fingerprint });
        }
        harvests.set(material, harvest);
    }
    return harvest.uniforms;
}

export const displayEncode = (color) => vec4(sRGBTransferOETF(color.rgb), color.a);

/** The renderer-wide output step: every node material encodes to sRGB. */
export function displayOutputContext() {
    return context({ getOutput: (output) => displayEncode(output) });
}

const shaderUniform = (name, type) => materialReference(`uniforms.${name}.value`, type);
const UNIFORM_DEFAULTS = { float: () => 0, vec2: () => new THREE.Vector2(), vec3: () => new THREE.Vector3(),
    vec4: () => new THREE.Vector4(), mat4: () => new THREE.Matrix4() };
const harvestedUniform = (name, type) => uniform(UNIFORM_DEFAULTS[type]())
    .onObjectUpdate(({ material }) => harvestOf(material)[name]?.value);

/*
 * The site's captured opaque depth, sampled at this fragment.
 *
 * Built with the site's current texture: the site's onBeforeRender captures
 * before three builds the program, so the first bind group already holds it.
 * r186's WebGPU backend rebinds a sampled texture only when its version
 * differs, so a later switch to another texture object of the same version
 * would leave the old one bound; the equipment depth target is stable for the
 * life of its fixtures, and a switch is reported if it ever happens. The
 * placeholder (cleared, depth 1) only stands in before a first capture, and
 * every site checks its own valid flag before trusting depth.
 */
let warnedDepthSwitch = false;
function screenDepthNode(placeholder, read, material) {
    const node = texture(read(material) ?? placeholder, screenUV);
    // TextureNode.update also sets the WebGL2 backend's flip for render-target
    // textures; onObjectUpdate replaces it, so chain to it.
    const textureUpdate = Object.getPrototypeOf(node).update;
    return node.onObjectUpdate(function updateDepth(frame) {
        const next = read(frame.material) ?? placeholder;
        if (next !== this.value && this.value !== placeholder && next !== placeholder && !warnedDepthSwitch) {
            warnedDepthSwitch = true;
            console.warn('[home-3d] WebGPU: an equipment depth texture was replaced; its bind group may lag one rebuild');
        }
        this.value = next;
        textureUpdate.call(this, frame);
    });
}

/*
 * three skips a render object's per-object uniform refresh when its world
 * matrix and its material's properties look unchanged, unless the material
 * holds node properties. The original materials here hold none, and the GLSL
 * sites mutate uniform values in place (the beam's eye position, the cutaway
 * and near-cut), which that check cannot see. Refresh them every draw, as
 * three does for any node material.
 */
function refreshEveryDraw(nodeMaterial) {
    nodeMaterial.setupObserver = function setupObserver(builder) {
        const observer = THREE.NodeMaterial.prototype.setupObserver.call(this, builder);
        observer.needsRefresh = () => THREE.RenderObjectRefreshType.FULL;
        return observer;
    };
    return nodeMaterial;
}

// ---- point cloud (pointcloud.js) -------------------------------------------

/**
 * The point cloud as camera-facing quads, one instance per point.
 *
 * WebGPU has no point size, so a Points object can only draw 1 px dots. The
 * cloud keeps its Points object (the loader, fit and visibility code own it);
 * this sprite child draws it. Same law as the GLSL: gl_PointSize in device
 * pixels, never below the 1 px every GL implementation rasterises.
 */
export function createPointSpriteMaterial(glMaterial, { positions, intensity }) {
    const u = (name, type) => reference('value', type, glMaterial.uniforms[name]);
    const position = instancedBufferAttribute(positions, 'vec3');
    const random = varying(fract(sin(dot(position.xy, vec2(12.9898, 78.233))).mul(43758.5453)), 'vRandom');
    const time = u('uTime', 'float'), jitter = u('uJitterAmp', 'float');
    const shimmered = vec3(
        position.x.add(sin(time.mul(0.5).add(random.mul(3.14159))).mul(jitter).mul(0.6)),
        position.y.add(cos(time.mul(0.6).add(random.mul(4.712))).mul(jitter).mul(0.6)),
        position.z.add(sin(time.mul(0.8).add(random.mul(6.2831))).mul(jitter)));
    const mvPosition = modelViewMatrix.mul(vec4(shimmered, 1));
    const dollhouseLocal = varying(u('uDollhouseWorldToLocal', 'mat4').mul(modelWorldMatrix)
        .mul(vec4(shimmered, 1)).xyz, 'vDollhouseLocal');
    const depth = mvPosition.z.negate();
    const wallNear = u('uWallNear', 'float');
    const depthFactor = varying(float(1).sub(smoothstep(u('uFadeNear', 'float'), u('uFadeFar', 'float'), depth))
        .mul(select(wallNear.greaterThan(0), smoothstep(wallNear.mul(0.35), wallNear.mul(0.85), depth), float(1))),
    'vDepthFactor');
    const sizeAtten = u('uBaseSize', 'float').mul(u('uPixelRatio', 'float'))
        .mul(u('uSizeRef', 'float').div(max(depth, u('uDepthFloor', 'float'))));
    const pointSize = max(clamp(sizeAtten.mul(depthFactor.mul(0.7).add(0.3)), 0.5, u('uMaxPointPx', 'float')), 1);
    const clip = cameraProjectionMatrix.mul(mvPosition);
    const offset = positionGeometry.xy.mul(pointSize).mul(2).div(screenSize).mul(clip.w);
    const vIntensity = varying(instancedBufferAttribute(intensity, 'float'), 'vIntensity');

    const material = new THREE.NodeMaterial();
    material.name = 'apartment-points-webgpu';
    material.transparent = true;
    material.blending = THREE.NormalBlending;
    material.depthWrite = true;
    material.depthTest = true;
    material.vertexNode = clip.add(vec4(offset, 0, 0));
    material.fragmentNode = Fn(() => {
        const dist = length(uv().sub(0.5));
        If(dist.greaterThan(0.5), () => { Discard(); });
        const keep = homeDollhouseKeepNode(u, dollhouseLocal);
        If(keep.lessThanEqual(0.001), () => { Discard(); });
        const glow = float(1).sub(smoothstep(0.25, 0.5, dist));
        const brightness = depthFactor.mul(0.75).add(0.25).mul(random.mul(0.15).add(0.85))
            .mul(vIntensity.mul(0.45).add(0.55));
        return vec4(u('uGlowColor', 'color').mul(brightness), glow.mul(u('uOpacity', 'float')).mul(keep));
    })();
    return material;
}

// ---- onBeforeCompile ports (converted built-in material + nodes) ------------

/* modes.js meshDisplayMaterial + injectMeshCutaway (+ injectPhotoFloorSupport)
 * + the near-camera melt. Discards only; colour is the material's own. */
function scanDisplay(photoFloor) {
    return (nodeMaterial) => {
        const local = varying(harvestedUniform('uDollhouseWorldToLocal', 'mat4').mul(modelWorldMatrix)
            .mul(vec4(positionLocal, 1)).xyz, 'vDollhouseLocal');
        nodeMaterial.colorNode = Fn(() => {
            const color = vec4(materialColor).toVar();
            // Derivatives before any discard, for WGSL's uniformity rules.
            const floorFace = photoFloor ? cross(dFdx(local), dFdy(local)).toVar() : null;
            If(homeDollhouseKeepNode(harvestedUniform, local).lessThan(0.5), () => { Discard(); });
            if (photoFloor) {
                If(harvestedUniform('uPhotoFloorOnly', 'float').greaterThan(0.5), () => {
                    const faceLength = length(floorFace);
                    If(local.z.lessThan(-0.25).or(local.z.greaterThan(0.10)).or(faceLength.lessThan(1e-10))
                        .or(abs(floorFace.z).lessThan(faceLength.mul(0.75))), () => { Discard(); });
                });
            }
            const nearCut = harvestedUniform('uMeshNearCut', 'float');
            If(nearCut.greaterThan(0).and(positionView.z.negate().lessThan(nearCut)), () => { Discard(); });
            return color;
        })();
    };
}

/* equipment-spotlight-visual.mjs attachEquipmentReceiverDepth. */
function equipmentReceiver(nodeMaterial, { placeholderDepth, material }) {
    const depthTexture = screenDepthNode(placeholderDepth,
        (source) => harvestOf(source).uReceiverSceneDepth?.value, material);
    nodeMaterial.opacityNode = Fn(() => {
        const sceneDepth = depthTexture.x.toVar();
        If(harvestedUniform('uReceiverDepthValid', 'float').lessThan(0.5), () => { Discard(); });
        If(sceneDepth.greaterThanEqual(1), () => { Discard(); });
        const view = getViewPosition(screenUV, sceneDepth, harvestedUniform('uReceiverProjectionInverse', 'mat4'));
        const separation = abs(view.z.negate().sub(positionView.z.negate()));
        return materialOpacity.mul(float(1).sub(smoothstep(0.02, 0.08, separation)));
    })();
    nodeMaterial.alphaTestNode = float(0.0005);
}

/* equipment-virtual-fixtures.mjs orbHaloShader (ORB_HALO_FALLOFF 2.5). */
function equipmentOrbHalo(nodeMaterial) {
    // GLSL's normalMatrix is view space; three's modelNormalMatrix is world.
    const view = varying(positionView.negate(), 'vOrbHaloView');
    nodeMaterial.opacityNode = materialOpacity.mul(pow(clamp(dot(normalViewGeometry, normalize(view)), 0, 1), 2.5));
}

// ---- ShaderMaterial ports (a NodeMaterial built for code generation) -------

/* equipment-spotlight-visual.mjs createEquipmentSpotlightVisual, the volume. */
function equipmentBeam(nodeMaterial, { placeholderDepth, material }) {
    const radius = shaderUniform('uRadius', 'float'), beamLength = shaderUniform('uLength', 'float');
    const beamPosition = varying(positionLocal.mul(vec3(radius, beamLength, radius)), 'vBeamPosition');
    const depthTexture = screenDepthNode(placeholderDepth, (source) => source.uniforms?.uSceneDepth.value, material);
    nodeMaterial.fragmentNode = Fn(() => {
        const sceneDepth = depthTexture.x.toVar();
        const level = shaderUniform('uLevel', 'float');
        If(level.lessThanEqual(0).or(shaderUniform('uSceneDepthValid', 'float').lessThan(0.5)), () => { Discard(); });
        const cameraLocal = shaderUniform('uCameraLocal', 'vec3');
        const ray = normalize(beamPosition.sub(cameraLocal)).toVar();
        const safeAxis = (value) => select(value.lessThan(0), max(abs(value), 0.00001).negate(), max(abs(value), 0.00001));
        const inverseRay = vec3(1).div(vec3(safeAxis(ray.x), safeAxis(ray.y), safeAxis(ray.z)));
        const lo = vec3(radius.negate(), beamLength.mul(-0.5), radius.negate()).sub(cameraLocal).mul(inverseRay);
        const hi = vec3(radius, beamLength.mul(0.5), radius).sub(cameraLocal).mul(inverseRay);
        const nearBounds = min(lo, hi), farBounds = max(lo, hi);
        const entry = max(0, max(nearBounds.x, max(nearBounds.y, nearBounds.z))).toVar();
        const exit = min(length(beamPosition.sub(cameraLocal)), min(farBounds.x, min(farBounds.y, farBounds.z))).toVar();
        If(sceneDepth.lessThan(1), () => {
            const view = getViewPosition(screenUV, sceneDepth, shaderUniform('uProjectionInverse', 'mat4'));
            const surface = shaderUniform('uViewToLocal', 'mat4').mul(vec4(view, 1)).xyz;
            exit.assign(min(exit, dot(surface.sub(cameraLocal), ray)));
        });
        If(exit.lessThanEqual(entry), () => { Discard(); });
        const steps = 32;
        const stepLength = exit.sub(entry).div(steps);
        const opticalDepth = float(0).toVar();
        const tanAngle = shaderUniform('uTanAngle', 'float');
        const beamToRoom = shaderUniform('uBeamToRoom', 'mat4');
        const planes = shaderUniform('uRoomPlanes', 'vec4');
        const planeCount = shaderUniform('uRoomPlaneCount', 'int');
        Loop(steps, ({ i }) => {
            const distanceToEye = entry.add(float(i).add(0.5).mul(stepLength));
            const point = cameraLocal.add(ray.mul(distanceToEye)).toVar();
            const roomPoint = beamToRoom.mul(vec4(point, 1)).xyz.toVar();
            const roomKeep = float(1).toVar();
            Loop(33, ({ i: j }) => {
                If(j.greaterThanEqual(planeCount), () => { Break(); });
                const plane = planes.element(j);
                roomKeep.mulAssign(step(plane.w.sub(0.001), dot(plane.xyz, roomPoint)));
            });
            const axial = beamLength.mul(0.5).sub(point.y);
            const coneRadius = max(0.012, axial.mul(tanAngle));
            const radial = length(point.xz).div(coneRadius);
            const penumbra = float(1).sub(smoothstep(0.48, 1.0, radial));
            const endFade = float(1).sub(smoothstep(beamLength.mul(0.5), beamLength, axial));
            const sourceFade = smoothstep(0.015, 0.10, axial);
            const inside = step(0, axial).mul(step(axial, beamLength)).mul(roomKeep);
            const falloff = float(1).div(axial.mul(axial).mul(0.42).add(1));
            const nearFade = smoothstep(0.02, 0.20, distanceToEye);
            opticalDepth.addAssign(penumbra.mul(penumbra).mul(endFade).mul(sourceFade).mul(inside)
                .mul(falloff).mul(nearFade).mul(stepLength));
        });
        const forward = pow(max(0, dot(ray.negate(), vec3(0, -1, 0))), 4);
        const alpha = min(float(1).sub(exp(opticalDepth.negate().mul(1.65))).mul(1.35).mul(forward.mul(0.35).add(0.65)), 0.32)
            .mul(level).toVar();
        If(alpha.lessThan(0.0005), () => { Discard(); });
        return displayEncode(vec4(shaderUniform('uLightColor', 'color'), alpha));
    })();
}

/* equipment-spotlight-visual.mjs, the source glow. */
function equipmentGlow(nodeMaterial) {
    nodeMaterial.fragmentNode = Fn(() => {
        const r = length(uv().sub(0.5)).mul(2);
        const a = exp(r.mul(r).mul(-6)).mul(float(1).sub(smoothstep(0.6, 1, r))).mul(shaderUniform('uLevel', 'float'));
        return displayEncode(vec4(mix(shaderUniform('uColor', 'color'), vec3(1), 0.25), a.mul(0.6)));
    })();
}

/* apartment-directional-presentation.mjs, the polar fill. Raw output, as in GLSL. */
function directionalFill(nodeMaterial) {
    const t = varying(attribute('t', 'float'), 'vT');
    const tint = varying(attribute('zoneTint', 'vec3'), 'vTint');
    const a = pow(float(1).sub(t), 1.8).mul(0.85).add(0.04).add(smoothstep(0.8, 1.0, t).mul(0.3));
    nodeMaterial.fragmentNode = vec4(tint.mul(a).mul(0.7), 1.0);
}

const ON_BEFORE_COMPILE_BUILDERS = Object.freeze({
    'scan-display-floor-0': scanDisplay(false),
    'scan-display-floor-1': scanDisplay(true),
    'equipment-receiver': equipmentReceiver,
    'equipment-orb-halo': equipmentOrbHalo,
});
const SHADER_BUILDERS = Object.freeze({
    'equipment-beam': equipmentBeam,
    'equipment-glow': equipmentGlow,
    'directional-fill': directionalFill,
});
const SHADER_PORT_BY_FINGERPRINT = new Map(Object.keys(SHADER_BUILDERS)
    .map((id) => [PORT_FINGERPRINTS[id], id]));
// What the code-generation material borrows from the original. Pipeline
// state is read from the original material itself.
const SHADER_FLAGS = ['side', 'transparent', 'blending', 'depthTest', 'depthWrite', 'toneMapped', 'forceSinglePass'];

/**
 * Hooks renderer.library so ported sites build from their TSL twin.
 * Returns the port readback and an uninstall.
 */
export function installMaterialPorts(renderer, { warn = (...args) => console.warn(...args) } = {}) {
    const library = renderer.library;
    const fromMaterial = library.fromMaterial;
    const counts = new Map();
    const problems = new Map();
    const placeholderTarget = new THREE.RenderTarget(1, 1, { depthBuffer: true });
    placeholderTarget.depthTexture = new THREE.DepthTexture(1, 1, THREE.UnsignedIntType);
    const options = { placeholderDepth: placeholderTarget.depthTexture };
    // Give the placeholder depth texture a GPU resource once.
    const previous = renderer.getRenderTarget();
    renderer.setRenderTarget(placeholderTarget);
    renderer.clear();
    renderer.setRenderTarget(previous);

    const count = (id) => counts.set(id, (counts.get(id) || 0) + 1);
    function problem(key, detail) {
        if (!problems.has(key)) {
            problems.set(key, detail);
            warn(`[home-3d] WebGPU: ${detail.status} material (${key}); drawn without its GLSL effect`);
        }
    }
    function build(material) {
        if (!material || material.isNodeMaterial) return fromMaterial.call(library, material);
        if (material.isShaderMaterial) {
            const fingerprint = shaderMaterialFingerprint(material);
            const id = SHADER_PORT_BY_FINGERPRINT.get(fingerprint);
            if (!id) {
                problem(`shader:${fingerprint}`, { status: 'unported', kind: 'ShaderMaterial', fingerprint, name: material.name || null });
                return fromMaterial.call(library, material);
            }
            const nodeMaterial = new THREE.NodeMaterial();
            for (const key of SHADER_FLAGS) nodeMaterial[key] = material[key];
            SHADER_BUILDERS[id](nodeMaterial, { ...options, material });
            count(id);
            return refreshEveryDraw(nodeMaterial);
        }
        if (!Object.hasOwn(material, 'onBeforeCompile')) return fromMaterial.call(library, material);
        const cacheKey = String(material.customProgramCacheKey());
        const id = ON_BEFORE_COMPILE_PORTS[cacheKey];
        const nodeMaterial = fromMaterial.call(library, material);
        if (!id) {
            problem(`onBeforeCompile:${cacheKey.slice(0, 64)}`, { status: 'unported', kind: 'onBeforeCompile', cacheKey: cacheKey.slice(0, 64) });
            return nodeMaterial;
        }
        const probe = probeOnBeforeCompile(material);
        if (probe.fingerprint !== PORT_FINGERPRINTS[id]) {
            problem(`onBeforeCompile:${cacheKey}`, { status: 'drifted', kind: 'onBeforeCompile', port: id,
                expected: PORT_FINGERPRINTS[id], actual: probe.fingerprint });
            return nodeMaterial;
        }
        ON_BEFORE_COMPILE_BUILDERS[id](nodeMaterial, { ...options, material });
        count(id);
        return refreshEveryDraw(nodeMaterial);
    }
    library.fromMaterial = build;
    return {
        readback: () => ({
            builds: Object.fromEntries(counts),
            problems: [...problems.values(), ...drifted.values()],
        }),
        dispose() {
            if (library.fromMaterial === build) library.fromMaterial = fromMaterial;
            placeholderTarget.dispose();
        },
    };
}

export { PORT_FINGERPRINTS, shaderMaterialFingerprint };
