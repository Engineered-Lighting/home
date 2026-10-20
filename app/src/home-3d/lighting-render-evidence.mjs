/* Opt-in timing evidence from the normal renderer. No capture, pixels, RAF
 * inference, or fixture writes. GPU completion proves the submitted frame
 * finished; the browser compositor/monitor presentation remains unobserved. */
export function createLightingRenderEvidence({ renderer, nowMs = () => Date.now(),
    setTimeout: later = globalThis.setTimeout, clearTimeout: cancel = globalThis.clearTimeout,
    isVisible = () => true, timeoutMs = 2000 } = {}) {
    let disposed = false, frameId = 0;
    const jobs = new Set();
    const valid = job => {
        try { return !disposed && isVisible() === true && job.isCurrent() === true; }
        catch (_) { return false; }
    };
    const finish = (job, result) => {
        if (!jobs.delete(job)) return;
        if (job.timer != null) cancel(job.timer);
        if (job.pollTimer != null) cancel(job.pollTimer);
        try { job.gl?.deleteSync(job.fence); } catch (_) { /* lost context */ }
        job.resolve(result);
    };
    const fail = (job, reason) => finish(job, { ok: false, reason });
    return Object.freeze({
        request({ isCurrent = () => false, isReady = () => true } = {}) {
            if (disposed || typeof isCurrent !== 'function' || jobs.size >= 16)
                return Promise.resolve({ ok: false, reason: 'renderer_receipt_unavailable' });
            return new Promise(resolve => {
                const job = { resolve, isCurrent, isReady, submitted: false, timer: null, started: nowMs() };
                jobs.add(job);
                job.timer = later(() => fail(job, 'renderer_frame_timeout'), timeoutMs);
            });
        },
        // Invoked synchronously after renderer.render(scene, camera), only in
        // the visible resident engine's ordinary presentation loop.
        afterRender() {
            frameId++;
            for (const job of jobs) {
                if (job.submitted) continue;
                if (!valid(job)) { fail(job, 'renderer_scope_changed'); continue; }
                try { if (job.isReady() !== true) continue; }
                catch (_) { fail(job, 'renderer_scope_changed'); continue; }
                job.submitted = true;
                const submittedAt = nowMs(), submittedFrame = frameId;
                const complete = backend => {
                    if (!valid(job)) { fail(job, 'renderer_scope_changed'); return; }
                    finish(job, { ok: true, stage: 'gpu_completed_frame', frame_id: submittedFrame,
                        frame_submitted_at_ms: submittedAt, gpu_completed_at_ms: nowMs(), backend,
                        presentation_observed: false, physical_commands: false });
                };
                const queue = renderer?.backend?.isWebGPUBackend && renderer.backend.device?.queue;
                if (typeof queue?.onSubmittedWorkDone === 'function') {
                    // The completion callback is bound to this submission,
                    // even if another frame is submitted while it is pending.
                    try { Promise.resolve(queue.onSubmittedWorkDone()).then(() => complete('webgpu'),
                        () => fail(job, 'renderer_gpu_completion_failed')); }
                    catch (_) { fail(job, 'renderer_gpu_completion_failed'); }
                    continue;
                }
                const gl = renderer?.backend?.gl || renderer?.getContext?.();
                if (!gl || typeof gl.fenceSync !== 'function' || gl.isContextLost?.()) {
                    fail(job, 'renderer_gpu_fence_unavailable'); continue;
                }
                try {
                    job.gl = gl; job.fence = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0);
                    if (!job.fence) { fail(job, 'renderer_gpu_fence_unavailable'); continue; }
                    gl.flush();
                    const poll = () => {
                        if (!jobs.has(job)) return;
                        if (!valid(job) || gl.isContextLost?.()) { fail(job, 'renderer_scope_changed'); return; }
                        let status;
                        try { status = gl.clientWaitSync(job.fence, 0, 0); }
                        catch (_) { fail(job, 'renderer_gpu_completion_failed'); return; }
                        if (status === gl.ALREADY_SIGNALED || status === gl.CONDITION_SATISFIED) complete('webgl2');
                        else if (status === gl.WAIT_FAILED) fail(job, 'renderer_gpu_completion_failed');
                        else if (nowMs() - job.started >= timeoutMs) fail(job, 'renderer_frame_timeout');
                        else job.pollTimer = later(poll, 8);
                    };
                    poll();
                } catch (_) { fail(job, 'renderer_gpu_completion_failed'); }
            }
        },
        dispose() {
            disposed = true;
            for (const job of jobs) { if (job.pollTimer != null) cancel(job.pollTimer); fail(job, 'renderer_disposed'); }
        },
    });
}
