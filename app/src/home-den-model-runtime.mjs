/* Pure metadata projection. No DOM, timers, requests, worker control or forecast admission.
 * Pass only this connection's source-clock anchor; its absence never means a fresh stage.
 * Reported configuration, provisioning, residency and work counts stay independent facts.
 * Running describes work, not consumption, production admission or a live forecast.
 * An intent hint stays an uncalibrated advisory: never a probability, a use or a consumption. */
export const DEN_MODEL_STAGES_SCHEMA = 'den-model-stages-1';
export const DEN_SOURCE_CLOCK_SCHEMA = 'den-source-clock-1';
export const DEN_MODEL_RUNTIME_LIMITS = Object.freeze({ stages: 32, token: 64, model_id: 192,
    revision: 96, count: 1024, setup_ms: 30_000, clock_elapsed_ms: 86_400_000,
    producer_seconds: 1e12, browser_ms: 1e12, validity_s: 300, hints: 2 });

const STATES = new Set(['disabled', 'not_admitted', 'preparing', 'idle', 'running', 'degraded', 'unavailable',
    'provisioned', 'ready', 'draining', 'released', 'externally_managed']);
const LABELS = Object.freeze({ disabled: 'Disabled', not_admitted: 'Not admitted', preparing: 'Preparing',
    idle: 'Idle', running: 'Running', degraded: 'Degraded', unavailable: 'Unavailable', provisioned: 'Provisioned',
    ready: 'Ready', draining: 'Draining', released: 'Released', externally_managed: 'Externally managed' });
const ADMISSION_LABELS = Object.freeze({ not_admitted: 'Not admitted', bounded_trial: 'Bounded trial / evaluation',
    production: 'Production admitted' });
// Den's own stage reasons (den-live enrichment_link, the worker's deferrals) in plain words.
const REASON_LABELS = Object.freeze({ worker_unreachable: 'Worker unreachable', worker_unavailable: 'Worker unavailable',
    optional_worker_failure: 'Optional worker failed', submission_unproven: 'Job submission unconfirmed',
    qwen_busy: 'Deferred: Qwen busy', stage_observation_stale: 'Stage report stale', not_resident: 'Model not resident',
    resource_lease_fault: 'Resource lease fault', control_state_unavailable: 'Control state unavailable',
    admission_or_driver_unavailable: 'Admission or GPU driver unavailable', awaiting_resource_observation: 'Awaiting resource check',
    host_memory_headroom: 'Deferred: host memory low', gpu_headroom: 'Deferred: GPU memory low', gpu_execution_busy: 'Deferred: GPU busy',
    crop_coverage_incomplete: 'Person crop incomplete', frame_store_over_bound: 'Frame store full' });
const REASONS = new Set(['planned', 'evaluating', 'disabled', 'not_admitted', 'not_configured',
    'not_provisioned', 'not_loaded', 'preparing', 'loading', 'warming', 'idle', 'no_work', 'ready',
    'model_missing', 'profile_missing', 'gpu_unavailable', 'cpu_fallback', 'timeout', 'worker_unavailable',
    'stale', 'stage_observation_stale', 'unavailable', 'error', 'busy', 'queued', 'unknown', ...Object.keys(REASON_LABELS)]);
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

export function denModelReasonLabel(stage) {
    const reason = own(stage, 'reason');
    if (typeof reason !== 'string' || !REASONS.has(reason)) return 'Unknown';
    if (Object.hasOwn(REASON_LABELS, reason)) return REASON_LABELS[reason];
    const words = reason.replace(/_/g, ' ');
    return words[0].toUpperCase() + words.slice(1);
}

const SHA256_K = [0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2];

/* Den's intent.generation: SHA-256 of the compact JSON [service_run_id, camera_epoch]. Printable ASCII only,
 * where JSON.stringify and Python's json.dumps agree byte for byte; anything else has no generation. */
