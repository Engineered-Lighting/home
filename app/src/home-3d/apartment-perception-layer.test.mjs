import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { createRig } from './rig.js';
import { createPicking } from './picking.js';
import { attachApartmentPerceptionLayer } from './apartment-perception-layer.mjs';

const scope = () => ({ siteId: 'victoria', contentRevision: 'authored-room-1', frameDigest: 'a'.repeat(64), registrationVersion: 1,
    cameraId: 'den', sessionId: 'authored-session-1', generation: 1 });
function fixture(options = {}) {
    const pin = scope(), apartmentRoot = new THREE.Group(), unrelated = new THREE.Object3D();
    apartmentRoot.name = 'resident-room'; apartmentRoot.rotation.x = -Math.PI / 2; apartmentRoot.position.set(1, 0, 2);
    apartmentRoot.add(unrelated); apartmentRoot.updateMatrixWorld(true);
    const camera = new THREE.PerspectiveCamera(35, 1280 / 800, .05, 200), rig = createRig(camera);
    rig.fitBounds(new THREE.Box3(new THREE.Vector3(-8, -1, -8), new THREE.Vector3(8, 6, 8)));
    rig.goTo({ az: 3, el: 1, zoom: 0, dur: 0 });
    const engine = { apartmentRoot, camera, rig, renderer: { domElement: {} }, siteId: pin.siteId, contentRevision: pin.contentRevision,
        sceneIdentity: { siteId: pin.siteId, contentRevision: pin.contentRevision, frameDigest: pin.frameDigest, registrationVersion: 1 },
        picking: createPicking(camera, { clientWidth: 1280, clientHeight: 800 }) };
    const layer = attachApartmentPerceptionLayer(engine, pin, { reducedMotion: true, ...options });
    const joint = (id, position_m, extra = {}) => ({ id, position_m, ...extra });
    const view = (offset = 0) => {
        const anchor = joint('hips', [offset, 1, .9]);
        return { scope: { ...pin }, sourceEpoch: 'authored-source-1', roomPose: { admitted: true, status: 'current', reason: null, qualification: 'qualified_viewing', diagnostic: false,
            noLightingAuthority: true, joints: [anchor, joint('head', [offset, 1, 1.7]), joint('left_hand', [offset - .4, 1, 1.2])],
            edges: [['hips', 'head'], ['head', 'left_hand']], anchor, age: { status: 'current', seconds: .2, uncertainty_s: .01, reason: null } },
            follow: { allowed: true, anchor, reason: null } };
    };
    return { engine, layer, pin, unrelated, view, joint, root: () => apartmentRoot.getObjectByName('home-apartment-perception-owned') };
}
const poseValues = rig => {
    const pose = rig.snapshotPose();
    return { ...pose, target: pose.target.toArray(), homeTarget: pose.homeTarget.toArray(), targetBounds: pose.targetBounds && [pose.targetBounds.min.toArray(), pose.targetBounds.max.toArray()] };
};

test('supplied joints and edges use the existing room/camera and preserve other layers', () => {
    const f = fixture(), before = poseValues(f.engine.rig), view = f.view();
    const readback = f.layer.draw(view);
    assert.equal(readback.jointCount, 3); assert.equal(readback.edgeCount, 2);
    assert.equal(readback.noLightingAuthority, true); assert.equal(readback.diagnostic, false);
    const root = f.root(), points = root.getObjectByName('perception-supplied-joints'), lines = root.getObjectByName('perception-supplied-edges');
    assert.equal(root.parent, f.engine.apartmentRoot);
    assert.deepEqual(Array.from(points.geometry.attributes.position.array).map(n => Math.round(n * 100) / 100), [0, 1, .9, 0, 1, 1.7, -.4, 1, 1.2]);
    assert.equal(lines.material.type, 'LineBasicMaterial');
    const expected = f.engine.picking.projectToScreen(f.engine.apartmentRoot.localToWorld(new THREE.Vector3(0, 1, .9)));
    assert.deepEqual(f.layer.screenAnchor(), { ...expected, jointId: 'hips', position_m: [0, 1, .9], diagnostic: false });
    assert.deepEqual(poseValues(f.engine.rig), before);
    view.roomPose.anchor.position_m[0] = 99;
    assert.deepEqual(f.layer.screenAnchor().position_m, [0, 1, .9], 'external mutation does not move owned anchors');
    f.layer.dispose(); assert.deepEqual(f.engine.apartmentRoot.children, [f.unrelated]);
});

