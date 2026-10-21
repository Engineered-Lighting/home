// Actual mounted toolbar, host callbacks and renderer mode method, with
// authored asset promises. No full renderer, pack, household content or service.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
const require=createRequire(import.meta.url),{chromium}=require('playwright');
const ROOT=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),SRC=path.join(ROOT,'app/src');
const read=name=>fs.readFileSync(path.join(SRC,name),'utf8'),sha=value=>createHash('sha256').update(value).digest('hex');
const source=read('home-apartment.jsx'),css=read('home-tokens.css'),uiSource=read('home-apartment-ui.jsx'),guideSource=read('home-apartment-room-guide.jsx'),babel=require(path.join(SRC,'vendor/babel-7.29.0/babel.min.js'));
const ast=babel.transform(source,{parserOpts:{plugins:['jsx']},ast:true,code:false}).ast.program,text=node=>source.slice(node.start,node.end);
function walk(node,visit){if(!node||typeof node!=='object')return;if(node.type)visit(node);for(const[key,value]of Object.entries(node)){if(['loc','extra','leadingComments','trailingComments','innerComments'].includes(key))continue;if(Array.isArray(value))value.forEach(item=>walk(item,visit));else if(value&&typeof value==='object')walk(value,visit)}}
const declaration=name=>{const found=ast.body.filter(node=>node.type==='FunctionDeclaration'&&node.id?.name===name);assert.equal(found.length,1,'one actual '+name);return text(found[0])};
function binding(name){const found=[];walk(ast,node=>{if(node.type==='VariableDeclaration'&&node.declarations.some(item=>item.id?.name===name))found.push(node)});assert.equal(found.length,1,'one actual '+name);return text(found[0])}
const toolbarOpenings=[];walk(ast,node=>{if(node.type==='JSXOpeningElement'&&node.name?.name==='AptUnifiedRoomToolbar')toolbarOpenings.push(node)});assert.equal(toolbarOpenings.length,1,'one unified toolbar owner');
const prop=name=>{const attr=toolbarOpenings[0].attributes.find(item=>item.name?.name===name);assert.ok(attr?.value?.expression,'actual toolbar supplies '+name);return text(attr.value.expression)};
const engineBindings=[],modeListeners=[];
walk(ast,node=>{
  if(node.type==='VariableDeclarator'&&node.id?.name==='bindInteriorEngine')engineBindings.push(node);
  if(node.type==='CallExpression'&&node.callee?.object?.object?.name==='engine'&&node.callee.object.property?.name==='modes'&&node.callee.property?.name==='onChange')modeListeners.push(node);
});
assert.equal(engineBindings.length,1,'one installed engine owner');assert.equal(modeListeners.length,1,'one actual renderer mode subscription');
const presentationNames=new Set(['modePresentationScope','modePresentationLifecycle','modePresentationIsCurrent']);
const presentationDeclarations=engineBindings[0].init.arguments[0].body.body.filter(node=>node.type==='VariableDeclaration'&&node.declarations.some(item=>presentationNames.has(item.id?.name))).map(text).join('\n');
assert.equal(engineBindings[0].init.arguments[0].body.body.filter(node=>node.type==='VariableDeclaration'&&node.declarations.some(item=>presentationNames.has(item.id?.name))).length,3,'all actual presentation declarations');
const rendererSource=read('home-3d/modes.js'),rendererAst=babel.transform(rendererSource,{ast:true,code:false}).ast.program,rendererMethods=[];
walk(rendererAst,node=>{if(node.type==='ObjectMethod'&&node.key?.name==='setMode')rendererMethods.push(node)});
assert.equal(rendererMethods.length,1,'one actual renderer mode method');
const rendererMethod=rendererSource.slice(rendererMethods[0].start,rendererMethods[0].end);

