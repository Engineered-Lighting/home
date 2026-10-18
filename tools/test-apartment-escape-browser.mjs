// APT-F04: actual HomeOverlay/useOverlayLayer with mounted React and real focus.
// This authored component proof does not mount Home's service graph or a room.
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
const hash = value => createHash('sha256').update(value).digest('hex');
const babel = require(path.join(SRC, 'vendor/babel-7.29.0/babel.min.js'));
const parsed = name => {
  const source = read(name);
  return { source, ast: babel.transform(source, { parserOpts: { plugins: ['jsx'] }, ast: true, code: false }).ast.program };
};
const apartment = parsed('home-apartment.jsx'), editor = parsed('home-apartment-edit.jsx');
const text = (file, node) => file.source.slice(node.start, node.end);
function walk(node, visit) {
  if (!node || typeof node !== 'object') return;
  if (node.type) visit(node);
  for (const [key, value] of Object.entries(node)) {
    if (['loc', 'extra', 'leadingComments', 'trailingComments', 'innerComments'].includes(key)) continue;
    if (Array.isArray(value)) value.forEach(item => walk(item, visit));
    else if (value && typeof value === 'object') walk(value, visit);
  }
}
function binding(file, name) {
  const found = [];
  walk(file.ast, node => {
    if (node.type === 'VariableDeclaration' && node.declarations.some(item => item.id?.name === name)) found.push(node);
  });
  assert.equal(found.length, 1, 'one production binding for ' + name); return text(file, found[0]);
}
function layer(file, key) {
  const found = [];
  walk(file.ast, node => {
    if (node.type === 'CallExpression' && node.callee?.property?.name === 'useOverlayLayer') {
      const arg = node.arguments[0];
      if (arg?.type === 'ObjectExpression' && arg.properties.some(item => item.key?.name === 'key' && item.value?.value === key)) found.push(arg);
    }
  });
  assert.equal(found.length, 1, 'one production layer ' + key); return text(file, found[0]);
}
function matchingStatement(file, type, signature) {
  const found = [];
  walk(file.ast, node => {
    const callee = node.expression?.callee;
    if (node.type === type && (callee?.name === 'useEffect' || callee?.property?.name === 'useLayoutEffect')
        && text(file, node).includes(signature)) found.push(node);
  });
  assert.equal(found.length, 1, 'one production statement for ' + signature); return text(file, found[0]);
}
function apartmentRootCapture(name) {
  const found = [];
  walk(apartment.ast, node => {
    if (node.type !== 'JSXOpeningElement' || !node.attributes.some(item => item.name?.name === 'data-home-apartment-view')) return;
    const attr = node.attributes.find(item => item.name?.name === name);
    assert.ok(attr?.value?.expression, 'actual Apartment root owns ' + name); found.push(attr.value.expression);
  });
  assert.equal(found.length, 1); return text(apartment, found[0]);
}

