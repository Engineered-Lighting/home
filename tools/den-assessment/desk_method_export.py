"""Evaluate frozen desk-method originals; never qualify a survey by itself.

The caller fixes the independent original resolver and its expected byte/content
pin before predictions are inspected. This is an existing trusted-input seam,
not authentication of measurement truth. Browser JSON cannot supply that port.
Bundled-family diagnostics never become historical source5 commissioning.
"""
from copy import deepcopy
import math
import hashlib
from pathlib import Path
import sys

from desk_method_assessment import (PARTS, REQUIREMENTS, AUTHORITY, _candidate, _box,
    _math, digest, encoded, exact, identity, need, number, sha)

SELECTOR = dict(layout='coco17',joint_id=0,anchor_part='head')
FAMILIES = {2:'existing-api-assessment-1',3:'existing-api-bundled-rgb-1'}
ORIGINAL_KEYS = {'schema','version','id','context','projection','registration','clock_mapping',
    'protection_policy','coverage','reference_alignment','exposure_alignment'}
CONTEXT_KEYS = ('scope','person_ref','subject_ref','device_id','workstation_id','method_ref',
    'association_method_ref','source_generation','source_position','anchor_original_ref','parent_publication_refs')


def _admission_math():
    # The existing public wire uses ECMAScript-number canonicalization. Private
    # held actions/candidate hashes keep their historical JSON encoding.
    root=Path(__file__).resolve().parents[2]
    for path in (root/'stack/services/home-context',root/'tools/shared-home'):
        if str(path) not in sys.path:sys.path.append(str(path))
    from lab_live_desk_admission import LabLiveDeskAdmission
    from journal import digest as wire_digest
    return LabLiveDeskAdmission,wire_digest


def reference(value):
    exact(value,('id','digest'),'qualified_original_reference_required')
    identity(value['id']);sha(value['digest'])


def validate_collection(value):
    exact(value,('binding','attempts','summary','collection_sha256'),'fixed_verified_collection_result_required')
    b=value['binding']; version=b.get('version') if isinstance(b,dict) else None
    need(type(version) is int and version in FAMILIES
        and b.get('schema')=='home-lab-assessment-binding-'+str(version), 'explicit_verified_collection_variant_required')
    need(b.get('binding_digest')==digest(b['binding'])
        and b['binding'].get('collection_mode')==FAMILIES[version], 'verified_collection_variant_binding_changed')
    sha(value['collection_sha256'])
    need(type(value['summary']) is dict and type(value['summary'].get('complete')) is bool
        and value['summary'].get('archive_sha256')==value['collection_sha256'],'verified_collection_summary_changed')
    need(type(value['attempts']) is list and len(value['attempts'])<=24,'bounded_verified_attempts_required')
    ids=set()
    for a in value['attempts']:
        need(type(a) is dict and type(a.get('version')) is int and a['version']==version
            and a.get('schema')=='home-lab-assessment-attempt-'+str(version)
            and a.get('binding_digest')==b['binding_digest'] and a.get('candidate_only') is True
            and a.get('coordinate_frame')=='rectified_left' and a.get('unit')=='m'
            and a.get('error_bound_m') is None and a.get('operational_geometry') is None
            and all(a.get(k) is False for k in ('geometry_authority','identity_authority','protection_authority','beam_authority')),
            'camera_candidates_do_not_confer_part_authority')
        key=a.get('source_event_id')
        need(type(key) is int and key>0 and key not in ids,'unique_verified_attempt_required');ids.add(key)
        need(isinstance(a.get('instances'),list) and len(a['instances'])<=2,'bounded_original_instances_required')
        for inst in a['instances']:
            joints=inst.get('joints')
            need(isinstance(joints,list) and len(joints)==17 and {j.get('joint_id') for j in joints}==set(range(17)),
                'exact_original_joint_slots_required')
            for j in joints:
                need(j.get('status') in ('candidate','unavailable'),'original_joint_status_required')
                point=j.get('camera_xyz_m')
                need(point is None if j['status']=='unavailable' else isinstance(point,list) and len(point)==3
                    and all(type(n) in (int,float) and math.isfinite(n) for n in point),'finite_original_point_required')
    return deepcopy(value)


def _measurement_frame_clock(o,candidate,collection,now,binding,*,clock_unit='unix_ns'):
    context=o['context'];exact(context,CONTEXT_KEYS,'fixed_independent_context_required')
    need(isinstance(binding,dict) and binding.get('reference_frame_digest')==digest(candidate['original_survey']['references'])
        and binding.get('collection_binding_digest')==collection['binding']['binding_digest']
        and binding.get('context_digest')==digest(context)
        and collection['binding']['binding']['session']['id']==context['scope']['session_id']
        and collection['binding']['binding']['session']['site_id']==context['scope']['site_id'],
        'independent_measurement_binding_changed')
    for k in ('observed_at','available_at','valid_until','registration_valid_until','clock_valid_until'):number(binding[k])
    need(binding['observed_at']<=binding['available_at']<=now<min(binding['valid_until'],
        binding['registration_valid_until'],binding['clock_valid_until']),'independent_measurement_inputs_expired')
    r,p=o['registration'],o['projection'];need(isinstance(r,dict) and isinstance(p,dict),'qualified_projection_and_registration_required')
    need(p.get('status')=='validated' and p.get('validation_receipt')
        and p.get('site_id')==r.get('site_id')==context['scope']['site_id']
        and r.get('room_id')==context['scope']['room_id']
        and p.get('camera_id')==r.get('camera_id')==collection['binding']['binding']['session']['camera_id']
        and p.get('mount_revision')==r.get('mount_revision'), 'qualified_projection_and_registration_required')
    for body in (r,p):number(body['error_bound_m'],0,10)
    validator,wire_digest=_admission_math()
    need(binding.get('projection_ref')==dict(id=p['id'],digest=wire_digest(p))
        and binding.get('registration_ref')==dict(id=r['id'],digest=wire_digest(r)), 'projection_original_binding_changed')
    # Reuse the actual incumbent admission validator, with the independently
    # supplied capture wire original. This is no synthetic capture-policy alias.
    from types import SimpleNamespace
    wire=binding['capture_registration']
    exact(wire,('registration_ref','registration_digest','geometry_revision','geometry_digest','mount_digest',
        'calibration_digest','coverage_refs','frame','unit','transform_chain_refs'),'original_capture_registration_wire_required')
    validator._calibration(SimpleNamespace(scope=context['scope'],source=dict(registration=wire),
        policy=dict(projection_ref=dict(id=p['id'],digest=wire_digest(p)))),o)
    matrix=r['room_from_rectified_left']
    need(isinstance(matrix,list) and len(matrix)==4 and all(isinstance(row,list) and len(row)==4 for row in matrix)
        and matrix[3]==[0,0,0,1] and all(type(x) in (int,float) and math.isfinite(x) for row in matrix for x in row),
        'qualified_registration_transform_required')
    # Existing transform validator also rejects malformed/non-rigid matrices.
    from geometry import transform_point
    transform_point(dict(frame='rectified_left',xyz_m=[0,0,1]),matrix,source_frame='rectified_left',
        target_frame='z_up_metric_floor0',version=r['id'],calibration_error_m=r['error_bound_m'])
    clock=o['clock_mapping'];need(isinstance(clock,dict) and clock.get('original_unit')==clock_unit
        and clock.get('source_epoch'),'qualified_capture_clock_original_required')
    number(clock['max_uncertainty_s'],0,10);reference(dict(id=clock['mapping_id'],digest=clock['mapping_digest']))


