import test from 'node:test';
import assert from 'node:assert/strict';
import { createDenSourceClock, denSourceClockInterval, normalizeDenModelStages, denModelStageLabel, denModelAdmissionLabel,
    DEN_MODEL_RUNTIME_LIMITS as LIMITS } from '../app/src/home-den-model-runtime.mjs';

const stage = changes => ({ id: 'vjepa', role: 'forecast', state: 'running', configured: true,
    provisioned: null, loaded: null, device: null, model_id: 'facebook/vjepa2-vitg-fpc64-256',
    model_revision: null, profile_digest: null, reason: null, queued: 0, running: 1,
    observed_mono: 9, valid_until_mono: 12, ...changes });
const packet = (stages = [stage()], changes = {}) => ({ schema: 'den-model-stages-1', sampled_mono: 10, stages, ...changes });
const source = changes => ({ schema: 'den-source-clock-1', clock_domain: 'producer_monotonic', server_mono: 10, ...changes });
const clock = () => createDenSourceClock(source(), { startedAt: 1000, receivedAt: 1100 });
const options = changes => ({ clock: clock(), receivedAt: 1100, now: 1100, ...changes });
const row = (changes, context = options()) => normalizeDenModelStages(packet([stage(changes)]), context).stages[0];

test('configured, provisioned, loaded and work counts remain independent nullable facts', () => {
    const value = row({ configured: true, provisioned: null, loaded: null });
    assert.equal(value.configured, true);
    assert.equal(value.provisioned, null);
    assert.equal(value.loaded, null);
    assert.equal(value.running, 1);
    assert.equal(Object.hasOwn(value, 'consumed'), false);
    const resident = row({ configured: false, provisioned: false, loaded: true, state: 'disabled' });
    assert.equal(resident.loaded, true, 'reported residency is not inferred from configuration');
    assert.equal(resident.configured, false);
    const unknown = row({ configured: 'yes', loaded: 1, provisioned: {}, running: true, queued: false });
    for (const key of ['configured', 'provisioned', 'loaded', 'running', 'queued']) assert.equal(unknown[key], null);
});

test('only whitelisted metadata survives; unknown admission scope remains null', () => {
    const privateFields = { host: 'PRIVATE_HOST', path: '/home/PRIVATE_PATH', argv: ['PRIVATE_ARGV'],
        error: 'PRIVATE_ERROR', prompt: 'PRIVATE_PROMPT', clip: 'PRIVATE_CLIP', url: 'https://PRIVATE_URL',
        consumed: true, live: true, admission_scope: 'PRIVATE_SCOPE' };
    const output = normalizeDenModelStages(packet([stage(privateFields)], { ...privateFields }), options());
    assert.doesNotMatch(JSON.stringify(output), /PRIVATE_|\/home\/|https:|consumed/);
    assert.equal(output.stages[0].admission_scope, null);
    assert.deepEqual(Object.keys(output.stages[0]), ['id', 'role', 'state', 'admission_scope', 'configured', 'provisioned', 'loaded',
        'device', 'model_id', 'model_revision', 'profile_digest', 'reason', 'queued', 'running',
        'observed_mono', 'valid_until_mono', 'freshness']);
    assert.equal(row({ reason: '/home/private-error', device: 'PRIVATE_HOST:cuda' }).reason, null);
    assert.equal(row({ device: 'PRIVATE_HOST:cuda' }).device, null);
});

