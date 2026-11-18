import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, unlinkSync, rmdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { reportTrace, matchCues, main } from './den-latency-report.mjs';
import { createDenLatencyTrace, producerCaptureAgeTiming } from '../app/src/home-den-latency.mjs';

const entry = (phase, at_ms, fields = {}) => ({ kind: 'body', run_id: 'run-1', seq: 1, phase, at_ms, ...fields });
const trace = entries => ({ schema: 'home-den-latency-1', entries, dropped_entries: 0, pending: 0 });

test('local durations join the exact stream identity and GPU frame', () => {
    const report = reportTrace(trace([
        entry('received', 100), entry('decoded', 103), entry('scheduled', 104), entry('applied', 106),
        entry('submitted', 112, { frame_id: 8 }), entry('gpu_completed', 118, { frame_id: 8 }),
        entry('received', 1, { run_id: 'another-run' }),
        entry('submitted', 500, { run_id: 'another-run', frame_id: 9 }),
        entry('gpu_completed', 505, { run_id: 'another-run', frame_id: 10 }),
        entry('received', 200, { kind: 'spot', epoch: 'spot-1' }),
        entry('submitted', 220, { kind: 'spot', epoch: 'spot-1', frame_id: 11 }),
    ]));
    assert.deepEqual(report.kinds.body.stages_ms.receive_to_decode, { n: 1, p50: 3, p95: 3, max: 3 });
    assert.equal(report.kinds.body.stages_ms.receive_to_submit.p95, 499);
    assert.equal(report.kinds.body.stages_ms.receive_to_gpu_complete.n, 1);
    assert.equal(report.kinds.body.incomplete.receive_to_gpu_complete.frame_mismatch, 1);
    assert.equal(report.kinds.spot.stages_ms.receive_to_submit.p95, 20);
    assert.equal(report.capture_to_paint, null);
    assert.equal(report.visible_paint_observed, false);
});

test('missing, out-of-order and repeated phases remain visible in counts', () => {
    const report = reportTrace(trace([
        entry('received', 10), entry('received', 12), entry('decoded', 9), entry('scheduled', 14),
        entry('submitted', 4, { seq: 2 }), entry('received', 15, { seq: 3 }),
        entry('decoded', 16, { seq: 3 }), entry('unavailable', 17, { seq: 3, reason: 'stale' }),
        entry('invalid', 0), entry('received', -1),
    ]));
    assert.equal(report.duplicate_phases, 1);
    assert.equal(report.rejected_entries, 2);
    assert.equal(report.kinds.body.incomplete.receive_to_decode.invalid_order, 1);
    assert.equal(report.kinds.body.incomplete.receive_to_submit.missing, 2);
    assert.equal(report.kinds.body.incomplete.receive_to_decode.excluded_unavailable, 1);
    assert.equal(report.kinds.body.stages_ms.receive_to_decode.n, 0, 'a decoded clear or stale frame cannot enter the latency distribution');
    assert.equal(report.unavailable.stale, 1);
    assert.throws(() => reportTrace(trace(Array(601).fill(entry('received', 1)))));
});

test('GPU receipt timestamps exclude delayed receipt callback scheduling', () => {
    const report = reportTrace(trace([
        entry('received', 10), entry('submitted', 20, { frame_id: 2, frame_submitted_at_ms: 18 }),
        entry('gpu_completed', 50, { frame_id: 2, gpu_completed_at_ms: 25 }),
        entry('unavailable', 51, { seq: 2, reason: 'private_reason_words' }),
    ]));
    assert.equal(report.kinds.body.stages_ms.submit_to_gpu_complete.p95, 7);
    assert.equal(report.kinds.body.stages_ms.receive_to_gpu_complete.p95, 15);
    assert.doesNotMatch(JSON.stringify(report), /private_reason_words/);
});

test('the browser trace export feeds the reporter without translation', async () => {
    let at = 10;
    const browser = createDenLatencyTrace({ now: () => at, wallNow: () => 100000 + at, timeOrigin: 100000 });
    const token = browser.receive('spot', { epoch: 'real-export', seq: 2, sampled_mono: 10, fixtures: {} });
    at = 12;
    browser.mark(token, 'decoded');
    browser.mark(token, 'scheduled');
    at = 15;
    browser.mark(token, 'applied');
    const target = { isPoints: true };
    let normalFrame = false, readyBeforeFrame = null, readyAfterFrame = null;
    browser.draw(token, { root: { traverse: callback => callback(target) }, isCurrent: () => true, isDrawing: () => normalFrame,
        request: ({ onSubmitted, isReady }) => {
            target.onAfterRender();
            readyBeforeFrame = isReady();
            normalFrame = true;
            target.onAfterRender();
            normalFrame = false;
            readyAfterFrame = isReady();
            at = 20;
            onSubmitted({ frame_id: 3, frame_submitted_mono_ms: 19, backend: 'webgl2' });
            return { ok: true, stage: 'gpu_completed_frame', frame_id: 3, frame_submitted_mono_ms: 19,
                gpu_completed_mono_ms: 22, backend: 'webgl2' };
        } });
    await Promise.resolve();
    const report = reportTrace(JSON.parse(browser.exportJson()));
    assert.equal(readyBeforeFrame, false);
    assert.equal(readyAfterFrame, true);
    assert.equal(report.kinds.spot.stages_ms.receive_to_submit.p95, 9);
    assert.equal(report.kinds.spot.stages_ms.receive_to_gpu_complete.p95, 12);
});

