/* APT-P01..P06 camera dock. The pure dock model, the real dock JSX with the
 * real projection and player in jsdom, and the lighting-brain panel it must
 * leave unchanged. Synthetic data only: Den's contract fixtures and authored
 * envelopes. No Start, trial, camera or model session is ever made; the
 * source fixture throws if any acquisition is attempted.
 *
 * HANDOFF acceptance examples covered here: "Frame honesty", "JEV abstains"
 * and "No session" (docs/design/apartment-unified/HANDOFF.md). */
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
const ROOT = path.join(__dirname, '..'), src = path.join(ROOT, 'app/src'), DEN = path.join(ROOT, 'docs/contracts/den');
const Vocabulary = require('../app/src/home-den-reason-vocabulary.js');
const Dock = require('../app/src/home-apartment-perception-dock-model.js');
const denFixture = name => JSON.parse(fs.readFileSync(path.join(DEN, 'fixtures', name), 'utf8'));
const copy = value => JSON.parse(JSON.stringify(value));

/* ---------- vocabulary ---------- */
test('the browser vocabulary is a verbatim copy of Den\'s contract and renders every example row', () => {
  const contract = JSON.parse(fs.readFileSync(path.join(DEN, 'reason-vocabulary.json'), 'utf8'));
  assert.deepEqual(copy(Vocabulary.VOCABULARY), contract, 'regenerate home-den-reason-vocabulary.js from the contract');
  for (const row of denFixture('reason-rows.json').rows) {
    const shown = Vocabulary.describe(row.group, row.code);
    assert.equal(shown.text, row.shown, `${row.group}:${row.code}`);
    assert.equal(shown.severity || undefined, row.severity, `${row.group}:${row.code} severity`);
  }
  assert.equal(Vocabulary.text('activity_loop.health', 'jev_released_resuming'), 'JEV released · resuming', 'aliases resolve');
  assert.equal(Vocabulary.text('legacy.desk_preview', 'preview_expired'), 'Preview expired (retired preview)');
  const unknown = Vocabulary.describe('perception.age', 'made_up_code');
  assert.equal(unknown.text, 'Unavailable'); assert.equal(unknown.code, 'made_up_code'); assert.equal(unknown.known, false);
});

/* ---------- pure dock model ---------- */
test('the dock follows the perception capability, not the site id', () => {
  const victoria = denFixture('home-den-capabilities-victoria.json');
  assert.equal(Dock.perceptionCapability({ siteId: 'victoria' }).variant, 'exact-frame');
  assert.equal(Dock.perceptionCapability({ siteId: 'victoria' }).source, 'interim_default');
  assert.equal(Dock.perceptionCapability({ siteId: 'echo' }).variant, 'none', 'no capability, no dock');
  const supplied = Dock.perceptionCapability({ siteId: 'victoria', capabilities: victoria });
  assert.equal(supplied.source, 'capability'); assert.equal(supplied.variant, 'exact-frame');
  assert.equal(supplied.body.tracking, null, 'no producer supplies continuous registered tracking');
  const off = copy(victoria); off.perception.supported = false; off.perception.reason = 'camera_no_viewing_source';
  assert.equal(Dock.perceptionCapability({ siteId: 'victoria', capabilities: off }).variant, 'none', 'a supplied capability wins over the home');
  const echo = copy(victoria); echo.site_id = 'echo';
  assert.equal(Dock.perceptionCapability({ siteId: 'echo', capabilities: echo }).variant, 'exact-frame', 'any home with the capability gets the dock');
});

test('frame timing reads only Den\'s frame-timing block: an unqualified clock never shows a number', () => {
  const block = denFixture('home-den-capabilities-victoria.json').perception.frame;
  const view = { frame: { descriptor: { captured_at: block.captured_at }, age: block.age, status: 'current-source' },
    pose2d: { renderable: true, age: block.age, originalTimes: { observed_at: block.observed_at, available_at: block.available_at, valid_until: block.valid_until } } };
  const timing = Dock.frameTiming(view);
  assert.equal(timing.qualified, false); assert.equal(timing.age.seconds, null);
  assert.equal(timing.text, 'Age unavailable · clock not qualified');
  assert.doesNotMatch(timing.text, /\d+\s*s|min/, 'no prototype age band');
  assert.equal(timing.valid_until, block.valid_until); assert.equal(timing.captured_at, block.captured_at);
  assert.equal(Dock.frameState(view, { frame: {} }, timing), 'unqualified');
  const current = copy(view); current.pose2d.age = { status: 'current', seconds: 1.25, uncertainty_s: .05, reason: null };
  assert.equal(Dock.frameTiming(current).text, 'Analyzed 1.3 s ago');
  const stale = copy(view); stale.pose2d.age = { status: 'stale', seconds: null, uncertainty_s: .05, reason: 'expired' };
  const staleTiming = Dock.frameTiming(stale);
  assert.equal(staleTiming.text, 'Expired'); assert.equal(Dock.frameState(stale, { frame: {} }, staleTiming), 'stale');
  const replay = copy(view); replay.frame.status = 'recorded';
  assert.equal(Dock.frameState(replay, { frame: {} }, Dock.frameTiming(replay)), 'replay');
  assert.equal(Dock.replayLabel({ started_at: '2026-10-16T21:04:12Z' }), 'Replay · recorded Oct 16, 2026');
  assert.equal(Dock.replayLabel({}), 'Replay · recording date unavailable');
});

