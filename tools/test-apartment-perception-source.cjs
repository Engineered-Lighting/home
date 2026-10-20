'use strict';
// Public transport/media helpers with authored Lab HTTP bodies. No camera,
// model, server, native executable or browser is started by these cases.
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const SRC=process.env.HOME_APARTMENT_SOURCE_TEST_ROOT
  ?path.resolve(process.env.HOME_APARTMENT_SOURCE_TEST_ROOT):path.resolve(__dirname,'../app/src');
const OBSERVATION_FIXTURE=path.resolve(__dirname,'../tools/fixtures/apartment-live-repair-20261018/observation-seam-01/authored-asgi-race-fixture.json');
const clone=x=>JSON.parse(JSON.stringify(x)),settle=async()=>{for(let i=0;i<150;i++)await Promise.resolve();};
function events(){const all=new Map();return {addEventListener(k,f){if(!all.has(k))all.set(k,new Set());all.get(k).add(f);},
  removeEventListener(k,f){all.get(k)?.delete(f);},fire(k,e={}){for(const f of all.get(k)||[])f(e);}};}
function clock(){let now=0,id=0;const q=new Map();return {setTimeout(f,ms){const k=++id;q.set(k,{f,at:now+ms});return k;},clearTimeout:k=>q.delete(k),now:()=>now,
  async advance(ms){const end=now+ms;for(;;){await settle();const next=[...q].filter(([,v])=>v.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];
    if(!next)break;now=next[1].at;q.delete(next[0]);next[1].f();}now=end;await settle();},size:()=>q.size};}
function fixture({onFetch}={}){
  const scope={siteId:'victoria',contentRevision:'authored-den-r1',frameDigest:'a'.repeat(64),registrationVersion:1,
    cameraId:'den',sessionId:'authored-lab-session',generation:2};
  const session={id:scope.sessionId,site_id:'victoria',camera_id:'den',state:'recording',generation:2,revision:7};
  const frame={id:'rgb-17',media_id:'image-17',site_id:'victoria',generation:2,captured_at:100,session_time_s:10,width:640,height:360};
  const arm={id:'B',status:'running',profile:{id:'pose-b',name:'authored-pose-model',runs_on:'authored-worker'},frame_id:'rgb-17',frame,
    layers:{skeleton:{available:true}},keypoints:[{x:100,y:110,score:.8,joint_id:5,instance_id:'crop-1'},
      {x:130,y:125,score:.7,joint_id:7,instance_id:'crop-1'}],skeleton_edges:[[0,1]],validation:[],
    reasoning_status:{state:'completed',available_at:100.6,current:true},reasoning_fresh:true,
    reasoning_source_end_s:9,reasoning_available_at_s:10.6,
    judgment:{activity:'phone_use',provider:'authored-jev',model_version:'authored-jev-v1',confidence:.6}};
  const live={site_id:'victoria',session_id:scope.sessionId,session,revision:7,state:'ready',server_time:100.8,experiment_generation:3,
    arms:{A:{id:'A',status:'unavailable'},B:arm},room:null};
  const specialist={stage:'specialist_output',status:'fresh',event_id:20,request_id:'pose-request-17',generation:3,frame_id:'rgb-17',
    observed_at:100,available_at:100.4,valid_until:102,model_version:'authored-pose-model',artifact_sha256:'b'.repeat(64),engine_epoch:'pose-epoch',frame};
  const graph={contract:'perception-lab-graph-1',contract_revision:3,site_id:'victoria',session_id:scope.sessionId,inference:'none',session:clone(session),
    sources:[],experiment:{generation:3},arms:{a:{chain:[{stage:'specialist_output',status:'none'}]},
      b:{generation:3,chain:[specialist,{stage:'result',status:'completed',activity:'phone_use',request_id:'jev-request',output_event_id:18,
        source_end_s:9,available_at_s:10.6,valid_until:101.8,freshness:'fresh',model_version:'authored-jev-v1'}]}}};
  const bytes=new Uint8Array([255,216,1,2,255,217]),timer=clock(),calls=[],updates=[],statuses=[];
  const descriptor={id:'image-17',media_id:'image-17',site_id:'victoria',session_id:scope.sessionId,
    generation:2,size_bytes:6,content_type:'image/jpeg',metadata:{frame_id:'rgb-17',capture_epoch:'original-camera-epoch',camera_id:'den',site_id:'victoria'}};
  let selected='victoria';const subscribers=new Set();
  const w={...events(),document:{...events(),hidden:false},setTimeout:timer.setTimeout,clearTimeout:timer.clearTimeout,
    Date:{now:()=>100800},AbortController,DOMException,Headers,Response,TextEncoder,TextDecoder,Uint8Array,URL,
    HomeSpatialViewport:{getSelectedSiteId:()=>selected,subscribe(f){subscribers.add(f);return()=>subscribers.delete(f);}}};
  w.window=w;const context=vm.createContext(w);
  const load=name=>vm.runInContext(fs.readFileSync(path.join(SRC,name),'utf8'),context,{filename:name});
  w.fetch=async(input,options)=>{const url=new URL(String(input),'https://home.invalid');calls.push({url,options});
    assert.equal(options.credentials,'same-origin');assert.equal(new Headers(options.headers).get('X-Home-Lab-Site'),'victoria');
    const base='/api/perception-lab/v1/sessions/'+scope.sessionId;
    const custom=await onFetch?.({url,options,scope,session,live,graph,descriptor,bytes,timer,base});
    if(custom!==undefined)return custom;
    if(url.pathname==='/api/perception-lab/v1/sessions')return Response.json({site_id:'victoria',version:1,items:[clone(session)]});
    if(url.pathname===base+'/live')return Response.json(clone(live));
    if(url.pathname===base+'/graph')return Response.json(clone(graph));
    if(url.pathname===base+'/events')return Response.json({items:[],after_id:Number(url.searchParams.get('after_id'))});
    if(url.pathname===base+'/media/image-17/descriptor')return Response.json(clone(descriptor));
    if(url.pathname===base+'/media/image-17'){assert.equal(new Headers(options.headers).get('Range'),'bytes=0-5');
      return new Response(bytes,{status:206,headers:{'Content-Type':'image/jpeg','Content-Range':'bytes 0-5/6'}});}
    throw Error('unexpected public read '+url.pathname);
  };
  load('home-perception-lab-transport.js');load('home-perception-lab-media.js');load('home-den-lab-stream.js');
  // Real graph projection definitions, matching the incumbent stream's seam.
  const m={exports:{}};new Function('module','exports',fs.readFileSync(path.join(SRC,'vendor/babel-7.29.0/babel.min.js'),'utf8'))(m,m.exports);
  const babel=m.exports.Babel||m.exports;w.React=require('react');
  vm.runInContext(babel.transform(fs.readFileSync(path.join(SRC,'home-perception-lab-graph.jsx'),'utf8'),{presets:['react']}).code,context);
  load('home-apartment-perception-source.js');
  return {w,scope,live,graph,descriptor,bytes,calls,updates,statuses,timer,subscribers,load,
    select(site){selected=site;for(const f of subscribers)f();}};
}

