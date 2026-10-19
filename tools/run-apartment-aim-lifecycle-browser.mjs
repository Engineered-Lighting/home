#!/usr/bin/env node
// Aim lifecycle in real Chromium against the fixed SPEC selector and ARIA
// vocabulary: the actual overlay bridge, aim presentation, proposal store and
// room overlay JSX, mounted by an authored harness host over a synthetic room.
// Synthetic Victoria fixtures only: no real homes, coordinates, digests,
// registration or assets. No WebGL frame is drawn; picking is a real Three
// raycast. Runs as plain `node`; node:test sets the exit code.
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
// Worst case 40 s + 14 × 8 s = 152 s, inside the runner's 180 s per harness.
const DEADLINE_MS = Object.freeze({ boot: 40000, step: 8000, wait: 3000 });

// The test-facing vocabulary, copied from SPEC independently of the product.
const V = Object.freeze({
  root: '[data-apt-room-overlay]', label: id => `[data-apt-fixture-label="${id}"]`, ring: id => `[data-apt-fixture-ring="${id}"]`,
  reticle: '[data-apt-aim-reticle]', chip: '[data-apt-aim-chip]', chipTitle: '[data-apt-aim-chip-title]',
  chipMetrics: '[data-apt-aim-chip-metrics]', chipNote: '[data-apt-aim-chip-note]', disclosure: id => `[data-apt-aim-disclosure="${id}"]`,
  proposal: '[data-apt-aim-proposal]', blocker: '[data-apt-aim-blocker]', bar: '[data-apt-aim-bar]', barTarget: '[data-apt-aim-bar-target]',
  set: '[data-apt-aim-action="set"]', discard: '[data-apt-aim-action="discard"]', pending: '[data-apt-aim-pending]',
  spot: '[data-apt-directional-mode="spot"]',
});
const ARIA = Object.freeze({ reticle: 'Aim handle. Drag onto a surface, or use the arrow keys. Enter keeps the proposal, Escape cancels.',
  bar: 'Proposed aim', set: 'Set aim', discard: 'Discard', pending: 'aim pending' });
const VICTORIA_ORIGIN = 'Modeled from the socket display point · optical pivot not registered';
const VICTORIA_TRAVEL = 'travel limits not commissioned';
const VOCABULARY = { root: 'data-apt-room-overlay', label: 'data-apt-fixture-label', ring: 'data-apt-fixture-ring',
  reticle: 'data-apt-aim-reticle', chip: 'data-apt-aim-chip', chipTitle: 'data-apt-aim-chip-title', chipMetrics: 'data-apt-aim-chip-metrics',
  chipNote: 'data-apt-aim-chip-note', disclosure: 'data-apt-aim-disclosure', proposal: 'data-apt-aim-proposal', blocker: 'data-apt-aim-blocker',
  bar: 'data-apt-aim-bar', barTarget: 'data-apt-aim-bar-target', action: 'data-apt-aim-action', pending: 'data-apt-aim-pending',
  state: 'data-state', aria: ARIA };

// Authored synthetic room (z-up, floor at 0). Points the steps aim at.
const POINT = Object.freeze({
  floorA: [0.8, 2.2, 0], floorB: [1.8, 2.5, 0], desk: [2.9, 0.75, 0.75], blocked: [1.9, 1.9, 0],
  nearPainting: [2.1, 3, 1.45], painting: [2, 2.99, 1.5], steepWall: [3.5, 3, 1.6], offRoom: [9, 9, 9],
});

const FILES = new Map([
  ['/vendor/react-18.3.1/react.production.min.js', 'vendor/react-18.3.1/react.production.min.js'],
  ['/vendor/react-18.3.1/react-dom.production.min.js', 'vendor/react-18.3.1/react-dom.production.min.js'],
  ['/vendor/babel-7.29.0/babel.min.js', 'vendor/babel-7.29.0/babel.min.js'],
  ['/vendor/three-0.180.0/three.module.js', 'vendor/three-0.180.0/three.module.js'],
  ['/vendor/three-0.180.0/three.core.js', 'vendor/three-0.180.0/three.core.js'],
  ['/home-apartment-aim-proposals.js', 'home-apartment-aim-proposals.js'],
  ['/home-3d/apartment-ui-overlay-bridge.mjs', 'home-3d/apartment-ui-overlay-bridge.mjs'],
  ['/home-3d/apartment-aim-presentation.mjs', 'home-3d/apartment-aim-presentation.mjs'],
]);

