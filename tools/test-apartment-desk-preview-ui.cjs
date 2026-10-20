'use strict';
// Actual JSX/controller and AST-extracted parent handlers. Player is injected at
// the validated-view boundary; overlay is authored. No browser/Three/live claim.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const React=require('react'),{JSDOM}=require('jsdom'),Babel=require('../app/src/vendor/babel-7.29.0/babel.min.js');
const Controller=require('../app/src/home-apartment-desk-aim-preview.js'),Aiming=require('../app/src/home-apartment-aiming.js');
const View=require('../app/src/home-apartment-perception-view.js'),SRC=path.resolve(__dirname,'../app/src');
const copy=structuredClone,hash=c=>c.repeat(64),pause=()=>new Promise(resolve=>setImmediate(resolve));
const binding={content_revision:'authored-preview-ui',registration_version:1,manifest_digest:hash('a'),frame_digest:hash('b'),canonical_collision_digest:hash('c')};
const identity={siteId:'victoria',contentRevision:binding.content_revision,registrationVersion:1,frameDigest:binding.frame_digest};
const flags=Object.fromEntries(['operational_geometry','geometry_authority','identity_authority','engagement_authority','absence_authority',
  'protection_authority','beam_authority','physical_permission','follow_allowed','lighting_authority','physical_commands'].map(k=>[k,false]));
function preview(event=1){return {available:true,status:'preview',reason:null,label:'Person visible · configured desk preview',
  virtual_preview_only:true,qualification:null,timingStatus:'unverified',sourceKey:'authored-current-'+event,remainingHoldS:1,validUntil:1800000002,
  observation:{schema:'home-lab-arm-observation-1',session_id:'authored-existing-session',capture_epoch:'authored-epoch',generation:1,
    engine_epoch:'authored-model',frame_id:'authored-frame-'+event,request_id:'authored-request-'+event},
  provenance:{source_event_id:event,configured_candidate_sha256:hash('f')},...flags,
  personPoint:{method:'approximate_supported_shoulder_midpoint',joint_ids:[5,6],point_m:[2,2,1.3],instance_ref:'object-0',error_bound_m:null},
  target:{kind:'configured_desk_centre',frame:'z_up_metric_floor0',unit:'m',point_m:[2,2,.8],normal_unit:[0,0,1],binding:copy(binding),
    target_ref:{schema:'home-registered-desk-target-2',digest:hash('d')},equipment_ref:{revision:2,digest:hash('e')}}};}
function overlayFixture(log=[]){
  const bank=new Map(Array.from({length:4},(_,i)=>{const id='fixture-'+i;return [id,{schema:'home-virtual-fixture-interaction-1',generation:1,
    fixture_id:id,mount_id:'mount-'+i,binding:copy(binding),equipment_revision:2,equipment_digest:hash('e'),truth_role:'virtual_preview',
    physical_commands:false,optical_origin_m:null,display_anchor_m:[i,0,2.4],display_origin_m:[i,0,2.4],horizontal_world_reference:[0,1,0],
    state:{pan_deg:0,tilt_deg:45,spotlight:{brightness:.3}},target:null}];}));
  const calls={aim:0,restore:0},before=copy([...bank.values()]);
  const overlay={virtualFixturesReadback:()=>({status:'ready',visible:true,physical_commands:false,revision:2,digest:hash('e'),
      fixtures:[...bank.values()].map(d=>({fixture_id:d.fixture_id,limits:{}}))}),
    virtualFixtureInteractionDescriptor(id){return copy(bank.get(id));},
    aimVirtualFixtureAtTarget(id,target){calls.aim++;log.push('aim');const d=bank.get(id);d.target=copy(target);d.state.pan_deg=45;d.state.tilt_deg=30;return {ok:true,descriptor:copy(d)};},
    restoreVirtualFixtureAim(id,snapshot){calls.restore++;log.push('restore');bank.set(id,copy(snapshot));return {ok:true};},
    setVirtualFixtureState(id,patch){log.push('set_state');Object.assign(bank.get(id).state,copy(patch));return {ok:true};}};
  return {overlay,calls,before,descriptors:()=>copy([...bank.values()])};
}
function controlledView(p=preview()){return {virtualTargetPreview:p,roomPose:{admitted:false},diagnosticRoomPose:{renderable:false},
  approximateRoomPose:{renderable:true,status:'approximate'},follow:{allowed:false},models:[],
  pose2d:{renderable:false,keypoints:[],edges:[],boxes:[],persons:[]}};}