def _reference_exposures(o):
    a=o['reference_alignment'];exact(a,('schema','version','status','boot_id','monotonic_origin_ns','utc_origin_s',
        'valid_until_monotonic_ns','uncertainty_s','qualification_ref'),'qualified_reference_alignment_required')
    need(a['schema']=='home-desk-reference-alignment-1' and type(a['version']) is int and a['version']==1
        and a['status']=='qualified' and isinstance(a['boot_id'],str) and a['boot_id'], 'qualified_reference_alignment_required')
    reference(a['qualification_ref']);number(a['utc_origin_s']);number(a['uncertainty_s'],0,1)
    for k in ('monotonic_origin_ns','valid_until_monotonic_ns'):
        need(isinstance(a[k],str) and a[k].isdigit() and 0<=int(a[k])<2**63,'exact_reference_clock_original_required')
    need(int(a['valid_until_monotonic_ns'])>int(a['monotonic_origin_ns']),'qualified_reference_alignment_expired')
    exposures=o['exposure_alignment'];need(isinstance(exposures,list) and len(exposures)<=24,'bounded_exposure_originals_required')
    ids=set()
    for e in exposures:
        exact(e,('source_event_id','original_event_sha256','frame_id','capture_epoch','model_generation','rgb','depth','qualification_ref'),
            'complete_exposure_alignment_original_required')
        need(type(e['source_event_id']) is int and e['source_event_id']>0 and e['source_event_id'] not in ids,
            'unique_exposure_original_required');ids.add(e['source_event_id']);sha(e['original_event_sha256']);reference(e['qualification_ref'])
        for k in ('rgb','depth'):
            exact(e[k],('observed_at','uncertainty_s'),'qualified_RGB_and_depth_exposure_required')
            number(e[k]['observed_at']);number(e[k]['uncertainty_s'],0,1)


def _originals(o,candidate,doc,collection,now,*,clock_unit='unix_ns'):
    exact(o,ORIGINAL_KEYS,'complete_independent_original_bundle_required')
    need(o['schema']=='home-desk-method-evaluation-originals-1' and type(o['version']) is int
        and o['version']==1,'independent_original_bundle_version_required');identity(o['id'])
    need(len(encoded(o))<=128*1024,'independent_original_bundle_bound')
    context=o['context'];exact(context,CONTEXT_KEYS,'fixed_independent_context_required')
    cov=o['coverage'];need(isinstance(cov,dict),'independent_hidden_coverage_required')
    for k in ('qualification_ref','applicability_ref','participant_ref','motion_ref'):reference(cov[k])
    coverage_version=2 if clock_unit=='unix_s' else 1
    need(cov.get('schema')=='home-desk-method-coverage-original-'+str(coverage_version) and type(cov.get('version')) is int
        and cov['version']==coverage_version and cov.get('status')=='qualified'
        and cov.get('candidate_digest')==candidate['digest']
        and cov.get('reference_frame_digest')==digest(candidate['original_survey']['references'])
        and cov.get('collection_binding_digest')==collection['binding']['binding_digest']
        and collection['binding']['binding']['session']['id']==context['scope']['session_id']
        and collection['binding']['binding']['session']['site_id']==context['scope']['site_id']
        and cov.get('context_digest')==digest(context)
        and cov.get('hidden_configuration_basis')=='independent_all_supported_hidden_configurations'
        and cov.get('part_coverage')==list(PARTS[1:]) and cov.get('anchor_part')=='head',
        'independent_complete_hidden_applicability_required')
    configs=cov.get('configuration_ids');need(isinstance(configs,list) and configs and len(configs)<=32
        and len(set(configs))==len(configs),'independent_configuration_coverage_required')
    need(set(doc['protocol']['body']['supported_configuration_ids'])<=set(configs),'unsupported_hidden_configuration')
    for k in ('observed_at','available_at','valid_until','registration_valid_until','clock_valid_until'):number(cov[k])
    need(cov['observed_at']<=cov['available_at']<=now<cov['valid_until'],'independent_coverage_expired')
    need(now<min(cov['registration_valid_until'],cov['clock_valid_until']), 'independent_calibration_or_clock_expired')
    for k in ('work','anchor_region'):_box(cov[k])
    exact(cov['parts'],PARTS[1:],'independent_distinct_parts_required')
    for p in cov['parts'].values():_box(p)
    number(cov['max_anchor_error_m'],0,10);number(cov['max_anchor_age_s'],0,3)
    need(cov['max_anchor_error_m']>0 and cov['max_anchor_age_s']>0,'independent_method_limits_required')
    exact(cov['motion_bound'],('max_speed_m_s','max_accel_m_s2'),'independent_motion_original_required')
    for v in cov['motion_bound'].values():number(v,0,100)
    _measurement_frame_clock(o,candidate,collection,now,cov,clock_unit=clock_unit)
    definition=o['protection_policy']
    exact(definition,('schema','version','id','required_body_part_ids','protected_parts','body_primitive_id','protection_primitive_id'),
        'complete_original_protection_definition_required')
    need(definition['schema']=='home-desk-protection-definition-1' and type(definition['version']) is int
        and definition['version']==1 and candidate['protection_policy_ref']==dict(id=definition['id'],digest=digest(definition))
        and isinstance(definition['protected_parts'],list) and 'head' in definition['protected_parts'],
        'independent_protection_policy_changed')
    _reference_exposures(o)
    return cov


def _stamp_bounds(s,alignment):
    exact(s,('utc_s','monotonic_ns','boot_id'),'complete_reference_clock_receipt_required')
    need(s['boot_id']==alignment['boot_id'],'reference_boot_or_end_unavailable')
    need(isinstance(s['monotonic_ns'],str) and s['monotonic_ns'].isdigit(),'exact_monotonic_original_required')
    number(s['utc_s']);mono=int(s['monotonic_ns']);origin=int(alignment['monotonic_origin_ns'])
    need(origin<=mono<int(alignment['valid_until_monotonic_ns']),'reference_alignment_expired')
    mapped=alignment['utc_origin_s']+(mono-origin)/1e9;u=alignment['uncertainty_s']
    need(abs(mapped-s['utc_s'])<=u+1e-9,'reference_clock_alignment_mismatch')
    return mapped-u,mapped+u


def _window(w,alignment):
    values=[_stamp_bounds(w[key],alignment) for key in ('started','ended')]
    low,high=values[0][1],values[1][0]
    need(low<high,'qualified_reference_interval_empty')
    return low,high


