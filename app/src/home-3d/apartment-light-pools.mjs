/* Restrained beams and per-channel pools for the Den's modeled fixtures (APT-L02).
 * Presentation only, on the admitted UI overlay layer: it reads the published
 * descriptors and receiver metadata and never changes equipment state,
 * receivers or the #247-protected fixture geometry. Not measured illumination. */
import * as THREE from 'three';

export const BEAM_RULES = Object.freeze(['selected', 'all', 'none']);
const DEG = Math.PI / 180, TAU = Math.PI * 2;
const finite3 = value => Array.isArray(value) && value.length === 3 && value.every(Number.isFinite);
const lit = channel => !!channel?.on && channel.brightness > 0;

// Floor height under a point from the registration's floor reference.
export function floorHeightAt(floor, x, y) {
    const normal = floor?.normal;
    if (!finite3(normal) || !(normal[2] > 1e-8) || !Number.isFinite(floor.offset_m)) return null;
    return (floor.offset_m - normal[0] * x - normal[1] * y) / normal[2];
}

// The supplied prototype's qualitative white for a colour temperature.
export function kelvinColor(kelvin) {
    const t = Math.max(1000, Math.min(12000, Number.isFinite(kelvin) ? kelvin : 2700)) / 100;
    const r = t <= 66 ? 255 : 329.7 * Math.pow(t - 60, -.1332);
    const g = t <= 66 ? 99.47 * Math.log(t) - 161.12 : 288.12 * Math.pow(t - 60, -.0755);
    const b = t <= 19 ? 0 : t <= 66 ? 138.52 * Math.log(t - 10) - 305.04 : 255;
    return [r, g, b].map(value => Math.max(0, Math.min(255, value)) / 255);
}

/* Pure plan. fixtures: [{ fixture_id, label, state, beam: { origin_m, endpoint_m, reach_m }, anchor_m }].
 * Beams: only the selected light by default (or all, or none). A selected
 * light whose spot is off shows a grey dashed ring at its last aim. Pools add
 * the Down channel below the socket; Spot and Radial pools already exist. */
export function lightPoolsPlan({ fixtures = [], selectedId = null, beams = 'selected', pools = true,
    floor = null, spotFwhmDeg = 20, downBeamDeg = 60 } = {}) {
    const rule = BEAM_RULES.includes(beams) ? beams : 'selected';
    const axes = [], rings = [], downPools = [], labels = [];
    for (const fixture of fixtures) {
        const id = fixture?.fixture_id, state = fixture?.state, beam = fixture?.beam;
        if (typeof id !== 'string' || !state) continue;
        const selected = id === selectedId, shown = rule === 'all' || rule === 'selected' && selected;
        const reach = Number(beam?.reach_m);
        const aimed = finite3(beam?.origin_m) && finite3(beam?.endpoint_m) && reach > .001;
        const radius = aimed ? reach * Math.tan(spotFwhmDeg * DEG / 2) : 0;
        if (shown && aimed && lit(state.spotlight)) {
            axes.push({ fixture_id: id, from: beam.origin_m.slice(), to: beam.endpoint_m.slice(), radius,
                kelvin: state.spotlight.color_temp_kelvin, brightness: state.spotlight.brightness });
        } else if (selected && rule !== 'none' && aimed && !lit(state.spotlight)) {
            rings.push({ fixture_id: id, center: beam.endpoint_m.slice(), normal: beam.origin_m.map((value, i) => value - beam.endpoint_m[i]), radius });
        }
        if (selected && aimed && lit(state.spotlight)) labels.push({ fixture_id: id, channel: 'spot', point_m: beam.endpoint_m.slice(), text: 'Spot · modeled' });
        const anchor = fixture.anchor_m, ground = finite3(anchor) ? floorHeightAt(floor, anchor[0], anchor[1]) : null;
        if (pools && lit(state.downward) && ground != null && anchor[2] - ground > .05) {
            const center = [anchor[0], anchor[1], ground + .008];
            downPools.push({ fixture_id: id, channel: 'down', center, radius: Math.min(1.6, (anchor[2] - ground) * Math.tan(downBeamDeg * DEG / 2)),
                kelvin: state.downward.color_temp_kelvin, brightness: state.downward.brightness });
            if (selected) labels.push({ fixture_id: id, channel: 'down', point_m: center, text: 'Down · modeled' });
        }
    }
    return Object.freeze({ beams: rule, pools: !!pools, axes, rings, downPools, labels, truth_role: 'virtual_preview', physical_commands: false });
}

function poolTexture() {
    const size = 64, data = new Uint8Array(size * size * 4);
    for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
        const d = Math.min(1, Math.hypot(x - size / 2 + .5, y - size / 2 + .5) / (size / 2));
        const alpha = Math.round(255 * Math.pow(1 - d, 1.6)), i = (y * size + x) * 4;
        data[i] = data[i + 1] = data[i + 2] = 255; data[i + 3] = alpha;
    }
    const texture = new THREE.DataTexture(data, size, size); texture.needsUpdate = true;
    return texture;
}

/* Draws a plan into a borrowed overlay layer group. Labels use the layer's
 * own anchors, so they share its projection and lifetime. */