export function denSourceGeneration(serviceRunId, cameraEpoch) {
    if (typeof serviceRunId !== 'string' || !/^[\x20-\x7e]{1,128}$/.test(serviceRunId)
        || !Number.isSafeInteger(cameraEpoch) || cameraEpoch < 0) return null;
    const bytes = Array.from(JSON.stringify([serviceRunId, cameraEpoch]), c => c.charCodeAt(0)), bits = bytes.length * 8;
    bytes.push(0x80);
    while (bytes.length % 64 !== 56) bytes.push(0);
    bytes.push(0, 0, 0, 0, bits >>> 24 & 255, bits >>> 16 & 255, bits >>> 8 & 255, bits & 255);
    const h = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
    const rotr = (x, n) => x >>> n | x << 32 - n, w = new Array(64);
    for (let at = 0; at < bytes.length; at += 64) {
        for (let i = 0; i < 64; i++) {
            const a = w[i - 15], b = w[i - 2];
            w[i] = i < 16 ? bytes[at + 4 * i] << 24 | bytes[at + 4 * i + 1] << 16 | bytes[at + 4 * i + 2] << 8 | bytes[at + 4 * i + 3]
                : w[i - 16] + (rotr(a, 7) ^ rotr(a, 18) ^ a >>> 3) + w[i - 7] + (rotr(b, 17) ^ rotr(b, 19) ^ b >>> 10) | 0;
        }
        let [a, b, c, d, e, f, g, z] = h;
        for (let i = 0; i < 64; i++) {
            const t1 = z + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + (e & f ^ ~e & g) + SHA256_K[i] + w[i] | 0;
            const t2 = (rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + (a & b ^ a & c ^ b & c) | 0;
            z = g; g = f; f = e; e = d + t1 | 0; d = c; c = b; b = a; a = t1 + t2 | 0;
        }
        [a, b, c, d, e, f, g, z].forEach((x, i) => { h[i] = h[i] + x | 0; });
    }
    return h.map(x => (x >>> 0).toString(16).padStart(8, '0')).join('');
}

const INTENT_CHOICES = new Set(['continue', 'stop', 'turn_left', 'turn_right', 'unclear']);
const HINT_DISPOSITIONS = new Set(['advisory_only', 'ignored', 'expired']);
const HINT_DEPENDENCIES = ['camera_id', 'service_run_id', 'camera_epoch', 'source_generation', 'track_id',
    'association_revision', 'transform_id', 'scan_revision', 'calibration_revision'];

/* model_projection's public den.intent-hint/v1 row. Raw option scores, prompts and request identities are dropped. */
function intentHint(value) {
    const deps = own(value, 'dependencies'), revision = own(value, 'model_revision');
    const observed = own(value, 'observed_at'), produced = own(value, 'produced_at'), until = own(value, 'expires_at');
    if (own(value, 'schema') !== 'den.intent-hint/v1' || own(value, 'clock') !== 'producer_monotonic'
        || own(value, 'uncertainty') !== 'uncalibrated_semantic_hint' || own(value, 'authority') !== 'semantic_prior_only'
        || own(value, 'consumed') !== false || !INTENT_CHOICES.has(own(value, 'intent'))
        || !HINT_DISPOSITIONS.has(own(value, 'disposition'))
        || typeof revision !== 'string' || !/^[0-9a-f]{40}(?:[0-9a-f]{24})?$/.test(revision)
        || !object(deps) || !HINT_DEPENDENCIES.every(key => typeof own(deps, key) === 'string' && deps[key].length > 0 && deps[key].length <= 128)
        || ![observed, produced, until].every(seconds) || !(observed <= produced && produced < until && until <= observed + 10)) return null;
    return { intent: value.intent, model_revision: revision, disposition: value.disposition, reason: token(own(value, 'reason')),
        observed_at: observed, produced_at: produced, expires_at: until,
        dependencies: Object.fromEntries(HINT_DEPENDENCIES.map(key => [key, deps[key]])) };
}

/* Every dependency must match this same people event's frame and track, and Den must not have declined it. */
function hintBinding(hint, event) {
    const deps = hint.dependencies, frame = own(event, 'frame');
    const run = own(event, 'service_run_id'), epoch = own(event, 'camera_epoch');
    if (!object(frame) || !Number.isSafeInteger(epoch) || own(frame, 'service_run_id') !== run
        || own(frame, 'camera_epoch') !== epoch) return 'no_frame';
    if (deps.service_run_id !== run || deps.camera_epoch !== String(epoch)
        || deps.source_generation !== denSourceGeneration(run, epoch)) return 'source';
    if (deps.transform_id !== own(frame, 'transform_id') || deps.scan_revision !== own(frame, 'scan_revision')
        || deps.calibration_revision !== own(frame, 'depth_calibration_sha256')
        || !seconds(own(frame, 'capture_mono')) || hint.observed_at > frame.capture_mono) return 'frame';
    const tracks = own(event, 'tracks'), track = Array.isArray(tracks) ? tracks.find(row => own(row, 'track_id') === deps.track_id) : null;
    if (!track || own(track, 'service_run_id') !== run || own(track, 'camera_epoch') !== epoch
        || own(track, 'state') !== 'confirmed' || own(track, 'association_status') !== 'confident') return 'track';
    const association = own(track, 'association_revision');
    if (!Number.isSafeInteger(association) || String(association) !== deps.association_revision) return 'association';
    return hint.disposition === 'ignored' ? 'declined' : 'bound';
}

/* event.intent_hints: null when Den projected none. The newest valid hint is judged against the event that
 * carried it; its age and expiry stay in the producer clock and are unknown without this connection's anchor. */
export function denIntentHintView(event, { clock = null, receivedAt, now = receivedAt } = {}) {
    if (!object(event) || !Object.hasOwn(event, 'intent_hints')) return null;
    const list = Array.isArray(event.intent_hints) && event.intent_hints.length <= DEN_MODEL_RUNTIME_LIMITS.hints ? event.intent_hints : null;
    const valid = (list ?? []).map(intentHint).filter(Boolean);
    const rejected = list ? list.length - valid.length : 1;
    const hint = valid.reduce((newest, row) => !newest || row.produced_at >= newest.produced_at ? row : newest, null);
    if (!hint) return { hint: null, rejected };
    const atReceive = denSourceClockInterval(clock, receivedAt);
    const atNow = atReceive && browserTime(now) && now >= receivedAt ? denSourceClockInterval(clock, now) : null;
    let freshness = hint.disposition === 'expired' ? 'expired' : 'unknown', age = null, left = null;
    if (atNow && hint.produced_at <= atReceive.upper_mono && hint.produced_at <= atNow.lower_mono) {
        age = atNow.midpoint_mono - hint.produced_at;
        if (atNow.lower_mono >= hint.expires_at) freshness = 'expired';
        else if (freshness !== 'expired' && atNow.upper_mono < hint.expires_at) {
            freshness = 'fresh'; left = hint.expires_at - atNow.upper_mono;
        }
    }
    return { hint: { ...hint, binding: hintBinding(hint, event), freshness, age_s: age, expires_in_s: left }, rejected };
}