const HARNESS_JSX = String.raw`
function AptAimHarness({ api }) {
  const [state, setState] = React.useState(api.initial);
  const latest = React.useRef(state); latest.current = state;
  const update = patch => setState(previous => ({ ...previous, ...patch }));
  React.useLayoutEffect(() => {
    api.set = patch => ReactDOM.flushSync(() => update(patch));
    api.state = () => latest.current;
    api.ready = true;
  }, []);
  const site = api.sites[state.site];
  const record = event => api.events.push(JSON.parse(JSON.stringify(event)));
  const aim = state.tool === "spot" && state.selectedId === site.aim.descriptor.fixture_id ? site.aim : null;
  return <>
    <div className="harness-pill" role="group" aria-label="Room lighting tool">
      <button type="button" data-apt-directional-mode="zones" aria-pressed={state.tool === "zones"} onClick={() => update({ tool: "zones" })}>Zones</button>
      <button type="button" data-apt-directional-mode="spot" aria-pressed={state.tool === "spot"} onClick={() => update({ tool: "spot" })}>
        Spot<AptSpotPendingDot store={api.store} siteId={state.site} descriptor={site.aim.descriptor} tool={state.tool} />
      </button>
    </div>
    {ReactDOM.createPortal(<AptRoomOverlay presentation={api.presentation} store={api.store} siteId={state.site}
      fixtures={site.fixtures} selectedId={state.selectedId} hoverId={state.hoverId}
      pillFixtureId={state.pill ? state.selectedId : null} aim={aim}
      onSelect={id => { record({ type: "select", id }); update({ selectedId: id }); }}
      onHover={id => { record({ type: "hover", id }); update({ hoverId: id }); }}
      onAimStart={event => record({ type: "aim_start", ...event })}
      onPropose={({ fixture_id, scope, origin, target }) => {
        const result = api.store.propose(scope, { origin, target });
        record({ type: "propose", fixture_id, origin, ok: result.ok, reason: result.reason || null, target });
      }}
      onSet={({ fixture_id, scope, proposal }) => {
        const result = api.store.take(scope, proposal.id);
        record({ type: "set", fixture_id, id: proposal.id, ok: result.ok });
      }}
      onDiscard={({ fixture_id, scope, proposal }) => {
        const result = api.store.discard(scope, proposal.id);
        if (result.ok) api.discarded.push(proposal);
        record({ type: "discard", fixture_id, id: proposal.id, ok: result.ok });
      }} />, api.overlayHost)}
  </>;
}
window.AptAimHarness = AptAimHarness;
`;

