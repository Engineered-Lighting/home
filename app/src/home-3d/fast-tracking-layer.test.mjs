import test from 'node:test';
import assert from 'node:assert/strict';
import {validateTrackingPacket,attachFastTracking} from './fast-tracking-layer.mjs';
import * as THREE from 'three';
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
  const stream=new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode('data: '+JSON.stringify(data)+'\n\n'));}});
  const fetchImpl=async url=>url.endsWith('/clock')?new Response(JSON.stringify({attempt_id:data.attempt_id,monotonic_s:performance.now()/1000}),{status:200}):
      new Response(stream,{status:200,headers:{'content-type':'text/event-stream'}});
  let layer;
  try{
    layer=attachFastTracking(engine,{fetchImpl,documentObject:{hidden:false}});
    for(let n=0;n<20&&!engine.apartmentRoot.children[0]?.children.length;n++)await new Promise(r=>setTimeout(r,1));
    const root=engine.apartmentRoot.children[0];assert.equal(root.children.length,2);
    assert.equal(layer.readback().receipts.length,0);
    root.children[0].onAfterRender();await new Promise(r=>setTimeout(r,1));
    assert.equal(layer.readback().receipts.length,0);
    finishGpu();await new Promise(r=>setTimeout(r,1));
    assert.equal(layer.readback().receipts.length,1);
    assert.equal(layer.readback().receipts[0].frame_key,data.frame_key);
    assert.equal(layer.readback().receipts[0].joints,2);
    layer.dispose();assert.equal(engine.apartmentRoot.children.length,0);assert.equal(engine.fastTrackingReadback,undefined);
  }finally{layer?.dispose();globalThis.window=previousWindow;}
});
