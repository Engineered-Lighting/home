import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { createDenSourceClock } from '../app/src/home-den-model-runtime.mjs';
import { admitDenMotionForecast, admitDenVirtualBeams, attachDenForecastLayer, DEN_FORECAST_LIMITS as LIMITS }
    from '../app/src/home-den-forecast-layer.mjs';
import { modelPipelineEvidence } from './fixtures/den-model-pipeline.mjs';

const copy = x => JSON.parse(JSON.stringify(x));
const bindingKeys = ['service_run_id', 'camera_epoch', 'frame_id', 'track_id', 'association_revision',
    'transform_id', 'scan_revision', 'depth_calibration_sha256'];
const picked = (x, keys) => Object.fromEntries(keys.map(k => [k, x[k]]));
const receipt = state => picked(state, [...bindingKeys, 'available_mono', 'basis', 'observed_mono',
    'source_obs_ids', 'time_qualified', 'time_status', 'timing_evidence_id', 'valid_until_mono', 'velocity_mps']);

function fixture() {
    // Motion source and beam values come from the walking owner's authored path-lighting-virtual.json
    // and test_walking.state fixture. These are synthetic virtual profiles, not physical measurements.
    const state = { service_run_id: 'walk-run', camera_epoch: 2, frame_id: 10, track_id: 'walker',
        association_revision: 3, transform_id: 'walk-transform', scan_revision: 'walk-scan',
        depth_calibration_sha256: 'c'.repeat(64), observed_mono: 100, available_mono: 100.05,
        valid_until_mono: 101.5, xy_m: [0, 0], velocity_mps: [.5, 0],
        covariance: [[.0001, 0, 0, 0], [0, .0001, 0, 0], [0, 0, .0001, 0], [0, 0, 0, .0001]],
        sample_count: 4, history_span_s: .6, white_accel_sigma_mps32: 0,
        registration_error_m: 0, timing_error_s: 0, source_obs_ids: ['walk-obs-10'],
        basis: 'den_track_kalman_cv', time_status: 'estimated_source_elapsed', time_qualified: false, timing_evidence_id: null };
    const forecast = { schema: 'den-spatial-forecast-1', state: copy(state), issued_mono: 100.2, valid_until_mono: 101.5,
        samples: [.2, .5, 1, 2].map(horizon_s => {
            const at_mono = 100.2 + horizon_s, dt = at_mono - state.observed_mono, variance = .0001 * (1 + dt ** 2);
            return { horizon_s, at_mono, xy_m: [.5 * dt, 0], covariance_m2: [[variance, 0], [0, variance]], sigma_m: Math.sqrt(variance) };
        }), coordinate_space: 'room_floor_xy_m', time_domain: 'producer_monotonic', covariance_order: ['x', 'y', 'vx', 'vy'],
        uncertainty_kind: 'conservative_envelope', qualified_at_mono: 100.2, remaining_freshness_s: 1.3,
        shadow_only: true, virtual_only: true, physical_qualified: false, walkable_qualified: false,
        beam_qualified: false, delay_qualified: false, aim_qualified: false, occupancy_qualified: false };
    const track = { track_id: 'walker', service_run_id: 'walk-run', camera_epoch: 2, association_revision: 3,
        state: 'confirmed', association_status: 'confident', current_obs_ids: ['walk-obs-10'], motion: state, motion_forecast: forecast };
    const frame = { ...picked(state, bindingKeys.filter(k => !['track_id', 'association_revision'].includes(k))),
        capture_mono: 100, arrival_mono: 100.02 };
    const scope = { home: 'den', site: 'victoria', ...picked(state, ['service_run_id', 'camera_epoch', 'transform_id',
        'scan_revision', 'depth_calibration_sha256']) };
    const beam = { fixture_id: 'F3', authority: 'virtual_only', branch: 'geometric_velocity',
        floor_profile_id: 'synthetic-floor', profile_id: 'synthetic-fast-profile', precision_qualified: false,
        target_m: [0.49013346877207764, 0, 0], start_mono: 100.27, arrival_mono: 100.7811028208132,
        valid_until_mono: 101.5, source_motion: receipt(state), radius_m: 0.21624310993808307,
        footprint: { basis: 'virtual_fwhm_floor_projection', heading_lead_m: .1,
            inner_radius_m: 0.20317822768056382, radius_m: 0.21624310993808307 },
        timing: { basis: 'virtual_profile', cadence_wait_s: 0, command_bound_s: .02, current_motion_wait_s: 0,
            fade_to_useful_s: 0, forecast_horizon_s: 0.5811028208132001, motor_s: 0.47110282081320026,
            observation_age_s: 0.20000000000000284, queue_bound_s: .02, settle_s: .04,
            solve_residual_s: 0.0008358832690475992, transport_bound_s: .03 },
        uncertainty: { calibrated_probability: false,
            covariance_m2: [[0.0001608816493824535, 0], [0, 0.0001608816493824535]],
            envelope_m: 0.03846968068074981, sigma_m: 0.01268391301540867, sigma_multiplier: 3 } };
    const path = { schema: 'den-path-lighting-1', mode: 'virtual', status: 'eligible', reason: 'geometric_velocity',
        evaluated_mono: 100.2, original_valid_until_mono: 101.5, valid_until_mono: 101.5,
        subject: picked(state, bindingKeys), evidence: { motion: { ...receipt(state), disposition: 'consumed',
            selected: true, reason: 'geometric_velocity' }, intent: { disposition: 'absent', consumed: false } },
        proposals: [copy(beam)], virtual_beams: [beam] };
    const snapshot = { schema: 'den-virtual-spots-1', home: 'den', site: 'victoria', epoch: 'coordinator-epoch', seq: 1,
        on: true, stopped: false, binding: { transform_id: 'walk-transform', scan_revision: 'walk-scan' }, path_lighting: path };
    const clock = createDenSourceClock({ schema: 'den-source-clock-1', clock_domain: 'producer_monotonic', server_mono: 100.25 },
        { startedAt: 999, receivedAt: 1000 });
    return { state, forecast, track, frame, scope, snapshot, path, beam, clock, receivedAt: 1000, now: 1000 };
}
const context = f => ({ ...f, tracks: [f.track], snapshot: f.snapshot });
const admitted = f => admitDenMotionForecast(f.forecast, context(f));
const beams = f => admitDenVirtualBeams(f.path, context(f));