test('public namespaced model IDs survive while path and URL forms are discarded', () => {
    for (const model_id of ['owner/model', 'Qwen/Qwen3-VL-4B-Instruct', 'facebook/vjepa2-vitg-fpc64-256', 'yolo26s']) {
        assert.equal(row({ model_id }).model_id, model_id);
    }
    for (const model_id of ['/opt/private/model', '../model', 'owner/../model', 'C:\\Users\\private\\model.pt',
        'C:/private/model', 'file:///private/model', 'https://private/model', '//private/model',
        'owner/model?token=private', 'owner/model#private', 'camera.example.com/model', 'camera.example.com', 'localhost/model',
        '127.0.0.1/model', 'models/private', 'weights/private', 'private.onnx', 'private.yaml',
        '$HOME/model', '~/.cache/model', 'owner/\nprivate', 'a'.repeat(193)]) {
        assert.equal(row({ model_id }).model_id, null, model_id);
    }
    assert.equal(row({ model_revision: '/private/revision' }).model_revision, null);
    assert.equal(row({ model_revision: 'https://private/revision' }).model_revision, null);
    assert.equal(row({ model_revision: 'v2.3.1' }).model_revision, 'v2.3.1');
    assert.equal(row({ profile_digest: `sha256:${'A'.repeat(64)}` }).profile_digest, `sha256:${'a'.repeat(64)}`);
    assert.equal(row({ profile_digest: '/private/digest' }).profile_digest, null);
});

test('lifecycle labels preserve planned/evaluating reasons without claiming live consumption', () => {
    for (const [state, label] of Object.entries({ disabled: 'Disabled', not_admitted: 'Not admitted',
        preparing: 'Preparing', idle: 'Idle', running: 'Running', degraded: 'Degraded', unavailable: 'Unavailable',
        provisioned: 'Provisioned', ready: 'Ready', draining: 'Draining', released: 'Released',
        externally_managed: 'Externally managed' })) {
        const value = row({ state });
        assert.equal(value.state, state);
        assert.equal(denModelStageLabel(value), label);
        assert.doesNotMatch(label, /live|consum/i);
    }
    for (const reason of ['planned', 'evaluating']) {
        const value = row({ state: 'not_admitted', reason, configured: true, running: 1 });
        assert.equal(value.reason, reason);
        assert.equal(value.state, 'not_admitted');
        assert.equal(denModelStageLabel(value), 'Not admitted');
    }
    assert.equal(row({ state: 'invented' }).state, 'unavailable');
    assert.equal(denModelStageLabel({ state: '__proto__' }), 'Unavailable');
    assert.equal(denModelStageLabel({ state: 'constructor' }), 'Unavailable');
    const ready = row({ state: 'ready', loaded: null, reason: 'stage_observation_stale' });
    assert.equal(ready.loaded, null, 'ready is a lifecycle fact, not inferred residency or consumption');
    assert.equal(ready.reason, 'stage_observation_stale');
    assert.equal(Object.hasOwn(ready, 'consumed'), false);
});

test('admission labels distinguish bounded evaluation, production and unknown independently from running', () => {
    for (const state of ['running', 'ready', 'idle', 'externally_managed', 'released']) {
        const trial = row({ state, configured: true, loaded: true, running: 1, admission_scope: 'bounded_trial' });
        assert.equal(trial.admission_scope, 'bounded_trial');
        assert.equal(denModelAdmissionLabel(trial), 'Bounded trial / evaluation');
        assert.doesNotMatch(denModelAdmissionLabel(trial), /production/i);
        assert.equal(Object.hasOwn(trial, 'consumed'), false);
    }
    assert.equal(denModelAdmissionLabel(row({ admission_scope: 'production' })), 'Production admitted');
    assert.equal(denModelAdmissionLabel(row({ admission_scope: 'not_admitted', running: 1 })), 'Not admitted');
    for (const admission_scope of [undefined, null, 'invented', '/private/scope', true, 1, 'constructor', '__proto__',
        { toString: {}, valueOf: {} }, []]) {
        const value = row({ admission_scope, state: 'running', loaded: true });
        assert.equal(value.admission_scope, null);
        assert.equal(denModelAdmissionLabel(value), 'Admission unknown');
    }
    assert.equal(row({ state: 'not_admitted' }).admission_scope, null, 'lifecycle does not fill missing scope');
    assert.equal(denModelAdmissionLabel(Object.create({ admission_scope: 'production' })), 'Admission unknown');
    assert.equal(denModelAdmissionLabel({ admission_scope: { toString: {}, valueOf: {} } }), 'Admission unknown');
    assert.equal(denModelStageLabel({ state: { toString: {}, valueOf: {} } }), 'Unavailable');
});