async function mounted({owned=false,emitOnStart=true}={}){
  const dom=new JSDOM('<div id="root"></div>',{pretendToBeVisual:true,runScripts:'outside-only',url:'http://localhost/'}),w=dom.window;
  const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'],old=Object.fromEntries(keys.map(k=>[k,Object.getOwnPropertyDescriptor(global,k)]));
  for(const [k,v]of Object.entries({window:w,document:w.document,navigator:w.navigator,IS_REACT_ACT_ENVIRONMENT:true}))Object.defineProperty(global,k,{value:v,writable:true,configurable:true});
  const timers=new Map();let seq=0,now=0,playerOptions,player=null,analysisWatcher;
  const counts={sourceStart:0,sourceStop:0,playerStart:0,playerDestroy:0,layerDispose:0,layerDraw:0,watchOff:0};
  Object.defineProperty(w.document,'hidden',{configurable:true,writable:true,value:false});
  Object.defineProperty(w.performance,'now',{configurable:true,value:()=>now});
  w.setTimeout=(fn,ms)=>{timers.set(++seq,{fn,ms,kind:'timeout'});return seq;};w.clearTimeout=id=>timers.delete(id);
  w.setInterval=(fn,ms)=>{timers.set(++seq,{fn,ms,kind:'interval'});return seq;};w.clearInterval=id=>timers.delete(id);
  const session={id:'authored-existing-session',site_id:'victoria',camera_id:'den',generation:0,state:'recording'};
  const source={analysisState:()=>({status:owned?'recording':'idle',owned,session:owned?session:null}),
    watchAnalysis(fn){analysisWatcher=fn;return()=>{counts.watchOff++;analysisWatcher=null;};},listSessions:async()=>[session],
    analysisAvailability:async()=>({available:true}),startAnalysis(){counts.sourceStart++;throw Error('no UI analysis Start authorized by this fixture');},
    async stopAnalysis(){counts.sourceStop++;analysisWatcher?.({status:'stopped',owned:false,session:{...session,state:'stopped'}});},
    subscribe(){throw Error('controlled Player owns the validated-view seam only');},sampleClock:()=>({domain:'unix',now_s:1800000000,uncertainty_s:null}),
    readFrame(){throw Error('no image retrieval at the injected validated-view seam');}};
  const registration=[],changes=[],overlay=overlayFixture(),engine={siteId:'victoria',sceneIdentity:copy(identity),overlay:overlay.overlay,
    apartmentRoot:{children:[]},renderer:{domElement:w.document.createElement('canvas')}};
  const emit=p=>playerOptions.onChange({connection:'live',view:controlledView(p),imageUrl:null,frame:null});
  Object.assign(w,{React,HomeApartmentPerceptionView:View,HomeApartmentDeskAimPreview:Controller,HomeApartmentAiming:Aiming,
    HomeSpatialViewport:{getSelectedSiteId:()=> 'victoria',subscribe:()=>()=>{}},HomeContentPacks:{get:()=>({manifest:{assets:{'collision.glb':{sha256:hash('c')}}}})},
    HomeApartmentPerceptionPlayer:{create(options){playerOptions=options;player={start(){counts.playerStart++;if(emitOnStart)emit(preview());},destroy(){counts.playerDestroy++;}};return player;}}});
  const layer={draw(){counts.layerDraw++;},screenAnchor:()=>null,readback:()=>({following:false,followPaused:false}),setFollowing(){},dispose(){counts.layerDispose++;}};
  vm.runInContext(Babel.transform(fs.readFileSync(path.join(SRC,'home-apartment-den-perception.jsx'),'utf8'),{presets:['react']}).code,dom.getInternalVMContext());
  const {createRoot}=require('react-dom/client'),root=createRoot(w.document.getElementById('root'));
  // The fixed-desk preview is retired in Home (AL-4); this suite keeps its opt-in path covered.
  const props={engine,source,legacyDeskAimPreview:true,loadLayer:async()=>({attachApartmentPerceptionLayer:()=>layer}),
    onAimPreviewController:(c,mounted)=>registration.push({controller:c,mounted}),onAimPreviewChange:(e,state)=>changes.push({engine:e,state:copy(state)})};
  let unmounted=false;
  const render=async active=>{await React.act(async()=>{root.render(React.createElement(w.HomeApartmentDenPerception,{...props,active}));await pause();});};
  const button=name=>[...w.document.querySelectorAll('button')].find(b=>b.textContent===name||b.getAttribute('aria-label')===name);
  const click=async name=>{const b=button(name);assert.ok(b,name);assert.equal(b.disabled,false,name+' enabled');await React.act(async()=>{b.click();await pause();});};
  const connect=async()=>{await click('View perception');await click('Connect');assert.ok(player);assert.equal(counts.sourceStart,0);};
  const unmount=async()=>{if(!unmounted){unmounted=true;await React.act(async()=>{root.unmount();await pause();});}};
  const cleanup=async()=>{try{await unmount();assert.equal(timers.size,0);assert.equal(counts.sourceStart,0);assert.equal(counts.watchOff,1);}
    finally{w.close();for(const[k,d]of Object.entries(old))if(d)Object.defineProperty(global,k,d);else delete global[k];}};
  await render(true);
  return {w,engine,props,root,source,counts,timers,overlay,registration,changes,button,click,connect,render,emit,cleanup,unmount,
    controller:()=>registration.find(r=>r.mounted)?.controller,latest:()=>registration.filter(r=>r.mounted).at(-1)?.controller,
    setNow:value=>{now=value;},playerOptions:()=>playerOptions,
    emitWith:async(p,evidence,connection='live')=>{await React.act(async()=>{
      playerOptions.onChange({connection,view:{...controlledView(p),deskPreviewEvidence:evidence},imageUrl:null,frame:null});await pause();});},
    hide:async()=>{await React.act(async()=>{w.document.hidden=true;w.document.dispatchEvent(new w.Event('visibilitychange'));await pause();});}};
}

