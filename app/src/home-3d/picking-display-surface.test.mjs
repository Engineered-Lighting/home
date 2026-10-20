import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { createPicking } from './picking.js';

test('display surface picking skips cutaway faces while canonical picking retains them', () => {
    const camera = new THREE.PerspectiveCamera(60, 1, .01, 20);
    camera.position.set(0, 0, 3); camera.lookAt(0, 0, 0); camera.updateMatrixWorld(true);
    const host = { getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 800 }) };
    const picking = createPicking(camera, host), root = new THREE.Group();
    const material = new THREE.MeshBasicMaterial({ side: THREE.DoubleSide, visible: false });
    const front = new THREE.Mesh(new THREE.PlaneGeometry(4, 4), material); front.position.z = 1;
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(4, 4), material);
    root.add(front, floor); root.updateMatrixWorld(true);
    try {
        const canonical = picking.surfaceHit(root, [root], 400, 400);
        assert.equal(canonical.point[2], 1);
        const displayed = picking.surfaceHit(root, [root], 400, 400, { acceptPoint: point => point[2] < .5 });
        assert.ok(Math.abs(displayed.point[2]) < 1e-8, 'cut-away foreground face cannot steal the visible floor target');
        assert.equal(picking.surfaceHit(root, [root], 400, 400, { acceptPoint: () => false }), null);
        assert.equal(picking.surfaceHit(root, [root], 400, 400).point[2], 1, 'display filtering never alters canonical geometry');
    } finally { front.geometry.dispose(); floor.geometry.dispose(); material.dispose(); }
});