def _anchor_reference(item,candidate):
    exact(item,('status','original_measurement','point_m','error_axes_m'),'closed_selected_anchor_reference_required')
    need(item['status'] in ('available','unavailable','unknown'),'selected_anchor_reference_status_required')
    if item['status']=='available':
        guide,_=_math();point,axes=guide.room_point(item['original_measurement'],candidate['original_survey']['references'])
        need(point==item['point_m'] and axes==item['error_axes_m'],'selected_anchor_original_measurement_changed')
        return True
    need(all(item[k] is None for k in ('original_measurement','point_m','error_axes_m')),'missing_anchor_has_no_point')
    return False


def _aligned_point(a,exposure,o,collection,low,high,*,qualified=True,measurements=None,diagnostic=False):
    event=a.get('original_event',{});p=event.get('payload',{});observed=a['source_times']['observed_at']
    need(exposure['original_event_sha256']==a.get('original_event_sha256')==digest(event)
        and exposure['frame_id']==p.get('frame_id') and exposure['capture_epoch']==p.get('capture_epoch')
        ==o['clock_mapping']['source_epoch'] and exposure['model_generation']==p.get('generation')
        ==collection['binding']['binding']['model_generation'] and exposure['rgb']['observed_at']==observed,
        'exposure_original_identity_changed')
    need(all(exposure[k]['uncertainty_s']<=o['clock_mapping']['max_uncertainty_s'] for k in ('rgb','depth')),
        'exposure_clock_bound_exceeded')
    valid=qualified and all(low<=exposure[k]['observed_at']-exposure[k]['uncertainty_s']
        <=exposure[k]['observed_at']+exposure[k]['uncertainty_s']<=high for k in ('rgb','depth'))
    point=None
    if measurements is not None:
        point=deepcopy(measurements[a['source_event_id']]['diagnostic_point' if diagnostic else 'point']) if valid else None
    elif len(a['instances'])==1 and valid:
        joint=next(j for j in a['instances'][0]['joints'] if j['joint_id']==SELECTOR['joint_id'])
        if joint['status']=='candidate':
            matrix=o['registration']['room_from_rectified_left'];xyz=joint['camera_xyz_m']
            point=[sum(matrix[i][j]*xyz[j] for j in range(3))+matrix[i][3] for i in range(3)]
    available=(measurements[a['source_event_id']]['available_at'] if measurements is not None
        else a['source_times']['available_at'])
    return point,available-(observed-exposure['rgb']['uncertainty_s'])