test('source clock bounds the entire setup request and grows uncertainty with elapsed time', () => {
    const anchor = createDenSourceClock(source({ server_mono: 100, private: '/secret' }), { startedAt: 0, receivedAt: 1000 });
    assert.equal(Object.isFrozen(anchor), true);
    assert.doesNotMatch(JSON.stringify(anchor), /secret|private/);
    const first = denSourceClockInterval(anchor, 1000);
    assert.ok(Math.abs(first.lower_mono - 99.9949) < 1e-9);
    assert.ok(Math.abs(first.upper_mono - 101.0051) < 1e-9);
    assert.ok(Math.abs(first.midpoint_mono - 100.5) < 1e-9);
    assert.ok(first.uncertainty_s > .5, 'request setup is uncertainty, not a receipt-only anchor');
    const later = denSourceClockInterval(anchor, 5000);
    assert.ok(later.uncertainty_s > first.uncertainty_s);
    assert.equal(first.request_window_ms, 1000);
});

test('unknown clock never yields freshness, including inconsistent callback context', () => {
    const value = row({}, {});
    assert.equal(value.freshness, 'unknown');
    assert.equal(normalizeDenModelStages(packet()).clock_known, false);
    assert.equal(row({}, options({ receivedAt: 1000, now: 1200 })).freshness, 'unknown',
        'an event before the anchor callback cannot borrow the later clock');
    assert.equal(row({}, options({ now: 1000 })).freshness, 'unknown');
    assert.equal(row({ observed_mono: null, valid_until_mono: null }).freshness, 'unknown');
});

test('configured unobserved 8B remains renderable while genuine lease times stay exact', () => {
    const waiting = stage({ id: 'vjepa-lm-qwen3-8b', role: 'motion_intent', state: 'unavailable', configured: true,
        model_id: 'kkt94/Llama-V-JEPA-LM-Qwen3-8B', observed_mono: null, valid_until_mono: null,
        loaded: null, provisioned: null, queued: null, running: null, reason: 'stage_observation_stale' });
    const expired = stage({ id: 'expired', state: 'unavailable', observed_mono: 6, valid_until_mono: 9, loaded: null });
    const fresh = stage({ id: 'fresh', state: 'idle', observed_mono: 10, valid_until_mono: 13, loaded: true });
    const output = normalizeDenModelStages(packet([waiting, expired, fresh]), options({ now: 3200 }));
    assert.equal(output.rejected_stages, 0);
    assert.equal(output.stages.length, 3);
    const [unknown, old, current] = output.stages;
    assert.equal(unknown.configured, true);
    assert.equal(unknown.freshness, 'unknown');
    assert.equal(denModelStageLabel(unknown), 'Unavailable');
    assert.equal(unknown.loaded, null);
    assert.deepEqual([unknown.observed_mono, unknown.valid_until_mono], [null, null]);
    assert.equal(old.freshness, 'stale');
    assert.deepEqual([old.observed_mono, old.valid_until_mono], [6, 9]);
    assert.equal(current.freshness, 'fresh');
    assert.equal(current.loaded, true);
    assert.deepEqual([current.observed_mono, current.valid_until_mono], [10, 13]);
});

test('fresh, uncertain and expired windows use conservative producer clock bounds', () => {
    assert.equal(row({}).freshness, 'fresh');
    assert.equal(row({}, options({ now: 3100 })).freshness, 'unknown', 'uncertainty straddles expiry');
    assert.equal(row({ loaded: true }, options({ now: 3200 })).freshness, 'stale');
    assert.equal(row({ loaded: true }, options({ now: 3200 })).loaded, true, 'reported facts stay distinct from freshness');
    const boundary = denSourceClockInterval(clock(), 1100).upper_mono;
    assert.equal(row({ valid_until_mono: boundary }).freshness, 'unknown', 'expiry boundary is not fresh');
    const uncertain = createDenSourceClock(source({ server_mono: 100 }), { startedAt: 0, receivedAt: 1000 });
    const value = normalizeDenModelStages(packet([stage({ observed_mono: 100.8, valid_until_mono: 102 })],
        { sampled_mono: 101 }), { clock: uncertain, receivedAt: 1000, now: 1000 });
    assert.equal(value.stages[0].freshness, 'unknown', 'observation may still be in the future within the mapping interval');
});