test('diagnostic alignment is amber and dashed; missing, hidden and invalid joints are never completed', () => {
    const f = fixture(), v = f.view();
    v.roomPose.qualification = 'diagnostic_alignment'; v.roomPose.diagnostic = true;
    v.roomPose.joints.push(f.joint('hidden', [0, 0, 1], { visible: false }), f.joint('invalid', [NaN, 0, 1]), f.joint('gpu_overflow', [1e100, 0, 1]));
    v.roomPose.edges.push(['head', 'missing'], ['head', 'hidden'], ['head', 'invalid'], ['head', 'hips']);
    f.layer.draw(v);
    assert.equal(f.layer.readback().jointCount, 3); assert.equal(f.layer.readback().edgeCount, 2);
    const lines = f.root().getObjectByName('perception-supplied-edges');
    assert.equal(lines.material.type, 'LineDashedMaterial'); assert.equal(lines.material.color.getHex(), 0xf0c278);
    assert.equal(lines.geometry.attributes.lineDistance.count, 4);
    v.roomPose.joints = []; v.roomPose.edges = [['hips', 'head']];
    f.layer.draw(v);
    assert.equal(f.layer.readback().jointCount, 0); assert.equal(f.layer.readback().edgeCount, 0);
    assert.equal(f.layer.screenAnchor(), null); assert.equal(f.layer.setFollowing(true).following, false);
    f.layer.dispose();
});

test('Follow uses only the supplied joint, transforms once, advances with observations and restores the actual rig snapshot', () => {
    const f = fixture(), before = poseValues(f.engine.rig);
    f.layer.draw(f.view()); f.layer.setFollowing(true);
    assert.equal(f.layer.readback().following, true); assert.equal(f.engine.rig.zoomIndex(), 1);
    const expected = f.engine.apartmentRoot.localToWorld(new THREE.Vector3(0, 1, .9));
    assert.ok(f.engine.rig.snapshotPose().target.distanceTo(expected) < 1e-12);
    f.layer.draw(f.view(.7));
    const moved = f.engine.apartmentRoot.localToWorld(new THREE.Vector3(.7, 1, .9));
    assert.ok(f.engine.rig.snapshotPose().target.distanceTo(moved) < 1e-12);
    assert.notDeepEqual(poseValues(f.engine.rig), before);
    f.layer.setFollowing(false); assert.deepEqual(poseValues(f.engine.rig), before);
    assert.equal(f.layer.readback().following, false);
    f.layer.dispose();
});

test('expiry, refusal, source stop and view-scope replacement clear geometry and stop/restore Follow', () => {
    for (const mutation of [
        v => { v.roomPose = { admitted: false, status: 'stale', reason: 'expired' }; },
        v => { v.roomPose = { admitted: false, status: 'refused', reason: 'registration_missing' }; },
        v => { v.roomPose = { admitted: false, status: 'unavailable', reason: 'source_stopped' }; },
        v => { v.scope.sessionId = 'different-session'; },
        v => { v.scope.generation++; },
    ]) {
        const f = fixture(), before = poseValues(f.engine.rig);
        f.layer.draw(f.view()); f.layer.setFollowing(true);
        const v = f.view(); mutation(v); f.layer.draw(v);
        assert.equal(f.layer.readback().following, false); assert.equal(f.layer.readback().jointCount, 0);
        assert.equal(f.layer.screenAnchor(), null); assert.equal(f.root().children.length, 0);
        assert.deepEqual(poseValues(f.engine.rig), before);
        f.layer.dispose();
    }
});