def _evaluate_method(candidate,document,collection,*,originals,expected_originals_sha256,now,causal_basis=None,diagnostic=None,bundled=None):
    proposed=_candidate(candidate);col=validate_collection(collection);number(now)
    need(document['candidate_ref']['digest']==candidate['digest'],'evaluation_candidate_changed')
    need(document['protocol']['sha256']==digest(document['protocol']['body'])
        and document['requirements']['sha256']==digest(document['requirements']['body']), 'frozen_requirements_or_protocol_changed')
    exact(document['requirements']['body'],REQUIREMENTS,'frozen_independent_requirements_required')
    req=document['requirements']['body'];missing=[k for k in REQUIREMENTS if req[k] is None]
    for k,v in req.items():
        if v is not None:number(v,0,1 if 'fraction' in k else 1e6)
    windows=document['windows'];need(isinstance(windows,list) and 1<=len(windows)<=32,'ended_reference_windows_required')
    need(all(w.get('ended') is not None for w in windows),'held_reference_interval_not_ended')
    reasons=[];cov=None;trusted=False
    if originals is None:reasons.append('independent_originals_unavailable')
    else:
        trusted=expected_originals_sha256 is not None and digest(originals)==expected_originals_sha256
        if not trusted:reasons.append('trusted_pinned_original_resolver_unavailable')
        try:
            cov=_originals(originals,candidate,document,col,now,clock_unit='unix_s' if bundled is not None else 'unix_ns')
            _,upper=_stamp_bounds(document['reported'],originals['reference_alignment'])
            need(document['reported']['utc_s']==now,'report_clock_receipt_changed')
            need(upper<min(cov['valid_until'],cov['registration_valid_until'],cov['clock_valid_until']),
                'independent_inputs_expired_at_report_clock_upper')
        except (ValueError,KeyError,TypeError,OverflowError) as error:
            cov=None
            reasons.append('independent_originals_invalid_'+(str(error) if isinstance(error,ValueError) else type(error).__name__))
    guide,contains=_math()
    from live_assessment_scoring import summary
    stats={p:dict(eligible_reference_s=0.,contained_s=0.,undercovered_s=0.,missing_prediction_s=0.,unavailable_reference_s=0.) for p in PARTS}
    errors=[];conservative_errors=[];latencies=[];work_errors=[];duration=available=longest=0.;attempt_missing=0;leases_failed=0
    matched=set();windows_seen=set();alignment_missing=False
    for w in windows:
        windows_seen.add(w['request']['configuration_id'])
        need(w['request']['configuration_id'] in document['protocol']['body']['supported_configuration_ids'],
            'reference_configuration_not_frozen')
        if cov is None:low,high=w['started']['utc_s'],w['ended']['utc_s'];qualified=False
        else:
            try:low,high=_window(w,originals['reference_alignment']);qualified=True
            except (ValueError,KeyError,TypeError):low,high=w['started']['utc_s'],w['ended']['utc_s'];qualified=False;alignment_missing=True
        number(low);number(high);need(high>low,'positive_reference_interval_required');duration+=high-low
        refparts=w['request']['reference_parts'];exact(refparts,PARTS,'six_independent_reference_parts_required')
        anchor_ref=w['request'].get('selected_anchor');need(isinstance(anchor_ref,dict),'selected_anchor_reference_required')
        if not _anchor_reference(anchor_ref,candidate):reasons.append('independent_selected_anchor_reference_unavailable')
        for name,ref in refparts.items():
            exact(ref,('status','shape','original_measurements'),'closed_independent_part_reference_required')
            need(ref['status'] in ('available','unavailable','unknown'),'independent_part_reference_status_required')
            if ref['status']=='available':
                raw=ref['original_measurements'];exact(raw,('references','outer_corners','thickness_m'),'original_independent_measurements_required')
                need(raw['references']==candidate['original_survey']['references'],'independent_reference_frame_changed')
                need(guide.outer_box(raw['outer_corners'],raw['references'],thickness_m=raw['thickness_m'],require_volume=name!='work')==ref['shape'],
                    'independent_reference_shape_changed')
            else:need(ref['shape'] is None and ref['original_measurements'] is None,'missing_reference_has_no_shape')
        source=[]
        for a in col['attempts']:
            times=a['source_times'];observed,at,until=(times[k] for k in ('observed_at','available_at','valid_until'))
            for v in (observed,at,until):number(v)
            need(observed<=at<until,'ordered_original_candidate_times_required')
            if not low<=observed<high:continue
            exposure=next((e for e in originals['exposure_alignment'] if e['source_event_id']==a['source_event_id']),None) if cov else None
            point=None;error=None;valid=qualified
            if exposure is None:valid=False;alignment_missing=True
            else:
                point,latency=_aligned_point(a,exposure,originals,col,low,high,qualified=valid,
                    measurements=bundled['measurements'] if bundled is not None else None)
                latencies.append((latency,1))
            if point is None:attempt_missing+=1
            else:
                matched.add(a['source_event_id'])
                if anchor_ref['status']=='available':
                    error=math.dist(point,anchor_ref['point_m']);errors.append((error,1))
                    margin=math.sqrt(sum(x*x for x in anchor_ref['error_axes_m']))+w['request']['reference_uncertainty_m']
                    conservative_errors.append((error+margin,1))
                # The existing method checks its complete prospective lease.
                age=cov['max_anchor_age_s'];motion=cov['motion_bound']
                radius=cov['max_anchor_error_m']+originals['registration']['error_bound_m']+motion['max_speed_m_s']*age+.5*motion['max_accel_m_s2']*age*age
                if not all(abs(point[i]-box['center_m'][i])+radius<=box['half_extents_m'][i]
                    for box in (cov['anchor_region'],cov['parts'][cov['anchor_part']]) for i in range(3)):
                    point=None;leases_failed+=1
            uncertainty=max(exposure[k]['uncertainty_s'] for k in ('rgb','depth')) if exposure else 0
            effective_until=min(until-uncertainty,observed+cov['max_anchor_age_s']-uncertainty) if cov else until
            if bundled is not None:
                measured=bundled['measurements'][a['source_event_id']]
                at=max(at,measured['available_at']);effective_until=min(effective_until,measured['valid_until'])
            source.append((observed,at,effective_until,point))
        cuts=sorted({low,high,*[t for r in source for t in r[1:3] if low<t<high]});gap=0.
        for start,end in zip(cuts,cuts[1:]):
            dt=end-start;prior=[r for r in source if r[1]<=start]
            latest=max(prior,key=lambda r:(r[0],r[1])) if prior else None
            supported=bool(latest and start<latest[2] and latest[3] is not None and trusted and cov)
            if supported:available+=dt;gap=0.
            else:gap+=dt;longest=max(longest,gap)
            for name in PARTS:
                ref=refparts[name];row=stats[name]
                if ref['status']!='available':row['unavailable_reference_s']+=dt;continue
                row['eligible_reference_s']+=dt
                if not supported:row['missing_prediction_s']+=dt;continue
                shape=cov['work'] if name=='work' else cov['parts'][name]
                covered=contains(shape,ref['shape'],w['request']['reference_uncertainty_m'])
                row['contained_s' if covered else 'undercovered_s']+=dt
                if name=='work':work_errors.append((math.dist(shape['center_m'],ref['shape']['center_m']),dt))
    if alignment_missing:reasons.append('qualified_reference_or_exposure_alignment_unavailable')
    missingconfigs=set(document['protocol']['body']['supported_configuration_ids'])-windows_seen
    reasons.extend('unmeasured_configuration_'+c for c in sorted(missingconfigs))
    failed=[]
    work=summary(work_errors);anchor=summary(errors);bounds=summary(conservative_errors);latency=summary(latencies)
    if cov:
        age=cov['max_anchor_age_s'];motion=cov['motion_bound']
        projected=(None if bounds['max'] is None else bounds['max']+originals['registration']['error_bound_m']
            +motion['max_speed_m_s']*age+.5*motion['max_accel_m_s2']*age*age)
        if projected is None or projected>cov['max_anchor_error_m']:failed.append('selected_anchor_error_bound')
    if leases_failed:failed.append('full_lease_anchor_containment')
    for name,row in stats.items():
        eligible=row['eligible_reference_s'];row['contained_fraction']=row['contained_s']/eligible if eligible else None
        row['undercoverage_fraction']=row['undercovered_s']/eligible if eligible else None
        if row['unavailable_reference_s']:reasons.append(name+'_independent_reference_unavailable')
        if req['min_reference_s'] is not None and eligible<req['min_reference_s']:failed.append(name+'_reference_duration')
        if req['min_containment_fraction'] is not None and (not eligible or row['contained_fraction']<req['min_containment_fraction']):failed.append(name+'_containment')
        if req['max_undercoverage_fraction'] is not None and eligible and row['undercoverage_fraction']>req['max_undercoverage_fraction']:failed.append(name+'_undercoverage')
    fraction=available/duration if duration else None
    if req['min_complete_protection_fraction'] is not None and (fraction is None or fraction<req['min_complete_protection_fraction']):failed.append('complete_method_availability')
    if req['max_work_center_error_m'] is not None and (work['p95'] is None or work['p95']>req['max_work_center_error_m']):failed.append('static_surveyed_work_center_error')
    if req['max_capture_to_available_s'] is not None and (latency['p95'] is None or latency['p95']>req['max_capture_to_available_s']):failed.append('capture_to_available_latency')
    if not col['summary']['complete']:reasons.append('original_collection_incomplete')
    if diagnostic is not None and not diagnostic['independent_inputs_valid']:
        reasons.append('independent_diagnostic_measurement_inputs_unavailable')
    family=FAMILIES[col['binding']['version']]
    if family!='existing-api-assessment-1' and bundled is None:reasons.append('new_family_operational_consumer_unavailable')
    if bundled is not None:reasons.append('contemporaneous_exact_method_service_timing_unavailable')
    report=dict(schema='home-desk-method-evaluation-report-1',version=1,selector=deepcopy(SELECTOR),
        decision='block' if missing or reasons or failed else 'eligible_for_existing_consumer_export',
        selected_anchor_error_m=anchor,selected_anchor_reference_error_upper_m=bounds,static_work_center_error_m=work,
        part_containment=stats,availability=dict(duration_s=duration,complete_s=available,complete_fraction=fraction,
            missing_prediction_s=duration-available,longest_gap_s=longest),capture_to_available_s=latency,
        attempt_count=len(col['attempts']),missing_selected_anchor_attempts=attempt_missing,
        original_family=family,collection_sha256=col['collection_sha256'],trusted_originals_resolved=trusted,
        qualification_truth_role='fixed_original_resolver_claim_and_computed_consistency_not_truth_authentication',
        missing_requirements=missing,missing_inputs=sorted(set(reasons)),failed_requirements=sorted(set(failed)),
        reference_document_ref=dict(id=document.get('id','held-references-'+digest(document)),digest=digest(document)),
        export_status='unavailable' if missing or reasons or failed else 'available_for_private_save',
        operational_geometry=None,authority=deepcopy(AUTHORITY))
    if causal_basis is not None:
        report.update(schema='home-desk-method-evaluation-report-2',version=2,evaluation_basis=deepcopy(causal_basis))
    if diagnostic is not None:
        method_ready=bool(cov and trusted and diagnostic['independent_inputs_valid']
            and (family=='existing-api-assessment-1' or bundled is not None))
        report.update(schema='home-desk-method-evaluation-report-3',version=3,diagnostic_measurement=deepcopy(diagnostic),
            method_qualified_error_bound_m=cov['max_anchor_error_m'] if method_ready else None,
            method_availability=deepcopy(report['availability']) if method_ready else None,
            method_protection=deepcopy(report['part_containment']['protection']) if method_ready else None)
        if not method_ready:
            report.update(decision='block',export_status='unavailable',availability=None,part_containment=None,
                selected_anchor_error_m=None,selected_anchor_reference_error_upper_m=None,capture_to_available_s=None,
                static_work_center_error_m=None,failed_requirements=[],unevaluated_method_requirements=list(REQUIREMENTS))
            report['missing_inputs']=sorted(set(report['missing_inputs']+['independent_complete_method_qualification_unavailable']))
    if bundled is not None:
        report.update(schema='home-desk-method-evaluation-report-4',version=4,
            measurement_method_ref=deepcopy(bundled['method_ref']),
            original_proofs=dict(count=len(bundled['proof_refs']),sha256=digest(bundled['proof_refs'])),
            claim_scope=deepcopy(BUNDLED_REPORT_SCOPE),
            retained_input_availability=deepcopy(report['availability']),
            retained_input_capture_to_available_s=deepcopy(report['capture_to_available_s']),
            availability=None,capture_to_available_s=None,method_availability=None,method_protection=None,
            exact_method_processing_latency_s=None,usable_service_status='not_measured',
            method_qualified_error_bound_m=None,
            unevaluated_service_requirements=['min_complete_protection_fraction','max_capture_to_available_s'],
            part_containment_basis='spatial_coverage_during_eligible_retained_input_intervals',
            source6_operational_adoption='pending_exact_wrapper_and_method_comparison')
    exported=None
    if report['decision']=='eligible_for_existing_consumer_export':
        import desk_enclosure as consumer
        context=deepcopy(originals['context']);r=originals['registration'];c=originals['clock_mapping']
        _,wire_digest=_admission_math()
        report_ref=dict(id='evaluated-desk-'+digest(report),digest=digest(report))
        assessment=dict(schema='home-desk-enclosure-assessment-2',version=2,id='desk-assessment-'+digest(report),
            status='independently_assessed',**{k:context[k] for k in ('scope','person_ref','subject_ref','device_id','workstation_id','method_ref','association_method_ref')},
            candidate_digest=candidate['digest'],registration=dict(ref=dict(id=r['id'],digest=wire_digest(r)),status='qualified',
                error_bound_m=r['error_bound_m'],valid_until=cov['registration_valid_until']),
            clock_mapping=dict(ref=dict(id=c['mapping_id'],digest=c['mapping_digest']),status='qualified',
                uncertainty_s=c['max_uncertainty_s'],valid_until=cov['clock_valid_until']),
            anchor_region=deepcopy(cov['anchor_region']),work=deepcopy(cov['work']),parts=deepcopy(cov['parts']),
            protection_policy_ref=deepcopy(candidate['protection_policy_ref']),protected_parts=deepcopy(originals['protection_policy']['protected_parts']),
            anchor_part=cov['anchor_part'],part_coverage=deepcopy(cov['part_coverage']),hidden_configuration_basis=cov['hidden_configuration_basis'],
            reference_protocol_ref=dict(id=document['protocol']['body']['id'],digest=document['protocol']['sha256']),
            requirements_ref=dict(id='held-requirements-'+document['requirements']['sha256'],digest=document['requirements']['sha256']),
            assessment_report_ref=report_ref,observed_at=cov['observed_at'],available_at=now,
            valid_until=min(cov['valid_until'],cov['registration_valid_until'],cov['clock_valid_until']),
            max_anchor_age_s=cov['max_anchor_age_s'],max_anchor_error_m=cov['max_anchor_error_m'],motion_bound=deepcopy(cov['motion_bound']))
        binding=dict(schema='home-desk-enclosure-binding-2',version=2,**context,
            assessment_ref=dict(id=assessment['id'],digest=consumer.digest(assessment)),candidate_digest=candidate['digest'],
            registration_ref=deepcopy(assessment['registration']['ref']),clock_mapping_ref=deepcopy(assessment['clock_mapping']['ref']))
        try:
            consumer._binding(binding);consumer._candidate(candidate,binding);consumer._assessment(assessment,binding,candidate,now)
        except (ValueError,KeyError,TypeError,OverflowError):
            report['decision']='block';report['export_status']='unavailable';report['missing_inputs'].append('existing_assessment_consumer_refused')
        else:
            exported=dict(candidate=deepcopy(candidate),assessment=assessment,binding=binding,
                projection=deepcopy(originals['projection']),registration=deepcopy(r),clock_mapping=deepcopy(c),
                protection_policy=deepcopy(originals['protection_policy']))
    return dict(report=report,commissioning=exported)


