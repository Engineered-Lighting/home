'use strict';
// Authored graph/event/media fixtures only. No browser/network/model requests.
const {test,after}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const Stream=require('../app/src/home-den-lab-stream.js'),ROOT=path.resolve(__dirname,'..'),SRC=path.join(ROOT,'app/src');
const babel=(()=>{const m={exports:{}};new Function('module','exports',fs.readFileSync(path.join(SRC,'vendor/babel-7.29.0/babel.min.js'),'utf8'))(m,m.exports);return m.exports.Babel||m.exports;})();
const modelWindow={},modelContext=vm.createContext({window:modelWindow,React:require('react')});
vm.runInContext(babel.transform(fs.readFileSync(path.join(SRC,'home-perception-lab-graph.jsx'),'utf8'),{presets:['react']}).code,modelContext);
const graphModel=modelWindow.HomePerceptionLabGraph.model;
const binding=()=>({digest:'a'.repeat(64),qualification:'live_shadow_unqualified',scope:{site_id:'victoria',room_id:'den',session_id:'authored-lab-session',domain_epoch:'authored-domain'}});
const settle=async()=>{for(let i=0;i<50;i++)await Promise.resolve();};
function clock(){let now=0,serial=0;const timers=new Map();return {setTimeout(fn,ms){const id=++serial;timers.set(id,{fn,at:now+ms});return id;},clearTimeout:id=>timers.delete(id),
  async advance(ms){const end=now+ms;for(;;){await settle();const next=[...timers].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;
    now=next[1].at;timers.delete(next[0]);next[1].fn();await settle();}now=end;await settle();},size:()=>timers.size};}
function events(){const listeners=new Map();return {addEventListener(name,fn){if(!listeners.has(name))listeners.set(name,new Set());listeners.get(name).add(fn);},
  removeEventListener(name,fn){listeners.get(name)?.delete(fn);},fire(name,event={}){for(const fn of listeners.get(name)||[])fn(event);},count:()=>[...listeners.values()].reduce((n,s)=>n+s.size,0)};}
function graph(){const b=binding();return {contract:'perception-lab-graph-1',contract_revision:3,site_id:'victoria',session_id:b.scope.session_id,inference:'none',
  session:{id:b.scope.session_id,site_id:'victoria',camera_id:'den',state:'recording',generation:0,started_at:100},sources:[
    {id:'ha_snapshot',title:'Home Assistant / HASS',status:'producing',counts:{entities:3,known:2},group:'home_device'},
    {id:'qwen_scene',title:'Qwen scene',status:'producing',group:'visual',native:{snippet:{json:{last_projection:{status:'available',latest:{facts:{activity_hypothesis:'working'}}}}}}},
    {id:'vjepa_scene',title:'V-JEPA',status:'not_provisioned',group:'spatial_temporal'},
    {id:'room_observation',title:'Room facts',status:'no_records',group:'spatial_temporal'},
    {id:'depth_bundle',title:'Depth bundle',status:'no_records',group:'spatial_temporal'},
    {id:'future_signal',title:'Additional recorded family',status:'unknown',group:'other'}],
  arms:{a:{chain:[{stage:'specialist_output',status:'none'},{stage:'result',status:'none'}]},
        b:{chain:[{stage:'specialist_output',status:'producing',frame_id:'original-rgb'},{stage:'result',status:'none'}]}}};}
function fixture(custom){const timer=clock(),doc={...events(),hidden:false},env={...events(),document:doc},subscribers=new Set(),calls=[],states=[];let site='victoria',generation=0;
  env.HomeSpatialViewport={getSelectedSiteId:()=>site,subscribe(fn){subscribers.add(fn);return()=>subscribers.delete(fn);}};
  const defaults=(op,args)=>op==='sessions.live'?{site_id:'victoria',session_id:binding().scope.session_id,server_time:101,
    session:{id:binding().scope.session_id,site_id:'victoria',camera_id:'den',state:'recording',generation}}
    :op==='sessions.graph'?graph():{items:[],after_id:args.afterId};
  const transport={request(op,args,options){calls.push({op,args,options});return custom?custom(op,args,options,defaults):Promise.resolve(defaults(op,args));}};
  const controller=Stream.create({binding:binding(),transport,graphModel,onState:s=>states.push(s),env,clock:timer});
  return {controller,transport,timer,doc,env,calls,states,defaults,setSite(next){site=next;for(const fn of subscribers)fn();},setGeneration:n=>generation=n,subscribers};}