test('registered motion samples and covariance survive unchanged, independently of semantic hints', () => {
    const f = fixture(), result = admitted(f);
    assert.ok(result);
    assert.deepEqual(result.samples, f.forecast.samples);
    assert.equal(result.samples.at(-1).at_mono > result.valid_until_mono, true, 'future description does not extend evidence lease');
    assert.equal(result.time_qualified, false);
    assert.equal(result.calibrated_probability, false);
    assert.match(result.label, /Estimated/);
    f.track.intent_hint = { invalid: true };
    assert.deepEqual(admitted(f), result);
    result.samples[0].xy_m[0] = 99;
    assert.notEqual(f.forecast.samples[0].xy_m[0], 99);
});

test('motion requires exact current motion, frame, scope and person binding', () => {
    for (const key of bindingKeys) {
        const f = fixture();
        f.forecast.state[key] = typeof f.forecast.state[key] === 'number' ? 999 : 'other';
        assert.equal(admitted(f), null, key);
    }
    for (const key of ['service_run_id', 'camera_epoch', 'frame_id', 'transform_id', 'scan_revision', 'depth_calibration_sha256', 'capture_mono']) {
        const f = fixture(); f.frame[key] = 'wrong'; assert.equal(admitted(f), null, key);
    }
    const f = fixture(); f.track.motion = null; assert.equal(admitted(f), null);
    const g = fixture(); g.track.state = 'coasting'; assert.equal(admitted(g), null);
    const h = fixture(); h.scope.scan_revision = 'other'; assert.equal(admitted(h), null);
    const i = fixture(); i.track.current_obs_ids = ['old']; assert.equal(admitted(i), null);
    const j = fixture(); j.track.association_status = 'ambiguous'; assert.equal(admitted(j), null); assert.deepEqual(beams(j), []);
    const k = fixture(); delete k.track.current_obs_ids; assert.equal(admitted(k), null); assert.deepEqual(beams(k), []);
});