def evaluate_method(candidate,document,collection,*,originals,expected_originals_sha256,now):
    """Retrospective control; its complete originals must already exist."""
    return _evaluate_method(candidate,document,collection,originals=originals,
        expected_originals_sha256=expected_originals_sha256,now=now)


QUALIFICATION_KEYS=(ORIGINAL_KEYS-{'exposure_alignment'})|{'exposure_alignment_method_ref','resolver_ref'}
MEASUREMENT_KEYS=('reference_frame_digest','collection_binding_digest','context_digest','projection_ref','registration_ref',
    'capture_registration','observed_at','available_at','valid_until','registration_valid_until','clock_valid_until')


def validate_qualification(value):
    if value is None:return None  # Explicit development/missing-input mode.
    version=value.get('version') if isinstance(value,dict) else None
    need(type(version) is int and version in (1,2,3),'static_method_qualification_version_required')
    exact(value,QUALIFICATION_KEYS|({'measurement_binding'} if version in (2,3) else set())
        |(BUNDLED_QUALIFICATION_KEYS if version==3 else set()),'static_method_qualification_originals_required')
    need(value['schema']=='home-desk-method-qualification-'+str(version),
        'static_method_qualification_version_required')
    reference(value['exposure_alignment_method_ref']);reference(value['resolver_ref'])
    if version in (2,3) and value['measurement_binding'] is not None:
        exact(value['measurement_binding'],MEASUREMENT_KEYS,'closed_independent_measurement_binding_required')
    if version==1 or value['coverage'] is not None:
        exact(value['coverage'],set(('schema','version','id','status','candidate_digest','reference_frame_digest','collection_binding_digest',
        'context_digest','configuration_ids','work','parts','anchor_region','anchor_part','part_coverage','hidden_configuration_basis',
        'max_anchor_age_s','max_anchor_error_m','motion_bound','observed_at','available_at','valid_until','registration_valid_until',
        'clock_valid_until','qualification_ref','applicability_ref','participant_ref','motion_ref','projection_ref','registration_ref',
            'capture_registration'))|({'measurement_method_ref'} if version==3 else set()),'closed_static_method_coverage_original_required')
    if version==3:_bundled_method(value)
    need(len(encoded(value))<=64*1024,'static_qualification_byte_bound')
    return deepcopy(value)


