import test from 'node:test';
import assert from 'node:assert/strict';
import {validateTrackingPacket,trackingPacketValidity,trackingGpuLatency,attachFastTracking} from './fast-tracking-layer.mjs';
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

test('a GPU-completed draw retains broad timing uncertainty when its raw interval crosses zero',async()=>{
  const priorWindow=globalThis.window,priorPerformance=Object.getOwnPropertyDescriptor(globalThis,'performance');
  let nowMs=0,streamController,layer;
  Object.defineProperty(globalThis,'performance',{configurable:true,value:{now:()=>nowMs}});
  globalThis.window={HomeContentPacks:{get:()=>({manifest:{assets:{'collision.glb':{sha256:scene.collisionDigest}}}})}};
  const engine={sceneIdentity:scene,siteId:'victoria',contentRevision:'pack',apartmentRoot:new THREE.Group(),
    renderer:{backend:{device:{queue:{onSubmittedWorkDone:async()=>{}}}}}};
  const stream=new ReadableStream({start(c){streamController=c;}});
  const fetchImpl=async url=>{
    if(url.endsWith('/clock')){nowMs=800;return new Response(JSON.stringify({attempt_id:'c'.repeat(32),monotonic_s:100.4}));}
    return new Response(stream,{headers:{'content-type':'text/event-stream'}});
  };
  const flush=()=>new Promise(resolve=>setImmediate(resolve));
  try{
    layer=attachFastTracking(engine,{fetchImpl,documentObject:{hidden:false}});await flush();
    const p=packet();p.people=[bodyPerson()];p.source_la_monotonic_interval_s=[100.6,100.65];p.expires_monotonic_s=101.35;
    nowMs=820;streamController.enqueue(new TextEncoder().encode('data: '+JSON.stringify(p)+'\n\n'));await flush();
    assert.ok(engine.apartmentRoot.children[0].children.length,'the original expiry check still admits this fresh packet');
    nowMs=850;engine.apartmentRoot.children[0].children[0].onAfterRender();await flush();
    const receipt=layer.readback().receipts.at(-1);
    assert.ok(receipt,'a real completed draw must not vanish merely because a valid uncertainty interval crosses zero');
    assert.equal(receipt.camera_to_gpu_timing,'causality_bounded');
    assert.ok(Math.abs(receipt.camera_to_gpu_raw_ms[0]+200)<1e-6);
    assert.ok(Math.abs(receipt.camera_to_gpu_raw_ms[1]-650)<1e-6);
    assert.equal(receipt.camera_to_gpu_ms[0],0);
    assert.ok(Math.abs(receipt.camera_to_gpu_ms[1]-650)<1e-6);
    assert.equal(receipt.expired_at_completion,false);
    assert.equal(layer.readback().diagnostics.counts.gpu_receipt_invalid_clock||0,0);
  }finally{
    layer?.dispose();streamController?.close();globalThis.window=priorWindow;
    Object.defineProperty(globalThis,'performance',priorPerformance);
  }
});

test('GPU timing preserves positive intervals and rejects impossible or malformed bounds',()=>{
  const good=trackingGpuLatency([10,10.01],{offsetLow:1,offsetHigh:1.02},11200);
  assert.equal(good.valid,true);assert.equal(good.qualification,'clock_bounded');
  assert.deepEqual(good.rawMs,good.boundsMs);
  assert.ok(Math.abs(good.boundsMs[0]-170)<1e-6);assert.ok(Math.abs(good.boundsMs[1]-200)<1e-6);
  for(const [source,sample,completed,reason] of [
    [[10,10.01],{offsetLow:1,offsetHigh:1.02},10900,'wholly_negative_bounds'],
    [[10.01,10],{offsetLow:1,offsetHigh:1.02},11200,'inverted_bounds'],
    [[10,10.01],{offsetLow:1.02,offsetHigh:1},11200,'inverted_bounds'],
    [[10,Infinity],{offsetLow:1,offsetHigh:1.02},11200,'nonfinite_bounds'],
    [[10,10.01],{offsetLow:NaN,offsetHigh:1.02},11200,'nonfinite_bounds'],
    [[10,10.01],{offsetLow:1,offsetHigh:1.02},Infinity,'nonfinite_bounds']]){
    assert.deepEqual(trackingGpuLatency(source,sample,completed),{valid:false,reason});
  }
});