test('actual Den JSX starts opted out, registers real controller and refreshes visible preview/Stop state without starting a source',async()=>{
  const f=await mounted();try{
    assert.equal(f.registration.length,0);await f.connect();const c=f.controller();
    assert.ok(c);assert.equal(c.readback().enabled,false);assert.equal(f.overlay.calls.aim,0);
    assert.equal(f.button('Preview desk aim').getAttribute('aria-pressed'),'false');
    await f.click('Preview desk aim');assert.equal(c.readback().active,true);assert.equal(f.overlay.calls.aim,1);
    assert.match(f.w.document.querySelector('[data-desk-aim-preview]').textContent,/Previewing configured desk/);
    assert.ok(f.changes.some(v=>v.engine===f.engine&&v.state.active===true));
    await f.click('Stop desk preview');assert.equal(c.readback().enabled,false);assert.equal(c.readback().active,false);
    assert.deepEqual(f.overlay.descriptors(),f.overlay.before);assert.equal(f.overlay.calls.restore,1);
    assert.equal(f.button('Preview desk aim').getAttribute('aria-pressed'),'false');assert.equal(f.counts.sourceStop,0);
    assert.ok(f.changes.some(v=>v.state.restoreStatus==='restored'));
  }finally{await f.cleanup();}
  assert.equal(f.registration.filter(r=>r.mounted).length,1);assert.equal(f.registration.filter(r=>!r.mounted).length,1);
});

test('source loss, Disconnect, hidden page, inactive parent and unmount restore and retire UI preview ownership',async()=>{
  for(const exit of ['source-stop','disconnect','hidden','inactive','unmount']){
    const f=await mounted();try{await f.connect();await f.click('Preview desk aim');const c=f.controller();assert.equal(c.readback().active,true,exit);
      if(exit==='source-stop')await React.act(async()=>{f.emit({available:false,reason:'desk_preview_source_stopped'});await pause();});
      if(exit==='disconnect')await f.click('Disconnect');if(exit==='hidden')await f.hide();
      if(exit==='inactive')await f.render(false);if(exit==='unmount')await f.unmount();
      assert.equal(c.readback().active,false,exit);assert.deepEqual(f.overlay.descriptors(),f.overlay.before,exit);
      assert.equal(f.overlay.calls.restore,1,exit);assert.equal(f.counts.sourceStop,0,exit);
      if(exit==='source-stop'){assert.match(f.w.document.querySelector('[data-desk-aim-preview]').textContent,/desk preview source stopped/);}
      else{assert.equal(f.registration.filter(r=>!r.mounted).length,1,exit);assert.equal(f.counts.playerDestroy,1,exit);assert.equal(f.counts.layerDispose,1,exit);}
    }finally{await f.cleanup();}
  }
});

