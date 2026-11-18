/* Read-only room annotations. Room XY is already registered: x/y stay x/y and z is up.
 * No camera transform, motion estimator, semantic route selection or lighting control lives here.
 * Callers supply current source receipts and a connection-owned producer clock. */
import * as THREE from 'three';
import { denSourceClockInterval } from './home-den-model-runtime.mjs';

export const DEN_FORECAST_LIMITS = Object.freeze({ tracks: 16, samples: 4, beams: 16,
    observations: 64, identifier: 128, coordinate_m: 100, envelope_m: 20, ring_segments: 32, retired_beams: 64 });
const BINDING = ['service_run_id', 'camera_epoch', 'frame_id', 'track_id', 'association_revision',
    'transform_id', 'scan_revision', 'depth_calibration_sha256'];
const SCOPE = ['service_run_id', 'camera_epoch', 'transform_id', 'scan_revision', 'depth_calibration_sha256'];
const MOTION_FIELDS = [...BINDING, 'observed_mono', 'available_mono', 'valid_until_mono', 'xy_m', 'velocity_mps',
    'covariance', 'sample_count', 'history_span_s', 'white_accel_sigma_mps32', 'registration_error_m',
    'timing_error_s', 'source_obs_ids', 'basis', 'time_status', 'time_qualified', 'timing_evidence_id'];
const RECEIPT_FIELDS = [...BINDING, 'observed_mono', 'available_mono', 'valid_until_mono', 'velocity_mps',
    'source_obs_ids', 'basis', 'time_status', 'time_qualified', 'timing_evidence_id'];
const TIMING_FIELDS = ['cadence_wait_s', 'command_bound_s', 'current_motion_wait_s', 'fade_to_useful_s',
    'forecast_horizon_s', 'motor_s', 'observation_age_s', 'queue_bound_s', 'settle_s', 'solve_residual_s', 'transport_bound_s'];
const finite = x => typeof x === 'number' && Number.isFinite(x);
const number = (x, max = 1e12) => finite(x) && x >= 0 && x <= max;
const counter = x => Number.isSafeInteger(x) && x >= 0;
const object = x => x !== null && typeof x === 'object' && !Array.isArray(x);
const own = (x, k) => object(x) && Object.hasOwn(x, k) ? x[k] : undefined;
const id = x => typeof x === 'string' && x.length > 0 && x.length <= DEN_FORECAST_LIMITS.identifier;
const vector = (x, n, max = DEN_FORECAST_LIMITS.coordinate_m) => Array.isArray(x) && x.length === n
    && x.every(v => finite(v) && Math.abs(v) <= max);
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const select = (x, keys) => Object.fromEntries(keys.map(k => [k, own(x, k)]));
const clone = x => JSON.parse(JSON.stringify(x));
const near = (a, b) => finite(a) && finite(b) && Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(a), Math.abs(b));

