/* Ordinary Apartment presentation of saved equipment proposals.
 * A socket anchor can display a symbolic fixture before its optical offset is
 * measured. The orb and aim preview are display models, never registered
 * fixture geometry, live execution, photometry or a physical command. */
import * as THREE from 'three';
const [Spotlight] = await Promise.all([
    import(`./equipment-spotlight-visual.mjs${new URL(import.meta.url).search}`),
    import(`../home-den-fixtures.js${new URL(import.meta.url).search}`),
    import(`../home-apartment-aiming.js${new URL(import.meta.url).search}`),
]);

const Den = globalThis.HomeDenFixtures;
const Aiming = globalThis.HomeApartmentAiming;
const PROFILE = Den.defaultFixture();
const DEG = Math.PI / 180;
const copy = value => JSON.parse(JSON.stringify(value));
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

const MAX_EQUIPMENT = 32;
const MAX_SPAN = 100;
// Ordinary viewing has no commissioned mechanical travel. Do not inherit a
// prototype gimbal's travel limits into this display-only exploration control.
const VIRTUAL_LIMITS = Object.freeze({ pan_min_deg: -180, pan_max_deg: 180,
    tilt_min_deg: -90, tilt_max_deg: 90, limit_role: 'virtual_preview', physical_travel: 'unknown' });
// Illustrative exterior proportions, not surveyed or manufactured dimensions.
// The tube is no longer drawn; sources, picks and receivers keep these values.
const TUBE_RADIUS = .04, TUBE_HEIGHT = .16;
const DISPLAY_PIVOT_Z = -.14;
// Orb cosmetics only (SPEC: a small orb with a glow tinted by the channels
// that are on). None of these moves a source, pick, handle or receiver.
const ORB_CORE_RADIUS = .03, ORB_CORE_COLOR = 0x3d3f43;
const ORB_HALO_RADIUS = .15, ORB_HALO_FALLOFF = 2.5, ORB_HALO_CHANNEL_OPACITY = .3;
const RADIAL_FADE_MS = 160, RADIAL_SETTLED_LEVEL = .0005, RADIAL_SETTLED_KELVIN = 2;
const RADIAL_OFF_COLOR = new THREE.Color(0x283039);
// SPEC Aim lifecycle: Set aim moves the warm beam to its new target with a
// 650 ms sweep (ease-out cubic, as in the prototype), instant under reduced
// motion. Drawing only: state, targets, readback, the descriptor, the pick and
// the aim handle take the new aim at once.
export const AIM_SWEEP_MS = 650;
const finite = value => typeof value === 'number' && Number.isFinite(value);
const vector = (value, length = 3) => Array.isArray(value) && value.length === length && value.every(finite);
const unit = value => vector(value) && Math.abs(Math.hypot(...value) - 1) < 1e-6;
const identifier = value => typeof value === 'string' && value.length > 0 && value.length <= 160;
const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const bindingKeys = ['content_revision', 'registration_version', 'manifest_digest', 'frame_digest', 'canonical_collision_digest'];
const sameBinding = (a, b) => bindingKeys.every(key => a[key] === b[key]);
const metadata = Object.freeze({ truth_role: 'virtual_preview', presentation: 'symbolic_fixture',
    geometry_role: 'non_metric_decoration', metric_geometry: false, physical_commands: false,
    optical_registration: 'unavailable', photometry: 'unmeasured', receiving_surface: false });