def alignment_request(qualification,collection):
    """Only original identities and clocks cross the fixed alignment port."""
    q=validate_qualification(qualification);col=validate_collection(collection);sources=[]
    for a in col['attempts']:
        event=a['original_event'];p=event['payload'];times=a['source_times']
        need(a['original_event_sha256']==digest(event) and event['id']==a['source_event_id']
            and event['session_id']==col['binding']['binding']['session']['id']
            and all(p.get(k)==times[k] for k in ('observed_at','available_at','valid_until')),
            'actual_published_source_identity_or_clock_changed')
        sources.append(dict(source_event_id=a['source_event_id'],original_event_sha256=a['original_event_sha256'],
            frame_id=p['frame_id'],capture_epoch=p['capture_epoch'],model_generation=p['generation'],source_times=deepcopy(times)))
    version=2 if q['version']==3 else 1
    need(q['version']!=3 or col['binding']['version']==3,'bundled_qualification_collection_family_required')
    request=dict(schema='home-desk-exposure-alignment-request-'+str(version),version=version,
        qualification_ref=dict(id=q['id'],digest=digest(q)),resolver_ref=deepcopy(q['resolver_ref']),
        method_ref=deepcopy(q['exposure_alignment_method_ref']),collection_sha256=col['collection_sha256'],
        binding_digest=col['binding']['binding_digest'],sources=sources)
    if q['version']==3:
        request.update(original_family=q['original_family'],measurement_method_ref=_method_ref(q['measurement_method']))
    need(len(encoded(request))<=64*1024,'bounded_original_alignment_request_required')
    return request


def validate_alignment(qualification,request,value,*,now):
    if value is None:return None  # Missing evidence is retained, never qualified.
    version=2 if qualification['version']==3 else 1
    exact(value,{'schema','version','qualification_ref','resolver_ref','method_ref','collection_sha256','request_sha256','exposures'}
        |({'original_family','measurement_method_ref'} if version==2 else set()),'exact_report_time_alignment_originals_required')
    need(value['schema']=='home-desk-exposure-alignment-originals-'+str(version) and type(value['version']) is int and value['version']==version
        and len(encoded(value))<=64*1024,'bounded_report_time_alignment_required')
    for key in ('qualification_ref','resolver_ref','method_ref','collection_sha256'):
        need(value[key]==request[key],'resolved_alignment_frozen_binding_changed')
    need(value['request_sha256']==digest(request),'resolved_alignment_request_changed')
    if version==2:
        need(all(value[k]==request[k] for k in ('original_family','measurement_method_ref')),'bundled_alignment_method_changed')
    rows=value['exposures'];need(isinstance(rows,list) and len(rows)<=24,'bounded_exact_exposure_leaves_required')
    originals={r['source_event_id']:r for r in request['sources']};seen=set()
    for row in rows:
        exact(row,{'source_event_id','original_event_sha256','frame_id','capture_epoch','model_generation','rgb','depth',
            'qualification_ref','available_at'}|({'status','reason','clock_original'} if version==2 else set()),
            'closed_resolved_exposure_leaf_required')
        event_id=row['source_event_id'];need(event_id in originals and event_id not in seen,'duplicate_or_foreign_alignment_original')
        seen.add(event_id);source=originals[event_id]
        need(all(row[k]==source[k] for k in ('source_event_id','original_event_sha256','frame_id','capture_epoch','model_generation'))
            and row['qualification_ref']==qualification['exposure_alignment_method_ref'],'resolved_exposure_original_changed')
        number(row['available_at']);need(source['source_times']['available_at']<=row['available_at']<=now,
            'exposure_resolution_predates_publication_or_is_future')
    need(seen==set(originals),'missing_exact_exposure_alignment_original')
    return deepcopy(value)


def _diagnostic_measurement(candidate,document,collection,q,original,*,trusted,now,measurements=None):
    """Independent aligned residuals; never infer an error/coverage bound."""
    _math()
    from live_assessment_scoring import summary
    residuals=[];margins=[];missing=0;reasons=[];valid=False
    try:
        need(trusted,'independent_measurement_original_pin_unavailable')
        binding=q['measurement_binding']
        _measurement_frame_clock(original,candidate,collection,now,binding,clock_unit='unix_s' if q['version']==3 else 'unix_ns')
        _reference_exposures(original)
        _,upper=_stamp_bounds(document['reported'],original['reference_alignment'])
        need(document['reported']['utc_s']==now and upper<min(binding['valid_until'],binding['registration_valid_until'],
            binding['clock_valid_until']),'independent_measurement_report_clock_expired')
        if q['coverage'] is not None:
            need(all(q['coverage'].get(k)==binding[k] for k in MEASUREMENT_KEYS),'measurement_and_method_originals_conflict')
        # Validate every interval/reference before accumulating any residual.
        windows=[]
        for window in document['windows']:
            low,high=_window(window,original['reference_alignment'])
            anchor=window['request']['selected_anchor'];available=_anchor_reference(anchor,candidate)
            windows.append((window,low,high,anchor,available))
        for window,low,high,anchor,available in windows:
            for attempt in collection['attempts']:
                observed,at,until=(attempt['source_times'][k] for k in ('observed_at','available_at','valid_until'))
                for value in (observed,at,until):number(value)
                need(observed<=at<until,'ordered_original_candidate_times_required')
                if not low<=observed<high:continue
                exposure=next((e for e in original['exposure_alignment'] if e['source_event_id']==attempt['source_event_id']),None)
                if exposure is None or not available:missing+=1;continue
                point,_=_aligned_point(attempt,exposure,original,collection,low,high,measurements=measurements,diagnostic=True)
                if point is None:missing+=1;continue
                error=math.dist(point,anchor['point_m']);residuals.append((error,1))
                margin=math.sqrt(sum(x*x for x in anchor['error_axes_m']))+window['request']['reference_uncertainty_m']
                margins.append((error+margin,1))
        valid=True
    except (ValueError,KeyError,TypeError,OverflowError) as error:
        residuals=[];margins=[];reasons.append(str(error) if isinstance(error,ValueError) else type(error).__name__)
    residual_summary,margin_summary=summary(residuals),summary(margins)
    # These statistics weight each original sample once, not by elapsed time.
    del residual_summary['eligible_s'];del margin_summary['eligible_s']
    return dict(status='measured_residuals' if residuals else 'unavailable',independent_inputs_valid=valid,
        selected_anchor_residual_m=residual_summary,residual_plus_reference_margin_m=margin_summary,
        missing_selected_anchor_samples=missing,missing_inputs=reasons,
        interpretation='aligned_sample_residual_not_a_qualified_method_error_bound_or_hidden_coverage',
        qualified_method_error_bound_m=None,operational_geometry=None,authority=deepcopy(AUTHORITY))