function binding(x) {
    return object(x) && BINDING.every(k => ['camera_epoch', 'frame_id', 'association_revision'].includes(k)
        ? counter(own(x, k)) : id(own(x, k)));
}
function observations(x) {
    return Array.isArray(x) && x.length > 0 && x.length <= DEN_FORECAST_LIMITS.observations
        && x.every(id) && new Set(x).size === x.length;
}
function covariance(x, n) {
    if (!Array.isArray(x) || x.length !== n || !x.every(r => vector(r, n, 1e4))) return false;
    const sd = x.map((r, i) => r[i] >= 0 ? Math.sqrt(r[i]) : NaN);
    if (!sd.every(finite)) return false;
    const corr = x.map((r, i) => r.map((v, j) => sd[i] === 0 || sd[j] === 0 ? (v === 0 ? 0 : NaN) : v / sd[i] / sd[j]));
    if (!corr.every((r, i) => r.every((v, j) => finite(v) && Math.abs(v) <= 1 + 1e-9 && near(v, corr[j][i])))) return false;
    const lower = Array.from({ length: n }, () => Array(n).fill(0));
    for (let i = 0; i < n; i++) {
        const pivot = corr[i][i] - lower[i].reduce((s, v) => s + v * v, 0);
        if (pivot < -1e-9) return false;
        lower[i][i] = Math.sqrt(Math.max(0, pivot));
        for (let j = i + 1; j < n; j++) {
            const residual = corr[j][i] - lower[j].slice(0, i).reduce((s, v, k) => s + v * lower[i][k], 0);
            if (lower[i][i] > Math.sqrt(1e-9)) lower[j][i] = residual / lower[i][i];
            else if (Math.abs(residual) > 1e-9) return false;
        }
    }
    return true;
}
function sigmaOf(cov) {
    return Math.sqrt(Math.max(0, (cov[0][0] + cov[1][1] + Math.hypot(cov[0][0] - cov[1][1], 2 * cov[0][1])) / 2));
}
function receipt(x) {
    return binding(x) && own(x, 'basis') === 'den_track_kalman_cv'
        && own(x, 'time_status') === 'estimated_source_elapsed' && own(x, 'time_qualified') === false
        && (own(x, 'timing_evidence_id') === null || id(own(x, 'timing_evidence_id')))
        && ['observed_mono', 'available_mono', 'valid_until_mono'].every(k => number(own(x, k)))
        && x.observed_mono <= x.available_mono && x.available_mono < x.valid_until_mono
        && x.valid_until_mono <= x.observed_mono + 1.5 + 1e-9
        && vector(x.velocity_mps, 2) && observations(x.source_obs_ids);
}
function motion(x) {
    return receipt(x) && vector(x.xy_m, 2) && covariance(x.covariance, 4)
        && counter(x.sample_count) && x.sample_count >= 3 && number(x.history_span_s, 2) && x.history_span_s >= .4 - 1e-9
        && ['white_accel_sigma_mps32', 'registration_error_m', 'timing_error_s'].every(k => number(x[k], 100))
        && x.valid_until_mono <= x.observed_mono + 1.5 - x.timing_error_s + 1e-9;
}
function scopeMatches(state, scope) {
    return object(scope) && id(scope.home) && SCOPE.every(k => own(scope, k) === own(state, k));
}
function currentMotion(track, frame, scope) {
    const state = own(track, 'motion');
    if (!motion(state) || own(track, 'state') !== 'confirmed' || own(track, 'association_status') !== 'confident'
        || !observations(own(track, 'current_obs_ids')) || !scopeMatches(state, scope)
        || !['track_id', 'service_run_id', 'camera_epoch', 'association_revision'].every(k => own(track, k) === state[k])
        || !SCOPE.concat('frame_id').every(k => own(frame, k) === state[k])
        || own(frame, 'capture_mono') !== state.observed_mono || !number(own(frame, 'arrival_mono'))
        || frame.arrival_mono > state.available_mono
        || !same(track.current_obs_ids, state.source_obs_ids)) return null;
    return state;
}
function intervals(clock, receivedAt, now) {
    if (!finite(receivedAt) || !finite(now) || now < receivedAt) return null;
    const received = denSourceClockInterval(clock, receivedAt), current = denSourceClockInterval(clock, now);
    return received && current ? { received, current } : null;
}
function liveTimes(times, observed, until) {
    return times && observed <= times.received.upper_mono && observed <= times.current.lower_mono
        && times.current.upper_mono < until;
}

