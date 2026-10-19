/* home-3d/lit-view.mjs — Tier-0 Lit (APT-V01; SPEC Lit view; LIGHT-AND-BODY-RESEARCH Tier 0).
 *
 * Relights the existing scan mesh with simulated light from the virtual
 * fixtures: each spot and downlight is a physically based spot light whose
 * intensity in candela comes from an ASSUMED lumen rating spread over its beam
 * angle, I = flux / (2 pi (1 - cos theta)), under a fixed night exposure with
 * ACES tone mapping. Spot shadows are crisp and down shadows soft (1024 PCF);
 * each radial zone is its own unshadowed lobe.
 *
 * This is a modeled view, never photometry. The lumen ratings are SPEC's
 * assumptions, not measured or profile values, and are disclosed with the
 * view. Materials are assumed: the scan's own colours stand in for albedo.
 *
 * Reversible and confined to the mesh: Lit swaps the mesh materials for lit
 * copies that keep the display material's shader hooks (dollhouse cutaway,
 * near cut), and restores the originals on exit. Tone mapping lives inside
 * the lit materials only, so the cloud, the splat, the orbs and every overlay
 * keep their exact unlit look. Shadow maps come from the default depth
 * material, so the camera's dollhouse cutaway never changes world shadowing.
 * The scene is static, so shadow maps re-render only when a light changes.
 *
 * One bounce (APT-V02; SPEC Lit view; prototype scene.js VPL block): the
 * eight strongest lit patches (each spot's target, each Down pool, each lit
 * zone's wall) become hemispherical bounce lights carrying the reflected
 * flux, flux x level x 0.8 (zones 0.7) x an assumed albedo, placed 0.25 m off
 * the patch with a 4.5 m range. Bounce lights cast no shadows, so bounce can
 * leak up to 4.5 m through a wall; the view states this limit.
 */
import * as THREE from 'three';

// SPEC.md > Lit view: assumed lumen ratings. Not measured; never presented as such.
export const LIT_ASSUMED_LUMENS = Object.freeze({ spot: 450, down: 350, zone: 120 });
export const LIT_ASSUMPTION_SOURCE = 'SPEC Lit view assumed lumen ratings';
export const LIT_DISCLOSURE = 'Simulated · not photometric · assumed output';
export const LIT_DISCLOSURE_DETAIL = 'Assumed output, not measured: spot 450 lm, down 350 lm, each radial zone 120 lm. '
    + 'Fixed night exposure. Materials are assumed from the scan colours. One bounce from the eight strongest lit patches, '
    + 'with assumed albedos.';
// SPEC Lit view: stated with the view, not only in a tooltip.
export const LIT_BOUNCE_LIMIT = 'One bounce · casts no shadows · can leak up to 4.5 m through walls';
export const LIT_BOUNCE_COUNT = 8;
export const LIT_BOUNCE_RANGE_M = 4.5;
// Prototype VPL albedos (assumed): spot target flat 0.35 or vertical 0.6,
// Down pool 0.35, zone wall 0.65, any other zone patch 0.4. A zone facing an
// opening finds no surface in range, so it adds nothing; the opening and
// 'Toward walls' classes wait on APT-Z04 facing kinds.
export const LIT_BOUNCE_ALBEDO = Object.freeze({ spotFlat: 0.35, spotVertical: 0.6, down: 0.35, zoneWall: 0.65, zoneOther: 0.4 });
const BOUNCE_TINT = new THREE.Color(1, 0.96, 0.9);
// Prototype scene.js _litApply: ACES at a fixed exposure of 0.038.
export const LIT_EXPOSURE = 0.038;
export const LIT_SHADOW_MAP_SIZE = 1024;
// Below this frame rate for LIT_SLOW_SAMPLES consecutive 500 ms stats, Lit
// declares a reduced quality instead of silently dropping frames.
export const LIT_TARGET_FPS = 30;
const LIT_SLOW_SAMPLES = 4;
// Prototype beam shapes: the spot cone half-angle is 0.8 x its FWHM in
// degrees with a 0.55 penumbra; the down ring and each radial zone are
// soft-edged lobes of 1.0 and 0.95 rad.
const SPOT_HALF_ANGLE_PER_FWHM_DEG = 0.8;
const DOWN_HALF_ANGLE = 1.0, ZONE_HALF_ANGLE = 0.95;
// Scan colours carry the capture's own lighting; scale them toward an albedo.
const ASSUMED_ALBEDO_SCALE = 0.8;
// A faint night fill so the unlit room stays navigable. Not a light source.
const NIGHT_FILL = 0.35;

