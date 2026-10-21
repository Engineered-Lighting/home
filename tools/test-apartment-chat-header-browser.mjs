// Actual header/leaf/controller seams with authored conversation text and inert
// clipboard/reset adapters. No Home service graph, room, camera or model runs.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'), SRC = path.join(ROOT, 'app/src');
const read = name => fs.readFileSync(path.join(SRC, name), 'utf8');
const sha = value => createHash('sha256').update(value).digest('hex');
const babel = require(path.join(SRC, 'vendor/babel-7.29.0/babel.min.js'));
const source = read('home-app.jsx'), css = read('home-tokens.css'), leaf = read('home-chat-header-actions.jsx');
const ast = babel.transform(source, { parserOpts: { plugins: ['jsx'] }, ast: true, code: false }).ast.program;
const text = node => source.slice(node.start, node.end);
function walk(node, visit) {
  if (!node || typeof node !== 'object') return;
  if (node.type) visit(node);
  for (const [key, value] of Object.entries(node)) {
    if (['loc', 'extra', 'leadingComments', 'trailingComments', 'innerComments'].includes(key)) continue;
    if (Array.isArray(value)) value.forEach(item => walk(item, visit));
    else if (value && typeof value === 'object') walk(value, visit);
  }
}
const declaration = name => {
  const found = ast.body.filter(node => node.type === 'FunctionDeclaration' && node.id?.name === name);
  assert.equal(found.length, 1, 'one actual declaration for ' + name); return text(found[0]);
};
const normal = ast.body.find(node => node.type === 'FunctionDeclaration' && node.id?.name === 'HomeNormalApp');
assert.ok(normal, 'actual controller remains present');
function binding(name) {
  const found = [];
  walk(normal, node => {
    if (node.type === 'VariableDeclaration' && node.declarations.some(item => item.id?.name === name
        || item.id?.type === 'ArrayPattern' && item.id.elements.some(element => element?.name === name))) found.push(node);
  });
  assert.equal(found.length, 1, 'one actual controller binding for ' + name); return text(found[0]);
}
function copyScopeEffect() {
  const found = [];
  walk(normal, node => {
    if (node.type === 'ExpressionStatement' && node.expression?.callee?.object?.name === 'React'
        && node.expression?.callee?.property?.name === 'useLayoutEffect'
        && text(node).includes('conversationCopyAttemptRef.current += 1;')) found.push(node);
  });
  assert.equal(found.length, 1, 'one actual copy lifetime effect'); return text(found[0]);
}
const headerOpenings = [];
walk(normal, node => { if (node.type === 'JSXOpeningElement' && node.name?.name === 'HomeHeader') headerOpenings.push(node); });
assert.equal(headerOpenings.length, 1, 'one Home header remains mounted');
const headerProp = name => {
  const attr = headerOpenings[0].attributes.find(item => item.name?.name === name);
  assert.ok(attr?.value?.expression, 'actual header supplies ' + name); return text(attr.value.expression);
};
assert.equal(headerProp('onCopyTranscript'), 'copyConversationTranscript');
assert.equal(headerProp('onNewConversation'), 'requestClearConfirm');
assert.match(headerProp('onExplainLastAnswer'), /handleCommand\(["']\/why["']\)/);

const undoCases = [];
walk(normal, node => { if (node.type === 'SwitchCase' && node.test?.value === 'undo-clear') undoCases.push(node); });
assert.equal(undoCases.length, 1, 'one existing undo-clear owner');
const undoClearBody = undoCases[0].consequent.map(text).join('\n');
const mountedBindings = ['conversationCopyState', 'conversationCopyBusyRef', 'conversationCopyAttemptRef', 'conversationVoiceBusy',
  'copyConversationTranscript', 'conversationCopyDisabledReason'].map(binding).join('\n');
const guardedClearBindings = ['requestClearConfirm', 'cancelAction', 'confirmAction'].map(binding).join('\n');
const headerSource = declaration('ConnectionDot') + '\n' + declaration('HomeHeader');

const scenarios = [
  { theme: 'dark', width: 1366, height: 768, coarse: false },
  { theme: 'light', width: 1366, height: 768, coarse: false },
  { theme: 'dark', width: 390, height: 844, coarse: true },
  { theme: 'light', width: 390, height: 844, coarse: true },
];

test('actual unified header delegates guarded actions and truthfully copies current conversation text', { timeout: 60000 }, async () => {
  const run = path.join(ROOT, '.tmp/apartment-chat-header-browser', 'run-' + Date.now());
  fs.mkdirSync(run, { recursive: true });
  const receipt = {
    schema: 'home-apartment-chat-header-browser-1', actual_header: true, actual_action_leaf: true,
    actual_copy_controller: true, actual_guarded_clear_callbacks: true, authored_events: true,
    complete_home_app: false, room_assets_loaded: false, household_data_loaded: false,
    physical_commands: 0, camera_sessions: 0, model_sessions: 0, screenshots: false,
    sources: { 'home-app.jsx': sha(source), 'home-chat-header-actions.jsx': sha(leaf), 'home-tokens.css': sha(css),
      'home-overlay.js': sha(read('home-overlay.js')) }, checks: [], requests: [], websocket_attempts: [], errors: [],
  };
  let browser;
  try {
    const installed = fs.existsSync(chromium.executablePath());
    browser = await chromium.launch({ headless: true,
      args: ['--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'],
      ...(!installed && process.platform === 'win32' ? { channel: 'msedge' } : {}) });
    for (const scenario of scenarios) {
      const label = `${scenario.theme}-${scenario.width}-${scenario.coarse ? 'coarse' : 'fine'}`;
      const context = await browser.newContext({ viewport: { width: scenario.width, height: scenario.height },
        hasTouch: scenario.coarse, isMobile: scenario.coarse, serviceWorkers: 'block' });
      try {
        await context.route('**/*', route => { receipt.requests.push(route.request().url()); return route.abort(); });
        await context.routeWebSocket('**/*', socket => { receipt.websocket_attempts.push(socket.url()); socket.close(); });
        const page = await context.newPage(); page.setDefaultTimeout(5000);
        page.on('pageerror', error => receipt.errors.push(error.message));
        await page.setContent(`<meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body{margin:0;width:100%;height:100%}#root{width:100%;height:100%;overflow:hidden}</style><div id="root"></div>`);
        await page.addStyleTag({ content: css.replace(/@font-face\s*\{[^}]*\}/g, '') });
        for (const name of ['vendor/react-18.3.1/react.production.min.js', 'vendor/react-18.3.1/react-dom.production.min.js', 'home-overlay.js']) {
          await page.addScriptTag({ content: read(name) });
        }
        await page.addScriptTag({ content: babel.transform(leaf, { presets: ['react'], sourceType: 'script' }).code });
        await page.addScriptTag({ content: 'const {useState,useEffect,useRef,useCallback}=React;\n' + babel.transform(headerSource, { presets: ['react'] }).code });
        const wrapper = `
          (()=>{
            const h=React.createElement;
            const proof=window.__chatHeaderProof={commands:[],writes:[],stateWrites:[],resetCalls:[],stops:0,nextId:0,
              clipboardMode:'resolve',pending:[],initialEvents:[
                {id:'question',kind:'user',text:'Authored question'},
                {id:'answer',kind:'home',text:'Authored answer'},
                {id:'voice-pending',kind:'voice',text:'Unclaimed voice placeholder'},
                {id:'streaming',kind:'home',text:'Incomplete answer',streaming:true},
                {id:'camera-private',kind:'home',text:'Private camera interpretation',privateCameraContext:true,snapshotUrl:'authored-private-image-reference'},
                {id:'background',kind:'home',text:'Background perception',backgroundPerception:true},
                {id:'structured-tool',kind:'tool',text:'Tool payload',args:{authored:true}}
              ]};
            const writeText=text=>{
              proof.writes.push(text);
              if(proof.clipboardMode==='deny')return Promise.reject(new Error('Authored clipboard denial'));
              if(proof.clipboardMode==='false')return Promise.resolve(false);
              if(proof.clipboardMode==='pending')return new Promise((resolve,reject)=>proof.pending.push({resolve,reject}));
              return Promise.resolve();
            };
            const clipboard={writeText};Object.defineProperty(navigator,'clipboard',{configurable:true,get:()=>proof.clipboardMode==='unavailable'?undefined:clipboard});
            window.HomePersonalMemory={reset:()=>proof.resetCalls.push('memory')};
            window.HomeLightingControl={reset:()=>proof.resetCalls.push('lighting')};
            window.HomeCameraQuery={reset:opts=>proof.resetCalls.push(['camera',opts])};
            function HeaderProof(){
              const useState=initial=>{const [value,setValue]=React.useState(initial);return [value,initial===''?next=>{proof.stateWrites.push(next);setValue(next)}:setValue]};
              const [snapshot,setSnapshot]=useState({events:proof.initialEvents,conversationId:'authored-conversation',endpoint:'authored-local-source',token:'authored-secret',voice:{state:'inactive'},s2sState:{state:'idle'}});
              const [events,setEvents]=useState(snapshot.events),[conversationId,setConversationId]=useState(snapshot.conversationId);
              const endpoint=snapshot.endpoint,token=snapshot.token,voice=snapshot.voice,s2sState=snapshot.s2sState;
              const s2sPlaceholderRef=useRef('voice-pending'),eventsSnapshotRef=useRef(events),clearedEventsRef=useRef(null),timers=useRef([]);
              eventsSnapshotRef.current=events;
              const addEvent=useCallback(event=>setEvents(previous=>[...previous,{id:'authored-new-'+(++proof.nextId),...event}]),[]);
              const stopStreaming=useCallback(()=>{proof.stops++},[]),saveOnboarding=value=>{proof.onboarding=value};
              const handleCommand=command=>{proof.commands.push(command)};
              ${mountedBindings}
              ${copyScopeEffect()}
              ${guardedClearBindings}
              const undoClear=()=>{${undoClearBody}};
              proof.update=patch=>ReactDOM.flushSync(()=>{setSnapshot(previous=>({...previous,...patch}));if(Object.hasOwn(patch,'events'))setEvents(patch.events);if(Object.hasOwn(patch,'conversationId'))setConversationId(patch.conversationId)});
              proof.copy=copyConversationTranscript;proof.cancel=cancelAction;proof.confirm=confirmAction;proof.undo=undoClear;
              proof.state={events,conversationId,conversationCopyState,conversationCopyDisabledReason};
              return h('section',{className:'apt-home-shell','data-theme':${JSON.stringify(scenario.theme)},style:{height:'100%',width:'100%',background:'var(--apt-surface)'}},
                h(HomeHeader,{theme:${JSON.stringify(scenario.theme)},mobile:${scenario.width < 900},unified:true,voice,connection:'online',chatSourceLine:'Authored model · Victoria modeled room',
                  onOpenRemoteProfile:()=>{},onOpenBench:()=>{},
                  onExplainLastAnswer:${headerProp('onExplainLastAnswer')},onCopyTranscript:${headerProp('onCopyTranscript')},onNewConversation:${headerProp('onNewConversation')},
                  conversationCopyState:${headerProp('conversationCopyState')},conversationCopyDisabledReason:${headerProp('conversationCopyDisabledReason')}}));
            }
            const root=ReactDOM.createRoot(document.getElementById('root'));ReactDOM.flushSync(()=>root.render(h(HeaderProof)));
            proof.unmount=()=>ReactDOM.flushSync(()=>root.unmount());
          })();
        `;
        await page.addScriptTag({ content: babel.transform(wrapper, { presets: ['react'] }).code });
        const check = async (name, callback) => { await callback(); receipt.checks.push(label + ': ' + name); };
        const button = key => page.locator(`[data-chat-header-action="${key}"]`);
        const state = () => page.evaluate(() => window.__chatHeaderProof.state);
        const status = () => page.locator('.apt-ui-chat-copy-status').textContent();

        await check('all three native actions and the local text scope remain visible without overflow', async () => {
          assert.equal(await page.getByRole('group',{name:'Conversation actions'}).count(),1);
          for(const key of ['explain','copy','new']){assert.equal(await button(key).isVisible(),true);assert.equal(await button(key).isEnabled(),true);}
          assert.equal(await button('copy').getAttribute('aria-description'),'Conversation text only');
          assert.match(await button('copy').getAttribute('title'), /Copy transcript.*Conversation text only/);
          const bounds=await page.evaluate(()=>{
            const shell=document.querySelector('.apt-home-shell'),header=document.querySelector('.apt-ui-chat-header');
            return {shell: shell.getBoundingClientRect().toJSON(),header:header.getBoundingClientRect().toJSON(),scroll:document.documentElement.scrollWidth,width:innerWidth,
              controls:[...document.querySelectorAll('[data-chat-header-action]')].map(node=>node.getBoundingClientRect().toJSON()),coarse:matchMedia('(pointer:coarse)').matches};
          });
          assert.ok(bounds.scroll<=bounds.width);assert.ok(bounds.header.right<=bounds.width+.1);
          assert.equal(bounds.coarse,scenario.coarse);
          for(const rect of bounds.controls){assert.ok(rect.x>=0&&rect.right<=bounds.width+.1);assert.ok(rect.height>=(scenario.coarse?44:32));if(scenario.coarse)assert.ok(rect.width>=44);}
        });
        await check('keyboard focus is visible and Enter/Space delegate Explain to the existing command',async()=>{
          await button('explain').focus();await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');
          const focus=await button('explain').evaluate(node=>{const style=getComputedStyle(node);return {focused:document.activeElement===node,visible:node.matches(':focus-visible'),outline:style.outlineStyle,width:parseFloat(style.outlineWidth)}});
          assert.equal(focus.focused,true);assert.equal(focus.visible,true);assert.notEqual(focus.outline,'none');assert.ok(focus.width>0);
          await page.keyboard.press('Enter');await page.keyboard.press('Space');
          assert.deepEqual(await page.evaluate(()=>window.__chatHeaderProof.commands),['/why','/why']);
          assert.equal(await page.evaluate(()=>window.__chatHeaderProof.writes.length),0);
        });
        await check('New only arms the existing guarded clear; cancellation preserves the conversation',async()=>{
          const before=(await state()).events;
          await button('new').click();
          const armed=(await state()).events,card=armed.at(-1);
          assert.equal(card.service,'home.clear_conversation');assert.equal(card.status,'pending-confirm');assert.deepEqual(armed.slice(0,-1),before);
          assert.equal(await page.evaluate(()=>window.__chatHeaderProof.stops),0);assert.deepEqual(await page.evaluate(()=>window.__chatHeaderProof.resetCalls),[]);
          await page.evaluate(id=>ReactDOM.flushSync(()=>window.__chatHeaderProof.cancel(id)),card.id);
          const cancelled=(await state()).events;assert.equal(cancelled.at(-1).status,'cancelled');assert.deepEqual(cancelled.slice(0,-1),before);
        });
        await check('Copy reports pending truthfully, refuses overlap and excludes private or incomplete content',async()=>{
          await page.evaluate(()=>{window.__chatHeaderProof.clipboardMode='pending'});await button('copy').click();
          assert.match(await status(),/^Copying conversation text/);assert.equal(await button('copy').isDisabled(),true);
          await page.evaluate(()=>window.__chatHeaderProof.copy());
          assert.deepEqual(await page.evaluate(()=>window.__chatHeaderProof.writes),['You: Authored question\nHome: Authored answer']);
          await page.evaluate(()=>window.__chatHeaderProof.pending.shift().resolve());
          await page.waitForFunction(()=>window.__chatHeaderProof.state.conversationCopyState==='Conversation text copied.');
          assert.equal(await status(),'Conversation text copied.');assert.equal(await button('copy').isEnabled(),true);
        });
        await check('clipboard denial and explicit failure never report success',async()=>{
          for(const mode of ['deny','false']){
            await page.evaluate(mode=>{window.__chatHeaderProof.clipboardMode=mode},mode);await button('copy').click();
            await page.waitForFunction(()=>window.__chatHeaderProof.state.conversationCopyState.startsWith('Could not copy'));
            assert.match(await status(),/unavailable or was denied/);
          }
        });
        await check('unavailable clipboard and empty completed text have explicit disabled reasons',async()=>{
          await page.evaluate(()=>{window.__chatHeaderProof.clipboardMode='unavailable';window.__chatHeaderProof.update({endpoint:'authored-unavailable'})});
          assert.equal(await button('copy').isDisabled(),true);assert.match(await button('copy').getAttribute('aria-description'),/Clipboard access is unavailable/);
          await page.evaluate(()=>{window.__chatHeaderProof.clipboardMode='resolve';window.__chatHeaderProof.update({events:[{id:'only-stream',kind:'home',text:'Incomplete',streaming:true}]})});
          assert.equal(await button('copy').isDisabled(),true);assert.equal(await button('copy').getAttribute('aria-description'),'No completed conversation text to copy.');
          assert.equal(await button('explain').isEnabled(),true);assert.equal(await button('new').isEnabled(),true);
        });
        await check('an active voice turn cannot copy its unclaimed placeholder',async()=>{
          await page.evaluate(()=>window.__chatHeaderProof.update({events:window.__chatHeaderProof.initialEvents,voice:{state:'listening'}}));
          assert.equal(await button('copy').isDisabled(),true);assert.match(await button('copy').getAttribute('aria-description'),/active voice turn/);
          const count=await page.evaluate(()=>window.__chatHeaderProof.writes.length);await page.evaluate(()=>window.__chatHeaderProof.copy());
          assert.equal(await page.evaluate(()=>window.__chatHeaderProof.writes.length),count);
          await page.evaluate(()=>window.__chatHeaderProof.update({voice:{state:'inactive'}}));
        });
        await check('a scope change ignores stale copy completion and permits a new current attempt',async()=>{
          await page.evaluate(()=>{window.__chatHeaderProof.clipboardMode='pending'});await button('copy').click();
          await page.evaluate(()=>window.__chatHeaderProof.update({conversationId:'authored-next-conversation',endpoint:'authored-next-source',token:'authored-next-secret',events:[{id:'new-text',kind:'user',text:'Next authored conversation'}]}));
          const before=await page.evaluate(()=>window.__chatHeaderProof.stateWrites.length);
          await page.evaluate(()=>window.__chatHeaderProof.pending.shift().resolve());
          await page.evaluate(()=>Promise.resolve());
          assert.equal(await page.evaluate(()=>window.__chatHeaderProof.stateWrites.length),before);assert.equal((await state()).conversationCopyState,'');
          await page.evaluate(()=>{window.__chatHeaderProof.clipboardMode='resolve'});await button('copy').click();
          await page.waitForFunction(()=>window.__chatHeaderProof.state.conversationCopyState==='Conversation text copied.');
          assert.equal(await page.evaluate(()=>window.__chatHeaderProof.writes.at(-1)),'You: Next authored conversation');
        });
        await check('confirm and one-shot undo remain with their existing guarded owners',async()=>{
          await page.evaluate(()=>window.__chatHeaderProof.update({events:window.__chatHeaderProof.initialEvents}));await button('new').click();
          const card=(await state()).events.at(-1);assert.equal(card.status,'pending-confirm');
          await page.evaluate(id=>ReactDOM.flushSync(()=>window.__chatHeaderProof.confirm(id)),card.id);
          const cleared=(await state()).events;assert.equal(cleared.length,1);assert.match(cleared[0].text,/conversation cleared/);
          assert.equal((await state()).conversationId,null);assert.equal(await page.evaluate(()=>window.__chatHeaderProof.stops),1);
          await page.evaluate(()=>ReactDOM.flushSync(()=>window.__chatHeaderProof.undo()));
          const restored=(await state()).events;assert.ok(restored.some(event=>event.id==='question'));assert.ok(restored.some(event=>event.id==='answer'));
          assert.equal(restored.some(event=>event.service==='home.clear_conversation'),false);
          const camera=restored.find(event=>event.id==='camera-private');assert.equal(camera.snapshotUrl,undefined);assert.equal(camera.imageNoLongerRetained,true);
          await page.evaluate(()=>ReactDOM.flushSync(()=>window.__chatHeaderProof.undo()));assert.equal((await state()).events.at(-1).text,'nothing to restore');
        });
        await check('unmount invalidates a pending copy without another status setter call',async()=>{
          await page.evaluate(()=>{window.__chatHeaderProof.clipboardMode='pending'});await button('copy').click();
          const before=await page.evaluate(()=>{window.__chatHeaderProof.unmount();return window.__chatHeaderProof.stateWrites.length});
          await page.evaluate(()=>window.__chatHeaderProof.pending.shift().resolve());await page.evaluate(()=>Promise.resolve());
          assert.equal(await page.evaluate(()=>window.__chatHeaderProof.stateWrites.length),before);assert.equal(await page.locator('.apt-ui-chat-header').count(),0);
        });
      } finally { await context.close(); }
    }
    assert.deepEqual(receipt.requests,[]);assert.deepEqual(receipt.websocket_attempts,[]);assert.deepEqual(receipt.errors,[]);
    receipt.passed=true;
  } catch(error){receipt.passed=false;receipt.failure=error.stack||String(error);throw error;}
  finally {
    await browser?.close();fs.writeFileSync(path.join(run,'receipt.json'),JSON.stringify(receipt,null,2)+'\n');
    console.log(JSON.stringify({receipt:path.join(run,'receipt.json'),passed:receipt.passed,checks:receipt.checks.length}));
  }
});