test('ordinary exact-session facade supplies original-frame pose and JPEG with independent JEV time and no room pose',async()=>{
  const f=fixture(),source=f.w.HomeApartmentPerceptionSource,abort=new AbortController();
  const unsubscribe=await source.subscribe({scope:f.scope,signal:abort.signal,onUpdate:x=>f.updates.push(x),onStatus:x=>f.statuses.push(x)});
  try{
    await settle();const packet=f.updates.at(-1);
    assert.deepEqual(f.calls.map(x=>x.url.pathname.split('/').pop()),['live','graph'],
      'ordinary viewing needs only incumbent live/graph operations, never an event cursor');
    assert.equal(packet.schema,'home-apartment-perception-1');assert.deepEqual(clone(packet.scope),f.scope);
    assert.equal(packet.frame.frame_id,'rgb-17');assert.equal(packet.pose2d.frame_id,'rgb-17');
    assert.deepEqual(clone(packet.pose2d.keypoints),[{id:0,x:100,y:110,raw_joint_id:5,raw_instance_id:'crop-1'},
      {id:1,x:130,y:125,raw_joint_id:7,raw_instance_id:'crop-1'}]);
    assert.deepEqual(clone(packet.pose2d.skeleton_edges),[[0,1]]);
    assert.equal(packet.pose2d.observed_at,100);assert.equal(packet.pose2d.available_at,100.4);assert.equal(packet.pose2d.valid_until,102);
    assert.equal(packet.room_pose,null);assert.equal(packet.source_epoch,null);
    assert(packet.models.some(x=>x.family==='jev'&&x.model_id==='authored-jev-v1'&&x.result.activity==='phone_use'&&x.result.source_end_s===9));
    const image=await source.readFrame(packet.frame,{scope:f.scope,signal:abort.signal});
    assert.deepEqual([...image.bytes],[255,216,1,2,255,217]);assert.equal(image.type,'image/jpeg');
    assert.equal(source.sampleClock().uncertainty_s,null);assert.equal(source.sampleClock().domain,'unix');
    assert(f.statuses.some(x=>x.status==='live'));assert(f.calls.every(x=>(x.options.method||'GET')==='GET'));
  }finally{abort.abort();unsubscribe();await settle();}
  assert.equal(f.timer.size(),0);assert.equal(f.subscribers.size,0);
});

test('actual source/media/player recover from delayed descriptor-to-range 410 and decode a later live original',async()=>{
  let polls=0,firstRanges=0;const decoded=[];
  const f=fixture({onFetch:async({url,options,live,graph,descriptor,bytes,timer,base})=>{
    if(url.pathname===base+'/live'&&++polls>=2){
      live.arms.B.frame_id='rgb-18';live.arms.B.frame.id='rgb-18';live.arms.B.frame.media_id='image-18';
      graph.arms.b.chain[0].frame_id='rgb-18';graph.arms.b.chain[0].frame.id='rgb-18';graph.arms.b.chain[0].frame.media_id='image-18';
    }
    if(url.pathname===base+'/media/image-17'){
      firstRanges++;await new Promise(resolve=>timer.setTimeout(resolve,57));
      return Response.json({error:'viewing_media_expired'},{status:410});
    }
    if(url.pathname===base+'/media/image-18/descriptor')return Response.json({...clone(descriptor),id:'image-18',media_id:'image-18',
      metadata:{...clone(descriptor.metadata),frame_id:'rgb-18'}});
    if(url.pathname===base+'/media/image-18'){
      assert.equal(new Headers(options.headers).get('Range'),'bytes=0-5');
      await new Promise(resolve=>timer.setTimeout(resolve,57));
      return new Response(bytes,{status:206,headers:{'Content-Type':'image/jpeg','Content-Range':'bytes 0-5/6'}});
    }
  }});
  f.w.setInterval=(fn,ms)=>{const slot={live:true,id:null};const tick=()=>{if(!slot.live)return;fn();slot.id=f.timer.setTimeout(tick,ms);};
    slot.id=f.timer.setTimeout(tick,ms);return slot;};
  f.w.clearInterval=slot=>{slot.live=false;f.timer.clearTimeout(slot.id);};
  f.w.performance={now:f.timer.now};
  f.load('home-apartment-perception-view.js');f.load('home-apartment-perception-player.js');
  const player=f.w.HomeApartmentPerceptionPlayer.create({source:f.w.HomeApartmentPerceptionSource,scope:f.scope,env:f.w,
    decode:async(value,{frame,signal})=>{assert.equal(signal.aborted,false);decoded.push(frame.frame_id);
      return {url:'blob:'+frame.frame_id,width:640,height:360,dispose(){}};}});
  try{
    player.start();await settle();await f.timer.advance(1200);
    assert(firstRanges>=1);assert(polls>=2,'a failed image must not freeze /live polling');
    assert.equal(player.snapshot().imageUrl,'blob:rgb-18',JSON.stringify(player.snapshot()));
    assert.equal(player.snapshot().view.pose2d.renderable,true);
    assert(decoded.every(id=>id==='rgb-18'),'an expired frame must never reach the decoder');
    assert(f.calls.every(x=>(x.options.method||'GET')==='GET'));
  }finally{player.destroy();}
  assert.equal(f.timer.size(),0);assert.equal(f.subscribers.size,0);
});