/* Candela from an assumed lumen rating spread evenly over a cone of half-angle theta. */
export function candelaFromLumens(lumens, halfAngleRad) {
    const solid = 2 * Math.PI * (1 - Math.cos(halfAngleRad));
    return solid > 0 && Number.isFinite(lumens) ? lumens / solid : 0;
}

/* Display colour only (Tanner Helland's blackbody fit, as the fixtures use):
 * makes warm and cool settings legible, not a measured spectrum. */
export function kelvinColor(kelvin, target = new THREE.Color()) {
    const t = (Number.isFinite(kelvin) ? kelvin : 3000) / 100;
    const red = t <= 66 ? 255 : 329.698727446 * Math.pow(t - 60, -0.1332047592);
    const green = t <= 66 ? 99.4708025861 * Math.log(t) - 161.1195681661 : 288.1221695283 * Math.pow(t - 60, -0.0755148492);
    const blue = t >= 66 ? 255 : t <= 19 ? 0 : 138.5177312231 * Math.log(t - 10) - 305.0447927307;
    const c = v => Math.min(255, Math.max(0, v)) / 255;
    return target.setRGB(c(red), c(green), c(blue), THREE.SRGBColorSpace);
}

const LIT_TONE_MAPPING = `
uniform float uLitExposure;
vec3 litRRTAndODTFit(vec3 v) {
    vec3 a = v * (v + 0.0245786) - 0.000090537;
    vec3 b = v * (0.983729 * v + 0.4329510) + 0.238081;
    return a / b;
}
vec3 litACES(vec3 color) {
    const mat3 inputMat = mat3(vec3(0.59719, 0.07600, 0.02840), vec3(0.35458, 0.90834, 0.13383), vec3(0.04823, 0.01566, 0.83777));
    const mat3 outputMat = mat3(vec3(1.60475, -0.10208, -0.00327), vec3(-0.53108, 1.10813, -0.07276), vec3(-0.07367, -0.00605, 1.07602));
    color *= uLitExposure / 0.6;
    color = outputMat * litRRTAndODTFit(inputMat * color);
    return clamp(color, 0.0, 1.0);
}`;

/* The light sources one virtual fixture contributes, read from the fixture's
 * own exact metadata (never the eased drawing), in world space. */
