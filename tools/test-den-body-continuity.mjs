// The live body's continuity in the client (app/src/home-den-live-view.mjs), on a fake EventSource and a fake clock:
// the backlog escape, the warm clock re-anchor, the 2 s lease bound and the counters in HomeDenLiveView.status().body.
// The view runs as served, with the real live-body decoder; the people layer, the body layer and labels, Qwen's line and
// the caption data are stand-ins. Every frame is the authored fixture body, and every presentation frame checks that
// nothing shown is past its producer expiry.
//   node --test tools/test-den-body-continuity.mjs
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { register } from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { measuredBodyEvent } from './fixtures/den-measured-body.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const VIEW = pathToFileURL(path.join(ROOT, 'app/src/home-den-live-view.mjs')).href;
const STUBS = {
    '/app/src/home-3d/apartment-people-layer.mjs': 'export const validPeopleEvent = () => false;'
        + ' export const attachApartmentPeopleLayer = () => ({ update() {}, dispose() {}, status: () => ({}), markers: () => [] });',
    '/app/src/home-3d/apartment-body-layer.mjs': 'export const attachApartmentBodyLayer = (...args) => globalThis.__denBodyHarness.attachLayer(...args);',
    '/app/src/home-3d/apartment-body-labels.mjs': 'export const attachApartmentBodyLabels = () => ({ tick() {}, clear() {}, dispose() {} });',
    '/app/src/home-den-live-qwen.mjs': "export const qwenLine = () => '';",
    '/app/src/home-den-inference-model.mjs': 'export const identityOf = () => ({}); export const answerOf = () => null; export const houseOf = () => null;',
};
register('data:text/javascript,' + encodeURIComponent(`const STUBS = ${JSON.stringify(STUBS)};
export async function resolve(specifier, context, next) {
  const resolved = await next(specifier, context);
  if (!resolved.url.startsWith('file:')) return resolved;
  const pathname = new URL(resolved.url).pathname, stub = Object.keys(STUBS).find(suffix => pathname.endsWith(suffix));
  return stub ? { url: 'data:text/javascript,' + encodeURIComponent(STUBS[stub]), shortCircuit: true } : resolved;
}`));

const BODY = '/den/body-v2', FRAME_MS = 1000 / 60, P0 = 5000;
const BASE = measuredBodyEvent();   // the authored figure; each test frame changes only its seq and clock fields
const realPerformance = Object.getOwnPropertyDescriptor(globalThis, 'performance');
after(() => Object.defineProperty(globalThis, 'performance', realPerformance));
let cases = 0;

function element() {
    const children = new Map();
    return { dataset: {}, style: {}, title: '', textContent: '', className: '', innerHTML: '', parentElement: null, isConnected: true,
        querySelector(selector) { if (!children.has(selector)) children.set(selector, element()); return children.get(selector); },
        appendChild(child) { child.parentElement = this; return child; }, append() {}, insertBefore() {}, remove() {},
        setAttribute() {}, getAttribute: () => null, addEventListener() {}, closest: () => null };
}
function stubLayer() {
    const layer = { shown: null, applies: 0, clears: 0, root: {},
        updateMeasured(decoded) { layer.shown = decoded; layer.applies += 1; return true; },
        clear() { layer.shown = null; layer.clears += 1; },
        tick() {}, dispose() { layer.shown = null; }, snapshot: () => null,
        status: () => ({ points: layer.shown?.n ?? 0, webgpu: false, draw_after_apply_s: layer.applies ? 0.004 : null }) };
    return layer;
}

/* One live view on its own fake clock. Body connections answer with a body_clock after their setup time (the
 * producer samples it halfway through), in a producer clock that may be offset. `replay` sends a new connection the
 * producer's latest frame, as den-live does on connect. */