test('unknown or uncertain source clock, stale leases and future receipt times cannot draw', () => {
    const f = fixture(); f.clock = null; assert.equal(admitted(f), null); assert.deepEqual(beams(f), []);
    const g = fixture(); g.now = 2245; assert.equal(admitted(g), null); assert.deepEqual(beams(g), []);
    const h = fixture(); h.receivedAt = 998; assert.equal(admitted(h), null);
    const i = fixture(); i.clock = createDenSourceClock({ schema: 'den-source-clock-1', clock_domain: 'producer_monotonic', server_mono: 100.2 },
        { startedAt: 0, receivedAt: 1000 });
    assert.equal(admitted(i), null, 'interval may still precede issuance');
    const j = fixture(); j.forecast.qualified_at_mono = 100.4; j.forecast.remaining_freshness_s = 1.1;
    j.now = 1500; assert.equal(admitted(j), null, 'definitely future at receipt cannot become usable by waiting');
});

test('nonfinite, excessive, indefinite or inconsistent motion geometry is rejected', () => {
    for (const mutate of [f => f.forecast.samples.push(copy(f.forecast.samples[0])),
        f => f.forecast.samples[0].xy_m[0] = Infinity, f => f.forecast.samples[0].xy_m[0] = 101,
        f => f.forecast.samples[0].covariance_m2 = [[1, 2], [2, 1]],
        f => f.forecast.samples[0].sigma_m = .9, f => f.forecast.samples[0].horizon_s = 3,
        f => f.forecast.samples[0].at_mono++, f => f.forecast.state.covariance[0][0] = -1,
        f => f.forecast.physical_qualified = true, f => f.forecast.remaining_freshness_s = 9]) {
        const f = fixture(); mutate(f); assert.equal(admitted(f), null);
    }
});

test('authored explicit virtual beam is accepted; shadow proposals, off and malformed annotations stay hidden', () => {
    assert.equal(beams(fixture()).length, 1);
    for (const mutate of [f => f.path.mode = 'shadow', f => f.path.mode = 'off', f => f.path.status = 'blocked',
        f => f.path.virtual_beams = [], f => f.snapshot.stopped = true, f => f.snapshot.on = false,
        f => f.snapshot.binding.transform_id = 'other', f => f.path.evidence.motion.selected = false,
        f => f.path.evaluated_mono = 100,
        f => f.beam.authority = 'physical', f => f.beam.target_m[0] = NaN,
        f => f.beam.timing.motor_s = Infinity, f => f.beam.uncertainty.calibrated_probability = true,
        f => f.path.virtual_beams = Array.from({ length: LIMITS.beams + 1 }, () => copy(f.beam))]) {
        const f = fixture(); mutate(f); assert.deepEqual(beams(f), []);
    }
});

test('beam lease can be shorter than source lease and never renews on newer snapshots', () => {
    const f = fixture(); f.path.valid_until_mono = f.path.original_valid_until_mono = f.beam.valid_until_mono = 101.3;
    assert.equal(beams(f).length, 1);
    f.now = 2050;
    assert.deepEqual(beams(f), []);
    assert.ok(admitted(f), 'geometric forecast keeps its independent original lease');
    const g = fixture(); g.path.valid_until_mono = g.beam.valid_until_mono = 101.6;
    assert.deepEqual(beams(g), [], 'a newer snapshot cannot refresh original expiry');
});