test('image lease stays unknown until its exact descriptor supplies the original RGB lease',async()=>{
  const f=fixture(),source=f.w.HomeApartmentPerceptionSource;f.descriptor.metadata.valid_until=101;
  const unsubscribe=source.subscribe({scope:f.scope,onUpdate:x=>f.updates.push(x),onStatus:x=>f.statuses.push(x)});
  try{
    await settle();const before=f.updates.at(-1);
    assert.equal(before.pose2d.valid_until,102,'model support keeps its own original lease');
    assert.equal(before.frame.valid_until,null,'the missing original RGB lease cannot inherit the model lease');
    await source.readFrame(before.frame,{scope:f.scope});
    const retained=f.updates.at(-1);
    assert.equal(retained.frame.valid_until,101);assert.equal(retained.source_epoch,'original-camera-epoch');
    assert.equal(retained.pose2d.valid_until,102);
  }finally{unsubscribe();}
});

test('explicit arm selection preserves seven viewing pins on the frame and refuses a foreign scope',async()=>{
  const f=fixture(),source=f.w.HomeApartmentPerceptionSource;
  f.live.arms.A={...clone(f.live.arms.B),id:'A',profile:{id:'pose-a',name:'authored-pose-model',runs_on:'authored-worker'}};
  f.graph.arms.a=clone(f.graph.arms.b);
  const unsubscribe=source.subscribe({scope:f.scope,arm:'A',onUpdate:x=>f.updates.push(x),onStatus:x=>f.statuses.push(x)});
  try{
    await settle();const packet=f.updates.at(-1);
    assert.equal(packet.pose2d.source_id,'specialist_a');assert.equal(packet.pose2d.model_id,'pose-a');
    assert.deepEqual(clone(packet.frame.scope),f.scope);
    const count=f.calls.length;
    await assert.rejects(source.readFrame(packet.frame,{scope:{...f.scope,contentRevision:'another-pack'}}),/current_scope/);
    await assert.rejects(source.readFrame({...packet.frame,scope:{...f.scope,sessionId:'another-session'}},{scope:f.scope}),/current_scope/);
    assert.equal(f.calls.length,count,'foreign viewing pins must not start a media read');
  }finally{unsubscribe();}
});

test('selected B empty producer result stays empty while A model rows remain separately labeled',async()=>{
  const f=fixture(),source=f.w.HomeApartmentPerceptionSource;
  f.live.arms.A={...clone(f.live.arms.B),id:'A',frame_id:'rgb-a-99',profile:{id:'pose-a',name:'authored-a',runs_on:'worker-a'},
    frame:{...clone(f.live.arms.B.frame),id:'rgb-a-99',media_id:'image-a-99'}};
  f.graph.arms.a=clone(f.graph.arms.b);f.graph.arms.a.chain[0].frame_id='rgb-a-99';
  f.graph.arms.a.chain[0].frame={...clone(f.live.arms.A.frame)};f.graph.arms.a.chain[0].artifact_sha256='a'.repeat(64);
  f.live.arms.B.keypoints=[];f.live.arms.B.skeleton_edges=[];
  const unsubscribe=source.subscribe({scope:f.scope,arm:'B',onUpdate:x=>f.updates.push(x),onStatus:x=>f.statuses.push(x)});
  try{
    await settle();const packet=f.updates.at(-1);
    assert.equal(packet.frame.frame_id,'rgb-17');assert.equal(packet.frame.media_id,'image-17');
    assert.equal(packet.pose2d.source_id,'specialist_b');assert.equal(packet.pose2d.model_id,'pose-b');
    assert.deepEqual(clone(packet.pose2d.keypoints),[]);assert.deepEqual(clone(packet.pose2d.skeleton_edges),[]);
    assert(packet.models.some(row=>row.source_id==='specialist_a'&&row.result.artifact_sha256==='a'.repeat(64)));
    assert(packet.models.some(row=>row.source_id==='specialist_b'&&row.result.artifact_sha256==='b'.repeat(64)));
    await source.readFrame(packet.frame,{scope:f.scope});
    assert(f.calls.filter(x=>x.url.pathname.includes('/media/')).every(x=>x.url.pathname.includes('/image-17')));
  }finally{unsubscribe();}
});

test('a disjoint specialist frame leaves pose clocks unknown without inventing room geometry or joint identity',async()=>{
  const f=fixture(),source=f.w.HomeApartmentPerceptionSource;
  f.graph.arms.b.chain[0].frame_id='older-original';
  f.live.arms.B.keypoints[1].joint_id=5;f.live.arms.B.keypoints[1].instance_id='crop-2';
  f.live.room={people:[{pos:[1,2,3],identity:null}],joints:[{position_m:[1,2,3]}]};
  f.w.Date.now=()=>NaN;
  const unsubscribe=source.subscribe({scope:f.scope,onUpdate:x=>f.updates.push(x),onStatus:x=>f.statuses.push(x)});
  try{await settle();const p=f.updates.at(-1);
    assert.equal(p.frame.captured_at,100);assert.equal(p.frame.valid_until,null);
    assert.equal(p.pose2d.observed_at,null);assert.equal(p.pose2d.available_at,null);assert.equal(p.pose2d.valid_until,null);
    assert.deepEqual(clone(p.pose2d.skeleton_edges),[[0,1]]);
    assert.deepEqual(clone(p.pose2d.keypoints.map(x=>x.id)),[0,1]);assert.equal(p.pose2d.keypoints[1].raw_instance_id,'crop-2');
    assert.equal(p.room_pose,null);assert.equal(source.sampleClock(),null);
    assert(p.models.some(x=>x.source_id==='specialist_b'&&x.result.frame_association==='different_or_unknown'));
  }finally{unsubscribe();}
});

function selectedObservation(f){
  return {schema:'home-lab-arm-observation-1',arm:'b',session_id:f.scope.sessionId,site_id:'victoria',camera_id:'den',
    request_id:'b'.repeat(64),generation:3,frame_id:'rgb-17',media_ids:['image-17'],capture_epoch:'original-camera-epoch',
    run_id:'pose-b',model_version:'authored-pose-model',artifact_sha256:'b'.repeat(64),engine_epoch:'pose-epoch',
    observed_at:100,available_at:100.4,valid_until:102};
}

