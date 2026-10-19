/* home-3d/lit-view.mjs — Tier-0 Lit (APT-V01; SPEC Lit view; LIGHT-AND-BODY-RESEARCH Tier 0).
 *
 * Relights the existing scan mesh with simulated light from the virtual
 * fixtures, using built-in three.js parts only: SpotLights, built-in PCF
 * shadow maps and MeshStandardMaterial, with no custom shader, so the view
 * ports to WebGPURenderer unchanged. The light model (assumed lumens to
 * candela by beam angle, the assumed dimming curve, constant-luminance
 * colour, zone directions, the 160 ms ease, the fixed night exposure) lives
 * in the renderer-independent lit-model.mjs. Lights ease toward each set
 * level on the fixtures' 160 ms clock; the aim follows the exact metadata.
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
 *
 * One bounce (APT-V02) is temporary instant radiosity (see lit-model.mjs):
 * eight built-in SpotLights placed at the strongest lit patches, found by
 * rays against the collision proxy.
 */
import * as THREE from 'three';

const Model = await import(`./lit-model.mjs${new URL(import.meta.url).search}`);
export const { LIT_ASSUMED_LUMENS, LIT_ASSUMPTION_SOURCE, LIT_DISCLOSURE, LIT_DISCLOSURE_DETAIL, LIT_EXPOSURE, LIT_TARGET_FPS,
    LIT_BOUNCE_LIMIT, LIT_BOUNCE_COUNT, LIT_BOUNCE_RANGE_M, LIT_BOUNCE_ALBEDO, candelaFromLumens, bouncePatches, litDimmingFraction } = Model;
export const LIT_SHADOW_MAP_SIZE = 1024;
// Below LIT_TARGET_FPS for this many consecutive 500 ms stats, Lit declares
// a reduced quality instead of silently dropping frames.
const LIT_SLOW_SAMPLES = 4;
// Scan colours carry the capture's own lighting; scale them toward an albedo.
const ASSUMED_ALBEDO_SCALE = 0.8;
// A faint night fill so the unlit room stays navigable. Not a light source.
const NIGHT_FILL = 0.35;

/* Light colour for a Kelvin setting at constant luminance (Kim et al. ->
 * XYZ at Y = 1 -> linear sRGB), so brightness alone sets luminance. The orbs
 * and beams keep their own display colour. */
