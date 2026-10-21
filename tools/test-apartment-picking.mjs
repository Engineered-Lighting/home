// Exercise production raycasting with the repository's real Three.js runtime.
// Only the browser bare import is resolved differently; no renderer is changed.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import * as THREE from '../app/src/vendor/three-0.180.0/three.module.js';

const vendorUrl = new URL('../app/src/vendor/three-0.180.0/three.module.js', import.meta.url).href;
const pickingSource = fs.readFileSync(new URL('../app/src/home-3d/picking.js', import.meta.url), 'utf8');
const bareImport = /import \* as THREE from 'three';/g;
assert.equal([...pickingSource.matchAll(bareImport)].length, 1, 'resolve exactly the production Three import');
const resolvedSource = pickingSource.replace(bareImport, `import * as THREE from ${JSON.stringify(vendorUrl)};`);
const { createPicking } = await import(`data:text/javascript;base64,${Buffer.from(resolvedSource).toString('base64')}`);

const rect = Object.freeze({ left: 120, top: 80, width: 800, height: 600 });
const center = Object.freeze([rect.left + rect.width / 2, rect.top + rect.height / 2]);
const host = { clientWidth: rect.width, clientHeight: rect.height, getBoundingClientRect: () => rect };

function closeVector(actual, expected, epsilon = 1e-6) {
  assert.equal(actual.length, expected.length);
  actual.forEach((value, index) => assert.ok(Math.abs(value - expected[index]) <= epsilon,
    `coordinate ${index}: expected ${expected[index]}, got ${value}`));
}

function plane(root, z = 0, { reversed = false } = {}) {
  const geometry = new THREE.PlaneGeometry(4, 4);
  if (reversed) {
    const indices = Array.from(geometry.index.array);
    for (let index = 0; index < indices.length; index += 3) {
      [indices[index + 1], indices[index + 2]] = [indices[index + 2], indices[index + 1]];
    }
    geometry.setIndex(indices);
  }
  const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }));
  mesh.position.z = z;
  root.add(mesh);
  root.updateMatrixWorld(true);
  return mesh;
}

function cameraAt(position, target, up = new THREE.Vector3(0, 1, 0)) {
  const camera = new THREE.PerspectiveCamera(60, rect.width / rect.height, .1, 100);
  camera.up.copy(up);
  camera.position.copy(position);
  camera.lookAt(target);
  camera.updateMatrixWorld(true);
  camera.updateProjectionMatrix();
  return camera;
}

function dispose(root) {
  root.traverse(object => {
    object.geometry?.dispose();
    object.material?.dispose();
  });
}

function geometrySnapshot(mesh) {
  return {
    geometry: mesh.geometry,
    index: Array.from(mesh.geometry.index.array),
    attributes: Object.fromEntries(Object.entries(mesh.geometry.attributes).map(([name, attribute]) =>
      [name, { version: attribute.version, array: Array.from(attribute.array) }])),
    visible: mesh.visible,
    materialVisible: mesh.material.visible,
    side: mesh.material.side,
  };
}

test('screen picks reset the shared near/far range left by a short beam trace', () => {
  const root = new THREE.Group();
  try {
    const mesh = plane(root);
    const camera = cameraAt(new THREE.Vector3(0, 0, 10), new THREE.Vector3());
    const picking = createPicking(camera, host);
    const trace = picking.beamTrace(root, [mesh], [0, 0, 1], [0, 0, -1], 20, .05);
    assert.equal(trace.center, null, 'the short beam cannot reach the plane');
    assert.equal(picking.raycaster.near, .015);
    assert.equal(picking.raycaster.far, .3);
    const hits = picking.pick([mesh], ...center);
    assert.ok(hits.some(hit => hit.object === mesh && Math.abs(hit.distance - 10) < 1e-6));
    assert.equal(picking.raycaster.near, 0);
    assert.equal(picking.raycaster.far, Infinity);
  } finally { dispose(root); }
});

for (const reversed of [false, true]) {
  test(`surface hits preserve exact apartment coordinates and a camera-facing normal (${reversed ? 'reversed' : 'ordinary'} winding)`, () => {
    const root = new THREE.Group();
    try {
      root.position.set(3, 1, -2);
      root.rotation.set(-Math.PI / 2, 0, .37);
      const mesh = plane(root, 1.2, { reversed });
      mesh.position.x = .6;
      mesh.position.y = -.4;
      mesh.rotation.set(.2, .4, -.15);
      root.updateMatrixWorld(true);
      const pointWorld = mesh.localToWorld(new THREE.Vector3(.25, -.15, 0));
      const expectedPoint = root.worldToLocal(pointWorld.clone()).toArray();
      const expectedNormal = new THREE.Vector3(0, 0, 1).applyQuaternion(mesh.quaternion).normalize();
      const rootQuaternion = root.getWorldQuaternion(new THREE.Quaternion());
      const worldNormal = expectedNormal.clone().applyQuaternion(rootQuaternion);
      const camera = cameraAt(pointWorld.clone().addScaledVector(worldNormal, 6), pointWorld);
      const picking = createPicking(camera, host);
      const hit = picking.surfaceHit(root, [mesh], ...center);
      assert.ok(hit);
      assert.equal(hit.object, mesh);
      closeVector(hit.point, expectedPoint);
      closeVector(hit.normal, expectedNormal.toArray());
      assert.ok(new THREE.Vector3(...hit.normal).applyQuaternion(rootQuaternion)
        .dot(picking.raycaster.ray.direction) < 0, 'the returned face points toward the viewing ray');
    } finally { dispose(root); }
  });
}

