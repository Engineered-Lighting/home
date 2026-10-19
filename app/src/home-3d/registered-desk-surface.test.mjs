import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { registerHooks } from 'node:module';

registerHooks({ resolve(specifier, context, nextResolve) {
    return specifier === 'three'
        ? { url: new URL('../vendor/three-0.180.0/three.module.js', import.meta.url).href, shortCircuit: true }
        : nextResolve(specifier, context);
} });
const THREE = await import('three');
const { createPicking, createRegisteredDeskSurfacePicker } = await import('./picking.js');
const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const copy = value => JSON.parse(JSON.stringify(value));
const near = (actual, expected) => actual.forEach((n, i) => assert.ok(Math.abs(n-expected[i]) < 1e-8, `${actual} != ${expected}`));

function fixture(name = 'mesh.glb') {
    const root = new THREE.Group();
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(4, 4), new THREE.MeshBasicMaterial());
    const proxy = new THREE.Mesh(new THREE.PlaneGeometry(.1, .1), new THREE.MeshBasicMaterial());
    proxy.position.x = 10; root.add(mesh, proxy); root.updateMatrixWorld(true);
    const camera = new THREE.PerspectiveCamera(60, 1, .01, 50);
    camera.position.set(0, 0, 4); camera.lookAt(0, 0, 0); camera.updateMatrixWorld(true);
    const host = { getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 800 }) };
    const picking = createPicking(camera, host);
    const manifest = { siteId: 'victoria', revision: 'authored-room-r1', registrationVersion: 2,
        assets: { 'frame.json': { sha256: 'a'.repeat(64) }, 'collision.glb': { sha256: 'b'.repeat(64) },
            'mesh.glb': { sha256: 'c'.repeat(64) }, 'mesh.mobile.glb': { sha256: 'd'.repeat(64) } } };
    const binding = { content_revision: manifest.revision, registration_version: manifest.registrationVersion,
        manifest_digest: digest(manifest), frame_digest: manifest.assets['frame.json'].sha256,
        canonical_collision_digest: manifest.assets['collision.glb'].sha256 };
    const state = { mesh, debug: { status: 'ready', meshSource: name, meshFallback: false,
        dollhouseCutaway: { enabled: false } } };
    const modes = { getMesh: () => state.mesh, debugInfo: () => state.debug };
    const sceneIdentity = { siteId: 'victoria', contentRevision: manifest.revision,
        registrationVersion: manifest.registrationVersion, frameDigest: binding.frame_digest };
    const create = () => createRegisteredDeskSurfacePicker({ apartmentRoot: root, modes, picking,
        getManifest: () => manifest, sceneIdentity, digest });
    const dispose = () => [mesh, proxy].forEach(value => { value.geometry.dispose(); value.material.dispose(); });
    return { root, mesh, proxy, camera, picking, manifest, binding, state, modes, sceneIdentity, create, dispose };
}

test('registered detailed mesh supplies an exact surface even when the collision proxy has no hit', () => {
    const f = fixture();
    try {
        assert.equal(f.picking.surfaceHit(f.root, [f.proxy], 400, 400), null);
        const picker = f.create(), hit = picker.surfaceHit(400, 400, f.binding);
        near(hit.point, [0, 0, 0]); near(hit.normal, [0, 0, 1]); assert.equal(hit.object, f.mesh);
        assert.deepEqual(hit.surface_ref, { asset_name: 'mesh.glb', asset_sha256: 'c'.repeat(64), manifest_digest: f.binding.manifest_digest });
        assert.deepEqual(picker.reference(f.binding), hit.surface_ref);
        assert.equal(picker.surfaceHit(-10000, -10000, f.binding), null);
    } finally { f.dispose(); }
});

test('desktop and mobile meshes retain exact apartment coordinates through the actual root transform', () => {
    for (const name of ['mesh.glb', 'mesh.mobile.glb']) {
        const f = fixture(name);
        try {
            f.mesh.position.z = .7; f.root.position.set(2, -3, 4); f.root.rotation.set(.4, -.3, .7);
            f.root.updateMatrixWorld(true);
            f.camera.position.copy(f.root.localToWorld(new THREE.Vector3(0, 0, 4)));
            f.camera.lookAt(f.root.localToWorld(new THREE.Vector3(0, 0, .7))); f.camera.updateMatrixWorld(true);
            const hit = f.create().surfaceHit(400, 400, f.binding);
            near(hit.point, [0, 0, .7]); near(hit.normal, [0, 0, 1]);
            assert.equal(hit.surface_ref.asset_name, name);
            assert.equal(hit.surface_ref.asset_sha256, f.manifest.assets[name].sha256);
        } finally { f.dispose(); }
    }
});

test('mixed winding is queried double-sided without changing legacy picks or rendered material sides', () => {
    const f = fixture();
    try {
        f.mesh.rotation.x = Math.PI; f.root.updateMatrixWorld(true);
        assert.equal(f.mesh.material.side, THREE.FrontSide);
        assert.equal(f.picking.surfaceHit(f.root, [f.mesh], 400, 400), null);
        const hit = f.create().surfaceHit(400, 400, f.binding);
        near(hit.point, [0, 0, 0]); near(hit.normal, [0, 0, 1]);
        assert.equal(f.mesh.material.side, THREE.FrontSide);
        assert.equal(f.proxy.material.side, THREE.FrontSide);
        assert.equal(f.picking.surfaceHit(f.root, [f.mesh], 400, 400), null);
        f.mesh.raycast = () => { throw new Error('authored-raycast-failure'); };
        assert.throws(() => f.picking.pick([f.mesh], 400, 400, { doubleSided: true }), /authored-raycast-failure/);
        assert.equal(f.mesh.material.side, THREE.FrontSide, 'query failure still restores its material');
        assert.equal(f.proxy.material.side, THREE.FrontSide, 'unselected object is unchanged');
    } finally { f.dispose(); }
});

