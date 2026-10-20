/* The Apartment camera dock (APT-P01..P06). It shares the column with the
 * inspector and never covers it. Existing session connections remain
 * read-only; a separate explicit action owns one bounded viewing session.
 * The pure wording and honesty rules live in
 * home-apartment-perception-dock-model.js; this file owns the lifecycle. */
(function (global) {
  'use strict';
  const {useState,useRef,useEffect,useCallback}=React;
  const mono='"Geist Mono", "JetBrains Mono", monospace';
  const words=value=>String(value||'unavailable').replaceAll('_',' ');
  const shortRef=value=>value.length>12?`${value.slice(0,12)}…`:value;
  const model=()=>global.HomeApartmentPerceptionDockModel;
  // Den's wording for listed codes; an unlisted code reads Unavailable and
  // keeps the code itself beside it rather than being dropped.
  const placementText=code=>{const row=model()?.reason('perception.placement',code);return row?.known?row.text:`Unavailable · ${words(code)}`;};
  const button={minHeight:40,padding:'6px 10px',border:'1px solid var(--hg-border-soft, #35404a)',
    background:'#111922',color:'inherit',font:'inherit',cursor:'pointer'};
  const field={...button,width:'100%',marginTop:5};
  const note={fontSize:11.5,color:'var(--hg-fg-3, #a8b5c0)',lineHeight:1.5};
  const timestamp=value=>{
    const date=Number.isFinite(value)?new Date(value*1000):null;
    return date&&Number.isFinite(date.getTime())?date.toISOString():'unavailable';
  };
  // Ages read through Den's perception.age vocabulary; a number appears only
  // when the view qualified the clock.
  function ageText(age){
    if(!age||age.status==='unavailable')return model()?.reason('perception.age',age?.reason||'original_time_unavailable')?.text||'Age unavailable';
    if(age.status==='current'&&Number.isFinite(age.seconds)&&Number.isFinite(age.uncertainty_s))return `current · ${age.seconds.toFixed(1)} s old`;
    return `${words(age.status)}${age.reason?` · ${model()?.reason('perception.age',age.reason)?.text||words(age.reason)}`:''}`;
  }
  function resultText(value){
    if(value==null)return 'No result supplied';
    if(typeof value==='string')return value.slice(0,800);
    for(const key of ['activity','label','summary','text'])if(typeof value[key]==='string')return value[key].slice(0,800);
    try{return JSON.stringify(value).slice(0,800);}catch(_){return 'Result unavailable';}
  }
  function readStored(key,fallback){try{return global.localStorage?.getItem(key)||fallback;}catch(_){return fallback;}}
  function writeStored(key,value){try{global.localStorage?.setItem(key,value);}catch(_){}}
  function initialDetail(){
    try{const value=new URLSearchParams(global.location?.search||'').get('detail');if(value)return model()?.normalizeDetail(value)||'simple';}catch(_){}
    return model()?.normalizeDetail(readStored('apt.dock.detail','simple'))||'simple';
  }
  // The raw player owns native ESM import; production Babel's env preset
  // rewrites import() in this JSX file to an unavailable CommonJS require.
  const defaultLayer=()=>global.HomeApartmentPerceptionPlayer.loadLayer();

  function PoseOverlay({pose,frame,dim}){
    if(!pose?.renderable||!frame||(!pose.keypoints.length&&!pose.boxes?.length))return null;
    const byId=new Map(pose.keypoints.map(point=>[point.id,point]));
    const dash=dim?'8 6':undefined;
    return <svg aria-label="Exact-frame pose overlay" data-overlay-dim={dim?'1':'0'} viewBox={`0 0 ${frame.width} ${frame.height}`}
      style={{position:'absolute',inset:0,width:'100%',height:'100%',pointerEvents:'none',opacity:dim?.55:1}}>
      {(pose.boxes||[]).map((box,index)=><rect key={box.instance_id||index} data-person-instance={box.instance_id||''}
        x={box.x} y={box.y} width={box.w} height={box.h} fill="none" stroke="#85d6af" strokeWidth="2" strokeDasharray={dash}>
        <title>{`Frame-local person box ${index+1}`}</title></rect>)}
      {pose.edges.map(([a,b],index)=>{const start=byId.get(a),end=byId.get(b);return start&&end?
        <line key={index} x1={start.x} y1={start.y} x2={end.x} y2={end.y} stroke="#b8d8ff" strokeWidth="2" strokeDasharray={dash}/>:null;})}
      {pose.keypoints.map(point=><circle key={point.id} cx={point.x} cy={point.y} r="3" fill={dim?'none':'#b8d8ff'} stroke="#b8d8ff" strokeDasharray={dim?'2 2':undefined}/>)}
    </svg>;
  }
  function ModelResult({model:row,historical=false,scope=null}){
    return <details style={{borderTop:'1px solid var(--apt-border-soft, #26323d)',padding:'7px 0'}}>
      <summary style={{cursor:'pointer',lineHeight:1.5}}>{row.family||'Model'} · {historical?'recorded update · ':''}{words(row.status)}
        <span style={{display:'block',...note}}>{resultText(row.result)}</span></summary>
      <dl className="apt-dock-kv">
        <dt>Model</dt><dd>{row.model_id||'unavailable'}{row.model_revision?` · ${row.model_revision}`:''}</dd>
        <dt>Source</dt><dd>{row.source_id||'unavailable'}</dd>
        <dt>Observed</dt><dd>{timestamp(row.observed_at)}</dd>
        <dt>Available</dt><dd>{timestamp(row.available_at)}</dd>
        <dt>Expires</dt><dd>{timestamp(row.valid_until)}</dd>
        <dt>{historical?'Source frame':'Frame association'}</dt><dd>{historical
          ?row.frame_id||'unavailable':`${words(row.association)}${row.frame_id?` · ${row.frame_id}`:''}`}</dd>
        {scope&&<><dt>Session</dt><dd>{scope.sessionId} · generation {scope.generation}</dd></>}
      </dl>
      <div style={note}>{historical?'History only · original expiry above; no current-evidence authority':ageText(row.age)}{row.reason?` · ${words(row.reason)}`:''}</div>
    </details>;
  }
  function DockBadge({state,children}){
    const Badge=global.HomeApartmentUI?.Badge;
    return Badge?<Badge state={state}>{children}</Badge>:<span className="apt-ui-badge" data-state={state}>{children}</span>;
  }
  function Glyph({Icon,name,text}){return Icon?<Icon name={name}/>:text;}

  function HomeApartmentDenPerception({engine,active=true,mobile=false,source:injectedSource,
    loadLayer=defaultLayer,onAimPreviewController,onAimPreviewChange,legacyDeskAimPreview=false,
    capability:suppliedCapability=null,dockState:controlledDock,onDockState,detail:controlledDetail,onDetail,
    onOpenLab=null,Icon=null}){
    const source=injectedSource||global.HomeApartmentPerceptionSource;
    const [opened,setOpened]=useState(false),[sessions,setSessions]=useState([]),[sessionId,setSessionId]=useState(''),
      [arm,setArm]=useState('B'),[notice,setNotice]=useState(''),[discovering,setDiscovering]=useState(false),
      [state,setState]=useState(null),[anchor,setAnchor]=useState(null),[following,setFollowing]=useState(false),
      [followPaused,setFollowPaused]=useState(false),[layerReady,setLayerReady]=useState(false),
      [analysis,setAnalysis]=useState(()=>source?.analysisState?.()||{status:'idle',owned:false}),
      [aimPreview,setAimPreview]=useState({enabled:false,active:false,status:'disabled',reason:null}),
      [analysisAvailability,setAnalysisAvailability]=useState(null),
      [loopState,setLoopState]=useState(null),
      [modelHistory,setModelHistory]=useState([]),
      [ownDock,setOwnDock]=useState(()=>model()?.normalizeDockState(readStored('apt.dock.state','default'))||'default'),
      [ownDetail,setOwnDetail]=useState(initialDetail),[frameSource,setFrameSource]=useState('analyzed'),[traceStep,setTraceStep]=useState(null);
    const dock=model()?.normalizeDockState(controlledDock??ownDock)||'default',detail=model()?.normalizeDetail(controlledDetail??ownDetail)||'simple';
    const setDock=next=>{writeStored('apt.dock.state',next);if(onDockState)onDockState(next);else setOwnDock(next);};
    const setDetail=next=>{writeStored('apt.dock.detail',next);if(onDetail)onDetail(next);else setOwnDetail(next);};
    const playerRef=useRef(null),layerRef=useRef(null),discoveryRef=useRef(null),generationRef=useRef(0);
    const loopRef=useRef(null),loopBindingRef=useRef(null);
    const engineRef=useRef(engine),lastViewRef=useRef(null),analysisGenerationRef=useRef(0),analysisBindingRef=useRef(null),activeRef=useRef(active);
    const aimPreviewRef=useRef(null),aimPreviewEngineRef=useRef(null),aimRegistrationRef=useRef(onAimPreviewController),aimChangeRef=useRef(onAimPreviewChange);
    // The owner's arm survives only a reconnect; every other stop disarms.
    const aimArmedRef=useRef(false);
    engineRef.current=engine;activeRef.current=active;
    aimRegistrationRef.current=onAimPreviewController;aimChangeRef.current=onAimPreviewChange;
    const identity=engine?.sceneIdentity;
    const identityKey=[identity?.siteId,identity?.contentRevision,identity?.frameDigest,identity?.registrationVersion].join('|');
    const capability=model()?.perceptionCapability({siteId:engine?.siteId,capabilities:suppliedCapability})||{variant:'none',cameraId:null,body:{}};
    const siteId=engine?.siteId,cameraId=capability.cameraId;

    const releaseAimPreview=useCallback(()=>{
      const controller=aimPreviewRef.current,previewEngine=aimPreviewEngineRef.current;
      aimPreviewRef.current=null;aimPreviewEngineRef.current=null;
      if(controller){aimRegistrationRef.current?.(controller,false);controller.dispose();aimChangeRef.current?.(previewEngine,controller.readback());}
    },[]);
    const stop=useCallback((reason='viewer_disconnected')=>{
      if(!['new_connection','activity_loop_waiting'].includes(reason)){
        loopRef.current?.();loopRef.current=null;loopBindingRef.current=null;setLoopState(null);
      }
      generationRef.current++;if(reason!=='new_connection')aimArmedRef.current=false;
      releaseAimPreview();setAimPreview({enabled:false,active:false,status:'disabled',reason:null});
      discoveryRef.current?.abort();discoveryRef.current=null;setDiscovering(false);
      playerRef.current?.destroy();playerRef.current=null;
      layerRef.current?.dispose();layerRef.current=null;
      lastViewRef.current=null;setState(null);setAnchor(null);setFollowing(false);setFollowPaused(false);setLayerReady(false);setTraceStep(null);
      setModelHistory([]);
      setNotice(reason==='viewer_disconnected'?'Disconnected from this view. The Lab session is unchanged.':
        reason==='dock_hidden'?'Camera dock hidden. The viewer disconnected; the Lab session is unchanged.':
        reason==='opened_in_lab'?'Opened in Perception Lab. This view disconnected; Connect again when you are back.':'');
    },[releaseAimPreview]);
    useEffect(()=>()=>{generationRef.current++;loopRef.current?.();releaseAimPreview();discoveryRef.current?.abort();playerRef.current?.destroy();layerRef.current?.dispose();},[releaseAimPreview]);
    useEffect(()=>{stop('view_identity_changed');setSessionId('');setSessions([]);setOpened(false);},[engine,identityKey,active,source,stop]);
    // Hidden detaches the viewer (no invisible media). Collapsed keeps it.
    useEffect(()=>{if(dock==='hidden'&&(playerRef.current||loopRef.current))stop('dock_hidden');},[dock,stop]);
    useEffect(()=>source?.watchAnalysis?.(setAnalysis),[source]);
    useEffect(()=>{
      analysisGenerationRef.current++;setAnalysisAvailability(null);
      return()=>{analysisGenerationRef.current++;if(analysisBindingRef.current)source?.releaseAnalysis?.(analysisBindingRef.current);analysisBindingRef.current=null;};
    },[engine,identityKey,active,source]);
    useEffect(()=>{
      const hidden=()=>{if(global.document.hidden)stop('page_hidden');};
      const logout=()=>{stop('owner_logout');if(analysisBindingRef.current)source?.releaseAnalysis?.(analysisBindingRef.current);setOpened(false);setSessions([]);setSessionId('');};
      const site=()=>{if(global.HomeSpatialViewport?.getSelectedSiteId?.()!==engineRef.current?.siteId)stop('selected_site_changed');};
      const logoutClick=event=>{if(event.target?.closest?.('#logout'))logout();};
      global.document.addEventListener('visibilitychange',hidden);
      global.document.addEventListener('click',logoutClick,true);
      global.addEventListener('pagehide',logout);global.addEventListener('home:logout',logout);
      const unsubscribe=global.HomeSpatialViewport?.subscribe?.(site);
      return()=>{global.document.removeEventListener('visibilitychange',hidden);global.document.removeEventListener('click',logoutClick,true);
        global.removeEventListener('pagehide',logout);global.removeEventListener('home:logout',logout);unsubscribe?.();};
    },[stop,source]);
    useEffect(()=>{
      if(!opened||!active)return undefined;
      const timer=global.setInterval(()=>{
        setAnchor(layerRef.current?.screenAnchor()||null);
        const readback=layerRef.current?.readback();setFollowing(readback?.following===true);setFollowPaused(readback?.followPaused===true);
      },120);
      const canvas=engine?.renderer?.domElement;
      const manual=()=>{layerRef.current?.setFollowing(false);setFollowing(false);setFollowPaused(false);};
      canvas?.addEventListener('pointerdown',manual);canvas?.addEventListener('wheel',manual,{passive:true});
      return()=>{global.clearInterval(timer);canvas?.removeEventListener('pointerdown',manual);canvas?.removeEventListener('wheel',manual);};
    },[engine,opened,active]);

    function analysisOptions(){
      const selectedEngine=engine,generation=analysisGenerationRef.current,selected={...engine?.sceneIdentity};
      return {identity:selected,generation,isCurrent:()=>activeRef.current&&engineRef.current===selectedEngine&&analysisGenerationRef.current===generation
        &&['siteId','contentRevision','frameDigest','registrationVersion'].every(key=>engineRef.current?.sceneIdentity?.[key]===selected[key])};
    }
    async function discover(){
      setOpened(true);setDiscovering(true);setNotice('');
      discoveryRef.current?.abort();const abort=new AbortController();discoveryRef.current=abort;
      const token=generationRef.current;
      try{
        if(typeof source?.listSessions!=='function')throw Error('Perception source is unavailable');
        const [listResult,availableResult]=await Promise.allSettled([
          source.listSessions({siteId,signal:abort.signal}),
          typeof source.analysisAvailability==='function'?source.analysisAvailability({...analysisOptions(),signal:abort.signal}):Promise.resolve(null)]);
        if(abort.signal.aborted||engineRef.current!==engine||token!==generationRef.current)return;
        setAnalysisAvailability(availableResult.status==='fulfilled'?availableResult.value:{available:false,reason:availableResult.reason?.message||'analysis_availability_unavailable'});
        if(listResult.status!=='fulfilled')throw listResult.reason;
        const result=listResult.value;
        const rows=(Array.isArray(result)?result:result?.items||result?.sessions||[])
          .filter(row=>row.site_id===siteId&&row.camera_id===cameraId&&Number.isSafeInteger(row.generation)&&row.generation>=0).slice(0,100);
        setSessions(rows);
        const activeRows=rows.filter(row=>['recording','running','starting'].includes(row.state));
        if(!sessionId&&activeRows.length===1)setSessionId(activeRows[0].id);
        if(!rows.length)setNotice('No existing Den session.');
      }catch(error){if(!abort.signal.aborted&&token===generationRef.current)setNotice(error.message||'Session discovery unavailable');}
      finally{if(discoveryRef.current===abort){discoveryRef.current=null;if(!abort.signal.aborted)setDiscovering(false);}}
    }
    async function connect(options={}){
      if(!options.activityLoop){loopRef.current?.();loopRef.current=null;loopBindingRef.current=null;setLoopState(null);}
      stop('new_connection');setNotice('');
      const session=options.session||sessions.find(row=>row.id===sessionId),selectedArm=options.arm||arm;
      if(!session){setNotice('Select an existing Den session first.');return;}
      const token=++generationRef.current;
      try{
        const projection=global.HomeApartmentPerceptionView,players=global.HomeApartmentPerceptionPlayer;
        if(!projection||!players||typeof source?.subscribe!=='function')throw Error('Perception viewer modules unavailable');
        const scope=projection.createScope(engine.sceneIdentity,session);
        const scopedSource={subscribe:args=>source.subscribe({...args,arm:selectedArm,previewArmed:()=>aimArmedRef.current===true}),sampleClock:()=>source.sampleClock(),
          readFrame:(frame,options)=>source.readFrame(frame,options)};
        const collisionDigest=global.HomeContentPacks?.get?.(identity.siteId,identity.contentRevision)?.manifest?.assets?.['collision.glb']?.sha256||null;
        // Retired by the lighting brain (AL-4): the fixed-desk preview runs only when explicitly opted in.
        if(legacyDeskAimPreview&&global.HomeApartmentDeskAimPreview?.create&&global.HomeApartmentAiming?.solveAim&&engine.overlay){
          try{
            const current=()=>token===generationRef.current&&engineRef.current===engine;
            const committed=()=>(engine.apartmentRoot?.children||[]).some(group=>group.visible!==false&&
              (group.name==='committed-room-fixtures'||group.name==='home-live-geometry-owned'&&
                group.children?.some(child=>child.visible!==false&&child.userData?.truth_role==='virtual_execution')));
            const controller=global.HomeApartmentDeskAimPreview.create({overlay:engine.overlay,
              solveAim:global.HomeApartmentAiming.solveAim,
              getContext:()=>({active:current()&&activeRef.current,identity:engine.sceneIdentity,
                hidden:global.document.hidden,committedOwnerActive:committed()}),
              nowMs:()=>global.performance.now(),setTimeout:(fn,ms)=>global.setTimeout(fn,ms),clearTimeout:id=>global.clearTimeout(id),
              onChange:next=>{if(current()){aimArmedRef.current=next.enabled===true;setAimPreview(next);aimChangeRef.current?.(engine,next);}}});
            aimPreviewRef.current=controller;aimPreviewEngineRef.current=engine;aimRegistrationRef.current?.(controller,true);
            if(aimArmedRef.current)controller.setEnabled(true);
          }catch(_){setAimPreview({enabled:false,active:false,status:'unavailable',reason:'desk_preview_controls_unavailable'});}
        }
        // Render the supplied viewing joints; the coordinator's reduced
        // shoulders/head event remains a separate contract and authority.
        const roomView=view=>model()?.roomLayerView(view)||view;
        const player=players.create({source:scopedSource,scope,collisionDigest,onChange:next=>{
          if(token!==generationRef.current||engineRef.current!==engine)return;
          lastViewRef.current=next.view;setState(next);
          setModelHistory(previous=>model()?.modelUpdates(previous,next.view)||previous);
          layerRef.current?.draw(roomView(next.view));
          aimPreviewRef.current?.update(next.view?.virtualTargetPreview,next.view?.deskPreviewEvidence);
          const readback=layerRef.current?.readback();setFollowing(readback?.following===true);setFollowPaused(readback?.followPaused===true);
        }});
        playerRef.current=player;player.start();
        const module=await loadLayer();
        if(token!==generationRef.current||engineRef.current!==engine)return;
        const layer=module.attachApartmentPerceptionLayer(engine,scope,{isCurrent:()=>token===generationRef.current&&engineRef.current===engine,
          reducedMotion:()=>global.matchMedia?.('(prefers-reduced-motion: reduce)').matches===true});
        layerRef.current=layer;setLayerReady(true);if(lastViewRef.current)layer.draw(roomView(lastViewRef.current));
      }catch(error){if(token===generationRef.current)setNotice(error.message||'Perception view unavailable');}
    }
    function viewActivityLoop(){
      if(typeof source?.watchActivityLoop!=='function')return;
      stop('activity_loop_selected');setOpened(true);setLoopState({status:'connecting',reason:null});
      setFrameSource(global.HomeApartmentOwnerCamera?'live':'analyzed');
      const options=analysisOptions();
      loopRef.current=source.watchActivityLoop({...options,onChange:next=>{
        if(!options.isCurrent())return;
        setLoopState(next);
        if(next.status!=='running'||!next.session){
          loopBindingRef.current=null;stop('activity_loop_waiting');return;
        }
        const row=next.session,key=[next.producerEpoch,row.id,row.generation].join(':');
        if(loopBindingRef.current===key)return;
        loopBindingRef.current=key;setSessions(previous=>[row,...previous.filter(item=>item.id!==row.id)].slice(0,100));
        setSessionId(row.id);setArm('B');
        void connect({session:row,arm:'B',activityLoop:true}).then(()=>{
          if(!playerRef.current&&loopBindingRef.current===key)loopBindingRef.current=null;
        });
      }});
    }
    async function ownAnalysis(reconcile=false){
      const options=analysisOptions();analysisBindingRef.current={identity:options.identity,generation:options.generation};setNotice('');
      try{
        const result=await (reconcile?source.reconcileAnalysis(options):source.startAnalysis(options));
        if(!options.isCurrent()||!result.adoptable||!result.session)return;
        const row=result.session;setSessions(previous=>[...previous.filter(item=>item.id!==row.id),row]);setSessionId(row.id);setArm('B');
        await connect({session:row,arm:'B'});
      }catch(error){if(options.isCurrent())setNotice(error.message||'Live analysis unavailable');}
    }
    async function stopOwnAnalysis(){
      const ownerId=analysis.session?.id;
      aimPreviewRef.current?.setEnabled(false);
      try{await source.stopAnalysis();if(ownerId&&ownerId===sessionId)stop('owned_analysis_stopped');}
      catch(error){setNotice(error.message||'Owned analysis stop not confirmed. Retry Stop own analysis.');}
    }
    function changeSelection(value,kind){stop('selection_changed');if(kind==='arm')setArm(value);else setSessionId(value);}
    function close(){stop();if(analysisBindingRef.current)source?.releaseAnalysis?.(analysisBindingRef.current);setOpened(false);}
    function toggleAimPreview(){
      const controller=aimPreviewRef.current;if(!controller)return;
      const enabled=!controller.readback().enabled;
      controller.setEnabled(enabled);
      if(enabled&&lastViewRef.current)controller.update(lastViewRef.current.virtualTargetPreview,lastViewRef.current.deskPreviewEvidence);
      setAimPreview(controller.readback());
    }
    if(!active||capability.variant!=='exact-frame'||!model())return null;
    const M=model();
    const view=state?.view,room=view?.roomPose,diagnosticRoom=view?.diagnosticRoomPose,approximateRoom=view?.approximateRoomPose,
      approximateActive=approximateRoom?.renderable===true&&room?.admitted!==true&&diagnosticRoom?.renderable!==true,
      models=view?.models||[],jevRows=models.filter(row=>/jev/i.test(`${row.family} ${row.model_id} ${row.source_id}`)),
      metricInput=models.find(row=>row.source_id==='apartment_viewing_metric'),
      approximateReason=approximateRoom?.reason&&approximateRoom.reason!=='approximate_pose_unavailable'?approximateRoom.reason:
        ['unavailable','stopped'].includes(metricInput?.status)?metricInput.reason:null;
    const sessionLabelState=session=>analysis.session?.id===session.id&&analysis.session.generation===session.generation
      &&analysis.session.site_id===siteId&&analysis.session.camera_id===cameraId?analysis.session.state:session.state;
    const frame=state?.frame,session=sessions.find(row=>row.id===sessionId)||null;
    const timing=M.frameTiming(view),frameState=M.frameState(view,state,timing),connected=!!state;
    const badge=M.badge(frameState,timing,session,state?.connection);
    const detector=M.detector(view),pose=M.pose(view),jev=M.jev(view,capability);
    const steps=M.trace({frameState,detector,jev,detail,connected,frameId:frame?.frame_id});
    const replayLabel=M.replayLabel(session);
    const summary=M.summary({frameState,jev,detector,discovered:opened,connected,sessions:sessions.length,replayLabel});
    const body=approximateActive?M.bodyInRoom(approximateRoom):null;
    const labIntent=M.labIntent({session,frame:frame?{frame_id:frame.frame_id,captured_at:frame.captured_at}:null});
    const followGate=capability.body?.tracking==='continuous_registered';
    const stale=frameState==='stale',detailed=detail==='detailed',isOpen=dock==='default'||dock==='expanded';
    const LiveCamera=global.HomeApartmentOwnerCamera;
    const width=mobile?Math.max(240,Math.min(340,(global.innerWidth||360)-24)):340;
    const screenWidth=global.innerWidth||1024;
    const x=anchor?.visible&&!mobile?Math.max(12,Math.min(
      anchor.x+72+width<=screenWidth-12?anchor.x+72:anchor.x-width-72,screenWidth-width-12)):null;
    const y=anchor?.visible&&!mobile?Math.max(76,Math.min(anchor.y-110,Math.max(76,(global.innerHeight||800)-590))):76;
    const style={position:'absolute',zIndex:14,right:x===null?12:undefined,left:x===null?undefined:x,top:y,
      width,maxWidth:'calc(100% - 24px)',maxHeight:mobile?'58%':`calc(100% - ${y+74}px)`,overflowY:'auto',
      padding:12,background:'rgba(9,16,23,.96)',border:'1px solid #35404a',color:'var(--hg-fg, #dbe7f1)',
      fontFamily:mono,fontSize:11.5,overflowWrap:'anywhere',pointerEvents:'auto'};
    // Lab covers the room, so this viewer disconnects; the session list stays
    // and the Lab session itself is unchanged.
    const openLab=intent=>{if(!onOpenLab)return;if(playerRef.current)stop('opened_in_lab');onOpenLab(intent);};
    const approximateBadge=approximateActive&&<div role="status" aria-label="Approximate room placement" data-apartment-approximate-badge="1"
      style={{position:'absolute',zIndex:14,top:68,left:'50%',transform:'translateX(-50%)',maxWidth:'calc(100% - 32px)',
        padding:'6px 10px',border:'1px solid #8d6d39',background:'rgba(9,16,23,.96)',color:'#e9bd7e',fontFamily:mono,fontSize:11.5,pointerEvents:'none'}}>Approximate room placement</div>;
    const bodyLabel=body&&<div role="status" data-apartment-body-label="1" data-body-stale={stale||frameState==='replay'?'1':'0'}
      style={{position:'absolute',zIndex:14,top:104,left:'50%',transform:'translateX(-50%)',maxWidth:'calc(100% - 32px)',
        padding:'4px 9px',border:'1px dashed #8d6d39',background:'rgba(9,16,23,.9)',color:'#e9bd7e',fontFamily:mono,fontSize:11.5,pointerEvents:'none',opacity:stale?.6:1}}>
      {body.label}</div>;

    if(dock==='hidden')return <>
      {approximateBadge}{bodyLabel}
      <aside aria-label="Den camera dock" data-apartment-den-perception="1" data-dock-state="hidden" className="apt-dock is-hidden">
        <button type="button" className="apt-ui-button apt-dock-show" data-dock-show-camera="1" onClick={()=>setDock('default')}>Show camera</button>
      </aside></>;

    const header=<header className="apt-ui-dock-header" data-collapsed={dock==='collapsed'?'true':'false'}>
      <div className="apt-ui-grow apt-dock-title"><h2 className="apt-ui-heading">Den camera</h2>
        <DockBadge state={badge.state}>{badge.label}</DockBadge></div>
      <button type="button" className="apt-ui-button is-icon" aria-label={dock==='expanded'?'Restore camera dock size':'Expand camera dock'}
        aria-pressed={dock==='expanded'} onClick={()=>setDock(dock==='expanded'?'default':'expanded')}>
        <Glyph Icon={Icon} name={dock==='expanded'?'collapse':'expand'} text={dock==='expanded'?'Restore':'Expand'}/></button>
      <button type="button" className="apt-ui-button is-icon" aria-label={dock==='collapsed'?'Show camera dock':'Collapse camera dock'}
        aria-expanded={dock!=='collapsed'} onClick={()=>setDock(dock==='collapsed'?'default':'collapsed')}>
        <Glyph Icon={Icon} name={dock==='collapsed'?'chevU':'chevD'} text={dock==='collapsed'?'Open':'Collapse'}/></button>
      {isOpen&&<button type="button" className="apt-ui-button is-icon" data-dock-details="1" aria-label={detailed?'Hide details':'Show details'}
        aria-pressed={detailed} title="Details: detector, pose and JEV values, frame metadata and raw results" onClick={()=>setDetail(detailed?'simple':'detailed')}>
        <Glyph Icon={Icon} name="info" text="Details"/></button>}
      <button type="button" className="apt-ui-button is-icon" aria-label="Hide camera dock" onClick={()=>setDock('hidden')}>
        <Glyph Icon={Icon} name="close" text="Hide"/></button>
    </header>;

    const sessionControls=opened&&<section aria-label="Den session" className="apt-dock-section">
      {typeof source?.watchActivityLoop==='function'&&<div className="apt-dock-analysis" aria-label="Existing activity loop">
        <button type="button" style={button} className="apt-ui-button" onClick={viewActivityLoop}>View activity loop</button>
        <p style={note}>Views the existing analysis and follows its recording rolls. No recording or model starts here.</p>
        {loopState&&<p role="status" data-activity-loop-status={loopState.status}>{words(loopState.status)}
          {loopState.reason?` · ${words(loopState.reason)}`:''}{Number.isSafeInteger(loopState.rolls)?` · roll ${loopState.rolls}`:''}</p>}
      </div>}
      {typeof source?.startAnalysis==='function'&&<div aria-label="Live analysis" role="group" className="apt-dock-analysis">
        {(analysisAvailability?.available===true||analysis.owned)&&<div className="apt-dock-row">
          <button type="button" style={button} className="apt-ui-button" disabled={!analysisAvailability?.available||analysis.owned} onClick={()=>ownAnalysis()}>Start live analysis (90 s)</button>
          <button type="button" style={button} className="apt-ui-button" disabled={!analysis.owned} onClick={stopOwnAnalysis}>Stop own analysis</button>
          {analysis.owned&&analysis.requestId&&['uncertain','stop_failed','unavailable','stopping'].includes(analysis.status)&&
            <button type="button" style={button} className="apt-ui-button" onClick={()=>ownAnalysis(true)}>Check analysis status</button>}
        </div>}
        <div role="status" aria-live="polite" style={note}>Own analysis: {words(analysis.status)}{analysis.reason?` · ${words(analysis.reason)}`:''}
          {analysisAvailability?.available===true||analysis.owned?' · stops after 90 seconds and does not renew':''}</div>
        {!analysisAvailability?.available&&<div style={note}>{analysisAvailability?.reason?words(analysisAvailability.reason):'Live analysis availability not confirmed'} · no live analysis can start here</div>}
        {analysis.owned&&<div style={note}>Only this Apartment-created session can be stopped here. Existing Lab sessions remain unchanged.</div>}
      </div>}
      {sessions.length>0&&<><label>Lab session<select aria-label="Den Lab session" value={sessionId} onChange={event=>changeSelection(event.target.value,'session')} style={field}>
        <option value="">Select existing session</option>{sessions.map(row=><option key={row.id} value={row.id}>
          {row.id.slice(-12)} · {sessionLabelState(row)}</option>)}</select></label>
      <div className="apt-dock-row" style={{alignItems:'end'}}>
        <label style={{flex:1}}>Pipeline arm<select aria-label="Perception pipeline arm" value={arm} onChange={event=>changeSelection(event.target.value,'arm')} style={field}>
          <option value="A">A</option><option value="B">B</option></select></label>
        <button type="button" style={button} className="apt-ui-button" disabled={discovering} onClick={discover}>{discovering?'Reading…':'Refresh sessions'}</button>
      </div>
      <div className="apt-dock-row">
        <button type="button" style={button} className="apt-ui-button" disabled={!sessionId||['connecting','live','recording'].includes(state?.connection)} onClick={()=>connect()}>Connect</button>
        <button type="button" style={button} className="apt-ui-button" disabled={!state} onClick={()=>stop()}>Disconnect</button>
      </div></>}
      <div className="apt-dock-row"><button type="button" style={button} className="apt-ui-button" aria-label="Close camera view" onClick={close}>Close</button></div>
      {(state||sessions.length>0)&&<div role="status" aria-live="polite" style={note}>{words(state?.connection||'disconnected')}{state?.reason?` · ${words(state.reason)}`:''}</div>}
      {state?.mediaReason&&state.mediaReason!==state.reason&&<div role="status" style={note}>{words(state.mediaReason)} · waiting for the next analyzed frame</div>}
      {notice&&<p role="status" style={note}>{notice}</p>}
    </section>;

    const emptyState=opened&&!discovering&&!sessions.length&&<section className="apt-dock-callout" data-dock-empty="no-session" aria-label="No perception session">
      <h3 className="apt-ui-heading">No perception session</h3>
      <p className="apt-ui-copy">Home isn't viewing a Den camera session. Sessions are recorded and reviewed in Perception Lab; this dock only shows their analyzed frames.</p>
      <div className="apt-dock-row"><button type="button" style={button} className="apt-ui-button" disabled={!onOpenLab} onClick={()=>openLab({session_id:null,t_s:null})}>Open in Lab</button>
        <button type="button" style={button} className="apt-ui-button" onClick={discover}>Refresh sessions</button></div>
      {!onOpenLab&&<p style={note}>Perception Lab isn't available from this view.</p>}
    </section>;

    const frameView=opened&&(connected||LiveCamera)&&<section aria-label="Analyzed frame" className="apt-dock-section">
      <div className="apt-dock-row" role="radiogroup" aria-label="Frame source">
        {[['analyzed','Analyzed frame'],['live','Live camera']].map(([value,label])=><button key={value} type="button" role="radio" style={button}
          className="apt-ui-button apt-dock-seg" aria-checked={frameSource===value} disabled={value==='live'&&!LiveCamera}
          title={value==='live'&&!LiveCamera?'Live camera unavailable in this view':undefined} onClick={()=>setFrameSource(value)}>{label}</button>)}
      </div>
      {frameSource==='live'&&LiveCamera?<div data-dock-live="1"><LiveCamera active={active&&isOpen} identityKey={identityKey}/>
        <p style={note}>Live video is a separate source. The analyzed skeleton and boxes are never drawn on it.</p></div>:!connected?
        <p style={note} data-frame-state="none">No analyzed frame · connect a Den session to see one.</p>:<>
      <figure className={`apt-dock-frame${stale?' is-stale':''}${frameState==='replay'?' is-replay':''}`} data-frame-state={frameState}
        onClick={()=>setTraceStep('seen')}
        style={{position:'relative',margin:0,background:'#05090c',aspectRatio:frame?`${frame.width} / ${frame.height}`:'16 / 9'}}>
        {state?.imageUrl&&frame?<><img src={state.imageUrl} alt={`Analyzed Den camera frame ${frame.frame_id}`}
          style={{display:'block',width:'100%',height:'100%',objectFit:'contain',filter:stale?'grayscale(1) brightness(.7)':undefined}}/>
          <PoseOverlay pose={view?.pose2d} frame={frame} dim={stale}/></>:<div style={{...note,padding:16}}>Exact analyzed camera frame unavailable</div>}
        {frame&&<figcaption className="apt-dock-stamp" data-frame-stamp="1">Frame {frame.frame_id} · Pose: {ageText(view?.pose2d?.age)}</figcaption>}
      </figure>
      <div className="apt-dock-row">
        <span style={{...note,flex:1}}>{frame?`Captured ${timestamp(frame.captured_at)}`:'No decoded frame'}</span>
        <button type="button" style={button} className="apt-ui-button" disabled title="No calibrated pose for this camera; it is never derived from room or socket positions">From camera</button>
      </div>
      <div data-frame-timing={timing.age.status} data-frame-qualified={timing.qualified?'1':'0'} style={note}>
        {timing.text}{!timing.qualified&&timing.age.status!=='stale'?' · ':''}{!timing.qualified&&timing.age.status!=='stale'?'no age band is shown without a qualified clock':''}</div>
      {stale&&<p role="status" data-frame-not-used="1" className="apt-dock-attn">Not used for lighting. {timing.reason?.text||'This frame is not current'}.</p>}
      {state?.heldFrame&&<div style={note}>Showing the preceding analyzed frame while the next exact frame decodes.</div>}
      {frameState==='replay'&&<p role="status" data-frame-replay="1" className="apt-dock-attn">{replayLabel}. Recorded session · nothing here is live · not used for lighting.</p>}
      {frameState!=='replay'&&!stale&&frame&&<p style={note}>Viewing only · not used for lighting by this dock.</p>}
      {frame&&view?.pose2d?.renderable&&view.pose2d.keypoints.length===0&&
        <div role="status" style={note}>No supported person joints in this analyzed frame</div>}
      </>}
    </section>;

    const traceView=connected&&<section aria-label="Evidence to outcome" className="apt-dock-section">
      <div className="apt-dock-trace" role="group" aria-label="Seen, interpreted, proposed, did">
        {steps.map(step=><button key={step.key} type="button" className="apt-dock-step" data-trace-step={step.key} data-tone={step.tone||'normal'}
          aria-pressed={traceStep===step.key} onClick={()=>setTraceStep(traceStep===step.key?null:step.key)}>
          <span className="k">{step.label}</span><span className="v" data-trace-value={step.key}>{step.value}</span></button>)}
      </div>
      {traceStep&&(()=>{const step=steps.find(row=>row.key===traceStep);return <div role="status" data-trace-detail={traceStep} style={note}>
        {traceStep==='seen'?<>{detector.text} · {pose.text}{body?` · ${body.text}`:''}</>:
          traceStep==='interpreted'?<>{jev.status==='judged'?`JEV reads ${jev.label.toLowerCase()}`:jev.status==='abstained'?'JEV abstained':'JEV result not available'}{step.reason?` · ${step.reason}`:''}</>:
          traceStep==='proposed'?<>Nothing proposed · {step.reason}</>:
          <>{step.reason} The den's virtual fixtures follow the lighting brain; its panel says why each light changed.</>}
      </div>;})()}
      {steps.filter(step=>step.reason&&step.key!=='did').map(step=><div key={step.key} data-trace-reason={step.key} style={note}>{step.label}: {step.reason}</div>)}
    </section>;

    const inference=connected&&<section aria-label="Inference" className="apt-dock-section">
      <div className="apt-dock-mini" data-inference="detector">{detector.text}
        {detailed&&detector.scores.length>0&&<> · detector raw score{detector.scores.length>1?'s':''} <span className="mono">{detector.scores.map(value=>value.toFixed(2)).join(', ')}</span> (not calibrated)</>}</div>
      <div className="apt-dock-mini" data-inference="pose">{pose.text}{detailed&&pose.model?<> · model <span className="mono">{pose.model}</span></>:null}</div>
      <div className="apt-dock-mini" data-inference="jev" data-jev-status={jev.status}>
        {jev.status==='judged'?<>JEV reads <b>{jev.label.toLowerCase()}</b>{detailed&&Number.isFinite(jev.probability)?<> · JEV probability <span className="mono">{jev.probability.toFixed(2)}</span></>:null}</>:
          jev.status==='abstained'?<>JEV <b>abstained</b>: {jev.reasons.join(' · ')}</>:<>JEV: not available · {jev.reasons.join(' · ')}</>}
        {jev.window&&<> · {jev.window.usable!=null?`${jev.window.usable} usable`:''}{jev.window.frames!=null?` of ${jev.window.frames} frames`:''}{jev.window.window_s!=null?` in ${jev.window.window_s} s`:''}{jev.window.min_usable!=null?` (needs ${jev.window.min_usable})`:''}</>}
      </div>
      {jev.health&&<div style={note} data-jev-health={jev.health.code}>{jev.health.text}</div>}
      {(detailed||dock==='expanded')&&jev.distribution.length>0&&<div className="apt-dock-dist" aria-label="JEV distribution">
        {jev.distribution.map((row,index)=><div key={row.activity} className={`apt-dock-dist-row${index===0?' top':''}`}><span>{row.label}</span>
          <span className="bar"><i style={{width:`${Math.round(row.p*100)}%`}}/></span><span className="mono">{row.p.toFixed(2)}</span></div>)}
        {jev.reasons.length>0&&<ul style={note}>{jev.reasons.map(text=><li key={text}>{text}</li>)}</ul>}
      </div>}
      <p style={note}>JEV probabilities are JEV's judgment, not detector or pose scores. Raw model responses are not calibrated probabilities.</p>
    </section>;

    const roomPlacement=connected&&<section aria-label="Body in the room" className="apt-dock-section">
      <div style={{...note,color:room?.diagnostic||diagnosticRoom?.renderable||approximateActive?'var(--apt-attn, #e9bd7e)':'var(--hg-fg-2, #b8d8ff)'}}>
        {approximateActive?<span data-apartment-approximate-note="1">Approximate room placement - {body?.text} - {body?.label} - timing unverified - no Follow or lighting authority</span>:
          diagnosticRoom?.renderable?<span aria-label="Diagnostic room pose">{diagnosticRoom.timingLabel} · {diagnosticRoom.joints.length} supplied joints · no Follow or lighting authority</span>:
          diagnosticRoom?.reason&&diagnosticRoom.reason!=='diagnostic_pose_unavailable'?`Diagnostic room pose unavailable · ${words(diagnosticRoom.reason)}`:
          room?.admitted?(room.diagnostic?'Diagnostic room alignment · no lighting authority':'Registered room pose · viewing only'):
          approximateReason?<span data-apartment-approximate-unavailable="1">Approximate room placement unavailable - {placementText(approximateReason)}</span>:
          `3D room position unavailable · ${words(room?.reason||'camera-to-room registration required')}`}
      </div>
      <div className="apt-dock-row">
        <button type="button" style={button} className="apt-ui-button" aria-pressed={following} disabled={!followGate||(!view?.follow?.allowed&&!following)||!layerReady}
          title={followGate?undefined:'Follow needs continuous registered tracking, which no producer supplies yet'}
          onClick={()=>{layerRef.current?.setFollowing(!following);const next=layerRef.current?.readback();setFollowing(next?.following===true);setFollowPaused(next?.followPaused===true);}}>Follow</button>
        <span style={{...note,flex:1}}>{!followGate?'Follow is off until continuous registered tracking exists.':followPaused?'Follow paused while the matching analyzed image decodes.':
          !view?.follow?.allowed?'Follow unavailable until a current registered room joint is supplied.':''}</span>
      </div>
    </section>;

    const persons=frame&&view?.pose2d?.renderable&&<section data-apartment-persons="1" aria-label="People in this analyzed frame" style={{...note,marginTop:6}}>
      {view.pose2d.personAssociation?.status==='unavailable'&&<div data-person-association-status="unavailable">
        Anonymous association unavailable · {words(view.pose2d.personAssociation.reason)}</div>}
      {view.pose2d.personAssociation?.timing_status==='unverified'&&<div>Association timing unverified · viewing only</div>}
      {view.pose2d.personSemantics?.scene&&<div data-person-scene-facts="1">Scene people · {words(view.pose2d.personSemantics.scene.people_visible)}</div>}
      {view.pose2d.personSemantics?.timing_status==='unverified'&&<div>Interpretation timing unverified · viewing only</div>}
      {view.pose2d.personJudgments?.timing_status==='unverified'&&<div>Judgment timing unverified · viewing only</div>}
      {view.pose2d.personSemantics&&<div data-person-decision-status="unavailable">Decision unavailable · {words(view.pose2d.personSemantics.decision.reason)}</div>}
      {(view.pose2d.personAssociation?.retired||[]).map(item=><div key={item.track_ref} data-retired-anonymous-ref={item.track_ref}>
        Anonymous reference <span title={item.track_ref}>{shortRef(item.track_ref)}</span> retired · {words(item.reason)}</div>)}
      {view.pose2d.personsReason?<div>Per-person detail unavailable · {words(view.pose2d.personsReason)}</div>:
        !view.pose2d.persons.length?<div>No detected people in this analyzed frame</div>:
        view.pose2d.persons.map((person,index)=>{
          const missing=person.joints.filter(joint=>joint.status!=='supported');
          return <details key={person.instance_id} data-person-instance={person.instance_id}>
            <summary>Person {index+1} · {person.pose_status==='available'?`${person.joints.length-missing.length}/${person.joints.length} joints supported`:'Pose unavailable'}</summary>
            <div>Frame-local reference {person.instance_id} · {ageText(view.pose2d.age)}</div>
            {person.association?<div data-person-track-epoch={person.association.track_epoch} data-person-track-ref={person.association.track_ref||''}>
              {person.association.track_ref?<span>Anonymous reference <span title={person.association.track_ref}>{shortRef(person.association.track_ref)}</span> · </span>:null}
              {words(person.association.status)} · epoch <span title={person.association.track_epoch}>{shortRef(person.association.track_epoch)}</span> · viewing only
              {person.association.reason?` · ${words(person.association.reason)}`:''}</div>:<div>Anonymous continuity unavailable</div>}
            {person.interpretation?.status==='available'?<div data-person-interpretation="available">
              <div>Activity hypothesis · {words(person.interpretation.facts.activity_hypothesis)}</div>
              <div>Posture · {words(person.interpretation.facts.subject_posture)} · Held object · {words(person.interpretation.facts.held_object)}</div>
              <div>Book · {words(person.interpretation.facts.book_state)} · Gaze · {words(person.interpretation.facts.gaze_target)}</div>
              <div>Typing visible · {words(person.interpretation.facts.typing_visible)} · Screen facing subject · {words(person.interpretation.facts.screen_facing_subject)}</div>
              <div>Visible basis · {person.interpretation.facts.visible_basis.map(words).join(', ')||'none supplied'}</div>
            </div>:<div data-person-interpretation="unknown">Interpretation unknown · {words(person.interpretation?.reason||'person_semantics_missing')}</div>}
            {person.judgment?.status==='available'?<div data-person-judgment="available">
              JEV judge · {words(person.judgment.judgment.activity)} · probability {Math.round(person.judgment.judgment.probabilities[person.judgment.judgment.activity]*100)}% · uncalibrated · viewing only
            </div>:<div data-person-judgment="unknown">JEV judgment unknown · {words(person.judgment?.reason||'person_judgments_missing')}</div>}
            {person.pose_reason&&<div>{words(person.pose_reason)}</div>}
            {person.score_semantics&&<div>Raw model responses; not calibrated probabilities</div>}
            {missing.map(joint=><div key={joint.joint_id}>{words(joint.name)} · {words(joint.reason||joint.status)}{Number.isFinite(joint.score)?` · raw ${joint.score}`:''}</div>)}
          </details>;
        })}
      {view.pose2d.boxesReason&&<div>Person boxes unavailable · {words(view.pose2d.boxesReason)}</div>}
    </section>;

    const details=connected&&<details className="apt-dock-details" data-dock-details-panel="1" open={detailed}>
      <summary>Frame, models and registration</summary>
      <dl className="apt-dock-kv">
        <dt>Frame</dt><dd className="mono">{frame?.frame_id||'unavailable'}</dd>
        <dt>Captured</dt><dd className="mono">{timestamp(timing.captured_at)}</dd>
        <dt>Observed</dt><dd className="mono">{timestamp(timing.observed_at)}</dd>
        <dt>Available</dt><dd className="mono">{timestamp(timing.available_at)}</dd>
        <dt>Expires</dt><dd className="mono">{timestamp(timing.valid_until)}</dd>
        <dt>Age</dt><dd>{timing.text}{timing.age.reason?<> · <span className="mono">{timing.age.reason}</span></>:null}</dd>
        <dt>Overlay</dt><dd>{view?.pose2d?.renderable?`Bound to frame ${frame?.frame_id}`:`Not drawn · ${words(view?.pose2d?.reason)}`}</dd>
        <dt>Detector</dt><dd>{detector.status==='available'?`${detector.count} · raw ${detector.scores.map(value=>value.toFixed(2)).join(', ')||'scores not supplied'}`:words(detector.reason)}</dd>
        <dt>Pose</dt><dd>{view?.pose2d?.model_id||'unavailable'} · {pose.text}</dd>
        <dt>JEV</dt><dd>{jev.model||'unavailable'} · {jev.label}{Number.isFinite(jev.probability)?` · ${jev.probability.toFixed(2)}`:''}</dd>
        <dt>Placement</dt><dd>{approximateActive?'approximate · supplied viewing joints':words(approximateReason||room?.reason||'none')}</dd>
        <dt>Capability</dt><dd>{capability.source==='capability'?'home-den-capabilities-1':'interim default · no capability supplied'}</dd>
      </dl>
      <details style={note}><summary>Pose metadata</summary>
        <div>Status {words(view?.pose2d?.status)}{view?.pose2d?.reason?` · ${words(view.pose2d.reason)}`:''}</div>
        <div>Model {view?.pose2d?.model_id||'unavailable'}</div>
        <div>Source {view?.pose2d?.source_id||'unavailable'}</div>
        <div>Observed {timestamp(view?.pose2d?.originalTimes?.observed_at)}</div>
        <div>Available {timestamp(view?.pose2d?.originalTimes?.available_at)}</div>
        <div>Expires {timestamp(view?.pose2d?.originalTimes?.valid_until)}</div>
      </details>
      <section aria-label="JEV activity" style={{marginTop:10}}>
        <strong>JEV activity</strong>{jevRows.length?jevRows.map((row,index)=><div key={index} style={{marginTop:5}}>
          {words(resultText(row.result))}<div style={note}>{words(row.status)} · {ageText(row.age)} · {words(row.association)}</div></div>):
          <div style={note}>Unavailable · no JEV result supplied</div>}
      </section>
      <section aria-label="Model results" style={{marginTop:10}}><strong>Model results</strong>
        {models.length?models.map((row,index)=><ModelResult key={`${row.family}:${row.source_id}:${row.model_id}:${index}`} model={row}/>):<p style={note}>No model observations supplied.</p>}
      </section>
    </details>;

    const labRow=opened&&sessions.length>0&&<section aria-label="Perception Lab" className="apt-dock-section">
      <div className="apt-dock-row">
        <span style={{...note,flex:1}} data-lab-intent={labIntent.available?'session':'none'}>{labIntent.available?
          `Opens session ${labIntent.session_id.length>24?`…${labIntent.session_id.slice(-12)}`:labIntent.session_id}${labIntent.t_s!=null?` at ${labIntent.t_s.toFixed(1)} s`:''}${labIntent.time_reason?` · ${labIntent.time_reason}`:''} · ${labIntent.selection_reason}`:labIntent.reason}</span>
        <button type="button" style={button} className="apt-ui-button" disabled={!onOpenLab||!labIntent.available}
          title={!onOpenLab?"Perception Lab isn't available from this view":labIntent.available?undefined:labIntent.reason}
          onClick={()=>openLab({session_id:labIntent.session_id,t_s:labIntent.t_s,camera_id:labIntent.camera_id,frame_id:labIntent.frame_id})}>Open in Lab</button>
      </div>
      <p style={note}>Lab A/B and the full pipeline live in Perception Lab.</p>
    </section>;

    const brainNote=opened&&!legacyDeskAimPreview&&<p data-desk-aim-preview-retired="1" style={{...note,marginTop:10,paddingTop:8,borderTop:'1px solid var(--apt-border-soft, #35404a)'}}>
      The den's virtual fixtures follow the lighting brain. See Virtual spotlights for why each light changed.</p>;
    const legacyPreview=opened&&legacyDeskAimPreview&&<section aria-label="Virtual desk aim preview" data-desk-aim-preview="1" style={{marginTop:10,paddingTop:8,borderTop:'1px solid #35404a'}}>
      <strong>Virtual desk aim preview</strong>
      <p style={note}>One visible person and your configured desk. Approximate placement; virtual controls only.</p>
      <button type="button" style={button} aria-pressed={aimPreview.enabled===true}
        disabled={!aimPreview.enabled&&!aimPreviewRef.current}
        onClick={toggleAimPreview}>{aimPreview.enabled?'Stop desk preview':'Preview desk aim'}</button>
      <div role="status" aria-live="polite" style={{...note,marginTop:5}}>
        {aimPreview.active?(aimPreview.holding&&aimPreview.lastSeenLabel?`Previewing configured desk · ${aimPreview.lastSeenLabel}`:'Previewing configured desk')
          :aimPreview.enabled?'Armed · waiting for one visible person':words(aimPreview.status)}
        {aimPreview.fixtureId?` · ${aimPreview.fixtureId}`:''}
        {aimPreview.reason&&!(aimPreview.active&&aimPreview.holding)?` · ${words(aimPreview.reason)}`:''}
      </div>
      {aimPreview.enabled===true&&aimPreview.policy==='development'&&<div data-desk-preview-development="1" style={note}>
        Development timing · strict limits deferred · {aimPreview.warningsRecorded||0} warnings recorded</div>}
    </section>;

    return <>
      {approximateBadge}{bodyLabel}
      <aside aria-label="Den camera dock" data-apartment-den-perception="1" data-dock-state={dock} data-dock-detail={detail}
        className={`apt-dock${dock==='expanded'?' is-expanded':''}`} style={style}>
        {header}
        {dock==='collapsed'?<p className="apt-dock-summary" role="status" data-dock-summary="1">{summary}</p>:<div className="apt-ui-dock-body apt-dock-body">
          {!opened&&<section className="apt-dock-callout" data-dock-empty="closed" aria-label="Camera view closed">
            <h3 className="apt-ui-heading">Camera view closed</h3>
            <p className="apt-ui-copy">Open this view to see analyzed frames from an existing Den session. Live video and analyzed frames are separate sources, and nothing starts by itself.</p>
            <div className="apt-dock-row"><button type="button" style={button} className="apt-ui-button" onClick={discover}>View perception</button></div>
          </section>}
          {!connected&&sessionControls}
          {emptyState}
          {frameView}
          {traceView}
          {inference}
          {roomPlacement}
          {persons}
          {connected&&<section aria-label="Recent model updates" className="apt-dock-section">
            <h3 className="apt-ui-heading">Recent model updates</h3>
            <p style={note}>Last 20 received updates in this session. History is separate from current evidence; original times and expiry are retained.</p>
            {modelHistory.length?modelHistory.map(entry=><ModelResult key={entry.key} model={entry.model} scope={entry.scope} historical/>):
              <p style={note}>No model updates supplied.</p>}
          </section>}
          {details}
          {connected&&sessionControls}
          {labRow}
          {brainNote}
          {legacyPreview}
        </div>}
      </aside></>;
  }
  global.HomeApartmentDenPerception=HomeApartmentDenPerception;
})(window);