test('detector, pose and JEV stay three separate values; an abstention is explicit and proposes nothing', () => {
  const view = { pose2d: { renderable: true, keypoints: [], boxes: [{ x: 1, y: 1, w: 9, h: 9, confidence: .81 }], persons: [] },
    models: [{ family: 'JEV', model_id: 'jev-1', status: 'candidate', result: { activity: 'insufficient_evidence', reason: 'person_partly_out_of_frame',
      usable_frames: 2, frames: 8, window_s: 8, min_usable: 4 } }] };
  const detector = Dock.detector(view), pose = Dock.pose(view), jev = Dock.jev(view, null);
  assert.equal(detector.text, 'Detector found 1 person'); assert.deepEqual(copy(detector.scores), [.81]);
  assert.equal(pose.text, 'Pose: no supported joints');
  assert.equal(jev.status, 'abstained'); assert.equal(jev.label, 'Abstained'); assert.equal(jev.probability, null);
  assert.deepEqual(copy(jev.reasons), ['Not enough to tell', 'person partly out of frame']);
  assert.deepEqual(copy(jev.window), { window_s: 8, frames: 8, usable: 2, min_usable: 4 });
  const steps = Dock.trace({ frameState: 'unqualified', detector, jev, detail: 'simple', connected: true, frameId: 'f9' });
  assert.deepEqual(steps.map(step => step.key), ['seen', 'interpreted', 'proposed', 'did']);
  assert.equal(steps[1].value, 'Abstained'); assert.equal(steps[2].value, 'Nothing'); assert.match(steps[2].reason, /JEV abstained/);
  assert.equal(steps[3].value, 'Not available'); assert.match(steps[3].reason, /No execution record/);
  const judged = Dock.jev({ pose2d: { renderable: true, persons: [{ judgment: { status: 'available', judgment: { activity: 'reading', confidence: .7,
    probabilities: { reading: .62, computer_work: .2, phone_use: .08, resting: .05, transition: .03, other: .02, insufficient_evidence: 0 }, model_version: 'jev-1.13.0' } } }] } }, null);
  assert.equal(judged.label, 'Reading'); assert.equal(judged.distribution[0].activity, 'reading'); assert.equal(judged.probability, .62);
  const proposed = Dock.trace({ frameState: 'current', detector, jev: judged, detail: 'detailed', connected: true })[2];
  assert.equal(proposed.value, 'Nothing', 'no proposal record exists, so none is invented');
  assert.match(proposed.reason, /no perception proposal record/);
  const health = Dock.jev({ models: [] }, { jev: { ok: false, detail: 'jev_egress_blocked_indeterminate' } }).health;
  assert.equal(health.text, 'JEV paused: an earlier request has an unknown outcome'); assert.equal(health.severity, 'warn');
});

test('the room body preserves every supplied viewing joint, with missing joints left missing', () => {
  const joint = (id, p = [id, 0, 1]) => ({ id, position_m: p, visible: true, error_bound_m: null });
  const placement = { renderable: true, joints: [0, 3, 4, 5, 6, 7, 9, 11].map(id => joint(id)), edges: [[5, 6], [5, 7], [7, 9], [5, 11]] };
  const body = Dock.bodyInRoom(placement);
  assert.deepEqual(body.joints.map(row => row.id), [0, 3, 4, 5, 6, 7, 9, 11]); assert.deepEqual(copy(body.edges), placement.edges);
  assert.equal(body.head, 'nose'); assert.equal(body.label, 'One analyzed frame, not tracked');
  const noNose = Dock.bodyInRoom({ renderable: true, joints: [3, 4, 5].map(id => joint(id)), edges: [[5, 6]] });
  assert.deepEqual(noNose.joints.map(row => row.id), [3, 4, 5], 'supplied ears remain separate; no midpoint is invented');
  assert.deepEqual(copy(noNose.edges), [], 'no shoulder line with one shoulder');
  assert.equal(noNose.text, '3 of 17 supplied joints · shoulders 1 of 2 · head from ears');
  const view = { status: 'available', approximateRoomPose: placement, roomPose: { admitted: false } };
  const narrowed = Dock.roomLayerView(view);
  assert.deepEqual(narrowed.approximateRoomPose.joints.map(row => row.id), [0, 3, 4, 5, 6, 7, 9, 11]);
  assert.equal(view.approximateRoomPose.joints.length, 8, 'the view itself is not mutated');
});

test('Open in Lab carries the session and the frame time inside that session, and says what it cannot carry', () => {
  const session = { id: 'den-session-one', camera_id: 'den', started_at: '2026-10-20T10:00:00Z', duration_s: 120 };
  const start = Date.parse(session.started_at) / 1000;
  const intent = Dock.labIntent({ session, frame: { frame_id: 'f1', captured_at: start + 42.26 } });
  assert.equal(intent.session_id, 'den-session-one'); assert.equal(intent.t_s, 42.3); assert.equal(intent.frame_id, 'f1');
  assert.equal(intent.selection, null); assert.match(intent.selection_reason, /no person selection/);
  assert.equal(Dock.labIntent({ session, frame: { captured_at: start + 500 } }).t_s, null, 'outside the session is refused, not clamped');
  assert.equal(Dock.labIntent({ session, frame: null }).t_s, null);
  assert.equal(Dock.labIntent({ session: null }).available, false);
});