function prepare(document, expectedBinding) {
    if (document?.schema !== 'home-equipment-registration-1' || document.version !== 1
        || document.scope?.site_id !== 'victoria' || document.scope?.map_id !== 'victoria-den' || document.scope?.room_id !== 'den'
        || !Number.isSafeInteger(document.revision) || document.revision < 0
        || !(document.digest === null && document.revision === 0 || hash(document.digest))) throw Error('equipment_document_identity');
    const binding = document.binding, refs = document.references;
    if (!binding || !identifier(binding.content_revision) || !Number.isSafeInteger(binding.registration_version) || binding.registration_version < 0
        || !bindingKeys.slice(2).every(key => hash(binding[key])) || expectedBinding && !sameBinding(binding, expectedBinding)) throw Error('equipment_binding_mismatch');
    if (refs?.frame !== 'z_up_metric_floor0' || refs.units !== 'm' || !unit(refs.floor?.normal)
        || refs.floor.normal[2] <= 1e-8 || !finite(refs.floor.offset_m)
        || !Array.isArray(refs.walls) || refs.walls.length < 3 || refs.walls.length > 32
        || refs.walls.some(wall => !vector(wall.normal_xy, 2) || Math.abs(Math.hypot(...wall.normal_xy) - 1) > 1e-6 || !finite(wall.offset_m))) throw Error('equipment_references_invalid');
    if (!Array.isArray(document.equipment) || document.equipment.length > MAX_EQUIPMENT
        || document.equipment.some(record => !identifier(record?.id)
            || !['mount', 'fixture', 'camera'].includes(record.kind)
            || !(record.position_m === null || vector(record.position_m))
            || !['missing', 'approximate', 'measured', 'modeled'].includes(record.position_status)
            || record.kind === 'fixture' && (!identifier(record.mounted_at_ref)
                || !(record.mount_offset_m === null || vector(record.mount_offset_m)))
            || !record.orientation || !(record.orientation.direction_unit === null || unit(record.orientation.direction_unit)))
        || new Set(document.equipment.map(record => record.id)).size !== document.equipment.length) throw Error('equipment_records_invalid');

    const floor = new THREE.Plane(new THREE.Vector3(...refs.floor.normal), -refs.floor.offset_m);
    const inside = point => refs.walls.every(wall => wall.normal_xy[0] * point.x + wall.normal_xy[1] * point.y >= wall.offset_m - 1e-6);
    const floorAt = (x, y) => (refs.floor.offset_m - refs.floor.normal[0] * x - refs.floor.normal[1] * y) / refs.floor.normal[2];
    const corners = [];
    for (let i = 0; i < refs.walls.length; i++) for (let j = i + 1; j < refs.walls.length; j++) {
        const a = refs.walls[i], b = refs.walls[j], det = a.normal_xy[0] * b.normal_xy[1] - a.normal_xy[1] * b.normal_xy[0];
        if (Math.abs(det) < 1e-6) continue;
        const x = (a.offset_m * b.normal_xy[1] - a.normal_xy[1] * b.offset_m) / det;
        const y = (a.normal_xy[0] * b.offset_m - a.offset_m * b.normal_xy[0]) / det;
        const corner = new THREE.Vector3(x, y, floorAt(x, y));
        if (vector(corner.toArray()) && inside(corner) && !corners.some(other => other.distanceTo(corner) < 1e-6)) corners.push(corner);
    }
    if (corners.length < 3) throw Error('equipment_room_unbounded');
    const center = corners.reduce((sum, point) => sum.add(point), new THREE.Vector3()).divideScalar(corners.length);
    if (corners.some(point => point.distanceTo(center) > MAX_SPAN)) throw Error('equipment_room_budget');
    const records = new Map(document.equipment.map(record => [record.id, record]));
    const fixtures = [], omitted = [];
    const linked = new Map();
    for (const record of document.equipment) if (record.kind === 'fixture') {
        // One visual assembly per socket; preserve the first linked record's
        // stable identity if an imported document contains multiple proposals.
        if (!linked.has(record.mounted_at_ref)) linked.set(record.mounted_at_ref, record);
        if (records.get(record.mounted_at_ref)?.kind !== 'mount') omitted.push({ fixture_id: record.id, reason: 'mount_position_missing' });
    }
    for (const mount of document.equipment) {
        if (mount.kind !== 'mount') continue;
        const saved = linked.get(mount.id);
        // A positioned socket without a proposed-fixture record gets a stable,
        // display-only fixture. The authoritative registration is never edited.
        const record = saved || { id: mount.id + '-virtual-fixture', label: mount.label + ' fixture', position_m: null, mount_offset_m: null };
        if (mount?.kind !== 'mount' || !vector(mount.position_m) || mount.position_status === 'missing') {
            omitted.push({ fixture_id: record.id, reason: 'mount_position_missing' }); continue;
        }
        // A resolved fixture origin is usable for presentation; an unresolved
        // optical/mount offset deliberately falls back only to its saved socket.
        const resolved = vector(record.position_m) && vector(record.mount_offset_m) && record.position_status !== 'missing';
        const anchor = new THREE.Vector3(...(resolved ? record.position_m : mount.position_m));
        if (!inside(anchor) || floor.distanceToPoint(anchor) < -.001 || anchor.distanceTo(center) > MAX_SPAN) {
            omitted.push({ fixture_id: record.id, reason: 'anchor_outside_room' }); continue;
        }
        let aimSource = 'modeled_room_preview';
        const towardCenter = center.clone().sub(anchor); towardCenter.z = 0;
        if (towardCenter.lengthSq() < 1e-8) towardCenter.set(1, 0, 0); else towardCenter.normalize();
        let across = MAX_SPAN;
        for (const wall of refs.walls) {
            const denominator = wall.normal_xy[0] * towardCenter.x + wall.normal_xy[1] * towardCenter.y;
            if (denominator < -1e-7) across = Math.min(across,
                (wall.offset_m - wall.normal_xy[0] * anchor.x - wall.normal_xy[1] * anchor.y) / denominator);
        }
        let target = anchor.clone().addScaledVector(towardCenter, across);
        target.z = floorAt(target.x, target.y) + .6 * (anchor.z - floorAt(anchor.x, anchor.y));
        let direction = target.clone().sub(anchor).normalize();
        if (record.orientation?.status === 'approximate' && unit(record.orientation.direction_unit)) {
            direction = new THREE.Vector3(...record.orientation.direction_unit);
            aimSource = 'owner_approximate';
            const denominator = floor.normal.dot(direction);
            const length = denominator < -1e-6 ? -floor.distanceToPoint(anchor) / denominator : -1;
            target = length > .3 && length < MAX_SPAN ? anchor.clone().addScaledVector(direction, length) : null;
            if (target && !inside(target)) target = null;
        }
        if (direction.lengthSq() < .99) direction.set(0, 0, -1);
        const reference = center.clone().sub(anchor); reference.z = 0;
        if (reference.lengthSq() < 1e-8) reference.set(1, 0, 0); else reference.normalize();
        const right = new THREE.Vector3(reference.y, -reference.x, 0);
        let pan = Math.atan2(direction.dot(right), direction.dot(reference)) / DEG;
        let tilt = Math.atan2(-direction.z, Math.hypot(direction.x, direction.y)) / DEG;
        pan = clamp(pan, VIRTUAL_LIMITS.pan_min_deg, VIRTUAL_LIMITS.pan_max_deg);
        tilt = clamp(tilt, VIRTUAL_LIMITS.tilt_min_deg, VIRTUAL_LIMITS.tilt_max_deg);
        fixtures.push({ id: record.id, mountId: mount.id, label: typeof record.label === 'string' ? record.label.slice(0, 160) : 'Virtual fixture',
            anchor, direction, target: target && anchor.distanceTo(target) > .35 ? target : null, floor,
            walls: refs.walls.map(wall => ({ normal: new THREE.Vector3(...wall.normal_xy, 0), offset: wall.offset_m })),
            reference, initialPan: pan, initialTilt: tilt, displayOnly: !saved,
            originRole: resolved ? 'registered_fixture_position' : 'mount_anchor', aimSource });
    }
    return { fixtures, omitted, revision: document.revision, digest: document.digest, binding: { ...binding } };
}

function disposeChildren(group) {
    const geometries = new Set(), materials = new Set(), textures = new Set();
    group.traverse(node => {
        if (node.geometry) geometries.add(node.geometry);
        for (const material of Array.isArray(node.material) ? node.material : node.material ? [node.material] : []) materials.add(material);
    });
    group.clear();
    geometries.forEach(geometry => geometry.dispose());
    materials.forEach(material => { if (material.map) textures.add(material.map); material.dispose(); });
    textures.forEach(texture => texture.dispose());
}

function initialState(item) {
    const channel = () => ({ on: true, brightness: .75, color_temp_kelvin: 3000 });
    return { radials: PROFILE.radials.zones.map(zone => ({ number: zone.number, ...channel(), azimuth_deg: zone.azimuth_deg })),
        downward: channel(), spotlight: { ...channel(), brightness: 1 }, pan_deg: Math.round(item.initialPan), tilt_deg: Math.round(item.initialTilt) };
}

// Display colour only: this approximation makes the virtual channel controls
// legible, and is not a measured spectrum or a photometric output estimate.
function displayWhite(kelvin) {
    const t = kelvin / 100;
    const red = t <= 66 ? 255 : 329.698727446 * Math.pow(t - 60, -.1332047592);
    const green = t <= 66 ? 99.4708025861 * Math.log(t) - 161.1195681661 : 288.1221695283 * Math.pow(t - 60, -.0755148492);
    const blue = t >= 66 ? 255 : t <= 19 ? 0 : 138.5177312231 * Math.log(t - 10) - 305.0447927307;
    return new THREE.Color(clamp(red, 0, 255) / 255, clamp(green, 0, 255) / 255, clamp(blue, 0, 255) / 255);
}