test('Stop own analysis withdraws preview before awaiting the existing source Stop and never issues Start',async()=>{
  const f=await mounted({owned:true});try{await f.connect();await f.click('Preview desk aim');const c=f.controller();
    const original=f.source.stopAnalysis;f.source.stopAnalysis=async()=>{assert.equal(c.readback().enabled,false);assert.equal(c.readback().active,false);await original();};
    await f.click('Stop own analysis');assert.equal(f.counts.sourceStop,1);assert.equal(f.counts.sourceStart,0);
    assert.deepEqual(f.overlay.descriptors(),f.overlay.before);assert.equal(f.registration.filter(r=>!r.mounted).length,1);
  }finally{await f.cleanup();}
});

const parentRaw=fs.readFileSync(path.join(SRC,'home-apartment.jsx'),'utf8');
const parentAST=Babel.packages.parser.parse(parentRaw,{sourceType:'unambiguous',plugins:['jsx']});
function extracted(name,context){let found;
  Babel.packages.traverse.default(parentAST,{VariableDeclarator(p){if(p.node.id.name!==name)return;const n=p.node.init;
    found=n.type==='CallExpression'&&n.callee.name==='useCallback'?n.arguments[0]:n;},
    ObjectProperty(p){if(name==='captureSnapshot'&&p.node.key.name===name)found=p.node.value;}});
  assert.ok(found&&['ArrowFunctionExpression','FunctionExpression'].includes(found.type),'actual parent AST function '+name);
  return vm.runInNewContext('('+parentRaw.slice(found.start,found.end)+')',context,{filename:'home-apartment.jsx#'+name});
}
test('actual parent registration and refresh callbacks keep current controller/engine ownership',()=>{
  const a={},b={},ref={current:null},engine={overlay:{virtualFixturesReadback:()=>({current:true})}},changes=[];
  const ctx={denDeskAimPreviewRef:ref,engineRef:{current:engine},setVirtualFixturePreview:v=>changes.push(v)};
  const register=extracted('registerDenDeskAimPreview',ctx),refresh=extracted('refreshDenDeskAimPreview',ctx);
  register(a,true);register(b,true);register(a,false);assert.equal(ref.current,b);register(b,false);assert.equal(ref.current,null);
  refresh({overlay:engine.overlay});assert.equal(changes.length,0);refresh(engine);assert.deepEqual(changes,[{current:true}]);
});