test('viewing geometry omits hidden, missing or invalid positions without creating limb endpoints',()=>{
  const joints=Array.from({length:17},(_,id)=>({id,position_m:[id,0,1],visible:true}));
  joints[7].visible=false;joints[9].position_m=null;joints[15].position_m=[NaN,0,1];
  const body=Dock.bodyInRoom({renderable:true,joints,edges:[[5,7],[7,9],[13,15],[11,12],[12,14],[14,16]]});
  assert.equal(body.joints.length,14);assert.deepEqual(copy(body.edges),[[11,12],[12,14],[14,16]]);
  assert.equal(body.joints.some(joint=>[7,9,15].includes(joint.id)),false);
});

test('rolling model history deduplicates age ticks, stays bounded and clears foreign session/epoch context',()=>{
  const scope={siteId:'victoria',sessionId:'loop-one',generation:0};
  const row={family:'Qwen',source_id:'source-one',model_id:'qwen-one',observed_at:99,available_at:100,valid_until:101,
    frame_id:'f1',result:{activity:'reading'},status:'candidate',age:{status:'current',seconds:1}};
  const view={status:'available',scope,sourceEpoch:'producer-one',models:[row]};
  let history=Dock.modelUpdates([],view);assert.equal(history.length,1);
  assert.equal(history[0].model.valid_until,101);assert.equal(history[0].model.age,undefined,'historical rows cannot retain a frozen current-age claim');
  const same=Dock.modelUpdates(history,{...view,models:[{...row,age:{status:'stale',seconds:10}}]});
  assert.equal(same,history,'clock ticks alone do not invent model results');
  for(let i=0;i<25;i++)history=Dock.modelUpdates(history,{...view,models:[{...row,frame_id:'f'+(i+2),observed_at:100+i}]});
  assert.equal(history.length,20);assert.equal(history[0].model.frame_id,'f26');
  history=Dock.modelUpdates(history,{...view,scope:{...scope,sessionId:'loop-two'}});
  assert.equal(history.length,1);assert.equal(history[0].scope.sessionId,'loop-two');
  history=Dock.modelUpdates(history,{...view,sourceEpoch:'producer-two'});
  assert.equal(history.length,1);assert.match(history[0].context,/producer-two/);
  assert.equal(row.age.status,'current','original projection is not mutated');
});

/* ---------- the dock component in jsdom ---------- */
const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', { pretendToBeVisual: true, runScripts: 'outside-only', url: 'http://127.0.0.1/' });
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'Node', 'Event'])
  Object.defineProperty(globalThis, key, { configurable: true, writable: true, value: key === 'window' ? dom.window : dom.window[key] });
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
w.React = React; w.matchMedia = () => ({ matches: false });
const timers = new Map(); let timerId = 0;
w.setInterval = (fn, ms) => { const id = ++timerId; timers.set(id, { fn, ms }); return id; };
w.clearInterval = id => timers.delete(id);
w.HomeSpatialViewport = { getSelectedSiteId: () => 'victoria', subscribe: () => () => {} };
for (const file of ['home-den-reason-vocabulary.js', 'home-apartment-perception-dock-model.js', 'home-apartment-perception-view.js', 'home-apartment-perception-player.js'])
  vm.runInContext(fs.readFileSync(path.join(src, file), 'utf8'), context);
const RealPlayer = w.HomeApartmentPerceptionPlayer;
w.HomeApartmentPerceptionPlayer = { create: options => RealPlayer.create({ ...options, env: w,
  decode: async (_value, { frame }) => ({ url: 'blob:test-' + frame.media_id, width: frame.width, height: frame.height, dispose() {} }) }) };
const compile = file => vm.runInContext(babel.transform(fs.readFileSync(path.join(src, file), 'utf8'), { presets: ['react'], filename: file }).code, context);
compile('home-apartment-den-perception.jsx');
compile('home-lighting-brain-panel.jsx');
after(() => { assert.equal(timers.size, 0, 'every viewer interval is released'); dom.window.close(); });

