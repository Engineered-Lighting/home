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
const TYPE_COLOR = {
    light: new THREE.Color(0xffe2a8),
    speaker: new THREE.Color(0xa8ffd8),
    tv: new THREE.Color(0xd8a8ff),
    amp: new THREE.Color(0xd8a8ff),
    camera: new THREE.Color(0xa8c8ff),
    other: new THREE.Color(0xcccccc),
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

export function createOverlay(apartmentRoot) {
    const overlayRoot = new THREE.Group();
    overlayRoot.renderOrder = 10;
    apartmentRoot.add(overlayRoot);

    const devicesGroup = new THREE.Group();
    const camerasGroup = new THREE.Group();
    const personGroup = new THREE.Group();
    const zonesGroup = new THREE.Group();
    overlayRoot.add(devicesGroup, camerasGroup, personGroup, zonesGroup);

    const glowTex = makeGlowTexture();
    const markersById = new Map();   // device id -> {group, core, ghost, glow, pick, device}
    const zonesById = new Map();     // zone id -> {line, mat, basePoints}

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
        for (const d of devices || []) {
            if (!Array.isArray(d.pos)) continue;
            const m = buildMarker(d);
            markersById.set(d.id, m);
            devicesGroup.add(m.group);
        }
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
            } else {
                m.core.material.color.set(0x444a55);
                m.glow.material.opacity = 0;
            }
        } else if (m.device.type === 'speaker' || m.device.type === 'tv' || m.device.type === 'amp') {
            m.userData_playing = st === 'playing';
            m.glow.material.opacity = st === 'playing' ? 0.4 : 0;
            m.core.material.color.copy(st === 'playing' || st === 'on'
                ? TYPE_COLOR[m.device.type] : new THREE.Color(0x444a55));
        }
    }

    /* ---------------- zones ---------------- */

    function setZones(zones) {
        for (const { line } of zonesById.values()) zonesGroup.remove(line);
        zonesById.clear();
        for (const z of zones || []) {
            const poly = z.floor_polygon;
            if (!Array.isArray(poly) || poly.length < 3) continue;
            const pts = poly.map(([x, y]) => new THREE.Vector3(x, y, 0.02));
            pts.push(pts[0].clone());
            const geo = new THREE.BufferGeometry().setFromPoints(pts);
            const mat = new THREE.LineBasicMaterial({
                color: z.color ? new THREE.Color(z.color) : ICE,
                transparent: true, opacity: 0.0,
            });
            const line = new THREE.Line(geo, mat);
            zonesGroup.add(line);
            zonesById.set(z.id, { line, mat });
        }
    }

    function setZonesVisible(opacity) {
        for (const z of zonesById.values()) { z.base = opacity; z.mat.opacity = opacity; }
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
                new THREE.SphereGeometry(0.14, 14, 12),
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

    return {
        devicesGroup, camerasGroup, personGroup, zonesGroup,
        markersById, zonesById, setCalibMarkers,
        setDevices, setDeviceState, setZones, setZonesVisible, setPerson,
        pickObjects() { return [...markersById.values()].map((m) => m.pick); },
        setHover(deviceId) {
            for (const [id, m] of markersById) {
                const hov = id === deviceId;
                m.core.scale.setScalar(hov ? 1.45 : 1.0);
                m.ghost.scale.setScalar(hov ? 1.45 : 1.0);
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
                z.mat.opacity = (id === pulseRoom)
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
        },
    };
}
