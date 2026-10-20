#!/usr/bin/env node
// Echo's engineered aim and named aims in real Chromium: the actual overlay
// bridge, aim presentation, proposal store, room overlay JSX, named-aim
// logic, Echo aim host and named-aim JSX (useAptEchoNamedAims), mounted by an
// authored host over an authored Echo room. The host stands in only for
// HomeApartmentLiveView's own state: the model, a recorded model save and the
// Simulation Mode aim runtime. No WebGL frame is drawn; picking is a real
// Three raycast. Authored data only; no household data, network or hardware.
// Runs as plain `node`; node:test sets the exit code.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'), SRC = path.join(ROOT, 'app/src');

// Fixed before the first run (2026-10-19); never raised to make a run pass.
// Worst case 40 s + 9 × 10 s = 130 s, inside the runner's 180 s per harness.
const DEADLINE_MS = Object.freeze({ boot: 40000, step: 10000, wait: 3000 });

// Authored Echo room, as in test-apartment-aim-library.mjs: the spotlight sits
// so the painting is 1.85 m away at 59° from straight down.
const SPOT = [2, 2.404, 2.453], PAINTING = [2, 3.99, 1.5];
const POINT = Object.freeze({ nearPainting: [2.12, 3.99, 1.43], wall: [1.0, 3.99, 0.9], steep: [0.0, 2.404, 2.1], floor: [2, 2, 0] });

const FILES = new Map([
  ['/vendor/react-18.3.1/react.production.min.js', 'vendor/react-18.3.1/react.production.min.js'],
  ['/vendor/react-18.3.1/react-dom.production.min.js', 'vendor/react-18.3.1/react-dom.production.min.js'],
  ['/vendor/babel-7.29.0/babel.min.js', 'vendor/babel-7.29.0/babel.min.js'],
  ['/vendor/three-0.180.0/three.module.js', 'vendor/three-0.180.0/three.module.js'],
  ['/vendor/three-0.180.0/three.core.js', 'vendor/three-0.180.0/three.core.js'],
  ['/home-apartment-ui.jsx', 'home-apartment-ui.jsx'],
  ['/home-apartment-aim-proposals.js', 'home-apartment-aim-proposals.js'],
  ['/home-apartment-aim-library.js', 'home-apartment-aim-library.js'],
  ['/home-apartment-echo-aim.js', 'home-apartment-echo-aim.js'],
  ['/home-3d/apartment-ui-overlay-bridge.mjs', 'home-3d/apartment-ui-overlay-bridge.mjs'],
  ['/home-3d/apartment-aim-presentation.mjs', 'home-3d/apartment-aim-presentation.mjs'],
]);

const HARNESS_JSX = String.raw`
function AptEchoHarness({ api }) {
  const [state, setState] = React.useState(api.initial);
  const latest = React.useRef(state); latest.current = state;
  const update = patch => setState(previous => ({ ...previous, ...(typeof patch === "function" ? patch(previous) : patch) }));
  React.useLayoutEffect(() => { api.set = patch => ReactDOM.flushSync(() => update(patch)); api.state = () => latest.current; api.ready = true; }, []);
  const setModel = next => update(previous => ({ model: typeof next === "function" ? next(previous.model) : next }));
  // The view's existing save, recorded: it reports the outcome the step chose.
  const saveModel = async () => {
    api.saves.push(JSON.parse(JSON.stringify(latest.current.model.targets)));
    update({ saveStatus: { state: "saving", detail: "writing" } });
    const outcome = await new Promise(resolve => { api.finishSave = resolve; });
    update({ saveStatus: { state: outcome, detail: outcome } });
    return { ok: outcome === "saved" };
  };
  const echo = useAptEchoNamedAims({ open: true, siteId: "echo", engine: api.engine, host: api.host, model: state.model, setModel, saveModel,
    saveStatus: state.saveStatus, markModelDirty: () => update({ saveStatus: { state: state.sim ? "simulation" : "unsaved" } }), sim: state.sim,
    store: api.store, aimMode: state.aimMode, aimFixtureId: state.aimFixtureId,
    setAimMode: value => update({ aimMode: value }), setAimFixtureId: value => update({ aimFixtureId: value }), editing: false,
    sceneEnabled: true, placementEnabled: true, contentRevision: "authored-echo-r1",
    showToast: text => api.toasts.push(text), openNamedAimEditor: id => { api.events.push({ type: "edit", id }); return true; },
    onShowControls: () => api.events.push({ type: "show_controls" }), onShowAim: () => api.events.push({ type: "show_aim" }) });
  api.echo = echo;
  return <div className="apt-home-shell">
    <div id="panel" data-panel={state.aimMode ? "aim" : "room"}>{state.aimMode ? echo.aimTab : echo.inspector || echo.roomList}</div>
    {echo.overlay}
  </div>;
}
window.AptEchoHarness = AptEchoHarness;
`;