test('cached beam source may precede current frame only with an exact separate validation receipt', () => {
    const f = fixture(), current = copy(f.state);
    Object.assign(current, { frame_id: 11, observed_mono: 100.1, available_mono: 100.15, valid_until_mono: 101.6,
        xy_m: [.05, 0], source_obs_ids: ['walk-obs-11'] });
    f.track.motion = current; f.track.current_obs_ids = ['walk-obs-11'];
    Object.assign(f.frame, { frame_id: 11, capture_mono: 100.1, arrival_mono: 100.12 });
    f.path.status = 'cadence';
    f.path.validation_source = { ...receipt(current), disposition: 'consumed', selected: false, reason: 'cadence_safety_validation' };
    assert.equal(beams(f).length, 1, 'selected frame10 and validation frame11 are distinct');
    assert.equal(beams(f)[0].valid_until_mono, 101.5);
    delete f.path.validation_source;
    assert.deepEqual(beams(f), []);
    f.path.validation_source = { ...receipt(current), disposition: 'consumed', selected: false, reason: 'cadence_safety_validation', frame_id: 12 };
    assert.deepEqual(beams(f), []);
    f.path.validation_source.frame_id = 11;
    f.path.evaluated_mono = 100.1;
    assert.deepEqual(beams(f), [], 'newer safety receipt must exist by the stated evaluation');
});

test('manual holds and Stop suppress beams; separate authored alternatives are not averaged', () => {
    const f = fixture();
    assert.deepEqual(admitDenVirtualBeams(f.path, { ...context(f), heldFixtureIds: ['F3'] }), []);
    assert.deepEqual(admitDenVirtualBeams(f.path, { ...context(f), stopped: true }), []);
    const alternative = copy(f.beam); alternative.fixture_id = 'F4'; alternative.branch = 'other_geometric_branch'; alternative.target_m[0] = -1;
    f.path.virtual_beams.push(alternative);
    assert.deepEqual(beams(f).map(b => b.target_m[0]), [f.beam.target_m[0], -1]);
});

test('Three annotations use existing room-floor axes, have no picking or light authority, and dispose resources', () => {
    const f = fixture(), parent = new THREE.Group(); parent.rotation.z = Math.PI / 2;
    const layer = attachDenForecastLayer({ apartmentRoot: parent }, { getScope: () => f.scope, getClock: () => f.clock, now: () => f.now, animate: false });
    assert.equal(layer.update({ tracks: [f.track], frame: f.frame, spot: f.snapshot, receivedAt: f.receivedAt }), true);
    assert.equal(layer.snapshot().forecasts.length, 1); assert.equal(layer.snapshot().beams.length, 1);
    const line = layer.root.children[0].children[0];
    assert.deepEqual(Array.from(line.geometry.attributes.position.array).slice(0, 3), [0, 0, Math.fround(.025)]);
    assert.equal(line.geometry.attributes.position.getX(1), Math.fround(f.forecast.samples[0].xy_m[0]));
    let objects = 0, disposed = 0;
    layer.root.traverse(node => {
        objects++; assert.equal(node.userData.noLightingAuthority, true); assert.equal(node.userData.pickable, false);
        const hits = []; node.raycast({}, hits); assert.deepEqual(hits, []); assert.ok(!node.isLight);
        node.geometry?.addEventListener('dispose', () => disposed++);
    });
    assert.ok(objects <= 1 + LIMITS.tracks * 6 + LIMITS.beams * 3);
    layer.dispose(); assert.ok(disposed > 0); assert.equal(parent.children.length, 0);
    layer.dispose(); assert.equal(layer.update({ tracks: [] }), false);
});

