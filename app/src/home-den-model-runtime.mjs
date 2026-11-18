/* Pure metadata projection. No DOM, timers, requests, worker control or forecast admission.
 * Pass only this connection's source-clock anchor; its absence never means a fresh stage.
 * Reported configuration, provisioning, residency and work counts stay independent facts.
 * Running describes work, not consumption, production admission or a live forecast. */
export const DEN_MODEL_STAGES_SCHEMA = 'den-model-stages-1';
export const DEN_SOURCE_CLOCK_SCHEMA = 'den-source-clock-1';
export const DEN_MODEL_RUNTIME_LIMITS = Object.freeze({ stages: 32, token: 64, model_id: 192,
    revision: 96, count: 1024, setup_ms: 30_000, clock_elapsed_ms: 86_400_000,
    producer_seconds: 1e12, browser_ms: 1e12, validity_s: 300 });

const STATES = new Set(['disabled', 'not_admitted', 'preparing', 'idle', 'running', 'degraded', 'unavailable',
    'provisioned', 'ready', 'draining', 'released', 'externally_managed']);
const LABELS = Object.freeze({ disabled: 'Disabled', not_admitted: 'Not admitted', preparing: 'Preparing',
    idle: 'Idle', running: 'Running', degraded: 'Degraded', unavailable: 'Unavailable', provisioned: 'Provisioned',
    ready: 'Ready', draining: 'Draining', released: 'Released', externally_managed: 'Externally managed' });
const ADMISSION_LABELS = Object.freeze({ not_admitted: 'Not admitted', bounded_trial: 'Bounded trial / evaluation',
    production: 'Production admitted' });
const REASONS = new Set(['planned', 'evaluating', 'disabled', 'not_admitted', 'not_configured',
    'not_provisioned', 'not_loaded', 'preparing', 'loading', 'warming', 'idle', 'no_work', 'ready',
    'model_missing', 'profile_missing', 'gpu_unavailable', 'cpu_fallback', 'timeout', 'worker_unavailable',
    'stale', 'stage_observation_stale', 'unavailable', 'error', 'busy', 'queued', 'unknown']);
const LOCAL_ROOTS = new Set(['home', 'users', 'user', 'tmp', 'var', 'etc', 'opt', 'usr', 'srv', 'mnt',
    'media', 'run', 'proc', 'sys', 'dev', 'app', 'models', 'weights', 'checkpoints', 'cache', 'private',
    'assets', 'data', 'storage', 'localhost']);
const finite = value => typeof value === 'number' && Number.isFinite(value);
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const own = (value, key) => object(value) && Object.hasOwn(value, key) ? value[key] : undefined;
const seconds = value => finite(value) && value >= 0 && value <= DEN_MODEL_RUNTIME_LIMITS.producer_seconds;
const browserTime = value => finite(value) && value >= 0 && value <= DEN_MODEL_RUNTIME_LIMITS.browser_ms;
const token = value => typeof value === 'string' && value.length <= DEN_MODEL_RUNTIME_LIMITS.token
    && /^[a-z][a-z0-9_-]*$/.test(value) ? value : null;
const fact = value => typeof value === 'boolean' ? value : null;
const count = value => Number.isSafeInteger(value) && value >= 0 && value <= DEN_MODEL_RUNTIME_LIMITS.count ? value : null;

/* The producer must supply public catalog IDs. Namespace syntax alone cannot
 * distinguish every owner/model ID from an arbitrary relative path. Recognizable
 * path, hostname, URL and filename forms are rejected, never truncated. */
function publicModelId(value) {
    if (typeof value !== 'string' || !value.length || value.length > DEN_MODEL_RUNTIME_LIMITS.model_id) return null;
    const parts = value.split('/');
    if (parts.length > 2 || parts.length === 2 && parts[0].includes('.')
        || parts.some(part => !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$/.test(part)
        || part.includes('..')) || LOCAL_ROOTS.has(parts[0].toLowerCase())) return null;
    if (/\.(?:pt|pth|onnx|bin|safetensors|ckpt|tflite|gguf|npz|npy|json|yaml|yml|toml|txt|log|py|mjs|exe)$/i.test(value)
        || /\.(?:local|lan|internal|localhost)$/i.test(value)
        || parts.length === 1 && /\.[A-Za-z][A-Za-z-]+$/.test(value)
        || /^(?:\d{1,3}\.){3}\d{1,3}(?:\/|$)/.test(value)) return null;
    return value;
}

function revision(value) {
    return typeof value === 'string' && value.length <= DEN_MODEL_RUNTIME_LIMITS.revision
        && /^[A-Za-z0-9][A-Za-z0-9._+-]*$/.test(value) && !value.includes('..') ? value : null;
}

function profileDigest(value) {
    if (typeof value !== 'string') return null;
    return /^(?:sha256:)?[0-9a-f]{64}$/i.test(value) ? value.toLowerCase() : null;
}

function device(value) {
    return typeof value === 'string' && /^(?:cpu|cuda(?::[0-9]{1,2})?|mps|webgpu|webgl2)$/.test(value) ? value : null;
}

/* Browser timestamps are milliseconds; wire producer monotonic timestamps are seconds.
 * The server samples after request start b0 and before callback entry b1. Neither
 * direction is assigned an RTT/2 fact. A 5ms +100ppm margin widens both bounds. */
