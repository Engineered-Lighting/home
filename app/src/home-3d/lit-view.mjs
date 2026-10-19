/* home-3d/lit-view.mjs — Tier-0 Lit (APT-V01; SPEC Lit view; LIGHT-AND-BODY-RESEARCH Tier 0).
 *
 * Relights the existing scan mesh with simulated light from the virtual
 * fixtures, using built-in three.js parts only: SpotLights, built-in PCF
 * shadow maps and MeshStandardMaterial, with no custom shader, so the view
 * ports to WebGPURenderer unchanged. The light model (assumed lumens to
 * candela by beam angle, zone directions, colours, the fixed night exposure)
 * lives in the renderer-independent lit-model.mjs.
 *
 * Spot shadows are crisp and Down shadows soft (1024 PCF); each radial zone
 * is its own unshadowed lobe. Light intensity is candela x the fixed night
 * exposure, and the renderer's tone mapping is left untouched, so the cloud,
 * the splat, the orbs and every overlay keep their exact unlit look.
 *
 * This is a modeled view, never photometry: the lumen ratings are SPEC's
 * assumptions and are disclosed with the view, and materials are assumed
 * (the scan's own colours stand in for albedo).
 *
 * Reversible and confined to the mesh: Lit swaps the mesh materials for lit
 * copies and restores the originals on exit. Shadow maps use the default
 * depth material, so the camera's dollhouse cutaway never changes world
 * shadowing. The scene is static, so shadow maps re-render only on change.
 */
import * as THREE from 'three';

const Model = await import(`./lit-model.mjs${new URL(import.meta.url).search}`);
export const { LIT_ASSUMED_LUMENS, LIT_ASSUMPTION_SOURCE, LIT_DISCLOSURE, LIT_DISCLOSURE_DETAIL, LIT_EXPOSURE, LIT_TARGET_FPS,
    candelaFromLumens } = Model;
export const LIT_SHADOW_MAP_SIZE = 1024;
// Below LIT_TARGET_FPS for this many consecutive 500 ms stats, Lit declares
// a reduced quality instead of silently dropping frames.
const LIT_SLOW_SAMPLES = 4;
// Scan colours carry the capture's own lighting; scale them toward an albedo.
const ASSUMED_ALBEDO_SCALE = 0.8;
// A faint night fill so the unlit room stays navigable. Not a light source.
const NIGHT_FILL = 0.35;

export function kelvinColor(kelvin, target = new THREE.Color()) {
    const [r, g, b] = Model.kelvinRgb(kelvin);
    return target.setRGB(r, g, b, THREE.SRGBColorSpace);
}

/* The model's input for one virtual fixture, read from the fixture's own
 * exact metadata (never the eased drawing), in its parent's frame. */
export function fixtureModelInput(fixtureRoot) {
    if (!fixtureRoot?.isObject3D || !fixtureRoot.parent) return null;
    fixtureRoot.updateWorldMatrix(true, true);
    const local = node => fixtureRoot.parent.worldToLocal(node.getWorldPosition(new THREE.Vector3())).toArray();
    const robotic = fixtureRoot.getObjectByName('robotic-spotlight')?.userData;
    const down = fixtureRoot.getObjectByName('downward-zone'), emitter = fixtureRoot.getObjectByName('downward-emitter');
    const orb = fixtureRoot.getObjectByName('fixture-orb');
    const zones = [];
    for (let number = 1; number <= 6; number++) {
        const channel = fixtureRoot.getObjectByName('radial-zone-' + number)?.userData?.channel;
        if (channel) zones.push({ number, azimuth_deg: channel.azimuth_deg, channel });
    }
    return {
        fixtureId: fixtureRoot.userData?.fixture_id ?? null,
        spot: robotic && Array.isArray(robotic.beam_origin_m) && Array.isArray(robotic.direction_unit)
            ? { origin: robotic.beam_origin_m, direction: robotic.direction_unit, channel: robotic.channel } : null,
        down: down && emitter ? { origin: local(emitter), channel: down.userData.channel } : null,
        center: orb ? local(orb) : fixtureRoot.position.toArray(),
        zones,
    };
}

/* The world-space lights of one fixture: the model's lights, moved from the
 * fixture's parent frame into the world. */
export function fixtureWorldLights(fixtureRoot, { spotFwhmDeg = 20 } = {}) {
    const input = fixtureModelInput(fixtureRoot);
    if (!input) return [];
    const parent = fixtureRoot.parent;
    return Model.fixtureLights(input, { spotFwhmDeg }).map(light => ({
        ...light, fixtureId: input.fixtureId,
        origin: parent.localToWorld(new THREE.Vector3(...light.origin)),
        direction: new THREE.Vector3(...light.direction).transformDirection(parent.matrixWorld),
    }));
}