const HARNESS_MODULE = String.raw`
import * as THREE from 'three';
import { createApartmentUiOverlayLayer } from '/home-3d/apartment-ui-overlay-bridge.mjs';
import * as Presentation from '/home-3d/apartment-aim-presentation.mjs';
const SPOT = ${JSON.stringify(SPOT)}, PAINTING = ${JSON.stringify(PAINTING)};
const engineered = limits => ({ id: 'authored-echo-spot', type: 'light', name: 'Authored spot', ha_entity_id: null, pos: SPOT, room_id: 'authored-room',
  fixture_kind: 'engineered_gimbal_v1', aiming_origin: 'fixture_bottom',
  gimbal: { limits: { pan_min_deg: -180, pan_max_deg: 180, tilt_min_deg: -90, tilt_max_deg: 90, tilt_positive: 'down', ...limits },
    product_profile_sha256: 'a'.repeat(64), default_target_id: 'authored-floor-spot' } });
const model = (limits = {}, extra = []) => ({ schema_version: 1, revision: 7, exists: true, zones: [], devices: [engineered(limits)],
  targets: [
    { id: 'authored-painting', name: 'Painting', category: 'art', shape: 'surface', pos: PAINTING, normal: [0, -1, 0], up: [0, 0, 1], size_m: [0.9, 0.65], source: 'authored' },
    { id: 'authored-floor-spot', name: 'Floor spot', category: 'custom', shape: 'point', pos: [2, 2, 0], normal: [0, 0, 1], up: [0, 1, 0], size_m: [0, 0], source: 'authored' },
    ...extra] });
const host = document.getElementById('room'), width = host.clientWidth, height = host.clientHeight;
const camera = new THREE.PerspectiveCamera(50, width / height, 0.05, 60);
camera.up.set(0, 0, 1); camera.position.set(3.6, -1.6, 3.0); camera.lookAt(1.6, 2.6, 1.0);
const scene = new THREE.Scene(), apartmentRoot = new THREE.Group(), overlayRoot = new THREE.Group();
scene.add(apartmentRoot, overlayRoot);
const surface = (name, geometry, position, rotation = [0, 0, 0]) => {
  const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }));
  mesh.name = name; mesh.position.fromArray(position); mesh.rotation.set(...rotation); apartmentRoot.add(mesh); return mesh;
};
surface('Floor', new THREE.PlaneGeometry(4, 4), [2, 2, 0]);
surface('Back wall', new THREE.PlaneGeometry(4, 2.5), [2, 4, 1.25], [Math.PI / 2, 0, 0]);
surface('Left wall', new THREE.PlaneGeometry(4, 2.5), [0, 2, 1.25], [Math.PI / 2, Math.PI / 2, 0]);
// Named-aim markers, as the engine overlay draws targets[].
const markers = new THREE.Group(); scene.add(markers);
const marker = (id, position, normal) => { const mesh = new THREE.Mesh(new THREE.PlaneGeometry(.5, .4), new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }));
  mesh.position.fromArray(position).addScaledVector(new THREE.Vector3(...normal), .02);
  mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), new THREE.Vector3(...normal)); mesh.userData.targetId = id; markers.add(mesh); return mesh; };
marker('authored-painting', PAINTING, [0, -1, 0]);
scene.updateMatrixWorld(true);
const raycaster = new THREE.Raycaster();
const ray = (clientX, clientY) => { const rect = host.getBoundingClientRect();
  raycaster.setFromCamera(new THREE.Vector2((clientX - rect.left) / rect.width * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1), camera); };
function pickSurface(clientX, clientY) {
  ray(clientX, clientY);
  const hit = raycaster.intersectObjects(apartmentRoot.children, false)[0];
  if (!hit) return null;
  const normal = hit.face.normal.clone().transformDirection(hit.object.matrixWorld).normalize();
  return { point: hit.point.toArray(), normal: normal.toArray(), object: hit.object };
}
function projectWorld(world) {
  const v = world.clone().project(camera);
  return { x: (v.x + 1) / 2 * width, y: (1 - v.y) / 2 * height, visible: v.z > -1 && v.z < 1 };
}
// The Simulation Mode runtime's aim surface, authored: it only remembers.
const sim = { destination: { kind: 'named', id: 'authored-floor-spot', name: 'Floor spot', pos: [2, 2, 0] }, calls: [] };
window.__SIM_APARTMENT_AIM_RUNTIME = { read: () => ({ current_destination: JSON.parse(JSON.stringify(sim.destination)) }),
  setDestination(id, destination) { sim.calls.push([id, destination]); sim.destination = JSON.parse(JSON.stringify(destination)); return { current_destination: { ...sim.destination } }; } };
const api = window.__echo = { host, events: [], toasts: [], saves: [], sweeps: [], copied: [], sim, models: { full: model, narrow: () => model({ tilt_min_deg: 15 }) },
  initial: { model: model(), saveStatus: { state: 'idle' }, sim: true, aimMode: false, aimFixtureId: null } };
api.store = window.HomeApartmentAimProposals.createAimProposalStore();
api.engine = { siteId: 'echo',
  async createUiOverlayLayer({ host: layerHost, signal, isCurrent, onPresentationFrame, onPresentationDispose }) {
    return createApartmentUiOverlayLayer({ THREE, camera, apartmentRoot, overlayRoot, host: layerHost, projectWorld, pickSurface, isCurrent, signal,
      onPresentationFrame, onPresentationDispose });
  },
  picking: { pick(objects, x, y) { ray(x, y); return raycaster.intersectObjects(objects, false); } },
  overlay: { pickObjects: () => [], targetPickObjects: () => markers.children,
    animateAimTransition: (spec, done) => { api.sweeps.push(spec); done?.(); return () => {}; } } };
window.Home3D = { ready: Promise.resolve({ loadAimPresentation: async () => Presentation }) };
Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async text => { api.copied.push(text); } } });
api.toClient = point => { const at = projectWorld(new THREE.Vector3(...point)), rect = host.getBoundingClientRect(); return { x: rect.left + at.x, y: rect.top + at.y }; };
api.proposal = () => { const scope = api.echo?.host?.held('authored-echo-spot')?.scope; const value = scope && api.store.current(scope); return value && JSON.parse(JSON.stringify(value)); };
api.reset = (patch = {}) => { api.store.clearAll(); api.echo?.host?.clear(); api.events.length = api.toasts.length = api.saves.length = api.sweeps.length = api.copied.length = sim.calls.length = 0;
  sim.destination = { kind: 'named', id: 'authored-floor-spot', name: 'Floor spot', pos: [2, 2, 0] }; api.set({ ...api.initial, model: model(), ...patch }); };
api.model = model;
const babel = source => Babel.transform(source, { presets: ['react', 'env'],
  plugins: ['transform-class-properties', 'transform-object-rest-spread', 'transform-flow-strip-types'] }).code;
for (const source of [window.__uiSource, window.__overlaySource, window.__namedAimSource, window.__harnessSource]) (0, eval)(babel(source));
ReactDOM.createRoot(document.getElementById('app')).render(React.createElement(window.AptEchoHarness, { api }));
`;

