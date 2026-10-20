/* Presentation only: supplied, already registered joints in the resident room.
 * Admission and freshness belong to the perception projection. This layer has
 * no acquisition, identity, lighting, network or model API. */
import * as THREE from 'three';
export { attachFastTracking } from './fast-tracking-layer.mjs';

const SCOPE_KEYS = ['siteId', 'contentRevision', 'frameDigest', 'registrationVersion', 'cameraId', 'sessionId', 'generation'];
const finite3 = value => Array.isArray(value) && value.length === 3 && value.every(number => Number.isFinite(number) && Number.isFinite(Math.fround(number)));
const validId = value => typeof value === 'string' && value.length > 0 && value.length <= 160;
const validJointId = value => validId(value) || Number.isSafeInteger(value) && value >= 0;
const samePosition = (a, b) => finite3(a) && finite3(b) && a.every((value, index) => value === b[index]);
const sameScope = (a, b) => a && Object.keys(a).length === SCOPE_KEYS.length && SCOPE_KEYS.every(key => a[key] === b[key]);
const copyJoint = joint => ({ id: joint.id, position_m: [...joint.position_m] });

function release(root) {
    const geometries = new Set(), materials = new Set();
    root.traverse(node => {
        if (node.geometry) geometries.add(node.geometry);
        for (const material of Array.isArray(node.material) ? node.material : node.material ? [node.material] : []) materials.add(material);
    });
    geometries.forEach(geometry => geometry.dispose());
    materials.forEach(material => material.dispose());
    root.clear();
}

