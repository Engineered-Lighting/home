/* Owner camera display, separate from exact analyzed-frame pose presentation. */
(function(global){
  'use strict';
  if(global.HomeApartmentOwnerCamera)return;
  const {useEffect,useRef,useState}=React;
  const words=value=>String(value||'unavailable').replaceAll('_',' ');
  const note={fontSize:11,lineHeight:1.5,color:'var(--hg-fg-3, #a8b5c0)'};
  const fail=message=>Object.assign(Error(message),{name:message==='camera_decode_aborted'?'AbortError':'Error'});

  function browserDecode(value,{signal,descriptor,env=global}){
    if(value?.type!=='image/jpeg'||!ArrayBuffer.isView(value.bytes)||value.bytes.BYTES_PER_ELEMENT!==1
      ||!value.bytes.byteLength||value.bytes.byteLength>262144)return Promise.reject(fail('camera_preview_invalid'));
    if(!env.Blob||!env.Image||!env.URL?.createObjectURL||!env.URL?.revokeObjectURL)return Promise.reject(fail('camera_decoder_unavailable'));
    return new Promise((resolve,reject)=>{
      let url=null,image=null,done=false,released=false;
      const dispose=()=>{if(released)return;released=true;if(image){image.onload=image.onerror=null;image.src='';}
        if(url!==null)env.URL.revokeObjectURL(url);};
      const finish=error=>{if(done)return;done=true;signal.removeEventListener('abort',cancel);dispose();reject(error);};
      const cancel=()=>finish(fail('camera_decode_aborted'));
      const ready=()=>{if(done)return;if(signal.aborted)return cancel();
        if(image.naturalWidth!==descriptor.width||image.naturalHeight!==descriptor.height)return finish(fail('camera_preview_dimensions_mismatch'));
        done=true;signal.removeEventListener('abort',cancel);resolve({url,width:image.naturalWidth,height:image.naturalHeight,dispose});};
      if(signal.aborted)return cancel();
      try{url=env.URL.createObjectURL(new env.Blob([value.bytes],{type:value.type}));image=new env.Image();
        signal.addEventListener('abort',cancel,{once:true});image.onerror=()=>finish(fail('camera_preview_decode_failed'));
        if(typeof image.decode!=='function')image.onload=ready;image.src=url;
        if(typeof image.decode==='function')Promise.resolve(image.decode()).then(ready,()=>finish(fail('camera_preview_decode_failed')));
      }catch(_){finish(fail('camera_preview_decode_failed'));}
    });
  }
  function previewKey(value){return value&&typeof value.jpeg_base64==='string'
    ?JSON.stringify([value.sequence??null,value.width,value.height,value.jpeg_base64]):null;}
  function contextText(value){if(typeof value?.text==='string')return value.text.slice(0,4000);return 'No saved description supplied.';}
  function metadata(value){try{return JSON.stringify(value,null,2).slice(0,8192);}catch(_){return 'Metadata unavailable';}}
  const factLabels=[['people_visible','People'],['subject_posture','Posture'],['held_object','Held object'],['gaze_target','Gaze'],
    ['activity_hypothesis','Activity hypothesis'],['book_state','Book'],['typing_visible','Typing visible'],['screen_facing_subject','Screen visible']];
  const instant=value=>Number.isFinite(value)?new Date(value*1000).toISOString():'unavailable';
  function StructuredFacts({value}){
    const scoped=value?.schema==='home-camera-structured-context-1'&&value.site_id==='victoria'&&value.camera_id==='den'
      &&value.scope?.kind==='perceiver_daily'&&value.scope.lab_session_id===null&&value.qualification===null&&value.physical_commands===false;
    const receipt=scoped?value.latest?.receipt:null,provider=scoped?value.provider:null;
    const current=scoped&&value.freshness==='current'&&receipt?.status==='valid',facts=receipt?.facts;
    const content=facts&&<dl style={{display:'grid',gridTemplateColumns:'auto 1fr',gap:'2px 12px',margin:'6px 0',fontSize:11}}>
      {factLabels.map(([key,label])=><React.Fragment key={key}><dt>{label}</dt><dd style={{margin:0}}>{words(facts[key])}</dd></React.Fragment>)}
      <dt>Visible basis</dt><dd style={{margin:0}}>{Array.isArray(facts.visible_basis)?facts.visible_basis.map(words).join(', ')||'none supplied':'unavailable'}</dd>
    </dl>;
    return <section aria-label="Structured camera facts" style={{marginTop:10}}>
      <strong style={{fontSize:11}}>Structured camera facts</strong>
      <div style={note}>Facts: {words(scoped?value.freshness:value?.reason?'invalid':'missing')}{value?.reason?` · ${words(value.reason)}`:''}</div>
      <div style={note}>Provider: {words(provider?.state)} · pump {words(provider?.pump_status)}
        {provider?.outstanding?' · request outstanding':''}{provider?.halted?' · halted':''}{provider?.last_reason?` · ${words(provider.last_reason)}`:''}</div>
      {current?content:<p style={{...note,margin:'5px 0'}}>No current validated structured facts.</p>}
      {!current&&facts&&<details style={note}><summary>Previous structured facts ({words(value.freshness)})</summary>{content}</details>}
      {receipt&&<div style={note}>Observed {instant(receipt.observed_at)} · available {instant(receipt.available_at)}<br/>
        Expires {instant(receipt.valid_until)} · age {Number.isFinite(value.age_seconds)?value.age_seconds.toFixed(1)+' s':'unavailable'}</div>}
      <div style={note}>Daily perceiver · separate from Lab recordings. Model interpretation; qualification unavailable.</div>
    </section>;
  }

  function HomeApartmentOwnerCamera({active=true,identityKey,source:providedSource,decodeFrame=browserDecode}){
    const source=providedSource||global.HomeApartmentCameraSource;
    const videoRef=useRef(null),generation=useRef(0),renderRef=useRef(null),ownedRef=useRef(null),pendingRef=useRef(null);
    const [snapshot,setSnapshot]=useState(null),[image,setImage]=useState(null),[mediaError,setMediaError]=useState(null);
    const state=snapshot?.identityKey===identityKey&&snapshot?.source===source?snapshot.value:null;
    renderRef.current={active,identityKey,source};
    useEffect(()=>{
      const token=++generation.current;let owner=null,detach=null,last=null;
      const current=()=>generation.current===token&&renderRef.current.active
        &&renderRef.current.identityKey===identityKey&&renderRef.current.source===source;
      const setState=value=>setSnapshot(value?{identityKey,source,value}:null);
      const release=value=>{try{value?.dispose?.();}catch(_){}};
      const clearImage=()=>{pendingRef.current?.abort.abort();pendingRef.current=null;release(ownedRef.current?.owned);ownedRef.current=null;setImage(null);};
      setState(null);setMediaError(null);clearImage();
      if(!active)return undefined;
      function update(value){
        if(!current())return;
        if(value?.schema!=='home-apartment-camera-view-1'||value.siteId!=='victoria'||value.cameraId!=='den'
          ||value.rawCamera!==true||value.exactAnalysis!==false){
          clearImage();setState({status:'refused',reason:'camera_scope_mismatch',context:null});generation.current++;
          try{detach?.();}catch(_){}detach=null;try{owner?.destroy?.();}catch(_){}owner=null;return;
        }
        last=value;setState(value);
        const key=value.status==='live'&&value.mode==='preview'?previewKey(value.preview):null;
        if(!key){clearImage();setMediaError(null);return;}
        if(pendingRef.current?.key===key||ownedRef.current?.key===key)return;
        clearImage();setMediaError(null);
        const job={key,abort:new global.AbortController()};pendingRef.current=job;
        const valid=()=>current()&&pendingRef.current===job&&!job.abort.signal.aborted
          &&last?.status==='live'&&last?.mode==='preview'&&previewKey(last.preview)===key;
        Promise.resolve().then(()=>{if(!valid())throw fail('camera_decode_aborted');return owner.readFrame();})
          .then(frame=>{if(!valid())throw fail('camera_decode_aborted');if(!frame)throw fail('camera_preview_unavailable');
            return decodeFrame(frame,{signal:job.abort.signal,descriptor:value.preview,env:global});})
          .then(owned=>{if(!valid()){release(owned);return;}
            if(typeof owned?.url!=='string'||!owned.url.startsWith('blob:')||typeof owned.dispose!=='function'
              ||owned.width!==undefined&&owned.width!==value.preview.width||owned.height!==undefined&&owned.height!==value.preview.height){
              release(owned);throw fail('camera_preview_decode_invalid');}
            pendingRef.current=null;ownedRef.current={key,owned};setImage({url:owned.url,identityKey,source});
          }).catch(error=>{if(!valid())return;pendingRef.current=null;setMediaError(error?.message||'camera_preview_decode_failed');});
      }
      try{
        if(typeof source?.create!=='function')throw fail('camera_source_unavailable');
        owner=source.create({siteId:'victoria',cameraId:'den',env:global,onUpdate:update});
        if(typeof owner?.attachVideo!=='function'||typeof owner.start!=='function'||typeof owner.destroy!=='function'
          ||typeof owner.readFrame!=='function')throw fail('camera_source_invalid');
        detach=owner.attachVideo(videoRef.current);owner.start();
      }catch(error){generation.current++;clearImage();try{detach?.();}catch(_){}detach=null;
        try{owner?.destroy?.();}catch(_){}owner=null;
        setState({status:'unavailable',reason:error?.message||'camera_source_unavailable',context:null});}
      return()=>{generation.current++;last=null;clearImage();try{detach?.();}catch(_){}try{owner?.destroy?.();}catch(_){};};
    },[active,identityKey,source,decodeFrame]);
    if(!active)return null;
    const picture=image?.identityKey===identityKey&&image?.source===source?image:null;
    const videoVisible=state?.status==='live'&&state.mode==='video';
    const context=state?.context;
    return <section aria-label="Live Den camera" style={{borderBottom:'1px solid #26323d',paddingBottom:12,marginBottom:14}}>
      <h3 style={{fontSize:12,margin:'0 0 6px'}}>Live camera</h3>
      <div role="status" style={note}>{words(state?.status||'connecting')}{state?.reason?` · ${words(state.reason)}`:''}
        {mediaError?` · ${words(mediaError)}`:''}</div>
      <div style={{position:'relative',marginTop:8,background:'#05090c',aspectRatio:'16 / 9'}}>
        <video ref={videoRef} aria-label="Live Den camera video" autoPlay muted playsInline controls hidden={!videoVisible}
          style={{width:'100%',height:'100%',objectFit:'contain'}}/>
        {picture&&<img src={picture.url} alt="Live Den camera JPEG fallback" style={{width:'100%',height:'100%',objectFit:'contain'}}/>}
        {!videoVisible&&!picture&&<div style={{...note,padding:12}}>Waiting for a current camera image</div>}
      </div>
      <div style={{...note,marginTop:6}}>{state?.mode==='video'?'Video · remux receipt only':'JPEG fallback · decode receipt only'} · exposure timing unverified</div>
      <StructuredFacts value={state?.structuredContext}/>
      <section aria-label="Saved camera description" style={{marginTop:10}}>
        <details style={note} open={context?.status==='current'}><summary style={{cursor:'pointer'}}>Saved description{context?.status==='current'?'':' · historical or unavailable'}</summary>
          <div>Saved description status: {words(context?.status)}</div>
          <p style={{fontSize:11,lineHeight:1.5,whiteSpace:'pre-wrap',overflowWrap:'anywhere',margin:'5px 0'}}>{contextText(context)}</p>
          {context&&<details><summary>Saved description metadata</summary>
            <pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',fontSize:10}}>{metadata(context)}</pre></details>}
        </details><div style={note}>Saved prose is separate from structured facts and does not drive lights.</div>
      </section>
    </section>;
  }
  global.HomeApartmentOwnerCamera=HomeApartmentOwnerCamera;
})(typeof window!=='undefined'?window:globalThis);