test('detailed selection uses the same cutaway policy without inventing a replacement surface', () => {
    const f = fixture(), front = new THREE.Mesh(new THREE.PlaneGeometry(4, 4), new THREE.MeshBasicMaterial());
    try {
        const selected = new THREE.Group(); f.root.add(selected); selected.add(f.mesh, front); f.state.mesh = selected;
        front.position.z = 2; f.camera.position.set(.9, 0, 4); f.camera.lookAt(.9, 0, 0);
        f.camera.updateMatrixWorld(true); f.root.updateMatrixWorld(true);
        f.state.debug.dollhouseCutaway = { enabled: true, center: [0, 0, 0], halfExtent: [1, 1, 4],
            direction: [1, 0], threshold: .78, feather: .03, floorZ: .25 };
        const picker = f.create(); near(picker.surfaceHit(400, 400, f.binding).point, [.9, 0, 0]);
        f.mesh.visible = false; selected.remove(f.mesh);
        assert.equal(picker.surfaceHit(400, 400, f.binding), null, 'a discarded face does not become floor geometry');
    } finally { front.geometry.dispose(); front.material.dispose(); f.dispose(); }
});

test('missing, loading, fallback and foreign registered sources refuse without collision substitution', () => {
    const changes = [
        f => { f.state.mesh = null; }, f => { f.state.mesh = null; f.state.debug.status = 'loading'; },
        f => { f.state.debug.meshFallback = true; }, f => { f.state.debug.meshSource = 'collision.glb'; },
        f => { f.state.debug.meshSource = '../mesh.glb'; }, f => { f.state.debug.disposed = true; },
        f => { f.state.debug.frameStatus = 'failed'; }, f => { f.state.debug.loadStatus = { mesh: 'failed' }; },
        f => { f.sceneIdentity.siteId = 'la'; }, f => { f.sceneIdentity.contentRevision = 'other'; },
        f => { f.sceneIdentity.registrationVersion += 1; }, f => { f.sceneIdentity.frameDigest = 'f'.repeat(64); },
        f => { f.manifest.siteId = 'la'; }, f => { f.manifest.assets['mesh.glb'].sha256 = 'wrong'; },
        f => { f.root.remove(f.mesh); },
    ];
    for (const change of changes) {
        const f = fixture();
        try { const picker = f.create(); change(f); assert.throws(() => picker.reference(f.binding), /registered_desk_surface_/); }
        finally { f.dispose(); }
    }
    const f = fixture();
    try {
        const picker = f.create();
        for (const binding of [null, {}, { ...f.binding, extra: true }, { ...f.binding, frame_digest: 'f'.repeat(64) }])
            assert.throws(() => picker.surfaceHit(400, 400, binding), /registered_desk_surface_binding_changed/);
        const changed = copy(f.manifest); changed.extra = true;
        const foreign = createRegisteredDeskSurfacePicker({ apartmentRoot: f.root, modes: f.modes,
            picking: f.picking, getManifest: () => changed, sceneIdentity: f.sceneIdentity, digest });
        assert.throws(() => foreign.reference(f.binding), /registered_desk_surface_binding_changed/);
    } finally { f.dispose(); }
});

test('an attached pinned mesh remains selectable while unrelated assets load, but an absent mesh refuses', () => {
    const f = fixture();
    try {
        const picker = f.create(), reference = picker.reference(f.binding);
        f.state.debug.status = 'loading';
        f.state.debug.loadStatus = { mesh: 'loaded', splat: 'loading', collision: 'loading' };
        assert.deepEqual(picker.reference(f.binding), reference);
        near(picker.surfaceHit(400, 400, f.binding).point, [0, 0, 0]);
        assert.deepEqual(picker.reference(f.binding), reference, 'warmup cannot change four-corner source identity');
        f.state.mesh = null;
        assert.throws(() => picker.reference(f.binding), /registered_desk_surface_loading/);
        f.state.debug.status = 'idle'; f.state.debug.loadStatus.mesh = 'loading';
        assert.throws(() => picker.surfaceHit(400, 400, f.binding), /registered_desk_surface_loading/);
    } finally { f.dispose(); }
});

test('constructing an ordinary non-Victoria engine stays inert until an explicit desk request', () => {
    const f = fixture();
    try {
        f.sceneIdentity.siteId = 'echo-park';
        const picker = createRegisteredDeskSurfacePicker({ apartmentRoot: f.root, modes: f.modes,
            picking: f.picking, getManifest: () => null, sceneIdentity: f.sceneIdentity, digest: () => undefined });
        assert.throws(() => picker.reference(f.binding), /registered_desk_surface_binding_changed/);
    } finally { f.dispose(); }
});

test('source replacement during an actual raycast cannot return a stale registered hit', () => {
    const f = fixture();
    try {
        const raycast = f.mesh.raycast;
        f.mesh.raycast = function (...args) {
            raycast.apply(this, args);
            f.state.debug.meshSource = 'mesh.mobile.glb';
        };
        assert.throws(() => f.create().surfaceHit(400, 400, f.binding), /registered_desk_surface_source_changed/);
        assert.equal(f.mesh.material.side, THREE.FrontSide);
    } finally { f.dispose(); }
});