test('exact current Lab reads reuse all graph families and both arms, with zero raw payload/command authority',async()=>{
  const fullKinds=['qwen.observation','ha.observation','vjepa.observation','reasoning.output','room.observation','reasoning.prepared'];
  const f=fixture((op,args,options,defaults)=>Promise.resolve(op==='sessions.events'?{items:fullKinds.map((kind,i)=>({id:i+1,session_id:binding().scope.session_id,
    kind,created_at:100,payload:{site_id:'victoria',raw_depth:'PRIVATE_RAW_MARKER'}})),after_id:fullKinds.length}:defaults(op,args)));
  f.controller.start();await settle();
  assert.deepEqual(f.calls.map(c=>c.op),['sessions.live','sessions.graph','sessions.events']);
  for(const call of f.calls)assert.equal(call.args.sessionId,binding().scope.session_id);
  const state=f.controller.snapshot();assert.equal(state.connection,'connected');assert.equal(state.beam_authority,false);assert.equal(state.operational_geometry,false);
  for(const title of ['Qwen scene','V-JEPA','Home Assistant / HASS','Room facts','Depth bundle','Additional recorded family'])assert(state.rows.some(row=>row.title===title));
  assert(state.rows.some(row=>row.id==='a:result'));assert(state.rows.some(row=>row.id==='b:result'));
  assert.deepEqual(state.event_kinds.map(row=>row.kind),fullKinds);assert(!JSON.stringify(state).includes('PRIVATE_RAW_MARKER'));
  f.controller.destroy();assert.equal(f.timer.size(),0);assert.equal(f.subscribers.size,0);
  for(const bad of [{...binding(),qualification:'controlled_software_test'},{...binding(),scope:{...binding().scope,site_id:'echo'}}]){
    assert.throws(()=>Stream.create({binding:bad,transport:f.transport,graphModel}),/fixed_live_binding/);
  }
});

test('deadline aborts without overlapping a transport that ignores cancellation; late response cannot restore cleared data',async()=>{
  let resolve,signal;const f=fixture((op,args,options,defaults)=>{signal=options.signal;return new Promise(r=>{resolve=()=>r(defaults(op,args));});});
  f.controller.start();await settle();await f.timer.advance(4000);
  assert.equal(f.controller.snapshot().connection,'unavailable');assert.equal(signal.aborted,true);assert.equal(f.calls.length,1);
  await f.timer.advance(12000);assert.equal(f.calls.length,1);
  f.controller.stop('owner_logout');resolve();await settle();await f.timer.advance(6000);
  assert.equal(f.calls.length,1);assert.equal(f.controller.snapshot().rows.length,0);f.controller.destroy();
});

test('hide/pagehide/logout/site change abort and clear; no old response enters another binding',async()=>{
  for(const exit of ['hide','pagehide','logout','site']){
    let resolve;const f=fixture((op,args,options,defaults)=>new Promise(r=>{resolve=()=>r(defaults(op,args));}));
    f.controller.start();await settle();
    if(exit==='hide'){f.doc.hidden=true;f.doc.fire('visibilitychange');}
    if(exit==='pagehide')f.env.fire('pagehide');if(exit==='logout')f.env.fire('home:logout');if(exit==='site')f.setSite('echo');
    assert.equal(f.calls[0].options.signal.aborted,true);resolve();await settle();await f.timer.advance(6000);
    assert.equal(f.calls.length,1,exit);assert.equal(f.controller.snapshot().rows.length,0,exit);f.controller.destroy();
    assert.equal(f.env.count(),0);assert.equal(f.doc.count(),0);assert.equal(f.subscribers.size,0);
  }
});

test('wrong session response and changed original media generation stop instead of silently rebinding',async()=>{
  const wrong=fixture((op,args,options,defaults)=>Promise.resolve({...defaults(op,args),session_id:'someone-else'}));
  wrong.controller.start();await settle();assert.equal(wrong.controller.snapshot().reason,'den_lab_response_binding_mismatch');
  assert.equal(wrong.calls.length,1);wrong.controller.destroy();
  const f=fixture();f.controller.start();await settle();assert(f.controller.snapshot().rows.length>0);
  f.setGeneration(1);await f.timer.advance(3000);assert.equal(f.controller.snapshot().reason,'den_lab_session_generation_changed');
  assert.equal(f.controller.snapshot().rows.length,0);const count=f.calls.length;await f.timer.advance(10000);assert.equal(f.calls.length,count);f.controller.destroy();
});

