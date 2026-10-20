/* Owner-selected, approximate configured-desk preview. No operational authority. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;
  else root.HomeApartmentDeskPreview=api;})(typeof window!=='undefined'?window:globalThis,function(){
  'use strict';
  const LABEL='Person visible · configured desk preview',SCHEMA='home-apartment-virtual-target-preview-1';
  const FLAGS=['operational_geometry','geometry_authority','identity_authority','engagement_authority','absence_authority',
    'protection_authority','beam_authority','physical_permission','follow_allowed','lighting_authority','physical_commands'];
  const SCOPE=['siteId','contentRevision','frameDigest','registrationVersion','cameraId','sessionId','generation'];
  const OBS=['schema','session_id','request_id','arm','site_id','camera_id','frame_id','media_ids','capture_epoch','generation',
    'run_id','model_version','artifact_sha256','engine_epoch','observed_at','available_at','valid_until'];
  // The decoded descriptor omits camera/epoch: their exact original values are
  // checked separately against the fixed scene scope and current source envelope.
  const FRAME=['site_id','frame_id','media_id','media_sha256','generation','width','height'];
  const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v),finite=Number.isFinite;
  const text=v=>typeof v==='string'&&v.length>0&&v.length<=256,sha=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
  const exact=(v,keys)=>object(v)&&Object.keys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
  const copy=v=>JSON.parse(JSON.stringify(v)),vector=v=>Array.isArray(v)&&v.length===3&&v.every(n=>finite(n)&&Math.abs(n)<=1000);
  const unit=v=>vector(v)&&Math.abs(Math.hypot(...v)-1)<1e-6;
  function canonical(v){return Array.isArray(v)?v.map(canonical):object(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;}
  const same=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
  // Development relaxes only the model output's available_at < valid_until,
  // as the backend does (it records finalize_expired); the order stays exact.
  const times=(v,late=false)=>exact(v,['observed_at','available_at','valid_until'])&&Object.values(v).every(finite)
    &&v.observed_at>=0&&v.observed_at<=v.available_at&&v.observed_at<v.valid_until&&(late||v.available_at<v.valid_until);
  const denied=()=>Object.fromEntries(FLAGS.map(k=>[k,false]));
  // Development timing is an owner-selected virtual-preview policy only. The
  // backend adds timing.policy/timing.warnings and evidence fields only then;
  // deadline refusals become recorded warnings and every identity/image pin
  // stays exact. Strict packets carry none of these fields.
  const CHECKS=['clock_uncertainty','anchor_containment','proof_expired','registration_expired','dispatch_late','lease_retired',
    'finalize_expired','media_expired','approximate_receipt_hold_elapsed','newer_observation_not_projected'];
  const UNDATED=new Set(['clock_uncertainty','anchor_containment','approximate_receipt_hold_elapsed','newer_observation_not_projected']);
  const EVIDENCE=['supporting','contrary','integrity','neutral'];
  function timingWarnings(v){
    return Array.isArray(v)&&v.length<=CHECKS.length&&new Set(v.map(w=>w?.check)).size===v.length
      &&v.every(w=>exact(w,['check','at','deadline'])&&CHECKS.includes(w.check)&&finite(w.at)
        &&(UNDATED.has(w.check)?w.deadline===null:w.deadline===null||finite(w.deadline)));
  }
  const developmentTiming=t=>object(t)&&t.policy==='development'&&timingWarnings(t.warnings);
  const eventId=v=>v===null||Number.isSafeInteger(v)&&v>0;
  // The backend's classification of its latest arm-B output (duplicate merge
  // applied), on available and unavailable packets. An unchanged event id
  // means no newer output.
  function backendEvidence(p,e){
    if(!object(p)||e?.timing_policy!=='development'||!developmentTiming(p.timing)
      ||!EVIDENCE.includes(p.evidence)||!eventId(p.evidence_event_id))return null;
    return {evidence:p.evidence,eventId:p.evidence_event_id};
  }
  function unavailable(reason='desk_preview_unavailable',extra={}){
    const parent=typeof extra?.parentReason==='string'&&/^[A-Za-z][A-Za-z0-9_]{0,159}$/.test(extra.parentReason)?extra.parentReason:null;
    return {available:false,status:'unavailable',reason:typeof reason==='string'&&/^[a-z][a-z0-9_]{0,159}$/.test(reason)?reason:'desk_preview_unavailable',
      target:null,personPoint:null,sourceKey:null,remainingHoldS:0,validUntil:null,observation:null,provenance:null,label:LABEL,
      virtual_preview_only:true,qualification:null,timingStatus:'unverified',timingPolicy:null,warnings:[],parentReason:parent,
      originalAgeS:null,evidence:null,evidenceEventId:null,...denied()};
  }
  function project(input={}){
    let out;
    try{out=checked(input);}catch(_){out=unavailable('desk_preview_packet_invalid');}
    // A refusal still reports the backend's evidence for its latest output.
    const backend=out.available?null:backendEvidence(input?.packet,input?.envelope);
    if(backend){out.evidence=backend.evidence;out.evidenceEventId=backend.eventId;}
    return out;
  }
  function checked({packet:p,envelope:e,scope,displayedFrame,approximate:a,clock,receipt}){
    const backend=backendEvidence(p,e);
    if(!object(p)||p.available!==true)return unavailable(text(p?.reason)?p.reason:'desk_preview_unavailable');
    // A development packet must be complete and agree with the stream's
    // development mark on the same envelope; strict packets stay exact.
    const marked=object(p.timing)&&(Object.hasOwn(p.timing,'policy')||Object.hasOwn(p.timing,'warnings'));
    const dev=marked&&!!backend;
    if(marked&&!dev)return unavailable('desk_preview_timing_policy_invalid');
    if(JSON.stringify(p).length>16384||!exact(p,['schema','version','status','available','reason','label','virtual_preview_only','qualification',
      'scope','frame','observation','provenance','source_times','timing','valid_until','target','person_point',...FLAGS,
      ...(dev?['evidence','evidence_event_id']:[])])
      ||p.schema!==SCHEMA||p.version!==1||p.status!=='preview'||p.reason!==null||p.label!==LABEL
      ||p.virtual_preview_only!==true||p.qualification!==null||FLAGS.some(k=>p[k]!==false))return unavailable('desk_preview_authority_invalid');
    // An available packet never carries contrary or integrity evidence.
    if(dev&&['contrary','integrity'].includes(backend.evidence))return unavailable('desk_preview_evidence_'+backend.evidence);
    if(!object(e)||e.session_state!=='recording'||!a?.renderable||a.status!=='approximate'
      ||a.qualification!=='approximate_visual_alignment'||a.noLightingAuthority!==true||a.followAllowed!==false)
      return unavailable('desk_preview_approximation_unavailable',{parentReason:a?.reason??null});
    if(!exact(scope,SCOPE)||!exact(p.scope,SCOPE)||!same(scope,p.scope)||!same(scope,e.scope)
      ||scope.siteId!=='victoria'||scope.cameraId!=='den')return unavailable('desk_preview_scope_mismatch');
    const original=e.approximate_room_pose?.approximate_pose,frame=p.frame,shown=displayedFrame?.descriptor||displayedFrame;
    if(!object(original)||!same(p.frame,e.approximate_room_pose.frame)||!same(p.source_times,original.source_times)
      ||!same(p.source_times,a.originalTimes)||!object(shown)||!FRAME.every(k=>shown[k]===frame[k])
      ||!sha(frame.media_sha256)||frame.site_id!==scope.siteId||frame.camera_id!==scope.cameraId
      ||frame.generation!==scope.generation||frame.source_epoch!==e.source_epoch
      ||!times(frame.original_times)||!times(p.source_times,dev))return unavailable('desk_preview_displayed_frame_mismatch');
    const provenance=p.provenance,base=object(provenance)?{...provenance}:null;
    if(base)delete base.configured_candidate_sha256;
    if(!sha(provenance?.configured_candidate_sha256)||!same(base,a.provenance)||!same(base,original.provenance)
      ||provenance.frame_media_id!==frame.media_id||provenance.frame_media_sha256!==frame.media_sha256)
      return unavailable('desk_preview_original_mismatch');
    const o=p.observation;
    if(!exact(o,OBS)||!same(o,e.virtual_target_preview_observation)||o.schema!=='home-lab-arm-observation-1'
      ||o.session_id!==scope.sessionId||o.site_id!==scope.siteId||o.camera_id!==scope.cameraId||o.arm!=='b'
      ||o.frame_id!==frame.frame_id||!same(o.media_ids,[frame.media_id])||o.capture_epoch!==e.source_epoch
      ||o.generation!==provenance.model_generation||!Number.isSafeInteger(o.generation)||o.generation<1
      ||!['request_id','run_id','model_version','engine_epoch'].every(k=>text(o[k]))||!sha(o.artifact_sha256)
      ||!['observed_at','available_at','valid_until'].every(k=>o[k]===p.source_times[k]))
      return unavailable('desk_preview_observation_mismatch');
    const t=p.target,b=t?.binding;
    if(!exact(t,['kind','frame','unit','point_m','normal_unit','target_ref','equipment_ref','binding'])
      ||t.kind!=='configured_desk_centre'||t.frame!=='z_up_metric_floor0'||t.unit!=='m'||!vector(t.point_m)
      ||!unit(t.normal_unit)||t.normal_unit[2]<=0||!exact(t.target_ref,['schema','digest'])
      ||t.target_ref.schema!=='home-registered-desk-target-2'||!sha(t.target_ref.digest)
      ||!exact(t.equipment_ref,['revision','digest'])||!Number.isSafeInteger(t.equipment_ref.revision)||t.equipment_ref.revision<0||!sha(t.equipment_ref.digest)
      ||!exact(b,['content_revision','registration_version','manifest_digest','frame_digest','canonical_collision_digest'])
      ||b.content_revision!==scope.contentRevision||b.registration_version!==scope.registrationVersion||b.frame_digest!==scope.frameDigest
      ||!sha(b.manifest_digest)||b.canonical_collision_digest!==a.registration?.canonical_collision_digest)
      return unavailable('desk_preview_target_binding_mismatch');
    const point=p.person_point,joints=[5,6].map(id=>a.joints?.find(j=>j.id===id));
    if(!exact(point,['method','joint_ids','point_m','instance_ref','error_bound_m'])
      ||point.method!=='approximate_supported_shoulder_midpoint'||!same(point.joint_ids,[5,6])||!vector(point.point_m)
      ||point.instance_ref!==original.instance_ref||!text(point.instance_ref)||point.error_bound_m!==null
      ||joints.some(j=>!j||j.visible!==true||!vector(j.position_m)||!Number.isSafeInteger(j.depth_support)||j.depth_support<3)
      ||point.point_m.some((n,i)=>Math.abs(n-(joints[0].position_m[i]+joints[1].position_m[i])/2)>1e-9))
      return unavailable('desk_preview_shoulders_unavailable');
    const timing=p.timing,elapsed=e.virtual_target_preview_receipt_elapsed_s,parentTiming=e.approximate_room_pose.timing;
    // /live reads the two arms from separate snapshots, so their receipt floats
    // (and development warnings) always differ live. The pairing is by identity
    // (event, frame, source times, scope: checked above); the parent's timing
    // need only carry the same policy.
    const policyOf=v=>object(v)&&Object.hasOwn(v,'policy')?v.policy:null;
    if(!object(parentTiming)||policyOf(parentTiming)!==policyOf(timing)||dev&&!developmentTiming(parentTiming)
      ||timing?.status!=='unverified'||timing.clock_qualification!=='unavailable'
      ||timing.exposure_error_bound_s!==null||timing.projection_qualification!=='unqualified_nominal_eeprom'
      ||timing.receipt_semantics!=='display_retirement_only_not_exposure_freshness'
      ||!finite(timing.remaining_receipt_hold_s)||timing.remaining_receipt_hold_s<=0||timing.remaining_receipt_hold_s>2
      ||!finite(timing.receipt_age_s)||timing.receipt_age_s<0||timing.receipt_age_s+timing.remaining_receipt_hold_s>2.000001
      ||!finite(elapsed)||elapsed<0||receipt?.eventKey!==a.eventKey||!finite(receipt.remainingHoldS)
      ||receipt.remainingHoldS<=0||receipt.remainingHoldS>2)return unavailable('desk_preview_receipt_retired');
    if(clock?.domain!=='unix'||!finite(clock.now_s)||clock.now_s<0
      ||!(clock.uncertainty_s===null||finite(clock.uncertainty_s)&&clock.uncertainty_s>=0))return unavailable('desk_preview_clock_unavailable');
    const originalUntil=Math.min(p.source_times.valid_until,frame.original_times.valid_until);
    if(!finite(p.valid_until)||p.valid_until!==originalUntil||!finite(shown.valid_until))return unavailable('desk_preview_original_deadline_invalid');
    const validUntil=Math.min(originalUntil,shown.valid_until),upper=clock.now_s+(clock.uncertainty_s===null?0:clock.uncertainty_s);
    // The receipt term bounds first sight of a projection under either policy.
    const receiptHold=Math.min(receipt.remainingHoldS,timing.remaining_receipt_hold_s-elapsed),originalHold=validUntil-upper;
    const warnings=[];
    if(dev)for(const w of [...timing.warnings,...parentTiming.warnings])if(!warnings.includes(w.check))warnings.push(w.check);
    let hold;
    if(!dev){hold=Math.min(receiptHold,originalHold);if(!(hold>0))return unavailable('desk_preview_original_expired');}
    else{
      if(!(receiptHold>0))return unavailable('desk_preview_receipt_expired');
      // A passed original deadline reuses the backend's own warning when it
      // recorded one; otherwise Home records its own.
      const late=!(originalHold>0)||p.source_times.available_at>=p.source_times.valid_until;
      if(late&&!warnings.some(w=>w==='finalize_expired'||w==='media_expired'))warnings.push('home_original_deadline_passed');
      hold=receiptHold;
    }
    return {available:true,status:'preview',reason:null,target:copy(t),personPoint:copy(point),
      sourceKey:JSON.stringify([a.eventKey,provenance.configured_candidate_sha256]),remainingHoldS:hold,validUntil,
      observation:copy(o),provenance:copy(provenance),label:LABEL,virtual_preview_only:true,qualification:null,
      timingStatus:'unverified',timingPolicy:dev?'development':null,warnings,parentReason:null,
      originalAgeS:clock.now_s-p.source_times.observed_at,evidence:dev?backend.evidence:null,
      evidenceEventId:dev?backend.eventId:null,...denied()};
  }
  // Contrary evidence for a development hold comes from the raw selected-arm
  // original, not the image-gated view, so it needs no decoded pixels. This set
  // is kept equal to HomeApartmentDeskAimPreview.INTEGRITY_REASONS (tested).
  const INTEGRITY_REASONS=Object.freeze(['approximate_camera_epoch_changed','approximate_camera_epoch_unverified',
    'approximate_camera_identity_changed','approximate_camera_calibration_changed','approximate_mount_identity_required',
    'approximate_scene_or_mount_identity_changed','approximate_observed_instance_unavailable','approximate_matrix_digest_changed',
    'approximate_original_source_pin_changed',...['calibration_changed','mount_identity_changed','scope_changed','predecessor_changed',
      'matrix_changed','verification_required','mount_check_required'].map(k=>'approximate_successor_'+k),
    'approximate_scene_scope_required','approximate_scope_mismatch','approximate_source_epoch_mismatch','approximate_view_alignment_invalid',
    'desk_preview_scope_changed','desk_preview_scope_mismatch','desk_preview_target_binding_changed','desk_preview_target_binding_mismatch']);
  const integritySet=new Set(INTEGRITY_REASONS);
  const integrity=reason=>typeof reason==='string'&&(integritySet.has(reason)||/^approximate_mount_signature_[a-z0-9_]{1,64}$/.test(reason));
  const MERGE_IOU=0.9,MERGE_CONFIDENCE=0.4;
  function personBox(b){
    if(!object(b)||!['x','y','w','h'].every(k=>finite(b[k]))||b.x<0||b.y<0||b.w<=0||b.h<=0||b.w>32768||b.h>32768)return null;
    if(Object.hasOwn(b,'confidence')&&b.confidence!==null&&!(finite(b.confidence)&&b.confidence>=0&&b.confidence<=1))return null;
    return {x:b.x,y:b.y,w:b.w,h:b.h,confidence:finite(b.confidence)?b.confidence:null};
  }
  function iou(a,b){
    const w=Math.max(0,Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x)),h=Math.max(0,Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y));
    const inter=w*h,union=a.w*a.h+b.w*b.h-inter;return union>0?inter/union:0;
  }
  function personCount(pose){
    let raw=null,countSource=null;
    if(Object.hasOwn(pose,'persons2d')){
      const persons=pose.persons2d;countSource='persons2d';
      if(!object(persons)||persons.schema!=='home-lab-frame-persons-1'||persons.frame_id!==pose.frame_id
        ||!Array.isArray(persons.persons)||persons.persons.length>512)return {personCount:null,mergedDuplicates:0,countSource};
      raw=persons.persons.map(person=>object(person)?person.box:null);
    }else if(Array.isArray(pose.boxes)&&pose.boxes.length<=512){raw=pose.boxes;countSource='boxes';}
    if(!raw)return {personCount:null,mergedDuplicates:0,countSource:null};
    const boxes=raw.map(personBox);
    // Malformed detail is neutral: it is neither one person nor contrary evidence.
    if(boxes.some(box=>!box))return {personCount:null,mergedDuplicates:0,countSource};
    // Exactly the backend's distinct_people: a box merges only when its own
    // confidence is known and below 0.4 and some other box (merged or not) has
    // a strictly higher known confidence at IoU >= 0.9. Ties and missing
    // confidences never merge.
    const merged=boxes.filter((box,i)=>box.confidence!==null&&box.confidence<MERGE_CONFIDENCE
      &&boxes.some((other,j)=>j!==i&&other.confidence!==null&&other.confidence>box.confidence&&iou(box,other)>=MERGE_IOU)).length;
    return {personCount:boxes.length-merged,mergedDuplicates:merged,countSource};
  }
  function evidence({envelope:e,connection,approximate:a}={}){
    try{
      if(!object(e))return null;
      const pose=object(e.pose2d)?e.pose2d:null;let armB=null;
      if(pose&&pose.source_id==='specialist_b'&&text(pose.request_id)&&text(pose.frame_id)&&finite(pose.observed_at))
        armB={key:JSON.stringify([pose.request_id,pose.frame_id]),observedAt:pose.observed_at,
          captureEpoch:text(pose.capture_epoch)?pose.capture_epoch:null,...personCount(pose)};
      const packet=e.approximate_room_pose,preview=e.virtual_target_preview;
      const metric=Array.isArray(e.models)?e.models.find(row=>row?.source_id==='apartment_viewing_metric'):null;
      const candidates=[object(packet)&&packet.renderable!==true?packet.reason:null,a?.reason,metric?.reason,
        object(preview)&&preview.available!==true?preview.reason:null];
      // The backend's own classification is preferred; armB is Home's fallback
      // from the same raw latest envelope when the backend gives none.
      return {policy:e.timing_policy==='development'?'development':'strict',backend:backendEvidence(preview,e),
        connection:text(connection)&&connection.length<=64?connection:null,
        sessionState:text(e.session_state)?e.session_state.slice(0,64):null,sourceEpoch:text(e.source_epoch)?e.source_epoch:null,
        armB,integrityReason:candidates.find(integrity)||null};
    }catch(_){return null;}
  }
  return Object.freeze({project,unavailable,evidence,isIntegrityReason:integrity,INTEGRITY_REASONS});
});