test('selected original carries every person box and unavailable-pose record independently of 3D and a later graph',async()=>{
  const f=fixture(),source=f.w.HomeApartmentPerceptionSource;
  f.live.arms.B.observation=selectedObservation(f);
  f.live.arms.B.persons2d={schema:'home-lab-frame-persons-1',version:1,frame_id:'rgb-17',coordinate_space:'source_pixels',
    persons:['desk','sofa'].map((instance_id,index)=>({instance_id,box:{x:30+index*300,y:10,w:150,h:300},
      pose_status:'unavailable',pose_reason:'pose_not_supplied',layout:null,score_semantics:null,joints:[]}))};
  f.live.arms.B.layers.skeleton.available=false;delete f.live.arms.B.keypoints;delete f.live.arms.B.skeleton_edges;
  f.graph.arms.b.chain[0].frame_id='later-unrelated-image';
  const close=source.subscribe({scope:f.scope,onUpdate:x=>f.updates.push(x),onStatus:x=>f.statuses.push(x)});
  try{await settle();const packet=f.updates.at(-1);
    assert.equal(packet.pose2d?.persons2d?.persons.length,2);
    assert.equal(packet.pose2d.media_id,'image-17');
    assert.equal(packet.pose2d.capture_epoch,'original-camera-epoch');
    assert.equal(packet.pose2d.request_id,selectedObservation(f).request_id);
    assert.equal(packet.pose2d.observed_at,100);assert.deepEqual(clone(packet.pose2d.keypoints),[]);
    assert.equal(packet.room_pose,null);
  }finally{close();}
});

test('a newer graph cannot erase the chosen live arm exact observation clocks after media loading',async()=>{
  const f=fixture(),source=f.w.HomeApartmentPerceptionSource;
  f.live.arms.B.observation=selectedObservation(f);
  const original=f.w.fetch;
  f.w.fetch=async(input,options)=>{
    if(new URL(String(input),'https://home.invalid').pathname.endsWith('/graph')){
      f.graph.arms.b.chain[0]={...clone(f.graph.arms.b.chain[0]),event_id:21,frame_id:'rgb-18',observed_at:101,
        available_at:101.4,valid_until:103,frame:{...clone(f.graph.arms.b.chain[0].frame),id:'rgb-18',media_id:'image-18'}};
    }
    return original(input,options);
  };
  const unsubscribe=source.subscribe({scope:f.scope,arm:'B',onUpdate:x=>f.updates.push(x),onStatus:x=>f.statuses.push(x)});
  try{
    await settle();const packet=f.updates.at(-1);
    assert.equal(packet.frame.frame_id,'rgb-17');assert.equal(packet.pose2d.source_id,'specialist_b');
    assert.equal(packet.pose2d.observed_at,100);assert.equal(packet.pose2d.available_at,100.4);assert.equal(packet.pose2d.valid_until,102);
    const model=packet.models.find(row=>row.source_id==='specialist_b');
    assert.equal(model.frame_id,'rgb-18');assert.equal(model.observed_at,101,'newer graph row keeps its independent clock');
    assert.equal(model.result.frame_association,'different_or_unknown');
  }finally{unsubscribe();}
});

test('actual frozen backend Store/ASGI observation joins the selected original as its real graph advances',async()=>{
  const raw=fs.readFileSync(OBSERVATION_FIXTURE);
  assert.equal(require('node:crypto').createHash('sha256').update(raw).digest('hex'),'7e007d841e9970aeb75f48048858536e3c556bfcb0a2d00b3a26d936f6feb7b5');
  const evidence=JSON.parse(raw),live=evidence.chosen_live,graph=evidence.later_graph;
  const chosen=live.arms.B,original=chosen.observation;
  const f=fixture({onFetch:async({url})=>{
    const base='/api/perception-lab/v1/sessions/'+live.session_id;
    if(url.pathname===base+'/live')return Response.json(live);
    if(url.pathname===base+'/graph')return Response.json(graph);
    throw Error('unexpected actual-fixture route '+url.pathname);
  }});
  const selected={...f.scope,sessionId:live.session_id,generation:live.session.generation};
  const unsubscribe=f.w.HomeApartmentPerceptionSource.subscribe({scope:selected,arm:'B',onUpdate:x=>f.updates.push(x),onStatus:x=>f.statuses.push(x)});
  try{
    await settle();const packet=f.updates.at(-1),newer=graph.arms.b.chain.find(row=>row.stage==='specialist_output');
    assert.notEqual(newer.frame_id,chosen.frame_id,'actual producer must have completed a different next image');
    assert.equal(packet.frame.frame_id,chosen.frame_id);assert.equal(packet.frame.media_id,chosen.frame.media_id);
    for(const key of ['observed_at','available_at','valid_until'])assert.equal(packet.pose2d[key],original[key]);
    assert.equal(packet.pose2d.keypoints.length,chosen.keypoints.length);
    assert.equal(packet.models.find(row=>row.family==='pose'&&row.source_id==='specialist_b').frame_id,newer.frame_id);
    assert.equal(packet.source_epoch,original.capture_epoch);assert.equal(packet.room_pose,null);
  }finally{unsubscribe();}
});