function page() {
  const inline = value => JSON.stringify(value).replace(/</g, '\\u003c');
  const source = name => inline(fs.readFileSync(path.join(SRC, name), 'utf8'));
  return `<!doctype html><html><head><meta charset="utf-8"><link rel="icon" href="data:,">
<style>html,body{margin:0;background:#101114;color:#eee;font:13px sans-serif}
#app{position:absolute;right:0;top:0;width:340px;height:800px;overflow:auto}
#room{position:absolute;left:0;top:0;width:1000px;height:760px;overflow:hidden;background:#1a1c20}</style>
<script>
window.__transport = [];
(function () {
  const note = kind => window.__transport.push(kind);
  const fetch = window.fetch; window.fetch = function () { note('fetch'); return fetch.apply(this, arguments); };
  const open = XMLHttpRequest.prototype.open; XMLHttpRequest.prototype.open = function () { note('xhr'); return open.apply(this, arguments); };
  for (const name of ['WebSocket', 'EventSource']) if (window[name]) window[name] = new Proxy(window[name], { construct(target, args) { note(name); return Reflect.construct(target, args); } });
  if (navigator.sendBeacon) navigator.sendBeacon = function () { note('beacon'); return false; };
})();
window.__uiSource = ${source('home-apartment-ui.jsx')};
window.__overlaySource = ${source('home-apartment-room-overlay.jsx')};
window.__namedAimSource = ${source('home-apartment-aim-library.jsx')};
window.__harnessSource = ${inline(HARNESS_JSX)};
</script>
<script src="/vendor/react-18.3.1/react.production.min.js"></script>
<script src="/vendor/react-18.3.1/react-dom.production.min.js"></script>
<script src="/vendor/babel-7.29.0/babel.min.js"></script>
<script src="/home-apartment-aim-proposals.js"></script>
<script src="/home-apartment-aim-library.js"></script>
<script src="/home-apartment-echo-aim.js"></script>
<script type="importmap">{"imports":{"three":"/vendor/three-0.180.0/three.module.js"}}</script>
</head><body><div id="room"></div><div id="app"></div>
<script type="module">${HARNESS_MODULE}</script></body></html>`;
}