async function den({ setups = [], offsets = [], replay = false } = {}) {
    const clock = { t: 1000 }, sources = [], layers = [], expiry = new Map(), presentation = {};
    const producer = local => P0 + local / 1000;
    const tally = { drawn: 0, hidden: 0 };
    class FakeEventSource {
        constructor(url) {
            Object.assign(this, { url, readyState: 0, listeners: {}, openedAt: clock.t, clocked: false });
            if (url === BODY) Object.assign(this, { setup: setups.shift() ?? 120, offset: offsets.shift() ?? 0 });
            sources.push(this);
        }
        addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
        close() { this.readyState = 2; }
        emit(type, data) {
            if (this.readyState === 2) return false;   // a closed EventSource dispatches nothing more
            this.readyState = 1;
            for (const listener of this.listeners[type] || []) listener({ data: typeof data === 'string' ? data : JSON.stringify(data) });
            return true;
        }
    }
    const view = element(), canvas = { isConnected: true, parentElement: { dataset: {} }, getClientRects: () => [{}],
        getBoundingClientRect: () => ({ width: 800, height: 600 }), closest: () => view };
    const engine = { siteId: 'den', apartmentRoot: {}, contentRevision: BASE.content_revision, sceneIdentity: {}, rendererBackend: 'webgl',
        renderer: { domElement: canvas }, picking: { getHost: () => element() },
        createUiOverlayLayer: async ({ onPresentationFrame }) => { presentation.frame = onPresentationFrame; return { dispose() {} }; } };
    globalThis.__denBodyHarness = { attachLayer: () => { const layer = stubLayer(); layers.push(layer); return layer; } };
    globalThis.window = { HomeDenLive: { stream: '/den/people', site: 'den', home: BASE.home, body: BODY }, EventSource: FakeEventSource,
        location: { search: '' }, __APT_ENGINE: engine, getComputedStyle: () => ({ visibility: 'visible' }),
        matchMedia: () => ({ matches: true }), addEventListener() {} };
    globalThis.document = { hidden: false, addEventListener() {}, createElement: element, querySelector: () => null };
    globalThis.EventSource = FakeEventSource;
    Object.defineProperty(globalThis, 'performance', { value: { now: () => clock.t }, configurable: true, writable: true });
    const ticks = [], setInterval = globalThis.setInterval;
    globalThis.setInterval = fn => ticks.push(fn);
    try { await import(`${VIEW}?case=${++cases}`); } finally { globalThis.setInterval = setInterval; }
    const live = window.HomeDenLiveView, sync = ticks[0];
    const bodies = () => sources.filter(source => source.url === BODY);
    for (let i = 0; i < 200 && !bodies().length; i++) { await new Promise(resolve => setImmediate(resolve)); sync(); }
    assert.equal(bodies().length, 1, 'the body connection opens');

    let nextSync = clock.t + 500, last = null, seq = 0;
    const layer = () => layers.at(-1);
    function frame() {
        presentation.frame({ deltaSeconds: FRAME_MS / 1000 });
        const shown = layer().shown;
        if (!shown) { tally.hidden += 1; return; }
        const [offset, expires] = expiry.get(shown.seq);
        assert.ok(producer(clock.t) + offset < expires, `frame ${shown.seq} drawn past its producer expiry`);
        tally.drawn += 1;
    }
    function step(ms, frameMs = FRAME_MS) {
        const end = clock.t + ms;
        while (clock.t < end - 1e-6) {
            clock.t = Math.min(end, clock.t + frameMs);
            for (const source of bodies()) {
                if (source.clocked || source.readyState === 2 || clock.t < source.openedAt + source.setup) continue;
                source.clocked = true;
                source.emit('body_clock', { server_ts: producer(source.openedAt + source.setup / 2) + source.offset });
                if (replay && last && producer(clock.t) + source.offset < last.expires_ts) source.emit('body', last);
            }
            if (clock.t >= nextSync) { nextSync += 500; sync(); }
            frame();
        }
    }
    /* A ready frame with `left` seconds of producer lease at this moment (2 s from its capture), in the producer clock
     * `offset` from the first one's. */
    function ready({ left = 1.5, offset = 0, ...fields } = {}) {
        const capture = producer(clock.t) + offset + left - 2;
        const packet = { ...BASE, seq: ++seq, capture_ts: capture, expires_ts: capture + 2, ...fields };
        expiry.set(packet.seq, [offset, packet.expires_ts]);
        return packet;
    }
    const clear = reason => ({ ...measuredBodyEvent({ reason }), seq: ++seq });
    // den-live sends each frame to every connection that has its clock; or name one (a backlogged socket).
    function send(packet, to = bodies().filter(source => source.clocked && source.readyState !== 2)) {
        last = packet;
        for (const source of [to].flat()) source.emit('body', packet);
    }
    return { clock, tally, step, ready, clear, send, bodies, layer, status: () => live.status().body };
}