function installedModeWrapper(theme){return `(()=>{
  const h=React.createElement,proof=window.__photoModeOwnerProof={effects:[],pending:[],selected:'victoria'};
  const state={mode:'points',targetMode:'points',modeSeq:0,fading:null,splat:{opacity:0}},listeners=[],
    assertLive=()=>{},pointsOnlyScene=async()=>false,visibleFor=()=>{},pointsMaterial={uniforms:{uOpacity:{value:1}}},
    load=mode=>new Promise((resolve,reject)=>proof.pending.push({mode,resolve,reject})),loadSplat=()=>load('splat'),loadMesh=()=>load('mesh');
  const engine=proof.engine={siteId:'victoria',contentRevision:'authored-victoria-r1',sceneIdentity:{siteId:'victoria',contentRevision:'authored-victoria-r1',frameDigest:'authored-frame',registrationVersion:1},rig:{inCameraPose:()=>false},modes:{
    get mode(){return state.mode},${rendererMethod},onChange:listener=>{listeners.push(listener);return()=>listeners.splice(listeners.indexOf(listener),1)}
  }};
  window.HomeSpatialViewport={getSelectedSiteId:()=>proof.selected,getSite:()=>({contentRevision:'authored-victoria-r1',sceneRegistration:{version:1}})};
  window.HomeContentPacks={get:()=>({manifest:{revision:'authored-victoria-r1',registrationVersion:1,assets:{'frame.json':{sha256:'authored-frame'}}}})};
  function InstalledModeProof(){
    const [mode,setModeState]=useState('points'),[modeLoading,setLoadingState]=useState(null),[modeError,setErrorState]=useState(null),
      engineRef=useRef(engine),viewOpenRef=useRef(true),preSnapModeRef=useRef(null),spatialLifecycleRevisionRef=useRef(1),spatialTravelIntentRef=useRef(1),
      worldOverviewAbortRef=useRef(null),travellingRef=useRef(false),spatialCoordinatorRef=useRef(null),worldSurfaceRef=useRef(null),bound=useRef(false);
    const selectedSiteInterior=true,liveCam=null,calibCam=null,
      setMode=value=>{proof.effects.push(['mode',value]);setModeState(value)},setModeLoading=value=>{proof.effects.push(['loading',value]);setLoadingState(value)},
      setModeError=value=>{proof.effects.push(['error',value]);setErrorState(value)},showToast=(...args)=>proof.effects.push(['toast',...args]),
      refitMobileOverview=value=>proof.effects.push(['refit',value]),setTravelling=()=>{};
    ${binding('modeRequestRef')}
    ${binding('pickMode')}
    ${binding('cancelSpatialTravel')}
    if(!bound.current){
      const intent=spatialTravelIntentRef.current,lifecycle=spatialLifecycleRevisionRef.current,
        current=()=>viewOpenRef.current&&engineRef.current===engine&&proof.selected===engine.siteId&&spatialTravelIntentRef.current===intent&&spatialLifecycleRevisionRef.current===lifecycle,
        persistView=()=>proof.effects.push(['persist']);
      ${presentationDeclarations}
      engine.modes.onChange(${text(modeListeners[0].arguments[0])});bound.current=true;
    }
    proof.state={mode,modeLoading,modeError};proof.request=()=>modeRequestRef.current;
    proof.start=mode=>{let result;ReactDOM.flushSync(()=>{result=pickMode(mode)});return result};
    proof.cancel=()=>{let result;ReactDOM.flushSync(()=>{result=cancelSpatialTravel()});return result};
    return h('section',{className:'apt-home-shell','data-theme':${JSON.stringify(theme)},style:{position:'relative',width:'100%',height:'100%',background:'var(--apt-surface)'}},
      h(AptUnifiedRoomToolbar,{mode,modeLoading,modeError,sourceLabel:'Authored installed mode',view:0,disabled:false,onMode:pickMode,onOrbit:()=>{},onFit:()=>{}}));
  }
  const root=ReactDOM.createRoot(document.getElementById('root'));ReactDOM.flushSync(()=>root.render(h(InstalledModeProof)));proof.unmount=()=>ReactDOM.flushSync(()=>root.unmount());
})()`}

