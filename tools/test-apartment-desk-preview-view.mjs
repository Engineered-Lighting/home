/* Authored same-NPZ producer -> real exact-image Player. No camera/model/browser. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url),Player=require('../app/src/home-apartment-perception-player.js');
const View=require('../app/src/home-apartment-perception-view.js'),Stream=require('../app/src/home-den-lab-stream.js');
const generated=JSON.parse(execFileSync(process.env.PYTHON||'python',['-c',String.raw`
import sys,json
sys.path[:0]=['stack/services/perception-lab','tools/den-live-runtime','tools/shared-home','stack/services/home-context']
from test_apartment_desk_preview import DeskPreviewTests
DeskPreviewTests.setUpClass(); f=DeskPreviewTests('runTest')
try:
 f.setUp(); print(json.dumps({'preview':f.project(),'approximate':f.approximate},sort_keys=True,separators=(',',':'),allow_nan=False))
finally:
 f.doCleanups();DeskPreviewTests.doClassCleanups()
`],{cwd:new URL('..',import.meta.url),encoding:'utf8',timeout:15000,maxBuffer:1024*1024}));
const copy=structuredClone,scope=generated.preview.scope,settle=()=>new Promise(resolve=>setImmediate(resolve));
function envelope(){const frame={...copy(generated.preview.frame),valid_until:generated.preview.valid_until};
  // Actual Stream/descriptor identity carries camera and epoch in its envelope,
  // not on the decoded image descriptor. The original approximation retains both.
  delete frame.camera_id;delete frame.source_epoch;
  return {schema:'home-apartment-perception-1',scope:copy(scope),session_state:'recording',
  source_epoch:generated.preview.frame.source_epoch,frame,pose2d:null,models:[],room_pose:null,
  approximate_room_pose:copy(generated.approximate),approximate_receipt_elapsed_s:.1,
  virtual_target_preview:copy(generated.preview),virtual_target_preview_observation:copy(generated.preview.observation),
  virtual_target_preview_receipt_elapsed_s:.1};}
function playerFixture({clockMissing=false,uncertainty=null,mediaSha=null}={}){
  let ms=0,wall=1800000000.65,seq=0,sub=null,released=0;
  const timeouts=new Map(),intervals=new Map(),env={AbortController,document:{hidden:false},performance:{now:()=>ms},
    HomeApartmentPerceptionView:View,setInterval:fn=>{intervals.set(++seq,fn);return seq;},clearInterval:id=>intervals.delete(id),
    setTimeout:(fn,delay)=>{timeouts.set(++seq,{fn,at:ms+delay});return seq;},clearTimeout:id=>timeouts.delete(id)};
  const source={subscribe(value){sub=value;return()=>{sub=null;};},sampleClock:()=>clockMissing?null:{domain:'unix',now_s:wall,uncertainty_s:uncertainty},
    async readFrame(frame){return {bytes:Uint8Array.of(137,80,78,71),type:'image/png',
      frame_metadata:{media_sha256:mediaSha||frame.media_sha256,valid_until:generated.preview.valid_until}};}};
  const player=Player.create({source,scope,env,collisionDigest:'2'.repeat(64),decode:async(_,{frame})=>({url:'blob:authored-preview',
    width:frame.width,height:frame.height,dispose(){released++;}})});
  return {player,timeouts,intervals,env,push:v=>sub.onUpdate(v),status:v=>sub.onStatus(v),
    get released(){return released;},setWall:v=>{wall=v;},
    async advance(amount,{wallAdvance=true}={}){ms+=amount;if(wallAdvance)wall+=amount/1000;
      for(const[id,t]of[...timeouts])if(t.at<=ms){timeouts.delete(id);t.fn();}
      for(const fn of intervals.values())fn();await settle();},
    async decode(){await settle();await settle();}};
}

test('actual authored producer preview requires exact decoded image and reaches separate Player view',async()=>{
  const f=playerFixture();try{
    f.player.start();f.push(envelope());
    assert.equal(f.player.snapshot().view.virtualTargetPreview?.available,false);
    await f.decode();const out=f.player.snapshot().view.virtualTargetPreview;
    assert.equal(out?.available,true);assert.equal(out.status,'preview');
    assert.deepEqual(out.target.point_m,[2,2,.8]);assert.deepEqual(out.personPoint.joint_ids,[5,6]);
    assert.equal(out.label,'Person visible · configured desk preview');assert.equal(out.timingStatus,'unverified');
    assert.ok(out.remainingHoldS>0&&out.remainingHoldS<=.351);assert.equal(out.validUntil,1800000001);
    assert.equal(f.player.snapshot().view.follow.allowed,false);assert.equal(f.player.snapshot().view.roomPose.admitted,false);
  }finally{f.player.destroy();}
  assert.equal(f.timeouts.size,0);assert.equal(f.intervals.size,0);assert.equal(f.released,1);
});

test('foreign or malformed preview cannot replace valid approximate geometry or acquire authority',async()=>{
  const changes=[
    v=>v.virtual_target_preview.scope.sessionId='foreign',v=>v.virtual_target_preview.frame.media_sha256='0'.repeat(64),
    v=>v.virtual_target_preview.frame.camera_id='foreign',v=>v.virtual_target_preview.frame.source_epoch='foreign',
    v=>v.virtual_target_preview.provenance.original_event_sha256='0'.repeat(64),
    v=>v.virtual_target_preview.provenance.model_generation++,v=>v.virtual_target_preview.observation.request_id='foreign',
    v=>v.virtual_target_preview.observation.engine_epoch='foreign',v=>v.virtual_target_preview.observation.capture_epoch='foreign',
    v=>v.virtual_target_preview.target.binding.frame_digest='0'.repeat(64),
    v=>v.virtual_target_preview.target.binding.canonical_collision_digest='0'.repeat(64),
    v=>v.virtual_target_preview.target.equipment_ref.digest=null,v=>v.virtual_target_preview.target.normal_unit=[0,0,0],
    v=>v.virtual_target_preview.person_point.joint_ids=[0,5],v=>v.virtual_target_preview.person_point.point_m[0]++,
    v=>v.virtual_target_preview.person_point.instance_ref='foreign',v=>v.virtual_target_preview.person_point.error_bound_m=.01,
    v=>v.virtual_target_preview.valid_until++,v=>v.virtual_target_preview.source_times.valid_until++,
    v=>v.virtual_target_preview.physical_commands=true,v=>v.virtual_target_preview.geometry_authority=true,
    v=>v.virtual_target_preview.lighting_authority=true,v=>v.virtual_target_preview.follow_allowed=true,
    v=>v.virtual_target_preview.qualification='qualified',v=>v.virtual_target_preview.extra=true,
    v=>delete v.virtual_target_preview_observation,v=>v.virtual_target_preview_receipt_elapsed_s=null];
  for(const change of changes){const f=playerFixture();try{
    f.player.start();const value=envelope();change(value);f.push(value);await f.decode();
    const out=f.player.snapshot().view;assert.equal(out.approximateRoomPose.renderable,true);
    assert.equal(out.virtualTargetPreview.available,false);assert.equal(out.virtualTargetPreview.target,null);
    assert.equal(out.roomPose.admitted,false);assert.equal(out.follow.allowed,false);
    for(const field of ['operational_geometry','geometry_authority','identity_authority','engagement_authority','absence_authority',
      'protection_authority','beam_authority','physical_permission','follow_allowed','lighting_authority','physical_commands'])
      assert.equal(out.virtualTargetPreview[field],false,field);
  }finally{f.player.destroy();}}
});

test('finite wall deadline is mandatory and known uncertainty retires conservatively',async()=>{
  for(const options of [{clockMissing:true},{uncertainty:-1},{uncertainty:Infinity},{uncertainty:.35}]){
    const f=playerFixture(options);try{f.player.start();f.push(envelope());await f.decode();
      assert.equal(f.player.snapshot().view.virtualTargetPreview.available,false);
    }finally{f.player.destroy();}
  }
  const f=playerFixture({uncertainty:.1});try{f.player.start();f.push(envelope());await f.decode();
    assert.ok(Math.abs(f.player.snapshot().view.virtualTargetPreview.remainingHoldS-.25)<1e-6);
    f.setWall(1800000001);f.player.refresh();assert.equal(f.player.snapshot().view.virtualTargetPreview.available,false);
  }finally{f.player.destroy();}
});

test('original receipt cannot be renewed by polling and late or stopped originals withdraw preview',async()=>{
  const f=playerFixture();try{
    f.player.start();f.push(envelope());await f.decode();const key=f.player.snapshot().view.virtualTargetPreview.sourceKey;
    await f.advance(100,{wallAdvance:false});f.push(envelope());
    assert.equal(f.player.snapshot().view.virtualTargetPreview.sourceKey,key);
    await f.advance(1701,{wallAdvance:false});assert.equal(f.player.snapshot().view.virtualTargetPreview.available,false);
    f.push(envelope());assert.equal(f.player.snapshot().view.virtualTargetPreview.available,false);
  }finally{f.player.destroy();}
  const stopped=playerFixture();try{
    stopped.player.start();stopped.push(envelope());await stopped.decode();stopped.status({status:'stopped'});
    assert.equal(stopped.player.snapshot().view.virtualTargetPreview.available,false);
    stopped.push(envelope());assert.equal(stopped.player.snapshot().view.virtualTargetPreview.available,false);
  }finally{stopped.player.destroy();}
  const changed=playerFixture();try{
    changed.player.start();changed.push(envelope());await changed.decode();const value=envelope();
    value.approximate_room_pose.approximate_pose.joints[0].position_m[0]+=.1;changed.push(value);
    assert.equal(changed.player.snapshot().view.approximateRoomPose.reason,'approximate_original_event_changed');
    assert.equal(changed.player.snapshot().view.virtualTargetPreview.available,false);
  }finally{changed.player.destroy();}
});

test('new source epoch and undecoded next frame cannot retain an old target',async()=>{
  const f=playerFixture();try{
    f.player.start();f.push(envelope());await f.decode();assert.equal(f.player.snapshot().view.virtualTargetPreview.available,true);
    const next=envelope();next.source_epoch='changed-epoch';f.push(next);
    assert.equal(f.player.snapshot().view.virtualTargetPreview.available,false);
    f.player.stop();assert.equal(f.player.snapshot().view,null);
  }finally{f.player.destroy();}
});

test('selected-arm Stream copies only bounded preview with its exact terminal observation and elapsed read time',async()=>{
  const p=generated.preview,session={id:scope.sessionId,site_id:'victoria',camera_id:'den',generation:scope.generation,state:'recording'};
  const arm={id:'B',frame_id:p.frame.frame_id,frame:{id:p.frame.frame_id,media_id:p.frame.media_id,site_id:'victoria',
    generation:scope.generation,width:640,height:360},profile:{id:p.observation.run_id,name:p.observation.model_version},
    observation:copy(p.observation),approximate_room_pose:copy(generated.approximate),virtual_target_preview:copy(p)};
  async function inspect(change=()=>{}){
    let now=100;const timers=new Map();let seq=0;
    const live={site_id:'victoria',session_id:session.id,session,server_time:1800000000.65,experiment_generation:1,arms:{B:copy(arm)}};
    change(live);
    const graph={contract:'perception-lab-graph-1',site_id:'victoria',session_id:session.id,session,inference:'none',sources:[],arms:{}};
    const observer=Stream.createObserver({siteId:'victoria',sessionId:session.id,scope,generation:scope.generation,
      env:{performance:{now:()=>now},document:{hidden:false},setTimeout:(fn,ms)=>{timers.set(++seq,{fn,ms});return seq;},clearTimeout:id=>timers.delete(id)},
      transport:{async request(operation){if(operation==='sessions.live'){now=300;return live;}if(operation==='sessions.graph'){now=600;return graph;}throw Error('unexpected');}}});
    try{observer.start();await settle();await settle();return {value:observer.snapshot().perception,observer};}
    finally{observer.destroy();assert.equal(timers.size,0);}
  }
  const good=await inspect();assert.deepEqual(good.value.virtual_target_preview,p);
  assert.deepEqual(good.value.virtual_target_preview_observation,p.observation);
  assert.equal(good.value.virtual_target_preview_receipt_elapsed_s,.5);
  for(const change of [v=>delete v.arms.B.observation,v=>v.arms.B.observation.engine_epoch='',
    v=>v.arms.B.observation.media_ids=['foreign'],v=>v.arms.B.virtual_target_preview.extra='x'.repeat(16384)]){
    const out=await inspect(change);assert.equal(out.value.virtual_target_preview,null);assert.equal(out.value.virtual_target_preview_observation,null);
  }
  const other=await inspect(v=>{v.arms.A={...v.arms.B,id:'A',observation:{...v.arms.B.observation,arm:'a'}};delete v.arms.B;});
  assert.equal(other.value.virtual_target_preview,null);
});

// Development timing (virtual preview only): the backend's frozen development
// fields, authored on the same producer output. The backend's own development
// export is exercised in test-apartment-desk-preview-integration.mjs.
const Preview=require('../app/src/home-apartment-desk-preview.js');
const {createHash,webcrypto}=require('node:crypto');
const warning=(check,deadline=null,at=1800000000.6)=>({check,at,deadline});
function devEnvelope(warnings=[warning('clock_uncertainty')],{evidence='supporting',eventId=7,parentWarnings=null}={}){
  const value=envelope(),p=value.virtual_target_preview;
  value.timing_policy='development';value.frame.timing_policy='development';value.frame.viewing_only=true;
  p.timing={...p.timing,policy:'development',warnings:copy(warnings)};
  value.approximate_room_pose.timing={...value.approximate_room_pose.timing,policy:'development',warnings:copy(parentWarnings??warnings)};
  p.evidence=evidence;p.evidence_event_id=eventId;
  return value;
}
const strictViewing=()=>{const value=envelope();value.frame.viewing_only=true;return value;};

test('development policy accepts a preview past its original deadline with home_original_deadline_passed; strict still refuses',async()=>{
  const f=playerFixture();try{
    f.player.start();f.push(devEnvelope());await f.decode();
    let out=f.player.snapshot().view.virtualTargetPreview;
    assert.equal(out.available,true);assert.equal(out.timingPolicy,'development');assert.deepEqual(out.warnings,['clock_uncertainty']);
    assert.equal(out.evidence,'supporting');assert.equal(out.evidenceEventId,7);
    f.setWall(1800000001.2);f.player.refresh();out=f.player.snapshot().view.virtualTargetPreview;
    assert.equal(out.available,true);assert.deepEqual(out.warnings,['clock_uncertainty','home_original_deadline_passed']);
    assert.ok(out.remainingHoldS>0&&out.remainingHoldS<=2);assert.equal(out.validUntil,1800000001);
    assert.ok(Math.abs(out.originalAgeS-(1800000001.2-generated.preview.source_times.observed_at))<1e-6);
    for(const field of ['physical_commands','lighting_authority','geometry_authority','follow_allowed'])assert.equal(out[field],false,field);
    assert.equal(f.player.snapshot().view.follow.allowed,false);
  }finally{f.player.destroy();}
  // A backend deadline warning is reused; a warning on either arm is reported once.
  const reuse=playerFixture();try{
    reuse.setWall(1800000001.2);reuse.player.start();
    reuse.push(devEnvelope([warning('clock_uncertainty'),warning('media_expired',1800000001,1800000001.1)],
      {parentWarnings:[warning('clock_uncertainty'),warning('proof_expired',1800000000.9)]}));await reuse.decode();
    const out=reuse.player.snapshot().view.virtualTargetPreview;
    assert.equal(out.available,true);assert.deepEqual(out.warnings,['clock_uncertainty','media_expired','proof_expired']);
  }finally{reuse.player.destroy();}
  const strict=playerFixture();try{
    strict.player.start();strict.push(strictViewing());await strict.decode();
    assert.equal(strict.player.snapshot().view.virtualTargetPreview.timingPolicy,null);
    strict.setWall(1800000001.2);strict.player.refresh();
    assert.equal(strict.player.snapshot().view.virtualTargetPreview.available,false);
    const late=playerFixture();try{late.player.start();late.push(envelope());await late.decode();late.setWall(1800000001.2);late.player.refresh();
      assert.equal(late.player.snapshot().view.virtualTargetPreview.reason,'desk_preview_original_expired');}finally{late.player.destroy();}
  }finally{strict.player.destroy();}
});

test('development still requires exact displayed PNG sha/media/dims',async()=>{
  for(const change of [v=>v.virtual_target_preview.frame.media_sha256='0'.repeat(64),v=>v.virtual_target_preview.frame.width=641,
    v=>v.virtual_target_preview.frame.media_id='foreign-media',v=>v.virtual_target_preview.frame.frame_id='foreign-frame']){
    const f=playerFixture();try{f.player.start();const value=devEnvelope();change(value);f.push(value);await f.decode();
      const out=f.player.snapshot().view.virtualTargetPreview;assert.equal(out.available,false);
      assert.equal(out.reason,'desk_preview_displayed_frame_mismatch');assert.equal(out.target,null);
    }finally{f.player.destroy();}
  }
  const f=playerFixture({mediaSha:'0'.repeat(64)});try{f.player.start();f.push(devEnvelope());await f.decode();
    const out=f.player.snapshot().view.virtualTargetPreview;assert.equal(out.available,false);
    assert.ok(['desk_preview_displayed_frame_mismatch','desk_preview_approximation_unavailable'].includes(out.reason),out.reason);
  }finally{f.player.destroy();}
});

test('development timing fields must be exact and well-formed; strict packets with development fields refuse',async()=>{
  const t=v=>v.virtual_target_preview.timing,w=v=>t(v).warnings,p=v=>v.virtual_target_preview;
  const invalid='desk_preview_timing_policy_invalid';
  const cases=[[v=>{t(v).policy='strict';},invalid],[v=>{t(v).warnings='clock_uncertainty';},invalid],
    [v=>{w(v)[0].check='capture_clock_uncertainty';},invalid],[v=>{w(v).push(warning('clock_uncertainty'));},invalid],
    [v=>{w(v)[0].at=null;},invalid],[v=>{w(v)[0].deadline=1800000001;},invalid,'clock_uncertainty has no deadline'],
    [v=>{w(v).push(warning('approximate_receipt_hold_elapsed',1800000001));},invalid],
    [v=>{w(v).push(warning('media_expired','soon'));},invalid],[v=>{w(v)[0].extra=1;},invalid],
    [v=>{delete w(v)[0].deadline;},invalid],[v=>{delete t(v).warnings;},invalid],[v=>{delete t(v).policy;},invalid],
    [v=>{p(v).evidence='maybe';},invalid],[v=>{p(v).evidence_event_id=0;},invalid],[v=>{p(v).evidence_event_id=1.5;},invalid],
    [v=>{delete p(v).evidence;},invalid],[v=>{delete v.timing_policy;},invalid,'the stream must mark the envelope'],
    [v=>{delete v.approximate_room_pose.timing.policy;},'desk_preview_receipt_retired'],
    [v=>{v.approximate_room_pose.timing.warnings=[warning('unknown_check')];},'desk_preview_receipt_retired'],
    [v=>{v.approximate_room_pose.timing.policy='strict';},'desk_preview_receipt_retired','the parent carries the same policy'],
    [v=>{p(v).development_timing={policy:'development'};},'desk_preview_authority_invalid','the superseded field is not accepted'],
    [v=>{delete t(v).policy;delete t(v).warnings;},'desk_preview_authority_invalid','strict packets carry no evidence'],
    [v=>{p(v).evidence='contrary';},'desk_preview_evidence_contrary'],[v=>{p(v).evidence='integrity';},'desk_preview_evidence_integrity']];
  for(const [change,reason,name=reason] of cases){const f=playerFixture();try{
    f.player.start();const value=devEnvelope();change(value);f.push(value);await f.decode();
    const out=f.player.snapshot().view.virtualTargetPreview;assert.equal(out.available,false,name);assert.equal(out.reason,reason,name);
    assert.equal(out.timingPolicy,null,name);assert.deepEqual(out.warnings,[],name);
  }finally{f.player.destroy();}}
  const all=['clock_uncertainty','anchor_containment','proof_expired','registration_expired','dispatch_late','lease_retired',
    'finalize_expired','media_expired','approximate_receipt_hold_elapsed','newer_observation_not_projected'];
  const every=all.map(check=>warning(check,['clock_uncertainty','anchor_containment','approximate_receipt_hold_elapsed',
    'newer_observation_not_projected'].includes(check)?null:1800000000.95));
  const f=playerFixture();try{f.player.start();f.push(devEnvelope(every));await f.decode();
    assert.deepEqual(f.player.snapshot().view.virtualTargetPreview.warnings,all,'one entry per check, all ten');
  }finally{f.player.destroy();}
});

test('preview and parent timing pair by identity, not by their separately sampled receipt floats',async()=>{
  const drift=value=>{const t=value.approximate_room_pose.timing;t.receipt_age_s+=.005;t.remaining_receipt_hold_s-=.005;return value;};
  for(const [name,value] of [['strict',drift(envelope())],['development',drift(devEnvelope([warning('clock_uncertainty')],{parentWarnings:[]}))]]){
    const f=playerFixture();try{f.player.start();f.push(value);await f.decode();
      assert.equal(f.player.snapshot().view.virtualTargetPreview.available,true,name);
    }finally{f.player.destroy();}
  }
  for(const [name,change,reason] of [
    ['other event',v=>{v.approximate_room_pose.approximate_pose.provenance.source_event_id++;},'desk_preview_original_mismatch'],
    ['other source times',v=>{v.approximate_room_pose.approximate_pose.source_times.observed_at-=.01;},'desk_preview_displayed_frame_mismatch'],
    ['other frame',v=>{v.approximate_room_pose.frame.frame_id='foreign-frame';},'desk_preview_displayed_frame_mismatch'],
    ['strict parent of a development preview',v=>{delete v.approximate_room_pose.timing.policy;delete v.approximate_room_pose.timing.warnings;},'desk_preview_receipt_retired']]){
    const f=playerFixture();try{f.player.start();const value=devEnvelope();change(value);f.push(value);await f.decode();
      const out=f.player.snapshot().view.virtualTargetPreview;assert.equal(out.available,false,name);
      if(out.reason!=='desk_preview_approximation_unavailable')assert.equal(out.reason,reason,name);
    }finally{f.player.destroy();}
  }
});

test('the named development mount-check skip is accepted only under development timing',async()=>{
  const skip=value=>{value.approximate_room_pose.approximate_pose.registration.mount_movement_detection={status:'mount_check_skipped_development',
    result:null,compared_cells:null,changed_cells:null,physical_movement_error_bound_m:null};return value;};
  const dev=playerFixture();try{dev.player.start();dev.push(skip(devEnvelope()));await dev.decode();
    const view=dev.player.snapshot().view;assert.equal(view.approximateRoomPose.renderable,true);assert.equal(view.virtualTargetPreview.available,true);
  }finally{dev.player.destroy();}
  for(const [name,value] of [['strict',skip(strictViewing())],['unmarked envelope',(v=>{delete v.timing_policy;return v;})(skip(devEnvelope()))],
    ['changed cells',(v=>{v.approximate_room_pose.approximate_pose.registration.mount_movement_detection.changed_cells=3;return v;})(skip(devEnvelope()))]]){
    const f=playerFixture();try{f.player.start();f.push(value);await f.decode();
      assert.equal(f.player.snapshot().view.approximateRoomPose.reason,'approximate_view_alignment_invalid',name);
    }finally{f.player.destroy();}
  }
});

test('development relaxes only the output available_at deadline, recording a warning; strict still refuses',async()=>{
  const late=(value,warnings=null)=>{const t=generated.preview.source_times.valid_until;
    for(const times of [value.virtual_target_preview.source_times,value.approximate_room_pose.approximate_pose.source_times,
      value.virtual_target_preview.observation,value.virtual_target_preview_observation])times.available_at=t;
    if(warnings){value.virtual_target_preview.timing.warnings=warnings;value.approximate_room_pose.timing.warnings=copy(warnings);}
    return value;};
  const f=playerFixture();try{f.player.start();f.push(late(devEnvelope()));await f.decode();
    const out=f.player.snapshot().view.virtualTargetPreview;assert.equal(out.available,true);
    assert.deepEqual(out.warnings,['clock_uncertainty','home_original_deadline_passed']);
  }finally{f.player.destroy();}
  const reuse=playerFixture();try{reuse.player.start();
    reuse.push(late(devEnvelope(),[warning('finalize_expired',generated.preview.source_times.valid_until)]));await reuse.decode();
    const out=reuse.player.snapshot().view.virtualTargetPreview;assert.equal(out.available,true);assert.deepEqual(out.warnings,['finalize_expired']);
  }finally{reuse.player.destroy();}
  const ordered=playerFixture();try{ordered.player.start();const value=late(devEnvelope());
    for(const times of [value.virtual_target_preview.source_times,value.approximate_room_pose.approximate_pose.source_times,
      value.virtual_target_preview.observation,value.virtual_target_preview_observation])times.observed_at=times.available_at+.01;
    ordered.push(value);await ordered.decode();
    assert.equal(ordered.player.snapshot().view.virtualTargetPreview.reason,'desk_preview_displayed_frame_mismatch','observed_at <= available_at stays exact');
  }finally{ordered.player.destroy();}
  const strict=playerFixture();try{strict.player.start();strict.push(late(strictViewing()));await strict.decode();
    assert.equal(strict.player.snapshot().view.virtualTargetPreview.reason,'desk_preview_displayed_frame_mismatch');
  }finally{strict.player.destroy();}
});

test('player keeps the development metric PNG past media deadline; strict frames retire frame_expired',async()=>{
  const dev=playerFixture();try{dev.setWall(1800000001.3);dev.player.start();dev.push(devEnvelope());await dev.decode();
    assert.equal(dev.player.snapshot().imageUrl,'blob:authored-preview');assert.deepEqual(dev.player.snapshot().mediaWarnings,['media_original_expired']);
    assert.equal(dev.player.snapshot().view.virtualTargetPreview.available,true);
    await dev.advance(250,{wallAdvance:true});assert.equal(dev.player.snapshot().imageUrl,'blob:authored-preview');
    dev.player.stop();assert.deepEqual(dev.player.snapshot().mediaWarnings,[]);
  }finally{dev.player.destroy();}
  const strict=playerFixture();try{strict.setWall(1800000001.3);strict.player.start();strict.push(strictViewing());await strict.decode();
    assert.equal(strict.player.snapshot().imageUrl,null);assert.equal(strict.player.snapshot().mediaReason,'frame_expired');
    assert.deepEqual(strict.player.snapshot().mediaWarnings,[]);
  }finally{strict.player.destroy();}
  const unmarked=playerFixture();try{unmarked.setWall(1800000001.3);unmarked.player.start();const value=devEnvelope();delete value.frame.timing_policy;
    unmarked.push(value);await unmarked.decode();assert.equal(unmarked.player.snapshot().mediaReason,'frame_expired');
    assert.equal(unmarked.player.snapshot().imageUrl,null);
  }finally{unmarked.player.destroy();}
});

function evidenceEnvelope(persons,{boxes=null,frame='f1',request='r1'}={}){
  const pose={frame_id:frame,source_id:'specialist_b',request_id:request,observed_at:10,capture_epoch:'epoch',
    ...(persons?{persons2d:{schema:'home-lab-frame-persons-1',version:1,frame_id:frame,coordinate_space:'source_pixels',
      persons:persons.map((box,i)=>({instance_id:'p'+i,box}))}}:{}),...(boxes?{boxes}:{})};
  return {timing_policy:'development',session_state:'recording',source_epoch:'epoch',pose2d:pose,models:[]};
}
test('evidence(): 0/1/2 counts, merge at IoU 0.9 with confidence 0.39, no merge at 0.4 or IoU 0.89, missing confidence counts two, boxes fallback',()=>{
  const count=(persons,options)=>Preview.evidence({envelope:evidenceEnvelope(persons,options),connection:'live',approximate:null}).armB;
  const a={x:0,y:0,w:100,h:100,confidence:.9};
  assert.equal(count([]).personCount,0);assert.equal(count([a]).personCount,1);
  assert.equal(count([a,{x:300,y:0,w:100,h:100,confidence:.8}]).personCount,2);
  const merged=count([{x:0,y:0,w:90,h:100,confidence:.39},a]);
  assert.equal(merged.personCount,1);assert.equal(merged.mergedDuplicates,1);assert.equal(merged.countSource,'persons2d');
  assert.equal(count([a,{x:0,y:0,w:90,h:100,confidence:.4}]).personCount,2);
  assert.equal(count([a,{x:0,y:0,w:89,h:100,confidence:.39}]).personCount,2);
  assert.equal(count([{x:0,y:0,w:100,h:100},{x:0,y:0,w:100,h:100}]).personCount,2,'missing confidence never merges');
  assert.equal(count([{x:0,y:0,w:100,h:100,confidence:.3},{x:0,y:0,w:100,h:100,confidence:.3}]).personCount,2,'a tie never merges');
  const chain=count([a,{x:0,y:0,w:100,h:95,confidence:.35},{x:0,y:0,w:100,h:89.9,confidence:.3}]);
  assert.equal(chain.personCount,1,'a chain merges against every box, merged ones included');assert.equal(chain.mergedDuplicates,2);
  assert.equal(count([a,{x:0,y:0,w:100,h:100}]).personCount,2);
  const fallback=count(null,{boxes:[{x:10,y:10,w:50,h:120,label:'person',instance_id:'b0'}]});
  assert.equal(fallback.personCount,1);assert.equal(fallback.countSource,'boxes');
  assert.equal(count([{x:0,y:0,w:-1,h:100}]).personCount,null,'malformed boxes are neutral');
  assert.equal(count([{x:0,y:0,w:10,h:10,confidence:2}]).personCount,null);
  assert.equal(count(null).personCount,null);
  const ev=Preview.evidence({envelope:evidenceEnvelope([a]),connection:'live',approximate:null});
  assert.deepEqual(ev,{policy:'development',backend:null,connection:'live',sessionState:'recording',sourceEpoch:'epoch',integrityReason:null,
    armB:{key:JSON.stringify(['r1','f1']),observedAt:10,captureEpoch:'epoch',personCount:1,mergedDuplicates:0,countSource:'persons2d'}});
  const noOriginal=evidenceEnvelope([a]);delete noOriginal.pose2d.request_id;
  assert.equal(Preview.evidence({envelope:noOriginal,connection:'live'}).armB,null);
  const otherArm=evidenceEnvelope([a]);otherArm.pose2d.source_id='specialist_a';
  assert.equal(Preview.evidence({envelope:otherArm,connection:'live'}).armB,null);
  const strict=evidenceEnvelope([a]);delete strict.timing_policy;assert.equal(Preview.evidence({envelope:strict}).policy,'strict');
  assert.equal(Preview.evidence({envelope:null}),null);
});

test('evidence(): duplicate merge matches the backend distinct_people on the same boxes',()=>{
  const b=(x,y,w,h,confidence)=>({x,y,w,h,...(confidence===undefined?{}:{confidence})});
  const cases=[[],[b(0,0,100,100,.9)],[b(0,0,100,100,.9),b(0,0,90,100,.39)],[b(0,0,100,100,.9),b(0,0,90,100,.4)],
    [b(0,0,100,100,.9),b(0,0,89,100,.39)],[b(0,0,100,100,.3),b(0,0,100,100,.3)],[b(0,0,100,100),b(0,0,100,100,.1)],
    [b(0,0,100,100,.9),b(0,0,100,95,.35),b(0,0,100,89.9,.3)],[b(0,0,100,100,.2),b(0,0,100,100,.1),b(0,0,100,100,.05)],
    [b(0,0,100,100,.9),b(300,0,100,100,.8),b(300,0,100,95,.2)],[b(0,0,100,100,.39),b(0,0,100,100,.38)]];
  const backend=JSON.parse(execFileSync(process.env.PYTHON||'python',['-I','-c',String.raw`
import json,sys
sys.path.insert(0,'stack/services/perception-lab')
from apartment_desk_preview import distinct_people
cases=json.loads(sys.stdin.read())
out=[]
for boxes in cases:
    objects=[{'label':'person','instance_id':'p%d'%i} for i in range(len(boxes))]
    rows=[dict(box,label='person',instance_id='p%d'%i) for i,box in enumerate(boxes)]
    out.append(len(distinct_people({'objects':objects,'boxes':rows})))
print(json.dumps(out))`],{cwd:new URL('..',import.meta.url),input:JSON.stringify(cases),encoding:'utf8',timeout:15000}));
  const home=cases.map(boxes=>Preview.evidence({envelope:evidenceEnvelope(boxes),connection:'live'}).armB.personCount);
  assert.deepEqual(home,backend);assert.deepEqual(backend,[0,1,1,2,2,2,2,1,1,2,1]);
});

test('evidence(): the backend classification is read from the raw preview packet only under development',()=>{
  const packet=(evidence,eventId=9,timing={policy:'development',warnings:[]})=>({available:false,reason:'desk_preview_single_person_required',
    timing,evidence,evidence_event_id:eventId});
  const read=(p,change=()=>{})=>{const value=evidenceEnvelope([]);value.virtual_target_preview=p;change(value);
    return Preview.evidence({envelope:value,connection:'live'}).backend;};
  for(const evidence of ['supporting','contrary','integrity','neutral'])assert.deepEqual(read(packet(evidence)),{evidence,eventId:9});
  assert.deepEqual(read(packet('neutral',null)),{evidence:'neutral',eventId:null});
  assert.equal(read(packet('contrary'),v=>{delete v.timing_policy;}),null,'strict envelopes carry no backend evidence');
  for(const bad of [packet('maybe'),packet('contrary',0),packet('contrary',9,{policy:'development'}),
    packet('contrary',9,{policy:'development',warnings:[{check:'clock_uncertainty',at:1,deadline:2}]}),packet('contrary',9,null),null])
    assert.equal(read(bad),null,JSON.stringify(bad));
  const value=devEnvelope([warning('clock_uncertainty')],{evidence:'neutral',eventId:12});value.virtual_target_preview.available=false;
  value.virtual_target_preview.reason='desk_preview_hold_elapsed';
  const out=Preview.project({packet:value.virtual_target_preview,envelope:value,scope});
  assert.equal(out.reason,'desk_preview_hold_elapsed');assert.equal(out.evidence,'neutral');assert.equal(out.evidenceEventId,12);
});

test('evidence(): integrity pass-through; parentReason carried',()=>{
  const reasonOf=(change,approximate=null)=>{const value=evidenceEnvelope([]);change(value);
    return Preview.evidence({envelope:value,connection:'live',approximate}).integrityReason;};
  assert.equal(reasonOf(v=>{v.approximate_room_pose={renderable:false,reason:'approximate_camera_identity_changed'};}),'approximate_camera_identity_changed');
  assert.equal(reasonOf(v=>{v.approximate_room_pose={renderable:false,reason:'approximate_mount_signature_changed'};}),'approximate_mount_signature_changed');
  assert.equal(reasonOf(()=>{},{reason:'approximate_source_epoch_mismatch'}),'approximate_source_epoch_mismatch');
  assert.equal(reasonOf(v=>{v.models=[{source_id:'apartment_viewing_metric',reason:'approximate_successor_scope_changed'}];}),'approximate_successor_scope_changed');
  assert.equal(reasonOf(v=>{v.virtual_target_preview={available:false,reason:'desk_preview_target_binding_changed'};}),'desk_preview_target_binding_changed');
  assert.equal(reasonOf(v=>{v.approximate_room_pose={renderable:false,reason:'approximate_receipt_retired'};},{reason:'approximate_displayed_frame_mismatch'}),null);
  assert.equal(reasonOf(v=>{v.approximate_room_pose={renderable:true,reason:'approximate_camera_identity_changed'};}),null,'renderable parent');
  const value=envelope(),out=Preview.project({packet:value.virtual_target_preview,envelope:value,scope,
    displayedFrame:null,approximate:{renderable:false,reason:'approximate_receipt_retired'},clock:null,receipt:null});
  assert.equal(out.reason,'desk_preview_approximation_unavailable');assert.equal(out.parentReason,'approximate_receipt_retired');
  assert.equal(Preview.project({packet:value.virtual_target_preview,envelope:value,scope,approximate:null}).parentReason,null);
});

// Stream observer: authored /live, /graph and media with an injected clock.
function fakeLocks(){const held=new Set(),calls=[];
  return {held,calls,request(name,options,callback){calls.push({name,options});
    if(held.has(name))return Promise.resolve().then(()=>callback(null));held.add(name);
    return Promise.resolve().then(()=>callback({name,mode:'exclusive'})).finally(()=>held.delete(name));}};}
function streamFixture({armed=()=>false,locks=null,armB=true,development=true,registered={},retention=null,wall=1800000000.65,item:mutateItem=()=>{},describe=null}={}){
  const p=generated.preview,png=Uint8Array.of(137,80,78,71,13,10,26,10,7,7,7),sha=createHash('sha256').update(png).digest('hex');
  const session={id:scope.sessionId,site_id:'victoria',camera_id:'den',generation:scope.generation,state:'recording'};
  const arm={id:'B',frame_id:p.frame.frame_id,frame:{id:p.frame.frame_id,media_id:p.frame.media_id,site_id:'victoria',
    generation:scope.generation,width:640,height:360,viewing_only:true},profile:{id:p.observation.run_id,name:p.observation.model_version},
    observation:copy(p.observation),approximate_room_pose:copy(generated.approximate),virtual_target_preview:copy(p)};
  const metric={enabled:true,arm:'b',physical_permission:false,operational_geometry:false,metric_depth:'diagnostic_nominal_eeprom',
    room_alignment:'approximate_viewing_only',state:'available',reason:null,...(development?{timing_policy:'development'}:{}),
    registered_frame:{frame_id:p.frame.frame_id,media_id:p.frame.media_id,media_sha256:sha,generation:scope.generation,event_id:null,...registered}};
  const liveWith=withArm=>({site_id:'victoria',session_id:session.id,session,server_time:1800000000.65,experiment_generation:1,
    arms:withArm?{B:copy(arm)}:{},apartment_viewing:{state:'active',media_storage:'bounded_ram',encoded_recording:false,viewing_only:true,
      physical_permission:false,metric:copy(metric)}});
  let live=liveWith(armB),mono=100,seq=0;const timers=new Map(),calls=[];
  const item={id:p.frame.media_id,media_id:p.frame.media_id,site_id:'victoria',session_id:session.id,generation:scope.generation,
    size_bytes:png.length,content_type:'image/png',sha256:sha,metadata:{site_id:'victoria',camera_id:'den',frame_id:p.frame.frame_id,
      capture_epoch:p.observation.capture_epoch,valid_until:1800000001,width:640,height:360},...(retention?{retention}:{})};
  mutateItem(item);
  const graph={contract:'perception-lab-graph-1',site_id:'victoria',session_id:session.id,session,inference:'none',sources:[],arms:{}};
  const transport={async request(op){calls.push(op);if(op==='sessions.live')return copy(live);if(op==='sessions.graph')return copy(graph);
      if(op==='media.describe'){const fault=describe?.(f.count('media.describe'));if(fault)throw fault;return copy(item);}throw Error('unexpected:'+op);},
    async requestMedia(args){calls.push('media.raw');const end=Math.min(args.offset+args.length,png.length);
      return {data:png.slice(args.offset,end),contentType:'image/png',contentRange:`bytes ${args.offset}-${end-1}/${png.length}`,generation:scope.generation};}};
  const env={performance:{now:()=>mono},document:{hidden:false},Date:{now:()=>wall*1000},crypto:webcrypto,
    HomePerceptionLabMedia:{readFrame(){calls.push('adapter.readFrame');throw Error('adapter path not expected');}},
    ...(locks?{navigator:{locks}}:{}),setTimeout:(fn,ms)=>{timers.set(++seq,{fn,ms});return seq;},clearTimeout:id=>timers.delete(id)};
  const observer=Stream.createObserver({siteId:'victoria',sessionId:session.id,scope,generation:scope.generation,env,transport,previewArmed:armed});
  // A turn ends by scheduling its next poll; WebCrypto digests settle on the
  // thread pool, so wait (bounded) for that schedule rather than a fixed tick count.
  const flush=async()=>{for(let i=0;i<6;i++)await settle();
    for(let i=0;i<500&&observer.pollState().intervalMs===null;i++)await new Promise(resolve=>setTimeout(resolve,1));};
  const f={observer,calls,timers,png,sha,arm,
    count:op=>calls.filter(c=>c===op).length,media:()=>calls.filter(c=>c==='media.describe'||c==='media.raw').length,
    pending:()=>[...timers.values()].filter(t=>t.ms===250||t.ms===1000),setLive:withArm=>{live=liveWith(withArm);},
    async start(){observer.start();await flush();},
    async fire(ms){const entry=[...timers].find(([,t])=>t.ms===ms);assert.ok(entry,'scheduled '+ms+' ms poll');
      timers.delete(entry[0]);mono+=ms;entry[1].fn();await flush();},
    close(){observer.destroy();assert.equal(timers.size,0);}};
  return f;
}

test('Stream: armed observer reads /live every 250 ms and /graph at most 1 Hz; disarm returns to 1 s',async()=>{
  let armed=true;const locks=fakeLocks(),f=streamFixture({armed:()=>armed,locks});try{
    await f.start();
    assert.deepEqual(f.pending().map(t=>t.ms),[250]);
    assert.deepEqual(f.observer.pollState(),{armed:true,lock:'held',intervalMs:250,reason:null});
    assert.equal(locks.calls[0].name,Stream.ARMED_LIVE_LOCK_NAME);assert.deepEqual(locks.calls[0].options,{ifAvailable:true});
    assert.equal(Stream.ARMED_LIVE_POLL_INTERVAL_MS,250);assert.equal(Stream.OBSERVER_POLL_INTERVAL_MS,1000);
    for(let i=0;i<4;i++)await f.fire(250);
    assert.equal(f.count('sessions.live'),5);assert.equal(f.count('sessions.graph'),2,'graph every fourth 250 ms turn');
    armed=false;await f.fire(250);await settle();
    assert.deepEqual(f.pending().map(t=>t.ms),[1000]);assert.equal(f.observer.pollState().lock,'idle');assert.equal(locks.held.size,0);
    assert.equal(f.count('sessions.graph'),3,'the disarmed turn reads the graph');
    await f.fire(1000);assert.equal(f.count('sessions.graph'),4,'unarmed turns read the graph every time');
  }finally{f.close();}
  assert.equal(locks.held.size,0);
});

test('Stream: second tab without the lock stays at 1 s',async()=>{
  const locks=fakeLocks(),first=streamFixture({armed:()=>true,locks}),second=streamFixture({armed:()=>true,locks});
  try{
    await first.start();await second.start();
    assert.equal(first.observer.pollState().intervalMs,250);
    assert.deepEqual(second.observer.pollState(),{armed:true,lock:'unavailable',intervalMs:1000,reason:'preview_armed_in_another_tab'});
    await second.fire(1000);assert.equal(second.count('sessions.graph'),2);assert.equal(second.observer.pollState().intervalMs,1000);
    first.observer.stop();await settle();await settle();assert.equal(locks.held.size,0);
    await second.fire(1000);assert.equal(second.observer.pollState().lock,'held');assert.deepEqual(second.pending().map(t=>t.ms),[250]);
  }finally{first.close();second.close();}
  const none=streamFixture({armed:()=>true,locks:null});try{await none.start();
    assert.deepEqual(none.observer.pollState(),{armed:true,lock:'unsupported',intervalMs:1000,reason:'preview_armed_lock_unavailable'});
  }finally{none.close();}
  const throwing=streamFixture({armed:()=>{throw Error('authored');},locks:fakeLocks()});try{await throwing.start();
    assert.equal(throwing.observer.pollState().intervalMs,1000);assert.equal(throwing.observer.pollState().armed,false);
  }finally{throwing.close();}
});

test('Stream: announced PNG prefetched then served from cache',async()=>{
  const f=streamFixture({armed:()=>true,armB:false});try{
    await f.start();
    assert.equal(f.observer.snapshot().perception.frame,null,'no arm-B output yet');
    assert.equal(f.count('media.describe'),1);assert.ok(f.count('media.raw')>=1);assert.equal(f.count('adapter.readFrame'),0);
    const before=f.media();f.setLive(true);await f.fire(1000);
    const frame=f.observer.snapshot().perception.frame;assert.equal(frame.timing_policy,'development');
    assert.equal(frame.width,640,'the selected frame keeps the arm descriptor dimensions');
    assert.equal(f.observer.snapshot().perception.timing_policy,'development');
    const value=await f.observer.readFrame(frame);
    assert.deepEqual([...value.bytes],[...f.png]);assert.equal(value.frame_metadata.media_sha256,f.sha);
    assert.equal(value.frame_metadata.valid_until,1800000001);assert.equal(f.media(),before,'served from the verified prefetch');
  }finally{f.close();}
  const changed=streamFixture({armed:()=>true,armB:false,item:i=>{i.metadata.capture_epoch='replacement-capture';}});try{
    await changed.start();assert.equal(changed.count('media.describe'),1);changed.setLive(true);await changed.fire(1000);
    await assert.rejects(changed.observer.readFrame(changed.observer.snapshot().perception.frame),/den_lab_frame_descriptor_mismatch/);
    assert.equal(changed.count('media.describe'),2,'a prefetched epoch mismatch is never served from cache');
  }finally{changed.close();}
  // The announcement has no dimensions: the descriptor's must match the selected frame.
  const resized=streamFixture({armed:()=>true,armB:false,item:i=>{i.metadata.width=1280;i.metadata.height=720;}});try{
    await resized.start();assert.equal(resized.count('media.describe'),1,'prefetched with the descriptor dimensions');
    resized.setLive(true);await resized.fire(1000);const before=resized.count('media.describe');
    await resized.observer.readFrame(resized.observer.snapshot().perception.frame);
    assert.equal(resized.count('media.describe'),before+1,'a dimension mismatch is never served from the prefetch');
  }finally{resized.close();}
});

test('Stream: transient prefetch failures retry; descriptor and SHA mismatches refuse the announcement',async()=>{
  const busy=Object.assign(Error('den_lab_read_busy'),{code:'den_lab_read_busy'});
  for(const fault of [busy,Object.assign(Error('den_lab_read_abandoned'),{code:'den_lab_read_abandoned'}),
    Object.assign(Error('den_lab_read_timeout'),{code:'den_lab_read_timeout'}),Error('network')]){
    const f=streamFixture({armed:()=>true,armB:false,locks:fakeLocks(),describe:n=>n===1?fault:null});try{
      await f.start();assert.equal(f.count('media.describe'),1,fault.message);assert.equal(f.count('media.raw'),1,'the parallel prefix');
      await f.fire(250);assert.equal(f.count('media.describe'),2,fault.message+' retries on the next armed turn');
      const raw=f.count('media.raw');await f.fire(250);
      assert.equal(f.count('media.describe'),2,'cached after the retry');assert.equal(f.count('media.raw'),raw);
    }finally{f.close();}
  }
  for(const [name,options] of [['sha',{registered:{media_sha256:'0'.repeat(64)}}],['dimensions',{item:i=>{delete i.metadata.width;}}],
    ['frame',{item:i=>{i.metadata.frame_id='foreign-frame';}}]]){
    const f=streamFixture({armed:()=>true,armB:false,locks:fakeLocks(),...options});try{
      await f.start();assert.equal(f.count('media.describe'),1,name);
      await f.fire(250);await f.fire(250);assert.equal(f.count('media.describe'),1,name+' is not retried at the armed cadence');
    }finally{f.close();}
  }
  const corrupt=streamFixture({armed:()=>true,armB:false,locks:fakeLocks(),item:i=>{i.sha256='0'.repeat(64);},registered:{media_sha256:'0'.repeat(64)}});try{
    await corrupt.start();assert.equal(corrupt.count('media.describe'),1);
    await corrupt.fire(250);assert.equal(corrupt.count('media.describe'),1,'a byte digest mismatch refuses the announcement');
  }finally{corrupt.close();}
});

test('Stream: unannounced prefetch refused',async()=>{
  for(const [name,options] of [['strict metric',{development:false}],['not armed',{armed:()=>false}],
    ['superseded dimensions key',{registered:{width:640}}],['foreign generation',{registered:{generation:scope.generation+1}}],
    ['bad event id',{registered:{event_id:0}}],['bad id',{registered:{media_id:'../escape'}}]]){
    const f=streamFixture({armed:()=>true,armB:false,...options});try{await f.start();
      assert.equal(f.media(),0,name);assert.equal(f.count('adapter.readFrame'),0,name);
    }finally{f.close();}
  }
  const mismatch=streamFixture({armed:()=>true,armB:false,registered:{media_sha256:'0'.repeat(64)}});try{
    await mismatch.start();assert.equal(mismatch.count('media.describe'),1);
    mismatch.setLive(true);await mismatch.fire(1000);
    const before=mismatch.media();
    await mismatch.observer.readFrame(mismatch.observer.snapshot().perception.frame);
    assert.ok(mismatch.media()>before,'a refused prefetch caches nothing');
  }finally{mismatch.close();}
  const f=streamFixture({armed:()=>false,armB:false});try{await f.start();
    const p=generated.preview,announced={frame_id:p.frame.frame_id,id:p.frame.frame_id,site_id:'victoria',media_id:p.frame.media_id,
      media_sha256:f.sha,generation:scope.generation,timing_policy:'development'};
    await assert.rejects(f.observer.readFrame(announced),/den_lab_current_frame_required/,'an announcement is not a selected frame');
    assert.equal(f.media(),0);
  }finally{f.close();}
});

test('Stream: den_lab_frame_expired skipped only for development-retained media',async()=>{
  const retention={policy:'development',original_valid_until:1800000001,retained_until:1800000013};
  const read=async options=>{const f=streamFixture({wall:1800000001.5,...options});try{await f.start();
    return await f.observer.readFrame(f.observer.snapshot().perception.frame);}finally{f.close();}};
  const value=await read({retention});assert.equal(value.frame_metadata.valid_until,1800000001,'the original deadline is unchanged');
  assert.ok((await read({retention:{...retention,retained_until:1800000002},registered:{event_id:14}})).bytes.length>0);
  for(const [name,options] of [['no retention',{}],['strict frame',{retention,development:false}],
    ['other policy',{retention:{...retention,policy:'strict'}}],['changed original',{retention:{...retention,original_valid_until:1800000002}}],
    ['retained deadline passed',{retention:{...retention,retained_until:1800000001.2}}],['malformed',{retention:{...retention,retained_until:{}}}],
    ['superseded label',{retention:{...retention,retained_until:'superseded_or_budget'}}],['extra key',{retention:{...retention,extra:1}}]])
    await assert.rejects(read(options),/den_lab_frame_expired/,name);
});