const flush = () => act(async () => { await new Promise(resolve => setImmediate(resolve)); });
const buttons = container => [...container.querySelectorAll('button')];
const button = (container, label) => buttons(container).find(item => item.textContent.trim() === label || item.getAttribute('aria-label') === label);
async function click(container, label) {
  const control = button(container, label); assert.ok(control, `button ${label}`); assert.equal(control.disabled, false, `${label} enabled`);
  await act(async () => control.click()); await flush();
}
async function choose(container, label, value) {
  const select = container.querySelector(`select[aria-label="${label}"]`); assert.ok(select, label);
  await act(async () => { select.value = value; select.dispatchEvent(new w.Event('change', { bubbles: true })); }); await flush();
}
function engine() {
  const canvas = w.document.createElement('canvas'); w.document.body.appendChild(canvas);
  return { siteId: 'victoria', sceneIdentity: { siteId: 'victoria', contentRevision: 'pack-a', frameDigest: 'frame-a', registrationVersion: 1 }, renderer: { domElement: canvas } };
}
function source(rows) {
  const api = { now: 100, uncertainty: .1, rows, subscriptions: [], listCalls: [], forbidden: [],
    listSessions(options) { api.listCalls.push(options); return Promise.resolve(api.rows); },
    subscribe(options) { api.subscriptions.push(options); return () => {}; },
    sampleClock: () => ({ now_s: api.now, uncertainty_s: api.uncertainty, domain: 'unix' }),
    readFrame: () => Promise.resolve({ bytes: new w.Uint8Array([1, 2, 3]), type: 'image/png' }),
    analysisAvailability: () => Promise.resolve({ available: false, reason: 'analysis_unavailable' }),
    startAnalysis() { api.forbidden.push('start'); throw Error('start forbidden'); },
    start() { api.forbidden.push('source-start'); throw Error('start forbidden'); },
    startCamera() { api.forbidden.push('camera'); throw Error('camera forbidden'); },
    runModel() { api.forbidden.push('model'); throw Error('model forbidden'); } };
  return api;
}
const denSession = (extra = {}) => ({ id: 'den-session-one', site_id: 'victoria', camera_id: 'den', generation: 7, state: 'recording',
  started_at: new Date(60 * 1000).toISOString(), ...extra });
function envelope(sub, { stopped = false, development = false, validUntil = 102, jev = 'judged' } = {}) {
  const scope = copy(sub.scope);
  const frame = { frame_id: 'f1', capture_id: 'capture-f1', sequence_id: 1, captured_at: 99, valid_until: validUntil, width: 640, height: 480,
    media_id: 'media-f1', generation: scope.generation, site_id: scope.siteId, ...(development ? { timing_policy: 'development' } : {}) };
  const pose = { frame_id: 'f1', capture_id: 'capture-f1', observed_at: 99, available_at: 99.2, valid_until: validUntil, model_id: 'pose-model',
    source_id: 'pose-specialist', status: 'candidate', keypoints: [{ id: 'head', x: 110, y: 60 }, { id: 'hip', x: 120, y: 220 }], skeleton_edges: [['head', 'hip']],
    boxes: [{ x: 90, y: 40, w: 80, h: 220, confidence: .91 }] };
  const jevResult = jev === 'abstained' ? { activity: 'insufficient_evidence', reason: 'person_partly_out_of_frame', usable_frames: 2, frames: 8, window_s: 8, min_usable: 4 }
    : { activity: 'reading', probability: .64, distribution: { reading: .64, computer_work: .21, phone_use: .15 } };
  return { schema: 'home-apartment-perception-1', scope, source_epoch: null, session_state: stopped ? 'stopped' : 'recording', frame, pose2d: pose,
    models: [{ family: 'JEV', model_id: 'jev-model', model_revision: 'r2', source_id: 'jev-specialist', status: 'candidate',
      observed_at: 99, available_at: 99.5, valid_until: validUntil, frame_id: 'f1', capture_id: 'capture-f1', result: jevResult }] };
}
async function mount({ rows = [denSession()], extra = {} } = {}) {
  w.localStorage.clear(); // dock state and detail persist; each test starts fresh
  const appSource = source(rows), appEngine = engine(), layers = [], opened = [];
  const layerModule = { attachApartmentPerceptionLayer() {
    const layer = { views: [], disposed: 0, draw(view) { layer.views.push(view); }, screenAnchor: () => null,
      readback: () => ({ following: false }), setFollowing() { return false; }, dispose() { layer.disposed++; } };
    layers.push(layer); return layer;
  } };
  const container = w.document.createElement('div'); w.document.body.appendChild(container);
  const root = createRoot(container);
  let props = { engine: appEngine, source: appSource, loadLayer: () => Promise.resolve(layerModule), onOpenLab: intent => opened.push(intent), ...extra };
  const render = async next => { props = { ...props, ...next }; await act(async () => root.render(React.createElement(w.HomeApartmentDenPerception, props))); await flush(); };
  await render({});
  return { container, appSource, layers, opened, render,
    async emit(value) { await act(async () => appSource.subscriptions.at(-1).onUpdate(value)); await flush(); },
    async connect() {
      await click(container, 'View perception'); await choose(container, 'Den Lab session', rows[0].id); await click(container, 'Connect');
      return appSource.subscriptions.at(-1);
    },
    async unmount() { await act(async () => root.unmount()); container.remove(); appEngine.renderer.domElement.remove(); } };
}

test('HANDOFF "No session": an empty state, no body layer and no start button', async () => {
  const m = await mount({ rows: [] });
  try {
    assert.ok(m.container.querySelector('[data-dock-empty="closed"]'), 'closed card before any read');
    assert.equal(m.appSource.listCalls.length, 0, 'mounting the dock reads nothing');
    await click(m.container, 'View perception');
    const empty = m.container.querySelector('[data-dock-empty="no-session"]');
    assert.ok(empty); assert.match(empty.textContent, /No perception session/);
    assert.equal(buttons(m.container).some(item => /start/i.test(item.textContent)), false, 'no start button');
    assert.equal(m.layers.length, 0, 'no body layer is attached'); assert.equal(m.container.querySelector('svg'), null);
    assert.equal(m.container.querySelector('[data-apartment-body-label]'), null);
    assert.equal(m.container.querySelector('.apt-ui-badge').textContent, 'No session');
    assert.deepEqual(m.appSource.forbidden, []);
  } finally { await m.unmount(); }
});