test('actual parent manual controls/history/gesture/desk selection take over before modifying or capturing fixture state',async()=>{
  for(const kind of ['control','undo','gesture','desk']){
    const log=[],f=overlayFixture(log),timers=new Map();let seq=0;
    const engine={siteId:'victoria',sceneIdentity:copy(identity),overlay:f.overlay,renderer:{domElement:{}}};
    const c=Controller.create({overlay:f.overlay,solveAim:Aiming.solveAim,getContext:()=>({active:true,hidden:false,committedOwnerActive:false,identity}),
      nowMs:()=>0,setTimeout:fn=>{timers.set(++seq,fn);return seq;},clearTimeout:id=>timers.delete(id)});
    c.setEnabled(true);c.update(preview());assert.equal(c.readback().active,true);log.length=0;
    const ref={current:{readback:()=>c.readback(),manualTakeover(reason){log.push('takeover:'+reason);return c.manualTakeover(reason);}}};
    const ctx={engineRef:{current:engine},denDeskAimPreviewRef:ref,virtualFixtureDocument:{binding,revision:2,digest:hash('e')},
      selectedVirtualFixture:{fixture_id:'fixture-0'},virtualFixtureItems:[{fixture_id:'fixture-0'}],directionalViewRef:{current:null},phase:'ready',workspaceAuthorityRef:{current:{key:'authority'}},apartmentAuthorityKey:'authority',
      syncVirtualFixturePreview:()=>log.push('sync'),setVirtualAimNotice:()=>{},releaseFixtureForHandChange:(id,{reason})=>log.push('release:'+reason),setVirtualFixtureId:()=>{},setVirtualAimHistoryVersion:()=>{},
      virtualFixtureBindingKey:'fixture-binding',virtualAimHistoryRef:{current:{undo:[{binding:'fixture-binding',fixture_id:'fixture-0',before:copy(f.before[0]),after:copy(f.before[0])}],redo:[]}}};
    try{
      // Main routes every hand change through the one seam (W2); Den binds it
      // to the takeover, which must restore the fixture before anything else.
      if(kind!=='desk'){
        Object.assign(ctx,{lightingBrainRef:{current:{follower:{handChange:(id,{reason})=>log.push('brain:'+id+':'+reason)}}}});
        const reason={control:'change',undo:'undo',gesture:'aim'}[kind];
        const answer=extracted('releaseFixtureForHandChange',ctx)('fixture-0',{reason});
        assert.equal(log[0],'takeover:'+{control:'manual_fixture_control',undo:'manual_aim_history',gesture:'manual_aim_gesture'}[kind]);
        assert.ok(log.indexOf('restore')>0&&log.indexOf('restore')<log.indexOf('brain:fixture-0:'+reason));
        assert.deepEqual(JSON.parse(JSON.stringify(answer)),{released:true,restored:false,excluded:false});
        assert.deepEqual(f.descriptors(),f.before,'the takeover restores the pre-preview original');
      }
      if(kind==='desk'){
        Object.assign(ctx,{open:true,editing:false,calibCam:false,liveOn:false,virtualSpotlightsVisible:true,spatialScale:'apartment',rehearsalIsolated:false,
          deskTargetCaptureRef:{current:null},virtualAimGestureRef:{current:{cancel(){assert.equal(c.readback().enabled,false);log.push('gesture_cancel');}}},
          model:{targets:[]},setDeskTargetSelection:value=>{assert.equal(c.readback().enabled,false);log.push('select:'+(value?.status||'cleared'));},
          window:{},showDeskTargetCorners(){},deskTargetSourceCurrent:()=>true});
        ctx.deskTargetContextCurrent=extracted('deskTargetContextCurrent',ctx);ctx.cancelDeskTargetSelection=extracted('cancelDeskTargetSelection',ctx);
        f.overlay.createDeskTargetCapture=()=>{assert.equal(c.readback().enabled,false);log.push('desk_capture');return {dispose(){}};};
        await extracted('startDeskTargetSelection',ctx)();
        assert.equal(log[0],'takeover:desk_selection_started');assert.ok(log.indexOf('restore')<log.indexOf('gesture_cancel'));
        assert.ok(log.indexOf('restore')<log.indexOf('select:selecting'));assert.ok(log.indexOf('restore')<log.indexOf('desk_capture'));
        ctx.cancelDeskTargetSelection();assert.equal(ctx.deskTargetCaptureRef.current,null);
      }
      assert.equal(c.readback().enabled,false,kind);assert.equal(c.readback().active,false,kind);
    }finally{c.dispose();assert.equal(timers.size,0);}
  }
});

// Development timing and arming. Authored validated-view outputs only.
function devPreview(event=1,warnings=['clock_uncertainty']){const p=preview(event);p.timingPolicy='development';p.warnings=[...warnings];
  p.observation.observed_at=1800000000+event;return p;}
const devEvidence=(over={})=>({policy:'development',connection:'live',sessionState:'recording',sourceEpoch:'authored-epoch',
  armB:null,integrityReason:null,...over});
const aimStatus=f=>f.w.document.querySelector('[data-desk-aim-preview] [role="status"]').textContent;

test('arming before an observation',async()=>{
  const f=await mounted({emitOnStart:false});try{
    await f.connect();const c=f.latest();assert.equal(f.button('Preview desk aim').disabled,false,'armable before any observation');
    await f.click('Preview desk aim');
    assert.equal(c.readback().enabled,true);assert.equal(c.readback().active,false);assert.equal(c.readback().reason,'preview_awaiting_observation');
    assert.equal(f.overlay.calls.aim,0);assert.match(aimStatus(f),/^Armed · waiting for one visible person/);
    assert.equal(f.button('Stop desk preview').getAttribute('aria-pressed'),'true');
    assert.equal(f.w.document.querySelector('[data-desk-preview-development]'),null);
  }finally{await f.cleanup();}
});