const HARNESS_MODULE = String.raw`
import * as THREE from 'three';
import { createApartmentUiOverlayLayer } from '/home-3d/apartment-ui-overlay-bridge.mjs';
import * as Presentation from '/home-3d/apartment-aim-presentation.mjs';
const hex = character => character.repeat(64);
const deepFreeze = value => { if (value && typeof value === 'object') { Object.values(value).forEach(deepFreeze); Object.freeze(value); } return value; };
const binding = () => ({ content_revision: 'authored-room-1', registration_version: 1, manifest_digest: hex('a'), frame_digest: hex('b'), canonical_collision_digest: hex('c') });
const VIRTUAL_LIMITS = { pan_min_deg: -180, pan_max_deg: 180, tilt_min_deg: -90, tilt_max_deg: 90, limit_role: 'virtual_preview', physical_travel: 'unknown' };
const COMMISSIONED = { pan_min_deg: -180, pan_max_deg: 180, tilt_min_deg: 15, tilt_max_deg: 90, limit_role: 'simulated', physical_travel: 'commissioned' };
const ANCHOR = [1.2, 1.0, 2.39], ORIGIN = [1.2, 1.0, 2.25];
const descriptor = (fixture_id, generation) => deepFreeze({ schema: 'home-virtual-fixture-interaction-1', generation, fixture_id,
  mount_id: 'mount-' + fixture_id, binding: binding(), equipment_revision: 1, equipment_digest: hex('d'),
  display_anchor_m: ANCHOR, display_origin_m: ORIGIN, horizontal_world_reference: [1, 0, 0], origin_role: 'mount_anchor',
  optical_origin_m: null, state: { pan_deg: 0, tilt_deg: 90 }, target: null, truth_role: 'virtual_preview', physical_commands: false });
const sites = {
  victoria: { fixtures: [
      { fixture_id: 'authored-den-spot', label: 'Den spotlight', short_label: 'Spotlight', display_anchor_m: ANCHOR, on: true },
      { fixture_id: 'authored-den-lamp', label: 'Reading lamp', short_label: 'Lamp', display_anchor_m: [3.0, 2.4, 2.39], on: false }],
    aim: deepFreeze({ descriptor: descriptor('authored-den-spot', 3), limits: VIRTUAL_LIMITS,
      namedAims: [{ id: 'authored-painting', name: 'Painting', point_m: [2, 2.99, 1.5], normal_m: [0, -1, 0], size_m: [0.9, 0.65] }] }) },
  echo: { fixtures: [{ fixture_id: 'authored-sim-spot', label: 'Simulated spot', short_label: 'Sim spot', display_anchor_m: ANCHOR, on: true }],
    aim: deepFreeze({ descriptor: descriptor('authored-sim-spot', 5), limits: COMMISSIONED, namedAims: [] }) },
};
const host = document.getElementById('room'), overlayHost = document.getElementById('overlay');
const width = host.clientWidth, height = host.clientHeight;
const camera = new THREE.PerspectiveCamera(50, width / height, 0.05, 60);
camera.up.set(0, 0, 1); camera.position.set(5.5, -1.5, 3.2); camera.lookAt(1.9, 1.4, 0.6);
const scene = new THREE.Scene(), apartmentRoot = new THREE.Group(), overlayRoot = new THREE.Group();
scene.add(apartmentRoot, overlayRoot);
const surface = (name, geometry, position, rotation = [0, 0, 0]) => {
  const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }));
  mesh.name = name; mesh.position.fromArray(position); mesh.rotation.set(...rotation); apartmentRoot.add(mesh); return mesh;
};
const floor = surface('Floor', new THREE.PlaneGeometry(4, 3), [2, 1.5, 0]);
surface('Back wall', new THREE.PlaneGeometry(4, 2.6), [2, 3, 1.3], [Math.PI / 2, 0, 0]);
surface('Left wall', new THREE.PlaneGeometry(2.6, 3), [0, 1.5, 1.3], [0, Math.PI / 2, 0]);
const desk = surface('Desk', new THREE.BoxGeometry(1, 0.7, 0.75), [2.9, 0.75, 0.375]);
// A hanging shelf a quarter of the way along the beam to the blocked spot.
const shelfAt = ORIGIN.map((value, index) => value + 0.25 * ([1.9, 1.9, 0][index] - value));
const shelf = surface('Shelf', new THREE.BoxGeometry(0.16, 0.16, 0.04), shelfAt);
scene.updateMatrixWorld(true);
const raycaster = new THREE.Raycaster();
function pickSurface(clientX, clientY) {
  const rect = host.getBoundingClientRect();
  raycaster.setFromCamera(new THREE.Vector2((clientX - rect.left) / rect.width * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1), camera);
  const hit = raycaster.intersectObjects(apartmentRoot.children, false)[0];
  if (!hit) return null;
  const normal = hit.face.normal.clone().transformDirection(hit.object.matrixWorld).normalize();
  return { point: hit.point.toArray(), normal: normal.toArray(), object: hit.object };
}
function projectWorld(world) {
  const v = world.clone().project(camera);
  return { x: (v.x + 1) / 2 * width, y: (1 - v.y) / 2 * height, visible: v.z > -1 && v.z < 1 };
}
// Stands in for Repair's canonical beam trace: blockers between the light and the spot.
function traceBeam(from, to) {
  const start = new THREE.Vector3(...from), direction = new THREE.Vector3(...to).sub(start), distance = direction.length();
  raycaster.set(start, direction.normalize()); raycaster.far = distance - 0.04;
  const hit = raycaster.intersectObjects([shelf, desk], false)[0];
  raycaster.far = Infinity;
  return hit ? { point_m: hit.point.toArray(), name: hit.object.name } : null;
}
const controller = new AbortController();
const api = window.__aim = { sites, events: [], discarded: [], insets: null, live: true, overlayHost, signal: controller.signal,
  initial: { site: 'victoria', selectedId: null, hoverId: null, tool: 'spot', pill: false } };
api.layer = createApartmentUiOverlayLayer({ THREE, camera, apartmentRoot, overlayRoot, host, projectWorld, pickSurface,
  isCurrent: () => api.live, signal: controller.signal });
api.presentation = Presentation.createAimPresentation({ layer: api.layer, host, isCurrent: () => api.live && !controller.signal.aborted,
  getInsets: () => api.insets, traceBeam });
api.store = window.HomeApartmentAimProposals.createAimProposalStore();
api.vocabulary = JSON.parse(JSON.stringify(Presentation.AIM_OVERLAY_VOCABULARY));
api.toClient = point => { const at = api.layer.project(point), rect = host.getBoundingClientRect(); return at && { x: rect.left + at.x, y: rect.top + at.y }; };
api.pick = (x, y) => api.layer.pick(x, y);
api.scope = siteId => api.store.scopeFromDescriptor(siteId, sites[siteId].aim.descriptor);
api.proposal = siteId => api.store.current(api.scope(siteId || api.state().site));
api.descriptorJson = siteId => JSON.stringify(sites[siteId].aim.descriptor);
api.abort = () => controller.abort();
api.reset = () => { api.store.clearAll(); api.events.length = 0; api.discarded.length = 0; api.insets = null; api.set({ ...api.initial }); };
// Classic-script boot: the same Babel options as index.html, then execute.
const babel = source => Babel.transform(source, { presets: ['react', 'env'],
  plugins: ['transform-class-properties', 'transform-object-rest-spread', 'transform-flow-strip-types'] }).code;
(0, eval)(babel(window.__overlaySource));
(0, eval)(babel(window.__harnessSource));
ReactDOM.createRoot(document.getElementById('app')).render(React.createElement(window.AptAimHarness, { api }));
`;

