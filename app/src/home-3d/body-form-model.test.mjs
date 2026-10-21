import test from 'node:test';
import assert from 'node:assert/strict';
import {buildBodyForm} from './body-form-model.mjs';
import {bodyPerson,estimatedLowerBody,BODY_FRAME_KEY as frameKey} from '../../../tools/fixtures/body-form-fixtures.mjs';
const build=person=>buildBodyForm(person,{frameKey});

test('authored standing and seated forms connect torso/head/limbs at their source joints in Z-up metres',()=>{
  for(const seated of [false,true]){
    const person=bodyPerson({seated,offset:[2,3,.4]}),form=build(person);
    assert.equal(form.status,'complete');assert.equal(form.observedJoints,17);assert.equal(form.estimatedJoints,0);
    assert.equal(form.measuredStature,false);assert.equal(form.physicalOcclusion,false);
    for(const part of form.parts.filter(p=>p.kind==='segment'&&p.key!=='neck')){
      assert.deepEqual(part.start,person.joints.find(j=>j.id===part.jointIds[0]).position_m);
      assert.deepEqual(part.end,person.joints.find(j=>j.id===part.jointIds[1]).position_m);
    }
    assert.ok(form.parts.find(p=>p.key==='head').center[2]>1.3);
    const thigh=form.parts.find(p=>p.key==='left-thigh');
    if(seated)assert.ok(Math.abs(thigh.end[1]-thigh.start[1])>.4,'seated knee extends forward, not to a guessed floor');
    assert.ok(form.parts.find(p=>p.key==='left-foot').center[2]>.4,'no floor snap');
  }
});

test('partial upper-body evidence yields disclosed neutral chest volume, never guessed pelvis/legs',()=>{
  const person=bodyPerson();person.joints=person.joints.filter(j=>[0,5,6,7,9].includes(j.id));
  const form=build(person);assert.equal(form.status,'partial');assert.equal(form.observedJoints,5);
  assert.equal(form.estimatedJoints,0,'neutral volume does not invent observed or fitted joints');
  assert.equal(form.parts.find(p=>p.key==='upper-chest').estimated,true);
  assert.ok(!form.parts.some(p=>/pelvis|thigh|calf|foot|right-upper-arm/.test(p.key)));
  person.joints=person.joints.filter(j=>j.id!==6);assert.equal(build(person).status,'unavailable');
  const wrongHead=bodyPerson();wrongHead.joints=wrongHead.joints.filter(j=>[0,5,6].includes(j.id));
  wrongHead.joints[0].position_m=[9,8,7];assert.equal(build(wrongHead).status,'unavailable','an incoherent head cannot justify a disconnected neutral chest');
});

test('optional rig estimates require exact-frame provenance and cannot overwrite observed evidence',()=>{
  const rig=estimatedLowerBody(bodyPerson({seated:true}));
  assert.equal(build(rig).status,'estimated');assert.equal(build(rig).estimatedJoints,6);
  assert.ok(build(rig).parts.filter(p=>/thigh|calf|pelvis/.test(p.key)).every(p=>p.estimated));
  for(const mutate of [p=>p.body_pose.frame_key='e'.repeat(64),p=>p.body_pose.frame='camera',
    p=>p.body_pose.joints[5].position_m[0]+=.01,p=>p.body_pose.joints[11].status='observed',
    p=>delete p.body_pose.joints[11].method,p=>p.body_pose.joints.push(p.body_pose.joints[0])]){
    const p=structuredClone(rig);mutate(p);assert.equal(build(p).status,'unavailable');
  }
});

test('outlier and crossed-depth limb segments cannot stretch across the room or connect people',()=>{
  const p=bodyPerson();p.joints.find(j=>j.id===7).position_m=[9,8,7];
  const form=build(p);assert.equal(form.status,'partial');assert.ok(form.rejected.includes('left-upper-arm'));
  assert.ok(!form.parts.some(part=>part.key==='left-forearm'),'do not render a detached forearm after rejecting its upper arm');
  assert.equal(build(bodyPerson({offset:[20,30,40]})).status,'complete','room placement is not secretly recentered');
});