test('a backlogged body connection is dropped once, and the first fresh frame on the new one draws', async () => {
    const d = await den();
    d.step(1000);
    const [first] = d.bodies();
    assert.ok(first.clocked);
    for (let i = 0; i < 10; i++) { d.send(d.ready({ left: -0.5 - i }), first); d.step(400); }   // 2.5 s to 11.5 s old
    assert.equal(d.layer().applies, 0, 'nothing past its lease is drawn');
    assert.equal(first.readyState, 2, 'the backlogged connection is closed');
    assert.equal(d.bodies().length, 2, 'exactly one reopen');
    const second = d.bodies()[1];
    assert.ok(second.clocked && second.readyState !== 2, 'the new connection has its own anchor');
    assert.deepEqual(d.status().reopens, { error: 0, backlog: 1, reanchor: 0 });
    assert.equal(d.status().rejected.past_lease, 3, 'three in a row; the rest went with the old socket');
    const fresh = d.ready({ left: 1.6 });
    d.send(fresh, second);
    d.step(FRAME_MS);
    assert.equal(d.layer().shown?.seq, fresh.seq, 'the fresh frame draws');
    assert.equal(d.status().accepted, 1);
    assert.equal(d.bodies().length, 2);
});

test('two late frames with none accepted for longer than the lease also reopen; one late frame does not', async () => {
    const d = await den();
    d.step(1000);
    const [first] = d.bodies();
    d.send(d.ready({ left: -1 }), first);
    d.step(1500);
    assert.equal(d.bodies().length, 1, 'one late frame after a gap is not a backlog');
    d.send(d.ready({ left: -1 }), first);
    d.step(FRAME_MS);
    assert.equal(first.readyState, 2);
    assert.equal(d.bodies().length, 2);
    assert.equal(d.status().reopens.backlog, 1);
    assert.equal(d.layer().applies, 0);
});

test('after 55 minutes, re-anchored without a blank, a frame with 1.0 s of producer lease left draws for nearly all of it', async () => {
    const d = await den();
    d.step(55 * 60 * 1000, 250);
    const frame = d.ready({ left: 1.0 });
    d.send(frame);
    let drawnMs = 0;
    for (let i = 0; i < 66; i++) { d.step(FRAME_MS); if (d.layer().shown?.seq === frame.seq) drawnMs += FRAME_MS; }
    // A 55-minute-old anchor charges 0.33 s of rate margin on top of its setup, and drew this for about 0.6 s.
    assert.ok(drawnMs >= 850, `drawn ${drawnMs.toFixed(0)} ms of its 1000 ms`);
    assert.equal(d.layer().shown, null, 'and hidden by its expiry');
    const s = d.status();
    assert.ok(s.reopens.reanchor >= 10 && s.reanchored === s.reopens.reanchor, JSON.stringify(s.reopens));
    assert.equal(d.layer().clears, 1, 'the only clear is this frame expiring: re-anchoring never cleared');
    assert.equal(s.expired, 1);
    assert.ok(s.anchor_age_s < 5 * 60 + 2, `anchor ${s.anchor_age_s} s old`);
    assert.equal(d.bodies().filter(source => source.readyState !== 2).length, 1, 'each older connection is closed');
});

