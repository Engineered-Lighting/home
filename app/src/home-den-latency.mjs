/* Local, opt-in Den timing metadata. No request, camera input, pixels, subject, coordinates, or fixture writes.
 * A target's onAfterRender witness joins its exact packet to the normal frame's GPU completion. The compositor
 * and display remain unobserved. All elapsed times use this page's performance clock. */
export const DEN_LATENCY_SCHEMA = 'home-den-latency-1';
export const DEN_LATENCY_LIMITS = Object.freeze({ entries: 600, pending: 4, draw_targets: 512 });
const PHASES = new Set(['decoded', 'scheduled', 'applied']);
const REASONS = new Set(['scope_changed', 'superseded', 'stale', 'unavailable', 'manual_hold', 'trace_cleared',
    'diagnostic_limit', 'draw_witness_unavailable', 'renderer_receipt_unavailable', 'renderer_frame_timeout',
    'renderer_scope_changed', 'renderer_gpu_completion_failed', 'renderer_gpu_fence_unavailable', 'renderer_disposed']);
const finite = Number.isFinite;
const validId = value => typeof value === 'string' && value.length > 0 && value.length <= 128;

/* The producer samples body_clock after the request starts and before its callback arrives. Either direction can
 * consume the whole setup window. The existing 5 ms + 100 ppm margin widens that clock interval. Capture timestamps
 * remain producer estimates. This helper supplies diagnostics only and never admits or extends a figure's lease. */
export function producerCaptureAgeTiming({ started, serverTs, clockReceivedAt } = {}, captureTs, receivedAt) {
    if (![started, serverTs, clockReceivedAt, captureTs, receivedAt].every(finite)
        || started < 0 || clockReceivedAt < started || receivedAt < clockReceivedAt) return {};
    const elapsed = (receivedAt - started) / 1000;
    const upper = serverTs + elapsed + 0.005 + elapsed * 0.0001;
    const lower = serverTs + (receivedAt - clockReceivedAt) / 1000 - 0.005 - elapsed * 0.0001;
    const bounds = [(lower - captureTs) * 1000, (upper - captureTs) * 1000];
    const midpoint = bounds[0] / 2 + bounds[1] / 2, uncertainty = (bounds[1] - bounds[0]) / 2;
    if (![...bounds, midpoint, uncertainty].every(finite)) return {};
    return { producer_age_at_receive_ms: bounds[1], producer_age_at_receive_interval_ms: bounds,
        producer_age_at_receive_midpoint_ms: midpoint, producer_clock_uncertainty_ms: uncertainty,
        producer_clock_round_trip_bound_ms: clockReceivedAt - started };
}

/* Temporary observers retain the target's own draw hook and restore it when this diagnostic finishes. No scene
 * traversal or observer remains on the disabled path. A whole-engine frame cannot satisfy this target witness. */
function observeDraw(root, isDrawing) {
    const targets = [];
    root?.traverse?.(node => { if (node.isMesh || node.isPoints || node.isLine || node.isSprite) targets.push(node); });
    if (!targets.length || targets.length > DEN_LATENCY_LIMITS.draw_targets) return null;
    let drawn = false, active = true;
    const hooks = targets.map(node => {
        const original = node.onAfterRender;
        const observer = function (...args) {
            original?.apply(this, args);
            try { if (active && isDrawing() === true) drawn = true; }
            catch (_) { /* an optional observer cannot interrupt drawing */ }
        };
        node.onAfterRender = observer;
        return { node, original, observer };
    });
    return {
        drawn: () => drawn,
        dispose() {
            active = false;
            for (const { node, original, observer } of hooks) if (node.onAfterRender === observer) node.onAfterRender = original;
        },
    };
}

