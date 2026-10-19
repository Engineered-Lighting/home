// Actual engine factory, renderer, Cloud/Photo/Mesh and authored room assets.
// Public source allowlist + generated fixture pack only; no household files.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import Core from '../app/src/home-equipment-registration.js';
import { createEquipmentRegistrationGateway, initRegistration, readActiveBinding } from '../web-gateway/equipment-registration.mjs';
import { installPack, authoredDocument } from './fixtures/apartment-home-shell-fixtures.mjs';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'), SRC = path.join(ROOT, 'app/src');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const phase=process.env.HOME_APARTMENT_OVERLAY_PROOF_PHASE||'candidate';
assert.ok(['candidate','baseline-before','baseline-after'].includes(phase),'only explicit baseline or candidate benchmark phases are allowed');
const profile=process.env.HOME_APARTMENT_OVERLAY_PROOF_PROFILE||'functional';
assert.ok(['functional','desktop-benchmark'].includes(profile),'only functional or desktop-benchmark resource profiles are allowed');
assert.ok(phase==='candidate'||profile==='desktop-benchmark','source-paired baseline phases require the explicit desktop-benchmark profile');
const viewport=profile==='functional'?{width:390,height:844}:{width:1366,height:768};
const deviceScaleFactor=profile==='functional'?0.5:1;
const baselineCommit='557164592f916cc4f93b79214f560d24d6f797b6';
const baselineEngine=phase==='candidate'?null:execFileSync('git',['show',baselineCommit+':app/src/home-3d/engine.js'],{cwd:ROOT,maxBuffer:2*1024*1024});

// Same fenced inventory policy as the retained full-shell proof. Never resolve
// request paths directly against the filesystem or serve runtime data trees.
function publicSources(root) {
  const result = new Map(), excluded = new Set(['node_modules','data','artifacts','private','recordings','logs']);
  assert.ok(fs.lstatSync(root).isDirectory() && !fs.lstatSync(root).isSymbolicLink());
  let count = 0, bytes = 0;
  function visit(directory, depth = 0) {
    assert.ok(depth <= 12);
    for (const entry of fs.readdirSync(directory,{withFileTypes:true})) {
      assert.ok(++count <= 10000);
      if(entry.name.startsWith('.') || excluded.has(entry.name))continue;
      const file=path.join(directory,entry.name), relative=path.relative(root,file).split(path.sep).join('/'), stat=fs.lstatSync(file);
      if(relative==='assets/apartment'||relative.startsWith('assets/apartment/')||stat.isSymbolicLink())continue;
      if(stat.isDirectory()){visit(file,depth+1);continue;}if(!stat.isFile())continue;
      const vendor=/^(?:vendor|fonts|icons)\//.test(relative)&&/\.(?:wasm|woff2?|ttf|otf|png|jpe?g|webp|svg|ico|ktx2|json)$/.test(entry.name);
      const rootAsset=directory===root&&['index.html','favicon.svg','favicon.ico','favicon-32.png','apple-touch-icon.png','site.webmanifest'].includes(entry.name);
      if(!/\.(?:js|mjs|jsx|css)$/.test(entry.name)&&!vendor&&!rootAsset)continue;
      bytes+=stat.size;assert.ok(stat.size<=32*1024*1024&&bytes<=256*1024*1024);
      result.set('/'+relative,{file,relative});
    }
  }
  visit(root);return result;
}

function browserOptions(){
  const installed=fs.existsSync(chromium.executablePath());
  assert.ok(installed||process.platform==='win32','pinned Chromium must exist on Linux');
  const args=['--disable-background-networking','--renderer-process-limit=2','--js-flags=--max-old-space-size=512'];
  if(process.platform==='win32')args.unshift('--use-angle=d3d11');
  else if(process.platform==='linux')args.unshift('--use-gl=angle','--use-angle=swiftshader-webgl','--enable-unsafe-swiftshader');
  if(profile==='functional')args.push('--disable-frame-rate-limit');
  return {headless:true,args,...(process.platform==='linux'?{channel:'chromium'}:!installed?{channel:'msedge'}:{})};
}
const median=values=>{const sorted=[...values].sort((a,b)=>a-b);return sorted.length%2?sorted[(sorted.length-1)/2]:(sorted[sorted.length/2-1]+sorted[sorted.length/2])/2;};
function noiseComparison(before,active,after,key){
  const windows=rows=>[median(rows.slice(0,2).map(row=>row[key])),median(rows.slice(2,4).map(row=>row[key]))];
  const baseline=[...windows(before),...windows(after)],overlay=windows(active),min=Math.min(...baseline),max=Math.max(...baseline);
  return {metric:key,baseline_window_medians:baseline,active_window_medians:overlay,baseline_range:[min,max],
    baseline_spread:max-min,paired_delta:median(overlay)-median(baseline),
    interpretation:overlay.every(value=>value>=min&&value<=max)?'within-observed-baseline-variability':'requires-review'};
}