test('re-anchoring under a moving figure never blanks it, and a slower anchor is not taken', async () => {
    const d = await den({ setups: [120, 120, 3000, 120], replay: true });   // the third connection's setup is slow
    d.step(1000);
    let hidden = 0, latest = null;
    const end = d.clock.t + 11.5 * 60 * 1000;
    while (d.clock.t < end) {   // a fresh frame every 0.8 s, each with 1.5 s of lease left
        d.send(latest = d.ready({ left: 1.5 }));
        for (let i = 0; i < 48; i++) { d.step(FRAME_MS); if (!d.layer().shown) hidden += 1; }
    }
    assert.equal(hidden, 0, 'no presentation frame was without the figure');
    assert.equal(d.layer().clears, 0, 'nothing was cleared');
    const s = d.status();
    assert.deepEqual(s.reopens, { error: 0, backlog: 0, reanchor: 3 });
    assert.equal(s.reanchored, 2, 'the slow anchor was dropped, and the next one taken');
    assert.deepEqual(d.bodies().map(source => source.readyState), [2, 2, 2, 1]);
    assert.equal(d.layer().shown.seq, latest.seq, 'frames keep coming on the newest connection');
    assert.equal(s.rejected.watermark, 2, "each new connection's repeat of the latest frame is ignored");
    assert.equal(s.accepted, s.applied, 'every accepted frame was drawn');
});

test('no frame is drawn past its producer expiry, even when a re-anchor brings another producer clock', async () => {
    // The second connection reaches a producer whose clock reads 3 s behind the first's. Dated by that anchor, the
    // first connection's last frame would live 3 s too long.
    const d = await den({ offsets: [0, -3] });
    let random = 7;
    const next = () => (random = (random * 48271) % 2147483647) / 2147483647;
    d.step(1000);
    while (d.clock.t < 300500) { d.send(d.ready({ left: 0.05 + 1.9 * next() })); d.step(100 + 1400 * next()); }
    const old = d.ready({ left: 1.9 });
    d.send(old);
    d.step(4500);
    assert.equal(d.status().reanchored, 1);
    assert.equal(d.layer().shown, null, 'the first connection\'s last frame expired on its own anchor');
    const restarted = { offset: -3, run_id: 'restarted-run' };
    for (let i = 0; i < 20; i++) { d.send(d.ready({ ...restarted, left: 0.05 + 1.9 * next() })); d.step(100 + 1400 * next()); }
    for (let i = 0; i < 3; i++) { d.send(d.ready({ ...restarted, left: -2 })); d.step(300); }
    assert.equal(d.status().reopens.backlog, 1);
    assert.equal(d.layer().shown, null);
    d.step(500);
    for (let i = 0; i < 10; i++) { d.send(d.ready({ ...restarted, left: 0.05 + 1.9 * next() })); d.step(100 + 1400 * next()); }
    assert.ok(d.tally.drawn > 10000 && d.tally.hidden > 0, JSON.stringify(d.tally));
});

test('a connection that stays backlogged reopens on a doubling backoff, then at most once a minute; 5 quiet minutes reset it', async () => {
    const d = await den();
    d.step(1000);
    const until = d.clock.t + 15 * 60 * 1000;
    while (d.clock.t < until) { d.send(d.ready({ left: -3 })); d.step(800, 50); }
    const escapes = d.bodies().slice(1).map(source => source.openedAt);
    const gaps = escapes.slice(1).map((at, i) => at - escapes[i]);
    gaps.forEach((gap, i) => assert.ok(gap >= Math.min(60000, 2000 * 2 ** i), `escape ${i + 2} came ${gap} ms after the one before`));
    assert.ok(escapes.length >= 15 && escapes.length <= 21, `${escapes.length} escapes in 15 min`);
    assert.equal(d.status().reopens.backlog, escapes.length);
    assert.equal(d.layer().applies, 0, 'nothing late was drawn');
    for (let i = 0; i < 450; i++) { d.send(d.ready({ left: 1.5 })); d.step(800, 50); }   // 6 minutes of fresh frames
    assert.equal(d.status().reopens.backlog, escapes.length);
    const lateFrom = d.clock.t;
    for (let i = 0; i < 4; i++) { d.send(d.ready({ left: -3 })); d.step(800, 50); }
    const escape = d.bodies().at(-1).openedAt;
    assert.equal(d.status().reopens.backlog, escapes.length + 1);
    assert.ok(escape - lateFrom <= 3 * 800, 'with the backoff reset, the third late frame escapes at once');
});

