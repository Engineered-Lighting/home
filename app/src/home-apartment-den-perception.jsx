/* Perception beside the Apartment canvas. Existing session connections remain
 * read-only; a separate explicit action owns one bounded viewing session. */
(function (global) {
  'use strict';
  const {useState,useRef,useEffect,useCallback}=React;
  const mono='"Geist Mono", "JetBrains Mono", monospace';
  const words=value=>String(value||'unavailable').replaceAll('_',' ');
  const approximateProblem=reason=>({approximate_mount_signature_changed:'Room or camera scene check changed',
    approximate_mount_signature_unknown:'Room or camera scene check unavailable'}[reason]||words(reason));
  const button={minHeight:40,padding:'6px 10px',border:'1px solid var(--hg-border-soft, #35404a)',
    background:'#111922',color:'inherit',font:'inherit',cursor:'pointer'};
  const field={...button,width:'100%',marginTop:5};
  const note={fontSize:11,color:'var(--hg-fg-3, #a8b5c0)',lineHeight:1.5};
  const timestamp=value=>{
    const date=Number.isFinite(value)?new Date(value*1000):null;
    return date&&Number.isFinite(date.getTime())?date.toISOString():'unavailable';
  };
  function ageText(age){
    if(!age||age.status==='unavailable')return 'Age unavailable';
    return `${words(age.status)}${Number.isFinite(age.seconds)?` · ${age.seconds.toFixed(1)} s old`:''}`;
  }
  function resultText(value){
    if(value==null)return 'No result supplied';
    if(typeof value==='string')return value.slice(0,800);
    for(const key of ['activity','label','summary','text'])if(typeof value[key]==='string')return value[key].slice(0,800);
    try{return JSON.stringify(value).slice(0,800);}catch(_){return 'Result unavailable';}
  }
  // The raw player owns native ESM import; production Babel's env preset
  // rewrites import() in this JSX file to an unavailable CommonJS require.
  const defaultLayer=()=>global.HomeApartmentPerceptionPlayer.loadLayer();

  function PoseOverlay({pose,frame}){
    if(!pose?.renderable||!pose.keypoints.length||!frame)return null;
    const byId=new Map(pose.keypoints.map(point=>[point.id,point]));
    return <svg aria-label="Exact-frame pose overlay" viewBox={`0 0 ${frame.width} ${frame.height}`}
      style={{position:'absolute',inset:0,width:'100%',height:'100%',pointerEvents:'none'}}>
      {pose.edges.map(([a,b],index)=>{const start=byId.get(a),end=byId.get(b);return start&&end?
        <line key={index} x1={start.x} y1={start.y} x2={end.x} y2={end.y} stroke="#b8d8ff" strokeWidth="2"/>:null;})}
      {pose.keypoints.map(point=><circle key={point.id} cx={point.x} cy={point.y} r="3" fill="#b8d8ff"/>)}
    </svg>;
  }
  function ModelResult({model}){
    return <details style={{borderTop:'1px solid #26323d',padding:'7px 0'}}>
      <summary style={{cursor:'pointer',lineHeight:1.5}}>{model.family||'Model'} · {words(model.status)}
        <span style={{display:'block',...note}}>{resultText(model.result)}</span></summary>
      <dl style={{margin:'8px 0',fontSize:10,lineHeight:1.6,overflowWrap:'anywhere'}}>
        <dt>Model</dt><dd>{model.model_id||'unavailable'}{model.model_revision?` · ${model.model_revision}`:''}</dd>
        <dt>Source</dt><dd>{model.source_id||'unavailable'}</dd>
        <dt>Observed</dt><dd>{timestamp(model.observed_at)}</dd>
        <dt>Available</dt><dd>{timestamp(model.available_at)}</dd>
        <dt>Expires</dt><dd>{timestamp(model.valid_until)}</dd>
        <dt>Frame association</dt><dd>{words(model.association)}{model.frame_id?` · ${model.frame_id}`:''}</dd>
      </dl>
      <div style={note}>{ageText(model.age)}{model.reason?` · ${words(model.reason)}`:''}</div>
    </details>;
  }

  function HomeApartmentDenPerception({engine,active=true,mobile=false,source:injectedSource,
    loadLayer=defaultLayer,onReturnToHome}){
    const source=injectedSource||global.HomeApartmentPerceptionSource;
    const [expanded,setExpanded]=useState(false),[sessions,setSessions]=useState([]),[sessionId,setSessionId]=useState(''),
      [arm,setArm]=useState('B'),[notice,setNotice]=useState(''),[discovering,setDiscovering]=useState(false),
      [state,setState]=useState(null),[anchor,setAnchor]=useState(null),[following,setFollowing]=useState(false),
      [followPaused,setFollowPaused]=useState(false),[layerReady,setLayerReady]=useState(false),
      [analysis,setAnalysis]=useState(()=>source?.analysisState?.()||{status:'idle',owned:false}),
      [analysisAvailability,setAnalysisAvailability]=useState(null);
    const playerRef=useRef(null),layerRef=useRef(null),discoveryRef=useRef(null),generationRef=useRef(0);
    const engineRef=useRef(engine),lastViewRef=useRef(null),analysisGenerationRef=useRef(0),analysisBindingRef=useRef(null),activeRef=useRef(active);
    engineRef.current=engine;activeRef.current=active;
    const identity=engine?.sceneIdentity;
    const identityKey=[identity?.siteId,identity?.contentRevision,identity?.frameDigest,identity?.registrationVersion].join('|');

    const stop=useCallback((reason='viewer_disconnected')=>{
      generationRef.current++;
      discoveryRef.current?.abort();discoveryRef.current=null;setDiscovering(false);
      playerRef.current?.destroy();playerRef.current=null;
      layerRef.current?.dispose();layerRef.current=null;
      lastViewRef.current=null;setState(null);setAnchor(null);setFollowing(false);setFollowPaused(false);setLayerReady(false);
      setNotice(reason==='viewer_disconnected'?'Disconnected from this view. The Lab session is unchanged.':'');
    },[]);
    useEffect(()=>()=>{generationRef.current++;discoveryRef.current?.abort();playerRef.current?.destroy();layerRef.current?.dispose();},[]);
    useEffect(()=>{stop('view_identity_changed');setSessionId('');setSessions([]);setExpanded(false);},[engine,identityKey,active,source,stop]);
    useEffect(()=>source?.watchAnalysis?.(setAnalysis),[source]);
    useEffect(()=>{
      analysisGenerationRef.current++;setAnalysisAvailability(null);
      return()=>{analysisGenerationRef.current++;if(analysisBindingRef.current)source?.releaseAnalysis?.(analysisBindingRef.current);analysisBindingRef.current=null;};
    },[engine,identityKey,active,source]);
    useEffect(()=>{
      const hidden=()=>{if(global.document.hidden)stop('page_hidden');};
      const logout=()=>{stop('owner_logout');if(analysisBindingRef.current)source?.releaseAnalysis?.(analysisBindingRef.current);setExpanded(false);setSessions([]);setSessionId('');};
      const site=()=>{if(global.HomeSpatialViewport?.getSelectedSiteId?.()!=='victoria')stop('selected_site_changed');};
      const logoutClick=event=>{if(event.target?.closest?.('#logout'))logout();};
      global.document.addEventListener('visibilitychange',hidden);
      global.document.addEventListener('click',logoutClick,true);
      global.addEventListener('pagehide',logout);global.addEventListener('home:logout',logout);
      const unsubscribe=global.HomeSpatialViewport?.subscribe?.(site);
      return()=>{global.document.removeEventListener('visibilitychange',hidden);global.document.removeEventListener('click',logoutClick,true);
        global.removeEventListener('pagehide',logout);global.removeEventListener('home:logout',logout);unsubscribe?.();};
    },[stop,source]);
    useEffect(()=>{
      if(!expanded||!active)return undefined;
      const timer=global.setInterval(()=>{
        setAnchor(layerRef.current?.screenAnchor()||null);
        const readback=layerRef.current?.readback();setFollowing(readback?.following===true);setFollowPaused(readback?.followPaused===true);
      },120);
      const canvas=engine?.renderer?.domElement;
      const manual=()=>{layerRef.current?.setFollowing(false);setFollowing(false);setFollowPaused(false);};
      canvas?.addEventListener('pointerdown',manual);canvas?.addEventListener('wheel',manual,{passive:true});
      return()=>{global.clearInterval(timer);canvas?.removeEventListener('pointerdown',manual);canvas?.removeEventListener('wheel',manual);};
    },[engine,expanded,active]);

    function analysisOptions(){
      const selectedEngine=engine,generation=analysisGenerationRef.current,selected={...engine?.sceneIdentity};
      return {identity:selected,generation,isCurrent:()=>activeRef.current&&engineRef.current===selectedEngine&&analysisGenerationRef.current===generation
        &&['siteId','contentRevision','frameDigest','registrationVersion'].every(key=>engineRef.current?.sceneIdentity?.[key]===selected[key])};
    }
    async function discover(){
      setExpanded(true);setDiscovering(true);setNotice('');
      discoveryRef.current?.abort();const abort=new AbortController();discoveryRef.current=abort;
      const token=generationRef.current;
      try{
        if(typeof source?.listSessions!=='function')throw Error('Perception source is unavailable');
        const [listResult,availableResult]=await Promise.allSettled([
          source.listSessions({siteId:'victoria',signal:abort.signal}),
          typeof source.analysisAvailability==='function'?source.analysisAvailability({...analysisOptions(),signal:abort.signal}):Promise.resolve(null)]);
        if(abort.signal.aborted||engineRef.current!==engine||token!==generationRef.current)return;
        setAnalysisAvailability(availableResult.status==='fulfilled'?availableResult.value:{available:false,reason:availableResult.reason?.message||'analysis_availability_unavailable'});
        if(listResult.status!=='fulfilled')throw listResult.reason;
        const result=listResult.value;
        const rows=(Array.isArray(result)?result:result?.items||result?.sessions||[])
          .filter(row=>row.site_id==='victoria'&&row.camera_id==='den'&&Number.isSafeInteger(row.generation)&&row.generation>=0).slice(0,100);
        setSessions(rows);
        const activeRows=rows.filter(row=>['recording','running','starting'].includes(row.state));
        if(!sessionId&&activeRows.length===1)setSessionId(activeRows[0].id);
        if(!rows.length)setNotice('No existing Den session. Start a five-minute live analysis here when available, or select a session from Perception Lab.');
      }catch(error){if(!abort.signal.aborted&&token===generationRef.current)setNotice(error.message||'Session discovery unavailable');}
      finally{if(discoveryRef.current===abort){discoveryRef.current=null;if(!abort.signal.aborted)setDiscovering(false);}}
    }
    async function connect(options={}){
      stop('new_connection');setNotice('');
      const session=options.session||sessions.find(row=>row.id===sessionId),selectedArm=options.arm||arm;
      if(!session){setNotice('Select an existing Den session first.');return;}
      const token=++generationRef.current;
      try{
        const projection=global.HomeApartmentPerceptionView,players=global.HomeApartmentPerceptionPlayer;
        if(!projection||!players||typeof source?.subscribe!=='function')throw Error('Perception viewer modules unavailable');
        const scope=projection.createScope(engine.sceneIdentity,session);
        const scopedSource={subscribe:args=>source.subscribe({...args,arm:selectedArm}),sampleClock:()=>source.sampleClock(),
          readFrame:(frame,options)=>source.readFrame(frame,options)};
        const collisionDigest=global.HomeContentPacks?.get?.(identity.siteId,identity.contentRevision)?.manifest?.assets?.['collision.glb']?.sha256||null;
        const player=players.create({source:scopedSource,scope,collisionDigest,onChange:next=>{
          if(token!==generationRef.current||engineRef.current!==engine)return;
          lastViewRef.current=next.view;setState(next);
          layerRef.current?.draw(next.view);
          const readback=layerRef.current?.readback();setFollowing(readback?.following===true);setFollowPaused(readback?.followPaused===true);
        }});
        playerRef.current=player;player.start();
        const module=await loadLayer();
        if(token!==generationRef.current||engineRef.current!==engine)return;
        const layer=module.attachApartmentPerceptionLayer(engine,scope,{isCurrent:()=>token===generationRef.current&&engineRef.current===engine,
          reducedMotion:()=>global.matchMedia?.('(prefers-reduced-motion: reduce)').matches===true});
        layerRef.current=layer;setLayerReady(true);if(lastViewRef.current)layer.draw(lastViewRef.current);
      }catch(error){if(token===generationRef.current)setNotice(error.message||'Perception view unavailable');}
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
      try{await source.stopAnalysis();if(ownerId&&ownerId===sessionId)stop('owned_analysis_stopped');}
      catch(error){setNotice(error.message||'Owned analysis stop not confirmed. Retry Stop own analysis.');}
    }
    function changeSelection(value,kind){stop('selection_changed');if(kind==='arm')setArm(value);else setSessionId(value);}
    function close(){stop();if(analysisBindingRef.current)source?.releaseAnalysis?.(analysisBindingRef.current);setExpanded(false);}
    if(!active||engine?.siteId!=='victoria')return null;
    const view=state?.view,room=view?.roomPose,diagnosticRoom=view?.diagnosticRoomPose,approximateRoom=view?.approximateRoomPose,
      approximateActive=approximateRoom?.renderable===true&&room?.admitted!==true&&diagnosticRoom?.renderable!==true,
      models=view?.models||[],jev=models.filter(row=>/jev/i.test(`${row.family} ${row.model_id} ${row.source_id}`)),
      metricInput=models.find(row=>row.source_id==='apartment_viewing_metric'),
      approximateReason=approximateRoom?.reason&&approximateRoom.reason!=='approximate_pose_unavailable'?approximateRoom.reason:
        ['unavailable','stopped'].includes(metricInput?.status)?metricInput.reason:null;
    const sessionLabelState=session=>analysis.session?.id===session.id&&analysis.session.generation===session.generation
      &&analysis.session.site_id==='victoria'&&analysis.session.camera_id==='den'?analysis.session.state:session.state;
    const frame=state?.frame;
    const width=mobile?Math.max(240,Math.min(340,(global.innerWidth||360)-24)):340;
    const screenWidth=global.innerWidth||1024;
    const x=anchor?.visible&&!mobile?Math.max(12,Math.min(
      anchor.x+72+width<=screenWidth-12?anchor.x+72:anchor.x-width-72,screenWidth-width-12)):null;
    const y=anchor?.visible&&!mobile?Math.max(76,Math.min(anchor.y-110,Math.max(76,(global.innerHeight||800)-590))):76;
    const style={position:'absolute',zIndex:14,right:x===null?12:undefined,left:x===null?undefined:x,top:y,
      width:expanded?width:undefined,maxWidth:'calc(100% - 24px)',maxHeight:mobile?'58%':`calc(100% - ${y+74}px)`,overflowY:'auto',
      padding:expanded?12:0,background:'rgba(9,16,23,.96)',border:'1px solid #35404a',color:'var(--hg-fg, #dbe7f1)',
      fontFamily:mono,fontSize:11,overflowWrap:'anywhere',pointerEvents:'auto'};
    return <>
      {approximateActive&&<div role="status" aria-label="Approximate room placement" data-apartment-approximate-badge="1"
        style={{position:'absolute',zIndex:14,top:68,left:'50%',transform:'translateX(-50%)',maxWidth:'calc(100% - 32px)',
          padding:'6px 10px',border:'1px solid #8d6d39',background:'rgba(9,16,23,.96)',color:'#e9bd7e',fontFamily:mono,fontSize:11,pointerEvents:'none'}}>Approximate room placement</div>}
      <aside aria-label="Victoria Den perception" data-apartment-den-perception="1" style={style}>
      {!expanded?<button type="button" style={button} onClick={discover}>View perception</button>:<>
        <header style={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:8}}>
          <strong style={{fontSize:12}}>Den perception</strong><button type="button" style={button} onClick={close} aria-label="Return to room">Return</button>
        </header>
        <p style={note}>Camera and model results · viewing only</p>
        {global.HomeApartmentOwnerCamera && <global.HomeApartmentOwnerCamera active={active && expanded} identityKey={identityKey} />}
        <div style={{marginTop:12,paddingTop:10,borderTop:'1px solid var(--hg-border, #35404a)'}}>
          <strong>Analyzed frame and model results</strong>
          <p style={note}>The analyzed image and pose are separate from the live camera above. A live analysis runs for five minutes and does not renew automatically.</p>
        </div>
        {typeof source?.startAnalysis==='function'&&<section aria-label="Live analysis" style={{margin:'10px 0',paddingBottom:10,borderBottom:'1px solid #35404a'}}>
          <div style={{display:'flex',gap:6,flexWrap:'wrap'}}>
            <button type="button" style={button} disabled={!analysisAvailability?.available||analysis.owned} onClick={()=>ownAnalysis()}>Start live analysis (5 min)</button>
            <button type="button" style={button} disabled={!analysis.owned} onClick={stopOwnAnalysis}>Stop own analysis</button>
            {analysis.owned&&analysis.requestId&&['uncertain','stop_failed','unavailable','stopping'].includes(analysis.status)&&
              <button type="button" style={button} onClick={()=>ownAnalysis(true)}>Check analysis status</button>}
          </div>
          <div role="status" aria-live="polite" style={{...note,marginTop:5}}>Own analysis: {words(analysis.status)}{analysis.reason?` · ${words(analysis.reason)}`:''}</div>
          {!analysisAvailability?.available&&<div style={note}>{words(analysisAvailability?.reason||'Checking live analysis availability')}</div>}
          {analysis.owned&&<div style={note}>Only this Apartment-created session can be stopped here. Existing Lab sessions remain unchanged.</div>}
        </section>}
        <label>Lab session<select aria-label="Den Lab session" value={sessionId} onChange={event=>changeSelection(event.target.value,'session')} style={field}>
          <option value="">Select existing session</option>{sessions.map(session=><option key={session.id} value={session.id}>
            {session.id.slice(-12)} · {sessionLabelState(session)}</option>)}</select></label>
        <div style={{display:'flex',gap:6,alignItems:'end',marginTop:8}}>
          <label style={{flex:1}}>Pipeline arm<select aria-label="Perception pipeline arm" value={arm} onChange={event=>changeSelection(event.target.value,'arm')} style={field}>
            <option value="A">A</option><option value="B">B</option></select></label>
          <button type="button" style={button} disabled={discovering} onClick={discover}>{discovering?'Reading…':'Refresh sessions'}</button>
        </div>
        <div style={{display:'flex',gap:6,marginTop:8}}>
          <button type="button" style={button} disabled={!sessionId||['connecting','live','recording'].includes(state?.connection)} onClick={()=>connect()}>Connect</button>
          <button type="button" style={button} disabled={!state} onClick={()=>stop()}>Disconnect</button>
          <button type="button" style={button} aria-pressed={following} disabled={(!view?.follow?.allowed&&!following)||!layerReady}
            onClick={()=>{layerRef.current?.setFollowing(!following);const next=layerRef.current?.readback();setFollowing(next?.following===true);setFollowPaused(next?.followPaused===true);}}>Follow</button>
        </div>
        <div role="status" aria-live="polite" style={{...note,marginTop:8}}>{words(state?.connection||'disconnected')}{state?.reason?` · ${words(state.reason)}`:''}</div>
        {state?.mediaReason && state.mediaReason !== state.reason && <div role="status" style={note}>{words(state.mediaReason)} · waiting for the next analyzed frame</div>}
        {notice&&<p role="status" style={note}>{notice}</p>}
        {!sessions.length&&onReturnToHome&&<button type="button" style={button} onClick={()=>{close();onReturnToHome();}}>Back to Home / Perception Lab</button>}
        <div style={{position:'relative',marginTop:10,background:'#05090c',aspectRatio:frame?`${frame.width} / ${frame.height}`:'16 / 9'}}>
          {state?.imageUrl&&frame?<><img src={state.imageUrl} alt={`Analyzed Den camera frame ${frame.frame_id}`} style={{display:'block',width:'100%',height:'100%',objectFit:'contain'}}/>
            <PoseOverlay pose={view?.pose2d} frame={frame}/></>:<div style={{...note,padding:16}}>Exact analyzed camera frame unavailable</div>}
        </div>
        <div style={{...note,marginTop:5}}>{frame?`Frame ${frame.frame_id} · Pose: ${ageText(view?.pose2d?.age)}`:'No decoded frame'}</div>
        {frame&&view?.pose2d?.renderable&&view.pose2d.keypoints.length===0&&
          <div role="status" style={{...note,marginTop:5}}>No supported person joints in this analyzed frame</div>}
        {approximateActive&&<div data-apartment-approximate-note="1" style={{...note,marginTop:5,color:'#e9bd7e'}}>
          Approximate room placement - timing unverified - no Follow or lighting authority</div>}
        {state?.heldFrame&&<div style={note}>Showing the preceding analyzed frame while the next exact frame decodes.</div>}
        {frame&&<div style={note}>Captured {timestamp(frame.captured_at)}</div>}
        {frame&&<details style={{...note,marginTop:5}}><summary>Pose metadata</summary>
          <div>Status {words(view?.pose2d?.status)}{view?.pose2d?.reason?` · ${words(view.pose2d.reason)}`:''}</div>
          <div>Model {view?.pose2d?.model_id||'unavailable'}</div>
          <div>Source {view?.pose2d?.source_id||'unavailable'}</div>
          <div>Observed {timestamp(view?.pose2d?.originalTimes?.observed_at)}</div>
          <div>Available {timestamp(view?.pose2d?.originalTimes?.available_at)}</div>
          <div>Expires {timestamp(view?.pose2d?.originalTimes?.valid_until)}</div>
        </details>}
        <div style={{...note,marginTop:8,color:room?.diagnostic||diagnosticRoom?.renderable?'#e9bd7e':'#b8d8ff'}}>
          {approximateActive?'Approximate room placement - supplied joints only':
            diagnosticRoom?.renderable?<span aria-label="Diagnostic room pose">{diagnosticRoom.timingLabel} · {diagnosticRoom.joints.length} supplied joints · no Follow or lighting authority</span>:
            diagnosticRoom?.reason&&diagnosticRoom.reason!=='diagnostic_pose_unavailable'?`Diagnostic room pose unavailable · ${words(diagnosticRoom.reason)}`:
            room?.admitted?(room.diagnostic?'Diagnostic room alignment · no lighting authority':'Registered room pose · viewing only'):
            approximateReason?<span data-apartment-approximate-unavailable="1">Approximate room placement unavailable - {approximateProblem(approximateReason)}</span>:
            `3D room position unavailable · ${words(room?.reason||'camera-to-room registration required')}`}
        </div>
        {followPaused?<div style={note}>Follow paused while the matching analyzed image decodes.</div>:
          !view?.follow?.allowed&&<div style={note}>Follow unavailable until a current registered room joint is supplied.</div>}
        <section aria-label="JEV activity" style={{marginTop:12,padding:'10px 0',borderTop:'1px solid #35404a',borderBottom:'1px solid #35404a'}}>
          <strong>JEV activity</strong>{jev.length?jev.map((row,index)=><div key={index} style={{marginTop:5}}>
            {words(resultText(row.result))}<div style={note}>{words(row.status)} · {ageText(row.age)} · {words(row.association)}</div></div>):
            <div style={note}>Unavailable · no JEV result supplied</div>}
        </section>
        <section aria-label="Model results" style={{marginTop:12}}><strong>Model results</strong>
          {models.length?models.map((row,index)=><ModelResult key={`${row.family}:${row.source_id}:${row.model_id}:${index}`} model={row}/>):<p style={note}>No model observations supplied.</p>}
        </section>
      </>}
    </aside></>;
  }
  global.HomeApartmentDenPerception=HomeApartmentDenPerception;
})(window);
