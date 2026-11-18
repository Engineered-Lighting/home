/* Authored browser evidence, adapted from the walking owner's synthetic virtual fixture.
 * Generate once per authored frame and reuse that object for people and spot events.
 * Keeping an existing object never renews its capture-based lease. No clocks or I/O here. */
export function modelPipelineEvidence({ nowMono, scanRevision, fixtureId = 'F3', frameId = Math.floor(nowMono * 1000) } = {}) {
    const text = x => typeof x === 'string' && x.length > 0 && x.length <= 128;
    if (typeof nowMono !== 'number' || !Number.isFinite(nowMono) || nowMono < .25 || nowMono > 1e12
        || !Number.isSafeInteger(frameId) || frameId < 0 || !text(scanRevision) || !text(fixtureId)) throw Error('invalid_authored_model_frame');
    const motion = { service_run_id: 'authored-run', camera_epoch: 1, frame_id: frameId, track_id: 'authored-person',
        association_revision: 1, transform_id: 'authored-transform', scan_revision: scanRevision,
        depth_calibration_sha256: 'c'.repeat(64), observed_mono: nowMono - .25, available_mono: nowMono - .20,
        valid_until_mono: nowMono - .25 + 1.5, xy_m: [0, 0], velocity_mps: [.5, 0],
        covariance: [[.0001, 0, 0, 0], [0, .0001, 0, 0], [0, 0, .0001, 0], [0, 0, 0, .0001]],
        sample_count: 4, history_span_s: .6, white_accel_sigma_mps32: 0, registration_error_m: 0,
        timing_error_s: 0, source_obs_ids: [`authored-obs-${frameId}`], basis: 'den_track_kalman_cv',
        time_status: 'estimated_source_elapsed', time_qualified: false, timing_evidence_id: null };
    const keys = ['service_run_id', 'camera_epoch', 'frame_id', 'track_id', 'association_revision',
        'transform_id', 'scan_revision', 'depth_calibration_sha256'];
    const pick = names => Object.fromEntries(names.map(k => [k, motion[k]]));
    const source = () => ({ ...pick(keys), ...pick(['observed_mono', 'available_mono', 'valid_until_mono',
        'basis', 'time_status', 'time_qualified', 'timing_evidence_id']),
        velocity_mps: [...motion.velocity_mps], source_obs_ids: [...motion.source_obs_ids] });
    const issued = nowMono - .05;
    const forecast = { schema: 'den-spatial-forecast-1', state: structuredClone(motion), issued_mono: issued,
        valid_until_mono: motion.valid_until_mono,
        samples: [.2, .5, 1, 2].map(horizon_s => {
            const at_mono = issued + horizon_s, dt = at_mono - motion.observed_mono, variance = .0001 * (1 + dt ** 2);
            return { horizon_s, at_mono, xy_m: [.5 * dt, 0], covariance_m2: [[variance, 0], [0, variance]], sigma_m: Math.sqrt(variance) };
        }), coordinate_space: 'room_floor_xy_m', time_domain: 'producer_monotonic', covariance_order: ['x', 'y', 'vx', 'vy'],
        uncertainty_kind: 'conservative_envelope', qualified_at_mono: issued,
        remaining_freshness_s: motion.valid_until_mono - issued, shadow_only: true, virtual_only: true,
        physical_qualified: false, walkable_qualified: false, beam_qualified: false,
        delay_qualified: false, aim_qualified: false, occupancy_qualified: false };
    const frame = { ...pick(keys.filter(k => !['track_id', 'association_revision'].includes(k))),
        capture_mono: motion.observed_mono, arrival_mono: nowMono - .23 };
    const track = { ...pick(['track_id', 'service_run_id', 'camera_epoch', 'association_revision']),
        state: 'confirmed', association_status: 'confident', current_obs_ids: [...motion.source_obs_ids],
        position_basis: 'stereo', xy_m: [...motion.xy_m], sigma_m: .01, heading_deg: 90,
        posture: 'walking', seated: false, motion, motion_forecast: forecast };
    const beam = { fixture_id: fixtureId, authority: 'virtual_only', branch: 'geometric_velocity',
        floor_profile_id: 'synthetic-floor', profile_id: 'synthetic-fast-profile', precision_qualified: false,
        target_m: [0.49013346877207764, 0, 0], start_mono: nowMono + .02, arrival_mono: nowMono + .5311028208132,
        valid_until_mono: motion.valid_until_mono, source_motion: source(), radius_m: 0.21624310993808307,
        footprint: { basis: 'virtual_fwhm_floor_projection', heading_lead_m: .1,
            inner_radius_m: 0.20317822768056382, radius_m: 0.21624310993808307 },
        timing: { basis: 'virtual_profile', cadence_wait_s: 0, command_bound_s: .02, current_motion_wait_s: 0,
            fade_to_useful_s: 0, forecast_horizon_s: .5811028208132001, motor_s: .47110282081320026,
            observation_age_s: .2, queue_bound_s: .02, settle_s: .04,
            solve_residual_s: .0008358832690475992, transport_bound_s: .03 },
        uncertainty: { calibrated_probability: false,
            covariance_m2: [[.0001608816493824535, 0], [0, .0001608816493824535]],
            envelope_m: .03846968068074981, sigma_m: .01268391301540867, sigma_multiplier: 3 } };
    const path = { schema: 'den-path-lighting-1', mode: 'virtual', status: 'eligible', reason: 'geometric_velocity',
        evaluated_mono: issued, original_valid_until_mono: motion.valid_until_mono, valid_until_mono: motion.valid_until_mono,
        subject: pick(keys), evidence: { motion: { ...source(), disposition: 'consumed', selected: true, reason: 'geometric_velocity' },
            intent: { schema: 'den.walking-intent/v1', disposition: 'absent', reason: 'intent_hint_absent', selected: false,
                consumed: false, authority: 'semantic_prior_only', uncertainty: 'uncalibrated_semantic_hint',
                clock: 'producer_monotonic', request_id: null, clip_id: null, profile_digest: null,
                snapshot_manifest_digest: null, model_revision: null, dependencies: {}, produced_at: null,
                observed_at: null, expires_at: null, intent: null, hypotheses: [] } },
        fixture_reasons: {}, proposals: [structuredClone(beam)], virtual_beams: [beam] };
    return { frame, track, path };
}
