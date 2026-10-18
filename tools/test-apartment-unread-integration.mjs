// Focused source-derived React/browser coverage for APT-S04. This does not load
// Home's service graph, household history, registration, room assets or camera.
// The unchanged authored shell suite separately covers the complete app graph.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SRC = path.join(ROOT, 'app/src');
const read = name => fs.readFileSync(path.join(SRC, name), 'utf8');
const source = read('home-app.jsx');
const babel = require(path.join(SRC, 'vendor/babel-7.29.0/babel.min.js'));
const ast = babel.transform(source, { parserOpts: { plugins: ['jsx'] }, ast: true, code: false }).ast.program;
const text = node => source.slice(node.start, node.end);
const digest = value => createHash('sha256').update(value).digest('hex');

function walk(node, visit) {
  if (!node || typeof node !== 'object') return;
  if (node.type) visit(node);
  for (const [key, value] of Object.entries(node)) {
    if (['loc', 'extra', 'leadingComments', 'trailingComments', 'innerComments'].includes(key)) continue;
    if (Array.isArray(value)) value.forEach(child => walk(child, visit));
    else if (value && typeof value === 'object') walk(value, visit);
  }
}
function declaration(name) {
  const node = ast.body.find(node => node.type === 'FunctionDeclaration' && node.id?.name === name);
  assert.ok(node, 'actual source must declare ' + name);
  return node;
}
function attr(element, name) {
  return element.openingElement.attributes.find(item => item.type === 'JSXAttribute' && item.name?.name === name);
}

