import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createLightingRenderEvidence } from '../app/src/home-3d/lighting-render-evidence.mjs';

const pause = () => new Promise(resolve => setImmediate(resolve));
function clock() {
    let now = 0, sequence = 0; const timers = new Map();
    return { nowMs: () => now, setTimeout: (fn, ms) => { timers.set(++sequence, { fn, at: now + ms }); return sequence; },
        clearTimeout: id => timers.delete(id), size: () => timers.size,
        advance(ms) { const end = now + ms; for (;;) {
            const next = [...timers].filter(([, task]) => task.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
            if (!next) break; timers.delete(next[0]); now = next[1].at; next[1].fn();
        } now = end; },
    };
}
function fixture() {
    const time = clock(), calls = [], gl = { SYNC_GPU_COMMANDS_COMPLETE: 1, TIMEOUT_EXPIRED: 2,
        ALREADY_SIGNALED: 3, CONDITION_SATISFIED: 4, WAIT_FAILED: 5, status: 2,
        fenceSync: (...args) => { calls.push(['fence', ...args]); return {}; },
        flush: () => calls.push(['flush']), clientWaitSync() { return this.status; },
        deleteSync: () => calls.push(['delete']), isContextLost: () => false };
    let current = true, visible = true, ready = true;
    const evidence = createLightingRenderEvidence({ renderer: { getContext: () => gl }, ...time, isVisible: () => visible });
    const request = () => evidence.request({ isCurrent: () => current, isReady: () => ready });
    return { time, gl, calls, evidence, request, setCurrent: value => { current = value; },
        setVisible: value => { visible = value; }, setReady: value => { ready = value; } };
}

test('a requested receipt waits for a normal rendered frame and an actual GPU fence completion', async () => {
    const f = fixture(); let result = null; const promise = f.request().then(value => { result = value; });
    f.time.advance(16); await pause(); assert.equal(result, null); assert.equal(f.calls.length, 0);
    f.evidence.afterRender(); await pause(); assert.equal(result, null, 'render submission is not GPU completion');
    f.time.advance(8); assert.equal(result, null);
    f.gl.status = f.gl.CONDITION_SATISFIED; f.time.advance(8); await promise;
    assert.equal(result.ok, true); assert.equal(result.stage, 'gpu_completed_frame');
    assert.equal(result.frame_submitted_at_ms, 16); assert.equal(result.gpu_completed_at_ms, 32);
    assert.equal(result.presentation_observed, false); assert.equal(f.time.size(), 0);
    assert.equal(f.calls.filter(row => row[0] === 'delete').length, 1); f.evidence.dispose();
});

test('settling presentation waits for the next normal frame instead of certifying an in-progress animation', async () => {
    const f = fixture(); f.setReady(false); let result = null;
    const pending = f.request().then(value => { result = value; });
    f.evidence.afterRender(); await pause(); assert.equal(f.calls.length, 0); assert.equal(result, null);
    f.setReady(true); f.gl.status = f.gl.ALREADY_SIGNALED; f.evidence.afterRender(); await pending;
    assert.equal(result.frame_id, 2); f.evidence.dispose();
});

test('hidden output, superseded scope and lost GPU context cannot produce completed evidence', async () => {
    for (const invalidate of [f => f.setCurrent(false), f => f.setVisible(false), f => { f.gl.isContextLost = () => true; }]) {
        const f = fixture(), pending = f.request(); f.evidence.afterRender(); invalidate(f);
        f.gl.status = f.gl.ALREADY_SIGNALED; f.time.advance(8);
        assert.equal((await pending).ok, false); assert.equal(f.time.size(), 0); f.evidence.dispose();
    }
});

test('no normal frame, absent fence and failed fence remain explicitly unavailable', async () => {
    const never = fixture(), noFrame = never.request(); never.time.advance(2000);
    assert.equal((await noFrame).reason, 'renderer_frame_timeout'); never.evidence.dispose();
    for (const mutate of [f => { f.gl.fenceSync = undefined; }, f => { f.gl.status = f.gl.WAIT_FAILED; }]) {
        const f = fixture(); mutate(f); const pending = f.request(); f.evidence.afterRender();
        assert.equal((await pending).ok, false); f.evidence.dispose();
    }
});

test('WebGPU uses submitted work completion and rechecks authority after completion', async () => {
    const time = clock(); let finish, current = true;
    const evidence = createLightingRenderEvidence({ ...time, renderer: { backend: { isWebGPUBackend: true,
        device: { queue: { onSubmittedWorkDone: () => new Promise(resolve => { finish = resolve; }) } } } } });
    let result = null; const pending = evidence.request({ isCurrent: () => current }).then(value => { result = value; });
    evidence.afterRender(); await pause(); assert.equal(result, null);
    current = false; finish(); await pending; assert.equal(result.ok, false);
    current = true; const next = evidence.request({ isCurrent: () => current }); evidence.afterRender(); finish();
    assert.equal((await next).backend, 'webgpu'); assert.equal(time.size(), 0); evidence.dispose();
});

test('dispose cancels pending polling and requests are bounded', async () => {
    const f = fixture(), pending = Array.from({ length: 16 }, () => f.request());
    assert.equal((await f.request()).reason, 'renderer_receipt_unavailable'); f.evidence.afterRender(); f.evidence.dispose();
    assert.ok((await Promise.all(pending)).every(result => result.reason === 'renderer_disposed'));
    assert.equal(f.time.size(), 0);
});

test('renderer evidence is wired after the ordinary render and remains opt-in in Home', () => {
    const engine = fs.readFileSync(new URL('../app/src/home-3d/engine.js', import.meta.url), 'utf8');
    const home = fs.readFileSync(new URL('../app/src/home-apartment.jsx', import.meta.url), 'utf8');
    assert.match(engine, /renderer\.render\(scene, camera\);\s*lightingRenderEvidence\.afterRender\(\);/);
    assert.match(home, /get\("den-lighting-evidence"\) === "1"/);
    assert.match(home, /if \(diagnosticApi\) window\.HomeDenLightingEvidence = diagnosticApi/);
    assert.match(home, /engine\.requestLightingRenderEvidence\?\.\(context\)/);
});