function page() {
  const inline = value => JSON.stringify(value).replace(/</g, '\\u003c');
  return `<!doctype html><html><head><meta charset="utf-8"><link rel="icon" href="data:,">
<style>html,body{margin:0;background:#101114;color:#eee;font:13px sans-serif}
.harness-pill{display:flex;gap:4px;padding:8px;height:44px;box-sizing:border-box}.harness-pill button{position:relative;padding:0 12px}
#room{position:relative;width:1100px;height:700px;overflow:hidden;background:#1a1c20}#overlay{position:absolute;inset:0;pointer-events:none}</style>
<script>
window.__transport = [];
(function () {
  const note = kind => window.__transport.push(kind);
  const fetch = window.fetch; window.fetch = function () { note('fetch'); return fetch.apply(this, arguments); };
  const open = XMLHttpRequest.prototype.open; XMLHttpRequest.prototype.open = function () { note('xhr'); return open.apply(this, arguments); };
  for (const name of ['WebSocket', 'EventSource']) if (window[name]) window[name] = new Proxy(window[name], { construct(target, args) { note(name); return Reflect.construct(target, args); } });
  if (navigator.sendBeacon) navigator.sendBeacon = function () { note('beacon'); return false; };
})();
window.__overlaySource = ${inline(fs.readFileSync(path.join(SRC, 'home-apartment-room-overlay.jsx'), 'utf8'))};
window.__harnessSource = ${inline(HARNESS_JSX)};
</script>
<script src="/vendor/react-18.3.1/react.production.min.js"></script>
<script src="/vendor/react-18.3.1/react-dom.production.min.js"></script>
<script src="/vendor/babel-7.29.0/babel.min.js"></script>
<script src="/home-apartment-aim-proposals.js"></script>
<script type="importmap">{"imports":{"three":"/vendor/three-0.180.0/three.module.js"}}</script>
</head><body><div id="app"></div><div id="room"><div id="overlay"></div></div>
<script type="module">${HARNESS_MODULE}</script></body></html>`;
}

function browserOptions() {
  const installed = fs.existsSync(chromium.executablePath());
  assert.ok(installed || process.platform === 'win32', 'pinned Chromium must exist on Linux');
  const args = ['--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'];
  return { headless: true, args, ...(process.platform === 'linux' ? { channel: 'chromium' } : !installed ? { channel: 'msedge' } : {}) };
}

let server, browser, tab, origin, booted = false;
const served = [], late = [], errors = [];
const run = (fn, arg) => tab.evaluate(fn, arg);
const until = (fn, arg) => tab.waitForFunction(fn, arg, { timeout: DEADLINE_MS.wait });
const at = async point => { const value = await run(p => window.__aim.toClient(p), point); assert.ok(value, 'projects: ' + JSON.stringify(point)); return value; };
const visible = selector => tab.locator(selector).isVisible();
const text = selector => tab.locator(selector).textContent();
const events = type => run(t => window.__aim.events.filter(event => !t || event.type === t), type);
const proposal = site => run(s => { const p = window.__aim.proposal(s); return p && JSON.parse(JSON.stringify(p)); }, site);
async function center(selector) {
  const box = await tab.locator(selector).boundingBox();
  assert.ok(box, selector + ' has a box');
  return { x: box.x + box.width / 2, y: box.y + box.height / 2, box };
}
async function selectSpot(site = 'victoria') {
  const id = site === 'victoria' ? 'authored-den-spot' : 'authored-sim-spot';
  await run(([s, i]) => window.__aim.set({ site: s, selectedId: i, tool: 'spot' }), [site, id]);
  await until(() => !document.querySelector('[data-apt-aim-reticle]').hidden);
}
// Press on the reticle and move to a world point; returns the release pixel.
async function dragTo(point, { release = true, steps = 6 } = {}) {
  const from = await center(V.reticle), to = await at(point);
  await tab.mouse.move(from.x, from.y); await tab.mouse.down();
  await tab.mouse.move(to.x, to.y, { steps });
  await until(() => !document.querySelector('[data-apt-aim-chip]').hidden);
  if (release) await tab.mouse.up();
  return to;
}
async function chip() {
  return run(() => {
    const root = document.querySelector('[data-apt-aim-chip]'), get = s => root.querySelector(s)?.textContent ?? null;
    return { hidden: root.hidden, state: root.getAttribute('data-state'), title: get('[data-apt-aim-chip-title]'), metrics: get('[data-apt-aim-chip-metrics]'),
      note: get('[data-apt-aim-chip-note]'), text: root.textContent,
      disclosures: [...root.querySelectorAll('[data-apt-aim-disclosure]')].map(node => [node.getAttribute('data-apt-aim-disclosure'), node.textContent]) };
  });
}
const step = (name, body) => test(name, { timeout: DEADLINE_MS.step }, async () => {
  assert.ok(booted, 'harness booted');
  await run(() => window.__aim.reset());
  await tab.mouse.move(2, 2);
  await body();
  assert.deepEqual(errors, [], 'no page errors');
});