test('HANDOFF "Frame honesty": a stale frame is greyed, its overlay dashed and dimmed, and Live never shows the skeleton', async () => {
  const m = await mount({ extra: { HomeApartmentOwnerCamera: undefined } });
  w.HomeApartmentOwnerCamera = function LiveStub() { return React.createElement('section', { 'aria-label': 'Live Den camera' }, 'live video'); };
  try {
    const sub = await m.connect();
    m.appSource.now = 101; await m.emit(envelope(sub));
    let figure = m.container.querySelector('figure[data-frame-state]');
    assert.equal(figure.getAttribute('data-frame-state'), 'current'); assert.doesNotMatch(figure.className, /is-stale/);
    assert.equal(m.container.querySelector('svg[aria-label="Exact-frame pose overlay"]').getAttribute('data-overlay-dim'), '0');
    // Development timing keeps an expired frame on screen; Den's expiry still marks it stale.
    m.appSource.now = 105; await m.emit(envelope(sub, { development: true, validUntil: 102 }));
    figure = m.container.querySelector('figure[data-frame-state]');
    assert.equal(figure.getAttribute('data-frame-state'), 'stale'); assert.match(figure.className, /is-stale/);
    assert.match(m.container.querySelector('img').style.filter, /grayscale/);
    const overlay = m.container.querySelector('svg[aria-label="Exact-frame pose overlay"]');
    assert.equal(overlay.getAttribute('data-overlay-dim'), '1'); assert.equal(overlay.style.opacity, '0.55');
    assert.ok([...overlay.querySelectorAll('line, rect')].every(node => node.getAttribute('stroke-dasharray')), 'overlay is dashed');
    assert.match(m.container.querySelector('[data-frame-not-used]').textContent, /Not used for lighting/);
    assert.equal(m.container.querySelector('[data-trace-value="proposed"]').textContent, 'Nothing');
    await click(m.container, 'Live camera');
    assert.ok(m.container.querySelector('[data-dock-live]'));
    assert.equal(m.container.querySelector('svg[aria-label="Exact-frame pose overlay"]'), null, 'Live never shows the analyzed skeleton');
    assert.equal(m.container.querySelector('img[alt^="Analyzed Den camera frame"]'), null);
    await click(m.container, 'Analyzed frame');
    assert.ok(m.container.querySelector('svg[aria-label="Exact-frame pose overlay"]'));
    assert.deepEqual(m.appSource.forbidden, []);
  } finally { delete w.HomeApartmentOwnerCamera; await m.unmount(); }
});

test('an unqualified clock shows "Age unavailable", never an age band, and the frame is not dimmed', async () => {
  const m = await mount();
  try {
    const sub = await m.connect(); m.appSource.uncertainty = null; await m.emit(envelope(sub));
    const timing = m.container.querySelector('[data-frame-timing]');
    assert.equal(timing.getAttribute('data-frame-qualified'), '0');
    assert.match(timing.textContent, /^Age unavailable · clock not qualified/);
    assert.doesNotMatch(m.container.querySelector('.apt-ui-badge').textContent, /\d/, 'the header badge carries no number');
    assert.equal(m.container.querySelector('figure').getAttribute('data-frame-state'), 'unqualified');
  } finally { await m.unmount(); }
});

test('HANDOFF "JEV abstains": Interpreted reads Abstained, Proposed reads Nothing with the reason, Details label each value apart', async () => {
  const m = await mount();
  try {
    const sub = await m.connect(); await m.emit(envelope(sub, { jev: 'abstained' }));
    assert.equal(m.container.querySelector('[data-trace-value="interpreted"]').textContent, 'Abstained');
    assert.equal(m.container.querySelector('[data-trace-value="proposed"]').textContent, 'Nothing');
    assert.match(m.container.querySelector('[data-trace-reason="interpreted"]').textContent, /Not enough to tell · person partly out of frame/);
    assert.match(m.container.querySelector('[data-trace-reason="proposed"]').textContent, /JEV abstained, so nothing is proposed/);
    assert.match(m.container.querySelector('[data-inference="jev"]').textContent, /JEV abstained: .*2 usable of 8 frames in 8 s \(needs 4\)/);
    await click(m.container, 'Show details');
    const details = m.container.querySelector('[data-dock-details-panel]');
    assert.equal(details.open, true);
    const labels = [...details.querySelectorAll('dl.apt-dock-kv > dt')].map(node => node.textContent);
    for (const label of ['Detector', 'Pose', 'JEV']) assert.ok(labels.includes(label), label);
    const values = Object.fromEntries([...details.querySelectorAll('dl.apt-dock-kv > dt')].map(node => [node.textContent, node.nextElementSibling.textContent]));
    assert.match(values.Detector, /raw 0\.91/); assert.match(values.Pose, /pose-model/); assert.match(values.JEV, /jev-model · Abstained/);
    assert.notEqual(values.Detector, values.JEV); assert.doesNotMatch(values.JEV, /0\.91/, 'no merged gauge');
    const step = async key => { await act(async () => m.container.querySelector(`[data-trace-step="${key}"]`).click()); await flush(); };
    await step('proposed');
    assert.match(m.container.querySelector('[data-trace-detail="proposed"]').textContent, /Nothing proposed/);
    await step('did');
    assert.match(m.container.querySelector('[data-trace-detail="did"]').textContent, /Nothing runs from perception without you/);
  } finally { await m.unmount(); }
});

