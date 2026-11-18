import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { createDenLatencyTrace, DEN_LATENCY_LIMITS, producerCaptureAgeTiming } from '../app/src/home-den-latency.mjs';
import { createLightingRenderEvidence } from '../app/src/home-3d/lighting-render-evidence.mjs';

const settle = () => new Promise(resolve => setImmediate(resolve));

test('request setup uncertainty includes either asymmetric transit and grows with the setup window', () => {
    const connection = { started: 100, clockReceivedAt: 500, serverTs: 10 };
    const timing = producerCaptureAgeTiming(connection, 10.4, 1000);
    assert.ok(Math.abs(timing.producer_age_at_receive_midpoint_ms - 300) < 0.001);
    assert.ok(Math.abs(timing.producer_clock_uncertainty_ms - 205.09) < 0.001);
    assert.equal(timing.producer_clock_round_trip_bound_ms, 400);
    for (const sampledAt of [100, 150, 450, 500]) {
        const age = (connection.serverTs + (1000 - sampledAt) / 1000 - 10.4) * 1000;
        assert.ok(age >= timing.producer_age_at_receive_interval_ms[0] && age <= timing.producer_age_at_receive_interval_ms[1]);
    }
    const queued = producerCaptureAgeTiming({ ...connection, clockReceivedAt: 900 }, 10.4, 1000);
    assert.ok(queued.producer_clock_uncertainty_ms > timing.producer_clock_uncertainty_ms);
    assert.equal(queued.producer_age_at_receive_ms, timing.producer_age_at_receive_ms, 'setup diagnostics preserve the original upper age');
    for (const invalid of [null, undefined, NaN, Infinity, 99, 1001]) {
        assert.deepEqual(producerCaptureAgeTiming({ ...connection, clockReceivedAt: invalid }, 10.4, 1000), {});
    }
});

test('the actual authority clock and all expiry checks retain their conservative upper bound', () => {
    const source = readFileSync(new URL('../app/src/home-den-live-view.mjs', import.meta.url), 'utf8');
    const clockSource = source.slice(source.indexOf('function producerNow('), source.indexOf('function openBody('));
    const authorityClock = runInNewContext('(' + clockSource + ')');
    for (const clockReceivedAt of [200, 500, 900]) {
        const connection = { started: 100, clockReceivedAt, serverTs: 10 };
        const capture = 9.9, now = 1000, expiry = capture + 1;
        const timing = producerCaptureAgeTiming(connection, capture, now);
        assert.equal(timing.producer_age_at_receive_ms, (authorityClock(connection, now) - capture) * 1000);
        assert.ok(authorityClock(connection, now) >= expiry, 'the upper clock expires the frame');
        assert.ok(capture + timing.producer_age_at_receive_midpoint_ms / 1000 < expiry, 'using the midpoint for authority would extend this lease');
    }
    assert.match(source, /const producerTime = producerNow\(connection, now\);\s*if \(producerTime < event.capture_ts \|\| producerTime >= event.expires_ts\)/);
    // Each frame's expiry uses the anchor of the connection that delivered it.
    assert.match(source, /const now = producerNow\(owned.ready.connection, performance.now\(\)\);\s*if \(now >= owned.ready.event.expires_ts\)/);
    assert.match(source, /producerNow\(ready.connection, performance.now\(\)\) < ready.event.expires_ts/);
    assert.match(source, /owned.ready = \{ event, decoded, trace, connection \};/);
    const clockCallback = source.slice(source.indexOf("source.addEventListener('body_clock'"), source.indexOf("source.addEventListener('body',"));
    assert.ok(clockCallback.indexOf('const receivedAt = performance.now();') < clockCallback.indexOf('if (!current()'));
});

