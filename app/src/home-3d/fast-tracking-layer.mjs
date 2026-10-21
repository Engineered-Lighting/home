// Independent presentation lane. No video decode, Qwen/JEV or actuation API.
import * as THREE from 'three';
import {createBodyFormRenderer} from './body-form-renderer.mjs';
const sceneKeys=['siteId','contentRevision','frameDigest','registrationVersion'];
const finite=value=>typeof value==='number'&&Number.isFinite(value);
export function trackingGpuLatency(source,clock,completedMs){
  const invalid=reason=>({valid:false,reason});
  if(!Array.isArray(source)||source.length!==2||!source.every(finite)
      ||!clock||![clock.offsetLow,clock.offsetHigh,completedMs].every(finite))return invalid('nonfinite_bounds');
  if(source[1]<source[0]||clock.offsetHigh<clock.offsetLow)return invalid('inverted_bounds');
  const rawMs=[completedMs/1000-clock.offsetHigh-source[1],completedMs/1000-clock.offsetLow-source[0]].map(v=>v*1000);
  if(!rawMs.every(finite))return invalid('nonfinite_bounds');
  if(rawMs[1]<rawMs[0])return invalid('inverted_bounds');
  if(rawMs[1]<0)return invalid('wholly_negative_bounds');
  // The draw is causally after its source observation. A broad valid clock
  // interval may straddle zero; retain that raw uncertainty and intersect it
  // with causality rather than discarding an actual GPU-completed update.
  return {valid:true,rawMs,boundsMs:[Math.max(0,rawMs[0]),rawMs[1]],
    qualification:rawMs[0]<0?'causality_bounded':'clock_bounded'};
}
export function validateTrackingPacket(packet,scene,{attempt=null,sequence=0,clock=null,nowMs=performance.now()}={}) {
  return trackingPacketValidity(packet,scene,{attempt,sequence,clock,nowMs}).valid;
}
export function trackingPacketValidity(packet,scene,{attempt=null,sequence=0,clock=null,nowMs=performance.now()}={}) {
  const invalid=reason=>({valid:false,reason});
  if(!packet||packet.schema!=='home-fast-tracking-1')return invalid('packet_contract');
  if(packet.status!=='available')return invalid('producer_unavailable');
  if(packet.lighting_authority!==false||packet.physical_permission!==false||packet.identity_authority!==false)return invalid('authority');
  if(attempt===packet.attempt_id&&packet.sequence<=sequence)return invalid('superseded');
  if(!clock)return invalid('clock_missing');
  if(clock.attempt!==packet.attempt_id)return invalid('clock_attempt');
  if(nowMs-clock.checkedAtMs>15000)return invalid('clock_expired');
  if(finite(packet.expires_monotonic_s)&&nowMs/1000-clock.offsetLow>=packet.expires_monotonic_s)return invalid('evidence_expired');
  if(sceneKeys.some(key=>packet.scene?.[key]!==scene[key])||packet.scene?.collisionDigest!==scene.collisionDigest)return invalid('scene_binding');
  if(!packet||packet.schema!=='home-fast-tracking-1'||packet.status!=='available'
      ||packet.lighting_authority!==false||packet.physical_permission!==false||packet.identity_authority!==false
      ||packet.label!=='Approximate room placement'||packet.projection!=='unqualified_nominal_eeprom'
      ||!/^[a-f0-9]{32}$/.test(packet.attempt_id)||!/^[a-f0-9]{64}$/.test(packet.frame_key)
      ||!Number.isSafeInteger(packet.sequence)||packet.sequence<1
      ||attempt===packet.attempt_id&&packet.sequence<=sequence
      ||!packet.identity||typeof packet.identity.epoch!=='string'||packet.scene?.cameraId!=='den'
      ||scene.siteId!=='victoria'||sceneKeys.some(key=>packet.scene?.[key]!==scene[key])
      ||packet.scene.collisionDigest!==scene.collisionDigest
      ||!clock||clock.attempt!==packet.attempt_id||nowMs-clock.checkedAtMs>15000
      ||!Array.isArray(packet.source_la_monotonic_interval_s)||packet.source_la_monotonic_interval_s.length!==2
      ||!packet.source_la_monotonic_interval_s.every(finite)
      ||packet.source_la_monotonic_interval_s[0]>packet.source_la_monotonic_interval_s[1]
      ||!finite(packet.expires_monotonic_s)||packet.expires_monotonic_s>packet.source_la_monotonic_interval_s[0]+.750001
      ||nowMs/1000-clock.offsetLow>=packet.expires_monotonic_s
      ||!Array.isArray(packet.people)||packet.people.length>2)return invalid('packet_contract');
  const people=new Set();
  for(const person of packet.people){
    if(typeof person.instance_id!=='string'||people.has(person.instance_id)||!Array.isArray(person.joints)||person.joints.length>17)return invalid('person_contract');
    people.add(person.instance_id);const ids=new Set();
    for(const joint of person.joints){
      if(!Number.isSafeInteger(joint.id)||joint.id<0||joint.id>16||ids.has(joint.id)||joint.visible!==true
          ||!Array.isArray(joint.position_m)||joint.position_m.length!==3
          ||!joint.position_m.every(v=>finite(v)&&Math.abs(v)<1000))return invalid('joint_contract');
      ids.add(joint.id);
    }
  }
  return {valid:true,reason:'valid'};
}