test('capture estimates require the exact completed body and retain their unqualified label', () => {
    const received = entry('received', 100, { capture_timing: 'producer_capture_estimate', producer_age_at_receive_ms: 800 });
    const report = reportTrace(trace([
        received, entry('submitted', 110, { frame_id: 7 }), entry('gpu_completed', 120, { frame_id: 7 }),
        { ...received, seq: 2 }, entry('submitted', 110, { seq: 2, frame_id: 8 }),
        entry('gpu_completed', 120, { seq: 2, frame_id: 8 }), entry('unavailable', 119, { seq: 2, reason: 'superseded' }),
        { ...received, seq: 3 }, entry('submitted', 110, { seq: 3, frame_id: 9 }),
        entry('gpu_completed', 120, { seq: 3, frame_id: 10 }),
    ]));
    const estimate = report.kinds.body.capture_estimate;
    assert.equal(estimate.qualified, false);
    assert.equal(estimate.estimated_age_at_receive_ms.n, 1);
    assert.equal(estimate.estimated_age_at_gpu_complete_ms.p95, 820);
    assert.equal(estimate.clock_mapping.samples, 0);
    assert.equal(estimate.clock_mapping.missing_samples, 1, 'older exports retain their age results without invented clock intervals');
    assert.equal(report.capture_to_paint, null);
});

test('clock mapping reports setup uncertainty, raw and causal intervals independently of capture qualification', () => {
    const timing = producerCaptureAgeTiming({ started: 100, clockReceivedAt: 500, serverTs: 10 }, 10.7, 1000);
    const received = entry('received', 1000, { capture_timing: 'producer_capture_estimate',
        producer_clock_mapping: 'request_receive_interval', ...timing });
    const invalid = [
        { producer_age_at_receive_interval_ms: [3, 2] },
        { producer_age_at_receive_interval_ms: [-10, -2] },
        { producer_age_at_receive_interval_ms: [0, Infinity] },
        { producer_clock_uncertainty_ms: 1 },
    ];
    const rows = [received, entry('submitted', 1005, { frame_id: 7 }), entry('gpu_completed', 1020, { frame_id: 7 })];
    for (const [index, fields] of invalid.entries()) {
        const seq = index + 2;
        rows.push({ ...received, ...fields, seq }, entry('submitted', 1005, { seq, frame_id: 7 }), entry('gpu_completed', 1020, { seq, frame_id: 7 }));
    }
    const estimate = reportTrace(trace(rows)).kinds.body.capture_estimate, mapping = estimate.clock_mapping;
    assert.equal(estimate.qualified, false); assert.equal(mapping.samples, 1); assert.equal(mapping.invalid_samples, 4);
    assert.equal(mapping.causality_bounded_samples, 1);
    assert.equal(mapping.setup_round_trip_bound_ms.p95, 400);
    assert.equal(mapping.midpoint_age_at_receive_ms.p95, 0);
    assert.equal(mapping.midpoint_age_at_gpu_complete_ms.p95, 20);
    assert.equal(mapping.raw_age_at_receive_ms.lower.p95, -205.09);
    assert.equal(mapping.causal_age_at_receive_ms.lower.p95, 0);
    assert.equal(mapping.raw_age_at_gpu_complete_ms.lower.p95, -185.092);
    assert.equal(mapping.raw_age_at_gpu_complete_ms.upper.p95, 225.092);
    assert.equal(mapping.uncertainty_at_gpu_complete_ms.p95, 205.092);
    assert.equal(estimate.estimated_age_at_receive_ms.p95, 205.09, 'the legacy upper-bound estimate remains available');
});

test('legacy cue matching requires epoch and sequence even when lit state matches', () => {
    const cues = [{ cue: 'private words', pc_unix: 100 }];
    const spots = [{ epoch: 'correct', seq: 4, lit: true, arrival_unix: 101 }];
    const wrong = [{ epoch: 'wrong', seq: 4, lit: true, paint_unix_ms: 101100 },
        { epoch: 'correct', seq: 5, lit: true, paint_unix_ms: 101150 }];
    assert.equal(matchCues(cues, spots, wrong, 5)[0].exact_snapshot_stamp_match, false);
    const matching = [...wrong, { epoch: 'correct', seq: 4, lit: true, paint_unix_ms: 101200 }];
    const unaligned = matchCues(cues, spots, matching)[0];
    assert.equal(unaligned.exact_snapshot_stamp_match, true);
    assert.equal(unaligned.cue_to_raf_ms, null);
    const aligned = matchCues(cues, spots, matching, 5)[0];
    assert.deepEqual(aligned.subscriber_to_raf_ms, { min: 195, max: 205 });
    assert.doesNotMatch(JSON.stringify(aligned), /private words/);
});

test('both device reports stay numeric and tolerate exported BOM JSON', () => {
    const folder = mkdtempSync(join(tmpdir(), 'den-latency-report-'));
    const file = join(folder, 'trace.json');
    try {
        writeFileSync(file, '\uFEFF' + JSON.stringify({ ...trace([entry('received', 20), entry('submitted', 22, { frame_id: 1 })]),
            private_media: 'never copied', entries: [entry('received', 20), entry('submitted', 22, { frame_id: 1, xyz: [1, 2, 3] })] }));
        const report = main(['--device', 'desktop', '--trace', file, '--device', 'phone', '--trace', file]);
        assert.deepEqual(report.devices.map(row => row.device), ['desktop', 'phone']);
        assert.equal(report.devices[1].trace.kinds.body.stages_ms.receive_to_submit.p95, 2);
        assert.doesNotMatch(JSON.stringify(report), /never copied|xyz|private_media/);
        assert.throws(() => main(['--trace', file]));
        assert.throws(() => main(['--device', 'desktop', '--trace', file, '--device', 'desktop', '--trace', file]));
    } finally { unlinkSync(file); rmdirSync(folder); }
});
