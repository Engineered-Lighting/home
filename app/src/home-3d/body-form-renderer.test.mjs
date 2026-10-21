import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import {createBodyFormRenderer} from './body-form-renderer.mjs';
import {bodyPerson,estimatedLowerBody,BODY_FRAME_KEY as frame_key} from '../../../tools/fixtures/body-form-fixtures.mjs';

test('real meshes preserve room depth and one Z-up conversion; estimated pose stays distinguishable',()=>{
  const root=new THREE.Group();root.rotation.x=-Math.PI/2;
  const renderer=createBodyFormRenderer(root),p=estimatedLowerBody(bodyPerson({seated:true,offset:[2,3,4]}));
  let draws=0;const result=renderer.update({frame_key,people:[p]},()=>draws++);
  assert.equal(result.status,'estimated');assert.equal(result.estimatedJoints,6);
  const head=root.getObjectByName('body-form-head'),leg=root.getObjectByName('body-form-left-thigh');
  assert.equal(head.material.depthTest,true);assert.equal(head.material.depthWrite,true);
  assert.equal(leg.material.wireframe,true);assert.equal(leg.material.depthWrite,false);assert.equal(leg.castShadow,false);
  root.updateMatrixWorld(true);
  const world=head.getWorldPosition(new THREE.Vector3());
  assert.ok(Math.abs(world.y-head.position.z)<1e-6);assert.ok(Math.abs(world.z+head.position.y)<1e-6);
  head.onAfterRender();assert.equal(draws,1,'completion stays attached to actual mesh rendering');
  renderer.dispose();assert.equal(root.children.length,0);
});

test('frame-scoped replacement withdraws occluded limbs without association or geometry growth',()=>{
  const root=new THREE.Group(),renderer=createBodyFormRenderer(root),first=bodyPerson({id:'per-frame-crop'});
  renderer.update({frame_key,people:[first,bodyPerson({offset:[2,0,0],id:'other-crop'})]},()=>{});
  const allocated=root.children.length;
  const partial=bodyPerson({offset:[7,0,0],id:'per-frame-crop'});partial.joints=partial.joints.filter(j=>[0,5,6].includes(j.id));
  for(let n=0;n<20;n++)renderer.update({frame_key,people:[partial]},()=>{});
  assert.equal(root.children.length,allocated,'buffers are bounded and reused');
  assert.ok(root.children.filter(m=>m.visible).every(m=>m.position.x>6),'no limb from the old crop survives');
  assert.ok(!root.children.some(m=>m.visible&&/thigh|calf/.test(m.name)));
  const missing=renderer.update({frame_key,people:[{instance_id:'per-frame-crop',joints:[]}]},()=>{});
  assert.equal(missing.status,'unavailable');assert.equal(root.children.filter(m=>m.visible).length,0);
  renderer.dispose();renderer.dispose();assert.equal(root.children.length,0);
});
