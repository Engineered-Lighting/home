/* Read-only, exact-session Lab inspection beside committed Den lighting.
 * All source/arm meanings come from the existing Lab graph model. No raw
 * observation payload becomes geometry, person identity or lighting authority. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;
  else root.HomeDenLabStream=api;})(typeof window!=='undefined'?window:globalThis,function(){
  'use strict';
  const copy=x=>JSON.parse(JSON.stringify(x)),ID=/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/,
    KEYS=['site_id','room_id','session_id','domain_epoch'],SCOPED=/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/;
  const fail=code=>{throw Object.assign(Error(code),{code});},text=(x,n=300)=>typeof x==='string'?x.slice(0,n):'';
  const PAGE=200,GAP=3000,TIMEOUT=4000;
  function create({binding,transport,graphModel,onState,env=globalThis,clock}={}){
    const scope=copy(binding?.scope||{}),pin=binding?.digest;
    if(binding?.qualification!=='live_shadow_unqualified'||scope.site_id!=='victoria'||!ID.test(scope.session_id||'')
      ||!KEYS.every(k=>typeof scope[k]==='string'&&SCOPED.test(scope[k]))||!/^[a-f0-9]{64}$/.test(pin||'')
      ||typeof transport?.request!=='function'||typeof graphModel?.buildModel!=='function')fail('den_lab_fixed_live_binding_required');
    const timers=clock||{setTimeout:(f,t)=>env.setTimeout(f,t),clearTimeout:t=>env.clearTimeout(t)};
    if(typeof timers.setTimeout!=='function'||typeof timers.clearTimeout!=='function')fail('den_lab_timer_required');
    let wanted=false,destroyed=false,serial=0,scheduled=null,abort=null,pending=null,busy=false,mediaGeneration=null,cursor=0;
    let state=empty('idle'),total=0,kinds={};
    function empty(connection,reason=null){return {connection,reason,session_id:scope.session_id,binding_digest:pin,
      session_state:null,rows:[],event_count:0,event_kinds:[],events_backlog:false,server_time:null,
      qualification:'live_shadow_unqualified',operational_geometry:false,beam_authority:false};}
    function publish(){try{onState?.(copy(state));}catch(_){/* A view callback gains no authority. */}}
    function selected(){return env.HomeSpatialViewport?.getSelectedSiteId?.();}
    function valid(token){return wanted&&!destroyed&&token===serial&&!env.document?.hidden&&(!selected()||selected()==='victoria')
      &&binding.digest===pin&&KEYS.every(k=>binding.scope?.[k]===scope[k]);}
    function clear(connection,reason){state=empty(connection,reason);total=0;kinds={};cursor=0;mediaGeneration=null;publish();}
    function abandon(connection,reason){serial++;timers.clearTimeout(scheduled);scheduled=null;abort?.abort();clear(connection,reason);}
    function stop(reason='observer_stopped'){wanted=false;abandon('disconnected',reason);return snapshot();}
    function schedule(){if(!wanted||destroyed||env.document?.hidden||scheduled!==null||busy||pending)return;
      scheduled=timers.setTimeout(()=>{scheduled=null;void turn(serial);},GAP);}
    async function read(operation,args,token){
      if(!valid(token))fail('den_lab_read_abandoned');
      const controller=new AbortController();abort=controller;let timeout;
      const request=Promise.resolve().then(()=>transport.request(operation,{siteId:'victoria',sessionId:scope.session_id,...args},{signal:controller.signal}));
      pending=request;
      const settled=()=>{if(pending===request){pending=null;if(wanted&&!busy)schedule();}};
      request.then(settled,settled);
      try{const result=await Promise.race([request,new Promise((_,reject)=>{timeout=timers.setTimeout(()=>{
        controller.abort();reject(Object.assign(Error('den_lab_read_timeout'),{code:'den_lab_read_timeout'}));},TIMEOUT);})]);
        if(!valid(token))fail('den_lab_read_abandoned');return result;
      }finally{timers.clearTimeout(timeout);if(abort===controller)abort=null;}
    }
    function liveCheck(live){
      const session=live?.session;
      if(live?.site_id!=='victoria'||live.session_id!==scope.session_id||session?.id!==scope.session_id
        ||session.site_id!=='victoria'||session.camera_id!=='den'||!Number.isSafeInteger(session.generation)||session.generation<0
        ||!Number.isFinite(live.server_time))fail('den_lab_response_binding_mismatch');
      if(mediaGeneration!==null&&mediaGeneration!==session.generation)fail('den_lab_session_generation_changed');
      mediaGeneration=session.generation;return session;
    }
    function graphRows(graph,session){
      if(graph?.contract!=='perception-lab-graph-1'||graph.site_id!=='victoria'||graph.session_id!==scope.session_id
        ||graph.inference!=='none'||graph.session?.id&&graph.session.id!==scope.session_id)fail('den_lab_response_binding_mismatch');
      if(!graph.session||['id','site_id','camera_id','generation'].some(key=>graph.session[key]!==session[key]))
        fail('den_lab_session_generation_changed');
      if(graph.session.state!==session.state)fail('den_lab_cycle_changed');
      const model=graphModel.buildModel(graph);
      if(!Array.isArray(model?.nodes)||model.nodes.length>256)fail('den_lab_graph_model_invalid');
      return model.nodes.map(node=>({id:text(node.id,160),title:text(node.title,160),lane:text(node.lane,16),
        kind:text(node.kind,32),status:text(node.state?.label,100),tone:text(node.state?.tone,32),
        reason:text(node.state?.why),value:text(node.simple?.v),detail:text(node.simple?.sub),
        notes:(Array.isArray(node.simple?.sx)?node.simple.sx:[]).slice(0,12).map(note=>({label:text(note.k,100),value:text(note.v)}))}));
    }
    function eventsCheck(page){
      if(!Array.isArray(page?.items)||page.items.length>PAGE)fail('den_lab_event_page_invalid');
      let next=cursor;
      const incoming=[];
      for(const event of page.items){if(event?.session_id!==scope.session_id||!Number.isSafeInteger(event.id)||event.id<=next
          ||typeof event.kind!=='string'||event.kind.length>160||!Number.isFinite(event.created_at)
          ||!event.payload||typeof event.payload!=='object'||Array.isArray(event.payload)
          ||event.payload?.site_id!==undefined&&event.payload.site_id!=='victoria'
          ||event.payload?.camera_id!==undefined&&event.payload.camera_id!=='den')fail('den_lab_response_binding_mismatch');
        next=event.id;incoming.push(event.kind);}
      if(page.after_id!==next)fail('den_lab_event_page_invalid');
      // Event kinds/IDs are diagnostics only. Original payloads stay in Lab;
      // existing graph projections own their privacy and semantic summaries.
      for(const kind of incoming){if(!Object.hasOwn(kinds,kind)&&Object.keys(kinds).length>=128)continue;
        kinds[kind]=(kinds[kind]||0)+1;}
      total+=incoming.length;cursor=next;return page.items.length===PAGE;
    }
    async function turn(token){
      if(!valid(token)||busy||pending)return;busy=true;
      try{
        const live=await read('sessions.live',{},token),session=liveCheck(live);
        const graph=await read('sessions.graph',{},token),rows=graphRows(graph,session);
        const page=await read('sessions.events',{afterId:cursor,limit:PAGE},token),backlog=eventsCheck(page);
        if(!valid(token))return;
        state={...empty(session.state==='recording'?'connected':'stopped'),session_state:session.state,rows,
          event_count:total,event_kinds:Object.entries(kinds).map(([kind,count])=>({kind,count})),
          events_backlog:backlog,server_time:live.server_time};publish();
        if(session.state!=='recording')wanted=false;
      }catch(error){if(token===serial&&wanted&&!destroyed){
        const code=error?.code||'den_lab_unavailable';
        const fatal=error?.status===401||error?.status===403||['den_lab_response_binding_mismatch','den_lab_session_generation_changed','lab_stale_connection'].includes(code);
        if(fatal)wanted=false;clear('unavailable',code);
      }}finally{busy=false;if(valid(serial))schedule();}
    }
    function start(){if(destroyed)fail('den_lab_stream_destroyed');if(wanted)return snapshot();wanted=true;serial++;
      if(selected()&&selected()!=='victoria'){stop('selected_site_changed');return snapshot();}
      state=empty(env.document?.hidden?'paused':'connecting');publish();
      if(!env.document?.hidden&&!pending)void turn(serial);return snapshot();}
    const visibility=()=>{if(env.document?.hidden){if(wanted)abandon('paused','page_hidden');}
      else if(wanted){serial++;if(!pending)void turn(serial);}};
    const pagehide=()=>stop('page_hidden'),logout=()=>stop('owner_logout');
    const logoutClick=e=>{if(e.target?.closest?.('#logout'))logout();};
    const siteChanged=()=>{if(selected()&&selected()!=='victoria')stop('selected_site_changed');};
    env.document?.addEventListener?.('visibilitychange',visibility);
    env.document?.addEventListener?.('click',logoutClick,true);
    env.addEventListener?.('pagehide',pagehide);env.addEventListener?.('home:logout',logout);
    const unsubscribe=env.HomeSpatialViewport?.subscribe?.(siteChanged);
    function snapshot(){return copy(state);}
    function destroy(){if(destroyed)return;stop('panel_unmounted');destroyed=true;
      env.document?.removeEventListener?.('visibilitychange',visibility);env.document?.removeEventListener?.('click',logoutClick,true);
      env.removeEventListener?.('pagehide',pagehide);env.removeEventListener?.('home:logout',logout);unsubscribe?.();}
    return Object.freeze({start,stop,destroy,snapshot});
  }
  // Ordinary Apartment viewing has no lighting binding. Its renderer scope is
  // a viewing pin only; /live and the exact specialist chain own source facts.
  const VIEW_KEYS=['siteId','contentRevision','frameDigest','registrationVersion','cameraId','sessionId','generation'];
  const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b),number=x=>Number.isFinite(x)?x:null;
  function viewScope(value,sessionId,generation){
    if(value===undefined)return null;
    if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).length!==VIEW_KEYS.length
      ||!VIEW_KEYS.every(k=>Object.hasOwn(value,k))||value.siteId!=='victoria'||value.cameraId!=='den'||value.sessionId!==sessionId
      ||typeof value.contentRevision!=='string'||!value.contentRevision||!/^[a-f0-9]{64}$/.test(value.frameDigest||'')
      ||!(value.registrationVersion===null||Number.isSafeInteger(value.registrationVersion)&&value.registrationVersion>=0)
      ||!Number.isSafeInteger(value.generation)||value.generation<0||generation!==undefined&&value.generation!==generation)
      fail('den_lab_view_scope_required');
    return Object.fromEntries(VIEW_KEYS.map(k=>[k,value[k]]));
  }
  function createObserver({siteId,sessionId,arm='B',generation,scope,transport,graphModel,onState,env=globalThis,clock}={}){
    if(siteId!=='victoria'||!ID.test(sessionId||'')||!['A','B'].includes(arm)||typeof transport?.request!=='function'
      ||generation!==undefined&&(!Number.isSafeInteger(generation)||generation<0))fail('den_lab_observer_binding_required');
    const viewing=viewScope(scope,sessionId,generation),timers=clock||{setTimeout:(f,t)=>env.setTimeout(f,t),clearTimeout:t=>env.clearTimeout(t)};
    if(typeof timers.setTimeout!=='function'||typeof timers.clearTimeout!=='function')fail('den_lab_timer_required');
    let selectedArm=arm,pinned=generation??viewing?.generation??null,wanted=false,destroyed=false,retired=false,serial=0,scheduled=null,
      busy=false,turning=false,mediaDone=null,pending=null,controller=null,lastLive=null,lastGraph=null,lastLiveReadStarted=null,frames=new Map(),frameObservations=new Map(),cache=new Map(),readImages=new Set();
    const monotonic=()=>{const value=env.performance?.now?.();return Number.isFinite(value)?value:null;};
    function blank(){return {schema:'home-apartment-perception-1',scope:viewing?copy(viewing):null,source_epoch:null,session_state:null,
      frame:null,pose2d:null,models:[],room_pose:null,diagnostic_room_pose:null,diagnostic_receipt_elapsed_s:null,
      approximate_room_pose:null,approximate_receipt_elapsed_s:null};}
    let state={connection:'idle',reason:null,perception:blank()};
    const selected=()=>env.HomeSpatialViewport?.getSelectedSiteId?.();
    const valid=token=>wanted&&!destroyed&&!retired&&serial===token&&!env.document?.hidden&&(!selected()||selected()==='victoria')
      &&(!viewing||VIEW_KEYS.every(k=>scope[k]===viewing[k]));
    const snapshot=()=>copy(state);
    function publish(){try{onState?.(snapshot());}catch(_){/* Display callbacks confer no authority. */}}
    function clear(connection,reason){state={connection,reason,perception:blank()};frames.clear();frameObservations.clear();cache.clear();readImages.clear();lastLive=null;lastGraph=null;publish();}
    function abandon(connection,reason){serial++;timers.clearTimeout(scheduled);scheduled=null;controller?.abort();clear(connection,reason);}
    function stop(reason='observer_stopped'){wanted=false;abandon('disconnected',reason);return snapshot();}
    function schedule(){if(!wanted||destroyed||retired||env.document?.hidden||scheduled!==null||busy||turning||pending)return;
      scheduled=timers.setTimeout(()=>{scheduled=null;void turn(serial);},1000);}
    async function effect(operation,token,externalSignal){
      if(!valid(token)||externalSignal?.aborted)fail('den_lab_read_abandoned');
      if(pending)fail('den_lab_read_busy');
      const abort=new AbortController();controller=abort;let timeout,expired=false,rejectAbort;
      const cancellation=new Promise((_,reject)=>{rejectAbort=()=>reject(Object.assign(Error(expired?'den_lab_read_timeout':'den_lab_read_abandoned'),
        {code:expired?'den_lab_read_timeout':'den_lab_read_abandoned'}));});
      abort.signal.addEventListener('abort',rejectAbort,{once:true});
      const cancel=()=>abort.abort();externalSignal?.addEventListener('abort',cancel,{once:true});
      const request=Promise.resolve().then(()=>operation(abort.signal));pending=request;
      const settled=()=>{if(pending===request){pending=null;if(wanted&&!busy)schedule();}};request.then(settled,settled);
      try{const value=await Promise.race([request,cancellation,new Promise((_,reject)=>{timeout=timers.setTimeout(()=>{
        expired=true;abort.abort();reject(Object.assign(Error('den_lab_read_timeout'),{code:'den_lab_read_timeout'}));},TIMEOUT);})]);
        if(!valid(token)||externalSignal?.aborted)fail('den_lab_read_abandoned');return value;
      }finally{timers.clearTimeout(timeout);abort.signal.removeEventListener('abort',rejectAbort);
        externalSignal?.removeEventListener('abort',cancel);if(controller===abort)controller=null;}
    }
    const read=(operation,args,token)=>effect(signal=>transport.request(operation,{siteId:'victoria',sessionId,...args},{signal}),token);
    function checkLive(live){
      const s=live?.session;
      if(live?.site_id!=='victoria'||live.session_id!==sessionId||s?.id!==sessionId||s.site_id!=='victoria'||s.camera_id!=='den'
        ||!Number.isSafeInteger(s.generation)||s.generation<0||!Number.isFinite(live.server_time))fail('den_lab_response_binding_mismatch');
      if(!['requested','starting','recording','stopping','stopped','blocked','failed','interrupted','indeterminate'].includes(s.state))
        fail('den_lab_session_state_unsupported');
      if(pinned!==null&&pinned!==s.generation)fail('den_lab_session_generation_changed');pinned=s.generation;
      return s;
    }
    function checkGraph(graph,live){
      const s=live.session;
      if(graph?.contract!=='perception-lab-graph-1'||graph.site_id!=='victoria'||graph.session_id!==sessionId||graph.inference!=='none')
        fail('den_lab_response_binding_mismatch');
      if(!graph.session||['id','site_id','camera_id','generation'].some(k=>graph.session[k]!==s[k]))fail('den_lab_session_generation_changed');
      if(graph.session.state!==s.state)fail('den_lab_cycle_changed');
    }
    function validFrame(a){
      const f=a?.frame;
      return f&&ID.test(f.media_id||'')&&typeof a.frame_id==='string'&&a.frame_id&&f.id===a.frame_id&&f.site_id==='victoria'&&f.generation===pinned
        &&Number.isInteger(f.width)&&Number.isInteger(f.height)&&f.width>0&&f.height>0&&f.width<=8192&&f.height<=8192;
    }
    function exactObservation(a,name,live){
      const value=a?.observation;
      if(!validFrame(a)||!value||value.schema!=='home-lab-arm-observation-1'||value.arm!==name.toLowerCase()
        ||value.session_id!==sessionId||value.site_id!=='victoria'||value.camera_id!=='den'
        ||typeof value.request_id!=='string'||!SCOPED.test(value.request_id)
        ||value.generation!==live.experiment_generation||!Number.isSafeInteger(value.generation)||value.generation<1
        ||value.frame_id!==a.frame_id||!Array.isArray(value.media_ids)||value.media_ids.length<1||value.media_ids.length>8
        ||!value.media_ids.every(id=>ID.test(id))||new Set(value.media_ids).size!==value.media_ids.length
        ||!value.media_ids.includes(a.frame.media_id)||typeof value.capture_epoch!=='string'||!value.capture_epoch||value.capture_epoch.length>256
        ||typeof value.run_id!=='string'||!SCOPED.test(value.run_id)||value.run_id!==a.profile?.id
        ||typeof value.model_version!=='string'||!value.model_version||value.model_version.length>256||value.model_version!==a.profile?.name
        ||typeof value.artifact_sha256!=='string'||!/^[a-f0-9]{64}$/.test(value.artifact_sha256)
        ||typeof value.engine_epoch!=='string'||!value.engine_epoch||value.engine_epoch.length>256
        ||!['observed_at','available_at','valid_until'].every(key=>Number.isFinite(value[key])&&value[key]>=0)
        ||value.observed_at>value.available_at||value.observed_at>=value.valid_until)return null;
      return value;
    }
    function frameMetadata(packet,metadata){
      if(!packet.frame||!metadata)return;
      packet.frame.valid_until=number(metadata.valid_until);
      if(typeof metadata.media_sha256==='string'&&/^[a-f0-9]{64}$/.test(metadata.media_sha256))packet.frame.media_sha256=metadata.media_sha256;
      packet.source_epoch=typeof metadata.capture_epoch==='string'?text(metadata.capture_epoch,256)||null:null;
    }
    function boundedOriginal(value,limit){
      if(value===undefined||value===null)return null;
      const raw=JSON.stringify(value);return raw.length<=limit?JSON.parse(raw):null;
    }
    function projection(live,graph){
      const packet=blank();packet.session_state=live.session.state;
      // The selected original RGB and its exact 2D points are available from
      // /live. Graph/model summaries may arrive later and keep their own clocks.
      graph=graph||{sources:[],arms:{}};
      if(!Array.isArray(graph.sources)||graph.sources.length>256)fail('den_lab_graph_model_invalid');
      const rendered=typeof graphModel?.buildModel==='function'?graphModel.buildModel(graph):null;
      const storage=live.apartment_viewing,metric=storage?.metric;
      if(storage?.media_storage==='bounded_ram'&&storage.encoded_recording===false&&storage.viewing_only===true
        &&storage.physical_permission===false&&metric?.enabled===true&&metric.arm==='b'&&metric.physical_permission===false
        &&metric.operational_geometry===false&&metric.metric_depth==='diagnostic_nominal_eeprom'
        &&['approximate_viewing_only','unavailable'].includes(metric.room_alignment)
        &&['awaiting','capturing','awaiting_model','available','unavailable','stopped'].includes(metric.state)
        &&(metric.reason===null||typeof metric.reason==='string'&&/^[A-Za-z][A-Za-z0-9_]{0,159}$/.test(metric.reason)))
        packet.models.push({family:'metric_input',source_id:'apartment_viewing_metric',model_id:null,status:metric.state,
          reason:metric.reason,observed_at:null,available_at:null,valid_until:null,
          result:{summary:'Metric input - '+metric.state,prepared_frames:Number.isSafeInteger(metric.prepared_frames)&&metric.prepared_frames>=0?metric.prepared_frames:null,
            projected_frames:Number.isSafeInteger(metric.projected_frames)&&metric.projected_frames>=0?metric.projected_frames:null,
            physical_permission:false,operational_geometry:false}});
      for(const source of graph.sources){
        if(typeof source?.id!=='string'||source.id.length>160)fail('den_lab_graph_model_invalid');
        const node=rendered?.nodes?.find(n=>n.sid===source.id);
        let summary=node&&typeof graphModel?.simpleOf==='function'?graphModel.simpleOf(node,graph):null;
        const storage=live.apartment_viewing;
        if(source.id==='rgb_camera'&&live.session.state==='recording'&&validFrame(live.arms?.[selectedArm])
          &&storage?.state==='active'&&storage.media_storage==='bounded_ram'&&storage.encoded_recording===false
          &&storage.viewing_only===true&&storage.physical_permission===false)
          summary={v:'Analyzed frame available in RAM',sub:'No durable image or video recording'};
        packet.models.push({family:source.id,source_id:source.id,model_id:typeof source.version?.model_version==='string'?text(source.version.model_version,256):null,
          status:text(source.status,100)||'unknown',reason:typeof source.reason==='string'?text(source.reason,300):null,
          observed_at:number(source.last_observed_at),available_at:number(source.last_available_at),valid_until:number(source.valid_until),
          result:{title:text(source.title,160),version:boundedOriginal(source.version,4096),counts:boundedOriginal(source.counts,1024),
            summary:summary?{value:text(summary.v,400),detail:text(summary.sub,400),
              notes:(Array.isArray(summary.sx)?summary.sx:[]).slice(0,12).map(n=>({label:text(n.k,100),value:text(n.v,300)}))}:null}});
      }
      for(const name of ['A','B']){
        const a=live.arms?.[name],g=graph.arms?.[name.toLowerCase()],chain=Array.isArray(g?.chain)?g.chain:[],s=chain.find(x=>x.stage==='specialist_output');
        const original=exactObservation(a,name,live);
        const matched=validFrame(a)&&g?.generation===live.experiment_generation&&s?.generation===live.experiment_generation
          &&s.frame_id===a.frame_id&&s.frame?.media_id===a.frame.media_id;
        if(a&&validFrame(a)){
          const known={frame_id:a.frame_id,...copy(a.frame)};frames.set(name,known);
          if(original)frameObservations.set(frameKey(known),copy(original));
        }
        if(s)packet.models.push({family:'pose',source_id:'specialist_'+name.toLowerCase(),model_id:text(s.model_version||a?.profile?.id,256)||null,
          status:text(s.status,64)||'unavailable',observed_at:number(s.observed_at),available_at:number(s.available_at),valid_until:number(s.valid_until),
          ...(typeof s.frame_id==='string'?{frame_id:s.frame_id}:{}),result:{event_id:s.event_id??null,request_id:s.request_id??null,
            artifact_sha256:s.artifact_sha256??null,engine_epoch:s.engine_epoch??null,frame_association:matched?'matched':'different_or_unknown'}});
        const j=chain.find(x=>x.stage==='result');
        if(j)packet.models.push({family:'jev',source_id:'reasoning_'+name.toLowerCase(),model_id:text(j.model_version,256)||null,
          status:text(j.status,64)||'unavailable',observed_at:null,available_at:number(j.available_at),valid_until:number(j.valid_until),
          result:{activity:typeof j.activity==='string'?text(j.activity,160):null,source_end_s:number(j.source_end_s),
            available_at_s:number(j.available_at_s),freshness:text(j.freshness,64)||null,request_id:j.request_id??null,output_event_id:j.output_event_id??null}});
        if(name!==selectedArm||!validFrame(a))continue;
        // The specialist lease can extend past the original RGB lease. Only
        // the exact image descriptor can supply the latter; absent stays null.
        packet.frame={frame_id:a.frame_id,...copy(a.frame),valid_until:null};
        if(original)packet.source_epoch=original.capture_epoch;
        frameMetadata(packet,cache.get(frameKey(packet.frame))?.metadata);
        const diagnostic=boundedOriginal(a.diagnostic_room_pose,128*1024),ended=monotonic();
        if(diagnostic){packet.diagnostic_room_pose=diagnostic;
          packet.diagnostic_receipt_elapsed_s=lastLiveReadStarted!==null&&ended!==null&&ended>=lastLiveReadStarted?(ended-lastLiveReadStarted)/1000:null;}
        const approximate=boundedOriginal(a.approximate_room_pose,128*1024);
        if(approximate){packet.approximate_room_pose=approximate;
          packet.approximate_receipt_elapsed_s=lastLiveReadStarted!==null&&ended!==null&&ended>=lastLiveReadStarted?(ended-lastLiveReadStarted)/1000:null;}
        const points=a.keypoints,edges=a.skeleton_edges;
        if(a.layers?.skeleton?.available!==true||!Array.isArray(points)||points.length>512||!Array.isArray(edges)||edges.length>1024)continue;
        if(!points.every(p=>p&&Number.isFinite(p.x)&&Number.isFinite(p.y)&&p.x>=0&&p.y>=0&&p.x<=a.frame.width&&p.y<=a.frame.height)
          ||!edges.every(e=>Array.isArray(e)&&e.length===2&&e.every(i=>Number.isInteger(i)&&i>=0&&i<points.length)))continue;
        const times=original||(matched?s:null);
        packet.pose2d={frame_id:a.frame_id,observed_at:number(times?.observed_at),available_at:number(times?.available_at),
          valid_until:number(times?.valid_until),model_id:text(a.profile?.id,256)||null,source_id:'specialist_'+name.toLowerCase(),
          keypoints:points.map((p,i)=>({id:i,x:p.x,y:p.y,...(Number.isFinite(p.confidence)?{confidence:p.confidence}:{}),
            ...(Number.isInteger(p.joint_id)?{raw_joint_id:p.joint_id}:{}),...(typeof p.instance_id==='string'?{raw_instance_id:text(p.instance_id,256)}:{})})),
          skeleton_edges:copy(edges),status:text(a.status,64)||'unavailable'};
      }
      return packet;
    }
    async function turn(token){
      if(!valid(token)||busy||turning||pending)return;busy=true;turning=true;
      try{lastLiveReadStarted=monotonic();const live=await read('sessions.live',{},token);checkLive(live);
        if(!valid(token))return;
        frames.clear();frameObservations.clear();readImages.clear();lastLive=copy(live);
        const waiting=['requested','starting'].includes(live.session.state),recording=live.session.state==='recording';
        state={connection:recording?'connected':waiting?'connecting':'stopped',reason:null,perception:projection(live,lastGraph)};
        for(const key of cache.keys())if(![...frames.values()].some(f=>frameKey(f)===key))cache.delete(key);
        // Give the selected frame read priority over the larger graph response.
        // Subscribers can claim the single IO lane in this microtask checkpoint;
        // graph reads wait for that bounded media task, never overlap it.
        busy=false;publish();await Promise.resolve();
        if(mediaDone)await mediaDone;
        if(!valid(token))return;
        if(pending)fail('den_lab_read_busy');busy=true;
        const graph=await read('sessions.graph',{},token);checkGraph(graph,live);
        if(!valid(token))return;lastGraph=copy(graph);
        state={connection:recording?'connected':waiting?'connecting':'stopped',reason:null,perception:projection(live,graph)};
        publish();if(!recording&&!waiting)wanted=false;
      }catch(error){if(token===serial&&wanted&&!destroyed){const code=error?.code||'den_lab_unavailable';
        retired=error?.status===401||error?.status===403||['den_lab_response_binding_mismatch','den_lab_session_generation_changed','den_lab_session_state_unsupported','lab_stale_connection'].includes(code);
        if(retired){wanted=false;abandon('unavailable',code);}
        else{state={...state,connection:'unavailable',reason:code,recoverable:true};publish();}
      }}finally{busy=false;turning=false;if(valid(serial))schedule();}
    }
    function start(){if(destroyed||retired)fail('den_lab_stream_destroyed');if(wanted)return snapshot();wanted=true;serial++;
      if(selected()&&selected()!=='victoria')return stop('selected_site_changed');
      state={connection:env.document?.hidden?'paused':'connecting',reason:null,perception:blank()};publish();
      if(!env.document?.hidden&&!pending)void turn(serial);return snapshot();}
    function setArm(next){if(!['A','B'].includes(next))fail('den_lab_arm_required');selectedArm=next;
      if(lastLive&&lastGraph){state.perception=projection(lastLive,lastGraph);publish();}return snapshot();}
    function sampleClock(){const value=env.Date?.now?.();return Number.isFinite(value)?{now_s:value/1000,uncertainty_s:null,domain:'unix'}:null;}
    const frameKey=f=>[f.site_id,sessionId,f.generation,f.media_id,f.frame_id].join(':');
    async function readFrame(frame,{signal}={}){
      const token=serial,known=[...frames.values()].find(f=>frameKey(f)===frameKey(frame));
      if(!known||!valid(token)||!state.perception.frame||frameKey(state.perception.frame)!==frameKey(frame)
        ||Object.keys(known).some(k=>!same(known[k],frame[k])))fail('den_lab_current_frame_required');
      const key=frameKey(known),original=frameObservations.get(key);
      if(cache.has(key)){const value=cache.get(key);return {bytes:new Uint8Array(value.bytes),type:value.type,frame_metadata:copy(value.metadata)};}
      if(busy||pending)fail('den_lab_read_busy');if(readImages.size>=2&&!readImages.has(key))fail('den_lab_image_budget');readImages.add(key);
      const media=env.HomePerceptionLabMedia;if(typeof media?.readFrame!=='function')fail('den_lab_media_adapter_required');
      busy=true;let complete;mediaDone=new Promise(resolve=>{complete=resolve;});
      try{const value=await effect(async abort=>{
        const item=await transport.request('media.describe',{siteId:'victoria',sessionId,mediaId:known.media_id,generation:pinned},{signal:abort});
        const m=item?.metadata;
        if(item?.site_id!=='victoria'||item.session_id!==sessionId||item.generation!==pinned||(item.media_id||item.id)!==known.media_id
          ||m?.site_id!=='victoria'||m.camera_id!=='den'||m.frame_id!==known.frame_id
          ||original&&m.capture_epoch!==original.capture_epoch)fail('den_lab_frame_descriptor_mismatch');
        if(!valid(token)||signal?.aborted)fail('den_lab_read_abandoned');
        const image=await media.readFrame(item,{siteId:'victoria',sessionId,generation:pinned},abort);
        return {...image,metadata:{valid_until:number(m.valid_until),capture_epoch:typeof m.capture_epoch==='string'?text(m.capture_epoch,256):null,
          media_sha256:typeof item.sha256==='string'&&/^[a-f0-9]{64}$/.test(item.sha256)?item.sha256:null}};
      },token,signal);
        if(!state.perception.frame||frameKey(state.perception.frame)!==key)fail('den_lab_current_frame_required');
        cache.set(key,{bytes:new Uint8Array(value.bytes),type:value.type,metadata:value.metadata});
        frameMetadata(state.perception,value.metadata);publish();return {bytes:new Uint8Array(value.bytes),type:value.type,frame_metadata:copy(value.metadata)};
      }finally{busy=false;mediaDone=null;complete();if(valid(serial))schedule();}
    }
    const visibility=()=>{if(env.document?.hidden){if(wanted)abandon('paused','page_hidden');}else if(wanted){serial++;if(!pending)void turn(serial);}};
    const pagehide=()=>stop('page_hidden'),logout=()=>stop('owner_logout'),click=e=>{if(e.target?.closest?.('#logout'))logout();},siteChanged=()=>{if(selected()&&selected()!=='victoria')stop('selected_site_changed');};
    env.document?.addEventListener?.('visibilitychange',visibility);env.document?.addEventListener?.('click',click,true);
    env.addEventListener?.('pagehide',pagehide);env.addEventListener?.('home:logout',logout);const unsubscribe=env.HomeSpatialViewport?.subscribe?.(siteChanged);
    function destroy(){if(destroyed)return;stop('panel_unmounted');destroyed=true;env.document?.removeEventListener?.('visibilitychange',visibility);
      env.document?.removeEventListener?.('click',click,true);env.removeEventListener?.('pagehide',pagehide);env.removeEventListener?.('home:logout',logout);unsubscribe?.();}
    return Object.freeze({start,stop,destroy,snapshot,setArm,sampleClock,readFrame});
  }
  return Object.freeze({create,createObserver,PAGE_LIMIT:PAGE,POLL_INTERVAL_MS:GAP,OBSERVER_POLL_INTERVAL_MS:1000});
});