test('actual ASGI source summaries use the completed specialist stage and distinguish bounded RAM from durable media',async()=>{
  const evidence=JSON.parse(fs.readFileSync(OBSERVATION_FIXTURE)),live=evidence.chosen_live,graph=evidence.later_graph;
  const f=fixture({onFetch:async({url})=>{
    const base='/api/perception-lab/v1/sessions/'+live.session_id;
    if(url.pathname===base+'/live')return Response.json(live);
    if(url.pathname===base+'/graph')return Response.json(graph);
    throw Error('unexpected actual-fixture route '+url.pathname);
  }});
  const selected={...f.scope,sessionId:live.session_id,generation:live.session.generation};
  const unsubscribe=f.w.HomeApartmentPerceptionSource.subscribe({scope:selected,arm:'B',onUpdate:x=>f.updates.push(x),onStatus:x=>f.statuses.push(x)});
  try{
    await settle();const packet=f.updates.at(-1);
    for(const arm of ['a','b']){
      const stage=graph.arms[arm].chain.find(row=>row.stage==='specialist_output');
      const source=packet.models.find(row=>row.family==='specialist_'+arm);
      assert.equal(source.status,'producing');assert.equal(source.result.counts,null,'source ledger counts remain original');
      assert.notEqual(source.result.summary.value,'No output recorded');
      assert(source.result.summary.detail.includes(stage.frame_id),'summary names its actual graph stage frame');
      assert(!JSON.stringify(source).includes('coordinate_space'),'raw model originals remain private');
    }
    const camera=packet.models.find(row=>row.family==='rgb_camera');
    assert.equal(camera.result.summary.value,'Analyzed frame available in RAM');
    assert.equal(camera.result.summary.detail,'No durable image or video recording');
    assert.deepEqual(clone(camera.result.counts),graph.sources.find(row=>row.id==='rgb_camera').counts);
    assert(f.calls.every(call=>(call.options.method||'GET')==='GET'));
  }finally{unsubscribe();}
});

test('RAM camera summary requires an active viewing-only source and a selected original frame',async()=>{
  for(const change of [live=>delete live.arms.B.frame,live=>live.apartment_viewing.state='expired',
    live=>live.apartment_viewing.physical_permission=true,live=>live.apartment_viewing.encoded_recording=true]){
    const evidence=JSON.parse(fs.readFileSync(OBSERVATION_FIXTURE)),live=evidence.chosen_live,graph=evidence.later_graph;
    change(live);
    const f=fixture({onFetch:async({url})=>{
      if(url.pathname.endsWith('/live'))return Response.json(live);
      if(url.pathname.endsWith('/graph'))return Response.json(graph);
      throw Error('unexpected summary route '+url.pathname);
    }});
    const selected={...f.scope,sessionId:live.session_id,generation:live.session.generation};
    const unsubscribe=f.w.HomeApartmentPerceptionSource.subscribe({scope:selected,arm:'B',onUpdate:x=>f.updates.push(x),onStatus:x=>f.statuses.push(x)});
    try{await settle();assert.equal(f.updates.at(-1).models.find(row=>row.family==='rgb_camera').result.summary.value,'No images recorded');}
    finally{unsubscribe();}
  }
});

test('bounded metric status survives a missing original arm frame without creating pixels or pose',async()=>{
  const f=fixture();delete f.live.arms.B.frame;
  f.live.apartment_viewing={state:'active',media_storage:'bounded_ram',viewing_only:true,physical_permission:false,encoded_recording:false,
    metric:{enabled:true,arm:'b',state:'unavailable',reason:'bundled_rgb_not_inside_original_anchors',prepared_frames:0,projected_frames:0,
      in_flight:false,reserved_bytes:0,metric_depth:'diagnostic_nominal_eeprom',room_alignment:'approximate_viewing_only',
      operational_geometry:false,physical_permission:false}};
  const unsubscribe=f.w.HomeApartmentPerceptionSource.subscribe({scope:f.scope,arm:'B',onUpdate:x=>f.updates.push(x),onStatus:x=>f.statuses.push(x)});
  try{
    await settle();const packet=f.updates.at(-1),row=packet.models.find(value=>value.source_id==='apartment_viewing_metric');
    assert.equal(row.status,'unavailable');assert.equal(row.reason,'bundled_rgb_not_inside_original_anchors');
    assert.equal(row.observed_at,null);assert.equal(row.available_at,null);assert.equal(row.valid_until,null);
    assert.equal(packet.frame,null);assert.equal(packet.pose2d,null);assert.equal(packet.room_pose,null);assert.equal(packet.approximate_room_pose,null);
    assert(f.calls.every(call=>!call.url.pathname.includes('/media/')));
  }finally{unsubscribe();}
});

test('foreign arm/frame/epoch/generation or malformed live observation cannot supply pose clocks',async()=>{
  for(const change of [o=>o.arm='a',o=>o.frame_id='other-frame',o=>o.media_ids=['other-media'],o=>o.generation=4,
    o=>o.session_id='foreign-session',o=>o.run_id='pose-a',o=>o.capture_epoch='',o=>o.available_at=99]){
    const f=fixture(),source=f.w.HomeApartmentPerceptionSource;
    f.live.arms.B.observation=selectedObservation(f);change(f.live.arms.B.observation);
    f.graph.arms.b.chain[0].frame_id='newer-graph';
    const unsubscribe=source.subscribe({scope:f.scope,arm:'B',onUpdate:x=>f.updates.push(x),onStatus:x=>f.statuses.push(x)});
    try{await settle();assert.equal(f.updates.at(-1).pose2d.observed_at,null);}
    finally{unsubscribe();}
  }
});

test('chosen live observation epoch must also match its exact registered image descriptor',async()=>{
  const f=fixture(),source=f.w.HomeApartmentPerceptionSource;
  f.live.arms.B.observation=selectedObservation(f);f.descriptor.metadata.capture_epoch='wrong-original-epoch';
  const unsubscribe=source.subscribe({scope:f.scope,arm:'B',onUpdate:x=>f.updates.push(x),onStatus:x=>f.statuses.push(x)});
  try{
    await settle();const packet=f.updates.at(-1);
    await assert.rejects(source.readFrame(packet.frame,{scope:f.scope}),/frame_descriptor_mismatch/);
    assert.equal(f.calls.filter(x=>x.url.pathname.endsWith('/media/image-17')).length,0,'mismatched epoch must not fetch image bytes');
  }finally{unsubscribe();}
});