def evaluate_resolved_method(candidate,document,collection,*,qualification,alignment,expected_qualification_sha256,now,bundled_collection=None):
    q=validate_qualification(qualification)
    if q is None:
        return _evaluate_method(candidate,document,collection,originals=None,expected_originals_sha256=None,now=now,
            causal_basis=dict(mode='independent_qualification_unavailable',alignment_sha256=None,leaf_digests=[]))
    request=alignment_request(q,collection)
    resolved=validate_alignment(q,request,alignment,now=now)
    bundled=None
    if q['version']==3:
        need(bundled_collection is not None,'same_read_bundled_originals_required')
        need(bundled_collection['collection']==collection,'bundled_verified_collection_changed')
        expected,samples,proof_refs=_bundled_alignment(q,bundled_collection,request)
        need(resolved==expected,'bundled_alignment_original_proof_changed')
        bundled=dict(measurements=samples,proof_refs=proof_refs,method=q['measurement_method'],
            method_ref=_method_ref(q['measurement_method']))
    # Internal common math receives explicit resolved leaves. These are never
    # stored or advertised as historical original-family records.
    original={k:deepcopy(v) for k,v in q.items() if k not in {'resolver_ref','exposure_alignment_method_ref','measurement_binding'}|BUNDLED_QUALIFICATION_KEYS}
    original.update(schema='home-desk-method-evaluation-originals-1',version=1,
        exposure_alignment=[{k:deepcopy(v) for k,v in row.items() if k not in ('available_at','status','reason','clock_original')}
            for row in resolved['exposures'] if row.get('status','aligned')=='aligned'] if resolved else [])
    trusted=expected_qualification_sha256 is not None and digest(q)==expected_qualification_sha256
    diagnostic=(_diagnostic_measurement(candidate,document,collection,q,original,trusted=trusted,now=now,
            measurements=bundled['measurements'] if bundled else None) if q['version'] in (2,3) else None)
    return _evaluate_method(candidate,document,collection,originals=original,
        expected_originals_sha256=digest(original) if trusted else None,now=now,diagnostic=diagnostic,bundled=bundled,
        causal_basis=dict(mode='frozen_qualification_then_published_original_alignment',qualification_ref=request['qualification_ref'],
            resolver_ref=request['resolver_ref'],method_ref=request['method_ref'],alignment_sha256=digest(resolved) if resolved else None,
            request_sha256=digest(request),leaf_digests=[dict(source_event_id=r['source_event_id'],sha256=digest(r)) for r in resolved['exposures']] if resolved else [],
            trust_role='fixed_resolver_claim_not_timestamp_truth_authentication'))


BUNDLED_FAMILY='existing-api-bundled-rgb-1'
BUNDLED_QUALIFICATION_KEYS={'original_family','bundle_binding','depth_clock_policy','measurement_method'}
METHOD_FILES=('lab_live_desk_anchor.py','depth_association.py','head3d.py')
BUNDLED_REPORT_SCOPE=dict(spatial_residuals='retrospective_diagnostic',hidden_coverage='independent_claim_and_computed_consistency',
    original_input_temporal_applicability='recomputed',
    exact_method_processing_latency='not_measured',usable_service='not_measured')


def _method_ref(method):
    return dict(id=method['id'],digest=digest(method))


def _bundled_method(q,collection=None):
    """Explicit source6 method preimage; no inferred estimator error bound."""
    need(q['original_family']==BUNDLED_FAMILY,'explicit_bundled_qualification_family_required')
    b=q['bundle_binding'];exact(b,('scope','lab_binding_digest','bundle_input_policy_sha256'),'fixed_bundled_assessment_binding_required')
    need(b['scope']==q['context']['scope'],'bundled_assessment_scope_changed')
    sha(b['lab_binding_digest']);sha(b['bundle_input_policy_sha256'])
    need(q['clock_mapping'].get('original_unit')=='unix_s' and q['clock_mapping'].get('epoch_origin_s')==0,
        'distinct_bundled_derived_clock_policy_required')
    from capture_timing import commissioning
    commissioning(q['depth_clock_policy'])
    m=q['measurement_method']
    exact(m,('schema','version','id','name','selector','lab_profile','sampling','rgb_roi','projection_ref',
        'implementation_sha256','max_abs_skew_s','max_depth_age_s'),'closed_bundled_measurement_method_required')
    need(m['schema']=='home-desk-nose-measurement-method-1' and type(m['version']) is int and m['version']==1
        and m['name']=='visible_coco17_nose_depth_lift' and m['selector']==SELECTOR and m['rgb_roi']=='selected_joint',
        'exact_operational_nose_measurement_method_required');identity(m['id'])
    exact(m['sampling'],('radius_px','min_support','max_spread_m'),'closed_operational_sampling_required')
    p=m['sampling']
    need(type(p['radius_px']) is int and 1<=p['radius_px']<=16
        and type(p['min_support']) is int and 3<=p['min_support']<=256,'operational_sampling_bound_required')
    number(p['max_spread_m'],0,10);need(p['max_spread_m']>0,'positive_operational_spread_bound_required')
    number(m['max_abs_skew_s'],0,.1);number(m['max_depth_age_s'],0,3)
    need(m['max_abs_skew_s']>0 and m['max_depth_age_s']>0,'positive_operational_time_bounds_required')
    if q['coverage'] is not None:
        need(q['coverage'].get('schema')=='home-desk-method-coverage-original-2'
            and type(q['coverage'].get('version')) is int and q['coverage']['version']==2
            and q['coverage']['measurement_method_ref']==_method_ref(m),'coverage_measurement_method_changed')
    _,wire_digest=_admission_math()
    need(m['projection_ref']==dict(id=q['projection']['id'],digest=wire_digest(q['projection'])),
        'measurement_projection_preimage_changed')
    exact(m['implementation_sha256'],METHOD_FILES,'fixed_measurement_implementation_inventory_required')
    service=Path(__file__).resolve().parents[2]/'stack/services/perception-lab'
    for name in METHOD_FILES:
        sha(m['implementation_sha256'][name])
        need(hashlib.sha256((service/name).read_bytes()).hexdigest()==m['implementation_sha256'][name],
            'operational_measurement_implementation_changed')
    if collection is not None:
        need(collection['binding']['version']==3 and m['lab_profile']==collection['binding']['binding']['lab_profile'],
            'exact_operational_model_profile_required')
    return m


def _public_bundled_row(value,*,scope):
    """Project genuine published fields; retain the full descriptor outside.

    Store.get_media publishes the original nine database fields plus five
    aliases. Missing originals are refused, never defaulted or reconstructed.
    This is a checked view for the unchanged private original verifier.
    """
    from lab_bundled_rgb_stereo import ROW_KEYS
    exact(value,ROW_KEYS|{'media_id','site_id','size_bytes','content_type','url'},
        'closed_public_bundled_descriptor_required')
    need(isinstance(value['id'],str) and value['id'] and value['session_id']==scope['session_id']
        and type(value['generation']) is int and value['generation']>=0
        and value['site_id']==scope['site_id'] and value['media_id']==value['id']
        and value['size_bytes']==value['size'] and value['content_type']==value['mime_type']
        and value['url']==f"/api/perception-lab/v1/sessions/{scope['session_id']}/media/{value['id']}?generation={value['generation']}",
        'public_bundled_descriptor_alias_changed')
    return {key:deepcopy(value[key]) for key in ROW_KEYS}