test('a graph from a different generation cannot be combined with a valid live header',async()=>{
  const f=fixture((op,args,options,defaults)=>{const data=defaults(op,args);if(op==='sessions.graph')data.session.generation=1;return Promise.resolve(data);});
  f.controller.start();await settle();assert.equal(f.controller.snapshot().reason,'den_lab_session_generation_changed');
  assert.equal(f.controller.snapshot().rows.length,0);assert.equal(f.calls.length,2);f.controller.destroy();
});

// Exercise the exact viewing observer, including its optional bounded browser
// prefix read. These bytes are authored; retained JPEG/latency replay is a
// separate artifact and does not claim live acceptance.
const {createHash}=require('node:crypto');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function mediaObserver(options={}){
  const timer=clock(),doc={...events(),hidden:false},calls=[],states=[],size=options.size||19;
  const bytes=Uint8Array.from({length:size},(_,i)=>i%251),sid='authored-view-session',mid='authored-view-media';
  const scope={siteId:'victoria',contentRevision:'authored-revision',frameDigest:'d'.repeat(64),registrationVersion:null,
    cameraId:'den',sessionId:sid,generation:0};
  const live={site_id:'victoria',session_id:sid,server_time:100,experiment_generation:1,
    session:{id:sid,site_id:'victoria',camera_id:'den',state:'recording',generation:0},arms:{B:{frame_id:'authored-frame',
      frame:{id:'authored-frame',site_id:'victoria',media_id:mid,generation:0,width:640,height:360,viewing_only:true},
      profile:{id:'authored-run',name:'authored-model'},observation:{schema:'home-lab-arm-observation-1',arm:'b',session_id:sid,
        site_id:'victoria',camera_id:'den',request_id:'authored-request',generation:1,frame_id:'authored-frame',media_ids:[mid],
        capture_epoch:'authored-capture',run_id:'authored-run',model_version:'authored-model',artifact_sha256:'e'.repeat(64),
        engine_epoch:'authored-engine',observed_at:99,available_at:100,valid_until:102}}}};
  const item={id:mid,media_id:mid,site_id:'victoria',session_id:sid,generation:0,size_bytes:size,content_type:'image/jpeg',
    sha256:createHash('sha256').update(bytes).digest('hex'),metadata:{site_id:'victoria',camera_id:'den',frame_id:'authored-frame',
      capture_epoch:'authored-capture',valid_until:101}};
  options.mutateItem?.(item);
  const part=args=>{const end=Math.min(args.offset+args.length,size),value={data:bytes.slice(args.offset,end),contentType:'image/jpeg',
    contentRange:`bytes ${args.offset}-${end-1}/${size}`,generation:0};options.mutatePart?.(value,args);return value;};
  const transport={request(op,args,opts){calls.push({op,args,signal:opts.signal});
    if(op==='sessions.live')return Promise.resolve(structuredClone(live));
    if(op==='sessions.graph')return Promise.resolve({contract:'perception-lab-graph-1',site_id:'victoria',session_id:sid,
      inference:'none',session:{...live.session},sources:[],arms:{}});
    assert.equal(op,'media.describe');return options.describe?options.describe(opts,item):Promise.resolve(item);},
    requestMedia(args,opts){calls.push({op:'media.raw',args,signal:opts.signal});return options.raw?options.raw(opts,args,part):Promise.resolve(part(args));}};
  const env={...events(),document:doc,Date:{now:()=>100000},HomeSpatialViewport:{getSelectedSiteId:()=> 'victoria'},
    crypto:{subtle:{digest:async(name,value)=>{assert.equal(name,'SHA-256');return Uint8Array.from(createHash('sha256').update(value).digest()).buffer;}}},
    HomePerceptionLabMedia:{readFrame:async(descriptor,context,signal)=>{calls.push({op:'adapter.readFrame',descriptor,context,signal});return {bytes,type:'image/jpeg'};}},
    ...options.env};
  const controller=Stream.createObserver({siteId:'victoria',sessionId:sid,scope,generation:0,transport,clock:timer,env,onState:s=>states.push(s)});
  return {controller,scope,live,item,bytes,calls,states,timer,doc,env,part,
    async start(){controller.start();await settle();return controller.snapshot().perception.frame;}};
}