/* Canonical den-spatial-forecast-1 only; the current motion/frame are independent caller-supplied receipts. */
export function admitDenMotionForecast(forecast, { track, frame, scope, clock, receivedAt, now = receivedAt } = {}) {
    const times = intervals(clock, receivedAt, now), state = currentMotion(track, frame, scope);
    if (!state || !object(forecast) || forecast.schema !== 'den-spatial-forecast-1'
        || forecast.coordinate_space !== 'room_floor_xy_m' || forecast.time_domain !== 'producer_monotonic'
        || forecast.uncertainty_kind !== 'conservative_envelope' || !same(forecast.covariance_order, ['x', 'y', 'vx', 'vy'])
        || forecast.shadow_only !== true || forecast.virtual_only !== true
        || !['physical', 'walkable', 'beam', 'delay', 'aim', 'occupancy'].every(k => forecast[`${k}_qualified`] === false)
        || !same(select(forecast.state, MOTION_FIELDS), select(state, MOTION_FIELDS))
        || !number(forecast.issued_mono) || !number(forecast.qualified_at_mono)
        || !number(forecast.remaining_freshness_s, 1.5)
        || !(state.available_mono <= forecast.issued_mono && forecast.issued_mono <= forecast.qualified_at_mono)
        || forecast.valid_until_mono !== state.valid_until_mono
        || !near(forecast.remaining_freshness_s, forecast.valid_until_mono - forecast.qualified_at_mono)
        || !liveTimes(times, forecast.qualified_at_mono, forecast.valid_until_mono)
        || !Array.isArray(forecast.samples) || !forecast.samples.length || forecast.samples.length > DEN_FORECAST_LIMITS.samples) return null;
    let previous = -1;
    const samples = [];
    for (const sample of forecast.samples) {
        if (!object(sample) || !number(sample.horizon_s, 2) || sample.horizon_s <= previous
            || sample.at_mono !== forecast.issued_mono + sample.horizon_s
            || !vector(sample.xy_m, 2) || !covariance(sample.covariance_m2, 2)
            || !number(sample.sigma_m, DEN_FORECAST_LIMITS.envelope_m) || !near(sample.sigma_m, sigmaOf(sample.covariance_m2))) return null;
        samples.push(clone(select(sample, ['horizon_s', 'at_mono', 'xy_m', 'covariance_m2', 'sigma_m'])));
        previous = sample.horizon_s;
    }
    return { track_id: state.track_id, binding: select(state, BINDING), xy_m: [...state.xy_m],
        issued_mono: forecast.issued_mono, valid_until_mono: forecast.valid_until_mono, samples,
        time_status: state.time_status, time_qualified: false, uncertainty_kind: 'conservative_envelope',
        calibrated_probability: false, label: 'Estimated motion · uncertainty envelope' };
}

/* Read only explicit virtual_beams. Proposals never supply annotations. Selected source and newer
 * cadence validation receipts are distinct, and neither a new snapshot nor receipt renews expiry. */