test('invalid, backwards, nonfinite and overlong source clocks are rejected', () => {
    for (const [wire, context] of [[source({ schema: 'other' }), { startedAt: 0, receivedAt: 1 }],
        [source({ clock_domain: 'wall' }), { startedAt: 0, receivedAt: 1 }],
        [source({ server_mono: NaN }), { startedAt: 0, receivedAt: 1 }],
        [source({ server_mono: Infinity }), { startedAt: 0, receivedAt: 1 }],
        [source({ server_mono: -1 }), { startedAt: 0, receivedAt: 1 }],
        [source(), { startedAt: 2, receivedAt: 1 }], [source(), { startedAt: -1, receivedAt: 1 }],
        [source(), { startedAt: 0, receivedAt: LIMITS.setup_ms + 1 }]]) assert.equal(createDenSourceClock(wire, context), null);
    assert.equal(denSourceClockInterval(clock(), 1000), null);
    assert.equal(denSourceClockInterval(clock(), Infinity), null);
    assert.equal(denSourceClockInterval(clock(), LIMITS.clock_elapsed_ms + 2000), null);
});

test('future and malformed model timing cannot become usable by waiting', () => {
    const future = packet([stage()], { sampled_mono: 11 });
    assert.equal(normalizeDenModelStages(future, options({ now: 20000 })), null);
    for (const timing of [{ observed_mono: 11 }, { observed_mono: Infinity }, { valid_until_mono: NaN },
        { observed_mono: '9' }, { observed_mono: null, valid_until_mono: 12 },
        { observed_mono: 9, valid_until_mono: 8 }, { observed_mono: 9, valid_until_mono: 310 }]) {
        const output = normalizeDenModelStages(packet([stage(timing)]), options());
        assert.equal(output.stages.length, 0);
        assert.equal(output.rejected_stages, 1);
    }
    for (const sampled_mono of [NaN, Infinity, -1, '10', null]) assert.equal(normalizeDenModelStages(packet([], { sampled_mono })), null);
});

test('bounds and ambiguous duplicate identities fail conservatively without truncation', () => {
    const rows = Array.from({ length: LIMITS.stages }, (_, i) => stage({ id: `stage_${i}` }));
    assert.equal(normalizeDenModelStages(packet(rows)).stages.length, LIMITS.stages);
    assert.equal(normalizeDenModelStages(packet([...rows, stage({ id: 'extra' })])), null);
    const duplicate = normalizeDenModelStages(packet([stage(), stage({ loaded: true }), stage({ id: 'other' })]));
    assert.deepEqual(duplicate.stages.map(value => value.id), ['other']);
    assert.equal(duplicate.rejected_stages, 2);
    assert.equal(row({ queued: LIMITS.count }).queued, LIMITS.count);
    for (const queued of [LIMITS.count + 1, -1, .5, Infinity, '1']) assert.equal(row({ queued }).queued, null);
    assert.equal(normalizeDenModelStages(packet([stage({ id: 'a'.repeat(LIMITS.token + 1) })])).stages.length, 0);
});

test('normalization copies no prototypes, references or inherited facts', () => {
    const inherited = Object.assign(Object.create({ loaded: true, provisioned: true }), stage());
    delete inherited.loaded;
    delete inherited.provisioned;
    const input = packet([inherited]);
    const output = normalizeDenModelStages(input, options());
    assert.equal(output.stages[0].loaded, null);
    assert.equal(output.stages[0].provisioned, null);
    output.stages[0].state = 'disabled';
    assert.equal(input.stages[0].state, 'running');
    assert.equal(normalizeDenModelStages(Object.assign(Object.create({ schema: 'den-model-stages-1' }),
        { sampled_mono: 10, stages: [] })), null);
});
