/* home-3d/picking.js — raycast picking + 3D->screen projection for HTML labels.
 * P0 ships projectToScreen (used by the HUD later); collider raycasting fills
 * in at P2 when device markers exist.
 */
import * as THREE from 'three';

/* The aim chip's grazing note ("the pool will be long and dim") starts here,
 * matching the W2 aim presentation's threshold. */
export const BEAM_GRAZING_DEG = 80;

const finite3 = (value) => Array.isArray(value) && value.length >= 3
    && value.slice(0, 3).every((n) => typeof n === 'number' && Number.isFinite(n));

/* A surface class from an Apartment-frame (Z-up) normal that faces the light.
 * No producer names furniture yet, so this is all a blocker or a surface can
 * honestly be called. */
export function surfaceClassFromNormal(normal, pointZ = null) {
    const z = Array.isArray(normal) ? normal[2] : NaN;
    if (!Number.isFinite(z)) return 'surface';
    if (z >= 0.7) return Number.isFinite(pointZ) && pointZ <= 0.15 ? 'floor' : 'flat surface';
    if (z <= -0.7) return 'ceiling';
    if (Math.abs(z) <= 0.35) return 'wall';
    return 'surface';
}

const normalDot = (n, d) => n[0] * d.x + n[1] * d.y + n[2] * d.z;

const CLASS_NAMES = Object.freeze({
    floor: 'the floor', 'flat surface': 'a surface', ceiling: 'the ceiling', wall: 'a wall', surface: 'an object',
});

/* The aim chip's `traceBeam` answer (W2 aim presentation): `{ point_m, name }`
 * plus the blocker's normal, distance and class when something is in the
 * way, else null. */
export function aimChipBlocker(path) {
    const blocker = path?.blocker;
    if (!blocker) return null;
    return Object.freeze({
        point_m: blocker.point_m.slice(),
        name: blocker.name,
        normal_m: blocker.normal_m.slice(),
        distance_m: blocker.distance_m,
        surface_class: blocker.surface_class,
    });
}

/* Pick filters for the dollhouse cut-away (the owner looks down into a room
 * whose near walls and ceiling are opened up).
 *
 * The collision proxy keeps its ceiling, so a ray toward floor the viewer can
 * see first meets the ceiling from above. `rejectHit` skips an up-facing
 * (toward the viewer) horizontal hit in the ceiling band, seen from above,
 * and the pick takes the next surface: floor, wall or furniture. The band is
 * the top `ceilingBandM` of the collision bounds and never below
 * `minCeilingHeightM` over the floor, so desks, shelves and other furniture
 * tops stay pickable.
 *
 * `floorGap` bridges hairline cracks in the scanned floor: when nothing is
 * accepted, the nearest floor hit within `toleranceM` of where the ray meets
 * the floor plane is used, at the ray's own floor point. Collision bytes are
 * never changed. Heights are Apartment-local Z-up metres. */
export function cutawayPickFilters({ floorZ, topZ, ceilingBandM = 0.35, minCeilingHeightM = 1.8, gapToleranceM = 0.04 } = {}) {
    if (!Number.isFinite(floorZ) || !Number.isFinite(topZ) || !(topZ > floorZ)) return null;
    const ceilingZ = Math.max(floorZ + minCeilingHeightM, topZ - ceilingBandM);
    return Object.freeze({
        ceilingZ,
        rejectHit: ({ point, normal, eye }) => normal[2] >= 0.7 && point[2] >= ceilingZ && eye[2] > point[2],
        floorGap: Object.freeze({ floorZ, toleranceM: gapToleranceM }),
    });
}

const GAP_RINGS = [0.01, 0.02, 0.03, 0.04, 0.06, 0.08];
const GAP_SPOKES = 8;

