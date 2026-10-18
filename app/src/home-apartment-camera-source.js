/* Demand-driven owner camera viewing, independent of Lab recordings. Raw video
 * has receipt timing only and never receives an analyzed-frame pose overlay.
 * This module uses the incumbent authenticated Lab read transport exclusively. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;
  else if(!root.HomeApartmentCameraSource)root.HomeApartmentCameraSource=api;})(typeof window!=='undefined'?window:globalThis,function(){
  'use strict';
  const copy=x=>x==null?x:JSON.parse(JSON.stringify(x)),finite=x=>typeof x==='number'&&Number.isFinite(x),
    fail=code=>{throw Object.assign(Error(code),{code});},B64=/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;
  const LIMITS=Object.freeze({jpegBytes:262144,initBytes:262144,fragmentBytes:2097152,contextBytes:32768,requestMs:4000});
  const exact=(value,keys)=>!!value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).length===keys.length&&keys.every(k=>Object.hasOwn(value,k)),
    token=value=>typeof value==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$/.test(value),
    hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value),
    instant=value=>finite(value)&&value>=0&&value<=1e12,
    stable=value=>Array.isArray(value)?'['+value.map(stable).join(',')+']':value&&typeof value==='object'?
      '{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+stable(value[k])).join(',')+'}':JSON.stringify(value);
  const FACTS=Object.freeze({people_visible:['none','one','multiple','unclear'],subject_posture:['sitting','standing','lying','walking','unclear'],
    held_object:['book','phone','cup','none','other','unclear'],book_state:['open','closed','not_visible','unclear'],
    gaze_target:['book','phone','screen','tv','person','elsewhere','unclear'],typing_visible:['yes','no','unclear'],screen_facing_subject:['yes','no','not_visible'],
    activity_hypothesis:['reading','phone_use','computer_work','resting','transition','other','unclear'],change_vs_prior:['no_change','changed','unclear','no_prior']});
  const BASIS=['person_visible','no_person_visible','multiple_people_visible','subject_partly_out_of_frame','seated_posture','standing_posture',
    'lying_or_reclined','moving_between_frames','still_between_frames','book_in_hands','book_pages_open','book_closed','phone_in_hands',
    'phone_screen_toward_face','cup_in_hands','hands_on_keyboard','hands_on_mouse','hands_resting','head_down','head_toward_monitor_side','eyes_closed','low_light_or_blur'];
  const INVALID_REASONS=['finish_not_stop','response_unbound','reply_too_long','not_json','not_object','keys_mismatch',
    'value_out_of_vocabulary','basis_invalid','change_vs_prior_mismatch'];
  const RECEIPT_KEYS=['schema','result_schema','site_id','camera_id','session_id','request_id','frame_ids','media_ids','capture_epoch','window_start',
    'observed_at','generated_at','valid_until','original_valid_until','max_age_seconds','prompt_version','context_digest','body_sha256','response_sha256',
    'engine_epoch','artifact_sha256','status','invalid_reason','facts','prior_supplied','origin','evidence_roots','text_scope','available_at','first_received_at','fingerprint'];
  function checkStructured(value,siteId,cameraId){
    const need=condition=>{if(!condition)fail('apartment_camera_invalid_structured_context');};
    need(exact(value,['schema','version','site_id','camera_id','provider','scope','latest','freshness','qualification','physical_commands','server_time'])
      &&value.schema==='home-camera-structured-context-1'&&value.version===1&&value.site_id===siteId&&value.camera_id===cameraId
      &&value.qualification===null&&value.physical_commands===false&&['current','stale','invalid','missing'].includes(value.freshness));
    const p=value.provider,s=value.scope;
    need(exact(p,['state','pump_status','last_reason','outstanding','halted'])&&token(p.state)&&token(p.pump_status)
      &&(p.last_reason===null||token(p.last_reason))&&typeof p.outstanding==='boolean'&&typeof p.halted==='boolean'
      &&exact(s,['kind','perceiver_session_id','lab_session_id'])&&s.kind==='perceiver_daily'&&s.lab_session_id===null
      &&(s.perceiver_session_id===null||typeof s.perceiver_session_id==='string'&&/^[a-f0-9]{32}$/.test(s.perceiver_session_id))
      &&(instant(value.server_time)||value.server_time===null&&['invalid','missing'].includes(value.freshness)));
    if(value.latest===null){need(s.perceiver_session_id===null&&['missing','invalid'].includes(value.freshness));return value;}
    need(exact(value.latest,['metadata','receipt']));
    const m=value.latest.metadata,r=value.latest.receipt;
    need(exact(m,['reference','frames','sent_at','profile'])&&exact(m.reference,['request_id','session_id','site_id','body_sha256','prompt_version'])
      &&exact(m.profile,['engine_epoch'])&&exact(r,RECEIPT_KEYS)&&r.schema==='qwen-observation-1'&&r.result_schema==='lab-live-scene-1'
      &&r.site_id===siteId&&r.camera_id===cameraId&&r.session_id===s.perceiver_session_id&&hash(r.request_id)
      &&r.prompt_version==='lab-live-scene-prompt-1'&&r.origin==='same_camera_visual'&&r.text_scope==='digest_only_reply_not_retained'
      &&token(r.capture_epoch)&&token(r.engine_epoch)&&typeof r.prior_supplied==='boolean'&&['valid','invalid'].includes(r.status)
      &&['body_sha256','context_digest','artifact_sha256','fingerprint'].every(k=>hash(r[k]))&&(r.response_sha256===null||hash(r.response_sha256))
      &&m.reference.request_id===r.request_id&&m.reference.session_id===r.session_id&&m.reference.site_id===siteId
      &&m.reference.body_sha256===r.body_sha256&&m.reference.prompt_version===r.prompt_version&&m.profile.engine_epoch===r.engine_epoch);
    need(Array.isArray(m.frames)&&m.frames.length>=2&&m.frames.length<=4&&Array.isArray(r.frame_ids)&&Array.isArray(r.media_ids)
      &&Array.isArray(r.evidence_roots)&&r.frame_ids.length===m.frames.length&&r.media_ids.length===m.frames.length&&r.evidence_roots.length===m.frames.length
      &&new Set(r.frame_ids).size===m.frames.length&&new Set(r.media_ids).size===m.frames.length);
    need(m.frames.every((f,i)=>exact(f,['media_id','frame_id','capture_epoch','observed_at'])&&token(f.media_id)&&token(f.frame_id)&&f.capture_epoch===r.capture_epoch
      &&instant(f.observed_at)&&(i===0||f.observed_at>m.frames[i-1].observed_at)&&f.frame_id===r.frame_ids[i]&&f.media_id===r.media_ids[i]
      &&r.evidence_roots[i]==='media:'+f.media_id)&&r.capture_epoch===m.frames.at(-1).capture_epoch
      &&r.window_start===m.frames[0].observed_at&&r.observed_at===m.frames.at(-1).observed_at&&r.observed_at-r.window_start<=6
      &&['window_start','observed_at','generated_at','available_at','first_received_at','valid_until'].every(k=>instant(r[k]))
      &&instant(m.sent_at)&&r.observed_at<=m.sent_at&&m.sent_at<=r.generated_at&&r.generated_at<=r.available_at&&r.first_received_at===r.available_at
      &&r.max_age_seconds===10&&(r.original_valid_until===null||instant(r.original_valid_until))
      &&r.valid_until===Math.min(r.observed_at+10,r.original_valid_until??Infinity));
    if(r.status==='valid')need(r.invalid_reason===null&&hash(r.response_sha256)&&exact(r.facts,[...Object.keys(FACTS),'visible_basis'])
      &&Object.entries(FACTS).every(([k,v])=>v.includes(r.facts[k]))&&Array.isArray(r.facts.visible_basis)&&r.facts.visible_basis.length<=6
      &&new Set(r.facts.visible_basis).size===r.facts.visible_basis.length&&r.facts.visible_basis.every(v=>BASIS.includes(v))
      &&(r.facts.change_vs_prior==='no_prior')===!r.prior_supplied);
    else need(r.facts===null&&INVALID_REASONS.includes(r.invalid_reason));
    if(value.freshness==='current'||value.freshness==='stale')need(r.status==='valid'&&instant(value.server_time)
      &&r.available_at<=value.server_time&&r.generated_at<=value.server_time&&r.observed_at<=value.server_time
      &&value.freshness===(value.server_time<r.valid_until?'current':'stale'));
    else need(value.freshness==='invalid');
    need(JSON.stringify(value).length<=LIMITS.contextBytes);return value;
  }
  function create({siteId='victoria',cameraId='den',transport,env=globalThis,onUpdate,onStatus}={}){
    transport=transport||env.HomePerceptionLabTransport;
    if(siteId!=='victoria'||cameraId!=='den'||typeof transport?.request!=='function')fail('apartment_camera_fixed_owner_required');
    const timers={set:(fn,ms)=>env.setTimeout(fn,ms),clear:id=>env.clearTimeout(id)},now=()=>env.performance?.now?.()??Date.now();
    let wanted=false,destroyed=false,serial=0,scheduled=null,busy=false,pending=null,abort=null,expiry=null,
      capabilities=null,nextCapabilities=0,nextMedia=0,nextContext=0,nextStructured=0,latest=null,video=null,media=null,videoRetryAt=0,mode='preview',
      structuredExpiry=null,structuredTiming=null,structuredServerTime=null;
    const structuredOriginals=new Map();
    const subscribers=new Set();
    function blank(status='idle',reason=null){return {schema:'home-apartment-camera-view-1',siteId,cameraId,status,reason,mode,
      rawCamera:true,exactAnalysis:false,recordingStarted:false,capabilities:null,preview:null,context:null,structuredContext:null,
      video:{status:'unavailable',generation:null,timestamp_semantics:'remux_receipt_only',exposure_timing:'unverified'}};}
    let state=blank();
    const snapshot=()=>{const value=copy(state);if(value.structuredContext?.latest&&structuredTiming){
      const elapsed=now()-structuredTiming.at;value.structuredContext.age_seconds=finite(structuredTiming.age)?Math.max(0,structuredTiming.age+Math.max(0,elapsed)/1000):null;
      value.structuredContext.remaining_seconds=Math.max(0,(structuredTiming.deadline-now())/1000);
      if(elapsed<0||value.structuredContext.freshness==='current'&&now()>=structuredTiming.deadline){value.structuredContext.freshness=elapsed<0?'invalid':'stale';value.structuredContext.remaining_seconds=0;}}
      return value;},selected=()=>env.HomeSpatialViewport?.getSelectedSiteId?.();
    const valid=token=>wanted&&!destroyed&&token===serial&&!env.document?.hidden&&(!selected()||selected()===siteId);
    function publish(){const value=snapshot();try{onUpdate?.(value);onStatus?.({status:state.status,reason:state.reason,mode:state.mode});}catch(_){}
      for(const fn of subscribers){try{fn(snapshot());}catch(_){}}}
    function disposeVideo(){
      if(media){media.queue.length=0;media.buffer?.removeEventListener?.('updateend',media.drain);media.buffer?.removeEventListener?.('error',media.error);
        media.source?.removeEventListener?.('sourceopen',media.open);if(media.url)env.URL?.revokeObjectURL?.(media.url);media=null;}
      if(video){try{video.pause();video.removeAttribute('src');video.load();}catch(_){}}
    }
    function clear(status,reason){timers.clear(expiry);expiry=null;timers.clear(structuredExpiry);structuredExpiry=null;structuredTiming=null;
      structuredOriginals.clear();structuredServerTime=null;disposeVideo();latest=null;capabilities=null;
      nextCapabilities=nextMedia=nextContext=nextStructured=videoRetryAt=0;state=blank(status,reason);publish();}
    function abandon(status,reason){serial++;timers.clear(scheduled);scheduled=null;abort?.abort();clear(status,reason);}
    function stop(reason='camera_view_stopped'){wanted=false;abandon('disconnected',reason);return snapshot();}
    function schedule(delay){if(!wanted||destroyed||env.document?.hidden||scheduled!==null||busy||pending)return;
      const due=capabilities?Math.min(nextMedia,nextContext,nextStructured):nextCapabilities,wait=delay??Math.max(0,due-now());
      scheduled=timers.set(()=>{scheduled=null;void turn(serial);},Math.max(0,wait));}
    async function read(operation,args,token){
      if(!valid(token))fail('apartment_camera_read_abandoned');
      const controller=new (env.AbortController||AbortController)();abort=controller;let timeout;
      const request=Promise.resolve().then(()=>transport.request(operation,{siteId,...args},{signal:controller.signal}));pending=request;
      const settled=()=>{if(pending===request){pending=null;if(wanted&&!busy)schedule();}};request.then(settled,settled);
      try{const value=await Promise.race([request,new Promise((_,reject)=>{timeout=timers.set(()=>{
        controller.abort();reject(Object.assign(Error('apartment_camera_read_timeout'),{code:'apartment_camera_read_timeout'}));},LIMITS.requestMs);})]);
        if(!valid(token))fail('apartment_camera_read_abandoned');return value;
      }finally{timers.clear(timeout);if(abort===controller)abort=null;}
    }
    function decoded(value,maximum){
      if(typeof value!=='string'||!value||value.length>Math.ceil(maximum/3)*4||!B64.test(value))fail('apartment_camera_invalid_media');
      let raw;try{raw=env.atob(value);}catch(_){fail('apartment_camera_invalid_media');}
      if(!raw.length||raw.length>maximum)fail('apartment_camera_invalid_media');return Uint8Array.from(raw,c=>c.charCodeAt(0));
    }
    function checkCapabilities(value){
      if(!value||typeof value!=='object'||Array.isArray(value)||value.site_id!==undefined&&value.site_id!==siteId)fail('apartment_camera_scope_mismatch');
      const cameras=value.cameras;if(cameras!==undefined&&(!Array.isArray(cameras)||cameras.length>64||!cameras.some(c=>c?.id===cameraId||c?.id===undefined&&c?.camera_id===cameraId)))fail('apartment_camera_not_advertised');
      return {video:value.live_videos?.[cameraId]===true,preview:!!value.live_previews?.[cameraId],context:true};
    }
    function expireAfter(ms,token){timers.clear(expiry);expiry=timers.set(()=>{expiry=null;if(!valid(token))return;
      disposeVideo();latest=null;state.preview=null;state.status='unavailable';state.reason='apartment_camera_media_stale';state.video.status='stale';publish();},ms);}
    function adoptPreview(value,started,token){
      if(value?.version!==1||value.site_id!==siteId||value.camera_id!==cameraId||value.width!==640||value.height!==360
        ||value.content_type!=='image/jpeg'||value.recorded!==false)fail('apartment_camera_invalid_preview');
      const receipt=value.timestamp_semantics==='decode_receipt_only',age=receipt?value.decoded_age_seconds:value.source_age_seconds,
        uncertainty=receipt?0:value.uncertainty_seconds;
      if(!finite(age)||age<0||!finite(uncertainty)||uncertainty<0||uncertainty>1)fail('apartment_camera_invalid_preview');
      const remaining=3000-(age+uncertainty)*1000-(now()-started);
      if(!finite(remaining)||remaining<=0)fail('apartment_camera_preview_stale');
      const bytes=decoded(value.jpeg_base64,LIMITS.jpegBytes);
      if(bytes.length<4||bytes[0]!==255||bytes[1]!==216||bytes.at(-2)!==255||bytes.at(-1)!==217)fail('apartment_camera_invalid_preview');
      disposeVideo();latest={bytes,type:'image/jpeg',descriptor:{...copy(value),timestamp_semantics:receipt?'decode_receipt_only':'source_reported_exposure'}};
      state.preview={...latest.descriptor};state.status='live';state.reason=null;state.mode='preview';
      state.video.status=capabilities?.video?'fallback':'not_advertised';expireAfter(remaining,token);
    }
    function videoFailure(){if(!media)return;media.failed=true;disposeVideo();videoRetryAt=now()+5000;state.video.status='recovering';state.status='unavailable';
      state.reason='apartment_camera_video_decode_failed';publish();}
    function startVideo(value,token){
      disposeVideo();const source=new env.MediaSource(),m={generation:value.generation,cursor:undefined,source,buffer:null,queue:[],failed:false,url:null,open:null,error:videoFailure,drain:null};
      media=m;m.url=env.URL.createObjectURL(source);video.src=m.url;
      m.drain=()=>{
        if(!valid(token)||media!==m||m.failed||!m.buffer||m.buffer.updating)return;
        try{const ranges=m.buffer.buffered;
          if(ranges?.length){const end=ranges.end(ranges.length-1),begin=ranges.start(0);
            if(video.currentTime<begin||end-video.currentTime>2.5||video.currentTime===0)video.currentTime=Math.max(begin,end-1.25);
            if(video.currentTime-begin>5){m.buffer.remove(begin,video.currentTime-3);return;}}
          if(m.queue.length)m.buffer.appendBuffer(m.queue.shift());
          Promise.resolve(video.play()).catch(()=>{});
        }catch(_){if(media===m)videoFailure();}
      };
      m.open=()=>{if(!valid(token)||media!==m)return;try{m.buffer=source.addSourceBuffer(value.codec_mime);
        m.buffer.addEventListener('updateend',m.drain);m.buffer.addEventListener('error',m.error);m.drain();}catch(_){videoFailure();}};
      source.addEventListener('sourceopen',m.open,{once:true});m.queue.push(decoded(value.init_base64,LIMITS.initBytes));return m;
    }
    function adoptVideo(value,started,token){
      if(value?.version!==1||value.site_id!==siteId||value.camera_id!==cameraId||value.recorded!==false
        ||!/^[a-f0-9]{32}$/.test(value.generation||'')||!/^video\/mp4; codecs="avc1\.[0-9a-fA-F]{6}"$/.test(value.codec_mime||'')
        ||!env.MediaSource?.isTypeSupported?.(value.codec_mime)||value.timestamp_semantics!=='remux_receipt_only'
        ||value.exposure_timing!=='unverified'||!finite(value.receipt_age_seconds)||value.receipt_age_seconds<0||value.receipt_age_seconds>=4
        ||!Array.isArray(value.fragments)||value.fragments.length>3)fail('apartment_camera_invalid_video');
      const remaining=(4-value.receipt_age_seconds)*1000-(now()-started);
      if(!finite(remaining)||remaining<=0)fail('apartment_camera_video_stale');
      const fresh=!media||media.failed||value.reset===true||media.generation!==value.generation;
      if(fresh&&(!value.init_base64||value.fragments.length===0))fail('apartment_camera_video_initialization_missing');
      let m=fresh?startVideo(value,token):media;
      for(const fragment of value.fragments){if(!Number.isSafeInteger(fragment.sequence)||fragment.sequence<1||m.cursor!==undefined&&fragment.sequence!==m.cursor+1)fail('apartment_camera_video_gap');
        m.queue.push(decoded(fragment.data_base64,LIMITS.fragmentBytes));m.cursor=fragment.sequence;}
      if(m.queue.length>6)fail('apartment_camera_video_backpressure');
      latest=null;state.preview=null;state.status='live';state.reason=null;state.mode='video';
      state.video={status:'receiving',generation:m.generation,timestamp_semantics:'remux_receipt_only',exposure_timing:'unverified',receipt_age_seconds:value.receipt_age_seconds};
      expireAfter(remaining,token);m.drain();
    }
    function adoptContext(value){
      if(!value||typeof value!=='object'||Array.isArray(value)||value.site_id!==siteId||value.camera_id!==cameraId
        ||typeof value.status!=='string'||value.status.length>128)fail('apartment_camera_invalid_context');
      const raw=JSON.stringify(value);if(raw.length>LIMITS.contextBytes)fail('apartment_camera_context_limit');
      state.context=copy(value);
    }
    function adoptStructured(value,started,token){
      checkStructured(value,siteId,cameraId);const elapsed=now()-started;
      if(!finite(elapsed)||elapsed<0||instant(value.server_time)&&structuredServerTime!==null&&value.server_time<structuredServerTime)fail('apartment_camera_structured_clock_changed');
      if(instant(value.server_time))structuredServerTime=value.server_time;
      let deadline=now(),age=0;
      if(value.latest){const r=value.latest.receipt,key=r.session_id+':'+r.request_id,body=stable(value.latest),old=structuredOriginals.get(key);
        if(old&&old.body!==body)fail('apartment_camera_structured_original_changed');
        deadline=value.freshness==='current'?now()+(r.valid_until-value.server_time)*1000-elapsed:now();
        if(old)deadline=Math.min(old.deadline,deadline);
        age=instant(value.server_time)?Math.max(0,value.server_time-r.observed_at+elapsed/1000):null;
        if(old&&finite(old.age))age=finite(age)?Math.max(age,old.age+Math.max(0,now()-old.at)/1000):null;
        structuredOriginals.set(key,{body,deadline,age,at:now()});if(structuredOriginals.size>64)structuredOriginals.delete(structuredOriginals.keys().next().value);
      }
      timers.clear(structuredExpiry);structuredExpiry=null;state.structuredContext=copy(value);
      structuredTiming=value.latest?{at:now(),age,deadline}:null;
      if(value.freshness==='current'&&deadline<=now())state.structuredContext.freshness='stale';
      if(state.structuredContext.freshness==='current')structuredExpiry=timers.set(()=>{structuredExpiry=null;if(!valid(token))return;
        state.structuredContext.freshness='stale';publish();},deadline-now());
    }
    function videoUsable(){return capabilities?.video&&!!video&&!!env.MediaSource&&!!env.URL?.createObjectURL&&now()>=videoRetryAt;}
    async function turn(token){
      if(!valid(token)||busy||pending)return;busy=true;let operation;
      try{
        const time=now();
        if(!capabilities){operation='capabilities.get';nextCapabilities=time+5000;capabilities=checkCapabilities(await read(operation,{},token));
          state.capabilities=copy(capabilities);nextMedia=nextContext=nextStructured=0;}
        else if(nextMedia<=time&&nextMedia<=nextContext&&nextMedia<=nextStructured){
          const useVideo=videoUsable()&&(!media?.failed||!capabilities.preview);operation=useVideo?'cameras.video':'cameras.preview';nextMedia=time+(useVideo?250:1000);
          if(!useVideo&&!capabilities.preview){nextMedia=time+5000;state.status='unavailable';state.reason='apartment_camera_not_advertised';}
          else{const started=now(),value=await read(operation,{cameraId,...(useVideo&&media?{afterSequence:media.cursor,videoGeneration:media.generation}:{})},token);
            if(useVideo)adoptVideo(value,started,token);else adoptPreview(value,started,token);}
        }else if(nextStructured<=time&&nextStructured<nextContext){operation='cameras.structuredContext';nextStructured=time+1000;
          const started=now();adoptStructured(await read(operation,{cameraId},token),started,token);
        }else if(nextContext<=time){operation='cameras.context';nextContext=time+3000;adoptContext(await read(operation,{cameraId},token));}
        if(valid(token))publish();
      }catch(error){if(valid(token)){
        if(error?.status===401||error?.status===403){wanted=false;abandon('refused','apartment_camera_owner_auth_required');}
        else{const reason=error?.code||'apartment_camera_unavailable';
          if(operation==='cameras.context')state.context={status:'unavailable',site_id:siteId,camera_id:cameraId,reason};
          else if(operation==='cameras.structuredContext'){timers.clear(structuredExpiry);structuredExpiry=null;structuredTiming=null;
            state.structuredContext={freshness:'invalid',reason,latest:null};}
          else{state.status='unavailable';state.reason=reason;latest=null;state.preview=null;timers.clear(expiry);expiry=null;
            if(operation==='cameras.video'){disposeVideo();videoRetryAt=now()+5000;state.video.status='recovering';if(capabilities?.preview){mode='preview';nextMedia=now()+1000;}}}
          publish();}
      }}finally{busy=false;if(valid(serial))schedule();}
    }
    function start(){if(destroyed)fail('apartment_camera_source_destroyed');if(wanted)return snapshot();wanted=true;serial++;
      if(selected()&&selected()!==siteId){stop('selected_site_changed');return snapshot();}
      clear(env.document?.hidden?'paused':'connecting',null);if(!env.document?.hidden&&!pending)void turn(serial);return snapshot();}
    function attachVideo(element){if(destroyed)fail('apartment_camera_source_destroyed');
      if(element!==null&&(!element||typeof element.addEventListener!=='function'||typeof element.removeAttribute!=='function'))fail('apartment_camera_video_element_required');
      video?.removeEventListener('error',videoFailure);disposeVideo();video=element;video?.addEventListener('error',videoFailure);nextMedia=0;
      if(wanted)schedule(0);return()=>{if(video===element){video?.removeEventListener('error',videoFailure);disposeVideo();video=null;nextMedia=0;if(wanted)schedule(0);}};}
    function subscribe(fn,{signal}={}){if(typeof fn!=='function')fail('apartment_camera_callback_required');if(signal?.aborted)return()=>{};
      subscribers.add(fn);fn(snapshot());const close=()=>{subscribers.delete(fn);signal?.removeEventListener('abort',close);};signal?.addEventListener('abort',close,{once:true});return close;}
    function readFrame(){return latest?{bytes:new Uint8Array(latest.bytes),type:latest.type,descriptor:copy(latest.descriptor)}:null;}
    const visibility=()=>{if(env.document?.hidden){if(wanted)abandon('paused','page_hidden');}else if(wanted){serial++;if(!pending)void turn(serial);}},
      pagehide=()=>stop('page_hidden'),logout=()=>stop('owner_logout'),click=e=>{if(e.target?.closest?.('#logout'))logout();},siteChanged=()=>{if(selected()&&selected()!==siteId)stop('selected_site_changed');};
    env.document?.addEventListener?.('visibilitychange',visibility);env.document?.addEventListener?.('click',click,true);
    env.addEventListener?.('pagehide',pagehide);env.addEventListener?.('home:logout',logout);const unsubscribe=env.HomeSpatialViewport?.subscribe?.(siteChanged);
    function destroy(){if(destroyed)return;stop('camera_view_unmounted');destroyed=true;video?.removeEventListener('error',videoFailure);video=null;subscribers.clear();
      env.document?.removeEventListener?.('visibilitychange',visibility);env.document?.removeEventListener?.('click',click,true);
      env.removeEventListener?.('pagehide',pagehide);env.removeEventListener?.('home:logout',logout);unsubscribe?.();}
    return Object.freeze({start,stop,destroy,snapshot,subscribe,attachVideo,readFrame});
  }
  return Object.freeze({create,LIMITS});
});