export function admitDenVirtualBeams(path, { snapshot, tracks, frame, scope, clock, receivedAt, peopleReceivedAt = receivedAt, now = receivedAt,
    heldFixtureIds = [], stopped = false } = {}) {
    const times = intervals(clock, receivedAt, now);
    if (!times || stopped !== false || !Array.isArray(heldFixtureIds) || heldFixtureIds.length > 128 || !heldFixtureIds.every(id)
        || !object(snapshot) || snapshot.schema !== 'den-virtual-spots-1' || snapshot.home !== scope?.home
        || snapshot.on !== true || snapshot.stopped !== false || snapshot.binding?.transform_id !== scope?.transform_id
        || snapshot.binding?.scan_revision !== scope?.scan_revision
        || scope?.site != null && snapshot.site !== scope.site
        || !object(path) || path.schema !== 'den-path-lighting-1' || path.mode !== 'virtual'
        || !['eligible', 'cadence'].includes(path.status) || !number(path.evaluated_mono)
        || !number(path.valid_until_mono) || path.original_valid_until_mono !== path.valid_until_mono
        || !liveTimes(times, path.evaluated_mono, path.valid_until_mono)
        || !Array.isArray(path.virtual_beams) || path.virtual_beams.length > DEN_FORECAST_LIMITS.beams
        || !Array.isArray(tracks) || tracks.length > DEN_FORECAST_LIMITS.tracks) return [];
    const selected = path.evidence?.motion;
    if (!receipt(selected) || selected.disposition !== 'consumed' || selected.selected !== true
        || !binding(path.subject) || !same(select(path.subject, BINDING), select(selected, BINDING))
        || !scopeMatches(selected, scope) || path.valid_until_mono > selected.valid_until_mono
        || selected.available_mono > path.evaluated_mono) return [];
    const candidates = tracks.filter(t => t?.track_id === selected.track_id);
    if (candidates.length !== 1) return [];
    const current = currentMotion(candidates[0], frame, scope);
    const currentReceiptClock = denSourceClockInterval(clock, peopleReceivedAt);
    if (!current || current.available_mono > path.evaluated_mono
        || !currentReceiptClock || current.available_mono > currentReceiptClock.upper_mono
        || selected.frame_id > current.frame_id || selected.observed_mono > current.observed_mono
        || current.valid_until_mono < path.valid_until_mono || !liveTimes(times, current.available_mono, current.valid_until_mono)
        || !BINDING.filter(k => k !== 'frame_id').every(k => selected[k] === current[k])) return [];
    const validation = path.validation_source;
    if (!same(select(selected, RECEIPT_FIELDS), select(current, RECEIPT_FIELDS)) || validation != null) {
        if (!receipt(validation) || validation.disposition !== 'consumed' || validation.selected !== false
            || validation.reason !== 'cadence_safety_validation'
            || !same(select(validation, RECEIPT_FIELDS), select(current, RECEIPT_FIELDS))) return [];
    }
    const beams = [];
    for (const beam of path.virtual_beams) {
        const footprint = beam?.footprint, u = beam?.uncertainty, timing = beam?.timing;
        if (!object(beam) || !id(beam.fixture_id) || !id(beam.branch) || beam.authority !== 'virtual_only'
            || beam.precision_qualified !== false || !vector(beam.target_m, 3)
            || !id(beam.profile_id) || !id(beam.floor_profile_id)
            || !same(select(beam.source_motion, RECEIPT_FIELDS), select(selected, RECEIPT_FIELDS))
            || beam.valid_until_mono !== path.valid_until_mono || !number(beam.start_mono) || !number(beam.arrival_mono)
            || !(selected.available_mono <= beam.start_mono && beam.start_mono <= beam.arrival_mono && beam.arrival_mono < beam.valid_until_mono)
            || !object(footprint) || footprint.basis !== 'virtual_fwhm_floor_projection'
            || !number(footprint.radius_m, DEN_FORECAST_LIMITS.envelope_m) || footprint.radius_m === 0
            || !number(footprint.inner_radius_m, footprint.radius_m) || !near(beam.radius_m, footprint.radius_m)
            || !number(footprint.heading_lead_m, DEN_FORECAST_LIMITS.envelope_m)
            || !object(timing) || timing.basis !== 'virtual_profile' || !TIMING_FIELDS.every(k => number(timing[k], 60))
            || timing.forecast_horizon_s > 2 || timing.observation_age_s > 1.5
            || !object(u) || u.calibrated_probability !== false || !covariance(u.covariance_m2, 2)
            || !number(u.sigma_m, DEN_FORECAST_LIMITS.envelope_m) || !near(u.sigma_m, sigmaOf(u.covariance_m2))
            || !number(u.sigma_multiplier, 10) || u.sigma_multiplier < 1
            || !number(u.envelope_m, DEN_FORECAST_LIMITS.envelope_m) || u.envelope_m + 1e-9 < u.sigma_m * u.sigma_multiplier) return [];
        if (!heldFixtureIds.includes(beam.fixture_id)) beams.push({ fixture_id: beam.fixture_id, branch: beam.branch,
            target_m: [...beam.target_m], radius_m: beam.radius_m, inner_radius_m: footprint.inner_radius_m,
            envelope_m: u.envelope_m, valid_until_mono: beam.valid_until_mono, arrival_mono: beam.arrival_mono,
            source_motion: clone(select(selected, RECEIPT_FIELDS)),
            time_status: selected.time_status, time_qualified: false, precision_qualified: false,
            authority: 'virtual_only', label: 'Virtual beam · estimated timing' });
    }
    return beams;
}

function release(root) {
    root.traverse(node => { node.geometry?.dispose(); node.material?.dispose(); });
    root.clear();
}
function unpickable(node) {
    node.raycast = () => {};
    node.userData.noLightingAuthority = true;
    node.userData.pickable = false;
    return node;
}
function loop(xy, cov, z, color) {
    const a = cov[0][0], b = cov[0][1], d = cov[1][1], angle = Math.atan2(2 * b, a - d) / 2;
    const delta = Math.hypot(a - d, 2 * b), major = Math.sqrt(Math.max(0, (a + d + delta) / 2));
    const minor = Math.sqrt(Math.max(0, (a + d - delta) / 2)), points = [];
    for (let i = 0; i < DEN_FORECAST_LIMITS.ring_segments; i++) {
        const t = i / DEN_FORECAST_LIMITS.ring_segments * Math.PI * 2, x = major * Math.cos(t), y = minor * Math.sin(t);
        points.push(new THREE.Vector3(xy[0] + x * Math.cos(angle) - y * Math.sin(angle),
            xy[1] + x * Math.sin(angle) + y * Math.cos(angle), z));
    }
    return unpickable(new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(points),
        new THREE.LineBasicMaterial({ color, transparent: true, opacity: .42, depthWrite: false })));
}

