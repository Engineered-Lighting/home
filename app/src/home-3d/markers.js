/* home-3d/markers.js — device markers, camera frustums, person dot, zone outlines.
 *
 * P0 ships the group structure + the person dot + an axes gizmo; device/camera
 * markers fill in at P2/P4. All groups live under apartmentRoot (Z-up object
 * space — positions are apartment-frame [x, y, z] used directly).
 */
import * as THREE from 'three';

export function createOverlay(apartmentRoot) {
    const overlayRoot = new THREE.Group();
    overlayRoot.renderOrder = 10;
    apartmentRoot.add(overlayRoot);

    const devicesGroup = new THREE.Group();
    const camerasGroup = new THREE.Group();
    const personGroup = new THREE.Group();
    const zonesGroup = new THREE.Group();
    overlayRoot.add(devicesGroup, camerasGroup, personGroup, zonesGroup);

    // person dot: floor disc + soft ring + vertical hairline (hidden until tracks)
    const ice = new THREE.Color(0xb8d8ff);
    const disc = new THREE.Mesh(
        new THREE.CircleGeometry(0.12, 32),
        new THREE.MeshBasicMaterial({ color: ice, transparent: true, opacity: 0.95 }),
    );
    const ring = new THREE.Mesh(
        new THREE.RingGeometry(0.95, 1.0, 48),
        new THREE.MeshBasicMaterial({ color: ice, transparent: true, opacity: 0.3, side: THREE.DoubleSide }),
    );
    const hair = new THREE.Mesh(
        new THREE.CylinderGeometry(0.004, 0.004, 1.4, 6),
        new THREE.MeshBasicMaterial({ color: ice, transparent: true, opacity: 0.5 }),
    );
    hair.rotation.x = Math.PI / 2;       // cylinder Y-axis -> object-space Z (up)
    hair.position.z = 0.7;
    disc.position.z = 0.02;
    ring.position.z = 0.025;
    personGroup.add(disc, ring, hair);
    personGroup.visible = false;

    // debug axes (X red, Y green, Z blue/up) — toggled by the dev HUD
    const axes = new THREE.AxesHelper(1.5);
    axes.visible = false;
    overlayRoot.add(axes);

    let pulse = 0;
    return {
        devicesGroup, camerasGroup, personGroup, zonesGroup, axes,
        setPerson(track) {
            if (!track || !track.pos) { personGroup.visible = false; return; }
            personGroup.visible = true;
            personGroup.position.set(track.pos[0], track.pos[1], 0);
            const sigma = track.sigma_m ?? (track.cov ? Math.sqrt(Math.max(track.cov[0][0], track.cov[1][1])) : 0.3);
            ring.scale.setScalar(Math.max(0.18, 2 * sigma));
        },
        update(dt) {
            if (!personGroup.visible) return;
            pulse += dt;
            const k = 0.6 + 0.4 * (0.5 + 0.5 * Math.sin(pulse * 2 * Math.PI * 0.8));
            disc.material.opacity = k;
        },
    };
}