test('the body counters reconcile: every event is accepted, rejected or a clear, and every accepted frame is accounted for', async () => {
    const d = await den({ setups: [400] });
    const [first] = d.bodies();
    d.send(d.ready(), first);                                          // before its body_clock
    d.step(500);
    d.send('not json'); d.send({ schema: 'den-live-body-0' });         // two that don't decode
    d.send(d.ready({ home: 'elsewhere' }));
    const repeated = d.ready();
    d.send(repeated); d.step(FRAME_MS);                                // accepted, applied
    d.send(d.ready({ run_id: 'another-run' }));                        // another run on this connection
    d.send(repeated);                                                  // a repeat: the watermark
    d.send(d.ready()); d.send(d.ready()); d.step(FRAME_MS);            // the first is replaced before it is applied
    d.send(d.ready({ content_revision: 'another-scan' }));             // another scan: a track change and a scope reject
    d.send(d.ready({ left: 4 }));                                      // dated ahead of the producer
    d.send(d.ready());                                                 // accepted, then cleared before it is applied
    d.send(d.clear('ambiguous'));
    d.send(d.ready({ track_id: 'another-track' })); d.step(FRAME_MS);
    d.send(d.ready()); d.step(FRAME_MS);                               // back to the authored track: a track change
    d.send(d.ready({ left: -1 }));                                     // past its lease
    const acceptedAt = d.clock.t;
    d.send(d.ready({ left: 1 })); d.step(FRAME_MS);
    d.step(1100);                                                      // ... and expired on a later presentation frame
    const s = d.status();
    assert.deepEqual(s.rejected, { decode: 2, home: 1, pre_clock: 1, run_mismatch: 1, watermark: 1, scope: 1, before_capture: 1, past_lease: 1 });
    assert.deepEqual(s.clears, { ambiguous: 1 });
    assert.deepEqual({ received: s.received, accepted: s.accepted, applied: s.applied, unapplied: s.unapplied, pending: s.pending,
        expired: s.expired, track_changes: s.track_changes, frames: s.frames },
        { received: 17, accepted: 7, applied: 5, unapplied: 2, pending: 0, expired: 1, track_changes: 2, frames: 7 });
    const sum = counts => Object.values(counts).reduce((total, n) => total + n, 0);
    assert.equal(s.received, s.accepted + sum(s.rejected) + sum(s.clears));
    assert.equal(s.accepted, s.applied + s.unapplied + s.pending);
    assert.deepEqual(s.reopens, { error: 0, backlog: 0, reanchor: 0 });
    assert.equal(s.reanchored, 0);
    assert.equal(s.last_accept_ms, acceptedAt);
    assert.equal(s.draw_after_apply_ms, 4, 'from the layer');
    assert.ok(Math.abs(s.anchor_age_s - (d.clock.t - first.openedAt) / 1000) < 1e-9);
    s.rejected.decode = 99; s.clears.ambiguous = 99;
    assert.equal(d.status().rejected.decode, 2, 'status is a copy');
    assert.equal(d.status().clears.ambiguous, 1);
    first.emit('error', '');
    assert.equal(d.status().stream, 'none');
    d.step(2600);
    assert.deepEqual(d.status().reopens, { error: 1, backlog: 0, reanchor: 0 }, 'an error reopens after 2 s');
});