test('diagnostic clock intervals remain copied numeric metadata including uncertain negative lower bounds', () => {
    const browser = createDenLatencyTrace({ now: () => 1000 });
    const timing = producerCaptureAgeTiming({ started: 100, clockReceivedAt: 500, serverTs: 10 }, 10.7, 1000);
    assert.ok(timing.producer_age_at_receive_interval_ms[0] < 0);
    browser.receive('body', { run_id: 'clock-run', seq: 1, capture_ts: 10.7 }, 1000, timing);
    const snapshot = browser.snapshot(), originalLower = snapshot.entries[0].producer_age_at_receive_interval_ms[0];
    timing.producer_age_at_receive_interval_ms[0] = -999;
    snapshot.entries[0].producer_age_at_receive_interval_ms[0] = -888;
    const row = JSON.parse(browser.exportJson()).entries[0];
    assert.equal(row.producer_age_at_receive_interval_ms[0], originalLower);
    assert.equal(row.producer_clock_mapping, 'request_receive_interval');
    assert.equal(row.capture_timing, 'producer_capture_estimate');
});
function fixture() {
    let at = 10, current = true, finish;
    const target = { isPoints: true, onAfterRender() { target.calls++; }, calls: 0 };
    const original = target.onAfterRender, root = { traverse: fn => fn(target) };
    const trace = createDenLatencyTrace({ now: () => at, wallNow: () => 100000 + at, timeOrigin: 100000 });
    const renderer = { backend: { isWebGPUBackend: true, device: { queue: {
        onSubmittedWorkDone: () => new Promise(resolve => { finish = resolve; }),
    } } } };
    const evidence = createLightingRenderEvidence({ renderer, nowMs: () => 100000 + at, monotonicMs: () => at });
    const packet = { run_id: 'fixture-run', seq: 1, capture_ts: 99, expires_ts: 101,
        points: { position_mm: 'private-depth' }, object: { label: 'private-caption' }, track_id: 'private-person' };
    const token = trace.receive('body', packet, at, { producer_age_at_receive_ms: 900 });
    const draw = (value = token) => trace.draw(value, { root, isCurrent: () => current, isDrawing: () => true,
        request: context => evidence.request(context) });
    return { trace, token, packet, root, target, original, evidence, draw, setCurrent: value => { current = value; },
        advance: value => { at += value; }, finish: () => finish(), dispose: () => evidence.dispose() };
}

test('exact body stages require the target draw, ordinary submission and GPU completion', async () => {
    const f = fixture();
    f.trace.mark(f.token, 'decoded'); f.advance(2); f.trace.mark(f.token, 'scheduled'); f.advance(3);
    f.trace.mark(f.token, 'applied'); f.draw();
    f.evidence.afterRender(); await settle();
    assert.equal(f.trace.snapshot().entries.some(row => row.phase === 'submitted'), false, 'a different engine target cannot witness this packet');
    f.target.onAfterRender(); f.advance(4); f.evidence.afterRender(); await settle();
    assert.equal(f.target.calls, 1, 'the original draw observer is preserved');
    assert.equal(f.trace.snapshot().entries.at(-1).phase, 'submitted');
    f.advance(7); f.finish(); await settle();
    const result = f.trace.snapshot(), completed = result.entries.at(-1);
    assert.deepEqual(result.entries.map(row => row.phase), ['received', 'decoded', 'scheduled', 'applied', 'submitted', 'gpu_completed']);
    assert.ok(result.entries.every(row => row.kind === 'body' && row.run_id === 'fixture-run' && row.seq === 1));
    assert.equal(completed.frame_id, 2); assert.equal(completed.frame_submitted_at_ms, 19); assert.equal(completed.gpu_completed_at_ms, 26);
    assert.equal(completed.producer_age_at_receive_ms, 900); assert.equal(completed.capture_timing, 'producer_capture_estimate');
    assert.equal(completed.backend, 'webgpu'); assert.equal(result.presentation_observed, false); assert.equal(result.physical_commands, false);
    assert.equal(result.pending, 0); assert.equal(f.target.onAfterRender, f.original);
    assert.doesNotMatch(f.trace.exportJson(), /private-depth|private-caption|private-person|position_mm|track_id/);
    f.dispose();
});