function gpuCompletion(renderer){
  const queue=renderer.backend?.device?.queue;
  if(queue?.onSubmittedWorkDone)return queue.onSubmittedWorkDone();
  const gl=renderer.backend?.gl||renderer.getContext?.();
  if(!gl?.fenceSync)return Promise.reject(Error('tracking_gpu_completion_unavailable'));
  const fence=gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE,0);gl.flush();
  if(!fence)return Promise.reject(Error('tracking_gpu_fence_unavailable'));
  return new Promise((resolve,reject)=>{
    const start=performance.now();
    function check(){
      const result=gl.clientWaitSync(fence,0,0);
      if(result===gl.ALREADY_SIGNALED||result===gl.CONDITION_SATISFIED){gl.deleteSync(fence);resolve();}
      else if(result===gl.WAIT_FAILED||performance.now()-start>250){gl.deleteSync(fence);reject(Error('tracking_gpu_completion_timeout'));}
      else setTimeout(check,1);
    }check();
  });
}

export function attachFastTracking(engine,{isCurrent=()=>true,onStatus=()=>{},fetchImpl=fetch,documentObject=document}={}){
  const pin={...engine.sceneIdentity,collisionDigest:window.HomeContentPacks?.get?.(engine.siteId,engine.contentRevision)?.manifest?.assets?.['collision.glb']?.sha256};
  const resident=engine.apartmentRoot,renderer=engine.renderer;
  if(pin.siteId!=='victoria'||!pin.collisionDigest||!resident||!renderer)return {dispose(){},readback:()=>({active:false})};
  const root=new THREE.Group();root.name='home-fast-tracking-owned';root.userData.noLightingAuthority=true;root.userData.approximateLabel='Approximate room placement';resident.add(root);
  const bodyRenderer=createBodyFormRenderer(root);
  let controller=new AbortController();let disposed=false,clock=null,attempt=null,sequence=0,pending=null,latest=null,completedSequence=0,streamRunning=false,retryTimer=null;
  let clockInFlight=false,clockRetryTimer=null,clockFailures=0,streamId=null;
  const receipts=[],events=[],counts={};let status={active:false,status:'connecting'};
  function trace(stage,packet=null,extra={}){counts[stage]=(counts[stage]||0)+1;
    events.push({stage,at_ms:performance.now(),stream_id:streamId,attempt_id:packet?.attempt_id??attempt,sequence:packet?.sequence??null,frame_key:packet?.frame_key??null,...extra});
    if(events.length>4096)events.shift();}
  const current=()=>!disposed&&!documentObject.hidden&&isCurrent()===true&&engine.apartmentRoot===resident&&engine.renderer===renderer
      &&sceneKeys.every(key=>engine.sceneIdentity?.[key]===pin[key]);
  function inView(){
    if(!engine.camera||!root.children.length)return null;
    root.updateWorldMatrix(true,true);
    const frustum=new THREE.Frustum().setFromProjectionMatrix(new THREE.Matrix4().multiplyMatrices(engine.camera.projectionMatrix,engine.camera.matrixWorldInverse));
    return root.children.some(child=>child.visible&&frustum.intersectsObject(child));
  }
  function clear(reason='replaced'){if(pending)trace(pending.submitted?'retired_after_submit':'retired_before_submit',pending.packet,{reason,in_view:pending.submitted?null:inView()});
    bodyRenderer.clear();pending=null;latest=null;}
  function publish(value){status={...value,receipts:receipts.length};onStatus(status);}
  async function sampleClock(){
    const start=performance.now()/1000,parent=controller.signal,requestController=new AbortController();
    const abort=()=>requestController.abort();parent.addEventListener('abort',abort,{once:true});
    if(parent.aborted)abort();const deadline=setTimeout(abort,1500);
    try{
      const response=await fetchImpl('/api/perception-tracking/v1/clock',{headers:{'X-Home-Lab-Site':'victoria'},credentials:'same-origin',cache:'no-store',signal:requestController.signal});
      if(!response.ok)throw Error('tracking_clock_unavailable');const value=await response.json();const end=performance.now()/1000;
      if(!finite(value.monotonic_s)||!/^[a-f0-9]{32}$/.test(value.attempt_id)||end-start>1)throw Error('tracking_clock_invalid');
      if(!current())throw Error('tracking_clock_retired');
      clock={attempt:value.attempt_id,offsetLow:start-value.monotonic_s,offsetHigh:end-value.monotonic_s,checkedAtMs:performance.now()};
      trace('clock_sample',null,{clock_attempt:value.attempt_id,rtt_ms:(end-start)*1000,offset_low_s:clock.offsetLow,offset_high_s:clock.offsetHigh});
    }finally{clearTimeout(deadline);parent.removeEventListener('abort',abort);}
  }
  async function refreshClock(){
    if(clockInFlight||!current())return;
    clockInFlight=true;
    try{await sampleClock();clockFailures=0;clearTimeout(clockRetryTimer);clockRetryTimer=null;}
    catch(error){
      clockFailures++;const usable=clock&&performance.now()-clock.checkedAtMs<=15000;
      trace('clock_refresh_failed',null,{retained_valid_clock:!!usable,reason:error.message==='tracking_clock_invalid'?'invalid_sample':'request_failed'});
      // A transport failure does not invalidate an earlier sample inside its
      // existing 15s limit. Neither its age nor any evidence expiry is renewed.
      if(!usable){clock=null;clear('clock_unavailable');}
      if(current()&&!clockRetryTimer)clockRetryTimer=setTimeout(()=>{clockRetryTimer=null;refreshClock();},Math.min(1000,250*2**Math.min(clockFailures-1,2)));
    }finally{clockInFlight=false;}
  }
  function draw(packet){
    trace('browser_received',packet,{producer_status:packet?.status==='available'?'available':'unavailable'});
    const validity=current()?trackingPacketValidity(packet,pin,{attempt,sequence,clock}):{valid:false,reason:'view_retired'};
    if(!validity.valid){trace('browser_rejected',packet,{reason:validity.reason});clear(validity.reason);publish({active:false,status:'unavailable',reason:validity.reason});return;}
    if(attempt!==packet.attempt_id){clear();attempt=packet.attempt_id;sequence=0;completedSequence=0;receipts.length=0;}
    sequence=packet.sequence;clear();latest=packet;pending={packet,submitted:false};
    let scheduled=false;
    const observed=()=>{
      if(scheduled)return;scheduled=true;
      if(pending?.packet===packet)pending.submitted=true;
      // Runs after the renderer has returned and submitted this actual draw.
      queueMicrotask(()=>{
        const submitted=performance.now(),drawClock=clock&&{...clock};
        trace('draw_submitted',packet);
        gpuCompletion(renderer).then(()=>{
          if(!current()||!drawClock||attempt!==packet.attempt_id||completedSequence>=packet.sequence){trace('gpu_receipt_retired',packet);return;}
          const completed=performance.now();completedSequence=packet.sequence;
          const timing=trackingGpuLatency(packet.source_la_monotonic_interval_s,drawClock,completed);
          if(!timing.valid){trace('gpu_receipt_invalid_clock',packet,{reason:timing.reason});return;}
          const latency=timing.boundsMs;
          trace('gpu_completed',packet,{expired_at_completion:completed/1000-drawClock.offsetLow>=packet.expires_monotonic_s,
            timing_qualification:timing.qualification});
          receipts.push({attempt_id:attempt,sequence:packet.sequence,frame_key:packet.frame_key,submitted_ms:submitted,completed_ms:completed,
            camera_to_gpu_ms:latency,camera_to_gpu_raw_ms:timing.rawMs,camera_to_gpu_timing:timing.qualification,
            source_clock:packet.source_clock,exposure_accuracy:packet.exposure_accuracy,
            expired_at_completion:completed/1000-drawClock.offsetLow>=packet.expires_monotonic_s,
            people:packet.people.length,joints:packet.people.reduce((n,p)=>n+p.joints.length,0),bodyForm:body});
          if(receipts.length>600)receipts.shift();
          const span=receipts.length>1?(completed-receipts[0].completed_ms)/1000:0;
          const live=!!latest&&completed/1000-clock.offsetLow<latest.expires_monotonic_s;
          publish({active:live,status:live?'tracking':'expired',updateHz:span>0?(receipts.length-1)/span:null,cameraToGpuMs:latency,
            cameraToGpuRawMs:timing.rawMs,cameraToGpuTiming:timing.qualification,bodyForm:body});
        }).catch(()=>{trace('gpu_timing_failed',packet);publish({active:true,status:'gpu_timing_unavailable',bodyForm:body});});
      });
    };
    const body=bodyRenderer.update(packet,observed);
    trace('scene_applied',packet,{people:packet.people.length,joints:packet.people.reduce((n,p)=>n+p.joints.length,0),expires_monotonic_s:packet.expires_monotonic_s,
      body_form_status:body.status,body_parts:body.parts,in_view:inView()});
    if(!body.parts){trace('no_supported_body',packet);publish({active:true,status:'no_supported_body',bodyForm:body});}
  }
  async function stream(){
    if(streamRunning||!current())return;
    streamRunning=true;
    if(controller.signal.aborted)controller=new AbortController();
    try{
      await sampleClock();
      const response=await fetchImpl('/api/perception-tracking/v1/stream',{headers:{'X-Home-Lab-Site':'victoria'},credentials:'same-origin',cache:'no-store',signal:controller.signal});
      if(!response.ok||!response.headers.get('content-type')?.startsWith('text/event-stream'))throw Error('tracking_stream_unavailable');
      const id=response.headers.get('x-home-tracking-stream');streamId=/^[a-f0-9]{32}$/.test(id)?id:null;trace('stream_connected');
      const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
      while(current()){
        const {value,done}=await reader.read();if(done)break;
        buffer+=decoder.decode(value,{stream:true});if(buffer.length>65536)throw Error('tracking_stream_bound');
        let split;while((split=buffer.indexOf('\n\n'))>=0){const event=buffer.slice(0,split);buffer=buffer.slice(split+2);
          if(!event.startsWith('data: '))throw Error('tracking_event_invalid');draw(JSON.parse(event.slice(6)));}
      }
      trace('stream_disconnected');clear('stream_disconnected');publish({active:false,status:'disconnected'});
    }catch(error){if(!disposed){trace('stream_failed');clear('stream_failed');publish({active:false,status:'unavailable'});}}
    finally { streamRunning=false;if(current())retryTimer=setTimeout(()=>{if(current())stream();},1000); }
  }
  const clockTimer=setInterval(()=>refreshClock(),5000);
  const staleTimer=setInterval(()=>{if(latest&&(!current()||!clock||performance.now()-clock.checkedAtMs>15000||performance.now()/1000-clock.offsetLow>=latest.expires_monotonic_s)){trace('evidence_withdrawn',latest);clear('evidence_expired');publish({active:false,status:'expired'});}},50);
  const visibility=()=>{if(documentObject.hidden){controller.abort();clear();publish({active:false,status:'hidden'});}else if(current())stream();};
  documentObject.addEventListener?.('visibilitychange',visibility);
  const readback=()=>({...status,bodyForm:bodyRenderer.readback(),attempt,sequence,receipts:receipts.map(r=>({...r,bodyForm:{...r.bodyForm}})),diagnostics:{schema:'home-fast-tracking-trace-1',clock_domain:'browser_performance',counts:{...counts},events:events.map(e=>({...e}))}});
  engine.fastTrackingReadback=readback;stream();
  return {readback,dispose(){if(disposed)return;disposed=true;controller.abort();clearTimeout(retryTimer);clearTimeout(clockRetryTimer);clearInterval(clockTimer);clearInterval(staleTimer);documentObject.removeEventListener?.('visibilitychange',visibility);clear('disposed');bodyRenderer.dispose();root.removeFromParent();if(engine.fastTrackingReadback===readback)delete engine.fastTrackingReadback;}};
}
