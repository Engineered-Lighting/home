/* Actual Apartment JSX, projection and decoded player in jsdom. Authored data only.
 * Decoder and public 3D layer are injected; real pixels/rig are covered separately. */
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
const src = path.join(__dirname, '../app/src');
const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
  pretendToBeVisual: true, runScripts: 'outside-only', url: 'http://127.0.0.1/',
});
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'Node', 'Event']) {
  Object.defineProperty(globalThis, key, { configurable: true, writable: true, value: key === 'window' ? dom.window : dom.window[key] });
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const React = require('react');
const { createRoot } = require('react-dom/client');
const { act } = React;
const babel = (() => {
  const module = { exports: {} };
  new Function('module', 'exports', fs.readFileSync(path.join(src, 'vendor/babel-7.29.0/babel.min.js'), 'utf8'))(module, module.exports);
  return module.exports.Babel || module.exports;
})();
const w = dom.window, context = dom.getInternalVMContext();
w.React = React;
w.matchMedia = () => ({ matches: false });
const timers = new Map(); let timerId = 0;
w.setInterval = (fn, ms) => { const id = ++timerId; timers.set(id, { fn, ms }); return id; };
w.clearInterval = id => timers.delete(id);
let selectedSite = 'victoria';
const registryListeners = new Set();
w.HomeSpatialViewport = {
  getSelectedSiteId: () => selectedSite,
  subscribe(fn) { registryListeners.add(fn); return () => registryListeners.delete(fn); },
};
for (const file of ['home-den-reason-vocabulary.js', 'home-apartment-perception-dock-model.js', 'home-apartment-perception-view.js', 'home-apartment-perception-player.js']) {
  vm.runInContext(fs.readFileSync(path.join(src, file), 'utf8'), context);
}
const RealPlayer = w.HomeApartmentPerceptionPlayer;
let decoder = null;
w.HomeApartmentPerceptionPlayer = { create: options => RealPlayer.create({ ...options, env: w, decode: (...args) => decoder(...args) }) };
vm.runInContext(babel.transform(fs.readFileSync(path.join(src, 'home-apartment-den-perception.jsx'), 'utf8'), {
  presets: ['react'], filename: 'home-apartment-den-perception.jsx',
}).code, context);
after(() => {
  assert.equal(timers.size, 0, 'all viewer and player intervals are released');
  assert.equal(registryListeners.size, 0, 'all selected-home observers are released');
  dom.window.close();
});

const copy = value => JSON.parse(JSON.stringify(value));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const flush = () => act(async () => { await new Promise(resolve => setImmediate(resolve)); });
const button = (container, label) => [...container.querySelectorAll('button')].find(item => item.textContent.trim() === label);
async function click(container, label) {
  const control = button(container, label); assert.ok(control, `button ${label}`); assert.equal(control.disabled, false, `${label} enabled`);
  await act(async () => control.click()); await flush();
}
async function choose(container, label, value) {
  const select = container.querySelector(`select[aria-label="${label}"]`); assert.ok(select, label);
  await act(async () => { select.value = value; select.dispatchEvent(new w.Event('change', { bubbles: true })); }); await flush();
}
async function tick(ms) {
  await act(async () => { for (const timer of [...timers.values()]) if (timer.ms === ms) timer.fn(); }); await flush();
}
function engine(extra = {}) {
  const canvas = w.document.createElement('canvas'); w.document.body.appendChild(canvas);
  return { siteId: 'victoria', sceneIdentity: { siteId: 'victoria', contentRevision: 'pack-a', frameDigest: 'frame-a', registrationVersion: 1 },
    renderer: { domElement: canvas }, ...extra };
}
const sessions = () => [
  { id: 'echo-den', site_id: 'echo', camera_id: 'den', generation: 7, state: 'recording' },
  { id: 'vic-other', site_id: 'victoria', camera_id: 'other', generation: 7, state: 'recording' },
  { id: 'invalid-gen', site_id: 'victoria', camera_id: 'den', generation: -1, state: 'recording' },
  { id: 'den-session-one', site_id: 'victoria', camera_id: 'den', generation: 7, state: 'recording' },
  { id: 'den-session-two', site_id: 'victoria', camera_id: 'den', generation: 8, state: 'stopped' },
  { id: 'den-session-three', site_id: 'victoria', camera_id: 'den', generation: 9, state: 'recording' },
];
function source({ list = null, read = null } = {}) {
  const listCalls = [], subscriptions = [], reads = [], forbidden = [];
  const api = {
    now: 100, rows: sessions(), listCalls, subscriptions, reads, forbidden,
    listSessions(options) { listCalls.push(options); return list ? list(options) : Promise.resolve(api.rows); },
    subscribe(options) { const sub = { ...options, off: 0 }; subscriptions.push(sub); return () => { sub.off++; }; },
    sampleClock: () => ({ now_s: api.now, uncertainty_s: .1, domain: 'unix' }),
    readFrame(frame, options) { reads.push({ frame, ...options }); return read ? read(frame, options) : Promise.resolve({ bytes: new w.Uint8Array([1, 2, 3]), type: 'image/png' }); },
    start() { forbidden.push('start'); throw Error('source start forbidden'); },
    startCamera() { forbidden.push('camera'); throw Error('camera acquisition forbidden'); },
    runModel() { forbidden.push('model'); throw Error('model acquisition forbidden'); },
    action() { forbidden.push('action'); throw Error('source writes forbidden'); },
  };
  return api;
}
function observations(sub, { id = 'f1', room = false, stopped = false, jev = true, poseId = null } = {}) {
  const scope = copy(sub.scope);
  const frame = { frame_id: id, capture_id: 'capture-' + id, sequence_id: id === 'f1' ? 1 : 2,
    captured_at: 99, valid_until: 102, width: 640, height: 480, media_id: 'media-' + id,
    generation: scope.generation, site_id: scope.siteId };
  const pose = { frame_id: poseId || id, capture_id: 'capture-' + (poseId || id), observed_at: null,
    available_at: null, valid_until: null, model_id: 'pose-model', source_id: 'pose-specialist', status: 'candidate',
    keypoints: [{ id: 'head', x: 110, y: 60 }, { id: 'hip', x: 120, y: 220 }, { id: 'hidden', x: 10, y: 10, visible: false }],
    skeleton_edges: [['head', 'hip'], ['head', 'hidden']] };
  return { schema: 'home-apartment-perception-1', scope, source_epoch: null,
    session_state: stopped ? 'stopped' : 'recording', frame, pose2d: pose,
    models: [{ family: 'Pose', model_id: 'pose-model', model_revision: 'r1', source_id: 'pose-specialist', status: 'candidate',
      observed_at: 99, available_at: 99.2, valid_until: 102, frame_id: id, capture_id: 'capture-' + id, result: { joints: 2 } },
    ...(jev ? [{ family: 'JEV', model_id: 'jev-phone-model', model_revision: 'r2', source_id: 'jev-specialist', status: 'candidate',
      reason: 'unscored', observed_at: 80, available_at: 90, valid_until: 95, frame_id: 'earlier-evidence',
      result: { label: 'phone_use', evidence_window: { start_s: 75, end_s: 80 } } }] : [])],
    room_pose: room ? { status: 'admitted', qualification: 'diagnostic_alignment', frame_id: id,
      observed_at: 99, available_at: 99.2, valid_until: 102, scope,
      registration: { status: 'applicable', camera_id: 'den', camera_to_room_digest: 'registered-a' },
      joints: [{ id: 'head', position_m: [1, 2, 1.7] }, { id: 'hip', position_m: [1, 2, .9] }],
      edges: [['head', 'hip']], anchor_joint_id: 'hip' } : null };
}
async function mount({ appSource = source(), appEngine = engine(), loadLayer = null, decode = null, onOpenLab = () => {}, capability = null } = {}) {
  selectedSite = 'victoria'; w.__APT_ENGINE = appEngine;
  const decoded = [], layerCalls = [], layerLoads = [];
  decoder = decode || (async (_value, { frame }) => {
    const owned = { url: 'blob:test-' + frame.media_id, width: frame.width, height: frame.height, disposed: 0,
      dispose() { owned.disposed++; } };
    decoded.push(owned); return owned;
  });
  const layerModule = { attachApartmentPerceptionLayer(owner, scope, options) {
    const layer = { owner, scope, options, following: false, disposed: 0, views: [], followCalls: [],
      draw(view) { layer.views.push(view); if (!view?.follow?.allowed) layer.following = false; },
      screenAnchor: () => null, readback: () => ({ following: layer.following }),
      setFollowing(value) { layer.followCalls.push(value); layer.following = value === true && layer.views.at(-1)?.follow?.allowed === true; return layer.following; },
      dispose() { layer.disposed++; layer.following = false; } };
    layerCalls.push(layer); return layer;
  } };
  const loader = loadLayer || (() => { layerLoads.push(1); return Promise.resolve(layerModule); });
  const container = w.document.createElement('div'); w.document.body.appendChild(container);
  const root = createRoot(container);
  let props = { engine: appEngine, source: appSource, loadLayer: loader, onOpenLab, ...(capability ? { capability } : {}) };
  const render = async extra => { props = { ...props, ...extra }; if (extra?.engine) w.__APT_ENGINE = extra.engine;
    await act(async () => root.render(React.createElement(w.HomeApartmentDenPerception, props))); await flush(); };
  await render({});
  let unmounted = false;
  return { container, appSource, appEngine, decoded, layerCalls, layerLoads, layerModule, render,
    async unmount() { if (!unmounted) { unmounted = true; await act(async () => root.unmount()); container.remove(); appEngine.renderer.domElement.remove(); } },
    async emit(value, index = appSource.subscriptions.length - 1) {
      const sub = appSource.subscriptions[index]; assert.ok(sub, 'explicit source subscription exists');
      await act(async () => sub.onUpdate(value)); await flush();
    },
  };
}
async function connect(m, id = 'den-session-one') {
  await click(m.container, 'View perception');
  await choose(m.container, 'Den Lab session', id);
  await click(m.container, 'Connect');
  assert.ok(m.appSource.subscriptions.length);
  return m.appSource.subscriptions.at(-1);
}

test('ordinary panel does not auto-load or acquire; discovery filters exact Den sessions and Connect chooses arm B explicitly', async () => {
  const m = await mount();
  try {
    assert.equal(m.appSource.listCalls.length, 0); assert.equal(m.appSource.subscriptions.length, 0); assert.equal(m.appSource.reads.length, 0); assert.equal(m.layerLoads.length, 0);
    await click(m.container, 'View perception');
    assert.equal(m.appSource.listCalls.length, 1); assert.equal(m.appSource.listCalls[0].siteId, 'victoria');
    const select = m.container.querySelector('select[aria-label="Den Lab session"]');
    assert.deepEqual([...select.options].map(option => option.value), ['', 'den-session-one', 'den-session-two', 'den-session-three']);
    assert.equal(select.value, '', 'multiple active sessions require explicit selection');
    assert.equal(button(m.container, 'Connect').disabled, true); assert.equal(m.appSource.subscriptions.length, 0);
    await choose(m.container, 'Den Lab session', 'den-session-one'); await click(m.container, 'Connect');
    assert.equal(m.appSource.subscriptions.length, 1); assert.equal(m.appSource.subscriptions[0].arm, 'B');
    assert.equal(m.appSource.subscriptions[0].scope.sessionId, 'den-session-one'); assert.equal(m.appSource.subscriptions[0].scope.generation, 7);
    assert.deepEqual(m.appSource.forbidden, []);
  } finally { await m.unmount(); }
});

test('actual decoded image and supplied 2D overlay render with JEV below, original model/source times and unavailable 3D', async () => {
  const m = await mount();
  try {
    const sub = await connect(m); await m.emit(observations(sub));
    const image = m.container.querySelector('img'); assert.ok(image); assert.equal(image.getAttribute('src'), 'blob:test-media-f1');
    assert.match(image.alt, /f1/);
    const overlay = m.container.querySelector('svg[aria-label="Exact-frame pose overlay"]'); assert.ok(overlay);
    assert.equal(overlay.querySelectorAll('circle').length, 2); assert.equal(overlay.querySelectorAll('line').length, 1);
    assert.equal(overlay.querySelector('circle').getAttribute('cx'), '110', 'supplied pixels are not normalized or guessed');
    const jev = m.container.querySelector('section[aria-label="JEV activity"]'); assert.ok(jev);
    assert.ok(image.compareDocumentPosition(jev) & w.Node.DOCUMENT_POSITION_FOLLOWING, 'JEV appears below the camera feed');
    assert.match(jev.textContent, /phone use/); assert.match(jev.textContent, /different-frame/);
    const results = m.container.querySelector('section[aria-label="Model results"]');
    assert.match(results.textContent, /jev-phone-model/); assert.match(results.textContent, /jev-specialist/);
    assert.match(results.textContent, /1970-01-01T00:01:20\.000Z/); assert.match(results.textContent, /1970-01-01T00:01:30\.000Z/);
    assert.match(results.textContent, /1970-01-01T00:01:35\.000Z/); assert.match(results.textContent, /earlier-evidence/);
    assert.match(m.container.textContent, /Age unavailable/); assert.match(m.container.textContent, /3D room position unavailable/);
    assert.equal(button(m.container, 'Follow').disabled, true); assert.equal(m.appSource.reads.length, 1);
    assert.equal(m.layerCalls[0].views.at(-1).follow.allowed, false);
  } finally { await m.unmount(); }
});

test('an exact decoded frame with no supported producer joints says so without drawing anatomy',async()=>{
  const m=await mount();
  try{
    const sub=await connect(m),packet=observations(sub);
    packet.pose2d.keypoints=[];packet.pose2d.skeleton_edges=[];
    await m.emit(packet);
    assert.ok(m.container.querySelector('img'));
    assert.match(m.container.textContent,/No supported person joints in this analyzed frame/);
    assert.equal(m.container.querySelector('svg[aria-label="Exact-frame pose overlay"]'),null);
    packet.pose2d.keypoints=[{id:'head',x:110,y:60}];
    await m.emit(packet);
    assert.doesNotMatch(m.container.textContent,/No supported person joints in this analyzed frame/);
    assert.equal(m.container.querySelectorAll('svg[aria-label="Exact-frame pose overlay"] circle').length,1);
    assert.equal(button(m.container,'Follow').disabled,true);
  }finally{await m.unmount();}
});

test('owned session dropdown reflects validated analysis transitions without discovery loops or mutating rows',async()=>{
  const appSource=source();appSource.rows=sessions().map(row=>row.id==='den-session-one'?{...row,state:'starting'}:row);
  let current={owned:true,status:'starting',session:copy(appSource.rows.find(row=>row.id==='den-session-one'))},listener;
  appSource.analysisState=()=>copy(current);appSource.watchAnalysis=fn=>{listener=fn;fn(copy(current));return()=>{listener=null;};};
  const before=copy(appSource.rows),m=await mount({appSource});
  try{
    await click(m.container,'View perception');await choose(m.container,'Den Lab session','den-session-one');
    const select=m.container.querySelector('select[aria-label="Den Lab session"]');
    assert.match(select.selectedOptions[0].textContent,/starting/);
    for(const state of ['recording','stopped']){
      current={owned:state!=='stopped',status:state,session:{...current.session,state}};
      await act(async()=>listener(copy(current)));await flush();
      assert.equal(select.value,'den-session-one');assert.match(select.selectedOptions[0].textContent,new RegExp(state));
    }
    assert.deepEqual(appSource.rows,before);assert.equal(appSource.listCalls.length,1);
    assert.equal(appSource.subscriptions.length,0);assert.deepEqual(appSource.forbidden,[]);
  }finally{await m.unmount();}
  assert.equal(listener,null);
});

test('approximate supplied room joints have a persistent canvas badge and analyzed-frame note with Follow disabled',async()=>{
  const packet=JSON.parse(fs.readFileSync(path.join(__dirname,'../tools/fixtures/apartment-live-repair-20261018/approximate-room-pose-authored.json'),'utf8'));
  const m=await mount();
  try{
    const sub=await connect(m),value=observations(sub);
    packet.approximate_pose.scope=copy(sub.scope);packet.frame.generation=sub.scope.generation;
    packet.approximate_pose.provenance.media_generation=sub.scope.generation;
    value.frame=copy(packet.frame);value.source_epoch=packet.frame.source_epoch;value.pose2d=null;
    value.approximate_room_pose=packet;value.approximate_receipt_elapsed_s=.1;
    await m.emit(value);
    const badge=m.container.querySelector('[data-apartment-approximate-badge="1"]');assert.ok(badge);
    assert.equal(badge.textContent,'Approximate room placement');
    assert.match(m.container.querySelector('[data-apartment-approximate-note="1"]').textContent,/Approximate room placement/);
    assert.equal(button(m.container,'Follow').disabled,true);
    assert.equal(m.layerCalls[0].views.at(-1).approximateRoomPose.joints.length,1);
    value.session_state='stopped';await m.emit(value);
    assert.equal(m.container.querySelector('[data-apartment-approximate-badge="1"]'),null);
  }finally{await m.unmount();}
});

test('changed or unknown approximate scene check is visible while geometry stays withdrawn and empty person wording remains honest',async()=>{
  const m=await mount();
  try{
    const sub=await connect(m);
    for(const reason of ['approximate_mount_signature_changed','approximate_mount_signature_unknown']){
      const value=observations(sub);value.pose2d.keypoints=[];value.pose2d.skeleton_edges=[];
      value.approximate_room_pose={schema:'home-apartment-approximate-room-pose-1',renderable:false,reason};
      await m.emit(value);
      assert.match(m.container.querySelector('[data-apartment-approximate-unavailable="1"]').textContent,
        reason.endsWith('changed')?/Room or camera scene check changed/:/Room or camera scene check unavailable/);
      assert.equal(m.container.querySelector('[data-apartment-approximate-badge="1"]'),null);
      assert.equal(m.layerCalls[0].views.at(-1).approximateRoomPose.renderable,false);
      assert.equal(button(m.container,'Follow').disabled,true);
      assert.match(m.container.textContent,/No supported person joints in this analyzed frame/);
    }
  }finally{await m.unmount();}
});

test('pre-frame metric input refusal is visible without an invented image, pose, clock or geometry',async()=>{
  const m=await mount();
  try{
    const sub=await connect(m),value=observations(sub);value.frame=null;value.pose2d=null;
    value.models=[{family:'metric_input',source_id:'apartment_viewing_metric',status:'unavailable',reason:'bundled_rgb_not_inside_original_anchors',
      observed_at:null,available_at:null,valid_until:null,result:{summary:'Metric input unavailable'}}];
    await m.emit(value);
    assert.match(m.container.querySelector('[data-apartment-approximate-unavailable="1"]').textContent,/bundled rgb not inside original anchors/);
    assert.equal(m.container.querySelector('img'),null);assert.equal(m.container.querySelector('svg'),null);
    assert.equal(m.layerCalls[0].views.at(-1).approximateRoomPose.renderable,false);
    assert.equal(button(m.container,'Follow').disabled,true);
  }finally{await m.unmount();}
});

test('the DOM retains the exact A image/pose and its unknown age while B decodes, then never paints A on B', async () => {
  const pending = deferred(), owned = [];
  const image = frame => { const value = { url: 'blob:held-' + frame.frame_id, width: frame.width, height: frame.height,
    disposed: 0, dispose() { value.disposed++; } }; owned.push(value); return value; };
  const m = await mount({ decode: async (_value, { frame }) => frame.frame_id === 'f2' ? pending.promise : image(frame) });
  try {
    const sub = await connect(m); await m.emit(observations(sub));
    const aURL = m.container.querySelector('img').getAttribute('src');
    const next = observations(sub, { id: 'f2' });
    next.pose2d.keypoints[0].x = 210; next.pose2d.observed_at = 98; next.pose2d.available_at = 99; next.pose2d.valid_until = 102;
    next.frame.captured_at = 99.5;
    await m.emit(next);
    assert.equal(m.container.querySelector('img').getAttribute('src'), aURL);
    assert.equal(m.container.querySelector('svg circle').getAttribute('cx'), '110');
    assert.match(m.container.textContent, /preceding analyzed frame/);
    assert.match(m.container.textContent, /Frame f1.*Pose: Age unavailable/);
    assert.match(m.container.textContent, /Captured 1970-01-01T00:01:39\.000Z/);
    assert.match(m.container.querySelector('section[aria-label="Model results"]').textContent, /different-frame.*f2/);
    assert.equal(owned[0].disposed, 0);
    const bImage = image(next.frame);
    await act(async () => pending.resolve(bImage)); await flush();
    assert.equal(m.container.querySelector('img').getAttribute('src'), bImage.url);
    assert.equal(m.container.querySelector('svg circle').getAttribute('cx'), '210');
    assert.match(m.container.textContent, /Captured 1970-01-01T00:01:39\.500Z/);
    assert.equal(owned[0].disposed, 1);
    const disjoint = observations(sub, { id: 'f3', poseId: 'f2' });
    await m.emit(disjoint);
    assert.match(m.container.querySelector('img').alt, /f3/);
    assert.equal(m.container.querySelector('svg'), null, 'new image cannot inherit the preceding pose');
  } finally { await m.unmount(); }
  for (const value of owned) assert.equal(value.disposed, 1);
});

// APT-P05: Follow stays off until a capability reports continuous registered
// tracking (no producer does yet); the layer mechanics below run behind it.
const continuousTracking = { perception: { supported: true, variant: 'exact-frame', camera_id: 'den' },
  body: { supported: true, placement: 'registered', tracking: 'continuous_registered' } };
test('Follow stays disabled without continuous registered tracking even for admitted joints', async () => {
  const m = await mount();
  try {
    const sub = await connect(m); await m.emit(observations(sub, { room: true }));
    assert.match(m.container.textContent, /Diagnostic room alignment.*no lighting authority/);
    assert.equal(button(m.container, 'Follow').disabled, true);
    assert.match(m.container.textContent, /Follow is off until continuous registered tracking exists/);
  } finally { await m.unmount(); }
});

test('admitted supplied joints enable the public layer Follow control and pointer/wheel cancel it', async () => {
  const m = await mount({ capability: continuousTracking });
  try {
    const sub = await connect(m); await m.emit(observations(sub, { room: true }));
    assert.match(m.container.textContent, /Diagnostic room alignment.*no lighting authority/);
    const layer = m.layerCalls[0]; assert.equal(layer.views.at(-1).roomPose.joints.length, 2);
    assert.equal(button(m.container, 'Follow').disabled, false);
    await click(m.container, 'Follow'); assert.equal(layer.following, true); assert.equal(button(m.container, 'Follow').getAttribute('aria-pressed'), 'true');
    await act(async () => m.appEngine.renderer.domElement.dispatchEvent(new w.Event('pointerdown', { bubbles: true }))); await flush();
    assert.equal(layer.following, false); assert.equal(button(m.container, 'Follow').getAttribute('aria-pressed'), 'false');
    await click(m.container, 'Follow'); assert.equal(layer.following, true);
    await act(async () => m.appEngine.renderer.domElement.dispatchEvent(new w.Event('wheel'))); await flush();
    assert.equal(layer.following, false);
    m.appSource.now = 103; await tick(250);
    assert.equal(button(m.container, 'Follow').disabled, true); assert.equal(layer.views.at(-1).roomPose.admitted, false);
  } finally { await m.unmount(); }
});

test('Return releases this viewer without stopping the Lab session; stopped selection stays recorded', async () => {
  const m = await mount();
  try {
    const live = await connect(m); await m.emit(observations(live, { room: true }));
    await click(m.container, 'Close');
    assert.ok(button(m.container, 'View perception')); assert.equal(m.container.querySelector('img'), null);
    assert.equal(live.signal.aborted, true); assert.equal(live.off, 1); assert.equal(m.decoded[0].disposed, 1); assert.equal(m.layerCalls[0].disposed, 1);
    assert.equal(m.appSource.rows.find(row => row.id === 'den-session-one').state, 'recording'); assert.deepEqual(m.appSource.forbidden, []);
    await click(m.container, 'View perception'); await choose(m.container, 'Den Lab session', 'den-session-two'); await click(m.container, 'Connect');
    const stopped = m.appSource.subscriptions.at(-1); await m.emit(observations(stopped, { room: true, stopped: true }));
    assert.ok(m.container.querySelector('img')); assert.match(m.container.textContent, /recorded/);
    assert.equal(button(m.container, 'Follow').disabled, true); assert.equal(m.layerCalls.at(-1).views.at(-1).roomPose.admitted, false);
  } finally { await m.unmount(); }
});

test('pending discovery is aborted by Return, logout and unmount; late rows cannot attach a session', async () => {
  for (const action of ['return', 'logout', 'unmount']) {
    const pending = deferred(), appSource = source({ list: () => pending.promise });
    const m = await mount({ appSource });
    try {
      await click(m.container, 'View perception'); assert.equal(appSource.listCalls.length, 1);
      if (action === 'return') await click(m.container, 'Close');
      else if (action === 'logout') { await act(async () => w.dispatchEvent(new w.Event('home:logout'))); await flush(); }
      else await m.unmount();
      assert.equal(appSource.listCalls[0].signal.aborted, true, action);
      await act(async () => pending.resolve(sessions())); await flush();
      assert.equal(appSource.subscriptions.length, 0); assert.equal(m.container.querySelector('select'), null, action);
      assert.equal(m.container.querySelector('img'), null);
    } finally { await m.unmount(); }
  }
});

test('Return, logout and unmount abort a pending decode and release its eventual private image without adoption', async () => {
  for (const action of ['return', 'logout', 'unmount']) {
    const pending = deferred(); let decodeSignal = null;
    const owned = { url: 'blob:late-' + action, width: 640, height: 480, disposed: 0, dispose() { owned.disposed++; } };
    const m = await mount({ decode: async (_value, options) => { decodeSignal = options.signal; return pending.promise; } });
    try {
      const sub = await connect(m); await m.emit(observations(sub)); assert.ok(decodeSignal); assert.equal(decodeSignal.aborted, false);
      if (action === 'return') await click(m.container, 'Close');
      else if (action === 'logout') { await act(async () => w.dispatchEvent(new w.Event('home:logout'))); await flush(); }
      else await m.unmount();
      assert.equal(decodeSignal.aborted, true, action); assert.equal(sub.signal.aborted, true); assert.equal(sub.off, 1);
      assert.equal(m.layerCalls[0].disposed, 1);
      await act(async () => pending.resolve(owned)); await flush();
      assert.equal(owned.disposed, 1); assert.equal(m.container.querySelector('img'), null, action);
      if (action !== 'unmount') assert.ok(button(m.container, 'View perception'));
    } finally { await m.unmount(); }
  }
});

test('same-pins engine replacement aborts private reads, clears imagery and disposes the old layer', async () => {
  const pending = deferred(); let readCount = 0;
  const appSource = source({ read: () => ++readCount === 1 ? Promise.resolve({ bytes: new w.Uint8Array([1]), type: 'image/png' }) : pending.promise });
  const m = await mount({ appSource }); const replacement = engine();
  try {
    const sub = await connect(m); await m.emit(observations(sub)); await m.emit(observations(sub, { id: 'f2' }));
    const read = appSource.reads.at(-1); assert.equal(read.signal.aborted, false); assert.ok(m.container.querySelector('img'));
    await m.render({ engine: replacement });
    assert.equal(sub.signal.aborted, true); assert.equal(sub.off, 1); assert.equal(read.signal.aborted, true);
    assert.equal(m.container.querySelector('img'), null); assert.ok(button(m.container, 'View perception'));
    assert.equal(m.decoded[0].disposed, 1); assert.equal(m.layerCalls[0].disposed, 1);
    await act(async () => pending.resolve({ bytes: new w.Uint8Array([1]), type: 'image/png' })); await flush();
    assert.equal(m.container.querySelector('img'), null); assert.equal(m.decoded.length, 1);
  } finally { await m.unmount(); replacement.renderer.domElement.remove(); }
});

test('source replacement disconnects the old exact-session observer before any new source is explicitly connected', async () => {
  const m = await mount(), nextSource = source();
  try {
    const sub = await connect(m); await m.emit(observations(sub, { room: true }));
    await m.render({ source: nextSource });
    assert.equal(sub.signal.aborted, true); assert.equal(sub.off, 1);
    assert.equal(m.container.querySelector('img'), null); assert.equal(m.layerCalls[0].disposed, 1);
    assert.equal(nextSource.subscriptions.length, 0); assert.equal(nextSource.reads.length, 0);
  } finally { await m.unmount(); }
});

test('session and arm changes disconnect previous reads; wrong generation and selected-home changes clear the viewer', async () => {
  const m = await mount();
  try {
    const first = await connect(m); await m.emit(observations(first, { room: true }));
    await choose(m.container, 'Perception pipeline arm', 'A');
    assert.equal(first.signal.aborted, true); assert.equal(first.off, 1); assert.equal(m.container.querySelector('img'), null);
    await click(m.container, 'Connect');
    const second = m.appSource.subscriptions.at(-1); assert.equal(second.arm, 'A'); await m.emit(observations(second));
    await choose(m.container, 'Den Lab session', 'den-session-three');
    assert.equal(second.signal.aborted, true); assert.equal(m.container.querySelector('img'), null);
    await click(m.container, 'Connect');
    const third = m.appSource.subscriptions.at(-1); assert.equal(third.scope.generation, 9);
    const wrong = observations(third); wrong.scope.generation = 10;
    await m.emit(wrong); assert.equal(m.container.querySelector('img'), null); assert.match(m.container.textContent, /refused.*source scope mismatch/);
    await click(m.container, 'Connect'); const fourth = m.appSource.subscriptions.at(-1); await m.emit(observations(fourth));
    await act(async () => { selectedSite = 'echo'; for (const listener of [...registryListeners]) listener(); }); await flush();
    assert.equal(fourth.signal.aborted, true); assert.equal(m.container.querySelector('img'), null);
  } finally { selectedSite = 'victoria'; await m.unmount(); }
});

test('a failed frame keeps independent JEV metadata and the next exact frame recovers without Connect or source acquisition', async () => {
  let available = false;
  const appSource = source({ read: () => available ? Promise.resolve({ bytes: new w.Uint8Array([1]), type: 'image/png' }) : Promise.reject(Error('authored media failure')) });
  const m = await mount({ appSource });
  try {
    const first = await connect(m); await m.emit(observations(first));
    assert.equal(m.container.querySelector('img'), null); assert.match(m.container.textContent, /frame read or decode failed/);
    const jev=m.container.querySelector('section[aria-label="JEV activity"]');
    assert.match(jev.textContent,/phone use/);assert.match(jev.textContent,/stale/);
    assert.match(m.container.textContent,/jev-phone-model/);assert.match(m.container.textContent,/1970-01-01T00:01:30.000Z/);
    assert.equal(first.signal.aborted,false);assert.equal(first.off,0);assert.equal(appSource.subscriptions.length,1);
    available=true;await m.emit(observations(first,{id:'f2'}));
    const image=m.container.querySelector('img');assert.ok(image);assert.equal(image.getAttribute('src'),'blob:test-media-f2');
    assert.match(image.alt,/f2/);assert.ok(m.container.querySelector('svg[aria-label="Exact-frame pose overlay"]'));
    assert.equal(appSource.subscriptions.length,1,'next frame recovers through the original subscription');
    assert.equal(appSource.reads.length,2);assert.deepEqual(appSource.forbidden,[]);
  } finally { await m.unmount(); }
});

test('a layer import completing after Return, engine replacement or unmount cannot attach', async () => {
  for (const action of ['return', 'engine', 'unmount']) {
    const pending = deferred(); const m = await mount({ loadLayer: () => pending.promise }); let replacement = null;
    try {
      const sub = await connect(m); await m.emit(observations(sub, { room: true }));
      if (action === 'return') await click(m.container, 'Close');
      else if (action === 'engine') { replacement = engine(); await m.render({ engine: replacement }); }
      else await m.unmount();
      await act(async () => pending.resolve(m.layerModule)); await flush();
      assert.equal(m.layerCalls.length, 0, action); assert.equal(sub.signal.aborted, true);
      assert.equal(m.container.querySelector('img'), null);
    } finally { await m.unmount(); replacement?.renderer.domElement.remove(); }
  }
});

test('no-session path offers a working Open in Lab and invalid timestamp metadata cannot crash the panel', async () => {
  const appSource = source(); appSource.rows = []; const opened = [];
  const m = await mount({ appSource, onOpenLab: intent => opened.push(intent) });
  try {
    await click(m.container, 'View perception'); assert.match(m.container.textContent, /No existing Den session/);
    assert.equal(button(m.container, 'Back to Home / Perception Lab'), undefined, 'the dead control is gone');
    await click(m.container, 'Open in Lab'); assert.deepEqual(copy(opened), [{ session_id: null, t_s: null }]);
    appSource.rows = sessions(); await click(m.container, 'Refresh sessions'); await choose(m.container, 'Den Lab session', 'den-session-one'); await click(m.container, 'Connect');
    const sub = appSource.subscriptions.at(-1), data = observations(sub);
    data.models[0].observed_at = 1e308; data.models[0].available_at = 1e308; data.models[0].valid_until = 1e308;
    await m.emit(data); assert.match(m.container.textContent, /Model results/); assert.ok(m.container.querySelector('img'));
    assert.match(m.container.querySelector('section[aria-label="Model results"]').textContent, /unavailable/);
  } finally { await m.unmount(); }
});