test('aiming with no second click',async()=>{
  const f=await mounted({emitOnStart:false});try{
    await f.connect();await f.click('Preview desk aim');const c=f.latest();
    await f.emitWith(devPreview(),devEvidence());
    assert.equal(c.readback().active,true);assert.equal(f.overlay.calls.aim,1);
    assert.match(aimStatus(f),/^Previewing configured desk · last seen 0s ago · fixture-/);
    await f.emitWith({available:false,reason:'desk_preview_single_person_required'},devEvidence({armB:{key:'["r9","f9"]',observedAt:1800000009,
      captureEpoch:'authored-epoch',personCount:0,mergedDuplicates:0,countSource:'boxes'}}));
    assert.equal(c.readback().active,false);assert.equal(c.readback().enabled,true,'stays armed after contrary evidence');
    assert.deepEqual(f.overlay.descriptors(),f.overlay.before);
    await f.emitWith(devPreview(2),devEvidence());assert.equal(c.readback().active,true,'re-aims from a fresh original with no click');
  }finally{await f.cleanup();}
});

test('reconnect stays armed',async()=>{
  const f=await mounted({emitOnStart:false});try{
    await f.connect();await f.click('Preview desk aim');const first=f.latest();
    await f.emitWith({available:false,reason:'desk_preview_unavailable'},devEvidence(),'stopped');
    await f.click('Connect');const second=f.latest();
    assert.notEqual(second,first);assert.equal(first.readback().status,'disposed');
    assert.equal(second.readback().enabled,true);assert.equal(second.readback().reason,'preview_awaiting_observation');
    await f.emitWith(devPreview(2),devEvidence());assert.equal(second.readback().active,true);
  }finally{await f.cleanup();}
});

test('Disconnect and Stop own analysis disarm',async()=>{
  const f=await mounted({emitOnStart:false});try{
    await f.connect();await f.click('Preview desk aim');
    await f.emitWith({available:false,reason:'desk_preview_unavailable'},devEvidence());assert.equal(f.latest().readback().enabled,true);
    await f.click('Disconnect');
    await f.click('Connect');assert.equal(f.latest().readback().enabled,false);
    assert.equal(f.button('Preview desk aim').getAttribute('aria-pressed'),'false');
  }finally{await f.cleanup();}
  const g=await mounted({owned:true,emitOnStart:false});try{
    await g.connect();await g.click('Preview desk aim');const c=g.latest();assert.equal(c.readback().enabled,true);
    await g.click('Stop own analysis');assert.equal(c.readback().enabled,false);assert.equal(g.counts.sourceStop,1);
    await g.click('Connect');assert.equal(g.latest().readback().enabled,false);
  }finally{await g.cleanup();}
});

test('hidden page disarms; a visibility effect re-run does not',async()=>{
  const f=await mounted({emitOnStart:false});try{
    await f.connect();await f.click('Preview desk aim');const c=f.latest();
    await f.render(true);
    await React.act(async()=>{f.w.document.dispatchEvent(new f.w.Event('visibilitychange'));await pause();});
    assert.equal(f.latest(),c);assert.equal(c.readback().enabled,true,'a visible visibility event keeps the arm');
    await f.hide();assert.equal(c.readback().status,'disposed');
    await React.act(async()=>{f.w.document.hidden=false;await pause();});
    await f.click('Connect');assert.equal(f.latest().readback().enabled,false);
  }finally{await f.cleanup();}
});

test('holding label shows last seen 3s ago',async()=>{
  const f=await mounted({emitOnStart:false});try{
    await f.connect();await f.click('Preview desk aim');
    f.setNow(1000);await f.emitWith(devPreview(1,['clock_uncertainty','dispatch_late']),devEvidence());
    f.setNow(4200);await f.emitWith({available:false,reason:'desk_preview_receipt_retired'},devEvidence());
    assert.match(aimStatus(f),/^Previewing configured desk · last seen 3s ago · fixture-\d$/);
    assert.equal(f.w.document.querySelector('[data-desk-preview-development]').textContent.trim(),
      'Development timing · strict limits deferred · 2 warnings recorded');
    assert.equal(f.latest().readback().status,'holding');
    await f.click('Stop desk preview');assert.equal(f.latest().readback().policy,null);
    assert.equal(f.w.document.querySelector('[data-desk-preview-development]'),null,'the development line leaves with the arm');
  }finally{await f.cleanup();}
  const strict=await mounted();try{await strict.connect();await strict.click('Preview desk aim');
    assert.match(aimStatus(strict),/^Previewing configured desk · fixture-/);
    assert.equal(strict.w.document.querySelector('[data-desk-preview-development]'),null);
  }finally{await strict.cleanup();}
});