export function kelvinColor(kelvin, target = new THREE.Color()) {
    const [r, g, b] = Model.cctToLinearRgb(kelvin);
    return target.setRGB(r, g, b, THREE.LinearSRGBColorSpace);
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

export function createLitView({ scene, renderer, getMesh, getFixtureRoots, getCollision = () => null, spotFwhmDeg = 20,
    now = () => performance.now(), reducedMotion = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)') } = {}) {
    if (!scene?.isScene || !renderer?.shadowMap || typeof getMesh !== 'function' || typeof getFixtureRoots !== 'function')
        throw new TypeError('lit_view_dependencies_required');
    const group = new THREE.Group();
    group.name = 'lit-view-lights';
    group.userData = { presentation: 'modeled_lit_view', photometry: 'assumed_not_measured', physical_commands: false };
    const fill = new THREE.HemisphereLight(0x9aa6b8, 0x2a2622, NIGHT_FILL);
    fill.name = 'lit-night-fill';
    group.add(fill);
    let active = false, quality = 'full', slow = 0, swapped = [], lights = [], signature = '', saved = null, mesh = null;
    // The 160 ms ease: what each light shows, and the clock of the last step.
    let placementSignature = '', shown = [], easedAt = null, easing = false;
    // Bounce lights: a fixed set, so a change never recompiles.
    const bounce = Array.from({ length: LIT_BOUNCE_COUNT }, (_, index) => {
        const light = new THREE.SpotLight(0xffffff, 0, LIT_BOUNCE_RANGE_M, Model.LIT_BOUNCE_CONE, 1, 2);
        light.name = 'lit-bounce-' + (index + 1);
        light.userData = { lit_source: 'bounce', measured: false };
        group.add(light, light.target);
        return light;
    });
    const bounceTint = new THREE.Color(...Model.LIT_BOUNCE_TINT);
    let bounceSources = 0;
    const raycaster = new THREE.Raycaster(), rayHits = new Map();
    // First surface along a light's axis, against the decimated collision
    // proxy (the mesh as a fallback). Cached per ray: only a moved light
    // casts again; level and colour changes reuse the hit.
    function castRay(origin, direction) {
        const target = getCollision() || mesh;
        if (!target) return null;
        const key = [...origin.toArray(), ...direction.toArray()].map(value => value.toFixed(4)).join(',') + '|' + target.uuid;
        if (rayHits.has(key)) return rayHits.get(key);
        raycaster.set(origin, direction.clone().normalize()); raycaster.near = 0.02; raycaster.far = 30;
        const first = raycaster.intersectObject(target, true).find(candidate => candidate.point && candidate.face);
        let hit = null;
        if (first) {
            const normal = first.face.normal.clone().transformDirection(first.object.matrixWorld);
            if (normal.dot(direction) > 0) normal.negate();
            hit = { point: first.point.toArray(), normal: normal.toArray() };
        }
        if (rayHits.size > 256) rayHits.clear();
        rayHits.set(key, hit);
        return hit;
    }

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
        rayHits.clear();
        for (const light of lights) { light.shadow?.map?.dispose?.(); group.remove(light, light.target); light.dispose?.(); }
        lights = sources.map(makeLight);
        shown = [];
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
        const exact = (getFixtureRoots() || []).flatMap(root => fixtureWorldLights(root, { spotFwhmDeg }));
        const shape = exact.map(source => source.kind + (source.number || '')).join(',');
        if (shape !== lights.map(light => light.userData.shape).join(',')) { rebuild(exact); applyQuality(); }
        // SPEC: ease each set level and Kelvin on the 160 ms clock. The first
        // sync after enable, a rebuilt light set and reduced motion snap.
        const at = now(), elapsed = easedAt === null ? Infinity : at - easedAt;
        const step = Model.easeLights(exact, shown, elapsed, { snap: elapsed === Infinity || !!reducedMotion?.matches });
        const sources = step.lights;
        easedAt = at; shown = step.shown; easing = step.easing;
        const format = values => values.map(value => (typeof value === 'number' ? value.toFixed(5) : String(value))).join(':');
        // Shadow maps depend on where lights are, never on how bright: an
        // eased level re-renders the lights only.
        const placement = sources.map(source => format([...source.origin.toArray(), ...source.direction.toArray()])).join('|') + '|' + quality;
        const key = placement + '#' + sources.map(source => format([source.candela, source.kelvin])).join('|');
        if (key === signature) return false;
        const moved = placement !== placementSignature;
        signature = key; placementSignature = placement;
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
        const patches = bouncePatches(sources, sources.map(source => (source.level > 0 ? castRay(source.origin, source.direction) : null)),
            { up: [0, 1, 0] });
        bounceSources = patches.length;
        bounce.forEach((light, index) => {
            const patch = patches[index];
            if (!patch) { light.intensity = 0; light.userData.patch = null; return; }
            light.position.fromArray(patch.position);
            light.target.position.fromArray(patch.position).add(new THREE.Vector3().fromArray(patch.normal));
            light.target.updateMatrixWorld(true);
            kelvinColor(patch.kelvin, light.color).multiply(bounceTint);
            light.intensity = patch.intensity;
            light.userData.patch = { kind: patch.kind, reflected_lm: patch.reflected, albedo: patch.albedo, assumed: true };
        });
        if (moved) renderer.shadowMap.needsUpdate = true;
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
        active = true; quality = 'full'; slow = 0; signature = ''; placementSignature = ''; shown = []; easedAt = null;
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
        lights = []; signature = ''; placementSignature = ''; shown = []; easedAt = null; easing = false; rayHits.clear(); bounceSources = 0;
        for (const light of bounce) light.intensity = 0;
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
            easing, easeTauMs: Model.LIT_EASE_TAU_MS, dimmingCurve: Model.LIT_DIMMING_CURVE.name,
            bounceLights: bounce.length, bounceSources, bounceRangeM: LIT_BOUNCE_RANGE_M, bounceLimit: LIT_BOUNCE_LIMIT,
            assumedAlbedos: LIT_BOUNCE_ALBEDO,
            assumedLumens: LIT_ASSUMED_LUMENS, assumptionSource: LIT_ASSUMPTION_SOURCE, measured: false,
            disclosure: LIT_DISCLOSURE, disclosureDetail: LIT_DISCLOSURE_DETAIL });
    }
    return { enable, disable, sync, observeFrameRate, state, group };
}