test('all published graph families keep source status and model version without exposing raw originals',async()=>{
  const f=fixture(),source=f.w.HomeApartmentPerceptionSource;
  f.graph.sources=[{id:'qwen_scene',title:'Qwen scene',group:'visual',status:'producing',last_observed_at:99.5,last_available_at:100.5,
    valid_until:102,version:{model_version:'qwen-recorded-v1',engine_epoch:'qwen-engine'},counts:{facts:1},
    native:{snippet:{json:{raw_private:'PRIVATE_ORIGINAL_MARKER'}}}},
    {id:'vjepa_scene',title:'V-JEPA',group:'spatial_temporal',status:'not_provisioned',version:null,last_observed_at:null,last_available_at:null,valid_until:null},
    {id:'depth_bundle',title:'Depth',group:'spatial_temporal',status:'no_records',version:null,last_observed_at:null,last_available_at:null,valid_until:null}];
  const unsubscribe=source.subscribe({scope:f.scope,onUpdate:x=>f.updates.push(x),onStatus:x=>f.statuses.push(x)});
  try{await settle();const p=f.updates.at(-1),q=p.models.find(x=>x.source_id==='qwen_scene');
    assert(q);assert.equal(q.model_id,'qwen-recorded-v1');assert.equal(q.observed_at,99.5);assert.equal(q.available_at,100.5);
    assert.equal(p.models.find(x=>x.source_id==='vjepa_scene').status,'not_provisioned');
    assert.equal(p.models.find(x=>x.source_id==='depth_bundle').status,'no_records');
    assert(!JSON.stringify(p).includes('PRIVATE_ORIGINAL_MARKER'));
  }finally{unsubscribe();}
});

test('a timed-out noncooperative read never overlaps or publishes after logout',async()=>{
  const f=fixture(),source=f.w.HomeApartmentPerceptionSource,original=f.w.fetch;let finish,seenSignal;
  f.w.fetch=(input,options)=>{
    if(String(input).endsWith('/live')){seenSignal=options.signal;f.calls.push({url:new URL(String(input),'https://home.invalid'),options});
      return new Promise(resolve=>{finish=()=>resolve(Response.json(clone(f.live)));});}
    return original(input,options);
  };
  const unsubscribe=source.subscribe({scope:f.scope,onUpdate:x=>f.updates.push(x),onStatus:x=>f.statuses.push(x)});
  try{await settle();await f.timer.advance(4000);assert.equal(seenSignal.aborted,true);
    assert.equal(f.statuses.at(-1).status,'unavailable');await f.timer.advance(6000);assert.equal(f.calls.length,1);
    f.w.fire('home:logout');finish();await settle();await f.timer.advance(5000);
    assert.equal(f.calls.length,1);assert.equal(f.updates.at(-1).frame,null);assert.equal(f.timer.size(),0);
  }finally{unsubscribe();}
});

test('foreign media and a changed original generation refuse while stopped recordings remain metadata-only',async()=>{
  const f=fixture(),source=f.w.HomeApartmentPerceptionSource;
  const unsubscribe=source.subscribe({scope:f.scope,onUpdate:x=>f.updates.push(x),onStatus:x=>f.statuses.push(x)});
  try{await settle();const current=f.updates.at(-1);
    f.descriptor.metadata.frame_id='foreign-original';const before=f.calls.length;
    await assert.rejects(source.readFrame(current.frame,{scope:f.scope}),/descriptor_mismatch/);
    assert.equal(f.calls.length,before+1,'a foreign descriptor cannot reach the binary GET');
    f.live.session.generation=3;await f.timer.advance(1000);
    assert.equal(f.statuses.at(-1).reason,'den_lab_session_generation_changed');assert.equal(f.updates.at(-1).frame,null);
    const ended=f.calls.length;await f.timer.advance(4000);assert.equal(f.calls.length,ended);
  }finally{unsubscribe();}
  const stopped=fixture();stopped.live.session.state='stopped';stopped.graph.session.state='stopped';stopped.live.arms={};
  const close=stopped.w.HomeApartmentPerceptionSource.subscribe({scope:stopped.scope,onUpdate:x=>stopped.updates.push(x),onStatus:x=>stopped.statuses.push(x)});
  try{await settle();assert.equal(stopped.statuses.at(-1).status,'stopped');assert.equal(stopped.updates.at(-1).pose2d,null);
    assert.equal(stopped.updates.at(-1).frame,null);assert.equal(stopped.updates.at(-1).session_state,'stopped');
  }finally{close();}
});

test('session discovery makes one existing authenticated GET and preserves original rows',async()=>{
  const f=fixture(),source=f.w.HomeApartmentPerceptionSource;
  const rows=await source.listSessions({siteId:'victoria'});
  assert.deepEqual(clone(rows),[f.live.session]);assert.equal(f.calls.length,1);
  assert.equal(f.calls[0].url.pathname,'/api/perception-lab/v1/sessions');
  await assert.rejects(source.listSessions({siteId:'echo'}),/session_read_unavailable/);assert.equal(f.calls.length,1);
});

test('public arm replacement cannot attach an in-flight old JPEG or its clocks to the new arm',async()=>{
  const f=fixture(),original=f.w.fetch;let finish;
  f.live.arms.A={...clone(f.live.arms.B),id:'A',frame_id:'rgb-18',frame:{...clone(f.live.arms.B.frame),id:'rgb-18',media_id:'image-18'}};
  f.graph.arms.a=clone(f.graph.arms.b);f.graph.arms.a.chain[0].frame_id='rgb-18';f.graph.arms.a.chain[0].frame=clone(f.live.arms.A.frame);
  f.descriptor.metadata.valid_until=101;
  f.w.fetch=(input,options)=>new URL(String(input),'https://home.invalid').pathname.endsWith('/media/image-17')?new Promise(resolve=>{finish=()=>resolve(new Response(f.bytes,
    {status:206,headers:{'Content-Type':'image/jpeg','Content-Range':'bytes 0-5/6'}}));}):original(input,options);
  const observer=f.w.HomeDenLabStream.createObserver({siteId:'victoria',sessionId:f.scope.sessionId,generation:2,scope:f.scope,
    transport:f.w.HomePerceptionLabTransport,env:f.w,onState:x=>f.updates.push(x)});
  observer.start();
  try{await settle();const pending=observer.readFrame(observer.snapshot().perception.frame);await settle();assert.equal(typeof finish,'function');
    observer.setArm('A');finish();await assert.rejects(pending,/abandoned|current_frame/);
    const current=observer.snapshot().perception;assert.equal(current.frame.frame_id,'rgb-18');assert.equal(current.frame.valid_until,null);
    assert.equal(current.source_epoch,null);
  }finally{observer.destroy();}
});