function nextState(current, patch) {
    if (!patch || typeof patch !== 'object' || Array.isArray(patch)
        || Object.keys(patch).some(key => !['radials', 'downward', 'spotlight', 'pan_deg', 'tilt_deg'].includes(key))) throw Error('equipment_preview_patch_invalid');
    const next = copy(current);
    function channel(target, update, cctRange) {
        if (!update || typeof update !== 'object' || Array.isArray(update)
            || Object.keys(update).some(key => !['on', 'brightness', 'color_temp_kelvin'].includes(key))) throw Error('equipment_preview_channel_invalid');
        if ('on' in update && typeof update.on !== 'boolean'
            || 'brightness' in update && (!finite(update.brightness) || update.brightness < 0 || update.brightness > 1)
            || 'color_temp_kelvin' in update && (!finite(update.color_temp_kelvin) || update.color_temp_kelvin < cctRange[0] || update.color_temp_kelvin > cctRange[1])) throw Error('equipment_preview_channel_range');
        Object.assign(target, update);
    }
    if ('radials' in patch) {
        if (!Array.isArray(patch.radials) || patch.radials.length > 6 || new Set(patch.radials.map(zone => zone?.number)).size !== patch.radials.length) throw Error('equipment_preview_radials_invalid');
        for (const zone of patch.radials) {
            if (!zone || !Number.isInteger(zone.number) || zone.number < 1 || zone.number > 6) throw Error('equipment_preview_radial_number');
            const { number, ...update } = zone;
            channel(next.radials[number - 1], update, PROFILE.radials.cct_range_k);
        }
    }
    if ('downward' in patch) channel(next.downward, patch.downward, PROFILE.downlight_ring.cct_range_k);
    if ('spotlight' in patch) channel(next.spotlight, patch.spotlight, PROFILE.spot.cct_range_k);
    for (const [key, min, max] of [['pan_deg', VIRTUAL_LIMITS.pan_min_deg, VIRTUAL_LIMITS.pan_max_deg], ['tilt_deg', VIRTUAL_LIMITS.tilt_min_deg, VIRTUAL_LIMITS.tilt_max_deg]]) {
        if (key in patch) {
            if (!finite(patch[key]) || patch[key] < min || patch[key] > max) throw Error('equipment_preview_aim_range');
            next[key] = patch[key];
        }
    }
    return next;
}

function receivingPoint(item, origin, direction) {
    let best = null;
    const surfaces = [{ normal: item.floor.normal, offset: -item.floor.constant, kind: 'floor', index: -1 },
        ...item.walls.map((wall, index) => ({ ...wall, kind: 'wall', index }))];
    for (const surface of surfaces) {
        const denominator = surface.normal.dot(direction);
        if (denominator >= -1e-7) continue;
        const distance = (surface.offset - surface.normal.dot(origin)) / denominator;
        if (distance >= -1e-7 && distance < MAX_SPAN && (!best || distance < best.distance)) best = {
            distance: Math.max(0, distance), position: origin.clone().addScaledVector(direction, Math.max(0, distance)), normal: surface.normal,
            kind: surface.kind, index: surface.index,
        };
    }
    return best;
}

// Convex wall and floor halfspaces are also used for receiver patches. This
// prevents a display footprint from spilling through the registered room edge.
function clipPolygon(item, points) {
    let polygon = points;
    for (const surface of [{ normal: item.floor.normal, offset: -item.floor.constant }, ...item.walls]) {
        const next = [];
        for (let i = 0; i < polygon.length; i++) {
            const a = polygon[i], b = polygon[(i + 1) % polygon.length];
            const da = surface.normal.dot(a) - surface.offset, db = surface.normal.dot(b) - surface.offset;
            if (da >= -1e-8) next.push(a);
            if ((da >= 0) !== (db >= 0)) next.push(a.clone().lerp(b, da / (da - db)));
        }
        polygon = next;
        if (polygon.length < 3) return [];
    }
    return polygon;
}

function putGeometry(geometry, points, capacity) {
    const position = geometry.attributes.position;
    for (let i = 0; i < capacity; i++) {
        const point = points[i] || points[0] || new THREE.Vector3();
        position.setXYZ(i, point.x, point.y, point.z);
    }
    position.needsUpdate = true;
    geometry.setDrawRange(0, points.length);
    geometry.computeBoundingSphere();
}

function triangleFan(points) {
    const triangles = [];
    for (let i = 1; i < points.length - 1; i++) triangles.push(points[0], points[i], points[i + 1]);
    return triangles;
}

function featherMap() {
    const pixels = new Uint8Array(32 * 32 * 4);
    for (let y = 0; y < 32; y++) for (let x = 0; x < 32; x++) {
        const index = (y * 32 + x) * 4, radius = Math.hypot((x + .5 - 16) / 16, (y + .5 - 16) / 16);
        pixels[index] = pixels[index + 1] = pixels[index + 2] = 255;
        pixels[index + 3] = Math.round(255 * Math.pow(Math.max(0, 1 - radius * radius), 2));
    }
    const texture = new THREE.DataTexture(pixels, 32, 32);
    texture.magFilter = texture.minFilter = THREE.LinearFilter; texture.needsUpdate = true;
    return texture;
}

// One soft, view-facing falloff shared by every halo layer: brightest toward
// the camera and fading to the silhouette, so the orb reads as one glow from
// any orbit angle. The shell sits in front of the core, so it lights it.
function orbHaloShader(shader) {
    const varyings = '#include <common>\nvarying vec3 vOrbHaloNormal;\nvarying vec3 vOrbHaloView;';
    shader.vertexShader = shader.vertexShader.replace('#include <common>', varyings).replace('#include <project_vertex>',
        '#include <project_vertex>\nvOrbHaloNormal = normalize( normalMatrix * normal );\nvOrbHaloView = - mvPosition.xyz;');
    shader.fragmentShader = shader.fragmentShader.replace('#include <common>', varyings).replace('#include <opaque_fragment>',
        `diffuseColor.a *= pow( clamp( dot( normalize( vOrbHaloNormal ), normalize( vOrbHaloView ) ), 0.0, 1.0 ), ${ORB_HALO_FALLOFF.toFixed(2)} );\n#include <opaque_fragment>`);
}