test('Photo recovery uses the existing mode owner and preserves scope, locks and accessible recovery controls',{timeout:60000},async()=>{
  const run=path.join(ROOT,'.tmp/apartment-photo-recovery-browser','run-'+Date.now());fs.mkdirSync(run,{recursive:true});
  const receipt={schema:'home-apartment-photo-recovery-browser-1',actual_toolbar:true,actual_mode_callback:true,actual_cancel_callback:true,actual_renderer_mode_method:true,actual_installed_mode_subscription:true,authored_engine_promises:true,
    complete_home_app:false,real_renderer:false,room_assets_loaded:false,household_data_loaded:false,physical_commands:0,camera_sessions:0,model_sessions:0,screenshots:false,
    sources:{'home-apartment.jsx':sha(source),'home-tokens.css':sha(css),'home-overlay.js':sha(read('home-overlay.js')),'home-3d/modes.js':sha(rendererSource),'home-apartment-ui.jsx':sha(uiSource),'home-apartment-room-guide.jsx':sha(guideSource)},checks:[],toast_coexistence:[],requests:[],websocket_attempts:[],errors:[]};
  let browser;
  try{
    const installed=fs.existsSync(chromium.executablePath());browser=await chromium.launch({headless:true,args:['--disable-background-networking','--renderer-process-limit=2','--js-flags=--max-old-space-size=512'],...(!installed&&process.platform==='win32'?{channel:'msedge'}:{})});
    for(const scenario of [{theme:'dark',width:1366,height:768,coarse:false},{theme:'light',width:1366,height:768,coarse:false},{theme:'dark',width:390,height:844,coarse:true},{theme:'light',width:390,height:844,coarse:true}]){
      const label=scenario.theme+'-'+scenario.width,context=await browser.newContext({viewport:{width:scenario.width,height:scenario.height},hasTouch:scenario.coarse,isMobile:scenario.coarse,serviceWorkers:'block'});
      try{
        await context.route('**/*',route=>{receipt.requests.push(route.request().url());return route.abort()});await context.routeWebSocket('**/*',socket=>{receipt.websocket_attempts.push(socket.url());socket.close()});
        const page=await context.newPage();page.setDefaultTimeout(5000);page.on('pageerror',error=>receipt.errors.push(error.message));
        await page.setContent('<meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body,#root{margin:0;width:100%;height:100%;overflow:hidden}</style><div id="root"></div>');
        await page.addStyleTag({content:css.replace(/@font-face\s*\{[^}]*\}/g,'')});
        for(const name of ['vendor/react-18.3.1/react.production.min.js','vendor/react-18.3.1/react-dom.production.min.js','home-overlay.js'])await page.addScriptTag({content:read(name)});
        await page.addScriptTag({content:babel.transform(uiSource,{presets:['react']}).code});
        await page.addScriptTag({content:babel.transform(guideSource,{presets:['react']}).code});
        await page.addScriptTag({content:'const {useState,useRef,useCallback}=React;\n'+babel.transform(declaration('AptUnifiedIcon')+'\n'+declaration('AptUnifiedRoomToolbar'),{presets:['react']}).code});
        const wrapper=`
          (()=>{
            const h=React.createElement,proof=window.__photoRecoveryProof={calls:[],toasts:[],refits:[],pending:[],behavior:'success',engines:{},selected:'victoria'};
            function makeEngine(site){const engine={siteId:site,contentRevision:'authored-'+site+'-r1',sceneIdentity:{siteId:site,contentRevision:'authored-'+site+'-r1'},scenePlaceholder:null,rig:{inCameraPose:()=>proof.cameraPose===true},modes:{mode:'points',setMode:mode=>{
              proof.calls.push({site,mode});return new Promise((resolve,reject)=>{const request={site,mode,resolve:()=>{engine.modes.mode=mode;resolve()},reject:()=>reject(Error('Authored Photo missing'))};if(proof.behavior==='pending')proof.pending.push(request);else if(proof.behavior==='failure')request.reject();else request.resolve()});
            }}};return engine}
            proof.engines.victoria=makeEngine('victoria');proof.engines.echo=makeEngine('echo');
            window.HomeSpatialViewport={getSelectedSiteId:()=>proof.selected,getSite:site=>({shortName:'Authored '+site,contentRevision:'authored-'+site+'-r1'})};
            function PhotoRecoveryProof(){
              const [snapshot,setSnapshot]=useState({disabled:false,pointsOnlyReason:null,liveCam:null,calibCam:null,selectedSiteInterior:true}),
                [mode,setMode]=useState('points'),[modeLoading,setModeLoading]=useState(null),[modeError,setModeError]=useState({mode:'splat',message:'photo unavailable',detail:'Authored unavailable Photo'}),[toast,setToast]=useState(null);
              const engineRef=useRef(proof.engines.victoria),preSnapModeRef=useRef(null),viewOpenRef=useRef(true),spatialLifecycleRevisionRef=useRef(1),spatialTravelIntentRef=useRef(1),
                workspaceAuthorityRef=useRef({key:'authored-authority'});
              const selectedSiteId=proof.selected,selectedContentRevision='authored-'+proof.selected+'-r1',apartmentAuthorityKey='authored-authority',selectedSiteInterior=snapshot.selectedSiteInterior,
                liveCam=snapshot.liveCam,calibCam=snapshot.calibCam,apartmentSceneControlsEnabled=!snapshot.disabled&&selectedSiteInterior;
              const editing=snapshot.editing===true;
              const refitMobileOverview=value=>proof.refits.push(value),setLiveOn=value=>{proof.liveOn=value};
              ${binding('showToast')}
              ${binding('dismissToast')}
              ${binding('modeRequestRef')}
              ${binding('pickMode')}
              if(toast&&toast!==proof.lastToast)proof.toasts.push(toast);proof.lastToast=toast;
              proof.state={mode,modeLoading,modeError,toast};proof.pick=pickMode;
              proof.update=patch=>ReactDOM.flushSync(()=>setSnapshot(previous=>({...previous,...patch})));
              proof.error=error=>ReactDOM.flushSync(()=>setModeError(error));
              proof.switchHome=(site,loading=null)=>ReactDOM.flushSync(()=>{proof.selected=site;engineRef.current=proof.engines[site];spatialLifecycleRevisionRef.current++;spatialTravelIntentRef.current++;setMode('points');setModeError(null);setModeLoading(loading);setSnapshot(previous=>({...previous}))});
              proof.close=()=>{viewOpenRef.current=false};
              return h('section',{className:'apt-home-shell','data-theme':${JSON.stringify(scenario.theme)},style:{position:'relative',width:'100%',height:'100%',background:'var(--apt-surface)'}},
                h('div',{'data-home-apartment-view':'1',style:{position:'relative',width:'100%',height:'100%'}},
                h(AptUnifiedRoomToolbar,{mode,modeLoading,modeError,sourceLabel:'Authored source',sourceDetail:'Authored component proof',view:0,disabled:!apartmentSceneControlsEnabled,
                  pointsOnlyReason:snapshot.pointsOnlyReason,modeLockedReason:${prop('modeLockedReason')},onMode:${prop('onMode')},onOrbit:()=>{},onFit:()=>{}}),
                h(window.HomeApartmentUI.Toast,{toast,onDismiss:dismissToast})));
            }
            const root=ReactDOM.createRoot(document.getElementById('root'));ReactDOM.flushSync(()=>root.render(h(PhotoRecoveryProof)));proof.unmount=()=>ReactDOM.flushSync(()=>root.unmount());
          })();`;
        await page.addScriptTag({content:babel.transform(wrapper,{presets:['react']}).code});
        const check=async(name,callback)=>{await callback();receipt.checks.push(label+': '+name)},group=()=>page.getByRole('group',{name:'Photo recovery',exact:true}),retry=()=>page.getByRole('button',{name:'Retry Photo',exact:true}),back=()=>page.getByRole('button',{name:'Back to Cloud',exact:true}),state=()=>page.evaluate(()=>window.__photoRecoveryProof.state);
        await check('missing Photo offers native bounded recovery controls and visible keyboard focus',async()=>{
          assert.equal(await group().isVisible(),true);assert.equal(await retry().isEnabled(),true);assert.equal(await back().isEnabled(),true);
          await retry().focus();await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');
          const focus=await retry().evaluate(node=>({focused:document.activeElement===node,visible:node.matches(':focus-visible'),outline:getComputedStyle(node).outlineStyle,width:parseFloat(getComputedStyle(node).outlineWidth)}));
          assert.equal(focus.focused,true);assert.equal(focus.visible,true);assert.notEqual(focus.outline,'none');assert.ok(focus.width>0);
          for(const control of [retry(),back()]){const rect=await control.boundingBox();assert.ok(rect&&rect.x>=0&&rect.x+rect.width<=scenario.width+.5);if(scenario.coarse)assert.ok(rect.height>=44&&rect.width>=44);}
        });
        await check('Retry stays pending, then shows Photo only after the existing owner succeeds',async()=>{
          await page.evaluate(()=>{window.__photoRecoveryProof.behavior='pending'});await retry().press('Enter');
          assert.equal((await state()).modeLoading,'splat');assert.equal((await state()).mode,'points');assert.equal(await group().count(),0);
          assert.deepEqual(await page.evaluate(()=>window.__photoRecoveryProof.calls),[{site:'victoria',mode:'splat'}]);
          await page.evaluate(()=>window.__photoRecoveryProof.pending.shift().resolve());await page.waitForFunction(()=>window.__photoRecoveryProof.state.modeLoading===null);
          assert.equal((await state()).mode,'splat');assert.equal((await state()).modeError,null);assert.equal(await page.getByRole('button',{name:'Photo view',exact:true}).getAttribute('aria-pressed'),'true');
        });
        await check('a failed Retry returns explicit recovery without changing home or silently selecting Cloud',async()=>{
          await page.evaluate(()=>{window.__photoRecoveryProof.error({mode:'splat',message:'photo unavailable'});window.__photoRecoveryProof.behavior='failure'});
          if(scenario.coarse)await retry().tap();else await retry().click();
          await page.waitForFunction(()=>window.__photoRecoveryProof.state.modeLoading===null&&window.__photoRecoveryProof.state.modeError?.mode==='splat');
          assert.equal(await group().isVisible(),true);assert.equal(await page.evaluate(()=>window.__photoRecoveryProof.selected),'victoria');assert.equal((await state()).mode,'splat');
        });
        await check('actual error Toast coexists with immediate native Cloud recovery and readable native dismissal',async()=>{
          const toast=page.locator('.apt-ui-toast'),dismiss=page.getByRole('button',{name:'Dismiss message',exact:true});
          await toast.waitFor();await toast.hover();
          const layout=await page.evaluate(()=>{
            const toast=document.querySelector('.apt-ui-toast'),recovery=document.querySelector('.apt-ui-toolbar-notice'),back=document.querySelector('.apt-ui-toolbar-recovery button:last-child'),dismiss=toast.querySelector('[aria-label="Dismiss message"]'),message=toast.querySelector('.apt-ui-toast-message');
            const box=node=>{const r=node.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}},a=box(toast),b=box(recovery),target=box(back),close=box(dismiss),style=getComputedStyle(message),background=getComputedStyle(toast).backgroundColor;
            return {toast:a,recovery:b,back:target,dismiss:close,nonoverlap:a.right<=b.x||b.right<=a.x||a.bottom<=b.y||b.bottom<=a.y,
              backReceivesPointer:back.contains(document.elementFromPoint(target.x+target.width/2,target.y+target.height/2)),
              dismissReceivesPointer:dismiss.contains(document.elementFromPoint(close.x+close.width/2,close.y+close.height/2)),
              message:message.textContent,fontSize:parseFloat(style.fontSize),foreground:style.color,background};
          });receipt.toast_coexistence.push({scenario:label,...layout});
          await page.evaluate(()=>{window.__photoRecoveryProof.behavior='success'});
          if(scenario.coarse)await back().tap({timeout:1000});else await back().click({timeout:1000});
          assert.equal(layout.nonoverlap,true);assert.equal(layout.backReceivesPointer,true);assert.equal(layout.dismissReceivesPointer,true);
          assert.match(layout.message,/photo unavailable/);assert.ok(layout.fontSize>=12);assert.notEqual(layout.foreground,layout.background);
          for(const rect of [layout.toast,layout.recovery,layout.dismiss])assert.ok(rect.x>=0&&rect.right<=scenario.width+.5&&rect.y>=0&&rect.bottom<=scenario.height+.5);
          if(scenario.coarse)assert.ok(layout.dismiss.width>=44&&layout.dismiss.height>=44);
          await page.waitForFunction(()=>window.__photoRecoveryProof.state.mode==='points'&&window.__photoRecoveryProof.state.modeLoading===null);
          assert.equal(await toast.isVisible(),true,'explicit recovery does not rewrite the retained sticky Toast lifetime');
          if(scenario.coarse)await dismiss.tap();else await dismiss.click();
          assert.equal(await toast.count(),0);
          await page.evaluate(async()=>{const proof=window.__photoRecoveryProof;proof.behavior='failure';await proof.pick('splat')});
          await page.waitForFunction(()=>window.__photoRecoveryProof.state.modeLoading===null&&window.__photoRecoveryProof.state.modeError?.mode==='splat');
          assert.equal(await group().isVisible(),true);assert.equal(await toast.isVisible(),true);
          await page.evaluate(()=>{const proof=window.__photoRecoveryProof;proof.behavior='pending';proof.retainedToast=proof.state.toast;proof.retainedToastElement=document.querySelector('.apt-ui-toast')});
          if(scenario.coarse)await retry().tap({timeout:1000});else await retry().click({timeout:1000});
          assert.equal((await state()).modeLoading,'splat');assert.equal(await group().count(),0);assert.equal(await toast.isVisible(),true);
          await page.evaluate(()=>window.__photoRecoveryProof.pending.shift().resolve());
          await page.waitForFunction(()=>window.__photoRecoveryProof.state.mode==='splat'&&window.__photoRecoveryProof.state.modeLoading===null);
          assert.equal(await page.getByRole('button',{name:'Photo view',exact:true}).getAttribute('aria-pressed'),'true');
          const restored=await page.evaluate(()=>{
            const proof=window.__photoRecoveryProof,element=document.querySelector('.apt-ui-toast'),dismiss=element.querySelector('[aria-label="Dismiss message"]'),r=dismiss.getBoundingClientRect(),style=getComputedStyle(element);
            return {sameToast:proof.state.toast===proof.retainedToast,sameElement:element===proof.retainedToastElement,message:element.querySelector('.apt-ui-toast-message').textContent,
              expectedMessage:proof.retainedToast.text,sticky:proof.state.toast.sticky,top:style.top,bottom:style.bottom,bottomGap:element.parentElement.getBoundingClientRect().bottom-element.getBoundingClientRect().bottom,
              actualMode:proof.engines.victoria.modes.mode,mode:proof.state.mode,
              dismissReceivesPointer:dismiss.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))};
          });receipt.toast_coexistence.at(-1).after_native_retry=restored;
          assert.equal(restored.sameToast,true);assert.equal(restored.sameElement,true);assert.equal(restored.message,restored.expectedMessage);assert.equal(restored.sticky,true);
          assert.notEqual(restored.bottom,'auto');assert.ok(Math.abs(restored.bottomGap-parseFloat(restored.bottom))<.5);assert.equal(restored.actualMode,'splat');assert.equal(restored.mode,restored.actualMode);assert.equal(restored.dismissReceivesPointer,true);
          if(scenario.coarse)await dismiss.tap();else await dismiss.click();
          assert.equal(await toast.count(),0);
          await page.evaluate(async()=>{const proof=window.__photoRecoveryProof;proof.behavior='failure';await proof.pick('splat')});
          await page.waitForFunction(()=>window.__photoRecoveryProof.state.modeLoading===null&&window.__photoRecoveryProof.state.modeError?.mode==='splat');
          assert.equal(await group().isVisible(),true);assert.equal(await toast.isVisible(),true);
        });
        await check('Back to Cloud is an explicit points-mode operation with native Space activation',async()=>{
          await page.evaluate(()=>{window.__photoRecoveryProof.behavior='success'});const count=await page.evaluate(()=>window.__photoRecoveryProof.calls.length);await back().focus();await page.keyboard.press('Space');
          await page.waitForFunction(()=>window.__photoRecoveryProof.state.mode==='points'&&window.__photoRecoveryProof.state.modeLoading===null);
          assert.deepEqual(await page.evaluate(index=>window.__photoRecoveryProof.calls.slice(index),count),[{site:'victoria',mode:'points'}]);assert.equal((await state()).modeError,null);assert.equal(await group().count(),0);
        });
        await check('camera lock and unavailable interior disable recovery; points-only permits only explicit Cloud',async()=>{
          await page.evaluate(()=>{window.__photoRecoveryProof.error({mode:'splat',message:'photo unavailable'});window.__photoRecoveryProof.update({liveCam:{id:'authored-camera'}})});
          assert.equal(await retry().isDisabled(),true);assert.equal(await back().isDisabled(),true);
          await page.evaluate(()=>window.__photoRecoveryProof.update({liveCam:null,selectedSiteInterior:false}));assert.equal(await retry().isDisabled(),true);assert.equal(await back().isDisabled(),true);
          await page.evaluate(()=>window.__photoRecoveryProof.update({selectedSiteInterior:true,pointsOnlyReason:'Authored point-cloud-only placeholder'}));assert.equal(await retry().isDisabled(),true);assert.equal(await back().isEnabled(),true);
          await page.evaluate(()=>window.__photoRecoveryProof.update({pointsOnlyReason:null}));
        });
        for(const failed of [true,false])await check('old-home '+(failed?'error':'success')+' cannot replace destination loading or publish a stale Photo recovery',async()=>{
          await page.evaluate(()=>{window.__photoRecoveryProof.behavior='pending'});await retry().click();
          await page.evaluate(()=>window.__photoRecoveryProof.switchHome('echo','mesh'));const refits=await page.evaluate(()=>window.__photoRecoveryProof.refits.length),toasts=await page.evaluate(()=>window.__photoRecoveryProof.toasts.length);
          await page.evaluate(failed=>{const pending=window.__photoRecoveryProof.pending.shift();failed?pending.reject():pending.resolve()},failed);await page.evaluate(()=>Promise.resolve());
          assert.equal((await state()).modeLoading,'mesh');assert.equal((await state()).modeError,null);assert.equal(await group().count(),0);
          assert.equal(await page.evaluate(()=>window.__photoRecoveryProof.refits.length),refits);assert.equal(await page.evaluate(()=>window.__photoRecoveryProof.toasts.length),toasts);
          await page.evaluate(()=>{window.__photoRecoveryProof.switchHome('victoria');window.__photoRecoveryProof.error({mode:'splat',message:'photo unavailable'})});
        });
        await page.evaluate(()=>window.__photoRecoveryProof.unmount());
        for(const failed of [false,true])await check('same-home cancellation '+(failed?'failure':'success')+' keeps controls honest without retired request effects or persistence',async()=>{
          await page.addScriptTag({content:babel.transform(installedModeWrapper(scenario.theme),{presets:['react']}).code});
          await page.evaluate(()=>{const proof=window.__photoModeOwnerProof;proof.retired=proof.start('splat')});
          await page.waitForFunction(()=>window.__photoModeOwnerProof.pending.length===1);
          await page.evaluate(()=>window.__photoModeOwnerProof.cancel());
          assert.equal(await page.evaluate(()=>window.__photoModeOwnerProof.state.modeLoading),null);
          assert.equal(await page.evaluate(()=>window.__photoModeOwnerProof.request()===null),true);
          await page.evaluate(async failed=>{const proof=window.__photoModeOwnerProof;proof.effects.length=0;const load=proof.pending.shift();failed?load.reject(Error('Authored cancelled Photo')):load.resolve();await proof.retired},failed);
          const result=await page.evaluate(()=>{const proof=window.__photoModeOwnerProof;return {state:proof.state,engineMode:proof.engine.modes.mode,effects:proof.effects}});
          assert.equal(result.state.mode,result.engineMode);assert.equal(result.engineMode,failed?'points':'splat');assert.equal(result.state.modeError,null);assert.equal(result.state.modeLoading,null);
          assert.deepEqual(result.effects,failed?[]:[['mode','splat']]);
          assert.equal(await page.getByRole('button',{name:'Photo view',exact:true}).getAttribute('aria-pressed'),String(!failed));
          await page.evaluate(()=>window.__photoModeOwnerProof.unmount());
        });
        for(const failed of [false,true])await check('cancelled Photo '+(failed?'failure':'success')+' preserves a newer real renderer request and its loading owner',async()=>{
          await page.addScriptTag({content:babel.transform(installedModeWrapper(scenario.theme),{presets:['react']}).code});
          await page.evaluate(()=>{const proof=window.__photoModeOwnerProof;proof.retired=proof.start('splat')});
          await page.waitForFunction(()=>window.__photoModeOwnerProof.pending.length===1);await page.evaluate(()=>window.__photoModeOwnerProof.cancel());
          await page.evaluate(()=>{const proof=window.__photoModeOwnerProof;proof.current=proof.start('mesh');proof.newestRequest=proof.request()});
          await page.waitForFunction(()=>window.__photoModeOwnerProof.pending.length===2);
          await page.evaluate(async failed=>{const proof=window.__photoModeOwnerProof;proof.effects.length=0;const load=proof.pending.shift();failed?load.reject(Error('Authored superseded Photo')):load.resolve();await proof.retired},failed);
          const pending=await page.evaluate(()=>{const proof=window.__photoModeOwnerProof;return {state:proof.state,effects:proof.effects,stillOwned:proof.request()===proof.newestRequest}});
          assert.equal(pending.state.modeLoading,'mesh');assert.equal(pending.state.modeError,null);assert.equal(pending.stillOwned,true);assert.deepEqual(pending.effects,[]);
          await page.evaluate(async()=>{const proof=window.__photoModeOwnerProof;proof.pending.shift().resolve();await proof.current});
          const settled=await page.evaluate(()=>{const proof=window.__photoModeOwnerProof;return {state:proof.state,engineMode:proof.engine.modes.mode,effects:proof.effects}});
          assert.equal(settled.engineMode,'mesh');assert.equal(settled.state.mode,'mesh');assert.equal(settled.state.modeLoading,null);assert.equal(settled.state.modeError,null);
          assert.equal(settled.effects.filter(row=>row[0]==='refit').length,1);assert.equal(settled.effects.filter(row=>row[0]==='persist').length,0);
          await page.evaluate(()=>window.__photoModeOwnerProof.unmount());
        });
      }finally{await context.close()}
    }
    assert.deepEqual(receipt.requests,[]);assert.deepEqual(receipt.websocket_attempts,[]);assert.deepEqual(receipt.errors,[]);receipt.result='passed';
  }catch(error){receipt.result='failed';receipt.failure=error.stack||String(error);throw error}
  finally{await browser?.close();fs.writeFileSync(path.join(run,'receipt.json'),JSON.stringify(receipt,null,2)+'\n');console.log(JSON.stringify({receipt:path.join(run,'receipt.json'),result:receipt.result,checks:receipt.checks.length}))}
});