test('a coalesced network chunk preserves separately bounded tracking events',async()=>{
  const priorWindow=globalThis.window;let layer,streamController;
  globalThis.window={HomeContentPacks:{get:()=>({manifest:{assets:{'collision.glb':{sha256:scene.collisionDigest}}}})}};
  const engine={sceneIdentity:scene,siteId:'victoria',contentRevision:'pack',apartmentRoot:new THREE.Group(),renderer:{}};
  const stream=new ReadableStream({start(c){streamController=c;}});
  const fetchImpl=async url=>url.endsWith('/clock')?
    new Response(JSON.stringify({attempt_id:'c'.repeat(32),monotonic_s:performance.now()/1000})):
    new Response(stream,{headers:{'content-type':'text/event-stream'}});
  const flush=()=>new Promise(resolve=>setImmediate(resolve));
  try{
    layer=attachFastTracking(engine,{fetchImpl,documentObject:{hidden:false}});await flush();
    const now=performance.now()/1000,events=[];
    for(let n=1;n<=200;n++){
      const p=packet();p.sequence=n;p.frame_key=n.toString(16).padStart(64,'0');p.identity.sequence=n;
      p.source_la_monotonic_interval_s=[now-.02,now-.01];p.expires_monotonic_s=now+.73;
      const event='data: '+JSON.stringify(p)+'\n\n';assert.ok(event.length<65536);events.push(event);
    }
    const chunk=new TextEncoder().encode(events.join(''));assert.ok(chunk.length>65536);
    streamController.enqueue(chunk);await flush();
    assert.equal(layer.readback().diagnostics.counts.browser_received,200,
      'a transport chunk may contain many valid events and must not discard all of them');
    assert.equal(layer.readback().diagnostics.counts.stream_failed||0,0);
    assert.equal(layer.readback().sequence,200);
  }finally{layer?.dispose();streamController?.close();globalThis.window=priorWindow;}
});

test('a maximum-sized event survives a delimiter split between network chunks',async()=>{
  const priorWindow=globalThis.window;let layer,streamController;
  globalThis.window={HomeContentPacks:{get:()=>({manifest:{assets:{'collision.glb':{sha256:scene.collisionDigest}}}})}};
  const engine={sceneIdentity:scene,siteId:'victoria',contentRevision:'pack',apartmentRoot:new THREE.Group(),renderer:{}};
  const stream=new ReadableStream({start(c){streamController=c;}});
  const fetchImpl=async url=>url.endsWith('/clock')?
    new Response(JSON.stringify({attempt_id:'c'.repeat(32),monotonic_s:performance.now()/1000})):
    new Response(stream,{headers:{'content-type':'text/event-stream'}});
  const flush=()=>new Promise(resolve=>setImmediate(resolve));
  try{
    layer=attachFastTracking(engine,{fetchImpl,documentObject:{hidden:false}});await flush();
    const p=packet(),now=performance.now()/1000;
    p.source_la_monotonic_interval_s=[now-.02,now-.01];p.expires_monotonic_s=now+.73;p.diagnostic_fixture='';
    p.diagnostic_fixture='x'.repeat(65536-('data: '+JSON.stringify(p)).length);
    const event='data: '+JSON.stringify(p);assert.equal(event.length,65536);
    streamController.enqueue(new TextEncoder().encode(event+'\n'));await flush();
    assert.equal(layer.readback().diagnostics.counts.stream_failed||0,0);
    assert.equal(layer.readback().diagnostics.counts.browser_received||0,0);
    streamController.enqueue(new TextEncoder().encode('\n'));await flush();
    assert.equal(layer.readback().diagnostics.counts.browser_received,1);
    assert.equal(layer.readback().sequence,1);
  }finally{layer?.dispose();streamController?.close();globalThis.window=priorWindow;}
});

for(const [name,input,reason] of [
  ['one oversized complete tracking event','data: '+JSON.stringify({...packet(),diagnostic_fixture:'x'.repeat(65536)})+'\n\n','tracking_stream_bound'],
  ['one oversized unfinished tracking event','data: '+JSON.stringify({...packet(),diagnostic_fixture:'x'.repeat(65536)}),'tracking_stream_bound'],
  ['malformed JSON with private-looking contents','data: {"diagnostic_fixture":"private-sentinel" broken}\n\n','tracking_event_invalid_json']
])test(name+' remains refused with a bounded diagnostic reason',async()=>{
  const priorWindow=globalThis.window;let layer,streamController;
  globalThis.window={HomeContentPacks:{get:()=>({manifest:{assets:{'collision.glb':{sha256:scene.collisionDigest}}}})}};
  const engine={sceneIdentity:scene,siteId:'victoria',contentRevision:'pack',apartmentRoot:new THREE.Group(),renderer:{}};
  const stream=new ReadableStream({start(c){streamController=c;}});
  const fetchImpl=async url=>url.endsWith('/clock')?
    new Response(JSON.stringify({attempt_id:'c'.repeat(32),monotonic_s:performance.now()/1000})):
    new Response(stream,{headers:{'content-type':'text/event-stream'}});
  const flush=()=>new Promise(resolve=>setImmediate(resolve));
  try{
    layer=attachFastTracking(engine,{fetchImpl,documentObject:{hidden:false}});await flush();
    streamController.enqueue(new TextEncoder().encode(input));await flush();
    assert.equal(layer.readback().diagnostics.counts.browser_received||0,0);
    assert.equal(layer.readback().diagnostics.events.find(e=>e.stage==='stream_failed')?.reason,reason);
    assert.doesNotMatch(JSON.stringify(layer.readback().diagnostics),/private-sentinel|diagnostic_fixture/);
    assert.equal(engine.apartmentRoot.children[0].children.length,0);
  }finally{layer?.dispose();streamController?.close();globalThis.window=priorWindow;}
});