test('Simple and Detailed: values appear only in Detailed, the distribution is JEV\'s and labelled as a judgment', async () => {
  const m = await mount();
  try {
    const sub = await m.connect(); m.appSource.now = 101; await m.emit(envelope(sub));
    assert.equal(m.container.querySelector('[data-trace-value="interpreted"]').textContent, 'Reading');
    assert.doesNotMatch(m.container.querySelector('[data-inference="detector"]').textContent, /0\.91/);
    assert.equal(m.container.querySelector('[aria-label="JEV distribution"]'), null);
    await click(m.container, 'Show details');
    assert.equal(m.container.querySelector('[data-trace-value="interpreted"]').textContent, 'Reading · 0.64');
    assert.match(m.container.querySelector('[data-inference="detector"]').textContent, /raw score 0\.91 \(not calibrated\)/);
    const dist = m.container.querySelector('[aria-label="JEV distribution"]');
    assert.deepEqual([...dist.querySelectorAll('.apt-dock-dist-row > span:first-child')].map(node => node.textContent), ['Reading', 'Computer work', 'Using a phone']);
    assert.match(m.container.textContent, /JEV probabilities are JEV's judgment, not detector or pose scores/);
  } finally { await m.unmount(); }
});

test('dock states: collapsed is one line, hidden leaves Show camera and detaches the viewer, the Lab session stays', async () => {
  const states = [];
  const m = await mount({ extra: { dockState: 'default', onDockState: next => states.push(next) } });
  try {
    const sub = await m.connect(); m.appSource.now = 101; await m.emit(envelope(sub));
    await click(m.container, 'Collapse camera dock'); assert.deepEqual(states, ['collapsed']);
    await m.render({ dockState: 'collapsed' });
    const summary = m.container.querySelector('[data-dock-summary]');
    assert.equal(summary.textContent, 'JEV reads reading · proposes nothing');
    assert.equal(m.container.querySelector('img'), null, 'no hidden media in the collapsed line');
    assert.ok(button(m.container, 'Show camera dock'));
    await m.render({ dockState: 'expanded' });
    assert.equal(m.container.querySelector('[data-apartment-den-perception]').getAttribute('data-dock-state'), 'expanded');
    assert.ok(m.container.querySelector('img'), 'collapsing kept the viewer attached');
    await m.render({ dockState: 'hidden' });
    assert.equal(m.layers.at(-1).disposed, 1, 'hidden detaches the viewer');
    assert.equal(m.container.querySelector('.apt-ui-dock-header'), null);
    await click(m.container, 'Show camera'); assert.equal(states.at(-1), 'default');
    await m.render({ dockState: 'default' });
    assert.match(m.container.textContent, /Camera dock hidden\. The viewer disconnected; the Lab session is unchanged/);
    for (const label of ['Expand camera dock', 'Collapse camera dock', 'Show details', 'Hide camera dock'])
      assert.doesNotMatch(button(m.container, label).getAttribute('aria-label'), /Return to room|^Back/, 'no reserved phrase in the header');
    assert.deepEqual(m.appSource.forbidden, []);
  } finally { await m.unmount(); }
});

test('a replay is labelled with its recording date and is never called live', async () => {
  const m = await mount({ rows: [denSession({ id: 'den-replay', state: 'stopped', started_at: '2026-10-16T21:04:12Z' })] });
  try {
    const sub = await m.connect(); await m.emit(envelope(sub, { stopped: true }));
    assert.equal(m.container.querySelector('figure').getAttribute('data-frame-state'), 'replay');
    assert.match(m.container.querySelector('[data-frame-replay]').textContent, /^Replay · recorded Oct 16, 2026\. Recorded session · nothing here is live/);
    assert.match(m.container.querySelector('.apt-ui-badge').textContent, /^Replay · Oct 16, 2026$/);
  } finally { await m.unmount(); }
});

test('the room body keeps all supplied approximate viewing joints and Follow stays disabled', async () => {
  const m = await mount();
  try {
    const sub = await m.connect();
    const value = envelope(sub);
    const joints = [0, 1, 2, 5, 6, 7, 11].map(id => ({ id, visible: true, position_m: [1 + id / 10, 2, 1.2], error_bound_m: null }));
    let shown = null;
    // The approximate placement is validated by the real view; here the
    // projected view preserves its supplied joints before the room draw.
    const projected = { status: 'available', sourceEpoch: null, approximateRoomPose: { renderable: true, status: 'approximate', joints, edges: [[5, 6], [5, 7], [5, 11]] },
      roomPose: { admitted: false }, diagnosticRoomPose: { renderable: false } };
    shown = w.HomeApartmentPerceptionDockModel.roomLayerView(projected);
    assert.deepEqual(copy(shown.approximateRoomPose.joints.map(joint => joint.id)), [0, 1, 2, 5, 6, 7, 11]);
    assert.deepEqual(copy(shown.approximateRoomPose.edges), [[5, 6], [5, 7], [5, 11]]);
    await m.emit(value);
    assert.equal(button(m.container, 'Follow').disabled, true);
    assert.match(m.container.textContent, /Follow is off until continuous registered tracking exists/);
    const source = fs.readFileSync(path.join(src, 'home-apartment-den-perception.jsx'), 'utf8');
    assert.match(source, /layerRef\.current\?\.draw\(roomView\(next\.view\)\)/, 'every room draw uses supplied viewing geometry');
    assert.match(source, /data-apartment-body-label="1"/); assert.match(source, /\{body\.label\}/);
  } finally { await m.unmount(); }
});

test('viewing the activity loop retires old observers on rolls and pause, recovers, and never starts analysis',async()=>{
  const m=await mount();let notify,closed=0;
  m.appSource.watchActivityLoop=options=>{notify=options.onChange;return()=>{closed++;};};
  await m.render({});
  try{
    await click(m.container,'View perception');await click(m.container,'View activity loop');
    const update=async value=>{await act(async()=>notify(value));await flush();};
    const first=denSession({id:'loop-one',generation:0});
    await update({status:'running',session:first,producerEpoch:'epoch-one',rolls:0});
    const old=m.appSource.subscriptions.at(-1);assert.equal(old.scope.sessionId,'loop-one');assert.equal(old.scope.generation,0);
    assert.equal(old.arm,'B');
    await update({status:'connecting',session:null,reason:'activity_loop_session_changed'});
    assert.equal(old.signal.aborted,true);assert.equal(m.layers[0].disposed,1);
    const second=denSession({id:'loop-two',generation:2});
    await update({status:'running',session:second,producerEpoch:'epoch-one',rolls:1});
    assert.equal(m.appSource.subscriptions.at(-1).scope.sessionId,'loop-two');
    const next=m.appSource.subscriptions.at(-1);
    await update({status:'paused',session:null,reason:'vacant'});
    assert.equal(next.signal.aborted,true);assert.equal(m.container.querySelector('[data-activity-loop-status]').dataset.activityLoopStatus,'paused');
    await update({status:'running',session:second,producerEpoch:'epoch-two',rolls:1});
    assert.equal(m.appSource.subscriptions.length,3);
    assert.deepEqual(m.appSource.forbidden,[]);assert.equal(closed,0,'pause keeps the read-only watcher for recovery');
    await click(m.container,'Hide camera dock');assert.equal(closed,1,'hiding the view stops its status reads');
  }finally{await m.unmount();}
});

test('Open in Lab is a working control that carries the session and frame time; the dead Back control is gone', async () => {
  const m = await mount();
  try {
    const sub = await m.connect(); await m.emit(envelope(sub));
    assert.equal(button(m.container, 'Back to Home / Perception Lab'), undefined);
    assert.match(m.container.querySelector('[data-lab-intent="session"]').textContent, /Opens session den-session-one at 39\.0 s/);
    await click(m.container, 'Open in Lab');
    assert.deepEqual(copy(m.opened), [{ session_id: 'den-session-one', t_s: 39, camera_id: 'den', frame_id: 'f1' }]);
    assert.equal(m.layers.at(-1).disposed, 1, 'the viewer under Lab disconnects');
    assert.ok(m.container.querySelector('select[aria-label="Den Lab session"]'), 'the session list stays for the way back');
    const app = fs.readFileSync(path.join(src, 'home-app.jsx'), 'utf8'), lab = fs.readFileSync(path.join(src, 'home-perception-lab.jsx'), 'utf8');
    assert.match(app, /onOpenPerceptionLab=\{openPerceptionLabFromDock\}/); assert.match(app, /intent=\{perceptionLabIntent\}/);
    assert.match(lab, /openSessionRef\.current\(intent\.session_id, Number\.isFinite\(intent\.t_s\) \? intent\.t_s : null\)/);
    assert.match(lab, /setT\(Number\.isFinite\(atTime\) && atTime >= 0 \? atTime : 0\)/);
  } finally { await m.unmount(); }
});

test('the Apartment mounts the dock from the capability and keeps Den\'s props and the column live feed', () => {
  const apartment = fs.readFileSync(path.join(src, 'home-apartment.jsx'), 'utf8');
  assert.match(apartment, /const perceptionDockAvailable = perceptionCapability\.variant === "exact-frame" && !!window\.HomeApartmentDenPerception;/);
  assert.match(apartment, /\{perceptionDockAvailable && !rehearsalIsolated && \(/);
  assert.doesNotMatch(apartment, /selectedSiteId === "victoria" && !rehearsalIsolated && window\.HomeApartmentDenPerception/);
  assert.match(apartment, /onAimPreviewController=\{registerDenDeskAimPreview\}\s*onAimPreviewChange=\{refreshDenDeskAimPreview\}/, 'Den\'s mount props are unchanged');
  assert.doesNotMatch(apartment, /onReturnToHome=\{onClose\}/, 'the dead Lab control is no longer wired');
  assert.match(apartment, /data-apartment-live-feed="column"/);
  assert.match(apartment, /\{!unifiedShell && apartmentSceneControlsEnabled && liveCam && liveOn && !calibCam && \(/, 'the over-the-room feed is classic only');
  const loader = fs.readFileSync(path.join(src, 'home-feature-loader.js'), 'utf8'), at = name => loader.indexOf(`"${name}"`);
  assert.ok(at('home-den-reason-vocabulary.js') > 0 && at('home-den-reason-vocabulary.js') < at('home-apartment-perception-dock-model.js'));
  assert.ok(at('home-apartment-perception-dock-model.js') < at('home-apartment-den-perception.jsx'));
});

/* ---------- lighting-brain panel preservation ---------- */
const brainReading = () => {
  const state = denFixture('brain-state-reading.json');
  const labels = { 'victoria-den-proposed-fixture-01': 'Mount 1', 'victoria-den-proposed-fixture-02': 'Mount 2',
    'victoria-den-mount-03-virtual-fixture': 'Mount 3', 'victoria-den-mount-04-virtual-fixture': 'Mount 4' };
  const fixtures = Object.keys(labels).map((fixture_id, index) => ({ fixture_id, auto: index !== 1, paused: index === 1, pause_reason: index === 1 ? 'aim' : null }));
  const decisions = Array.from({ length: 20 }, (_, i) => ({ at: 1800000000 - i, rule: i ? 'zone_ambient' : 'reading_spot_on', why: i ? 'Sofa occupied' : 'Reading at sofa', change: null }));
  return { labels, brain: { status: 'online', online: true, mode: state.mode || 'reading', why: 'Reading at sofa · spotlight on', fixtures, decisions } };
};
test('brain panel: why line, online/offline, den-ops banner, last-20 log, Auto/Paused with Return to Auto render as before', async () => {
  const { labels, brain } = brainReading(), returned = [];
  const container = w.document.createElement('div'); w.document.body.appendChild(container);
  const root = createRoot(container);
  const render = async (value, ops = null) => { await act(async () => root.render(React.createElement(w.HomeLightingBrainPanel, { brain: value, ops, labels, onReturnToAuto: id => returned.push(id) }))); await flush(); };
  try {
    await render(brain, { severity: 'alert', host: 'la-host', text: 'Deep sensing paused · ambient only', level: 2, step: 'deep_stack_paused' });
    assert.equal(container.querySelector('[data-lighting-brain-why]').textContent, 'Reading at sofa · spotlight on');
    assert.match(container.querySelector('[data-lighting-brain-online]').textContent, /^Brain online/);
    assert.equal(container.querySelector('[data-den-ops-banner]').getAttribute('data-den-ops-banner'), 'alert');
    assert.equal(container.querySelectorAll('[data-lighting-brain-decision]').length, 20);
    assert.deepEqual([...container.querySelectorAll('[data-lighting-brain-auto]')].map(node => node.getAttribute('data-lighting-brain-auto')), ['1', '0', '1', '1']);
    await act(async () => container.querySelector('button[aria-label="Return Mount 2 to Auto"]').click()); await flush();
    assert.deepEqual(returned, ['victoria-den-proposed-fixture-02']);
    await render({ ...brain, status: 'offline', online: false });
    assert.match(container.querySelector('[data-lighting-brain-online]').textContent, /Brain offline · holding the last state/);
    assert.equal(container.querySelector('[data-lighting-brain-why]').textContent, 'Reading at sofa · spotlight on', 'offline keeps the last why');
  } finally { await act(async () => root.unmount()); container.remove(); }
});

test('brain panel mounts, Mark sofa and the follower wiring are unchanged; the dock does not render or wrap them', () => {
  const apartment = fs.readFileSync(path.join(src, 'home-apartment.jsx'), 'utf8');
  const dockSource = fs.readFileSync(path.join(src, 'home-apartment-den-perception.jsx'), 'utf8');
  const mounts = apartment.match(/<window\.HomeLightingBrainPanel brain=\{lightingBrain\} ops=\{denOpsBanner\}\s*labels=\{Object\.fromEntries\(virtualFixtureItems\.map\(\(fixture\) => \[fixture\.fixture_id, fixture\.label\]\)\)\}\s*onReturnToAuto=\{\(id\) => lightingBrainRef\.current\?\.follower\.returnToAuto\(id\)\} \/>/g) || [];
  assert.equal(mounts.length, 2, 'the unified and classic inspectors both keep the panel with the same props');
  assert.equal((apartment.match(/onClick=\{\(\) => \{ setTargetMarkKind\("sofa"\); startDeskTargetSelection\("sofa"\); \}\}/g) || []).length, 2, 'Mark sofa in both inspectors');
  assert.match(apartment, /Follower\.createBrainFollowing\(/);
  assert.match(apartment, /lightingBrainRef\.current\?\.follower\.handChange\(fixtureId, \{ reason: reason \|\| "change" \}\)/);
  assert.doesNotMatch(dockSource, /HomeLightingBrain|lightingBrain|follower/, 'the dock never touches the brain modules');
  const loader = fs.readFileSync(path.join(src, 'home-feature-loader.js'), 'utf8');
  for (const name of ['home-lighting-brain-client.js', 'home-lighting-brain-follower.js', 'home-lighting-sofa-target.js', 'home-lighting-brain-panel.jsx'])
    assert.ok(loader.includes(`["${name}", ${name.endsWith('.jsx')}]`), name);
});
