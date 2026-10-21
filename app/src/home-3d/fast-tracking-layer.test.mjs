import test from 'node:test';
import assert from 'node:assert/strict';
import {validateTrackingPacket,trackingPacketValidity,attachFastTracking} from './fast-tracking-layer.mjs';
import * as THREE from 'three';
import {bodyPerson} from '../../../tools/fixtures/body-form-fixtures.mjs';
const scene={siteId:'victoria',contentRevision:'pack',frameDigest:'a'.repeat(64),registrationVersion:1,collisionDigest:'b'.repeat(64),cameraId:'den'};
const packet=()=>({schema:'home-fast-tracking-1',attempt_id:'c'.repeat(32),sequence:1,status:'available',frame_key:'d'.repeat(64),
  identity:{epoch:'capture'},scene,lighting_authority:false,physical_permission:false,identity_authority:false,
  label:'Approximate room placement',projection:'unqualified_nominal_eeprom',source_la_monotonic_interval_s:[10,10.01],
  expires_monotonic_s:10.75,people:[{instance_id:'local',joints:[{id:5,position_m:[1,2,3],visible:true},{id:6,position_m:[2,2,3],visible:true}]}]});
const clock={attempt:'c'.repeat(32),offsetLow:1,offsetHigh:1.02,checkedAtMs:11200};
test('independent joints need no video image but retain scene, clock, authority and expiry checks',()=>{
  assert.equal(validateTrackingPacket(packet(),scene,{clock,nowMs:11200}),true);
  for(const mutate of [p=>p.physical_permission=true,p=>p.scene={...scene,contentRevision:'other'},
    p=>p.expires_monotonic_s=11,p=>p.people[0].joints.push(p.people[0].joints[0]),p=>p.people[0].joints[0].position_m[0]=Infinity]){
    const p=packet();mutate(p);assert.equal(validateTrackingPacket(p,scene,{clock,nowMs:11200}),false);
  }
  assert.equal(validateTrackingPacket(packet(),scene,{clock,nowMs:11800}),false);
  assert.equal(validateTrackingPacket(packet(),scene,{clock,nowMs:11200,attempt:'c'.repeat(32),sequence:1}),false);
  assert.equal(validateTrackingPacket(packet(),scene,{clock:{...clock,attempt:'other'},nowMs:11200}),false);
});

test('receipt waits for an actual draw AND GPU completion; disconnect clears owned geometry',async()=>{
  let finishGpu;const fence=new Promise(resolve=>finishGpu=resolve);
  const previousWindow=globalThis.window;globalThis.window={HomeContentPacks:{get:()=>({manifest:{assets:{'collision.glb':{sha256:scene.collisionDigest}}}})}};
  const renderer={backend:{device:{queue:{onSubmittedWorkDone:()=>fence}}}};
  const engine={sceneIdentity:scene,siteId:'victoria',contentRevision:'pack',apartmentRoot:new THREE.Group(),renderer};
  const elapsed=performance.now()/1000;
  let sent=false;
  const data=packet();data.source_la_monotonic_interval_s=[elapsed-.02,elapsed-.01];data.expires_monotonic_s=elapsed+.73;
  data.people=[bodyPerson()];
  const stream=new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode('data: '+JSON.stringify(data)+'\n\n'));}});
  const fetchImpl=async url=>url.endsWith('/clock')?new Response(JSON.stringify({attempt_id:data.attempt_id,monotonic_s:performance.now()/1000}),{status:200}):
      new Response(stream,{status:200,headers:{'content-type':'text/event-stream'}});
  let layer;
  try{
    layer=attachFastTracking(engine,{fetchImpl,documentObject:{hidden:false}});
    for(let n=0;n<20&&!engine.apartmentRoot.children[0]?.children.length;n++)await new Promise(r=>setTimeout(r,1));
    const root=engine.apartmentRoot.children[0];assert.ok(root.children.filter(mesh=>mesh.visible).length>0,'the body form has real draw geometry');
    assert.equal(layer.readback().receipts.length,0);
    root.children[0].onAfterRender();await new Promise(r=>setTimeout(r,1));
    assert.equal(layer.readback().receipts.length,0);
    finishGpu();await new Promise(r=>setTimeout(r,1));
    assert.equal(layer.readback().receipts.length,1);
    assert.equal(layer.readback().receipts[0].frame_key,data.frame_key);
    assert.equal(layer.readback().receipts[0].joints,17);
    assert.equal(layer.readback().receipts[0].bodyForm.status,'complete');
    assert.deepEqual(layer.readback().diagnostics.events.filter(e=>e.sequence===1).map(e=>e.stage),
      ['browser_received','scene_applied','draw_submitted','gpu_completed']);
    assert.doesNotMatch(JSON.stringify(layer.readback().diagnostics),/position_m|camera_xyz/);
    layer.dispose();assert.equal(engine.apartmentRoot.children.length,0);assert.equal(engine.fastTrackingReadback,undefined);
  }finally{layer?.dispose();globalThis.window=previousWindow;}
});

