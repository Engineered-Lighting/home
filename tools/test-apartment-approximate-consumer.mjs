/* Same-NPZ producer packet -> independent presentation -> actual Three layer.
 * Original camera/model/room inputs are authored; no real placement acceptance. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import * as THREE from 'three';
import { createRig } from '../app/src/home-3d/rig.js';
import { createPicking } from '../app/src/home-3d/picking.js';
import { attachApartmentPerceptionLayer } from '../app/src/home-3d/apartment-perception-layer.mjs';
const require=createRequire(import.meta.url),View=require('../app/src/home-apartment-perception-view.js'),Player=require('../app/src/home-apartment-perception-player.js');
const Stream=require('../app/src/home-den-lab-stream.js');
const raw=readFileSync(new URL('../tools/fixtures/apartment-live-repair-20261018/approximate-room-pose-authored.json',import.meta.url));
assert.equal(createHash('sha256').update(raw).digest('hex'),'fbe9a05ef17ccc277b2a10dbf85fe4c205f331320ae7f0e15812497829ab3636');
const packet=JSON.parse(raw),scope=packet.approximate_pose.scope,copy=x=>structuredClone(x),settle=()=>new Promise(resolve=>setImmediate(resolve));
function envelope(){return {schema:'home-apartment-perception-1',scope:copy(scope),session_state:'recording',source_epoch:packet.frame.source_epoch,
  frame:copy(packet.frame),pose2d:null,models:[],room_pose:null,approximate_room_pose:copy(packet),approximate_receipt_elapsed_s:.2};}
function project(value=envelope(),extra={}){return View.project({envelope:value,scope,clock:null,collisionDigest:'2'.repeat(64),
  displayedFrame:{descriptor:copy(value.frame),scope:copy(scope),key:View.frameKey(value.frame,scope)},
  approximateReceipt:{eventKey:View.approximateEventKey(value.approximate_room_pose),remainingHoldS:1},...extra});}
function controller(){
  let now=0,sequence=0,sub=null,released=0;const timeouts=new Map(),intervals=new Map();
  const env={AbortController,document:{hidden:false},performance:{now:()=>now},HomeApartmentPerceptionView:View,
    setInterval:fn=>{intervals.set(++sequence,fn);return sequence;},clearInterval:id=>intervals.delete(id),
    setTimeout:(fn,ms)=>{timeouts.set(++sequence,{fn,at:now+ms});return sequence;},clearTimeout:id=>timeouts.delete(id)};
  const source={subscribe(value){sub=value;return()=>{sub=null;};},sampleClock:()=>null,
    async readFrame(frame){return {bytes:Uint8Array.of(137,80,78,71),type:'image/png',frame_metadata:{media_sha256:frame.media_sha256}};}};
  const player=Player.create({source,scope,env,collisionDigest:'2'.repeat(64),decode:async(_,{frame})=>({url:'blob:authored-approximate',
    width:frame.width,height:frame.height,dispose(){released++;}})});
  return {player,timeouts,intervals,push:value=>sub.onUpdate(value),status:value=>sub.onStatus(value),get released(){return released;},
    async advance(ms){now+=ms;for(const[id,value]of[...timeouts])if(value.at<=now){timeouts.delete(id);value.fn();}await settle();}};
}

test('actual producer partial joints retain approximate label and every authority stays false',()=>{
  const out=project();assert.equal(out.approximateRoomPose.renderable,true);
  assert.equal(out.approximateRoomPose.label,'Approximate room placement');assert.equal(out.approximateRoomPose.qualification,'approximate_visual_alignment');
  assert.equal(out.approximateRoomPose.joints.length,1);assert.deepEqual(out.approximateRoomPose.edges,[]);
  assert.deepEqual(out.approximateRoomPose.joints[0].position_m,packet.approximate_pose.joints[0].position_m);
  assert.equal(out.approximateRoomPose.error_bound_m,null);assert.equal(out.approximateRoomPose.timing.exposure_error_bound_s,null);
  assert.equal(out.roomPose.admitted,false);assert.equal(out.follow.allowed,false);assert.equal(out.diagnosticRoomPose.renderable,false);
  assert.equal(out.approximateRoomPose.registration.source_fit_status,'refused');
});

test('scene/source/image/review/coarse-check/authority mismatches refuse approximate geometry',()=>{
  for(const change of [v=>v.approximate_room_pose.approximate_pose.scope.contentRevision='wrong',v=>v.approximate_room_pose.approximate_pose.scope.generation++,
    v=>v.approximate_room_pose.frame.media_sha256='0'.repeat(64),v=>v.approximate_room_pose.approximate_pose.source_epoch='wrong',
    v=>v.approximate_room_pose.approximate_pose.registration.review_digest=null,v=>v.approximate_room_pose.approximate_pose.registration.source_fit_status='accepted',
    v=>v.approximate_room_pose.approximate_pose.registration.mount_movement_detection.result='changed',
    v=>v.approximate_room_pose.approximate_pose.registration.canonical_collision_digest='0'.repeat(64),
    v=>v.approximate_room_pose.label='Qualified room position',v=>v.approximate_room_pose.follow_allowed=true,
    v=>v.approximate_room_pose.lighting_authority=true,v=>v.approximate_room_pose.physical_permission=true,
    v=>v.approximate_room_pose.approximate_pose.joints[0].position_m[0]=NaN,v=>v.session_state='stopped']){
    const value=envelope();change(value);const out=project(value);
    assert.equal(out.approximateRoomPose.renderable,false);assert.equal(out.follow.allowed,false);assert.equal(out.roomPose.admitted,false);
  }
  assert.equal(project(envelope(),{displayedFrame:null}).approximateRoomPose.renderable,false);
  assert.equal(project(envelope(),{approximateReceipt:null}).approximateRoomPose.renderable,false);
});

test('selected-arm observer carries packet and charges actual live/graph request time',async()=>{
  let now=100;const timers=new Set(),session={id:scope.sessionId,site_id:'victoria',camera_id:'den',generation:scope.generation,state:'recording'};
  const frame={id:packet.frame.frame_id,media_id:packet.frame.media_id,site_id:'victoria',generation:scope.generation,width:640,height:360};
  const live={site_id:'victoria',session_id:session.id,session,server_time:1800000001,experiment_generation:1,
    arms:{B:{id:'B',frame_id:frame.id,frame,approximate_room_pose:copy(packet)}}};
  const graph={contract:'perception-lab-graph-1',site_id:'victoria',session_id:session.id,session,inference:'none',sources:[],arms:{}};
  const observer=Stream.createObserver({siteId:'victoria',sessionId:session.id,generation:scope.generation,scope,
    env:{performance:{now:()=>now},document:{hidden:false},setTimeout:(fn,ms)=>{const id=setTimeout(fn,ms);timers.add(id);return id;},
      clearTimeout:id=>{clearTimeout(id);timers.delete(id);}},transport:{async request(operation){
        if(operation==='sessions.live'){now=450;return live;}if(operation==='sessions.graph'){now=900;return graph;}throw Error('unexpectedread');}}});
  try{
    observer.start();await settle();assert.deepEqual(observer.snapshot().perception.approximate_room_pose,packet);
    assert.equal(observer.snapshot().perception.approximate_receipt_elapsed_s,.8);
    observer.setArm('A');assert.equal(observer.snapshot().perception.approximate_room_pose,null);
  }finally{observer.destroy();}
  assert.equal(timers.size,0);
});

test('player needs exact decoded image and cannot renew or mutate the same event receipt',async()=>{
  const f=controller();f.player.start();f.push(envelope());assert.equal(f.player.snapshot().view.approximateRoomPose.renderable,false);
  await settle();await settle();assert.equal(f.player.snapshot().view.approximateRoomPose.renderable,true);
  await f.advance(1000);f.push(envelope());await f.advance(800);
  assert.equal(f.player.snapshot().view.approximateRoomPose.renderable,false);
  f.push(envelope());assert.equal(f.player.snapshot().view.approximateRoomPose.renderable,false);
  f.player.destroy();assert.equal(f.timeouts.size,0);assert.equal(f.intervals.size,0);assert.equal(f.released,1);
  const changed=controller();changed.player.start();changed.push(envelope());await settle();await settle();
  const value=envelope();value.approximate_room_pose.approximate_pose.joints[0].position_m[0]+=.1;changed.push(value);
  assert.equal(changed.player.snapshot().view.approximateRoomPose.reason,'approximate_original_event_changed');
  changed.status({status:'stopped'});changed.push(envelope());assert.equal(changed.player.snapshot().view.approximateRoomPose.renderable,false);
  changed.player.destroy();
});

test('actual Three layer renders only approximate supplied joints and never changes Follow camera',()=>{
  const root=new THREE.Group(),camera=new THREE.PerspectiveCamera(35,1,.1,200),rig=createRig(camera);
  rig.fitBounds(new THREE.Box3(new THREE.Vector3(0,0,0),new THREE.Vector3(4,3,4)));rig.snapHome(0);
  const engine={apartmentRoot:root,camera,rig,renderer:{domElement:{}},siteId:scope.siteId,contentRevision:scope.contentRevision,
    sceneIdentity:copy(scope),picking:createPicking(camera,{clientWidth:800,clientHeight:800})};
  const layer=attachApartmentPerceptionLayer(engine,scope),before=rig.snapshotPose().target.toArray();layer.draw(project());
  assert.equal(layer.readback().renderableApproximate,true);assert.equal(layer.readback().jointCount,1);assert.equal(layer.readback().edgeCount,0);
  assert.equal(layer.readback().qualification,'approximate_visual_alignment');assert.equal(layer.readback().followAllowed,false);
  layer.setFollowing(true);assert.equal(layer.readback().following,false);assert.deepEqual(rig.snapshotPose().target.toArray(),before);
  const out=project();out.approximateRoomPose.renderable=false;layer.draw(out);assert.equal(layer.readback().jointCount,0);
  layer.dispose();assert.equal(root.children.length,0);
});