test('actual unread hook and shell tabs preserve Chat owners and accessible responsive state', { timeout: 60000 }, async () => {
  const normal = declaration('HomeNormalApp');
  let hookBinding, tabs, surfaceOpening;
  walk(normal, node => {
    if (node.type === 'VariableDeclaration' && node.declarations.some(item => item.init?.type === 'CallExpression'
        && item.init.callee?.name === 'useApartmentChatUnread')) {
      assert.equal(hookBinding, undefined, 'production has one unread hook owner'); hookBinding = node;
    }
    if (node.type === 'JSXElement' && attr(node, 'className')?.value?.value === 'apt-home-tabs') {
      assert.equal(tabs, undefined, 'production has one shell tab tree'); tabs = node;
    }
    if (node.type === 'JSXOpeningElement' && node.name?.name === 'HomeChatSurface') {
      assert.equal(surfaceOpening, undefined, 'production keeps one mounted Chat surface'); surfaceOpening = node;
    }
  });
  assert.ok(hookBinding && tabs && surfaceOpening, 'production hook, tabs and stable Chat surface are present');
  const surfaceActive = surfaceOpening.attributes.find(item => item.name?.name === 'active');
  assert.match(text(surfaceActive), /shellTab\s*===\s*["']chat["']/, 'Chat visibility follows the actual shell tab');
  const helperNames = ast.body.filter(node => node.type === 'FunctionDeclaration'
    && /ApartmentChatUnread|ApartmentAssistantTurn/.test(node.id?.name || '')).map(node => node.id.name);
  assert.ok(helperNames.includes('useApartmentChatUnread'), 'actual hook is included');
  const hookSource = helperNames.map(name => text(declaration(name))).join('\n');
  const actualSource = hookSource + '\n' + text(declaration('HomeChatSurface')) + '\n' + text(declaration('ApartmentChatTurn'));
  const run = path.join(ROOT, '.tmp/apartment-unread-integration', 'run-' + Date.now());
  fs.mkdirSync(run, { recursive: true });
  const receipt = { schema: 'home-apartment-unread-component-integration-1',
    actual_hook: true, actual_tab_jsx: true, actual_chat_presenter: true, complete_home_app: false,
    authored_events: true, room_assets_loaded: false, household_data_loaded: false,
    physical_commands: 0, camera_sessions: 0, model_sessions: 0, screenshots: false,
    sources: { 'home-app.jsx': digest(source), 'home-events.jsx': digest(read('home-events.jsx')),
      'home-tokens.css': digest(read('home-tokens.css')) }, checks: [], network: [], websocket_attempts: [], errors: [] };
  let browser, context;
  try {
    const installed = fs.existsSync(chromium.executablePath());
    browser = await chromium.launch({ headless: true,
      args: ['--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'],
      ...(!installed && process.platform === 'win32' ? { channel: 'msedge' } : {}) });
    context = await browser.newContext({ viewport: { width: 1366, height: 768 }, serviceWorkers: 'block' });
    await context.route('**/*', route => { receipt.network.push(route.request().url()); return route.abort(); });
    await context.routeWebSocket('**/*', socket => { receipt.websocket_attempts.push(socket.url()); socket.close(); });
    const page = await context.newPage(); page.setDefaultTimeout(5000);
    page.on('pageerror', error => receipt.errors.push(error.message));
    await page.setContent('<style>html,body,#authored-root{margin:0;width:100%;height:100%;overflow:hidden}</style><div id="authored-root"></div>');
    // Do not issue font URLs. Typography is assessed by the retained design
    // comparisons; this proof tests the changed state and computed geometry.
    await page.addStyleTag({ content: read('home-tokens.css').replace(/@font-face\s*\{[^}]*\}/g, '') });
    for (const name of ['vendor/react-18.3.1/react.production.min.js', 'vendor/react-18.3.1/react-dom.production.min.js']) {
      await page.addScriptTag({ content: read(name) });
    }
    await page.addScriptTag({ content: 'const {useState,useRef,useEffect,useLayoutEffect,useMemo,useCallback} = React;' });
    await page.addScriptTag({ content: babel.transform(read('home-events.jsx'), { presets: ['react'] }).code });
    await page.addScriptTag({ content: babel.transform(actualSource, { presets: ['react'] }).code });
    const wrapper = `
      const authoredState = window.__unreadProof = { mounts:0, cleanups:0, reviewMounts:0, reviewCleanups:0,
        reviewLoads:0, undoCalls:0, initialEvents:[{id:'history',kind:'home',turnKey:'history-turn',text:'Restored assistant history'}] };
      const mountReview = slot => {
        authoredState.reviewMounts++;
        const frame=document.createElement('iframe'); frame.title='Authored review';
        frame.srcdoc='<script>parent.__unreadProof.reviewLoads+=1</'+'script>';
        authoredState.originalFrame ||= frame; slot.append(frame);
        return () => { authoredState.reviewCleanups++; frame.remove(); };
      };
      authoredState.group={speaker:'home',time:'12:00:00',events:[
        {id:'action',kind:'action',title:'Authored local action',service:'light.turn_on',target:'authored.only',status:'success'},
        {id:'review',kind:'personal-memory-review',text:'Authored review',mountReview},
        {id:'stream',kind:'home',text:'Authored original partial',streaming:true}
      ]};
      function AuthoredUnreadComposition() {
        const [snapshot,setSnapshot]=useState({events:authoredState.initialEvents,endpoint:'https://authored-a.invalid',
          token:'authored-token-a',conversationId:'authored-conversation-a',connection:'disconnected',unifiedHome:true,
          theme:'dark',shellDetent:'half',viewportWidth:1366});
        const {events,endpoint,token,conversationId,connection,unifiedHome,theme,shellDetent}=snapshot;
        const {perceptionLabOpen=false,videoLabelerOpen=false,peopleOpen=false,intelligenceOpen=false,
          shellMetricsOpen=false,remotePanelOpen=false,externalKeyModalOpen=false,simulationControlsOpen=false,
          worldStateDrawerOpen=false,lightsOpen=false,spatialDrawerOpen=false,lookDrawerOpen=false,apartmentOpen=false}=snapshot;
        const viewport={width:snapshot.viewportWidth};
        const [shellTab,setShellTab]=useState('controls');
        const [input,setInput]=useState('');
        const showShellTab=tab=>setShellTab(tab);
        const appColumnStyle={};
        ${text(hookBinding)}
        useEffect(()=>{authoredState.mounts++;return()=>{authoredState.cleanups++}},[]);
        window.__updateUnreadProof=patch=>ReactDOM.flushSync(()=>setSnapshot(previous=>({...previous,...patch})));
        window.__selectUnreadProof=tab=>ReactDOM.flushSync(()=>setShellTab(tab));
        return <div className='apt-home-shell' data-theme={theme} data-home-layout={unifiedHome?'unified':'classic'}
          data-panel={shellTab} data-sheet={shellDetent} data-dock='default' style={{width:'100%',height:'100%'}}>
          <aside className='apt-home-panel' aria-label='Home workspace'>${text(tabs)}</aside>
          ${text(surfaceOpening)}
            <input aria-label='Authored retained draft' value={input} onChange={event=>setInput(event.target.value)}/>
            <ApartmentChatTurn group={authoredState.group} density='compact' unified={unifiedHome}
              onUndoAction={async()=>{authoredState.undoCalls++}}/>
          </HomeChatSurface>
        </div>;
      }
      authoredState.root=ReactDOM.createRoot(document.getElementById('authored-root'));
      ReactDOM.flushSync(()=>authoredState.root.render(<AuthoredUnreadComposition/>));
    `;
    await page.addScriptTag({ content: babel.transform(wrapper, { presets: ['react'] }).code });
    await page.waitForFunction(() => window.__unreadProof.reviewLoads === 1);
    const chat = page.locator('#apt-home-tab-chat');
    const unread = page.locator('#apt-home-tab-chat [data-apartment-chat-unread]');
    const update = patch => page.evaluate(patch => window.__updateUnreadProof(patch), patch);
    const select = tab => page.evaluate(tab => window.__selectUnreadProof(tab), tab);
    const settle = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const checkUnread = async (expected, stage) => {
      await settle();
      assert.equal(await unread.count(), expected ? 1 : 0, stage);
      receipt.checks.push({ stage, unread: expected });
    };
    const home = (id, turnKey, text = 'Authored assistant turn', extra = {}) => ({ id,kind:'home',turnKey,text,...extra });
    const history = [home('history','history-turn','Restored assistant history')];
    await checkUnread(false,'history_baseline_is_read');
    await update({events:history.map(event=>({...event}))});
    await checkUnread(false,'history_rerender_does_not_set_unread');
    await select('chat');
    const draft = page.getByRole('textbox',{name:'Authored retained draft'});
    await draft.fill('Authored unsent draft');
    await page.getByRole('button',{name:'undo',exact:true}).click();
    await page.waitForFunction(()=>window.__unreadProof.undoCalls===1);
    await page.evaluate(()=>{window.__unreadProof.originalSurface=document.querySelector('[data-home-chat-surface]');
      window.__unreadProof.originalDraft=document.querySelector('input[aria-label="Authored retained draft"]')});
    await select('controls');
    let events=[...history,home('new-partial','new-turn','Authored new partial',{streaming:true})];
    await update({events}); await checkUnread(true,'new_hidden_assistant_turn_sets_unread');
    assert.equal(await unread.getAttribute('aria-hidden'),'true','the dot is decorative');
    const accessible = await chat.getAttribute('aria-label');
    assert.match(accessible || await chat.innerText(), /unread|new (?:message|repl)/i,'Chat exposes a nonvisual unread description');
    await chat.click(); await checkUnread(false,'opening_chat_clears_unread');
    await select('controls');
    events=[...history,home('new-partial','new-turn','Authored stream completion',{streaming:false})];
    await update({events}); await checkUnread(false,'same_turn_stream_completion_stays_read');
    await update({events:events.map(event=>({...event}))}); await checkUnread(false,'event_clone_rerender_stays_read');
    await select('chat');
    events=[...events,home('visible','visible-turn')]; await update({events});
    await checkUnread(false,'visible_new_assistant_turn_is_read');
    await select('controls');
    events=[...events,home('scope-old','scope-old-turn')]; await update({events}); await checkUnread(true,'second_hidden_turn_is_unread');
    await update({conversationId:'authored-conversation-b',events:[home('scope-history','scope-history-turn')]});
    await checkUnread(false,'conversation_change_does_not_inherit_unread');
    await update({events:[home('scope-history','scope-history-turn'),home('scope-new','scope-new-turn')]});
    await checkUnread(true,'new_conversation_can_receive_its_own_unread');
    await update({endpoint:'https://authored-b.invalid',events:[home('endpoint-history','endpoint-history-turn')]});
    await checkUnread(false,'endpoint_change_does_not_inherit_unread');
    await update({events:[home('endpoint-history','endpoint-history-turn'),home('responsive','responsive-turn')]});
    for (const theme of ['dark','light']) for (const [width,height] of [[1366,768],[820,1180],[390,844]]) {
      await page.setViewportSize({width,height}); await update({theme,viewportWidth:width}); await settle();
      const bounds=await chat.evaluate(button=>{
        const dot=button.querySelector('[data-apartment-chat-unread]'), rect=button.getBoundingClientRect(), dr=dot.getBoundingClientRect();
        const css=getComputedStyle(dot);
        return {tab:rect.toJSON(),dot:dr.toJSON(),color:css.backgroundColor,opacity:css.opacity,
          overflow:document.documentElement.scrollWidth>innerWidth};
      });
      assert.ok(bounds.dot.width>=4 && bounds.dot.height>=4, 'unread dot is allocated in '+theme+' '+width);
      assert.ok(bounds.dot.left>=bounds.tab.left && bounds.dot.right<=bounds.tab.right+.5,'dot fits its tab');
      assert.ok(bounds.dot.top>=bounds.tab.top && bounds.dot.bottom<=bounds.tab.bottom+.5,'dot fits vertically');
      assert.notEqual(bounds.color,'rgba(0, 0, 0, 0)'); assert.notEqual(bounds.opacity,'0'); assert.equal(bounds.overflow,false);
      receipt.checks.push({stage:'responsive_unread',theme,viewport:{width,height},...bounds});
    }
    await chat.focus(); await chat.press('Enter'); await checkUnread(false,'keyboard_chat_activation_clears_unread');
    await update({shellDetent:'peek'});
    await update({events:[home('endpoint-history','endpoint-history-turn'),home('responsive','responsive-turn'),home('peek','peek-turn')]});
    await checkUnread(true,'collapsed_phone_chat_does_not_consume_new_reply');
    await update({shellDetent:'half'}); await checkUnread(false,'expanding_phone_chat_consumes_reply');
    await select('controls');
    await update({events:[home('endpoint-history','endpoint-history-turn'),home('responsive','responsive-turn'),home('peek','peek-turn'),home('token','token-turn')]});
    await checkUnread(true,'hidden_reply_before_credential_change_is_unread');
    await update({token:'authored-token-b',events:[home('token-history','token-history-turn')]});
    await checkUnread(false,'credential_change_does_not_inherit_unread');
    await select('chat');
    await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});
      document.dispatchEvent(new Event('visibilitychange'))});
    await settle();
    await update({events:[home('token-history','token-history-turn'),home('page-hidden','page-hidden-turn')]});
    await checkUnread(true,'reply_in_background_document_is_unread_even_on_chat_tab');
    await page.evaluate(()=>{delete document.hidden;document.dispatchEvent(new Event('visibilitychange'))});
    await checkUnread(false,'returning_to_visible_chat_consumes_background_reply');
    await select('controls');
    const retained=[home('token-history','token-history-turn'),home('page-hidden','page-hidden-turn')];
    await update({events:[...retained,{id:'user',kind:'you',text:'Authored user'},
      {id:'system',kind:'system',text:'Authored diagnostic'},home('perception','perception-turn','Authored caption',{backgroundPerception:true}),
      home('onboarding','onboarding-turn','Authored welcome',{onboarding:true}),home('empty','empty-turn','',{streaming:true})]});
    await checkUnread(false,'diagnostics_background_history_and_empty_stream_do_not_alert');
    await update({events:[...retained,home('empty','empty-turn','Authored first nonblank stream token',{streaming:true})]});
    await checkUnread(true,'first_nonblank_token_in_existing_stream_event_alerts');
    await select('chat');await checkUnread(false,'reading_first_nonblank_token_clears');
    await select('controls');
    await update({conversationId:null,events:[home('first-seed','first-seed-turn')]});
    await checkUnread(false,'new_unassigned_conversation_history_is_baselined');
    await update({conversationId:'authored-first-conversation',events:[home('first-seed','first-seed-turn'),home('first-reply','first-reply-turn')]});
    await checkUnread(true,'first_server_conversation_id_does_not_swallow_first_reply');
    await select('chat'); await checkUnread(false,'first_conversation_reply_can_be_read');
    const overlays=['perceptionLabOpen','videoLabelerOpen','peopleOpen','intelligenceOpen','shellMetricsOpen',
      'remotePanelOpen','externalKeyModalOpen','simulationControlsOpen','worldStateDrawerOpen','lightsOpen',
      'spatialDrawerOpen','lookDrawerOpen'];
    let overlayEvents=[home('overlay-seed','overlay-seed-turn')];
    await update({events:overlayEvents});
    for (const overlay of overlays) {
      overlayEvents=[...overlayEvents,home('covered-'+overlay,'covered-turn-'+overlay)];
      await update({[overlay]:true,events:overlayEvents});
      await checkUnread(true,'reply_covered_by_'+overlay+'_is_unread');
      await update({[overlay]:false});
      await checkUnread(false,'closing_'+overlay+'_reveals_and_consumes_reply');
    }
    overlayEvents=[...overlayEvents,home('classic-covered','classic-covered-turn')];
    await update({unifiedHome:false,apartmentOpen:true,events:overlayEvents});
    await checkUnread(true,'classic_apartment_overlay_does_not_consume_reply');
    await update({apartmentOpen:false});
    await checkUnread(false,'closing_classic_apartment_overlay_consumes_reply');
    await update({unifiedHome:true});
    await select('controls');
    const privateAnswer=home('retired-private','retired-private-turn');
    const survivingAnswer=home('surviving-answer','surviving-answer-turn');
    await update({events:[...overlayEvents,privateAnswer,survivingAnswer]});
    await checkUnread(true,'multiple_hidden_replies_are_unread');
    await update({events:[...overlayEvents,survivingAnswer]});
    await checkUnread(true,'retiring_one_private_reply_preserves_another_unread_reply');
    await update({events:overlayEvents});
    await checkUnread(false,'retiring_last_unread_reply_clears_the_dot');
    await update({events:[...overlayEvents,home('eligible','eligible-turn')]});
    await checkUnread(true,'new_eligible_reply_can_alert_after_retirement');
    await update({events:[...overlayEvents,home('eligible','eligible-turn','')]});
    await checkUnread(false,'sanitizing_last_reply_content_clears_the_dot');
    await select('chat');
    const identity=await page.evaluate(()=>{
      const state=window.__unreadProof;
      return {mounts:state.mounts,cleanups:state.cleanups,reviewMounts:state.reviewMounts,reviewCleanups:state.reviewCleanups,
        reviewLoads:state.reviewLoads,undoCalls:state.undoCalls,sameReview:document.querySelector('iframe')===state.originalFrame,
        sameSurface:document.querySelector('[data-home-chat-surface]')===state.originalSurface,
        sameDraft:document.querySelector('input[aria-label="Authored retained draft"]')===state.originalDraft,
        draft:state.originalDraft.value,undoButtons:[...document.querySelectorAll('button')].filter(button=>button.textContent==='undo').length};
    });
    assert.deepEqual(identity,{mounts:1,cleanups:0,reviewMounts:1,reviewCleanups:0,reviewLoads:1,undoCalls:1,
      sameReview:true,sameSurface:true,sameDraft:true,draft:'Authored unsent draft',undoButtons:0});
    receipt.checks.push({stage:'chat_lifecycle_and_unsaved_draft_retained',...identity});
    assert.deepEqual(receipt.network,[],'the focused proof makes no HTTP requests');
    assert.deepEqual(receipt.websocket_attempts,[],'the focused proof attempts no WebSocket connections');
    assert.deepEqual(receipt.errors,[],'the actual React proof has no page errors');
    receipt.result='passed';
    await page.evaluate(()=>ReactDOM.flushSync(()=>window.__unreadProof.root.unmount()));
  } catch (error) {
    receipt.result='failed'; receipt.failure=error.message; throw error;
  } finally {
    await context?.close(); await browser?.close();
    fs.writeFileSync(path.join(run,'receipt.json'),JSON.stringify(receipt,null,2)+'\n');
    process.stdout.write('Unread component integration receipt: '+path.join(run,'receipt.json')+'\n');
  }
});