// Exercise the actual ordinary source and player together. Only external HTTP
// bodies, browser image decoding and the wall clock are authored boundaries.
function viewingFixture(){
  const f=fixture(),context=vm.createContext(f.w),intervals=new Map();let nextInterval=0;
  f.w.setInterval=(fn,ms)=>{const id=++nextInterval,entry={timer:null};intervals.set(id,entry);
    const run=()=>{if(!intervals.has(id))return;fn();entry.timer=f.timer.setTimeout(run,ms);};
    entry.timer=f.timer.setTimeout(run,ms);return id;};
  f.w.clearInterval=id=>{const entry=intervals.get(id);if(entry)f.timer.clearTimeout(entry.timer);intervals.delete(id);};
  for(const name of ['home-apartment-perception-view.js','home-apartment-perception-player.js'])
    vm.runInContext(fs.readFileSync(path.join(SRC,name),'utf8'),context,{filename:name});
  const snapshots=[],decodes=[],released=[];
  const player=f.w.HomeApartmentPerceptionPlayer.create({scope:f.scope,source:f.w.HomeApartmentPerceptionSource,env:f.w,
    onChange:value=>snapshots.push(value),decode:async(value,args)=>{
      decodes.push({value,args});return {url:'blob:authored-'+args.frame.frame_id,width:640,height:360,
        dispose(){released.push(args.frame.frame_id);}};}});
  const liveArms=clone(f.live.arms);
  const setState=state=>{f.live.session.state=state;f.graph.session.state=state;
    f.live.arms=state==='recording'?clone(liveArms):{};};
  f.descriptor.metadata.valid_until=101;
  return {...f,player,snapshots,decodes,released,setState};
}

test('ordinary viewer can connect before recording and decode exact-frame pose without an initial RGB lease',async()=>{
  const f=viewingFixture();f.setState('requested');f.player.start();
  try{
    await settle();assert.equal(f.player.snapshot().connection,'connecting');
    assert.equal(f.player.snapshot().imageUrl,null);assert.equal(f.calls.length,2);
    f.setState('starting');await f.timer.advance(999);assert.equal(f.calls.length,2);
    await f.timer.advance(1);assert.equal(f.calls.length,4);
    assert.equal(f.player.snapshot().connection,'connecting');assert.equal(f.decodes.length,0);
    f.setState('recording');await f.timer.advance(999);assert.equal(f.calls.length,4);
    await f.timer.advance(1);
    const shown=f.player.snapshot();
    assert.equal(shown.connection,'live');assert.equal(shown.imageUrl,'blob:authored-rgb-17');
    assert.equal(shown.view.pose2d.renderable,true);assert.equal(shown.view.pose2d.keypoints.length,2);
    assert.equal(shown.frame.valid_until,101);assert.equal(shown.view.roomPose.admitted,false);
    assert.equal(shown.view.follow.allowed,false);assert.equal(f.decodes.length,1);
    assert.deepEqual(f.calls.map(x=>x.url.pathname.split('/').pop()),
      ['live','graph','live','graph','live','descriptor','image-17','graph']);
    assert(f.snapshots.some(x=>x.view?.frame?.descriptor?.valid_until===null),
      'an original RGB lease is not borrowed from the specialist while the descriptor is still unknown');
    assert.equal(f.w.HomeApartmentPerceptionSource.sampleClock().uncertainty_s,null);
    f.setState('stopped');await f.timer.advance(1000);const ended=f.calls.length;
    assert.equal(f.player.snapshot().connection,'stopped');assert.equal(f.player.snapshot().imageUrl,null);
    await f.timer.advance(3000);assert.equal(f.calls.length,ended,'terminal sessions stop source polling');
    assert(f.calls.every(x=>(x.options.method||'GET')==='GET'));
  }finally{f.player.destroy();}
  assert.equal(f.timer.size(),0);assert.equal(f.subscribers.size,0);
});

function holdNextGraph(f){
  const original=f.w.fetch;let finish,signal,held=false;
  f.w.fetch=(input,options)=>{
    const url=new URL(String(input),'https://home.invalid');
    if(url.pathname.endsWith('/graph')&&!held){held=true;signal=options.signal;f.calls.push({url,options});
      return new Promise(resolve=>{finish=()=>resolve(Response.json(clone(f.graph)));});}
    return original(input,options);
  };
  return {finish(){assert.equal(typeof finish,'function');finish();},signal:()=>signal};
}

test('exact RGB reaches the actual source/player before a slow graph response and metadata joins afterward',async()=>{
  const f=viewingFixture(),held=holdNextGraph(f);f.player.start();
  try{
    await settle();
    assert.equal(f.player.snapshot().imageUrl,'blob:authored-rgb-17',
      'an unrelated graph read must not spend the original RGB presentation lease');
    assert.equal(f.player.snapshot().view.pose2d.renderable,true);
    assert.equal(f.player.snapshot().view.follow.allowed,false);
    assert.equal(f.decodes.length,1);
    held.finish();await settle();
    assert(f.player.snapshot().view.models.some(x=>x.family==='jev'&&x.result.activity==='phone_use'));
    assert.equal(f.player.snapshot().frame.valid_until,101);
  }finally{f.player.destroy();}
  assert.equal(f.timer.size(),0);
});

test('a failed image does not remove inference metadata or stop the actual observer/player from retrying',async()=>{
  const f=viewingFixture(),original=f.w.fetch;let rejectImage=true;
  f.w.fetch=(input,options)=>new URL(String(input),'https://home.invalid').pathname.endsWith('/media/image-17')&&rejectImage
    ?Promise.resolve(new Response('missing original image',{status:404})):original(input,options);
  f.player.start();
  try{
    await settle();
    assert(f.player.snapshot().view?.models.some(x=>x.family==='jev'&&x.result.activity==='phone_use'),
      'model metadata has its own provenance and must survive an image read failure');
    assert.equal(f.player.snapshot().imageUrl,null);
    rejectImage=false;await f.timer.advance(1000);
    assert.equal(f.player.snapshot().imageUrl,'blob:authored-rgb-17');
    assert.equal(f.player.snapshot().view.pose2d.renderable,true);
    assert.equal(f.decodes.length,1);
  }finally{f.player.destroy();}
  assert.equal(f.timer.size(),0);
});

