/* Home's Perception Lab surface. The controller outlives this view. */
(function (global) {
  "use strict";

  const {
    useState,
    useEffect,
    useRef,
    useCallback,
    useMemo
  } = React;
  const D = global.HomePerceptionLabData;
  const C = global.HomePerceptionLabController;
  const COLUMNS = [['jevA', 'Live JEV A'], ['jevB', 'Live JEV B'], ['qBase', 'Qwen baseline'], ['qA', 'Qwen + A'], ['qB', 'Qwen + B'], ['qJev', 'Qwen-assisted JEV'], ['detectorA', 'Perception A - Jetson'], ['detectorB', 'Perception B - LA']];
  const layerNames = {
    boxes: "Bounding boxes",
    masks: "Segmentation masks",
    skeleton: "Skeleton / keypoints",
    labels: "Labels + confidence",
    tracks: "Track IDs + trails",
    spatial: "3D positions + relationships"
  };
  function useLab() {
    const [state, set] = useState(C.getSnapshot);
    useEffect(() => C.subscribe(set), []);
    return state;
  }
  function Button({
    children,
    primary,
    onClick,
    disabled,
    className = '',
    ...props
  }) {
    return <button type="button" className={'pl-button ' + (primary ? 'pl-primary ' : '') + className} disabled={disabled} onClick={onClick} {...props}>{children}</button>;
  }
  function Badge({
    children,
    warn
  }) {
    return <span className={'pl-badge ' + (warn ? 'pl-warn' : '')}>{children}</span>;
  }
  function ArchiveExport({ session, siteId, mode }) {
    const [status,setStatus]=useState(''),[busy,setBusy]=useState(false);
    const active=useRef(null),mounted=useRef(true);
    useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;active.current?.abort();};},[]);
    const native=!!(global.__TAURI__||global.__TAURI_INTERNALS__);
    const reason=mode!=='real'?'Media export requires a recorded real-mode session.':
      !native&&typeof global.showSaveFilePicker!=='function'?'Streaming file save requires a supported browser.':
      !['stopped','interrupted','failed'].includes(session?.state)?'Stop and finalize the session before exporting.':'';
    async function save(){
      if(reason||active.current)return;
      const controller=new AbortController();active.current=controller;setBusy(true);setStatus('Choose a file location');
      let sink;
      try {
        // Invoke the picker directly from the click, before network awaits.
        if(native){
          sink=await global.HomePerceptionLabTransport.createNativeArchiveSink({siteId,sessionId:session.id});
          if(!sink){if(mounted.current)setStatus('Export cancelled');return;}
        }else{
        const handle=await global.showSaveFilePicker({suggestedName:'perception-lab-'+session.id+'.tar',
          types:[{description:'Perception Lab session archive',accept:{'application/x-tar':['.tar']}}]});
        if(controller.signal.aborted)return;
        sink=await handle.createWritable();
        }
        if(controller.signal.aborted){await sink.abort();return;}
        if(mounted.current)setStatus('Preparing archive');
        await global.HomePerceptionLabTransport.downloadArchive({siteId,sessionId:session.id},sink,{
          signal:controller.signal,onProgress:({bytes})=>{if(mounted.current)setStatus((bytes/1024**2).toFixed(1)+' MiB received · verifying');}
        });
        if(mounted.current)setStatus('Saved · media integrity verified');
      } catch(error){
        try{await sink?.abort();}catch(_){}
        if(mounted.current)setStatus(controller.signal.aborted||error?.name==='AbortError'?'Export cancelled':
          error?.code==='export_snapshot_changed'?'Session changed. Start a new export.':'Export failed. No completed archive was saved.');
      } finally {
        active.current=null;if(mounted.current){setBusy(false);if(controller.signal.aborted)setStatus('Export cancelled');}
      }
    }
    return <div className="pl-archive-export"><div className="pl-inline">
      <Button disabled={!!reason||busy} onClick={save} title={reason||'Save recorded media, labels and metadata'}>Export session + media</Button>
      {busy&&<Button onClick={()=>{active.current?.abort();setStatus('Cancelling export');}}>Cancel export</Button>}
      </div><span className="pl-muted" role="status">{status||reason||'Includes recorded media and labels. Training review is still required.'}</span></div>;
  }
  // Beside the recording timer: bookmark now, or bookmark with a quick activity
  // tag. The tag word is the bookmark's label; labelling from cues reads it.
  function BookmarkBar({onMark}) {
    return <div className="pl-bookmark-bar pl-inline" role="group" aria-label="Bookmark this moment" style={{flexWrap: 'wrap', gap: 6, margin: '6px 0'}}>
      <Button primary className="pl-bookmark-now" title="Mark this moment to label it later (B)" onClick={() => onMark()}>Bookmark · B</Button>
      <span className="pl-muted">or bookmark as</span>
      {BOOKMARK_TAGS.map(([tag, name]) => <Button key={tag} className="pl-bookmark-tag" data-tag={tag} title={'Bookmark this moment as ' + tag} onClick={() => onMark(tag)}>{name}</Button>)}
    </div>;
  }
  function Empty({
    title,
    children
  }) {
    return <div className="pl-empty"><span className="pl-empty-mark">—</span><strong>{title}</strong>{children && <p>{children}</p>}</div>;
  }
  function Modal({
    title,
    onClose,
    children,
    wide
  }) {
    const ref = useRef(null);
    useEffect(() => {
      const previous = document.activeElement;
      ref.current?.focus();
      const key = e => {
        if (e.key === 'Escape') {
          e.stopPropagation();
          onClose();
        }
        if (e.key === 'Tab') {
          const nodes = Array.from(ref.current?.querySelectorAll('button:not(:disabled),input,select,textarea,[tabindex="0"]') || []);
          if (!nodes.length) return;
          const first = nodes[0],
            last = nodes[nodes.length - 1];
          if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus();
          } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
          }
        }
      };
      document.addEventListener('keydown', key);
      return () => {
        document.removeEventListener('keydown', key);
        previous?.focus?.();
      };
    }, []);
    return <div className="pl-modal-backdrop"><section ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title} className={'pl-modal ' + (wide ? 'pl-modal-wide' : '')}><header><h2>{title}</h2><Button onClick={onClose} aria-label={'Close ' + title}>Close</Button></header>{children}</section></div>;
  }
  function useNow(ms) {
    const [now,setNow]=useState(Date.now);
    useEffect(()=>{
      if(!ms)return;
      const timer=setInterval(()=>setNow(Date.now()),ms);
      return()=>clearInterval(timer);
    },[ms]);
    return ms?now:Date.now();
  }
  // Session-clock seconds of an analyzed (s) or source-plane (ms) frame; NaN when unmapped.
  const recordedAt=f=>Number.isFinite(f?.session_time_s)?f.session_time_s:
    Number.isFinite(f?.session_time_ms??f?.timeline_start_ms)?(f.session_time_ms??f.timeline_start_ms)/1000:NaN;
  // Replay holds an older pair only when it was recorded at or before both the
  // frame now due and the cursor; unmapped or later imagery is never held.
  const replayHold=playhead=>(held,next)=>recordedAt(held)<=recordedAt(next)&&recordedAt(held)<=playhead;
  // Live age ticks between polls. With a reported server_time it is read on the
  // Lab clock (RTT-midpoint offset estimate); otherwise on this device's clock.
  function FrameAge({frame,review,playhead,fallback}) {
    const captured=!review&&frame?.captured_at!=null?D.timestamp(frame.captured_at):NaN;
    const now=useNow(Number.isFinite(captured)?250:0);
    if(review) {
      const at=Number.isFinite(recordedAt(frame))?recordedAt(frame):fallback;
      if(!Number.isFinite(at))return 'Recorded time not mapped';
      // A result that is later than the cursor (a fetch not yet landed) says so.
      const d=Math.round((playhead-at)*10)/10;
      return d<0?(-d).toFixed(1)+' s after replay cursor':d.toFixed(1)+' s at replay cursor';
    }
    if(!Number.isFinite(captured))return 'Age unknown';
    const clock=C.delivery?.clock?.();
    const age=Math.max(0,(now+(clock?clock.offset_ms:0)-captured)/1000);
    return <span title={clock?'Lab clock offset estimated from live poll round trip':'Lab server time not reported; age uses this device clock'}>{age.toFixed(1)} s old{clock?' ±'+(clock.uncertainty_ms/1000).toFixed(2)+' s est.':' · device clock'}</span>;
  }
  function useFrameURL(frame, sessionId, own, report) {
    const [value,set]=useState({key:null,url:null,status:'idle'});
    const key=[frame?.site_id,sessionId,frame?.generation,frame?.media_id].join(':');
    const reportRef=useRef(report);
    reportRef.current=report;
    useEffect(()=>{
      let dead=false;
      const abort=new AbortController();
      const failed=code=>{set({key,url:null,status:'failed',code});reportRef.current?.('failure',code);};
      set({key,url:null,status:frame?.media_id?'loading':'idle'});
      if(!frame?.media_id)return;
      // A missing adapter is a visible failure, never an endless 'loading'.
      if(!global.HomePerceptionLabTransport?.requestMedia||!global.HomePerceptionLabMedia?.readFrame){failed('transport_unavailable');return;}
      const context={siteId:frame.site_id,sessionId,generation:frame.generation};
      global.HomePerceptionLabMedia.readFrame(frame,context,abort.signal,attempt=>{
        if(dead)return;
        set({key,url:null,status:attempt>1?'retrying':'loading'});
        if(attempt>1)reportRef.current?.('retry');
      }).then(({bytes,type})=>{
        // The owner revokes the URL once neither buffer shows it.
        if(!dead)set({key,url:own(URL.createObjectURL(new Blob([bytes],{type}))),status:'loaded'});
      }).catch(error=>{
        if(!dead&&!abort.signal.aborted)failed(error?.code||'frame_read_failed');
      });
      return()=>{dead=true;abort.abort();};
    },[key]);
    return value.key===key?value:{key,url:null,status:frame?.media_id?'loading':'idle'};
  }
  function VideoSource({
    media,
    session,
    t,
    playing,
    speed,
    review,
    bare,
    onStatus
  }) {
    const ref = useRef(null),
      player = useRef(null),
      [status, setStatus] = useState({
        state: 'loading'
      });
    // `bare` (label review): no native controls or caption; the view draws its
    // own opening/hidden states from onStatus. The cursor still drives seeking.
    const reportRef = useRef(onStatus);
    reportRef.current = onStatus;
    useEffect(() => {
      if (!session?.id || !ref.current) return;
      player.current = global.HomePerceptionLabMedia.createPlayer(ref.current, {
        siteId: session.site_id,
        sessionId: session.id
      }, s => { setStatus(s); reportRef.current?.(s); });
      return () => {
        player.current?.dispose();
        player.current = null;
      };
    }, [session?.id, session?.site_id, media?.generation]);
    useEffect(() => {
      player.current?.update(media);
    }, [media, session?.id]);
    useEffect(() => {
      if (!ref.current) return;
      ref.current.playbackRate = speed || 1;
      if (playing || !review) ref.current.play().catch(() => {});else ref.current.pause();
      if (!review && status.state === 'ready') {
        const end = Math.max(0, ...global.HomePerceptionLabMedia.videoItems(media).map(i => i.metadata?.end_seconds || 0));
        if (end - ref.current.currentTime > 5) player.current?.seek(Math.max(0, end - 1));
      }
    }, [playing, speed, review, status.state, media]);
    useEffect(() => {
      if (review && ref.current && Math.abs(ref.current.currentTime - t) > (playing ? 1 : .008)) player.current?.seek(t);
    }, [t, review, playing]);
    if (bare) return <div className="pl-video-source pl-video-bare"><video ref={ref} muted playsInline aria-label="Recorded session video" /></div>;
    return <div className="pl-video-source"><video ref={ref} muted playsInline controls aria-label="Recorded encoded video track" />{status.state !== 'ready' && <div className="pl-video-status">{status.reason || 'Opening video track…'}</div>}<p className="pl-muted">Encoded-video timeline. RGB–depth association is not assumed; processed-frame overlays remain separate.</p></div>;
  }
  function DepthSource({
    frame,
    session,
    range,
    mode
  }) {
    const canvas = useRef(null),
      data = useRef(null),
      [status, setStatus] = useState('Loading numerical depth'),
      [sample, setSample] = useState(null);
    useEffect(() => {
      let dead = false;
      const abort = new AbortController();
      data.current = null;
      setSample(null);
      setStatus('Loading numerical depth');
      if (!frame) {
        setStatus('Depth not supplied for this frame');
        return;
      }
      const item = {
        ...frame,
        size_bytes: frame.size_bytes || frame.bytes
      };
      global.HomePerceptionLabMedia.readObject(item, {
        siteId: session.site_id,
        sessionId: session.id,
        generation: frame.generation
      }, abort.signal).then(bytes => {
        if (dead) return;
        data.current = {
          bytes,
          metadata: frame.metadata || frame
        };
        setStatus('ready');
      }).catch(e => {
        if (!dead) setStatus(e.message);
      });
      return () => {
        dead = true;
        abort.abort();
      };
    }, [frame?.media_id, frame?.generation, session?.id, session?.site_id]);
    useEffect(() => {
      if (!canvas.current || !data.current || status !== 'ready') return;
      try {
        const pixels = global.HomePerceptionLabMedia.depthPixels(data.current.bytes, data.current.metadata, range[0], range[1], mode);
        data.current.pixels = pixels;
        canvas.current.width = pixels.width;
        canvas.current.height = pixels.height;
        canvas.current.getContext('2d').putImageData(new ImageData(pixels.rgba, pixels.width, pixels.height), 0, 0);
      } catch (e) {
        setStatus(e.message);
      }
    }, [status, range, mode, frame?.media_id]);
    return <div className="pl-depth-source">{status === 'ready' ? <><canvas ref={canvas} onClick={e => {
          const rect = e.currentTarget.getBoundingClientRect(),
            p = data.current?.pixels;
          if (!p) return;
          const x = Math.min(p.width - 1, Math.floor((e.clientX - rect.left) / rect.width * p.width)),
            y = Math.min(p.height - 1, Math.floor((e.clientY - rect.top) / rect.height * p.height));
          setSample({
            x,
            y,
            value: p.sample(x, y)
          });
        }} aria-label="Numerical depth, aligned to rectified left" /><p>{sample ? sample.value == null ? 'Unknown · no valid stereo measurement here' : sample.value.toFixed(3) + ' m raw sample at ' + sample.x + ', ' + sample.y : 'Click to inspect a raw depth sample. This is not a validated 3D joint.'}</p></> : <Empty title="Depth unavailable">{status}</Empty>}</div>;
  }
  function RoomScan({
    room,
    view
  }) {
    const host = useRef(null),
      canvas = useRef(null),
      engine = useRef(null),
      [error, setError] = useState('');
    useEffect(() => {
      let dead = false,
        cleanup = null,
        resize = null;
      setError('');
      const pack = global.HomeContentPacks?.get?.(room.site_id);
      if (!pack || room.content_revision !== pack.manifest.revision) {
        setError('The recorded site and content revision are not available');
        return;
      }
      import('./home-3d/engine.js').then(async mod => {
        const e = await mod.createEngine({
          canvas: canvas.current,
          hostEl: host.current,
          siteId: room.site_id,
          contentRevision: room.content_revision,
          sim: false
        });
        if (dead) {
          e.dispose();
          return;
        }
        engine.current = e;
        cleanup = e.attachInput(host.current);
        resize = new ResizeObserver(() => {
          if (host.current) e.setSize(host.current.clientWidth, host.current.clientHeight);
        });
        resize.observe(host.current);
        e.setSize(host.current.clientWidth, host.current.clientHeight);
        await e.modes.setMode(view, {
          duration: 0
        });
      }).catch(e => {
        if (!dead) setError(e.message);
      });
      return () => {
        dead = true;
        resize?.disconnect();
        cleanup?.();
        engine.current?.dispose();
        engine.current = null;
      };
    }, [room.site_id, room.content_revision]);
    useEffect(() => {
      engine.current?.modes.setMode(view, {
        duration: 0
      }).catch(e => setError(e.message));
    }, [view]);
    return <div ref={host} className="pl-scan"><canvas ref={canvas} />{error && <div className="pl-video-status">{error}</div>}<span className="pl-scan-note">Registered room asset · no device controls</span></div>;
  }
  function Overlay({
    arm,
    layers,
    width,
    height
  }) {
    const color = arm.id === 'B' ? '#B8D8FF' : 'white',
      dash = arm.id === 'B' ? '8 5' : undefined,
      ok = name => layers[name] && D.overlaySupport(arm, name).available;
    const point = p => Array.isArray(p) ? {
      x: p[0],
      y: p[1],
      confidence: p[2]
    } : p;
    const points = (arm.keypoints || []).map(point),
      valid = p => p && Number.isFinite(p.x) && Number.isFinite(p.y) && (p.confidence == null || p.confidence >= .3);
    return <svg className="pl-overlay-svg" viewBox={'0 0 ' + width + ' ' + height} aria-hidden="true">{ok('masks') && (arm.masks || []).flatMap((m, i) => (m.polygons || [m.points || []]).map((poly, j) => <polygon key={i + '-' + j} points={poly.map(p => point(p)).filter(valid).map(p => p.x + ',' + p.y).join(' ')} fill={color} fillOpacity=".16" stroke={color} strokeWidth="1" />))}{ok('boxes') && (arm.boxes || []).map((b, i) => <g key={i}><rect x={b.x} y={b.y} width={b.w} height={b.h} fill="none" stroke={color} strokeWidth="2" strokeDasharray={dash} />{ok('labels') && <text x={b.x} y={Math.max(14, b.y - 8)} fill={color} fontSize="12">{b.label}{Number.isFinite(b.confidence) ? ' ' + b.confidence.toFixed(2) : ''}</text>}</g>)}{ok('skeleton') && <g>{(arm.skeleton_edges || []).filter(([a, b]) => valid(points[a]) && valid(points[b])).map(([a, b], i) => <line key={i} x1={points[a].x} y1={points[a].y} x2={points[b].x} y2={points[b].y} stroke={color} strokeWidth="2" strokeDasharray={dash} />)}{points.filter(valid).map((p, i) => <circle key={i} cx={p.x} cy={p.y} r="3" fill={color} />)}</g>}{ok('tracks') && (arm.tracks || []).map(track => <g key={track.id}><polyline points={(track.trail || []).map(point).filter(valid).map(p => p.x + ',' + p.y).join(' ')} fill="none" stroke={color} strokeDasharray={dash} />{valid(track.pixel) && <text x={track.pixel.x} y={track.pixel.y} fill={color} fontSize="11">Track {track.id}</text>}</g>)}{ok('spatial') && (arm.image_relations || []).filter(r => valid(r.pixel)).map((r, i) => <text key={i} x={r.pixel.x} y={r.pixel.y} fill={color} fontSize="11">{r.label}</text>)}</svg>;
  }
  function SharedCameraContext({siteId,cameraId}) {
    const [result,setResult]=useState(null),[now,setNow]=useState(Date.now());
    const desktop=!!(global.__TAURI__||global.__TAURI_INTERNALS__);
    useEffect(()=>{
      let stopped=false,timer,request;
      setResult(null);
      async function refresh(){
        if(stopped)return;
        if(document.hidden){timer=setTimeout(refresh,5000);return;}
        request=new AbortController();
        try{
          const value=await global.HomePerceptionLabTransport.request('cameras.context',{siteId,cameraId},{signal:request.signal});
          if(value.site_id!==siteId||value.camera_id!==cameraId)throw Error('Context site mismatch');
          if(!stopped)setResult(value);
        }catch(_){if(!stopped)setResult({status:'unavailable',site_id:siteId,camera_id:cameraId});}
        finally{if(!stopped)timer=setTimeout(refresh,5000);}
      }
      const tick=setInterval(()=>setNow(Date.now()),1000);refresh();
      return()=>{stopped=true;request?.abort();clearTimeout(timer);clearInterval(tick);};
    },[siteId,cameraId]);
    const value=result?.site_id===siteId&&result?.camera_id===cameraId?result:null;
    const text=typeof value?.text==='string'&&['current','stale'].includes(value.status)?value.text:null;
    const fresh=text&&value.status==='current'&&Number.isFinite(value.valid_until)&&now<value.valid_until*1000;
    return <section aria-label="Shared camera analysis" style={{padding:'12px',borderTop:'1px solid var(--pl-line)'}}>
      <strong>LA analysis · single image</strong>
      <p role="status">{text?fresh?'Fresh result':'Historical result':value?.status==='unavailable'?'Result connection unavailable':'Waiting for the supervised Victoria → LA result'}</p>
      {text&&<><p>{text}</p><p className="pl-muted">Victoria · {cameraId} · observed {new Date(value.observed_at*1000).toLocaleTimeString()} · {value.model}</p><p className="pl-muted">A separate captured image was analyzed. This result is not an overlay on the current preview.</p></>}
      {desktop&&<p className="pl-muted">Requesting a new camera analysis is unavailable in the desktop app: it has no native camera refresh operation. Results requested from Home on the web still appear here.</p>}
    </section>;
  }
  function LivePlanePreview({siteId,cameraId,mode,range}) {
    const canvas=useRef(null),[value,setValue]=useState(null),[status,setStatus]=useState('Connecting to numerical source');
    const plane=mode==='validity'?'depth':mode;
    useEffect(()=>{
      let stopped=false,timer,expiry,request;
      setValue(null);setStatus('Connecting to numerical source');
      async function poll(){
        if(stopped)return;
        if(document.hidden){setValue(null);timer=setTimeout(poll,1000);return;}
        request=new AbortController();const timeout=setTimeout(()=>request.abort(),3500);
        try{
          const p=await global.HomePerceptionLabTransport.request('cameras.plane',{siteId,cameraId,modality:plane},{signal:request.signal});
          if(stopped)return;
          if(p.site_id!==siteId||p.camera_id!==cameraId||p.modality!==plane||p.width!==640||p.height!==400||typeof p.data_base64!=='string'||p.data_base64.length>700000)throw Error('Invalid plane');
          const bytes=Uint8Array.from(atob(p.data_base64),c=>c.charCodeAt(0));
          if(bytes.length!==640*400*(plane==='depth'?2:1))throw Error('Invalid dimensions');
          clearTimeout(expiry);setValue({...p,bytes});setStatus('Sampled live numerical view · 1 update/s · exposure timing unverified');
          expiry=setTimeout(()=>{setValue(null);setStatus('Numerical view is stale');},3000);
        }catch(_){if(!stopped){setValue(null);setStatus('Numerical source unavailable — retrying');}}
        finally{clearTimeout(timeout);if(!stopped)timer=setTimeout(poll,1000);}
      }
      poll();return()=>{stopped=true;clearTimeout(timer);clearTimeout(expiry);request?.abort();};
    },[siteId,cameraId,plane]);
    useEffect(()=>{
      if(!value||!canvas.current)return;
      try{
        let rgba;
        if(plane==='depth')rgba=global.HomePerceptionLabMedia.depthPixels(value.bytes,value,range[0],range[1],mode).rgba;
        else{rgba=new Uint8ClampedArray(640*400*4);for(let i=0;i<value.bytes.length;i++){rgba[i*4]=rgba[i*4+1]=rgba[i*4+2]=value.bytes[i];rgba[i*4+3]=255;}}
        canvas.current.width=640;canvas.current.height=400;
        canvas.current.getContext('2d').putImageData(new ImageData(rgba,640,400),0,0);
      }catch(_){setValue(null);setStatus('Numerical source format unsupported');}
    },[value,mode,range]);
    return <div className="pl-depth-source">{value?<canvas ref={canvas} aria-label={'Live '+mode+' view'}/>:<Empty title={status}>Numerical views use the existing camera owner and do not record or start models.</Empty>}<p className="pl-muted" role="status">{status}</p></div>;
  }
  function LiveCameraVideo({siteId,cameraId,available,snapshotAvailable}) {
    const video=useRef(null),[snapshot,setSnapshot]=useState(false),[status,setStatus]=useState('Connecting to live video');
    useEffect(()=>{
      if(snapshot||!available)return;
      if(!global.MediaSource){setStatus('Live video unavailable in this build');return;}
      let stopped=false,timer,request,source,buffer,url,generation,cursor,queue=[],failed=false,lastSuccess=0;
      const element=video.current;
      function dispose(){
        queue=[];buffer=null;
        element.pause();element.removeAttribute('src');element.load();
        if(url)URL.revokeObjectURL(url);url=null;source=null;
      }
      function reset(){dispose();generation=undefined;cursor=undefined;failed=false;}
      function drain(){
        if(stopped||!buffer||buffer.updating||failed)return;
        try{
          if(buffer.buffered.length){
            const end=buffer.buffered.end(buffer.buffered.length-1),start=buffer.buffered.start(0);
            if(element.currentTime<start||end-element.currentTime>2.5||element.currentTime===0)element.currentTime=Math.max(start,end-1.25);
            if(element.currentTime-start>5){buffer.remove(start,element.currentTime-3);return;}
          }
          if(queue.length)buffer.appendBuffer(queue.shift());
          element.play().catch(()=>{});
        }catch(_){failed=true;setStatus('Video playback interrupted - reconnecting');}
      }
      function decoderError(){failed=true;setStatus('Video playback interrupted - reconnecting');}
      element.addEventListener('error',decoderError);
      function decode(value,max){
        if(typeof value!=='string'||value.length>max)throw Error('Invalid video size');
        return Uint8Array.from(atob(value),c=>c.charCodeAt(0));
      }
      async function poll(){
        if(stopped)return;
        if(document.hidden){reset();setStatus('Live video paused while tab is hidden');timer=setTimeout(poll,500);return;}
        request=new AbortController();const timeout=setTimeout(()=>request.abort(),4000);
        try{
          if(failed)reset();
          const p=await global.HomePerceptionLabTransport.request('cameras.video',{siteId,cameraId,afterSequence:cursor,videoGeneration:generation},{signal:request.signal});
          if(stopped)return;
          if(p.site_id!==siteId||p.camera_id!==cameraId||!Array.isArray(p.fragments)||p.fragments.length>3||!Number.isFinite(p.receipt_age_seconds)||p.receipt_age_seconds>4||!/^[a-f0-9]{32}$/.test(p.generation)||!/^video\/mp4; codecs="avc1\.[0-9a-fA-F]{6}"$/.test(p.codec_mime)||!MediaSource.isTypeSupported(p.codec_mime))throw Error('Invalid video');
          if(p.reset||generation!==p.generation){
            reset();generation=p.generation;
            queue.push(decode(p.init_base64,350000));
            source=new MediaSource();const current=source;
            url=URL.createObjectURL(source);element.src=url;
            source.addEventListener('sourceopen',()=>{
              if(stopped||source!==current)return;
              try{buffer=source.addSourceBuffer(p.codec_mime);buffer.addEventListener('updateend',drain);buffer.addEventListener('error',()=>{failed=true;});drain();}catch(_){failed=true;}
            },{once:true});
          }
          for(const fragment of p.fragments){
            if(!Number.isSafeInteger(fragment.sequence)||fragment.sequence<1||(cursor!==undefined&&fragment.sequence!==cursor+1))throw Error('Video gap');
            queue.push(decode(fragment.data_base64,2800000));cursor=fragment.sequence;
          }
          if(queue.length>6)throw Error('Video backpressure');
          lastSuccess=performance.now();drain();setStatus('Live RGB video - exposure latency unverified');
        }catch(_){if(!stopped){failed=true;queue=[];if(lastSuccess&&performance.now()-lastSuccess>4000)reset();setStatus('Live video connecting or recovering');}}
        finally{clearTimeout(timeout);if(!stopped)timer=setTimeout(poll,250);}
      }
      poll();return()=>{stopped=true;clearTimeout(timer);request?.abort();element.removeEventListener('error',decoderError);dispose();};
    },[siteId,cameraId,available,snapshot]);
    if(snapshot||!available)return <div><LiveCameraPreview siteId={siteId} cameraId={cameraId} available={snapshotAvailable}/>{available&&<button onClick={()=>setSnapshot(false)}>Return to live video</button>}</div>;
    return <div><div className="pl-frame"><video ref={video} aria-label="Live OAK RGB video" autoPlay muted playsInline style={{width:'100%',height:'100%',objectFit:'contain'}} onError={()=>setStatus('Video could not be decoded')}/></div><p className="pl-muted" role="status">{status}</p><button onClick={()=>setSnapshot(true)}>Use 1 fps snapshot preview</button></div>;
  }
  function LiveCameraPreview({siteId, cameraId, available}) {
    const [view,setView]=useState({status:'Connecting to camera'});
    useEffect(()=>{
      let stopped=false,timer,expiry,request;
      const key=siteId+':'+cameraId;
      setView({key,status:!available?'Live preview is not connected':'Connecting to camera'});
      if(!available)return;
      async function read(){
        if(stopped)return;
        if(document.hidden){timer=setTimeout(read,1000);return;}
        request=new AbortController();const began=performance.now();
        const timeout=setTimeout(()=>request.abort(),3000);
        try{
          const p=await global.HomePerceptionLabTransport.request('cameras.preview',{siteId,cameraId},{signal:request.signal});
          const displayOnly=p.timestamp_semantics==='decode_receipt_only';
          const age=displayOnly?p.decoded_age_seconds:p.source_age_seconds+p.uncertainty_seconds;
          const remaining=Number.isFinite(age)?3000-age*1000-(performance.now()-began):0;
          if(p.site_id!==siteId||p.camera_id!==cameraId||p.width!==640||p.height!==360||p.content_type!=='image/jpeg'||
            typeof p.jpeg_base64!=='string'||p.jpeg_base64.length>349528||!/^[A-Za-z0-9+/]+={0,2}$/.test(p.jpeg_base64)||
            !Number.isFinite(remaining)||remaining<=0)throw Error('Invalid or stale preview');
          if(stopped)return;
          clearTimeout(expiry);
          setView({key,url:'data:image/jpeg;base64,'+p.jpeg_base64,status:displayOnly?'Live RGB preview · 1 frame/s · exposure timing unverified':'Live RGB preview · 1 frame/s · not recorded'});
          expiry=setTimeout(()=>setView({key,status:'Camera preview is stale — reconnecting'}),remaining);
        }catch(_){if(!stopped){clearTimeout(expiry);setView({key,status:'Camera preview unavailable — reconnecting'});}}
        finally{clearTimeout(timeout);if(!stopped)timer=setTimeout(read,1000);}
      }
      function visibility(){if(document.hidden){request?.abort();clearTimeout(expiry);setView({key,status:'Preview paused while tab is hidden'});}}
      document.addEventListener('visibilitychange',visibility);read();
      return()=>{stopped=true;clearTimeout(timer);clearTimeout(expiry);request?.abort();document.removeEventListener('visibilitychange',visibility);};
    },[siteId,cameraId,available]);
    const current=view.key===siteId+':'+cameraId?view:{status:'Connecting to camera'};
    return <div><div className="pl-frame">{current.url?<img src={current.url} alt="Live OAK RGB preview" onError={()=>setView({key:siteId+':'+cameraId,status:'Camera image could not be decoded'})}/>:<Empty title={current.status}>Live viewing is independent of recording and model readiness.</Empty>}</div><p className="pl-muted" role="status">{current.status}</p></div>;
  }
  function Scene({
    frame,
    arm,
    layers,
    source = 'rgb',
    sessionId,
    onPaint,
    report,
    ageLabel = f => <FrameAge frame={f} />,
    nextLabel = 'newer frame',
    canHold
  }) {
    // Double buffer: the last decoded {URL, frame, arm} pair stays on screen until
    // the next frame decodes in a hidden image; the pair then swaps in one commit.
    // An overlay is only ever drawn from the pair that owns the displayed URL.
    const owned = useRef(new Set()),
      latest = useRef(null);
    const own = useCallback(url => { owned.current.add(url); return url; }, []);
    const frameLoad = useFrameURL(frame, sessionId, own, report);
    const [shown, setShown] = useState(null),
      [decodeFailed, setDecodeFailed] = useState(null);
    const reportRef = useRef(report);
    reportRef.current = report;
    const pair = frameLoad.key + '|' + (arm?.id || '') + '|' + (arm?.frame_id ?? '');
    latest.current = pair;
    const decoded = frameLoad.url && decodeFailed !== frameLoad.url ? frameLoad.url : null;
    const current = decoded ? { pair, url: decoded, frame, arm, sessionId } : null;
    // Same output: newest props (status may turn stale). Same bytes, other arm: re-paired.
    // Older output: held only while a newer frame of this site/session/generation is due
    // and, in replay, only when canHold places it at or before that frame and the cursor.
    const front = shown?.pair === pair ? { ...shown, frame, arm } :
      current && current.url === shown?.url ? current :
      shown && frame?.media_id && shown.sessionId === sessionId && shown.frame?.site_id === frame.site_id &&
        shown.frame?.generation === frame.generation && (!canHold || canHold(shown.frame, frame)) ? shown : null;
    const back = current && current.url !== front?.url ? current : null;
    useEffect(() => {
      if (front !== shown && (front?.pair !== shown?.pair || !front)) setShown(front);
    });
    useEffect(() => {
      for (const url of owned.current) if (url !== shown?.url && url !== frameLoad.url) {
        URL.revokeObjectURL(url);
        owned.current.delete(url);
      }
    });
    useEffect(() => () => {
      latest.current = null;
      owned.current.forEach(url => URL.revokeObjectURL(url));
      owned.current.clear();
    }, []);
    const swap = p => {
      if (latest.current !== p.pair) return;
      setShown(p);
      onPaint?.(p.frame, p.arm);
    };
    const synthetic = frame?.synthetic === true;
    const width = frame?.width || 640,
      height = frame?.height || 360;
    const box = arm?.boxes || [];
    const scene = frame?.scene || 0;
    const depth = source === 'depth' || source === 'validity';
    const noun = arm ? 'analyzed frame' : depth ? 'depth frame' : source === 'rgb' ? 'source frame' : source + ' frame';
    const frameName = p => p?.arm?.frame_id ?? p?.frame?.id ?? p?.frame?.frame_id ?? p?.frame?.media_id ?? 'unknown';
    const failed = frameLoad.status === 'failed' || !!frameLoad.url && decodeFailed === frameLoad.url;
    const held = front && front.pair !== pair;
    return <div className={'pl-frame ' + (depth ? 'pl-depth' : '')}>
    {[front, back].filter(Boolean).map(p => p === back ?
      <img key={p.url} className="pl-frame-next" src={p.url} alt="" aria-hidden="true" onLoad={e => {
        const img = e.currentTarget, done = () => swap(p);
        img.decode ? img.decode().then(done, done) : done();
      }} onError={() => { setDecodeFailed(p.url); reportRef.current?.('failure', 'image_decode_failed'); }} /> :
      <img key={p.url} src={p.url} alt={(p.arm ? 'Analyzed ' : 'Source ') + source + ' frame ' + frameName(p)} onError={() => { setDecodeFailed(p.url); setShown(null); }} />)}
    {front ? null : synthetic ? <svg viewBox="0 0 640 360" role="img" aria-label="Synthetic den scene"><defs><pattern id={'pl-grid-' + (arm?.id || source)} width="32" height="32" patternUnits="userSpaceOnUse"><path d="M 32 0 L 0 0 0 32" fill="none" stroke="#1a1a1a" strokeWidth="1" /></pattern></defs><rect width="640" height="360" fill={depth ? '#081321' : '#080808'} /><rect width="640" height="360" fill={'url(#pl-grid-' + (arm?.id || source) + ')'} /><path d="M0 275L640 275M115 0L115 275M527 0L527 275" stroke="#363636" /><path d="M182 242L486 242L552 307L130 307Z" fill={depth ? '#284660' : '#222'} stroke="#656565" /><path d="M150 307L140 360M530 307L541 360" stroke="#7c7c7c" strokeWidth="5" /><rect x="381" y="162" width="85" height="61" fill="#101010" stroke="#8f8f8f" /><path d="M423 223V242" stroke="#8f8f8f" /><circle cx={scene === 3 ? 347 : 294} cy="108" r="27" fill={depth ? '#b8d8ff' : '#b7b7b7'} /><path d={scene === 3 ? 'M328 138Q317 202 327 243L333 319M357 140L370 240L378 319' : 'M274 136Q250 188 262 221L306 249L290 299M316 139L328 214L347 237'} fill="none" stroke={depth ? '#99bbd7' : '#777'} strokeWidth="23" strokeLinecap="round" /><path d="M284 157L310 210L337 241" fill="none" stroke="#aaa" strokeWidth="13" /><rect x="321" y={scene === 1 ? 211 : 238} width={scene === 1 ? 20 : 49} height={scene === 1 ? 36 : 25} fill={scene === 1 ? '#111' : '#ccc'} stroke="#eee" /><text x="16" y="337" fill="#8f8f8f" fontSize="11" fontFamily="monospace">SYNTHETIC FIXTURE · NOT A CAMERA FEED</text>{source === 'validity' && <path d="M0 0L120 0L70 360L0 360Z" fill="#ffb224" opacity=".24" />}</svg> : <Empty title={failed ? noun[0].toUpperCase() + noun.slice(1) + ' could not load' : frameLoad.status==='retrying' ? 'Retrying this frame' : ['loading', 'loaded'].includes(frameLoad.status) ? 'Loading ' + noun : frame ? 'Frame unavailable' : 'No frame supplied'}>{failed ? (arm ? 'This result retains its original frame identity. The next result will load independently.' : 'The next frame will load independently.') : 'Waiting for a source frame from the selected camera.'}</Empty>}
    {front ? front.arm && Number.isFinite(front.frame?.width) && Number.isFinite(front.frame?.height) && <Overlay arm={front.arm} layers={layers} width={front.frame.width} height={front.frame.height} /> :
      synthetic && arm && Number.isFinite(frame?.width) && Number.isFinite(frame?.height) && <Overlay arm={arm} layers={layers} width={width} height={height} />}
    {held && <div className={'pl-frame-hold ' + (failed ? 'pl-warn' : '')} role="status">Showing previous {noun} {frameName(front)} · {ageLabel(front.frame)} · {nextLabel} {frameName({frame, arm})} {decodeFailed && decodeFailed === frameLoad.url ? 'could not be decoded' : frameLoad.status === 'failed' ? 'failed to load' : frameLoad.status === 'retrying' ? 'retrying' : 'loading'}</div>}
  </div>;
  }
  function Room({
    room,
    selected,
    onSelect
  }) {
    const [view, setView] = useState('top'),
      [zoom, setZoom] = useState(1),
      [pan, setPan] = useState({
        x: 0,
        y: 0
      }),
      [layer, setLayer] = useState({
        zones: true,
        targets: true,
        people: true,
        relations: true
      }),
      drag = useRef(null);
    const floorIndex = room?.floor_axes === 'xz' ? 2 : 1,
      frameVerified = room?.units === 'metres' && ['xy', 'xz'].includes(room?.floor_axes);
    const xy = p => Array.isArray(p) ? {
      x: p[0],
      y: p.length === 3 ? p[floorIndex] : p[1]
    } : p?.pos ? {
      ...p,
      x: p.pos[0],
      y: p.pos[floorIndex]
    } : p;
    const valid = p => p && Number.isFinite(p.x) && Number.isFinite(p.y);
    const targets = (room?.targets || []).map(xy).filter(valid),
      people = (room?.people || []).map(xy).filter(valid),
      zones = (room?.zones || []).filter(z => Array.isArray(z.polygon)),
      relations = (room?.relations || []).filter(r => valid(xy(r.from)) && valid(xy(r.to))),
      all = [...targets, ...people, ...zones.flatMap(z => z.polygon.map(xy).filter(valid))],
      available = all.length > 0 && frameVerified;
    const xs = all.map(p => p.x),
      ys = all.map(p => p.y),
      minX = Math.min(0, ...xs) - 1,
      maxX = Math.max(1, ...xs) + 1,
      minY = Math.min(0, ...ys) - 1,
      maxY = Math.max(1, ...ys) + 1,
      wide = maxX - minX,
      high = maxY - minY,
      cx = (minX + maxX) / 2,
      cy = -(minY + maxY) / 2;
    const pack = room?.site_id && global.HomeContentPacks?.get?.(room.site_id),
      registered = pack?.manifest?.sceneAvailable && room?.content_revision === pack.manifest.revision;
    const has = {
      zones: zones.length > 0,
      targets: targets.length > 0,
      people: people.length > 0,
      relations: relations.length > 0
    };
    return <section className="pl-panel pl-room"><div className="pl-panel-heading"><span>Room context</span><div className="pl-segment">{[['top', 'Top-down'], ['mesh', 'Mesh'], ['splat', 'Splat']].map(([v, l]) => <button key={v} aria-pressed={view === v} disabled={v !== 'top' && !registered} title={v === 'top' ? 'Measured map and relations' : registered ? 'Registered room asset' : 'A matching registered room asset is not available'} onClick={() => setView(v)}>{l}</button>)}</div></div>{view !== 'top' && registered ? <RoomScan room={room} view={view} /> : <div className="pl-room-canvas" onWheel={e => setZoom(z => Math.max(.5, Math.min(3, z + (e.deltaY < 0 ? .1 : -.1))))} onPointerDown={e => {
        drag.current = {
          x: e.clientX,
          y: e.clientY,
          pan
        };
        e.currentTarget.setPointerCapture(e.pointerId);
      }} onPointerMove={e => {
        const bounds = e.currentTarget.getBoundingClientRect();
        const unitsPerPixel = Math.max(wide / Math.max(1, bounds.width), high / Math.max(1, bounds.height));
        if (drag.current) setPan({
          x: drag.current.pan.x + (e.clientX - drag.current.x) * unitsPerPixel,
          y: drag.current.pan.y + (e.clientY - drag.current.y) * unitsPerPixel
        });
      }} onPointerUp={() => {
        drag.current = null;
      }}>{available ? <svg viewBox={minX + ' ' + -maxY + ' ' + wide + ' ' + high} role="img" aria-label="Registered map positions in metres"><g transform={'translate(' + pan.x + ' ' + pan.y + ') translate(' + cx + ' ' + cy + ') scale(' + zoom + ') translate(' + -cx + ' ' + -cy + ')'}>{layer.zones && zones.map(z => <g key={z.id}><polygon points={z.polygon.map(xy).filter(valid).map(p => p.x + ',' + -p.y).join(' ')} fill="#151515" stroke="#777" strokeWidth=".015" /><text x={xy(z.polygon[0]).x} y={-xy(z.polygon[0]).y} fill="#999" fontSize=".14">{z.label || z.id}</text></g>)}{layer.targets && targets.map(target => <g key={target.id}><rect x={target.x - (target.width || .08) / 2} y={-target.y - (target.height || .08) / 2} width={target.width || .08} height={target.height || .08} fill="#151515" stroke="#929292" strokeWidth=".018" /><text x={target.x} y={-target.y - .05} fill="#aaa" fontSize=".14" textAnchor="middle">{target.label || target.id}</text></g>)}{layer.relations && relations.map((r, i) => {
              const a = xy(r.from),
                b = xy(r.to);
              return <g key={i}><line x1={a.x} y1={-a.y} x2={b.x} y2={-b.y} stroke="#777" strokeWidth=".02" strokeDasharray=".05 .04" /><text x={(a.x + b.x) / 2} y={-(a.y + b.y) / 2} fontSize=".12" fill="#aaa">{r.label}</text></g>;
            })}{layer.people && people.map(p => <g key={p.id} onClick={e => {
              e.stopPropagation();
              onSelect(p.id);
            }} style={{
              cursor: 'pointer'
            }}>{Number.isFinite(p.uncertainty_m) && <circle cx={p.x} cy={-p.y} r={p.uncertainty_m} fill="none" stroke={p.arm === 'B' ? '#B8D8FF' : 'white'} strokeWidth=".012" opacity=".5" />}<circle cx={p.x} cy={-p.y} r=".07" fill={selected === p.id ? '#0072F5' : p.arm === 'B' ? '#B8D8FF' : 'white'} /><text x={p.x + .12} y={-p.y + .04} fill={p.arm === 'B' ? '#B8D8FF' : 'white'} fontSize=".13">{p.arm} · {p.id}</text></g>)}</g></svg> : <Empty title="Room frame not supplied">This camera needs a site-matched map with explicit metric units and floor axes. Another home's scan is never substituted.</Empty>}</div>}{available && view === 'top' && <div className="pl-room-legend" aria-label="Room map labels">
          {layer.people && people.map(p => <button key={'person-' + p.id} type="button" aria-pressed={selected === p.id} onClick={() => onSelect(p.id)}><span className="pl-map-symbol">{p.arm || 'Person'}</span><span>Track {p.id}<small>{p.x.toFixed(2)}, {p.y.toFixed(2)} m{Number.isFinite(p.uncertainty_m) ? ` | uncertainty ${p.uncertainty_m.toFixed(2)} m` : ' | uncertainty unknown'}</small></span></button>)}
          {layer.targets && targets.map(target => <div key={'target-' + target.id}><span className="pl-map-symbol">Object</span><span>{target.label || target.id}<small>{target.x.toFixed(2)}, {target.y.toFixed(2)} m</small></span></div>)}
          {layer.zones && zones.map(z => <div key={'zone-' + z.id}><span className="pl-map-symbol">Zone</span><span>{z.label || z.id}</span></div>)}
          {layer.relations && relations.map((r,i) => <div key={'relation-' + i}><span className="pl-map-symbol">Link</span><span>{r.label || 'Relation supplied'}</span></div>)}
        </div>}<div className="pl-room-tools"><details><summary>Layers</summary>{Object.keys(layer).map(key => <label key={key}><input type="checkbox" disabled={!has[key] || view !== 'top'} checked={layer[key] && has[key]} onChange={e => setLayer({
              ...layer,
              [key]: e.target.checked
            })} />{key}{!has[key] && ' · not supplied'}</label>)}</details><span className="pl-muted">Drag to pan · wheel to zoom</span><Button disabled={view !== 'top'} onClick={() => setZoom(z => Math.min(3, z + .2))} aria-label="Zoom room in">+</Button><Button disabled={view !== 'top'} onClick={() => setZoom(z => Math.max(.5, z - .2))} aria-label="Zoom room out">−</Button><Button onClick={() => {
          setZoom(1);
          setPan({
            x: 0,
            y: 0
          });
        }}>Reset</Button></div></section>;
  }
  function ArmCard({
    arm,
    id,
    sessionId,
    columns,
    onColumn,
    review,
    kind,
    run,
    playhead,
    serviceDelivery
  }) {
    const [layers, setLayers] = useState({
      boxes: true,
      masks: false,
      skeleton: true,
      labels: true,
      tracks: false,
      spatial: true
    });
    // Live cards feed the client delivery monitor; replay never does.
    const armKey = arm?.id || id;
    useEffect(() => {
      if (review) return;
      return () => C.delivery?.pause?.(armKey);
    }, [review, armKey]);
    const paint = review ? undefined : (f, a) => C.delivery?.paint?.(armKey, { ...f, frame_id: a?.frame_id ?? f?.id }, sessionId);
    const report = review ? undefined : (kind, code) => C.delivery?.[kind]?.(armKey, code, sessionId);
    const needsStored = review && !columns.startsWith('jev');
    const j = run?.judgment || (!needsStored ? arm?.judgment : null);
    const status = run ? run.status : needsStored ? 'not generated' : arm?.status || 'unavailable';
    const stale = ['stale', 'unavailable', 'failed', 'error', 'not generated'].includes(status);
    return <section className={'pl-arm pl-arm-' + id}><div className="pl-arm-heading"><span className="pl-letter">{id}</span><div><h3>{review ? <select aria-label={'Column ' + id} value={columns} onChange={e => onColumn(e.target.value)}>{COLUMNS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select> : arm?.profile?.name || 'Pipeline ' + id}</h3><span className="pl-muted">{arm?.profile?.runs_on || 'Placement not verified'}</span></div><Badge warn={stale}>{status}</Badge></div><div className="pl-arm-meta"><span>{kind || 'Live'}</span><details className="pl-layer-pop"><summary>Layers · {Object.values(layers).filter(Boolean).length}</summary><div>{Object.keys(layerNames).map(key => {
              const support = D.overlaySupport(arm, key);
              return <label key={key}><input type="checkbox" disabled={!support.available} checked={layers[key] && support.available} onChange={e => setLayers({
                  ...layers,
                  [key]: e.target.checked
                })} /><span>{layerNames[key]}<small>{support.available ? layers[key] ? 'On' : 'Hidden · still recorded' : support.reason || 'Not supplied'}</small></span></label>;
            })}<p>Display only. Layer toggles never start a model or change recording.</p></div></details></div><Scene frame={arm?.frame} arm={arm} layers={layers} sessionId={sessionId} onPaint={paint} report={report} ageLabel={f => <FrameAge frame={f} review={review} playhead={playhead} />} nextLabel={review ? 'frame at cursor' : 'newer frame'} canHold={review ? replayHold(playhead) : undefined} />{arm?.frame?.time_basis === "encoded_video" && <p className="pl-muted">Saved analyzed frame at {arm.frame.session_time_s.toFixed(3)} s of video. Exposure clock unverified. This image stays attached to its detections as playback advances.</p>}{Number.isFinite(arm?.timing?.worker_wall_ms) && <p className="pl-muted">Worker processing {arm.timing.worker_wall_ms.toFixed(1)} ms. Offline result; live end-to-end delay unavailable.</p>}<p className="pl-muted">{Number.isFinite(arm?.timing?.stages_ms?.inference) && <>Model inference {arm.timing.stages_ms.inference.toFixed(1)} ms. </>}{Number.isFinite(arm?.timing?.source_to_controller_ms) && <>Source to controller {arm.timing.source_to_controller_ms.toFixed(1)} ms{Number.isFinite(arm.timing.source_clock_uncertainty_ms) ? <> (clock uncertainty ±{arm.timing.source_clock_uncertainty_ms.toFixed(0)} ms)</> : <> (clock uncertainty unavailable)</>}; display delay not included.</>}</p>{serviceDelivery && <p className="pl-muted">Service delivery: {serviceDelivery}</p>}<div className="pl-frame-caption"><span>{arm?.frame_id ? 'Analyzed frame ' + arm.frame_id : 'No analyzed frame'}</span><span><FrameAge frame={arm?.frame} review={review} playhead={playhead} fallback={run?.source_end_s} /></span></div><div className="pl-arm-body">{!review && <p role="status" className="pl-muted">JEV: {arm?.reasoning_status?.state || "awaiting status"}</p>}{arm?.pose_summary?.length > 0 && <details className="pl-inspect"><summary>Pose evidence: {arm.pose_summary.length} person crop(s)</summary>{arm.pose_summary.map(p => <div key={p.instance_id}><b>{p.layout}: {p.supported_joints}/{p.joint_count} supported joints</b><p>{p.depth}</p><p>{p.overlay}. {p.scores}.</p><small>Frame-local crop {p.instance_id}; not a person identity.</small></div>)}</details>}{arm?.validation?.length > 0 && <p role="status" className="pl-muted">Some evidence could not be displayed: {arm.validation.join(', ')}</p>}<div className="pl-evidence"><span className="pl-label">Observation</span><div className="pl-chips">{(arm?.observations || []).map((x, i) => <span key={i}>{typeof x === 'string' ? x : x.label}</span>)}{!arm?.observations?.length && <span className="pl-muted">No observations supplied</span>}</div><p>{(arm?.relations || []).map(x => typeof x === 'string' ? x : x.relation).join(' · ') || 'Spatial relations not measured'}</p></div><div className={'pl-judgment ' + (stale ? 'pl-stale' : '')}><span className="pl-label">{j?.provider || 'Reasoner'} · activity hypothesis</span><div className="pl-answer">{D.activityLabel(j?.activity || run?.activity) || 'Waiting for evidence'}</div><div className="pl-probability">{Number.isFinite(j?.probability) ? <span>P {j.probability.toFixed(2)}</span> : <span>Probability not supplied</span>}{Number.isFinite(j?.confidence) && <><div className="pl-bar"><i style={{
                  width: j.confidence * 100 + '%'
                }} /></div><span>Confidence {j.confidence.toFixed(2)}</span></>}</div><p className="pl-muted">{run ? (run.mode === 'live' ? 'Recorded live · source-associated' : 'Generated after recording · source-associated') : arm?.timing?.capture_to_judgment_s ? 'Capture → judgment ' + arm.timing.capture_to_judgment_s.toFixed(1) + ' s' : 'Timing not supplied'}</p></div><div className="pl-recommendation"><span className="pl-label">Policy recommendation</span><strong>{(needsStored ? run?.recommendation : arm?.recommendation) || 'No recommendation supplied'}</strong><span className="pl-muted">Shown only · lights are never changed</span></div><details className="pl-inspect"><summary>Inspect evidence, question, answer & policy</summary><ol><li><b>Evidence supplied</b><p>{j?.diagnostics ? "Exact admitted request; structured evidence only." : "Historical request unavailable; these are display observations, not the exact provider input."}</p>{j?.diagnostics ? <pre style={{whiteSpace: "pre-wrap", overflowWrap: "anywhere"}}>{JSON.stringify(j.diagnostics.request, null, 2)}</pre> : <p>{JSON.stringify(arm?.observations || [])}</p>}</li><li><b>Question asked</b><p>{j?.question || 'Not supplied'}</p></li><li><b>Answer returned</b>{(j?.distribution || []).map((d, i) => <p key={i}>{D.activityLabel(d.label)}: {Number(d.p).toFixed(3)}</p>)}<p>No private reasoning text is inferred or displayed.</p>{j?.diagnostics && <><pre style={{whiteSpace: "pre-wrap"}}>{JSON.stringify(j.diagnostics.answers, null, 2)}</pre><p>Token usage: {j.diagnostics.usage_status === "reported" ? JSON.stringify(j.diagnostics.usage) : "Not reported"}. Question: {j.diagnostics.question_version}; projection: {j.diagnostics.projection_version}.</p></>}</li><li><b>Policy recommendation</b><p>{(needsStored ? run?.recommendation : arm?.recommendation) || 'Not supplied'}</p></li></ol></details></div></section>;
  }
  // Setup readiness from the data module; a page that booted an older module
  // keeps the previous status-only rule until it reloads.
  const setupView = x => D.setupReadiness ? D.setupReadiness(x) : {group: x?.status === 'blocked' || x?.saved_only ? 'blocked' : 'ready',
    label: x?.status === 'ready' ? 'Ready' : 'Readiness not verified', summary: D.experimentReadiness(x), reasons: [], prerequisites: []};
  const setupName = x => D.setupName ? D.setupName(x) : x?.setup_name || x?.question;
  // The setup that is recording now says so. Its service readiness reads
  // "a comparison is already preparing or recording", which is about starting
  // another one, not about this setup.
  const statusView = (x, session) => D.isRecording(session) && x?.id != null && x.id === session.experiment_id ?
    {group: 'recording', startable: false, prepares_on_start: false, checked_at: null, reasons: [], prerequisites: [],
      label: D.recordingLabel(session), summary: 'This setup is ' + (session.state === 'starting' ? 'preparing now.' : 'recording now.') + ' Switching appends a comparison segment.'} : setupView(x);
  const SETTLE_MS = 4000, SETTLE_TRIES = 15;
  const STAGES = [['Input', 'input'], ['Detector', 'detector'], ['Pose', 'pose'], ['Masks', 'masks'], ['Localization', 'localization'], ['Room map', 'room_map'], ['History', 'history'], ['Sampling', 'sampling_hz'], ['Precision', 'precision'], ['HA evidence', 'ha_evidence'], ['Caption evidence', 'caption_evidence'], ['Reasoner', 'reasoner'], ['Policy', 'policy'], ['Runs on', 'device']];
  function stageValue(key, value) {
    // Recipes without ha_evidence keep the service's shared-HA default.
    if (key === 'ha_evidence') return value === false ? 'Withheld' : 'Shared input (when provisioned)';
    if (key === 'caption_evidence') return value === true ? 'Included' : value === false ? 'Not included' : 'Not specified';
    if (key === 'reasoner' && value === null) return 'Not set · live JEV is a session opt-in';
    if (value == null) return 'Not used';
    if (key === 'history') return value === 0 ? 'Latest only' : value + ' s';
    if (key === 'sampling_hz') return value + ' Hz';
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    return typeof value === 'object' ? JSON.stringify(value) : String(value);
  }
  function SetupReadiness({view, onRecheck}) {
    return <section className={'pl-setup-readiness pl-setup-' + view.group} role="status" aria-label="Setup readiness"><div className="pl-inline"><Badge warn={!['ready', 'recording'].includes(view.group)}>{view.label}</Badge>{Number.isFinite(view.checked_at) && <span className="pl-muted">Hosts checked {new Date(view.checked_at * 1000).toLocaleTimeString()} without starting anything</span>}</div>
      {!['blocked', 'coming_later'].includes(view.group) && <p>{view.summary}</p>}
      {view.group === 'blocked' && <ul>{view.reasons.map((r, i) => <li key={i}>{r.text}</li>)}</ul>}
      {view.group === 'coming_later' && <><p>Inspect only. It cannot start until these exist:</p><ul>{view.prerequisites.map(p => <li key={p.code}>{p.text}</li>)}</ul></>}
      {view.group === 'blocked' && onRecheck && <Button onClick={onRecheck}>Check again</Button>}</section>;
  }
  function Catalog({
    experiments,
    current,
    onSelect,
    onClose,
    onRecheck,
    recording,
    viewOf = setupView
  }) {
    const [selected, setSelected] = useState(current || experiments[0]?.id);
    const x = experiments.find(e => e.id === selected) || experiments[0];
    const view = viewOf(x), later = view.group === 'coming_later', saved = view.group === 'saved' || x.saved_only && view.group !== 'ready';
    const groups = D.setupGroups ? D.setupGroups(experiments) : [{id: 'all', label: 'Setups', items: experiments}];
    return <Modal title="Choose a setup" wide onClose={onClose}><div className="pl-catalog"><nav aria-label="Setups">{groups.map(group => <section key={group.id} aria-label={group.label}><h3>{group.label} · {group.items.length}</h3>{group.items.map(e => {
              const v = viewOf(e);
              return <button key={e.id} className={e.id === x.id ? 'selected' : ''} aria-pressed={e.id === x.id} onClick={() => setSelected(e.id)}><span className="pl-setup-name">{setupName(e)}</span><small className={'pl-setup-state pl-setup-' + v.group}>{v.label}{v.group === 'blocked' && v.reasons[0] ? ' · ' + v.reasons[0].text : ''}</small><small>{e.question}</small></button>;
            })}{!group.items.length && <p className="pl-muted pl-setup-none">None</p>}</section>)}</nav><article><span className="pl-label">{D.FAMILIES[x.family]} · {x.kind}</span><h2>{setupName(x)}</h2><p className="pl-setup-question">Question · {x.question}</p><SetupReadiness view={view} onRecheck={onRecheck} />{x.description && <p>{x.description}</p>}<details className="pl-setup-details"><summary>Detailed settings</summary><p className="pl-muted">{x.kind === 'Complete pipeline' ? 'Several stages change. Compare overall usefulness; do not attribute the result to one stage.' : 'Changed stages are explicit. Unchanged evidence and provider settings stay fixed.'}</p><table><thead><tr><th>Stage</th><th>Setup A</th><th>Setup B</th></tr></thead><tbody>{STAGES.map(([stage, key], i) => {
                const a = x.a ? stageValue(key, x.a[key]) : x.arms?.A?.[key] || (i === 0 ? x.arms?.A?.name : 'Manifest required');
                const b = x.b ? stageValue(key, x.b[key]) : x.arms?.B?.[key] || (i === 0 ? x.arms?.B?.name : 'Manifest required');
                return <tr key={stage} className={a !== b ? 'pl-changed' : ''}><th>{stage}</th><td>{a}</td><td>{b}</td></tr>;
              })}</tbody></table>{x.lever?.note && <><h3>Lever · the only difference between A and B</h3><p>{x.lever.note}</p></>}<h3>Held constant</h3><p>{x.isolation || x.held_constant}</p><h3>Try this</h3><div className="pl-chips">{x.try_this?.map(t => <span key={t}>{t}</span>)}</div>{x.revision && <><h3>Recipe revision</h3><p className="pl-muted">{x.revision}</p></>}</details><div className="pl-actions"><Button primary disabled={later || saved && recording || view.group === 'recording'} onClick={() => onSelect(x)}>{later ? 'Coming later · not startable' : view.group === 'recording' ? view.label + ' now' : saved ? 'Use with a saved session' : recording ? 'Switch to this setup' : 'Use this setup'}</Button><span className="pl-muted">{later ? 'Coming-later setups are listed so their requirements are visible.' : saved ? recording ? 'Stop the recording first; a saved-session setup never joins a live comparison.' : 'Opens saved-session review; nothing is recorded.' : view.group === 'blocked' ? 'You can choose it; Start stays disabled until the reasons above clear.' : 'Next: enable reasoning if you want it, then record.'}</span></div></article></div></Modal>;
  }
  function Library({
    caps,
    onClose,
    onPrepare,
    pending
  }) {
    const [picked, setPicked] = useState([]),
      [keepReady, setKeepReady] = useState(false);
    return <Modal title="Experiment library" wide onClose={onClose}><div className="pl-library"><p className="pl-muted">Stored artifacts and resident models are different. Production services are never unloaded by this tool.</p><div className="pl-device-grid">{(caps?.devices || []).map(d => <section className="pl-panel" key={d.id}><h3>{d.name || d.id}</h3><p>{d.budget_status || 'Approved memory budget not supplied'}</p><span className="pl-muted">{d.reserved?.join?.(' · ') || 'Protected workloads supplied by the runtime admission service'}</span></section>)}</div><table><thead><tr><th>Select</th><th>Artifact</th><th>Device</th><th>State</th><th>Version</th></tr></thead><tbody>{(caps?.artifacts || []).map(a => <tr key={a.id}><td><input type="checkbox" aria-label={'Select ' + (a.name || a.id)} checked={picked.includes(a.id)} onChange={e => setPicked(e.target.checked ? [...picked, a.id] : picked.filter(id => id !== a.id))} /></td><td>{a.name || a.model || a.id}</td><td>{a.device || a.device_id || 'Unknown'}</td><td>{a.state || 'Unverified'}</td><td><details><summary>Artifacts</summary><pre>{JSON.stringify({
                      checkpoint: a.checkpoint,
                      sha256: a.sha256,
                      runtime: a.runtime,
                      precision: a.precision,
                      startup: a.measured_startup_s
                    }, null, 2)}</pre></details></td></tr>)}</tbody></table>{!caps?.artifacts?.length && <Empty title="No artifact registry supplied">Connect an admitted Lab service to prepare exact model builds.</Empty>}<label className="pl-check"><input type="checkbox" disabled={caps?.keep_ready_allowed !== true} checked={keepReady && caps?.keep_ready_allowed === true} onChange={e => setKeepReady(e.target.checked)} />Keep selected models ready <small>{caps?.keep_ready_allowed === true ? 'Subject to admission; residency is reported by the runtime' : 'Runtime residency control is not available'}</small></label><div className="pl-actions"><Button primary disabled={!picked.length || pending} onClick={() => onPrepare(picked, keepReady)}>Prepare selected ({picked.length})</Button><span className="pl-muted">A preparation request can be denied. No models load merely by opening this drawer.</span></div></div></Modal>;
  }
  // ---- Recorded session review (v9) ----
  // Labels live on the session clock: the clock of the sensor/model timeline
  // and of the scored findings. The review cursor `t` is that clock; the video
  // is placed on it by the graph's cursor mapping (video second ≈ session
  // second; the measured offset is under about a second). Labels are
  // evaluation data only: they are saved to the annotation store and never
  // reach a model input. Only reviewed, human, session-clock labels are scored.
  const LABEL_ACTIVITIES = ['reading', 'phone_use', 'computer_work', 'resting', 'transition', 'other'];
  const ACT_NAMES = {reading: 'Reading', phone_use: 'Phone use', computer_work: 'Computer work', resting: 'Resting', transition: 'Transition', other: 'Other', insufficient_evidence: 'Insufficient evidence'};
  const actName = a => ACT_NAMES[a] || (a ? D.activityLabel(a) : '') || 'No activity';
  const LABEL_HOWTO = 'Play the video, click a label, drag its edges to where each activity really started and stopped, then mark it Reviewed.';
  const EVALUATION_ONLY = 'Labels are evaluation only; models never see them.';
  const round1 = v => Math.round((Number(v) || 0) * 10) / 10;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const tenths = v => {
    const n = Math.max(0, round1(v)), m = Math.floor(n / 60), s = n - m * 60;
    return String(m).padStart(2, '0') + ':' + (s < 10 ? '0' : '') + s.toFixed(1);
  };
  const mss1 = v => { const n = Math.max(0, round1(v)), m = Math.floor(n / 60), s = n - m * 60; return m + ':' + (s < 10 ? '0' : '') + s.toFixed(1); };
  const mmss = v => { const n = Math.max(0, Math.round(Number(v) || 0)); return Math.floor(n / 60) + ':' + String(n % 60).padStart(2, '0'); };
  const percent = x => x == null || !Number.isFinite(x) ? '—' : Math.round(x * 100) + '%';
  const MIN_LABEL_S = .5;
  const labelId = () => global.crypto?.randomUUID?.() || 'label-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
  // The fields a save sends. A session-clock label never carries a video reference.
  const labelFields = l => {
    const basis = l.time_basis || 'session';
    return {id: l.id, start_s: round1(l.start_s), end_s: round1(l.end_s), activity: l.activity || '', note: l.note || '', reviewed: l.reviewed === true, time_basis: basis,
      ...(basis === 'encoded_video' && l.video_init_id ? {video_init_id: l.video_init_id} : {})};
  };
  const activityClass = a => 'pl-act-' + (LABEL_ACTIVITIES.includes(a) || a === 'insufficient_evidence' ? a : 'unknown');
  // Colour always travels with a pattern and the activity name.
  const swatchClass = (a, reviewed) => 'pl-sw ' + activityClass(a) + (reviewed ? '' : ' pl-sw-open');
  const scorable = l => l.reviewed === true && l.origin === 'human' && (l.time_basis || 'session') === 'session';
  const LABEL_ERRORS = {
    revision_conflict: 'This label changed elsewhere. The latest version was reloaded; check it and retry.',
    invalid_time: 'The start and end must be inside this recording, with the end after the start.',
    invalid_label: 'Choose an activity before saving.',
    invalid_time_basis: 'This label’s clock reference is not valid for this recording.',
    too_large: 'The note is too long.',
    not_found: 'This label no longer exists. The list was reloaded.'
  };
  const labelError = e => LABEL_ERRORS[e?.code] || e?.message || 'the Lab service did not confirm the change';
  // Quick activity tags on a live bookmark. The bookmark label is free text:
  // the tag word itself is sent as the label, so labelling from cues can use it.
  const BOOKMARK_TAGS = [['reading', 'Reading', 'reading'], ['phone', 'Phone', 'phone_use'], ['computer', 'Computer', 'computer_work'], ['resting', 'Resting', 'resting'], ['other', 'Other', 'other']];
  const DEFAULT_CUE = 'Review this moment';
  const tagActivity = text => { const x = String(text || '').trim().toLowerCase(); return BOOKMARK_TAGS.find(([tag]) => tag === x)?.[2] || null; };
  // A cue's text suggests an activity; the owner confirms it before it counts.
  // null: the cue names no activity (for example the plain “Review this moment”).
  const guessActivity = text => {
    const tagged = tagActivity(text);
    if (tagged) return tagged;
    const x = String(text || '').toLowerCase();
    if (/closed/.test(x)) return 'other';
    if (/read|book/.test(x)) return 'reading';
    if (/phone/.test(x)) return 'phone_use';
    if (/computer|desk|\bpc\b|laptop/.test(x)) return 'computer_work';
    if (/rest|sofa|couch|\btv\b/.test(x)) return 'resting';
    if (/\bother\b/.test(x)) return 'other';
    return null;
  };
  // Each cue becomes an unreviewed label from 1 s after it to 1 s before the next.
  // A cue that names no activity gets activity null: the view asks for it.
  const cueLabels = (cues, end) => {
    const sorted = [...cues].filter(c => Number.isFinite(c.t_s)).sort((a, b) => a.t_s - b.t_s);
    return sorted.map((c, i) => ({id: labelId(), start_s: round1(clamp(c.t_s + 1, 0, end)), end_s: round1(clamp(i < sorted.length - 1 ? sorted[i + 1].t_s - 1 : end - 1, 0, end)),
      activity: guessActivity(c.label), note: tagActivity(c.label) || String(c.label || '').trim() === DEFAULT_CUE ? '' : String(c.label || '').replace(/^\s*\d+\s*[.)·:-]?\s*/, '').slice(0, 200), reviewed: false, origin: 'human', time_basis: 'session',
      cue_t_s: c.t_s, cue_label: String(c.label || '')}))
      .filter(l => l.end_s - l.start_s >= MIN_LABEL_S);
  };
  // Sessions named like delivery tests or canaries, with no human labels, are test runs.
  const TEST_NAME = /\b(canary|canaries|delivery|test|tests|retest|commissioning|acceptance|validation|smoke|fixture)\b|\bpair \d+\b/i;
  const sessionKind = s => {
    if ((s?.label_summary?.human || 0) > 0) return 'owner';
    return s?.fixture === true || TEST_NAME.test(s?.name || '') || !(D.seconds(s) > 0) ? 'test' : 'owner';
  };
  const labelStatus = s => {
    const L = s?.label_summary;
    if (!L) return {text: 'Labels not counted', tone: 'quiet'};
    const n = L.total || 0, r = L.reviewed || 0, word = n === 1 ? ' label' : ' labels';
    if (!n) return {text: 'No labels', tone: 'none'};
    if ((L.other_clock || 0) > 0) return {text: n + word + ' · ' + r + ' reviewed · video clock', tone: 'warn'};
    if (r === n) return {text: 'All reviewed ✓', tone: 'done'};
    return {text: n + word + ' · ' + r + ' reviewed', tone: 'open'};
  };
  // Arm answer strips from the replay timeline's JEV results: each answer holds
  // from its arrival (t_ms) until the next arrival or its valid_until, whichever
  // is first. Abstentions are their own run; gaps mean no current answer.
  const answerChoice = r => r.abstained === true || !r.activity || r.activity === 'insufficient_evidence' || !['ready', 'stale', 'completed'].includes(r.status || 'ready') ? 'abstain' : D.activityId(r.activity) || r.activity;
  const predictionRuns = track => {
    const items = (track?.items || []).filter(r => Number.isFinite(r?.t_ms)).sort((a, b) => a.t_ms - b.t_ms), out = [];
    items.forEach((r, i) => {
      const next = items[i + 1], s = r.t_ms / 1000;
      const e = Math.min(Number.isFinite(r.valid_until_ms) ? r.valid_until_ms / 1000 : s, next ? next.t_ms / 1000 : Infinity);
      if (!(e > s)) return;
      const choice = answerChoice(r), last = out[out.length - 1];
      const p = Number.isFinite(r.probability) ? r.probability : null;
      if (last && last.choice === choice && Math.abs(last.e - s) < .05) last.e = e;
      else out.push({s, e, choice, p});
    });
    return out;
  };
  const runAt = (runs, t) => runs.find(r => r.s <= t && t < r.e) || null;
  // Per-viewer UI preferences only (zoom, hidden predictions); never labels.
  function usePref(key, initial) {
    const [value, set] = useState(() => {
      try { const v = global.localStorage?.getItem('pl.v9.' + key); return v == null ? initial : JSON.parse(v); } catch (_) { return initial; }
    });
    const update = useCallback(v => { set(v); try { global.localStorage?.setItem('pl.v9.' + key, JSON.stringify(v)); } catch (_) {} }, [key]);
    return [value, update];
  }
  function useDocHidden() {
    const read = () => global.document?.visibilityState === 'hidden';
    const [hidden, set] = useState(read);
    useEffect(() => {
      const on = () => set(read());
      global.document?.addEventListener?.('visibilitychange', on);
      return () => global.document?.removeEventListener?.('visibilitychange', on);
    }, []);
    return hidden;
  }

  // Working copy of the session's labels: the stored labels with unsaved or
  // in-flight edits laid over them. Saves go one at a time, per label, through
  // annotations.save with the same id and the last known revision.
  function useLabelStore({C, sid, server, labelEnd, onConflict}) {
    const drafts = useRef({}), revs = useRef({}), timers = useRef({}), queue = useRef(Promise.resolve()), alive = useRef(true);
    const [, bump] = useState(0);
    const [saves, setSaves] = useState({});
    const rerender = () => bump(x => x + 1);
    useEffect(() => () => { alive.current = false; Object.values(timers.current).forEach(clearTimeout); }, []);
    for (const l of server) revs.current[l.id] = Math.max(revs.current[l.id] || 0, l.revision || 0);
    const stored = new Map(server.map(l => [l.id, l]));
    const labels = server.map(l => drafts.current[l.id] ? {...l, ...drafts.current[l.id]} : l)
      .concat(Object.values(drafts.current).filter(d => d._new && !stored.has(d.id)))
      .sort((a, b) => a.start_s - b.start_s || a.end_s - b.end_s);
    const byId = id => labels.find(l => l.id === id) || null;
    const status = (id, value) => { if (alive.current) setSaves(s => ({...s, [id]: value})); };
    const save = async id => {
      const d = drafts.current[id];
      if (!d) { status(id, {state: 'saved'}); return; }
      const body = labelFields(d);
      if (!LABEL_ACTIVITIES.includes(body.activity) && !stored.get(id)) { status(id, {state: 'failed', error: 'Choose an activity for this label.'}); return; }
      if (!(body.start_s >= 0) || !(body.end_s > body.start_s) || body.end_s > labelEnd + 1e-6) { status(id, {state: 'failed', error: LABEL_ERRORS.invalid_time}); return; }
      status(id, {state: 'saving'});
      try {
        const expected = revs.current[id] || 0;
        let saved;
        if (C.saveLabel) saved = await C.saveLabel(sid, body, expected);
        else {
          // A page that booted an older data module keeps the whole-session save.
          const r = await C.action('annotations.save', {session_id: sid, annotation: body, expected_revision: expected});
          saved = r?.annotations?.find(a => a.id === id) || null;
        }
        revs.current[id] = Math.max(revs.current[id] || 0, saved?.revision || expected + 1);
        if (drafts.current[id] === d) { delete drafts.current[id]; rerender(); }
        status(id, drafts.current[id] ? {state: 'saving'} : {state: 'saved'});
      } catch (e) {
        status(id, {state: 'failed', error: labelError(e)});
        if (['revision_conflict', 'not_found'].includes(e?.code)) onConflict?.();
      }
    };
    const enqueue = id => { queue.current = queue.current.then(() => save(id), () => save(id)); return queue.current; };
    // Commit an edit: saved after `delay` ms, so nudges and typing coalesce.
    const commit = (id, delay = 0) => {
      clearTimeout(timers.current[id]);
      status(id, {state: 'saving'});
      timers.current[id] = setTimeout(() => { delete timers.current[id]; enqueue(id); }, delay);
    };
    const edit = (id, patch, delay) => {
      const base = byId(id);
      if (!base) return null;
      const next = {...(drafts.current[id] || base), ...patch};
      if ((next.time_basis || 'session') === 'session') delete next.video_init_id;
      drafts.current[id] = next;
      rerender();
      if (delay != null) commit(id, delay);
      return next;
    };
    const add = list => {
      for (const l of list) { drafts.current[l.id] = {...l, _new: true}; commit(l.id, 0); }
      rerender();
    };
    // Edges snap to 0.1 s, keep 0.5 s minimum length and stay between neighbours.
    const moveEdge = (id, edge, value) => {
      const i = labels.findIndex(l => l.id === id), l = labels[i];
      if (!l) return null;
      const lo = i > 0 ? labels[i - 1].end_s : 0, hi = i < labels.length - 1 ? labels[i + 1].start_s : labelEnd;
      const v = edge === 'start' ? clamp(round1(value), Math.min(lo, l.start_s), round1(l.end_s - MIN_LABEL_S)) : clamp(round1(value), round1(l.start_s + MIN_LABEL_S), Math.max(hi, l.end_s));
      edit(id, edge === 'start' ? {start_s: v} : {end_s: v});
      return v;
    };
    const remove = async id => {
      const l = byId(id);
      if (!l) return false;
      clearTimeout(timers.current[id]);
      if (!stored.has(id)) { delete drafts.current[id]; rerender(); status(id, null); return true; }
      status(id, {state: 'deleting'});
      try {
        const run = () => C.deleteLabel ? C.deleteLabel(sid, id, revs.current[id] || l.revision) :
          C.action('annotations.delete', {session_id: sid, annotation_id: id, expected_revision: revs.current[id] || l.revision});
        const done = queue.current.then(run, run);
        queue.current = done.catch(() => {});
        await done;
        delete drafts.current[id];
        rerender();
        status(id, null);
        return true;
      } catch (e) {
        status(id, {state: 'failed', error: labelError(e), deleting: true});
        if (['revision_conflict', 'not_found'].includes(e?.code)) onConflict?.();
        return false;
      }
    };
    const retry = id => saves[id]?.deleting ? remove(id) : commit(id, 0);
    return {labels, saves, byId, edit, commit, add, moveEdge, remove, retry, isStored: id => stored.has(id)};
  }

  function SessionList({sessions, opening, onOpen, onRefresh}) {
    const [testsOpen, setTestsOpen] = useState(false);
    const rows = sessions.map(s => ({s, kind: sessionKind(s), status: labelStatus(s)}));
    const real = rows.filter(r => r.kind !== 'test'), tests = rows.filter(r => r.kind === 'test');
    const when = s => { const at = s.started_at ?? s.created_at; return at != null ? new Date(D.timestamp(at)).toLocaleString([], {weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}) : '—'; };
    const row = ({s, status}, test) => {
      const busy = opening?.id === s.id && !opening.error, cues = Number.isFinite(s.bookmark_count) ? s.bookmark_count ? s.bookmark_count + (s.bookmark_count === 1 ? ' cue' : ' cues') : '—' : '';
      return <button key={s.id} type="button" className={'pl-srow' + (test ? ' pl-srow-test' : '')} data-session-id={s.id} disabled={busy} aria-busy={busy ? 'true' : undefined} onClick={() => onOpen(s.id)}>
        <span className="pl-srow-name"><span className="pl-srow-title">{s.name || s.id}</span>{!test && <small>{s.id.slice(0, 8)} · {s.camera_id}{s.state !== 'stopped' ? ' · ' + s.state : ''}</small>}</span>
        <span className="pl-srow-when">{when(s)}</span><span className="pl-srow-len">{mmss(D.seconds(s))}</span><span className="pl-srow-cues">{cues}</span>
        <span className={'pl-pill pl-pill-' + (test && status.tone === 'none' ? 'quiet' : status.tone)}>{busy ? 'Opening…' : status.text}</span>{!test && <span className="pl-srow-chev" aria-hidden="true">›</span>}</button>;
    };
    return <section className="pl-sessions" aria-label="Recorded sessions">
      <div className="pl-section-title"><div><h2>Recorded sessions</h2><p className="pl-muted">Open a session to review its activity labels. Playback never starts inference.</p></div><Button onClick={onRefresh}>Refresh</Button></div>
      {rows.length > 0 && <div className="pl-srow pl-srow-head" aria-hidden="true"><span>SESSION</span><span>RECORDED</span><span>LENGTH</span><span>CUES</span><span>LABELS</span><span /></div>}
      <div className="pl-srows">{real.map(r => row(r, false))}</div>
      {!real.length && rows.length > 0 && <p className="pl-muted pl-srows-none">No owner sessions yet. Test runs are listed below.</p>}
      {tests.length > 0 && <div className="pl-stests"><button type="button" className="pl-stests-toggle" aria-expanded={testsOpen} onClick={() => setTestsOpen(!testsOpen)}>
        <span className="pl-stests-chev" aria-hidden="true">›</span><span className="pl-stests-name">Test runs</span><span className="pl-stests-count">{tests.length}</span><span className="pl-muted">Delivery tests and canaries · unlabelled, not scored</span></button>
        {testsOpen && <div className="pl-srows">{tests.map(r => row(r, true))}</div>}</div>}
      {!rows.length && <Empty title="No saved sessions">Start a recording from Live & record.</Empty>}
    </section>;
  }
  const OPEN_STEPS = [['record', 'Session record'], ['labels', 'Labels'], ['cues', 'Your cues'], ['media', 'Video and media list'], ['findings', 'Scores']];
  function OpeningView({opening, name, onBack}) {
    useNow(250);
    const done = opening.steps || {}, count = OPEN_STEPS.filter(([k]) => done[k] != null).length;
    const current = OPEN_STEPS.findIndex(([k]) => done[k] == null), share = Math.round(count / OPEN_STEPS.length * 100);
    const slow = Date.now() - (opening.started || Date.now()) > 2500;
    return <section className="pl-open" aria-label="Opening session"><div className="pl-open-head"><Button onClick={onBack}>← Sessions</Button><h2>{name}</h2></div>
      <div className="pl-opening pl-open-card" role="status" aria-busy="true"><div className="pl-open-line"><i className="pl-spinner" aria-hidden="true" /><span>Opening session</span><span className="pl-flex" /><span className="pl-mono">{share}%</span></div>
        <div className="pl-open-bar"><i style={{width: share + '%'}} /></div>
        <ol className="pl-open-steps">{OPEN_STEPS.map(([k, label], i) => <li key={k} className={done[k] != null ? 'pl-done' : i === current ? 'pl-now' : ''}><span className="pl-mono" aria-hidden="true">{done[k] != null ? '✓' : i === current ? '…' : '·'}</span><span>{label}</span><span className="pl-mono pl-muted">{done[k] ?? ''}</span></li>)}</ol>
        {slow && <p className="pl-muted">Large sessions take a few seconds. Nothing is wrong.</p>}</div></section>;
  }

  // The recorded-session view: header with progress and save state, then the
  // Label review, Pipeline detail and (once scored) Score tabs.
  function SessionReview({C, session, detail, duration, labelEnd, t, onSeek, playing, setPlaying, speed, setSpeed, hidePred, setHidePred, videoMedia, clockVerified, onBack, pipeline, demo}) {
    const sid = session.id;
    const server = detail.annotations || [];
    const cues = (detail.events || []).filter(e => e.kind === 'bookmark' && Number.isFinite(e.t_s)).sort((a, b) => a.t_s - b.t_s);
    const store = useLabelStore({C, sid, server, labelEnd, onConflict: () => C.open(sid, undefined, {keepCursor: true}).catch(() => {})});
    const L = store.labels, reviewed = L.filter(l => l.reviewed).length, unscorable = L.some(l => (l.time_basis || 'session') !== 'session');
    const allReviewed = L.length > 0 && reviewed === L.length;
    const [tab, setTab] = useState(() => allReviewed && !unscorable ? 'score' : 'labels');
    const [scored, setScored] = useState(tab === 'score');
    const [sel, setSel] = useState(null);
    const [toast, setToast] = useState(null);
    const toastTimer = useRef(null);
    const flash = useCallback(text => { setToast(text); clearTimeout(toastTimer.current); toastTimer.current = setTimeout(() => setToast(null), 2800); }, []);
    useEffect(() => () => clearTimeout(toastTimer.current), []);
    const hiddenDoc = useDocHidden();
    useEffect(() => { if (hiddenDoc) setPlaying(false); }, [hiddenDoc]);
    const entries = Object.entries(store.saves).filter(([, v]) => v);
    const failed = entries.filter(([, v]) => v.state === 'failed'), saving = entries.filter(([, v]) => ['saving', 'deleting'].includes(v.state));
    const saveSum = failed.length ? failed.length + (failed.length > 1 ? ' changes' : ' change') + ' couldn’t save' : saving.length ? 'Saving…' : entries.length ? 'All changes saved' : '';
    const canScore = allReviewed && !unscorable && tab !== 'score';
    const score = () => { setScored(true); setTab('score'); setPlaying(false); setSel(null); };
    const tabs = [['labels', 'Label review'], ['pipeline', 'Pipeline detail']].concat(scored ? [['score', 'Score']] : []);
    // Keys outside the label tab: Space plays and pauses.
    useEffect(() => {
      if (tab === 'labels') return;
      const key = e => {
        if (e.key !== ' ' || e.target?.closest?.('input,textarea,select,button,[contenteditable],.pl-modal')) return;
        e.preventDefault(); setPlaying(x => !x);
      };
      document.addEventListener('keydown', key);
      return () => document.removeEventListener('keydown', key);
    }, [tab]);
    const started = session.started_at ?? session.created_at;
    const meta = [sid.slice(0, 8), session.camera_id, mmss(D.seconds(session) || duration), started != null ? new Date(D.timestamp(started)).toLocaleString([], {weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}) : null, session.state].filter(Boolean).join(' · ');
    return <section className="pl-sv" aria-label="Recorded session">
      <div className="pl-sv-head"><Button className="pl-sv-back" onClick={onBack}>← Sessions</Button>
        <div className="pl-sv-title"><h2>{session.name || 'Session'}</h2><span className="pl-mono pl-muted">{meta}</span></div>
        <div className="pl-sv-tabs" role="tablist" aria-label="Session views">{tabs.map(([k, label]) => <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => { setTab(k); setPlaying(false); }}>{label}</button>)}</div>
        <span className="pl-flex" />
        <span className={'pl-sv-save' + (failed.length ? ' pl-sv-save-failed' : '')} role="status" aria-live="polite">{saveSum}{failed.length > 0 && <Button className="pl-mini-retry" onClick={() => failed.forEach(([id]) => store.retry(id))}>Retry</Button>}</span>
        <div className="pl-sv-prog"><span className="pl-sv-cells" aria-hidden="true">{L.slice(0, 12).map(l => <i key={l.id} className={l.reviewed ? 'pl-on' : ''} />)}</span><span>{L.length ? reviewed + ' of ' + L.length + ' reviewed' + (unscorable ? ' · not scorable' : '') : 'No labels'}</span></div>
        {canScore && <Button primary onClick={score}>Score this session</Button>}
      </div>
      {tab === 'labels' && <LabelTab C={C} session={session} store={store} sel={sel} setSel={setSel} t={t} onSeek={onSeek} playing={playing} setPlaying={setPlaying} speed={speed} setSpeed={setSpeed}
        duration={duration} labelEnd={labelEnd} cues={cues} hidePred={hidePred} setHidePred={setHidePred} videoMedia={videoMedia} clockVerified={clockVerified} flash={flash} hiddenDoc={hiddenDoc} demo={demo} />}
      {tab === 'pipeline' && <div className="pl-pipeline" role="tabpanel" aria-label="Pipeline detail">{pipeline}</div>}
      {tab === 'score' && <ScoreTab C={C} sid={sid} detail={detail} labels={L} onBack={() => setTab('labels')} demo={demo} />}
      {toast && <div className="pl-toast" role="status">{toast}</div>}
    </section>;
  }

  function LabelTab({C, session, store, sel, setSel, t, onSeek, playing, setPlaying, speed, setSpeed, duration, labelEnd, cues, hidePred, setHidePred, videoMedia, clockVerified, flash, hiddenDoc, demo}) {
    const sid = session.id, lab = useLab(), L = store.labels, axis = Math.max(1, labelEnd);
    const [zoom, setZoom] = usePref('zoom', 'fit');
    const [v0, setV0] = useState(0), [drag, setDrag] = useState(null), [markIn, setMarkIn] = useState(null);
    const [confirmDel, setConfirmDel] = useState(null), [syncOpen, setSyncOpen] = useState(false), [startHelp, setStartHelp] = useState(false);
    // Cue labels waiting for their activity: [{label, cue}], asked one at a time.
    const [ask, setAsk] = useState([]);
    const [vstatus, setVstatus] = useState({state: 'loading'});
    const track = useRef(null), ruler = useRef(null), dragRef = useRef(null), scrubRef = useRef(false);
    const span = zoom === 'fit' ? axis : Math.min(axis, zoom === '60' ? 60 : 20);
    const view0 = clamp(v0, 0, Math.max(0, axis - span));
    const P = x => (x - view0) / span * 100;
    const inView = (a, b) => b > view0 && a < view0 + span;
    // The view follows the playhead when zoomed in.
    useEffect(() => {
      if (zoom === 'fit') return;
      if (t < view0 || t > view0 + span * .97) setV0(clamp(t - span * .1, 0, Math.max(0, axis - span)));
    }, [t, zoom, span]);
    // Arm answers come from the replay timeline (read-only; never inference).
    const slot = lab.timeline?.session_id === sid ? lab.timeline : null;
    const whole = slot && slot.from_ms == null && slot.status === 'ready';
    useEffect(() => { if (!demo) C.timelineWindow?.(sid); }, [sid, lab.generation, whole ? 0 : lab.projectionEpoch || 0]);
    const jev = slot?.data?.tracks?.jev;
    const runs = useMemo(() => ({A: predictionRuns(jev?.a?.results), B: predictionRuns(jev?.b?.results)}), [jev]);
    const predState = demo || !C.timelineWindow ? 'none' : slot?.data ? 'ready' : slot?.status === 'failed' ? 'failed' : 'loading';
    const at = L.find(l => t >= l.start_s && t < l.end_s) || null;
    const selected = L.find(l => l.id === sel) || null, index = selected ? L.indexOf(selected) : -1;
    const hasVideo = !!global.HomePerceptionLabMedia?.videoItems?.(videoMedia)?.length;
    const togglePlay = () => { if (!playing && t >= duration - .05) onSeek(0); setPlaying(x => !x); };
    const seek = v => onSeek(clamp(round1(v), 0, duration));
    const select = (id, noSeek) => { const l = store.byId(id); if (!l) return; setSel(id); setConfirmDel(null); if (!noSeek) seek(l.start_s); };
    const cueBefore = s => [...cues].filter(c => c.t_s <= s + 2).pop();
    const create = (a, b) => {
      a = round1(a); b = round1(b);
      if (L.some(l => a >= l.start_s && a < l.end_s)) { flash('Labels can’t overlap. Start outside an existing label.'); return; }
      const next = L.find(l => l.start_s >= a);
      if (next) b = Math.min(b, next.start_s);
      b = Math.min(b, round1(axis));
      if (b - a < MIN_LABEL_S) { flash('Too short. Labels need at least 0.5 s.'); return; }
      const cue = cueBefore(a);
      const label = {id: labelId(), start_s: a, end_s: b, activity: (cue && guessActivity(cue.label)) || 'other', note: '', reviewed: false, origin: 'human', time_basis: 'session'};
      store.add([label]); setSel(label.id); setMarkIn(null); setStartHelp(false);
      flash('Label created · choose its activity, then mark it reviewed');
    };
    const doMarkIn = () => { const v = round1(t); setMarkIn(v); flash('In at ' + mss1(v) + ' · press O at the end of the activity'); };
    const doMarkOut = () => {
      if (markIn == null) { flash('Press I (Mark in) first'); return; }
      if (t <= markIn + MIN_LABEL_S) { flash('Mark out must be at least 0.5 s after mark in'); return; }
      create(markIn, t);
    };
    const review = (id, value) => {
      store.edit(id, {reviewed: value}, 0);
      if (!value) return;
      const i = L.findIndex(l => l.id === id);
      const next = L.slice(i + 1).concat(L.slice(0, i)).find(l => !l.reviewed && l.id !== id);
      if (next) setTimeout(() => select(next.id), 150);
      else { setSel(null); flash(L.every(l => (l.time_basis || 'session') === 'session') ? 'All labels reviewed. You can score this session now.' : 'All labels reviewed.'); }
    };
    const moveEdge = (id, edge, value, commitDelay) => {
      const v = store.moveEdge(id, edge, value);
      if (v != null) { seek(v); if (commitDelay != null) store.commit(id, commitDelay); }
      return v;
    };
    const del = async id => { setConfirmDel(null); const ok = await store.remove(id); if (ok) { setSel(s => s === id ? null : s); flash('Label deleted'); } };
    const cycle = step => {
      if (!L.length) return;
      const i = L.findIndex(l => l.id === sel), j = i < 0 ? (step > 0 ? 0 : L.length - 1) : (i + step + L.length) % L.length;
      select(L[j].id);
    };
    // A cue tagged or named with an activity becomes a label at once; a cue
    // with no activity word waits in `ask` until the owner picks one.
    const fromCues = () => {
      const made = cueLabels(cues, round1(axis)).map(({cue_t_s, cue_label, ...l}) => ({label: l, cue: {t_s: cue_t_s, label: cue_label}}));
      if (!made.length) { flash('The cues are too close together to make labels.'); return; }
      const known = made.filter(x => x.label.activity).map(x => x.label), unknown = made.filter(x => !x.label.activity);
      if (known.length) store.add(known);
      setConfirmDel(null); setAsk(unknown);
      if (unknown.length) { setSel(null); seek(unknown[0].label.start_s); }
      else setTimeout(() => { setSel(known[0].id); seek(known[0].start_s); }, 50);
      flash((known.length ? known.length + (known.length === 1 ? ' label' : ' labels') + ' created from your cues' : 'No cue names an activity') +
        (unknown.length ? ' · choose the activity for ' + unknown.length + (unknown.length === 1 ? ' more cue' : ' more cues') : ' · check each one'));
    };
    const answerAsk = activity => {
      const [first, ...rest] = ask;
      if (!first) return;
      if (activity) { const label = {...first.label, activity}; store.add([label]); if (!rest.length) setTimeout(() => { setSel(label.id); seek(label.start_s); }, 50); }
      setAsk(rest);
      if (rest.length) seek(rest[0].label.start_s);
      else if (!activity) flash('Cue skipped');
    };
    const convert = () => {
      const moved = L.filter(l => (l.time_basis || 'session') !== 'session');
      for (const l of moved) store.edit(l.id, {time_basis: 'session', reviewed: false, start_s: clamp(l.start_s, 0, axis), end_s: clamp(l.end_s, 0, axis)}, 0);
      flash('Converted to session clock · check each label and mark it reviewed');
    };
    // Keyboard map (ignored while typing; Esc leaves the field).
    useEffect(() => {
      const key = e => {
        if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
        const target = e.target, field = target?.closest?.('input,textarea,select,[contenteditable]');
        if (field) { if (e.key === 'Escape') target.blur?.(); return; }
        if (target?.closest?.('.pl-modal')) return;
        const control = !!target?.closest?.('button,a,summary,[role=slider]') && !target.closest('[data-lbl-nav]');
        const k = e.key;
        if (k === ' ') { e.preventDefault(); if (control) target.blur?.(); togglePlay(); }
        else if (k === 'ArrowLeft' || k === 'ArrowRight') { e.preventDefault(); seek(t + (k === 'ArrowLeft' ? -1 : 1) * (e.shiftKey ? 5 : .1)); }
        else if (k === 'i' || k === 'I') doMarkIn();
        else if (k === 'o' || k === 'O') doMarkOut();
        else if (k === 'Enter') { if (selected && !control) { e.preventDefault(); if (!selected.reviewed) review(selected.id, true); } }
        else if (k === 'Tab') { if (!control && L.length) { e.preventDefault(); cycle(e.shiftKey ? -1 : 1); } }
        else if (k === 'Delete' || k === 'Backspace') { if (selected) { e.preventDefault(); setConfirmDel(selected.id); } }
        else if (k === 'Escape') { setSel(null); setMarkIn(null); setConfirmDel(null); }
      };
      document.addEventListener('keydown', key);
      return () => document.removeEventListener('keydown', key);
    });
    // Pointer geometry on the track and ruler (6 px edge hit zone).
    const xOf = (el, e) => { const r = el.getBoundingClientRect(), w = r.width || el.clientWidth || 1; return {x: e.clientX - r.left, w, t: view0 + (e.clientX - r.left) / w * span}; };
    const onTrackDown = e => {
      if (e.button != null && e.button !== 0) return;
      const el = track.current, {x, w, t: t0} = xOf(el, e), px = v => (v - view0) / span * w;
      const order = selected ? [selected, ...L.filter(l => l !== selected)] : L;
      let hit = null;
      for (const l of order) {
        if (!inView(l.start_s, l.end_s)) continue;
        if (Math.abs(x - px(l.start_s)) <= 6) { hit = {id: l.id, edge: 'start'}; break; }
        if (Math.abs(x - px(l.end_s)) <= 6) { hit = {id: l.id, edge: 'end'}; break; }
      }
      if (!hit) { const inside = L.find(l => x > px(l.start_s) && x < px(l.end_s)); if (inside) { select(inside.id); return; } }
      e.preventDefault(); el.setPointerCapture?.(e.pointerId);
      if (hit) {
        const l = L.find(x => x.id === hit.id);
        dragRef.current = {mode: 'edge', ...hit}; setSel(hit.id); setConfirmDel(null); setPlaying(false);
        setDrag({mode: 'edge', t: hit.edge === 'start' ? l.start_s : l.end_s});
      } else dragRef.current = {mode: 'create', a: clamp(round1(t0), 0, axis), x0: x, moved: false};
    };
    const onTrackMove = e => {
      const d = dragRef.current, el = track.current;
      if (!d) {
        const {x, w} = xOf(el, e), px = v => (v - view0) / span * w;
        el.style.cursor = L.some(l => Math.abs(x - px(l.start_s)) <= 6 || Math.abs(x - px(l.end_s)) <= 6) ? 'ew-resize' : L.some(l => x > px(l.start_s) && x < px(l.end_s)) ? 'pointer' : 'crosshair';
        return;
      }
      const q = xOf(el, e), v = clamp(round1(q.t), 0, axis);
      if (d.mode === 'edge') { const moved = moveEdge(d.id, d.edge, v); if (moved != null) setDrag({mode: 'edge', t: moved}); }
      else { if (Math.abs(q.x - d.x0) > 4) d.moved = true; if (d.moved) { d.b = v; setDrag({mode: 'create', a: d.a, b: v}); } }
    };
    const onTrackUp = () => {
      const d = dragRef.current; dragRef.current = null; setDrag(null);
      if (!d) return;
      if (d.mode === 'edge') { store.commit(d.id, 0); return; }
      if (!d.moved) { seek(d.a); return; }
      create(Math.min(d.a, d.b), Math.max(d.a, d.b));
    };
    const onRuler = kind => e => {
      if (kind === 'down') { if (e.button != null && e.button !== 0) return; e.preventDefault(); ruler.current.setPointerCapture?.(e.pointerId); scrubRef.current = true; }
      if (kind === 'up') { scrubRef.current = false; return; }
      if (!scrubRef.current) return;
      seek(xOf(ruler.current, e).t);
    };
    // Ruler ticks: about eight labels across the visible span.
    const step = span > 150 ? 30 : span > 40 ? 10 : 2, ticks = [];
    for (let x = Math.ceil(view0 / step) * step; x <= view0 + span + .001; x += step) ticks.push(x);
    const ghost = drag?.mode === 'create' ? {a: Math.min(drag.a, drag.b), b: Math.max(drag.a, drag.b)} : markIn != null && t > markIn ? {a: markIn, b: t} : null;
    const tip = drag ? (drag.mode === 'edge' ? drag.t : drag.b) : null;
    const phOn = t >= view0 && t <= view0 + span;
    const cueRows = cues.map((c, i) => ({c, next: cues[i + 1] ? cues[i + 1].t_s : axis})).filter(({c}) => c.t_s >= view0 && c.t_s <= view0 + span);
    const answer = arm => { const r = runAt(runs[arm], t); return r ? r.choice === 'abstain' ? ['insufficient evidence', 'pl-ans-abstain'] : [actName(r.choice) + (r.p != null ? ' ' + percent(r.p) : ''), activityClass(r.choice)] : ['no current answer', 'pl-ans-none']; };
    const cueNear = s => [...cues].filter(c => c.t_s <= s + 3).pop();
    const save = selected ? store.saves[selected.id] : null;
    const saveText = s => s?.state === 'saving' ? 'Saving…' : s?.state === 'deleting' ? 'Deleting…' : s?.state === 'failed' ? (s.deleting ? 'Couldn’t delete' : 'Couldn’t save') : s?.state === 'saved' ? 'Saved' : '';
    const unscorable = L.filter(l => (l.time_basis || 'session') !== 'session');
    const clockText = clockVerified ?
      'Times are on the session clock, the clock of the sensor and model timeline. Labels made here are scored against each arm’s answers on the same clock, from the time each answer arrived.' :
      'Times are on the session clock, the clock of the sensor and model timeline. Labels made here are scored against each arm’s answers on the same clock, from the time each answer arrived. For this session the video is placed on that clock second-for-second (video second = session second), the same approximation as Pipeline detail; the measured offset is under about a second, so an edge can look up to about a second off. That affects where the frame appears, not whether a reviewed label is scored.';
    return <div className="pl-lv" role="tabpanel" aria-label="Label review">
      <p className="pl-lv-howto pl-label-howto">{LABEL_HOWTO}</p>
      {unscorable.length > 0 && <div className="pl-lv-unscorable" role="note"><div><strong>These labels can’t be scored yet</strong>
        <p>{unscorable.length === 1 ? 'One label was' : unscorable.length + ' labels were'} made on the video clock, before labels were saved on the session clock. Scoring counts only session-clock labels. Converting keeps each time as it is (the video is placed on the session clock second-for-second, within about a second) and marks every converted label for review again so you can check it.</p></div>
        <Button primary onClick={convert}>Convert to session clock</Button></div>}
      <div className="pl-lv-grid">
        <div className="pl-lv-main">
          <div className={'pl-lv-video' + (hasVideo ? '' : ' pl-lv-novideo')} onClick={e => { if (!e.target.closest('button')) togglePlay(); }}>
            {hasVideo ? <VideoSource media={videoMedia} session={session} t={t} playing={playing && !hiddenDoc} speed={speed} review bare onStatus={setVstatus} /> :
              <div className="pl-lv-msg">No recorded video covers this session. Labels can still be edited on the timeline.</div>}
            {at && <span className={'pl-lv-tag ' + activityClass(at.activity)}>{(at.reviewed ? '✓ ' : '') + actName(at.activity) + (at.note ? ' · ' + at.note : '') + (at.reviewed ? '' : ' · needs review')}</span>}
            {hasVideo && !hiddenDoc && vstatus.state !== 'ready' && <div className="pl-lv-overlay pl-lv-opening" role="status">{vstatus.state === 'loading' ? <><i className="pl-spinner" aria-hidden="true" />{vstatus.reason && vstatus.reason !== 'Video opens when this tab is visible' ? vstatus.reason : 'Opening video track · ' + (session.camera_id || 'camera') + ' · ' + mmss(duration)}</> : vstatus.reason || 'Video unavailable at this time'}</div>}
            {hiddenDoc && <div className="pl-lv-overlay pl-lv-hidden" role="status">This tab is hidden: video opens when visible.</div>}
          </div>
          <div className="pl-lv-controls">
            <button type="button" className="pl-lv-play" aria-label={playing ? 'Pause' : 'Play'} onClick={togglePlay}>{playing ? <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="5" width="4" height="14" /><rect x="14" y="5" width="4" height="14" /></svg> : <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 5l12 7-12 7z" /></svg>}</button>
            <span className="pl-lv-time pl-lbl-time" aria-label="Playhead">{tenths(t)}<span className="pl-muted"> / {tenths(duration)}</span></span>
            <button type="button" className="pl-lv-info" aria-label="About timing" aria-expanded={syncOpen} title="About timing" onClick={() => setSyncOpen(!syncOpen)}>i</button>
            <span className="pl-lv-group">{[[-5, '−5 s', 'Shift ←'], [-.1, '−0.1', '←'], [.1, '+0.1', '→'], [5, '+5 s', 'Shift →']].map(([d, label, k]) => <button key={label} type="button" title={k} onClick={() => seek(t + d)}>{label}</button>)}</span>
            <span className="pl-lv-group" role="group" aria-label="Playback speed">{[.5, 1, 2].map(v => <button key={v} type="button" aria-pressed={speed === v} onClick={() => setSpeed(v)}>{v}×</button>)}</span>
            <span className="pl-flex" />
            {markIn != null && <span className="pl-lv-markin pl-mono">in {mss1(markIn)}</span>}
            <button type="button" className="pl-lv-mark" onClick={doMarkIn}>Mark in <kbd>I</kbd></button>
            <button type="button" className="pl-lv-mark" onClick={doMarkOut}>Mark out <kbd>O</kbd></button>
          </div>
          {syncOpen && <p className="pl-lv-sync pl-lbl-clock" role="note">{clockText}</p>}
          <div className="pl-lv-timeline">
            <div className="pl-lv-tlhead"><span className="pl-label">Timeline</span>
              <span className="pl-lv-group" role="group" aria-label="Timeline zoom">{[['fit', 'Fit'], ['60', '1 min'], ['20', '20 s']].map(([k, label]) => <button key={k} type="button" aria-pressed={zoom === k} onClick={() => { setZoom(k); setV0(clamp((selected ? selected.start_s : t) - (k === 'fit' ? 0 : (k === '60' ? 60 : 20) * .35), 0, axis)); }}>{label}</button>)}</span>
              <span className="pl-flex" /><button type="button" className="pl-lv-small" aria-pressed={!hidePred} onClick={() => setHidePred(!hidePred)}>{hidePred ? 'Show predictions' : 'Hide predictions'}</button></div>
            <div className="pl-lv-rows">
              <span />
              <div ref={ruler} className="pl-lv-ruler" role="slider" tabIndex={-1} aria-label="Scrub timeline" aria-valuemin={0} aria-valuemax={round1(duration)} aria-valuenow={round1(t)} aria-valuetext={tenths(t)}
                onPointerDown={onRuler('down')} onPointerMove={onRuler('move')} onPointerUp={onRuler('up')} onPointerCancel={onRuler('up')}>
                {ticks.map(x => <React.Fragment key={x}><i className="pl-lv-tick" style={{left: P(x) + '%'}} /><span className="pl-lv-ticklabel" style={{left: P(x) + '%'}}>{step < 10 ? mss1(x).replace(/\.0$/, '') : mmss(x)}</span></React.Fragment>)}
                {phOn && <i className="pl-lv-phmark" style={{left: P(t) + '%'}} />}
                {tip != null && <span className="pl-lv-tip" role="status" style={{left: P(tip) + '%'}}>{tenths(tip)}</span>}
              </div>
              <span className="pl-lv-rowname pl-lv-rowname-main">LABELS</span>
              <div ref={track} className="pl-lv-track pl-lbl-track" role="group" aria-label="Label track" onPointerDown={onTrackDown} onPointerMove={onTrackMove} onPointerUp={onTrackUp} onPointerCancel={() => { dragRef.current = null; setDrag(null); }}>
                {!L.length && <span className="pl-lv-trackhint">Drag here to create a label, or press I then O while the video plays</span>}
                {L.filter(l => inView(l.start_s, l.end_s)).map(l => <div key={l.id} data-label-id={l.id} title={actName(l.activity) + ' · ' + mss1(l.start_s) + '–' + mss1(l.end_s) + ' · ' + (l.reviewed ? 'reviewed' : 'needs review') + ((l.time_basis || 'session') !== 'session' ? ' · video clock' : '')}
                  className={'pl-lb ' + swatchClass(l.activity, l.reviewed) + (l.reviewed ? ' pl-lb-reviewed' : '') + (l.id === sel ? ' pl-lb-selected' : '') + ((l.time_basis || 'session') !== 'session' ? ' pl-lb-video' : '')}
                  style={{left: P(l.start_s) + '%', width: Math.max(.2, (l.end_s - l.start_s) / span * 100) + '%'}}>
                  <span className="pl-lb-text">{(l.reviewed ? '✓ ' : '') + actName(l.activity) + (l.note ? ' · ' + l.note : '')}</span>
                  {l.id === sel && <><i className="pl-lb-edge pl-lb-edge-start" data-edge="start" /><i className="pl-lb-edge pl-lb-edge-end" data-edge="end" /></>}</div>)}
                {ghost && inView(ghost.a, ghost.b) && <div className="pl-lv-ghost" style={{left: P(ghost.a) + '%', width: (ghost.b - ghost.a) / span * 100 + '%'}} />}
                {phOn && <i className="pl-lv-ph" style={{left: P(t) + '%'}} />}
              </div>
              <span className="pl-lv-rowname">YOUR CUES</span>
              <div className="pl-lv-cues">{cueRows.map(({c, next}) => <span key={c.id || c.t_s} className="pl-lv-cue" title={mmss(c.t_s) + ' · ' + (c.label || 'bookmark')} style={{left: P(c.t_s) + '%', width: Math.max(.5, (Math.min(next, view0 + span) - c.t_s) / span * 100) + '%'}}>{c.label || 'bookmark'}</span>)}
                {!cues.length && <span className="pl-lv-none">No cues were bookmarked while recording</span>}{phOn && <i className="pl-lv-ph pl-lv-ph-soft" style={{left: P(t) + '%'}} />}</div>
              {!hidePred && ['A', 'B'].map(arm => <React.Fragment key={arm}><span className={'pl-lv-rowname pl-lv-arm-' + arm}>ARM {arm}</span>
                <div className="pl-lv-strip" data-arm={arm}>{runs[arm].filter(r => inView(r.s, r.e)).map((r, i) => <i key={i} className={'pl-lv-run ' + (r.choice === 'abstain' ? 'pl-ans-abstain' : activityClass(r.choice))} title={(r.choice === 'abstain' ? 'Insufficient evidence' : actName(r.choice)) + ' · ' + mss1(r.s) + '–' + mss1(r.e)}
                  style={{left: P(Math.max(r.s, view0)) + '%', width: (Math.min(r.e, view0 + span) - Math.max(r.s, view0)) / span * 100 + '%'}} />)}
                  {predState !== 'ready' && <span className="pl-lv-none">{predState === 'loading' ? 'Loading arm answers…' : predState === 'failed' ? 'Arm answers unavailable · ' + (slot?.error || 'timeline read failed') : 'Arm answers are not available here'}</span>}
                  {phOn && <i className="pl-lv-ph pl-lv-ph-soft" style={{left: P(t) + '%'}} />}</div></React.Fragment>)}
            </div>
            <div className="pl-lv-legend">{LABEL_ACTIVITIES.map(a => <span key={a}><i className={swatchClass(a, true)} />{actName(a)}</span>)}
              <span><i className="pl-sw pl-act-unknown pl-sw-open" />Needs review</span><span><i className="pl-sw pl-lv-legend-rev">✓</i>Reviewed</span>
              {!hidePred && <span><i className="pl-lv-legend-abstain" />Arm abstained · insufficient evidence</span>}</div>
            <div className="pl-lv-readout" aria-live="off"><span className="pl-mono">{tenths(t)}</span>
              <span>· Label <b className={at ? activityClass(at.activity) : 'pl-ans-none'}>{at ? actName(at.activity) + (at.reviewed ? '' : ' (needs review)') : 'none'}</b></span>
              {!hidePred && ['A', 'B'].map(arm => { const [text, cls] = answer(arm); return <span key={arm}>· Arm {arm} <b className={cls}>{text}</b></span>; })}</div>
            <p className="pl-lv-keys pl-mono">Space play · ←/→ 0.1 s · Shift ←/→ 5 s · I / O mark in / out · Enter reviewed · Tab next label · Delete remove · Esc deselect</p>
          </div>
        </div>
        <aside className="pl-lv-aside">
          {ask.length > 0 && (() => { const {label, cue} = ask[0], plain = !cue.label || cue.label.trim() === DEFAULT_CUE;
            return <section className="pl-insp pl-lv-ask" aria-label="Choose the activity for a cue">
              <div className="pl-insp-head"><span className="pl-label">Cue activity</span><span className="pl-flex" /><span className="pl-mono pl-muted">{ask.length} left</span></div>
              <p>{plain ? 'Your bookmark' : 'Your cue “' + cue.label + '”'} at {mmss(cue.t_s)} names no activity. What were you doing from {mss1(label.start_s)} to {mss1(label.end_s)}?</p>
              <div className="pl-insp-acts" role="group" aria-label="Activity for this cue">{LABEL_ACTIVITIES.map(a => <button key={a} type="button" onClick={() => answerAsk(a)}><i className={swatchClass(a, true)} />{actName(a)}</button>)}</div>
              <button type="button" className="pl-insp-delete" onClick={() => answerAsk(null)}>Skip this cue</button></section>; })()}
          {selected && <section className="pl-insp pl-lbl-edit" aria-label="Selected label">
            <div className="pl-insp-head"><i className={swatchClass(selected.activity, true)} /><span className="pl-label">Label {index + 1} of {L.length}</span><span className="pl-flex" />
              <span className={'pl-insp-save' + (save?.state === 'failed' ? ' pl-warn-text' : '')} role="status" aria-live="polite">{saveText(save)}{save?.state === 'failed' && <Button className="pl-mini-retry" onClick={() => store.retry(selected.id)}>Retry</Button>}</span>
              <button type="button" className="pl-insp-close" aria-label="Close label" onClick={() => { setSel(null); setConfirmDel(null); }}>×</button></div>
            {save?.state === 'failed' && save.error && <p className="pl-insp-error">{save.error}</p>}
            <div className="pl-insp-block"><span className="pl-insp-k">Activity</span>
              <div className="pl-insp-acts" role="group" aria-label="Activity">{LABEL_ACTIVITIES.map(a => <button key={a} type="button" aria-pressed={selected.activity === a} onClick={() => store.edit(selected.id, {activity: a}, 0)}><i className={swatchClass(a, true)} />{actName(a)}</button>)}</div>
              {!LABEL_ACTIVITIES.includes(selected.activity) && <small>Saved as “{actName(selected.activity)}”. Choose one of the activities above.</small>}</div>
            {[['Start', 'start', 'start_s'], ['End', 'end', 'end_s']].map(([name, edge, field]) => <div key={edge} className="pl-insp-edge"><div><span className="pl-insp-k">{name}</span><span className="pl-insp-v">{tenths(selected[field])}</span></div>
              <div className="pl-insp-row"><button type="button" aria-label={name + ' −0.1 s'} onClick={() => moveEdge(selected.id, edge, selected[field] - .1, 400)}>−0.1 s</button><button type="button" aria-label={name + ' +0.1 s'} onClick={() => moveEdge(selected.id, edge, selected[field] + .1, 400)}>+0.1 s</button>
                <button type="button" className="pl-insp-grow" onClick={() => moveEdge(selected.id, edge, t, 0)}>Set {edge} to playhead</button><button type="button" title="Jump video here" aria-label={'Go to ' + edge} onClick={() => seek(selected[field])}>Go</button></div></div>)}
            <span className="pl-mono pl-muted">Length {(selected.end_s - selected.start_s).toFixed(1)} s · {(selected.time_basis || 'session') === 'session' ? 'session clock' : 'video clock · not scored'}</span>
            {(selected.time_basis || 'session') !== 'session' && <p className="pl-lbl-clock">This older label uses the video clock, so the findings do not score it. <Button onClick={() => store.edit(selected.id, {time_basis: 'session', reviewed: false}, 0)}>Use the session clock so it is scored</Button></p>}
            {(() => { const c = cueNear(selected.start_s); return c ? <span className="pl-insp-cue">Your cue: “{c.label || 'bookmark'}” at {mmss(c.t_s)}</span> : null; })()}
            <label className="pl-insp-note"><span className="pl-insp-k">Note (optional)</span><input aria-label="Note" value={selected.note || ''} maxLength={1000} placeholder="e.g. closed book" onChange={e => store.edit(selected.id, {note: e.target.value})} onBlur={() => store.commit(selected.id, 0)} /></label>
            {selected.reviewed ? <div className="pl-insp-reviewed"><span>✓ Reviewed</span><span className="pl-flex" /><button type="button" onClick={() => review(selected.id, false)}>Mark as needs review</button></div> :
              <button type="button" className="pl-insp-review" onClick={() => review(selected.id, true)}>Mark reviewed <kbd>Enter</kbd></button>}
            {confirmDel === selected.id ? <div className="pl-insp-confirm" role="alertdialog" aria-label="Delete this label?"><span>Delete this label?</span><Button primary onClick={() => del(selected.id)}>Delete</Button><Button onClick={() => setConfirmDel(null)}>Cancel</Button></div> :
              <button type="button" className="pl-insp-delete" onClick={() => setConfirmDel(selected.id)}>Delete label</button>}
          </section>}
          {!L.length && !ask.length && <section className="pl-insp pl-lv-empty"><strong>No labels yet</strong><p className="pl-muted">{cues.length ? 'You added ' + cues.length + (cues.length === 1 ? ' cue' : ' cues') + ' while recording. Start from them and adjust each edge, or label from scratch.' : 'Play the video and mark each activity as you see it.'}</p>
            {cues.length > 0 && <Button primary onClick={fromCues}>Create labels from cues ({cues.length})</Button>}
            <Button onClick={() => setStartHelp(true)}>Start labelling</Button>
            {startHelp && <p className="pl-lv-help">Press Space to play. Press I when an activity starts and O when it stops, or drag across the label track.</p>}</section>}
          {L.length > 0 && <section className="pl-lv-list" aria-label="All labels"><div className="pl-lv-listhead"><span className="pl-label">All labels</span><span className="pl-mono pl-muted">{L.length}</span></div>
            <ol className="pl-lbl-list">{L.map(l => { const s = store.saves[l.id]; const st = s?.state === 'failed' || s?.state === 'saving' || s?.state === 'deleting' ? saveText(s) : l.reviewed ? '✓ Reviewed' : 'Needs review';
              return <li key={l.id}><button type="button" data-lbl-nav="1" aria-pressed={l.id === sel} onClick={() => select(l.id)}><i className={swatchClass(l.activity, l.reviewed)} />
                <span className="pl-lv-listname"><span>{actName(l.activity) + (l.note ? ' · ' + l.note : '')}</span><span className="pl-mono pl-muted">{mss1(l.start_s)} – {mss1(l.end_s)}{(l.time_basis || 'session') !== 'session' ? ' · video clock · not scored' : ''}</span></span>
                <span className={'pl-lv-liststate' + (s?.state === 'failed' ? ' pl-warn-text' : l.reviewed ? ' pl-lv-yes' : '')}>{st}</span></button></li>; })}</ol></section>}
          <p className="pl-lv-quiet">{EVALUATION_ONLY}</p>
        </aside>
      </div>
    </div>;
  }

  // Results the service stored automatically after the recording stopped: the
  // live answers against the saved labels, else against draft labels made from
  // the bookmarks (unreviewed; local evaluation data, never a model input).
  const AUTO_TRUTH = {saved_labels: 'the saved labels', bookmark_drafts: 'draft labels from the bookmarks (unreviewed)', none: 'nothing: no labels or activity bookmarks'};
  function AutoResults({r}) {
    const rows = Object.entries(r?.arms || {}).flatMap(([arm, acts]) => Object.entries(acts || {}).map(([act, m]) => ({arm, act, m: m || {}})));
    const sec = v => Number.isFinite(v) ? v.toFixed(1) + ' s' : '—';
    const n = Number.isFinite(r?.labels_used) ? r.labels_used : 0;
    return <section className="pl-score-auto pl-score-others" aria-label="Results"><div className="pl-score-lever"><span className="pl-label">Results</span>
      <span>Scored automatically after the recording against {AUTO_TRUTH[r?.truth] || 'labels'}{n ? ' · ' + n + ' interval' + (n === 1 ? '' : 's') : ''}.</span></div>
      {rows.length ? <table><thead><tr><th>Arm · activity</th><th>Labelled</th><th>Coverage</th><th>Accuracy when answered</th><th>Confidently wrong</th><th>Answered as another activity</th></tr></thead>
        <tbody>{rows.map(({arm, act, m}) => <tr key={arm + act}><td>{String(arm).toUpperCase()} · {actName(act === 'empty_room' ? 'other' : act)}{act === 'empty_room' ? ' (empty room)' : ''}</td><td>{sec(m.labelled_s)}</td><td>{percent(m.coverage)}</td><td>{percent(m.exact_at_coverage)}</td><td>{sec(m.false_confident_s)}</td>
          <td>{Object.entries(m.false_positive_s || {}).map(([k, v]) => actName(k) + ' ' + sec(v)).join(', ') || '—'}</td></tr>)}</tbody></table>
        : <p className="pl-muted">{r?.truth === 'none' ? 'Add bookmarks or labels to score this recording.' : 'No live answers to score.'}</p>}</section>;
  }
  // Score: the service's stored findings for this session (read-only). The
  // definitions are the controller's, not the design prototype's proposal.
  function ScoreTab({C, sid, detail, labels, onBack, demo}) {
    const [state, setState] = useState(detail.findings && !detail.findings_stale ? 'ready' : 'loading');
    const load = () => {
      if (!C.findings) { setState('ready'); return; }
      setState('loading');
      C.findings(sid).then(() => setState('ready'), () => setState('failed'));
    };
    useEffect(() => { if (detail.findings_stale || !detail.findings) load(); }, [sid]);
    const f = detail.findings || {};
    const live = (f.runs || []).filter(r => r.execution === 'live' && r.mode === 'live' && ['a', 'b'].includes(String(r.arm).toLowerCase()));
    const generations = Array.from(new Set(live.map(r => r.generation))).sort((a, b) => (a ?? 0) - (b ?? 0));
    const others = (f.runs || []).filter(r => !live.includes(r));
    const n = Number.isFinite(f.reviewed_intervals) ? f.reviewed_intervals : labels.filter(scorable).length;
    const leverText = g => {
      const x = (f.levers || []).find(l => l.generation === g) || (generations.length <= 1 ? (f.levers || []).slice(-1)[0] : null);
      const lever = x?.lever;
      return lever ? (lever.note || (lever.paths || []).join(', ')) : null;
    };
    const card = (row, arm) => {
      const m = row?.metrics || {};
      return <div key={arm} className={'pl-score-card pl-score-' + arm}><div className="pl-score-card-head"><span>Arm {arm}</span><span className="pl-mono pl-muted">{row ? row.run_id + (row.generation != null ? ' · generation ' + row.generation : '') : 'no live answers'}</span></div>
        <div className="pl-score-metrics">{[[percent(m.coverage), 'Coverage', 'time answered, not abstaining'], [percent(m.exact_match_at_coverage), 'Accuracy when answered', 'correct share of answered time'],
          [Number.isFinite(m.false_confident_seconds) ? m.false_confident_seconds.toFixed(1) + ' s' : '—', 'Confidently wrong', 'wrong at confidence ≥ 0.80']].map(([v, k, d]) => <div key={k}><b className="pl-mono">{v}</b><span>{k}</span><small>{d}</small></div>)}</div></div>;
    };
    const perActivity = (a, b) => {
      const keys = Array.from(new Set([...Object.keys(a?.metrics?.per_activity || {}), ...Object.keys(b?.metrics?.per_activity || {})]));
      const order = k => { const i = LABEL_ACTIVITIES.indexOf(k); return i < 0 ? 99 : i; };
      keys.sort((x, y) => order(x) - order(y) || x.localeCompare(y));
      if (!keys.length) return <p className="pl-muted">{a?.metrics && !a.metrics.per_activity ? 'Per-activity rows need the updated Lab service.' : 'No per-activity rows yet.'}</p>;
      const cell = (v, arm) => <span className={'pl-score-cell pl-score-' + arm}><span className="pl-mono">{percent(v)}</span><span className="pl-score-bar"><i style={{width: (Number.isFinite(v) ? Math.round(v * 100) : 0) + '%'}} /></span></span>;
      return <div className="pl-score-table" role="table" aria-label="Per-activity scores"><div className="pl-score-tr pl-score-th" role="row"><span role="columnheader">ACTIVITY</span><span role="columnheader">LABELLED</span><span role="columnheader">A COVERAGE</span><span role="columnheader">A ACCURACY</span><span role="columnheader" className="pl-score-B">B COVERAGE</span><span role="columnheader" className="pl-score-B">B ACCURACY</span></div>
        {keys.map(k => { const ra = a?.metrics?.per_activity?.[k], rb = b?.metrics?.per_activity?.[k];
          return <div key={k} className="pl-score-tr" role="row"><span role="cell" className="pl-score-act"><i className={swatchClass(k.includes('+') ? 'unknown' : k, true)} />{k.split('+').map(actName).join(' + ')}</span>
            <span role="cell" className="pl-mono pl-muted">{((ra || rb)?.reviewed_seconds ?? 0).toFixed(1)} s</span>{cell(ra?.coverage, 'A')}{cell(ra?.exact_match_at_coverage, 'A')}{cell(rb?.coverage, 'B')}{cell(rb?.exact_match_at_coverage, 'B')}</div>; })}</div>;
    };
    return <div className="pl-score" role="tabpanel" aria-label="Score">
      <div className="pl-score-top"><div><h2>Session score</h2><p className="pl-muted">Scored on {n} reviewed interval{n === 1 ? '' : 's'} from one session; not household-wide accuracy.</p></div><span className="pl-flex" />
        <Button onClick={load} disabled={state === 'loading' || demo}>Refresh score</Button><Button onClick={onBack}>Back to labels</Button></div>
      {state === 'loading' && <p className="pl-muted" role="status"><i className="pl-spinner" aria-hidden="true" />Reading the stored findings…</p>}
      {state === 'failed' && <p className="pl-lv-unscorable" role="alert">Could not read the findings: {detail.findings_error || 'the Lab service did not answer'}. Nothing was changed. <Button onClick={load}>Try again</Button></p>}
      {demo && <p className="pl-muted">Developer demo: synthetic sessions have no stored findings.</p>}
      {state !== 'loading' && !demo && !live.length && <p className="pl-lv-unscorable" role="note">{n ? 'No live Arm A/B answers were scored for this session.' : 'No reviewed session-clock labels yet. Mark labels reviewed, then score again.'} {f.status === 'insufficient_reviewed_data' ? 'The service reports insufficient reviewed data.' : ''}</p>}
      {(generations.length ? generations : []).map(g => { const a = live.find(r => r.generation === g && String(r.arm).toLowerCase() === 'a'), b = live.find(r => r.generation === g && String(r.arm).toLowerCase() === 'b'), lever = leverText(g);
        return <section key={String(g)} className="pl-score-gen" aria-label={'Generation ' + g}>
          <div className={'pl-score-lever' + (lever ? '' : ' pl-score-lever-none')}><span className="pl-label">{lever ? 'One lever differs' : 'Lever'}</span><span>{lever || 'Lever not recorded for this session'}</span>{lever && <span className="pl-muted">All other inputs, the JEV question and the cadence are identical.</span>}</div>
          <div className="pl-score-cards">{card(a, 'A')}{card(b, 'B')}</div>{perActivity(a, b)}</section>; })}
      {f.auto_results && typeof f.auto_results === 'object' && <AutoResults r={f.auto_results} />}
      {others.length > 0 && <details className="pl-score-others"><summary>Other scored runs · {others.length}</summary><table><thead><tr><th>Run</th><th>Coverage</th><th>Accuracy when answered</th><th>Confidently wrong</th></tr></thead><tbody>{others.map((r, i) => <tr key={i}><td>{r.run_id} · {r.arm}<small>{r.execution} · {r.mode}{r.job_id ? ' · job ' + r.job_id : ''}</small></td><td>{percent(r.metrics?.coverage)}</td><td>{percent(r.metrics?.exact_match_at_coverage)}</td><td>{Number.isFinite(r.metrics?.false_confident_seconds) ? r.metrics.false_confident_seconds.toFixed(1) + ' s' : '—'}</td></tr>)}</tbody></table></details>}
      <div className="pl-score-defs"><p>Coverage is the share of reviewed labelled time in which an arm had a current answer other than insufficient evidence. An answer counts from when it arrived until its valid-until time. Accuracy when answered is measured only over that answered time and needs the exact activity. Confidently wrong counts the seconds in which the current answer was wrong and its reported confidence was 0.80 or higher; these are the Lab service’s scoring rules.</p>
        <p>“Other” includes holding a closed book and the empty room. An arm that says Reading during the closed-book interval counts as wrong.</p><p>{EVALUATION_ONLY}</p></div>
    </div>;
  }
  // Compact view of window.__perceptionLabDelivery: what this view displayed, when.
  function DeliveryReadout() {
    const [snapshot, setSnapshot] = useState(() => C.delivery?.snapshot?.() || null);
    useEffect(() => {
      const timer = setInterval(() => setSnapshot(C.delivery?.snapshot?.() || null), 1000);
      return () => clearInterval(timer);
    }, []);
    if (!snapshot) return null;
    const s = ms => Number.isFinite(ms) ? (ms / 1000).toFixed(1) + ' s' : '—';
    const clock = snapshot.clock;
    return <section className="pl-source-health pl-delivery-readout" aria-label="Delivery measured in this view"><div><strong>Delivery · this view</strong><span className="pl-muted">{Number.isFinite(clock.offset_ms) ? 'Display age is an estimate: Lab clock offset ±' + s(clock.uncertainty_ms) + ' from live poll round trip.' : 'Display age needs Lab server time, which this service does not report.'}</span><Button onClick={() => { C.delivery?.reset?.(); setSnapshot(C.delivery?.snapshot?.() || null); }}>Reset</Button></div><ul>{['A', 'B'].map(id => {
          const a = snapshot.arms[id], failures = a ? Object.entries(a.failures).map(([code, n]) => code + ' ' + n).join(', ') : '';
          return <li key={id}><span>{id}</span><span className="pl-muted">{a ? a.frames_displayed + ' shown · cadence ' + s(a.visible_cadence_ms?.median) + ' median · longest freeze ' + s(a.longest_freeze_ms) + ' · display age ' + (a.display_age_ms ? '≈' + s(a.display_age_ms.median) + ' median' : 'unavailable') + ' · ' + (failures ? 'load failures ' + failures : 'no load failures') + (a.retries ? ' · ' + a.retries + ' retries' : '') : 'No analyzed frame shown yet'}</span></li>;
        })}</ul></section>;
  }
  // Existing media components the graph workspace's video dock reuses.
  const graphParts = Object.freeze({ Scene, FrameAge, LiveCameraVideo, VideoSource, replayHold });
  // A render exception inside the Lab must never unmount Home's whole React
  // root (a black screen with no message). It stops at the Lab surface, says
  // so, and offers a fresh view; recorded sessions are untouched.
  class LabBoundary extends React.Component {
    constructor(props) {
      super(props);
      this.state = {error: null};
    }
    static getDerivedStateFromError(error) {
      return {error};
    }
    componentDidCatch(error) {
      global.console?.error?.('[perception-lab] view failed to render', error);
    }
    render() {
      const {error} = this.state, {open, onClose, spatialMode, children} = this.props;
      if (!error) return children;
      if (!open) return null;
      const reason = String(error?.message || error || 'unknown error').slice(0, 300);
      return <section className={'pl-root ' + (spatialMode ? 'pl-spatial' : '')} aria-label="Perception Lab"><header className="pl-header"><div className="pl-header-title"><Button onClick={onClose} aria-label="Back">← Back</Button><div><h1>Perception Lab</h1><p>Choose setup → enable reasoning → record → compare</p></div></div></header>
        <div className="pl-message pl-view-error" role="alert"><span>This Lab view stopped drawing because of an error: {reason}. Recorded sessions and labels are unchanged.</span><Button primary onClick={() => this.setState({error: null})}>Reload Lab view</Button></div></section>;
    }
  }
  function HomePerceptionLab(props) {
    return <LabBoundary open={props.open} onClose={props.onClose} spatialMode={props.spatialMode}><LabSurface {...props} /></LabBoundary>;
  }
  function LabSurface({
    open,
    onClose,
    spatialMode,
    intent = null
  }) {
    const state = useLab(),
      [mode, setMode] = useState('live'),
      [catalog, setCatalog] = useState(false),
      [library, setLibrary] = useState(false),
      [experiment, setExperiment] = useState('placement-same'),
      [camera, setCamera] = useState('den_oak'),
      [name, setName] = useState(''),
      [source, setSource] = useState('rgb'),
      [split, setSplit] = useState(false),
      [selected, setSelected] = useState(null),
      [reviewId, setReviewId] = useState(null),
      [t, setT] = useState(0),
      [playing, setPlaying] = useState(false),
      [speed, setSpeed] = useState(1),
      [cols, setCols] = useState(['detectorA', 'detectorB']),
      [analyze, setAnalyze] = useState(false),
      [retro, setRetro] = useState(false),
      // Live JEV reasoning for new sessions: on unless this viewer last chose off.
      [liveReasoning, setLiveReasoning] = usePref('liveReasoning', true),
      [diagnose, setDiagnose] = useState(false),
      [fact, setFact] = useState(''),
      [diagnostic, setDiagnostic] = useState(null),
      [hidden, setHidden] = useState(false),
      [notice, setNotice] = useState(''),
      [range, setRange] = useState([.3, 5]),
      [deleteMedia, setDeleteMedia] = useState(false),
      [eraseRecords, setEraseRecords] = useState(false),
      [classic, setClassic] = useState(false),
      // The session being opened from a list: {id, error}. Reading a recorded
      // session takes seconds; the list says so instead of looking idle.
      [opening, setOpening] = useState(null),
      openSeq = useRef(0),
      // A short confirmation at the bottom of the Lab (bookmarks).
      [toast, setToast] = useState(null),
      toastTimer = useRef(null),
      // Bookmark presses go out one after another, in press order; none is dropped.
      marks = useRef(Promise.resolve()),
      // The start failure the owner dismissed (session id).
      [failureSeen, setFailureSeen] = useState(null),
      settleTries = useRef(0);
    // The v8 graph workspace is the main surface when its module loaded; a page
    // without it keeps the previous source, room and per-arm panels.
    const Graph = global.HomePerceptionLabGraph?.GraphHost ? global.HomePerceptionLabGraph : null,
      panels = classic || !Graph;
    const caps = state.capabilities,
      experiments = D.mergeCatalog(caps?.experiments),
      detail = state.detail || {},
      session = detail.session,
      recording = D.isRecording(session),
      exp = experiments.find(e => e.id === (recording ? session.experiment_id : experiment)) || experiments[0],
      duration = Math.max(1, (detail.videoMedia || detail.media)?.encoded_track?.duration_seconds ?? D.seconds(session)),
      review = mode === 'review' && !!reviewId;
    const cameraInfo = (caps?.cameras || []).find(c => c.id === (review ? session?.camera_id : camera));
    const recordedModalities = detail.session?.modalities || detail.media?.available_modalities || ['rgb', ...(detail.media?.items || []).map(m => m.metadata?.modality || (m.metadata?.format === 'depth_u16_le' ? 'depth' : null)).filter(Boolean), ...Object.keys(detail.frames || {})];
    const modalities = review ? state.mode === 'demo' ? detail.media?.depth ? ['rgb', 'depth', 'left', 'right'] : ['rgb'] : recordedModalities : Array.from(new Set([...(cameraInfo?.modalities || ['rgb']),...(caps?.live_planes?.[camera] || [])]));
    const mediaItems = detail.media?.items || [],
      videoMedia = detail.videoMedia || detail.media,
      depthMedia = detail.depthMedia || detail.media;
    const sourceTime = review ? t : duration;
    const depthItem = global.HomePerceptionLabMedia.sourcePlane(depthMedia, 'depth', sourceTime),
      depthFrame = depthItem ? {
        ...depthItem,
        media_id: depthItem.media_id || depthItem.id,
        generation: depthMedia.generation
      } : null;
    const imageMedia = detail.imageMedia || detail.media,
      imageItem = global.HomePerceptionLabMedia.sourcePlane(imageMedia, source, sourceTime),
      sourceFrame = (review ? null : detail.frames?.[source]) || (imageItem ? {
        ...imageItem.metadata,
        ...imageItem,
        site_id: session?.site_id,
        media_id: imageItem.media_id || imageItem.id,
        generation: imageMedia.generation
      } : null);
    // Replay source views hold only earlier imagery, aged at the cursor.
    const replayScene = review ? {canHold: replayHold(t), ageLabel: f => <FrameAge frame={f} review playhead={t} />, nextLabel: 'frame at cursor'} : {};
    const timelineVerified = state.mode === 'demo' || detail.timeline?.session_clock_verified === true || (videoMedia?.items || []).some(m => (m.content_type || '').startsWith('video/') && m.metadata?.clock_mapping_status === 'verified' && Number.isFinite(m.metadata?.timeline_start_ms));
    const action = useCallback(async (op, args = {}) => {
      try {
        return await C.action(op, args);
      } catch (_) {
        return null;
      }
    }, []);
    const flash = useCallback(text => {
      setToast(text);
      clearTimeout(toastTimer.current);
      toastTimer.current = setTimeout(() => setToast(null), 2800);
    }, []);
    useEffect(() => () => clearTimeout(toastTimer.current), []);
    // Bookmark the recording (or the open session) at its current time, with an
    // optional activity tag word as the free-text label.
    const bookmarkAt = review ? t : duration;
    const bookmark = useCallback(tag => {
      if (!session?.id) return;
      const sid = session.id, at = bookmarkAt, label = typeof tag === 'string' ? tag : undefined;
      marks.current = marks.current.then(async () => {
        try {
          if (C.bookmark) await C.bookmark(sid, at, label);
          else {
            // A page that booted an older data module: wait out the pending lock instead of dropping the press.
            for (let i = 0; i < 50 && C.getSnapshot().pending; i++) await new Promise(r => setTimeout(r, 200));
            const r = await C.action('bookmarks.add', {session_id: sid, t_s: at, ...(label ? {note: label} : {})});
            if (!r) throw Error('bookmark_not_confirmed');
          }
          flash('Bookmarked at ' + mmss(at) + (label ? ' · ' + label : ''));
        } catch (_) {
          flash('Couldn’t bookmark, try again');
        }
      });
      return marks.current;
    }, [session?.id, bookmarkAt, flash]);
    useEffect(() => {
      if (!document.getElementById('home-perception-lab-css')) {
        const l = document.createElement('link');
        l.id = 'home-perception-lab-css';
        l.rel = 'stylesheet';
        l.href = './home-perception-lab.css';
        document.head.appendChild(l);
      }
    }, []);
    useEffect(() => {
      if (open && state.status === 'idle') {
        if (new URLSearchParams(location.search).get('perceptionLabDemo') === '1') C.setMode('demo');else C.refresh();
        C.start();
      }
    }, [open, state.status]);
    useEffect(() => {
      if (caps?.cameras?.length && !caps.cameras.find(c => c.id === camera)) setCamera(caps.cameras[0].id);
    }, [caps]);
    useEffect(() => {
      if (!playing || !open || !review) return;
      const timer = setInterval(() => setT(x => {
        if (x >= duration) {
          setPlaying(false);
          return duration;
        }
        return Math.min(duration, x + .1 * speed);
      }), 100);
      return () => clearInterval(timer);
    }, [playing, open, review, duration, speed]);
    useEffect(() => {
      if (open && session?.id && state.mode === 'real' && (mode === 'live' || review)) C.mediaWindow(review ? t : duration, 'video');
    }, [open, session?.id, state.mode, mode, review, Math.floor((review ? t : duration) / 10)]);
    useEffect(() => {
      if (open && session?.id && state.mode === 'real' && ['depth', 'validity'].includes(source)) C.mediaWindow(review ? t : duration, 'depth');
    }, [open, session?.id, state.mode, source, Math.floor((review ? t : duration) * 2)]);
    useEffect(() => {
      if (open && session?.id && state.mode === 'real' && ['left', 'right', 'disparity', 'confidence'].includes(source)) C.mediaWindow(review ? t : duration, 'image');
    }, [open, session?.id, state.mode, source, Math.floor((review ? t : duration) * 2)]);
    useEffect(() => {
      if (review && state.mode === 'demo') C.open(reviewId, t).catch(() => {});
      // The graph reads its own compact replay projection; the heavier detail
      // read at the cursor is only for the per-arm panels.
      else if(open && review && state.mode==='real' && panels) C.replayWindow(t);
    }, [open, reviewId, Math.floor(t * 2), review, state.mode, panels]);
    useEffect(() => {
      if (!open) return;
      const key = e => {
        if (e.target.closest('input,textarea,select,[contenteditable]') || catalog || library || diagnose || analyze) return;
        // In a recorded session Esc belongs to the label review (deselect).
        if (e.key === 'Escape') {
          if (!review) onClose();
          return;
        }
        if (e.key.toLowerCase() === 'b' && !e.ctrlKey && !e.metaKey && !e.altKey && session) {
          e.preventDefault();
          bookmark();
        }
        // Space, arrows, I/O, Enter, Tab and Delete are handled by the session review.
      };
      document.addEventListener('keydown', key);
      return () => document.removeEventListener('keydown', key);
    }, [open, catalog, library, diagnose, analyze, session?.id, review, bookmark]);
    // Setup readiness that only waits for the last session's cleanup is
    // re-checked every few seconds (for about a minute) until it clears.
    const settling = open && state.mode === 'real' && state.status === 'ready' && !recording && !!D.readinessSettling?.(caps, exp);
    useEffect(() => {
      if (!settling) { settleTries.current = 0; return; }
      if (settleTries.current >= SETTLE_TRIES) return;
      const timer = setTimeout(() => { settleTries.current++; C.refresh(); }, SETTLE_MS);
      return () => clearTimeout(timer);
    }, [settling, caps]);
    // APT-P06: the Apartment camera dock opens a session at the analyzed
    // frame's time. Lab opens exactly that session or reports why it cannot.
    const openSessionRef = useRef(null), intentSeen = useRef(null);
    useEffect(() => {
      if (!open || !intent?.session_id || intentSeen.current === intent.seq || !openSessionRef.current) return;
      intentSeen.current = intent.seq;
      openSessionRef.current(intent.session_id, Number.isFinite(intent.t_s) ? intent.t_s : null);
    }, [open, intent?.seq, intent?.session_id, intent?.t_s, !!openSessionRef.current]);
    if (!open) return null;
    const selectExperiment = async x => {
      if (recording) {
        const r = await action('sessions.switch', {
          session_id: session.id,
          experiment_id: x.id,
          expected_revision: session.revision
        });
        if (!r) return;
      }
      setExperiment(x.id);
      if (x.saved_only || setupView(x).group === 'saved') setMode('review');
      setCatalog(false);
    };
    const begin = async () => {
      const r = await action('sessions.start', {
        camera_id: camera,
        experiment_id: experiment,
        // The service refuses reasoning it cannot provide; the remembered choice stays for when it can.
        reasoning_enabled: liveReasoning === true && caps?.reasoning?.available === true,
        name
      });
      if (r) {
        setReviewId(null);
        setMode('live');
        setNotice(r.session?.state === 'starting' ? 'The service prepares both workers before recording. Session status below updates automatically; Stop & save cancels preparation.' : D.isRecording(r.session) ? 'Recording is owned by the service and continues across Home tabs.' : r.session?.reason || 'Recording has not started. Review service admission and capture status.');
      }
    };
    const openSession = async (id, atTime = null) => {
      const request = ++openSeq.current;
      // The opening view replaces the list at once and reports each read.
      setOpening({id, error: null, started: Date.now(), steps: {}});
      setMode('review');
      setReviewId(null);
      setPlaying(false);
      const step = patch => { if (request === openSeq.current) setOpening(o => o && o.id === id && !o.error ? {...o, steps: {...o.steps, ...patch}} : o); };
      const onProgress = (name, value) => {
        if (name === 'record') {
          const labels = (value?.annotations || []).length, cues = (value?.timeline_events || value?.events || []).filter(e => e.kind === 'bookmark' || e.kind === 'bookmark.added').length;
          step({record: String(value?.session?.id || id).slice(0, 8), labels: labels + (labels === 1 ? ' label' : ' labels'), cues: cues + (cues === 1 ? ' cue' : ' cues')});
        } else if (name === 'media') {
          const secs = value?.encoded_track?.duration_seconds;
          step({media: Number.isFinite(secs) ? mmss(secs) + ' of video' : 'listed'});
        } else if (name === 'findings') step({findings: Number.isFinite(value?.reviewed_intervals) ? value.reviewed_intervals + ' reviewed' : 'read'});
      };
      try {
        await C.open(id, undefined, {onProgress});
        if (request !== openSeq.current) return;
        setOpening(null);
        setReviewId(id);
        setMode('review');
        setSource('rgb');
        setT(Number.isFinite(atTime) && atTime >= 0 ? atTime : 0);
        setPlaying(false);
      } catch (e) {
        if (request === openSeq.current) setOpening({id, error: e?.code || e?.message || 'session_open_failed'});
      }
    };
    openSessionRef.current = openSession;
    const openingName = opening ? (state.sessions.find(s => s.id === opening.id)?.name || opening.id) : '';
    const browserReadOnly = state.mode === 'real' && caps?.browser_access?.write === false;
    const liveAllowed = state.mode === 'demo' || caps?.recording_allowed === true;
    const expView = statusView(exp, session);
    // The latest start that never reached recording, with its plain reason.
    const failure = mode === 'live' && D.startFailure && session && session.id === state.sessions[0]?.id && session.id !== failureSeen ?
      D.startFailure({session, events: detail.events, caps}) : null;
    const reasoningOn = caps?.reasoning?.available === true;
    // Start is disabled with the explicit reason; the service still runs every gate.
    const startWhy = browserReadOnly ? 'This browser connection is read-only. View existing analysis in Apartment.' : D.startBlockedReason ? D.startBlockedReason({connected: state.status === 'ready', recordingAllowed: liveAllowed, experiment: exp, modalities}) :
      state.status !== 'ready' || !liveAllowed || exp.saved_only || exp.status === 'blocked' || exp.needs_depth && !modalities.includes('depth') ? 'Inputs and admission must be ready' : '';
    const annotations = detail.annotations || [],
      events = detail.events || [];
    const seekTo = x => setT(Math.max(0, Math.min(duration, x)));
    // Labels may end at the end of the session even when the video track is shorter.
    const labelEnd = Math.max(duration, D.seconds(session));
    const findings = D.scoreIntervals(annotations, D.columnRuns(detail.runs, cols[0]));
    // Live shows only the recording session (never an older saved one); replay shows the opened session.
    const graphSession = review ? session : state.activeSession || null,
      graphExp = experiments.find(e => e.id === graphSession?.experiment_id) || exp,
      graphCamera = review ? session?.camera_id : state.activeSession?.camera_id || camera,
      graphArms = !review && graphSession && detail.session?.id === graphSession.id ? detail.arms : null;
    // A page that booted an older data module keeps it (boot guard); new fields then stay hidden until reload.
    const acquisition = mode === 'live' && state.mode === 'real' ? D.acquisitionCounters?.(detail, state.status === 'ready') || [] : [];
    // The graph workspace, source/room panels and per-arm cards: the live view,
    // and in a recorded session the Pipeline detail tab.
    const graphEl = Graph ? <><Graph.GraphHost C={C} lab={state} mode={review ? 'replay' : 'live'} session={graphSession} cameraId={graphCamera} siteId={state.siteId} caps={caps} parts={graphParts}
        t={review ? t : duration} duration={duration} playing={review && playing} speed={speed} onTogglePlay={() => setPlaying(x => !x)} onSeek={x => setT(Math.max(0, Math.min(duration, x)))}
        detailArms={graphArms} videoMedia={videoMedia} setupTitle={setupName(graphExp)} hidePredictions={hidden} onBookmark={!review && recording ? bookmark : null} />
        <div className="pl-classic-toggle"><Button aria-expanded={classic} onClick={() => setClassic(!classic)}>{classic ? 'Hide' : 'Show'} source, room and per-arm panels</Button><span className="pl-muted">Depth and stereo planes, the room map, per-arm cards and pose evidence. {review ? 'Opening them adds the full detail read at the cursor.' : 'Opening them starts a second live video reader.'}</span></div></> : null;
    const panelsEl = panels && <div className="pl-workspace"><section className="pl-panel"><div className="pl-panel-heading"><span>{review ? 'Recorded source' : 'Camera source'}</span><div className="pl-inline"><select aria-label="Camera visualization" value={source} onChange={e => setSource(e.target.value)}>{[['rgb', 'RGB'], ['depth', 'Depth · metric color'], ['validity', 'Depth validity'], ['left', 'Left · rectified'], ['right', 'Right · rectified'], ['disparity', 'Disparity'], ['confidence', 'Stereo confidence']].map(([v, l]) => <option key={v} value={v} disabled={v !== 'rgb' && !(v === 'validity' ? modalities.includes('depth') : modalities.includes(v))}>{l}</option>)}</select><label><input type="checkbox" checked={split} onChange={e => setSplit(e.target.checked)} />Split</label><Badge>{review ? 'Recorded ' + D.formatTime(t) : state.mode === 'demo' ? 'Synthetic' : 'Live'}</Badge></div></div><div className={split ? 'pl-split' : ''}>{split && !(state.mode === 'real' && mode === 'live' && source === 'rgb') && <Scene frame={detail.frame} source="rgb" sessionId={session?.id} {...replayScene} />} {state.mode === 'real' && mode === 'live' && source === 'rgb' ? (open && <div key={state.siteId + ':' + camera}><LiveCameraVideo siteId={state.siteId} cameraId={camera} available={!!caps?.live_videos?.[camera]} snapshotAvailable={!!caps?.live_previews?.[camera]} /><SharedCameraContext siteId={state.siteId} cameraId={camera} /></div>) : state.mode === 'real' && mode === 'live' && ['depth','validity','left','right'].includes(source) ? (open && <LivePlanePreview siteId={state.siteId} cameraId={camera} mode={source} range={range}/>) : state.mode === 'real' && source === 'rgb' && global.HomePerceptionLabMedia.videoItems(videoMedia).length ? <VideoSource media={videoMedia} session={session} t={t} playing={playing} speed={speed} review={review} /> : state.mode === 'real' && ['depth', 'validity'].includes(source) && depthFrame ? <DepthSource frame={depthFrame} session={session} range={range} mode={source} /> : <Scene frame={source === 'rgb' || state.mode === 'demo' ? detail.frame : sourceFrame} source={source} sessionId={session?.id} {...replayScene} />}</div>{split && <p className="pl-muted pl-split-note">Each view retains its source time. Synchronization is shown only when capture identity is verified.</p>}{source !== 'rgb' && <div className="pl-depth-tools"><span>Display range only · camera settings unchanged</span><input aria-label="Depth near display range" type="range" min="0.2" max="3" step=".1" value={range[0]} onChange={e => setRange([Number(e.target.value), Math.max(range[1], Number(e.target.value))])} /><input aria-label="Depth far display range" type="range" min="3" max="10" step=".1" value={range[1]} onChange={e => setRange([range[0], Number(e.target.value)])} /><span>{range[0]}–{range[1]} m</span><p>Depth is aligned to rectified left, not RGB. Invalid pixels are unknown.</p>{(depthFrame?.metadata?.timeline_basis === 'controller_receipt' || sourceFrame?.metadata?.timeline_basis === 'controller_receipt') && <p role="status">Sampled archive shown by receipt time. Exposure timing and RGB synchronization are unverified.</p>}</div>}<div className="pl-frame-caption"><span>{state.mode === 'real' && mode === 'live' && source === 'rgb' ? 'Independent RGB preview' : detail.frame?.id || 'No source frame'}</span><span>{detail.frame?.synthetic ? 'Synthetic demonstration' : 'Capture timestamps come from the source'}</span></div></section><Room room={detail.room?.site_id === session?.site_id ? detail.room : null} selected={selected} onSelect={setSelected} /></div>;
    const comparisonsEl = !panels ? null : !hidden ? <div className="pl-comparisons">{['A', 'B'].map((id, i) => {
              const column = cols[i];
              const runs = D.causalResults(D.columnRuns(detail.runs, column), t, false).filter(r => r.mode !== 'live' || Number.isFinite(r.available_at_s) && r.available_at_s <= t);
              const run = runs[runs.length - 1];
              const armId = review ? column === 'jevB' || column === 'qB' ? 'B' : 'A' : id;
              const arm = review && state.mode !== 'demo' ? run?.result?.arm_snapshot || run?.arm_snapshot || null : detail.arms?.[armId];
              return <ArmCard key={id} id={id} arm={arm} sessionId={session?.id} review={review} columns={column} onColumn={value => setCols(x => x.map((v, j) => j === i ? value : v))} run={review ? run : null} playhead={t} serviceDelivery={review ? null : D.serviceDelivery?.(detail, armId, state.status === 'ready')} kind={review ? run?.mode === 'live' || column.startsWith('jev') ? 'Recorded live' : 'Generated after recording' : 'Live'} />;
            })}</div> : <Empty title="Model predictions hidden">Use the source recording to label independently. Show predictions when you are ready to compare.</Empty>;
    // Pipeline detail: the engineering view of the same session.
    const pipelineTab = review && session ? <>
      <div className="pl-pipe-top"><p>Engineering view of the same session. Nothing here is needed for labelling.</p><span className="pl-flex" /><div className="pl-inline pl-pipe-actions"><Button onClick={async () => {
              const r = await action('sessions.export', {
                session_id: session.id
              });
              if (r) {
                const u = URL.createObjectURL(new Blob([JSON.stringify(r.manifest || r, null, 2)], {
                  type: 'application/json'
                }));
                const a = document.createElement('a');
                a.href = u;
                a.download = 'perception-lab-' + session.id + '.json';
                a.click();
                URL.revokeObjectURL(u);
              }
            }}>Export metadata manifest</Button><ArchiveExport key={state.siteId+':'+session?.id} session={session} siteId={state.siteId} mode={state.mode} /><Button primary onClick={() => setAnalyze(true)}>Analyze session</Button><Button onClick={() => { C.invalidateProjections?.(session.id); C.open(session.id, undefined, {keepCursor: true}).catch(() => {}); }}>Refresh results</Button><Button disabled={recording} onClick={() => { setEraseRecords(false); setDeleteMedia(true); }}>Delete recording or session</Button></div></div>
      {detail.jobs?.length > 0 && <div className="pl-analysis-strip">{detail.jobs.map(job => <span key={job.id}>{job.kind}<Badge>{job.state}</Badge>{job.error&&<small>{job.error}</small>}{['requested', 'queued', 'running'].includes(job.state) && <Button disabled={state.pending} onClick={() => action('jobs.cancel', {
                job_id: job.id,
                session_id: session.id
              })}>Cancel</Button>}</span>)}</div>}{detail.runs?.length > 0 && <div className="pl-analysis-strip">{Array.from(new Set(detail.runs.map(r => r.run_id || r.id))).slice(0, 4).map(id => <span key={id}>{COLUMNS.find(c => c[0] === id)?.[1] || id}<Badge>{detail.runs.find(r => (r.run_id || r.id) === id)?.status || 'Unknown'}</Badge></span>)}</div>}
      {graphEl}{panelsEl}{comparisonsEl}
      {state.mode === 'real' && detail.capture_status && <section className="pl-capture" aria-label="Capture status"><h3>Capture status</h3><p className="pl-muted">A stopped recorder does not establish complete capture or synchronized geometry.</p><dl className="pl-capture-grid">{[['Stop reason', detail.capture_status.reason || 'Unknown'], ['Final segment collection', detail.capture_status.final_collection || 'Not confirmed'], ['Committed segments', detail.capture_status.committed_segments ?? 'Unknown'], ['Capture completeness', detail.capture_status.capture_completeness || 'Unknown'], ['Synchronization', detail.capture_status.synchronization || 'Unknown'], ['Geometry', detail.capture_status.geometric_validity || 'Unknown']].map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{String(v)}</dd></div>)}</dl></section>}
      {state.mode === 'real' && detail.findings?.specialist_delivery?.length > 0 && <section className="pl-panel pl-delivery"><h3>Specialist delivery</h3><p className="pl-muted">Coverage of offered inputs, including cadence skips. These counts do not measure activity accuracy.</p><table><thead><tr><th>Setup</th><th>Offered</th><th>Completed</th><th>Fresh completions</th><th>Skipped inputs</th></tr></thead><tbody>{detail.findings.specialist_delivery.map((row,i)=><tr key={i}><td>{row.arm.toUpperCase()} · {row.profile_id}<small>Generation {row.generation} · {row.sampling_hz == null ? 'Rate not recorded' : `${row.sampling_hz} Hz`}</small></td><td>{row.offered}</td><td>{row.completed}</td><td>{row.fresh_completed} ({(row.fresh_completed_fraction*100).toFixed(1)}%)</td><td>{Object.entries(row.drop_reasons).map(([reason,count])=><div key={reason}>{reason === 'source_sampling_cadence' ? 'Sampling cadence' : reason === 'replaced_by_latest' ? 'Replaced by newer input' : reason}: {count}</div>)}{Object.entries(row.states || {}).filter(([status]) => !['completed', 'dropped'].includes(status)).map(([status, count]) => <div key={status}>{status === 'indeterminate' ? 'Execution unknown' : status === 'cancelled' ? 'Cancelled' : status === 'rejected' ? 'Rejected' : status}: {count}</div>)}</td></tr>)}</tbody></table><p className="pl-muted">Fresh means the worker completed before source evidence expired. It does not prove timely display. Capture failures before an input was offered require separate capture diagnostics.</p></section>}
      <section className="pl-findings"><div className="pl-section-title"><div><h2>Findings · all recorded runs</h2><p className="pl-muted">Reviewed human intervals only. Missing support is never counted as a correct answer.</p></div><Button onClick={() => setDiagnose(true)}>Diagnose this moment</Button></div>{state.mode === 'demo' ? <><p className="pl-muted">Synthetic demo counts · {COLUMNS.find(c => c[0] === cols[0])?.[1]}</p><div className="pl-findings-counts">{[['Matched', findings.matched], ['Wrong', findings.wrong], ['Insufficient', findings.insufficient], ['Not scored', findings.unsupported]].map(([label, n]) => <div key={label}><b>{n}</b><span>{label}</span></div>)}</div></> : detail.findings?.runs?.length ? <table><thead><tr><th>Recorded run</th><th>Reviewed</th><th>Coverage</th><th>Correct when covered</th><th>False confident</th><th>Timing</th></tr></thead><tbody>{detail.findings.runs.map((row, i) => {
                  const m = row.metrics || {};
                  return <tr key={i}><td>{row.run_id} · {row.arm}<small>{row.job_id ? `Job ${row.job_id}` : `Generation ${row.generation ?? 'unknown'}`} · {row.mode}</small></td><td>{m.reviewed_seconds?.toFixed(1)} s</td><td>{Number.isFinite(m.coverage) ? (m.coverage * 100).toFixed(1) + '%' : 'Not scored'}<small>{m.coverage_kind === 'delivered_non_abstaining_time' ? 'Delivered evidence' : 'Offline interval support'}</small></td><td>{Number.isFinite(m.exact_match_at_coverage) ? (m.exact_match_at_coverage * 100).toFixed(1) + '%' : 'Not scored'}</td><td>{m.false_confident_seconds?.toFixed(1)} s</td><td>{m.latency_kind === 'actual_delivery' ? 'Actual delivery' : 'Offline · no live latency'}<small>{m.missed_transitions ?? 0} missed transitions</small></td></tr>;
                })}</tbody></table> : <p className="pl-muted">{detail.findings_error || 'Not enough reviewed human intervals and typed predictions to score this session.'}</p>}<p className="pl-muted">The service scores time coverage, delivery, and causal predictions separately. Diagnostic copies and retrospective outputs do not establish live performance. These results describe this session.</p><div className="pl-bookmarks">{events.filter(e => e.kind === 'bookmark').map(e => <Button key={e.id} onClick={() => setT(e.t_s)}>Bookmark {D.formatTime(e.t_s)}</Button>)}</div></section>
    </> : null;
    return <section className={'pl-root ' + (spatialMode ? 'pl-spatial' : '')} aria-label="Perception Lab"><header className="pl-header"><div className="pl-header-title"><Button onClick={onClose} aria-label="Back">← Back</Button><div><h1>Perception Lab</h1><p>Choose setup → enable reasoning → record → compare</p></div></div><div className="pl-segment pl-mode"><button aria-pressed={mode === 'live'} onClick={() => {
            setMode('live');
            setPlaying(false);
            if (state.activeSession) C.open(state.activeSession.id).catch(() => {});
          }}>Live & record</button><button aria-pressed={mode === 'review'} onClick={() => {
            setMode('review');
            setPlaying(false);
            C.refresh();
          }}>Review & analyze</button></div><Button onClick={() => setLibrary(true)}>Experiment library</Button><details className="pl-dev"><summary>Connection</summary><p>{state.mode === 'demo' ? 'Synthetic demonstration · no real services' : 'Real service connection'}</p><label>Home<select aria-label="Experiment home" disabled={!!state.activeSession || state.mode === 'demo'} value={state.siteId} onChange={e => C.setSite(e.target.value).catch(x => setNotice(x.message))}><option value="victoria">Victoria</option><option value="echo">Echo</option></select></label><Button disabled={!!state.activeSession} onClick={() => C.setMode(state.mode === 'demo' ? 'real' : 'demo').catch(e => setNotice(e.message))}>{state.mode === 'demo' ? 'Connect real service' : 'Use developer demo'}</Button><Button onClick={() => C.refresh()}>Reconnect</Button></details></header>
      <div className="pl-storage-line"><span>{state.mode === 'demo' ? 'DEVELOPER DEMO · SYNTHETIC DATA' : 'CONTEXT & RECOMMENDATIONS ONLY · NO ACTUATION'}</span><span>{state.activeSession && <b>{D.recordingLabel(state.activeSession)} {state.activeSession.camera_id} · {D.formatTime(D.seconds(state.activeSession))} · </b>}{caps?.storage?.policy || 'Storage policy not supplied'}{caps?.storage?.used_bytes != null ? ' · ' + (caps.storage.used_bytes / 1e9).toFixed(2) + ' GB used' : ''}</span></div>
      {(state.error || notice && !failure) && <div className="pl-message" role="status"><span>{state.error || notice}</span><Button onClick={() => {
          setNotice('');
          if (state.error) C.refresh();
        }}>{state.error ? 'Retry' : 'Dismiss'}</Button></div>}
      {opening?.error && <div className="pl-message pl-view-error" role="alert"><span>Could not open “{openingName}”: {opening.error}. Nothing was changed; the recording is still stored.</span><div className="pl-inline"><Button primary onClick={() => openSession(opening.id)}>Try again</Button><Button onClick={() => setOpening(null)}>Dismiss</Button></div></div>}
      {browserReadOnly&&<p role="status" className="pl-message" data-lab-browser-access="read-only">Read-only browser access · view existing sessions, camera and model results. Recording, model changes and saved edits are unavailable through this connection.</p>}
      {state.status === 'blocked' && <div className="pl-blocked"><b>Live experiment service unavailable</b><p>Configure a provisioned Lab connection to record or run models. The catalog remains available; the developer demo is an explicit separate mode.</p><Button onClick={() => setCatalog(true)}>Browse experiment catalog</Button></div>}
      {state.mode === 'real' && caps && caps.recording_allowed !== true && <details className="pl-readiness"><summary>{recording ? 'New recordings are blocked; the existing session remains visible' : 'Recording is blocked until capture and admission are verified'}</summary>{(Array.isArray(caps.gates) ? caps.gates : []).map(g => <p key={g.id}><b>{g.id}</b>: {g.state} {g.reason || ''}</p>)}<p>Browsing this workspace does not start a model or capture process.</p></details>}
      <div className="pl-scroll">
      {failure ? <section className="pl-message pl-view-error pl-start-failed" role="alert" data-reason={failure.code}><span><b>Couldn’t start: {failure.text}</b>{failure.code !== failure.reason && failure.reason ? ' (' + failure.reason.replaceAll('_', ' ') + ')' : ''}. Nothing was recorded.{startWhy ? ' ' + startWhy : ''}</span>
          <div className="pl-inline"><Button primary disabled={!!startWhy || state.pending} onClick={() => { setFailureSeen(failure.session_id); begin(); }}>Try again</Button><Button onClick={() => setFailureSeen(failure.session_id)}>Dismiss</Button></div></section> :
        mode === 'live' && !D.startFailure && ['worker_busy','worker_preparation_failed','worker_preparation_timeout','worker_preparation_uncertain','insufficient_worker_lease','paired_start_failed'].includes(session?.reason) && <section className="pl-message" role="alert">Comparison could not start: {session.reason.replaceAll('_', ' ')}. Review the session status before starting again.</section>}
      {mode === 'live' && caps?.worker_mode === 'on_demand_bounded' && <section className="pl-source-health" role="status"><div><strong>On-demand paired experiment · up to {caps.storage?.max_session_seconds} seconds</strong><p>Start prepares Jetson and LA, then records both. Stop saves the session and releases the workers. Other experiments remain unavailable until commissioned.</p>{session?.state === 'starting' && <p>Preparing comparison — recording has not started.</p>}</div></section>}
      {mode === 'live' && <><section className="pl-record-bar" aria-label="Choose a setup, enable reasoning, record"><div className="pl-step pl-step-setup"><span className="pl-label">1 · Setup</span><button className="pl-setup-pick" onClick={() => setCatalog(true)} aria-label={'Setup ' + setupName(exp) + ' · choose a setup'}>{setupName(exp)}<span>⌄</span></button><span className={'pl-setup-state pl-setup-' + expView.group}><Badge warn={!['ready', 'recording'].includes(expView.group)}>{expView.label}</Badge> {recording ? 'Switching appends a comparison segment' : expView.summary}</span><small>Question · {exp.question}</small>{!recording && expView.group === 'blocked' && state.mode === 'real' && <Button onClick={() => C.refresh()}>Check again</Button>}</div><div className="pl-step" aria-label="Live reasoning setting"><span className="pl-label">2 · Reasoning</span><label className="pl-check"><input type="checkbox" checked={recording ? session.reasoning_enabled === true : liveReasoning === true && reasoningOn} disabled={state.pending || !reasoningOn} onChange={e => recording ? action('sessions.switch', {session_id:session.id, reasoning_enabled:e.target.checked, expected_reasoning_revision:session.reasoning_revision}) : setLiveReasoning(e.target.checked)} />Live JEV reasoning</label><span className="pl-muted">{caps?.reasoning?.available ? 'Independent A/B histories. Sends permitted structured evidence only. Turning this off leaves perception and recording running.' : caps?.reasoning?.reason || 'Lab household JEV policy and provider are not provisioned.'}</span></div><div className="pl-step pl-record-action"><span className="pl-label">{recording ? '3 · ' + D.recordingLabel(session) : '3 · Record'}</span>{recording ? <><div className="pl-record-time"><i className="pl-rec-dot" />{D.formatTime(duration)}</div>
                {session.reasoning_enabled !== true && <span className="pl-pill pl-pill-warn pl-reasoning-off" role="status">JEV reasoning: off</span>}
                <BookmarkBar onMark={bookmark} /></> : <div className="pl-inline"><select aria-label="Camera" value={camera} onChange={e => {
                setCamera(e.target.value);
                setSource('rgb');
              }}>{(caps?.cameras || [{
                  id: 'unavailable',
                  name: 'No camera provisioned'
                }]).map(c => <option key={c.id} value={c.id}>{c.name || c.id}</option>)}</select><input aria-label="Session name" value={name} onChange={e => setName(e.target.value)} placeholder="Optional session name" /></div>}<span className="pl-muted">{recording ? 'Capture continues independently of model inference' : modalities.includes('depth') ? 'OAK-D Pro · stereo depth' : 'RGB · depth not supplied'}</span>{recording ? <Button primary disabled={state.pending} onClick={() => action('sessions.stop', {
                session_id: session.id,
                expected_revision: session.revision
              })}>Stop & save</Button> : <Button primary disabled={!!startWhy || state.pending} aria-describedby="pl-start-reason" onClick={begin}>Start & record session</Button>}<span className="pl-muted" id="pl-start-reason" role="status">{!recording && startWhy ? startWhy : 'Overlays are not burned in'}</span>{['saved', 'stopped', 'interrupted'].includes(session?.state) && <Button onClick={() => openSession(session.id)}>Open saved session</Button>}</div></section><div className="pl-try"><span className="pl-label">Try this</span>{exp.try_this?.map(task => <label key={task}><input type="checkbox" />{task}</label>)}<span className="pl-muted">or just explore{recording ? ' · bookmark each change with B or the Bookmark button beside the timer' : ''}</span></div><details className="pl-confusers"><summary>Confusing alternatives</summary><div>{D.ALTERNATIVES.map(a => <p key={a}>{a}</p>)}<p>Insufficient evidence is a valid answer when the available signals cannot distinguish these.</p></div></details></>}
      {mode === 'live' && state.mode === 'real' && D.recordingOnly(caps, state.status === 'ready') && <section className="pl-source-health" role="status" aria-label="Recording-only mode"><div><strong>Recording only: inference is disabled</strong><p>Use this mode to check capture, save a session, replay it and add labels. The comparison cards will have no model results. Switching experiments does not enable inference.</p></div></section>}
      {mode === 'live' && state.mode === 'real' && session && <section className="pl-source-health" aria-label="Source acquisition status"><div><strong>Source acquisition</strong><span className="pl-muted">Independent of recording and inference. Read status does not prove frame freshness or alignment.</span></div><ul>{D.sourceHealth(detail, state.status === 'ready').map(source => <li key={source.key}><span>{source.name}</span><Badge>{source.label}</Badge></li>)}</ul>{acquisition.length > 0 && <p className="pl-muted">Acquisition counters reported by the service: {acquisition.map(([key, value]) => key.replaceAll('_', ' ') + ' ' + (Number.isInteger(value) ? value : value.toFixed(1))).join(' · ')}</p>}</section>}
      {opening && !opening.error ? <OpeningView opening={opening} name={openingName} onBack={() => { openSeq.current++; setOpening(null); }} /> : <>
      {mode === 'review' && !review && <SessionList sessions={state.sessions} opening={opening} onOpen={openSession} onRefresh={() => C.refresh()} />}
      {review && session && <SessionReview key={session.id} C={C} session={session} detail={detail} duration={duration} labelEnd={labelEnd} t={t} onSeek={seekTo} playing={playing} setPlaying={setPlaying}
        speed={speed} setSpeed={setSpeed} hidePred={hidden} setHidePred={setHidden} videoMedia={videoMedia} clockVerified={timelineVerified} demo={state.mode === 'demo'}
        onBack={() => { setReviewId(null); setPlaying(false); C.refresh(); }} pipeline={pipelineTab} />}
      {mode === 'live' && <>{graphEl}{panelsEl}{comparisonsEl}{state.mode === 'real' && recording && <DeliveryReadout />}</>}
      </>}
      </div>
      {catalog && <Catalog experiments={experiments} current={recording ? session.experiment_id : experiment} recording={recording} viewOf={x => statusView(x, session)} onSelect={selectExperiment} onRecheck={state.mode === 'real' ? () => C.refresh() : null} onClose={() => setCatalog(false)} />}
      {toast && <div className="pl-toast pl-lab-toast" role="status">{toast}</div>}
      {library && <Library caps={caps} pending={state.pending} onClose={() => setLibrary(false)} onPrepare={async (ids, keepReady) => {
        const r = await action('jobs.create', {
          kind: 'preparation',
          artifact_ids: ids,
          keep_ready: keepReady
        });
        if (r) setNotice(r.error||r.job?.error||'Preparation request accepted. Readiness is reported by the service.');
      }} />}
      {deleteMedia && <Modal title={eraseRecords ? "Delete this session?" : "Delete recording media?"} onClose={() => setDeleteMedia(false)}><div className="pl-dialog-body"><label className="pl-check"><input type="checkbox" checked={eraseRecords} onChange={e => setEraseRecords(e.target.checked)} />Also delete labels, observations and analysis results</label><p>{eraseRecords ? 'Delete this local session and its recorded evidence, labels and results. A minimal deletion record remains. Sessions with external inference copies are blocked until deletion propagation is available. Exported copies and backups are not recalled.' : 'Delete the stored video, images, and numerical bundles for this session. Labels, results, and audit records remain.'} This cannot be undone.</p><Button primary disabled={state.pending} onClick={async () => {
            const r = await action('sessions.delete', {
              session_id: session.id,
              expected_revision: session.revision,
              erase_records: eraseRecords
            });
            if (r) {
              setDeleteMedia(false);
              if (eraseRecords) { setReviewId(null); setPlaying(false); setDiagnostic(null); }
              setNotice(eraseRecords ? 'Local session deleted. External exports and backups are not recalled.' : 'Recorded media deleted. Labels and audit history are retained.');
            }
          }}>{eraseRecords ? 'Delete session and records' : 'Delete recorded media'}</Button><Button onClick={() => setDeleteMedia(false)}>Keep recording</Button></div></Modal>}
      {analyze && <Modal title="Analyze session" onClose={() => setAnalyze(false)}><div className="pl-dialog-body"><p>Qwen runs sequentially. Recorded live results remain unchanged.</p>{COLUMNS.slice(2, 5).map(([id, label]) => <div className="pl-analysis-option" key={id}>{label}<small>{id === 'qBase' ? 'Sampled RGB + as-of HA state' : 'Same inputs plus recorded perception evidence'}</small></div>)}<label className="pl-check" title="Qwen-assisted JEV not implemented yet"><input type="checkbox" checked={false} disabled aria-describedby="pl-qjev-reason" />Also run Qwen-assisted JEV <small id="pl-qjev-reason">Qwen-assisted JEV not implemented yet; the service would reject the whole analysis job.</small></label><label className="pl-check"><input type="radio" name="pl-analysis-mode" checked={!retro} onChange={() => setRetro(false)} />As the session unfolded</label><label className="pl-check"><input type="radio" name="pl-analysis-mode" checked={retro} onChange={() => setRetro(true)} />Retrospective · may use future evidence</label><p className="pl-muted">Human labels are excluded. Replay itself never runs inference.</p><Button primary disabled={state.pending} onClick={async () => {
            const r = await action('jobs.create', {
              kind: 'analysis',
              session_id: session.id,
              run_ids: ['qBase', 'qA', 'qB'],
              mode: retro ? 'retrospective' : 'causal'
            });
            if (r) setAnalyze(false);
          }}>Queue analysis</Button></div></Modal>}
      {diagnose && <Modal title="Diagnose this moment" onClose={() => {
        setDiagnose(false);
        setDiagnostic(null);
      }}><div className="pl-dialog-body"><Badge warn>Diagnostic · excluded from scores</Badge><p>Correct one observed fact in a copy of the original packet. The original result and labels remain unchanged.</p><label>Corrected fact<textarea value={fact} onChange={e => setFact(e.target.value)} placeholder="For example: the object near the hands is a phone." /></label><Button primary disabled={!fact.trim() || state.pending} onClick={async () => {
            const r = await action('diagnostics.create', {
              session_id: session.id,
              t_s: t,
              source_run: cols[0],
              corrected_fact: fact
            });
            if (r) setDiagnostic(r.diagnostic || r);
          }}>Rerun reasoning only</Button>{diagnostic && <div className="pl-message">{diagnostic.answer || diagnostic.error || diagnostic.job?.error || diagnostic.state || diagnostic.job?.state || diagnostic.status || 'Diagnostic queued'}</div>}<p className="pl-muted">A changed answer suggests an evidence bottleneck; one diagnostic cannot prove the cause.</p></div></Modal>}
    </section>;
  }
  function RecordingBanner({
    onOpen,
    labOpen
  }) {
    const s = useLab(),
      rec = s.sessions.find(D.isRecording) || (D.isRecording(s.detail?.session) ? s.detail.session : null);
    if (!rec || labOpen) return null;
    return <div className="pl-global-recording"><i className="pl-rec-dot" /><strong>{s.mode === 'demo' ? 'Demo ' + D.recordingLabel(rec).toLowerCase() : D.recordingLabel(rec)} {D.formatTime(D.seconds(rec))}</strong><span>Perception Lab continues while you use Home.</span><Button onClick={onOpen}>Back to Perception Lab</Button></div>;
  }
  // Internal components for contract tests only; not a public surface.
  HomePerceptionLab.parts = Object.freeze({ Scene, ArmCard, DeliveryReadout, AutoResults, replayHold, LiveCameraVideo, LiveCameraPreview, SharedCameraContext, FrameAge, VideoSource,
    review: Object.freeze({ sessionKind, labelStatus, predictionRuns, guessActivity, cueLabels, labelFields, scorable }) });
  global.HomePerceptionLab = HomePerceptionLab;
  global.HomePerceptionLabRecordingBanner = RecordingBanner;
})(window);
