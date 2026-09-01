/* home-3d/markers.js — device markers, zone outlines, person dot, room pulse.
 *
 * All under apartmentRoot (Z-up object space): positions are apartment-frame
 * [x, y, z] used directly. Device markers draw twice (depth-tested + faint
 * x-ray pass) so they stay readable when occluded. The person dot follows the
 * plan's honesty ladder: precise pos -> dot + 2σ ring; pos null -> the room's
 * zone outline pulses instead (never a guessed dot).
 */
import * as THREE from 'three';

const ICE = new THREE.Color(0xb8d8ff);
const SURVEY = new THREE.Color(0xffb45f);
const OPTICAL_CYAN = new THREE.Color(0x45dfff);
const STALE_BEAM = new THREE.Color(0xa6adb7);
const TYPE_COLOR = {
    light: new THREE.Color(0xffe2a8),
    speaker: new THREE.Color(0xa8ffd8),
    tv: new THREE.Color(0xd8a8ff),
    amp: new THREE.Color(0xd8a8ff),
    camera: new THREE.Color(0xa8c8ff),
    other: new THREE.Color(0xcccccc),
};
const TARGET_COLOR = {
    table: new THREE.Color(0x8ce6cf),
    island: new THREE.Color(0xffcc7a),
    art: new THREE.Color(0xc6a8ff),
    custom: ICE,
};

function makeGlowTexture() {
    const c = document.createElement('canvas');
    c.width = c.height = 64;
    const g = c.getContext('2d');
    const grad = g.createRadialGradient(32, 32, 2, 32, 32, 30);
    grad.addColorStop(0, 'rgba(255,255,255,0.9)');
    grad.addColorStop(0.4, 'rgba(255,255,255,0.25)');
    grad.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = grad;
    g.fillRect(0, 0, 64, 64);
    return new THREE.CanvasTexture(c);
}

function makeTagSprite(text, color, { compact = false } = {}) {
    const canvas = document.createElement('canvas');
    canvas.width = compact ? 384 : 512;
    canvas.height = 96;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = 'rgba(8,11,16,0.86)';
    ctx.fillRect(4, 12, canvas.width - 8, 72);
    ctx.strokeStyle = `#${color.getHexString()}`;
    ctx.lineWidth = 3;
    ctx.strokeRect(4, 12, canvas.width - 8, 72);
    ctx.fillStyle = `#${color.getHexString()}`;
    ctx.font = `${compact ? 28 : 31}px "Geist Mono", monospace`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    const label = String(text || '').toLowerCase();
    ctx.fillText(label.length > 29 ? `${label.slice(0, 28)}…` : label,
        canvas.width / 2, canvas.height / 2 + 1);
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    const material = new THREE.SpriteMaterial({
        map: texture, transparent: true, opacity: 0.78,
        depthTest: false, depthWrite: false,
    });
    const sprite = new THREE.Sprite(material);
    sprite.scale.set(compact ? 0.9 : 1.25, compact ? 0.225 : 0.235, 1);
    sprite.renderOrder = 35;
    return sprite;
}

function clearDisposableGroup(group) {
    while (group.children.length) {
        const child = group.children[0];
        group.remove(child);
        child.traverse?.((node) => {
            node.geometry?.dispose?.();
            if (Array.isArray(node.material)) node.material.forEach((m) => {
                m.map?.dispose?.(); m.dispose?.();
            });
            else { node.material?.map?.dispose?.(); node.material?.dispose?.(); }
        });
    }
}

function targetBasis(target) {
    const normal = new THREE.Vector3(...(target.normal || [0, 0, 1])).normalize();
    let up = new THREE.Vector3(...(target.up || (Math.abs(normal.z) < 0.75
        ? [0, 0, 1] : [0, 1, 0]))).normalize();
    let right = new THREE.Vector3().crossVectors(up, normal);
    if (right.lengthSq() < 0.001) {
        up = Math.abs(normal.z) < 0.75 ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(0, 1, 0);
        right.crossVectors(up, normal);
    }
    right.normalize();
    up = new THREE.Vector3().crossVectors(normal, right).normalize();
    return { normal, right, up };
}