test('superseded, expired, hidden or cleared packets cannot later produce success and observers are removed', async () => {
    for (const invalidate of [
        f => f.trace.receive('body', { ...f.packet, seq: 2 }),
        f => f.trace.invalidate('body', 'stale'),
        f => f.setCurrent(false),
        f => f.trace.clear(),
    ]) {
        const f = fixture(); f.draw(); f.target.onAfterRender(); f.evidence.afterRender();
        invalidate(f); f.advance(50); f.finish(); await settle();
        assert.equal(f.trace.snapshot().entries.some(row => row.phase === 'gpu_completed'), false);
        assert.equal(f.trace.snapshot().pending, 0); assert.equal(f.target.onAfterRender, f.original);
        f.dispose();
    }
});

test('spot epochs and body runs stay independent, replay is ignored, and exported snapshots cannot alter the buffer', () => {
    const f = fixture();
    const spot = f.trace.receive('spot', { epoch: 'fixture-epoch', seq: 1, sampled_mono: 44,
        fixtures: { one: { on: true, target_m: ['private-geometry'] } } });
    f.trace.mark(spot, 'decoded'); f.trace.mark(spot, 'scheduled');
    assert.equal(f.trace.receive('spot', { epoch: 'fixture-epoch', seq: 1 }), null);
    assert.equal(f.trace.receive('body', f.packet), null);
    const snapshot = f.trace.snapshot(); snapshot.entries[0].run_id = 'mutated'; snapshot.entries.length = 0;
    assert.equal(f.trace.snapshot().entries[0].run_id, 'fixture-run');
    const rows = JSON.parse(f.trace.exportJson()).entries.filter(row => row.kind === 'spot');
    assert.ok(rows.every(row => row.epoch === 'fixture-epoch' && row.seq === 1 && row.lit === true));
    assert.doesNotMatch(f.trace.exportJson(), /private-geometry/);
    f.dispose();
});

test('buffers and live diagnostic requests remain bounded and clearing aborts their work', async () => {
    const f = fixture();
    for (let seq = 2; seq < 800; seq++) f.trace.receive('body', { ...f.packet, seq });
    assert.equal(f.trace.snapshot().entries.length, DEN_LATENCY_LIMITS.entries);
    assert.ok(f.trace.snapshot().dropped_entries > 0);
    let requests = 0;
    for (let seq = 800; seq < 810; seq++) {
        const token = f.trace.receive('body', { ...f.packet, seq });
        f.trace.draw(token, { root: f.root, isCurrent: () => true, isDrawing: () => true, request: () => { requests++; return new Promise(() => {}); } });
    }
    assert.equal(requests, DEN_LATENCY_LIMITS.pending);
    assert.equal(f.trace.snapshot().pending, DEN_LATENCY_LIMITS.pending);
    assert.equal(f.trace.snapshot().entries.at(-1).reason, 'diagnostic_limit');
    f.trace.clear(); assert.equal(f.trace.snapshot().entries.length, 0); f.dispose();
});

test('missing target or receipt stays unavailable and an old failed request cannot invalidate a newer scope', async () => {
    const f = fixture();
    f.trace.draw(f.token, { root: { traverse() {} }, request() {}, isCurrent: () => true });
    assert.equal(f.trace.snapshot().entries.at(-1).reason, 'draw_witness_unavailable');
    const first = f.trace.receive('spot', { epoch: 'first', seq: 1 });
    let reject;
    f.trace.draw(first, { root: f.root, isCurrent: () => true, request: () => new Promise((_, fail) => { reject = fail; }) });
    const second = f.trace.receive('spot', { epoch: 'second', seq: 1 });
    reject(Error('private message')); await settle(); f.trace.mark(second, 'scheduled');
    assert.equal(f.trace.snapshot().entries.at(-1).phase, 'scheduled');
    assert.doesNotMatch(f.trace.exportJson(), /private message/); f.dispose();
});