test('a transient clock refresh failure does not erase a still-valid clock or fresh body',async()=>{
  const priorWindow=globalThis.window,priorInterval=globalThis.setInterval,priorClear=globalThis.clearInterval;
  const timers=new Map();let next=0,refreshes=0,streamController,layer;
  globalThis.setInterval=(fn,ms)=>{const id=++next;timers.set(id,{fn,ms});return id;};
  globalThis.clearInterval=id=>timers.delete(id);
  globalThis.window={HomeContentPacks:{get:()=>({manifest:{assets:{'collision.glb':{sha256:scene.collisionDigest}}}})}};
  const engine={sceneIdentity:scene,siteId:'victoria',contentRevision:'pack',apartmentRoot:new THREE.Group(),renderer:{backend:{device:{queue:{onSubmittedWorkDone:async()=>{}}}}}};
  const stream=new ReadableStream({start(c){streamController=c;}});
  const fetchImpl=async url=>url.endsWith('/clock')?(++refreshes===1?
    new Response(JSON.stringify({attempt_id:'c'.repeat(32),monotonic_s:performance.now()/1000}),{status:200}):new Response('{}',{status:503})):
    new Response(stream,{headers:{'content-type':'text/event-stream'}});
  const emit=n=>{const p=packet(),now=performance.now()/1000;p.sequence=n;p.source_la_monotonic_interval_s=[now-.02,now-.01];p.expires_monotonic_s=now+.73;
    p.people=[bodyPerson()];
    streamController.enqueue(new TextEncoder().encode('data: '+JSON.stringify(p)+'\n\n'));};
  const flush=()=>new Promise(resolve=>setImmediate(resolve));
  try{
    layer=attachFastTracking(engine,{fetchImpl,documentObject:{hidden:false}});await flush();emit(1);await flush();
    assert.ok(engine.apartmentRoot.children[0].children.some(mesh=>mesh.visible));
    [...timers.values()].find(t=>t.ms===5000).fn();await flush();emit(2);await flush();
    assert.ok(engine.apartmentRoot.children[0].children.some(mesh=>mesh.visible),'one refresh failure must not create a five-second blank');
    engine.apartmentRoot.children[0].children[0].onAfterRender();await flush();
    assert.equal(layer.readback().receipts.at(-1)?.sequence,2);
    assert.equal(layer.readback().diagnostics.events.find(e=>e.stage==='clock_refresh_failed').retained_valid_clock,true);
  }finally{layer?.dispose();streamController?.close();globalThis.window=priorWindow;globalThis.setInterval=priorInterval;globalThis.clearInterval=priorClear;}
});

test('diagnostic reasons preserve clock and original evidence expiry limits',()=>{
  const fresh=packet();fresh.source_la_monotonic_interval_s=[25.1,25.11];fresh.expires_monotonic_s=25.85;
  assert.equal(trackingPacketValidity(fresh,scene,{clock,nowMs:26201}).reason,'clock_expired');
  assert.equal(trackingPacketValidity(packet(),scene,{clock,nowMs:11800}).reason,'evidence_expired');
  assert.equal(trackingPacketValidity(packet(),scene,{clock:null,nowMs:11200}).reason,'clock_missing');
});