export function createPicking(camera, hostEl) {
    const raycaster = new THREE.Raycaster();
    // beamPath has its own raycaster, so it can never change pick() bounds.
    const pathRaycaster = new THREE.Raycaster();
    // The floor-gap fallback casts its own rays too.
    const gapRaycaster = new THREE.Raycaster();
    const v = new THREE.Vector3();
    let host = hostEl;   // re-homed on every view mount — the engine outlives
                         // the React tree, so the boot-time host div goes stale

    /* A hairline crack lets the ray fall through the floor. Find where the
     * ray meets the floor plane and look for the nearest floor hit in rings
     * up to `toleranceM` around it; answer at the ray's own floor point with
     * that floor's height and normal. */
    function floorGapHit(apartmentRoot, objects, ray, { floorZ, toleranceM = 0.04 }, firstAccepted, doubleSided) {
        const originLocal = apartmentRoot.worldToLocal(ray.origin.clone());
        const ahead = apartmentRoot.worldToLocal(ray.origin.clone().add(ray.direction));
        const directionLocal = ahead.sub(originLocal);
        if (!(directionLocal.z < -1e-6)) return null;
        const t = (floorZ - originLocal.z) / directionLocal.z;
        if (!(t > 0)) return null;
        const onPlane = originLocal.clone().addScaledVector(directionLocal, t);
        const sides = new Map();
        try {
            if (doubleSided === true) for (const object of objects) object.traverse(node => {
                if (!node.isMesh) return;
                for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
                    if (material && !sides.has(material)) { sides.set(material, material.side); material.side = THREE.DoubleSide; }
                }
            });
            for (const radius of GAP_RINGS) {
                if (radius > toleranceM + 1e-9) break;
                let best = null;
                for (let i = 0; i < GAP_SPOKES; i++) {
                    const angle = (i / GAP_SPOKES) * Math.PI * 2;
                    const targetLocal = onPlane.clone().add(new THREE.Vector3(Math.cos(angle) * radius, Math.sin(angle) * radius, 0));
                    const direction = apartmentRoot.localToWorld(targetLocal).sub(ray.origin).normalize();
                    gapRaycaster.set(ray.origin, direction); gapRaycaster.near = 0; gapRaycaster.far = Infinity;
                    const hit = firstAccepted(gapRaycaster.intersectObjects(objects, true), direction);
                    // Only the floor itself bridges a crack.
                    if (!hit || hit.normal[2] < 0.7 || hit.point[2] > floorZ + 0.1) continue;
                    const offset = Math.hypot(hit.point[0] - onPlane.x, hit.point[1] - onPlane.y);
                    if (!best || offset < best.offset) best = { ...hit, offset };
                }
                if (best) {
                    // The ray's own floor point, at the found floor's height.
                    const z = best.point[2], s = (z - originLocal.z) / directionLocal.z;
                    const at = originLocal.clone().addScaledVector(directionLocal, s);
                    return { point: [at.x, at.y, z], normal: best.normal, object: best.object };
                }
            }
            return null;
        } finally { for (const [material, side] of sides) material.side = side; }
    }

    return {
        raycaster,
        setHost(el) { if (el) host = el; },
        getHost() { return host; },
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
        pick(objects, clientX, clientY, { doubleSided = false } = {}) {
            const rect = host.getBoundingClientRect();
            const ndc = new THREE.Vector2(
                ((clientX - rect.left) / rect.width) * 2 - 1,
                -((clientY - rect.top) / rect.height) * 2 + 1,
            );
            // beamTrace intentionally bounds this shared raycaster to the
            // requested beam distance. Screen picking is camera-relative and
            // must not inherit that range or a valid visible mesh can become
            // unreachable after the first beam preview.
            raycaster.near = 0;
            raycaster.far = Infinity;
            raycaster.setFromCamera(ndc, camera);
            // A scan may contain mixed triangle winding. This opt-in affects
            // only the synchronous query, never the material's rendered side.
            const sides = new Map();
            try {
                if (doubleSided === true) for (const object of objects) object.traverse(node => {
                    if (!node.isMesh) return;
                    for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
                        if (material && !sides.has(material)) {
                            sides.set(material, material.side); material.side = THREE.DoubleSide;
                        }
                    }
                });
                return raycaster.intersectObjects(objects, true);
            } finally { for (const [material, side] of sides) material.side = side; }
        },

        /* Screen point -> exact apartment-frame mesh hit. The point and face
         * normal are both converted back into the shared Z-up apartment
         * frame, so edit tools can place horizontal work surfaces and
         * vertical art/custom aim planes directly on the runtime mesh. */
        surfaceHit(apartmentRoot, objects, clientX, clientY, { acceptPoint = null, doubleSided = false } = {}) {
            const hits = this.pick(objects || [], clientX, clientY, { doubleSided });
            apartmentRoot.updateMatrixWorld(true);
            // Presentation filtering chooses a visible deeper face without
            // modifying the collision mesh used for independent world queries.
            let hit = null, point = null;
            for (const candidate of hits) {
                if (!candidate.point) continue;
                const local = apartmentRoot.worldToLocal(candidate.point.clone());
                if (acceptPoint && !acceptPoint([local.x, local.y, local.z], candidate)) continue;
                hit = candidate; point = local; break;
            }
            if (!hit) return null;
            hit.object.updateMatrixWorld(true);
            let normal = new THREE.Vector3(0, 0, 1);
            if (hit.face?.normal) {
                normal.copy(hit.face.normal).transformDirection(hit.object.matrixWorld);
                // Double-sided scan triangles have mixed winding. Use the
                // geometric normal facing the viewing ray, as Three does.
                if (normal.dot(raycaster.ray.direction) > 0) normal.negate();
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

        /* surfaceHit under the dollhouse cut-away, with the filters from
         * cutawayPickFilters: `rejectHit({ point, normal, eye }, candidate)`
         * skips hits the viewer cannot see (the ceiling from above) and
         * `floorGap` bridges hairline floor cracks. The result has the same
         * { point, normal, object } shape as surfaceHit. */
        surfaceHitUnderCutaway(apartmentRoot, objects, clientX, clientY, { acceptPoint = null, doubleSided = false, rejectHit = null, floorGap = null } = {}) {
            const hits = this.pick(objects || [], clientX, clientY, { doubleSided });
            apartmentRoot.updateMatrixWorld(true);
            const rootQInverse = apartmentRoot.getWorldQuaternion(new THREE.Quaternion()).invert();
            const eyeLocal = apartmentRoot.worldToLocal(raycaster.ray.origin.clone());
            const eye = [eyeLocal.x, eyeLocal.y, eyeLocal.z];
            // The face normal in the Apartment frame, facing the viewing ray.
            const normalOf = (candidate, rayDirection) => {
                const normal = new THREE.Vector3(0, 0, 1);
                if (candidate.face?.normal) {
                    candidate.object.updateMatrixWorld(true);
                    normal.copy(candidate.face.normal).transformDirection(candidate.object.matrixWorld);
                    if (normal.dot(rayDirection) > 0) normal.negate();
                    normal.applyQuaternion(rootQInverse).normalize();
                }
                return [normal.x, normal.y, normal.z];
            };
            const firstAccepted = (candidates, rayDirection) => {
                for (const candidate of candidates) {
                    if (!candidate.point) continue;
                    const local = apartmentRoot.worldToLocal(candidate.point.clone());
                    const point = [local.x, local.y, local.z];
                    if (acceptPoint && !acceptPoint(point, candidate)) continue;
                    const normal = normalOf(candidate, rayDirection);
                    if (rejectHit && rejectHit({ point, normal, eye }, candidate)) continue;
                    return { point, normal, object: candidate.object };
                }
                return null;
            };
            const found = firstAccepted(hits, raycaster.ray.direction);
            if (found || !floorGap) return found;
            return floorGapHit(apartmentRoot, objects || [], raycaster.ray, floorGap, firstAccepted, doubleSided);
        },

        /* Apartment-local beam ray sampling against the dedicated collision
         * proxy. Returns the first center hit plus an exact mesh contour made
         * from first-hit boundary rays. Sparse/grazing contours are explicitly
         * marked partial rather than promoted to an ellipse. */
        beamTrace(apartmentRoot, objects, originValue, directionValue, fullFwhmDeg, requestedDistance) {
            if (!apartmentRoot || !objects || !Array.isArray(originValue) || !Array.isArray(directionValue)) return null;
            apartmentRoot.updateMatrixWorld(true);
            const originLocal = new THREE.Vector3(...originValue);
            const directionLocal = new THREE.Vector3(...directionValue).normalize();
            const originWorld = apartmentRoot.localToWorld(originLocal.clone());
            const rootQ = new THREE.Quaternion();
            apartmentRoot.getWorldQuaternion(rootQ);
            const directionWorld = directionLocal.clone().applyQuaternion(rootQ).normalize();
            const cast = (localDirection) => {
                const worldDirection = localDirection.clone().applyQuaternion(rootQ).normalize();
                raycaster.set(originWorld, worldDirection);
                raycaster.near = 0.015;
                raycaster.far = Number.isFinite(requestedDistance) ? Math.max(0.02, requestedDistance + 0.25) : Infinity;
                const hit = raycaster.intersectObjects(Array.isArray(objects) ? objects : [objects], true)
                    .find((candidate) => candidate.point);
                if (!hit) return null;
                return {
                    point: apartmentRoot.worldToLocal(hit.point.clone()).toArray(),
                    distance_m: hit.distance,
                    object: hit.object,
                };
            };
            const center = cast(directionLocal);
            const helper = Math.abs(directionLocal.z) < 0.9
                ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(0, 1, 0);
            const u = new THREE.Vector3().crossVectors(directionLocal, helper).normalize();
            const w = new THREE.Vector3().crossVectors(u, directionLocal).normalize();
            const halfAngle = THREE.MathUtils.degToRad(Math.max(0.1, Math.min(179, +fullFwhmDeg || 20)) / 2);
            const points = [];
            const samples = 48;
            for (let i = 0; i < samples; i++) {
                const phi = (i / samples) * Math.PI * 2;
                const rim = u.clone().multiplyScalar(Math.cos(phi)).addScaledVector(w, Math.sin(phi));
                const ray = directionLocal.clone().multiplyScalar(Math.cos(halfAngle))
                    .addScaledVector(rim, Math.sin(halfAngle)).normalize();
                const hit = cast(ray);
                if (hit) points.push(hit.point);
            }
            const requested = Number.isFinite(requestedDistance) ? requestedDistance : null;
            const blocked = !!(center && requested != null && center.distance_m + 0.03 < requested);
            return {
                center,
                footprint: points.length >= 2 ? {
                    kind: points.length >= samples * 0.75 ? 'ellipse' : 'partial',
                    points,
                    hit_fraction: points.length / samples,
                } : null,
                obstruction_point: blocked ? center.point : null,
                blocked,
            };
        },

        /* One straight ray from a light toward an aim point, for the aim chip
         * (SPEC Gestures: "Blocked by ..." with a marker, and the grazing
         * note). Additive beside beamTrace: the same Apartment-local Z-up
         * frame and collision objects, but it reports the hit normal and the
         * first blocker.
         *
         * Returns null for unusable input; otherwise a frozen
         *   { clear, target_distance_m,
         *     hit: { point_m, normal_m, distance_m, surface_class, object } | null,
         *     blocker: { point_m, normal_m, distance_m, surface_class, name, object } | null,
         *     incidence_deg, grazing }
         * `hit` is the first accepted surface along the ray, up to just past
         * the target. It is the blocker when it lies more than `toleranceM`
         * short of the target. Normals face the light. `incidence_deg` and
         * `grazing` describe the target surface and are null while blocked or
         * when the ray meets no surface. `acceptPoint(localPoint, candidate)`
         * skips faces, such as dollhouse cut-away walls, that must not block.
         * `name` is the object's explicit `userData.semantic_name` when it has
         * one, otherwise a phrase from the surface class ("a wall"). */
        beamPath(apartmentRoot, objects, originValue, targetValue, { toleranceM = 0.03, acceptPoint = null } = {}) {
            if (!apartmentRoot || !objects || !finite3(originValue) || !finite3(targetValue)) return null;
            apartmentRoot.updateMatrixWorld(true);
            const originLocal = new THREE.Vector3(...originValue.slice(0, 3));
            const targetLocal = new THREE.Vector3(...targetValue.slice(0, 3));
            const directionLocal = targetLocal.clone().sub(originLocal);
            const targetDistance = directionLocal.length();
            if (!(targetDistance > 1e-4)) return null;
            directionLocal.normalize();
            const originWorld = apartmentRoot.localToWorld(originLocal.clone());
            const directionWorld = apartmentRoot.localToWorld(targetLocal.clone()).sub(originWorld);
            const worldPerLocal = directionWorld.length() / targetDistance;
            directionWorld.normalize();
            const rootQInverse = apartmentRoot.getWorldQuaternion(new THREE.Quaternion()).invert();
            pathRaycaster.set(originWorld, directionWorld);
            pathRaycaster.near = 0.015 * worldPerLocal;
            pathRaycaster.far = (targetDistance + 0.25) * worldPerLocal;
            const tolerance = Number.isFinite(toleranceM) && toleranceM >= 0 ? toleranceM : 0.03;
            let first = null;
            for (const candidate of pathRaycaster.intersectObjects(Array.isArray(objects) ? objects : [objects], true)) {
                if (!candidate.point) continue;
                const local = apartmentRoot.worldToLocal(candidate.point.clone());
                if (acceptPoint && !acceptPoint([local.x, local.y, local.z], candidate)) continue;
                first = { candidate, local };
                break;
            }
            let surface = null;
            if (first) {
                const { candidate, local } = first;
                const normal = new THREE.Vector3(0, 0, 1);
                if (candidate.face?.normal) {
                    candidate.object.updateMatrixWorld(true);
                    normal.copy(candidate.face.normal).transformDirection(candidate.object.matrixWorld)
                        .applyQuaternion(rootQInverse).normalize();
                }
                // Double-sided scan triangles have mixed winding: face the light.
                if (normal.dot(directionLocal) > 0) normal.negate();
                const normalM = Object.freeze([normal.x, normal.y, normal.z]);
                const surfaceClass = surfaceClassFromNormal(normalM, local.z);
                const label = candidate.object?.userData?.semantic_name;
                surface = {
                    point_m: Object.freeze([local.x, local.y, local.z]),
                    normal_m: normalM,
                    distance_m: local.distanceTo(originLocal),
                    surface_class: surfaceClass,
                    name: typeof label === 'string' && label.trim() ? label.trim().slice(0, 80) : CLASS_NAMES[surfaceClass],
                    object: candidate.object,
                };
            }
            const blocked = !!(surface && surface.distance_m + tolerance < targetDistance);
            const incidence = surface && !blocked
                ? THREE.MathUtils.radToDeg(Math.acos(THREE.MathUtils.clamp(-normalDot(surface.normal_m, directionLocal), -1, 1)))
                : null;
            let hit = null;
            if (surface) { const { name, ...rest } = surface; hit = Object.freeze(rest); }
            return Object.freeze({
                clear: !blocked,
                target_distance_m: targetDistance,
                hit,
                blocker: blocked ? Object.freeze(surface) : null,
                incidence_deg: incidence,
                grazing: incidence != null && incidence > BEAM_GRAZING_DEG,
            });
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

/* Owner target selection against one independently pinned detailed display
 * mesh. This never supplies collision or perception/fixture authority. */
export function createRegisteredDeskSurfacePicker({ apartmentRoot, modes, picking,
    getManifest, sceneIdentity, digest, dollhouseKeep } = {}) {
    const need = (condition, reason) => { if (!condition) throw TypeError(reason); };
    const sha = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
    const keys = ['content_revision', 'registration_version', 'manifest_digest', 'frame_digest', 'canonical_collision_digest'];
    need(apartmentRoot?.isObject3D && typeof modes?.getMesh === 'function'
        && typeof modes?.debugInfo === 'function' && typeof picking?.surfaceHit === 'function'
        && typeof getManifest === 'function' && typeof digest === 'function'
        && typeof dollhouseKeep === 'function', 'registered_desk_surface_unavailable');
    function selection(binding) {
        need(binding && Object.keys(binding).length === keys.length && keys.every(key => Object.hasOwn(binding, key))
            && typeof binding.content_revision === 'string' && binding.content_revision.length > 0
            && Number.isSafeInteger(binding.registration_version) && binding.registration_version >= 0
            && keys.slice(2).every(key => sha(binding[key])), 'registered_desk_surface_binding_changed');
        const manifest = getManifest();
        need(sceneIdentity?.siteId === 'victoria' && manifest?.siteId === sceneIdentity.siteId
            && manifest.revision === sceneIdentity.contentRevision && manifest.revision === binding.content_revision
            && manifest.registrationVersion === sceneIdentity.registrationVersion
            && manifest.registrationVersion === binding.registration_version
            && manifest.assets?.['frame.json']?.sha256 === sceneIdentity.frameDigest
            && sceneIdentity.frameDigest === binding.frame_digest
            && manifest.assets?.['collision.glb']?.sha256 === binding.canonical_collision_digest
            && digest(manifest) === binding.manifest_digest, 'registered_desk_surface_binding_changed');
        const debug = modes.debugInfo(), mesh = modes.getMesh();
        // The aggregate status includes unrelated splat/collision work. A
        // loaded pinned mesh stays usable while those assets warm up.
        need(mesh?.isObject3D || !(debug?.status === 'loading' || debug?.loadStatus?.mesh === 'loading'),
            'registered_desk_surface_loading');
        need(['ready', 'loading'].includes(debug?.status) && debug.disposed !== true
            && debug.frameStatus !== 'failed' && !['failed', 'aborted', 'timed-out'].includes(debug.loadStatus?.mesh)
            && mesh?.isObject3D
            && debug.meshFallback === false && ['mesh.glb', 'mesh.mobile.glb'].includes(debug.meshSource)
            && sha(manifest.assets?.[debug.meshSource]?.sha256), 'registered_desk_surface_unavailable');
        let ancestor = mesh;
        while (ancestor && ancestor !== apartmentRoot) ancestor = ancestor.parent;
        need(ancestor === apartmentRoot, 'registered_desk_surface_unavailable');
        return { mesh, policy: debug.dollhouseCutaway,
            ref: { asset_name: debug.meshSource, asset_sha256: manifest.assets[debug.meshSource].sha256,
                manifest_digest: binding.manifest_digest } };
    }
    return {
        reference(binding) { return selection(binding).ref; },
        surfaceHit(clientX, clientY, binding) {
            const before = selection(binding);
            const hit = picking.surfaceHit(apartmentRoot, [before.mesh], clientX, clientY, {
                doubleSided: true, acceptPoint: point => dollhouseKeep(point, before.policy) > .05,
            });
            const after = selection(binding);
            need(after.mesh === before.mesh && after.ref.asset_name === before.ref.asset_name
                && after.ref.asset_sha256 === before.ref.asset_sha256,
            'registered_desk_surface_source_changed');
            return hit ? { ...hit, surface_ref: before.ref } : null;
        },
    };
}