function browserOptions() {
  const installed = fs.existsSync(chromium.executablePath());
  assert.ok(installed || process.platform === 'win32', 'pinned Chromium must exist on Linux');
  const args = ['--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'];
  return { headless: true, args, ...(process.platform === 'linux' ? { channel: 'chromium' } : !installed ? { channel: 'msedge' } : {}) };
}

let server, browser, tab, booted = false;
const served = [], errors = [];
const run = (fn, arg) => tab.evaluate(fn, arg);
const until = (fn, arg) => tab.waitForFunction(fn, arg, { timeout: DEADLINE_MS.wait });
const at = point => run(p => window.__echo.toClient(p), point);
const panel = () => run(() => document.getElementById('panel').innerText);
const proposal = () => run(() => window.__echo.proposal());
const chip = () => run(() => { const root = document.querySelector('[data-apt-aim-chip]'), get = s => root?.querySelector(s)?.textContent ?? null;
  return root && { hidden: root.hidden, state: root.getAttribute('data-state'), title: get('[data-apt-aim-chip-title]'),
    metrics: get('[data-apt-aim-chip-metrics]'), note: get('[data-apt-aim-chip-note]') }; });
async function center(selector) {
  const box = await tab.locator(selector).boundingBox();
  assert.ok(box, selector + ' has a box');
  return { x: box.x + box.width / 2, y: box.y + box.height / 2, box };
}
async function aimSpot() {
  await run(() => window.__echo.set({ aimMode: true, aimFixtureId: 'authored-echo-spot' }));
  await until(() => { const node = document.querySelector('[data-apt-aim-reticle]'); return node && !node.hidden; });
}
async function dragTo(point, { release = true } = {}) {
  const from = await center('[data-apt-aim-reticle]'), to = await at(point);
  await tab.mouse.move(from.x, from.y); await tab.mouse.down();
  await tab.mouse.move(to.x, to.y, { steps: 8 });
  await until(() => !document.querySelector('[data-apt-aim-chip]').hidden);
  if (release) { await tab.mouse.up(); await until(() => !document.querySelector('[data-apt-aim-chip]').hidden === false); }
}
async function saveAs(name) {
  await tab.locator('[data-apt-named-aim-action="save-as"]').click();
  const field = tab.getByRole('textbox', { name: 'Name for this aim' });
  await field.fill(name); await field.press('Enter');
}
const step = (name, body) => test(name, { timeout: DEADLINE_MS.step }, async () => {
  assert.ok(booted, 'harness booted');
  await run(() => window.__echo.reset());
  await tab.mouse.move(2, 2);
  await body();
  assert.deepEqual(errors, [], 'no page errors');
});

