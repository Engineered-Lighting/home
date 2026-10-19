/* One leased, presentation-only desk aim. The existing overlay owns all geometry
 * and solving; this controller owns only a reversible aim and its display timer. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.HomeApartmentDeskAimPreview = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const FLAGS = ['operational_geometry','geometry_authority','identity_authority','engagement_authority',
    'absence_authority','protection_authority','beam_authority','physical_permission','follow_allowed',
    'lighting_authority','physical_commands'];
  const BINDING = ['content_revision','registration_version','manifest_digest','frame_digest','canonical_collision_digest'];
  const object = v => v !== null && typeof v === 'object' && !Array.isArray(v);
  const finite = v => typeof v === 'number' && Number.isFinite(v);
  const text = (v, max = 256) => typeof v === 'string' && v.length > 0 && v.length <= max;
  const sha = v => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
  const vector = v => Array.isArray(v) && v.length === 3 && v.every(n => finite(n) && Math.abs(n) <= 1000);
  const unit = v => vector(v) && Math.abs(Math.hypot(...v) - 1) < 1e-6;
  const copy = v => JSON.parse(JSON.stringify(v));
  // Development timing (virtual preview only): deadline checks become recorded
  // warnings and the aim holds on the latest supporting observation for 10 s of
  // Home receive time unless newer contrary evidence, Stop or identity refusal.
  const HOLD_MS = 10000, WARNING_CODES = 32, CODE = /^[a-z][a-z0-9_]{0,63}$/;
  // Kept equal to HomeApartmentDeskPreview.INTEGRITY_REASONS (tested).
  const INTEGRITY_REASONS = Object.freeze(['approximate_camera_epoch_changed','approximate_camera_epoch_unverified',
    'approximate_camera_identity_changed','approximate_camera_calibration_changed','approximate_mount_identity_required',
    'approximate_scene_or_mount_identity_changed','approximate_observed_instance_unavailable','approximate_matrix_digest_changed',
    'approximate_original_source_pin_changed',...['calibration_changed','mount_identity_changed','scope_changed','predecessor_changed',
      'matrix_changed','verification_required','mount_check_required'].map(k => 'approximate_successor_' + k),
    'approximate_scene_scope_required','approximate_scope_mismatch','approximate_source_epoch_mismatch','approximate_view_alignment_invalid',
    'desk_preview_scope_changed','desk_preview_scope_mismatch','desk_preview_target_binding_changed','desk_preview_target_binding_mismatch']);
  const integritySet = new Set(INTEGRITY_REASONS);
  const integrity = r => typeof r === 'string' && (integritySet.has(r) || /^approximate_mount_signature_[a-z0-9_]{1,64}$/.test(r));
  // Refusals that only say "this poll cannot re-prove the aim". Under a
  // development hold they keep the latest support; anything else withdraws.
  // desk_preview_hold_elapsed (the backend's own 10 s window from publish) is
  // deliberately not neutral: after it the backend stops classifying newer
  // outputs, so its evidence would go stale while Home kept holding.
  const NEUTRAL = new Set(['desk_preview_original_expired','desk_preview_receipt_retired','desk_preview_receipt_expired',
    'desk_preview_parent_retired','desk_preview_observation_changed','desk_preview_awaiting_observation',
    'desk_preview_displayed_frame_mismatch','desk_preview_unavailable','desk_preview_clock_unavailable',
    'desk_preview_shoulders_unavailable','desk_preview_single_person_required']);
  function neutral(p) {
    if (!object(p) || p.available === true || typeof p.reason !== 'string') return false;
    if (p.reason === 'desk_preview_approximation_unavailable') return !integrity(p.parentReason);
    return NEUTRAL.has(p.reason);
  }
  const warningList = v => Array.isArray(v) && v.length <= WARNING_CODES && new Set(v).size === v.length
    && v.every(c => typeof c === 'string' && CODE.test(c));
  const EVIDENCE = ['supporting','contrary','integrity','neutral'];
  const eventId = v => v === null || Number.isSafeInteger(v) && v > 0;
  function validEvidence(e) {
    const b = e && e.armB, k = e && e.backend;
    return object(e) && ['development','strict'].includes(e.policy) && (e.connection === null || text(e.connection, 64))
      && (k === undefined || k === null || object(k) && EVIDENCE.includes(k.evidence) && eventId(k.eventId))
      && (e.sessionState === null || text(e.sessionState, 64)) && (e.sourceEpoch === null || text(e.sourceEpoch))
      && (e.integrityReason === null || text(e.integrityReason, 160))
      && (b === null || object(b) && text(b.key, 1024) && finite(b.observedAt)
        && (b.personCount === null || Number.isSafeInteger(b.personCount) && b.personCount >= 0));
  }
  function same(a, b) {
    if (a === b) return true;
    if (!a || !b || typeof a !== 'object' || typeof b !== 'object' || Array.isArray(a) !== Array.isArray(b)) return false;
    const keys = Object.keys(a);
    return keys.length === Object.keys(b).length && keys.every(k => Object.hasOwn(b, k) && same(a[k], b[k]));
  }
  function binding(v) {
    return object(v) && Object.keys(v).length === BINDING.length && BINDING.every(k => Object.hasOwn(v, k))
      && text(v.content_revision) && Number.isSafeInteger(v.registration_version) && v.registration_version >= 0
      && BINDING.slice(2).every(k => sha(v[k]));
  }
  function sceneMatches(context, b) {
    const i = context && context.identity;
    return object(i) && i.siteId === 'victoria' && i.contentRevision === b.content_revision
      && i.registrationVersion === b.registration_version && i.frameDigest === b.frame_digest;
  }
  function validInput(p) {
    const t = p && p.target, e = t && t.equipment_ref;
    return object(p) && p.available === true && p.status === 'preview' && p.virtual_preview_only === true
      && p.qualification === null && FLAGS.every(k => p[k] === false)
      && text(p.sourceKey, 16384) && finite(p.remainingHoldS) && p.remainingHoldS > 0 && p.remainingHoldS <= 2
      && finite(p.validUntil) && p.validUntil > 0 && object(p.observation) && object(p.provenance)
      && Number.isSafeInteger(p.provenance.source_event_id) && p.provenance.source_event_id > 0
      && sha(p.provenance.configured_candidate_sha256) && text(p.observation.session_id)
      && text(p.observation.capture_epoch) && Number.isSafeInteger(p.observation.generation)
      && p.observation.generation >= 1 && text(p.observation.engine_epoch)
      && object(t) && t.kind === 'configured_desk_centre' && t.frame === 'z_up_metric_floor0' && t.unit === 'm'
      && vector(t.point_m) && unit(t.normal_unit) && binding(t.binding)
      && object(e) && Number.isSafeInteger(e.revision) && e.revision >= 0 && sha(e.digest)
      && object(t.target_ref) && t.target_ref.schema === 'home-registered-desk-target-2' && sha(t.target_ref.digest)
      && object(p.personPoint) && p.personPoint.error_bound_m === null && vector(p.personPoint.point_m)
      && (p.timingPolicy === undefined || p.timingPolicy === null
        ? p.warnings === undefined || Array.isArray(p.warnings) && p.warnings.length === 0
        : p.timingPolicy === 'development' && warningList(p.warnings) && finite(p.observation.observed_at)
          && text(p.observation.request_id) && text(p.observation.frame_id)
          && (p.evidence === undefined || p.evidence === null || EVIDENCE.includes(p.evidence))
          && (p.evidenceEventId === undefined || eventId(p.evidenceEventId)));
  }
  function descriptorMatches(d, t) {
    return object(d) && d.schema === 'home-virtual-fixture-interaction-1'
      && Number.isSafeInteger(d.generation) && d.generation >= 0 && text(d.fixture_id) && text(d.mount_id)
      && same(d.binding, t.binding) && d.equipment_revision === t.equipment_ref.revision
      && d.equipment_digest === t.equipment_ref.digest && d.truth_role === 'virtual_preview'
      && d.physical_commands === false && d.optical_origin_m === null
      && vector(d.display_anchor_m) && vector(d.display_origin_m) && unit(d.horizontal_world_reference)
      && object(d.state) && finite(d.state.pan_deg) && finite(d.state.tilt_deg);
  }
  // Warnings and age are excluded: a later poll of the same original may add
  // home_original_deadline_passed without being a changed original.
  function fingerprint(p) {
    return copy({ target: p.target, personPoint: p.personPoint, observation: p.observation,
      provenance: p.provenance, label: p.label, validUntil: p.validUntil, timingPolicy: p.timingPolicy || null });
  }

  function create({ overlay, solveAim, getContext, nowMs = () => globalThis.performance.now(),
    setTimeout: schedule = (fn, ms) => globalThis.setTimeout(fn, ms),
    clearTimeout: unschedule = id => globalThis.clearTimeout(id), onChange = () => {} } = {}) {
    if (!overlay || !['virtualFixturesReadback','virtualFixtureInteractionDescriptor','aimVirtualFixtureAtTarget',
      'restoreVirtualFixtureAim'].every(k => typeof overlay[k] === 'function')
      || ![solveAim,getContext,nowMs,schedule,unschedule,onChange].every(fn => typeof fn === 'function')) {
      throw TypeError('desk_aim_preview_dependencies_required');
    }
    let enabled = false, disposed = false, owned = null, source = null, timer = null, serial = 0;
    let status = 'disabled', reason = 'preview_disabled', restoreStatus = 'none', lastNow = null;
    let hold = null, policy = null;
    const warningCounts = new Map();
    function clock() {
      const n = nowMs();
      if (!finite(n) || n < 0 || lastNow !== null && n < lastNow) return null;
      lastNow = n; return n;
    }
    function context() { try { return getContext(); } catch (_) { return null; } }
    function readback() {
      const n = clock(), lastSeenS = owned && hold && n !== null ? Math.max(0, (n - hold.lastSeen) / 1000) : null;
      return { enabled, active: !!owned, status, reason, fixtureId: owned ? owned.after.fixture_id : null,
        sourceKey: source ? source.key : null, remainingHoldS: owned && n !== null ? Math.max(0, (source.deadline - n) / 1000) : 0,
        restoreStatus, policy, holding: !!(owned && hold), lastSeenS,
        lastSeenLabel: lastSeenS === null ? null : `last seen ${Math.floor(lastSeenS)}s ago`,
        warnings: owned && hold ? [...hold.warnings] : [], warningCounts: Object.fromEntries(warningCounts),
        warningsRecorded: warningCounts.size, virtual_preview_only: true, physical_commands: false };
    }
    // Each code counts at most once per supporting original; at most 32 codes.
    function record(support, codes) {
      for (const code of codes) {
        if (support.recorded.has(code)) continue;
        if (!warningCounts.has(code) && warningCounts.size >= WARNING_CODES) continue;
        support.recorded.add(code); warningCounts.set(code, (warningCounts.get(code) || 0) + 1);
        if (!support.warnings.includes(code)) support.warnings.push(code);
      }
    }
    function notify() { const result = readback(); try { onChange(result); } catch (_) { /* UI owns its callback. */ } return result; }
    function cancelTimer() { serial++; if (timer !== null) unschedule(timer); timer = null; }
    function release() {
      restoreStatus = 'none';
      if (!owned) return;
      const prior = owned; owned = null;
      let d;
      try { d = overlay.virtualFixtureInteractionDescriptor(prior.after.fixture_id); } catch (_) { /* Retired overlay. */ }
      if (!sceneMatches(context(), prior.after.binding) || !d || d.generation !== prior.after.generation
        || !same(d.binding, prior.after.binding) || d.equipment_revision !== prior.after.equipment_revision
        || d.equipment_digest !== prior.after.equipment_digest) { restoreStatus = 'scope_changed'; return; }
      if (!same(d, prior.after)) { restoreStatus = 'not_owned'; enabled = false; return; }
      try { restoreStatus = overlay.restoreVirtualFixtureAim(d.fixture_id, copy(prior.before))?.ok === true ? 'restored' : 'refused'; }
      catch (_) { restoreStatus = 'refused'; }
    }
    function withdraw(why = 'preview_withdrawn') {
      cancelTimer(); if (source) source.retired = true; hold = null; release();
      // A disarmed controller reports no timing policy (UI development line).
      if (!enabled) policy = null;
      status = disposed ? 'disposed' : enabled ? 'unavailable' : 'disabled';
      reason = text(why, 160) ? why : 'preview_withdrawn'; return notify();
    }
    const expiredReason = () => hold ? 'preview_support_timeout' : 'preview_expired';
    function armTimer() {
      cancelTimer(); const n = clock();
      if (n === null || !source || source.deadline <= n) return withdraw(n === null ? 'preview_clock_unavailable' : expiredReason());
      const token = serial;
      timer = schedule(() => { if (disposed || token !== serial) return; timer = null; withdraw(expiredReason()); }, source.deadline - n);
    }
    function ownershipLost() {
      let d; try { d = overlay.virtualFixtureInteractionDescriptor(owned.after.fixture_id); } catch (_) { /* Gone. */ }
      if (d && same(d, owned.after)) return null;
      // A manual edit in the same scene must not be overwritten by a later
      // frame; release() disarms on not_owned before the notice is built.
      return withdraw('preview_ownership_changed');
    }
    // Newer contrary evidence ends a development hold; absent or stale evidence
    // never extends it. Only the 10 s support timer and these rows withdraw.
    // The backend's classification of its latest arm-B output is preferred:
    // contrary or integrity withdraws at once, supporting/neutral keeps the
    // hold. Home's own person count is only the fallback when it gives none.
    function contrary(e) {
      if (!validEvidence(e)) return 'preview_evidence_unavailable';
      // A stopped source no longer reports a development policy; name the Stop.
      if (['stopped','disconnected','refused'].includes(e.connection)
        || ['stopped','stopping','blocked','failed','interrupted','indeterminate'].includes(e.sessionState)) return 'preview_source_stopped';
      if (e.integrityReason) return e.integrityReason;
      if (e.policy !== 'development') return 'preview_timing_policy_changed';
      if (e.sourceEpoch !== null && e.sourceEpoch !== hold.captureEpoch) return 'preview_camera_identity_changed';
      if (e.backend) {
        if (e.backend.evidence === 'contrary') return 'preview_evidence_contrary';
        if (e.backend.evidence === 'integrity') return 'preview_evidence_integrity';
        return null;
      }
      const b = e.armB;
      if (b && b.observedAt > hold.observedAt && b.key !== hold.supportKey) {
        if (b.personCount === 0) return 'preview_no_person';
        if (b.personCount >= 2) return 'preview_multiple_people';
      }
      return null;
    }
    function update(input, evidence) {
      if (disposed) return readback();
      if (!enabled) return readback();
      const n = clock(), c = context();
      if (n === null) return withdraw('preview_clock_unavailable');
      if (!c || c.active !== true || c.hidden !== false || c.committedOwnerActive !== false) return withdraw('preview_context_unavailable');
      if (hold && owned) {
        if (n >= source.deadline) return withdraw('preview_support_timeout');
        const lost = ownershipLost(); if (lost) return lost;
        try { if (overlay.virtualFixturesReadback()?.visible !== true) return withdraw('preview_equipment_unavailable'); }
        catch (_) { return withdraw('preview_equipment_unavailable'); }
        const why = contrary(evidence); if (why) return withdraw(why);
      }
      let p;
      try { p = copy(input); } catch (_) { return withdraw('preview_input_invalid'); }
      if (!validInput(p)) {
        // The timer is untouched: only a new supporting original moves lastSeen.
        if (hold && owned && input != null && neutral(p)) { status = 'holding'; reason = p.reason; return notify(); }
        return withdraw(text(p?.reason, 160) ? p.reason : 'preview_input_unavailable');
      }
      if (!sceneMatches(c, p.target.binding)) return withdraw('preview_scene_changed');
      const next = p.timingPolicy === 'development' ? 'development' : 'strict';
      if (owned && source && source.policy !== next) return withdraw('preview_timing_policy_changed');
      if (hold && owned && p.observation.capture_epoch !== hold.captureEpoch) return withdraw('preview_camera_identity_changed');
      const identity = [p.observation.session_id,p.observation.capture_epoch,p.observation.generation,p.observation.engine_epoch];
      const event = p.provenance.source_event_id, mark = fingerprint(p);
      if (source && same(identity, source.identity)) {
        if (event < source.event || event === source.event && (p.sourceKey !== source.key || !same(mark, source.mark))) return withdraw('preview_original_changed');
        if (p.sourceKey === source.key && (source.retired || n >= source.deadline)) return withdraw(source.retired ? 'preview_expired' : expiredReason());
      }
      if (owned) {
        const lost = ownershipLost(); if (lost) return lost;
        if (!same(owned.after.binding, p.target.binding) || owned.after.equipment_revision !== p.target.equipment_ref.revision
          || owned.after.equipment_digest !== p.target.equipment_ref.digest) return withdraw('preview_equipment_changed');
      }
      let rows;
      try {
        const r = overlay.virtualFixturesReadback();
        if (r.status !== 'ready' || r.visible !== true || r.physical_commands !== false || !Array.isArray(r.fixtures)
          || r.fixtures.length !== 4 || new Set(r.fixtures.map(f => f.fixture_id)).size !== 4
          || r.revision !== p.target.equipment_ref.revision || r.digest !== p.target.equipment_ref.digest) return withdraw('preview_equipment_unavailable');
        rows = r.fixtures.map(row => ({ row, d: overlay.virtualFixtureInteractionDescriptor(row.fixture_id) }));
        if (rows.some(v => !descriptorMatches(v.d, p.target))) return withdraw('preview_equipment_changed');
        rows.sort((a, b) => a.d.fixture_id < b.d.fixture_id ? -1 : a.d.fixture_id > b.d.fixture_id ? 1 : 0);
      } catch (_) { return withdraw('preview_equipment_unavailable'); }
      const target = { point: p.target.point_m, normal: p.target.normal_unit,
        surface_id: 'configured-desk-preview', kind: 'registered_mesh' };
      let chosen;
      for (const v of rows) {
        try {
          const aim = solveAim(v.d.display_origin_m, target.point,
            { horizontal_world_reference: v.d.horizontal_world_reference, limits: v.row.limits });
          if (aim?.ok === true && aim.executable === true && aim.pan_indeterminate !== true) { chosen = v; break; }
        } catch (_) { /* Try the next existing fixture without mutating this one. */ }
      }
      if (!chosen) return withdraw('preview_no_feasible_fixture');
      if (owned && owned.after.fixture_id !== chosen.d.fixture_id) { withdraw('preview_fixture_changed'); return readback(); }
      if (next === 'development') {
        // Duplicates of the same original never move lastSeen.
        if (!(hold && source && source.key === p.sourceKey)) {
          hold = { lastSeen: n, observedAt: p.observation.observed_at, captureEpoch: p.observation.capture_epoch,
            supportKey: JSON.stringify([p.observation.request_id, p.observation.frame_id]), warnings: [], recorded: new Set() };
        }
        record(hold, p.warnings);
        if (!(source && source.key === p.sourceKey)) source = { key: p.sourceKey, identity, event, mark, deadline: 0, retired: false, policy: next };
        source.deadline = hold.lastSeen + HOLD_MS;
      } else {
        hold = null;
        const deadline = n + p.remainingHoldS * 1000;
        if (source && source.key === p.sourceKey) source.deadline = Math.min(source.deadline, deadline);
        else source = { key: p.sourceKey, identity, event, mark, deadline, retired: false, policy: next };
      }
      policy = next;
      try {
        if (!owned) {
          const before = copy(chosen.d), result = overlay.aimVirtualFixtureAtTarget(before.fixture_id, target, { expectedGeneration: before.generation });
          if (result?.ok !== true || !descriptorMatches(result.descriptor, p.target)) return withdraw('preview_aim_refused');
          owned = { before, after: copy(result.descriptor) };
        } else if (!same(owned.after.target, target)) {
          const result = overlay.aimVirtualFixtureAtTarget(owned.after.fixture_id, target, { expectedGeneration: owned.after.generation });
          if (result?.ok !== true || !descriptorMatches(result.descriptor, p.target)) return withdraw('preview_aim_refused');
          owned.after = copy(result.descriptor);
        }
      } catch (_) { return withdraw('preview_aim_refused'); }
      status = 'preview'; reason = null; restoreStatus = 'none'; armTimer(); return notify();
    }
    function setEnabled(value) {
      if (typeof value !== 'boolean') throw TypeError('desk_aim_preview_enabled_boolean_required');
      if (disposed) return readback();
      if (!value) { enabled = false; return withdraw('preview_disabled'); }
      if (!enabled) { enabled = true; warningCounts.clear(); status = 'unavailable'; reason = 'preview_awaiting_observation'; }
      return notify();
    }
    return { update, withdraw, setEnabled, readback,
      manualTakeover(why = 'preview_manual_takeover') { if (disposed) return readback(); enabled = false; return withdraw(why); },
      dispose() { if (disposed) return readback(); enabled = false; disposed = true; return withdraw('preview_disposed'); } };
  }
  return Object.freeze({ create, HOLD_MS, INTEGRITY_REASONS, isIntegrityReason: integrity });
});