test('exact browser view starts descriptor and a clipped64KiB prefix together, then caches only verified bytes and original lease',async()=>{
  const desc=deferred(),raw=deferred(),f=mediaObserver({describe:()=>desc.promise,raw:()=>raw.promise});
  const frame=await f.start(),read=f.controller.readFrame(frame);await settle();
  assert.deepEqual(f.calls.filter(c=>c.op.startsWith('media.')).map(c=>c.op),['media.describe','media.raw']);
  assert.equal(f.calls.at(-1).args.length,65536);assert.equal(f.controller.snapshot().perception.frame.media_sha256,undefined);
  raw.resolve(f.part(f.calls.at(-1).args));await settle();
  assert.equal(f.controller.snapshot().perception.frame.media_sha256,undefined,'unverified prefix has no presentation authority');
  desc.resolve(f.item);const value=await read;assert.deepEqual(value.bytes,f.bytes);assert.equal(value.frame_metadata.valid_until,101);
  assert.equal(value.frame_metadata.capture_epoch,'authored-capture');assert.equal(value.frame_metadata.media_sha256,f.item.sha256);
  const count=f.calls.length;value.bytes[0]=255;
  assert.deepEqual((await f.controller.readFrame(f.controller.snapshot().perception.frame)).bytes,f.bytes);
  assert.equal(f.calls.length,count,'verified cache clone avoids another media request');f.controller.destroy();
});

test('browser prefix continuation remains bounded4MiB with exact final clipping and whole-object SHA',async()=>{
  const f=mediaObserver({size:65536+4*1024*1024+9}),frame=await f.start();
  assert.deepEqual((await f.controller.readFrame(frame)).bytes,f.bytes);
  assert.deepEqual(f.calls.filter(c=>c.op==='media.raw').map(c=>[c.args.offset,c.args.length]),[[0,65536],[65536,4*1024*1024],[65536+4*1024*1024,9]]);
  assert.equal(f.calls.filter(c=>c.op==='adapter.readFrame').length,0);f.controller.destroy();
});

test('browser view rejects changed descriptor identity, malformed ranges/types/sizes and corrupt SHA before cache or decoder',async()=>{
  const cases=[
    ['session',f=>{f.session_id='other-session';},null,'descriptor'],['generation',f=>{f.generation=1;},null,'descriptor'],
    ['media',f=>{f.media_id='other-media';},null,'descriptor'],['ambiguous IDs',f=>{f.id='other-media';},null,'descriptor'],
    ['frame',f=>{f.metadata.frame_id='other-frame';},null,'descriptor'],
    ['capture',f=>{f.metadata.capture_epoch='other-capture';},null,'descriptor'],
    ['size',f=>{f.size_bytes=33*1024*1024;},null,'descriptor'],['type',f=>{f.content_type='text/html';},null,'descriptor'],
    ['missing original expiry',f=>{delete f.metadata.valid_until;},null,'descriptor'],
    ...[null,0,-1,NaN,Infinity,'101'].map(value=>['malformed original expiry '+value,f=>{f.metadata.valid_until=value;},null,'descriptor']),
    ['range',null,p=>{p.contentRange='bytes 0-17/19';},'range'],['short',null,p=>{p.data=p.data.slice(1);},'range'],
    ['raw MIME',null,p=>{p.contentType='image/png';},'range'],['raw generation',null,p=>{p.generation=1;},'range'],
    ['SHA',f=>{f.sha256='0'.repeat(64);},null,'media_sha256']];
  for(const [name,mutateItem,mutatePart,reason]of cases){const f=mediaObserver({mutateItem,mutatePart}),frame=await f.start();
    await assert.rejects(f.controller.readFrame(frame),new RegExp('den_lab_frame_'+reason+'_mismatch'),name);
    assert.equal(f.controller.snapshot().perception.frame.media_sha256,undefined,name);
    assert.equal(f.calls.filter(c=>c.op==='adapter.readFrame').length,0,name);f.controller.destroy();}
});

test('optimized browser read refuses the original expiry at admission and again after hashing, without altering that deadline',async()=>{
  const expired=mediaObserver({mutateItem:item=>{item.metadata.valid_until=100;}}),old=await expired.start();
  await assert.rejects(expired.controller.readFrame(old),/den_lab_frame_expired/);
  assert.equal(expired.controller.snapshot().perception.frame.media_sha256,undefined);expired.controller.destroy();
  let now=100000;const digest=deferred(),f=mediaObserver({env:{Date:{now:()=>now},crypto:{subtle:{digest:()=>digest.promise}}}});
  const frame=await f.start(),read=f.controller.readFrame(frame);await settle();now=101000;
  digest.resolve(Uint8Array.from(createHash('sha256').update(f.bytes).digest()).buffer);
  await assert.rejects(read,/den_lab_frame_expired/);assert.equal(f.item.metadata.valid_until,101);
  assert.equal(f.controller.snapshot().perception.frame.media_sha256,undefined);f.controller.destroy();
});