export function fixtureLightSources(fixtureRoot, { spotFwhmDeg = 20 } = {}) {
    if (!fixtureRoot?.isObject3D) return null;
    fixtureRoot.updateWorldMatrix(true, true);
    const parent = fixtureRoot.parent;
    const toWorld = point => (parent ? parent.localToWorld(new THREE.Vector3(...point)) : new THREE.Vector3(...point));
    const dirToWorld = direction => {
        const v = new THREE.Vector3(...direction);
        return parent ? v.transformDirection(parent.matrixWorld) : v.normalize();
    };
    const level = channel => (channel && channel.on && channel.brightness > 0 ? channel.brightness : 0);
    const sources = [];
    const robotic = fixtureRoot.getObjectByName('robotic-spotlight')?.userData;
    if (robotic && Array.isArray(robotic.beam_origin_m) && Array.isArray(robotic.direction_unit)) {
        const half = (Math.max(1, Number(spotFwhmDeg) || 20) * SPOT_HALF_ANGLE_PER_FWHM_DEG) * Math.PI / 180;
        const origin = toWorld(robotic.beam_origin_m), direction = dirToWorld(robotic.direction_unit);
        sources.push({ kind: 'spot', origin, direction, halfAngle: half, penumbra: 0.55, shadow: 'crisp',
            level: level(robotic.channel), kelvin: robotic.channel?.color_temp_kelvin,
            candela: candelaFromLumens(LIT_ASSUMED_LUMENS.spot, half) * level(robotic.channel),
            endpoint: Array.isArray(robotic.endpoint_m) ? toWorld(robotic.endpoint_m) : null });
    }
    const down = fixtureRoot.getObjectByName('downward-zone');
    const downEmitter = fixtureRoot.getObjectByName('downward-emitter');
    if (down && downEmitter) {
        const origin = downEmitter.getWorldPosition(new THREE.Vector3());
        const direction = dirToWorld([0, 0, -1]);
        sources.push({ kind: 'down', origin, direction, halfAngle: DOWN_HALF_ANGLE, penumbra: 1, shadow: 'soft',
            level: level(down.userData.channel), kelvin: down.userData.channel?.color_temp_kelvin,
            candela: candelaFromLumens(LIT_ASSUMED_LUMENS.down, DOWN_HALF_ANGLE) * level(down.userData.channel) });
    }
    const orb = fixtureRoot.getObjectByName('fixture-orb');
    const center = orb ? orb.getWorldPosition(new THREE.Vector3()) : fixtureRoot.getWorldPosition(new THREE.Vector3());
    for (let number = 1; number <= 6; number++) {
        const zone = fixtureRoot.getObjectByName('radial-zone-' + number);
        const channel = zone?.userData?.channel;
        if (!zone || !channel) continue;
        const azimuth = (Number(channel.azimuth_deg) || 0) * Math.PI / 180;
        // Each lobe aims outward and slightly down, as in the prototype.
        const local = new THREE.Vector3(Math.cos(azimuth) * 2, Math.sin(azimuth) * 2, -0.35).normalize();
        const direction = dirToWorld(local.toArray());
        sources.push({ kind: 'zone', number, origin: center.clone().addScaledVector(direction, 0.05), direction,
            halfAngle: ZONE_HALF_ANGLE, penumbra: 1, shadow: null, level: level(channel), kelvin: channel.color_temp_kelvin,
            candela: candelaFromLumens(LIT_ASSUMED_LUMENS.zone, ZONE_HALF_ANGLE) * level(channel) });
    }
    const fixtureId = fixtureRoot.userData?.fixture_id ?? null;
    for (const source of sources) source.fixtureId = fixtureId;
    return sources;
}

/* The bounce patches for a set of light sources, strongest first, at most
 * LIT_BOUNCE_COUNT. `castRay(origin, direction)` returns the first surface
 * as { point, normal } in world space (normal facing the light) or null. */
export function bouncePatches(sources, castRay, { up = new THREE.Vector3(0, 1, 0) } = {}) {
    const patches = [];
    for (const source of sources || []) {
        if (!(source.level > 0)) continue;
        const hit = castRay(source.origin, source.direction);
        if (!hit) continue;
        const vertical = Math.abs(hit.normal.dot(up)) <= 0.35, flat = Math.abs(hit.normal.dot(up)) > 0.8;
        let flux, albedo;
        if (source.kind === 'spot') { flux = LIT_ASSUMED_LUMENS.spot * source.level * 0.8; albedo = flat ? LIT_BOUNCE_ALBEDO.spotFlat : LIT_BOUNCE_ALBEDO.spotVertical; }
        else if (source.kind === 'down') { flux = LIT_ASSUMED_LUMENS.down * source.level * 0.8; albedo = LIT_BOUNCE_ALBEDO.down; }
        else { flux = LIT_ASSUMED_LUMENS.zone * source.level * 0.7; albedo = vertical ? LIT_BOUNCE_ALBEDO.zoneWall : LIT_BOUNCE_ALBEDO.zoneOther; }
        const reflected = flux * albedo;
        if (!(reflected > 1)) continue;
        patches.push({ kind: source.kind, number: source.number || null, fixtureId: source.fixtureId ?? null,
            point: hit.point.clone(), normal: hit.normal.clone().normalize(), reflected, albedo, kelvin: source.kelvin });
    }
    return patches.sort((a, b) => b.reflected - a.reflected).slice(0, LIT_BOUNCE_COUNT);
}