function buildFixture(item, state, depth, reducedMotion) {
    const root = new THREE.Group();
    root.name = 'equipment-virtual-fixture:' + item.id;
    root.position.copy(item.anchor);
    root.userData = { ...metadata, fixture_id: item.id, mount_id: item.mountId, origin_role: item.originRole,
        label: item.label, aim_source: item.aimSource, label_suffix: 'Virtual preview', optical_origin_m: null,
        display_only: item.displayOnly, fixture_kind: PROFILE.kind, radial_zone_count: 6, downward_zone_count: 1, robotic_spotlight_count: 1 };
    const material = (color, opacity = .98, additive = false, depthTest = true) => new THREE.MeshBasicMaterial({
        color, transparent: true, opacity, depthWrite: false, depthTest, side: THREE.DoubleSide, toneMapped: false,
        forceSinglePass: true,
        blending: additive ? THREE.AdditiveBlending : THREE.NormalBlending,
    });
    const mesh = (parent, name, geometry, color, opacity, additive, depthTest) => {
        const node = new THREE.Mesh(geometry, material(color, opacity, additive, depthTest));
        node.name = name; node.renderOrder = 30; parent.add(node); return node;
    };
    const buffer = capacity => new THREE.BufferGeometry().setAttribute('position',
        new THREE.BufferAttribute(new Float32Array(capacity * 3), 3));
    const assembly = new THREE.Group(); assembly.name = 'symbolic-engineered-fixture-assembly'; root.add(assembly);
    // An abstract light point: no housing or strips are drawn. The opaque,
    // depth-writing core sits on the radial source centreline, the pick centre.
    const orb = new THREE.Group(); orb.name = 'fixture-orb'; orb.position.z = -TUBE_HEIGHT / 2; assembly.add(orb);
    const core = mesh(orb, 'fixture-orb-core', new THREE.SphereGeometry(ORB_CORE_RADIUS, 24, 16), ORB_CORE_COLOR);
    core.material.opacity = 1; core.material.transparent = false; core.material.depthWrite = true;
    core.userData = { orb_part: 'core', visual_dimensions_only: true };
    // Every channel adds one layer of the same halo, tinted by its display
    // white and scaled by its level, so the layers sum to one channel-blended
    // glow. Display-only layers are never hit targets.
    const haloGeometry = new THREE.SphereGeometry(ORB_HALO_RADIUS, 32, 16);
    const halo = (parent, name, opacity) => {
        const layer = mesh(parent, name, haloGeometry, 0xffdfab, opacity, true);
        layer.material.side = THREE.FrontSide; layer.material.onBeforeCompile = orbHaloShader;
        layer.material.customProgramCacheKey = () => 'equipment-orb-halo-1';
        layer.position.z = -TUBE_HEIGHT / 2; layer.raycast = () => {};
        return layer;
    };

    const radialViews = state.radials.map(zone => {
        const group = new THREE.Group(); group.name = 'radial-zone-' + zone.number; root.add(group);
        const angle = zone.azimuth_deg * DEG, direction = new THREE.Vector3(Math.cos(angle), Math.sin(angle), 0);
        const port = mesh(group, 'radial-emitter-' + zone.number, new THREE.PlaneGeometry(.008, .10), 0xffdfab);
        port.position.copy(direction).multiplyScalar(.032); port.position.z = -TUBE_HEIGHT / 2;
        port.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(
            new THREE.Vector3(-direction.y, direction.x, 0), new THREE.Vector3(0, 0, 1), direction));
        port.visible = false;
        port.userData = { enclosed: true, source_kind: 'led_strip' };
        // The zone's faint tint carrier is its layer of the orb halo. Name,
        // channel number, visibility and the .1 x level opacity are unchanged.
        const sector = halo(group, 'radial-diffuser-sector-' + zone.number, .10);
        sector.userData = { orb_halo_layer: 'radial', channel_number: zone.number };
        const start = new THREE.Vector3(0, 0, -TUBE_HEIGHT / 2), worldStart = start.clone().add(item.anchor);
        const centerHit = receivingPoint(item, worldStart, direction);
        const patchPoints = [], samples = [];
        // A diffuser strip spreads outward through its 60-degree sector. The
        // authored display fan is illustrative, not a measured beam envelope.
        for (const elevation of [-24, -8, 8]) {
            const row = [];
            for (let i = 0; i <= 8; i++) {
                const azimuth = angle + (-30 + 60 * i / 8) * DEG, pitch = elevation * DEG;
                const ray = new THREE.Vector3(Math.cos(azimuth) * Math.cos(pitch), Math.sin(azimuth) * Math.cos(pitch), Math.sin(pitch));
                const hit = receivingPoint(item, worldStart, ray);
                row.push(hit);
            }
            samples.push(row);
        }
        for (let j = 0; j < 2; j++) for (let i = 0; i < 8; i++) {
            const cell = [samples[j][i], samples[j][i + 1], samples[j + 1][i + 1], samples[j + 1][i]];
            if (cell.some(hit => !hit) || cell.some(hit => hit.kind !== cell[0].kind || hit.index !== cell[0].index)) continue;
            const polygon = clipPolygon(item, cell.map(hit => hit.position.clone().addScaledVector(hit.normal, .006)));
            patchPoints.push(...triangleFan(polygon).map(point => point.clone().sub(item.anchor)));
        }
        const patch = mesh(group, 'radial-receiver-patch-' + zone.number, buffer(192), 0xffdfab, .025, true);
        patch.renderOrder = 20;
        putGeometry(patch.geometry, patchPoints, 192);
        patch.geometry.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(192 * 2), 2));
        const uv = patch.geometry.attributes.uv;
        for (let i = 0; i < 192; i++) {
            const ray = (patchPoints[i] || start).clone().sub(start).normalize();
            const azimuth = Math.atan2(Math.sin(Math.atan2(ray.y, ray.x) - angle), Math.cos(Math.atan2(ray.y, ray.x) - angle));
            uv.setXY(i, .5 + azimuth / (60 * DEG), (Math.asin(clamp(ray.z, -1, 1)) / DEG + 24) / 32);
        }
        patch.material.map = featherMap();
        Spotlight.attachEquipmentReceiverDepth(patch, depth);
        const illumination = { illumination_role: 'modeled_outward_strip', direction_unit: direction.toArray(),
            beam_origin_m: worldStart.toArray(), endpoint_m: centerHit?.position.toArray() || null,
            reach_m: centerHit?.distance || null, receiver_kind: centerHit?.kind || 'unavailable' };
        patch.userData = { ...patch.userData, ...illumination, presentation: 'feathered_outward_wash' };
        return { port, sector, patch, group, tone: displayWhite(zone.color_temp_kelvin), colorTemp: zone.color_temp_kelvin };
    });

    function beam(parent) {
        const visual = Spotlight.createEquipmentSpotlightVisual(parent, depth, item);
        const disc = mesh(parent, 'modeled-aim-receiver-glow', buffer(192), 0xffdb9c, .18, true);
        disc.geometry.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(192 * 2), 2));
        disc.material.map = featherMap(); disc.renderOrder = 21;
        Spotlight.attachEquipmentReceiverDepth(disc, depth);
        return { ...visual, disc };
    }
    const downwardGroup = new THREE.Group(); downwardGroup.name = 'downward-zone'; root.add(downwardGroup);
    const downwardEmitter = mesh(downwardGroup, 'downward-emitter', new THREE.RingGeometry(.014, .037, 48), 0xfff1db);
    downwardEmitter.position.z = -TUBE_HEIGHT - .0005;
    const downGlow = mesh(downwardGroup, 'downward-source-glow', new THREE.CylinderGeometry(.018, .05, .12, 24, 1, true), 0xfff1db, .035, true);
    downGlow.rotation.x = Math.PI / 2; downGlow.position.z = -TUBE_HEIGHT - .06;
    downGlow.userData.illumination_role = 'short_source_cue';
    const roboticGroup = new THREE.Group(); roboticGroup.name = 'robotic-spotlight'; root.add(roboticGroup);
    const panStage = new THREE.Group(); panStage.name = 'robotic-spotlight-pan-stage'; panStage.position.z = -.12;
    panStage.visible = false; panStage.userData = { enclosed: true }; roboticGroup.add(panStage);
    const head = new THREE.Group(); head.name = 'robotic-spotlight-head'; head.position.z = -.14;
    head.visible = false; head.userData = { enclosed: true }; roboticGroup.add(head);
    const headBody = mesh(head, 'robotic-spotlight-housing', new THREE.CylinderGeometry(.012, .016, .025, 20), 0x71818c);
    headBody.position.y = -.002;
    const spotEmitter = mesh(head, 'robotic-spotlight-emitter', new THREE.CircleGeometry(.011, 20), 0xffe5bc);
    spotEmitter.position.y = -.015; spotEmitter.rotation.x = Math.PI / 2;
    const spotBeam = beam(roboticGroup);

    // Dedicated, forgiving housing targets never make the beam volume or its
    // receiver patch clickable. Invisible materials remain raycastable.
    const pick = new THREE.Mesh(new THREE.SphereGeometry(.13, 12, 8), new THREE.MeshBasicMaterial({ visible: false }));
    pick.name = 'virtual-fixture-pick'; pick.position.z = -TUBE_HEIGHT / 2; root.add(pick);
    pick.userData = { interaction_role: 'fixture', fixture_id: item.id, mount_id: item.mountId };
    const handle = mesh(root, 'virtual-fixture-aim-handle', new THREE.SphereGeometry(.075, 12, 8), 0xb8dcff, .9, false, false);
    handle.renderOrder = 40; handle.visible = false;
    handle.userData = { interaction_role: 'aim_handle', fixture_id: item.id, mount_id: item.mountId };
    const handlePick = new THREE.Mesh(new THREE.SphereGeometry(.14, 12, 8), new THREE.MeshBasicMaterial({ visible: false }));
    handlePick.name = 'virtual-fixture-aim-pick'; root.add(handlePick);
    handlePick.userData = { interaction_role: 'aim_handle', fixture_id: item.id, mount_id: item.mountId };
    const downwardHalo = halo(downwardGroup, 'orb-halo-downward', ORB_HALO_CHANNEL_OPACITY);
    downwardHalo.userData = { orb_halo_layer: 'downward' };
    const spotlightHalo = halo(roboticGroup, 'orb-halo-spotlight', ORB_HALO_CHANNEL_OPACITY);
    spotlightHalo.userData = { orb_halo_layer: 'spotlight' };
    const paintHalo = (layer, channel) => {
        layer.visible = channel.on && channel.brightness > 0;
        layer.material.color.copy(displayWhite(channel.color_temp_kelvin)); layer.material.opacity = ORB_HALO_CHANNEL_OPACITY * channel.brightness;
    };
    let selected = false;

    function updateBeam(view, channel, origin, direction, fullAngle, target) {
        const worldOrigin = origin.clone().add(item.anchor);
        const hit = target ? { position: new THREE.Vector3(...target.point), normal: new THREE.Vector3(...target.normal),
            distance: worldOrigin.distanceTo(new THREE.Vector3(...target.point)), kind: target.kind || 'registered_mesh', index: -1 }
            : receivingPoint(item, worldOrigin, direction);
        const reach = hit ? hit.distance : 3;
        drawBeam(view, channel, origin, direction, fullAngle, reach, hit);
        const aim = { beam_origin_m: worldOrigin.toArray(), direction_unit: direction.toArray(), reach_m: reach,
            endpoint_m: worldOrigin.clone().addScaledVector(direction, reach).toArray(),
            receiver_kind: hit?.kind || 'unavailable', receiver_role: hit ? 'registered_reference_surface' : 'bounded_guide_only' };
        for (const node of [roboticGroup, view.volume, view.glow, view.disc]) Object.assign(node.userData, aim);
        return { channel, origin, direction, fullAngle, reach, hit, aim };
    }
    function drawBeam(view, channel, origin, direction, fullAngle, reach, hit) {
        const active = channel.on && channel.brightness > 0 && reach > .001;
        view.update(origin, direction, reach, fullAngle, channel);
        view.disc.visible = active && !!hit;
        if (!active) return;
        const color = Spotlight.displaySpotlightWhite(channel.color_temp_kelvin), radius = Aiming.beamRadius(reach, fullAngle);
        if (hit) {
            const patchU = new THREE.Vector3().crossVectors(hit.normal, Math.abs(hit.normal.z) < .9 ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(1, 0, 0)).normalize();
            const patchV = new THREE.Vector3().crossVectors(hit.normal, patchU).normalize();
            const center = hit.position.clone().addScaledVector(hit.normal, .006), polygon = [];
            for (let i = 0; i < 32; i++) polygon.push(center.clone().addScaledVector(patchU, Math.cos(i / 32 * Math.PI * 2) * radius)
                .addScaledVector(patchV, Math.sin(i / 32 * Math.PI * 2) * radius));
            const points = triangleFan(clipPolygon(item, polygon));
            putGeometry(view.disc.geometry, points.map(point => point.clone().sub(item.anchor)), 192);
            const uv = view.disc.geometry.attributes.uv;
            for (let i = 0; i < 192; i++) {
                const offset = (points[i] || center).clone().sub(center);
                uv.setXY(i, .5 + patchU.dot(offset) / (2 * radius), .5 + patchV.dot(offset) / (2 * radius));
            }
            uv.needsUpdate = true;
            view.disc.material.color.copy(color); view.disc.material.opacity = .18 * channel.brightness;
        }
    }
    /* The aim sweep: the endpoint drawn last, and while sweeping the from and
     * to beams. Metadata is re-asserted from the exact beam after every eased
     * draw, so nothing but pixels ever sees the in-between aim. */
    let drawnSpot = null, sweep = null;
    function endpointOf(beamState) {
        return beamState.hit ? beamState.hit.position.clone()
            : beamState.origin.clone().add(item.anchor).addScaledVector(beamState.direction, beamState.reach);
    }
    function drawSweep(nowMs) {
        const k = clamp((nowMs - sweep.at) / AIM_SWEEP_MS, 0, 1), eased = 1 - Math.pow(1 - k, 3);
        const to = sweep.to, worldOrigin = to.origin.clone().add(item.anchor);
        const point = sweep.from.clone().lerp(endpointOf(to), eased), delta = point.clone().sub(worldOrigin);
        const reach = delta.length();
        if (k >= 1 || !(reach > 1e-6)) { finishSweep(); return; }
        const direction = delta.divideScalar(reach);
        const normal = to.hit ? sweep.fromNormal.clone().lerp(to.hit.normal, eased) : null;
        const hit = to.hit ? { ...to.hit, position: point, distance: reach,
            normal: normal.lengthSq() > 1e-12 ? normal.normalize() : to.hit.normal.clone() } : null;
        drawBeam(spotBeam, to.channel, to.origin, direction, to.fullAngle, reach, hit);
        for (const node of [roboticGroup, spotBeam.volume, spotBeam.glow, spotBeam.disc]) Object.assign(node.userData, to.aim);
        drawnSpot = { point, normal: hit ? hit.normal.clone() : sweep.fromNormal.clone() };
    }
    function finishSweep() {
        if (!sweep) return;
        const to = sweep.to;
        sweep = null;
        drawBeam(spotBeam, to.channel, to.origin, to.direction, to.fullAngle, to.reach, to.hit);
        for (const node of [roboticGroup, spotBeam.volume, spotBeam.glow, spotBeam.disc]) Object.assign(node.userData, to.aim);
        drawnSpot = { point: endpointOf(to), normal: to.hit ? to.hit.normal.clone() : new THREE.Vector3(0, 0, 1) };
    }
    const shownLevels = new Float64Array(state.radials.map(zone => zone.on ? zone.brightness : 0));
    const targetLevels = shownLevels.slice();
    const shownKelvins = new Float64Array(state.radials.map(zone => zone.color_temp_kelvin));
    const targetKelvins = shownKelvins.slice();
    let radialMoving = false, radialAt = performance.now();
    function paintRadials() {
        for (let i = 0; i < radialViews.length; i++) {
            const view = radialViews[i], level = shownLevels[i], active = level > 0;
            if (view.colorTemp !== shownKelvins[i]) {
                view.tone.copy(displayWhite(shownKelvins[i])); view.colorTemp = shownKelvins[i];
            }
            view.port.material.color.copy(active ? view.tone : RADIAL_OFF_COLOR);
            view.port.material.opacity = active ? .4 + level * .6 : .65;
            view.sector.visible = active; view.sector.material.color.copy(view.tone); view.sector.material.opacity = .10 * level;
            view.patch.visible = active; view.patch.material.color.copy(view.tone); view.patch.material.opacity = .06 * level;
        }
    }
    function snapPresentation(nowMs) {
        shownLevels.set(targetLevels); shownKelvins.set(targetKelvins); radialMoving = false; radialAt = nowMs; paintRadials();
        finishSweep();
    }
    function updateRadials(radials) {
        let retarget = false;
        for (const zone of radials) {
            const index = zone.number - 1, view = radialViews[index], level = zone.on ? zone.brightness : 0;
            if (targetLevels[index] !== level) { targetLevels[index] = level; retarget = true; }
            if (targetKelvins[index] !== zone.color_temp_kelvin) { targetKelvins[index] = zone.color_temp_kelvin; retarget = true; }
            // Metadata describes the exact target, never the eased drawing.
            view.group.userData.channel = copy(zone);
        }
        if (retarget) {
            // Pointer updates can outnumber rendered frames. Keep the last
            // drawn clock while moving so retargeting cannot starve the fade.
            if (!radialMoving) radialAt = performance.now();
            radialMoving = false;
            for (let i = 0; i < shownLevels.length; i++) if (shownLevels[i] !== targetLevels[i] || shownKelvins[i] !== targetKelvins[i]) radialMoving = true;
        }
        if (reducedMotion?.matches) snapPresentation(performance.now());
        else paintRadials();
    }
    function advancePresentation(nowMs) {
        if (sweep) {
            if (reducedMotion?.matches) { snapPresentation(nowMs); return true; }
            drawSweep(nowMs);
            if (!radialMoving) return true;
        }
        if (!radialMoving) return false;
        if (reducedMotion?.matches) { snapPresentation(nowMs); return true; }
        const elapsed = nowMs - radialAt;
        if (!(elapsed > 0)) return false;
        const alpha = -Math.expm1(-elapsed / RADIAL_FADE_MS);
        radialAt = nowMs; radialMoving = false;
        for (let i = 0; i < shownLevels.length; i++) {
            const next = shownLevels[i] + (targetLevels[i] - shownLevels[i]) * alpha;
            shownLevels[i] = Math.abs(targetLevels[i] - next) <= RADIAL_SETTLED_LEVEL ? targetLevels[i] : next;
            const kelvin = shownKelvins[i] + (targetKelvins[i] - shownKelvins[i]) * alpha;
            shownKelvins[i] = Math.abs(targetKelvins[i] - kelvin) <= RADIAL_SETTLED_KELVIN ? targetKelvins[i] : kelvin;
            if (shownLevels[i] !== targetLevels[i] || shownKelvins[i] !== targetKelvins[i]) radialMoving = true;
        }
        paintRadials(); return true;
    }
    function update(next, target = null, { sweep: sweepAim = false } = {}) {
        updateRadials(next.radials);
        downwardEmitter.material.color.copy(next.downward.on && next.downward.brightness > 0 ? displayWhite(next.downward.color_temp_kelvin) : new THREE.Color(0x283039));
        downwardEmitter.material.opacity = next.downward.on && next.downward.brightness > 0 ? .4 + .6 * next.downward.brightness : .65;
        downwardGroup.userData.channel = copy(next.downward);
        downGlow.visible = next.downward.on && next.downward.brightness > 0;
        downGlow.material.color.copy(displayWhite(next.downward.color_temp_kelvin)); downGlow.material.opacity = .035 * next.downward.brightness;
        paintHalo(downwardHalo, next.downward); paintHalo(spotlightHalo, next.spotlight);
        const direction = new THREE.Vector3(...Aiming.directionFromAim(next.pan_deg, next.tilt_deg, { horizontal_world_reference: item.reference.toArray() }));
        head.quaternion.setFromUnitVectors(new THREE.Vector3(0, -1, 0), direction);
        panStage.rotation.z = Math.atan2(item.reference.y, item.reference.x) - next.pan_deg * DEG;
        const origin = head.position.clone();
        spotEmitter.material.color.copy(next.spotlight.on && next.spotlight.brightness > 0 ? displayWhite(next.spotlight.color_temp_kelvin) : new THREE.Color(0x283039));
        spotEmitter.material.opacity = next.spotlight.on && next.spotlight.brightness > 0 ? .4 + .6 * next.spotlight.brightness : .65;
        roboticGroup.userData.channel = copy(next.spotlight); roboticGroup.userData.pan_deg = next.pan_deg; roboticGroup.userData.tilt_deg = next.tilt_deg;
        const from = sweepAim ? drawnSpot : null;
        const beamState = updateBeam(spotBeam, next.spotlight, origin, direction, PROFILE.spot.fwhm_deg, target);
        sweep = null;
        drawnSpot = { point: endpointOf(beamState), normal: beamState.hit ? beamState.hit.normal.clone() : new THREE.Vector3(0, 0, 1) };
        if (from && from.point.distanceTo(drawnSpot.point) > 1e-4) {
            sweep = { at: performance.now(), from: from.point.clone(), fromNormal: from.normal.clone(), to: beamState };
            drawSweep(sweep.at);
        }
        const handlePoint = target ? new THREE.Vector3(...target.point).sub(item.anchor)
            : origin.clone().addScaledVector(direction, Math.min(1, roboticGroup.userData.reach_m || 1));
        handle.position.copy(handlePoint); handlePick.position.copy(handlePoint);
        handle.visible = selected;
    }
    root.traverse(node => { if (node !== root) node.userData = { ...metadata, ...node.userData, fixture_id: item.id, mount_id: item.mountId, origin_role: item.originRole, aim_source: item.aimSource, optical_origin_m: null }; });
    update(state);
    return { root, update, updateRadials, advancePresentation, snapPresentation, presentationActive: () => radialMoving || !!sweep,
        pick, handlePick, setSelected(value) { selected = value; handle.visible = value; } };
}

