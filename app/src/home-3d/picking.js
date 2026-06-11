/* home-3d/picking.js — raycast picking + 3D->screen projection for HTML labels.
 * P0 ships projectToScreen (used by the HUD later); collider raycasting fills
 * in at P2 when device markers exist.
 */
import * as THREE from 'three';

export function createPicking(camera, hostEl) {
    const raycaster = new THREE.Raycaster();
    const v = new THREE.Vector3();

    return {
        raycaster,
        /* world-space Vector3 -> {x, y, visible} in host-element pixels */
        projectToScreen(worldPos) {
            v.copy(worldPos).project(camera);
            const w = hostEl.clientWidth, h = hostEl.clientHeight;
            return {
                x: (v.x * 0.5 + 0.5) * w,
                y: (-v.y * 0.5 + 0.5) * h,
                visible: v.z < 1 && Math.abs(v.x) <= 1.05 && Math.abs(v.y) <= 1.05,
            };
        },
        pick(objects, clientX, clientY) {
            const rect = hostEl.getBoundingClientRect();
            const ndc = new THREE.Vector2(
                ((clientX - rect.left) / rect.width) * 2 - 1,
                -((clientY - rect.top) / rect.height) * 2 + 1,
            );
            raycaster.setFromCamera(ndc, camera);
            return raycaster.intersectObjects(objects, true);
        },

        /* Screen point -> apartment-frame floor coords [x, y] (Z-up, z=0).
         * Ray ∩ world floor plane (y=0 after the root conversion), then into
         * the root's object space. Returns null when the ray misses. */
        floorPoint(apartmentRoot, clientX, clientY) {
            const rect = hostEl.getBoundingClientRect();
            const ndc = new THREE.Vector2(
                ((clientX - rect.left) / rect.width) * 2 - 1,
                -((clientY - rect.top) / rect.height) * 2 + 1,
            );
            raycaster.setFromCamera(ndc, camera);
            const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
            const hit = new THREE.Vector3();
            if (!raycaster.ray.intersectPlane(plane, hit)) return null;
            const local = apartmentRoot.worldToLocal(hit.clone());
            return [local.x, local.y];
        },
    };
}