export function createLightPools({ layer } = {}) {
    if (!layer?.group?.isGroup || typeof layer.addAnchor !== 'function') throw TypeError('light_pools_layer_required');
    const root = new THREE.Group(); root.name = 'apartment-light-pools';
    root.userData = { presentation_only: true, truth_role: 'virtual_preview', physical_commands: false };
    layer.group.add(root);
    const texture = poolTexture(), owned = new Set(), anchors = new Map();
    let disposed = false, last = null, signature = '';
    const own = resource => { owned.add(resource); return resource; };
    const clear = () => {
        for (const child of [...root.children]) {
            child.removeFromParent(); child.geometry?.dispose(); child.material?.dispose();
        }
    };
    const vec = value => new THREE.Vector3(...value);
    const basis = (normal) => {
        const n = vec(normal).normalize(), u = new THREE.Vector3().crossVectors(n, Math.abs(n.z) < .9 ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(1, 0, 0)).normalize();
        return [u, new THREE.Vector3().crossVectors(n, u).normalize()];
    };
    const circle = (center, normal, radius, count) => {
        const [u, v] = basis(normal), c = vec(center);
        return Array.from({ length: count + 1 }, (_, i) => c.clone().addScaledVector(u, Math.cos(i / count * TAU) * radius)
            .addScaledVector(v, Math.sin(i / count * TAU) * radius));
    };
    const line = (points, material, name, segments = false) => {
        const object = segments ? new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(points), material)
            : new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), material);
        object.name = name; object.renderOrder = 22; object.frustumCulled = false;
        if (material.isLineDashedMaterial) object.computeLineDistances();
        root.add(object); return object;
    };
    function update(plan) {
        if (disposed || !plan) return readback();
        const next = JSON.stringify(plan);
        if (next === signature) return readback();
        signature = next; last = plan; clear();
        for (const axis of plan.axes) {
            const color = new THREE.Color(...kelvinColor(axis.kelvin)), from = vec(axis.from), to = vec(axis.to);
            line([from, to], new THREE.LineBasicMaterial({ color, transparent: true, opacity: .75, depthTest: false, depthWrite: false }), 'light-pools-axis:' + axis.fixture_id);
            const ring = circle(axis.to, axis.from.map((value, i) => value - axis.to[i]), axis.radius, 32);
            const spokes = [0, 8, 16, 24].flatMap(i => [from, ring[i]]);
            const faint = new THREE.LineBasicMaterial({ color, transparent: true, opacity: .2, depthTest: false, depthWrite: false });
            line(spokes, faint, 'light-pools-cone:' + axis.fixture_id, true);
            line(ring, faint.clone(), 'light-pools-cone-rim:' + axis.fixture_id);
        }
        for (const ring of plan.rings) line(circle(ring.center, ring.normal, Math.max(.06, ring.radius), 48),
            new THREE.LineDashedMaterial({ color: 0x9aa0a6, transparent: true, opacity: .7, dashSize: .04, gapSize: .03, depthTest: false, depthWrite: false }),
            'light-pools-last-aim:' + ring.fixture_id);
        for (const pool of plan.downPools) {
            const mesh = new THREE.Mesh(new THREE.CircleGeometry(pool.radius, 48), new THREE.MeshBasicMaterial({
                color: new THREE.Color(...kelvinColor(pool.kelvin)), map: texture, transparent: true, opacity: .1 + .25 * pool.brightness,
                blending: THREE.AdditiveBlending, depthWrite: false }));
            mesh.name = 'light-pools-down:' + pool.fixture_id; mesh.position.set(...pool.center); mesh.renderOrder = 19;
            mesh.userData = { channel: 'down', illumination_role: 'modeled_down_pool', truth_role: 'virtual_preview', physical_commands: false };
            root.add(mesh);
        }
        const wanted = new Set(plan.labels.map(label => label.fixture_id + ':' + label.channel));
        for (const [key, handle] of anchors) if (!wanted.has(key)) { handle.dispose(); anchors.delete(key); }
        for (const label of plan.labels) {
            const key = label.fixture_id + ':' + label.channel, handle = anchors.get(key);
            if (handle) handle.update({ point_m: label.point_m, label: label.text });
            else { const created = layer.addAnchor({ point_m: label.point_m, label: label.text }); if (created) anchors.set(key, created); }
        }
        return readback();
    }
    function readback() {
        return { disposed, beams: last?.beams ?? null, pools: last?.pools ?? null,
            axes: last?.axes.map(axis => axis.fixture_id) || [], rings: last?.rings.map(ring => ring.fixture_id) || [],
            down_pools: last?.downPools.map(pool => pool.fixture_id) || [], labels: last?.labels.map(label => label.text) || [],
            objects: root.children.map(child => child.name), truth_role: 'virtual_preview', physical_commands: false };
    }
    function dispose() {
        if (disposed) return;
        disposed = true; clear();
        for (const handle of anchors.values()) handle.dispose();
        anchors.clear(); texture.dispose(); for (const resource of owned) resource.dispose?.();
        root.removeFromParent();
    }
    return Object.freeze({ update, readback, dispose });
}
