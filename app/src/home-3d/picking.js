/* home-3d/picking.js — raycast picking + 3D->screen projection for HTML labels.
 * P0 ships projectToScreen (used by the HUD later); collider raycasting fills
 * in at P2 when device markers exist.
 */
import * as THREE from 'three';

export function createPicking(camera, hostEl) {
    const raycaster = new THREE.Raycaster();
    const v = new THREE.Vector3();
    let host = hostEl;   // re-homed on every view mount — the engine outlives
                         // the React tree, so the boot-time host div goes stale

    return {
        raycaster,
        setHost(el) { if (el) host = el; },
        /* world-space Vector3 -> {x, y, visible} in host-element pixels */
        projectToScreen(worldPos) {
            v.copy(worldPos).project(camera);
            const w = host.clientWidth, h = host.clientHeight;
            return {
                x: (v.x * 0.5 + 0.5) * w,
                y: (-v.y * 0.5 + 0.5) * h,
                visible: v.z < 1 && Math.abs(v.x) <= 1.05 && Math.abs(v.y) <= 1.05,
            };
        },
        pick(objects, clientX, clientY) {
            const rect = host.getBoundingClientRect();
            const ndc = new THREE.Vector2(
                ((clientX - rect.left) / rect.width) * 2 - 1,
                -((clientY - rect.top) / rect.height) * 2 + 1,
            );
            raycaster.setFromCamera(ndc, camera);
            return raycaster.intersectObjects(objects, true);
        },

        /* Screen point -> exact apartment-frame mesh hit. The point and face
         * normal are both converted back into the shared Z-up apartment
         * frame, so edit tools can place horizontal work surfaces and
         * vertical art/custom aim planes directly on the runtime mesh. */
        surfaceHit(apartmentRoot, objects, clientX, clientY) {
            const hits = this.pick(objects || [], clientX, clientY);
            const hit = hits.find((candidate) => candidate.point);
            if (!hit) return null;
            apartmentRoot.updateMatrixWorld(true);
            hit.object.updateMatrixWorld(true);
            const point = apartmentRoot.worldToLocal(hit.point.clone());
            let normal = new THREE.Vector3(0, 0, 1);
            if (hit.face?.normal) {
                normal.copy(hit.face.normal).transformDirection(hit.object.matrixWorld);
                const rootQ = new THREE.Quaternion();
                apartmentRoot.getWorldQuaternion(rootQ);
                normal.applyQuaternion(rootQ.invert()).normalize();
            }
            return {
                point: [point.x, point.y, point.z],
                normal: [normal.x, normal.y, normal.z],
                object: hit.object,
            };
        },

        /* Screen point -> apartment-frame floor coords [x, y] (Z-up, z=0).
         * Ray ∩ world floor plane (y=0 after the root conversion), then into
         * the root's object space. Returns null when the ray misses. */
        floorPoint(apartmentRoot, clientX, clientY) {
            const rect = host.getBoundingClientRect();
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