export function createDenSourceClock(payload, { startedAt, receivedAt } = {}) {
    if (!object(payload) || own(payload, 'schema') !== DEN_SOURCE_CLOCK_SCHEMA
        || own(payload, 'clock_domain') !== 'producer_monotonic' || !seconds(own(payload, 'server_mono'))
        || !browserTime(startedAt) || !browserTime(receivedAt) || receivedAt < startedAt
        || receivedAt - startedAt > DEN_MODEL_RUNTIME_LIMITS.setup_ms) return null;
    return Object.freeze({ schema: DEN_SOURCE_CLOCK_SCHEMA, clock_domain: 'producer_monotonic',
        server_mono: payload.server_mono, started_at_ms: startedAt, received_at_ms: receivedAt });
}

export function denSourceClockInterval(clock, now) {
    if (!object(clock) || !browserTime(now)) return null;
    const checked = createDenSourceClock(clock, { startedAt: own(clock, 'started_at_ms'), receivedAt: own(clock, 'received_at_ms') });
    if (!checked || now < checked.received_at_ms || now - checked.started_at_ms > DEN_MODEL_RUNTIME_LIMITS.clock_elapsed_ms) return null;
    const elapsed = (now - checked.started_at_ms) / 1000;
    const margin = .005 + elapsed * .0001;
    const lower = checked.server_mono + (now - checked.received_at_ms) / 1000 - margin;
    const upper = checked.server_mono + elapsed + margin;
    if (![lower, upper].every(finite) || upper < 0 || lower > upper) return null;
    return { lower_mono: lower, upper_mono: upper, midpoint_mono: lower / 2 + upper / 2,
        uncertainty_s: (upper - lower) / 2, request_window_ms: checked.received_at_ms - checked.started_at_ms };
}

function rowTiming(row, sampled, atReceive, atNow) {
    const observed = own(row, 'observed_mono') ?? null, until = own(row, 'valid_until_mono') ?? null;
    if (observed !== null && (!seconds(observed) || observed > sampled)
        || until !== null && !seconds(until)
        || until !== null && observed === null
        || until !== null && (until < observed || until - observed > DEN_MODEL_RUNTIME_LIMITS.validity_s)) return null;
    if (atReceive && observed !== null && observed > atReceive.upper_mono) return null;
    let freshness = 'unknown';
    if (atNow && observed !== null && until !== null) {
        if (atNow.lower_mono >= until) freshness = 'stale';
        else if (atNow.lower_mono >= observed && atNow.upper_mono < until) freshness = 'fresh';
    }
    return { observed_mono: observed, valid_until_mono: until, freshness };
}

/* payload is event.health.model_stages. null means the envelope was rejected;
 * malformed rows are omitted, counted, and cannot lend freshness to another row.
 * Expired/unknown facts remain explicitly reported facts, never fresh authority.
 * Admission scope is independent from lifecycle/work and never implies consumption. */
export function normalizeDenModelStages(payload, { clock = null, receivedAt, now = receivedAt } = {}) {
    if (!object(payload) || own(payload, 'schema') !== DEN_MODEL_STAGES_SCHEMA || !seconds(own(payload, 'sampled_mono'))
        || !Array.isArray(own(payload, 'stages')) || payload.stages.length > DEN_MODEL_RUNTIME_LIMITS.stages) return null;
    const atReceive = denSourceClockInterval(clock, receivedAt);
    const atNow = atReceive && browserTime(now) && browserTime(receivedAt) && now >= receivedAt
        ? denSourceClockInterval(clock, now) : null;
    if (atReceive && payload.sampled_mono > atReceive.upper_mono) return null;
    const stages = [], ids = new Map();
    for (const row of payload.stages) {
        const id = token(own(row, 'id'));
        if (id) ids.set(id, (ids.get(id) || 0) + 1);
    }
    let rejected = 0;
    for (const row of payload.stages) {
        const id = token(own(row, 'id'));
        const role = token(own(row, 'role'));
        if (!id || !role || ids.get(id) !== 1) { rejected++; continue; }
        const timing = rowTiming(row, payload.sampled_mono, atReceive, atNow);
        if (!timing) { rejected++; continue; }
        const state = own(row, 'state'), reason = own(row, 'reason');
        const admission = own(row, 'admission_scope');
        stages.push({ id, role, state: STATES.has(state) ? state : 'unavailable',
            admission_scope: typeof admission === 'string' && Object.hasOwn(ADMISSION_LABELS, admission) ? admission : null,
            configured: fact(own(row, 'configured')), provisioned: fact(own(row, 'provisioned')), loaded: fact(own(row, 'loaded')),
            device: device(own(row, 'device')), model_id: publicModelId(own(row, 'model_id')), model_revision: revision(own(row, 'model_revision')),
            profile_digest: profileDigest(own(row, 'profile_digest')), reason: REASONS.has(reason) ? reason : null,
            queued: count(own(row, 'queued')), running: count(own(row, 'running')), ...timing });
    }
    return { schema: DEN_MODEL_STAGES_SCHEMA, sampled_mono: payload.sampled_mono,
        clock_known: atReceive !== null && atNow !== null, rejected_stages: rejected, stages };
}

/* State labels never claim that configured, resident, queued or running work is
 * consumed by the live loop. Planned/evaluating reasons remain separate metadata. */
export function denModelStageLabel(stage) {
    const state = own(stage, 'state');
    return typeof state === 'string' && Object.hasOwn(LABELS, state) ? LABELS[state] : LABELS.unavailable;
}

/* An explicitly reported production admission is still not evidence that a
 * result was consumed. A bounded trial/evaluation never becomes production
 * because a stage is configured, ready, loaded or running. */
export function denModelAdmissionLabel(stage) {
    const scope = own(stage, 'admission_scope');
    return typeof scope === 'string' && Object.hasOwn(ADMISSION_LABELS, scope) ? ADMISSION_LABELS[scope] : 'Admission unknown';
}
