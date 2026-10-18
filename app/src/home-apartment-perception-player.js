/* Exact-frame decoded presentation only. Source owns authenticated reads and polling. */
(function(root,factory){
  const api=factory(root,()=>typeof module==='object'&&module.exports
    ?require('./home-apartment-perception-view.js'):root.HomeApartmentPerceptionView);
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.HomeApartmentPerceptionPlayer=api;
})(typeof window!=='undefined'?window:globalThis,function(root,getView){
  'use strict';
  const copy=x=>x==null?null:JSON.parse(JSON.stringify(x));
  const abortError=()=>Object.assign(Error('frame_decode_aborted'),{name:'AbortError'});
  const frameBytes=value=>value&&['image/jpeg','image/png'].includes(value.type)
    &&ArrayBuffer.isView(value.bytes)&&value.bytes.BYTES_PER_ELEMENT===1
    &&value.bytes.byteLength>0&&value.bytes.byteLength<=32*1024*1024;

  // Keep native ESM import outside JSX: the production env preset rewrites
  // import() there to CommonJS require, which the ordinary browser does not own.
  function loadLayer(){
    const url=new (root.URL||URL)('home-3d/apartment-perception-layer.mjs',root.document.baseURI);
    const suffix=root.__HOME_BOOT_ASSET_SUFFIX||(root.__HOME_ASSET_VERSION
      ?'?v='+encodeURIComponent(root.__HOME_ASSET_VERSION):'');
    return import(url.href+suffix);
  }

  function browserDecode(value,{signal,frame,env}){
    if(!frameBytes(value))return Promise.reject(Error('frame_media_invalid'));
    if(!env?.Blob||!env?.Image||typeof env.URL?.createObjectURL!=='function'
      ||typeof env.URL?.revokeObjectURL!=='function')return Promise.reject(Error('frame_decoder_unavailable'));
    if(signal.aborted)return Promise.reject(abortError());
    return new Promise((resolve,reject)=>{
      let url=null,image=null,finished=false,released=false;
      const dispose=()=>{
        if(released)return;released=true;
        if(image){image.onload=null;image.onerror=null;image.src='';}
        if(url!==null)env.URL.revokeObjectURL(url);
      };
      const cleanup=()=>signal.removeEventListener('abort',cancel);
      const fail=error=>{if(finished)return;finished=true;cleanup();dispose();reject(error);};
      const cancel=()=>fail(abortError());
      const ready=()=>{
        if(finished)return;
        if(signal.aborted)return cancel();
        const width=image.naturalWidth,height=image.naturalHeight;
        if(!(width>0&&height>0)||frame.width!==undefined&&frame.width!==width
          ||frame.height!==undefined&&frame.height!==height)return fail(Error('frame_dimensions_mismatch'));
        finished=true;cleanup();
        resolve({url,width,height,dispose});
      };
      try{
        url=env.URL.createObjectURL(new env.Blob([value.bytes],{type:value.type}));
        image=new env.Image();
        signal.addEventListener('abort',cancel,{once:true});
        image.onerror=()=>fail(Error('frame_decode_failed'));
        if(typeof image.decode!=='function')image.onload=ready;
        image.src=url;
        if(typeof image.decode==='function')Promise.resolve(image.decode()).then(ready,()=>fail(Error('frame_decode_failed')));
      }catch(_){fail(Error('frame_decode_failed'));}
    });
  }

  function create({source,scope,onChange,env=root,decode=browserDecode,collisionDigest=null}={}){
    const View=env.HomeApartmentPerceptionView||getView();
    if(!View||typeof View.project!=='function'||typeof View.frameKey!=='function'
      ||typeof View.scopeMatches!=='function'||!View.scopeMatches(scope,scope)
      ||typeof source?.subscribe!=='function'||typeof source?.sampleClock!=='function'
      ||typeof source?.readFrame!=='function'||typeof decode!=='function')throw Error('perception_player_inputs_invalid');
    const originalScope=scope,fixedScope=copy(scope);
    const Abort=env.AbortController||root.AbortController;
    if(typeof Abort!=='function'||typeof env.setInterval!=='function'||typeof env.clearInterval!=='function')throw Error('perception_player_environment_invalid');
    let disposed=false,wanted=false,serial=0,sourceAbort=null,unsubscribe=null,timer=null;
    let pending=null,displayed=null,latest=null,epochKnown=false,sourceEpoch=null;
    let connection='idle',reason=null,mediaReason=null,view=null;
    let diagnostic=null,diagnosticTimer=null,diagnosticSourceStopped=false;
    let approximate=null,approximateTimer=null,approximateSourceStopped=false;
    const monotonic=()=>{const value=env.performance?.now?.();return Number.isFinite(value)?value:null;};
    const clearDiagnosticTimer=()=>{if(diagnosticTimer!==null)(env.clearTimeout||root.clearTimeout)(diagnosticTimer);diagnosticTimer=null;};
    const clearApproximateTimer=()=>{if(approximateTimer!==null)(env.clearTimeout||root.clearTimeout)(approximateTimer);approximateTimer=null;};
    function approximateReceipt(){
      const now=monotonic();
      return approximate&&!approximate.invalid?{eventKey:approximate.key,remainingHoldS:!approximateSourceStopped&&connection==='live'&&now!==null
        ?Math.max(0,(approximate.deadline-now)/1000):0}:null;
    }
    function adoptApproximate(envelope){
      const packet=envelope.approximate_room_pose,key=View.approximateEventKey?.(packet),pose=packet?.approximate_pose;
      if(!key)return;
      const event=pose.provenance.source_event_id,epoch=pose.source_epoch,now=monotonic();
      const fingerprint=JSON.stringify({pose:packet.approximate_pose,frame:packet.frame,status:packet.status,label:packet.label});
      const elapsed=envelope.approximate_receipt_elapsed_s,timing=packet.timing;
      const validReceipt=now!==null&&Number.isFinite(elapsed)&&elapsed>=0&&Number.isFinite(timing?.remaining_receipt_hold_s)
        &&timing.remaining_receipt_hold_s>0&&timing.remaining_receipt_hold_s<=2&&Number.isFinite(timing.receipt_age_s)
        &&timing.receipt_age_s>=0&&timing.receipt_age_s+timing.remaining_receipt_hold_s<=2.000001;
      if(approximate&&approximate.epoch===epoch&&(event<approximate.event||event===approximate.event
        &&(approximate.key!==key||approximate.fingerprint!==fingerprint||approximate.invalid))){
        approximate.invalid=true;approximate.deadline=0;clearApproximateTimer();return;
      }
      if(!validReceipt){
        if(approximate&&approximate.epoch===epoch&&approximate.event===event){approximate.invalid=true;approximate.deadline=0;}
        return;
      }
      const deadline=now+Math.max(0,timing.remaining_receipt_hold_s-elapsed)*1000;
      if(approximate&&approximate.key===key)approximate.deadline=Math.min(approximate.deadline,deadline);
      else approximate={key,event,epoch,fingerprint,deadline,invalid:false};
      clearApproximateTimer();
      const token=serial;
      approximateTimer=(env.setTimeout||root.setTimeout)(()=>{approximateTimer=null;
        if(active(token)&&checkIdentity()){project();notify();}},Math.max(0,approximate.deadline-now));
    }
    function diagnosticReceipt(){
      const now=monotonic();
      return diagnostic&&!diagnostic.invalid?{eventKey:diagnostic.key,remainingHoldS:!diagnosticSourceStopped&&connection==='live'&&now!==null
        ?Math.max(0,(diagnostic.deadline-now)/1000):0}:null;
    }
    function adoptDiagnostic(envelope){
      const packet=envelope.diagnostic_room_pose,key=View.diagnosticEventKey?.(packet),pose=packet?.diagnostic_pose;
      if(!key)return;
      const event=pose.provenance.source_event_id,epoch=pose.source_epoch;
      const fingerprint=JSON.stringify({pose:packet.diagnostic_pose,frame:packet.frame,status:packet.status});
      const now=monotonic(),elapsed=envelope.diagnostic_receipt_elapsed_s,timing=packet.timing,recorded=packet.status==='recorded';
      const validReceipt=recorded||now!==null&&Number.isFinite(elapsed)&&elapsed>=0&&Number.isFinite(timing?.remaining_receipt_hold_s)
        &&timing.remaining_receipt_hold_s>0&&timing.remaining_receipt_hold_s<=2
        &&Number.isFinite(timing.receipt_age_s)&&timing.receipt_age_s>=0&&timing.receipt_age_s+timing.remaining_receipt_hold_s<=2.000001;
      if(diagnostic&&diagnostic.epoch===epoch&&(event<diagnostic.event||event===diagnostic.event
        &&(diagnostic.key!==key||diagnostic.fingerprint!==fingerprint||diagnostic.invalid))){
        diagnostic.invalid=true;diagnostic.deadline=0;clearDiagnosticTimer();return;
      }
      if(!validReceipt){
        if(diagnostic&&diagnostic.epoch===epoch&&diagnostic.event===event){diagnostic.invalid=true;diagnostic.deadline=0;}
        return;
      }
      const deadline=recorded?Infinity:now+Math.max(0,timing.remaining_receipt_hold_s-elapsed)*1000;
      if(diagnostic&&diagnostic.key===key)diagnostic.deadline=Math.min(diagnostic.deadline,deadline);
      else diagnostic={key,event,epoch,fingerprint,deadline,invalid:false};
      clearDiagnosticTimer();
      if(!recorded&&Number.isFinite(diagnostic.deadline)){
        const token=serial;
        diagnosticTimer=(env.setTimeout||root.setTimeout)(()=>{diagnosticTimer=null;
          if(active(token)&&checkIdentity()){project();notify();}},Math.max(0,diagnostic.deadline-now));
      }
    }
    let environmentCleanup=[];
    function snapshot(){return {connection,reason:reason||mediaReason,mediaReason,view:copy(view),imageUrl:displayed?.owned.url||null,
      frame:copy(displayed?.frame||null),loading:pending!==null,
      heldFrame:!!(pending&&displayed),disposed};}
    function notify(){const value=snapshot();try{onChange?.(value);}catch(_){/* Presentation cannot acquire source authority. */}return value;}
    function release(value){try{value?.dispose?.();}catch(_){/* Continue releasing the remaining private state. */}}
    function abandonRead(){const job=pending;pending=null;job?.abort.abort();}
    function clearImage(){abandonRead();release(displayed?.owned);displayed=null;}
    function currentIdentity(){
      if(!View.scopeMatches(fixedScope,originalScope))return false;
      const selected=env.HomeSpatialViewport?.getSelectedSiteId?.();
      if(selected&&selected!==fixedScope.siteId)return false;
      const identity=env.__APT_ENGINE?.sceneIdentity;
      return !identity||['siteId','contentRevision','frameDigest','registrationVersion']
        .every(key=>identity[key]===undefined||identity[key]===fixedScope[key]);
    }
    function active(token){return wanted&&!disposed&&token===serial&&!sourceAbort?.signal.aborted;}
    function clock(){try{return source.sampleClock();}catch(_){return null;}}
    function poseFor(frame,envelope){
      const paired=View.project({envelope,scope:fixedScope,clock:null,
        displayedFrame:{descriptor:frame,scope:fixedScope,key:View.frameKey(frame,fixedScope)}});
      return paired.pose2d.renderable?copy(envelope.pose2d):null;
    }
    function project(){
      if(!latest){view=null;return;}
      const envelope=displayed?{...latest,pose2d:displayed.pose}:latest;
      const safeEnvelope=connection==='stopped'?{...envelope,session_state:'stopped'}:envelope;
      view=View.project({envelope:safeEnvelope,scope:fixedScope,clock:clock(),collisionDigest,diagnosticReceipt:diagnosticReceipt(),approximateReceipt:approximateReceipt(),displayedFrame:displayed
        ?{descriptor:displayed.frame,scope:fixedScope,key:displayed.key}:null});
      if(diagnostic?.invalid){view.diagnosticRoomPose={...view.diagnosticRoomPose,renderable:false,status:'refused',reason:'diagnostic_original_event_changed',joints:[],edges:[],anchor:null};}
      if(diagnosticSourceStopped&&safeEnvelope.diagnostic_room_pose?.status!=='recorded'){
        view.diagnosticRoomPose={...view.diagnosticRoomPose,renderable:false,status:'unavailable',reason:'diagnostic_source_stopped',joints:[],edges:[],anchor:null};
      }
      if(approximate?.invalid){view.approximateRoomPose={...view.approximateRoomPose,renderable:false,status:'refused',reason:'approximate_original_event_changed',joints:[],edges:[],anchor:null};}
      if(approximateSourceStopped){view.approximateRoomPose={...view.approximateRoomPose,renderable:false,status:'unavailable',reason:'approximate_source_stopped',joints:[],edges:[],anchor:null};}
      if(connection!=='live'){
        view.roomPose={...view.roomPose,admitted:false,status:'unavailable',reason:'source_not_live',joints:[],edges:[],anchor:null};
        view.follow={allowed:false,anchor:null,reason:'source_not_live'};
      }
    }
    function detach(){
      sourceAbort?.abort();sourceAbort=null;
      const off=unsubscribe;unsubscribe=null;try{off?.();}catch(_){}
      if(timer!==null){env.clearInterval(timer);timer=null;}
      clearDiagnosticTimer();
      clearApproximateTimer();
      for(const cleanup of environmentCleanup.splice(0))try{cleanup();}catch(_){}
    }
    function stop(nextReason='viewer_disconnected'){
      wanted=false;serial++;detach();clearImage();latest=null;view=null;epochKnown=false;sourceEpoch=null;diagnostic=null;diagnosticSourceStopped=false;
      approximate=null;approximateSourceStopped=false;
      connection='disconnected';reason=nextReason;mediaReason=null;return notify();
    }
    function unavailable(nextConnection,nextReason){
      stop(nextReason);connection=nextConnection;reason=nextReason;return notify();
    }
    function checkIdentity(){
      if(!currentIdentity()){stop('viewer_identity_changed');return false;}
      if(env.document?.hidden){stop('page_hidden');return false;}
      return true;
    }
    function tick(){if(!wanted||disposed||!checkIdentity())return;project();notify();}
    function startRead(frame,key){
      const job={abort:new Abort(),token:serial,key,frame:copy(frame)};
      const current=()=>active(job.token)&&pending===job&&!job.abort.signal.aborted&&checkIdentity();
      pending=job;reason=displayed?'new_frame_loading':'frame_loading';project();notify();
      Promise.resolve().then(()=>{
        if(!current())throw abortError();
        return source.readFrame(job.frame,{scope:copy(fixedScope),signal:job.abort.signal});
      }).then(value=>{
        if(!current())throw abortError();
        if(!frameBytes(value))throw Error('frame_media_invalid');
        if(typeof value.frame_metadata?.media_sha256==='string'&&/^[a-f0-9]{64}$/.test(value.frame_metadata.media_sha256)){
          job.frame.media_sha256=value.frame_metadata.media_sha256;
          job.verifiedMediaSha256=value.frame_metadata.media_sha256;
        }
        return decode(value,{scope:copy(fixedScope),frame:copy(job.frame),signal:job.abort.signal,env});
      }).then(owned=>{
        if(!current()){release(owned);return;}
        if(typeof owned?.url!=='string'||!owned.url.startsWith('blob:')||typeof owned.dispose!=='function'){
          release(owned);throw Error('frame_decode_result_invalid');
        }
        if(owned.width!==undefined&&owned.width!==job.frame.width||owned.height!==undefined&&owned.height!==job.frame.height){
          release(owned);throw Error('frame_dimensions_mismatch');
        }
        const preceding=displayed;
        displayed={owned,frame:job.frame,key:job.key,pose:poseFor(job.frame,latest),verifiedMediaSha256:job.verifiedMediaSha256||job.frame.media_sha256||null};
        pending=null;reason=null;mediaReason=null;project();notify();release(preceding?.owned);
      }).catch(error=>{
        if(!active(job.token)||pending!==job||job.abort.signal.aborted)return;
        if([401,403].includes(error?.status)||[401,403].includes(error?.statusCode)
          ||['den_lab_frame_descriptor_mismatch','apartment_perception_current_scope_required'].includes(error?.code)){
          unavailable('refused',error?.code||'frame_access_refused');return;
        }
        // A missing/late image is independent from model/JEV availability.
        // Keep the subscription and original metadata so a later poll can
        // retry this exact descriptor or replace it with a newer frame.
        pending=null;
        reason=null;mediaReason=error?.message==='frame_dimensions_mismatch'?'frame_dimensions_mismatch'
          :typeof error?.code==='string'?error.code.slice(0,160):'frame_read_or_decode_failed';
        project();notify();
      });
    }
    function update(envelope,token,preserveConnection=false){
      if(!active(token))return;
      if(!checkIdentity())return;
      if(envelope?.schema!=='home-apartment-perception-1'||!View.scopeMatches(envelope.scope,fixedScope)){
        unavailable('refused','source_scope_mismatch');return;
      }
      const nextEpoch=envelope.source_epoch??null;
      if(nextEpoch!==null&&(typeof nextEpoch!=='string'||nextEpoch.length>256)){
        unavailable('refused','source_epoch_invalid');return;
      }
      if(epochKnown&&nextEpoch!==sourceEpoch)clearImage();
      sourceEpoch=nextEpoch;epochKnown=true;
      try{latest=copy(envelope);}catch(_){unavailable('refused','source_envelope_invalid');return;}
      if(['stopped','stopping','blocked','interrupted','indeterminate'].includes(envelope.session_state))diagnosticSourceStopped=true;
      if(['stopped','stopping','blocked','interrupted','indeterminate'].includes(envelope.session_state))approximateSourceStopped=true;
      adoptDiagnostic(latest);
      adoptApproximate(latest);
      if(envelope.session_state==='failed'){unavailable('unavailable','source_session_failed');return;}
      if(!preserveConnection)connection=['recording','live'].includes(envelope.session_state)?'live':envelope.session_state==='stopped'?'stopped':'connecting';
      const candidate=View.project({envelope:latest,scope:fixedScope,clock:clock(),displayedFrame:null});
      const frame=candidate.frame?.descriptor,key=candidate.frame?.key;
      if(!key){clearImage();reason='frame_unavailable';project();notify();return;}
      if(displayed?.key===key&&displayed.frame.width===frame.width&&displayed.frame.height===frame.height){
        abandonRead();displayed.frame=copy(frame);
        if(displayed.verifiedMediaSha256)displayed.frame.media_sha256=displayed.verifiedMediaSha256;
        displayed.pose=poseFor(frame,latest);reason=null;project();notify();return;
      }
      if(displayed&&displayed.key!==key){
        const nextPose=poseFor(displayed.frame,latest);
        if(nextPose)displayed.pose=nextPose;
      }
      if(pending?.key===key){project();notify();return;}
      abandonRead();startRead(frame,key);
    }
    function status(value,token){
      if(!active(token))return;
      if(!checkIdentity())return;
      if(['unavailable','refused','disconnected'].includes(value?.status)){
        if(value.status==='unavailable'&&value.recoverable===true){
          connection='unavailable';reason=typeof value.reason==='string'?value.reason.slice(0,300):'source_unavailable';
          project();notify();return;
        }
        unavailable(value.status,typeof value.reason==='string'?value.reason.slice(0,300):'source_'+value.status);return;
      }
      if(['connecting','live','stopped'].includes(value?.status)){
        if(value.status==='stopped'){diagnosticSourceStopped=true;approximateSourceStopped=true;}
        connection=value.status;reason=typeof value.reason==='string'?value.reason.slice(0,300):null;project();notify();
      }
    }
    function listen(target,event,handler,options){
      if(!target?.addEventListener)return;
      target.addEventListener(event,handler,options);
      environmentCleanup.push(()=>target.removeEventListener(event,handler,options));
    }
    function start(){
      if(disposed)throw Error('perception_player_destroyed');
      if(wanted)return snapshot();
      wanted=true;serial++;connection='connecting';reason=null;sourceAbort=new Abort();
      if(!checkIdentity())return snapshot();
      const token=serial;
      listen(env,'pagehide',()=>stop('page_hidden'));
      listen(env,'home:logout',()=>stop('owner_logout'));
      listen(env.document,'visibilitychange',()=>{if(env.document.hidden)stop('page_hidden');});
      listen(env.document,'click',event=>{if(event.target?.closest?.('#logout'))stop('owner_logout');},true);
      const registryOff=env.HomeSpatialViewport?.subscribe?.(()=>{if(wanted)checkIdentity();});
      if(typeof registryOff==='function')environmentCleanup.push(registryOff);
      timer=env.setInterval(tick,250);notify();
      try{
        const result=source.subscribe({scope:copy(fixedScope),signal:sourceAbort.signal,
          onUpdate:value=>update(value,token),onStatus:value=>status(value,token)});
        Promise.resolve(result).then(off=>{
          if(typeof off!=='function'){if(active(token))unavailable('unavailable','source_subscription_invalid');return;}
          if(!active(token)){try{off();}catch(_){}return;}
          unsubscribe=off;
        },()=>{if(active(token))unavailable('unavailable','source_subscription_failed');});
      }catch(_){if(active(token))unavailable('unavailable','source_subscription_failed');}
      return snapshot();
    }
    function refresh(){
      if(disposed)return snapshot();
      if(!wanted)return start();
      if(!checkIdentity())return snapshot();
      if(latest)update(latest,serial,true);else tick();
      return snapshot();
    }
    function destroy(){if(disposed)return snapshot();stop('viewer_unmounted');disposed=true;return notify();}
    return Object.freeze({start,stop,destroy,snapshot,refresh});
  }
  return Object.freeze({create,loadLayer});
});