export function createEquipmentVirtualFixtures(parent, { binding = null } = {}) {
    if (!parent?.isObject3D) throw TypeError('equipment_preview_parent_required');
    const expectedBinding = binding && { ...binding };
    const root = new THREE.Group();
    root.name = 'equipment-virtual-fixtures'; root.userData = { ...metadata };
    parent.add(root);
    const depth = Spotlight.createEquipmentSpotlightDepth(root);
    const reducedMotion = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)');
    const visibilityDocument = globalThis.document;
    let disposed = false, signature = null, scopeIdentity = null, status = 'empty', reason = null, omitted = [], revision = null, digest = null;
    let fixtures = [], views = new Map(), states = new Map(), targets = new Map(), generation = 0, selectedId = null;
    let presentationActive = false;
    const snapPresentation = nowMs => {
        for (const view of views.values()) view.snapPresentation(nowMs);
        presentationActive = false;
    };
    const trackPresentation = view => {
        if (!root.visible || visibilityDocument?.hidden || reducedMotion?.matches) view.snapPresentation(performance.now());
        if (view.presentationActive()) presentationActive = true;
    };
    const visibilityChanged = () => { if (!disposed) snapPresentation(performance.now()); };
    visibilityDocument?.addEventListener?.('visibilitychange', visibilityChanged);
    const readback = () => ({ status, reason, visible: root.visible && !disposed, disposed, fixture_count: root.children.length,
        visual_implementation: 'equipment-spotlight-scattering-1', scattering_steps: 32, depth: depth.readback(),
        revision, digest, fixtures: fixtures.map(item => ({ fixture_id: item.id, mount_id: item.mountId, label: item.label, display_only: item.displayOnly,
            origin_role: item.originRole, aim_source: item.aimSource, fixture_kind: PROFILE.kind,
            radial_zone_count: 6, downward_zone_count: 1, robotic_spotlight_count: 1, state: copy(states.get(item.id)),
            limits: { ...VIRTUAL_LIMITS },
            color_temp_ranges: { radials: [...PROFILE.radials.cct_range_k], downward: [...PROFILE.downlight_ring.cct_range_k], spotlight: [...PROFILE.spot.cct_range_k] },
            profile_status: 'display_model', geometry_status: 'symbolic', optical_origin_m: null,
        })), omitted: omitted.map(item => ({ ...item })), physical_commands: false });
    const clear = () => { presentationActive = false; disposeChildren(root); depth.reset(); views.clear(); fixtures = []; signature = null; revision = null; digest = null; omitted = []; generation++; };
    const interactionDescriptor = fixtureId => {
        const item = fixtures.find(candidate => candidate.id === fixtureId);
        if (disposed || !item) return null;
        return copy({ schema: 'home-virtual-fixture-interaction-1', generation, fixture_id: item.id, mount_id: item.mountId,
            binding: JSON.parse(scopeIdentity), equipment_revision: revision, equipment_digest: digest,
            display_anchor_m: item.anchor.toArray(), display_origin_m: item.anchor.clone().add(new THREE.Vector3(0, 0, DISPLAY_PIVOT_Z)).toArray(),
            horizontal_world_reference: item.reference.toArray(), origin_role: item.originRole, optical_origin_m: null,
            state: states.get(item.id), target: targets.get(item.id) || null,
            truth_role: 'virtual_preview', physical_commands: false });
    };
    const setDocument = document => {
        if (disposed) throw Error('equipment_preview_disposed');
        if (document == null) { clear(); states.clear(); targets.clear(); selectedId = null; scopeIdentity = null; status = 'empty'; reason = null; return readback(); }
        try {
            const next = prepare(document, expectedBinding);
            const nextScope = JSON.stringify(next.binding);
            const nextSignature = JSON.stringify({ revision: next.revision, digest: next.digest, binding: next.binding,
                fixtures: next.fixtures.map(item => ({ id: item.id, mountId: item.mountId, label: item.label, anchor: item.anchor.toArray(),
                    reference: item.reference.toArray(), pan: item.initialPan, tilt: item.initialTilt,
                    floor: [item.floor.normal.toArray(), item.floor.constant], walls: item.walls.map(wall => [wall.normal.toArray(), wall.offset]),
                    displayOnly: item.displayOnly, originRole: item.originRole, aimSource: item.aimSource })), omitted: next.omitted });
            if (signature !== nextSignature) {
                const nextStates = new Map(next.fixtures.map(item => [item.id, nextScope === scopeIdentity && states.has(item.id) ? states.get(item.id) : initialState(item)]));
                const nextTargets = new Map(next.fixtures.filter(item => nextScope === scopeIdentity && targets.has(item.id)
                    && fixtures.find(previous => previous.id === item.id)?.anchor.distanceTo(item.anchor) < 1e-9)
                    .map(item => [item.id, targets.get(item.id)]));
                const nextViews = new Map();
                try { for (const item of next.fixtures) {
                    const view = buildFixture(item, nextStates.get(item.id), depth, reducedMotion);
                    view.update(nextStates.get(item.id), nextTargets.get(item.id)); view.setSelected(item.id === selectedId); nextViews.set(item.id, view);
                } }
                catch (error) { for (const view of nextViews.values()) disposeChildren(view.root); throw error; }
                clear(); states = nextStates; targets = nextTargets; views = nextViews; fixtures = next.fixtures;
                root.add(...[...views.values()].map(view => view.root)); signature = nextSignature; scopeIdentity = nextScope;
            }
            revision = next.revision; digest = next.digest; omitted = next.omitted;
            status = next.fixtures.length ? 'ready' : 'empty'; reason = null;
        } catch (error) {
            clear(); states.clear(); targets.clear(); selectedId = null; scopeIdentity = null; status = 'unavailable'; reason = error.message;
        }
        return readback();
    };
    return { root, setDocument, readback, interactionDescriptor,
        advancePresentation(nowMs) {
            // The settled RAF path creates no arrays, iterators or snapshots.
            if (disposed || !presentationActive || !finite(nowMs)) return false;
            if (!root.visible || visibilityDocument?.hidden) { snapPresentation(nowMs); return false; }
            let changed = false; presentationActive = false;
            for (const view of views.values()) {
                if (view.advancePresentation(nowMs)) changed = true;
                if (view.presentationActive()) presentationActive = true;
            }
            return changed;
        },
        /* Read-only: a beam sweep or radial ease is still drawing (Lit's
         * bounce re-bake waits for the Set sweep to land). */
        presentationActive() { return !disposed && presentationActive; },
        pickObjects() { return disposed || !root.visible ? [] : [...views].flatMap(([id, view]) => id === selectedId ? [view.pick, view.handlePick] : [view.pick]); },
        setSelected(fixtureId) {
            selectedId = views.has(fixtureId) ? fixtureId : null;
            for (const [id, view] of views) view.setSelected(id === selectedId);
            return interactionDescriptor(selectedId);
        },
        /* `sweep: true` (Set aim) draws the AIM_SWEEP_MS beam sweep; every
         * other caller, such as a live preview following a target, moves the
         * beam at once exactly as before. */
        aimAtTarget(fixtureId, target, { expectedGeneration = generation, sweep = false } = {}) {
            const descriptor = interactionDescriptor(fixtureId);
            if (!descriptor) return { ok: false, reason: disposed ? 'equipment_preview_disposed' : 'equipment_preview_fixture_missing' };
            if (expectedGeneration !== generation) return { ok: false, reason: 'equipment_preview_generation_changed' };
            if (!target || !vector(target.point) || !unit(target.normal)
                || target.surface_id != null && !identifier(target.surface_id)
                || target.kind != null && !identifier(target.kind)) return { ok: false, reason: 'equipment_preview_target_invalid' };
            const direction = new THREE.Vector3(...target.point).sub(new THREE.Vector3(...descriptor.display_origin_m));
            if (direction.length() < .015 || direction.length() > MAX_SPAN) return { ok: false, reason: 'equipment_preview_target_distance' };
            const reference = new THREE.Vector3(...descriptor.horizontal_world_reference), right = new THREE.Vector3(reference.y, -reference.x, 0);
            const pan = Math.hypot(direction.x, direction.y) < 1e-8 ? descriptor.state.pan_deg : Math.atan2(direction.dot(right), direction.dot(reference)) / DEG;
            const tilt = Math.atan2(-direction.z, Math.hypot(direction.x, direction.y)) / DEG;
            const state = nextState(states.get(fixtureId), { pan_deg: pan, tilt_deg: tilt });
            const savedTarget = { point: target.point.slice(), normal: target.normal.slice(), surface_id: target.surface_id || null, kind: target.kind || 'registered_mesh' };
            states.set(fixtureId, state); targets.set(fixtureId, savedTarget);
            const view = views.get(fixtureId); view.update(state, savedTarget, { sweep: sweep === true }); trackPresentation(view);
            return { ok: true, descriptor: interactionDescriptor(fixtureId), physical_commands: false };
        },
        restoreAim(fixtureId, snapshot) {
            if (!snapshot || snapshot.fixture_id !== fixtureId || snapshot.generation !== generation || !states.has(fixtureId)) return { ok: false, reason: 'equipment_preview_generation_changed' };
            try {
                const state = nextState(states.get(fixtureId), { ...snapshot.state,
                    radials: snapshot.state.radials.map(({ number, on, brightness, color_temp_kelvin }) => ({ number, on, brightness, color_temp_kelvin })) });
                states.set(fixtureId, state);
                if (snapshot.target) targets.set(fixtureId, copy(snapshot.target)); else targets.delete(fixtureId);
                const view = views.get(fixtureId); view.update(state, targets.get(fixtureId)); trackPresentation(view);
                return { ok: true, descriptor: interactionDescriptor(fixtureId), physical_commands: false };
            } catch (error) { return { ok: false, reason: error.message }; }
        },
        setFixtureState(fixtureId, patch) {
            if (disposed) return { ...readback(), ok: false, reason: 'equipment_preview_disposed' };
            if (!states.has(fixtureId)) return { ...readback(), ok: false, reason: 'equipment_preview_fixture_missing' };
            try {
                const state = nextState(states.get(fixtureId), patch);
                if ('pan_deg' in patch || 'tilt_deg' in patch) targets.delete(fixtureId);
                // Property presence preserves inherited/mixed-patch semantics:
                // even an unchanged pan retires its old target and must take
                // the full aim/downward/receiver update path.
                const radialOnly = Object.hasOwn(patch, 'radials') && Object.keys(patch).length === 1
                    && !('downward' in patch || 'spotlight' in patch || 'pan_deg' in patch || 'tilt_deg' in patch);
                const view = views.get(fixtureId);
                if (radialOnly) view.updateRadials(state.radials);
                else view.update(state, targets.get(fixtureId));
                trackPresentation(view);
                states.set(fixtureId, state);
                return { ...readback(), ok: true };
            } catch (error) { return { ...readback(), ok: false, reason: error.message }; }
        },
        setVisible(visible) { if (disposed) return; if (typeof visible !== 'boolean') throw TypeError('equipment_preview_visibility_boolean');
            snapPresentation(performance.now()); root.visible = visible; },
        dispose() { if (disposed) return; visibilityDocument?.removeEventListener?.('visibilitychange', visibilityChanged);
            clear(); depth.dispose(); states.clear(); targets.clear(); selectedId = null; scopeIdentity = null; root.removeFromParent(); disposed = true; status = 'disposed'; reason = null; },
    };
}

