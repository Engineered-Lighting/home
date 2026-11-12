/* The live den view's people layer: one floor marker per track in den-live's people stream (den-live-people-1).
 * Presentation only. Positions arrive in the apartment frame (z up, metres, floor at 0), the frame the camera is
 * registered to, and are drawn as Home's person marker is: a disc, a 2-sigma ring, and Home's own critically damped
 * spring (omega 3.5, at most 2.5 m/s). It adds a facing arrow when the stream gives a heading, a trail of the last
 * TRAIL_S, and dims coasting tracks. A track gone from the stream fades over FADE_S after GRACE_S and is removed.
 * This layer has no network, identity, lighting or model API: whoever holds the stream calls update(). */
import * as THREE from 'three';

export const PEOPLE_SCHEMA = 'den-live-people-1';
const FADE_S = 2, GRACE_S = .5, TRAIL_S = 10, TRAIL_POINTS = 50, SPRING_W = 3.5, MAX_SPEED = 2.5, COASTING = .45;
const ICE = new THREE.Color(0xb8d8ff);
const finite = value => typeof value === 'number' && Number.isFinite(value);
const validId = value => typeof value === 'string' && value.length > 0 && value.length <= 64;

/* The event's own checks: anything else is ignored whole, never drawn in part. */
export function validPeopleEvent(event) {
    return !!event && event.schema === PEOPLE_SCHEMA && Array.isArray(event.tracks) && event.tracks.length <= 32
        && event.tracks.every(track => track && validId(track.track_id) && ['confirmed', 'coasting'].includes(track.state)
            && Array.isArray(track.xy_m) && track.xy_m.length === 2 && track.xy_m.every(v => finite(v) && Math.abs(v) <= 100)
            && (track.sigma_m == null || finite(track.sigma_m) && track.sigma_m >= 0)
            && (track.heading_deg == null || finite(track.heading_deg)));
}

function release(root) {
    root.traverse(node => {
        node.geometry?.dispose?.();
        for (const material of Array.isArray(node.material) ? node.material : node.material ? [node.material] : []) material.dispose();
    });
    root.clear();
}