def _bundled_alignment(q,verified,request):
    """Recompute current aggregate source6 proofs from same-read originals.

    Derived UTC is not a camera Unix timestamp. Neither read completion nor
    alignment creates exposure truth; genuine commissioning remains required.
    Every attempt receives an aligned or unavailable leaf.
    """
    exact(verified,('collection','original_family','input_binding','captures'),'same_read_bundled_result_required')
    col=validate_collection(verified['collection']);m=_bundled_method(q,col)
    need(verified['original_family']==BUNDLED_FAMILY,'same_read_bundled_family_changed')
    b=verified['input_binding'];bound=col['binding']['binding']
    need(b==bound['bundled_rgb_binding'] and b.get('original_family')==BUNDLED_FAMILY
        and b.get('policy_sha256')==q['bundle_binding']['bundle_input_policy_sha256']
        and b.get('site_id')==q['context']['scope']['site_id']
        and b.get('session_id')==q['context']['scope']['session_id'], 'same_read_bundled_binding_changed')
    captures=verified['captures'];need(isinstance(captures,list) and len(captures)<=24,'bounded_bundled_capture_snapshots_required')
    by_id={}
    for c in captures:
        exact(c,('source_event_id','event','first_consumer_received_at','calculation_completed_at','frame_record',
            'records','capture_started','capture_terminal'),'closed_bundled_capture_snapshot_required')
        key=c['source_event_id'];need(type(key) is int and key>0 and key not in by_id,'unique_bundled_capture_snapshot_required')
        by_id[key]=c
    need(set(by_id)=={a['source_event_id'] for a in col['attempts']},'every_bundled_attempt_original_required')
    from lab_bundled_rgb_stereo import BASE_ROLES,build_bundled_clock_original,validate_bundled_rgb_originals
    from depth_clock import bundled_depth_material
    from lab_live_desk_anchor import _projected_pose, sample_visible_nose_depth
    from bundle_ingest import calibration_content_id
    from spatial_validation import check_mount
    leaves=[];samples={};proof_refs=[]
    for a in col['attempts']:
        c=by_id[a['source_event_id']];event=a['original_event'];at=a['source_times']['available_at']
        need(all(event['payload'].get(k)==m['lab_profile'].get(v) for k,v in
            (('run_id','profile_id'),('engine_epoch','engine_epoch'),('model_version','model_version'),('artifact_sha256','artifact_sha256'))),
            'operational_model_original_profile_changed')
        need(c['event']==event and c['first_consumer_received_at']==a['first_consumer_received_at'],
            'same_read_bundled_event_changed')
        completed=c['calculation_completed_at']
        need(completed is None or type(completed) in (int,float) and math.isfinite(completed)
            and completed>=c['first_consumer_received_at']>=at,'retained_full_read_receipt_changed')
        leaf={k:deepcopy(next(s for s in request['sources'] if s['source_event_id']==a['source_event_id'])[k])
            for k in ('source_event_id','original_event_sha256','frame_id','capture_epoch','model_generation')}
        leaf.update(qualification_ref=deepcopy(q['exposure_alignment_method_ref']),available_at=completed if completed is not None else at,
            status='unavailable',reason='required_bundled_original_unavailable',clock_original=None,rgb=None,depth=None)
        samples[a['source_event_id']]=dict(point=None,diagnostic_point=None,available_at=leaf['available_at'],valid_until=a['source_times']['valid_until'])
        if (completed is None or c['frame_record'] is None or c['capture_started'] is None
                or c['capture_terminal'] is None or set(c['records'])!=BASE_ROLES):
            leaves.append(leaf);continue
        records=c['records'];tuples={}
        for role,item in records.items():
            exact(item,('record','data'),'same_read_bundled_binary_required')
            tuples[role]=(_public_bundled_row(item['record'],scope=q['context']['scope']),item['data'])
        need(c['frame_record']==records['rgb']['record'],'same_read_bundled_C_frame_changed')
        clock=build_bundled_clock_original(tuples,expected_bundle_binding=q['bundle_binding'],capture_clock_policy=q['clock_mapping'],
            started=c['capture_started'],terminal=c['capture_terminal'],received_at=completed,event=event,frame=c['frame_record']['metadata'])
        originals=dict(binding=q['bundle_binding'],start=c['capture_started'],terminal=c['capture_terminal'],clock_original=clock)
        lookup={row['id']:dict(record=row,data=data) for row,data in tuples.values()}
        checked=validate_bundled_rgb_originals(originals,media_refs=clock['media_refs'],expected_bundle_binding=q['bundle_binding'],
            capture_clock_policy=q['clock_mapping'],resolve_media=lambda ref:lookup[ref['id']],event=event,frame=c['frame_record']['metadata'])
        plane,depth=bundled_depth_material(records,binding=b,clock=q['depth_clock_policy'])
        need(calibration_content_id(checked['calibration'])==q['projection']['id']
            and check_mount(q['projection']['mount_signature'],plane)['status']=='unchanged',
            'independent_projection_or_mount_changed')
        leaf.update(status='aligned',reason=None,clock_original=clock,
            rgb=dict(observed_at=c['frame_record']['metadata']['observed_at'],uncertainty_s=clock['clock']['uncertainty_s']),
            depth=dict(observed_at=depth['observed_at'],uncertainty_s=depth['uncertainty_seconds']))
        proof_refs.append(dict(source_event_id=a['source_event_id'],clock_original_sha256=digest(clock),
            media_refs=deepcopy(clock['media_refs']),started_event_id=clock['started_event_id'],terminal_event_id=clock['terminal_event_id']))
        skew=leaf['rgb']['observed_at']-depth['observed_at']+leaf['rgb']['uncertainty_s']+depth['uncertainty_seconds']
        until=min(depth['valid_until']-depth['uncertainty_seconds'],depth['observed_at']+m['max_depth_age_s']-depth['uncertainty_seconds'],
            c['frame_record']['metadata']['valid_until'],a['source_times']['valid_until'])
        sample=samples[a['source_event_id']];sample['valid_until']=until
        eligible=depth['observed_at']<=leaf['rgb']['observed_at'] and skew<=m['max_abs_skew_s'] and completed<until
        try:
            _,pixel=_projected_pose(dict(event=event))
            measured=sample_visible_nose_depth(plane,checked['source_metadata'],q['projection'],q['registration'],pixel,
                radius_px=m['sampling']['radius_px'],min_support=m['sampling']['min_support'],
                max_spread_m=m['sampling']['max_spread_m'])
            sample['diagnostic_point']=measured['room_xyz_m']
            if eligible:sample['point']=measured['room_xyz_m']
        except ValueError:
            pass  # Missing person/nose/depth support remains a measured miss.
        leaves.append(leaf)
    result=dict(schema='home-desk-exposure-alignment-originals-2',version=2,
        **{k:deepcopy(request[k]) for k in ('qualification_ref','resolver_ref','method_ref','collection_sha256',
            'original_family','measurement_method_ref')},request_sha256=digest(request),exposures=leaves)
    need(len(encoded(result))<=64*1024,'bounded_bundled_alignment_originals_required')
    return result,samples,proof_refs


def build_bundled_alignment(qualification,verified,request):
    """Fixed Guide code port; no model/media fetch or owner-entered clocks."""
    q=validate_qualification(qualification)
    need(q is not None and q['version']==3 and request==alignment_request(q,verified['collection']),
        'exact_bundled_alignment_request_required')
    return _bundled_alignment(q,verified,request)[0]