test('either401/410 media branch aborts and drains its blocked sibling without publishing bytes',async()=>{
  for(const failing of ['describe','raw'])for(const status of [401,410]){
    let peerSignal,peerAborted=false;const error=Object.assign(Error('authored_denial'),{code:'authored_denial',status});
    const blocked=({signal})=>{peerSignal=signal;return new Promise((_,reject)=>{signal.addEventListener('abort',()=>{
      peerAborted=true;reject(new DOMException('Aborted','AbortError'));},{once:true});});};
    const f=mediaObserver({describe:failing==='describe'?()=>Promise.reject(error):blocked,
      raw:failing==='raw'?()=>Promise.reject(error):blocked}),frame=await f.start();
    await assert.rejects(f.controller.readFrame(frame),e=>e===error);assert(peerSignal.aborted);assert(peerAborted);
    assert.equal(f.controller.snapshot().perception.frame.media_sha256,undefined);
    assert.equal(f.calls.filter(c=>c.op==='media.raw').length,1);f.controller.destroy();
  }
});

test('an ignored sibling abort cannot free the IO lane; outer deadline and Stop stay bounded, late completion is discarded',async()=>{
  const desc=deferred();let peerSignal;
  const f=mediaObserver({describe:({signal})=>{peerSignal=signal;return desc.promise;},
    raw:()=>Promise.reject(Object.assign(Error('gone'),{status:410,code:'lab_media_unavailable'}))});
  const frame=await f.start(),read=f.controller.readFrame(frame),rejected=assert.rejects(read,/den_lab_read_timeout/);await settle();
  assert(peerSignal.aborted);const count=f.calls.length;await f.timer.advance(4000);await rejected;
  await f.timer.advance(10000);assert.equal(f.calls.length,count,'uncancellable peer retains the single IO lane');
  f.controller.stop();assert.equal(f.controller.snapshot().perception.frame,null);
  desc.resolve(f.item);await settle();await f.timer.advance(5000);assert.equal(f.calls.length,count);f.controller.destroy();
});

test('Stop, hidden page, changed scope and selected arm discard late prefix/descriptor results',async()=>{
  for(const exit of ['stop','hide','scope','arm']){
    const desc=deferred(),raw=deferred(),f=mediaObserver({describe:()=>desc.promise,raw:()=>raw.promise});
    const frame=await f.start(),read=f.controller.readFrame(frame),rejected=assert.rejects(read,/den_lab_(read_abandoned|current_frame_required)/);await settle();
    if(exit==='stop')f.controller.stop();if(exit==='hide'){f.doc.hidden=true;f.doc.fire('visibilitychange');}
    if(exit==='scope')f.scope.contentRevision='new-epoch';if(exit==='arm')f.controller.setArm('A');
    desc.resolve(f.item);raw.resolve(f.part(f.calls.find(c=>c.op==='media.raw').args));await rejected;await settle();
    assert(!f.states.some(s=>s.perception.frame?.media_sha256),exit);f.controller.destroy();assert.equal(f.timer.size(),0);
  }
});

test('Tauri authority and no-WebCrypto fallback retain sequential descriptor then existing media adapter',async()=>{
  for(const env of [{__TAURI__:{}},{__TAURI_INTERNALS__:{}},{crypto:undefined}]){
    const desc=deferred(),f=mediaObserver({env,describe:()=>desc.promise}),frame=await f.start(),read=f.controller.readFrame(frame);await settle();
    assert.equal(f.calls.filter(c=>c.op==='media.describe').length,1);assert.equal(f.calls.filter(c=>c.op==='media.raw'||c.op==='adapter.readFrame').length,0);
    desc.resolve(f.item);assert.deepEqual((await read).bytes,f.bytes);
    assert.equal(f.calls.filter(c=>c.op==='media.raw').length,0);assert.equal(f.calls.filter(c=>c.op==='adapter.readFrame').length,1);
    f.controller.destroy();
  }
});