test('previewArmed getter reflects arming',async()=>{
  const f=await mounted({emitOnStart:false});try{
    await f.connect();let captured;f.source.subscribe=args=>{captured=args;return()=>{};};
    f.playerOptions().source.subscribe({scope:{},onUpdate(){},onStatus(){}});
    assert.equal(typeof captured.previewArmed,'function');assert.equal(captured.arm,'B');assert.equal(captured.previewArmed(),false);
    await f.click('Preview desk aim');assert.equal(captured.previewArmed(),true);
    await f.click('Stop desk preview');assert.equal(captured.previewArmed(),false);
    await f.click('Preview desk aim');assert.equal(captured.previewArmed(),true);
    await React.act(async()=>{f.latest().manualTakeover('manual_knob');await pause();});
    assert.equal(captured.previewArmed(),false,'manual takeover disarms');
  }finally{await f.cleanup();}
});

test('Parent: syncVirtualFixturePreview skips the owned fixture',()=>{
  const states={current:{key:'k',states:new Map(),targets:new Map([['fixture-1',{point:[9,9,9]}]])}},changes=[];
  const fixtures=[0,1,2].map(i=>({fixture_id:'fixture-'+i,state:{radials:[{number:1,on:true,brightness:.5,color_temp_kelvin:3000,extra:1}],
    downward:{on:false},spotlight:{brightness:.3},pan_deg:i,tilt_deg:45}}));
  const engine={overlay:{virtualFixturesReadback:()=>({fixtures}),virtualFixtureInteractionDescriptor:id=>({target:id==='fixture-2'?{point:[1,2,3]}:null})}};
  let previewState={active:true,fixtureId:'fixture-1'};
  const ctx={virtualFixtureStatesRef:states,denDeskAimPreviewRef:{current:{readback:()=>previewState}},setVirtualFixturePreview:v=>changes.push(v),
    virtualFixturePreviewDirtyRef:{current:false}};
  const sync=extracted('syncVirtualFixturePreview',ctx);
  sync(engine);
  assert.deepEqual([...states.current.states.keys()].sort(),['fixture-0','fixture-2']);
  assert.deepEqual(states.current.targets.get('fixture-1'),{point:[9,9,9]},'the owned fixture memory is untouched');
  assert.deepEqual(states.current.targets.get('fixture-2'),{point:[1,2,3]});assert.equal(changes.length,1);
  assert.deepEqual(JSON.parse(JSON.stringify(states.current.states.get('fixture-0'))),{radials:[{number:1,on:true,brightness:.5,color_temp_kelvin:3000}],
    downward:{on:false},spotlight:{brightness:.3},pan_deg:0,tilt_deg:45});
  previewState={active:false,fixtureId:null};sync(engine);
  assert.deepEqual([...states.current.states.keys()].sort(),['fixture-0','fixture-1','fixture-2']);assert.equal(states.current.targets.has('fixture-1'),false);
  ctx.denDeskAimPreviewRef.current=null;sync(engine);assert.equal(changes.length,3);
});

test('Parent: reapplyVirtualFixtureMemory skips the owned fixture',()=>{
  const log=[],overlay={setVirtualFixtureState:id=>log.push('state:'+id),aimVirtualFixtureAtTarget:id=>log.push('aim:'+id)};
  const ctx={virtualFixtureStatesRef:{current:{states:new Map([['fixture-0',{}],['fixture-1',{}]]),targets:new Map([['fixture-1',{}],['fixture-2',{}]])}}};
  const reapply=extracted('reapplyVirtualFixtureMemory',ctx);
  reapply(overlay,'fixture-1');assert.deepEqual(log,['state:fixture-0','aim:fixture-2']);
  log.length=0;reapply(overlay,null);assert.deepEqual(log,['state:fixture-0','state:fixture-1','aim:fixture-1','aim:fixture-2']);
  assert.match(parentRaw,/reapplyVirtualFixtureMemory\(overlay, preview\?\.active \? preview\.fixtureId : null\)/,
    'the scene effect passes the active preview fixture');
});