test('lost anchor or denied Follow cannot synthesize a target while remaining joints remain visible', () => {
    const f = fixture(), before = poseValues(f.engine.rig);
    f.layer.draw(f.view()); f.layer.setFollowing(true);
    const v = f.view(); v.roomPose.joints[0].visible = false;
    f.layer.draw(v);
    assert.equal(f.layer.readback().following, false); assert.equal(f.layer.readback().jointCount, 2);
    assert.equal(f.layer.screenAnchor(), null); assert.deepEqual(poseValues(f.engine.rig), before);
    const denied = f.view(); denied.follow.anchor = { ...denied.follow.anchor, position_m: [3, 3, 3] };
    f.layer.draw(denied); assert.equal(f.layer.setFollowing(true).following, false);
    f.layer.dispose();
});

test('engine identity or resident replacement retires visuals without restoring an old-home camera pose', () => {
    for (const mutate of [
        e => { e.siteId = 'echo'; }, e => { e.contentRevision = 'different'; },
        e => { e.sceneIdentity.siteId = 'echo'; }, e => { e.sceneIdentity.contentRevision = 'different'; },
        e => { e.sceneIdentity.frameDigest = 'b'.repeat(64); }, e => { e.sceneIdentity.registrationVersion++; },
        e => { e.apartmentRoot = new THREE.Group(); }, e => { e.camera = new THREE.PerspectiveCamera(); },
        e => { e.renderer.domElement = {}; },
    ]) {
        const f = fixture(); f.layer.draw(f.view()); f.layer.setFollowing(true);
        const followingPose = poseValues(f.engine.rig); mutate(f.engine);
        f.layer.draw(f.view());
        assert.equal(f.layer.readback().reason, 'resident_identity_changed'); assert.equal(f.layer.readback().jointCount, 0);
        assert.equal(f.layer.readback().following, false); assert.deepEqual(poseValues(f.engine.rig), followingPose);
        f.layer.dispose(); assert.deepEqual(poseValues(f.engine.rig), followingPose);
    }
});

test('Follow preserves a closer zoom and normal motion uses the real rig tween without predicting another position', () => {
    const f = fixture({ reducedMotion: false });
    f.engine.rig.goTo({ zoom: 3, dur: 0 });
    const before = poseValues(f.engine.rig), targetBefore = f.engine.rig.snapshotPose().target.clone();
    f.layer.draw(f.view(2)); f.layer.setFollowing(true);
    assert.equal(f.engine.rig.zoomIndex(), 3);
    assert.equal(f.engine.rig.isTweening(), true);
    assert.deepEqual(f.engine.rig.snapshotPose().target, targetBefore);
    f.engine.rig.update(.2);
    const target = f.engine.apartmentRoot.localToWorld(new THREE.Vector3(2, 1, .9));
    assert.ok(f.engine.rig.snapshotPose().target.distanceTo(target) < 1e-12);
    f.engine.rig.update(10);
    assert.ok(f.engine.rig.snapshotPose().target.distanceTo(target) < 1e-12, 'no extrapolation beyond the last supplied joint');
    f.layer.dispose(); assert.deepEqual(poseValues(f.engine.rig), before);
});

test('current-view guard, ambiguous IDs and missing authority or qualification refuse stale display', () => {
    let current = true;
    const f = fixture({ isCurrent: () => current }), before = poseValues(f.engine.rig);
    f.layer.draw(f.view()); f.layer.setFollowing(true); current = false;
    assert.equal(f.layer.screenAnchor(), null); assert.deepEqual(poseValues(f.engine.rig), before);
    current = true;
    for (const mutate of [v => { v.roomPose.noLightingAuthority = false; }, v => { v.roomPose.qualification = 'unknown'; },
        v => { v.roomPose.joints.push({ ...v.roomPose.joints[0] }); }, v => { v.scope.extra = true; }]) {
        f.layer.draw(f.view()); const v = f.view(); mutate(v); f.layer.draw(v);
        assert.equal(f.layer.readback().jointCount, 0); assert.equal(f.layer.setFollowing(true).following, false);
    }
    f.layer.dispose();
});

