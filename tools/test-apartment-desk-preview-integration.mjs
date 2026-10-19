/* Actual authored backend RAM/ASGI -> production transport/media/Stream/Source/
 * Player/View -> preview controller -> existing solver and unchanged Three.
 * Image decode is injected; no browser pixels, live accuracy or hardware claim. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import { webcrypto } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import * as THREE from 'three';
import { createEquipmentVirtualFixtures } from '../app/src/home-3d/equipment-virtual-fixtures.mjs';

const require = createRequire(import.meta.url), root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const Core = require('../app/src/home-equipment-registration.js');
const Aiming = require('../app/src/home-apartment-aiming.js');
const copy = value => structuredClone(value);
const binding = {content_revision:'authored-revision',registration_version:1,manifest_digest:'3'.repeat(64),
  frame_digest:'1'.repeat(64),canonical_collision_digest:'2'.repeat(64)};

function equipment() {
  const references={frame:'z_up_metric_floor0',units:'m',confirmed:true,provenance:{kind:'authored_test'},
    floor:{id:'floor',normal:[0,0,1],offset_m:0},walls:[
      {id:'wall-x0',label:'Authored x0',normal_xy:[1,0],offset_m:0},
      {id:'wall-y0',label:'Authored y0',normal_xy:[0,1],offset_m:0},
      {id:'wall-x4',label:'Authored x4',normal_xy:[-1,0],offset_m:-4},
      {id:'wall-y5',label:'Authored y5',normal_xy:[0,-1],offset_m:-5}]};
  let model=Core.toModel(Core.createDocument(binding,references));model=Core.addSocket(Core.addSocket(model));
  for(let i=1;i<=4;i++)model=Core.updateModelEquipment(model,'victoria-den-mount-0'+i,{physical_kind:'socket',mount_type:'ceiling',
    measurements:{wall_distances:[{wall_id:'wall-x0',distance_m:i<3?i:3},{wall_id:'wall-y0',distance_m:i<3?1:i}],
      height_above_floor_m:2.4,method:'laser',uncertainty_m:.005}});
  return Core.sealDocument(Core.toDocument(model));
}
const documentFixture=equipment();
const retained={};
function exported(name){
  if(!retained[name])retained[name]=JSON.parse(execFileSync('python',['-I','-B',path.join(root,'tools/fixtures',name)],
    {cwd:root,input:JSON.stringify({equipment_ref:{revision:documentFixture.revision,digest:documentFixture.digest}}),encoding:'utf8',timeout:30000,maxBuffer:8*1024*1024}));
  return copy(retained[name]);
}
const fixture=()=>exported('generate-apartment-desk-preview.py');
// The backend's own development export: private policy timing 'development',
// read by Home one second after the PNG's original deadline.
const developmentExport=()=>exported('generate-apartment-desk-preview-development.py');

function harness(data,{delayDecode=false,armed=null,locks=null,wallOffsetS=0,live=null,otherMedia='fail'}={}){
  const environment=new EventTarget(),document=new EventTarget(),timeouts=new Map(),intervals=new Map(),reads=[];
  let sequence=0,mono=1000,wall=(data.now+wallOffsetS)*1000,stopped=false,decodeRelease=null,released=0,liveBody=live;
  document.hidden=false;
  Object.assign(environment,{window:environment,document,AbortController,DOMException,Headers,Response,TextEncoder,TextDecoder,Uint8Array,URL,
    crypto:webcrypto,performance:{now:()=>mono},Date:{now:()=>wall},
    setTimeout(fn,ms){const id=++sequence;timeouts.set(id,{fn,ms,at:mono+ms});return id;},clearTimeout:id=>timeouts.delete(id),
    setInterval(fn,ms){const id=++sequence;intervals.set(id,{fn,ms,at:mono+ms});return id;},clearInterval:id=>intervals.delete(id),
    HomeSpatialViewport:{getSelectedSiteId:()=>'victoria',subscribe:()=>()=>{}},...(locks?{navigator:{locks}}:{})});
  environment.fetch=async(input,options={})=>{
    const url=new URL(String(input),'https://home.invalid'),base='/api/perception-lab/v1/sessions/'+data.session.id;
    assert.equal(options.method||'GET','GET');assert.equal(options.credentials,'same-origin');
    assert.equal(new Headers(options.headers).get('X-Home-Lab-Site'),'victoria');reads.push(url.pathname);
    if(url.pathname===base+'/live')return Response.json(copy(stopped?data.stopped:liveBody||data.live));
    if(url.pathname===base+'/graph')return Response.json(copy(data.graph));
    const media=base+'/media/'+data.descriptor.id;
    if(url.pathname===media+'/descriptor')return Response.json(copy(data.descriptor));
    // A newer frame's image: its read fails, or stays pending until abandoned.
    if(url.pathname.startsWith(base+'/media/')&&url.pathname!==media){
      if(otherMedia==='hang')return new Promise((_,reject)=>options.signal?.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true}));
      throw Error('authored_newer_frame_read_failed');
    }
    if(url.pathname===media){
      const bytes=Buffer.from(data.png_hex,'hex'),range=/^bytes=(\d+)-(\d+)$/.exec(new Headers(options.headers).get('Range'));
      assert.ok(range,'production media path must request a bounded range');
      const start=+range[1],end=Math.min(+range[2],bytes.length-1),body=bytes.subarray(start,end+1);
      return new Response(body,{status:206,headers:{'Content-Type':'image/png','Content-Length':String(body.length),
        'Content-Range':`bytes ${start}-${end}/${bytes.length}`,ETag:'"'+data.descriptor.sha256+'"'}});
    }
    throw Error('unexpected_read:'+url.pathname);
  };
  const context=vm.createContext(environment);
  for(const name of ['home-perception-lab-transport.js','home-perception-lab-media.js','home-den-lab-stream.js',
    'home-apartment-perception-source.js','home-apartment-perception-view.js','home-apartment-desk-preview.js',
    'home-apartment-perception-player.js','home-apartment-desk-aim-preview.js'])
    vm.runInContext(fs.readFileSync(path.join(root,'app/src',name),'utf8'),context,{filename:name});
  const parent=new THREE.Group(),fixtures=createEquipmentVirtualFixtures(parent,{binding});fixtures.setDocument(documentFixture);
  assert.equal(fixtures.readback().fixture_count,4);
  const scope={siteId:'victoria',contentRevision:binding.content_revision,frameDigest:binding.frame_digest,
    registrationVersion:binding.registration_version,cameraId:'den',sessionId:data.session.id,generation:data.session.generation};
  const previewContext={active:true,identity:copy(scope),hidden:false,committedOwnerActive:false};
  const overlay={virtualFixturesReadback:()=>fixtures.readback(),virtualFixtureInteractionDescriptor:id=>fixtures.interactionDescriptor(id),
    aimVirtualFixtureAtTarget:(...args)=>fixtures.aimAtTarget(...args),restoreVirtualFixtureAim:(...args)=>fixtures.restoreAim(...args)};
  const notices=[];
  const controller=environment.HomeApartmentDeskAimPreview.create({overlay,solveAim:Aiming.solveAim,getContext:()=>previewContext,
    nowMs:()=>mono,setTimeout:environment.setTimeout,clearTimeout:environment.clearTimeout,onChange:value=>notices.push(value)});
  // The Den JSX passes its arm getter through the same Source subscription.
  const Source=environment.HomeApartmentPerceptionSource,source=armed?{subscribe:args=>Source.subscribe({...args,previewArmed:armed}),
    sampleClock:()=>Source.sampleClock(),readFrame:(frame,options)=>Source.readFrame(frame,options)}:Source;
  const player=environment.HomeApartmentPerceptionPlayer.create({source,scope,env:environment,
    collisionDigest:binding.canonical_collision_digest,onChange:state=>controller.update(state.view?.virtualTargetPreview,state.view?.deskPreviewEvidence),
    decode:async(image)=>{
      assert.equal(Buffer.from(image.bytes).subarray(0,8).toString('hex'),'89504e470d0a1a0a');
      if(delayDecode)await new Promise(resolve=>{decodeRelease=resolve;});
      return {url:'blob:authored-preview',width:640,height:360,dispose(){released++;}};
    }});
  const baseline=fixtures.readback(),origins=fixtures.root.children.map(n=>n.position.toArray());
  async function until(predicate){const end=Date.now()+3000;while(Date.now()<end){if(predicate())return;await new Promise(r=>setTimeout(r,1));}
    const state=player.snapshot();assert.fail('bounded fixture settlement: '+JSON.stringify({frame:state.frame,reason:state.reason,
      loading:state.loading,approx:state.view?.approximateRoomPose?.reason,preview:state.view?.virtualTargetPreview,controller:controller.readback()}));}
  async function poll(ms=1000){const entry=[...timeouts].find(([,v])=>v.ms===ms);assert.ok(entry,'one incumbent observer poll');
    timeouts.delete(entry[0]);entry[1].fn();await new Promise(r=>setTimeout(r,0));}
  const quiet=async()=>{for(let i=0;i<3;i++)await new Promise(r=>setTimeout(r,5));};
  const nextPoll=()=>[...timeouts.values()].some(v=>v.ms===250||v.ms===1000);
  function channels(){return fixtures.readback().fixtures.map(f=>({fixture_id:f.fixture_id,radials:f.state.radials,downward:f.state.downward,spotlight:f.state.spotlight}));}
  return {player,controller,fixtures,previewContext,baseline,origins,reads,channels,until,poll,timeouts,notices,setLive:value=>{liveBody=value;},
    // Advance Home monotonic and wall time together, firing due timers in order.
    async advance(ms,step=250){for(let done=0;done<ms;done+=step){mono+=step;wall+=step;
      for(const[id,t]of[...timeouts].sort((a,b)=>a[1].at-b[1].at))if(t.at<=mono&&timeouts.delete(id))t.fn();
      for(const t of intervals.values())if(t.at<=mono){t.at=mono+t.ms;t.fn();}
      await quiet();}},
    async armedPoll(){mono+=250;wall+=250;await poll(250);await until(nextPoll);},
    async start(){controller.setEnabled(true);player.start();await until(()=>delayDecode?!!decodeRelease:player.snapshot().view?.virtualTargetPreview?.available);},
    async expire(settled=()=>!controller.readback().active&&!player.snapshot().imageUrl){
      const ms=Math.max(1,(data.live.arms.B.virtual_target_preview.valid_until*1000-wall)+1);mono+=ms;wall+=ms;
      for(const[id,t]of[...timeouts])if(t.at<=mono){timeouts.delete(id);t.fn();}
      for(const t of intervals.values())if(t.at<=mono){t.at=mono+t.ms;t.fn();}
      await until(settled);},
    async stop(){stopped=true;await poll();await until(()=>!player.snapshot().imageUrl&&
      (delayDecode?player.snapshot().view?.virtualTargetPreview?.available===false:!player.snapshot().loading));},
    async finishDecode(){decodeRelease?.();await until(()=>!player.snapshot().loading&&released>=1);},
    // Abandon pending reads first so their abort cleanup settles before close().
    async drain(){player.stop();await quiet();},
    close(){player.destroy();controller.dispose();fixtures.dispose();assert.equal(timeouts.size,0);assert.equal(intervals.size,0);assert.equal(parent.children.length,0);},
    get released(){return released;}};
}

test('actual backend optional preview reaches existing solver and unchanged Three direction; expiry and Stop restore owned aim',async()=>{
  const data=fixture();assert.equal(data.schema,'home-authored-desk-preview-integration-1');
  assert.equal(data.npz_reads,1);assert.equal(data.durable_media_rows,0);assert.ok(data.resident_bytes_before_stop<=8388608);
  assert.equal(data.cleanup.resident_bytes,0);assert.equal(data.cleanup.resident_frames,0);
  for(const k of ['host_operations','real_model_calls','camera_calls','native_launches'])assert.equal(data[k],0);
  const h=harness(data),channels=h.channels();
  try{
    await h.start();const state=h.player.snapshot(),v=state.view.virtualTargetPreview;
    assert.ok(state.imageUrl);assert.equal(v.status,'preview');assert.equal(v.qualification,null);
    for(const k of ['physical_permission','lighting_authority','geometry_authority','identity_authority','engagement_authority'])assert.equal(v[k],false,k);
    assert.equal(state.view.follow.allowed,false);assert.equal(state.view.roomPose.admitted,false);
    const applied=h.fixtures.readback().fixtures.filter(f=>h.fixtures.interactionDescriptor(f.fixture_id).target);
    assert.equal(applied.length,1);const descriptor=h.fixtures.interactionDescriptor(applied[0].fixture_id);
    assert.deepEqual(descriptor.target.point,copy(v.target.point_m));
    const solution=Aiming.solveAim(descriptor.display_origin_m,v.target.point_m,{horizontal_world_reference:descriptor.horizontal_world_reference});
    assert.ok(solution.ok&&solution.executable);
    assert.ok(Math.abs(descriptor.state.pan_deg-solution.pan_deg)<1e-9);assert.ok(Math.abs(descriptor.state.tilt_deg-solution.tilt_deg)<1e-9);
    const beam=h.fixtures.root.children.find(n=>n.userData.fixture_id===descriptor.fixture_id).getObjectByName('modeled-aim-volume');
    const actual=new THREE.Vector3(...beam.userData.direction_unit),expected=new THREE.Vector3(...v.target.point_m).sub(new THREE.Vector3(...descriptor.display_origin_m)).normalize();
    assert.ok(actual.distanceTo(expected)<1e-9);assert.deepEqual(h.channels(),channels);
    assert.deepEqual(h.fixtures.root.children.map(n=>n.position.toArray()),h.origins);
    await h.expire();assert.equal(h.player.snapshot().view.virtualTargetPreview.available,false);
    assert.deepEqual(h.fixtures.readback().fixtures,h.baseline.fixtures);
    await h.stop();assert.deepEqual(h.fixtures.readback().fixtures,h.baseline.fixtures);
    assert.ok(h.reads.every(p=>p.startsWith('/api/perception-lab/v1/sessions/')));
  }finally{h.close();}
});

test('manual and committed ownership prevent preview reapplication without changing channels or inventory',async()=>{
  const h=harness(fixture());
  try{
    await h.start();h.previewContext.committedOwnerActive=true;
    h.controller.update(h.player.snapshot().view.virtualTargetPreview);
    assert.deepEqual(h.fixtures.readback().fixtures,h.baseline.fixtures,'committed ownership withdraws an active preview');
  }finally{h.close();}
  const manualHarness=harness(fixture());
  try{
    await manualHarness.start();manualHarness.controller.manualTakeover('authored_manual_takeover');
    const h=manualHarness;
    const id=h.fixtures.readback().fixtures[0].fixture_id;
    h.fixtures.setFixtureState(id,{pan_deg:17,tilt_deg:23});const manual=h.fixtures.readback();
    h.controller.update(h.player.snapshot().view.virtualTargetPreview);
    assert.deepEqual(h.fixtures.readback(),manual,'same queued preview cannot overwrite manual state');
    h.previewContext.committedOwnerActive=true;h.controller.setEnabled(true);h.controller.update(h.player.snapshot().view.virtualTargetPreview);
    assert.deepEqual(h.fixtures.readback(),manual,'qualified committed owner takes precedence');
  }finally{manualHarness.close();}
});

test('an actual stopped source restores an active preview and disposes its decoded image',async()=>{
  const h=harness(fixture());
  try{
    await h.start();assert.equal(h.controller.readback().active,true);
    await h.stop();assert.equal(h.player.snapshot().view.virtualTargetPreview.available,false);
    assert.deepEqual(h.fixtures.readback().fixtures,h.baseline.fixtures);assert.ok(h.released>=1);
  }finally{h.close();}
});

test('Stop before injected decode settles cannot adopt late image or apply a virtual target',async()=>{
  const h=harness(fixture(),{delayDecode:true});
  try{
    await h.start();await h.stop();await h.finishDecode();
    assert.equal(h.player.snapshot().imageUrl,null);assert.ok(h.released>=1,'late decoded image is disposed');
    assert.deepEqual(h.fixtures.readback().fixtures,h.baseline.fixtures);
  }finally{h.close();}
});

// Development timing: every case runs on the backend's own development export
// (private policy 'development', read 1 s after the PNG's original deadline).
// A newer arm-B output for a new frame whose image Home has not read.
function newerOutput(data,{people=0,preview=null}={}){
  const live=copy(data.live),B=live.arms.B,frame='authored-newer-frame',media='view_'+'e'.repeat(32),o=B.observation;
  B.frame_id=frame;B.frame={...B.frame,id:frame,media_id:media};
  B.observation={...o,frame_id:frame,media_ids:[media],request_id:'authored-newer-request',
    observed_at:o.observed_at+.5,available_at:o.available_at+.5,valid_until:o.valid_until+5};
  B.boxes=people?B.boxes.slice(0,1):[];B.layers={...B.layers,boxes:{available:true}};
  delete B.approximate_room_pose;delete B.virtual_target_preview;
  if(preview)B.virtual_target_preview=preview;
  return live;
}
const refused=(reason,evidence,eventId)=>({schema:'home-apartment-virtual-target-preview-1',version:1,status:'unavailable',available:false,reason,
  label:'Person visible · configured desk preview',virtual_preview_only:true,qualification:null,scope:null,frame:null,observation:null,
  provenance:null,source_times:null,timing:{policy:'development',warnings:[]},valid_until:null,target:null,person_point:null,
  ...Object.fromEntries(['operational_geometry','geometry_authority','identity_authority','engagement_authority','absence_authority',
    'protection_authority','beam_authority','physical_permission','follow_allowed','lighting_authority','physical_commands'].map(k=>[k,false])),
  evidence,evidence_event_id:eventId});
function fakeLocks(){const held=new Set();
  return {held,request(name,options,callback){if(held.has(name))return Promise.resolve().then(()=>callback(null));held.add(name);
    return Promise.resolve().then(()=>callback({name,mode:'exclusive'})).finally(()=>held.delete(name));}};}
const appliedDescriptor=h=>{const applied=h.fixtures.readback().fixtures.filter(f=>h.fixtures.interactionDescriptor(f.fixture_id).target);
  assert.equal(applied.length,1);return h.fixtures.interactionDescriptor(applied[0].fixture_id);};

test('backend development export past its deadline reaches the solver with its warnings; retained descriptor allows the PNG read',async()=>{
  const data=developmentExport(),B=data.live.arms.B,p=B.virtual_target_preview;
  assert.equal(data.development.timing_policy,'development');assert.equal(data.development.served_after_original_deadline_s,1);
  assert.ok(data.now>=data.descriptor.metadata.valid_until,'Home reads after the original media deadline');
  assert.equal(data.descriptor.metadata.valid_until,data.descriptor.retention.original_valid_until,'metadata keeps the original deadline');
  assert.equal(p.timing.policy,'development');assert.equal(B.approximate_room_pose.timing.policy,'development');
  assert.equal(data.live.apartment_viewing.metric.timing_policy,'development');
  assert.deepEqual(Object.keys(data.live.apartment_viewing.metric.registered_frame).sort(),['event_id','frame_id','generation','media_id','media_sha256']);
  const h=harness(data);
  try{
    await h.start();const state=h.player.snapshot(),v=state.view.virtualTargetPreview;
    assert.ok(state.imageUrl);assert.equal(v.status,'preview');assert.equal(v.timingPolicy,'development');
    assert.deepEqual([...v.warnings],p.timing.warnings.map(w=>w.check),'the backend media_expired warning is reused');
    assert.ok(v.warnings.includes('media_expired'));assert.ok(!v.warnings.includes('home_original_deadline_passed'));
    assert.equal(v.evidence,'supporting');assert.equal(v.evidenceEventId,p.evidence_event_id);
    assert.deepEqual([...state.mediaWarnings],['media_original_expired']);
    for(const k of ['physical_permission','lighting_authority','geometry_authority','identity_authority','engagement_authority'])assert.equal(v[k],false,k);
    assert.equal(state.view.follow.allowed,false);assert.equal(state.view.roomPose.admitted,false);
    const r=h.controller.readback();assert.equal(r.active,true);assert.equal(r.holding,true);assert.equal(r.policy,'development');
    assert.deepEqual({...r.warningCounts},Object.fromEntries(v.warnings.map(w=>[w,1])));
    const descriptor=appliedDescriptor(h),solution=Aiming.solveAim(descriptor.display_origin_m,v.target.point_m,{horizontal_world_reference:descriptor.horizontal_world_reference});
    assert.ok(solution.ok&&solution.executable);assert.ok(Math.abs(descriptor.state.pan_deg-solution.pan_deg)<1e-9);
    assert.ok(h.reads.some(p=>p.endsWith('/descriptor')));assert.deepEqual(h.fixtures.root.children.map(n=>n.position.toArray()),h.origins);
  }finally{h.close();}
  const strict=harness(fixture(),{wallOffsetS:1.2});
  try{
    strict.controller.setEnabled(true);strict.player.start();await strict.until(()=>strict.player.snapshot().mediaReason==='frame_expired');
    assert.equal(strict.player.snapshot().imageUrl,null);assert.equal(strict.controller.readback().active,false);
    assert.ok(!strict.reads.some(p=>p.includes('/media/')),'strict never reads an expired original');
  }finally{strict.close();}
});

test('development hold persists through expire(), withdraws at 10 s, Stop restores',async()=>{
  const h=harness(developmentExport());
  try{
    await h.start();assert.equal(h.controller.readback().holding,true);
    await h.expire(()=>true);
    assert.equal(h.controller.readback().active,true,'the original deadline is a warning, not a withdrawal');
    assert.ok(h.player.snapshot().imageUrl,'the retained development PNG stays displayed');
    await h.advance(3000);
    let r=h.controller.readback();assert.equal(r.active,true);assert.equal(r.lastSeenLabel,'last seen 3s ago');
    assert.equal(h.player.snapshot().view.virtualTargetPreview.available,false,'the receipt term no longer re-proves the aim');
    await h.advance(6750);r=h.controller.readback();assert.equal(r.active,true);assert.equal(r.status,'holding');
    const seen=h.notices.length;await h.advance(250);r=h.controller.readback();
    assert.equal(r.active,false);assert.equal(r.enabled,true);
    assert.equal(h.notices.slice(seen).find(n=>!n.active)?.reason,'preview_support_timeout');
    assert.deepEqual(h.fixtures.readback().fixtures,h.baseline.fixtures);
  }finally{h.close();}
  const s=harness(developmentExport());
  try{
    await s.start();await s.advance(3000);assert.equal(s.controller.readback().active,true);
    const seen=s.notices.length;await s.stop();const r=s.controller.readback();
    assert.equal(r.active,false);assert.equal(s.notices.slice(seen).find(n=>!n.active)?.reason,'preview_source_stopped');
    assert.deepEqual(s.fixtures.readback().fixtures,s.baseline.fixtures);assert.ok(s.released>=1);
  }finally{s.close();}
});

test('armed harness poll uses the 250 ms timer and graph every 4th turn',async()=>{
  let armed=true;const locks=fakeLocks(),h=harness(developmentExport(),{armed:()=>armed,locks});
  try{
    await h.start();await h.until(()=>[...h.timeouts.values()].some(v=>v.ms===250));
    const count=suffix=>h.reads.filter(p=>p.endsWith(suffix)).length,live=count('/live'),graph=count('/graph');
    for(let i=0;i<4;i++)await h.armedPoll();
    assert.equal(count('/live')-live,4);assert.equal(count('/graph')-graph,1,'graph stays at most 1 Hz');
    assert.equal(h.controller.readback().active,true);
    armed=false;await h.armedPoll();
    assert.ok([...h.timeouts.values()].some(v=>v.ms===1000));assert.ok(![...h.timeouts.values()].some(v=>v.ms===250));
    await h.until(()=>locks.held.size===0);
  }finally{h.close();}
});

test('prefetch read precedes arm-B output',async()=>{
  const data=developmentExport(),announced=copy(data.live);delete announced.arms.B;
  const h=harness(data,{armed:()=>true,live:announced});
  const media=()=>h.reads.filter(p=>p.includes('/media/'));
  try{
    h.controller.setEnabled(true);h.player.start();
    await h.until(()=>media().length>=2&&[...h.timeouts.values()].some(v=>v.ms===1000));
    assert.ok(media()[0].endsWith('/descriptor'));assert.equal(h.player.snapshot().imageUrl,null,'nothing selected before arm-B output');
    const before=media().length;h.setLive(null);await h.poll();
    await h.until(()=>h.player.snapshot().view?.virtualTargetPreview?.available);
    assert.equal(media().length,before,'the arm-B output is served from the verified prefetch');
    assert.ok(h.player.snapshot().imageUrl);assert.equal(h.controller.readback().active,true);
  }finally{h.close();}
});

for(const otherMedia of ['fail','hang'])
  test(`a newer 0-person output withdraws the hold while its image read ${otherMedia==='fail'?'has failed':'is pending'} (raw latest evidence)`,async()=>{
    const data=developmentExport(),h=harness(data,{otherMedia});
    try{
      await h.start();assert.equal(h.controller.readback().holding,true);const shown=h.player.snapshot().imageUrl;
      const seen=h.notices.length;h.setLive(newerOutput(data,{people:0}));await h.poll();
      await h.until(()=>!h.controller.readback().active);
      const r=h.controller.readback(),notice=h.notices.slice(seen).find(n=>!n.active);
      assert.equal(notice?.reason,'preview_no_person');assert.equal(r.enabled,true,'stays armed');
      assert.deepEqual(h.fixtures.readback().fixtures,h.baseline.fixtures);
      const state=h.player.snapshot();assert.equal(state.view.deskPreviewEvidence.armB.personCount,0);
      assert.equal(state.view.deskPreviewEvidence.backend,null,'no backend classification: Home counts the raw output');
      if(otherMedia==='hang')assert.equal(state.loading,true,'the newer image is still being read');
      else assert.equal(state.mediaReason!==null||state.loading===false,true);
      assert.equal(state.imageUrl,shown,'the old image is still the displayed one');
    }finally{await h.drain();h.close();}
  });

test('backend contrary evidence for a newer output withdraws even when Home counts one person',async()=>{
  const data=developmentExport(),event=data.live.arms.B.virtual_target_preview.evidence_event_id;
  const h=harness(data,{otherMedia:'hang'});
  try{
    await h.start();assert.equal(h.controller.readback().holding,true);
    const seen=h.notices.length;
    h.setLive(newerOutput(data,{people:1,preview:refused('desk_preview_observation_changed','contrary',event+1)}));await h.poll();
    await h.until(()=>!h.controller.readback().active);
    assert.equal(h.notices.slice(seen).find(n=>!n.active)?.reason,'preview_evidence_contrary');
    const evidence=h.player.snapshot().view.deskPreviewEvidence;
    assert.deepEqual(copy(evidence.backend),{evidence:'contrary',eventId:event+1});assert.equal(evidence.armB.personCount,1);
    assert.equal(h.controller.readback().enabled,true);assert.deepEqual(h.fixtures.readback().fixtures,h.baseline.fixtures);
  }finally{await h.drain();h.close();}
  // Backend neutral for a newer one-person output keeps the hold.
  const n=harness(data,{otherMedia:'hang'});
  try{
    await n.start();n.setLive(newerOutput(data,{people:1,preview:refused('desk_preview_observation_changed','neutral',event+1)}));await n.poll();
    await n.until(()=>n.player.snapshot().view?.deskPreviewEvidence?.backend?.eventId===event+1);
    const r=n.controller.readback();assert.equal(r.active,true);assert.equal(r.status,'holding');
  }finally{await n.drain();n.close();}
});