test('boot: the actual modules mount in Chromium with no transport', { timeout: DEADLINE_MS.boot }, async () => {
  const html = page();
  server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1');
    served.push(url.pathname);
    if (url.pathname === '/') { response.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' }); response.end(html); return; }
    const file = FILES.get(url.pathname);
    if (!file) { response.writeHead(404); response.end(); return; }
    response.writeHead(200, { 'content-type': 'text/javascript; charset=utf-8', 'cache-control': 'no-store' });
    response.end(fs.readFileSync(path.join(SRC, file)));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  browser = await chromium.launch(browserOptions());
  const context = await browser.newContext({ viewport: { width: 1340, height: 800 }, deviceScaleFactor: 1, serviceWorkers: 'block' });
  tab = await context.newPage();
  tab.on('pageerror', error => errors.push(String(error?.message || error)));
  tab.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await tab.goto(`http://127.0.0.1:${server.address().port}/`);
  await tab.waitForFunction(() => window.__echo?.ready === true, null, { timeout: DEADLINE_MS.boot / 2 });
  booted = true;
  assert.deepEqual(errors, []);
  assert.deepEqual(served.filter(name => name !== '/' && !FILES.has(name)), [], 'only the allowlisted modules were requested');
  assert.match(await panel(), /Named aims[\s\S]*Painting[\s\S]*Floor spot/, 'the room lists Echo’s named aims');
});

step('HANDOFF Drag aim on the Echo painting: Snaps to “Painting” · 1.85 m · 59° tilt; Set is simulated', async () => {
  await aimSpot();
  assert.match(await panel(), /Aim\s*Simulated/, 'the Aim header says Simulated');
  assert.match(await panel(), /Now\s*Floor spot · /);
  await dragTo(POINT.nearPainting, { release: false });
  const live = await chip();
  assert.deepEqual([live.state, live.title, live.metrics], ['valid', 'Snaps to “Painting”', '1.85 m · 59° tilt']);
  await tab.mouse.up();
  await until(() => { const bar = document.querySelector('[data-apt-aim-bar]'); return bar && !bar.hidden; });
  assert.equal(await tab.locator('[data-apt-aim-bar-target]').textContent(), 'Painting');
  const proposed = await proposal();
  assert.deepEqual([proposed.origin, proposed.target.point_m, proposed.target.surface], ['manual', PAINTING, 'Painting']);
  assert.match(await panel(), /Proposed\s*Painting · 1\.85 m · 59° tilt/);
  assert.equal(await tab.locator('[data-apt-named-aim-chip="authored-painting"]').getAttribute('data-proposed'), 'true', 'the Painting chip is cyan');
  assert.deepEqual(await run(() => window.__echo.sim.calls.length), 0, 'the warm current aim stays until Set');
  await tab.locator('[data-apt-aim-action="set"]').click();
  await until(() => document.querySelector('[data-apt-aim-bar]').hidden);
  assert.deepEqual(await run(() => window.__echo.sim.destination.id), 'authored-painting');
  assert.deepEqual(await run(() => window.__echo.toasts), ['Aim set · simulated, no hardware command sent']);
  assert.equal(await run(() => window.__echo.sweeps.length), 1, 'the bar Set sweeps the beam');
  await until(() => document.querySelector('[data-apt-named-aim-chip="authored-painting"]').getAttribute('aria-current') === 'true');
  assert.match(await panel(), /Now\s*Painting · 1\.85 m · 59° tilt/);
});

step('Unreachable on an authored narrow-limit Echo profile: angle and limit, a suggestion, Set unavailable', async () => {
  await run(() => window.__echo.reset({ model: window.__echo.models.narrow(), sim: false }));
  await aimSpot();
  assert.match(await panel(), /Aim\s*Engineered profile/, 'outside Simulation Mode the light is not called simulated');
  await dragTo(POINT.steep, { release: false });
  const live = await chip();
  assert.equal(live.state, 'invalid');
  assert.match(live.title, /^Out of reach · (79|8[0-2])° from straight down, limit 75°$/);
  assert.equal(live.note, 'Choose a spot closer to straight below the light.');
  await tab.mouse.up();
  await until(() => { const bar = document.querySelector('[data-apt-aim-bar]'); return bar && !bar.hidden; });
  assert.equal(await tab.locator('[data-apt-aim-bar-target]').textContent(), 'Out of reach');
  assert.equal(await tab.locator('[data-apt-aim-action="set"]').count(), 0);
  assert.equal(await tab.locator('[data-apt-aim-tab-action="set"]').count(), 0, 'no Set aim in the Aim tab either');
  assert.equal(await tab.locator('[data-apt-aim-suggestion]').textContent(), 'Choose a spot closer to straight below the light.');
  // A reachable spot outside Simulation Mode still only proposes: Set refuses.
  await tab.locator('[data-apt-aim-tab-action="discard"]').click();
  await tab.locator('[data-apt-named-aim-chip="authored-painting"]').click();
  await tab.locator('[data-apt-aim-tab-action="set"]').click();
  assert.match(await tab.locator('[data-apt-aim-notice]').textContent(), /simulated only/);
  assert.deepEqual(await run(() => window.__echo.sim.calls.length), 0, 'nothing moved and nothing was sent');
});

step('Save as named aim: the naming field claims Escape; Saving → Saved, Saved locally and a revision conflict that never overwrites', async () => {
  await aimSpot();
  await dragTo(POINT.wall);
  await tab.locator('[data-apt-named-aim-action="save-as"]').click();
  const field = tab.getByRole('textbox', { name: 'Name for this aim' });
  await field.fill('Reading');
  await field.press('Escape');
  assert.equal(await tab.locator('[data-apt-named-aim-naming]').count(), 0, 'Escape closes only the field');
  assert.ok(await proposal(), 'the proposal stays');
  await saveAs('Reading');
  await until(() => document.querySelector('[data-apt-named-aim-save-state]')?.getAttribute('data-apt-named-aim-save-state') === 'saving');
  const saved = await run(() => window.__echo.saves.at(-1).find(target => target.name === 'Reading'));
  assert.deepEqual([saved.source, saved.surface, saved.category, saved.aim_basis.scene], ['aimed', 'Back wall', 'custom', 'authored-echo-r1']);
  assert.match(await tab.locator('[data-apt-named-aim-save-state]').textContent(), /“Reading”\s*Saving/);
  await run(() => window.__echo.finishSave('saved'));
  await until(() => document.querySelector('[data-apt-named-aim-save-state]')?.getAttribute('data-apt-named-aim-save-state') === 'saved');
  assert.match(await tab.locator('[data-apt-named-aim-save-state]').textContent(), /Saved/);
  assert.equal(await tab.locator('[data-apt-named-aim-chip]').filter({ hasText: 'Reading' }).count(), 1, 'the new aim is a recall chip');
  // Offline: kept as a local draft.
  await saveAs('Reading two');
  await run(() => window.__echo.finishSave('offline'));
  await until(() => document.querySelector('[data-apt-named-aim-save-state]')?.getAttribute('data-apt-named-aim-save-state') === 'local');
  assert.match(await panel(), /“Reading two” is saved in this browser only\./);
  // The model changed elsewhere: nothing overwritten, draft kept.
  await saveAs('Reading three');
  await run(() => window.__echo.finishSave('conflict'));
  await until(() => document.querySelector('[data-apt-named-aim-save-state]')?.getAttribute('data-apt-named-aim-save-state') === 'not_saved');
  assert.match(await panel(), /“Reading three” isn't saved\. The model changed elsewhere, so nothing was overwritten and your draft is kept\./);
  assert.equal(await run(() => window.__echo.state().model.targets.filter(target => target.name.startsWith('Reading')).length), 3, 'the draft is kept');
});

step('Name conflict: an existing name offers Replace existing or Keep both; Replace keeps the id', async () => {
  await aimSpot();
  await dragTo(POINT.wall);
  await saveAs('painting');
  await until(() => !!document.querySelector('[data-apt-named-aim-flag][data-state="conflict"]'));
  const flag = tab.locator('[data-apt-named-aim-flag][data-state="conflict"]');
  assert.match(await flag.textContent(), /“painting” already exists\./);
  assert.deepEqual(await flag.locator('button').allTextContents(), ['Replace existing', 'Keep both']);
  assert.equal(await run(() => window.__echo.saves.length), 0, 'nothing is saved before the choice');
  await flag.getByRole('button', { name: 'Keep both' }).click();
  await until(() => window.__echo.saves.length === 1);
  await run(() => window.__echo.finishSave('saved'));
  assert.deepEqual(await run(() => window.__echo.state().model.targets.map(target => target.name)), ['Painting', 'Floor spot', 'painting 2']);
  await saveAs('Painting');
  await tab.locator('[data-apt-named-aim-flag][data-state="conflict"]').getByRole('button', { name: 'Replace existing' }).click();
  await until(() => window.__echo.saves.length === 2);
  await run(() => window.__echo.finishSave('saved'));
  const painting = await run(() => window.__echo.state().model.targets.find(target => target.id === 'authored-painting'));
  assert.equal(painting.name, 'Painting'); assert.equal(painting.source, 'aimed');
  assert.notDeepEqual(painting.pos, PAINTING, 'Replace existing takes the new spot under the same id');
});

step('Recall chips propose only; an aim from an earlier scan says so with Preview it and Keep as is', async () => {
  const old = { id: 'authored-old', name: 'Old desk', category: 'custom', shape: 'point', pos: [3.2, 3.99, 1.0], normal: [0, -1, 0], source: 'aimed',
    aim_basis: { scene: 'authored-echo-r0', collision: null, fixture_id: 'authored-echo-spot', profile: 'a'.repeat(64) } };
  await run(extra => window.__echo.reset({ model: window.__echo.model({}, [extra]) }), old);
  await aimSpot();
  assert.equal(await tab.locator('[data-apt-named-aim-chip="authored-floor-spot"]').getAttribute('aria-current'), 'true', 'the current aim is marked');
  await tab.locator('[data-apt-named-aim-chip="authored-painting"]').click();
  assert.equal((await proposal()).target.surface, 'Painting');
  assert.equal(await run(() => window.__echo.sim.calls.length), 0, 'a chip only proposes');
  const flag = tab.locator('[data-apt-named-aim-flag="authored-old"]');
  assert.match(await flag.textContent(), /“Old desk” predates the latest scan\./);
  assert.ok(await tab.locator('[data-apt-named-aim-chip="authored-old"] .flag').count(), 'the chip carries an attention dot');
  await flag.getByRole('button', { name: 'Preview it' }).click();
  assert.equal((await proposal()).target.surface, 'Old desk', 'Preview it proposes the old spot to look at');
  await flag.getByRole('button', { name: 'Keep as is' }).click();
  await until(() => window.__echo.saves.length === 1);
  await run(() => window.__echo.finishSave('saved'));
  await until(() => !document.querySelector('[data-apt-named-aim-flag="authored-old"]'));
  assert.equal(await run(() => window.__echo.state().model.targets.find(target => target.id === 'authored-old').aim_basis.scene), 'authored-echo-r1');
});

step('R04 inspector from the list and the room: users, reach, Aim a light here proposes, Copy id, Geometry', async () => {
  await tab.locator('[data-apt-named-aim-open="authored-painting"]').click();
  const inspector = tab.locator('[data-apt-named-aim-inspector="authored-painting"]');
  await inspector.waitFor();
  assert.match(await inspector.textContent(), /Named aim · art · wall surface/);
  assert.match(await inspector.textContent(), /No light is aimed here now\./);
  assert.equal(await inspector.locator('[data-apt-named-aim-reach-row="authored-echo-spot"]').getAttribute('data-state'), 'reachable');
  await inspector.locator('[data-apt-named-aim-automation] summary').click();
  assert.equal(await inspector.locator('[data-apt-named-aim-id]').textContent(), 'authored-painting');
  assert.match(await inspector.locator('[data-apt-named-aim-automation]').textContent(), /how automations reference it is proposed/);
  await inspector.locator('[data-apt-named-aim-copy]').click();
  await until(() => window.__echo.copied.length === 1);
  assert.deepEqual(await run(() => window.__echo.copied), ['authored-painting']);
  await inspector.locator('[data-apt-named-aim-geometry] summary').click();
  assert.match(await inspector.locator('[data-apt-named-aim-geometry]').textContent(), /Centre2, 3\.99, 1\.5 m/);
  await inspector.locator('[data-apt-named-aim-edit]').click();
  assert.deepEqual(await run(() => window.__echo.events.filter(event => event.type === 'edit').map(event => event.id)), ['authored-painting']);
  await inspector.locator('[data-apt-named-aim-aim-here="authored-echo-spot"]').click();
  await until(() => document.getElementById('panel').dataset.panel === 'aim');
  assert.equal((await proposal()).target.surface, 'Painting', 'Aim a light here creates a proposal');
  assert.equal(await run(() => window.__echo.sim.calls.length), 0, 'nothing changes until Set');
  // From the room: the painting's marker selects it when no light is being aimed.
  await run(() => window.__echo.reset());
  const painting = await at(PAINTING);
  await tab.mouse.click(painting.x, painting.y);
  await tab.locator('[data-apt-named-aim-inspector="authored-painting"]').waitFor();
  assert.equal(await run(() => window.__echo.echo.escape('selection')), true, 'Escape clears the named-aim selection');
  await tab.locator('[data-apt-named-aim-list]').waitFor();
});

step('Escape order and keys: a drag cancels alone, then the proposal is discarded and Ctrl+Z restores it', async () => {
  await aimSpot();
  await dragTo(POINT.wall);
  const kept = await proposal();
  await dragTo(POINT.nearPainting, { release: false });
  assert.equal(await run(() => window.__echo.echo.escape('drag')), true);
  await tab.mouse.up();
  assert.equal((await proposal()).id, kept.id, 'cancelling a drag leaves the proposal unchanged');
  assert.equal(await run(() => window.__echo.echo.escape('proposal')), true);
  assert.equal(await proposal(), null);
  await tab.locator('#room').click({ position: { x: 5, y: 5 } }).catch(() => {});
  await run(() => document.activeElement?.blur());
  await tab.keyboard.press('Control+z');
  await until(() => !!window.__echo.proposal());
  assert.equal((await proposal()).id, kept.id, 'Ctrl+Z restores the discarded proposal');
  await tab.keyboard.press('Enter');
  await until(() => window.__echo.sim.calls.length === 1);
  assert.deepEqual(await run(() => window.__echo.toasts), ['Aim set · simulated, no hardware command sent'], 'Enter sets through the same Set');
});

test('teardown: zero transport', { timeout: DEADLINE_MS.boot }, async () => {
  try {
    if (tab) assert.deepEqual(await run(() => window.__transport), [], 'no fetch, XHR, WebSocket, EventSource or beacon');
    assert.deepEqual(errors, []);
  } finally { await browser?.close(); await new Promise(resolve => server ? server.close(resolve) : resolve()); }
});