export function attachApartmentPerceptionLayer(engine, scope, { isCurrent = () => true, reducedMotion = false } = {}) {
    if (!scope || !sameScope(scope, scope) || !['siteId', 'contentRevision', 'cameraId', 'sessionId'].every(key => validId(scope[key]))
        || !/^[a-f0-9]{64}$/.test(scope.frameDigest || '') || !Number.isSafeInteger(scope.generation) || scope.generation < 0
        || !(scope.registrationVersion === null || Number.isSafeInteger(scope.registrationVersion) && scope.registrationVersion >= 0)
        || typeof isCurrent !== 'function') throw Error('apartment_perception_scope_invalid');
    const pin = Object.freeze({ ...scope });
    const resident = { root: engine?.apartmentRoot, camera: engine?.camera, canvas: engine?.renderer?.domElement, rig: engine?.rig };
    const residentMatches = () => !!resident.root && !!resident.camera && !!resident.canvas && !!resident.rig
        && engine.apartmentRoot === resident.root && engine.camera === resident.camera && engine.renderer?.domElement === resident.canvas && engine.rig === resident.rig
        && engine.siteId === pin.siteId && engine.contentRevision === pin.contentRevision
        && ['siteId', 'contentRevision', 'frameDigest', 'registrationVersion'].every(key => engine.sceneIdentity?.[key] === pin[key]);
    const current = () => { try { return isCurrent() === true; } catch (_) { return false; } };
    if (!residentMatches() || typeof resident.root.add !== 'function' || typeof engine.picking?.projectToScreen !== 'function'
        || !['snapshotPose', 'restorePose', 'goTo', 'zoomIndex'].every(key => typeof resident.rig[key] === 'function'))
        throw Error('apartment_perception_resident_mismatch');
    const root = new THREE.Group();
    root.name = 'home-apartment-perception-owned';
    root.userData.noLightingAuthority = true;
    resident.root.add(root);

    let disposed = false, status = 'unavailable', reason = 'room_pose_unavailable', diagnostic = false, qualification = null, renderableDiagnostic = false;
    let renderableApproximate = false;
    let sourceEpochSeen = false, sourceEpoch = null;
    let joints = [], edges = [], anchor = null, followAllowed = false, following = false, followPaused = false, savedPose = null, geometryKey = '', lastFollowTarget = null;
    const assertOpen = () => { if (disposed) throw Error('apartment_perception_layer_disposed'); };
    function stopFollowing() {
        following = false;
        followPaused = false;
        lastFollowTarget = null;
        const restore = savedPose;
        savedPose = null;
        // A previous home's fit/target must never be restored into a new room.
        if (restore && residentMatches()) resident.rig.restorePose(restore);
    }
    function clear(nextStatus, nextReason) {
        stopFollowing();
        release(root);
        joints = []; edges = []; anchor = null; followAllowed = false; geometryKey = '';
        status = nextStatus; reason = nextReason; diagnostic = false; qualification = null; renderableDiagnostic = false;
        renderableApproximate = false; root.userData.approximateLabel = null;
        root.userData.diagnosticStatus = null;
    }
    function pauseFollow(pose) {
        // Freeze at the current real camera pose; keep the original user pose
        // separately so a matched pair can resume without a Return/Follow jump.
        if (!followPaused) resident.rig.restorePose(resident.rig.snapshotPose());
        followPaused = true; lastFollowTarget = null;
        release(root); joints = []; edges = []; anchor = null; followAllowed = false; geometryKey = '';
        status = pose.status; reason = pose.reason; qualification = pose.qualification;
        diagnostic = pose.diagnostic === true || qualification === 'diagnostic_alignment';
    }
    function usable() {
        if (!residentMatches()) { clear('refused', 'resident_identity_changed'); return false; }
        if (!current()) { clear('unavailable', 'viewer_no_longer_current'); return false; }
        return true;
    }
    function acceptedAnchor(value, byId) {
        const joint = value && value.visible !== false && byId.get(value.id);
        return joint && samePosition(value.position_m, joint.position_m) ? copyJoint(joint) : null;
    }
    function updateFollow() {
        if (!following || !followAllowed || !anchor) return;
        resident.root.updateWorldMatrix(true, false);
        const target = resident.root.localToWorld(new THREE.Vector3(...anchor.position_m));
        if (lastFollowTarget?.equals(target)) return;
        const reduced = typeof reducedMotion === 'function' ? reducedMotion() === true : reducedMotion === true;
        resident.rig.goTo({ target, zoom: Math.max(1, resident.rig.zoomIndex()), dur: reduced ? 0 : 180 });
        lastFollowTarget = target.clone();
    }
    function draw(view) {
        assertOpen();
        if (!usable()) return readback();
        if (!sameScope(view?.scope, pin)) { clear('refused', 'view_scope_mismatch'); return readback(); }
        const nextEpoch = view.sourceEpoch ?? null;
        if (nextEpoch !== null && (typeof nextEpoch !== 'string' || nextEpoch.length > 256)) {
            clear('refused', 'source_epoch_invalid'); return readback();
        }
        // Source restarts are a new observation owner, including unknown↔known
        // transitions. Never carry a user's Follow choice into that new source.
        if (sourceEpochSeen && nextEpoch !== sourceEpoch) stopFollowing();
        sourceEpochSeen = true; sourceEpoch = nextEpoch;
        const proposed = view.diagnosticRoomPose;
        const displayDiagnostic = view.roomPose?.admitted !== true && view.status === 'available' && proposed?.renderable === true;
        const approximate = view.approximateRoomPose;
        const displayApproximate = !displayDiagnostic && view.roomPose?.admitted !== true && view.status === 'available' && approximate?.renderable === true;
        const pose = displayDiagnostic ? proposed : displayApproximate ? approximate : view.roomPose;
        if (following && view.status === 'available' && ['recording', 'live'].includes(view.sessionState)
            && pose?.reason === 'displayed_frame_mismatch' && pose.age?.status === 'current' && pose.noLightingAuthority === true
            && ['qualified_viewing', 'diagnostic_alignment'].includes(pose.qualification)) {
            pauseFollow(pose); return readback();
        }
        if (!displayDiagnostic && !displayApproximate && (pose?.admitted !== true || pose.status !== 'current')) {
            clear(pose?.status || 'unavailable', pose?.reason || 'room_pose_unavailable'); return readback();
        }
        if (pose.noLightingAuthority !== true || pin.registrationVersion === null
            || !(displayApproximate ? pose.qualification === 'approximate_visual_alignment' : ['qualified_viewing', 'diagnostic_alignment'].includes(pose.qualification))
            || displayDiagnostic && (pose.admitted !== false || pose.followAllowed !== false || !['timing_unverified', 'recorded'].includes(pose.status))
            || displayApproximate && (pose.admitted !== false || pose.followAllowed !== false || pose.status !== 'approximate' || pose.label !== 'Approximate room placement')
            || !Array.isArray(pose.joints) || pose.joints.length > 256 || !Array.isArray(pose.edges) || pose.edges.length > 512) {
            clear('refused', 'room_pose_invalid'); return readback();
        }
        const byId = new Map();
        for (const joint of pose.joints) {
            if (!validJointId(joint?.id) || !finite3(joint.position_m) || joint.visible === false) continue;
            if (byId.has(joint.id)) { clear('refused', 'room_joint_id_ambiguous'); return readback(); }
            byId.set(joint.id, copyJoint(joint));
        }
        const acceptedEdges = [], seen = new Set();
        for (const edge of pose.edges) {
            if (!Array.isArray(edge) || edge.length !== 2 || !byId.has(edge[0]) || !byId.has(edge[1]) || edge[0] === edge[1]) continue;
            const key = JSON.stringify(edge.map(id => JSON.stringify(id)).sort());
            if (!seen.has(key)) { seen.add(key); acceptedEdges.push([...edge]); }
        }
        status = displayDiagnostic || displayApproximate ? pose.status : 'current'; reason = null; qualification = pose.qualification;
        renderableDiagnostic = displayDiagnostic;
        renderableApproximate = displayApproximate; root.userData.approximateLabel = displayApproximate ? 'Approximate room placement' : null;
        root.userData.diagnosticStatus = displayDiagnostic ? pose.status : null;
        followPaused = false;
        diagnostic = pose.diagnostic === true || qualification === 'diagnostic_alignment' || displayApproximate;
        joints = [...byId.values()]; edges = acceptedEdges;
        anchor = displayDiagnostic || displayApproximate ? null : acceptedAnchor(pose.anchor, byId);
        const followAnchor = acceptedAnchor(view.follow?.anchor, byId);
        followAllowed = !displayDiagnostic && !displayApproximate && view.follow?.allowed === true && !!anchor && !!followAnchor && anchor.id === followAnchor.id
            && samePosition(anchor.position_m, followAnchor.position_m);
        if (!followAllowed) stopFollowing();
        const key = JSON.stringify({ joints, edges, diagnostic });
        if (geometryKey !== key) {
            release(root); geometryKey = key;
            const color = diagnostic ? 0xf0c278 : 0x76dce7;
            if (joints.length) {
                const geometry = new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute(joints.flatMap(joint => joint.position_m), 3));
                const points = new THREE.Points(geometry, new THREE.PointsMaterial({ color, size: .065, sizeAttenuation: true, transparent: true, opacity: .95, depthTest: false, depthWrite: false }));
                points.name = 'perception-supplied-joints'; points.renderOrder = 25;
                root.add(points);
            }
            if (edges.length) {
                const positions = edges.flatMap(edge => edge.flatMap(id => byId.get(id).position_m));
                const geometry = new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
                const options = { color, transparent: true, opacity: .9, depthTest: false, depthWrite: false };
                const material = diagnostic ? new THREE.LineDashedMaterial({ ...options, dashSize: .08, gapSize: .045 }) : new THREE.LineBasicMaterial(options);
                const lines = new THREE.LineSegments(geometry, material);
                if (diagnostic) lines.computeLineDistances();
                lines.name = 'perception-supplied-edges'; lines.renderOrder = 24;
                root.add(lines);
            }
        }
        updateFollow();
        return readback();
    }
    function setFollowing(value) {
        assertOpen();
        if (!value) { stopFollowing(); return readback(); }
        if (!usable() || !followAllowed || !anchor) return readback();
        if (!following) {
            if (resident.rig.inCameraPose?.()) return readback();
            savedPose = resident.rig.snapshotPose();
            following = true;
        }
        updateFollow();
        return readback();
    }
    function screenAnchor() {
        assertOpen();
        if (!usable() || !anchor) return null;
        resident.root.updateWorldMatrix(true, false);
        const world = resident.root.localToWorld(new THREE.Vector3(...anchor.position_m));
        resident.camera.updateWorldMatrix(true, false);
        return { ...engine.picking.projectToScreen(world), jointId: anchor.id, position_m: [...anchor.position_m], diagnostic };
    }
    function readback() {
        return { scope: { ...pin }, disposed, status, reason, diagnostic, qualification, jointCount: joints.length, edgeCount: edges.length,
            following, followPaused, followAllowed, renderableDiagnostic, renderableApproximate, noLightingAuthority: true };
    }
    function dispose() { if (disposed) return; clear('unavailable', 'layer_disposed'); root.removeFromParent(); disposed = true; }
    return Object.freeze({ draw, setFollowing, screenAnchor, readback, dispose });
}
