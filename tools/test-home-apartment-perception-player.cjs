/* Public controller lifecycle tests with authored frames; no camera, model or network. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const path=require('node:path');
const Player=require('../app/src/home-apartment-perception-player.js');
const View=require('../app/src/home-apartment-perception-view.js');
const copy=x=>JSON.parse(JSON.stringify(x));
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const scope=()=>({siteId:'victoria',contentRevision:'authored-pack-r1',frameDigest:'frame-authored',registrationVersion:1,
  cameraId:'den',sessionId:'authored-session',generation:1});

test('raw classic loadLayer crosses the native import boundary at only the fixed versioned module URL',()=>{
  // Run the untransformed public browser API with Node's actual import() hook.
  // The hook returns a harmless native module; the real renderer ESM is covered
  // by the production-Babel browser journey, without acquiring camera authority.
  const script=String.raw`
    const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
    const raw=fs.readFileSync(process.argv[1],'utf8'),seen=[];
    const window={document:{baseURI:'https://home.invalid/ordinary/index.html?layout=classic'},URL,
      __HOME_BOOT_ASSET_SUFFIX:'?v=frozen%20generation',__HOME_ASSET_VERSION:'ignored fallback'};
    const sandbox=vm.createContext({window,URL});
    const playerScript=new vm.Script(raw,{filename:process.argv[1],importModuleDynamically(specifier){
      seen.push(specifier);return import('node:assert/strict');
    }});
    playerScript.runInContext(sandbox);
    (async()=>{
      const player=window.HomeApartmentPerceptionPlayer;
      assert.equal(typeof player.create,'function');assert.equal(typeof player.loadLayer,'function');
      assert.equal(player.loadLayer.length,0);assert.deepEqual(seen,[],'no eager module load');
      const module=await player.loadLayer('https://outside.invalid/unwanted.mjs');
      assert.equal(typeof module.strictEqual,'function','the native import promise resolves a module namespace');
      assert.equal(seen[0],'https://home.invalid/ordinary/home-3d/apartment-perception-layer.mjs?v=frozen%20generation');
      delete window.__HOME_BOOT_ASSET_SUFFIX;window.__HOME_ASSET_VERSION='release + slash/';
      await player.loadLayer();
      assert.equal(seen[1],'https://home.invalid/ordinary/home-3d/apartment-perception-layer.mjs?v=release%20%2B%20slash%2F');
      window.__HOME_BOOT_ASSET_SUFFIX='';window.__HOME_ASSET_VERSION='empty boot fallback';
      await player.loadLayer();
      assert.equal(seen[2],'https://home.invalid/ordinary/home-3d/apartment-perception-layer.mjs?v=empty%20boot%20fallback');
      delete window.__HOME_ASSET_VERSION;window.document.baseURI='https://home.invalid/?layout=classic';
      await player.loadLayer();
      assert.equal(seen[3],'https://home.invalid/home-3d/apartment-perception-layer.mjs');
      process.stdout.write(JSON.stringify({nativeImports:seen.length,requireAvailableInBrowser:vm.runInContext('typeof require',sandbox)}));
    })().catch(error=>{console.error(error);process.exitCode=1;});
  `;
  const receipt=JSON.parse(execFileSync(process.execPath,['--experimental-vm-modules','-e',script,
    path.join(__dirname,'../app/src/home-apartment-perception-player.js')],{encoding:'utf8',timeout:15000}));
  assert.deepEqual(receipt,{nativeImports:4,requireAvailableInBrowser:'undefined'});
});

test('raw classic loadLayer exposes a native import failure without a CommonJS fallback',()=>{
  const script=String.raw`
    const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
    const window={document:{baseURI:'https://home.invalid/'},URL};let attempts=0;
    const sandbox=vm.createContext({window,URL});
    new vm.Script(fs.readFileSync(process.argv[1],'utf8'),{importModuleDynamically(specifier){
      attempts++;assert.equal(specifier,'https://home.invalid/home-3d/apartment-perception-layer.mjs');
      throw Error('authored_native_module_unavailable');
    }}).runInContext(sandbox);
    (async()=>{
      await assert.rejects(window.HomeApartmentPerceptionPlayer.loadLayer(),/authored_native_module_unavailable/);
      assert.equal(attempts,1);assert.equal(vm.runInContext('typeof require',sandbox),'undefined');
      process.stdout.write('native failure preserved');
    })().catch(error=>{console.error(error);process.exitCode=1;});
  `;
  const receipt=execFileSync(process.execPath,['--experimental-vm-modules','-e',script,
    path.join(__dirname,'../app/src/home-apartment-perception-player.js')],{encoding:'utf8',timeout:15000});
  assert.equal(receipt,'native failure preserved');
});

function envelope(id='A',x=.1,options={}){
  const s=scope();
  return {schema:'home-apartment-perception-1',scope:s,source_epoch:'source-1',session_state:'recording',
    frame:{frame_id:id,capture_id:'capture-'+id,media_id:'media-'+id,sequence_id:id,
      captured_at:99,valid_until:101,width:640,height:360,generation:1,site_id:'victoria'},
    pose2d:{frame_id:id,capture_id:'capture-'+id,observed_at:99,available_at:100,valid_until:101,
      model_id:'pose-model',source_id:'authored-pose',status:'available',
      keypoints:[{id:'hip',x,y:.4,visible:true},{id:'head',x,y:.2,visible:true}],skeleton_edges:[['hip','head']]},
    models:[{family:'jev',source_id:'authored-jev',model_id:'jev-model',status:'available',
      observed_at:95,available_at:99,valid_until:102,frame_id:id,capture_id:'capture-'+id,result:{activity:'reading'}}],
    room_pose:{status:'admitted',qualification:'diagnostic_alignment',frame_id:id,capture_id:'capture-'+id,
      observed_at:99,available_at:100,valid_until:101,scope:s,
      registration:{status:'applicable',camera_id:'den',camera_to_room_digest:'authored-registration'},
      joints:[{id:'hip',position_m:[1,2,1],visible:true},{id:'head',position_m:[1,2,1.7],visible:true}],
      edges:[['hip','head']],anchor_joint_id:'hip'},...options};
}
function fixture({decode:customDecode,lateSubscription=false,browser=false}={}){
  const environment=new EventTarget(),document=new EventTarget(),timers=new Map();
  let timerSeq=0,now=100,selected='victoria',registryCallback=null,offCount=0;
  document.hidden=false;
  Object.assign(environment,{document,AbortController,HomeApartmentPerceptionView:View,
    setInterval(fn,ms){assert.equal(ms,250);timers.set(++timerSeq,fn);return timerSeq;},
    clearInterval(id){timers.delete(id);},
    HomeSpatialViewport:{getSelectedSiteId:()=>selected,subscribe(fn){registryCallback=fn;return ()=>{registryCallback=null;};}}});
  const subscriptions=[],reads=[],decodes=[],released=[],updates=[],subscriptionDeferred=deferred();
  const source={subscribe(args){subscriptions.push(args);return lateSubscription?subscriptionDeferred.promise:()=>{offCount++;};},
    sampleClock:()=>({now_s:now,uncertainty_s:0,domain:'unix'}),
    readFrame(frame,args){const job={frame:copy(frame),args,...deferred()};reads.push(job);return job.promise;}};
  const fakeDecode=(value,args)=>{const job={value,args,...deferred()};decodes.push(job);return job.promise;};
  const options={source,scope:scope(),onChange:x=>updates.push(x),env:environment};
  if(!browser)options.decode=customDecode||fakeDecode;
  const player=Player.create(options);
  function owned(id,width,height){return {url:'blob:'+id,...(width===undefined?{}:{width,height}),dispose(){released.push(id);}};}
  async function media(index=reads.length-1){reads[index].resolve({bytes:new Uint8Array([1,2,3]),type:'image/jpeg'});await settle();}
  async function complete(id='A'){
    subscriptions.at(-1).onUpdate(envelope(id));await settle();await media();decodes.at(-1).resolve(owned(id));await settle();
  }
  return {player,source,env:environment,subscriptions,reads,decodes,released,updates,timers,document,owned,media,complete,
    push:value=>subscriptions.at(-1).onUpdate(value),status:value=>subscriptions.at(-1).onStatus(value),
    setClock:value=>{now=value;},tick:()=>{for(const fn of [...timers.values()])fn();},
    switchHome:value=>{selected=value;registryCallback?.();},offCount:()=>offCount,
    resolveSubscription:()=>subscriptionDeferred.resolve(()=>{offCount++;})};
}

test('publishes image and exact supplied pose only after decoding, as one swap',async()=>{
  const f=fixture();f.player.start();f.push(envelope());await settle();
  assert.equal(f.reads.length,1);assert.equal(f.player.snapshot().imageUrl,null);
  assert.equal(f.player.snapshot().view.follow.allowed,false);
  await f.media();assert.equal(f.decodes.length,1);assert.equal(f.player.snapshot().imageUrl,null);
  f.decodes[0].resolve(f.owned('A'));await settle();
  const value=f.player.snapshot();assert.equal(value.imageUrl,'blob:A');assert.equal(value.frame.frame_id,'A');
  assert.equal(value.view.pose2d.renderable,true);assert.equal(value.view.pose2d.keypoints[0].x,.1);
  assert.equal(value.view.follow.allowed,true);assert.equal(value.loading,false);
  assert.ok(f.updates.filter(x=>x.imageUrl).every(x=>x.frame.frame_id==='A'&&x.view.pose2d.keypoints[0].x===.1));
  f.player.destroy();assert.deepEqual(f.released,['A']);
});

test('viewing-only exact image and all overlays withdraw at original expiry, including an expired delayed decode',async()=>{
  const f=fixture();f.player.start();
  const packet=envelope();packet.frame.viewing_only=true;
  packet.pose2d.persons2d={schema:'home-lab-frame-persons-1',version:1,frame_id:'A',coordinate_space:'source_pixels',persons:[]};
  f.push(packet);await settle();await f.media();f.decodes[0].resolve(f.owned('A'));await settle();
  assert.equal(f.player.snapshot().imageUrl,'blob:A');
  f.setClock(103);f.tick();
  assert.equal(f.player.snapshot().imageUrl,null,'an original expiry cannot leave a current-looking image/overlay');
  assert.equal(f.player.snapshot().view.pose2d.renderable,false);
  const next=envelope('B');next.frame.viewing_only=true;next.pose2d.persons2d={...packet.pose2d.persons2d,frame_id:'B'};
  f.setClock(100);f.push(next);await settle();await f.media();
  f.setClock(103);f.decodes.at(-1).resolve(f.owned('B'));await settle();
  assert.equal(f.player.snapshot().imageUrl,null,'late decode cannot resurrect expired original pixels');
  assert(f.released.includes('B'));f.player.destroy();
});

test('holds preceding image with its own pose while latest model rows keep original times and association',async()=>{
  const f=fixture();f.player.start();await f.complete();
  f.push(envelope('B',.9));await settle();await f.media();
  let value=f.player.snapshot();assert.equal(value.imageUrl,'blob:A');assert.equal(value.frame.frame_id,'A');
  assert.equal(value.heldFrame,true);assert.equal(value.reason,'new_frame_loading');
  assert.equal(value.view.pose2d.keypoints[0].x,.1);assert.equal(value.view.models[0].association,'different-frame');
  assert.equal(value.view.models[0].observed_at,95);assert.equal(value.view.follow.allowed,false);
  f.decodes.at(-1).resolve(f.owned('B'));await settle();value=f.player.snapshot();
  assert.equal(value.imageUrl,'blob:B');assert.equal(value.frame.frame_id,'B');
  assert.equal(value.view.pose2d.keypoints[0].x,.9);assert.equal(value.view.models[0].association,'same-frame');
  assert.deepEqual(f.released,['A']);f.player.destroy();
});

test('same exact decoded frame updates pose without another media read or decode',async()=>{
  const f=fixture();f.player.start();await f.complete();f.push(envelope('A',.7));
  assert.equal(f.player.snapshot().view.pose2d.keypoints[0].x,.7);
  assert.equal(f.reads.length,1);assert.equal(f.decodes.length,1);f.player.destroy();
});

test('changed descriptor dimensions do not attach new pose coordinates to a retained old image',async()=>{
  const f=fixture();f.player.start();await f.complete();
  const changed=envelope('A',.8);changed.frame.width=320;changed.frame.height=180;
  f.push(changed);await settle();await f.media();
  let value=f.player.snapshot();assert.equal(value.frame.width,640);assert.equal(value.heldFrame,true);
  assert.equal(value.view.pose2d.keypoints[0].x,.1);
  f.decodes.at(-1).resolve(f.owned('A-small',320,180));await settle();value=f.player.snapshot();
  assert.equal(value.frame.width,320);assert.equal(value.view.pose2d.keypoints[0].x,.8);
  assert.equal(value.imageUrl,'blob:A-small');f.player.destroy();
});

test('a newly decoded image never inherits the prior frame skeleton',async()=>{
  const f=fixture();f.player.start();await f.complete();
  const b=envelope('B');b.pose2d=envelope('A').pose2d;f.push(b);await settle();await f.media();
  f.decodes.at(-1).resolve(f.owned('B'));await settle();
  assert.equal(f.player.snapshot().imageUrl,'blob:B');assert.equal(f.player.snapshot().view.pose2d.renderable,false);
  assert.deepEqual(f.player.snapshot().view.pose2d.keypoints,[]);f.player.destroy();
});

test('latest frame wins delayed decodes and every stale decoded result is disposed',async()=>{
  const f=fixture();f.player.start();await f.complete();
  f.push(envelope('B'));await settle();await f.media();const b=f.decodes.at(-1);
  f.push(envelope('C'));await settle();assert.equal(b.args.signal.aborted,true);await f.media();
  f.decodes.at(-1).resolve(f.owned('C'));await settle();b.resolve(f.owned('B'));await settle();
  assert.equal(f.player.snapshot().imageUrl,'blob:C');assert.deepEqual(f.released,['A','B']);
  f.player.destroy();assert.deepEqual(f.released,['A','B','C']);
});

test('late reads are abandoned before decoder creation',async()=>{
  const f=fixture();f.player.start();f.push(envelope('A'));await settle();const a=f.reads[0];
  f.push(envelope('B'));await settle();assert.equal(a.args.signal.aborted,true);
  a.resolve({bytes:new Uint8Array([1]),type:'image/jpeg'});await settle();assert.equal(f.decodes.length,0);
  await f.media();assert.equal(f.decodes.length,1);f.player.destroy();
  f.decodes[0].resolve(f.owned('B'));await settle();assert.deepEqual(f.released,['B']);
});

test('identity change during a read is rechecked before decoder ownership or Blob creation',async()=>{
  const f=fixture();f.player.start();f.push(envelope());await settle();
  f.env.__APT_ENGINE={sceneIdentity:{...scope(),frameDigest:'replacement-frame'}};
  f.reads[0].resolve({bytes:new Uint8Array([1]),type:'image/jpeg'});await settle();
  assert.equal(f.decodes.length,0);assert.equal(f.player.snapshot().imageUrl,null);
  assert.equal(f.player.snapshot().connection,'disconnected');assert.equal(f.reads[0].args.signal.aborted,true);
  assert.equal(f.offCount(),1);f.player.destroy();
});

test('stop aborts reads, clears private state and releases a promise-delayed subscription once',async()=>{
  const f=fixture({lateSubscription:true});f.player.start();f.push(envelope());await settle();
  f.player.stop('owner_disconnect');assert.equal(f.reads[0].args.signal.aborted,true);
  assert.equal(f.subscriptions[0].signal.aborted,true);assert.equal(f.timers.size,0);
  assert.equal(f.player.snapshot().view,null);assert.equal(f.player.snapshot().imageUrl,null);
  f.resolveSubscription();await settle();assert.equal(f.offCount(),1);
  f.reads[0].resolve({bytes:new Uint8Array([1]),type:'image/jpeg'});await settle();assert.equal(f.decodes.length,0);
  f.player.destroy();assert.equal(f.offCount(),1);
});

test('stop during decode disposes its late result and cannot republish a frame',async()=>{
  const f=fixture();f.player.start();f.push(envelope());await settle();await f.media();
  f.player.stop('home_switched');assert.equal(f.decodes[0].args.signal.aborted,true);
  f.decodes[0].resolve(f.owned('A'));await settle();
  assert.equal(f.player.snapshot().imageUrl,null);assert.deepEqual(f.released,['A']);assert.equal(f.offCount(),1);
  f.player.destroy();
});

test('source epoch replacement clears old private image before reading even an identical frame ID',async()=>{
  const f=fixture();f.player.start();await f.complete();f.push(envelope('A',.2,{source_epoch:'source-2'}));
  assert.equal(f.player.snapshot().imageUrl,null);assert.equal(f.player.snapshot().frame,null);
  assert.deepEqual(f.released,['A']);await settle();assert.equal(f.reads.length,2);
  f.player.destroy();
});

for(const length of [160,161,256,257])test('source epoch public boundary '+length+' aligns with the producer 256-character bound',async()=>{
  const f=fixture();f.player.start();f.push(envelope('A',.1,{source_epoch:'e'.repeat(length)}));await settle();
  if(length<=256){
    assert.equal(f.reads.length,1);await f.media();f.decodes.at(-1).resolve(f.owned('A'));await settle();
    assert.equal(f.player.snapshot().imageUrl,'blob:A');assert.equal(f.player.snapshot().connection,'live');
  }else{
    assert.equal(f.reads.length,0);assert.equal(f.player.snapshot().connection,'refused');
    assert.equal(f.player.snapshot().reason,'source_epoch_invalid');assert.equal(f.player.snapshot().imageUrl,null);
  }
  f.player.destroy();
});

for(const [field,value] of [['generation',2],['contentRevision','other-pack'],['frameDigest','other-frame'],['cameraId','other-camera']]){
  test('wrong '+field+' clears private image and refuses the source immediately',async()=>{
    const f=fixture();f.player.start();await f.complete();const wrong=envelope();wrong.scope[field]=value;f.push(wrong);
    assert.equal(f.player.snapshot().connection,'refused');assert.equal(f.player.snapshot().imageUrl,null);
    assert.equal(f.player.snapshot().view,null);assert.deepEqual(f.released,['A']);assert.equal(f.offCount(),1);
    f.player.destroy();
  });
}

for(const status of ['unavailable','refused','disconnected'])test('source '+status+' clears media and follow without retaining a subscription',async()=>{
  const f=fixture();f.player.start();await f.complete();f.status({status,reason:'authored-source-state'});
  assert.equal(f.player.snapshot().connection,status);assert.equal(f.player.snapshot().view,null);
  assert.equal(f.player.snapshot().imageUrl,null);assert.equal(f.offCount(),1);assert.equal(f.timers.size,0);f.player.destroy();
});

test('stopped session is explicitly recorded and never follows the supplied room pose',async()=>{
  const f=fixture();f.player.start();await f.complete();f.status({status:'stopped'});
  const value=f.player.snapshot();assert.equal(value.imageUrl,'blob:A');assert.equal(value.connection,'stopped');
  assert.equal(value.view.pose2d.status,'recorded');assert.equal(value.view.follow.allowed,false);
  f.player.refresh();assert.equal(f.player.snapshot().connection,'stopped');assert.equal(f.player.snapshot().view.follow.allowed,false);
  f.push(envelope('A',.1,{session_state:'stopped'}));assert.equal(f.player.snapshot().view.follow.allowed,false);f.player.destroy();
});

test('transient connecting status withdraws follow without relabeling original source times',async()=>{
  const f=fixture();f.player.start();await f.complete();f.status({status:'connecting'});
  const value=f.player.snapshot();assert.equal(value.connection,'connecting');assert.equal(value.imageUrl,'blob:A');
  assert.equal(value.view.follow.allowed,false);assert.equal(value.view.roomPose.admitted,false);
  assert.equal(value.view.pose2d.originalTimes.observed_at,99);
  f.player.refresh();assert.equal(f.player.snapshot().connection,'connecting');assert.equal(f.player.snapshot().view.follow.allowed,false);f.player.destroy();
});

test('nonlive status cannot retain a pause-eligible mismatch while a newer exact frame decodes',async()=>{
  const f=fixture();f.player.start();await f.complete();f.push(envelope('B'));await settle();await f.media();
  assert.equal(f.player.snapshot().view.roomPose.reason,'displayed_frame_mismatch');
  assert.equal(f.player.snapshot().view.roomPose.age.status,'current');
  f.status({status:'connecting'});
  let value=f.player.snapshot();assert.equal(value.imageUrl,'blob:A');assert.equal(value.heldFrame,true);
  assert.equal(value.view.roomPose.reason,'source_not_live');assert.equal(value.view.roomPose.admitted,false);
  assert.equal(value.view.roomPose.anchor,null);assert.deepEqual(value.view.roomPose.joints,[]);
  assert.equal(value.view.follow.allowed,false);
  f.status({status:'stopped'});value=f.player.snapshot();assert.equal(value.view.roomPose.reason,'source_not_live');
  f.player.destroy();f.decodes.at(-1).resolve(f.owned('B'));await settle();
});

test('clock expiry during a decode hold stays stale and cannot re-admit follow when that decode completes',async()=>{
  const f=fixture();f.player.start();await f.complete();f.push(envelope('B'));await settle();await f.media();
  let value=f.player.snapshot();assert.equal(value.view.roomPose.reason,'displayed_frame_mismatch');
  assert.equal(value.view.roomPose.age.status,'current');
  f.setClock(103);f.tick();value=f.player.snapshot();
  assert.equal(value.view.roomPose.age.status,'stale');assert.equal(value.view.roomPose.status,'stale');
  assert.equal(value.view.follow.allowed,false);assert.equal(value.imageUrl,'blob:A');
  f.decodes.at(-1).resolve(f.owned('B'));await settle();value=f.player.snapshot();
  assert.equal(value.imageUrl,'blob:B');assert.equal(value.view.roomPose.age.status,'stale');
  assert.equal(value.view.follow.allowed,false);f.player.destroy();
});

test('local 250ms tick expires room geometry without polling source or changing observation times',async()=>{
  const f=fixture();f.player.start();await f.complete();assert.equal(f.player.snapshot().view.follow.allowed,true);
  f.setClock(103);f.tick();const value=f.player.snapshot();
  assert.equal(value.view.follow.allowed,false);assert.equal(value.view.roomPose.age.status,'stale');
  assert.equal(value.view.pose2d.originalTimes.observed_at,99);assert.equal(f.reads.length,1);assert.equal(f.subscriptions.length,1);
  f.player.destroy();
});

for(const kind of ['pagehide','logout','home-switch','renderer-replacement'])test(kind+' withdraws media, pending reads and projection',async()=>{
  const f=fixture();f.player.start();await f.complete();
  if(kind==='home-switch')f.switchHome('echo');
  else if(kind==='renderer-replacement'){f.env.__APT_ENGINE={sceneIdentity:{...scope(),contentRevision:'new-pack'}};f.tick();}
  else f.env.dispatchEvent(new Event(kind==='logout'?'home:logout':'pagehide'));
  assert.equal(f.player.snapshot().imageUrl,null);assert.equal(f.player.snapshot().view,null);assert.equal(f.offCount(),1);
  assert.equal(f.timers.size,0);f.player.destroy();
});

test('refresh does not add a polling loop and destroyed controller cannot restart',async()=>{
  const f=fixture();f.player.start();await f.complete();f.player.refresh();f.player.refresh();
  assert.equal(f.subscriptions.length,1);assert.equal(f.reads.length,1);assert.equal(f.timers.size,1);
  f.player.destroy();assert.equal(f.player.snapshot().disposed,true);assert.equal(f.timers.size,0);
  assert.throws(()=>f.player.start(),/destroyed/);f.player.destroy();assert.deepEqual(f.released,['A']);
});

test('frame descriptor generation/site mismatch is never read or presented',async()=>{
  const f=fixture();f.player.start();const wrong=envelope();wrong.frame.generation=2;f.push(wrong);await settle();
  assert.equal(f.reads.length,0);assert.equal(f.player.snapshot().imageUrl,null);
  assert.equal(f.player.snapshot().view.follow.allowed,false);f.player.destroy();
});

function browserFixture(dimensions={width:640,height:360}){
  const f=fixture({browser:true}),images=[],created=[],revoked=[];
  f.env.Blob=Blob;f.env.URL={createObjectURL(){const id='blob:browser-'+created.length;created.push(id);return id;},
    revokeObjectURL(id){revoked.push(id);}};
  f.env.Image=class{constructor(){this.naturalWidth=dimensions.width;this.naturalHeight=dimensions.height;this.wait=deferred();images.push(this);}
    decode(){return this.wait.promise;}};
  return {...f,images,created,revoked};
}
test('default browser decoder waits for real decode and verifies intrinsic dimensions',async()=>{
  const f=browserFixture();f.player.start();f.push(envelope());await settle();await f.media();
  assert.deepEqual(f.created,['blob:browser-0']);assert.equal(f.player.snapshot().imageUrl,null);
  f.images[0].wait.resolve();await settle();assert.equal(f.player.snapshot().imageUrl,'blob:browser-0');
  f.player.destroy();assert.deepEqual(f.revoked,['blob:browser-0']);
});
test('default browser decoder rejects descriptor/image mismatch and revokes the private URL',async()=>{
  const f=browserFixture({width:1280,height:720});f.player.start();f.push(envelope());await settle();await f.media();
  f.images[0].wait.resolve();await settle();assert.equal(f.player.snapshot().connection,'live');
  assert.equal(f.player.snapshot().reason,'frame_dimensions_mismatch');assert.equal(f.player.snapshot().imageUrl,null);
  assert.equal(f.player.snapshot().view.models[0].result.activity,'reading');assert.equal(f.offCount(),0);
  assert.deepEqual(f.revoked,['blob:browser-0']);f.player.destroy();
});

test('image access refusal withdraws private data even when metadata had already arrived',async()=>{
  const f=fixture();f.player.start();f.push(envelope());await settle();
  assert.equal(f.player.snapshot().view.models[0].result.activity,'reading');
  f.reads[0].reject(Object.assign(Error('owner access refused'),{status:403,code:'lab_forbidden'}));await settle();
  assert.equal(f.player.snapshot().connection,'refused');assert.equal(f.player.snapshot().view,null);
  assert.equal(f.player.snapshot().imageUrl,null);assert.equal(f.offCount(),1);f.player.destroy();
});

test('whole-object SHA refusal withdraws the preceding image and subscription without decode or retry',async()=>{
  const f=fixture();f.player.start();await f.complete('A');f.push(envelope('B'));await settle();
  f.reads.at(-1).reject(Object.assign(Error('den_lab_frame_media_sha256_mismatch'),{code:'den_lab_frame_media_sha256_mismatch'}));await settle();
  assert.equal(f.player.snapshot().connection,'refused');assert.equal(f.player.snapshot().reason,'den_lab_frame_media_sha256_mismatch');
  assert.equal(f.player.snapshot().view,null);assert.equal(f.player.snapshot().imageUrl,null);assert.equal(f.offCount(),1);
  assert.deepEqual(f.released,['A']);assert.equal(f.decodes.length,1);const count=f.reads.length;
  f.push(envelope('B'));await settle();assert.equal(f.reads.length,count);f.player.destroy();
});

test('recoverable observer failure keeps original metadata and subscription, then resumes exact frames',async()=>{
  const f=fixture();f.player.start();await f.complete();
  f.status({status:'unavailable',reason:'den_lab_read_timeout',recoverable:true});
  assert.equal(f.player.snapshot().connection,'unavailable');assert.equal(f.player.snapshot().view.follow.allowed,false);
  assert.equal(f.player.snapshot().view.models[0].result.activity,'reading');assert.equal(f.offCount(),0);
  f.push(envelope('B'));await settle();await f.media();f.decodes.at(-1).resolve(f.owned('B'));await settle();
  assert.equal(f.player.snapshot().connection,'live');assert.equal(f.player.snapshot().imageUrl,'blob:B');
  assert.equal(f.player.snapshot().view.pose2d.association,'same-frame');f.player.destroy();
});
test('default browser decoder releases an aborted URL before its decode promise settles',async()=>{
  const f=browserFixture();f.player.start();f.push(envelope());await settle();await f.media();
  f.player.stop('owner_disconnect');assert.deepEqual(f.revoked,['blob:browser-0']);
  f.images[0].wait.resolve();await settle();assert.equal(f.player.snapshot().imageUrl,null);
  assert.deepEqual(f.revoked,['blob:browser-0']);f.player.destroy();
});