test('presentation filtering can choose a deeper hit without changing independent collision geometry', () => {
  const root = new THREE.Group();
  try {
    root.position.set(3, 1, -2);
    root.rotation.x = -Math.PI / 2;
    const foreground = plane(root, 1), deeper = plane(root, 0);
    const before = [foreground, deeper].map(geometrySnapshot);
    const target = root.localToWorld(new THREE.Vector3(.25, .1, 0));
    const camera = cameraAt(root.localToWorld(new THREE.Vector3(.25, .1, 6)), target,
      new THREE.Vector3(0, 1, 0).applyQuaternion(root.quaternion));
    const picking = createPicking(camera, host);
    assert.equal(picking.surfaceHit(root, [foreground, deeper], ...center).object, foreground);
    const visited = [];
    const hit = picking.surfaceHit(root, [foreground, deeper], ...center, {
      acceptPoint(point, intersection) {
        visited.push({ point: [...point], object: intersection.object });
        return point[2] < .5;
      },
    });
    assert.equal(visited[0].object, foreground);
    closeVector(visited[0].point, [.25, .1, 1]);
    assert.equal(visited.at(-1).object, deeper);
    assert.equal(hit.object, deeper);
    closeVector(hit.point, [.25, .1, 0]);
    closeVector(hit.normal, [0, 0, 1]);
    assert.deepEqual([foreground, deeper].map(geometrySnapshot), before);
    assert.equal(picking.surfaceHit(root, [foreground, deeper], ...center).object, foreground,
      'the optional predicate must not change later unfiltered picks');
    const trace = picking.beamTrace(root, [foreground, deeper], [.25, .1, 6], [0, 0, -1], 20, 6);
    assert.equal(trace.center.object, foreground);
    closeVector(trace.center.point, [.25, .1, 1]);
    assert.equal(trace.blocked, true, 'independent beams still see the foreground blocker');
  } finally { dispose(root); }
});

test('surface hits fail closed for rejected faces and rays with no mesh intersection', () => {
  const root = new THREE.Group();
  try {
    const mesh = plane(root);
    const camera = cameraAt(new THREE.Vector3(0, 0, 10), new THREE.Vector3());
    const picking = createPicking(camera, host);
    assert.equal(picking.surfaceHit(root, [mesh], ...center, { acceptPoint: () => false }), null);
    assert.equal(picking.surfaceHit(root, [], ...center), null);
    assert.equal(picking.surfaceHit(root, [mesh], rect.left, rect.top), null);
  } finally { dispose(root); }
});

test('intersections without a point are ignored before presentation filtering', () => {
  const root = new THREE.Group();
  try {
    const mesh = plane(root);
    const camera = cameraAt(new THREE.Vector3(0, 0, 10), new THREE.Vector3());
    const picking = createPicking(camera, host);
    picking.pick = () => [{ object: mesh }, { object: mesh, point: null }];
    assert.equal(picking.surfaceHit(root, [mesh], ...center, {
      acceptPoint() { assert.fail('a missing point must never enter the predicate'); },
    }), null);
  } finally { dispose(root); }
});


for (const method of ['surfaceHit','surfaceHitUnderCutaway']) test(`${method} uses a newly attached mesh transform before its first rendered frame`, () => {
  const root = new THREE.Group();
  root.position.set(3, 1, -2);root.rotation.x=-Math.PI/2;
  const fresh = new THREE.Group();fresh.position.set(.4,.2,.3);
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(4,4),new THREE.MeshBasicMaterial({side:THREE.DoubleSide}));
  mesh.position.z=1.2;fresh.add(mesh);root.add(fresh);
  try{
    const expected=[.4,.2,1.5];
    // localToWorld updates the apartment root, as the UI's projection does,
    // but no render has propagated that transform to the new mesh yet.
    const target=root.localToWorld(new THREE.Vector3(...expected));
    const normal=new THREE.Vector3(0,0,1).applyQuaternion(root.quaternion);
    const camera=cameraAt(target.clone().addScaledVector(normal,6),target);
    const picking=createPicking(camera,host),before=geometrySnapshot(mesh);
    const hit=picking[method](root,[fresh],...center);
    assert.ok(hit,'the just-loaded mesh is pickable before a render');
    closeVector(hit.point,expected);closeVector(hit.normal,[0,0,1]);
    assert.deepEqual(geometrySnapshot(mesh),before,'picking does not change mesh geometry or material');
  }finally{dispose(root);}
});