export function createOverlay(apartmentRoot) {
    const overlayRoot = new THREE.Group();
    overlayRoot.renderOrder = 10;
    apartmentRoot.add(overlayRoot);

    const devicesGroup = new THREE.Group();
    const camerasGroup = new THREE.Group();
    const personGroup = new THREE.Group();
    const zonesGroup = new THREE.Group();
    const zoneDraftGroup = new THREE.Group();
    const targetsGroup = new THREE.Group();
    const fixtureMeasureGroup = new THREE.Group();
    const aimBeamGroup = new THREE.Group();
    const aimTransitionGroup = new THREE.Group();
    overlayRoot.add(devicesGroup, camerasGroup, personGroup, zonesGroup,
        targetsGroup, fixtureMeasureGroup, aimBeamGroup, aimTransitionGroup);
    zonesGroup.add(zoneDraftGroup);

    const glowTex = makeGlowTexture();
    const markersById = new Map();   // device id -> {group, core, ghost, glow, pick, device}
    const zonesById = new Map();     // zone id -> editable outline/fill/handles
    const targetsById = new Map();   // target id -> rendered named point/surface
    let hoveredTargetId = null;
    let selectedTargetId = null;

    /* floor glow pools: one soft additive disc per ON light, created lazily.
       Shared geometry + texture; per-disc material (color/opacity vary). */
    const floorGlowGroup = new THREE.Group();
    overlayRoot.add(floorGlowGroup);
    const floorGlowGeo = new THREE.CircleGeometry(1.1, 48);  // XY plane, faces +Z (up)
    const glowDiscsById = new Map();                         // device id -> Mesh

    function getGlowDisc(m) {
        let disc = glowDiscsById.get(m.device.id);
        if (!disc) {
            disc = new THREE.Mesh(floorGlowGeo, new THREE.MeshBasicMaterial({
                map: glowTex, color: TYPE_COLOR.light.clone(),
                transparent: true, opacity: 0.0,
                blending: THREE.AdditiveBlending, depthWrite: false,
            }));
            disc.position.set(m.device.pos[0], m.device.pos[1], 0.02);
            disc.renderOrder = 9;    // under the markers' x-ray pass
            glowDiscsById.set(m.device.id, disc);
            floorGlowGroup.add(disc);
        }
        return disc;
    }

    /* hover floor ring: single reusable pulsing ring under the hovered marker */
    const hoverRing = new THREE.Mesh(
        new THREE.RingGeometry(0.40, 0.45, 48),
        new THREE.MeshBasicMaterial({ color: ICE, transparent: true, opacity: 0.0,
                                      side: THREE.DoubleSide, depthWrite: false }),
    );
    hoverRing.renderOrder = 12;
    hoverRing.visible = false;
    overlayRoot.add(hoverRing);

    /* ---------------- devices ---------------- */

    function buildMarker(device) {
        const color = (TYPE_COLOR[device.type] || TYPE_COLOR.other).clone();
        const group = new THREE.Group();
        group.position.set(device.pos[0], device.pos[1], device.pos[2] ?? 0);

        const core = new THREE.Mesh(
            new THREE.SphereGeometry(0.055, 16, 12),
            new THREE.MeshBasicMaterial({ color }),
        );
        const ghost = new THREE.Mesh(
            core.geometry,
            new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.22, depthTest: false }),
        );
        ghost.renderOrder = 11;
        const glow = new THREE.Sprite(new THREE.SpriteMaterial({
            map: glowTex, color, transparent: true, opacity: 0.0,
            blending: THREE.AdditiveBlending, depthWrite: false,
        }));
        glow.scale.setScalar(0.6);
        const pick = new THREE.Mesh(
            new THREE.SphereGeometry(0.18, 8, 6),
            new THREE.MeshBasicMaterial({ visible: false }),
        );
        pick.userData.deviceId = device.id;
        // hairline from floor to the marker, grounds it visually
        const stem = new THREE.Mesh(
            new THREE.CylinderGeometry(0.003, 0.003, 1, 4),
            new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.25 }),
        );
        const h = device.pos[2] ?? 0;
        stem.rotation.x = Math.PI / 2;       // cylinder Y -> object Z (up)
        stem.scale.y = Math.max(0.001, h);
        stem.position.z = -h / 2;
        group.add(core, ghost, glow, pick, stem);

        // camera devices: a small always-on frustum cone along yaw (40%),
        // landmark + click affordance for the fly-to
        if (device.type === 'camera') {
            const L = 0.7, tan = 0.45, tanV = 0.3;
            const corners = [
                new THREE.Vector3(L, L * tan, -L * tanV), new THREE.Vector3(L, -L * tan, -L * tanV),
                new THREE.Vector3(L, -L * tan, L * tanV), new THREE.Vector3(L, L * tan, L * tanV),
            ];
            const pts = [];
            for (let i = 0; i < 4; i++) {
                pts.push(new THREE.Vector3(0, 0, 0), corners[i]);
                pts.push(corners[i], corners[(i + 1) % 4]);
            }
            const cone = new THREE.LineSegments(
                new THREE.BufferGeometry().setFromPoints(pts),
                new THREE.LineBasicMaterial({ color: TYPE_COLOR.camera, transparent: true, opacity: 0.4 }),
            );
            cone.rotation.z = device.yaw_rad || 0;
            group.add(cone);
        }
        return { group, core, ghost, glow, pick, device, state: null };
    }

    function setDevices(devices) {
        for (const { group } of markersById.values()) devicesGroup.remove(group);
        markersById.clear();
        for (const disc of glowDiscsById.values()) {
            floorGlowGroup.remove(disc);
            disc.material.dispose();   // geometry + texture are shared, keep them
        }
        glowDiscsById.clear();
        hoverRing.visible = false;
        for (const d of devices || []) {
            if (!Array.isArray(d.pos)) continue;
            const m = buildMarker(d);
            markersById.set(d.id, m);
            devicesGroup.add(m.group);
        }
    }

    /* ---------------- named aiming targets ---------------- */

    function buildTarget(target) {
        const color = (TARGET_COLOR[target.category] || TARGET_COLOR.custom).clone();
        const group = new THREE.Group();
        group.position.set(+(target.pos?.[0] || 0), +(target.pos?.[1] || 0), +(target.pos?.[2] || 0));
        const { normal, right, up } = targetBasis(target);
        const shape = target.shape === 'point' ? 'point' : 'surface';
        let pick;
        const visualMaterials = [];
        if (shape === 'point') {
            const core = new THREE.Mesh(
                new THREE.SphereGeometry(0.045, 14, 10),
                new THREE.MeshBasicMaterial({ color, depthTest: false }));
            const ring = new THREE.Mesh(
                new THREE.RingGeometry(0.11, 0.135, 28),
                new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.72,
                    side: THREE.DoubleSide, depthTest: false }));
            ring.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), normal);
            core.renderOrder = ring.renderOrder = 31;
            group.add(core, ring);
            visualMaterials.push(core.material, ring.material);
            pick = new THREE.Mesh(new THREE.SphereGeometry(0.2, 8, 6),
                new THREE.MeshBasicMaterial({ visible: false }));
        } else {
            const size = Array.isArray(target.size_m) ? target.size_m : [0.8, 0.6];
            const hw = Math.max(0.05, +size[0] || 0.8) / 2;
            const hh = Math.max(0.05, +size[1] || 0.6) / 2;
            const corners = [
                right.clone().multiplyScalar(-hw).addScaledVector(up, -hh),
                right.clone().multiplyScalar(hw).addScaledVector(up, -hh),
                right.clone().multiplyScalar(hw).addScaledVector(up, hh),
                right.clone().multiplyScalar(-hw).addScaledVector(up, hh),
            ];
            const geometry = new THREE.BufferGeometry();
            geometry.setAttribute('position', new THREE.Float32BufferAttribute([
                ...corners[0].toArray(), ...corners[1].toArray(), ...corners[2].toArray(),
                ...corners[0].toArray(), ...corners[2].toArray(), ...corners[3].toArray(),
            ], 3));
            geometry.computeVertexNormals();
            const plane = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({
                color, transparent: true, opacity: 0.12, side: THREE.DoubleSide,
                depthWrite: false,
            }));
            const outlinePoints = [...corners, corners[0]].map((p) => p.clone().addScaledVector(normal, 0.004));
            const outline = new THREE.Line(
                new THREE.BufferGeometry().setFromPoints(outlinePoints),
                new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.78, depthTest: false }));
            const cross = new THREE.LineSegments(
                new THREE.BufferGeometry().setFromPoints([
                    right.clone().multiplyScalar(-0.08), right.clone().multiplyScalar(0.08),
                    up.clone().multiplyScalar(-0.08), up.clone().multiplyScalar(0.08),
                ]),
                new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.88, depthTest: false }));
            plane.renderOrder = 29; outline.renderOrder = cross.renderOrder = 31;
            group.add(plane, outline, cross);
            visualMaterials.push(plane.material, outline.material, cross.material);
            pick = new THREE.Mesh(geometry.clone(), new THREE.MeshBasicMaterial({
                visible: false, side: THREE.DoubleSide,
            }));
        }
        pick.userData.targetId = target.id;
        group.add(pick);
        const tag = makeTagSprite(target.name || target.id, color);
        tag.position.copy(up).multiplyScalar((target.size_m?.[1] || 0.4) / 2 + 0.16)
            .addScaledVector(normal, 0.035);
        group.add(tag);
        return { group, pick, tag, target, color, visualMaterials };
    }

    function setTargets(targets) {
        clearDisposableGroup(targetsGroup);
        targetsById.clear();
        for (const target of targets || []) {
            if (!target?.id || !Array.isArray(target.pos)) continue;
            const rendered = buildTarget(target);
            targetsById.set(target.id, rendered);
            targetsGroup.add(rendered.group);
        }
        setTargetHover(hoveredTargetId, selectedTargetId);
    }

    function setTargetHover(targetId, selectedId = null) {
        hoveredTargetId = targetId;
        selectedTargetId = selectedId;
        for (const [id, rendered] of targetsById) {
            const active = id === targetId || id === selectedId;
            rendered.group.scale.setScalar(active ? 1.06 : 1);
            rendered.tag.material.opacity = active ? 1 : 0.16;
            rendered.tag.scale.set(active ? 1.5 : 0.92, active ? 0.282 : 0.18, 1);
            for (const material of rendered.visualMaterials) {
                if ('opacity' in material) {
                    const surface = material.side === THREE.DoubleSide && material.depthWrite === false;
                    material.opacity = active ? (surface ? 0.24 : 1) : (surface ? 0.055 : 0.3);
                }
            }
        }
    }

    /* ---------------- fixture tape calibration ---------------- */

    function surveyLine(a, b, opacity = 0.9) {
        const line = new THREE.Line(
            new THREE.BufferGeometry().setFromPoints([a, b]),
            new THREE.LineBasicMaterial({ color: SURVEY, transparent: true, opacity,
                depthTest: false }));
        line.renderOrder = 42;
        fixtureMeasureGroup.add(line);
        return line;
    }

    function surveyLabel(a, b, text) {
        const tag = makeTagSprite(text, SURVEY, { compact: true });
        tag.position.copy(a).lerp(b, 0.5).add(new THREE.Vector3(0, 0, 0.07));
        fixtureMeasureGroup.add(tag);
    }

    function feetInches(meters) {
        const total = meters / 0.0254;
        const feet = Math.floor(total / 12);
        return `${feet}′ ${(total - feet * 12).toFixed(1)}″`;
    }

    function setFixtureCalibration(device, zone) {
        clearDisposableGroup(fixtureMeasureGroup);
        const calibration = device?.fixture_calibration;
        if (!device || !Array.isArray(device.pos) || !calibration) return;
        const origin = new THREE.Vector3(...device.pos);
        const originRing = new THREE.Mesh(
            new THREE.RingGeometry(0.12, 0.155, 32),
            new THREE.MeshBasicMaterial({ color: SURVEY, transparent: true, opacity: 1,
                side: THREE.DoubleSide, depthTest: false }));
        originRing.position.copy(origin);
        originRing.renderOrder = 43;
        fixtureMeasureGroup.add(originRing);

        const ceiling = calibration.floor_to_ceiling_m;
        if (Number.isFinite(ceiling)) {
            const floor = new THREE.Vector3(origin.x, origin.y, 0);
            const top = new THREE.Vector3(origin.x, origin.y, ceiling);
            surveyLine(floor, top, 0.34);
            surveyLabel(floor, top, `floor → ceiling ${feetInches(ceiling)}`);
            if (Number.isFinite(calibration.ceiling_to_fixture_bottom_m)) {
                surveyLine(top, origin, 1);
                surveyLabel(top, origin, `drop ${feetInches(calibration.ceiling_to_fixture_bottom_m)}`);
            }
        }

        const poly = zone?.floor_polygon || [];
        if (poly.length >= 3) {
            const xs = poly.map((p) => +p[0]);
            const ys = poly.map((p) => +p[1]);
            const bounds = {
                west: Math.min(...xs), east: Math.max(...xs),
                south: Math.min(...ys), north: Math.max(...ys),
            };
            for (const measurement of calibration.wall_distances || []) {
                if (!Number.isFinite(measurement?.distance_m) || !(measurement.wall in bounds)) continue;
                const endpoint = origin.clone();
                if (measurement.wall === 'west' || measurement.wall === 'east') endpoint.x = bounds[measurement.wall];
                else endpoint.y = bounds[measurement.wall];
                surveyLine(origin, endpoint, 0.86);
                surveyLabel(origin, endpoint,
                    `${measurement.wall} ${feetInches(measurement.distance_m)}`);
            }
        }
    }

    /* ---------------- engineered fixture beam preview ---------------- */

    function approximateWhite(kelvin) {
        if (!Number.isFinite(+kelvin)) return new THREE.Color(0xffddb0);
        const t = Math.max(1000, Math.min(40000, +kelvin)) / 100;
        const red = t <= 66 ? 255 : 329.698727446 * Math.pow(t - 60, -0.1332047592);
        const green = t <= 66
            ? 99.4708025861 * Math.log(t) - 161.1195681661
            : 288.1221695283 * Math.pow(t - 60, -0.0755148492);
        const blue = t >= 66 ? 255 : t <= 19 ? 0 : 138.5177312231 * Math.log(t - 10) - 305.0447927307;
        return new THREE.Color(
            THREE.MathUtils.clamp(red, 0, 255) / 255,
            THREE.MathUtils.clamp(green, 0, 255) / 255,
            THREE.MathUtils.clamp(blue, 0, 255) / 255,
        );
    }

    function beamColor(kind, state, kelvin) {
        if (kind === 'preview') return OPTICAL_CYAN.clone();
        if (state === 'simulated') return OPTICAL_CYAN.clone();
        if (state === 'stale' || state === 'unknown' || state === 'unavailable') return STALE_BEAM.clone();
        return approximateWhite(kelvin); // qualitative screen approximation, not spectral colorimetry
    }

    function dashedCone(origin, direction, distance, radius, color, opacity) {
        const end = origin.clone().addScaledVector(direction, distance);
        const basis = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), direction);
        const material = new THREE.LineDashedMaterial({ color, dashSize: 0.075, gapSize: 0.055,
            transparent: true, opacity, depthTest: false });
        for (let i = 0; i < 8; i += 1) {
            const angle = (i / 8) * Math.PI * 2;
            const edge = new THREE.Vector3(Math.cos(angle) * radius, Math.sin(angle) * radius, 0)
                .applyQuaternion(basis).add(end);
            const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([origin, edge]), material.clone());
            line.computeLineDistances(); line.renderOrder = 38; aimBeamGroup.add(line);
        }
        for (const fraction of [0.34, 0.67, 1]) {
            const ring = new THREE.LineLoop(
                new THREE.CircleGeometry(radius * fraction, 48).deleteAttribute('normal').deleteAttribute('uv'),
                material.clone(),
            );
            const positions = ring.geometry.attributes.position;
            const points = [];
            for (let i = 1; i < positions.count; i += 1) points.push(new THREE.Vector3().fromBufferAttribute(positions, i));
            ring.geometry.dispose();
            ring.geometry = new THREE.BufferGeometry().setFromPoints(points);
            ring.position.copy(origin).addScaledVector(direction, distance * fraction);
            ring.quaternion.copy(basis); ring.computeLineDistances(); ring.renderOrder = 38;
            aimBeamGroup.add(ring);
        }
    }

    function addVolumeCone(origin, direction, distance, radius, color, opacity) {
        const layers = [
            { radius: radius * 1.02, opacity: opacity * 0.42 },
            { radius: radius * 0.72, opacity: opacity * 0.72 },
            { radius: radius * 0.38, opacity },
        ];
        for (const layer of layers) {
            const cone = new THREE.Mesh(
                new THREE.ConeGeometry(layer.radius, distance, 64, 1, true),
                new THREE.MeshBasicMaterial({ color, transparent: true, opacity: layer.opacity,
                    side: THREE.DoubleSide, depthWrite: false, depthTest: true,
                    blending: THREE.AdditiveBlending }),
            );
            cone.position.copy(origin).addScaledVector(direction, distance / 2);
            cone.quaternion.setFromUnitVectors(new THREE.Vector3(0, -1, 0), direction);
            cone.renderOrder = 18; aimBeamGroup.add(cone);
        }
    }

    function addVolumeToFootprint(origin, footprint, color, opacity) {
        const boundary = (footprint?.points || []).map((point) => new THREE.Vector3(...point));
        if (boundary.length < 3) return false;
        const center = Array.isArray(footprint.point)
            ? new THREE.Vector3(...footprint.point)
            : boundary.reduce((sum, point) => sum.add(point), new THREE.Vector3()).multiplyScalar(1 / boundary.length);
        const layers = [
            { scale: 1.02, opacity: opacity * 0.42 },
            { scale: 0.72, opacity: opacity * 0.72 },
            { scale: 0.38, opacity },
        ];
        for (const layer of layers) {
            const positions = [];
            const scaled = boundary.map((point) => center.clone().addScaledVector(point.clone().sub(center), layer.scale));
            for (let index = 0; index < scaled.length; index += 1) {
                const next = (index + 1) % scaled.length;
                positions.push(origin.x, origin.y, origin.z,
                    scaled[index].x, scaled[index].y, scaled[index].z,
                    scaled[next].x, scaled[next].y, scaled[next].z);
            }
            const geometry = new THREE.BufferGeometry();
            geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
            geometry.computeVertexNormals();
            const volume = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({
                color, transparent: true, opacity: layer.opacity, side: THREE.DoubleSide,
                depthWrite: false, depthTest: true, blending: THREE.AdditiveBlending,
            }));
            volume.renderOrder = 18; aimBeamGroup.add(volume);
        }
        return true;
    }

    let aimTransitionFrame = 0;
    let aimTransitionCancel = null;

    function animateAimTransition(spec, onComplete) {
        aimTransitionCancel?.();
        clearDisposableGroup(aimTransitionGroup);
        if (!spec || !Array.isArray(spec.origin) || !Array.isArray(spec.from_direction)
                || !Array.isArray(spec.to_direction)) {
            onComplete?.(); return () => {};
        }
        const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
        const duration = reduced ? 0 : Math.max(180, +(spec.duration_ms || 880));
        if (!duration) { onComplete?.(); return () => {}; }

        const origin = new THREE.Vector3(...spec.origin);
        const fromDirection = new THREE.Vector3(...spec.from_direction).normalize();
        const toDirection = new THREE.Vector3(...spec.to_direction).normalize();
        const fromQ = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, -1, 0), fromDirection);
        const toQ = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, -1, 0), toDirection);
        const fromDistance = Math.max(0.05, +(spec.from_distance_m || 1));
        const toDistance = Math.max(0.05, +(spec.to_distance_m || 1));
        const halfAngle = THREE.MathUtils.degToRad(Math.max(0.1, Math.min(179, +(spec.full_fwhm_deg || 20))) / 2);
        const color = approximateWhite(spec.color_temp_kelvin);
        const pivot = new THREE.Group();
        pivot.position.copy(origin); aimTransitionGroup.add(pivot);
        const layers = [
            { scale: 1.02, opacity: 0.045 },
            { scale: 0.72, opacity: 0.075 },
            { scale: 0.38, opacity: 0.11 },
        ];
        const volumes = layers.map((layer) => {
            const volume = new THREE.Mesh(new THREE.ConeGeometry(1, 1, 64, 1, true),
                new THREE.MeshBasicMaterial({ color, transparent: true, opacity: layer.opacity,
                    side: THREE.DoubleSide, depthWrite: false, depthTest: true,
                    blending: THREE.AdditiveBlending }));
            volume.position.set(0, -0.5, 0); volume.renderOrder = 44; pivot.add(volume);
            return { volume, layer };
        });
        let cancelled = false;
        const cancel = () => {
            cancelled = true;
            if (aimTransitionFrame) cancelAnimationFrame(aimTransitionFrame);
            aimTransitionFrame = 0;
            clearDisposableGroup(aimTransitionGroup);
            if (aimTransitionCancel === cancel) aimTransitionCancel = null;
        };
        aimTransitionCancel = cancel;
        const started = performance.now();
        const tick = (now) => {
            if (cancelled) return;
            const progress = Math.min(1, (now - started) / duration);
            const eased = progress < 0.5 ? 4 * progress * progress * progress
                : 1 - Math.pow(-2 * progress + 2, 3) / 2;
            pivot.quaternion.slerpQuaternions(fromQ, toQ, eased);
            const distance = THREE.MathUtils.lerp(fromDistance, toDistance, eased);
            const radius = distance * Math.tan(halfAngle);
            for (const { volume, layer } of volumes) {
                volume.position.y = -distance / 2;
                volume.scale.set(radius * layer.scale, distance, radius * layer.scale);
            }
            if (progress < 1) aimTransitionFrame = requestAnimationFrame(tick);
            else {
                aimTransitionFrame = 0;
                clearDisposableGroup(aimTransitionGroup);
                if (aimTransitionCancel === cancel) aimTransitionCancel = null;
                onComplete?.();
            }
        };
        aimTransitionFrame = requestAnimationFrame(tick);
        return cancel;
    }

    function addMirrorWash(spec, color, selected) {
        const origin = new THREE.Vector3(...spec.origin);
        const mirror = origin.clone().add(new THREE.Vector3(0, 0, +(spec.mirror_height_m || 0.2)));
        const up = new THREE.Vector3(0, 0, 1);
        addVolumeCone(origin, up, origin.distanceTo(mirror), 0.055, color, selected ? 0.18 : 0.08);
        const cap = new THREE.Mesh(new THREE.SphereGeometry(0.085, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2),
            new THREE.MeshPhysicalMaterial({ color: 0xf5f7fb, metalness: 0.86, roughness: 0.14,
                transparent: true, opacity: selected ? 0.82 : 0.46, depthWrite: false }));
        cap.position.copy(mirror); cap.rotation.x = Math.PI; cap.renderOrder = 29; aimBeamGroup.add(cap);
        const downDistance = Math.max(0.3, mirror.z - 0.04);
        const washRadius = Math.min(1.7, downDistance * Math.tan(THREE.MathUtils.degToRad(34)));
        addVolumeCone(mirror, new THREE.Vector3(0, 0, -1), downDistance, washRadius, color,
            selected ? 0.045 : 0.018);
        const washTexture = glowTex.clone(); washTexture.needsUpdate = true;
        const pool = new THREE.Mesh(new THREE.CircleGeometry(washRadius, 64), new THREE.MeshBasicMaterial({
            map: washTexture, color, transparent: true, opacity: selected ? 0.18 : 0.075,
            blending: THREE.AdditiveBlending, depthWrite: false,
        }));
        pool.position.set(mirror.x, mirror.y, 0.035); pool.renderOrder = 19; aimBeamGroup.add(pool);
    }

    function addAimRay(spec, kind, selected = false) {
        if (!spec || !Array.isArray(spec.origin) || !Array.isArray(spec.direction)
                || !(spec.distance_m > 0)) return;
        const origin = new THREE.Vector3(...spec.origin);
        const direction = new THREE.Vector3(...spec.direction).normalize();
        const distance = +spec.distance_m;
        const fullFwhm = Math.max(0.1, Math.min(179, +(spec.full_fwhm_deg || 20)));
        const radius = distance * Math.tan(THREE.MathUtils.degToRad(fullFwhm / 2));
        const state = spec.state || 'on';
        const color = spec.mode === 'mirror_bounce'
            ? approximateWhite(spec.color_temp_kelvin)
            : beamColor(kind, state, spec.color_temp_kelvin);
        if (spec.mode === 'mirror_bounce') {
            addMirrorWash(spec, color, selected); return;
        }
        const qualitativeFill = kind === 'preview'
            || (state !== 'off' && state !== 'unknown' && state !== 'simulated');

        if (qualitativeFill) {
            const opacity = kind === 'preview' ? 0.11 : state === 'stale' ? 0.018 : selected ? 0.075 : 0.028;
            if (!(spec.surface_aligned && spec.footprint?.kind === 'ellipse'
                    && addVolumeToFootprint(origin, spec.footprint, color, opacity))) {
                addVolumeCone(origin, direction, distance, radius, color, opacity);
            }
        } else if (state === 'off' || state === 'simulated') {
            dashedCone(origin, direction, distance, radius, color,
                state === 'simulated' ? (selected ? 0.58 : 0.24) : (selected ? 0.72 : 0.34));
        }

        const axisMaterial = state === 'off' || state === 'simulated'
            ? new THREE.LineDashedMaterial({ color, dashSize: 0.08, gapSize: 0.055,
                transparent: true, opacity: state === 'simulated'
                    ? (selected ? 0.58 : 0.24) : (selected ? 0.72 : 0.32), depthTest: false })
            : new THREE.LineBasicMaterial({ color, transparent: true,
                opacity: kind === 'preview' ? 0.8 : state === 'stale' ? 0.25 : selected ? 0.52 : 0.18,
                depthTest: false });
        const axis = new THREE.Line(
            new THREE.BufferGeometry().setFromPoints([origin, origin.clone().addScaledVector(direction, distance)]),
            axisMaterial,
        );
        axis.computeLineDistances?.();
        axis.renderOrder = 39;
        aimBeamGroup.add(axis);

        // This ring is the exact half-maximum contour implied by configured
        // full FWHM. The cone fill is only qualitative.
        if (state !== 'off' && !(spec.surface_aligned && spec.footprint?.points?.length >= 3)) {
            const contour = new THREE.Mesh(
                new THREE.RingGeometry(Math.max(0.001, radius - 0.009), radius + 0.009, 64),
                new THREE.MeshBasicMaterial({ color, transparent: true,
                    opacity: kind === 'preview' ? 0.8 : selected ? 0.58 : 0.22,
                    side: THREE.DoubleSide, depthTest: false }),
            );
            contour.position.copy(origin).addScaledVector(direction, distance);
            contour.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), direction);
            contour.renderOrder = 40; aimBeamGroup.add(contour);
        }

        const footprint = spec.footprint;
        if (Array.isArray(footprint?.points) && footprint.points.length >= 2) {
            const points = footprint.points.map((p) => new THREE.Vector3(...p));
            const line = footprint.kind === 'partial'
                ? new THREE.Line(new THREE.BufferGeometry().setFromPoints(points),
                    new THREE.LineDashedMaterial({ color, dashSize: 0.08, gapSize: 0.045,
                        transparent: true, opacity: 0.9, depthTest: false }))
                : new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(points),
                    new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.9,
                        depthTest: false }));
            line.computeLineDistances?.();
            line.renderOrder = 41;
            aimBeamGroup.add(line);
        }

        if (Array.isArray(spec.obstruction_point)) {
            const marker = new THREE.Mesh(
                new THREE.SphereGeometry(0.065, 16, 12),
                new THREE.MeshBasicMaterial({ color: 0xff625f, depthTest: false }),
            );
            marker.position.set(...spec.obstruction_point);
            marker.renderOrder = 42;
            aimBeamGroup.add(marker);
        }
    }

    let aimRadialPickObjects = [];

    function addRadialHalo(spec, selected) {
        const origin = spec.origin;
        if (!Array.isArray(origin)) return;
        for (const radial of spec.radial || []) {
            const angle = THREE.MathUtils.degToRad(90 - (+radial.angle_deg || 0));
            const width = THREE.MathUtils.degToRad(48);
            const color = approximateWhite(radial.color_temp_kelvin || spec.color_temp_kelvin || 3000);
            const wedge = new THREE.Mesh(
                new THREE.RingGeometry(0.16, selected ? 0.45 : 0.37, 22, 1, angle - width / 2, width),
                new THREE.MeshBasicMaterial({ color: radial.active ? color : STALE_BEAM,
                    transparent: true, opacity: radial.active ? (selected ? 0.42 : 0.17) : (selected ? 0.14 : 0.055),
                    side: THREE.DoubleSide, depthTest: false, depthWrite: false,
                    blending: radial.active ? THREE.AdditiveBlending : THREE.NormalBlending }),
            );
            wedge.position.set(origin[0], origin[1], origin[2] - 0.035);
            wedge.userData = { kind: 'aim-radial-zone', fixtureId: spec.fixture_id, zoneNumber: radial.number };
            wedge.renderOrder = 40; aimBeamGroup.add(wedge); aimRadialPickObjects.push(wedge);
        }
    }

    function setAimBeams(specs, selectedFixtureId = null) {
        clearDisposableGroup(aimBeamGroup);
        aimRadialPickObjects = [];
        for (const spec of specs || []) {
        if (!spec) continue;
        const selected = !selectedFixtureId || spec.fixture_id === selectedFixtureId;
        addAimRay(spec.current, 'current', selected);
        addAimRay(spec.preview, 'preview', true);
        const origin = spec.origin;
        if (Array.isArray(origin)) {
            const aimingOrigin = new THREE.Mesh(
                new THREE.RingGeometry(0.075, 0.102, 32),
                new THREE.MeshBasicMaterial({ color: selected ? OPTICAL_CYAN : approximateWhite(spec.color_temp_kelvin),
                    transparent: true, opacity: selected ? 1 : 0.32, side: THREE.DoubleSide, depthTest: false }),
            );
            aimingOrigin.position.set(...origin);
            aimingOrigin.renderOrder = 42;
            aimBeamGroup.add(aimingOrigin);
        }
        addRadialHalo(spec, selected);
        }
    }

    function setAimBeam(spec) {
        setAimBeams(spec ? [spec] : [], spec?.fixture_id || null);
    }

    /* live HA state -> visuals */
    function setDeviceState(deviceId, haState) {
        const m = markersById.get(deviceId);
        if (!m) return;
        m.state = haState;
        const st = haState?.state;
        const attrs = haState?.attributes || {};
        if (m.device.type === 'light' || m.device.ha_entity_id?.startsWith('switch.')) {
            const on = st === 'on';
            if (on) {
                let c = TYPE_COLOR.light.clone();
                if (Array.isArray(attrs.rgb_color)) {
                    c = new THREE.Color(attrs.rgb_color[0] / 255, attrs.rgb_color[1] / 255, attrs.rgb_color[2] / 255);
                } else if (attrs.color_temp_kelvin) {
                    const k = attrs.color_temp_kelvin;
                    c = new THREE.Color().setHSL(0.085, 0.8, Math.min(0.75, 0.5 + (3500 - Math.min(k, 3500)) / 7000));
                }
                const bri = (attrs.brightness ?? 255) / 255;
                m.core.material.color.copy(c);
                m.glow.material.color.copy(c);
                m.glow.material.opacity = 0.25 + 0.55 * bri;
                m.glow.scale.setScalar(0.5 + 0.9 * bri);
                if (m.device.type === 'light') {
                    const disc = getGlowDisc(m);
                    disc.material.color.copy(c);
                    disc.material.opacity = bri * 0.18;
                    disc.visible = true;
                }
            } else {
                m.core.material.color.set(0x444a55);
                m.glow.material.opacity = 0;
                const disc = glowDiscsById.get(deviceId);
                if (disc) { disc.visible = false; disc.material.opacity = 0; }
            }
        } else if (m.device.type === 'speaker' || m.device.type === 'tv' || m.device.type === 'amp') {
            m.userData_playing = st === 'playing';
            m.glow.material.opacity = st === 'playing' ? 0.4 : 0;
            m.core.material.color.copy(st === 'playing' || st === 'on'
                ? TYPE_COLOR[m.device.type] : new THREE.Color(0x444a55));
        }
    }

    /* ---------------- zones ---------------- */

    let zoneEditState = { zoneId: null, active: false, vertexIndex: null };

    function zoneLineGeometry(poly) {
        const pts = (poly || []).map(([x, y]) => new THREE.Vector3(x, y, 0.025));
        if (pts.length) pts.push(pts[0].clone());
        return new THREE.BufferGeometry().setFromPoints(pts);
    }

    function zoneFillGeometry(poly) {
        const shape = new THREE.Shape();
        (poly || []).forEach(([x, y], index) => {
            if (index === 0) shape.moveTo(x, y);
            else shape.lineTo(x, y);
        });
        if ((poly || []).length) shape.closePath();
        return new THREE.ShapeGeometry(shape);
    }

    function disposeZoneEntry(entry) {
        entry.group.traverse((object) => {
            object.geometry?.dispose?.();
            if (Array.isArray(object.material)) object.material.forEach((m) => m.dispose?.());
            else object.material?.dispose?.();
        });
        zonesGroup.remove(entry.group);
    }

    function setZones(zones) {
        for (const entry of zonesById.values()) disposeZoneEntry(entry);
        zonesById.clear();
        for (const z of zones || []) {
            const poly = z.floor_polygon;
            if (!Array.isArray(poly) || poly.length < 3) continue;
            const group = new THREE.Group();
            group.userData.zoneId = z.id;
            const color = z.color ? new THREE.Color(z.color) : ICE;
            const fillMat = new THREE.MeshBasicMaterial({
                color, transparent: true, opacity: 0, depthWrite: false,
                side: THREE.DoubleSide,
            });
            const fill = new THREE.Mesh(zoneFillGeometry(poly), fillMat);
            fill.position.z = 0.012;
            fill.userData.zoneId = z.id;
            fill.userData.zonePart = 'body';
            fill.renderOrder = 18;
            const mat = new THREE.LineBasicMaterial({
                color,
                transparent: true, opacity: 0.0,
            });
            const line = new THREE.Line(zoneLineGeometry(poly), mat);
            line.renderOrder = 20;
            const handles = new THREE.Group();
            handles.visible = false;
            poly.forEach(([x, y], vertexIndex) => {
                const handle = new THREE.Mesh(
                    new THREE.SphereGeometry(0.105, 14, 10),
                    new THREE.MeshBasicMaterial({
                        color, transparent: true, opacity: 0.96,
                        depthTest: false, depthWrite: false,
                    }),
                );
                handle.position.set(x, y, 0.065);
                handle.renderOrder = 24;
                handle.userData.zoneId = z.id;
                handle.userData.zonePart = 'vertex';
                handle.userData.zoneVertexIndex = vertexIndex;
                handles.add(handle);
            });
            group.add(fill, line, handles);
            zonesGroup.add(group);
            zonesById.set(z.id, { group, fill, fillMat, line, mat, handles, base: 0, editSelected: false });
        }
        setZoneEdit(zoneEditState.zoneId, zoneEditState.active, zoneEditState.vertexIndex);
    }

    function setZonesVisible(opacity) {
        for (const z of zonesById.values()) { z.base = opacity; z.mat.opacity = opacity; }
    }

    function setZoneEdit(zoneId, active, vertexIndex = null) {
        zoneEditState = {
            zoneId: zoneId || null,
            active: !!active,
            vertexIndex: Number.isInteger(vertexIndex) ? vertexIndex : null,
        };
        for (const [id, z] of zonesById) {
            const selected = !!active && id === zoneId;
            z.editSelected = selected;
            z.handles.visible = selected;
            z.handles.children.forEach((handle, index) => {
                const selectedVertex = selected && index === zoneEditState.vertexIndex;
                handle.scale.setScalar(selectedVertex ? 1.35 : 1);
                handle.material.color.copy(selectedVertex ? new THREE.Color(0xffffff) : z.line.material.color);
            });
            z.fillMat.opacity = active ? (selected ? 0.13 : 0.025) : 0;
            z.mat.opacity = selected ? 1 : (z.base || 0);
        }
    }

    function previewZone(zoneId, poly) {
        const z = zonesById.get(zoneId);
        if (!z || !Array.isArray(poly) || poly.length < 3) return;
        z.line.geometry.dispose();
        z.line.geometry = zoneLineGeometry(poly);
        z.fill.geometry.dispose();
        z.fill.geometry = zoneFillGeometry(poly);
        z.handles.children.forEach((handle, index) => {
            const point = poly[index];
            if (point) handle.position.set(point[0], point[1], 0.065);
        });
    }

    function setZoneDraft(poly) {
        while (zoneDraftGroup.children.length) {
            const child = zoneDraftGroup.children[0];
            zoneDraftGroup.remove(child);
            child.geometry?.dispose?.();
            if (Array.isArray(child.material)) child.material.forEach((material) => material.dispose?.());
            else child.material?.dispose?.();
        }
        const points = (poly || []).map(([x, y]) => new THREE.Vector3(x, y, 0.075));
        if (points.length >= 2) {
            const line = new THREE.Line(
                new THREE.BufferGeometry().setFromPoints(points),
                new THREE.LineBasicMaterial({ color: ICE, transparent: true, opacity: 0.95, depthTest: false }),
            );
            line.renderOrder = 25;
            zoneDraftGroup.add(line);
        }
        for (const point of points) {
            const handle = new THREE.Mesh(
                new THREE.SphereGeometry(0.085, 12, 8),
                new THREE.MeshBasicMaterial({ color: ICE, depthTest: false, depthWrite: false }),
            );
            handle.position.copy(point);
            handle.renderOrder = 26;
            zoneDraftGroup.add(handle);
        }
    }

    /* ---------------- person dot + room pulse ---------------- */

    const disc = new THREE.Mesh(
        new THREE.CircleGeometry(0.12, 32),
        new THREE.MeshBasicMaterial({ color: ICE, transparent: true, opacity: 0.95 }),
    );
    const ring = new THREE.Mesh(
        new THREE.RingGeometry(0.95, 1.0, 48),
        new THREE.MeshBasicMaterial({ color: ICE, transparent: true, opacity: 0.3, side: THREE.DoubleSide }),
    );
    const hair = new THREE.Mesh(
        new THREE.CylinderGeometry(0.004, 0.004, 1.4, 6),
        new THREE.MeshBasicMaterial({ color: ICE, transparent: true, opacity: 0.5 }),
    );
    hair.rotation.x = Math.PI / 2;
    hair.position.z = 0.7;
    disc.position.z = 0.02;
    ring.position.z = 0.025;
    personGroup.add(disc, ring, hair);
    personGroup.visible = false;

    // spring-damped dot (critically damped, ω≈3.5) + speed clamp 2.5 m/s
    const spring = { x: 0, y: 0, vx: 0, vy: 0, tx: 0, ty: 0, has: false };
    let pulseRoom = null;       // zone id pulsing for room-level presence
    let pulseT = 0;

    function setPerson(track) {
        if (!track) {
            personGroup.visible = false;
            pulseRoom = null;
            return;
        }
        if (track.pos) {
            if (!spring.has) {
                spring.x = track.pos[0]; spring.y = track.pos[1];
                spring.vx = 0; spring.vy = 0; spring.has = true;
            }
            spring.tx = track.pos[0]; spring.ty = track.pos[1];
            personGroup.visible = true;
            pulseRoom = null;
            const sigma = track.sigma_m ?? (track.cov
                ? Math.sqrt(Math.max(track.cov[0][0], track.cov[1][1])) : 0.3);
            ring.scale.setScalar(Math.max(0.18, 2 * sigma));
        } else {
            personGroup.visible = false;
            spring.has = false;
            pulseRoom = track.room || null;
        }
    }

    let pulse = 0;

    /* calibration correspondence markers: numbered bright spheres + floor
       hairlines, managed here because the UI layer has no THREE access */
    const calibGroup = new THREE.Group();
    calibGroup.renderOrder = 40;
    overlayRoot.add(calibGroup);
    function setCalibMarkers(points) {
        while (calibGroup.children.length) {
            const c = calibGroup.children[0];
            calibGroup.remove(c);
            if (c.geometry) c.geometry.dispose();
            if (c.material) c.material.dispose();
        }
        for (const p of points || []) {
            const s = new THREE.Mesh(
                new THREE.SphereGeometry(0.045, 12, 10),
                new THREE.MeshBasicMaterial({ color: 0x22ff88, depthTest: false,
                                              transparent: true, opacity: 0.95 }));
            s.position.set(p[0], p[1], p[2]);
            s.renderOrder = 41;
            calibGroup.add(s);
            const lg = new THREE.BufferGeometry().setFromPoints([
                new THREE.Vector3(p[0], p[1], 0),
                new THREE.Vector3(p[0], p[1], p[2])]);
            const ln = new THREE.Line(lg, new THREE.LineBasicMaterial(
                { color: 0x22ff88, transparent: true, opacity: 0.55, depthTest: false }));
            ln.renderOrder = 41;
            calibGroup.add(ln);
        }
    }

    function moveCalibMarker(i, xyz) {
        const s = calibGroup.children[i * 2];
        const ln = calibGroup.children[i * 2 + 1];
        if (!s) return;
        s.position.set(xyz[0], xyz[1], xyz[2]);
        if (ln) {
            const pos = ln.geometry.attributes.position;
            pos.setXYZ(0, xyz[0], xyz[1], 0);
            pos.setXYZ(1, xyz[0], xyz[1], xyz[2]);
            pos.needsUpdate = true;
        }
    }

    return {
        devicesGroup, camerasGroup, personGroup, zonesGroup, targetsGroup,
        markersById, zonesById, setCalibMarkers, calibGroup, moveCalibMarker,
        targetsById, setTargets, setTargetHover, setFixtureCalibration, setAimBeam, setAimBeams,
        animateAimTransition, aimBeamGroup, aimTransitionGroup,
        setDevices, setDeviceState, setZones, setZonesVisible, setZoneEdit,
        previewZone, setZoneDraft, setPerson,
        pickObjects() { return [...markersById.values()].map((m) => m.pick); },
        targetPickObjects() { return [...targetsById.values()].map((m) => m.pick); },
        aimRadialPickObjects() { return aimRadialPickObjects; },
        zonePickObjects() {
            return [...zonesById.values()].flatMap((z) =>
                z.handles.visible ? [z.fill, ...z.handles.children] : [z.fill]);
        },
        setHover(deviceId) {
            let hovered = null;
            for (const [id, m] of markersById) {
                const hov = id === deviceId;
                if (hov) hovered = m;
                m.core.scale.setScalar(hov ? 1.45 : 1.0);
                m.ghost.scale.setScalar(hov ? 1.45 : 1.0);
            }
            // pulsing floor ring under any hovered marker (incl. 'other'/proposals)
            if (hovered) {
                hoverRing.position.set(hovered.device.pos[0], hovered.device.pos[1], 0.03);
                hoverRing.material.color.copy(
                    TYPE_COLOR[hovered.device.type] || TYPE_COLOR.other);
                hoverRing.scale.setScalar(1.0);
                hoverRing.visible = true;
            } else {
                hoverRing.visible = false;
            }
        },
        update(dt) {
            pulse += dt;
            // dot spring (critically damped: a = ω²(t−x) − 2ω·v), clamp 2.5 m/s
            if (personGroup.visible && spring.has) {
                const w = 3.5;
                for (const ax of ['x', 'y']) {
                    const v = ax === 'x' ? 'vx' : 'vy', t = ax === 'x' ? 'tx' : 'ty';
                    const acc = w * w * (spring[t] - spring[ax]) - 2 * w * spring[v];
                    spring[v] += acc * dt;
                }
                const sp = Math.hypot(spring.vx, spring.vy);
                if (sp > 2.5) { spring.vx *= 2.5 / sp; spring.vy *= 2.5 / sp; }
                spring.x += spring.vx * dt;
                spring.y += spring.vy * dt;
                personGroup.position.set(spring.x, spring.y, 0);
                disc.material.opacity = 0.6 + 0.4 * (0.5 + 0.5 * Math.sin(pulse * 2 * Math.PI * 0.8));
            }
            // room pulse for room-level presence (pos unknown — honest state)
            for (const [id, z] of zonesById) {
                z.mat.opacity = z.editSelected ? 1 : (id === pulseRoom)
                    ? 0.25 + 0.35 * (0.5 + 0.5 * Math.sin(pulse * 2 * Math.PI * 0.5))
                    : (z.base || 0);
            }
            // media pulse rings
            for (const m of markersById.values()) {
                if (m.userData_playing) {
                    const k = 0.5 + 0.5 * Math.sin(pulse * 2 * Math.PI / 1.2);
                    m.glow.scale.setScalar(0.8 + 0.5 * k);
                }
            }
            // hover floor ring pulse
            if (hoverRing.visible) {
                const k = 0.5 + 0.5 * Math.sin(pulse * 2 * Math.PI * 1.4);
                hoverRing.material.opacity = 0.35 + 0.4 * k;
                hoverRing.scale.setScalar(1.0 + 0.12 * k);
            }
        },
    };
}