test('real renderer overlay bridge stays canonical across modes, lifetime races and paired performance windows',{timeout:180000},async()=>{
  assert.ok(!process.env.HOME_APARTMENT_CANDIDATE_ROOT,'this proof uses only current worktree source');
  const run=path.join(ROOT,'.tmp/apartment-overlay-bridge-browser','run-'+Date.now()+'-'+phase);fs.mkdirSync(run,{recursive:true});
  const packs=path.join(run,'packs'),privateRoot=path.join(run,'private'),echo=installPack(packs,'echo'),victoria=installPack(packs,'victoria');
  fs.writeFileSync(path.join(packs,'index.json'),JSON.stringify({schemaVersion:1,packs:[echo,victoria].map(item=>({manifest:item.manifest,active:true,baseUrl:item.base}))}));
  initRegistration(privateRoot,authoredDocument(readActiveBinding(packs),Core),{contentPacksDir:packs});
  const protectedFiles=[echo,victoria].flatMap(item=>Object.keys(item.files).map(name=>path.join(packs,item.site,item.revision,name)));
  protectedFiles.push(path.join(packs,'echo/ACTIVE'),path.join(packs,'victoria/ACTIVE'),path.join(packs,'index.json'));
  const protectedHashes=Object.fromEntries(protectedFiles.map(file=>[file,hash(fs.readFileSync(file))]));
  const sources=publicSources(SRC),receipt={schema:'home-apartment-overlay-bridge-authored-browser-1',authored_room:true,real_renderer:true,
    household_data:false,physical_commands:0,camera_sessions:0,model_sessions:0,survey_writes:0,screenshots:false,
    source_phase:phase,proof_profile:profile,proof_pacing:profile==='functional'?'unthrottled-real-raf':'browser-default',configured_viewport:viewport,configured_device_scale_factor:deviceScaleFactor,
    performance_scope:profile==='functional'?'functional-viewport-capacity-only':'desktop-benchmark-requires-paired-review',
    engine_source:baselineEngine?{mode:'public-baseline-engine-only',commit:baselineCommit,sha256:hash(baselineEngine),other_sources:'current-worktree'}
      :{mode:'candidate-worktree',sha256:hash(fs.readFileSync(path.join(SRC,'home-3d/engine.js')))},
    checks:[],performance:[],warmups:[],checkpoints:[],timing_windows:[],requests:[],forbidden:[],missing_code:[],errors:[],served:{},browser:browserOptions(),startup:{checkpoints:[],responses:[]}};
  const proofStarted=Date.now(),markStartup=name=>receipt.startup.checkpoints.push({name,elapsed_ms:Date.now()-proofStarted});
  const checkpoint=(name,detail={})=>{receipt.current_phase=name;receipt.checkpoints.push({name,elapsed_ms:Date.now()-proofStarted,...detail});};
  const recordWindow=(mode,condition,rows)=>{receipt.timing_windows.push({mode,condition,elapsed_ms:Date.now()-proofStarted,rows});checkpoint(mode+' '+condition+' stats complete');};
  let server,browser,context,page,origin,handler,timer,releaseLeafRequest;
  const deadline=new Promise((_,reject)=>{timer=setTimeout(()=>{browser?.close().catch(()=>{});server?.closeAllConnections();reject(Error('145s overlay bridge browser deadline'));},145000)});
  async function proof(){
    server=http.createServer((req,res)=>{
      try{
        const url=new URL(req.url,'http://localhost');receipt.requests.push({path:url.pathname,method:req.method});
        if(!['GET','HEAD'].includes(req.method)||/\/api\/services\/|\/capture(?:\/|$)|\/acquisition(?:\/|$)|\/control(?:\/|$)|\/sessions\/[^/]+\/(?:start|stop)(?:\/|$)/.test(url.pathname)){
          receipt.forbidden.push({path:url.pathname,method:req.method});res.writeHead(403);res.end();return;
        }
        if(url.pathname.startsWith('/api/apartment/v1/'))return handler(req,res);
        let body,type='application/octet-stream';
        if(url.pathname.startsWith('/api/perception-lab/v1/')){
          const route=url.pathname.slice('/api/perception-lab/v1/'.length),value=route==='capabilities'?{site_id:'victoria',cameras:[],live_previews:{},live_videos:{},recording_allowed:false,experiments:[],apartment_viewing:{provisioned:false}}
            :route==='sessions'?{site_id:'victoria',version:1,items:[]}:route==='recordings'?{items:[]}:null;
          if(value){body=Buffer.from(JSON.stringify(value));type='application/json';}
        }
        if(url.pathname==='/content-packs/index.json'){body=fs.readFileSync(path.join(packs,'index.json'));type='application/json';}
        else{const pack=[echo,victoria].find(item=>url.pathname.startsWith(item.base));if(pack){body=pack.files[url.pathname.slice(pack.base.length)];if(url.pathname.endsWith('.json'))type='application/json';}}
        if(!body&&url.pathname.startsWith('/proxy/')){const value=url.pathname.endsWith('/model')||url.pathname.endsWith('/apartment_model')?echo.seed:url.pathname.endsWith('/api/states')?[]:url.pathname.endsWith('/api/config')?{unit_system:{length:'m'}}:{};body=Buffer.from(JSON.stringify(value));type='application/json';}
        const entry=sources.get(url.pathname==='/'?'/index.html':url.pathname);
        if(!body&&entry){const relative=path.relative(fs.realpathSync(SRC),fs.realpathSync(entry.file)).split(path.sep).join('/');assert.equal(relative,entry.relative);assert.ok(!relative.startsWith('../')&&!path.isAbsolute(relative));
          body=relative==='home-3d/engine.js'&&baselineEngine?baselineEngine:fs.readFileSync(entry.file);
          receipt.served[relative]={sha256:hash(body),source:relative==='home-3d/engine.js'&&baselineEngine?'public-baseline-commit':'worktree',...(relative==='home-3d/engine.js'&&baselineEngine?{commit:baselineCommit}:{})};
          type=/\.(?:js|mjs|jsx)$/.test(entry.file)?'text/javascript':entry.file.endsWith('.html')?'text/html':entry.file.endsWith('.css')?'text/css':/\.(?:json|webmanifest)$/.test(entry.file)?'application/json':entry.file.endsWith('.svg')?'image/svg+xml':type;
        }
        if(!body){if(/\.(?:js|mjs|jsx|css|wasm)$/.test(url.pathname))receipt.missing_code.push(url.pathname);res.writeHead(404);res.end();return;}
        res.writeHead(200,{'Content-Type':type,'Content-Length':body.length,'Cache-Control':'no-store'});res.end(req.method==='HEAD'?undefined:body);
      }catch(error){receipt.errors.push('authored HTTP handler: '+error.message);res.writeHead(500);res.end();}
    });
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin='http://127.0.0.1:'+server.address().port;
    handler=createEquipmentRegistrationGateway({env:{NODE_ENV:'test',HOME_WEB_EQUIPMENT_REGISTRATION_ENABLED:'1',HOME_WEB_EQUIPMENT_REGISTRATION_ROOT:privateRoot,HOME_WEB_EQUIPMENT_REGISTRATION_ORIGIN:origin,HOME_CONTENT_PACKS_DIR:packs},authenticate:req=>req.headers.cookie?.includes('authored_owner_session=1')===true});
    browser=await chromium.launch(receipt.browser);context=await browser.newContext({viewport,deviceScaleFactor,serviceWorkers:'block'});
    await context.addCookies([{name:'authored_owner_session',value:'1',url:origin}]);
    await context.addInitScript(()=>{localStorage.setItem('apartment3d.site','echo');localStorage.setItem('home.perf.backgroundWarmup','off');window.HG_DISABLE_SERVICE_WORKER=true;});
    let notifyLeafRequest;const firstLeafRequest=new Promise(resolve=>{notifyLeafRequest=resolve});let blockedOnce=false;
    await context.route('**/*',async route=>{
      const request=route.request(),url=new URL(request.url());if(['data:','blob:'].includes(url.protocol))return route.continue();
      if(url.origin!==origin){const harmless=['127.0.0.1','localhost','[::1]'].includes(url.hostname)&&request.method()==='GET'&&url.pathname==='/healthz';if(!harmless)receipt.forbidden.push({origin:url.origin,path:url.pathname,method:request.method()});return route.abort();}
      if(url.pathname==='/home-3d/apartment-ui-overlay-bridge.mjs'&&!blockedOnce){blockedOnce=true;notifyLeafRequest();await new Promise(resolve=>{releaseLeafRequest=resolve});}
      return route.continue();
    });
    await context.routeWebSocket('**/*',socket=>socket.close());
    page=await context.newPage();page.setDefaultTimeout(15000);page.on('pageerror',error=>receipt.errors.push(error.message));
    if(profile==='functional')await page.exposeFunction('__recordOverlayWarmup',event=>{
      checkpoint(event.mode+' '+event.stage+' '+event.kind,{id:event.id,observed_rafs:event.observed_rafs,renderer_frames:event.renderer_frames});
      receipt.warmup_progress=event;
      if(event.kind==='complete')receipt.warmups.push(event.warmup);
    });
    page.on('response',response=>{const pathname=new URL(response.url()).pathname;if(['/home-app.jsx','/home-mount.jsx','/home-apartment.jsx','/auth/status','/content-packs/index.json'].includes(pathname))receipt.startup.responses.push({path:pathname,status:response.status(),elapsed_ms:Date.now()-proofStarted});});
    markStartup('navigation-start');
    await page.goto(origin+'/?apartmentRedesign=1&worldZoom=off',{waitUntil:'domcontentloaded'});
    markStartup('dom-content-loaded');
    // Match the retained full-shell proof: cold Babel/app boot is a separate
    // precondition; each actual room load still has the original 15s budget.
    await page.locator('.apt-home-shell[data-home-layout="unified"]').waitFor({timeout:45000});
    markStartup('unified-shell-ready');
    await page.waitForFunction(()=>window.__HOME_APARTMENT_SITE_API?.readback().phase==='ready'&&window.__APT_ENGINE?.siteId==='echo');
    markStartup('echo-ready');
    await page.getByRole('group',{name:'Home',exact:true}).getByRole('button',{name:'Victoria',exact:true}).click();
    await page.waitForFunction(()=>window.__HOME_APARTMENT_SITE_API?.readback().phase==='ready'&&window.__APT_ENGINE?.siteId==='victoria');
    markStartup('victoria-ready');
    receipt.graphics=await page.evaluate(()=>{const engine=window.__APT_ENGINE,renderer=engine.renderer,gl=renderer.getContext(),debug=gl.getExtension('WEBGL_debug_renderer_info');return {gpu:engine.gpu,webglVendor:gl.getParameter(gl.VENDOR),webglRenderer:gl.getParameter(gl.RENDERER),unmaskedVendor:debug?gl.getParameter(debug.UNMASKED_VENDOR_WEBGL):null,pixelRatio:renderer.getPixelRatio(),devicePixelRatio,canvas:[renderer.domElement.width,renderer.domElement.height],cssCanvas:[renderer.domElement.clientWidth,renderer.domElement.clientHeight],contextLost:gl.isContextLost(),viewport:[innerWidth,innerHeight]}});
    assert.equal(receipt.graphics.contextLost,false);
    assert.deepEqual(receipt.graphics.viewport,[viewport.width,viewport.height]);
    assert.equal(receipt.graphics.devicePixelRatio,deviceScaleFactor);assert.equal(receipt.graphics.pixelRatio,deviceScaleFactor);
    assert.ok(receipt.graphics.cssCanvas.every(value=>value>0));
    assert.deepEqual(receipt.graphics.canvas,receipt.graphics.cssCanvas.map(value=>Math.floor(value*deviceScaleFactor)));
    receipt.browser_version=browser.version();
    if(phase==='candidate')assert.equal(await page.evaluate(()=>typeof window.__APT_ENGINE.createUiOverlayLayer),'function');
    const check=async(name,operation)=>{checkpoint(name+' start');const result=await operation();receipt.checks.push({name,...result});checkpoint(name+' complete');return result;};
    await page.evaluate(()=>{
      const engine=window.__APT_ENGINE;window.__overlayBridgeProof={engine,identity:engine.sceneIdentity,host:engine.picking.getHost(),point:[.6,.9,2.3]};
      if(!engine.createUiOverlayLayer)return;
      const controller=new AbortController();window.__overlayBridgeLate={controller};
      engine.createUiOverlayLayer({host:engine.picking.getHost(),signal:controller.signal,isCurrent:()=>true}).then(value=>{window.__overlayBridgeLate.result=value;window.__overlayBridgeLate.settled=true});
    });
    if(phase==='candidate'){
      await Promise.race([firstLeafRequest,new Promise((_,reject)=>setTimeout(()=>reject(Error('leaf lazy-import request did not arrive')),10000))]);
      await page.evaluate(()=>window.__overlayBridgeLate.controller.abort());releaseLeafRequest();releaseLeafRequest=null;
      await page.waitForFunction(()=>window.__overlayBridgeLate.settled);
      await check('late lazy import after consumer abort attaches no DOM or scene group',async()=>{
      const result=await page.evaluate(()=>({isNull:window.__overlayBridgeLate.result===null,labels:document.querySelectorAll('.apt-ui-overlay-layer').length,groups:window.__APT_ENGINE.overlay.overlayRoot.children.filter(child=>child.name==='apartment-ui-overlay-layer').length}));
      assert.deepEqual(result,{isNull:true,labels:0,groups:0});return result;
      });
      await check('factory admission refuses missing authorities, wrong host and retired owner bindings',async()=>{
        const result=await page.evaluate(async()=>{
          const p=window.__overlayBridgeProof,engine=p.engine,make=extra=>({host:p.host,signal:new AbortController().signal,isCurrent:()=>true,...extra}),out={};
          out.missingSignal=await engine.createUiOverlayLayer(make({signal:undefined}))===null;
          out.missingPredicate=await engine.createUiOverlayLayer(make({isCurrent:undefined}))===null;
          out.missingHost=await engine.createUiOverlayLayer(make({host:undefined}))===null;
          const wrong=document.createElement('div');document.body.append(wrong);try{out.wrongHost=await engine.createUiOverlayLayer(make({host:wrong}))===null}finally{wrong.remove()}
          const aborted=new AbortController();aborted.abort();out.aborted=await engine.createUiOverlayLayer(make({signal:aborted.signal}))===null;
          out.falsePredicate=await engine.createUiOverlayLayer(make({isCurrent:()=>false}))===null;
          out.throwingPredicate=await engine.createUiOverlayLayer(make({isCurrent:()=>{throw Error('Authored predicate refusal')}}))===null;
          const originals={viewport:window.HomeSpatialViewport,packs:window.HomeContentPacks,shadow:window.HomeLiveSceneShadow,identity:engine.sceneIdentity};
          try{
            window.HomeSpatialViewport={...originals.viewport,getSelectedSiteId:()=> 'echo'};out.selectedSite=await engine.createUiOverlayLayer(make())===null;window.HomeSpatialViewport=originals.viewport;
            for(const mismatch of ['revision','registrationVersion','frame','collision']){
              window.HomeContentPacks={...originals.packs,get:(...args)=>{
                const value=originals.packs.get(...args);if(args[0]!==engine.siteId||!value)return value;
                const manifest={...value.manifest,assets:{...value.manifest.assets}};
                if(mismatch==='revision')manifest.revision+='-retired';
                else if(mismatch==='registrationVersion')manifest.registrationVersion++;
                else {const asset=mismatch==='frame'?'frame.json':'collision.glb';manifest.assets[asset]={...manifest.assets[asset],sha256:'authored-mismatched-'+mismatch};}
                return {...value,manifest};
              }};out[mismatch]=await engine.createUiOverlayLayer(make())===null;
            }
            window.HomeContentPacks=originals.packs;
            window.HomeLiveSceneShadow={active:true};out.shadow=await engine.createUiOverlayLayer(make())===null;window.HomeLiveSceneShadow=originals.shadow;
            engine.sceneIdentity={...originals.identity,frameDigest:'authored-retired-identity'};out.sceneIdentity=await engine.createUiOverlayLayer(make())===null;
          }finally{window.HomeSpatialViewport=originals.viewport;window.HomeContentPacks=originals.packs;window.HomeLiveSceneShadow=originals.shadow;engine.sceneIdentity=originals.identity;}
          out.noAttachment=p.host.querySelectorAll('.apt-ui-overlay-layer').length===0&&engine.overlay.overlayRoot.children.every(child=>child.name!=='apartment-ui-overlay-layer');
          return out;
        });for(const [key,value]of Object.entries(result))assert.equal(value,true,key+' refuses admission');return result;
      });
    }
    await page.evaluate(functional=>{
      const p=window.__overlayBridgeProof;
      p.warmups=[];
      p.warm=(stage='warm')=>new Promise((resolve,reject)=>{
        const started=performance.now(),firstFrame=p.engine.renderer.info.render.frame,stats=[];
        const id=p.warmups.length+1,mode=p.engine.modes.mode;
        let ticks=0,last=started,maxGap=0,raf,settled=false,gpuCompletion=null;
        const report=(kind,detail={})=>{if(functional)window.__recordOverlayWarmup({kind,id,mode,stage,observed_rafs:ticks,renderer_frames:p.engine.renderer.info.render.frame-firstFrame,...detail}).catch(()=>{});};
        const off=p.engine.on('stats',value=>{stats.push({p95:value.p95,renderP95:value.renderP95,fps:value.fps,calls:value.calls,tris:value.tris});if(stats.length>4)stats.shift()});
        const finish=result=>{
          if(settled)return;settled=true;clearTimeout(timeout);cancelAnimationFrame(raf);off();
          const warmup={id,stage,result,target_rafs:120,observed_rafs:ticks,renderer_frames:p.engine.renderer.info.render.frame-firstFrame,
            elapsed_ms:performance.now()-started,max_raf_gap_ms:maxGap,visibility:document.visibilityState,mode:p.engine.modes.mode,
            canvas:[p.engine.renderer.domElement.width,p.engine.renderer.domElement.height],performance:p.engine.performanceDiagnostics(),gpu_completion:gpuCompletion,recent_stats:stats};
          p.warmups.push(warmup);report('complete',{warmup});
          if(result==='passed')resolve();else reject(Error(result==='context-lost'?'GPU context lost during warmup':'120-frame warmup deadline'));
        };
        const timeout=setTimeout(()=>finish('deadline'),25000);
        const step=()=>{
          const now=performance.now();maxGap=Math.max(maxGap,now-last);last=now;++ticks;
          if(ticks%30===0)report('progress');
          if(ticks>=120){
            if(functional){
              const gl=p.engine.renderer.getContext(),barrierStarted=performance.now(),lostBefore=gl.isContextLost();
              if(!lostBefore)gl.finish();
              gpuCompletion={method:'finish',barrier_ms:performance.now()-barrierStarted,context_lost_before:lostBefore,context_lost_after:gl.isContextLost()};
              if(lostBefore||gpuCompletion.context_lost_after){finish('context-lost');return;}
              if(performance.now()-started>=25000){finish('deadline');return;}
            }
            finish('passed');
          }else raf=requestAnimationFrame(step);
        };
        report('start');
        raf=requestAnimationFrame(step);
      });
      p.stats=()=>new Promise((resolve,reject)=>{const rows=[],timeout=setTimeout(()=>{off();reject(Error('production stats deadline'))},10000);const off=p.engine.on('stats',value=>{rows.push({p95:value.p95,renderP95:value.renderP95,fps:value.fps,pixelRatio:value.pixelRatio,calls:value.calls,tris:value.tris,quality:value.performance.qualityLevel,performanceMode:value.performance.mode,...(p.expectActive?{dotDraws:p.timedDraws,labelHidden:p.label.hidden,dotVisible:p.layer.group.children[0]?.visible,layerAttached:!!p.layer.group.parent,projection:p.layer.project(p.point)}:{})});if(rows.length===4){clearTimeout(timeout);off();resolve(rows)}})});
    },profile==='functional');
    for(const mode of ['points','splat','mesh']){
      checkpoint(mode+' mode and empty warm start');
      await page.evaluate(async mode=>{const p=window.__overlayBridgeProof;p.engine.setPerformanceMode('maxFidelity');p.engine.rig.snapHome(0);await p.engine.modes.setMode(mode);await p.warm('empty')},mode);
      const readiness=await page.evaluate(async()=>{const p=window.__overlayBridgeProof,points=await p.engine.pointsPromise;return {mode:p.engine.modes.mode,debug:p.engine.modes.debugInfo(),points:{count:points.geometry.attributes.position.count,visible:points.visible}}});
      (receipt.mode_readiness ||= []).push(readiness);
      if(mode==='points'){assert.ok(readiness.points.count>0);assert.equal(readiness.points.visible,true);}
      if(mode==='splat'){assert.equal(readiness.debug.loadStatus.splat,'loaded');assert.equal(readiness.debug.splat,true);assert.equal(readiness.debug.splatVisible,true);assert.equal(readiness.debug.sparkVisible,true);assert.ok(readiness.debug.splatSource);assert.ok(readiness.debug.numSplats>0);}
      if(mode==='mesh'){assert.equal(readiness.debug.loadStatus.mesh,'loaded');assert.equal(readiness.debug.meshFallback,false);assert.ok(readiness.debug.meshSource?.endsWith('mesh.glb'));}
      checkpoint(mode+' before stats start');
      const before=await page.evaluate(()=>window.__overlayBridgeProof.stats());recordWindow(mode,'before',before);
      if(phase!=='candidate'){
        for(const row of before){assert.ok(Number.isFinite(row.p95)&&row.p95>0&&Number.isFinite(row.renderP95)&&row.renderP95>=0);assert.equal(row.performanceMode,'maxFidelity');assert.equal(row.quality,'maximum');assert.equal(row.pixelRatio,deviceScaleFactor);}
        receipt.performance.push({mode,orbit:'snapHome(0)',quality:'maxFidelity',warmup_renderer_ticks:120,empty:before});
        continue;
      }
      await check(mode+' dot and label follow canonical fixture while orbiting',async()=>{
        const result=await page.evaluate(async()=>{
          const p=window.__overlayBridgeProof,controller=new AbortController();p.controller=controller;
          p.layer=await p.engine.createUiOverlayLayer({host:p.host,signal:controller.signal,isCurrent:()=>window.__APT_ENGINE===p.engine&&p.engine.sceneIdentity===p.identity});
          p.anchor=p.layer.addAnchor({point_m:p.point,label:'Authored fixture overlay'});p.label=p.host.querySelector('.apt-ui-overlay-label');
          const originalLabel=p.label;p.anchor.update({label:'Updated authored label'});const anchorIdentity=originalLabel===p.host.querySelector('.apt-ui-overlay-label')&&originalLabel.textContent==='Updated authored label';p.anchor.update({label:'Authored fixture overlay'});
          const dot=p.layer.group.children[0],originalBeforeRender=dot.onBeforeRender;let draws=0;dot.onBeforeRender=function(...args){draws++;originalBeforeRender?.apply(this,args)};
          const first=p.layer.project(p.point);p.engine.rig.stepAzimuth(1);await p.warm('orbit');dot.onBeforeRender=originalBeforeRender;
          const ancestry=[];for(let parent=dot;parent;parent=parent.parent)ancestry.push({type:parent.type,name:parent.name,visible:parent.visible,layers:parent.layers.mask});
          const world=dot.getWorldPosition(dot.position.clone()),view=world.clone().applyMatrix4(p.engine.camera.matrixWorldInverse),clip=world.clone().project(p.engine.camera);
          const draw={draws,ancestry,world:world.toArray(),view:view.toArray(),clip:clip.toArray(),cameraLayers:p.engine.camera.layers.mask,frustumCulled:dot.frustumCulled,materialVisible:dot.material.visible,materialOpacity:dot.material.opacity};
          const last=p.layer.project(p.point),label=p.label;return {first,last,anchorIdentity,draw,label:{x:parseFloat(label.style.left),y:parseFloat(label.style.top),hidden:label.hidden,text:label.textContent},position:p.layer.group.children[0].position.toArray(),mode:p.engine.modes.mode,hostUnchanged:p.engine.picking.getHost()===p.host,engineUnchanged:window.__APT_ENGINE===p.engine};
        });
        (receipt.overlay_draw_diagnostics ||= []).push({mode,...result.draw});
        assert.ok(result.first&&result.last);assert.ok(Math.hypot(result.last.x-result.first.x,result.last.y-result.first.y)>0);
        assert.ok(result.draw.draws>0,'the actual canonical dot must reach the real renderer, not only its DOM label');
        assert.equal(result.label.hidden,false);assert.equal(result.label.text,'Authored fixture overlay');assert.deepEqual(result.position,[.6,.9,2.3]);
        assert.equal(result.anchorIdentity,true);
        assert.ok(Math.abs(result.label.x-result.last.x)<.01&&Math.abs(result.label.y-result.last.y)<.01);assert.equal(result.mode,mode);assert.equal(result.hostUnchanged,true);assert.equal(result.engineUnchanged,true);
        return result;
      });
      // Return to the exact same orbit used by the baseline before collecting
      // active samples. Warmup RAFs finish before production stats sampling.
      await page.evaluate(async()=>{const p=window.__overlayBridgeProof;p.engine.rig.snapHome(0);await p.warm('active')});
      await page.evaluate(()=>{const p=window.__overlayBridgeProof,dot=p.layer.group.children[0];p.timedDraws=0;p.expectActive=true;p.timedDrawOriginal=dot.onBeforeRender;dot.onBeforeRender=function(...args){p.timedDraws++;p.timedDrawOriginal?.apply(this,args)}});
      checkpoint(mode+' active stats start');
      const active=await page.evaluate(()=>window.__overlayBridgeProof.stats());recordWindow(mode,'active',active);
      (receipt.timed_draw_diagnostics ||= []).push({mode,active});
      assert.ok(active.every(row=>row.dotDraws>0&&row.dotVisible===true&&row.labelHidden===false&&row.layerAttached===true&&row.projection),'active timing must include the real dot and label');
      await page.evaluate(()=>{const p=window.__overlayBridgeProof;p.layer.group.children[0].onBeforeRender=p.timedDrawOriginal;p.expectActive=false});
      await page.evaluate(async()=>{const p=window.__overlayBridgeProof;p.controller.abort();await p.warm('after')});
      checkpoint(mode+' after stats start');
      const after=await page.evaluate(()=>window.__overlayBridgeProof.stats());recordWindow(mode,'after',after);
      for(const rows of [before,active,after])for(const row of rows){assert.ok(Number.isFinite(row.p95)&&row.p95>0&&Number.isFinite(row.renderP95)&&row.renderP95>=0);assert.equal(row.performanceMode,'maxFidelity');assert.equal(row.quality,'maximum');assert.equal(row.pixelRatio,deviceScaleFactor);}
      receipt.performance.push({mode,orbit:'snapHome(0)',quality:'maxFidelity',warmup_renderer_ticks:120,before,active,after,
        comparisons:['p95','renderP95'].map(key=>noiseComparison(before,active,after,key))});
    }
    if(phase!=='candidate'){
      await page.evaluate(()=>window.__overlayBridgeProof.engine.dispose());
      assert.deepEqual(receipt.forbidden,[]);assert.deepEqual(receipt.missing_code,[]);assert.deepEqual(receipt.errors,[]);
      assert.equal(receipt.served['home-3d/engine.js'].sha256,hash(baselineEngine));
      receipt.functional_result='not-run-baseline-engine-source';receipt.performance_result='baseline-samples-recorded';
      return;
    }
    await check('canonical scan pick returns exact point, normal and native surface name',async()=>{
      const result=await page.evaluate(async()=>{
        const p=window.__overlayBridgeProof;await p.engine.modes.preload(['collision']);const controller=new AbortController();
        const layer=await p.engine.createUiOverlayLayer({host:p.host,signal:controller.signal,isCurrent:()=>true}),rect=p.host.getBoundingClientRect();
        let result=null;for(const point of [[2,2,0],[0,2,1],[2,4,1]]){const screen=layer.project(point);if(!screen)continue;const x=rect.left+screen.x*rect.width/p.host.clientWidth,y=rect.top+screen.y*rect.height/p.host.clientHeight,expected=p.engine.displaySurfaceHit(x,y),actual=layer.pick(x,y);if(expected&&actual){result={expected:{point:expected.point,normal:expected.normal,surfaceName:expected.object.name?.trim()||'Scan surface'},actual};break;}}controller.abort();return result;
      });assert.ok(result);assert.deepEqual(result.actual,result.expected);return result;
    });
    await check('paused consumer abort detaches synchronously; host replacement retires without retarget',async()=>{
      const result=await page.evaluate(async()=>{
        const p=window.__overlayBridgeProof;p.engine.setRunning(false);const frame=p.engine.renderer.info.render.frame,controller=new AbortController();
        const layer=await p.engine.createUiOverlayLayer({host:p.host,signal:controller.signal,isCurrent:()=>true});
        layer.addAnchor({point_m:p.point,label:'Paused authored label'});controller.abort();const abort={labels:p.host.querySelectorAll('.apt-ui-overlay-layer').length,parent:layer.group.parent===null,framesUnchanged:p.engine.renderer.info.render.frame===frame};
        const secondController=new AbortController(),second=await p.engine.createUiOverlayLayer({host:p.host,signal:secondController.signal,isCurrent:()=>true});second.addAnchor({point_m:p.point,label:'Old host only'});
        const replacement=document.createElement('div');replacement.style.cssText='position:fixed;width:100px;height:100px';document.body.append(replacement);
        let host;try{p.engine.picking.setHost(replacement);host={updated:second.update(),retired:second.group.parent===null,replacementLabels:replacement.querySelectorAll('.apt-ui-overlay-layer').length,oldLabels:p.host.querySelectorAll('.apt-ui-overlay-layer').length}}finally{p.engine.picking.setHost(p.host);secondController.abort();replacement.remove();p.engine.setRunning(true)}return {abort,host};
      });assert.deepEqual(result.abort,{labels:0,parent:true,framesUnchanged:true});assert.deepEqual(result.host,{updated:false,retired:true,replacementLabels:0,oldLabels:0});return result;
    });
    await check('throwing owner predicate retires locally and subsequent frames continue',async()=>{
      const result=await page.evaluate(async()=>{const p=window.__overlayBridgeProof,controller=new AbortController();let fail=false;const layer=await p.engine.createUiOverlayLayer({host:p.host,signal:controller.signal,isCurrent:()=>{if(fail)throw Error('Authored owner error');return true}});layer.addAnchor({point_m:p.point,label:'Owner failure'});fail=true;const projected=layer.project(p.point),frame=p.engine.renderer.info.render.frame;await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));return {projected,retired:layer.group.parent===null,rendererAdvanced:p.engine.renderer.info.render.frame>frame}});
      assert.deepEqual(result,{projected:null,retired:true,rendererAdvanced:true});return result;
    });
    await check('engine lifetime disposal clears layers first and leaves borrowed resources with their owner',async()=>{
      const result=await page.evaluate(async()=>{const p=window.__overlayBridgeProof,THREE=await import('/vendor/three-0.180.0/three.module.js'),controller=new AbortController();let geometryDisposals=0,materialDisposals=0;
        const layer=await p.engine.createUiOverlayLayer({host:p.host,signal:controller.signal,isCurrent:()=>true}),geometry=new THREE.BoxGeometry(.1,.1,.1),material=new THREE.MeshBasicMaterial();geometry.addEventListener('dispose',()=>geometryDisposals++);material.addEventListener('dispose',()=>materialDisposals++);layer.group.add(new THREE.Mesh(geometry,material));layer.addAnchor({point_m:p.point,label:'Engine lifetime'});
        p.engine.dispose();const value={labels:document.querySelectorAll('.apt-ui-overlay-layer').length,retired:layer.group.parent===null,geometryDisposals,materialDisposals,refused:layer.project(p.point)===null};geometry.dispose();material.dispose();return value;});
      assert.deepEqual(result,{labels:0,retired:true,geometryDisposals:0,materialDisposals:0,refused:true});return result;
    });
    for(const [file,expected] of Object.entries(protectedHashes))assert.equal(hash(fs.readFileSync(file)),expected,'authored pack remains unchanged');
    assert.deepEqual(receipt.forbidden,[]);assert.deepEqual(receipt.missing_code,[]);assert.deepEqual(receipt.errors,[]);
    const warmups=await page.evaluate(()=>window.__overlayBridgeProof.warmups);
    assert.equal(receipt.checks.length,9);assert.equal(warmups.length,12);
    for(const warmup of warmups){assert.equal(warmup.result,'passed');assert.equal(warmup.observed_rafs,120);assert.ok(warmup.renderer_frames>=120);assert.equal(warmup.performance.mode,'maxFidelity');assert.equal(warmup.performance.qualityLevel,'maximum');if(profile==='functional'){assert.equal(warmup.gpu_completion.method,'finish');assert.equal(warmup.gpu_completion.context_lost_before,false);assert.equal(warmup.gpu_completion.context_lost_after,false);assert.ok(warmup.elapsed_ms<25000);}}
    receipt.functional_result='passed';receipt.performance_result=receipt.performance.every(row=>row.comparisons.every(item=>item.interpretation==='within-observed-baseline-variability'))?'within-observed-baseline-variability':'requires-review';
  }
  try{await Promise.race([proof(),deadline]);receipt.result='passed';}
  catch(error){receipt.result='failed';receipt.failure=error.stack||String(error);if(page&&!page.isClosed())receipt.startup.failure_state=await page.evaluate(()=>({document_ready:document.readyState,unified_shell_count:document.querySelectorAll('.apt-home-shell[data-home-layout="unified"]').length,registry_present:!!window.HomeSpatialViewport,selected_site:window.HomeSpatialViewport?.getSelectedSiteId?.()||null,site_phase:window.__HOME_APARTMENT_SITE_API?.readback().phase||null,engine_site:window.__APT_ENGINE?.siteId||null})).catch(()=>null);throw error;}
  finally{clearTimeout(timer);releaseLeafRequest?.();if(page&&!page.isClosed()){const saved=await page.evaluate(()=>window.__overlayBridgeProof?.warmups||null).catch(()=>null);if(saved)receipt.warmups=saved;}await context?.close();await browser?.close();server?.closeAllConnections();if(server)await new Promise(resolve=>server.close(resolve));fs.writeFileSync(path.join(run,'receipt.json'),JSON.stringify(receipt,null,2)+'\n');console.log(JSON.stringify({receipt:path.join(run,'receipt.json'),result:receipt.result,functional:receipt.functional_result,performance:receipt.performance_result,checks:receipt.checks.length}));}
});
