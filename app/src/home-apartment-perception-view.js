/* Pure presentation projection. No media IO, source clocks, geometry solving or device authority. */
(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.HomeApartmentPerceptionView = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var SCOPE_FIELDS = Object.freeze(['siteId', 'contentRevision', 'frameDigest', 'registrationVersion', 'cameraId', 'sessionId', 'generation']);
  var LIMITS = Object.freeze({ joints: 128, edges: 256, models: 32, resultDepth: 5, resultNodes: 512, resultArray: 64, resultKeys: 32, resultString: 2048 });
  function own(value, key) { return Object.prototype.hasOwnProperty.call(value || {}, key); }
  function object(value) { return !!value && typeof value === 'object' && !Array.isArray(value); }
  function number(value) { return typeof value === 'number' && Number.isFinite(value); }
  function text(value, max) { return typeof value === 'string' && value.length > 0 && value.length <= (max || 256); }
  function token(value) { return text(value) || (number(value) && Number.isInteger(value) && value >= 0); }
  function scopeValid(scope) {
    return object(scope) && Object.keys(scope).length === SCOPE_FIELDS.length && SCOPE_FIELDS.every(function (key) { return own(scope, key); })
      && ['siteId', 'contentRevision', 'frameDigest', 'cameraId', 'sessionId'].every(function (key) { return text(scope[key]); })
      && (scope.registrationVersion === null || (number(scope.registrationVersion) && Number.isInteger(scope.registrationVersion) && scope.registrationVersion >= 0))
      && token(scope.generation);
  }
  function scopeMatches(a, b) {
    return scopeValid(a) && scopeValid(b) && SCOPE_FIELDS.every(function (key) { return a[key] === b[key]; });
  }
  function copyScope(value) {
    var out = {};
    SCOPE_FIELDS.forEach(function (key) { out[key] = value && value[key]; });
    return out;
  }
  function createScope(engineIdentity, session) {
    engineIdentity = engineIdentity || {}; session = session || {};
    return { siteId: engineIdentity.siteId, contentRevision: engineIdentity.contentRevision,
      frameDigest: engineIdentity.frameDigest, registrationVersion: own(engineIdentity, 'registrationVersion') ? engineIdentity.registrationVersion : null,
      cameraId: own(session, 'cameraId') ? session.cameraId : session.camera_id,
      sessionId: own(session, 'sessionId') ? session.sessionId : (own(session, 'session_id') ? session.session_id : session.id),
      generation: session.generation };
  }
  function frameKey(frame, scope) {
    if (frame && frame.descriptor) { scope = scope || frame.scope; frame = frame.descriptor; }
    if (!scopeValid(scope) || !object(frame) || !token(frame.frame_id) || !token(frame.media_id)) return null;
    if (own(frame, 'capture_id') && frame.capture_id !== null && !token(frame.capture_id)) return null;
    if (own(frame, 'sequence_id') && frame.sequence_id !== null && !token(frame.sequence_id)) return null;
    return JSON.stringify(SCOPE_FIELDS.map(function (key) { return scope[key]; }).concat([
      frame.frame_id, frame.capture_id == null ? null : frame.capture_id, frame.media_id,
      frame.sequence_id == null ? null : frame.sequence_id,
    ]));
  }
  function bounded(value) {
    var state = { nodes: 0, truncated: false, invalid: false }, seen = new Set();
    function copy(item, depth) {
      state.nodes++;
      if (state.nodes > LIMITS.resultNodes || depth > LIMITS.resultDepth) { state.truncated = true; return null; }
      if (item == null || typeof item === 'boolean') return item == null ? null : item;
      if (typeof item === 'number') { if (!number(item)) state.invalid = true; return number(item) ? item : null; }
      if (typeof item === 'string') { if (item.length > LIMITS.resultString) state.truncated = true; return item.slice(0, LIMITS.resultString); }
      if (typeof item !== 'object' || seen.has(item)) { state.invalid = true; return null; }
      seen.add(item);
      var out;
      if (Array.isArray(item)) {
        if (item.length > LIMITS.resultArray) state.truncated = true;
        out = [];
        for (var index = 0; index < Math.min(item.length, LIMITS.resultArray); index++) {
          if (state.nodes >= LIMITS.resultNodes) { state.truncated = true; break; }
          out.push(copy(item[index], depth + 1));
        }
      } else {
        out = {};
        var keys = Object.keys(item);
        if (keys.length > LIMITS.resultKeys) state.truncated = true;
        for (var k = 0; k < Math.min(keys.length, LIMITS.resultKeys); k++) {
          if (state.nodes >= LIMITS.resultNodes) { state.truncated = true; break; }
          var key = keys[k];
          if (key === '__proto__' || key === 'constructor' || key === 'prototype') { state.invalid = true; continue; }
          if (key.length > 128) { state.truncated = true; continue; }
          out[key] = copy(item[key], depth + 1);
        }
      }
      seen.delete(item); return out;
    }
    var result = copy(value, 0);
    return { value: state.invalid ? null : result, truncated: state.truncated, invalid: state.invalid };
  }
  function originalTimes(record, field) {
    record = record || {};
    return { observed_at: record[field || 'observed_at'] == null ? null : record[field || 'observed_at'],
      available_at: record.available_at == null ? null : record.available_at,
      valid_until: record.valid_until == null ? null : record.valid_until };
  }
  function age(record, clock, field, allTimes) {
    var times = originalTimes(record || {}, field), observed = times.observed_at, available = times.available_at, until = times.valid_until;
    var out = { status: 'unavailable', seconds: null, uncertainty_s: null, reason: 'clock_unavailable' };
    if ([observed, available, until].some(function (v) { return v !== null && (!number(v) || v < 0); })) return { status: 'invalid', seconds: null, uncertainty_s: null, reason: 'invalid_original_time' };
    if (!object(clock) || clock.domain !== 'unix' || !number(clock.now_s) || clock.now_s < 0 || !number(clock.uncertainty_s) || clock.uncertainty_s < 0) return out;
    out.uncertainty_s = clock.uncertainty_s;
    if (observed === null) { out.reason = 'original_time_unavailable'; return out; }
    var low = clock.now_s - clock.uncertainty_s, high = clock.now_s + clock.uncertainty_s;
    if (!number(low) || !number(high)) { out.reason = 'clock_unavailable'; return out; }
    if ((available !== null && available < observed) || (until !== null && (until < observed || (available !== null && until < available)))) {
      out.status = 'invalid'; out.reason = 'time_order_invalid'; return out;
    }
    if (observed > low || (available !== null && available > low)) { out.status = 'future'; out.reason = 'future_or_clock_overlap'; return out; }
    out.seconds = clock.now_s - observed;
    if (until !== null && until <= high) { out.status = 'stale'; out.reason = 'expired'; return out; }
    if (until === null || (allTimes && available === null)) { out.reason = until === null ? 'expiry_unavailable' : 'available_time_unavailable'; return out; }
    out.status = 'current'; out.reason = null; return out;
  }
  function frameValid(frame, scope) {
    return object(frame) && frameKey(frame, scope) !== null && frame.site_id === scope.siteId && frame.generation === scope.generation
      && number(frame.width) && Number.isInteger(frame.width) && frame.width > 0 && frame.width <= 32768
      && number(frame.height) && Number.isInteger(frame.height) && frame.height > 0 && frame.height <= 32768
      && (!own(frame, 'scope') || scopeMatches(frame.scope, scope))
      && (!own(frame, 'camera_id') || frame.camera_id === scope.cameraId)
      && (!own(frame, 'session_id') || frame.session_id === scope.sessionId);
  }
  function association(record, frame) {
    if (!frame) return 'no-frame';
    if (!record || !token(record.frame_id)) return 'unassociated';
    if (record.frame_id !== frame.frame_id) return 'different-frame';
    if (record.capture_id != null && frame.capture_id != null && record.capture_id !== frame.capture_id) return 'different-frame';
    return 'same-frame';
  }
  function displayedMatches(displayed, projected, scope) {
    if (displayed === undefined) return true;
    if (!projected || !object(displayed) || !scopeMatches(displayed.scope, scope)) return false;
    var key = frameKey(displayed, scope);
    return key !== null && key === projected.key && (!own(displayed, 'key') || displayed.key === key);
  }
  function idKey(id) { return typeof id + ':' + String(id); }
  function suppliedPoints(record, room) {
    var input = record && (room ? record.joints : record.keypoints), edges = record && (room ? record.edges : record.skeleton_edges);
    if (!Array.isArray(input)) return { reason: 'joints_unavailable', joints: [], edges: [] };
    if (input.length > LIMITS.joints || (Array.isArray(edges) && edges.length > LIMITS.edges)) return { reason: 'skeleton_limit', joints: [], edges: [] };
    var selected = [], ids = new Set(), invalid = null;
    input.forEach(function (joint) {
      if (!object(joint) || joint.visible === false || !token(joint.id)) return;
      var position = room ? joint.position_m : [joint.x, joint.y];
      if (!Array.isArray(position) || position.length !== (room ? 3 : 2) || position.some(function (v) { return v == null; })) return;
      if (!position.every(number) || (own(joint, 'confidence') && joint.confidence !== null && !number(joint.confidence))) { invalid = 'nonfinite_joint'; return; }
      var key = idKey(joint.id);
      if (ids.has(key)) { invalid = 'duplicate_joint_id'; return; }
      ids.add(key);
      var out = { id: joint.id, visible: true };
      if (room) out.position_m = position.slice(); else { out.x = position[0]; out.y = position[1]; }
      if (own(joint, 'confidence')) out.confidence = joint.confidence;
      selected.push(out);
    });
    if (invalid) return { reason: invalid, joints: [], edges: [] };
    var keptEdges = [], seen = new Set();
    (Array.isArray(edges) ? edges : []).forEach(function (edge) {
      if (!Array.isArray(edge) || edge.length !== 2 || !ids.has(idKey(edge[0])) || !ids.has(idKey(edge[1])) || edge[0] === edge[1]) return;
      var key = JSON.stringify(edge);
      if (!seen.has(key)) { seen.add(key); keptEdges.push(edge.slice()); }
    });
    return { reason: null, joints: selected, edges: keptEdges };
  }
  function blankRoom(reason, status) {
    return { admitted: false, status: status || 'unavailable', reason: reason || 'room_pose_unavailable', qualification: null,
      diagnostic: false, noLightingAuthority: true, joints: [], edges: [], anchor: null,
      age: { status: 'unavailable', seconds: null, uncertainty_s: null, reason: 'original_time_unavailable' } };
  }
  function blankDiagnostic(reason) {
    return { renderable: false, admitted: false, status: 'unavailable', reason: reason || 'diagnostic_pose_unavailable',
      diagnostic: true, noLightingAuthority: true, followAllowed: false, joints: [], edges: [], anchor: null,
      qualification: 'diagnostic_alignment', timingLabel: null, eventKey: null, provenance: null, originalTimes: null };
  }
  function sha(value) { return typeof value === 'string' && /^[a-f0-9]{64}$/.test(value); }
  function diagnosticEventKey(packet) {
    var pose = packet && packet.diagnostic_pose, provenance = pose && pose.provenance;
    if (!object(packet) || packet.schema !== 'home-apartment-diagnostic-room-pose-1' || !object(pose)
      || !scopeValid(pose.scope) || !object(provenance) || !Number.isSafeInteger(provenance.source_event_id)
      || provenance.source_event_id <= 0 || !sha(provenance.original_event_sha256) || !sha(provenance.candidate_sha256)
      || !sha(provenance.frame_media_sha256) || !text(provenance.frame_media_id) || !text(pose.source_epoch)) return null;
    return JSON.stringify(SCOPE_FIELDS.map(function (key) { return pose.scope[key]; }).concat([
      pose.source_epoch, provenance.source_event_id, provenance.original_event_sha256, provenance.candidate_sha256,
      provenance.frame_media_id, provenance.frame_media_sha256, provenance.model_generation,
      pose.registration && pose.registration.camera_to_room_digest, pose.registration && pose.registration.alignment_digest]));
  }
  function projectDiagnostic(packet, envelope, scope, shown, receipt, collisionDigest) {
    var out = blankDiagnostic(), pose = packet && packet.diagnostic_pose, key = diagnosticEventKey(packet), why = null;
    if (!object(packet)) return out;
    if (packet.renderable !== true) { out.reason = text(packet.reason) ? packet.reason : 'diagnostic_pose_unavailable'; return out; }
    var denied = ['operational_geometry', 'geometry_authority', 'identity_authority', 'engagement_authority', 'absence_authority',
      'protection_authority', 'beam_authority', 'physical_permission', 'follow_allowed'];
    var recorded = packet.status === 'recorded', frame = packet.frame, shownFrame = shown && (shown.descriptor || shown),
      registration = pose && pose.registration, provenance = pose && pose.provenance, timing = packet.timing;
    if (!key || denied.some(function (field) { return packet[field] !== false; }) || packet.room_pose !== null) why = 'diagnostic_authority_invalid';
    else if (!scopeMatches(pose.scope, scope) || scope.siteId !== 'victoria' || scope.cameraId !== 'den') why = 'diagnostic_scope_mismatch';
    else if (pose.qualification !== 'diagnostic_alignment' || registration && registration.status !== 'reviewed_diagnostic'
      || !registration || registration.camera_id !== scope.cameraId || !sha(registration.camera_to_room_digest) || !sha(registration.alignment_digest)
      || !sha(registration.canonical_collision_digest) || collisionDigest != null && registration.canonical_collision_digest !== collisionDigest) why = 'diagnostic_registration_unreviewed';
    else if (!frameValid(frame, scope) || frameKey(envelope.frame, scope) !== frameKey(frame, scope)
      || frame.camera_id !== scope.cameraId || frame.frame_id !== pose.frame_id
      || frame.media_id !== provenance.frame_media_id || !sha(frame.media_sha256) || frame.media_sha256 !== provenance.frame_media_sha256
      || provenance.media_generation !== scope.generation || !Number.isSafeInteger(provenance.model_generation)
      || !sha(provenance.collection_sha256) || !sha(provenance.calibration_content_id)) why = 'diagnostic_media_binding_mismatch';
    else if (!text(envelope.source_epoch) || pose.source_epoch !== envelope.source_epoch || frame.source_epoch !== envelope.source_epoch) why = 'diagnostic_source_epoch_mismatch';
    else if (!shownFrame || frameKey(shownFrame, scope) !== frameKey(frame, scope) || shownFrame.media_sha256 !== frame.media_sha256
      || shownFrame.width !== frame.width || shownFrame.height !== frame.height) why = 'diagnostic_displayed_frame_mismatch';
    else if (packet.session_state !== envelope.session_state || !recorded && envelope.session_state !== 'recording') why = 'diagnostic_source_not_recording';
    else if (!object(timing) || timing.clock_qualification !== 'unavailable' || timing.exposure_error_bound_s !== null
      || timing.receipt_semantics !== 'display_retirement_only_not_exposure_freshness'
      || timing.status !== (recorded ? 'recorded' : 'unverified') || !recorded && packet.status !== 'timing_unverified') why = 'diagnostic_timing_invalid';
    else if (!recorded && (!object(receipt) || receipt.eventKey !== key || !number(receipt.remainingHoldS)
      || receipt.remainingHoldS <= 0 || receipt.remainingHoldS > 2)) why = 'diagnostic_receipt_retired';
    else if (!Array.isArray(pose.joints) || !pose.joints.length || pose.joints.length > 17
      || !Array.isArray(pose.edges) || pose.edges.length > 16
      || pose.joints.some(function (joint) { return !object(joint) || !Number.isSafeInteger(joint.id) || joint.id < 0 || joint.id > 16
        || joint.visible !== true || !Array.isArray(joint.position_m) || joint.position_m.length !== 3
        || !joint.position_m.every(function (v) { return number(v) && Number.isFinite(Math.fround(v)); }); })) why = 'diagnostic_joints_invalid';
    var points = why ? null : suppliedPoints(pose, true);
    if (!why && points.reason) why = points.reason;
    if (why) { out.status = 'refused'; out.reason = why; return out; }
    out.renderable = true; out.status = recorded ? 'recorded' : 'timing_unverified'; out.reason = null;
    out.timingLabel = recorded ? 'Recorded diagnostic pose' : 'Diagnostic pose — timing unverified';
    out.eventKey = key; out.joints = points.joints.map(function (joint) {
      var original = pose.joints.find(function (item) { return item.id === joint.id; });
      ['camera_xyz_m', 'pixel', 'error_bound_m', 'depth_support', 'depth_spread_m'].forEach(function (field) {
        if (own(original, field)) joint[field] = bounded(original[field]).value;
      });
      return joint;
    }); out.edges = points.edges;
    out.originalTimes = bounded(pose.source_times).value; out.provenance = bounded(provenance).value;
    out.registration = bounded(registration).value; out.error_bound_m = pose.error_bound_m == null ? null : bounded(pose.error_bound_m).value;
    out.timing = { clock_qualification: 'unavailable', exposure_error_bound_s: null,
      remaining_receipt_hold_s: recorded ? null : receipt.remainingHoldS, receipt_semantics: timing.receipt_semantics,
      original_depth_clock: bounded(timing.original_depth_clock).value };
    return out;
  }
  function blankApproximate(reason) {
    return {renderable:false,admitted:false,status:'unavailable',reason:reason||'approximate_pose_unavailable',
      approximate:true,noLightingAuthority:true,followAllowed:false,joints:[],edges:[],anchor:null,
      qualification:'approximate_visual_alignment',label:'Approximate room placement',eventKey:null,provenance:null,originalTimes:null};
  }
  function approximateEventKey(packet) {
    var pose=packet&&packet.approximate_pose,p=pose&&pose.provenance,r=pose&&pose.registration;
    if(!object(packet)||packet.schema!=='home-apartment-approximate-room-pose-1'||!object(pose)||!scopeValid(pose.scope)
      ||!object(p)||!Number.isSafeInteger(p.source_event_id)||p.source_event_id<=0||!sha(p.original_event_sha256)
      ||!sha(p.frame_media_sha256)||!text(p.frame_media_id)||!text(pose.source_epoch)||!object(r)
      ||!sha(r.alignment_digest)||!sha(r.review_digest)||!sha(r.camera_to_room_digest))return null;
    return JSON.stringify(SCOPE_FIELDS.map(function(key){return pose.scope[key];}).concat([
      pose.source_epoch,p.source_event_id,p.original_event_sha256,p.frame_media_id,p.frame_media_sha256,p.model_generation,
      r.alignment_digest,r.review_digest,r.camera_to_room_digest]));
  }
  function projectApproximate(packet,envelope,scope,shown,receipt,collisionDigest) {
    var out=blankApproximate(),pose=packet&&packet.approximate_pose,key=approximateEventKey(packet),why=null;
    if(!object(packet))return out;
    if(packet.renderable!==true){out.reason=text(packet.reason)?packet.reason:'approximate_pose_unavailable';return out;}
    var frame=packet.frame,shownFrame=shown&&(shown.descriptor||shown),r=pose&&pose.registration,p=pose&&pose.provenance,t=packet.timing;
    var denied=['operational_geometry','geometry_authority','identity_authority','engagement_authority','absence_authority',
      'protection_authority','beam_authority','physical_permission','follow_allowed','lighting_authority'];
    if(!key||denied.some(function(field){return packet[field]!==false;})||packet.room_pose!==null)why='approximate_authority_invalid';
    else if(!scopeMatches(pose.scope,scope)||scope.siteId!=='victoria'||scope.cameraId!=='den')why='approximate_scope_mismatch';
    else if(packet.status!=='approximate'||packet.label!=='Approximate room placement'||pose.label!==packet.label
      ||pose.qualification!=='approximate_visual_alignment'||pose.error_bound_m!==null)why='approximate_label_or_qualification_invalid';
    else if(!r||r.status!=='approximate_visual_alignment'||r.camera_id!==scope.cameraId||r.source_fit_status!=='refused'
      ||!sha(r.source_fit_digest)||!sha(r.canonical_collision_digest)||r.metric_error_bound_m!==null
      ||collisionDigest!=null&&r.canonical_collision_digest!==collisionDigest||!text(r.mount_revision)
      ||!object(r.mount_movement_detection)||r.mount_movement_detection.status!=='unvalidated_coarse_scene_check'
      ||r.mount_movement_detection.result!=='unchanged'||r.mount_movement_detection.physical_movement_error_bound_m!==null)why='approximate_view_alignment_invalid';
    else if(!frameValid(frame,scope)||frame.camera_id!==scope.cameraId||frameKey(envelope.frame,scope)!==frameKey(frame,scope)
      ||frame.frame_id!==pose.frame_id||frame.media_id!==p.frame_media_id||frame.media_sha256!==p.frame_media_sha256
      ||p.media_generation!==scope.generation||!Number.isSafeInteger(p.model_generation)||!sha(p.calibration_content_id)
      ||p.input_family!=='home-apartment-viewing-metric-attempt-1')why='approximate_media_binding_mismatch';
    else if(!object(p.ram_originals)||Object.keys(p.ram_originals).sort().join(',')!=='anchor_a,anchor_b,calibration,npz'
      ||Object.keys(p.ram_originals).some(function(role){var ref=p.ram_originals[role],max=role==='npz'?4194304:role==='calibration'?24576:262144;
        return !object(ref)||!sha(ref.sha256)||!Number.isSafeInteger(ref.size)||ref.size<=0||ref.size>max;}))why='approximate_original_pins_invalid';
    else if(!text(envelope.source_epoch)||pose.source_epoch!==envelope.source_epoch||frame.source_epoch!==envelope.source_epoch)why='approximate_source_epoch_mismatch';
    else if(!shownFrame||frameKey(shownFrame,scope)!==frameKey(frame,scope)||shownFrame.media_sha256!==frame.media_sha256
      ||shownFrame.width!==frame.width||shownFrame.height!==frame.height)why='approximate_displayed_frame_mismatch';
    else if(packet.session_state!==envelope.session_state||envelope.session_state!=='recording')why='approximate_source_not_recording';
    else if(!object(t)||t.clock_qualification!=='unavailable'||t.exposure_error_bound_s!==null||t.status!=='unverified'
      ||t.projection_qualification!=='unqualified_nominal_eeprom'||t.receipt_semantics!=='display_retirement_only_not_exposure_freshness')why='approximate_timing_invalid';
    else if(!object(receipt)||receipt.eventKey!==key||!number(receipt.remainingHoldS)||receipt.remainingHoldS<=0||receipt.remainingHoldS>2)why='approximate_receipt_retired';
    else if(!Array.isArray(pose.joints)||!pose.joints.length||pose.joints.length>17||!Array.isArray(pose.edges)||pose.edges.length>16
      ||pose.joints.some(function(j){return !object(j)||!Number.isSafeInteger(j.id)||j.id<0||j.id>16||j.visible!==true
        ||!Array.isArray(j.position_m)||j.position_m.length!==3||!j.position_m.every(function(v){return number(v)&&Number.isFinite(Math.fround(v));})
        ||j.error_bound_m!==null;}))why='approximate_joints_invalid';
    var points=why?null:suppliedPoints(pose,true);
    if(!why&&points.reason)why=points.reason;
    if(why){out.status='refused';out.reason=why;return out;}
    out.renderable=true;out.status='approximate';out.reason=null;out.eventKey=key;
    out.joints=points.joints.map(function(j){var original=pose.joints.find(function(item){return item.id===j.id;});
      ['camera_xyz_m','pixel','error_bound_m','depth_support','depth_spread_m'].forEach(function(field){if(own(original,field))j[field]=bounded(original[field]).value;});return j;});
    out.edges=points.edges;out.error_bound_m=null;out.originalTimes=bounded(pose.source_times).value;
    out.provenance=bounded(p).value;out.registration=bounded(r).value;
    out.timing={clock_qualification:'unavailable',exposure_error_bound_s:null,remaining_receipt_hold_s:receipt.remainingHoldS,
      projection_qualification:t.projection_qualification,receipt_semantics:t.receipt_semantics,original_depth_clock:bounded(t.original_depth_clock).value};
    return out;
  }
  function project(input) {
    input = input || {};
    var envelope = input.envelope, scope = input.scope, clock = input.clock;
    var result = { schema: 'home-apartment-perception-view-1', scope: copyScope(scope), status: 'refused', reason: 'scope_unavailable',
      sourceEpoch: null, sessionState: null, frame: null,
      pose2d: { renderable: false, status: 'unavailable', reason: 'pose_unavailable', association: 'no-frame', keypoints: [], edges: [],
        age: { status: 'unavailable', seconds: null, uncertainty_s: null, reason: 'original_time_unavailable' }, model_id: null, source_id: null, originalTimes: null },
      models: [], modelsTruncated: false, roomPose: blankRoom(), diagnosticRoomPose: blankDiagnostic(),approximateRoomPose:blankApproximate(),
      follow: { allowed: false, anchor: null, reason: 'room_pose_unavailable' } };
    if (!scopeValid(scope)) return result;
    if (!object(envelope) || envelope.schema !== 'home-apartment-perception-1') { result.reason = 'envelope_unavailable'; return result; }
    if (!scopeMatches(envelope.scope, scope)) { result.reason = 'scope_mismatch'; return result; }
    result.status = 'available'; result.reason = null;
    result.sourceEpoch = text(envelope.source_epoch) ? envelope.source_epoch : null;
    result.sessionState = text(envelope.session_state) ? envelope.session_state : null;
    var live = result.sessionState === 'recording' || result.sessionState === 'live';
    if (frameValid(envelope.frame, scope)) {
      var descriptor = bounded(envelope.frame);
      if (!descriptor.invalid && !descriptor.truncated) result.frame = { descriptor: descriptor.value, key: frameKey(envelope.frame, scope), scope: copyScope(scope),
        age: age(envelope.frame, clock, 'captured_at', false), status: live ? 'current-source' : 'recorded' };
    }
    var shownMatches = displayedMatches(input.displayedFrame, result.frame, scope);
    var shown = input.displayedFrame === undefined ? result.frame :
      (object(input.displayedFrame) && scopeMatches(input.displayedFrame.scope, scope)
        && frameValid(input.displayedFrame.descriptor, scope)
        && frameKey(input.displayedFrame, scope) === input.displayedFrame.key ? input.displayedFrame : null);
    var shownDescriptor = shown && (shown.descriptor || shown);
    result.diagnosticRoomPose = projectDiagnostic(envelope.diagnostic_room_pose, envelope, scope, shown, input.diagnosticReceipt, input.collisionDigest);
    result.approximateRoomPose = projectApproximate(envelope.approximate_room_pose,envelope,scope,shown,input.approximateReceipt,input.collisionDigest);
    var pose = envelope.pose2d;
    if (object(pose)) {
      var paired = association(pose, shownDescriptor), pts = suppliedPoints(pose, false);
      var poseAge = age(pose, clock, 'observed_at', false);
      var poseScope = (!own(pose, 'scope') || scopeMatches(pose.scope, scope)) && (!own(pose, 'generation') || pose.generation === scope.generation);
      var ambiguousMedia = !!shown && !!result.frame && !shownMatches && association(pose, result.frame.descriptor) === 'same-frame';
      result.pose2d = { renderable: paired === 'same-frame' && !!shown && !ambiguousMedia && poseScope && !pts.reason,
        status: live ? (text(pose.status) ? pose.status : 'available') : 'recorded',
        reason: !poseScope ? 'scope_mismatch' : paired !== 'same-frame' ? paired : !shown ? 'displayed_frame_mismatch' : ambiguousMedia ? 'media_identity_ambiguous' : pts.reason,
        association: paired, keypoints: [], edges: [], age: poseAge,
        model_id: text(pose.model_id) ? pose.model_id : null, source_id: text(pose.source_id) ? pose.source_id : null,
        originalTimes: originalTimes(pose) };
      if (result.pose2d.renderable) { result.pose2d.keypoints = pts.joints; result.pose2d.edges = pts.edges; }
    }
    if (Array.isArray(envelope.models)) {
      result.modelsTruncated = envelope.models.length > LIMITS.models;
      envelope.models.slice(0, LIMITS.models).forEach(function (model) {
        if (!object(model)) return;
        var output = bounded(model.result), row = {};
        ['family', 'source_id', 'model_id', 'model_revision', 'status', 'reason', 'frame_id', 'capture_id'].forEach(function (key) {
          if (own(model, key)) row[key] = model[key] === null ? null : (token(model[key]) ? model[key] : null);
        });
        var times = originalTimes(model);
        row.observed_at = times.observed_at; row.available_at = times.available_at; row.valid_until = times.valid_until;
        row.result = output.value; row.resultTruncated = output.truncated; row.resultUnavailable = output.invalid;
        row.association = association(model, shownDescriptor); row.age = age(model, clock, 'observed_at', false);
        result.models.push(row);
      });
    }
    var room = envelope.room_pose;
    if (object(room)) {
      var projected = blankRoom(), roomAge = age(room, clock, 'observed_at', true), why = null;
      projected.age = roomAge; projected.qualification = text(room.qualification) ? room.qualification : null;
      projected.diagnostic = room.qualification === 'diagnostic_alignment';
      if (!scopeMatches(room.scope, scope)) why = 'room_scope_mismatch';
      else if (!live) why = 'session_not_live';
      else if (scope.registrationVersion === null) why = 'registration_version_unavailable';
      else if (room.status !== 'admitted' && room.status !== 'current') why = 'room_status_not_admitted';
      else if (room.qualification !== 'qualified_viewing' && room.qualification !== 'diagnostic_alignment') why = 'qualification_unavailable';
      else if (!object(room.registration) || room.registration.status !== 'applicable' || room.registration.camera_id !== scope.cameraId || !text(room.registration.camera_to_room_digest)) why = 'registration_not_applicable';
      else if (association(room, result.frame && result.frame.descriptor) !== 'same-frame') why = 'room_frame_mismatch';
      else if (!shownMatches) why = 'displayed_frame_mismatch';
      else if (roomAge.status !== 'current') why = roomAge.reason;
      var roomPoints = why ? null : suppliedPoints(room, true);
      if (!why && roomPoints.reason) why = roomPoints.reason;
      if (why) {
        projected.reason = why;
        projected.status = roomAge.status === 'stale' || roomAge.status === 'future' || roomAge.status === 'invalid' ? roomAge.status : 'refused';
      } else {
        projected.admitted = true; projected.status = 'current'; projected.reason = null;
        projected.joints = roomPoints.joints; projected.edges = roomPoints.edges;
        projected.anchor = projected.joints.find(function (joint) { return joint.id === room.anchor_joint_id; }) || null;
      }
      result.roomPose = projected;
    }
    result.follow = { allowed: result.roomPose.admitted && !!result.roomPose.anchor,
      anchor: result.roomPose.admitted ? result.roomPose.anchor : null,
      reason: !result.roomPose.admitted ? result.roomPose.reason : !result.roomPose.anchor ? 'supplied_anchor_unavailable' : null };
    return result;
  }
  return Object.freeze({ SCOPE_FIELDS: SCOPE_FIELDS, LIMITS: LIMITS, scopeMatches: scopeMatches, createScope: createScope,
    frameKey: frameKey, diagnosticEventKey: diagnosticEventKey,approximateEventKey:approximateEventKey, project: project });
});
