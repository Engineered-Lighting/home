/* Exact-session viewing plus an explicit, bounded owner analysis action.
 * Session ownership is private app-lifetime RAM, never browser storage. */
(function(root){
  'use strict';
  if(root.HomeApartmentPerceptionSource)return;
  const KEYS=['siteId','contentRevision','frameDigest','registrationVersion','cameraId','sessionId','generation'],ID=/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
  const copy=x=>JSON.parse(JSON.stringify(x)),fail=code=>{throw Object.assign(Error(code),{code});};
  let active=null,epoch=0,listPending=null;
  const loopReaders=new Set();
  function scope(value){
    if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).length!==KEYS.length||!KEYS.every(k=>Object.hasOwn(value,k))
      ||value.siteId!=='victoria'||value.cameraId!=='den'||!ID.test(value.sessionId||'')||!Number.isSafeInteger(value.generation)||value.generation<0
      ||typeof value.contentRevision!=='string'||!value.contentRevision||!/^[a-f0-9]{64}$/.test(value.frameDigest||'')
      ||!(value.registrationVersion===null||Number.isSafeInteger(value.registrationVersion)&&value.registrationVersion>=0))fail('apartment_perception_scope_required');
    return Object.fromEntries(KEYS.map(k=>[k,value[k]]));
  }
  const same=(a,b)=>KEYS.every(k=>a?.[k]===b?.[k]);
  const SCENE=['siteId','contentRevision','frameDigest','registrationVersion'],TERMINAL=new Set(['stopped','failed','interrupted','blocked']);
  const OPERATOR_MS=90000,CLEANUP_MS=30000;
  const analysisListeners=new Set();let ownedAnalysis=null,lastAnalysis={status:'idle',reason:null,requestId:null,session:null};
  function analysisState(){
    const value=ownedAnalysis;
    return copy(value?{owned:true,status:value.status,reason:value.reason,requestId:value.requestId,session:value.session,
      identity:value.identity,generation:value.generation,cancelRequested:value.cancelRequested,sessionSeconds:300,
      operatorSeconds:OPERATOR_MS/1000,cleanupSeconds:CLEANUP_MS/1000}:{...lastAnalysis,owned:false});
  }
  function publishAnalysis(){const value=analysisState();for(const fn of analysisListeners)fn(copy(value));}
  function watchAnalysis(fn){if(typeof fn!=='function')fail('apartment_analysis_callback_required');analysisListeners.add(fn);fn(analysisState());return()=>analysisListeners.delete(fn);}
  function analysisOptions(value){
    const identity=value?.identity;
    if(!identity||identity.siteId!=='victoria'||typeof identity.contentRevision!=='string'||!identity.contentRevision
      ||!/^[a-f0-9]{64}$/.test(identity.frameDigest||'')||!(identity.registrationVersion===null||Number.isSafeInteger(identity.registrationVersion)&&identity.registrationVersion>=0)
      ||!Number.isSafeInteger(value.generation)||value.generation<0||typeof value.isCurrent!=='function')fail('apartment_analysis_scene_scope_required');
    return {...value,identity:Object.fromEntries(SCENE.map(key=>[key,identity[key]]))};
  }
  function currentAnalysis(value){return !value.signal?.aborted&&!root.document?.hidden&&value.isCurrent()===true
    &&(!root.HomeSpatialViewport?.getSelectedSiteId?.()||root.HomeSpatialViewport.getSelectedSiteId()==='victoria');}
  function requireAnalysisCurrent(value){if(!currentAnalysis(value))fail('apartment_analysis_current_scene_scope_required');}
  function uuid(){const value=root.crypto?.randomUUID?.();if(typeof value!=='string'||!/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(value))fail('apartment_analysis_secure_request_id_unavailable');return value;}
  async function analysisAvailability(value){
    const options=analysisOptions(value);requireAnalysisCurrent(options);
    const caps=await root.HomePerceptionLabTransport.request('capabilities.get',{siteId:'victoria'},{signal:options.signal});
    requireAnalysisCurrent(options);
    const recipe=caps?.experiments?.find?.(row=>row.id==='apartment-viewing'),bounded=caps?.apartment_viewing;
    const available=caps?.site_id==='victoria'&&caps.browser_access?.write!==false&&caps.recording_allowed===true&&recipe?.status==='ready'&&recipe.readiness?.state==='ready'
      &&bounded?.provisioned===true&&bounded.experiment_id==='apartment-viewing'&&bounded.session_seconds===300
      &&bounded.media_storage==='bounded_ram'&&Number.isSafeInteger(bounded.max_frames)&&bounded.max_frames>0&&bounded.max_frames<=8
      &&Number.isSafeInteger(bounded.max_retained_bytes)&&bounded.max_retained_bytes>0&&bounded.max_retained_bytes<=8388608
      &&bounded.automatic_renewal===false&&bounded.viewing_only===true&&bounded.physical_permission===false;
    return {available,reason:available?null:caps?.browser_access?.write===false?'apartment_viewing_browser_read_only':'apartment_viewing_recipe_unavailable',sessionSeconds:300};
  }
  function analysisSession(row,owner){
    if(!row||row.site_id!=='victoria'||row.camera_id!=='den'||row.experiment_id!=='apartment-viewing'||row.request_id!==owner.requestId
      ||!ID.test(row.id||'')||!Number.isSafeInteger(row.generation)||row.generation<0
      ||!['requested','starting','recording','stopping','stopped','failed','interrupted','blocked','indeterminate'].includes(row.state)
      ||owner.session&&(row.id!==owner.session.id||row.generation!==owner.session.generation))fail('apartment_analysis_session_response_mismatch');
    return copy(row);
  }
  function finishAnalysis(owner,status,reason=null){
    if(ownedAnalysis!==owner)return;
    root.clearTimeout(owner.deadline);root.clearTimeout(owner.cleanupDeadline);root.clearTimeout(owner.pollTimer);owner.unsubscribe?.();
    lastAnalysis={status,reason,requestId:owner.requestId,session:owner.session,sessionSeconds:300};ownedAnalysis=null;publishAnalysis();
  }
  function acceptAnalysisSession(owner,row){
    if(ownedAnalysis!==owner)fail('apartment_analysis_owner_retired');
    owner.session=analysisSession(row,owner);
    if(TERMINAL.has(owner.session.state)){finishAnalysis(owner,owner.session.state==='stopped'?'stopped':'failed',owner.session.reason||owner.session.state);return;}
    if(owner.cleanupExpired){owner.status='stop_failed';owner.reason='cleanup_unconfirmed';publishAnalysis();return;}
    if(owner.status!=='stop_failed'){owner.status=owner.cancelRequested?'stopping':owner.session.state;owner.reason=null;}
    publishAnalysis();
  }
  function analysisError(owner,error,status){
    if(ownedAnalysis===owner){owner.status=owner.cleanupExpired?'stop_failed':status;
      owner.reason=owner.cleanupExpired?'cleanup_unconfirmed':error.code||error.message||'apartment_analysis_unavailable';publishAnalysis();}
  }
  // This poll only reads the session we created. The operator deadline stops
  // that exact session; the unchanged service ceiling remains a backstop.
  function pollAnalysis(owner){
    if(ownedAnalysis!==owner||owner.pollTimer||owner.cleanupExpired)return;
    owner.pollTimer=root.setTimeout(async()=>{
      owner.pollTimer=null;if(ownedAnalysis!==owner||!owner.session)return;
      try{
        const response=await root.HomePerceptionLabTransport.request('sessions.get',{siteId:'victoria',sessionId:owner.session.id});
        if(ownedAnalysis!==owner)return;acceptAnalysisSession(owner,response?.session||response);
        if(ownedAnalysis===owner&&owner.cleanupExpired){owner.status='stop_failed';owner.reason='cleanup_unconfirmed';publishAnalysis();}
      }catch(error){analysisError(owner,error,owner.cancelRequested?'stop_failed':'unavailable');}
      if(ownedAnalysis===owner&&owner.polls++<165)pollAnalysis(owner);
      else if(ownedAnalysis===owner)analysisError(owner,Error('apartment_analysis_cleanup_confirmation_required'),'stop_failed');
    },2000);
  }
  async function resolveAnalysis(owner){
    const response=await root.HomePerceptionLabTransport.request('sessions.list',{siteId:'victoria'});
    if(ownedAnalysis!==owner)fail('apartment_analysis_owner_retired');
    if(response?.site_id!=='victoria'||!Array.isArray(response.items)||response.items.length>200)fail('apartment_analysis_session_response_mismatch');
    const found=response.items.filter(row=>row.request_id===owner.requestId);
    if(found.length!==1)fail(found.length?'apartment_analysis_request_not_unique':'apartment_analysis_acknowledgement_unresolved');
    acceptAnalysisSession(owner,found[0]);return owner.session;
  }
  async function stopAnalysis(){
    const owner=ownedAnalysis;if(!owner)return analysisState();
    owner.cancelRequested=true;owner.reason=null;
    if(owner.status==='checking'){owner.status='stopping';publishAnalysis();return analysisState();}
    if(!owner.requestId){finishAnalysis(owner,'stopped');return analysisState();}
    if(owner.status==='starting'&&!owner.session&&!owner.deadlineFired){owner.status='stopping';publishAnalysis();return analysisState();}
    if(owner.stopPromise)return owner.stopPromise;
    owner.status='stopping';publishAnalysis();
    owner.stopPromise=(async()=>{
      try{
        if(!owner.session)await resolveAnalysis(owner);
        if(ownedAnalysis!==owner)return analysisState();
        owner.stopRequestId=owner.stopRequestId||uuid();
        const row=await root.HomePerceptionLabTransport.request('sessions.stop',{siteId:'victoria',sessionId:owner.session.id,request_id:owner.stopRequestId});
        if(ownedAnalysis!==owner)return analysisState();acceptAnalysisSession(owner,row?.session||row);
        if(ownedAnalysis===owner)pollAnalysis(owner);return analysisState();
      }catch(error){analysisError(owner,error,owner.session?'stop_failed':'uncertain');throw error;}
      finally{owner.stopPromise=null;}
    })();return owner.stopPromise;
  }
  async function reconcileAnalysis(value){
    const options=analysisOptions(value),owner=ownedAnalysis;
    if(!owner?.requestId)fail('apartment_analysis_no_pending_request');
    try{
      await resolveAnalysis(owner);
      const adoptable=ownedAnalysis===owner&&!owner.cancelRequested&&currentAnalysis(options)
        &&SCENE.every(key=>owner.identity[key]===options.identity[key])&&owner.generation===options.generation;
      if(ownedAnalysis===owner&&(owner.cancelRequested||!adoptable))await stopAnalysis();
      else if(ownedAnalysis===owner)pollAnalysis(owner);
      return {session:copy(owner.session),adoptable,reconciled:true};
    }catch(error){analysisError(owner,error,owner.session?'unavailable':'uncertain');throw error;}
  }
  async function startAnalysis(value){
    const options=analysisOptions(value);requireAnalysisCurrent(options);
    if(ownedAnalysis)fail('apartment_analysis_already_pending');
    const owner={identity:options.identity,generation:options.generation,status:'checking',reason:null,session:null,requestId:null,
      cancelRequested:false,deadline:null,pollTimer:null,polls:0,unsubscribe:null,stopPromise:null};
    ownedAnalysis=owner;publishAnalysis();
    try{
      const availability=await analysisAvailability(options);requireAnalysisCurrent(options);
      if(!availability.available)fail(availability.reason);
      if(owner.cancelRequested){finishAnalysis(owner,'stopped');return {session:null,adoptable:false};}
      owner.requestId=uuid();owner.status='starting';publishAnalysis();
      owner.deadline=root.setTimeout(()=>{if(ownedAnalysis===owner){owner.deadlineFired=true;stopAnalysis().catch(()=>{});}},OPERATOR_MS);
      owner.cleanupDeadline=root.setTimeout(()=>{
        if(ownedAnalysis!==owner)return;
        owner.cleanupExpired=true;root.clearTimeout(owner.pollTimer);owner.pollTimer=null;
        owner.status='stop_failed';owner.reason='cleanup_unconfirmed';publishAnalysis();
      },OPERATOR_MS+CLEANUP_MS);
      owner.unsubscribe=root.HomeSpatialViewport?.subscribe?.(()=>{
        if(root.HomeSpatialViewport?.getSelectedSiteId?.()!=='victoria'&&ownedAnalysis===owner)stopAnalysis().catch(()=>{});
      });
      let row,reconciled=false;
      try{row=await root.HomePerceptionLabTransport.request('sessions.start',{siteId:'victoria',camera_id:'den',experiment_id:'apartment-viewing',
        name:'Victoria Apartment live view',request_id:owner.requestId,reasoning_enabled:true});}
      catch(error){
        // A failed acknowledgement is not proof the POST failed. Recover only
        // the original request via GET; never create another UUID or POST here.
        analysisError(owner,error,'uncertain');
        try{row=await resolveAnalysis(owner);reconciled=true;}
        catch(readError){
          if(Number.isSafeInteger(error.status)&&error.status>=400&&error.status<500&&readError.code==='apartment_analysis_acknowledgement_unresolved')finishAnalysis(owner,'failed',error.code||error.message);
          else analysisError(owner,readError,'uncertain');throw readError;
        }
      }
      if(ownedAnalysis===owner)acceptAnalysisSession(owner,row);
      const adoptable=ownedAnalysis===owner&&!owner.cancelRequested&&currentAnalysis(options);
      if(ownedAnalysis===owner&&!adoptable)await stopAnalysis();else if(ownedAnalysis===owner)pollAnalysis(owner);
      return {session:copy(owner.session),adoptable,reconciled};
    }catch(error){
      if(ownedAnalysis===owner&&!owner.requestId)finishAnalysis(owner,'failed',error.code||error.message);
      else if(ownedAnalysis===owner&&!['uncertain','stop_failed'].includes(owner.status))analysisError(owner,error,owner.session?'unavailable':'uncertain');
      throw error;
    }
  }
  function releaseAnalysis({identity,generation}={}){
    const owner=ownedAnalysis;
    if(owner&&owner.generation===generation&&SCENE.every(key=>owner.identity[key]===identity?.[key]))stopAnalysis().catch(()=>{});
  }
  // previewArmed is a read-only getter for the desk-preview arm state; it only
  // selects the observer cadence and the announced-frame prefetch.
  function subscribe({scope:value,arm='B',signal,onUpdate,onStatus,previewArmed}={}){
    const selected=scope(value);if(typeof onUpdate!=='function'||typeof onStatus!=='function')fail('apartment_perception_callbacks_required');
    if(!['A','B'].includes(arm))fail('den_lab_arm_required');
    if(signal?.aborted){onStatus({status:'disconnected',reason:'subscription_aborted'});return()=>{};}
    if(active)active.close();const token=++epoch;
    const observer=root.HomeDenLabStream?.createObserver({siteId:'victoria',sessionId:selected.sessionId,generation:selected.generation,scope:selected,
      arm,transport:root.HomePerceptionLabTransport,graphModel:root.HomePerceptionLabGraph?.model,env:root,
      ...(typeof previewArmed==='function'?{previewArmed}:{}),onState:state=>{
        if(active?.token!==token)return;
        const statuses={idle:'connecting',connecting:'connecting',connected:'live',stopped:'stopped',paused:'unavailable',unavailable:'unavailable',disconnected:'disconnected'};
        const packet={...copy(state.perception),scope:copy(selected)};
        if(packet.frame)packet.frame.scope=copy(selected);
        if(ownedAnalysis?.session?.id===selected.sessionId&&ownedAnalysis.session.generation===selected.generation&&TERMINAL.has(packet.session_state))
          acceptAnalysisSession(ownedAnalysis,{...ownedAnalysis.session,state:packet.session_state});
        onUpdate(packet);onStatus({status:statuses[state.connection]||'unavailable',...(state.reason?{reason:state.reason}:{}),
          ...(state.recoverable===true?{recoverable:true}:{})});
      }});
    if(!observer)fail('apartment_perception_observer_unavailable');
    let closed=false;
    const close=()=>{if(closed)return;closed=true;signal?.removeEventListener('abort',close);observer.destroy();if(active?.token===token)active=null;};
    active={token,scope:selected,observer,close};signal?.addEventListener('abort',close,{once:true});observer.start();return close;
  }
  function sampleClock(){const now=root.Date?.now?.();return Number.isFinite(now)?{now_s:now/1000,uncertainty_s:null,domain:'unix'}:null;}
  async function readFrame(frame,{scope:value,signal}={}){
    const selected=scope(value),owner=active;
    if(!owner||!same(owner.scope,selected)||!same(scope(frame?.scope),selected)||signal?.aborted)fail('apartment_perception_current_scope_required');
    const image=await owner.observer.readFrame(frame,{signal});
    if(active!==owner||!same(owner.scope,selected)||signal?.aborted)fail('apartment_perception_current_scope_required');
    return image;
  }
  async function listSessions({siteId,signal}={}){
    if(siteId!=='victoria'||signal?.aborted||root.document?.hidden||listPending)fail('apartment_perception_session_read_unavailable');
    const token=epoch,selected=root.HomeSpatialViewport?.getSelectedSiteId?.();
    if(selected&&selected!=='victoria')fail('apartment_perception_current_scope_required');
    const request=Promise.resolve().then(()=>root.HomePerceptionLabTransport.request('sessions.list',{siteId:'victoria'},{signal}));listPending=request;
    try{const value=await request;
      if(signal?.aborted||root.document?.hidden||epoch!==token||root.HomeSpatialViewport?.getSelectedSiteId?.()&&root.HomeSpatialViewport.getSelectedSiteId()!=='victoria')
        fail('apartment_perception_current_scope_required');
      if(value?.site_id!=='victoria'||!Array.isArray(value.items)||value.items.length>200||value.items.some(s=>s?.site_id!=='victoria'||!ID.test(s.id||'')
        ||!Number.isSafeInteger(s.generation)||s.generation<0))fail('apartment_perception_session_response_mismatch');return copy(value.items);
    }finally{if(listPending===request)listPending=null;}
  }
  // Observe the already-running producer. This path never creates, stops or
  // renews a recording. A changed epoch/session is announced before its new
  // session read so the consumer can retire the preceding frame immediately.
  function watchActivityLoop(value){
    const options=analysisOptions(value);
    if(typeof value.onChange!=='function')fail('apartment_activity_loop_callback_required');
    let closed=false,timer=null,pending=null,lastKey=null;
    const valid=()=>!closed&&currentAnalysis(options);
    const publish=state=>{if(valid())value.onChange(copy(state));};
    const close=()=>{if(closed)return;closed=true;root.clearTimeout(timer);pending?.abort();
      value.signal?.removeEventListener('abort',close);loopReaders.delete(close);};
    async function read(){
      if(!valid()){close();return;}
      const abort=new (root.AbortController||AbortController)();pending=abort;
      try{
        const status=await root.HomePerceptionLabTransport.request('activityLoop.get',{siteId:'victoria'},{signal:abort.signal});
        if(!valid()||abort.signal.aborted)return;
        if(status?.schema!=='perception-lab-activity-loop-1'||!['running','paused','stopped'].includes(status.state))fail('apartment_activity_loop_response_invalid');
        const deep=status.deep_stack;
        if(status.state!=='running'||!deep?.session_id||deep.wanted!==true){lastKey=null;
          publish({status:status.state,reason:status.stopped_reason||'activity_loop_not_producing',session:null});return;}
        if(!ID.test(deep.session_id)||typeof deep.producer_epoch!=='string'||!/^[a-f0-9]{32}$/.test(deep.producer_epoch)
          ||!Number.isSafeInteger(deep.rolls)||deep.rolls<0)fail('apartment_activity_loop_binding_invalid');
        const key=[deep.producer_epoch,deep.session_id].join(':');
        if(key!==lastKey){lastKey=null;publish({status:'connecting',reason:'activity_loop_session_changed',session:null});}
        const response=await root.HomePerceptionLabTransport.request('sessions.get',{siteId:'victoria',sessionId:deep.session_id},{signal:abort.signal});
        if(!valid()||abort.signal.aborted)return;
        const session=response?.session||response;
        if(session?.id!==deep.session_id||session.site_id!=='victoria'||session.camera_id!=='den'
          ||!Number.isSafeInteger(session.generation)||session.generation<0)fail('apartment_activity_loop_session_mismatch');
        if(!['recording','running','starting'].includes(session.state)){lastKey=null;
          publish({status:'waiting',reason:'activity_loop_session_not_running',session:null});return;}
        lastKey=key;
        publish({status:'running',reason:null,session,producerEpoch:deep.producer_epoch,rolls:deep.rolls});
      }catch(error){lastKey=null;publish({status:'unavailable',reason:error.code||error.message||'activity_loop_unavailable',session:null});}
      finally{if(pending===abort)pending=null;if(valid())timer=root.setTimeout(read,1000);else close();}
    }
    loopReaders.add(close);value.signal?.addEventListener('abort',close,{once:true});
    void read();return close;
  }
  const invalidate=()=>{epoch++;for(const close of [...loopReaders])close();active?.close();if(ownedAnalysis)stopAnalysis().catch(()=>{});};
  root.addEventListener?.('home:logout',invalidate);root.addEventListener?.('pagehide',invalidate);
  root.document?.addEventListener?.('visibilitychange',()=>{if(root.document.hidden)invalidate();});
  root.document?.addEventListener?.('click',e=>{if(e.target?.closest?.('#logout'))invalidate();},true);
  root.HomeApartmentPerceptionSource=Object.freeze({subscribe,sampleClock,readFrame,listSessions,watchActivityLoop,
    analysisState,watchAnalysis,analysisAvailability,startAnalysis,reconcileAnalysis,stopAnalysis,releaseAnalysis});
})(typeof window!=='undefined'?window:globalThis);