test('repeated identical draws retain GPU resources and disposal releases only owned resources once', () => {
    const f = fixture(); f.layer.draw(f.view());
    const children = [...f.root().children]; let geometryDisposals = 0, materialDisposals = 0;
    children.forEach(node => { node.geometry.addEventListener('dispose', () => geometryDisposals++); node.material.addEventListener('dispose', () => materialDisposals++); });
    for (let i = 0; i < 100; i++) f.layer.draw(f.view());
    assert.deepEqual(f.root().children, children); assert.equal(geometryDisposals, 0);
    f.layer.setFollowing(true); const originalRoot = f.root();
    f.layer.dispose(); f.layer.dispose();
    assert.equal(geometryDisposals, 2); assert.equal(materialDisposals, 2); assert.equal(originalRoot.parent, null);
    assert.deepEqual(f.engine.apartmentRoot.children, [f.unrelated]); assert.equal(f.layer.readback().disposed, true);
    assert.throws(() => f.layer.draw(f.view()), /disposed/);
});

test('scope and engine admission fail before attaching any owned resources', () => {
    const f = fixture(); f.layer.dispose();
    assert.throws(() => attachApartmentPerceptionLayer(f.engine, { ...f.pin, generation: -1 }), /scope_invalid/);
    f.engine.sceneIdentity.frameDigest = 'b'.repeat(64);
    assert.throws(() => attachApartmentPerceptionLayer(f.engine, f.pin), /resident_mismatch/);
    assert.deepEqual(f.engine.apartmentRoot.children, [f.unrelated]);
});

test('null registration remains attachable for diagnostics but cannot place room joints or Follow', () => {
    const f = fixture(); f.layer.dispose();
    f.engine.sceneIdentity.registrationVersion = null;
    const pin = { ...f.pin, registrationVersion: null }, layer = attachApartmentPerceptionLayer(f.engine, pin);
    const v = f.view(); v.scope = pin;
    layer.draw(v);
    assert.equal(layer.readback().jointCount, 0); assert.equal(layer.readback().status, 'refused');
    assert.equal(layer.setFollowing(true).following, false); assert.equal(layer.screenAnchor(), null);
    layer.dispose();
});

test('original numeric joint IDs retain their actual endpoints and supplied Follow anchor', () => {
    const f = fixture(), v = f.view();
    v.roomPose.joints = [f.joint(0, [0, 1, .9]), f.joint(1, [0, 1, 1.7]), f.joint('1', [.3, 1, 1.4])];
    v.roomPose.edges = [[0, 1], [1, '1'], ['1', 1]];
    v.roomPose.anchor = v.roomPose.joints[0]; v.follow.anchor = { ...v.roomPose.joints[0] };
    f.layer.draw(v);
    assert.equal(f.layer.readback().jointCount, 3); assert.equal(f.layer.readback().edgeCount, 2);
    assert.equal(f.layer.screenAnchor().jointId, 0);
    assert.equal(f.layer.setFollowing(true).following, true);
    const expected = f.engine.apartmentRoot.localToWorld(new THREE.Vector3(0, 1, .9));
    assert.ok(f.engine.rig.snapshotPose().target.distanceTo(expected) < 1e-12);
    f.layer.dispose();
});

const pendingPair = f => ({ ...f.view(), status: 'available', sessionState: 'recording',
    roomPose: { admitted: false, status: 'refused', reason: 'displayed_frame_mismatch', noLightingAuthority: true,
        qualification: 'diagnostic_alignment', diagnostic: true, joints: [], edges: [], anchor: null,
        age: { status: 'current', seconds: .1, uncertainty_s: .01 } }, follow: { allowed: false, anchor: null } });