test('layer independently expires annotations and immediately responds to holds, scope, reconnect and unmount', () => {
    const f = fixture(), parent = new THREE.Group(); let held = [], stopped = false, current = true;
    f.path.valid_until_mono = f.path.original_valid_until_mono = f.beam.valid_until_mono = 101.3;
    const layer = attachDenForecastLayer({ apartmentRoot: parent }, { getScope: () => f.scope, getClock: () => f.clock,
        getHeldFixtureIds: () => held, isStopped: () => stopped, isCurrent: () => current, now: () => f.now, animate: false });
    const update = () => layer.update({ tracks: [f.track], frame: f.frame, spot: f.snapshot, receivedAt: f.receivedAt });
    update(); held = ['F3']; layer.tick(); assert.equal(layer.snapshot().beams.length, 0); assert.equal(layer.snapshot().forecasts.length, 1);
    held = []; layer.tick(); assert.equal(layer.snapshot().beams.length, 0, 'release without new evidence cannot resurrect held beam');
    update(); assert.equal(layer.snapshot().beams.length, 0, 'replaying same selected source still cannot resurrect held beam');
    stopped = true; layer.tick(); stopped = false; layer.tick(); assert.equal(layer.snapshot().beams.length, 0);
    f.now = 2050; layer.tick(); assert.equal(layer.snapshot().beams.length, 0); assert.equal(layer.snapshot().forecasts.length, 1);
    f.now = 2245; layer.tick(); assert.equal(layer.root.children.length, 0);
    f.now = 1000; update(); f.scope.scan_revision = 'other'; layer.tick(); assert.equal(layer.root.children.length, 0);
    f.scope.scan_revision = 'walk-scan'; layer.tick(); assert.equal(layer.root.children.length, 0, 'scope clear cannot resurrect cached geometry');
    update(); f.clock = { ...f.clock }; layer.tick(); assert.equal(layer.root.children.length, 0, 'reconnect anchor retires old data');
    update(); layer.reconnect(); assert.equal(layer.root.children.length, 0);
    update(); current = false; layer.tick(); assert.equal(layer.root.children.length, 0);
    layer.dispose();
});

test('layer rejects oversized or duplicate track sets and retains no unknown input references', () => {
    const f = fixture(), parent = new THREE.Group();
    const layer = attachDenForecastLayer({ apartmentRoot: parent }, { getScope: () => f.scope, getClock: () => f.clock, now: () => f.now, animate: false });
    assert.equal(layer.update({ tracks: Array(LIMITS.tracks + 1).fill(f.track) }), false);
    layer.update({ tracks: [f.track, f.track], frame: f.frame, spot: f.snapshot, receivedAt: 1000 });
    assert.deepEqual(layer.snapshot(), { forecasts: [], beams: [] });
    const value = { tracks: [f.track], frame: f.frame, spot: f.snapshot, receivedAt: 1000, private: 'SECRET' }; value.unused = value;
    layer.update(value); assert.equal(layer.snapshot().forecasts.length, 1, 'unknown cyclic metadata is not cloned');
    f.forecast.samples[0].xy_m[0] = 99;
    assert.notEqual(layer.snapshot().forecasts[0].samples[0].xy_m[0], 99);
    assert.doesNotMatch(JSON.stringify(layer.snapshot()), /SECRET|private|unused/);
    layer.dispose();
});

