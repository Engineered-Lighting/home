/* Camera context in typed Home chat, using the existing authenticated Lab
 * transport. No HA tokens, model calls, new grants or action tools are used. */
(function (root) {
  "use strict";
  const CAMERAS = Object.freeze({
    victoria: [{id:'den', names:['den']}],
    echo: [
      {id:'living_room',names:['living room','living']},
      {id:'kitchen',names:['kitchen']},
      {id:'dining_room',names:['dining room','dining']},
      {id:'workshop',names:['workshop','office']},
      {id:'driveway',names:['driveway','front door','outside']},
    ],
  });
  let pending = null, busy = false, focus = null, generation = 0, active = null;
  const observations = new Map();
  let authorityTag = null;
  async function validateAuthority(onLoss) {
    if (!root.HG_WEB_MODE) return true;
    try {
      const response=await root.fetch('/auth/status',{credentials:'same-origin',redirect:'error',cache:'no-store',signal:AbortSignal.timeout(5000)});
      const value=await response.json();
      if(!response.ok || value.authenticated!==true || !/^[a-f0-9]{64}$/.test(value.authorityTag)) throw new Error('unauthorized');
      if(authorityTag && authorityTag!==value.authorityTag) {reset({privateData:true});onLoss?.();authorityTag=value.authorityTag;return false;}
      authorityTag=value.authorityTag;
      return true;
    } catch {reset({privateData:true});authorityTag=null;onLoss?.();return false;}
  }
  function reset({privateData = false} = {}) {
    generation++; pending = null; focus = null; active?.abort();
    if (privateData) observations.clear();
  }
  function subscribeSiteChange(cancel) {
    let site=root.HomeSpatialViewport?.getSelectedSiteId?.();
    return root.HomeSpatialViewport?.subscribe(() => {
      const next=root.HomeSpatialViewport?.getSelectedSiteId?.();
      if(next===site && next!==undefined) return;
      site=next;cancel();
    });
  }
  function retainImages(events, incoming) {
    if (!incoming.privateCameraContext || !incoming.cameraId) return events;
    return events.map(e => e.privateCameraContext && e.siteId === incoming.siteId &&
      e.cameraId === incoming.cameraId && e.snapshotUrl ?
      {...e, snapshotUrl: undefined, imageUnavailable: true, imageNoLongerRetained: true} : e);
  }
  const normalize = text => String(text || '').normalize('NFKC').toLowerCase()
    .replace(/[\u2018\u2019]/g,"'").replace(/[_-]/g,' ').replace(/\s+/g,' ').trim()
    .replace(/[?.!]+$/,'').replace(/ please$/,'');
  function target(text, inheritedSite) {
    const sites = [];
    let rest = text.replace(/\b(victoria|los angeles|la|echo park|echo)\b/g, name => {
      sites.push(name === 'victoria' ? 'victoria' : 'echo'); return ' ';
    });
    if (new Set(sites).size > 1) return {error:'Please name one room in each home, for example Victoria den and LA kitchen.'};
    const site = sites[0] || inheritedSite;
    rest = rest.replace(/'s\b/g,' ').replace(/\b(my|the|our|in|at|of|from|home|house|camera|cameras|feed|feeds|right now|now)\b/g,' ')
      .replace(/\s+/g,' ').trim();
    const choices = Object.entries(CAMERAS).flatMap(([siteId,cameras]) =>
      cameras.filter(c => (!site || siteId === site) && c.names.includes(rest)).map(c=>({siteId,cameraId:c.id})));
    if (choices.length === 1) return {targets:choices};
    if (!rest && site === 'victoria') return {targets:[{siteId:'victoria',cameraId:'den'}]};
    if (!rest || choices.length > 1) return {clarify: site === 'echo' ? 'la_room' : 'home'};
    return {error: site === 'victoria' ? 'Victoria currently has one connected camera: the den.' :
      'Which camera did you mean? Victoria: den. LA: living room, kitchen, dining room, workshop, or driveway.'};
  }
  function resolve(text) {
    const s = normalize(text);
    const prefix = /^(?:(?:can|could|would) you )?(?:please )?(?:show(?: me)?|look at|take a look at|check(?: on)?|describe|what (?:do|can) you see(?: in| at)?|what(?:'s|s| is) (?:going on|happening)(?: in| at)?|is (?:anyone|anybody|someone) (?:in|at))\s+(.+)$/;
    const match = prefix.exec(s);
    if (!match) return null;
    const subject = match[1];
    if (/\b(yesterday|last|earlier|history|historical|recorded|recording|footage|clip|photo|picture|image|remember|tomorrow)\b/.test(subject))
      return /\b(victoria|both homes|both houses)\b/.test(subject) ? {error:'I can check the connected cameras now. Historical or supplied images need a separate request.'} : null;
    if (/\b(turn|switch|set|dim|brighten|open|close|lock|unlock|start|stop|enable|disable|toggle|run|play|pause)\b/.test(subject))
      return /\b(victoria|both|los angeles|la)\b/.test(subject) ? {error:'Please ask for the camera view separately from device actions.'} : null;
    if (/^(?:both|both my|both of my|my two|the two) (?:homes|houses)(?: cameras)?$/.test(subject))
      return {clarify:'both_room'};
    // A fixed registry resolves explicit names; model output never chooses a site.
    if (!/\b(victoria|los angeles|la|echo|homes?|houses?|den|living|kitchen|dining|workshop|office|driveway|outside|camera)\b/.test(subject)) return null;
    const parts = subject.split(/\s+(?:and|&)\s+/);
    if (parts.length > 2) return {error:'Please check at most one camera per home in a request.'};
    const values = parts.map(part=>target(part));
    if (values.some(v=>v.error)) return values.find(v=>v.error);
    if (values.some(v=>v.clarify)) {
      if(values.some(v=>v.targets?.some(t=>t.siteId==='victoria'))) return {clarify:'both_room'};
      if(values.some(v=>v.targets?.length)) return {error:'Please name both cameras, for example LA kitchen and Victoria den.'};
      return {clarify:values.find(v=>v.clarify).clarify};
    }
    const targets = values.flatMap(v=>v.targets);
    if (new Set(targets.map(t=>t.siteId)).size !== targets.length)
      return {error:'Please check one camera per home at a time.'};
    return {targets};
  }
  function matches(text) { return resolve(text) !== null; }
  // A question naming no room and no home goes to the home the owner is in,
  // from the same resolver light commands use (HomeLightingControl.resolveHome).
  // It only picks a target; the check itself is unchanged. Victoria has one
  // camera, so it resolves fully; LA still asks which room.
  const HOME={echo:'LA',victoria:'Victoria'};
  function homeNote(home) {
    if(!home) return '';
    if(home.basis==='network' || home.basis==='phone') return ` (you're in ${HOME[home.site]})`;
    if(home.basis==='last') return ` (I couldn't tell where you are, so I used ${HOME[home.site]}, the last home you used)`;
    return '';
  }
  async function defaultHome(text, plan, options) {
    if(plan?.clarify!=='home' || !root.HG_WEB_MODE || typeof root.HomeLightingControl?.resolveHome!=='function' ||
        /\b(victoria|los angeles|la|echo|both)\b/.test(normalize(text))) return {plan};
    try {
      const home=await root.HomeLightingControl.resolveHome(options.homeChoice);
      if(home?.site==='victoria') return {plan:{targets:[{siteId:'victoria',cameraId:'den'}]},home};
      if(home?.site==='echo') return {plan:{clarify:'la_room'}};
    } catch {/* keep asking which home */}
    return {plan};
  }
  function answer(value, now, request) {
    const unavailable = "I can't get a current view of your Victoria den right now. No fresh camera description is available.";
    if (!value || !request || value.request_id !== request.request_id ||
        value.observed_at < request.accepted_at - 3 || value.generated_at > request.deadline ||
        value.schema_version !== 1 || value.status !== "current" ||
        value.site_id !== "victoria" || value.camera_id !== "den" ||
        value.processing_site_id !== "echo" || value.model !== "qwen3-vl-30b" ||
        typeof value.text !== "string" || !value.text.trim() || value.text.length > 4000 ||
        ![value.observed_at, value.generated_at, value.valid_until, now].every(Number.isFinite) ||
        value.observed_at <= 0 || value.observed_at > value.generated_at ||
        value.generated_at > now || value.valid_until <= now ||
        value.valid_until <= value.observed_at || value.valid_until - value.observed_at > 180) return unavailable;
    const stamp = new Date(value.observed_at * 1000).toISOString();
    return `Victoria den (${stamp}): ${value.text.trim()}`;
  }
  function receipt(value, requestId) {
    if (!value || value.version !== 1 || value.site_id !== 'victoria' || value.camera_id !== 'den' ||
        value.request_id !== requestId || !Number.isFinite(value.accepted_at) || !Number.isFinite(value.deadline) ||
        value.accepted_at <= 0 || value.deadline !== value.accepted_at + 15 ||
        !['queued','dispatching','running','indeterminate','terminal'].includes(value.state)) throw new Error('invalid receipt');
    return value;
  }
  function pause(signal) {
    return new Promise((resolve, reject) => {
      if (signal.aborted) return reject(new Error('cancelled'));
      const abort = () => { clearTimeout(timer); reject(new Error('cancelled')); };
      const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, 1000);
      signal.addEventListener('abort', abort, {once:true});
    });
  }
  async function refreshVictoria(options) {
    let settled = false, acceptedReceipt = false, observation = null;
    const requestId = root.crypto.randomUUID();
    const controller = new AbortController();
    const emit = event => options.addEvent({ ...event, privateCameraContext: true, siteId: "victoria" });
    let cancelled = false;
    const cancel = () => { cancelled = true; options.onCancel?.(); controller.abort(); };
    options.signal?.addEventListener('abort', cancel, {once:true});
    const unsubscribe = subscribeSiteChange(cancel);
    root.addEventListener?.("pagehide", cancel);
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const transport = root.HomePerceptionLabTransport;
      if (!transport) throw new Error("unavailable");
      const args = {siteId:'victoria', cameraId:'den', requestId};
      const call = operation => transport.request(operation, args, {signal:controller.signal});
      const started = root.performance.now();
      // Exactly one POST. Failure/timeout cannot authorize a replacement request.
      let state = receipt(await call('cameras.refresh'), requestId), value;
      acceptedReceipt = true;
      const accepted = state;
      // Acceptance occurs after POST starts. Adding the entire monotonic elapsed
      // interval gives a conservative upper bound on server time, independent of
      // the desktop wall clock. Slow transport shortens usable freshness.
      const serverNowUpper = () => accepted.accepted_at + (root.performance.now() - started) / 1000 + .001;
      while (!controller.signal.aborted && !cancelled && options.isCurrent()) {
        if (state.accepted_at !== accepted.accepted_at || state.deadline !== accepted.deadline ||
            serverNowUpper() > state.deadline || state.state === 'indeterminate') throw new Error('refresh unresolved');
        if (state.state === 'terminal') {
          settled = true;
          if (state.outcome !== 'completed') throw new Error('refresh failed');
          value = await call('cameras.context');
          if (value?.request_id === requestId) break;
        }
        await pause(controller.signal);
        state = receipt(await call('cameras.refreshStatus'), requestId);
      }
      if (!cancelled && !controller.signal.aborted && options.isCurrent()) {
        const description = answer(value, serverNowUpper(), accepted);
        if (!description.startsWith('Victoria den (')) {
          emit({ kind: 'home', text: description });
        } else {
          let snapshotUrl;
          try {
            const image = await call('cameras.refreshImage');
            if (image?.version === 1 && image.site_id === 'victoria' && image.camera_id === 'den' &&
                image.request_id === requestId && typeof value.frame_id === 'string' &&
                image.frame_id === value.frame_id && image.observed_at === value.observed_at &&
                image.valid_until === value.valid_until && image.valid_until > serverNowUpper() &&
                typeof image.image_base64 === 'string' && image.image_base64.length <= 349528 &&
                /^[A-Za-z0-9+/]+={0,2}$/.test(image.image_base64) && image.image_base64.length % 4 === 0) {
              const bytes = root.atob(image.image_base64);
              if (bytes.length >= 4 && bytes.length <= 262144 && bytes.startsWith('\xff\xd8') && bytes.endsWith('\xff\xd9'))
                snapshotUrl = 'data:image/jpeg;base64,' + image.image_base64;
            }
          } catch (error) {
            if (error?.status === 401 || error?.status === 403) throw error;
            // A missing image never causes another capture or inference request.
          }
          if (!cancelled && !controller.signal.aborted && options.isCurrent()) {
            observation = {siteId:'victoria',cameraId:'den',requestId,frameId:value.frame_id,
              description:value.text.trim(),observedAt:value.observed_at,validUntil:value.valid_until,
              timestampSemantics:'observed_at',status:'completed'};
            emit({ kind: 'perception',
              text: `victoria_den: ${value.text.trim().replace(/\s+/g, ' ')} · observed ${new Date(value.observed_at * 1000).toISOString()}`,
              snapshotUrl, imageUnavailable: !snapshotUrl, cameraId:'den', observation });
          }
        }
      }
    } catch (error) {
      if (!acceptedReceipt && [401,403,404,422,503].includes(error?.status)) settled = true;
      if ([401,403].includes(error?.status)) options.onAuthorityLoss?.();
      if (!cancelled && options.isCurrent()) {
        const denied = error?.status === 401 || error?.status === 403;
        emit({ kind: "home", text: denied
          ? "This Home session doesn't have access to the Victoria den camera context."
          : answer(null, Date.now() / 1000) });
      }
    } finally {
      clearTimeout(timer); unsubscribe?.(); root.removeEventListener?.("pagehide", cancel);
      options.signal?.removeEventListener('abort', cancel);
    }
    return observation || {siteId:'victoria',cameraId:'den',requestId,frameId:null,status:settled?'unavailable':'unknown'};
  }
  async function refreshLA(cameraId, options) {
    const controller = new AbortController(), requestId = root.crypto.randomUUID();
    let cancelled = false, settled = false, observation = null;
    const cancel = () => {cancelled=true;options.onCancel?.();controller.abort();};
    options.signal?.addEventListener('abort',cancel,{once:true});
    const unsubscribe = subscribeSiteChange(cancel);
    root.addEventListener?.('pagehide',cancel);
    const timer = setTimeout(()=>controller.abort(),32000);
    const emit = event => {if (!cancelled && options.isCurrent()) options.addEvent({...event,privateCameraContext:true,siteId:'echo'});};
    try {
      if (!root.HG_WEB_MODE) throw new Error('web only');
      const response = await root.fetch('/proxy/vision/describe',{
        method:'POST',credentials:'same-origin',redirect:'error',cache:'no-store',
        headers:{'Content-Type':'application/json'},signal:controller.signal,
        body:JSON.stringify({camera:cameraId,evidence_request_id:requestId}),
      });
      if (!response.ok) throw Object.assign(new Error('camera unavailable'),{status:response.status});
      const raw = await response.text();
      if (raw.length > 400000) throw new Error('oversize response');
      const value = JSON.parse(raw), e=value.evidence;
      if (value.camera !== cameraId || value.entity_id !== 'camera.'+cameraId ||
          typeof value.description !== 'string' || !value.description.trim() || value.description.length>4000 ||
          !e || e.version!==1 || e.site_id!=='echo' || e.request_id!==requestId ||
          e.timestamp_semantics!=='rtsp_acquisition_interval' ||
          ![e.started_at,e.retrieved_at,e.generated_at].every(Number.isFinite) ||
          e.started_at<=0 || e.retrieved_at<e.started_at || e.generated_at<e.retrieved_at ||
          e.generated_at-e.started_at>30 || !/^[a-f0-9]{64}$/.test(e.frame_id) ||
          typeof e.image_base64!=='string' || e.image_base64.length>349528 ||
          !/^[A-Za-z0-9+/]+={0,2}$/.test(e.image_base64)) throw new Error('invalid evidence');
      const bytes=root.atob(e.image_base64);
      if(bytes.length<4 || bytes.length>262144 || !bytes.startsWith('\xff\xd8') || !bytes.endsWith('\xff\xd9')) throw new Error('invalid image');
      settled = true;
      observation = {siteId:'echo',cameraId,requestId,frameId:e.frame_id,description:value.description.trim(),
        observedAt:e.retrieved_at,timestampSemantics:e.timestamp_semantics,status:'completed'};
      if (!controller.signal.aborted) emit({kind:'perception',
        text:`la_${cameraId}: ${value.description.trim().replace(/\s+/g,' ')} - retrieved ${new Date(e.retrieved_at*1000).toISOString()}`,
        snapshotUrl:'data:image/jpeg;base64,'+e.image_base64,cameraId,observation});
    } catch(error) {
      if ([401,403,404,422].includes(error?.status)) settled = true;
      if ([401,403].includes(error?.status)) options.onAuthorityLoss?.();
      emit({kind:'home',text:error?.status===401 || error?.status===403 ?
        "This Home session doesn't have access to the LA camera." :
        `I couldn't get a fresh view of the LA ${cameraId.replace(/_/g,' ')}. No other camera was substituted.`});
    } finally {clearTimeout(timer);unsubscribe?.();root.removeEventListener?.('pagehide',cancel);options.signal?.removeEventListener('abort',cancel);}
    return observation || {siteId:'echo',cameraId,requestId,frameId:null,status:settled?'unavailable':'unknown'};
  }
  async function run(text,options) {
    if(options.simActive) return false;
    if(options.validateAuthority && !await options.validateAuthority()) {
      options.addEvent({kind:'home',privateCameraContext:true,text:'I cannot verify this Home session right now. Private camera context has been cleared. Please reconnect before continuing.'});
      return true;
    }
    const turnId = root.crypto.randomUUID();
    const originalOptions = options;
    options = {...options,addEvent:event=>originalOptions.addEvent({...event,turnId,turnKey:turnId,privateCameraContext:true})};
    const s = normalize(text);
    let plan = resolve(text), inferred = null;
    if(plan?.clarify==='home') {
      ({plan, home:inferred} = await defaultHome(text, plan, originalOptions));
      if(originalOptions.isCurrent?.()===false) return true;
    }
    const refresh = /^(?:look(?: there)? again|check(?: there)? again|refresh(?: (?:it|that|the view))?|look at (?:it|that|there))$/.test(s);
    const recall = /^(?:what did you see(?: there)?|what (?:was|is) in (?:that|the) (?:image|picture)|describe (?:it|that|the image))$/.test(s);
    const detail = /^(?:what (?:colou?r|size|kind|type)|how many|is (?:the|that)|are (?:the|those)|can you (?:see|tell))\b/.test(s);
    if (!plan && /^what about\s+/.test(s) && /\b(victoria|los angeles|la|echo|den|kitchen|living|dining|workshop|driveway)\b/.test(s)) plan=target(s.replace(/^what about\s+/,''));
    if (!plan && refresh) plan=focus ? {targets:[focus]} : {clarify:'home'};
    if (!plan && (recall || (detail && observations.size))) {
      pending=null;
      const namedSite=/\b(victoria|los angeles|la|echo)\b/.exec(s);
      const differentSite=namedSite && (namedSite[1]==='victoria'?'victoria':'echo')!==focus?.siteId;
      const value=!differentSite && focus && observations.get(focus.siteId+':'+focus.cameraId);
      options.addEvent({kind:'user',text});
      options.addEvent({kind:'home',text:value ?
        `${detail ? 'I can only use the saved description below; details it does not establish are unknown. ' : ''}Saved description from ${value.siteId==='victoria'?'Victoria':'LA'} ${value.cameraId.replace(/_/g,' ')} (${value.timestampSemantics==='rtsp_acquisition_interval'?'retrieved':'observed'} ${new Date(value.observedAt*1000).toISOString()}; historical, not a live view):\n${value.description}` :
        'Which home and room did you mean? Please name the camera; I have no unambiguous saved view for this question.'});
      return true;
    }
    const old=pending;pending=null;
    if(!plan && old && old.until>root.performance.now() && old.isCurrent()) {
      if(!/^(?:cancel|never mind|nevermind)$/.test(s)) {
        const reply=target(s,old.kind==='home'?undefined:'echo');
        if(reply.targets && (old.kind!=='both_room' || reply.targets.every(t=>t.siteId==='echo'))) plan={targets:old.kind==='both_room' ?
          [{siteId:'victoria',cameraId:'den'},...reply.targets] : reply.targets};
      } else {options.addEvent({kind:'home',text:'Camera request cancelled.'});return true;}
    }
    if(!plan) {reset();return false;}
    // Preserve the native shell's established LA camera path. This release
    // adds cross-home routing only to authenticated Home web.
    if(!root.HG_WEB_MODE && !plan.targets?.some(t=>t.siteId==='victoria') &&
        !/\b(victoria|both)\b/.test(normalize(text))) return false;
    const emit=text=>options.addEvent({kind:'home',text,privateCameraContext:true});
    options.addEvent({kind:'user',text,privateCameraContext:true});
    if(busy) {emit('A camera check is already running. Please wait for its result.');return true;}
    focus=null;
    if(plan.error) {emit(plan.error);return true;}
    if(plan.clarify) {
      pending={kind:plan.clarify,until:root.performance.now()+60000,isCurrent:options.isCurrent};
      emit(plan.clarify==='home' ? 'Which home and room: Victoria den, or an LA camera?' :
        (plan.clarify==='both_room' ? 'I can check Victoria den too. ' : '')+
        'Which LA camera: living room, kitchen, dining room, workshop, or driveway?');
      return true;
    }
    busy=true;
    const ownGeneration=++generation;
    active=new AbortController();
    let cancelledBatch=false;
    const batch={...options,signal:active.signal,onCancel:()=>{cancelledBatch=true;},
      isCurrent:()=>!cancelledBatch && ownGeneration===generation && options.isCurrent()};
    try {
      for(const target of plan.targets) {
        if(!batch.isCurrent()) break;
        const note=inferred?.site===target.siteId ? homeNote(inferred) : '';
        options.addEvent({kind:'system',text:`Checking ${target.siteId==='victoria'?'Victoria':'LA'} ${target.cameraId.replace(/_/g,' ')}${note}...`,privateCameraContext:true});
        const outcome = target.siteId==='victoria' ? await refreshVictoria(batch) : await refreshLA(target.cameraId,batch);
        outcome.roomId=target.cameraId;
        if(batch.isCurrent()) {
          options.onOutcome?.(outcome);
          if(outcome.status==='completed') {
            observations.set(target.siteId+':'+target.cameraId,Object.freeze(outcome));
            if(plan.targets.length===1) focus=Object.freeze({...target});
          }
        }
        if(outcome.status==='unknown') {
          if(batch.isCurrent() && target !== plan.targets.at(-1)) emit('The remaining camera check was not started because the previous request has an unknown outcome. I will not retry it automatically.');
          break;
        }
      }
    } finally {busy=false;active=null;}
    return true;
  }
  root.HomeCameraQuery = Object.freeze({ matches, resolve, answer, run, reset, retainImages, validateAuthority });
})(window);