test('a qualified next-image pause freezes actual rig motion and resumes Follow without replacing the original Return pose', () => {
    const f = fixture({ reducedMotion: false }), original = poseValues(f.engine.rig);
    f.layer.draw(f.view(1)); f.layer.setFollowing(true); f.engine.rig.update(.05);
    const pausedAt = poseValues(f.engine.rig);
    f.layer.draw(pendingPair(f));
    assert.equal(f.layer.readback().following, true); assert.equal(f.layer.readback().followPaused, true);
    assert.equal(f.layer.readback().jointCount, 0); assert.equal(f.layer.screenAnchor(), null);
    f.engine.rig.update(1);
    assert.deepEqual(poseValues(f.engine.rig), pausedAt, 'the previous target does not keep moving the camera while its pair is absent');
    f.layer.draw(pendingPair(f)); assert.deepEqual(poseValues(f.engine.rig), pausedAt);
    f.layer.draw(f.view(2));
    assert.equal(f.layer.readback().following, true); assert.equal(f.layer.readback().followPaused, false);
    f.engine.rig.update(.2);
    const resumedTarget = f.engine.apartmentRoot.localToWorld(new THREE.Vector3(2, 1, .9));
    assert.ok(f.engine.rig.snapshotPose().target.distanceTo(resumedTarget) < 1e-12);
    f.layer.setFollowing(false); assert.deepEqual(poseValues(f.engine.rig), original);
    f.layer.dispose();
});

test('expiry, unknown timing, nonlive status or scope replacement during a pause cancels and restores the original user pose', () => {
    for (const mutate of [v => { v.roomPose.age.status = 'stale'; }, v => { v.roomPose.age.status = 'unavailable'; },
        v => { v.status = 'unavailable'; }, v => { v.sessionState = 'stopped'; },
        v => { v.roomPose.qualification = 'unknown'; }, v => { v.roomPose.reason = 'registration_not_applicable'; },
        v => { v.scope.generation++; }]) {
        const f = fixture(), original = poseValues(f.engine.rig);
        f.layer.draw(f.view()); f.layer.setFollowing(true); f.layer.draw(pendingPair(f));
        const v = pendingPair(f); mutate(v); f.layer.draw(v);
        assert.equal(f.layer.readback().following, false); assert.equal(f.layer.readback().followPaused, false);
        assert.equal(f.layer.readback().jointCount, 0); assert.deepEqual(poseValues(f.engine.rig), original);
        f.layer.dispose();
    }
});

test('manual cancellation and disposal while paused each restore the original user pose', () => {
    for (const action of ['stop', 'dispose']) {
        const f = fixture(), original = poseValues(f.engine.rig);
        f.layer.draw(f.view()); f.layer.setFollowing(true); f.layer.draw(pendingPair(f));
        if (action === 'stop') f.layer.setFollowing(false); else f.layer.dispose();
        assert.equal(f.layer.readback().following, false); assert.equal(f.layer.readback().followPaused, false);
        assert.deepEqual(poseValues(f.engine.rig), original); f.layer.dispose();
    }
});

test('a changed original source epoch cancels paused Follow and a matched replacement pair never resumes it automatically', () => {
    for (const [previous, next] of [['camera-A', 'camera-B'], [null, 'camera-B'], ['camera-A', null]]) {
        const f = fixture(), original = poseValues(f.engine.rig), initial = f.view(); initial.sourceEpoch = previous;
        f.layer.draw(initial); f.layer.setFollowing(true);
        const pending = pendingPair(f); pending.sourceEpoch = previous;
        f.layer.draw(pending); assert.equal(f.layer.readback().followPaused, true);
        const replacement = pendingPair(f); replacement.sourceEpoch = next;
        f.layer.draw(replacement);
        assert.equal(f.layer.readback().following, false); assert.equal(f.layer.readback().followPaused, false);
        assert.deepEqual(poseValues(f.engine.rig), original);
        const matched = f.view(2); matched.sourceEpoch = next;
        f.layer.draw(matched);
        assert.equal(f.layer.readback().jointCount, 3); assert.equal(f.layer.readback().followAllowed, true);
        assert.equal(f.layer.readback().following, false, 'the new source needs a new explicit Follow choice');
        assert.deepEqual(poseValues(f.engine.rig), original);
        f.layer.dispose();
    }
});