test('mounted production overlay respects editable ownership, one-layer Escape, Tab and usable focus restoration', { timeout: 60000 }, async () => {
  const run = path.join(ROOT, '.tmp/apartment-escape-browser', 'run-' + Date.now());
  fs.mkdirSync(run, { recursive: true });
  const receipt = {
    schema: 'home-apartment-escape-browser-1', actual_overlay_core: true, actual_overlay_react_hook: true,
    complete_home_app: false, authored_component: true, room_assets_loaded: false, household_data_loaded: false,
    camera_sessions: 0, model_sessions: 0, physical_commands: 0, screenshots: false,
    sources: { 'home-overlay.js': hash(read('home-overlay.js')), 'home-apartment.jsx': hash(apartment.source),
      'home-apartment-edit.jsx': hash(editor.source) },
    checks: [], requests: [], websocket_attempts: [], errors: [],
  };
  let browser, context;
  try {
    const installed = fs.existsSync(chromium.executablePath());
    browser = await chromium.launch({ headless: true,
      args: ['--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'],
      ...(!installed && process.platform === 'win32' ? { channel: 'msedge' } : {}) });
    context = await browser.newContext({ viewport: { width: 1100, height: 760 }, serviceWorkers: 'block' });
    await context.route('**/*', route => { receipt.requests.push(route.request().url()); return route.abort(); });
    await context.routeWebSocket('**/*', socket => { receipt.websocket_attempts.push(socket.url()); socket.close(); });
    const page = await context.newPage(); page.setDefaultTimeout(5000);
    page.on('pageerror', error => receipt.errors.push(error.message));
    await page.setContent('<style>button,input,textarea,select{margin:5px}section{padding:10px;border:1px solid}#fallback{display:block}</style><div id="root"></div>');
    for (const name of ['vendor/react-18.3.1/react.production.min.js', 'vendor/react-18.3.1/react-dom.production.min.js', 'home-overlay.js']) {
      await page.addScriptTag({ content: read(name) });
    }
    await page.addScriptTag({ content: `
      const proof = window.__escapeProof = { calls: [], renderCount: 0 };
      const h = React.createElement;
      function EscapeProof() {
        const [open, setOpen] = React.useState(false), [nested, setNested] = React.useState(false);
        const root = React.useRef(null), child = React.useRef(null), restore = React.useRef(null);
        proof.renderCount++;
        window.HomeOverlay.useOverlayLayer({ key:'apartment-embedded', active:true, passive:true, initialFocus:'none',
          onEscape:()=>{ proof.calls.push('embedded-pass'); return false; } });
        window.HomeOverlay.useOverlayLayer({ key:'authored-modal', active:open, rootRef:root,
          getRoots:()=>[document.getElementById('authored-portal')], trap:true, initialFocus:'first', restoreTo:restore,
          onEscape:()=>{ proof.calls.push('modal'); setOpen(false); } });
        window.HomeOverlay.useOverlayLayer({ key:'authored-nested', active:nested, rootRef:child, trap:true, initialFocus:'first',
          onEscape:()=>{ proof.calls.push('nested'); setNested(false); } });
        return h('main',{},
          h('span',{id:'opener-wrap'},h('button',{id:'opener',onClick:()=>setOpen(true)},'Open authored modal')),
          h('button',{id:'restore',ref:restore},'Restore target'),
          h('button',{id:'fallback'},'App fallback'),
          h('textarea',{id:'behind','aria-label':'Editor behind modal'}),
          h('section',{id:'authored-portal'},h('textarea',{id:'portal-editor','aria-label':'Editor in owned portal'})),
          open && h('section',{id:'modal',ref:root,role:'dialog','aria-label':'Authored modal'},
            h('button',{id:'first'},'First control'),
            h('input',{id:'inside-input','aria-label':'Input inside modal'}),
            h('textarea',{id:'inside-textarea','aria-label':'Textarea inside modal'}),
            h('select',{id:'inside-select','aria-label':'Select inside modal'},h('option',{},'One'),h('option',{},'Two')),
            h('div',{id:'inside-editor',contentEditable:true,suppressContentEditableWarning:true,tabIndex:0},'Editable draft'),
            h('button',{id:'nested-opener',onClick:()=>setNested(true)},'Open nested modal'),
            h('button',{id:'last'},'Last control')),
          nested && h('section',{id:'nested',ref:child,role:'dialog','aria-label':'Nested authored modal'},h('button',{id:'nested-first'},'Nested control')));
      }
      window.HomeOverlay.setFallbackFocus(()=>document.getElementById('fallback'));
      const root=ReactDOM.createRoot(document.getElementById('root'));
      ReactDOM.flushSync(()=>root.render(h(EscapeProof)));
      proof.unmount=()=>ReactDOM.flushSync(()=>root.unmount());
    ` });
    const check = async (name, callback) => { await callback(); receipt.checks.push(name); };
    const active = () => page.evaluate(() => document.activeElement?.id);
    const top = () => page.evaluate(() => window.HomeOverlay.topKey());
    const open = async () => {
      await page.evaluate(() => {
        const opener = document.getElementById('opener'); opener.hidden = false; opener.disabled = false; opener.inert = false; opener.style.visibility = '';
        const wrapper = document.getElementById('opener-wrap'); wrapper.hidden = false; wrapper.inert = false;
        const restore = document.getElementById('restore'); restore.hidden = false; restore.disabled = false; restore.inert = false; restore.style.visibility = '';
      });
      await page.locator('#opener').click();
      await page.waitForFunction(() => window.HomeOverlay.topKey() === 'authored-modal' && document.activeElement?.id === 'first');
    };
    await check('embedded idle Escape passes without removing the Apartment layer', async () => {
      await page.keyboard.press('Escape'); assert.equal(await top(), 'apartment-embedded');
      assert.deepEqual(await page.evaluate(() => window.__escapeProof.calls), ['embedded-pass']);
    });
    await open();
    await check('opening a modal focuses its first control', async () => assert.equal(await active(), 'first'));
    for (const id of ['inside-input', 'inside-textarea', 'inside-select', 'inside-editor']) {
      await check(id + ' claims Escape before its modal', async () => {
        await page.locator('#' + id).focus(); await page.keyboard.press('Escape');
        assert.equal(await top(), 'authored-modal');
        assert.equal(await page.locator('#modal').count(), 1);
      });
    }
    await check('an owned portal editor keeps Escape while Tab still enters the main modal', async () => {
      await page.locator('#portal-editor').focus(); await page.keyboard.press('Escape');
      assert.equal(await top(), 'authored-modal');
      await page.keyboard.press('Tab'); assert.equal(await active(), 'first');
    });
    await check('a held Escape cannot advance or dismiss the modal', async () => {
      await page.locator('#first').focus();
      await page.evaluate(() => document.getElementById('first').dispatchEvent(new KeyboardEvent('keydown', { key:'Escape', repeat:true, bubbles:true, cancelable:true })));
      assert.equal(await top(), 'authored-modal');
      assert.deepEqual(await page.evaluate(() => window.__escapeProof.calls), ['embedded-pass']);
    });
    await check('Tab wraps within the actual modal in both directions', async () => {
      await page.locator('#last').focus(); await page.keyboard.press('Tab'); assert.equal(await active(), 'first');
      await page.keyboard.press('Shift+Tab'); assert.equal(await active(), 'last');
    });
    await check('a nested modal consumes one Escape and restores its own opener', async () => {
      await page.locator('#nested-opener').click();
      await page.waitForFunction(() => window.HomeOverlay.topKey() === 'authored-nested' && document.activeElement?.id === 'nested-first');
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => window.HomeOverlay.topKey() === 'authored-modal');
      assert.equal(await active(), 'nested-opener');
      assert.equal(await page.locator('#modal').count(), 1);
      assert.equal(await page.locator('#nested').count(), 0);
      assert.deepEqual(await page.evaluate(() => window.__escapeProof.calls), ['embedded-pass', 'nested']);
    });
    await check('a focused editor behind the modal cannot swallow its Escape', async () => {
      await page.locator('#behind').focus(); await page.keyboard.press('Escape');
      await page.waitForFunction(() => window.HomeOverlay.topKey() === 'apartment-embedded');
      assert.equal(await active(), 'opener');
    });
    for (const unavailable of ['hidden', 'disabled', 'visibility-hidden', 'inert', 'hidden-parent', 'inert-parent']) {
      await open();
      await check(unavailable + ' opener falls back to visible enabled restore target', async () => {
        await page.evaluate(kind => {
          if (kind === 'hidden-parent') document.getElementById('opener-wrap').hidden = true;
          else if (kind === 'inert-parent') document.getElementById('opener-wrap').inert = true;
          else if (kind === 'visibility-hidden') document.getElementById('opener').style.visibility = 'hidden';
          else document.getElementById('opener')[kind] = true;
        }, unavailable);
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => window.HomeOverlay.topKey() === 'apartment-embedded');
        assert.equal(await active(), 'restore');
      });
    }
    for (const unavailable of ['hidden', 'disabled', 'visibility-hidden', 'inert']) {
      await open();
      await check(unavailable + ' restore target falls back to the usable app target', async () => {
        await page.evaluate(kind => {
          document.getElementById('opener').hidden = true;
          if (kind === 'visibility-hidden') document.getElementById('restore').style.visibility = 'hidden';
          else document.getElementById('restore')[kind] = true;
        }, unavailable);
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => window.HomeOverlay.topKey() === 'apartment-embedded');
        assert.equal(await active(), 'fallback');
      });
    }
    await check('every completed close leaves the embedded Apartment mounted', async () => {
      assert.equal(await top(), 'apartment-embedded');
      assert.equal(await page.evaluate(() => window.HomeOverlay._stackSize()), 1);
    });
    await check('an incidental body opener uses a real restore control', async () => {
      const result = await page.evaluate(() => {
        window.HomeOverlay.restoreFocus(document.body, document.getElementById('fallback'));
        return document.activeElement?.id;
      });
      assert.equal(result, 'fallback');
    });
    await page.evaluate(() => window.__escapeProof.unmount());
    assert.equal(await page.evaluate(() => window.HomeOverlay._stackSize()), 0);

    // Mount the actual parent callback, operation-opener effect, deferred focus
    // restoration and root capture props. Only their neighboring state/producer
    // values are authored; the complete Home/renderer/service graph is absent.
    const parentBindings = ['rememberEscapeTrigger', 'restoreApartmentOperationFocus', 'clearVirtualFixtureSelection', 'handleApartmentEscape']
      .map(name => binding(apartment, name)).join('\n');
    const openerEffect = matchingStatement(apartment, 'ExpressionStatement', 'escapeOperationsRef.current = next;');
    const focusCleanup = matchingStatement(apartment, 'ExpressionStatement', 'cancelAnimationFrame(escapeFocusFrameRef.current);');
    await page.addScriptTag({ content: `
      (()=>{
        const {useState,useRef,useCallback,useEffect}=React;
        const proof=window.__parentEscapeProof={calls:[],mounts:0};
        function ParentEscapeProof(){
          const [aimMode,setAimMode]=useState(false), [cardId,setCardId]=useState(null), [editing,setEditing]=useState(false),
            [virtualFixtureControlsOpen,setVirtualFixtureControlsOpen]=useState(false);
          const open=true, embedded=true, unifiedShell=true, apartmentSceneControlsEnabled=true;
          const liveCam=null, virtualFixtureId=null, deskTargetSelection=null, shellControlsHost=null, shellCameraHost=null;
          const viewRootRef=useRef(null), escapeTriggerRef=useRef(null), escapeOpenersRef=useRef({}),
            escapeOperationsRef=useRef({}), escapeFocusFrameRef=useRef(null),viewOpenRef=useRef(true),
            deskTargetCaptureRef=useRef(null), virtualAimGestureRef=useRef(null), travellingRef=useRef(false),
            spatialCoordinatorRef=useRef(null),calibPairCountRef=useRef(0),calibEscArmRef=useRef(0),
            engineRef=useRef({renderer:{domElement:null},rig:{inCameraPose:()=>false},overlay:{setVirtualFixtureSelected:()=>{}}});
          const mapRehearsalOpen=false,calibCam=null;
          const setAimDestination=value=>proof.calls.push('destination-clear');
          const cancelDeskTargetSelection=()=>{throw Error('inactive desk owner called')},cancelSpatialTravel=()=>{throw Error('inactive travel owner called')},
            exitCameraPose=()=>{throw Error('inactive camera owner called')},showToast=()=>{throw Error('inactive calibration owner called')},setToast=()=>{};
          ${parentBindings}
          ${openerEffect}
          ${focusCleanup}
          window.HomeOverlay.useOverlayLayer(${layer(apartment, 'apartment-embedded')});
          proof.cancel=()=>ReactDOM.flushSync(()=>handleApartmentEscape());
          proof.openers=escapeOpenersRef;
          return h('section',{id:'actual-parent',ref:viewRootRef,tabIndex:-1,
              onKeyDownCapture:${apartmentRootCapture('onKeyDownCapture')},onPointerDownCapture:${apartmentRootCapture('onPointerDownCapture')}},
            h('button',{id:'start-aim',onClick:()=>setAimMode(true)},'Start authored tap aim'),
            !aimMode&&h('button',{id:'transient-aim',onClick:()=>setAimMode(true)},'Start with transient control'),
            h('output',{id:'tap-state'},aimMode?'aiming':'idle'));
        }
        const root=ReactDOM.createRoot(document.getElementById('root'));
        ReactDOM.flushSync(()=>root.render(h(ParentEscapeProof)));
        proof.unmount=()=>ReactDOM.flushSync(()=>root.unmount());
      })();
    ` });
    await check('actual root capture and operation effect remember a pointer opener, then restore it after cancellation commits', async () => {
      await page.locator('#start-aim').click();
      assert.equal(await page.locator('#tap-state').textContent(), 'aiming');
      assert.equal(await page.evaluate(() => window.__parentEscapeProof.openers.current.aim?.id), 'start-aim');
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => document.getElementById('tap-state')?.textContent === 'idle' && document.activeElement?.id === 'start-aim');
      assert.equal(await top(), 'apartment-embedded');
    });
    await check('actual key capture remembers a keyboard opener without replacing it on Escape', async () => {
      await page.locator('#start-aim').focus(); await page.keyboard.press('Enter');
      assert.equal(await page.evaluate(() => window.__parentEscapeProof.openers.current.aim?.id), 'start-aim');
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => document.getElementById('tap-state')?.textContent === 'idle' && document.activeElement?.id === 'start-aim');
    });
    await check('a removed operation opener falls back to the actual parent viewport root', async () => {
      await page.locator('#transient-aim').click();
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => document.getElementById('tap-state')?.textContent === 'idle');
      // The transient control remounts as a different DOM node. The recorded
      // opener stays disconnected and cannot take focus away from the viewport.
      await page.waitForFunction(() => document.activeElement?.id === 'actual-parent');
    });
    await check('deferred operation restoration yields to a newly opened modal', async () => {
      await page.locator('#start-aim').click();
      await page.evaluate(() => {
        window.__parentEscapeProof.cancel();
        const root=document.createElement('section'),button=document.createElement('button'); button.id='later-modal-control';button.textContent='Later modal';root.append(button);document.body.append(root);
        window.__laterEscapeModal=window.HomeOverlay.push({key:'later-modal',getRoot:()=>root,initialFocus:'none'});button.focus();
      });
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(await active(), 'later-modal-control');
      await page.evaluate(() => {window.__laterEscapeModal.pop();document.getElementById('later-modal-control').parentElement.remove();});
    });
    await check('actual operation cleanup cancels queued restoration on unmount', async () => {
      await page.locator('#start-aim').click();
      await page.evaluate(() => {
        window.__parentEscapeProof.cancel();window.__parentEscapeProof.unmount();
        const stable=document.createElement('button');stable.id='post-unmount-control';stable.textContent='After unmount';document.body.append(stable);stable.focus();
      });
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(await active(), 'post-unmount-control'); assert.equal(await page.evaluate(() => window.HomeOverlay._stackSize()), 0);
    });

    // Use the production target/zone layer declarations and Cancel bodies,
    // plus the actual zone naming input. Only surrounding scene state is
    // authored; no edit-model service or renderer is mounted.
    const nameComponent = editor.ast.body.find(node => node.type === 'FunctionDeclaration' && node.id?.name === 'EdZoneNameInput');
    assert.ok(nameComponent, 'the existing naming field remains present');
    const editorProof = `
      (()=>{
        const {useState,useRef,useCallback,useEffect}=React;const ED_SANS='sans-serif';
        ${text(editor, nameComponent)}
        const proof=window.__editorEscapeProof={};
        function EditorEscapeProof(){
          const [tool,setTool]=useState('targets'),[placing,setPlacing]=useState(null),[placingTarget,setPlacingTarget]=useState(null),[targetDraftId,setTargetDraftId]=useState(null),
            [selectedTargetId,setSelectedTargetId]=useState(null),[moveTargetId,setMoveTargetId]=useState(null),
            [zoneDrawing,setZoneDrawing]=useState(false),[zoneNaming,setZoneNaming]=useState(null),[zoneDraft,setZoneDraft]=useState([]),[notice,setNotice]=useState(''),
            [model,setModel]=useState({targets:[{id:'existing',name:'Existing'}],zones:[{id:'existing-zone'}],unsaved:true});
          const editorToolbarRef=useRef(null),editorPaletteRef=useRef(null),editorInspectorRef=useRef(null);
          const mutate=callback=>setModel(previous=>{const next=JSON.parse(JSON.stringify(previous));callback(next);return next;});
          ${binding(editor, 'cancelTargetPlacement')}
          ${binding(editor, 'cancelZoneNaming')}
          ${binding(editor, 'finishZone')}
          ${binding(editor, 'selectEditorTool')}
          ${binding(editor, 'cancelEditorToolOperation')}
          window.HomeOverlay.useOverlayLayer(${layer(editor, 'apartment-editor-tool')});
          window.HomeOverlay.useOverlayLayer(${layer(editor, 'apartment-target-placement')});
          window.HomeOverlay.useOverlayLayer(${layer(editor, 'apartment-zones')});
          proof.state={model,tool,placing,placingTarget,targetDraftId,moveTargetId,selectedTargetId,zoneDraft,zoneDrawing,zoneNaming,notice};
          return h('section',{id:'actual-editor'},
            h('div',{ref:editorToolbarRef,id:'editor-toolbar',tabIndex:-1},h('button',{id:'start-zone',onClick:()=>{setTool('zones');setZoneDrawing(true);setZoneDraft([[0,0],[1,0],[1,1]]);}},'Draw authored zone'),
              zoneDrawing&&!zoneNaming&&h('button',{id:'finish-zone',onClick:finishZone},'Finish authored zone')),
            h('div',{ref:editorPaletteRef},h('button',{id:'start-target',onClick:()=>{setTool('targets');setPlacingTarget({label:'Authored target'});setTargetDraftId('draft');setModel(previous=>({...previous,targets:[...previous.targets,{id:'draft',name:'Draft'}]}));}},'Place authored target'),
              h('button',{id:'start-target-move',onClick:()=>{setTool('targets');setSelectedTargetId('existing');setMoveTargetId('existing');}},'Move authored target'),
              h('button',{id:'start-device',onClick:()=>{setTool('add');setPlacing({id:'authored-device'});}},'Place authored device'),
              h('button',{id:'start-measure',onClick:()=>setTool('measure')},'Measure authored fixture')),
            h('div',{ref:editorInspectorRef},zoneNaming&&h(EdZoneNameInput,{suggested:zoneNaming.suggested,onCancel:cancelZoneNaming,onCommit:()=>{throw Error('Escape must not commit a name')}})));
        }
        const root=ReactDOM.createRoot(document.getElementById('root'));ReactDOM.flushSync(()=>root.render(h(EditorEscapeProof)));
        proof.unmount=()=>ReactDOM.flushSync(()=>root.unmount());
      })();
    `;
    await page.addScriptTag({ content: babel.transform(editorProof, { presets: ['react'] }).code });
    await check('actual target-placement Escape uses the existing Cancel mutation and returns to its opener', async () => {
      await page.locator('#start-target').click();
      await page.waitForFunction(() => window.HomeOverlay.topKey() === 'apartment-target-placement');
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => window.HomeOverlay.topKey() === null);
      const state = await page.evaluate(() => window.__editorEscapeProof.state);
      assert.deepEqual(state.model.targets, [{id:'existing',name:'Existing'}]);
      assert.deepEqual(state.model.zones,[{id:'existing-zone'}]);assert.equal(state.model.unsaved,true);assert.equal(state.tool,'targets');
      assert.equal(state.targetDraftId,null);assert.equal(state.placingTarget,null);assert.equal(await active(),'start-target');
    });
    await check('actual zone-name Escape cancels only naming and preserves the drawn boundary', async () => {
      await page.locator('#start-zone').click();
      await page.waitForFunction(() => window.HomeOverlay.topKey() === 'apartment-zones');
      await page.locator('#finish-zone').click();
      await page.waitForFunction(() => window.HomeOverlay.topKey() === 'apartment-zones' && document.activeElement?.getAttribute('aria-label') === 'new zone name');
      await page.keyboard.press('Escape');
      const state=await page.evaluate(()=>window.__editorEscapeProof.state);
      assert.equal(state.zoneNaming,null);assert.equal(state.zoneDrawing,true);assert.equal(state.zoneDraft.length,3);
      assert.equal(await top(),'apartment-zones');assert.deepEqual(state.model.zones,[{id:'existing-zone'}]);
    });
    await check('the next Escape cancels only the zone draft and leaves Setup plus existing model content', async () => {
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => window.HomeOverlay.topKey() === null);
      const state=await page.evaluate(()=>window.__editorEscapeProof.state);
      assert.equal(state.zoneDrawing,false);assert.deepEqual(state.zoneDraft,[]);assert.equal(state.tool,'zones');
      assert.deepEqual(state.model.zones,[{id:'existing-zone'}]);assert.equal(state.model.unsaved,true);assert.equal(await active(),'start-zone');
      assert.equal(await page.locator('#actual-editor').count(),1);
    });
    for(const [id,tool,cleared] of [['start-target-move','targets','moveTargetId'],['start-device','add','placing'],['start-measure','select',null]]){
      await check('actual armed '+id+' cancels only its UI operation before leaving Setup',async()=>{
        const before=await page.evaluate(()=>JSON.stringify(window.__editorEscapeProof.state.model));
        await page.locator('#'+id).click();await page.waitForFunction(()=>window.HomeOverlay.topKey()==='apartment-editor-tool');
        await page.keyboard.press('Escape');await page.waitForFunction(()=>window.HomeOverlay.topKey()===null);
        const state=await page.evaluate(()=>window.__editorEscapeProof.state);
        assert.equal(state.tool,tool);if(cleared)assert.equal(state[cleared],null);
        assert.equal(JSON.stringify(state.model),before);assert.equal(await active(),id);
        assert.equal(await page.locator('#actual-editor').count(),1);
      });
    }
    await page.evaluate(() => window.__editorEscapeProof.unmount());
    assert.equal(await page.evaluate(() => window.HomeOverlay._stackSize()), 0);
    assert.deepEqual(receipt.requests, []); assert.deepEqual(receipt.websocket_attempts, []); assert.deepEqual(receipt.errors, []);
    receipt.passed = true;
  } catch (error) {
    receipt.passed = false; receipt.failure = error.stack || String(error); throw error;
  } finally {
    await context?.close(); await browser?.close();
    fs.writeFileSync(path.join(run, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    console.log(JSON.stringify({ receipt: path.join(run, 'receipt.json'), passed: receipt.passed, checks: receipt.checks.length }));
  }
});