export function createLitView({ scene, renderer, getMesh, getFixtureRoots, spotFwhmDeg = 20 } = {}) {
    if (!scene?.isScene || !renderer?.shadowMap || typeof getMesh !== 'function' || typeof getFixtureRoots !== 'function')
        throw new TypeError('lit_view_dependencies_required');
    const group = new THREE.Group();
    group.name = 'lit-view-lights';
    group.userData = { presentation: 'modeled_lit_view', photometry: 'assumed_not_measured', physical_commands: false };
    const fill = new THREE.HemisphereLight(0x9aa6b8, 0x2a2622, NIGHT_FILL);
    fill.name = 'lit-night-fill';
    group.add(fill);
    let active = false, quality = 'full', slow = 0, swapped = [], lights = [], signature = '', saved = null, mesh = null;

    function litMaterialFor(original) {
        const source = Array.isArray(original) ? original.find(Boolean) : original;
        const tinted = !!(source?.map || source?.vertexColors);
        const material = new THREE.MeshStandardMaterial({
            map: source?.map || null, vertexColors: !!source?.vertexColors,
            color: tinted ? new THREE.Color(ASSUMED_ALBEDO_SCALE, ASSUMED_ALBEDO_SCALE, ASSUMED_ALBEDO_SCALE) : 0x9ea4aa,
            roughness: 0.88, metalness: 0, side: source?.side ?? THREE.FrontSide,
        });
        material.name = 'lit-view-assumed-material';
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
        light.userData = { lit_source: source.kind, assumed_lumens: LIT_ASSUMED_LUMENS[source.kind], measured: false,
            shape: source.kind + (source.number || '') };
        if (source.shadow) {
            light.castShadow = true;
            light.shadow.mapSize.set(LIT_SHADOW_MAP_SIZE, LIT_SHADOW_MAP_SIZE);
            light.shadow.camera.near = 0.05; light.shadow.camera.far = 14;
            light.shadow.bias = -0.0004; light.shadow.normalBias = 0.02;
            light.shadow.radius = Model.SHADOW_RADIUS[source.shadow];
        }
        group.add(light, light.target);
        return light;
    }
    function rebuild(sources) {
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
        const sources = (getFixtureRoots() || []).flatMap(root => fixtureWorldLights(root, { spotFwhmDeg }));
        const shape = sources.map(source => source.kind + (source.number || '')).join(',');
        if (shape !== lights.map(light => light.userData.shape).join(',')) { rebuild(sources); applyQuality(); }
        const key = sources.map(source => [...source.origin.toArray(), ...source.direction.toArray(), source.candela, source.kelvin]
            .map(value => (typeof value === 'number' ? value.toFixed(5) : String(value))).join(':')).join('|') + '|' + quality;
        if (key === signature) return false;
        signature = key;
        sources.forEach((source, index) => {
            const light = lights[index];
            light.position.copy(source.origin);
            light.target.position.copy(source.origin).addScaledVector(source.direction, 2);
            light.target.updateMatrixWorld(true);
            light.intensity = quality === 'reduced' && source.kind === 'zone' ? 0 : source.intensity;
            kelvinColor(source.kelvin, light.color);
            light.userData.candela = source.candela;
            light.userData.fixture_id = source.fixtureId;
        });
        renderer.shadowMap.needsUpdate = true;
        return true;
    }

    function enable() {
        if (active) return state();
        saved = { enabled: renderer.shadowMap.enabled, type: renderer.shadowMap.type, autoUpdate: renderer.shadowMap.autoUpdate,
            ownBeforeRender: Object.hasOwn(scene, 'onBeforeRender'), onBeforeRender: scene.onBeforeRender };
        renderer.shadowMap.enabled = true;
        renderer.shadowMap.type = THREE.PCFShadowMap;
        renderer.shadowMap.autoUpdate = false;
        scene.add(group);
        active = true; quality = 'full'; slow = 0; signature = '';
        const previous = saved.onBeforeRender;
        // Sync on the render that uses it, without touching the engine loop.
        scene.onBeforeRender = function litBeforeRender(...args) {
            try { sync(); } catch (_) { /* the frame still renders */ }
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
        lights = []; signature = '';
        scene.remove(group);
        renderer.shadowMap.enabled = saved.enabled;
        renderer.shadowMap.type = saved.type;
        renderer.shadowMap.autoUpdate = saved.autoUpdate;
        if (saved.ownBeforeRender) scene.onBeforeRender = saved.onBeforeRender; else delete scene.onBeforeRender;
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
            shadowLights: lights.filter(light => light.castShadow).length,
            meshSwapped: swapped.length, exposure: LIT_EXPOSURE,
            assumedLumens: LIT_ASSUMED_LUMENS, assumptionSource: LIT_ASSUMPTION_SOURCE, measured: false,
            disclosure: LIT_DISCLOSURE, disclosureDetail: LIT_DISCLOSURE_DETAIL });
    }
    return { enable, disable, sync, observeFrameRate, state, group };
}