export function attachApartmentPeopleLayer(engine, { now = () => performance.now() / 1000, animate = true } = {}) {
    const parent = engine?.apartmentRoot;
    if (!parent || typeof parent.add !== 'function') throw Error('apartment_people_root_missing');
    const root = new THREE.Group();
    root.name = 'home-apartment-people-owned';
    root.userData.noLightingAuthority = true;
    parent.add(root);
    const markers = new Map();
    let disposed = false, rafId = null, last = null, status = { mode: null, camera: null, health: null, at: null };

    function marker(id, xy, at) {
        const group = new THREE.Group();
        group.name = `person-${id}`;
        const material = opacity => new THREE.MeshBasicMaterial({ color: ICE, transparent: true, opacity, depthWrite: false, side: THREE.DoubleSide });
        const disc = new THREE.Mesh(new THREE.CircleGeometry(.12, 32), material(.95));
        const ring = new THREE.Mesh(new THREE.RingGeometry(.95, 1, 48), material(.3));
        const arrow = new THREE.Mesh(new THREE.ConeGeometry(.07, .22, 3), material(.9));
        disc.position.z = .02; ring.position.z = .025;
        // The cone's axis is its +y, which already lies on the floor (z is up here), pointing at a bearing of 0.
        const pivot = new THREE.Group();           // turned about z to the heading
        arrow.position.set(0, .26, .03);
        pivot.add(arrow);
        pivot.visible = false;
        group.add(disc, ring, pivot);
        const positions = new Float32Array(TRAIL_POINTS * 3);
        const trailGeometry = new THREE.BufferGeometry();
        trailGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
        trailGeometry.setDrawRange(0, 0);
        const trail = new THREE.Line(trailGeometry, new THREE.LineBasicMaterial({ color: ICE, transparent: true, opacity: .5, depthWrite: false }));
        trail.name = `trail-${id}`;
        root.add(group, trail);
        const m = { id, group, disc, ring, pivot, trail, positions, points: [], lastSample: -Infinity,
            spring: { x: xy[0], y: xy[1], vx: 0, vy: 0, tx: xy[0], ty: xy[1] }, sigma: .3, heading: null, coasting: false, seen: at };
        group.position.set(xy[0], xy[1], 0);
        markers.set(id, m);
        return m;
    }
    function remove(m) {
        root.remove(m.group, m.trail);
        release(m.group);
        m.trail.geometry.dispose(); m.trail.material.dispose();
        markers.delete(m.id);
    }

    /* One stream event. Returns false, drawing nothing, for one that isn't den-live-people-1. */
    function update(event) {
        if (disposed || !validPeopleEvent(event)) return false;
        const at = now();
        status = { mode: event.mode ?? null, camera: event.camera ?? null, health: event.health ?? null, at };
        for (const track of event.tracks) {
            const m = markers.get(track.track_id) || marker(track.track_id, track.xy_m, at);
            m.spring.tx = track.xy_m[0]; m.spring.ty = track.xy_m[1];
            m.sigma = track.sigma_m ?? .3;
            m.heading = track.heading_deg ?? null;
            m.coasting = track.state === 'coasting';
            // What's near them, from the den's labels (den-live's room_labels): words only, at most 160 characters.
            m.near = typeof track.near === 'string' && track.near.length <= 160 ? track.near : null;
            m.seen = at;
        }
        return true;
    }

    /* Advance the springs, the trails and the fades by dt seconds. */
    function tick(dt) {
        if (disposed) return;
        const at = now(), step = Math.max(0, Math.min(.1, dt));
        for (const m of [...markers.values()]) {
            const fade = Math.max(0, Math.min(1, 1 - Math.max(0, at - m.seen - GRACE_S) / FADE_S));
            if (fade <= 0) { remove(m); continue; }
            const s = m.spring;
            for (const [p, v, t] of [['x', 'vx', 'tx'], ['y', 'vy', 'ty']]) s[v] += (SPRING_W * SPRING_W * (s[t] - s[p]) - 2 * SPRING_W * s[v]) * step;
            const speed = Math.hypot(s.vx, s.vy);
            if (speed > MAX_SPEED) { s.vx *= MAX_SPEED / speed; s.vy *= MAX_SPEED / speed; }
            s.x += s.vx * step; s.y += s.vy * step;
            m.group.position.set(s.x, s.y, 0);
            m.ring.scale.setScalar(Math.max(.18, 2 * m.sigma));
            m.pivot.visible = m.heading != null;
            if (m.heading != null) m.pivot.rotation.z = -m.heading * Math.PI / 180;   // a bearing runs clockwise from +y
            const strength = fade * (m.coasting ? COASTING : 1);
            m.disc.material.opacity = .95 * strength; m.ring.material.opacity = .3 * strength;
            m.pivot.children[0].material.opacity = .9 * strength; m.trail.material.opacity = .5 * strength;
            if (at - m.lastSample >= TRAIL_S / TRAIL_POINTS - 1e-6) {   // the tolerance keeps float steps from skipping one
                m.lastSample = at;
                m.points.push([s.x, s.y, at]);
                while (m.points.length > TRAIL_POINTS || m.points.length && at - m.points[0][2] > TRAIL_S) m.points.shift();
                m.points.forEach((p, i) => m.positions.set([p[0], p[1], .015], i * 3));
                m.trail.geometry.attributes.position.needsUpdate = true;
                m.trail.geometry.setDrawRange(0, m.points.length);
            }
        }
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
        /* What the page shows beside the markers: the stream's mode and camera status, and how long since an event. */
        status: () => ({ ...status, tracks: markers.size, since_s: status.at == null ? null : now() - status.at }),
        markers: () => [...markers.values()].map(m => ({ track_id: m.id, xy_m: [m.spring.x, m.spring.y], coasting: m.coasting,
            heading_deg: m.heading, trail: m.points.length, near: m.near ?? null })),
        dispose() {
            if (disposed) return;
            disposed = true;
            if (rafId != null && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(rafId);
            for (const m of [...markers.values()]) remove(m);
            parent.remove(root);
            release(root);
        },
    };
}