test('graph outage keeps original model rows and image while serial observer retries without a new subscription',async()=>{
  const f=viewingFixture(),original=f.w.fetch;let graphUnavailable=false;
  f.w.fetch=(input,options)=>new URL(String(input),'https://home.invalid').pathname.endsWith('/graph')&&graphUnavailable
    ?Promise.resolve(new Response('graph temporarily unavailable',{status:503})):original(input,options);
  f.player.start();
  try{
    await settle();const before=clone(f.player.snapshot().view.models.find(x=>x.family==='jev'));
    graphUnavailable=true;await f.timer.advance(1000);
    const failed=f.player.snapshot();assert.equal(failed.connection,'unavailable');
    assert.equal(failed.imageUrl,'blob:authored-rgb-17');assert.equal(failed.view.follow.allowed,false);
    assert.deepEqual(clone(failed.view.models.find(x=>x.family==='jev')),before,
      'a graph retry cannot invent a fresh JEV timestamp or erase its independent result');
    graphUnavailable=false;await f.timer.advance(1000);
    assert.equal(f.player.snapshot().connection,'live');assert.equal(f.decodes.length,1);
    assert.equal(f.player.snapshot().view.models.find(x=>x.family==='jev').result.available_at_s,10.6);
  }finally{f.player.destroy();}
  assert.equal(f.timer.size(),0);assert.equal(f.subscribers.size,0);
});

test('media priority remains one bounded IO lane and cannot block lifecycle cleanup or launch a late graph',async()=>{
  const f=viewingFixture(),original=f.w.fetch;let finish,signal;
  f.w.fetch=(input,options)=>{
    if(new URL(String(input),'https://home.invalid').pathname.endsWith('/media/image-17')){
      f.calls.push({url:new URL(String(input),'https://home.invalid'),options});signal=options.signal;
      return new Promise(resolve=>{finish=()=>resolve(new Response(f.bytes,{status:206,
        headers:{'Content-Type':'image/jpeg','Content-Range':'bytes 0-5/6'}}));});
    }
    return original(input,options);
  };
  f.player.start();
  try{
    await settle();assert.equal(typeof finish,'function');
    assert.deepEqual(f.calls.map(x=>x.url.pathname.split('/').pop()),['live','descriptor','image-17']);
    await f.timer.advance(3000);assert.equal(f.calls.length,3,'graph/poll cannot overlap the image lane');
    f.player.stop('owner_disconnected');assert.equal(signal.aborted,true);
    await settle();assert.equal(f.timer.size(),1,
      'only the incumbent transport deadline may remain when its authored fetch ignores abort; observer/player timers clear');
    finish();await settle();await f.timer.advance(3000);
    assert.equal(f.calls.length,3);assert.equal(f.player.snapshot().view,null);assert.equal(f.decodes.length,0);
  }finally{f.player.destroy();}
  assert.equal(f.timer.size(),0);assert.equal(f.subscribers.size,0);
});

test('startup polling remains serial and waits one second after the prior live/graph cycle settles',async()=>{
  const f=viewingFixture();f.setState('starting');const held=holdNextGraph(f);f.player.start();
  try{
    await settle();assert.equal(f.calls.length,2);await f.timer.advance(3000);
    assert.equal(f.calls.length,2,'no overlapping cycle while the graph read is pending');
    held.finish();await settle();assert.equal(f.player.snapshot().connection,'connecting');
    f.setState('recording');await f.timer.advance(999);assert.equal(f.calls.length,2);
    await f.timer.advance(1);assert.equal(f.calls.length,6);
    assert.equal(f.player.snapshot().imageUrl,'blob:authored-rgb-17');assert.equal(f.decodes.length,1);
  }finally{f.player.destroy();}
  assert.equal(f.timer.size(),0);
});

for(const [name,abandon] of [
  ['disconnect',f=>f.player.stop()],
  ['home switch',f=>f.select('echo')],
  ['logout',f=>f.w.fire('home:logout')],
])test('startup '+name+' aborts a pending graph and rejects its late response',async()=>{
  const f=viewingFixture();f.setState('starting');const held=holdNextGraph(f);f.player.start();
  try{
    await settle();assert.equal(f.calls.length,2);abandon(f);assert.equal(held.signal().aborted,true);
    f.setState('recording');held.finish();await settle();await f.timer.advance(3000);
    assert.equal(f.calls.length,2);assert.equal(f.player.snapshot().imageUrl,null);
    assert.equal(f.decodes.length,0);assert.equal(f.player.snapshot().view,null);
  }finally{f.player.destroy();}
  assert.equal(f.timer.size(),0);assert.equal(f.subscribers.size,0);
});

test('startup cannot acquire a replacement generation when recording begins',async()=>{
  const f=viewingFixture();f.setState('starting');f.player.start();
  try{
    await settle();assert.equal(f.calls.length,2);f.setState('recording');
    f.live.session.generation=3;f.graph.session.generation=3;
    await f.timer.advance(1000);assert.equal(f.calls.length,3);
    const rejected=f.player.snapshot();assert.equal(rejected.connection,'unavailable');
    assert.equal(rejected.reason,'den_lab_session_generation_changed');assert.equal(rejected.imageUrl,null);
    await f.timer.advance(3000);assert.equal(f.calls.length,3);assert.equal(f.decodes.length,0);
  }finally{f.player.destroy();}
  assert.equal(f.timer.size(),0);
});

test('an unknown session state refuses once instead of polling an invented startup phase',async()=>{
  const f=viewingFixture();f.setState('preparing');f.player.start();
  try{
    await settle();const rejected=f.player.snapshot();
    assert.equal(rejected.connection,'unavailable');assert.equal(rejected.reason,'den_lab_session_state_unsupported');
    assert.equal(rejected.imageUrl,null);assert.equal(f.calls.length,1,'an unknown session cannot reach graph or image reads');
    await f.timer.advance(3000);assert.equal(f.calls.length,1);assert.equal(f.decodes.length,0);
  }finally{f.player.destroy();}
  assert.equal(f.timer.size(),0);
});