export function createDenLatencyTrace({ now = () => performance.now(), wallNow = () => Date.now(),
    timeOrigin = performance.timeOrigin } = {}) {
    const entries = [], lanes = new Map(), pending = new Set();
    let dropped = 0;
    function record(token, phase, fields = {}, at = now()) {
        const row = { ...token.key, phase, at_ms: at, unix_ms: wallNow() };
        if (finite(token.capture_ts)) row.capture_ts = token.capture_ts;
        if (finite(token.expires_ts)) row.expires_ts = token.expires_ts;
        if (finite(token.sampled_mono)) row.sampled_mono = token.sampled_mono;
        if (finite(token.producer_age_at_receive_ms)) row.producer_age_at_receive_ms = token.producer_age_at_receive_ms;
        if (token.producer_age_at_receive_interval_ms) {
            row.producer_clock_mapping = 'request_receive_interval';
            row.producer_age_at_receive_interval_ms = token.producer_age_at_receive_interval_ms.slice();
            row.producer_age_at_receive_midpoint_ms = token.producer_age_at_receive_midpoint_ms;
            row.producer_clock_uncertainty_ms = token.producer_clock_uncertainty_ms;
            row.producer_clock_round_trip_bound_ms = token.producer_clock_round_trip_bound_ms;
        }
        if (typeof token.lit === 'boolean') row.lit = token.lit;
        if (token.key.kind === 'body') row.capture_timing = 'producer_capture_estimate';
        if (finite(fields.frame_id)) row.frame_id = fields.frame_id;
        if (finite(fields.frame_submitted_mono_ms)) row.frame_submitted_at_ms = fields.frame_submitted_mono_ms;
        if (finite(fields.gpu_completed_mono_ms)) row.gpu_completed_at_ms = fields.gpu_completed_mono_ms;
        if (['webgl2', 'webgpu'].includes(fields.backend)) row.backend = fields.backend;
        if (REASONS.has(fields.reason)) row.reason = fields.reason;
        entries.push(row);
        if (entries.length > DEN_LATENCY_LIMITS.entries) { entries.shift(); dropped++; }
    }
    const current = token => token && lanes.get(token.key.kind) === token && !token.finished;
    function invalidate(kind, reason = 'scope_changed') {
        const token = lanes.get(kind);
        if (!token) return;
        lanes.delete(kind);
        if (!token.finished) record(token, 'unavailable', { reason: REASONS.has(reason) ? reason : 'unavailable' });
        token.finished = true;
        token.controller?.abort();
        token.witness?.dispose();
    }
    function receive(kind, event, receivedAt = now(), timing = {}) {
        const id = kind === 'body' ? event?.run_id : kind === 'spot' ? event?.epoch : null;
        if (!validId(id) || !Number.isSafeInteger(event?.seq) || event.seq < 0 || !finite(receivedAt)) return null;
        const previous = lanes.get(kind)?.key;
        if (previous && (previous.run_id ?? previous.epoch) === id && event.seq <= previous.seq) return null;
        invalidate(kind, 'superseded');
        const token = { key: { kind, ...(kind === 'body' ? { run_id: id } : { epoch: id }), seq: event.seq },
            capture_ts: finite(event.capture_ts) ? event.capture_ts : undefined,
            expires_ts: finite(event.expires_ts) ? event.expires_ts : undefined,
            sampled_mono: finite(event.sampled_mono) ? event.sampled_mono : undefined,
            producer_age_at_receive_ms: finite(timing.producer_age_at_receive_ms) ? timing.producer_age_at_receive_ms : undefined,
            lit: kind === 'spot' ? Object.values(event.fixtures || {}).some(row => row?.on === true) : undefined,
            finished: false };
        const bounds = timing.producer_age_at_receive_interval_ms;
        if (kind === 'body' && Array.isArray(bounds) && bounds.length === 2 && bounds.every(finite) && bounds[0] <= bounds[1]
            && [timing.producer_age_at_receive_midpoint_ms, timing.producer_clock_uncertainty_ms,
                timing.producer_clock_round_trip_bound_ms].every(finite)) {
            token.producer_age_at_receive_interval_ms = bounds.slice();
            token.producer_age_at_receive_midpoint_ms = timing.producer_age_at_receive_midpoint_ms;
            token.producer_clock_uncertainty_ms = timing.producer_clock_uncertainty_ms;
            token.producer_clock_round_trip_bound_ms = timing.producer_clock_round_trip_bound_ms;
        }
        lanes.set(kind, token);
        record(token, 'received', {}, receivedAt);
        return token;
    }
    function mark(token, phase) {
        if (current(token) && PHASES.has(phase)) record(token, phase);
    }
    function draw(token, { root, request, isCurrent, isDrawing = () => false } = {}) {
        if (!current(token) || token.controller) return;
        if (pending.size >= DEN_LATENCY_LIMITS.pending) { invalidate(token.key.kind, 'diagnostic_limit'); return; }
        if (typeof request !== 'function' || typeof isCurrent !== 'function') { invalidate(token.key.kind, 'renderer_receipt_unavailable'); return; }
        const witness = observeDraw(root, isDrawing);
        if (!witness) { invalidate(token.key.kind, 'draw_witness_unavailable'); return; }
        const controller = new AbortController();
        token.controller = controller; token.witness = witness;
        pending.add(token);
        const admitted = () => { try { return current(token) && isCurrent() === true; } catch (_) { return false; } };
        let receipt;
        try {
            receipt = request({ signal: controller.signal, isCurrent: admitted, isReady: witness.drawn,
                onSubmitted: value => { if (admitted()) record(token, 'submitted', value); } });
        } catch (_) { receipt = { ok: false, reason: 'renderer_receipt_unavailable' }; }
        Promise.resolve(receipt).then(result => {
            if (!current(token)) return;
            if (result?.ok === true && result.stage === 'gpu_completed_frame' && witness.drawn() && admitted()) {
                record(token, 'gpu_completed', result);
                token.finished = true;
            } else invalidate(token.key.kind, REASONS.has(result?.reason) ? result.reason : 'renderer_scope_changed');
        }, () => { if (current(token)) invalidate(token.key.kind, 'renderer_gpu_completion_failed'); }).finally(() => {
            pending.delete(token);
            witness.dispose();
        });
    }
    function snapshot() {
        return { schema: DEN_LATENCY_SCHEMA, presentation_observed: false, physical_commands: false,
            clock: { time_origin_ms: timeOrigin, exported_at_ms: now(), exported_unix_ms: wallNow() },
            limits: { ...DEN_LATENCY_LIMITS }, dropped_entries: dropped, pending: pending.size,
            entries: entries.map(row => ({ ...row, ...(row.producer_age_at_receive_interval_ms
                ? { producer_age_at_receive_interval_ms: row.producer_age_at_receive_interval_ms.slice() } : {}) })) };
    }
    function clear() {
        for (const kind of [...lanes.keys()]) invalidate(kind, 'trace_cleared');
        entries.length = 0; dropped = 0;
    }
    return Object.freeze({ receive, mark, draw, invalidate, snapshot, exportJson: () => JSON.stringify(snapshot()), clear,
        captureAgeTiming: producerCaptureAgeTiming });
}