test('boot: the actual modules mount in Chromium with the fixed vocabulary and no transport', { timeout: DEADLINE_MS.boot }, async () => {
  const html = page();
  server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1');
    (booted ? late : served).push(url.pathname);
    if (url.pathname === '/') { response.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' }); response.end(html); return; }
    const file = FILES.get(url.pathname);
    if (!file) { response.writeHead(404); response.end(); return; }
    response.writeHead(200, { 'content-type': 'text/javascript; charset=utf-8', 'cache-control': 'no-store' });
    response.end(fs.readFileSync(path.join(SRC, file)));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch(browserOptions());
  const context = await browser.newContext({ viewport: { width: 1200, height: 800 }, deviceScaleFactor: 1, serviceWorkers: 'block' });
  tab = await context.newPage();
  tab.on('pageerror', error => errors.push(String(error?.message || error)));
  tab.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  tab.on('request', request => { if (booted) late.push(request.url()); });
  tab.on('websocket', socket => late.push(socket.url()));
  await tab.goto(origin + '/');
  await tab.waitForFunction(() => window.__aim?.ready === true, null, { timeout: DEADLINE_MS.boot / 2 });
  booted = true;
  assert.deepEqual(errors, []);
  assert.deepEqual(served.filter(name => name !== '/' && !FILES.has(name)), [], 'only the allowlisted modules were requested');
  assert.deepEqual(await run(() => window.__aim.vocabulary), VOCABULARY, 'product vocabulary equals the SPEC copy');
  assert.deepEqual(await run(() => window.__transport), []);
  for (const point of Object.entries(POINT).filter(([key]) => key !== 'offRoom')) await at(point[1]);
});

step('Direct selection: hover shows a label ring only; a label click selects with emphasis', async () => {
  await run(() => window.__aim.set({ tool: 'zones' }));
  assert.equal(await visible(V.label('authored-den-spot')), true);
  assert.equal(await visible(V.label('authored-den-lamp')), true);
  assert.equal(await text(V.label('authored-den-spot')), 'Spotlight', 'few fixtures show their short names');
  assert.equal(await visible(V.ring('authored-den-lamp')), false);
  const lamp = await center(V.label('authored-den-lamp'));
  await tab.mouse.move(lamp.x, lamp.y);
  await until(id => document.querySelector(`[data-apt-fixture-label="${id}"]`).getAttribute('data-state') === 'hover', 'authored-den-lamp');
  assert.equal(await visible(V.ring('authored-den-lamp')), true);
  assert.equal(await tab.locator(V.ring('authored-den-lamp')).getAttribute('data-state'), 'hover');
  assert.equal(await text(V.label('authored-den-lamp')), 'Reading lamp');
  assert.equal(await run(() => window.__aim.state().selectedId), null, 'hover never changes the inspector');
  const spot = await center(V.label('authored-den-spot'));
  await tab.mouse.click(spot.x, spot.y);
  await until(id => document.querySelector(`[data-apt-fixture-label="${id}"]`).getAttribute('data-state') === 'selected', 'authored-den-spot');
  assert.equal(await tab.locator(V.label('authored-den-spot')).getAttribute('aria-current'), 'true');
  assert.equal(await tab.locator(V.ring('authored-den-spot')).getAttribute('data-state'), 'selected');
  assert.equal(await text(V.label('authored-den-spot')), 'Den spotlight');
  assert.deepEqual((await events('select')).map(event => event.id), ['authored-den-spot']);
  assert.equal(await tab.locator(V.label('authored-den-spot')).getAttribute('tabindex'), '-1', 'canvas labels are skipped by Tab; the list is the keyboard path');
  // With the pill showing, the pill carries the name and the ring stays.
  await run(() => window.__aim.set({ pill: true }));
  await until(() => document.querySelector('[data-apt-fixture-label="authored-den-spot"]').hidden);
  assert.equal(await visible(V.ring('authored-den-spot')), true);
  assert.equal(await visible(V.reticle), false, 'no reticle on the Zones tool');
});

step('Drag aim: the reticle chip, a cyan proposal, the warm aim untouched, then Enter sets', async () => {
  const before = await run(() => window.__aim.descriptorJson('victoria'));
  await selectSpot();
  const reticle = await center(V.reticle);
  assert.deepEqual([reticle.box.width, reticle.box.height], [44, 44], '44 px hit area');
  assert.equal(await tab.locator(V.reticle).getAttribute('aria-label'), ARIA.reticle);
  assert.equal(await tab.locator(V.reticle).getAttribute('data-state'), 'current');
  await tab.mouse.click(reticle.x, reticle.y);
  assert.equal(await visible(V.chip), false);
  assert.equal(await proposal(), null, 'a click on the reticle proposes nothing');
  await tab.mouse.move(2, 2);
  const release = await dragTo(POINT.floorA, { release: false });
  const live = await chip();
  assert.equal(live.state, 'valid'); assert.equal(live.title, 'Floor');
  const expected = Math.hypot(...POINT.floorA.map((value, index) => value - [1.2, 1.0, 2.25][index]));
  const [, distance, tilt] = /^(\d+\.\d\d) m · (\d+)° tilt$/.exec(live.metrics) || [];
  assert.ok(Math.abs(Number(distance) - expected) <= 0.02, `${live.metrics} vs ${expected.toFixed(2)} m`);
  assert.ok(Math.abs(Number(tilt) - Math.acos(2.25 / expected) * 180 / Math.PI) <= 1.5, live.metrics);
  assert.deepEqual(live.disclosures, [['optical-origin', VICTORIA_ORIGIN], ['travel-limits', VICTORIA_TRAVEL]]);
  assert.deepEqual((await events('aim_start')).map(event => [event.fixture_id, event.source]), [['authored-den-spot', 'drag'], ['authored-den-spot', 'drag']]);
  assert.equal(await proposal(), null, 'dragging proposes nothing yet');
  await tab.mouse.up();
  await until(() => !document.querySelector('[data-apt-aim-bar]').hidden);
  const pending = await proposal();
  assert.equal(pending.origin, 'manual'); assert.equal(pending.target.valid, true); assert.equal(pending.target.surface, 'Floor');
  const native = await run(([x, y]) => window.__aim.pick(x, y), [release.x, release.y]);
  const offset = Math.hypot(...pending.target.point_m.map((value, index) => value - native.point[index]));
  assert.ok(offset <= 0.03, `proposal ${offset.toFixed(4)} m from the native hit (≤ 3 cm)`);
  assert.equal(await run(() => window.__aim.descriptorJson('victoria')), before, 'the current aim and descriptor are unchanged');
  assert.equal(await visible(V.chip), false);
  assert.equal(await visible(V.proposal), true); assert.equal(await tab.locator(V.proposal).getAttribute('data-state'), 'valid');
  assert.equal(await tab.locator(V.reticle).getAttribute('data-state'), 'proposed');
  assert.equal(await run(() => window.__aim.presentation.readback().proposal_drawn), true, 'cyan ray drawn in the room');
  assert.equal(await tab.getByRole('group', { name: ARIA.bar }).isVisible(), true);
  assert.equal(await text(V.barTarget), 'Floor');
  assert.equal(await tab.locator(V.set).textContent(), ARIA.set);
  assert.equal(await tab.locator(V.discard).textContent(), ARIA.discard);
  assert.equal(await run(() => document.activeElement?.hasAttribute('data-apt-aim-reticle')), true, 'focus stays on the reticle');
  await tab.keyboard.press('Enter');
  await until(() => document.querySelector('[data-apt-aim-bar]').hidden);
  assert.deepEqual((await events('set')).map(event => [event.id, event.ok]), [[pending.id, true]]);
  assert.equal(await proposal(), null);
  assert.equal(await tab.locator(V.reticle).getAttribute('data-state'), 'current');
});

step('Escape order: mid-drag cancels and keeps the proposal, next Escape discards, then nothing', async () => {
  await selectSpot();
  await dragTo(POINT.floorA);
  await until(() => !document.querySelector('[data-apt-aim-bar]').hidden);
  const first = await proposal();
  await dragTo(POINT.floorB, { release: false });
  assert.equal(await visible(V.bar), false, 'the bar hides while dragging');
  await tab.keyboard.press('Escape');
  await until(() => document.querySelector('[data-apt-aim-chip]').hidden);
  await tab.mouse.up();
  await until(() => !document.querySelector('[data-apt-aim-bar]').hidden);
  assert.equal((await proposal()).id, first.id, 'the cancelled drag left the proposal unchanged');
  assert.equal((await events('propose')).length, 1);
  await tab.keyboard.press('Escape');
  await until(() => document.querySelector('[data-apt-aim-bar]').hidden);
  assert.deepEqual((await events('discard')).map(event => [event.id, event.ok]), [[first.id, true]]);
  assert.equal(await proposal(), null);
  // The host's Cmd+Z restores exactly what was discarded.
  assert.equal(await run(() => window.__aim.store.restore(window.__aim.scope('victoria'), window.__aim.discarded[0]).ok), true);
  await until(() => !document.querySelector('[data-apt-aim-bar]').hidden);
  await tab.locator(V.reticle).focus();
  await tab.keyboard.press('Escape');
  await until(() => document.querySelector('[data-apt-aim-bar]').hidden);
  const count = (await events()).length;
  await tab.keyboard.press('Escape');
  assert.equal((await events()).length, count, 'with nothing pending the overlay takes no Escape step');
});

step('Blocked: the chip names the blocker with a marker, and the bar offers only Discard', async () => {
  await selectSpot();
  await dragTo(POINT.blocked, { release: false });
  const live = await chip();
  assert.equal(live.state, 'invalid'); assert.equal(live.title, 'Blocked by Shelf');
  assert.equal(await visible(V.blocker), true, 'red marker at the blocking point');
  assert.equal(await tab.locator(V.reticle).getAttribute('data-state'), 'invalid');
  await tab.mouse.up();
  await until(() => !document.querySelector('[data-apt-aim-bar]').hidden);
  const blocked = await proposal();
  assert.deepEqual([blocked.target.valid, blocked.target.reason], [false, 'blocked']);
  assert.equal(await tab.locator(V.bar).getAttribute('data-state'), 'blocked');
  assert.equal(await text(V.barTarget), 'Blocked');
  assert.equal(await tab.locator(V.set).count(), 0, 'no Set aim');
  assert.equal(await tab.locator(V.discard).count(), 1);
  assert.equal(await tab.locator(V.proposal).getAttribute('data-state'), 'invalid');
  await tab.locator(V.reticle).focus();
  await tab.keyboard.press('Enter');
  assert.deepEqual(await events('set'), [], 'Enter never sets a blocked proposal');
  assert.equal((await proposal()).id, blocked.id);
  await tab.locator(V.discard).click();
  await until(() => document.querySelector('[data-apt-aim-bar]').hidden);
  assert.deepEqual((await events('discard')).map(event => event.id), [blocked.id]);
});

step('Unreachable: only commissioned travel says out of reach, with angle and limit', async () => {
  await selectSpot('echo');
  await dragTo(POINT.steepWall, { release: false });
  const live = await chip();
  assert.equal(live.state, 'invalid');
  assert.match(live.title, /^Out of reach · (7[6-9]|8\d)° from straight down, limit 75°$/);
  assert.deepEqual(live.disclosures.map(([id]) => id), ['optical-origin'], 'commissioned travel drops the travel disclosure');
  await tab.mouse.up();
  await until(() => !document.querySelector('[data-apt-aim-bar]').hidden);
  assert.equal(await text(V.barTarget), 'Out of reach');
  assert.equal(await tab.locator(V.set).count(), 0);
  assert.equal((await proposal('echo')).target.reason, 'out_of_reach');
});

step('Victoria truthfulness: every spot keeps both disclosures and none claims reach', async () => {
  await selectSpot();
  const from = await center(V.reticle);
  await tab.mouse.move(from.x, from.y); await tab.mouse.down();
  const seen = [];
  for (const key of ['floorA', 'floorB', 'desk', 'steepWall', 'nearPainting', 'blocked', 'floorA']) {
    const to = await at(POINT[key]);
    await tab.mouse.move(to.x, to.y, { steps: 3 });
    await until(() => !document.querySelector('[data-apt-aim-chip]').hidden);
    const live = await chip();
    seen.push([key, live.title]);
    assert.deepEqual(live.disclosures, [['optical-origin', VICTORIA_ORIGIN], ['travel-limits', VICTORIA_TRAVEL]], key);
    assert.doesNotMatch(live.text.replace(VICTORIA_TRAVEL, ''), /out of reach|\breach(?:able|es)?\b|in range|within range|measured/i, key);
  }
  assert.equal(seen.find(([key]) => key === 'steepWall')[1], 'Back wall', 'a 78° spot is still offered on Victoria');
  await tab.mouse.up();
  await until(() => !document.querySelector('[data-apt-aim-bar]').hidden);
  assert.doesNotMatch(await text(V.bar), /reach/i);
  assert.equal(await run(() => window.__aim.presentation.readback().physical_commands), false);
  assert.deepEqual(await run(() => window.__transport), []);
});

step('Snap: near a named aim the chip says Snaps to “Painting” and the proposal takes its point', async () => {
  await selectSpot();
  await dragTo(POINT.nearPainting, { release: false });
  assert.equal((await chip()).title, 'Snaps to “Painting”');
  await tab.mouse.up();
  await until(() => !document.querySelector('[data-apt-aim-bar]').hidden);
  const snapped = await proposal();
  assert.deepEqual(snapped.target.point_m, POINT.painting); assert.equal(snapped.target.surface, 'Painting');
  assert.equal(await text(V.barTarget), 'Painting');
});

step('Chat open or sheet raised: the bar stays in the visible canvas and Set works from the room', async () => {
  const room = await tab.locator('#room').boundingBox();
  for (const insets of [{ right: 340 }, { bottom: 350 }, { right: 340, bottom: 350 }]) {
    await run(() => window.__aim.reset());
    await selectSpot();
    await dragTo(POINT.floorB);
    await until(() => !document.querySelector('[data-apt-aim-bar]').hidden);
    // Chat opens (or the sheet rises) over a pending proposal.
    await run(value => { window.__aim.insets = value; window.__aim.set({}); }, insets);
    const box = await tab.locator(V.bar).boundingBox();
    assert.ok(box.x >= room.x && box.x + box.width <= room.x + room.width - (insets.right || 0) + 0.5, JSON.stringify(insets) + ' horizontal');
    assert.ok(box.y >= room.y && box.y + box.height <= room.y + room.height - (insets.bottom || 0) + 0.5, JSON.stringify(insets) + ' vertical');
    const id = (await proposal()).id;
    await tab.locator(V.set).click();
    await until(() => document.querySelector('[data-apt-aim-bar]').hidden);
    assert.deepEqual((await events('set')).map(event => event.id), [id]);
  }
});

step('Pending dot: the Spot button shows a cyan dot while an aim waits, except on Spot itself', async () => {
  await selectSpot();
  assert.equal(await tab.locator(V.pending).count(), 0);
  await dragTo(POINT.floorA);
  await until(() => !document.querySelector('[data-apt-aim-bar]').hidden);
  assert.equal(await tab.locator(V.pending).count(), 0, 'on Spot the bar is showing instead');
  await tab.locator('[data-apt-directional-mode="zones"]').click();
  await until(() => !!document.querySelector('[data-apt-aim-pending]'));
  assert.equal(await tab.locator(`${V.spot} ${V.pending}`).isVisible(), true);
  assert.equal(await tab.getByRole('button', { name: `Spot (${ARIA.pending})`, exact: true }).count(), 1);
  assert.equal(await visible(V.bar), false, 'the proposal waits off the Spot tool');
  await tab.locator(V.spot).click();
  await until(() => !document.querySelector('[data-apt-aim-pending]') && !document.querySelector('[data-apt-aim-bar]').hidden);
  // Focus is on the pill now; the global Escape ladder is the host's (F04).
  await tab.locator(V.discard).click();
  await run(() => window.__aim.set({ tool: 'zones' }));
  assert.equal(await tab.locator(V.pending).count(), 0, 'no dot once discarded');
});

step('Keyboard: arrows on the focused reticle nudge the proposal 10 px, Shift 40 px', async () => {
  await selectSpot();
  await dragTo(POINT.floorB);
  await until(() => !document.querySelector('[data-apt-aim-bar]').hidden);
  const first = await proposal(), start = await at(first.target.point_m);
  await tab.locator(V.reticle).focus();
  await tab.keyboard.press('ArrowRight');
  await until(id => window.__aim.proposal()?.id !== id, first.id);
  const once = await at((await proposal()).target.point_m);
  assert.ok(Math.abs(once.x - start.x - 10) <= 1.5 && Math.abs(once.y - start.y) <= 1.5, JSON.stringify([start, once]));
  const id = (await proposal()).id;
  await tab.keyboard.press('Shift+ArrowUp');
  await until(previous => window.__aim.proposal()?.id !== previous, id);
  const twice = await at((await proposal()).target.point_m);
  assert.ok(Math.abs(twice.y - once.y + 40) <= 1.5 && Math.abs(twice.x - once.x) <= 1.5, JSON.stringify([once, twice]));
  assert.deepEqual((await events('aim_start')).map(event => event.source), ['drag', 'keys', 'keys']);
  assert.equal(await visible(V.bar), true);
});

step('A proposal stays with its light across selection and home switches', async () => {
  await selectSpot();
  await dragTo(POINT.floorA);
  await until(() => !document.querySelector('[data-apt-aim-bar]').hidden);
  const kept = await proposal();
  await run(() => window.__aim.set({ selectedId: 'authored-den-lamp' }));
  await until(() => document.querySelector('[data-apt-aim-bar]').hidden && document.querySelector('[data-apt-aim-reticle]').hidden);
  assert.equal(await visible(V.proposal), false);
  await selectSpot('echo');
  assert.equal(await visible(V.bar), false, 'Echo has no proposal of its own');
  await selectSpot('victoria');
  await until(() => !document.querySelector('[data-apt-aim-bar]').hidden);
  assert.equal((await proposal()).id, kept.id);
});

step('Off-room drags say No surface here and leave the proposal alone', async () => {
  await selectSpot();
  await dragTo(POINT.floorA);
  await until(() => !document.querySelector('[data-apt-aim-bar]').hidden);
  const kept = await proposal();
  const room = await tab.locator('#room').boundingBox(), from = await center(V.reticle);
  await tab.mouse.move(from.x, from.y); await tab.mouse.down();
  await tab.mouse.move(room.x + room.width - 4, room.y + 4, { steps: 4 });
  await until(() => !document.querySelector('[data-apt-aim-chip]').hidden);
  const live = await chip();
  assert.deepEqual([live.state, live.title, live.metrics], ['invalid', 'No surface here', null]);
  await tab.mouse.up();
  await until(() => !document.querySelector('[data-apt-aim-bar]').hidden);
  assert.equal((await proposal()).id, kept.id);
});

step('Retirement: aborting the owner signal withdraws the presentation without errors', async () => {
  await selectSpot();
  await dragTo(POINT.floorA);
  await until(() => !document.querySelector('[data-apt-aim-bar]').hidden);
  await run(() => { window.__aim.abort(); window.__aim.set({ hoverId: 'authored-den-lamp' }); });
  await until(() => window.__aim.presentation.readback().disposed === true);
  for (const selector of [V.reticle, V.bar, V.proposal, V.chip, V.label('authored-den-spot'), V.label('authored-den-lamp')])
    assert.equal(await visible(selector), false, selector);
  assert.equal(await run(() => window.__aim.presentation.readback().proposal_drawn), false);
  assert.ok(await proposal(), 'retiring the view never touches the store');
});

test('zero transport across the whole lifecycle', { timeout: DEADLINE_MS.step }, async () => {
  assert.ok(booted);
  assert.deepEqual(late, [], 'no request after boot');
  assert.deepEqual(await run(() => window.__transport), []);
  assert.deepEqual(errors, []);
});

test.after(async () => {
  await browser?.close();
  await new Promise(resolve => server ? server.close(resolve) : resolve());
});