test('Stop and manual release need a new selected source rather than a cached replay', () => {
    for (const control of ['stop', 'hold']) {
        const f = fixture(), parent = new THREE.Group(); let stopped = false, held = [];
        const layer = attachDenForecastLayer({ apartmentRoot: parent }, { getScope: () => f.scope, getClock: () => f.clock,
            getHeldFixtureIds: () => held, isStopped: () => stopped, now: () => f.now, animate: false });
        const update = () => layer.update({ tracks: [f.track], frame: f.frame, spot: f.snapshot, receivedAt: f.receivedAt });
        const advance = (frame_id, observed_mono, receivedAt) => {
            Object.assign(f.state, { frame_id, observed_mono, available_mono: observed_mono + .05,
                valid_until_mono: observed_mono + 1.5, source_obs_ids: [`walk-obs-${frame_id}`] });
            f.track.current_obs_ids = [...f.state.source_obs_ids];
            Object.assign(f.frame, { frame_id, capture_mono: observed_mono, arrival_mono: observed_mono + .02 });
            f.path.subject = picked(f.state, bindingKeys);
            f.path.evidence.motion = { ...receipt(f.state), disposition: 'consumed', selected: true, reason: 'geometric_velocity' };
            f.beam.source_motion = receipt(f.state); f.beam.start_mono = observed_mono + .17; f.beam.arrival_mono = observed_mono + .68;
            f.path.valid_until_mono = f.path.original_valid_until_mono = f.beam.valid_until_mono = f.state.valid_until_mono;
            f.path.evaluated_mono = observed_mono + .1; f.receivedAt = f.now = receivedAt;
        };
        update(); assert.equal(layer.snapshot().beams.length, 1);
        if (control === 'stop') stopped = true; else held = ['F3'];
        layer.tick(); assert.equal(layer.snapshot().beams.length, 0);
        advance(11, 100.3, 1200);
        update(); assert.equal(layer.snapshot().beams.length, 0, 'new source received under control is retired too');
        stopped = false; held = []; layer.tick(); assert.equal(layer.snapshot().beams.length, 0);
        update(); assert.equal(layer.snapshot().beams.length, 0, 'same source replay stays retired');
        advance(12, 100.5, 1400);
        update(); assert.equal(layer.snapshot().beams.length, 1, 'new post-control source can draw');
        layer.dispose();
    }
});

test('controls observed without input or through a clock outage fence delayed pre-control receipts', () => {
    for (const control of ['stop', 'hold']) for (const lifecycle of ['new', 'clear', 'reconnect', 'clock']) {
        const f = fixture(), parent = new THREE.Group(); let stopped = false, held = [], anchor = f.clock;
        const layer = attachDenForecastLayer({ apartmentRoot: parent }, { getScope: () => f.scope, getClock: () => anchor,
            getHeldFixtureIds: () => held, isStopped: () => stopped, now: () => f.now, animate: false });
        if (lifecycle !== 'new') {
            layer.update({ tracks: [f.track], frame: f.frame, spot: f.snapshot, receivedAt: 1000 });
            if (lifecycle === 'clock') anchor = null; else layer[lifecycle]();
        }
        if (control === 'stop') stopped = true; else held = ['F3'];
        layer.tick();
        f.now = 1100; stopped = false; held = []; layer.tick();
        anchor = f.clock;
        layer.update({ tracks: [f.track], frame: f.frame, spot: f.snapshot, receivedAt: 1100 });
        assert.equal(layer.snapshot().beams.length, 0, `${control}/${lifecycle} must fence delayed old evidence`);
        layer.dispose();
    }
});