export function createLitView({ scene, renderer, getMesh, getFixtureRoots, getCollision = () => null, spotFwhmDeg = 20 } = {}) {
    if (!scene?.isScene || !renderer?.shadowMap || typeof getMesh !== 'function' || typeof getFixtureRoots !== 'function')
        throw new TypeError('lit_view_dependencies_required');
    const group = new THREE.Group();
    group.name = 'lit-view-lights';
    group.userData = { presentation: 'modeled_lit_view', photometry: 'assumed_not_measured', physical_commands: false };
    const fill = new THREE.HemisphereLight(0x9aa6b8, 0x2a2622, NIGHT_FILL);
    fill.name = 'lit-night-fill';
    group.add(fill);
    const exposure = { value: LIT_EXPOSURE };
    let active = false, quality = 'full', slow = 0, swapped = [], lights = [], signature = '', saved = null, mesh = null;
    // Bounce lights: a fixed set of eight, so a change never recompiles.
    const bounce = Array.from({ length: LIT_BOUNCE_COUNT }, (_, index) => {
        const light = new THREE.SpotLight(0xffffff, 0, LIT_BOUNCE_RANGE_M, 1.45, 1, 2);
        light.name = 'lit-bounce-' + (index + 1);
        light.userData = { lit_source: 'bounce', measured: false, casts_shadow: false };
        group.add(light, light.target);
        return light;
    });
    let bounceSources = 0;
    const raycaster = new THREE.Raycaster(), rayHits = new Map();
    // Rays against the decimated collision proxy, cached per ray: level and
    // colour changes reuse them, only a moved light casts again.
    function castRay(origin, direction) {
        const collision = getCollision();
        const target = collision || mesh;
        if (!target) return null;
        const key = [...origin.toArray(), ...direction.toArray()].map(value => value.toFixed(4)).join(',') + '|' + target.uuid;
        if (rayHits.has(key)) return rayHits.get(key);
        raycaster.set(origin, direction.clone().normalize()); raycaster.near = 0.02; raycaster.far = 30;
        const first = raycaster.intersectObject(target, true).find(hit => hit.point && hit.face);
        let hit = null;
        if (first) {
            const normal = first.face.normal.clone().transformDirection(first.object.matrixWorld);
            if (normal.dot(direction) > 0) normal.negate();
            hit = { point: first.point.clone(), normal };
        }
        if (rayHits.size > 256) rayHits.clear();
        rayHits.set(key, hit);
        return hit;
    }

    function litMaterialFor(original) {
        const source = Array.isArray(original) ? original.find(Boolean) : original;
        const material = new THREE.MeshStandardMaterial({
            map: source?.map || null,
            vertexColors: !!source?.vertexColors,
            color: source?.map || source?.vertexColors ? new THREE.Color(ASSUMED_ALBEDO_SCALE, ASSUMED_ALBEDO_SCALE, ASSUMED_ALBEDO_SCALE) : 0x9ea4aa,
            roughness: 0.88, metalness: 0, side: source?.side ?? THREE.FrontSide,
        });
        material.name = 'lit-view-assumed-material';
        const hook = source?.onBeforeCompile;
        material.onBeforeCompile = (shader, rendererArg) => {
            if (typeof hook === 'function' && hook !== THREE.Material.prototype.onBeforeCompile) hook.call(material, shader, rendererArg);
            shader.uniforms.uLitExposure = exposure;
            shader.fragmentShader = shader.fragmentShader
                .replace('#include <common>', '#include <common>\n' + LIT_TONE_MAPPING)
                .replace('#include <tonemapping_fragment>', 'gl_FragColor.rgb = litACES(gl_FragColor.rgb);');
        };
        const key = typeof source?.customProgramCacheKey === 'function' ? source.customProgramCacheKey() : '';
        material.customProgramCacheKey = () => 'lit-view-1|' + key;
        return material;
    }

    function swapMaterials(next) {
        restoreMaterials();
        mesh = next;
        if (!mesh) return;
        mesh.traverse(node => {
            if (!node.isMesh) return;
            // The unlit display never needed normals, and scan exports may not
            // carry them. Lit derives them for its lifetime only.
            const addedNormals = !node.geometry.attributes.normal;
            if (addedNormals) node.geometry.computeVertexNormals();
            swapped.push({ node, material: node.material, castShadow: node.castShadow, receiveShadow: node.receiveShadow, addedNormals });
            node.material = litMaterialFor(node.material);
            node.castShadow = true; node.receiveShadow = true;
        });
    }
    function restoreMaterials() {
        for (const entry of swapped) {
            entry.node.material.dispose?.();
            entry.node.material = entry.material;
            entry.node.castShadow = entry.castShadow; entry.node.receiveShadow = entry.receiveShadow;
            if (entry.addedNormals) entry.node.geometry.deleteAttribute('normal');
        }
        swapped = []; mesh = null;
    }
    function makeLight(source) {
        const light = new THREE.SpotLight(0xffffff, 0, 0, source.halfAngle, source.penumbra, 2);
        light.name = 'lit-' + source.kind + (source.number ? '-' + source.number : '');
        light.userData = { lit_source: source.kind, assumed_lumens: LIT_ASSUMED_LUMENS[source.kind], measured: false };
        if (source.shadow) {
            light.castShadow = true;
            light.shadow.mapSize.set(LIT_SHADOW_MAP_SIZE, LIT_SHADOW_MAP_SIZE);
            light.shadow.camera.near = 0.05; light.shadow.camera.far = 14;
            light.shadow.bias = -0.0004; light.shadow.normalBias = 0.02;
            light.shadow.radius = source.shadow === 'crisp' ? 2.5 : 6;
        }
        group.add(light, light.target);
        return light;
    }
    function rebuild(sources) {
        rayHits.clear();
        for (const light of lights) { light.shadow?.map?.dispose?.(); group.remove(light, light.target); light.dispose?.(); }
        lights = sources.map(makeLight);
    }
    function applyQuality() {
        const size = quality === 'reduced' ? LIT_SHADOW_MAP_SIZE / 2 : LIT_SHADOW_MAP_SIZE;
        for (const light of lights) {
            if (!light.castShadow || light.shadow.mapSize.x === size) continue;
            light.shadow.mapSize.set(size, size); light.shadow.map?.dispose?.(); light.shadow.map = null;
        }
        renderer.shadowMap.needsUpdate = true;
    }

    /* Follow the fixtures' current exact state. Cheap when nothing changed. */
    function sync() {
        if (!active) return false;
        const currentMesh = getMesh();
        if (currentMesh !== mesh) { swapMaterials(currentMesh); renderer.shadowMap.needsUpdate = true; }
        const sources = (getFixtureRoots() || []).flatMap(root => fixtureLightSources(root, { spotFwhmDeg }) || []);
        const shape = sources.map(source => source.kind + (source.number || '')).join(',');
        if (shape !== lights.map(light => light.userData.shape).join(',')) {
            rebuild(sources);
            lights.forEach((light, index) => { light.userData.shape = sources[index].kind + (sources[index].number || ''); });
            applyQuality();
        }
        const key = sources.map(source => [source.origin.toArray(), source.direction.toArray(), source.candela, source.kelvin]
            .flat().map(value => (typeof value === 'number' ? value.toFixed(5) : String(value))).join(':')).join('|') + '|' + quality;
        if (key === signature) return false;
        signature = key;
        sources.forEach((source, index) => {
            const light = lights[index];
            light.position.copy(source.origin);
            light.target.position.copy(source.origin).addScaledVector(source.direction, 2);
            light.target.updateMatrixWorld(true);
            light.intensity = quality === 'reduced' && source.kind === 'zone' ? 0 : source.candela;
            kelvinColor(source.kelvin, light.color);
            light.userData.candela = source.candela;
            light.userData.fixture_id = source.fixtureId;
        });
        const patches = bouncePatches(sources, castRay);
        bounceSources = patches.length;
        bounce.forEach((light, index) => {
            const patch = patches[index];
            if (!patch) { light.intensity = 0; light.userData.patch = null; return; }
            light.position.copy(patch.point).addScaledVector(patch.normal, 0.25);
            light.target.position.copy(light.position).add(patch.normal);
            light.target.updateMatrixWorld(true);
            kelvinColor(patch.kelvin, light.color).multiply(BOUNCE_TINT);
            light.intensity = (patch.reflected / Math.PI) * 0.9;
            light.userData.patch = { kind: patch.kind, reflected_lm: patch.reflected, albedo: patch.albedo, assumed: true };
        });
        renderer.shadowMap.needsUpdate = true;
        return true;
    }

    function enable() {
        if (active) return state();
        saved = { enabled: renderer.shadowMap.enabled, type: renderer.shadowMap.type, autoUpdate: renderer.shadowMap.autoUpdate,
            onBeforeRender: scene.onBeforeRender };
        renderer.shadowMap.enabled = true;
        renderer.shadowMap.type = THREE.PCFShadowMap;
        renderer.shadowMap.autoUpdate = false;
        scene.add(group);
        active = true; quality = 'full'; slow = 0; signature = '';
        const previous = saved.onBeforeRender;
        // Sync on the render that uses it, without touching the engine loop.
        scene.onBeforeRender = function litBeforeRender(...args) {
            try { sync(); } catch (_) { /* the frame still renders unlit-safe */ }
            return previous.apply(this, args);
        };
        sync();
        return state();
    }
    function disable() {
        if (!active) return state();
        active = false;
        restoreMaterials();
        for (const light of lights) { light.shadow?.map?.dispose?.(); light.dispose?.(); }
        group.remove(...lights.flatMap(light => [light, light.target]));
        lights = []; signature = ''; rayHits.clear(); bounceSources = 0;
        for (const light of bounce) light.intensity = 0;
        scene.remove(group);
        renderer.shadowMap.enabled = saved.enabled;
        renderer.shadowMap.type = saved.type;
        renderer.shadowMap.autoUpdate = saved.autoUpdate;
        scene.onBeforeRender = saved.onBeforeRender;
        saved = null;
        return state();
    }
    /* Declared degradation: a sustained frame rate under the target halves
     * the shadow maps and drops the radial lobes, and says so. */
    function observeFrameRate(fps) {
        if (!active || quality === 'reduced' || !Number.isFinite(fps)) return state();
        slow = fps < LIT_TARGET_FPS ? slow + 1 : 0;
        if (slow >= LIT_SLOW_SAMPLES) { quality = 'reduced'; applyQuality(); signature = ''; sync(); }
        return state();
    }
    function state() {
        return Object.freeze({ active, quality, lights: lights.length,
            bounceLights: bounce.length, bounceSources, bounceRangeM: LIT_BOUNCE_RANGE_M, bounceLimit: LIT_BOUNCE_LIMIT,
            shadowLights: lights.filter(light => light.castShadow).length,
            meshSwapped: swapped.length, exposure: LIT_EXPOSURE,
            assumedLumens: LIT_ASSUMED_LUMENS, assumptionSource: LIT_ASSUMPTION_SOURCE, measured: false,
            disclosure: LIT_DISCLOSURE, disclosureDetail: LIT_DISCLOSURE_DETAIL });
    }
    return { enable, disable, sync, observeFrameRate, state, group };
}