export function attachDenForecastLayer(engine, { getScope, getClock, getHeldFixtureIds = () => [], isStopped = () => false,
    isCurrent = () => true, now = () => performance.now(), animate = true } = {}) {
    const parent = engine?.apartmentRoot;
    if (!parent?.add || typeof getScope !== 'function' || typeof getClock !== 'function') throw Error('den_forecast_dependencies_required');
    const root = unpickable(new THREE.Group());
    root.name = 'home-den-forecast-owned';
    parent.add(root);
    let disposed = false, input = null, inputClock = null, inputScope = null, key = '', raf = null;
    let shown = { forecasts: [], beams: [] };
    const retired = new Map();
    let revocationOverflowUntil = 0;
    const holdCutoffs = new Map();
    let stopCutoff = 0, previousHeld = [], previousSuppressed = false, controlScope = null;
    function bindControlScope(scope) {
        if (controlScope && !same(controlScope, scope)) {
            retired.clear(); holdCutoffs.clear(); stopCutoff = revocationOverflowUntil = 0;
            previousHeld = []; previousSuppressed = false;
        }
        controlScope = clone(scope);
    }
    function observeControls(held, stopped, interval) {
        const valid = Array.isArray(held) && held.length <= 128 && held.every(id), suppressed = stopped !== false || !valid;
        if (!interval) {
            // Remember control transitions through a clock outage; the first valid bound fences their release.
            const pending = [...new Set([...previousHeld, ...(valid ? held : [])])];
            previousHeld = pending.slice(0, 128);
            previousSuppressed = previousSuppressed || suppressed || pending.length > 128;
            return;
        }
        if (suppressed || previousSuppressed) stopCutoff = Math.max(stopCutoff, interval.upper_mono);
        for (const fixture of new Set([...previousHeld, ...(valid ? held : [])])) {
            if (holdCutoffs.has(fixture) || holdCutoffs.size < 128) holdCutoffs.set(fixture, interval.upper_mono);
            else stopCutoff = Math.max(stopCutoff, interval.upper_mono);
        }
        previousHeld = valid ? [...held] : [];
        previousSuppressed = suppressed;
    }
    const beamKey = beam => JSON.stringify([beam.fixture_id, ...BINDING.map(k => beam.source_motion[k])]);
    function suppress(held, stopped) {
        if (!input) return;
        const validHolds = Array.isArray(held) && held.length <= 128 && held.every(id);
        input.beams = input.beams.filter(beam => {
            if (stopped === false && validHolds && !held.includes(beam.fixture_id)) return true;
            const key = beamKey(beam);
            if (retired.size >= DEN_FORECAST_LIMITS.retired_beams && !retired.has(key))
                revocationOverflowUntil = Math.max(revocationOverflowUntil, beam.source_motion.valid_until_mono);
            else retired.set(key, beam.source_motion.valid_until_mono);
            return false;
        });
    }
    const clear = () => { release(root); shown = { forecasts: [], beams: [] }; key = ''; input = null; };
    function tick() {
        if (disposed) return;
        try {
            const scope = getScope(), clock = getClock();
            const at = now(), interval = denSourceClockInterval(clock, at);
            if (!isCurrent()) { clear(); return; }
            bindControlScope(scope);
            const held = getHeldFixtureIds(), stopped = isStopped();
            observeControls(held, stopped, interval);
            if (!input || clock !== inputClock || !same(scope, inputScope) || !interval || at < input.receivedAt) { clear(); return; }
            for (const [key, expiry] of retired) if (interval.lower_mono >= expiry) retired.delete(key);
            const forecasts = input.forecasts.filter(f => interval.upper_mono < f.valid_until_mono);
            suppress(held, stopped);
            const beams = stopped === false && Array.isArray(held) && held.length <= 128 && held.every(id)
                && interval.lower_mono >= revocationOverflowUntil
                ? input.beams.filter(b => interval.upper_mono < b.valid_until_mono && !retired.has(beamKey(b))
                    && b.source_motion.observed_mono >= Math.max(stopCutoff, holdCutoffs.get(b.fixture_id) || 0)) : [];
            const next = { forecasts, beams }, nextKey = JSON.stringify(next);
            if (key === nextKey) return;
            release(root); shown = next; key = nextKey;
            for (const forecast of forecasts) {
                const group = unpickable(new THREE.Group());
                group.userData.annotation = clone(forecast);
                const points = [forecast.xy_m, ...forecast.samples.map(s => s.xy_m)].map(p => new THREE.Vector3(p[0], p[1], .025));
                group.add(unpickable(new THREE.Line(new THREE.BufferGeometry().setFromPoints(points),
                    new THREE.LineBasicMaterial({ color: 0xb8d8ff, transparent: true, opacity: .6, depthWrite: false }))));
                for (const sample of forecast.samples) group.add(loop(sample.xy_m, sample.covariance_m2, .028, 0xb8d8ff));
                root.add(group);
            }
            for (const beam of beams) {
                const group = unpickable(new THREE.Group());
                group.userData.annotation = clone(beam);
                const patch = unpickable(new THREE.Mesh(new THREE.RingGeometry(beam.inner_radius_m, beam.radius_m,
                    DEN_FORECAST_LIMITS.ring_segments), new THREE.MeshBasicMaterial({ color: 0xffd9a0,
                    transparent: true, opacity: .35, depthWrite: false, side: THREE.DoubleSide })));
                patch.position.set(...beam.target_m); patch.position.z += .018;
                group.add(patch, loop(beam.target_m, [[beam.envelope_m ** 2, 0], [0, beam.envelope_m ** 2]],
                    beam.target_m[2] + .022, 0xffd9a0));
                root.add(group);
            }
        } catch (_) { clear(); }
    }
    const frame = () => { if (!disposed) { tick(); raf = requestAnimationFrame(frame); } };
    if (animate && typeof requestAnimationFrame === 'function') raf = requestAnimationFrame(frame);
    return { root, tick, clear, reconnect: clear,
        update(value) {
            if (disposed) return false;
            try {
                bindControlScope(getScope());
                const held = getHeldFixtureIds(), stopped = isStopped();
                observeControls(held, stopped, denSourceClockInterval(getClock(), now()));
                suppress(held, stopped);
            }
            catch (_) { clear(); return false; }
            clear();
            if (!object(value) || !Array.isArray(value.tracks) || value.tracks.length > DEN_FORECAST_LIMITS.tracks) return false;
            try {
                const nextScope = clone(getScope());
                bindControlScope(nextScope);
                inputClock = getClock(); inputScope = nextScope;
                // Separate original callbacks prevent a cached packet from borrowing a later packet's clock bound.
                const peopleReceivedAt = Object.hasOwn(value, 'peopleReceivedAt') ? value.peopleReceivedAt : value.receivedAt;
                const spotReceivedAt = Object.hasOwn(value, 'spotReceivedAt') ? value.spotReceivedAt : value.receivedAt;
                const context = { scope: inputScope, clock: inputClock, receivedAt: peopleReceivedAt, now: now(), frame: value.frame };
                const ids = new Set(), duplicate = new Set();
                for (const track of value.tracks) { if (ids.has(track?.track_id)) duplicate.add(track.track_id); ids.add(track?.track_id); }
                // Store only admitted, detached projections; arbitrary input metadata is never cloned or retained.
                const forecasts = value.tracks.filter(t => !duplicate.has(t?.track_id)).map(track =>
                    admitDenMotionForecast(track?.motion_forecast, { ...context, track })).filter(Boolean);
                // Parse candidates before applying local controls, so receipts seen during a local hold
                // are retired too. The containing snapshot's own on/Stop contract is always enforced.
                const beams = admitDenVirtualBeams(value.spot?.path_lighting, { ...context, receivedAt: spotReceivedAt, peopleReceivedAt,
                    snapshot: value.spot,
                    tracks: value.tracks, heldFixtureIds: [], stopped: false });
                input = { forecasts, beams, receivedAt: Math.max(...[peopleReceivedAt, spotReceivedAt].filter(finite)) };
                tick(); return true;
            }
            catch (_) { clear(); return false; }
        },
        snapshot: () => clone(shown),
        dispose() { if (disposed) return; disposed = true; if (raf !== null) cancelAnimationFrame(raf); clear(); parent.remove(root); },
    };
}