test('authored browser helper shares exact frames and original leases at ordinary and large producer timestamps', () => {
    for (const nowMono of [100.25, 9_000_000.25, 1_794_996_400.25]) {
        const value = modelPipelineEvidence({ nowMono, scanRevision: 'browser-scan', fixtureId: 'F8', frameId: 47 });
        const { frame, track, path } = value, state = track.motion;
        assert.equal(frame.frame_id, 47); assert.equal(track.track_id, 'authored-person');
        assert.equal(frame.capture_mono, nowMono - .25);
        assert.equal(state.valid_until_mono - state.observed_mono, 1.5);
        assert.equal(path.valid_until_mono, state.valid_until_mono);
        assert.deepEqual(path.subject, picked(state, bindingKeys));
        const clock = createDenSourceClock({ schema: 'den-source-clock-1', clock_domain: 'producer_monotonic', server_mono: nowMono },
            { startedAt: 999, receivedAt: 1000 });
        const ctx = { track, frame, scope: { home: 'den', site: 'victoria', ...picked(state,
            ['service_run_id', 'camera_epoch', 'transform_id', 'scan_revision', 'depth_calibration_sha256']) }, clock, receivedAt: 1000, now: 1000,
            tracks: [track], snapshot: { schema: 'den-virtual-spots-1', home: 'den', site: 'victoria', on: true, stopped: false,
                binding: { transform_id: 'authored-transform', scan_revision: 'browser-scan' } } };
        assert.ok(admitDenMotionForecast(track.motion_forecast, ctx));
        assert.equal(admitDenVirtualBeams(path, ctx).length, 1);
        assert.equal(admitDenMotionForecast(track.motion_forecast, { ...ctx, now: 2300 }), null, 'frozen object expires while stream flows');
        const later = modelPipelineEvidence({ nowMono: nowMono + .5, scanRevision: 'browser-scan', fixtureId: 'F8', frameId: 48 });
        assert.equal(later.frame.frame_id, 48); assert.notEqual(later.track.motion.source_obs_ids[0], state.source_obs_ids[0]);
        assert.equal(path.valid_until_mono, state.valid_until_mono, 'generating another frame does not mutate earlier evidence');
    }
    assert.throws(() => modelPipelineEvidence({ nowMono: NaN, scanRevision: 's' }));
    assert.throws(() => modelPipelineEvidence({ nowMono: 100, scanRevision: 's', frameId: -1 }));
});

function issueAt(f, issued) {
    f.forecast.issued_mono = f.forecast.qualified_at_mono = issued;
    f.forecast.remaining_freshness_s = f.forecast.valid_until_mono - issued;
    for (const sample of f.forecast.samples) {
        sample.at_mono = issued + sample.horizon_s;
        const dt = sample.at_mono - f.state.observed_mono, variance = .0001 * (1 + dt ** 2);
        sample.xy_m = [.5 * dt, 0]; sample.covariance_m2 = [[variance, 0], [0, variance]]; sample.sigma_m = Math.sqrt(variance);
    }
}

test('separate people and spot callbacks cannot lend a later receive bound to cached future metadata', () => {
    for (const lane of ['people', 'spot']) {
        const f = fixture(), parent = new THREE.Group(); f.now = 1500;
        if (lane === 'people') issueAt(f, 100.3);
        else { f.path.evaluated_mono = 100.3; f.beam.start_mono = 100.4; f.beam.arrival_mono = 100.9; }
        const layer = attachDenForecastLayer({ apartmentRoot: parent }, { getScope: () => f.scope, getClock: () => f.clock, now: () => f.now, animate: false });
        layer.update({ tracks: [f.track], frame: f.frame, spot: f.snapshot,
            peopleReceivedAt: lane === 'people' ? 1000 : 1500, spotReceivedAt: lane === 'spot' ? 1000 : 1500 });
        assert.equal(layer.snapshot()[lane === 'people' ? 'forecasts' : 'beams'].length, 0);
        assert.equal(layer.snapshot()[lane === 'people' ? 'beams' : 'forecasts'].length, 1, 'other lane keeps its own valid callback');
        layer.dispose();
    }
});

test('bounded delayed re-admission keeps original receipts and distinguishes uncertain from definitely future', () => {
    for (const issued of [100.25, 100.3]) {
        const f = fixture(), parent = new THREE.Group(); issueAt(f, issued);
        const layer = attachDenForecastLayer({ apartmentRoot: parent }, { getScope: () => f.scope, getClock: () => f.clock, now: () => f.now, animate: false });
        const value = { tracks: [f.track], frame: f.frame, spot: f.snapshot, peopleReceivedAt: 1000, spotReceivedAt: 1000 };
        layer.update(value); assert.equal(layer.snapshot().forecasts.length, 0);
        f.now = 1100; layer.update(value);
        assert.equal(layer.snapshot().forecasts.length, issued === 100.25 ? 1 : 0,
            'only evidence within the original receive interval can become provably past');
        assert.equal(f.forecast.valid_until_mono, 101.5);
        layer.dispose();
    }
});
