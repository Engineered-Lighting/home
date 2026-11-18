#!/usr/bin/env node
/* Report local metadata exports. No network access or fixture writes.
 *
 * node tools/den-latency-report.mjs --device desktop --trace desktop.json
 *   --device phone --trace phone.json
 * Optional legacy cue instrument inputs: --cues cues.json --spots spots.jsonl
 * and --paint paint.json following each --device. Legacy stamps are rAF
 * callbacks. Wall-clock differences require a verified --clock-bound-ms N.
 */
import { readFileSync, statSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const PHASES = new Set(['received', 'decoded', 'scheduled', 'applied', 'submitted', 'gpu_completed', 'unavailable']);
const REASONS = new Set(['scope_changed', 'superseded', 'stale', 'unavailable', 'manual_hold', 'trace_cleared',
    'diagnostic_limit', 'draw_witness_unavailable', 'renderer_receipt_unavailable', 'renderer_frame_timeout',
    'renderer_scope_changed', 'renderer_gpu_completion_failed', 'renderer_gpu_fence_unavailable', 'renderer_disposed']);
const STAGES = [
    ['receive_to_decode', 'received', 'decoded'],
    ['decode_to_schedule', 'decoded', 'scheduled'],
    ['schedule_to_apply', 'scheduled', 'applied'],
    ['apply_to_submit', 'applied', 'submitted'],
    ['receive_to_submit', 'received', 'submitted'],
    ['submit_to_gpu_complete', 'submitted', 'gpu_completed'],
    ['receive_to_gpu_complete', 'received', 'gpu_completed'],
];
const finite = value => typeof value === 'number' && Number.isFinite(value);
const validId = value => typeof value === 'string' && value.length > 0 && value.length <= 128;
const sequence = value => Number.isSafeInteger(value) && value >= 0;

function stageTime(row) {
    if (row.phase === 'submitted' && finite(row.frame_submitted_at_ms) && row.frame_submitted_at_ms >= 0) return row.frame_submitted_at_ms;
    if (row.phase === 'gpu_completed' && finite(row.gpu_completed_at_ms) && row.gpu_completed_at_ms >= 0) return row.gpu_completed_at_ms;
    return row.at_ms;
}

function distribution(values) {
    values.sort((a, b) => a - b);
    const percentile = p => values.length ? Math.round(values[Math.ceil(p * values.length) - 1] * 1000) / 1000 : null;
    return { n: values.length, p50: percentile(0.5), p95: percentile(0.95), max: percentile(1) };
}

function clockMappingSummary(samples, attempted, completed) {
    const summarize = select => distribution(samples.map(select));
    return { basis: 'request_receive_interval', samples: samples.length,
        missing_samples: completed - attempted, invalid_samples: attempted - samples.length,
        causality_bounded_samples: samples.filter(row => row.lower < 0).length,
        setup_round_trip_bound_ms: summarize(row => row.roundTrip),
        uncertainty_at_receive_ms: summarize(row => row.uncertainty),
        uncertainty_at_gpu_complete_ms: summarize(row => row.uncertainty + row.elapsed * 0.0001),
        midpoint_age_at_receive_ms: summarize(row => row.midpoint),
        midpoint_age_at_gpu_complete_ms: summarize(row => row.midpoint + row.elapsed),
        raw_age_at_receive_ms: { lower: summarize(row => row.lower), upper: summarize(row => row.upper) },
        raw_age_at_gpu_complete_ms: {
            lower: summarize(row => row.lower + row.elapsed * 0.9999), upper: summarize(row => row.upper + row.elapsed * 1.0001) },
        causal_age_at_receive_ms: { lower: summarize(row => Math.max(0, row.lower)), upper: summarize(row => row.upper) },
        causal_age_at_gpu_complete_ms: {
            lower: summarize(row => Math.max(0, row.lower + row.elapsed * 0.9999)),
            upper: summarize(row => row.upper + row.elapsed * 1.0001) } };
}

export function reportTrace(trace) {
    if (trace?.schema !== 'home-den-latency-1' || !Array.isArray(trace.entries) || trace.entries.length > 600) {
        throw Error('Expected a bounded home-den-latency-1 trace');
    }
    const samples = new Map(), reasons = {};
    let rejected = 0, duplicates = 0;
    for (const row of trace.entries) {
        const identity = row?.kind === 'body' ? row.run_id : row?.epoch;
        if (!['body', 'spot'].includes(row?.kind) || !validId(identity) || !sequence(row.seq)
            || !PHASES.has(row.phase) || !finite(row.at_ms) || row.at_ms < 0) {
            rejected += 1;
            continue;
        }
        const key = JSON.stringify([row.kind, identity, row.seq]);
        if (!samples.has(key)) samples.set(key, { kind: row.kind, phases: new Map() });
        const sample = samples.get(key);
        const previous = sample.phases.get(row.phase);
        if (previous) duplicates += 1;
        if (!previous || row.at_ms < previous.at_ms) sample.phases.set(row.phase, row);
        if (row.phase === 'unavailable') {
            const reason = REASONS.has(row.reason) ? row.reason : 'unspecified';
            reasons[reason] = (reasons[reason] || 0) + 1;
        }
    }
    const kinds = {};
    for (const kind of ['body', 'spot']) {
        const selected = [...samples.values()].filter(sample => sample.kind === kind);
        const stages = {}, incomplete = {};
        const captureAges = [], completedAges = [];
        const clockMappings = [];
        let mappingAttempts = 0, completedBodies = 0;
        for (const [name, from, to] of STAGES) {
            const durations = [];
            let invalidOrder = 0, wrongFrame = 0, excludedUnavailable = 0;
            for (const sample of selected) {
                if (sample.phases.has('unavailable')) { excludedUnavailable += 1; continue; }
                const start = sample.phases.get(from), end = sample.phases.get(to);
                if (!start || !end) continue;
                if (to === 'gpu_completed') {
                    const submitted = sample.phases.get('submitted');
                    if (!submitted || submitted.frame_id !== end.frame_id || !sequence(end.frame_id)) {
                        wrongFrame += 1;
                        continue;
                    }
                }
                if (stageTime(end) < stageTime(start)) { invalidOrder += 1; continue; }
                durations.push(stageTime(end) - stageTime(start));
                if (kind === 'body' && name === 'receive_to_gpu_complete') {
                    completedBodies++;
                    const elapsed = stageTime(end) - stageTime(start);
                    if (start.capture_timing === 'producer_capture_estimate'
                        && finite(start.producer_age_at_receive_ms) && start.producer_age_at_receive_ms >= 0) {
                        captureAges.push(start.producer_age_at_receive_ms);
                        completedAges.push(start.producer_age_at_receive_ms + elapsed);
                    }
                    if (Object.hasOwn(start, 'producer_age_at_receive_interval_ms')) {
                        mappingAttempts++;
                        const bounds = start.producer_age_at_receive_interval_ms;
                        const midpoint = start.producer_age_at_receive_midpoint_ms;
                        const uncertainty = start.producer_clock_uncertainty_ms, roundTrip = start.producer_clock_round_trip_bound_ms;
                        if (start.capture_timing === 'producer_capture_estimate' && start.producer_clock_mapping === 'request_receive_interval'
                            && Array.isArray(bounds) && bounds.length === 2 && bounds.every(finite) && bounds[0] <= bounds[1] && bounds[1] >= 0
                            && [midpoint, uncertainty, roundTrip].every(finite) && uncertainty >= 0 && roundTrip >= 0
                            && Math.abs(midpoint - (bounds[0] / 2 + bounds[1] / 2)) <= 0.001
                            && Math.abs(uncertainty - (bounds[1] - bounds[0]) / 2) <= 0.001) {
                            clockMappings.push({ lower: bounds[0], upper: bounds[1], midpoint, uncertainty, roundTrip, elapsed });
                        }
                    }
                }
            }
            stages[name] = distribution(durations);
            incomplete[name] = { missing: selected.length - durations.length - invalidOrder - wrongFrame - excludedUnavailable,
                invalid_order: invalidOrder, frame_mismatch: wrongFrame, excluded_unavailable: excludedUnavailable };
        }
        kinds[kind] = { samples: selected.length, stages_ms: stages, incomplete };
        if (kind === 'body') kinds[kind].capture_estimate = { qualified: false, source: 'producer_capture_estimate',
            estimated_age_basis: 'conservative_clock_upper_bound',
            estimated_age_at_receive_ms: distribution(captureAges), estimated_age_at_gpu_complete_ms: distribution(completedAges),
            clock_mapping: clockMappingSummary(clockMappings, mappingAttempts, completedBodies) };
    }
    return { entries: trace.entries.length, rejected_entries: rejected, duplicate_phases: duplicates,
        dropped_entries: sequence(trace.dropped_entries) ? trace.dropped_entries : null,
        pending: sequence(trace.pending) ? trace.pending : null, unavailable: reasons, kinds,
        capture_to_paint: null, visible_paint_observed: false };
}

function validSpot(row) {
    return validId(row?.epoch) && sequence(row.seq) && typeof row.lit === 'boolean' && finite(row.arrival_unix);
}

function interval(value, bound) {
    if (bound === null) return null;
    return { min: Math.round((value - bound) * 1000) / 1000, max: Math.round((value + bound) * 1000) / 1000 };
}

export function matchCues(cues, spots, paints, clockBoundMs = null) {
    if (!Array.isArray(cues) || !Array.isArray(spots) || !Array.isArray(paints)
        || clockBoundMs !== null && (!finite(clockBoundMs) || clockBoundMs < 0)) throw Error('Invalid cue inputs');
    const snapshots = spots.filter(validSpot).sort((a, b) => a.arrival_unix - b.arrival_unix);
    const stamps = new Map();
    for (const paint of paints) {
        if (!validId(paint?.epoch) || !sequence(paint.seq) || typeof paint.lit !== 'boolean' || !finite(paint.paint_unix_ms)) continue;
        const key = JSON.stringify([paint.epoch, paint.seq]);
        const old = stamps.get(key);
        if (!old || paint.paint_unix_ms < old.paint_unix_ms) stamps.set(key, paint);
    }
    const marks = cues.filter(cue => finite(cue?.pc_unix)).sort((a, b) => a.pc_unix - b.pc_unix);
    return marks.map((cue, index) => {
        const end = marks[index + 1]?.pc_unix ?? Infinity;
        const before = snapshots.filter(row => row.arrival_unix <= cue.pc_unix);
        const wasLit = before.at(-1)?.lit ?? false;
        const change = snapshots.find(row => row.arrival_unix > cue.pc_unix && row.arrival_unix <= end && row.lit !== wasLit);
        const stamp = change && stamps.get(JSON.stringify([change.epoch, change.seq]));
        const matched = !!stamp && stamp.lit === change.lit;
        return { cue_index: index, was_lit: wasLit, to_lit: change?.lit ?? null,
            snapshot_after_cue_ms: change ? Math.round((change.arrival_unix - cue.pc_unix) * 1000) : null,
            exact_snapshot_stamp_match: matched,
            cue_to_raf_ms: matched ? interval(stamp.paint_unix_ms - cue.pc_unix * 1000, clockBoundMs) : null,
            subscriber_to_raf_ms: matched ? interval(stamp.paint_unix_ms - change.arrival_unix * 1000, clockBoundMs) : null,
            clock_bound_asserted_ms: clockBoundMs, visible_paint_observed: false };
    });
}

function read(path, jsonLines = false) {
    if (statSync(path).size > 32 * 1024 * 1024) throw Error('Input exceeds 32 MiB');
    const text = readFileSync(path, 'utf8').replace(/^\uFEFF/, '');
    return jsonLines ? text.split(/\r?\n/).filter(line => line.trim()).map(line => JSON.parse(line)) : JSON.parse(text);
}

export function main(args) {
    const devices = [];
    let current = null, spots = null, cues = null, clockBound = null;
    for (let index = 0; index < args.length; index += 2) {
        const option = args[index], value = args[index + 1];
        if (!value) throw Error('Each option requires a value');
        if (option === '--device') {
            if (!['desktop', 'phone'].includes(value) || devices.some(device => device.device === value)) throw Error('Use one desktop and one phone device');
            current = { device: value };
            devices.push(current);
        } else if (option === '--trace' || option === '--paint') {
            if (!current || current[option.slice(2)]) throw Error('Select a device before its trace or paint file');
            current[option.slice(2)] = value;
        } else if (option === '--spots') spots = value;
        else if (option === '--cues') cues = value;
        else if (option === '--clock-bound-ms' && finite(Number(value)) && Number(value) >= 0) clockBound = Number(value);
        else throw Error('Unknown option or invalid clock bound');
    }
    if (!devices.length || devices.some(device => !device.trace && !device.paint) || !!spots !== !!cues
        || devices.some(device => device.paint) && !spots) throw Error('Supply device traces, or paints with cues and spots');
    const spotRows = spots ? read(spots, true) : [], cueRows = cues ? read(cues) : [];
    if (!Array.isArray(cueRows) || spotRows.length > 100000 || cueRows.length > 10000) throw Error('Invalid or excessive cue inputs');
    return { schema: 'home-den-latency-report-1', physical_commands: false, presentation_observed: false,
        clock_basis: 'browser_monotonic_stage_durations',
        legacy_clock_basis: clockBound === null ? 'unaligned' : 'operator_verified_wall_clock_bound',
        devices: devices.map(device => ({ device: device.device,
            ...(device.trace ? { trace: reportTrace(read(device.trace)) } : {}),
            ...(device.paint ? { legacy_cues: matchCues(cueRows, spotRows, read(device.paint), clockBound) } : {}) })) };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    try { process.stdout.write(`${JSON.stringify(main(process.argv.slice(2)), null, 2)}\n`); }
    catch { process.stderr.write('Den latency report failed. Check input schemas, sizes and command options.\n'); process.exitCode = 2; }
}
